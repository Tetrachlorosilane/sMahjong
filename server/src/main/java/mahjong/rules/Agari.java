package mahjong.rules;

import java.util.ArrayList;
import java.util.List;

import mahjong.core.Meld;
import mahjong.core.Tiles;

/** 和了形分解：把一个和了手牌枚举成所有可能的「4 面子 + 1 雀头 / 七对子 / 国士无双」解释。 */
public final class Agari {

    public static final int TYPE_STANDARD = 0;
    public static final int TYPE_CHIITOITSU = 1;
    public static final int TYPE_KOKUSHI = 2;

    public static final int SET_RUN = 0;
    public static final int SET_TRIPLET = 1;
    public static final int SET_QUAD = 2;

    public static final int WAIT_RYANMEN = 0;
    public static final int WAIT_KANCHAN = 1;
    public static final int WAIT_PENCHAN = 2;
    public static final int WAIT_SHANPON = 3;
    public static final int WAIT_TANKI = 4;

    private Agari() {
    }

    /** 一种和了形解释。 */
    public static final class Form {
        public int type = TYPE_STANDARD;
        public int pair = -1;
        public int nSets = 0;
        public final int[] setType = new int[4];
        public final int[] setStart = new int[4];
        public final boolean[] setConcealed = new boolean[4];
        public final boolean[] setFromMeld = new boolean[4];
        /** 和了牌所在的面子下标；-1 表示在雀头。 */
        public int winSet = -1;
        public int waitType = WAIT_RYANMEN;
        /** 国士无双十三面。 */
        public boolean kokushi13 = false;
        /** 供 Evaluator 填入副露引用。 */
        public final Meld[] setMeld = new Meld[4];



        public Form copy() {
            Form f = new Form();
            f.type = type;
            f.pair = pair;
            f.nSets = nSets;
            System.arraycopy(setType, 0, f.setType, 0, 4);
            System.arraycopy(setStart, 0, f.setStart, 0, 4);
            System.arraycopy(setConcealed, 0, f.setConcealed, 0, 4);
            System.arraycopy(setFromMeld, 0, f.setFromMeld, 0, 4);
            System.arraycopy(setMeld, 0, f.setMeld, 0, 4);
            f.winSet = winSet;
            f.waitType = waitType;
            f.kokushi13 = kokushi13;
            return f;
        }
    }

    /**
     * 分解和了形。
     *
     * @param counts    暗手牌计数（**含**和了牌）
     * @param melds     已副露的面子
     * @param winKind   和了牌的 kind
     * @return 所有合法解释（可能为空，表示不和了）
     */
    public static List<Form> decompose(int[] counts, List<Meld> melds, int winKind) {
        List<Form> out = new ArrayList<>();
        int meldCount = melds == null ? 0 : melds.size();
        int need = 4 - meldCount;
        int[] c = counts.clone();
        int total = Tiles.sum(c);
        if (total != need * 3 + 2) {
            return out;
        }
        // ---- 一般型
        List<int[]> rawShapes = new ArrayList<>(); // 编码：pair, nSets, [type,start]...
        for (int p = 0; p < Tiles.KIND_COUNT; p++) {
            if (c[p] < 2) {
                continue;
            }
            c[p] -= 2;
            Shape shape = new Shape();
            shape.pair = p;
            enumSets(c, 0, need, shape, rawShapes);
            c[p] += 2;
        }
        for (int[] shape : rawShapes) {
            addWinVariants(out, shape, winKind, melds);
        }
        // ---- 七对子
        if (meldCount == 0) {
            boolean ok = true;
            for (int k = 0; k < Tiles.KIND_COUNT; k++) {
                if (c[k] != 0 && c[k] != 2) {
                    ok = false;
                    break;
                }
            }
            if (ok) {
                Form f = new Form();
                f.type = TYPE_CHIITOITSU;
                f.pair = winKind;
                f.nSets = 0;
                f.winSet = -1;
                f.waitType = WAIT_TANKI;
                out.add(f);
            }
            // ---- 国士无双
            if (isKokushi(c)) {
                Form f = new Form();
                f.type = TYPE_KOKUSHI;
                f.pair = winKind;
                f.nSets = 0;
                int[] pre = c.clone();
                pre[winKind]--;
                f.kokushi13 = allOrphanKinds(pre);
                f.winSet = -1;
                f.waitType = WAIT_TANKI;
                out.add(f);
            }
        }
        return out;
    }

