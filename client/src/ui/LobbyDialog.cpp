#include "ui/LobbyDialog.h"

#include "i18n/Lang.h"

#include <QComboBox>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QPushButton>
#include <QSpinBox>
#include <QVBoxLayout>

LobbyDialog::LobbyDialog(QWidget* parent)
    : QDialog(parent)
{
    setWindowTitle(lang::t("ui.lobby.window_title"));
    setMinimumSize(560, 520);

    auto* root = new QVBoxLayout(this);

    // ---- 连接 ----
    auto* connBox = new QGroupBox(lang::t("ui.lobby.connect_group"), this);
    auto* connForm = new QFormLayout(connBox);
    m_host = new QLineEdit(QStringLiteral("127.0.0.1"), connBox);
    m_host->setToolTip(lang::t(QStringLiteral("ui.lobby.conn_hint")));
    m_port = new QSpinBox(connBox);
    m_port->setRange(1, 65535);
    m_port->setValue(10086);
    m_name = new QLineEdit(lang::t("ui.lobby.col_player"), connBox);
    m_connectBtn = new QPushButton(lang::t("ui.lobby.connect"), connBox);
    m_settingsBtn = new QPushButton(lang::t(QStringLiteral("ui.settings.title")), connBox);
    m_settingsBtn->setToolTip(lang::t(QStringLiteral("ui.settings.hint")));
    connect(m_settingsBtn, &QPushButton::clicked, this, &LobbyDialog::settingsRequested);
    connForm->addRow(lang::t("ui.lobby.host"), m_host);
    connForm->addRow(lang::t("ui.lobby.port"), m_port);
    connForm->addRow(lang::t("ui.lobby.nickname"), m_name);
    auto* connBtns = new QHBoxLayout();
    connBtns->addWidget(m_connectBtn);
    connBtns->addWidget(m_settingsBtn);
    connBtns->addStretch(1);
    connForm->addRow(QString(), connBtns);
    root->addWidget(connBox);

    // ---- 房间列表 ----
    auto* roomBox = new QGroupBox(lang::t("ui.lobby.room_list"), this);
    auto* roomLayout = new QVBoxLayout(roomBox);
    m_rooms = new QListWidget(roomBox);
    m_rooms->setMinimumHeight(130);
    m_refreshBtn = new QPushButton(lang::t("ui.lobby.refresh"), roomBox);
    m_replayBtn = new QPushButton(lang::t("ui.replay.open_list"), roomBox);
    m_replayBtn->setToolTip(lang::t(QStringLiteral("ui.replay.open_list_hint")));
    connect(m_replayBtn, &QPushButton::clicked, this, &LobbyDialog::replayRequested);
    auto* refreshRow = new QHBoxLayout();
    refreshRow->addWidget(m_refreshBtn);
    refreshRow->addWidget(m_replayBtn);
    refreshRow->addStretch(1);
    roomLayout->addWidget(m_rooms);
    roomLayout->addLayout(refreshRow);
    root->addWidget(roomBox);

    // ---- 创建房间 ----
    auto* createBox = new QGroupBox(lang::t("ui.lobby.create_group"), this);
    auto* createForm = new QFormLayout(createBox);
    m_roomName = new QLineEdit(lang::t("ui.lobby.my_room"), createBox);
    // 规则预设：服务端按 preset 铺一整套取舍，再让下面那些单项覆盖它
    //（M.League 是服务端默认值；这里显式选一遍，玩家才看得见自己打的是哪套规则）。
    m_preset = new QComboBox(createBox);
    m_preset->addItem(lang::t("ui.lobby.preset_mleague"), QStringLiteral("mleague"));
    m_preset->addItem(lang::t("ui.lobby.preset_tenhou"), QStringLiteral("tenhou"));
    m_preset->addItem(lang::t("ui.lobby.preset_majsoul"), QStringLiteral("majsoul"));
    m_preset->setToolTip(lang::t(QStringLiteral("ui.lobby.preset_hint")));
    m_length = new QComboBox(createBox);
    m_length->addItem(lang::t("ui.lobby.rule_hanchan"), QStringLiteral("hanchan"));
    m_length->addItem(lang::t("ui.lobby.rule_tonpuu"), QStringLiteral("tonpuu"));
    m_aka = new QComboBox(createBox);
    m_aka->addItem(lang::t("ui.lobby.aka_0"), 0);
    m_aka->addItem(lang::t("ui.lobby.aka_3"), 3);
    m_aka->addItem(lang::t("ui.lobby.aka_4"), 4);
    m_aka->setCurrentIndex(1);
    // 思考时间。规格串写作「额外+每巡」，与通行写法一致：
    //   "20+5" = 总额外时长 20 秒 + 每巡基本时长 5 秒（大数在前）
    // 注意顺序不能反：每巡基本时长一般远小于总额外时长。
    m_think = new QComboBox(createBox);
    {
        const char* presets[] = {"20+5", "0+15", "10+5", "30+5", "15+3", "60+10"};
        for (const char* preset : presets) {
            const QString spec = QString::fromLatin1(preset);
            const QStringList parts = spec.split(QLatin1Char('+'));
            const int bankSec = parts.value(0).toInt();
            const int baseSec = parts.value(1).toInt();
            m_think->addItem(bankSec > 0
                                     ? lang::t("ui.lobby.clock_both").arg(baseSec).arg(bankSec)
                                     : lang::t("ui.lobby.clock_base").arg(baseSec),
                             spec);
        }
        m_think->setCurrentIndex(0);
        m_think->setToolTip(lang::t(QStringLiteral("ui.lobby.clock_hint")));
    }
    m_bots = new QSpinBox(createBox);
    m_bots->setRange(0, 3);
    m_bots->setValue(0);
    // 机器人用哪一代 AI：清单来自服务端（`hello_ok.bot_ais`），这里先放"跟服务端默认"。
    // ⚠ 客户端**只发名字**：`net:<路径>` 是服务器本机文件，服务端只认注册表里的名字
    //   （见 `mahjong.ai.BotAis`），所以这里也不给任何手填入口。
    m_botAi = new QComboBox(createBox);
    m_botAi->addItem(lang::t(QStringLiteral("ui.lobby.bot_ai_default")), QString());
    m_botAi->setToolTip(lang::t(QStringLiteral("ui.lobby.bot_ai_tip")));
    // 一位必要点数（`docs/日本麻将.md` L116/L143/L157）：0 = 不要求。
    // 自选填写框 —— 切预设时填该预设的默认值（M.League 0 / 天凤·雀魂 30000），
    // 玩家可以改成任意值；它决定 All Last 轮庄后是否进延长战、以及庄家能否和了止/听牌止。
    m_requiredPoints = new QSpinBox(createBox);
    m_requiredPoints->setRange(0, 1000000);
    m_requiredPoints->setSingleStep(1000);
    m_requiredPoints->setSpecialValueText(lang::t(QStringLiteral("ui.lobby.required_points_none")));
    m_requiredPoints->setToolTip(lang::t(QStringLiteral("ui.lobby.required_points_hint")));
    m_requiredPoints->setValue(lobbyrules::defaultRequiredPoints(m_preset->currentData().toString()));
    connect(m_preset, QOverload<int>::of(&QComboBox::currentIndexChanged),
            this, &LobbyDialog::onPresetChanged);
    m_createBtn = new QPushButton(lang::t("ui.lobby.create_room"), createBox);
    createForm->addRow(lang::t("ui.lobby.room_name"), m_roomName);
    createForm->addRow(lang::t("ui.lobby.preset"), m_preset);
    createForm->addRow(lang::t("ui.lobby.rules"), m_length);
    createForm->addRow(lang::t("ui.lobby.aka"), m_aka);
    createForm->addRow(lang::t("ui.lobby.clock"), m_think);
    createForm->addRow(lang::t("ui.lobby.required_points"), m_requiredPoints);
    createForm->addRow(lang::t("ui.lobby.fill_bots"), m_bots);
    createForm->addRow(lang::t("ui.lobby.bot_ai"), m_botAi);
    createForm->addRow(QString(), m_createBtn);
    root->addWidget(createBox);

    // ---- 加入房间 ----
    auto* joinBox = new QGroupBox(lang::t("ui.lobby.join_group"), this);
    auto* joinRow = new QHBoxLayout(joinBox);
    m_joinId = new QLineEdit(joinBox);
    m_joinId->setPlaceholderText(lang::t("ui.lobby.room_id_placeholder"));
    m_joinBtn = new QPushButton(lang::t("ui.lobby.join"), joinBox);
    joinRow->addWidget(m_joinId, 1);
    joinRow->addWidget(m_joinBtn);
    root->addWidget(joinBox);

    m_status = new QLabel(lang::t("ui.main.disconnected"), this);
    m_status->setWordWrap(true);
    root->addWidget(m_status);

    connect(m_connectBtn, &QPushButton::clicked, this, &LobbyDialog::onConnectClicked);
    connect(m_refreshBtn, &QPushButton::clicked, this, &LobbyDialog::refreshRequested);
    connect(m_createBtn, &QPushButton::clicked, this, &LobbyDialog::onCreateClicked);
    connect(m_joinBtn, &QPushButton::clicked, this, &LobbyDialog::onJoinClicked);
    connect(m_rooms, &QListWidget::itemDoubleClicked, this,
            [this](QListWidgetItem*) { onJoinClicked(); });
    connect(m_joinId, &QLineEdit::returnPressed, this, &LobbyDialog::onJoinClicked);
}

