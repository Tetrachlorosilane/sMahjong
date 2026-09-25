#pragma once

// 主窗口：QStackedWidget 切换「大厅 / 等待室 / 牌桌」，
// 负责把 NetClient 的事件分发到 TableModel / TableView / ActionBar。

#include <QJsonObject>
#include <QMainWindow>
#include <QString>

#include <functional>

#include "model/AutoPolicy.h"
#include "model/TableModel.h"
#include "model/Settings.h"
#include "net/NetClient.h"

class ActionBar;
class AutoBar;
class LobbyDialog;
class QComboBox;
class QLabel;
class QLineEdit;
class QPushButton;
class QStackedWidget;
class QTextBrowser;
class ResultDialog;
class ReplayWindow;
class TableView;

class MainWindow : public QMainWindow
{
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);
    ~MainWindow() override;

    // 演示 / 联调用：自动连接 → 建房 → 补机器人 → 准备 → 自动应答。
    //   mj-bots = 0 时只自动连接并在牌桌上等待人工操作。
    //   botAi = 建房时指定的机器人 AI 名字（空 = 跟服务端默认）。
    void autoStart(const QString& host, quint16 port, const QString& name, int bots,
                   const QString& botAi = QString());
    // 局间确认：告诉服务端可以直接开下一局
    void sendConfirmNextRound();
    // 自动进房但仍由人工操作（演示/截图用）
    void setAutoAnswer(bool on) { m_autoPlay = on; }

    // ---- 自检钩子 ----
    // 拦下即将发出的命令：不接网络也能断言「自动应答到底发了什么报文」。
    void setCommandTapForTest(std::function<void(const QJsonObject&)> tap);
    AutoBar* autoBarForTest() const { return m_autoBar; }
    void feedEventForTest(const QJsonObject& ev) { onEvent(ev); }
    /** 自选座位按钮（0=东…3=北）：等待室的"上一个按钮有没有弹起"要看它的 enabled。 */
    QPushButton* seatButtonForTest(int seat) const
    {
        return (seat >= 0 && seat < 4) ? m_takeSeatBtn[seat] : nullptr;
    }
    /** 「结束对局」按钮（投票入口）；投票中的「同意 / 不同意」与状态行也一并给出。 */
    QPushButton* voteEndButtonForTest() const { return m_voteEndBtn; }
    QPushButton* voteAgreeButtonForTest() const { return m_voteAgreeBtn; }
    QPushButton* voteDisagreeButtonForTest() const { return m_voteDisagreeBtn; }
    QLabel* voteLabelForTest() const { return m_voteLabel; }
    /** 当前持有的身份（uuid）：`uuid_ok{issued:true}` 之后应当被写进设置。 */
    QString uuidForTest() const { return m_settings.uuid; }
    /** 等待室的座位行（自检要看「掉线」标记有没有画进去）。 */
    QLabel* seatLabelForTest(int seat) const
    {
        return (seat >= 0 && seat < 4) ? m_seatLabels[seat] : nullptr;
    }
    /**
     * 当前显示的是哪一页（`"table"` / `"wait"` / `"other"`）。
     *
     * <p>用来钉住那条容易回归的行为：**对局进行中的 `room` 事件**（有人掉线托管时会广播一次）
     * 不得把界面切回等待室 —— 否则四个人正在打牌时会突然看到"准备/开始游戏"按钮。
     */
    QString stackPageForTest() const;

    /**
     * 等待室的「机器人 AI」下拉框（房主开局前可换"机器人用哪一代"）。
     *
     * <p>自检要钉三件事：清单来自服务端 `hello_ok.bot_ais`、当前值跟着 `room.bot_ai` 走、
     * **只有房主且不在牌局中**才可点。
     */
    QComboBox* botAiComboForTest() const { return m_botAiCombo; }

    /**
     * 上电时把**持久化的个人设置**装进来：预填大厅的地址/端口/昵称，
     * 并记住设置文件路径（用户改完设置、或从大厅连上过一次，就写回去）。
     */
    void applySettings(const Settings& st, const QString& path);
    /** 打开个人设置对话框（大厅的「设置」按钮走这里）。 */
    void openSettings();