    private static boolean isKokushi(int[] c) {
        int kinds = 0;
        int total = 0;
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (c[k] == 0) {
                continue;
            }
            if (!Tiles.isYaochu(k)) {
                return false;
            }
            kinds++;
            total += c[k];
            if (c[k] > 2) {
                return false;
            }
        }
        return kinds == 13 && total == 14;
    }

    private static boolean allOrphanKinds(int[] c) {
        int kinds = 0;
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (Tiles.isYaochu(k) && c[k] > 0) {
                kinds++;
            }
        }
        return kinds == 13;
    }

    private static final class Shape {
        int pair;
        int nSets;
        final int[] type = new int[4];
        final int[] start = new int[4];
    }

    private static void enumSets(int[] c, int from, int need, Shape shape, List<int[]> out) {
        if (need == 0) {
            for (int k = from; k < Tiles.KIND_COUNT; k++) {
                if (c[k] != 0) {
                    return;
                }
            }
            int[] enc = new int[2 + shape.nSets * 2];
            enc[0] = shape.pair;
            enc[1] = shape.nSets;
            for (int i = 0; i < shape.nSets; i++) {
                enc[2 + i * 2] = shape.type[i];
                enc[3 + i * 2] = shape.start[i];
            }
            out.add(enc);
            return;
        }
        int i = from;
        while (i < Tiles.KIND_COUNT && c[i] == 0) {
            i++;
        }
        if (i == Tiles.KIND_COUNT) {
            return;
        }
        // 刻子
        if (c[i] >= 3) {
            c[i] -= 3;
            shape.type[shape.nSets] = SET_TRIPLET;
            shape.start[shape.nSets] = i;
            shape.nSets++;
            enumSets(c, i, need - 1, shape, out);
            shape.nSets--;
            c[i] += 3;
        }
        // 顺子
        if (i < 27 && i % 9 <= 6 && c[i + 1] > 0 && c[i + 2] > 0) {
            c[i]--;
            c[i + 1]--;
            c[i + 2]--;
            shape.type[shape.nSets] = SET_RUN;
            shape.start[shape.nSets] = i;
            shape.nSets++;
            enumSets(c, i, need - 1, shape, out);
            shape.nSets--;
            c[i]++;
            c[i + 1]++;
            c[i + 2]++;
        }
    }

    /** 为一个「形」加上副露面子，并展开和了牌归属的所有变体。 */
    private static void addWinVariants(List<Form> out, int[] enc, int winKind, List<Meld> melds) {
        int pair = enc[0];
        int n = enc[1];
        int meldCount = melds == null ? 0 : melds.size();
        int total4 = n + meldCount;
        if (total4 != 4) {
            return;
        }
        // 基础形（不含副露）
        Form base = new Form();
        base.pair = pair;
        base.nSets = n;
        for (int i = 0; i < n; i++) {
            base.setType[i] = enc[2 + i * 2];
            base.setStart[i] = enc[3 + i * 2];
            base.setConcealed[i] = true;
            base.setFromMeld[i] = false;
        }
        // 把和了牌归属展开
        List<int[]> assign = new ArrayList<>(); // [setIndex(-1=雀头), waitType]
        if (pair == winKind) {
            assign.add(new int[]{-1, WAIT_TANKI});
        }
        for (int i = 0; i < n; i++) {
            if (base.setType[i] == SET_RUN) {
                int s = base.setStart[i];
                if (winKind == s) {
                    // 缺顺子最小牌：手中剩 s+1,s+2；只有 8,9 才是边张
                    assign.add(new int[]{i, (s % 9 == 6) ? WAIT_PENCHAN : WAIT_RYANMEN});
                } else if (winKind == s + 1) {
                    assign.add(new int[]{i, WAIT_KANCHAN});
                } else if (winKind == s + 2) {
                    // 缺顺子最大牌：手中剩 s,s+1；只有 1,2 才是边张
                    assign.add(new int[]{i, (s % 9 == 0) ? WAIT_PENCHAN : WAIT_RYANMEN});
                }
            } else if (base.setType[i] == SET_TRIPLET && base.setStart[i] == winKind) {
                assign.add(new int[]{i, WAIT_SHANPON});
            }
        }
        if (assign.isEmpty()) {
            return;
        }
        for (int[] a : assign) {
            Form f = base.copy();
            f.winSet = a[0];
            f.waitType = a[1];
            // 追加副露面子
            int idx = f.nSets;
            for (Meld m : melds) {
                f.setType[idx] = m.isKan() ? SET_QUAD : (m.isRun() ? SET_RUN : SET_TRIPLET);
                f.setStart[idx] = m.baseKind();
                f.setConcealed[idx] = !m.open();
                f.setFromMeld[idx] = true;
                f.setMeld[idx] = m;
                idx++;
            }
            f.nSets = idx;
            out.add(f);
        }
    }

    /** 是否和了形（不检查役种）。 */
    public static boolean isAgari(int[] counts, int meldCount) {
        return Shanten.min(counts, meldCount) < 0;
    }

    /**
     * 计算听牌种类（返回 kind 列表）。
     *
     * @param counts13 暗手 13 张（或 13-3k 张）计数
     */
    public static List<Integer> waits(int[] counts13, int meldCount) {
        List<Integer> out = new ArrayList<>();
        int[] c = counts13.clone();
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (c[k] >= 4) {
                continue;
            }
            c[k]++;
            if (isAgari(c, meldCount)) {
                out.add(k);
            }
            c[k]--;
        }
        return out;
    }
}
