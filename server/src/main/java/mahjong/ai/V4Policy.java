package mahjong.ai;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;

import mahjong.util.Log;

/**
 * v4 网络的**纯 Java 手写前向**（P5：把 v4 权重真正跑在服务端）。
 *
 * <p>权重格式 = `net.bin` **格式 2**（带块清单与张量表，逐字段见 {@code docs/FEATURES-V4.md} §6
 * 与 {@code python/mahjong_ml/v4/export.py} 的模块注释）。与 v3 的 {@link NeuralPolicy}
 * （格式 1 / 定长 MLP）**共用同一个魔数 `MJNN`**，靠 `format` 字段区分 ⇒ 拿错了一律构造期报错。
 *
 * <h2>拓扑（`python/mahjong_ml/v4/model.py`，逐层对应）</h2>
 * <pre>
 *   tile[34,48] → 逐牌种 MLP → 自注意力 → LayerNorm → proj            → h_tile[34,tileD] + pool[d]
 *   evt [60,96] → MLP → GRUCell ×60（冷启动）→ TransformerEncoder×1 → e_tokens[60,d] + h_evt[d]
 *   ctx [64] / cand[n,128] → MLP（末尾**也有** ReLU）
 *   融合：候选当 query 对 tile/evt 做注意力；delta→state 门控写回；state→delta 用 FiLM 调制；
 *         拼接 [u_tile, evt_mod, ctx] → MLP（末尾 ReLU）→ u[n,d]
 *   头：policy/effect/danger 用 u；value/placement/belief_* 用 mean_i(u)
 * </pre>
 *
 * <p>⚠ 三处"看起来多余但**不能省**"的地方（省了就是静默偏移）：
 * ① `tile.enc` 第二个线性层之后**有** ReLU，而 `event.enc` 第二个之后**没有**；
 * ② `Mlp` 末端的 ReLU（ctx/cand/fusion.out 都有）；
 * ③ 事件塔冷启动要**喂满 K=60 个 token**（含前面全 0 的 padding），
 * 与训练时的 `EventTower.forward(h=None)` 逐位同源 —— "增量 == 全量"那条判据的基准就是它。
 *
 * <p>⚠ 宽度（`dModel/tileD/nHeads/valueBins`）从**头部**读，拓扑固定 —— 与 v3 的
 * `hidden/head/trunkLayers` 同一个思路：golden 夹具能用小网络钉住前向，上线照跑 192/64/4/51。
 */
public final class V4Policy implements LogitPolicy {

    /** 与 v3 同一个魔数（`mahjong.ai.NeuralPolicy.MAGIC`），用 `format` 区分代号。 */
    public static final int MAGIC = 0x4D4A4E4E;
    /** v4 = 格式 2（带块清单）；v3 = 1。 */
    public static final int FORMAT_VERSION = 2;
    /** 头部字节数（9 个 u32 + 2 个 u32 + 16 B 指纹 + 1 个 u32）。 */
    public static final int HEADER_BYTES = 64;

    private static final float LN_EPS = 1e-5f;

    private final int dModel;
    private final int tileD;
    private final int nHeads;
    private final int valueBins;
    private final String fingerprint;
    private final List<String> blocks;
    /** 注册表里有、权重里没有的块（**训练时就没用** ⇒ 推理也必须置 0，规范 §7）。 */
    private final Set<String> missingBlocks;

    private final Map<String, float[][]> mats = new HashMap<>();
    private final Map<String, float[]> vecs = new HashMap<>();
    private final Set<String> allNames = new HashSet<>();
    private final Set<String> used = new HashSet<>();

    /** 增量事件缓存（L2；每份权重一份，按座位分槽）。关掉时走全量路径（判据的基准）。 */
    private final V4Cache cache = new V4Cache(V4Features.K_EVT);
    /** 新装的策略实例默认开不开缓存（`--no-v4-cache` 置 false；判据的基准路径）。 */
    private static boolean cacheDefault = true;
    private boolean cacheEnabled = cacheDefault;
    /**
     * 缓存**深度**（只用于归因与自检，四档的输出必须逐位相同）：
     * `1` = 只把隐状态改成增量（事件行与 `in_proj` 照旧全算）；`2` = 再复用事件编码行；
     * `3` = 再复用注意力 `in_proj`（最全）。0 = 等于关缓存（走全量路径）。
     */
    private int cacheLevel = 3;
    /** 全量路径最近一次的冷启动 `h`（自检钩子；见 {@link #cachedHidden}）。 */
    private float[] lastFullHidden;
    /** padding 行的常量（零 token 的编码 / 注意力 `in_proj`）——多行共享同一份引用。 */
    private float[] padTokRow;
    private float[] padEncRow;
    private float[] padQkvRow;

    // ---------------------------------------------------------------- 加载
    private V4Policy(int dModel, int tileD, int nHeads, int valueBins, String fingerprint,
                     List<String> blocks, Set<String> missingBlocks,
                     Map<String, float[][]> mats, Map<String, float[]> vecs,
                     Set<String> allNames) throws IOException {
        this.dModel = dModel;
        this.tileD = tileD;
        this.nHeads = nHeads;
        this.valueBins = valueBins;
        this.fingerprint = fingerprint;
        this.blocks = List.copyOf(blocks);
        this.missingBlocks = Set.copyOf(missingBlocks);
        this.mats.putAll(mats);
        this.vecs.putAll(vecs);
        this.allNames.addAll(allNames);
        bindAll();
        if (used.size() != allNames.size()) {
            List<String> unused = new ArrayList<>(allNames);
            unused.removeAll(used);
            unused.sort(String::compareTo);
            throw new IOException("权重里有 " + unused.size() + " 个张量没有任何层读它："
                    + unused.subList(0, Math.min(6, unused.size())) + " —— 导出器与本前向不同版本");
        }
    }

    public static V4Policy load(Path path) throws IOException {
        return loadBytes(Files.readAllBytes(path), path.toString());
    }

