#include "ui/ActionBar.h"

#include "i18n/Lang.h"
#include "model/Tile.h"
#include "net/Protocol.h"

#include <QAction>
#include <QCursor>
#include <QDateTime>
#include <QFont>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QLabel>
#include <QLayoutItem>
#include <QMenu>
#include <QProgressBar>
#include <QPushButton>

#include <cmath>

namespace {

// 牌面中文名（按钮文案用）
QString tileText(const QString& tile)
{
    return mj::tileLabel(tile);
}

QString joinTiles(const QStringList& tiles)
{
    QStringList names;
    for (const QString& t : tiles)
        names << tileText(t);
    return names.join(QStringLiteral(" "));
}

/**
 * 这一组牌码里有没有**赤五**，有就返回那个码（`0m`/`0p`/`0s`）。
 *
 * <p>用来区分「副露赤宝选择」下发的多条同名选项：服务端会给
 * `tiles:["5p","5p"]`（不用赤）与 `tiles:["0p","5p"]`（用赤）各一条，
 * 按钮文案必须让玩家看出哪条会用掉赤五（报障：副露无法区分红五与普通五）。
 */
QString akaCodeIn(const QStringList& tiles)
{
    for (const QString& t : tiles) {
        if (mj::isRedTile(t))
            return t;
    }
    return QString();
}

/**
 * 按动作种类给按钮上色的样式表（用户要求：「附录提示不明显，增大按钮或添加不同按钮颜色
 * 或按钮颜色闪烁」）。
 *
 * 三种语义档次，颜色与「有多该点」一致：
 *   · `win`  —— 和牌（自摸 / 荣和）：金色，最醒目；
 *   · `call` —— 鸣牌（吃 / 碰 / 杠 / 立直 / 九种九牌）：蓝色，次之；
 *   · `pass` —— 跳过：灰底细边，刻意不抢眼（它是"什么都不做"，不该比动作更亮）。
 *
 * `armed` 为真时给一圈更粗的亮边 —— 由 `m_alertTimer` 交替切换，
 * 效果就是"提示按钮在闪"。只改样式表、不重排布局，所以不会让牌桌重新布局。
 */
QString actionButtonStyle(const QString& type, bool armed)
{
    const bool isPass = (type == QLatin1String("pass"));
    const bool isWin = (type == QLatin1String("tsumo") || type == QLatin1String("ron"));

    QString bg, fg, border;
    if (isPass) {
        bg = QStringLiteral("#4A5A55");
        fg = QStringLiteral("#D8E4DF");
        border = QStringLiteral("#6B7C76");
    } else if (isWin) {
        bg = QStringLiteral("#E8B33A");
        fg = QStringLiteral("#2A1F00");
        border = armed ? QStringLiteral("#FFF3C4") : QStringLiteral("#A87A16");
    } else {
        bg = QStringLiteral("#2E6E8E");
        fg = QStringLiteral("#EAF4FA");
        border = armed ? QStringLiteral("#CFEBFA") : QStringLiteral("#1C4A62");
    }
    const int width = armed ? 3 : 1;
    return QStringLiteral("QPushButton {"
                          " background-color: %1; color: %2;"
                          " border: %3px solid %4; border-radius: 6px;"
                          " padding: 8px 18px; font-size: 16px; font-weight: bold; }"
                          "QPushButton:hover { border-color: #FFFFFF; }"
                          "QPushButton:disabled { background-color: #3A4642; color: #90A09A; }")
        .arg(bg, fg)
        .arg(width)
        .arg(border);
}

} // namespace

ActionBar::ActionBar(QWidget* parent)
    : QWidget(parent)
{
    m_layout = new QHBoxLayout(this);
    m_layout->setContentsMargins(8, 4, 8, 4);
    m_layout->setSpacing(8);

    m_title = new QLabel(this);
    m_title->setMinimumWidth(150);
    QFont tf = m_title->font();
    tf.setBold(true);
    m_title->setFont(tf);
    m_layout->addWidget(m_title);
    m_layout->addStretch(1);

    m_tick.setInterval(250);
    connect(&m_tick, &QTimer::timeout, this, &ActionBar::onTick);

    // 提示闪烁：交替切换按钮边框粗细/亮度（见 actionButtonStyle）。
    // 只在"有询问且未提交"时跑，步进 550ms —— 比心跳慢、比倒计时肉眼可见，
    // 关键是不重排布局（只换样式表），所以牌桌尺寸不会跟着跳。
    m_alertTimer.setInterval(550);
    connect(&m_alertTimer, &QTimer::timeout, this, [this]() {
        m_alertArmed = !m_alertArmed;
        for (QPushButton* b : m_buttons) {
            const QString t = b->property("mjAction").toString();
            if (!t.isEmpty())
                b->setStyleSheet(actionButtonStyle(t, m_alertArmed));
        }
    });

    // 固定高度：按钮是动态增删的，若让布局自己撑高，牌桌控件的高度会随
    // 「有几个选项」变化，导致整张牌桌重新布局、牌面尺寸跳动。
    {
        QPushButton probe(QStringLiteral("测量"), this);   // i18n-keep: 隐藏的测高按钮，玩家看不到
        const int h = qMax(44, probe.sizeHint().height() + 16);
        probe.hide();
        setFixedHeight(h);
    }

    setAsk(QJsonObject());
}

