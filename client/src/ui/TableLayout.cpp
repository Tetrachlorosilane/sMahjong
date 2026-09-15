#include "ui/TableLayout.h"

// 牌桌几何求解（AUDIT §3.2-1）。
//
// 本文件**只有算术**：没有 QWidget / QPainter / 信号，唯一的 Qt 依赖是 QRectF/QTransform。
// 这样做是为了让这块最容易踩坑的几何能离开控件单独断言（以前只能靠 `grab()` 数像素）。
//
// ⚠ 这里的每一条公式都是**行为契约**，不是"可以顺手优化"的实现细节：
//   系数、钳制顺序、qMax/qMin 的嵌套、以及哪一步用到哪个中间量，改动任何一处都会
//   让 401 条自检断言（四家点数等距 / 得点框一样大且居中 / 牌河不压手牌 / 宝牌行只占中间列…）
//   里的某几条翻红。它们全是历史报障换来的，注释里逐条写了"为什么"。

#include <QtGlobal>

namespace {

constexpr qreal kPad = 10.0;
constexpr qreal kGap = 6.0;
constexpr int kRiverCols = 6;
constexpr int kMaxRiverRows = 3;   // 标称 3 行；**超过就继续向外长**（见 riverRowsFor）
constexpr int kMaxDoraIndicators = 5;   // 宝牌 1 张 + 四个杠各翻 1 张 = 一局最多 5 张
// 牌河一行内的列间距 / 行与行之间的间距（相对牌河牌宽/高）。
// 调小一点：牌河更紧凑，同样的空间能放下更大的牌（用户要求）。
constexpr qreal kRiverColGap = 0.09;
constexpr qreal kRiverRowGap = 0.08;
constexpr qreal kSideReserve = 44.0;   // 左右两家竖排手牌两端留给名牌的余量
// 满手张数。手牌区锚点按它算，与当前实际张数无关 —— 这是手牌不抖的根据。
constexpr int kHandSlots = 13;

/**
 * 该座位是否有「刚摸到、尚未打出」的牌。
 * 四家**一视同仁**：自家能看到牌面，别家只看到牌背，但摆放规则相同。
 */
bool seatHasDrawnTile(const TableModel& m, int seat)
{
    if (seat == m.mySeat())
        return !m.drawnTile().isEmpty();
    // 回放开了「显示他家手牌」时，别家的摸牌格也来自真实数据
    // （实时对局下 `drawnSeat()` 才是唯一依据 —— 客户端看不到别家的牌，只看得到谁摸了牌）
    if (m.hasGodHand(seat))
        return !m.godDrawn(seat).isEmpty();
    return m.drawnSeat() == seat;
}

} // namespace

int TableLayout::riverRowsFor(int n)
{
    return n <= 0 ? 0 : (n + kRiverCols - 1) / kRiverCols;
}

int TableLayout::meldRotatedIndex(const Meld& m, int ownerSeat)
{
    // 规则本体见 meldSidewaysIndex()（model/TableModel.h 里的自由函数）——
    // 结算界面的牌面示意也要用同一条规则，所以抽出去共用，避免两处各写一份而漂移。
    return meldSidewaysIndex(m, ownerSeat);
}

qreal TableLayout::meldWidthOf(const Meld& m, int ownerSeat, qreal tileW, qreal tileH, qreal hgap)
{
    const int cnt = m.tiles.size();
    if (cnt <= 0)
        return 0.0;
    const qreal sidewaysW = (meldRotatedIndex(m, ownerSeat) >= 0) ? tileH : tileW;
    return (cnt - 1) * tileW + sidewaysW + (cnt - 1) * hgap;
}

QVector<qreal> TableLayout::meldLeftsOf(const QVector<Meld>& melds, int ownerSeat, qreal groupRight,
                                        qreal tileW, qreal tileH, qreal hgap, qreal between)
{
    QVector<qreal> out;
    out.reserve(melds.size());
    qreal right = groupRight;
    for (const Meld& m : melds) {
        const qreal w = meldWidthOf(m, ownerSeat, tileW, tileH, hgap);
        out.append(right - w);
        right = right - w - between;
    }
    return out;
}

