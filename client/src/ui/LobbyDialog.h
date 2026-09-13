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

    QString host() const;
    quint16 port() const;
    QString playerName() const;

signals:
    void connectRequested(const QString& host, quint16 port, const QString& name);
    void refreshRequested();
    void createRoomRequested(const QString& name, const QJsonObject& rules, int fillBots);
    void joinRoomRequested(const QString& roomId);

private:
    void onConnectClicked();
    void onCreateClicked();
    void onJoinClicked();
    QString selectedRoomId() const;

    QLineEdit* m_host = nullptr;
    QSpinBox* m_port = nullptr;
    QLineEdit* m_name = nullptr;
    QPushButton* m_connectBtn = nullptr;
    QLabel* m_status = nullptr;

    QListWidget* m_rooms = nullptr;
    QPushButton* m_refreshBtn = nullptr;

    QLineEdit* m_roomName = nullptr;
    QComboBox* m_length = nullptr;
    QComboBox* m_aka = nullptr;
    QComboBox* m_think = nullptr;   // 思考时间：每巡基本时长 + 总额外时长
    QSpinBox* m_bots = nullptr;
    QPushButton* m_createBtn = nullptr;

    QLineEdit* m_joinId = nullptr;
    QPushButton* m_joinBtn = nullptr;
};
