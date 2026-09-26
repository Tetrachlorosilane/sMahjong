// 授受点数计算 —— 与 Java `mahjong.rules.Payments` **逐分支同口径**。
//
// 这里有三个"看起来能简化、其实不能"的地方（都在 Java 的注释里写明过）：
//   ① 包牌是**列表**（一手牌可以同时成立两个各有责任者的役满），同一家出现多次要**按座位累加**；
//   ② 包牌时**本场棒整体由包牌者出**（不是放铳者），多个包牌者按 100 点平摊、余数给靠前的；
//   ③ 不听罚符**先定"每家收多少"再让付方凑出同一总量** —— 收付各自取整会让点数凭空生灭
//      （AUDIT F11：默认 3000 时看不出，改小就露头）。
#pragma once

#include <array>
#include <vector>

#include "evaluator.hpp"

namespace trainer {

int ceil100(int v);

/** 一条包牌责任（责任支付）。 */
struct Pao {
    int seat = 0;
    int base = 0;
};

/** 一次和牌的收支（与 Java `Payments.Result` 同字段）。 */
struct PaymentResult {
    std::array<int, 4> delta{};
    int winnerGain = 0;
    int winnerPoints = 0;
    int riichiTaken = 0;
};

/** 计算授受点数（`loser` 自摸时传 -1）。 */
PaymentResult paymentsCompute(const HandScore &score, int winner, int loser, int dealer, int honba,
                              int sticks, bool tsumo, const std::vector<Pao> &paos);

/** 不听罚符：听牌者一共收 `total`，不听者一共付 `total`（收付严格相等）。 */
std::array<int, 4> notenPenalty(const std::array<bool, 4> &tenpai, int total);

}  // namespace trainer
