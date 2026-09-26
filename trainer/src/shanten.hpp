// 向听数（一般型 / 七对子 / 国士）—— 与 Java `mahjong.rules.Shanten` **同值**，但换算法：
// Java 是「对整手 34 种牌做一次全局 DFS」（实测占训练回路 99% 的 CPU），
// 这里改成「按花色分组枚举 + 组合」并把每组的可达 (面子, 雀头, 搭子) 组合**按牌型缓存**。
//
// 相等性的论证（也见 docs/TRAINER-CPP.md §5 M1）：
//   Java 的判据 = max(2*melds + partials + head)，约束 melds+partials ≤ 4（melds 含副露数），
//   面子 = 刻子/顺子，搭子 = 对子/两面/边张/嵌张，head = 用对子当雀头（不占搭子额度）。
//   分组后**组内**的合法组合与整手 DFS 完全同构（顺子/搭子不跨花色），
//   再把四组做一次 (面子, 雀头, 搭子) 的背包合并 + 同一条全局约束 ⇒ 极值必然一致。
//   ⚠ 组内 DFS 允许「面子+搭子 ≤ 4」的局部组合，全局约束在合并后统一过滤（组内上限比全局松不会漏解）。
#pragma once

#include <cstdint>

#include "counts.hpp"

namespace trainer {

/** 一般型向听（含副露：`meldCount` 个已完成面子）。和了形 = -1。 */
int shantenStandard(const Counts& c, int meldCount);

/** 七对子向听（副露存在时 Java 不参与取最小，但函数本身照算）。 */
int shantenChiitoi(const Counts& c);

/** 国士无双向听。 */
int shantenKokushi(const Counts& c);

/** 三种牌型取最小（与 Java `Shanten.min` 同口径：只有 `meldCount == 0` 才并七对子/国士）。 */
int shantenMin(const Counts& c, int meldCount);

/** **参考实现**：Java `Shanten.dfs` 的逐行移植（只用于自检对拍，不用于生产路径）。 */
int shantenStandardRef(const Counts& c, int meldCount);

/** 「和了形」判定（不查役种）。 */
inline bool isAgari(const Counts& c, int meldCount) { return shantenMin(c, meldCount) < 0; }

/** 听牌判定。 */
inline bool isTenpai(const Counts& c, int meldCount) { return shantenMin(c, meldCount) <= 0; }

}  // namespace trainer
