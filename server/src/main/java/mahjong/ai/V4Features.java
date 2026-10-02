package mahjong.ai;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import mahjong.core.Tiles;

/**
 * v4 观测 → 四张量：`tile[34,48] / evt[60,96] / ctx[64] / cand[n,128]`。
 *
 * <p>规范：{@code docs/FEATURES-V4.md} §3 / §4。**这里是权威实现**（服务端零第三方依赖、
 * 训练端 C++ 逐行镜像）——Python 侧 {@code v4/blocks.py} 是"离线从 sidecar 读"的那一份，
 * 两者必须逐元素一致（判据：`python/tests/golden/forward-v4.bin` + {@code SelfTest.v4ForwardTests}）。
 *
 * <h2>为什么 Java 必须能**自己算**派生量</h2>
 *
 * 离线训练时 `tile.danger/safety` 与 `cand` 的派生段来自 sidecar（引擎算、Python 只读），
 * 但**对局中推理没有 sidecar** —— 所以那些量必须由引擎在进程内实时算出来。
 * 它们本来就在 {@link ObsFeatures}（{@code perSeat} / {@code perCandidate}）里，
 * 这里只是把它们**摆进 v4 的通道布局**。⚠ 这意味着"训练输入 == 推理输入"不是设计口号，
 * 而是要靠对拍钉住的事实：2026-09-27 就抓到过离线侧 `cand[88:128]` 整块为 0
 * （`sidecar_dict` 没给 `cand`、`dataset` 又没查 `degraded`），而推理侧算的是真值 —— 两侧会差 40 列。
 *
 * <h2>块清单 = 消融开关（规范 §7）</h2>
 *
 * 每个块有 id 与宽度；{@link #assemble(Map, Set)} 的第二个参数按块把通道清零。
 * `net.bin` 格式 2 的块清单与当前注册表**逐块核对**（id + 宽度 + 相对顺序），
 * 缺项视为"训练时就没用这块"（填 0），宽度或顺序不符一律报错。
 */
public final class V4Features {

    /** 张量布局版本（与 Python `spec.FEATURE_VERSION_V4` 同号）。 */
    public static final int FEATURE_VERSION = 4;
    /** 需要的 obs 版本（v4 要事件流与逐张属性）。 */
    public static final int OBS_VERSION = 3;

    // 张量形状（规范 §3；与 Python `spec` 逐字对应）
    public static final int C_TILE = 48;
    public static final int C_EVT = 96;
    public static final int C_CTX = 64;
    public static final int C_CAND = 128;
    public static final int K_EVT = 60;

    /** 候选段：v3 的基础 88 + 派生 11（**v4 新 3 维目前恒 0**）+ 预留 29。 */
    public static final int CAND_BASE = 88;
    public static final int CAND_DERIVED = 11;
    public static final int CAND_RESERVED = C_CAND - CAND_BASE - CAND_DERIVED;

    public static final int N_PLAYERS = 4;
    public static final int KIND_COUNT = Tiles.KIND_COUNT;

    // ---------------------------------------------------------------- 布局表
    //
    // ⚠ 下面这几张表**就是契约**（`docs/FEATURES-V4.md` §4 的逐通道表）：`public` 是有意的 ——
    //   `SelfTest.v4ForwardTests` 要按它们核对宽度合计，C++ 侧与探针也照抄同一份顺序。
    /** `tile` 的通道分组（顺序即内存布局；宽度合计必须 == {@link #C_TILE}）。 */
    public static final String[] TILE_CHANNELS = {
        "own_count", "own_aka", "own_drawn",
        "river_all", "river_before_riichi", "river_after_riichi",
        "river_tedashi", "river_tsumogiri", "meld_count",
        "safety_genbutsu", "safety_suji", "danger_all", "danger_riichi",
        "visible", "unseen", "drawable", "dora_indicator",
        "is_dora", "is_aka", "is_yakuhai", "is_terminal", "is_honor",
        "suit_m", "suit_p", "suit_s", "reserved",
    };
    public static final int[] TILE_WIDTHS = {
        1, 1, 1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3,
    };

    /** `evt` 的字段布局（规范 §4.2），顺序即内存布局。 */
    public static final String[] EVT_FIELDS = {
        "type", "tile_kind", "tile_aka", "called_kind", "called_aka", "actor", "from",
        "meld_kind", "turn", "tsumogiri", "sideways", "rip_phase", "seq_delta",
    };
    public static final int[] EVT_WIDTHS = {8, 34, 1, 34, 1, 4, 4, 5, 1, 1, 1, 1, 1};

    /** 事件类型字符串（one-hot 顺序；`draw/agari/ryuukyoku` 服务端不发，但槽位留着）。 */
    public static final String[] EVT_TYPES = {
        "draw", "discard", "meld", "riichi", "kan", "dora_flip", "agari", "ryuukyoku",
    };
    /** 副露类型（与 `Tiles`/Python `MELD_KINDS` 同序）。 */
    public static final String[] MELD_KINDS = {"chi", "pon", "ankan", "kakan", "daiminkan"};

