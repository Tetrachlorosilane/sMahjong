#include "ReplayWindow.h"

#include <QComboBox>
#include <QDir>
#include <QFileDialog>
#include <QDateTime>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSpinBox>
#include <QSplitter>
#include <QTimer>
#include <QVBoxLayout>

#include "../i18n/Lang.h"
#include "../model/ReplayModel.h"
#include "../model/TableModel.h"
#include "../net/NetClient.h"
#include "ResultDialog.h"
#include "model/TenhouLog.h"
#include "TableView.h"
#include "WallView.h"

namespace {
// 一页拉多少条：与服务端的单次上限（2000）一致 —— 少一次往返，也不至于把一页做得过大
constexpr int kPageSize = 2000;
// 播放速度：每条操作停留多久（毫秒）
constexpr int kPlayIntervalMs = 700;
}   // namespace

ReplayWindow::ReplayWindow(QWidget* parent)
    : QDialog(parent)
{
    setWindowTitle(lang::t(QStringLiteral("ui.replay.title")));
    m_replay = new ReplayModel();
    m_model = new TableModel(this);
    buildUi();
    m_timer = new QTimer(this);
    connect(m_timer, &QTimer::timeout, this, &ReplayWindow::onTick);
    resize(1080, 700);
}

ReplayWindow::~ReplayWindow()
{
    delete m_replay;
}

