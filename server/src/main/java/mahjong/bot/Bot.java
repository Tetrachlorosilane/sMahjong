package mahjong.bot;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import mahjong.core.Meld;
import mahjong.core.Tiles;
import mahjong.game.Round;
import mahjong.game.RoundScoring;
import mahjong.rules.Agari;
import mahjong.rules.Danger;
import mahjong.rules.Evaluator;
import mahjong.rules.HandEval;
import mahjong.rules.Payments;
import mahjong.rules.Shanten;
import mahjong.rules.Visible;
import mahjong.util.Json;

/**
 * 补位机器人：**牌效 + 押し引き + 打点 + 鸣き役 + 顺位/终局**的启发式 AI。
 *
 * <p>它同时是训练用的 <b>teacher</b>（行为克隆的标签来源），所以这里的每一处取舍都会
 * 直接决定"学出来的策略长什么样" —— 一个只会"向听优先"的 teacher 教不出会防守的学生。
 *
 * <h2>决策的五层</h2>
 * <ol>
 *   <li><b>牌效</b>（{@link HandEval}）：向听 → 进张**枚数按实际可见牌扣**（{@link Visible}）
 *       → 听牌时优先良形（两面）；</li>
 *   <li><b>押し引き</b>（{@link Danger} + 期望值）：有人立直（或鸣き手威胁明显）且期望值为负 → 弃和，
 *       在一张不后退的候选里挑**最安全**的（现物 → 筋/壁 → 无信息）；</li>
 *   <li><b>打点与役</b>（{@code Round.scoreIfWin}）：同效率时留宝牌；立直还是ダマテン
 *       按"不立直能值多少"决定；鸣牌前先确认鸣完**还有役**；</li>
 *   <li><b>开杠</b>（{@link #shouldKan}）：暗杠/加杠要**不丢听牌、不后退**，加杠还要过危险度
 *       （会被抢杠 = 直接放铳），弃和中与"第 4 个杠会四杠散了"时一律不开；
 *       大明杠与碰同一把尺子（有役计划 + 向听真的变好）。</li>
 *   <li><b>顺位与终局</b>（档 C）：押し引き的期望值门槛随**当前顺位**走
 *       （{@link #pushThreshold}：1 位守住、4 位抢分），终局（オーラス）翻倍；
 *       终局默听手在"**荣和抬不动顺位、自摸才抬得动**"时见逃（{@link #shouldDeclineRon}）；
 *       对手模型（{@link #openThreat}）把"未立直但已经鸣开"的几家当成疑似听牌，
 *       没人立直也可能该弃和。</li>
 * </ol>
 *
 * <h2>可测性</h2>
 * 上面五层判据都写成**只吃公开信息**的静态纯函数（{@link #chooseDiscard}、
 * {@link #hasYakuPlan}、{@link #shouldDeclareRiichi}、{@link #shouldKan}、
 * {@link #shouldDeclineRon}、{@link #pushThreshold}），{@link HandState} 是那份公开信息。
 * 于是自检可以**直接构造局面**断言取舍（"有人立直时该打现物""无役的碰要放掉""暗杠拆搭子就不开"
 * "ラス目の终局不该无脑弃和""荣和抬不动顺位就见逃"），
 * 而不必去跑一整局碰运气；{@link HandState#of} 是唯一读 {@code Round} 的地方，
 * 它也**只读公开字段**（回归：{@code SelfTest.teacherTests} 的置换不变式）。
 */
public final class Bot {

    private Bot() {
    }

    /**
     * 自检用：teacher 各条取舍**实际被走过**的次数。
     *
     * <p>为什么要有这个：这几条（弃和 / 默听 / 因为无役而放掉鸣牌 / 开杠与各种"不开"）
     * 都是"写了判据但可能根本没接上"的高危改动 —— 只测纯函数会出现"判据对、实战一次没走到"的假绿。
     * 与 {@code RoundScoring.debugCallCounts()} 同一个套路：实局里计数，自检断言非零。
     */
    public static long debugFoldCount;
    public static long debugDamaCount;
    /** 因为"鸣完没役"而放掉的鸣牌次数。 */
    public static long debugNoYakuRefuseCount;
    /** 真的开了杠的次数（暗杠 / 加杠 / 大明杠）。 */
    public static long debugKanCount;
    /** 因为"杠完会丢听牌 / 向听倒退"而放掉的杠。 */
    public static long debugKanRefuseWait;
    /** 因为"正在弃和"而放掉的杠。 */
    public static long debugKanRefusePressure;
    /** 因为"第 4 个杠会把本局打散（四杠散了）"而放掉的杠。 */
    public static long debugKanRefuseFourKan;
    /** 因为"这张牌对他家太危险（加杠会被抢杠）"而放掉的**加杠**。 */
    public static long debugKanRefuseDanger;
    /** 因为"门清鸣了不值（不听牌又不到 2 番）"而放掉的副露。 */
    public static long debugCallValueRefuseCount;
    /** 押し引き判定为**推进**（期望值为正）的次数。 */
    public static long debugPushCount;
    /** 因为"顺位门槛"（{@link #pushThreshold}）而**改了结论**的次数（期望值正负与最终判据不一致）。 */
    public static long debugPlacementFlipCount;
    /** 终局见逃（{@link #shouldDeclineRon}）的次数。 */
    public static long debugRonDeclineCount;
    /** 因为"没人立直、但有人鸣开得很明显"而弃和的次数（对手模型那条闸门）。 */
    public static long debugOpenFoldCount;

