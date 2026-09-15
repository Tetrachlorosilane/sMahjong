#pragma once

// 牌桌绘制控件：只用 QPainter 手绘，随窗口自适应。扁平风格。
//
// 方位约定（docs/PROTOCOL.md §4）：屏幕方位 pos = (seat - mySeat + 4) % 4
//   pos 0 = 下（自己，画真实手牌） / 1 = 右（下家） / 2 = 上（对家） / 3 = 左（上家）
//
// 布局参考雀魂 / 天凤：
//   * 四家手牌贴控件外缘（自己在底部居中、对家在上、上下家竖排在左右）
//   * 四家牌河贴着中央风盘围成一圈（每行 6 张，向外生长）
//   * 中央风盘只放「局数 / 余牌 / 供託 / 四家点数（按方位排布）」
//   * 宝牌指示牌在**风盘内、紧贴対面点数下方**，一局最多 5 张（不再占屏幕左上角）
//   * 玩家名牌半透明，贴在自己手牌旁边（不放得点，得点在风盘内）
//
// **几何求解已经拆到 `ui/TableLayout.{h,cpp}`**（AUDIT §3.2-1）：本文件的职责只剩
// 「画」与「收事件」。布局结果都在 `m_layout` 里，绘制处只读不算 ——
// 以前布局与绘制混在一起，几何没法单独断言，只能靠抓图数像素。
// 本类**不再持有任何布局中间量**，只保留纯绘制的输出（点亮的手牌矩形、上一帧的动画状态等）。
//
// 实现要点：每家在自己的「局部坐标系」里绘制（局部 +v 指向该家自己、+u 指向该家右手侧），
// 再通过旋转变换映射到屏幕 —— 因此侧家的牌自然横置、"横置/非横置"以**牌主视角**判定：
// 邻家的非横置牌在本家看来是横着的，邻家的横置牌（立直宣言牌、副露里被鸣的那张）在本家看来是竖着的。

#include "model/TableModel.h"   // Meld
#include "ui/TableLayout.h"     // SeatFrame / HandLayout / TableLayout

#include <QColor>
#include <QRectF>
#include <QString>
#include <QStringList>
#include <QTimer>
#include <QVector>
#include <QWidget>

class TableModel;

class TableView : public QWidget
{
    Q_OBJECT
public:
    explicit TableView(QWidget* parent = nullptr);

    void setModel(TableModel* model);
    TableModel* model() const { return m_model; }

    void setStatusText(const QString& text);
    void showToast(const QString& text, const QColor& color = QColor(0xFF, 0xD2, 0x4A));
    void setHighlightTiles(const QStringList& tiles);

    /**
     * **上帝视角**（回放「显示他家手牌」）：把四家暗牌交给 `TableModel`，本控件只负责画。
     *
     * @param hands 四家的暗牌（**不含**刚摸到的那张；空串表示该家未知）
     * @param drawn 四家刚摸到的那张（空 = 没有），单独占一格、与自家规则一致
     *
     * ⚠ 为什么要经过模型：手牌区的**布局**是按 `TableModel::concealedCount()` 算的
     * （行宽、摸牌槽位置），出牌动画的起点也按布局算。数据只放在控件里的话，
     * 布局用"张数"、绘制用另一份手牌 → 张数不对、牌飞出的格子也不对。
     * 传空即恢复实时对局的行为（别家画牌背）。
     */
    void setGodHands(const QVector<QStringList>& hands, const QStringList& drawn);
    /** 关闭上帝视角（恢复实时对局画法）。 */
    void clearGodHands();

signals:
    void tileClicked(const QString& tile, int index);
    /** 点了某家的名牌 → 请求切到那家的视角（回放用）。 */
    void seatClicked(int seat);

protected:
    void paintEvent(QPaintEvent* event) override;
    void mousePressEvent(QMouseEvent* event) override;

private:
    // 出牌 / 鸣牌的飞行动画（局部坐标，随所属座位的变换一起旋转）
    struct Flight {
        QString tile;
        bool red = false;
        QRectF from;
        QRectF to;
        qreal t = 0.0;
        int pos = 0;           // 屏幕方位（决定用哪个局部坐标系）
        int seat = -1;         // 牌河所属座位
        int riverIndex = -1;   // 飞行期间跳过绘制该牌河槽位（避免"复制一张"）
        bool sideways = false; // 目标是否为横置牌
    };

