#include "model/TableModel.h"

#include "i18n/Lang.h"

#include <QDebug>

#include "model/Tile.h"
#include "net/Protocol.h"

#include <QDateTime>
#include <QJsonValue>
#include <QSet>
#include <algorithm>

namespace {

// 从手牌字符串数组统计 kind 计数
QVector<int> countsOf(const QStringList& tiles)
{
    QVector<int> counts(mj::KindCount, 0);
    for (const QString& t : tiles) {
        const int k = mj::kindOfTile(t);
        if (k >= 0 && k < mj::KindCount)
            counts[k] += 1;
    }
    return counts;
}

// 把 counts 拆成 setsNeeded 个面子（刻子 / 顺子）
bool canFormSets(QVector<int>& counts, int need)
{
    if (need == 0) {
        for (int c : counts) {
            if (c != 0)
                return false;
        }
        return true;
    }
    int i = 0;
    while (i < mj::KindCount && counts[i] == 0)
        ++i;
    if (i == mj::KindCount)
        return false;

    if (counts[i] >= 3) {
        counts[i] -= 3;
        const bool ok = canFormSets(counts, need - 1);
        counts[i] += 3;
        if (ok)
            return true;
    }
    if (i < 27 && (i % 9) <= 6 && counts[i + 1] > 0 && counts[i + 2] > 0) {
        counts[i] -= 1;
        counts[i + 1] -= 1;
        counts[i + 2] -= 1;
        const bool ok = canFormSets(counts, need - 1);
        counts[i] += 1;
        counts[i + 1] += 1;
        counts[i + 2] += 1;
        if (ok)
            return true;
    }
    return false;
}

// 七对子
bool isSevenPairs(const QVector<int>& counts)
{
    int pairs = 0;
    for (int c : counts) {
        if (c == 0)
            continue;
        if (c != 2)
            return false;
        ++pairs;
    }
    return pairs == 7;
}

// 国士无双（13 种幺九字牌齐全 + 其中一种成对）
bool isKokushi(const QVector<int>& counts)
{
    static const int kTerminals[13] = { 0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33 };
    QVector<int> term(mj::KindCount, 0);
    for (int t : kTerminals)
        term[t] = 1;
    int pairs = 0;
    for (int k = 0; k < mj::KindCount; ++k) {
        if (term[k] == 0) {
            if (counts[k] != 0)
                return false;
            continue;
        }
        if (counts[k] == 0)
            return false;
        if (counts[k] >= 2)
            ++pairs;
    }
    return pairs == 1;
}

} // namespace

TableModel::TableModel(QObject* parent)
    : QObject(parent)
{
    reset();
}

void TableModel::reset()
{
    m_hasSeat = false;
    m_mySeat = 0;
    m_dealer = 0;
    m_bakaze = QStringLiteral("E");
    m_kyoku = 1;
    m_honba = 0;
    m_riichiSticks = 0;

    m_hand.clear();
    m_drawn.clear();
    m_drawnSeat = -1;
    // ⚠ **不在这里清上帝手牌**：它是"事件流之外"的数据（回放窗口自己推），而回放每次跳转都是
    //    `reset()` + 从头重放一遍。若在这里清掉，重放到"出牌"那一步时模型里已经没有四家暗牌了
    //    → 出牌动画只能退回"随机挑一格"（真机上就是这么表现的）。保留上一帧那份，
    //    正好就是**出牌前**的手牌 → 动画起点才对得上。
    //   换一场记录时由 `ReplayWindow` 显式 `clearGodHands()`。

    m_discards = QVector<QStringList>(4);
    m_discardSide = QVector<QVector<bool>>(4);
    m_melds = QVector<QVector<Meld>>(4);
    m_scores = QVector<int>(4, 25000);
    m_names = QStringList { lang::t("ui.table.seat_self"), lang::t("ui.table.seat_right"), lang::t("ui.table.seat_opposite"),
                            lang::t("ui.table.seat_left") };
    m_riichi = QVector<bool>(4, false);
    m_furiten = QVector<bool>(4, false);

    m_dora.clear();
    m_tilesLeft = 0;
    m_deadWallLeft = 0;
    m_turn = 0;
    m_phase.clear();

    m_ask = QJsonObject();
    m_askValid = false;
    m_askDeadlineAbs = 0;
    m_askTotalMs = 0;

    m_agari = QJsonObject();
    m_ryuukyoku = QJsonObject();
    m_roundEnd = QJsonObject();
    m_gameEnd = QJsonObject();
    m_error = QJsonObject();
}

