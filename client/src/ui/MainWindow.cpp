#include "ui/MainWindow.h"

#include "i18n/Lang.h"
#include "model/AutoPolicy.h"
#include "model/Sound.h"
#include "model/Tile.h"
#include "net/Protocol.h"
#include "ui/ActionBar.h"
#include "ui/AutoBar.h"
#include "ui/LobbyDialog.h"
#include "ui/ReplayWindow.h"
#include "ui/ResultDialog.h"
#include "ui/SettingsDialog.h"
#include "ui/TableView.h"

#include <QApplication>
#include <QFile>
#include <QFont>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPushButton>
#include <QStackedWidget>
#include <QStatusBar>
#include <QTextBrowser>
#include <QTimer>
#include <QVBoxLayout>

namespace {

QString evName(const QJsonObject& ev)
{
    return ev.value(QStringLiteral("ev")).toString();
}

// 自动应答发出前的小延时：让**摸牌与询问栏先画出来**再动作（否则刚摸到就被打掉，
// 玩家看不到发生了什么），也给玩家一点时间立刻关掉开关。
// 真正发出时若询问已失效（clearAsk / 超时 / 别处已提交），actionCmd() 会返回空对象，
// onActionReady() 直接丢弃 —— 不需要自己维护「已自动应答过」的标志。
constexpr int kAutoDelayMs = 260;

/**
 * 座位号 → 门风字形（0=东/起家、1=南、2=西、3=北）。
 *
 * 门风**就是**座次（`Round.seatWind()` 按 `(seat - dealer + 4) % 4` 推），所以"选座位"
 * 与"选门风"是同一件事 —— 自选座位按钮上直接写门风，玩家一眼看得出自己会坐哪。
 * 这里是字形不是文案，所以不进语言文件（同 `TileRenderer` 里的「東」）。
 */
const char* const SEAT_WIND[4] = { "东", "南", "西", "北" };   // i18n-keep: 牌面字形（同「萬」「東」），不是文案

} // namespace

MainWindow::MainWindow(QWidget* parent)
    : QMainWindow(parent)
{
    setWindowTitle(lang::t("ui.main.window_title"));
    resize(1280, 800);

    m_stack = new QStackedWidget(this);
    setCentralWidget(m_stack);
    statusBar()->showMessage(lang::t("ui.main.disconnected"));

    buildWaitingPage();
    buildTablePage();

    m_lobby = new LobbyDialog(this);
    // 大厅的「对局回放」：打开回放窗口（它自己连一条，与是否入座无关）
    connect(m_lobby, &LobbyDialog::replayRequested, this, [this]() { openReplayWindow(); });
    // 大厅的「设置」：个人设置对话框（地址/端口/昵称/材质包）
    connect(m_lobby, &LobbyDialog::settingsRequested, this, &MainWindow::openSettings);
    connect(m_lobby, &LobbyDialog::connectRequested, this,
            [this](const QString& host, quint16 port, const QString& name) {
                if (port == 0) {
                    m_net.disconnectFromServer();
                    return;
                }
                m_myName = name;
                m_host = host;      // 回放窗口要用同一个目标
                m_port = port;
                // 「个人设置」：从大厅连过一次就把地址/端口/昵称记下来，下次启动直接带出来
                saveCurrentEndpoint();
                QJsonObject hello;
                hello.insert(QStringLiteral("cmd"), QStringLiteral("hello"));
                hello.insert(QStringLiteral("name"), name);
                hello.insert(QStringLiteral("ver"), proto::Version);
                m_pendingHello = hello;
                m_lobby->setStatus(lang::t("ui.main.connecting").arg(host).arg(port));
                m_net.connectToServer(host, port);
            });
    connect(m_lobby, &LobbyDialog::refreshRequested, this, [this]() {
        QJsonObject cmd;
        cmd.insert(QStringLiteral("cmd"), QStringLiteral("list_rooms"));
        sendCommand(cmd);
    });
    connect(m_lobby, &LobbyDialog::createRoomRequested, this,
            [this](const QString& name, const QJsonObject& rules, int bots) {
                QJsonObject cmd;
                cmd.insert(QStringLiteral("cmd"), QStringLiteral("create_room"));
                cmd.insert(QStringLiteral("name"), name.isEmpty() ? lang::t("ui.main.room") : name);
                cmd.insert(QStringLiteral("rules"), rules);
                cmd.insert(QStringLiteral("fill_bots"), bots);
                sendCommand(cmd);
            });
    connect(m_lobby, &LobbyDialog::joinRoomRequested, this, [this](const QString& id) {
        QJsonObject cmd;
        cmd.insert(QStringLiteral("cmd"), QStringLiteral("join_room"));
        cmd.insert(QStringLiteral("room"), id);
        sendCommand(cmd);
    });

    connect(&m_net, &NetClient::connected, this, &MainWindow::onConnected);
    connect(&m_net, &NetClient::disconnected, this, &MainWindow::onDisconnected);
    connect(&m_net, &NetClient::errorOccurred, this, &MainWindow::onNetError);
    connect(&m_net, &NetClient::eventReceived, this, &MainWindow::onEvent);

    // 分数栏每收到**一个事件**刷新一次，由 onEvent() 末尾显式调用。
    // 这里**不再**接 TableModel::changed —— 两者一起接会让同一事件刷两遍，
    // 而每次刷新都要跑 waits()（上千次和了形判定）＋振听检查，代价翻倍。
    connect(&m_model, &TableModel::chatReceived, this,
            [this](int seat, const QString& name, const QString& text) {
                Q_UNUSED(seat);
                appendChat(name, text);
            });

    showLobby();
}

MainWindow::~MainWindow() = default;

