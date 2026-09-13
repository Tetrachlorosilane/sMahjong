#pragma once

// 牌桌外的一排**自动应答开关**：自动胡了 / 不吃碰杠 / 自动摸切。
//
// 这里只负责「收集开关状态」并把变化发出去；替玩家做什么动作由
// `autopolicy::decide()` 决定（纯逻辑，可单测）。**每小局结束时**
// MainWindow 会调 `reset()` 把三个开关立刻全部关掉（用户要求）。
//
// 高度固定：它和 ActionBar 一起夹着牌桌，高度若随文案变化会让整桌重新布局。

#include <QStringList>
#include <QWidget>

#include "model/AutoPolicy.h"

class QPushButton;

class AutoBar : public QWidget
{
    Q_OBJECT
public:
    explicit AutoBar(QWidget* parent = nullptr);

    autopolicy::Flags flags() const;
    void reset();                                  // 三个开关全部关掉（每小局结束）

    // 自检用
    QPushButton* buttonForTest(const QString& label) const;
    QStringList labelsForTest() const;

signals:
    // 任一开关被点动（或 reset）后发出，携带**当前**三个开关的状态
    void flagsChanged(const autopolicy::Flags& flags);

private:
    QPushButton* makeToggle(const QString& label, const QString& tip);

    QPushButton* m_winBtn = nullptr;
    QPushButton* m_noCallBtn = nullptr;
    QPushButton* m_tsumogiriBtn = nullptr;
};
