package mahjong.game;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import mahjong.core.Tiles;
import mahjong.util.Json;
import mahjong.rules.Agari;

/**
 * 询问玩家时「能做什么」的**纯计算**部分。
 *
 * <p>从 {@link Round} 抽出来的第二步：选项生成原本和局面状态机混在一个类里，
 * 而其中相当一部分并不依赖牌桌——给定手牌/副露/状态就能算出结论。
 * 这部分搬到这里做成静态纯函数，可以**不构造牌桌直接单测**。
 *
 * <p>仍需读取局面状态的部分（例如「哪些牌能立直宣言」要跑听牌判定）
 * 继续留在 {@code Round} 里，由它把参数喂进来。
 */
public final class RoundOptions {

    private RoundOptions() {
    }

    /**
     * 本巡可打的牌（去重后的牌串）。
     *
     * <p>两种模式：
     * <ul>
     *   <li><b>已立直</b>：只能摸切，所以候选就一张——刚摸到的那张；</li>
     *   <li><b>未立直</b>：手牌去重后列出，但排除「振听禁打」的那几种牌。</li>
     * </ul>
     *
     * @param hand           该家手牌（牌 id 列表）
     * @param riichi         该家是否已立直
     * @param drawn          刚摸到的牌 id；{@code < 0} 表示没有摸牌
     * @param forbiddenKinds 振听等原因禁止打出的**牌种**（{@code kind}）
     */
    public static List<Object> discardChoices(List<Integer> hand, boolean riichi, int drawn,
                                              Set<Integer> forbiddenKinds) {
        final List<Object> out = new ArrayList<>();
        if (riichi && drawn >= 0) {
            out.add(Tiles.toStr(drawn));
            return out;
        }
        final Set<String> seen = new LinkedHashSet<>();
        for (int id : hand) {
            if (forbiddenKinds != null && forbiddenKinds.contains(Tiles.kind(id))) {
                continue;
            }
            final String s = Tiles.toStr(id);
            if (seen.add(s)) {
                out.add(s);
            }
        }
        return out;
    }

    /**
     * 立直之后还能不能杠某一种牌。
     *
     * <p>规则：立直后杠牌不得改变听牌形。所以把这种牌的四张从暗牌里拿走
     * （等于多了一副露），再看听牌集合是否与杠之前**完全一致**；
     * 只要变了、或者干脆不听牌了，就不允许杠。
     *
     * @param waitsBefore 杠之前的听牌牌种集合
     * @param concealed   杠之前的暗牌计数（长度 34），**不会被修改**
     * @param meldCount   杠之前的副露数
     * @param kind        想杠的牌种
     */
    public static boolean kanAllowedAfterRiichi(List<Integer> waitsBefore, int[] concealed,
                                                int meldCount, int kind) {
        final int[] after = concealed.clone();
        after[kind] -= 4;
        final List<Integer> waitsAfter = Agari.waits(after, meldCount + 1);
        if (waitsAfter.isEmpty()) {
            return false;
        }
        return new LinkedHashSet<>(waitsBefore).equals(new LinkedHashSet<>(waitsAfter));
    }

    /**
     * 吃：给定被吃的那张牌与暗牌计数，列出所有可行的搭子。
     *
     * <p>同花色内只可能有三种组合（被吃牌在左 / 居中 / 在右），逐一试；
     * 越界检查用的是**花色区间**而不是 0..33，所以不会串到相邻花色去。
     *
     * @param concealed  暗牌计数（长度 34）
     * @param calledKind 被吃的那张的牌种
     * @return 形如 {@code [["1m","2m"], ["2m","4m"]]} 的列表；字牌或无可吃组合时为空
     */
    public static List<Object> chiSets(int[] concealed, int calledKind) {
        final List<Object> sets = new ArrayList<>();
        if (Tiles.isHonor(calledKind)) {
            return sets;
        }
        final int suit = Tiles.suit(calledKind);
        final int base = suit * 9 + Tiles.num(calledKind) - 1;
        final int lo = suit * 9;
        final int hi = suit * 9 + 8;
        final int[][] cands = {{-2, -1}, {-1, 1}, {1, 2}};
        for (int[] cd : cands) {
            final int a = base + cd[0];
            final int b = base + cd[1];
            if (a < lo || b < lo || a > hi || b > hi) {
                continue;
            }
            if (concealed[a] > 0 && concealed[b] > 0) {
                sets.add(Json.arr(Tiles.kindToStr(a), Tiles.kindToStr(b)));
            }
        }
        return sets;
    }
}