void TableModel::setMySeat(int seat)
{
    m_hasSeat = true;
    m_mySeat = qBound(0, seat, 3);
}

QString TableModel::roundText() const
{
    const QString wind = (m_bakaze == QLatin1String("S")) ? lang::t("ui.table.wind_south")
                                                          : lang::t("ui.table.wind_east");
    return lang::t("ui.table.round_text").arg(wind).arg(m_kyoku).arg(m_honba);
}

QString TableModel::seatWind(int seat) const
{
    // 自风是**显示文本**（東/南/西/北），所以走语言文件；
    // 这里存 key、用时再取文案 —— 静态缓存 QString 会跨语言（改语言后不刷新）。
    static const char* const kWindKeys[4] = { "ui.table.wind_east", "ui.table.wind_south",
                                              "ui.table.wind_west", "ui.table.wind_north" };
    const int idx = ((seat - m_dealer) % 4 + 4) % 4;
    return lang::t(QLatin1String(kWindKeys[idx]));
}

int TableModel::concealedCount(int seat) const
{
    if (seat < 0 || seat > 3)
        return 0;
    // 自家优先：手里的牌就是**画出来的那份**（`m_hand`），布局必须跟着它走，
    // 不能让上帝数据插进来 —— 那两份数据一旦有分歧，行宽就与画出来的牌对不上。
    if (seat == m_mySeat && m_hasSeat)
        return m_hand.size() + (m_drawn.isEmpty() ? 0 : 1);
    // 回放「显示他家手牌」：别家以真实暗牌为准 —— **布局与绘制必须同源**，
    // 否则行宽按"张数"算、牌按另一份数据画，就会出现张数不对/牌飞出位置不对。
    if (m_godHas[seat])
        return m_godHand[seat].size() + (m_godDrawn[seat].isEmpty() ? 0 : 1);
    int n = 13 - 3 * int(m_melds.at(seat).size());
    if (m_drawnSeat == seat)
        n += 1;
    return qMax(0, n);
}

void TableModel::clearGodHands()
{
    for (int s = 0; s < 4; ++s) {
        m_godHand[s].clear();
        m_godDrawn[s].clear();
        m_godHas[s] = false;
    }
}

void TableModel::setGodHand(int seat, const QStringList& concealed, const QString& drawn)
{
    if (seat < 0 || seat > 3)
        return;
    m_godHand[seat] = concealed;
    mj::sortTiles(m_godHand[seat]);   // 自动理牌（与自家手牌同一把尺子）
    m_godDrawn[seat] = drawn;
    m_godHas[seat] = true;
}

bool TableModel::hasGodHand(int seat) const
{
    return seat >= 0 && seat <= 3 && m_godHas[seat];
}

QStringList TableModel::godHand(int seat) const
{
    return (seat >= 0 && seat <= 3) ? m_godHand[seat] : QStringList();
}

QString TableModel::godDrawn(int seat) const
{
    return (seat >= 0 && seat <= 3) ? m_godDrawn[seat] : QString();
}

QStringList TableModel::discards(int seat) const
{
    if (seat < 0 || seat > 3)
        return QStringList();
    return m_discards.at(seat);
}

bool TableModel::discardSideways(int seat, int index) const
{
    if (seat < 0 || seat > 3)
        return false;
    const QVector<bool>& v = m_discardSide.at(seat);
    if (index < 0 || index >= v.size())
        return false;
    return v.at(index);
}

QVector<Meld> TableModel::melds(int seat) const
{
    if (seat < 0 || seat > 3)
        return QVector<Meld>();
    return m_melds.at(seat);
}

int TableModel::score(int seat) const
{
    if (seat < 0 || seat > 3)
        return 0;
    return m_scores.at(seat);
}

QString TableModel::playerName(int seat) const
{
    if (seat < 0 || seat > 3)
        return QString();
    return m_names.at(seat);
}

void TableModel::setPlayerName(int seat, const QString& name)
{
    if (seat < 0 || seat > 3)
        return;
    m_names[seat] = name;
}

void TableModel::setScores(const QVector<int>& scores)
{
    for (int i = 0; i < 4 && i < scores.size(); ++i)
        m_scores[i] = scores.at(i);
}

