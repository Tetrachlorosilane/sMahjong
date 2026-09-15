#pragma once

#include <QString>

/**
 * 结算界面用的牌面字体（I.MahjongJP.otf）加载器。
 *
 * 该字体只有一个 OpenType 特性 `liga`（**默认生效**），靠连字把牌码串
 * （如 `3ma-3m=3m3m`）直接渲染成牌面图形，语法见 docs 里的探针图。
 *
 * 加载顺序（与牌面 SVG 一致，**目录优先、资源兜底**）：
 *   1) exe 同级 fonts/I.MahjongJP.otf
 *   2) qrc 内嵌 :/fonts/I.MahjongJP.otf
 * 都拿不到时 {@link available()} 返回 false，调用方回退到汉字表示。
 *
 * ⚠ 字体授权为 M+ 字型授权条款：可自由使用/复制/分发/修改，商业与非商业均可。
 */
namespace tilefont {

/** 单例加载；重复调用只加载一次。返回是否可用。 */
bool load();

/** 是否已成功加载。 */
bool available();

/** 可用的字族名；不可用时返回空串。 */
QString family();

/** 牌码串是否**只含**该字体认识的字元（否则不该送进去渲染，避免画出错误的牌）。 */
bool isRenderableSchematic(const QString& s);

} // namespace tilefont
