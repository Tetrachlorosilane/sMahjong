package mahjong.ai;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import mahjong.core.Meld;
import mahjong.core.Tiles;
import mahjong.rules.HandEval;

/**
 * 一层前瞻的搜索策略（策略串 {@code search[:危险权重]}）—— 训练侧"**比 teacher 强的老师**"候选。
 *
 * <h2>为什么这样设计（而不是从零写一个"更强的启发"）</h2>
 * 内置 teacher（{@link Bot}）在"打哪张"上已经把牌效、危险度、打点揉好了，而且那些权重是**调过的**；
 * 一个手写的、粗糙的危险度模型放进去只会**更差**。所以这里的结构是
 * **"teacher 当先验 + 显式效用做否决"**：
 *
 * <ol>
 *   <li>先问 teacher 要一手（它是 prior，保住它调好的危险度/牌效）；</li>
 *   <li>把**本次合法动作集**里每个打牌候选（含摸切）用下面这条显式效用打分（**打完之后的局面**）：</li>
 * </ol>
 *
 * <pre>
 *   未听牌  U = -1000·向听 + 2·进张枚数 + 1·进张种类
 *   听牌    U = 40·听牌枚数 + 25·良形枚数                      // ⚠ v1 不含打点项（estimatedHan 要 8 个参数，先不引；下一版按实测再加）
 *   危险    U -= w·危险度（有人立直/副露时才计入）            // w = 危险权重（缺省 8）
 * </pre>
 *
 * <ol start="3">
 *   <li>只有当**最优候选比 teacher 那一手高出 `margin`** 时才改判（缺省 60 分 ≈ 一个良形听牌的
 *       1.5 倍"枚数分"），否则**听 teacher 的**。这条"边际"是刻意的保守：
 *       我的效用里没有 teacher 那么多细节（山読み/押し引き/打点细算），
 *       看不清的差别就不该被一个粗糙模型翻掉。</li>
 * </ol>
 *
 * <p>⚠ **只读合法信息**（{@link Round} 的公开辅助方法：{@code concealCounts} / {@code discards} /
 * {@code melds} / {@code doraIndicators} / {@code riichi}）—— 不读别家手牌、不读牌山。
 * 这与 teacher 的读法**同一套**，所以它既不是作弊、也可以直接拿来做离线标签（下一轮蒸馏用）。
 *
 * <p>⚠ 与 teacher 的**同种子可复现**：本类无随机源，同输入必同输出（搜索里没有采样）。
 */
public final class SearchPolicy implements Policy {

    /** 打牌候选之外，`pass` / 非打牌动作要不要也进搜索：第一版只搜打牌（其它沿用 teacher）。 */
    private final Policy prior;
    private final double dangerWeight;
    private final double margin;

    public SearchPolicy(Policy prior, double dangerWeight, double margin) {
        this.prior = prior;
        this.dangerWeight = dangerWeight;
        this.margin = margin;
    }

    @Override
    public Map<String, Object> decide(Decision d) {
        Map<String, Object> base = prior.decide(d);
        // 只在**自家摸打**的询问上做前瞻：鸣牌段的风险/收益口径不同（且没人和你抢这一手），
        // 留给下一版（先用 teacher 的鸣牌，避免一个没标定的模型把"碰不碰"弄坏）。
        if (!"turn".equals(d.kind)) {
            return base;
        }
        List<Action> discards = new ArrayList<>();
        for (Action a : d.legal()) {
            if (Action.DISCARD.equals(a.type)) {
                discards.add(a);
            }
        }
        if (discards.size() < 2) {
            return base;                       // 只有一张能打：没有可搜的
        }
        double best = Double.NEGATIVE_INFINITY;
        Action bestAction = null;
        double baseScore = Double.NEGATIVE_INFINITY;
        String baseKey = base == null ? null : String.valueOf(base.get("tile"));
        for (Action a : discards) {
            double u = score(d, a);
            if (u > best) {
                best = u;
                bestAction = a;
            }
            if (a.tile != null && a.tile.equals(baseKey)) {
                baseScore = u;
            }
        }
        if (bestAction == null) {
            return base;
        }
        // teacher 那一手不在打牌候选里（例如它选择了立直/自摸/九种九牌）⇒ 不翻。
        if (baseScore == Double.NEGATIVE_INFINITY) {
            return base;
        }
        // ⚠ **代价审计**：`MAHJONG_SEARCH_TRACE=1` 时每次改判打一行（`tile` = 改成打哪张、
        //   `base` = teacher 原来打哪张、两边效用）。为什么要有它：搜索与 teacher 的差别
        //   全在这些改判上 —— 只看"赢没赢"不知道**改了哪一类**（为了 +1 枚进张丢掉现物？
        //   还是把愚形听牌换成良形？），而这两类该往哪个方向调完全相反。
        if (best - baseScore > margin) {
            if (traceOn()) {
                System.err.println("[search] 改判 " + baseKey + " → " + bestAction.tile
                        + "  U " + fmt(baseScore) + " → " + fmt(best)
                        + "（Δ" + fmt(best - baseScore) + "）");
            }
            return bestAction.toCmd();
        }
        return base;
    }

