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
    /** v4 = 格式 **3**（64 B 头 + arch 段 + 块清单 + 张量表）；v3 = 1，格式 2 已退役。 */
    public static final int FORMAT_VERSION = 3;
    /** 格式 2 = A− 之前的老包（没有 arch 段）。读到 ⇒ **专用错误**（⛔ 不自动升级）。 */
    public static final int FORMAT_RETIRED = 2;
    /** 头部字节数（9 个 u32 + 2 个 u32 + 16 B 指纹 + 1 个 u32）。 */
    public static final int HEADER_BYTES = 64;
    /** arch 段：总长固定 2,048 B = 96 B 核心 + 1,952 B 保留尾（必须全 0）。 */
    public static final int ARCH_SEG_BYTES = 2048;
    /** arch 段核心（含首 4 B 的 `archLen`）。 */
    public static final int ARCH_CORE_BYTES = 96;
    /** `archVersion`：1 = 本表。>1 ⇒ 报错（⛔ 不猜新字段）。 */
    public static final int ARCH_VERSION = 1;
    /** `topoId`：2 = v4（格式 2 时代的旧图顺延）、3 = A−/T1。未注册 ⇒ 报错。 */
    public static final int TOPO_V4 = 2;
    public static final int TOPO_A = 3;
    /** `flags` 的位定义（与 `v4/export.py` 同值）。 */
    public static final int FLAG_B3 = 1;
    public static final int FLAG_TILE_ID = 1 << 1;
    public static final int FLAG_FILM = 1 << 2;
    public static final int FLAG_MEM_GATE = 1 << 3;
    /** A− 的 3 段嵌入（tile / evt / mem）与 `danger → policy` 的固定通道（0 参数）。 */
    static final int N_SEG = 3;
    static final float DANGER_COEF = 1.0f;
    static final float DANGER_EPS = 1e-6f;
    /** 死维/恒等维（`v4/model.py` 的 `CTX_DEAD_DIMS` / `CAND_DEAD_DIMS`；非持久 ⇒ 不在 net.bin 里）。 */
    static final int[] CTX_DEAD_DIMS = {17, 35, 37, 58, 59, 60, 61, 62, 63};
    // ⚠ E3（第一百一十三轮）：`cand[96..98]` 已三端接线 ⇒ 只掩 99..127（预留 29 列）。
    static final int[] CAND_DEAD_DIMS = java.util.stream.IntStream.range(99, 128).toArray();

    // ★ 第一百一十六轮（P4）：`evt` 的**恒 0 / 零信息**槽 —— 与 `v4/model.py` 的
    //   `EVT_DEAD_DIMS` 逐项相同。0/6/7 = 服务端从不进窗口的 3 个事件类型
    //   （draw/agari/ryuukyoku，实测全量恒 0）；95 = `seq_delta`（服务端从不发 ⇒ 恒 0.125 ⇒ 方差 0）。
    //   ⚠ 只在 `evtLN=1` 的包上生效（**掩码与 LN 同一个开关**：旧包连掩码都不乘 ⇒ 逐位不变）。
    static final int[] EVT_DEAD_DIMS = {0, 6, 7, 95};

    /**
     * **推理必需**的头 = 前向路径**实际读取**的头（⛔ 不是"训练过"的头）。
     *
     * <p>判据（唯一权威，三端同一把尺子）：把该头的参数**清零**后重跑同一次前向 ⇒ 动作 logits
     * **逐位不变** ⇔ 它不在推理路径上。**唯一规格 = `docs/TRAINER-CPP.md` §6.25**；三端同值
     * （Python `model.ONLINE_HEADS` / C++ `v4policy.hpp`）。
     *
     * <p>⚠ `belief_tenpai` 在线的**原因**：逐候选门控 `gate = 1 + tanh(policy_gate(sigmoid(bt)))`
     * （第五十五轮）乘在候选表示上 ⇒ 真的改 argmax。而 `heads.policy_gate.*` 是**可缺张量**
     * （缺 = 恒等门控）⇒ **老网**的在线集合退化成 `{policy}`：这是"**每个 checkpoint 的性质**"，
     * 不是代码常量。实测探针见 `tools.V4Probe --golden` 那行里的 `dValue/dBelief/dGate`。
     *
     * <p>⚠ 这里是**两个 arch 的并集**（Java/C++ 的常量与 arch 无关）：v4 只经 gate 读
     * `belief_tenpai`；**a− 的 `AHeads.forward` 还读 `danger`**（0 参数固定通道）
     * ⇒ 逐 arch 的准确集合 = Python `online_heads_declared(arch)`（第一百二十九轮修正）。
     */
    public static final java.util.List<String> ONLINE_HEADS =
            java.util.List.of("policy", "belief_tenpai", "danger");

    /** **训练与契约必需**的头：必须训练 + 过 parity + 进消融/加载器契约（⛔ 与"上线"无关）。 */
    public static final java.util.List<String> TRAIN_HEADS =
            java.util.List.of("policy", "value", "belief_tenpai", "danger");

    private static final float LN_EPS = 1e-5f;

    /** 头部 + arch 段（格式 3 的判据）：拓扑与开关**只认 arch 段**，⛔ 不靠张量名反推。 */
    private final int topoId;
    private final int archFlags;
    /** ★ S1（第一百一十六轮）：arch 段 `evtReadout=1` ⇒ 有逐候选事件读出（`fusion.evt_kv`/`evt_gamma`）。 */
    private final boolean evtReadout;
    /** ★ P4（第一百一十六轮）：arch 段 `evtLn=1` ⇒ `evt` 首层有 LayerNorm（`event.norm0.*`）。 */
    private final boolean evtLn;
    private final float valueRange;

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
    /** 三维张量（`Conv1d.weight` = `[out][in][k]`）。格式 3 新引入（A− 的卷积塔）。 */
    private final Map<String, float[][][]> mats3 = new HashMap<>();
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
    /** 全量路径最近一次的**整手 carry**（自检钩子；见 {@link #cachedHidden}）。 */
    private float[] lastFullHidden;
    /** padding 行的常量（零 token 的编码 / 注意力 `in_proj`）——多行共享同一份引用。 */
    private float[] padTokRow;
    private float[] padEncRow;
    private float[] padQkvRow;
    /** A− 的 sin/cos 相对位置编码（0 参数、非持久 ⇒ 不进 `net.bin`；按窗口形状缓存）。 */
    private float[][] peCache;
    /** `EVT_DEAD_DIMS` 对应的 0/1 掩码（P4；非持久 ⇒ 不进 `net.bin`）。 */
    private float[] evtMaskCache;

    // ---------------------------------------------------------------- 加载
    private V4Policy(int topoId, int archFlags, int evtReadout, int evtLn, float valueRange,
                     int dModel, int tileD, int nHeads, int valueBins, String fingerprint,
                     List<String> blocks, Set<String> missingBlocks,
                     Map<String, float[][]> mats, Map<String, float[]> vecs,
                     Map<String, float[][][]> mats3, Set<String> allNames) throws IOException {
        this.topoId = topoId;
        this.archFlags = archFlags;
        this.evtReadout = evtReadout != 0;
        this.evtLn = evtLn != 0;
        this.valueRange = valueRange;
        this.dModel = dModel;
        this.tileD = tileD;
        this.nHeads = nHeads;
        this.valueBins = valueBins;
        this.fingerprint = fingerprint;
        this.blocks = List.copyOf(blocks);
        this.missingBlocks = Set.copyOf(missingBlocks);
        this.mats.putAll(mats);
        this.vecs.putAll(vecs);
        this.mats3.putAll(mats3);
        this.allNames.addAll(allNames);
        bindAll();
        checkFlags();
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
        if (fmt == 1) {
            throw new IOException("权重格式版本 1 = v3 的定长 MLP（同一个魔数、没有块清单/没有 arch 段）"
                    + " —— 由 NeuralPolicy 加载；本加载器不解析、不猜");
        }
        if (fmt == FORMAT_RETIRED) {
            throw new IOException(RETIRED_FORMAT2);
        }
        if (fmt != FORMAT_VERSION) {
            throw new IOException("权重格式版本 " + fmt + " 未知（本服务端只认 " + FORMAT_VERSION
                    + "；1 = v3、2 = A− 之前的老包已退役）");
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
        int declaredParams = bb.getInt();                       // 校验位（格式 3 起不再"读了不用"）
        if (nBlocks < 0 || nTensors < 0 || nBlocks > 10_000 || nTensors > 10_000) {
            throw new IOException("块/张量数越界：nBlocks=" + nBlocks + " / nTensors=" + nTensors
                    + "（上限 10,000）");
        }

        // ---- arch 段（格式 3 的判据：拓扑/开关只认它）
        Arch arch = readArch(raw, featVer, obsVer, derivedVer, dModel, tileD, nHeads, valueBins);
        int off = HEADER_BYTES + arch.archLen;
        bb.position(off);

        Map<String, Integer> registry = V4Features.blockWidths();
        List<String> blocks = new ArrayList<>();
        Set<String> missing = new HashSet<>(registry.keySet());
        int lastIndex = -1;
        for (int i = 0; i < nBlocks; i++) {
            need(bb, 2, "第 " + i + " 个块 id 长度");
            int ln = bb.getShort() & 0xFFFF;
            need(bb, ln + 4, "块 " + i + "（id + 宽度）");
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
        Map<String, float[][][]> mats3 = new HashMap<>();
        Set<String> names = new HashSet<>();
        long actualParams = 0;
        for (int i = 0; i < nTensors; i++) {
            need(bb, 2, "第 " + i + " 个张量的名字长度");
            int ln = bb.getShort() & 0xFFFF;
            need(bb, ln, "第 " + i + " 个张量的名字");
            byte[] nb = new byte[ln];
            bb.get(nb);
            String name = new String(nb, StandardCharsets.UTF_8);
            need(bb, 1, "`" + name + "` 的维数");
            int nd = bb.get() & 0xFF;
            if (nd < 1 || nd > 8) {
                throw new IOException("张量 " + name + " 的维数 " + nd + " 不合理（只支持 1..8 维）");
            }
            int[] shape = new int[nd];
            long total = 1;
            for (int j = 0; j < nd; j++) {
                need(bb, 4, "`" + name + "` 的形状");
                shape[j] = bb.getInt();
                if (shape[j] < 0 || (long) shape[j] > 10_000_000L) {
                    throw new IOException("张量 " + name + " 的形状[" + j + "]=" + shape[j] + " 非法");
                }
                total *= shape[j];
                if (total > 40_000_000L) {
                    throw new IOException("张量 " + name + " 元素数 " + total + " 越界");
                }
            }
            need(bb, (int) (total * 4L), "`" + name + "` 的数据");
            float[] data = new float[(int) total];
            for (int j = 0; j < total; j++) {
                data[j] = bb.getFloat();
            }
            if (!names.add(name)) {
                throw new IOException("权重里有重复张量名：" + name);
            }
            if (nd == 1) {
                vecs.put(name, data);
            } else if (nd == 2) {
                float[][] m = new float[shape[0]][shape[1]];
                for (int r = 0; r < shape[0]; r++) {
                    System.arraycopy(data, r * shape[1], m[r], 0, shape[1]);
                }
                mats.put(name, m);
            } else if (nd == 3) {
                float[][][] c = new float[shape[0]][shape[1]][shape[2]];
                int p = 0;
                for (int a = 0; a < shape[0]; a++) {
                    for (int b2 = 0; b2 < shape[1]; b2++) {
                        System.arraycopy(data, p, c[a][b2], 0, shape[2]);
                        p += shape[2];
                    }
                }
                mats3.put(name, c);
            } else {
                throw new IOException("张量 " + name + " 是 " + nd + " 维（只支持 1/2/3 维）");
            }
            actualParams += total;
        }
        if (bb.hasRemaining()) {
            throw new IOException("权重文件尾部多了 " + bb.remaining() + " 字节（张量表与头部/arch 段不符）");
        }
        // ⑤ `params` 是**校验位**：必须等于张量表实算（⛔ "读了不用"的时代结束）
        if (actualParams != declaredParams) {
            throw new IOException("头部的 `params`=" + declaredParams + " 与张量表实算 " + actualParams
                    + " 不符（差 " + (actualParams - declaredParams) + "）");
        }
        // ① `valueBins` 三处（头 / arch / heads.value.weight 行数）—— 前两处已在 readArch 核过，
        //    第三处由 bindAll 的 `mat("heads.value.weight", vb, dm)` 钉住。
        return new V4Policy(arch.topoId, arch.flags, arch.evtReadout, arch.evtLn, arch.valueRange,
                dModel, tileD, nHeads,
                valueBins, fingerprint, blocks, missing, mats, vecs, mats3, names);
    }

    /** 格式 2 的**硬切**文案：要让用户一眼看出"这是老包、需要旧二进制"，而不是"文件坏了"。 */
    static final String RETIRED_FORMAT2 =
            "权重格式版本 2 已退役：format 3 起是 v4 新拓扑（64 B 头 + arch 段）。"
            + "**这是 A− 之前的老包**（format 2，没有 arch 段）—— 只有**旧二进制**能读它："
            + "冻结快照 `lab/mv1/frozen-format2`（`mahjong-server.jar` sha 3E7D0ADC…、"
            + "`trainer.exe` F6AAA307…）。⛔ 本加载器不会自动升级 / 不补 0 / 不猜 arch；"
            + "要转格式请用冻结快照里的旧工具重新导出，或按段 3 的新端重训。";

    /** `need`：带**边界检查**的读游标（⑥ 截断/越界一律 IOException，⛔ 不让 ByteBuffer 裸抛）。 */
    private static void need(ByteBuffer bb, int n, String what) throws IOException {
        if (n < 0 || bb.remaining() < n) {
            throw new IOException("权重文件被截断/越界：读 " + what + " 需要 " + n + " B（偏移 "
                    + bb.position() + "），文件只有 " + bb.limit() + " B");
        }
    }

    /** arch 段解析结果（字段名与 `v4/export.py` 的 `_ARCH_FIELDS` 一一对应）。 */
    static final class Arch {
        int archLen;
        int archVersion;
        int topoId;
        int dTile;
        int dEvt;
        int dFuse;
        int dHead;
        int dCand;
        int headDim;
        int nTileConv;
        int nTileAttn;
        int nJointLayer;
        int actId;
        int normId;
        int segEmbMode;
        int memTokenMode;
        int flags;
        int valueBins;
        int nHeads;
        float valueRange;
        float lnEps;
        int[] tileKernel = new int[4];
        int nSeg;
        // ★ 第一百一十六轮：arch 段**保留区（偏移 70/71）里新开的两个字段**（不升 format）
        int evtReadout;
        int evtLn;
    }

    /**
     * 解析 `[64, 64+archLen)` 的 arch 段 ⇒ 字段；不合格一律报错（§3 fail-loud 清单逐条落地）。
     *
     * <p>⚠ 块表偏移 = `64 + archLen`，而 `archLen` 写的是**整段** 2,048 B ⇒ 96 B 核心之后的
     * 1,952 B 保留尾在块表**之前**，不会漏读。两处必要出入（`archLen=2048`、`topoId=2` 注册）
     * 见 `ARCH-REDESIGN` §11.1，⛔ 不回改 §3 正文。
     */
    static Arch readArch(byte[] raw, int feat, int obs, int derived, int dm, int td, int nh, int vb)
            throws IOException {
        if (raw.length < HEADER_BYTES + 4) {
            throw new IOException("权重文件被截断：" + raw.length + " B，连 arch 段的 `archLen` 都读不到");
        }
        ByteBuffer ab = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        int alen = ab.getInt(HEADER_BYTES);
        if (alen < ARCH_CORE_BYTES || alen > ARCH_SEG_BYTES || alen % 4 != 0) {
            throw new IOException("archLen=" + alen + " 越界：必须在 [" + ARCH_CORE_BYTES + ", "
                    + ARCH_SEG_BYTES + "] 且为 4 的倍数（§3：arch 段 96 B 核心 + 1,952 B 保留尾）");
        }
        if (raw.length < HEADER_BYTES + alen) {
            throw new IOException("权重文件被截断：arch 段声明 " + alen + " B，文件只剩 "
                    + (raw.length - HEADER_BYTES) + " B");
        }
        ByteBuffer s = ByteBuffer.wrap(raw, HEADER_BYTES, alen).order(ByteOrder.LITTLE_ENDIAN)
                .slice().order(ByteOrder.LITTLE_ENDIAN);
        Arch a = new Arch();
        a.archLen = alen;
        a.archVersion = s.getShort(4) & 0xFFFF;
        a.topoId = s.getShort(6) & 0xFFFF;
        a.dTile = s.getShort(8) & 0xFFFF;
        a.dEvt = s.getShort(10) & 0xFFFF;
        a.dFuse = s.getShort(12) & 0xFFFF;
        a.dHead = s.getShort(14) & 0xFFFF;
        a.dCand = s.getShort(16) & 0xFFFF;
        a.headDim = s.getShort(18) & 0xFFFF;
        a.nTileConv = s.get(20) & 0xFF;
        a.nTileAttn = s.get(21) & 0xFF;
        a.nJointLayer = s.get(22) & 0xFF;
        a.actId = s.get(23) & 0xFF;
        a.normId = s.get(24) & 0xFF;
        a.segEmbMode = s.get(25) & 0xFF;
        a.memTokenMode = s.get(26) & 0xFF;
        a.flags = s.get(27) & 0xFF;
        a.valueBins = s.getShort(28) & 0xFFFF;
        a.nHeads = s.getShort(30) & 0xFFFF;
        a.valueRange = s.getFloat(32);
        a.lnEps = s.getFloat(36);
        for (int i = 0; i < 4; i++) {
            a.tileKernel[i] = s.get(40 + i) & 0xFF;
        }
        a.nSeg = s.get(44) & 0xFF;
        int pad45 = s.get(45) & 0xFF;
        int pad46 = s.get(46) & 0xFF;
        int pad47 = s.get(47) & 0xFF;
        int sFeat = s.getInt(48);
        int sObs = s.getInt(52);
        int sDerived = s.getInt(56);
        int cTile = s.getShort(60) & 0xFFFF;
        int cEvt = s.getShort(62) & 0xFFFF;
        int kEvt = s.getShort(64) & 0xFFFF;
        int cCtx = s.getShort(66) & 0xFFFF;
        int cCand = s.getShort(68) & 0xFFFF;
        a.evtReadout = s.get(70) & 0xFF;
        a.evtLn = s.get(71) & 0xFF;

        if (a.archVersion != ARCH_VERSION) {
            throw new IOException("archVersion=" + a.archVersion + " 不支持（本实现只认 " + ARCH_VERSION + "）");
        }
        if (a.topoId != TOPO_V4 && a.topoId != TOPO_A) {
            throw new IOException("topoId=" + a.topoId + " 未注册（已注册：{" + TOPO_V4 + "=v4, "
                    + TOPO_A + "=a-}）");
        }
        boolean isA = a.topoId == TOPO_A;
        for (String[] en : new String[][]{{"actId", "" + a.actId}, {"normId", "" + a.normId},
                {"segEmbMode", "" + a.segEmbMode}, {"memTokenMode", "" + a.memTokenMode}}) {
            int v = Integer.parseInt(en[1]);
            if (v != 0 && v != 1) {
                throw new IOException("arch 段的 `" + en[0] + "`=" + v + " 未注册（已注册：[0, 1]）");
            }
        }
        // 头 ↔ arch 段：三版本号 + 两个宽度必须相等（§3「头与 arch 两处必须相等」）
        if (sFeat != feat) {
            throw new IOException("头与 arch 段的 `featVersion` 不一致：头 " + feat + " / arch " + sFeat
                    + "（文件自相矛盾）");
        }
        if (sObs != obs) {
            throw new IOException("头与 arch 段的 `obsVersion` 不一致：头 " + obs + " / arch " + sObs);
        }
        if (sDerived != derived) {
            throw new IOException("头与 arch 段的 `derivedVersion` 不一致：头 " + derived + " / arch "
                    + sDerived);
        }
        if (a.valueBins != vb) {
            throw new IOException("`valueBins` 三处不一致：头 " + vb + " / arch " + a.valueBins
                    + "（第三处是 `heads.value.weight` 的行数，bindAll 会再核）—— ① fail-loud");
        }
        if (a.nHeads != nh) {
            throw new IOException("头与 arch 段的 `nHeads` 不一致：头 " + nh + " / arch " + a.nHeads);
        }
        if (a.dFuse != dm || a.dTile != td) {
            throw new IOException("arch 段的 `dFuse/dTile` = " + a.dFuse + "/" + a.dTile
                    + " 与头部 `dModel/tileD` = " + dm + "/" + td + " 不一致");
        }
        if ((a.flags & ~(FLAG_B3 | FLAG_TILE_ID | FLAG_FILM | FLAG_MEM_GATE)) != 0) {
            throw new IOException(String.format(
                    "arch 段的 `flags`=%#04x 用了**保留位**（bit4–7 必须为 0）", a.flags));
        }
        if ((a.flags & FLAG_FILM) != 0) {
            throw new IOException("arch 段声明 hasFiLM=1：本服务端未实现 FiLM（A− 已删除 gate/write/film）");
        }
        // 其余"由拓扑决定"的字段：必须逐字段等于期望值（⛔ 文件说 GELU、代码建 ReLU）
        int nTileConv = isA ? 4 : 0;
        int nJoint = isA ? 1 : 0;
        int actId = isA ? 1 : 0;
        int normId = isA ? 1 : 0;
        int segEmb = isA ? 1 : 0;
        int memTok = isA ? 1 : 0;
        int[] kern = isA ? new int[]{5, 5, 3, 3} : new int[]{0, 0, 0, 0};
        int nSeg = isA ? N_SEG : 0;
        float vrange = isA ? 60.0f : 30.0f;
        List<String> wrong = new ArrayList<>();
        if (a.nTileConv != nTileConv) wrong.add("nTileConv=" + a.nTileConv + "（应 " + nTileConv + "）");
        if (a.nTileAttn != 1) wrong.add("nTileAttn=" + a.nTileAttn + "（应 1）");
        if (a.nJointLayer != nJoint) wrong.add("nJointLayer=" + a.nJointLayer + "（应 " + nJoint + "）");
        if (a.actId != actId) wrong.add("actId=" + a.actId + "（应 " + actId + "）");
        if (a.normId != normId) wrong.add("normId=" + a.normId + "（应 " + normId + "）");
        if (a.segEmbMode != segEmb) wrong.add("segEmbMode=" + a.segEmbMode + "（应 " + segEmb + "）");
        if (a.memTokenMode != memTok) wrong.add("memTokenMode=" + a.memTokenMode + "（应 " + memTok + "）");
        for (int i = 0; i < 4; i++) {
            if (a.tileKernel[i] != kern[i]) wrong.add("tileKernel" + i + "=" + a.tileKernel[i]
                    + "（应 " + kern[i] + "）");
        }
        if (a.nSeg != nSeg) wrong.add("nSeg=" + a.nSeg + "（应 " + nSeg + "）");
        if (cTile != V4Features.C_TILE) wrong.add("cTile=" + cTile + "（应 " + V4Features.C_TILE + "）");
        if (cEvt != V4Features.C_EVT) wrong.add("cEvt=" + cEvt + "（应 " + V4Features.C_EVT + "）");
        if (kEvt != V4Features.K_EVT) wrong.add("kEvt=" + kEvt + "（应 " + V4Features.K_EVT + "）");
        if (cCtx != V4Features.C_CTX) wrong.add("cCtx=" + cCtx + "（应 " + V4Features.C_CTX + "）");
        if (cCand != V4Features.C_CAND) wrong.add("cCand=" + cCand + "（应 " + V4Features.C_CAND + "）");
        if (a.headDim != td / nh) wrong.add("headDim=" + a.headDim + "（应 " + (td / nh) + "）");
        if (a.dEvt != dm) wrong.add("dEvt=" + a.dEvt + "（应 " + dm + "）");
        if (a.dHead != dm) wrong.add("dHead=" + a.dHead + "（应 " + dm + "）");
        if (a.dCand != V4Features.C_CAND) wrong.add("dCand=" + a.dCand + "（应 " + V4Features.C_CAND + "）");
        if (!wrong.isEmpty()) {
            throw new IOException("arch 段与 `" + (isA ? "a-" : "v4") + "` 拓扑不符（" + wrong.size()
                    + " 条）：" + String.join("；", wrong));
        }
        if (Float.floatToIntBits(a.valueRange) != Float.floatToIntBits(vrange)) {
            throw new IOException("`valueRange`=" + a.valueRange + " 与 arch `"
                    + (isA ? "a-" : "v4") + "` 的声明 " + vrange + " 不符（① fail-loud）");
        }
        // 保留：核心 72..95（24 B）+ 2 个 pad + 保留尾 96..archLen 全 0
        // ⚠ 第一百一十六轮：70/71 已是 `evtReadout`/`evtLn` 两个具名字段（不再是保留区）。
        if (pad45 != 0 || pad46 != 0 || pad47 != 0) {
            throw new IOException("arch 段的 pad 字节非 0（" + pad45 + "/" + pad46 + "/" + pad47 + "）");
        }
        if (a.evtReadout > 1 || a.evtLn > 1) {
            throw new IOException("arch 段的 `evtReadout`/`evtLn` = " + a.evtReadout + "/" + a.evtLn
                    + " 未注册（只认 0 / 1）");
        }
        if (!isA && (a.evtReadout != 0 || a.evtLn != 0)) {
            throw new IOException("arch 段的 `evtReadout`/`evtLn` = " + a.evtReadout + "/" + a.evtLn
                    + "：v4 的图里没有这两条路径（只有 a- 有）");
        }
        for (int i = 72; i < ARCH_CORE_BYTES; i++) {
            if (s.get(i) != 0) {
                throw new IOException("arch 段的保留区（偏移 72–95，24 B）非 0（§3：archVersion=1 必须全 0）");
            }
        }
        for (int i = ARCH_CORE_BYTES; i < alen; i++) {
            if (s.get(i) != 0) {
                throw new IOException("arch 段的保留尾（偏移 96–" + (alen - 1) + "）非 0 —— 写端必须全 0");
            }
        }
        return a;
    }

    /**
     * `flags` ↔ 张量存在的**双向**校验（§3 fail-loud ②③）：
     * flag 说"有" ⇒ 必须存在；flag 说"没有" ⇒ 必须不存在（⛔ 不补 0：那等于跑一个没声明的分支）。
     */
    private void checkFlags() throws IOException {
        checkFlag("hasB3", FLAG_B3, new String[]{"fusion.u_norm.weight", "fusion.u_norm.bias",
                "fusion.u_res"}, "B3（`fusion.u_norm.*`/`fusion.u_res`）");
        checkFlag("hasTileId", FLAG_TILE_ID, new String[]{"tile.tile_id"},
                "#0 身份嵌入（`tile.tile_id`）");
        checkFlag("hasMemGate", FLAG_MEM_GATE, new String[]{"fusion.mem.weight", "fusion.mem.bias"},
                "长程记忆门（`fusion.mem.*`）");
        // ★ 第一百一十六轮：两个**新字段**（不是 flags 的位）走同一把双向尺子。
        checkFlag2("evtReadout", evtReadout,
                new String[]{"fusion.evt_kv.weight", "fusion.evt_gamma"},
                "S1 逐候选事件读出（`fusion.evt_kv.*`/`fusion.evt_gamma`）");
        checkFlag2("evtLN", evtLn, new String[]{"event.norm0.weight", "event.norm0.bias"},
                "evt 首层 LN（`event.norm0.*`）");
        if (topoId == TOPO_A) {
            // `a-` 的 B3 / `policy_gate` 是 T1 参数账的一部分：⛔ 缺 ⇒ 必须报错（补 0 = 静默换语义）
            for (String n : new String[]{"fusion.u_norm.weight", "fusion.u_norm.bias", "fusion.u_res",
                    "heads.policy_gate.weight", "heads.policy_gate.bias", "fusion.seg"}) {
                if (!allNames.contains(n)) {
                    throw new IOException("`a-` 的权重缺**拓扑必需**张量：" + n
                            + "（⛔ 不补 0 / 不猜 —— a- 必须是全套）");
                }
            }
        }
    }

    private void checkFlag(String label, int bit, String[] keys, String human) throws IOException {
        boolean claimed = (archFlags & bit) != 0;
        for (String k : keys) {
            boolean has = allNames.contains(k);
            if (claimed && !has) {
                throw new IOException("arch 段声明 " + label + "=1（" + human + "），但包里**缺** `" + k
                        + "`（⛔ 不补 0：那等于让前向跑一个 arch 没声明的分支）");
            }
            if (!claimed && has) {
                throw new IOException("arch 段声明 " + label + "=0（无" + human + "），但包里**有** `"
                        + k + "` —— 文件与水印（flags）不符");
            }
        }
    }

    /** 与 {@link #checkFlag} 同一把尺子，只是"claimed"来自 arch 段的**具名字段**而不是 `flags` 的位。 */
    private void checkFlag2(String label, boolean claimed, String[] keys, String human)
            throws IOException {
        for (String k : keys) {
            boolean has = allNames.contains(k);
            if (claimed && !has) {
                throw new IOException("arch 段声明 `" + label + "`=1（" + human + "），但包里**缺** `"
                        + k + "`（⛔ 不补 0：那等于让前向跑一个 arch 没声明的分支）");
            }
            if (!claimed && has) {
                throw new IOException("arch 段声明 `" + label + "`=0（无" + human + "），但包里**有** `"
                        + k + "` —— 文件与水印不符");
            }
        }
    }

    // ---------------------------------------------------------------- 取张量
    /**
     * `heads.policy.weight` 的**兼容绑定**：接受 `[1, dm]`（第五十五轮起的正常宽度）或
     * `[1, dm+3]`（第五十轮那版"拼接 sigmoid(belief_tenpai)"）。
     *
     * <p>对 `dm+3` 做**截断**（丢掉后 3 列）不是权宜近似：那 3 列乘的是**逐行常数**
     * （同一行的所有候选共用同一组 `sigmoid(bt)`）⇒ 对 argmax / softmax 无贡献
     * （红证：行内位移极差 4.8e-07~7.6e-06、argmax 完全一致、softmax 差 8.3e-07；见 NOTES §6.5）。
     * 于是三种网（第一季旧网 / 第二季拼接网 / 新门控网）共用同一条前向代码。
     */
    private void matPolicyCompat(int dm) throws IOException {
        float[][] w = mats.get("heads.policy.weight");
        if (w == null) {
            throw new IOException("权重缺张量：heads.policy.weight");
        }
        int cols = w.length > 0 ? w[0].length : 0;
        if (cols == dm) {
            used.add("heads.policy.weight");
            return;
        }
        if (cols != dm + 3) {
            throw new IOException("张量 heads.policy.weight 形状 [" + w.length + "," + cols
                    + "] != [" + 1 + "," + dm + "]，也不是第五十轮那版 [" + 1 + "," + (dm + 3) + "]");
        }
        float[][] cut = new float[w.length][dm];
        for (int i = 0; i < w.length; i++) {
            System.arraycopy(w[i], 0, cut[i], 0, dm);      // 丢掉后 3 列（逐行常数，等价）
        }
        mats.put("heads.policy.weight", cut);
        used.add("heads.policy.weight");
    }

    /**
     * 逐候选门控张量（第五十五轮）：`heads.policy_gate.weight [dm,3]` + `bias [dm]`。
     *
     * <p>**可缺**：旧网 / 第二季的网里没有它 ⇒ 补 0 ⇒ `g[j] = 1 + tanh(0) = 1` ⇒ 恒等
     * （不给旧权重强加新行为）。有它时把名字记进 `used`，否则"有张量没人读"的校验会红。
     */
    private void bindPolicyGate(int dm) {
        float[][] gw = mats.get("heads.policy_gate.weight");
        if (gw != null) {
            used.add("heads.policy_gate.weight");
            if (gw.length != dm || (dm > 0 && gw[0].length != 3)) {
                throw new IllegalStateException("张量 heads.policy_gate.weight 形状 [" + gw.length + ","
                        + (gw.length > 0 ? gw[0].length : 0) + "] != [" + dm + ",3]");
            }
        }
        // ⚠ **必须在这里（绑定期）标记**，不能等前向：加载期就有一条"有张量没人读"的校验，
        //   前向里再 `used.add(...)` 太晚 —— 症状是「权重里 1 个张量没有任何参数读取它：
        //   [heads.policy_gate.bias]」，看起来像多余张量，其实是标记时机不对（实测踩过）。
        float[] gb = vecs.get("heads.policy_gate.bias");
        if (gb != null) {
            used.add("heads.policy_gate.bias");
            if (gb.length != dm) {
                throw new IllegalStateException("张量 heads.policy_gate.bias 长度 " + gb.length
                        + " != " + dm);
            }
        }
    }

    /**
     * 门控偏置（`heads.policy_gate.bias`，长度 `dm`）：**可缺** ⇒ 全 0（与缺权重一起 = 恒等门控）。
     *
     * <p>⚠ 一维张量存在 `vecs` 里，**不在** `mats` —— 拿 `mats.get(...)` 去查它永远是 null。
     */
    private float[] gateBias(int dm) {
        float[] v = vecs.get("heads.policy_gate.bias");
        if (v != null) {
            used.add("heads.policy_gate.bias");
            return v;
        }
        return new float[dm];
    }

    /**
     * 长程记忆门控张量（W1，第六十轮）：`fusion.mem.weight [dm,dm]` + `bias [dm]`。
     *
     * <p>**可缺**：v1.14.1 及以前训练出来的网里没有它 ⇒ 补 0 ⇒ `g_mem[j] = 1 + tanh(0) = 1`
     * ⇒ 恒等（不给旧权重强加新行为）。有它时把名字记进 `used` —— ⚠ **必须在绑定期标记**，
     * 理由同 {@link #bindPolicyGate}（加载期的"有张量没人读"校验不等前向）。
     */
    private void bindMemGate(int dm) {
        float[][] mw = mats.get("fusion.mem.weight");
        if (mw != null) {
            used.add("fusion.mem.weight");
            if (mw.length != dm || (dm > 0 && mw[0].length != dm)) {
                throw new IllegalStateException("张量 fusion.mem.weight 形状 [" + mw.length + ","
                        + (mw.length > 0 ? mw[0].length : 0) + "] != [" + dm + "," + dm + "]");
            }
        }
        float[] mb = vecs.get("fusion.mem.bias");
        if (mb != null) {
            used.add("fusion.mem.bias");
            if (mb.length != dm) {
                throw new IllegalStateException("张量 fusion.mem.bias 长度 " + mb.length + " != " + dm);
            }
        }
    }

    /** 长程记忆门控偏置（**可缺** ⇒ 全 0；一维张量在 `vecs` 里，`mats.get` 查不到）。 */
    private float[] memBias(int dm) {
        float[] v = vecs.get("fusion.mem.bias");
        if (v != null) {
            used.add("fusion.mem.bias");
            return v;
        }
        return new float[dm];
    }

    /**
     * **B3（第一百零四轮）**：`u` 的 `LayerNorm(192)` + 零初始化逐维残差门 —— 三组**可缺**张量。
     *
     * <p>`fusion.u_norm.{weight,bias}`（各 `[dm]`）与 `fusion.u_res`（`[dm]`）。前向是
     * `u_i ← u_i + tanh(u_res) ⊙ LN(u_i)`（逐候选）。
     *
     * <p>**可缺**：旧网没有它们 ⇒ `u_res` 当全 0 ⇒ `tanh(0)=0` ⇒ **跳过整段** ⇒ 与"没有 B3"
     * **逐位相同**（同 `fusion.mem` / `heads.policy_gate` 的兼容套路）。有它时把名字记进 `used`
     * —— ⚠ **必须在绑定期标记**，理由同 {@link #bindPolicyGate}（加载期的"有张量没人读"校验不等前向）。
     *
     * <p>⚠ 形状核对**不能**用 `mat()`/`vec()` 探测：那两个会污染 `err`，而且"维度不符"必须炸掉、
     * 不能被当成"没有这个张量"（同一理由见 C++ 侧 `fusion.mem` 的查表写法）。
     */
    private void bindUNorm(int dm) {
        String[] names = {"fusion.u_norm.weight", "fusion.u_norm.bias", "fusion.u_res"};
        for (String n : names) {
            float[] v = vecs.get(n);
            if (v == null) {
                continue;
            }
            if (v.length != dm) {
                throw new IllegalStateException("张量 " + n + " 长度 " + v.length + " != " + dm);
            }
            used.add(n);
        }
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

    /** 三维卷积核 `[cout][cin][k]`（格式 3 新增；形状在绑定期钉死）。 */
    private float[][][] conv(String name, int cout, int cin, int k) throws IOException {
        float[][][] w = mats3.get(name);
        if (w == null) {
            throw new IOException("权重缺张量：" + name);
        }
        if (w.length != cout || (cout > 0 && (w[0].length != cin
                || (cin > 0 && w[0][0].length != k)))) {
            throw new IOException("张量 " + name + " 形状 [" + w.length + ","
                    + (w.length > 0 ? w[0].length : 0) + ","
                    + (w.length > 0 && w[0].length > 0 ? w[0][0].length : 0)
                    + "] != [" + cout + "," + cin + "," + k + "]");
        }
        used.add(name);
        return w;
    }

    /**
     * 把每一层要用的张量都取一遍 —— **同时**完成"形状核对 + 无多余张量"两件事。
     *
     * <p>这就是 Java 侧不需要再抄一份**张量形状表**（当前 **78** 行 = 74 必读 + 2 `heads.policy_gate.*`
     * + 2 `fusion.mem.*` 两个**可缺**组，见 `NOTES.md` §10.4）的原因：前向要把每个张量都读一遍，
     * 读的时候顺手钉住 `[rows,cols]`，最后 {@link #used} 与文件里的名字集合比对 ⇒
     * 少张量、多张量、改名、形状不符**都在构造期**炸掉（不是等到对局里算出个奇怪的牌）。
     */
    private void bindAll() throws IOException {
        if (topoId == TOPO_A) {
            bindAllA();
        } else {
            bindAllV4();
        }
    }

    /** `tile.tile_id`（#0）：**可缺**（`--legacy-tile-id` 的网不建 ⇒ 缺 = `tile + 0` 恒等）。 */
    private void bindTileId() throws IOException {
        float[][] t = mats.get("tile.tile_id");
        if (t == null) {
            return;
        }
        if (t.length != V4Features.KIND_COUNT || (t.length > 0 && t[0].length != V4Features.C_TILE)) {
            throw new IOException("张量 tile.tile_id 形状 [" + t.length + ","
                    + (t.length > 0 ? t[0].length : 0) + "] != [" + V4Features.KIND_COUNT + ","
                    + V4Features.C_TILE + "]");
        }
        used.add("tile.tile_id");
    }

    /**
     * ★ S1（第一百一十六轮）：`fusion.evt_kv.weight`（`[2d, d]`，**无 bias**）+ `fusion.evt_gamma`（`[d]`）。
     * 两组**可缺**（旧包没有 ⇒ `evtReadout=0` ⇒ 前向不跑这条读出）。
     * ⚠ K/V **没有 bias** 是刻意的：`v` 的 bias 在 `Σ_j a_ij = 1` 下是**逐行常数** ⇒ 对 softmax 空操作。
     */
    private void bindEvtReadout() throws IOException {
        float[][] w = mats.get("fusion.evt_kv.weight");
        if (w == null) {
            return;
        }
        int dm = dModel;
        if (w.length != 2 * dm) {
            throw new IOException("张量 fusion.evt_kv.weight 行数 " + w.length + " != " + (2 * dm));
        }
        for (int r = 0; r < w.length; r++) {
            if (w[r].length != dm) {
                throw new IOException("张量 fusion.evt_kv.weight 第 " + r + " 行列数 " + w[r].length
                        + " != " + dm);
            }
        }
        if (allNames.contains("fusion.evt_kv.bias")) {
            throw new IOException("fusion.evt_kv 不该有 bias（K/V 的 bias 是逐行常数 ⇒ 对 softmax 空操作；"
                    + "导出器与本前向不同版本）");
        }
        used.add("fusion.evt_kv.weight");
        vec("fusion.evt_gamma", dm);
    }

    /** ★ P4（第一百一十六轮）：`event.norm0.weight/bias`（`LayerNorm(C_EVT)`）—— **可缺**。 */
    private void bindEvtLn() throws IOException {
        if (!allNames.contains("event.norm0.weight")) {
            return;
        }
        vec("event.norm0.weight", V4Features.C_EVT);
        vec("event.norm0.bias", V4Features.C_EVT);
    }

    /** v4（`topoId=2`）的张量表：与格式 2 的 81 张量逐个相同（另加可缺的 `tile.tile_id`）。 */
    private void bindAllV4() throws IOException {
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
        matPolicyCompat(dm);
        bindPolicyGate(dm);
        bindMemGate(dm);
        bindUNorm(dm);
        bindTileId();
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

    /**
     * **A−（T1）**的张量表：93 个张量（含可缺的 `tile.tile_id`），逐条钉住 `[rows, cols]` /
     * `[out][in][k]`。形状与 `v4/export.py` 的 `_expected_shapes_a` **逐项相等**——这里不再抄一份
     * 表，读一遍就是核对一遍（多/少/改名/形状不符都在构造期炸）。
     */
    private void bindAllA() throws IOException {
        final int dm = dModel;
        final int td = tileD;
        final int vb = valueBins;
        final int ct = V4Features.C_TILE;
        final int ce = V4Features.C_EVT;

        // 牌种塔：34 轴卷积（48→td,k=5）+ 3 个 pre-LN 残差块 + 轴向注意力 + proj
        bindTileId();
        conv("tile.stem.weight", td, ct, 5);
        vec("tile.stem.bias", td);
        vec("tile.stem_norm.weight", td);
        vec("tile.stem_norm.bias", td);
        for (int b = 0; b < 3; b++) {
            int k = (b == 0) ? 5 : 3;
            vec("tile.res." + b + ".norm.weight", td);
            vec("tile.res." + b + ".norm.bias", td);
            conv("tile.res." + b + ".conv.weight", td, td, k);
            vec("tile.res." + b + ".conv.bias", td);
        }
        vec("tile.attn_norm.weight", td);
        vec("tile.attn_norm.bias", td);
        mat("tile.attn.in_proj_weight", 3 * td, td);
        vec("tile.attn.in_proj_bias", 3 * td);
        mat("tile.attn.out_proj.weight", td, td);
        vec("tile.attn.out_proj.bias", td);
        mat("tile.proj.weight", dm, td);
        vec("tile.proj.bias", dm);
        vec("tile.proj_norm.weight", dm);
        vec("tile.proj_norm.bias", dm);

        // 事件塔：Linear + GRUCell（增量 carry 契约不变）+ 2×Conv1d(k=3)+LN
        mat("event.enc.weight", dm, ce);
        vec("event.enc.bias", dm);
        mat("event.cell.weight_ih", 3 * dm, dm);
        mat("event.cell.weight_hh", 3 * dm, dm);
        vec("event.cell.bias_ih", 3 * dm);
        vec("event.cell.bias_hh", 3 * dm);
        conv("event.conv1.weight", dm, dm, 3);
        vec("event.conv1.bias", dm);
        vec("event.norm1.weight", dm);
        vec("event.norm1.bias", dm);
        conv("event.conv2.weight", dm, dm, 3);
        vec("event.conv2.bias", dm);
        vec("event.norm2.weight", dm);
        vec("event.norm2.bias", dm);

        // ctx / cand：首层 LN（C5）+ 两个 Linear（死维掩码是**非持久** buffer ⇒ 见 CTX/CAND_DEAD_DIMS）
        vec("ctx.norm.weight", V4Features.C_CTX);
        vec("ctx.norm.bias", V4Features.C_CTX);
        mat("ctx.fc1.weight", dm, V4Features.C_CTX);
        vec("ctx.fc1.bias", dm);
        mat("ctx.fc2.weight", dm, dm);
        vec("ctx.fc2.bias", dm);
        vec("cand.norm.weight", V4Features.C_CAND);
        vec("cand.norm.bias", V4Features.C_CAND);
        mat("cand.fc1.weight", dm, V4Features.C_CAND);
        vec("cand.fc1.bias", dm);
        mat("cand.fc2.weight", dm, dm);
        vec("cand.fc2.bias", dm);

        // 融合：3 段嵌入 + joint(95 token) + FFN + cross + out（LN(3Df) 在入口）+ B3
        mat("fusion.seg", N_SEG, dm);
        vec("fusion.joint_norm.weight", dm);
        vec("fusion.joint_norm.bias", dm);
        mat("fusion.joint.in_proj_weight", 3 * dm, dm);
        vec("fusion.joint.in_proj_bias", 3 * dm);
        mat("fusion.joint.out_proj.weight", dm, dm);
        vec("fusion.joint.out_proj.bias", dm);
        vec("fusion.ffn_norm.weight", dm);
        vec("fusion.ffn_norm.bias", dm);
        mat("fusion.ffn1.weight", dm, dm);
        vec("fusion.ffn1.bias", dm);
        mat("fusion.ffn2.weight", dm, dm);
        vec("fusion.ffn2.bias", dm);
        mat("fusion.cross.in_proj_weight", 3 * dm, dm);
        vec("fusion.cross.in_proj_bias", 3 * dm);
        mat("fusion.cross.out_proj.weight", dm, dm);
        vec("fusion.cross.out_proj.bias", dm);
        mat("fusion.out.weight", dm, 3 * dm);
        vec("fusion.out.bias", dm);
        vec("fusion.out_norm.weight", 3 * dm);
        vec("fusion.out_norm.bias", 3 * dm);
        bindUNorm(dm);                                    // B3：**a- 必需**（checkFlags 会再核）
        // ★ 第一百一十六轮：S1 读出 / P4 的 evt LN 都是**可缺组**（`evtReadout`/`evtLn` 决定）
        bindEvtReadout();
        bindEvtLn();

        // 头（26,054）：d=128 · value 81 bin（±60）· policy_gate 逐候选门控
        mat("heads.policy.weight", 1, dm);
        vec("heads.policy.bias", 1);
        mat("heads.policy_gate.weight", dm, 3);
        vec("heads.policy_gate.bias", dm);
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

    /**
     * 全部七个头（**生产全量路径**，判据④的基准）：事件塔的隐状态 = **整手 carry**
     * （从 0 起重放 `obs.events` 里的**全部**事件行），Transformer 仍只吃窗口。
     *
     * <p>⚠ W1 起 `h` 被融合消费（`fusion.mem` 门控）⇒ 全量与增量**必须**给出同一个 `h`
     * （{@link #forwardCached} 的 `s.h`）。所以这里不能再用"窗口 60 行的冷启动"当全量基准：
     * 那个量与缓存槽的 carry 是两个不同的数（见 {@link #cachedHidden} 的注释）。
     */
    public Map<String, float[]> forwardAll(Map<String, Object> obs) throws IOException {
        V4Features.Tensors t = V4Features.assemble(obs, missingBlocks);
        EvtTower tw = eventTowerRound(obs, t.evt);
        return forwardWith(t, tw.tokens, tw.h);
    }

    /**
     * 只有四张量、**没有事件表**的退化入口（性能基准与老调用）：窗口内冷启动。
     *
     * <p>它**不等于**生产路径（生产有整手 carry）—— 只用来量"前向本身"的耗时。
     */
    public Map<String, float[]> forwardAll(V4Features.Tensors t) throws IOException {
        EvtTower tw = eventTowerWindow(t.evt, null);
        return forwardWith(t, tw.tokens, tw.h);
    }

    /**
     * **golden 夹具入口**：`h0` = **窗口之前**的 carry（夹具显式给出，三端同值）。
     *
     * <p>这就是"三端同源"在 W1 之后的形式：给定同一个 `h0` 与同一个窗口，`h_evt` 必须逐位相同
     * （`docs/FEATURES-V4.md` §6 的夹具布局 format 2 里带着它）。
     */
    public Map<String, float[]> forwardAll(V4Features.Tensors t, float[] h0) throws IOException {
        EvtTower tw = eventTowerWindow(t.evt, h0);
        return forwardWith(t, tw.tokens, tw.h);
    }

    // ---------------------------------------------------------------- 增量事件缓存
    /**
     * 全部七个头，走**增量事件缓存**（`docs/FEATURES-V4.md` §5.3）。
     *
     * <p>与 {@link #forwardAll(Map)} 的关系：**只有事件塔的前半段不同**
     * （逐行编码 + 注意力 `in_proj` 复用 + 隐状态增量推进），从 Transformer 的注意力往后是
     * **同一份代码**、同一批浮点值 ⇒ 七个头应当**逐位相同**（自检按 `floatToIntBits` 比）。
     *
     * <p>三条纪律（设计文档 §5.3）：① 隐状态每小局清零（小局键变了就丢槽）；
     * ② 缓存必须能从当前 obs 完整重建（{@link V4Cache#overlapOk} 逐行校验，不信"应该是它"）；
     * ③ 自检/对拍永远拿无缓存路径当基准（{@link #setCacheEnabled}）。
     *
     * <p>⚠ W1 起 `h`（槽里的整手 carry）**被融合消费** ⇒ "增量 == 全量"这条判据第一次真正
     * 管到隐状态：槽里的 carry 与全量路径重放出来的必须**逐位**相同（自检直接比对两者）。
     */
    public Map<String, float[]> forwardCached(Map<String, Object> obs) throws IOException {
        V4Features.Tensors t = V4Features.assemble(obs, missingBlocks);
        return forwardFrom(obs, t);
    }

    /**
     * 张量已拼好之后的统一入口：缓存开 ⇒ 增量事件塔；关 / 消融 ⇒ 整手重放。
     *
     * <p>抽出来是为了让 {@link #chooseIndex} **只拼一次特征**（tie-break 要用 `cand`，
     * 而策略头要用 `effect`；同一份 `t` 两边共用，省一次 `assemble`）。
     */
    private Map<String, float[]> forwardFrom(Map<String, Object> obs, V4Features.Tensors t)
            throws IOException {
        if (!cacheEnabled || !missingBlocks.isEmpty()) {
            EvtTower tw = eventTowerRound(obs, t.evt);           // 关掉缓存 / 消融实验：走基准路径
            return forwardWith(t, tw.tokens, tw.h);
        }
        EvtTower tw = eventTowerCached(obs, t);
        return forwardWith(t, tw.tokens, tw.h);
    }

    /** 事件塔一次前向的两样产物：窗口的 Transformer 表示 + 隐状态（W1 起两者都被消费）。 */
    private static final class EvtTower {
        final float[][] tokens;
        final float[] h;

        EvtTower(float[][] tokens, float[] h) {
            this.tokens = tokens;
            this.h = h;
        }
    }

    /**
     * 增量路径的事件塔：只编码**新事件**的行，并复用上一决策的注意力 `in_proj`。
     *
     * <p>复用什么、为什么安全（`v4/cache.py` 的 `EventStream` 同语义）：
     * 一条事件的 token 行只依赖 `(事件, 自己座位)`，它的 MLP 输出与 `in_proj` 也只依赖这一行
     * ⇒ 窗口整体左移（新事件在尾部）时，**幸存行可以按引用平移**，只有新增的 `delta` 行要算。
     * 代价从"每决策 60 行"降到"每决策 delta 行"（实测 delta 的中位数是 1–2）。
     *
     * <p>⚠ 隐状态**只喂真实事件行**（前部零 padding 不喂）—— 与 {@link #eventTowerRound}
     * 和 `v4/cache.py` 的 `EventStream.recompute` 同一条口径：`GRUCell(0, h) ≠ h`，
     * 把 padding 也喂进去会让两条路径的 carry 差出 1e-2 量级（历史上实测过）。
     */
    private EvtTower eventTowerCached(Map<String, Object> obs, V4Features.Tensors t)
            throws IOException {
        final int k = cache.window();
        int seat = V4Features.i(obs.get("seat"), 0);
        List<Map<String, Object>> ev = V4Features.eventsOf(obs);
        int events = ev.size();
        String key = V4Features.handKey(obs);
        if (key == null) {
            return eventTowerRound(obs, t.evt);                   // 没有 round 信息 ⇒ 不缓存（但仍是整手 carry）
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
        // ⚠ A− 的事件塔**没有** Transformer 层（塔尾是 2×Conv1d）⇒ 没有可复用的 `in_proj`
        boolean reuseQkv = cacheLevel >= 3 && topoId != TOPO_A;
        if (!rebuild) {
            int shift = reuseRows ? delta : k;                     // 不复用行 ⇒ 视作整窗失效
            V4Cache.shiftRows(s.tok, shift);
            V4Cache.shiftRows(s.enc, shift);
            if (topoId != TOPO_A) {
                V4Cache.shiftRows(s.qkv, shift);
            }
        }
        // ① 前部 padding 行：共享常量行（零 token 的编码；值必须与基准路径逐位相同）
        for (int p = 0; p < pad; p++) {
            s.tok[p] = padTok();
            s.enc[p] = padEnc();
            if (topoId != TOPO_A) {
                s.qkv[p] = padQkv();
            }
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
        if (topoId != TOPO_A) {
            if (reuseQkv) {
                for (int p = Math.max(pad, first); p < k; p++) {
                    s.qkv[p] = eventQkvRow(s.enc[p]);
                }
            } else {
                for (int p = pad; p < k; p++) {
                    s.qkv[p] = eventQkvRow(s.enc[p]);
                }
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
        return new EvtTower(eventTokens(s.enc), s.h);
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

    /** 一条事件 token 行 → 事件编码器输出（v4：两层 MLP，**末尾没有 ReLU**；a-：Linear+GELU）。
     *
     * <p>★ P4（第一百一十六轮）：`evtLn=1` 时先过 **LayerNorm(C_EVT) + 死维掩码**（掩码前后各一次，
     * 与 `amlp` 同一条判据：只在 LN 前掩 ⇒ LN 的 `bias`/均值会把死维变成非 0）。
     * `evtLn=0`（旧包）⇒ 连掩码都不乘 ⇒ 与改动前**逐位**相同。
     */
    private float[] eventMlpRow(float[] tok) throws IOException {
        if (topoId == TOPO_A) {
            float[] x = tok;
            if (evtLn) {
                float[] m = evtMask();
                x = new float[V4Features.C_EVT];
                for (int i = 0; i < x.length; i++) {
                    x[i] = tok[i] * m[i];
                }
                layerNorm(x, vec("event.norm0.weight", V4Features.C_EVT),
                        vec("event.norm0.bias", V4Features.C_EVT));
                for (int i = 0; i < x.length; i++) {
                    x[i] *= m[i];
                }
            }
            float[] e = new float[dModel];
            linear(e, x, mat("event.enc.weight", dModel, V4Features.C_EVT),
                    vec("event.enc.bias", dModel));
            gelu(e);
            return e;
        }
        float[] e = new float[dModel];
        linear(e, tok, mat("event.enc.0.weight", dModel, V4Features.C_EVT),
                vec("event.enc.0.bias", dModel));
        relu(e);
        linear(e, e, mat("event.enc.2.weight", dModel, dModel),
                vec("event.enc.2.bias", dModel));
        return e;
    }

    /**
     * 窗口 token 的**塔尾**：v4 = 1 层 TransformerEncoder（pre-LN、ReLU FFN、2d）；
     * a- = `2×Conv1d(k=3)+LN+GELU`（**窗口内顺序**；卷积只作用在编码后的 token 上，
     * 不改变 `h`，所以增量 carry 契约一个字节都没动）。
     */
    private float[][] eventTokens(float[][] e) throws IOException {
        return topoId == TOPO_A ? eventConvStack(e) : transformerLayer(e, "event.tr.layers.0");
    }

    /** a- 的事件塔塔尾：`GELU(LN(e)) → conv1 → GELU(LN(·)) → conv2`（时间轴 = 行轴）。 */
    private float[][] eventConvStack(float[][] e) throws IOException {
        final int d = dModel;
        float[] n1g = vec("event.norm1.weight", d);
        float[] n1b = vec("event.norm1.bias", d);
        float[] n2g = vec("event.norm2.weight", d);
        float[] n2b = vec("event.norm2.bias", d);
        float[][] y = new float[e.length][d];
        for (int t = 0; t < e.length; t++) {
            System.arraycopy(e[t], 0, y[t], 0, d);
            layerNorm(y[t], n1g, n1b);
            gelu(y[t]);
        }
        float[][] c1 = conv1d(y, conv("event.conv1.weight", d, d, 3),
                vec("event.conv1.bias", d), 1);
        float[][] z = new float[c1.length][d];
        for (int t = 0; t < c1.length; t++) {
            System.arraycopy(c1[t], 0, z[t], 0, d);
            layerNorm(z[t], n2g, n2b);
            gelu(z[t]);
        }
        return conv1d(z, conv("event.conv2.weight", d, d, 3), vec("event.conv2.bias", d), 1);
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

    /**
     * 事件塔（**窗口内**推进）：从 `h0`（**窗口之前**的 carry；`null` ⇒ 全 0）推进窗口里的
     * **真实事件行**，`padding` 不喂。
     *
     * <p>三条路径都汇到这里或与它同口径（见 {@link #eventTowerRound}）：
     * ① 全量生产路径 = {@link #eventTowerRound}（整手 carry）；
     * ② 增量生产路径 = 缓存槽的 `s.h`（同一把递推，只是分成多次走）；
     * ③ **golden 夹具** = 这里（`h0` 由 Python 显式给出）—— 于是"给定同一个 `h0`，三端逐位相同"
     * 成为可判据（W1 的核心验收，见 `docs/VALVES-AND-FIXTURES.md` §3 W1）。
     */
    private EvtTower eventTowerWindow(float[][] evt, float[] h0) throws IOException {
        float[][] e = new float[evt.length][];
        for (int i = 0; i < e.length; i++) {
            e[i] = eventMlpRow(evt[i]);
        }
        float[][] gih = mat("event.cell.weight_ih", 3 * dModel, dModel);
        float[][] ghh = mat("event.cell.weight_hh", 3 * dModel, dModel);
        float[] gbi = vec("event.cell.bias_ih", 3 * dModel);
        float[] gbh = vec("event.cell.bias_hh", 3 * dModel);
        float[] h = (h0 == null) ? new float[dModel] : h0.clone();
        int real = realRows(evt);                                 // 前部零 padding 不喂 GRU
        for (int i = evt.length - real; i < evt.length; i++) {
            h = gruStep(e[i], h, gih, ghh, gbi, gbh);
        }
        lastFullHidden = h;
        return new EvtTower(eventTokens(e), h);
    }

    /**
     * 事件塔（**整手**推进）：从 0 起重放 `obs.events` 的**全部**真实事件行 ⇒ 得到"整手 carry"，
     * 与增量路径的 `s.h` **逐位同源**（同一把递推、同一个顺序、同一批行）。
     *
     * <p>为什么全量路径不能只喂窗口：窗口 `K=60` 覆盖不到一整手，那个"窗口冷启动"的 h
     * 与槽里的 carry 是**两个不同的量**（见 {@link #cachedHidden}）；一旦融合消费 h，
     * 两条路径就会分叉（三端不同值 + 判据④变红）。
     */
    private EvtTower eventTowerRound(Map<String, Object> obs, float[][] evt) throws IOException {
        final int k = cache.window();
        int seat = V4Features.i(obs.get("seat"), 0);
        List<Map<String, Object>> ev = V4Features.eventsOf(obs);
        int events = ev.size();
        float[][] e = new float[evt.length][];
        for (int i = 0; i < e.length; i++) {
            e[i] = eventMlpRow(evt[i]);
        }
        float[] h = replayCarry(ev, seat, Math.max(0, events - k), null);
        int real = Math.min(events, k);
        // ⚠ 张量**取一次**（`mat`/`vec` 每行都会走一遍 HashMap + 形状核对；放在循环里纯属浪费）
        float[][] gih = mat("event.cell.weight_ih", 3 * dModel, dModel);
        float[][] ghh = mat("event.cell.weight_hh", 3 * dModel, dModel);
        float[] gbi = vec("event.cell.bias_ih", 3 * dModel);
        float[] gbh = vec("event.cell.bias_hh", 3 * dModel);
        for (int i = evt.length - real; i < evt.length; i++) {
            h = gruStep(e[i], h, gih, ghh, gbi, gbh);
        }
        lastFullHidden = h;
        return new EvtTower(eventTokens(e), h);
    }

    /** 把 `ev[0, upto)` 这 `upto` 条**真实**事件行喂进 GRU（`h == null` ⇒ 从 0 起）。 */
    private float[] replayCarry(List<Map<String, Object>> ev, int seat, int upto, float[] h)
            throws IOException {
        float[] acc = (h == null) ? new float[dModel] : h;
        float[][] gih = mat("event.cell.weight_ih", 3 * dModel, dModel);
        float[][] ghh = mat("event.cell.weight_hh", 3 * dModel, dModel);
        float[] gbi = vec("event.cell.bias_ih", 3 * dModel);
        float[] gbh = vec("event.cell.bias_hh", 3 * dModel);
        for (int i = 0; i < upto; i++) {
            acc = gruStep(eventMlpRow(eventRowOf(ev.get(i), seat)), acc, gih, ghh, gbi, gbh);
        }
        return acc;
    }

    /**
     * 窗口里**真实事件**的行数（前部是零 padding ⇒ 全是 0 的行）。
     *
     * <p>为什么靠"整行是否全 0"判定：真实事件 token 的 `type` 那一段是 one-hot，
     * 恒有一位为 1 ⇒ 全 0 行只可能是 padding。判定后还要确认它们**确实在尾部前面的连续段**
     * （否则窗口形状就坏了），不一致当场报错而不是算出一个奇怪的 carry。
     */
    static int realRows(float[][] evt) throws IOException {
        int real = 0;
        for (float[] row : evt) {
            boolean nz = false;
            for (float x : row) {
                if (x != 0f) {
                    nz = true;
                    break;
                }
            }
            if (nz) {
                real++;
            } else if (real > 0) {
                throw new IOException("事件窗口的零行出现在真实事件之后（形状坏了）");
            }
        }
        return real;
    }

    /**
     * 七个头的主体：**事件塔的输出从参数进来**（`eTokens[K][dm]` + 隐状态 `hEvt[dm]`），
     * 其余全量共用。
     *
     * <p>这就是"增量 == 全量"的判据能成立的原因：两条路径从 Transformer 的注意力往后
     * 是**同一份代码、同一批浮点值**（`eTokens` 与 `hEvt` 逐位相同）⇒ 七个头逐位相同，
     * 不是"差一点点"。⚠ 别把这里的顺序改成"看起来等价"的样子（浮点加法不满足结合律）。
     */
    private Map<String, float[]> forwardWith(V4Features.Tensors t, float[][] eTokens, float[] hEvt)
            throws IOException {
        return topoId == TOPO_A ? forwardWithA(t, eTokens, hEvt) : forwardWithV4(t, eTokens, hEvt);
    }

    /** v4（`topoId=2`）的主干：tile MLP+post-LN MHA / q_tile+q_evt / gate·write·film / mem 门 / B3。 */
    private Map<String, float[]> forwardWithV4(V4Features.Tensors t, float[][] eTokens, float[] hEvt)
            throws IOException {
        int n = t.cand.length;
        int dm = dModel;

        // ---- 牌种塔（#0：`tile_id` 加在 `enc` 之前 —— 可缺 ⇒ `tile + 0` 逐位不变）
        float[][] ht = new float[V4Features.KIND_COUNT][tileD];
        float[][] tileIn = t.tile;
        float[][] tileId = mats.get("tile.tile_id");
        if (tileId != null) {
            tileIn = new float[t.tile.length][];
            for (int k = 0; k < t.tile.length; k++) {
                float[] row = Arrays.copyOf(t.tile[k], t.tile[k].length);
                for (int c = 0; c < row.length; c++) {
                    row[c] += tileId[k][c];
                }
                tileIn[k] = row;
            }
        }
        float[][] w1 = mat("tile.enc.0.weight", tileD, V4Features.C_TILE);
        float[] b1 = vec("tile.enc.0.bias", tileD);
        float[][] w2 = mat("tile.enc.2.weight", tileD, tileD);
        float[] b2 = vec("tile.enc.2.bias", tileD);
        for (int k = 0; k < ht.length; k++) {
            linear(ht[k], tileIn[k], w1, b1);
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
                pool[i] += ht[k][i];                             // 先求和再除（与 torch `.mean(1)` 同序）
            }
        }
        for (int i = 0; i < tileD; i++) {
            pool[i] /= ht.length;
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
            u[i] = mlp(cat3, "fusion.out.net", 3 * dm, dm, false);   // C1：**末端无 ReLU**（current 语义）
        }

        // ---- 长程记忆门控（W1，第六十轮）：`u_i ⊙ g_mem`（**逐候选**）
        //   `g_mem[j] = 1 + tanh(dot(memW[j], hEvt) + memB[j])`。
        //   ⛔ 为什么不能"把 hEvt 拼到候选后面 / 当逐行常数加进去"：那给同一行所有候选加的是
        //   **同一个量** ⇒ softmax/argmax 数学上不变、那几列梯度恒为 0（第五十轮就是这么栽的，
        //   红证见 NOTES §6.5 第四十八~五十四轮）。门控乘在 `u_i` 上而 `u_i` 各不相同 ⇒ 真能改判。
        //   ⚠ 门控在**所有头**之前（danger/effect/meanU 一起看到长程记忆），不只是 policy。
        //   旧网没有 `fusion.mem.*` ⇒ 全 0 ⇒ `g_mem ≡ 1` ⇒ 与"没有门控"**逐位相同**。
        float[][] memW = mats.get("fusion.mem.weight");
        if (memW != null) {
            float[] memB = memBias(dm);
            float[] memG = new float[dm];
            for (int j = 0; j < dm; j++) {
                double acc = 0.0;
                float[] row = memW[j];
                for (int q = 0; q < dm; q++) {
                    acc += (double) row[q] * hEvt[q];
                }
                memG[j] = (float) (1.0 + Math.tanh(acc + memB[j]));
            }
            for (int i = 0; i < n; i++) {
                for (int j = 0; j < dm; j++) {
                    u[i][j] *= memG[j];
                }
            }
        }

        // ---- ★ B3（第一百零四轮）：`u_i ← u_i + tanh(u_res) ⊙ LN(u_i)`（逐候选、逐维）
        //   为什么在**所有头之前**、在 mem 门**之后**：`u` 是 policy/danger/effect/meanU 的唯一入口，
        //   而实测级联每一跳都由 policy 领跑（第一百零三轮：`g_policy` 97.17 vs `g_aux` 0.615）。
        //   LN 把"尺度"从 `fusion.out → 三处 tanh 门 → 线性 policy` 这条链里解耦出来；
        //   残差门保留原 `u` ⇒ 不动 argmax 主体、只做逐维校正（不是整行缩放）。
        //   ⚠ 旧网缺这三组张量 ⇒ `u_res` 当 0 ⇒ `tanh(0)=0` ⇒ **跳过整段** ⇒ 逐位不变。
        float[] uNw = vecs.get("fusion.u_norm.weight");
        float[] uNb = vecs.get("fusion.u_norm.bias");
        float[] uRw = vecs.get("fusion.u_res");
        if (uRw != null) {
            if (uNw == null) {
                uNw = new float[dm];
                java.util.Arrays.fill(uNw, 1f);
            }
            if (uNb == null) {
                uNb = new float[dm];
            }
            float[] urT = new float[dm];
            for (int j = 0; j < dm; j++) {
                urT[j] = (float) Math.tanh(uRw[j]);
            }
            float[] r = new float[dm];
            for (int i = 0; i < n; i++) {
                System.arraycopy(u[i], 0, r, 0, dm);
                layerNorm(r, uNw, uNb);                  // 原地：r = LN(u_i)（有偏方差、eps=1e-5）
                for (int j = 0; j < dm; j++) {
                    u[i][j] += urT[j] * r[j];
                }
            }
        }

        // ---- 头
        // ⚠ **belief 用"逐候选门控"接进 policy**（2026-09-30 第五十五轮）：
        //   `g = 1 + tanh(W_g·sigmoid(bt) + b_g)`（[dm]），`policy_i = W·(u_i ⊙ g) + b`。
        //   ⛔ 第五十轮那版把 `sigmoid(bt)` **拼在每个候选后面**是**数学空操作**：同一行所有候选
        //   共用同一组 bt ⇒ 只给 logits 加逐行常数 ⇒ softmax/argmax 不变、梯度恒为 0
        //   （红证见 NOTES §6.5 第四十八~五十四轮）。门控是**逐候选**的（`u_i` 各不相同）⇒ 真能改判。
        //   `bt` 由 `meanU` 算出 ⇒ 必须先求 meanU、再求 bt、最后出 policy。
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
            danger[i] = new float[4];
            linear(danger[i], u[i], danW, danB);
            effect[i] = new float[3];
            linear(effect[i], u[i], effW, effB);
            for (int j = 0; j < dm; j++) {
                meanU[j] += u[i][j];                             // C2：先求和、再除 n（与 `state_pool` 同序）
            }
        }
        for (int j = 0; j < dm; j++) {
            meanU[j] /= Math.max(1, n);
        }
        float[] bt = new float[3];
        linear(bt, meanU, mat("heads.belief_tenpai.weight", 3, dm),
                vec("heads.belief_tenpai.bias", 3));
        for (int j = 0; j < 3; j++) {
            // ⚠ 这里用 sigmoid 后的**概率**（与 Python 侧 `torch.sigmoid(bt)` 一致）；
            //   而**输出的 `belief_tenpai` 仍然是 logits**（见下面的 `head(...)`）—— 别把两者搞混。
            bt[j] = (float) (1.0 / (1.0 + Math.exp(-bt[j])));
        }
        // ---- 逐候选门控（第五十五轮）：`g[j] = 1 + tanh(dot(gw[j], bt) + gb[j])`
        //   旧网没有这两个张量 ⇒ 全 0 ⇒ `g ≡ 1` ⇒ 与"没有门控"逐位相同。
        float[][] pgW = mats.get("heads.policy_gate.weight");        // [dm,3] 或 null
        float[] pgB = gateBias(dm);
        float[] g = new float[dm];
        for (int j = 0; j < dm; j++) {
            double acc = 0.0;
            if (pgW != null) {
                for (int k = 0; k < 3; k++) {
                    acc += (double) pgW[j][k] * bt[k];
                }
            }
            g[j] = (float) (1.0 + Math.tanh(acc + pgB[j]));
        }
        float[] gu = new float[dm];
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < dm; j++) {
                gu[j] = u[i][j] * g[j];                                // 逐候选：u_i 各不相同
            }
            policy[i] = polB + dot(polW[0], gu);
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
        // W1 起把**事件塔的隐状态**也交出来：它现在是融合的输入（`fusion.mem` 门控），
        // 于是"三端同值"可以直接比这个向量（比只看七个头灵敏得多 —— 见 golden 夹具格式 2）。
        out.put("h_evt", hEvt);
        return out;
    }

    /**
     * **A−（T1）主干**（`ARCH-REDESIGN` §2.2）：tile `Conv1d(48→160,k=5)+LN+GELU` → 1×k=5 + 2×k=3
     * pre-LN 残差 → 轴向 MHA(pre-LN) → `Linear(160→128)+LN`；evt 塔尾 2×Conv1d；融合 =
     * **95-token joint attention**（34 tile + 60 evt + `h_evt` 第 95 个 token，含 3 段嵌入与 sin/cos
     * 相对位置编码）→ FFN → `cross(cand→95)` → `out(LN(384)→128)` → B3；heads：`danger` 以
     * **0 参数固定通道**进 policy。
     *
     * <p>⚠ 三处"少一个就静默偏移"的地方：① `[34,48] → [48,34]` 的转置只发生在卷积入口，
     * 数据布局不变；② `tile.tile_id` 加在**转置之前**（#0）；③ pre-LN 的位置（残差主干不被压小）。
     */
    private Map<String, float[]> forwardWithA(V4Features.Tensors t, float[][] eTokens, float[] hEvt)
            throws IOException {
        final int n = t.cand.length;
        final int dm = dModel;
        final int td = tileD;
        final int ct = V4Features.C_TILE;
        final int kinds = V4Features.KIND_COUNT;

        // ---- 牌种塔：身份嵌入加在输入侧；`[34][48]`（时间轴在行）直接进 time-major 卷积
        float[][] tileId = mats.get("tile.tile_id");
        float[][] x = new float[kinds][ct];
        for (int k = 0; k < kinds; k++) {
            for (int c = 0; c < ct; c++) {
                float v = t.tile[k][c];
                if (tileId != null) {
                    v += tileId[k][c];
                }
                x[k][c] = v;
            }
        }
        float[][] tt = conv1d(x, conv("tile.stem.weight", td, ct, 5),
                vec("tile.stem.bias", td), 2);                        // [34][td]
        float[] sg = vec("tile.stem_norm.weight", td);
        float[] sb = vec("tile.stem_norm.bias", td);
        for (int k = 0; k < tt.length; k++) {
            layerNorm(tt[k], sg, sb);
            gelu(tt[k]);
        }
        for (int b = 0; b < 3; b++) {
            int kk = (b == 0) ? 5 : 3;
            float[] ng = vec("tile.res." + b + ".norm.weight", td);
            float[] nb = vec("tile.res." + b + ".norm.bias", td);
            float[][] y = new float[tt.length][td];
            for (int q = 0; q < tt.length; q++) {
                System.arraycopy(tt[q], 0, y[q], 0, td);
                layerNorm(y[q], ng, nb);
                gelu(y[q]);
            }
            float[][] c = conv1d(y, conv("tile.res." + b + ".conv.weight", td, td, kk),
                    vec("tile.res." + b + ".conv.bias", td), kk / 2);
            for (int q = 0; q < tt.length; q++) {
                for (int i = 0; i < td; i++) {
                    tt[q][i] += c[q][i];                              // 残差（pre-LN）
                }
            }
        }
        // 轴向注意力（34 个 token 之间的全局混合，pre-LN + 残差）
        float[] ang = vec("tile.attn_norm.weight", td);
        float[] anb = vec("tile.attn_norm.bias", td);
        float[][] tn = new float[tt.length][td];
        for (int q = 0; q < tt.length; q++) {
            System.arraycopy(tt[q], 0, tn[q], 0, td);
            layerNorm(tn[q], ang, anb);
        }
        float[][] aa = mha(tn, tn, td, "tile.attn");
        for (int q = 0; q < tt.length; q++) {
            for (int i = 0; i < td; i++) {
                tt[q][i] += aa[q][i];
            }
        }
        // proj：GELU → Linear(td→dm) → LN(dm)
        float[][] z = new float[tt.length][dm];
        float[][] pw = mat("tile.proj.weight", dm, td);
        float[] pb = vec("tile.proj.bias", dm);
        float[] ppg = vec("tile.proj_norm.weight", dm);
        float[] ppb = vec("tile.proj_norm.bias", dm);
        for (int q = 0; q < tt.length; q++) {
            float[] g = Arrays.copyOf(tt[q], td);
            gelu(g);
            linear(z[q], g, pw, pb);
            layerNorm(z[q], ppg, ppb);
        }
        float[] tilePool = new float[dm];
        for (int q = 0; q < z.length; q++) {
            for (int i = 0; i < dm; i++) {
                tilePool[i] += z[q][i];
            }
        }
        for (int i = 0; i < dm; i++) {
            tilePool[i] /= z.length;
        }

        // ---- ctx / cand：首层 LN（C5）+ 死维掩码（非持久 buffer ⇒ 这里按同一张表重建）
        float[] ctxEmb = amlp(t.ctx, "ctx", V4Features.C_CTX, dm, CTX_DEAD_DIMS);
        float[][] candEmb = new float[n][];
        for (int i = 0; i < n; i++) {
            candEmb[i] = amlp(t.cand[i], "cand", V4Features.C_CAND, dm, CAND_DEAD_DIMS);
        }

        // ---- 融合：95 token（34 tile + 60 evt + 1 mem），3 段嵌入 + evt 的 sin/cos 相对位置
        float[][] seg = mat("fusion.seg", N_SEG, dm);
        int kev = eTokens.length;
        int ntok = z.length + kev + 1;
        float[][] tokens = new float[ntok][dm];
        for (int k = 0; k < z.length; k++) {
            for (int i = 0; i < dm; i++) {
                tokens[k][i] = z[k][i] + seg[0][i];
            }
        }
        float[][] pe = sincosPe(kev, dm);
        for (int e = 0; e < kev; e++) {
            for (int i = 0; i < dm; i++) {
                tokens[z.length + e][i] = eTokens[e][i] + seg[1][i] + pe[e][i];
            }
        }
        for (int i = 0; i < dm; i++) {
            tokens[ntok - 1][i] = hEvt[i] + seg[2][i];                // h_evt = 第 95 个 token
        }
        float[][] jn = new float[ntok][dm];
        float[] jng = vec("fusion.joint_norm.weight", dm);
        float[] jnb = vec("fusion.joint_norm.bias", dm);
        for (int q = 0; q < ntok; q++) {
            System.arraycopy(tokens[q], 0, jn[q], 0, dm);
            layerNorm(jn[q], jng, jnb);
        }
        float[][] ja = mha(jn, jn, dm, "fusion.joint");
        for (int q = 0; q < ntok; q++) {
            for (int i = 0; i < dm; i++) {
                tokens[q][i] += ja[q][i];
            }
        }
        float[] fng = vec("fusion.ffn_norm.weight", dm);
        float[] fnb = vec("fusion.ffn_norm.bias", dm);
        float[][] f1W = mat("fusion.ffn1.weight", dm, dm);
        float[] f1B = vec("fusion.ffn1.bias", dm);
        float[][] f2W = mat("fusion.ffn2.weight", dm, dm);
        float[] f2B = vec("fusion.ffn2.bias", dm);
        float[][] fn = new float[ntok][dm];
        float[] hid = new float[dm];
        float[] res = new float[dm];
        for (int q = 0; q < ntok; q++) {
            System.arraycopy(tokens[q], 0, fn[q], 0, dm);
            layerNorm(fn[q], fng, fnb);
            linear(hid, fn[q], f1W, f1B);
            gelu(hid);
            linear(res, hid, f2W, f2B);
            for (int i = 0; i < dm; i++) {
                tokens[q][i] += res[i];
            }
        }
        // cross：q = 无参数 `F.layer_norm(cand)`（0 参数 pre-LN），kv = joint tokens
        // ⚠ 这里的 `cand` 是**候选编码器的输出**（`candEmb`，宽 = dm），不是原始 128 维 `t.cand`
        float[][] q = new float[n][dm];
        float[] ones = filled(dm, 1f);
        float[] zeros = new float[dm];
        for (int i = 0; i < n; i++) {
            System.arraycopy(candEmb[i], 0, q[i], 0, dm);
            layerNorm(q[i], ones, zeros);
        }
        float[][] cross = mha(q, tokens, dm, "fusion.cross");
        // out：LN(3*Df) 在**入口**（pre-LN），再 Linear(3Df→Df)
        float[][] outW = mat("fusion.out.weight", dm, 3 * dm);
        float[] outB = vec("fusion.out.bias", dm);
        float[] ong = vec("fusion.out_norm.weight", 3 * dm);
        float[] onb = vec("fusion.out_norm.bias", 3 * dm);
        float[] cat3 = new float[3 * dm];
        float[][] u = new float[n][dm];
        for (int i = 0; i < n; i++) {
            System.arraycopy(cross[i], 0, cat3, 0, dm);
            System.arraycopy(tilePool, 0, cat3, dm, dm);
            System.arraycopy(ctxEmb, 0, cat3, 2 * dm, dm);
            layerNorm(cat3, ong, onb);
            linear(u[i], cat3, outW, outB);
        }
        // B3：`u ← u + tanh(u_res) ⊙ LN(u)`（a- 的 B3 是**拓扑必需**，checkFlags 已核）
        float[] uNw = vecs.get("fusion.u_norm.weight");
        float[] uNb = vecs.get("fusion.u_norm.bias");
        float[] uRw = vecs.get("fusion.u_res");
        if (uRw != null) {
            float[] urT = new float[dm];
            for (int j = 0; j < dm; j++) {
                urT[j] = (float) Math.tanh(uRw[j]);
            }
            float[] r = new float[dm];
            for (int i = 0; i < n; i++) {
                System.arraycopy(u[i], 0, r, 0, dm);
                layerNorm(r, uNw, uNb);
                for (int j = 0; j < dm; j++) {
                    u[i][j] += urT[j] * r[j];
                }
            }
        }

        // ---- ★ S1（第一百一十六轮）：`h_evt` 的**逐候选**读出 ------------------------------
        // `KV = h_evt ⊕ e_tokens`（60+1 = 61 行）· `Q = q`（= `LN(cand_i)`，与 cross 用的是同一个）
        // `r_i = softmax(q_i·Kᵀ/√d)·V` · `u_i += tanh(γ) ⊙ r_i`
        // ⚠ **逐候选**是硬条件：`q_i` 逐候选不同 ⇒ `r_i` 不同 ⇒ 对 softmax 有真实影响
        //   （若 query 用逐行共享量，`u_i += 常数` 对 softmax 是空操作 —— NOTES 第五十/五十五轮）。
        if (evtReadout) {
            int nkv = kev + 1;
            float[][] kvW = mat("fusion.evt_kv.weight", 2 * dm, dm);
            float[] kvB = new float[2 * dm];                       // **无 bias**（见 bindEvtReadout）
            float[][] kv = new float[nkv][2 * dm];
            float[] src = new float[dm];
            for (int ti = 0; ti < nkv; ti++) {
                if (ti < kev) {
                    System.arraycopy(eTokens[ti], 0, src, 0, dm);
                } else {
                    System.arraycopy(hEvt, 0, src, 0, dm);          // 第 95 个 token = h_evt
                }
                linear(kv[ti], src, kvW, kvB);
            }
            float[] gamma = vec("fusion.evt_gamma", dm);
            float[] gT = new float[dm];
            for (int j = 0; j < dm; j++) {
                gT[j] = (float) Math.tanh(gamma[j]);
            }
            float inv = (float) (1.0 / Math.sqrt(dm));
            float[] logits = new float[nkv];
            float[] acc = new float[dm];
            for (int i = 0; i < n; i++) {
                float maxv = Float.NEGATIVE_INFINITY;
                for (int ti = 0; ti < nkv; ti++) {
                    double s = 0.0;
                    for (int j = 0; j < dm; j++) {
                        s += (double) q[i][j] * kv[ti][j];           // k = kv[ti][0..dm)
                    }
                    logits[ti] = (float) s * inv;
                    if (logits[ti] > maxv) {
                        maxv = logits[ti];
                    }
                }
                double sum = 0.0;
                for (int ti = 0; ti < nkv; ti++) {
                    logits[ti] = (float) Math.exp(logits[ti] - maxv);
                    sum += logits[ti];
                }
                Arrays.fill(acc, 0f);
                for (int ti = 0; ti < nkv; ti++) {
                    float a = (float) (logits[ti] / sum);
                    for (int j = 0; j < dm; j++) {
                        acc[j] += a * kv[ti][dm + j];                // v = kv[ti][dm..2dm)
                    }
                }
                for (int j = 0; j < dm; j++) {
                    u[i][j] += gT[j] * acc[j];
                }
            }
        }

        // ---- 头：state = 逐候选均值（无 padding ⇒ 分母 = n）；danger 以 0 参数通道进 policy
        float[][] danW = mat("heads.danger.weight", 4, dm);
        float[] danB = vec("heads.danger.bias", 4);
        float[][] effW = mat("heads.effect.weight", 3, dm);
        float[] effB = vec("heads.effect.bias", 3);
        float[][] danger = new float[n][4];
        float[][] effect = new float[n][3];
        float[] state = new float[dm];
        for (int i = 0; i < n; i++) {
            linear(danger[i], u[i], danW, danB);
            linear(effect[i], u[i], effW, effB);
            for (int j = 0; j < dm; j++) {
                state[j] += u[i][j];
            }
        }
        for (int j = 0; j < dm; j++) {
            state[j] /= Math.max(1, n);
        }
        float[] bt = new float[3];
        linear(bt, state, mat("heads.belief_tenpai.weight", 3, dm),
                vec("heads.belief_tenpai.bias", 3));
        float[][] pgW = mat("heads.policy_gate.weight", dm, 3);
        float[] pgB = vec("heads.policy_gate.bias", dm);
        float[] sbt = new float[3];
        for (int j = 0; j < 3; j++) {
            sbt[j] = (float) (1.0 / (1.0 + Math.exp(-bt[j])));
        }
        float[] gate = new float[dm];
        for (int j = 0; j < dm; j++) {
            double acc = 0.0;
            for (int k2 = 0; k2 < 3; k2++) {
                acc += (double) pgW[j][k2] * sbt[k2];
            }
            gate[j] = (float) (1.0 + Math.tanh(acc + pgB[j]));
        }
        float[][] polW = mat("heads.policy.weight", 1, dm);
        float polB = vec("heads.policy.bias", 1)[0];
        float[] policy = new float[n];
        float[] gu = new float[dm];
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < dm; j++) {
                gu[j] = u[i][j] * gate[j];
            }
            double sig = 0.0;
            for (int k2 = 0; k2 < 4; k2++) {
                sig += 1.0 / (1.0 + Math.exp(-danger[i][k2]));
            }
            float risk = (float) Math.log(sig / 4.0 + DANGER_EPS);
            policy[i] = polB + dot(polW[0], gu) + DANGER_COEF * risk;
        }
        Map<String, float[]> out = new LinkedHashMap<>();
        out.put("policy", policy);
        out.put("value", head(state, "value", valueBins));
        out.put("placement", head(state, "placement", 4));
        out.put("belief_hand", head(state, "belief_hand", 3 * kinds));
        out.put("belief_tenpai", bt);
        out.put("danger", flatten(danger, 4));
        out.put("effect", flatten(effect, 3));
        out.put("h_evt", hEvt);
        return out;
    }

    /** A− 的 `ctx`/`cand` 编码器：`fc2(GELU(fc1(LN(x·mask)·mask)))`（死维**前后各掩一次**）。 */
    private float[] amlp(float[] x, String prefix, int cin, int dm, int[] dead) throws IOException {
        float[] m = filled(cin, 1f);
        for (int i : dead) {
            m[i] = 0f;
        }
        float[] xn = new float[cin];
        for (int i = 0; i < cin; i++) {
            xn[i] = x[i] * m[i];
        }
        layerNorm(xn, vec(prefix + ".norm.weight", cin), vec(prefix + ".norm.bias", cin));
        for (int i = 0; i < cin; i++) {
            xn[i] *= m[i];                                     // LN 会把死维变成非 0 ⇒ 必须再掩一次
        }
        float[] h = new float[dm];
        linear(h, xn, mat(prefix + ".fc1.weight", dm, cin), vec(prefix + ".fc1.bias", dm));
        gelu(h);
        float[] out = new float[dm];
        linear(out, h, mat(prefix + ".fc2.weight", dm, dm), vec(prefix + ".fc2.bias", dm));
        return out;
    }

    /** 固定 sin/cos **相对窗口位置**编码（0 参数、非持久 ⇒ 不进 `net.bin`；按窗口形状缓存）。 */
    private float[][] sincosPe(int n, int d) {
        if (peCache != null && peCache.length == n && peCache[0].length == d) {
            return peCache;
        }
        float[][] pe = new float[n][d];
        for (int p = 0; p < n; p++) {
            for (int i = 0; i < d / 2; i++) {
                // ⚠ `i` 是**偶数列号**（0,2,4,…）⇒ 与 Python 的 `arange(0,d,2)` 同义，
                //   指数里的分子必须是 `2*i`，少了这个 2 就是一套完全不同的位置编码。
                float div = (float) Math.exp((float) (-Math.log(10000.0) * (2 * i)) / d);
                double ang = (double) p * div;
                pe[p][2 * i] = (float) Math.sin(ang);
                if (2 * i + 1 < d) {
                    pe[p][2 * i + 1] = (float) Math.cos(ang);
                }
            }
        }
        peCache = pe;
        return pe;
    }

    /** `EVT_DEAD_DIMS` 的 0/1 掩码（按包缓存；非持久 ⇒ 不进 `net.bin`）。 */
    private float[] evtMask() {
        if (evtMaskCache == null) {
            float[] m = filled(V4Features.C_EVT, 1f);
            for (int i : EVT_DEAD_DIMS) {
                m[i] = 0f;
            }
            evtMaskCache = m;
        }
        return evtMaskCache;
    }

    /** 时间轴在行上的转置：`[rows][cols] → [cols][rows]`。 */
    private static float[][] transpose(float[][] a) {
        float[][] o = new float[a[0].length][a.length];
        for (int r = 0; r < a.length; r++) {
            for (int c = 0; c < a[r].length; c++) {
                o[c][r] = a[r][c];
            }
        }
        return o;
    }

    /**
     * `Conv1d`（**跨相关**，与 torch 同向）：`out[t][oc] = b[oc] + Σ_ic Σ_k w[oc][ic][k]·x[t+k-pad][ic]`。
     *
     * <p>⛔ 不新增 SIMD kernel：参数化之后它就是"k 次移位 + 逐点乘加"，与三端逐位对拍的
     * 关键是**固定累加顺序**（先 `ic` 后 `k`，单累加器），不是向量化。
     */
    private static float[][] conv1d(float[][] x, float[][][] w, float[] b, int pad) {
        int len = x.length;
        int cin = x.length > 0 ? x[0].length : 0;
        int cout = w.length;
        int k = cout > 0 && w[0].length > 0 ? w[0][0].length : 0;
        float[][] out = new float[len][cout];
        for (int t = 0; t < len; t++) {
            for (int oc = 0; oc < cout; oc++) {
                float s = b == null ? 0f : b[oc];
                for (int ic = 0; ic < cin; ic++) {
                    float[] row = w[oc][ic];
                    for (int kk = 0; kk < k; kk++) {
                        int idx = t + kk - pad;
                        if (idx >= 0 && idx < len) {
                            s += row[kk] * x[idx][ic];
                        }
                    }
                }
                out[t][oc] = s;
            }
        }
        return out;
    }

    private static float[] filled(int n, float v) {
        float[] a = new float[n];
        if (v != 0f) {
            Arrays.fill(a, v);
        }
        return a;
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
     * 增量路径的隐状态（该座位当前小局的**整手 carry**）。
     *
     * <p>⚠ W1（第六十轮）起 `h` **被融合消费**（`fusion.mem` 逐候选门控）—— 于是它与
     * {@link #debugFullHidden()}（全量路径重放出来的**同一个量**）必须**逐位相同**，
     * 这从"一条警戒线"变成了**硬判据**（`SelfTest.v4CacheTests` 直接比两者）。
     * 第五十八轮那条"h 不同但七头逐位相同"的绊线已经**兑现**：这里给出的是同一个 carry。
     */
    public float[] cachedHidden(Map<String, Object> obs) throws IOException {
        forwardCached(obs);
        return cache.slot(V4Features.i(obs.get("seat"), 0)).h;
    }

    /** 全量路径最后一次前向里的**整手 carry**（自检对比用；见 {@link #cachedHidden}）。 */
    public float[] debugFullHidden() {
        return lastFullHidden;
    }

    /**
     * **窗口之前**的 carry（自检用）：重放 `obs.events` 的前 `max(0, n-K)` 条。
     *
     * <p>这是"夹具/离线给的那个 `h0`"在生产侧的对应物 —— 有了它，"整手重放"与
     * "前缀 carry + 窗口推进"两条路线可以在自检里**逐位对拍**（判据见
     * {code SelfTest.v4CacheTests}）。
     */
    public float[] debugPrefixCarry(Map<String, Object> obs) throws IOException {
        int seat = V4Features.i(obs.get("seat"), 0);
        List<Map<String, Object>> ev = V4Features.eventsOf(obs);
        return replayCarry(ev, seat, Math.max(0, ev.size() - cache.window()), null);
    }

    /** 缓存槽当前的窗口 token 行（自检用：必须与 `V4Features.eventMatrix` 逐位相同）。 */
    public float[][] debugCachedWindow(int seat) {
        return cache.slot(seat).tok;
    }

    /**
     * 带先验的 argmax + **A− 确定性 tie-break**（`ARCH-REDESIGN` §2.6）。
     *
     * <p>⚠ **归属 = caller**（与 `v4/policy.py:choose_index` 同一条规格）：`Heads` 只拿得到 logits，
     * 拿不到"同动作族 / 牌码 / 进张数"这些候选语义 ⇒ 规则活在这里。并列判据：
     * `top1 − logit < τ=1e-3` **且**与 argmax **同动作族**（`cand[0:9].argmax()`）⇒ 依次比较
     * ① `effect` 的**进张数**（第 2 维，越大越好）② **牌码**（`cand[9:46].argmax()`，越小越好）
     * ③ 最小合法下标。⛔ 不许浮点末位/随机破平。
     */
    @Override
    public int chooseIndex(Map<String, Object> json, List<String> keys, int priorIndex, float alpha) {
        try {
            V4Features.Tensors t = V4Features.assemble(json, missingBlocks);
            Map<String, float[]> o = forwardFrom(json, t);
            float[] out = o.get("policy");
            if (priorIndex >= 0 && priorIndex < out.length && alpha > 0f) {
                out[priorIndex] += alpha;
            }
            int pick = Logits.argmaxTieBreak(out, t.cand, o.get("effect"), Logits.TAU);
            assert pick >= 0 && pick < out.length : "tie-break 返回了越界下标：" + pick;
            return pick;
        } catch (IOException | RuntimeException e) {
            Log.warn("v4 网络前向失败：" + e.getMessage());
            return -1;                                          // 调用方兜底（Policies.fromAction / hybrid）
        }
    }

    @Override
    public int sampleIndex(Map<String, Object> json, List<String> keys, int priorIndex, float alpha,
                           float temp, Random rng) {
        try {
            V4Features.Tensors t = V4Features.assemble(json, missingBlocks);
            Map<String, float[]> o = forwardFrom(json, t);
            float[] out = o.get("policy");
            if (priorIndex >= 0 && priorIndex < out.length && alpha > 0f) {
                out[priorIndex] += alpha;
            }
            if (!(temp > 0f)) {
                return Logits.argmaxTieBreak(out, t.cand, o.get("effect"), Logits.TAU);
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
        for (float[][][] c : mats3.values()) {
            for (float[][] a : c) {
                for (float[] row : a) {
                    total += row.length;
                }
            }
        }
        for (float[] v : vecs.values()) {
            total += v.length;
        }
        return total;
    }

    /** `topoId`（2 = v4、3 = A−）：格式 3 的拓扑**只认 arch 段**。 */
    public int topoId() {
        return topoId;
    }

    /** arch 段的 `flags`（bit0 B3 / bit1 #0 / bit2 FiLM / bit3 mem 门）。 */
    public int archFlags() {
        return archFlags;
    }

    /** arch 段声明的值域（v4 = ±30、a- = ±60，千点）。 */
    public float valueRange() {
        return valueRange;
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
            return new V4Policy(topoId, archFlags, evtReadout ? 1 : 0, evtLn ? 1 : 0, valueRange,
                    dModel, tileD, nHeads, valueBins,
                    fingerprint, blocks, missingBlocks, new HashMap<>(mats), v2,
                    new HashMap<>(mats3), new HashSet<>(allNames));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    /** 该权重文件里有没有这个张量（头表判据用：`heads.policy_gate.*` 是**可缺张量**）。 */
    public boolean hasTensor(String name) {
        return allNames.contains(name);
    }

    /**
     * 自检用：返回一份"某个头的全部张量被清零"的副本 —— 头表判据的**探针**
     * （清零后动作 logits 逐位不变 ⇔ 该头不在推理路径上；规格见 {@link #ONLINE_HEADS}）。
     *
     * <p>⚠ 前缀带点（`heads.<head>.`）⇒ `policy` 不会误伤 `policy_gate`（后者是**策略路径**的一部分，
     * 清零它**必须**改变 logits）。
     */
    public V4Policy debugZeroHead(String head) {
        return debugZeroPrefix("heads." + head + ".");
    }

    /** 自检用：把**指定前缀**的张量全部清零（`debugZeroHead` 的泛化版；也用于 B3 红证）。 */
    public V4Policy debugZeroPrefix(String prefix) {
        Map<String, float[][]> m2 = new HashMap<>(mats);
        Map<String, float[]> v2 = new HashMap<>(vecs);
        Map<String, float[][][]> m3 = new HashMap<>(mats3);
        for (String k : allNames) {
            if (!k.startsWith(prefix)) {
                continue;
            }
            float[][] m = m2.get(k);
            if (m != null) {
                float[][] z = new float[m.length][];
                for (int r = 0; r < m.length; r++) {
                    z[r] = new float[m[r].length];
                }
                m2.put(k, z);
            }
            float[] v = v2.get(k);
            if (v != null) {
                v2.put(k, new float[v.length]);
            }
            float[][][] c = m3.get(k);
            if (c != null) {
                float[][][] z = new float[c.length][][];
                for (int r = 0; r < c.length; r++) {
                    z[r] = new float[c[r].length][];
                    for (int q = 0; q < c[r].length; q++) {
                        z[r][q] = new float[c[r][q].length];
                    }
                }
                m3.put(k, z);
            }
        }
        try {
            return new V4Policy(topoId, archFlags, evtReadout ? 1 : 0, evtLn ? 1 : 0, valueRange,
                    dModel, tileD, nHeads, valueBins,
                    fingerprint, blocks, missingBlocks, m2, v2, m3, new HashSet<>(allNames));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    public String describe() {
        return "V4Policy（" + paramCount() + " 参数，格式 " + FORMAT_VERSION + "，topoId=" + topoId
                + "（" + (topoId == TOPO_A ? "a-" : "v4") + "），flags="
                + String.format("%#04x", archFlags) + "，evtReadout=" + (evtReadout ? 1 : 0)
                + "，evtLN=" + (evtLn ? 1 : 0) + "，valueRange=" + valueRange
                + "，dims=" + dims()
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
            mean += v;                                  // 先求和、再除 n（与 torch `.mean` 同序）
        }
        mean /= n;
        float var = 0f;
        for (float v : x) {
            float d = v - mean;
            var += d * d;
        }
        var /= n;
        float inv = (float) (1.0 / Math.sqrt(var + LN_EPS));
        for (int i = 0; i < n; i++) {
            x[i] = (x[i] - mean) * inv * g[i] + b[i];
        }
    }

    /** `Mlp`（=`Linear + ReLU + Linear + ReLU`，注意**末尾那个 ReLU**）。 */
    private float[] mlp(float[] x, String prefix, int cin, int dm) throws IOException {
        return mlp(x, prefix, cin, dm, true);
    }

    /**
     * 参数化的 `Mlp`：`Linear → ReLU → Linear → [ReLU]`。
     *
     * <p>`finalRelu=false` 是 **C1**（`ARCH-REDESIGN` §2.4："去 `fusion.out` ReLU"）——格式 3 起的
     * v4 图按 **current 语义**跑（旧 c2 包是格式 2，已被硬切退役，只能由冻结快照读）。
     */
    private float[] mlp(float[] x, String prefix, int cin, int dm, boolean finalRelu)
            throws IOException {
        float[] h = new float[dm];
        linear(h, x, mat(prefix + ".0.weight", dm, cin), vec(prefix + ".0.bias", dm));
        relu(h);
        float[] out = new float[dm];
        linear(out, h, mat(prefix + ".2.weight", dm, dm), vec(prefix + ".2.bias", dm));
        if (finalRelu) {
            relu(out);
        }
        return out;
    }

    /** **erf 版 GELU**（`0.5·x·(1+erf(x/√2))`）；⛔ 不许换 tanh 近似（三端末位就不同）。 */
    static void gelu(float[] x) {
        for (int i = 0; i < x.length; i++) {
            double v = x[i];
            x[i] = (float) (0.5 * v * (1.0 + erf(v / 1.4142135623730951)));
        }
    }

    /**
     * 双精度 `erf`（Java 没有 `Math.erf`）。
     *
     * <p>算法：`|x| ≤ 3` 用 Maclaurin 级数（double 下最坏抵消 ~450×，仍留 1e-13）；
     * `|x| > 3` 用 `erfc` 的渐近展开（截到第 10 项，误差 ~1e-9）。⛔ 不用 A&S 7.1.26 那类
     * 1.5e-7 的近似 —— 它会顺着 4 层卷积放大到 1e-5 量级，正好吃掉 G1 的容差。
     */
    static double erf(double x) {
        double ax = Math.abs(x);
        if (ax <= 3.0) {
            double term = x;                                  // n = 0 项
            double sum = x;
            double x2 = x * x;
            for (int n = 1; n < 80; n++) {
                term *= -x2 / n;
                double add = term / (2 * n + 1);
                sum += add;
                if (Math.abs(add) < 1e-20 * Math.abs(sum) + 1e-300) {
                    break;
                }
            }
            return 1.1283791670955126 * sum;                  // 2/√π
        }
        // erfc(x) = exp(-x²)/(x√π) · Σ_{n≥0} (-1)^n (2n-1)!! / (2x²)^n
        // ⚠ 这是**渐近**级数：项先减后增 ⇒ 必须在**最小项**处截断（截到固定 30 项在 x≈3 会发散）。
        double u = 1.0 / (2.0 * ax * ax);
        double term = 1.0;
        double series = 1.0;
        double prev = Double.MAX_VALUE;
        for (int n = 1; n <= 60; n++) {
            term *= -(2.0 * n - 1.0) * u;
            double at = Math.abs(term);
            if (at > prev) {
                break;                                        // 到最小项：再往后是发散段
            }
            series += term;
            prev = at;
            if (at < 1e-18) {
                break;
            }
        }
        double erfc = Math.exp(-ax * ax) / (ax * 1.7724538509055159) * series;
        double e = 1.0 - erfc;
        return x < 0 ? -e : e;
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
