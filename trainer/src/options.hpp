// 一次询问的**选项**（结构化） + 展开成动作空间 —— 对应 Java 的
// `List<Map<String,Object>> options` 与 `Action.enumerate(options)`。
//
// 为什么要在 C++ 侧也做一层结构：`legal`（= 展开后的动作键列表）是**数据集的动作空间**，
// 它的下标就是 `chosen_index`；而选项文本（`tools/RoundProbe.java` 那条对拍口径）也必须
// **从同一份结构派生** —— 两处各生成一遍的话，"选项给对了、动作空间错了"这类漂移不会被发现。
//
// 与 Java 的对照：
//   `Action.enumerate` 的每个分支逐条照抄（含 `canonical` 的槽位升序与"张数不符即退化"）；
//   `optionsToText` 是 C++ 独有的**规范文本**（只给对拍用）：`discard=…;riichi=…;tsumo;kan=…`。
#pragma once

#include <algorithm>
#include <array>
#include <string>
#include <vector>

#include "action.hpp"

namespace trainer {

/** `kan` 选项里的一条候选（Java `{"kind":…,"tile":…,"tiles":[…]?}`）。 */
struct KanEntry {
    std::string kind;                  // ankan / kakan / daiminkan
    std::string tile;                  // 牌码（暗杠/加杠是单个码；大明杠是被杠那张的码）
    std::vector<std::string> tiles;    // 大明杠的"从手里取哪三张"；其余为空
    bool hasTiles = false;
};

/** 一条询问选项。 */
struct Option {
    std::string type;                                          // discard/riichi/tsumo/ron/pon/chi/kan/kyuushu/pass
    std::vector<std::string> tiles;                            // discard / riichi / pon
    std::vector<KanEntry> kans;                                // kan
    std::vector<std::array<std::string, 2>> sets;              // chi
};

/** 是否下发过某类型的选项（Java `Round.hasOption`）。 */
inline bool optionHas(const std::vector<Option> &opts, const std::string &type) {
    for (const Option &o : opts) {
        if (o.type == type) {
            return true;
        }
    }
    return false;
}

namespace detail {

/** 牌码按**槽位**升序（`Action.canonical`）；张数不符 / 有非法码 → false。 */
inline bool canonicalCodes(const std::vector<std::string> &codes, int need,
                           std::vector<std::string> &out) {
    if (static_cast<int>(codes.size()) != need) {
        return false;
    }
    for (const std::string &c : codes) {
        if (actionTileIndex(c) < 0) {
            return false;
        }
    }
    out = codes;
    std::sort(out.begin(), out.end(), [](const std::string &a, const std::string &b) {
        return actionTileIndex(a) < actionTileIndex(b);
    });
    return true;
}

inline std::string joinPlus(const std::vector<std::string> &v) {
    std::string out;
    for (size_t i = 0; i < v.size(); i++) {
        if (i > 0) {
            out += '+';
        }
        out += v[i];
    }
    return out;
}

}  // namespace detail

/**
 * 选项 → 具体动作（与 Java `Action.enumerate` **逐分支**一致，顺序也一致）。
 *
 * 这是"合法动作集 + 掩码"：展开出来的每一项都保证被状态机接受，训练侧不需要知道任何规则。
 */
inline std::vector<Action> enumerateOptions(const std::vector<Option> &opts) {
    std::vector<Action> out;
    for (const Option &o : opts) {
        if (o.type == kActDiscard) {
            for (const std::string &t : o.tiles) {
                Action a = actionOf(kActDiscard);
                a.tile = t;
                a.hasTile = true;
                out.push_back(a);
            }
        } else if (o.type == kActRiichi) {
            for (const std::string &t : o.tiles) {
                Action a = actionOf(kActRiichi);
                a.tile = t;
                a.hasTile = true;
                out.push_back(a);
            }
        } else if (o.type == kActKan) {
            for (const KanEntry &k : o.kans) {
                Action a = actionOf(kActKan);
                a.kanKind = k.kind;
                a.tile = k.tile;
                a.hasTile = true;
                std::vector<std::string> three;
                // 大明杠带 `tiles`（手里取哪三张）；暗杠/加杠不带（那两种没有取法可挑）
                if (k.hasTiles && detail::canonicalCodes(k.tiles, 3, three)) {
                    a.tiles = three;
                    a.hasTiles = true;
                }
                out.push_back(a);
            }
        } else if (o.type == kActChi) {
            for (const std::array<std::string, 2> &pair : o.sets) {
                Action a = actionOf(kActChi);
                a.tiles = {pair[0], pair[1]};
                a.hasTiles = true;
                out.push_back(a);
            }
        } else if (o.type == kActPon) {
            // 取法进键：手里有赤五时，"用普通五碰"与"用赤五碰"是两个不同的合法动作。
            // `tiles` 缺失 = 老服务端下发的裸 pon（兼容形态，键就是 "pon"）。
            std::vector<std::string> two;
            if (detail::canonicalCodes(o.tiles, 2, two)) {
                Action a = actionOf(kActPon);
                a.tiles = two;
                a.hasTiles = true;
                out.push_back(a);
            } else {
                out.push_back(actionOf(kActPon));
            }
        } else if (o.type == kActTsumo || o.type == kActRon || o.type == kActPass
                   || o.type == kActKyuushu) {
            out.push_back(actionOf(o.type));
        }
        // 认不出的选项**不进动作空间**（新协议字段不该让老训练器乱猜）
    }
    return out;
}

/**
 * 选项的**规范文本**（只给 `tools/trainer-opts-parity.mjs` 逐字符对拍用）。
 *
 * 与 `tools/RoundProbe.java` 的写法一一对应：段间 `;`、段内 `,`；
 * `kan` 段里每条是 `kind:tile[:t1+t2+t3]`。空段（不可能出现）直接跳过。
 */
inline std::string optionsToText(const std::vector<Option> &opts) {
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
    for (const Option &o : opts) {
        if (o.type == kActDiscard || o.type == kActRiichi) {
            std::string seg = o.type + "=";
            for (size_t i = 0; i < o.tiles.size(); i++) {
                seg += (i ? "," : "");
                seg += o.tiles[i];
            }
            add(seg);
        } else if (o.type == kActTsumo || o.type == kActKyuushu || o.type == kActRon
                   || o.type == kActPass) {
            add(o.type);
        } else if (o.type == kActPon) {
            add(o.tiles.size() == 2 ? ("pon=" + o.tiles[0] + "+" + o.tiles[1]) : std::string());
        } else if (o.type == kActChi) {
            std::string seg = "chi=";
            for (size_t i = 0; i < o.sets.size(); i++) {
                seg += (i ? "," : "");
                seg += o.sets[i][0] + "+" + o.sets[i][1];
            }
            add(seg);
        } else if (o.type == kActKan) {
            std::string seg = "kan=";
            bool any = false;
            for (const KanEntry &k : o.kans) {
                if (any) {
                    seg += ',';
                }
                seg += k.kind + ":" + k.tile;
                if (k.hasTiles) {
                    seg += ":" + detail::joinPlus(k.tiles);
                }
                any = true;
            }
            add(any ? seg : std::string());
        }
    }
    return out;
}

}  // namespace trainer
