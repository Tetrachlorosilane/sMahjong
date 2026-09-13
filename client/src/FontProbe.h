#pragma once

#include <QString>

/**
 * 字体语法探针：把一批候选输入串用指定字体渲染成一张对照图，
 * 用来**实测**该字体的连字（liga）输入语法，而不是靠猜。
 *
 * 背景：`I.MahjongJP.otf` 只有 `liga` 一个 OpenType 特性（默认生效），
 * 靠连字把 `3m`、`3ma`、`-`、`=` 这类代码组合成牌面图形。语法未公开，
 * 故用探针把「输入串 → 实际牌面」并排印出来对照。
 *
 * @param outPng  输出图片路径
 * @param fontPath 字体文件路径
 * @param suite   1 = 基础语法总览；2 = 只测组合符 `-` / `=`，放大渲染
 * @return 0 成功
 */
int runFontProbe(const QString& outPng, const QString& fontPath, int suite = 1);
