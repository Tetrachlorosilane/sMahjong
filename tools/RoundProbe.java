// Java 侧**只读探针**：用真实的 `Table` + 真实策略跑整场，把**每个自家回合询问**的
// 「局面 + Java 自己生成的选项」按固定文本打出来，供 C++ 对拍。
//
//   java -cp server\build\mahjong-server.jar tools\RoundProbe.java <hands> <policy> <seed> <out> [games]
//
// 为什么这么写（对拍契约，见 docs/TRAINER-CPP.md §4）：
//   · 走**真实牌局**（`Table.playGame()`）而不是构造局面 —— 状态由 Java 自己推进，
//     探针只读不判；
//   · 选项文本**直接来自 `Decision.options`**（Java 自己的产物），不在探针里重写生成逻辑；
//   · 只有 `forbiddenDiscard` 是 private，用反射读（只读，不改）。
//
// 一行格式（空格分隔的 token，全部 ASCII）：
//   t <seat> <drawnId> <rinshan> <atLastLive> <handIds> <melds> <menzen> <riichi> <dblRiichi>
//     <ippatsu> <scores4> <roundWind> <kyoku> <honba> <dealer> <tilesLeft> <deadWallLeft>
//     <kanCount> <anyCall> <playerDraws> <doraKinds> <uraKinds> <forbiddenKinds> <preset>
//     @ <javaOptions>
// `javaOptions` 的规范文本（C++ 侧必须逐字符一致）：
//   discard=<码,码>  riichi=<码,码>  tsumo  kyuushu  kan=<kind:码,kind:码>   以 `;` 连接
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.FileWriter;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import mahjong.ai.Decision;
import mahjong.ai.Policies;
import mahjong.core.Meld;
import mahjong.core.Rules;
import mahjong.core.Tiles;
import mahjong.game.Round;
import mahjong.game.Table;
import mahjong.train.SelfPlay;

public final class RoundProbe {

    private static BufferedWriter out;
    private static long rows;
    private static long checksum;
    private static long claims;
    private static Field forbiddenField;

    public static void main(String[] args) throws Exception {
        if (args.length < 4) {
            System.err.println("用法：RoundProbe <hands> <policy> <seed> <out> [games]");
            System.exit(2);
        }
        int hands = Integer.parseInt(args[0]);
        String policy = args[1];
        long seed = Long.parseLong(args[2]);
        int games = args.length > 4 ? Integer.parseInt(args[4]) : 1;
        try {
            forbiddenField = Round.class.getDeclaredField("forbiddenDiscard");
            forbiddenField.setAccessible(true);
        } catch (ReflectiveOperationException e) {
            System.err.println("读不到 forbiddenDiscard：" + e);
            System.exit(2);
        }
        out = new BufferedWriter(new FileWriter(args[3]));
        for (int g = 0; g < games; g++) {
            final long gameSeed = SelfPlay.seedFor(seed, g);
            Table t = new Table("probe", "probe", Rules.defaults());
            t.botDelayMs = 0;
            t.roundDelayMs = 0;
            t.debugDeterministicSeed = true;
            t.seedBase = gameSeed;
            t.debugMaxHands = hands;
            for (int i = 0; i < 4; i++) {
                t.policy[i] = Policies.byName(policy).create(i, gameSeed);
                t.addBot(i);
            }
            t.debugDecisionTap = (Decision d) -> {
                try {
                    onDecision(d);
                } catch (Exception e) {
                    throw new RuntimeException(e);
                }
            };
            t.playGame();
        }
        out.close();
        System.err.printf("[RoundProbe] 策略 %s 局数/场 %d 场数 %d → %d 行（其中鸣牌询问 %d）校验和 %d%n",
                policy, hands, games, rows, claims, checksum);
    }

