#include "handeval.hpp"

#include <algorithm>

#include "shanten.hpp"

namespace trainer {
namespace {

/** 可摸张数：`max(0, 4 − 可见 − 自己手里)`（与 Java `Visible.drawable` 同式）。 */
inline int drawableOf(const Counts &visible, const Counts &own, int k) {
    const int v = std::max(0, 4 - static_cast<int>(visible[static_cast<size_t>(k)])
                                  - static_cast<int>(own[static_cast<size_t>(k)]));
    return std::max(0, v);
}

// ---------------------------------------------------------------- 听牌形分解
//
// 与 Java `Agari.decompose` 的**一般型**部分等价：枚举雀头 + need 个面子，
// 只在完全拆完时记录「和了牌能扮演的角色」，取 rank 最小者。
// Java 里副露只影响 need（= 4 − 副露数）与面子总数，不参与 waitType 判定，
// 所以这里只需要 `meldCount`。
struct Decomp {
    Counts c{};
    int need = 0;
    int winKind = 0;
    int pairKind = -1;
    // 已选面子的 (type, start)：type 0 = 顺子，1 = 刻子
    std::array<std::array<int, 2>, 4> sets{};
    int nSets = 0;
    int bestRank = -1;
};

inline void considerRank(Decomp &d) {
    int rank = -1;
    if (d.pairKind == d.winKind) {
        rank = 4;                                    // 单骑
    }
    for (int i = 0; i < d.nSets; i++) {
        const int type = d.sets[static_cast<size_t>(i)][0];
        const int start = d.sets[static_cast<size_t>(i)][1];
        int r = -1;
        if (type == 0) {                             // 顺子
            if (d.winKind == start + 1) {
                r = 2;                               // 嵌张
            } else if (d.winKind == start) {
                r = (start % 9 == 6) ? 3 : 0;         // 边张（8,9 等 7）/ 两面
            } else if (d.winKind == start + 2) {
                r = (start % 9 == 0) ? 3 : 0;         // 边张（1,2 等 3）/ 两面
            }
        } else if (start == d.winKind) {
            r = 1;                                   // 双碰
        }
        if (r >= 0 && (rank < 0 || r < rank)) {
            rank = r;
        }
    }
    if (rank >= 0 && (d.bestRank < 0 || rank < d.bestRank)) {
        d.bestRank = rank;
    }
}

void enumSets(Decomp &d, int from, int left) {
    if (left == 0) {
        for (int k = from; k < kKindCount; k++) {
            if (d.c[static_cast<size_t>(k)] != 0) {
                return;                              // 还有剩牌 → 不是合法分解
            }
        }
        considerRank(d);
        return;
    }
    int i = from;
    while (i < kKindCount && d.c[static_cast<size_t>(i)] == 0) {
        i++;
    }
    if (i == kKindCount) {
        return;
    }
    if (d.c[static_cast<size_t>(i)] >= 3) {           // 刻子
        d.c[static_cast<size_t>(i)] -= 3;
        d.sets[static_cast<size_t>(d.nSets)][0] = 1;
        d.sets[static_cast<size_t>(d.nSets)][1] = i;
        d.nSets++;
        enumSets(d, i, left - 1);
        d.nSets--;
        d.c[static_cast<size_t>(i)] += 3;
    }
    if (i < 27 && i % 9 <= 6 && d.c[static_cast<size_t>(i + 1)] > 0
            && d.c[static_cast<size_t>(i + 2)] > 0) { // 顺子
        d.c[static_cast<size_t>(i)]--;
        d.c[static_cast<size_t>(i + 1)]--;
        d.c[static_cast<size_t>(i + 2)]--;
        d.sets[static_cast<size_t>(d.nSets)][0] = 0;
        d.sets[static_cast<size_t>(d.nSets)][1] = i;
        d.nSets++;
        enumSets(d, i, left - 1);
        d.nSets--;
        d.c[static_cast<size_t>(i)]++;
        d.c[static_cast<size_t>(i + 1)]++;
        d.c[static_cast<size_t>(i + 2)]++;
    }
}

inline int typeOfRank(int rank) {
    switch (rank) {
        case 0: return kWaiRyanmen;
        case 1: return kWaiShanpon;
        case 2: return kWaiKanchan;
        case 3: return kWaiPenchan;
        default: return kWaiTanki;
    }
}

}  // namespace

int bestWaitType(const Counts &counts14, int meldCount, int winKind) {
    const int need = 4 - (meldCount < 0 ? 0 : (meldCount > 4 ? 4 : meldCount));
    Decomp d;
    d.winKind = winKind;
    d.need = need;
    for (int p = 0; p < kKindCount; p++) {
        if (counts14[static_cast<size_t>(p)] < 2) {
            continue;
        }
        d.c = counts14;
        d.c[static_cast<size_t>(p)] -= 2;
        d.pairKind = p;
        d.nSets = 0;
        enumSets(d, 0, need);
    }
    // Java：`best < 0 ? WAIT_TANKI : best`——没有任何一般型分解（例如七对子/国士听牌）时按单骑记
    return d.bestRank < 0 ? kWaiTanki : typeOfRank(d.bestRank);
}

std::array<uint8_t, kKindCount> advanceKinds(const Counts &c, int meldCount) {
    std::array<uint8_t, kKindCount> out{};
    const int cur = shantenMin(c, meldCount);
    if (cur <= 0) {
        return out;                                  // 已听牌：该看 waits，不是"进张"
    }
    Counts probe = c;
    for (int k = 0; k < kKindCount; k++) {
        if (probe[static_cast<size_t>(k)] >= 4) {
            continue;
        }
        probe[static_cast<size_t>(k)]++;
        if (shantenMin(probe, meldCount) < cur) {
            out[static_cast<size_t>(k)] = 1;
        }
        probe[static_cast<size_t>(k)]--;
    }
    return out;
}

int advanceTiles(const std::array<uint8_t, kKindCount> &adv, const Counts &own,
                 const Counts &visible) {
    int n = 0;
    for (int k = 0; k < kKindCount; k++) {
        if (adv[static_cast<size_t>(k)] == 0) {
            continue;
        }
        n += drawableOf(visible, own, k);
    }
    return n;
}

std::vector<int> waits(const Counts &counts13, int meldCount) {
    std::vector<int> out;
    Counts probe = counts13;
    for (int k = 0; k < kKindCount; k++) {
        if (probe[static_cast<size_t>(k)] >= 4) {
            continue;
        }
        probe[static_cast<size_t>(k)]++;
        if (shantenMin(probe, meldCount) < 0) {
            out.push_back(k);
        }
        probe[static_cast<size_t>(k)]--;
    }
    return out;
}

std::array<int8_t, kKindCount> waitShapes(const Counts &counts13, int meldCount) {
    std::array<int8_t, kKindCount> out{};
    out.fill(-1);
    Counts c14 = counts13;
    for (int k : waits(counts13, meldCount)) {
        c14 = counts13;
        c14[static_cast<size_t>(k)]++;
        out[static_cast<size_t>(k)] = static_cast<int8_t>(bestWaitType(c14, meldCount, k));
    }
    return out;
}

Snapshot evalOf(const Counts &counts, int meldCount, const Counts &visible) {
    Snapshot s;
    s.waitShapes.fill(-1);                               // 与 Java `waitShapes` 同约定：-1 = 不是听牌张
    s.shanten = shantenMin(counts, meldCount);
    if (s.shanten <= 0) {
        s.tenpai = true;
        s.waitShapes = waitShapes(counts, meldCount);
        for (int k = 0; k < kKindCount; k++) {
            const int shape = s.waitShapes[static_cast<size_t>(k)];
            if (shape < 0) {
                continue;
            }
            const int draw = drawableOf(visible, counts, k);
            s.waitTypes++;
            s.waitTiles += draw;
            if (isGoodShape(shape)) {
                s.goodWaitTypes++;
                s.goodWaitTiles += draw;
            }
        }
    } else {
        s.advanceKinds = advanceKinds(counts, meldCount);
        s.advanceTypes = advanceTypes(s.advanceKinds);
        s.advanceTiles = advanceTiles(s.advanceKinds, counts, visible);
    }
    return s;
}

Snapshot evalAfterDiscard(const Counts &counts14, int meldCount, int discardKind,
                          const Counts &visible) {
    Counts c = counts14;
    if (discardKind >= 0 && discardKind < kKindCount && c[static_cast<size_t>(discardKind)] > 0) {
        c[static_cast<size_t>(discardKind)]--;
    }
    return evalOf(c, meldCount, visible);
}

}  // namespace trainer
