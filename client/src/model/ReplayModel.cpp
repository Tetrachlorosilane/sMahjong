#include "ReplayModel.h"

#include <QJsonValue>

#include "../i18n/Lang.h"
#include "../net/Protocol.h"

void ReplayModel::reset()
{
    m_hasMeta = false;
    m_id.clear();
    m_names.clear();
    m_rules = QJsonObject();
    m_total = 0;
    m_entries.clear();
    m_rounds.clear();
    m_walls.clear();
    m_roundOf.clear();
    m_turnOf.clear();
    m_turnStart.clear();
    m_godAt = -1;
    m_god = GodState();
}

bool ReplayModel::setMeta(const QJsonObject& meta)
{
    m_id = meta.value(QStringLiteral("id")).toString();
    m_names.clear();
    for (const QJsonValue& v : meta.value(QStringLiteral("names")).toArray()) {
        m_names << v.toString();
    }
    m_rules = meta.value(QStringLiteral("rules")).toObject();
    m_total = meta.value(QStringLiteral("entries")).toInt(0);
    m_hasMeta = !m_id.isEmpty();

    // 每小局的牌山 + 边界。`walls` 与 `round_at` 同长（都是小局数）。
    m_rounds.clear();
    m_walls.clear();
    const QJsonArray walls = meta.value(QStringLiteral("walls")).toArray();
    const QJsonArray roundAt = meta.value(QStringLiteral("round_at")).toArray();
    // 局况（bakaze/kyoku/honba/dealer）在各自的 `replay_round` 条目里，
    // 那一刻才知道 —— 先把占位建好，`build()` 里再填。
    for (int i = 0; i < walls.size(); ++i) {
        Round r;
        r.start = i < roundAt.size() ? roundAt.at(i).toInt() : 0;
        for (const QJsonValue& v : walls.at(i).toArray()) {
            r.wall << v.toInt();
        }
        m_rounds << r;
        m_walls << QVector<WallSlot>(r.wall.size());
    }
    return m_hasMeta;
}

void ReplayModel::addEntries(const QJsonArray& arr)
{
    m_entries.reserve(m_entries.size() + arr.size());
    for (const QJsonValue& v : arr) {
        const QJsonObject o = v.toObject();
        ReplayEntry e;
        e.seq = o.value(QStringLiteral("seq")).toInt();
        e.t = static_cast<qint64>(o.value(QStringLiteral("t")).toDouble());
        e.to = o.value(QStringLiteral("to")).toInt(-1);
        e.body = o.value(QStringLiteral("b")).toObject();
        m_entries << e;
    }
}

void ReplayModel::build()
{
    buildRoundTurn();
    for (int i = 0; i < m_rounds.size(); ++i) {
        buildWall(i);
    }
    m_godAt = -1;
    m_god = GodState();
}

// ---------------------------------------------------------------- 小局 / 巡

