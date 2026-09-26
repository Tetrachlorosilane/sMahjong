// 协议词汇表 —— 与 Java `mahjong.rules.YakuCodes` **同一张表**。
//
// 报文里不出现中文（AGENTS §6.4）：服务端只上报稳定 ASCII 码。训练端要它的唯一理由
// 是**轨迹里写的是码**（`TraceRecorder` 用的是 `YakuCodes.codeOf`），所以码表必须逐条对齐；
// 同时它也是"新增役种必须登记"的那道闸门（Java 侧 `misses() > 0` 会让自检红）。
#pragma once

#include <string>

namespace trainer {

// 参数化役种的码（与牌码 `tile` 组合）
inline constexpr const char *kCodeYakuhai = "yakuhai";
inline constexpr const char *kCodeRoundWind = "round_wind";
inline constexpr const char *kCodeSeatWind = "seat_wind";
inline constexpr const char *kCodeUnknown = "unknown";

/** 役种名（中文）→ 码；参数化役种返回角色码（牌码另取 `tileOf`）。 */
std::string yakuCodeOf(const std::string &name);

/** 参数化役种的牌码（`1z..7z`）；非参数化返回空串。 */
std::string yakuTileOf(const std::string &name);

/** 打点档位（中文）→ 码；认不出的合成文本按"n 倍役满"归到 `yakuman`。 */
std::string limitCodeOf(const std::string &limit);

/** 流局原因（中文）→ 码；认不出返回空串。 */
std::string reasonCodeOf(const std::string &reason);

}  // namespace trainer
