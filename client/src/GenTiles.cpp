#include "GenTiles.h"

#include <QChar>
#include <QDir>
#include <QFile>
#include <QFont>
#include <QFontDatabase>
#include <QPainterPath>
#include <QString>
#include <QStringList>
#include <QTextStream>
#include <QTransform>

#include <cmath>

namespace {

// 牌面画布（与既有素材一致，替换时按矩形缩放）
constexpr int W = 300;
constexpr int H = 400;
// 字形摆放区（留出牌边）——字体里每个码位就是**整张牌面**，所以直接按整块缩放
constexpr qreal INSET_X = 10.0;   // 字形自带牌外框，留一点余量即可
constexpr qreal INSET_Y = 10.0;

/**
 * 牌种 → Unicode Mahjong Tiles 码位。
 *
 * 方块布局（与 Unicode 1F000..1F02B 一致）：
 *   1F000..1F003 東南西北   1F004 中   1F005 發   1F006 白
 *   1F007..1F00F 一萬..九萬
 *   1F010..1F018 一索..九索
 *   1F019..1F021 一筒..九筒
 *   1F02B 牌背
 *
 * 本项目 kind 编号：0..8 = 1m..9m，9..17 = 1p..9p，18..26 = 1s..9s，27..33 = 1z..7z
 * （1z..4z = 東南西北，5z = 白，6z = 發，7z = 中）。
 */
uint tileCodepoint(int kind)
{
    if (kind >= 0 && kind <= 8) {
        return 0x1F007u + uint(kind);          // 萬子
    }
    if (kind >= 9 && kind <= 17) {
        return 0x1F019u + uint(kind - 9);      // 筒子
    }
    if (kind >= 18 && kind <= 26) {
        return 0x1F010u + uint(kind - 18);     // 索子
    }
    switch (kind) {
    case 27: return 0x1F000u;                  // 東
    case 28: return 0x1F001u;                  // 南
    case 29: return 0x1F002u;                  // 西
    case 30: return 0x1F003u;                  // 北
    case 31: return 0x1F006u;                  // 白
    case 32: return 0x1F005u;                  // 發
    case 33: return 0x1F004u;                  // 中
    default: return 0u;
    }
}

/** 牌种 → 素材文件名（= 客户端 mj::tileString 的牌码）。 */
QString tileCode(int kind, bool red)
{
    if (red) {
        if (kind == 4) return QStringLiteral("0m");
        if (kind == 13) return QStringLiteral("0p");
        if (kind == 22) return QStringLiteral("0s");
    }
    if (kind <= 8) return QStringLiteral("%1m").arg(kind + 1);
    if (kind <= 17) return QStringLiteral("%1p").arg(kind - 8);
    if (kind <= 26) return QStringLiteral("%1s").arg(kind - 17);
    return QStringLiteral("%1z").arg(kind - 26);
}

/** QPainterPath → SVG path 的 d 属性。 */
QString pathToD(const QPainterPath& path)
{
    QString d;
    for (int i = 0; i < path.elementCount(); ++i) {
        const QPainterPath::Element e = path.elementAt(i);
        switch (e.type) {
        case QPainterPath::MoveToElement:
            d += QStringLiteral("M%1 %2").arg(e.x, 0, 'f', 2).arg(e.y, 0, 'f', 2);
            break;
        case QPainterPath::LineToElement:
            d += QStringLiteral("L%1 %2").arg(e.x, 0, 'f', 2).arg(e.y, 0, 'f', 2);
            break;
        case QPainterPath::CurveToElement: {
            // 三次贝塞尔：CurveToElement 后紧跟两个 CurveToDataElement
            const QPainterPath::Element c1 = path.elementAt(i + 1);
            const QPainterPath::Element c2 = path.elementAt(i + 2);
            d += QStringLiteral("C%1 %2 %3 %4 %5 %6")
                     .arg(e.x, 0, 'f', 2).arg(e.y, 0, 'f', 2)
                     .arg(c1.x, 0, 'f', 2).arg(c1.y, 0, 'f', 2)
                     .arg(c2.x, 0, 'f', 2).arg(c2.y, 0, 'f', 2);
            i += 2;
            break;
        }
        case QPainterPath::CurveToDataElement:
            break;   // 已在 CurveToElement 分支消费
        }
        if (e.type != QPainterPath::CurveToDataElement) {
            d += QLatin1Char(' ');
        }
    }
    return d.trimmed();
}

/** 把字形路径缩放到画布内（保持比例、居中）。 */
QPainterPath fitToCanvas(const QPainterPath& raw)
{
    const QRectF b = raw.boundingRect();
    if (b.isEmpty()) {
        return QPainterPath();
    }
    const qreal bw = W - 2 * INSET_X;
    const qreal bh = H - 2 * INSET_Y;
    const qreal s = qMin(bw / b.width(), bh / b.height());
    QTransform t;
    t.translate(INSET_X + (bw - b.width() * s) / 2.0, INSET_Y + (bh - b.height() * s) / 2.0);
    t.scale(s, s);
    t.translate(-b.left(), -b.top());
    return t.map(raw);
}

QString tileSvg(const QString& d, bool red)
{
    // 只输出字形轮廓。**不要再画自己的牌底矩形**：
    // 该字体每个码位的字形本身就是一整张牌（自带外框），再套一层就成双层框了。
    //
    // 默认**填充**（fill 上色、不描边），牌面才是实心的；
    // 想要描边风格就自己把 fill 改成 "none" 并加 stroke。
    const QString fill = red ? QStringLiteral("#C0392B") : QStringLiteral("#142A54");
    return QStringLiteral(
               "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 %1 %2\" width=\"%1\" height=\"%2\">\n"
               "  <!-- 由 I.MahjongJP.otf 的 Unicode 麻将牌字形（U+1F0xx）轮廓化生成 -->\n"
               "  <!-- 默认已填充；想改描边风格：fill=\"none\" 并自行加 stroke -->\n"
               "  <path d=\"%3\" fill=\"%4\" fill-rule=\"evenodd\" stroke=\"none\"/>\n"
               "</svg>\n")
        .arg(W).arg(H).arg(d, fill);
}

QString backSvg(const QString& d)
{
    // 牌背同理：字体有牌背字形（U+1F02B），本身就是一整张牌，不要再画底色与斜纹。
    return QStringLiteral(
               "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 %1 %2\" width=\"%1\" height=\"%2\">\n"
               "  <!-- 牌背：由字体牌背字形（U+1F02B）轮廓化生成（默认已填充） -->\n"
               "  <path d=\"%3\" fill=\"#1F5C4A\" fill-rule=\"evenodd\" stroke=\"none\"/>\n"
               "</svg>\n")
        .arg(W).arg(H).arg(d);
}

} // namespace

