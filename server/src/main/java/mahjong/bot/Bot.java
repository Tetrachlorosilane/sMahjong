package mahjong.bot;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import mahjong.core.Meld;
import mahjong.core.Tiles;
import mahjong.game.Round;
import mahjong.rules.Agari;
import mahjong.rules.Danger;
import mahjong.rules.HandEval;
import mahjong.rules.Shanten;
import mahjong.rules.Visible;
import mahjong.util.Json;

/**
 * 补位机器人：**牌效 + 押し引き + 打点 + 鸣き役**的启发式 AI。
 *
 * <p>它同时是训练用的 <b>teacher</b>（行为克隆的标签来源），所以这里的每一处取舍都会
 * 直接决定"学出来的策略长什么样" —— 一个只会"向听优先"的 teacher 教不出会防守的学生。
 *
 * <h2>决策的三层</h2>
 * <ol>
 *   <li><b>牌效</b>（{@link HandEval}）：向听 → 进张**枚数按实际可见牌扣**（{@link Visible}）
 *       → 听牌时优先良形（两面）；</li>
 *   <li><b>押し引き</b>（{@link Danger}）：有人立直且自己离和了还远 → 弃和，
 *       在一张不后退的候选里挑**最安全**的（现物 → 筋/壁 → 无信息）；</li>
 *   <li><b>打点与役</b>（{@code Round.scoreIfWin}）：同效率时留宝牌；立直还是ダマテン
 *       按"不立直能值多少"决定；鸣牌前先确认鸣完**还有役**。</li>
 * </ol>
 *
 * <h2>可测性</h2>
 * 上面三层判据都写成**只吃公开信息**的静态纯函数（{@link #chooseDiscard}、
 * {@link #hasYakuPlan}、{@link #shouldDeclareRiichi}），{@link HandState} 是那份公开信息。
 * 于是自检可以**直接构造局面**断言取舍（"有人立直时该打现物""无役的碰要放掉"），
 * 而不必去跑一整局碰运气；{@link HandState#of} 是唯一读 {@code Round} 的地方，
 * 它也**只读公开字段**（回归：{@code SelfTest.teacherTests} 的置换不变式）。
 *
 * <h2>刻意保留的简化</h2>
 * 机器人**从不开杠**（大明杠"简化：不开"，出牌段不看 {@code kan} 选项）——
 * 这条被自检依赖：岭上那条路径靠 {@link #debugAlwaysKan} 强制覆盖。
 * 因此"该不该开杠"不在本轮 teacher 的取舍范围内。
 */
public final class Bot {

    private Bot() {
    }

    /**
     * 自检用：teacher 各条取舍**实际被走过**的次数。
     *
     * <p>为什么要有这个：这三条（弃和 / 默听 / 因为无役而放掉鸣牌）都是"写了判据但可能根本没接上"
     * 的高危改动 —— 只测纯函数会出现"判据对、实战一次没走到"的假绿。
     * 与 {@code RoundScoring.debugCallCounts()} 同一个套路：实局里计数，自检断言非零。
     */
    public static long debugFoldCount;
    public static long debugDamaCount;
    /** 因为"鸣完没役"而放掉的鸣牌次数。 */
    public static long debugNoYakuRefuseCount;

    public static void debugResetCounts() {
        debugFoldCount = 0;
        debugDamaCount = 0;
        debugNoYakuRefuseCount = 0;
    }

    /**
     * 自检专用开关：一有机会就开杠。
     *
     * <p><b>正常对局里机器人从不开杠</b>（{@link #decideClaim} 里对大明杠是"简化：不开"，
     * 出牌段也不看 `kan` 选项）—— 后果是**整条「开杠 → 从岭上摸牌」的路径在整场模拟里一次都没走过**，
     * 于是 `dead_wall_left` 只在开局发过一次这种 bug 没人发现（报障：「杠后岭上牌并没有减少」）。
     * 自检把开关打开，让机器人真的开杠，从而覆盖：岭上摸牌、岭上开花、新宝牌指示牌、四杠散了。
     *
     * <p>默认 {@code false}；只有 {@code SelfTest.rinshanTests} 会临时打开并在 finally 里复位。
     */
    public static boolean debugAlwaysKan;

