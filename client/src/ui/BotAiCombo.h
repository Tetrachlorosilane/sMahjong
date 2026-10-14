#pragma once

// 「机器人 AI」下拉框的三个必要动作（大厅建桌 + 等待室两处共用，避免两份实现漂移）：
//   ① 宽度钉在 **30 字符**上 —— `QComboBox` 默认策略是 `AdjustToContentsOnFirstShow`，
//      它按**第一项**（「跟服务端默认」，很短）定宽 ⇒ AI 包名一长就被省略号截断
//      （玩家报障："AI 选择栏过短、显示不全 AI 包名"；只做"按最长名自适应"用户实测**仍嫌短**）。
//   ② 策略设 `AdjustToContents` —— 清单里出现比 30 字符更长的名字时**还能继续长**。
//   ③ 悬停显示**完整**包名 —— 兜底，即使控件被别处压窄也能看到全名。

#include <QComboBox>
#include <QFontMetrics>
#include <QString>

/** 「机器人 AI」下拉框的固定宽度口径：**30 个字符**。
 *  ⚠ 口径 = `QFontMetrics::horizontalAdvance(QString(30, 'W'))` —— 用**最宽的常见 ASCII 字
 *  `W`** 量"30 字符"，得到的是该字体下"30 字符宽"的**上界**；换 `averageCharWidth()*30`
 *  会随字体的平均字宽偏小（本机字体里平均字宽 ≈ 0.53×`W`，30 个平均字只有约 16 个 `W` 宽），
 *  长包名照样显示不全。⛔ 不写死像素魔数：换字号 / 高 DPI 时自动跟着变。 */
inline int botAiComboWidth30(const QFontMetrics& fm)
{
    return fm.horizontalAdvance(QString(30, QLatin1Char('W')));
}

/**
 * 下拉框为「箭头 + 左右内边距」必须预留的像素数（在 30 字符宽之外再兜一层最长项判据）。
 * = **4 个最宽 ASCII 字符**的宽度，而不是写死像素：默认样式下右侧箭头约合 2.4 字宽、
 * 左右 margin 约合 1.5 字宽；用字宽才能在换字号 / 高 DPI 时跟着缩放。
 */
inline int botAiComboPad(const QFontMetrics& fm)
{
    return 4 * fm.horizontalAdvance(QLatin1Char('M'));
}

/** 当前清单里最长的包名需要的像素宽（文本 + 内边距）。自检按**同一个函数**算判据。 */
inline int botAiComboLongestNeed(QComboBox* box)
{
    if (!box)
        return 0;
    const QFontMetrics fm(box->font());
    int widest = 0;
    for (int i = 0; i < box->count(); ++i)
        widest = qMax(widest, fm.horizontalAdvance(box->itemText(i)));
    return widest + botAiComboPad(fm);
}

/**
 * 把下拉框钉到「30 字符宽」（清单里有更长名字时取更长的那一个），完整名字放进 tooltip。
 *
 * @param box  目标下拉框（空指针直接返回，调用处不必判空）。
 * @param hint 可选的解释性提示（如「只有房主能改」），拼在完整包名**下面**一行。
 */
inline void fitBotAiCombo(QComboBox* box, const QString& hint = QString())
{
    if (!box)
        return;
    box->setSizeAdjustPolicy(QComboBox::AdjustToContents);   // 比 30 字符更长时仍可增长
    box->setMinimumWidth(qMax(botAiComboWidth30(QFontMetrics(box->font())), botAiComboLongestNeed(box)));
    const QString full = box->currentText();
    box->setToolTip(hint.isEmpty() ? full : full + QLatin1Char('\n') + hint);
}
