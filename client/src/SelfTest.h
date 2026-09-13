#pragma once

// --selftest 自检模式：不连网，画联系表 + 模拟牌桌 + 协议/牌面转换单元测试。

#include <QString>

namespace selftest {

// 返回 0 = 全部通过；1 = 有失败。产物写入 outDir。
int run(const QString& outDir);

} // namespace selftest
