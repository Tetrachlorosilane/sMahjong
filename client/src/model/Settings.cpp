#include "model/Settings.h"

#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QJsonValue>
#include <QStandardPaths>

namespace {

/** 设置文件里我们**管**的键；其余键原样保留（见 Settings::extra）。 */
const char* const kKnownKeys[] = {"host", "port", "name", "uuid", "pack", "sfx", "sfx_volume"};

bool isKnownKey(const QString& k)
{
    for (const char* s : kKnownKeys) {
        if (k == QLatin1String(s)) {
            return true;
        }
    }
    return false;
}

/** 昵称上限与服务端一致（`Json.clampCodePoints(name, 24)`），按**码点**数。 */
int codePointCount(const QString& s)
{
    return int(s.toUcs4().size());
}

/** 有没有控制字符（换行、制表……）：这类值会让地址/昵称在界面与报文里都变味。 */
bool hasControlChar(const QString& s)
{
    for (const QChar& c : s) {
        if (c.unicode() < 0x20 || c.unicode() == 0x7F) {
            return true;
        }
    }
    return false;
}

/**
 * uuid 的形状：36 字符、位置 8/13/18/23 是 `-`、其余是十六进制。
 *
 * <p>与服务端 `PlayerStore.validUuid` **同一把尺子**（那边是权威；这边只是为了
 * "设置文件被手改坏了"时不把垃圾发上去、也不把垃圾当成自己的身份留着）。
 * 大小写都收：服务端反正会规范成小写。
 */
bool isUuidShape(const QString& s)
{
    if (s.size() != 36) {
        return false;
    }
    for (int i = 0; i < 36; ++i) {
        const QChar c = s.at(i);
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (c != QLatin1Char('-')) {
                return false;
            }
            continue;
        }
        const bool hex = (c >= QLatin1Char('0') && c <= QLatin1Char('9'))
                || (c >= QLatin1Char('a') && c <= QLatin1Char('f'))
                || (c >= QLatin1Char('A') && c <= QLatin1Char('F'));
        if (!hex) {
            return false;
        }
    }
    return true;
}

/** 把一份默认设置写成文件（父目录不存在就建）。 */
bool writeDefaults(const QString& path, const Settings& s, QString* err)
{
    return s.save(path, err);
}

}   // namespace

QString Settings::defaultPath()
{
    // ① 环境变量（自检 / 多份配置并存时用）
    const QByteArray env = qgetenv("MAHJONG_SETTINGS");
    if (!env.isEmpty()) {
        return QString::fromLocal8Bit(env);
    }
    // ② exe 同级 —— 绿色版：解压到哪，设置就在哪
    const QString beside = QCoreApplication::applicationDirPath() + QStringLiteral("/settings.json");
    const QFileInfo fi(beside);
    const QDir dir = fi.absoluteDir();
    if (dir.exists() && QFileInfo(dir.absolutePath()).isWritable()) {
        // 文件已存在但只读（比如被别的用户建的）→ 也别硬写它
        if (!fi.exists() || fi.isWritable()) {
            return beside;
        }
    }
    // ③ 用户配置目录（exe 装在 Program Files 之类的只读位置时的兜底）
    const QString cfg = QStandardPaths::writableLocation(QStandardPaths::AppConfigLocation);
    return (cfg.isEmpty() ? QDir::tempPath() : cfg) + QStringLiteral("/settings.json");
}

void Settings::sanitize(QStringList* repaired)
{
    const Settings def;
    auto fix = [&](bool bad, const char* key) {
        if (bad && repaired != nullptr && !repaired->contains(QLatin1String(key))) {
            repaired->append(QLatin1String(key));
        }
        return bad;
    };

    host = host.trimmed();
    if (fix(host.isEmpty() || codePointCount(host) > 255 || hasControlChar(host)
                    || host.contains(QLatin1Char(' ')) || host.contains(QLatin1Char('/')),
            "host")) {
        host = def.host;
    }
    // 端口：`port` 是 quint16，超范围在 fromJson 里已经被挡掉（回落成缺省）；这里再兜一次 0
    if (fix(port == 0, "port")) {
        port = def.port;
    }
    name = name.trimmed();
    if (fix(hasControlChar(name) || codePointCount(name) > 24, "name")) {
        name = def.name;   // 空 = 用默认名
    }
    // 身份：形状不对就**清空**（空 = "我还没有身份"，服务端会给一个并让我们保存）。
    // ⚠ 不能"保留一个坏 uuid"：那会被原样发给服务端，而服务端会把它当"没有记录"、
    //   再生成一个 —— 但客户端这边永远存着旧的坏值，每次连接都白跑一轮。
    uuid = uuid.trimmed();
    if (fix(!uuid.isEmpty() && !isUuidShape(uuid), "uuid")) {
        uuid = def.uuid;
    }
    pack = pack.trimmed();
    // ⚠ 材质包路径**不因为"文件不在"而清掉**：用户可能插着 U 盘、或盘符还没挂上。
    //   路径不可用时只是"回退默认素材"，设置在原地保留（材质包那层单独回报问题）。
    if (fix(codePointCount(pack) > 512 || hasControlChar(pack), "pack")) {
        pack = def.pack;
    }
    // 音效音量：`fromJson` 用 -1 表示"给了但不在 0..100"（或类型不对）→ 只重置这一项。
    if (fix(sfxVolume < 0 || sfxVolume > 100, "sfx_volume")) {
        sfxVolume = def.sfxVolume;
    }
}