    /**
     * 自检专用：把出牌段开杠时上报的 `tile` 换成这个牌码（默认 {@code null} = 不换）。
     *
     * <p>用来构造**非法/过期**的杠动作（例如 {@code "9z"} 这种根本不存在的牌码），
     * 验证服务端不会因此白摸一张岭上牌、也不会吃掉一次开杠名额
     * （见 {@code Round.play()} 里「杠没成立就退回摸切」的兜底与 {@code SelfTest.kanLimitTests}）。
     */
    public static String debugKanTileOverride;

    // ================================================================= 公开信息视图

    /**
     * teacher 决策所需的一切 —— **只有公开信息 + 自己的手牌**。
     *
     * <p>把它单独拎出来有两个理由：① 让取舍判据可以脱离牌桌单测；② 从类型上挡住
     * "顺手读一下别家手牌"这种事（{@link #of} 是唯一入口，只读公开字段）。
     */
    public static final class HandState {
        /** 自家暗牌计数（自家回合是 14 张，含刚摸到的那张）。 */
        public int[] counts = new int[Tiles.KIND_COUNT];
        public int meldCount;
        public List<Meld> melds = List.of();
        /** 可见牌计数（{@link Visible#counts}）。 */
        public int[] visible = new int[Tiles.KIND_COUNT];
        /** 四家牌河的**牌种计数**（现物/筋判据）。 */
        public int[][] rivers = new int[4][Tiles.KIND_COUNT];
        public boolean[] riichi = new boolean[4];
        public boolean selfRiichi;
        /** 食断（喰いタン）是否成立 —— 决定"鸣完还有没有役"里断幺九算不算数。 */
        public boolean kuitan;
        public int seat;
        public int dealer;
        public int roundWind;
        /** 巡目（已打出的总张数），只用于危险度打分。 */
        public int turn;
        public List<Integer> doraIndicators = List.of();
        /** 自家暗牌里有几张**赤五**（打点查询要它 —— `Evaluator` 靠牌 id 数赤宝）。 */
        public int akaInHand;

        /** 从牌桌取公开信息。**只读自家手牌与公开字段**（回归：置换不变式见 SelfTest）。 */
        public static HandState of(Round r, int seat) {
            HandState st = new HandState();
            for (int id : r.hand[seat]) {
                st.counts[Tiles.kind(id)]++;
                if (Tiles.isRedId(id)) {
                    st.akaInHand++;
                }
            }
            st.melds = List.copyOf(r.melds[seat]);
            st.meldCount = r.melds[seat].size();
            st.visible = Visible.counts(r.discards, r.melds, r.doraIndicators());
            st.rivers = Danger.riverCounts(r.discards);
            st.riichi = r.riichi.clone();
            st.selfRiichi = r.riichi[seat];
            st.kuitan = r.rules.kuitan;
            st.seat = seat;
            st.dealer = r.dealer;
            st.roundWind = r.roundWind;
            st.turn = r.totalDiscards / 4;
            st.doraIndicators = List.copyOf(r.doraIndicators());
            return st;
        }

        public int kindCount(int kind) {
            return counts[kind];
        }

        /** 是否有别家立直。 */
        public boolean opponentRiichi() {
            for (int s = 0; s < 4; s++) {
                if (s != seat && riichi[s]) {
                    return true;
                }
            }
            return false;
        }

        /** 除自己外，有几家立直。 */
        public int opponentRiichiCount() {
            int n = 0;
            for (int s = 0; s < 4; s++) {
                if (s != seat && riichi[s]) {
                    n++;
                }
            }
            return n;
        }
    }

    // ================================================================= 决策入口

    public static Map<String, Object> decide(Round r, int seat, String kind,
                                             List<Map<String, Object>> options,
                                             Map<String, Object> extra) {
        try {
            if ("turn".equals(kind)) {
                return decideTurn(r, seat, options);
            }
            return decideClaim(r, seat, options, extra);
        } catch (RuntimeException e) {
            return fallback(options);
        }
    }

