// Java 侧**只读探针**：把顺位点 / 终局余棒 / 连庄判据 / 种子链按固定文本打出来，供 C++ 对拍。
//
//   java -cp server\build\mahjong-server.jar tools\SettleProbe.java <corpus> <out>
//
// 语料格式与 `trainer settle` 完全一致（每行以类型开头，见 trainer/src/main.cpp 的注释）。
//
// ⚠ 浮点一律打印**原始位模式**（`Double.doubleToLongBits` 的 16 位十六进制）：
//   两边十进制格式化的差异（Java 最短往返 vs printf %g）会让"值相同但文本不同"，
//   位模式是唯一可靠的口径。
// ⚠ 只 import jar 的公开 API（`RoundScoring` / `Table.mixSeed` 是包私有的，所以这里**抄了一遍**
//   `mixSeed`/`nextRoundSeed` 的确定性分支与 `SelfPlay.seedFor`）；抄写逐字对照过源码，
//   而"两边是否同源"由对拍本身判定（seed 链那几行就是判据）。
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import mahjong.core.Rules;
import mahjong.game.RoundClaims;
import mahjong.game.RoundScoring;
import mahjong.train.SelfPlay;

public final class SettleProbe {

    /** 与 `Table.mixSeed` 逐字一致。 */
    private static long mixSeed(long x) {
        x += 0x9E3779B97F4A7C15L;
        x = (x ^ (x >>> 30)) * 0xBF58476D1CE4E5B9L;
        x = (x ^ (x >>> 27)) * 0x94D049BB133111EBL;
        return x ^ (x >>> 31);
    }

    /** `Table.nextRoundSeed()` 的确定性岔路（`debugDeterministicSeed = true`）。 */
    private static long roundSeedAt(long seedBase, int roundIndex) {
        return mixSeed(seedBase + roundIndex);
    }

    private static Rules rulesOfPreset(String spec) {
        boolean koyaku = false;
        boolean kazoe = false;
        boolean dbl = false;
        boolean west = false;
        String renhou = null;
        String base = spec;
        while (true) {
            int pos = base.indexOf('+');
            if (pos < 0) {
                break;
            }
            String opt = base.substring(pos + 1);
            if (opt.equals("koyaku")) {
                koyaku = true;
            } else if (opt.equals("kazoe")) {
                kazoe = true;
            } else if (opt.equals("dbl")) {
                dbl = true;
            } else if (opt.equals("renhou")) {
                renhou = "mangan";
            } else if (opt.equals("renhouy")) {
                renhou = "yakuman";
            } else if (opt.equals("west")) {
                west = true;
            }
            base = base.substring(0, pos);
        }
        Rules r = Rules.defaults();
        r.applyPreset(base.isEmpty() ? "mleague" : base);
        if (koyaku) {
            r.koyaku = true;
        }
        if (kazoe) {
            r.kazoeYakuman = true;
        }
        if (dbl) {
            r.doubleYakuman = true;
        }
        if (west) {
            r.westExtension = true;
        }
        if (renhou != null) {
            r.renhou = renhou;
        }
        return r;
    }

    private static String bits(double v) {
        return String.format("%016x", Double.doubleToLongBits(v));
    }

