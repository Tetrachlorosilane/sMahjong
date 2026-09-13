#pragma once

// 牌面绘制：**优先用 SVG 矢量素材**（client/assets/tiles/<牌码>.svg → exe 同级 tiles/），
// 取不到时回退到内建的程序化绘制（只用系统字体，不依赖字体资源文件）。
// 参考 docs/DESIGN.md「牌面绘制」。

#include <QRect>
#include <QRectF>
#include <QString>

class QPainter;

class TileRenderer
{
public:
    // ---- 牌字符串 ↔ (kind, red) 双向转换（严格按 PROTOCOL §1）----
    static bool parseTile(const QString& tile, int* kind, bool* red);
    static QString tileString(int kind, bool red = false);
    static bool isRed(const QString& tile);
    static QString label(const QString& tile);

    // ---- 绘制 ----
    // 牌面；red=true 强制赤宝牌配色；sideways=true 时按 90° 横置绘制（r 为横置后的外接矩形）
    static void drawFace(QPainter& p, const QRect& r, const QString& tile, bool red = false,
                         bool sideways = false);
    // 牌背
    static void drawBack(QPainter& p, const QRect& r);
    // 小号牌面（牌河 / 副露 / 宝牌指示牌）
    static void drawSmall(QPainter& p, const QRect& r, const QString& tile);

    // ---- 扩展接口（牌桌内部使用，浮点矩形 + 任意直角旋转）----
    static void drawFaceF(QPainter& p, const QRectF& r, const QString& tile, bool red = false,
                          bool small = false);
    static void drawBackF(QPainter& p, const QRectF& r);
    static void drawFaceRot(QPainter& p, const QRectF& r, const QString& tile, bool red,
                            int quarterTurns);
    static void drawBackRot(QPainter& p, const QRectF& r, int quarterTurns);
};