    /** 从内存字节加载（golden 夹具里的内嵌权重走这条）。 */
    public static V4Policy loadBytes(byte[] raw, String what) throws IOException {
        if (raw.length < HEADER_BYTES) {
            throw new IOException("权重文件太短：" + what + "（" + raw.length + " B）");
        }
        ByteBuffer bb = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        int magic = bb.getInt();
        int fmt = bb.getInt();
        if (magic != MAGIC) {
            throw new IOException(String.format("权重文件魔数不对：%#x（期望 %#x）", magic, MAGIC));
        }
        if (fmt != FORMAT_VERSION) {
            throw new IOException("权重格式版本 " + fmt + " != 本服务端 " + FORMAT_VERSION
                    + "（格式 1 是 v3 的定长 MLP，由 NeuralPolicy 加载）");
        }
        int featVer = bb.getInt();
        int obsVer = bb.getInt();
        int derivedVer = bb.getInt();
        int dModel = bb.getInt();
        int tileD = bb.getInt();
        int nHeads = bb.getInt();
        int valueBins = bb.getInt();
        if (featVer != V4Features.FEATURE_VERSION) {
            throw new IOException("权重特征版本 " + featVer + " != 本服务端 "
                    + V4Features.FEATURE_VERSION + " —— 重新导出权重（`python -m mahjong_ml.v4.export`）");
        }
        if (obsVer != V4Features.OBS_VERSION) {
            throw new IOException("权重是按 obs v" + obsVer + " 训练的，本服务端特征要 obs v"
                    + V4Features.OBS_VERSION + " —— 重新导出权重");
        }
        if (dModel <= 0 || tileD <= 0 || nHeads <= 0 || valueBins <= 0
                || dModel % nHeads != 0 || tileD % nHeads != 0) {
            throw new IOException("权重头部非法：dModel=" + dModel + " tileD=" + tileD
                    + " nHeads=" + nHeads + " valueBins=" + valueBins);
        }
        int nBlocks = bb.getInt();
        int nTensors = bb.getInt();
        byte[] fpB = new byte[16];
        bb.get(fpB);
        String fingerprint = new String(fpB, StandardCharsets.US_ASCII);
        bb.getInt();                                            // params（日志用，不信它）

        Map<String, Integer> registry = V4Features.blockWidths();
        List<String> blocks = new ArrayList<>();
        Set<String> missing = new HashSet<>(registry.keySet());
        int lastIndex = -1;
        for (int i = 0; i < nBlocks; i++) {
            int ln = bb.getShort() & 0xFFFF;
            byte[] idb = new byte[ln];
            bb.get(idb);
            String bid = new String(idb, StandardCharsets.UTF_8);
            int width = bb.getInt();
            Integer want = registry.get(bid);
            if (want == null) {
                throw new IOException("权重里的块 id 未注册：" + bid + "（本服务端有 "
                        + registry.keySet() + "）");
            }
            if (want != width) {
                throw new IOException("块 " + bid + " 宽度 " + width + " != 本服务端 " + want
                        + " —— 张量布局变过，重新导出权重");
            }
            int idx = V4Features.indexOfBlock(bid);
            if (idx <= lastIndex) {
                throw new IOException("权重里的块顺序/重复不对：" + bid + "（注册表顺序必须递增）");
            }
            lastIndex = idx;
            blocks.add(bid);
            missing.remove(bid);
        }
        String wantFp = V4Features.fingerprint(blocks, V4Features.widthsOf(blocks));
        if (!wantFp.equals(fingerprint)) {
            throw new IOException("块清单指纹不符：文件里 " + fingerprint + "，按清单重算 " + wantFp
                    + " —— 权重被改过或导出器与本服务端不同版本");
        }
        if (missing.isEmpty() && !V4Features.fingerprint().equals(fingerprint)) {
            throw new IOException("权重声明了全部块，但指纹 " + fingerprint + " 与本服务端注册表 "
                    + V4Features.fingerprint() + " 不符");
        }

        Map<String, float[][]> mats = new HashMap<>();
        Map<String, float[]> vecs = new HashMap<>();
        Set<String> names = new HashSet<>();
        for (int i = 0; i < nTensors; i++) {
            int ln = bb.getShort() & 0xFFFF;
            byte[] nb = new byte[ln];
            bb.get(nb);
            String name = new String(nb, StandardCharsets.UTF_8);
            int nd = bb.get() & 0xFF;
            int[] shape = new int[nd];
            int total = 1;
            for (int j = 0; j < nd; j++) {
                shape[j] = bb.getInt();
                total *= shape[j];
            }
            float[] data = new float[total];
            for (int j = 0; j < total; j++) {
                data[j] = bb.getFloat();
            }
            if (!names.add(name)) {
                throw new IOException("权重里有重复张量名：" + name);
            }
            if (nd == 2) {
                float[][] m = new float[shape[0]][shape[1]];
                for (int r = 0; r < shape[0]; r++) {
                    System.arraycopy(data, r * shape[1], m[r], 0, shape[1]);
                }
                mats.put(name, m);
            } else if (nd == 1) {
                vecs.put(name, data);
            } else {
                throw new IOException("张量 " + name + " 是 " + nd + " 维（只支持 1/2 维）");
            }
        }
        if (bb.hasRemaining()) {
            throw new IOException("权重文件尾部多了 " + bb.remaining() + " 字节（张量表与头部不符）");
        }
        return new V4Policy(dModel, tileD, nHeads, valueBins, fingerprint, blocks, missing,
                mats, vecs, names);
    }

    // ---------------------------------------------------------------- 取张量
    /**
     * `heads.policy.weight` 的**兼容绑定**：接受 `[1, dm+3]`（新）或 `[1, dm]`（旧，右侧补 0）。
     *
     * <p>补 0 不是权宜近似：policy 的输入是 `[u ; sigmoid(belief_tenpai)]`，右侧 3 列乘 0
     * ⇒ 那三项对 logits 的贡献恒为 0 ⇒ 与"接 belief 之前"逐位相同。于是旧网照跑、新网用 belief，
     * 两者共用同一条前向代码（`forwardAll` 里只有 `mat(..., 1, dm+3)` 一种读法）。
     */
    private void matOrPadPolicy(int dm) throws IOException {
        float[][] w = mats.get("heads.policy.weight");
        if (w == null) {
            throw new IOException("权重缺张量：heads.policy.weight");
        }
        int cols = w.length > 0 ? w[0].length : 0;
        if (cols == dm + 3) {
            used.add("heads.policy.weight");
            return;
        }
        if (cols != dm) {
            throw new IOException("张量 heads.policy.weight 形状 [" + w.length + "," + cols
                    + "] != [" + 1 + "," + (dm + 3) + "]，也不是旧宽 [" + 1 + "," + dm + "]");
        }
        float[][] pad = new float[w.length][dm + 3];
        for (int i = 0; i < w.length; i++) {
            System.arraycopy(w[i], 0, pad[i], 0, dm);      // 后 3 列保持 0
        }
        mats.put("heads.policy.weight", pad);
        used.add("heads.policy.weight");
    }

    private float[][] mat(String name, int rows, int cols) throws IOException {        float[][] m = mats.get(name);
        if (m == null) {
            throw new IOException("权重缺张量：" + name);
        }
        if (m.length != rows || (rows > 0 && m[0].length != cols)) {
            throw new IOException("张量 " + name + " 形状 [" + m.length + ","
                    + (m.length > 0 ? m[0].length : 0) + "] != [" + rows + "," + cols + "]");
        }
        used.add(name);
        return m;
    }

    private float[] vec(String name, int n) throws IOException {
        float[] v = vecs.get(name);
        if (v == null) {
            throw new IOException("权重缺张量：" + name);
        }
        if (v.length != n) {
            throw new IOException("张量 " + name + " 长度 " + v.length + " != " + n);
        }
        used.add(name);
        return v;
    }