void MainWindow::buildWaitingPage()
{
    m_waitPage = new QWidget(this);
    auto* root = new QVBoxLayout(m_waitPage);

    m_roomLabel = new QLabel(lang::t("ui.main.not_in_room"), m_waitPage);
    QFont f = m_roomLabel->font();
    f.setPointSize(f.pointSize() + 3);
    f.setBold(true);
    m_roomLabel->setFont(f);
    root->addWidget(m_roomLabel);

    auto* seatBox = new QGroupBox(lang::t("ui.main.seat"), m_waitPage);
    auto* seatLayout = new QVBoxLayout(seatBox);
    for (int i = 0; i < 4; ++i) {
        auto* row = new QHBoxLayout();
        m_seatLabels[i] = new QLabel(lang::t("ui.main.seat_empty").arg(i), seatBox);
        row->addWidget(m_seatLabels[i], 1);
        // 开局前**自选座位**（门风就是座次：0=东/起家）。按钮在牌局开始后自动置灰。
        m_takeSeatBtn[i] = new QPushButton(lang::t("ui.main.take_seat").arg(SEAT_WIND[i]), seatBox);
        m_takeSeatBtn[i]->setToolTip(lang::t("ui.main.take_seat_tip"));
        m_takeSeatBtn[i]->setFocusPolicy(Qt::NoFocus);
        connect(m_takeSeatBtn[i], &QPushButton::clicked, this, [this, i]() { takeSeat(i); });
        row->addWidget(m_takeSeatBtn[i]);
        seatLayout->addLayout(row);
    }
    root->addWidget(seatBox);

    auto* btnRow = new QHBoxLayout();
    m_readyBtn = new QPushButton(lang::t("ui.main.ready"), m_waitPage);
    m_addBotBtn = new QPushButton(lang::t("ui.main.add_bot"), m_waitPage);
    m_removeBotBtn = new QPushButton(lang::t("ui.main.remove_bot"), m_waitPage);
    m_startBtn = new QPushButton(lang::t("ui.main.start_game"), m_waitPage);
    m_shuffleBtn = new QPushButton(lang::t("ui.main.shuffle_seats"), m_waitPage);
    m_shuffleBtn->setToolTip(lang::t("ui.main.shuffle_seats_tip"));
    auto* leaveBtn = new QPushButton(lang::t("ui.main.leave_room"), m_waitPage);
    btnRow->addWidget(m_readyBtn);
    btnRow->addWidget(m_addBotBtn);
    btnRow->addWidget(m_removeBotBtn);
    btnRow->addWidget(m_startBtn);
    btnRow->addWidget(m_shuffleBtn);
    btnRow->addStretch(1);
    btnRow->addWidget(leaveBtn);
    root->addLayout(btnRow);

    m_waitChat = new QTextBrowser(m_waitPage);
    m_waitChat->setMinimumHeight(120);
    root->addWidget(m_waitChat, 1);

    auto* chatRow = new QHBoxLayout();
    m_waitChatEdit = new QLineEdit(m_waitPage);
    m_waitChatEdit->setPlaceholderText(lang::t("ui.main.chat_placeholder"));
    auto* sendBtn = new QPushButton(lang::t("ui.main.send"), m_waitPage);
    chatRow->addWidget(m_waitChatEdit, 1);
    chatRow->addWidget(sendBtn);
    root->addLayout(chatRow);

    connect(m_readyBtn, &QPushButton::clicked, this, [this]() {
        m_ready = !m_ready;
        QJsonObject cmd;
        cmd.insert(QStringLiteral("cmd"), QStringLiteral("ready"));
        cmd.insert(QStringLiteral("ready"), m_ready);
        sendCommand(cmd);
        m_readyBtn->setText(m_ready ? lang::t("ui.main.cancel_ready") : lang::t("ui.main.ready"));
    });
    connect(m_addBotBtn, &QPushButton::clicked, this, [this]() {
        QJsonObject cmd;
        cmd.insert(QStringLiteral("cmd"), QStringLiteral("add_bot"));
        sendCommand(cmd);
    });
    connect(m_removeBotBtn, &QPushButton::clicked, this, [this]() {
        const QJsonArray seats = m_room.value(QStringLiteral("seats")).toArray();
        for (const QJsonValue& v : seats) {
            if (!v.isObject())
                continue;
            const QJsonObject o = v.toObject();
            if (o.value(QStringLiteral("bot")).toBool()) {
                QJsonObject cmd;
                cmd.insert(QStringLiteral("cmd"), QStringLiteral("remove_bot"));
                cmd.insert(QStringLiteral("seat"), o.value(QStringLiteral("seat")).toInt());
                sendCommand(cmd);
                return;
            }
        }
        statusBar()->showMessage(lang::t("ui.main.no_bot_to_remove"), 3000);
    });
    connect(m_startBtn, &QPushButton::clicked, this, [this]() {
        QJsonObject cmd;
        cmd.insert(QStringLiteral("cmd"), QStringLiteral("start_game"));
        sendCommand(cmd);
    });
    // 随机洗座（随机门风）：房主一键打乱四家座位；洗完大家重新准备。
    connect(m_shuffleBtn, &QPushButton::clicked, this, [this]() {
        QJsonObject cmd;
        cmd.insert(QStringLiteral("cmd"), QStringLiteral("shuffle_seats"));
        sendCommand(cmd);
    });
    connect(leaveBtn, &QPushButton::clicked, this, &MainWindow::onLeaveRoom);
    connect(sendBtn, &QPushButton::clicked, this, &MainWindow::onChatSend);
    connect(m_waitChatEdit, &QLineEdit::returnPressed, this, &MainWindow::onChatSend);

    m_stack->addWidget(m_waitPage);
}

void MainWindow::buildTablePage()
{
    m_tablePage = new QWidget(this);
    auto* root = new QHBoxLayout(m_tablePage);
    root->setContentsMargins(0, 0, 0, 0);

    auto* left = new QVBoxLayout();
    m_table = new TableView(m_tablePage);
    m_table->setModel(&m_model);
    m_actions = new ActionBar(m_tablePage);
    // 三个自动开关摆在**牌桌外**（牌桌下方、操作栏之下），不挤占牌桌绘制区，
    // 也不进 ActionBar —— 后者的按钮是随询问动态增删的，混进去会改它的固定高度。
    m_autoBar = new AutoBar(m_tablePage);
    left->addWidget(m_table, 1);
    left->addWidget(m_actions);
    left->addWidget(m_autoBar);
    root->addLayout(left, 1);

    auto* right = new QWidget(m_tablePage);
    right->setFixedWidth(240);
    auto* rightLayout = new QVBoxLayout(right);

    m_scorePanel = new QLabel(right);
    m_scorePanel->setTextFormat(Qt::RichText);
    m_scorePanel->setAlignment(Qt::AlignTop);
    m_scorePanel->setWordWrap(true);
    rightLayout->addWidget(m_scorePanel);

    auto* chatLabel = new QLabel(lang::t("ui.main.chat"), right);
    rightLayout->addWidget(chatLabel);
    m_chatView = new QTextBrowser(right);
    rightLayout->addWidget(m_chatView, 1);
    // 聊天输入行：输入框 + 「发送」按钮。
    // ⚠ 这里曾经**只有** QLineEdit 而没有任何按钮 —— 看得见输入框却发现点不了发送，
    //   只有回车能发（用户报障："消息发送键没有绑定发送事件"）。
    //   与等待页那一行保持同样的结构（输入框 + 按钮 + 同一个槽）。
    auto* chatInputRow = new QHBoxLayout();
    m_chatEdit = new QLineEdit(right);
    m_chatEdit->setPlaceholderText(lang::t("ui.main.enter_to_send"));
    chatInputRow->addWidget(m_chatEdit, 1);
    auto* chatSendBtn = new QPushButton(lang::t("ui.main.send"), right);
    chatSendBtn->setToolTip(lang::t("ui.main.enter_to_send"));
    chatInputRow->addWidget(chatSendBtn);
    rightLayout->addLayout(chatInputRow);

    root->addWidget(right);
    m_stack->addWidget(m_tablePage);

    connect(m_table, &TableView::tileClicked, this, &MainWindow::onTileClicked);
    connect(m_actions, &ActionBar::actionReady, this, &MainWindow::onActionReady);
    connect(m_actions, &ActionBar::riichiModeChanged, this, &MainWindow::onRiichiModeChanged);
    connect(m_autoBar, &AutoBar::flagsChanged, this, &MainWindow::onAutoFlagsChanged);
    connect(m_actions, &ActionBar::hint, this,
            [this](const QString& text) { statusBar()->showMessage(text, 4000); });
    connect(m_chatEdit, &QLineEdit::returnPressed, this, &MainWindow::onChatSend);
    connect(chatSendBtn, &QPushButton::clicked, this, &MainWindow::onChatSend);
}

