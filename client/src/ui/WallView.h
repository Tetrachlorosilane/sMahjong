#pragma once

// 牌山整体视图：**136 张按抓牌顺序铺开**，并标出牌局进展。
//
// 布局（PROTOCOL §3.11 的抓牌顺序）：
//   · 一行 4 张（**每列 4 张**）—— 这 4 张是抓牌序列里连续的 4 张，**与"哪一家"无关**：
//     副露（吃/碰/杠）会改变下一个摸牌的人，所以任何"按玩家分行"的排法都是错的。
//   · 阅读顺序：**每列自上而下**（第 1~4 张）、再从左到右下一列。每 4 列留一个稍大的空隙，
//     纯粹是防止看花眼的视觉分组。
//   · 末尾 14 张（k ≥ 122）是王牌：4 张岭上 + 5 张表宝牌指示牌 + 5 张里宝指示牌，
//     仍在同一序列里，只是底色不同。
//   · 每张牌按**当前步**着色：未摸到（牌背）/ 手牌 / 已打出（灰）/ 已副露（绿框）/
//     王牌（黄底）/ 开杠移入王牌（红底）；**底部一条细线是"谁拿走的"的颜色**（图例在标题行）。
//
// 为什么要单独一个窗口：牌桌视图只能表现"当前局面"，而"牌山走到哪了、还剩什么"是跨整局的信息，
// 塞进牌桌会把风盘挤坏（见 AGENTS §6.2）。

#include "../model/ReplayModel.h"   // 牌山归属结构（Round / WallSlot）就是模型里的那两个

#include <QVector>
#include <QWidget>

class QLabel;

class WallView : public QWidget
{
    Q_OBJECT
public:
    explicit WallView(QWidget* parent = nullptr);

    /** 绑定模型 + 当前小局 + 当前步（步 = entries 下标）。 */
    void setState(ReplayModel* model, int round, int step);

signals:
    /** 点某一张牌 → 请求跳到"它被拿走"的那一步（-1 = 王牌/还没摸到）。 */
    void seekRequested(int entryIndex);

protected:
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;

private:
    enum Fate { Untaken, InHand, Discarded, Melded, DeadWall, MovedByKan };

    struct Cell
    {
        int wallIndex = -1;
        Fate fate = Untaken;
        int seat = -1;          // 谁拿走的（-1 = 还没被拿走 / 王牌）
        int entryIndex = -1;    // 何时被拿走
        QRect rect;
    };

    void rebuild();
    /**
     * 一张**已被拿走**的牌现在在哪（手牌 / 已打出 / 已副露）。
     *
     * <p>牌山里同一牌码有多份（最多 4 张），所以只能按"这一张是该家同类份里的第几张"
     * 依次去占牌河与副露的名额，剩下的算在手里 —— 这是**视觉近似**，够用且不需要新协议字段。
     */
    Fate fateOfTakenTile(const ReplayModel::Round& r,
                         const QVector<ReplayModel::WallSlot>& wallSlots,
                         const ReplayModel::WallSlot& s, int k) const;

    ReplayModel* m_model = nullptr;
    int m_round = 0;
    int m_step = 0;
    QVector<Cell> m_cells;      // 136 张，下标 = 抓牌顺序
    QLabel* m_title = nullptr;
    int m_cellW = 26;
    int m_cellH = 35;
    int m_gap = 3;
    int m_groupGap = 14;        // 每 4 列的视觉分隔（纯阅读辅助）
};
