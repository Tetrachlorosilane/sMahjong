package mahjong.rules;

import java.util.List;

import mahjong.core.Meld;
import mahjong.core.Tiles;

/**
 * 手牌评估：向听 / 进张 / 听牌形（**牌效启发式**，不是规则判定）。
 *
 * <p>这里回答的是"这手牌离和了多远、摸什么能前进、听得好不好"——
 * 全部由 {@link Shanten} 与 {@link Agari} 这两个既有判据推导，**不新增任何规则语义**，
 * 也不参与任何"能不能和 / 能不能鸣"的判定（那些仍然只在 {@code Round} 里）。
 *
 * <p>为什么要有这一层：这些量原来只以私有方法的形式躺在 {@code Bot} 里
 * （进张数）或者干脆没有（听牌形）。训练侧要拿它们当特征、teacher 要拿它们做取舍、
 * 评测要拿它们解释"为什么这手打得差"——三处各写一份必然漂。现在只有这一份。
 *
 * <p>⚠ 成本：{@link #of} 至少要跑 1 + 34 次向听 DFS，听牌时还要对每个听牌张做一次和了形分解。
 * 它是**离线/评估用**的判据，不要塞进 {@code Bot} 的出牌热路径
 * （{@code Bot} 只用了其中更便宜的 {@link #advanceKinds}，且行为保持不变）。
 */
public final class HandEval {

    private HandEval() {
    }

    /** 向听数（含七对子/国士；和了形 = -1，听牌 = 0）。 */
    public static int shanten(int[] counts, int meldCount) {
        return Shanten.min(counts, meldCount);
    }

    // ------------------------------------------------------------------ 进张

