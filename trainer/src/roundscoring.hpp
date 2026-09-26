// 一局结束后的**连庄判据 + 终局精算** —— 与 Java `mahjong.game.RoundScoring` 逐分支同口径。
//
// 为什么这一层值得单独对拍：
//   · `rank_points`（顺位点）是**奖励函数的直接输入**（`python/mahjong_ml/rewards.py` 直接读它，
//     从不自己重算 uma）—— 算错一位，整条 RL 的奖励就悄悄偏了；
//   · 连庄/本场数/和了止/延长战这几条判据在 Java 侧原来是**散在五个地方各写一行**的（AUDIT S-46/S-54/S-55），
//     每一处写错都只表现为"分数算错但不报错"；
//   · 同点拆分（`tieSplitPoint`）用的是"**0.1 分**为单位向下取整、尾数归更接近起家者"，
//     与终局立直棒的"**100 点**为单位"是同一条规则的两个单位 —— 两处都必须用 `floorDiv`（负数下取整）。
#pragma once

#include <array>
#include <cstdint>
#include <vector>

#include "counts.hpp"
#include "rules.hpp"

namespace trainer {

/** 一局的最终精算结果（与 Java `RoundScoring.Settlement` 同字段同索引口径）。 */
struct Settlement {
    std::array<int, 4> order{};       // order[i] = 第 i 名（0 起）的**座位**
    std::array<int, 4> rank{};        // rank[seat] = 该座位的名次（并列时取区间首名）
    std::array<double, 4> point{};    // point[seat] = 顺位点（精算点数）
    std::array<double, 4> uma{};      // ⚠ 按**名次**索引（不是座位）
    std::array<double, 4> oka{};      // ⚠ 同上
};

/** 终局余棒的分配（只有"最后一局是流局"时才用；和了结束时供託已被和牌者收走）。 */
std::array<int, 4> endGameSticks(const std::array<int, 4> &scores, int sticks);

/** 通用拆分：等分 `total` 给 `n` 家、每份以 `unit` 为单位**向下取整**，余数全给第一家。 */
std::array<int, 2> splitRemainder(int total, int n, int unit);

/** 精算点数 = (点数 − 返点)/1000 + 马点 + 头名赏（仅 1 位）。 */
Settlement settle(const std::array<int, 4> &scores, const Rules &rules);

/** 下一局的本场数：连庄 +1、闲家和了清零、流局满贯按和了清零、荒牌流局轮庄也 +1。 */
int nextHonba(int honba, bool dealerRenchan, bool agari, bool nagashi);

/** 荒牌流局：庄家听牌才连庄（不听则轮庄，但本场仍 +1）。 */
bool exhaustiveBy(int dealer, const std::array<bool, 4> &tenpai);

/** 流局满贯的连庄判据与荒牌流局**完全一致**（判据是庄家是否听牌，不是"成立者里有没有庄家"）。 */
bool nagashiBy(int dealer, const std::array<bool, 4> &tenpai);

/** 和了止 / 听牌止（三条件：`agariyame` 开、庄家和了或庄家听牌、庄家 1 位且达一位必要点数）。 */
bool stopAtAllLast(int dealer, bool agari, bool nagashi, const std::array<bool, 4> &tenpai,
                   const std::array<int, 4> &scores, const Rules &rules);

/** 本场赛制的最后一场风：东风战 0（东）、半庄 1（南）。 */
int lastWindOf(const Rules &rules);

/** All Last 轮庄后要不要进延长战（门槛是 `requiredPoints`，场风上限是 `lastWind + 1`，没有北入）。 */
bool keepPlayingWest(const Rules &rules, int top, int nextRoundWind, int lastWind);

/** 流局满贯的分数增减（不含立直棒；四家之和恒为 0）。 */
std::array<int, 4> nagashiPayments(int winnerSeat, int dealer);

/** 流局满贯是否成立：没打过非幺九牌，且牌河没有被鸣走过（空牌河不算）。 */
bool nagashiEligible(const std::vector<int> &river, bool calledFrom);

/** 形式听牌（只看牌形，不看役、不看振听）—— 不听罚符与庄家连庄用的都是它。 */
bool tenpaiOf(const Counts &concealed, int meldCount);

}  // namespace trainer
