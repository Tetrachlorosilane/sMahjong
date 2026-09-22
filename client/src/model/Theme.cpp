#include "model/Theme.h"

#include "model/Tile.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QJsonValue>
#include <QStringList>

#if MAHJONG_HAVE_ZIP
#  include <QtCore/private/qzipreader_p.h>
#endif

namespace {

constexpr int kMaxManifestBytes = 64 * 1024;      // 清单不该超过 64 KB
constexpr int kMaxAssetBytes = 8 * 1024 * 1024;   // 单张素材上限（位图/字体都够用）
constexpr int kMaxAssetCount = 4096;              // 类别目录里最多认这么多文件
constexpr int kMaxImageCache = 128;               // 位图缓存条数（38 张牌 + 牌背足够）

/** 位图后缀（按优先级）。 */
const QStringList& rasterExts()
{
    static const QStringList e { QStringLiteral("png"), QStringLiteral("jpg"),
                                 QStringLiteral("jpeg"), QStringLiteral("bmp"),
                                 QStringLiteral("webp") };
    return e;
}

const QStringList& svgExts()
{
    static const QStringList e { QStringLiteral("svg") };
    return e;
}

const QStringList& fontExts()
{
    static const QStringList e { QStringLiteral("ttf"), QStringLiteral("otf"),
                                 QStringLiteral("ttc") };
    return e;
}

/**
 * 清单里的相对路径 → 安全形式。
 *
 * <p>**这是这一层最重要的安全闸**：清单来自第三方材质包，`C:\x`、`../../..`、`//server/share`
 * 这类值绝不能落到文件系统上。
 *
 * <p>允许**一个**前导 `/`（清单里写 `/assets/tiles` 与 `assets/tiles` 等价，是本项目文档里的写法），
 * 但那只是"从包根算起"的意思，不是绝对路径；`//`、盘符、`..`、`~` 一律拒。
 * 非法就返回空串，调用方按"这个类别没写"处理。
 */
QString sanitizeRel(const QString& in)
{
    QString p = in.trimmed();
    if (p.isEmpty() || p.size() > 256) {
        return QString();
    }
    p.replace(QLatin1Char('\\'), QLatin1Char('/'));
    if (p.startsWith(QLatin1Char('/'))) {
        p = p.mid(1);   // 只剥一个：`/assets/x` → `assets/x`
    }
    if (p.isEmpty() || p.startsWith(QLatin1Char('/'))) {
        return QString();   // `//server/share` 这种 UNC
    }
    if (p.size() >= 2 && p.at(1) == QLatin1Char(':')) {
        return QString();   // 盘符
    }
    const QStringList parts = p.split(QLatin1Char('/'), Qt::SkipEmptyParts);
    QStringList out;
    for (const QString& seg : parts) {
        if (seg == QLatin1String(".")) {
            continue;
        }
        if (seg == QLatin1String("..")) {
            return QString();       // 任何一段上跳都拒（不做"回到根就算了"的妥协）
        }
        if (seg.contains(QChar(0)) || seg.startsWith(QLatin1Char('~'))) {
            return QString();
        }
        out << seg;
    }
    return out.join(QLatin1Char('/'));
}

/** 牌码能否用来拼素材名（与 TileRenderer 同一口径：38 种 + back）。 */
bool isKnownTileCode(const QString& code)
{
    return code == QLatin1String("back") || mj::kindOfTile(code) >= 0;
}

}   // namespace

QString Theme::sourceName(Source s)
{
    switch (s) {
    case Source::PackSvg:    return QStringLiteral("pack-svg");
    case Source::PackRaster: return QStringLiteral("pack-raster");
    case Source::None:
    default:                 return QStringLiteral("none");
    }
}

bool Theme::zipSupported()
{
#if MAHJONG_HAVE_ZIP
    return true;
#else
    return false;
#endif
}

Theme& Theme::instance()
{
    static Theme t;
    return t;
}

void Theme::clear()
{
    m_root.clear();
    m_isZip = false;
    m_dirs.clear();
    m_status = Status();
    m_imgCache.clear();
    m_cloth = QImage();
    m_stick = QImage();
    m_font.clear();
    m_fontName.clear();
}

void Theme::clearCaches()
{
    m_imgCache.clear();
}

bool Theme::exists(const QString& rel) const
{
    if (m_root.isEmpty() || rel.isEmpty()) {
        return false;
    }
    if (!m_isZip) {
        return QFileInfo::exists(m_root + QLatin1Char('/') + rel);
    }
    // zip：条目表是**一次扫完**的（`load()` 里填好），这里只查表
    if (m_zipEntries.contains(rel)) {
        return true;
    }
    const QString prefix = rel.endsWith(QLatin1Char('/')) ? rel : rel + QLatin1Char('/');
    for (auto it = m_zipEntries.constBegin(); it != m_zipEntries.constEnd(); ++it) {
        if (it.key().startsWith(prefix)) {
            return true;   // 目录本身可以作为前缀存在
        }
    }
    return false;
}