void ActionBar::clearAsk()
{
    m_valid = false;
    m_ask = QJsonObject();
    m_options.clear();
    m_kind.clear();
    m_note.clear();
    m_deadlineAbs = 0;
    m_totalMs = 0;
    m_expired = false;
    if (m_riichiMode)
        setRiichiMode(false);
    m_tick.stop();
    m_alertTimer.stop();
    m_alertArmed = false;
    rebuild();
}

void ActionBar::setRiichiMode(bool on)
{
    if (m_riichiMode == on)
        return;
    m_riichiMode = on;
    emit riichiModeChanged(m_riichiMode);
    // ⚠ 标题必须跟着模式一起刷新。曾经这里只改「开」的文案、关的时候不管，
    //   而 onTick 又是从当前标题里截「（」前面那段 —— 于是连点两下立直按钮后，
    //   标题永远停在「立直：请点击要打出的宣言牌」，玩家以为还在立直模式，
    //   点手牌却打出一张普通弃牌（报障：重复点击立直按钮行为异常）。
    refreshTitle();
    if (m_riichiBtn)
        m_riichiBtn->setChecked(m_riichiMode);
    update();
}

QStringList ActionBar::riichiTiles() const
{
    QStringList out;
    for (const QJsonObject& o : m_options) {
        if (o.value(QStringLiteral("type")).toString() == QLatin1String("riichi"))
            out = proto::stringList(o.value(QStringLiteral("tiles")));
    }
    return out;
}

qint64 ActionBar::askId() const
{
    const QJsonValue v = m_ask.value(QStringLiteral("ask_id"));
    if (!v.isDouble())
        return -1;
    const double d = v.toDouble();
    // ask_id 是服务端的**整数**标识，会原样回带并参与「这条答复属于哪次询问」的判定。
    // 非有限 / 负数 / 非整数 / 超过 2^53（double 能精确表示整数的上界）一律当作
    // **没有有效 id**：宁可少带一个字段（服务端按老客户端兼容处理），
    // 也不能把一个越界的 double 硬转成 qint64（未定义行为）或伪造一个错误 id。
    if (!std::isfinite(d) || d < 0.0 || d > 9007199254740992.0 || d != std::floor(d))
        return -1;
    return static_cast<qint64>(d);
}

QJsonObject ActionBar::actionCmd(const QString& type) const
{
    // 已提交（clearAsk 后 m_valid=false）或已超时：不再产生任何动作。
    // 这是「重复点击」的第二道闸 —— 第一道是提交后立刻 clearAsk() 把按钮删掉。
    if (!m_valid || m_expired)
        return {};

    // 第三道闸（客户端侧）：`type` 必须出现在**当前询问**下发的 options 里。
    // 自动应答（260ms 的 applyAuto / 1200ms 的 --demo）是延时触发的，这期间询问
    // 可能已经换成另一条 —— 旧动作配上新 ask_id 发出去会替玩家做出他没做的选择。
    // 服务端还有一道同判据的闸（AGENTS §2.2），但客户端不该把希望全押在对面。
    bool known = false;
    for (const QJsonObject& o : m_options) {
        if (o.value(QStringLiteral("type")).toString() == type) {
            known = true;
            break;
        }
    }
    if (!known)
        return {};

    QJsonObject cmd;
    cmd.insert(QStringLiteral("cmd"), QStringLiteral("action"));
    cmd.insert(QStringLiteral("type"), type);
    const qint64 id = askId();
    if (id >= 0)
        cmd.insert(QStringLiteral("ask_id"), QJsonValue(id));   // 服务端靠它丢弃过期/重复包
    return cmd;
}

