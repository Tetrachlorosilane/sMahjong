#pragma once

// 语言/文案：所有**用户可见文本**都放在 assets/i18n/<locale>.json 里，
// 代码只引用 key（`lang::t("yaku.riichi")`）。这样改文案、加语言都不用重编译。
//
// 加载顺序（与 tiles/fonts 同一套约定）：
//   exe 同级 i18n/<locale>.json  →  qrc :/i18n/<locale>.json  →  都没有则 t() 原样返回 key
// 语言由 `--lang <code>` 或环境变量 DSH_MAHJONG_LANG 指定，缺省 zh_CN。

#include <QString>
#include <QStringList>

namespace lang {

/** 载入语言文件（幂等；没找到就保持"key 原样返回"）。返回是否成功载入。 */
bool load(const QString& locale = QString());

/** 当前语言代码（如 zh_CN）；未载入时为空。 */
QString locale();

/** 取文案：`t("yaku.riichi")`。缺失时**原样返回 key** 并计一次 miss（自检会看这个数）。 */
QString t(const QString& key);

/** 取带参数的文案：先查 key，再 `QString::arg(a)`（模板里写 `%1`）。 */
QString t(const QString& key, const QString& a);

/**
 * 该 key 是否有登记（**不计 miss**）。
 *
 * <p>用于「协议码 → 文案」时区分「认识的码」与「认不出的码」：
 * 认不出时应当原样显示码本身（或老服务端发来的原文），而不是显示 `前缀.码` 这种裸键。
 */
bool has(const QString& key);

/** 缺 key 的次数（自检断言为 0；测试里可 reset）。 */
int misses();
void resetMisses();

/** 语言文件里登记的 key 数量（自检用）。 */
int keyCount();

/** 语言文件里全部 key（自检按前缀分族统计、check-i18n 交叉核对用）。 */
QStringList keys();

/** 缺失过的 key 清单（排查用，按首次出现顺序，去重）。 */
QStringList missingKeys();

/** 自检用：从内存里的 JSON 文本载入（不碰文件系统）。 */
bool loadFromJson(const QString& json, const QString& locale);

/** 自检用：协议码的翻译是否存在（不存在会计一次 miss）。 */
QString yakuText(const QString& code, const QString& tile);

/** 牌码 → 显示名（`tile.1m` → 一万）。认不出的码原样返回，不显示裸键。 */
QString tileName(const QString& code);

/**
 * 协议码 → 显示文案，键 = `<prefix><code>`（如 `prefix="error."`、`code="no_room"`）。
 *
 * <p>服务端**只发 ASCII 码**，中文文案只存在于语言文件里。三条兜底，**宁可显示原文也不显示空白**：
 *   ① `code` 为空 → 返回空串，由调用方回退到老服务端的中文字段（新旧混跑不炸）；
 *   ② 码认得 → 用语言文件的文案（模板里的 `%1` 用 `arg` 填）；
 *   ③ 码认不出（服务端加了码、客户端语言文件还没跟上）→ **原样显示码**（带参数就一起显示），
 *      既不会显示成 `error.xxx` 这种裸键，也能一眼看出是"还没翻译"而不是"没收到"。
 */
QString code(const QString& prefix, const QString& code, const QString& arg = QString());

} // namespace lang
