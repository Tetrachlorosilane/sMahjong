#include "ui/AutoBar.h"

#include "i18n/Lang.h"

#include <QHBoxLayout>
#include <QPushButton>
#include <QSignalBlocker>

AutoBar::AutoBar(QWidget* parent)
    : QWidget(parent)
{
    auto* row = new QHBoxLayout(this);
    row->setContentsMargins(8, 0, 8, 4);
    row->setSpacing(6);

    // 文案一律走语言文件（源码里不留中文，见 AGENTS §6）；
    // tooltip 里写清「和牌优先」这条规矩，免得玩家以为自动摸切会把能胡的牌打掉。
    m_winBtn = makeToggle(lang::t("ui.auto.win"), lang::t("ui.auto.win_tip"));
    m_noCallBtn = makeToggle(lang::t("ui.auto.no_call"), lang::t("ui.auto.no_call_tip"));
    m_tsumogiriBtn = makeToggle(lang::t("ui.auto.tsumogiri"), lang::t("ui.auto.tsumogiri_tip"));

    row->addWidget(m_winBtn);
    row->addWidget(m_noCallBtn);
    row->addWidget(m_tsumogiriBtn);
    row->addStretch(1);

    // 固定高度：这一排与 ActionBar 一起夹着牌桌，高度变了整张牌桌会重新布局。
    {
        QPushButton probe(QStringLiteral("测量"), this);   // i18n-keep: 隐藏的测高按钮，玩家看不到
        const int h = qMax(28, probe.sizeHint().height() + 4);
        probe.hide();
        setFixedHeight(h);
    }
}

QPushButton* AutoBar::makeToggle(const QString& label, const QString& tip)
{
    // checkable：开关状态**看得见**（与立直按钮同一条经验 —— 看不见状态的开关
    // 会让人以为点了没生效，见 AGENTS §2.3-9）。
    auto* b = new QPushButton(label, this);
    b->setCheckable(true);
    b->setToolTip(tip);
    connect(b, &QPushButton::toggled, this, [this]() { emit flagsChanged(flags()); });
    return b;
}

autopolicy::Flags AutoBar::flags() const
{
    autopolicy::Flags f;
    f.autoWin = m_winBtn && m_winBtn->isChecked();
    f.noCall = m_noCallBtn && m_noCallBtn->isChecked();
    f.autoTsumogiri = m_tsumogiriBtn && m_tsumogiriBtn->isChecked();
    return f;
}

void AutoBar::reset()
{
    // 逐个 setChecked(false) 会连发三次 flagsChanged（前两次状态还是旧的），
    // 所以先屏蔽信号，最后统一发一次。
    {
        const QSignalBlocker b1(m_winBtn);
        const QSignalBlocker b2(m_noCallBtn);
        const QSignalBlocker b3(m_tsumogiriBtn);
        if (m_winBtn)
            m_winBtn->setChecked(false);
        if (m_noCallBtn)
            m_noCallBtn->setChecked(false);
        if (m_tsumogiriBtn)
            m_tsumogiriBtn->setChecked(false);
    }
    emit flagsChanged(flags());
}

QPushButton* AutoBar::buttonForTest(const QString& label) const
{
    const QPushButton* all[3] = { m_winBtn, m_noCallBtn, m_tsumogiriBtn };
    for (const QPushButton* b : all) {
        if (b && b->text() == label)
            return const_cast<QPushButton*>(b);
    }
    return nullptr;
}

QStringList AutoBar::labelsForTest() const
{
    QStringList out;
    for (const QPushButton* b : { m_winBtn, m_noCallBtn, m_tsumogiriBtn }) {
        if (b)
            out << b->text();
    }
    return out;
}