void ReplayWindow::buildUi()
{
    auto* root = new QVBoxLayout(this);

    // ---- 顶部：状态 + 打开 ----
    auto* top = new QHBoxLayout();
    m_status = new QLabel(lang::t(QStringLiteral("ui.replay.connecting")), this);
    m_status->setWordWrap(true);
    top->addWidget(m_status, 1);
    auto* openLabel = new QLabel(lang::t(QStringLiteral("ui.replay.open_id")), this);
    top->addWidget(openLabel);
    m_openEdit = new QLineEdit(this);
    m_openEdit->setFixedWidth(140);
    top->addWidget(m_openEdit);
    auto* openBtn = new QPushButton(lang::t(QStringLiteral("ui.replay.open")), this);
    connect(openBtn, &QPushButton::clicked, this, [this]() {
        const QString id = m_openEdit->text().trimmed().toUpper();
        if (!id.isEmpty()) {
            m_pendingId = id;
            if (m_net != nullptr && m_net->isConnected()) {
                m_replay->reset();
                m_ops->clear();
                m_opsRound = -1;
                requestChunk(0);
            }
        }
    });
    top->addWidget(openBtn);
    root->addLayout(top);

    // ---- 导航条（**两行**：控件太多，挤一行会把「视角」下拉压成一个字）----
    auto* nav = new QHBoxLayout();
    auto* nav2 = new QHBoxLayout();
    auto mkBtn = [&](const char* key, const char* slotKey) {
        auto* b = new QPushButton(lang::t(QString::fromLatin1(key)), this);
        b->setToolTip(lang::t(QString::fromLatin1(slotKey)));
        nav->addWidget(b);
        return b;
    };
    m_prevRound = mkBtn("ui.replay.prev_round", "ui.replay.prev_round_hint");
    m_prevTurn = mkBtn("ui.replay.prev_turn", "ui.replay.prev_turn_hint");
    m_prevOp = mkBtn("ui.replay.prev_op", "ui.replay.prev_op_hint");
    m_nextOp = mkBtn("ui.replay.next_op", "ui.replay.next_op_hint");
    m_nextTurn = mkBtn("ui.replay.next_turn", "ui.replay.next_turn_hint");
    m_nextRound = mkBtn("ui.replay.next_round", "ui.replay.next_round_hint");
    connect(m_prevOp, &QPushButton::clicked, this, &ReplayWindow::onPrevOp);
    connect(m_nextOp, &QPushButton::clicked, this, &ReplayWindow::onNextOp);
    connect(m_prevTurn, &QPushButton::clicked, this, &ReplayWindow::onPrevTurn);
    connect(m_nextTurn, &QPushButton::clicked, this, &ReplayWindow::onNextTurn);
    connect(m_prevRound, &QPushButton::clicked, this, &ReplayWindow::onPrevRound);
    connect(m_nextRound, &QPushButton::clicked, this, &ReplayWindow::onNextRound);

    m_play = new QPushButton(lang::t(QStringLiteral("ui.replay.play")), this);
    connect(m_play, &QPushButton::clicked, this, &ReplayWindow::onTogglePlay);
    nav->addWidget(m_play);

    nav->addWidget(new QLabel(lang::t(QStringLiteral("ui.replay.jump_turn")), this));
    m_turnSpin = new QSpinBox(this);
    m_turnSpin->setRange(1, 1);
    nav->addWidget(m_turnSpin);
    m_jump = new QPushButton(lang::t(QStringLiteral("ui.replay.jump")), this);
    connect(m_jump, &QPushButton::clicked, this, &ReplayWindow::onJumpTurn);
    nav->addWidget(m_jump);
    nav->addStretch(1);

    // 第二行：小局 / 视角 / 三个开关
    nav2->addWidget(new QLabel(lang::t(QStringLiteral("ui.replay.round")), this));
    m_roundBox = new QComboBox(this);
    m_roundBox->setMinimumWidth(130);
    connect(m_roundBox, QOverload<int>::of(&QComboBox::activated), this, &ReplayWindow::onPickRound);
    nav2->addWidget(m_roundBox);

    nav2->addWidget(new QLabel(lang::t(QStringLiteral("ui.replay.view_seat")), this));
    m_seatBox = new QComboBox(this);
    m_seatBox->setMinimumWidth(120);
    for (int s = 0; s < 4; ++s) {
        m_seatBox->addItem(QStringLiteral("%1").arg(s + 1), s);
    }
    connect(m_seatBox, QOverload<int>::of(&QComboBox::activated), this, &ReplayWindow::onSeatChanged);
    nav2->addWidget(m_seatBox);

    // 需求：一个切换键，切换是否显示非自家手牌（上帝视角）。
    m_godBtn = new QPushButton(lang::t(QStringLiteral("ui.replay.god_hands")), this);
    m_godBtn->setCheckable(true);
    m_godBtn->setToolTip(lang::t(QStringLiteral("ui.replay.god_hands_hint")));
    connect(m_godBtn, &QPushButton::toggled, this, &ReplayWindow::onGodToggled);
    nav2->addWidget(m_godBtn);

    m_resultBtn = new QPushButton(lang::t(QStringLiteral("ui.replay.round_result")), this);
    m_resultBtn->setToolTip(lang::t(QStringLiteral("ui.replay.round_result_hint")));
    connect(m_resultBtn, &QPushButton::clicked, this, &ReplayWindow::onRoundResult);
    nav2->addWidget(m_resultBtn);

    m_wallBtn = new QPushButton(lang::t(QStringLiteral("ui.replay.wall")), this);
    connect(m_wallBtn, &QPushButton::clicked, this, &ReplayWindow::showWall);
    nav2->addWidget(m_wallBtn);

    // 导出天鳳牌譜（tenhou.net/6 的 #json= 链接，见 model/TenhouLog）
    m_exportBtn = new QPushButton(lang::t(QStringLiteral("ui.replay.export")), this);
    m_exportBtn->setToolTip(lang::t(QStringLiteral("ui.replay.export_hint")));
    connect(m_exportBtn, &QPushButton::clicked, this, &ReplayWindow::onExportTenhou);
    nav2->addWidget(m_exportBtn);

    nav2->addStretch(1);

    root->addLayout(nav);
    root->addLayout(nav2);

    // ---- 主体：左（牌桌）/ 右（操作列表 + 聊天 + 记录列表）----
    auto* split = new QSplitter(Qt::Horizontal, this);
    m_view = new TableView(split);
    m_view->setModel(m_model);
    // 点名牌 = 切到那家的视角（与下拉框同一条路径）
    connect(m_view, &TableView::seatClicked, this, &ReplayWindow::onSeatClicked);
    split->addWidget(m_view);

    auto* right = new QSplitter(Qt::Vertical, split);
    // 操作列表标题：写明"这里只装当前小局的记录"（需求：按小局切割）
    m_opsTitle = new QLabel(right);
    m_opsTitle->setStyleSheet(QStringLiteral("color:#9AA3B2;padding:2px 4px;"));
    right->addWidget(m_opsTitle);
    m_ops = new QListWidget(right);
    connect(m_ops, &QListWidget::itemClicked, this, &ReplayWindow::onListClicked);
    right->addWidget(m_ops);
    m_chat = new QPlainTextEdit(right);
    m_chat->setReadOnly(true);
    right->addWidget(m_chat);
    m_list = new QListWidget(right);
    connect(m_list, &QListWidget::itemDoubleClicked, this, &ReplayWindow::onListDoubleClicked);
    right->addWidget(m_list);
    // 操作列表是主角：多给点高度（聊天默认空着，用不了那么多）。
    // ⚠ 这里有 **4 个**子控件（标题标签 + 操作列表 + 聊天 + 记录列表），setSizes 必须给 4 个数。
    right->setSizes({26, 440, 190, 170});
    split->addWidget(right);
    split->setStretchFactor(0, 3);
    split->setStretchFactor(1, 2);
    root->addWidget(split, 1);

    m_pos = new QLabel(this);
    root->addWidget(m_pos);

    setEnabledAll(false);
}