private slots:
    void onEvent(const QJsonObject& ev);
    void onConnected();
    void onDisconnected();
    void onNetError(const QString& msg);
    void onActionReady(const QJsonObject& action);
    void onTileClicked(const QString& tile, int index);    /**
     * 点了某家的名牌：**观战时**把视角切到那一家（与回放界面同一条路径）。
     *
     * <p>实时对局里坐着的人点自己的名牌没有意义 —— 那时直接忽略。
     */
    void onSeatClicked(int seat);
    /** 观战视角下拉选了一条（`index` = 座位号）。 */
    void onViewSeatPicked(int index);
    /** 观战 UI 开关：隐藏操作栏/自动开关，显示「观战中 + 视角」那一条。 */
    void setSpectatingUi(bool on);
    void onRiichiModeChanged(bool on);
    void onAutoFlagsChanged();
    void onLeaveRoom();

private:
    void buildWaitingPage();
    void buildTablePage();
    void showLobby();
    void updateWaitingRoom(const QJsonObject& room);
    /**
     * 填「机器人 AI」下拉框（清单来自服务端 `hello_ok.bot_ais`）。
     *
     * 客户端**只认名字**：`net:<路径>` 是服务器本机文件，服务端只接受注册表里的名字
     * （`mahjong.ai.BotAis`），所以这里也不提供手填入口。第一项固定是「跟服务端默认」。
     */
    void setBotAiCatalogue(const QJsonArray& ais);
    /** 把下拉框设成某个名字（空 = 默认项）；信号会被**临时挡住**，避免"同步"被当成"用户改选"。 */
    void showBotAiSelection(const QString& name);
    void updateScorePanel();
    void appendChat(const QString& who, const QString& text);
    /**
     * 发送聊天：把**指定输入框**的内容发出去。
     *
     * 两个「发送」按钮分别属于等待页（`m_waitChatEdit`）与牌桌页（`m_chatEdit`），
     * 各自配自己的输入框 —— **不能**用 `sender()` 反推（按钮点击时它是 QPushButton，
     * 反推会拿到 nullptr 并静默 return，症状就是"回车能发、点按钮没反应"）。
     */
    void sendChatFrom(QLineEdit* edit);
    /** 从大厅连上过一次 → 把地址/端口/昵称写回设置文件（材质包那条不动）。 */
    void saveCurrentEndpoint();
    void sendCommand(const QJsonObject& obj);
    /** 按三个自动开关替玩家应答本次询问（不满足条件时什么都不做）。 */
    void applyAuto(const QString& kind, const QJsonObject& ask);
    /** 每小局结束：三个自动开关立刻全部关掉（用户要求）。 */
    void resetAutoFlags();
    /**
     * 弹出结算界面。
     *
     * @param offerReplay 是否给「看本局回放」按钮。**只有整场总结算（`game_end`）才传 true** ——
     *        牌局还没打完时，回放记录尚未落盘（服务端是终局才写盘），那个按钮点不出任何东西；
     *        用户也明确要求"未结束时不该提供回放按钮"。
     */
    void showResultDialog(const QString& title, const QString& html,
                           const QString& schematic = QString(), bool offerReplay = false);
    /** 关闭结算弹窗。confirm=true 视为玩家确认；服务端已推进时传 false（不发 confirm）。 */
    void closeResultDialog(bool confirm);
    /** 开局前自选座位（门风 = 座次）。服务端把两家的住户**互换**，谁也不被踢出去。 */
    void takeSeat(int seat);
    bool isHost() const;

    // ---- 结束对局投票（服务端权威：计票与冷却都由它算，见 PROTOCOL §2.5/§3.12）----
    /** 发 `vote_end`（冷却中/不在对局中时服务端会回 `vote_denied`，客户端只管显示）。 */
    void requestVoteEnd();
    /** 对进行中的投票表态（同意 / 不同意）。 */
    void castVote(bool agree);
    /** 按当前状态重画投票条：按钮可见性与倒计时文案。 */
    void refreshVoteUi();
    /** 把 `uuid_ok` 给的**身份**写进设置文件（`issued=true` 时必须存下来）。 */
    void saveIdentity();

    /**
     * 打开回放窗口（懒建）：replayId 为空 = 只打开列表让用户挑。
     *
     * <p>回放窗口**自己开一条连接**（服务端的 `replay_*` 与是否入座无关），
     * 所以玩家在一局里也能开回放，不会干扰当前对局。
     */
    void openReplayWindow(const QString& replayId = QString());

    NetClient m_net;
    TableModel m_model;
    QStackedWidget* m_stack = nullptr;
    LobbyDialog* m_lobby = nullptr;

    // 等待室
    QWidget* m_waitPage = nullptr;
    QLabel* m_roomLabel = nullptr;
    QLabel* m_seatLabels[4] = { nullptr, nullptr, nullptr, nullptr };
    /** 开局前「自选座位」按钮（下标 = 座位号，也就是门风：0=东/起家）。 */
    QPushButton* m_takeSeatBtn[4] = { nullptr, nullptr, nullptr, nullptr };
    QPushButton* m_readyBtn = nullptr;
    QPushButton* m_addBotBtn = nullptr;
    QPushButton* m_removeBotBtn = nullptr;
    QPushButton* m_startBtn = nullptr;
    QPushButton* m_shuffleBtn = nullptr;   // 房主：随机洗座（随机门风）
    /** 房主：这一桌的机器人用哪一代 AI（清单来自服务端；开局前可换）。 */
    QComboBox* m_botAiCombo = nullptr;
    QLabel* m_botAiLabel = nullptr;
    QTextBrowser* m_waitChat = nullptr;
    QLineEdit* m_waitChatEdit = nullptr;
    bool m_ready = false;
    QJsonObject m_room;

    // 牌桌
    QWidget* m_tablePage = nullptr;
    TableView* m_table = nullptr;
    ActionBar* m_actions = nullptr;
    AutoBar* m_autoBar = nullptr;          // 牌桌外的三个自动开关
    // 观战（对局中入局 = 无座位）：说明条 + 视角下拉（点名牌也走同一条路）
    QWidget* m_spectateBar = nullptr;
    QLabel* m_spectateLabel = nullptr;
    QComboBox* m_spectateSeat = nullptr;
    autopolicy::Flags m_autoFlags;         // 它们的当前状态（decide 的输入）
    QLabel* m_scorePanel = nullptr;
    QTextBrowser* m_chatView = nullptr;
    QLineEdit* m_chatEdit = nullptr;
    QString m_myName;
    int m_myPid = 0;
    QJsonObject m_pendingHello;
    QString m_lastNetError;   // 连接失败提示去重
    /** 哪几家正在**掉线托管**（来自 `room.seats[].away`）：分数栏要用它标出来。 */
    bool m_away[4] = {false, false, false, false};

    // 结束对局投票（与自动开关同一排，高度固定，免得整桌重新布局）
    QWidget* m_voteBar = nullptr;
    QPushButton* m_voteEndBtn = nullptr;
    QPushButton* m_voteAgreeBtn = nullptr;
    QPushButton* m_voteDisagreeBtn = nullptr;
    QLabel* m_voteLabel = nullptr;
    QTimer* m_voteTick = nullptr;         // 每秒刷一次倒计时（只影响显示）
    bool m_inGame = false;                // 本场的对局是否进行中（`game_start` … `game_end`）
    bool m_voteRunning = false;           // 服务端的投票进行中
    bool m_voteVoted = false;             // 我已经表过态（发起人算已表态）
    int m_voteAgree = 0;
    int m_voteNeed = 0;
    int m_voteTotal = 0;
    qint64 m_voteDeadlineMs = 0;          // 投票截止（本地推算，仅用于倒计时显示）
    qint64 m_voteCooldownUntilMs = 0;     // 冷却到期（本地推算，仅用于倒计时显示）

    // 自动模式
    bool m_resultOpen = false;    // 结算弹窗是否开着
    ResultDialog* m_resultDlg = nullptr;   // 局间倒计时/自动关闭要用
    bool m_autoCreate = false;   // 自动建房（与是否自动应答解耦）
    bool m_autoPlay = false;
    bool m_autoRoomSent = false;
    int m_autoBots = 3;
    /** `--demo ... --bot-ai <名字>`：自动建房时指定的机器人 AI（空 = 跟服务端默认）。 */
    QString m_autoBotAi;
    std::function<void(const QJsonObject&)> m_cmdTap;   // 自检用（正常运行为空）

    // 回放
    ReplayWindow* m_replay = nullptr;   // 懒建；自己一条连接
    QString m_replayId;                 // 本场的回放 ID（`game_start` / `game_end` 里带）
    QString m_host;                     // 当前连接目标（回放窗口要用）
    quint16 m_port = 0;
    Settings m_settings;                // 持久化的个人设置（地址/端口/昵称/材质包）
    QString m_settingsPath;             // 设置文件路径（由 main 传进来）
};
