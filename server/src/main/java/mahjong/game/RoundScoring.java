package mahjong.game;

import java.util.Collection;

import mahjong.core.Rules;
import mahjong.rules.Agari;

/**
 * 一局结束后的**连庄判据**（纯函数，可单独单测）。
 *
 * <p>从 {@link Round} 抽出来的第四步。连庄规则原本散在**五个**地方各写一行：
 *
 * <pre>
 *   和了       r.dealerRenchan = seat == dealer
 *   多家和了   r.dealerRenchan = winners.contains(dealer)
 *   流局满贯   r.dealerRenchan = nagashi.contains(dealer)
 *   荒牌流局   r.dealerRenchan = tenpai[dealer]
 *   途中流局   r.dealerRenchan = true
 * </pre>
 *
 * <p>摊成这样，任何一处写错（例如把荒牌流局误写成"庄家没听也连庄"）
 * 都只会表现为"分数算错但不报错"，极难发现。现在四条规则集中在这里，
 * 调用方只表达"本局是怎么结束的"，不再自己推导结论。
 */
public final class RoundScoring {

    // ---- 自测用调用计数：证明这些判据确实在实局路径上被调用，而不是死代码 ----
    static int callsRenchan;
    static int callsNagashiEligible;
    static int callsNagashiPay;
    static int callsTenpai;
    static int callsEndSticks;
    static int callsAgariyame;

    private RoundScoring() {
    }

    /** 自测：清零计数。 */
    public static void debugResetCounts() {
        callsRenchan = 0;
        callsNagashiEligible = 0;
        callsNagashiPay = 0;
        callsTenpai = 0;
        callsEndSticks = 0;
        callsAgariyame = 0;
    }

    /** 自测：读计数（连庄 / 满贯成立 / 满贯支付 / 形式听牌 / 终局余棒分配 / 和了止判据）。 */
    public static String debugCallCounts() {
        return "renchan=" + callsRenchan + " nagashiEligible=" + callsNagashiEligible
                + " nagashiPay=" + callsNagashiPay + " tenpai=" + callsTenpai
                + " endSticks=" + callsEndSticks + " agariyame=" + callsAgariyame;
    }

    /** 自测：计数总和。 */
    public static int debugTotalCalls() {
        return callsRenchan + callsNagashiEligible + callsNagashiPay + callsTenpai
                + callsEndSticks + callsAgariyame;
    }

    /** 单人荣和 / 自摸：庄家和了才连庄。 */
    public static boolean winBy(int dealer, int winnerSeat) {
        callsRenchan++;
        return winnerSeat == dealer;
    }

    /** 多家和了（双响及以上）：和了者里含庄家就连庄。 */
    public static boolean winBy(int dealer, Collection<Integer> winners) {
        callsRenchan++;
        return winners != null && winners.contains(dealer);
    }

    /** 流局满贯：成立者里含庄家就连庄。 */
    public static boolean nagashiBy(int dealer, Collection<Integer> nagashi) {
        callsRenchan++;
        return nagashi != null && nagashi.contains(dealer);
    }

    /** 荒牌流局：**庄家听牌**才连庄（不听则轮庄）。 */
    public static boolean exhaustiveBy(int dealer, boolean[] tenpai) {
        callsRenchan++;
        return tenpai != null && dealer >= 0 && dealer < tenpai.length && tenpai[dealer];
    }

    /**
     * 下一局的**本场数**。
     *
     * <p>规则（`docs/日本麻将.md` §连庄 / §流局 / §和牌）：
     * <ul>
     *   <li><b>连庄一律 +1</b>：庄家和了、以及**四种中途流局**（九种九牌 / 四风连打 / 四家立直 / 四杠散了）
     *       —— 中途流局虽然「本场不增加」的说法常被误传，原文 L534 明写「中途流局…本场数增加」；</li>
     *   <li><b>流局后轮庄也 +1</b>：荒牌流局庄家不听 → 轮庄，但原文 L110/L623 明写「流局时即使庄家不听而
     *       轮庄，本场数也会增加」「流局轮庄时本场数仍会增加」；</li>
     *   <li><b>只有「闲家和了轮庄」才清零</b>（流局满贯按"和了"处理，见下面 nagashi 的注释）。</li>
     * </ul>
     *
     * <p>⚠ 这条判据原来散在 `Table.playGame` 里内联写着，而且是**错的**（审计 S-46）：
     * 中途流局不加本场、荒牌流局庄家不听时把本场**清零** —— 默认 M.League 预设下每出现一次流局，
     * 之后所有和牌都会少算 300 点/本场（自摸 100/家）。抽成纯函数是为了能直接把真值表钉住。
     */
    public static int nextHonba(int honba, boolean dealerRenchan, boolean agari, boolean nagashi) {
        callsRenchan++;
        if (dealerRenchan) {
            return honba + 1;
        }
        if (agari) {
            return 0;                     // 闲家和了 → 轮庄且清零
        }
        if (nagashi) {
            return 0;                     // 流局满贯：按"和了"处理本场（连庄口径见 AUDIT S-55）
        }
        return honba + 1;                 // 荒牌流局庄家不听 → 本场 +1 后再轮庄
    }

