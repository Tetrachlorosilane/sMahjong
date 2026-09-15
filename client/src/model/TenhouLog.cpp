#include "model/TenhouLog.h"

#include "model/ReplayModel.h"
#include "model/Tile.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QStringList>

namespace {

/** 天鳳牌号 → 我们内部用的牌码（导出时只用反方向，这里留给自检与回读）。 */
const char* const kTileCodes[34] = {
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "1z", "2z", "3z", "4z", "5z", "6z", "7z",
};

/** 场风 → 场局次的基（東=0 南=4 西=8）。 */
int windBase(const QString& bakaze)
{
    if (bakaze == QLatin1String("S")) {
        return 4;
    }
    if (bakaze == QLatin1String("W")) {
        return 8;
    }
    return 0;   // E（以及认不出的都按東算）
}

/** 牌码列表 → 牌号数组（跳过认不出的，并记一笔问题）。 */
QJsonArray toNumbers(const QStringList& tiles, QStringList* problems, const char* where)
{
    QJsonArray arr;
    for (const QString& t : tiles) {
        const int n = TenhouLog::tileNumber(t);
        if (n < 0) {
            if (problems != nullptr && !problems->contains(QLatin1String(where))) {
                problems->append(QLatin1String(where));
            }
            continue;
        }
        arr.append(n);
    }
    return arr;
}

/** 配牌排序：与自家手牌同一把尺子（牌种升序，赤五紧跟普通五）。 */
QStringList sortedHand(QStringList h)
{
    mj::sortTiles(h);
    return h;
}

}   // namespace

int TenhouLog::tileNumber(const QString& code)
{
    if (code.size() != 2) {
        return -1;
    }
    bool red = false;
    const int kind = mj::kindOfTile(code, &red);
    if (kind < 0) {
        return -1;
    }
    if (red) {
        // 赤五：0m/0p/0s → 51/52/53
        const int suit = kind / 9;
        return suit == 0 ? 51 : (suit == 1 ? 52 : 53);
    }
    const int suit = kind / 9;                 // 0=m 1=p 2=s 3=z
    const int num = (suit == 3) ? (kind - 27 + 1) : (kind % 9 + 1);
    return (suit == 0 ? 10 : (suit == 1 ? 20 : (suit == 2 ? 30 : 40))) + num;
}