QByteArray Theme::readFile(const QString& rel) const
{
    if (m_root.isEmpty() || rel.isEmpty()) {
        return QByteArray();
    }
    if (!m_isZip) {
        const QString p = m_root + QLatin1Char('/') + rel;
        QFile f(p);
        if (!f.open(QIODevice::ReadOnly)) {
            return QByteArray();
        }
        if (f.size() > kMaxAssetBytes) {
            return QByteArray();   // 超大素材直接不认（宁可回退，也不吃内存）
        }
        return f.read(kMaxAssetBytes + 1);
    }
#if MAHJONG_HAVE_ZIP
    // 先查条目表：不存在的路径**连归档都不打开**（少一次磁盘 IO，也少一条异常路径）
    const auto it = m_zipEntries.constFind(rel);
    if (it == m_zipEntries.constEnd() || it.value() > kMaxAssetBytes || it.value() < 0) {
        return QByteArray();
    }
    QZipReader z(m_root);
    if (!z.isReadable()) {
        return QByteArray();
    }
    return z.fileData(rel);
#else
    return QByteArray();
#endif
}


QString Theme::firstFileIn(const QString& dir, const QStringList& exts) const
{
    if (m_root.isEmpty() || dir.isEmpty()) {
        return QString();
    }
    QStringList names;
    if (!m_isZip) {
        const QDir d(m_root + QLatin1Char('/') + dir);
        if (!d.exists()) {
            return QString();
        }
        names = d.entryList(QDir::Files | QDir::NoSymLinks, QDir::Name);
    } else {
        // zip：从**已经扫好的**条目表里挑这一层的文件
        const QString prefix = dir + QLatin1Char('/');
        for (auto it = m_zipEntries.constBegin(); it != m_zipEntries.constEnd(); ++it) {
            if (!it.key().startsWith(prefix)) {
                continue;
            }
            const QString rest = it.key().mid(prefix.size());
            if (rest.isEmpty() || rest.contains(QLatin1Char('/'))) {
                continue;   // 只看这一层（不做递归：包结构越简单越不容易出意外）
            }
            names << rest;
            if (names.size() > kMaxAssetCount) {
                break;
            }
        }
        names.sort();
    }
    if (names.size() > kMaxAssetCount) {
        names = names.mid(0, kMaxAssetCount);
    }
    // 按**后缀优先级**找：先 svg，再 png/jpg…（同一张牌同时放了两种格式时，
    // 矢量优先 —— 缩放不糊，符合"能矢量就别位图"的常识）
    for (const QString& ext : exts) {
        const QString suffix = QLatin1Char('.') + ext;
        for (const QString& n : names) {
            if (n.endsWith(suffix, Qt::CaseInsensitive)) {
                return dir + QLatin1Char('/') + n;
            }
        }
    }
    return QString();
}

