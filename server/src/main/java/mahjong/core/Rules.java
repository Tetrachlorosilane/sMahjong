package mahjong.core;

import java.util.Map;

import mahjong.util.Json;

/** 规则配置。字段含义见 docs/PROTOCOL.md §5。 */
public final class Rules {

    /** "tonpuu"（东风战）或 "hanchan"（半庄）。 */
    public String length = "hanchan";
    /** 赤宝牌数量：0 / 3 / 4。 */
    public int aka = 3;
    public boolean kuitan = true;
    public boolean ura = true;
    public boolean kanDora = true;
    public boolean doubleYakuman = true;
    /** "off" / "mangan" / "yakuman"。 */
    public String renhou = "off";
    public boolean headBump = false;
    public boolean sanchaAbort = false;
    public boolean fourRiichiAbort = true;
    public boolean fourKanAbort = true;
    public boolean fourWindAbort = true;
    public boolean kyuushuAbort = true;
    public boolean nagashiMangan = true;
    public boolean tobi = true;
    public boolean agariyame = true;
    public boolean westExtension = false;
    public boolean kuikae = true;
    public boolean pao = true;
    public boolean koyaku = false;
    public int notenPenalty = 3000;
    public int startScore = 25000;
    public int returnScore = 30000;
    public int[] uma = {15, 5, -5, -15};
    /**
     * 每巡基本时长（毫秒）。这段时间内出牌**不消耗**总额外时长。
     */
    public int thinkingBaseMs = 15000;
    /**
     * 总额外时长（毫秒）。超出每巡基本时长的部分从这里扣，扣减按 1 秒离散、
     * 剩余量向上去整（即偏向玩家多给）。
     */
    public int thinkingBankMs = 0;
    /** 兼容旧字段：thinking_ms 等价于「固定思考时长」，即 base=thinking_ms、bank=0。 */
    public int thinkingMs = 15000;
    public int minHan = 1;

    public static Rules defaults() {
        return new Rules();
    }

    public static Rules fromJson(Map<String, Object> m) {
        Rules r = new Rules();
        if (m == null) {
            return r;
        }
        r.length = Json.str(m, "length", r.length);
        r.aka = Json.i(m, "aka", r.aka);
        r.kuitan = Json.bool(m, "kuitan", r.kuitan);
        r.ura = Json.bool(m, "ura", r.ura);
        r.kanDora = Json.bool(m, "kan_dora", r.kanDora);
        r.doubleYakuman = Json.bool(m, "double_yakuman", r.doubleYakuman);
        r.renhou = Json.str(m, "renhou", r.renhou);
        r.headBump = Json.bool(m, "head_bump", r.headBump);
        r.sanchaAbort = Json.bool(m, "sancha_abort", r.sanchaAbort);
        r.fourRiichiAbort = Json.bool(m, "four_riichi_abort", r.fourRiichiAbort);
        r.fourKanAbort = Json.bool(m, "four_kan_abort", r.fourKanAbort);
        r.fourWindAbort = Json.bool(m, "four_wind_abort", r.fourWindAbort);
        r.kyuushuAbort = Json.bool(m, "kyuushu_abort", r.kyuushuAbort);
        r.nagashiMangan = Json.bool(m, "nagashi_mangan", r.nagashiMangan);
        r.tobi = Json.bool(m, "tobi", r.tobi);
        r.agariyame = Json.bool(m, "agariyame", r.agariyame);
        r.westExtension = Json.bool(m, "west_extension", r.westExtension);
        r.kuikae = Json.bool(m, "kuikae", r.kuikae);
        r.pao = Json.bool(m, "pao", r.pao);
        r.koyaku = Json.bool(m, "koyaku", r.koyaku);
        r.notenPenalty = Json.i(m, "noten_penalty", r.notenPenalty);
        r.startScore = Json.i(m, "start_score", r.startScore);
        r.returnScore = Json.i(m, "return_score", r.returnScore);
        r.thinkingBaseMs = Json.i(m, "thinking_base_ms", r.thinkingBaseMs);
        r.thinkingBankMs = Json.i(m, "thinking_bank_ms", r.thinkingBankMs);
        // 旧字段 thinking_ms：等价于固定时长（base = bank 之前的那段，无额外时长）
        if (m.containsKey("thinking_ms")) {
            r.thinkingBaseMs = Json.i(m, "thinking_ms", r.thinkingBaseMs);
            r.thinkingBankMs = 0;
        }
        r.thinkingMs = r.thinkingBaseMs;
        r.minHan = Json.i(m, "min_han", r.minHan);
        java.util.List<Object> uma = Json.list(m, "uma");
        if (uma != null && uma.size() == 4) {
            int[] u = new int[4];
            for (int i = 0; i < 4; i++) {
                Object o = uma.get(i);
                // 元素类型也要挡：`"uma":["a","b","c","d"]` 会在强转时抛 ClassCastException
                // （被读线程的 catch 兜住，只回一个 internal，但没必要让它发生）。
                u[i] = (o instanceof Number) ? clamp(((Number) o).intValue(), -1000, 1000) : r.uma[i];
            }
            r.uma = u;
        }
        r.clampToSane();
        return r;
    }

