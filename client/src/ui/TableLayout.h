#pragma once

// 牌桌**纯几何层**（AUDIT §3.2-1）：把布局求解从 TableView 里拆出来。
//
// 为什么要有这个文件：
//   * `ui/TableView.cpp` 原来 1200+ 行同时承担「布局求解 + 8 个绘制函数 + 飞行动画 + 点击命中」，
//     而布局恰恰是踩坑最多的一块（风盘尺寸反推、四边同构区带、手牌块避让、牌河行数不封顶）。
//     埋在绘制代码里的几何没法单独断言，只能靠 `grab()` 抓图看像素。
//   * 这里是一个**普通 struct**：不含 QWidget、不存 `TableModel*`、不绘制、不发信号。
//     所有输入都是显式参数（控件尺寸 + `const TableModel&` + 已测好的字体宽度），
//     因此可以脱离控件直接算、直接断言。
//
// 它**只算矩形与坐标**，一个像素都不画；`TableView` 负责按这里算出的带/框/槽位绘制。
//
// 方位约定（docs/PROTOCOL.md §4）：pos = (seat - mySeat + 4) % 4
//   pos 0 = 下（自己） / 1 = 右（下家） / 2 = 上（对家） / 3 = 左（上家）
//
// 局部坐标系：每家在自己的坐标系里摆放（局部 +u 指向该家自己的右手侧、+v 指向该家自己），
// 再由 `SeatFrame::toScreen` 整体旋转到屏幕 —— 侧家的牌自然横置、
// 「横置以牌主视角判定」自动成立。

#include "model/TableModel.h"   // Meld / const TableModel&

#include <QRectF>
#include <QSize>
#include <QSizeF>
#include <QTransform>
#include <QVector>

/**
 * 一家的坐标系与手牌/牌河的行位置（局部坐标 → 屏幕）。
 */
struct SeatFrame {
    QTransform toScreen;   // 局部 → 屏幕
    qreal uLen = 100.0;
    qreal vLen = 100.0;
    qreal handV0 = 0.0;    // 手牌行：v ∈ [handV0, handV1]
    qreal handV1 = 0.0;
    qreal riverStartV = 0.0;  // 牌河第 0 行靠近风盘的那条边的 v
    qreal riverStep = 0.0;
    QRectF plate;             // 名牌（屏幕坐标）
};

/**
 * 手牌区布局（局部坐标 u，+u 朝向该家的右手侧）。
 *
 * 关键点：副露钉在各家自己的右下角并从右往左排；「手牌 + 摸牌」是一个整体块，
 * 手牌在块内居左、摸牌有**独立槽位**；副露往左长时把整块一起往左顶 ——
 * 于是摸牌 / 打牌都不会让手牌位移（副露时是有意让整块左移）。
 */
struct HandLayout {
    qreal areaLeft = 0;    // 手牌区左边缘（固定锚点，永不移动）
    qreal handLeft = 0;    // 实际手牌左边缘（= areaLeft，居左）
    qreal drawnLeft = 0;   // 摸牌槽左边缘（独立间隔之后）
    qreal meldLeft = 0;    // 副露区左边缘（副露钉在右下角，向左生长）
    qreal meldRight = 0;   // 副露区右边缘（= 该家自己的右手侧行末）
    qreal meldBetween = 0; // 两副副露之间的间隔
    qreal hgap = 0;        // 手牌之间的间隔
    qreal drawGap = 0;     // 手牌与摸牌之间的独立间隔
    qreal meldGap = 0;     // 摸牌槽与副露之间的间隔
};

/**
 * 牌桌布局求解器。
 *
 * 生命周期：`computeLayout()` 每帧（`paintEvent` 开头）重算一次，之后所有绘制只读结果。
 * 输入全部来自参数，本类**不持有**模型指针 —— `const TableModel&` 只在调用期间被读。
 */
