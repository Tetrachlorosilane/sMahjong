#include "ui/TableView.h"

#include "i18n/Lang.h"
#include "model/TableModel.h"
#include "model/Tile.h"
#include "ui/TileRenderer.h"

#include <QFont>
#include <QFontMetricsF>
#include <QMouseEvent>
#include <QPainter>
#include <QRandomGenerator>

namespace {

// ⚠ 布局常量（kPad / kGap / kRiverCols / kRiverRowGap / kRiverColGap / kDoraScale…）
//   已经搬进 `ui/TableLayout.cpp` —— 那里是唯一的一份。绘制这边只留**绘制需要**的：
//   把两个间距各写一份，就是「预留范围与实际排布不一致」那类老毛病的起点。
constexpr int kRiverCols = 6;
constexpr int kMaxDoraIndicators = 5;   // 宝牌 1 张 + 四个杠各翻 1 张 = 一局最多 5 张
constexpr qreal kRiverColGap = 0.09;    // 与 TableLayout 的钳制/布槽同源（见上）
// 控件内边距：布局那边（TableLayout 的 kPad）也用它决定手牌行的起止；
// 这里只用在外圈装饰线与提示条上 —— 两处**必须是同一个数**，否则外框会与手牌行错位。
constexpr qreal kPad = 10.0;
constexpr int kAnimIntervalMs = 16;    // ≈60fps
constexpr int kAnimDurationMs = 360;   // 出牌动画时长（放慢，看得清手切/摸切）

// ---- 扁平配色（纯色，无渐变/阴影/发光）----
const QColor kTable      (0x11, 0x4B, 0x3B);
const QColor kTableRing  (0x0C, 0x3A, 0x2E);
const QColor kPanelBg    (0x0A, 0x28, 0x20, 232);
const QColor kPanelEdge  (0x2E, 0x6E, 0x59);
const QColor kAccent     (0xF2, 0xC1, 0x4B);
const QColor kAccentSoft (0x9F, 0xE8, 0xC4);
// 立直棒的「长 : 厚」。真实立直棒约 4:1；3.6 那版用户反馈「稍有一些短」。
// 各家自己面前的棒与盘中央的供託棒**共用**这个比例，否则两处会看起来不是一种东西。
constexpr qreal kStickRatio = 4.4;
const QColor kText       (0xEA, 0xF4, 0xEF);
const QColor kTextDim    (0x9C, 0xC2, 0xB3);
const QColor kWarn       (0xFF, 0x8A, 0x7A);

QFont uiFont(int pixelSize, bool bold = false)
{
    QFont f;
    f.setFamilies(QStringList { QStringLiteral("Microsoft YaHei"), QStringLiteral("SimHei"),
                                QStringLiteral("Noto Sans CJK SC"), QStringLiteral("DejaVu Sans") });
    f.setPixelSize(qMax(8, pixelSize));
    f.setBold(bold);
    return f;
}

void flatBox(QPainter& p, const QRectF& r, const QColor& fill, const QColor& edge,
             qreal radius = 6.0, qreal edgeWidth = 1.0)
{
    p.setPen(Qt::NoPen);
    p.setBrush(fill);
    p.drawRoundedRect(r, radius, radius);
    if (edgeWidth > 0) {
        p.setPen(QPen(edge, edgeWidth));
        p.setBrush(Qt::NoBrush);
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius);
    }
}

void drawStick(QPainter& p, const QRectF& r, const QColor& color)
{
    // 圆角与中心点都按**短边**算：立直棒横放（上下两家）与竖放（左右两家）看起来才一致。
    const qreal thick = qMin(r.width(), r.height());
    p.setPen(Qt::NoPen);
    p.setBrush(color);
    p.drawRoundedRect(r, thick * 0.45, thick * 0.45);
    p.setBrush(QColor(0x20, 0x20, 0x20, 200));
    p.drawEllipse(r.center(), thick * 0.15, thick * 0.15);
}

// 副露的**显示顺序**：把被鸣的那张挪到「来源方位」对应的格子上，
// 其余牌保持原有相对顺序填满剩下的格子。
// （吃的时候服务端下发的是升序数组，这里只调整摆放，不改数值内容。）
QStringList meldDisplayTiles(const Meld& m, int rotIdx)
{
    QStringList disp = m.tiles;
    if (rotIdx < 0 || m.kind != QLatin1String("chi"))
        return disp;
    const int cur = disp.indexOf(m.calledTile);
    if (cur >= 0 && cur != rotIdx)
        disp.move(cur, rotIdx);
    return disp;
}

} // namespace

TableView::TableView(QWidget* parent)
    : QWidget(parent)
{
    setMinimumSize(480, 360);
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    setAutoFillBackground(false);
    setMouseTracking(false);
    m_toastTimer.setSingleShot(true);
    m_toastTimer.setInterval(1600);
    connect(&m_toastTimer, &QTimer::timeout, this, [this]() {
        m_toast.clear();
        update();
    });
    m_animTimer.setInterval(kAnimIntervalMs);
    connect(&m_animTimer, &QTimer::timeout, this, &TableView::stepAnimation);
}

