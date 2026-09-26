#include "action.hpp"

#include <algorithm>
#include <array>

#include "tiles.hpp"

namespace trainer {
namespace {

/** 是否请求赤宝牌（字符串形式 0m/0p/0s）。 */
bool isRedStr(const std::string &s) { return s.size() == 2 && s[0] == '0'; }

std::vector<std::string> splitChar(const std::string &s, char sep) {
    std::vector<std::string> out;
    size_t start = 0;
    while (true) {
        const size_t pos = s.find(sep, start);
        if (pos == std::string::npos) {
            out.push_back(s.substr(start));
            break;
        }
        out.push_back(s.substr(start, pos - start));
        start = pos + 1;
    }
    return out;
}

/**
 * 把"从手里取哪几张"规范化：**按槽位升序**、张数与牌码都必须合法，否则判失败。
 * 顺序固定下来键才唯一（服务端下发顺序与策略回包顺序都不该改变动作身份）。
 */
bool canonical(const std::vector<std::string> &codes, int need, std::vector<std::string> &out) {
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

std::string joinPlus(const std::vector<std::string> &v) {
    std::string out;
    for (size_t i = 0; i < v.size(); i++) {
        if (i > 0) {
            out += '+';
        }
        out += v[i];
    }
    return out;
}

}  // namespace

int actionTileIndex(const std::string &code) {
    if (code.size() != 2) {
        return -1;
    }
    const int kind = parseKind(code);
    if (kind < 0) {
        return -1;
    }
    if (isRedStr(code)) {
        // "0m"/"0p"/"0s" 的 kind 与普通 5 相同，所以另给三个槽位 34..36
        return 34 + suitOf(kind);
    }
    return kind;
}

std::string actionTileCode(int slot) {
    if (slot < 0 || slot >= kActionTileSlots) {
        return "";
    }
    if (slot < 34) {
        return kindToStr(slot, false);
    }
    static const std::array<char, 3> kSuits = {'m', 'p', 's'};
    return std::string("0") + kSuits[static_cast<size_t>(slot - 34)];
}

std::string Action::key() const {
    if (type == kActDiscard) {
        return tsumogiri ? ("discard:" + tile + "/tsumogiri") : ("discard:" + tile);
    }
    if (type == kActRiichi) {
        return "riichi:" + tile;
    }
    if (type == kActPon) {
        return hasTiles ? ("pon:" + tiles[0] + "+" + tiles[1]) : std::string(kActPon);
    }
    if (type == kActKan) {
        return hasTiles ? ("kan:" + kanKind + ":" + joinPlus(tiles))
                        : ("kan:" + kanKind + ":" + tile);
    }
    if (type == kActChi) {
        return "chi:" + tiles[0] + "+" + tiles[1];
    }
    return type;
}

int Action::index() const {
    const int t = actionTileIndex(tile);
    if (type == kActDiscard) {
        return t < 0 ? -1 : kActionDiscardBase + t;
    }
    if (type == kActRiichi) {
        return t < 0 ? -1 : kActionRiichiBase + t;
    }
    if (type == kActTsumo) {
        return kActionTsumoId;
    }
    if (type == kActRon) {
        return kActionRonId;
    }
    if (type == kActPon) {
        return hasTiles ? -1 : kActionPonId;       // 带取法的碰是参数化动作
    }
    if (type == kActPass) {
        return kActionPassId;
    }
    if (type == kActKyuushu) {
        return kActionKyuushuId;
    }
    return -1;
}

std::string Action::cmdTokens() const {
    std::string out = "type=" + type;
    if (type == kActDiscard || type == kActRiichi) {
        out += ";tile=" + tile;
        if (tsumogiri) {
            out += ";tsumogiri=1";
        }
    } else if (type == kActKan) {
        out += ";kind=" + kanKind;
        out += ";tile=" + tile;
        if (hasTiles) {
            out += ";tiles=" + joinPlus(tiles);
        }
    } else if (type == kActPon) {
        if (hasTiles) {
            out += ";tiles=" + joinPlus(tiles);
        }
    } else if (type == kActChi) {
        out += ";tiles=" + joinPlus(tiles);
    }
    return out;
}

Action actionOf(const std::string &type) {
    Action a;
    a.type = type;
    return a;
}

Action actionParse(const std::string &key, bool &ok) {
    ok = true;
    if (key == kActTsumo || key == kActRon || key == kActPon || key == kActPass
            || key == kActKyuushu) {
        return actionOf(key);
    }
    const auto starts = [&key](const char *p) {
        const std::string pre(p);
        return key.size() >= pre.size() && key.compare(0, pre.size(), pre) == 0;
    };
    if (starts("discard:")) {
        std::string rest = key.substr(8);
        const std::string suffix = "/tsumogiri";
        const bool tsumo = rest.size() >= suffix.size()
                && rest.compare(rest.size() - suffix.size(), suffix.size(), suffix) == 0;
        const std::string code = tsumo ? rest.substr(0, rest.size() - suffix.size()) : rest;
        if (actionTileIndex(code) < 0) {
            ok = false;
            return {};
        }
        Action a = actionOf(kActDiscard);
        a.tile = code;
        a.hasTile = true;
        a.tsumogiri = tsumo;
        return a;
    }
    if (starts("riichi:")) {
        const std::string code = key.substr(7);
        if (actionTileIndex(code) < 0) {
            ok = false;
            return {};
        }
        Action a = actionOf(kActRiichi);
        a.tile = code;
        a.hasTile = true;
        return a;
    }
    if (starts("pon:")) {
        std::vector<std::string> two;
        if (!canonical(splitChar(key.substr(4), '+'), 2, two)) {
            ok = false;
            return {};
        }
        Action a = actionOf(kActPon);
        a.tiles = two;
        a.hasTiles = true;
        return a;
    }
    if (starts("kan:")) {
        const std::vector<std::string> p = splitChar(key, ':');
        if (p.size() != 3) {
            ok = false;
            return {};
        }
        // 大明杠的第三段是"手里取哪三张"（带 `+`）；暗杠/加杠是单个牌码
        std::vector<std::string> hand;
        if (canonical(splitChar(p[2], '+'), 3, hand)) {
            Action a = actionOf(kActKan);
            a.kanKind = p[1];
            a.tile = kindToStr(parseKind(hand[0]), false);
            a.hasTile = true;
            a.tiles = hand;
            a.hasTiles = true;
            return a;
        }
        if (actionTileIndex(p[2]) < 0) {
            ok = false;
            return {};
        }
        Action a = actionOf(kActKan);
        a.kanKind = p[1];
        a.tile = p[2];
        a.hasTile = true;
        return a;
    }
    if (starts("chi:")) {
        const std::vector<std::string> p = splitChar(key.substr(4), '+');
        if (p.size() != 2 || actionTileIndex(p[0]) < 0 || actionTileIndex(p[1]) < 0) {
            ok = false;
            return {};
        }
        Action a = actionOf(kActChi);
        a.tiles = p;
        a.hasTiles = true;
        return a;
    }
    ok = false;
    return {};
}

Action actionFromCmdTokens(const std::string &tokens, bool &ok) {
    ok = false;
    std::string type;
    std::string tile;
    bool hasTile = false;
    std::string kind;
    bool hasKind = false;
    bool tsumogiri = false;
    std::vector<std::string> tiles;
    bool hasTiles = false;
    for (const std::string &field : splitChar(tokens, ';')) {
        const size_t eq = field.find('=');
        if (eq == std::string::npos) {
            continue;
        }
        const std::string k = field.substr(0, eq);
        const std::string v = field.substr(eq + 1);
        if (k == "type") {
            type = v;
        } else if (k == "tile") {
            tile = v;
            hasTile = true;
        } else if (k == "kind") {
            kind = v;
            hasKind = true;
        } else if (k == "tsumogiri") {
            tsumogiri = (v == "1" || v == "true");
        } else if (k == "tiles") {
            tiles = splitChar(v, '+');
            hasTiles = true;
        }
    }
    if (type.empty()) {
        return {};
    }
    if (type == kActDiscard) {
        if (!hasTile || actionTileIndex(tile) < 0) {
            return {};
        }
        Action a = actionOf(kActDiscard);
        a.tile = tile;
        a.hasTile = true;
        a.tsumogiri = tsumogiri;
        ok = true;
        return a;
    }
    if (type == kActRiichi) {
        if (!hasTile || actionTileIndex(tile) < 0) {
            return {};
        }
        Action a = actionOf(kActRiichi);
        a.tile = tile;
        a.hasTile = true;
        ok = true;
        return a;
    }
    if (type == kActKan) {
        if (!hasKind || !hasTile || actionTileIndex(tile) < 0) {
            return {};
        }
        Action a = actionOf(kActKan);
        a.kanKind = kind;
        a.tile = tile;
        a.hasTile = true;
        if (hasTiles) {
            std::vector<std::string> three;
            if (canonical(tiles, 3, three)) {
                a.tiles = three;
                a.hasTiles = true;
            }
        }
        ok = true;
        return a;
    }
    if (type == kActPon) {
        Action a = actionOf(kActPon);
        if (hasTiles) {
            std::vector<std::string> two;
            if (!canonical(tiles, 2, two)) {
                return {};                       // 取法非法 → 认不出（不猜）
            }
            a.tiles = two;
            a.hasTiles = true;
        }
        ok = true;
        return a;
    }
    if (type == kActChi) {
        if (!hasTiles || tiles.size() != 2 || actionTileIndex(tiles[0]) < 0
                || actionTileIndex(tiles[1]) < 0) {
            return {};
        }
        std::vector<std::string> two = tiles;
        std::sort(two.begin(), two.end(), [](const std::string &a, const std::string &b) {
            return actionTileIndex(a) < actionTileIndex(b);
        });
        Action a = actionOf(kActChi);
        a.tiles = two;
        a.hasTiles = true;
        ok = true;
        return a;
    }
    if (type == kActTsumo || type == kActRon || type == kActPass || type == kActKyuushu) {
        ok = true;
        return actionOf(type);
    }
    return {};
}

std::string actionResolveKey(const std::string &cmdTokens, const std::vector<std::string> &legalKeys,
                             bool &ok) {
    ok = false;
    if (legalKeys.empty()) {
        return {};
    }
    bool cmdOk = false;
    const Action exact = actionFromCmdTokens(cmdTokens, cmdOk);
    if (cmdOk) {
        const std::string exactKey = exact.key();
        for (const std::string &k : legalKeys) {
            if (k == exactKey) {
                ok = true;
                return k;
            }
        }
    }
    // 只对碰/杠开"同类型第一条"这个口子
    const std::string type = exact.type;
    if (!cmdOk || (type != kActPon && type != kActKan)) {
        return {};
    }
    for (const std::string &k : legalKeys) {
        bool lk = false;
        const Action a = actionParse(k, lk);
        if (!lk || a.type != type) {
            continue;
        }
        if (type == kActKan && a.kanKind != exact.kanKind) {
            continue;
        }
        ok = true;
        return k;
    }
    return {};
}

}  // namespace trainer