struct TableLayout {
    // ---- 输出：由 computeLayout() 填 ----
    SeatFrame m_frames[4];
    QRectF m_centerRect;          // 风盘本身（应接近正方形）
    QRectF m_doraRect;            // 风盘内：対面点数下方的宝牌指示牌行（最多 5 张）
    // 风盘四边**同构**的两条带（下标与座位方位同号：0=下自家 1=右下家 2=上対面 3=左上家）：
    //   立直棒带贴盘边、点数带在其内侧 —— 这样「点数到盘边的距离」四边完全一致，
    //   而每家的立直棒正好落在**各家自己那一侧**（不再统统堆在盘底）。
    QRectF m_stickBand[4];        // 风盘内：各家面前的立直棒带（在点数**外侧**）
    QRectF m_scoreBand[4];        // 风盘内：各家点数带
    QRectF m_centerBand;          // 风盘内：中间（场次/余牌/供託）区带（左右避开两条竖排点数）
    QSizeF m_scoreBoxSize;        // 四家统一的得点框尺寸（长 × 高）
    QRectF m_riverArea;           // 河区预留范围（风盘向外等距扩一圈；只算布局，不画）
    int m_riverRows = 3;          // 本帧为牌河预留的行数（>3 = 有家超过 18 张，牌河继续向外长）
    qreal m_plateReserve = 84.0;  // 角落为名牌/宝牌栏保留的宽度
    qreal m_plateW = 120.0;       // 名牌宽（横排边上的角落住户）
    qreal m_plateH = 24.0;        // 名牌高（竖排边上的角落住户）
    qreal m_tileW = 20.0;
    qreal m_tileH = 27.0;
    qreal m_riverW = 16.0;
    qreal m_riverH = 22.0;

    /**
     * 解一帧的布局。
     *
     * <p>尺寸来源必须显式传入，**不读控件也不读模型指针**：
     *   - `viewSize`：控件当前尺寸（`width()` / `height()`）；
     *   - `model`：**只读**，只用来问「四家弃牌数」（牌河预留行数）；可以是空模型；
     *   - `scoreTextW` / `scoreDigitsW`：**已经量好的**字体宽度 —— 四家里最长的分数文本宽度、
     *     三位数字的宽度。字号随盘宽变，所以量宽只能在调用处用 QFontMetricsF 做，
     *     这里只收结果值（本类不引 QWidget/QPainter，才能脱开控件单测）。
     */
    void computeLayout(QSize viewSize, const TableModel& model,
                       qreal scoreTextW, qreal scoreDigitsW);

    /** 手牌区布局（局部坐标）。需要模型：暗牌张数 / 副露 / 刚摸牌的是哪家。 */
    HandLayout layoutHand(int pos, const TableModel& model) const;

    /** 角落为名牌/宝牌栏保留的宽度（对外的下限 72，见 plateReserve）。 */
    qreal plateReserve() const;

    // ---- 供绘制与动画使用的坐标换算 ----

    /** 牌河第 index 张在**屏幕**上的矩形（飞行动画终点）。 */
    QRectF riverSlotScreen(int seat, int index, const TableModel& model) const;
    /**
     * 牌河第 index 张在**该家局部坐标系**里的矩形（+u 朝该家自己的右手侧）。
     *
     * <p>与 `riverSlotScreen` 是同一份几何（后者只是把它经 `SeatFrame::toScreen` 旋转到屏幕），
     * 抽出来是为了让「牌河固定左缘」这条不变量能**不看旋转**直接断言：
     * 第 0/6/12 张的局部左沿都必须等于 `riverLeftU()`。
     */
    QRectF riverSlotLocal(int seat, int index, const TableModel& model) const;
    /** 该家手牌行里第 index 张牌的**局部**矩形（飞行动画起点）。 */
    QRectF handSlotLocal(int pos, int index, bool drawn, const TableModel& model) const;

    // ---- 纯函数（原来散在 TableView.cpp 的匿名命名空间里）----

