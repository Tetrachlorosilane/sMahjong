#include "roundscoring.hpp"

#include <algorithm>
#include <cmath>

#include "agari.hpp"

namespace trainer {
namespace {

/** 把一段顺位点（马点或头名赏）拆给同分的 `[i..j]` 几家：0.1 分单位向下取整，尾数归第一家。 */
void splitPoint(std::array<double, 4> &out, int i, int j, double total) {
    const int n = j - i + 1;
    const std::array<int, 2> sr =
            splitRemainder(static_cast<int>(std::llround(total * 10)), n, 1);
    for (int k = i; k <= j; k++) {
        out[static_cast<size_t>(k)] = sr[0] / 10.0;
    }
    out[static_cast<size_t>(i)] += sr[1] / 10.0;
}

}  // namespace

std::array<int, 2> splitRemainder(int total, int n, int unit) {
    // ⚠ 必须向下取整（Java `Math.floorDiv`）：末位的顺位点是**负**的，C++ 的截断除法会给出负余数
    const int divisor = n * unit;
    int q = total / divisor;
    if (total % divisor != 0 && ((total < 0) != (divisor < 0))) {
        q--;
    }
    const int each = q * unit;
    return {each, total - each * n};
}

std::array<int, 4> endGameSticks(const std::array<int, 4> &scores, int sticks) {
    std::array<int, 4> add{};
    if (sticks <= 0) {
        return add;
    }
    int top = scores[0];
    for (int i = 1; i < 4; i++) {
        top = std::max(top, scores[static_cast<size_t>(i)]);
    }
    std::array<int, 4> tied{};
    int n = 0;
    for (int i = 0; i < 4; i++) {
        if (scores[static_cast<size_t>(i)] == top) {
            tied[static_cast<size_t>(n++)] = i;      // 自然按座次升序
        }
    }
    const int total = sticks * 1000;
    if (n == 1) {
        add[static_cast<size_t>(tied[0])] = total;
        return add;
    }
    const std::array<int, 2> sr = splitRemainder(total, n, 100);   // 立直棒以 100 点为单位拆
    for (int k = 0; k < n; k++) {
        add[static_cast<size_t>(tied[static_cast<size_t>(k)])] = sr[0];
    }
    add[static_cast<size_t>(tied[0])] += sr[1];                    // 尾数全归更接近起家者
    return add;
}

Settlement settle(const std::array<int, 4> &scores, const Rules &rules) {
    Settlement st;
    std::array<int, 4> idx = {0, 1, 2, 3};
    std::sort(idx.begin(), idx.end(), [&scores](int a, int b) {
        if (scores[static_cast<size_t>(a)] != scores[static_cast<size_t>(b)]) {
            return scores[static_cast<size_t>(a)] > scores[static_cast<size_t>(b)];
        }
        return a < b;
    });
    for (int i = 0; i < 4; i++) {
        st.order[static_cast<size_t>(i)] = idx[static_cast<size_t>(i)];
        st.rank[static_cast<size_t>(idx[static_cast<size_t>(i)])] = i;
    }
    const double oka = (rules.returnScore - rules.startScore) * 4 / 1000.0;
    for (int i = 0; i < 4; i++) {
        st.uma[static_cast<size_t>(i)] = rules.uma[static_cast<size_t>(i)];
        st.oka[static_cast<size_t>(i)] = (i == 0) ? oka : 0;
    }
    if (rules.tieSplitPoint) {
        int i = 0;
        while (i < 4) {
            int j = i;
            while (j + 1 < 4
                   && scores[static_cast<size_t>(idx[static_cast<size_t>(j + 1)])]
                           == scores[static_cast<size_t>(idx[static_cast<size_t>(i)])]) {
                j++;
            }
            if (j > i) {
                double u = 0;
                double o = 0;
                for (int k = i; k <= j; k++) {
                    u += rules.uma[static_cast<size_t>(k)];
                    o += (k == 0) ? oka : 0;
                }
                splitPoint(st.uma, i, j, u);
                splitPoint(st.oka, i, j, o);
                for (int k = i; k <= j; k++) {
                    st.rank[static_cast<size_t>(idx[static_cast<size_t>(k)])] = i;   // 并列同顺位
                }
            }
            i = j + 1;
        }
    }
    for (int i = 0; i < 4; i++) {
        const int s = idx[static_cast<size_t>(i)];
        st.point[static_cast<size_t>(s)] = (scores[static_cast<size_t>(s)] - rules.returnScore) / 1000.0
                + st.uma[static_cast<size_t>(i)] + st.oka[static_cast<size_t>(i)];
    }
    return st;
}

int nextHonba(int honba, bool dealerRenchan, bool agari, bool nagashi) {
    if (dealerRenchan) {
        return honba + 1;
    }
    if (agari) {
        return 0;                     // 闲家和了 → 轮庄且清零
    }
    if (nagashi) {
        return 0;                     // 流局满贯：按"和了"处理本场
    }
    return honba + 1;                 // 荒牌流局庄家不听 → 本场 +1 后再轮庄
}

bool exhaustiveBy(int dealer, const std::array<bool, 4> &tenpai) {
    return dealer >= 0 && dealer < 4 && tenpai[static_cast<size_t>(dealer)];
}

bool nagashiBy(int dealer, const std::array<bool, 4> &tenpai) {
    return exhaustiveBy(dealer, tenpai);
}

bool stopAtAllLast(int dealer, bool agari, bool nagashi, const std::array<bool, 4> &tenpai,
                   const std::array<int, 4> &scores, const Rules &rules) {
    if (!rules.agariyame || dealer < 0 || dealer > 3) {
        return false;
    }
    const bool tenpaiAbort = !nagashi && tenpai[static_cast<size_t>(dealer)];
    if (!agari && !tenpaiAbort) {
        return false;
    }
    if (scores[static_cast<size_t>(dealer)] < rules.requiredPoints) {
        return false;
    }
    int top = scores[0];
    for (int i = 1; i < 4; i++) {
        top = std::max(top, scores[static_cast<size_t>(i)]);
    }
    return scores[static_cast<size_t>(dealer)] >= top;
}

int lastWindOf(const Rules &rules) { return rules.length == "tonpuu" ? 0 : 1; }

bool keepPlayingWest(const Rules &rules, int top, int nextRoundWind, int lastWind) {
    return rules.westExtension && nextRoundWind <= lastWind + 1 && top < rules.requiredPoints;
}

std::array<int, 4> nagashiPayments(int winnerSeat, int dealer) {
    std::array<int, 4> d{};
    const bool dealerWin = winnerSeat == dealer;
    int total = 0;
    for (int o = 0; o < 4; o++) {
        if (o == winnerSeat) {
            continue;
        }
        const int amt = dealerWin ? 4000 : (o == dealer ? 4000 : 2000);
        d[static_cast<size_t>(o)] -= amt;
        total += amt;
    }
    d[static_cast<size_t>(winnerSeat)] += total;
    return d;
}

bool nagashiEligible(const std::vector<int> &river, bool calledFrom) {
    if (river.empty() || calledFrom) {
        return false;
    }
    for (int id : river) {
        if (!isYaochu(kindOf(id))) {
            return false;
        }
    }
    return true;
}

bool tenpaiOf(const Counts &concealed, int meldCount) {
    return !agariWaits(concealed, meldCount).empty();
}

}  // namespace trainer
