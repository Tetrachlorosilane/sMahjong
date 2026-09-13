#include "ui/TileFont.h"

#include <QCoreApplication>
#include <QFile>
#include <QFontDatabase>
#include <QRegularExpression>
#include <QStringList>

namespace tilefont {
namespace {

const char* kFontFile = "I.MahjongJP.otf";

bool g_tried = false;
bool g_ok = false;
QString g_family;

} // namespace

bool load()
{
    if (g_tried) {
        return g_ok;
    }
    g_tried = true;

    int id = -1;
    // 1) exe 同级 fonts/（构建时拷入；换字体不用重新编译）
    const QString beside = QCoreApplication::applicationDirPath()
            + QStringLiteral("/fonts/") + QLatin1String(kFontFile);
    if (QFile::exists(beside)) {
        id = QFontDatabase::addApplicationFont(beside);
    }
    // 2) qrc 内嵌兜底
    if (id < 0) {
        const QString res = QStringLiteral(":/fonts/") + QLatin1String(kFontFile);
        if (QFile::exists(res)) {
            id = QFontDatabase::addApplicationFont(res);
        }
    }
    if (id < 0) {
        return false;
    }
    const QStringList fams = QFontDatabase::applicationFontFamilies(id);
    if (fams.isEmpty()) {
        return false;
    }
    g_family = fams.first();
    g_ok = true;
    return true;
}

bool available()
{
    return load();
}

QString family()
{
    load();
    return g_family;
}

bool isRenderableSchematic(const QString& s)
{
    if (s.isEmpty()) {
        return false;
    }
    // 允许：牌码（数字+花色）、赤牌简写 0x、后缀 a、横置符 -、空格分隔。
    // 任何其他字符都可能被字体当作字面字符画出来 —— 与其画出"看着像牌其实不是"的图，
    // 不如让调用方回退到汉字表示。
    static const QRegularExpression re(QStringLiteral("^(?:[0-9][mpsz]a?-?|\\s)+$"));
    return re.match(s).hasMatch();
}

} // namespace tilefont
