#include "payments.hpp"

#include <algorithm>

namespace trainer {
namespace {

/** 自摸时该付款者的份额倍数：庄家 2、闲家 1；和牌者是庄家时三家都 2。 */
int tsumoMult(bool winnerDealer, int payer, int dealer) {
    if (winnerDealer) {
        return 2;
    }
    return payer == dealer ? 2 : 1;
}

/**
 * 本场棒。
 *
 * <p>`docs/日本麻将.md` §包牌 L802：触发包牌时**包牌者需要支付所有的本场棒** ——
 * 常规是自摸三家各 100/本场（合计 300）、荣和放铳者 300/本场，包牌时这 300/本场**整体**
 * 改由包牌者出（和牌者收入不变，只有"谁出"变了）。
 */
void applyHonba(PaymentResult &res, int winner, int loser, int honba, bool tsumo,
                const std::array<int, 4> &payers, int payerCount) {
    if (honba <= 0) {
        return;
    }
    const int honbaPer = 100 * honba;
    if (payerCount == 0) {
        if (tsumo) {
            for (int s = 0; s < 4; s++) {
                if (s != winner) {
                    res.delta[static_cast<size_t>(s)] -= honbaPer;
                }
            }
        } else {
            res.delta[static_cast<size_t>(loser)] -= 3 * honbaPer;
        }
        return;
    }
    const int total = 3 * honbaPer;
    const int each = (total / payerCount / 100) * 100;
    int rem = total - each * payerCount;            // 100 的整数倍
    for (int i = 0; i < payerCount; i++) {
        int share = each;
        if (rem >= 100) {
            share += 100;
            rem -= 100;
        }
        res.delta[static_cast<size_t>(payers[static_cast<size_t>(i)])] -= share;
    }
}

}  // namespace

int ceil100(int v) {
    if (v <= 0) {
        return 0;
    }
    return ((v + 99) / 100) * 100;
}

PaymentResult paymentsCompute(const HandScore &score, int winner, int loser, int dealer, int honba,
                              int sticks, bool tsumo, const std::vector<Pao> &paos) {
    PaymentResult res;
    const int totalBase = score.base;
    // 归并成"每个包牌者一共包了多少基本点"；`left` = 分给包牌者之后剩下的基本点
    std::array<int, 4> paoOf{};
    int left = totalBase;
    for (const Pao &p : paos) {
        if (p.seat < 0 || p.seat == winner || p.base <= 0) {
            continue;
        }
        const int take = std::min(p.base, left);
        paoOf[static_cast<size_t>(p.seat)] += take;
        left -= take;
    }
    const int restBase = left;
    // 责任者的**出场顺序**（按列表去重）：本场棒的余数给靠前那位
    std::array<int, 4> payers{};
    int payerCount = 0;
    for (const Pao &p : paos) {
        if (p.seat < 0 || p.seat == winner || paoOf[static_cast<size_t>(p.seat)] <= 0) {
            continue;
        }
        bool seen = false;
        for (int i = 0; i < payerCount; i++) {
            if (payers[static_cast<size_t>(i)] == p.seat) {
                seen = true;
                break;
            }
        }
        if (!seen) {
            payers[static_cast<size_t>(payerCount++)] = p.seat;
        }
    }
    const bool winnerDealer = winner == dealer;

    if (tsumo) {
        for (int s = 0; s < 4; s++) {
            if (s == winner) {
                continue;
            }
            res.delta[static_cast<size_t>(s)] -=
                    ceil100(tsumoMult(winnerDealer, s, dealer) * restBase);
        }
        for (int i = 0; i < payerCount; i++) {
            int amount = 0;
            for (int s = 0; s < 4; s++) {
                if (s == winner) {
                    continue;
                }
                amount += ceil100(tsumoMult(winnerDealer, s, dealer) * paoOf[static_cast<size_t>(payers[static_cast<size_t>(i)])]);
            }
            res.delta[static_cast<size_t>(payers[static_cast<size_t>(i)])] -= amount;
        }
    } else {
        const int mult = winnerDealer ? 6 : 4;
        res.delta[static_cast<size_t>(loser)] -= ceil100(mult * restBase);
        for (int i = 0; i < payerCount; i++) {
            const int paoTotal = ceil100(mult * paoOf[static_cast<size_t>(payers[static_cast<size_t>(i)])]);
            const int half = (paoTotal / 2 / 100) * 100;
            if (payers[static_cast<size_t>(i)] == loser) {
                res.delta[static_cast<size_t>(loser)] -= paoTotal;      // 包牌者即放铳者：全额
            } else {
                res.delta[static_cast<size_t>(payers[static_cast<size_t>(i)])] -= half;
                res.delta[static_cast<size_t>(loser)] -= paoTotal - half;
            }
        }
    }
    applyHonba(res, winner, loser, honba, tsumo, payers, payerCount);

    int gain = 0;
    for (int s = 0; s < 4; s++) {
        if (s != winner) {
            gain -= res.delta[static_cast<size_t>(s)];
        }
    }
    res.delta[static_cast<size_t>(winner)] += gain;
    res.winnerPoints = gain;
    res.riichiTaken = 1000 * sticks;
    res.delta[static_cast<size_t>(winner)] += res.riichiTaken;
    res.winnerGain = gain + res.riichiTaken;
    return res;
}

std::array<int, 4> notenPenalty(const std::array<bool, 4> &tenpai, int total) {
    std::array<int, 4> d{};
    int n = 0;
    for (bool b : tenpai) {
        if (b) {
            n++;
        }
    }
    if (n == 0 || n == 4 || total <= 0) {
        return d;
    }
    const int receivers = n;
    const int payersN = 4 - n;
    const int get = (total / receivers / 100) * 100;      // 收方每家（罚符以 100 为单位）
    if (get <= 0) {
        return d;
    }
    const int need = get * receivers;
    int base = (need / payersN / 100) * 100;
    int rest = need - base * payersN;
    for (int s = 0; s < 4; s++) {
        if (tenpai[static_cast<size_t>(s)]) {
            d[static_cast<size_t>(s)] = get;
        } else {
            const int extra = std::min(rest, 100);
            d[static_cast<size_t>(s)] = -(base + extra);
            rest -= extra;
        }
    }
    return d;
}

}  // namespace trainer