QJsonObject ActionBar::riichiCmd(const QString& tile) const
{
    QJsonObject cmd = actionCmd(QStringLiteral("riichi"));
    if (cmd.isEmpty())
        return cmd;
    cmd.insert(QStringLiteral("tile"), tile);
    return cmd;
}

QJsonObject ActionBar::discardCmd(const QString& tile, bool tsumogiri) const
{
    QJsonObject cmd = actionCmd(QStringLiteral("discard"));
    if (cmd.isEmpty())
        return cmd;
    cmd.insert(QStringLiteral("tile"), tile);
    // 摸切标记：服务端据此决定从**摸牌位**还是**暗手**取牌（见头文件里的说明）。
    cmd.insert(QStringLiteral("tsumogiri"), tsumogiri);
    return cmd;
}

QString ActionBar::baseTitle() const
{
    if (!m_valid)
        return lang::t("ui.action.waiting");
    QString t;
    if (m_kind == QLatin1String("turn"))
        t = lang::t("ui.action.your_turn");
    else if (m_kind == QLatin1String("claim"))
        t = lang::t("ui.action.can_claim");
    else if (m_kind == QLatin1String("chankan"))
        t = lang::t("ui.action.chankan");
    else
        t = lang::t("ui.action.choose");
    if (!m_note.isEmpty())
        t += QStringLiteral(" · ") + m_note;
    return t;
}

void ActionBar::refreshTitle()
{
    QString t = m_riichiMode ? lang::t("ui.action.riichi_prompt") : baseTitle();
    if (m_deadlineAbs > 0) {
        qint64 remain = m_deadlineAbs - QDateTime::currentMSecsSinceEpoch();
        if (remain < 0)
            remain = 0;
        t = lang::t("ui.action.title_with_seconds").arg(t).arg((remain + 999) / 1000);
    }
    if (m_title->text() != t)
        m_title->setText(t);
}

QString ActionBar::titleTextForTest() const
{
    return m_title->text();
}

QPushButton* ActionBar::buttonForTest(const QString& label) const
{
    for (QPushButton* b : m_buttons) {
        if (b->text() == label)
            return b;
    }
    return nullptr;
}

QStringList ActionBar::buttonTextsForTest() const
{
    QStringList out;
    for (QPushButton* b : m_buttons) {
        out << b->text();
    }
    return out;
}

bool ActionBar::canDiscard() const
{
    if (!m_valid || m_expired)
        return false;
    for (const QJsonObject& o : m_options) {
        if (o.value(QStringLiteral("type")).toString() == QLatin1String("discard"))
            return true;
    }
    return false;
}

void ActionBar::setNote(const QString& note)
{
    m_note = note;
    rebuild();
}

void ActionBar::setAsk(const QJsonObject& ask)
{
    const proto::AskInfo info = proto::parseAsk(ask);
    m_ask = ask;
    m_valid = info.valid;
    m_kind = info.kind;
    m_options = info.options;
    m_expired = false;
    m_totalMs = qMax<qint64>(0, info.deadlineMs);
    m_deadlineAbs = (info.deadlineMs > 0)
                        ? QDateTime::currentMSecsSinceEpoch() + info.deadlineMs
                        : 0;
    if (m_riichiMode)
        setRiichiMode(false);

    if (m_valid) {
        m_tick.start();
    } else {
        m_tick.stop();
    }
    rebuild();
    onTick();
}

