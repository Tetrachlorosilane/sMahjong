#include "AutoPlay.h"

#include "i18n/Lang.h"

#include <QCoreApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTextStream>

namespace {

QTextStream& out()
{
    static QTextStream s(stdout);
    return s;
}

// 从 options 里找指定 type 的项
QJsonObject findOption(const QJsonArray& options, const QString& type)
{
    for (const QJsonValue& v : options) {
        const QJsonObject o = v.toObject();
        if (o.value(QStringLiteral("type")).toString() == type)
            return o;
    }
    return {};
}

} // namespace

AutoPlay::AutoPlay(QString host, quint16 port, QString name, int timeoutSec, QString logPath,
                   QObject* parent)
    : QObject(parent)
    , m_host(std::move(host))
    , m_port(port)
    , m_name(std::move(name))
{
    // GUI 子系统程序在重定向场景下拿不到 stdout，因此同时写一份日志文件
    m_logFile.setFileName(logPath);
    if (!m_logFile.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text))
        m_logFile.setFileName(QString());
    connect(&m_net, &NetClient::connected, this, &AutoPlay::onConnected);
    connect(&m_net, &NetClient::disconnected, this, &AutoPlay::onDisconnected);
    connect(&m_net, &NetClient::eventReceived, this, &AutoPlay::onEvent);
    connect(&m_net, &NetClient::errorOccurred, this, [this](const QString& msg) {
        log(QStringLiteral("[autoplay] 网络错误: ") + msg);
    });
    m_timeout.setSingleShot(true);
    connect(&m_timeout, &QTimer::timeout, this, [this]() {
        log(QStringLiteral("[autoplay] 超时，统计: ask=%1 discard=%2 draw=%3 round=%4 agari=%5 ryuukyoku=%6")
                .arg(m_asks)
                .arg(m_discards)
                .arg(m_draws)
                .arg(m_rounds)
                .arg(m_agari)
                .arg(m_ryuukyoku));
        done(1);
    });
    // 超时秒数来自命令行：这里再夹一次。负间隔 Qt 直接不启动计时器
    //（autoplay 会永远挂着不退出），超大值乘 1000 也会溢出 int。
    m_timeout.start(qBound(1, timeoutSec, 3600) * 1000);
}

void AutoPlay::log(const QString& line)
{
    out() << line << '\n';
    out().flush();
    if (m_logFile.isOpen()) {
        m_logFile.write(line.toUtf8());
        m_logFile.write("\n");
        m_logFile.flush();
    }
}

void AutoPlay::start()
{
    log(QStringLiteral("[autoplay] 连接 %1:%2 ...").arg(m_host).arg(m_port));
    m_net.connectToServer(m_host, m_port);
}

void AutoPlay::send(const QJsonObject& obj)
{
    m_net.sendCommand(obj);
}

void AutoPlay::onConnected()
{
    log(QStringLiteral("[autoplay] TCP 已连接，发送 hello"));
    send(QJsonObject { { QStringLiteral("cmd"), QStringLiteral("hello") },
                       { QStringLiteral("name"), m_name },
                       { QStringLiteral("ver"), 1 } });
}

void AutoPlay::onDisconnected()
{
    if (!m_done) {
        log(QStringLiteral("[autoplay] 连接断开"));
        done(m_exitCode);
    }
}

void AutoPlay::done(int code)
{
    if (m_done)
        return;
    m_done = true;
    m_exitCode = code;
    if (code == 0)
        log(QStringLiteral("[autoplay] AUTOPLAY PASS"));
    else
        log(QStringLiteral("[autoplay] AUTOPLAY FAIL (code=%1)").arg(code));
    m_timeout.stop();
    emit finished(code);
}