TenhouLog::Result TenhouLog::build(const ReplayModel& rp)
{
    Result out;
    if (rp.roundCount() == 0) {
        out.problems << QStringLiteral("no_rounds");
        return out;
    }

    QJsonObject root;
    // ⚠ 这两处是**导出文件里的格式常量**（天鳳牌譜的房间名/结果标识），不是界面文案；
    //   天鳳的查看器按日文串识别，所以必须原样保留 —— 用 `// i18n-keep` 就地豁免。
    root.insert(QStringLiteral("title"),
                QJsonArray { QStringLiteral("四人麻雀"),   // i18n-keep 牌谱格式常量
                             QDateTime::currentDateTimeUtc()
                                     .toString(QStringLiteral("ddd, dd MMM yyyy HH:mm:ss 'GMT'")) });
    QJsonArray names;
    for (int s = 0; s < 4; ++s) {
        names.append(rp.playerName(s));
    }
    root.insert(QStringLiteral("name"), names);
    // 规则：赤五各 1 张（我们的默认规则就是 3 张赤五：0m/0p/0s 各一）
    // `disp` 同样是**牌谱格式里的房间名常量**（不是界面文案）：// i18n-keep
    root.insert(QStringLiteral("rule"),
                QJsonObject { { QStringLiteral("disp"), QStringLiteral("四人麻雀") },   // i18n-keep
                              { QStringLiteral("aka51"), 1 },
                              { QStringLiteral("aka52"), 1 },
                              { QStringLiteral("aka53"), 1 } });

    const QVector<ReplayEntry>& entries = rp.entries();
    QJsonArray games;

    for (int round = 0; round < rp.roundCount(); ++round) {
        const int start = rp.roundStart(round);
        const int stop = (round + 1 < rp.roundCount()) ? rp.roundStart(round + 1) : entries.size();
        if (start < 0 || stop <= start) {
            out.problems << QStringLiteral("round_empty");
            continue;
        }

        // 局况来自边界条目（`replay_round`）
        int kyoku = 0;
        int honba = 0;
        QString bakaze = QStringLiteral("E");
        for (int i = start; i < stop; ++i) {
            if (entries.at(i).ev() == QLatin1String("replay_round")) {
                bakaze = entries.at(i).body.value(QStringLiteral("bakaze")).toString(bakaze);
                kyoku = entries.at(i).body.value(QStringLiteral("kyoku")).toInt(1);
                honba = entries.at(i).body.value(QStringLiteral("honba")).toInt(0);
                break;
            }
        }

        QVector<QStringList> hands(4);
        QVector<QStringList> draws(4);
        QVector<QJsonArray> discards(4);
        QStringList dora;
        QStringList ura;
        QVector<int> scores(4, 25000);
        int sticks = 0;
        QJsonArray result;
        bool sawDealerOpening = false;

        for (int i = start; i < stop; ++i) {
            const ReplayEntry& e = entries.at(i);
            const QString ev = e.ev();
            const int seat = e.seat();
            if (ev == QLatin1String("round_start") && seat >= 0 && seat < 4) {
                // 配牌：庄家那条是 14 张，第 14 张留给"第一次摸牌"（天鳳的语义）
                const QJsonArray h = e.body.value(QStringLiteral("hand")).toArray();
                QStringList tiles;
                for (const QJsonValue& v : h) {
                    tiles << v.toString();
                }
                if (scores.isEmpty()) {
                    scores = QVector<int>(4, 25000);
                }
                const QJsonArray sc = e.body.value(QStringLiteral("scores")).toArray();
                if (sc.size() == 4) {
                    for (int s = 0; s < 4; ++s) {
                        scores[s] = sc.at(s).toInt();
                    }
                }
                const QJsonObject rnd = e.body.value(QStringLiteral("round")).toObject();
                sticks = rnd.value(QStringLiteral("riichi_sticks")).toInt(sticks);
                if (dora.isEmpty()) {
                    for (const QJsonValue& v :
                         e.body.value(QStringLiteral("dora_indicators")).toArray()) {
                        dora << v.toString();
                    }
                }
                if (tiles.size() == 14 && !sawDealerOpening) {
                    // 庄家的第 14 张 = 他本次"摸到"的牌
                    const QString extra = tiles.takeLast();
                    hands[seat] = sortedHand(tiles);
                    draws[seat] << extra;
                    sawDealerOpening = true;
                } else {
                    while (tiles.size() > 13) {
                        tiles.removeLast();   // 只在异常记录里才会走到
                    }
                    hands[seat] = sortedHand(tiles);
                }
            } else if (ev == QLatin1String("draw") && seat >= 0 && seat < 4) {
                const QString t = e.body.value(QStringLiteral("tile")).toString();
                if (!t.isEmpty()) {
                    draws[seat] << t;
                }
            } else if (ev == QLatin1String("discard") && seat >= 0 && seat < 4) {
                const QString t = e.body.value(QStringLiteral("tile")).toString();
                const bool tsumogiri = e.body.value(QStringLiteral("tsumogiri")).toBool(false);
                const bool riichi = e.body.value(QStringLiteral("riichi")).toBool(false);
                const int n = tileNumber(t);
                QJsonValue v;
                if (tsumogiri) {
                    v = QJsonValue(QStringLiteral("60"));
                } else if (n >= 0) {
                    v = QJsonValue(n);
                }
                if (riichi) {
                    // 立直宣言牌：`"r60"` / `"r<牌号>"`
                    v = QJsonValue(QStringLiteral("r")
                                   + (tsumogiri ? QStringLiteral("60") : QString::number(n)));
                }
                if (!v.isNull()) {
                    discards[seat].append(v);
                } else {
                    out.problems << QStringLiteral("bad_discard_tile");
                }
            } else if (ev == QLatin1String("dora_reveal")) {
                for (const QJsonValue& v : e.body.value(QStringLiteral("dora_indicators")).toArray()) {
                    const QString t = v.toString();
                    if (!dora.contains(t)) {
                        dora << t;   // 杠后追加的宝牌指示牌
                    }
                }
            } else if (ev == QLatin1String("agari")) {
                if (ura.isEmpty()) {
                    for (const QJsonValue& v : e.body.value(QStringLiteral("ura_indicators")).toArray()) {
                        ura << v.toString();
                    }
                }
                if (result.isEmpty()) {
                    result.append(QStringLiteral("和了"));   // i18n-keep
                    result.append(e.body.value(QStringLiteral("winner")).toInt(-1));
                    result.append(e.body.value(QStringLiteral("from")).toInt(-1));
                }
            } else if (ev == QLatin1String("ryuukyoku")) {
                if (result.isEmpty()) {
                    result.append(QStringLiteral("流局"));   // i18n-keep
                }
            }
        }

        QJsonArray g;
        g.append(QJsonArray { windBase(bakaze) + qMax(0, kyoku - 1), honba, sticks });
        QJsonArray sc;
        for (int s = 0; s < 4; ++s) {
            sc.append(scores.value(s, 25000));
        }
        g.append(sc);
        g.append(toNumbers(dora, &out.problems, "bad_dora"));
        g.append(toNumbers(ura, &out.problems, "bad_ura"));
        for (int s = 0; s < 4; ++s) {
            g.append(toNumbers(hands.at(s), &out.problems, "bad_hand"));
        }
        for (int s = 0; s < 4; ++s) {
            g.append(toNumbers(draws.at(s), &out.problems, "bad_draw"));
        }
        for (int s = 0; s < 4; ++s) {
            g.append(discards.at(s));
        }
        g.append(result);   // [16] 结果（尽力而为，见头文件说明）
        games.append(g);
        ++out.rounds;
    }

    root.insert(QStringLiteral("log"), games);
    out.json = QString::fromUtf8(QJsonDocument(root).toJson(QJsonDocument::Indented));
    out.url = QStringLiteral("https://tenhou.net/6/#json=") + out.json;
    out.ok = out.rounds > 0;
    return out;
}

