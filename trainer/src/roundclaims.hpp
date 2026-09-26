// 鸣牌仲裁的**纯判据** —— 与 Java `mahjong.game.RoundClaims` 逐分支同口径。
//
// 为什么值得单独对拍：这几条判据决定"什么时候可以不再等别家的应答"，而它们最容易写错的地方是
// **提前收工改变赢家**（AGENTS §2.3-10）：
//   · 等级「荣和 > 杠 = 碰 > 吃」这把尺子必须与真实仲裁循环**同一把**（同等级看座次距离）；
//   · **荣和要收齐**（多荣和）→ 还有 `ronCapable` 的人没答就绝不能收工；
//   · 低优先级**压不过**已到手的最优时才收工 —— 手上有碰的人还在等时，不能因为"还有一家没点跳过"拖到时限；
//   · `askedBestRank` **缺项要保守继续等**（"继续等"只是慢一点，"收工"却可能丢掉他的碰/杠）。
#pragma once

#include <algorithm>
#include <cstdint>
#include <map>
#include <set>
#include <vector>

namespace trainer {

inline constexpr int kRankNone = -1;
inline constexpr int kRankChi = 0;
inline constexpr int kRankPon = 1;
inline constexpr int kRankKan = 2;
inline constexpr int kRankRon = 3;

/** 动作类型 → 鸣牌等级（⚠ 这个顺序就是「荣和 > 杠 > 碰 > 吃」）。 */
inline int claimRankOf(const std::string &type) {
    if (type == "ron") {
        return kRankRon;
    }
    if (type == "kan") {
        return kRankKan;
    }
    if (type == "pon") {
        return kRankPon;
    }
    if (type == "chi") {
        return kRankChi;
    }
    return kRankNone;      // pass / 认不出的类型
}

/** 一条候选鸣牌：等级 + 座次距离（离打牌者几步，1..3）。 */
struct Claim {
    int rank = kRankNone;
    int seatDist = 0;
};

/** 一条「等级 rank、座次距离 seatDist」的候选能否压过当前最优 `best`（空 = 还没有任何有效鸣牌）。 */
inline bool claimCanBeat(bool hasBest, const Claim &best, int rank, int seatDist) {
    if (rank <= kRankNone) {
        return false;                    // pass / 兑现不了的动作，永远压不过任何人
    }
    if (!hasBest) {
        return true;
    }
    if (rank != best.rank) {
        return rank > best.rank;
    }
    return seatDist < best.seatDist;     // 同级：离打牌者近的赢
}

/** 是否所有可能荣和的人都已应答（没有荣和者时返回 false —— 那时还得等碰/吃）。 */
inline bool claimAllRonAnswered(const std::vector<int> &ronCapable, const std::set<int> &answered) {
    if (ronCapable.empty()) {
        return false;
    }
    for (int s : ronCapable) {
        if (answered.find(s) == answered.end()) {
            return false;
        }
    }
    return true;
}

/** 是否可以结束本轮鸣牌询问。 */
inline bool claimShouldStop(const std::vector<int> &asked, const std::vector<int> &ronCapable,
                            const std::set<int> &answered, int64_t remainMs, bool hasBest,
                            const Claim &best, const std::map<int, int> &askedBestRank,
                            const std::map<int, int> &seatDist) {
    if (asked.empty()) {
        return true;                       // 都答完了
    }
    if (remainMs <= 0) {
        return true;                       // 超时兜底
    }
    if (claimAllRonAnswered(ronCapable, answered)) {
        return true;                       // 荣和都收齐了，剩下的等着也没用
    }
    for (int s : asked) {
        if (std::find(ronCapable.begin(), ronCapable.end(), s) != ronCapable.end()) {
            return false;                  // 还有人可能荣和 → 必须等他（多荣和要收齐）
        }
        const auto it = askedBestRank.find(s);
        if (it == askedBestRank.end()) {
            // 没有该座位的优先级信息 → **保守地继续等**（缺信息时"收工"可能丢掉他的碰/杠）
            return false;
        }
        const auto dit = seatDist.find(s);
        const int dist = dit == seatDist.end() ? INT32_MAX : dit->second;
        if (claimCanBeat(hasBest, best, it->second, dist)) {
            return false;
        }
    }
    return true;                           // 没人能翻盘 → 立刻收工
}

/**
 * 这个回包该不该认领。
 *
 * <p>三种情况必须丢弃，否则会把**上一轮的迟到回包**当成这一轮的应答：
 * 该座位本轮没被问 / 当前没有挂起的询问 / 回包带的 `ask_id` 与挂起的对不上。
 * 回包**没带** `ask_id` 时按座位认领（兼容老客户端）。
 */
inline bool claimAcceptsReply(bool seatIsAsked, bool hasPendingAskId, int64_t pendingAskId,
                              bool hasReplyId, int64_t replyAskId) {
    if (!seatIsAsked || !hasPendingAskId) {
        return false;
    }
    if (!hasReplyId) {
        return true;
    }
    return replyAskId == pendingAskId;
}

}  // namespace trainer
