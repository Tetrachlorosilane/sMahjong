#pragma once

// NDJSON 协议编解码（docs/PROTOCOL.md §0 / §3.6）
// 一行一个 JSON 对象，UTF-8，'\n' 结尾；空行忽略。

#include <QByteArray>
#include <QJsonObject>
#include <QJsonValue>
#include <QString>
#include <QStringList>
#include <QVector>

namespace proto {

// 协议版本号（握手 {"cmd":"hello","ver":1}）
constexpr int Version = 1;

// 单条报文上限 1 MiB（docs/PROTOCOL.md §0）
constexpr int MaxLineBytes = 1024 * 1024;

// 协议数组的长度上限（docs/PROTOCOL.md §3：每个数组都有确定上限）。
// 超限只可能来自损坏或恶意的服务端 —— 客户端的策略是**截断 + qWarning**，
// 绝不静默照单全收：一个超大数组就能把手牌/牌河无限撑大（内存 + 每帧绘制都跟着涨）。
constexpr int MaxHandTiles = 14;          // 暗牌 ≤ 13 + 摸牌 1（庄家配牌 14 张）
constexpr int MaxDoraIndicators = 5;      // 宝牌指示牌：1 + 四个杠各 1
constexpr int MaxMeldTiles = 4;           // 一组副露最多 4 张（杠）
constexpr int MaxMeldsPerSeat = 4;        // 每人最多 4 组副露
constexpr int MaxDiscardsPerSeat = 60;    // 每人牌河上限（正常一局远小于它）
constexpr int MaxAskOptions = 16;         // 一次询问的动作选项上限

// 思考时间上限：`deadline_ms` 是服务端给的相对剩余毫秒，先夹到这个区间再转 qint64。
constexpr qint64 MaxAskDeadlineMs = 600000;   // 10 分钟，远超任何合法思考时间

// 序列化一个对象为一行 NDJSON（追加 '\n'）
QByteArray encodeLine(const QJsonObject& obj);

// 解析一行（不含结尾 '\n'）；失败时 *ok = false
QJsonObject decodeLine(const QByteArray& line, bool* ok);

// 便捷取值（缺失或类型不符返回缺省值）
QStringList stringList(const QJsonValue& v);
// 同上，但带长度上限：超过 maxItems 即截断并 qWarning（`what` 是字段名，只用于日志）。
// 协议里每个数组都有确定上限，超限一律按损坏/恶意报文处理。
QStringList stringList(const QJsonValue& v, int maxItems, const char* what);
QVector<int> intVector(const QJsonValue& v);
QVector<bool> boolVector(const QJsonValue& v);

// §3.6 ask 事件的解析结果
struct AskInfo {
    bool valid = false;
    QString kind;                  // turn / claim / chankan
    int seat = -1;                 // 被询问者座位
    int from = -1;                 // claim/chankan：被鸣/被和者座位
    QString tile;                  // claim/chankan：被鸣/被和的那张牌
    qint64 deadlineMs = 0;         // 相对剩余毫秒（<=0 表示不限时）
    int tilesLeft = -1;            // turn：牌山剩余
    QVector<QJsonObject> options;  // 可用动作列表

    bool hasOption(const QString& type) const;
    QJsonObject option(const QString& type) const;
};

// 解析 ask 事件；非 ask 事件返回 valid=false
AskInfo parseAsk(const QJsonObject& ev);

} // namespace proto