void AutoPlay::handleAsk(const QJsonObject& ev)
{
    const int seat = ev.value(QStringLiteral("seat")).toInt(-1);
    if (seat != m_mySeat)
        return;
    ++m_asks;
    const QJsonArray options = ev.value(QStringLiteral("options")).toArray();
    const QString kind = ev.value(QStringLiteral("kind")).toString();
    {
        QStringList types;
        for (const QJsonValue& v : options)
            types << v.toObject().value(QStringLiteral("type")).toString();
        log(QStringLiteral("[autoplay] ask kind=%1 seat=%2 options=[%3]")
                .arg(kind)
                .arg(seat)
                .arg(types.join(QLatin1Char(','))));
    }

    QJsonObject action;
    const QJsonObject tsumo = findOption(options, QStringLiteral("tsumo"));
    const QJsonObject ron = findOption(options, QStringLiteral("ron"));
    const QJsonObject riichi = findOption(options, QStringLiteral("riichi"));
    if (kind == QLatin1String("turn") && !tsumo.isEmpty()) {
        action = QJsonObject { { QStringLiteral("type"), QStringLiteral("tsumo") } };
    } else if (!ron.isEmpty()) {
        action = QJsonObject { { QStringLiteral("type"), QStringLiteral("ron") } };
    } else if (kind == QLatin1String("turn") && !riichi.isEmpty()) {
        // 会立直：立直后手牌锁死听牌，必定能等到和牌机会（用于验证和牌按钮）
        const QJsonArray rt = riichi.value(QStringLiteral("tiles")).toArray();
        if (!rt.isEmpty()) {
            action = QJsonObject { { QStringLiteral("type"), QStringLiteral("riichi") },
                                   { QStringLiteral("tile"), rt.at(0).toString() } };
        }
    } else {
        const QJsonObject disc = findOption(options, QStringLiteral("discard"));
        const QJsonArray tiles = disc.value(QStringLiteral("tiles")).toArray();
        if (!tiles.isEmpty()) {
            action = QJsonObject { { QStringLiteral("type"), QStringLiteral("discard") },
                                   { QStringLiteral("tile"), tiles.at(0).toString() } };
        } else {
            action = QJsonObject { { QStringLiteral("type"), QStringLiteral("pass") } };
        }
    }
    action.insert(QStringLiteral("cmd"), QStringLiteral("action"));
    if (ev.contains(QStringLiteral("ask_id")))
        action.insert(QStringLiteral("ask_id"), ev.value(QStringLiteral("ask_id")));
    send(action);
}

