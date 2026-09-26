#include "shanten.hpp"

#include <algorithm>
#include <array>
#include <bit>
#include <vector>

namespace trainer {
namespace {

// ---------------------------------------------------------------- 位并行可达集
//
// 一组的「牌型」用 5 进制编码（每种牌 0..4 张）：9 张的花色 5^9 = 1_953_125 种，
// 7 张的字牌 5^7 = 78_125 种。
//
// 每组的拆解结果压成一个**位掩码**：位 `s*10 + q` 表示"这一组能拆出 s 个面子 + q 个搭子"，
// 雀头单独占一个掩码（`p0` / `p1`）。乘 10 而不是乘 5 是**故意的**：合并两组时
// `q_a + q_b ≤ 4 < 10`，所以 `b << (s_a*10 + q_a)` 的位移不会把 q 进位到 s 位上去 ——
// 一次移位 + 与就完成了一个"和卷积"，且**一个比特位都不用循环**（比逐状态三重循环快 ~25×）。
struct Reach {
    uint64_t p0 = 0;                                  // 无雀头
    uint64_t p1 = 0;                                  // 这一组出雀头
};

constexpr int kSuitPatterns = 1953125;
constexpr int kHonorPatterns = 78125;

/** 合并两组时，"另一个组的哪些 (s,q) 允许"——按 `s+q ≤ 4` 预先算好。 */
struct RectDom {
    uint64_t mask[5][5]{};
    RectDom() {
        for (int s = 0; s <= 4; s++) {
            for (int q = 0; q <= 4; q++) {
                uint64_t m = 0;
                for (int s2 = 0; s2 <= 4; s2++) {
                    for (int q2 = 0; q2 <= 4; q2++) {
                        if (s + s2 + q + q2 <= 4) {
                            m |= 1ULL << (s2 * 10 + q2);
                        }
                    }
                }
                mask[s][q] = m;
            }
        }
    }
};
const RectDom kRect;

std::vector<Reach> &suitTable() {
    static std::vector<Reach> t(kSuitPatterns);
    return t;
}
std::vector<Reach> &honorTable() {
    static std::vector<Reach> t(kHonorPatterns);
    return t;
}

/** 一个掩码与另一组按 `s+q ≤ 4` 卷积（位移不产生进位，见 `Reach` 的注释）。 */
inline uint64_t convolve(uint64_t a, uint64_t b) {
    uint64_t out = 0;
    while (a != 0) {
        const int i = std::countr_zero(a);
        a &= a - 1;
        out |= (b & kRect.mask[i / 10][i % 10]) << i;
    }
    return out;
}

inline Reach mergeReach(const Reach &a, const Reach &b) {
    Reach out;
    out.p0 = convolve(a.p0, b.p0);
    // 雀头是"或"不是"和"：两边都出雀头也不会变成两个
    out.p1 = convolve(a.p0, b.p1) | convolve(a.p1, b.p0) | convolve(a.p1, b.p1);
    return out;
}

inline void setBit(Reach &r, int sets, int pair, int partials) {
    const uint64_t bit = 1ULL << (sets * 10 + partials);
    if (pair == 0) {
        r.p0 |= bit;
    } else {
        r.p1 |= bit;
    }
}

/** 组内 DFS：与 Java `Shanten.dfs` 的选择集完全一致（只是不跨花色、不带副露数）。 */
void dfsGroup(uint8_t *c, int n, bool allowRuns, int from, int sets, int pair, int partials,
              Reach &r) {
    int i = from;
    while (i < n && c[i] == 0) {
        i++;
    }
    if (i == n) {
        setBit(r, sets, pair, partials);
        return;
    }
    const bool room = sets + partials < 4;              // Java: melds + partials < 4
    if (c[i] >= 3 && room) {                            // 刻子
        c[i] -= 3;
        dfsGroup(c, n, allowRuns, i, sets + 1, pair, partials, r);
        c[i] += 3;
    }
    if (allowRuns && i + 2 < n && c[i + 1] > 0 && c[i + 2] > 0 && room) {   // 顺子
        c[i]--;
        c[i + 1]--;
        c[i + 2]--;
        dfsGroup(c, n, allowRuns, i, sets + 1, pair, partials, r);
        c[i]++;
        c[i + 1]++;
        c[i + 2]++;
    }
    if (c[i] >= 2) {                                    // 对子：当雀头 或 当搭子
        c[i] -= 2;
        if (pair == 0) {
            dfsGroup(c, n, allowRuns, i, sets, 1, partials, r);
        }
        if (room) {
            dfsGroup(c, n, allowRuns, i, sets, pair, partials + 1, r);
        }
        c[i] += 2;
    }
    if (allowRuns && i + 1 < n && c[i + 1] > 0 && room) {              // 两面/边张搭子
        c[i]--;
        c[i + 1]--;
        dfsGroup(c, n, allowRuns, i, sets, pair, partials + 1, r);
        c[i]++;
        c[i + 1]++;
    }
    if (allowRuns && i + 2 < n && c[i + 2] > 0 && room) {              // 嵌张搭子
        c[i]--;
        c[i + 2]--;
        dfsGroup(c, n, allowRuns, i, sets, pair, partials + 1, r);
        c[i]++;
        c[i + 2]++;
    }
    c[i]--;                                            // 孤张丢弃
    dfsGroup(c, n, allowRuns, i, sets, pair, partials, r);
    c[i]++;
}

/** 花色 / 字牌两套缓存的统一取用：算过就直接返回（未算过的槽 `p0 == 0`）。 */
const Reach &groupReach(const uint8_t *c, int n, bool allowRuns, int pattern) {
    Reach &slot = allowRuns ? suitTable()[static_cast<size_t>(pattern)]
                            : honorTable()[static_cast<size_t>(pattern)];
    if (slot.p0 != 0) {
        return slot;
    }
    uint8_t tmp[9];
    for (int i = 0; i < n; i++) {
        tmp[i] = c[i];
    }
    dfsGroup(tmp, n, allowRuns, 0, 0, 0, 0, slot);
    return slot;
}

inline int suitPattern(const Counts &c, int suit) {
    int p = 0;
    for (int j = 8; j >= 0; j--) {                     // 高位在前，随便定的，只要两端一致
        p = p * 5 + c[static_cast<size_t>(suit * 9 + j)];
    }
    return p;
}

inline int honorPattern(const Counts &c) {
    int p = 0;
    for (int j = 6; j >= 0; j--) {
        p = p * 5 + c[static_cast<size_t>(27 + j)];
    }
    return p;
}

/** 整手牌的合并可达集：花色 0/1/2 + 字牌。 */
Reach reachOfHand(const Counts &c) {
    Reach acc;
    acc.p0 = 1ULL;                                     // (s=0, q=0) 恒可达
    uint8_t tmp[9];
    for (int suit = 0; suit < 3; suit++) {
        for (int j = 0; j < 9; j++) {
            tmp[j] = c[static_cast<size_t>(suit * 9 + j)];
        }
        acc = mergeReach(acc, groupReach(tmp, 9, true, suitPattern(c, suit)));
    }
    uint8_t htmp[7];
    for (int j = 0; j < 7; j++) {
        htmp[j] = c[static_cast<size_t>(27 + j)];
    }
    return mergeReach(acc, groupReach(htmp, 7, false, honorPattern(c)));
}

/** 从合并可达集取向听最小值（Java：`8 − 2·面子 − 搭子 − 雀头`，且 `面子+搭子 ≤ 4`）。 */
inline int shantenFromReach(const Reach &acc, int meldCount) {
    int best = 99;
    for (int pair = 0; pair <= 1; pair++) {
        uint64_t m = pair == 0 ? acc.p0 : acc.p1;
        while (m != 0) {
            const int i = std::countr_zero(m);
            m &= m - 1;
            const int s = i / 10;
            const int q = i % 10;
            const int melds = meldCount + s;
            if (melds + q > 4) {
                continue;                               // Java：melds + partials ≤ 4
            }
            const int sh = 8 - 2 * melds - q - pair;
            if (sh < best) {
                best = sh;
            }
        }
    }
    return best;
}

}  // namespace

int shantenStandard(const Counts &c, int meldCount) {
    if (meldCount < 0) {
        meldCount = 0;
    }
    if (meldCount > 4) {
        meldCount = 4;
    }
    return shantenFromReach(reachOfHand(c), meldCount);
}

int shantenChiitoi(const Counts &c) {
    int pairs = 0;
    int kinds = 0;
    for (int k = 0; k < kKindCount; k++) {
        if (c[static_cast<size_t>(k)] > 0) {
            kinds++;
        }
        if (c[static_cast<size_t>(k)] >= 2) {
            pairs++;
        }
    }
    int sh = 6 - pairs;
    if (kinds < 7) {
        sh += 7 - kinds;
    }
    return sh;
}

int shantenKokushi(const Counts &c) {
    int kinds = 0;
    bool pair = false;
    for (int k = 0; k < kKindCount; k++) {
        if (!isYaochuKind(k)) {
            continue;
        }
        if (c[static_cast<size_t>(k)] > 0) {
            kinds++;
        }
        if (c[static_cast<size_t>(k)] >= 2) {
            pair = true;
        }
    }
    return 13 - kinds - (pair ? 1 : 0);
}

int shantenMin(const Counts &c, int meldCount) {
    int s = shantenStandard(c, meldCount);
    if (meldCount == 0) {
        s = std::min(s, shantenChiitoi(c));
        s = std::min(s, shantenKokushi(c));
    }
    return s;
}

// ---------------------------------------------------------------- 参考实现（自检用）
namespace {
void refDfs(Counts &c, int start, int melds, int partials, int head, int &best) {
    int i = start;
    while (i < kKindCount && c[static_cast<size_t>(i)] == 0) {
        i++;
    }
    if (i == kKindCount) {
        const int sh = 8 - 2 * melds - partials - head;
        if (sh < best) {
            best = sh;
        }
        return;
    }
    auto &ci = c[static_cast<size_t>(i)];
    if (ci >= 3 && melds + partials < 4) {
        ci -= 3;
        refDfs(c, i, melds + 1, partials, head, best);
        ci += 3;
    }
    if (i < 27 && i % 9 <= 6 && c[static_cast<size_t>(i + 1)] > 0 && c[static_cast<size_t>(i + 2)] > 0
            && melds + partials < 4) {
        c[static_cast<size_t>(i)]--;
        c[static_cast<size_t>(i + 1)]--;
        c[static_cast<size_t>(i + 2)]--;
        refDfs(c, i, melds + 1, partials, head, best);
        c[static_cast<size_t>(i)]++;
        c[static_cast<size_t>(i + 1)]++;
        c[static_cast<size_t>(i + 2)]++;
    }
    if (ci >= 2) {
        ci -= 2;
        if (head == 0) {
            refDfs(c, i, melds, partials, 1, best);
        }
        if (melds + partials < 4) {
            refDfs(c, i, melds, partials + 1, head, best);
        }
        ci += 2;
    }
    if (i < 27 && i % 9 <= 7 && c[static_cast<size_t>(i + 1)] > 0 && melds + partials < 4) {
        c[static_cast<size_t>(i)]--;
        c[static_cast<size_t>(i + 1)]--;
        refDfs(c, i, melds, partials + 1, head, best);
        c[static_cast<size_t>(i)]++;
        c[static_cast<size_t>(i + 1)]++;
    }
    if (i < 27 && i % 9 <= 6 && c[static_cast<size_t>(i + 2)] > 0 && melds + partials < 4) {
        c[static_cast<size_t>(i)]--;
        c[static_cast<size_t>(i + 2)]--;
        refDfs(c, i, melds, partials + 1, head, best);
        c[static_cast<size_t>(i)]++;
        c[static_cast<size_t>(i + 2)]++;
    }
    ci--;
    refDfs(c, i, melds, partials, head, best);
    ci++;
}
}  // namespace

int shantenStandardRef(const Counts &c, int meldCount) {
    Counts copy = c;
    int best = 99;
    refDfs(copy, 0, meldCount, 0, 0, best);
    return best;
}

}  // namespace trainer
