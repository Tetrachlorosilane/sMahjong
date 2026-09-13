#include "net/NetClient.h"
#include "net/Protocol.h"

#include "i18n/Lang.h"

#include <QHostInfo>
#include <QJsonObject>
#include <QStringList>

namespace {
// 心跳间隔（docs/PROTOCOL.md §0：客户端可选发 ping）
constexpr int kPingIntervalMs = 20000;
} // namespace

NetClient::NetClient(QObject* parent)
    : QObject(parent)
{
    m_pingTimer.setInterval(kPingIntervalMs);
    connect(&m_pingTimer, &QTimer::timeout, this, &NetClient::onPingTimer);

    connect(&m_socket, &QTcpSocket::connected, this, &NetClient::onConnected);
    connect(&m_socket, &QTcpSocket::disconnected, this, &NetClient::onDisconnected);
    connect(&m_socket, &QTcpSocket::readyRead, this, &NetClient::onReadyRead);
    connect(&m_socket, &QAbstractSocket::errorOccurred, this, &NetClient::onSocketError);
}

NetClient::~NetClient()
{
    m_pingTimer.stop();
    if (m_socket.state() != QAbstractSocket::UnconnectedState) {
        m_socket.blockSignals(true);
        m_socket.abort();
    }
}

void NetClient::connectToServer(const QString& host, quint16 port)
{
    if (m_socket.state() != QAbstractSocket::UnconnectedState)
        m_socket.abort();
    m_buffer.clear();
    m_lastError.clear();
    m_handshakeDone = false;
    m_host = host;
    m_port = port;
    buildCandidates(host, port);
    m_tried.clear();
    m_candidateIndex = -1;
    m_connecting = true;
    if (!tryNextCandidate()) {
        m_connecting = false;
        fail(lang::t("ui.net.bad_host").arg(host));
    }
}

// 组装候选地址列表。顺序原则：
//   1) 用户显式写的 IP 优先；
//   2) 若写的是回环地址，自动把另一族的回环也加进来（127.0.0.1 ⇄ ::1）——
//      这是 WSL 场景的关键：服务端在 WSL 里，转发只绑了 IPv6 回环；
//   3) 主机名则展开 DNS 的**全部**结果（localhost 通常同时给出 ::1 与 127.0.0.1）。
void NetClient::buildCandidates(const QString& host, quint16 port)
{
    Q_UNUSED(port);
    m_candidates.clear();
    const QHostAddress direct(host);
    if (!direct.isNull()) {
        m_candidates << direct;
        if (direct == QHostAddress(QHostAddress::LocalHost))
            m_candidates << QHostAddress(QHostAddress::LocalHostIPv6);
        else if (direct == QHostAddress(QHostAddress::LocalHostIPv6))
            m_candidates << QHostAddress(QHostAddress::LocalHost);
        return;
    }
    const QHostInfo info = QHostInfo::fromName(host);
    for (const QHostAddress& a : info.addresses()) {
        if (!m_candidates.contains(a))
            m_candidates << a;
    }
    if (m_candidates.isEmpty())
        m_candidates << QHostAddress(host);
}

bool NetClient::tryNextCandidate()
{
    while (++m_candidateIndex < m_candidates.size()) {
        const QHostAddress addr = m_candidates.at(m_candidateIndex);
        if (m_tried.contains(addr.toString()))
            continue;
        m_tried << addr.toString();
        emit tryingAddress(addr.toString(), m_candidateIndex, m_candidates.size());
        m_socket.connectToHost(addr, m_port);
        return true;
    }
    return false;
}

void NetClient::disconnectFromServer()
{
    m_connecting = false;
    m_pingTimer.stop();
    if (m_socket.state() == QAbstractSocket::UnconnectedState) {
        emit disconnected();
        return;
    }
    m_socket.disconnectFromHost();
    if (m_socket.state() != QAbstractSocket::UnconnectedState)
        m_socket.waitForDisconnected(500);
}

bool NetClient::isConnected() const
{
    return m_socket.state() == QAbstractSocket::ConnectedState;
}

