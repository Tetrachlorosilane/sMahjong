#pragma once

// 牌的表示与转换（严格对齐 docs/PROTOCOL.md §1）
//
//   kind 0..8   = 1m..9m
//   kind 9..17  = 1p..9p
//   kind 18..26 = 1s..9s
//   kind 27..33 = 1z..7z（1z=东 2z=南 3z=西 4z=北 5z=白 6z=发 7z=中）
//
//   赤宝牌：kind ∈ {4,13,22} 且为赤五；字符串写作 "0m"/"0p"/"0s"。

#include <QChar>
#include <QString>
#include <QStringList>

namespace mj {

// 牌种总数（不含赤宝牌，赤五与普通五同 kind）
constexpr int KindCount = 34;

// 解析牌字符串 → kind（0..33）；非法返回 -1。red 输出是否赤宝牌。
int kindOfTile(const QString& tile, bool* red = nullptr);

// (kind, red) → 牌字符串；kind 非法返回空串
QString tileString(int kind, bool red = false);

// 是否为赤宝牌字符串（"0m"/"0p"/"0s"）
bool isRedTile(const QString& tile);

// kind 是否合法
inline bool isValidKind(int kind) { return kind >= 0 && kind < KindCount; }

// 花色字符：m / p / s / z
QChar suitOfKind(int kind);

// 点数：万筒索 1..9，字牌 1..7
int numberOfKind(int kind);

// 中文短名（用于自检图标注与提示），如 "五万"、"东"、"白"
QString tileLabel(const QString& tile);

/**
 * 手牌排序（**理牌**）：按牌种 0..33 升序，同点数时普通牌在赤宝牌前。
 *
 * <p>`TableModel` 里的自家手牌与回放「显示他家手牌」的别家手牌**必须用同一把尺子**
 * （`QStringList::sort()` 是字典序 —— 赤五 `0m` 会被排到 `1m` 前面，看着像没理牌）。
 */
void sortTiles(QStringList& tiles);

} // namespace mj