    /** 一行转发到 `m_layout.computeLayout()`；模型为空时用一个空模型顶上（原来 `m_model` 为空时只跳过「弃牌数」那一段）。 */
    void computeLayout();
    void paintBackground(QPainter& p);
    void paintCenter(QPainter& p);
    void paintDoraPanel(QPainter& p);
    void paintSeat(QPainter& p, int pos);
    void paintRiver(QPainter& p, const SeatFrame& f, int seat, const QStringList& tiles,
                    const QVector<bool>& sideways, int skipIndex);
    void paintFlights(QPainter& p);
    void paintToast(QPainter& p);
    void paintNamePlate(QPainter& p, const QRectF& r, int seat, bool active);

    // 牌河第 index 张在屏幕上的矩形（飞行动画终点）
    QRectF riverSlotScreen(int seat, int index) const;

public:
    /**
     * 副露里哪一张横置（自测用；与内部实现同一份逻辑）。
     * 规则：吃 → 被吃的那张；碰/大明杠 → 由**打牌者方位**决定
     * （上家=最左、对家=中间、下家=最右）；暗杠不横置；加杠横在第二张。
     */
    static int meldRotatedIndexForTest(const Meld& m, int ownerSeat);

    /**
     * 自检用：一族牌河要排几行（一行 6 列）。
     * **不封顶**：18 张 = 3 行，第 19 张起第 4 行 —— 多出来的牌继续向外排，不再被裁掉。
     */
    static int riverRowCountForTest(int tiles);
    /** 自检用：上一帧布局为牌河预留的行数（正常 3；某家超过 18 张时更大）。 */
    int riverRowsForTest() const { return m_layout.m_riverRows; }

    /** 角落为名牌/宝牌栏保留的宽度。 */
    qreal plateReserve() const;

    /**
     * 自检用：`computeLayout()` 之后宝牌指示牌栏的屏幕矩形。
     * 一局最多 5 张指示牌（宝牌 1 + 四个杠各 1），它必须容得下 5 张。
     */
    QRectF doraRectForTest() const { return m_layout.m_doraRect; }

    /** 自检用：风盘内的立直棒带 / 自家点数带（两者绝不能相交）。 */
    QRectF stickBandForTest() const { return m_layout.m_stickBand[0]; }
    QRectF selfScoreBandForTest() const { return m_layout.m_scoreBand[0]; }
    QRectF topScoreBandForTest() const { return m_layout.m_scoreBand[2]; }
    QRectF centerBandForTest() const { return m_layout.m_centerBand; }

    /**
     * 自检用：某一侧的「点数带 / 立直棒带」。
     *
     * <p>`pos` 与座位方位同一编号：**0=下(自家) 1=右(下家) 2=上(対面) 3=左(上家)**。
     * 四边同构：**棒在外（贴盘边）、点数在内**，所以「点数到盘边的距离」四边一致。
     */
    QRectF scoreBandForTest(int pos) const {
        return (pos >= 0 && pos < 4) ? m_layout.m_scoreBand[pos] : QRectF();
    }
    QRectF stickBandForTest(int pos) const {
        return (pos >= 0 && pos < 4) ? m_layout.m_stickBand[pos] : QRectF();
    }

    /**
     * 自检用：上一帧里**四家得点框**的尺寸（按 pos 顺序）——
     * 四个必须**完全一样大**（横排两家与竖排两家只是朝向不同）。
     *
     * ⚠ 这是**绘制阶段**的输出（`paintCenter()` 每画一家 append 一份），
     * 框本身的长宽由布局给出（`m_layout.m_scoreBoxSize`），所以四个值必然相等。
     */
    QVector<QSizeF> scoreBoxSizesForTest() const { return m_scoreBoxSizes; }

