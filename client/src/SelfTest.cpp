#include "SelfTest.h"

#include "i18n/Lang.h"
#include "model/AutoPolicy.h"
#include "model/ReplayModel.h"
#include "model/Settings.h"
#include "model/TenhouLog.h"
#include "model/Theme.h"
#include "model/Sound.h"
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
#include <QLabel>
#include <QPainter>
#include <QPixmap>
#include <QPushButton>
#include <QLayout>
#include <QLineEdit>
#include <QTextBrowser>
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

        // 加杠（小明杠）：横的是**第 4 张（加上的那张）**，它叠在碰的中张之上。
        // 旧实现横下标 1 → 加上的那张被排成一个新槽位，整副多占一格（报障：加杠跑到一边）。
        Meld k;
        k.kind = QStringLiteral("kakan");
        k.tiles = QStringList { QStringLiteral("6z"), QStringLiteral("6z"),
                                QStringLiteral("6z"), QStringLiteral("6z") };
        k.calledTile = QStringLiteral("6z");
        k.from = 1;
        checkEq(QString::number(TableView::meldRotatedIndexForTest(k, 0)), QStringLiteral("3"),
                QStringLiteral("加杠横置的是第 4 张（加上的那张）"));
        // 宽度按"三格一横排"算：叠上去那张不占新槽位
        const qreal kw = TableLayout::meldWidthOf(k, 0, 10.0, 13.6, 1.0);
        const qreal expectKw = 2.0 * 10.0 + 13.6 + 2.0 * 1.0;   // 两竖直 + 一横置 + 两个间隔
        check(qAbs(kw - expectKw) < 0.01,
              QStringLiteral("加杠宽度应按三格算（叠放不占槽位），期望 %1 实际 %2")
                  .arg(expectKw).arg(kw));
    }

    // ---- 回归：副露三张**底部齐平**（横置那张不许"浮"在中间）----
    // 报障原文：「副露的三张牌底部应该是相互齐平的，但现在横置的那张是居中而应该是居下的」。
    // 断言直接读**绘制用的同一份几何**（`TableView::meldSlotRectForTest` → `meldSlotRects`），
    // 不是照着重算一遍公式。
    {
        TableModel tm;
        const auto feed = [&tm](const char* json) {
            tm.applyEvent(proto::decodeLine(QByteArray(json), nullptr));
        };
        feed(R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","4m","5m","6m","7m","8m","9m","1p","2p","3p"],"dora_indicators":[],"tiles_left":60,"dead_wall_left":4})");
        // 自家碰了下家的 5z：横置位在最右（meldRotatedIndex = 2）
        feed(R"({"ev":"meld","seat":0,"kind":"pon","tiles":["5z","5z","5z"],"from":1,"called_tile":"5z","aka":[false,false,false],"called_index":0})");
        TableView tv;
        tv.resize(900, 640);
        tv.setModel(&tm);
        tv.updateLayoutForTest();
        const QRectF r0 = tv.meldSlotRectForTest(0, 0, 0);
        const QRectF r1 = tv.meldSlotRectForTest(0, 0, 1);
        const QRectF rRot = tv.meldSlotRectForTest(0, 0, 2);   // 横置那张
        check(r0.isValid() && r1.isValid() && rRot.isValid(),
              QStringLiteral("副露三格的矩形都算得出来"));
        check(qAbs(r0.bottom() - r1.bottom()) < 0.01,
              QStringLiteral("两张竖直的底边齐平（%1 vs %2）").arg(r0.bottom()).arg(r1.bottom()));
        check(qAbs(rRot.bottom() - r0.bottom()) < 0.01,
              QStringLiteral("横置那张的底边也要齐平：期望 %1 实际 %2")
                  .arg(r0.bottom()).arg(rRot.bottom()));
        check(rRot.width() > rRot.height(),
              QStringLiteral("横置那张确实是横的（宽 %1 > 高 %2）")
                  .arg(rRot.width()).arg(rRot.height()));
        check(rRot.height() < r0.height() + 0.01,
              QStringLiteral("横置那张的高 = 牌河牌宽（比竖直的矮）"));
    }

    // ---- 回归：名牌（ID 框）在**右下**，四家角位轮转一位 ----
    // 用户口径：「ID 框从左下移动到右下，即顺时针旋转变换一位」。
    {
        TableView tv;
        tv.resize(900, 640);
        tv.updateLayoutForTest();
        const QRectF self = tv.plateRectForTest(0);
        const QRectF right = tv.plateRectForTest(1);
        const QRectF top = tv.plateRectForTest(2);
        const QRectF left = tv.plateRectForTest(3);
        check(self.center().x() > 450.0 && self.center().y() > 320.0,
              QStringLiteral("自家名牌在**右下**（中心 %1,%2）")
                  .arg(self.center().x()).arg(self.center().y()));
        check(right.center().x() > 450.0 && right.center().y() < 320.0,
              QStringLiteral("下家名牌在右上（轮转一位后）"));
        check(top.center().x() < 450.0 && top.center().y() < 320.0,
              QStringLiteral("对家名牌在左上（轮转一位后）"));
        check(left.center().x() < 450.0 && left.center().y() > 320.0,
              QStringLiteral("上家名牌在左下（轮转一位后）"));
        const QStringList names { QStringLiteral("自家"), QStringLiteral("下家"),
                                  QStringLiteral("对家"), QStringLiteral("上家") };
        for (int i = 0; i < 4; ++i) {
            const QRectF a = tv.plateRectForTest(i);
            const QRectF b = tv.plateRectForTest((i + 1) % 4);
            check(!a.intersects(b),
                  QStringLiteral("%1 与 %2 的名牌不重叠").arg(names.at(i), names.at((i + 1) % 4)));
        }
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

    // ---- 回归：牌河**固定左缘**（先假定一行放满、算出那条最左沿，再从那里向右放牌）----
    // 用户第二次数（上一次改的是"每行按自己的宽度居中"，那只是行与行之间不齐）：
    // 真正的毛病是**整条牌河随张数往左挪** —— 旧实现按「本帧已打出的最大列数」算左缘再居中，
    // 于是只打 1 张时那张落在牌河带中间，每多打一张整体左移一点，打满 6 张才落到最左；
    // 第 1 张的位置自己会动，看起来就是"没左对齐"。
    // 要求：左缘 = **一行排满**时最左那张的左沿，与「已经打了几张」**完全无关**。
    // ⚠ 断言用**局部坐标**（`riverSlotLocalForTest`）而不是屏幕坐标：屏幕那份带着四家的旋转，
    //    「左沿」在左右两家那里其实是上下方向（`pos=1` 的 toScreen 把局部 u 映射到屏幕 y）。
    {
        TableModel mrv;
        mrv.applyEvent(parseEv(
            R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},)"
            R"("seat":0,"dealer":0,"scores":[25000,25000,25000,25000],)"
            R"("hand":["1m","2m","3m","4m","5m","6m","7m","8m","9m","1p","2p","3p","4p"],)"
            R"("tiles_left":60,"dead_wall_left":4})"));
        TableView rv;
        rv.setModel(&mrv);
        rv.resize(1354, 930);
        rv.grab();                       // 先跑一次 paintEvent → computeLayout，之后读到的就是定下来的几何

        // 用**对家**（pos 2）的牌河：既不碰自家手牌，也省得看旋转
        auto throwOne = [&mrv]() {
            mrv.applyEvent(parseEv(R"({"ev":"discard","seat":2,"tile":"1m","tsumogiri":false})"));
        };
        const qreal anchor = rv.riverLeftUForTest();
        const qreal rw = rv.riverWForTest();
        const qreal rh = rv.riverHForTest();
        const qreal cgap = qMax(1.0, rw * 0.09);   // 与布局/绘制同一公式

        // ① 一把尺子：满行宽度 = **6 张普通牌 + 5 个列间距**（横置牌的额外宽度不算进去，
        //    用户口径）；左缘 = 它的负一半。
        check(qAbs(rv.riverFullRowExtentForTest() - (6.0 * rw + 5.0 * cgap)) < 0.01,
              QStringLiteral("一行排满的宽度应为 6×牌宽 + 5×列间距（实际 %1）")
                  .arg(rv.riverFullRowExtentForTest()));
        check(qAbs(anchor + rv.riverFullRowExtentForTest() / 2.0) < 0.01,
              QStringLiteral("固定左缘应是一行排满宽度的负一半（实际 %1）").arg(anchor));

        // ② 红证：旧实现打 1 张时按「1 列」算 → 落在 −牌高/2（≈ 牌河带中间），
        //    与固定左缘差着好几张牌宽。这条一红就说明左缘还在跟着张数走。
        check(qAbs(anchor - (-rh / 2.0)) > rw,
              QStringLiteral("固定左缘必须远离「按 1 张居中」的旧位置（新 %1 / 旧 %2）")
                  .arg(anchor).arg(-rh / 2.0));

        // ③ 张数变化时左缘**不动**：1 → 3 → 6 → 7 → 13 → 18（跨越三行）
        throwOne();
        rv.grab();
        checkEq(QString::number(mrv.discards(2).size()), QStringLiteral("1"),
                QStringLiteral("对家先打 1 张"));
        check(qAbs(rv.riverSlotLocalForTest(2, 0).left() - anchor) < 0.01,
              QStringLiteral("只打 1 张时这张就在固定左缘上（%1 vs %2）")
                  .arg(rv.riverSlotLocalForTest(2, 0).left()).arg(anchor));
        for (int total : {3, 6, 7, 13, 18}) {
            while (mrv.discards(2).size() < total)
                throwOne();
            rv.grab();
            check(qAbs(rv.riverSlotLocalForTest(2, 0).left() - anchor) < 0.01,
                  QStringLiteral("打到 %1 张后，第 1 张仍在同一条左缘上（%2 vs %3）")
                      .arg(total).arg(rv.riverSlotLocalForTest(2, 0).left()).arg(anchor));
        }
        // 每一行的行首都共用这条左缘（第 2 行 = 第 7 张，第 3 行 = 第 13 张）
        check(qAbs(rv.riverSlotLocalForTest(2, 6).left() - anchor) < 0.01,
              QStringLiteral("第 2 行第 1 张与第 1 行同一条左缘（%1 vs %2）")
                  .arg(rv.riverSlotLocalForTest(2, 6).left()).arg(anchor));
        check(qAbs(rv.riverSlotLocalForTest(2, 12).left() - anchor) < 0.01,
              QStringLiteral("第 3 行第 1 张与第 1 行同一条左缘（%1 vs %2）")
                  .arg(rv.riverSlotLocalForTest(2, 12).left()).arg(anchor));

        // ④ 排满是**向右**长：第 6 张的右沿 = 左缘 + 6 张普通牌宽 + 5 个列间距
        //    —— 正好等于这条预留线的右端（普通行不多不少地铺满它）。
        const QRectF last = rv.riverSlotLocalForTest(2, 5);
        check(qAbs(last.right() - (anchor + 6.0 * rw + 5.0 * cgap)) < 0.01,
              QStringLiteral("一行是左缘向右累加出来的（右沿 %1）").arg(last.right()));
        check(last.right() <= -anchor + 0.01,
              QStringLiteral("普通行不得越出一行排满的预留范围（%1 vs %2）")
                  .arg(last.right()).arg(-anchor));

        // ⑤ 左缘**与有没有横置牌无关**；立直那一行允许比普通行**向右多出 (牌高 − 牌宽)**
        //    （用户口径：横置牌的额外宽度不算进左缘，宁可让立直行右偏一点）。
        {
            TableModel ms;
            ms.applyEvent(parseEv(
                R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},)"
                R"("seat":0,"dealer":0,"scores":[25000,25000,25000,25000],)"
                R"("hand":["1m","2m","3m","4m","5m","6m","7m","8m","9m","1p","2p","3p","4p"],)"
                R"("tiles_left":60,"dead_wall_left":4})"));
            TableView sv;
            sv.setModel(&ms);
            sv.resize(1354, 930);
            for (int i = 0; i < 6; ++i) {
                // 第 1 张是立直宣言牌（横置），其余 5 张普通牌 —— 刚好铺满一行
                ms.applyEvent(parseEv(i == 0
                    ? R"({"ev":"discard","seat":2,"tile":"1m","tsumogiri":false,"sideways":true})"
                    : R"({"ev":"discard","seat":2,"tile":"1m","tsumogiri":false,"sideways":false})"));
            }
            sv.grab();
            const qreal sideAnchor = sv.riverLeftUForTest();
            const QRectF sideFirst = sv.riverSlotLocalForTest(2, 0);
            check(qAbs(sideAnchor - anchor) < 0.01,
                  QStringLiteral("左缘不得因为这一行有横置牌而改变（%1 vs %2）")
                      .arg(sideAnchor).arg(anchor));
            check(qAbs(sideFirst.left() - sideAnchor) < 0.01,
                  QStringLiteral("横置宣言牌也从固定左缘起排（%1 vs %2）")
                      .arg(sideFirst.left()).arg(sideAnchor));
            const QRectF sideLast = sv.riverSlotLocalForTest(2, 5);
            check(qAbs(sideLast.right() - (-sideAnchor + (rh - rw))) < 0.01,
                  QStringLiteral("立直行正好比普通行多出 (牌高 − 牌宽)（%1 vs %2）")
                      .arg(sideLast.right()).arg(-sideAnchor + (rh - rw)));
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

    // ---- 回归：同码手切/摸切的**动画起点**（报障：同码手切被演成摸切）----
    // 服务端那一半由 `SelfTest.discardAlignTests` 钉住（手切必须打**手里那张**、广播
    // `tsumogiri=false`；`hand` 按 id 排序时 `findByCode` 会先撞上摸牌位那张）。
    // 这里钉客户端这一半：拿到 `tsumogiri=false` 时动画必须从**手牌那一格**起飞，
    // 只有 `tsumogiri=true` 才从摸牌槽起飞 —— 两张牌长得一模一样，除了这个字段没有别的依据。
    {
        TableModel mv;
        mv.setMySeat(0);
        TableView tv;
        tv.setModel(&mv);
        tv.resize(1354, 930);
        mv.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["5m","5m","1m","2m","3m","4m","6m","7m","8m","1p","2p","3p","9s"],"dora_indicators":["1z"],"tiles_left":70,"dead_wall_left":4})"),
            nullptr));
        mv.applyEvent(proto::decodeLine(
            QByteArrayLiteral(R"({"ev":"draw","seat":0,"tiles_left":69,"rinshan":false,"tile":"5m"})"),
            nullptr));
        tv.grab();          // 画一帧 → TableView 记下「手牌 13 格 + 摸牌位 1 格」
        // ① 手切（手里那张 5m）：起点必须是手牌行里的一格，不能是摸牌槽
        mv.applyEvent(proto::decodeLine(
            QByteArrayLiteral(R"({"ev":"discard","seat":0,"tile":"5m","tsumogiri":false,"riichi":false,"riichi_stick":false})"),
            nullptr));
        const int idxHandCut = tv.lastFlightFromIndexForTest();
        check(idxHandCut >= 0,
              QStringLiteral("同码手切：动画从**手牌格**起飞（第 %1 格；-1 = 摸牌槽）")
                  .arg(idxHandCut));
        check(tv.lastFlightWasExactForTest(),
              QStringLiteral("同码手切：起点按真实手牌定位（不是随机兜底）"));
        // ② 再摸一张同码，这次**摸切**：起点固定是摸牌槽（-1）
        mv.applyEvent(proto::decodeLine(
            QByteArrayLiteral(R"({"ev":"draw","seat":0,"tiles_left":68,"rinshan":false,"tile":"5m"})"),
            nullptr));
        tv.grab();
        mv.applyEvent(proto::decodeLine(
            QByteArrayLiteral(R"({"ev":"discard","seat":0,"tile":"5m","tsumogiri":true,"riichi":false,"riichi_stick":false})"),
            nullptr));
        checkEq(QString::number(tv.lastFlightFromIndexForTest()), QStringLiteral("-1"),
                QStringLiteral("同码摸切：动画从摸牌槽起飞（两张同码牌只能靠 tsumogiri 区分）"));
        check(tv.lastFlightWasExactForTest(),
              QStringLiteral("同码摸切：起点是确定的（摸牌槽），不是随机兜底"));
    }

    // ---- 回归：庄家第一巡「哪张是刚摸到的」必须由 round_start.drawn 点名 ----
    // 报障：庄家第一巡点摸牌位，服务端却打了另一张（摸切），之后手牌内容与服务端差一张。
    // 根因：`round_start.hand` 是**已排序**的 14 张，客户端按"最后一张"认摸牌位，
    // 而服务端真正刚摸到的是第 14 张 openingTile（排序后通常在中间）—— 两端认知不同，
    // 牌码对不上，服务端打 A、客户端扣 B，**张数相同、内容差一张**（幽灵手牌）。
    {
        TableModel md;
        // 14 张：排序后最后一张是 9s，真正刚摸到的是 3p（服务端点名 drawn）
        md.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","5m","6m","7m","1p","2p","3p","1s","2s","3s","3p","9s"],"drawn":"3p","dora_indicators":["5p"],"tiles_left":69,"dead_wall_left":4})"),
            nullptr));
        checkEq(md.drawnTile(), QStringLiteral("3p"),
                QStringLiteral("庄家配牌：摸牌位是 drawn 点名的那张（不是排序后的最后一张）"));
        checkEq(QString::number(md.hand().size()), QStringLiteral("13"),
                QStringLiteral("庄家配牌：点名的摸牌不留在暗牌里（14 → 13）"));
        // 3p 有两张（重复牌是常态）：点名的只是"其中一张"，总数不能变
        const int n3p = int(md.hand().count(QStringLiteral("3p")))
                + (md.drawnTile() == QStringLiteral("3p") ? 1 : 0);
        checkEq(QString::number(n3p), QStringLiteral("2"),
                QStringLiteral("庄家配牌：3p 的总数仍是 2 张"));
        check(md.hand().contains(QStringLiteral("9s")),
              QStringLiteral("庄家配牌：排序最后那张仍在暗牌里"));

        // 服务端的摸切声明与客户端摸牌位**牌码不同**（两端"摸到哪张"认知不同）：
        // 客户端必须按**牌码**对账 —— 扣掉服务端真正打出的那张、把摸牌位的牌并回暗手，
        // 而不是信标记把摸牌位那一张清掉（旧写法就会那样：张数还对得上，内容差一张）。
        md.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"discard","seat":0,"tile":"5m","tsumogiri":true,"riichi":false,"riichi_stick":false})"),
            nullptr));
        checkEq(md.drawnTile(), QString(), QStringLiteral("错配摸切后摸牌位也清空"));
        checkEq(QString::number(md.hand().size()), QStringLiteral("13"),
                QStringLiteral("错配摸切后暗牌恰好少一张（14 → 13）"));
        check(!md.hand().contains(QStringLiteral("5m")),
              QStringLiteral("错配摸切：服务端打出的 5m 已从暗牌扣掉"));
        checkEq(QString::number(md.hand().count(QStringLiteral("3p"))), QStringLiteral("2"),
                QStringLiteral("错配摸切：被并回暗手的摸牌没有被误清掉"));
    }

    // ---- 老服务端没有 `drawn` 字段时，退回"最后一张是刚摸到的"（不崩，但会认错）----
    {
        TableModel mo;
        mo.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","5m","6m","7m","1p","2p","3p","1s","2s","3s","3p","9s"],"dora_indicators":["5p"],"tiles_left":69,"dead_wall_left":4})"),
            nullptr));
        // 这份配牌里真正的第 14 张是 3p，但它排序后在中间 —— 旧启发式只能拿最后一张，
        // 于是摸牌位显示 9s。这正是「服务端必须发 drawn」的原因：客户端猜不出来。
        checkEq(mo.drawnTile(), QStringLiteral("9s"),
                QStringLiteral("老服务端无 drawn → 按最后一张认摸牌位（会认错）"));
        checkEq(QString::number(mo.hand().size()), QStringLiteral("13"),
                QStringLiteral("老服务端路径暗牌仍是 13 张"));
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
        // 一位必要点数（批次三）：建房对话框的输入框默认值必须跟预设走
        //（M.League 不要求；《天凤》《雀魂》= 30000 —— `docs/日本麻将.md` L116/L157）。
        // 0 在界面上显示成「不要求」而不是 0 点。
        {
            checkEq(QString::number(lobbyrules::defaultRequiredPoints(QStringLiteral("mleague"))),
                    QStringLiteral("0"), QStringLiteral("一位必要点数：M.League 不要求"));
            checkEq(QString::number(lobbyrules::defaultRequiredPoints(QStringLiteral("tenhou"))),
                    QStringLiteral("30000"), QStringLiteral("一位必要点数：《天凤》= 30000"));
            checkEq(QString::number(lobbyrules::defaultRequiredPoints(QStringLiteral("majsoul"))),
                    QStringLiteral("30000"), QStringLiteral("一位必要点数：《雀魂》= 30000"));
            check(!lang::t(QStringLiteral("ui.lobby.required_points")).isEmpty()
                      && !lang::t(QStringLiteral("ui.lobby.required_points_none")).isEmpty()
                      && !lang::t(QStringLiteral("ui.lobby.required_points_hint")).isEmpty(),
                  QStringLiteral("一位必要点数的三条文案都在语言文件里"));
        }

        // 燕返（S-48）：`agari` 带 `riichi_void` 时必须清掉那家的立直标记与那根供託。        // 客户端**只认服务端这个字段**，绝不自己推断"被荣和的这张是不是宣言牌"（AGENTS §2.1）；
        // 分数以 `scores_after` 为准（退回的 1000 点已经算在里面）。
        {
            TableModel mt;
            mt.applyEvent(proto::decodeLine(QByteArrayLiteral(
                R"({"ev":"round_start","round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},"seat":0,"dealer":0,"scores":[25000,25000,25000,25000],"hand":["1m","2m","3m","4m","5m","6m","7m"],"dora_indicators":["5p"],"tiles_left":40,"dead_wall_left":4})"),
                nullptr));
            mt.applyEvent(proto::decodeLine(
                QByteArrayLiteral(R"({"ev":"riichi","seat":1,"stick_index":0,"sticks":1,"scores":[25000,24000,25000,25000]})"),
                nullptr));
            check(mt.riichi(1) && mt.riichiSticks() == 1,
                  QStringLiteral("立直宣言后：标记置位 + 供託 1 根"));
            mt.applyEvent(proto::decodeLine(QByteArrayLiteral(
                R"({"ev":"agari","winner":2,"from":1,"tsumo":false,"riichi_void":1,"scores_after":[25000,25000,28000,22000]})"),
                nullptr));
            check(!mt.riichi(1), QStringLiteral("燕返：agari 的 riichi_void 清掉那家的立直标记"));
            checkEq(QString::number(mt.riichiSticks()), QStringLiteral("0"),
                    QStringLiteral("燕返：那根供託退回，盘上不再画"));
            checkEq(QString::number(mt.scores().value(1)), QStringLiteral("25000"),
                    QStringLiteral("燕返：分数以 scores_after 为准（1000 点回来了）"));
        }
        // 反向对照：没有 `riichi_void`（普通舍张放铳 / 老服务端）时立直必须留着
        {
            TableModel mt;
            mt.applyEvent(proto::decodeLine(
                QByteArrayLiteral(R"({"ev":"riichi","seat":1,"stick_index":0,"sticks":1,"scores":[25000,24000,25000,25000]})"),
                nullptr));
            mt.applyEvent(proto::decodeLine(QByteArrayLiteral(
                R"({"ev":"agari","winner":2,"from":1,"tsumo":false,"scores_after":[25000,24000,28000,23000]})"),
                nullptr));
            check(mt.riichi(1), QStringLiteral("对照：无 riichi_void 时立直仍然成立"));
            checkEq(QString::number(mt.riichiSticks()), QStringLiteral("1"),
                    QStringLiteral("对照：供託仍留在盘上"));
        }
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
        checkEq(QString::number(ab.discardCmd(QStringLiteral("1m"), false)
                                    .value(QStringLiteral("ask_id")).toInt()),
                QStringLiteral("77"), QStringLiteral("普通出牌回包也必须带 ask_id"));

        // 摸切标记必须显式下发：服务端要靠它区分「从摸牌位取」还是「从暗手取」。
        // 只发牌码时，手里已有 5m、摸到的也是 5m 这种局面两端会取到不同副本（幽灵手牌）。
        {
            const QJsonObject sg = ab.discardCmd(QStringLiteral("5m"), true);
            check(sg.value(QStringLiteral("tsumogiri")).isBool(),
                  QStringLiteral("discard 回包必须带 tsumogiri（bool）"));
            checkEq(QString::number(sg.value(QStringLiteral("tsumogiri")).toBool() ? 1 : 0),
                    QStringLiteral("1"), QStringLiteral("摸切时 tsumogiri=true"));
            const QJsonObject hd = ab.discardCmd(QStringLiteral("5m"), false);
            checkEq(QString::number(hd.value(QStringLiteral("tsumogiri")).toBool() ? 1 : 0),
                    QStringLiteral("0"), QStringLiteral("手切时 tsumogiri=false"));
        }

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
        check(ab.discardCmd(QStringLiteral("1m"), false).isEmpty(),
              QStringLiteral("提交后 discardCmd 必须返回空对象"));
        check(ab.buttonForTest(QStringLiteral("立直")) == nullptr,
              QStringLiteral("提交后「立直」按钮已销毁"));
    }

    // ---------- 副露赤宝选择：**子列表**（碰只有一个按钮，用哪几张在子列表里选）----------
    // 服务端对「不用赤五 / 用赤五」各下发一条 `pon`（各带 `tiles`，见 PROTOCOL §3.6）。
    // 用户口径（2026-09 二次修订）：
    //   ① 赤宝**不能拼在「碰」后面**，要放进副露的**子列表栏**；
    //   ② 只有一种取法（手里只有赤五 / 只有普通五）时**无须区分两者** —— 单按钮直接执行、不弹子弹。
    // 红证：把 `rebuild()` 里那段改回"每条 pon 各出一个按钮 + 拼牌名"，下面两条立刻红。
    {
        const QString plainLabel = lang::t("ui.action.pon");
        const QString akaName = TileRenderer::label(QStringLiteral("0p"));
        const QString fiveName = TileRenderer::label(QStringLiteral("5p"));

        // ① 真有得选（两条 pon）→ **一个**「碰」按钮 + 子列表两条
        ActionBar ab;
        ab.setAsk(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"ask","ask_id":91,"seat":0,"kind":"claim","deadline_ms":15000,)"
            R"("options":[{"type":"pon","tiles":["5p","5p"]},)"
            R"({"type":"pon","tiles":["0p","5p"]},{"type":"pass"}]})"),
            nullptr));
        QPushButton* ponBtn = ab.buttonForTest(plainLabel);
        check(ponBtn != nullptr, QStringLiteral("「碰」按钮在（实际按钮：%1）")
                                     .arg(ab.buttonTextsForTest().join(QStringLiteral("/"))));
        check(ab.buttonForTest(plainLabel + QStringLiteral(" ") + akaName) == nullptr,
              QStringLiteral("按钮文案里**不许**再拼赤五（赤宝改放子列表，实际按钮：%1）")
                  .arg(ab.buttonTextsForTest().join(QStringLiteral("/"))));
        checkEq(QString::number(ab.buttonTextsForTest().size()), QStringLiteral("2"),
                QStringLiteral("两条 pon 只出一个按钮（另一个是「跳过」），实际：%1")
                    .arg(ab.buttonTextsForTest().join(QStringLiteral("/"))));
        const QStringList ponEntries = ab.menuEntriesForTest("pon");
        checkEq(QString::number(ponEntries.size()), QStringLiteral("2"),
                QStringLiteral("子列表里两条取法"));
        checkEq(ponEntries.value(0), QStringLiteral("%1 %2").arg(fiveName, fiveName),
                QStringLiteral("子列表第 1 条 = 不用赤五（普通牌优先）"));
        checkEq(ponEntries.value(1), QStringLiteral("%1 %2").arg(akaName, fiveName),
                QStringLiteral("子列表第 2 条 = 用赤五（写的是牌，不是拼在「碰」后面）"));
        QJsonObject sent;
        QObject::connect(&ab, &ActionBar::actionReady, [&](const QJsonObject& o) { sent = o; });
        ab.triggerMenuEntryForTest(QStringLiteral("pon"), 1);
        const QJsonArray akaTiles = sent.value(QStringLiteral("tiles")).toArray();
        checkEq(QString::number(akaTiles.size()), QStringLiteral("2"),
                QStringLiteral("点「用赤五」那条回包要带 tiles（两张）"));
        checkEq(akaTiles.isEmpty() ? QString() : akaTiles.at(0).toString(), QStringLiteral("0p"),
                QStringLiteral("回包的第一张就是赤五（服务端据此精确取牌）"));
        checkEq(sent.value(QStringLiteral("type")).toString(), QStringLiteral("pon"),
                QStringLiteral("回包 type 仍是 pon"));
        checkEq(QString::number(sent.value(QStringLiteral("ask_id")).toInt()),
                QStringLiteral("91"), QStringLiteral("赤宝选择回包也要带 ask_id"));
        sent = QJsonObject();
        ab.triggerMenuEntryForTest(QStringLiteral("pon"), 0);
        const QJsonArray plainTiles = sent.value(QStringLiteral("tiles")).toArray();
        checkEq(QString::number(plainTiles.size()), QStringLiteral("2"),
                QStringLiteral("不用赤五那条也要带 tiles（普通五优先）"));
        check(plainTiles.isEmpty() || !plainTiles.at(0).toString().startsWith(QLatin1Char('0')),
              QStringLiteral("不用赤五那条的第一张不能是赤牌，实际 %1")
                  .arg(plainTiles.isEmpty() ? QString() : plainTiles.at(0).toString()));

        // ② 只有一种取法（手里只有赤五）→ 单按钮直接执行，**任何地方都不区分**
        ActionBar solo;
        solo.setAsk(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"ask","ask_id":92,"seat":0,"kind":"claim","deadline_ms":15000,)"
            R"("options":[{"type":"pon","tiles":["0p","0p"]},{"type":"pass"}]})"),
            nullptr));
        checkEq(solo.buttonTextsForTest().join(QStringLiteral("/")),
                QStringLiteral("%1/%2").arg(plainLabel, lang::t("ui.action.pass")),
                QStringLiteral("只有赤五时按钮就是「碰」+「跳过」，不带任何牌名"));
        QJsonObject soloSent;
        QObject::connect(&solo, &ActionBar::actionReady,
                         [&](const QJsonObject& o) { soloSent = o; });
        QPushButton* soloBtn = solo.buttonForTest(plainLabel);
        check(soloBtn != nullptr, QStringLiteral("只有赤五时「碰」按钮在"));
        if (soloBtn != nullptr) {
            soloBtn->click();     // 单一取法：直接执行（不弹子列表，所以这里不会阻塞）
            const QJsonArray t = soloSent.value(QStringLiteral("tiles")).toArray();
            checkEq(QString::number(t.size()), QStringLiteral("2"),
                    QStringLiteral("单一取法也要带 tiles（服务端据此精确取牌）"));
            checkEq(t.isEmpty() ? QString() : t.at(0).toString(), QStringLiteral("0p"),
                    QStringLiteral("单一取法（只有赤五）回包仍带赤五牌码"));
        }
    }

    // ---------- 大明杠的赤宝选择：同样只做"真有得选"时的区分 ----------
    {
        const QString kanLabel = lang::t("ui.action.daiminkan");
        const QString akaName = TileRenderer::label(QStringLiteral("0p"));
        const QString fiveName = TileRenderer::label(QStringLiteral("5p"));
        ActionBar ab;
        ab.setAsk(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"ask","ask_id":93,"seat":0,"kind":"turn","deadline_ms":15000,)"
            R"("options":[{"type":"kan","kans":[)"
            R"({"kind":"daiminkan","tile":"5p","tiles":["5p","5p","5p"]},)"
            R"({"kind":"daiminkan","tile":"5p","tiles":["0p","5p","5p"]},)"
            R"({"kind":"ankan","tile":"9m"}]}]})"),
            nullptr));
        const QStringList entries = ab.menuEntriesForTest("kan");
        checkEq(QString::number(entries.size()), QStringLiteral("3"),
                QStringLiteral("杠子列表三条（两种大明杠取法 + 一个暗杠）"));
        checkEq(entries.value(0), QStringLiteral("%1 %2 %3 %4").arg(kanLabel, fiveName, fiveName, fiveName),
                QStringLiteral("大明杠第 1 条 = 不用赤五"));
        checkEq(entries.value(1), QStringLiteral("%1 %2 %3 %4").arg(kanLabel, akaName, fiveName, fiveName),
                QStringLiteral("大明杠第 2 条 = 用赤五（列的是牌，不拼在动作名后面）"));
        check(!entries.value(0).endsWith(akaName) && !entries.value(2).contains(akaName),
              QStringLiteral("不用赤五那条与暗杠那条都不许出现赤五字样，实际：%1")
                  .arg(entries.join(QStringLiteral(" / "))));
        QJsonObject sent;
        QObject::connect(&ab, &ActionBar::actionReady, [&](const QJsonObject& o) { sent = o; });
        ab.triggerMenuEntryForTest(QStringLiteral("kan"), 1);
        checkEq(sent.value(QStringLiteral("kind")).toString(), QStringLiteral("daiminkan"),
                QStringLiteral("回包 kind = daiminkan"));
        checkEq(sent.value(QStringLiteral("tile")).toString(), QStringLiteral("5p"),
                QStringLiteral("回包 tile = 被鸣的那张"));
        const QJsonArray kt = sent.value(QStringLiteral("tiles")).toArray();
        checkEq(kt.isEmpty() ? QString() : kt.at(0).toString(), QStringLiteral("0p"),
                QStringLiteral("用赤五那条回包要带精确牌码（第一张是赤五）"));

        // 单一取法：带着赤五牌码，但界面上**不区分**（按牌种写）
        ActionBar solo;
        solo.setAsk(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"ask","ask_id":94,"seat":0,"kind":"turn","deadline_ms":15000,)"
            R"("options":[{"type":"kan","kans":[)"
            R"({"kind":"daiminkan","tile":"5p","tiles":["0p","0p","0p"]}]}]})"),
            nullptr));
        checkEq(solo.menuEntriesForTest("kan").join(QStringLiteral("/")),
                QStringLiteral("%1 %2").arg(kanLabel, fiveName),
                QStringLiteral("只有一种取法时大明杠按牌种写，不出现赤五字样"));
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

    // ---------- 回归：自选座位的按钮状态必须**两个方向**都对 ----------
    // 报障：「由东南西北按下去，上一个按钮不会弹起；而反向点选的时候是正常的」。
    // 根因：`updateWaitingRoom()` 把「按 pid 反查自己的座位」与「按座位号把按钮置灰」
    // 写在**同一个 0→3 的循环**里 —— 座位号变大（东→南）时，循环先处理**我刚离开的那一格**
    // （号小、在前），此刻 `mySeat()` 还是旧值，那一格被判成"我坐着"而**永远置灰**；
    // 反过来点（号变小）时新座位排在旧座位**之前**，读到的已是新值，于是恰好正常。
    {
        MainWindow w;
        if (LobbyDialog* dlg = w.findChild<LobbyDialog*>())
            dlg->hide();
        w.setAutoAnswer(false);
        w.feedEventForTest(parseEv(R"({"ev":"hello_ok","pid":1001,"name":"我"})"));

        auto roomWithMeAt = [](int mySeat) {
            QJsonArray seats;
            for (int i = 0; i < 4; ++i) {
                QJsonObject s;
                s.insert(QStringLiteral("pid"), i == mySeat ? 1001 : 2000 + i);
                s.insert(QStringLiteral("name"),
                         i == mySeat ? QStringLiteral("我") : QStringLiteral("机器人"));
                s.insert(QStringLiteral("bot"), i != mySeat);
                s.insert(QStringLiteral("ready"), i != mySeat);
                s.insert(QStringLiteral("score"), 25000);
                seats.append(s);
            }
            QJsonObject ev;
            ev.insert(QStringLiteral("ev"), QStringLiteral("room"));
            ev.insert(QStringLiteral("id"), QStringLiteral("TEST"));
            ev.insert(QStringLiteral("name"), QStringLiteral("自检房"));
            ev.insert(QStringLiteral("playing"), false);
            ev.insert(QStringLiteral("seats"), seats);
            return ev;
        };
        auto seatEnabled = [&w](int seat) {
            QPushButton* b = w.seatButtonForTest(seat);
            return b != nullptr && b->isEnabled();
        };

        w.feedEventForTest(roomWithMeAt(0));      // 我坐在东
        check(!seatEnabled(0) && seatEnabled(1) && seatEnabled(2) && seatEnabled(3),
              QStringLiteral("自选座位：坐在东时，东置灰、其余三个可点"));

        w.feedEventForTest(roomWithMeAt(1));      // 东 → 南（座位号**变大**）
        check(seatEnabled(0),
              QStringLiteral("自选座位：东→南后，刚离开的东必须重新可点（不能停在置灰）"));
        check(!seatEnabled(1) && seatEnabled(2) && seatEnabled(3),
              QStringLiteral("自选座位：东→南后，南置灰、西/北可点"));

        w.feedEventForTest(roomWithMeAt(3));      // 南 → 北（继续变大）
        check(seatEnabled(0) && seatEnabled(1) && seatEnabled(2),
              QStringLiteral("自选座位：南→北后，前三个都必须可点"));
        check(!seatEnabled(3), QStringLiteral("自选座位：南→北后，北置灰"));

        w.feedEventForTest(roomWithMeAt(0));      // 北 → 东（反向）
        check(seatEnabled(1) && seatEnabled(2) && seatEnabled(3),
              QStringLiteral("自选座位：北→东（反向）后，其余三个都必须可点"));
        check(!seatEnabled(0), QStringLiteral("自选座位：北→东（反向）后，东置灰"));

        QJsonObject playing = roomWithMeAt(2);
        playing.insert(QStringLiteral("playing"), true);
        w.feedEventForTest(playing);
        check(!seatEnabled(0) && !seatEnabled(1) && !seatEnabled(2) && !seatEnabled(3),
              QStringLiteral("自选座位：牌局进行中四个按钮一律不可用"));
    }

    // ---------- 端到端接线：开关 → 策略 → ActionBar 组包 → sendCommand ----------
    // ---------- 建房报文必须带上「一位必要点数」（批次三）----------
    // 与上面那条同一个思路：**真的点那个按钮**，抓真正发出去的 `create_room` 报文 ——
    // 只断言 `lobbyrules::defaultRequiredPoints()` 是测不出"字段有没有进报文"的。
    {
        MainWindow w;
        QJsonObject createCmd;
        w.setCommandTapForTest([&](const QJsonObject& o) {
            if (o.value(QStringLiteral("cmd")).toString() == QLatin1String("create_room")) {
                createCmd = o;
            }
        });
        LobbyDialog* dlg = w.findChild<LobbyDialog*>();
        check(dlg != nullptr, QStringLiteral("大厅对话框存在"));
        QPushButton* createBtn = nullptr;
        if (dlg) {
            const QString label = lang::t(QStringLiteral("ui.lobby.create_room"));
            for (QPushButton* b : dlg->findChildren<QPushButton*>()) {
                if (b->text() == label) {
                    createBtn = b;
                    break;
                }
            }
        }
        check(createBtn != nullptr, QStringLiteral("找到大厅的「建房间」按钮"));
        if (createBtn) {
            // ⚠ 未连服务端时「建房间」按钮是**禁用**的，而 `QAbstractButton::click()` 对禁用按钮
            //   是个空操作（点了也不发信号）—— 所以自检里必须先启用它，否则这条断言会
            //   "看起来测了、其实什么都没发"（真连服务端的路径由 `--lobbytest` 覆盖）。
            createBtn->setEnabled(true);
            createBtn->click();
            const QJsonObject rules = createCmd.value(QStringLiteral("rules")).toObject();
            check(!createCmd.isEmpty() && rules.contains(QStringLiteral("required_points")),
                  QStringLiteral("建房报文带上了 required_points"));
            checkEq(QString::number(rules.value(QStringLiteral("required_points")).toInt()),
                    QStringLiteral("0"),
                    QStringLiteral("默认预设（M.League）的一位必要点数 = 0（不要求）"));
            // 顺带钉住"默认思考时间 20+5"确实上到报文（服务端默认值也是这一组）
            checkEq(QString::number(rules.value(QStringLiteral("thinking_base_ms")).toInt()),
                    QStringLiteral("5000"), QStringLiteral("默认思考时间：每巡 5000ms"));
            checkEq(QString::number(rules.value(QStringLiteral("thinking_bank_ms")).toInt()),
                    QStringLiteral("20000"), QStringLiteral("默认思考时间：额外 20000ms"));
        }
    }

    // ---------- 回归：聊天「发送」按钮必须真的能发出去 ----------
    // 真踩过的坑：`onChatSend()` 用 `qobject_cast<QLineEdit*>(sender())` 反推输入框 ——
    // **按钮点击时 sender 是 QPushButton**，cast 得到 nullptr、函数直接 return，
    // 于是"回车能发、点按钮毫无反应"（用户两次报障）。回车那条路恰好 sender 是输入框，
    // 所以只测回车是**测不出来**的：必须真的 `click()` 那个按钮。
    {
        MainWindow w;
        if (LobbyDialog* dlg = w.findChild<LobbyDialog*>())
            dlg->hide();
        w.setAutoAnswer(false);

        QStringList sentChats;
        w.setCommandTapForTest([&](const QJsonObject& o) {
            if (o.value(QStringLiteral("cmd")).toString() == QLatin1String("chat")) {
                sentChats << o.value(QStringLiteral("text")).toString();
            }
        });

        // 找「发送」按钮 + 同一页里的聊天输入框（不依赖私有成员：与 autoBarForTest 同一思路）。
        // ⚠ 输入框与按钮**同属一个 QHBoxLayout**，而那个布局是**布局项**不是 widget ——
        //   所以不能靠 `itemAt(i)->widget()` 找它，得在按钮所在页面上按"有占位文字的输入框"认。
        auto findSendPairs = [&w]() {
            QVector<QPair<QPushButton*, QLineEdit*>> out;
            const QString sendLabel = lang::t(QStringLiteral("ui.main.send"));
            for (QPushButton* b : w.findChildren<QPushButton*>()) {
                if (b->text() != sendLabel || !b->parentWidget()) {
                    continue;
                }
                QLineEdit* edit = nullptr;
                for (QLineEdit* e : b->parentWidget()->findChildren<QLineEdit*>()) {
                    if (!e->placeholderText().isEmpty()) {   // 聊天框都有占位提示
                        edit = e;
                        break;
                    }
                }
                out.append({ b, edit });
            }
            return out;
        };

        const auto pairs = findSendPairs();
        check(pairs.size() >= 2,
              QStringLiteral("等待页与牌桌页各应有一个「发送」按钮，实际 %1 个").arg(pairs.size()));
        for (int i = 0; i < pairs.size(); ++i) {
            QPushButton* btn = pairs.at(i).first;
            QLineEdit* edit = pairs.at(i).second;
            check(edit != nullptr,
                  QStringLiteral("第 %1 个「发送」按钮旁边必须有输入框").arg(i + 1));
            if (!btn || !edit) {
                continue;
            }
            const int before = sentChats.size();
            edit->setText(QStringLiteral("测试消息%1").arg(i + 1));
            btn->click();          // ← 这条就是原来静默失效的那条路径
            checkEq(QString::number(sentChats.size() - before), QStringLiteral("1"),
                    QStringLiteral("点「发送」按钮必须发出 chat 报文（第 %1 个）").arg(i + 1));
            if (sentChats.size() > before) {
                checkEq(sentChats.last(), QStringLiteral("测试消息%1").arg(i + 1),
                        QStringLiteral("发出的正文必须是输入框里的内容（第 %1 个）").arg(i + 1));
            }
            check(edit->text().isEmpty(),
                  QStringLiteral("发送后输入框必须清空（第 %1 个）").arg(i + 1));

            // ② 空白不发（回车与按钮同一条路径，这里钉住符号行为）
            const int beforeBlank = sentChats.size();
            edit->setText(QStringLiteral("   "));
            btn->click();
            checkEq(QString::number(sentChats.size() - beforeBlank), QStringLiteral("0"),
                    QStringLiteral("空白内容不发送（第 %1 个）").arg(i + 1));
        }
    }

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
                                              QJsonArray{QStringLiteral("1z")}}})));
        }
        // 座位 1：摸 5s → 手切 3p → 摸 2s → 摸切 → 摸 4s → 立直手切 4s
        arr3.append(entry(5, 1, obj({{QStringLiteral("ev"), QStringLiteral("draw")},
                                     {QStringLiteral("seat"), 1},
                                     {QStringLiteral("tile"), QStringLiteral("5s")}})));
        arr3.append(entry(6, -1, obj({{QStringLiteral("ev"), QStringLiteral("discard")},
                                      {QStringLiteral("seat"), 1},
                                      {QStringLiteral("tile"), QStringLiteral("6p")},
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
        arr3.append(entry(10, -1, obj({{QStringLiteral("ev"), QStringLiteral("dora_reveal")},
                                       {QStringLiteral("dora_indicators"),
                                        QJsonArray{QStringLiteral("3z"), QStringLiteral("4z")}}})));
        arr3.append(entry(11, -1, obj({{QStringLiteral("ev"), QStringLiteral("agari")},
                                       {QStringLiteral("winner"), 1},
                                       {QStringLiteral("from"), 0},
                                       {QStringLiteral("ura_indicators"),
                                        QJsonArray{QStringLiteral("2z")}}})));
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
        checkEq(QString::number(g.at(2).toArray().at(0).toInt()), QStringLiteral("41"),
                QStringLiteral("牌谱：表宝牌指示牌 1z → 41"));
        checkEq(QString::number(g.at(3).toArray().at(0).toInt()), QStringLiteral("42"),
                QStringLiteral("牌谱：里宝指示牌 2z → 42"));
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
        checkEq(QString::number(d1.at(0).toInt()), QStringLiteral("26"),
                QStringLiteral("牌谱：手切 6p → 26"));
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

        // ---- 完整牌谱（mjlog XML）----
        // 关键判据：① 结构；② 牌号范围；③ **摸切复用刚摸到的那张牌号**
        //（公开实现就是靠这个判断摸切，错了整局的手切/摸切都会标反）；
        // ④ 位域 `m` 用**照抄参考实现的解码公式**解回来，必须还原出同一副鸣牌。
        {
            const TenhouLog::MjlogResult mj = TenhouLog::buildMjlog(tp);
            check(mj.ok, QStringLiteral("mjlog：生成成功"));
            check(mj.xml.startsWith(QStringLiteral("<mjloggm ver=\"2.3\">")),
                  QStringLiteral("mjlog：根标签带版本"));
            check(mj.xml.contains(QStringLiteral("<TAIKYOKU oya=\"0\"/>")),
                  QStringLiteral("mjlog：TAIKYOKU"));
            check(mj.xml.contains(QStringLiteral("<INIT seed=\"0,0,1,0,0,")),
                  QStringLiteral("mjlog：INIT seed = 场局次,本场,供託,骰,骰,宝牌指示牌"));
            check(mj.xml.contains(QStringLiteral("<UN n0=\"甲\"")),
                  QStringLiteral("mjlog：UN 带四家名字"));
            check(mj.xml.contains(QStringLiteral("<DORA hai=")),
                  QStringLiteral("mjlog：DORA 有输出"));
            check(!mj.xml.contains(QStringLiteral("<RYUUKYOKU ")),
                  QStringLiteral("mjlog：这一局是和了，不该有流局标签"));
            {
                const QRegularExpression re(QStringLiteral("<([TUVWDEFG])(\\d+)/>"));
                auto it = re.globalMatch(mj.xml);
                int n = 0;
                bool allOk = true;
                while (it.hasNext()) {
                    const QRegularExpressionMatch mm = it.next();
                    const int id = mm.captured(2).toInt();
                    if (id < 0 || id > 135) {
                        allOk = false;
                    }
                    ++n;
                }
                check(n >= 7, QStringLiteral("mjlog：摸打标签个数合理（实际 %1）").arg(n));
                check(allOk, QStringLiteral("mjlog：摸打牌号都在 0..135"));
            }
            // 摸切复用：座位 1 摸了 2s 之后摸切，两张牌号必须一样
            {
                // 反向引用：要求两张牌号**相同**（摸切 = 打出的就是刚摸到的那张）
                const QRegularExpression re(QStringLiteral("<U(\\d+)/>\\s*<E\\1/>"));
                const QRegularExpressionMatch mm = re.match(mj.xml);
                check(mm.hasMatch(), QStringLiteral("mjlog：摸切那一对的牌号同号（= 刚摸到的那张）"));
                if (mm.hasMatch()) {
                    checkEq(mm.captured(1), QStringLiteral("76"),
                            QStringLiteral("mjlog：摸切的是刚摸到的 2s（牌号 76）"));
                }
            }
            check(mj.xml.contains(QStringLiteral("<REACH who=\"1\" step=\"1\"/>"))
                          && mj.xml.contains(QStringLiteral("<REACH who=\"1\" step=\"2\"/>")),
                  QStringLiteral("mjlog：立直两段都写了"));
            {
                const int i1 = mj.xml.indexOf(QStringLiteral("<REACH who=\"1\" step=\"1\"/>"));
                const int i2 = mj.xml.indexOf(QStringLiteral("<REACH who=\"1\" step=\"2\"/>"));
                const int idisc = mj.xml.indexOf(QStringLiteral("<E"), i1);
                check(i1 < idisc && idisc < i2,
                      QStringLiteral("mjlog：step=1 在宣言牌前、step=2 在其后"));
            }
            check(mj.xml.contains(QStringLiteral("<AGARI who=\"1\" fromWho=\"0\"")),
                  QStringLiteral("mjlog：AGARI 带和了家与放銃家"));
            check(mj.xml.contains(QStringLiteral("doraHaiUra=\"112\"")),
                  QStringLiteral("mjlog：AGARI 带里宝牌（2z → 牌号 112）"));
            check(mj.xml.contains(QStringLiteral("doraHai=\"108,116,120\"")),
                  QStringLiteral("mjlog：AGARI 的宝牌指示牌复用 INIT/DORA 那三个牌号"));
            check(mj.xml.contains(QStringLiteral("</mjloggm>")), QStringLiteral("mjlog：闭合标签"));
            const QString xp = outDir + QStringLiteral("/tenhou_export.xml");
            QString xerr;
            check(TenhouLog::writeMjlogFile(mj, xp, &xerr), QStringLiteral("mjlog：能写出文件"));
            QFile xf(xp);
            check(xf.open(QIODevice::ReadOnly), QStringLiteral("mjlog：文件可读"));
            const QString head = QString::fromUtf8(xf.readLine()).trimmed();
            check(head.startsWith(QStringLiteral("<?xml")), QStringLiteral("mjlog：首行是 XML 声明"));
            xf.close();
        }
        check(!TenhouLog::buildMjlog(empty).ok, QStringLiteral("mjlog：空记录不谎报成功"));

        // 位域 `m`：自己编的，用参考实现的解码公式解回来
        {
            ReplayModel mp;
            mp.setMeta(meta3);
            QJsonArray a2;
            a2.append(arr3.at(0));                       // replay_round
            for (int s = 0; s < 4; ++s) {
                a2.append(arr3.at(1 + s));               // 四条 round_start
            }
            a2.append(entry(5, 2, obj({{QStringLiteral("ev"), QStringLiteral("draw")},
                                       {QStringLiteral("seat"), 2},
                                       {QStringLiteral("tile"), QStringLiteral("2m")}})));
            a2.append(entry(6, -1, obj({{QStringLiteral("ev"), QStringLiteral("discard")},
                                        {QStringLiteral("seat"), 2},
                                        {QStringLiteral("tile"), QStringLiteral("2m")},
                                        {QStringLiteral("tsumogiri"), true}})));
            a2.append(entry(7, 1, obj({{QStringLiteral("ev"), QStringLiteral("draw")},
                                       {QStringLiteral("seat"), 1},
                                       {QStringLiteral("tile"), QStringLiteral("2m")}})));
            QJsonArray ponTiles;
            ponTiles.append(QStringLiteral("5m"));
            ponTiles.append(QStringLiteral("5m"));
            ponTiles.append(QStringLiteral("5m"));
            a2.append(entry(8, -1, obj({{QStringLiteral("ev"), QStringLiteral("meld")},
                                        {QStringLiteral("seat"), 1},
                                        {QStringLiteral("kind"), QStringLiteral("pon")},
                                        {QStringLiteral("tiles"), ponTiles},
                                        {QStringLiteral("from"), 2},
                                        {QStringLiteral("called_index"), 0}})));
            mp.addEntries(a2);
            mp.build();
            const TenhouLog::MjlogResult m2 = TenhouLog::buildMjlog(mp);
            const QRegularExpression reN(QStringLiteral("<N who=\"1\" m=\"(\\d+)\"/>"));
            const QRegularExpressionMatch mm = reN.match(m2.xml);
            check(mm.hasMatch(), QStringLiteral("mjlog：碰写成了 <N who m>"));
            if (mm.hasMatch()) {
                const int m = mm.captured(1).toInt();
                QString kind;
                int dir = -1;
                const QVector<int> dec = TenhouLog::decodeMeldForTest(m, &kind, &dir);
                checkEq(kind, QStringLiteral("pon"), QStringLiteral("mjlog：位域解回来是「碰」"));
                checkEq(QString::number(dir), QStringLiteral("1"),
                        QStringLiteral("mjlog：相对座位 = 対面（座位 1 碰座位 2 的牌）"));
                checkEq(QString::number(dec.size()), QStringLiteral("4"),
                        QStringLiteral("mjlog：碰解出 4 项（被鸣 + 3 张）"));
                QSet<int> kinds;
                for (int i = 1; i < dec.size(); ++i) {
                    kinds.insert(dec.at(i) / 4);
                }
                checkEq(QString::number(kinds.size()), QStringLiteral("1"),
                        QStringLiteral("mjlog：碰的三张同种类"));
                if (!kinds.isEmpty()) {
                    checkEq(QString::number(*kinds.constBegin()), QStringLiteral("4"),
                            QStringLiteral("mjlog：种类 = 5m（4）"));
                }
                check(dec.size() >= 2 && dec.mid(1).contains(dec.at(0)),
                      QStringLiteral("mjlog：被鸣的那张属于这副鸣牌"));
            }
        }
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
        checkEq(QString::number(lang::keyCount()), QStringLiteral("442"),
                QStringLiteral("语言文件条目数（新增/删除 key 必须同步这条断言）"));
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
        checkEq(QString::number(family.value(QStringLiteral("reason"))), QStringLiteral("7"),
                QStringLiteral("reason.* 条目数（荒牌/流满/九种九牌/四风/四杠/四家立直/三家和了）"));
        checkEq(QString::number(family.value(QStringLiteral("error"))), QStringLiteral("12"),
                QStringLiteral("error.* 条目数（含回放的两个码 + bad_seat）"));
        checkEq(QString::number(family.value(QStringLiteral("ui"))), QStringLiteral("318"),
                QStringLiteral("ui.* 条目数（界面固定文案；**代码里的中文都在这族里**）"));
        // 结束对局投票 / 掉线托管：这两族同样是"漏一条 key 就会显示裸键"，
        // 所以除了上面那条总数断言，再把**用得着的几条**逐条点名（占位符也点）。
        check(!lang::t(QStringLiteral("ui.vote.end")).isEmpty()
                  && !lang::t(QStringLiteral("ui.vote.agree")).isEmpty()
                  && !lang::t(QStringLiteral("ui.vote.disagree")).isEmpty()
                  && !lang::t(QStringLiteral("ui.vote.idle")).isEmpty()
                  && !lang::t(QStringLiteral("ui.vote.started")).isEmpty()
                  && !lang::t(QStringLiteral("ui.vote.passed")).isEmpty()
                  && lang::t(QStringLiteral("ui.vote.rejected")).contains(QStringLiteral("%1"))
                  && lang::t(QStringLiteral("ui.vote.running")).contains(QStringLiteral("%4")),
              QStringLiteral("投票族文案齐全（含 %1/%4 占位符）"));
        check(!lang::t(QStringLiteral("ui.vote.denied_cooldown")).isEmpty()
                  && !lang::t(QStringLiteral("ui.vote.denied_running")).isEmpty()
                  && !lang::t(QStringLiteral("ui.vote.denied_not_playing")).isEmpty(),
              QStringLiteral("投票被拒的三种原因都有文案"));
        check(!lang::t(QStringLiteral("ui.main.away_tag")).isEmpty()
                  && !lang::t(QStringLiteral("ui.main.identity_saved")).isEmpty(),
              QStringLiteral("掉线托管标记与身份提示的文案都在语言文件里"));
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
            // 「依次浮现」的落地检查：正文必须真的进了 QTextBrowser。
            // 只断言 htmlBlocksForTest 的条数是不够的 —— 那个函数与构造函数的调用
            // 之间还有一段（谁把块喂给浏览器），错了照样是空白框。
            {
                const QList<QTextBrowser*> brs = rd.findChildren<QTextBrowser*>();
                checkEq(QString::number(brs.size()), QStringLiteral("1"),
                        QStringLiteral("结算弹窗应当有一个正文浏览器"));
                if (!brs.isEmpty()) {
                    check(brs.first()->toPlainText().contains(QStringLiteral("x")),
                          QStringLiteral("结算正文必须进入浏览器（依次浮现拿的是同一份块），实际：%1")
                              .arg(brs.first()->toPlainText().left(60)));
                }
                // 真机路径：多块内容 + 真实尺寸 + 真实计时器，抓成图看正文是否**真的显示出来**。
                // 这一条是为「内容进了浏览器但被滚出可视区」那个坑加的 —— 只查 toPlainText
                // 是查不出滚动位置的（真机上结算弹窗当时只剩标题，正文在滚动区外）。
                const QString bigHtml = QStringLiteral("<h2>标题</h2><ul><li>役一</li></ul>")
                                        + QStringLiteral("<p>合计 <b>3</b> 番 40 符</p>")
                                        + QStringLiteral("<h3>点数收支</h3><table width='100%'>")
                                        + QStringLiteral("<tr><td>甲</td><td>+8000</td></tr>")
                                        + QStringLiteral("<tr><td>乙</td><td>-2000</td></tr>")
                                        + QStringLiteral("<tr><td>丙</td><td>-2000</td></tr>")
                                        + QStringLiteral("<tr><td>丁</td><td>-4000</td></tr></table>");
                ResultDialog big(QStringLiteral("t"), bigHtml);
                big.resize(560, 460);
                for (int i = 0; i < 8; ++i) {
                    QCoreApplication::processEvents();
                    QThread::msleep(30);
                }
                const QImage img = big.grab().toImage();
                const QString shot = dir.absoluteFilePath(QStringLiteral("result_dialog.png"));
                img.save(shot);
                check(shot.endsWith(QStringLiteral("result_dialog.png")),
                      QStringLiteral("结算弹窗出图：%1").arg(shot));
                // 正文区里必须出现**足够多**的深色像素（文字），而不是一片空白
                int dark = 0;
                for (int y = 0; y < img.height(); ++y) {
                    for (int x = 0; x < img.width(); ++x) {
                        const QRgb c = img.pixel(x, y);
                        if (qRed(c) < 120 && qGreen(c) < 120 && qBlue(c) < 120)
                            ++dark;
                    }
                }
                check(dark > 220,
                      QStringLiteral("结算弹窗正文必须真的画出来（深色文字像素 %1 > 220）").arg(dark));
                big.close();
            }
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
                               R"("scores_after":[33000,23000,23000,23000],"pao":{"seat":-1,"seats":[]}})")
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

        // ⑩ 自主摸 vs 荣和（用户报障：「自摸时自摸张在结算界面出现两次」）
        //    自摸的 `hand` 里**含**和了牌（14 张），荣和的 `hand` 不含（13 张）——
        //    示意图必须先把自摸那张从手牌里摘掉，再单独摆到和了牌位；
        //    并且自摸**竖置**（裸牌码）、荣和**横置**（后置 `-`）。
        {
            const QChar aside(0x1D);
            const QChar rowSep(0x1F);
            auto handOf = [rowSep](const QString& row) {
                return row.section(QChar(0x1E), 1).section(rowSep, 0, 0);
            };
            // 自摸：手牌 14 张，5m 出现两次（一张在手里，一张是和了牌）
            const QJsonObject tsumoEv = proto::decodeLine(
                QStringLiteral(R"({"ev":"agari","winner":0,"from":-1,"tsumo":true,)"
                               R"("hand":["1m","2m","3m","4m","5m","6m","7m","8m","9m","9m","9m",)"
                               R"("5m","5m","5m"],"melds":[],"winning_tile":"5m",)"
                               R"("dora_indicators":["1p"],"ura_indicators":[],"yaku":[],"han":1,)"
                               R"("fu":40,"yakuman":0,"limit":"","base_points":0,)"
                               R"("score_delta":[0,0,0,0],"scores_after":[0,0,0,0]})").toUtf8(),
                nullptr);
            TableModel tm;
            const QString sTsumo = ResultDialog::schematicOf(tsumoEv, &tm, true);
            const QString handRow = handOf(sTsumo);
            const QStringList parts = handRow.split(aside);
            checkEq(QString::number(parts.size()), QStringLiteral("2"),
                    QStringLiteral("自摸示意：手牌段 + 和了牌段两段"));
            if (parts.size() == 2) {
                const QStringList handTiles = parts.at(0).split(QLatin1Char(' '));
                checkEq(QString::number(handTiles.size()), QStringLiteral("13"),
                        QStringLiteral("自摸示意：和了牌必须从手牌里摘掉（应剩 13 张）"));
                checkEq(parts.at(1), QStringLiteral("5m"),
                        QStringLiteral("自摸：和了牌**竖置**（裸牌码，不带 `-`）"));
            }

            // 荣和：手牌只有 13 张、winning_tile 另发 → 不摘，且和了牌横置
            const QJsonObject ronEv = proto::decodeLine(
                QStringLiteral(R"({"ev":"agari","winner":0,"from":1,"tsumo":false,)"
                               R"("hand":["1m","2m","3m","4m","5m","6m","7m","8m","9m","9m","9m",)"
                               R"("5m","5m"],"melds":[],"winning_tile":"5m",)"
                               R"("dora_indicators":["1p"],"ura_indicators":[],"yaku":[],"han":1,)"
                               R"("fu":40,"yakuman":0,"limit":"","base_points":0,)"
                               R"("score_delta":[0,0,0,0],"scores_after":[0,0,0,0]})").toUtf8(),
                nullptr);
            const QString sRon = ResultDialog::schematicOf(ronEv, &tm, true);
            const QStringList rparts = handOf(sRon).split(aside);
            checkEq(QString::number(rparts.size()), QStringLiteral("2"),
                    QStringLiteral("荣和示意：手牌段 + 和了牌段两段"));
            if (rparts.size() == 2) {
                checkEq(QString::number(rparts.at(0).split(QLatin1Char(' ')).size()),
                        QStringLiteral("13"), QStringLiteral("荣和示意：手牌保持 13 张（不摘）"));
                checkEq(rparts.at(1), QStringLiteral("5m-"),
                        QStringLiteral("荣和：和了牌**横置**（后置 `-`）"));
            }
        }

        // ⑪ 结算 HTML 的块切分（「文字依次浮现」的根据）+ **标签配平**。
        //    真踩过的坑：役满分支的合计文案曾经只有 `</p>` 没有 `<p>`
        //    （`ui.result.total_yakuman` 少了个开标签），块扫描于是把后面的
        //    「点数收支」表当成嵌套内容吞掉 —— 役满时结算界面只剩标题。
        //    这里对 y1（役满）/ n1（普通役）两份 HTML 都钉住条数与配平。
        {
            auto tagsBalanced = [](const QString& h) {
                const QStringList names = { QStringLiteral("p"), QStringLiteral("h3"),
                                            QStringLiteral("ul"), QStringLiteral("table"),
                                            QStringLiteral("tr") };
                for (const QString& t : names) {
                    const int open = h.count(QStringLiteral("<%1").arg(t));
                    const int close = h.count(QStringLiteral("</%1>").arg(t));
                    if (open != close)
                        return false;
                }
                return true;
            };
            check(tagsBalanced(y1),
                  QStringLiteral("役满结算 HTML 的标签必须配平（total_yakuman 少过 <p>）：%1")
                      .arg(y1.left(160)));
            check(tagsBalanced(n1),
                  QStringLiteral("普通役结算 HTML 的标签必须配平"));
            const QStringList yb = ResultDialog::htmlBlocksForTest(y1);
            check(yb.size() >= 4,
                  QStringLiteral("役满结算至少切成 4 块（标题/役种/合计/收支），实际 %1")
                      .arg(yb.size()));
            const QStringList nb = ResultDialog::htmlBlocksForTest(n1);
            check(nb.size() >= 4,
                  QStringLiteral("普通役结算至少切成 4 块，实际 %1").arg(nb.size()));
            // 最后一块必须真的含「点数收支」表 —— 只数条数会被"吞掉一块"蒙过去
            check(!nb.isEmpty() && nb.last().contains(QStringLiteral("<table")),
                  QStringLiteral("最后一块应当是点数收支表（含 <table>）"));
        }
    }

    // ---------- 新增：音效（离线合成 WAV + 材质包可替换 + 后端可用性）----------
    {
        sound::Player& sp = sound::Player::instance();
        sp.init();
        log << QStringLiteral("[i] 音效后端：%1（可用 %2）")
                   .arg(sp.backendName(), sp.available() ? QStringLiteral("是") : QStringLiteral("否"));

        // ① 8 个音效都要能取到素材（目录优先，qrc 兜底）。
        //    取不到就是"开关开着但没声音"，玩家无从判断，所以必须钉住。
        const QStringList names = sound::allNames();
        checkEq(QString::number(names.size()), QStringLiteral("8"),
                QStringLiteral("音效种类数（吃/碰/杠/立直/自摸/荣和/提示/摸牌）"));
        checkEq(QString::number(sp.loadedCountForTest()), QStringLiteral("8"),
                QStringLiteral("8 个音效都要能取到 WAV 素材"));
        for (const QString& n : names) {
            const int size = sp.dataSizeForTest(n);
            check(size > 1000,
                  QStringLiteral("音效 %1 的 WAV 大小应 > 1KB，实际 %2").arg(n).arg(size));
        }
        // qrc 兜底：删掉整个 sfx/ 目录也要有声音（与 tiles/ 同一套约定）
        check(QFile::exists(QStringLiteral(":/sfx/chi.wav")),
              QStringLiteral("qrc 兜底里必须有音效（删掉 sfx/ 目录也不会没声音）"));

        // ② 材质包里的 `sfx/` 能覆盖：放进一个包 → packSfx 取到包内的那份
        {
            const QString packDir = dir.absoluteFilePath(QStringLiteral("sfxpack"));
            QDir().mkpath(packDir + QStringLiteral("/assets/sfx"));
            {
                QFile mf(packDir + QStringLiteral("/theme.json"));
                if (mf.open(QIODevice::WriteOnly | QIODevice::Text)) {
                    mf.write("{\"sfx\":\"/assets/sfx\"}\n");
                }
            }
            // 包内放一个**可辨认**的 wav（内容随意，只要非空且与默认不同）
            const QByteArray fake("RIFF____WAVEfmt ");
            {
                QFile wf(packDir + QStringLiteral("/assets/sfx/pon.wav"));
                if (wf.open(QIODevice::WriteOnly)) {
                    wf.write(fake);
                }
            }
            const Theme::Status st = Theme::instance().load(packDir);
            check(st.loaded, QStringLiteral("音效材质包应当载入成功"));
            check(st.applied.contains(QStringLiteral("sfx")),
                  QStringLiteral("材质包清单里的 sfx 类别应生效，实际：%1")
                      .arg(st.applied.join(QStringLiteral(","))));
            checkEq(QString::number(Theme::instance().packSfx(QStringLiteral("pon")).size()),
                    QString::number(fake.size()),
                    QStringLiteral("材质包里的 pon.wav 应被取到（逐文件对应）"));
            check(Theme::instance().packSfx(QStringLiteral("chi")).isEmpty(),
                  QStringLiteral("包里没给的音效取不到（其余用默认，不串味）"));
            // 复原：换回无材质包，并按"换包"的正规路径清缓存
            Theme::instance().load(QString());
            sp.clearCache();
            checkEq(QString::number(sp.loadedCountForTest()), QStringLiteral("8"),
                    QStringLiteral("换回默认素材后 8 个音效仍可取到"));
        }

        // ③ 开关与后端：关掉不能出声、音量钳制在 0..100
        sp.setEnabled(false);
        check(!sp.enabled(), QStringLiteral("音效开关能关"));
        sp.setVolume(150);
        checkEq(QString::number(sp.volume()), QStringLiteral("100"),
                QStringLiteral("音量超上限要被钳到 100"));
        sp.setVolume(-5);
        checkEq(QString::number(sp.volume()), QStringLiteral("0"),
                QStringLiteral("音量负值要被钳到 0"));
        sp.setVolume(70);
        sp.setEnabled(true);

        // ④ 后端真的**接受了**素材吗？（"文件找到了"不等于"后端认得它"）
        //    多媒体后端下 `QSoundEffect::status()` 会从 Loading 变成 Ready：
        //    这是无头环境能拿到的最强证据（WAV 头写坏会被这里抓住）。
        //    ⚠ 它仍然**证明不了扬声器真的响了** —— 那件事只能由人听。
        if (sp.backendName() == QLatin1String("qsoundeffect")) {
            const QString notify = QLatin1String(sound::name::Notify);
            sp.play(notify);
            for (int i = 0; i < 40 && !sp.effectReadyForTest(notify); ++i) {
                QCoreApplication::processEvents();
                QThread::msleep(25);
            }
            check(sp.effectReadyForTest(notify),
                  QStringLiteral("Qt Multimedia 后端必须接受这份 WAV（status == Ready）"));

            // ⑤ 实例池 + `allowOverlap`：报障「只有第一小局有音效」的现场是
            //    「同一个 QSoundEffect 被反复 stop()+play()」——旧实现**忽略**了
            //    `allowOverlap=false`（摸牌那条路明确要求"别叠"），每次都停掉重放。
            //    现在：池子里换一个空闲实例（根本不停），池子都忙时才按 allowOverlap 决定。
            for (const QString& n : names) {
                check(sp.poolSizeForTest(n) >= 2,
                      QStringLiteral("音效 %1 要有实例池（>=2），实际 %2")
                              .arg(n)
                              .arg(sp.poolSizeForTest(n)));
            }
            const QString draw = QLatin1String(sound::name::Draw);
            sp.play(draw);                       // 先确保这个音效已 Ready
            for (int i = 0; i < 40 && !sp.effectReadyForTest(draw); ++i) {
                QCoreApplication::processEvents();
                QThread::msleep(25);
            }
            // 叠放（默认）：连着两次都要真的放出去 —— 池子换实例，不丢音
            const int kanBefore = sp.playCountForTest(QLatin1String(sound::name::Kan));
            sp.play(QLatin1String(sound::name::Kan), true);
            sp.play(QLatin1String(sound::name::Kan), true);
            checkEq(QString::number(sp.playCountForTest(QLatin1String(sound::name::Kan))
                                    - kanBefore),
                    QStringLiteral("2"),
                    QStringLiteral("allowOverlap=true：连续两次都要放（换池内实例，不停不丢）"));
            // 不叠（摸牌那条路的口径）：上一次还在播时必须**跳过**。
            // ⚠ 用 `ron`（≈0.5 s）而不是 `draw`（≈70 ms）来测：70 ms 的窗口太窄，
            //   两次调用之间稍微慢一点（事件循环/GC）就变成"上一次已经放完"了。
            const QString quiet = QLatin1String(sound::name::Ron);
            sp.play(quiet);                      // 热身（确保已 Ready 并进入过 playing）
            for (int i = 0; i < 80 && !sp.effectReadyForTest(quiet); ++i) {
                QCoreApplication::processEvents();
                QThread::msleep(25);
            }
            for (int i = 0; i < 200 && sp.effectPlayingForTest(quiet); ++i) {
                QCoreApplication::processEvents();
                QThread::msleep(10);
            }
            check(!sp.effectPlayingForTest(quiet),
                  QStringLiteral("自检前提：热身那次已经放完（否则测不出「跳过」）"));
            const int skipBefore = sp.overlapSkipCountForTest();
            const int quietBefore = sp.playCountForTest(quiet);
            sp.play(quiet, false);
            bool started = false;
            for (int i = 0; i < 20 && !started; ++i) {
                started = sp.effectPlayingForTest(quiet);
                if (!started) {
                    QCoreApplication::processEvents();
                    QThread::msleep(10);
                }
            }
            check(started, QStringLiteral("自检前提：play() 之后立刻进入 playing"
                                          "（否则下面的跳过断言没有意义）"));
            sp.play(quiet, false);
            const int quietAfter = sp.playCountForTest(quiet);
            checkEq(QString::number(quietAfter - quietBefore), QStringLiteral("1"),
                    QStringLiteral("allowOverlap=false：上一次还在播时第二次**跳过**"
                                   "（旧实现会停掉重放 —— 那条路径正是把声卡搞哑的嫌疑）"));
            check(sp.overlapSkipCountForTest() > skipBefore,
                  QStringLiteral("跳过计数被走到"));
        } else {
            log << QStringLiteral("[i] 后端 %1 没有 Ready 状态可查（跳过该断言）")
                       .arg(sp.backendName());
        }

        // ⑥ 「自称在播」必须与 **WAV 时长** 对账（2026-09 二次报障：
        //    「音效在有副露 / 在可副露时选择不副露之后消失」）。
        //
        //    真因机制：设备异常后 `QSoundEffect::isPlaying()` 会**永远为真**，而"上一次还在播
        //    就跳过"这条判据会因此把那条音效**永久静音**。修法是记每个实例的开始时刻、
        //    超过「时长 + 余量」就判卡死并复用。这里把两件事都钉住：
        //      ① WAV 时长解析得对（拿真实素材对账，不是拿假数据自证）；
        //      ② 判据本身对"卡死实例"的处理 —— **红证**：把 `pickSlot` 里那句
        //         `ageMs < limit` 去掉（只看 isPlaying），下面第一条断言立刻变红。
        {
            const int drawMs = sound::wavDurationMs(sp.dataForTest(QLatin1String(sound::name::Draw)));
            const int ronMs = sound::wavDurationMs(sp.dataForTest(QLatin1String(sound::name::Ron)));
            check(drawMs > 20 && drawMs < 400,
                  QStringLiteral("摸牌音效时长解析（应 ≈140ms），实际 %1 ms").arg(drawMs));
            check(ronMs > 600 && ronMs < 2000,
                  QStringLiteral("荣和音效时长解析（应 ≈1050ms），实际 %1 ms").arg(ronMs));
            checkEq(QString::number(sound::wavDurationMs(QByteArrayLiteral("RIFFxxxxWAVEjunk"))),
                    QStringLiteral("0"),
                    QStringLiteral("不是合法 WAV 时时长返回 0（退回保守上限，绝不静默永恒在播）"));

            const qint64 dur = drawMs > 0 ? drawMs : 140;
            const qint64 stale = dur + sound::kStuckMarginMs + 50;   // 早该结束了
            const qint64 fresh = 10;                                 // 刚刚才播
            // 三个实例都自称在播、但都"卡"了很久 → 必须还能挑出一个来复用（不能全军覆没）
            checkEq(QString::number(sound::pickSlot({ true, true, true },
                                                    { stale, stale, stale }, int(dur), true)),
                    QStringLiteral("0"),
                    QStringLiteral("自称在播但已超过时长上限 → 仍要挑一个出来复用（否则该音效永久静音）"));
            // `allowOverlap=false`：「真在播」才有否决权
            checkEq(QString::number(sound::pickSlot({ true, false, false }, { fresh, stale, stale },
                                                    int(dur), false)),
                    QStringLiteral("-1"),
                    QStringLiteral("allowOverlap=false：确实还在响 → 跳过（不叠口径不变）"));
            checkEq(QString::number(sound::pickSlot({ true, false, false }, { stale, stale, stale },
                                                    int(dur), false)),
                    QStringLiteral("1"),
                    QStringLiteral("allowOverlap=false：卡死的那个**没有否决权**——"
                                   "还有空闲实例就照放（旧实现永久哑在这里）"));
            checkEq(QString::number(sound::pickSlot({ true, true, true }, { fresh, fresh, fresh },
                                                    int(dur), true)),
                    QStringLiteral("-2"),
                    QStringLiteral("池子都在真播 → 放弃这次（叠放口径也不再 stop() 硬插）"));
            checkEq(QString::number(sound::pickSlot({ false, false, false }, { stale, stale, stale },
                                                    int(dur), false)),
                    QStringLiteral("0"),
                    QStringLiteral("都空闲 → 用第一个"));
            checkEq(QString::number(sound::pickSlot({ true, false, true }, { stale, fresh, stale },
                                                    int(dur), true)),
                    QStringLiteral("1"),
                    QStringLiteral("混合：优先用**真正空闲**的那个，卡死的次之，不动真在播的"));
            // 自检期间真的播过之后，卡死计数**不该**被无端增加（没有假阳性）
            checkEq(QString::number(sp.stuckStopCountForTest()), QStringLiteral("0"),
                    QStringLiteral("正常播放不该被判成卡死（无假阳性）"));
        }
    }

    // ---------- 观战：对局中入局 = 无座位（四家牌背 + 视角可切） ----------
    // 报障：对局中入局会被放行进入"未定义的观战状态" —— 旧代码把 `state.seat = -1`
    // 用 qBound 夹成 0 并 `m_hasSeat = true`，于是观战者被当成"东家、手里没牌"的玩家。
    {
        TableModel m;
        m.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"spectate","room":"AB12"})"),
            nullptr));
        check(m.spectating(), QStringLiteral("spectate 事件 → 进入观战模式"));
        check(!m.hasSeat(), QStringLiteral("观战者没有座位（hasSeat=false）"));

        // 服务端随后补的公开快照：seat = -1
        m.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"state","phase":"playing","seat":-1,"spectate":true,"dealer":2,)"
            R"("drawn_seat":2,"turn":2,"round":{"bakaze":"E","kyoku":2,"honba":0,)"
            R"("riichi_sticks":0},"scores":[25000,24000,26000,25000],)"
            R"("melds":[[],[],[],[]],"discards":[[],["1m"],[],[]],)"
            R"("riichi":[false,false,false,false],"furiten":[false,false,false,false],)"
            R"("dora_indicators":["5p"],"tiles_left":60,"dead_wall_left":4})"),
            nullptr));
        check(m.spectating(), QStringLiteral("seat=-1 的快照仍然保持观战（不被夹成座位 0）"));
        check(!m.hasSeat(), QStringLiteral("观战快照不得把 hasSeat 置真"));
        check(m.hand().isEmpty(), QStringLiteral("观战者手里没有牌"));
        checkEq(QString::number(m.dealer()), QStringLiteral("2"),
                QStringLiteral("快照要带上 dealer（自风显示靠它）"));
        checkEq(QString::number(m.drawnSeat()), QStringLiteral("2"),
                QStringLiteral("快照要带上 drawn_seat（半场进入时各家张数靠它）"));
        checkEq(QString::number(m.concealedCount(2)), QStringLiteral("14"),
                QStringLiteral("刚摸牌那家按 14 张画（13 + 摸牌）"));
        checkEq(QString::number(m.concealedCount(0)), QStringLiteral("13"),
                QStringLiteral("其余家 13 张"));
        checkEq(QString::number(m.kyoku()), QStringLiteral("2"),
                QStringLiteral("快照里的场次要吃进来（半场进入不能从东 1 局重来）"));
        // 视角切换：只改"哪家画在下方"，不改任何判定
        const int before = m.mySeat();
        m.setViewSeat(3);
        checkEq(QString::number(m.viewSeat()), QStringLiteral("3"), QStringLiteral("视角切到 3"));
        checkEq(QString::number(m.mySeat()), QStringLiteral("3"),
                QStringLiteral("观战下 mySeat 就是视角座位（旋转用）"));
        check(before != m.mySeat(), QStringLiteral("切换视角确实换了座位号"));
        check(!m.hasSeat(), QStringLiteral("切视角不会让观战者变成有座位"));
        // 回到有座位：接一份正常 state 必须退出观战
        m.applyEvent(proto::decodeLine(QByteArrayLiteral(
            R"({"ev":"state","phase":"playing","seat":1,"spectate":false,"dealer":0,)"
            R"("turn":0,"drawn_seat":0,"round":{"bakaze":"E","kyoku":1,"honba":0,)"
            R"("riichi_sticks":0},"scores":[25000,25000,25000,25000],)"
            R"("hand":["1m","2m","3m"],"melds":[[],[],[],[]],"discards":[[],[],[],[]],)"
            R"("riichi":[false,false,false,false],"furiten":[false,false,false,false],)"
            R"("dora_indicators":[],"tiles_left":69,"dead_wall_left":4})"),
            nullptr));
        check(!m.spectating(), QStringLiteral("正常快照（seat>=0）退出观战"));
        check(m.hasSeat(), QStringLiteral("正常快照恢复 hasSeat"));
        checkEq(QString::number(m.mySeat()), QStringLiteral("1"),
                QStringLiteral("正常快照的座位号照旧"));
    }

    // ---------- 回归：身份（uuid）握手 + 掉线托管标记 + 结束对局投票 ----------
    // 三条 2026-09 需求，客户端这一侧各有一半责任：
    //   ① 身份：服务端问 → 客户端报上本地保存的；服务端给新的 → **必须存下来**；
    //   ② 掉线托管：服务端不换机器人、只自动摸切，界面上要看得见"谁掉线了"；
    //   ③ 投票：界面给「结束对局」+「同意/不同意」，**计票与冷却全在服务端**
    //      （客户端只显示，连倒计时都只是本地推算 —— 改客户端改不动规则）。
    {
        const QString dir = outDir + QStringLiteral("/uuid_vote_test");
        QDir().mkpath(dir);
        const QString path = dir + QStringLiteral("/settings.json");
        QFile::remove(path);
        const QString kOld = QStringLiteral("6f1c1f0e-8f4a-4a1f-9d5f-2c3a4b5c6d7e");
        const QString kNew = QStringLiteral("11112222-3333-4444-5555-666677778888");

        // ---- ① uuid 存得住、坏值被清掉 ----
        {
            Settings st;
            st.uuid = kOld;
            check(st.save(path), QStringLiteral("身份：uuid 能写进设置文件"));
            Settings back = Settings::load(path);
            checkEq(back.uuid, kOld, QStringLiteral("身份：uuid 往返（下次连接才报得出身份）"));

            QJsonObject bad;
            bad.insert(QStringLiteral("uuid"), QStringLiteral("not-a-uuid"));
            bad.insert(QStringLiteral("host"), QStringLiteral("10.0.0.9"));
            QFile f(path);
            if (f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
                f.write(QJsonDocument(bad).toJson());
                f.close();
            }
            QStringList repaired;
            Settings fixed = Settings::load(path, &repaired);
            checkEq(fixed.uuid, QString(),
                    QStringLiteral("身份：形状不对的 uuid 被清掉（= 还没有身份）"));
            check(repaired.contains(QStringLiteral("uuid")),
                  QStringLiteral("身份：坏 uuid 出现在重置清单里（不静默）"));
            checkEq(fixed.host, QStringLiteral("10.0.0.9"),
                    QStringLiteral("身份：清 uuid 不影响别的键"));
        }

        // ---- ① uuid 握手：问 → 答；服务端给的新身份存下来 ----
        {
            MainWindow w;
            Settings st;
            st.uuid = kOld;
            w.applySettings(st, path);
            QVector<QJsonObject> sent;
            w.setCommandTapForTest([&sent](const QJsonObject& o) { sent.append(o); });

            w.feedEventForTest(parseEv(R"({"ev":"uuid_ask"})"));
            check(!sent.isEmpty()
                          && sent.last().value(QStringLiteral("cmd")).toString()
                                  == QLatin1String("uuid"),
                  QStringLiteral("身份：收到 uuid_ask 要回一条 uuid 命令"));
            checkEq(sent.last().value(QStringLiteral("uuid")).toString(), kOld,
                    QStringLiteral("身份：把本地保存的 uuid 报上去"));

            sent.clear();
            w.feedEventForTest(parseEv(
                    R"({"ev":"uuid_ok","uuid":"11112222-3333-4444-5555-666677778888",)"
                    R"("issued":true,"new_player":true})"));
            checkEq(w.uuidForTest(), kNew,
                    QStringLiteral("身份：服务端给的新身份被采纳"));
            Settings reread = Settings::load(path);
            checkEq(reread.uuid, kNew,
                    QStringLiteral("身份：新身份**立刻落盘**（否则下次连接又变成新玩家）"));
        }

        // ---- ② 掉线托管：等待室座位行与牌桌分数栏都要标出来 ----
        {
            MainWindow w;
            w.feedEventForTest(parseEv(
                    R"({"ev":"room","id":"AB12","name":"房","host":1,"playing":true,)"
                    R"("rules":{},"seats":[)"
                    R"({"seat":0,"pid":1,"name":"甲","ready":false,"bot":false,"away":false,"score":25000},)"
                    R"({"seat":1,"pid":2,"name":"乙","ready":false,"bot":false,"away":true,"score":24000},)"
                    R"({"seat":2,"pid":3,"name":"丙","ready":true,"bot":false,"away":false,"score":26000},)"
                    R"({"seat":3,"pid":4,"name":"丁","ready":false,"bot":false,"away":false,"score":25000}]})"));
            QLabel* row1 = w.seatLabelForTest(1);
            QLabel* row0 = w.seatLabelForTest(0);
            check(row1 != nullptr && row1->text().contains(QStringLiteral("掉线")),
                  QStringLiteral("托管：座位行标出「掉线」（实际：%1）")
                          .arg(row1 == nullptr ? QStringLiteral("<null>") : row1->text()));
            check(row0 != nullptr && !row0->text().contains(QStringLiteral("掉线")),
                  QStringLiteral("托管：没掉线的那家不标"));
            check(row1 != nullptr && !row1->text().contains(QStringLiteral("准备")),
                  QStringLiteral("托管：托管中的座位不再显示「准备/未准备」（那两个词没有意义）"));
            // 对局进行中的 `room` 事件**不得**把界面切回等待室（掉线会触发一次广播）
            check(w.stackPageForTest() == QLatin1String("table"),
                  QStringLiteral("托管：对局中的 room 事件不该把界面切回等待室"));
        }

        // ---- ③ 投票条：可见性 / 组包 / 冷却 ----
        {
            MainWindow w;
            QVector<QJsonObject> sent;
            w.setCommandTapForTest([&sent](const QJsonObject& o) { sent.append(o); });
            check(w.voteEndButtonForTest() != nullptr && w.voteAgreeButtonForTest() != nullptr
                          && w.voteDisagreeButtonForTest() != nullptr
                          && w.voteLabelForTest() != nullptr,
                  QStringLiteral("投票：投票条三个按钮与状态行都在"));
            check(w.voteEndButtonForTest()->isHidden(),
                  QStringLiteral("投票：还没开局时「结束对局」不显示"));

            w.feedEventForTest(parseEv(
                    R"({"ev":"game_start","replay_id":"","seats":[],)"
                    R"("round":{"bakaze":"E","kyoku":1,"honba":0}})"));
            check(!w.voteEndButtonForTest()->isHidden(),
                  QStringLiteral("投票：牌局中「结束对局」按钮出现"));
            check(w.voteEndButtonForTest()->isEnabled(),
                  QStringLiteral("投票：没有冷却时可以发起"));

            sent.clear();
            w.voteEndButtonForTest()->click();
            check(!sent.isEmpty()
                          && sent.last().value(QStringLiteral("cmd")).toString()
                                  == QLatin1String("vote_end"),
                  QStringLiteral("投票：点「结束对局」发 vote_end"));

            // 服务端广播"有人发起了投票"（by=1 = 别家）
            w.feedEventForTest(parseEv(
                    R"({"ev":"vote_start","by":1,"need":2,"total":3,"deadline_ms":60000})"));
            check(!w.voteAgreeButtonForTest()->isHidden()
                          && !w.voteDisagreeButtonForTest()->isHidden(),
                  QStringLiteral("投票：进行中才出现「同意 / 不同意」"));
            check(w.voteAgreeButtonForTest()->isEnabled(),
                  QStringLiteral("投票：我还没表态，可以点"));
            check(!w.voteEndButtonForTest()->isEnabled(),
                  QStringLiteral("投票：投票进行中不能再发起"));

            sent.clear();
            w.voteAgreeButtonForTest()->click();
            check(!sent.isEmpty()
                          && sent.last().value(QStringLiteral("cmd")).toString()
                                  == QLatin1String("vote")
                          && sent.last().value(QStringLiteral("agree")).toBool(),
                  QStringLiteral("投票：点「同意」发 vote{agree:true}"));
            check(!w.voteAgreeButtonForTest()->isEnabled(),
                  QStringLiteral("投票：表态后立刻锁住（防连点，服务端只认第一次）"));

            w.feedEventForTest(parseEv(
                    R"({"ev":"vote_update","agree":2,"need":2,"total":3,"agreed":[0,1],)"
                    R"("declined":[]})"));
            check(w.voteLabelForTest()->text().contains(QStringLiteral("2/2")),
                  QStringLiteral("投票：状态行显示票数（实际：%1）")
                          .arg(w.voteLabelForTest()->text()));

            w.feedEventForTest(parseEv(
                    R"({"ev":"vote_result","result":"rejected","agree":1,"need":2,"total":3,)"
                    R"("reason":"impossible","cooldown_ms":300000})"));
            check(w.voteAgreeButtonForTest()->isHidden()
                          && w.voteDisagreeButtonForTest()->isHidden(),
                  QStringLiteral("投票：出结论后收起「同意 / 不同意」"));
            check(!w.voteEndButtonForTest()->isEnabled(),
                  QStringLiteral("投票：冷却期内不能发起（由服务端计时，客户端只显示）"));
            check(w.voteLabelForTest()->text().contains(QStringLiteral("冷却")),
                  QStringLiteral("投票：状态行显示冷却（实际：%1）")
                          .arg(w.voteLabelForTest()->text()));

            // 发起被服务端拒（冷却中）：客户端把倒计时补上，别一直显示"可发起"
            w.feedEventForTest(parseEv(R"({"ev":"vote_denied","reason":"cooldown","wait_ms":120000})"));
            check(!w.voteEndButtonForTest()->isEnabled(),
                  QStringLiteral("投票：被拒（冷却）之后按钮仍然不可点"));

            w.feedEventForTest(parseEv(
                    R"({"ev":"game_end","final":[)"
                    R"({"seat":0,"name":"甲","score":25000,"point":0,"uma":0,"rank":1}],)"
                    R"("scores":[25000,25000,25000,25000],"ranking":[0,1,2,3],"reason":"vote"})"));
            check(w.voteEndButtonForTest()->isHidden(),
                  QStringLiteral("投票：整场结束后「结束对局」按钮收起"));
        }
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
