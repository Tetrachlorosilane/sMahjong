// 「询问玩家时能做什么」的**纯计算**部分 —— 与 Java `mahjong.game.RoundOptions` 同口径。
//
// 这些函数不碰牌桌、不碰牌山，给定手牌/副露/状态就能算出结论；它们的输出**同时**是
// 下发给客户端的选项、服务端的合法性校验、以及训练端策略的动作空间 —— 三处必须同源。
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "counts.hpp"
#include "handeval.hpp"
#include "tiles.hpp"

namespace trainer {

// 牌串格式化直接用 `tiles.hpp` 的 `kindToStr`（kind，red）/ `tileToStr`（id）—— 只留一份。

/**
 * 本巡可打的牌（去重后的牌串）；**顺序即候选顺序**（与 Java 的 `LinkedHashSet` 插入序一致）。
 *
 * - 已立直：只能摸切 → 候选只有刚摸到的那张（`drawn < 0` 时退化成手牌去重，Java 同）； 
 * - 未立直：手牌去重，但排除 `forbiddenKinds`（振听禁打 / 食替）里的**牌种**。
 */
inline std::vector<std::string> discardChoices(const std::vector<int> &hand, bool riichi, int drawn,
                                               const std::vector<int> &forbiddenKinds) {
    std::vector<std::string> out;
    if (riichi && drawn >= 0) {
        out.push_back(tileToStr(drawn));
        return out;
    }
    for (int id : hand) {
        if (!forbiddenKinds.empty()) {
            bool hit = false;
            for (int k : forbiddenKinds) {
                if (k == kindOf(id)) {
                    hit = true;
                    break;
                }
            }
            if (hit) {
                continue;
            }
        }
        const std::string s = tileToStr(id);
        bool seen = false;
        for (const std::string &t : out) {
            if (t == s) {
                seen = true;
                break;
            }
        }
        if (!seen) {
            out.push_back(s);
        }
    }
    return out;
}

/**
 * 食替禁打集合：吃之后**哪些牌种不能马上打出**（`docs/日本麻将.md` §食替）。
 * 顺序 = Java `LinkedHashSet` 插入序：先現物（被吃那张），再 `lo-1`、`hi+1`。
 *
 * ⚠ 两侧都要算：旧实现只算「较大那两张 +1」，于是「被吃的那张在顺子上边」时永远漏判
 * （3m4m 吃 5m 应禁 {5m, 2m}）。坎张吃没有第二个完成形，所以只有現物食替。
 */
inline std::vector<int> kuikaeForbidden(int calledKind, int k0, int k1) {
    std::vector<int> out;
    out.push_back(calledKind);                       // 現物食替
    if (calledKind >= 27 || k0 >= 27 || k1 >= 27) {
        return out;                                  // 字牌不成顺子
    }
    if (suitOf(k0) != suitOf(calledKind) || suitOf(k1) != suitOf(calledKind)) {
        return out;
    }
    const int lo = k0 < k1 ? k0 : k1;
    const int hi = k0 < k1 ? k1 : k0;
    if (hi - lo != 1) {
        return out;                                  // 坎张（差 2）→ 只有現物食替
    }
    const int base = suitOf(lo) * 9;
    const int cands[2] = {lo - 1, hi + 1};
    for (int cand : cands) {
        if (cand < base || cand > base + 8) {
            continue;                                // 越过花色边界（1m 的向下补不存在）
        }
        if (cand == calledKind) {
            continue;                                // 就是被吃那张，已在集合里
        }
        out.push_back(cand);
    }
    return out;
}

/**
 * 立直之后还能不能杠某种牌：杠不得改变听牌形。
 * 把这种牌的四张从暗牌里拿走（等于多一副露），再看听牌集合是否与杠之前**完全一致**；
 * 变了或不听牌了 → 不允许。
 */
inline bool kanAllowedAfterRiichi(const std::vector<int> &waitsBefore, const Counts &concealed,
                                  int meldCount, int kind) {
    // ⚠ 前提：手里真有 4 张。Java 的调用点只在这种时候问；若越界，Java 的 `int[]` 会得到**负数**，
    //   而 `Shanten.dfs` 遇到负数会直接 **StackOverflowError**（对拍时真炸过一次），
    //   而 C++ 的计数是 uint8_t（减 4 会绕回 255）→ 两个引擎会看到不同输入。这里显式挡住。
    if (concealed[static_cast<size_t>(kind)] < 4) {
        return false;
    }
    Counts after = concealed;
    after[static_cast<size_t>(kind)] =
        static_cast<uint8_t>(after[static_cast<size_t>(kind)] - 4);
    const std::vector<int> waitsAfter = waits(after, meldCount + 1);
    if (waitsAfter.empty()) {
        return false;
    }
    // 集合相等（两边都由 `waits` 产出且升序去重，逐元素比即可，但仍按集合语义写）
    for (int k : waitsBefore) {
        bool hit = false;
        for (int w : waitsAfter) {
            if (w == k) {
                hit = true;
                break;
            }
        }
        if (!hit) {
            return false;
        }
    }
    for (int w : waitsAfter) {
        bool hit = false;
        for (int k : waitsBefore) {
            if (k == w) {
                hit = true;
                break;
            }
        }
        if (!hit) {
            return false;
        }
    }
    return true;
}

/**
 * 吃：给定被吃的那张与暗牌计数，列出所有可行搭子（顺序 = Java 的三种组合 {-2,-1} {-1,1} {1,2}）。
 * 越界检查用**花色区间**，不会串到相邻花色。
 */
inline std::vector<std::array<std::string, 2>> chiSets(const Counts &concealed, int calledKind) {
    std::vector<std::array<std::string, 2>> sets;
    if (calledKind >= 27) {
        return sets;                                  // 字牌
    }
    const int suit = suitOf(calledKind);
    const int base = suit * 9 + numOf(calledKind) - 1;
    const int lo = suit * 9;
    const int hi = suit * 9 + 8;
    const int cands[3][2] = {{-2, -1}, {-1, 1}, {1, 2}};
    for (const auto &cd : cands) {
        const int a = base + cd[0];
        const int b = base + cd[1];
        if (a < lo || b < lo || a > hi || b > hi) {
            continue;
        }
        if (concealed[static_cast<size_t>(a)] > 0 && concealed[static_cast<size_t>(b)] > 0) {
            sets.push_back({kindToStr(a, false), kindToStr(b, false)});
        }
    }
    return sets;
}

}  // namespace trainer