void LobbyDialog::onConnectClicked()
{
    if (m_connectBtn->text() == lang::t("ui.lobby.disconnect")) {
        emit connectRequested(QString(), 0, playerName()); // 端口 0 = 断开
        return;
    }
    emit connectRequested(host(), port(), playerName());
}

QString LobbyDialog::host() const
{
    return m_host->text().trimmed();
}

quint16 LobbyDialog::port() const
{
    return quint16(m_port->value());
}

QString LobbyDialog::playerName() const
{
    const QString n = m_name->text().trimmed();
    // 昵称留空时的默认名（**用户数据**，但也是给人看的字，所以走语言文件）
    return n.isEmpty() ? lang::t(QStringLiteral("ui.lobby.default_name")) : n;
}

void LobbyDialog::applySettings(const QString& host, quint16 port, const QString& name)
{
    if (!host.isEmpty()) {
        m_host->setText(host);
    }
    if (port != 0) {
        m_port->setValue(int(port));
    }
    if (!name.isEmpty()) {
        m_name->setText(name);
    }
}

void LobbyDialog::setConnected(bool on)
{
    m_connectBtn->setText(on ? lang::t("ui.lobby.disconnect") : lang::t("ui.lobby.connect"));
    m_refreshBtn->setEnabled(on);
    m_createBtn->setEnabled(on);
    m_joinBtn->setEnabled(on);
    if (on)
        setStatus(lang::t("ui.main.connected"));
}

