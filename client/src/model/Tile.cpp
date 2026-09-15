#include "model/Tile.h"

#include "i18n/Lang.h"

namespace mj {

int kindOfTile(const QString& tile, bool* red)
{
    if (red)
        *red = false;
    if (tile.size() != 2)
        return -1;

    const QChar numChar = tile.at(0);
    const QChar suitChar = tile.at(1);
    if (numChar < QLatin1Char('0') || numChar > QLatin1Char('9'))
        return -1;

    int base = -1;
    switch (suitChar.unicode()) {
    case 'm':
        base = 0;
        break;
    case 'p':
        base = 9;
        break;
    case 's':
        base = 18;
        break;
    case 'z':
        base = 27;
        break;
    default:
        return -1;
    }

    int num = numChar.digitValue();
    if (num == 0) {
        // 赤宝牌只存在于数牌的五
        if (base == 27)
            return -1;
        if (red)
            *red = true;
        num = 5;
    }

    if (base == 27) {
        if (num < 1 || num > 7)
            return -1;
    } else {
        if (num < 1 || num > 9)
            return -1;
    }
    return base + num - 1;
}

QString tileString(int kind, bool red)
{
    if (!isValidKind(kind))
        return QString();
    int base = 0;
    if (kind < 9)
        base = 0;
    else if (kind < 18)
        base = 9;
    else if (kind < 27)
        base = 18;
    else
        base = 27;

    const int num = kind - base + 1;
    const bool isAka = red && base != 27 && num == 5;
    QString s;
    s.append(isAka ? QLatin1Char('0') : QChar(QLatin1Char('0').unicode() + num));
    s.append(suitOfKind(kind));
    return s;
}

bool isRedTile(const QString& tile)
{
    bool red = false;
    kindOfTile(tile, &red);
    return red;
}

QChar suitOfKind(int kind)
{
    if (kind < 0 || kind >= 27) {
        if (kind >= 27 && kind < KindCount)
            return QLatin1Char('z');
        return QChar();
    }
    if (kind < 9)
        return QLatin1Char('m');
    if (kind < 18)
        return QLatin1Char('p');
    return QLatin1Char('s');
}

int numberOfKind(int kind)
{
    if (!isValidKind(kind))
        return 0;
    if (kind < 27)
        return (kind % 9) + 1;
    return (kind - 27) + 1;
}

QString tileLabel(const QString& tile)
{
    bool red = false;
    const int kind = kindOfTile(tile, &red);
    if (kind < 0)
        return QStringLiteral("?");

    // 牌名**不再是写在代码里的汉字**，而是语言文件里的 `tile.<牌码>`（一 locale 一份）。
    // 这里是全仓唯一的牌名出口：ActionBar / ResultDialog / TileRenderer 都走它，
    // 所以换语言只需换 i18n/<locale>.json，不必动 C++。
    // 非法牌码（上面已经挡掉）与语言文件里没登记的码由 tileName 原样返回牌码。
    return lang::tileName(tileString(kind, red));
}

} // namespace mj
