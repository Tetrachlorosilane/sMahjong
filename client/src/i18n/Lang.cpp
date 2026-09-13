#include "i18n/Lang.h"

#include <QCoreApplication>
#include <QDebug>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcessEnvironment>
#include <QRegularExpression>
#include <QSet>

namespace {

QHash<QString, QString> g_text;
QString g_locale;
int g_misses = 0;
QStringList g_missing;

// 语言文件是纯文本资源，正常几十 KB。给个上限：文件被换成超大文件时
// 不至于一次性 readAll() 把内存吃光（locale 是可从命令行/环境变量进来的输入）。
constexpr qint64 kMaxLangFileBytes = 1024 * 1024;

/**
 * locale 会被**拼进文件路径**（`<exe>/i18n/<locale>.json`），而它来自
 * `--lang` / `DSH_MAHJONG_LANG`，所以必须先做严格校验再碰文件系统 ——
 * 否则 `--lang ../../../x` 这类值就成了目录穿越。
 * 合法形态：2~8 个字母/下划线，可再跟 `_` + 两个字母（如 `zh_CN`）。
 */
bool isSafeLocale(const QString& locale)
{
    static const QRegularExpression re(QStringLiteral("^[A-Za-z_]{2,8}(_[A-Za-z]{2})?$"));
    return re.match(locale).hasMatch();
}

void noteMiss(const QString& key)
{
    g_misses++;
    if (!g_missing.contains(key))
        g_missing.append(key);
}

bool ingest(const QByteArray& bytes, const QString& locale)
{
    QJsonParseError err {};
    const QJsonDocument doc = QJsonDocument::fromJson(bytes, &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject())
        return false;

    g_text.clear();
    const QJsonObject obj = doc.object();
    for (auto it = obj.begin(); it != obj.end(); ++it) {
        if (it.value().isString())
            g_text.insert(it.key(), it.value().toString());
    }
    g_locale = locale;
    return true;
}

} // namespace

namespace lang {

bool loadFromJson(const QString& json, const QString& locale)
{
    return ingest(json.toUtf8(), locale);
}

bool load(const QString& localeWanted)
{
    QString locale = localeWanted;
    if (locale.isEmpty())
        locale = QProcessEnvironment::systemEnvironment().value(QStringLiteral("DSH_MAHJONG_LANG"));
    if (locale.isEmpty())
        locale = QStringLiteral("zh_CN");

    // locale 会拼进路径 → 先校验，非法值直接拒绝（绝不落到文件系统调用上）
    if (!isSafeLocale(locale)) {
        qWarning("mahjong: 非法 locale \"%s\"（只接受字母/下划线，如 zh_CN），拒绝加载语言文件",
                 qUtf8Printable(locale));
        g_text.clear();
        g_locale.clear();
        return false;
    }

    const QString name = locale + QStringLiteral(".json");

    // 读文件时**按上限截断读取**（不用 QFile::size()：对管道之类特殊文件它不可靠）：
    // 最多读「上限 + 1」字节，读满就说明文件超限 —— 拒绝，而不是把它整块塞进内存。
    auto readCapped = [](QFile& file, QByteArray* out) {
        *out = file.read(kMaxLangFileBytes + 1);
        if (out->size() > kMaxLangFileBytes) {
            qWarning("mahjong: 语言文件超过 %lld 字节上限，拒绝加载",
                     static_cast<long long>(kMaxLangFileBytes));
            return false;
        }
        return true;
    };

    // ① exe 同级的 i18n/（**优先**：改文案不用重编译，与 tiles/fonts 同一套约定）
    const QString external = QCoreApplication::applicationDirPath()
            + QStringLiteral("/i18n/") + name;
    QFile f(external);
    QByteArray bytes;
    if (f.open(QIODevice::ReadOnly) && readCapped(f, &bytes) && ingest(bytes, locale))
        return true;

    // ② qrc 兜底（整个 i18n/ 目录被删也不会白屏，只是文案退回 key）
    QFile q(QStringLiteral(":/i18n/") + name);
    bytes.clear();
    if (q.open(QIODevice::ReadOnly) && readCapped(q, &bytes) && ingest(bytes, locale))
        return true;

    // ③ 都没有：保持空表 → t() 原样返回 key（可见但不算崩）
    g_text.clear();
    g_locale.clear();
    return false;
}

QString locale()
{
    return g_locale;
}

QString t(const QString& key)
{
    const auto it = g_text.constFind(key);
    if (it != g_text.constEnd())
        return it.value();
    noteMiss(key);
    return key;
}

QString t(const QString& key, const QString& a)
{
    return t(key).arg(a);
}

bool has(const QString& key)
{
    return g_text.contains(key);
}

QString tileName(const QString& code)
{
    const QString key = QStringLiteral("tile.") + code;
    return has(key) ? t(key) : code;
}

QString code(const QString& prefix, const QString& rawCode, const QString& arg)
{
    if (rawCode.isEmpty())
        return QString();   // 空码：交给调用方回退到老服务端的中文字段
    const QString key = prefix + rawCode;
    if (has(key))
        return arg.isEmpty() ? t(key) : t(key, arg);
    // 认不出的码：原样显示（带参数就附在括号里），便于发现"服务端加了码、语言文件没跟上"
    return arg.isEmpty()
            ? rawCode
            : rawCode + QStringLiteral(" (") + arg + QLatin1Char(')');
}

QString yakuText(const QString& code, const QString& tile)
{
    const QString key = QStringLiteral("yaku.") + code;
    // 认不出的役种码**原样显示**（而不是裸键 `yaku.xxx`）：既看得见、又能直接定位。
    // 词表缺口的兜底在服务端（YakuCodes.misses）与 tools/check-i18n.mjs，不在这里。
    if (!has(key))
        return code;
    // 参数化役种（役牌/场风/自风）：模板里带 %1，用牌码的显示名填充
    if (!tile.isEmpty())
        return t(key, tileName(tile));
    return t(key);
}

int misses()
{
    return g_misses;
}

void resetMisses()
{
    g_misses = 0;
    g_missing.clear();
}

int keyCount()
{
    return g_text.size();
}

QStringList keys()
{
    QStringList out(g_text.keys());
    out.sort();
    return out;
}

QStringList missingKeys()
{
    return g_missing;
}

} // namespace lang
