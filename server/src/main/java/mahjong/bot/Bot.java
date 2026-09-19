package mahjong.bot;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import mahjong.core.Meld;
import mahjong.core.Tiles;
import mahjong.game.Round;
import mahjong.rules.Agari;
import mahjong.rules.HandEval;
import mahjong.rules.Shanten;
import mahjong.util.Json;

/** 补位机器人：以向听数为主、进张数为辅的简单牌效 AI。 */
public final class Bot {

    private Bot() {
    }

    /**
     * 自检专用开关：一有机会就开杠。
     *
     * <p><b>正常对局里机器人从不开杠</b>（{@link #decideClaim} 里对大明杠是"简化：不开"，
     * 出牌段也不看 `kan` 选项）—— 后果是**整条「开杠 → 从岭上摸牌」的路径在整场模拟里一次都没走过**，
     * 于是 `dead_wall_left` 只在开局发过一次这种 bug 没人发现（报障：「杠后岭上牌并没有减少」）。
     * 自检把开关打开，让机器人真的开杠，从而覆盖：岭上摸牌、岭上开花、新宝牌指示牌、四杠散了。
     *
     * <p>默认 {@code false}；只有 {@code SelfTest.rinshanTests} 会临时打开并在 finally 里复位。
     */
    public static boolean debugAlwaysKan;

    /**
     * 自检专用：把出牌段开杠时上报的 `tile` 换成这个牌码（默认 {@code null} = 不换）。
     *
     * <p>用来构造**非法/过期**的杠动作（例如 {@code "9z"} 这种根本不存在的牌码），
     * 验证服务端不会因此白摸一张岭上牌、也不会吃掉一次开杠名额
     * （见 {@code Round.play()} 里「杠没成立就退回摸切」的兜底与 {@code SelfTest.kanLimitTests}）。
     */
    public static String debugKanTileOverride;

