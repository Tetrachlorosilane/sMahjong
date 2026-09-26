// 动作空间 —— 与 Java `mahjong.ai.Action` **逐分支同口径**。
//
// 这一层是**数据集里存的东西**：轨迹的 `chosen` / `legal` 写的就是 `key()`。三条被钉死的性质：
//   ① `key()` —— 稳定字符串键（跨版本可复用）；
//   ② `index()` —— 固定头下标 0..78（打牌 0..36 / 立直 37..73 / 自摸 74 / 荣和 75 / 碰 76 /
//      过 77 / 九种九牌 78；76 是历史槽位，现在只给"裸 pon"用）；吃/碰/杠是**参数化**动作 → -1；
//   ③ `cmdTokens()` —— 交回状态机的回包（与客户端报文同形，这里用**规范化 token 串**表示，
//      便于两边逐字符比对；真正的 JSON 序列化在协议层，与本层无关）。
//
// ⚠ **碰与大明杠的键里必须带"从手里取哪几张"**（`pon:5p+5p` / `kan:daiminkan:5s+5s+0s`）：
//   手里同时有赤五与普通五时，服务端为两种取法**各下发一条**选项，它们是两个不同的合法动作；
//   折成裸 `pon` 会让 `legal` 出现重复键、`chosen_index` 无从分辨（自检会红）。
//   裸 `pon` 只作为老数据的兼容形态保留，落位走 `resolve` 的"同类型第一条"。
#pragma once

#include <string>
#include <vector>

namespace trainer {

inline constexpr int kActionTileSlots = 37;      // 34 种牌 + 赤 5m/5p/5s
inline constexpr int kActionDiscardBase = 0;
inline constexpr int kActionRiichiBase = kActionTileSlots;      // 37
inline constexpr int kActionTsumoId = 2 * kActionTileSlots;     // 74
inline constexpr int kActionRonId = kActionTsumoId + 1;         // 75
inline constexpr int kActionPonId = kActionTsumoId + 2;         // 76（历史槽位）
inline constexpr int kActionPassId = kActionTsumoId + 3;        // 77
inline constexpr int kActionKyuushuId = kActionTsumoId + 4;     // 78
inline constexpr int kActionFixedCount = kActionKyuushuId + 1;  // 79

inline constexpr const char *kActDiscard = "discard";
inline constexpr const char *kActRiichi = "riichi";
inline constexpr const char *kActTsumo = "tsumo";
inline constexpr const char *kActRon = "ron";
inline constexpr const char *kActPon = "pon";
inline constexpr const char *kActChi = "chi";
inline constexpr const char *kActKan = "kan";
inline constexpr const char *kActKyuushu = "kyuushu";
inline constexpr const char *kActPass = "pass";

/** 一个可执行动作（`tile` / `tiles` / `kanKind` 用 `has*` 区分"没有"与"空串"）。 */
struct Action {
    std::string type;
    std::string tile;
    bool hasTile = false;
    std::vector<std::string> tiles;
    bool hasTiles = false;
    std::string kanKind;
    bool tsumogiri = false;

    /** 稳定字符串键。 */
    std::string key() const;

    /** 固定头下标；吃/碰（带取法）/杠返回 -1。 */
    int index() const;

    /** 回包的规范化 token 串（字段顺序固定：type / tile / kind / tsumogiri / tiles）。 */
    std::string cmdTokens() const;
};

/** 牌码 → 槽位（0..36）；认不出返回 -1。 */
int actionTileIndex(const std::string &code);

/** 槽位 → 牌码；越界返回空串。 */
std::string actionTileCode(int slot);

/** 构造：单类型动作（tsumo/ron/pass/kyuushu/裸 pon）。 */
Action actionOf(const std::string &type);

/** `key()` 的逆；非法键返回 `nullopt`（用 `ok` 报出来，避免引入 optional 的头）。 */
Action actionParse(const std::string &key, bool &ok);

/** 回包 token 串 → 动作；认不出返回 ok=false（**不猜**默认动作）。 */
Action actionFromCmdTokens(const std::string &tokens, bool &ok);

/**
 * 把策略回包落位成"本次 `legal` 里那一个"：先精确匹配，匹配不上只对**碰/杠**退化为
 * "同类型（同杠种）第一条"。其余动作必须精确匹配 —— 绝不挑一个像的（那会把错标签写进数据集）。
 */
std::string actionResolveKey(const std::string &cmdTokens, const std::vector<std::string> &legalKeys,
                             bool &ok);

}  // namespace trainer
