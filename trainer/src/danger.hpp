// 危险度（押し引き）—— 与 Java `mahjong.rules.Danger` **逐分支同值**。
//
// 为什么单独一层：`ObsFeatures.perDecision` 的 68 维里有 **整整 68 维**（两套各 34）都来自这里，
// 而它是**启发式**、不是规则判定 —— 换句话说"没有对拍就一定会漂"。所以这里逐句照抄
// `server/src/main/java/mahjong/rules/Danger.java`，连"立直家现物也是 0 分""取最坏的一家"
// 这类看起来可以简化的地方都不动。
//
// 四条口径（Java 类注释里的那张表）：
//   · 现物（该家牌河里已有这个牌种）= 0 分，**唯一的硬保证**（他若听这张就自振听）；
//   · 筋（同花色 ±3 已在他牌河里）或壁（该牌种可见 ≥3 张）= 12 分基准；
//   · 其余无信息 = 30 分基准；该家立直 = 65 分基准；
//   · 基准分 + 巡目（封顶 18），再封顶 100。
#pragma once

#include <algorithm>

#include "counts.hpp"
#include "tiles.hpp"

namespace trainer {

/** 级别（数值本身是接口：`BASE[]` 按它索引，别重排）。 */
inline constexpr int kDangerSafe = 0;
inline constexpr int kDangerRelativelySafe = 1;
inline constexpr int kDangerSuspicious = 2;
inline constexpr int kDangerDangerous = 3;

/** 各级别的基准分（0..100 的连续量，给"取最安全的那张"排序用）。 */
inline constexpr int kDangerBase[4] = {0, 12, 30, 65};

/**
 * 筋：同花色 ±3 的牌已在他牌河里。字牌没有筋；`rivers == nullptr` 等价于"看不见他的牌河"。
 *
 * <p>道理：两面搭子听的是 `{a, a+3}`（手里是 `a+1, a+2`）。他既然打过 `a+3`，
 * 就不可能正听 `a`（那样他打掉的正是自己的和了牌 → 自振听）。
 */
inline bool dangerIsSuji(int kind, const Counts *rivers) {
    if (rivers == nullptr || kind < 0 || kind >= 27) {
        return false;
    }
    const int suit = suitOf(kind);
    const int num = numOf(kind);
    for (int d = -3; d <= 3; d += 6) {
        const int n = num + d;
        if (n < 1 || n > 9) {
            continue;
        }
        const int partner = suit * 9 + (n - 1);
        if ((*rivers)[static_cast<size_t>(partner)] > 0) {
            return true;
        }
    }
    return false;
}

/**
 * 级别 + 分数 —— Java `Danger.Report` 的二元组（`code` / `genbutsu` / `suji` / `wall` 在
 * 训练端没人读，所以不搬）。
 *
 * <p>为什么要连级别一起给：teacher（`bot.cpp`）有两处闸门看的是**级别**
 * （`dealScore` 的"已经是 DANGEROUS 就不再抬档"、`shouldKan` 的加杠危险闸门），
 * 而特征工程（`obffeatures.cpp`）只看**分数**。两处必须**同一份实现**，否则"抬档"与
 * "特征里的危险度"会各算一遍、迟早漂 —— 所以分数也从这个函数派生。
 */
struct DangerReport {
    int level = kDangerSuspicious;
    int score = 0;
};

/** 对**一家**打 `kind` 的危险度（级别 + 分数）= Java `Danger.of`。 */
inline DangerReport dangerOf(int kind, const Counts &visible, const Counts *rivers,
                             bool theirRiichi, int turn) {
    const bool inRange = kind >= 0 && kind < kKindCount;
    if (rivers != nullptr && inRange && (*rivers)[static_cast<size_t>(kind)] > 0) {
        return {kDangerSafe, 0};                         // 现物：唯一的硬保证
    }
    const bool suji = dangerIsSuji(kind, rivers);
    const bool wall = inRange && visible[static_cast<size_t>(kind)] >= 3;
    int level;
    if (suji || wall) {
        // 三个"筋/壁"分支在 Java 里各写一条（`suji && wall` / `suji` / `wall`），但**取同一档**；
        // 这里合并成一条 —— 级别与分数都只由 `level` 决定，故逐位等价。
        level = kDangerRelativelySafe;
    } else if (theirRiichi) {
        level = kDangerDangerous;
    } else {
        level = kDangerSuspicious;
    }
    const int t = std::max(0, std::min(turn, 18));
    return {level, std::min(100, kDangerBase[level] + t)};
}

/** 对**一家**打 `kind` 的危险度**分数**（`Danger.Report.score`）。 */
inline int dangerScore(int kind, const Counts &visible, const Counts *rivers, bool theirRiichi,
                       int turn) {
    return dangerOf(kind, visible, rivers, theirRiichi, turn).score;
}

/** 对**四家**取最危险的那一家（排除自己）—— `Danger.worst`（级别 + 分数）。 */
inline DangerReport dangerWorstReport(int kind, const Counts &visible,
                                      const std::array<Counts, 4> &rivers,
                                      const std::array<bool, 4> &riichi, int turn, int selfSeat) {
    bool has = false;
    DangerReport best{};
    for (int s = 0; s < 4; s++) {
        if (s == selfSeat) {
            continue;
        }
        const DangerReport rep = dangerOf(kind, visible, &rivers[static_cast<size_t>(s)],
                                          riichi[static_cast<size_t>(s)], turn);
        if (!has || rep.score > best.score) {             // 严格大于才替换（并列保留先出现的那家）
            has = true;
            best = rep;
        }
    }
    // 三家全被排除（`selfSeat` 越界等）时的兜底：Java 传 `null` 牌河 + 未立直
    return has ? best : dangerOf(kind, visible, nullptr, false, turn);
}

/** 对**四家**取最危险的那一家（排除自己）—— 只要分数。 */
inline int dangerWorst(int kind, const Counts &visible, const std::array<Counts, 4> &rivers,
                       const std::array<bool, 4> &riichi, int turn, int selfSeat) {
    return dangerWorstReport(kind, visible, rivers, riichi, turn, selfSeat).score;
}

/**
 * 只对**立直家**取最危险 —— 弃和（ベタオリ）时该用这一支（级别 + 分数）。
 *
 * <p>为什么不能对四家取最坏：没人立直的对手手里是什么样无从判断，把他们算进来会让每一张牌
 * 都"一样危险"、把立直家现物那份真正有价值的安全度淹掉。没有任何立直家时**回退成 `worst`**。
 */
inline DangerReport dangerWorstAgainstRiichiReport(int kind, const Counts &visible,
                                                   const std::array<Counts, 4> &rivers,
                                                   const std::array<bool, 4> &riichi, int turn,
                                                   int selfSeat) {
    bool has = false;
    DangerReport best{};
    for (int s = 0; s < 4; s++) {
        if (s == selfSeat || !riichi[static_cast<size_t>(s)]) {
            continue;
        }
        const DangerReport rep = dangerOf(kind, visible, &rivers[static_cast<size_t>(s)], true, turn);
        if (!has || rep.score > best.score) {
            has = true;
            best = rep;
        }
    }
    return has ? best : dangerWorstReport(kind, visible, rivers, riichi, turn, selfSeat);
}

/** 只对**立直家**取最危险 —— 只要分数。 */
inline int dangerWorstAgainstRiichi(int kind, const Counts &visible,
                                    const std::array<Counts, 4> &rivers,
                                    const std::array<bool, 4> &riichi, int turn, int selfSeat) {
    return dangerWorstAgainstRiichiReport(kind, visible, rivers, riichi, turn, selfSeat).score;
}

}  // namespace trainer