void ReplayWindow::setEnabledAll(bool on)
{
    for (QPushButton* b : {m_prevOp, m_nextOp, m_prevTurn, m_nextTurn, m_prevRound, m_nextRound,
                           m_play, m_jump, m_wallBtn, m_godBtn, m_resultBtn}) {
        if (b != nullptr) {
            b->setEnabled(on);
        }
    }
    m_turnSpin->setEnabled(on);
    m_roundBox->setEnabled(on);
    m_seatBox->setEnabled(on);
}

void ReplayWindow::openReplay(const QString& host, quint16 port, const QString& replayId)
{
    m_host = host;
    m_port = port;
    m_pendingId = replayId.trimmed().toUpper();
    m_openEdit->setText(m_pendingId);
    if (m_net == nullptr) {
        m_net = new NetClient(this);
        connect(m_net, &NetClient::connected, this, &ReplayWindow::onConnected);
        connect(m_net, &NetClient::disconnected, this, &ReplayWindow::onDisconnected);
        connect(m_net, &NetClient::eventReceived, this, &ReplayWindow::onEvent);
        connect(m_net, &NetClient::errorOccurred, this, &ReplayWindow::onError);
    }
    setStatus(lang::t(QStringLiteral("ui.replay.connecting")));
    m_net->connectToServer(host, port);
}

void ReplayWindow::onConnected()
{
    m_net->sendCommand(QJsonObject{{QStringLiteral("cmd"), QStringLiteral("hello")},
                                   {QStringLiteral("name"), lang::t(QStringLiteral("ui.replay.viewer"))}});
}

void ReplayWindow::onDisconnected()
{
    setStatus(lang::t(QStringLiteral("ui.replay.disconnected")));
}

void ReplayWindow::onError(const QString& msg)
{
    setStatus(msg);
}

void ReplayWindow::setStatus(const QString& text)
{
    m_status->setText(text);
}

void ReplayWindow::onEvent(const QJsonObject& ev)
{
    const QString name = ev.value(QStringLiteral("ev")).toString();
    if (name == QLatin1String("hello_ok")) {
        m_net->sendCommand(QJsonObject{{QStringLiteral("cmd"), QStringLiteral("replay_list")},
                                       {QStringLiteral("limit"), 50}});
        if (!m_pendingId.isEmpty()) {
            m_replay->reset();
            m_ops->clear();
            m_opsRound = -1;
            requestChunk(0);
        }
        return;
    }
    if (name == QLatin1String("replay_list")) {
        m_list->clear();
        for (const QJsonValue& v : ev.value(QStringLiteral("items")).toArray()) {
            const QJsonObject o = v.toObject();
            const QString id = o.value(QStringLiteral("id")).toString();
            const QString ts = QDateTime::fromMSecsSinceEpoch(
                                       static_cast<qint64>(o.value(QStringLiteral("created")).toDouble()))
                                       .toString(QStringLiteral("MM-dd HH:mm"));
            QStringList names;
            for (const QJsonValue& n : o.value(QStringLiteral("names")).toArray()) {
                names << n.toString();
            }
            auto* item = new QListWidgetItem(
                    lang::t(QStringLiteral("ui.replay.list_item"))
                            .arg(id, ts, names.join(QStringLiteral("/")))
                            .arg(o.value(QStringLiteral("rounds")).toInt())
                            .arg(o.value(QStringLiteral("entries")).toInt()));
            item->setData(Qt::UserRole, id);
            m_list->addItem(item);
        }
        if (m_pendingId.isEmpty() && m_list->count() > 0) {
            setStatus(lang::t(QStringLiteral("ui.replay.pick")).arg(m_list->count()));
        }
        return;
    }
    if (name == QLatin1String("replay_get")) {
        const QString id = ev.value(QStringLiteral("id")).toString();
        if (!m_pendingId.isEmpty() && id != m_pendingId) {
            return;
        }
        if (m_replay->replayId() != id) {
            m_replay->reset();
            // 换一场记录：上一场的上帝手牌必须清掉（`TableModel::reset()` **故意不清**它，
            // 因为回放每次跳转都要靠"上一帧那份"算出牌动画的起点）。见 TableModel 的注释。
            m_model->clearGodHands();
            if (!m_replay->setMeta(ev.value(QStringLiteral("meta")).toObject())) {
                setStatus(lang::t(QStringLiteral("ui.replay.bad")));
                return;
            }
            m_currentId = id;
            m_ops->clear();
            m_opsRound = -1;
            setStatus(lang::t(QStringLiteral("ui.replay.loading")).arg(m_replay->total()));
        }
        const QJsonArray entries = ev.value(QStringLiteral("entries")).toArray();
        m_replay->addEntries(entries);
        const int next = ev.value(QStringLiteral("from")).toInt() + entries.size();
        if (entries.isEmpty() || m_replay->loaded() >= m_replay->total()) {
            finishLoad();
        } else {
            requestChunk(next);
        }
        return;
    }
    if (name == QLatin1String("error")) {
        const QString code = ev.value(QStringLiteral("code")).toString();
        setStatus(lang::t(QStringLiteral("ui.replay.not_found")).arg(code));
        return;
    }
}

