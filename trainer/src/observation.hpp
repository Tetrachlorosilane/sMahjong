// 一个座位的**信息集观测** —— 与 Java `mahjong.ai.Observation` 同字段、同 `toJson()` 键序。
//
// 为什么键序被当成契约：`obs` 是轨迹里唯一的"输入侧"，也是特征工程（Python）的读入口；
// 键序或字段名差一个字符，"逐字节一致"就不成立（docs/TRAINER-CPP.md §2 铁律 2）。
// 这里**逐字段照抄** `Observation.toJson()` 的插入序，不做任何"顺手整理"。
//
// ⚠ 只暴露该座位合法可见的信息（反作弊口径，AGENTS §6.5）：别家手牌、牌山顺序、里宝指示牌、
//   别家振听一律不进这个结构 —— 它与 Java 那侧是同一条纪律。
#pragma once

#include <algorithm>
#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "jsonw.hpp"
#include "options.hpp"
#include "tiles.hpp"

namespace trainer {

/** 观测格式版本（字段增删要 +1；与 Java `Observation.VERSION` 同步）。 */
inline constexpr int kObservationVersion = 1;

/** 副露的 JSON 形状（Java `Meld.toJson()`）。 */
struct MeldJson {
    std::string kind;                        // chi/pon/daiminkan/ankan/kakan
    std::vector<std::string> tiles;
    int from = 0;
    std::string calledTile;
    /** 逐张的"是不是赤五"（最多 4 张；用定长数组是因为 `vector<bool>` 取不出 `bool*`）。 */
    std::array<bool, 4> aka{};
};

struct Observation {
    int seat = 0;
    std::string kind;                        // "turn" / "claim"

    // ---- 自己 ----
    std::array<uint8_t, kKindCount> hand{};
    std::array<bool, kKindCount> handRed{};
    bool hasDrawn = false;
    std::string drawn;
    int playerDraws = 0;
    bool menzen = false;
    bool selfRiichi = false;
    bool furiten = false;

    // ---- 公开 ----
    std::vector<std::vector<MeldJson>> melds;              // 四家
    std::vector<std::vector<std::string>> discards;        // 四家
    std::vector<std::string> doraIndicators;
    std::array<bool, 4> riichi{};
    std::array<bool, 4> ippatsu{};
    std::array<int, 4> scores{};
    int roundWind = 0;
    int kyoku = 1;
    int honba = 0;
    int sticks = 0;
    int dealer = 0;
    int tilesLeft = 0;
    int deadWallLeft = 0;
    int totalDiscards = 0;
    int kanCount = 0;
    bool anyCall = false;
    std::array<uint8_t, kKindCount> visible{};

    // ---- 局面标记 ----
    bool haitei = false;
    bool houtei = false;
    bool rinshan = false;

    // ---- 鸣牌询问专用 ----
    int from = -1;
    bool hasCalledTile = false;
    std::string calledTile;
    bool hasWinNote = false;
    std::string winNote;

    /** 本次询问的全部合法动作（由 `options` 展开，顺序一致）。 */
    std::vector<Action> legal;

    std::vector<std::string> legalKeys() const {
        std::vector<std::string> ks;
        ks.reserve(legal.size());
        for (const Action &a : legal) {
            ks.push_back(a.key());
        }
        return ks;
    }

    /** 该动作在本次合法集里的下标；不合法返回 -1（Java `Observation.indexOf`）。 */
    int indexOfKey(const std::string &key) const {
        for (size_t i = 0; i < legal.size(); i++) {
            if (legal[i].key() == key) {
                return static_cast<int>(i);
            }
        }
        return -1;
    }