void MainWindow::showLobby()
{
    m_model.reset();
    m_ready = false;
    resetAutoFlags();   // 离开牌桌：自动开关也一并复位，别带进下一个房间
    if (m_readyBtn)
        m_readyBtn->setText(lang::t("ui.main.ready"));
    if (m_lobby) {
        m_lobby->setConnected(m_net.isConnected());
        m_lobby->show();
        m_lobby->raise();
        m_lobby->activateWindow();
    }
    m_stack->setCurrentWidget(m_waitPage);
}

bool MainWindow::isHost() const
{
    if (m_room.isEmpty())
        return false;
    const int host = m_room.value(QStringLiteral("host")).toInt(-1);
    // host 字段语义在协议里未明确（座位号或 pid），两种解释都接受
    return host == m_model.mySeat() || (m_myPid != 0 && host == m_myPid);
}

void MainWindow::updateWaitingRoom(const QJsonObject& room)
{
    m_room = room;
    const QString id = room.value(QStringLiteral("id")).toString();
    const QString name = room.value(QStringLiteral("name")).toString();
    m_roomLabel->setText(lang::t("ui.main.room_status").arg(id, name));

    const QJsonArray seats = room.value(QStringLiteral("seats")).toArray();
    for (int i = 0; i < 4; ++i) {
        QString text = lang::t("ui.main.seat_empty").arg(i);
        if (i < seats.size() && seats.at(i).isObject()) {
            const QJsonObject o = seats.at(i).toObject();
            text = lang::t("ui.main.seat_row")
                       .arg(i)
                       .arg(o.value(QStringLiteral("name")).toString())
                       .arg(o.value(QStringLiteral("score")).toInt())
                       .arg(o.value(QStringLiteral("ready")).toBool() ? lang::t("ui.main.is_ready")
                                                                     : lang::t("ui.main.not_ready"))
                       .arg(o.value(QStringLiteral("bot")).toBool() ? lang::t("ui.main.bot_tag")
                                                                    : QString());
            // 用 pid 反查自己的座位（room 事件里不含 seat→me 的映射）
            if (m_myPid != 0 && o.value(QStringLiteral("pid")).toInt(-1) == m_myPid) {
                m_model.setMySeat(i);
                m_model.setPlayerName(i, m_myName);
            }
        }
        m_seatLabels[i]->setText(text);
        // 自选座位按钮：牌局进行中不可用；已经是我坐的那一格也不可用（点了没意义）
        const bool playing = room.value(QStringLiteral("playing")).toBool();
        if (m_takeSeatBtn[i]) {
            m_takeSeatBtn[i]->setEnabled(!playing && i != m_model.mySeat());
        }
    }

    bool full = true;
    for (int i = 0; i < 4; ++i) {
        if (i >= seats.size() || !seats.at(i).isObject())
            full = false;
    }
    const bool host = isHost();
    m_startBtn->setEnabled(host && full && !room.value(QStringLiteral("playing")).toBool());
    m_addBotBtn->setEnabled(host);
    m_removeBotBtn->setEnabled(host);
    m_startBtn->setToolTip(host ? QString() : lang::t("ui.main.host_only_start"));
    // 洗座只有房主能用，且只在开局前
    if (m_shuffleBtn) {
        m_shuffleBtn->setEnabled(host && !room.value(QStringLiteral("playing")).toBool());
        m_shuffleBtn->setToolTip(host ? lang::t("ui.main.shuffle_seats_tip")
                                      : lang::t("ui.main.host_only_start"));
    }
}

void MainWindow::updateScorePanel()
{
    if (!m_scorePanel)
        return;
    QString html = QStringLiteral("<h3>%1</h3>").arg(m_model.roundText());
    html += lang::t("ui.main.tiles_left_html")
                .arg(m_model.tilesLeft())
                .arg(m_model.deadWallLeft());
    html += QStringLiteral("<table cellspacing='0' cellpadding='3'>");
    for (int s = 0; s < 4; ++s) {
        const bool me = (s == m_model.mySeat());
        html += QStringLiteral("<tr><td>%1%2</td><td align='right'>%3</td><td>%4</td></tr>")
                    .arg(me ? QStringLiteral("<b>") : QString(),
                         // 玩家名是**用户数据**，进 HTML 前必须转义（与聊天同一套处理），
                         // 否则名字里的 `<`/`&` 会破坏表格结构、甚至注入标记。
                         m_model.playerName(s).toHtmlEscaped()
                             + (me ? QStringLiteral("</b>") : QString()))
                    .arg(m_model.score(s))
                    .arg(m_model.riichi(s) ? QStringLiteral("立") : QString());  // i18n-keep: 立直标记（字形，不是文案）
    }
    html += QStringLiteral("</table>");

    if (m_model.hasAsk()) {
        // waits() 每次刷新只算**一遍**（它内部要做上千次 isWinningForm），
        // 算出来的结果直接给后面的振听判断复用（振听状态本身只信服务端）。
        const QStringList waits = m_model.waits();
        if (!waits.isEmpty()) {
            QStringList names;
            for (const QString& t : waits)
                names << mj::tileLabel(t);
            html += lang::t("ui.main.waits_html")
                        .arg(names.join(QStringLiteral(" ")),
                             m_model.furiten(m_model.mySeat())
                                 ? lang::t("ui.main.furiten_suffix")
                                 : QString());
        }
    }
    m_scorePanel->setText(html);
}

