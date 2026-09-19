package mahjong.rules;

import java.util.List;

import mahjong.core.Meld;
import mahjong.core.Tiles;

/**
 * 可见牌 / 剩余牌统计 —— **纯计数**，没有任何启发式，也不参与任何规则判定。
 *
 * <p>「可见」= 桌上四家都能看到的东西：
 * <ul>
 *   <li>四家牌河（被鸣走的那张已由服务端从牌河移除，同时出现在副露里，所以不会重复计）；</li>
 *   <li>四家副露（含被鸣的那张）；</li>
 *   <li>宝牌指示牌（含杠后新翻的）。</li>
 * </ul>
 * ⛔ **里宝指示牌不算可见** —— 它只有和牌者自己看得到（`Round.uraIndicators()` 属于隐藏信息）。
 *
 * <p>训练侧要它是因为"同样一张牌，桌上还剩几张"几乎决定了一切牌效与危险度；
 * 而把这笔账放在规则层（而不是各调用点自己数一遍）才能保证
 * {@code Observation.visible}、进张枚数、危险度三者**同源**。
 */
public final class Visible {

    private Visible() {
    }

    /**
     * 汇总可见计数（长度 34）。
     *
     * @param discards 四家牌河（牌 id 列表），可为 {@code null}
     * @param melds    四家副露，可为 {@code null}
     * @param dora     宝牌指示牌的 kind 列表，可为 {@code null}
     */
    public static int[] counts(List<Integer>[] discards, List<Meld>[] melds, List<Integer> dora) {
        int[] acc = new int[Tiles.KIND_COUNT];
        for (int s = 0; s < 4; s++) {
            if (discards != null && s < discards.length) {
                addDiscards(acc, discards[s]);
            }
            if (melds != null && s < melds.length) {
                addMelds(acc, melds[s]);
            }
        }
        if (dora != null) {
            for (int k : dora) {
                if (k >= 0 && k < Tiles.KIND_COUNT) {
                    acc[k]++;
                }
            }
        }
        return acc;
    }

    /** 把一手牌河（牌 id）并入可见计数。 */
    public static void addDiscards(int[] acc, List<Integer> discards) {
        if (discards == null) {
            return;
        }
        for (int id : discards) {
            acc[Tiles.kind(id)]++;
        }
    }

    /** 把一副副露（全部牌，含被鸣那张）并入可见计数。 */
    public static void addMelds(int[] acc, List<Meld> melds) {
        if (melds == null) {
            return;
        }
        for (Meld m : melds) {
            for (int t : m.tiles) {
                acc[Tiles.kind(t)]++;
            }
        }
    }

    /**
     * 34 维剩余张数 = {@code 4 − 可见}（下限 0）。
     *
     * <p>⚠ 这是"相对于**完全未知**"的剩余量。要算"我还能摸到几张"，还要再减掉**自己手里**的
     * ——见 {@link #drawable}。混用这两个量是牌效统计里最常见的错。
     */
    public static int[] unseen(int[] visible) {
        int[] out = new int[Tiles.KIND_COUNT];
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            out[k] = Math.max(0, 4 - (visible == null ? 0 : visible[k]));
        }
        return out;
    }

    /** 34 维**可摸张数** = {@code 4 − 可见 − 自己手里}（下限 0）。 */
    public static int[] drawable(int[] visible, int[] own) {
        int[] out = new int[Tiles.KIND_COUNT];
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            int v = visible == null ? 0 : visible[k];
            int o = own == null ? 0 : own[k];
            out[k] = Math.max(0, 4 - v - o);
        }
        return out;
    }

    /** 单个牌种的剩余张数（下限 0）。 */
    public static int unseenOf(int[] visible, int kind) {
        if (kind < 0 || kind >= Tiles.KIND_COUNT) {
            return 0;
        }
        return Math.max(0, 4 - (visible == null ? 0 : visible[kind]));
    }

    public static int total(int[] counts) {
        int n = 0;
        if (counts != null) {
            for (int v : counts) {
                n += v;
            }
        }
        return n;
    }
}
