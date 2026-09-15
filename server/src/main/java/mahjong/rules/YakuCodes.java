package mahjong.rules;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 协议词汇表：**役种名 / 打点档位 / 流局原因 → ASCII 码**。
 *
 * <p>报文里不出现中文（`docs/PROTOCOL.md` §0）：服务端只上报稳定 ASCII 码，
 * 由客户端查语言文件（`client/assets/i18n/*.json`）翻成显示文本。
 * 好处：报文可 grep、跨端不依赖字符集、换文案/加语言只动客户端资源。
 *
 * <p>本表是**唯一**的中文名 → 码映射处：`Evaluator` 内部仍用中文名（日志、自测可读），
 * 但 {@code sendAgari} 只发码。所以**新增役种必须在这里登记**，否则
 * 自检的整场模拟会统计到 `misses() > 0` 而失败。
 *
 * <p>参数化役种（「役牌 白」「场风 东」「自风 北」）拆成 `code` + `tile` 两个字段：
 * 码是角色（`yakuhai`），牌码是 ASCII 的 `1z..7z`，客户端用模板拼显示文本
 * （模板里有 `%1` 占位）。
 */
public final class YakuCodes {

    private YakuCodes() {
    }

    /** 参数化役种的码（与牌码 `tile` 组合）。 */
    public static final String YAKUHAI = "yakuhai";
    public static final String ROUND_WIND = "round_wind";
    public static final String SEAT_WIND = "seat_wind";

    /** 查不到的兜底码（客户端会显示成"未知役种"）。 */
    public static final String UNKNOWN = "unknown";

    /** 役种名 → 码（顺序即登记顺序，便于阅读）。 */
    private static final Map<String, String> YAKU = new LinkedHashMap<>();

    static {
        // ---- 一般役 ----
        put("立直", "riichi");
        put("两立直", "double_riichi");
        put("一发", "ippatsu");
        put("门前清自摸和", "menzen_tsumo");
        put("平和", "pinfu");
        put("断幺九", "tanyao");
        put("一杯口", "iipeiko");
        put("二杯口", "ryanpeiko");
        put("三色同顺", "sanshoku_doujun");
        put("一气通贯", "ittsu");
        put("三色同刻", "sanshoku_doukou");
        put("三暗刻", "sanankou");
        put("三杠子", "sankantsu");
        put("三连刻", "sanrenkou");
        put("对对和", "toitoi");
        put("七对子", "chiitoitsu");
        put("混全带幺九", "chanta");
        put("纯全带幺九", "junchan");
        put("混老头", "honroutou");
        put("混一色", "honitsu");
        put("清一色", "chinitsu");
        put("一色三顺", "isshoku_sanjun");
        put("小三元", "shousangen");
        put("岭上开花", "rinshan");
        put("抢杠", "chankan");
        put("海底摸月", "haitei");
        put("河底捞鱼", "houtei");
        put("流局满贯", "nagashi_mangan");
        // ---- 役牌（参数化：+ tile）----
        put("宝牌", "dora");
        put("赤宝牌", "aka_dora");
        put("里宝牌", "ura_dora");
        // ---- 役满 ----
        put("国士无双", "kokushi");
        put("国士无双十三面", "kokushi_13");
        put("九莲宝灯", "chuuren_poutou");
        put("纯正九莲宝灯", "junsei_chuuren_poutou");
        put("四暗刻", "suuankou");
        put("四暗刻单骑", "suuankou_tanki");
        put("大三元", "daisangen");
        put("四杠子", "suukantsu");
        put("字一色", "tsuuiisou");
        put("绿一色", "ryuuiisou");
        put("清老头", "chinroutou");
        put("小四喜", "shousuushii");
        put("大四喜", "daisuushii");
        put("天和", "tenhou");
        put("地和", "chiihou");
        // ---- 古役（默认关闭，见 rules.koyaku）----
        put("人和", "renhou");
        put("石上三年", "ishou_sannen");
        put("大七星", "daishichisei");
        put("大数邻", "daisuurin");
        put("大车轮", "daisharin");
        put("大竹林", "daichikurin");
        put("十二落抬", "shiisanraotai");
        put("五门齐", "uumenchai");
        put("九筒捞鱼", "kyuusonrou");
        put("一筒摸月", "iipinmougetsu");
        put("杠振", "kouken");
        put("燕返", "tsubamegaeshi");
    }