int generateTileSvgs(const QString& outDir, const QString& fontPath)
{
    const int fid = QFontDatabase::addApplicationFont(fontPath);
    if (fid < 0) {
        QTextStream(stderr) << "无法加载字体: " << fontPath << "\n";
        return 1;
    }
    const QStringList families = QFontDatabase::applicationFontFamilies(fid);
    if (families.isEmpty()) {
        QTextStream(stderr) << "字体没有可用的字族\n";
        return 1;
    }
    QFont font(families.first());
    font.setPixelSize(1000);   // 任意大尺寸；后面按包围盒缩放，与字号无关
    QTextStream(stdout) << "字体: " << families.first() << " ← " << fontPath << "\n";

    QDir().mkpath(outDir);
    int written = 0;
    int missing = 0;

    const auto emitOne = [&](const QString& code, uint cp, bool red, bool isBack) {
        if (cp == 0u) {
            QTextStream(stderr) << "  跳过 " << code << "（无对应码位）\n";
            ++missing;
            return;
        }
        QPainterPath raw;
        raw.addText(0, 0, font, QString(QChar::fromUcs4(cp)));
        const QPainterPath fitted = fitToCanvas(raw);
        if (fitted.isEmpty()) {
            QTextStream(stderr) << "  跳过 " << code
                                << QStringLiteral("（字体缺字形 U+%1）").arg(cp, 5, 16, QLatin1Char('0'))
                                << "\n";
            ++missing;
            return;
        }
        const QString d = pathToD(fitted);
        const QString svg = isBack ? backSvg(d) : tileSvg(d, red);
        QFile f(QDir(outDir).filePath(code + QStringLiteral(".svg")));
        if (!f.open(QIODevice::WriteOnly | QIODevice::Text)) {
            QTextStream(stderr) << "  写不了 " << f.fileName() << "\n";
            ++missing;
            return;
        }
        QTextStream(&f) << svg;
        ++written;
    };

    for (int kind = 0; kind < 34; ++kind) {
        emitOne(tileCode(kind, false), tileCodepoint(kind), false, false);
    }
    // 赤五：字形同 5，靠颜色区分（正好交给美术上色）
    for (int kind : {4, 13, 22}) {
        emitOne(tileCode(kind, true), tileCodepoint(kind), true, false);
    }
    // 牌背
    emitOne(QStringLiteral("back"), 0x1F02Bu, false, true);

    QTextStream(stdout) << "完成：写出 " << written << " 个，跳过 " << missing << " 个 → " << outDir << "\n";
    return missing == 0 ? 0 : 2;
}