void TableView::setModel(TableModel* model)
{
    if (m_model)
        m_model->disconnect(this);
    m_model = model;
    if (m_model) {
        connect(m_model, &TableModel::changed, this, [this]() { update(); });
        connect(m_model, &TableModel::discarded, this, &TableView::onDiscarded);
        connect(m_model, &TableModel::calledFromRiver, this, &TableView::onCalled);
    }
    // 换了模型就等于布局缓存全部作废：`m_seatDataValid` 是"已经画过一帧、座位数据可用"的
    // 标志，不复位的话下一次 `onDiscarded` 会拿**上一个模型**的帧数据去算动画（AUDIT C-S2）。
    m_seatDataValid = false;
    update();
}

void TableView::setStatusText(const QString& text)
{
    m_status = text;
    update();
}

void TableView::showToast(const QString& text, const QColor& color)
{
    m_toast = text;
    m_toastColor = color;
    m_toastTimer.start();
    update();
}

void TableView::setHighlightTiles(const QStringList& tiles)
{
    m_highlight = tiles;
    update();
}

// ---------------------------------------------------------------------------
// 布局：**求解在 ui/TableLayout.cpp**，这里只做「量字体 + 转发」
// ---------------------------------------------------------------------------
//
// 为什么字体宽度要在控件这边量：字号跟着风盘宽度走（`scoreH` 由布局算出来），
// 而 QFontMetricsF 依赖 Qt 的字体引擎 —— 把它留在控件侧，纯几何层就能收一个**数值**，
// 从而彻底不依赖 QWidget/QPainter（这是「可脱开控件单测」的前提）。
void TableView::computeLayout()
{
    static const TableModel kNoModel;   // 没连上模型时也要能算（原来 `m_model` 为空只跳过弃牌数）
    const TableModel& model = m_model ? *m_model : kNoModel;

    // 得点框的字号必须与 paintCenter() 完全一致：两处都用「盘宽 × 0.105，钳到 [13,19]」。
    // 布局算出的 m_scoreBoxSize 就是绘制实际用的框长，所以两者不能各算各的。
    const qreal scoreBoxH = qBound(13.0, m_layout.m_centerRect.width() * 0.105, 19.0);
    const int scoreFontPx = int(qBound(9.0, scoreBoxH * 0.74, 15.0));
    const QFontMetricsF fm(uiFont(scoreFontPx, true));
    qreal scoreTextW = 0.0;
    if (m_model) {
        for (int pos = 0; pos < 4; ++pos) {
            const int seat = (m_model->mySeat() + pos) % 4;
            const QString text = QStringLiteral("%1 %2")
                                     .arg(m_model->seatWind(seat))
                                     .arg(m_model->score(seat));
            scoreTextW = qMax(scoreTextW, fm.horizontalAdvance(text));
        }
    }
    const qreal scoreDigitsW = fm.horizontalAdvance(QStringLiteral("000"));

    m_layout.computeLayout(size(), model, scoreTextW, scoreDigitsW);
}

void TableView::paintEvent(QPaintEvent* event)
{
    Q_UNUSED(event);
    QPainter p(this);
    p.setRenderHint(QPainter::Antialiasing, true);
    p.setRenderHint(QPainter::TextAntialiasing, true);

    // 布局必须先算好：背景之后的每一层（牌河/副露/风盘）都直接用布局结果。
    // 顺序写反的话，第一帧会用上一轮的尺寸（改风盘尺寸时会闪一帧错位）。
    computeLayout();
    paintBackground(p);
    m_seatDataValid = true;

    if (!m_model) {
        p.setPen(kText);
        p.setFont(uiFont(16));
        p.drawText(rect(), Qt::AlignCenter, lang::t("ui.table.not_connected"));
        return;
    }

    paintCenter(p);
    paintDoraPanel(p);
    for (int pos = 0; pos < 4; ++pos)
        paintSeat(p, pos);
    paintFlights(p);
    // 牌桌顶部那块「出牌信息框」（打出 / 立直 / 新宝牌 / 下一局 …）已按需求**隐藏**。
    // 通知链路整条都留着（MainWindow 仍在调 showToast()，paintToast() 也还在），
    // 要恢复只需把下面这行放回来 —— 所以这里注释掉而不是删掉。
    // paintToast(p);
}

void TableView::paintBackground(QPainter& p)
{
    p.fillRect(rect(), kTable);
    const QRectF ring = QRectF(rect()).adjusted(kPad * 0.55, kPad * 0.55, -kPad * 0.55, -kPad * 0.55);
    p.setPen(QPen(kTableRing, 2));
    p.setBrush(Qt::NoBrush);
    p.drawRoundedRect(ring, 10, 10);

    // 这里曾经画一圈浅白色「牌河区线框」（风盘向外等距扩一圈）。
    // 按需求已删除：牌河本身的排列已经够清楚，多这一圈只是噪音。
    // ⚠ 布局里那块**预留量**（m_layout.m_riverArea）不能跟着删 —— 牌河是真的占那块地方，
    //   预算不算它，风盘就会长到让四家的河压上手牌（自检仍有断言兜底）。
}

