package mahjong.ai;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import mahjong.core.Meld;
import mahjong.core.Tiles;
import mahjong.game.Round;
import mahjong.rules.Danger;
import mahjong.rules.HandEval;
import mahjong.rules.Shanten;
import mahjong.rules.Visible;
import mahjong.util.Json;

/**
 * **派生特征**：把 teacher 决策时用的那几把尺子（向听 / 进张 / 听牌形 / 逐张危险度）
 * 从观测里算出来，作为训练特征的增量。
 *
 * <h2>为什么放在 Java，而不是在 Python 里再写一份</h2>
 * 这些量在**推理时也必须算**（B 形态的纯 Java 前向、以及将来任何服务端策略），
 * 而它们的权威实现就在 {@link HandEval} / {@link Danger} / {@link Shanten}。
 * 在 Python 里移植＝两套实现、必然漂移；所以：**Java 算一次，Python 只读**。
 * 两条数据通路（内存里的 {@code Round} 与离线轨迹里的 obs JSON）走**同一份公式**，
 * 差别只在"怎么把数据填进 {@link View}"—— 那正是 {@code SelfTest.obsFeaturesTests} 对拍的东西。
 *
 * <h2>布局（自检里钉着；改动要同步 {@code docs/TRAINING.md} §5 与 Python 侧）</h2>
 * <pre>
 *   逐决策（{@link #PER_DECISION} = 68）：
 *     [0,34)   danger_worst[k]    —— 对四家最坏的放铳危险度（0..100，含"鸣き手威胁"口径由消费侧决定）
 *     [34,68)  danger_riichi[k]   —— 只对立直家最坏（弃和口径）
 *   逐候选（{@link #PER_CANDIDATE} = 8，顺序固定）：
 *     shanten, advance_types, advance_tiles, wait_types, wait_tiles,
 *     good_wait_types, good_wait_tiles, dora_count
 * </pre>
 *
 * <p>⚠ **逐候选的快照统一是"这一手做完、该打的那张也打完"之后的形态**（目标张数
 * {@code 13 − 3 × 新副露数}）：吃/碰/加杠之后本来就还要打一张，暗杠/大明杠之后不用。
 * 需要挑一张打时取**向听最小**的那张，同向听取**牌种下标最小**的（确定性，
 * 与 {@code Bot} 的两遍扫描同源但更简单：只求特征，不求策略）。
 *
 * <p>⚠ 只读**合法可见**信息：{@code hand}（自家）/ {@code melds} / {@code discards} /
 * {@code dora_indicators} / {@code visible} / {@code riichi} / {@code total_discards}。
 * 别家手牌、牌山顺序、里宝都不在这里出现（与 {@code PROTOCOL.md} §8.2 同一条纪律）。
 */
public final class ObsFeatures {

    /** 派生特征的版本（变了就要 +1：数据集靠它判兼容）。 */
    public static final int FEATURE_VERSION = 1;
    /** 逐决策段长度。 */
    public static final int PER_DECISION = 2 * Tiles.KIND_COUNT;
    /** 逐候选段长度。 */
    public static final int PER_CANDIDATE = 8;

    private ObsFeatures() {
    }

    // ================================================================= 公开信息视图

    /** 算派生特征所需的全部输入（**只用公开信息 + 自家手牌**）。 */
    public static final class View {
        public int[] hand = new int[Tiles.KIND_COUNT];
        public List<Meld> melds = List.of();
        public int[] visible = new int[Tiles.KIND_COUNT];
        public int[][] rivers = new int[4][Tiles.KIND_COUNT];
        public boolean[] riichi = new boolean[4];
        /** 宝牌指示牌的**牌种 kind**（与 `Round.doraIndicators()` / `HandEval.doraCount` 同一口径）。 */
        public List<Integer> dora = List.of();
        public int turn;
        public int seat;
        public String kind = "turn";
        /** 被鸣 / 被荣那张的**牌码**（鸣牌询问才有；`chi` 合成面子时需要它才知道第三张是什么）。 */
        public String calledTile;

