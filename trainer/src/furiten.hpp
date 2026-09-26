// 振听（フリテン）记账 —— 与 Java `Round` 的 `furitenTemp` / `furitenPerm` / `discardKindsEver`
// + `isFuriten` **同一套口径**。
//
// 三种振听（`docs/日本麻将.md` §振听；AGENTS §2.3-12）：
//   ① **舍张振听**：当前所听的牌里有一种是自己**曾经打出过**的 —— 含听牌前打出的、
//      也含**后来被他家吃/碰/杠走**的舍牌（所以账记在"曾经打出过"上，**不是**牌河的镜像）；
//   ② **同巡振听**：本巡之内被给荣和却见逃（含超时未答）→ 自家**下一次摸牌**时解除；
//   ③ **立直后振听**：立直状态下见逃 → **持续到本局结束**。
//
// ⚠ 唯一的记账点是「出牌」那一刻（Java `Round.recordDiscard`）——测试不许绕过它（老用例直接往
//    `discards[]` 里塞牌 = 给 bug 背书）。
#pragma once

#include <array>
#include <cstdint>
#include <vector>

#include "counts.hpp"
#include "tiles.hpp"

namespace trainer {

struct FuritenState {
    /** 每座位**曾经打出过**的牌种计数（34 维）。 */
    std::array<Counts, 4> ever{};
    std::array<bool, 4> temp{};
    std::array<bool, 4> perm{};

    /** 出牌的**唯一**记账点（Java `Round.recordDiscard` 的振听那一半）。 */
    void recordDiscard(int seat, int kind) {
        ever[static_cast<size_t>(seat)][static_cast<size_t>(kind)]++;
    }

    /** 自家摸牌 → 同巡振听解除（Java `Round` 主循环里的 `furitenTemp[turn] = false`）。 */
    void onOwnDraw(int seat) { temp[static_cast<size_t>(seat)] = false; }

    /** 见逃一张荣和：置同巡振听；立直状态下再置**永久**（到本局结束）。 */
    void onRonPassed(int seat, bool riichi) {
        temp[static_cast<size_t>(seat)] = true;
        if (riichi) {
            perm[static_cast<size_t>(seat)] = true;
        }
    }

    /** 曾经打出过的牌种（升序）。 */
    std::vector<int> ownDiscardKinds(int seat) const {
        std::vector<int> out;
        for (int k = 0; k < kKindCount; k++) {
            if (ever[static_cast<size_t>(seat)][static_cast<size_t>(k)] > 0) {
                out.push_back(k);
            }
        }
        return out;
    }

    /** 是否振听（`waitKinds` = 该家当前的听牌种）。 */
    bool isFuriten(int seat, const std::vector<int> &waitKinds) const {
        if (temp[static_cast<size_t>(seat)] || perm[static_cast<size_t>(seat)]) {
            return true;
        }
        const Counts &own = ever[static_cast<size_t>(seat)];
        for (int k : waitKinds) {
            if (own[static_cast<size_t>(k)] > 0) {
                return true;
            }
        }
        return false;
    }
};

}  // namespace trainer