// 风盘：场次居中，四家点数贴四边（上下家竖排）
void TableView::paintCenter(QPainter& p)
{
    const QRectF c = m_layout.m_centerRect;
    flatBox(p, c, kPanelBg, kPanelEdge, 9, 1.0);

    const qreal S = c.width();
    // 与布局同一公式（TableLayout::computeLayout 的 scoreH）：框高由它定，
    // 框长 m_layout.m_scoreBoxSize 也是按这个字号量出来的 —— 不要在这里另算一套。
    const qreal scoreH = qBound(13.0, S * 0.105, 19.0);

    // ---- 先算「正中心场次块」的尺寸，上下家的竖排牌点要**按它剩下的空间**排，
    //      否则旋转后的文字长度（≈0.66S）会顶出面板外沿。 ----
    const QString rt = m_model->roundText();
    const int sp = rt.indexOf(QLatin1Char(' '));
    const QString line1 = sp > 0 ? rt.left(sp) : rt;
    const QString line2 = sp > 0 ? rt.mid(sp + 1) : QString();
    const qreal l1H = qBound(16.0, S * 0.135, 30.0);
    const qreal l2H = line2.isEmpty() ? 0.0 : qBound(12.0, S * 0.100, 22.0);
    const qreal infoH = qBound(11.0, S * 0.078, 15.0);
    const qreal total = l1H + l2H + infoH + 4.0;

    // ---- 四家点数 ----
    // **上家/下家（左右两家）竖排**，自家/対面（上下两家）横排。
    // 竖排是把画布旋转 90°，只占**一个牌高**的宽度 —— 面板宽度才收得下来，
    // 否则左右两家的横排文字是宽度的唯一瓶颈。
    // 四条带全部来自 TableLayout（四边同构：棒在外、点数在内），
    // 绘制处只按带中心画 —— 不再现算 pad，点数就不会漂到棒上。
    //
    // **四家的得点框尺寸必须完全一样**（用户要求）：上面两条横排带横跨整个盘内宽度、
    // 左右两条是细长竖排带，若按各自的带长画框，四个框会长短差一倍。
    // 所以框长统一取「四家里最长的那个分数文本 + 内边距 + 三位数字的宽度」，
    // 四边都用它（用户要求再留出三位数字的余量，点数涨到十几万也不会挤字）——
    // 这个长度已经由布局算好（`m_layout.m_scoreBoxSize`），它量字体、这里只管画。
    const int scoreFontPx = int(qBound(9.0, scoreH * 0.74, 15.0));
    const qreal scoreBoxLen = m_layout.m_scoreBoxSize.width();
    m_scoreBoxSizes.clear();

    const auto drawSeat = [&](int pos, const QPointF& ctr, qreal deg) {
        const int seat = (m_model->mySeat() + pos) % 4;
        const bool active = (m_model->turn() == seat);
        const QString text = QStringLiteral("%1 %2")
                                 .arg(m_model->seatWind(seat))
                                 .arg(m_model->score(seat));
        p.save();
        // 字号必须在 lambda 内设：否则会沿用调用前遗留的字体，
        // 文字比槽位大 → AlignCenter 会**对称溢出**（曾经因此让竖排点数顶出面板）。
        p.setFont(uiFont(scoreFontPx, true));
        p.translate(ctr);
        if (deg != 0.0) {
            p.rotate(deg);
        }
        // 四家同一个尺寸的框：面朝当前行动家的那个用高亮色，其余是低调的底+淡边
        const QRectF r(-scoreBoxLen / 2.0, -scoreH / 2.0, scoreBoxLen, scoreH);
        flatBox(p, r, active ? QColor(0x1E, 0x5A, 0x38) : QColor(0x11, 0x33, 0x2A, 170),
                active ? kAccent : QColor(0x2E, 0x6E, 0x59, 150), 4, active ? 1.4 : 1.0);
        p.setPen(active ? kAccent : kText);
        p.drawText(r, Qt::AlignCenter, text);
        p.restore();
        m_scoreBoxSizes.append(QSizeF(scoreBoxLen, scoreH));
    };

    // 角度按「**文字读向要正对该家**」定，与牌桌上「横置以牌主视角判定」同一原则：
    //   自家 —— 与屏幕同向，0°
    //   対面 —— 180°（他的视角相对本家转了半圈）
    //   上家（左）—— 90°
    //   下家（右）—— 270°（= 90° + 180°）
    // 框长四边统一（见 scoreBoxLen），所以四个框**一样大**，只是朝向不同。
    drawSeat(2, m_layout.m_scoreBand[2].center(), 180.0);    // 対面：上
    drawSeat(0, m_layout.m_scoreBand[0].center(), 0.0);      // 自家：下
    drawSeat(3, m_layout.m_scoreBand[3].center(), 90.0);     // 上家：左
    drawSeat(1, m_layout.m_scoreBand[1].center(), 270.0);    // 下家：右

    // ---- 正中心：场次信息（拆两行居中，比一行横排更省宽度）----
    // 垂直居中于**中间区带**（左右已避开两条竖排点数带），文字块比区带矮时居中、高时对称溢出。
    const QRectF mb = m_layout.m_centerBand;
    qreal ty = mb.center().y() - total / 2.0;
    const QRectF cb(mb.left(), ty, mb.width(), l1H);
    p.setPen(kText);
    p.setFont(uiFont(int(qBound(12.0, l1H * 0.62, 24.0)), true));
    p.drawText(cb, Qt::AlignCenter, line1);
    ty += l1H;

    if (!line2.isEmpty()) {
        p.setFont(uiFont(int(qBound(10.0, l2H * 0.62, 18.0)), true));
        p.drawText(QRectF(cb.left(), ty, cb.width(), l2H), Qt::AlignCenter, line2);
        ty += l2H;
    }

    p.setFont(uiFont(int(qBound(8.0, infoH * 0.66, 12.0))));
    p.setPen(kTextDim);
    p.drawText(QRectF(cb.left(), ty, cb.width(), infoH), Qt::AlignCenter,
               lang::t("ui.table.tiles_left_offering")
                   .arg(m_model->tilesLeft())
                   .arg(m_model->riichiSticks()));
    ty += infoH;

    // ---- 立直棒 ----
    // **各家自己的棒放在各家面前**（报障：原来把四家的棒统统堆在盘底，看着像都是自家的）。
    // 每家的带都在自己那一侧、且在点数**外侧**（带由布局算好），所以按座位取带即可。
    // 左右两家的棒**竖着**放（正对他们，与那两家竖排点数的朝向一致）。
    m_lastStickPos.clear();
    for (int pos = 0; pos < 4; ++pos) {
        const int seat = (m_model->mySeat() + pos) % 4;
        if (pos >= 4 || !m_model->riichi(seat))
            continue;
        const QRectF b = m_layout.m_stickBand[pos];
        const qreal thick = qMin(b.width(), b.height());
        const qreal longSide = thick * kStickRatio;
        const bool vertical = (pos % 2) != 0;
        const QRectF sr = vertical
                ? QRectF(b.center().x() - thick / 2.0, b.center().y() - longSide / 2.0,
                         thick, longSide)
                : QRectF(b.center().x() - longSide / 2.0, b.center().y() - thick / 2.0,
                         longSide, thick);
        drawStick(p, sr, QColor(0xF2, 0xF2, 0xF2));
        m_lastStickPos.append(pos);
    }
    // 多出来的 = 上一局留下来、**不属于任何一家**的供託（客户端拿不到"上一局是谁立的直"）。
    // 摆在盘中央「供託 N」那行下面，别塞给某一家。
    m_lastPot = qMax(0, m_model->riichiSticks() - m_lastStickPos.size());
    const qreal potTh = qBound(5.0, mb.height() * 0.07, 9.0);
    // 只在「供託 N」那行**下面真放得下**时才画（小窗口里宁可只留数字，也不压到场次文字）
    if (m_lastPot > 0 && ty + 6.0 + potTh <= mb.bottom()) {
        const qreal sw = potTh * kStickRatio;
        // 根数还要受**中间列的宽度**约束：一排供託棒横着摆，放不下就少画几根
        // （原来硬写 8 根，6 根以上就会伸出中间列、压到左右两家的点数带上）。
        const int fit = qMax(1, int((mb.width() + 3.0) / (sw + 3.0)));
        const int n = qMin(m_lastPot, qMin(8, fit));
        const qreal totalW = n * sw + (n - 1) * 3.0;
        qreal sx = mb.center().x() - totalW / 2.0;
        const qreal sy = ty + 6.0;
        for (int i = 0; i < n; ++i) {
            drawStick(p, QRectF(sx, sy, sw, potTh), QColor(0xF2, 0xF2, 0xF2));
            sx += sw + 3.0;
        }
    }
}