void MainWindow::appendChat(const QString& who, const QString& text)
{
    const QString line = lang::t("ui.main.wait_line_html").arg(who.toHtmlEscaped(),
                                                            text.toHtmlEscaped());
    if (m_chatView)
        m_chatView->append(line);
    if (m_waitChat)
        m_waitChat->append(line);
}

void MainWindow::onChatSend()
{
    QLineEdit* edit = qobject_cast<QLineEdit*>(sender());
    if (!edit)
        return;
    const QString text = edit->text().trimmed();
    if (text.isEmpty())
        return;
    QJsonObject cmd;
    cmd.insert(QStringLiteral("cmd"), QStringLiteral("chat"));
    cmd.insert(QStringLiteral("text"), text);
    sendCommand(cmd);
    edit->clear();
}

/**
 * 开局前自选座位（门风 = 座次：0=东/起家）。
 *
 * 服务端的语义是**互换**：目标是真人时两家对调，是机器人时机器人搬过去 ——
 * 所以这个按钮永远不会把别人挤出去，玩家可以放心点。
 */
void MainWindow::takeSeat(int seat)
{
    if (seat < 0 || seat > 3) {
        return;
    }
    QJsonObject cmd;
    cmd.insert(QStringLiteral("cmd"), QStringLiteral("take_seat"));
    cmd.insert(QStringLiteral("seat"), seat);
    sendCommand(cmd);
}

void MainWindow::onLeaveRoom()
{
    QJsonObject cmd;
    cmd.insert(QStringLiteral("cmd"), QStringLiteral("leave_room"));
    sendCommand(cmd);
    showLobby();
}

void MainWindow::sendCommand(const QJsonObject& obj)
{
    // 自检钩子：先让测试看清「到底发了什么」，再照常发送（没连服务端也不影响断言）。
    if (m_cmdTap)
        m_cmdTap(obj);
    if (!m_net.isConnected()) {
        statusBar()->showMessage(lang::t("ui.main.no_server"), 3000);
        if (m_lobby)
            m_lobby->setStatus(lang::t("ui.main.no_server"));
        return;
    }
    m_net.sendCommand(obj);
}

void MainWindow::onConnected()
{
    statusBar()->showMessage(lang::t("ui.main.connected"));
    if (m_lobby) {
        // 关键：连接成功必须重新启用「建房间 / 加入 / 刷新」。
        // setConnected() 是唯一会 setEnabled(true) 的地方，而它此前只在
        // showLobby()（构造时=未连接、断线时）被调用 —— 结果连上以后
        // 建房按钮仍然是灰的，用户点不动。
        m_lobby->setConnected(true);
        m_lobby->setStatus(lang::t("ui.main.connected_handshake"));
    }
    if (!m_pendingHello.isEmpty())
        m_net.sendCommand(m_pendingHello);
}

void MainWindow::onDisconnected()
{
    statusBar()->showMessage(lang::t("ui.main.connection_lost"));
    if (m_lobby)
        m_lobby->setStatus(lang::t("ui.main.connection_lost"));
    QMessageBox::warning(this, lang::t("ui.main.connection_lost_title"),
                         lang::t("ui.main.connection_lost_detail"));
    showLobby();
}

void MainWindow::onNetError(const QString& msg)
{
    // 连接阶段的失败：状态栏放一行摘要，详细信息弹窗（含排查建议）
    const bool connectFailure = msg.contains(lang::t("ui.main.connect_refused"))
            || msg.contains(lang::t("ui.main.connect_failed"))
            || msg.contains(lang::t("ui.main.bad_host"))
            || msg.contains(QStringLiteral("Connection refused"));
    const QString brief = msg.section(QLatin1Char('\n'), 0, 0);
    statusBar()->showMessage(brief, 8000);
    if (m_lobby && m_lobby->isVisible())
        m_lobby->setStatus(brief);
    if (connectFailure) {
        // 避免刷屏：同一条消息只弹一次
        if (m_lastNetError != msg) {
            m_lastNetError = msg;
            QMessageBox::warning(this, lang::t("ui.main.connect_failed_title"), msg);
        }
    } else {
        m_lastNetError.clear();
    }
}

void MainWindow::sendConfirmNextRound()
{
    QJsonObject cmd;
    cmd.insert(QStringLiteral("cmd"), QStringLiteral("confirm"));
    sendCommand(cmd);
}

void MainWindow::closeResultDialog(bool confirm)
{
    if (!m_resultDlg)
        return;
    ResultDialog* dlg = m_resultDlg;
    m_resultDlg = nullptr;
    m_resultOpen = false;
    if (!confirm) {
        // 服务端已推进（新一局/终局）→ 只关窗，**不发 confirm**，
        // 否则这个 confirm 会残留到下一次局间，把 5 秒等待吃掉。
        disconnect(dlg, nullptr, this, nullptr);
    }
    dlg->close();
}

void MainWindow::showResultDialog(const QString& title, const QString& html, const QString& schematic,
                                  bool offerReplay)
{
    // 上一张结算弹窗必须先关掉：否则它留在屏幕上，而且它的 finished 处理器
    // 还会再发一条 confirm（服务端已经推进了 → 那条会残留到下一次局间，把 5 秒等待吃掉）。
    // 用 closeResultDialog(false)：只关窗、**不发 confirm**。
    closeResultDialog(false);
    auto* dlg = new ResultDialog(title, html, schematic, this);
    dlg->setAttribute(Qt::WA_DeleteOnClose);
    m_resultOpen = true;
    m_resultDlg = dlg;
    // 「看本局回放」**只在整场总结算出现**（用户要求：牌局未结束时不给这个按钮）。
    // 不是终局时 m_replayId 即使有值也不下发，按钮保持隐藏。
    if (offerReplay) {
        dlg->enableReplay(m_replayId);
    }
    connect(dlg, &ResultDialog::replayRequested, this,
            [this](const QString& id) { openReplayWindow(id); });
    // 关闭结算弹窗 = 玩家确认进入下一局（局间最多等 5 秒，见 Table.ROUND_CONFIRM_MS）。
    // 倒计时到点由 ResultDialog 自己 accept()，同样走这里 → 不必等玩家点按钮。
    connect(dlg, &QDialog::finished, this, [this, dlg]() {
        m_resultOpen = false;
        if (m_resultDlg == dlg)
            m_resultDlg = nullptr;
        sendConfirmNextRound();
    });
    dlg->show();
}