    /**
     * 把每一层要用的张量都取一遍 —— **同时**完成"形状核对 + 无多余张量"两件事。
     *
     * <p>这就是 Java 侧不需要再抄一份 74 行形状表的原因：前向要把每个张量都读一遍，
     * 读的时候顺手钉住 `[rows,cols]`，最后 {@link #used} 与文件里的名字集合比对 ⇒
     * 少张量、多张量、改名、形状不符**都在构造期**炸掉（不是等到对局里算出个奇怪的牌）。
     */
    private void bindAll() throws IOException {
        int dm = dModel;
        int td = tileD;
        int vb = valueBins;
        int hd = 3 * dm;
        mat("tile.enc.0.weight", td, V4Features.C_TILE);
        vec("tile.enc.0.bias", td);
        mat("tile.enc.2.weight", td, td);
        vec("tile.enc.2.bias", td);
        mat("tile.attn.in_proj_weight", 3 * td, td);
        vec("tile.attn.in_proj_bias", 3 * td);
        mat("tile.attn.out_proj.weight", td, td);
        vec("tile.attn.out_proj.bias", td);
        vec("tile.norm.weight", td);
        vec("tile.norm.bias", td);
        mat("tile.proj.weight", dm, td);
        vec("tile.proj.bias", dm);

        mat("event.enc.0.weight", dm, V4Features.C_EVT);
        vec("event.enc.0.bias", dm);
        mat("event.enc.2.weight", dm, dm);
        vec("event.enc.2.bias", dm);
        mat("event.cell.weight_ih", 3 * dm, dm);
        mat("event.cell.weight_hh", 3 * dm, dm);
        vec("event.cell.bias_ih", 3 * dm);
        vec("event.cell.bias_hh", 3 * dm);
        mat("event.tr.layers.0.self_attn.in_proj_weight", hd, dm);
        vec("event.tr.layers.0.self_attn.in_proj_bias", hd);
        mat("event.tr.layers.0.self_attn.out_proj.weight", dm, dm);
        vec("event.tr.layers.0.self_attn.out_proj.bias", dm);
        mat("event.tr.layers.0.linear1.weight", 2 * dm, dm);
        vec("event.tr.layers.0.linear1.bias", 2 * dm);
        mat("event.tr.layers.0.linear2.weight", dm, 2 * dm);
        vec("event.tr.layers.0.linear2.bias", dm);
        vec("event.tr.layers.0.norm1.weight", dm);
        vec("event.tr.layers.0.norm1.bias", dm);
        vec("event.tr.layers.0.norm2.weight", dm);
        vec("event.tr.layers.0.norm2.bias", dm);

        mat("ctx.net.0.weight", dm, V4Features.C_CTX);
        vec("ctx.net.0.bias", dm);
        mat("ctx.net.2.weight", dm, dm);
        vec("ctx.net.2.bias", dm);
        mat("cand.net.0.weight", dm, V4Features.C_CAND);
        vec("cand.net.0.bias", dm);
        mat("cand.net.2.weight", dm, dm);
        vec("cand.net.2.bias", dm);

        mat("fusion.tile_proj.weight", dm, td);
        vec("fusion.tile_proj.bias", dm);
        mat("fusion.q_tile.in_proj_weight", hd, dm);
        vec("fusion.q_tile.in_proj_bias", hd);
        mat("fusion.q_tile.out_proj.weight", dm, dm);
        vec("fusion.q_tile.out_proj.bias", dm);
        mat("fusion.q_evt.in_proj_weight", hd, dm);
        vec("fusion.q_evt.in_proj_bias", hd);
        mat("fusion.q_evt.out_proj.weight", dm, dm);
        vec("fusion.q_evt.out_proj.bias", dm);
        mat("fusion.gate.weight", dm, 2 * dm);
        vec("fusion.gate.bias", dm);
        mat("fusion.write.weight", dm, dm);
        vec("fusion.write.bias", dm);
        mat("fusion.film.weight", dm, dm);
        vec("fusion.film.bias", dm);
        mat("fusion.out.net.0.weight", dm, 3 * dm);
        vec("fusion.out.net.0.bias", dm);
        mat("fusion.out.net.2.weight", dm, dm);
        vec("fusion.out.net.2.bias", dm);

        // ⚠ **旧网兼容**（第四十九轮之前 policy 的输入宽 = `dm`，第四十九轮起 = `dm+3`）：
        //   旧宽在**右侧补 3 个 0** ⇒ 等价于那 3 个 `sigmoid(belief_tenpai)` 输入恒为 0
        //   ⇒ 前向与"接 belief 之前"**逐位相同**（不是近似）。为什么要留这条路：
        //   `p3-001` / 联赛 `g08` 等已训权重都还是旧宽，没有它全部作废（重训要几小时）。
        matOrPadPolicy(dm);
        vec("heads.policy.bias", 1);
        mat("heads.value.weight", vb, dm);
        vec("heads.value.bias", vb);
        mat("heads.placement.weight", 4, dm);
        vec("heads.placement.bias", 4);
        mat("heads.belief_hand.weight", 3 * V4Features.KIND_COUNT, dm);
        vec("heads.belief_hand.bias", 3 * V4Features.KIND_COUNT);
        mat("heads.belief_tenpai.weight", 3, dm);
        vec("heads.belief_tenpai.bias", 3);
        mat("heads.danger.weight", 4, dm);
        vec("heads.danger.bias", 4);
        mat("heads.effect.weight", 3, dm);
        vec("heads.effect.bias", 3);
    }

    // ---------------------------------------------------------------- 前向
    /** 四个推理头的输出：`policy[n] / value[valueBins] / belief_tenpai[3] / danger[n*4]`。 */
    public Map<String, float[]> forward(Map<String, Object> obs) throws IOException {
        Map<String, float[]> all = forwardAll(obs);
        return Map.of("policy", all.get("policy"), "value", all.get("value"),
                "belief_tenpai", all.get("belief_tenpai"), "danger", all.get("danger"));
    }

    /** 全部七个头（自检与 golden 对拍用）。 */
    public Map<String, float[]> forwardAll(Map<String, Object> obs) throws IOException {
        return forwardAll(V4Features.assemble(obs, missingBlocks));
    }

    /**
     * 全部七个头，**输入已经拼好的四张量**（无缓存路径 = 判据④的基准）。
     *
     * <p>分开这一层是为了两件事：① 性能基准要能把"特征"与"网络"分开计时
     * （两者的优化手段完全不同，混在一起报一个数就没法判断该动哪一边——
     * ⚠ **只报实测，不设指标**（`docs/FEATURES-V4.md` §8））；② **增量缓存**
     * （{@link #forwardCached}）要把"事件塔"换成只算新行的版本、其余原样复用 ——
     * 所以这里把事件塔的输出当参数传下去（{@link #forwardWith}）。
     */
    public Map<String, float[]> forwardAll(V4Features.Tensors t) throws IOException {
        return forwardWith(t, eventTowerFull(t.evt));
    }