void ReplayModel::buildRoundTurn()
{
    const int n = m_entries.size();
    m_roundOf.assign(n, 0);
    m_turnOf.assign(n, 1);
    m_turnStart.clear();
    m_steps.clear();
    m_stepOf.assign(n, 0);

    int round = 0;
    int turn = 1;
    int draws = 0;          // 本小局已发生的**真实**摸牌数（四家各一次 = 一巡）
    bool sawRoundStart = false;
    for (int i = 0; i < n; ++i) {
        const ReplayEntry& e = m_entries.at(i);
        const QString ev = e.ev();
        // ---- 步：把"同一事件的多份按座位副本"折成一步 ----
        // 判据（两条一起）：① 与上一条事件名相同、且两条都是**单发**（to ≥ 0）；
        //   ② 要么是 `round_start`（四家的 body 各不相同：各人手牌），
        //      要么 body 完全一样（`game_start` 这种四份逐字相同的广播式单发）。
        // ⚠ 不能只看"事件名相同"：两次相邻的摸牌（真机记录里不会相邻，但记录格式允许）
        //   会被误折成一步。
        bool newStep = m_steps.isEmpty();
        if (!newStep) {
            const ReplayEntry& prev = m_entries.at(i - 1);
            const bool sameEv = ev == prev.ev();
            const bool bothPrivate = e.to >= 0 && prev.to >= 0;
            const bool identical = sameEv && proto::encodeLine(e.body) == proto::encodeLine(prev.body);
            const bool multiCopy = sameEv && ev == QLatin1String("round_start");
            newStep = !(sameEv && bothPrivate && (identical || multiCopy));
        }
        if (newStep) {
            m_steps << i;
        }
        m_stepOf[i] = m_steps.size() - 1;

        if (ev == QLatin1String("replay_round")) {
            // 小局边界：局况与牌山在这里（也顺便校正 round_at 与条目一致）
            const int idx = e.body.value(QStringLiteral("index")).toInt(round);
            if (idx >= 0 && idx < m_rounds.size()) {
                round = idx;
                Round& r = m_rounds[idx];
                r.start = i;
                r.bakaze = e.body.value(QStringLiteral("bakaze")).toString();
                r.kyoku = e.body.value(QStringLiteral("kyoku")).toInt(1);
                r.honba = e.body.value(QStringLiteral("honba")).toInt(0);
                r.dealer = e.body.value(QStringLiteral("dealer")).toInt(0);
                if (r.wall.isEmpty()) {
                    for (const QJsonValue& v : e.body.value(QStringLiteral("wall")).toArray()) {
                        r.wall << v.toInt();
                    }
                    if (m_walls.size() <= idx) {
                        m_walls.resize(idx + 1);
                    }
                    m_walls[idx] = QVector<WallSlot>(r.wall.size());
                }
            }
            while (m_turnStart.size() <= round) {
                m_turnStart << QVector<int>();
            }
            m_turnStart[round] << i;
            turn = 1;
            draws = 0;
            sawRoundStart = true;
            m_roundOf[i] = round;
            m_turnOf[i] = turn;
            continue;
        }
        // 巡：**每 4 次摸牌进一巡**（含岭上摸牌）。只数带牌面的那份（摸牌者自己的副本），
        // 否则"谁摸了牌"的三份广播副本会把巡目算成四倍。
        if (ev == QLatin1String("draw") && e.body.contains(QStringLiteral("tile"))) {
            if (draws > 0 && draws % 4 == 0) {
                turn++;
                while (m_turnStart.size() <= round) {
                    m_turnStart << QVector<int>();
                }
                m_turnStart[round] << i;
            }
            draws++;
        }
        m_roundOf[i] = sawRoundStart ? round : 0;
        m_turnOf[i] = turn;
    }
    if (m_turnStart.isEmpty()) {
        m_turnStart << QVector<int>();
    }
    if (m_steps.isEmpty()) {
        m_steps << 0;
    }
}

int ReplayModel::stepEntry(int step) const
{
    if (step < 0 || step >= m_steps.size()) {
        return -1;
    }
    return m_steps.at(step);
}

int ReplayModel::stepOfEntry(int entry) const
{
    if (entry < 0 || entry >= m_stepOf.size()) {
        return 0;
    }
    return m_stepOf.at(entry);
}

int ReplayModel::nextStepEntry(int entry) const
{
    const int step = stepOfEntry(entry) + 1;
    const int at = stepEntry(step);
    return at >= 0 ? at : qMax(0, m_entries.size() - 1);
}

int ReplayModel::prevStepEntry(int entry) const
{
    const int step = qMax(0, stepOfEntry(entry) - 1);
    const int at = stepEntry(step);
    return at >= 0 ? at : 0;
}

int ReplayModel::roundOpenEnd(int round) const
{
    const int start = roundStart(round);
    if (start < 0) {
        return -1;
    }
    const int stop = (round + 1 < m_rounds.size()) ? m_rounds.at(round + 1).start : m_entries.size();
    int last = start;
    for (int i = start; i < stop && i < m_entries.size(); ++i) {
        if (m_entries.at(i).ev() == QLatin1String("round_start")) {
            last = i;
        } else if (i > start) {
            break;   // 配牌那几条一定紧跟在边界之后，遇到别的事件就停
        }
    }
    return last;
}

int ReplayModel::roundOf(int index) const
{
    if (index < 0 || index >= m_roundOf.size()) {
        return 0;
    }
    return m_roundOf.at(index);
}

int ReplayModel::turnOf(int index) const
{
    if (index < 0 || index >= m_turnOf.size()) {
        return 1;
    }
    return m_turnOf.at(index);
}