        public int meldCount() {
            return melds.size();
        }
    }

    /** 从**离线轨迹的 obs JSON**（{@code PROTOCOL.md} §8.2）填视图。 */
    public static View ofObs(Map<String, Object> obs) {
        View v = new View();
        v.seat = Json.i(obs, "seat", 0);
        v.kind = Json.str(obs, "kind", "turn");
        int[] handJson = ints(obs.get("hand"), Tiles.KIND_COUNT);
        System.arraycopy(handJson, 0, v.hand, 0, Tiles.KIND_COUNT);
        int[] vis = ints(obs.get("visible"), Tiles.KIND_COUNT);
        System.arraycopy(vis, 0, v.visible, 0, Tiles.KIND_COUNT);
        List<Object> meldsAll = Json.list(obs, "melds");
        List<Meld> mine = new ArrayList<>();
        if (meldsAll != null && v.seat < meldsAll.size()) {
            for (Object o : Json.asArr(meldsAll.get(v.seat))) {
                Meld m = meldOf(Json.asObj(o));
                if (m != null) {
                    mine.add(m);
                }
            }
        }
        v.melds = mine;
        List<Object> discardsAll = Json.list(obs, "discards");
        if (discardsAll != null) {
            for (int s = 0; s < 4 && s < discardsAll.size(); s++) {
                for (Object code : Json.asArr(discardsAll.get(s))) {
                    int k = Tiles.parseKind(String.valueOf(code));
                    if (k >= 0) {
                        v.rivers[s][k]++;
                    }
                }
            }
        }
        List<Object> riichiJson = Json.list(obs, "riichi");
        if (riichiJson != null) {
            for (int s = 0; s < 4 && s < riichiJson.size(); s++) {
                v.riichi[s] = Boolean.TRUE.equals(riichiJson.get(s));
            }
        }
        List<Object> doraJson = Json.list(obs, "dora_indicators");
        List<Integer> dora = new ArrayList<>();
        if (doraJson != null) {
            for (Object code : doraJson) {
                int k = Tiles.parseKind(String.valueOf(code));
                if (k >= 0) {
                    // ⚠ 这里必须存**牌种 kind**（不是牌 id）：`HandEval.doraCount` → `Tiles.doraFrom(kind)`，
                    //   而 `Round.doraIndicators()` / `Bot.HandState.of` 一路也都是 kind。
                    //   包成 `Tiles.id(k,0)` 会让 dora 整个算错 —— golden 对拍第二版就是这样红的。
                    dora.add(k);
                }
            }
        }
        v.dora = dora;
        Object called = obs.get("called_tile");
        v.calledTile = called instanceof String s && !s.isEmpty() ? s : null;
        // `turn` 的口径与 `Bot.HandState.of` 完全一致（已打出的总张数 / 4）
        v.turn = Json.i(obs, "total_discards", 0) / 4;
        return v;
    }

    /** 从**内存里的牌局**填视图（golden 对拍的另一条通路；与 {@code Bot.HandState.of} 同源）。 */
    public static View ofRound(Round r, int seat, String kind) {
        return ofRound(r, seat, kind, null);
    }

    /**
     * @param calledTile 被鸣那张的**牌码**（鸣牌决策要传；`chi` 合成面子时缺它就只好靠猜）
     */
    public static View ofRound(Round r, int seat, String kind, String calledTile) {
        View v = new View();
        v.seat = seat;
        v.kind = kind;
        v.calledTile = calledTile;
        for (int id : r.hand[seat]) {
            v.hand[Tiles.kind(id)]++;
        }
        v.melds = List.copyOf(r.melds[seat]);
        v.visible = Visible.counts(r.discards, r.melds, r.doraIndicators());
        v.rivers = Danger.riverCounts(r.discards);
        v.riichi = r.riichi.clone();
        v.dora = List.copyOf(r.doraIndicators());
        v.turn = r.totalDiscards / 4;
        return v;
    }

