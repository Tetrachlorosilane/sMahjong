#include "model/AutoPolicy.h"

#include <QJsonArray>

namespace autopolicy {

QJsonObject optionOf(const QJsonObject& ask, const QString& type)
{
    const QJsonArray options = ask.value(QStringLiteral("options")).toArray();
    for (const QJsonValue& v : options) {
        const QJsonObject o = v.toObject();
        if (o.value(QStringLiteral("type")).toString() == type)
            return o;
    }
    return {};
}

QJsonObject decide(const Flags& f, const QJsonObject& ask, const QString& kind,
                   const QString& drawnTile)
{
    const bool canTsumo = !optionOf(ask, QStringLiteral("tsumo")).isEmpty();
    const bool canRon = !optionOf(ask, QStringLiteral("ron")).isEmpty();

    // ① 和牌截获一切自动动作。
    //    有和牌机会时**只有**「自动胡了」能替玩家动；没开就什么都不做、留给玩家点。
    //    这一条同时挡住两种事故：自动摸切把能胡的那张打出去、不吃碰杠把荣和 pass 掉。
    if (canTsumo || canRon) {
        if (!f.autoWin)
            return {};
        QJsonObject act;
        act.insert(QStringLiteral("type"),
                   canTsumo ? QStringLiteral("tsumo") : QStringLiteral("ron"));
        return act;
    }

    // ② 自动摸切：只在**轮到自己**时动手，打的是刚摸到的那张。
    //    摸到的牌不在「可打列表」里（立直后只能打摸牌、或服务端限制了某些牌）时，
    //    退化为列表第一张 —— 绝不发出不带 tile 的 discard。
    if (kind == QLatin1String("turn") && f.autoTsumogiri) {
        const QJsonArray tiles = optionOf(ask, QStringLiteral("discard"))
                                     .value(QStringLiteral("tiles"))
                                     .toArray();
        if (tiles.isEmpty())
            return {};
        QString tile = drawnTile;
        bool usable = !tile.isEmpty();
        if (usable) {
            usable = false;
            for (const QJsonValue& v : tiles) {
                if (v.toString() == tile) {
                    usable = true;
                    break;
                }
            }
        }
        if (!usable)
            tile = tiles.at(0).toString();
        QJsonObject act;
        act.insert(QStringLiteral("type"), QStringLiteral("discard"));
        act.insert(QStringLiteral("tile"), tile);
        return act;
    }

    // ③ 不吃碰杠：别人的舍张（含抢杠）一律 pass。
    //    走到这里说明没有荣和可和，pass 不会丢和牌机会。
    if (kind != QLatin1String("turn") && f.noCall
        && !optionOf(ask, QStringLiteral("pass")).isEmpty()) {
        QJsonObject act;
        act.insert(QStringLiteral("type"), QStringLiteral("pass"));
        return act;
    }

    return {};
}

} // namespace autopolicy
