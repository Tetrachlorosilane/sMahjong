// **自家回合的询问内容**（= Java `Round.turnOptions`）—— 选项生成的全部判据。
//
// 为什么单独成一层：这是"策略能做什么"的定义，输出顺序**是协议的一部分**
// （`Action.enumerate` 的展开序 = `legal` 的下标 = `chosen_index`）：
//   discard → riichi → tsumo → kan → kyuushu
// 每一段的闸门都在 Java 侧散在 `Round` 里（`riichiAllowed` / `canRiichi` / `checkWin` /
// `kanAllowedByRiichi` + `ankanKeepsShape` / `canKyuushu`），这里按同一口径收拢。
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "counts.hpp"
#include "evaluator.hpp"
#include "handeval.hpp"
#include "meld.hpp"
#include "roundoptions.hpp"
#include "rules.hpp"
#include "shanten.hpp"
#include "visible.hpp"
#include "tiles.hpp"

namespace trainer {

/** 一次自家回合询问的**输入状态**（全部来自 Java 侧的读数，不含任何隐藏信息）。 */
struct TurnAsk {
    int seat = 0;
    int drawnId = -1;              // 刚摸到的牌 id；-1 = 本巡没摸（理论上只在庄家开局的特殊分支）
    Counts hand{};                 // 自家暗牌计数（**含**刚摸到的那张 → 14 − 3×副露 张）
    std::vector<int> handIds;      // 自家暗牌 id（Java 的排序序，`canRiichi` 的候选序就是它）
    std::vector<Meld> melds;       // 自家副露
    int drawnKind = -1;
    bool rinshan = false;
    bool atLastLive = false;       // `wall.atLastLiveTile()`
    bool menzen = false;
    bool riichi = false;
    bool doubleRiichi = false;
    bool ippatsu = false;
    std::array<int, 4> scores{};
    int roundWind = 0;
    int kyoku = 1;
    int honba = 0;
    int dealer = 0;
    int tilesLeft = 0;
    int deadWallLeft = 0;          // = 剩余岭上数
    int kanCount = 0;
    bool anyCall = false;
    int playerDraws = 0;
    std::vector<int> forbiddenKinds;   // 食替禁打牌种
    std::vector<int> doraKinds;
    std::vector<int> uraKinds;
    Rules rules{};