void ReplayWindow::requestChunk(int from)
{
    if (m_pendingId.isEmpty()) {
        return;
    }
    m_loading = true;
    m_net->sendCommand(QJsonObject{{QStringLiteral("cmd"), QStringLiteral("replay_get")},
                                   {QStringLiteral("id"), m_pendingId},
                                   {QStringLiteral("from"), from},
                                   {QStringLiteral("count"), kPageSize}});
}

void ReplayWindow::finishLoad()
{
    m_loading = false;
    m_replay->build();
    m_roundBox->clear();
    for (int i = 0; i < m_replay->roundCount(); ++i) {
        m_roundBox->addItem(m_replay->roundText(i));
    }
    m_opsRound = -1;          // 强制按小局重建操作列表
    m_ops->clear();
    // 视角下拉改用**玩家名**（1/2/3/4 看不出谁是谁；点名牌切视角也靠这个同步）
    m_seatBox->clear();
    for (int s = 0; s < 4; ++s) {
        m_seatBox->addItem(QStringLiteral("%1 (%2)").arg(m_replay->playerName(s)).arg(s + 1), s);
    }
    setEnabledAll(true);
    setStatus(lang::t(QStringLiteral("ui.replay.loaded"))
                      .arg(m_replay->replayId())
                      .arg(m_replay->roundCount())
                      .arg(m_replay->stepCount()));
    const int open0 = m_replay->roundOpenEnd(0);
    seek(open0 >= 0 ? open0 : 0);
    emit replayLoaded();
}

void ReplayWindow::loadForTest()
{
    // 与 `finishLoad()` 的收尾完全一致（除了它还要处理分页/状态栏）
    m_loading = false;
    m_replay->build();
    m_roundBox->clear();
    for (int i = 0; i < m_replay->roundCount(); ++i) {
        m_roundBox->addItem(m_replay->roundText(i));
    }
    m_opsRound = -1;
    m_ops->clear();
    m_seatBox->clear();
    for (int s = 0; s < 4; ++s) {
        m_seatBox->addItem(QStringLiteral("%1 (%2)").arg(m_replay->playerName(s)).arg(s + 1), s);
    }
    setEnabledAll(true);
    const int open0 = m_replay->roundOpenEnd(0);
    seek(open0 >= 0 ? open0 : 0);
}

void ReplayWindow::nextOpForTest()
{
    onNextOp();   // 与点「下一步」按钮**同一个槽**，带动画
}

void ReplayWindow::setGodMode(bool on)
{
    if (m_godBtn != nullptr) {
        m_godBtn->setChecked(on);   // toggled → onGodToggled → updateGodView
    } else {
        m_godOn = on;
        updateGodView();
    }
}

void ReplayWindow::openRoundResult()
{
    showRoundResult(false);
}

