#include "ui/TileRenderer.h"

#include "model/Theme.h"
#include "model/Tile.h"

#include <QCoreApplication>
#include <QFile>
#include <QFont>
#include <QFontMetricsF>
#include <QHash>
#include <QImage>
#include <QLineF>
#include <QLinearGradient>
#include <QPainter>
#include <QPainterPath>
#include <QPointF>
#include <QStringList>
#include <QSvgRenderer>
#include <QVector>
#include <QtMath>

namespace {

/**
 * 牌码是否可以用来拼素材文件名。
 *
 * <p>牌码来自报文（`hand` / `tile` / `dora_indicators` …），**不能**直接拼进路径：
 * `"../../x"` 之类会让客户端去读工作区外的文件。所以只认「合法牌码」与 `back`
 * （= 37 种牌 + 赤五写法 `0m/0p/0s` + 牌背）；非法码一律走程序化绘制。
 */
bool isKnownTileCode(const QString& code)
{
    if (code == QLatin1String("back"))
        return true;
    return mj::kindOfTile(code) >= 0;
}

/**
 * 素材缓存（**文件作用域**：`clearAssetCache()` 换材质包时要能一起清掉）。
 * 合法牌码只有 38 种，正常永远打不满；上限是为了让"异常输入撑爆缓存"这条路走不通
 * （超出上限就不再插新项，宁可回退程序化绘制）。
 */
QHash<QString, QSvgRenderer*>& svgCache()
{
    static QHash<QString, QSvgRenderer*> c;
    return c;
}

QHash<QString, QImage>& rasterCache()
{
    static QHash<QString, QImage> c;
    return c;
}

constexpr int kMaxAssetCache = 64;

/**
 * 牌面矢量素材。查找顺序：
 *   1) **材质包**里的 `<牌码>.svg`（用户在设置里指定；见 `model/Theme`）
 *   2) exe 同级 `tiles/<牌码>.svg` —— **美术改完 SVG 重启客户端即可生效，无需重新编译**
 *   3) qrc 内嵌 `:/tiles/<牌码>.svg` —— 兜底（若将来改用资源内嵌）
 *
 * 找不到或解析失败时返回 nullptr，调用方回退到下面的程序化绘制
 * —— 这样删掉素材也不会白屏，只是回到旧画法。
 *
 * 每个文件只解析一次并缓存；美术替换 SVG 后重新构建即可生效（qrc 内嵌）。
 */
QSvgRenderer* svgFor(const QString& code)
{
    if (!isKnownTileCode(code))
        return nullptr;   // 非法码：绝不碰文件系统

    const auto it = svgCache().constFind(code);
    if (it != svgCache().constEnd()) {
        return it.value();
    }
    QSvgRenderer* r = nullptr;
    // 1) 材质包（内容直接来自内存，不进文件系统）
    const QByteArray packed = Theme::instance().packSvg(code);
    if (!packed.isEmpty()) {
        r = new QSvgRenderer(packed);
        if (!r->isValid()) {
            delete r;
            r = nullptr;   // 包里的 SVG 坏了 → 当作"这张没有"，继续往下找默认素材
        }
    }
    // 2/3) 默认素材目录 → qrc
    if (r == nullptr) {
        QStringList candidates;
        candidates << QCoreApplication::applicationDirPath() + QStringLiteral("/tiles/%1.svg").arg(code);
        candidates << QStringLiteral(":/tiles/%1.svg").arg(code);
        for (const QString& path : candidates) {
            if (!QFile::exists(path)) {
                continue;
            }
            r = new QSvgRenderer(path);
            if (r->isValid()) {
                break;
            }
            delete r;
            r = nullptr;
        }
    }
    if (svgCache().size() >= kMaxAssetCache) {
        delete r;   // 不进缓存 → 也不能泄漏
        return nullptr;
    }
    svgCache().insert(code, r);
    return r;
}

/**
 * 牌面位图素材（材质包里的 `png/jpg/…`）。
 *
 * <p>位图与矢量**共存**：同一张牌优先用矢量（缩放不糊），没有再退回位图 ——
 * 绘制时按目标矩形**拉伸铺满**（作者理应给对比例，见 `docs/THEME.md`）。
 * 缓存里存空图表示"查过了、确实没有"（避免每帧都摸一遍磁盘）。
 */
QImage* rasterFor(const QString& code)
{
    if (!isKnownTileCode(code))
        return nullptr;

    auto it = rasterCache().find(code);
    if (it == rasterCache().end()) {
        if (rasterCache().size() >= kMaxAssetCache) {
            return nullptr;
        }
        it = rasterCache().insert(code, Theme::instance().packImage(code));
    }
    return it.value().isNull() ? nullptr : &it.value();
}


/** 赤五：调用方可能给的是 "5m" 而非 "0m"，统一成素材用的牌码。 */
QString tileCodeOf(const QString& tile, bool red)
{
    if (red && tile.size() == 2 && tile.at(0) == QLatin1Char('5')) {
        return QStringLiteral("0") + tile.mid(1);
    }
    return tile;
}

constexpr qreal kPi = 3.14159265358979323846;

// 配色
// ---- 扁平化：牌面/牌背一律**单层纯色 + 一圈描边** ----
// 不画渐变，也**不画厚度层**（早期版本在右下偏移画一层牌厚度做出立体感，已按要求去掉）。
const QColor kIvoryTop(0xFA, 0xF6, 0xE7);    // 牌面（纯色）
const QColor kOutline(0x8C, 0x86, 0x72);     // 描边（低对比，扁平）
const QColor kInk(0x14, 0x2A, 0x54);         // 深蓝黑（万子数字 / 筒子）
const QColor kRed(0xC0, 0x39, 0x2B);         // 红（萬、中、赤宝）
const QColor kGreen(0x1E, 0x84, 0x49);       // 绿（索子、發）
const QColor kBlue(0x1F, 0x4E, 0x9C);        // 深蓝（白板边框）
const QColor kBackTop(0x1F, 0x5C, 0x4A);     // 牌背（纯色）
const QColor kBackEdge(0x16, 0x42, 0x36);    // 牌背描边

// 中文字体（系统字体，逐级回退）
// 候选字族表每次构造都要拼一长串 QStringList，而程序化回退路径是**逐字**调用的
// （一张牌要画好几次）→ 缓存一份基字体，调用处只改字号。
const QFont& baseCjkFont()
{
    static const QFont f = [] {
        QFont x;
        x.setFamilies(QStringList { QStringLiteral("Microsoft YaHei"),
                                    QStringLiteral("SimHei"),
                                    QStringLiteral("Microsoft JhengHei"),
                                    QStringLiteral("Noto Sans CJK SC"),
                                    QStringLiteral("Source Han Sans SC"),
                                    QStringLiteral("SimSun"),
                                    QStringLiteral("DejaVu Sans") });
        x.setBold(true);
        x.setStyleStrategy(QFont::PreferAntialias);
        return x;
    }();
    return f;
}

// 在矩形内居中绘制文字，自动缩放字号以适配
void drawFittedText(QPainter& p, const QRectF& box, const QString& text, const QColor& color,
                    qreal pixelSize)
{
    if (text.isEmpty() || box.width() <= 1.0 || box.height() <= 1.0)
        return;
    QFont f = baseCjkFont();
    f.setPixelSize(qMax(1, int(pixelSize)));
    const QFontMetricsF fm(f);
    const QRectF br = fm.boundingRect(text);
    if (br.width() > 0.5 && br.height() > 0.5) {
        const qreal s = qMin(box.width() / br.width(), box.height() / br.height());
        if (s < 1.0)
            f.setPixelSize(qMax(1, int(pixelSize * s * 0.94)));
    }
    p.setFont(f);
    p.setPen(color);
    p.drawText(box, Qt::AlignCenter, text);
}

// 内容区（牌面有效绘制区域）
QRectF contentRect(const QRectF& face)
{
    return face.adjusted(face.width() * 0.13, face.height() * 0.07, -face.width() * 0.13,
                         -face.height() * 0.07);
}

// 把内容区收敛成不太扁的绘制区（保证圆点不会变成椭圆）
QRectF squareish(const QRectF& in, qreal maxAspect = 1.30)
{
    QRectF r = in;
    if (r.height() > r.width() * maxAspect) {
        const qreal h = r.width() * maxAspect;
        r.moveTop(r.center().y() - h / 2.0);
        r.setHeight(h);
    }
    return r;
}

// 定位点位表：归一化坐标（0..1）
using Points = QVector<QPointF>;

struct Layout {
    Points pts;
    qreal radiusFactor = 0.14; // 相对绘制区短边
};

// 筒子标准点位
Layout pinLayout(int num)
{
    const qreal a = 0.18, b = 0.50, c = 0.82;
    Layout L;
    switch (num) {
    case 1:
        L.pts << QPointF(b, b);
        L.radiusFactor = 0.30;
        break;
    case 2:
        L.pts << QPointF(b, a) << QPointF(b, c);
        L.radiusFactor = 0.19;
        break;
    case 3:
        L.pts << QPointF(a, a) << QPointF(b, b) << QPointF(c, c);
        L.radiusFactor = 0.17;
        break;
    case 4:
        L.pts << QPointF(a, a) << QPointF(c, a) << QPointF(a, c) << QPointF(c, c);
        L.radiusFactor = 0.17;
        break;
    case 5:
        L.pts << QPointF(a, a) << QPointF(c, a) << QPointF(b, b) << QPointF(a, c) << QPointF(c, c);
        L.radiusFactor = 0.155;
        break;
    case 6:
        for (int y = 0; y < 3; ++y)
            L.pts << QPointF(a, 0.15 + 0.35 * y) << QPointF(c, 0.15 + 0.35 * y);
        L.radiusFactor = 0.145;
        break;
    case 7:
        L.pts << QPointF(a, 0.10) << QPointF(b, 0.10) << QPointF(c, 0.10)
              << QPointF(a, 0.52) << QPointF(c, 0.52)
              << QPointF(a, 0.90) << QPointF(c, 0.90);
        L.radiusFactor = 0.13;
        break;
    case 8:
        for (int y = 0; y < 4; ++y)
            L.pts << QPointF(0.30, 0.12 + 0.253 * y) << QPointF(0.70, 0.12 + 0.253 * y);
        L.radiusFactor = 0.115;
        break;
    case 9:
        for (int y = 0; y < 3; ++y) {
            for (int x = 0; x < 3; ++x) {
                const qreal px = (x == 0) ? a : (x == 1 ? b : c);
                const qreal py = (y == 0) ? a : (y == 1 ? b : c);
                L.pts << QPointF(px, py);
            }
        }
        L.radiusFactor = 0.135;
        break;
    default:
        break;
    }
    return L;
}

// 索子标准点位（1 索单独画鸟）
Layout souLayout(int num)
{
    Layout L;
    switch (num) {
    case 2:
        L.pts << QPointF(0.50, 0.16) << QPointF(0.50, 0.84);
        break;
    case 3:
        L.pts << QPointF(0.50, 0.14) << QPointF(0.24, 0.80) << QPointF(0.76, 0.80);
        break;
    case 4:
        L.pts << QPointF(0.26, 0.22) << QPointF(0.74, 0.22) << QPointF(0.26, 0.78)
              << QPointF(0.74, 0.78);
        break;
    case 5:
        L.pts << QPointF(0.22, 0.18) << QPointF(0.78, 0.18) << QPointF(0.50, 0.50)
              << QPointF(0.22, 0.82) << QPointF(0.78, 0.82);
        break;
    case 6:
        for (int y = 0; y < 2; ++y)
            L.pts << QPointF(0.20, 0.28 + 0.44 * y) << QPointF(0.50, 0.28 + 0.44 * y)
                  << QPointF(0.80, 0.28 + 0.44 * y);
        break;
    case 7:
        L.pts << QPointF(0.50, 0.10);
        for (int y = 0; y < 2; ++y)
            L.pts << QPointF(0.18, 0.42 + 0.36 * y) << QPointF(0.50, 0.42 + 0.36 * y)
                  << QPointF(0.82, 0.42 + 0.36 * y);
        break;
    case 8:
        for (int y = 0; y < 4; ++y)
            L.pts << QPointF(0.30, 0.12 + 0.253 * y) << QPointF(0.70, 0.12 + 0.253 * y);
        break;
    case 9:
        for (int y = 0; y < 3; ++y)
            for (int x = 0; x < 3; ++x)
                L.pts << QPointF(0.18 + 0.32 * x, 0.18 + 0.32 * y);
        break;
    default:
        break;
    }
    return L;
}

// 点位 → 实际坐标；同时给出最小间距（用于限制尺寸）
Points resolve(const Points& norm, const QRectF& area, qreal* minSpacing)
{
    Points out;
    out.reserve(norm.size());
    for (const QPointF& n : norm)
        out.append(QPointF(area.left() + n.x() * area.width(), area.top() + n.y() * area.height()));

    qreal spacing = 1.0e9;
    for (int i = 0; i < out.size(); ++i) {
        for (int j = i + 1; j < out.size(); ++j) {
            const qreal d = QLineF(out.at(i), out.at(j)).length();
            if (d < spacing)
                spacing = d;
        }
    }
    if (minSpacing)
        *minSpacing = (spacing > 1.0e8) ? -1.0 : spacing;
    return out;
}

// ---- 万子 ----
void drawMan(QPainter& p, const QRectF& face, int num, bool aka)
{
    const QRectF in = contentRect(face);
    const QRectF up(in.left(), in.top(), in.width(), in.height() * 0.53);
    const QRectF dn(in.left(), in.top() + in.height() * 0.50, in.width(), in.height() * 0.50);
    static const char* const kNum[9] = { "一", "二", "三", "四", "五", "六", "七", "八", "九" };
    drawFittedText(p, up, QString::fromUtf8(kNum[num - 1]), aka ? kRed : kInk, in.height() * 0.60);
    drawFittedText(p, dn, QString(QChar(0x4E07)), kRed, in.height() * 0.52);
}

// ---- 筒子 ----
void drawPinCircle(QPainter& p, const QPointF& c, qreal r, const QColor& ring, bool big)
{
    p.setPen(Qt::NoPen);
    p.setBrush(ring);
    p.drawEllipse(c, r, r);
    p.setBrush(kIvoryTop);
    if (big) {
        // 花瓣状内圈（1 筒）
        const int petals = 8;
        const qreal pr = r * 0.155;
        const qreal rr = r * 0.46;
        p.setBrush(ring);
        for (int i = 0; i < petals; ++i) {
            const qreal ang = 2.0 * kPi * i / petals;
            p.drawEllipse(QPointF(c.x() + rr * qCos(ang), c.y() + rr * qSin(ang)), pr, pr);
        }
        p.setBrush(kIvoryTop);
        p.drawEllipse(c, r * 0.26, r * 0.26);
    } else {
        p.drawEllipse(c, r * 0.66, r * 0.66);
        p.setBrush(ring);
        p.drawEllipse(c, r * 0.30, r * 0.30);
    }
}

void drawPin(QPainter& p, const QRectF& face, int num, bool aka)
{
    const Layout L = pinLayout(num);
    const QRectF area = squareish(contentRect(face));
    qreal spacing = -1.0;
    const Points pts = resolve(L.pts, area, &spacing);
    qreal r = L.radiusFactor * qMin(area.width(), area.height());
    if (spacing > 0.0)
        r = qMin(r, spacing * 0.46);
    r = qMax(r, 0.6);
    const QColor ring = aka ? kRed : kBlue;
    for (const QPointF& c : pts)
        drawPinCircle(p, c, r, ring, num == 1);
}

// ---- 索子 ----
void drawBamboo(QPainter& p, const QPointF& c, qreal w, qreal h, const QColor& col)
{
    const QRectF bar(c.x() - w / 2.0, c.y() - h / 2.0, w, h);
    // 扁平：竹节用纯色 + 端部节线，不做渐变
    p.setPen(QPen(col.darker(170), qMax(0.7, w * 0.10)));
    p.setBrush(col);
    p.drawRoundedRect(bar, w * 0.45, w * 0.45);
    // 上下端节
    p.setPen(QPen(col.darker(185), qMax(0.7, h * 0.05)));
    p.drawLine(QPointF(bar.left(), bar.top() + h * 0.10), QPointF(bar.right(), bar.top() + h * 0.10));
    p.drawLine(QPointF(bar.left(), bar.bottom() - h * 0.10),
               QPointF(bar.right(), bar.bottom() - h * 0.10));
    // 中间横节
    p.setPen(QPen(col.darker(190), qMax(0.8, h * 0.07)));
    p.drawLine(QPointF(bar.left(), c.y()), QPointF(bar.right(), c.y()));
}

// 1 索：简化的绿色鸟
void drawBird(QPainter& p, const QRectF& face, const QColor& col)
{
    QRectF a = squareish(contentRect(face), 1.25);
    p.save();
    p.translate(a.left(), a.top());
    p.scale(a.width(), a.height());
    // 归一化坐标绘制
    QPainterPath tail;
    tail.moveTo(0.52, 0.62);
    tail.lineTo(0.16, 0.96);
    tail.lineTo(0.40, 0.70);
    tail.lineTo(0.22, 0.78);
    tail.lineTo(0.46, 0.60);
    tail.closeSubpath();
    p.setPen(Qt::NoPen);
    p.setBrush(col.darker(115));
    p.drawPath(tail);

    QPainterPath body;
    body.addEllipse(QRectF(0.34, 0.40, 0.44, 0.44));
    p.setBrush(col);
    p.setPen(QPen(col.darker(160), 0.02));
    p.drawPath(body);

    QPainterPath head;
    head.addEllipse(QRectF(0.44, 0.16, 0.28, 0.26));
    p.setBrush(col.lighter(108));
    p.drawPath(head);

    // 喙
    QPainterPath beak;
    beak.moveTo(0.44, 0.26);
    beak.lineTo(0.24, 0.32);
    beak.lineTo(0.44, 0.36);
    beak.closeSubpath();
    p.setBrush(QColor(0xE6, 0x9B, 0x1E));
    p.drawPath(beak);

    // 眼
    p.setBrush(kIvoryTop);
    p.setPen(Qt::NoPen);
    p.drawEllipse(QPointF(0.56, 0.27), 0.045, 0.045);
    p.setBrush(QColor(0x10, 0x2A, 0x1E));
    p.drawEllipse(QPointF(0.565, 0.27), 0.022, 0.022);

    // 翅膀纹理
    p.setBrush(Qt::NoBrush);
    p.setPen(QPen(col.darker(140), 0.025));
    p.drawArc(QRectF(0.42, 0.48, 0.26, 0.24), 30 * 16, 150 * 16);
    p.restore();
}

void drawSou(QPainter& p, const QRectF& face, int num, bool aka)
{
    if (num == 1) {
        drawBird(p, face, kGreen);
        return;
    }
    const Layout L = souLayout(num);
    const QRectF area = squareish(contentRect(face), 1.25);
    qreal spacing = -1.0;
    const Points pts = resolve(L.pts, area, &spacing);
    qreal h = (spacing > 0.0 ? spacing * 0.90 : area.height() * 0.22);
    h = qMin(h, area.height() * 0.30);
    qreal w = h * 0.44;
    w = qMin(w, area.width() * 0.30);

    // 赤 5 索：仅中间一根为红（与实际牌面一致）
    for (int i = 0; i < pts.size(); ++i) {
        QColor col = kGreen;
        if (aka)
            col = (num == 5) ? (i == 2 ? kRed : kGreen) : kRed;
        drawBamboo(p, pts.at(i), w, h, col);
    }
}

// ---- 字牌 ----
void drawHonor(QPainter& p, const QRectF& face, int num, bool aka)
{
    Q_UNUSED(aka);
    static const char* const kHonor[7] = { "東", "南", "西", "北", "白", "發", "中" };
    const QRectF in = contentRect(face);

    if (num == 5) {
        // 白板：深蓝双线边框的空白面
        const QRectF outer = in.adjusted(in.width() * 0.06, in.height() * 0.06, -in.width() * 0.06,
                                         -in.height() * 0.06);
        const qreal lw = qMax(1.0, qMin(in.width(), in.height()) * 0.055);
        p.setBrush(Qt::NoBrush);
        p.setPen(QPen(kBlue, lw));
        p.drawRoundedRect(outer, lw * 1.2, lw * 1.2);
        p.setPen(QPen(kBlue, lw * 0.62));
        p.drawRoundedRect(outer.adjusted(lw * 2.0, lw * 2.0, -lw * 2.0, -lw * 2.0), lw, lw);
        return;
    }

    QColor col = kInk;
    if (num == 6)
        col = kGreen;
    else if (num == 7)
        col = kRed;
    drawFittedText(p, in, QString::fromUtf8(kHonor[num - 1]), col, in.height() * 0.86);
}

// ---- 牌面主体 ----
void drawFaceBody(QPainter& p, const QRectF& r, const QString& tile, bool forceRed, bool small)
{
    if (r.width() < 2.0 || r.height() < 2.0)
        return;
    bool parsedRed = false;
    const int kind = mj::kindOfTile(tile, &parsedRed);
    const bool aka = forceRed || parsedRed;
    // 去掉厚度层后 small 不再影响底框（保留参数以兼容调用点）
    Q_UNUSED(small);

    const qreal radius = qMax(1.0, qMin(r.width(), r.height()) * 0.13);

    // 牌面：**单层扁平** —— 一圈描边 + 纯色填充，不画厚度层、不做偏移。
    // （早期版本在右下偏移画一层"牌厚度"当立体感；已按要求去掉，牌面只有一层外框。）
    p.setBrush(kIvoryTop);
    p.setPen(QPen(kOutline, qMax(0.8, qMin(r.width(), r.height()) * 0.018)));
    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius);

