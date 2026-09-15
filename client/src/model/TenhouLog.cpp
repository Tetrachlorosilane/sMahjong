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