bool TableModel::riichi(int seat) const
{
    if (seat < 0 || seat > 3)
        return false;
    return m_riichi.at(seat);
}

bool TableModel::furiten(int seat) const
{
    if (seat < 0 || seat > 3)
        return false;
    return m_furiten.at(seat);
}

void TableModel::sortHand()
{
    mj::sortTiles(m_hand);   // 与回放「他家手牌」共用同一把尺子（见 mj::sortTiles）
}

bool TableModel::takeFromHand(const QString& tile)
{
    const int kind = mj::kindOfTile(tile);
    if (kind < 0)
        return false;
    const bool red = mj::isRedTile(tile);
    for (int i = 0; i < m_hand.size(); ++i) {
        if (mj::kindOfTile(m_hand.at(i)) == kind && mj::isRedTile(m_hand.at(i)) == red) {
            m_hand.removeAt(i);
            return true;
        }
    }
    for (int i = 0; i < m_hand.size(); ++i) {
        if (mj::kindOfTile(m_hand.at(i)) == kind) {
            m_hand.removeAt(i);
            return true;
        }
    }
    return false;
}

bool TableModel::isWinningForm(const QVector<int>& counts, int meldCount)
{
    const int setsNeeded = 4 - meldCount;
    if (setsNeeded < 0)
        return false;
    if (meldCount == 0) {
        if (isSevenPairs(counts))
            return true;
        if (isKokushi(counts))
            return true;
    }
    QVector<int> work = counts;
    for (int i = 0; i < mj::KindCount; ++i) {
        if (work[i] >= 2) {
            work[i] -= 2;
            const bool ok = canFormSets(work, setsNeeded);
            work[i] += 2;
            if (ok)
                return true;
        }
    }
    return false;
}

QStringList TableModel::waits() const
{
    QStringList result;
    if (!m_hasSeat)
        return result;

    const int meldCount = int(m_melds.at(m_mySeat).size());
    QVector<int> counts = countsOf(m_hand);
    int concealed = m_hand.size();

    // 摸牌后是 14 张：取所有打出一张后的听牌并集
    if (concealed % 3 == 2) {
        QSet<int> tried;
        for (int k = 0; k < mj::KindCount; ++k) {
            if (counts[k] == 0 || tried.contains(k))
                continue;
            tried.insert(k);
            counts[k] -= 1;
            for (int w = 0; w < mj::KindCount; ++w) {
                if (counts[w] >= 4)
                    continue;
                counts[w] += 1;
                const bool win = isWinningForm(counts, meldCount);
                counts[w] -= 1;
                if (win)
                    result << mj::tileString(w, false);
            }
            counts[k] += 1;
        }
    } else if (concealed % 3 == 1) {
        for (int w = 0; w < mj::KindCount; ++w) {
            if (counts[w] >= 4)
                continue;
            counts[w] += 1;
            const bool win = isWinningForm(counts, meldCount);
            counts[w] -= 1;
            if (win)
                result << mj::tileString(w, false);
        }
    }

    result.removeDuplicates();
    std::sort(result.begin(), result.end(), [](const QString& a, const QString& b) {
        return mj::kindOfTile(a) < mj::kindOfTile(b);
    });
    return result;
}

QString TableModel::askKind() const
{
    return m_ask.value(QStringLiteral("kind")).toString();
}

QVector<QJsonObject> TableModel::askOptions() const
{
    return proto::parseAsk(m_ask).options;
}

qint64 TableModel::askRemainMs() const
{
    if (!m_askValid)
        return -1;
    if (m_askDeadlineAbs <= 0)
        return -1;
    return m_askDeadlineAbs - QDateTime::currentMSecsSinceEpoch();
}

qint64 TableModel::askTotalMs() const
{
    return m_askTotalMs;
}

int TableModel::askFrom() const
{
    return m_ask.value(QStringLiteral("from")).toInt(-1);
}

QString TableModel::askTile() const
{
    return m_ask.value(QStringLiteral("tile")).toString();
}

void TableModel::clearAsk()
{
    m_askValid = false;
    m_ask = QJsonObject();
    m_askDeadlineAbs = 0;
    m_askTotalMs = 0;
}

QString TableModel::lastErrorText() const
{
    return m_error.value(QStringLiteral("msg")).toString();
}

