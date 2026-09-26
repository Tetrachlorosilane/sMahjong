// **鸣牌询问的内容**（= Java `Round.claimOptions`）—— 与 `turnoptions.hpp` 对称的一层。
//
// 顺序（协议的一部分）：`ron` →（河底/已立直时只到 `pass`）→ `pon`×取法 → `kan`(大明杠) →
// `chi`（只有下家）→ `pass`。⚠ 与自家回合的两处口径差异：
//   · 手牌是 **13 张形态**（被鸣那张在别人牌河），荣和判定要把那张加进来；
//   · 燕返（`tsubame`）与河底（`houtei`）只有这条路上才有。
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
#include "visible.hpp"

namespace trainer {

/** 一次鸣牌询问的**输入状态**（全部来自 Java 侧读数）。 */
struct ClaimAsk {
    int seat = 0;
    int from = -1;                 // 打牌者座位
    int calledId = -1;             // 被鸣/被荣那张的**牌 id**（荣和判定要用 id 比燕返）
    int calledKind = -1;           // = kindOf(calledId)
    bool houtei = false;           // `wall.atLastLiveTile()`
    Counts hand{};                 // 自家暗牌计数（13 − 3×副露 张）
    std::vector<int> handIds;      // 自家暗牌 id（Java 排序序，取法顺序就是它）
    std::vector<Meld> melds;
    bool menzen = false;
    bool riichi = false;
    bool doubleRiichi = false;
    bool ippatsu = false;
    bool fromRiichi = false;       // `riichi[from]`（燕返）
    int fromDiscardsSinceRiichi = 0;
    int playerDraws = 0;
    bool anyCall = false;
    int dealer = 0;
    int roundWind = 0;
    Counts discardKindsEver{};     // 舍张振听的账（唯一记账点写的那份）
    bool furitenTemp = false;
    bool furitenPerm = false;
    int deadWallLeft = 0;
    int kanCount = 0;
    std::vector<int> doraKinds;
    std::vector<int> uraKinds;
    Rules rules{};

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

/** `canKan()`（鸣牌段的大明杠与自家回合共用同一把尺子）。 */
inline bool canKanClaim(const ClaimAsk &a) { return a.deadWallLeft > 0 && a.kanCount < 4; }

/** `isFuriten(seat)`：同巡 / 立直后 / **舍张振听**（听牌里有自己打出过的牌）。 */
inline bool isFuritenClaim(const ClaimAsk &a) {
    if (a.furitenTemp || a.furitenPerm) {
        return true;
    }
    const std::vector<int> wa = waits(a.hand, static_cast<int>(a.melds.size()));
    for (int k : wa) {
        if (a.discardKindsEver[static_cast<size_t>(k)] > 0) {
            return true;
        }
    }
    return false;
}

/** `Round.akaVariants(seat, kind, need)`：普通牌优先，然后"用赤五"那一组（去重）。 */
inline std::vector<std::vector<std::string>> akaVariants(const ClaimAsk &a, int kind, int need) {
    std::vector<int> plain;
    std::vector<int> red;
    for (int id : a.handIds) {
        if (kindOf(id) != kind) {
            continue;
        }
        if (isRedId(id)) {
            red.push_back(id);
        } else {
            plain.push_back(id);
        }
    }
    std::vector<std::vector<std::string>> out;
    if (static_cast<int>(plain.size()) >= need) {
        std::vector<std::string> one;
        for (int i = 0; i < need; i++) {
            one.push_back(tileToStr(plain[static_cast<size_t>(i)]));
        }
        out.push_back(one);
    }
    if (!red.empty() && static_cast<int>(plain.size() + red.size()) >= need) {
        std::vector<int> mix;
        mix.push_back(red[0]);
        for (int i = 0; i < need - 1 && i < static_cast<int>(plain.size()); i++) {
            mix.push_back(plain[static_cast<size_t>(i)]);
        }
        if (static_cast<int>(mix.size()) == need) {
            std::vector<std::string> codes;
            for (int id : mix) {
                codes.push_back(tileToStr(id));
            }
            bool dup = false;
            for (const auto &c : out) {
                if (c == codes) {
                    dup = true;
                    break;
                }
            }
            if (!dup) {
                out.push_back(codes);
            }
        }
    }
    if (out.empty()) {
        std::vector<int> all = plain;
        all.insert(all.end(), red.begin(), red.end());
        if (static_cast<int>(all.size()) >= need) {
            std::vector<std::string> one;
            for (int i = 0; i < need; i++) {
                one.push_back(tileToStr(all[static_cast<size_t>(i)]));
            }
            out.push_back(one);
        }
    }
    return out;
}

/** 荣和（`checkWin(..., tsumo=false, ..., houtei)`）：13 张手牌 + 那张和了牌。 */
inline bool canWinRon(const ClaimAsk &a) {
    if (a.calledKind < 0) {
        return false;
    }
    Counts merged{};
    if (!winCounts(a.hand, static_cast<int>(a.melds.size()), a.calledKind, /*tsumo=*/false, merged)) {
        return false;
    }
    WinContext ctx;
    ctx.rules = a.rules;
    ctx.seat = a.seat;
    ctx.dealerSeat = a.dealer;
    ctx.roundWind = 27 + a.roundWind;
    ctx.tsumo = false;
    ctx.riichi = a.riichi && !a.doubleRiichi;
    ctx.doubleRiichi = a.riichi && a.doubleRiichi;
    ctx.ippatsu = a.riichi && a.ippatsu && a.riichi;
    ctx.chankan = false;
    ctx.rinshan = false;
    ctx.haitei = false;
    ctx.houtei = a.houtei;
    ctx.tenhou = false;
    ctx.chiihou = false;
    ctx.renhou = a.seat != a.dealer && a.playerDraws == 0 && !a.anyCall;
    // 燕返：打牌者**刚立直**那张宣言牌被打出（`discardsSinceRiichi == 1`），且比的是**牌 id**
    // （鸣牌段里 `lastDiscardTile == winTileId` 恒真，所以只由"是不是刚立直那张"决定）
    ctx.tsubame = a.from >= 0 && a.from != a.seat && a.fromRiichi
                  && a.fromDiscardsSinceRiichi == 1;
    ctx.kanburi = false;
    ctx.winKind = a.calledKind;
    ctx.doraIndicators = a.doraKinds;
    ctx.uraIndicators = a.uraKinds;                 // `checkWin` 走 includeUra = true
    ctx.allTileIds.clear();
    for (const Meld &m : a.melds) {
        for (int i = 0; i < m.tileCount; i++) {
            ctx.allTileIds.push_back(m.tiles[static_cast<size_t>(i)]);
        }
    }
    if (a.calledId >= 0) {
        ctx.allTileIds.push_back(a.calledId);
    }
    // ⚠ 荣和时赤五数 = 暗牌里的 + **和了牌本身若是赤五**
    int aka = a.akaInHand();
    if (a.calledId >= 0 && isRedId(a.calledId)) {
        aka++;
    }
    for (int i = 0; i < aka; i++) {
        const int kind = (i % 3 == 0) ? kAkaM : ((i % 3 == 1) ? kAkaP : kAkaS);
        ctx.allTileIds.push_back(idOf(kind, 0));
    }
    ctx.menzen = a.menzen;
    const HandScore s = evaluate(ctx, merged, a.melds, a.calledKind);
    return s.valid;
}

/** `canDaiminkan(seat, kind)`：未立直 + 还能杠 + 手里有 3 张。 */
inline bool canDaiminkan(const ClaimAsk &a) {
    return a.calledKind >= 0 && !a.riichi && canKanClaim(a)
           && a.hand[static_cast<size_t>(a.calledKind)] >= 3;
}

/** `Round.claimOptions` 的规范文本（与 `tools/RoundProbe.java` 逐字符一致）。 */
inline std::string claimOptionsText(const ClaimAsk &a) {
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

    // ① 荣和（振听时连问都不问）
    if (!isFuritenClaim(a) && canWinRon(a)) {
        add("ron");
    }
    // ② 河底：只能荣和或过
    if (a.houtei) {
        add("pass");
        return out;
    }
    // ③ 已立直：只能荣和或过
    if (a.riichi) {
        add("pass");
        return out;
    }
    // ④ 碰（每种赤宝取法一条）
    if (a.calledKind >= 0 && a.hand[static_cast<size_t>(a.calledKind)] >= 2) {
        for (const auto &v : akaVariants(a, a.calledKind, 2)) {
            add("pon=" + v[0] + "+" + v[1]);
        }
    }
    // ⑤ 大明杠（同一个选项里列出各取法）
    if (canDaiminkan(a)) {
        std::string seg = "kan=";
        bool any = false;
        for (const auto &v : akaVariants(a, a.calledKind, 3)) {
            if (any) {
                seg += ",";
            }
            seg += "daiminkan:" + kindToStr(a.calledKind, false) + ":" + v[0] + "+" + v[1] + "+" + v[2];
            any = true;
        }
        add(any ? seg : "");
    }
    // ⑥ 吃（只有下家；组合枚举与自家回合的 `chiSets` 同源）
    if (a.from >= 0 && a.seat == (a.from + 1) % 4) {
        const auto sets = chiSets(a.hand, a.calledKind);
        if (!sets.empty()) {
            std::string seg = "chi=";
            for (size_t i = 0; i < sets.size(); i++) {
                seg += (i ? "," : "");
                seg += sets[i][0] + "+" + sets[i][1];
            }
            add(seg);
        }
    }
    // ⑦ 过（永远最后）
    add("pass");
    return out;
}

}  // namespace trainer