    /** `ctx` 的通道分组（规范 §4.4）。 */
    public static final String[] CTX_GROUPS = {"round", "points", "wall", "self", "seats", "ask", "reserved"};
    public static final int[] CTX_WIDTHS = {8, 12, 8, 10, 12, 8, 6};

    /** 块注册表（**顺序 = 规范里的块顺序**；`net.bin` 格式 2 的清单据此核对）。 */
    public static final String[] BLOCK_IDS = {
        "tile.own", "tile.per_opp", "tile.safety", "tile.danger", "tile.global", "evt.stream",
        "ctx.round", "ctx.points", "ctx.wall", "ctx.self", "ctx.seats", "ctx.ask", "ctx.reserved",
        "cand.base", "cand.derived", "cand.reserved",
    };
    public static final int[] BLOCK_WIDTHS = {
        3, 18, 6, 6, 15, C_EVT, 8, 12, 8, 10, 12, 8, 6, CAND_BASE, CAND_DERIVED, CAND_RESERVED,
    };

    /** 场风字母表（obs 里是字母，**不是数字**）。 */
    static final String[] WINDS = {"E", "S", "W", "N"};
    /** `win_note` 的两类（顺序与 Python `features.WIN_NOTES` 一致）。 */
    static final String[] WIN_NOTES = {"furiten", "no_yaku"};
    /** 赤五所在牌种（与 `Tiles.AKA_M/P/S` 同源）。 */
    static final int[] AKA_KINDS = {Tiles.AKA_M, Tiles.AKA_P, Tiles.AKA_S};

    static {
        // 布局自洽（少一列 = 有通道没人写；多一列 = 张量装不下）——构造期就炸，别等对拍
        checkSum("TILE", TILE_WIDTHS, C_TILE);
        checkSum("EVT", EVT_WIDTHS, C_EVT);
        checkSum("CTX", CTX_WIDTHS, C_CTX);
        int blockSum = 0;
        for (int w : BLOCK_WIDTHS) {
            blockSum += w;
        }
        if (blockSum != C_TILE + C_EVT + C_CTX + C_CAND) {
            throw new IllegalStateException("块宽度合计 " + blockSum + " != 四张量合计");
        }
    }

    private static void checkSum(String what, int[] widths, int want) {
        int s = 0;
        for (int w : widths) {
            s += w;
        }
        if (s != want) {
            throw new IllegalStateException(what + " 布局宽度合计 " + s + " != " + want);
        }
    }

    private V4Features() {
    }

    // ---------------------------------------------------------------- 结果
    /** 一次决策的四张量（`legal` 与 `cand` 的行序一一对应）。 */
    public static final class Tensors {
        public final float[][] tile;        // [34][48]
        public final float[][] evt;         // [60][96]
        public final float[] ctx;           // [64]
        public final float[][] cand;        // [n][128]
        public final List<String> legal;
        /** 被置 0 的块（消融）；训练侧另有 `degraded`，推理侧**没有降级**（全部实时算）。 */
        public final Set<String> ablated;

        Tensors(float[][] tile, float[][] evt, float[] ctx, float[][] cand,
                List<String> legal, Set<String> ablated) {
            this.tile = tile;
            this.evt = evt;
            this.ctx = ctx;
            this.cand = cand;
            this.legal = legal;
            this.ablated = ablated;
        }
    }

    // ---------------------------------------------------------------- 总装
    /** 按 obs 拼四张量（不做消融）。 */
    public static Tensors assemble(Map<String, Object> obs) {
        return assemble(obs, Set.of());
    }

    /**
     * 按 obs 拼四张量；{@code ablate} 里的块**整块置 0**（规范 §7）。
     *
     * @throws IllegalArgumentException obs 版本不够 / 未知块 id（**不静默降级**）
     */
    public static Tensors assemble(Map<String, Object> obs, Set<String> ablate) {
        int v = i(obs.get("v"), 2);
        if (v < OBS_VERSION) {
            throw new IllegalArgumentException("v4 特征需要 obs v" + OBS_VERSION
                    + "（拿到 v" + v + "）—— 老轨迹没有 events/riichi_turn，不能静默填 0");
        }
        for (String bid : ablate) {
            if (indexOfBlock(bid) < 0) {
                throw new IllegalArgumentException("未注册的块 id：" + bid);
            }
        }
        ObsFeatures.View view = ObsFeatures.ofObs(obs);
        ObsFeatures.PerSeat perSeat = ObsFeatures.perSeat(view);

        float[][] tile = tileMatrix(obs, view, perSeat);
        float[][] evt = eventMatrix(obs);
        float[] ctx = ctxVector(obs);
        float[][] cand = candMatrix(obs, view);

        for (String bid : ablate) {
            zeroBlock(bid, tile, evt, ctx, cand);
        }
        return new Tensors(tile, evt, ctx, cand, legalOf(obs), Set.copyOf(ablate));
    }

