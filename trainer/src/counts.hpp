// 计数向量的公共小工具（与 Java `Tiles` 的语义对齐的部分）
#pragma once

#include <array>
#include <cstdint>

#include "tiles.hpp"

namespace trainer {

using Counts = std::array<uint8_t, kKindCount>;

inline int sumOf(const Counts& c) {
    int n = 0;
    for (int k = 0; k < kKindCount; k++) {
        n += c[static_cast<size_t>(k)];
    }
    return n;
}

/** 幺九牌（老头牌 + 字牌）—— 与 Java `Tiles.isYaochu` 同口径。 */
inline bool isYaochuKind(int kind) {
    if (kind >= 27) {
        return true;                       // 字牌全是幺九
    }
    const int num = kind % 9;              // 0..8 → 1..9
    return num == 0 || num == 8;
}

/** 宝牌指示牌 → 宝牌（与 Java `Tiles.doraFrom` 同口径：同花色内循环，字牌 4→7→1）。 */
inline int doraFrom(int indicatorKind) {
    if (indicatorKind < 0 || indicatorKind >= kKindCount) {
        return -1;
    }
    if (indicatorKind < 27) {
        const int suit = indicatorKind / 9;
        const int num = indicatorKind % 9;
        return suit * 9 + (num + 1) % 9;
    }
    if (indicatorKind < 31) {               // 东南西北 → 下一个风
        return 27 + (indicatorKind - 27 + 1) % 4;
    }
    return 31 + (indicatorKind - 31 + 1) % 3;  // 白发中
}

}  // namespace trainer