    // ---------------------------------------------------------------- 增量事件缓存
    /**
     * 全部七个头，走**增量事件缓存**（`docs/FEATURES-V4.md` §5.3）。
     *
     * <p>与 {@link #forwardAll(V4Features.Tensors)} 的关系：**只有事件塔的前半段不同**
     * （逐行编码 + 注意力 `in_proj` 复用），从 Transformer 的注意力往后是**同一份代码**、
     * 同一批浮点值 ⇒ 七个头应当**逐位相同**（自检按 `floatToIntBits` 比）。
     *
     * <p>三条纪律（设计文档 §5.3）：① 隐状态每小局清零（小局键变了就丢槽）；
     * ② 缓存必须能从当前 obs 完整重建（{@link V4Cache#overlapOk} 逐行校验，不信"应该是它"）；
     * ③ 自检/对拍永远拿无缓存路径当基准（{@link #setCacheEnabled}）。
     */
    public Map<String, float[]> forwardCached(Map<String, Object> obs) throws IOException {
        V4Features.Tensors t = V4Features.assemble(obs, missingBlocks);
        if (!cacheEnabled || !missingBlocks.isEmpty()) {
            return forwardAll(t);                                // 关掉缓存 / 消融实验：走基准路径
        }
        return forwardWith(t, eventTowerCached(obs, t));
    }

    /**
     * 增量路径的事件塔：只编码**新事件**的行，并复用上一决策的注意力 `in_proj`。
     *
     * <p>复用什么、为什么安全（`v4/cache.py` 的 `EventStream` 同语义）：
     * 一条事件的 token 行只依赖 `(事件, 自己座位)`，它的 MLP 输出与 `in_proj` 也只依赖这一行
     * ⇒ 窗口整体左移（新事件在尾部）时，**幸存行可以按引用平移**，只有新增的 `delta` 行要算。
     * 代价从"每决策 60 行"降到"每决策 delta 行"（实测 delta 的中位数是 1–2）。
     */
    private float[][] eventTowerCached(Map<String, Object> obs, V4Features.Tensors t)
            throws IOException {
        final int k = cache.window();
        int seat = V4Features.i(obs.get("seat"), 0);
        List<Map<String, Object>> ev = V4Features.eventsOf(obs);
        int events = ev.size();
        String key = V4Features.handKey(obs);
        if (key == null) {
            return eventTowerFull(t.evt);                         // 没有 round 信息 ⇒ 不缓存
        }
        V4Cache.Slot s = cache.slot(seat);
        boolean rebuild = !s.valid || !key.equals(s.handKey);
        if (!rebuild && (events < s.seen || !cache.overlapOk(s, t.evt, events))) {
            cache.stale++;
            rebuild = true;                                       // 前缀对不上：丢掉整个槽
        }
        if (rebuild) {
            cache.cold++;
            s.clear();
            s.handKey = key;
            s.tok = new float[k][];
            s.enc = new float[k][];
            s.qkv = new float[k][];
            s.h = new float[dModel];                              // 冷启动 h=0（每小局清零）
            s.len = 0;
            s.seen = 0;
        } else {
            cache.hits++;
        }
        int delta = events - s.seen;
        int len = Math.min(events, k);
        int pad = k - len;                                        // 前部 padding 行数
        boolean reuseRows = cacheLevel >= 2;
        boolean reuseQkv = cacheLevel >= 3;
        if (!rebuild) {
            int shift = reuseRows ? delta : k;                     // 不复用行 ⇒ 视作整窗失效
            V4Cache.shiftRows(s.tok, shift);
            V4Cache.shiftRows(s.enc, shift);
            V4Cache.shiftRows(s.qkv, shift);
        }
        // ① 前部 padding 行：共享常量行（零 token 的编码；值必须与基准路径逐位相同）
        for (int p = 0; p < pad; p++) {
            s.tok[p] = padTok();
            s.enc[p] = padEnc();
            s.qkv[p] = padQkv();
        }
        // ② 真实事件行 [pad, k)：复用时只有新增的 delta 行要算，幸存行按引用平移
        int first = k - Math.min(delta, k);
        for (int p = pad; p < k; p++) {
            if (reuseRows && p < first) {
                cache.rowsReused++;
                continue;                                          // 幸存的编码行原位复用
            }
            float[] tok = reuseRows ? eventRowOf(ev.get(events - k + p), seat) : t.evt[p];
            s.tok[p] = tok;
            s.enc[p] = eventMlpRow(tok);
            cache.rowsEncoded++;
        }
        // ③ 注意力 in_proj：level ≥ 3 复用（幸存行保留平移过来的 `qkv`），否则**逐行重算**
        if (reuseQkv) {
            for (int p = Math.max(pad, first); p < k; p++) {
                s.qkv[p] = eventQkvRow(s.enc[p]);
            }
        } else {
            for (int p = pad; p < k; p++) {
                s.qkv[p] = eventQkvRow(s.enc[p]);
            }
        }
        // ④ 隐状态：建槽时重放**全部**事件（`EventStream.recompute()` 语义），否则只推进新行
        float[][] gih = mat("event.cell.weight_ih", 3 * dModel, dModel);
        float[][] ghh = mat("event.cell.weight_hh", 3 * dModel, dModel);
        float[] gbi = vec("event.cell.bias_ih", 3 * dModel);
        float[] gbh = vec("event.cell.bias_hh", 3 * dModel);
        if (rebuild) {
            float[] tok = new float[V4Features.C_EVT];
            for (int i = 0; i < events; i++) {
                V4Features.eventRow(ev.get(i), seat, tok);
                s.h = gruStep(eventMlpRow(tok), s.h, gih, ghh, gbi, gbh);
            }
        } else {
            for (int i = s.seen; i < events; i++) {
                float[] row = (reuseRows && i >= events - k) ? s.enc[k - (events - i)]
                        : eventMlpRow(eventRowOf(ev.get(i), seat));
                s.h = gruStep(row, s.h, gih, ghh, gbi, gbh);
            }
        }
        s.seen = events;
        s.len = len;
        s.valid = true;
        return transformerFromQkv(s.enc, s.qkv, "event.tr.layers.0");
    }

    private float[] eventRowOf(Map<String, Object> event, int seat) {
        float[] tok = new float[V4Features.C_EVT];
        V4Features.eventRow(event, seat, tok);
        return tok;
    }

    /** 零 token 行（padding 的 token）。 */
    private float[] padTok() {
        if (padTokRow == null) {
            padTokRow = new float[V4Features.C_EVT];
        }
        return padTokRow;
    }

    /** 零 token 行的编码（padding 行常量；`linear(0) == b`）。 */
    private float[] padEnc() throws IOException {
        if (padEncRow == null) {
            padEncRow = eventMlpRow(padTok());
        }
        return padEncRow;
    }

    private float[] padQkv() throws IOException {
        if (padQkvRow == null) {
            padQkvRow = eventQkvRow(padEnc());
        }
        return padQkvRow;
    }

    /** 一条事件 token 行 → 事件编码器输出（`event.enc` 两层，**末尾没有 ReLU**）。 */
    private float[] eventMlpRow(float[] tok) throws IOException {
        float[] e = new float[dModel];
        linear(e, tok, mat("event.enc.0.weight", dModel, V4Features.C_EVT),
                vec("event.enc.0.bias", dModel));
        relu(e);
        linear(e, e, mat("event.enc.2.weight", dModel, dModel),
                vec("event.enc.2.bias", dModel));
        return e;
    }