    /**
     * 途中流局（九种九牌 / 四风连打 / 四家立直 / 三家和了 / 四杠散了）：
     * 一律连庄，本场 +1。
     */
    public static boolean abortive() {
        callsRenchan++;
        return true;
    }

    /**
     * **和了止 / 听牌止**（`docs/日本麻将.md` L116）：
     * 「《天凤》在 All Last 庄家**达到一位必要点数、且为 1 位**时，采用自动和了止、**听牌止**；
     * M.League 则继续按通常的连庄条件进行，直到庄家轮庄」。
     *
     * <p>三个条件全满足才结束对局：
     * <ol>
     *   <li>{@code rules.agariyame} 开着（M.League 关 → 永远不中止）；</li>
     *   <li>本局是「庄家和了」**或**「荒牌流局且庄家听牌」：后者就是**听牌止**。
     *       ⚠ 流局满贯**不算** —— 原文只说"荒牌流局时庄家听牌"，而流局满贯是另一种流局
     *       （`res.nagashi`），它按和了结算。</li>
     *   <li>庄家是 1 位**且**持点 ≥ {@code rules.requiredPoints}（一位必要点数）。</li>
     * </ol>
     *
     * <p>调用方只负责判断"这是不是 All Last、且庄家连庄"（{@code kyoku == 4 && 场风 == 最后一场}
     * 且 {@code res.dealerRenchan}）—— 那两条与"本局怎么结束的"无关，留在这里更好单测。
     *
     * @param agari 本局的 {@code Result.agari}（是否有人和牌）
     */
    public static boolean stopAtAllLast(int dealer, boolean agari, boolean nagashi,
                                       boolean[] tenpai, int[] scores, Rules rules) {
        callsAgariyame++;
        if (rules == null || !rules.agariyame || dealer < 0 || dealer > 3 || scores == null) {
            return false;
        }
        final boolean tenpaiAbort = !nagashi && tenpai != null && dealer < tenpai.length
                && tenpai[dealer];
        if (!agari && !tenpaiAbort) {
            return false;                    // 闲家和了 / 庄家不听 —— 都谈不上和了止、听牌止
        }
        if (scores[dealer] < rules.requiredPoints) {
            return false;                    // 没达到一位必要点数 → 继续打
        }
        int top = Integer.MIN_VALUE;
        for (int v : scores) {
            top = Math.max(top, v);
        }
        return scores[dealer] >= top;        // 必须是 1 位（并列第一也算）
    }

    /**
     * All Last 轮庄后要不要**进延长战**（东风战 → 南入、半庄战 → 西入）。
     *
     * <p>`docs/日本麻将.md` L143/L145：「决定是否进入延长战的分数称为**一位必要点数**」；
     * 「如果 All Last 轮庄后 1 位玩家的点数还没有达到一位必要点数，那么游戏会进入延长战」。
     *
     * <p>⚠ 门槛是 **`requiredPoints`（一位必要点数）**，而不是 `returnScore`（返点 = 精算基准）：
     * 《雀魂》正是「一位必要点数 30000 + 精算基准 25000」，用 `returnScore` 当门槛会让
     * 「27000 点也要结束（不延长）」——两者**经常设成相同数值**，但概念不同（原文 L143 明说）。
     *
     * <p>⚠ 场风上限是 **`lastWind + 1`**（东风战 → 南入、半庄战 → 西入），**没有北入**
     * （原文 L145 明写「大部分规则没有北风场，因而也没有北入」，且「半庄战最多进行到西 4 局」）。
     * 旧实现写死 `nw &lt;= 3`，于是半庄的西 4 轮庄后会进**北 1 局**、东风战还能一路进到**西场**
     * （审计 S-54；三套预设 `westExtension` 全关 → 休眠缺陷，但开关一开就错）。
     *
     * @param top             当前 1 位的持点
     * @param nextRoundWind   轮庄后的场风编号（0=东 1=南 2=西 3=北）
     * @param lastWind        本场赛制的最后一场风（东风战 0 / 半庄 1）
     */
    public static boolean keepPlayingWest(Rules rules, int top, int nextRoundWind, int lastWind) {
        callsRenchan++;
        return rules != null && rules.westExtension
                && nextRoundWind <= lastWind + 1
                && top < rules.requiredPoints;
    }