int ReplayModel::maxTurn(int round) const
{
    int best = 1;
    for (int i = 0; i < m_entries.size(); ++i) {
        if (m_roundOf.at(i) == round && m_turnOf.at(i) > best) {
            best = m_turnOf.at(i);
        }
    }
    return best;
}

int ReplayModel::turnStart(int round, int turn) const
{
    if (round < 0 || round >= m_turnStart.size()) {
        return -1;
    }
    const QVector<int>& starts = m_turnStart.at(round);
    const int idx = turn - 1;
    if (idx < 0 || idx >= starts.size()) {
        return -1;
    }
    return starts.at(idx);
}

int ReplayModel::nextTurnStart(int index) const
{
    const int round = roundOf(index);
    const int turn = turnOf(index);
    const int at = turnStart(round, turn + 1);
    if (at >= 0) {
        return at;
    }
    // 本小局没有下一巡 → 给下一个小局的第一条（没有就是末尾）
    const int next = nextRoundStart(index);
    return next >= 0 ? next : qMax(0, m_entries.size() - 1);
}

int ReplayModel::prevTurnStart(int index) const
{
    const int round = roundOf(index);
    const int turn = turnOf(index);
    if (turn > 1) {
        const int at = turnStart(round, turn - 1);
        if (at >= 0) {
            return at;
        }
    }
    const int prev = prevRoundStart(index);
    if (prev >= 0) {
        // 上一个小局的最后一巡
        const int pr = roundOf(prev);
        const int at = turnStart(pr, maxTurn(pr));
        return at >= 0 ? at : prev;
    }
    return 0;
}

int ReplayModel::roundStart(int round) const
{
    if (round < 0 || round >= m_rounds.size()) {
        return -1;
    }
    return m_rounds.at(round).start;
}

int ReplayModel::nextRoundStart(int index) const
{
    const int round = roundOf(index);
    return roundStart(round + 1);
}

int ReplayModel::prevRoundStart(int index) const
{
    const int round = roundOf(index);
    return roundStart(round - 1);
}

QString ReplayModel::roundLabel(int round) const
{
    if (round < 0 || round >= m_rounds.size()) {
        return QString();
    }
    const Round& r = m_rounds.at(round);
    return lang::t(QStringLiteral("ui.replay.round_label")).arg(r.bakaze).arg(r.kyoku);
}

QString ReplayModel::roundText(int round) const
{
    if (round < 0 || round >= m_rounds.size()) {
        return QString();
    }
    const Round& r = m_rounds.at(round);
    return lang::t(QStringLiteral("ui.replay.round_text"))
            .arg(r.bakaze)
            .arg(r.kyoku)
            .arg(r.honba);
}

QString ReplayModel::playerName(int seat) const
{
    if (seat < 0 || seat >= m_names.size()) {
        return lang::t(QStringLiteral("ui.replay.seat_fallback")).arg(seat);
    }
    return m_names.at(seat);
}

// ---------------------------------------------------------------- 牌山归属

