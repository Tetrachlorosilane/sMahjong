#pragma once

// 牌山整体视图：**136 张按抓牌顺序**铺开，并标出牌局进展。
//
// 布局（PROTOCOL §3.11 的坐标）：
//   行 = 四家（从庄家起逆时针，第 0 行就是庄家）
//   列 = 该家第几次拿牌（0 起）：前 3 轮各 4 张（每 4 列一组、组间留空隙）、
//        第 13 张、庄家的第 14 张、之后是牌局中一张一张的摸牌
//   底部单独一条：王牌 14 张（4 岭上 / 5 表宝牌指示牌 / 5 里宝指示牌）
//
// 每张牌按**当前时刻**的去向着色：未摸到（牌背）/ 在手里 / 已打出（灰）/ 已副露（蓝框）/
// 王牌（黄底）/ 开杠移入王牌（红底）。当前这一步摸到的那张加红框。
//
// 为什么要单独一个窗口：牌桌视图（`TableView`）只能表现"当前局面"，
// 而"牌山走到哪了、还剩什么"是**跨整局**的信息，塞进牌桌会把风盘挤坏（见 AGENTS §6.2）。

#include <QVector>
#include <QWidget>

class QLabel;
class ReplayModel;

class WallView : public QWidget
{
    Q_OBJECT
public:
    explicit WallView(QWidget* parent = nullptr);

    /** 绑定模型 + 当前小局 + 当前步（步 = entries 下标）。 */
    void setState(ReplayModel* model, int round, int step);
    /** 换小局（重新按该小局的牌山排布）。 */
    void setRound(int round);

signals:
    /** 点某一张牌 → 请求跳到"它被拿走"的那一步（-1 = 王牌/未摸到）。 */
    void seekRequested(int entryIndex);

protected:
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;

private:
    enum Fate { Untaken, InHand, Discarded, Melded, DeadWall, MovedByKan };

    struct Cell
    {
        int wallIndex = -1;     // 牌山下标（-1 = 空位）
        Fate fate = Untaken;
        int entryIndex = -1;    // 何时被拿走
        QRect rect;
    };

    void rebuild();
    Fate fateOf(int wallIndex, int seat, const QVector<int>& assigned) const;

    ReplayModel* m_model = nullptr;
    int m_round = 0;
    int m_step = 0;
    QVector<QVector<Cell>> m_rows;      // 行 = 座位（0..3，按抓牌顺序从庄家起）
    QVector<Cell> m_deadCells;          // 王牌 14 张
    QVector<int> m_seatOfRow;           // 每行对应的座位
    QLabel* m_title = nullptr;
    int m_cellW = 26;
    int m_cellH = 35;
    int m_gap = 3;
    int m_groupGap = 9;
};