void MainWindow::applySettings(const Settings& st, const QString& path)
{
    m_settings = st;
    m_settingsPath = path;
    if (m_lobby != nullptr) {
        m_lobby->applySettings(st.host, st.port, st.name);
    }
    // 音效开关/音量随设置立刻生效（设置对话框里试听也是这条路径）
    sound::Player::instance().setVolume(st.sfxVolume);
    sound::Player::instance().setEnabled(st.sfx);
}

void MainWindow::saveCurrentEndpoint()
{
    if (m_settingsPath.isEmpty()) {
        return;
    }
    // 只更新"跟连接有关"的三项；材质包那条路径由设置对话框负责（别在这里覆盖掉）
    m_settings.host = m_host;
    m_settings.port = m_port;
    if (!m_myName.isEmpty() && m_myName != lang::t(QStringLiteral("ui.lobby.default_name"))) {
        m_settings.name = m_myName;
    }
    QString err;
    m_settings.save(m_settingsPath, &err);
}

void MainWindow::openSettings()
{
    SettingsDialog dlg(m_settings, m_settingsPath.isEmpty() ? Settings::defaultPath() : m_settingsPath,
                       this);
    connect(&dlg, &SettingsDialog::applied, this, [this, &dlg]() {
        m_settings = dlg.settings();
        if (m_lobby != nullptr) {
            m_lobby->applySettings(m_settings.host, m_settings.port, m_settings.name);
        }
        // 材质包换过 → 整个牌桌重画（素材缓存已经在对话框里清掉了）
        if (m_table != nullptr) {
            m_table->update();
        }
    });
    dlg.exec();
}

void MainWindow::openReplayWindow(const QString& replayId)
{
    if (m_host.isEmpty() || m_port == 0) {
        // 还没连过任何服务端：回放入口没有目标，直接提示（不静默失败）
        statusBar()->showMessage(lang::t(QStringLiteral("ui.replay.need_connect")), 5000);
        return;
    }
    if (m_replay == nullptr) {
        m_replay = new ReplayWindow(this);
        m_replay->setAttribute(Qt::WA_DeleteOnClose);
        connect(m_replay, &QObject::destroyed, this, [this]() { m_replay = nullptr; });
    }
    m_replay->show();
    m_replay->raise();
    m_replay->activateWindow();
    m_replay->openReplay(m_host, m_port, replayId);
}

void MainWindow::onRiichiModeChanged(bool on)
{
    m_table->setHighlightTiles(on ? m_actions->riichiTiles() : QStringList());
    if (on)
        statusBar()->showMessage(lang::t("ui.main.riichi_prompt"), 5000);
}

void MainWindow::onTileClicked(const QString& tile, int index)
{
    Q_UNUSED(index);
    if (!m_model.hasAsk())
        return;

    if (m_actions->riichiMode()) {
        const QStringList tiles = m_actions->riichiTiles();
        if (!tiles.contains(tile)) {
            statusBar()->showMessage(
                lang::t("ui.main.riichi_bad_tile"), 3000);
            return;
        }
        // ⚠ 必须走 onActionReady：它会先 clearAsk()（按钮删掉、计时停掉、模式复位），
        //   于是连点第二下时 actionCmd() 直接返回空、什么也发不出去。
        //   曾经这里直接 sendCommand，询问栏会一直活到服务端回事件为止 ——
        //   连点两下就发出**两条**立直，第二条落到下一巡被当成那一巡的答复，
        //   玩家没动就被代打（报障：重复点击立直按钮行为异常）。
        onActionReady(m_actions->riichiCmd(tile));
        return;
    }

    if (!m_actions->canDiscard()) {
        statusBar()->showMessage(lang::t("ui.main.cannot_discard"), 3000);
        return;
    }
    // 同理：出牌也必须走这条唯一入口，否则连点手牌会发出两条弃牌。
    //
    // `tsumogiri` 按**点的是哪一格**判定：`tileClicked` 的 index 指向 TableView 的
    // `m_handTiles`，摸牌位**固定是最后一格**（手牌区在前、摸牌单独一格）。
    // 这一格是布局层当时的真实情况，比"牌码是否相等"可靠（见 isTsumogiriDiscard 的说明）。
    const bool tsumogiri = (index == m_model.hand().size()) && !m_model.drawnTile().isEmpty();
    onActionReady(m_actions->discardCmd(tile, tsumogiri));
}

void MainWindow::autoStart(const QString& host, quint16 port, const QString& name, int bots)
{
    m_myName = name;
    m_autoBots = bots;
    m_autoCreate = true;
    m_autoPlay = bots > 0;   // --no-answer 随后会把 m_autoPlay 关掉（见 setAutoAnswer）
    if (m_lobby)
        m_lobby->setStatus(lang::t("ui.main.auto_connecting").arg(host).arg(port));
    // 与大厅「连接」按钮走同一条路径：先备好 hello，连上后由 onConnected 发出
    QJsonObject hello;
    hello.insert(QStringLiteral("cmd"), QStringLiteral("hello"));
    hello.insert(QStringLiteral("name"), name);
    hello.insert(QStringLiteral("ver"), proto::Version);
    m_pendingHello = hello;
    m_net.connectToServer(host, port);
}

void MainWindow::onActionReady(const QJsonObject& action)
{
    // 组装端已经保证「动作一定带 ask_id」，且询问失效/超时时给出空对象 ——
    // 空对象表示这次点击作废（重复点击的第二下就走这里被丢掉）。
    if (action.isEmpty())
        return;
    // 动作一旦提交就立刻停表：否则读秒会一直走到下一次事件才停
    // （副露后尤其明显：要等自己再摸牌才重新计时）。
    // 同时把询问栏清空 —— 这是防止「同一询发出两条动作」的第一道闸。
    m_actions->clearAsk();
    m_model.clearAsk();   // 让 hasAsk() 也说真话，别再拿它当「还能出牌」的判据
    sendCommand(action);
}

void MainWindow::setCommandTapForTest(std::function<void(const QJsonObject&)> tap)
{
    m_cmdTap = std::move(tap);
}