    // 内容
    if (kind < 0) {
        drawFittedText(p, contentRect(r), QStringLiteral("?"), kOutline, r.height() * 0.5);
        return;
    }
    p.save();
    p.setRenderHint(QPainter::Antialiasing, true);
    if (kind < 9)
        drawMan(p, r, kind + 1, aka);
    else if (kind < 18)
        drawPin(p, r, kind - 9 + 1, aka);
    else if (kind < 27)
        drawSou(p, r, kind - 18 + 1, aka);
    else
        drawHonor(p, r, kind - 27 + 1, aka);
    p.restore();
}

// ---- 牌背主体 ----
void drawBackBody(QPainter& p, const QRectF& r)
{
    if (r.width() < 2.0 || r.height() < 2.0)
        return;
    const qreal radius = qMax(1.0, qMin(r.width(), r.height()) * 0.13);

    // 牌背同样单层扁平：不画厚度层，只有一圈描边
    p.setBrush(kBackTop);
    p.setPen(QPen(kBackEdge, qMax(0.8, qMin(r.width(), r.height()) * 0.02)));
    p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius);

    // 斜线纹理
    p.save();
    QPainterPath clip;
    clip.addRoundedRect(r.adjusted(1, 1, -1, -1), radius * 0.9, radius * 0.9);
    p.setClipPath(clip);
    p.setPen(QPen(QColor(255, 255, 255, 26), qMax(1.0, qMin(r.width(), r.height()) * 0.030)));
    const qreal step = qMax(3.0, qMin(r.width(), r.height()) * 0.20);
    for (qreal x = r.left() - r.height(); x < r.right(); x += step)
        p.drawLine(QPointF(x, r.bottom()), QPointF(x + r.height(), r.top()));
    p.restore();

    // 内边框
    p.setBrush(Qt::NoBrush);
    p.setPen(QPen(QColor(255, 255, 255, 52), qMax(0.8, qMin(r.width(), r.height()) * 0.02)));
    p.drawRoundedRect(r.adjusted(r.width() * 0.11, r.height() * 0.08, -r.width() * 0.11,
                                 -r.height() * 0.08),
                      radius * 0.7, radius * 0.7);
}

} // namespace

