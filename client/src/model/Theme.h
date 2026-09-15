#pragma once

// 材质包：替换**牌面/牌背、桌布、立直棒、UI 字体**（结算用的麻将字体不在此列，见 §9.2）。
//
// ---------------------------------------------------------------------------
// 包的组织方式
// ---------------------------------------------------------------------------
// 包 = 一个**目录**，或一个 **zip**（本构建支持 zip 时；见 `zipSupported()`）。
// 顶层放一份清单 `theme.json`（找不到就认 `manifest.json`）：
//
//   {
//     "name": "示例材质包",
//     "tiles": "assets/tiles",     // 牌面 + 牌背：<牌码>.svg / .png / .jpg …（缺哪张用哪张默认）
//     "back":  "assets/back",      // 可选：牌背单独一个文件夹（不给就用 tiles 里的 back.*）
//     "cloth": "assets/cloth",     // 桌布：文件夹里第一张图
//     "stick": "assets/stick",     // 立直棒：同上
//     "font":  "assets/font"       // UI 字体：.ttf / .otf / .ttc，取第一个
//   }
//
// 键值 = **包内的相对文件夹路径**（写法 `/assets/x` 与 `assets/x` 等价）。
// **没写的类别一律用默认素材**；写了但目录不存在/里面没东西，也只是该类别回退。
//
// ---------------------------------------------------------------------------
// 鲁棒性总则（每条都只影响它自己，绝不崩、绝不白屏）
// ---------------------------------------------------------------------------
//   · 路径不存在 / 既不是 zip 也不是目录        → 全默认，回报一句人话
//   · 归档损坏 / 清单缺失 / 清单不是 JSON 对象  → 该包作废，全默认
//   · 某个类别目录不存在 / 是空的               → **只有该类别**回退默认
//   · 单个素材损坏（不是合法 SVG / 图片）        → **只有那一张**回退默认
//   · 清单里的路径含 `..` 或是绝对路径           → 判为非法，忽略该键（不碰包外文件）
//   · 单个素材 / 清单 / 归档超大                 → 拒绝，回退

#include <QByteArray>
#include <QHash>
#include <QImage>
#include <QString>
#include <QStringList>

class QSvgRenderer;

class Theme
{
public:
    /** 一张牌/牌背的素材最终从哪来 —— 自检与诊断都用这一口径。 */
    enum class Source {
        None,         // 这张牌不来自材质包（交给默认素材那条链：file → qrc → 程序化）
        PackSvg,      // 材质包里的矢量图
        PackRaster,   // 材质包里的位图
    };
    static QString sourceName(Source s);

    struct Status {
        bool loaded = false;        // 清单可用（"包被接受了"）
        QString pack;               // 用户给的路径
        QString name;               // 清单里的名字（没写就是空）
        bool zip = false;           // 这份包是 zip 还是目录
        QStringList applied;        // 真正生效的类别：tiles / back / cloth / stick / font
        QStringList problems;       // 人话问题清单（界面直接显示，含回退原因）
    };

    static Theme& instance();

    /** 本构建支不支持 zip（不支持时只认目录包）。 */
    static bool zipSupported();

    /**
     * 载入材质包；`path` 为空 = 清空（全用默认素材）。
     * **任何失败都不会抛异常**，只把原因写进返回的 `Status::problems`。
     */
    Status load(const QString& path);

    const Status& status() const { return m_status; }
    bool active() const { return m_status.loaded; }

    // ---- 牌面 / 牌背（`code` = 牌码，`back` = 牌背）----
    /** 包内 `<code>.svg` 的内容；没有返回空。 */
    QByteArray packSvg(const QString& code) const;
    /** 包内 `<code>.(png|jpg|jpeg|bmp|webp)` 解码后的图；没有/坏了返回空图。 */
    QImage packImage(const QString& code) const;
    /** 这张牌在**材质包这一层**有没有素材（None = 没有，走默认素材那条链）。 */
    Source sourceOf(const QString& code) const;

    // ---- 桌布 / 立直棒 / UI 字体 ----
    QImage cloth() const { return m_cloth; }
    QImage stick() const { return m_stick; }
    QByteArray fontData() const { return m_font; }
    QString fontFileName() const { return m_fontName; }

    /** 清掉所有缓存与已载入的素材（换包 / 自检里复原用）。 */
    void clear();
    /** 只清**解码缓存**（保留已载入的包）—— 换素材后由 `TileRenderer` 调用。 */
    void clearCaches();

private:
    Theme() = default;

    /** 包内某目录下、后缀属于 `exts` 的第一个文件名（按字典序，保证可复现）。 */
    QString firstFileIn(const QString& dir, const QStringList& exts) const;
    /**
     * 找一张素材：`anyName=true` 时取目录里第一个符合后缀的文件（**只对"专用目录"用**，
     * 比如只放牌背的 `back` 目录、只放桌布的 `cloth` 目录）；否则必须**精确**叫 `<base>.<ext>`。
     */
    QString findAsset(const QString& dir, const QString& base, const QStringList& exts,
                      bool anyName) const;
    /** 牌背所在的目录（没单独给 `back` 就用 `tiles`）+ 它是不是**专用**目录。 */
    QString backDir(bool* dedicated) const;
    /** 读包内一个相对路径的字节；目录包走 QFile，zip 包走 QZipReader。 */
    QByteArray readFile(const QString& rel) const;
    /** 该相对路径是否存在（目录或文件）。 */
    bool exists(const QString& rel) const;

    /** 清单键 → 相对目录（已做安全检查）；没有/非法时返回空。 */
    QString categoryDir(const QString& key) const;

    QString m_root;                     // 目录包：根目录；zip 包：zip 文件路径
    bool m_isZip = false;
    QHash<QString, QString> m_dirs;     // 类别 → 包内相对目录
    /** zip 包的条目表（相对路径 → 字节数）；目录包为空。一次读完，之后不再重扫归档。 */
    QHash<QString, qint64> m_zipEntries;
    Status m_status;

    mutable QHash<QString, QImage> m_imgCache;   // code → 位图（空图 = 已知没有/坏了）
    QImage m_cloth;
    QImage m_stick;
    QByteArray m_font;
    QString m_fontName;
};