    /**
     * 流局满贯（荒牌满贯）是否成立。
     *
     * <p>条件：该家**没打过非幺九牌**，且**牌河没有被鸣走过**。
     * 空牌河不算成立（一巡都没打完整）。
     *
     * @param river      该家牌河里的牌（牌 id）
     * @param calledFrom 该家牌河是否被鸣走过（被鸣走的那张不计入幺九判定）
     */
    public static boolean nagashiEligible(java.util.List<Integer> river, boolean calledFrom) {
        callsNagashiEligible++;
        if (river == null || river.isEmpty() || calledFrom) {
            return false;
        }
        for (int id : river) {
            if (!mahjong.core.Tiles.isYaochu(mahjong.core.Tiles.kind(id))) {
                return false;
            }
        }
        return true;
    }

    /**
     * 流局满贯的分数增减（**不含立直棒**，供立直棒另算时使用）。
     *
     * <p>庄家成立：三家各付 4000；
     * 闲家成立：庄家付 4000、其余闲家各付 2000。
     *
     * @return 长度 4 的增减数组，四家之和恒为 0
     */
    public static int[] nagashiPayments(int winnerSeat, int dealer) {
        callsNagashiPay++;
        final int[] d = new int[4];
        final boolean dealerWin = winnerSeat == dealer;
        int total = 0;
        for (int o = 0; o < 4; o++) {
            if (o == winnerSeat) {
                continue;
            }
            final int amt = dealerWin ? 4000 : (o == dealer ? 4000 : 2000);
            d[o] -= amt;
            total += amt;
        }
        d[winnerSeat] += total;
        return d;
    }

    /**
     * 该家是否听牌（**形式听牌**）。
     *
     * <p>只看牌形有没有听牌种，**不看役、不看振听**——不听罚符与庄家连庄
     * 用的都是形式听牌，跟"能不能真的荣和"是两回事，别混用。
     *
     * @param concealed 暗牌计数（长度 34）
     * @param meldCount 副露数
     */
    public static boolean tenpai(int[] concealed, int meldCount) {
        callsTenpai++;
        return !Agari.waits(concealed, meldCount).isEmpty();
    }

    // ================================================================= 精算点数

    /**
     * **终局余棒（供託里的立直棒）的分配** —— M.League 原文（见 `docs/DESIGN.md`「终局与精算」）：
     *
     * <blockquote>
     * 因流局导致半庄结束时，立直棒加算给第一位（Top者）。<br>
     * 因流局结束时的立直棒由**相关者**均分。3 人时，将 1000 点分为 400、300、300，
     * 若无法整除则按此倍数分配。（2000 点时为 800、600、600）。
     * 与顺位点相同，更接近起家的一方获得更多点数。
     * </blockquote>
     *
     * <p>⚠ **只有「流局结束」这一支**：和了结束时 `agariRon` 已经把供託全给和牌者
     * （`Result.sticksLeft = 0`），所以调用方只在 `sticks > 0`（= 最后一局是流局）时问这里。
     * 于是本函数要分的两种情况：
     * <ol>
     *   <li>1 位**只有一家** → 全给它（原文第一句「加算给第一位（Top者）」）；</li>
     *   <li>1 位**并列** → 由**相关者**（= 并列第一的那几家）均分：按 100 点为单位向下取整，
     *       尾数全给**更接近起家**的那家 —— 所以 3 人 1000 → **400/300/300**、
     *       2000 → **800/600/600**，与原文逐字一致。</li>
     * </ol>
     *
     * <p>「更接近起家」= **座次更小**（`seat 0` = 起家），与
     * {@link #settle(int[], Rules)} 里"同点按座次先后定名次"用的是**同一把尺子**。
     * ⚠ 注意这里只动**持点**：并列的几家仍然**同顺位**（名次不由这个分配改变）。
     *
     * @param scores 终局四家持点（不修改）
     * @param sticks 供託里的立直棒根数
     * @return 长度 4 的加点数组，总和恒为 {@code sticks * 1000}（点数守恒）
     */
    public static int[] endGameSticks(int[] scores, int sticks) {
        callsEndSticks++;
        final int[] add = new int[4];
        if (scores == null || scores.length < 4 || sticks <= 0) {
            return add;
        }
        int top = scores[0];
        for (int i = 1; i < 4; i++) {
            top = Math.max(top, scores[i]);
        }
        final java.util.List<Integer> tied = new java.util.ArrayList<>();
        for (int i = 0; i < 4; i++) {
            if (scores[i] == top) {
                tied.add(i);                      // 自然按座次升序 → tied.get(0) 就是更接近起家那家
            }
        }
        final int total = sticks * 1000;
        if (tied.size() == 1) {
            add[tied.get(0)] = total;
            return add;
        }
        final int n = tied.size();
        final int unit = (total / n / 100) * 100;   // 每家先拿 100 点的整数倍（向下取整）
        for (int s : tied) {
            add[s] = unit;
        }
        add[tied.get(0)] += total - unit * n;       // 尾数（< n×100）全归更接近起家者
        return add;
    }