    /** 从 `kan` 选项里挑第一组可执行的开杠，拼成回包（形状与客户端一致）。 */
    private static Map<String, Object> firstKan(Map<String, Object> kanOption) {
        List<Object> kans = Json.list(kanOption, "kans");
        if (kans == null || kans.isEmpty()) {
            return null;
        }
        Object first = kans.get(0);
        if (!(first instanceof Map)) {
            return null;
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> m = (Map<String, Object>) first;
        Map<String, Object> cmd = Json.obj("type", "kan");
        cmd.put("kind", String.valueOf(m.get("kind")));
        cmd.put("tile", debugKanTileOverride != null ? debugKanTileOverride
                                                    : String.valueOf(m.get("tile")));
        return cmd;
    }

    public static Map<String, Object> decide(Round r, int seat, String kind,
                                             List<Map<String, Object>> options,
                                             Map<String, Object> extra) {
        try {
            if ("turn".equals(kind)) {
                return decideTurn(r, seat, options);
            }
            return decideClaim(r, seat, options, extra);
        } catch (RuntimeException e) {
            return fallback(options);
        }
    }

    private static Map<String, Object> fallback(List<Map<String, Object>> options) {
        for (Map<String, Object> o : options) {
            String t = (String) o.get("type");
            if ("pass".equals(t)) {
                return Json.obj("type", "pass");
            }
        }
        for (Map<String, Object> o : options) {
            String t = (String) o.get("type");
            if ("discard".equals(t)) {
                List<Object> ts = Json.list(o, "tiles");
                if (ts != null && !ts.isEmpty()) {
                    return Json.obj("type", "discard", "tile", ts.get(0));
                }
            }
        }
        return Json.obj("type", "pass");
    }

    // ------------------------------------------------------------- 自家回合

    private static Map<String, Object> decideTurn(Round r, int seat, List<Map<String, Object>> options) {
        Map<String, Object> tsumo = find(options, "tsumo");
        if (tsumo != null) {
            return Json.obj("type", "tsumo");
        }
        // 自检专用：一有机会就开杠（正常对局里机器人**从不**开杠，见下）
        Map<String, Object> debugKan = debugAlwaysKan ? find(options, "kan") : null;
        if (debugKan != null) {
            Map<String, Object> pick = firstKan(debugKan);
            if (pick != null) {
                return pick;
            }
        }
        Map<String, Object> kyuushu = find(options, "kyuushu");
        if (kyuushu != null) {
            int n = yaochuKinds(r, seat);
            // ⚠ 这里原来是 `Math.random()`：自对弈数据与"配对同牌山评测"都要求同种子逐事件可复现，
            //   而这条岔路一旦命中就会让同一副牌打出不同的结果（实测 8 个种子恰好都没触发九种九牌，
            //   所以"看着可复现"—— 正是这种偶发才最毒）。随机源改由 Table 从 seedBase 派生。
            if (n >= 10 || (n == 9 && r.table.botRng().nextDouble() < 0.5)) {
                return Json.obj("type", "kyuushu");
            }
        }
        Map<String, Object> discardOpt = find(options, "discard");
        List<Object> candidates = discardOpt == null ? new ArrayList<>() : Json.list(discardOpt, "tiles");
        if (candidates == null || candidates.isEmpty()) {
            return Json.obj("type", "pass");
        }
        int bestShanten = 99;
        int bestUkeire = -1;
        String bestTile = null;
        for (Object o : candidates) {
            String ts = (String) o;
            int id = resolve(r, seat, ts);
            if (id < 0) {
                continue;
            }
            int[] counts = countsWithout(r, seat, id);
            final int meldCount = r.melds[seat].size();
            int sh = Shanten.min(counts, meldCount);
            // 进张枚数走规则层的公共判据（`HandEval`）。这里传 `null` 可见牌 = "什么也看不见"，
            // 与原来那份私有实现**逐字等价**（`Σ 4 − 手里`），所以教师的行为一字不变；
            // 想让它按实际可见牌算，是下一轮"加强 teacher"的事，不在规则层做。
            // 等价性钉在 `SelfTest.handEvalTests`（把旧公式抄进断言里对拍）。
            int uk = sh <= 0 ? 0
                    : HandEval.advanceTiles(HandEval.advanceKinds(counts, meldCount), counts, null);
            if (sh < bestShanten || (sh == bestShanten && uk > bestUkeire)
                    || (sh == bestShanten && uk == bestUkeire && betterTieBreak(ts, bestTile))) {
                bestShanten = sh;
                bestUkeire = uk;
                bestTile = ts;
            }
        }
        if (bestTile == null) {
            bestTile = (String) candidates.get(0);
        }
        // 立直
        Map<String, Object> riichi = find(options, "riichi");
        if (riichi != null) {
            List<Object> rts = Json.list(riichi, "tiles");
            if (rts != null && rts.contains(bestTile)) {
                return Json.obj("type", "riichi", "tile", bestTile);
            }
        }
        return Json.obj("type", "discard", "tile", bestTile);
    }

    private static boolean betterTieBreak(String a, String b) {
        if (b == null) {
            return true;
        }
        return isolateScore(b) < isolateScore(a);
    }

    /** 越"孤立"的牌越优先打出。 */
    private static int isolateScore(String t) {
        int k = Tiles.parseKind(t);
        if (k < 0) {
            return -1;
        }
        if (Tiles.isHonor(k)) {
            return 0;
        }
        int n = Tiles.num(k);
        return Math.min(Math.abs(n - 5), 4);
    }

    private static int yaochuKinds(Round r, int seat) {
        int[] c = r.concealCounts(seat);
        int n = 0;
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (c[k] > 0 && Tiles.isYaochu(k)) {
                n++;
            }
        }
        return n;
    }

    private static int resolve(Round r, int seat, String s) {
        int kind = Tiles.parseKind(s);
        if (kind < 0) {
            return -1;
        }
        boolean red = Tiles.isRedStr(s);
        int fb = -1;
        for (int id : r.hand[seat]) {
            if (Tiles.kind(id) != kind) {
                continue;
            }
            if (red && Tiles.isRedId(id)) {
                return id;
            }
            if (!red) {
                if (!Tiles.isRedId(id)) {
                    return id;
                }
                fb = id;
            }
        }
        return fb;
    }

    private static int[] countsWithout(Round r, int seat, int removeId) {
        int[] c = new int[Tiles.KIND_COUNT];
        boolean removed = false;
        for (int id : r.hand[seat]) {
            if (!removed && id == removeId) {
                removed = true;
                continue;
            }
            c[Tiles.kind(id)]++;
        }
        return c;
    }

    // ------------------------------------------------------------- 鸣牌