void ReplayWindow::seekStep(int step)
{
    if (m_replay == nullptr || m_replay->total() == 0) {
        return;
    }
    // 钳到 [0, 最后一步]：`--step 99999` 就是"跳到末尾"（截图脚本按这个用）
    const int s = qBound(0, step, qMax(0, m_replay->stepCount() - 1));
    const int at = m_replay->stepEntry(s);
    seek(at >= 0 ? at : m_cursor);
}

void ReplayWindow::seek(int index)
{
    replayTo(index, false);
}

void ReplayWindow::seekAnimated(int index)
{
    replayTo(index, true);
}

void ReplayWindow::replayTo(int index, bool animate)
{
    if (m_replay->total() == 0) {
        return;
    }
    const int n = m_replay->entries().size();
    const int target = qBound(0, index, qMax(0, n - 1));
    // 「前进一个操作」的**唯一**判据：目标恰好是当前步的下一步。
    // ⚠ 少了这个判据，走到末尾时 `nextStepEntry()` 会返回当前 entry，
    //    于是"整个小局都算最后一步" → 一次点击把整局的弃牌动画全播一遍（就是那个渲染 bug）。
    const bool forward = animate && target > m_cursor
                         && target == m_replay->nextStepEntry(m_cursor);
    m_cursor = target;
    const int round = m_replay->roundOf(m_cursor);
    const int start = m_replay->roundStart(round);
    // 从**本小局的第一条**重放：一个小局最多几百条，够快，且不必给每种事件写反向操作。
    // 重放本身**一律静默**，只有"落在最后一步里"的事件才允许播动画 —— 否则每跳一次
    // 就把前几巡的弃牌动画重打一遍（需求点名的渲染 bug）。
    const int finalStep = m_replay->stepOfEntry(m_cursor);
    m_model->reset();
    m_model->setMySeat(m_viewSeat);
    for (int i = qMax(0, start); i <= m_cursor; ++i) {
        const ReplayEntry& e = m_replay->entries().at(i);
        // 私有事件（draw / ask / round_start）只喂给"视角那一家"，
        // 这样 TableView 画出来的画面与那一家的实时对局一致
        if (e.to >= 0 && e.to != m_viewSeat) {
            continue;
        }
        m_model->setSilent(!(forward && m_replay->stepOfEntry(i) == finalStep));
        m_model->applyEvent(e.body);
    }
    m_model->setSilent(true);   // 复原：之后的任何重建都不播动画
    refreshUi();
}

void ReplayWindow::rebuildOps(int round)
{
    m_ops->clear();
    m_opsRound = round;
    const int start = m_replay->roundStart(round);
    if (start < 0) {
        m_opsTitle->setText(QString());
        return;
    }
    const int stop = (round + 1 < m_replay->roundCount()) ? m_replay->roundStart(round + 1)
                                                          : m_replay->entries().size();
    for (int i = start; i < stop; ++i) {
        // 一步一个代表 entry（配牌/摸牌各算一步），与服务端的下发粒度对齐
        if (m_replay->stepEntry(m_replay->stepOfEntry(i)) != i) {
            continue;
        }
        auto* item = new QListWidgetItem(
                QStringLiteral("%1. %2")
                        .arg(m_replay->stepOfEntry(i) + 1)
                        .arg(m_replay->describe(i)));
        item->setData(Qt::UserRole, m_replay->stepOfEntry(i));
        m_ops->addItem(item);
    }
    m_opsTitle->setText(lang::t(QStringLiteral("ui.replay.ops_title"))
                                .arg(m_replay->roundText(round))
                                .arg(m_ops->count()));
}

void ReplayWindow::updateGodView()
{
    // 「显示他家手牌」只决定**画不画牌面**；真实暗牌**始终**交给模型 ——
    //   ① 手牌行的布局（几格、摸牌槽在哪）要按真实张数算；
    //   ② 出牌动画的起点要落在"这张牌真正待着的那一格"上。
    // 实时对局把别家手切动画随机化是对的（不泄露手牌顺序），回放没有这个顾虑、且要求动画正确。
    m_view->setGodVisible(m_godOn);

    const ReplayModel::GodState& god = m_replay->godState(m_cursor);
    QVector<QStringList> hands;
    QStringList drawn;
    for (int s = 0; s < 4; ++s) {
        QStringList h = god.hands[s];
        // `god.hands` 含刚摸到的那张；牌桌要求它单独占一格，所以这里要摘出来。
        // ⚠ 只摘**一张**：同一牌码可能有两张（比如摸到 5m 而手里本来也有 5m），
        //   `removeOne` 正好只去一张。
        if (!god.drawn[s].isEmpty()) {
            h.removeOne(god.drawn[s]);
        }
        hands << h;
        drawn << god.drawn[s];
    }
    m_view->setGodHands(hands, drawn);
}

