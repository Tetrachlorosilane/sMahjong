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
    void autoStart(const QString& host, quint16 port, const QString& name, int bots);
    // 局间确认：告诉服务端可以直接开下一局
    void sendConfirmNextRound();
    // 自动进房但仍由人工操作（演示/截图用）
    void setAutoAnswer(bool on) { m_autoPlay = on; }

    // ---- 自检钩子 ----
    // 拦下即将发出的命令：不接网络也能断言「自动应答到底发了什么报文」。
    void setCommandTapForTest(std::function<void(const QJsonObject&)> tap);
    AutoBar* autoBarForTest() const { return m_autoBar; }
    void feedEventForTest(const QJsonObject& ev) { onEvent(ev); }

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
    void onTileClicked(const QString& tile, int index);
    void onRiichiModeChanged(bool on);
    void onAutoFlagsChanged();
    void onChatSend();
    void onLeaveRoom();

private:
    void buildWaitingPage();
    void buildTablePage();
    void showLobby();
    void updateWaitingRoom(const QJsonObject& room);
    void updateScorePanel();
    void appendChat(const QString& who, const QString& text);
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
    QTextBrowser* m_waitChat = nullptr;
    QLineEdit* m_waitChatEdit = nullptr;
    bool m_ready = false;
    QJsonObject m_room;

    // 牌桌
    QWidget* m_tablePage = nullptr;
    TableView* m_table = nullptr;
    ActionBar* m_actions = nullptr;
    AutoBar* m_autoBar = nullptr;          // 牌桌外的三个自动开关
    autopolicy::Flags m_autoFlags;         // 它们的当前状态（decide 的输入）
    QLabel* m_scorePanel = nullptr;
    QTextBrowser* m_chatView = nullptr;
    QLineEdit* m_chatEdit = nullptr;
    QString m_myName;
    int m_myPid = 0;
    QJsonObject m_pendingHello;
    QString m_lastNetError;   // 连接失败提示去重

    // 自动模式
    bool m_resultOpen = false;    // 结算弹窗是否开着
    ResultDialog* m_resultDlg = nullptr;   // 局间倒计时/自动关闭要用
    bool m_autoCreate = false;   // 自动建房（与是否自动应答解耦）
    bool m_autoPlay = false;
    bool m_autoRoomSent = false;
    int m_autoBots = 3;
    std::function<void(const QJsonObject&)> m_cmdTap;   // 自检用（正常运行为空）

    // 回放
    ReplayWindow* m_replay = nullptr;   // 懒建；自己一条连接
    QString m_replayId;                 // 本场的回放 ID（`game_start` / `game_end` 里带）
    QString m_host;                     // 当前连接目标（回放窗口要用）
    quint16 m_port = 0;
    Settings m_settings;                // 持久化的个人设置（地址/端口/昵称/材质包）
    QString m_settingsPath;             // 设置文件路径（由 main 传进来）
};
