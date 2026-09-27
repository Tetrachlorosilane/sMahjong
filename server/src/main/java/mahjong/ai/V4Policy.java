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
    private float[][] mat(String name, int rows, int cols) throws IOException {
        float[][] m = mats.get(name);
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

        mat("heads.policy.weight", 1, dm);
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
     * 全部七个头，**输入已经拼好的四张量**。
     *
     * <p>分开这一层是为了两件事：① 性能基准要能把"特征"与"网络"分开计时
     * （P5 验收就是两个数：≤2 ms 特征 + ≤1.5 ms 网络）；② 将来的**增量缓存**
     * 要复用上一决策的张量（`v4/cache.py` 的 `EventStream`）—— 那时调用方自己拼。
     */
    public Map<String, float[]> forwardAll(V4Features.Tensors t) throws IOException {
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

        // ---- 事件塔
        float[][] e = new float[V4Features.K_EVT][dm];
        float[][] ew1 = mat("event.enc.0.weight", dm, V4Features.C_EVT);
        float[] eb1 = vec("event.enc.0.bias", dm);
        float[][] ew2 = mat("event.enc.2.weight", dm, dm);
        float[] eb2 = vec("event.enc.2.bias", dm);
        for (int i = 0; i < e.length; i++) {
            linear(e[i], t.evt[i], ew1, eb1);
            relu(e[i]);
            linear(e[i], e[i], ew2, eb2);                        // ⚠ 这里**没有** ReLU（与训练一致）
        }
        float[][] gih = mat("event.cell.weight_ih", 3 * dm, dm);
        float[][] ghh = mat("event.cell.weight_hh", 3 * dm, dm);
        float[] gbi = vec("event.cell.bias_ih", 3 * dm);
        float[] gbh = vec("event.cell.bias_hh", 3 * dm);
        float[] h = new float[dm];                               // 冷启动 h=0，喂满 K 个 token
        for (int i = 0; i < e.length; i++) {
            h = gruStep(e[i], h, gih, ghh, gbi, gbh);
        }
        float[][] x2 = transformerLayer(e, "event.tr.layers.0");
        float[][] eTokens = x2;

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
        float[] policy = new float[n];
        float[][] danger = new float[n][];
        float[][] effect = new float[n][];
        float[][] polW = mat("heads.policy.weight", 1, dm);
        float polB = vec("heads.policy.bias", 1)[0];
        float[][] danW = mat("heads.danger.weight", 4, dm);
        float[] danB = vec("heads.danger.bias", 4);
        float[][] effW = mat("heads.effect.weight", 3, dm);
        float[] effB = vec("heads.effect.bias", 3);
        float[] meanU = new float[dm];
        for (int i = 0; i < n; i++) {
            policy[i] = polB + dot(polW[0], u[i]);
            danger[i] = new float[4];
            linear(danger[i], u[i], danW, danB);
            effect[i] = new float[3];
            linear(effect[i], u[i], effW, effB);
            for (int j = 0; j < dm; j++) {
                meanU[j] += u[i][j] / Math.max(1, n);
            }
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
        return forward(json).get("policy");
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
     * `nn.TransformerEncoderLayer(d, nhead, dim_feedforward=2d, dropout=0, norm_first=True)` 一层。
     *
     * <p>norm_first 的顺序：`x = x + attn(norm1(x))`，再 `x = x + linear2(relu(linear1(norm2(x))))`。
     */
    private float[][] transformerLayer(float[][] x, String prefix) throws IOException {
        int len = x.length;
        int d = dModel;
        float[] n1g = vec(prefix + ".norm1.weight", d);
        float[] n1b = vec(prefix + ".norm1.bias", d);
        float[][] xn = new float[len][d];
        for (int i = 0; i < len; i++) {
            xn[i] = Arrays.copyOf(x[i], d);
            layerNorm(xn[i], n1g, n1b);
        }
        float[][] att = mha(xn, xn, d, prefix + ".self_attn");
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

    /** 头部里那个 `valueBins` 与推理头的宽度（自检读夹具时要用）。 */
    public int valueBins() {
        return valueBins;
    }
}
