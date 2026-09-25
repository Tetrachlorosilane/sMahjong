#pragma once

// 大厅：服务器地址/昵称、房间列表、创建房间（规则）、加入房间。

#include <QDialog>
#include <QJsonArray>
#include <QJsonObject>
#include <QString>

class QComboBox;
class QLabel;
class QLineEdit;
class QListWidget;
class QPushButton;
class QSpinBox;

class LobbyDialog : public QDialog
{
    Q_OBJECT
public:
    explicit LobbyDialog(QWidget* parent = nullptr);

    void setRooms(const QJsonArray& rooms);
    void setStatus(const QString& text);
    void setConnected(bool on);

    /**
     * 服务端可选的**机器人 AI 清单**（来自 `hello_ok.bot_ais`）：填进「机器人 AI」下拉框。
     * 第一项固定是「跟服务端默认」（数据 = 空串 = 建桌报文不带 `bot_ai`）。
     */
    void setBotAis(const QJsonArray& ais);

    /** 自检用：机器人 AI 下拉框（不依赖私有成员名）。 */
    QComboBox* botAiComboForTest() const { return m_botAi; }

    QString host() const;
    quint16 port() const;
    QString playerName() const;

    /**
     * 用**持久化的个人设置**预填三个输入框（启动时调一次；设置改完后再调一次）。
     * 只填非空值：昵称留空时保留界面上的默认名，不把用户输入清成空。
     */
    void applySettings(const QString& host, quint16 port, const QString& name);

signals:
    void connectRequested(const QString& host, quint16 port, const QString& name);
    void refreshRequested();
    void createRoomRequested(const QString& name, const QJsonObject& rules, int fillBots,
                             const QString& botAi);
    void joinRoomRequested(const QString& roomId);
    /** 「对局回放」：打开回放窗口（未连接时入口无效）。 */
    void replayRequested();
    /** 「设置」：打开个人设置对话框。 */
    void settingsRequested();

private:
    void onConnectClicked();
    void onCreateClicked();
    void onJoinClicked();
    /** 切换预设时把「一位必要点数」的默认值填进输入框（玩家仍可改成任意值）。 */
    void onPresetChanged();
    QString selectedRoomId() const;
    /** 选中的机器人 AI 名字；空 = 「跟服务端默认」（建桌报文就不带 `bot_ai`）。 */
    QString selectedBotAi() const;

    QLineEdit* m_host = nullptr;
    QSpinBox* m_port = nullptr;
    QLineEdit* m_name = nullptr;
    QPushButton* m_connectBtn = nullptr;
    QLabel* m_status = nullptr;

    QListWidget* m_rooms = nullptr;
    QPushButton* m_refreshBtn = nullptr;
    QPushButton* m_replayBtn = nullptr;
    QPushButton* m_settingsBtn = nullptr;

    QLineEdit* m_roomName = nullptr;
    QComboBox* m_preset = nullptr;   // 规则预设：mleague（默认）/ tenhou / majsoul
    QComboBox* m_length = nullptr;
    QComboBox* m_aka = nullptr;
    QComboBox* m_think = nullptr;   // 思考时间：每巡基本时长 + 总额外时长
    QSpinBox* m_requiredPoints = nullptr;   // 一位必要点数（0 = 不要求）
    QSpinBox* m_bots = nullptr;
    // 机器人用哪一代 AI：清单由服务端给（`hello_ok.bot_ais`），第一项 = 跟服务端默认
    QComboBox* m_botAi = nullptr;
    QPushButton* m_createBtn = nullptr;

    QLineEdit* m_joinId = nullptr;
    QPushButton* m_joinBtn = nullptr;
};

/**
 * 建房规则的**纯判据**（不依赖控件，方便自检直接钉住）。
 *
 * ⚠ 刻意做成**自由函数**而不是 `LobbyDialog` 的静态成员：带 `Q_OBJECT` 的类里放静态成员函数
 * 会让 moc 生成的代码落到那个函数的上下文里（`'this' is unavailable for static member
 * functions`，编译不过）。
 */
namespace lobbyrules {

/**
 * 预设对应的**一位必要点数**（0 = 不要求）：M.League 不要求、《天凤》《雀魂》= 30000
 * （`docs/日本麻将.md` L116/L157）。界面上按它填默认值，玩家仍可改成任意值。
 */
int defaultRequiredPoints(const QString& preset);

}  // namespace lobbyrules