bool TenhouLog::writeFile(const Result& r, const QString& path, QString* err)
{
    if (!r.ok) {
        if (err != nullptr) {
            *err = QStringLiteral("empty");
        }
        return false;
    }
    const QFileInfo fi(path);
    if (!fi.absoluteDir().exists() && !QDir().mkpath(fi.absolutePath())) {
        if (err != nullptr) {
            *err = QStringLiteral("mkdir_failed");
        }
        return false;
    }
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        if (err != nullptr) {
            *err = QStringLiteral("open_failed");
        }
        return false;
    }
    // 文件内容 = 一行链接（与参考项目一致），末尾补 JSON 便于直接读
    QByteArray out;
    out += "https://tenhou.net/6/#json=";
    out += QJsonDocument::fromJson(r.json.toUtf8()).toJson(QJsonDocument::Compact);
    out += "\n\n";
    out += r.json.toUtf8();
    const bool ok = f.write(out) == out.size();
    f.close();
    if (!ok && err != nullptr) {
        *err = QStringLiteral("write_failed");
    }
    return ok;
}

// ======================= 完整天鳳牌谱（mjlog XML）=======================
//
// 见头文件里那段说明：位域编码按公开实现（mjlog2mjai / mjlog2json / tehai.js）核对过，
// 自检里用**照抄的解码公式**反过来解一遍，确保"我编的位域公开实现能解回来"。

namespace {

/** 牌号分配器：一场之内每个 0..135 的牌号最多用一次（这是 mjlog 的硬要求）。 */
struct IdAlloc
{
    QSet<int> taken;
    QStringList* problems = nullptr;
    int count = 0;

    /** 按牌码分配一个还没用过的牌号；赤五固定用该种类的 0 号副本。 */
    int fresh(const QString& code, const char* where)
    {
        bool red = false;
        const int kind = mj::kindOfTile(code, &red);
        if (kind < 0) {
            if (problems != nullptr && !problems->contains(QLatin1String(where))) {
                problems->append(QLatin1String(where));
            }
            return -1;
        }
        const bool five = (kind == 4 || kind == 13 || kind == 22);
        if (red) {
            const int id = kind * 4;
            if (taken.contains(id)) {
                if (problems != nullptr && !problems->contains(QStringLiteral("dup_red_five"))) {
                    problems->append(QStringLiteral("dup_red_five"));
                }
                return -1;
            }
            taken.insert(id);
            ++count;
            return id;
        }
        for (int c = five ? 1 : 0; c < 4; ++c) {
            const int id = kind * 4 + c;
            if (!taken.contains(id)) {
                taken.insert(id);
                ++count;
                return id;
            }
        }
        if (problems != nullptr && !problems->contains(QLatin1String(where))) {
            problems->append(QLatin1String(where));
        }
        return -1;
    }