void ReplayWindow::refreshUi()
{
    const int step = m_replay->stepOfEntry(m_cursor);
    const int steps = qMax(1, m_replay->stepCount());
    const int round = m_replay->roundOf(m_cursor);
    m_pos->setText(lang::t(QStringLiteral("ui.replay.position"))
                           .arg(step + 1)
                           .arg(steps)
                           .arg(m_replay->roundText(round))
                           .arg(m_replay->turnOf(m_cursor)));
    if (m_roundBox->currentIndex() != round) {
        const QSignalBlocker block(m_roundBox);
        m_roundBox->setCurrentIndex(round);
    }
    const int maxTurn = m_replay->maxTurn(round);
    m_turnSpin->setRange(1, qMax(1, maxTurn));
    m_turnSpin->setValue(m_replay->turnOf(m_cursor));
    // 操作列表按小局切割：换小局才重建（需求：每小局只展示对应的记录）
    if (m_opsRound != round) {
        rebuildOps(round);
    }
    // 当前行 = 本小局内的第几条（列表下标与本小局步骤序号一一对应）
    int row = -1;
    for (int i = 0; i < m_ops->count(); ++i) {
        if (m_ops->item(i)->data(Qt::UserRole).toInt() == step) {
            row = i;
            break;
        }
    }
    if (row >= 0 && m_ops->currentRow() != row) {
        const QSignalBlocker block(m_ops);
        m_ops->setCurrentRow(row);
        m_ops->scrollToItem(m_ops->item(row), QListWidget::PositionAtCenter);
    }
    // 「本局结算」只有该小局真的打完了才可点
    const bool done = m_replay->roundResultEntry(round) >= 0
                      && m_cursor >= m_replay->roundResultEntry(round);
    m_resultBtn->setEnabled(done);
    // 聊天：只显示当前步之前的发言（需求：以操作分割、顺序稳定）
    QStringList chat;
    const QVector<ReplayEntry>& entries = m_replay->entries();
    for (int i = 0; i <= m_cursor && i < entries.size(); ++i) {
        if (entries.at(i).isChat()) {
            chat << QStringLiteral("%1 %2")
                        .arg(entries.at(i).body.value(QStringLiteral("name")).toString(),
                             entries.at(i).body.value(QStringLiteral("text")).toString());
        }
    }
    m_chat->setPlainText(chat.join(QStringLiteral("\n")));
    updateGodView();
    if (m_wall != nullptr) {
        m_wall->setState(m_replay, round, m_cursor);
    }
}

void ReplayWindow::onPrevOp()
{
    seek(m_replay->prevStepEntry(m_cursor));
}

void ReplayWindow::onNextOp()
{
    // 唯一播动画的入口之一：前进**一个**操作
    seekAnimated(m_replay->nextStepEntry(m_cursor));
}

void ReplayWindow::onPrevTurn()
{
    if (m_replay->total() == 0) {
        return;
    }
    seek(m_replay->prevTurnStart(m_cursor));
}

void ReplayWindow::onNextTurn()
{
    if (m_replay->total() == 0) {
        return;
    }
    seek(m_replay->nextTurnStart(m_cursor));
}

void ReplayWindow::onPrevRound()
{
    if (m_replay->total() == 0) {
        return;
    }
    const int r = m_replay->roundOf(m_cursor);
    const int at = m_replay->roundOpenEnd(r - 1);
    seek(at >= 0 ? at : 0);
}

void ReplayWindow::onNextRound()
{
    if (m_replay->total() == 0) {
        return;
    }
    const int r = m_replay->roundOf(m_cursor);
    const int at = m_replay->roundOpenEnd(r + 1);
    seek(at >= 0 ? at : m_replay->entries().size() - 1);
}

void ReplayWindow::onJumpTurn()
{
    if (m_replay->total() == 0) {
        return;
    }
    const int round = m_replay->roundOf(m_cursor);
    const int at = m_replay->turnStart(round, m_turnSpin->value());
    if (at >= 0) {
        seek(at);
    }
}