void ReplayModel::buildWall(int roundIndex)
{
    if (roundIndex < 0 || roundIndex >= m_walls.size() || roundIndex >= m_rounds.size()) {
        return;
    }
    const Round& r = m_rounds.at(roundIndex);
    if (r.wall.size() != kWallSize) {
        return;
    }
    QVector<WallSlot>& wallSlots = m_walls[roundIndex];
    wallSlots = QVector<WallSlot>(kWallSize);
    int order[4] = {0, 0, 0, 0};

    // ① 配牌：k=0..47 三轮各 4 张；48..51 各补一张；52 庄家第 14 张。
    //    ⚠ 这几张的"被拿走时刻"取**小局边界那一条**（配牌发生在小局开始时）——
    //    不设的话它们永远显示成牌背（真机截图上第一版就是这样：前 12 列全是背面）。
    const int dealAt = r.start;
    for (int k = 0; k < 52; ++k) {
        int s;
        if (k < 48) {
            s = dealSeatOf(k);
        } else {
            s = k - 48;
        }
        const int seat = (r.dealer + s) % 4;
        wallSlots[k].seat = seat;
        wallSlots[k].order = order[seat]++;
        wallSlots[k].takenAt = dealAt;
    }
    {
        const int k = 52;
        wallSlots[k].seat = r.dealer;
        wallSlots[k].order = order[r.dealer]++;
        wallSlots[k].takenAt = dealAt;
    }
    // ② 王牌：122..135（岭上 4 / 表宝牌 5 / 里宝 5）
    for (int k = 122; k < kWallSize; ++k) {
        wallSlots[k].dead = true;
        wallSlots[k].seat = -1;
    }

    // ③ 牌局中的摸牌：`draw` 事件按顺序消费 k=53,54,...；岭上摸牌不占普通牌山
    int next = 53;
    int rinshanTaken = 0;
    const int stop = (roundIndex + 1 < m_rounds.size()) ? m_rounds.at(roundIndex + 1).start
                                                        : m_entries.size();
    for (int i = r.start; i < stop && i < m_entries.size(); ++i) {
        const ReplayEntry& e = m_entries.at(i);
        if (e.ev() != QLatin1String("draw")) {
            continue;
        }
        const bool rinshan = e.body.value(QStringLiteral("rinshan")).toBool(false);
        const int seat = e.seat();
        if (seat < 0) {
            continue;
        }
        if (rinshan) {
            // 岭上：122 + 第 n 张（一发一张，最多 4）
            const int k = 122 + rinshanTaken;
            if (k >= 122 && k < 126) {
                wallSlots[k].takenAt = i;
                wallSlots[k].seat = seat;
                wallSlots[k].order = order[seat]++;
                rinshanTaken++;
            }
            continue;
        }
        if (next < 122) {
            wallSlots[next].takenAt = i;
            wallSlots[next].seat = seat;
            wallSlots[next].order = order[seat]++;
            next++;
        }
    }
    // ④ 开杠把可摸牌山末尾一张移进王牌补位：第 m 次开杠 → 下标 122-m
    int kanCount = 0;
    for (int i = r.start; i < stop && i < m_entries.size(); ++i) {
        const ReplayEntry& e = m_entries.at(i);
        if (e.ev() == QLatin1String("meld")) {
            const QString kind = e.body.value(QStringLiteral("kind")).toString();
            if (kind.endsWith(QLatin1String("kan"))) {
                kanCount++;
                const int k = 122 - kanCount;
                if (k >= 53 && k < 122) {
                    wallSlots[k].movedByKan = true;
                }
            }
        }
    }
}

const QVector<ReplayModel::WallSlot>& ReplayModel::wallSlots(int round) const
{
    static const QVector<WallSlot> empty;
    if (round < 0 || round >= m_walls.size()) {
        return empty;
    }
    return m_walls.at(round);
}

// ---------------------------------------------------------------- 上帝视角

void ReplayModel::buildGod(int index) const
{
    GodState st;
    if (index < 0) {
        index = 0;
    }
    if (index >= m_entries.size()) {
        index = m_entries.size() - 1;
    }
    const int round = roundOf(index);
    st.roundText = roundText(round);
    for (int i = 0; i <= index; ++i) {
        const ReplayEntry& e = m_entries.at(i);
        const QString ev = e.ev();
        if (ev == QLatin1String("replay_round")) {
            for (int s = 0; s < 4; ++s) {
                st.hands[s].clear();
                st.rivers[s].clear();
                st.melds[s].clear();
            }
            continue;
        }
        if (ev == QLatin1String("round_start")) {
            const int seat = e.seat();
            if (seat >= 0 && e.to == seat) {
                st.hands[seat].clear();
                for (const QJsonValue& v : e.body.value(QStringLiteral("hand")).toArray()) {
                    st.hands[seat] << v.toString();
                }
            }
        } else if (ev == QLatin1String("draw")) {
            const int seat = e.seat();
            const QString tile = e.body.value(QStringLiteral("tile")).toString();
            if (seat >= 0 && !tile.isEmpty()) {
                st.hands[seat] << tile;
            }
        } else if (ev == QLatin1String("discard")) {
            const int seat = e.seat();
            const QString tile = e.body.value(QStringLiteral("tile")).toString();
            if (seat >= 0 && !tile.isEmpty()) {
                st.hands[seat].removeOne(tile);
                st.rivers[seat] << tile;
            }
        } else if (ev == QLatin1String("meld")) {
            const int seat = e.seat();
            const QString kind = e.body.value(QStringLiteral("kind")).toString();
            QStringList tiles;
            for (const QJsonValue& v : e.body.value(QStringLiteral("tiles")).toArray()) {
                tiles << v.toString();
            }
            if (seat >= 0) {
                const QString called = e.body.value(QStringLiteral("called_tile")).toString();
                for (const QString& t : tiles) {
                    if ((kind == QLatin1String("chi") || kind == QLatin1String("pon")
                         || kind == QLatin1String("daiminkan"))
                        && t == called) {
                        continue;   // 被鸣的那张不是从手里出的
                    }
                    if (kind == QLatin1String("kakan")) {
                        if (t == called) {
                            st.hands[seat].removeOne(t);
                        }
                        continue;
                    }
                    st.hands[seat].removeOne(t);
                }
                st.melds[seat].append(qMakePair(kind, tiles));
            }
        } else if (ev == QLatin1String("round_end") || ev == QLatin1String("ryuukyoku")
                   || ev == QLatin1String("agari")) {
            // 结算事件里带最新点数，用来显示四家分数
            if (e.body.contains(QStringLiteral("scores"))) {
                st.scores.clear();
                for (const QJsonValue& v : e.body.value(QStringLiteral("scores")).toArray()) {
                    st.scores << v.toInt();
                }
            }
        }
    }
    st.valid = true;
    m_god = st;
    m_godAt = index;
}