    /** 一行事件表示的注意力 `in_proj`（在 `LayerNorm1` 之后；逐行可缓存的那一块）。 */
    private float[] eventQkvRow(float[] enc) throws IOException {
        float[] xn = Arrays.copyOf(enc, dModel);
        layerNorm(xn, vec("event.tr.layers.0.norm1.weight", dModel),
                vec("event.tr.layers.0.norm1.bias", dModel));
        float[] qkv = new float[3 * dModel];
        float[][] inW = mat("event.tr.layers.0.self_attn.in_proj_weight", 3 * dModel, dModel);
        float[] inB = vec("event.tr.layers.0.self_attn.in_proj_bias", 3 * dModel);
        projRows(qkv, xn, inW, inB, 0, 3 * dModel);
        return qkv;
    }

    /** 事件塔（全量路径）：喂满 K 个 token 的冷启动 —— 判据④的基准。 */
    private float[][] eventTowerFull(float[][] evt) throws IOException {
        float[][] e = new float[evt.length][];
        for (int i = 0; i < e.length; i++) {
            e[i] = eventMlpRow(evt[i]);
        }
        float[][] gih = mat("event.cell.weight_ih", 3 * dModel, dModel);
        float[][] ghh = mat("event.cell.weight_hh", 3 * dModel, dModel);
        float[] gbi = vec("event.cell.bias_ih", 3 * dModel);
        float[] gbh = vec("event.cell.bias_hh", 3 * dModel);
        float[] h = new float[dModel];                            // 冷启动 h=0，喂满 K 个 token
        for (int i = 0; i < e.length; i++) {
            h = gruStep(e[i], h, gih, ghh, gbi, gbh);
        }
        lastFullHidden = h;
        return transformerLayer(e, "event.tr.layers.0");
    }

    /**
     * 七个头的主体：**事件塔的输出从参数进来**（`eTokens[K][dm]`），其余全量共用。
     *
     * <p>这就是"增量 == 全量"的判据能成立的原因：两条路径从 Transformer 的注意力往后
     * 是**同一份代码、同一批浮点值**（`eTokens` 逐位相同）⇒ 七个头逐位相同，
     * 不是"差一点点"。⚠ 别把这里的顺序改成"看起来等价"的样子（浮点加法不满足结合律）。
     */
    private Map<String, float[]> forwardWith(V4Features.Tensors t, float[][] eTokens)
            throws IOException {
        int n = t.cand.length;
        int dm = dModel;

        // ---- 牌种塔
        float[][] ht = new float[V4Features.KIND_COUNT][tileD];
        float[][] w1 = mat("tile.enc.0.weight", tileD, V4Features.C_TILE);
        float[] b1 = vec("tile.enc.0.bias", tileD);
        float[][] w2 = mat("tile.enc.2.weight", tileD, tileD);
        float[] b2 = vec("tile.enc.2.bias", tileD);
        for (int k = 0; k < ht.length; k++) {
            linear(ht[k], t.tile[k], w1, b1);
            relu(ht[k]);
            linear(ht[k], ht[k], w2, b2);
            relu(ht[k]);
        }
        float[][] at = mha(ht, ht, tileD, "tile.attn");
        float[] normG = vec("tile.norm.weight", tileD);
        float[] normB = vec("tile.norm.bias", tileD);
        float[] pool = new float[tileD];
        for (int k = 0; k < ht.length; k++) {
            for (int i = 0; i < tileD; i++) {
                ht[k][i] += at[k][i];
            }
            layerNorm(ht[k], normG, normB);
            for (int i = 0; i < tileD; i++) {
                pool[i] += ht[k][i] / ht.length;                 // mean over tokens
            }
        }
        float[] hTilePool = new float[dm];
        linear(hTilePool, pool, mat("tile.proj.weight", dm, tileD), vec("tile.proj.bias", dm));

        // ---- ctx / cand 编码器
        float[] ctxEmb = mlp(t.ctx, "ctx.net", V4Features.C_CTX, dm);
        float[][] candEmb = new float[n][];
        for (int i = 0; i < n; i++) {
            candEmb[i] = mlp(t.cand[i], "cand.net", V4Features.C_CAND, dm);
        }

        // ---- 融合
        float[][] tpW = mat("fusion.tile_proj.weight", dm, tileD);
        float[] tpB = vec("fusion.tile_proj.bias", dm);
        float[][] kvTile = new float[ht.length][dm];
        for (int k = 0; k < ht.length; k++) {
            linear(kvTile[k], ht[k], tpW, tpB);
        }
        float[][] uTile = mha(candEmb, kvTile, dm, "fusion.q_tile");
        float[][] uEvt = mha(candEmb, eTokens, dm, "fusion.q_evt");
        float[][] gateW = mat("fusion.gate.weight", dm, 2 * dm);
        float[] gateB = vec("fusion.gate.bias", dm);
        float[][] writeW = mat("fusion.write.weight", dm, dm);
        float[] writeB = vec("fusion.write.bias", dm);
        float[][] filmW = mat("fusion.film.weight", dm, dm);
        float[] filmB = vec("fusion.film.bias", dm);
        float[][] u = new float[n][dm];
        float[] cat = new float[2 * dm];
        float[] state = new float[dm];
        float[] cat3 = new float[3 * dm];
        for (int i = 0; i < n; i++) {
            System.arraycopy(uEvt[i], 0, cat, 0, dm);
            System.arraycopy(uTile[i], 0, cat, dm, dm);
            float[] g = new float[dm];
            linear(g, cat, gateW, gateB);
            sigmoid(g);
            float[] w = new float[dm];
            linear(w, uEvt[i], writeW, writeB);
            for (int j = 0; j < dm; j++) {
                state[j] = hTilePool[j] + g[j] * w[j];
            }
            float[] f = new float[dm];
            linear(f, state, filmW, filmB);
            tanh(f);
            for (int j = 0; j < dm; j++) {
                f[j] = uEvt[i][j] * (1f + f[j]);
            }
            System.arraycopy(uTile[i], 0, cat3, 0, dm);
            System.arraycopy(f, 0, cat3, dm, dm);
            System.arraycopy(ctxEmb, 0, cat3, 2 * dm, dm);
            u[i] = mlp(cat3, "fusion.out.net", 3 * dm, dm);
        }

        // ---- 头
        // ⚠ **policy 的输入是 `[u ; sigmoid(belief_tenpai)]`（宽度 dm+3）**（2026-09-30 第四十九轮）：
        //   实测 `belief_tenpai` 的 AUC 0.978（BCE 比边缘基线好 73%），但它原先**与 policy 不互通**
        //   （各头各算、算完即丢）⇒ 押し引き的输入信息在模型手里却没接上。顺序必须是
        //   **u 在前、三个听牌概率在后**（与 `python/mahjong_ml/v4/model.py` 的 `Heads.forward`
        //   逐位同序）；因此 bt 必须在 policy 之前算出来 ⇒ 先求 `meanU`，再求 bt，最后出 policy。
        float[] policy = new float[n];
        float[][] danger = new float[n][];
        float[][] effect = new float[n][];
        float[][] polW = mat("heads.policy.weight", 1, dm + 3);
        float polB = vec("heads.policy.bias", 1)[0];
        float[][] danW = mat("heads.danger.weight", 4, dm);
        float[] danB = vec("heads.danger.bias", 4);
        float[][] effW = mat("heads.effect.weight", 3, dm);
        float[] effB = vec("heads.effect.bias", 3);
        float[] meanU = new float[dm];
        for (int i = 0; i < n; i++) {
            danger[i] = new float[4];
            linear(danger[i], u[i], danW, danB);
            effect[i] = new float[3];
            linear(effect[i], u[i], effW, effB);
            for (int j = 0; j < dm; j++) {
                meanU[j] += u[i][j] / Math.max(1, n);
            }
        }
        float[] bt = new float[3];
        linear(bt, meanU, mat("heads.belief_tenpai.weight", 3, dm),
                vec("heads.belief_tenpai.bias", 3));
        for (int j = 0; j < 3; j++) {
            // ⚠ 这里用 sigmoid 后的**概率**（与 Python 侧 `torch.sigmoid(bt)` 一致）；
            //   而**输出的 `belief_tenpai` 仍然是 logits**（见下面的 `head(...)`）—— 别把两者搞混。
            bt[j] = (float) (1.0 / (1.0 + Math.exp(-bt[j])));
        }
        float[] polVec = new float[dm + 3];
        for (int i = 0; i < n; i++) {
            System.arraycopy(u[i], 0, polVec, 0, dm);
            System.arraycopy(bt, 0, polVec, dm, 3);
            policy[i] = polB + dot(polW[0], polVec);
        }
        if (n == 0) {
            meanU = new float[dm];
        }
        Map<String, float[]> out = new LinkedHashMap<>();
        out.put("policy", policy);
        out.put("value", head(meanU, "value", valueBins));
        out.put("placement", head(meanU, "placement", 4));
        out.put("belief_hand", head(meanU, "belief_hand", 3 * V4Features.KIND_COUNT));
        out.put("belief_tenpai", head(meanU, "belief_tenpai", 3));
        out.put("danger", flatten(danger, 4));
        out.put("effect", flatten(effect, 3));
        return out;
    }