    private static void onDecision(Decision d) throws Exception {
        if (!"turn".equals(d.kind)) {
            claims++;
            return;
        }
        Round r = d.round;
        int seat = d.obs.seat;
        StringBuilder sb = new StringBuilder(512);
        sb.append("t ").append(seat).append(' ');
        sb.append(d.obs.drawn == null ? "-" : d.obs.drawn).append(' ');
        sb.append(d.obs.rinshan ? 1 : 0).append(' ');
        sb.append(r.atLastLiveTile() ? 1 : 0).append(' ');
        sb.append(joinInts(r.hand[seat])).append(' ');
        sb.append(meldsSpec(r.melds[seat])).append(' ');
        sb.append(r.menzen[seat] ? 1 : 0).append(' ');
        sb.append(r.riichi[seat] ? 1 : 0).append(' ');
        sb.append(r.doubleRiichi[seat] ? 1 : 0).append(' ');
        sb.append(r.ippatsu[seat] ? 1 : 0).append(' ');
        sb.append(r.scores[0]).append(',').append(r.scores[1]).append(',')
          .append(r.scores[2]).append(',').append(r.scores[3]).append(' ');
        sb.append(r.roundWind).append(' ').append(r.kyoku).append(' ').append(r.honba).append(' ');
        sb.append(r.dealer).append(' ');
        sb.append(r.tilesLeft()).append(' ').append(r.deadWallLeft()).append(' ');
        sb.append(r.kanCount).append(' ');
        sb.append(r.anyCall ? 1 : 0).append(' ');
        sb.append(r.playerDraws[seat]).append(' ');
        sb.append(kinds(r.doraIndicators())).append(' ');
        sb.append(kinds(r.uraIndicators())).append(' ');
        sb.append(forbiddenSpec(r)).append(' ');
        sb.append(r.rules.preset);
        sb.append(" @ ").append(optionsText(d.options));
        sb.append('\n');
        String line = sb.toString();
        out.write(line);
        rows++;
        for (int i = 0; i < line.length(); i++) {
            checksum = checksum * 131 + line.charAt(i);
        }
    }

    /** `forbiddenDiscard`（食替禁打牌种）—— private，只能反射读。 */
    @SuppressWarnings("unchecked")
    private static String forbiddenSpec(Round r) throws Exception {
        Set<Integer> s = (Set<Integer>) forbiddenField.get(r);
        if (s == null || s.isEmpty()) {
            return "-";
        }
        StringBuilder sb = new StringBuilder();
        for (int k : new LinkedHashSet<>(s)) {
            if (sb.length() > 0) {
                sb.append(',');
            }
            sb.append(k);
        }
        return sb.toString();
    }

    private static String meldsSpec(List<Meld> melds) {
        if (melds == null || melds.isEmpty()) {
            return "-";
        }
        StringBuilder sb = new StringBuilder();
        for (Meld m : melds) {
            if (sb.length() > 0) {
                sb.append(';');
            }
            sb.append(m.kind.wire()).append(':').append(m.from).append(':').append(m.calledId)
              .append(':').append(joinInts(m.tiles));
        }
        return sb.toString();
    }

    private static String joinInts(int[] a) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < a.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(a[i]);
        }
        return sb.toString();
    }

    private static String joinInts(List<Integer> a) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < a.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(a.get(i));
        }
        return sb.toString();
    }

    private static String kinds(List<Integer> a) {
        if (a == null || a.isEmpty()) {
            return "-";
        }
        return joinInts(a);
    }

    /** 选项列表 → 规范文本（与 C++ 侧同一套口径）。 */
    @SuppressWarnings("unchecked")
    private static String optionsText(List<Map<String, Object>> options) {
        StringBuilder sb = new StringBuilder();
        for (Map<String, Object> o : options) {
            String type = String.valueOf(o.get("type"));
            if (sb.length() > 0) {
                sb.append(';');
            }
            if ("discard".equals(type) || "riichi".equals(type)) {
                List<Object> tiles = (List<Object>) o.get("tiles");
                sb.append(type).append('=');
                for (int i = 0; i < tiles.size(); i++) {
                    if (i > 0) {
                        sb.append(',');
                    }
                    sb.append(tiles.get(i));
                }
            } else if ("kan".equals(type)) {
                List<Object> kans = (List<Object>) o.get("kans");
                sb.append("kan=");
                for (int i = 0; i < kans.size(); i++) {
                    Map<String, Object> k = (Map<String, Object>) kans.get(i);
                    if (i > 0) {
                        sb.append(',');
                    }
                    sb.append(k.get("kind")).append(':').append(k.get("tile"));
                }
            } else {
                sb.append(type);
            }
        }
        return sb.toString();
    }
}
