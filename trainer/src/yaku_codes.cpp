#include "yaku_codes.hpp"

#include <array>
#include <string_view>

namespace trainer {
namespace {

struct Entry {
    const char *name;
    const char *code;
};

// 顺序与 Java 的 `YAKU` 表一致（登记顺序，便于人工核对）
constexpr Entry kYaku[] = {
    // ---- 一般役 ----
    {"立直", "riichi"},
    {"两立直", "double_riichi"},
    {"一发", "ippatsu"},
    {"门前清自摸和", "menzen_tsumo"},
    {"平和", "pinfu"},
    {"断幺九", "tanyao"},
    {"一杯口", "iipeiko"},
    {"二杯口", "ryanpeiko"},
    {"三色同顺", "sanshoku_doujun"},
    {"一气通贯", "ittsu"},
    {"三色同刻", "sanshoku_doukou"},
    {"三暗刻", "sanankou"},
    {"三杠子", "sankantsu"},
    {"三连刻", "sanrenkou"},
    {"对对和", "toitoi"},
    {"七对子", "chiitoitsu"},
    {"混全带幺九", "chanta"},
    {"纯全带幺九", "junchan"},
    {"混老头", "honroutou"},
    {"混一色", "honitsu"},
    {"清一色", "chinitsu"},
    {"一色三顺", "isshoku_sanjun"},
    {"小三元", "shousangen"},
    {"岭上开花", "rinshan"},
    {"抢杠", "chankan"},
    {"海底摸月", "haitei"},
    {"河底捞鱼", "houtei"},
    {"流局满贯", "nagashi_mangan"},
    // ---- 宝牌 ----
    {"宝牌", "dora"},
    {"赤宝牌", "aka_dora"},
    {"里宝牌", "ura_dora"},
    // ---- 役满 ----
    {"国士无双", "kokushi"},
    {"国士无双十三面", "kokushi_13"},
    {"九莲宝灯", "chuuren_poutou"},
    {"纯正九莲宝灯", "junsei_chuuren_poutou"},
    {"四暗刻", "suuankou"},
    {"四暗刻单骑", "suuankou_tanki"},
    {"大三元", "daisangen"},
    {"四杠子", "suukantsu"},
    {"字一色", "tsuuiisou"},
    {"绿一色", "ryuuiisou"},
    {"清老头", "chinroutou"},
    {"小四喜", "shousuushii"},
    {"大四喜", "daisuushii"},
    {"天和", "tenhou"},
    {"地和", "chiihou"},
    // ---- 古役 ----
    {"人和", "renhou"},
    {"石上三年", "ishou_sannen"},
    {"大七星", "daishichisei"},
    {"大数邻", "daisuurin"},
    {"大车轮", "daisharin"},
    {"大竹林", "daichikurin"},
    {"十二落抬", "shiisanraotai"},
    {"五门齐", "uumenchai"},
    {"九筒捞鱼", "kyuusonrou"},
    {"一筒摸月", "iipinmougetsu"},
    {"杠振", "kouken"},
    {"燕返", "tsubamegaeshi"},
};

struct TileEntry {
    const char *cn;
    const char *tile;
};

constexpr std::array<TileEntry, 7> kTileOfCn = {{
    {"东", "1z"}, {"南", "2z"}, {"西", "3z"}, {"北", "4z"},
    {"白", "5z"}, {"发", "6z"}, {"中", "7z"},
}};

bool startsWith(const std::string &s, std::string_view prefix) {
    return s.size() >= prefix.size() && std::string_view(s).substr(0, prefix.size()) == prefix;
}

}  // namespace

std::string yakuCodeOf(const std::string &name) {
    for (const Entry &e : kYaku) {
        if (name == e.name) {
            return e.code;
        }
    }
    if (startsWith(name, "役牌 ")) {
        return kCodeYakuhai;
    }
    if (startsWith(name, "场风 ")) {
        return kCodeRoundWind;
    }
    if (startsWith(name, "自风 ")) {
        return kCodeSeatWind;
    }
    return kCodeUnknown;
}

std::string yakuTileOf(const std::string &name) {
    std::string_view prefix;
    if (startsWith(name, "役牌 ")) {
        prefix = "役牌 ";
    } else if (startsWith(name, "场风 ")) {
        prefix = "场风 ";
    } else if (startsWith(name, "自风 ")) {
        prefix = "自风 ";
    } else {
        return "";
    }
    const std::string rest = name.substr(prefix.size());
    for (const TileEntry &e : kTileOfCn) {
        if (rest == e.cn) {
            return e.tile;
        }
    }
    return "";
}

std::string limitCodeOf(const std::string &limit) {
    if (limit.empty()) {
        return "";
    }
    if (limit == "满贯") {
        return "mangan";
    }
    if (limit == "跳满") {
        return "haneman";
    }
    if (limit == "倍满") {
        return "baiman";
    }
    if (limit == "三倍满") {
        return "sanbaiman";
    }
    if (limit == "累计役满") {
        return "kazoe_yakuman";
    }
    if (limit == "役满" || limit == "两倍役满" || limit == "三倍役满" || limit == "四倍役满"
            || limit == "五倍役满" || limit == "六倍役满") {
        return "yakuman";
    }
    // 「n倍役满」这种合成文本：统一归到 yakuman（倍数在 yakuman 字段里）
    const std::string suffix = "倍役满";
    if (limit.size() >= suffix.size()
            && limit.compare(limit.size() - suffix.size(), suffix.size(), suffix) == 0) {
        return "yakuman";
    }
    return "";
}

std::string reasonCodeOf(const std::string &reason) {
    if (reason == "荒牌流局") {
        return "exhaustive";
    }
    if (reason == "流局满贯") {
        return "nagashi";
    }
    if (reason == "九种九牌") {
        return "kyuushu";
    }
    if (reason == "四风连打") {
        return "four_winds";
    }
    if (reason == "四杠散了") {
        return "four_kans";
    }
    if (reason == "四家立直") {
        return "four_riichi";
    }
    if (reason == "三家和了") {
        return "triple_ron";
    }
    return "";
}

}  // namespace trainer
