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
    /** 配给原点（开局持有）。M.League /《天凤》四人 = 25000。 */
    public int startScore = 25000;
    /**
     * 返点 = **精算基准点数**（`(点数 − 返点)/1000 + 马点 + 头名赏`）。
     *
     * <p>头名赏 = {@code (返点 − 原点)/1000 × 4} → M.League /《天凤》= 20，
     * 《雀魂》段位场「精算基准与配给原点相同（25000）」→ 无头名赏
     * （见 `docs/日本麻将.md` §精算点数）。⚠《雀魂》的**一位必要点数**（和了止/续行）是 30000，
     * 与精算基准不同 —— 本项目把它拆成两个概念不合适，所以这一项只当**精算基准**用；
     * 唯一的另一位必要点数使用者是西入（`westExtension`，三套预设都是关）。
     */
    public int returnScore = 30000;
    public int[] uma = {15, 5, -5, -15};
    /**
     * 每巡基本时长（毫秒）。这段时间内出牌**不消耗**总额外时长。
     *
     * <p>默认 `20+5` = **额外 20s + 每巡 5s**：客户端大厅的默认值本来就是这一组，
     * 而服务端字段原来写的是 `0+15`（固定 15s）—— 于是"机器人补位牌桌 / 脚本客户端 /
     * 真人建房"三条路径的钟模型不一致（批次三统一到**较大**的一侧，见 AUDIT §1.10）。
     * ⚠ 规格写作「额外+每巡」，**别把 `20+5` 读成"每巡 20s"**（见 PROTOCOL §5.1）。
     */
    public int thinkingBaseMs = 5000;
    /**
     * 总额外时长（毫秒）。超出每巡基本时长的部分从这里扣，扣减按 1 秒离散、
     * 剩余量向上去整（即偏向玩家多给）。**每小局重置**（`Table.playGame`）。
     */
    public int thinkingBankMs = 20000;
    /** 兼容旧字段：thinking_ms 等价于「固定思考时长」，即 base=thinking_ms、bank=0。 */
    public int thinkingMs = 5000;
    public int minHan = 1;

    /**
     * **一位必要点数**（`docs/日本麻将.md` L143/L145/L157/L116）：决定
     * ①「All Last 轮庄后要不要进延长战」②「All Last 庄家能不能和了止/听牌止」的点数门槛。
     *
     * <p>M.League **不使用**（= 0，打到南 4 局庄家轮庄为止）；《天凤》《雀魂》段位场 = **30000**。
     * ⚠ 它与 {@link #returnScore}（返点 = **精算**基准）是两个概念 ——《雀魂》正是
     * 「一位必要点数 30000 + 精算基准 25000」，所以不能拿 `returnScore` 当门槛
     * （原来西入那一支就是这么写的，见 AUDIT S-66）。
     * 0 = 不要求（任何分数都算达到）。
     */
    public int requiredPoints = 0;

    // ================================================================= M.League 差异
    // 依据 docs/日本麻将.md（2026-09-14 版，各节都补了《雀魂》《天凤》与 M.League 的差异说明）。

    /** 规则预设：{@code "mleague"}（默认）/ {@code "tenhou"} / {@code "majsoul"} / {@code "custom"}。 */
    public String preset = "mleague";
    /**
     * 切上满贯：3 番 60 符、4 番 30 符（基本点 1920）按**满贯**计。
     * M.League 采用；《雀魂》《天凤》不采用。
     */
    public boolean kiriageMangan = false;
    /**
     * 累计役满：番数 ≥13 且没有役满役时按役满（基本点 8000）计。
     * 《雀魂》《天凤》采用；**M.League 以三倍满（6000）为普通役上限**。
     */
    public boolean kazoeYakuman = true;
    /**
     * 连风牌（场风与自风相同的雀头）的符数：**M.League 为 2**，其余规则 4（自风 2 + 场风 2 叠加）。
     */
    public int doubleWindPairFu = 4;
    /** 立直所需最低点数（《雀魂》《天凤》= 1000；**M.League 无此要求** = 0）。 */
    public int riichiMinScore = 1000;
    /** 立直所需剩余可摸牌数（《雀魂》《天凤》= 4；**M.League 无此要求** = 0）。 */
    public int riichiMinTilesLeft = 4;
    /** 摸到海底牌后不允许立直（**M.League 为 true**）。 */
    public boolean riichiNoHaitei = false;
    /** 立直后的暗杠除「所听牌不变」外，还要求**面子构成不变**（M.League；《雀魂》《天凤》只看听牌）。 */
    public boolean ankanKeepsShape = false;
    /** 终局同点时**平分**对应名次的加点（M.League）；否则按起家座次先后定名次。 */
    public boolean tieSplitPoint = false;
    /**
     * 四杠子包牌：由他家的舍张**大明杠**完成第 4 个杠时，那家包牌。
     * **M.League 采用**，《雀魂》《天凤》不采用（它们只对大三元、大四喜包牌）。
     */
    public boolean paoFourKan = false;
    /**
     * **天和时如果成立国士无双，视作成立国士无双十三面**（`docs/日本麻将.md` L1149）。
     *
     * <p>**《雀魂》采用**；《天凤》与 M.League 不采用（那时天和国士就是"国士无双 + 天和"两个役满复合）。
     * ⚠ 只有**天和**（庄家第一巡自摸）算，**地和不算**（原文只写天和）；
     * 而且只在牌型本来就是国士无双（**非**十三面听）时把它"升级"为十三面 ——
     * 十三面本身就是十三面，不会重复计。
     */
    public boolean kokushiTenhou13 = false;
    /**
     * 包牌承担**全部**役满得点（《天凤》）而不是只包「被包的那一役」的基本点
     * （《雀魂》/ M.League —— 复合了别的役满时，别的役满仍由放铳者照常支付）。
     */
    public boolean paoCoversAll = false;

    public Rules() {
        applyPreset(preset);
    }

    /**
     * 铺一套预设值（不改思考时间）。{@code "custom"} 表示不铺、保留当前各项。
     *
     * <p>调用顺序很重要：{@link #fromJson} 先铺预设，再让报文里的单项字段覆盖，
     * 所以「选 M.League 再把某一条改掉」是支持的。
     */
    public void applyPreset(String name) {
        preset = (name == null || name.isEmpty()) ? "custom" : name;
        switch (preset) {
            case "mleague":
                // 赤 3、食断 + 后付、里宝/杠宝/杠里宝、无古役、常时一番缚
                aka = 3; kuitan = true; ura = true; kanDora = true; koyaku = false; minHan = 1;
                // 4 种役满不加倍；无中途流局（含三家和了 → 头跳）；无流局满贯
                doubleYakuman = false; renhou = "off"; headBump = true; sanchaAbort = false;
                fourRiichiAbort = false; fourKanAbort = false; fourWindAbort = false; kyuushuAbort = false;
                nagashiMangan = false;
                // 无击飞、无和了止、无西入（南 4 局庄家轮庄即终局）；**无一位必要点数**
                tobi = false; agariyame = false; westExtension = false; requiredPoints = 0;
                // 食い替え禁止、包牌（含四杠子；暗杠计入大三元/大四喜的个数判定）
                kuikae = true; pao = true; paoFourKan = true; paoCoversAll = false;
                // 25000 配给 / 30000 返还、马点 10-30 + 头名赏、同点平分加点
                notenPenalty = 3000; startScore = 25000; returnScore = 30000;
                uma = new int[]{30, 10, -10, -30}; tieSplitPoint = true;
                // 计分：切上满贯、无累计役满、连风雀头 2 符
                kiriageMangan = true; kazoeYakuman = false; doubleWindPairFu = 2;
                // 立直：无 1000 点/残牌要求，但摸到海底后不可立直；立直后暗杠要求面子构成不变
                riichiMinScore = 0; riichiMinTilesLeft = 0; riichiNoHaitei = true; ankanKeepsShape = true;
                break;
            case "tenhou":
                aka = 3; kuitan = true; ura = true; kanDora = true; koyaku = false; minHan = 1;
                doubleYakuman = false; renhou = "off"; headBump = false; sanchaAbort = true;
                fourRiichiAbort = true; fourKanAbort = true; fourWindAbort = true; kyuushuAbort = true;
                nagashiMangan = true; tobi = true; agariyame = true; westExtension = false;
                // 《天凤》：All Last 庄家**达到一位必要点数且为 1 位**时才和了止/听牌止
                //（文档 L116），西入的门槛也是它（L145：达到 30000 即可结束）
                requiredPoints = 30000;
                // 包牌只到「大三元 / 大四喜」，但**包牌承担复合后的全部役满得点**
                kuikae = true; pao = true; paoFourKan = false; paoCoversAll = true;
                notenPenalty = 3000; startScore = 25000; returnScore = 30000;
                uma = new int[]{20, 10, -10, -20}; tieSplitPoint = false;
                kiriageMangan = false; kazoeYakuman = true; doubleWindPairFu = 4;
                riichiMinScore = 1000; riichiMinTilesLeft = 4; riichiNoHaitei = false; ankanKeepsShape = false;
                break;
            case "majsoul":
                aka = 3; kuitan = true; ura = true; kanDora = true; koyaku = false; minHan = 1;
                doubleYakuman = true; renhou = "off"; headBump = false; sanchaAbort = false;
                fourRiichiAbort = true; fourKanAbort = true; fourWindAbort = true; kyuushuAbort = true;
                nagashiMangan = true; tobi = true; agariyame = true; westExtension = false;
                // 《雀魂》四人段位场：**一位必要点数 = 30000**（文档 L157）
                requiredPoints = 30000;
                // 《雀魂》特有：**天和时国士无双视作国士无双十三面**（文档 L1149）
                kokushiTenhou13 = true;
                // 包牌：只到大三元 / 大四喜，且**只包被包的那一役**（与 M.League 同侧）
                kuikae = true; pao = true; paoFourKan = false; paoCoversAll = false;
                // 《雀魂》段位场：精算基准 = 配给原点 25000（**没有头名赏**），
                // 与它的「一位必要点数 30000」是两个数 —— 现已拆成 `requiredPoints` 两个字段。
                notenPenalty = 3000; startScore = 25000; returnScore = 25000;
                uma = new int[]{15, 5, -5, -15}; tieSplitPoint = false;
                kiriageMangan = false; kazoeYakuman = true; doubleWindPairFu = 4;
                riichiMinScore = 1000; riichiMinTilesLeft = 4; riichiNoHaitei = false; ankanKeepsShape = false;
                break;
            default:
                break;
        }
    }

    public static Rules defaults() {
        return new Rules();
    }

    public static Rules fromJson(Map<String, Object> m) {
        Rules r = new Rules();
        if (m == null) {
            return r;
        }
        // ⚠ 先铺预设：报文里没提到的字段沿用预设值，提到了才覆盖
        r.applyPreset(Json.str(m, "preset", r.preset));
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
        r.requiredPoints = Json.i(m, "required_points", r.requiredPoints);
        r.kokushiTenhou13 = Json.bool(m, "kokushi_tenhou_13", r.kokushiTenhou13);
        r.kiriageMangan = Json.bool(m, "kiriage_mangan", r.kiriageMangan);
        r.kazoeYakuman = Json.bool(m, "kazoe_yakuman", r.kazoeYakuman);
        r.doubleWindPairFu = Json.i(m, "double_wind_pair_fu", r.doubleWindPairFu);
        r.riichiMinScore = Json.i(m, "riichi_min_score", r.riichiMinScore);
        r.riichiMinTilesLeft = Json.i(m, "riichi_min_tiles_left", r.riichiMinTilesLeft);
        r.riichiNoHaitei = Json.bool(m, "riichi_no_haitei", r.riichiNoHaitei);
        r.ankanKeepsShape = Json.bool(m, "ankan_keeps_shape", r.ankanKeepsShape);
        r.tieSplitPoint = Json.bool(m, "tie_split_point", r.tieSplitPoint);
        r.paoFourKan = Json.bool(m, "pao_four_kan", r.paoFourKan);
        r.paoCoversAll = Json.bool(m, "pao_covers_all", r.paoCoversAll);
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
        doubleWindPairFu = clamp(doubleWindPairFu, 0, 4);
        riichiMinScore = clamp(riichiMinScore, 0, 1000000);
        riichiMinTilesLeft = clamp(riichiMinTilesLeft, 0, 69);
        requiredPoints = clamp(requiredPoints, 0, 1000000);
    }

    private static int clamp(int v, int lo, int hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    public Map<String, Object> toJson() {
        return Json.obj(
                "preset", preset,
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
                "min_han", minHan,
                "required_points", requiredPoints,
                "kokushi_tenhou_13", kokushiTenhou13,
                "kiriage_mangan", kiriageMangan,
                "kazoe_yakuman", kazoeYakuman,
                "double_wind_pair_fu", doubleWindPairFu,
                "riichi_min_score", riichiMinScore,
                "riichi_min_tiles_left", riichiMinTilesLeft,
                "riichi_no_haitei", riichiNoHaitei,
                "ankan_keeps_shape", ankanKeepsShape,
                "tie_split_point", tieSplitPoint,
                "pao_four_kan", paoFourKan,
                "pao_covers_all", paoCoversAll);
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
