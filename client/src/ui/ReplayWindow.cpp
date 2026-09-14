#include "ReplayWindow.h"

#include <QComboBox>
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
                requestChunk(0);
            }
        }
    });
    top->addWidget(openBtn);
    root->addLayout(top);

    // ---- 导航条 ----
    auto* nav = new QHBoxLayout();
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

    nav->addWidget(new QLabel(lang::t(QStringLiteral("ui.replay.round")), this));
    m_roundBox = new QComboBox(this);
    connect(m_roundBox, QOverload<int>::of(&QComboBox::activated), this, &ReplayWindow::onPickRound);
    nav->addWidget(m_roundBox);

    nav->addWidget(new QLabel(lang::t(QStringLiteral("ui.replay.view_seat")), this));
    m_seatBox = new QComboBox(this);
    for (int s = 0; s < 4; ++s) {
        m_seatBox->addItem(QStringLiteral("%1").arg(s + 1), s);
    }
    connect(m_seatBox, QOverload<int>::of(&QComboBox::activated), this, &ReplayWindow::onSeatChanged);
    nav->addWidget(m_seatBox);

    m_wallBtn = new QPushButton(lang::t(QStringLiteral("ui.replay.wall")), this);
    connect(m_wallBtn, &QPushButton::clicked, this, &ReplayWindow::showWall);
    nav->addWidget(m_wallBtn);
    nav->addStretch(1);
    root->addLayout(nav);

    // ---- 主体：左（牌桌）/ 右（操作列表 + 聊天 + 记录列表）----
    auto* split = new QSplitter(Qt::Horizontal, this);
    m_view = new TableView(split);
    m_view->setModel(m_model);
    split->addWidget(m_view);

    auto* right = new QSplitter(Qt::Vertical, split);
    m_ops = new QListWidget(right);
    connect(m_ops, &QListWidget::itemClicked, this, &ReplayWindow::onListClicked);
    right->addWidget(m_ops);
    m_chat = new QPlainTextEdit(right);
    m_chat->setReadOnly(true);
    right->addWidget(m_chat);
    m_list = new QListWidget(right);
    connect(m_list, &QListWidget::itemDoubleClicked, this, &ReplayWindow::onListDoubleClicked);
    right->addWidget(m_list);
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
                           m_play, m_jump, m_wallBtn}) {
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
            if (!m_replay->setMeta(ev.value(QStringLiteral("meta")).toObject())) {
                setStatus(lang::t(QStringLiteral("ui.replay.bad")));
                return;
            }
            m_currentId = id;
            m_ops->clear();
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
    // 操作列表按**步**（不是按 entry）：一次配牌/一次摸牌各占一行，
    // 否则"下一步"要按四次才过一个操作（真机第一版就是那样）。
    for (int step = 0; step < m_replay->stepCount(); ++step) {
        const int at = m_replay->stepEntry(step);
        m_ops->addItem(QStringLiteral("%1. %2").arg(step + 1).arg(m_replay->describe(at)));
    }
    setEnabledAll(true);
    setStatus(lang::t(QStringLiteral("ui.replay.loaded"))
                      .arg(m_replay->replayId())
                      .arg(m_replay->roundCount())
                      .arg(m_replay->stepCount()));
    const int open0 = m_replay->roundOpenEnd(0);
    seek(open0 >= 0 ? open0 : 0);
}

void ReplayWindow::seek(int index)
{
    if (m_replay->total() == 0) {
        return;
    }
    const int n = m_replay->entries().size();
    m_cursor = qBound(0, index, qMax(0, n - 1));
    const int round = m_replay->roundOf(m_cursor);
    const int start = m_replay->roundStart(round);
    // 从**本小局的第一条**重放：一个小局最多几百条，够快，且不必给每种事件写反向操作
    m_model->reset();
    m_model->setMySeat(m_viewSeat);
    for (int i = qMax(0, start); i <= m_cursor; ++i) {
        const ReplayEntry& e = m_replay->entries().at(i);
        // 私有事件（draw / ask / round_start）只喂给"视角那一家"，
        // 这样 TableView 画出来的画面与那一家的实时对局一致
        if (e.to >= 0 && e.to != m_viewSeat) {
            continue;
        }
        m_model->applyEvent(e.body);
    }
    refreshUi();
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
    if (m_ops->currentRow() != step) {
        const QSignalBlocker block(m_ops);
        m_ops->setCurrentRow(step);
        if (m_ops->currentItem() != nullptr) {
            m_ops->scrollToItem(m_ops->currentItem(), QListWidget::PositionAtCenter);
        }
    }
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
    seek(m_replay->nextStepEntry(m_cursor));
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

void ReplayWindow::onTogglePlay()
{
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
    if (m_replay->stepOfEntry(m_cursor) + 1 >= m_replay->stepCount()) {
        onTogglePlay();
        return;
    }
    seek(m_replay->nextStepEntry(m_cursor));
}

void ReplayWindow::onListClicked()
{
    const int row = m_ops->currentRow();
    const int at = m_replay->stepEntry(row);
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