void ActionBar::rebuild()
{
    // 清空布局（保留标题控件）
    while (QLayoutItem* item = m_layout->takeAt(0)) {
        if (QWidget* w = item->widget()) {
            if (w != m_title)
                delete w;
        }
        delete item;
    }
    m_buttons.clear();
    m_bars.clear();
    m_riichiBtn = nullptr;      // 按钮已被 delete，裸指针必须一起清掉
    m_layout->addWidget(m_title);

    if (!m_valid) {
        m_alertTimer.stop();
        m_alertArmed = false;
        refreshTitle();
        return;
    }
    // 有询问才闪（闪烁 = 「轮到你操作了」的提示）
    m_alertArmed = false;
    m_alertTimer.start();

    for (const QJsonObject& opt : m_options) {
        const QString type = opt.value(QStringLiteral("type")).toString();
        QString label;
        if (type == QLatin1String("discard"))
            label = lang::t("ui.action.discard_hint");
        else if (type == QLatin1String("riichi"))
            label = lang::t("ui.action.riichi");
        else if (type == QLatin1String("tsumo"))
            label = lang::t("ui.action.tsumo");
        else if (type == QLatin1String("ron"))
            label = lang::t("ui.action.ron");
        else if (type == QLatin1String("pon")) {
            // 副露赤宝选择：服务端对"用赤 / 不用赤"各下发一条 pon（各带 `tiles`）。
            // 用赤那条必须在文案里写出来，否则两个按钮长得一样（报障的原文）。
            label = lang::t("ui.action.pon");
            const QString aka = akaCodeIn(proto::stringList(opt.value(QStringLiteral("tiles"))));
            if (!aka.isEmpty())
                label += QStringLiteral(" ") + tileText(aka);
        } else if (type == QLatin1String("chi"))
            label = lang::t("ui.action.chi");
        else if (type == QLatin1String("kan"))
            label = lang::t("ui.action.kan");
        else if (type == QLatin1String("kyuushu"))
            label = lang::t("ui.action.kyuushu");
        else if (type == QLatin1String("pass"))
            label = lang::t("ui.action.pass");
        else
            continue;

        QPushButton* btn = new QPushButton(label, this);
        btn->setMinimumWidth(120);
        btn->setFocusPolicy(Qt::NoFocus);
        // 动作种类存成动态属性：闪烁时按它重算样式（不需要为每种动作各存一个指针）
        btn->setProperty("mjAction", type);
        btn->setStyleSheet(actionButtonStyle(type, false));
        m_layout->addWidget(btn);
        m_buttons.append(btn);

        // 每个按钮旁的倒计时进度
        QProgressBar* bar = new QProgressBar(this);
        bar->setRange(0, 1000);
        bar->setValue(1000);
        bar->setTextVisible(false);
        bar->setFixedWidth(44);
        bar->setFixedHeight(10);
        m_layout->addWidget(bar);
        m_bars.append(bar);

        if (type == QLatin1String("discard")) {
            connect(btn, &QPushButton::clicked, this, [this]() {
                emit hint(lang::t("ui.action.discard_click"));
            });
        } else if (type == QLatin1String("riichi")) {
            // 立直是个「模式」：点一下进入宣言态，再点一下取消。
            // 做成 checkable 是为了**让状态看得见** —— 旧版没有任何视觉反馈，
            // 玩家不确定点上没有就反复点，而第二次点击恰好把模式关掉，
            // 接着点手牌就打出普通弃牌（报障：重复点击立直按钮行为异常）。
            btn->setCheckable(true);
            btn->setChecked(m_riichiMode);
            m_riichiBtn = btn;
            connect(btn, &QPushButton::clicked, this, [this, btn]() {
                setRiichiMode(!riichiMode());
                btn->setChecked(riichiMode());   // 以实际模式为准，防止勾选态漂移
                if (riichiMode()) {
                    const QStringList tiles = riichiTiles();
                    emit hint(lang::t("ui.action.riichi_tiles").arg(joinTiles(tiles)));
                }
            });
        } else if (type == QLatin1String("chi")) {
            connect(btn, &QPushButton::clicked, this, &ActionBar::showChiMenu);
        } else if (type == QLatin1String("kan")) {
            connect(btn, &QPushButton::clicked, this, &ActionBar::showKanMenu);
        } else if (type == QLatin1String("pon")) {
            // 碰：把这一条选项原样带回去（可能带 `tiles` = 用哪几张，见 PROTOCOL §3.6）
            const QJsonObject o = opt;
            connect(btn, &QPushButton::clicked, this, [this, o]() { sendOption(o); });
        } else {
            const QString t = type;
            connect(btn, &QPushButton::clicked, this, [this, t]() { sendSimple(t); });
        }
    }
    m_layout->addStretch(1);
    refreshTitle();
}

void ActionBar::sendSimple(const QString& type)
{
    const QJsonObject cmd = actionCmd(type);
    if (cmd.isEmpty())
        return;
    emit actionReady(cmd);
}

void ActionBar::sendOption(const QJsonObject& opt)
{
    const QString type = opt.value(QStringLiteral("type")).toString();
    QJsonObject cmd = actionCmd(type);
    if (cmd.isEmpty())
        return;
    // 副露赤宝选择：服务端下发的 `tiles` 是「这一副**用哪几张**」的精确牌码，
    // 原样带回（不带就等于"普通牌优先"，见 PROTOCOL §3.6）。
    const QJsonArray tiles = opt.value(QStringLiteral("tiles")).toArray();
    if (!tiles.isEmpty())
        cmd.insert(QStringLiteral("tiles"), tiles);
    emit actionReady(cmd);
}