void AutoPlay::onEvent(const QJsonObject& ev)
{
    const QString name = ev.value(QStringLiteral("ev")).toString();

    if (name == QLatin1String("hello_ok")) {
        m_myPid = static_cast<long long>(ev.value(QStringLiteral("pid")).toDouble());
        log(QStringLiteral("[autoplay] 握手成功 pid=%1").arg(m_myPid));
        QJsonObject rules;
        rules.insert(QStringLiteral("length"), QStringLiteral("tonpuu"));
        send(QJsonObject { { QStringLiteral("cmd"), QStringLiteral("create_room") },
                           { QStringLiteral("name"), QStringLiteral("Qt 自走测试房") },
                           { QStringLiteral("rules"), rules },
                           { QStringLiteral("fill_bots"), 3 } });
        return;
    }
    if (name == QLatin1String("room_joined")) {
        m_mySeat = ev.value(QStringLiteral("seat")).toInt(-1);
        log(QStringLiteral("[autoplay] 入座 seat=%1").arg(m_mySeat));
        if (!m_readySent) {
            m_readySent = true;
            send(QJsonObject { { QStringLiteral("cmd"), QStringLiteral("ready") },
                               { QStringLiteral("ready"), true } });
        }
        return;
    }
    if (name == QLatin1String("round_start")) {
        ++m_rounds;
        m_mySeat = ev.value(QStringLiteral("seat")).toInt(m_mySeat);
        const QJsonObject round = ev.value(QStringLiteral("round")).toObject();
        const QJsonArray hand = ev.value(QStringLiteral("hand")).toArray();
        QStringList tiles;
        for (const QJsonValue& v : hand)
            tiles << v.toString();
        log(QStringLiteral("[autoplay] 第 %1 局 %2%3局 %4本场  手牌[%5]: %6")
                .arg(m_rounds)
                .arg(round.value(QStringLiteral("bakaze")).toString())
                .arg(round.value(QStringLiteral("kyoku")).toInt())
                .arg(round.value(QStringLiteral("honba")).toInt())
                .arg(tiles.size())
                .arg(tiles.join(QLatin1Char(' '))));
        return;
    }
    if (name == QLatin1String("draw")) {
        ++m_draws;
        return;
    }
    if (name == QLatin1String("discard")) {
        ++m_discards;
        return;
    }
    if (name == QLatin1String("ask")) {
        handleAsk(ev);
        return;
    }
    if (name == QLatin1String("agari")) {
        ++m_agari;
        QStringList ys;
        const QJsonArray yaku = ev.value(QStringLiteral("yaku")).toArray();
        for (const QJsonValue& v : yaku) {
            const QJsonObject y = v.toObject();
            // 服务端只发 ASCII 码（code + 参数化役种的 tile），日志翻成中文便于人读。
            // 认不出的码由 lang::yakuText 原样返回 —— 日志里一眼就能看出词表缺口。
            const QString yname = lang::yakuText(y.value(QStringLiteral("code")).toString(),
                                                 y.value(QStringLiteral("tile")).toString());
            if (y.contains(QStringLiteral("yakuman")))
                ys << QStringLiteral("%1(%2倍役满)").arg(yname)
                          .arg(y.value(QStringLiteral("yakuman")).toInt());
            else
                ys << QStringLiteral("%1%2").arg(yname)
                          .arg(y.value(QStringLiteral("han")).toInt());
        }
        log(QStringLiteral("[autoplay] 和了 seat=%1 %2 %3 %4番%5符 → %6")
                .arg(ev.value(QStringLiteral("winner")).toInt())
                .arg(ev.value(QStringLiteral("tsumo")).toBool() ? QStringLiteral("自摸")
                                                                : QStringLiteral("荣和"))
                .arg(ys.join(QLatin1Char('+')))
                .arg(ev.value(QStringLiteral("han")).toInt())
                .arg(ev.value(QStringLiteral("fu")).toInt())
                .arg(QString(QJsonDocument(ev.value(QStringLiteral("score_delta")).toArray())
                                 .toJson(QJsonDocument::Compact))));
        return;
    }
    if (name == QLatin1String("ryuukyoku")) {
        ++m_ryuukyoku;
        log(QStringLiteral("[autoplay] 流局 %1")
                .arg(lang::code(QStringLiteral("reason."),
                                ev.value(QStringLiteral("reason")).toString())));
        return;
    }
    if (name == QLatin1String("round_end")) {
        log(QStringLiteral("[autoplay] 局终 分数=%1")
                .arg(QString(QJsonDocument(ev.value(QStringLiteral("scores")).toArray())
                                 .toJson(QJsonDocument::Compact))));
        return;
    }
    if (name == QLatin1String("game_end")) {
        const QJsonArray finalArr = ev.value(QStringLiteral("final")).toArray();
        long long sum = 0;
        const QJsonArray scores = ev.value(QStringLiteral("scores")).toArray();
        for (const QJsonValue& v : scores)
            sum += static_cast<long long>(v.toDouble());
        log(QStringLiteral("[autoplay] ===== 终局 ====="));
        for (const QJsonValue& v : finalArr) {
            const QJsonObject f = v.toObject();
            log(QStringLiteral("  %1位 %2 %3点 精算%4")
                    .arg(f.value(QStringLiteral("rank")).toInt())
                    .arg(f.value(QStringLiteral("name")).toString())
                    .arg(f.value(QStringLiteral("score")).toInt())
                    .arg(f.value(QStringLiteral("point")).toDouble()));
        }
        log(QStringLiteral("[autoplay] 统计: ask=%1 discard=%2 draw=%3 round=%4 agari=%5 ryuukyoku=%6")
                .arg(m_asks)
                .arg(m_discards)
                .arg(m_draws)
                .arg(m_rounds)
                .arg(m_agari)
                .arg(m_ryuukyoku));
        if (sum != 100000) {
            log(QStringLiteral("[autoplay] 点数和 %1 != 100000").arg(sum));
            done(1);
            return;
        }
        if (m_rounds < 1 || m_asks < 1) {
            log(QStringLiteral("[autoplay] 对局未真正开始"));
            done(1);
            return;
        }
        done(0);
        return;
    }
    if (name == QLatin1String("error")) {
        log(QStringLiteral("[autoplay] 服务端错误 %1: %2")
                .arg(ev.value(QStringLiteral("code")).toString())
                .arg(ev.value(QStringLiteral("msg")).toString()));
    }
}