// ---------------------------------------------------------------------------
// 布局：解「十字环」的尺寸约束；风盘刻意做小
// ---------------------------------------------------------------------------
void TableLayout::computeLayout(QSize viewSize, const TableModel& model,
                                qreal scoreTextW, qreal scoreDigitsW)
{
    const qreal W = qMax(1, viewSize.width());
    const qreal H = qMax(1, viewSize.height());

    const qreal availW = W - 2 * kPad - 4 * kGap;
    const qreal availH = H - 2 * kPad - 4 * kGap;

    const qreal kAspect = 1.36;
    // 牌河 / 副露的牌相对手牌的比例。0.80 时牌河牌只有手牌的 80% 宽，
    // 再叠上「风盘先钉死、牌河硬缩进去」的老逻辑，实际只剩 25px（手牌的 45%），读不出来。
    // 现在风盘尺寸由内容反推，牌河能拿到标称大小；再按用户要求 ×0.95 收一点。
    const qreal kRiverScale = 0.84 * 0.95;   // ≈0.798
    const qreal kRiverAspect = kAspect * kRiverScale;   // 牌河牌的高宽比（相对 tw）
    // 宝牌指示牌尺寸：**手牌宽 × 0.76**（= 上一版 0.95 的 0.8 —— 用户反馈太大）。
    // 与牌河牌解耦：它由风盘内那一行的高度决定，不受牌河尺寸影响。
    const qreal kDoraScale = 0.95 * 0.8;

    // 本帧牌河要预留几行：标称 3 行；**某家超过 18 张时按同一函数继续长**。
    // 预留范围、以及左右两家牌河占的横向空间都要跟着它走，
    // 否则第 4 行会压到邻家手牌（自检有断言）。
    int riverRows = kMaxRiverRows;
    for (int s = 0; s < 4; ++s)
        riverRows = qMax(riverRows, riverRowsFor(model.discards(s).size()));
    m_riverRows = riverRows;

    qreal tw = (availH - 2.0 * kSideReserve) / (13.0 * 1.05 + 0.6);
    tw = qMin(tw, availW / (2.0 * kAspect + 2.0 * riverRows * kRiverAspect + 4.0));
    tw = qBound(13.0, tw, 78.0);

    const qreal th = tw * kAspect;
    qreal rw = tw * kRiverScale;
    qreal rh = rw * kAspect;

    m_tileW = tw;
    m_tileH = th;

    // ---------------------------------------------------------------------
    // 风盘：尺寸**由内容反推**，而不是先定一个"好看的正方形"再把牌河塞进去。
    // 老顺序（先钉 ~209 的正方形 → 牌河按剩余空间缩）正是牌河/副露过小的根因。
    // 盘内要放：宝牌指示牌一行（最多 5 张）、四家点数、场次、立直棒；
    // 盘外要留：四家牌河各 3 行 + 一点余量（这块范围只算布局，不画线框）。
    // ---------------------------------------------------------------------
    // 宝牌指示牌尺寸：**手牌宽 × 0.76**（= 上一版 0.95 的 0.8，用户反馈太大）。
    // 与牌河牌解耦，由风盘内那一行单独定尺。
    const qreal doraTileW = tw * kDoraScale;
    const qreal doraTileH = doraTileW * kAspect;
    const qreal doraGap = 3.0;
    // 一行 5 张指示牌（最多 5 张：宝牌 1 + 四个杠各 1）。
    // **不再给「宝牌」字样留位置**（那个标签已按需求去掉），整行要收进中间列。
    const qreal doraRowNeed = kMaxDoraIndicators * doraTileW
            + (kMaxDoraIndicators - 1) * doraGap + 8.0;
    // 牌河一行 6 列，其中**最多 1 张横置**——只有立直宣言牌横置，且每家至多 1 张。
    // （按"整行全横置"估会多要一倍宽度，把牌河压掉一半；那是过度保守。）
    const qreal riverRowNeed = rh + 5.0 * rw + 5.0 * qMax(1.0, rw * kRiverColGap);

    // 盘内纵向区带（与 paintCenter 用同一批公式，保证算出来一致）
    const qreal estScoreH = qBound(13.0, W * 0.012, 19.0);
    const qreal estStickH = 10.0;
    const qreal estStickGap = 7.0;
    const qreal estCenterH = qBound(16.0, W * 0.022, 30.0) + qBound(12.0, W * 0.017, 22.0)
            + qBound(11.0, W * 0.012, 15.0) + 4.0;
    // 盘内宽度取这两个要求里更宽的那个：
    //   ① 牌河一行 6 列放得下盘内宽度（盘外那圈牌河才不会被迫缩小 —— 见下面那段钳制）；
    //   ② **中间列**（左右两条「立直棒 + 点数」带之间）塞得下 5 张宝牌指示牌。
    //      这就是「五张指示牌要包含在四家分数框以内」对风盘宽度提的要求。
    const qreal sideBands = estStickH + estStickGap + estScoreH;
    const qreal innerNeed = qMax(riverRowNeed - 2.0 * kGap, 2.0 * (sideBands + 6.0) + doraRowNeed);
    // 上下各要一条「立直棒 + 点数」带（每边都是棒在外、点数在内），四边同构
    const qreal chContent = (doraTileH + 6.0) + 2.0 * (estStickH + estStickGap + estScoreH)
            + estCenterH + 6.0;
    // 内边距 pad = 盘宽的 3%（与下文同一公式）→ 盘内约占 94%。
    // 这个除数必须跟着 pad 的比例走：pad 取 3% 时用 0.94（曾写死 0.88，那对应 6%）。
    qreal cw = qMax(200.0, innerNeed / 0.94);
    qreal ch = qMax(140.0, chContent + 2.0 * qMax(6.0, cw * 0.03));

    // 盘外一圈：牌河 N 行 + 一点余量（N = riverRows，正常 3；有家超过 18 张就更多）。
    // **河区线框已删除，但这块预留量仍然要算** ——
    // 牌河是真的占这块地方，预算不算它，风盘会长到让四家的河压上手牌。
    const qreal riverStep0 = rh + qMax(1.0, rh * kRiverRowGap);
    // 河区最外一张牌与手牌之间的余量。0.55 太奢侈 —— 它直接吃掉风盘能用的高度，
    // 是「风盘偏扁、不像正方形」的元凶之一（见下面把 ch 补到接近 cw 的那段）。
    const qreal riverPad = qMax(6.0, rh * 0.35);
    const qreal riverExtent = kGap + (riverRows - 1) * riverStep0 + rh + riverPad;

    // 外圈预算：盘半 + riverExtent 必须落在「屏幕半 − 内边距 − 手牌厚」之内，
    // 再留一点呼吸，**绝不能压到四家手牌**。
    const qreal slack = kGap;
    const qreal budgetW = 2.0 * (W / 2.0 - kPad - th - riverExtent - slack);
    const qreal budgetH = 2.0 * (H / 2.0 - kPad - th - riverExtent - slack);
    const qreal maxCh = qMax(110.0, budgetH);
    cw = qMin(cw, qMax(150.0, budgetW));
    // 风盘**尽量接近正方形**：宽度被「牌河一行 6 列」撑着，按内容算出的高度总偏矮。
    // 所以把高度补上去（最多补到 cw = 正方形）；补高只影响盘内留白与牌河的起步距离，
    // 预算里已经算进 riverExtent，补高不会挤到手牌（自检有断言）。
    ch = qMin(qMax(ch, qMin(cw, maxCh)), maxCh);
    m_centerRect = QRectF((W - cw) / 2.0, (H - ch) / 2.0, cw, ch);
    // 河区预留范围（自检要断言牌河这一圈不碰手牌，所以在这里算、存成成员）
    m_riverArea = m_centerRect.adjusted(-riverExtent, -riverExtent, riverExtent, riverExtent);

    // 牌河不得与**相邻两家**的河重叠。
    // 相邻家的河从「风盘半宽 + 间距」起步，两者相交要求**两个方向同时**重叠，
    // 所以界限是半个**较大**边（max），不是 min —— 盘做成宽扁形时，
    // 用 min 会平白把牌河再压小一轮（这正是牌河偏小的第二个来源）。
    {
        const qreal cgap0 = qMax(1.0, rw * kRiverColGap);  // 与绘制处同一公式
        const qreal halfPanel = qMax(cw, ch) / 2.0 + kGap; // 可用的半行宽
        const qreal worstHalf = (rh + 5.0 * rw + 5.0 * cgap0) / 2.0;   // 一行最多 1 张横置
        if (worstHalf > halfPanel) {
            const qreal k = halfPanel / worstHalf;
            rw *= k;
            rh *= k;
        }
    }

    m_riverW = rw;
    m_riverH = rh;

    const qreal riverStartVert = ch / 2.0 + kGap;
    const qreal riverStartHorz = cw / 2.0 + kGap;
    const qreal riverStep = rh + qMax(1.0, rh * kRiverRowGap);

    const qreal plateH = qBound(18.0, H * 0.026, 26.0);
    const qreal plateW = qBound(100.0, W * 0.130, 172.0);
    m_plateReserve = plateW + 2.0 * kGap + m_riverW;   // 名牌 + 余量，供角落保留用
    m_plateW = plateW;
    m_plateH = plateH;

    for (int pos = 0; pos < 4; ++pos) {
        SeatFrame& f = m_frames[pos];
        const bool odd = (pos % 2) != 0;
        f.uLen = odd ? H : W;
        f.vLen = odd ? W : H;

        switch (pos) {
        case 0:
            f.toScreen = QTransform(1, 0, 0, 1, W / 2.0, H / 2.0);
            break;
        case 1:
            f.toScreen = QTransform(0, -1, 1, 0, W / 2.0, H / 2.0);
            break;
        case 2:
            f.toScreen = QTransform(-1, 0, 0, -1, W / 2.0, H / 2.0);
            break;
        default:
            f.toScreen = QTransform(0, 1, -1, 0, W / 2.0, H / 2.0);
            break;
        }

        f.handV1 = f.vLen / 2.0 - kPad;
        f.handV0 = f.handV1 - th;
        f.riverStartV = odd ? riverStartHorz : riverStartVert;
        f.riverStep = riverStep;

        // 名牌沿**对角线向内**移到「两家牌河之间的拐角空隙」：
        // 离开屏幕角落，就不会和邻家的手牌行 / 副露带挤在一起
        const qreal diag = m_tileH * 1.9;
        if (pos == 0)
            f.plate = QRectF(kPad + diag, H - kPad - plateH - diag, plateW, plateH);
        else if (pos == 2)
            f.plate = QRectF(W - kPad - plateW - diag, kPad + diag, plateW, plateH);
        else if (pos == 1)
            f.plate = QRectF(W - kPad - plateW - diag, H - kPad - plateH - diag, plateW, plateH);
        else
            f.plate = QRectF(kPad + diag, kPad + diag, plateW, plateH);
    }

    // ---- 盘内区带 ----
    // **四边同构**：每一边都是「立直棒带（贴盘边）+ 点数带（在其内侧）」两条。
    // 好处有两个：
    //   ① 各家点数到风盘边缘的距离**处处相等**（= pad + stickH + stickGap），盘看起来正；
    //   ② 立直棒落在**各家自己那一侧** —— 自家的棒在自家面前、対面的在対面面前，
    //      而不是把四家的棒统统堆在盘底（那是报障的 bug）。
    const qreal pad = qMax(6.0, cw * 0.03);
    const qreal scoreH = qBound(13.0, cw * 0.105, 19.0);
    const qreal stickH = qBound(6.0, ch * 0.025, 10.0);
    const qreal stickGap = 7.0;
    // 左右两条竖排点数带与**中间列**之间的留白（与下面 colLeft/colRight 同一常量）
    const qreal colPad = 6.0;
    const qreal l1H = qBound(16.0, cw * 0.135, 30.0);
    const qreal l2H = qBound(12.0, cw * 0.100, 22.0);
    const qreal infoH = qBound(11.0, cw * 0.078, 15.0);
    const qreal centerNeed = l1H + l2H + infoH + 4.0;
    const qreal innerL = m_centerRect.left() + pad;
    const qreal innerW = cw - 2.0 * pad;
    // 「中间列」= 左右两条竖排点数带之间（宝牌行、场次、供託都住在这根中轴上）
    const qreal colW = qMax(20.0, innerW - 2.0 * (stickH + stickGap + scoreH + colPad));
    // 宝牌行能用多高：三个上限取最小 ——
    //   ① 标称尺寸（手牌宽 × 0.76，见 kDoraScale）；
    //   ② 纵向余量：扣掉**上下各一条「立直棒 + 点数」带**、中间块与各处间距；
    //   ③ **中间列的宽度**：5 张牌 + 4 个间距必须塞得进这一列（宽高同缩，绝不横向压扁）。
    // ③ 就是「五张指示牌要包含在四家分数框以内」这条要求：盘子不够宽时，
    // 指示牌跟着变小（形状不变），而不是挤出行外。
    const qreal doraAvailV = qMax(7.0, (ch - 2.0 * pad) - 2.0 * (stickH + stickGap + scoreH)
                                        - centerNeed - 6.0);
    const qreal doraGaps = (kMaxDoraIndicators - 1) * 3.0;
    const qreal doraTileWFit = qMax(4.0, (colW - 4.0 - doraGaps) / kMaxDoraIndicators);
    const qreal dTileH = qMin(doraTileH, qMin(doraAvailV, doraTileWFit * kAspect));
    // 四家统一的得点框尺寸：框长 = **四家里最长的分数文本**（调用处量好传入）
    //        + 文字两侧各 7px 内边距 + **三位数字的宽度**（用户要求：点数涨到十几万也不挤字）。
    // 绑在盘宽上的字号（scoreH）由这里算，所以宽度值也只能由调用处的字体度量给出。
    m_scoreBoxSize = QSizeF(scoreTextW + 14.0 + scoreDigitsW, scoreH);
    // 上（対面）与下（自家）：棒在外、点数在内
    m_stickBand[2] = QRectF(innerL, m_centerRect.top() + pad, innerW, stickH);
    m_scoreBand[2] = QRectF(innerL, m_stickBand[2].bottom() + stickGap, innerW, scoreH);
    m_stickBand[0] = QRectF(innerL, m_centerRect.bottom() - pad - stickH, innerW, stickH);
    m_scoreBand[0] = QRectF(innerL, m_stickBand[0].top() - stickGap - scoreH, innerW, scoreH);
    const qreal colLeft = innerL + stickH + stickGap + scoreH + colPad;
    const qreal colRight = m_centerRect.right() - pad - stickH - stickGap - scoreH - colPad;
    // 顺序：**対面点数在上，宝牌行紧贴其下**（用户要求）。
    // 宝牌行只占**中间列**（不再横跨整个盘内宽度）：去掉「宝牌」字样后
    // 5 张指示牌正好收在四家点数框围出的那一列里，整盘看着是一根中轴。
    m_doraRect = QRectF(colLeft, m_scoreBand[2].bottom() + 6.0,
                        qMax(20.0, colRight - colLeft), dTileH);
    // 左（上家）与右（下家）：竖排（绘制时转 90°），与上下**同构** —— 棒在外、点数在内。
    // 起点取「対面点数带下沿」而**不再**取宝牌行下沿：宝牌行只占**中间列**，
    // 与左右两条竖排带在 x 上完全不相交（colLeft = 竖排带右沿 + colPad），所以竖排带
    // 可以用满「上点数带下沿 → 下点数带上沿」这一整条。两端各留同样的 6px，
    // 带中心就正好落在风盘的**水平中轴**上 → 左右两家的得点框沿各自的边**居中**
    // （旧版被宝牌行往下顶，两家点数明显偏下）。
    const qreal vTop = m_scoreBand[2].bottom() + 6.0;
    const qreal vLen = qMax(40.0, (m_scoreBand[0].top() - 6.0) - vTop);
    m_stickBand[3] = QRectF(innerL, vTop, stickH, vLen);
    m_scoreBand[3] = QRectF(m_stickBand[3].right() + stickGap, vTop, scoreH, vLen);
    m_stickBand[1] = QRectF(m_centerRect.right() - pad - stickH, vTop, stickH, vLen);
    m_scoreBand[1] = QRectF(m_stickBand[1].left() - stickGap - scoreH, vTop, scoreH, vLen);
    // 中间块 = **宝牌行以下**到自家点数带之间的空间，左右与宝牌行同宽（一根中轴）。
    // 起点必须取「宝牌行下沿」：宝牌行紧贴対面点数，
    // 若还按「対面点数带下沿」起算，场次首行会被宝牌牌面盖住。
    const qreal midTop = qMax(m_scoreBand[2].bottom(), m_doraRect.bottom());
    const qreal midLeft = m_doraRect.left();
    const qreal midRight = m_doraRect.right();
    m_centerBand = QRectF(midLeft, midTop, qMax(20.0, midRight - midLeft),
                          qMax(20.0, m_scoreBand[0].top() - midTop));
}