    /**
     * 逐项钳制客户端传来的规则数值（建房间时 `create_room.rules`，见 PROTOCOL §5）。
     *
     * <p>不校验的话，几条普通数字就能把服务端玩坏（AUDIT F11）：
     * <ul>
     *   <li>{@code thinking_base_ms: 2147483647} → 服务端真的会等约 24 天才代打（牌桌线程挂死）；</li>
     *   <li>{@code start_score: 2147483647} → 结算累加溢出，终局顺位按负数排；</li>
     *   <li>{@code min_han}/{@code aka} 超出范围 → 役种判定读到的规则自相矛盾。</li>
     * </ul>
     * 罚符的**零和**不靠这里钳制，而是由 {@code Payments.notenPenalty} 按"收方定额、
     * 付方凑齐"构造保证（这样任何罚符值都不会凭空生灭点数）。
     */
    private void clampToSane() {
        aka = clamp(aka, 0, 3);
        notenPenalty = clamp(notenPenalty, 0, 12000);
        startScore = clamp(startScore, 1000, 1000000);
        returnScore = clamp(returnScore, 1000, 1000000);
        thinkingBaseMs = clamp(thinkingBaseMs, 1000, 60000);
        thinkingBankMs = clamp(thinkingBankMs, 0, 600000);
        thinkingMs = thinkingBaseMs;
        minHan = clamp(minHan, 1, 13);
    }

    private static int clamp(int v, int lo, int hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    public Map<String, Object> toJson() {
        return Json.obj(
                "length", length,
                "aka", aka,
                "kuitan", kuitan,
                "ura", ura,
                "kan_dora", kanDora,
                "double_yakuman", doubleYakuman,
                "renhou", renhou,
                "head_bump", headBump,
                "sancha_abort", sanchaAbort,
                "four_riichi_abort", fourRiichiAbort,
                "four_kan_abort", fourKanAbort,
                "four_wind_abort", fourWindAbort,
                "kyuushu_abort", kyuushuAbort,
                "nagashi_mangan", nagashiMangan,
                "tobi", tobi,
                "agariyame", agariyame,
                "west_extension", westExtension,
                "kuikae", kuikae,
                "pao", pao,
                "koyaku", koyaku,
                "noten_penalty", notenPenalty,
                "start_score", startScore,
                "return_score", returnScore,
                "uma", Json.arr(uma[0], uma[1], uma[2], uma[3]),
                "thinking_base_ms", thinkingBaseMs,
                "thinking_bank_ms", thinkingBankMs,
                "thinking_ms", thinkingBaseMs,   // 兼容旧客户端
                "min_han", minHan);
    }

    /** 该规则下可用赤宝牌种类（kind 列表）。 */
    public int[] akaKinds() {
        if (aka <= 0) {
            return new int[0];
        }
        if (aka == 3) {
            return new int[]{Tiles.AKA_M, Tiles.AKA_P, Tiles.AKA_S};
        }
        // 4 张：0m 0p 0p 0s
        return new int[]{Tiles.AKA_M, Tiles.AKA_P, Tiles.AKA_P, Tiles.AKA_S};
    }
}