// 宝牌指示牌：画在**风盘内**、対面点数下方那一行（见 AGENTS §6「风盘尺寸由内容反推」）
void TableView::paintDoraPanel(QPainter& p)
{
    const QStringList dora = m_model->doraIndicators();
    if (dora.isEmpty())
        return;
    const QRectF r = m_layout.m_doraRect;
    // 不再自己画底板：这一行住在风盘里，风盘已经有底和边。
    // 也**不再画「宝牌」字样**（用户要求去掉）：它占掉左边一截宽度，
    // 让 5 张指示牌整行伸出「四家分数框」围出的中间列 —— 现在整行都收在那一列里。

    // 牌宽取「高度 / 1.36」（＝牌的形状），再用**可用宽度**夹一次；
    // 被夹时**宽度和高度一起缩**（下面 th 跟着 tw 走），绝不只压宽度 ——
    // 只压宽度会让牌变成"瘦长条"，已经不是牌的形状了（用户实测现象）。
    const int shown = qMin(int(dora.size()), kMaxDoraIndicators);
    const qreal gaps = qMax(0, shown - 1) * 3.0;
    const qreal roomForTiles = qMax(4.0, r.width() - 4.0);
    qreal tw = qMin(r.height() / 1.36, (roomForTiles - gaps) / qMax(1, shown));
    tw = qMax(4.0, tw);
    const qreal th = tw * 1.36;
    const qreal ty = r.top() + (r.height() - th) / 2.0;   // 行内垂直居中
    // 整行在中间列里**居中**（去掉字样后没有"左侧标签"要避让了）
    const qreal rowW = shown * tw + gaps;
    qreal x = r.center().x() - rowW / 2.0;
    for (int i = 0; i < shown; ++i) {
        TileRenderer::drawSmall(p, QRectF(x, ty, tw, th).toRect(), dora.at(i));
        x += tw + 3.0;
    }
}

QRectF TableView::riverSlotScreen(int seat, int index) const
{
    if (!m_model)
        return QRectF();
    return m_layout.riverSlotScreen(seat, index, *m_model);
}

HandLayout TableView::layoutHand(int pos) const
{
    return m_layout.layoutHand(pos, *m_model);
}

qreal TableView::plateReserve() const
{
    return m_layout.plateReserve();
}

