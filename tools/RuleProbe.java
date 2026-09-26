// Java 侧**只读探针**：把向听/进张/听牌形按固定文本打出来，供 C++ 引擎逐行对拍。
//
//   java -cp server\build\mahjong-server.jar tools\RuleProbe.java <corpus> <mode> <out>
//
//   mode = shanten  → 每行一个向听数（`Shanten.min`）
//          of       → 每行 10 个字段（`HandEval.of` 的快照 + 两个数组的 FNV-1a）
//          discard  → 每行 11 个字段：`k` + 上面那 10 个（`HandEval.afterDiscard`，k 升序）
//
// 语料格式（`tools/trainer-rule-parity.mjs` 生成）：每行 69 个整数
//   `meldCount c0..c33 v0..v33`（c = 暗手牌计数，v = 可见牌计数）
//
// ⚠ 三条口径说明（改这里前先读 docs/TRAINER-CPP.md §4）：
//   1. 它**只 import jar 里的公开 API**，不修改服务端任何东西；
//   2. 副露用**同样数量的占位 Meld**：HandEval 的这几个输出只依赖 `melds.size()`
//      （`Shanten.min` 用 meldCount；`Agari.decompose` 只用它算 need 与 total；
//       waitType 由**暗手**的基础形决定，副露只被追加到 setType/setStart 里）。
//      真正带牌面的副露要到 M2 的实局对拍里覆盖；
//   3. FNV-1a 与 C++ 侧**逐字节**同序：34 个 byte（kind 升序），waitShapes 的 -1 记 0xFF，
//      `of` 未听牌时 Java 的 advanceKinds/waitShapes 是 null → 按全 0 / 全 -1 参与哈希。
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.util.Collections;
import java.util.List;

import mahjong.core.Meld;
import mahjong.core.Tiles;
import mahjong.rules.HandEval;
import mahjong.rules.Shanten;

public final class RuleProbe {

    /** 占位副露：内容不影响本探针的任何输出（见文件头 ②）。 */
    private static final Meld DUMMY = new Meld(Meld.Kind.CHI, new int[] {0, 1, 2}, 0, 0);

    private static final int KIND_COUNT = Tiles.KIND_COUNT;

    /** 与 C++ `fnv1a` 逐位一致：32 位、每字节先异或再乘。 */
    private static int fnv1a(byte[] data) {
        int h = 0x811C9DC5;                   // 2166136261（写成十进制会超 int 字面量上限）
        for (byte b : data) {
            h ^= (b & 0xFF);
            h *= 16777619;
        }
        return h;
    }

    private static byte[] advanceBytes(int[] adv) {
        byte[] out = new byte[KIND_COUNT];
        if (adv != null) {
            for (int k = 0; k < KIND_COUNT; k++) {
                out[k] = (byte) (adv[k] > 0 ? 1 : 0);
            }
        }
        return out;
    }

    private static byte[] shapeBytes(int[] shapes) {
        byte[] out = new byte[KIND_COUNT];
        for (int k = 0; k < KIND_COUNT; k++) {
            out[k] = (byte) (shapes == null ? -1 : shapes[k]);
        }
        return out;
    }

    private static void appendSnapshot(StringBuilder sb, HandEval.Snapshot s) {
        sb.append(s.shanten).append(' ').append(s.tenpai ? 1 : 0).append(' ')
          .append(s.advanceTypes).append(' ').append(s.advanceTiles).append(' ')
          .append(s.waitTypes).append(' ').append(s.waitTiles).append(' ')
          .append(s.goodWaitTypes).append(' ').append(s.goodWaitTiles).append(' ')
          .append(Integer.toUnsignedLong(fnv1a(advanceBytes(s.advanceKinds)))).append(' ')
          .append(Integer.toUnsignedLong(fnv1a(shapeBytes(s.waitShapes))));
    }

    private static List<Meld> meldsOf(int mc) {
        // ⚠ 必须传**空列表**而不是 null：`Agari.addWinVariants` 在展开和了牌归属时会
        //   `for (Meld m : melds)`（没有 null 兜底，而同方法开头的
        //   `meldCount = melds == null ? 0 : melds.size()` 又说明它本该容忍 null）——
        //   传 null 会 NPE。服务端的两个调用点（ObsFeatures、Bot）传的都是列表，所以
        //   这里照生产口径传空列表。见 NOTES.md §6.5「对拍发现的 Java 侧隐患」。
        return Collections.nCopies(mc <= 0 ? 0 : mc, DUMMY);
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 3) {
            System.err.println("用法：java -cp server\\build\\mahjong-server.jar tools\\RuleProbe.java "
                             + "<corpus> <shanten|of|discard> <out>");
            System.exit(2);
        }
        final String mode = args[1];
        if (!mode.equals("shanten") && !mode.equals("of") && !mode.equals("discard")) {
            System.err.println("未知 mode：" + mode);
            System.exit(2);
        }

        long rows = 0;
        long checksum = 0;
        final long t0 = System.nanoTime();
        try (BufferedReader in = new BufferedReader(new FileReader(args[0]), 1 << 20);
             BufferedWriter out = new BufferedWriter(new FileWriter(args[2]), 1 << 20)) {
            String line;
            while ((line = in.readLine()) != null) {
                if (line.isEmpty()) {
                    continue;
                }
                String[] parts = line.trim().split("\\s+");
                if (parts.length < 69) {
                    continue;
                }
                int[] vals = new int[69];
                for (int i = 0; i < 69; i++) {
                    vals[i] = Integer.parseInt(parts[i]);
                }
                final int mc = vals[0];
                int[] counts = new int[KIND_COUNT];
                int[] visible = new int[KIND_COUNT];
                for (int k = 0; k < KIND_COUNT; k++) {
                    counts[k] = vals[1 + k];
                    visible[k] = vals[35 + k];
                }
                final List<Meld> melds = meldsOf(mc);
                final StringBuilder sb = new StringBuilder(96);
                if (mode.equals("shanten")) {
                    final int sh = Shanten.min(counts, mc);
                    checksum += sh;
                    sb.append(sh);
                } else if (mode.equals("of")) {
                    HandEval.Snapshot s = HandEval.of(counts, melds, visible);
                    checksum += s.shanten + s.advanceTypes + s.waitTypes;
                    appendSnapshot(sb, s);
                } else {
                    for (int k = 0; k < KIND_COUNT; k++) {
                        if (counts[k] == 0) {
                            continue;
                        }
                        HandEval.Snapshot s = HandEval.afterDiscard(counts, melds, k, visible);
                        checksum += s.shanten + s.advanceTypes + s.waitTypes;
                        sb.append(k).append(' ');
                        appendSnapshot(sb, s);
                        sb.append('\n');
                    }
                    // 最后一行是多出来的换行：统一在下面按需裁掉
                    if (sb.length() > 0) {
                        sb.setLength(sb.length() - 1);
                    }
                }
                sb.append('\n');
                out.write(sb.toString());
                rows++;
            }
        }
        final double sec = (System.nanoTime() - t0) / 1e9;
        System.err.printf("[RuleProbe] %s 语料 %d 行，用时 %.3f s（%.0f 行/秒） 校验和 %d%n",
                          mode, rows, sec, rows / sec, checksum);
    }

    private RuleProbe() {
    }
}