void TableModel::syncNamesFromSeats(const QJsonArray& seats)
{
    for (const QJsonValue& v : seats) {
        if (!v.isObject())
            continue;
        const QJsonObject o = v.toObject();
        const int seat = o.value(QStringLiteral("seat")).toInt(-1);
        if (seat < 0 || seat > 3)
            continue;
        const QString name = o.value(QStringLiteral("name")).toString();
        if (!name.isEmpty())
            m_names[seat] = name;
        if (o.contains(QStringLiteral("score")))
            m_scores[seat] = o.value(QStringLiteral("score")).toInt(m_scores.at(seat));
    }
}

void TableModel::applyEvent(const QJsonObject& ev)
{
    const QString name = ev.value(QStringLiteral("ev")).toString();
    if (name.isEmpty())
        return;

    if (name == QLatin1String("hello_ok")) {
        const int pid = ev.value(QStringLiteral("pid")).toInt();
        const QString uname = ev.value(QStringLiteral("name")).toString();
        Q_UNUSED(pid);
        if (!uname.isEmpty())
            m_names[0] = uname;
    } else if (name == QLatin1String("room")) {
        syncNamesFromSeats(ev.value(QStringLiteral("seats")).toArray());
    } else if (name == QLatin1String("game_start")) {
        m_agari = QJsonObject();
        m_ryuukyoku = QJsonObject();
        m_roundEnd = QJsonObject();
        m_gameEnd = QJsonObject();
        syncNamesFromSeats(ev.value(QStringLiteral("seats")).toArray());
        const QJsonObject round = ev.value(QStringLiteral("round")).toObject();
        m_bakaze = round.value(QStringLiteral("bakaze")).toString(m_bakaze);
        m_kyoku = round.value(QStringLiteral("kyoku")).toInt(m_kyoku);
        m_honba = round.value(QStringLiteral("honba")).toInt(m_honba);
    } else if (name == QLatin1String("round_start")) {
        const QJsonObject round = ev.value(QStringLiteral("round")).toObject();
        m_bakaze = round.value(QStringLiteral("bakaze")).toString(m_bakaze);
        m_kyoku = round.value(QStringLiteral("kyoku")).toInt(m_kyoku);
        m_honba = round.value(QStringLiteral("honba")).toInt(m_honba);
        m_riichiSticks = round.value(QStringLiteral("riichi_sticks")).toInt(0);
        m_mySeat = qBound(0, ev.value(QStringLiteral("seat")).toInt(0), 3);
        m_hasSeat = true;
        m_dealer = qBound(0, ev.value(QStringLiteral("dealer")).toInt(m_dealer), 3);
        setScores(proto::intVector(ev.value(QStringLiteral("scores"))));

        // 手牌上限 14（13 张暗牌 + 庄家多发的那张）：超限只可能来自损坏/恶意服务端，
        // 截断 + qWarning（见 proto::stringList(v,n,what)）。
        m_hand = proto::stringList(ev.value(QStringLiteral("hand")), proto::MaxHandTiles,
                                   "round_start.hand");
        m_drawn.clear();
        m_drawnSeat = -1;
        // 庄家 14 张时把最后一张视为刚摸到的牌
        if (m_hand.size() % 3 == 2 && !m_hand.isEmpty()) {
            m_drawn = m_hand.takeLast();
            m_drawnSeat = m_mySeat;
        }
        sortHand();

        m_dora = proto::stringList(ev.value(QStringLiteral("dora_indicators")),
                                   proto::MaxDoraIndicators, "round_start.dora_indicators");
        m_tilesLeft = ev.value(QStringLiteral("tiles_left")).toInt(0);
        m_deadWallLeft = ev.value(QStringLiteral("dead_wall_left")).toInt(0);
        m_turn = m_dealer;
        m_phase = QStringLiteral("wait_discard");

        for (int s = 0; s < 4; ++s) {
            m_discards[s].clear();
            m_discardSide[s].clear();
            m_melds[s].clear();
            m_riichi[s] = false;
            m_furiten[s] = false;
        }
        clearAsk();
    } else if (name == QLatin1String("draw")) {
        const int seat = ev.value(QStringLiteral("seat")).toInt(-1);
        m_tilesLeft = ev.value(QStringLiteral("tiles_left")).toInt(m_tilesLeft);
        // 岭上剩余张数：**每次摸牌都会带**（杠后那张是从王牌摸的，`tiles_left` 看不出它少了没有）。
        // 老服务端没有这个字段时用 `toInt(旧值)` 保持不变，不会把计数打回 0。
        m_deadWallLeft = ev.value(QStringLiteral("dead_wall_left")).toInt(m_deadWallLeft);
        if (seat >= 0 && seat < 4) {
            m_drawnSeat = seat;
            m_turn = seat;
            m_phase = QStringLiteral("wait_discard");
            if (seat == m_mySeat && m_hasSeat) {
                const QString tile = ev.value(QStringLiteral("tile")).toString();
                if (!tile.isEmpty()) {
                    // 暗牌**最多 13 张**，第 14 张永远住在 m_drawn 里。
                    // 判据用「手上已有的张数（含还在摸牌位的那张）≤ 13」：
                    //   · 正常一巡是「手牌 13 张 → 摸第 14 张」，必须照常收下
                    //     （写死 `m_hand.size() < 13` 会把这一张直接吞掉 —— 手牌永远停在 12 张）；
                    //   · 对端连发 draw 时第二次就会被挡掉，暗牌涨不到 14 张以上
                    //     （否则手牌无上限增长：内存涨、每帧绘制也跟着越来越慢）。
                    const int concealed = int(m_hand.size()) + (m_drawn.isEmpty() ? 0 : 1);
                    if (concealed <= 13) {
                        // 摸到的牌单独放在最右，不混入已排序手牌
                        if (!m_drawn.isEmpty())
                            m_hand.append(m_drawn);
                        m_drawn = tile;
                        sortHand();
                    } else {
                        qWarning("mahjong: 暗牌已有 %d 张（含摸牌位，上限 13），忽略这次 draw 摸牌 %s",
                                 concealed, qUtf8Printable(tile));
                    }
                }
            }
        }
    } else if (name == QLatin1String("discard")) {
        const int seat = ev.value(QStringLiteral("seat")).toInt(-1);
        if (seat >= 0 && seat < 4) {
            const QString tile = ev.value(QStringLiteral("tile")).toString();
            const bool isRiichi = ev.value(QStringLiteral("riichi")).toBool(false);
            const bool stick = ev.value(QStringLiteral("riichi_stick")).toBool(false);
            // 横置以服务端下发的 sideways 为准：只有立直宣言牌横置；
            // 宣言牌被鸣走后服务端会把横置顺延到该家下一张打出的牌。
            const bool sideways = ev.contains(QStringLiteral("sideways"))
                    ? ev.value(QStringLiteral("sideways")).toBool(false)
                    : isRiichi;   // 兼容旧服务端
            bool riverRecorded = false;
            if (!tile.isEmpty()) {
                // 牌河也封顶（每人 ≤ 60 张）：正常一局最多十几张，超限只可能是坏报文。
                if (m_discards[seat].size() < proto::MaxDiscardsPerSeat) {
                    m_discards[seat].append(tile);
                    m_discardSide[seat].append(sideways);
                    riverRecorded = true;
                } else {
                    qWarning("mahjong: 座位 %d 的牌河已有 %d 张（上限 %d），忽略这次弃牌",
                             seat, int(m_discards[seat].size()), proto::MaxDiscardsPerSeat);
                }
            }
            const bool tsumogiri = ev.value(QStringLiteral("tsumogiri")).toBool(false);
            const int concealedBefore = m_hand.size() + (m_drawn.isEmpty() ? 0 : 1);
            if (seat == m_mySeat && m_hasSeat) {
                // 以服务端下发的 tsumogiri 为准。绝不能靠「kind 是否等于摸到的牌」去猜：
                // 手里已有 5m 又摸到 5m 而手切原来那张时，猜法会把摸牌当成打出去的，
                // 于是手牌数多出一张（这就是「超时后手牌 +1」的根因）。
                if (tsumogiri && !m_drawn.isEmpty()) {
                    // 摸切：打出的就是刚摸到的那张 —— 摸牌位清空，暗牌原样不动
                    m_drawn.clear();
                } else {
                    // 手切：先从暗牌里扣掉打出的那张……
                    if (!takeFromHand(tile)) {
                        // 兜底：理论上不该发生（服务端下发的 tile 一定在自己手里）
                        qWarning("mahjong: 出牌 %s 不在手牌中，手牌数可能失真", qUtf8Printable(tile));
                    }
                    // ……再把刚摸到的牌**立即并入暗牌**。
                    // 出完牌它就不再是「刚摸到」了：留在摸牌位上会让手牌区空出一个槽，
                    // 也会让人误以为自己还有一张可以打。
                    if (!m_drawn.isEmpty()) {
                        m_hand.append(m_drawn);
                        m_drawn.clear();
                        sortHand();
                    }
                }
                const int concealedAfter = m_hand.size() + (m_drawn.isEmpty() ? 0 : 1);
                if (concealedAfter >= concealedBefore) {
                    // 兜底：无论走哪条分支，自己的暗牌都必须恰好少一张
                    takeFromHand(tile);
                }
            }
            if (isRiichi && !m_riichi.at(seat)) {
                m_riichi[seat] = true;
                m_riichiSticks += 1;
            } else if (stick && m_riichiSticks == 0) {
                m_riichiSticks = 1;
            }
            m_drawnSeat = -1;
            m_turn = (seat + 1) % 4;
            m_phase = QStringLiteral("wait_discard");
            clearAsk();
            if (riverRecorded && !m_silent)
                emit discarded(seat, tile, tsumogiri, m_discards[seat].size() - 1);
        }
    } else if (name == QLatin1String("meld")) {
        const int seat = ev.value(QStringLiteral("seat")).toInt(-1);
        if (seat >= 0 && seat < 4) {
            Meld meld;
            meld.kind = ev.value(QStringLiteral("kind")).toString();
            meld.tiles = proto::stringList(ev.value(QStringLiteral("tiles")), proto::MaxMeldTiles,
                                           "meld.tiles");
            meld.from = ev.value(QStringLiteral("from")).toInt(seat);
            meld.calledTile = ev.value(QStringLiteral("called_tile")).toString();
            const QJsonArray akaArr = ev.value(QStringLiteral("aka")).toArray();
            for (const QJsonValue& a : akaArr)
                meld.aka.append(a.toBool(false));

            if (meld.kind == QLatin1String("kakan")) {
                // 加杠：把已有的碰升级为杠
                const int kind =
                    mj::kindOfTile(meld.tiles.isEmpty() ? meld.calledTile : meld.tiles.first());
                for (int i = 0; i < m_melds[seat].size(); ++i) {
                    Meld& old = m_melds[seat][i];
                    if (old.kind == QLatin1String("pon")
                        && mj::kindOfTile(old.tiles.isEmpty() ? QString() : old.tiles.first())
                               == kind) {
                        old.kind = meld.kind;
                        old.tiles = meld.tiles;
                        old.aka = meld.aka;
                        old.calledTile =
                            meld.calledTile.isEmpty() ? old.calledTile : meld.calledTile;
                        break;
                    }
                }
            } else {
                // 每人最多 4 组副露：超出的只能来自坏报文，别让它无限堆在牌桌上。
                if (m_melds[seat].size() < proto::MaxMeldsPerSeat) {
                    m_melds[seat].append(meld);
                } else {
                    qWarning("mahjong: 座位 %d 已有 %d 组副露（上限 %d），忽略这次 meld",
                             seat, int(m_melds[seat].size()), proto::MaxMeldsPerSeat);
                }
            }

            // 被鸣走的那张要从牌河里「移走」——它是被移动到副露里，不是复制一张
            const int calledIdx = ev.value(QStringLiteral("called_index")).toInt(-1);
            if (calledIdx >= 0 && meld.from != seat && meld.from >= 0 && meld.from < 4
                && calledIdx < m_discards[meld.from].size()) {
                m_discards[meld.from].removeAt(calledIdx);
                m_discardSide[meld.from].removeAt(calledIdx);
                if (!m_silent)
                    emit calledFromRiver(meld.from, calledIdx, seat);
            }

            if (seat == m_mySeat && m_hasSeat) {
                // 从自己的暗牌中扣掉实际拿出去的那几张
                QStringList toRemove = meld.tiles;
                if (meld.from != seat && !meld.calledTile.isEmpty()) {
                    const int idx = toRemove.indexOf(meld.calledTile);
                    if (idx >= 0)
                        toRemove.removeAt(idx);
                    if (toRemove.isEmpty() && !meld.tiles.isEmpty())
                        toRemove = meld.tiles;
                }
                if (meld.kind == QLatin1String("kakan")) {
                    toRemove = QStringList { meld.tiles.isEmpty() ? meld.calledTile
                                                                  : meld.tiles.first() };
                }
                // 若刚摸到的牌正好用于这次副露（暗杠/加杠/大明杠），先扣掉摸牌
                if (!m_drawn.isEmpty() && !toRemove.isEmpty()
                    && mj::kindOfTile(m_drawn) == mj::kindOfTile(toRemove.first())) {
                    m_drawn.clear();
                    toRemove.removeFirst();
                }
                for (const QString& t : toRemove)
                    takeFromHand(t);
            }
            m_drawnSeat = -1;
            m_phase = QStringLiteral("after_meld");
            clearAsk();
        }
    } else if (name == QLatin1String("riichi")) {
        const int seat = ev.value(QStringLiteral("seat")).toInt(-1);
        if (seat >= 0 && seat < 4) {
            m_riichi[seat] = true;
            // 供託总数**以服务端的 `sticks` 为准**（权威值，含上一局留下来的）。
            // 老服务端没有这个字段时，回退到 `stick_index + 1`（服务端保证 stick_index = sticks - 1）。
            const int sticks = ev.value(QStringLiteral("sticks")).toInt(-1);
            if (sticks >= 0) {
                m_riichiSticks = sticks;
            } else {
                const int stickIndex = ev.value(QStringLiteral("stick_index")).toInt(-1);
                if (stickIndex >= 0)
                    m_riichiSticks = qMax(m_riichiSticks, stickIndex + 1);
            }
            // 注意：这里**绝不能**顺手把牌河里最后一张标成横置。
            // 服务端的顺序是「先广播 riichi、再广播 discard」，此刻牌河最后一张
            // 还是宣言牌之前的那张，标记它会导致一个人牌河出现两张横置。
            // 横置一律只认 discard 事件里的 sideways（旧服务端回退到 riichi 字段）。
        }
    } else if (name == QLatin1String("dora_reveal")) {
        m_dora = proto::stringList(ev.value(QStringLiteral("dora_indicators")),
                                   proto::MaxDoraIndicators, "dora_reveal.dora_indicators");
    } else if (name == QLatin1String("ask")) {
        const proto::AskInfo info = proto::parseAsk(ev);
        if (info.valid && (info.seat < 0 || info.seat == m_mySeat)) {
            m_ask = ev;
            m_askValid = true;
            m_askTotalMs = qMax<qint64>(0, info.deadlineMs);
            m_askDeadlineAbs = (info.deadlineMs > 0)
                                   ? QDateTime::currentMSecsSinceEpoch() + info.deadlineMs
                                   : 0;
            if (info.tilesLeft >= 0)
                m_tilesLeft = info.tilesLeft;
            // 振听是**规则判定**，只能信服务端：`win_note` 是服务端对这一询给出的权威原因
            //（`furiten` = 振听所以不能荣和）。客户端绝不自己数弃牌推断（AGENTS §2.1）。
            // 只置位、不因为其它原因（如 `no_yaku`）清位 —— 否则会把服务端的永久振听抹掉。
            if (info.seat == m_mySeat
                && ev.value(QStringLiteral("win_note")).toString() == QLatin1String("furiten"))
                m_furiten[m_mySeat] = true;
        }
    } else if (name == QLatin1String("ask_cancel")) {
        const int seat = ev.value(QStringLiteral("seat")).toInt(-1);
        if (seat < 0 || seat == m_mySeat)
            clearAsk();
    } else if (name == QLatin1String("agari")) {
        m_agari = ev;
        setScores(proto::intVector(ev.value(QStringLiteral("scores_after"))));
        if (ev.contains(QStringLiteral("dora_indicators")))
            m_dora = proto::stringList(ev.value(QStringLiteral("dora_indicators")),
                                       proto::MaxDoraIndicators, "agari.dora_indicators");
        clearAsk();
    } else if (name == QLatin1String("ryuukyoku")) {
        m_ryuukyoku = ev;
        setScores(proto::intVector(ev.value(QStringLiteral("scores_after"))));
        clearAsk();
    } else if (name == QLatin1String("round_end")) {
        m_roundEnd = ev;
        const QJsonObject round = ev.value(QStringLiteral("round")).toObject();
        if (!round.isEmpty()) {
            m_bakaze = round.value(QStringLiteral("bakaze")).toString(m_bakaze);
            m_kyoku = round.value(QStringLiteral("kyoku")).toInt(m_kyoku);
            m_honba = round.value(QStringLiteral("honba")).toInt(m_honba);
            m_riichiSticks = round.value(QStringLiteral("riichi_sticks")).toInt(0);
        }
        setScores(proto::intVector(ev.value(QStringLiteral("scores"))));
        clearAsk();
    } else if (name == QLatin1String("game_end")) {
        m_gameEnd = ev;
        setScores(proto::intVector(ev.value(QStringLiteral("scores"))));
        clearAsk();
    } else if (name == QLatin1String("state")) {
        m_hasSeat = true;
        m_mySeat = qBound(0, ev.value(QStringLiteral("seat")).toInt(0), 3);
        const QJsonObject round = ev.value(QStringLiteral("round")).toObject();
        m_bakaze = round.value(QStringLiteral("bakaze")).toString(m_bakaze);
        m_kyoku = round.value(QStringLiteral("kyoku")).toInt(m_kyoku);
        m_honba = round.value(QStringLiteral("honba")).toInt(m_honba);
        m_riichiSticks = round.value(QStringLiteral("riichi_sticks")).toInt(0);
        if (round.contains(QStringLiteral("dealer")))
            m_dealer = qBound(0, round.value(QStringLiteral("dealer")).toInt(m_dealer), 3);
        setScores(proto::intVector(ev.value(QStringLiteral("scores"))));

        m_hand = proto::stringList(ev.value(QStringLiteral("hand")), proto::MaxHandTiles,
                                   "state.hand");
        m_drawn.clear();
        sortHand();
        m_dora = proto::stringList(ev.value(QStringLiteral("dora_indicators")),
                                   proto::MaxDoraIndicators, "state.dora_indicators");
        m_tilesLeft = ev.value(QStringLiteral("tiles_left")).toInt(0);
        // 重连/中途入座的全量同步也要带上岭上剩余数（服务端 stateFor 里有），
        // 否则重连后界面上的「岭上 N」会退回旧值。
        m_deadWallLeft = ev.value(QStringLiteral("dead_wall_left")).toInt(m_deadWallLeft);
        m_turn = ev.value(QStringLiteral("turn")).toInt(0);
        m_phase = ev.value(QStringLiteral("phase")).toString();

        const QJsonArray melds = ev.value(QStringLiteral("melds")).toArray();
        for (int s = 0; s < 4; ++s) {
            m_melds[s].clear();
            if (s < melds.size() && melds.at(s).isArray()) {
                const QJsonArray arr = melds.at(s).toArray();
                for (const QJsonValue& v : arr) {
                    if (!v.isObject())
                        continue;
                    // 每人最多 4 组副露（重连全量同步同样要封顶）
                    if (m_melds[s].size() >= proto::MaxMeldsPerSeat) {
                        qWarning("mahjong: state.melds 中座位 %d 超过 %d 组，已截断", s,
                                 proto::MaxMeldsPerSeat);
                        break;
                    }
                    const QJsonObject o = v.toObject();
                    Meld m;
                    m.kind = o.value(QStringLiteral("kind")).toString();
                    m.tiles = proto::stringList(o.value(QStringLiteral("tiles")),
                                                proto::MaxMeldTiles, "state.meld.tiles");
                    m.from = o.value(QStringLiteral("from")).toInt(s);
                    m.calledTile = o.value(QStringLiteral("called_tile")).toString();
                    m_melds[s].append(m);
                }
            }
            m_discards[s].clear();
            m_discardSide[s].clear();
        }
        const QJsonArray disc = ev.value(QStringLiteral("discards")).toArray();
        for (int s = 0; s < 4 && s < disc.size(); ++s) {
            m_discards[s] = proto::stringList(disc.at(s), proto::MaxDiscardsPerSeat,
                                              "state.discards");
            m_discardSide[s] = QVector<bool>(m_discards[s].size(), false);
        }
        const QVector<bool> ri = proto::boolVector(ev.value(QStringLiteral("riichi")));
        const QVector<bool> fu = proto::boolVector(ev.value(QStringLiteral("furiten")));
        for (int s = 0; s < 4; ++s) {
            m_riichi[s] = (s < ri.size()) ? ri.at(s) : false;
            m_furiten[s] = (s < fu.size()) ? fu.at(s) : false;
        }
        clearAsk();
    } else if (name == QLatin1String("chat")) {
        emit chatReceived(ev.value(QStringLiteral("seat")).toInt(-1),
                          ev.value(QStringLiteral("name")).toString(),
                          ev.value(QStringLiteral("text")).toString());
    } else if (name == QLatin1String("error")) {
        m_error = ev;
    }

    emit changed();
}
