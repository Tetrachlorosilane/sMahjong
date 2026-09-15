#include "SelfTest.h"

#include "i18n/Lang.h"
#include "model/AutoPolicy.h"
#include "model/ReplayModel.h"
#include "model/Settings.h"
#include "model/TenhouLog.h"
#include "model/Theme.h"
#include "model/TableModel.h"
#include "model/Tile.h"
#include "net/Protocol.h"
#include "ui/ActionBar.h"
#include "ui/AutoBar.h"
#include "ui/LobbyDialog.h"
#include "ui/MainWindow.h"
#include "ui/ReplayWindow.h"
#include "ui/ResultDialog.h"
#include "ui/TableView.h"
#include "ui/TileRenderer.h"
#include "ui/WallView.h"

#include <QApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFont>
#include <QFontDatabase>
#include <QElapsedTimer>
#include <QFontInfo>
#include <QFontMetricsF>
#include <QHash>
#include <QImage>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPainter>
#include <QPixmap>
#include <QPushButton>
#include <QStringList>
#include <QTextStream>
#include <QThread>
#include <QVector>
#include <cstdio>

namespace {

// 跑一小段真事件循环：自动应答是 QTimer::singleShot 发出来的，
// 断言「到底发了什么」之前必须真的等它到点（不能靠 sleep 空转）。
void pumpFor(int ms)
{
    QElapsedTimer t;
    t.start();
    while (t.elapsed() < ms) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
        QThread::msleep(5);
    }
}

// 解析一行协议报文（自动应答那两组断言都用它造询问）
QJsonObject parseEv(const char* line)
{
    return proto::decodeLine(QByteArray(line), nullptr);
}

// 全部 37 种牌（34 种 + 3 张赤宝）
QStringList allTiles()
{
    QStringList out;
    const char* const suits[3] = { "m", "p", "s" };
    for (const char* s : suits) {
        for (int n = 1; n <= 9; ++n)
            out << QStringLiteral("%1%2").arg(n).arg(QLatin1String(s));
    }
    for (int n = 1; n <= 7; ++n)
        out << QStringLiteral("%1z").arg(n);
    out << QStringLiteral("0m") << QStringLiteral("0p") << QStringLiteral("0s");
    return out;
}

QFont cjkFont(int px, bool bold = false)
{
    QFont f;
    f.setFamilies(QStringList { QStringLiteral("Microsoft YaHei"), QStringLiteral("SimHei"),
                                QStringLiteral("Noto Sans CJK SC"), QStringLiteral("DejaVu Sans") });
    f.setPixelSize(qMax(8, px));
    f.setBold(bold);
    return f;
}

// ---- 步骤 2：37 种牌联系表 ----
bool drawTilesSheet(const QString& path, QString* err, QSize* sizeOut)
{
    const QStringList tiles = allTiles();
    const int cols = 10;
    const int tileW = 60, tileH = 80, gap = 8;
    const int labelH = 34;
    const int rows = (tiles.size() + cols - 1) / cols;
    const int pad = 12;
    const int titleH = 34;

    const int sheetW = pad * 2 + cols * (tileW + gap) - gap;
    const int sheetH = pad * 2 + titleH + rows * (tileH + labelH + gap) - gap;

    QImage img(sheetW, sheetH, QImage::Format_ARGB32_Premultiplied);
    if (img.isNull()) {
        if (err)
            *err = QStringLiteral("无法创建 QImage");
        return false;
    }
    img.fill(QColor(0xF7, 0xF4, 0xEC));

    QPainter p(&img);
    p.setRenderHint(QPainter::Antialiasing, true);
    p.setRenderHint(QPainter::TextAntialiasing, true);
    p.setPen(QColor(0x22, 0x33, 0x2E));
    p.setFont(cjkFont(18, true));
    p.drawText(QRectF(pad, pad, sheetW - pad * 2, titleH), Qt::AlignVCenter | Qt::AlignLeft,
               QStringLiteral("立直麻将 · 全 37 种牌面（1m..9m / 1p..9p / 1s..9s / 1z..7z / 0m,0p,0s）"));

    for (int i = 0; i < tiles.size(); ++i) {
        const int r = i / cols;
        const int c = i % cols;
        const int x = pad + c * (tileW + gap);
        const int y = pad + titleH + r * (tileH + labelH + gap);
        const QString& t = tiles.at(i);

        TileRenderer::drawFace(p, QRect(x, y, tileW, tileH), t, TileRenderer::isRed(t));

        p.setPen(QColor(0x2A, 0x3A, 0x34));
        p.setFont(cjkFont(13, true));
        p.drawText(QRectF(x, y + tileH + 1, tileW, 15), Qt::AlignCenter | Qt::AlignTop,
                   TileRenderer::label(t));
        p.setFont(cjkFont(11));
        p.setPen(QColor(0x77, 0x80, 0x7C));
        p.drawText(QRectF(x, y + tileH + 16, tileW, 14), Qt::AlignCenter | Qt::AlignTop, t);
    }
    // 牌背示例
    const int bx = pad + ((tiles.size() % cols) + 1) * (tileW + gap);
    const int by = pad + titleH + (rows - 1) * (tileH + labelH + gap);
    if (bx + tileW < sheetW) {
        TileRenderer::drawBack(p, QRect(bx, by, tileW, tileH));
        p.setPen(QColor(0x2A, 0x3A, 0x34));
        p.setFont(cjkFont(13, true));
        p.drawText(QRectF(bx, by + tileH + 1, tileW, 15), Qt::AlignCenter | Qt::AlignTop,
                   QStringLiteral("牌背"));
        // 横置示例
        TileRenderer::drawFace(p, QRect(bx + tileW + gap, by, tileH, tileW), QStringLiteral("5m"),
                               false, true);
        p.setFont(cjkFont(13, true));
        p.drawText(QRectF(bx + tileW + gap, by + tileW + 1, tileH, 15),
                   Qt::AlignCenter | Qt::AlignTop, QStringLiteral("横置"));
    }
    p.end();

    if (!img.save(path, "PNG")) {
        if (err)
            *err = QStringLiteral("保存失败：%1").arg(path);
        return false;
    }
    if (sizeOut)
        *sizeOut = QSize(sheetW, sheetH);
    return true;
}

// 模拟牌桌用的事件（同时验证 applyEvent 与解码器）
QStringList fakeTableEvents()
{
    QStringList evs;
    evs << QStringLiteral(
        R"({"ev":"game_start","rules":{"length":"hanchan"},"seats":[{"seat":0,"name":"自家","score":25000},{"seat":1,"name":"下家","score":24000},{"seat":2,"name":"对家","score":26000},{"seat":3,"name":"上家","score":25000}],"round":{"bakaze":"E","kyoku":1,"honba":0}})");
    evs << QStringLiteral(
        R"({"ev":"round_start","round":{"bakaze":"E","kyoku":2,"honba":1,"riichi_sticks":0},"seat":0,"dealer":1,"scores":[25000,24000,26000,25000],"hand":["1m","1m","1m","2m","3m","4m","5m","6m","7m","9m","9m","2p","2p"],"dora_indicators":["5m","1p"],"tiles_left":42,"dead_wall_left":4,"cans":{"riichi":true,"kyuushu":false}})");
    // 牌河
    evs << QStringLiteral(
        R"({"ev":"discard","seat":0,"tile":"1z","tsumogiri":false,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":0,"tile":"9p","tsumogiri":false,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":0,"tile":"3s","tsumogiri":false,"riichi":true,"riichi_stick":true})");
    evs << QStringLiteral(
        R"({"ev":"riichi","seat":3,"stick_index":1})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":1,"tile":"1p","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":1,"tile":"2p","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":1,"tile":"3p","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":1,"tile":"4p","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":1,"tile":"6p","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":1,"tile":"7p","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":1,"tile":"8p","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":1,"tile":"5p","tsumogiri":true,"riichi":true,"riichi_stick":true})");
    evs << QStringLiteral(
        R"({"ev":"meld","seat":1,"kind":"pon","tiles":["6z","6z","6z"],"from":2,"called_tile":"6z","aka":[false,false,false]})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":2,"tile":"5z","tsumogiri":false,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":2,"tile":"1s","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":2,"tile":"2s","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":2,"tile":"9s","tsumogiri":true,"riichi":true,"riichi_stick":true})");
    evs << QStringLiteral(
        R"({"ev":"meld","seat":2,"kind":"ankan","tiles":["5m","5m","5m","5m"],"from":2})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":3,"tile":"5m","tsumogiri":false,"riichi":true,"riichi_stick":true})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":3,"tile":"9m","tsumogiri":false,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":3,"tile":"8m","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":3,"tile":"7m","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":3,"tile":"6m","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":3,"tile":"4m","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":3,"tile":"3m","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":3,"tile":"2m","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"discard","seat":3,"tile":"1m","tsumogiri":true,"riichi":false,"riichi_stick":false})");
    evs << QStringLiteral(
        R"({"ev":"dora_reveal","dora_indicators":["5m","1p","9s"]})");
    evs << QStringLiteral(
        R"({"ev":"ask","seat":0,"kind":"turn","deadline_ms":15000,"tiles_left":42,"options":[{"type":"discard","tiles":["1m","2p"]},{"type":"riichi","tiles":["1m"]},{"type":"tsumo"},{"type":"kan","kans":[{"kind":"ankan","tile":"1m"}]}]})");
    return evs;
}

bool drawTableSheet(const QString& path, TableModel* model, QString* err, QSize* sizeOut)
{
    TableView view;
    view.setModel(model);
    view.resize(1280, 800);
    if (view.width() < 800) {
        if (err)
            *err = QStringLiteral("TableView 尺寸异常");
        return false;
    }
    const QPixmap pm = view.grab();
    if (pm.isNull()) {
        if (err)
            *err = QStringLiteral("grab() 失败");
        return false;
    }
    if (!pm.save(path, "PNG")) {
        if (err)
            *err = QStringLiteral("保存失败：%1").arg(path);
        return false;
    }
    if (sizeOut)
        *sizeOut = pm.size();
    return true;
}

} // namespace