    /**
     * 一局的最终精算结果（按**座位**索引）。
     *
     * <p>`order[i]` = 第 i 名（0 起）的座位；`rank[seat]` = 该座位名次（0 = 1 位）；
     * `point` = 精算点数；`uma` / `oka` = 该座位实际拿到的马点与头名赏
     * （同点平分时会与 `rules.uma` 不同，所以要回传而不是让调用方再算一遍）。
     */
    public static final class Settlement {
        public final int[] order = new int[4];
        public final int[] rank = new int[4];
        public final double[] point = new double[4];
        public final double[] uma = new double[4];
        public final double[] oka = new double[4];
    }

    /**
     * 精算点数 = {@code (点数 − 返点)/1000 + 马点 + 头名赏（仅 1 位）}。
     *
     * <p>头名赏 = {@code (返点 − 配给原点) × 4 / 1000}：M.League 为
     * (30000−25000)×4/1000 = **20**（所以 1 位常合并写成 +50 = +30 马点 +20 头名赏）；
     * 配给原点与返点相同时（《雀魂》段位场）不存在头名赏。
     *
     * <p>同点时：`rules.tieSplitPoint`（M.League）**平分对应名次的马点与头名赏**；
     * 否则按起家座次先后来定名次（《天凤》）。
     *
     * <p>例子见 `docs/日本麻将.md` §精算点数：同样是 53600/28600/20000/−2200，
     * M.League 得 +73.6 / +8.6 / −20 / −62.2，《雀魂》得 +43.6 / +8.6 / −10 / −42.2。
     */
    public static Settlement settle(int[] scores, Rules rules) {
        Settlement st = new Settlement();
        Integer[] idx = {0, 1, 2, 3};
        // 分数降序；同点按座次 —— 本服务端 seat 0 = 起家（東1局の親），
        // 所以「座次升序」就是《天凤》的「按起家座次先后定名次」。
        java.util.Arrays.sort(idx, (a, b) -> scores[a] != scores[b] ? scores[b] - scores[a] : a - b);
        for (int i = 0; i < 4; i++) {
            st.order[i] = idx[i];
            st.rank[idx[i]] = i;
        }
        double oka = (rules.returnScore - rules.startScore) * 4 / 1000.0;
        for (int i = 0; i < 4; i++) {
            st.uma[i] = rules.uma[i];
            st.oka[i] = (i == 0) ? oka : 0;
        }
        if (rules.tieSplitPoint) {
            for (int i = 0; i < 4; ) {
                int j = i;
                while (j + 1 < 4 && scores[idx[j + 1]] == scores[idx[i]]) {
                    j++;
                }
                if (j > i) {
                    double u = 0;
                    double o = 0;
                    for (int k = i; k <= j; k++) {
                        u += rules.uma[k];
                        o += (k == 0) ? oka : 0;
                    }
                    for (int k = i; k <= j; k++) {
                        st.uma[k] = u / (j - i + 1);
                        st.oka[k] = o / (j - i + 1);
                    }
                }
                i = j + 1;
            }
        }
        for (int i = 0; i < 4; i++) {
            int s = idx[i];
            st.point[s] = (scores[s] - rules.returnScore) / 1000.0 + st.uma[i] + st.oka[i];
        }
        return st;
    }
}