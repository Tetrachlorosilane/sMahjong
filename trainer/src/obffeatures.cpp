#include "obffeatures.hpp"

#include <algorithm>

#include "action.hpp"
#include "danger.hpp"
#include "handeval.hpp"
#include "shanten.hpp"

namespace trainer {
namespace {

/** 34 维整数数组 → `Counts`（缺项/非数组全 0；与 Java `ObsFeatures.ints` 同口径）。 */
void fillCounts(const JVal *a, Counts &out) {
    out.fill(0);
    if (a == nullptr || a->type != JVal::Type::Arr) {
        return;
    }
    for (size_t i = 0; i < out.size() && i < a->arr.size(); i++) {
        const int v = a->arr[i].asInt(0);
        out[i] = static_cast<uint8_t>(v < 0 ? 0 : (v > 255 ? 255 : v));
    }
}

std::string toLower(std::string s) {
    for (char &c : s) {
        if (c >= 'A' && c <= 'Z') {
            c = static_cast<char>(c - 'A' + 'a');
        }
    }
    return s;
}

/** 副露种类（= Java `Meld.Kind.of`；认不出返回 false 而不是静默取一种）。 */
bool meldKindOf(const std::string &s, Meld::Kind &out) {
    const std::string k = toLower(s);
    if (k == "chi") {
        out = Meld::Kind::CHI;
    } else if (k == "pon") {
        out = Meld::Kind::PON;
    } else if (k == "daiminkan") {
        out = Meld::Kind::DAIMINKAN;
    } else if (k == "ankan") {
        out = Meld::Kind::ANKAN;
    } else if (k == "kakan") {
        out = Meld::Kind::KAKAN;
    } else {
        return false;
    }
    return true;
}

/** `obs.melds[s][i]` → `Meld`（牌码 → 合成 id：同一 kind 的 copy 依次取 0,1,2,3）。 */
bool meldOf(const JVal &m, Meld &out) {
    if (m.type != JVal::Type::Obj) {
        return false;
    }
    const JVal *kindJ = m.find("kind");
    if (kindJ == nullptr || kindJ->type != JVal::Type::Str) {
        return false;
    }
    Meld::Kind kind = Meld::Kind::CHI;
    if (!meldKindOf(kindJ->strVal, kind)) {
        return false;
    }
    const JVal *codes = m.find("tiles");
    if (codes == nullptr || codes->type != JVal::Type::Arr || codes->arr.empty()) {
        return false;
    }
    const int n = static_cast<int>(codes->arr.size());
    if (n > 4) {
        return false;                       // 真实副露最多 4 张（杠）
    }
    std::array<int, 4> ids{};
    std::array<int, kKindCount> used{};
    for (int i = 0; i < n; i++) {
        const int k = parseKind(codes->arr[static_cast<size_t>(i)].asStr());
        if (k < 0) {
            return false;
        }
        ids[static_cast<size_t>(i)] = idOf(k, used[static_cast<size_t>(k)]++);
    }
    const JVal *called = m.find("called_tile");
    const int calledKind = called == nullptr ? -1 : parseKind(called->asStr());
    const int calledId = calledKind < 0 ? ids[0] : idOf(calledKind, 0);
    const int from = m.find("from") == nullptr ? -1 : m.find("from")->asInt(-1);
    out = Meld(kind, ids.data(), n, from, calledId);
    return true;
}

/** 只为"面子形状"合成一副副露（特征只需要 kind 与 tile 的 kind，见 `Meld`）。 */
Meld synthetic(Meld::Kind kind, const std::vector<std::string> &codes) {
    std::array<int, 4> ids{};
    std::array<int, kKindCount> used{};
    const int n = std::min(4, static_cast<int>(codes.size()));
    for (int i = 0; i < n; i++) {
        const int k = std::max(0, parseKind(codes[static_cast<size_t>(i)]));
        ids[static_cast<size_t>(i)] = idOf(k, used[static_cast<size_t>(k)]++);
    }
    const int first = n > 0 ? std::max(0, parseKind(codes[0])) : 0;
    return Meld(kind, ids.data(), std::max(1, n), -1, idOf(first, 0));
}

/** 被鸣那张缺失时的兜底猜测（= Java `ObsFeatures.guessedThird`）——轨迹里其实总带。 */
bool guessedThird(Meld::Kind kind, const std::vector<std::string> &handCodes, std::string &out) {
    if (kind != Meld::Kind::CHI || handCodes.size() != 2) {
        if (handCodes.empty()) {
            return false;
        }
        out = handCodes[0];
        return true;
    }
    const int a = parseKind(handCodes[0]);
    const int b = parseKind(handCodes[1]);
    const int lo = std::min(a, b);
    const int hi = std::max(a, b);
    if (hi - lo == 2) {
        out = kindToStr(lo + 1, false);                   // 坎张：被鸣的是中间那张
        return true;
    }
    if (hi - lo == 1) {
        if (lo % 9 >= 1) {
            out = kindToStr(lo - 1, false);               // 边张/两面：优先补小的那边
        } else {
            out = kindToStr(hi + 1, false);
        }
        return true;
    }
    out = kindToStr(lo, false);
    return true;
}

/**
 * 合成**鸣来的那副面子**：键里只有"从手里取的 1~3 张"，还差**被鸣的那张**。
 *
 * <p>⚠ 别省这一张：张数不对会让 `doraCount` 少算，也可能让和了形分解失真。
 */
Meld calledMeld(Meld::Kind kind, const std::vector<std::string> &handCodes,
                const std::string &calledTile) {
    std::vector<std::string> codes = handCodes;
    if (!calledTile.empty()) {
        codes.push_back(calledTile);
    } else {
        std::string guess;
        if (guessedThird(kind, handCodes, guess)) {
            codes.push_back(guess);
        }
    }
    return synthetic(kind, codes);
}

/** 从 `counts` 里扣掉这些牌码（不够扣返回 false）。 */
bool take(Counts &counts, const std::vector<std::string> &codes) {
    if (codes.empty()) {
        return false;
    }
    Counts need{};
    need.fill(0);
    for (const std::string &c : codes) {
        const int k = parseKind(c);
        if (k < 0) {
            return false;
        }
        need[static_cast<size_t>(k)]++;
    }
    for (int k = 0; k < kKindCount; k++) {
        if (need[static_cast<size_t>(k)] > counts[static_cast<size_t>(k)]) {
            return false;
        }
    }
    for (int k = 0; k < kKindCount; k++) {
        counts[static_cast<size_t>(k)] = static_cast<uint8_t>(
                counts[static_cast<size_t>(k)] - need[static_cast<size_t>(k)]);
    }
    return true;
}

}  // namespace

bool featureViewOfObs(const JVal &obs, FeatureView &v) {
    if (obs.type != JVal::Type::Obj) {
        return false;
    }
    v = FeatureView{};
    const JVal *seat = obs.find("seat");
    v.seat = seat == nullptr ? 0 : seat->asInt(0);
    fillCounts(obs.find("hand"), v.hand);
    fillCounts(obs.find("visible"), v.visible);

    // ---- 副露：只看**自家**那一行（别家手牌不进任何特征）----
    const JVal *meldsAll = obs.find("melds");
    if (meldsAll != nullptr && meldsAll->type == JVal::Type::Arr
            && v.seat >= 0 && static_cast<size_t>(v.seat) < meldsAll->arr.size()) {
        const JVal &mine = meldsAll->arr[static_cast<size_t>(v.seat)];
        if (mine.type == JVal::Type::Arr) {
            for (const JVal &m : mine.arr) {
                Meld md;
                if (meldOf(m, md)) {
                    v.melds.push_back(md);
                }
            }
        }
    }

    // ---- 四家牌河 → 每家的牌种计数（危险度的"现物/筋"就靠它）----
    const JVal *discardsAll = obs.find("discards");
    if (discardsAll != nullptr && discardsAll->type == JVal::Type::Arr) {
        for (int s = 0; s < 4 && static_cast<size_t>(s) < discardsAll->arr.size(); s++) {
            const JVal &row = discardsAll->arr[static_cast<size_t>(s)];
            if (row.type != JVal::Type::Arr) {
                continue;
            }
            for (const JVal &code : row.arr) {
                const int k = parseKind(code.asStr());
                if (k >= 0) {
                    v.rivers[static_cast<size_t>(s)][static_cast<size_t>(k)]++;
                }
            }
        }
    }

    const JVal *riichiJson = obs.find("riichi");
    if (riichiJson != nullptr && riichiJson->type == JVal::Type::Arr) {
        for (int s = 0; s < 4 && static_cast<size_t>(s) < riichiJson->arr.size(); s++) {
            // Java `Boolean.TRUE.equals(...)`：只有真正的 `true` 才算
            v.riichi[static_cast<size_t>(s)] = riichiJson->arr[static_cast<size_t>(s)].type
                    == JVal::Type::Bool && riichiJson->arr[static_cast<size_t>(s)].boolVal;
        }
    }

    // ⚠ 这里存的是**牌种 kind**（不是牌 id）：`doraCount → Tiles.doraFrom(kind)`，
    //   包成 `Tiles.id(k,0)` 会让宝牌整个算错。
    const JVal *doraJson = obs.find("dora_indicators");
    if (doraJson != nullptr && doraJson->type == JVal::Type::Arr) {
        for (const JVal &code : doraJson->arr) {
            const int k = parseKind(code.asStr());
            if (k >= 0) {
                v.dora.push_back(k);
            }
        }
    }

    const JVal *called = obs.find("called_tile");
    if (called != nullptr && called->type == JVal::Type::Str && !called->strVal.empty()) {
        v.calledTile = called->strVal;
    }
    // `turn` 的口径与 `Bot.HandState.of` 完全一致（已打出的总张数 / 4）
    const JVal *td = obs.find("total_discards");
    v.turn = (td == nullptr ? 0 : td->asInt(0)) / 4;
    return true;
}

std::array<int, kPerDecision> perDecision(const FeatureView &v) {
    std::array<int, kPerDecision> out{};
    for (int k = 0; k < kKindCount; k++) {
        out[static_cast<size_t>(k)]
                = dangerWorst(k, v.visible, v.rivers, v.riichi, v.turn, v.seat);
        out[static_cast<size_t>(kKindCount + k)]
                = dangerWorstAgainstRiichi(k, v.visible, v.rivers, v.riichi, v.turn, v.seat);
    }
    return out;
}

int doraCount(const Counts &counts, const std::vector<Meld> &melds, const std::vector<int> &dora) {
    if (dora.empty()) {
        return 0;
    }
    int n = 0;
    for (int ind : dora) {
        const int d = doraFrom(ind);
        if (d < 0 || d >= kKindCount) {
            continue;
        }
        n += counts[static_cast<size_t>(d)];
        for (const Meld &m : melds) {
            for (int i = 0; i < m.tileCount; i++) {
                if (kindOf(m.tiles[static_cast<size_t>(i)]) == d) {
                    n++;
                }
            }
        }
    }
    return n;
}

std::array<int, kPerCandidate> perCandidate(const FeatureView &v, const std::string &key) {
    std::array<int, kPerCandidate> out{};
    out.fill(0);
    bool parsed = false;
    const Action a = actionParse(key, parsed);
    if (!parsed) {
        return out;                          // Java `Action.parse` 返回 null
    }
    if (a.type == kActTsumo || a.type == kActRon) {
        return out;                          // 终局动作：没有"之后的形态"可言
    }
    Counts counts = v.hand;
    std::vector<Meld> melds = v.melds;
    if (a.type == kActDiscard || a.type == kActRiichi) {
        const int k = parseKind(a.tile);
        if (k < 0 || counts[static_cast<size_t>(k)] == 0) {
            return out;
        }
        counts[static_cast<size_t>(k)]--;
    } else if (a.type == kActChi) {
        if (!take(counts, a.tiles)) {
            return out;
        }
        melds.push_back(calledMeld(Meld::Kind::CHI, a.tiles, v.calledTile));
    } else if (a.type == kActPon) {
        if (!take(counts, a.tiles)) {
            return out;
        }
        melds.push_back(calledMeld(Meld::Kind::PON, a.tiles, v.calledTile));
    } else if (a.type == kActKan) {
        const int k = parseKind(a.tile);
        if (a.kanKind == "daiminkan" || a.kanKind == "ankan") {
            if (a.kanKind == "ankan" && k >= 0 && counts[static_cast<size_t>(k)] >= 4) {
                counts[static_cast<size_t>(k)] =
                        static_cast<uint8_t>(counts[static_cast<size_t>(k)] - 4);
                melds.push_back(synthetic(Meld::Kind::ANKAN, {a.tile, a.tile, a.tile, a.tile}));
            } else if (!a.tiles.empty()) {
                if (!take(counts, a.tiles)) {
                    return out;
                }
                Meld::Kind mk = Meld::Kind::DAIMINKAN;
                if (!meldKindOf(a.kanKind, mk)) {
                    mk = Meld::Kind::DAIMINKAN;
                }
                melds.push_back(calledMeld(mk, a.tiles, v.calledTile));
            } else if (k >= 0 && counts[static_cast<size_t>(k)] >= 3) {
                counts[static_cast<size_t>(k)] =
                        static_cast<uint8_t>(counts[static_cast<size_t>(k)] - 3);
                melds.push_back(calledMeld(Meld::Kind::DAIMINKAN, {a.tile}, v.calledTile));
            } else {
                return out;
            }
        } else {                             // 加杠：手里那一张并进已有的碰（那副碰已在 melds 里）
            if (k < 0 || counts[static_cast<size_t>(k)] == 0) {
                return out;
            }
            counts[static_cast<size_t>(k)]--;
        }
    }
    // 统一规则：张数超过 `13 − 3×新副露数` 就再挑一张打（向听最小、同向听取小下标）
    const int target = 13 - 3 * static_cast<int>(melds.size());
    if (sumOf(counts) > target) {
        int best = -1;
        int bestSh = 99;
        Counts probe = counts;
        for (int k = 0; k < kKindCount; k++) {
            if (probe[static_cast<size_t>(k)] == 0) {
                continue;
            }
            probe[static_cast<size_t>(k)]--;
            const int sh = shantenMin(probe, static_cast<int>(melds.size()));
            probe[static_cast<size_t>(k)]++;
            if (sh < bestSh) {
                bestSh = sh;
                best = k;
            }
        }
        if (best >= 0) {
            counts[static_cast<size_t>(best)]--;
        }
    }
    const Snapshot snap = evalOf(counts, static_cast<int>(melds.size()), v.visible);
    out[0] = snap.tenpai ? 0 : snap.shanten;   // 听牌记 0（与 shanten 同轴，和了形 -1 不出现）
    out[1] = snap.advanceTypes;
    out[2] = snap.advanceTiles;
    out[3] = snap.waitTypes;
    out[4] = snap.waitTiles;
    out[5] = snap.goodWaitTypes;
    out[6] = snap.goodWaitTiles;
    out[7] = doraCount(counts, melds, v.dora);
    return out;
}

std::string featureLayout() {
    return "ObsFeatures v" + std::to_string(kFeatureVersion)
            + " perDecision=" + std::to_string(kPerDecision)
            + "（danger_worst[34] + danger_riichi[34]）"
            + " perCandidate=" + std::to_string(kPerCandidate)
            + "（shanten, advance_types, advance_tiles, wait_types, wait_tiles,"
              " good_wait_types, good_wait_tiles, dora_count）";
}

}  // namespace trainer