void MainWindow::onAutoFlagsChanged()
{
    m_autoFlags = m_autoBar->flags();
    // 询问正在进行中点动开关 → 立刻按新状态重新决策一次
    // （比如轮到自己时打开「自动摸切」，这一巡就直接打出去，不必等下一巡）。
    if (m_actions->hasAsk())
        applyAuto(m_actions->currentKind(), m_actions->currentAsk());
}

namespace {

/**
 * 待打出的这张牌是不是**刚摸到的那一张**（决定 `discard.tsumogiri`）。
 *
 * <p>为什么必须发这个字段：`discard.tile` 只有一个牌码，服务端拿它无法区分
 * 「摸切」与「手切一张同种牌」—— 手里已有 5m、又摸到 5m 时两者牌码相同，
 * 而赤五（`0m`）与普通五（`5m`）同 kind，更是必然猜不出。猜错的后果是**两端手牌各差一张**：
 * 服务端去动了摸牌位，客户端却按手切扣掉了暗牌，越打越歪（报障：幽灵手牌）。
 *
 * <p>判据取**保守侧**：只有当待打的牌确实是刚摸到的那张、**且暗手里没有同样的牌码**时
 * 才声明摸切。否则一律按手切上报（服务端会在暗手里找同 kind 的副本）。
 * 这样即使两端的"摸到哪张"认知有偏差，也只会退化成一次普通手切，
 * 而不会让服务端的摸牌位被误动。
 */
bool isTsumogiriDiscard(const TableModel& model, const QString& tile)
{
    const QString drawn = model.drawnTile();
    if (drawn.isEmpty())
        return false;
    // 立直后**只有摸切这一种合法出牌**（服务端也这么判），所以直接声明摸切：
    // 此时 `tile` 一定来自服务端的"只能打摸到的那张"选项，不必再比对牌码
    // ——比对反而会在"摸到普通 5m、手里还有赤 0m"这类同 kind 不同码的情况下判错。
    if (model.riichi(model.mySeat()))
        return true;
    if (tile != drawn)
        return false;
    return !model.hand().contains(tile);
}

} // namespace

void MainWindow::applyAuto(const QString& kind, const QJsonObject& ask)
{
    const QJsonObject act = autopolicy::decide(m_autoFlags, ask, kind, m_model.drawnTile());
    if (act.isEmpty())
        return;   // 不满足自动条件（含「有和牌机会但没开自动胡了」）→ 交给玩家
    QTimer::singleShot(kAutoDelayMs, this, [this, act]() {
        const QString type = act.value(QStringLiteral("type")).toString();
        // 组包只能走 ActionBar 这两个入口 —— 它们会自动带上 ask_id，
        // 且在询问失效/超时时返回空对象（见 AGENTS §2.3-9）。
        const QString tile = act.value(QStringLiteral("tile")).toString();
        QJsonObject cmd;
        if (type == QLatin1String("discard")) {
            cmd = m_actions->discardCmd(tile, isTsumogiriDiscard(m_model, tile));
        } else {
            cmd = m_actions->actionCmd(type);
        }
        onActionReady(cmd);
    });
}

void MainWindow::resetAutoFlags()
{
    if (!m_autoBar)
        return;
    // reset() 会发 flagsChanged → onAutoFlagsChanged() 把我们下面这句赋值做掉，
    // 这里再取一次是为了让「没接信号」的场景也一致。
    m_autoBar->reset();
    m_autoFlags = m_autoBar->flags();
}