void ActionBar::showChiMenu()
{
    if (!m_valid || m_expired)
        return;
    QJsonObject chi;
    for (const QJsonObject& o : m_options) {
        if (o.value(QStringLiteral("type")).toString() == QLatin1String("chi"))
            chi = o;
    }
    const QJsonArray sets = chi.value(QStringLiteral("sets")).toArray();
    if (sets.isEmpty())
        return;

    QMenu menu(this);
    for (const QJsonValue& v : sets) {
        const QStringList tiles = proto::stringList(v);
        QAction* act = menu.addAction(joinTiles(tiles));
        act->setData(tiles);
    }
    QAction* chosen = menu.exec(QCursor::pos());
    if (!chosen)
        return;

    QJsonObject cmd = actionCmd(QStringLiteral("chi"));
    if (cmd.isEmpty())
        return;
    QJsonArray arr;
    for (const QString& t : chosen->data().toStringList())
        arr.append(t);
    cmd.insert(QStringLiteral("tiles"), arr);
    emit actionReady(cmd);
}

void ActionBar::showKanMenu()
{
    if (!m_valid || m_expired)
        return;
    QJsonObject kan;
    for (const QJsonObject& o : m_options) {
        if (o.value(QStringLiteral("type")).toString() == QLatin1String("kan"))
            kan = o;
    }
    const QJsonArray kans = kan.value(QStringLiteral("kans")).toArray();
    if (kans.isEmpty())
        return;

    QMenu menu(this);
    for (const QJsonValue& v : kans) {
        const QJsonObject k = v.toObject();
        const QString kind = k.value(QStringLiteral("kind")).toString();
        const QString tile = k.value(QStringLiteral("tile")).toString();
        QString prefix = lang::t("ui.action.kan");
        if (kind == QLatin1String("ankan"))
            prefix = lang::t("ui.action.ankan");
        else if (kind == QLatin1String("kakan"))
            prefix = lang::t("ui.action.kakan");
        else if (kind == QLatin1String("daiminkan"))
            prefix = lang::t("ui.action.daiminkan");
        QString label = QStringLiteral("%1 %2").arg(prefix, tileText(tile));
        // 大明杠的赤宝选择：用赤五那一条在菜单里写出来（与碰同一口径）
        const QString aka = akaCodeIn(proto::stringList(k.value(QStringLiteral("tiles"))));
        if (!aka.isEmpty())
            label += QStringLiteral(" ") + tileText(aka);
        QAction* act = menu.addAction(label);
        act->setData(k);
    }
    QAction* chosen = menu.exec(QCursor::pos());
    if (!chosen)
        return;

    const QJsonObject k = chosen->data().toJsonObject();
    QJsonObject cmd = actionCmd(QStringLiteral("kan"));
    if (cmd.isEmpty())
        return;
    cmd.insert(QStringLiteral("kind"), k.value(QStringLiteral("kind")).toString());
    cmd.insert(QStringLiteral("tile"), k.value(QStringLiteral("tile")).toString());
    // 大明杠的赤宝选择：选中的那条带 `tiles` 就原样带回（不带 = 普通牌优先）
    const QJsonArray kanTiles = k.value(QStringLiteral("tiles")).toArray();
    if (!kanTiles.isEmpty())
        cmd.insert(QStringLiteral("tiles"), kanTiles);
    emit actionReady(cmd);
}

void ActionBar::onTick()
{
    if (!m_valid)
        return;

    qint64 remain = -1;
    if (m_deadlineAbs > 0) {
        remain = m_deadlineAbs - QDateTime::currentMSecsSinceEpoch();
        if (remain < 0)
            remain = 0;
    }

    const int permille =
        (m_totalMs > 0)
            ? int(qBound<qint64>(qint64(0), remain * 1000 / m_totalMs, qint64(1000)))
            : 1000;
    for (QProgressBar* bar : m_bars)
        bar->setValue(permille);

    if (m_deadlineAbs > 0 && remain <= 0 && !m_expired) {
        m_expired = true;
        m_tick.stop();
        for (QPushButton* b : m_buttons)
            b->setEnabled(false);
        m_title->setText(lang::t("ui.action.timed_out"));
        return;
    }
    // 标题统一由 refreshTitle() 产生（它同时管倒计时与立直模式文案）
    refreshTitle();
}