    private static Map<String, Object> fallback(List<Map<String, Object>> options) {
        for (Map<String, Object> o : options) {
            String t = (String) o.get("type");
            if ("pass".equals(t)) {
                return Json.obj("type", "pass");
            }
        }
        for (Map<String, Object> o : options) {
            String t = (String) o.get("type");
            if ("discard".equals(t)) {
                List<Object> ts = Json.list(o, "tiles");
                if (ts != null && !ts.isEmpty()) {
                    return Json.obj("type", "discard", "tile", ts.get(0));
                }
            }
        }
        return Json.obj("type", "pass");
    }

    // ------------------------------------------------------------- 自家回合

    private static Map<String, Object> decideTurn(Round r, int seat, List<Map<String, Object>> options) {
        Map<String, Object> tsumo = find(options, "tsumo");
        if (tsumo != null) {
            return Json.obj("type", "tsumo");
        }
        // 自检专用：一有机会就开杠（正常对局里机器人**从不**开杠，见 debugAlwaysKan）
        Map<String, Object> debugKan = debugAlwaysKan ? find(options, "kan") : null;
        if (debugKan != null) {
            Map<String, Object> pick = firstKan(debugKan);
            if (pick != null) {
                return pick;
            }
        }
        Map<String, Object> kyuushu = find(options, "kyuushu");
        if (kyuushu != null) {
            int n = yaochuKinds(r, seat);
            // 随机源由 Table 从 seedBase 派生（不是 Math.random）：自对弈/评测要同种子可复现
            if (n >= 10 || (n == 9 && r.table.botRng().nextDouble() < 0.5)) {
                return Json.obj("type", "kyuushu");
            }
        }
        Map<String, Object> discardOpt = find(options, "discard");
        List<Object> candidates = discardOpt == null ? new ArrayList<>() : Json.list(discardOpt, "tiles");
        if (candidates == null || candidates.isEmpty()) {
            return Json.obj("type", "pass");
        }
        final HandState st = HandState.of(r, seat);
        final String bestTile = chooseDiscard(st, candidates);
        if (bestTile == null) {
            return Json.obj("type", "discard", "tile", candidates.get(0));
        }
        // 立直：先问"不立直能不能和、值多少"（ダマテン判断），见 shouldDeclareRiichi
        Map<String, Object> riichi = find(options, "riichi");
        if (riichi != null) {
            List<Object> rts = Json.list(riichi, "tiles");
            if (rts != null && rts.contains(bestTile) && shouldDeclareRiichi(r, seat, st, bestTile)) {
                return Json.obj("type", "riichi", "tile", bestTile);
            }
        }
        return Json.obj("type", "discard", "tile", bestTile);
    }

    /**
     * 立直还是ダマテン（默听）—— **公开给自检**（它是 teacher 的核心取舍之一，要能单独断言）。
     *
     * <p>判据（只有一条，宁可保守）：**不立直也能和、且已经不亏**（≥4 番）时默听。
     * 立直只加 1 番（+ 里宝期望），而默听保留"手替え / 换听 / 之后鸣牌"的自由度 ——
     * 手已经够大时那份自由度更值钱。其余情况一律立直（+1 番 + 里宝期望 + 压制力）。
     *
     * <p>⚠ 前提是"不立直也能和"：如果这手**没有别的役**，不立直就根本和不了，
     * 所以那种情况必须立直（`scoreIfWin(..., assumeRiichi=false) == null`）。
     * 这一步正好用上 {@code Round.scoreIfWin} 的 {@code assumeRiichi} 参数。
     */
    public static boolean shouldDeclareRiichi(Round r, int seat, HandState st, String tileCode) {
        final int kind = Tiles.parseKind(tileCode);
        if (kind < 0) {
            return true;
        }
        // ⚠ 自家回合的 `st.counts` 是 **14 张**（含刚摸到的那张），要先把它减掉才是"打这张之后的 13 张"。
        //   传进来 13 张也容忍（减了会变成 12 张，那样听牌表恒为空 → 会误判成"必须立直"）。
        int[] after = st.counts.clone();
        if (Visible.total(after) >= 14 && kind >= 0 && after[kind] > 0) {
            after[kind]--;
        }
        int best = 0;
        for (int wk : Agari.waits(after, st.meldCount)) {
            // 对**计算出来的**那份 13 张暗牌求值（牌桌手上还是 14 张，见 Round.scoreIfWin 的重载）
            mahjong.rules.Evaluator.HandScore s =
                    r.scoreIfWin(seat, after, wk, false, false, st.akaInHand);
            if (s != null) {
                best = Math.max(best, s.totalHan());
            }
        }
        if (best < 4) {
            return true;
        }
        debugDamaCount++;
        return false;                    // 默听只在"已经够大"时用
    }