    /** 这个牌码要不要用赤五的牌号（"0m"/"0p"/"0s"）。 */
    bool take(int id) { if (taken.contains(id)) return false; taken.insert(id); ++count; return true; }
};

/** 座位 → 标签前缀（摸牌 T/U/V/W，打牌 D/E/F/G）。 */
QChar drawTag(int seat) { return QLatin1Char("TUVW"[seat & 3]); }
QChar discardTag(int seat) { return QLatin1Char("DEFG"[seat & 3]); }

/** 相对座位：被鸣者在鸣牌者的哪一侧（0=上家 1=対面 2=下家 3=自己）。 */
int relSeat(int caller, int from) { return ((from - caller) % 4 + 4) % 4; }

QString xmlEscape(const QString& s)
{
    QString o = s;
    o.replace(QLatin1Char('&'), QLatin1String("&amp;"));
    o.replace(QLatin1Char('<'), QLatin1String("&lt;"));
    o.replace(QLatin1Char('>'), QLatin1String("&gt;"));
    o.replace(QLatin1Char('"'), QLatin1String("&quot;"));
    return o;
}

QString joinIds(const QVector<int>& v)
{
    QStringList s;
    for (int id : v) {
        s << QString::number(id);
    }
    return s.join(QLatin1Char(','));
}

}   // namespace

QVector<int> TenhouLog::decodeMeldForTest(int m, QString* kind, int* dir)
{
    // ⚠ 这段是**照抄**参考实现的解码（mjlog2mjai 的 _parse_shuntsu/_parse_koutsu/_parse_kakan/_parse_kan
    //   与 mjlog2json 的 conv_meld_from_u16），只用于自检 —— 编解码两头都自己写就等于没验证。
    QVector<int> out;
    const int d = m & 0x3;
    if (dir != nullptr) {
        *dir = d;
    }
    if (m & 0x4) {                       // 吃
        if (kind != nullptr) *kind = QStringLiteral("chi");
        const int pattern = (m & 0xfc00) >> 10;
        const int calledPos = pattern % 3;
        const int minNum = (pattern / 3) % 7;
        const int suit = (pattern / 3) / 7;
        const int base = (suit * 9 + minNum) * 4;
        int h[3] = { base + ((m & 0x0018) >> 3), base + 4 + ((m & 0x0060) >> 5),
                     base + 8 + ((m & 0x0180) >> 7) };
        if (calledPos == 1) { const int t = h[0]; h[0] = h[1]; h[1] = t; }
        else if (calledPos == 2) { const int t = h[2]; h[0] = h[2]; h[2] = h[1]; h[1] = t; }
        out << h[0] << h[1] << h[2];
        return out;
    }
    if (m & 0x08 || m & 0x10) {          // 碰 / 加杠
        if (kind != nullptr) *kind = (m & 0x10) ? QStringLiteral("kakan") : QStringLiteral("pon");
        const int pattern = (m & 0xfe00) >> 9;
        const int unused = (m & 0x60) >> 5;
        const int calledIndex = pattern % 3;
        const int base = (pattern / 3) * 4;
        int h[4] = { base, base + 1, base + 2, base + 3 };
        const int tmp = h[3]; h[3] = h[unused]; h[unused] = tmp;
        const int called = h[calledIndex];
        if (m & 0x10) {
            out << h[3] << called << h[0] << h[1] << h[2];   // 加杠：先给加的那张
        } else {
            out << called << h[0] << h[1] << h[2];
        }
        return out;
    }
    if (m & 0x20) {                      // 拔北（本项目不产生）
        if (kind != nullptr) *kind = QStringLiteral("nuki");
        out << (m >> 8);
        return out;
    }
    // 杠：`m&0x3 == 0` = 暗杠，否则大明杠
    const int hai = (m & 0xff00) >> 8;
    const bool ankan = (d == 0);
    if (kind != nullptr) *kind = ankan ? QStringLiteral("ankan") : QStringLiteral("daiminkan");
    const int t = (hai / 4) * 4;
    out << t << t + 1 << t + 2 << t + 3;
    return out;
}

