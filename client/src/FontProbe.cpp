#include "FontProbe.h"

#include <QColor>
#include <QFile>
#include <QFont>
#include <QFontDatabase>
#include <QFontMetricsF>
#include <QImage>
#include <QPainter>
#include <QStringList>
#include <QTextStream>

namespace {

/** 一条候选：左侧原样打印输入串，右侧用该字体渲染（liga 默认生效 → 出现牌面）。 */
struct Probe {
    QString label;    // 说明
    QString input;    // 喂给字体的字符串
};

QStringList probeInputs()
{
    QStringList list;

    // ---- A. 单张牌码：验证 1m..9m / 1p..9p / 1s..9s / 1z..7z 是否各自成一张牌
    for (const char* c : {"1m", "3m", "5m", "9m", "5p", "9p", "5s", "9s", "1z", "4z", "5z", "7z"}) {
        list << QString::fromLatin1(c);
    }
    // ---- B. 赤牌写法：0x 还是 5xa
    for (const char* c : {"0m", "0p", "0s", "5ma", "5pa", "5sa", "3ma", "4za", "5za"}) {
        list << QString::fromLatin1(c);
    }
    // ---- C. 未知符号单独看
    for (const char* c : {"a", "-", "=", "+", "8z", "9z", "0z", "8za", "9za"}) {
        list << QString::fromLatin1(c);
    }
    // ---- D. 两张的组合：看 -、=、无分隔 的差别
    for (const char* c : {"3m3m", "3m-3m", "3m=3m", "3m+3m", "3m,3m", "3m 3m"}) {
        list << QString::fromLatin1(c);
    }
    // ---- E. 四张（四张牌并排 / 两行）
    list << QStringLiteral("3m3m5p5p");
    list << QStringLiteral("3m-3m=3m3m");
    // ---- F. 你提供的两条完整示例（原样）
    list << QStringLiteral("3ma-3m=3m3m5p-5pa=5p5p4z-4z=4z4za5za-5z=5z5z");
    list << QStringLiteral("3ma-3m=3m3m5p-0p=5p5p4z-4z=4z8z0z-5z=5z5z");
    // ---- G. 示例的切段：逐段加长，定位哪一段开始变样
    list << QStringLiteral("3ma");
    list << QStringLiteral("3ma-3m");
    list << QStringLiteral("3ma-3m=3m3m");
    list << QStringLiteral("3ma-3m=3m3m5p");
    list << QStringLiteral("3ma-3m=3m3m5p-5pa=5p5p");
    return list;
}

/** 第二轮：只盯组合符 `-` / `=`，放大看清每一格。 */
QStringList probeInputs2()
{
    QStringList list;
    // 基线：同一张牌，单独 / 两张并排，用来对比宽度与朝向
    list << QStringLiteral("3m");
    list << QStringLiteral("3m3m");
    // `-` 的位置语义：在左 / 在右 / 两张牌之间 / 连用
    list << QStringLiteral("3m-");
    list << QStringLiteral("-3m");
    list << QStringLiteral("3m-5m");
    list << QStringLiteral("5m-3m");
    list << QStringLiteral("3m-3m-3m");
    list << QStringLiteral("3m--3m");
    // `=` 的位置语义
    list << QStringLiteral("3m=");
    list << QStringLiteral("=3m");
    list << QStringLiteral("3m=5m");
    list << QStringLiteral("5m=3m");
    list << QStringLiteral("3m=3m=3m");
    // 与实例里出现的形式对齐
    list << QStringLiteral("3ma-3m");
    list << QStringLiteral("3m=3m3m");
    list << QStringLiteral("3ma-3m=3m3m");
    // 空格 / 逗号 是否是排版控制符
    list << QStringLiteral("3m 3m 3m");
    list << QStringLiteral("3m,3m,3m");
    // 实例整体
    list << QStringLiteral("3ma-3m=3m3m5p-5pa=5p5p4z-4z=4z4za5za-5z=5z5z");
    return list;
}

/** 第三轮：副露横置标记 `-` 与「空格/位置」的组合 —— 定位"横置方向随机"的根因。 */
QStringList probeInputs3()
{
    QStringList list;
    // 基准
    list << QStringLiteral("3m");
    list << QStringLiteral("3m-");
    // 被鸣的牌在 前 / 中 / 后，且**用空格分隔**（结算界面就是这么拼的）
    list << QStringLiteral("3m- 4m 5m");
    list << QStringLiteral("3m 4m- 5m");
    list << QStringLiteral("3m 4m 5m-");
    // 同样三种位置，但**不用空格**
    list << QStringLiteral("3m-4m5m");
    list << QStringLiteral("3m4m-5m");
    list << QStringLiteral("3m4m5m-");
    // 碰 / 杠（四张）
    list << QStringLiteral("5z 5z- 5z");
    list << QStringLiteral("5z-5z5z");
    list << QStringLiteral("5z 5z 5z 5z-");
    // 两组副露相邻（结算界面里副露之间也只隔一个空格）
    list << QStringLiteral("3m 4m- 5m 6z- 6z 6z");
    list << QStringLiteral("3m 4m- 5m");
    // 被鸣的是最后一张的完整副露
    list << QStringLiteral("1m 2m- 3m");
    return list;
}

} // namespace

