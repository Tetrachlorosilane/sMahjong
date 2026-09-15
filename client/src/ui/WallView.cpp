#include "WallView.h"

#include <QLabel>
#include <QMouseEvent>
#include <QPainter>
#include <QVBoxLayout>

#include "../i18n/Lang.h"
#include "../model/ReplayModel.h"
#include "TileRenderer.h"

namespace {
const QColor kBg(0x1E, 0x1F, 0x24);
const QColor kFg(0xE6, 0xE8, 0xEE);
const QColor kDim(0x7A, 0x80, 0x8C);
const QColor kHandEdge(0x4A, 0x90, 0xD9);
const QColor kMeldEdge(0x3D, 0xB2, 0x7F);
const QColor kDeadEdge(0xD9, 0xA8, 0x4A);
const QColor kMovedEdge(0xD9, 0x53, 0x4F);
const QColor kCursor(0xFF, 0x53, 0x53);
const QColor kRiverEdge(0x6B, 0x70, 0x7A);
const QColor kEmpty(0x2A, 0x2C, 0x33);

// 四家的"归属色"（牌山底部那一条细线 + 图例）：只用来标"谁拿走的"，
// 与"牌山怎么排"无关 —— 牌山永远是抓牌顺序，不按玩家分组。
const QColor kSeatColor[4] = {
    QColor(0xE6, 0xB8, 0x4A),
    QColor(0x4A, 0x90, 0xD9),
    QColor(0x3D, 0xB2, 0x7F),
    QColor(0xD9, 0x53, 0x4F),
};

/** 牌 id → 牌码（服务端给的牌山是 id：0..135，每 4 张同 kind）。 */
QString tileCodeOfId(int id)
{
    if (id < 0 || id > 135) {
        return QString();
    }
    const int kind = id / 4;
    const int copy = id % 4;
    const int suit = kind / 9;
    const int num = kind % 9 + 1;
    static const char* sc = "mpsz";
    if (suit == 3) {
        return QStringLiteral("%1z").arg(num);
    }
    const bool red = (kind == 4 || kind == 13 || kind == 22) && copy == 0;
    return red ? QStringLiteral("0%1").arg(QLatin1Char(sc[suit]))
               : QStringLiteral("%1%2").arg(num).arg(QLatin1Char(sc[suit]));
}
}   // namespace

WallView::WallView(QWidget* parent)
    : QWidget(parent, Qt::Window)
{
    setWindowTitle(lang::t(QStringLiteral("ui.replay.wall_title")));
    setMinimumSize(1180, 330);
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(10, 8, 10, 8);
    layout->setSpacing(4);
    m_title = new QLabel(this);
    m_title->setStyleSheet(QStringLiteral("color:#E6E8EE;font-size:13px;"));
    m_title->setWordWrap(true);
    layout->addWidget(m_title);
    layout->addStretch(1);
    setStyleSheet(QStringLiteral("background:#1E1F24;"));
}

void WallView::setState(ReplayModel* model, int round, int step)
{
    m_model = model;
    m_round = round;
    m_step = step;
    rebuild();
    update();
}

void WallView::setRound(int round)
{
    m_round = round;
    rebuild();
    update();
}

WallView::Fate WallView::fateOfTakenTile(const ReplayModel::Round& r,
                                         const QVector<ReplayModel::WallSlot>& wallSlots,
                                         const ReplayModel::WallSlot& s, int k) const
{
    const int seat = s.seat;
    const QString code = tileCodeOfId(r.wall.value(k, -1));
    if (seat < 0 || code.isEmpty()) {
        return InHand;
    }
    const ReplayModel::GodState& god = m_model->godState(m_step);
    int inRiver = 0;
    for (const QString& t : god.rivers[seat]) {
        if (t == code) {
            inRiver++;
        }
    }
    int inMeld = 0;
    for (const auto& m : god.melds[seat]) {
        if (m.second.contains(code)) {
            inMeld++;
        }
    }
    // 牌山下标顺序 == 抓牌顺序（配牌三轮 → 补第 13 张 → 庄家第 14 张 → 逐张摸牌），
    // 所以「第几张」直接数下标即可，不必再按 takenAt 排序。
    int ordinal = 0;
    for (int j = 0; j <= k && j < wallSlots.size(); ++j) {
        const ReplayModel::WallSlot& sj = wallSlots.at(j);
        if (sj.seat != seat || sj.takenAt < 0 || sj.takenAt > m_step) {
            continue;
        }
        if (tileCodeOfId(r.wall.value(j, -1)) == code) {
            ordinal++;
        }
    }
    if (ordinal <= inRiver) {
        return Discarded;
    }
    if (ordinal <= inRiver + inMeld) {
        return Melded;
    }
    return InHand;
}