Theme::Status Theme::load(const QString& pathIn)
{
    clear();
    Status st;
    st.pack = pathIn.trimmed();
    st.zip = false;

    if (st.pack.isEmpty()) {
        m_status = st;   // 没指定 = 默认素材，不是错误
        return m_status;
    }

    const QFileInfo fi(st.pack);
    if (fi.isDir()) {
        m_root = fi.absoluteFilePath();
        m_isZip = false;
    } else if (fi.isFile()) {
        const QString suffix = fi.suffix().toLower();
        if (suffix == QLatin1String("zip")) {
#if MAHJONG_HAVE_ZIP
            m_root = fi.absoluteFilePath();
            m_isZip = true;
            st.zip = true;
#else
            st.problems << QStringLiteral("zip_unsupported");
            m_status = st;
            return m_status;
#endif
        } else {
            st.problems << QStringLiteral("pack_not_archive");
            m_status = st;
            return m_status;
        }
    } else {
        st.problems << QStringLiteral("pack_missing");
        m_status = st;
        return m_status;
    }

    // zip：**一次**把条目表扫出来（之后每次查素材都只查表，不再重扫归档）
    if (m_isZip) {
#if MAHJONG_HAVE_ZIP
        QZipReader z(m_root);
        if (!z.isReadable()) {
            st.problems << QStringLiteral("pack_missing");   // 损坏到打不开
            m_status = st;
            m_root.clear();
            return m_status;
        }
        const QList<QZipReader::FileInfo> list = z.fileInfoList();
        if (list.size() > kMaxAssetCount * 4) {
            st.problems << QStringLiteral("pack_too_many_entries");
            m_status = st;
            m_root.clear();
            return m_status;
        }
        for (const QZipReader::FileInfo& fi : list) {
            if (!fi.isFile || fi.filePath.isEmpty() || fi.size < 0 || fi.size > kMaxAssetBytes) {
                continue;
            }
            // 归档里的路径也过一遍安全闸：`../x` 这样的条目一律不进表
            const QString rel = sanitizeRel(fi.filePath);
            if (!rel.isEmpty()) {
                m_zipEntries.insert(rel, fi.size);
            }
        }
#else
        st.problems << QStringLiteral("zip_unsupported");
        m_status = st;
        m_root.clear();
        return m_status;
#endif
    }

    // ---- 清单 ----
    QString manifest = QStringLiteral("theme.json");
    if (!exists(manifest)) {
        manifest = QStringLiteral("manifest.json");
    }
    const QByteArray raw = readFile(manifest);
    if (raw.isEmpty()) {
        st.problems << QStringLiteral("manifest_missing");
        m_status = st;
        return m_status;
    }
    if (raw.size() > kMaxManifestBytes) {
        st.problems << QStringLiteral("manifest_too_large");
        m_status = st;
        return m_status;
    }
    QJsonParseError pe {};
    const QJsonDocument doc = QJsonDocument::fromJson(raw, &pe);
    if (pe.error != QJsonParseError::NoError || !doc.isObject()) {
        st.problems << QStringLiteral("manifest_broken");
        m_status = st;
        return m_status;
    }
    const QJsonObject o = doc.object();
    st.name = o.value(QStringLiteral("name")).toString();
    st.loaded = true;

    // ---- 各类别目录（写错了只影响它自己）----
    for (const char* key : {"tiles", "back", "cloth", "stick", "font", "sfx"}) {
        const QString k = QLatin1String(key);
        if (!o.contains(k)) {
            continue;
        }
        const QJsonValue v = o.value(k);
        if (!v.isString()) {
            st.problems << (k + QStringLiteral("_not_string"));
            continue;
        }
        const QString rel = sanitizeRel(v.toString());
        if (rel.isEmpty()) {
            st.problems << (k + QStringLiteral("_bad_path"));
            continue;
        }
        if (!exists(rel)) {
            st.problems << (k + QStringLiteral("_dir_missing"));
            continue;
        }
        m_dirs.insert(k, rel);
    }
    // 牌背：没单独给就用 tiles 那个文件夹 —— 但**只认精确叫 `back.<后缀>`** 的那张
    //（tiles 目录是牌面的，随便抓一张当牌背就会张冠李戴；单独的 back 目录才允许任意文件名）。
    // 这里不再把 back 塞进 m_dirs：查表时靠 `m_dirs.contains("back")` 判断"是不是专用目录"。

    // ---- 桌布 / 立直棒 ----
    if (m_dirs.contains(QStringLiteral("cloth"))) {
        // cloth 是**专用目录**：里面第一张图就是桌布（作者随便怎么命名）
        const QString f = firstFileIn(m_dirs.value(QStringLiteral("cloth")), rasterExts());
        if (!f.isEmpty()) {
            const QImage img = QImage::fromData(readFile(f));
            if (!img.isNull()) {
                m_cloth = img;
                st.applied << QStringLiteral("cloth");
            } else {
                st.problems << QStringLiteral("cloth_broken");
            }
        } else {
            st.problems << QStringLiteral("cloth_empty");
        }
    }
    if (m_dirs.contains(QStringLiteral("stick"))) {
        const QString f = firstFileIn(m_dirs.value(QStringLiteral("stick")), rasterExts());
        if (!f.isEmpty()) {
            const QImage img = QImage::fromData(readFile(f));
            if (!img.isNull()) {
                m_stick = img;
                st.applied << QStringLiteral("stick");
            } else {
                st.problems << QStringLiteral("stick_broken");
            }
        } else {
            st.problems << QStringLiteral("stick_empty");
        }
    }
    // ---- UI 字体 ----
    if (m_dirs.contains(QStringLiteral("font"))) {
        const QString f = firstFileIn(m_dirs.value(QStringLiteral("font")), fontExts());
        if (!f.isEmpty()) {
            const QByteArray data = readFile(f);
            if (!data.isEmpty()) {
                m_font = data;
                m_fontName = QFileInfo(f).fileName();
                st.applied << QStringLiteral("font");
            } else {
                st.problems << QStringLiteral("font_unreadable");
            }
        } else {
            st.problems << QStringLiteral("font_empty");
        }
    }
    // ---- 牌面/牌背：只要目录在就算这一类生效（具体哪张有、哪张没有，逐张查）----
    if (m_dirs.contains(QStringLiteral("tiles"))) {
        st.applied << QStringLiteral("tiles");
    }
    if (m_dirs.contains(QStringLiteral("back"))) {
        st.applied << QStringLiteral("back");
    }
    // 音效：与牌面同一套"逐张对应"的约定 —— 目录在就算这一类生效，
    // 里面有没有某个音效由 `packSfx()` 逐名去查（缺哪个用哪个默认音效）。
    if (m_dirs.contains(QStringLiteral("sfx"))) {
        st.applied << QStringLiteral("sfx");
    }

    m_status = st;
    return m_status;
}