    /**
     * 打哪张 —— teacher 的核心取舍（**纯函数**，只吃 {@link HandState}）。
     *
     * <p>顺序：
     * <ol>
     *   <li>候选按**向听**分组，只看最好的那一组；</li>
     *   <li>**押し引き**：有人立直且自己还在 2 向听以上 → 弃和：
     *       在"不增加向听"的候选里选危险度最低的；若全都会后退，就直接选最安全的
     *       （此时牌效已不重要，先别放铳）；</li>
     *   <li>进攻：听牌候选取 (良形枚数, 总枚数, 宝牌数, 危险度)；未听牌取 (进张枚数, 宝牌数, 危险度)。
     *       ⚠ 危险度**排在最后**：没人立直时牌效优先（这是刻意的，别把顺序调过来）。</li>
     * </ol>
     *
     * @return 牌码；{@code null} = 没有可用候选
     */
    public static String chooseDiscard(HandState st, List<Object> candidates) {
        final boolean folding = st.opponentRiichi() && HandEval.shanten(st.counts, st.meldCount) >= 2;
        // ① 便宜的一遍：向听（1 次 DFS）+ 危险度 + 宝牌。
        //    `HandEval.of` 里有 34 次 DFS，听牌时还要对每个听牌张做一次和了形分解 ——
        //    为 14 张候选各跑一遍会把整场自对弈拖慢 1.7 倍（实测 3.6 → 6.2 秒/场）。
        //    所以先只用便宜的判据圈定"向听最小的那一组"，贵的评估只跑这一组。
        final int n = candidates.size();
        final String[] codes = new String[n];
        final int[] kinds = new int[n];
        final int[] shantens = new int[n];
        final int[] dangers = new int[n];
        final int[] doras = new int[n];
        int used = 0;
        int minSh = 99;
        for (Object o : candidates) {
            final String code = String.valueOf(o);
            final int kind = Tiles.parseKind(code);
            if (kind < 0 || st.counts[kind] <= 0) {
                continue;
            }
            int[] after = st.counts.clone();
            after[kind]--;
            final int sh = HandEval.shanten(after, st.meldCount);
            // 弃和时只看**立直家**的危险度（见 Danger.worstAgainstRiichi 的注释）；
            // 进攻时对四家取最坏（任何一家都可能已经听牌）。
            final Danger.Report danger = folding
                    ? Danger.worstAgainstRiichi(kind, st.visible, st.rivers, st.riichi, st.turn, st.seat)
                    : Danger.worst(kind, st.visible, st.rivers, st.riichi, st.turn, st.seat);
            codes[used] = code;
            kinds[used] = kind;
            shantens[used] = sh;
            dangers[used] = danger.score;
            doras[used] = HandEval.doraCount(after, st.melds, st.doraIndicators);
            used++;
            if (sh < minSh) {
                minSh = sh;
            }
        }
        // ② 贵的一遍：只在"向听最小"的那一组里比效率/听牌形/打点/安全度
        String bestTile = null;
        double bestScore = Double.NEGATIVE_INFINITY;
        int bestDanger = Integer.MAX_VALUE;
        for (int i = 0; i < used; i++) {
            if (shantens[i] != minSh) {
                continue;
            }
            int[] after = st.counts.clone();
            after[kinds[i]]--;
            double score;
            if (folding) {
                score = -dangers[i];                 // 弃和：唯一目标是安全
            } else {
                HandEval.Snapshot snap = HandEval.of(after, st.melds, st.visible);
                score = snap.tenpai
                        ? 200 + snap.goodWaitTiles * 4.0 + snap.waitTiles + doras[i] * 3.0
                        : snap.advanceTiles + doras[i] * 3.0;
                score -= dangers[i] * 0.05;          // 同效率时略偏好安全牌
            }
            final boolean better = bestTile == null
                    || score > bestScore + 1e-9
                    || (Math.abs(score - bestScore) <= 1e-9 && dangers[i] < bestDanger)
                    || (Math.abs(score - bestScore) <= 1e-9 && dangers[i] == bestDanger
                        && betterTieBreak(codes[i], bestTile));
            if (better) {
                bestTile = codes[i];
                bestScore = score;
                bestDanger = dangers[i];
            }
        }
        if (bestTile != null && folding) {
            debugFoldCount++;
        }
        return bestTile;
    }