    /**
     * 一族牌河排几行：一行 6 列，**排满标称 3 行后不封顶**。
     *
     * ⚠ 曾经这里对行数取了 `qMin(3, …)`：18 张（3×6）排满之后，第 19 张及以后的牌
     * **整张不画**（报障：「第 18 张之后的牌河牌不显示」）。鸣牌多的局里一家真的会超过 18 张，
     * 所以行数必须继续长。布局按**同一函数**的最大值预留（见 `computeLayout` 的 `m_riverRows`），
     * 多出来的行才不会压到手牌。
     */
    static int riverRowsFor(int n);

    /**
     * 牌河**一行排满**时的横向宽度（一族 6 列，且允许最左那张是横置牌）。
     *
     * ⚠ 这是牌河**固定左缘**的唯一尺子：左缘 = `-riverFullRowExtent() / 2`，
     *   与「这一帧已经打出了几张」**完全无关**。用户要求的是「**先假定一行放满**，
     *   算出它的最最左沿在哪，再从这个地方开始向右放牌」—— 于是第一张就打在最左，
     *   之后只向右长：同一局的前后两手、以及同一家的每一行，左沿都不动。
     *   旧实现按「本帧已打出的最大列数」算，**牌河会随着张数变多整体往左挪**
     *   （第一张的位置自己在移动，看着就像"没左对齐"）。
     *   横置牌占的是**牌高**，一行最多 1 张，所以满行宽度 = 牌高 + 5×牌宽 + 5×列间距，
     *   与「横置的是第几列」无关（几张牌的总宽是同一个数）。
     */
    qreal riverFullRowExtent() const;

    /** 牌河固定左缘（局部 u，+u 朝向该家自己的右手侧）。 */
    qreal riverLeftU() const { return -riverFullRowExtent() / 2.0; }

    /**
     * 副露里哪一张横置。
     *
     * <p>规则：吃 → 被吃的那张；碰/大明杠 → 由**打牌者方位**决定
     * （上家=最左、对家=中间、下家=最右）；暗杠不横置；加杠横在第二张。
     * 本体是 `model/TableModel.h` 里的自由函数 `meldSidewaysIndex()` ——
     * 结算界面的牌面示意也要用同一条规则，所以那边共用一份，避免两处各写一份而漂移。
     */
    static int meldRotatedIndex(const Meld& m, int ownerSeat);

    /**
     * 一副副露占的横向宽度。
     *
     * ⚠ 横置那张占的是**牌高**（`tileH`），但**暗杠一张都不横置**（四张全竖，两端画牌背）——
     *   一律按「必有一张横置」估，会比实际多算 `tileH − tileW`（约 0.36 张牌宽），
     *   副露带右端就凭空多出一段空隙。
     *   这把尺子必须与绘制处（`TableView::paintSeat` 里 `rotIdx < 0` 的分支）**用同一条判据**，
     *   否则「预留范围与实际排布不一致」的老毛病会换个地方再犯一次。
     */
    static qreal meldWidthOf(const Meld& m, int ownerSeat, qreal tileW, qreal tileH, qreal hgap);

    /**
     * 副露带里**每一副的左端**（局部 u，+u 朝向该家自己的右手侧）。
     *
     * 顺序约定：`melds` 按**鸣牌先后**（下标 0 = 最早），摆放是
     * **最早的在最右（钉在行末），之后依次向左** —— 于是新的副露出现在左端，
     * **已有的副露不会移动**（组在行末右对齐，随副露增多向左生长）。
     *
     * ⚠ 这里曾经与注释相反：绘制时从 `meldLeft` 一路向右排，结果**最早的落在最左**，
     *   与「第 1 副在最右」的布局约定（`meldRight` 固定、`meldLeft = meldRight - groupW`）矛盾。
     *   抽成函数是为了让自检能直接断言这个顺序。
     */
    static QVector<qreal> meldLeftsOf(const QVector<Meld>& melds, int ownerSeat, qreal groupRight,
                                      qreal tileW, qreal tileH, qreal hgap, qreal between);
};
