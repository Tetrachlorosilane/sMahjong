// Java 侧**只读探针**：把役种 / 符数 / 打点 / 授受按固定文本打出来，供 C++ 引擎逐行对拍。
//
//   java -cp server\build\mahjong-server.jar tools\ScoreProbe.java <corpus> <out>
//
// 语料格式与 `trainer score` 完全一致（每行 15 个空格分隔字段，见 trainer/src/main.cpp 的注释）：
//   preset winKind flags roundWind seat dealerSeat menzen honba sticks loser dora ura pao hand melds
//
// ⚠ 它只 import jar 里的公开 API（`Rules.applyPreset` / `Evaluator.evaluate` / `Payments.compute`），
//   不修改服务端任何东西。输出的 17 个字段与 C++ 侧**逐字节**可比。
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

import mahjong.core.Meld;
import mahjong.core.Rules;
import mahjong.core.Tiles;
import mahjong.rules.Agari;
import mahjong.rules.Evaluator;
import mahjong.rules.Payments;
import mahjong.rules.WinContext;
import mahjong.rules.YakuCodes;

public final class ScoreProbe {

    private static final int KIND_COUNT = Tiles.KIND_COUNT;

    /** 预设串：`mleague` / `tenhou` / `majsoul`，可带 `+koyaku` / `+kazoe` / `+dbl` / `+renhou[y]` 后缀。 */
    private static Rules rulesOfPreset(String spec) {
        boolean koyaku = false;
        boolean kazoe = false;
        boolean dbl = false;
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
        if (renhou != null) {
            r.renhou = renhou;
        }
        return r;
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

    private static String reasonTag(String reason) {
        if (reason == null || reason.isEmpty()) {
            return "-";
        }
        if (reason.equals("不是和了形")) {
            return "not_agari";
        }
        if (reason.equals("无役")) {
            return "no_yaku";
        }
        if (reason.equals("番缚不足")) {
            return "han_shibari";
        }
        return "?";
    }

    private static void appendFormSig(StringBuilder sb, Evaluator.HandScore s) {
        if (s.form == null) {
            sb.append('-');
            return;
        }
        Agari.Form f = s.form;
        sb.append(f.type).append(':').append(f.pair).append(':').append(f.winSet).append(':')
          .append(f.waitType).append(':').append(f.nSets);
        for (int i = 0; i < f.nSets; i++) {
            sb.append(':');
            sb.append(f.setType[i] == Agari.SET_RUN ? 'r' : (f.setType[i] == Agari.SET_TRIPLET ? 't' : 'q'));
            sb.append(f.setStart[i]);
            sb.append(f.setConcealed[i] ? 'c' : 'o');
            sb.append(f.setFromMeld[i] ? 'm' : '-');
        }
    }

    public static void main(String[] args) throws IOException {
        if (args.length < 2) {
            System.err.println("用法：java -cp server\\build\\mahjong-server.jar tools\\ScoreProbe.java "
                             + "<corpus> <out>");
            System.exit(2);
        }
        long rows = 0;
        long checksum = 0;
        long t0 = System.nanoTime();
        try (BufferedReader in = new BufferedReader(new FileReader(args[0]), 1 << 20);
             BufferedWriter out = new BufferedWriter(new FileWriter(args[1]), 1 << 20)) {
            String line;
            StringBuilder sb = new StringBuilder(256);
            while ((line = in.readLine()) != null) {
                if (line.isEmpty()) {
                    continue;
                }
                String[] f = line.split(" ", -1);
                if (f.length < 15) {
                    continue;
                }
                WinContext ctx = new WinContext();
                ctx.rules = rulesOfPreset(f[0]);
                int winKind = Integer.parseInt(f[1]);
                int flags = Integer.parseInt(f[2]);
                ctx.roundWind = Integer.parseInt(f[3]);
                ctx.seat = Integer.parseInt(f[4]);
                ctx.dealerSeat = Integer.parseInt(f[5]);
                boolean menzen = Integer.parseInt(f[6]) != 0;
                int honba = Integer.parseInt(f[7]);
                int sticks = Integer.parseInt(f[8]);
                int loser = Integer.parseInt(f[9]);
                ctx.tsumo = (flags & (1 << 0)) != 0;
                ctx.riichi = (flags & (1 << 1)) != 0;
                ctx.doubleRiichi = (flags & (1 << 2)) != 0;
                ctx.ippatsu = (flags & (1 << 3)) != 0;
                ctx.chankan = (flags & (1 << 4)) != 0;
                ctx.rinshan = (flags & (1 << 5)) != 0;
                ctx.haitei = (flags & (1 << 6)) != 0;
                ctx.houtei = (flags & (1 << 7)) != 0;
                ctx.tenhou = (flags & (1 << 8)) != 0;
                ctx.chiihou = (flags & (1 << 9)) != 0;
                ctx.renhou = (flags & (1 << 10)) != 0;
                ctx.tsubame = (flags & (1 << 11)) != 0;
                ctx.kanburi = (flags & (1 << 12)) != 0;
                ctx.nagashi = (flags & (1 << 13)) != 0;
                ctx.winKind = winKind;
                ctx.menzen = menzen;
                ctx.doraIndicators = parseIntList(f[10]);
                ctx.uraIndicators = parseIntList(f[11]);

                List<Payments.Pao> paos = new ArrayList<>();
                if (!f[12].equals("-")) {
                    for (String item : f[12].split(";", -1)) {
                        int c = item.indexOf(':');
                        if (c < 0) {
                            continue;
                        }
                        paos.add(new Payments.Pao(Integer.parseInt(item.substring(0, c)),
                                                  Integer.parseInt(item.substring(c + 1))));
                    }
                }

                List<Integer> handIds = parseIntList(f[13]);
                int[] concealed = new int[KIND_COUNT];
                List<Integer> allIds = new ArrayList<>(handIds);
                for (int id : handIds) {
                    concealed[Tiles.kind(id)]++;
                }
                List<Meld> melds = new ArrayList<>();
                if (!f[14].equals("-")) {
                    for (String item : f[14].split(";", -1)) {
                        if (item.length() < 2) {
                            continue;
                        }
                        char tc = item.charAt(0);
                        char oc = item.charAt(1);
                        Meld.Kind kind;
                        switch (tc) {
                            case 'r': kind = Meld.Kind.CHI; break;
                            case 't': kind = Meld.Kind.PON; break;
                            case 'q': kind = oc == 'c' ? Meld.Kind.ANKAN : Meld.Kind.DAIMINKAN; break;
                            case 'k': kind = Meld.Kind.KAKAN; break;
                            default: continue;
                        }
                        List<Integer> ids = parseIntList(item.substring(item.indexOf(':') + 1));
                        int[] arr = new int[ids.size()];
                        for (int i = 0; i < ids.size(); i++) {
                            arr[i] = ids.get(i);
                            allIds.add(ids.get(i));
                        }
                        melds.add(new Meld(kind, arr, 0, 0));
                    }
                }
                ctx.allTileIds = allIds;

                Evaluator.HandScore s = Evaluator.evaluate(ctx, concealed, melds, winKind);
                Payments.Result p = Payments.compute(s, ctx.seat, loser, ctx.dealerSeat, honba, sticks,
                                                     ctx.tsumo, paos);
                checksum += s.base + p.winnerGain;

                sb.setLength(0);
                sb.append(s.valid ? 1 : 0).append(' ')
                  .append(s.hanYaku).append(' ').append(s.han).append(' ').append(s.fu).append(' ')
                  .append(s.yakuman).append(' ').append(s.dora).append(' ').append(s.ura).append(' ')
                  .append(s.aka).append(' ').append(s.base).append(' ');
                String limitCode = YakuCodes.limitOf(s.limit);
                sb.append(limitCode.isEmpty() ? "-" : limitCode).append(' ');
                sb.append(reasonTag(s.reason)).append(' ');
                appendFormSig(sb, s);
                sb.append(' ');
                if (s.yaku.isEmpty()) {
                    sb.append('-');
                } else {
                    for (int i = 0; i < s.yaku.size(); i++) {
                        if (i > 0) {
                            sb.append(',');
                        }
                        Evaluator.Yaku y = s.yaku.get(i);
                        sb.append(YakuCodes.codeOf(y.name)).append(':').append(y.equivalentHan())
                          .append(':').append(y.yakuman).append(':');
                        String tile = YakuCodes.tileOf(y.name);
                        sb.append(tile == null ? "" : tile);
                    }
                }
                sb.append(' ')
                  .append(p.delta[0]).append(',').append(p.delta[1]).append(',')
                  .append(p.delta[2]).append(',').append(p.delta[3]).append(' ')
                  .append(p.winnerGain).append(' ').append(p.winnerPoints).append(' ')
                  .append(p.riichiTaken).append('\n');
                out.write(sb.toString());
                rows++;
            }
        }
        double sec = (System.nanoTime() - t0) / 1e9;
        System.err.printf("[ScoreProbe] 语料 %d 行，用时 %.3f s（%.0f 行/秒） 校验和 %d%n",
                          rows, sec, rows / sec, checksum);
    }

    private ScoreProbe() {
    }
}