// ============================ 对外接口 ============================

bool TileRenderer::parseTile(const QString& tile, int* kind, bool* red)
{
    bool r = false;
    const int k = mj::kindOfTile(tile, &r);
    if (kind)
        *kind = k;
    if (red)
        *red = r;
    return k >= 0;
}

QString TileRenderer::tileString(int kind, bool red)
{
    return mj::tileString(kind, red);
}

bool TileRenderer::isRed(const QString& tile)
{
    return mj::isRedTile(tile);
}

QString TileRenderer::label(const QString& tile)
{
    return mj::tileLabel(tile);
}

void TileRenderer::drawFaceF(QPainter& p, const QRectF& r, const QString& tile, bool red, bool small)
{
    // 优先用矢量素材；没有就用**材质包里的位图**（拉伸铺满目标矩形）；再没有才程序化绘制
    const QString code = tileCodeOf(tile, red);
    if (QSvgRenderer* svg = svgFor(code)) {
        p.save();
        p.setRenderHint(QPainter::Antialiasing, true);
        p.setRenderHint(QPainter::SmoothPixmapTransform, true);
        svg->render(&p, r);
        p.restore();
        return;
    }
    if (const QImage* img = rasterFor(code)) {
        p.save();
        p.setRenderHint(QPainter::SmoothPixmapTransform, true);
        p.drawImage(r, *img);
        p.restore();
        return;
    }
    p.save();
    p.setRenderHint(QPainter::Antialiasing, true);
    p.setRenderHint(QPainter::TextAntialiasing, true);
    drawFaceBody(p, r, tile, red, small);
    p.restore();
}

