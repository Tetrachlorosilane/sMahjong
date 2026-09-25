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

/** 牌码 → 天鳳的**两位数字**（"1m"→"11" … "0s"→"53"）；认不出的返回空串。 */
QString digits(const QString& code)
{
    const int n = TenhouLog::tileNumber(code);
    if (n < 0) {
        return QString();
    }
    return QStringLiteral("%1").arg(n, 2, 10, QLatin1Char('0'));
}

/** 相对座位：被鸣者在鸣牌者的哪一侧（0=上家 1=対面 2=下家 3=自己）。 */
int relSeat(int caller, int from)
{
    return ((from - caller) % 4 + 4) % 4;
}

/**
 * 鸣牌串里关键字**插在第几格之前** —— 只由来源决定（判据照抄参考实现的 `take_action_to_events`）：
 * 上家 = 0、対面 = 2、下家 = 碰/加杠 4、大明杠 6（换算成"第几格之前"就是 0 / 1 / 2 或 3）。
 */
int nakiKeyAt(QChar key, int rel)
{
    if (rel == 3) {
        return 0;                                             // 上家
    }
    if (rel == 2) {
        return 1;                                             // 対面
    }
    if (rel == 1) {
        return (key == QLatin1Char('m')) ? 3 : 2;             // 下家：大明杠的 m 在 [6]
    }
    return 0;   // 自己（正常只有暗杠；暗杠的调用处显式给 3）
}

/**
 * 组装鸣牌串：**关键字后面紧挨着的那张就是被鸣的牌**（参考实现就是这么解的）。
 * @param key      关键字（c/p/m/k/a）
 * @param tiles    各格子的两位牌码（吃/碰 3 张、杠 4 张）
 * @param calledIdx 被鸣那张在 `tiles` 里的下标（关键字插在它前面）
 */
QString nakiString(QChar key, const QStringList& tiles, int calledIdx)
{
    QString s;
    for (int i = 0; i < tiles.size(); ++i) {
        if (i == calledIdx) {
            s += key;
        }
        s += tiles.at(i);
    }
    return s;
}

/** `n` 张同种牌（碰/杠的四张必然同种）。 */
QStringList sameTile(const QString& dig, int n)
{
    QStringList l;
    for (int i = 0; i < n; ++i) {
        l << dig;
    }
    return l;
}

/**
 * 流局原因码（协议里是 ASCII）→ 天鳳牌譜里的**状态字**（查看器/解析器按它显示）。
 * `results[0]` 只要不是魔法串 `和了` 就一律按流局处理，所以途中流局写状态字即可。
 */
