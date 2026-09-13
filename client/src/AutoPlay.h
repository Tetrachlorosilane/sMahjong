#pragma once

// 联调自走模式：让 Qt 客户端真的连上服务端打一局，用于端到端验证。
//   mahjong-client.exe --autoplay <host> <port> [--name 名字] [--timeout 秒]
// 退出码：0 = 正常终局；1 = 超时/错误。

#include <QFile>
#include <QJsonObject>
#include <QObject>
#include <QString>
#include <QTimer>

#include "net/NetClient.h"

class AutoPlay : public QObject
{
    Q_OBJECT
public:
    AutoPlay(QString host, quint16 port, QString name, int timeoutSec, QString logPath,
             QObject* parent = nullptr);

    int exitCode() const { return m_exitCode; }

public slots:
    void start();

signals:
    void finished(int code);

private slots:
    void onConnected();
    void onDisconnected();
    void onEvent(const QJsonObject& ev);

private:
    void send(const QJsonObject& obj);
    void handleAsk(const QJsonObject& ev);
    void log(const QString& line);
    void done(int code);

    NetClient m_net;
    QString m_host;
    quint16 m_port;
    QString m_name;
    QFile m_logFile;
    QTimer m_timeout;
    int m_exitCode = 1;
    int m_mySeat = -1;
    long long m_myPid = 0;
    int m_asks = 0;
    int m_discards = 0;
    int m_draws = 0;
    int m_rounds = 0;
    int m_agari = 0;
    int m_ryuukyoku = 0;
    bool m_readySent = false;
    bool m_done = false;
};
