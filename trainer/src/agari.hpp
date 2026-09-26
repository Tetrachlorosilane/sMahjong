// 和了形分解 —— 与 Java `mahjong.rules.Agari` **同枚举顺序、同判定**。
//
// 为什么连"枚举顺序"都要照抄：`Evaluator` 用高点法在**所有解释**里取最优，并列时取
// **先出现的那个**（Java：`best == null || better(s, best)`）。顺序一变，同分的两种解释
// 就会互换 —— 番/符一样、但**役种列表**不一样（例如"一杯口 vs 三色"同分），
// 于是写进轨迹的 `yaku[]` 就与 Java 不同了。所以这里的枚举顺序是**接口**，不是实现细节。
#pragma once

#include <array>
#include <cstdint>
#include <vector>

#include "counts.hpp"
#include "meld.hpp"

namespace trainer {

inline constexpr int kTypeStandard = 0;
inline constexpr int kTypeChiitoitsu = 1;
inline constexpr int kTypeKokushi = 2;

inline constexpr int kSetRun = 0;
inline constexpr int kSetTriplet = 1;
inline constexpr int kSetQuad = 2;

// 听牌形（与 Java `Agari.WAIT_*` 同号；`handeval.hpp` 里的 kWai* 是同一套）
inline constexpr int kWaitRyanmen = 0;
inline constexpr int kWaitKanchan = 1;
inline constexpr int kWaitPenchan = 2;
inline constexpr int kWaitShanpon = 3;
inline constexpr int kWaitTanki = 4;

/** 一种和了形解释。 */
struct Form {
    int type = kTypeStandard;
    int pair = -1;
    int nSets = 0;                              // **含**副露
    std::array<int, 4> setType{};
    std::array<int, 4> setStart{};
    std::array<bool, 4> setConcealed{};
    std::array<bool, 4> setFromMeld{};
    int winSet = -1;                            // 和了牌所在的面子下标；-1 = 在雀头
    int waitType = kWaitRyanmen;
    bool kokushi13 = false;
};

/** 分解和了形（`counts` 含和了牌；`melds` 可空）。返回所有合法解释（可能为空 = 不和了）。 */
std::vector<Form> decompose(const Counts &counts, const std::vector<Meld> &melds, int winKind);

/** 听牌种类（升序 kind 列表）—— 与 Java `Agari.waits` 同序、同判据。 */
std::vector<int> agariWaits(const Counts &counts13, int meldCount);

}  // namespace trainer
