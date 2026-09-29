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
    /** ① 打点项权重：每多 1 番值多少"枚数分"（第三十九轮加，治"偏好更宽更贱的听牌"）。 */
    private final double hanWeight;

    public SearchPolicy(Policy prior, double dangerWeight, double margin, double hanWeight) {
        this.prior = prior;
        this.dangerWeight = dangerWeight;
        this.margin = margin;
        this.hanWeight = hanWeight;
    }

    @Override
    public Map<String, Object> decide(Decision d) {
        Map<String, Object> base = prior.decide(d);
        // 只在**自家摸打**的询问上做前瞻：鸣牌段的风险/收益口径不同（且没人和你抢这一手），
        // 留给下一版（先用 teacher 的鸣牌，避免一个没标定的模型把"碰不碰"弄坏）。
        if (!"turn".equals(d.kind)) {
            return base;
        }
        int melds = d.obs.melds.get(d.obs.seat).size();
        if (melds >= 1) {
            return base;      // 副露手：`HandEval.of` 的副露口径没接（宁可不动，见 `score`）
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
        // ---- 第一遍：每个候选打完之后的快照（顺带求出"最好的向听"）----
        List<HandEval.Snapshot> snaps = new ArrayList<>();
        int bestShanten = Integer.MAX_VALUE;
        for (Action a : discards) {
            HandEval.Snapshot s = snapshot(d, a);
            snaps.add(s);
            if (s != null && s.shanten < bestShanten) {
                bestShanten = s.shanten;
            }
        }
        // ---- 第二遍：算效用（含 ①打点项 与 ②危险硬约束）----
        boolean threat = underThreat(d.obs);
        double best = Double.NEGATIVE_INFINITY;
        Action bestAction = null;
        double baseScore = Double.NEGATIVE_INFINITY;
        String baseKey = base == null ? null : String.valueOf(base.get("tile"));
        for (int i = 0; i < discards.size(); i++) {
            Action a = discards.get(i);
            HandEval.Snapshot s = snaps.get(i);
            double u = utility(d, a, s, bestShanten, threat);
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

    /** 打完这一张之后的**快照**（向听/进张/听牌形）；`null` = 这个候选不可评（手里没这张）。 */
    private static HandEval.Snapshot snapshot(Decision d, Action a) {
        int kind = a.tile == null ? -1 : Tiles.parseKind(a.tile);
        if (kind < 0 || d.obs.hand[kind] <= 0) {
            return null;
        }
        int[] after = d.obs.hand.clone();
        after[kind]--;
        return HandEval.of(after, List.of(), visibleCounts(d.obs, after));
    }

    /**
     * 显式效用（见类注释的公式）+ 两条第三十九轮加的规矩。
     *
     * @param bestShanten 本次全部候选里**最好**的向听（用来判"这一手有没有结构性改进"）
     * @param threat      现在是否有人立直（② 的硬约束只在有人立直时生效）
     */
    private double utility(Decision d, Action a, HandEval.Snapshot s, int bestShanten,
                           boolean threat) {
        if (s == null) {
            return Double.NEGATIVE_INFINITY;
        }
        double u;
        if (s.tenpai) {
            u = 40.0 * s.waitTiles + 25.0 * s.goodWaitTiles;
            // ① **打点项**：v1 缺它 ⇒ 系统性偏好"更宽但更贱"的听牌（实测把 `9p` 换成 `2p`）。
            u += hanWeight * (estimatedHan(d, a) - 1.0);
        } else {
            u = -1000.0 * s.shanten + 2.0 * s.advanceTiles + 1.0 * s.advanceTypes;
        }
        u -= dangerWeight * dangerOf(d, a);
        // ② **危险硬约束**：有人立直时，**非现物**且**没有结构性改进**（没把向听压到本次最好）
        //    的手一律重罚 —— teacher 的押し引き是调过的，用它换 1~3 枚进张是亏的（实测 Δ=+2.03）。
        int kind = a.tile == null ? -1 : Tiles.parseKind(a.tile);
        if (threat && kind >= 0 && !isGenbutsu(d.obs, kind) && s.shanten >= bestShanten) {
            u -= HARD_DANGER_PENALTY;
        }
        return u;
    }

    /** ② 的重罚额度：远大于任何"枚数分/番数分"的差，等价于一条**硬约束**。 */
    private static final double HARD_DANGER_PENALTY = 1.0e5;

    /** ① 打点：`HandEval.estimatedHan` 的 8 参数版（缺任何一项都退化成按 1 番算，绝不抛）。 */
    private double estimatedHan(Decision d, Action a) {
        Observation obs = d.obs;
        int kind = a.tile == null ? -1 : Tiles.parseKind(a.tile);
        if (kind < 0 || obs.hand[kind] <= 0) {
            return 1.0;
        }
        int[] after = obs.hand.clone();
        after[kind]--;
        int aka = 0;
        for (int k = 0; k < obs.handRed.length; k++) {
            if (obs.handRed[k]) {
                aka++;
            }
        }
        try {
            // ⚠ `Observation` 只有 `roundWind`（没有自风字段）⇒ 这里把自风也按场风传：
            //   只影响"自风役牌"那一项估番（偏保守），不影响牌效/危险的主判据。
            return Math.max(1, HandEval.estimatedHan(after, List.of(), doraKinds(obs), aka,
                    obs.selfRiichi, obs.kuitan, obs.roundWind, obs.roundWind));
        } catch (RuntimeException e) {
            return 1.0;                        // 估不出来就按 1 番（别让搜索因为打点崩掉）
        }
    }

    private static List<Integer> doraKinds(Observation obs) {
        List<Integer> out = new ArrayList<>();
        for (String t : obs.doraIndicators) {
            out.add(Tiles.parseKind(t));
        }
        return out;
    }

    /** 现在是否有人立直（② 的开关）。 */
    private static boolean underThreat(Observation obs) {
        for (int s = 0; s < 4; s++) {
            if (s != obs.seat && obs.riichi[s]) {
                return true;
            }
        }
        return false;
    }

    /** 这张牌对**某个立直家**是不是现物（公开信息，能自己算）。 */
    private static boolean isGenbutsu(Observation obs, int kind) {
        String code = Tiles.kindToStr(kind);
        for (int s = 0; s < 4; s++) {
            if (s != obs.seat && obs.riichi[s] && obs.discards.get(s).contains(code)) {
                return true;
            }
        }
        return false;
    }


    /**
     * 危险度的**粗糙**代理（第一版刻意保守：只在有人立直时给分，且只认"现物 / 几乎绝张"）。
     *
     * <p>⚠ 真正细的危险度在 {@code rules/Danger.java}（teacher 用的是它）；这里不重复实现，
     * 因为搜索只在**边际足够大**时才改判 —— 粗糙的危险项只是用来避免"为了 1 枚进张
     * 打一张对立的危险牌"这种明显的亏。
     */
    private double dangerOf(Decision d, Action act) {
        Observation obs = d.obs;
        int kind = act.tile == null ? -1 : Tiles.parseKind(act.tile);
        if (kind < 0) {
            return 0.0;
        }
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
