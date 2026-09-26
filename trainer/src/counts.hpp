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

// 宝牌指示牌 → 宝牌：见 `tiles.hpp` 的 `doraFrom`（只留一份，避免两个定义漂移）。

}  // namespace trainer