namespace selftest {

int run(const QString& outDir)
{
    QDir dir(outDir);
    if (!dir.exists() && !QDir().mkpath(outDir)) {
        std::printf("SELFTEST FAIL: 无法创建输出目录 %s\n", qPrintable(outDir));
        return 1;
    }

    QStringList log;
    QStringList failures;
    int checks = 0;

    auto check = [&](bool ok, const QString& what) {
        ++checks;
        log << QStringLiteral("%1 %2").arg(ok ? QStringLiteral("[ok]  ") : QStringLiteral("[FAIL]"),
                                           what);
        if (!ok)
            failures << what;
    };
    auto checkEq = [&](const QString& got, const QString& want, const QString& what) {
        check(got == want, QStringLiteral("%1（期望 %2，实际 %3）").arg(what, want, got));
    };

    log << QStringLiteral("== 立直麻将 Qt 客户端自检 ==");
    log << QStringLiteral("输出目录：%1").arg(QDir::toNativeSeparators(dir.absolutePath()));
    log << QStringLiteral("Qt 平台：%1").arg(QApplication::platformName());
    {
        // 字体环境诊断（牌面汉字依赖系统中文字体）
        const QFont probe = cjkFont(24, true);
        const QFontInfo info(probe);
        const QFontMetricsF fm(probe);
        log << QStringLiteral("字体族数：%1；Microsoft YaHei=%2；SimHei=%3；SimSun=%4")
                   .arg(QFontDatabase::families().size())
                   .arg(QFontDatabase::hasFamily(QStringLiteral("Microsoft YaHei")) ? QStringLiteral("有")
                                                                                   : QStringLiteral("无"))
                   .arg(QFontDatabase::hasFamily(QStringLiteral("SimHei")) ? QStringLiteral("有")
                                                                          : QStringLiteral("无"))
                   .arg(QFontDatabase::hasFamily(QStringLiteral("SimSun")) ? QStringLiteral("有")
                                                                          : QStringLiteral("无"));
        log << QStringLiteral("实际选中：%1；含「萬」=%2；含「東」=%3")
                   .arg(info.family())
                   .arg(fm.inFont(QChar(0x842C)) ? QStringLiteral("是") : QStringLiteral("否"))
                   .arg(fm.inFont(QChar(0x6771)) ? QStringLiteral("是") : QStringLiteral("否"));
    }

    // ---------- 1. 牌字符串 ↔ (kind, red) 双向转换（全 37 种）----------
    log << QString() << QStringLiteral("-- 1. 牌字符串转换（37 种）--");
    const QStringList tiles = allTiles();
    check(tiles.size() == 37, QStringLiteral("牌种数量应为 37，实际 %1").arg(tiles.size()));
    for (const QString& t : tiles) {
        int kind = -1;
        bool red = false;
        const bool ok = TileRenderer::parseTile(t, &kind, &red);
        check(ok, QStringLiteral("解析 %1 应成功").arg(t));
        if (!ok)
            continue;
        checkEq(TileRenderer::tileString(kind, red), t,
                QStringLiteral("%1 反序列化回自身").arg(t));
        checkEq(mj::tileString(kind, red), t, QStringLiteral("mj::tileString(%1) 一致").arg(t));
    }
    {
        int kind = -1;
        bool red = false;
        TileRenderer::parseTile(QStringLiteral("0m"), &kind, &red);
        check(kind == 4 && red, QStringLiteral("\"0m\" → kind 4 且赤"));
        TileRenderer::parseTile(QStringLiteral("5m"), &kind, &red);
        check(kind == 4 && !red, QStringLiteral("\"5m\" → kind 4 非赤"));
        TileRenderer::parseTile(QStringLiteral("0p"), &kind, &red);
        check(kind == 13 && red, QStringLiteral("\"0p\" → kind 13 且赤"));
        TileRenderer::parseTile(QStringLiteral("0s"), &kind, &red);
        check(kind == 22 && red, QStringLiteral("\"0s\" → kind 22 且赤"));
        TileRenderer::parseTile(QStringLiteral("1z"), &kind, &red);
        check(kind == 27, QStringLiteral("\"1z\" → kind 27"));
        TileRenderer::parseTile(QStringLiteral("7z"), &kind, &red);
        check(kind == 33, QStringLiteral("\"7z\" → kind 33"));
        checkEq(mj::tileString(4, true), QStringLiteral("0m"), QStringLiteral("kind4+赤 → 0m"));
        checkEq(mj::tileString(4, false), QStringLiteral("5m"), QStringLiteral("kind4 → 5m"));
        checkEq(mj::tileString(13, true), QStringLiteral("0p"), QStringLiteral("kind13+赤 → 0p"));
        checkEq(mj::tileString(22, true), QStringLiteral("0s"), QStringLiteral("kind22+赤 → 0s"));
        check(!TileRenderer::parseTile(QStringLiteral("0z"), &kind, &red),
              QStringLiteral("\"0z\" 非法"));
        check(!TileRenderer::parseTile(QStringLiteral("8z"), &kind, &red),
              QStringLiteral("\"8z\" 非法"));
        check(!TileRenderer::parseTile(QStringLiteral("10m"), &kind, &red),
              QStringLiteral("\"10m\" 非法"));
        check(!TileRenderer::parseTile(QString(), &kind, &red), QStringLiteral("空串非法"));
        check(TileRenderer::isRed(QStringLiteral("0s")), QStringLiteral("isRed(0s)"));
        check(!TileRenderer::isRed(QStringLiteral("5s")), QStringLiteral("!isRed(5s)"));
    }

    // ---------- 2. NDJSON 协议解析 ----------
    log << QString() << QStringLiteral("-- 2. NDJSON 协议解析 --");
    {
        bool ok = false;
        const QByteArray line =
            R"({"ev":"ask","seat":0,"kind":"turn","deadline_ms":15000,"tiles_left":69,"options":[{"type":"discard","tiles":["1m","2m"]},{"type":"riichi","tiles":["4m","7p"]},{"type":"tsumo"},{"type":"kan","kans":[{"kind":"ankan","tile":"5m"},{"kind":"kakan","tile":"3s"}]},{"type":"kyuushu"}]})";
        const QJsonObject o = proto::decodeLine(line, &ok);
        check(ok, QStringLiteral("turn ask 解析成功"));
        checkEq(o.value(QStringLiteral("ev")).toString(), QStringLiteral("ask"),
                QStringLiteral("ev 字段"));
        const proto::AskInfo info = proto::parseAsk(o);
        check(info.valid, QStringLiteral("AskInfo.valid"));
        checkEq(info.kind, QStringLiteral("turn"), QStringLiteral("ask.kind"));
        check(info.seat == 0, QStringLiteral("ask.seat == 0"));
        check(info.deadlineMs == 15000, QStringLiteral("ask.deadline_ms == 15000"));
        check(info.tilesLeft == 69, QStringLiteral("ask.tiles_left == 69"));
        check(info.options.size() == 5, QStringLiteral("options 数量 5"));
        check(info.hasOption(QStringLiteral("riichi")), QStringLiteral("含 riichi 选项"));
        check(info.hasOption(QStringLiteral("kan")), QStringLiteral("含 kan 选项"));
        check(!info.hasOption(QStringLiteral("pon")), QStringLiteral("不含 pon 选项"));
        const QStringList riichiTiles =
            proto::stringList(info.option(QStringLiteral("riichi")).value(QStringLiteral("tiles")));
        check(riichiTiles == QStringList({ QStringLiteral("4m"), QStringLiteral("7p") }),
              QStringLiteral("riichi 候选牌 [4m,7p]"));
        const QJsonArray kans =
            info.option(QStringLiteral("kan")).value(QStringLiteral("kans")).toArray();
        check(kans.size() == 2, QStringLiteral("kans 数量 2"));
        checkEq(kans.at(0).toObject().value(QStringLiteral("kind")).toString(),
                QStringLiteral("ankan"), QStringLiteral("kans[0].kind"));
        checkEq(kans.at(1).toObject().value(QStringLiteral("tile")).toString(),
                QStringLiteral("3s"), QStringLiteral("kans[1].tile"));
    }
    {
        bool ok = false;
        const QJsonObject o = proto::decodeLine(
            R"({"ev":"ask","seat":1,"kind":"claim","deadline_ms":15000,"from":0,"tile":"3p","options":[{"type":"ron"},{"type":"pon"},{"type":"chi","sets":[["3m","4m"],["2m","4m"]]},{"type":"kan","kans":[{"kind":"daiminkan","tile":"3p"}]},{"type":"pass"}]})",
            &ok);
        check(ok, QStringLiteral("claim ask 解析成功"));
        const proto::AskInfo info = proto::parseAsk(o);
        checkEq(info.kind, QStringLiteral("claim"), QStringLiteral("claim.kind"));
        checkEq(info.tile, QStringLiteral("3p"), QStringLiteral("claim.tile"));
        check(info.from == 0, QStringLiteral("claim.from == 0"));
        const QJsonArray sets =
            info.option(QStringLiteral("chi")).value(QStringLiteral("sets")).toArray();
        check(sets.size() == 2, QStringLiteral("chi sets 数量 2"));
        check(proto::stringList(sets.at(0))
                  == QStringList({ QStringLiteral("3m"), QStringLiteral("4m") }),
              QStringLiteral("chi sets[0] == [3m,4m]"));
    }
    {
        bool ok = false;
        const QJsonObject o = proto::decodeLine(
            R"({"ev":"ask","seat":3,"kind":"chankan","from":2,"tile":"5z","options":[{"type":"ron"},{"type":"pass"}]})",
            &ok);
        check(ok, QStringLiteral("chankan ask 解析成功"));
        const proto::AskInfo info = proto::parseAsk(o);
        checkEq(info.kind, QStringLiteral("chankan"), QStringLiteral("chankan.kind"));
        checkEq(info.tile, QStringLiteral("5z"), QStringLiteral("chankan.tile"));
    }
    {
        bool ok = true;
        proto::decodeLine(QByteArray("{ this is not json"), &ok);
        check(!ok, QStringLiteral("非法 JSON 应解析失败"));
        ok = true;
        proto::decodeLine(QByteArray("[1,2,3]"), &ok);
        check(!ok, QStringLiteral("非对象 JSON 应解析失败"));
        ok = true;
        proto::decodeLine(QByteArray("   "), &ok);
        check(!ok, QStringLiteral("空行应被忽略"));
    }
    {
        QJsonObject cmd;
        cmd.insert(QStringLiteral("cmd"), QStringLiteral("ping"));
        const QByteArray line = proto::encodeLine(cmd);
        check(line.endsWith('\n'), QStringLiteral("encodeLine 以 \\n 结尾"));
        check(!line.contains("\n\n"), QStringLiteral("encodeLine 只含一个换行"));
        bool ok = false;
        const QJsonObject back = proto::decodeLine(line, &ok);
        check(ok, QStringLiteral("encodeLine/decodeLine 往返"));
        checkEq(back.value(QStringLiteral("cmd")).toString(), QStringLiteral("ping"),
                QStringLiteral("往返后 cmd 字段"));
        QJsonObject withText;
        withText.insert(QStringLiteral("cmd"), QStringLiteral("chat"));
        // 中文 + BMP 之外的字符（代理对）：报文全程 UTF-8，往返必须逐字相同
        // （服务端的 yaku 名 / limit / reason / 玩家名 / 聊天都是这类文本）
        withText.insert(QStringLiteral("text"), QStringLiteral("换行\n测试・𠮷🀄"));
        const QByteArray l2 = proto::encodeLine(withText);
        check(l2.count('\n') == 1, QStringLiteral("文本中的换行被转义"));
        bool ok2 = false;
        const QJsonObject back2 = proto::decodeLine(l2, &ok2);
        check(ok2, QStringLiteral("含中文的报文往返可解析"));
        checkEq(back2.value(QStringLiteral("text")).toString(),
                QStringLiteral("换行\n测试・𠮷🀄"),
                QStringLiteral("中文/代理对往返逐字相同"));
        // QByteArray::contains 收 const char*（按字节比）：源码是 UTF-8，正好验证"直出 UTF-8 字节"
        check(l2.contains("测试"),
              QStringLiteral("中文以 UTF-8 字节直出（未被转义成 Unicode 转义）"));
    }
    {
        bool ok = false;
        const QJsonObject o = proto::decodeLine(
            R"({"ev":"discard","seat":1,"tile":"3p","tsumogiri":true,"riichi":false,"riichi_stick":false})",
            &ok);
        check(ok, QStringLiteral("discard 事件解析"));
        checkEq(o.value(QStringLiteral("tile")).toString(), QStringLiteral("3p"),
                QStringLiteral("discard.tile"));
        check(o.value(QStringLiteral("seat")).toInt() == 1, QStringLiteral("discard.seat == 1"));
    }

    // ---------- 3. 模型事件应用 ----------
    log << QString() << QStringLiteral("-- 3. TableModel 事件应用 --");
    TableModel model;
    const QStringList evs = fakeTableEvents();
    for (const QString& line : evs) {
        bool ok = false;
        const QJsonObject o = proto::decodeLine(line.toUtf8(), &ok);
        check(ok, QStringLiteral("样例事件可解析：%1").arg(line.left(48)));
        if (ok)
            model.applyEvent(o);
    }
    check(model.hasSeat(), QStringLiteral("round_start 后 hasSeat"));
    check(model.mySeat() == 0, QStringLiteral("mySeat == 0"));
    check(model.hand().size() == 13, QStringLiteral("手牌 13 张"));
    checkEq(model.hand().value(0), QStringLiteral("1m"), QStringLiteral("手牌已排序首张 1m"));
    checkEq(model.playerName(2), QStringLiteral("对家"), QStringLiteral("玩家名同步"));
    check(model.discards(0).size() == 3, QStringLiteral("自己牌河 3 张"));
    check(model.discardSideways(0, 2), QStringLiteral("立直宣言牌横置"));
    // ---- 副露横置位：必须由「打牌者方位」决定 ----
    // 座位递增 = 下家。上家=最左(0)、对家=中间(1)、下家=最右(2)。
    {
        Meld m;
        m.kind = QStringLiteral("pon");
        m.tiles = QStringList { QStringLiteral("5z"), QStringLiteral("5z"), QStringLiteral("5z") };
        m.calledTile = QStringLiteral("5z");
        m.from = 3;                                    // 自家(0)的上家
        checkEq(QString::number(TableView::meldRotatedIndexForTest(m, 0)), QStringLiteral("0"),
                QStringLiteral("碰上家 → 横在左侧"));
        m.from = 2;                                    // 对家
        checkEq(QString::number(TableView::meldRotatedIndexForTest(m, 0)), QStringLiteral("1"),
                QStringLiteral("碰对家 → 横在中间"));
        m.from = 1;                                    // 下家
        checkEq(QString::number(TableView::meldRotatedIndexForTest(m, 0)), QStringLiteral("2"),
                QStringLiteral("碰下家 → 横在右侧"));

        // 吃：横置位由**来源方位**决定，与点数顺序无关
        Meld c;
        c.kind = QStringLiteral("chi");
        c.tiles = QStringList { QStringLiteral("1m"), QStringLiteral("2m"), QStringLiteral("3m") };
        c.calledTile = QStringLiteral("2m");
        c.from = 3;                                    // 上家
        checkEq(QString::number(TableView::meldRotatedIndexForTest(c, 0)), QStringLiteral("0"),
                QStringLiteral("吃上家 → 横在最左"));
        c.from = 2;                                    // 对家
        checkEq(QString::number(TableView::meldRotatedIndexForTest(c, 0)), QStringLiteral("1"),
                QStringLiteral("吃对家 → 横在中间"));
        c.from = 1;                                    // 下家
        checkEq(QString::number(TableView::meldRotatedIndexForTest(c, 0)), QStringLiteral("2"),
                QStringLiteral("吃下家 → 横在最右"));
        // 暗杠不横置
        Meld a;
        a.kind = QStringLiteral("ankan");
        a.tiles = QStringList { QStringLiteral("5z"), QStringLiteral("5z"),
                                QStringLiteral("5z"), QStringLiteral("5z") };
        a.calledTile = QStringLiteral("5z");
        checkEq(QString::number(TableView::meldRotatedIndexForTest(a, 0)), QStringLiteral("-1"),
                QStringLiteral("暗杠不横置"));
    }

    // ---- 回归：一个人牌河里最多只能有一张横置牌 ----
    // 旧实现的问题：riichi 事件到达时把「牌河最后一张」误标为横置，
    // 而服务端是先发 riichi 再发 discard，于是宣言牌之前那张被误标，
    // 加上宣言牌自身的 sideways，导致一人牌河出现两张横置。
    {
        TableModel m3;
        const auto feed = [&m3](const char* json) {
            m3.applyEvent(proto::decodeLine(QByteArray(json), nullptr));
        };
        feed(R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","4m","5m","6m","7m","8m","9m","1p","2p","3p","4p"],"dora_indicators":["5p"],"tiles_left":60,"dead_wall_left":4})");
        feed(R"({"ev":"discard","seat":0,"tile":"1m","tsumogiri":false,"riichi":false,"sideways":false})");
        feed(R"({"ev":"discard","seat":0,"tile":"2m","tsumogiri":false,"riichi":false,"sideways":false})");
        feed(R"({"ev":"discard","seat":0,"tile":"3m","tsumogiri":false,"riichi":true,"sideways":true})");
        feed(R"({"ev":"discard","seat":0,"tile":"4m","tsumogiri":false,"riichi":false,"sideways":false})");
        feed(R"({"ev":"riichi","seat":0,"stick_index":0,"sticks":1})");   // 先 riichi 后 discard 的真实顺序
        feed(R"({"ev":"meld","seat":1,"kind":"pon","tiles":["3m","3m","3m"],"from":0,"called_tile":"3m","aka":[false,false,false],"called_index":2})");
        feed(R"({"ev":"discard","seat":0,"tile":"5m","tsumogiri":false,"riichi":false,"sideways":true})");  // 顺延牌
        feed(R"({"ev":"discard","seat":0,"tile":"6m","tsumogiri":false,"riichi":false,"sideways":false})");

        int nSide = 0;
        for (int i = 0; i < m3.discards(0).size(); ++i) {
            if (m3.discardSideways(0, i))
                ++nSide;
        }
        checkEq(QString::number(m3.discards(0).size()), QStringLiteral("5"), QStringLiteral("鸣牌后牌河张数"));
        checkEq(QString::number(nSide), QStringLiteral("1"), QStringLiteral("一人牌河横置张数"));
        check(m3.discardSideways(0, 3), QStringLiteral("横置应落在顺延牌（第 4 张 = 5m）上"));
        check(!m3.discardSideways(0, 2), QStringLiteral("顺延牌之前那张（4m）不得被误标横置"));
    }

    // ---- 回归：宝牌指示牌行 / 牌河尺寸 / 立直棒带（风盘布局）----
    // 用户实测的三个问题，每条对应一个断言：
    //   ① 牌河/副露太小：风盘曾经先钉死成 ~209 的正方形，再把牌河硬缩进去，
    //      牌河牌只剩手牌的 45%（25px vs 56px）。现在风盘尺寸**由内容反推**。
    //   ② 宝牌指示牌被横向压扁：旧绘制 `min(高/1.36, 宽/5)` 只压宽度、不动高度。
    //   ③ 立直棒与点数重合：旧代码 `sy = c.bottom()-pad-sh` 与自家点数行几乎同一行。
    // 窗口尺寸取实机复现值（2400x1500 抓图 / DPR 1.5 → 1354x930）。
    {
        TableModel md;
        md.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":2},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","4m","5m","6m","7m","8m","9m","1p","2p","3p","4p"],"dora_indicators":["5m","2p","8s","3z","6m"],"tiles_left":60,"dead_wall_left":4})"),
            nullptr));
        TableView tv;
        tv.setModel(&md);
        tv.resize(1354, 930);
        tv.grab();                       // 触发一次 paintEvent → computeLayout()

        // ① 牌河/副露牌没被风盘挤小（标称 = 手牌宽 × 0.84 × 0.95 ≈ 0.798）
        const qreal rw = tv.riverWForTest();
        const qreal hw = tv.handWForTest();
        check(hw > 0.0 && rw >= hw * 0.79,
              QStringLiteral("牌河/副露牌宽不应被挤小（牌河 %1，手牌 %2）").arg(rw).arg(hw));
        // ①b 宝牌指示牌与牌河牌同量级（≈0.95 × 牌河牌）：不能太小，也不该反超太多
        check(tv.doraRectForTest().height() >= tv.riverHForTest() * 0.90
                      && tv.doraRectForTest().height() <= tv.riverHForTest() * 1.05,
              QStringLiteral("宝牌指示牌应与牌河牌同量级（宝牌 %1，牌河 %2）")
                  .arg(tv.doraRectForTest().height()).arg(tv.riverHForTest()));
        // ①c 风盘应接近正方形（宽度被牌河一行撑着，高度要补上去）
        const QRectF pr = tv.centerRectForTest();
        check(pr.width() > 0.0 && pr.width() <= pr.height() * 1.25,
              QStringLiteral("风盘应接近正方形（宽 %1，高 %2）").arg(pr.width()).arg(pr.height()));

        // ② 宝牌行：5 张**不被压扁**的牌要放得下 —— 即「按行高算出的牌宽」
        //    必须 ≤「按行宽算出的牌宽」。若反了，说明绘制只能压宽度（牌变瘦长条）。
        //    ⚠ 行里**不再有「宝牌」字样**（已按需求去掉），所以算可用宽度时不留标签位。
        const QRectF dora = tv.doraRectForTest();
        const qreal twFromH = dora.height() / 1.36;
        const qreal twFromW = (dora.width() - 4.0 - 4.0 * 3.0) / 5.0;
        check(twFromH > 0.0 && twFromW >= twFromH,
              QStringLiteral("宝牌指示牌行应容得下 5 张不压扁的牌（按高 %1，按宽 %2）")
                  .arg(twFromH).arg(twFromW));
        // ②b 五张指示牌整行必须落在**四家分数框围出的中间列**以内
        check(dora.left() >= tv.scoreBandForTest(3).right() - 0.01
                  && dora.right() <= tv.scoreBandForTest(1).left() + 0.01,
              QStringLiteral("宝牌指示牌行应包含在四家分数框以内"
                             "（行 %1..%2，左右点数框内沿 %3 / %4）")
                  .arg(dora.left()).arg(dora.right())
                  .arg(tv.scoreBandForTest(3).right()).arg(tv.scoreBandForTest(1).left()));
        check(dora.top() >= tv.scoreBandForTest(2).bottom() - 0.01
                  && dora.bottom() <= tv.scoreBandForTest(0).top() + 0.01,
              QStringLiteral("宝牌指示牌行应在上下两家点数框之间"));
        const QRectF blk = tv.handBlockScreenForTest(2);
        check(!blk.isEmpty(), QStringLiteral("对家「手牌 + 摸牌」块应可测得"));
        check(!dora.intersects(blk),
              QStringLiteral("对家的手牌/摸牌不得侵占宝牌指示牌行（行下 %1，块上 %2）")
                  .arg(dora.bottom()).arg(blk.top()));

        // ②c 四家的得点框**一样大**（横排两家与竖排两家只是朝向不同）
        const QVector<QSizeF> boxes = tv.scoreBoxSizesForTest();
        checkEq(QString::number(boxes.size()), QStringLiteral("4"),
                QStringLiteral("四家得点框都应参与绘制"));
        if (boxes.size() == 4) {
            bool same = true;
            for (int i = 1; i < 4; ++i) {
                same = same && qAbs(boxes.at(i).width() - boxes.at(0).width()) < 0.01
                            && qAbs(boxes.at(i).height() - boxes.at(0).height()) < 0.01;
            }
            check(same && boxes.at(0).width() > 0.0,
                  QStringLiteral("四家得点框必须一样大（实际 %1 / %2 / %3 / %4）")
                      .arg(boxes.at(0).width()).arg(boxes.at(1).width())
                      .arg(boxes.at(2).width()).arg(boxes.at(3).width()));
            // 框不能比带还长（否则会伸到相邻的带里去）
            for (int pos = 0; pos < 4; ++pos) {
                const qreal bandLong = ((pos % 2) == 0) ? tv.scoreBandForTest(pos).width()
                                                        : tv.scoreBandForTest(pos).height();
                check(boxes.at(pos).width() <= bandLong + 0.01,
                      QStringLiteral("pos=%1 的得点框不得超出本侧点数带（框 %2，带 %3）")
                          .arg(pos).arg(boxes.at(pos).width()).arg(bandLong));
            }
        }

        // ②d 四家得点框必须**沿各自那条边居中**：上下两家 = 水平居中于盘的中轴，
        //    左右两家 = 垂直居中于盘的水平中轴（旧版左右两家被宝牌行往下顶，明显偏下）。
        {
            const QPointF c = tv.centerRectForTest().center();
            const auto off = [](qreal a, qreal b) { return qAbs(a - b); };
            check(off(tv.scoreBandForTest(2).center().x(), c.x()) < 0.5
                      && off(tv.scoreBandForTest(0).center().x(), c.x()) < 0.5,
                  QStringLiteral("上下两家的得点框应水平居中（框心 %1 / %2，盘中轴 %3）")
                      .arg(tv.scoreBandForTest(2).center().x())
                      .arg(tv.scoreBandForTest(0).center().x()).arg(c.x()));
            check(off(tv.scoreBandForTest(3).center().y(), c.y()) < 0.5
                      && off(tv.scoreBandForTest(1).center().y(), c.y()) < 0.5,
                  QStringLiteral("左右两家的得点框应垂直居中（框心 %1 / %2，盘中轴 %3）")
                      .arg(tv.scoreBandForTest(3).center().y())
                      .arg(tv.scoreBandForTest(1).center().y()).arg(c.y()));
            // 左右两家的棒带与点数带同长同心：棒也必须跟着居中，不能一条上一个下
            check(off((tv.stickBandForTest(3).center().y() + tv.stickBandForTest(1).center().y()) / 2.0,
                      c.y()) < 0.5,
                  QStringLiteral("左右两家的立直棒带也应垂直居中"));
        }

        // ③ 四边同构：**各家点数到风盘边缘的距离处处相等**，且每边都在点数**外侧**
        //    留了一条立直棒带（棒在自己面前，不在盘底堆一堆）。
        {
            const QRectF c = tv.centerRectForTest();
            const qreal dTop = tv.scoreBandForTest(2).top() - c.top();
            const qreal dBottom = c.bottom() - tv.scoreBandForTest(0).bottom();
            const qreal dLeft = tv.scoreBandForTest(3).left() - c.left();
            const qreal dRight = c.right() - tv.scoreBandForTest(1).right();
            check(qAbs(dTop - dBottom) < 0.5 && qAbs(dTop - dLeft) < 0.5
                      && qAbs(dTop - dRight) < 0.5,
                  QStringLiteral("四家点数到风盘边缘的距离应一致（上 %1 / 下 %2 / 左 %3 / 右 %4）")
                      .arg(dTop).arg(dBottom).arg(dLeft).arg(dRight));
            // 每边：棒带在点数带**外侧**（更靠盘边），且两者不相交
            check(tv.stickBandForTest(2).bottom() <= tv.scoreBandForTest(2).top() + 0.01
                      && tv.stickBandForTest(0).top() >= tv.scoreBandForTest(0).bottom() - 0.01
                      && tv.stickBandForTest(3).right() <= tv.scoreBandForTest(3).left() + 0.01
                      && tv.stickBandForTest(1).left() >= tv.scoreBandForTest(1).right() - 0.01,
                  QStringLiteral("立直棒带应在同侧点数带的**外侧**（更靠盘边）"));
            for (int pos = 0; pos < 4; ++pos) {
                check(!tv.stickBandForTest(pos).intersects(tv.scoreBandForTest(pos)),
                      QStringLiteral("pos=%1 的立直棒带不得与同侧点数带重合").arg(pos));
                // 棒带必须落在盘内（贴边那条）
                check(c.contains(tv.stickBandForTest(pos)),
                      QStringLiteral("pos=%1 的立直棒带应落在风盘内").arg(pos));
            }
        }
        check(!tv.doraRectForTest().intersects(tv.topScoreBandForTest()),
              QStringLiteral("宝牌行不得与対面点数重合"));
        // 顺序要求：**対面点数在上、宝牌行紧贴其下**（用户指定），别被改回去
        check(tv.doraRectForTest().top() >= tv.topScoreBandForTest().bottom(),
              QStringLiteral("宝牌行应在対面点数**下方**（点数底 %1，宝牌行顶 %2）")
                  .arg(tv.topScoreBandForTest().bottom()).arg(tv.doraRectForTest().top()));
        check(tv.doraRectForTest().bottom() <= tv.selfScoreBandForTest().top(),
              QStringLiteral("宝牌行应在自家点数上方（宝牌行底 %1，自家点数顶 %2）")
                  .arg(tv.doraRectForTest().bottom()).arg(tv.selfScoreBandForTest().top()));
        // 场次块必须整个落在宝牌行**下方**，否则首行（東1局）会被宝牌牌面盖住
        check(!tv.centerBandForTest().intersects(tv.doraRectForTest()),
              QStringLiteral("场次区带不得与宝牌行相交（宝牌行底 %1，场次带顶 %2）")
                  .arg(tv.doraRectForTest().bottom()).arg(tv.centerBandForTest().top()));

        // ④ 立直棒：**各家自己的棒要画在各家面前**（报障：四家的棒全堆在盘底，看着像都是自家的）。
        //    做法是"按座位取自己那一侧的棒带"，所以这里直接看上一帧画在哪几侧。
        {
            // ① 対面（seat 2）+ 下家（seat 1）立直 → 应该只画在 pos 2 / pos 1，绝不落在盘底 pos 0
            TableModel ms;
            ms.applyEvent(proto::decodeLine(QByteArrayLiteral(
                R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","4m","5m","6m","7m"],"dora_indicators":["5p"],"tiles_left":40,"dead_wall_left":4})"),
                nullptr));
            ms.applyEvent(proto::decodeLine(
                QByteArrayLiteral(R"({"ev":"riichi","seat":2,"stick_index":0,"sticks":1,"scores":[25000,24000,25000,25000]})"),
                nullptr));
            ms.applyEvent(proto::decodeLine(
                QByteArrayLiteral(R"({"ev":"riichi","seat":1,"stick_index":1,"sticks":2,"scores":[25000,24000,24000,25000]})"),
                nullptr));
            TableView ts;
            ts.setModel(&ms);
            ts.resize(1354, 930);
            ts.grab();
            const QVector<int> pos = ts.stickPositionsForTest();
            QStringList posList;
            for (int v : pos)
                posList << QString::number(v);
            checkEq(QString::number(pos.size()), QStringLiteral("2"),
                    QStringLiteral("两家立直 → 恰好画 2 根棒"));
            check(pos.contains(2) && pos.contains(1),
                  QStringLiteral("対面/下家的棒应画在各自那一侧（pos 2 / 1），实际：%1")
                      .arg(posList.join(QLatin1Char(','))));
            check(!pos.contains(0),
                  QStringLiteral("別家的立直棒不得画在自家面前（盘底）"));
            checkEq(QString::number(ts.potSticksForTest()), QStringLiteral("0"),
                    QStringLiteral("两家立直、供託正好用光 → 盘中央不该再画"));

            // ② 自家（seat 0）立直 → 棒落在盘底（自家面前）
            TableModel m0;
            m0.applyEvent(proto::decodeLine(QByteArrayLiteral(
                R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","4m","5m","6m","7m"],"dora_indicators":["5p"],"tiles_left":40,"dead_wall_left":4})"),
                nullptr));
            m0.applyEvent(proto::decodeLine(
                QByteArrayLiteral(R"({"ev":"riichi","seat":0,"stick_index":0,"sticks":1,"scores":[24000,25000,25000,25000]})"),
                nullptr));
            TableView t0;
            t0.setModel(&m0);
            t0.resize(1354, 930);
            t0.grab();
            checkEq(QString::number(t0.stickPositionsForTest().size()), QStringLiteral("1"),
                    QStringLiteral("自家立直 → 画 1 根棒"));
            check(t0.stickPositionsForTest().contains(0),
                  QStringLiteral("自家的立直棒应画在自家这一侧（pos 0）"));

            // ③ 上一局留下的**供託**（不属于任何一家）：别塞给某一家，摆在盘中央
            TableModel mp;
            mp.applyEvent(proto::decodeLine(QByteArrayLiteral(
                R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":2},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","4m","5m","6m","7m"],"dora_indicators":["5p"],"tiles_left":40,"dead_wall_left":4})"),
                nullptr));
            TableView tp;
            tp.setModel(&mp);
            tp.resize(1354, 930);
            tp.grab();
            check(tp.stickPositionsForTest().isEmpty(),
                  QStringLiteral("没人立直时不该在任何一家面前画棒"));
            checkEq(QString::number(tp.potSticksForTest()), QStringLiteral("2"),
                    QStringLiteral("上一局留下的 2 根供託应摆在盘中央"));
        }

        // ⑤ 副露带顺序：**最早的一副在最右**（局部 u 最大），之后依次向左。
        //    这一段曾经与注释相反（画成了从左到右、最早的在最左），所以钉一条断言。
        //    用 allmeld 之外的最小构造：直接给模型灌两次「碰」，再比较两副露的左端。
        {
            TableModel mm;
            mm.applyEvent(proto::decodeLine(QByteArrayLiteral(
                R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","4m","5m","6m","7m"],"dora_indicators":["5p"],"tiles_left":40,"dead_wall_left":4})"),
                nullptr));
            mm.applyEvent(proto::decodeLine(QByteArrayLiteral(
                R"({"ev":"meld","seat":0,"kind":"pon","tiles":["9s","9s","9s"],"from":1,"called_tile":"9s","aka":[false,false,false],"called_index":0})"),
                nullptr));
            mm.applyEvent(proto::decodeLine(QByteArrayLiteral(
                R"({"ev":"meld","seat":0,"kind":"pon","tiles":["8s","8s","8s"],"from":3,"called_tile":"8s","aka":[false,false,false],"called_index":0})"),
                nullptr));
            TableView mv;
            mv.setModel(&mm);
            mv.resize(1354, 930);
            mv.grab();
            const QVector<qreal> lefts = mv.meldLeftsForTest(0);
            checkEq(QString::number(lefts.size()), QStringLiteral("2"), QStringLiteral("副露带应有 2 副"));
            if (lefts.size() == 2) {
                check(lefts.at(0) > lefts.at(1),
                      QStringLiteral("最早的一副应在最右（第 1 副左端 %1 应 > 第 2 副左端 %2）")
                          .arg(lefts.at(0)).arg(lefts.at(1)));
                check(lefts.at(0) < mv.meldRightForTest(0) + 0.01,
                      QStringLiteral("副露带右端不得越出行末保留（%1 vs %2）")
                          .arg(lefts.at(0)).arg(mv.meldRightForTest(0)));
            }
        }

        // ④ 河区预留范围：套得住风盘（不小于风盘 + 3 行 + 一张），且不碰四家手牌。
        //    线框本身已按需求不再绘制，但**布局预算仍在用这块范围**，所以断言保留。
        const QRectF rf = tv.riverAreaForTest();
        check(rf.contains(tv.doraRectForTest()) || rf.width() > tv.doraRectForTest().width(),
              QStringLiteral("河区预留范围应把风盘整个套住"));
        for (int pos = 0; pos < 4; ++pos) {
            const QRectF hb = tv.handBlockScreenForTest(pos);
            check(!hb.isEmpty() && !rf.intersects(hb),
                  QStringLiteral("河区预留范围不得压到 pos=%1 的手牌（范围 %2..%3 / %4..%5，手牌 %6..%7 / %8..%9）")
                      .arg(pos).arg(rf.left()).arg(rf.right()).arg(rf.top()).arg(rf.bottom())
                      .arg(hb.left()).arg(hb.right()).arg(hb.top()).arg(hb.bottom()));
        }
    }

    // ---- 回归：牌河排满 3 行（18 张）后**继续向外长**，不再丢牌 ----
    // 报障：3×6 排满后，第 19 张及以后的牌河牌**整张不显示**。
    // 根因：paintRiver 里行数取了 qMin(kMaxRiverRows, …) —— 第 4 行压根没画。
    // 三条不变量：① 行数按 6 列一行**不封顶**；② 布局按同一函数预留（多出来的行不压手牌）；
    //            ③ 这些牌**真的画出来了**（对抓图里的牌面像素计数，不是只看几何）。
    {
        checkEq(QString::number(TableView::riverRowCountForTest(18)), QStringLiteral("3"),
                QStringLiteral("18 张正好排满 3 行"));
        checkEq(QString::number(TableView::riverRowCountForTest(19)), QStringLiteral("4"),
                QStringLiteral("第 19 张起排第 4 行（旧代码在这里被截断）"));
        checkEq(QString::number(TableView::riverRowCountForTest(24)), QStringLiteral("4"),
                QStringLiteral("24 张仍是 4 行"));
        checkEq(QString::number(TableView::riverRowCountForTest(25)), QStringLiteral("5"),
                QStringLiteral("第 25 张起排第 5 行"));

        // 打 18 张 → 抓图数牌面像素；再打 3 张 → 必须**明显变多**（多出来的那行画了牌）
        TableModel md;
        md.applyEvent(parseEv(
            R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},)"
            R"("seat":0,"dealer":0,"scores":[25000,25000,25000,25000],)"
            R"("hand":["1m","2m","3m","4m","5m","6m","7m","8m","9m","1p","2p","3p","4p"],)"
            R"("tiles_left":60,"dead_wall_left":4})"));
        TableView tv;
        tv.setModel(&md);
        tv.resize(1354, 930);

        // 牌面底色（TileRenderer 的 kIvoryTop ≈ 0xFA,0xF6,0xE7）像素计数
        auto facePixels = [](const QPixmap& pm) {
            const QImage img = pm.toImage();
            int n = 0;
            for (int y = 0; y < img.height(); ++y) {
                for (int x = 0; x < img.width(); ++x) {
                    const QRgb c = img.pixel(x, y);
                    if (qRed(c) >= 235 && qGreen(c) >= 230 && qBlue(c) >= 210)
                        ++n;
                }
            }
            return n;
        };
        // 用**别家**（seat 1）的牌河：模型不会去动自己的手牌，两次抓图之间
        // 唯一变化的就只有牌河张数 —— 增量就代表新画出来的牌。
        const auto discardForSeat1 = [&md]() {
            md.applyEvent(parseEv(R"({"ev":"discard","seat":1,"tile":"1m","tsumogiri":false})"));
        };
        for (int i = 0; i < 18; ++i)
            discardForSeat1();
        checkEq(QString::number(md.discards(1).size()), QStringLiteral("18"),
                QStringLiteral("先打满 18 张（3 行）"));
        pumpFor(500);                       // 等出牌动画落定（飞行中的牌不在牌河里）
        const QPixmap pm18 = tv.grab();
        const int px18 = facePixels(pm18);
        checkEq(QString::number(tv.riverRowsForTest()), QStringLiteral("3"),
                QStringLiteral("18 张时预留 3 行"));

        for (int i = 0; i < 3; ++i)
            discardForSeat1();
        checkEq(QString::number(md.discards(1).size()), QStringLiteral("21"),
                QStringLiteral("再打 3 张（第 19~21 张）"));
        pumpFor(500);
        const QPixmap pm21 = tv.grab();
        const int px21 = facePixels(pm21);
        checkEq(QString::number(tv.riverRowsForTest()), QStringLiteral("4"),
                QStringLiteral("21 张时应预留 4 行（预留范围跟着长，才不会压手牌）"));
        // 3 张牌面 ≈ 3 × 44 × 60 ≈ 7900 px；门槛取 1500，远高于噪声、又远低于真画出来的量
        check(px21 > px18 + 1500,
              QStringLiteral("第 19~21 张必须**真的画出来**（牌面像素 %1 → %2）").arg(px18).arg(px21));
        // 留一张图（回归时能直接看第 4 行）
        const QString riverPng = dir.absoluteFilePath(QStringLiteral("river_overflow.png"));
        check(pm21.save(riverPng),
              QStringLiteral("多行牌河截图已保存：%1").arg(riverPng));

        // 多出来的那一行也要落在河区预留范围内，并且仍然不碰四家手牌
        const QRectF rf = tv.riverAreaForTest();
        for (int pos = 0; pos < 4; ++pos) {
            const QRectF hb = tv.handBlockScreenForTest(pos);
            check(!hb.isEmpty() && !rf.intersects(hb),
                  QStringLiteral("牌河长到第 4 行后仍不得压到 pos=%1 的手牌").arg(pos));
        }
    }

    // ---- 回归：手里已有 5m 又摸到 5m，手切原来那张（tsumogiri=false）----
    // 旧实现靠「kind 是否等于摸到的牌」猜，会把摸牌当打出去的清掉，导致手牌数对不上。
    {
        TableModel m2;
        m2.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["5m","5m","1m","2m","3m","4m","6m","7m","8m","1p","2p","3p","9s"],"dora_indicators":["1z"],"tiles_left":70,"dead_wall_left":4})"),
            nullptr));
        const int handBefore = m2.hand().size();
        m2.applyEvent(proto::decodeLine(
            QByteArrayLiteral(R"({"ev":"draw","seat":0,"tiles_left":69,"rinshan":false,"tile":"5m"})"),
            nullptr));
        checkEq(m2.drawnTile(), QStringLiteral("5m"), QStringLiteral("摸到 5m"));
        check(m2.hand().size() == handBefore, QStringLiteral("摸牌不并入手牌"));
        // 手切：服务端明确告知 tsumogiri=false
        m2.applyEvent(proto::decodeLine(
            QByteArrayLiteral(R"({"ev":"discard","seat":0,"tile":"5m","tsumogiri":false,"riichi":false,"riichi_stick":false})"),
            nullptr));
        // 手切之后，刚摸到的牌应立即并入暗牌：摸牌位清空、暗牌恰好 -1
        checkEq(m2.drawnTile(), QString(), QStringLiteral("手切后摸牌位立即清空（并入暗牌）"));
        // 并入之后暗牌应回到「摸牌前」的张数：13 张暗牌 = 手切掉 1 张 + 摸到的 1 张并入
        checkEq(QString::number(m2.hand().size()), QString::number(handBefore),
                QStringLiteral("手切后暗牌回到摸牌前张数（摸牌已并入）"));
        check(m2.hand().contains(QStringLiteral("5m")), QStringLiteral("并入的摸牌仍在暗牌里"));
        // 摸切：服务端告知 tsumogiri=true
        m2.applyEvent(proto::decodeLine(
            QByteArrayLiteral(R"({"ev":"draw","seat":0,"tiles_left":68,"rinshan":false,"tile":"7p"})"),
            nullptr));
        const int before2 = m2.hand().size();
        m2.applyEvent(proto::decodeLine(
            QByteArrayLiteral(R"({"ev":"discard","seat":0,"tile":"7p","tsumogiri":true,"riichi":false,"riichi_stick":false})"),
            nullptr));
        checkEq(m2.drawnTile(), QString(), QStringLiteral("摸切后摸牌位清空"));
        check(m2.hand().size() == before2, QStringLiteral("摸切不动手牌（摸牌本来就不在里面）"));
    }

    // ---- 回归：杠后从岭上摸牌，界面上的「岭上 N」必须跟着减 ----
    // 报障：「杠后摸的牌（明杠/加杠/暗杠）应该从岭上摸，但岭上牌并没有减少」。
    // 根因是服务端只在开局报了一次 `dead_wall_left`；现在每次摸牌都带，
    // 客户端也必须**跟着更新**（`tiles_left` 看不出这件事：岭上摸牌不动牌山）。
    {
        TableModel mk;
        mk.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","1m","1m","1m","2m","3m","4m","5p","6p","7p","2s","3s","4s"],"dora_indicators":["5p"],"tiles_left":70,"dead_wall_left":4})"),
            nullptr));
        checkEq(QString::number(mk.deadWallLeft()), QStringLiteral("4"),
                QStringLiteral("开局岭上 4 张"));

        // 暗杠 → 新宝牌指示牌 → 从岭上摸牌（rinshan=true，岭上剩 3）
        mk.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"meld","seat":0,"kind":"ankan","tiles":["1m","1m","1m","1m"],"from":0,"called_tile":"1m","called_index":-1})"),
            nullptr));
        mk.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"dora_reveal","dora_indicators":["5p","2z"]})"), nullptr));
        mk.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"draw","seat":0,"tiles_left":70,"rinshan":true,"tile":"9s","dead_wall_left":3})"),
            nullptr));
        checkEq(QString::number(mk.deadWallLeft()), QStringLiteral("3"),
                QStringLiteral("暗杠后从岭上摸牌：岭上剩 3"));
        checkEq(QString::number(mk.tilesLeft()), QStringLiteral("70"),
                QStringLiteral("岭上摸牌不消耗牌山（余牌不变）"));

        // 再杠一次 → 岭上剩 2（加杠也走同一条路）
        mk.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"draw","seat":0,"tiles_left":69,"rinshan":false,"tile":"3p","dead_wall_left":3})"),
            nullptr));
        mk.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"draw","seat":0,"tiles_left":69,"rinshan":true,"tile":"7z","dead_wall_left":2})"),
            nullptr));
        checkEq(QString::number(mk.deadWallLeft()), QStringLiteral("2"),
                QStringLiteral("第二次杠后再摸：岭上剩 2"));
        checkEq(QString::number(mk.tilesLeft()), QStringLiteral("69"),
                QStringLiteral("普通摸牌才消耗牌山（余牌 70→69）"));

        // 老服务端（不带这个字段）：保持旧值，不要被打回 0
        mk.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"draw","seat":1,"tiles_left":68,"rinshan":false})"), nullptr));
        checkEq(QString::number(mk.deadWallLeft()), QStringLiteral("2"),
                QStringLiteral("老服务端的摸牌事件（无 dead_wall_left）不得把岭上数打回 0"));

        // 重连全量同步（state）也要带上，否则重连后计数退回旧值
        mk.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"state","phase":"playing","seat":0,"round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","1p","2p","3p","4p","5p","6p","7p","8p","9p","1z"],"melds":[[],[],[],[]],"discards":[[],[],[],[]],"riichi":[false,false,false,false],"furiten":[false,false,false,false],"dora_indicators":["5p","2z"],"tiles_left":68,"dead_wall_left":1})"),
            nullptr));
        checkEq(QString::number(mk.deadWallLeft()), QStringLiteral("1"),
                QStringLiteral("重连全量同步带上岭上剩余数"));
    }

    check(!model.discardSideways(0, 0), QStringLiteral("非宣言牌不横置"));
    check(model.riichiSticks() == 4, QStringLiteral("立直棒 4 根（四家各立直一次）"));
    check(model.riichi(3), QStringLiteral("上家立直状态"));
    check(model.melds(1).size() == 1, QStringLiteral("下家 1 个副露"));
    checkEq(model.melds(1).value(0).kind, QStringLiteral("pon"), QStringLiteral("副露类型 pon"));
    check(model.melds(2).size() == 1, QStringLiteral("对家 1 个副露"));
    check(model.melds(2).value(0).isConcealed(), QStringLiteral("对家暗杠"));
    check(model.doraIndicators().size() == 3, QStringLiteral("宝牌指示牌 3 张"));
    checkEq(model.roundText(), QStringLiteral("東2局 1本場"), QStringLiteral("局数文本"));
    check(model.tilesLeft() == 42, QStringLiteral("余牌 42"));
    {
        const QStringList waits = model.waits();
        check(waits.contains(QStringLiteral("9m")), QStringLiteral("听牌提示含 9m"));
        check(waits.contains(QStringLiteral("2p")), QStringLiteral("听牌提示含 2p"));
        check(waits.size() == 2, QStringLiteral("听牌恰好 2 种"));
        // 振听只信服务端下发的状态（本地不做振听推断，见 AGENTS §2.1 / TableModel.h）
        check(!model.furiten(model.mySeat()), QStringLiteral("未振听"));
    }
    check(model.hasAsk(), QStringLiteral("ask 事件已记录"));
    checkEq(model.askKind(), QStringLiteral("turn"), QStringLiteral("ask.kind"));
    check(model.askRemainMs() > 0 && model.askRemainMs() <= 15000,
          QStringLiteral("ask 剩余时间在 (0,15000]"));
    model.clearAsk();
    check(!model.hasAsk(), QStringLiteral("clearAsk 生效"));

    // ---------- 4. 出图 ----------
    log << QString() << QStringLiteral("-- 4. 生成 PNG --");
    const QString tilesPng = dir.absoluteFilePath(QStringLiteral("tiles.png"));
    QString err;
    QSize sz;
    if (drawTilesSheet(tilesPng, &err, &sz))
        log << QStringLiteral("[ok]   tiles.png %1 (%2x%3)").arg(tilesPng).arg(sz.width()).arg(sz.height());
    else {
        ++checks;
        failures << QStringLiteral("绘制 tiles.png 失败：%1").arg(err);
        log << QStringLiteral("[FAIL] tiles.png %1").arg(err);
    }

    const QString tablePng = dir.absoluteFilePath(QStringLiteral("table.png"));
    if (drawTableSheet(tablePng, &model, &err, &sz))
        log << QStringLiteral("[ok]   table.png %1 (%2x%3)").arg(tablePng).arg(sz.width()).arg(sz.height());
    else {
        ++checks;
        failures << QStringLiteral("绘制 table.png 失败：%1").arg(err);
        log << QStringLiteral("[FAIL] table.png %1").arg(err);
    }

    // 文件确实存在且非空
    for (const QString& f : { tilesPng, tablePng }) {
        QFile file(f);
        check(file.exists() && file.size() > 1024,
              QStringLiteral("%1 已生成且非空").arg(QFileInfo(f).fileName()));
    }

    // ---------- 回归：立直按钮的连点（报障「重复点击立直按钮行为异常」）----------
    // 三个不变量：
    //   ① 立直是个模式，点两下 = 进→退；**退出来之后标题必须还原**，
    //      否则玩家看着「立直：请点击要打出的宣言牌」点手牌，实际发出的是普通弃牌；
    //   ② 每个动作都必须带 ask_id（服务端靠它丢弃过期/重复回包）；
    //   ③ 提交之后询问栏必须锁死：按钮没了、actionCmd() 返回空对象，
    //      于是连点第二下发不出任何东西。
    {
        ActionBar ab;
        ab.setAsk(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"ask","ask_id":77,"seat":0,"kind":"turn","deadline_ms":15000,"tiles_left":69,)"
            R"("options":[{"type":"discard","tiles":["1m","4m","7p"]},{"type":"riichi","tiles":["4m","7p"]}]})"),
            nullptr));

        checkEq(QString::number(ab.askId()), QStringLiteral("77"),
                QStringLiteral("ActionBar 记住本询 ask_id"));
        const QJsonObject rc = ab.riichiCmd(QStringLiteral("4m"));
        checkEq(rc.value(QStringLiteral("type")).toString(), QStringLiteral("riichi"),
                QStringLiteral("riichiCmd 组出的 type"));
        checkEq(rc.value(QStringLiteral("tile")).toString(), QStringLiteral("4m"),
                QStringLiteral("riichiCmd 组出的 tile"));
        checkEq(QString::number(rc.value(QStringLiteral("ask_id")).toInt()), QStringLiteral("77"),
                QStringLiteral("立直回包必须带 ask_id（服务端据此丢弃重复/过期包）"));
        checkEq(QString::number(ab.discardCmd(QStringLiteral("1m")).value(QStringLiteral("ask_id")).toInt()),
                QStringLiteral("77"), QStringLiteral("普通出牌回包也必须带 ask_id"));

        QPushButton* rb = ab.buttonForTest(QStringLiteral("立直"));
        check(rb != nullptr, QStringLiteral("询问栏里有「立直」按钮"));
        if (rb) {
            check(rb->isCheckable(), QStringLiteral("「立直」按钮应为 checkable（状态可见，减少连点）"));
            rb->click();
            check(ab.riichiMode(), QStringLiteral("第一次点击进入立直模式"));
            check(ab.titleTextForTest().contains(QStringLiteral("立直")),
                  QStringLiteral("立直模式下标题提示宣言：%1").arg(ab.titleTextForTest()));
            check(rb->isChecked(), QStringLiteral("立直按钮勾选态跟随模式"));

            rb->click();
            check(!ab.riichiMode(), QStringLiteral("第二次点击退出立直模式"));
            check(rb->isChecked() == false, QStringLiteral("退出后按钮不勾选"));
            check(!ab.titleTextForTest().contains(QStringLiteral("立直")),
                  QStringLiteral("退出立直模式后标题必须还原（不得再写着立直），实际：%1")
                      .arg(ab.titleTextForTest()));

            rb->click();   // 再点一次仍要能正常进入（连点多次不残留怪状态）
            check(ab.riichiMode(), QStringLiteral("再次点击仍能进入立直模式"));
        }

        // ③ 提交后锁定
        ab.clearAsk();
        check(ab.riichiCmd(QStringLiteral("4m")).isEmpty(),
              QStringLiteral("提交后 riichiCmd 必须返回空对象（连点第二下发不出动作）"));
        check(ab.discardCmd(QStringLiteral("1m")).isEmpty(),
              QStringLiteral("提交后 discardCmd 必须返回空对象"));
        check(ab.buttonForTest(QStringLiteral("立直")) == nullptr,
              QStringLiteral("提交后「立直」按钮已销毁"));
    }

    // ---------- 回归：牌桌外的三个自动开关（自动胡了 / 不吃碰杠 / 自动摸切）----------
    // 三条不变量：
    //   ① 三个按钮的文案全部来自语言文件、都是 checkable（状态看得见）；
    //   ② **和牌永远截获自动动作**：options 里有 tsumo/ron 时，「自动摸切」不许把那张
    //      能胡的牌打出去、「不吃碰杠」不许把荣和 pass 掉（这正是用户点名的要求）；
    //   ③ reset() 把三个开关全部关掉 —— 每小局结束就调它。
    {
        // 轮到自己：能打 1m/4m，其中 4m 是刚摸到的
        const QJsonObject turnPlain = parseEv(
            R"({"ev":"ask","ask_id":5,"seat":0,"kind":"turn","deadline_ms":15000,)"
            R"("options":[{"type":"discard","tiles":["1m","4m"]},{"type":"riichi","tiles":["4m"]}]})");
        // 轮到自己，而且摸到的牌就是和牌张
        const QJsonObject turnWin = parseEv(
            R"({"ev":"ask","ask_id":6,"seat":0,"kind":"turn","deadline_ms":15000,)"
            R"("options":[{"type":"discard","tiles":["1m","4m"]},{"type":"tsumo"}]})");
        // 别人的舍张：可以荣和，也可以碰
        const QJsonObject claimRon = parseEv(
            R"({"ev":"ask","ask_id":7,"seat":0,"kind":"claim","deadline_ms":3000,)"
            R"("options":[{"type":"ron"},{"type":"pon"},{"type":"pass"}]})");
        // 别人的舍张：只能碰 / 吃 / 过
        const QJsonObject claimPon = parseEv(
            R"({"ev":"ask","ask_id":8,"seat":0,"kind":"claim","deadline_ms":3000,)"
            R"("options":[{"type":"pon"},{"type":"pass"}]})");

        const autopolicy::Flags off;
        check(autopolicy::decide(off, turnPlain, QStringLiteral("turn"), QStringLiteral("4m")).isEmpty(),
              QStringLiteral("三个开关都关时不得替玩家动作"));

        autopolicy::Flags ts;
        ts.autoTsumogiri = true;
        checkEq(autopolicy::decide(ts, turnPlain, QStringLiteral("turn"), QStringLiteral("4m"))
                    .value(QStringLiteral("tile")).toString(),
                QStringLiteral("4m"), QStringLiteral("自动摸切打的是刚摸到的那张"));
        checkEq(autopolicy::decide(ts, turnPlain, QStringLiteral("turn"), QStringLiteral("9s"))
                    .value(QStringLiteral("tile")).toString(),
                QStringLiteral("1m"),
                QStringLiteral("摸到的牌不在可打列表里时退化为第一张（不能发空动作）"));
        check(autopolicy::decide(ts, turnWin, QStringLiteral("turn"), QStringLiteral("4m")).isEmpty(),
              QStringLiteral("自动摸切必须被**自摸**截获：绝不切出能胡的那张牌"));
        check(autopolicy::decide(ts, claimPon, QStringLiteral("claim"), QStringLiteral("1m")).isEmpty(),
              QStringLiteral("不是自己的回合，自动摸切不许动"));

        autopolicy::Flags nc;
        nc.noCall = true;
        check(autopolicy::decide(nc, claimRon, QStringLiteral("claim"), QString()).isEmpty(),
              QStringLiteral("不吃碰杠必须被**荣和**截获：绝不把到手的荣和 pass 掉"));
        checkEq(autopolicy::decide(nc, claimPon, QStringLiteral("claim"), QString())
                    .value(QStringLiteral("type")).toString(),
                QStringLiteral("pass"), QStringLiteral("不吃碰杠：没有荣和时一律 pass"));

        autopolicy::Flags aw;
        aw.autoWin = true;
        checkEq(autopolicy::decide(aw, turnWin, QStringLiteral("turn"), QStringLiteral("4m"))
                    .value(QStringLiteral("type")).toString(),
                QStringLiteral("tsumo"), QStringLiteral("自动胡了：能自摸就自摸"));
        checkEq(autopolicy::decide(aw, claimRon, QStringLiteral("claim"), QString())
                    .value(QStringLiteral("type")).toString(),
                QStringLiteral("ron"), QStringLiteral("自动胡了：能荣和就荣和"));

        autopolicy::Flags both;
        both.autoWin = true;
        both.autoTsumogiri = true;
        checkEq(autopolicy::decide(both, turnWin, QStringLiteral("turn"), QStringLiteral("4m"))
                    .value(QStringLiteral("type")).toString(),
                QStringLiteral("tsumo"),
                QStringLiteral("自动胡了与自动摸切同开时，和牌优先"));

        // ① / ③ 三个按钮本体
        AutoBar bar;
        checkEq(QString::number(bar.labelsForTest().size()), QStringLiteral("3"),
                QStringLiteral("牌桌外应有三个自动开关"));
        check(bar.labelsForTest().contains(lang::t(QStringLiteral("ui.auto.win")))
                  && bar.labelsForTest().contains(lang::t(QStringLiteral("ui.auto.no_call")))
                  && bar.labelsForTest().contains(lang::t(QStringLiteral("ui.auto.tsumogiri"))),
              QStringLiteral("三个开关的文案必须来自语言文件：%1")
                  .arg(bar.labelsForTest().join(QStringLiteral("/"))));
        check(!bar.flags().any(), QStringLiteral("开关初始状态都是关"));
        QPushButton* winBtn = bar.buttonForTest(lang::t(QStringLiteral("ui.auto.win")));
        check(winBtn != nullptr && winBtn->isCheckable(),
              QStringLiteral("「自动胡了」应是可切换（checkable）按钮"));
        if (winBtn) {
            winBtn->click();
            check(bar.flags().autoWin, QStringLiteral("点一下就打开"));
            check(winBtn->isChecked(), QStringLiteral("按钮勾选态跟随开关"));
            bar.reset();
            check(!bar.flags().any() && !winBtn->isChecked(),
                  QStringLiteral("reset() 后三个开关与按钮勾选态全部复位（每小局结束要调它）"));
        }
    }

    // ---------- 端到端接线：开关 → 策略 → ActionBar 组包 → sendCommand ----------
    // 用命令钩子抓**真正发出去的报文**，把「自动应答」整条链子钉住：
    //   ① 自动摸切发的是 `{"cmd":"action","type":"discard","tile":"<摸到的那张>","ask_id":…}`；
    //   ② 摸到的牌能自摸时，自动摸切**一个动作都不许发**（用户点名的截获）；
    //   ③ 自动胡了才发 tsumo / ron；
    //   ④ 每小局结束（round_end）三个开关立刻全关。
    {
        MainWindow w;
        // 构造时 showLobby() 会把大厅弹窗 show 出来，自检里立刻收掉
        if (LobbyDialog* dlg = w.findChild<LobbyDialog*>())
            dlg->hide();
        w.setAutoAnswer(false);

        QJsonObject lastAction;
        int actionCount = 0;
        w.setCommandTapForTest([&](const QJsonObject& o) {
            if (o.value(QStringLiteral("cmd")).toString() == QLatin1String("action")) {
                lastAction = o;
                ++actionCount;
            }
        });

        AutoBar* bar = w.autoBarForTest();
        check(bar != nullptr, QStringLiteral("牌桌页上挂了那排自动开关"));
        if (bar) {
            const QJsonObject turnPlain = parseEv(
                R"({"ev":"ask","ask_id":5,"seat":0,"kind":"turn","deadline_ms":15000,)"
                R"("options":[{"type":"discard","tiles":["1m","4m"]}]})");
            const QJsonObject turnWin = parseEv(
                R"({"ev":"ask","ask_id":6,"seat":0,"kind":"turn","deadline_ms":15000,)"
                R"("options":[{"type":"discard","tiles":["1m","4m"]},{"type":"tsumo"}]})");

            // 先把「我是 0 号座、本局开始」告诉模型：摸到的牌只有在自己座位上才单独记着
            // （TableModel 需要 m_hasSeat=true 才会把 draw 的牌存进 drawnTile()）。
            w.feedEventForTest(parseEv(
                R"({"ev":"round_start","seat":0,"dealer":0,"scores":[25000,25000,25000,25000],)"
                R"("hand":["1m","2m","3m","4m","5m","6m","7m","8m","9m","1p","2p","3p","4p"],)"
                R"("round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0}})"));
            w.feedEventForTest(parseEv(
                R"({"ev":"draw","seat":0,"tile":"4m","tiles_left":69,"dead_wall_left":4})"));

            // ① 自动摸切
            if (QPushButton* tb = bar->buttonForTest(lang::t(QStringLiteral("ui.auto.tsumogiri"))))
                tb->click();
            w.feedEventForTest(turnPlain);
            pumpFor(700);
            checkEq(lastAction.value(QStringLiteral("type")).toString(), QStringLiteral("discard"),
                    QStringLiteral("自动摸切真的发出了 discard"));
            checkEq(lastAction.value(QStringLiteral("tile")).toString(), QStringLiteral("4m"),
                    QStringLiteral("自动摸切发的是刚摸到的那张"));
            checkEq(QString::number(lastAction.value(QStringLiteral("ask_id")).toInt()),
                    QStringLiteral("5"),
                    QStringLiteral("自动应答的报文同样必须带 ask_id（服务端据此丢弃过期包）"));

            // ② 和牌截获：这条询问能自摸 → 自动摸切不许发任何动作
            const int before = actionCount;
            w.feedEventForTest(turnWin);
            pumpFor(700);
            checkEq(QString::number(actionCount), QString::number(before),
                    QStringLiteral("有自摸机会时自动摸切**一个动作都不许发**（不得切出能胡的牌）"));

            // ③ 自动胡了 → 发 tsumo
            if (QPushButton* wb = bar->buttonForTest(lang::t(QStringLiteral("ui.auto.win"))))
                wb->click();
            pumpFor(700);
            checkEq(lastAction.value(QStringLiteral("type")).toString(), QStringLiteral("tsumo"),
                    QStringLiteral("自动胡了把上一条被截获的自摸发出去"));

            // ④ 每小局结束：三个开关立刻全关
            if (QPushButton* nb = bar->buttonForTest(lang::t(QStringLiteral("ui.auto.no_call"))))
                nb->click();
            check(bar->flags().any(), QStringLiteral("小局结束前开关是开着的"));
            w.feedEventForTest(parseEv(R"({"ev":"round_end","reason":"exhaustive"})"));
            check(!bar->flags().any(),
                  QStringLiteral("round_end 后三个自动开关立刻全部关闭（用户要求）"));
        }
    }

    // ---------- 回归：个人设置（settings.json）与材质包 ----------
    // 两条硬要求：① 设置文件缺/坏/某个键不可用 → **不报错**，用缺省值重新生成；
    //             ② 材质包缺/坏/内容格式不对 → **只回退默认素材**，设置里的路径原样保留。
    {
        const QString dir = outDir + QStringLiteral("/settings_test");
        QDir().mkpath(dir);
        const QString path = dir + QStringLiteral("/settings.json");
        QFile::remove(path);

        // 测试夹具的读写：**必须确认成功**。写不进去的话后面的断言就是"静默地什么都没测"
        // —— 那是这个项目最讨厌的一类假绿（Qt 6.11 起 `QFile::open` 是 `[[nodiscard]]`，
        //    忽略返回值会直接报警告，这次正好借它把夹具补严）。
        auto writeFixture = [&](const QString& p, const QByteArray& data) {
            QFile f(p);
            if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
                check(false, QStringLiteral("测试夹具：写不进 %1").arg(p));
                return;
            }
            f.write(data);
            f.close();
        };
        auto readFixture = [&](const QString& p) -> QByteArray {
            QFile f(p);
            if (!f.open(QIODevice::ReadOnly)) {
                check(false, QStringLiteral("测试夹具：读不出 %1").arg(p));
                return QByteArray();
            }
            return f.readAll();
        };

        // ① 文件不存在 → 生成缺省文件，并回报一句说明
        QString note;
        QStringList repaired;
        Settings s1 = Settings::load(path, &repaired, &note);
        check(QFile::exists(path), QStringLiteral("设置：文件不存在时会生成一份"));
        checkEq(s1.host, QStringLiteral("127.0.0.1"), QStringLiteral("设置：缺省地址"));
        checkEq(QString::number(s1.port), QStringLiteral("10086"), QStringLiteral("设置：缺省端口"));
        checkEq(note, QStringLiteral("settings_missing"), QStringLiteral("设置：回报「文件不存在」"));

        // ② 好文件往返
        s1.host = QStringLiteral("10.0.0.7");
        s1.port = 12345;
        s1.name = QStringLiteral("小明");
        s1.pack = QStringLiteral("D:/packs/demo.zip");
        check(s1.save(path), QStringLiteral("设置：能写回文件"));
        Settings s2 = Settings::load(path, &repaired, &note);
        checkEq(s2.host, QStringLiteral("10.0.0.7"), QStringLiteral("设置：地址往返"));
        checkEq(QString::number(s2.port), QStringLiteral("12345"), QStringLiteral("设置：端口往返"));
        checkEq(s2.name, QStringLiteral("小明"), QStringLiteral("设置：昵称往返"));
        checkEq(s2.pack, QStringLiteral("D:/packs/demo.zip"), QStringLiteral("设置：材质包路径往返"));
        check(repaired.isEmpty() && note.isEmpty(), QStringLiteral("设置：好文件不报任何问题"));

        // ③ 坏 JSON → 缺省值重新生成（原文件备份成 .bak，不直接扔）
        writeFixture(path, QByteArrayLiteral("{ this is not json "));
        QStringList rep3;
        Settings s3 = Settings::load(path, &rep3, &note);
        checkEq(s3.host, QStringLiteral("127.0.0.1"), QStringLiteral("设置：坏 JSON → 回缺省"));
        checkEq(note, QStringLiteral("settings_broken"), QStringLiteral("设置：回报「文件坏了」"));
        check(QFile::exists(path + QStringLiteral(".bak")), QStringLiteral("设置：坏文件先备份成 .bak"));
        check(QJsonDocument::fromJson(readFixture(path)).isObject(),
              QStringLiteral("设置：重新生成的是合法 JSON"));

        // ④ 某个键不可用 → **只重置那一个键**，其余保留；认不出的键不丢
        {
            QJsonObject o;
            o.insert(QStringLiteral("host"), QStringLiteral("  192.168.1.9  "));
            o.insert(QStringLiteral("port"), 999999);          // 越界
            o.insert(QStringLiteral("name"), QStringLiteral("这个昵称实在是太长了超过二十四个字所以不合法不合法"));
            o.insert(QStringLiteral("pack"), QStringLiteral("ok.zip"));
            o.insert(QStringLiteral("future_key"), 42);        // 别的版本写的
            writeFixture(path, QJsonDocument(o).toJson());
        }
        QStringList rep4;
        Settings s4 = Settings::load(path, &rep4, &note);
        checkEq(s4.host, QStringLiteral("192.168.1.9"), QStringLiteral("设置：地址两端空白被裁掉"));
        checkEq(QString::number(s4.port), QStringLiteral("10086"), QStringLiteral("设置：越界端口回缺省"));
        checkEq(s4.name, QString(), QStringLiteral("设置：超长昵称回缺省（空 = 用默认名）"));
        checkEq(s4.pack, QStringLiteral("ok.zip"), QStringLiteral("设置：好的键不受别的键影响"));
        check(rep4.contains(QStringLiteral("port")) && rep4.contains(QStringLiteral("name")),
              QStringLiteral("设置：回报被重置的键（%1）").arg(rep4.join(QLatin1Char(','))));
        check(!rep4.contains(QStringLiteral("host")), QStringLiteral("设置：合法键不进重置清单"));
        checkEq(note, QStringLiteral("settings_repaired"), QStringLiteral("设置：回报「有键被重置」"));
        {
            const QJsonObject o2 = QJsonDocument::fromJson(readFixture(path)).object();
            checkEq(QString::number(o2.value(QStringLiteral("future_key")).toInt()), QStringLiteral("42"),
                    QStringLiteral("设置：认不出的键原样保留（向前兼容）"));
        }

        // ---------- 材质包 ----------
        // 目录形式：清单 + 牌面（位图）/牌背（矢量）/桌布/立直棒/字体
        const QString pack = dir + QStringLiteral("/pack");
        QDir().mkpath(pack + QStringLiteral("/assets/tiles"));
        QDir().mkpath(pack + QStringLiteral("/assets/cloth"));
        QDir().mkpath(pack + QStringLiteral("/assets/stick"));
        QDir().mkpath(pack + QStringLiteral("/assets/font"));
        {
            // 1m 用**位图**（验证 png 也能用）；不提供 3m（验证"逐张回退默认素材"）
            QImage img(40, 60, QImage::Format_ARGB32);
            img.fill(QColor(200, 30, 30));
            check(img.save(pack + QStringLiteral("/assets/tiles/1m.png")),
                  QStringLiteral("材质包：测试位图写出成功"));
            check(img.save(pack + QStringLiteral("/assets/cloth/felt.png")),
                  QStringLiteral("材质包：测试桌布写出成功"));
            check(img.save(pack + QStringLiteral("/assets/stick/stick.png")),
                  QStringLiteral("材质包：测试立直棒写出成功"));
            // 牌背的位图（与 1m 同目录、不同颜色）：用来钉住"牌背不能被随便抓一张别的牌顶上"
            QImage backImg(40, 60, QImage::Format_ARGB32);
            backImg.fill(QColor(20, 140, 150));
            check(backImg.save(pack + QStringLiteral("/assets/tiles/back.png")),
                  QStringLiteral("材质包：测试牌背位图写出成功"));
            // 一张**坏**位图：内容是垃圾字节，扩展名却是 png
            writeFixture(pack + QStringLiteral("/assets/tiles/2m.png"),
                         QByteArrayLiteral("not an image at all"));
            // 牌背用 svg（验证"矢量与位图混用"）
            writeFixture(pack + QStringLiteral("/assets/tiles/back.svg"),
                         QByteArrayLiteral("<svg xmlns=\"http://www.w3.org/2000/svg\" "
                                           "viewBox=\"0 0 10 10\">"
                                           "<rect width=\"10\" height=\"10\" fill=\"#123456\"/></svg>"));
            QJsonObject m;
            m.insert(QStringLiteral("name"), QStringLiteral("自检材质包"));
            m.insert(QStringLiteral("tiles"), QStringLiteral("assets/tiles"));
            m.insert(QStringLiteral("cloth"), QStringLiteral("/assets/cloth"));   // 带前导斜杠也要认
            m.insert(QStringLiteral("stick"), QStringLiteral("assets/stick"));
            m.insert(QStringLiteral("font"), QStringLiteral("assets/font"));
            writeFixture(pack + QStringLiteral("/theme.json"), QJsonDocument(m).toJson());
        }
        {
            const Theme::Status st = Theme::instance().load(pack);
            TileRenderer::clearAssetCache();
            check(st.loaded, QStringLiteral("材质包：目录包被接受"));
            checkEq(st.name, QStringLiteral("自检材质包"), QStringLiteral("材质包：读到清单里的名字"));
            check(st.applied.contains(QStringLiteral("tiles"))
                          && st.applied.contains(QStringLiteral("cloth"))
                          && st.applied.contains(QStringLiteral("stick")),
                  QStringLiteral("材质包：生效类别 = %1").arg(st.applied.join(QLatin1Char(','))));
            check(st.problems.contains(QStringLiteral("font_empty")),
                  QStringLiteral("材质包：空的字体目录只影响它自己（%1）")
                      .arg(st.problems.join(QLatin1Char(','))));
            checkEq(TileRenderer::assetSourceForTest(QStringLiteral("1m")),
                    QStringLiteral("pack-raster"), QStringLiteral("材质包：1m 用了包里的位图"));
            checkEq(TileRenderer::assetSourceForTest(QStringLiteral("back")),
                    QStringLiteral("pack-svg"), QStringLiteral("材质包：牌背用了包里的矢量图"));
            checkEq(TileRenderer::assetSourceForTest(QStringLiteral("2m")),
                    QStringLiteral("file-svg"),
                    QStringLiteral("材质包：包里的 2m.png 是坏的 → 回退默认素材"));
            checkEq(TileRenderer::assetSourceForTest(QStringLiteral("3m")),
                    QStringLiteral("file-svg"), QStringLiteral("材质包：没提供的牌回退默认素材"));
            check(!Theme::instance().cloth().isNull() && !Theme::instance().stick().isNull(),
                  QStringLiteral("材质包：桌布与立直棒都已载入"));
            // 牌背位图必须是**牌背自己**那张，不能是同目录里排序靠前/靠后的别的牌
            //（踩过：`tiles` 目录里"取第一张图当牌背"会把 1m 画成牌背）
            {
                const QImage backRaster = Theme::instance().packImage(QStringLiteral("back"));
                check(!backRaster.isNull(), QStringLiteral("材质包：牌背位图取到了"));
                checkEq(backRaster.pixelColor(2, 2).name(), QStringLiteral("#148c96"),
                        QStringLiteral("材质包：牌背位图是 back.png 而不是别的牌"));
                const QImage face = Theme::instance().packImage(QStringLiteral("1m"));
                checkEq(face.pixelColor(2, 2).name(), QStringLiteral("#c81e1e"),
                        QStringLiteral("材质包：1m 位图是它自己那张"));
            }
            check(Theme::instance().fontData().isEmpty(), QStringLiteral("材质包：没给字体就不动 UI 字体"));
            // 非法牌码**绝不**去碰包里的文件（安全闸）
            checkEq(TileRenderer::assetSourceForTest(QStringLiteral("../../etc/passwd")),
                    QStringLiteral("procedural"), QStringLiteral("材质包：非法牌码一律程序化绘制"));
        }

        // 清单里的路径不安全 → 该键忽略（不碰包外文件）
        {
            QJsonObject m;
            m.insert(QStringLiteral("tiles"), QStringLiteral("../../outside"));
            m.insert(QStringLiteral("cloth"), QStringLiteral("C:/Windows"));
            writeFixture(pack + QStringLiteral("/theme.json"), QJsonDocument(m).toJson());
            const Theme::Status st = Theme::instance().load(pack);
            TileRenderer::clearAssetCache();
            check(st.loaded, QStringLiteral("材质包：清单还是合法的（只是路径不能用）"));
            check(st.problems.contains(QStringLiteral("tiles_bad_path"))
                          && st.problems.contains(QStringLiteral("cloth_bad_path")),
                  QStringLiteral("材质包：绝对路径/上跳一律拒（%1）")
                      .arg(st.problems.join(QLatin1Char(','))));
            checkEq(TileRenderer::assetSourceForTest(QStringLiteral("1m")),
                    QStringLiteral("file-svg"), QStringLiteral("材质包：被拒的键回退默认素材"));
        }

        // 清单缺失 / 不是 JSON 对象 / 路径不存在 → 只回退，不崩
        {
            QFile::remove(pack + QStringLiteral("/theme.json"));
            const Theme::Status st = Theme::instance().load(pack);
            check(!st.loaded && st.problems.contains(QStringLiteral("manifest_missing")),
                  QStringLiteral("材质包：没有 theme.json → 作废并回报"));
            TileRenderer::clearAssetCache();
            checkEq(TileRenderer::assetSourceForTest(QStringLiteral("1m")), QStringLiteral("file-svg"),
                    QStringLiteral("材质包：作废后全用默认素材"));
        }
        {
            // 是合法 JSON，但不是对象
            writeFixture(pack + QStringLiteral("/theme.json"), QByteArrayLiteral("[1,2,3]"));
            const Theme::Status st = Theme::instance().load(pack);
            check(!st.loaded && st.problems.contains(QStringLiteral("manifest_broken")),
                  QStringLiteral("材质包：清单不是 JSON 对象 → 作废"));
        }
        {
            const Theme::Status st = Theme::instance().load(dir + QStringLiteral("/no_such_pack"));
            check(!st.loaded && st.problems.contains(QStringLiteral("pack_missing")),
                  QStringLiteral("材质包：路径不存在 → 回退并回报"));
        }
        {   // 清空 = 全默认（后面的渲染断言也用这个状态）
            const Theme::Status st = Theme::instance().load(QString());
            TileRenderer::clearAssetCache();
            check(!st.loaded && st.problems.isEmpty(), QStringLiteral("材质包：留空 = 用默认素材"));
        }
    }

    // ---------- 回归：天鳳牌譜导出（tenhou.net/6 的 #json= 形式）----------
    // 格式参考 github.com/wuye999/tenhou（`生成2.py`）。断言钉的是**结构**与**编码**：
    // 小局元素个数、每家的配牌 13 张、摸切 = 60、立直 = "r…"、牌号落在天鳳的编码集合里。
    {
        auto entry = [](int seq, int to, const QJsonObject& body) {
            QJsonObject o;
            o.insert(QStringLiteral("seq"), seq);
            o.insert(QStringLiteral("t"), seq * 10);
            o.insert(QStringLiteral("to"), to);
            o.insert(QStringLiteral("b"), body);
            return o;
        };
        auto obj = [](std::initializer_list<QPair<QString, QJsonValue>> kv) {
            QJsonObject o;
            for (const auto& p : kv) {
                o.insert(p.first, p.second);
            }
            return o;
        };
        // 牌号编码：11..19 / 21..29 / 31..39 / 41..47，赤五 51/52/53
        checkEq(QString::number(TenhouLog::tileNumber(QStringLiteral("1m"))), QStringLiteral("11"),
                QStringLiteral("牌谱：1m → 11"));
        checkEq(QString::number(TenhouLog::tileNumber(QStringLiteral("9s"))), QStringLiteral("39"),
                QStringLiteral("牌谱：9s → 39"));
        checkEq(QString::number(TenhouLog::tileNumber(QStringLiteral("7z"))), QStringLiteral("47"),
                QStringLiteral("牌谱：7z（中）→ 47"));
        checkEq(QString::number(TenhouLog::tileNumber(QStringLiteral("0p"))), QStringLiteral("52"),
                QStringLiteral("牌谱：赤五筒 → 52"));
        checkEq(QString::number(TenhouLog::tileNumber(QStringLiteral("5p"))), QStringLiteral("25"),
                QStringLiteral("牌谱：普通五筒 → 25（赤与不赤是两套码）"));
        checkEq(QString::number(TenhouLog::tileNumber(QStringLiteral("nope"))), QStringLiteral("-1"),
                QStringLiteral("牌谱：认不出的牌码 → -1"));

        // 用手写的一小局记录跑一遍：配牌 13×4、一次摸牌、一次手切、一次摸切、一次立直手切
        ReplayModel tp;
        QJsonArray wall3;
        for (int i = 0; i < 136; ++i) {
            wall3.append(i);
        }
        QJsonObject meta3;
        meta3.insert(QStringLiteral("id"), QStringLiteral("TENHOU1"));
        meta3.insert(QStringLiteral("entries"), 8);
        meta3.insert(QStringLiteral("names"), QJsonArray{QStringLiteral("甲"), QStringLiteral("乙"),
                                                         QStringLiteral("丙"), QStringLiteral("丁")});
        meta3.insert(QStringLiteral("walls"), QJsonArray{wall3});
        meta3.insert(QStringLiteral("round_at"), QJsonArray{0});
        tp.setMeta(meta3);
        QJsonArray arr3;
        arr3.append(entry(0, -1, obj({{QStringLiteral("ev"), QStringLiteral("replay_round")},
                                      {QStringLiteral("index"), 0},
                                      {QStringLiteral("bakaze"), QStringLiteral("E")},
                                      {QStringLiteral("kyoku"), 1},
                                      {QStringLiteral("honba"), 0},
                                      {QStringLiteral("dealer"), 0},
                                      {QStringLiteral("wall"), wall3}})));
        const QStringList deal { QStringLiteral("1m"), QStringLiteral("2m"), QStringLiteral("3m"),
                                 QStringLiteral("4m"), QStringLiteral("5m"), QStringLiteral("6m"),
                                 QStringLiteral("7m"), QStringLiteral("8m"), QStringLiteral("9m"),
                                 QStringLiteral("1p"), QStringLiteral("2p"), QStringLiteral("3p"),
                                 QStringLiteral("4p") };
        for (int s = 0; s < 4; ++s) {
            QJsonArray hand;
            for (const QString& t : deal) {
                hand.append(t);
            }
            if (s == 0) {
                hand.append(QStringLiteral("9s"));   // 庄家第 14 张 → 算他的第一次摸牌
            }
            arr3.append(entry(1 + s, s, obj({{QStringLiteral("ev"), QStringLiteral("round_start")},
                                             {QStringLiteral("seat"), s},
                                             {QStringLiteral("hand"), hand},
                                             {QStringLiteral("round"),
                                              obj({{QStringLiteral("bakaze"), QStringLiteral("E")},
                                                   {QStringLiteral("kyoku"), 1},
                                                   {QStringLiteral("riichi_sticks"), 1}})},
                                             {QStringLiteral("scores"),
                                              QJsonArray{25000, 25000, 25000, 25000}},
                                             {QStringLiteral("dora_indicators"),
                                              QJsonArray{QStringLiteral("1p")}}})));
        }
        // 座位 1：摸 5s → 手切 3p → 摸 2s → 摸切 → 摸 4s → 立直手切 4s
        arr3.append(entry(5, 1, obj({{QStringLiteral("ev"), QStringLiteral("draw")},
                                     {QStringLiteral("seat"), 1},
                                     {QStringLiteral("tile"), QStringLiteral("5s")}})));
        arr3.append(entry(6, -1, obj({{QStringLiteral("ev"), QStringLiteral("discard")},
                                      {QStringLiteral("seat"), 1},
                                      {QStringLiteral("tile"), QStringLiteral("3p")},
                                      {QStringLiteral("tsumogiri"), false},
                                      {QStringLiteral("riichi"), false}})));
        arr3.append(entry(7, 1, obj({{QStringLiteral("ev"), QStringLiteral("draw")},
                                     {QStringLiteral("seat"), 1},
                                     {QStringLiteral("tile"), QStringLiteral("2s")}})));
        arr3.append(entry(8, -1, obj({{QStringLiteral("ev"), QStringLiteral("discard")},
                                      {QStringLiteral("seat"), 1},
                                      {QStringLiteral("tile"), QStringLiteral("2s")},
                                      {QStringLiteral("tsumogiri"), true},
                                      {QStringLiteral("riichi"), false}})));
        arr3.append(entry(9, 1, obj({{QStringLiteral("ev"), QStringLiteral("draw")},
                                     {QStringLiteral("seat"), 1},
                                     {QStringLiteral("tile"), QStringLiteral("4s")}})));
        arr3.append(entry(10, -1, obj({{QStringLiteral("ev"), QStringLiteral("discard")},
                                       {QStringLiteral("seat"), 1},
                                       {QStringLiteral("tile"), QStringLiteral("4s")},
                                       {QStringLiteral("tsumogiri"), false},
                                       {QStringLiteral("riichi"), true}})));
        arr3.append(entry(11, -1, obj({{QStringLiteral("ev"), QStringLiteral("agari")},
                                       {QStringLiteral("winner"), 1},
                                       {QStringLiteral("from"), 0},
                                       {QStringLiteral("ura_indicators"),
                                        QJsonArray{QStringLiteral("9m")}}})));
        tp.addEntries(arr3);
        tp.build();

        const TenhouLog::Result tr = TenhouLog::build(tp);
        check(tr.ok, QStringLiteral("牌谱：导出成功"));
        checkEq(QString::number(tr.rounds), QStringLiteral("1"), QStringLiteral("牌谱：导出 1 个小局"));
        check(tr.url.startsWith(QStringLiteral("https://tenhou.net/6/#json=")),
              QStringLiteral("牌谱：链接前缀正确"));
        const QJsonObject root = QJsonDocument::fromJson(tr.json.toUtf8()).object();
        check(!root.isEmpty(), QStringLiteral("牌谱：JSON 可解析"));
        checkEq(QString::number(root.value(QStringLiteral("name")).toArray().size()),
                QStringLiteral("4"), QStringLiteral("牌谱：四家名字"));
        const QJsonArray logs = root.value(QStringLiteral("log")).toArray();
        checkEq(QString::number(logs.size()), QStringLiteral("1"), QStringLiteral("牌谱：log 有 1 局"));
        const QJsonArray g = logs.at(0).toArray();
        checkEq(QString::number(g.size()), QStringLiteral("17"),
                QStringLiteral("牌谱：小局元素个数（4 头部 + 4×3 家 + 1 结果）"));
        // 头部：[场局次, 本场, 供託] = 東1局0本場1供託
        const QJsonArray head = g.at(0).toArray();
        checkEq(QString::number(head.at(0).toInt()), QStringLiteral("0"),
                QStringLiteral("牌谱：東1局 → 场局次 0"));
        checkEq(QString::number(head.at(1).toInt()), QStringLiteral("0"), QStringLiteral("牌谱：本场"));
        checkEq(QString::number(head.at(2).toInt()), QStringLiteral("1"), QStringLiteral("牌谱：供託"));
        checkEq(QString::number(g.at(1).toArray().size()), QStringLiteral("4"),
                QStringLiteral("牌谱：四家点数"));
        checkEq(QString::number(g.at(2).toArray().at(0).toInt()), QStringLiteral("21"),
                QStringLiteral("牌谱：表宝牌指示牌 1p → 21"));
        checkEq(QString::number(g.at(3).toArray().at(0).toInt()), QStringLiteral("19"),
                QStringLiteral("牌谱：里宝牌指示牌 9m → 19"));
        for (int s = 0; s < 4; ++s) {
            checkEq(QString::number(g.at(4 + s).toArray().size()), QStringLiteral("13"),
                    QStringLiteral("牌谱：座位 %1 配牌 13 张").arg(s));
        }
        // 取牌：庄家的第 14 张算第一次摸牌；座位 1 摸了 3 张
        checkEq(QString::number(g.at(8).toArray().size()), QStringLiteral("1"),
                QStringLiteral("牌谱：庄家第一次摸牌 = 配牌那张第 14 张"));
        checkEq(QString::number(g.at(8).toArray().at(0).toInt()), QStringLiteral("39"),
                QStringLiteral("牌谱：庄家摸到 9s → 39"));
        checkEq(QString::number(g.at(9).toArray().size()), QStringLiteral("3"),
                QStringLiteral("牌谱：座位 1 摸了 3 张"));
        // 出牌：手切 = 牌号；摸切 = 60；立直手切 = "r<牌号>"
        const QJsonArray d1 = g.at(13).toArray();
        checkEq(QString::number(d1.size()), QStringLiteral("3"),
                QStringLiteral("牌谱：座位 1 打出 3 张"));
        checkEq(QString::number(d1.at(0).toInt()), QStringLiteral("23"),
                QStringLiteral("牌谱：手切 3p → 23"));
        checkEq(d1.at(1).toString(), QStringLiteral("60"), QStringLiteral("牌谱：摸切 → 60"));
        checkEq(d1.at(2).toString(), QStringLiteral("r34"),
                QStringLiteral("牌谱：立直手切 4s → r34"));
        const QJsonArray res = g.at(16).toArray();
        checkEq(res.at(0).toString(), QStringLiteral("和了"), QStringLiteral("牌谱：结果 = 和了"));
        checkEq(QString::number(res.at(1).toInt()), QStringLiteral("1"), QStringLiteral("牌谱：和了家"));
        checkEq(QString::number(res.at(2).toInt()), QStringLiteral("0"), QStringLiteral("牌谱：放銃家"));

        // 写文件：内容第一行就是那条链接
        const QString outPath = outDir + QStringLiteral("/tenhou_export.txt");
        QString werr;
        check(TenhouLog::writeFile(tr, outPath, &werr), QStringLiteral("牌谱：能写出文件"));
        {
            QFile f(outPath);
            check(f.open(QIODevice::ReadOnly), QStringLiteral("牌谱：文件可读"));
            const QString first = QString::fromUtf8(f.readLine()).trimmed();
            check(first.startsWith(QStringLiteral("https://tenhou.net/6/#json=")),
                  QStringLiteral("牌谱：文件第一行是链接"));
            f.close();
        }
        // 空记录不能崩，也不能谎报成功
        ReplayModel empty;
        check(!TenhouLog::build(empty).ok, QStringLiteral("牌谱：空记录导出失败（不谎报成功）"));
    }

    // ---------- 回归：语言文件（协议里只有 ASCII 码，中文全在这里）----------
    // 报文里**不再有中文**：役种/打点/流局原因/错误都只发 ASCII 码，客户端查这张表。
    // 所以这里必须钉住两件事：① 语言文件真的被载入（不是"返回 key"）；② 每个码都有对应文案。
    {
        lang::resetMisses();

        // ① 先从内存 JSON 载入一次（不能依赖文件系统），验证解析路径本身
        const bool mini = lang::loadFromJson(
            QStringLiteral(R"({"a.b":"甲乙","a.c":"点炮：%1"})"), QStringLiteral("xx_YY"));
        check(mini, QStringLiteral("loadFromJson 能解析内存 JSON"));
        checkEq(lang::locale(), QStringLiteral("xx_YY"), QStringLiteral("locale 跟随载入"));
        checkEq(lang::t(QStringLiteral("a.b")), QStringLiteral("甲乙"), QStringLiteral("t() 取到文案"));
        checkEq(lang::t(QStringLiteral("a.c"), QStringLiteral("丙")), QStringLiteral("点炮：丙"),
                QStringLiteral("t(key,arg) 替换 %1"));
        check(!lang::has(QStringLiteral("a.zzz")), QStringLiteral("has() 对缺失 key 返回 false"));

        // ② 再载入真正的语言文件（后面的断言都基于它；也验证了"exe 同级 i18n/ → qrc"这条路）
        check(lang::load(), QStringLiteral("语言文件载入成功（exe 同级 i18n/ 或 qrc）"));
        checkEq(lang::locale(), QStringLiteral("zh_CN"), QStringLiteral("缺省语言是 zh_CN"));
        checkEq(QString::number(lang::keyCount()), QStringLiteral("409"),
                QStringLiteral("语言文件条目数（新增 key 必须同步这条断言）"));
        // 建房对话框的「规则预设」三条文案 + 字段标题 + tooltip 必须在语言文件里
        //（服务端加了预设而客户端没跟上时，这条会先红）
        check(!lang::t(QStringLiteral("ui.lobby.preset")).isEmpty()
                  && !lang::t(QStringLiteral("ui.lobby.preset_mleague")).isEmpty()
                  && !lang::t(QStringLiteral("ui.lobby.preset_tenhou")).isEmpty()
                  && !lang::t(QStringLiteral("ui.lobby.preset_majsoul")).isEmpty()
                  && !lang::t(QStringLiteral("ui.lobby.preset_hint")).isEmpty(),
              QStringLiteral("建房对话框的规则预设文案都在语言文件里"));

        // ③ 分族统计：族名就是前缀，加文案时不会悄悄加错族
        QHash<QString, int> family;
        for (const QString& k : lang::keys()) {
            const int dot = k.indexOf(QLatin1Char('.'));
            family[dot > 0 ? k.left(dot) : QStringLiteral("(无前缀)")]++;
        }
        checkEq(QString::number(family.value(QStringLiteral("yaku"))), QStringLiteral("62"),
                QStringLiteral("yaku.* 条目数（58 一般役 + 役牌/场风/自风 + unknown）"));
        checkEq(QString::number(family.value(QStringLiteral("tile"))), QStringLiteral("37"),
                QStringLiteral("tile.* 条目数（37 种牌，赤五走 0m/0p/0s）"));
        checkEq(QString::number(family.value(QStringLiteral("limit"))), QStringLiteral("6"),
                QStringLiteral("limit.* 条目数（满贯/跳满/倍满/三倍满/累计役满/役满）"));
        checkEq(QString::number(family.value(QStringLiteral("reason"))), QStringLiteral("6"),
                QStringLiteral("reason.* 条目数（荒牌/流满/九种九牌/四风/四杠/四家立直）"));
        checkEq(QString::number(family.value(QStringLiteral("error"))), QStringLiteral("11"),
                QStringLiteral("error.* 条目数（含回放的两个码）"));
        checkEq(QString::number(family.value(QStringLiteral("ui"))), QStringLiteral("287"),
                QStringLiteral("ui.* 条目数（界面固定文案；**代码里的中文都在这族里**）"));
        // 回放：文案键必须齐（源码里直接写 lang::t("ui.replay.*")，漏一条就会显示裸键）
        check(!lang::t(QStringLiteral("ui.replay.title")).isEmpty()
                  && !lang::t(QStringLiteral("ui.replay.wall_legend")).isEmpty()
                  && !lang::t(QStringLiteral("ui.replay.wall_seat_legend")).isEmpty()
                  && !lang::t(QStringLiteral("ui.replay.god_hands")).isEmpty()
                  && !lang::t(QStringLiteral("ui.replay.round_result")).isEmpty()
                  && !lang::t(QStringLiteral("ui.replay.ops_title")).isEmpty()
                  && !lang::t(QStringLiteral("ui.replay.op.draw")).isEmpty()
                  && !lang::t(QStringLiteral("error.replay_not_found")).isEmpty(),
              QStringLiteral("回放相关文案都在语言文件里"));

        // ④ 没有任何条目是空串或"复制了 key"（后者 = 表格里写了 key 当文案）
        QStringList emptyish;
        for (const QString& k : lang::keys()) {
            const QString v = lang::t(k);
            if (v.isEmpty() || v == k)
                emptyish << k;
        }
        check(emptyish.isEmpty(),
              QStringLiteral("所有条目都有非空文案，实际异常：%1").arg(emptyish.mid(0, 5).join(QLatin1Char(','))));

        // ⑤ 抽查：牌名 / 役种码 / 参数化役种 / 打点档位 / 流局原因
        checkEq(mj::tileLabel(QStringLiteral("1m")), QStringLiteral("一万"), QStringLiteral("牌名 1m"));
        checkEq(mj::tileLabel(QStringLiteral("0p")), QStringLiteral("赤五筒"), QStringLiteral("牌名 0p（赤五）"));
        checkEq(mj::tileLabel(QStringLiteral("7z")), QStringLiteral("中"), QStringLiteral("牌名 7z"));
        checkEq(lang::yakuText(QStringLiteral("riichi"), QString()), QStringLiteral("立直"),
                QStringLiteral("役种码 riichi → 立直"));
        checkEq(lang::yakuText(QStringLiteral("suuankou_tanki"), QString()), QStringLiteral("四暗刻单骑"),
                QStringLiteral("役种码 suuankou_tanki → 四暗刻单骑"));
        checkEq(lang::yakuText(QStringLiteral("yakuhai"), QStringLiteral("5z")), QStringLiteral("役牌 白"),
                QStringLiteral("参数化役种：役牌 + 牌码 5z"));
        checkEq(lang::yakuText(QStringLiteral("round_wind"), QStringLiteral("1z")), QStringLiteral("场风 东"),
                QStringLiteral("参数化役种：场风 + 牌码 1z"));
        checkEq(lang::yakuText(QStringLiteral("seat_wind"), QStringLiteral("4z")), QStringLiteral("自风 北"),
                QStringLiteral("参数化役种：自风 + 牌码 4z"));

        // ⑥ 认不出的码：**原样显示码**，绝不显示成裸键 `yaku.xxx`（否则看起来像 bug）
        checkEq(lang::yakuText(QStringLiteral("brand_new_yaku"), QString()),
                QStringLiteral("brand_new_yaku"),
                QStringLiteral("未登记的役种码原样显示（不显示成 yaku.xxx）"));
        checkEq(lang::code(QStringLiteral("limit."), QStringLiteral("mangan")), QStringLiteral("满贯"),
                QStringLiteral("打点档位 mangan → 满贯"));
        checkEq(lang::code(QStringLiteral("reason."), QStringLiteral("kyuushu")), QStringLiteral("九种九牌"),
                QStringLiteral("流局原因 kyuushu → 九种九牌"));
        checkEq(lang::code(QStringLiteral("error."), QStringLiteral("unknown_cmd"), QStringLiteral("frobnicate")),
                QStringLiteral("未知命令：frobnicate"),
                QStringLiteral("错误码带参数：error.unknown_cmd + arg"));
        checkEq(lang::code(QStringLiteral("limit."), QStringLiteral("zzz")), QStringLiteral("zzz"),
                QStringLiteral("未登记的打点码原样显示"));
        checkEq(lang::code(QStringLiteral("error."), QStringLiteral("nope"), QStringLiteral("arg")),
                QStringLiteral("nope (arg)"),
                QStringLiteral("未登记的错误码带上参数原样显示（能看出「没翻译」而不是「没收到」）"));
        check(lang::code(QStringLiteral("error."), QString()).isEmpty(),
              QStringLiteral("空码返回空串（调用方据此回退老服务端的中文 msg）"));

        // ⑦ 缺 key 的行为：原样返回 key 并计一次 miss（这是"表格缺口"的探针）
        const int before = lang::misses();
        checkEq(lang::t(QStringLiteral("definitely.missing")), QStringLiteral("definitely.missing"),
                QStringLiteral("缺 key 时原样返回 key"));
        checkEq(QString::number(lang::misses() - before), QStringLiteral("1"),
                QStringLiteral("缺 key 计一次 miss"));
        lang::resetMisses();
    }

    // ---------- 回归：对局记录（回放）的解析与索引 ----------
    // 用**手写的**一份记录（不连网、不依赖服务端）钉住三件事：
    //   ① 小局 / 巡 / 跳转的索引算法；② 136 张牌山的归属映射（PROTOCOL §3.11）；
    //   ③ 上帝视角的四家手牌（按事件流重建，供牌山视图标"这张牌现在在哪"）。
    {
        ReplayModel rp;
        // 牌山：id k 就放 k（便于断言"第 k 张给了谁"）
        QJsonArray wall;
        for (int i = 0; i < 136; ++i) {
            wall.append(i);
        }
        QJsonObject meta;
        meta.insert(QStringLiteral("id"), QStringLiteral("TESTREPLAY"));
        meta.insert(QStringLiteral("entries"), 15);
        meta.insert(QStringLiteral("names"), QJsonArray{QStringLiteral("甲"), QStringLiteral("乙"),
                                                         QStringLiteral("丙"), QStringLiteral("丁")});
        meta.insert(QStringLiteral("walls"), QJsonArray{wall});
        meta.insert(QStringLiteral("round_at"), QJsonArray{0});
        check(rp.setMeta(meta), QStringLiteral("回放：头信息解析成功"));
        checkEq(QString::number(rp.total()), QStringLiteral("15"), QStringLiteral("回放：总步数"));

        auto entry = [](int seq, int to, const QJsonObject& body) {
            QJsonObject o;
            o.insert(QStringLiteral("seq"), seq);
            o.insert(QStringLiteral("t"), seq * 10);
            o.insert(QStringLiteral("to"), to);
            o.insert(QStringLiteral("b"), body);
            return o;
        };
        auto obj = [](std::initializer_list<QPair<QString, QJsonValue>> kv) {
            QJsonObject o;
            for (const auto& p : kv) {
                o.insert(p.first, p.second);
            }
            return o;
        };
        QJsonArray arr;
        // 0: 小局边界（庄家 = 0）
        arr.append(entry(0, -1, obj({{QStringLiteral("ev"), QStringLiteral("replay_round")},
                                     {QStringLiteral("index"), 0},
                                     {QStringLiteral("bakaze"), QStringLiteral("E")},
                                     {QStringLiteral("kyoku"), 1},
                                     {QStringLiteral("dealer"), 0},
                                     {QStringLiteral("wall"), wall}})));
        // 1..4: 四家配牌（各 13 张，这里只用张数占位）。
        // ⚠ 座位 1 特意给**两张 5p**：副露的牌码会重复（碰是三张同码），
        //   而"被鸣的那张来自别人、只该从手里扣掉一张同码"这件事必须能被测出来。
        for (int s = 0; s < 4; ++s) {
            QJsonArray hand;
            if (s == 1) {
                hand.append(QStringLiteral("5p"));
                hand.append(QStringLiteral("5p"));
            }
            for (int i = 0; i < (s == 1 ? 11 : 13); ++i) {
                hand.append(QStringLiteral("1m"));
            }
            arr.append(entry(1 + s, s, obj({{QStringLiteral("ev"), QStringLiteral("round_start")},
                                            {QStringLiteral("seat"), s},
                                            {QStringLiteral("hand"), hand}})));
        }
        // 5..8: 前四巡各摸一张（给 seat 0..3 各一张 5p），第 5 张起进入下一巡
        for (int s = 0; s < 4; ++s) {
            arr.append(entry(5 + s, s, obj({{QStringLiteral("ev"), QStringLiteral("draw")},
                                            {QStringLiteral("seat"), s},
                                            {QStringLiteral("tile"), QStringLiteral("5p")}})));
        }
        // 9: 座位 0 打出一张（配牌里的 1m）
        arr.append(entry(9, -1, obj({{QStringLiteral("ev"), QStringLiteral("discard")},
                                     {QStringLiteral("seat"), 0},
                                     {QStringLiteral("tile"), QStringLiteral("1m")},
                                     {QStringLiteral("tsumogiri"), false}})));
        // 10: 一条聊天（广播）—— 用来验证"按 seq 切分、顺序稳定"
        arr.append(entry(10, -1, obj({{QStringLiteral("ev"), QStringLiteral("chat")},
                                      {QStringLiteral("seat"), 1},
                                      {QStringLiteral("name"), QStringLiteral("乙")},
                                      {QStringLiteral("text"), QStringLiteral("碰！")}})));
        // 11: 座位 3 再摸一张（第 2 巡）
        arr.append(entry(11, 3, obj({{QStringLiteral("ev"), QStringLiteral("draw")},
                                     {QStringLiteral("seat"), 3},
                                     {QStringLiteral("tile"), QStringLiteral("9s")}})));
        // 12: 座位 1 碰（三张同码）—— 只该从手里扣 **2 张**（被鸣的那张来自别人）。
        //     曾经的写法"凡等于 called_tile 就跳过"会把三张全跳过 → 一张都不扣，
        //     副露的牌同时留在手里（用户报障：他家手牌张数不对、副露牌去向不对）。
        arr.append(entry(12, -1, obj({{QStringLiteral("ev"), QStringLiteral("meld")},
                                      {QStringLiteral("seat"), 1},
                                      {QStringLiteral("kind"), QStringLiteral("pon")},
                                      {QStringLiteral("from"), 0},
                                      {QStringLiteral("called_tile"), QStringLiteral("5p")},
                                      {QStringLiteral("tiles"),
                                       QJsonArray{QStringLiteral("5p"), QStringLiteral("5p"),
                                                  QStringLiteral("5p")}}})));
        // 13: 座位 1 加杠 —— 只有第 4 张来自手里（前三张碰的时候已经扣过）
        arr.append(entry(13, -1, obj({{QStringLiteral("ev"), QStringLiteral("meld")},
                                      {QStringLiteral("seat"), 1},
                                      {QStringLiteral("kind"), QStringLiteral("kakan")},
                                      {QStringLiteral("from"), 0},
                                      {QStringLiteral("called_tile"), QStringLiteral("5p")},
                                      {QStringLiteral("tiles"),
                                       QJsonArray{QStringLiteral("5p"), QStringLiteral("5p"),
                                                  QStringLiteral("5p"), QStringLiteral("5p")}}})));
        // 14: 本小局的结算事件（和牌）—— 用来验「每小局结算」的定位
        arr.append(entry(14, -1, obj({{QStringLiteral("ev"), QStringLiteral("agari")},
                                      {QStringLiteral("winner"), 0},
                                      {QStringLiteral("tsumo"), true},
                                      {QStringLiteral("scores"), QJsonArray{30000, 25000, 25000, 20000}}})));
        rp.addEntries(arr);
        rp.build();

        checkEq(QString::number(rp.roundCount()), QStringLiteral("1"), QStringLiteral("回放：小局数"));
        checkEq(QString::number(rp.roundStart(0)), QStringLiteral("0"), QStringLiteral("回放：小局起点"));
        checkEq(QString::number(rp.roundOf(9)), QStringLiteral("0"), QStringLiteral("回放：第 9 步属于第 0 小局"));
        checkEq(QString::number(rp.turnOf(5)), QStringLiteral("1"), QStringLiteral("回放：第一次摸牌在第 1 巡"));
        checkEq(QString::number(rp.turnOf(11)), QStringLiteral("2"),
                QStringLiteral("回放：第 5 次摸牌开始第 2 巡"));
        checkEq(QString::number(rp.turnStart(0, 2)), QStringLiteral("11"), QStringLiteral("回放：第 2 巡的起点"));
        checkEq(QString::number(rp.nextTurnStart(9)), QStringLiteral("11"),
                QStringLiteral("回放：下一巡 = 第 2 巡起点"));
        checkEq(QString::number(rp.prevTurnStart(11)), QStringLiteral("0"),
                QStringLiteral("回放：上一巡 = 第 1 巡起点（第 1 巡含配牌，起点就是小局边界）"));
        checkEq(QString::number(rp.maxTurn(0)), QStringLiteral("2"), QStringLiteral("回放：最大巡目"));

        // 牌山归属：0..47 = 三轮各 4 张（庄家先），48..51 = 各补一张，52 = 庄家第 14 张
        const QVector<ReplayModel::WallSlot>& ws = rp.wallSlots(0);
        checkEq(QString::number(ws.size()), QStringLiteral("136"), QStringLiteral("牌山槽位数 = 136"));
        checkEq(QString::number(ws.at(0).seat), QStringLiteral("0"), QStringLiteral("牌山：第 0 张给庄家"));
        checkEq(QString::number(ws.at(1).seat), QStringLiteral("0"),
                QStringLiteral("牌山：第 1 张还是庄家（一次抓 4 张）"));
        checkEq(QString::number(ws.at(4).seat), QStringLiteral("1"),
                QStringLiteral("牌山：第 4 张给下家（庄家抓完 4 张）"));
        checkEq(QString::number(ws.at(16).seat), QStringLiteral("0"),
                QStringLiteral("牌山：第 16 张又回到庄家（第 2 轮）"));
        checkEq(QString::number(ws.at(48).seat), QStringLiteral("0"), QStringLiteral("牌山：第 48 张 = 补庄家第 13 张"));
        checkEq(QString::number(ws.at(52).seat), QStringLiteral("0"), QStringLiteral("牌山：第 52 张 = 庄家第 14 张"));
        check(ws.at(122).dead && ws.at(135).dead, QStringLiteral("牌山：末尾 14 张是王牌"));
        checkEq(QString::number(ws.at(53).takenAt), QStringLiteral("5"),
                QStringLiteral("牌山：牌局中第 1 张摸牌（k=53）对应第 5 步"));

        // 上帝视角：配牌 13 张 + 摸牌 - 打出
        const ReplayModel::GodState& g = rp.godState(9);
        checkEq(QString::number(g.hands[0].size()), QStringLiteral("13"),
                QStringLiteral("上帝视角：座位 0 打出后剩 13 张（13 配牌 + 1 摸 - 1 打）"));
        checkEq(QString::number(g.rivers[0].size()), QStringLiteral("1"), QStringLiteral("上帝视角：牌河 1 张"));
        checkEq(g.rivers[0].value(0), QStringLiteral("1m"), QStringLiteral("上帝视角：牌河内容"));
        checkEq(QString::number(g.hands[3].size()), QStringLiteral("14"),
                QStringLiteral("上帝视角：座位 3 在第 9 步已摸到 1 张（13 配牌 + 1 摸）"));

        // 操作说明（界面列表用；文案来自语言文件，不该是空的、也不该是裸键）
        check(!rp.describe(0).isEmpty() && !rp.describe(9).isEmpty() && !rp.describe(10).isEmpty(),
              QStringLiteral("回放：每步都有中文说明"));
        check(rp.describe(9).contains(QStringLiteral("1m")),
              QStringLiteral("回放：打牌那步的说明里带牌码：%1").arg(rp.describe(9)));
        check(rp.describe(10).contains(QStringLiteral("碰！")),
              QStringLiteral("回放：聊天那步带正文：%1").arg(rp.describe(10)));
        checkEq(rp.playerName(0), QStringLiteral("甲"), QStringLiteral("回放：玩家名"));
        checkEq(rp.roundText(0), QStringLiteral("E 1局 0本场"), QStringLiteral("回放：小局标题"));
        checkEq(QString::number(rp.stepCount()), QStringLiteral("12"),
                QStringLiteral("回放：步数（四家配牌合成一步）"));
        checkEq(QString::number(rp.stepOfEntry(4)), QStringLiteral("1"),
                QStringLiteral("回放：4 条配牌属于同一步"));
        checkEq(QString::number(rp.roundOpenEnd(0)), QStringLiteral("4"),
                QStringLiteral("回放：小局开头落在配牌完成处（第 4 条）"));

        // 「每小局结算」定位：结算事件（agari / ryuukyoku）是渲染结算界面的**唯一**依据，
        // 靠 round_end 里的布尔字段是拿不到番符/牌面的。
        checkEq(QString::number(rp.roundResultEntry(0)), QStringLiteral("14"),
                QStringLiteral("回放：本小局结算事件可定位（agari）"));
        checkEq(QString::number(rp.roundResultEntry(7)), QStringLiteral("-1"),
                QStringLiteral("回放：越界小局没有结算事件"));

        // 副露扣牌：吃/碰/大明杠的 `tiles` **包含**被鸣的那张（来自别人），只能扣一张同码；
        // 加杠只有第 4 张来自手里。两个都踩过（碰三张同码时"全跳过"→ 一张都不扣）。
        checkEq(QString::number(rp.godState(12).hands[1].size()), QStringLiteral("12"),
                QStringLiteral("上帝视角：碰之后暗牌 14 → 12（只扣手里那两张）"));
        checkEq(QString::number(rp.godState(12).hands[1].count(QStringLiteral("5p"))),
                QStringLiteral("1"),
                QStringLiteral("上帝视角：碰之后手里还剩一张 5p（被鸣的那张不该算在手里）"));
        checkEq(QString::number(rp.godState(13).hands[1].size()), QStringLiteral("11"),
                QStringLiteral("上帝视角：加杠只再扣 1 张（不是 4 张）"));

        // 上帝视角的「刚摸到的那张」要与手牌分开（牌桌要把它单独画一格）
        checkEq(rp.godState(5).drawn[0], QStringLiteral("5p"),
                QStringLiteral("上帝视角：座位 0 刚摸到 5p"));
        checkEq(QString::number(rp.godState(5).hands[0].size()), QStringLiteral("14"),
                QStringLiteral("上帝视角：摸牌后手牌 14 张（含刚摸的那张）"));
        checkEq(rp.godState(9).drawn[0], QString(),
                QStringLiteral("上帝视角：打出后摸牌格空出来"));
        checkEq(rp.godState(8).drawn[3], QStringLiteral("5p"),
                QStringLiteral("上帝视角：座位 3 第 1 张摸到的是 5p"));
        checkEq(rp.godState(11).drawn[3], QStringLiteral("9s"),
                QStringLiteral("上帝视角：座位 3 第 2 张摸到 9s（覆盖前一格）"));

        // ---------- 回归：回放「显示他家手牌」——布局、理牌、出牌动画起点 ----------
        // 这三条是同一个需求的三面：**别家暗牌必须存在模型里**（布局按它算、牌按它画）、
        // 必须**自动理牌**（赤五排在普通五旁边，而不是字典序把它甩到 1m 前面）、
        // 出牌动画必须**从这张牌待着的那一格**起飞。
        {
            TableModel gm;
            gm.setMySeat(0);

            // ① 理牌：字典序会把 `0m` 排到 `1m` 前面；正确顺序是 1m,5m,0m,5p,9s
            QStringList h { QStringLiteral("9s"), QStringLiteral("0m"), QStringLiteral("1m"),
                            QStringLiteral("5m"), QStringLiteral("5p") };
            gm.setGodHand(1, h, QStringLiteral("3z"));
            check(gm.hasGodHand(1), QStringLiteral("他家手牌：设置后标记为已知"));
            checkEq(gm.godHand(1).join(QLatin1Char(',')), QStringLiteral("1m,5m,0m,5p,9s"),
                    QStringLiteral("他家手牌：自动理牌（赤五紧随普通五，不是字典序）"));
            checkEq(QString::number(gm.concealedCount(1)), QStringLiteral("6"),
                    QStringLiteral("他家手牌：暗牌总数 = 手牌 5 + 摸牌 1（布局与绘制同源）"));

            // ② 布局：开了他家手牌后，手牌行按**真实张数**排；摸牌单独占一格
            //    ⚠ 量宽度必须取**对家**（pos 2）：左右两家整体旋转 90°，
            //      屏幕矩形的 width 是局部高度（恒等于牌高），量不出手牌行长度。
            TableView tv;
            tv.setModel(&gm);
            tv.resize(1354, 930);
            tv.grab();                       // 触发一次 paintEvent → computeLayout()
            const qreal wDerived = tv.handBlockScreenForTest(2).width();
            gm.setGodHand(2, QStringList(10, QStringLiteral("1m")), QString());
            tv.grab();
            const qreal wTen = tv.handBlockScreenForTest(2).width();
            gm.setGodHand(2, QStringList(10, QStringLiteral("1m")), QStringLiteral("3z"));
            tv.grab();
            const qreal wTenDrawn = tv.handBlockScreenForTest(2).width();
            check(wTen < wDerived - 1.0,
                  QStringLiteral("他家手牌：布局按真实张数排（10 张 %1 < 实时口径的 13 张 %2）")
                      .arg(wTen).arg(wDerived));
            check(qAbs(wTenDrawn - wTen) < 0.5,
                  QStringLiteral("他家手牌：摸牌单独占一格，不挤手牌行（%1 vs %2）")
                      .arg(wTenDrawn).arg(wTen));

            // ③ 出牌动画起点 = 这张牌在手牌行里**理牌后**的那一格
            const QStringList ten = gm.godHand(2);          // 已理牌（10 张全 1m）
            gm.clearGodHands();
            gm.setGodHand(2, ten, QString());
            tv.grab();
            gm.applyEvent(obj({{QStringLiteral("ev"), QStringLiteral("discard")},
                               {QStringLiteral("seat"), 2},
                               {QStringLiteral("tile"), QStringLiteral("1m")},
                               {QStringLiteral("tsumogiri"), false}}));
            const int idx = tv.lastFlightFromIndexForTest();
            check(idx >= 0 && idx < ten.size(),
                  QStringLiteral("他家手牌：手切动画从手牌行里的一格起飞（第 %1 格）").arg(idx));
            check(tv.lastFlightWasExactForTest(),
                  QStringLiteral("他家手牌：手切动画按**真实手牌**定位（不是随机兜底）"));
            // 摸切则固定从摸牌槽起飞
            gm.applyEvent(obj({{QStringLiteral("ev"), QStringLiteral("discard")},
                               {QStringLiteral("seat"), 2},
                               {QStringLiteral("tile"), QStringLiteral("1m")},
                               {QStringLiteral("tsumogiri"), true}}));
            checkEq(QString::number(tv.lastFlightFromIndexForTest()), QStringLiteral("-1"),
                    QStringLiteral("他家手牌：摸切动画从摸牌槽起飞"));

            // ④ **复刻 `ReplayWindow::replayTo` 的真实顺序**（这条是上一版的漏网之鱼）：
            //    回放窗口每次跳转都是 `reset()` + 从头重放一遍。若 `reset()` 把上帝手牌清掉，
            //    重放到"出牌"那一步时模型里就没有四家暗牌了 → 动画只能退回随机兜底
            //    （真机上的表现正是"仍然从随机位置打出"，而当时的自检**绕过**了 reset，
            //      所以照样全绿 —— 教训：自检必须走与 App 同一条路径）。
            gm.clearGodHands();
            gm.setGodHand(2, ten, QString());          // 上一帧：出牌前的真实手牌
            gm.reset();                                // replayTo 的第一步
            check(gm.hasGodHand(2),
                  QStringLiteral("上帝手牌：`reset()` 不会清掉它（回放重放要靠上一帧那份算动画）"));
            checkEq(QString::number(gm.concealedCount(2)), QStringLiteral("10"),
                    QStringLiteral("上帝手牌：reset 之后布局仍按真实张数"));
            gm.applyEvent(obj({{QStringLiteral("ev"), QStringLiteral("discard")},
                               {QStringLiteral("seat"), 2},
                               {QStringLiteral("tile"), QStringLiteral("1m")},
                               {QStringLiteral("tsumogiri"), false}}));
            check(tv.lastFlightWasExactForTest() && tv.lastFlightFromIndexForTest() >= 0,
                  QStringLiteral("上帝手牌：reset + 重放之后，出牌动画仍然按真实手牌定位"
                                 "（第 %1 格）").arg(tv.lastFlightFromIndexForTest()));
            // 换一场记录时才显式清掉
            gm.clearGodHands();
            gm.reset();
            check(!gm.hasGodHand(2), QStringLiteral("上帝手牌：换记录时显式清掉"));

            gm.clearGodHands();
            check(!gm.hasGodHand(2), QStringLiteral("他家手牌：可以关掉"));
            checkEq(QString::number(gm.concealedCount(2)), QStringLiteral("13"),
                    QStringLiteral("关掉后回到「13 − 3×副露」的实时对局口径"));
        }

        // ---------- 回归：回放的「下一步」必须走**真实的**动画路径 ----------
        // 上一个版本的教训：自检自己 setGodHand + applyEvent（**绕过** `replayTo`），
        // 于是"reset 把上帝手牌清掉 → 动画退回随机"这个真机 bug 完全测不到。
        // 这里改成：塞一份手写记录 → `loadForTest()` → `nextOpForTest()`（与点按钮同一个槽），
        // 再读牌桌的"这次动画是不是按真实手牌定位的"。
        {
            ReplayWindow rw;
            rw.resize(1200, 800);
            ReplayModel* rm = rw.replayForTest();

            QJsonArray wall2;
            for (int i = 0; i < 136; ++i) {
                wall2.append(i);
            }
            QJsonObject meta2;
            meta2.insert(QStringLiteral("id"), QStringLiteral("GODPATH"));
            meta2.insert(QStringLiteral("entries"), 7);
            meta2.insert(QStringLiteral("names"), QJsonArray{QStringLiteral("甲"), QStringLiteral("乙"),
                                                             QStringLiteral("丙"), QStringLiteral("丁")});
            meta2.insert(QStringLiteral("walls"), QJsonArray{wall2});
            meta2.insert(QStringLiteral("round_at"), QJsonArray{0});
            rm->setMeta(meta2);

            QJsonArray arr2;
            arr2.append(entry(0, -1, obj({{QStringLiteral("ev"), QStringLiteral("replay_round")},
                                          {QStringLiteral("index"), 0},
                                          {QStringLiteral("bakaze"), QStringLiteral("E")},
                                          {QStringLiteral("kyoku"), 1},
                                          {QStringLiteral("dealer"), 0},
                                          {QStringLiteral("wall"), wall2}})));
            const QStringList h1 { QStringLiteral("1m"), QStringLiteral("2m"), QStringLiteral("3m"),
                                   QStringLiteral("4m"), QStringLiteral("5m"), QStringLiteral("6m"),
                                   QStringLiteral("7m"), QStringLiteral("8m"), QStringLiteral("9m"),
                                   QStringLiteral("1p"), QStringLiteral("2p"), QStringLiteral("3p"),
                                   QStringLiteral("4p") };
            for (int s = 0; s < 4; ++s) {
                QJsonArray hand;
                for (const QString& t : h1) {
                    hand.append(t);
                }
                arr2.append(entry(1 + s, s, obj({{QStringLiteral("ev"), QStringLiteral("round_start")},
                                                 {QStringLiteral("seat"), s},
                                                 {QStringLiteral("hand"), hand}})));
            }
            // 座位 1 摸一张、再手切一张 **7m**（它是手牌第 7 格，理牌后位置明确）
            arr2.append(entry(5, 1, obj({{QStringLiteral("ev"), QStringLiteral("draw")},
                                         {QStringLiteral("seat"), 1},
                                         {QStringLiteral("tile"), QStringLiteral("9s")}})));
            arr2.append(entry(6, -1, obj({{QStringLiteral("ev"), QStringLiteral("discard")},
                                          {QStringLiteral("seat"), 1},
                                          {QStringLiteral("tile"), QStringLiteral("7m")},
                                          {QStringLiteral("tsumogiri"), false}})));
            rm->addEntries(arr2);
            rw.loadForTest();                 // 光标落在"配牌完成"那一步
            rw.tableForTest()->grab();        // 画一帧（记录"上一帧的手牌"）

            rw.nextOpForTest();               // → 摸牌（不播动画）
            rw.tableForTest()->grab();
            rw.nextOpForTest();               // → 打出 7m（**这一步要播动画**）
            const int fi = rw.tableForTest()->lastFlightFromIndexForTest();
            check(rw.tableForTest()->lastFlightWasExactForTest(),
                  QStringLiteral("回放「下一步」：出牌动画按**真实手牌**定位（不是随机兜底）"));
            checkEq(QString::number(fi), QStringLiteral("6"),
                    QStringLiteral("回放「下一步」：7m 起飞的格子 = 它在理牌后手牌里的下标"));
        }

        // 回放入口：大厅一个按钮；结算弹窗要拿到 replay_id 才显示「看本局回放」
        {
            LobbyDialog lobby;
            int lobbyBtn = 0;
            for (QPushButton* b : lobby.findChildren<QPushButton*>()) {
                if (b->text() == lang::t(QStringLiteral("ui.replay.open_list"))) {
                    lobbyBtn++;
                }
            }
            checkEq(QString::number(lobbyBtn), QStringLiteral("1"),
                    QStringLiteral("大厅有「对局回放」入口按钮"));
            ResultDialog rd(QStringLiteral("t"), QStringLiteral("<p>x</p>"));
            int hiddenBtn = 0;
            int shownBtn = 0;
            for (QPushButton* b : rd.findChildren<QPushButton*>()) {
                if (b->text() != lang::t(QStringLiteral("ui.replay.watch_this"))) {
                    continue;
                }
                if (b->isVisibleTo(&rd)) {
                    shownBtn++;
                } else {
                    hiddenBtn++;
                }
            }
            checkEq(QString::number(hiddenBtn), QStringLiteral("1"),
                    QStringLiteral("没有 replay_id 时「看本局回放」隐藏"));
            checkEq(QString::number(shownBtn), QStringLiteral("0"),
                    QStringLiteral("没有 replay_id 时不显示「看本局回放」"));
            rd.enableReplay(QStringLiteral("ABCDEFGHIJ"));
            shownBtn = 0;
            for (QPushButton* b : rd.findChildren<QPushButton*>()) {
                if (b->text() == lang::t(QStringLiteral("ui.replay.watch_this"))
                    && b->isVisibleTo(&rd)) {
                    shownBtn++;
                }
            }
            checkEq(QString::number(shownBtn), QStringLiteral("1"),
                    QStringLiteral("拿到 replay_id 后「看本局回放」出现"));
        }

        // 回放窗口的入口按钮：显示他家手牌（可切换）/ 本局结算 —— 都必须存在且文案来自语言文件
        {
            ReplayWindow rw;
            int godBtn = 0;
            int godCheckable = 0;
            int resultBtn = 0;
            for (QPushButton* b : rw.findChildren<QPushButton*>()) {
                if (b->text() == lang::t(QStringLiteral("ui.replay.god_hands"))) {
                    godBtn++;
                    godCheckable += b->isCheckable() ? 1 : 0;
                }
                if (b->text() == lang::t(QStringLiteral("ui.replay.round_result"))) {
                    resultBtn++;
                }
            }
            checkEq(QString::number(godBtn), QStringLiteral("1"),
                    QStringLiteral("回放窗口有「显示他家手牌」切换键"));
            checkEq(QString::number(godCheckable), QStringLiteral("1"),
                    QStringLiteral("「显示他家手牌」是**可切换**的（checkable）"));
            checkEq(QString::number(resultBtn), QStringLiteral("1"),
                    QStringLiteral("回放窗口有「本局结算」按钮"));
        }
    }

    // ---------- 性能：牌面位图缓存（动画卡顿的根因）----------
    // `QSvgRenderer::render()` 每次调用都重新栅格化矢量，而动画 16ms 一帧要重画一百多张牌。
    // 这里做**同机同轮**的 A/B：关缓存（= 优化前的行为）与开缓存各画 N 帧，量每帧耗时。
    // 断言只钉**确定性**的东西（缓存命中数、第二帧零未命中），耗时只打印出来给人看
    //（机器负载会抖，拿绝对毫秒当判据会变成 flaky 测试）。
    {
        TableModel pm;
        pm.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"round_start","round":{"bakaze":"E","kyoku":2,"honba":1,"riichi_sticks":1},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","1m","1m","2m","3m","4m","5m","6m","7m","9m","9m","9m","9m"],"dora_indicators":["5m","2p","8s","3z","6m"],"tiles_left":42,"dead_wall_left":4})"),
            nullptr));
        // 把四家牌河填满（每行 6 列 → 18 张是 3 行的满配），这是动画期间一帧要画的量级
        for (int seat = 0; seat < 4; ++seat) {
            for (int i = 0; i < 18; ++i) {
                QJsonObject d;
                d.insert(QStringLiteral("ev"), QStringLiteral("discard"));
                d.insert(QStringLiteral("seat"), seat);
                d.insert(QStringLiteral("tile"), QStringLiteral("3p"));
                d.insert(QStringLiteral("tsumogiri"), false);
                (void)seat;
                pm.applyEvent(d);
            }
        }
        TableView tv;
        tv.setModel(&pm);
        tv.resize(1354, 930);

        constexpr int kFrames = 40;
        auto bench = [&](bool cacheOn) {
            TileRenderer::setAssetCacheEnabledForTest(cacheOn);
            TileRenderer::clearAssetCache();
            TileRenderer::resetAssetCacheStatsForTest();
            tv.grab();                       // 预热一帧（布局 + 素材解析）
            QElapsedTimer t;
            t.start();
            for (int i = 0; i < kFrames; ++i) {
                tv.grab();
            }
            const qint64 ms = t.elapsed();
            TileRenderer::AssetCacheStats st = TileRenderer::assetCacheStatsForTest();
            return QPair<qint64, TileRenderer::AssetCacheStats>(ms, st);
        };

        const auto slow = bench(false);
        const auto fast = bench(true);
        const double slowPer = double(slow.first) / kFrames;
        const double fastPer = double(fast.first) / kFrames;
        // 打印出来（自检输出就是给人看的证据）
        QTextStream(stdout) << QStringLiteral("[perf] 牌桌 %1 帧：优化前(每帧重栅格化矢量) %2 ms/帧"
                                             " ｜ 优化后(位图缓存) %3 ms/帧"
                                             "（命中 %4 / 未命中 %5 / 缓存 %6 张）\n")
                                     .arg(kFrames)
                                     .arg(slowPer, 0, 'f', 2)
                                     .arg(fastPer, 0, 'f', 2)
                                     .arg(fast.second.hits)
                                     .arg(fast.second.misses)
                                     .arg(fast.second.entries);
        fflush(stdout);

        check(fast.second.hits > 0, QStringLiteral("性能：位图缓存有命中"));
        check(fast.second.entries > 0, QStringLiteral("性能：位图缓存里确实存下了图"));
        check(fast.second.misses <= 64,
              QStringLiteral("性能：整个基准里的未命中次数只等于「牌的种类 × 尺寸档位」（实际 %1）")
                  .arg(fast.second.misses));
        check(fastPer < slowPer,
              QStringLiteral("性能：开缓存后每帧更快（%1 ms → %2 ms）").arg(slowPer, 0, 'f', 2)
                  .arg(fastPer, 0, 'f', 2));
        // 第二帧（缓存已热）必须**零未命中** —— 这条是确定性的，钉住"缓存真的在复用"
        TileRenderer::resetAssetCacheStatsForTest();
        tv.grab();
        checkEq(QString::number(TileRenderer::assetCacheStatsForTest().misses),
                QStringLiteral("0"), QStringLiteral("性能：缓存热了以后一帧内零未命中"));
        TileRenderer::setAssetCacheEnabledForTest(true);
        TileRenderer::clearAssetCache();
    }

    // ---------- 回归：结算界面的役满必须写「n倍役满」，不能写「0 番」----------
    // 规则：成立 n 种役满役则基本点 = 8000n，**符数与番数全部失效**（见 docs/日本麻将.md）。
    // 旧版界面直接印 yaku[].han，而服务端当时对役满役发的 han 是 0 →
    // 「国士无双 0 番」。这里把几条路都钉住：
    //   ① 役满役 → 「n倍役满」；② 合计 → 只报倍数、不写「0 番 0 符」；
    //   ③ 非役满（含累计役满）→ 仍然照常写番/符；
    //   ④ 役种名/打点档位都是**协议里的 ASCII 码**翻出来的（报文里没有中文）。
    {
        TableModel mm;
        // ⚠ 报文现在是纯 ASCII（code/tile/limit 都是码），但**老服务端兼容那条会传中文** ——
        //   所以 yakuJson 一律走 QString（QStringLiteral），**绝不能**用 QLatin1String 接中文字面量：
        //   那会把 UTF-8 字节按 Latin-1 解，中文直接变乱码（这个坑绊过一次）。
        auto agari = [](const QString& yakuJson, int han, int fu, int yakuman, const QString& limit) {
            return proto::decodeLine(
                QStringLiteral(R"({"ev":"agari","winner":0,"from":-1,"tsumo":true,)"
                               R"("hand":["9m","9m","9m"],"melds":[],"winning_tile":"9m",)"
                               R"("dora_indicators":["5m"],"ura_indicators":[],)"
                               R"("yaku":%1,"han":%2,"fu":%3,"dora":0,"aka":0,"ura":0,)"
                               R"("yakuman":%4,"limit":"%5","base_points":8000,)"
                               R"("score_delta":[8000,-2000,-2000,-2000],)"
                               R"("scores_after":[33000,23000,23000,23000],"pao":{"seat":-1}})")
                    .arg(yakuJson).arg(han).arg(fu).arg(yakuman)
                    .arg(limit).toUtf8(),
                nullptr);
        };

        // ① 单倍役满
        const QString y1 = ResultDialog::agariHtml(
            agari(QStringLiteral(R"([{"code":"kokushi","han":13,"yakuman":1}])"), 13, 0, 1,
                  QStringLiteral("yakuman")), &mm);
        check(y1.contains(QStringLiteral("1倍役满")),
              QStringLiteral("役满役应写成「1倍役满」"));
        check(y1.contains(QStringLiteral("国士无双")),
              QStringLiteral("役种码 kokushi 应翻成「国士无双」，实际：%1").arg(y1.left(80)));
        check(!y1.contains(QStringLiteral("0 番")),
              QStringLiteral("役满界面不得出现「0 番」"));

        // ② 两倍役满（服务端给的番数是 13×2，但仍该显示倍数）
        const QString y2 = ResultDialog::agariHtml(
            agari(QStringLiteral(R"([{"code":"suuankou_tanki","han":26,"yakuman":2}])"), 26, 0, 2,
                  QStringLiteral("yakuman")), &mm);
        check(y2.contains(QStringLiteral("2倍役满")),
              QStringLiteral("两倍役满应写成「2倍役满」"));
        check(y2.contains(QStringLiteral("四暗刻单骑")),
              QStringLiteral("役种码 suuankou_tanki 应翻成「四暗刻单骑」"));
        check(!y2.contains(QStringLiteral("0 番")),
              QStringLiteral("两倍役满界面不得出现「0 番」"));

        // ③ 非役满：照旧写番/符 + 打点档位（ASCII 码 mangan → 满贯），且不能误判成役满
        const QString n1 = ResultDialog::agariHtml(
            agari(QStringLiteral(R"([{"code":"riichi","han":1},{"code":"tanyao","han":1}])"), 2, 40, 0,
                  QStringLiteral("mangan")), &mm);
        check(n1.contains(QStringLiteral("番")) && n1.contains(QStringLiteral("40 符")),
              QStringLiteral("普通和牌仍应写「合计 N 番 M 符」"));
        check(n1.contains(QStringLiteral("满贯")) && !n1.contains(QStringLiteral("mangan")),
              QStringLiteral("打点档位码 mangan 应翻成「满贯」，实际：%1")
                  .arg(n1.section(QStringLiteral("合计"), 1, 1).left(40)));
        check(!n1.contains(QStringLiteral("倍役满")),
              QStringLiteral("普通和牌不得出现「倍役满」"));

        // ④ 累计役满（番数 ≥13 但**没有**役满役）→ 仍然写番，不能被当成役满
        const QString n2 = ResultDialog::agariHtml(
            agari(QStringLiteral(R"([{"code":"chinitsu","han":6},{"code":"ryanpeiko","han":3}])"), 26, 40, 0,
                  QStringLiteral("kazoe_yakuman")), &mm);
        check(n2.contains(QStringLiteral("累计役满")) && n2.contains(QStringLiteral("26")),
              QStringLiteral("累计役满应照常显示番数 + 「累计役满」，实际：%1")
                  .arg(n2.section(QStringLiteral("合计"), 1, 1).left(40)));
        check(!n2.contains(QStringLiteral("倍役满")),
              QStringLiteral("累计役满不是役满役，不得写成「n倍役满」"));

        // ⑤ 参数化役种（役牌 + 牌码）在逐役列表里也要拼对
        const QString n3 = ResultDialog::agariHtml(
            agari(QStringLiteral(R"([{"code":"yakuhai","tile":"5z","han":1}])"), 1, 40, 0, QString()), &mm);
        check(n3.contains(QStringLiteral("役牌 白")),
              QStringLiteral("参数化役种应拼成「役牌 白」，实际：%1").arg(n3.left(120)));

        // ⑥ 认不出的役种码：**原样显示码**（可见、可定位），不能显示成裸键
        const QString n4 = ResultDialog::agariHtml(
            agari(QStringLiteral(R"([{"code":"future_yaku","han":1}])"), 1, 40, 0, QString()), &mm);
        check(n4.contains(QStringLiteral("future_yaku")) && !n4.contains(QStringLiteral("yaku.future_yaku")),
              QStringLiteral("未登记的役种码原样显示，实际：%1").arg(n4.left(120)));

        // ⑦ 老服务端兼容：只有中文字段（没有 code）时，照旧显示原文，而不是空白
        const QString n5 = ResultDialog::agariHtml(
            agari(QStringLiteral(R"([{"name":"国士无双","han":13,"yakuman":1}])"), 13, 0, 1,
                  QStringLiteral("两倍役满")), &mm);
        check(n5.contains(QStringLiteral("国士无双")) && n5.contains(QStringLiteral("1倍役满")),
              QStringLiteral("老服务端（只有中文 name/limit）仍应正常显示，实际：%1").arg(n5.left(120)));

        // ⑧ 流局原因：ASCII 码 reason → 文案
        const QString r1 = ResultDialog::ryuukyokuHtml(
            proto::decodeLine(QByteArrayLiteral(
                R"({"ev":"ryuukyoku","type":"exhaustive","reason":"exhaustive","tenpai":[true,false,false,false],)"
                R"("hands":[[],null,null,null],"nagashi":[-1,-1,-1,-1],)"
                R"("scores_after":[25000,25000,25000,25000],"score_delta":[0,0,0,0]})"),
                nullptr),
            &mm);
        check(r1.contains(QStringLiteral("荒牌流局")),
              QStringLiteral("流局原因码 exhaustive 应翻成「荒牌流局」，实际：%1").arg(r1.left(80)));

        // ⑨ 终局（对局结束）的表格：表头与行都是**整块 HTML** 搬进语言文件的
        //    （`ui.result.table_head` / `ui.result.rank_row`），人工改写过，必须有断言兜底：
        //    这两条一旦漏 key，只会在真打完一整场时才看出来。
        const QString g1 = ResultDialog::gameEndHtml(
            proto::decodeLine(
                QStringLiteral(R"({"ev":"game_end","final":[)"
                               R"({"seat":0,"name":"甲","score":41200,"point":53.6,"uma":15,"rank":1},)"
                               R"({"seat":1,"name":"乙","score":30000,"point":0,"uma":0,"rank":2}],)"
                               R"("scores":[41200,30000,18000,10800]})").toUtf8(),
                nullptr),
            &mm);
        check(g1.contains(QStringLiteral("顺位")) && g1.contains(QStringLiteral("精算"))
                  && g1.contains(QStringLiteral("1位")) && g1.contains(QStringLiteral("41200")),
              QStringLiteral("终局表格应含表头（顺位/精算）与行数据，实际：%1").arg(g1.left(160)));
        check(g1.contains(QStringLiteral("<table")) && g1.contains(QStringLiteral("</table>")),
              QStringLiteral("终局表格的 HTML 结构必须完整（整块文案里含 <table> 开标签）"));
    }

    // ---------- 汇总 ----------
    log << QString();
    const QString summary = failures.isEmpty()
                                ? QStringLiteral("SELFTEST PASS")
                                : QStringLiteral("SELFTEST FAIL: %1").arg(failures.first());
    log << QStringLiteral("检查项：%1，失败：%2").arg(checks).arg(failures.size());
    log << summary;

    // 写日志文件
    QFile logFile(dir.absoluteFilePath(QStringLiteral("selftest.log")));
    if (logFile.open(QIODevice::WriteOnly | QIODevice::Text)) {
        QTextStream ts(&logFile);
        ts.setEncoding(QStringConverter::Utf8);
        for (const QString& l : log)
            ts << l << '\n';
    }

    // 打印到控制台
    for (const QString& l : log)
        std::printf("%s\n", qPrintable(l));
    std::fflush(stdout);

    return failures.isEmpty() ? 0 : 1;
}

} // namespace selftest
