#include "agari.hpp"

#include <vector>

#include "shanten.hpp"
#include "tiles.hpp"

namespace trainer {
namespace {

/** 枚举出的"暗手部分"形状：雀头 + n 个面子（不含副露）。 */
struct Shape {
    int pair = -1;
    int nSets = 0;
    std::array<int, 4> type{};
    std::array<int, 4> start{};
};

/** 与 Java `Agari.enumSets` 逐行同序（刻子在前、顺子在后；`need == 0` 要求恰好拆空）。 */
void enumSets(Counts &c, int from, int need, Shape &shape, std::vector<Shape> &out) {
    if (need == 0) {
        for (int k = from; k < kKindCount; k++) {
            if (c[static_cast<size_t>(k)] != 0) {
                return;
            }
        }
        out.push_back(shape);
        return;
    }
    int i = from;
    while (i < kKindCount && c[static_cast<size_t>(i)] == 0) {
        i++;
    }
    if (i == kKindCount) {
        return;
    }
    auto &ci = c[static_cast<size_t>(i)];
    if (ci >= 3) {                                       // 刻子
        ci -= 3;
        shape.type[static_cast<size_t>(shape.nSets)] = kSetTriplet;
        shape.start[static_cast<size_t>(shape.nSets)] = i;
        shape.nSets++;
        enumSets(c, i, need - 1, shape, out);
        shape.nSets--;
        ci += 3;
    }
    if (i < 27 && i % 9 <= 6 && c[static_cast<size_t>(i + 1)] > 0
            && c[static_cast<size_t>(i + 2)] > 0) {      // 顺子
        ci--;
        c[static_cast<size_t>(i + 1)]--;
        c[static_cast<size_t>(i + 2)]--;
        shape.type[static_cast<size_t>(shape.nSets)] = kSetRun;
        shape.start[static_cast<size_t>(shape.nSets)] = i;
        shape.nSets++;
        enumSets(c, i, need - 1, shape, out);
        shape.nSets--;
        ci++;
        c[static_cast<size_t>(i + 1)]++;
        c[static_cast<size_t>(i + 2)]++;
    }
}

bool isKokushi(const Counts &c) {
    int kinds = 0;
    int total = 0;
    for (int k = 0; k < kKindCount; k++) {
        const int v = c[static_cast<size_t>(k)];
        if (v == 0) {
            continue;
        }
        if (!isYaochu(k)) {
            return false;
        }
        kinds++;
        total += v;
        if (v > 2) {
            return false;
        }
    }
    return kinds == 13 && total == 14;
}

bool allOrphanKinds(const Counts &c) {
    int kinds = 0;
    for (int k = 0; k < kKindCount; k++) {
        if (isYaochu(k) && c[static_cast<size_t>(k)] > 0) {
            kinds++;
        }
    }
    return kinds == 13;
}

/** 为一个「形」加上副露面子，并展开和了牌归属的所有变体（与 Java `addWinVariants` 同序）。 */
void addWinVariants(std::vector<Form> &out, const Shape &shape, int winKind,
                    const std::vector<Meld> &melds) {
    const int pair = shape.pair;
    const int n = shape.nSets;
    const int meldCount = static_cast<int>(melds.size());
    if (n + meldCount != 4) {
        return;
    }
    Form base;
    base.pair = pair;
    base.nSets = n;
    for (int i = 0; i < n; i++) {
        base.setType[static_cast<size_t>(i)] = shape.type[static_cast<size_t>(i)];
        base.setStart[static_cast<size_t>(i)] = shape.start[static_cast<size_t>(i)];
        base.setConcealed[static_cast<size_t>(i)] = true;
        base.setFromMeld[static_cast<size_t>(i)] = false;
    }
    // 和了牌归属：[面子下标(-1 = 雀头), waitType]
    std::array<std::array<int, 2>, 5> assign{};
    int nAssign = 0;
    if (pair == winKind) {
        assign[static_cast<size_t>(nAssign++)] = {-1, kWaitTanki};
    }
    for (int i = 0; i < n; i++) {
        if (base.setType[static_cast<size_t>(i)] == kSetRun) {
            const int s = base.setStart[static_cast<size_t>(i)];
            if (winKind == s) {
                // 缺顺子最小牌：手中剩 s+1,s+2；只有 8,9 才是边张
                assign[static_cast<size_t>(nAssign++)] = {i, (s % 9 == 6) ? kWaitPenchan : kWaitRyanmen};
            } else if (winKind == s + 1) {
                assign[static_cast<size_t>(nAssign++)] = {i, kWaitKanchan};
            } else if (winKind == s + 2) {
                // 缺顺子最大牌：手中剩 s,s+1；只有 1,2 才是边张
                assign[static_cast<size_t>(nAssign++)] = {i, (s % 9 == 0) ? kWaitPenchan : kWaitRyanmen};
            }
        } else if (base.setType[static_cast<size_t>(i)] == kSetTriplet
                   && base.setStart[static_cast<size_t>(i)] == winKind) {
            assign[static_cast<size_t>(nAssign++)] = {i, kWaitShanpon};
        }
    }
    if (nAssign == 0) {
        return;
    }
    for (int a = 0; a < nAssign; a++) {
        Form f = base;
        f.winSet = assign[static_cast<size_t>(a)][0];
        f.waitType = assign[static_cast<size_t>(a)][1];
        int idx = f.nSets;
        for (const Meld &m : melds) {
            f.setType[static_cast<size_t>(idx)] = m.isKan() ? kSetQuad : (m.isRun() ? kSetRun : kSetTriplet);
            f.setStart[static_cast<size_t>(idx)] = m.baseKind();
            f.setConcealed[static_cast<size_t>(idx)] = !m.open();
            f.setFromMeld[static_cast<size_t>(idx)] = true;
            idx++;
        }
        f.nSets = idx;
        out.push_back(f);
    }
}

}  // namespace

std::vector<Form> decompose(const Counts &counts, const std::vector<Meld> &melds, int winKind) {
    std::vector<Form> out;
    const int meldCount = static_cast<int>(melds.size());
    const int need = 4 - meldCount;
    Counts c = counts;
    if (sumOf(c) != need * 3 + 2) {
        return out;
    }
    std::vector<Shape> rawShapes;
    for (int p = 0; p < kKindCount; p++) {
        if (c[static_cast<size_t>(p)] < 2) {
            continue;
        }
        c[static_cast<size_t>(p)] -= 2;
        Shape shape;
        shape.pair = p;
        enumSets(c, 0, need, shape, rawShapes);
        c[static_cast<size_t>(p)] += 2;
    }
    for (const Shape &shape : rawShapes) {
        addWinVariants(out, shape, winKind, melds);
    }
    if (meldCount == 0) {
        // ---- 七对子
        bool ok = true;
        for (int k = 0; k < kKindCount; k++) {
            if (c[static_cast<size_t>(k)] != 0 && c[static_cast<size_t>(k)] != 2) {
                ok = false;
                break;
            }
        }
        if (ok) {
            Form f;
            f.type = kTypeChiitoitsu;
            f.pair = winKind;
            f.nSets = 0;
            f.winSet = -1;
            f.waitType = kWaitTanki;
            out.push_back(f);
        }
        // ---- 国士无双
        if (isKokushi(c)) {
            Form f;
            f.type = kTypeKokushi;
            f.pair = winKind;
            f.nSets = 0;
            Counts pre = c;
            pre[static_cast<size_t>(winKind)]--;
            f.kokushi13 = allOrphanKinds(pre);
            f.winSet = -1;
            f.waitType = kWaitTanki;
            out.push_back(f);
        }
    }
    return out;
}

std::vector<int> agariWaits(const Counts &counts13, int meldCount) {
    std::vector<int> out;
    // ⚠ 非法输入防护：真实牌局里同一牌种最多 4 张，但**语料可以手写**（`trainer settle` 的 `furiten`
    //   行撞到过同种 5 张）。`isAgari` 走查表向听，而表的计数维只覆盖 0..4 → 多一张就**越界崩**
    //   （实测 `0xC0000005`）。Java 的 DFS 对这份输入不崩，但那是"两边都接受非法输入"的另一回事 ——
    //   这里只保证 **C++ 侧不崩**：返回空集，于是对拍会**响亮地失败**而不是把进程挂掉。
    for (int k = 0; k < kKindCount; k++) {
        if (counts13[static_cast<size_t>(k)] > 4) {
            return out;
        }
    }
    Counts c = counts13;
    for (int k = 0; k < kKindCount; k++) {
        if (c[static_cast<size_t>(k)] >= 4) {
            continue;
        }
        c[static_cast<size_t>(k)]++;
        if (isAgari(c, meldCount)) {
            out.push_back(k);
        }
        c[static_cast<size_t>(k)]--;
    }
    return out;
}

}  // namespace trainer
