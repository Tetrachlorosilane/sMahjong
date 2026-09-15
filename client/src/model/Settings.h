#pragma once

// 个人设置（`settings.json`）：服务器地址/端口、用户名、材质包路径。
//
// **需求点：坏数据不能把程序卡住**。所以这里的 `load()` 不返回"失败"，而是
// **永远给出一份可用的设置**，并把"改了什么"回报出来：
//   · 文件不存在 / 读不出来 / JSON 解析失败 → 全部取缺省值，并**重新生成**文件；
//   · 某个键缺失或类型/范围不对     → **只重置那一个键**，同样回写文件；
//   · 认不出的键（别的版本写的）    → 原样保留，回写时带上（向前兼容）。
// 调用方拿到 `repaired` 就能在界面上说清楚"哪些项被重置了"，而不是静默改掉用户的东西。
//
// 存放位置（`defaultPath()`）：`MAHJONG_SETTINGS` 环境变量 > exe 同级 `settings.json`
// > 用户配置目录（exe 同级不可写时，比如装在 Program Files 里）。

#include <QJsonObject>
#include <QString>
#include <QStringList>

struct Settings
{
    QString host = QStringLiteral("127.0.0.1");
    quint16 port = 10086;
    /** 昵称；空 = 用语言文件里的默认名（见 `ui.lobby.default_name`）。 */
    QString name;
    /** 材质包路径：目录或 `.zip`。空 = 全用默认素材。 */
    QString pack;

    /** 设置文件位置（不保证存在，也不保证可写 —— `save()` 会如实回报错误）。 */
    static QString defaultPath();

    /**
     * 读设置，**永不失败**。
     *
     * @param path     设置文件路径（空 = `defaultPath()`）
     * @param repaired 输出：被重置成缺省值的键（空 = 文件本来就是好的）
     * @param note     输出：一句人话说明（文件不存在 / 解析失败 / 已重新生成 之类）
     */
    static Settings load(const QString& path, QStringList* repaired = nullptr,
                         QString* note = nullptr);

    /** 写回设置（父目录会自动创建）。失败时通过 `err` 回报，不抛异常。 */
    bool save(const QString& path, QString* err = nullptr) const;

    /** 规范化：把不合规的值就地修成缺省值，并把键名记进 `repaired`。 */
    void sanitize(QStringList* repaired);

    QJsonObject toJson() const;
    static Settings fromJson(const QJsonObject& o, QStringList* repaired);

    /// 认不出的键（原样保留，回写时带上）
    QJsonObject extra;
};
