#pragma once

// 网络层：QTcpSocket + UTF-8 NDJSON（docs/PROTOCOL.md §0）
// - 收包按 '\n' 切分，容忍粘包/半包；单条上限 1 MiB
// - 每 20 秒发送 {"cmd":"ping"}

#include <QAbstractSocket>
#include <QByteArray>
#include <QHostAddress>
#include <QList>
#include <QObject>
#include <QString>
#include <QTcpSocket>
#include <QTimer>

class QJsonObject;

class NetClient : public QObject
{
    Q_OBJECT
public:
    explicit NetClient(QObject* parent = nullptr);
    ~NetClient() override;

    // 连接到服务器（异步）。
    // 会自动把主机名解析成多个候选地址并逐个尝试：只要有一个通就连上。
    // 这一条专门解决「127.0.0.1 拒连但 localhost / ::1 能通」的情况
    // （典型场景：服务端在 WSL 里跑，WSL 的 localhost 转发只绑了 IPv6 回环）。
    void connectToServer(const QString& host, quint16 port);
    // 主动断开
    void disconnectFromServer();
    bool isConnected() const;

    // 发送一条命令（对象会自动补 '\n'）
    void sendCommand(const QJsonObject& obj);
    // 最近一次错误描述
    QString lastError() const { return m_lastError; }

signals:
    void connected();
    void disconnected();
    void eventReceived(const QJsonObject& ev);
    void errorOccurred(const QString& msg);
    // 正在尝试第 index+1/total 个候选地址（用于界面提示）
    void tryingAddress(const QString& addr, int index, int total);

private slots:
    void onConnected();
    void onDisconnected();
    void onReadyRead();
    void onSocketError(QAbstractSocket::SocketError err);
    void onPingTimer();

private:
    void fail(const QString& msg);
    void buildCandidates(const QString& host, quint16 port);
    bool tryNextCandidate();

    QTcpSocket m_socket;
    QByteArray m_buffer;
    QTimer m_pingTimer;
    QString m_lastError;
    bool m_handshakeDone = false;

    QString m_host;
    quint16 m_port = 0;
    QList<QHostAddress> m_candidates;
    int m_candidateIndex = -1;
    bool m_connecting = false;
    QStringList m_tried;
};