    /**
     * → 定长 int[]。**两种形态都必须认**（这是 golden 对拍抓到的一个真坑）：
     * 轨迹文件里的 `hand` / `visible` 是 JSON 数组（解析后是 `List`），
     * 而 `Observation.toJson()` 在**内存里**放的是原始 `int[]`（`"hand", hand`）——
     * 只认一种，另一条通路就会静默算成全 0。
     */
    public static int[] ints(Object o, int size) {
        int[] out = new int[size];
        if (o instanceof int[] a) {
            System.arraycopy(a, 0, out, 0, Math.min(a.length, size));
        } else if (o instanceof List<?> l) {
            for (int i = 0; i < size && i < l.size(); i++) {
                if (l.get(i) instanceof Number n) {
                    out[i] = n.intValue();
                }
            }
        }
        return out;
    }

    /** 从**内存里的观测对象**填视图（B 形态推理路径；与 obs JSON / Round 两条通路同一份公式）。 */
    public static View ofObservation(Observation o) {
        View v = new View();
        v.seat = o.seat;
        v.kind = o.kind;
        v.calledTile = o.calledTile == null || o.calledTile.isEmpty() ? null : o.calledTile;
        System.arraycopy(o.hand, 0, v.hand, 0, Math.min(o.hand.length, v.hand.length));
        System.arraycopy(o.visible, 0, v.visible, 0, Math.min(o.visible.length, v.visible.length));
        List<Meld> mine = new ArrayList<>();
        if (o.melds != null && o.seat < o.melds.size()) {
            for (Map<String, Object> m : o.melds.get(o.seat)) {
                Meld meld = meldOf(m);
                if (meld != null) {
                    mine.add(meld);
                }
            }
        }
        v.melds = mine;
        if (o.discards != null) {
            for (int s = 0; s < 4 && s < o.discards.size(); s++) {
                for (String code : o.discards.get(s)) {
                    int k = Tiles.parseKind(code);
                    if (k >= 0) {
                        v.rivers[s][k]++;
                    }
                }
            }
        }
        v.riichi = o.riichi.clone();
        List<Integer> dora = new ArrayList<>();
        for (String code : o.doraIndicators) {
            int k = Tiles.parseKind(code);
            if (k >= 0) {
                dora.add(k);          // 与 `Round.doraIndicators()` 同口径：**牌种 kind**
            }
        }
        v.dora = dora;
        v.turn = o.totalDiscards / 4;
        return v;
    }

    /** obs 里的 `melds[s][]` → {@link Meld}（牌码 → 合成 id：同一 kind 的 copy 依次取 0,1,2,3）。 */
    private static Meld meldOf(Map<String, Object> m) {        if (m == null) {
            return null;
        }
        Meld.Kind kind = Meld.Kind.of(Json.str(m, "kind", null));
        List<Object> codes = Json.list(m, "tiles");
        if (kind == null || codes == null || codes.isEmpty()) {
            return null;
        }
        int[] ids = new int[codes.size()];
        int[] used = new int[Tiles.KIND_COUNT];
        for (int i = 0; i < codes.size(); i++) {
            int k = Tiles.parseKind(String.valueOf(codes.get(i)));
            if (k < 0) {
                return null;
            }
            ids[i] = Tiles.id(k, used[k]++);
        }
        int calledKind = Tiles.parseKind(Json.str(m, "called_tile", ""));
        int calledId = calledKind < 0 ? ids[0] : Tiles.id(calledKind, 0);
        return new Meld(kind, ids, Json.i(m, "from", -1), calledId);
    }

    // ================================================================= 逐决策