void TileRenderer::drawBackF(QPainter& p, const QRectF& r)
{
    if (QSvgRenderer* svg = svgFor(QStringLiteral("back"))) {
        p.save();
        p.setRenderHint(QPainter::Antialiasing, true);
        p.setRenderHint(QPainter::SmoothPixmapTransform, true);
        svg->render(&p, r);
        p.restore();
        return;
    }
    if (const QImage* img = rasterFor(QStringLiteral("back"))) {
        p.save();
        p.setRenderHint(QPainter::SmoothPixmapTransform, true);
        p.drawImage(r, *img);
        p.restore();
        return;
    }
    p.save();
    p.setRenderHint(QPainter::Antialiasing, true);
    drawBackBody(p, r);
    p.restore();
}

void TileRenderer::drawFaceRot(QPainter& p, const QRectF& r, const QString& tile, bool red,
                               int quarterTurns)
{
    const int q = ((quarterTurns % 4) + 4) % 4;
    if (q == 0) {
        drawFaceF(p, r, tile, red, false);
        return;
    }
    p.save();
    p.translate(r.center());
    p.rotate(90.0 * q);
    QRectF inner;
    if (q % 2 == 1)
        inner = QRectF(-r.height() / 2.0, -r.width() / 2.0, r.height(), r.width());
    else
        inner = QRectF(-r.width() / 2.0, -r.height() / 2.0, r.width(), r.height());
    drawFaceF(p, inner, tile, red, false);
    p.restore();
}