    private static List<String> legalOf(Map<String, Object> obs) {
        List<String> out = new ArrayList<>();
        Object raw = obs.get("legal");
        if (raw instanceof List<?> l) {
            for (Object o : l) {
                out.add(String.valueOf(o));
            }
        }
        return out;
    }

    static int indexOfBlock(String bid) {
        for (int i = 0; i < BLOCK_IDS.length; i++) {
            if (BLOCK_IDS[i].equals(bid)) {
                return i;
            }
        }
        return -1;
    }

    /** 块清单指纹（与 Python `spec.fingerprint()` **逐字节同算法**：`[[id,width],…]` 的 sha256 前 16 位十六进制）。 */
    public static String fingerprint() {
        List<String> ids = new ArrayList<>();
        List<Integer> widths = new ArrayList<>();
        for (int i = 0; i < BLOCK_IDS.length; i++) {
            ids.add(BLOCK_IDS[i]);
            widths.add(BLOCK_WIDTHS[i]);
        }
        return fingerprint(ids, widths);
    }

    /**
     * **任意一份块清单**的指纹（`net.bin` 格式 2 里存的就是它）。
     *
     * <p>为什么不能只算全量指纹：消融后的权重只带子集（规范 §7），加载时要能
     * "按文件里那份清单重算一遍"与文件里的 16 字节比对 —— 否则清单被手改过也看不出来。
     */
    public static String fingerprint(List<String> ids, List<Integer> widths) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < ids.size(); i++) {
            sb.append(i == 0 ? "[" : ",").append("[\"").append(ids.get(i)).append("\",")
              .append(widths.get(i)).append("]");
        }
        sb.append("]");
        try {
            byte[] h = java.security.MessageDigest.getInstance("SHA-256")
                    .digest(sb.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder();
            for (int i = 0; i < 8; i++) {
                hex.append(String.format("%02x", h[i]));
            }
            return hex.toString();
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException("JDK 没有 SHA-256", e);
        }
    }

    /** 块 id 列表 → 宽度列表（加载器核对文件里的清单用）。 */
    public static List<Integer> widthsOf(List<String> ids) {
        List<Integer> out = new ArrayList<>();
        Map<String, Integer> reg = blockWidths();
        for (String id : ids) {
            Integer w = reg.get(id);
            if (w == null) {
                throw new IllegalArgumentException("未注册的块 id：" + id);
            }
            out.add(w);
        }
        return out;
    }

    // ---------------------------------------------------------------- tile
    /** [34][48]，四家块**旋转到自己为下标 0**（0=自己 / 1=下家 / 2=対面 / 3=上家）。 */
    static float[][] tileMatrix(Map<String, Object> obs, ObsFeatures.View view,
                                ObsFeatures.PerSeat perSeat) {
        float[][] m = new float[KIND_COUNT][C_TILE];
        int seat = i(obs.get("seat"), 0);

        // ⚠ `hand_red` 在 obs JSON 里是**布尔数组**（"这个牌种有没有赤五"），不是计数 ——
        //   用 `ints()` 去读会全变 0（2026-09-27 对拍抓到：`tile[k][1]` 整列差 1.0，
        //   `V4Probe --golden` 直接把格子点名出来了）。
        int[] hand = view.hand;
        int[] handRed = boolFlags(obs.get("hand_red"), KIND_COUNT);
        int drawnKind = Tiles.parseKind(str(obs.get("drawn")));
        put(m, "own_count", col(hand, 4.0f));
        put(m, "own_aka", col(handRed, 1.0f));
        float[] ownDrawn = new float[KIND_COUNT];
        if (drawnKind >= 0) {
            ownDrawn[drawnKind] = 1f;
        }
        put(m, "own_drawn", ownDrawn);

        List<List<String>> discards = discardsOf(obs);
        List<Map<String, Object>> events = eventsOf(obs);
        for (int j = 1; j < N_PLAYERS; j++) {
            int abs = (seat + j) % N_PLAYERS;
            int ch = j - 1;
            putCol(m, "river_all", ch, col(countsOf(discards.get(abs)), 4f));
            List<String> before = new ArrayList<>();
            List<String> after = new ArrayList<>();
            List<String> tsumo = new ArrayList<>();
            List<String> tedashi = new ArrayList<>();
            for (Map<String, Object> e : events) {
                if (!"discard".equals(str(e.get("type"))) || i(e.get("actor"), -1) != abs) {
                    continue;
                }
                String code = str(e.get("tile"));
                if (bool(e.get("rip_phase"))) {
                    after.add(code);
                } else {
                    before.add(code);
                }
                if (bool(e.get("tsumogiri"))) {
                    tsumo.add(code);
                } else {
                    tedashi.add(code);
                }
            }
            putCol(m, "river_before_riichi", ch, col(countsOf(before), 4f));
            putCol(m, "river_after_riichi", ch, col(countsOf(after), 4f));
            putCol(m, "river_tedashi", ch, col(countsOf(tedashi), 4f));
            putCol(m, "river_tsumogiri", ch, col(countsOf(tsumo), 4f));
            putCol(m, "meld_count", ch, col(meldCounts(obs, abs), 4f));
            putCol(m, "safety_genbutsu", ch, bitmap(perSeat.genbutsu[ch]));
            putCol(m, "safety_suji", ch, bitmap(perSeat.suji[ch]));
            putCol(m, "danger_all", ch, scale(perSeat.danger[ch], 1f / 100f));
            putCol(m, "danger_riichi", ch, scale(perSeat.dangerRiichi[ch], 1f / 100f));
        }

        int[] visible = ObsFeatures.ints(obs.get("visible"), KIND_COUNT);
        float[] unseen = new float[KIND_COUNT];
        float[] drawable = new float[KIND_COUNT];
        for (int k = 0; k < KIND_COUNT; k++) {
            unseen[k] = Math.max(0f, 4f - visible[k]) / 4f;
            drawable[k] = Math.max(0f, 4f - visible[k] - hand[k]) / 4f;
        }
        boolean[] dora = doraKinds(listOf(obs.get("dora_indicators")));
        put(m, "visible", col(visible, 4f));
        put(m, "unseen", unseen);
        put(m, "drawable", drawable);
        put(m, "dora_indicator", col(countsOf(listOf(obs.get("dora_indicators"))), 4f));
        put(m, "is_dora", flag(k -> dora[k]));
        put(m, "is_aka", flag(k -> k == Tiles.AKA_M || k == Tiles.AKA_P || k == Tiles.AKA_S));
        put(m, "is_yakuhai", flag(k -> k >= 27));
        put(m, "is_terminal", flag(k -> Tiles.isTerminal(k)));
        put(m, "is_honor", flag(k -> Tiles.isHonor(k)));
        put(m, "suit_m", flag(k -> k < 9));
        put(m, "suit_p", flag(k -> k >= 9 && k < 18));
        put(m, "suit_s", flag(k -> k >= 18 && k < 27));
        return m;                                      // reserved 3 列保持 0
    }

    private interface Flag {
        boolean ok(int kind);
    }

    private static float[] flag(Flag f) {
        float[] out = new float[KIND_COUNT];
        for (int k = 0; k < KIND_COUNT; k++) {
            out[k] = f.ok(k) ? 1f : 0f;
        }
        return out;
    }

    private static float[] col(int[] counts, float div) {
        float[] out = new float[KIND_COUNT];
        for (int k = 0; k < KIND_COUNT; k++) {
            out[k] = counts[k] / div;
        }
        return out;
    }

    private static float[] scale(int[] values, float mul) {
        float[] out = new float[KIND_COUNT];
        for (int k = 0; k < Math.min(KIND_COUNT, values.length); k++) {
            out[k] = values[k] * mul;
        }
        return out;
    }

    private static float[] bitmap(byte[] packed) {
        float[] out = new float[KIND_COUNT];
        for (int k = 0; k < KIND_COUNT && k < packed.length * 8; k++) {
            out[k] = ((packed[k / 8] >> (k % 8)) & 1) != 0 ? 1f : 0f;   // 字节内 LSB 在前
        }
        return out;
    }

    private static void put(float[][] m, String channel, float[] values) {
        int off = 0;
        for (int i = 0; i < TILE_CHANNELS.length; i++) {
            if (TILE_CHANNELS[i].equals(channel)) {
                for (int k = 0; k < KIND_COUNT; k++) {
                    m[k][off] = values[k];
                }
                return;
            }
            off += TILE_WIDTHS[i];
        }
        throw new IllegalArgumentException("没有这个 tile 通道：" + channel);
    }

    private static void putCol(float[][] m, String channel, int col, float[] values) {
        int off = 0;
        for (int i = 0; i < TILE_CHANNELS.length; i++) {
            if (TILE_CHANNELS[i].equals(channel)) {
                for (int k = 0; k < KIND_COUNT; k++) {
                    m[k][off + col] = values[k];
                }
                return;
            }
            off += TILE_WIDTHS[i];
        }
        throw new IllegalArgumentException("没有这个 tile 通道：" + channel);
    }

    // ---------------------------------------------------------------- evt
    /** [60][96]：最近 K 条公开事件，**新的在尾部**（与增量缓存同序）。 */
    public static float[][] eventMatrix(Map<String, Object> obs) {
        float[][] out = new float[K_EVT][C_EVT];
        List<Map<String, Object>> events = eventsOf(obs);
        int seat = i(obs.get("seat"), 0);
        int m = Math.min(events.size(), K_EVT);
        for (int x = 0; x < m; x++) {
            eventRow(events.get(events.size() - m + x), seat, out[K_EVT - m + x]);
        }
        return out;
    }

    /**
     * **一条事件**的 token 行（`eventMatrix` 的逐行版本）。
     *
     * <p>为什么要单独一份：增量事件缓存（`V4Cache` / `docs/FEATURES-V4.md` §5.3）每决策只编码
     * **新增**的那几条事件 —— 逐行函数是"同一份实现、两种调用"的前提（另写一份必然漂移，
     * 而漂移的症状是"缓存看起来正常、结果差一点点"）。⚠ 行内容只依赖 `(事件, seat)`，
     * **不依赖窗口位置或其它事件** ⇒ 前缀可以安全复用（这是缓存成立的唯一前提）。
     *
     * <p>⚠ **本函数自带清零**（`FEATURES-V4.md` §5.3 的纪律④）：签名收的是"一个待填的行缓冲"，
     * 而调用方**会复用**同一个缓冲（缓存建槽时逐事件重放就是复用一支 `tok`）。
     * 不清零的后果是**静默累积**：第 i 行 = 前 i 条事件所有位或的结果 —— 而 `s.h` 原先没人消费，
     * 所以这个 bug 藏了很久（W1 把 `h_evt` 接进融合的当天，"增量 == 全量"与"陈旧退回"
     * 三条判据一起红，见 NOTES §6.5 第六十轮）。清零的成本是 96 次写，相对一次 matvec 可忽略。
     */
    static void eventRow(Map<String, Object> e, int seat, float[] row) {
        java.util.Arrays.fill(row, 0f);
        String t = str(e.get("type"));
        for (int i = 0; i < EVT_TYPES.length; i++) {
            if (EVT_TYPES[i].equals(t)) {
                row[evtOff("type") + i] = 1f;
            }
        }
        int tk = Tiles.parseKind(str(e.get("tile")));
        if (tk >= 0) {
            row[evtOff("tile_kind") + tk] = 1f;
        }
        int ck = Tiles.parseKind(str(e.get("called_tile")));
        if (ck >= 0) {
            row[evtOff("called_kind") + ck] = 1f;
        }
        Object actor = e.get("actor");
        if (actor != null) {
            row[evtOff("actor") + Math.floorMod(i(actor, 0) - seat, N_PLAYERS)] = 1f;
        }
        Object src = e.get("from");
        if (src != null) {
            row[evtOff("from") + Math.floorMod(i(src, 0) - seat, N_PLAYERS)] = 1f;
        }
        String mk = str(e.get("meld_kind"));
        for (int i = 0; i < MELD_KINDS.length; i++) {
            if (MELD_KINDS[i].equals(mk)) {
                row[evtOff("meld_kind") + i] = 1f;
            }
        }
        row[evtOff("tile_aka")] = str(e.get("tile")).startsWith("0") ? 1f : 0f;
        row[evtOff("called_aka")] = str(e.get("called_tile")).startsWith("0") ? 1f : 0f;
        row[evtOff("turn")] = i(e.get("turn"), 0) / 18f;
        row[evtOff("tsumogiri")] = bool(e.get("tsumogiri")) ? 1f : 0f;
        row[evtOff("sideways")] = bool(e.get("sideways")) ? 1f : 0f;
        row[evtOff("rip_phase")] = bool(e.get("rip_phase")) ? 1f : 0f;
        row[evtOff("seq_delta")] = (float) (Math.min(d(e.get("seq_delta"), 1.0), 8.0) / 8.0);
    }

    /**
     * 小局身份（增量缓存的失效键）：`场风-几局-本场@座位`。
     *
     * <p>`round` 缺失时返回 `null` ⇒ **不使用缓存**（宁可不省，也不能拿上一局的窗口当这一局的）。
     */
    static String handKey(Map<String, Object> obs) {        // 包内（自检经 V4Policy 钩子读）
        Map<String, Object> rnd = map(obs.get("round"));
        if (rnd == null) {
            return null;
        }
        return str(rnd.get("bakaze")) + "-" + i(rnd.get("kyoku"), 0) + "-" + i(rnd.get("honba"), 0)
                + "@" + i(obs.get("seat"), 0);
    }

    static int evtOff(String field) {
        int off = 0;
        for (int i = 0; i < EVT_FIELDS.length; i++) {
            if (EVT_FIELDS[i].equals(field)) {
                return off;
            }
            off += EVT_WIDTHS[i];
        }
        throw new IllegalArgumentException("没有这个 evt 字段：" + field);
    }

    // ---------------------------------------------------------------- ctx
    /** [64]：每次重算（便宜）。 */
    static float[] ctxVector(Map<String, Object> obs) {
        float[] v = new float[C_CTX];
        int seat = i(obs.get("seat"), 0);
        Map<String, Object> rnd = map(obs.get("round"));
        Object bz = rnd.get("bakaze");
        String bakaze = (bz == null || String.valueOf(bz).isEmpty() ? "E" : String.valueOf(bz))
                .toUpperCase(java.util.Locale.ROOT);

        int off = 0;
        for (String w : WINDS) {
            v[off++] = bakaze.equals(w) ? 1f : 0f;
        }
        v[off++] = i(rnd.get("kyoku"), 1) / 4f;
        v[off++] = i(rnd.get("honba"), 0) / 10f;
        v[off++] = i(rnd.get("riichi_sticks"), 0) / 4f;
        v[off++] = Math.floorMod(i(rnd.get("dealer"), 0) - seat, N_PLAYERS) / 3f;

        int[] scores = ObsFeatures.ints(obs.get("scores"), N_PLAYERS);
        int self = scores[seat];
        int othersSum = 0;
        int rank = 1;
        int minUp = Integer.MAX_VALUE;
        int minDown = Integer.MAX_VALUE;
        for (int j = 1; j < N_PLAYERS; j++) {
            int s = scores[(seat + j) % N_PLAYERS];
            othersSum += s;
            if (s > self) {
                rank++;
                minUp = Math.min(minUp, s - self);
            } else if (s < self) {
                minDown = Math.min(minDown, self - s);
            }
            v[off + j] = (s - 25000f) / 1000f;           // 下标 0 = 自己（旋转后）
        }
        v[off] = (self - 25000f) / 1000f;
        off += N_PLAYERS;
        for (int r = 1; r <= N_PLAYERS; r++) {
            v[off++] = rank == r ? 1f : 0f;
        }
        v[off++] = (self - othersSum / 3f) / 1000f;
        v[off++] = (self - 25000f) / 1000f;
        v[off++] = (minUp == Integer.MAX_VALUE ? 0f : minUp) / 1000f;
        v[off++] = (minDown == Integer.MAX_VALUE ? 0f : minDown) / 1000f;

        v[off++] = i(obs.get("tiles_left"), 0) / 70f;
        v[off++] = i(obs.get("dead_wall_left"), 0) / 4f;
        v[off++] = i(obs.get("total_discards"), 0) / 72f;
        v[off++] = i(obs.get("kan_count"), 0) / 4f;
        v[off++] = bool(obs.get("any_call")) ? 1f : 0f;
        v[off++] = bool(obs.get("haitei")) ? 1f : 0f;
        v[off++] = bool(obs.get("houtei")) ? 1f : 0f;
        v[off++] = bool(obs.get("rinshan")) ? 1f : 0f;

        List<List<Map<String, Object>>> melds = meldsOf(obs);
        int ownMelds = seat < melds.size() ? melds.get(seat).size() : 0;
        List<List<String>> discards = discardsOf(obs);
        int ownRiver = seat < discards.size() ? discards.get(seat).size() : 0;
        int[] riichiTurn = ObsFeatures.ints(obs.get("riichi_turn"), N_PLAYERS);
        v[off++] = i(obs.get("player_draws"), 0) / 18f;
        v[off++] = bool(obs.get("menzen")) ? 1f : 0f;
        v[off++] = bool(obs.get("self_riichi")) ? 1f : 0f;
        v[off++] = bool(obs.get("furiten")) ? 1f : 0f;
        v[off++] = ownMelds / 4f;
        v[off++] = str(obs.get("drawn")).startsWith("0") ? 1f : 0f;
        v[off++] = riichiTurn[seat] / 18f;
        v[off++] = 0f;                                  // 自家舍牌摸切率（v3 的逐张属性，暂留 0）
        v[off++] = ownRiver / 18f;
        v[off++] = 0f;

        // ⚠ `riichi` / `ippatsu` 与 `hand_red` 一样是**布尔数组**（`ints()` 读会整段变 0）——
        //   2026-09-27 C++ 镜像对拍后由子代理发现：夹具 7 个用例恰好都**没人立直**，
        //   所以 Java/Python 的这条漂移（ctx.seats 的 riichi/ippatsu 8 个通道）当时没暴露。
        //   修 Java 的同时**必须重做夹具并让它覆盖立直态**（`v4/export.py` 的选例闸门）。
        int[] riichi = boolFlags(obs.get("riichi"), N_PLAYERS);
        int[] ippatsu = boolFlags(obs.get("ippatsu"), N_PLAYERS);
        for (int j = 0; j < N_PLAYERS; j++) {
            v[off + j] = riichi[(seat + j) % N_PLAYERS] != 0 ? 1f : 0f;
        }
        off += N_PLAYERS;
        for (int j = 0; j < N_PLAYERS; j++) {
            v[off + j] = riichiTurn[(seat + j) % N_PLAYERS] / 18f;
        }
        off += N_PLAYERS;
        for (int j = 0; j < N_PLAYERS; j++) {
            v[off + j] = ippatsu[(seat + j) % N_PLAYERS] != 0 ? 1f : 0f;
        }
        off += N_PLAYERS;

        v[off++] = "turn".equals(str(obs.get("kind"))) ? 1f : 0f;
        v[off++] = str(obs.get("called_tile")).startsWith("0") ? 1f : 0f;
        for (int j = 0; j < N_PLAYERS; j++) {
            v[off + j] = 0f;
        }
        if ("claim".equals(str(obs.get("kind"))) && obs.get("from") != null) {
            v[off + Math.floorMod(i(obs.get("from"), 0) - seat, N_PLAYERS)] = 1f;
        }
        off += N_PLAYERS;
        String wn = str(obs.get("win_note"));
        for (String w : WIN_NOTES) {
            v[off++] = w.equals(wn) ? 1f : 0f;
        }
        return v;                                       // reserved 6 维保持 0
    }

    // ---------------------------------------------------------------- cand
    /** [n][128] = 基础 88（沿用 v3）+ 派生 8（v3，逐维分母不同）+ v4 新 3（恒 0）+ 预留 29。 */
    static float[][] candMatrix(Map<String, Object> obs, ObsFeatures.View view) {
        List<String> legal = legalOf(obs);
        float[][] out = new float[legal.size()][C_CAND];
        for (int i = 0; i < legal.size(); i++) {
            String key = legal.get(i);
            float[] base = Features.candidate(key, ObsFeatures.perCandidate(view, key));
            System.arraycopy(base, 0, out[i], 0, Math.min(base.length, C_CAND));
            // ⚠ `Features.candidate` 已经按 `DERIVED_CAND_SCALE` 归一化过派生段（与 Python 同一把尺子）
        }
        return out;
    }

    // ---------------------------------------------------------------- 消融
    static void zeroBlock(String bid, float[][] tile, float[][] evt, float[] ctx, float[][] cand) {
        switch (bid) {
            case "tile.own" -> zeroTile(tile, "own_count", "own_aka", "own_drawn");
            case "tile.per_opp" -> zeroTile(tile, "river_all", "river_before_riichi",
                    "river_after_riichi", "river_tedashi", "river_tsumogiri", "meld_count");
            case "tile.safety" -> zeroTile(tile, "safety_genbutsu", "safety_suji");
            case "tile.danger" -> zeroTile(tile, "danger_all", "danger_riichi");
            case "tile.global" -> zeroTile(tile, "visible", "unseen", "drawable", "dora_indicator",
                    "is_dora", "is_aka", "is_yakuhai", "is_terminal", "is_honor",
                    "suit_m", "suit_p", "suit_s", "reserved");
            case "evt.stream" -> {
                for (float[] row : evt) {
                    java.util.Arrays.fill(row, 0f);
                }
            }
            case "cand.base" -> zeroCols(cand, 0, CAND_BASE);
            case "cand.derived" -> zeroCols(cand, CAND_BASE, CAND_BASE + CAND_DERIVED);
            case "cand.reserved" -> zeroCols(cand, CAND_BASE + CAND_DERIVED, C_CAND);
            default -> {
                String group = bid.startsWith("ctx.") ? bid.substring(4) : null;
                if (group == null) {
                    throw new IllegalArgumentException("块 " + bid + " 不知道怎么清零");
                }
                int off = 0;
                for (int i = 0; i < CTX_GROUPS.length; i++) {
                    if (CTX_GROUPS[i].equals(group)) {
                        for (int j = 0; j < CTX_WIDTHS[i]; j++) {
                            ctx[off + j] = 0f;
                        }
                        return;
                    }
                    off += CTX_WIDTHS[i];
                }
                throw new IllegalArgumentException("块 " + bid + " 没有对应的 ctx 通道组");
            }
        }
    }

    private static void zeroTile(float[][] tile, String... channels) {
        for (String ch : channels) {
            int off = 0;
            for (int i = 0; i < TILE_CHANNELS.length; i++) {
                if (TILE_CHANNELS[i].equals(ch)) {
                    for (int k = 0; k < KIND_COUNT; k++) {
                        for (int c = 0; c < TILE_WIDTHS[i]; c++) {
                            tile[k][off + c] = 0f;
                        }
                    }
                    break;
                }
                off += TILE_WIDTHS[i];
            }
        }
    }

    private static void zeroCols(float[][] cand, int from, int to) {
        for (float[] row : cand) {
            for (int c = from; c < to; c++) {
                row[c] = 0f;
            }
        }
    }

    // ---------------------------------------------------------------- obs 小工具
    static int evtIndex(String type) {
        for (int i = 0; i < EVT_TYPES.length; i++) {
            if (EVT_TYPES[i].equals(type)) {
                return i;
            }
        }
        return -1;
    }

    static int i(Object o, int dflt) {
        if (o instanceof Number n) {
            return n.intValue();
        }
        if (o instanceof Boolean b) {
            return b ? 1 : 0;
        }
        if (o instanceof String s && !s.isEmpty()) {
            try {
                return Integer.parseInt(s.trim());
            } catch (NumberFormatException ignored) {
                return dflt;
            }
        }
        return dflt;
    }

    static double d(Object o, double dflt) {
        if (o instanceof Number n) {
            return n.doubleValue();
        }
        return dflt;
    }

    static boolean bool(Object o) {
        if (o instanceof Boolean b) {
            return b;
        }
        if (o instanceof Number n) {
            return n.doubleValue() != 0;
        }
        return false;
    }

    static String str(Object o) {
        return o == null ? "" : String.valueOf(o);
    }

    @SuppressWarnings("unchecked")
    static Map<String, Object> map(Object o) {
        return o instanceof Map ? (Map<String, Object>) o : Map.of();
    }

    @SuppressWarnings("unchecked")
    static List<Object> listOf(Object o) {
        return o instanceof List ? (List<Object>) o : List.of();
    }

    @SuppressWarnings("unchecked")
    static List<Map<String, Object>> eventsOf(Map<String, Object> obs) {
        Object raw = obs.get("events");
        List<Map<String, Object>> out = new ArrayList<>();
        if (raw instanceof List<?> l) {
            for (Object o : l) {
                if (o instanceof Map) {
                    out.add((Map<String, Object>) o);
                }
            }
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    static List<List<String>> discardsOf(Map<String, Object> obs) {
        List<List<String>> out = new ArrayList<>();
        for (int s = 0; s < N_PLAYERS; s++) {
            out.add(new ArrayList<>());
        }
        Object raw = obs.get("discards");
        if (raw instanceof List<?> l) {
            for (int s = 0; s < Math.min(N_PLAYERS, l.size()); s++) {
                Object row = l.get(s);
                if (row instanceof List<?> r) {
                    for (Object c : r) {
                        out.get(s).add(String.valueOf(c));
                    }
                }
            }
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    static List<List<Map<String, Object>>> meldsOf(Map<String, Object> obs) {
        List<List<Map<String, Object>>> out = new ArrayList<>();
        for (int s = 0; s < N_PLAYERS; s++) {
            out.add(new ArrayList<>());
        }
        Object raw = obs.get("melds");
        if (raw instanceof List<?> l) {
            for (int s = 0; s < Math.min(N_PLAYERS, l.size()); s++) {
                if (l.get(s) instanceof List<?> r) {
                    for (Object m : r) {
                        if (m instanceof Map) {
                            out.get(s).add((Map<String, Object>) m);
                        }
                    }
                }
            }
        }
        return out;
    }

    /** obs 里的**布尔数组**（`hand_red` 这种）→ 0/1 的 int 数组（顺带认 0/1 数字）。 */
    static int[] boolFlags(Object o, int size) {
        int[] out = new int[size];
        if (o instanceof List<?> l) {
            for (int k = 0; k < l.size() && k < size; k++) {
                Object v = l.get(k);
                if (v instanceof Boolean b) {
                    out[k] = b ? 1 : 0;
                } else if (v instanceof Number n) {
                    out[k] = n.doubleValue() != 0 ? 1 : 0;
                }
            }
        }
        return out;
    }

    /** 牌码序列 → 34 维计数。 */
    static int[] countsOf(List<?> codes) {
        int[] out = new int[KIND_COUNT];
        for (Object c : codes) {
            int k = Tiles.parseKind(String.valueOf(c));
            if (k >= 0 && k < KIND_COUNT) {
                out[k]++;
            }
        }
        return out;
    }

    static int[] meldCounts(Map<String, Object> obs, int absSeat) {
        int[] out = new int[KIND_COUNT];
        List<List<Map<String, Object>>> melds = meldsOf(obs);
        if (absSeat >= melds.size()) {
            return out;
        }
        for (Map<String, Object> m : melds.get(absSeat)) {
            for (Object code : listOf(m.get("tiles"))) {
                int k = Tiles.parseKind(String.valueOf(code));
                if (k >= 0 && k < KIND_COUNT) {
                    out[k]++;
                }
            }
        }
        return out;
    }

    /** 指示牌 → 宝牌牌种（数牌 +1 循环、风 E→S→W→N、三元 白→発→中）。 */
    static boolean[] doraKinds(List<Object> indicators) {
        boolean[] out = new boolean[KIND_COUNT];
        for (Object code : indicators) {
            int k = Tiles.parseKind(String.valueOf(code));
            if (k < 0) {
                continue;
            }
            int d = Tiles.doraFrom(k);
            if (d >= 0 && d < KIND_COUNT) {
                out[d] = true;
            }
        }
        return out;
    }

    /** 供 `V4Policy` 打日志用的一句话规格。 */
    public static String describe() {
        return "V4Features v" + FEATURE_VERSION + "（obs>=" + OBS_VERSION + "）"
                + " tile[" + KIND_COUNT + "," + C_TILE + "] evt[" + K_EVT + "," + C_EVT + "]"
                + " ctx[" + C_CTX + "] cand[n," + C_CAND + "]"
                + " blocks=" + BLOCK_IDS.length + " fingerprint=" + fingerprint();
    }

    /** 块 id → 宽度（加载器核对 `net.bin` 格式 2 的清单用）。 */
    public static Map<String, Integer> blockWidths() {
        Map<String, Integer> m = new LinkedHashMap<>();
        for (int i = 0; i < BLOCK_IDS.length; i++) {
            m.put(BLOCK_IDS[i], BLOCK_WIDTHS[i]);
        }
        return m;
    }
}