    /** 暗牌里的赤五张数（Java `redCount(seat)`：**只数暗牌**，副露不算）。 */
    int akaInHand() const {
        int n = 0;
        for (int id : handIds) {
            if (isRedId(id)) {
                n++;
            }
        }
        return n;
    }
};

/** `riichiAllowed`：立直宣言的**门槛**（不含"这张牌能不能宣"）。 */
inline bool riichiAllowed(const TurnAsk &a) {
    return a.menzen && !a.riichi && a.scores[static_cast<size_t>(a.seat)] >= a.rules.riichiMinScore
           && a.tilesLeft >= a.rules.riichiMinTilesLeft
           && !(a.rules.riichiNoHaitei && a.atLastLive);
}

/** `canRiichi(seat, tileId)`：把这一张（按 **id** 相等）从手里去掉后还要听牌。 */
inline bool canRiichi(const TurnAsk &a, int tileId) {
    if (!riichiAllowed(a)) {
        return false;
    }
    bool has = false;
    for (int id : a.handIds) {
        if (id == tileId) {
            has = true;
            break;
        }
    }
    if (!has) {
        return false;
    }
    Counts c{};
    for (int id : a.handIds) {
        if (id == tileId) {
            continue;                   // ⚠ 按 **id** 去掉（aka==0 时同一 id 出现两次会一起去掉）
        }
        c[static_cast<size_t>(kindOf(id))]++;
    }
    return !waits(c, static_cast<int>(a.melds.size())).empty();
}

/** M.League 的「立直后暗杠还要求面子构成不变」（`Round.ankanKeepsShape`）。 */
inline bool ankanKeepsShape(const Counts &c, int kind) {
    if (isHonor(kind)) {
        return true;                    // 字牌不可能进顺子
    }
    for (int d = -2; d <= 2; d++) {
        if (d == 0) {
            continue;
        }
        const int k = kind + d;
        if (k < 0 || k >= kKindCount || suitOf(k) != suitOf(kind)) {
            continue;
        }
        if (c[static_cast<size_t>(k)] > 0) {
            return false;
        }
    }
    return true;
}

/** `Round.kanAllowedByRiichi`：未立直一律允许；立直后要过两道门槛。 */
inline bool kanAllowedByRiichi(const TurnAsk &a, int kind) {
    if (!a.riichi) {
        return true;
    }
    Counts before = a.hand;
    if (a.drawnKind >= 0 && before[static_cast<size_t>(a.drawnKind)] > 0) {
        before[static_cast<size_t>(a.drawnKind)]--;      // 「杠前听牌」按 **13 张**算
    }
    const std::vector<int> wa = waits(before, static_cast<int>(a.melds.size()));
    if (!kanAllowedAfterRiichi(wa, a.hand, static_cast<int>(a.melds.size()), kind)) {
        return false;
    }
    return !a.rules.ankanKeepsShape || ankanKeepsShape(a.hand, kind);
}

/** `canKan()`：还有岭上牌、且一局没到 4 次杠。 */
inline bool canKan(const TurnAsk &a) { return a.deadWallLeft > 0 && a.kanCount < 4; }

/** `Round.canKyuushu(seat)`（M.League 关着，所以正常情况下永远为假）。 */
inline bool canKyuushu(const TurnAsk &a) {
    if (!a.rules.kyuushuAbort || a.anyCall || a.playerDraws != 1) {
        return false;
    }
    int n = 0;
    for (int k = 0; k < kKindCount; k++) {
        if (a.hand[static_cast<size_t>(k)] > 0 && isYaochuKind(k)) {
            n++;
        }
    }
    return n >= 9;
}

/**
 * `Round.checkWin(seat, winTileId, tsumo=true, rinshan, haitei, false, false)`：
 * 张数契约（14 − 3×副露，自摸时和了牌**已在**手里）→ 构造 `WinContext` → 求值取 `valid`。
 */
inline bool canWinTsumo(const TurnAsk &a) {
    if (a.drawnKind < 0) {
        return false;
    }
    Counts merged{};
    if (!winCounts(a.hand, static_cast<int>(a.melds.size()), a.drawnKind, /*tsumo=*/true, merged)) {
        return false;
    }
    WinContext ctx;
    ctx.rules = a.rules;
    ctx.seat = a.seat;
    ctx.dealerSeat = a.dealer;
    ctx.roundWind = 27 + a.roundWind;
    ctx.tsumo = true;
    ctx.riichi = a.riichi && !a.doubleRiichi;
    ctx.doubleRiichi = a.riichi && a.doubleRiichi;
    ctx.ippatsu = a.riichi && a.ippatsu;
    ctx.chankan = false;
    ctx.rinshan = a.rinshan;
    ctx.haitei = a.atLastLive;                     // haitei && tsumo
    ctx.houtei = false;
    ctx.tenhou = a.seat == a.dealer && a.playerDraws == 1 && !a.anyCall;
    ctx.chiihou = a.seat != a.dealer && a.playerDraws == 1 && !a.anyCall;
    ctx.renhou = false;
    ctx.tsubame = false;                           // 燕返只在荣和
    ctx.kanburi = false;
    ctx.winKind = a.drawnKind;
    ctx.doraIndicators = a.doraKinds;
    ctx.uraIndicators = a.uraKinds;                // `checkWin` 走 includeUra = true
    ctx.allTileIds.clear();
    for (const Meld &m : a.melds) {
        for (int i = 0; i < m.tileCount; i++) {
            ctx.allTileIds.push_back(m.tiles[static_cast<size_t>(i)]);
        }
    }
    if (a.drawnId >= 0) {
        ctx.allTileIds.push_back(a.drawnId);
    }
    const int aka = a.akaInHand();                 // 暗牌 + 和了牌里的赤五（和了牌就在暗牌里）
    for (int i = 0; i < aka; i++) {
        const int kind = (i % 3 == 0) ? kAkaM : ((i % 3 == 1) ? kAkaP : kAkaS);
        ctx.allTileIds.push_back(idOf(kind, 0));
    }
    ctx.menzen = a.menzen;
    const HandScore s = evaluate(ctx, merged, a.melds, a.drawnKind);
    return s.valid;
}

/**
 * `Round.turnOptions` 的规范文本（与 `tools/RoundProbe.java` 逐字符一致）：
 *   `discard=…;riichi=…;tsumo;kan=ankan:5m,kakan:7s;kyuushu`（没有的段直接不出现）
 */
inline std::string turnOptionsText(const TurnAsk &a) {
    std::string out;
    auto add = [&out](const std::string &seg) {
        if (seg.empty()) {
            return;
        }
        if (!out.empty()) {
            out += ';';
        }
        out += seg;
    };

    // ① 打牌（永远存在且第一）：已立直 → 只剩摸切那一张；否则手牌去重 − 食替禁打
    {
        std::string seg = "discard=";
        const std::vector<std::string> v
            = discardChoices(a.handIds, a.riichi, a.drawnId, a.forbiddenKinds);
        for (size_t i = 0; i < v.size(); i++) {
            seg += (i ? "," : "");
            seg += v[i];
        }
        add(seg);
    }
    // ② 立直（未立直时按手牌序、按牌码去重）
    {
        std::string seg = "riichi=";
        bool any = false;
        std::vector<std::string> seen;
        for (int id : a.handIds) {
            if (!canRiichi(a, id)) {
                continue;
            }
            const std::string code = tileToStr(id);
            bool dup = false;
            for (const std::string &s : seen) {
                if (s == code) {
                    dup = true;
                    break;
                }
            }
            if (dup) {
                continue;
            }
            seen.push_back(code);
            if (any) {
                seg += ",";
            }
            seg += code;
            any = true;
        }
        add(any ? seg : "");
    }
    // ③ 自摸
    if (canWinTsumo(a)) {
        add("tsumo");
    }
    // ④ 杠（暗杠按牌种升序在前，加杠按副露顺序在后；同一个选项里）
    if (canKan(a) && !a.atLastLive) {
        std::string seg = "kan=";
        bool any = false;
        for (int k = 0; k < kKindCount; k++) {
            if (a.hand[static_cast<size_t>(k)] != 4 || !kanAllowedByRiichi(a, k)) {
                continue;
            }
            if (any) {
                seg += ",";
            }
            seg += "ankan:" + kindToStr(k, false);
            any = true;
        }
        for (const Meld &m : a.melds) {
            if (m.kind != Meld::Kind::PON) {
                continue;
            }
            const int k = kindOf(m.calledId);
            if (a.hand[static_cast<size_t>(k)] <= 0) {
                continue;
            }
            if (any) {
                seg += ",";
            }
            seg += "kakan:" + kindToStr(k, false);
            any = true;
        }
        add(any ? seg : "");
    }
    // ⑤ 九种九牌
    if (canKyuushu(a)) {
        add("kyuushu");
    }
    return out;
}

}  // namespace trainer