QString drawStatusText(const QString& reason)
{
    if (reason == QLatin1String("nine_terms")) {
        return QStringLiteral("九種九牌");       // i18n-keep 牌谱格式常量
    }
    if (reason == QLatin1String("four_winds")) {
        return QStringLiteral("四風連打");       // i18n-keep 牌谱格式常量
    }
    if (reason == QLatin1String("four_kans")) {
        return QStringLiteral("四槓散了");       // i18n-keep 牌谱格式常量
    }
    if (reason == QLatin1String("four_riichi")) {
        return QStringLiteral("四家立直");       // i18n-keep 牌谱格式常量
    }
    if (reason == QLatin1String("triple_ron")) {
        return QStringLiteral("三家和了");       // i18n-keep 牌谱格式常量
    }
    if (reason == QLatin1String("nagashi")) {
        return QStringLiteral("流し満貫");       // i18n-keep 牌谱格式常量
    }
    return QStringLiteral("流局");               // i18n-keep 牌谱格式常量
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

    // 规则取自回放头：`length` 判 東風/半庄、`aka` 决定赤五声明的张数（0 或 3）。
    const QJsonObject rules = rp.rules();
    const bool tonpuu = rules.value(QStringLiteral("length")).toString() == QLatin1String("tonpuu");
    const int akaPerSuit = (rules.value(QStringLiteral("aka")).toInt(0) >= 3) ? 1 : 0;

    QJsonObject root;
    // ⚠ 下面这些是**导出文件里的格式常量**（天鳳牌譜的房间名/结果标识），不是界面文案；
    //   解析器与查看器按日文串识别，所以必须原样保留 —— 用 `// i18n-keep` 就地豁免。
    root.insert(QStringLiteral("title"),
                QJsonArray { QStringLiteral("四人麻雀"),   // i18n-keep 牌谱格式常量
                             QDateTime::currentDateTimeUtc()
                                     .toString(QStringLiteral("ddd, dd MMM yyyy HH:mm:ss 'GMT'")) });
    QJsonArray names;
    for (int s = 0; s < 4; ++s) {
        names.append(rp.playerName(s));
    }
    root.insert(QStringLiteral("name"), names);
    // `disp` 决定参考实现怎么判局制：含「東」= 東風戦（只支持半庄的引擎会**明确拒绝**，
    // 好过假装半庄再按错的期望值算）。`aka` 是三麻用的字段，四麻写 aka51/52/53。
    root.insert(QStringLiteral("rule"),
                QJsonObject { { QStringLiteral("disp"),
                                // 解析器按这个日文串判三麻/東風戦 —— 牌谱格式常量，不是界面文案
                                tonpuu ? QStringLiteral("般東喰赤")      // i18n-keep
                                       : QStringLiteral("般南喰赤") },    // i18n-keep
                              { QStringLiteral("aka"), 0 },
                              { QStringLiteral("aka51"), akaPerSuit },
                              { QStringLiteral("aka52"), akaPerSuit },
                              { QStringLiteral("aka53"), akaPerSuit } });

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
        int kyoku = 1;
        int honba = 0;
        int sticks = 0;
        QString bakaze = QStringLiteral("E");
        for (int i = start; i < stop; ++i) {
            if (entries.at(i).ev() == QLatin1String("replay_round")) {
                const QJsonObject b = entries.at(i).body;
                bakaze = b.value(QStringLiteral("bakaze")).toString(bakaze);
                kyoku = b.value(QStringLiteral("kyoku")).toInt(1);
                honba = b.value(QStringLiteral("honba")).toInt(0);
                break;
            }
        }

        QVector<QStringList> hands(4);
        QVector<QJsonArray> takes(4);         // 取：摸牌（数字）/ 鸣牌（字符串）
        QVector<QJsonArray> discards(4);      // 出：手切（数字）/ 摸切（**数字** 60）/ 立直 "r…" / 杠（字符串）/ 大明杠空位（数字 0）
        QVector<QStringList> river(4);        // 牌河牌码：鸣牌要从这里取"被鸣那张"（赤五与普通五是两套码）
        QStringList doraCodes;
        QStringList uraCodes;
        QVector<int> scores(4, 25000);
        QJsonArray result;
        bool hora = false;
        bool openingUsed = false;
        int kans = 0;

        for (int i = start; i < stop; ++i) {
            const ReplayEntry& e = entries.at(i);
            const QString ev = e.ev();
            const int seat = e.seat();
            if (ev == QLatin1String("round_start") && seat >= 0 && seat < 4) {
                QStringList tiles;
                for (const QJsonValue& v : e.body.value(QStringLiteral("hand")).toArray()) {
                    tiles << v.toString();
                }
                const QJsonArray sc = e.body.value(QStringLiteral("scores")).toArray();
                if (sc.size() == 4) {
                    for (int k = 0; k < 4; ++k) {
                        scores[k] = sc.at(k).toInt(scores.at(k));
                    }
                }
                sticks = e.body.value(QStringLiteral("round")).toObject()
                                 .value(QStringLiteral("riichi_sticks")).toInt(sticks);
                if (doraCodes.isEmpty()) {
                    for (const QJsonValue& v :
                         e.body.value(QStringLiteral("dora_indicators")).toArray()) {
                        doraCodes << v.toString();
                    }
                }
                if (tiles.size() == 14 && !openingUsed) {
                    // 庄家的第 14 张 = 他本局的**第一次摸牌**（配牌只写 13 张）
                    const QString extra = tiles.takeLast();
                    openingUsed = true;
                    const int n = tileNumber(extra);
                    if (n >= 0) {
                        takes[seat].append(n);
                    } else {
                        out.problems << QStringLiteral("bad_tile_code");
                    }
                }
                while (tiles.size() > 13) {
                    tiles.removeLast();   // 只在异常记录里才会走到
                }
                hands[seat] = sortedHand(tiles);
            } else if (ev == QLatin1String("draw") && seat >= 0 && seat < 4) {
                const int n = tileNumber(e.body.value(QStringLiteral("tile")).toString());
                if (n >= 0) {
                    takes[seat].append(n);
                } else {
                    out.problems << QStringLiteral("bad_draw_tile");
                }
            } else if (ev == QLatin1String("discard") && seat >= 0 && seat < 4) {
                const QString tile = e.body.value(QStringLiteral("tile")).toString();
                const bool tsumogiri = e.body.value(QStringLiteral("tsumogiri")).toBool(false);
                const bool riichi = e.body.value(QStringLiteral("riichi")).toBool(false);
                const int n = tileNumber(tile);
                river[seat] << tile;
                if (n < 0 && !tsumogiri) {
                    out.problems << QStringLiteral("bad_discard_tile");
                    continue;
                }
                if (riichi) {
                    // 立直宣言占**一个**元素（参考实现据此发 Reach + Dahai 两个事件）
                    discards[seat].append(QStringLiteral("r")
                                          + (tsumogiri ? QStringLiteral("60") : digits(tile)));
                } else if (tsumogiri) {
                    // ⚠ 必须是**数字** 60：写成字符串会被当成鸣牌串（`invalid naki string`）
                    discards[seat].append(60);
                } else {
                    discards[seat].append(n);
                }
            } else if (ev == QLatin1String("meld") && seat >= 0 && seat < 4) {
                const QJsonObject b = e.body;
                const QString kind = b.value(QStringLiteral("kind")).toString();
                const int from = b.value(QStringLiteral("from")).toInt(seat);
                const int calledIdx = b.value(QStringLiteral("called_index")).toInt(-1);
                QStringList codes;
                for (const QJsonValue& v : b.value(QStringLiteral("tiles")).toArray()) {
                    codes << v.toString();
                }
                if (kind == QLatin1String("daiminkan") || kind == QLatin1String("ankan")
                    || kind == QLatin1String("kakan")) {
                    ++kans;
                }
                // 被鸣那张：**优先用牌河里那一个位置的牌码** —— 只有它是唯一正确的来源
                // （牌河里是赤五而手里是普通五时，两者的牌号是两套码）。
                // ⚠ 只有"吃别人的舍牌"（吃/碰/大明杠）才看牌河：加杠/暗杠的牌来自**自己手里**，
                //   而那张被鸣的牌早在碰的当时就从牌河里移走了 —— 再按 `called_index` 去查会查到别人。
                const bool callsRiver = (kind == QLatin1String("chi") || kind == QLatin1String("pon")
                                         || kind == QLatin1String("daiminkan"));
                QString called;
                if (callsRiver && from >= 0 && from < 4 && from != seat && calledIdx >= 0
                    && calledIdx < river[from].size()) {
                    called = river[from].at(calledIdx);
                    river[from].removeAt(calledIdx);
                } else {
                    const QString ct = b.value(QStringLiteral("called_tile")).toString();
                    called = !ct.isEmpty() ? ct : (codes.isEmpty() ? QString() : codes.first());
                }
                const QString cd = digits(called);
                const int rel = relSeat(seat, from);
                if (cd.isEmpty()) {
                    out.problems << QStringLiteral("bad_meld_tile");
                } else if (kind == QLatin1String("chi")) {
                    // 另外两张 = 副露里除了"和被鸣那张同种"的一张（赤五与普通五算同种）
                    bool dropped = false;
                    const int calledKind = mj::kindOfTile(called);
                    QStringList own;
                    for (const QString& c : codes) {
                        if (!dropped && mj::kindOfTile(c) == calledKind) {
                            dropped = true;
                            continue;
                        }
                        own << digits(c);
                    }
                    while (own.size() < 2) {
                        own << cd;
                    }
                    takes[seat].append(nakiString(QLatin1Char('c'),
                                                  QStringList { cd, own.at(0), own.at(1) }, 0));
                } else if (kind == QLatin1String("pon")) {
                    takes[seat].append(nakiString(QLatin1Char('p'), sameTile(cd, 3),
                                                  nakiKeyAt(QLatin1Char('p'), rel)));
                } else if (kind == QLatin1String("daiminkan")) {
                    takes[seat].append(nakiString(QLatin1Char('m'), sameTile(cd, 4),
                                                  nakiKeyAt(QLatin1Char('m'), rel)));
                    // 大明杠在「出」里留一个 `0` 空位：参考实现的 finalize_discards 靠它对齐（解析时删掉）
                    discards[seat].append(0);
                } else if (kind == QLatin1String("kakan")) {
                    discards[seat].append(nakiString(QLatin1Char('k'), sameTile(cd, 4),
                                                     nakiKeyAt(QLatin1Char('k'), rel)));
                } else if (kind == QLatin1String("ankan")) {
                    // 暗杠的 `a` **只能**在 [6]：三张两两一组 + a + 第 4 张
                    discards[seat].append(
                            nakiString(QLatin1Char('a'), sameTile(cd, 4), 3));
                } else {
                    out.problems << QStringLiteral("unknown_meld_kind");
                }
            } else if (ev == QLatin1String("dora_reveal")) {
                // 这个事件给的是**更新后的完整数组** → 整组替换
                // ⚠ 不能按牌码去重：同一个牌种可以当两次指示牌（4 张同种），去重会让指示牌不够
                QStringList fresh;
                for (const QJsonValue& v :
                     e.body.value(QStringLiteral("dora_indicators")).toArray()) {
                    fresh << v.toString();
                }
                if (!fresh.isEmpty()) {
                    doraCodes = fresh;
                }
            } else if (ev == QLatin1String("agari")) {
                if (uraCodes.isEmpty()) {
                    for (const QJsonValue& v :
                         e.body.value(QStringLiteral("ura_indicators")).toArray()) {
                        uraCodes << v.toString();
                    }
                }
                hora = true;
                const int winner = e.body.value(QStringLiteral("winner")).toInt(-1);
                const int from = e.body.value(QStringLiteral("from")).toInt(-1);
                QJsonArray deltas;
                for (const QJsonValue& v : e.body.value(QStringLiteral("score_delta")).toArray()) {
                    deltas.append(v.toInt());
                }
                while (deltas.size() < 4) {
                    deltas.append(0);
                }
                result.append(deltas);
                // 和了明细：只有前两个元素（和了家 / 放銃家）被解析，其余是给人看的文字。
                // 自摸时放銃家 = 和了家（参考实现与天鳳的约定）。
                QJsonArray detail;
                detail.append(winner);
                detail.append(from < 0 ? winner : from);
                const int pts = (winner >= 0 && winner < 4) ? deltas.at(winner).toInt() : 0;
                detail.append(QStringLiteral("%1符%2飜%3点")   // i18n-keep 牌谱里的说明文字
                                      .arg(e.body.value(QStringLiteral("fu")).toInt(0))
                                      .arg(e.body.value(QStringLiteral("han")).toInt(0))
                                      .arg(pts));
                result.append(detail);
            } else if (ev == QLatin1String("ryuukyoku")) {
                if (!hora && result.isEmpty()) {
                    result.append(drawStatusText(
                            e.body.value(QStringLiteral("reason")).toString()));
                    QJsonArray deltas;
                    for (const QJsonValue& v : e.body.value(QStringLiteral("score_delta")).toArray()) {
                        deltas.append(v.toInt());
                    }
                    while (deltas.size() < 4) {
                        deltas.append(0);
                    }
                    result.append(deltas);
                }
            }
        }

        // `和了` 是**魔法串**（必须放在 results[0]，后面每两个元素一组：点数增减 + 明细）
        if (hora) {
            result.prepend(QStringLiteral("和了"));   // i18n-keep 牌谱格式常量
        } else if (result.isEmpty()) {
            // 记录不全（没打完就断了）：写一个空流局，别让解析器拿到空的 results
            result.append(QStringLiteral("流局"));     // i18n-keep 牌谱格式常量
            result.append(QJsonArray { 0, 0, 0, 0 });
            out.problems << QStringLiteral("round_no_result");
        }

        for (int s = 0; s < 4; ++s) {
            if (hands.at(s).size() != 13) {
                // 配牌必须是**恰好 13 张**，否则参考实现直接解析失败 —— 如实记一笔
                out.problems << QStringLiteral("bad_haipai");
            }
        }
        if (doraCodes.isEmpty()) {
            out.problems << QStringLiteral("no_dora_indicator");
        } else if (doraCodes.size() < 1 + kans) {
            // 参考实现按"每杠一张"消费指示牌，不够会报 insufficient dora indicators
            out.problems << QStringLiteral("insufficient_dora");
        }

        QJsonArray g;
        g.append(QJsonArray { windBase(bakaze) + qMax(0, kyoku - 1), honba, sticks });
        QJsonArray sc;
        for (int s = 0; s < 4; ++s) {
            sc.append(scores.value(s, 25000));
        }
        g.append(sc);
        g.append(toNumbers(doraCodes, &out.problems, "bad_dora"));
        g.append(toNumbers(uraCodes, &out.problems, "bad_ura"));
        // ⚠ 四家必须**逐家交错**：配牌、取、出、配牌、取、出…（位置固定的 17 元组）。
        //   要是像以前那样"先四家配牌、再四家取、再四家出"，解析器会把取牌表当配牌读 → 直接解析失败。
        for (int s = 0; s < 4; ++s) {
            g.append(toNumbers(hands.at(s), &out.problems, "bad_hand"));
            g.append(takes.at(s));
            g.append(discards.at(s));
        }
        g.append(result);
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

bool TenhouLog::writeJsonFile(const Result& r, const QString& path, QString* err)
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
    // **纯 JSON**（缩进过，便于人肉 diff）：这一份才能直接喂
    // `mjai-reviewer -e mortal -i <文件>.json -a <座位>`（`.txt` 的第一行是链接，工具解析不了）
    const QByteArray out = r.json.toUtf8();
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

// `relSeat` 在文件开头那份牌谱导出里已经定义过（同一个翻译单元的两个匿名 namespace 不能重名）。

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
                // （映射只有一份：`drawStatusText`，JSON 那份牌谱也用它）
                const QString type = drawStatusText(
                        e.body.value(QStringLiteral("reason")).toString());
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