void MainWindow::onEvent(const QJsonObject& ev)
{
    const QString name = evName(ev);

    // 1) 先写入数据模型
    if (name != QLatin1String("rooms") && name != QLatin1String("room")
        && name != QLatin1String("left_room") && name != QLatin1String("spectate")) {
        m_model.applyEvent(ev);
    }

    // 2) 再处理界面
    if (name == QLatin1String("draw")) {
        // 摸牌音效：只在自己摸到时响（别家摸牌每巡都响会很吵）。
        // 放在"模型已更新"之后：万一 applyEvent 抛异常也不会先出声。
        if (ev.value(QStringLiteral("seat")).toInt(-1) == m_model.mySeat())
            sound::Player::instance().play(QLatin1String(sound::name::Draw), false);
    }
    if (name == QLatin1String("hello_ok")) {
        m_myPid = ev.value(QStringLiteral("pid")).toInt();
        m_myName = ev.value(QStringLiteral("name")).toString(m_myName);
        if (m_lobby)
            m_lobby->setStatus(lang::t("ui.main.handshake_done"));
        QJsonObject cmd;
        cmd.insert(QStringLiteral("cmd"), QStringLiteral("list_rooms"));
        sendCommand(cmd);
        if (m_autoCreate && !m_autoRoomSent) {
            m_autoRoomSent = true;
            QJsonObject rules;
            rules.insert(QStringLiteral("length"), QStringLiteral("tonpuu"));
            QJsonObject create;
            create.insert(QStringLiteral("cmd"), QStringLiteral("create_room"));
            create.insert(QStringLiteral("name"), lang::t("ui.main.demo_room"));
            create.insert(QStringLiteral("rules"), rules);
            create.insert(QStringLiteral("fill_bots"), m_autoBots);
            sendCommand(create);
        }
    } else if (name == QLatin1String("room_joined")) {
        if (m_autoCreate) {
            QTimer::singleShot(300, this, [this]() {
                QJsonObject ready;
                ready.insert(QStringLiteral("cmd"), QStringLiteral("ready"));
                ready.insert(QStringLiteral("ready"), true);
                sendCommand(ready);
            });
        }
    } else if (name == QLatin1String("rooms")) {
        if (m_lobby)
            m_lobby->setRooms(ev.value(QStringLiteral("rooms")).toArray());
    } else if (name == QLatin1String("room")) {
        updateWaitingRoom(ev);
        if (m_lobby && m_lobby->isVisible())
            m_lobby->hide();
        m_stack->setCurrentWidget(m_waitPage);
        setWindowTitle(lang::t("ui.main.room_window_title")
                           .arg(ev.value(QStringLiteral("id")).toString()));
    } else if (name == QLatin1String("left_room")) {
        showLobby();
    } else if (name == QLatin1String("spectate")) {
        if (m_lobby && m_lobby->isVisible())
            m_lobby->hide();
        m_stack->setCurrentWidget(m_tablePage);
        m_table->showToast(lang::t("ui.main.spectating"), QColor(0x9F, 0xE8, 0xC4));
    } else if (name == QLatin1String("game_start")) {
        m_room = QJsonObject();
        resetAutoFlags();   // 新的一场：自动开关一律从关闭开始
        // 记下这一场的回放 ID：结算界面的「看本局回放」要用它
        m_replayId = ev.value(QStringLiteral("replay_id")).toString();
        if (m_lobby && m_lobby->isVisible())
            m_lobby->hide();
        m_stack->setCurrentWidget(m_tablePage);
        m_chatView->clear();
        m_table->showToast(lang::t("ui.main.game_started"), QColor(0x9F, 0xE8, 0xC4));
    } else if (name == QLatin1String("round_start")) {
        // 新一局已经开始 → 上一局的结算弹窗必须关掉。
        // 这里**不算玩家确认**（服务端已经开新局了，再发 confirm 会残留到下一次局间，
        // 把下一局的 5 秒等待直接吃掉）。
        closeResultDialog(false);
        // 自动开关**每小局开始也复位一次**（用户要求：局间结算期间点开的自动，
        // 不许带进新的一局）—— 与 round_end 那一次合起来是"一小局两次"。
        resetAutoFlags();
        if (m_stack->currentWidget() != m_tablePage)
            m_stack->setCurrentWidget(m_tablePage);
        m_actions->clearAsk();
        m_table->setHighlightTiles(QStringList());
        m_table->showToast(m_model.roundText(), QColor(0x9F, 0xE8, 0xC4));
    } else if (name == QLatin1String("ask")) {
        const proto::AskInfo info = proto::parseAsk(ev);
        if (info.valid && (info.seat < 0 || info.seat == m_model.mySeat())) {
            m_actions->setAsk(ev);
            // 服务端会告诉客户端「和了形但没有和牌按钮」的原因（无役 / 振听）
            const QString winNote = ev.value(QStringLiteral("win_note")).toString();
            QStringList notes;
            if (winNote == QLatin1String("furiten"))
                notes << lang::t("ui.main.win_note_furiten");
            else if (winNote == QLatin1String("no_yaku"))
                notes << lang::t("ui.main.win_note_no_yaku");
            // 思考时间：本巡基本时长 + 剩余总额外时长
            if (ev.contains(QStringLiteral("base_ms"))) {
                const int baseSec = ev.value(QStringLiteral("base_ms")).toInt() / 1000;
                const int bankSec = ev.value(QStringLiteral("bank_ms")).toInt() / 1000;
                if (baseSec > 0) {
                    notes << (bankSec > 0
                                      ? lang::t("ui.main.clock_both").arg(baseSec).arg(bankSec)
                                      : lang::t("ui.main.clock_base").arg(baseSec));
                }
            }
            m_actions->setNote(notes.join(QStringLiteral(" · ")));
            if (info.kind == QLatin1String("turn"))
                m_table->setStatusText(lang::t("ui.action.your_turn"));
            else if (info.kind == QLatin1String("claim"))
                m_table->setStatusText(lang::t("ui.action.can_claim"));
            else if (info.kind == QLatin1String("chankan"))
                m_table->setStatusText(lang::t("ui.action.chankan"));
            // 三个自动开关（牌桌外那排按钮）替玩家应答：能胡就先胡，其次是摸切/不叫。
            // 「有和牌机会时不许替他动」这条判断在 autopolicy::decide() 里。
            applyAuto(info.kind, ev);
            if (m_autoPlay) {
                // 留出 1.2 秒让按钮可见，再自动应答。
                // 决策读**这条 ask 的 options**，但组包必须走 ActionBar 的入口：
                //   ① 入口会校验并只带上合法的 ask_id（不合法就当没有 id，见 Protocol/ActionBar）；
                //   ② 入口会确认 type 属于**当前**询问的 options —— 这 1.2 秒里询问可能已经
                //      换成另一条，旧动作绝不能配上新 ask_id 发出去（AGENTS §2.2）。
                QTimer::singleShot(1200, this, [this, ev, info]() {
                    const QJsonArray options = ev.value(QStringLiteral("options")).toArray();
                    auto pick = [&options](const QString& type) -> QJsonObject {
                        for (const QJsonValue& v : options) {
                            const QJsonObject o = v.toObject();
                            if (o.value(QStringLiteral("type")).toString() == type)
                                return o;
                        }
                        return {};
                    };
                    QString type;
                    QString tile;
                    if (info.kind == QLatin1String("turn") && !pick(QStringLiteral("tsumo")).isEmpty()) {
                        type = QStringLiteral("tsumo");
                    } else if (!pick(QStringLiteral("ron")).isEmpty()) {
                        type = QStringLiteral("ron");
                    } else {
                        const QJsonArray tiles = pick(QStringLiteral("discard"))
                                                     .value(QStringLiteral("tiles"))
                                                     .toArray();
                        if (!tiles.isEmpty()) {
                            type = QStringLiteral("discard");
                            tile = tiles.at(0).toString();
                        } else {
                            type = QStringLiteral("pass");
                        }
                    }
                    const QJsonObject cmd = (type == QLatin1String("discard"))
                            ? m_actions->discardCmd(tile, isTsumogiriDiscard(m_model, tile))
                            : m_actions->actionCmd(type);
                    onActionReady(cmd);
                });
            }
        }
    } else if (name == QLatin1String("ask_cancel")) {
        const int seat = ev.value(QStringLiteral("seat")).toInt(-1);
        if (seat < 0 || seat == m_model.mySeat()) {
            m_actions->clearAsk();
            m_table->setHighlightTiles(QStringList());
            m_table->setStatusText(QString());
        }
    } else if (name == QLatin1String("discard")) {
        const int seat = ev.value(QStringLiteral("seat")).toInt();
        if (seat == m_model.mySeat()) {
            m_actions->clearAsk();
            m_table->setStatusText(QString());
            m_table->setHighlightTiles(QStringList());
        }
        m_table->showToast(lang::t("ui.main.log_discard")
                               .arg(m_model.playerName(seat), mj::tileLabel(
                                        ev.value(QStringLiteral("tile")).toString())),
                           QColor(0xD8, 0xE8, 0xE0));
    } else if (name == QLatin1String("meld")) {
        const int seat = ev.value(QStringLiteral("seat")).toInt();
        if (seat == m_model.mySeat())
            m_actions->clearAsk();
        m_table->showToast(QStringLiteral("%1 %2")
                               .arg(m_model.playerName(seat),
                                    ev.value(QStringLiteral("kind")).toString()),
                           QColor(0x9F, 0xC8, 0xE8));
        // 鸣牌音效：按 kind 分开（吃/碰/杠三种声音不同 —— 听得出发生了什么）。
        // 服务端发的是 ASCII 码（chi/pon/daiminkan/ankan/kakan），所以这里能直接判。
        const QString kind = ev.value(QStringLiteral("kind")).toString();
        const char* sfx = sound::name::Pon;
        if (kind == QLatin1String("chi"))
            sfx = sound::name::Chi;
        else if (kind == QLatin1String("daiminkan") || kind == QLatin1String("ankan")
                 || kind == QLatin1String("kakan"))
            sfx = sound::name::Kan;
        sound::Player::instance().play(QLatin1String(sfx));
    } else if (name == QLatin1String("riichi")) {
        const int seat = ev.value(QStringLiteral("seat")).toInt();
        m_table->showToast(lang::t("ui.main.log_riichi").arg(m_model.playerName(seat)),
                           QColor(0xFF, 0xD2, 0x4A));
        sound::Player::instance().play(QLatin1String(sound::name::Riichi));
        // 立直之后**只能摸切**（规则如此），所以轮到自己时自动替玩家打出摸到的牌
        // （用户要求：「立直后应该自动开启自动摸切」）。只对自己那一张立直生效。
        if (seat == m_model.mySeat())
            m_autoBar->setAutoTsumogiri(true);
    } else if (name == QLatin1String("dora_reveal")) {
        m_table->showToast(lang::t("ui.main.log_new_dora"), QColor(0xFF, 0xD2, 0x4A));
        sound::Player::instance().play(QLatin1String(sound::name::Notify));
    } else if (name == QLatin1String("agari")) {
        m_actions->clearAsk();
        m_table->setStatusText(QString());
        m_table->showToast(lang::t("ui.main.log_agari")
                               .arg(m_model.playerName(ev.value(QStringLiteral("winner")).toInt())),
                           QColor(0xFF, 0xD2, 0x4A));
        // 自摸与荣和用不同音效（听感上立刻分得清）；`from < 0` 才是自摸。
        const bool tsumo = ev.value(QStringLiteral("from")).toInt(-1) < 0;
        sound::Player::instance().play(QLatin1String(tsumo ? sound::name::Tsumo
                                                          : sound::name::Ron));
        showResultDialog(lang::t("ui.result.title_agari"),
                         ResultDialog::agariHtml(ev, &m_model),
                         ResultDialog::schematicOf(ev, &m_model, true));
    } else if (name == QLatin1String("ryuukyoku")) {
        m_actions->clearAsk();
        m_table->setStatusText(QString());
        sound::Player::instance().play(QLatin1String(sound::name::Notify));
        showResultDialog(lang::t("ui.result.title_ryuukyoku"),
                         ResultDialog::ryuukyokuHtml(ev, &m_model));
    } else if (name == QLatin1String("round_end")) {
        m_actions->clearAsk();
        m_table->setHighlightTiles(QStringList());
        // 每小局结束：三个自动开关立刻全部回到关闭状态（用户要求：一小局两次，
        // 另一次在 round_start —— 见 §6「三个自动开关」）。
        resetAutoFlags();
        const QJsonObject next = ev.value(QStringLiteral("next")).toObject();
        if (!next.isEmpty()) {
            m_table->showToast(lang::t("ui.main.next_round")
                                   .arg(next.value(QStringLiteral("bakaze")).toString())
                                   .arg(next.value(QStringLiteral("kyoku")).toInt()),
                               QColor(0x9F, 0xE8, 0xC4));
        }
    } else if (name == QLatin1String("round_wait")) {
        // 小局之间的间隔：服务端已经先停顿了 DEFAULT_ROUND_DELAY_MS（默认 10 秒，
        // 结算弹窗就停在那段时间里、文字依次浮现），然后**等满这次声明的时长（或所有人确认）**
        // 再开下一局。所以这里的倒计时是「本局结算还剩多久」，不是「下一局已开始后的等待」。
        // 结算弹窗开着 → 倒计时到点自动关闭它（关闭即确认），不必等玩家点按钮；
        // 已经关掉了 → 立刻替他确认，免得明明看完了还要干等。
        m_actions->clearAsk();
        const int ms = ev.value(QStringLiteral("ms")).toInt(5000);
        const int sec = qMax(1, ms / 1000);
        m_table->setStatusText(lang::t("ui.main.round_end_hint").arg(sec));
        if (m_resultDlg) {
            m_resultDlg->startCountdown(ms);
        } else {
            sendConfirmNextRound();
        }
    } else if (name == QLatin1String("game_end")) {
        closeResultDialog(false);
        // 终局报文里也带 replay_id（game_start 没收到时兜底）
        const QString endId = ev.value(QStringLiteral("replay_id")).toString();
        if (!endId.isEmpty()) {
            m_replayId = endId;
        }
        showResultDialog(lang::t("ui.result.title_game_end"),
                         ResultDialog::gameEndHtml(ev, &m_model), QString(),
                         true);   // 整场总结算：这里是「看本局回放」唯一的出口
        m_stack->setCurrentWidget(m_waitPage);
        QJsonObject cmd;
        cmd.insert(QStringLiteral("cmd"), QStringLiteral("list_rooms"));
        sendCommand(cmd);
    } else if (name == QLatin1String("error")) {
        // 服务端只发 ASCII 码：`code` 是词汇码，`arg` 是可变参数（如 unknown_cmd 的命令名）。
        // 中文文案在语言文件里（`error.<code>`）；认不出的码原样显示，方便发现协议新增码。
        const QString code = ev.value(QStringLiteral("code")).toString();
        // 老服务端兼容：只发 `msg`（中文原文）时直接用原文。
        const QString detail = code.isEmpty()
                ? ev.value(QStringLiteral("msg")).toString()
                : lang::code(QStringLiteral("error."), code,
                             ev.value(QStringLiteral("arg")).toString());
        statusBar()->showMessage(lang::t("ui.main.server_error").arg(detail), 6000);
        if (m_lobby && m_lobby->isVisible())
            m_lobby->setStatus(lang::t("ui.main.error_prefix").arg(detail));
        // ⚠ 这里**不要**在收到 error 后去 clearAsk()：服务端并没有重新下发 ask，
        //   清掉询问栏只会让玩家彻底点不动（旧代码曾等一个服务端从不发送的
        //   `illegal_action`，`tools/check-i18n.mjs` 会把这个失效码报出来）。
    }

    updateScorePanel();
}