    private static boolean betterTieBreak(String a, String b) {
        if (b == null) {
            return true;
        }
        return isolateScore(b) < isolateScore(a);
    }

    /** 越"孤立"的牌越优先打出。 */
    private static int isolateScore(String t) {
        int k = Tiles.parseKind(t);
        if (k < 0) {
            return -1;
        }
        if (Tiles.isHonor(k)) {
            return 0;
        }
        int n = Tiles.num(k);
        return Math.min(Math.abs(n - 5), 4);
    }

    private static int yaochuKinds(Round r, int seat) {
        int[] c = r.concealCounts(seat);
        int n = 0;
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (c[k] > 0 && Tiles.isYaochu(k)) {
                n++;
            }
        }
        return n;
    }

    private static int resolve(Round r, int seat, String s) {
        int kind = Tiles.parseKind(s);
        if (kind < 0) {
            return -1;
        }
        boolean red = Tiles.isRedStr(s);
        int fb = -1;
        for (int id : r.hand[seat]) {
            if (Tiles.kind(id) != kind) {
                continue;
            }
            if (red && Tiles.isRedId(id)) {
                return id;
            }
            if (!red) {
                if (!Tiles.isRedId(id)) {
                    return id;
                }
                fb = id;
            }
        }
        return fb;
    }

    private static int[] countsWithout(Round r, int seat, int removeId) {
        int[] c = new int[Tiles.KIND_COUNT];
        boolean removed = false;
        for (int id : r.hand[seat]) {
            if (!removed && id == removeId) {
                removed = true;
                continue;
            }
            c[Tiles.kind(id)]++;
        }
        return c;
    }

    private static int currentShanten(Round r, int seat) {
        return Shanten.min(r.concealCounts(seat), r.melds[seat].size());
    }

    // ------------------------------------------------------------- 鸣牌

    private static Map<String, Object> decideClaim(Round r, int seat,
                                                   List<Map<String, Object>> options,
                                                   Map<String, Object> extra) {
        if (find(options, "ron") != null) {
            return Json.obj("type", "ron");
        }
        // 自检专用：能大明杠就杠（正常对局里机器人不开杠，见 debugAlwaysKan）
        if (debugAlwaysKan && find(options, "kan") != null) {
            return Json.obj("type", "kan");
        }
        final HandState st = HandState.of(r, seat);
        final int cur = HandEval.shanten(st.counts, st.meldCount);
        final int kind = extra == null ? -1 : Tiles.parseKind(Json.str(extra, "tile", ""));
        if (kind < 0) {
            return Json.obj("type", "pass");
        }
        // 有人立直时，除非鸣完立刻是"役牌"这种硬役，否则不跟着上 —— 鸣牌会把牌打薄、还失去门清
        final boolean underPressure = st.opponentRiichiCount() >= 1;

        Map<String, Object> pon = find(options, "pon");
        if (pon != null && st.counts[kind] >= 2) {
            int[] c = st.counts.clone();
            c[kind] -= 2;
            Meld meld = new Meld(Meld.Kind.PON,
                    new int[]{Tiles.id(kind, 1), Tiles.id(kind, 2), Tiles.id(kind, 3)},
                    -1, Tiles.id(kind, 0));
            final boolean better = betterAfterCall(st, c, cur);
            final boolean yaku = hasYakuPlan(st, c, meld);
            if (better && !yaku) {
                debugNoYakuRefuseCount++;
            }
            if (better && yaku && !(underPressure && !isYakuhai(st, kind))) {
                return Json.obj("type", "pon");
            }
        }
        Map<String, Object> chi = find(options, "chi");
        if (chi != null) {
            List<Object> sets = Json.list(chi, "sets");
            List<Object> bestSet = null;
            int bestShanten = 99;
            if (sets != null) {
                for (Object so : sets) {
                    List<Object> pair = Json.asArr(so);
                    if (pair == null || pair.size() != 2) {
                        continue;
                    }
                    int[] c = st.counts.clone();
                    boolean ok = true;
                    int[] tiles = new int[3];
                    int n = 0;
                    for (Object t : pair) {
                        int k = Tiles.parseKind((String) t);
                        if (k < 0 || c[k] <= 0) {
                            ok = false;
                            break;
                        }
                        c[k]--;
                        tiles[n++] = Tiles.id(k, 1);
                    }
                    if (!ok) {
                        continue;
                    }
                    tiles[2] = Tiles.id(kind, 0);
                    java.util.Arrays.sort(tiles);
                    Meld meld = new Meld(Meld.Kind.CHI, tiles, -1, Tiles.id(kind, 0));
                    int after = bestShantenAfterCall(c, st.meldCount + 1,
                            13 - 3 * st.meldCount - 1);
                    if (after >= cur || after >= bestShanten) {
                        continue;                       // 不改善向听 / 不如已找到的最优
                    }
                    if (!hasYakuPlan(st, c, meld)) {
                        debugNoYakuRefuseCount++;       // 鸣完没役 → 这手吃下去也永远和不了
                        continue;
                    }
                    if (underPressure) {
                        continue;                       // 有人立直时不跟着吃
                    }
                    bestShanten = after;
                    bestSet = pair;
                }
            }
            if (bestSet != null) {
                return Json.obj("type", "chi", "tiles", bestSet);
            }
        }
        return Json.obj("type", "pass");
    }

    /** 鸣完（暗牌 {@code afterCall} + 新面子 {@code meld}）能不能比以前更接近和了。 */
    private static boolean betterAfterCall(HandState st, int[] afterCall, int cur) {
        return bestShantenAfterCall(afterCall, st.meldCount + 1, 13 - 3 * st.meldCount - 1) < cur;
    }

    /** 鸣牌后（多一张牌待打）能达到的最好向听。 */
    private static int bestShantenAfterCall(int[] countsAfterCall, int meldCount, int handSize) {
        int extra = handSize - (13 - 3 * meldCount);
        int best = 99;
        if (extra <= 0) {
            return Shanten.min(countsAfterCall, meldCount);
        }
        int[] c = countsAfterCall.clone();
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (c[k] == 0) {
                continue;
            }
            c[k]--;
            best = Math.min(best, Shanten.min(c, meldCount));
            c[k]++;
        }
        return best;
    }

    /**
     * 这张牌是不是役牌（三元牌 / 场风 / 自风）—— 鸣它本身就成役，是最硬的"鸣き役"。
     *
     * <p>公开给自检：场风/自风随座位与庄家变，这条判据错了整个"鸣き役"都会错。
     */
    public static boolean isYakuhai(HandState st, int kind) {
        if (Tiles.isDragon(kind)) {
            return true;
        }
        if (!Tiles.isWind(kind)) {
            return false;
        }
        final int roundWindKind = 27 + st.roundWind;
        final int seatWindKind = 27 + ((st.seat - st.dealer + 4) % 4);
        return kind == roundWindKind || kind == seatWindKind;
    }

    /**
     * 鸣完**还有没有役** —— teacher 不鸣"无役的手牌"。
     *
     * <p>为什么必须查：鸣牌打掉门前清，也就打掉了立直这条路；如果鸣完还剩个"没有役"的牌型，
     * 那就是**和不了**的手（只能靠自摸役牌/宝牌？宝牌不是役）。旧 teacher 只看"向听是否变好"，
     * 会心安理得地鸣出一手永远和不了的牌 —— 这是它最伤自己的一条。
     *
     * <p>判据是**计划**而不是保证（后续摸牌可能破坏它），按可靠性排序只认四种：
     * ① 役牌（三元牌/场风/自风 成刻）；② 断幺九（仅在食断成立的规则下）；③ 混一色/清一色；
     * ④ 对对和（全是碰/杠且暗牌里已有两组刻子或对子）。
     * ⚠ 三色同顺、一气通贯这类形状役**故意不查** —— 便宜的近似查不准，
     * 宁可漏掉一些"其实有役"的鸣牌，也不放行无役的手。
     */
    public static boolean hasYakuPlan(HandState st, int[] afterCall, Meld meld) {
        final int calledKind = Tiles.kind(meld.calledId);
        final boolean isPon = meld.kind == Meld.Kind.PON;
        // ① 役牌：碰役牌立刻成刻；或者暗牌里已有役牌的暗刻
        if (isPon && isYakuhai(st, calledKind)) {
            return true;
        }
        for (int k = 27; k < Tiles.KIND_COUNT; k++) {
            if (afterCall[k] >= 3 && isYakuhai(st, k)) {
                return true;
            }
        }
        // ② 断幺九（只在食断成立的规则下算役）
        if (st.kuitan && allSimples(st, afterCall, meld)) {
            return true;
        }
        // ③ 混一色 / 清一色
        if (singleSuit(st, afterCall, meld)) {
            return true;
        }
        // ④ 对对和：手上全是碰/杠，且暗牌里还有两组"刻子或对子"可用
        if (isPon) {
            boolean allTriplets = true;
            for (Meld m : st.melds) {
                if (m.kind == Meld.Kind.CHI) {
                    allTriplets = false;
                }
            }
            if (allTriplets) {
                int pairs = 0;
                for (int k = 0; k < Tiles.KIND_COUNT; k++) {
                    if (afterCall[k] >= 2) {
                        pairs++;
                    }
                }
                if (pairs >= 2) {
                    return true;
                }
            }
        }
        return false;
    }

    /** 暗牌 + 副露全是中张（2..8）—— 断幺九的前提。 */
    private static boolean allSimples(HandState st, int[] afterCall, Meld meld) {
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (afterCall[k] > 0 && !Tiles.isSimple(k)) {
                return false;
            }
        }
        for (Meld m : st.melds) {
            for (int t : m.tiles) {
                if (!Tiles.isSimple(Tiles.kind(t))) {
                    return false;
                }
            }
        }
        for (int t : meld.tiles) {
            if (!Tiles.isSimple(Tiles.kind(t))) {
                return false;
            }
        }
        return true;
    }

    /** 暗牌 + 副露只占一种花色（外加字牌）—— 混一色/清一色的前提。 */
    private static boolean singleSuit(HandState st, int[] afterCall, Meld meld) {
        int suit = -1;
        for (int k = 0; k < 27; k++) {
            if (afterCall[k] > 0) {
                int s = Tiles.suit(k);
                if (suit >= 0 && s != suit) {
                    return false;
                }
                suit = s;
            }
        }
        for (Meld m : st.melds) {
            for (int t : m.tiles) {
                int k = Tiles.kind(t);
                if (k >= 27) {
                    continue;
                }
                int s = Tiles.suit(k);
                if (suit >= 0 && s != suit) {
                    return false;
                }
                suit = s;
            }
        }
        for (int t : meld.tiles) {
            int k = Tiles.kind(t);
            if (k >= 27) {
                continue;
            }
            int s = Tiles.suit(k);
            if (suit >= 0 && s != suit) {
                return false;
            }
            suit = s;
        }
        return true;                     // 全是字牌也算（字一色 / 混一色达成）
    }

    private static Map<String, Object> firstKan(Map<String, Object> kanOption) {
        List<Object> kans = Json.list(kanOption, "kans");
        if (kans == null || kans.isEmpty()) {
            return null;
        }
        Object first = kans.get(0);
        if (!(first instanceof Map)) {
            return null;
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> m = (Map<String, Object>) first;
        Map<String, Object> cmd = Json.obj("type", "kan");
        cmd.put("kind", String.valueOf(m.get("kind")));
        cmd.put("tile", debugKanTileOverride != null ? debugKanTileOverride
                                                    : String.valueOf(m.get("tile")));
        return cmd;
    }

    private static Map<String, Object> find(List<Map<String, Object>> options, String type) {
        if (options == null) {
            return null;
        }
        for (Map<String, Object> o : options) {
            if (type.equals(o.get("type"))) {
                return o;
            }
        }
        return null;
    }

    /** 听牌种类（供外部调试）。 */
    public static Set<Integer> waits(Round r, int seat) {
        return new LinkedHashSet<>(Agari.waits(r.concealCounts(seat), r.melds[seat].size()));
    }
}