void ReplayWindow::onPickRound(int index)
{
    if (m_replay->total() == 0 || index < 0) {
        return;
    }
    const int at = m_replay->roundOpenEnd(index);
    if (at >= 0) {
        seek(at);
    }
}

void ReplayWindow::onSeatChanged(int index)
{
    m_viewSeat = m_seatBox->itemData(index).toInt();
    seek(m_cursor);
}

void ReplayWindow::onSeatClicked(int seat)
{
    // 需求：点牌桌上的 ID 框（名牌）切到那家的视角。与下拉框走同一条路径，
    // 所以顺带把下拉框也同步过去（否则两处显示会不一致）。
    if (seat < 0 || seat >= 4 || seat == m_viewSeat) {
        return;
    }
    m_viewSeat = seat;
    const QSignalBlocker block(m_seatBox);
    m_seatBox->setCurrentIndex(m_seatBox->findData(seat));
    seek(m_cursor);
}

void ReplayWindow::onGodToggled(bool on)
{
    m_godOn = on;
    updateGodView();
    m_view->update();
}

void ReplayWindow::onRoundResult()
{
    showRoundResult(false);
}

QString ReplayWindow::exportTenhou(const QString& pathIn, QString* err)
{
    if (m_replay == nullptr || m_replay->total() == 0) {
        if (err != nullptr) {
            *err = QStringLiteral("no_replay");
        }
        return QString();
    }
    const TenhouLog::Result r = TenhouLog::build(*m_replay);
    if (!r.ok) {
        if (err != nullptr) {
            *err = r.problems.join(QLatin1Char(','));
        }
        return QString();
    }
    QString path = pathIn;
    if (path.isEmpty()) {
        const QString suggest = (m_replay->replayId().isEmpty() ? QStringLiteral("replay")
                                                                : m_replay->replayId())
                + QStringLiteral("-tenhou.txt");
        path = QFileDialog::getSaveFileName(this,
                                            lang::t(QStringLiteral("ui.replay.export_title")),
                                            QDir::homePath() + QLatin1Char('/') + suggest,
                                            lang::t(QStringLiteral("ui.replay.export_filter")));
        if (path.isEmpty()) {
            return QString();   // 用户取消
        }
    }
    QString werr;
    if (!TenhouLog::writeFile(r, path, &werr)) {
        if (err != nullptr) {
            *err = werr;
        }
        return QString();
    }
    // **完整牌谱**：同时写一份 mjlog XML（鸣牌/立直/和了细节都在）到同名 .xml
    QString xmlPath = path;
    if (xmlPath.endsWith(QLatin1String(".txt"), Qt::CaseInsensitive)) {
        xmlPath.chop(4);
    }
    xmlPath += QStringLiteral(".xml");
    const TenhouLog::MjlogResult mj = TenhouLog::buildMjlog(*m_replay);
    QString mjErr;
    const bool mjOk = TenhouLog::writeMjlogFile(mj, xmlPath, &mjErr);
    // 把链接也留在状态栏（可以直接粘到浏览器打开 tenhou.net/6）
    setStatus(lang::t(QStringLiteral("ui.replay.export_done")).arg(path, QString::number(r.rounds)));
    if (mjOk) {
        setStatus(m_status->text() + QStringLiteral(" | ")
                  + lang::t(QStringLiteral("ui.replay.export_mjlog")).arg(xmlPath));
    } else {
        setStatus(m_status->text() + QStringLiteral(" | ")
                  + lang::t(QStringLiteral("ui.replay.export_mjlog_failed")).arg(mjErr));
    }
    if (!r.problems.isEmpty()) {
        setStatus(m_status->text() + QStringLiteral(" ⚠ ")
                  + r.problems.join(QLatin1Char(',')));
    }
    return path;
}

void ReplayWindow::onExportTenhou()
{
    QString err;
    if (exportTenhou(QString(), &err).isEmpty() && !err.isEmpty()) {
        setStatus(lang::t(QStringLiteral("ui.replay.export_failed")).arg(err));
    }
}

