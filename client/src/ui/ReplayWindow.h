#pragma once

// 回放窗口：入口有两个（大厅「对局回放」/ 结算界面「看本局回放」），行为一致。
//
// 组成：
//   · 牌桌视图 —— 复用实时对局的 `TableView` + `TableModel`：把记录里的事件按顺序喂进去，
//     所以回放的画面与实时对局**同一条渲染路径**（不会两套画法漂移）。
//   · 导航条 —— 上一/下一操作、上一/下一巡、上一/下一小局、跳到指定巡目、播放/暂停。
//   · 操作列表 —— 每条事件一行（`ReplayModel::describe`），点哪条跳哪条。
//   · 聊天面板 —— 按 seq 切分：只显示"当前步之前"的发言（需求：以操作分割、顺序稳定）。
//   · 牌山窗口 —— `WallView`（单开窗口，136 张 + 进度）。
//
// 性能与内存：
//   · 事件只向服务端拉一次（2000 条一页）；索引在 `ReplayModel::build()` 里一趟算完。
//   · 跳转 = 从**所在小局的第一条**重放（一个小局最多几百条，微秒级），
//     所以"上一操作"不需要为每种事件写反向操作（那才是 bug 温床），内存也只是几个小数组。
//   · 位置变化只重画牌桌与列表当前行，不重建整个窗口。

#include <QDialog>
#include <QJsonObject>

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

private:
    void buildUi();
    void requestChunk(int from);
    void finishLoad();
    void seek(int index);
    void refreshUi();
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
    QTimer* m_timer = nullptr;

    QString m_host;
    quint16 m_port = 0;
    QString m_pendingId;
    QString m_currentId;
    int m_cursor = 0;
    int m_viewSeat = 0;
    bool m_loading = false;
    bool m_playing = false;
};