    private static String intsCsv(int[] v) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < v.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(v[i]);
        }
        return sb.toString();
    }

    private static List<Integer> parseIntList(String s) {
        List<Integer> out = new ArrayList<>();
        if (s == null || s.isEmpty() || s.equals("-")) {
            return out;
        }
        for (String item : s.split(",", -1)) {
            if (!item.isEmpty()) {
                out.add(Integer.parseInt(item));
            }
        }
        return out;
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 2) {
            System.err.println("用法：java -cp server\\build\\mahjong-server.jar tools\\SettleProbe.java "
                             + "<corpus> <out>");
            System.exit(2);
        }
        long rows = 0;
        long checksum = 0;
        try (BufferedReader in = new BufferedReader(new FileReader(args[0]), 1 << 20);
             BufferedWriter out = new BufferedWriter(new FileWriter(args[1]), 1 << 20)) {
            String line;
            while ((line = in.readLine()) != null) {
                if (line.isEmpty()) {
                    continue;
                }
                String[] f = line.split(" ", -1);
                StringBuilder sb = new StringBuilder(256);
                String kind = f[0];
                if (kind.equals("settle") && f.length >= 6) {
                    int[] scores = new int[4];
                    for (int i = 0; i < 4; i++) {
                        scores[i] = Integer.parseInt(f[2 + i]);
                    }
                    RoundScoring.Settlement st = RoundScoring.settle(scores, rulesOfPreset(f[1]));
                    sb.append(intsCsv(st.order)).append(' ').append(intsCsv(st.rank));
                    for (int i = 0; i < 4; i++) {
                        sb.append(' ').append(bits(st.point[i]));
                    }
                    for (int i = 0; i < 4; i++) {
                        sb.append(' ').append(bits(st.uma[i]));
                    }
                    for (int i = 0; i < 4; i++) {
                        sb.append(' ').append(bits(st.oka[i]));
                    }
                    checksum += (long) (st.point[0] * 10);
                } else if (kind.equals("sticks") && f.length >= 6) {
                    int[] scores = new int[4];
                    for (int i = 0; i < 4; i++) {
                        scores[i] = Integer.parseInt(f[1 + i]);
                    }
                    int[] add = RoundScoring.endGameSticks(scores, Integer.parseInt(f[5]));
                    sb.append(intsCsv(add));
                    checksum += add[0];
                } else if (kind.equals("honba") && f.length >= 5) {
                    int v = RoundScoring.nextHonba(Integer.parseInt(f[1]),
                                                   Integer.parseInt(f[2]) != 0,
                                                   Integer.parseInt(f[3]) != 0,
                                                   Integer.parseInt(f[4]) != 0);
                    sb.append(v);
                    checksum += v;
                } else if (kind.equals("alllast") && f.length >= 10) {
                    boolean[] tenpai = new boolean[4];
                    int mbits = Integer.parseInt(f[5]);
                    for (int i = 0; i < 4; i++) {
                        tenpai[i] = (mbits & (1 << i)) != 0;
                    }
                    int[] scores = new int[4];
                    for (int i = 0; i < 4; i++) {
                        scores[i] = Integer.parseInt(f[6 + i]);
                    }
                    boolean v = RoundScoring.stopAtAllLast(Integer.parseInt(f[2]),
                                                           Integer.parseInt(f[3]) != 0,
                                                           Integer.parseInt(f[4]) != 0,
                                                           tenpai, scores, rulesOfPreset(f[1]));
                    sb.append(v ? 1 : 0);
                    checksum += v ? 1 : 0;
                } else if (kind.equals("west") && f.length >= 5) {
                    boolean v = RoundScoring.keepPlayingWest(rulesOfPreset(f[1]),
                                                             Integer.parseInt(f[2]),
                                                             Integer.parseInt(f[3]),
                                                             Integer.parseInt(f[4]));
                    sb.append(v ? 1 : 0);
                    checksum += v ? 1 : 0;
                } else if (kind.equals("nagashi") && f.length >= 3) {
                    sb.append(intsCsv(RoundScoring.nagashiPayments(Integer.parseInt(f[1]),
                                                                   Integer.parseInt(f[2]))));
                } else if (kind.equals("ngelig") && f.length >= 3) {
                    boolean v = RoundScoring.nagashiEligible(parseIntList(f[2]),
                                                             Integer.parseInt(f[1]) != 0);
                    sb.append(v ? 1 : 0);
                    checksum += v ? 1 : 0;
                } else if (kind.equals("split") && f.length >= 4) {
                    int[] sr = splitRemainder(Integer.parseInt(f[1]), Integer.parseInt(f[2]),
                                              Integer.parseInt(f[3]));
                    sb.append(sr[0]).append(' ').append(sr[1]);
                } else if (kind.equals("seeds") && f.length >= 3) {
                    long base = Long.parseLong(f[1]);
                    int n = Integer.parseInt(f[2]);
                    for (int i = 0; i < n; i++) {
                        if (i > 0) {
                            sb.append(',');
                        }
                        sb.append(roundSeedAt(base, i));
                    }
                } else if (kind.equals("seedfor") && f.length >= 3) {
                    long base = Long.parseLong(f[1]);
                    int n = Integer.parseInt(f[2]);
                    for (int i = 0; i < n; i++) {
                        if (i > 0) {
                            sb.append(',');
                        }
                        sb.append(SelfPlay.seedFor(base, i));
                    }
                } else if (kind.equals("place") && f.length >= 5) {
                    int[] scores = new int[4];
                    for (int i = 0; i < 4; i++) {
                        scores[i] = Integer.parseInt(f[1 + i]);
                    }
                    List<Object> place = SelfPlay.placementOf(scores);
                    for (int i = 0; i < 4; i++) {
                        if (i > 0) {
                            sb.append(',');
                        }
                        sb.append(((Number) place.get(i)).intValue());
                    }
                } else if (kind.equals("rank") && f.length >= 2) {
                    sb.append(RoundClaims.rankOf(f[1]));
                } else if (kind.equals("beat") && f.length >= 4) {
                    RoundClaims.Claim best = parseClaim(f[1]);
                    sb.append(RoundClaims.canBeat(best, Integer.parseInt(f[2]),
                                                  Integer.parseInt(f[3])) ? 1 : 0);
                } else if (kind.equals("allron") && f.length >= 3) {
                    sb.append(RoundClaims.allRonAnswered(parseIntList(f[1]), intSetOf(f[2])) ? 1 : 0);
                } else if (kind.equals("stop") && f.length >= 8) {
                    boolean v = RoundClaims.shouldStop(
                            parseIntList(f[1]), parseIntList(f[2]), intSetOf(f[3]),
                            Long.parseLong(f[4]), parseClaim(f[5]), intMapOf(f[6]), intMapOf(f[7]));
                    sb.append(v ? 1 : 0);
                } else if (kind.equals("accept") && f.length >= 5) {
                    Long pending = f[2].equals("-") ? null : Long.valueOf(f[2]);
                    sb.append(RoundClaims.acceptsReply(Integer.parseInt(f[1]) != 0, pending,
                            Integer.parseInt(f[3]) != 0, Long.parseLong(f[4])) ? 1 : 0);
                } else {
                    System.err.println("认不出的语料行：" + line);
                    System.exit(2);
                }
                sb.append('\n');
                out.write(sb.toString());
                rows++;
            }
        }
        System.err.printf("[SettleProbe] 语料 %d 行 校验和 %d%n", rows, checksum);
    }

    /** 与 `RoundScoring.splitRemainder` 逐字一致（包私有，所以在这里抄一份）。 */
    private static int[] splitRemainder(int total, int n, int unit) {
        final int each = Math.floorDiv(total, n * unit) * unit;
        return new int[]{each, total - each * n};
    }

    /** `rank:dist` 或 `-`（= 还没有任何有效鸣牌）。 */
    private static RoundClaims.Claim parseClaim(String s) {
        if (s == null || s.isEmpty() || s.equals("-")) {
            return null;
        }
        String[] p = s.split(":");
        return new RoundClaims.Claim(Integer.parseInt(p[0]),
                                     p.length > 1 ? Integer.parseInt(p[1]) : 0);
    }

    private static java.util.Set<Integer> intSetOf(String s) {
        java.util.Set<Integer> out = new java.util.LinkedHashSet<>(parseIntList(s));
        return out;
    }

    private static Map<Integer, Integer> intMapOf(String s) {
        Map<Integer, Integer> out = new java.util.LinkedHashMap<>();
        if (s == null || s.isEmpty() || s.equals("-")) {
            return out;
        }
        for (String item : s.split(";", -1)) {
            int c = item.indexOf(':');
            if (c < 0) {
                continue;
            }
            out.put(Integer.parseInt(item.substring(0, c)), Integer.parseInt(item.substring(c + 1)));
        }
        return out;
    }

    private SettleProbe() {
    }
}