void ReplayWindow::showRoundResult(bool resumeAfter)
{
    if (m_replay == nullptr || m_replay->total() == 0) {
        return;
    }
    const int round = m_replay->roundOf(m_cursor);
    const int at = m_replay->roundResultEntry(round);
    if (at < 0) {
        setStatus(lang::t(QStringLiteral("ui.replay.no_round_result"))
                          .arg(m_replay->roundText(round)));
        return;
    }
    // 结算画面要按"结算那一刻"的状态渲染（可能比当前光标更靠后）
    if (m_cursor < at) {
        seek(at);
    }
    m_resumeAfterResult = resumeAfter;
    m_roundResultOpen = true;
    if (m_playing) {
        m_timer->stop();   // 结算期间**暂停播放**，直到玩家确认
    }
    const ReplayEntry& e = m_replay->entries().at(at);
    const bool agari = (e.ev() == QLatin1String("agari"));
    auto* dlg = new ResultDialog(agari ? lang::t(QStringLiteral("ui.result.title_agari"))
                                       : lang::t(QStringLiteral("ui.result.title_ryuukyoku")),
                                 agari ? ResultDialog::agariHtml(e.body, m_model)
                                       : ResultDialog::ryuukyokuHtml(e.body, m_model),
                                 agari ? ResultDialog::schematicOf(e.body, m_model, true)
                                       : QString(),
                                 this);
    dlg->setAttribute(Qt::WA_DeleteOnClose);
    // ⚠ 故意**不调** `startCountdown()`：回放里没有"5 秒后自动开下一局"这回事，
    //    必须等玩家点确认（需求）。
    connect(dlg, &QDialog::finished, this, [this, round](int) {
        m_roundResultOpen = false;
        const bool resume = m_resumeAfterResult;
        m_resumeAfterResult = false;
        if (!resume || m_replay->total() == 0) {
            return;
        }
        // 玩家确认后才进下一小局；接着播（他本来就是开着播放的）。
        // ⚠ 用**弹窗打开时**那一局 +1，不要现算 `roundOf(m_cursor)` ——
        //    弹窗是无模态的，玩家可能已经翻到别的小局去了。
        const int next = m_replay->roundOpenEnd(round + 1);
        if (next >= 0) {
            seek(next);
        }
        if (m_playing) {
            m_timer->start(kPlayIntervalMs);
        }
    });
    dlg->show();
}

void ReplayWindow::onTogglePlay()
{
    if (m_roundResultOpen) {
        return;   // 本局结算还开着：先确认，再谈播放
    }
    m_playing = !m_playing;
    m_play->setText(lang::t(m_playing ? QStringLiteral("ui.replay.pause")
                                      : QStringLiteral("ui.replay.play")));
    if (m_playing) {
        m_timer->start(kPlayIntervalMs);
    } else {
        m_timer->stop();
    }
}

void ReplayWindow::onTick()
{
    if (m_roundResultOpen) {
        m_timer->stop();
        return;
    }
    const int next = m_replay->nextStepEntry(m_cursor);
    if (m_replay->stepOfEntry(m_cursor) + 1 >= m_replay->stepCount() || next <= m_cursor) {
        // 整场放完：停播，并把**最后一小局**的结算也显示出来（每一小局都该有一次结算）
        onTogglePlay();
        showRoundResult(false);
        return;
    }
    if (m_replay->roundOf(next) != m_replay->roundOf(m_cursor)) {
        // 跨小局：停在本局末尾，先把本局结算显示出来；玩家确认后才进下一局（无倒计时）
        showRoundResult(true);
        return;
    }
    seekAnimated(next);   // 播放的每一拍 = 前进一个操作，这一拍要播动画
}

void ReplayWindow::onListClicked()
{
    QListWidgetItem* item = m_ops->currentItem();
    if (item == nullptr) {
        return;
    }
    const int step = item->data(Qt::UserRole).toInt();
    const int at = m_replay->stepEntry(step);
    if (at >= 0) {
        seek(at);
    }
}

void ReplayWindow::onListDoubleClicked()
{
    QListWidgetItem* item = m_list->currentItem();
    if (item == nullptr) {
        return;
    }
    m_pendingId = item->data(Qt::UserRole).toString();
    m_openEdit->setText(m_pendingId);
    m_replay->reset();
    m_ops->clear();
    m_opsRound = -1;
    requestChunk(0);
}

void ReplayWindow::showWall()
{
    if (m_wall == nullptr) {
        m_wall = new WallView(this);
        connect(m_wall, &WallView::seekRequested, this, &ReplayWindow::seek);
    }
    m_wall->setState(m_replay, m_replay->roundOf(m_cursor), m_cursor);
    m_wall->show();
    m_wall->raise();
    m_wall->activateWindow();
}