int TableView::riverRowCountForTest(int tiles)
{
    // 与绘制/布局**同一份**逻辑（见 ui/TableLayout.cpp 的 riverRowsFor）
    return TableLayout::riverRowsFor(tiles);
}

QVector<qreal> TableView::meldLeftsForTest(int pos) const
{
    if (!m_model || pos < 0 || pos > 3 || m_layout.m_tileW <= 0.0)
        return QVector<qreal>();
    const HandLayout L = layoutHand(pos);
    const int seat = (m_model->mySeat() + pos) % 4;
    return TableLayout::meldLeftsOf(m_model->melds(seat), seat, L.meldRight, m_layout.m_riverW,
                                    m_layout.m_riverH, L.hgap, L.meldBetween);
}

qreal TableView::meldRightForTest(int pos) const
{
    if (!m_model || pos < 0 || pos > 3 || m_layout.m_tileW <= 0.0)
        return 0.0;
    return layoutHand(pos).meldRight;
}

QRectF TableView::handBlockScreenForTest(int pos) const
{
    if (!m_model || pos < 0 || pos > 3 || m_layout.m_tileW <= 0.0)
        return QRectF();
    const SeatFrame& f = m_layout.m_frames[pos];
    const HandLayout L = layoutHand(pos);
    // 块 = [handLeft, drawnLeft + 一张牌]：摸牌槽**即使当前没牌也照样占位**
    // （手牌不抖的前提），所以这条断言在「对家刚摸牌」和「没摸牌」时同样成立。
    const qreal right = L.drawnLeft + m_layout.m_tileW;
    return f.toScreen.mapRect(QRectF(L.handLeft, f.handV0, right - L.handLeft, m_layout.m_tileH));
}

int TableView::meldRotatedIndexForTest(const Meld& m, int ownerSeat)
{
    return TableLayout::meldRotatedIndex(m, ownerSeat);
}

void TableView::onDiscarded(int seat, const QString& tile, bool tsumogiri, int riverIndex)
{
    if (!m_model || !m_seatDataValid || tile.isEmpty() || riverIndex < 0)
        return;
    computeLayout();

    const int pos = ((seat - m_model->mySeat()) % 4 + 4) % 4;
    const SeatFrame& f = m_layout.m_frames[pos];
    const QRectF toScreen = riverSlotScreen(seat, riverIndex);
    if (!toScreen.isValid())
        return;
    const QRectF toLocal = f.toScreen.inverted().mapRect(toScreen);

    QRectF fromLocal;
    int fromIndex = -1;          // 起点格（-1 = 摸牌槽）；自检断言用它
    bool exact = false;          // 起点是不是"按真实手牌"定的（false = 未知手牌的兜底）
    // ⚠ 实时对局里**别家的手牌是不可见的**，所以"手切"只能随便挑一格起飞 —— 那是**故意**的：
    //   若按真实位置起飞，看久了就能反推出手牌顺序（泄露信息）。
    //   回放没有这个顾虑（记录里本来就有四家暗牌），而且要求**动画与手牌位置一致**，
    //   所以只要模型里有该家的真实手牌，就一律从"这张牌真正待着的那一格"起飞。
    const bool known = (pos != 0) ? m_model->hasGodHand(seat) : !m_handTiles.isEmpty();
    if (tsumogiri && (pos == 0 || m_model->hasGodHand(seat))) {
        // 摸切：起点固定为「摸牌槽」。绝不能用上一帧缓存的 last()：
        // draw 与 discard 常在同一批 TCP 里到达、中间没有重绘，
        // 那时的缓存还是「没摸牌」的一帧，会把摸切演成手切。
        fromLocal = m_layout.handSlotLocal(pos, 0, true, *m_model);
        exact = true;
    } else if (known) {
        // 这张牌待在第几格：自家用**上一帧实际画出来的**手牌串（那就是屏幕上的顺序），
        // 别家用模型里的真实暗牌（`setGodHand()` 已理牌，与自家同一把尺子）。
        const QStringList h = (pos == 0) ? m_handTiles : m_model->godHand(seat);
        QVector<int> cand;
        const int last = (pos == 0) ? h.size() - 1 : h.size();   // 自家末位是摸牌槽，先排除
        for (int i = 0; i < last; ++i) {
            if (h.at(i) == tile)
                cand.append(i);
        }
        if (cand.isEmpty()) {
            for (int i = 0; i < h.size(); ++i) {
                if (h.at(i) == tile)
                    cand.append(i);
            }
        }
        // ⚠ 起点**按当前布局现算**，不要用上一帧缓存的手牌矩形：`draw` 与 `discard`
        // 可能同一批到达（中间没有重绘），那时缓存的手牌矩形还是"没摸牌 / 没打牌"那一版，
        // 动画会从一个已经不存在的槽位起飞（AUDIT C-S3）。用上一帧的牌面串只为了**挑哪一格**，
        // 坐标一律来自当前布局。
        // 同码有多张时取**第一张**（不再随机）：那几张长得一模一样，定死才可复现。
        if (!cand.isEmpty()) {
            fromIndex = cand.first();
            fromLocal = m_layout.handSlotLocal(pos, fromIndex, false, *m_model);
            exact = true;
        }
    }
    if (!fromLocal.isValid()) {
        const int concealed = qMax(1, m_model->concealedCount(seat));
        const int idx = tsumogiri ? concealed - 1
                                  : int(QRandomGenerator::global()->bounded(qMax(1, concealed)));
        fromLocal = m_layout.handSlotLocal(pos, idx, tsumogiri, *m_model);
        fromIndex = tsumogiri ? -1 : idx;
        exact = false;
    }
    m_lastFlightFromIndex = fromIndex;
    m_lastFlightExact = exact;

    Flight fl;
    fl.tile = tile;
    fl.red = TileRenderer::isRed(tile);
    fl.from = fromLocal;
    fl.to = toLocal;
    fl.t = 0.0;
    fl.pos = pos;
    fl.seat = seat;
    fl.riverIndex = riverIndex;
    fl.sideways = m_model->discardSideways(seat, riverIndex);
    m_flights.append(fl);
    if (!m_animTimer.isActive())
        m_animTimer.start();
    update();
}