    /** `Observation.toJson()` 的等价物（键序逐条照抄）。 */
    std::string toJson() const {
        static const char *kWindNames[4] = {"E", "S", "W", "N"};
        std::string o;
        o.reserve(2048);
        o += "{\"v\":";
        o += std::to_string(kObservationVersion);
        o += ",\"seat\":";
        o += std::to_string(seat);
        o += ",\"kind\":";
        jsonStr(o, kind);
        o += ",\"hand\":";
        jsonU8Array(o, hand.data(), kKindCount);
        o += ",\"hand_red\":[";
        for (int k = 0; k < kKindCount; k++) {
            if (k > 0) {
                o.push_back(',');
            }
            o += handRed[static_cast<size_t>(k)] ? "true" : "false";
        }
        o += "],\"drawn\":";
        jsonStrOrNull(o, hasDrawn, drawn);
        o += ",\"player_draws\":";
        o += std::to_string(playerDraws);
        o += ",\"menzen\":";
        jsonBool(o, menzen);
        o += ",\"self_riichi\":";
        jsonBool(o, selfRiichi);
        o += ",\"furiten\":";
        jsonBool(o, furiten);

        o += ",\"melds\":[";
        for (int s = 0; s < 4; s++) {
            if (s > 0) {
                o.push_back(',');
            }
            o.push_back('[');
            const std::vector<MeldJson> &ms = melds[static_cast<size_t>(s)];
            for (size_t i = 0; i < ms.size(); i++) {
                if (i > 0) {
                    o.push_back(',');
                }
                o += "{\"kind\":";
                jsonStr(o, ms[i].kind);
                o += ",\"tiles\":";
                jsonStrArray(o, ms[i].tiles);
                o += ",\"from\":";
                o += std::to_string(ms[i].from);
                o += ",\"called_tile\":";
                jsonStr(o, ms[i].calledTile);
                o += ",\"aka\":";
                jsonBoolArray(o, ms[i].aka.data(), static_cast<int>(ms[i].tiles.size()));
                o.push_back('}');
            }
            o.push_back(']');
        }
        o += "],\"discards\":[";
        for (int s = 0; s < 4; s++) {
            if (s > 0) {
                o.push_back(',');
            }
            jsonStrArray(o, discards[static_cast<size_t>(s)]);
        }
        o += "],\"dora_indicators\":";
        jsonStrArray(o, doraIndicators);

        o += ",\"riichi\":";
        jsonBoolArray(o, riichi.data(), 4);
        o += ",\"ippatsu\":";
        jsonBoolArray(o, ippatsu.data(), 4);
        o += ",\"scores\":";
        jsonIntArray(o, scores.data(), 4);

        o += ",\"round\":{\"bakaze\":";
        jsonStr(o, kWindNames[static_cast<size_t>(std::max(0, std::min(3, roundWind)))]);
        o += ",\"kyoku\":";
        o += std::to_string(kyoku);
        o += ",\"honba\":";
        o += std::to_string(honba);
        o += ",\"dealer\":";
        o += std::to_string(dealer);
        o += ",\"riichi_sticks\":";
        o += std::to_string(sticks);
        o.push_back('}');

        o += ",\"tiles_left\":";
        o += std::to_string(tilesLeft);
        o += ",\"dead_wall_left\":";
        o += std::to_string(deadWallLeft);
        o += ",\"total_discards\":";
        o += std::to_string(totalDiscards);
        o += ",\"kan_count\":";
        o += std::to_string(kanCount);
        o += ",\"any_call\":";
        jsonBool(o, anyCall);
        o += ",\"visible\":";
        jsonU8Array(o, visible.data(), kKindCount);
        o += ",\"haitei\":";
        jsonBool(o, haitei);
        o += ",\"houtei\":";
        jsonBool(o, houtei);
        o += ",\"rinshan\":";
        jsonBool(o, rinshan);
        o += ",\"from\":";
        o += std::to_string(from);
        o += ",\"called_tile\":";
        jsonStrOrNull(o, hasCalledTile, calledTile);
        o += ",\"win_note\":";
        jsonStrOrNull(o, hasWinNote, winNote);
        o += ",\"legal\":";
        jsonStrArray(o, legalKeys());
        o.push_back('}');
        return o;
    }
};

}  // namespace trainer