    private static boolean traceOn() {
        return System.getenv("MAHJONG_SEARCH_TRACE") != null;
    }

    private static String fmt(double v) {
        return String.format(java.util.Locale.ROOT, "%.1f", v);
    }

    /** 打完这一张之后的显式效用（见类注释的公式）。 */
    private double score(Decision d, Action a) {
        int kind = a.tile == null ? -1 : Tiles.parseKind(a.tile);
        if (kind < 0) {
            return Double.NEGATIVE_INFINITY;
        }
        Observation obs = d.obs;
        int[] after = obs.hand.clone();
        if (after[kind] <= 0) {
            return Double.NEGATIVE_INFINITY;   // 手里没有这张（不该发生：legal 已校验）
        }
        after[kind]--;
        int melds = obs.melds.get(obs.seat).size();
        int[] visible = visibleCounts(obs, after);
        HandEval.Snapshot s = HandEval.of(after, List.of(), visible);
        double u;
        if (s.tenpai) {
            u = 40.0 * s.waitTiles + 25.0 * s.goodWaitTiles;
        } else {
            u = -1000.0 * s.shanten + 2.0 * s.advanceTiles + 1.0 * s.advanceTypes;
        }
        // ⚠ `HandEval.of` 的第 2 个参数是**副露列表**（这里给空表）⇒ 副露手的向听/听牌会偏保守：
        //   第一版先这样（副露手沿用 teacher，见 `decide` 里 `melds >= 1` 的守门）。
        if (melds >= 1) {
            return Double.NEGATIVE_INFINITY;   // 副露手：不进搜索（口径不齐，别乱翻）
        }
        u -= dangerWeight * dangerOf(d, kind);
        return u;
    }

    /** 公开可见的牌数（自己手牌 + 四家牌河 + 宝牌指示牌）——**不含**别家手牌与牌山。 */
    private static int[] visibleCounts(Observation obs, int[] ownAfter) {
        int[] v = ownAfter.clone();
        for (List<String> river : obs.discards) {
            for (String t : river) {
                v[Tiles.parseKind(t)]++;
            }
        }
        for (String t : obs.doraIndicators) {
            v[Tiles.parseKind(t)]++;
        }
        for (int k = 0; k < v.length; k++) {
            v[k] = Math.min(4, v[k]);
        }
        return v;
    }

    /**
     * 危险度的**粗糙**代理（第一版刻意保守：只在有人立直时给分，且只认"现物 / 几乎绝张"）。
     *
     * <p>⚠ 真正细的危险度在 {@code rules/Danger.java}（teacher 用的是它）；这里不重复实现，
     * 因为搜索只在**边际足够大**时才改判 —— 粗糙的危险项只是用来避免"为了 1 枚进张
     * 打一张对立的危险牌"这种明显的亏。
     */
    private double dangerOf(Decision d, int kind) {
        Observation obs = d.obs;
        boolean threat = false;
        for (int s = 0; s < 4; s++) {
            if (s != obs.seat && obs.riichi[s]) {
                threat = true;
            }
        }
        if (!threat) {
            return 0.0;
        }
        String code = Tiles.kindToStr(kind);
        for (int s = 0; s < 4; s++) {
            if (s == obs.seat || !obs.riichi[s]) {
                continue;
            }
            if (obs.discards.get(s).contains(code)) {
                return 0.0;                    // 现物：对这家绝对安全（公开信息，能自己算）
            }
        }
        int seen = 0;
        for (List<String> river : obs.discards) {
            for (String t : river) {
                if (Tiles.parseKind(t) == kind) {
                    seen++;
                }
            }
        }
        return seen >= 3 ? 0.5 : 3.0;          // 几乎绝张 vs 生牌
    }


    /** 仅供自检：把 Meld 列表转成计数（当前版本搜索不使用，保留给下一版副露口径）。 */
    static int meldCount(List<Map<String, Object>> melds) {
        return melds == null ? 0 : melds.size();
    }

    /** 仅供自检/调试：本类只读公开信息，列出来便于审查。 */
    static List<Meld> noMelds() {
        return List.of();
    }
}