    private static Map<String, Object> decideClaim(Round r, int seat,
                                                   List<Map<String, Object>> options,
                                                   Map<String, Object> extra) {
        if (find(options, "ron") != null) {
            return Json.obj("type", "ron");
        }
        // 自检专用：能大明杠就杠（正常对局里机器人不开杠，见 debugAlwaysKan）
        if (debugAlwaysKan && find(options, "kan") != null) {
            return Json.obj("type", "kan");
        }
        int cur = currentShanten(r, seat);
        int meldCount = r.melds[seat].size();
        int handSize = r.hand[seat].size();

        Map<String, Object> pon = find(options, "pon");
        if (pon != null && extra != null) {
            int kind = Tiles.parseKind(Json.str(extra, "tile", ""));
            if (kind >= 0) {
                int[] c = r.concealCounts(seat);
                if (c[kind] >= 2) {
                    c[kind] -= 2;
                    int after = bestShantenAfterCall(c, meldCount + 1, handSize - 2 + 1);
                    if (after < cur) {
                        return Json.obj("type", "pon");
                    }
                }
            }
        }
        Map<String, Object> chi = find(options, "chi");
        if (chi != null && extra != null) {
            int kind = Tiles.parseKind(Json.str(extra, "tile", ""));
            List<Object> sets = Json.list(chi, "sets");
            if (kind >= 0 && sets != null) {
                int best = 99;
                for (Object so : sets) {
                    List<Object> pair = Json.asArr(so);
                    if (pair == null || pair.size() != 2) {
                        continue;
                    }
                    int[] c = r.concealCounts(seat);
                    boolean ok = true;
                    for (Object t : pair) {
                        int k = Tiles.parseKind((String) t);
                        if (k < 0 || c[k] <= 0) {
                            ok = false;
                            break;
                        }
                        c[k]--;
                    }
                    if (!ok) {
                        continue;
                    }
                    int after = bestShantenAfterCall(c, meldCount + 1, handSize - 2 + 1);
                    best = Math.min(best, after);
                }
                if (best < cur) {
                    return chooseChi(r, seat, sets);
                }
            }
        }
        Map<String, Object> kan = find(options, "kan");
        if (kan != null && extra != null && r.melds[seat].isEmpty()) {
            // 机器人大明杠保守处理：仅在明显损失不大时开杠（简化：不开）
        }
        return Json.obj("type", "pass");
    }

    /** 鸣牌后（多一张牌待打）能达到的最好向听。 */
    private static int bestShantenAfterCall(int[] countsAfterCall, int meldCount, int handSize) {
        int extra = handSize - (13 - 3 * meldCount);
        int best = 99;
        if (extra <= 0) {
            return Shanten.min(countsAfterCall, meldCount);
        }
        int[] c = countsAfterCall.clone();
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (c[k] == 0) {
                continue;
            }
            c[k]--;
            best = Math.min(best, Shanten.min(c, meldCount));
            c[k]++;
        }
        return best;
    }

    private static int currentShanten(Round r, int seat) {
        return Shanten.min(r.concealCounts(seat), r.melds[seat].size());
    }

    private static Map<String, Object> chooseChi(Round r, int seat, List<Object> sets) {
        int bestAfter = 99;
        List<Object> bestSet = null;
        int meldCount = r.melds[seat].size();
        int handSize = r.hand[seat].size();
        for (Object so : sets) {
            List<Object> pair = Json.asArr(so);
            if (pair == null || pair.size() != 2) {
                continue;
            }
            int[] c = r.concealCounts(seat);
            boolean ok = true;
            for (Object t : pair) {
                int k = Tiles.parseKind((String) t);
                if (k < 0 || c[k] <= 0) {
                    ok = false;
                    break;
                }
                c[k]--;
            }
            if (!ok) {
                continue;
            }
            int after = bestShantenAfterCall(c, meldCount + 1, handSize - 2 + 1);
            if (after < bestAfter) {
                bestAfter = after;
                bestSet = pair;
            }
        }
        if (bestSet == null) {
            return Json.obj("type", "pass");
        }
        return Json.obj("type", "chi", "tiles", bestSet);
    }

    private static Map<String, Object> find(List<Map<String, Object>> options, String type) {
        if (options == null) {
            return null;
        }
        for (Map<String, Object> o : options) {
            if (type.equals(o.get("type"))) {
                return o;
            }
        }
        return null;
    }

    /** 听牌种类（供外部调试）。 */
    public static Set<Integer> waits(Round r, int seat) {
        return new LinkedHashSet<>(Agari.waits(r.concealCounts(seat), r.melds[seat].size()));
    }

    /** 副露面子占位，避免 IDE 警告未使用。 */
    static int meldCount(List<Meld> melds) {
        return melds.size();
    }
}
