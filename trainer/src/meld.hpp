// 副露（面子） —— 与 Java `mahjong.core.Meld` 同字段同语义。
//
// 为什么自己在手里存 `tiles`（牌 id）而不是只存"种类 + 起张"：
// `Evaluator` 要按**牌 id** 数赤宝牌、`Visible` 要按 id 计可见牌、
// `Round` 还要用被鸣的那张 id 去牌河里 `removeAt` —— 这三处的账都必须落在同一份 id 上。
#pragma once

#include <array>
#include <cstdint>

#include "tiles.hpp"

namespace trainer {

struct Meld {
    enum class Kind : uint8_t { CHI, PON, DAIMINKAN, ANKAN, KAKAN };

    Kind kind = Kind::CHI;
    std::array<int, 4> tiles{};
    int tileCount = 0;
    int from = 0;          // 被鸣牌的玩家座位；暗杠时等于自己
    int calledId = 0;      // 被鸣的那张牌 id（加杠时为加上的第 4 张）

    Meld() = default;
    Meld(Kind k, const int *ts, int n, int f, int c)
        : kind(k), tileCount(n), from(f), calledId(c) {
        for (int i = 0; i < n && i < 4; i++) {
            tiles[static_cast<size_t>(i)] = ts[i];
        }
    }

    /** 是否为明面（破坏门前清）。 */
    bool open() const { return kind != Kind::ANKAN; }
    bool isKan() const {
        return kind == Kind::DAIMINKAN || kind == Kind::ANKAN || kind == Kind::KAKAN;
    }
    bool isRun() const { return kind == Kind::CHI; }

    /** 面子对应的 kind（顺子取最小牌；刻子/杠取刻子牌）。 */
    int baseKind() const {
        int mn = 99;
        for (int i = 0; i < tileCount; i++) {
            const int k = kindOf(tiles[static_cast<size_t>(i)]);
            if (k < mn) {
                mn = k;
            }
        }
        return mn;
    }

    int kindCount() const { return tileCount; }
};

}  // namespace trainer