    /**
     * 自检用：上一帧里「立直棒画在哪几家那一侧」（pos 列表）+ 画在盘中央的**供託**根数。
     * 回归：各家立直棒必须落在**各家自己面前**，不能统统堆在盘底。
     */
    QVector<int> stickPositionsForTest() const { return m_lastStickPos; }
    int potSticksForTest() const { return m_lastPot; }

    /** 自检用：河区预留范围（**只用于布局预算，不绘制**；必须套住牌河、且不碰四家手牌）。 */
    QRectF riverAreaForTest() const { return m_layout.m_riverArea; }
    /** 自检用：风盘本身（应接近正方形）。 */
    QRectF centerRectForTest() const { return m_layout.m_centerRect; }

    /** 自检用：牌河/副露牌宽与手牌宽（前者不应被风盘挤小）。 */
    qreal riverWForTest() const { return m_layout.m_riverW; }
    qreal riverHForTest() const { return m_layout.m_riverH; }
    qreal handWForTest() const { return m_layout.m_tileW; }

    /**
     * 自检用：某家副露带里**每一副的左端**（局部 u，+u 朝向该家自己的右手侧）。
     * 约定：下标 0 = 最早的一副，且**最早的在最右**（u 最大），之后依次向左。
     */
    QVector<qreal> meldLeftsForTest(int pos) const;

    /** 自检用：某家副露带整块的右端（行末）。 */
    qreal meldRightForTest(int pos) const;

    /**
     * 自检用：某家「手牌 + 摸牌槽」整块（**不含副露**）的屏幕矩形。
     * 用来断言对家的手牌/摸牌不侵占宝牌指示牌栏。
     */
    QRectF handBlockScreenForTest(int pos) const;

    /**
     * 自检用：上一次出牌动画的起点是**手牌行的第几格**。
     *
     * <p>`-1` = 摸牌槽（摸切）或没有动画。上帝视角下别家的牌面已知，
     * 起点必须落在"这张牌真正待着的那一格"上 —— 这条断言就是钉它。
     */
    int lastFlightFromIndexForTest() const { return m_lastFlightFromIndex; }

private:
    /** 手牌区布局（局部坐标）：直接转发到 `m_layout`。 */
    HandLayout layoutHand(int pos) const;

    void onDiscarded(int seat, const QString& tile, bool tsumogiri, int riverIndex);
    void onCalled(int fromSeat, int riverIndex, int bySeat);
    void stepAnimation();

    TableModel* m_model = nullptr;
    TableLayout m_layout;               // 纯几何层：本帧的风盘 / 区带 / 牌河 / 手牌布局

    QVector<QRectF> m_handRects;        // 自己手牌的屏幕矩形（点击命中）
    QStringList m_handTiles;
    QStringList m_highlight;
    QString m_status;
    QString m_toast;
    QColor m_toastColor = QColor(0xFF, 0xD2, 0x4A);
    QTimer m_toastTimer;

    // 纯绘制的输出（不属于几何层）：上一帧立直棒画在哪几侧 / 盘中央供託几根。
    // 它们由 `paintCenter()` 写入、只给自检读，布局求解根本不看 —— 放在控件这边。
    QVector<int> m_lastStickPos;
    int m_lastPot = 0;
    QVector<QSizeF> m_scoreBoxSizes;    // 自检用：四家各自实际画的框尺寸

    QVector<Flight> m_flights;
    QTimer m_animTimer;
    bool m_seatDataValid = false;
    int m_lastFlightFromIndex = -1;     // 自检用：上次出牌动画的起点格（-1 = 摸牌槽）

    QRectF m_plateRects[4];             // 四家名牌的屏幕矩形（点击切视角用）
};

using TableWidget = TableView;