void WallView::rebuild()
{
    m_cells.clear();
    if (m_model == nullptr || m_model->roundCount() == 0) {
        setWindowTitle(lang::t(QStringLiteral("ui.replay.wall_title")));
        return;
    }
    if (m_round < 0 || m_round >= m_model->roundCount()) {
        m_round = 0;
    }
    const ReplayModel::Round& r = m_model->rounds().at(m_round);
    const QVector<ReplayModel::WallSlot>& wallSlots = m_model->wallSlots(m_round);
    if (r.wall.size() != ReplayModel::kWallSize || wallSlots.size() != ReplayModel::kWallSize) {
        return;
    }
    m_cells.resize(ReplayModel::kWallSize);
    for (int k = 0; k < ReplayModel::kWallSize; ++k) {
        const ReplayModel::WallSlot& s = wallSlots.at(k);
        Cell c;
        c.wallIndex = k;
        c.seat = s.seat;
        c.entryIndex = s.takenAt;
        // ⚠ 必须与**当前步**比较：只按"整局里会不会被拿走"着色的话，一开始就把整局全画成已拿走。
        const bool taken = s.takenAt >= 0 && s.takenAt <= m_step;
        if (s.movedByKan) {
            // 开杠把牌山末尾一张移进王牌补位 → 从此摸不到（红色底框）
            c.fate = MovedByKan;
        } else if (s.dead && !taken) {
            c.fate = DeadWall;          // 王牌里还没被动过的
        } else if (!taken) {
            c.fate = Untaken;           // 牌背
        } else if (s.seat < 0) {
            c.fate = InHand;
        } else {
            c.fate = fateOfTakenTile(r, wallSlots, s, k);
        }
        m_cells[k] = c;
    }

    setWindowTitle(lang::t(QStringLiteral("ui.replay.wall_title_round")).arg(m_model->roundText(m_round)));
    if (m_title != nullptr) {
        int taken = 0;
        for (const Cell& c : m_cells) {
            if (c.fate == InHand || c.fate == Discarded || c.fate == Melded) {
                taken++;
            }
        }
        QStringList names;
        for (int s = 0; s < 4; ++s) {
            names << QStringLiteral("%1=%2").arg(m_model->playerName(s)).arg(s + 1);
        }
        m_title->setText(lang::t(QStringLiteral("ui.replay.wall_legend"))
                                 .arg(m_model->roundText(m_round))
                                 .arg(taken)
                                 + QLatin1Char('\n')
                                 + lang::t(QStringLiteral("ui.replay.wall_seat_legend"))
                                           .arg(names.join(QStringLiteral(" / "))));
    }
}

void WallView::paintEvent(QPaintEvent* event)
{
    Q_UNUSED(event)
    QPainter p(this);
    p.fillRect(rect(), kBg);
    if (m_model == nullptr || m_cells.isEmpty()) {
        p.setPen(kFg);
        p.drawText(rect(), Qt::AlignCenter, lang::t(QStringLiteral("ui.replay.wall_empty")));
        return;
    }
    p.setRenderHint(QPainter::Antialiasing, true);
    QFont f = font();
    f.setPointSizeF(8.5);
    p.setFont(f);

    const int left = 18;
    const int top = 44;
    const int cols = ReplayModel::kWallSize / 4;      // 34
    auto colX = [&](int col) {
        // 每 4 列留一个稍大的空隙：纯粹是阅读分组（与玩家无关）
        return left + col * (m_cellW + m_gap) + (col / 4) * m_groupGap;
    };
    auto rowY = [&](int row) { return top + row * (m_cellH + m_gap); };

    // 列号标尺（每 4 列标一次，便于定位"第几张"）
    p.setPen(kDim);
    for (int col = 0; col < cols; col += 4) {
        p.drawText(QRect(colX(col), top - 18, 120, 14), Qt::AlignLeft,
                   QStringLiteral("%1").arg(col * 4));
    }

    for (int k = 0; k < m_cells.size(); ++k) {
        Cell& c = m_cells[k];
        const int row = k % 4;
        const int col = k / 4;
        const QRect rc(colX(col), rowY(row), m_cellW, m_cellH);
        c.rect = rc;
        const int id = m_model->rounds().at(m_round).wall.value(k, -1);
        const QString code = tileCodeOfId(id);
        const bool boss = (k >= 122);

        if (c.fate == Untaken || c.fate == DeadWall || c.fate == MovedByKan || code.isEmpty()) {
            if (boss) {
                p.fillRect(rc, QColor(0x3A, 0x33, 0x1C));   // 王牌：底色不同，仍在同一序列里
            }
            TileRenderer::drawBack(p, rc);
        } else {
            if (boss) {
                p.fillRect(rc, QColor(0x3A, 0x33, 0x1C));
            }
            TileRenderer::drawFaceF(p, QRectF(rc), code, TileRenderer::isRed(code), true);
        }
        QColor edge = kEmpty;
        int width = 1;
        switch (c.fate) {
            case InHand: edge = kHandEdge; break;
            case Discarded: edge = kRiverEdge; break;
            case Melded: edge = kMeldEdge; break;
            case DeadWall: edge = kDeadEdge; width = 2; break;
            case MovedByKan: edge = kMovedEdge; width = 2; break;
            case Untaken:
            default: edge = boss ? kDeadEdge : kEmpty; break;
        }
        const bool cursor = (c.entryIndex >= 0 && c.entryIndex == m_step);
        p.setPen(QPen(cursor ? kCursor : edge, cursor ? 3 : width));
        p.drawRect(rc.adjusted(0, 0, -1, -1));
        if (c.fate == Discarded) {
            p.fillRect(rc, QColor(20, 20, 24, 110));        // 打出的牌压暗
        }
        // 归属色：底部一条细线 = 这张是谁拿走的（四家混合排列也能看出归属）
        if (c.seat >= 0 && c.fate != Untaken && c.fate != DeadWall) {
            p.fillRect(QRect(rc.left() + 2, rc.bottom() - 3, rc.width() - 4, 3),
                       kSeatColor[c.seat]);
        }
    }

    // 王牌区标注（k = 122 起）
    const int deadX = colX(122 / 4);
    p.setPen(kDeadEdge);
    p.drawText(QRect(deadX, rowY(3) + m_cellH + 6, 300, 14), Qt::AlignLeft,
               lang::t(QStringLiteral("ui.replay.wall_dead_note")));
}

void WallView::mousePressEvent(QMouseEvent* event)
{
    for (const Cell& c : m_cells) {
        if (c.rect.contains(event->pos()) && c.entryIndex >= 0) {
            emit seekRequested(c.entryIndex);
            return;
        }
    }
}