void NetClient::sendCommand(const QJsonObject& obj)
{
    if (!isConnected()) {
        emit errorOccurred(lang::t("ui.net.not_connected"));
        return;
    }
    const QByteArray line = proto::encodeLine(obj);
    if (line.size() > proto::MaxLineBytes) {
        emit errorOccurred(lang::t("ui.net.tx_too_large"));
        return;
    }
    m_socket.write(line);
    m_socket.flush();
}

void NetClient::onConnected()
{
    m_lastError.clear();
    m_connecting = false;
    m_pingTimer.start();
    emit connected();
}

void NetClient::onDisconnected()
{
    m_pingTimer.stop();
    m_buffer.clear();
    if (!m_handshakeDone)
        return; // 握手前断开由 errorOccurred 负责提示
    emit disconnected();
}

void NetClient::onSocketError(QAbstractSocket::SocketError err)
{
    // 还在逐个试候选地址的阶段：换下一个，不要立刻报错
    if (m_connecting && err != QAbstractSocket::RemoteHostClosedError) {
        m_socket.abort();
        if (tryNextCandidate())
            return;
        m_connecting = false;
        const QString tried = m_tried.join(QStringLiteral(", "));
        if (err == QAbstractSocket::ConnectionRefusedError) {
            // 整段排查提示是**一条文案**（多行）：拆成多条 key 会让行数与语序没法翻译。
            fail(lang::t(QStringLiteral("ui.net.refused_help"))
                     .arg(m_host)
                     .arg(m_port)
                     .arg(tried));
        } else {
            fail(lang::t("ui.net.connect_failed")
                     .arg(m_host)
                     .arg(m_port)
                     .arg(m_socket.errorString())
                     .arg(tried));
        }
        return;
    }
    fail(m_socket.errorString());
}

void NetClient::onPingTimer()
{
    if (!isConnected())
        return;
    QJsonObject ping;
    ping.insert(QStringLiteral("cmd"), QStringLiteral("ping"));
    sendCommand(ping);
}

void NetClient::onReadyRead()
{
    m_buffer.append(m_socket.readAll());

    // 单条上限保护：未找到换行且缓冲已经超限 → 断链
    int newline = m_buffer.indexOf('\n');
    if (newline < 0 && m_buffer.size() > proto::MaxLineBytes) {
        fail(lang::t("ui.net.rx_too_large"));
        m_socket.abort();
        return;
    }

    // 先按下标**一次扫完**、只做一次 remove：旧写法在循环里逐行 remove(0, n)，
    // 每行都把剩余缓冲整体前移（memmove）→ 报文一多就是 O(n²)（大报文时会卡住）。
    int consumed = 0;
    QList<QByteArray> lines;
    for (;;) {
        const int nl = m_buffer.indexOf('\n', consumed);
        if (nl < 0)
            break;
        QByteArray line = m_buffer.mid(consumed, nl - consumed);
        consumed = nl + 1;
        if (line.endsWith('\r'))
            line.chop(1);
        if (line.trimmed().isEmpty())
            continue; // 空行忽略
        lines.append(line);
    }
    if (consumed > 0)
        m_buffer.remove(0, consumed);

    // 行收集完再解析/派发：这样 emit 期间 m_buffer 一定是「只含未处理数据」的干净状态，
    // 槽函数里若开了嵌套事件循环（如弹窗）再触发 readyRead 也不会互相错位。
    for (const QByteArray& line : lines) {
        bool ok = false;
        const QJsonObject obj = proto::decodeLine(line, &ok);
        if (!ok) {
            emit errorOccurred(lang::t("ui.net.bad_packet")
                                   .arg(QString::fromUtf8(line.left(200))));
            continue;
        }
        if (obj.value(QStringLiteral("ev")).toString() == QLatin1String("hello_ok"))
            m_handshakeDone = true;
        emit eventReceived(obj);
    }
}

void NetClient::fail(const QString& msg)
{
    m_lastError = msg;
    m_connecting = false;
    m_pingTimer.stop();
    emit errorOccurred(msg);
}