    private float[] head(float[] state, String name, int width) throws IOException {
        float[] out = new float[width];
        linear(out, state, mat("heads." + name + ".weight", width, dModel),
                vec("heads." + name + ".bias", width));
        return out;
    }

    private static float[] flatten(float[][] rows, int cols) {
        float[] out = new float[rows.length * cols];
        for (int i = 0; i < rows.length; i++) {
            System.arraycopy(rows[i], 0, out, i * cols, cols);
        }
        return out;
    }

    /** 策略头 logits（逐候选，顺序 = `obs.legal`）。 */
    public float[] logits(Map<String, Object> json) throws IOException {
        return (cacheEnabled ? forwardCached(json) : forward(json)).get("policy");
    }

    // ---------------------------------------------------------------- 缓存的开关与自检钩子
    /**
     * 开/关增量事件缓存（**默认开**）。
     *
     * <p>设计文档 §5.3 纪律③：自检与对拍**永远用无缓存路径当基准** —— 所以这个开关是判据的一部分，
     * 不是"调试用的旋钮"。关掉之后 `logits` 走 {@link #forwardAll(Map)}（全量重算）。
     */
    public void setCacheEnabled(boolean on) {
        cacheEnabled = on;
        if (!on) {
            cache.clearAll();
        }
    }

    /**
     * 新实例的默认档（`Main --no-v4-cache` 用）。
     *
     * <p>只影响**之后**创建的实例 —— 已经在跑的对局不受影响（换档不该悄悄改一份正在用的权重）。
     */
    public static void setCacheDefault(boolean on) {
        cacheDefault = on;
    }

    public boolean cacheEnabled() {
        return cacheEnabled;
    }

    /** 缓存深度（1/2/3；见 {@link #cacheLevel}）。关缓存时无意义。 */
    public void setCacheLevel(int level) {
        this.cacheLevel = Math.max(1, Math.min(3, level));
        cache.clearAll();
    }

    public int cacheLevel() {
        return cacheLevel;
    }

    /** 缓存计数（自检/基准要看"到底命中没有"——全 miss 的缓存等于没写）。 */
    public String cacheStats() {
        return cache.stats();
    }

    public long cacheHits() {
        return cache.hits;
    }

    public long cacheCold() {
        return cache.cold;
    }

    public long cacheStale() {
        return cache.stale;
    }

    public long cacheRowsEncoded() {
        return cache.rowsEncoded;
    }

    public long cacheRowsReused() {
        return cache.rowsReused;
    }

    public void resetCacheStats() {
        cache.resetStats();
    }

    /**
     * 增量路径的隐状态（该座位当前小局的 carry）。
     *
     * <p>⚠ 它与**全量路径**的 `h`（{@link #debugFullHidden()}，窗口 60 个 token 的冷启动）
     * 是两个不同的量 —— 见 {@code docs/FEATURES-V4.md} §5.3 的口径说明。当前模型里
     * `h` **不被任何头消费**（融合只读 `eTokens`），所以两者不同不影响任何输出；
     * 自检把"h 不同但七头逐位相同"钉成一条判据，将来谁把 `h` 接进融合，这条就会红。
     */
    public float[] cachedHidden(Map<String, Object> obs) throws IOException {
        forwardCached(obs);
        return cache.slot(V4Features.i(obs.get("seat"), 0)).h;
    }

    /** 全量路径最后一次前向里的冷启动 `h`（自检对比用；见 {@link #cachedHidden}）。 */
    public float[] debugFullHidden() {
        return lastFullHidden;
    }

    /** 缓存槽当前的窗口 token 行（自检用：必须与 `V4Features.eventMatrix` 逐位相同）。 */
    public float[][] debugCachedWindow(int seat) {
        return cache.slot(seat).tok;
    }

    @Override
    public int chooseIndex(Map<String, Object> json, List<String> keys, int priorIndex, float alpha) {
        try {
            float[] out = logits(json);
            if (priorIndex >= 0 && priorIndex < out.length && alpha > 0f) {
                out[priorIndex] += alpha;
            }
            return Logits.argmaxOf(out);
        } catch (IOException | RuntimeException e) {
            Log.warn("v4 网络前向失败：" + e.getMessage());
            return -1;                                          // 调用方兜底（Policies.fromAction / hybrid）
        }
    }

    @Override
    public int sampleIndex(Map<String, Object> json, List<String> keys, int priorIndex, float alpha,
                           float temp, Random rng) {
        try {
            float[] out = logits(json);
            if (priorIndex >= 0 && priorIndex < out.length && alpha > 0f) {
                out[priorIndex] += alpha;
            }
            if (!(temp > 0f)) {
                return Logits.argmaxOf(out);
            }
            return Logits.sampleSoftmax(out, temp, rng);
        } catch (IOException | RuntimeException e) {
            Log.warn("v4 网络前向失败：" + e.getMessage());
            return -1;
        }
    }

    @Override
    public Action choose(Decision d) {
        return chooseWithPrior(d, null, 0f);
    }