    /**
     * 进张：34 维 0/1，1 = 摸到该牌种会让向听数**下降**。
     *
     * <p>已听牌（向听 0）时返回全 0 —— 那时该看的是 {@link #waits}，不是"进张"。
     */
    public static int[] advanceKinds(int[] counts, int meldCount) {
        int[] out = new int[Tiles.KIND_COUNT];
        final int cur = Shanten.min(counts, meldCount);
        if (cur <= 0) {
            return out;
        }
        int[] c = counts.clone();
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (c[k] >= 4) {
                continue;
            }
            c[k]++;
            if (Shanten.min(c, meldCount) < cur) {
                out[k] = 1;
            }
            c[k]--;
        }
        return out;
    }

    /** 进张**种类数**。 */
    public static int advanceTypes(int[] advanceKinds) {
        int n = 0;
        if (advanceKinds != null) {
            for (int v : advanceKinds) {
                if (v > 0) {
                    n++;
                }
            }
        }
        return n;
    }

    /**
     * 进张**枚数**：{@code Σ max(0, 4 − 自己手里 − 可见)}，只数进张牌种。
     *
     * @param advanceKinds {@link #advanceKinds} 的结果
     * @param own          自己的暗牌计数（副露里的牌本来就不在手里）
     * @param visible      {@link Visible#counts} 的结果；传 {@code null} 等价于"什么也看不见"
     */
    public static int advanceTiles(int[] advanceKinds, int[] own, int[] visible) {
        int n = 0;
        if (advanceKinds == null) {
            return 0;
        }
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (advanceKinds[k] <= 0) {
                continue;
            }
            int o = own == null ? 0 : own[k];
            int v = visible == null ? 0 : visible[k];
            n += Math.max(0, 4 - o - v);
        }
        return n;
    }

    /** 听牌种类（= {@link Agari#waits}）。 */
    public static List<Integer> waits(int[] counts13, int meldCount) {
        return Agari.waits(counts13, meldCount);
    }

    /** 是否听牌（不检查役种）。 */
    public static boolean tenpai(int[] counts13, int meldCount) {
        return Shanten.min(counts13, meldCount) <= 0;
    }

    /**
     * 手牌 + 副露里有多少张**宝牌**（按宝牌指示牌推；杠宝牌指示牌也在这份列表里）。
     *
     * <p>⛔ 宝牌**不是役**：它只加番，凑不齐役种照样不能和 —— 所以这个量只能用来
     * **排序/取舍**（留宝牌还是留形状），绝不能拿去判"能不能和"。
     * 赤宝牌与里宝牌要看具体牌 id，实局里由 {@link Evaluator} 在算分时一起收（见
     * {@code Round.scoreIfWin}）。
     */
    public static int doraCount(int[] counts, List<Meld> melds, List<Integer> doraIndicators) {
        if (doraIndicators == null || doraIndicators.isEmpty()) {
            return 0;
        }
        int n = 0;
        for (int ind : doraIndicators) {
            final int dora = Tiles.doraFrom(ind);
            if (dora < 0 || dora >= Tiles.KIND_COUNT) {
                continue;
            }
            if (counts != null) {
                n += counts[dora];
            }
            if (melds != null) {
                for (Meld m : melds) {
                    for (int t : m.tiles) {
                        if (Tiles.kind(t) == dora) {
                            n++;
                        }
                    }
                }
            }
        }
        return n;
    }

    // ------------------------------------------------------------------ 听牌形

    /**
     * 每个听牌种的**最好形**：长度 34，{@code -1} = 该牌种不是听牌张。
     *
     * <p>取值是 {@link Agari#WAIT_RYANMEN} / {@code WAIT_KANCHAN} / {@code WAIT_PENCHAN} /
     * {@code WAIT_SHANPON} / {@code WAIT_TANKI}。同一个听牌张可能有多种和了形解释
     * （例如 22345m 听 2m/5m 既可能两面也可能双碰），取**最优**的那一种
     * （两面 > 双碰 > 嵌张 > 边张 > 单骑，见 {@link #shapeRank}）。
     *
     * <p>⚠ 七对子与国士无双没有两面形，它们的听牌一律按"单骑"记（保守）；
     * 国士十三面因此被算作愚形 —— 这是**故意的**：这个量是给牌效/押し引き当特征用的，
     * 而役满牌型的价值要走 {@link Evaluator}，不该混进来。
     */
    public static int[] waitShapes(int[] counts13, List<Meld> melds) {
        int[] out = new int[Tiles.KIND_COUNT];
        java.util.Arrays.fill(out, -1);
        final int mc = melds == null ? 0 : melds.size();
        for (int k : Agari.waits(counts13, mc)) {
            int[] c = counts13.clone();
            c[k]++;
            List<Agari.Form> forms = Agari.decompose(c, melds, k);
            int best = -1;
            for (Agari.Form f : forms) {
                best = best < 0 ? f.waitType : betterShape(best, f.waitType);
            }
            out[k] = best < 0 ? Agari.WAIT_TANKI : best;
        }
        return out;
    }

    /** 形的优劣序（越小越好）：两面 < 双碰 < 嵌张 < 边张 < 单骑。 */
    public static int shapeRank(int waitType) {
        switch (waitType) {
            case Agari.WAIT_RYANMEN: return 0;
            case Agari.WAIT_SHANPON: return 1;
            case Agari.WAIT_KANCHAN: return 2;
            case Agari.WAIT_PENCHAN: return 3;
            case Agari.WAIT_TANKI:    return 4;
            default:                  return 5;
        }
    }

    /** 取更优的形。 */
    public static int betterShape(int a, int b) {
        return shapeRank(a) <= shapeRank(b) ? a : b;
    }

    /** 良形（= 两面）。 */
    public static boolean isGoodShape(int waitType) {
        return waitType == Agari.WAIT_RYANMEN;
    }

    // ------------------------------------------------------------------ 快照

    /** 一次评估的快照（打某张牌**之后**的局面）。 */
    public static final class Snapshot {
        /** 向听数（-1 = 已和）。 */
        public int shanten;
        public boolean tenpai;
        /** 未听牌时：进张。 */
        public int[] advanceKinds;
        public int advanceTypes;
        public int advanceTiles;
        /** 听牌时：34 维听牌形（-1 = 不是听牌张）。 */
        public int[] waitShapes;
        public int waitTypes;
        public int waitTiles;
        public int goodWaitTypes;
        public int goodWaitTiles;

        @Override
        public String toString() {
            return tenpai ? ("听牌 种类=" + waitTypes + " 枚=" + waitTiles
                             + " 良形种类=" + goodWaitTypes + " 良形枚=" + goodWaitTiles)
                          : ("向听=" + shanten + " 进张种类=" + advanceTypes + " 枚=" + advanceTiles);
        }
    }

    /**
     * 评估一手 13 张（或 13−3×副露 张）的手牌。
     *
     * @param visible {@link Visible#counts}；{@code null} 等价于看不见任何牌
     */
    public static Snapshot of(int[] counts, List<Meld> melds, int[] visible) {
        final int mc = melds == null ? 0 : melds.size();
        Snapshot s = new Snapshot();
        s.shanten = Shanten.min(counts, mc);
        if (s.shanten <= 0) {
            s.tenpai = true;
            s.waitShapes = waitShapes(counts, melds);
            int[] own = counts.clone();
            int[] draw = Visible.drawable(visible, own);
            for (int k = 0; k < Tiles.KIND_COUNT; k++) {
                if (s.waitShapes[k] < 0) {
                    continue;
                }
                s.waitTypes++;
                s.waitTiles += draw[k];
                if (isGoodShape(s.waitShapes[k])) {
                    s.goodWaitTypes++;
                    s.goodWaitTiles += draw[k];
                }
            }
        } else {
            s.advanceKinds = advanceKinds(counts, mc);
            s.advanceTypes = advanceTypes(s.advanceKinds);
            s.advanceTiles = advanceTiles(s.advanceKinds, counts, visible);
        }
        return s;
    }

    /**
     * 「打掉 {@code discardKind} 之后」的评估。
     *
     * @param counts14 自家回合的**14 张**暗牌计数（含刚摸到的那张）
     */
    public static Snapshot afterDiscard(int[] counts14, List<Meld> melds, int discardKind,
                                        int[] visible) {
        int[] c = counts14.clone();
        if (discardKind >= 0 && discardKind < Tiles.KIND_COUNT && c[discardKind] > 0) {
            c[discardKind]--;
        }
        return of(c, melds, visible);
    }

    // ================================================================== 打点粗估

    /**
     * 放铳的**平均失点**（本作口径；只在期望值比较里用，不影响任何规则判定）。
     */
    public static final int AVG_DEAL_POINTS = 5200;

    /**
     * 打点**粗估**（番数）—— 押し引き与特征派生**共用同一把尺子**（原实现是 {@code Bot} 的私有方法，
     * 2026-09 特征 v3 起提到这里：训练侧要把它当输入，不能再有两份）。
     *
     * <p>为什么不直接用 {@code Round.scoreIfWin}：① 它要在"假设已经鸣牌"的前提下重算
     * （真实的 {@code melds[seat]} / {@code menzen[seat]} 都还没变，照抄会白算平和/门清符/一杯口 ——
     * 两把尺子）；② 押し引き是**每巡**都要做的判断，一次 Evaluator 太贵。所以这里只用
     * 公开信息数形状：宝牌（含赤五）、立直、役牌刻、断幺九（食い断规则下）、
     * 混一色/清一色（副露降番）、对对和（没有吃且没有单张）。
     *
     * @param counts       暗牌计数（34）
     * @param melds        自家副露
     * @param doraIndicators 宝牌指示牌的**牌种 kind**
     * @param akaInHand    手里赤五张数
     * @param selfRiichi   自家是否已立直（没立直且门清 → 计一根立直）
     * @param kuitan       规则是否允许食い断
     * @param roundWind    场风（0=东）
     * @param seatWind     自风（0=东，即自家是庄家）
     */
    public static int estimatedHan(int[] counts, List<Meld> melds, List<Integer> doraIndicators,
                                   int akaInHand, boolean selfRiichi, boolean kuitan,
                                   int roundWind, int seatWind) {
        final int[] c = counts == null ? new int[Tiles.KIND_COUNT] : counts;
        final List<Meld> ms = melds == null ? List.of() : melds;
        final boolean open = !ms.isEmpty();
        int han = doraCount(c, ms, doraIndicators) + akaInHand;
        if (!open && !selfRiichi) {
            han += 1;                                   // 立直
        }
        for (int k = 27; k < Tiles.KIND_COUNT; k++) {
            if (isYakuhai(k, roundWind, seatWind) && hasTriplet(c, ms, k)) {
                han += 1;
            }
        }
        if (kuitan && allSimplesOf(c, ms)) {
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

    /** 役牌（三元牌 / 场风 / 自风）—— 与 {@code Bot.isYakuhai} 同一把尺子。 */
    public static boolean isYakuhai(int kind, int roundWind, int seatWind) {
        if (Tiles.isDragon(kind)) {
            return true;
        }
        if (!Tiles.isWind(kind)) {
            return false;
        }
        return kind == 27 + roundWind || kind == 27 + seatWind;
    }

    /** 手里或副露里有这个牌种的刻子/杠。 */
    public static boolean hasTriplet(int[] counts, List<Meld> melds, int kind) {
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

    /** 暗牌 + 副露里有没有字牌。 */
    public static boolean hasHonor(int[] counts, List<Meld> melds) {
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

    /** 副露里没有吃。 */
    public static boolean noChi(List<Meld> melds) {
        for (Meld m : melds) {
            if (m.kind == Meld.Kind.CHI) {
                return false;
            }
        }
        return true;
    }

    /** 暗牌 + 副露**全是中张（2..8）** —— 断幺九的前提（对任意"假设的"手牌都能问）。 */
    public static boolean allSimplesOf(int[] counts, List<Meld> melds) {
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
    public static int singleSuitOf(int[] counts, List<Meld> melds) {
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
}