const ReplayModel::GodState& ReplayModel::godState(int index) const
{
    if (m_godAt != index || !m_god.valid) {
        buildGod(index);
    }
    return m_god;
}

// ---------------------------------------------------------------- 操作说明

QString ReplayModel::describe(int index) const
{
    if (index < 0 || index >= m_entries.size()) {
        return QString();
    }
    const ReplayEntry& e = m_entries.at(index);
    const QString ev = e.ev();
    const int seat = e.seat();
    const QString who = seat >= 0 ? playerName(seat) : QString();
    const QString tile = e.body.value(QStringLiteral("tile")).toString();
    auto T = [](const char* key) { return lang::t(QString::fromLatin1(key)); };
    if (ev == QLatin1String("replay_round")) {
        return T("ui.replay.op.round")
                .arg(roundText(e.body.value(QStringLiteral("index")).toInt(0)));
    }
    if (ev == QLatin1String("round_start")) {
        return T("ui.replay.op.round_start").arg(who);
    }
    if (ev == QLatin1String("draw")) {
        const QString t = tile.isEmpty() ? T("ui.replay.unknown_tile") : tile;
        return e.body.value(QStringLiteral("rinshan")).toBool()
                       ? T("ui.replay.op.draw_rinshan").arg(who, t)
                       : T("ui.replay.op.draw").arg(who, t);
    }
    if (ev == QLatin1String("discard")) {
        return e.body.value(QStringLiteral("tsumogiri")).toBool()
                       ? T("ui.replay.op.discard_tsumogiri").arg(who, tile)
                       : T("ui.replay.op.discard").arg(who, tile);
    }
    if (ev == QLatin1String("meld")) {
        QStringList meldTiles;
        for (const QJsonValue& v : e.body.value(QStringLiteral("tiles")).toArray()) {
            meldTiles << v.toString();
        }
        return T("ui.replay.op.meld")
                .arg(who, e.body.value(QStringLiteral("kind")).toString(),
                     meldTiles.join(QStringLiteral(" ")));
    }
    if (ev == QLatin1String("riichi")) {
        return T("ui.replay.op.riichi").arg(who);
    }
    if (ev == QLatin1String("agari")) {
        return T("ui.replay.op.agari")
                .arg(playerName(e.body.value(QStringLiteral("winner")).toInt(-1)));
    }
    if (ev == QLatin1String("ryuukyoku")) {
        return T("ui.replay.op.ryuukyoku");
    }
    if (ev == QLatin1String("dora_reveal")) {
        return T("ui.replay.op.dora");
    }
    if (ev == QLatin1String("chat")) {
        return T("ui.replay.op.chat")
                .arg(e.body.value(QStringLiteral("name")).toString(),
                     e.body.value(QStringLiteral("text")).toString());
    }
    if (ev == QLatin1String("round_end")) {
        return T("ui.replay.op.round_end");
    }
    if (ev == QLatin1String("game_end")) {
        return T("ui.replay.op.game_end");
    }
    if (ev == QLatin1String("ask")) {
        return T("ui.replay.op.ask").arg(who);
    }
    if (ev == QLatin1String("round_wait")) {
        return T("ui.replay.op.round_wait");
    }
    return ev;
}
