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

/** 对**一家**打 `kind` 的危险度**分数**（`Danger.Report.score`；级别只用来取基准分）。 */
inline int dangerScore(int kind, const Counts &visible, const Counts *rivers, bool theirRiichi,
                       int turn) {
    const bool inRange = kind >= 0 && kind < kKindCount;
    if (rivers != nullptr && inRange && (*rivers)[static_cast<size_t>(kind)] > 0) {
        return 0;                                        // 现物：`Report(SAFE, 0, …)`
    }
    const bool suji = dangerIsSuji(kind, rivers);
    const bool wall = inRange && visible[static_cast<size_t>(kind)] >= 3;
    int level;
    if (suji && wall) {
        level = kDangerRelativelySafe;                   // 两个理由同时成立仍是同一档（Java 也是）
    } else if (suji) {
        level = kDangerRelativelySafe;
    } else if (wall) {
        level = kDangerRelativelySafe;
    } else if (theirRiichi) {
        level = kDangerDangerous;
    } else {
        level = kDangerSuspicious;
    }
    const int t = std::max(0, std::min(turn, 18));
    return std::min(100, kDangerBase[level] + t);
}

/** 对**四家**取最危险的那一家（排除自己）—— `Danger.worst`。 */
inline int dangerWorst(int kind, const Counts &visible, const std::array<Counts, 4> &rivers,
                       const std::array<bool, 4> &riichi, int turn, int selfSeat) {
    bool has = false;
    int best = 0;
    for (int s = 0; s < 4; s++) {
        if (s == selfSeat) {
            continue;
        }
        const int sc = dangerScore(kind, visible, &rivers[static_cast<size_t>(s)],
                                   riichi[static_cast<size_t>(s)], turn);
        if (!has || sc > best) {                         // 严格大于才替换（并列保留先出现的那家）
            has = true;
            best = sc;
        }
    }
    // 三家全被排除（`selfSeat` 越界等）时的兜底：Java 传 `null` 牌河 + 未立直
    return has ? best : dangerScore(kind, visible, nullptr, false, turn);
}

/**
 * 只对**立直家**取最危险 —— 弃和（ベタオリ）时该用这一支。
 *
 * <p>为什么不能对四家取最坏：没人立直的对手手里是什么样无从判断，把他们算进来会让每一张牌
 * 都"一样危险"、把立直家现物那份真正有价值的安全度淹掉。没有任何立直家时**回退成 `worst`**。
 */
inline int dangerWorstAgainstRiichi(int kind, const Counts &visible,
                                    const std::array<Counts, 4> &rivers,
                                    const std::array<bool, 4> &riichi, int turn, int selfSeat) {
    bool has = false;
    int best = 0;
    for (int s = 0; s < 4; s++) {
        if (s == selfSeat || !riichi[static_cast<size_t>(s)]) {
            continue;
        }
        const int sc = dangerScore(kind, visible, &rivers[static_cast<size_t>(s)], true, turn);
        if (!has || sc > best) {
            has = true;
            best = sc;
        }
    }
    return has ? best : dangerWorst(kind, visible, rivers, riichi, turn, selfSeat);
}

}  // namespace trainer
