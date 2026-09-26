// 可见牌统计 + 和了形的纯判断 —— 与 Java `mahjong.rules.Visible` / `mahjong.game.WinCheck` 同口径。
//
// 为什么先做这一小块：`Visible.counts` 是**观测特征（M3）的底座** —— `Observation.visible`、
// 进张枚数、危险度三者必须**同源**（都是这一份账），否则"同样一张牌桌上还剩几张"会三处各算一遍、
// 迟早漂。三条口径：
//   · 「可见」= 四家牌河 + 四家副露（含被鸣那张）+ **宝牌指示牌**；
//   · ⛔ **里宝指示牌不算可见**（只有和牌者自己看得到）；
//   · `unseen = 4 − 可见` 与 `drawable = 4 − 可见 − 自己手里` 是**两个量**，混用是牌效统计最常见的错。
#pragma once

#include <algorithm>
#include <array>
#include <vector>

#include "agari.hpp"
#include "counts.hpp"
#include "meld.hpp"
#include "tiles.hpp"

namespace trainer {

/** 汇总可见计数（长度 34）：四家牌河 + 四家副露 + 宝牌指示牌（kind）。 */
inline Counts visibleCounts(const std::array<std::vector<int>, 4> &discards,
                            const std::array<std::vector<Meld>, 4> &melds,
                            const std::vector<int> &doraKinds) {
    Counts acc{};
    for (int s = 0; s < 4; s++) {
        for (int id : discards[static_cast<size_t>(s)]) {
            acc[static_cast<size_t>(kindOf(id))]++;
        }
        for (const Meld &m : melds[static_cast<size_t>(s)]) {
            for (int i = 0; i < m.tileCount; i++) {
                acc[static_cast<size_t>(kindOf(m.tiles[static_cast<size_t>(i)]))]++;
            }
        }
    }
    for (int k : doraKinds) {
        if (k >= 0 && k < kKindCount) {
            acc[static_cast<size_t>(k)]++;
        }
    }
    return acc;
}

/** 34 维剩余张数 = 4 − 可见（下限 0）—— "相对于**完全未知**"的剩余量。 */
inline Counts visibleUnseen(const Counts &visible) {
    Counts out{};
    for (int k = 0; k < kKindCount; k++) {
        out[static_cast<size_t>(k)] = static_cast<uint8_t>(
                std::max(0, 4 - static_cast<int>(visible[static_cast<size_t>(k)])));
    }
    return out;
}

/** 34 维**可摸张数** = 4 − 可见 − 自己手里（下限 0）—— "我还能摸到几张"。 */
inline Counts visibleDrawable(const Counts &visible, const Counts &own) {
    Counts out{};
    for (int k = 0; k < kKindCount; k++) {
        out[static_cast<size_t>(k)] = static_cast<uint8_t>(std::max(
                0, 4 - static_cast<int>(visible[static_cast<size_t>(k)])
                           - static_cast<int>(own[static_cast<size_t>(k)])));
    }
    return out;
}

/** 是「和了形」但不是「能和」时给出被挡住的原因（`furiten` / `no_yaku`）。 */
inline std::string winBlockReason(bool tsumo, bool furiten) {
    if (!tsumo && furiten) {
        return "furiten";
    }
    return "no_yaku";
}

/** 把和了牌并入暗牌计数（自摸时已含）并校验张数：`14 − 3×副露数`，不符返回 false。 */
inline bool winCounts(const Counts &concealed, int meldCount, int winKind, bool tsumo,
                      Counts &out) {
    out = concealed;
    if (!tsumo) {
        out[static_cast<size_t>(winKind)]++;
    }
    return sumOf(out) == 14 - meldCount * 3;
}

}  // namespace trainer