    /** 「役牌 白」这类名字里的中文牌名 → 牌码。 */
    private static final Map<String, String> TILE_OF_CN = new LinkedHashMap<>();

    static {
        TILE_OF_CN.put("东", "1z");
        TILE_OF_CN.put("南", "2z");
        TILE_OF_CN.put("西", "3z");
        TILE_OF_CN.put("北", "4z");
        TILE_OF_CN.put("白", "5z");
        TILE_OF_CN.put("发", "6z");
        TILE_OF_CN.put("中", "7z");
    }

    /** 打点档位：中文 → 码（役满只报 `yakuman`，倍数由 `yakuman` 字段给）。 */
    private static final Map<String, String> LIMIT = new LinkedHashMap<>();

    static {
        LIMIT.put("", "");
        LIMIT.put("满贯", "mangan");
        LIMIT.put("跳满", "haneman");
        LIMIT.put("倍满", "baiman");
        LIMIT.put("三倍满", "sanbaiman");
        LIMIT.put("累计役满", "kazoe_yakuman");
        LIMIT.put("役满", "yakuman");
        LIMIT.put("两倍役满", "yakuman");
        LIMIT.put("三倍役满", "yakuman");
        LIMIT.put("四倍役满", "yakuman");
        LIMIT.put("五倍役满", "yakuman");
        LIMIT.put("六倍役满", "yakuman");
    }

    /** 流局原因：中文 → 码。 */
    private static final Map<String, String> REASON = new LinkedHashMap<>();

    static {
        REASON.put("荒牌流局", "exhaustive");
        REASON.put("流局满贯", "nagashi");
        REASON.put("九种九牌", "kyuushu");
        REASON.put("四风连打", "four_winds");
        REASON.put("四杠散了", "four_kans");
        REASON.put("四家立直", "four_riichi");
    }

    private static int misses;

    private static void put(String cn, String code) {
        YAKU.put(cn, code);
    }

    /** 役种名 → 码；参数化役种返回角色码（牌码另取 {@link #tileOf}）。 */
    public static String codeOf(String name) {
        String code = YAKU.get(name);
        if (code != null) {
            return code;
        }
        if (name.startsWith("役牌 ")) {
            return YAKUHAI;
        }
        if (name.startsWith("场风 ")) {
            return ROUND_WIND;
        }
        if (name.startsWith("自风 ")) {
            return SEAT_WIND;
        }
        misses++;
        return UNKNOWN;
    }

    /** 参数化役种的牌码（`1z..7z`）；非参数化返回 {@code null}。 */
    public static String tileOf(String name) {
        final String prefix;
        if (name.startsWith("役牌 ")) {
            prefix = "役牌 ";
        } else if (name.startsWith("场风 ")) {
            prefix = "场风 ";
        } else if (name.startsWith("自风 ")) {
            prefix = "自风 ";
        } else {
            return null;
        }
        return TILE_OF_CN.get(name.substring(prefix.length()));
    }

    /** 打点档位 → 码。 */
    public static String limitOf(String limit) {
        String code = LIMIT.get(limit);
        if (code != null) {
            return code;
        }
        // 「n倍役满」这种合成文本：统一归到 yakuman（倍数在 yakuman 字段里）
        return limit.endsWith("倍役满") ? "yakuman" : "";
    }

    /** 流局原因 → 码；认不出时回空串（客户端会退回 type 字段）。 */
    public static String reasonOf(String reason) {
        String code = REASON.get(reason);
        return code != null ? code : "";
    }

    /** 未登记的中文名出现次数（自检断言为 0；见类注释）。 */
    public static int misses() {
        return misses;
    }

    public static void resetMisses() {
        misses = 0;
    }

    /** 只读的役种词汇表（自检/文档用）。 */
    public static Map<String, String> vocabulary() {
        return Collections.unmodifiableMap(YAKU);
    }
}