TenhouLog::MjlogResult TenhouLog::buildMjlog(const ReplayModel& rp)
{
    MjlogResult out;
    if (rp.roundCount() == 0) {
        out.problems << QStringLiteral("no_rounds");
        return out;
    }

    IdAlloc alloc;
    alloc.problems = &out.problems;

    QStringList lines;
    lines << QStringLiteral("<mjloggm ver=\"2.3\">");
    // 玩家名（用户数据，允许非 ASCII）。不写 SHUFFLE/GO：见头文件说明。
    {
        QString un = QStringLiteral("<UN");
        for (int s = 0; s < 4; ++s) {
            un += QStringLiteral(" n%1=\"%2\"").arg(s).arg(xmlEscape(rp.playerName(s)));
        }
        lines << un + QStringLiteral("/>");
    }
    lines << QStringLiteral("<TAIKYOKU oya=\"0\"/>");

    const QVector<ReplayEntry>& entries = rp.entries();
    // 座位 → 刚摸到的牌号（摸切要复用它）/ 牌河牌号（鸣牌要把被鸣那张从河里取出来）
    QVector<int> lastDraw(4, -1);
    QVector<QVector<int>> river(4);
    // ⚠ 必须给足 4 个元素：ponCalled[seat] 落在空 QVector 上就是越界写 → 直接 SIGSEGV
    QVector<QHash<int, int>> ponCalled(4);   // 座位 → (种类 → 碰时被鸣的牌号)
    QVector<int> roundDoraIds;               // 本小局宝牌指示牌的**牌号**（AGARI 复用同一批）

    for (int round = 0; round < rp.roundCount(); ++round) {
        const int start = rp.roundStart(round);
        const int stop = (round + 1 < rp.roundCount()) ? rp.roundStart(round + 1) : entries.size();
        if (start < 0 || stop <= start) {
            out.problems << QStringLiteral("round_empty");
            continue;
        }
        for (int s = 0; s < 4; ++s) {
            river[s].clear();
            ponCalled[s].clear();
            lastDraw[s] = -1;
        }

        // 局况（边界条目）
        int kyoku = 1, honba = 0, sticks = 0;
        QString bakaze = QStringLiteral("E");
        QStringList doraCodes;
        for (int i = start; i < stop; ++i) {
            if (entries.at(i).ev() == QLatin1String("replay_round")) {
                const QJsonObject b = entries.at(i).body;
                bakaze = b.value(QStringLiteral("bakaze")).toString(bakaze);
                kyoku = b.value(QStringLiteral("kyoku")).toInt(1);
                honba = b.value(QStringLiteral("honba")).toInt(0);
                break;
            }
        }

        // 配牌：庄家 14 张里的第 14 张算他"第一次摸牌"
        QVector<QVector<int>> hands(4);
        int dealer = 0;
        int dealerOpening = -1;
        QStringList initDora;
        QVector<int> scores(4, 25000);
        for (int i = start; i < stop; ++i) {
            const ReplayEntry& e = entries.at(i);
            if (e.ev() != QLatin1String("round_start") || e.seat() < 0) {
                continue;
            }
            const int s = e.seat();
            QStringList tiles;
            for (const QJsonValue& v : e.body.value(QStringLiteral("hand")).toArray()) {
                tiles << v.toString();
            }
            const int seatDealer = e.body.value(QStringLiteral("dealer")).toInt(0);
            if (s == 0) {
                dealer = seatDealer;
                const QJsonArray sc = e.body.value(QStringLiteral("scores")).toArray();
                if (sc.size() == 4) {
                    for (int k = 0; k < 4; ++k) {
                        scores[k] = sc.at(k).toInt(scores.at(k));
                    }
                }
                const QJsonObject rnd = e.body.value(QStringLiteral("round")).toObject();
                sticks = rnd.value(QStringLiteral("riichi_sticks")).toInt(0);
                for (const QJsonValue& v :
                     e.body.value(QStringLiteral("dora_indicators")).toArray()) {
                    initDora << v.toString();
                }
            }
            const bool hasOpening = (tiles.size() == 14);
            if (hasOpening) {
                const QString extra = tiles.takeLast();
                dealerOpening = alloc.fresh(extra, "bad_tile_code");
                lastDraw[s] = dealerOpening;
            }
            for (const QString& t : tiles) {
                const int id = alloc.fresh(t, "bad_tile_code");
                if (id >= 0) {
                    hands[s] << id;
                }
            }
        }

        // INIT：seed = 场局次, 本场, 供託, 骰子1, 骰子2, 表宝牌指示牌（牌号）
        const int windBase = (bakaze == QLatin1String("S")) ? 4
                : ((bakaze == QLatin1String("W")) ? 8 : 0);
        roundDoraIds.clear();
        const int doraId = alloc.fresh(initDora.value(0), "bad_dora");
        if (doraId >= 0) {
            roundDoraIds << doraId;
        }
        {
            QString init = QStringLiteral("<INIT seed=\"%1,%2,%3,0,0,%4\" ten=\"%5\" oya=\"%6\"")
                                   .arg(windBase + qMax(0, kyoku - 1))
                                   .arg(honba)
                                   .arg(sticks)
                                   .arg(doraId)
                                   .arg(QStringList { QString::number(scores.at(0) / 100),
                                                      QString::number(scores.at(1) / 100),
                                                      QString::number(scores.at(2) / 100),
                                                      QString::number(scores.at(3) / 100) }
                                                .join(QLatin1Char(',')))
                                   .arg(dealer);
            for (int s = 0; s < 4; ++s) {
                init += QStringLiteral(" hai%1=\"%2\"").arg(s).arg(joinIds(hands.at(s)));
            }
            lines << init + QStringLiteral("/>");
        }
        // 庄家的第 14 张 = 他本次摸到的那张
        if (dealerOpening >= 0) {
            lines << QStringLiteral("<%1%2/>").arg(drawTag(dealer)).arg(dealerOpening);
        }


        for (int i = start; i < stop; ++i) {
            const ReplayEntry& e = entries.at(i);
            const QString ev = e.ev();
            const int seat = e.seat();
            if (ev == QLatin1String("replay_round") || ev == QLatin1String("round_start")) {
                continue;
            }
            if (ev == QLatin1String("draw") && seat >= 0) {
                const int id = alloc.fresh(e.body.value(QStringLiteral("tile")).toString(),
                                           "bad_tile_code");
                if (id >= 0) {
                    lastDraw[seat] = id;
                    lines << QStringLiteral("<%1%2/>").arg(drawTag(seat)).arg(id);
                }
            } else if (ev == QLatin1String("discard") && seat >= 0) {
                const bool tsumogiri = e.body.value(QStringLiteral("tsumogiri")).toBool(false);
                const bool riichi = e.body.value(QStringLiteral("riichi")).toBool(false);
                int id = -1;
                if (tsumogiri) {
                    // ⚠ **摸切必须复用刚摸到的那张牌号** —— 公开实现就是靠这个判断摸切的
                    id = lastDraw[seat];
                    if (id < 0) {
                        id = alloc.fresh(e.body.value(QStringLiteral("tile")).toString(),
                                         "tsumogiri_without_draw");
                    }
                } else {
                    id = alloc.fresh(e.body.value(QStringLiteral("tile")).toString(),
                                     "bad_tile_code");
                }
                if (id < 0) {
                    continue;
                }
                if (riichi) {
                    lines << QStringLiteral("<REACH who=\"%1\" step=\"1\"/>").arg(seat);
                }
                lines << QStringLiteral("<%1%2/>").arg(discardTag(seat)).arg(id);
                if (riichi) {
                    // step=2 的 ten 是可选的（老牌谱就没有），这里不写
                    lines << QStringLiteral("<REACH who=\"%1\" step=\"2\"/>").arg(seat);
                }
                river[seat].append(id);
                lastDraw[seat] = -1;
            } else if (ev == QLatin1String("meld") && seat >= 0) {
                const QString kind = e.body.value(QStringLiteral("kind")).toString();
                QStringList codes;
                for (const QJsonValue& v : e.body.value(QStringLiteral("tiles")).toArray()) {
                    codes << v.toString();
                }
                const int from = e.body.value(QStringLiteral("from")).toInt(seat);
                const int calledIdx = e.body.value(QStringLiteral("called_index")).toInt(-1);
                // 被鸣的那张：优先取"打牌者牌河里那一个位置的牌号"（那是唯一正确的来源）
                int calledId = -1;
                if (from >= 0 && from < 4 && calledIdx >= 0 && calledIdx < river[from].size()
                    && calledIdx != seat) {
                    calledId = river[from].at(calledIdx);
                    river[from].removeAt(calledIdx);
                }
                bool red = false;
                int calledKind = -1;
                if (calledId >= 0) {
                    calledKind = calledId / 4;
                } else if (!codes.isEmpty()) {
                    calledKind = mj::kindOfTile(codes.first(), &red);
                }
                // 其余几张从手里拿（分配新牌号）
                QVector<int> ids;
                bool usedCalled = false;
                bool usedDrawn = false;
                for (const QString& c : codes) {
                    bool r2 = false;
                    const int k = mj::kindOfTile(c, &r2);
                    if (!usedCalled && calledId >= 0 && k == calledKind) {
                        ids << calledId;      // 被鸣那张（含赤五）就用河里那张的牌号
                        usedCalled = true;
                    } else if (!usedDrawn && lastDraw[seat] >= 0 && k == lastDraw[seat] / 4) {
                        // ⚠ 刚摸到的那张常常就是鸣进去的那张（碰/杠的常见来源）——
                        //   直接**复用它的牌号**；不然同一个碰会把 4 张同种的牌号要出第 5 张，
                        //   最后一张分配不到（实测就踩在这里）
                        ids << lastDraw[seat];
                        usedDrawn = true;
                    } else {
                        ids << alloc.fresh(c, "bad_tile_code");
                    }
                }
                if (calledId < 0 && !ids.isEmpty()) {
                    calledId = ids.first();
                    usedCalled = true;
                }
                const int dir = (kind == QLatin1String("ankan")) ? 3 : relSeat(seat, from);
                int m = dir & 0x3;
                if (kind == QLatin1String("chi") && ids.size() >= 3) {
                    QVector<int> sorted = ids;
                    std::sort(sorted.begin(), sorted.end());
                    const int base = sorted.first() / 4;
                    const int suit = base / 9;
                    const int num0 = base % 9;
                    int calledPos = 0;
                    for (int k = 0; k < 3; ++k) {
                        if (sorted.at(k) == calledId) {
                            calledPos = k;
                        }
                    }
                    const int pattern = (suit * 7 + num0) * 3 + calledPos;
                    m |= 0x4 | (pattern << 10)
                            | (((sorted.at(0) % 4) & 0x3) << 3)
                            | (((sorted.at(1) % 4) & 0x3) << 5)
                            | (((sorted.at(2) % 4) & 0x3) << 7);
                } else if ((kind == QLatin1String("pon") || kind == QLatin1String("kakan"))
                           && ids.size() >= 3) {
                    const int base = (calledKind >= 0 ? calledKind : ids.first() / 4);
                    QVector<int> used;
                    int addedId = -1;
                    for (int id : ids) {
                        if (kind == QLatin1String("kakan") && lastDraw[seat] >= 0
                            && id == lastDraw[seat]) {
                            addedId = id;   // 加杠：刚摸到的那张就是加的那张（最常见也最可判）
                        } else {
                            used << id;
                        }
                    }
                    if (kind == QLatin1String("kakan")) {
                        if (addedId < 0 && ids.size() >= 4) {
                            addedId = ids.last();
                            if (!used.isEmpty() && used.last() == addedId) {
                                used.removeLast();
                            }
                        }
                        if (addedId < 0) {
                            addedId = base * 4 + 3;
                        }
                    }
                    std::sort(used.begin(), used.end());
                    int calledIndex = 0;
                    for (int k = 0; k < used.size(); ++k) {
                        if (used.at(k) == calledId) {
                            calledIndex = k;
                        }
                    }
                    int missing = 3;
                    for (int c = 0; c < 4; ++c) {
                        if (!used.contains(base * 4 + c)) {
                            missing = c;
                            break;
                        }
                    }
                    const int pattern = base * 3 + qBound(0, calledIndex, 2);
                    if (kind == QLatin1String("kakan")) {
                        const int addedCopy = qBound(0, addedId - base * 4, 3);
                        m |= 0x10 | (pattern << 9) | (addedCopy << 5);
                    } else {
                        m |= 0x08 | (pattern << 9) | (missing << 5);
                        ponCalled[seat].insert(base, calledId);
                    }
                } else if (kind.endsWith(QLatin1String("kan"))) {
                    int rep = calledId;
                    if (rep < 0) {
                        rep = ids.isEmpty() ? (calledKind >= 0 ? calledKind * 4 + 3 : 0)
                                            : ids.first();
                    }
                    if (kind == QLatin1String("ankan")) {
                        m = 0 | (rep << 8);          // 暗杠：相对座位 0 = 自己
                    } else {
                        m = (dir & 0x3) | (rep << 8);
                    }
                }
                lines << QStringLiteral("<N who=\"%1\" m=\"%2\"/>").arg(seat).arg(m);
                lastDraw[seat] = -1;
            } else if (ev == QLatin1String("dora_reveal")) {
                for (const QJsonValue& v : e.body.value(QStringLiteral("dora_indicators")).toArray()) {
                    const int id = alloc.fresh(v.toString(), "bad_dora");
                    if (id >= 0) {
                        roundDoraIds << id;
                        lines << QStringLiteral("<DORA hai=\"%1\"/>").arg(id);
                    }
                }
            } else if (ev == QLatin1String("agari")) {
                QVector<int> hai;
                for (const QJsonValue& v : e.body.value(QStringLiteral("hand")).toArray()) {
                    const int id = alloc.fresh(v.toString(), "bad_tile_code");
                    if (id >= 0) {
                        hai << id;
                    }
                }
                const int winTileId = alloc.fresh(
                        e.body.value(QStringLiteral("winning_tile")).toString(), "bad_tile_code");
                // ⚠ 天鳳这里要的是**牌号**（0..135），不是两位数牌码；宝牌指示牌还必须与本小局
                //   INIT/DORA 里那几个是**同一个牌号**（同一张牌不能在牌谱里出现两次）
                QVector<int> doraIds = roundDoraIds;
                QVector<int> uraIds;
                for (const QJsonValue& v :
                     e.body.value(QStringLiteral("ura_indicators")).toArray()) {
                    const int id = alloc.fresh(v.toString(), "bad_ura");
                    if (id >= 0) {
                        uraIds << id;
                    }
                }
                // ⚠ 和了家/放銃家在**事件体**里（`winner` / `from`），不能拿 entry 的 to ——
                //   录下来的 agari 条目 to 往往是 -1，直接写出去就是 who="-1"
                const int who = e.body.value(QStringLiteral("winner")).toInt(seat);
                QString ag = QStringLiteral("<AGARI who=\"%1\" fromWho=\"%2\" hai=\"%3\" "
                                            "machi=\"%4\"")
                                    .arg(who)
                                    .arg(e.body.value(QStringLiteral("from")).toInt(who))
                                    .arg(joinIds(hai))
                                    .arg(winTileId);
                if (!doraIds.isEmpty()) {
                    ag += QStringLiteral(" doraHai=\"%1\"").arg(joinIds(doraIds));
                }
                if (!uraIds.isEmpty()) {
                    ag += QStringLiteral(" doraHaiUra=\"%1\"").arg(joinIds(uraIds));
                }
                ag += QStringLiteral(" ba=\"%1,%2\"/>").arg(honba).arg(sticks);
                lines << ag;
            } else if (ev == QLatin1String("ryuukyoku")) {
                // type 是天鳳的**日文串**（查看器按它显示流局原因）→ 协议里是 ASCII 码
                const QString reason = e.body.value(QStringLiteral("reason")).toString();
                QString type = QStringLiteral("流局") /* i18n-keep 牌谱格式常量 */;
                if (reason == QLatin1String("nine_terms")) {
                    type = QStringLiteral("九種九牌") /* i18n-keep 牌谱格式常量 */;
                } else if (reason == QLatin1String("four_winds")) {
                    type = QStringLiteral("四風連打") /* i18n-keep 牌谱格式常量 */;
                } else if (reason == QLatin1String("four_kans")) {
                    type = QStringLiteral("四槓散了") /* i18n-keep 牌谱格式常量 */;
                } else if (reason == QLatin1String("four_riichi")) {
                    type = QStringLiteral("四家立直") /* i18n-keep 牌谱格式常量 */;
                } else if (reason == QLatin1String("triple_ron")) {
                    type = QStringLiteral("三家和了") /* i18n-keep 牌谱格式常量 */;
                } else if (reason == QLatin1String("nagashi")) {
                    type = QStringLiteral("流し満貫") /* i18n-keep 牌谱格式常量 */;
                }
                QString ry = QStringLiteral("<RYUUKYOKU ba=\"%1,%2\" type=\"%3\"")
                                     .arg(honba)
                                     .arg(sticks)
                                     .arg(type);
                // 聴牌家的手牌（天鳳只写聴牌家）
                for (const QJsonValue& v : e.body.value(QStringLiteral("tenpai")).toArray()) {
                    (void)v;
                }
                const QJsonArray tp = e.body.value(QStringLiteral("tenpai")).toArray();
                for (const QJsonValue& v : tp) {
                    const int s = v.toInt(-1);
                    if (s >= 0 && s < 4) {
                        ry += QStringLiteral(" hai%1=\"%2\"").arg(s).arg(joinIds(hands.at(s)));
                    }
                }
                lines << ry + QStringLiteral("/>");
            }
        }

        ++out.rounds;
    }

    lines << QStringLiteral("</mjloggm>");
    out.xml = lines.join(QLatin1Char('\n')) + QLatin1Char('\n');
    out.tiles = alloc.count;
    out.ok = out.rounds > 0;
    return out;
}

bool TenhouLog::writeMjlogFile(const MjlogResult& r, const QString& path, QString* err)
{
    if (!r.ok) {
        if (err != nullptr) {
            *err = QStringLiteral("empty");
        }
        return false;
    }
    const QFileInfo fi(path);
    if (!fi.absoluteDir().exists() && !QDir().mkpath(fi.absolutePath())) {
        if (err != nullptr) {
            *err = QStringLiteral("mkdir_failed");
        }
        return false;
    }
    QFile f(path);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        if (err != nullptr) {
            *err = QStringLiteral("open_failed");
        }
        return false;
    }
    QByteArray out = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n";
    out += r.xml.toUtf8();
    const bool ok = f.write(out) == out.size();
    f.close();
    if (!ok && err != nullptr) {
        *err = QStringLiteral("write_failed");
    }
    return ok;
}