HandLayout TableLayout::layoutHand(int pos, const TableModel& model) const
{
    const SeatFrame& f = m_frames[pos];
    const int seat = (model.mySeat() + pos) % 4;
    int concealed = qMax(0, model.concealedCount(seat));
    // concealedCount 含「刚摸到的那张」；手牌块只排实际手牌，摸牌另占一格。
    // 不减这一张的话，手牌块会宽出一张牌，摸牌槽也跟着整体右移。
    if (seatHasDrawnTile(model, seat) && concealed > 0) {
        concealed -= 1;
    }
    const QVector<Meld> melds = model.melds(seat);

    HandLayout L;
    L.hgap = qMax(1.0, m_tileW * 0.06);
    L.drawGap = m_tileW * 0.26;    // 摸牌紧贴手牌（原来 0.55 太远）
    L.meldGap = m_tileW * 0.55;    // 手牌块与副露之间
    L.meldBetween = m_riverW * 0.35;

    // 行末（= 该家自己的右手侧）。四家的 +u 都指向自己的右方，
    // 所以对局中四家的副露会分别落在屏幕的四个角上。
    const qreal u1 = f.uLen / 2.0 - kPad;
    // ---- 角落保留 ----
    // 每个屏幕角落同时住着「某家的副露带」和「另一家的手牌行末 / 名牌」，
    // 所以副露带必须从行末内缩一段。
    // 每个角落的住户不同，保留量也不同：
    //   横排的两家（自己 / 对家）副露带沿屏幕横向走，要让开角落名牌的**宽度**；
    //   竖排的两家（下家 / 上家）副露带沿屏幕纵向走，要让开名牌的**高度**；
    //   左上角曾经还住着宝牌指示牌栏，现已搬进风盘 —— 那里只剩名牌。
    //   （宝牌栏的让位量曾经被算进 `reservePlus`，两端共用时会把行宽白吃一百多 px，
    //     块放不下时 ③ 又把整块推回栏上，第 4、5 张宝牌指示牌就被对家的摸牌盖住。）
    const qreal gap = qMax(4.0, m_riverW * 0.35);
    const qreal plateReserve = (pos == 0 || pos == 2) ? (m_plateW + gap) : (m_plateH + gap);
    L.meldRight = u1 - plateReserve;

    // 副露钉在右下角：第 1 副在最右，之后依次向左排，已有的副露不会移动。
    qreal groupW = 0.0;
    for (int i = 0; i < melds.size(); ++i) {
        groupW += meldWidthOf(melds.at(i), seat, m_riverW, m_riverH, L.hgap);
        if (i + 1 < melds.size())
            groupW += L.meldBetween;
    }
    L.meldLeft = L.meldRight - groupW;   // 必须跟着让位后的 meldRight 走

    // 「手牌 + 摸牌」作为一个整体块：
    //   左边缘锚点按**满手 13 张**算（与当前张数无关）→ 摸牌 / 打牌都不位移；
    //   摸牌紧贴手牌右侧（独立间隔）→ 副露使手牌变短时，摸牌跟着一起往左收；
    //   基准比居中再偏左一点。
    const qreal handW = concealed > 0
            ? concealed * m_tileW + qMax(0, concealed - 1) * L.hgap
            : 0.0;
    const qreal fullW = kHandSlots * m_tileW + qMax(0, kHandSlots - 1) * L.hgap;

    L.handLeft = -fullW / 2.0 - m_tileW * 1.5;      // 基准：偏左一点
    L.drawnLeft = L.handLeft + handW + L.drawGap;

    // 避让与边界**一次算完**：分开钳制会互相抵消——
    // 后一段在 handLeft 已经越过边界时会算出**负的 over**，直接 `-=` 等于把整块往右推，
    // 把前一段让出来的空间又吃回去（副露盖住摸牌、摸牌盖住宝牌栏都是这么来的）。
    // 所以这里只算**一个** over，且 ③ 的右移量必须封顶。
    {
        const qreal uLimit = f.uLen / 2.0 - kPad - plateReserve;   // 行两端的可用半长
        qreal over = 0.0;
        // ① 不得压到副露（优先级最高）
        if (!melds.isEmpty())
            over = qMax(0.0, (L.drawnLeft + m_tileW + L.meldGap) - L.meldLeft);
        // ② 不得伸进角落保留带（名牌）
        over = qMax(over, qMax(0.0, (L.drawnLeft + m_tileW) - uLimit));
        // ③ 行首一侧的角落：只有上面两条都不需要左移时才为它让步，
        //    并且**右移量必须封顶**——封顶要同时看两边：
        //      `room`（行末端一侧的余量）与「不能反压到副露」（副露优先级最高）。
        //    否则这一推会把刚让开的空间又吃回去（牌河/宝牌栏被盖住都是这么来的）。
        if (over <= 0.0 && L.handLeft < -uLimit) {
            const qreal push = -uLimit - L.handLeft;                   // 需要的右移量
            qreal room = uLimit - (L.drawnLeft + m_tileW);             // 另一侧还剩多少
            if (!melds.isEmpty())
                room = qMin(room, L.meldLeft - (L.drawnLeft + m_tileW + L.meldGap));
            over = -qMin(push, qMax(0.0, room));
        }
        L.handLeft -= over;
        L.drawnLeft -= over;
    }

    L.areaLeft = L.handLeft;
    return L;
}

