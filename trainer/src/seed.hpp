// 种子链 —— 与 Java **逐位同源**（`Table.mixSeed` / `Table.nextRoundSeed` / `SelfPlay.seedFor`）。
//
// 为什么单独拎出来：整条"同种子 → 同轨迹"的契约就建立在这三个函数上。差一位，整场牌换掉，
// 而且**不会报错**（数据看着都正常，只是与 Java 那份不再同源）。
//
// ⚠ 全部用 `uint64_t` 做运算再转回 `int64_t`：Java 的 `long` 溢出**有定义**（二进制补码回绕），
//   而 C++ 的**有符号**溢出是 UB —— 用无符号算再 `bit_cast`，语义与 Java 逐位一致。
#pragma once

#include <array>
#include <cstdint>
#include <vector>

#include "tiles.hpp"

namespace trainer {

inline constexpr uint64_t kGolden = 0x9E3779B97F4A7C15ULL;

/** SplitMix64 终混（`Table.mixSeed`：先加黄金比再两轮 xor-mul-shift）。 */
inline int64_t mixSeed(int64_t x) {
    uint64_t v = static_cast<uint64_t>(x) + kGolden;
    v = (v ^ (v >> 30)) * 0xBF58476D1CE4E5B9ULL;
    v = (v ^ (v >> 27)) * 0x94D049BB133111EBULL;
    return static_cast<int64_t>(v ^ (v >> 31));
}

/**
 * 自对弈单场种子（`SelfPlay.seedFor`）：只与 `(seedBase, 场号)` 有关，**与并行顺序无关**。
 * ⚠ 它与 `mixSeed` **不同**：少最后那一步 `+ golden`（Java 就是这么写的，别"顺手统一"）。
 */
inline int64_t seedFor(int64_t seedBase, int game) {
    uint64_t x = static_cast<uint64_t>(seedBase) + static_cast<uint64_t>(game) * kGolden;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ULL;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBULL;
    return static_cast<int64_t>(x ^ (x >> 31));
}

/**
 * `Table.nextRoundSeed()` 的**确定性岔路**（`debugDeterministicSeed = true`，自对弈/自检走这条）：
 * 第 i 局的种子 = `mixSeed(seedBase + i)`，i 从 0 起。
 *
 * <p>生产路径是"当前时刻毫秒数与 seedBase 混合"（刻意不可复现），训练端不用它。
 */
inline int64_t roundSeedAt(int64_t seedBase, int roundIndex) {
    return mixSeed(static_cast<int64_t>(static_cast<uint64_t>(seedBase)
                                        + static_cast<uint64_t>(roundIndex)));
}

/** 连开 n 局的种子序列（训练端只需要这一条岔路）。 */
inline std::vector<int64_t> roundSeeds(int64_t seedBase, int n) {
    std::vector<int64_t> out;
    out.reserve(static_cast<size_t>(n < 0 ? 0 : n));
    for (int i = 0; i < n; i++) {
        out.push_back(roundSeedAt(seedBase, i));
    }
    return out;
}

/**
 * 顺位（1 = 最高分）：**同点时按座次先后**拆开，保证恒为 1..4 的一个排列
 * （`SelfPlay.placementOf` / `TraceRecorder.placementOf`；平均顺位只有在"每人恰好一个名次"时才有意义）。
 */
inline std::array<int, 4> placementOf(const std::array<int, 4> &scores) {
    std::array<int, 4> idx = {0, 1, 2, 3};
    for (int i = 0; i < 4; i++) {
        for (int j = i + 1; j < 4; j++) {
            const bool swap = scores[static_cast<size_t>(idx[static_cast<size_t>(j)])]
                                      > scores[static_cast<size_t>(idx[static_cast<size_t>(i)])]
                    || (scores[static_cast<size_t>(idx[static_cast<size_t>(j)])]
                                == scores[static_cast<size_t>(idx[static_cast<size_t>(i)])]
                        && idx[static_cast<size_t>(j)] < idx[static_cast<size_t>(i)]);
            if (swap) {
                const int t = idx[static_cast<size_t>(i)];
                idx[static_cast<size_t>(i)] = idx[static_cast<size_t>(j)];
                idx[static_cast<size_t>(j)] = t;
            }
        }
    }
    std::array<int, 4> place{};
    for (int rank = 0; rank < 4; rank++) {
        place[static_cast<size_t>(idx[static_cast<size_t>(rank)])] = rank + 1;
    }
    return place;
}

}  // namespace trainer