    public static void debugResetCounts() {
        debugFoldCount = 0;
        debugDamaCount = 0;
        debugNoYakuRefuseCount = 0;
        debugKanCount = 0;
        debugKanRefuseWait = 0;
        debugKanRefusePressure = 0;
        debugKanRefuseFourKan = 0;
        debugKanRefuseDanger = 0;
        debugCallValueRefuseCount = 0;
        debugPushCount = 0;
        debugPlacementFlipCount = 0;
        debugRonDeclineCount = 0;
        debugOpenFoldCount = 0;
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
        /** 本局**全场**已经开过的杠数（副露是公开信息，所以这是公开的）。 */
        public int kanCount;
        /** 规则开关：四杠散了会不会强制流局（决定"第 4 个杠"值不值得开）。 */
        public boolean fourKanAbort;
        /** 牌山剩余可摸张数（押し引き的期望值要知道"自己还能摸几巡"）。 */
        public int tilesLeft;
        /**
         * 四家点数（{@code Round.scores}）—— 档 C 的顺位判断要用它。
         *
         * <p>⚠ {@code null} = **点数未知**（自检直接构造的局面）。顺位类判据一律先看这个：
         * 未知就不施加顺位权重（{@link #pushThreshold} 返回 0、{@link #shouldDeclineRon} 直接返回假），
         * 这样"纯形状"的老断言不会被一条隐式的顺位假设污染。
         */
        public int[] scores;
        /** 供託里的立直棒根数（和了能收走，算打点时要它）。 */
        public int sticks;
        /** 本局是第几局（1..4）。 */
        public int kyoku;
        /** 是否**终局**（オーラス）：场风已达本赛制上限且是第 4 局（含南入 / 西入的延长战）。 */
        public boolean allLast;
        /** 四家副露数（公开信息；对手模型要知道"谁已经鸣开了"）。 */
        public int[] meldCounts = new int[4];

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
            st.kanCount = r.kanCount;
            st.fourKanAbort = r.rules.fourKanAbort;
            st.tilesLeft = r.tilesLeft();
            st.scores = r.scores.clone();
            st.sticks = r.sticks;
            st.kyoku = r.kyoku;
            // 「最后一局」= 场风已到本赛制上限 **且** 是第 4 局。用 `>=` 是因为延长战（南入 / 西入）
            // 的场风会超过上限，那时西 4 局同样是终局（判据同源：RoundScoring.lastWind）。
            st.allLast = r.kyoku == 4 && r.roundWind >= RoundScoring.lastWind(r.rules);
            for (int s = 0; s < 4; s++) {
                st.meldCounts[s] = r.melds[s].size();
            }
            return st;
        }