    // ---------------------------------------------------------------- 元信息 / 自检钩子
    public int paramCount() {
        int total = 0;
        for (float[][] m : mats.values()) {
            for (float[] row : m) {
                total += row.length;
            }
        }
        for (float[] v : vecs.values()) {
            total += v.length;
        }
        return total;
    }

    public Map<String, Integer> dims() {
        Map<String, Integer> m = new LinkedHashMap<>();
        m.put("d_model", dModel);
        m.put("tile_d", tileD);
        m.put("n_heads", nHeads);
        m.put("value_bins", valueBins);
        return m;
    }

    public String blockFingerprint() {
        return fingerprint;
    }

    public List<String> blockList() {
        return blocks;
    }

    public Set<String> missingBlocks() {
        return missingBlocks;
    }

    /** 自检用：返回一份"策略头输出偏置整体偏移 delta"的副本（红证：每条 logit 必须恰好 +delta）。 */
    public V4Policy debugOutputBiasShift(float delta) {
        Map<String, float[]> v2 = new HashMap<>(vecs);
        float[] bias = vecs.get("heads.policy.bias").clone();
        bias[0] += delta;
        v2.put("heads.policy.bias", bias);
        try {
            return new V4Policy(dModel, tileD, nHeads, valueBins, fingerprint, blocks, missingBlocks,
                    new HashMap<>(mats), v2, new HashSet<>(allNames));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    public String describe() {
        return "V4Policy（" + paramCount() + " 参数，格式 " + FORMAT_VERSION + "，dims=" + dims()
                + "，块 " + blocks.size() + " / 注册表 " + V4Features.BLOCK_IDS.length
                + "，指纹 " + fingerprint + "）";
    }

    // ---------------------------------------------------------------- 算子（顺序固定 = 与 Python/C++ 逐位对拍的基础）
    /**
     * `out = W·x + b`（W 行主序 `[out][in]`，**单累加器顺序求和** —— 别改成并行规约）。
     *
     * <p>⚠ **别名安全**：`out === x` 时先复制一份再算。torch 的 `nn.Linear` 天然不别名，
     * 而这里为了少分配会把隐藏层原地覆盖（`linear(h, h, …)`）—— 少了这一句，
     * 第 r 行就会读到已被改写的 `x[0..r)`（2026-09-27 对拍抓到：logits 整体偏 0.0044）。
     */
    static void linear(float[] out, float[] x, float[][] w, float[] b) {
        float[] src = (out == x) ? Arrays.copyOf(x, x.length) : x;
        for (int r = 0; r < out.length; r++) {
            float s = b[r];
            float[] row = w[r];
            for (int c = 0; c < src.length; c++) {
                s += row[c] * src[c];
            }
            out[r] = s;
        }
    }

    static float dot(float[] a, float[] b) {
        float s = 0f;
        for (int i = 0; i < a.length; i++) {
            s += a[i] * b[i];
        }
        return s;
    }

    static void relu(float[] x) {
        for (int i = 0; i < x.length; i++) {
            if (x[i] < 0f) {
                x[i] = 0f;
            }
        }
    }

    static void sigmoid(float[] x) {
        for (int i = 0; i < x.length; i++) {
            x[i] = (float) (1.0 / (1.0 + Math.exp(-x[i])));
        }
    }

    static void tanh(float[] x) {
        for (int i = 0; i < x.length; i++) {
            x[i] = (float) Math.tanh(x[i]);
        }
    }

    /** 原地 LayerNorm（**有偏方差**，eps = 1e-5，与 torch 缺省一致）。 */
    static void layerNorm(float[] x, float[] g, float[] b) {
        int n = x.length;
        float mean = 0f;
        for (float v : x) {
            mean += v / n;
        }
        float var = 0f;
        for (float v : x) {
            float d = v - mean;
            var += d * d / n;
        }
        float inv = (float) (1.0 / Math.sqrt(var + LN_EPS));
        for (int i = 0; i < n; i++) {
            x[i] = (x[i] - mean) * inv * g[i] + b[i];
        }
    }

    /** `Mlp`（=`Linear + ReLU + Linear + ReLU`，注意**末尾那个 ReLU**）。 */
    private float[] mlp(float[] x, String prefix, int cin, int dm) throws IOException {
        float[] h = new float[dm];
        linear(h, x, mat(prefix + ".0.weight", dm, cin), vec(prefix + ".0.bias", dm));
        relu(h);
        float[] out = new float[dm];
        linear(out, h, mat(prefix + ".2.weight", dm, dm), vec(prefix + ".2.bias", dm));
        relu(out);
        return out;
    }

    /** 多头注意力（单样本、无 mask）：`q` 对 `kv` 做注意力，`prefix` 给出三块权重。 */
    private float[][] mha(float[][] q, float[][] kv, int d, String prefix) throws IOException {
        float[][] inW = mat(prefix + ".in_proj_weight", 3 * d, d);
        float[] inB = vec(prefix + ".in_proj_bias", 3 * d);
        float[][] outW = mat(prefix + ".out_proj.weight", d, d);
        float[] outB = vec(prefix + ".out_proj.bias", d);
        int lq = q.length;
        int lk = kv.length;
        int hd = d / nHeads;
        float scale = (float) (1.0 / Math.sqrt(hd));
        float[][] qp = new float[lq][d];
        float[][] kp = new float[lk][d];
        float[][] vp = new float[lk][d];
        for (int t = 0; t < lq; t++) {
            projRows(qp[t], q[t], inW, inB, 0, d);
        }
        for (int s = 0; s < lk; s++) {
            projRows(kp[s], kv[s], inW, inB, d, d);
            projRows(vp[s], kv[s], inW, inB, 2 * d, d);
        }
        float[] scores = new float[lk];
        float[][] ctx = new float[lq][d];
        for (int t = 0; t < lq; t++) {
            for (int hh = 0; hh < nHeads; hh++) {
                int base = hh * hd;
                for (int s = 0; s < lk; s++) {
                    float sum = 0f;
                    for (int i = 0; i < hd; i++) {
                        sum += qp[t][base + i] * kp[s][base + i];
                    }
                    scores[s] = sum * scale;
                }
                softmax(scores);
                for (int i = 0; i < hd; i++) {
                    float sum = 0f;
                    for (int s = 0; s < lk; s++) {
                        sum += scores[s] * vp[s][base + i];
                    }
                    ctx[t][base + i] = sum;
                }
            }
        }
        float[][] out = new float[lq][d];
        for (int t = 0; t < lq; t++) {
            linear(out[t], ctx[t], outW, outB);
        }
        return out;
    }

    private static void projRows(float[] out, float[] x, float[][] w, float[] b, int rowOff, int d) {
        for (int i = 0; i < d; i++) {
            float s = b[rowOff + i];
            float[] row = w[rowOff + i];
            for (int c = 0; c < x.length; c++) {
                s += row[c] * x[c];
            }
            out[i] = s;
        }
    }

    /** 原地 softmax（减去最大值再 exp）。 */
    static void softmax(float[] x) {
        float max = Float.NEGATIVE_INFINITY;
        for (float v : x) {
            if (v > max) {
                max = v;
            }
        }
        float sum = 0f;
        for (int i = 0; i < x.length; i++) {
            x[i] = (float) Math.exp(x[i] - max);
            sum += x[i];
        }
        if (sum > 0f) {
            for (int i = 0; i < x.length; i++) {
                x[i] /= sum;
            }
        }
    }

    /**
     * GRU 一步（PyTorch `nn.GRUCell` 的 r/z/n 三段顺序）。
     *
     * <p>⚠ **返回新数组**，不复用 `h`：`gh` 要读**整条旧 h**，原地写会把"还没读到的下标"
     * 换成新值（错得很隐蔽：小网络上看不出，训练久了才偏）。
     */
    static float[] gruStep(float[] x, float[] h, float[][] wih, float[][] whh,
                           float[] bih, float[] bhh) {
        int d = x.length;
        float[] out = new float[d];
        for (int i = 0; i < d; i++) {
            float ir = bih[i] + rowDot(wih[i], x);
            float iz = bih[d + i] + rowDot(wih[d + i], x);
            float in = bih[2 * d + i] + rowDot(wih[2 * d + i], x);
            float hr = bhh[i] + rowDot(whh[i], h);
            float hz = bhh[d + i] + rowDot(whh[d + i], h);
            float hn = bhh[2 * d + i] + rowDot(whh[2 * d + i], h);
            float r = (float) (1.0 / (1.0 + Math.exp(-(ir + hr))));
            float z = (float) (1.0 / (1.0 + Math.exp(-(iz + hz))));
            float n = (float) Math.tanh(in + r * hn);
            out[i] = (1f - z) * n + z * h[i];
        }
        return out;
    }

    private static float rowDot(float[] w, float[] x) {
        float s = 0f;
        for (int i = 0; i < x.length; i++) {
            s += w[i] * x[i];
        }
        return s;
    }

    /**
     * 逐行算 `in_proj(LayerNorm1(x))`（`[len][3d]`）—— **可跨决策缓存**的那一块。
     *
     * <p>`norm1` 与 `in_proj` 都是逐行运算（不跨行混合）⇒ 只要那一行的输入没变，结果逐位不变。
     * 事件窗口整体左移时，幸存事件的行按引用平移即可（`V4Cache.shiftRows`）。
     */
    private float[][] attentionQkv(float[][] x, String prefix) throws IOException {
        int len = x.length;
        int d = dModel;
        float[] n1g = vec(prefix + ".norm1.weight", d);
        float[] n1b = vec(prefix + ".norm1.bias", d);
        float[][] inW = mat(prefix + ".self_attn.in_proj_weight", 3 * d, d);
        float[] inB = vec(prefix + ".self_attn.in_proj_bias", 3 * d);
        float[][] qkv = new float[len][3 * d];
        float[] xn = new float[d];
        for (int i = 0; i < len; i++) {
            System.arraycopy(x[i], 0, xn, 0, d);
            layerNorm(xn, n1g, n1b);
            projRows(qkv[i], xn, inW, inB, 0, 3 * d);
        }
        return qkv;
    }

    /**
     * `in_proj` 已算好的那一层（增量缓存路径）：注意力 + `out_proj` + `norm2` + FFN。
     *
     * @param x   这一层的输入行（`enc`；必须与 `qkv` 的 `norm1` 输入**逐位对应**）
     * @param qkv 逐行 `[q|k|v]`（长度 3d）
     */
    private float[][] transformerFromQkv(float[][] x, float[][] qkv, String prefix)
            throws IOException {
        int len = x.length;
        int d = dModel;
        float[][] att = attentionFromQkv(qkv, d, prefix + ".self_attn");
        float[] n2g = vec(prefix + ".norm2.weight", d);
        float[] n2b = vec(prefix + ".norm2.bias", d);
        float[][] f1W = mat(prefix + ".linear1.weight", 2 * d, d);
        float[] f1B = vec(prefix + ".linear1.bias", 2 * d);
        float[][] f2W = mat(prefix + ".linear2.weight", d, 2 * d);
        float[] f2B = vec(prefix + ".linear2.bias", d);
        float[][] out = new float[len][d];
        for (int i = 0; i < len; i++) {
            float[] y = new float[d];
            for (int j = 0; j < d; j++) {
                y[j] = x[i][j] + att[i][j];
            }
            float[] y2 = Arrays.copyOf(y, d);
            layerNorm(y2, n2g, n2b);
            float[] hid = new float[2 * d];
            linear(hid, y2, f1W, f1B);
            relu(hid);
            float[] res = new float[d];
            linear(res, hid, f2W, f2B);
            for (int j = 0; j < d; j++) {
                out[i][j] = y[j] + res[j];
            }
        }
        return out;
    }

    /** 注意力 + `out_proj`（`qkv[i]` = 第 i 行的 `[q|k|v]`，三段都是 d）。 */
    private float[][] attentionFromQkv(float[][] qkv, int d, String prefix) throws IOException {
        int len = qkv.length;
        int hd = d / nHeads;
        float scale = (float) (1.0 / Math.sqrt(hd));
        float[][] outW = mat(prefix + ".out_proj.weight", d, d);
        float[] outB = vec(prefix + ".out_proj.bias", d);
        float[] scores = new float[len];
        float[][] ctx = new float[len][d];
        for (int t = 0; t < len; t++) {
            for (int hh = 0; hh < nHeads; hh++) {
                int base = hh * hd;
                for (int s = 0; s < len; s++) {
                    float sum = 0f;
                    for (int i = 0; i < hd; i++) {
                        sum += qkv[t][base + i] * qkv[s][d + base + i];
                    }
                    scores[s] = sum * scale;
                }
                softmax(scores);
                for (int i = 0; i < hd; i++) {
                    float sum = 0f;
                    for (int s = 0; s < len; s++) {
                        sum += scores[s] * qkv[s][2 * d + base + i];
                    }
                    ctx[t][base + i] = sum;
                }
            }
        }
        float[][] out = new float[len][d];
        for (int t = 0; t < len; t++) {
            linear(out[t], ctx[t], outW, outB);
        }
        return out;
    }

    /**
     * `nn.TransformerEncoderLayer(d, nhead, dim_feedforward=2d, dropout=0, norm_first=True)` 一层。
     *
     * <p>norm_first 的顺序：`x = x + attn(norm1(x))`，再 `x = x + linear2(relu(linear1(norm2(x))))`。
     */
    private float[][] transformerLayer(float[][] x, String prefix) throws IOException {
        return transformerFromQkv(x, attentionQkv(x, prefix), prefix);
    }

    /** 小局身份与座位（自检/基准要按同一把尺子分组；实现见 `V4Features.handKey`）。 */
    public static String handKeyOf(Map<String, Object> obs) {
        return V4Features.handKey(obs);
    }

    public static int seatOf(Map<String, Object> obs) {
        return V4Features.i(obs.get("seat"), 0);
    }

    /** 头部里那个 `valueBins` 与推理头的宽度（自检读夹具时要用）。 */
    public int valueBins() {
        return valueBins;
    }
}