void TableView::onCalled(int fromSeat, int riverIndex, int bySeat)
{
    // 被鸣的那张已由模型从牌河移除（不是复制一张），副露里会正常绘制；这里只需刷新
    Q_UNUSED(fromSeat);
    Q_UNUSED(riverIndex);
    Q_UNUSED(bySeat);
    update();
}

void TableView::stepAnimation()
{
    const qreal step = qreal(kAnimIntervalMs) / qreal(kAnimDurationMs);
    for (Flight& f : m_flights)
        f.t += step;
    while (!m_flights.isEmpty() && m_flights.first().t >= 1.0)
        m_flights.removeFirst();
    if (m_flights.isEmpty())
        m_animTimer.stop();
    update();
}

void TableView::paintFlights(QPainter& p)
{
    for (const Flight& fl : m_flights) {
        const qreal t = qBound(0.0, fl.t, 1.0);
        const qreal e = 1.0 - (1.0 - t) * (1.0 - t);   // ease-out
        const QRectF r(fl.from.left() + (fl.to.left() - fl.from.left()) * e,
                       fl.from.top() + (fl.to.top() - fl.from.top()) * e,
                       fl.from.width() + (fl.to.width() - fl.from.width()) * e,
                       fl.from.height() + (fl.to.height() - fl.from.height()) * e);
        p.save();
        p.setTransform(m_layout.m_frames[fl.pos].toScreen, true);
        TileRenderer::drawFaceF(p, r, fl.tile, fl.red, true);
        p.restore();
    }
}

void TableView::paintRiver(QPainter& p, const SeatFrame& f, int seat, const QStringList& tiles,
                           const QVector<bool>& sideways, int skipIndex)
{
    Q_UNUSED(seat);
    const int n = tiles.size();
    if (n <= 0)
        return;
    const qreal cgap = qMax(1.0, m_layout.m_riverW * kRiverColGap);
    const qreal rgap = f.riverStep - m_layout.m_riverH;
    // **不封顶**：3 行排满（18 张）之后继续向外排第 4 行、第 5 行……
    // 旧代码这里是 qMin(kMaxRiverRows, …)，于是第 19 张及以后的牌整张消失（报障）。
    // 布局按**同一函数**预留行数（m_layout.m_riverRows），两者必须同源。
    const int rows = TableLayout::riverRowsFor(n);

    for (int r = 0; r < rows; ++r) {
        QVector<int> idx;
        qreal rowW = 0;
        for (int c = 0; c < kRiverCols; ++c) {
            const int i = r * kRiverCols + c;
            if (i >= n)
                break;
            const bool side = (i < sideways.size()) && sideways.at(i);
            idx.append(i);
            rowW += (side ? m_layout.m_riverH : m_layout.m_riverW) + cgap;
        }
        if (idx.isEmpty())
            continue;
        rowW -= cgap;

        const qreal v = f.riverStartV + r * (m_layout.m_riverH + rgap);
        qreal u = -rowW / 2.0;
        for (int i : idx) {
            const bool side = (i < sideways.size()) && sideways.at(i);
            const qreal w = side ? m_layout.m_riverH : m_layout.m_riverW;
            const qreal h = side ? m_layout.m_riverW : m_layout.m_riverH;
            if (i != skipIndex) {
                const QString& t = tiles.at(i);
                if (side) {
                    // 横置：在牌主视角里多转 90°，故本家视角下邻家的横置牌是竖的
                    const QRectF slot(u, v + (m_layout.m_riverH - m_layout.m_riverW) / 2.0, w, h);
                    TileRenderer::drawFaceRot(p, slot, t, TileRenderer::isRed(t), 1);
                } else {
                    TileRenderer::drawFaceF(p, QRectF(u, v, w, h), t, TileRenderer::isRed(t), true);
                }
            }
            u += w + cgap;
        }
    }
}