void TileRenderer::drawBackRot(QPainter& p, const QRectF& r, int quarterTurns)
{
    const int q = ((quarterTurns % 4) + 4) % 4;
    if (q == 0) {
        drawBackF(p, r);
        return;
    }
    p.save();
    p.translate(r.center());
    p.rotate(90.0 * q);
    QRectF inner;
    if (q % 2 == 1)
        inner = QRectF(-r.height() / 2.0, -r.width() / 2.0, r.height(), r.width());
    else
        inner = QRectF(-r.width() / 2.0, -r.height() / 2.0, r.width(), r.height());
    drawBackF(p, inner);
    p.restore();
}

void TileRenderer::drawFace(QPainter& p, const QRect& r, const QString& tile, bool red, bool sideways)
{
    if (!sideways) {
        drawFaceF(p, QRectF(r), tile, red, false);
        return;
    }
    drawFaceRot(p, QRectF(r), tile, red, 1);
}

void TileRenderer::drawBack(QPainter& p, const QRect& r)
{
    drawBackF(p, QRectF(r));
}

void TileRenderer::drawSmall(QPainter& p, const QRect& r, const QString& tile)
{
    drawFaceF(p, QRectF(r), tile, false, true);
}

QString TileRenderer::assetSourceForTest(const QString& code)
{
    if (!isKnownTileCode(code)) {
        return QStringLiteral("procedural");
    }
    const Theme::Source ts = Theme::instance().sourceOf(code);
    if (ts != Theme::Source::None) {
        return Theme::sourceName(ts);
    }
    if (svgFor(code) != nullptr) {
        const QString file =
                QCoreApplication::applicationDirPath() + QStringLiteral("/tiles/%1.svg").arg(code);
        return QFile::exists(file) ? QStringLiteral("file-svg") : QStringLiteral("qrc");
    }
    return QStringLiteral("procedural");
}

void TileRenderer::clearAssetCache()
{
    // 换材质包后必须清：否则还是上一次那批图（缓存里存的是解析好的素材）
    svgCache().clear();
    rasterCache().clear();
    Theme::instance().clearCaches();
}