QByteArray Theme::packSfx(const QString& name) const
{
    if (!m_status.loaded || name.isEmpty() || !m_dirs.contains(QStringLiteral("sfx"))) {
        return QByteArray();
    }
    // 音效是**逐个文件对应**的（与牌面同一个约定）：必须精确叫 `<名字>.wav`，
    // 不做"抓目录里第一个当音效"那种事 —— 否则换一个音效会把别的音效顶掉。
    static const QStringList wavExts = { QStringLiteral("wav") };
    const QString f = findAsset(m_dirs.value(QStringLiteral("sfx")), name, wavExts, false);
    if (f.isEmpty()) {
        return QByteArray();
    }
    return readFile(f);
}

QString Theme::backDir(bool* dedicated) const
{
    if (m_dirs.contains(QStringLiteral("back"))) {
        if (dedicated != nullptr) {
            *dedicated = true;
        }
        return m_dirs.value(QStringLiteral("back"));
    }
    if (dedicated != nullptr) {
        *dedicated = false;
    }
    return m_dirs.value(QStringLiteral("tiles"));
}

QString Theme::findAsset(const QString& dir, const QString& base, const QStringList& exts,
                         bool anyName) const
{
    if (dir.isEmpty()) {
        return QString();
    }
    if (!anyName) {
        // 精确名：`<base>.svg` → `<base>.png` → …（矢量优先，缩放不糊）
        for (const QString& ext : exts) {
            const QString rel = dir + QLatin1Char('/') + base + QLatin1Char('.') + ext;
            if (exists(rel)) {
                return rel;
            }
        }
        return QString();
    }
    return firstFileIn(dir, exts);
}

QByteArray Theme::packSvg(const QString& code) const
{
    if (!m_status.loaded || !isKnownTileCode(code)) {
        return QByteArray();   // 非法码：绝不碰文件系统（同 TileRenderer 的既有闸门）
    }
    // 牌面必须**逐张对应**（`<牌码>.svg`）；牌背走它自己的目录（专用目录里任意名 / tiles 里的 back.*）。
    // 找不到就让这张回退默认素材 —— 宁可回退，也不能张冠李戴。
    if (code == QLatin1String("back")) {
        bool dedicated = false;
        const QString d = backDir(&dedicated);
        const QString f = findAsset(d, QStringLiteral("back"), svgExts(), dedicated);
        return f.isEmpty() ? QByteArray() : readFile(f);
    }
    const QString d = m_dirs.value(QStringLiteral("tiles"));
    const QString f = findAsset(d, code, svgExts(), false);
    return f.isEmpty() ? QByteArray() : readFile(f);
}

QImage Theme::packImage(const QString& code) const
{
    if (!m_status.loaded || !isKnownTileCode(code)) {
        return QImage();
    }
    const auto it = m_imgCache.constFind(code);
    if (it != m_imgCache.constEnd()) {
        return it.value();   // 空图 = 已经查过、没有（避免每次都摸一遍磁盘）
    }
    QImage img;
    const bool back = (code == QLatin1String("back"));
    bool dedicated = false;
    const QString d = back ? backDir(&dedicated) : m_dirs.value(QStringLiteral("tiles"));
    const QString f = findAsset(d, code, rasterExts(), back && dedicated);
    if (!f.isEmpty()) {
        img = QImage::fromData(readFile(f));
    }
    if (m_imgCache.size() < kMaxImageCache) {
        m_imgCache.insert(code, img);
    }
    return img;
}

Theme::Source Theme::sourceOf(const QString& code) const
{
    if (!isKnownTileCode(code)) {
        return Source::None;
    }
    if (!packSvg(code).isEmpty()) {
        return Source::PackSvg;
    }
    if (!packImage(code).isNull()) {
        return Source::PackRaster;
    }
    return Source::None;
}