        /** 自家已经开过的杠数（暗杠 / 加杠 / 大明杠）—— 判"四杠散了会不会被自己打散"。 */
        public int myKans() {
            int n = 0;
            for (Meld m : melds) {
                if (m.kind == Meld.Kind.ANKAN || m.kind == Meld.Kind.KAKAN
                        || m.kind == Meld.Kind.DAIMINKAN) {
                    n++;
                }
            }
            return n;
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
        // 公开信息视图：杠与打牌都要用，先算一次（它只读公开字段，见 HandState.of）
        final HandState st = HandState.of(r, seat);
        // 开杠（暗杠/加杠）：判据见 shouldKan —— 不丢听牌、不后退、危险/弃和/四杠散了都不开
        Map<String, Object> kanOpt = find(options, "kan");
        if (kanOpt != null) {
            Map<String, Object> pick = pickKan(st, kanOpt);
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
     *   <li>进攻：听牌候选取 (良形枚数, 总枚数, 宝牌数, 危险度)；未听牌取 (进张枚数, 宝牌数, 危险度)。
     *       ⚠ 危险度**排在最后**：没人立直时牌效优先（这是刻意的，别把顺序调过来）；</li>
     *   <li>**押し引き**（有人立直 / 鸣き手威胁明显时）：拿"进攻最好的那张"的**期望值**做判断 ——
     *       `P(和了)×和了点 − P(放铳)×平均放铳失点`。期望值超过**顺位门槛**
     *       （{@link #pushThreshold}：1 位守住、4 位抢分）就推；否则改弃和
     *       （弃和＝在"不后退"的一组里挑最安全的，只看**威胁家**的现物/筋）。
     *       这才是"打点高就推、远手小牌就撤"的连续判断，而不是"2 向听以下一律弃和"的开关。</li>
     * </ol>
     *
     * @return 牌码；{@code null} = 没有可用候选
     */
    public static String chooseDiscard(HandState st, List<Object> candidates) {
        // ① 便宜的一遍：向听（1 次 DFS）+ 危险度 + 宝牌。
        //    `HandEval.of` 里有 34 次 DFS，听牌时还要对每个听牌张做一次和了形分解 ——
        //    为 14 张候选各跑一遍会把整场自对弈拖慢 1.7 倍（实测 3.6 → 6.2 秒/场）。
        //    所以先只用便宜的判据圈定"向听最小的那一组"，贵的评估只跑这一组。
        //    ⚠ 危险度这里**一律按进攻口径**（四家取最坏 + 鸣き手威胁抬档，见 dealScore）：
        //    押し引き要先知道"我打算打的那张有多危险"；
        //    真决定弃和时再按**威胁家**口径重算（见 ③）—— 两处口径不同是刻意的，见 Danger 的注释。
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
            final Danger.Report danger =
                    Danger.worst(kind, st.visible, st.rivers, st.riichi, st.turn, st.seat);
            codes[used] = code;
            kinds[used] = kind;
            shantens[used] = sh;
            dangers[used] = dealScore(st, kind, danger);
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
        int[] bestAfter = null;
        HandEval.Snapshot bestSnap = null;
        for (int i = 0; i < used; i++) {
            if (shantens[i] != minSh) {
                continue;
            }
            int[] after = st.counts.clone();
            after[kinds[i]]--;
            HandEval.Snapshot snap = HandEval.of(after, st.melds, st.visible);
            double score = snap.tenpai
                    ? 200 + snap.goodWaitTiles * 4.0 + snap.waitTiles + doras[i] * 3.0
                    : snap.advanceTiles + doras[i] * 3.0;
            score -= dangers[i] * 0.05;              // 同效率时略偏好安全牌
            final boolean better = bestTile == null
                    || score > bestScore + 1e-9
                    || (Math.abs(score - bestScore) <= 1e-9 && dangers[i] < bestDanger)
                    || (Math.abs(score - bestScore) <= 1e-9 && dangers[i] == bestDanger
                        && betterTieBreak(codes[i], bestTile));
            if (better) {
                bestTile = codes[i];
                bestScore = score;
                bestDanger = dangers[i];
                bestAfter = after;
                bestSnap = snap;
            }
        }
        if (bestTile == null) {
            return null;
        }
        // ③ 押し引き（有人立直、或"没人立直但有人鸣开得很明显"时才谈）：
        //    期望值没过**顺位门槛**就弃和，改成挑"对威胁家"最安全的。
        if (st.opponentRiichi() || openThreat(st) >= OPEN_THREAT_GATE) {
            final int needTiles = bestSnap != null && bestSnap.tenpai
                    ? bestSnap.waitTiles : (bestSnap == null ? 0 : bestSnap.advanceTiles);
            final int turnsLeft = Math.max(1, st.tilesLeft / 4);
            final double winP = winProbability(needTiles, st.tilesLeft, turnsLeft, minSh + 1);
            final int winPts = hanToPoints(estimatedHan(st, bestAfter, st.melds));
            final double dealP = dealProbability(bestDanger);
            final double ev = pushEv(winP, winPts, dealP);
            final double threshold = pushThreshold(st);
            final boolean push = ev > threshold;
            if (push != (ev > 0)) {
                debugPlacementFlipCount++;               // 顺位门槛真的改了结论（红证盯这一条）
            }
            if (push) {
                debugPushCount++;
            } else {
                debugFoldCount++;
                if (!st.opponentRiichi()) {
                    debugOpenFoldCount++;                // 对手模型那条闸门（档 C）
                }
                return safestAgainstThreats(st, codes, kinds, shantens, minSh, used);
            }
        }
        return bestTile;
    }

    /**
     * 弃和时打哪张：在"不增加向听"的一组里挑对**威胁家**最安全的（现物 → 筋/壁 → 无信息）。
     *
     * <p>⚠ 威胁家 **立直家优先**（{@link Danger#worstAgainstRiichi}）：没人立直的对手"手里是什么样"
     * 无从判断，把四家都算进来会让每张牌一样危险、把现物的价值淹掉。
     * 档 C 起：**一个立直家都没有、但有鸣き手威胁**（{@link #openThreat} 过线）时，
     * 把那些"已经鸣开"的几家**当作疑似听牌家**（口径与 {@link #dealScore} 的抬档一致 ——
     * 现物/筋/壁照样成立，只是"完全无信息"那一档按危险处理），这样弃和才挑得出安全牌。
     */
    private static String safestAgainstThreats(HandState st, String[] codes, int[] kinds,
                                               int[] shantens, int minSh, int used) {
        final boolean[] threats = threatSeats(st);
        boolean any = false;
        for (boolean b : threats) {
            any |= b;
        }
        String best = null;
        int bestDanger = Integer.MAX_VALUE;
        for (int i = 0; i < used; i++) {
            if (shantens[i] != minSh) {
                continue;
            }
            final int d = any ? threatScore(st, kinds[i], threats)
                              : Danger.worstAgainstRiichi(kinds[i], st.visible, st.rivers,
                                            st.riichi, st.turn, st.seat).score;
            if (best == null || d < bestDanger
                    || (d == bestDanger && betterTieBreak(codes[i], best))) {
                best = codes[i];
                bestDanger = d;
            }
        }
        return best;
    }

    /**
     * 对**威胁家**的最大危险度分（威胁家都不是现物时才是 0）。
     *
     * <p>判据与"把威胁家当作已立直"同源：{@link Danger#of} 里现物 > 筋/壁 > 无信息（立直家才是 DANGEROUS），
     * 所以这里传 {@code theirRiichi = true} —— 对"疑似听牌"的鸣き手，无信息的牌就该按危险对待。
     */
    private static int threatScore(HandState st, int kind, boolean[] threats) {
        int worst = 0;
        for (int s = 0; s < 4; s++) {
            if (!threats[s]) {
                continue;
            }
            worst = Math.max(worst, Danger.of(kind, st.visible, st.rivers[s], true, st.turn).score);
        }
        return worst;
    }

    private static boolean betterTieBreak(String a, String b) {
        if (b == null) {
            return true;
        }
        return isolateScore(b) < isolateScore(a);
    }

    // ================================================================= 打点粗估 / 押し引き

    /** 放铳的**平均失点**（本作口径；只在期望值比较里用，不影响任何规则判定）。 */
    public static final int AVG_DEAL_POINTS = 5200;

    /**
     * 打点**粗估**（番数）—— 押し引き与副露取舍要的是"量级对不对"，不是精确番数。
     *
     * <p>为什么不直接用 {@code Round.scoreIfWin}：① 它要在"假设已经鸣牌"的前提下重算
     * （真实的 `melds[seat]` / `menzen[seat]` 都还没变，照抄会白算平和/门清符/一杯口 ——
     * 两把尺子）；② 押し引き是**每巡**都要做的判断，一次 Evaluator 太贵。所以这里只用
     * 公开信息数形状：
     * <ul>
     *   <li>宝牌（含自己手里的赤五）；</li>
     *   <li>立直（门清且还没立直时 +1：本作策略默认会立直）；</li>
     *   <li>役牌刻（三元牌/场风/自风，每种 1 番，可以复合）；</li>
     *   <li>断幺九（只在食断规则下）；</li>
     *   <li>混一色 / 清一色（按含不含字牌分，副露降一番）；</li>
     *   <li>对对和（没有吃、且暗牌里至少两组对子/刻子）。</li>
     * </ul>
     * ⚠ 精确番数仍然只在两处用真货：立直/默听（`shouldDeclareRiichi` 用 `scoreIfWin`）
     * 与训练侧的观测 —— 这里只服务"值不值得"这种粗判断。
     */
    public static int estimatedHan(HandState st, int[] counts, List<Meld> melds) {
        final int[] c = counts == null ? new int[Tiles.KIND_COUNT] : counts;
        final List<Meld> ms = melds == null ? List.of() : melds;
        final boolean open = !ms.isEmpty();
        int han = HandEval.doraCount(c, ms, st.doraIndicators) + st.akaInHand;
        if (!open && !st.selfRiichi) {
            han += 1;                                   // 立直
        }
        for (int k = 27; k < Tiles.KIND_COUNT; k++) {
            if (isYakuhai(st, k) && hasTriplet(c, ms, k)) {
                han += 1;
            }
        }
        if (st.kuitan && allSimplesOf(c, ms)) {
            han += 1;
        }
        if (singleSuitOf(c, ms) >= 0) {
            han += hasHonor(c, ms) ? (open ? 2 : 3) : (open ? 5 : 6);
        }
        if (noChi(ms)) {
            // 对对和的**粗判**：没有吃、且暗牌里一张"单张"都没有（全是刻子/对子）。
            // ⚠ 宁可漏算：真实的对对和途中多半还留着搭子的残张，那种形状这里不认
            //   （认了会把"混一色 + 顺子苗头"的手白算成对对和 —— 试过，虚高得很离谱）。
            int pairs = 0;
            boolean single = false;
            for (int k = 0; k < Tiles.KIND_COUNT; k++) {
                if (c[k] == 1) {
                    single = true;
                } else if (c[k] >= 2) {
                    pairs++;
                }
            }
            if (!single && pairs >= 2) {
                han += 2;                               // 对对和
            }
        }
        return han;
    }

    /** 番数 → 闲家荣和的实收点数（粗表：够期望值用；庄家/自摸的差别不在这里体现）。 */
    public static int hanToPoints(int han) {
        if (han >= 13) {
            return 32000;
        }
        if (han >= 11) {
            return 24000;
        }
        if (han >= 8) {
            return 16000;
        }
        if (han >= 6) {
            return 12000;
        }
        if (han >= 5) {
            return 8000;
        }
        if (han >= 4) {
            return 7700;
        }
        if (han >= 3) {
            return 3900;
        }
        if (han >= 2) {
            return 2000;
        }
        return han >= 1 ? 1000 : 0;
    }

    /**
     * 和了概率的**粗模型**：先按"每巡命中率 = 想要的牌 / 牌山剩余"算"这么多巡里至少命中一次"，
     * 再按**还差几步**（向听 + 1）打个折 —— 远手推不动，就是靠这一步。
     *
     * @param needTiles  想要的牌的张数（听牌 = 听牌枚数；未听牌 = 进张枚数）
     * @param tilesLeft  牌山剩余可摸张数
     * @param turnsLeft  **自己**还能摸几巡（≈ 牌山剩余 / 4）
     * @param steps      还差几步到和了（听牌 = 1、1 向听 = 2 …）
     */
    public static double winProbability(int needTiles, int tilesLeft, int turnsLeft, int steps) {
        if (needTiles <= 0 || tilesLeft <= 0 || turnsLeft <= 0 || steps <= 0) {
            return 0;
        }
        final double perTurn = Math.min(0.5, (double) needTiles / tilesLeft);
        final double p = (1 - Math.pow(1 - perTurn, turnsLeft)) / steps;
        return Math.max(0, Math.min(0.9, p));
    }

    /**
     * 放铳概率的**粗模型**：由 {@link Danger} 的 0..100 分数线性映射到 0..0.30。
     *
     * <p>现物 → 0（危险度 0 分是硬保证）；筋/壁 ≈ 0.04；无信息 ≈ 0.09；
     * 立直家的无筋中张 ≈ 0.20 起（巡目越深分越高）。
     */
    public static double dealProbability(int dangerScore) {
        return Math.min(0.30, Math.max(0, dangerScore) / 100.0 * 0.30);
    }

    /** 押し引き的期望值（点）：`P(和了)×和了点 − P(放铳)×平均放铳失点`。正 = 值得推。 */
    public static double pushEv(double winProb, int winPoints, double dealProb) {
        return winProb * winPoints - dealProb * AVG_DEAL_POINTS;
    }

    /** 这一手推不推（期望值为正才推）。弃和按 0 处理：弃和之后仍有被自摸/罚符的小额支出。 */
    public static boolean shouldPush(double winProb, int winPoints, double dealProb) {
        return shouldPush(winProb, winPoints, dealProb, 0);
    }

    /**
     * 带**顺位门槛**的押し引き：期望值要超过 {@code threshold} 才推（{@code threshold = 0} 时就是"期望为正"）。
     *
     * <p>门槛由 {@link #pushThreshold} 给 —— 与本方法分开是为了让"顺位怎么影响取舍"能单独断言，
     * 而不是把两条判据揉进一个 if 里。
     */
    public static boolean shouldPush(double winProb, int winPoints, double dealProb,
                                     double threshold) {
        return pushEv(winProb, winPoints, dealProb) > threshold;
    }

    // ================================================================= 顺位与终局（档 C）

    /**
     * 顺位偏置（点）：1 位..4 位各给押し引き的期望值**门槛**加多少。
     *
     * <p>为什么要有它：期望值本身只算"这一手值不值"，**完全不管着顺位**。而着顺点是
     * 非对称的（1 位与 2 位的马点差最大、ラス目与 3 位次之），所以：
     * <ul>
     *   <li>1 位（{@code +800}）＝**守**：领先时"多赢 1000"远不如"别把 5200 送出去"值钱；</li>
     *   <li>4 位（{@code -1000}）＝**抢**：不推就输定了，期望值略负也该上；</li>
     *   <li>2 / 3 位（{@code +200 / -300}）＝几乎中立，只留一点"2 位比 3 位值钱"的梯度。</li>
     * </ul>
     *
     * <p>⚠ 这是一张**启发式**表（本作口径），不是规则：规则层只认"终局精算按马点算顺位点"，
     * 而"领先该收多少手"没有唯一答案。它喂给训练当标签，所以显式写在这里、可断言、可替换。
     */
    private static final double[] PLACEMENT_BIAS = {800, 200, -300, -1000};

    /** 终局（オーラス）把顺位偏置**放大**：守领先 / 抢点数都更极端。 */
    public static final double ALL_LAST_SCALE = 2.0;

    /** 鸣き手威胁（{@link #openThreat}）到多少就按"疑似听牌"权衡押し引き。 */
    public static final double OPEN_THREAT_GATE = 0.6;

    /** 见逃至少要还剩这么多张可摸（≈ 4 巡）：没时间把"更大的和了"摸回来就别赌。 */
    public static final int RON_DECLINE_MIN_TILES = 16;

    /**
     * 顺位（1 = 1 位 … 4 = 4 位）。
     *
     * <p>同点按**座次**：本服务端 seat 0 = 起家，所以座次小的在前 —— 与
     * {@code RoundScoring.settle}（"同点按起家座次先后定名次"）和 {@code endGameSticks}
     * （"尾数归更接近起家者"）是**同一把尺子**（自检逐座位对拍 `settle().rank`）。
     *
     * @return 1..4；{@code scores} 为 null / 长度不足（= **点数未知**）时返回 0
     */
    public static int placementOf(int[] scores, int seat) {
        if (scores == null || scores.length < 4 || seat < 0 || seat > 3) {
            return 0;
        }
        int place = 1;
        for (int s = 0; s < 4; s++) {
            if (s == seat) {
                continue;
            }
            if (scores[s] > scores[seat] || (scores[s] == scores[seat] && s < seat)) {
                place++;
            }
        }
        return place;
    }

    /**
     * 押し引き的**门槛**（点）：期望值必须超过它才推（取值表见 {@link #PLACEMENT_BIAS}）。
     *
     * <p>点数未知（{@code st.scores == null}）时返回 0 —— 不看顺位。这条对**自检**很重要：
     * 纯构造的局面不带点数，隐式给一个顺位会污染"形状类"的老断言。
     */
    public static double pushThreshold(HandState st) {
        final int place = placementOf(st.scores, st.seat);
        if (place == 0) {
            return 0;
        }
        final double v = PLACEMENT_BIAS[place - 1];
        return st.allLast ? v * ALL_LAST_SCALE : v;
    }

    // ------------------------------------------------------------- 对手模型（只吃公开信息）

    /**
     * 鸣き手威胁：**未立直、但已经鸣开的那几家**有多像已经听牌（0..1，取最大）。
     *
     * <p>为什么要有它：押し引き原来只在 `opponentRiichi()` 时才谈，于是"对面碰了两副、
     * 巡目都过半了"这种局面照样无脑推 —— 而明牌副露是**最公开**的听牌信号。
     * 权重（本作口径，纯启发式）：
     * <ul>
     *   <li>第一副露 {@code +0.25}（鸣一副可能只是碰运气，不够说明听牌）；</li>
     *   <li>每多一副 {@code +0.20}（两副露几乎必有役有型）；</li>
     *   <li>巡目 {@code +0.03 × turn}（打得越深越可能已经听牌），上限 {@code 0.9}。</li>
     * </ul>
     * 于是：2 副露在中终盘（turn ≥ 7）就过 {@link #OPEN_THREAT_GATE}，1 副露要到终盘才过。
     */
    public static double openThreat(HandState st) {
        if (st.meldCounts == null) {
            return 0;
        }
        double best = 0;
        for (int s = 0; s < 4; s++) {
            if (s == st.seat || st.riichi[s] || st.meldCounts[s] <= 0) {
                continue;
            }
            final double w = 0.25 + 0.20 * (st.meldCounts[s] - 1) + 0.03 * st.turn;
            best = Math.max(best, Math.min(0.9, w));
        }
        return best;
    }

    /**
     * 押し引き该盯哪几家：**立直家优先**；一个立直家都没有时，退而把"鸣き手威胁"的几家当威胁。
     *
     * <p>返回四家布尔表（自己永远是 false）。这条判据被两处共用：弃和选牌（{@link #threatScore}）
     * 与危险度抬档（{@link #dealScore}）—— 各写一份就会漂（"弃和按 A 家算、期望值按 B 家算"）。
     */
    public static boolean[] threatSeats(HandState st) {
        final boolean[] out = new boolean[4];
        boolean any = false;
        for (int s = 0; s < 4; s++) {
            if (s != st.seat && st.riichi[s]) {
                out[s] = true;
                any = true;
            }
        }
        if (any || st.meldCounts == null) {
            return out;
        }
        for (int s = 0; s < 4; s++) {
            if (s != st.seat && st.meldCounts[s] > 0) {
                out[s] = true;
            }
        }
        return out;
    }

    /** {@link #dealScore(HandState, int, Danger.Report)}：自己去算基准危险度的那一版。 */
    public static int dealScore(HandState st, int kind) {
        return dealScore(st, kind,
                Danger.worst(kind, st.visible, st.rivers, st.riichi, st.turn, st.seat));
    }

    /**
     * 打这张牌的**放铳风险分**（0..100）：立直家照旧，未立直但鸣开了的按威胁权重**抬档**。
     *
     * <p>做法是把那几家"当作已经立直"再算一次危险度（{@link Danger#of} 传
     * {@code theirRiichi = true}）：现物 / 筋 / 壁 照样成立（它们与立直无关），
     * 只有"完全无信息"那一档被抬到 DANGEROUS，然后按 {@link #openThreat} 的权重在两者之间插值。
     * 这样"对面鸣了两副但我手里有他的现物"仍然敢打现物 —— 抬档**不会**破坏现物的硬保证。
     */
    public static int dealScore(HandState st, int kind, Danger.Report base) {
        if (base == null || base.level >= Danger.DANGEROUS || st.meldCounts == null) {
            return base == null ? 0 : base.score;
        }
        final double t = openThreat(st);
        if (t <= 0) {
            return base.score;
        }
        int asIfRiichi = 0;
        for (int s = 0; s < 4; s++) {
            if (s == st.seat || st.riichi[s] || st.meldCounts[s] <= 0) {
                continue;
            }
            asIfRiichi = Math.max(asIfRiichi,
                    Danger.of(kind, st.visible, st.rivers[s], true, st.turn).score);
        }
        final int blended = (int) Math.round(base.score + t * (asIfRiichi - base.score));
        return Math.max(base.score, blended);           // 抬档只许往上，绝不让某张牌"变安全"
    }

    // ------------------------------------------------------------- 终局见逃

    /**
     * **终局见逃**（档 C）：要不要放弃这次荣和，去赌一个更大的和了。
     *
     * <p>为什么只在终局谈：别的局和了就继续打，见逃纯亏（还白白背上同巡振听）。终局才可能出现
     * "这一手和了下去就定死名次"的局面。判据全部满足才见逃：
     * <ol>
     *   <li>{@code st.allLast}：最后一局（含南入 / 西入的延长战）；</li>
     *   <li>{@code !st.selfRiichi}：**立直见逃 = 立直振听**，之后只能靠自摸，代价太大 —— 一律不和；</li>
     *   <li>这次荣和**抬不动顺位**（和完之后的名次与现在一样）；</li>
     *   <li>**自摸抬得动**（自摸点数更高，能多上一个名次）—— 这才是见逃的收益；</li>
     *   <li>还剩得下 {@link #RON_DECLINE_MIN_TILES} 张可摸 —— 没时间摸回来就不赌。</li>
     * </ol>
     *
     * <p>点数用**生产同一套**结算（{@code Evaluator} + {@link Payments}，含本场棒与供託），
     * 不是粗表：见逃是个"差 100 点就翻结论"的判断（自检里那两条用例正是靠 100 点之差分开的）。
     * 荣和的进账与"谁放铳"无关（单人付款），所以取一个名义放铳者即可；
     * 别家的点数按不动处理（自己加、别人不减）—— 顺位比较只需要自己的点，保守估计足够。
     *
     * <p>⚠ 口径刻意保守：只认"ロンでは届かないがツモなら届く"这一种收益，
     * **不**做"见逃去狙満貫/役満"的打点升级（那种判断要估"手还能不能长大"，便宜的近似会亏得多）。
     */
    public static boolean shouldDeclineRon(Round r, int seat, HandState st, int winKind) {
        if (r == null || st == null || !st.allLast || st.selfRiichi || winKind < 0
                || winKind >= Tiles.KIND_COUNT) {
            return false;
        }
        final int[] scores = st.scores;
        if (scores == null || scores.length < 4 || st.tilesLeft < RON_DECLINE_MIN_TILES) {
            return false;
        }
        final Evaluator.HandScore ron = r.scoreIfWin(seat, winKind, false, false);
        if (ron == null) {
            return false;                                // 不能和（不该走到这里）→ 照和
        }
        // 自摸：这一次询问手上是 13 张，把和了牌并进去才是 `tsumo = true` 要的 14 张形态
        final int[] withWin = st.counts.clone();
        withWin[winKind]++;
        final Evaluator.HandScore tsumo =
                r.scoreIfWin(seat, withWin, winKind, true, false, st.akaInHand);
        final int ronGain = winGain(r, seat, ron, false);
        final int tsumoGain = tsumo == null ? 0 : winGain(r, seat, tsumo, true);
        final int now = placementOf(scores, seat);
        final int afterRon = placementOf(gainAt(scores, seat, ronGain), seat);
        final int afterTsumo = placementOf(gainAt(scores, seat, tsumoGain), seat);
        if (afterRon < now || afterTsumo >= afterRon) {
            return false;                                // 荣和能抬顺位 / 自摸也抬不动 → 照和
        }
        debugRonDeclineCount++;
        return true;
    }

    private static int[] gainAt(int[] scores, int seat, int gain) {
        final int[] out = scores.clone();
        out[seat] += gain;
        return out;
    }

    /**
     * 和了这一手**自己**能进多少点（含本场棒与供託）——走生产的 {@link Payments}。
     *
     * <p>荣和时和牌者的进账与"谁放铳"无关（单人付款、且倍数只看和牌者是不是庄家），
     * 所以放铳者取一个名义上的下一家即可（包牌的分摊这里不还原：包牌是公开账，
     * 但见逃判断看的是自己的进账，包牌只影响"谁出钱"）。
     */
    private static int winGain(Round r, int seat, Evaluator.HandScore sc, boolean tsumo) {
        final int loser = tsumo ? -1 : (seat + 1) % 4;
        final Payments.Result pay = Payments.compute(sc, seat, loser, r.dealer, r.honba, r.sticks,
                tsumo, Payments.NO_PAO);
        return pay.winnerGain;
    }

    private static boolean hasTriplet(int[] counts, List<Meld> melds, int kind) {
        if (counts[kind] >= 3) {
            return true;
        }
        for (Meld m : melds) {
            if (!m.isRun() && m.baseKind() == kind) {
                return true;
            }
        }
        return false;
    }

    private static boolean hasHonor(int[] counts, List<Meld> melds) {
        for (int k = 27; k < Tiles.KIND_COUNT; k++) {
            if (counts[k] > 0) {
                return true;
            }
        }
        for (Meld m : melds) {
            for (int t : m.tiles) {
                if (Tiles.kind(t) >= 27) {
                    return true;
                }
            }
        }
        return false;
    }

    private static boolean noChi(List<Meld> melds) {
        for (Meld m : melds) {
            if (m.kind == Meld.Kind.CHI) {
                return false;
            }
        }
        return true;
    }

    /** 暗牌 + 副露**全是中张（2..8）**—— 断幺九的前提（对任意"假设的"手牌都能问）。 */
    private static boolean allSimplesOf(int[] counts, List<Meld> melds) {
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (counts[k] > 0 && !Tiles.isSimple(k)) {
                return false;
            }
        }
        for (Meld m : melds) {
            for (int t : m.tiles) {
                if (!Tiles.isSimple(Tiles.kind(t))) {
                    return false;
                }
            }
        }
        return true;
    }

    /** 暗牌 + 副露只占**一种花色**（可含字牌）时返回该花色，否则 -1。 */
    private static int singleSuitOf(int[] counts, List<Meld> melds) {
        int suit = -1;
        for (int k = 0; k < 27; k++) {
            if (counts[k] > 0) {
                int s = Tiles.suit(k);
                if (suit >= 0 && s != suit) {
                    return -1;
                }
                suit = s;
            }
        }
        for (Meld m : melds) {
            for (int t : m.tiles) {
                int k = Tiles.kind(t);
                if (k >= 27) {
                    continue;
                }
                int s = Tiles.suit(k);
                if (suit >= 0 && s != suit) {
                    return -1;
                }
                suit = s;
            }
        }
        return suit;
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
        // 公开信息视图：荣和（见逃）与鸣牌都要用，先算一次
        final HandState st = HandState.of(r, seat);
        final int kind = extra == null ? -1 : Tiles.parseKind(Json.str(extra, "tile", ""));
        // 和牌永远优先 —— 唯一的例外是**终局见逃**（档 C：这一手荣和抬不动顺位、自摸才抬得动）。
        // 见逃之后**继续往下走**：碰/吃照常判（"见逃了但把这手留下继续打"是同一件事的两面）。
        Map<String, Object> ron = find(options, "ron");
        if (ron != null && (kind < 0 || !shouldDeclineRon(r, seat, st, kind))) {
            return Json.obj("type", "ron");
        }
        // 自检专用：能大明杠就杠（平时按下面的判据走，见 shouldKan / hasYakuPlan）
        if (debugAlwaysKan && find(options, "kan") != null) {
            return Json.obj("type", "kan");
        }
        final int cur = HandEval.shanten(st.counts, st.meldCount);
        if (kind < 0) {
            return Json.obj("type", "pass");
        }
        // 有人立直时，除非鸣完立刻是"役牌"这种硬役，否则不跟着上 —— 鸣牌会把牌打薄、还失去门清
        final boolean underPressure = st.opponentRiichiCount() >= 1;
        // 门清 vs 已经鸣过牌：门清鸣牌的代价（放弃立直/门清）要算进价值比较，见 callWorthForMenzen
        final boolean menzenBefore = st.meldCount == 0;

        // 大明杠：手里已有 3 张，所以它**不像碰那样能改善向听**（暗刻本来就按面子算）。
        // 取舍因此落在别处：暗刻的符、三暗刻/四暗刻、门清（立直/平和）都比"杠宝牌 + 少一张牌山"值钱，
        // 而杠宝牌是**对四家都翻开**的。所以本作口径：**门清手不大明杠**（宁可漏掉一些其实有利的），
        // 已经鸣过牌的手门清早就断了 → 只要还有役、不违两条硬闸门、向听不倒退就杠。
        Map<String, Object> kan = find(options, "kan");
        if (kan != null && st.counts[kind] >= 3) {
            int[] c = st.counts.clone();
            c[kind] -= 3;
            Meld meld = new Meld(Meld.Kind.DAIMINKAN,
                    new int[]{Tiles.id(kind, 1), Tiles.id(kind, 2), Tiles.id(kind, 3), Tiles.id(kind, 0)},
                    -1, Tiles.id(kind, 0));
            final boolean open = st.meldCount > 0;
            final boolean yaku = hasYakuPlan(st, c, meld);
            final boolean noRegress = HandEval.shanten(c, st.meldCount + 1) <= cur;
            if (open && !yaku) {
                debugNoYakuRefuseCount++;
            }
            if (open && yaku && noRegress && !kanGuardRefuse(st)) {
                debugKanCount++;
                return firstKan(kan);
            }
        }

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
                if (menzenBefore && !callWorthForMenzen(st, c, meld)) {
                    debugCallValueRefuseCount++;         // 门清 + 鸣了不值 → 不鸣
                } else {
                    return Json.obj("type", "pon");
                }
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
                    if (menzenBefore && !callWorthForMenzen(st, c, meld)) {
                        debugCallValueRefuseCount++;     // 门清 + 吃下去不值 → 不吃
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

    /**
     * **门清手值不值得副露** —— 档 B 的"副露打分"（粗估，只用公开信息）。
     *
     * <p>门清鸣牌的代价是**放弃立直**（+1 番 + 里宝期望 + 门清荣和符，加起来约 1 番多），
     * 所以只在这两种情况鸣：
     * <ol>
     *   <li>这一鸣**直接把向听打到 0（听牌）** —— 速度换得值；</li>
     *   <li>鸣完这手**至少 2 番** —— 有得赚。</li>
     * </ol>
     * 两条都不满足就不鸣（留着门清做立直）。⚠ 已经鸣过牌的手门清早就断了，不适用这条 ——
     * 那种情况只要"还有役 + 向听改善"就继续鸣（`decideClaim` 里的原判据）。
     */
    public static boolean callWorthForMenzen(HandState st, int[] afterCall, Meld meld) {
        if (bestShantenAfterCall(afterCall, st.meldCount + 1, 13 - 3 * st.meldCount - 1) == 0) {
            return true;                                 // 鸣完直接听牌
        }
        List<Meld> ms = new ArrayList<>(st.melds);
        ms.add(meld);
        return estimatedHan(st, afterCall, ms) >= 2;
    }

    /**
     * 鸣牌后（暗牌 {@code countsAfterCall}，还要再打一张）能达到的最好向听。
     *
     * <p>公开给自检：副露取舍（"值不值得鸣"）与"鸣完有没有改善"共用它，判据错了整条路都错。
     */
    public static int bestShantenAfterCall(int[] countsAfterCall, int meldCount, int handSize) {
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
        // 碰 / 大明杠 / 加杠 / 暗杠都是"刻子级"的面子；只有吃不是。
        // ⚠ 原来写的是 `kind == PON`：大明杠走这条判据时**役牌那一支会静默失效**。
        final boolean isTriplet = meld.kind != Meld.Kind.CHI;
        // ① 役牌：碰役牌立刻成刻；或者暗牌里已有役牌的暗刻
        if (isTriplet && isYakuhai(st, calledKind)) {
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
        if (isTriplet) {
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

    /** 暗牌 + 副露全是中张（2..8）—— 断幺九的前提（把"这次要鸣的面子"也算进去）。 */
    private static boolean allSimples(HandState st, int[] afterCall, Meld meld) {
        List<Meld> ms = new ArrayList<>(st.melds);
        ms.add(meld);
        return allSimplesOf(afterCall, ms);
    }

    /** 暗牌 + 副露只占一种花色（外加字牌）—— 混一色/清一色的前提。 */
    private static boolean singleSuit(HandState st, int[] afterCall, Meld meld) {
        List<Meld> ms = new ArrayList<>(st.melds);
        ms.add(meld);
        return singleSuitOf(afterCall, ms) >= 0;
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

    /**
     * 从下发的杠选项里挑一个**该开**的（自家回合：暗杠 / 加杠）；都不该开返回 {@code null}。
     *
     * <p>顺序按服务端给的列表（暗杠在前、加杠在后，见 `Round.turnOptions`）——
     * 先来的先判，判据本身与顺序无关。
     */
    private static Map<String, Object> pickKan(HandState st, Map<String, Object> kanOption) {
        if (debugAlwaysKan) {
            return firstKan(kanOption);          // 自检专用：强制开杠（岭上那条路径）
        }
        List<Object> kans = Json.list(kanOption, "kans");
        if (kans == null) {
            return null;
        }
        for (Object o : kans) {
            if (!(o instanceof Map)) {
                continue;
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> m = (Map<String, Object>) o;
            final String kindStr = String.valueOf(m.get("kind"));
            final String tileStr = String.valueOf(m.get("tile"));
            final int kind = Tiles.parseKind(tileStr);
            if (kind < 0 || !shouldKan(st, kindStr, kind)) {
                continue;
            }
            return Json.obj("type", "kan", "kind", kindStr, "tile", tileStr);
        }
        return null;
    }

    /**
     * **该不该开这个杠**（暗杠 / 加杠）—— teacher 的第四条取舍，公开信息纯函数。
     *
     * <p>四道闸门，任何一条不过就不开（顺序：先"无论如何都不开"，再看代价）：
     * <ol>
     *   <li><b>弃和中不开</b>：有人立直且自己 ≥2 向听时本来就在弃和 —— 开杠等于给全场翻杠宝牌、
     *       还多摸一张未知牌，与弃和的目的相反；</li>
     *   <li><b>别把本局打散</b>：`four_kan_abort` 规则下，本局已有 3 个杠、且**不全是自己开的**
     *       时第 4 个杠会触发**四杠散了**（强制流局）—— 白白扔掉一手能和的牌；</li>
     *   <li><b>加杠先看会不会被抢</b>：加杠的那张牌对他家是**无筋中张**（`Danger.DANGEROUS`）时，
     *       被抢杠就是直接放铳 —— 不开（现物/筋壁照开，因为抢杠的前提是"他正好听这张"）；</li>
     *   <li><b>杠完不能丢听牌、也不能倒退</b>：把 4 张（加杠 1 张）拿走、面子数 +1 之后，
     *       向听数不能变大。这一条同时挡住两类坏杠：拆掉听牌的形、
     *       以及把七对子的两对拆没（`Shanten.min` 含七对子/国士，所以口径一致）。</li>
     * </ol>
     *
     * <p>⚠ 这个函数**不看**"杠宝牌会便宜别人"这种全局权衡，也不看一发/里宝期望 ——
     * 那属于押し引き数值化，档 B 的事（`docs/DESIGN.md`「teacher」）。
     *
     * @param kanKind {@code "ankan"} / {@code "kakan"}（服务端下发的 kind 原样传进来）
     */
    public static boolean shouldKan(HandState st, String kanKind, int kind) {
        if (st == null || kind < 0 || Tiles.KIND_COUNT <= kind) {
            return false;
        }
        final boolean kakan = !"ankan".equals(kanKind);
        final int cur = HandEval.shanten(st.counts, st.meldCount);
        if (st.opponentRiichi() && cur >= 2) {
            debugKanRefusePressure++;
            return false;
        }
        if (st.fourKanAbort && st.kanCount == 3 && st.myKans() < st.kanCount) {
            debugKanRefuseFourKan++;
            return false;
        }
        if (kakan) {
            Danger.Report d = Danger.worst(kind, st.visible, st.rivers, st.riichi, st.turn, st.seat);
            if (d.level >= Danger.DANGEROUS) {
                debugKanRefuseDanger++;
                return false;
            }
        }
        int[] after = st.counts.clone();
        after[kind] -= Math.min(kakan ? 1 : 4, after[kind]);
        final int afterMelds = kakan ? st.meldCount : st.meldCount + 1;
        if (HandEval.shanten(after, afterMelds) > cur) {
            debugKanRefuseWait++;
            return false;
        }
        debugKanCount++;
        return true;
    }

    /** 两条"无论如何都别开"的闸门（弃和中 / 第 4 个杠会四杠散了）—— 大明杠也用同一把尺子。 */
    private static boolean kanGuardRefuse(HandState st) {
        if (st.opponentRiichi() && HandEval.shanten(st.counts, st.meldCount) >= 2) {
            debugKanRefusePressure++;
            return true;
        }
        if (st.fourKanAbort && st.kanCount == 3 && st.myKans() < st.kanCount) {
            debugKanRefuseFourKan++;
            return true;
        }
        return false;
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
