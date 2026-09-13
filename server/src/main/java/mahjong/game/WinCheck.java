package mahjong.game;

import mahjong.core.Tiles;
import mahjong.rules.Agari;

/**
 * 和了形的**纯判断**（不含役种/符数/点数）。
 *
 * <p>从 {@link Round} 抽出来的第一步：这几件事原本散在 {@code Round} 内部，
 * 且「把和了牌并入暗牌 + 校验张数」被 {@code checkWin} 和 {@code winBlockReason}
 * 各写了一遍，属于典型的「改一处漏一处」。现在只剩这里一份。
 *
 * <p>做成**无状态纯函数**是为了能直接单测：不需要构造牌桌、不需要跑状态机，
 * 给定计数就能断言结论。
 */
public final class WinCheck {

    private WinCheck() {
    }

    /**
     * 把和了牌并入暗牌计数，并校验张数是否符合和了形。
     *
     * <p>张数公式：和了时暗牌恒为 {@code 14 - 副露占的张数}。
     * 杠虽然让牌多了，但杠的第四张不算在暗牌里，所以这里按「每副露 3 张」算是对的。
     *
     * @param concealed 当前暗牌计数（长度 34），**不会被修改**
     * @param meldCount 该家的副露数
     * @param winTileId 和了牌的牌 id
     * @param tsumo     是否自摸（自摸时暗牌里已经含这张，不能重复加）
     * @return 并入后的计数；张数不符时返回 {@code null}
     */
    public static int[] counts(int[] concealed, int meldCount, int winTileId, boolean tsumo) {
        final int[] c = concealed.clone();
        if (!tsumo) {
            c[Tiles.kind(winTileId)]++;
        }
        if (Tiles.sum(c) != 14 - meldCount * 3) {
            return null;
        }
        return c;
    }

    /**
     * 是「和了形」但不是「能和」时，给出被挡住的原因。
     *
     * <p>调用方应先确认 {@link #counts} 非 null（即确实是和了形张数）。
     *
     * @return {@code "furiten"}（振听不能荣和）/ {@code "no_yaku"}（无役或番缚不足）
     */
    public static String blockReason(boolean tsumo, boolean furiten) {
        if (!tsumo && furiten) {
            return "furiten";
        }
        return "no_yaku";
    }

    /** 该暗牌计数在指定副露数下是否成和了形。 */
    public static boolean isComplete(int[] counts, int meldCount) {
        return counts != null && Agari.isAgari(counts, meldCount);
    }
}
