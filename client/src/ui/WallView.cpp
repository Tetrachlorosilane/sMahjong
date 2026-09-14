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

QString suitOf(const QString& tile)
{
    if (tile.isEmpty()) {
        return QString();
    }
    return tile.right(1);
}

int kindOrder(const QString& tile)
{
    // 排序用：m < p < s < z，同花色按点数（0 当作 5 处理，赤五排在一起）
    if (tile.size() < 2) {
        return 999;
    }
    const int num = tile.at(0).digitValue();
    const QChar suit = tile.at(1);
    const int base = suit == QLatin1Char('m')   ? 0
                     : suit == QLatin1Char('p') ? 100
                     : suit == QLatin1Char('s') ? 200
                                                : 300;
    return base + (num == 0 ? 5 : num);
}
}   // namespace

WallView::WallView(QWidget* parent)
    : QWidget(parent, Qt::Window)
{
    setWindowTitle(lang::t(QStringLiteral("ui.replay.wall_title")));
    setMinimumSize(760, 360);
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(10, 8, 10, 8);
    layout->setSpacing(4);
    m_title = new QLabel(this);
    m_title->setStyleSheet(QStringLiteral("color:#E6E8EE;font-size:13px;"));
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

void WallView::rebuild()
{
    m_rows.clear();
    m_deadCells.clear();
    m_seatOfRow.clear();
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
    const int dealer = r.dealer;

    // 行 = 座位（从庄家起），每行 = 该家按抓牌顺序拿到的牌（不含王牌）
    m_seatOfRow.resize(4);
    m_rows.resize(4);
    QVector<QVector<int>> byOrder(4);          // 行 → 牌山下标（按 order 排序）
    QVector<int> maxOrder(4, 0);
    for (int k = 0; k < ReplayModel::kWallSize; ++k) {
        const ReplayModel::WallSlot& s = wallSlots.at(k);
        if (s.dead) {
            Cell c;
            c.wallIndex = k;
            c.entryIndex = s.takenAt;
            c.fate = DeadWall;
            m_deadCells << c;
            continue;
        }
        if (s.seat < 0) {
            continue;
        }
        int row = (s.seat - dealer + 4) % 4;
        if (row < 0 || row > 3) {
            row = 0;
        }
        if (s.order + 1 > maxOrder[row]) {
            maxOrder[row] = s.order + 1;
        }
        byOrder[row] << k;
    }
    for (int row = 0; row < 4; ++row) {
        m_seatOfRow[row] = (dealer + row) % 4;
        std::sort(byOrder[row].begin(), byOrder[row].end(), [&wallSlots](int a, int b) {
            return wallSlots.at(a).order < wallSlots.at(b).order;
        });
    }

    // 每张牌在当前时刻的去处：按行做「贪心分配」——同一牌码可能有多张，
    // 依拿牌顺序先把牌河里的份数分掉、再分副露的份数，剩下的就算在手里。
    const ReplayModel::GodState& god = m_model->godState(m_step);
    for (int row = 0; row < 4; ++row) {
        const int seat = m_seatOfRow.at(row);
        QHash<QString, int> riverLeft;
        QHash<QString, int> meldLeft;
        for (const QString& t : god.rivers[seat]) {
            riverLeft[t] += 1;
        }
        for (const auto& m : god.melds[seat]) {
            const QString called = m.second.isEmpty() ? QString() : m.second.first();
            for (const QString& t : m.second) {
                if ((m.first == QLatin1String("chi") || m.first == QLatin1String("pon")
                     || m.first == QLatin1String("daiminkan"))
                    && t == called) {
                    continue;
                }
                if (m.first == QLatin1String("kakan") && t != called) {
                    continue;
                }
                meldLeft[t] += 1;
            }
        }
        for (int k : byOrder[row]) {
            const ReplayModel::WallSlot& s = wallSlots.at(k);
            Cell c;
            c.wallIndex = k;
            c.entryIndex = s.takenAt;
            // ⚠ 必须与**当前步**比较：只按"整局里会不会被拿走"着色的话，
            //   一开始就把整局都画成已拿走（真机截图上第一版就是这样：前半背面、后半全部翻开了）。
            const bool taken = s.takenAt >= 0 && s.takenAt <= m_step;
            if (s.movedByKan) {
                c.fate = MovedByKan;
            } else if (!taken) {
                c.fate = Untaken;
            } else {
                // 该牌的实际牌码：由模型给出的牌山 id 推（与服务端同一套 id 编码）
                c.fate = InHand;
                const int id = r.wall.at(k);
                const int kind = id / 4;
                const int copy = id % 4;
                const int suitI = kind / 9;
                const int num = kind % 9 + 1;
                static const char* sc = "mpsz";
                const bool red = (kind == 4 || kind == 13 || kind == 22) && copy == 0;
                const QString code = suitI == 3
                                             ? QStringLiteral("%1z").arg(num)
                                             : (red ? QStringLiteral("0%1").arg(QLatin1Char(sc[suitI]))
                                                    : QStringLiteral("%1%2")
                                                              .arg(num)
                                                              .arg(QLatin1Char(sc[suitI])));
                if (riverLeft.value(code, 0) > 0) {
                    riverLeft[code] -= 1;
                    c.fate = Discarded;
                } else if (meldLeft.value(code, 0) > 0) {
                    meldLeft[code] -= 1;
                    c.fate = Melded;
                }
            }
            m_rows[row] << c;
        }
    }

    setWindowTitle(lang::t(QStringLiteral("ui.replay.wall_title_round")).arg(m_model->roundText(m_round)));
    if (m_title != nullptr) {
        int taken = 0;
        for (const QVector<Cell>& row : m_rows) {
            for (const Cell& c : row) {
                if (c.fate != Untaken) {
                    taken++;
                }
            }
        }        m_title->setText(lang::t(QStringLiteral("ui.replay.wall_legend"))
                                 .arg(m_model->roundText(m_round))
                                 .arg(taken));
    }
}

void WallView::paintEvent(QPaintEvent* event)
{
    Q_UNUSED(event)
    QPainter p(this);
    p.fillRect(rect(), kBg);
    if (m_model == nullptr || m_rows.isEmpty()) {
        p.setPen(kFg);
        p.drawText(rect(), Qt::AlignCenter, lang::t(QStringLiteral("ui.replay.wall_empty")));
        return;
    }
    p.setRenderHint(QPainter::Antialiasing, true);
    const ReplayModel::Round& r = m_model->rounds().at(m_round);
    const QVector<ReplayModel::WallSlot>& wallSlots = m_model->wallSlots(m_round);
    QFont f = font();
    f.setPointSizeF(8.5);
    p.setFont(f);

    int cols = 14;   // 至少铺到"庄家第 14 张"
    for (const QVector<Cell>& row : m_rows) {
        cols = qMax(cols, row.size());
    }
    const int left = 68;
    const int top = 34;
    const int rowH = m_cellH + 14;

    auto colX = [&](int col) {
        // 前 12 列按「每 4 张一组」留组间空隙，视觉上就是三次抓 4 张
        const int groups = col / 4;
        const int inGroup = col % 4;
        return left + groups * (4 * (m_cellW + m_gap) + m_groupGap) + inGroup * (m_cellW + m_gap);
    };

    for (int row = 0; row < m_rows.size(); ++row) {
        const int seat = m_seatOfRow.at(row);
        const int y = top + row * rowH;
        p.setPen(kFg);
        QFont bf = f;
        bf.setBold(true);
        p.setFont(bf);
        const QString wind = m_model->playerName(seat);
        p.drawText(QRect(6, y, left - 12, m_cellH), Qt::AlignVCenter | Qt::AlignRight,
                   lang::t(QStringLiteral("ui.replay.wall_row")).arg(row).arg(wind));
        p.setFont(f);

        const QVector<Cell>& cells = m_rows.at(row);
        for (int col = 0; col < cells.size(); ++col) {
            const Cell& c = cells.at(col);
            QRect rc(colX(col), y, m_cellW, m_cellH);
            const int id = c.wallIndex >= 0 ? r.wall.at(c.wallIndex) : -1;
            const int kind = id >= 0 ? id / 4 : -1;
            const int copy = id >= 0 ? id % 4 : 0;
            const int suitI = kind >= 0 ? kind / 9 : -1;
            const int num = kind >= 0 ? kind % 9 + 1 : 0;
            static const char* sc = "mpsz";
            QString code;
            if (kind >= 0) {
                if (suitI == 3) {
                    code = QStringLiteral("%1z").arg(num);
                } else {
                    const bool red = (kind == 4 || kind == 13 || kind == 22) && copy == 0;
                    code = red ? QStringLiteral("0%1").arg(QLatin1Char(sc[suitI]))
                               : QStringLiteral("%1%2").arg(num).arg(QLatin1Char(sc[suitI]));
                }
            }
            if (c.fate == Untaken || c.fate == DeadWall || c.fate == MovedByKan) {
                TileRenderer::drawBack(p, rc);
            } else {
                TileRenderer::drawSmall(p, rc, code);
            }
            // 状态描边
            QColor edge;
            int width = 1;
            switch (c.fate) {
                case InHand: edge = kHandEdge; break;
                case Discarded: edge = kRiverEdge; break;
                case Melded: edge = kMeldEdge; break;
                case DeadWall: edge = kDeadEdge; width = 2; break;
                case MovedByKan: edge = kMovedEdge; width = 2; break;
                case Untaken:
                default: edge = QColor(0x33, 0x36, 0x3D); break;
            }
            if (code.isEmpty() && c.fate != DeadWall && c.fate != MovedByKan) {
                // 空位（该家还没拿到这么多张）
                p.setPen(QPen(QColor(0x2A, 0x2C, 0x33), 1, Qt::DashLine));
                p.drawRect(rc);
                continue;
            }
            const bool cursor = (c.entryIndex >= 0 && c.entryIndex == m_step)
                                || (c.wallIndex >= 0 && c.wallIndex < wallSlots.size()
                                    && wallSlots.at(c.wallIndex).takenAt == m_step);
            p.setPen(QPen(cursor ? kCursor : edge, cursor ? 3 : width));
            p.drawRect(rc.adjusted(0, 0, -1, -1));
            if (c.fate == Discarded) {
                p.fillRect(rc, QColor(20, 20, 24, 110));   // 打出的牌压暗一点
            }
            const_cast<Cell&>(c).rect = rc;
        }
    }

    // 王牌
    const int deadY = top + m_rows.size() * rowH + 10;
    p.setPen(kDim);
    p.drawText(QRect(6, deadY, left - 12, m_cellH), Qt::AlignVCenter | Qt::AlignRight,
               lang::t(QStringLiteral("ui.replay.wall_dead")));
    const QString deadLabel[3] = {lang::t(QStringLiteral("ui.replay.wall_rinshan")),
                                      lang::t(QStringLiteral("ui.replay.wall_dora")),
                                      lang::t(QStringLiteral("ui.replay.wall_ura"))};
    for (int i = 0; i < m_deadCells.size(); ++i) {
        Cell& c = const_cast<Cell&>(m_deadCells[i]);
        const int seg = i < 4 ? 0 : (i < 9 ? 1 : 2);
        const int segStart = seg == 0 ? 0 : (seg == 1 ? 4 : 9);
        const int inSeg = i - segStart;
        const int x = left + inSeg * (m_cellW + m_gap) + seg * (6 * (m_cellW + m_gap) + 14);
        QRect rc(x, deadY, m_cellW, m_cellH);
        TileRenderer::drawBack(p, rc);
        const bool taken = c.entryIndex >= 0 && c.entryIndex <= m_step;
        p.setPen(QPen(taken ? kMovedEdge : kDeadEdge, taken ? 3 : 2));
        p.drawRect(rc.adjusted(0, 0, -1, -1));
        c.rect = rc;
        p.setPen(kDim);
        if (inSeg == 0) {
            p.drawText(QRect(x, deadY - 13, 120, 12), Qt::AlignLeft, deadLabel[seg]);
        }
    }
}

void WallView::mousePressEvent(QMouseEvent* event)
{
    for (const QVector<Cell>& row : m_rows) {
        for (const Cell& c : row) {
            if (c.rect.contains(event->pos()) && c.entryIndex >= 0) {
                emit seekRequested(c.entryIndex);
                return;
            }
        }
    }
    for (const Cell& c : m_deadCells) {
        if (c.rect.contains(event->pos()) && c.entryIndex >= 0) {
            emit seekRequested(c.entryIndex);
            return;
        }
    }
}
