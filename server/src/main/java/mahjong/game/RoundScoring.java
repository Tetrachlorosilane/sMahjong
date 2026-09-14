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

    private RoundScoring() {
    }

    /** 自测：清零计数。 */
    public static void debugResetCounts() {
        callsRenchan = 0;
        callsNagashiEligible = 0;
        callsNagashiPay = 0;
        callsTenpai = 0;
    }

    /** 自测：读计数（连庄 / 满贯成立 / 满贯支付 / 形式听牌）。 */
    public static String debugCallCounts() {
        return "renchan=" + callsRenchan + " nagashiEligible=" + callsNagashiEligible
                + " nagashiPay=" + callsNagashiPay + " tenpai=" + callsTenpai;
    }

    /** 自测：计数总和。 */
    public static int debugTotalCalls() {
        return callsRenchan + callsNagashiEligible + callsNagashiPay + callsTenpai;
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
     * 途中流局（九种九牌 / 四风连打 / 四家立直 / 三家和了 / 四杠散了）：
     * 一律连庄，本场 +1。
     */
    public static boolean abortive() {
        callsRenchan++;
        return true;
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