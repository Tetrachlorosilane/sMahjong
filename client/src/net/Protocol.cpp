#include "net/Protocol.h"

#include <QDebug>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>

namespace proto {

QByteArray encodeLine(const QJsonObject& obj)
{
    QByteArray out = QJsonDocument(obj).toJson(QJsonDocument::Compact);
    out.append('\n');
    return out;
}

QJsonObject decodeLine(const QByteArray& line, bool* ok)
{
    if (ok)
        *ok = false;
    const QByteArray trimmed = line.trimmed();
    if (trimmed.isEmpty())
        return QJsonObject();

    QJsonParseError err {};
    const QJsonDocument doc = QJsonDocument::fromJson(trimmed, &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject())
        return QJsonObject();
    if (ok)
        *ok = true;
    return doc.object();
}

QStringList stringList(const QJsonValue& v)
{
    QStringList out;
    if (!v.isArray())
        return out;
    const QJsonArray arr = v.toArray();
    out.reserve(arr.size());
    for (const QJsonValue& item : arr) {
        if (item.isString())
            out.append(item.toString());
    }
    return out;
}

QStringList stringList(const QJsonValue& v, int maxItems, const char* what)
{
    QStringList out = stringList(v);
    if (maxItems > 0 && out.size() > maxItems) {
        // 日志走 qWarning（不进语言文件，见 AGENTS §6）
        qWarning("mahjong: 协议字段 %s 有 %d 项，超过上限 %d，已截断", what, int(out.size()), maxItems);
        out = out.mid(0, maxItems);
    }
    return out;
}

QVector<int> intVector(const QJsonValue& v)
{
    QVector<int> out;
    if (!v.isArray())
        return out;
    const QJsonArray arr = v.toArray();
    out.reserve(arr.size());
    for (const QJsonValue& item : arr)
        out.append(item.toInt());
    return out;
}

QVector<bool> boolVector(const QJsonValue& v)
{
    QVector<bool> out;
    if (!v.isArray())
        return out;
    const QJsonArray arr = v.toArray();
    out.reserve(arr.size());
    for (const QJsonValue& item : arr)
        out.append(item.toBool(false));
    return out;
}

bool AskInfo::hasOption(const QString& type) const
{
    for (const QJsonObject& o : options) {
        if (o.value(QStringLiteral("type")).toString() == type)
            return true;
    }
    return false;
}

QJsonObject AskInfo::option(const QString& type) const
{
    for (const QJsonObject& o : options) {
        if (o.value(QStringLiteral("type")).toString() == type)
            return o;
    }
    return QJsonObject();
}

AskInfo parseAsk(const QJsonObject& ev)
{
    AskInfo info;
    if (ev.value(QStringLiteral("ev")).toString() != QLatin1String("ask"))
        return info;

    info.valid = true;
    info.kind = ev.value(QStringLiteral("kind")).toString();
    info.seat = ev.value(QStringLiteral("seat")).toInt(-1);
    info.from = ev.value(QStringLiteral("from")).toInt(-1);
    info.tile = ev.value(QStringLiteral("tile")).toString();
    // `deadline_ms` 是服务端下发的相对剩余毫秒。**先夹到 [0, MaxAskDeadlineMs] 再转 qint64**：
    // 越界的 double→qint64 是未定义行为，而调用方会把它加到当前时刻上（见 AGENTS §2.2）。
    const double rawDeadline = ev.value(QStringLiteral("deadline_ms")).toDouble(0);
    info.deadlineMs = (rawDeadline > 0.0)
            ? static_cast<qint64>(qMin(rawDeadline, double(MaxAskDeadlineMs)))
            : 0;
    info.tilesLeft = ev.value(QStringLiteral("tiles_left")).toInt(-1);

    const QJsonArray arr = ev.value(QStringLiteral("options")).toArray();
    // 选项数也要封顶：选项是「动态增删按钮」的输入，超长数组会把操作栏撑爆。
    const int optCount = qMin(int(arr.size()), MaxAskOptions);
    if (arr.size() > MaxAskOptions)
        qWarning("mahjong: ask.options 有 %d 项，超过上限 %d，已截断", int(arr.size()), MaxAskOptions);
    info.options.reserve(optCount);
    for (int i = 0; i < optCount; ++i) {
        const QJsonValue item = arr.at(i);
        if (item.isObject())
            info.options.append(item.toObject());
    }
    return info;
}

} // namespace proto
