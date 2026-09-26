// 手牌评估：向听 / 进张 / 听牌形 —— 与 Java `mahjong.rules.HandEval` **同值**。
//
// 这一层是观测特征（615/96）里最贵的一块：`ObsFeatures.perCandidate` 会对每个候选打牌调用
// 一次 `HandEval.of`，而 `of` 至少要跑 1 + 34 次向听（听牌时还要对每个听牌张做一次和了形分解）。
// 实测（见 docs/TRAINING.md §3.3）：**一次决策九成时间花在这里**（≈0.5~0.8 ms/候选）。
#pragma once

#include <array>
#include <cstdint>
#include <vector>

#include "counts.hpp"

namespace trainer {

// 听牌形（与 Java `Agari.WAIT_*` 同号：数值本身就是接口，别重排）
inline constexpr int kWaiRyanmen = 0;
inline constexpr int kWaiKanchan = 1;
inline constexpr int kWaiPenchan = 2;
inline constexpr int kWaiShanpon = 3;
inline constexpr int kWaiTanki = 4;

/** 形的优劣序（越小越好），与 Java `HandEval.shapeRank` 一致。 */
inline int shapeRank(int waitType) {
    switch (waitType) {
        case kWaiRyanmen: return 0;
        case kWaiShanpon: return 1;
        case kWaiKanchan: return 2;
        case kWaiPenchan: return 3;
        case kWaiTanki:   return 4;
        default:          return 5;
    }
}

inline bool isGoodShape(int waitType) { return waitType == kWaiRyanmen; }

/** 进张：34 维 0/1（1 = 摸到该牌种会让向听数下降；已听牌时全 0）。 */
std::array<uint8_t, kKindCount> advanceKinds(const Counts &c, int meldCount);

inline int advanceTypes(const std::array<uint8_t, kKindCount> &adv) {
    int n = 0;
    for (uint8_t v : adv) {
        if (v > 0) {
            n++;
        }
    }
    return n;
}

/** 进张枚数 = Σ max(0, 4 − 自己手里 − 可见)，只数进张牌种。 */
int advanceTiles(const std::array<uint8_t, kKindCount> &adv, const Counts &own,
                 const Counts &visible);

/** 听牌种类（升序，与 Java `Agari.waits` 同序）。 */
std::vector<int> waits(const Counts &counts13, int meldCount);

/** 每个听牌种的最好形（-1 = 不是听牌张；其余取 WAIT_*）。 */
std::array<int8_t, kKindCount> waitShapes(const Counts &counts13, int meldCount);

/** 一次评估的快照（与 Java `HandEval.Snapshot` 同字段）。 */
struct Snapshot {
    int shanten = 99;
    bool tenpai = false;
    std::array<uint8_t, kKindCount> advanceKinds{};      // 未听牌时有意义
    int advanceTypes = 0;
    int advanceTiles = 0;
    std::array<int8_t, kKindCount> waitShapes{};         // 听牌时有意义
    int waitTypes = 0;
    int waitTiles = 0;
    int goodWaitTypes = 0;
    int goodWaitTiles = 0;
};

/** 评估一手 13 张（或 13−3×副露 张）的手牌；`visible` 传全 0 等价于"什么也看不见"。 */
Snapshot evalOf(const Counts &counts, int meldCount, const Counts &visible);

/** 「打掉 `discardKind` 之后」的评估（`counts14` 是含刚摸那张的 14 张）。 */
Snapshot evalAfterDiscard(const Counts &counts14, int meldCount, int discardKind,
                          const Counts &visible);

/** 某个和了牌在**一般型分解**里能扮演的最好形（无一般型分解时返回 kWaiTanki）。 */
int bestWaitType(const Counts &counts14, int meldCount, int winKind);

}  // namespace trainer