int runFontProbe(const QString& outPng, const QString& fontPath, int suite)
{
    const int fid = QFontDatabase::addApplicationFont(fontPath);
    if (fid < 0) {
        QTextStream(stderr) << "无法加载字体: " << fontPath << "\n";
        return 1;
    }
    const QStringList fams = QFontDatabase::applicationFontFamilies(fid);
    if (fams.isEmpty()) {
        QTextStream(stderr) << "字体没有可用字族\n";
        return 1;
    }
    QFont font(fams.first());
    font.setPixelSize(suite == 2 ? 120 : 56);

    const QStringList inputs = (suite == 2) ? probeInputs2() : (suite == 3 ? probeInputs3() : probeInputs());
    const int W = 1600;
    const int rowH = (suite == 2) ? 160 : 86;
    const int headH = 56;
    const int H = headH + rowH * inputs.size() + 24;

    QImage img(W, H, QImage::Format_ARGB32);
    img.fill(QColor(0xFF, 0xFF, 0xFF));
    QPainter p(&img);
    p.setRenderHint(QPainter::Antialiasing, true);
    p.setRenderHint(QPainter::TextAntialiasing, true);

    QFont title(QStringLiteral("Microsoft YaHei"));
    title.setPixelSize(26);
    p.setFont(title);
    p.setPen(QColor(0x22, 0x22, 0x22));
    p.drawText(12, 34, QStringLiteral("I.MahjongJP 语法探针(第%1轮) · 字体=%2 · liga 默认生效").arg(suite).arg(fams.first()));

    QFont label(QStringLiteral("Consolas"));
    label.setPixelSize(22);

    int y = headH;
    for (const QString& in : inputs) {
        // 交替底色，便于对行。
        // 注意三元运算符**不能**直接写在 QColor(...) 的参数表里：
        // `QColor(c ? a1,a2,a3 : b1,b2,b3)` 会被解析成 `((c) ? a1 : b1), a2, a3`，
        // 条件只作用于第一个参数（曾因此让交替色失效，并产生 comma-operator 警告）。
        const bool odd = ((y / rowH) % 2) != 0;
        p.fillRect(0, y, W, rowH, odd ? QColor(0xF4, 0xF6, 0xF8) : QColor(0xFF, 0xFF, 0xFF));
        // 左：输入串本身（等宽字体，原样可读）
        p.setFont(label);
        p.setPen(QColor(0x33, 0x33, 0x33));
        p.drawText(QRectF(12, y, 520, rowH), Qt::AlignVCenter | Qt::AlignLeft,
                   QStringLiteral("\"%1\"").arg(in));
        // 右：用麻将字体渲染同一串
        p.setFont(font);
        p.setPen(Qt::black);
        p.drawText(QRectF(560, y, W - 580, rowH), Qt::AlignVCenter | Qt::AlignLeft, in);

        p.setPen(QColor(0xDD, 0xDD, 0xDD));
        p.drawLine(0, y + rowH - 1, W, y + rowH - 1);
        y += rowH;
    }
    p.end();

    if (!img.save(outPng)) {
        QTextStream(stderr) << "保存失败: " << outPng << "\n";
        return 1;
    }
    QTextStream(stdout) << "探针已输出: " << outPng << QStringLiteral("（%1 行）").arg(inputs.size()) << "\n";
    return 0;
}