qreal TableLayout::plateReserve() const
{
    return qMax(72.0, m_plateReserve);
}

QRectF TableLayout::riverSlotScreen(int seat, int index, const TableModel& model) const
{
    if (index < 0)
        return QRectF();
    const int pos = ((seat - model.mySeat()) % 4 + 4) % 4;
    const SeatFrame& f = m_frames[pos];
    const QStringList tiles = model.discards(seat);
    const int n = tiles.size();
    if (index >= n)
        return QRectF();

    const int row = index / kRiverCols;
    const qreal cgap = qMax(1.0, m_riverW * kRiverColGap);
    qreal rowW = 0;
    for (int c = 0; c < kRiverCols; ++c) {
        const int i = row * kRiverCols + c;
        if (i >= n)
            break;
        rowW += (model.discardSideways(seat, i) ? m_riverH : m_riverW) + cgap;
    }
    if (rowW > 0)
        rowW -= cgap;

    qreal u = -rowW / 2.0;
    const qreal v = f.riverStartV + row * f.riverStep;
    for (int c = 0; c < kRiverCols; ++c) {
        const int i = row * kRiverCols + c;
        if (i >= n)
            break;
        const bool side = model.discardSideways(seat, i);
        const qreal w = side ? m_riverH : m_riverW;
        const qreal h = side ? m_riverW : m_riverH;
        if (i == index) {
            const QRectF local(u, v + (side ? (m_riverH - m_riverW) / 2.0 : 0.0), w, h);
            return f.toScreen.mapRect(local);
        }
        u += w + cgap;
    }
    return QRectF();
}

QRectF TableLayout::handSlotLocal(int pos, int index, bool drawn, const TableModel& model) const
{
    const SeatFrame& f = m_frames[pos];
    const HandLayout L = layoutHand(pos, model);
    const qreal u = drawn ? L.drawnLeft
                          : L.handLeft + index * (m_tileW + L.hgap);
    return QRectF(u, f.handV0, m_tileW, m_tileH);
}