    /** 68 维：两套逐张危险度。 */
    public static int[] perDecision(View v) {
        int[] out = new int[PER_DECISION];
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            out[k] = Danger.worst(k, v.visible, v.rivers, v.riichi, v.turn, v.seat).score;
            out[Tiles.KIND_COUNT + k] =
                    Danger.worstAgainstRiichi(k, v.visible, v.rivers, v.riichi, v.turn, v.seat).score;
        }
        return out;
    }

    // ================================================================= 逐候选

    /**
     * 8 维：这一手做完（该打的也打完）之后的形态。
     *
     * @param key 动作键（{@code PROTOCOL.md} §8.3）—— 只用到它的 type / 牌码 / 取法
     */
    public static int[] perCandidate(View v, String key) {
        Action a = Action.parse(key);
        if (a == null) {
            return new int[PER_CANDIDATE];
        }
        if ("tsumo".equals(a.type) || "ron".equals(a.type)) {
            return new int[PER_CANDIDATE];       // 终局动作：没有"之后的形态"可言
        }
        int[] counts = v.hand.clone();
        List<Meld> melds = new ArrayList<>(v.melds);
        switch (a.type) {
            case "discard":
            case "riichi": {
                int k = Tiles.parseKind(a.tile);
                if (k < 0 || counts[k] <= 0) {
                    return new int[PER_CANDIDATE];
                }
                counts[k]--;
                break;
            }
            case "chi": {
                if (!take(counts, a.tiles)) {
                    return new int[PER_CANDIDATE];
                }
                melds.add(calledMeld(Meld.Kind.CHI, a.tiles, v.calledTile));
                break;
            }
            case "pon": {
                if (!take(counts, a.tiles)) {
                    return new int[PER_CANDIDATE];
                }
                melds.add(calledMeld(Meld.Kind.PON, a.tiles, v.calledTile));
                break;
            }
            case "kan": {
                int k = a.tile == null ? -1 : Tiles.parseKind(a.tile);
                if ("daiminkan".equals(a.kanKind) || "ankan".equals(a.kanKind)) {
                    if ("ankan".equals(a.kanKind) && k >= 0 && counts[k] >= 4) {
                        counts[k] -= 4;               // 暗杠的四张全在手里（没有"被鸣那张"）
                        melds.add(synthetic(Meld.Kind.ANKAN, fourOf(a.tile)));
                    } else if (a.tiles != null && !a.tiles.isEmpty()) {
                        if (!take(counts, a.tiles)) {
                            return new int[PER_CANDIDATE];
                        }
                        melds.add(calledMeld(Meld.Kind.of(a.kanKind), a.tiles, v.calledTile));
                    } else if (k >= 0 && counts[k] >= 3) {
                        counts[k] -= 3;
                        melds.add(calledMeld(Meld.Kind.DAIMINKAN, List.of(a.tile), v.calledTile));
                    } else {
                        return new int[PER_CANDIDATE];
                    }
                } else {                              // 加杠：手里那一张并进已有的碰（那副碰已在 melds 里）
                    if (k < 0 || counts[k] <= 0) {
                        return new int[PER_CANDIDATE];
                    }
                    counts[k]--;
                }
                break;
            }
            default:
                break;                                // pass / kyuushu：形态不变
        }
        // 统一规则：张数超过 `13 − 3×新副露数` 就再挑一张打（向听最小、同向听取小下标）
        final int target = 13 - 3 * melds.size();
        int sum = 0;
        for (int c : counts) {
            sum += c;
        }
        if (sum > target) {
            int best = -1;
            int bestSh = 99;
            int[] probe = counts.clone();
            for (int k = 0; k < Tiles.KIND_COUNT; k++) {
                if (probe[k] <= 0) {
                    continue;
                }
                probe[k]--;
                int sh = Shanten.min(probe, melds.size());
                probe[k]++;
                if (sh < bestSh) {
                    bestSh = sh;
                    best = k;
                }
            }
            if (best >= 0) {
                counts[best]--;
            }
        }
        HandEval.Snapshot snap = HandEval.of(counts, melds, v.visible);
        int[] out = new int[PER_CANDIDATE];
        out[0] = snap.tenpai ? 0 : snap.shanten;      // 听牌记 0（与 shanten 同轴，和了形 -1 不出现）
        out[1] = snap.advanceTypes;
        out[2] = snap.advanceTiles;
        out[3] = snap.waitTypes;
        out[4] = snap.waitTiles;
        out[5] = snap.goodWaitTypes;
        out[6] = snap.goodWaitTiles;
        out[7] = HandEval.doraCount(counts, melds, v.dora);
        return out;
    }

    /** 从 `counts` 里扣掉这些牌码（不够扣返回 false）。 */
    private static boolean take(int[] counts, List<String> codes) {
        if (codes == null || codes.isEmpty()) {
            return false;
        }
        int[] need = new int[Tiles.KIND_COUNT];
        for (String c : codes) {
            int k = Tiles.parseKind(c);
            if (k < 0) {
                return false;
            }
            need[k]++;
        }
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (need[k] > counts[k]) {
                return false;
            }
        }
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            counts[k] -= need[k];
        }
        return true;
    }

    /** 只为"面子形状"合成一副副露（特征只需要 kind 与 tile 的 kind，见 {@code Meld}）。 */
    private static Meld synthetic(Meld.Kind kind, List<String> codes) {
        int[] ids = new int[Math.max(1, codes.size())];
        int[] used = new int[Tiles.KIND_COUNT];
        for (int i = 0; i < codes.size(); i++) {
            int k = Math.max(0, Tiles.parseKind(codes.get(i)));
            ids[i] = Tiles.id(k, used[k]++);
        }
        int first = Tiles.parseKind(codes.get(0));
        return new Meld(kind, ids, -1, Tiles.id(Math.max(0, first), 0));
    }

    /**
     * 合成**鸣来的那副面子**：键里只有"从手里取的 1~3 张"，还差**被鸣的那张**
     * （`pon`/`daiminkan` 的 kind 与手牌同 kind；`chi` 要从 {@code called_tile} 拿）。
     *
     * <p>⚠ 别省这一张：张数不对会让 `HandEval.doraCount` 少算（实测就是它让 golden 对拍红过一次），
     * 也可能让和了形分解失真。轨迹里鸣牌决策**一定**带 `called_tile`；实在缺了就按顺子窗口补一个。
     */
    private static Meld calledMeld(Meld.Kind kind, List<String> handCodes, String calledTile) {
        List<String> codes = new ArrayList<>(handCodes);
        String called = calledTile != null ? calledTile : guessedThird(kind, handCodes);
        if (called != null) {
            codes.add(called);
        }
        return synthetic(kind, codes);
    }

    private static String guessedThird(Meld.Kind kind, List<String> handCodes) {
        if (kind != Meld.Kind.CHI || handCodes.size() != 2) {
            return handCodes.isEmpty() ? null : handCodes.get(0);
        }
        int a = Tiles.parseKind(handCodes.get(0));
        int b = Tiles.parseKind(handCodes.get(1));
        int lo = Math.min(a, b);
        int hi = Math.max(a, b);
        if (hi - lo == 2 || hi - lo == 1) {
            if (hi - lo == 2) {
                return Tiles.toStr(lo + 1);           // 坎张：被鸣的是中间那张
            }
            if (lo % 9 >= 1) {
                return Tiles.toStr(lo - 1);           // 边张/两面：优先补小的那边
            }
            return Tiles.toStr(hi + 1);
        }
        return Tiles.toStr(lo);
    }

    private static List<String> fourOf(String code) {
        return List.of(code, code, code, code);
    }

    /** 布局自述（Java 与 Python 两侧都要照它对齐）。 */
    public static String describe() {
        return "ObsFeatures v" + FEATURE_VERSION
                + " perDecision=" + PER_DECISION + "（danger_worst[34] + danger_riichi[34]）"
                + " perCandidate=" + PER_CANDIDATE
                + "（shanten, advance_types, advance_tiles, wait_types, wait_tiles,"
                + " good_wait_types, good_wait_tiles, dora_count）";
    }
}
