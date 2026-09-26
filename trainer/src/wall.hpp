// 牌山 + 王牌 —— 与 Java `mahjong.core.Wall` **同构**（账必须一样，见 docs/TRAINER-CPP.md §4）。
//
//   tiles[0 .. 121]    可摸牌山（122 张）
//   tiles[122 .. 135]  王牌（14 张）= dead[0..3] 岭上 + dead[4..8] 表宝牌 + dead[9..13] 里宝
//
// 两个账分开记（照抄 Java 的注释，因为这是同一个坑）：
//   · 开杠那一刻：牌山末尾一张移进王牌 → liveEnd--（tilesLeft 减 1）
//   · 杠后岭上摸牌：取自王牌 → tilesLeft **不动**，只有 rinshanLeft 减 1
#pragma once

#include <cstdint>
#include <vector>

#include "tiles.hpp"

namespace trainer {

inline constexpr int kLiveTiles = 122;
inline constexpr int kRinshanTiles = 4;
inline constexpr int kDeadTiles = 14;
inline constexpr int kDoraMax = 5;

class Wall {
public:
    // 与 Java 一致：洗 0..135 → （aka==0 时）把赤五改写成普通五 → 切出王牌
    Wall(int64_t seed, int aka);

    // 配牌：按顺序取一张（不经过可摸牌山）
    int deal() { return tiles_[static_cast<size_t>(dealt_++)]; }

    // 配牌结束：把可摸牌山起点钉在配牌之后（庄家第 14 张也算在 dealt 里）
    void finishDealing() { livePos_ = dealt_; }

    int draw() { return tiles_[static_cast<size_t>(livePos_++)]; }
    int drawRinshan() { return dead_[static_cast<size_t>(rinshanPos_++)]; }

    void onKan() { liveEnd_--; }
    void revealDora() {
        if (doraRevealed_ < kDoraMax) {
            doraRevealed_++;
        }
    }

    /** 可摸牌山还剩几张（报文里的 `tiles_left`）—— Java 同名方法带 `Math.max(0, …)` 的钳制。 */
    int tilesLeft() const { return liveEnd_ - livePos_ > 0 ? liveEnd_ - livePos_ : 0; }

    /**
     * 这一张是不是海底 / 河底（摸到 / 打出它之后就荒牌流局）。
     *
     * ⚠ 口径是 Java `Wall.atLastLiveTile()` 的 `livePos >= liveEnd`（**已经摸完**最后一张），
     * 不是"还剩一张"：调用点（立直条件、河底闸门、海底摸月）问的都是"这一张是不是最后一张"。
     * 这里原来是 `livePos_ == liveEnd_ - 1`（差一位），因为当时只有 `turnoptions` 的语料对拍
     * 用到它、而那一项由 Java 探针喂进来，所以一直没被抓住 —— 实局用上就会整片偏一巡。
     */
    bool atLastLiveTile() const { return livePos_ >= liveEnd_; }
    int rinshanLeft() const { return kRinshanTiles - rinshanPos_; }
    int doraCount() const { return doraRevealed_; }

    // ⚠ 与 Java 逐字同口径：`Wall.indicators()` 返回的是 **kind**（不是 id），
    //   而且**张数 = 已翻开的张数**（开局 1 张；`uraIndicators()` 与表宝同长）。
    //   要原始 id 用 `deadTile(i)`（供 M2 的宝牌计账）。
    int deadTile(int i) const { return dead_[static_cast<size_t>(i)]; }

    std::vector<int> doraIndicators() const { return indicators(4); }
    std::vector<int> uraIndicators() const { return indicators(9); }

    const std::vector<int>& allTiles() const { return tiles_; }
    int dealt() const { return dealt_; }

private:
    // Java `Wall.indicators(base)`：前 `doraRevealed_` 张，取的是 **kind**
    std::vector<int> indicators(int base) const {
        std::vector<int> out;
        out.reserve(static_cast<size_t>(doraRevealed_));
        for (int i = 0; i < doraRevealed_; i++) {
            out.push_back(kindOf(dead_[static_cast<size_t>(base + i)]));
        }
        return out;
    }

    std::vector<int> tiles_;                 // 洗好的 136 张
    std::vector<int> dead_ = std::vector<int>(kDeadTiles);
    int dealt_ = 0;                          // 配牌已取走几张
    int livePos_ = 0;                        // 下一张可摸牌山下标
    int liveEnd_ = kLiveTiles;               // 每开一次杠前移一位
    int rinshanPos_ = 0;
    int doraRevealed_ = 1;                   // 开局翻 1 张
};

}  // namespace trainer
