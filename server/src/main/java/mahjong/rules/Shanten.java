package mahjong.rules;

import mahjong.core.Tiles;

/**
 * 向听数计算。
 *
 * <p>约定：和了形为 {@code -1}，听牌为 {@code 0}。
 */
public final class Shanten {

    private Shanten() {
    }

    /** 一般型向听（含副露：{@code meldCount} 个已完成面子）。 */
    public static int standard(int[] counts, int meldCount) {
        int[] c = counts.clone();
        int[] best = {99};
        dfs(c, 0, meldCount, 0, 0, best);
        return best[0];
    }

    /**
     * 递归搜索面子/搭子。
     *
     * @param melds    已完成面子数
     * @param partials 搭子数（不含雀头）
     * @param head     是否已有雀头
     */
    private static void dfs(int[] c, int start, int melds, int partials, int head, int[] best) {
        int i = start;
        while (i < Tiles.KIND_COUNT && c[i] == 0) {
            i++;
        }
        if (i == Tiles.KIND_COUNT) {
            int sh = 8 - 2 * melds - partials - head;
            if (sh < best[0]) {
                best[0] = sh;
            }
            return;
        }
        // 刻子
        if (c[i] >= 3 && melds + partials < 4) {
            c[i] -= 3;
            dfs(c, i, melds + 1, partials, head, best);
            c[i] += 3;
        }
        // 顺子
        if (i < 27 && i % 9 <= 6 && c[i + 1] > 0 && c[i + 2] > 0 && melds + partials < 4) {
            c[i]--;
            c[i + 1]--;
            c[i + 2]--;
            dfs(c, i, melds + 1, partials, head, best);
            c[i]++;
            c[i + 1]++;
            c[i + 2]++;
        }
        // 对子：作雀头 或 作搭子
        if (c[i] >= 2) {
            c[i] -= 2;
            if (head == 0) {
                dfs(c, i, melds, partials, 1, best);
            }
            if (melds + partials < 4) {
                dfs(c, i, melds, partials + 1, head, best);
            }
            c[i] += 2;
        }
        // 两面/边张搭子
        if (i < 27 && i % 9 <= 7 && c[i + 1] > 0 && melds + partials < 4) {
            c[i]--;
            c[i + 1]--;
            dfs(c, i, melds, partials + 1, head, best);
            c[i]++;
            c[i + 1]++;
        }
        // 嵌张搭子
        if (i < 27 && i % 9 <= 6 && c[i + 2] > 0 && melds + partials < 4) {
            c[i]--;
            c[i + 2]--;
            dfs(c, i, melds, partials + 1, head, best);
            c[i]++;
            c[i + 2]++;
        }
        // 孤张丢弃
        c[i]--;
        dfs(c, i, melds, partials, head, best);
        c[i]++;
    }

    /** 七对子向听。 */
    public static int chiitoitsu(int[] counts) {
        int pairs = 0;
        int kinds = 0;
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (counts[k] > 0) {
                kinds++;
            }
            if (counts[k] >= 2) {
                pairs++;
            }
        }
        int sh = 6 - pairs;
        if (kinds < 7) {
            sh += 7 - kinds;
        }
        return sh;
    }

    /** 国士无双向听。 */
    public static int kokushi(int[] counts) {
        int kinds = 0;
        boolean pair = false;
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (!Tiles.isYaochu(k)) {
                continue;
            }
            if (counts[k] > 0) {
                kinds++;
            }
            if (counts[k] >= 2) {
                pair = true;
            }
        }
        return 13 - kinds - (pair ? 1 : 0);
    }

    /** 取三种牌型的最小向听。 */
    public static int min(int[] counts, int meldCount) {
        int s = standard(counts, meldCount);
        if (meldCount == 0) {
            s = Math.min(s, chiitoitsu(counts));
            s = Math.min(s, kokushi(counts));
        }
        return s;
    }
}