Settings Settings::fromJson(const QJsonObject& o, QStringList* repaired)
{
    Settings s;
    s.host = o.value(QStringLiteral("host")).toString(s.host);
    const QJsonValue pv = o.value(QStringLiteral("port"));
    if (pv.isDouble()) {
        const double d = pv.toDouble();
        // 越界/非整数 → 缺省（在 sanitize 里登记）
        if (d >= 1.0 && d <= 65535.0 && d == double(int(d))) {
            s.port = quint16(d);
        } else {
            s.port = 0;
        }
    } else if (!pv.isUndefined()) {
        s.port = 0;   // 类型不对
    }
    s.name = o.value(QStringLiteral("name")).toString(s.name);
    // 身份（uuid）：类型不对 → 空串（= 还没有身份）；形状不对由 sanitize 清掉
    const QJsonValue uv = o.value(QStringLiteral("uuid"));
    s.uuid = uv.isString() ? uv.toString() : QString();
    s.pack = o.value(QStringLiteral("pack")).toString(s.pack);
    // 音效：缺省开、音量 70。类型不对时用**缺省值**（sanitize 里登记为已修复）。
    {
        const QJsonValue sv = o.value(QStringLiteral("sfx"));
        s.sfx = sv.isBool() ? sv.toBool() : true;
        const QJsonValue vv = o.value(QStringLiteral("sfx_volume"));
        if (vv.isDouble()) {
            const double d = vv.toDouble();
            s.sfxVolume = (d >= 0.0 && d <= 100.0) ? int(d) : -1;   // -1 => sanitize 重置
        } else if (!vv.isUndefined()) {
            s.sfxVolume = -1;
        }
    }

    for (auto it = o.constBegin(); it != o.constEnd(); ++it) {
        if (!isKnownKey(it.key())) {
            s.extra.insert(it.key(), it.value());   // 认不出的键原样留着
        }
    }
    s.sanitize(repaired);
    return s;
}

QJsonObject Settings::toJson() const
{
    QJsonObject o = extra;   // 先铺认不出的键，再盖我们管的（保证我们的键是权威值）
    o.insert(QStringLiteral("host"), host);
    o.insert(QStringLiteral("port"), int(port));
    o.insert(QStringLiteral("name"), name);
    o.insert(QStringLiteral("uuid"), uuid);
    o.insert(QStringLiteral("pack"), pack);
    o.insert(QStringLiteral("sfx"), sfx);
    o.insert(QStringLiteral("sfx_volume"), sfxVolume);
    return o;
}

Settings Settings::load(const QString& pathIn, QStringList* repaired, QString* note)
{
    if (repaired != nullptr) {
        repaired->clear();
    }
    if (note != nullptr) {
        note->clear();   // ⚠ 必须清：调用方常复用同一个变量，留着上一次的值会误报
    }
    const QString path = pathIn.isEmpty() ? defaultPath() : pathIn;
    Settings def;

    QFile f(path);
    if (!f.exists()) {
        // ① 文件不存在 —— 按需求**生成**一份缺省文件（顺便让用户知道去哪改）
        QString err;
        if (note != nullptr) {
            *note = QStringLiteral("settings_missing");
        }
        writeDefaults(path, def, &err);
        return def;
    }
    if (!f.open(QIODevice::ReadOnly)) {
        if (note != nullptr) {
            *note = QStringLiteral("settings_unreadable");
        }
        return def;   // 读不出来：**不覆盖**用户的文件（可能只是权限临时不对）
    }
    const QByteArray raw = f.read(64 * 1024 + 1);   // 设置文件不该有几 KB 以上
    f.close();
    if (raw.size() > 64 * 1024) {
        if (note != nullptr) {
            *note = QStringLiteral("settings_too_large");
        }
        return def;
    }

    QJsonParseError pe {};
    const QJsonDocument doc = QJsonDocument::fromJson(raw, &pe);
    if (pe.error != QJsonParseError::NoError || !doc.isObject()) {
        // ② 解析失败 —— 用缺省值**重新生成**（坏文件先备份成 .bak，别直接扔了）
        QFile::remove(path + QStringLiteral(".bak"));
        QFile::copy(path, path + QStringLiteral(".bak"));
        QString err;
        writeDefaults(path, def, &err);
        if (note != nullptr) {
            *note = QStringLiteral("settings_broken");
        }
        return def;
    }

    Settings s = fromJson(doc.object(), repaired);
    if (repaired != nullptr && !repaired->isEmpty()) {
        // ③ 有键不可用 —— 把**修好的**那份写回去（其余键原样保留）
        QString err;
        s.save(path, &err);
        if (note != nullptr) {
            *note = QStringLiteral("settings_repaired");
        }
    }
    return s;
}

bool Settings::save(const QString& pathIn, QString* err) const
{
    const QString path = pathIn.isEmpty() ? defaultPath() : pathIn;
    const QFileInfo fi(path);
    if (!fi.absoluteDir().exists() && !QDir().mkpath(fi.absolutePath())) {
        if (err != nullptr) {
            *err = QStringLiteral("mkdir_failed");
        }
        return false;
    }
    // 原子写：先写临时文件再改名 —— 写到一半崩了也不会留下半个 JSON
    const QString tmp = path + QStringLiteral(".tmp");
    QFile f(tmp);
    if (!f.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        if (err != nullptr) {
            *err = QStringLiteral("open_failed");
        }
        return false;
    }
    const QByteArray out = QJsonDocument(toJson()).toJson(QJsonDocument::Indented);
    const bool wrote = f.write(out) == out.size();
    f.close();
    if (!wrote) {
        QFile::remove(tmp);
        if (err != nullptr) {
            *err = QStringLiteral("write_failed");
        }
        return false;
    }
    QFile::remove(path);
    if (!QFile::rename(tmp, path)) {
        QFile::remove(tmp);
        if (err != nullptr) {
            *err = QStringLiteral("rename_failed");
        }
        return false;
    }
    return true;
}