void LobbyDialog::setStatus(const QString& text)
{
    m_status->setText(text);
}

void LobbyDialog::setRooms(const QJsonArray& rooms)
{
    m_rooms->clear();
    for (const QJsonValue& v : rooms) {
        if (!v.isObject())
            continue;
        const QJsonObject o = v.toObject();
        const QString id = o.value(QStringLiteral("id")).toString();
        const QString name = o.value(QStringLiteral("name")).toString();
        const int players = o.value(QStringLiteral("players")).toInt();
        const int seats = o.value(QStringLiteral("seats")).toInt(4);
        const bool playing = o.value(QStringLiteral("playing")).toBool();
        auto* item = new QListWidgetItem(
            QStringLiteral("%1  %2   %3/%4%5")
                .arg(id, name)
                .arg(players)
                .arg(seats)
                .arg(playing ? lang::t("ui.lobby.playing_tag") : QString()),
            m_rooms);
        item->setData(Qt::UserRole, id);
    }
    if (rooms.isEmpty())
        setStatus(lang::t("ui.lobby.no_rooms"));
}

QString LobbyDialog::selectedRoomId() const
{
    QListWidgetItem* item = m_rooms->currentItem();
    if (item)
        return item->data(Qt::UserRole).toString();
    return QString();
}

void LobbyDialog::onCreateClicked()
{
    QJsonObject rules;
    // preset 必须**先**塞：服务端按它铺一整套取舍，再让后面的单项覆盖
    rules.insert(QStringLiteral("preset"), m_preset->currentData().toString());
    rules.insert(QStringLiteral("length"), m_length->currentData().toString());
    rules.insert(QStringLiteral("aka"), m_aka->currentData().toInt());
    // 一位必要点数：0 = 不要求（房主自己填的值，覆盖预设）
    rules.insert(QStringLiteral("required_points"), m_requiredPoints->value());
    // 思考时间：规格串「额外+每巡」→ "20+5" = base 5000ms / bank 20000ms
    const QStringList think = m_think->currentData().toString().split(QLatin1Char('+'));
    if (think.size() == 2) {
        rules.insert(QStringLiteral("thinking_bank_ms"), think.at(0).toInt() * 1000);
        rules.insert(QStringLiteral("thinking_base_ms"), think.at(1).toInt() * 1000);
    }
    emit createRoomRequested(m_roomName->text().trimmed(), rules, m_bots->value(),
                             selectedBotAi());
}

QString LobbyDialog::selectedBotAi() const
{
    // 数据为空 = 「跟服务端默认」：报文里就**不带** `bot_ai`（服务端用自己的默认）
    return m_botAi ? m_botAi->currentData().toString() : QString();
}

void LobbyDialog::setBotAis(const QJsonArray& ais)
{
    if (!m_botAi)
        return;
    const QString keep = selectedBotAi();
    m_botAi->clear();
    m_botAi->addItem(lang::t(QStringLiteral("ui.lobby.bot_ai_default")), QString());
    for (const QJsonValue& v : ais) {
        if (!v.isObject())
            continue;
        const QJsonObject o = v.toObject();
        const QString name = o.value(QStringLiteral("name")).toString();
        if (name.isEmpty())
            continue;
        // 名字进下拉框（服务端**只发名字与默认标记**：策略串里是服务器本机路径，不下发）
        m_botAi->addItem(name, name);
    }
    const int idx = m_botAi->findData(keep);
    m_botAi->setCurrentIndex(idx >= 0 ? idx : 0);
}

int lobbyrules::defaultRequiredPoints(const QString& preset)
{
    // M.League 不使用一位必要点数（打到南 4 局庄家轮庄为止）；《天凤》《雀魂》段位场 = 30000
    return preset == QLatin1String("mleague") ? 0 : 30000;
}

void LobbyDialog::onPresetChanged()
{
    m_requiredPoints->setValue(lobbyrules::defaultRequiredPoints(m_preset->currentData().toString()));
}

void LobbyDialog::onJoinClicked(){
    QString id = m_joinId->text().trimmed().toUpper();
    if (id.isEmpty())
        id = selectedRoomId();
    if (id.isEmpty()) {
        setStatus(lang::t("ui.lobby.enter_room_id"));
        return;
    }
    emit joinRoomRequested(id);
}