void TableView::paintNamePlate(QPainter& p, const QRectF& r, int seat, bool active)
{
    // 半透明底（能透出桌面）；不显示得点——得点放在风盘内
    const QColor bg = active ? QColor(0x1E, 0x5A, 0x38, 150) : QColor(0x0A, 0x28, 0x20, 118);
    const QColor edge = active ? QColor(kAccent.red(), kAccent.green(), kAccent.blue(), 190)
                               : QColor(0x2E, 0x6E, 0x59, 115);
    flatBox(p, r, bg, edge, 6, active ? 1.4 : 1.0);

    const qreal fs = qBound(9.0, r.height() * 0.50, 14.0);
    p.setFont(uiFont(int(fs), active));
    p.setPen(active ? kAccent : QColor(0xEA, 0xF4, 0xEF, 215));
    QString text = m_model->playerName(seat);
    if (m_model->riichi(seat))
        text += lang::t("ui.table.riichi_suffix");
    p.drawText(r.adjusted(7, 0, -7, 0), Qt::AlignVCenter | Qt::AlignLeft, text);

    const qreal bw = r.height();
    const QRectF badge(r.right() - 4 - bw, r.top() + 2, bw - 2, r.height() - 4);
    flatBox(p, badge, QColor(0x16, 0x3E, 0x33, 165), QColor(0x2E, 0x6E, 0x59, 160), 4, 1.0);
    p.setFont(uiFont(int(fs * 1.02), true));
    p.setPen(QColor(0xEA, 0xF4, 0xEF, 232));
    p.drawText(badge, Qt::AlignCenter, m_model->seatWind(seat));
}

void TableView::paintSeat(QPainter& p, int pos)
{
    const SeatFrame& f = m_layout.m_frames[pos];
    const int seat = (m_model->mySeat() + pos) % 4;
    const bool isSelf = (pos == 0);
    const bool active = (m_model->turn() == seat);

    p.save();
    p.setTransform(f.toScreen, true);

    // 不给手牌区加任何外框/底衬（自家手牌区不框住）

    QStringList handTiles;
    bool hasDrawn = false;
    // 回放：四家暗牌始终在模型里（`setGodHands()` 由回放窗口推），这里只决定**画不画牌面**。
    //   · 自家永远是牌面；
    //   · 别家：开了「显示他家手牌」画牌面，否则画牌背 —— 但**张数仍按真实暗牌**算
    //     （回放知道真实张数，没理由再退回"13 − 3×副露"的估算）。
    const bool godData = (pos != 0) && m_model->hasGodHand(seat);
    const bool godFace = godData && m_godVisible;
    if (isSelf) {
        handTiles = m_model->hand();
        hasDrawn = !m_model->drawnTile().isEmpty();
    } else if (godFace) {
        handTiles = m_model->godHand(seat);      // 已由模型理牌
        hasDrawn = !m_model->godDrawn(seat).isEmpty();
    } else if (godData) {
        int cnt = m_model->concealedCount(seat); // 含摸牌
        hasDrawn = !m_model->godDrawn(seat).isEmpty();
        if (hasDrawn && cnt > 0) {
            cnt -= 1;
        }
        for (int i = 0; i < cnt; ++i)
            handTiles << QString();
    } else {
        // 实时对局：别家同样遵循「手牌 + 单独一格摸牌」的摆放规则。
        // concealedCount 含那张刚摸到的牌，这里把它拆出来单独画一个牌背。
        int cnt = m_model->concealedCount(seat);
        hasDrawn = (seat == m_model->mySeat() ? !m_model->drawnTile().isEmpty()
                                              : m_model->drawnSeat() == seat) && cnt > 0;
        if (hasDrawn) {
            cnt -= 1;
        }
        for (int i = 0; i < cnt; ++i)
            handTiles << QString();
    }
    const QVector<Meld> melds = m_model->melds(seat);
    int meldTiles = 0;
    for (const Meld& m : melds)
        meldTiles += m.tiles.size();

    const HandLayout L = layoutHand(pos);
    const qreal hgap = L.hgap;
    qreal x = L.handLeft;
    const qreal y = f.handV0;

    if (isSelf) {
        m_handRects.clear();
        m_handTiles.clear();
    }

    for (int i = 0; i < handTiles.size(); ++i) {
        const QRectF tr(x, y, m_layout.m_tileW, m_layout.m_tileH);
        if (isSelf || godFace) {
            const QString& t = handTiles.at(i);
            if (isSelf && m_highlight.contains(t)) {
                flatBox(p, tr.adjusted(-1.5, -1.5, 1.5, 1.5), QColor(0xF2, 0xC1, 0x4B, 70), kAccent,
                        5, 2.0);
            }
            TileRenderer::drawFaceF(p, tr, t, TileRenderer::isRed(t));
            if (isSelf) {
                m_handRects.append(f.toScreen.mapRect(tr));
                m_handTiles.append(t);
            }
        } else {
            TileRenderer::drawBackF(p, tr);
        }
        x += m_layout.m_tileW + hgap;
    }

    if (hasDrawn) {
        const QRectF tr(L.drawnLeft, y, m_layout.m_tileW, m_layout.m_tileH);
        flatBox(p, tr.adjusted(-2, -2, 2, 2), QColor(0x9F, 0xE8, 0xC4, 40), kAccentSoft, 5, 1.5);
        if (isSelf) {
            const QString t = m_model->drawnTile();
            TileRenderer::drawFaceF(p, tr, t, TileRenderer::isRed(t));
            m_handRects.append(f.toScreen.mapRect(tr));
            m_handTiles.append(t);
        } else if (godFace) {
            const QString t = m_model->godDrawn(seat);
            TileRenderer::drawFaceF(p, tr, t, TileRenderer::isRed(t));
        } else {
            // 别家的摸牌只画牌背，但同样占独立的一格（与自家规则一致）
            TileRenderer::drawBackF(p, tr);
        }
    }

    // ---- 副露：钉在右下角（行末），**第 1 副（最早）在最右**，之后依次向左；
    //      被鸣的那张按牌主视角横置。新增副露出现在左端，已有的副露不动。----
    if (!melds.isEmpty()) {
        const QVector<qreal> lefts = TableLayout::meldLeftsOf(melds, seat, L.meldRight,
                                                              m_layout.m_riverW, m_layout.m_riverH,
                                                              hgap, L.meldBetween);
        const qreal my = y + (m_layout.m_tileH - m_layout.m_riverH) / 2.0;   // 与手牌行垂直居中对齐
        for (int mi = 0; mi < melds.size(); ++mi) {
            const Meld& m = melds.at(mi);
            qreal mx = lefts.at(mi);           // 本副露内部仍是从左到右
            const int rotIdx = TableLayout::meldRotatedIndex(m, seat);
            // 显示顺序：被鸣的那张按来源方位落位（吃时可能与点数顺序不同）
            const QStringList disp = meldDisplayTiles(m, rotIdx);
            const int cnt = disp.size();
            for (int ti = 0; ti < cnt; ++ti) {
                const QString& t = disp.at(ti);
                const bool edge = m.isConcealed() && (ti == 0 || ti == cnt - 1);
                if (ti == rotIdx) {
                    const QRectF slot(mx, my + (m_layout.m_riverH - m_layout.m_riverW) / 2.0,
                                      m_layout.m_riverH, m_layout.m_riverW);
                    if (edge)
                        TileRenderer::drawBackRot(p, slot, 1);
                    else
                        TileRenderer::drawFaceRot(p, slot, t, TileRenderer::isRed(t), 1);
                    mx += m_layout.m_riverH + hgap;
                } else {
                    const QRectF tr(mx, my, m_layout.m_riverW, m_layout.m_riverH);
                    if (edge)
                        TileRenderer::drawBackF(p, tr);
                    else
                        TileRenderer::drawFaceF(p, tr, t, TileRenderer::isRed(t));
                    mx += m_layout.m_riverW + hgap;
                }
            }
        }
    }

    // ---- 牌河（跳过正在飞的那张，避免"同时出现在手牌与牌河"）----
    const QStringList riverTiles = m_model->discards(seat);
    QVector<bool> riverSide;
    riverSide.reserve(riverTiles.size());
    for (int i = 0; i < riverTiles.size(); ++i)
        riverSide.append(m_model->discardSideways(seat, i));

    int skip = -1;
    for (const Flight& fl : m_flights) {
        if (fl.seat == seat && fl.t < 1.0)
            skip = fl.riverIndex;
    }
    paintRiver(p, f, seat, riverTiles, riverSide, skip);

    p.restore();
    paintNamePlate(p, f.plate, seat, active);
    m_plateRects[pos] = f.toScreen.mapRect(f.plate);
}

