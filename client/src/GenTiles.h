#pragma once

#include <QString>

/**
 * 用本机字体把 Unicode 麻将牌字形（U+1F000..U+1F02B）**轮廓化**成 SVG 素材。
 *
 * 输出 38 个文件到 `outDir`：37 种牌（含赤五）+ `back.svg`，
 * 每个 SVG 里是一条 `<path>`（默认 `fill="none"` + 描边）——**上色由美术自行处理**：
 * 想实心化就把 `fill="none"` 改成颜色、去掉 `stroke`。
 *
 * 码位映射见 GenTiles.cpp 顶部注释；赤五与普通五共用字形，靠颜色区分。
 *
 * @param outDir  输出目录（通常是 `client/assets/tiles`）
 * @param fontPath 字体文件路径（如 `…/I.MahjongJP.otf`）
 * @return 0 = 全部成功；1 = 字体加载失败；2 = 有字形缺失
 */
int generateTileSvgs(const QString& outDir, const QString& fontPath);
