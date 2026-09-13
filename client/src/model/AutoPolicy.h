#pragma once

// 自动应答策略：牌桌外那三个开关（自动胡了 / 不吃碰杠 / 自动摸切）替玩家做什么动作。
// **纯逻辑、不碰控件**，所以能在自检里拿假询问直接断言（见 SelfTest 的「自动开关」组）。
//
// ⚠ 铁律：**和牌永远优先**。本次询问的 options 里只要出现 `tsumo` / `ron`，
//   「自动摸切」与「不吃碰杠」就都不许替玩家动 ——
//   否则会「切出本来能胡的那张牌」，或把到手的荣和 `pass` 掉。
//   判据**只看服务端下发的 options**（客户端不自己算听牌/和牌，见 AGENTS §2.1），
//   所以服务端加/减和牌选项时这里自动跟着变，不会两边判据不一致。

#include <QJsonObject>
#include <QString>

namespace autopolicy {

// 三个开关的当前状态（由 AutoBar 收集）
struct Flags {
    bool autoWin = false;        // 自动胡了：能自摸就自摸、能荣和就荣和
    bool noCall = false;         // 不吃碰杠：别人的舍张一律不叫（吃/碰/杠都不）
    bool autoTsumogiri = false;  // 自动摸切：轮到自己就把刚摸到的那张打出去

    bool any() const { return autoWin || noCall || autoTsumogiri; }
};

// 在 ask.options 里找指定 type 的选项；没有则返回空对象。
QJsonObject optionOf(const QJsonObject& ask, const QString& type);

// 唯一的决策入口：返回要替玩家发出的动作（只含 `type` 与可能的 `tile`；
// `cmd` / `ask_id` 由 ActionBar::actionCmd/discardCmd 补，见 AGENTS §2.3-9）。
// 返回**空对象 = 不自动应答**，交给玩家自己点。
QJsonObject decide(const Flags& f, const QJsonObject& ask, const QString& kind,
                   const QString& drawnTile);

} // namespace autopolicy