void TableView::setGodHands(const QVector<QStringList>& hands, const QStringList& drawn)
{
    if (!m_model)
        return;
    if (hands.size() != 4) {
        clearGodHands();
        return;
    }
    for (int s = 0; s < 4; ++s) {
        m_model->setGodHand(s, hands.at(s), s < drawn.size() ? drawn.at(s) : QString());
    }
    update();
}

void TableView::clearGodHands()
{
    if (m_model)
        m_model->clearGodHands();
    update();
}

void TableView::setGodVisible(bool on)
{
    m_godVisible = on;
    update();
}

/**
 * 牌桌顶部居中那块「提示条」。
 *
 * <p>⚠ **当前不绘制**（`paintEvent` 里的调用已按需求注掉）：牌桌顶部的出牌信息框太显眼，
 * 会压住上家牌河一带。函数与 {@code showToast()} 都保留着，恢复只需把那一行的注释去掉。
 */
void TableView::paintToast(QPainter& p)
{
    if (m_toast.isEmpty())
        return;
    // 提示条不参与布局，用的仍是与 TableLayout 同一个 kPad。
    const qreal w = qMin(width() * 0.46, 420.0);
    const qreal h = qBound(30.0, height() * 0.045, 44.0);
    const QRectF r((width() - w) / 2.0, kPad + 2, w, h);
    flatBox(p, r, QColor(0x0A, 0x28, 0x20, 235), m_toastColor, 8, 2.0);
    p.setPen(m_toastColor);
    p.setFont(uiFont(int(qBound(14.0, h * 0.46, 20.0)), true));
    p.drawText(r, Qt::AlignCenter, m_toast);
}

void TableView::mousePressEvent(QMouseEvent* event)
{
    if (event->button() != Qt::LeftButton || !m_model) {
        QWidget::mousePressEvent(event);
        return;
    }
    const QPointF pos = event->position();
    for (int i = 0; i < m_handRects.size() && i < m_handTiles.size(); ++i) {
        if (m_handRects.at(i).contains(pos)) {
            emit tileClicked(m_handTiles.at(i), i);
            return;
        }
    }
    // 点名牌 = 切到那家的视角（回放用；实时对局里没人接这个信号，等同无效点击）
    for (int s = 0; s < 4; ++s) {
        if (!m_plateRects[s].isEmpty() && m_plateRects[s].contains(pos)) {
            emit seatClicked(s);
            return;
        }
    }
    QWidget::mousePressEvent(event);
}
