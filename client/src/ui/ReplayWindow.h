#pragma once

// 回放窗口：入口有两个（大厅「对局回放」/ 整场总结算的「看本局回放」），行为一致。
//
// 组成：
//   · 牌桌视图 —— 复用实时对局的 `TableView` + `TableModel`：把记录里的事件按顺序喂进去，
//     所以回放的画面与实时对局**同一条渲染路径**（不会两套画法漂移）。
//   · 导航条 —— 上一/下一操作、上一/下一巡、上一/下一小局、跳到指定巡目、播放/暂停、
//     显示他家手牌开关、视角切换（下拉或直接点牌桌上的名牌）、本局结算。
//   · 操作列表 —— **按小局切割**：只显示当前小局的步骤（需求：每小局只展示对应的记录）。
//   · 聊天面板 —— 按 seq 切分：只显示"当前步之前"的发言（需求：以操作分割、顺序稳定）。
//   · 牌山窗口 —— `WallView`（136 张按抓牌顺序铺开，见 PROTOCOL §3.11）。
//
// **动画策略**（需求：只有该播的牌才播）：
//   只有「前进一个操作」（下一步 / 自动播放的每一拍）播飞牌动画；
//   其余一切跳转（上一步 / 换巡 / 换小局 / 跳巡目 / 点列表 / 换视角 / 刚载入）都是**静默重建**。
//   原因是重建是"从本小局第一条重放"，若每次都带动画，就会把前几巡的牌重打一遍 ——
//   这正是「每回放一个操作，四家都重复打出前几巡的牌」那个渲染 bug 的根因。
//
// **每小局结算**：播放跨到下一小局前会停下来显示本局结算，**没有倒计时**，
//   直到玩家点确认才进下一局；工具栏上的「本局结算」按钮可随时手动打开。
//
// 性能与内存：
//   · 事件只向服务端拉一次（2000 条一页）；索引在 `ReplayModel::build()` 里一趟算完。
//   · 跳转 = 从**所在小局的第一条**重放（一个小局最多几百条，微秒级），
//     所以"上一操作"不需要为每种事件写反向操作（那才是 bug 温床），内存也只是几个小数组。
//   · 位置变化只重画牌桌与列表当前行，不重建整个窗口。

#include <QDialog>
#include <QJsonObject>
#include <QStringList>
#include <QVector>

class QComboBox;
class QLabel;
class QLineEdit;
class QListWidget;
class QPlainTextEdit;
class QPushButton;
class QSpinBox;
class QTimer;
class NetClient;
class QSplitter;
class TableModel;
class TableView;
class WallView;
class ReplayModel;

class ReplayWindow : public QDialog
{
    Q_OBJECT
public:
    explicit ReplayWindow(QWidget* parent = nullptr);
    ~ReplayWindow() override;

    /** 连服务端并（可选）直接打开某一场；replayId 为空时显示列表让用户挑。 */
    void openReplay(const QString& host, quint16 port, const QString& replayId = QString());

    /** 打开牌山窗口（命令行 `--replay --wall` 与按钮都走它）。 */
    void showWall();

    // ---- 命令行 / L4 定点复现用的钩子（与界面按钮走同一条路径，不另开分支）----
    /** 打开或关闭「显示其他家手牌」（等价于点那个切换键）。 */
    void setGodMode(bool on);
    /** 弹出当前小局的结算界面（等价于点「本局结算」）。回放里**没有倒计时**。 */
    void openRoundResult();
    /** 跳到第 `step` 步（0 起；越界会被钳制）。 */
    void seekStep(int step);

signals:
    /** 记录载入并建好索引（命令行据此在"真的能看了"之后再执行 `--wall` / `--god` / `--step`）。 */
    void replayLoaded();

private slots:
    void onConnected();
    void onDisconnected();
    void onEvent(const QJsonObject& ev);
    void onError(const QString& msg);
    void onPrevOp();
    void onNextOp();
    void onPrevTurn();
    void onNextTurn();
    void onPrevRound();
    void onNextRound();
    void onJumpTurn();
    void onPickRound(int index);
    void onTogglePlay();
    void onTick();
    void onListDoubleClicked();
    void onListClicked();
    void onSeatChanged(int index);
    /** 点牌桌上的名牌 → 切到那家的视角（需求：点 ID 框切视角）。 */
    void onSeatClicked(int seat);
    /** 「显示其他家手牌」开关。 */
    void onGodToggled(bool on);
    /** 「本局结算」按钮。 */
    void onRoundResult();

private:
    void buildUi();
    void requestChunk(int from);
    void finishLoad();
    /** 静默跳转（一切"非前进一个操作"的跳转都走这里）。 */
    void seek(int index);
    /** 带动画地前进到 `index`（只有"下一步 / 播放的一拍"用）。 */
    void seekAnimated(int index);
    /**
     * 从本小局第一条重放到 `index`。
     *
     * @param animate 只让**落在最后一步里**的事件播动画；`false` = 全程静默。
     */
    void replayTo(int index, bool animate);
    void refreshUi();
    /** 按小局重建右侧操作列表（本来就是"每小局只展示对应的记录"）。 */
    void rebuildOps(int round);
    /** 把 `m_godOn` 对应的四家手牌喂给牌桌（关掉时传空 = 恢复实时对局的画法）。 */
    void updateGodView();
    /**
     * 打开本小局的结算界面。
     *
     * @param resumeAfter 关掉结算后是否自动接着播（播放跨小局时为真；手动点按钮时为假）。
     */
    void showRoundResult(bool resumeAfter);
    void setStatus(const QString& text);
    void setEnabledAll(bool on);

    NetClient* m_net = nullptr;
    TableModel* m_model = nullptr;
    TableView* m_view = nullptr;
    ReplayModel* m_replay = nullptr;
    WallView* m_wall = nullptr;

    QListWidget* m_ops = nullptr;
    QListWidget* m_list = nullptr;
    QPlainTextEdit* m_chat = nullptr;
    QLabel* m_status = nullptr;
    QLabel* m_pos = nullptr;
    QLabel* m_opsTitle = nullptr;
    QLineEdit* m_openEdit = nullptr;
    QComboBox* m_roundBox = nullptr;
    QComboBox* m_seatBox = nullptr;
    QSpinBox* m_turnSpin = nullptr;
    QPushButton* m_prevOp = nullptr;
    QPushButton* m_nextOp = nullptr;
    QPushButton* m_prevTurn = nullptr;
    QPushButton* m_nextTurn = nullptr;
    QPushButton* m_prevRound = nullptr;
    QPushButton* m_nextRound = nullptr;
    QPushButton* m_play = nullptr;
    QPushButton* m_jump = nullptr;
    QPushButton* m_wallBtn = nullptr;
    QPushButton* m_godBtn = nullptr;
    QPushButton* m_resultBtn = nullptr;
    QTimer* m_timer = nullptr;

    QString m_host;
    quint16 m_port = 0;
    QString m_pendingId;
    QString m_currentId;
    int m_cursor = 0;
    int m_viewSeat = 0;
    int m_opsRound = -1;         // 操作列表当前装的是哪一小局（-1 = 还没装）
    bool m_loading = false;
    bool m_playing = false;
    bool m_godOn = false;        // 显示其他家手牌
    bool m_resumeAfterResult = false;
    /** 本局结算弹窗开着：这期间不允许继续播放（"直到玩家点确认再进下一局"）。 */
    bool m_roundResultOpen = false;
};
