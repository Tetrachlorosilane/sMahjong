// `Round` 状态机的实现 —— 与 Java `mahjong.game.Round` 逐句对应（见 round.hpp 顶部说明）。
//
// 阅读顺序建议与 Java 一致：`play()`(主循环) → `claimPhase()`(鸣牌) → `turnKan()`(杠)
// → `agariTsumo/agariRon()`(和了) → `exhaustive()/abort()`(流局)。
#include "round.hpp"

#include <algorithm>
#include <map>
#include <set>

#include "claimoptions.hpp"
#include "handeval.hpp"
#include "roundscoring.hpp"
#include "shanten.hpp"
#include "table.hpp"
#include "turnoptions.hpp"
#include "visible.hpp"
#include "yaku_codes.hpp"

namespace trainer {
namespace {

/** `Meld.Kind.wire()`（报文/观测里的 kind 串）。 */
inline const char *meldKindWire(Meld::Kind k) {
    switch (k) {
        case Meld::Kind::CHI: return "chi";
        case Meld::Kind::PON: return "pon";
        case Meld::Kind::DAIMINKAN: return "daiminkan";
        case Meld::Kind::ANKAN: return "ankan";
        case Meld::Kind::KAKAN: return "kakan";
    }
    return "chi";
}

inline bool containsInt(const std::vector<int> &v, int x) {
    return std::find(v.begin(), v.end(), x) != v.end();
}

/** `Tiles.isRedStr`（"0m" / "0p" / "0s"）。 */
inline bool isRedStr(const std::string &s) { return s.size() == 2 && s[0] == '0'; }

/**
 * 按**牌码**在一摞牌里找那张（Java `Round.findByCode`）：先按 kind 缩小，再按"要不要赤五"
 * 精确匹配（`0m` 要赤、`5m` 要普通）；要普通却只有赤五时退回赤五。
 */
int findByCode(const std::vector<int> &pile, const std::string &s) {
    const int kind = parseKind(s);
    if (kind < 0) {
        return -1;
    }
    const bool wantRed = isRedStr(s);
    int fallback = -1;
    for (int id : pile) {
        if (kindOf(id) != kind) {
            continue;
        }
        if (wantRed) {
            if (isRedId(id)) {
                return id;
            }
        } else {
            if (!isRedId(id)) {
                return id;
            }
            fallback = id;
        }
    }
    return fallback;
}

/**
 * 出牌取牌的**纯判据**（Java `Round.pickDiscardId`）：牌码决定打哪张。
 * ⚠ 手切必须从"除摸牌位之外"的暗手里找（同码牌在按 id 排序的 hand[] 里谁先撞上纯属偶然，
 *   不排除摸牌位会有约一半概率把手切演成摸切 —— AGENTS §2.3-11）。
 */
int pickDiscardId(const std::vector<int> &hand, int drawn, const std::string &code,
                  bool claimTsumogiri) {
    if (claimTsumogiri && drawn >= 0 && code == tileToStr(drawn)) {
        return drawn;
    }
    if (drawn >= 0) {
        std::vector<int> rest = hand;
        const auto it = std::find(rest.begin(), rest.end(), drawn);   // ⚠ 按**值**删
        if (it != rest.end()) {
            rest.erase(it);
        }
        const int id = findByCode(rest, code);
        if (id >= 0) {
            return id;
        }
    }
    return findByCode(hand, code);
}

/** 出牌合法性的**服务端权威判据**（Java `Round.discardAllowed`），与下发选项同一把尺子。 */
bool discardAllowed(const std::vector<int> &hand, bool riichi, int drawn,
                    const std::vector<int> &forbidden, bool kuikae, int id) {
    if (id < 0 || !containsInt(hand, id)) {
        return false;
    }
    if (riichi && drawn >= 0 && id != drawn) {
        return false;
    }
    return !(kuikae && containsInt(forbidden, kindOf(id)));
}

/** 按**值**从手里删掉一张（Java `hand.remove((Integer) id)`）。 */
void removeOne(std::vector<int> &hand, int id) {
    const auto it = std::find(hand.begin(), hand.end(), id);
    if (it != hand.end()) {
        hand.erase(it);
    }
}

}  // namespace

Rules Round::tableRules(Table *t) { return t->rules; }

bool Round::awaySeat(int seat) const { return !table->seat(seat).bot; }

// ================================================================= 主循环

RoundResult Round::play() {
    setup();
    table->currentRound = this;

    int turn = dealer;
    bool needDraw = true;
    // 庄家第一巡：第 14 张已随配牌发出，不能再摸，也不能重复下发 draw 事件
    bool dealerOpening = true;
    bool rinshanNext = false;

    while (true) {
        int drawn = -1;
        bool isRinshan = false;
        bool haitei = false;
        if (needDraw) {
            isRinshan = rinshanNext;
            rinshanNext = false;
            const bool opening = dealerOpening;
            dealerOpening = false;
            if (opening) {
                // 庄家起手第 14 张：配牌时已入手，这里只把它当作「本次摸到的牌」交给后续判定
                drawn = openingTile;
            } else if (isRinshan) {
                drawn = wall.drawRinshan();
            } else {
                if (wall.tilesLeft() <= 0) {
                    return exhaustive();
                }
                drawn = wall.draw();
                haitei = wall.atLastLiveTile();
            }
            if (!opening) {
                hand[static_cast<size_t>(turn)].push_back(drawn);
            }
            playerDraws[static_cast<size_t>(turn)]++;      // 天和判定需要它为 1
            sortHands();
            furiten.temp[static_cast<size_t>(turn)] = false;
        }

        // ---------- 询问
        const std::vector<Option> opts = turnOptions(turn, drawn, isRinshan);
        bool hasWinNote = false;
        std::string winNote;
        if (drawn >= 0 && !optionHas(opts, kActTsumo)) {
            hasWinNote = winBlockReason(turn, drawn, true, winNote);
        }
        const Observation obs = makeObservation(turn, "turn", opts, drawn, isRinshan, -1, false, "",
                                                hasWinNote, winNote);
        Cmd act = ask(turn, "turn", obs, opts, -1);
        std::string type = "discard";
        if (act.valid && !act.action.type.empty()) {
            type = act.action.type;
        }

        if (type == kActTsumo && drawn >= 0) {
            HandScore sc;
            if (checkWin(turn, drawn, true, isRinshan, haitei, false, false, sc)) {
                return agariTsumo(turn, drawn, sc);
            }
            type = "discard";
            act = Cmd{};
        }
        // 立直振听：**能给自摸却见逃**（走到这里就说明没有和）→ 本局之内不能再荣和。
        if (riichi[static_cast<size_t>(turn)] && drawn >= 0 && optionHas(opts, kActTsumo)) {
            furiten.perm[static_cast<size_t>(turn)] = true;
        }
        if (type == kActKyuushu) {
            return abort("九种九牌");
        }
        if (type == kActKan) {
            const int kanBefore = kanCount;
            bool ended = false;
            const RoundResult r = turnKan(turn, act, drawn, ended);
            if (ended) {
                return r;
            }
            if (kanCount == kanBefore) {
                // ⚠ 杠**没成立**（牌不在手里 / 动作过期 / 名额已满）：绝不能顺着往下发岭上牌。
                //   与「立直不成立」同一条兜底：退回默认摸切，绝不替玩家打出一张他没选的牌。
                type = "discard";
                act = Cmd{};
            } else {
                needDraw = true;
                rinshanNext = true;
                continue;
            }
        }

        // ---------- 打牌
        bool declareRiichi = false;
        bool badRiichi = false;
        int discardId = -1;
        if (type == kActRiichi) {
            const int want = resolveTile(act, turn);
            if (want >= 0 && canRiichiSeat(turn, want)) {
                declareRiichi = true;
                discardId = want;
            } else {
                // 立直不成立时**绝不能**把「宣言牌」当普通打牌用（旧代码就是这么退化的）
                badRiichi = true;
            }
        }
        if (!declareRiichi) {
            if (badRiichi) {
                discardId = defaultDiscardId(turn, drawn);
            } else {
                const bool wantTsumogiri = drawn >= 0 && act.action.tsumogiri;
                discardId = act.valid ? resolveDiscardId(act, turn, wantTsumogiri, drawn) : -1;
                if (!discardAllowed(hand[static_cast<size_t>(turn)],
                                    riichi[static_cast<size_t>(turn)], drawn, forbiddenDiscard,
                                    rules.kuikae, discardId)) {
                    discardId = defaultDiscardId(turn, drawn);
                }
            }
        }
        const bool tsumogiri = drawn >= 0 && discardId == drawn;
        if (declareRiichi) {
            doRiichi(turn, discardId);
        }
        removeOne(hand[static_cast<size_t>(turn)], discardId);
        sortHands();
        recordDiscard(turn, discardId, declareRiichi);
        totalDiscards++;
        lastDiscardSeat = turn;
        lastDiscardTile = discardId;
        lastDiscardTsumogiri = tsumogiri;
        chankanKoyaku = kanJustHappened;
        kanJustHappened = false;
        forbiddenDiscard.clear();
        incRiichiDiscard(turn);

        // ---------- 鸣牌询问
        const Claim cl = claimPhase(turn, discardId, declareRiichi);
        if (!cl.isNull && cl.hasAbort) {
            return abort(cl.abortReason);                  // 三家和了：中途流局
        }
        if (!cl.isNull && cl.type == Claim::Type::RON) {
            return agariRon(cl.multiRon, turn, discardId, false, declareRiichi);
        }
        // 四杠散了：**成立即流局**，且必须在"鸣牌落地"之前判（三种豁免由和了路径先 return 天然满足）
        if (fourKanAbortNow()) {
            return abort("四杠散了");
        }
        if (cl.isNull) {
            if (rules.fourRiichiAbort && riichi[0] && riichi[1] && riichi[2] && riichi[3]) {
                return abort("四家立直");
            }
            if (rules.fourWindAbort && !anyCall && totalDiscards == 4
                    && discards[0].size() == 1 && discards[1].size() == 1 && discards[2].size() == 1
                    && discards[3].size() == 1) {
                const int k0 = kindOf(discards[0][0]);
                if (isWind(k0) && kindOf(discards[1][0]) == k0 && kindOf(discards[2][0]) == k0
                        && kindOf(discards[3][0]) == k0) {
                    return abort("四风连打");
                }
            }
        }

        if (cl.isNull) {
            turn = (turn + 1) % 4;
            needDraw = true;
            rinshanNext = false;
            continue;
        }
        // 鸣牌成立
        applyMeld(cl, turn, discardId);
        // `cl.type == KAN` 是**第 4 次杠本身**（大明杠）：那一手还要摸岭上牌，
        // 岭上开花/放铳的豁免可能发生，所以这一刻不能判流局 —— 等那张牌落地再看。
        if (cl.type != Claim::Type::KAN && fourKanAbortNow()) {
            return abort("四杠散了");
        }
        if (cl.type == Claim::Type::KAN) {
            needDraw = true;
            rinshanNext = true;
        } else {
            needDraw = false;
            rinshanNext = false;
        }
        turn = cl.seat;
    }
}

// ================================================================= 记账

void Round::sortHands() {
    for (auto &h : hand) {
        std::sort(h.begin(), h.end(), &RoundState::compareTile);
    }
}

bool Round::noteDiscard(int seat, bool declareRiichi) {
    const bool sideways = declareRiichi || sidewaysPending[static_cast<size_t>(seat)];
    if (sideways) {
        sidewaysDiscard[static_cast<size_t>(seat)] =
            static_cast<int>(discards[static_cast<size_t>(seat)].size()) - 1;
        sidewaysPending[static_cast<size_t>(seat)] = false;
    }
    return sideways;
}

void Round::noteCalledFromRiver(int from, int index) {
    if (from < 0 || from > 3 || index < 0) {
        return;
    }
    if (sidewaysDiscard[static_cast<size_t>(from)] == index) {
        sidewaysDiscard[static_cast<size_t>(from)] = -1;
        sidewaysPending[static_cast<size_t>(from)] = true;      // 顺延给下一张打出的牌
    } else if (sidewaysDiscard[static_cast<size_t>(from)] > index) {
        sidewaysDiscard[static_cast<size_t>(from)]--;
    }
}

/** 「出牌」的**唯一**记账点：牌河 + 曾经打出过（振听）+ 横置。 */
void Round::recordDiscard(int seat, int id, bool declareRiichi) {
    discards[static_cast<size_t>(seat)].push_back(id);
    furiten.recordDiscard(seat, kindOf(id));
    noteDiscard(seat, declareRiichi);
}

void Round::removeCalledFromRiver(int from, int calledIndex) {
    if (calledIndex < 0 || from < 0 || from > 3) {
        return;
    }
    if (calledIndex < static_cast<int>(discards[static_cast<size_t>(from)].size())) {
        discards[static_cast<size_t>(from)].erase(
            discards[static_cast<size_t>(from)].begin() + calledIndex);
    }
    discardCalledIndex[static_cast<size_t>(from)] = calledIndex;
    noteCalledFromRiver(from, calledIndex);
}

void Round::incRiichiDiscard(int seat) {
    if (riichi[static_cast<size_t>(seat)]) {
        discardsSinceRiichi[static_cast<size_t>(seat)]++;
        if (discardsSinceRiichi[static_cast<size_t>(seat)] >= 2) {
            ippatsu[static_cast<size_t>(seat)] = false;
        }
    }
}

void Round::doRiichi(int seat, int discardTile) {
    (void) discardTile;                    // Java 的形参同样没用到（宣言牌由调用方打出去）
    riichi[static_cast<size_t>(seat)] = true;
    if (!anyCall && playerDraws[static_cast<size_t>(seat)] == 1
            && discards[static_cast<size_t>(seat)].empty()) {
        doubleRiichi[static_cast<size_t>(seat)] = true;
    }
    ippatsu[static_cast<size_t>(seat)] = true;
    discardsSinceRiichi[static_cast<size_t>(seat)] = 0;
    scores[static_cast<size_t>(seat)] -= 1000;
    sticks++;
}

void Round::clearIppatsu() {
    for (int i = 0; i < 4; i++) {
        ippatsu[static_cast<size_t>(i)] = false;
    }
}

void Round::revealKanDora() {
    if (rules.kanDora) {
        wall.revealDora();
    }
}

// ================================================================= 门槛判据

bool Round::riichiAllowed(int seat) const {
    const size_t s = static_cast<size_t>(seat);
    if (!menzen[s] || riichi[s]) {
        return false;
    }
    if (scores[s] < rules.riichiMinScore || wall.tilesLeft() < rules.riichiMinTilesLeft) {
        return false;
    }
    return !(rules.riichiNoHaitei && wall.atLastLiveTile());
}

bool Round::canRiichiSeat(int seat, int tileId) const {
    if (!riichiAllowed(seat)) {
        return false;
    }
    if (!containsInt(hand[static_cast<size_t>(seat)], tileId)) {
        return false;
    }
    Counts c{};
    for (int id : hand[static_cast<size_t>(seat)]) {
        if (id == tileId) {
            continue;                      // ⚠ 按 **id** 去掉（aka==0 时同一 id 出现两次会一起去掉）
        }
        c[static_cast<size_t>(kindOf(id))]++;
    }
    return !waits(c, static_cast<int>(melds[static_cast<size_t>(seat)].size())).empty();
}

bool Round::ankanKeepsShape(int seat, int kind) const {
    if (isHonor(kind)) {
        return true;                       // 字牌不可能进顺子
    }
    const Counts c = concealCounts(seat);
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

bool Round::kanAllowedByRiichi(int seat, int kind, int drawn) const {
    if (!riichi[static_cast<size_t>(seat)]) {
        return true;
    }
    const Counts c = concealCounts(seat);
    Counts before = c;
    if (drawn >= 0) {
        const int dk = kindOf(drawn);
        if (before[static_cast<size_t>(dk)] > 0) {
            before[static_cast<size_t>(dk)]--;         // 「杠前听牌」按 **13 张**算
        }
    }
    const std::vector<int> waitsBefore
        = waits(before, static_cast<int>(melds[static_cast<size_t>(seat)].size()));
    if (!kanAllowedAfterRiichi(waitsBefore, c, static_cast<int>(melds[static_cast<size_t>(seat)].size()),
                               kind)) {
        return false;
    }
    // M.League 追加：立直后的暗杠还要求**面子构成不变**（允许役种增减）
    return !rules.ankanKeepsShape || ankanKeepsShape(seat, kind);
}

int Round::defaultDiscardId(int seat, int drawn) const {
    const auto forbidden = [this](int kind) { return containsInt(forbiddenDiscard, kind); };
    if (drawn >= 0 && !forbidden(kindOf(drawn))) {
        return drawn;
    }
    if (riichi[static_cast<size_t>(seat)] && drawn >= 0) {
        return drawn;
    }
    for (int id : hand[static_cast<size_t>(seat)]) {
        if (!forbidden(kindOf(id))) {
            return id;
        }
    }
    return drawn >= 0 ? drawn : hand[static_cast<size_t>(seat)][0];
}

int Round::resolveTile(const Cmd &act, int seat) const {
    if (!act.valid || !act.action.hasTile) {
        return -1;
    }
    return findByCode(hand[static_cast<size_t>(seat)], act.action.tile);
}

int Round::resolveDiscardId(const Cmd &act, int seat, bool wantTsumogiri, int drawn) const {
    if (!act.valid || !act.action.hasTile) {
        return -1;
    }
    return pickDiscardId(hand[static_cast<size_t>(seat)], drawn, act.action.tile, wantTsumogiri);
}

bool Round::canKyuushu(int seat) const {
    return rules.kyuushuAbort && !anyCall && playerDraws[static_cast<size_t>(seat)] == 1
           && [&] {
                  const Counts c = concealCounts(seat);
                  int n = 0;
                  for (int k = 0; k < kKindCount; k++) {
                      if (c[static_cast<size_t>(k)] > 0 && isYaochuKind(k)) {
                          n++;
                      }
                  }
                  return n >= 9;
              }();
}

bool Round::canDaiminkan(int seat, int kind) const {
    return kind >= 0 && !riichi[static_cast<size_t>(seat)] && canKan()
           && concealCounts(seat)[static_cast<size_t>(kind)] >= 3;
}

bool Round::fourKanAbortNow() const {
    return rules.fourKanAbort && kanCount == 4 && !allKansByOnePlayer();
}

bool Round::allKansByOnePlayer() const {
    for (int s = 0; s < 4; s++) {
        if (kanByPlayer[static_cast<size_t>(s)] == 4) {
            return true;
        }
    }
    return false;
}

// ================================================================= 和了判定

int Round::redCount(int seat) const {
    int n = 0;
    for (int id : hand[static_cast<size_t>(seat)]) {
        if (isRedId(id)) {
            n++;
        }
    }
    return n;
}

std::vector<int> Round::akaTiles(int seat, int winTileId, int akaInConcealed) const {
    std::vector<int> ids;
    for (const Meld &m : melds[static_cast<size_t>(seat)]) {
        for (int i = 0; i < m.tileCount; i++) {
            ids.push_back(m.tiles[static_cast<size_t>(i)]);
        }
    }
    if (winTileId >= 0) {
        ids.push_back(winTileId);
    }
    for (int i = 0; i < akaInConcealed; i++) {
        const int kind = (i % 3 == 0) ? kAkaM : ((i % 3 == 1) ? kAkaP : kAkaS);
        ids.push_back(idOf(kind, 0));
    }
    return ids;
}

bool Round::scoreWith(int seat, const Counts &merged, int winTileId, bool tsumo, bool rinshan,
                      bool haitei, bool chankan, bool houtei, bool assumeRiichi, bool includeUra,
                      int akaInConcealed, HandScore &out) const {
    const int winKind = kindOf(winTileId);
    const size_t s = static_cast<size_t>(seat);
    WinContext ctx;
    ctx.rules = rules;
    ctx.seat = seat;
    ctx.dealerSeat = dealer;
    ctx.roundWind = 27 + roundWind;
    ctx.tsumo = tsumo;
    ctx.riichi = assumeRiichi && !doubleRiichi[s];
    ctx.doubleRiichi = assumeRiichi && doubleRiichi[s];
    ctx.ippatsu = assumeRiichi && ippatsu[s] && riichi[s];
    ctx.chankan = chankan;
    ctx.rinshan = rinshan;
    ctx.haitei = haitei && tsumo;
    ctx.houtei = houtei && !tsumo;
    ctx.tenhou = tsumo && seat == dealer && playerDraws[s] == 1 && !anyCall;
    ctx.chiihou = tsumo && seat != dealer && playerDraws[s] == 1 && !anyCall;
    ctx.renhou = !tsumo && seat != dealer && playerDraws[s] == 0 && !anyCall;
    ctx.tsubame = !tsumo && lastDiscardSeat >= 0 && lastDiscardSeat != seat
                  && lastDiscardTile == winTileId && riichi[static_cast<size_t>(lastDiscardSeat)]
                  && discardsSinceRiichi[static_cast<size_t>(lastDiscardSeat)] == 1;
    ctx.kanburi = !tsumo && chankanKoyaku;
    ctx.winKind = winKind;
    ctx.doraIndicators = wall.doraIndicators();
    ctx.uraIndicators = includeUra ? wall.uraIndicators() : std::vector<int>{};
    ctx.allTileIds = akaTiles(seat, winTileId, akaInConcealed);
    ctx.menzen = menzen[s];
    out = evaluate(ctx, merged, melds[s], winKind);
    return out.valid;
}

bool Round::checkWin(int seat, int winTileId, bool tsumo, bool rinshan, bool haitei, bool chankan,
                     bool houtei, HandScore &out) const {
    Counts c{};
    if (!winCountsOf(seat, winTileId, tsumo, c)) {
        return false;
    }
    const int aka = redCount(seat) + ((!tsumo && isRedId(winTileId)) ? 1 : 0);
    return scoreWith(seat, c, winTileId, tsumo, rinshan, haitei, chankan, houtei,
                     riichi[static_cast<size_t>(seat)], true, aka, out);
}

bool Round::winBlockReason(int seat, int winTileId, bool tsumo, std::string &out) const {
    Counts c{};
    if (!winCountsOf(seat, winTileId, tsumo, c)) {
        return false;                      // 根本不是和了形
    }
    if (!(shantenMin(c, static_cast<int>(melds[static_cast<size_t>(seat)].size())) < 0)) {
        return false;
    }
    out = (!tsumo && isFuriten(seat)) ? "furiten" : "no_yaku";
    return true;
}

bool Round::isKokushiScore(const HandScore &sc) const {
    return sc.hasForm && sc.form.type == kTypeKokushi;
}

// ================================================================= 杠

RoundResult Round::turnKan(int seat, const Cmd &act, int drawn, bool &ended) {
    ended = false;
    if (!act.valid) {
        return {};
    }
    const std::string kindStr = act.action.kanKind.empty() ? "ankan" : act.action.kanKind;
    const int kind = act.action.hasTile ? parseKind(act.action.tile) : -1;
    if (kind < 0) {
        return {};
    }
    std::vector<int> picked;
    for (int id : hand[static_cast<size_t>(seat)]) {
        if (kindOf(id) == kind) {
            picked.push_back(id);
        }
    }
    if (kindStr == "ankan") {
        if (static_cast<int>(picked.size()) < 4) {
            return {};
        }
        // ⚠ 选项列表不是安全边界：立直后的暗杠必须在这里**再校验一次**
        if (!kanAllowedByRiichi(seat, kind, drawn)) {
            return {};
        }
        // **国士抢暗杠**（《雀魂》：判在**杠成立之前** —— 被抢时这次杠整个不成立）
        if (rules.kokushiAnkan) {
            const std::vector<int> rob = chankanRon(seat, picked[0], true);
            if (!rob.empty()) {
                ended = true;
                return agariRon(rob, seat, picked[0], true, false);
            }
        }
        std::array<int, 4> tiles{picked[0], picked[1], picked[2], picked[3]};
        for (int i = 0; i < 4; i++) {
            removeOne(hand[static_cast<size_t>(seat)], picked[static_cast<size_t>(i)]);
        }
        Meld m(Meld::Kind::ANKAN, tiles.data(), 4, seat, tiles[0]);
        melds[static_cast<size_t>(seat)].push_back(m);
        anyCall = true;
        kanCount++;
        kanByPlayer[static_cast<size_t>(seat)]++;
        wall.onKan();
        kanJustHappened = true;
        clearIppatsu();
        revealKanDora();
        return {};
    }
    // 加杠
    int targetIdx = -1;
    for (size_t i = 0; i < melds[static_cast<size_t>(seat)].size(); i++) {
        const Meld &m = melds[static_cast<size_t>(seat)][i];
        if (m.kind == Meld::Kind::PON && kindOf(m.calledId) == kind) {
            targetIdx = static_cast<int>(i);
            break;
        }
    }
    if (targetIdx < 0 || picked.empty()) {
        return {};
    }
    const int addId = picked[0];
    removeOne(hand[static_cast<size_t>(seat)], addId);
    const Meld target = melds[static_cast<size_t>(seat)][static_cast<size_t>(targetIdx)];
    std::array<int, 4> tiles{target.tiles[0], target.tiles[1], target.tiles[2], addId};
    Meld m(Meld::Kind::KAKAN, tiles.data(), 4, target.from, addId);
    melds[static_cast<size_t>(seat)][static_cast<size_t>(targetIdx)] = m;
    anyCall = true;
    kanCount++;
    kanByPlayer[static_cast<size_t>(seat)]++;
    wall.onKan();
    kanJustHappened = true;
    // 抢杠：**先判抢杠，杠成立之后才翻杠宝牌**（顺序反了会算错分）
    const std::vector<int> ron = chankanRon(seat, addId, false);
    if (!ron.empty()) {
        ended = true;
        return agariRon(ron, seat, addId, true, false);      // 抢杠：不是燕返
    }
    clearIppatsu();                  // 杠真的成立了，这才打断一发
    revealKanDora();
    return {};
}

std::vector<int> Round::chankanRon(int kanSeat, int tileId, bool kokushiOnly) const {
    std::vector<int> ron;
    for (int d = 1; d < 4; d++) {
        const int s = (kanSeat + d) % 4;
        if (isFuriten(s)) {
            continue;
        }
        HandScore sc;
        if (!checkWin(s, tileId, false, false, false, true, false, sc)) {
            continue;
        }
        if (kokushiOnly && !isKokushiScore(sc)) {
            continue;
        }
        ron.push_back(s);
    }
    // 多家抢杠同样受**头跳**约束
    if (rules.headBump && ron.size() > 1) {
        ron.resize(1);
    }
    return ron;
}

// ================================================================= 和了结算

std::vector<trainer::Pao> Round::paoPaysFor(int seat, const HandScore &sc) const {
    std::vector<trainer::Pao> out;
    if (!rules.pao || pao[static_cast<size_t>(seat)].empty()) {
        return out;
    }
    // 《天凤》：包牌涉及**复合后的全部役满得点**（只在**一位**责任者时才有意义）
    if (rules.paoCoversAll && paoSeatsOf(seat).size() == 1) {
        out.push_back(trainer::Pao{pao[static_cast<size_t>(seat)][0].payer,
                                   sc.base > 0 ? sc.base : 0});
        return out;
    }
    for (const Pao &p : pao[static_cast<size_t>(seat)]) {
        const int base = yakumanBaseOf(sc, p.yaku);
        if (base > 0) {
            out.push_back(trainer::Pao{p.payer, base});
        }
    }
    return out;
}

std::vector<int> Round::paoSeatsOf(int seat) const {
    std::vector<int> out;
    for (const Pao &p : pao[static_cast<size_t>(seat)]) {
        if (!containsInt(out, p.payer)) {
            out.push_back(p.payer);
        }
    }
    return out;
}

int Round::yakumanBaseOf(const HandScore &sc, const std::string &yaku) {
    int base = 0;
    for (const Yaku &y : sc.yaku) {
        if (y.yakuman > 0 && yaku == y.name) {
            base += 8000 * y.yakuman;
        }
    }
    return base;
}

void Round::applyDelta(RoundResult &r, const std::array<int, 4> &d) {
    for (int i = 0; i < 4; i++) {
        r.delta[static_cast<size_t>(i)] += d[static_cast<size_t>(i)];
        scores[static_cast<size_t>(i)] += d[static_cast<size_t>(i)];
    }
}

RoundResult Round::agariTsumo(int seat, int tileId, const HandScore &sc) {
    (void) tileId;
    RoundResult r;
    r.agari = true;
    r.winner = seat;
    r.loser = -1;
    r.tsumo = true;
    const std::vector<trainer::Pao> paos = paoPaysFor(seat, sc);
    const PaymentResult pay = paymentsCompute(sc, seat, -1, dealer, honba, sticks, true, paos);
    applyDelta(r, pay.delta);
    r.sticksLeft = 0;
    r.dealerRenchan = (seat == dealer);                 // RoundScoring.winBy(dealer, seat)
    r.tenpai[static_cast<size_t>(seat)] = true;
    return r;
}

RoundResult Round::agariRon(const std::vector<int> &winners, int from, int tileId, bool chankan,
                            bool riichiDiscard) {
    RoundResult r;
    r.agari = true;
    r.loser = from;
    r.tsumo = false;
    if (winners.empty()) {
        r.winner = -1;
        return r;
    }
    // ---------- 燕返：荣和的正是**首次放置的**立直宣言牌 → 立直不成立，1000 点退回
    if (riichiDiscard && riichi[static_cast<size_t>(from)]) {
        riichi[static_cast<size_t>(from)] = false;
        doubleRiichi[static_cast<size_t>(from)] = false;
        ippatsu[static_cast<size_t>(from)] = false;
        sticks = std::max(0, sticks - 1);
        std::array<int, 4> refund{};
        refund[static_cast<size_t>(from)] = 1000;
        applyDelta(r, refund);
    }
    r.winner = winners[0];
    int sticksLeft = sticks;
    // ⚠ 先把**所有**赢家结算完再发报文；供託与本场加点都只归 `winners[0]`（离放铳者最近那家）
    for (size_t i = 0; i < winners.size(); i++) {
        const int w = winners[i];
        HandScore sc;
        if (!checkWin(w, tileId, false, false, false, chankan, wall.atLastLiveTile(), sc)) {
            continue;
        }
        const std::vector<trainer::Pao> paos = paoPaysFor(w, sc);
        const int useSticks = (i == 0) ? sticks : 0;
        const int useHonba = (i == 0) ? honba : 0;
        const PaymentResult pay
            = paymentsCompute(sc, w, from, dealer, useHonba, useSticks, false, paos);
        sticksLeft -= useSticks;
        applyDelta(r, pay.delta);
    }
    r.sticksLeft = std::max(0, sticksLeft);
    r.dealerRenchan = containsInt(winners, dealer);
    for (int w : winners) {
        r.tenpai[static_cast<size_t>(w)] = true;
    }
    return r;
}

// ================================================================= 流局

RoundResult Round::exhaustive() {
    RoundResult r;
    std::array<bool, 4> tenpai{};
    for (int s = 0; s < 4; s++) {
        tenpai[static_cast<size_t>(s)] = tenpaiOf(concealCounts(s),
                                                  static_cast<int>(melds[static_cast<size_t>(s)].size()));
        r.tenpai[static_cast<size_t>(s)] = tenpai[static_cast<size_t>(s)];
    }
    // 流局满贯
    std::vector<int> nagashi;
    if (rules.nagashiMangan) {
        for (int s = 0; s < 4; s++) {
            if (nagashiEligible(discards[static_cast<size_t>(s)],
                                hadDiscardCalled[static_cast<size_t>(s)])) {
                nagashi.push_back(s);
            }
        }
    }
    if (!nagashi.empty()) {
        r.nagashi = true;
        for (size_t i = 0; i < nagashi.size(); i++) {
            const int s = nagashi[i];
            std::array<int, 4> d = nagashiPayments(s, dealer);
            if (i == 0 && sticks > 0) {
                d[static_cast<size_t>(s)] += sticks * 1000;
            }
            applyDelta(r, d);
        }
        r.sticksLeft = 0;
        r.dealerRenchan = nagashiBy(dealer, tenpai);
        return r;
    }
    const std::array<int, 4> d = notenPenalty(tenpai, rules.notenPenalty);
    applyDelta(r, d);
    r.dealerRenchan = exhaustiveBy(dealer, tenpai);
    r.sticksLeft = sticks;                 // 立直棒留到下一局
    return r;
}

RoundResult Round::abort(const std::string &reason) {
    RoundResult r;
    r.abortive = true;
    r.abortReason = reason;
    r.sticksLeft = sticks;
    r.dealerRenchan = true;                // RoundScoring.abortive()
    return r;
}

// ================================================================= 鸣牌

Round::Claim Round::claimPhase(int from, int tileId, bool riichiDiscard) {
    (void) riichiDiscard;                  // Java 的形参在这个函数里也没用到（燕返在 agariRon 判）
    // 同一张舍张的选项**只算一次**：`claimOptions` 里要跑完整的和了判定 + 振听扫描
    std::map<int, std::vector<Option>> optsBySeat;
    std::vector<int> eligible;
    for (int d = 1; d < 4; d++) {
        const int s = (from + d) % 4;
        if (awaySeat(s)) {
            continue;
        }
        std::vector<Option> o = claimOptions(s, from, tileId);
        optsBySeat[s] = o;
        // 只有"有得选"的座位才算 eligible（只有 `pass` 一条的**不问**）
        if (o.size() > 1 || (o.size() == 1 && o[0].type != kActPass)) {
            eligible.push_back(s);
        }
    }
    if (eligible.empty()) {
        return Claim{};
    }
    // ⚠ Java 在这里还有 asked / ronCapable / askedBestRank / best 与**等待循环** ——
    //    那些只服务"等真人应答"与"提前收工"（`RoundClaims.shouldStop`）。训练端四个座位恒为
    //    机器人：应答在下面这个循环里当场拿到，`asked` 恒为空、等待循环一次都不进
    //    （收工判据由 `tools/trainer-settle-parity.mjs` 的行级对拍单独钉住）。
    std::map<int, std::set<std::string>> askedTypes;
    std::map<int, Action> answers;
    for (const int s : eligible) {
        const std::vector<Option> &opts = optsBySeat[s];
        const std::string calledTile = tileToStr(tileId);
        bool hasWinNote = false;
        std::string winNote;
        if (!optionHas(opts, kActRon)) {
            hasWinNote = winBlockReason(s, tileId, false, winNote);
        }
        std::set<std::string> myTypes;
        for (const Option &o : opts) {
            myTypes.insert(o.type);
        }
        askedTypes[s] = myTypes;
        const Observation obs = makeObservation(s, "claim", opts, -1, false, from, true, calledTile,
                                               hasWinNote, winNote);
        const Cmd a = ask(s, "claim", obs, opts, tileId);
        if (a.valid) {
            answers[s] = a.action;
        }
    }
    // 同巡振听 / 立直振听：**被给过荣和选项却没和**（含超时未答）= 见逃
    for (int d = 1; d < 4; d++) {
        const int s = (from + d) % 4;
        const auto tit = askedTypes.find(s);
        if (tit == askedTypes.end() || tit->second.find(kActRon) == tit->second.end()) {
            continue;                       // 本次没给过他荣和选项 → 谈不上见逃
        }
        const auto ait = answers.find(s);
        if (ait != answers.end() && ait->second.type == kActRon) {
            continue;                       // 和了
        }
        if (isFuriten(s)) {
            continue;                       // 本来就在振听，不重复记账
        }
        furiten.temp[static_cast<size_t>(s)] = true;
        if (riichi[static_cast<size_t>(s)]) {
            furiten.perm[static_cast<size_t>(s)] = true;
        }
    }
    // 荣和 > 杠 > 碰 > 吃
    std::vector<int> ronSeats;
    for (int d = 1; d < 4; d++) {
        const int s = (from + d) % 4;
        const auto ait = answers.find(s);
        if (ait == answers.end() || ait->second.type != kActRon || isFuriten(s)) {
            continue;
        }
        const bool houtei = wall.atLastLiveTile();
        HandScore sc;
        if (checkWin(s, tileId, false, false, false, false, houtei, sc)) {
            ronSeats.push_back(s);
        }
    }
    if (!ronSeats.empty()) {
        // ⚠ 顺序有意义：**头跳先归约**（头跳一旦成立，"三家同时荣和"这个前提就不存在了）
        if (rules.headBump && ronSeats.size() > 1) {
            ronSeats.resize(1);
        } else if (rules.sanchaAbort && ronSeats.size() >= 3) {
            Claim c;
            c.isNull = false;
            c.hasAbort = true;
            c.abortReason = "三家和了";
            return c;
        }
        Claim c;
        c.isNull = false;
        c.type = Claim::Type::RON;
        c.seat = ronSeats[0];
        c.multiRon = ronSeats;
        return c;
    }
    for (const Claim::Type t : {Claim::Type::KAN, Claim::Type::PON, Claim::Type::CHI}) {
        for (int d = 1; d < 4; d++) {
            const int s = (from + d) % 4;
            const auto ait = answers.find(s);
            if (ait == answers.end()) {
                continue;
            }
            const Action &a = ait->second;
            const std::string ty = a.type.empty() ? "pass" : a.type;
            if (t == Claim::Type::KAN && ty == kActKan && canDaiminkan(s, kindOf(tileId))) {
                Claim c;
                c.isNull = false;
                c.type = t;
                c.seat = s;
                if (a.hasTiles) {
                    c.wantTiles = a.tiles;
                    c.hasWantTiles = true;
                }
                return c;
            }
            if (t == Claim::Type::PON && ty == kActPon) {
                Claim c;
                c.isNull = false;
                c.type = t;
                c.seat = s;
                if (a.hasTiles) {
                    c.wantTiles = a.tiles;
                    c.hasWantTiles = true;
                }
                return c;
            }
            if (t == Claim::Type::CHI && ty == kActChi) {
                std::array<int, 2> ids{};
                if (pickChiTiles(s, tileId, a.tiles, ids)) {
                    Claim c;
                    c.isNull = false;
                    c.type = t;
                    c.seat = s;
                    c.tiles[0] = ids[0];
                    c.tiles[1] = ids[1];
                    c.tileCount = 2;
                    return c;
                }
            }
        }
    }
    return Claim{};
}

int Round::findHandTile(int seat, int kind, bool red, const std::vector<int> &used) const {
    for (int id : hand[static_cast<size_t>(seat)]) {
        if (kindOf(id) != kind || isRedId(id) != red) {
            continue;
        }
        if (!containsInt(used, id)) {
            return id;
        }
    }
    return -1;
}

bool Round::pickChiTiles(int seat, int tileId, const std::vector<std::string> &want,
                         std::array<int, 2> &out) const {
    if (want.size() != 2) {
        return false;                      // 多给 / 少给一律作废
    }
    const int called = kindOf(tileId);    const int k0 = parseKind(want[0]);
    const int k1 = parseKind(want[1]);
    if (k0 < 0 || k1 < 0) {
        return false;
    }
    if (k0 == k1) {
        return false;                      // 两张必须不同（挡"两张 1m 当顺子"）
    }
    if (called >= 27 || k0 >= 27 || k1 >= 27) {
        return false;                      // 字牌不能吃
    }
    if (suitOf(k0) != suitOf(called) || suitOf(k1) != suitOf(called)) {
        return false;                      // 必须同花色
    }
    const int lo = std::min(called, std::min(k0, k1));
    const int hi = std::max(called, std::max(k0, k1));
    if (hi - lo != 2) {
        return false;                      // 必须恰好连成三张
    }
    std::vector<int> used;
    for (int i = 0; i < 2; i++) {
        const int k = (i == 0) ? k0 : k1;
        const int id = findHandTile(seat, k, isRedStr(want[static_cast<size_t>(i)]), used);
        if (id < 0) {
            return false;                  // 挑不到就作废这次吃，绝不替玩家拿错一张
        }
        out[static_cast<size_t>(i)] = id;
        used.push_back(id);
    }
    return true;
}

bool Round::pickHandTiles(int seat, int tileId, const std::vector<std::string> &want, int n,
                          std::vector<int> &out) const {
    if (want.empty() || static_cast<int>(want.size()) != n) {
        return false;
    }
    const int kind = kindOf(tileId);
    std::vector<int> picked;
    for (const std::string &s : want) {
        if (parseKind(s) != kind) {
            return false;                  // 牌种必须与被鸣的那张一致
        }
        const int id = findHandTile(seat, kind, isRedStr(s), picked);
        if (id < 0) {
            return false;                  // 手里没有这一种（例如要赤五却只有普通五）
        }
        picked.push_back(id);
    }
    out = picked;
    return true;
}

std::vector<int> Round::pickAuto(int seat, int kind, int n) const {
    std::vector<int> out;
    for (int id : hand[static_cast<size_t>(seat)]) {
        if (kindOf(id) == kind && !isRedId(id) && static_cast<int>(out.size()) < n) {
            out.push_back(id);
        }
    }
    for (int id : hand[static_cast<size_t>(seat)]) {
        if (kindOf(id) == kind && static_cast<int>(out.size()) < n && !containsInt(out, id)) {
            out.push_back(id);
        }
    }
    return out;
}

void Round::applyMeld(const Claim &cl, int from, int tileId) {
    const int seat = cl.seat;
    const int kind = kindOf(tileId);
    // ⚠ 大明杠要**先校验再改状态**：手里不足 3 张就什么都不做
    std::vector<int> kanPicked;
    if (cl.type == Claim::Type::KAN) {
        const std::vector<std::string> want = cl.hasWantTiles ? cl.wantTiles : std::vector<std::string>{};
        if (!pickHandTiles(seat, tileId, want, 3, kanPicked)) {
            kanPicked = pickAuto(seat, kind, 3);         // 普通牌优先（别顺手吃赤五）
        }
        if (static_cast<int>(kanPicked.size()) < 3) {
            return;
        }
    }
    menzen[static_cast<size_t>(seat)] = false;
    anyCall = true;
    clearIppatsu();
    forbiddenDiscard.clear();
    int calledIndex = -1;
    if (seat < 4 && !discards[static_cast<size_t>(from)].empty()) {
        calledIndex = static_cast<int>(discards[static_cast<size_t>(from)].size()) - 1;
        hadDiscardCalled[static_cast<size_t>(from)] = true;
    }
    switch (cl.type) {
        case Claim::Type::CHI: {
            std::array<int, 3> tiles{tileId, cl.tiles[0], cl.tiles[1]};
            std::sort(tiles.begin(), tiles.end());
            for (int i = 0; i < cl.tileCount; i++) {
                removeOne(hand[static_cast<size_t>(seat)], cl.tiles[static_cast<size_t>(i)]);
            }
            Meld m(Meld::Kind::CHI, tiles.data(), 3, from, tileId);
            melds[static_cast<size_t>(seat)].push_back(m);
            removeCalledFromRiver(from, calledIndex);
            if (rules.kuikae) {
                // ⚠ 参与运算的必须是**牌种 kind**（`tiles[]` 里存的是 id）
                const std::vector<int> forb = kuikaeForbidden(kind, kindOf(cl.tiles[0]),
                                                              kindOf(cl.tiles[1]));
                for (int k : forb) {
                    forbiddenDiscard.push_back(k);
                }
            }
            break;
        }
        case Claim::Type::PON: {
            const std::vector<std::string> want = cl.hasWantTiles ? cl.wantTiles : std::vector<std::string>{};
            std::vector<int> picked;
            if (!pickHandTiles(seat, tileId, want, 2, picked)) {
                picked = pickAuto(seat, kind, 2);        // 普通牌优先（别顺手吃赤五）
            }
            if (static_cast<int>(picked.size()) < 2) {
                return;                                  // 状态改之前就挡住（防伪造报文改分）
            }
            for (int id : picked) {
                removeOne(hand[static_cast<size_t>(seat)], id);
            }
            std::array<int, 3> tiles{picked[0], picked[1], tileId};
            std::sort(tiles.begin(), tiles.end());
            Meld m(Meld::Kind::PON, tiles.data(), 3, from, tileId);
            melds[static_cast<size_t>(seat)].push_back(m);
            removeCalledFromRiver(from, calledIndex);
            updatePao(seat, from, m);
            if (rules.kuikae) {
                forbiddenDiscard.push_back(kind);
            }
            break;
        }
        case Claim::Type::KAN: {
            for (int id : kanPicked) {
                removeOne(hand[static_cast<size_t>(seat)], id);
            }
            std::array<int, 4> tiles{kanPicked[0], kanPicked[1], kanPicked[2], tileId};
            std::sort(tiles.begin(), tiles.end());
            Meld m(Meld::Kind::DAIMINKAN, tiles.data(), 4, from, tileId);
            melds[static_cast<size_t>(seat)].push_back(m);
            removeCalledFromRiver(from, calledIndex);
            kanCount++;
            kanByPlayer[static_cast<size_t>(seat)]++;
            wall.onKan();
            kanJustHappened = true;
            updatePao(seat, from, m);
            revealKanDora();
            break;
        }
        default:
            break;
    }
    sortHands();
}

void Round::updatePao(int seat, int from, const Meld &m) {
    if (!rules.pao || from < 0) {
        return;
    }
    int dragons = 0;
    int winds = 0;
    for (const Meld &x : melds[static_cast<size_t>(seat)]) {
        if (x.isRun()) {
            continue;
        }
        const int k = x.baseKind();
        if (isDragon(k)) {
            dragons++;
        }
        if (isWind(k)) {
            winds++;
        }
    }
    // 大三元 / 大四喜：造成第 3 个三元牌 / 第 4 个风牌副露的那家包牌（自然计入暗杠）
    if (isDragon(m.baseKind()) && dragons == 3) {
        addPao(seat, from, "大三元");
    }
    if (isWind(m.baseKind()) && winds == 4) {
        addPao(seat, from, "大四喜");
    }
    // 四杠子包牌：**只有 M.League** 有，且必须是"由他家的舍张大明杠完成第 4 个杠"
    if (rules.paoFourKan && m.kind == Meld::Kind::DAIMINKAN) {
        int kans = 0;
        for (const Meld &x : melds[static_cast<size_t>(seat)]) {
            if (x.kind == Meld::Kind::ANKAN || x.kind == Meld::Kind::DAIMINKAN
                    || x.kind == Meld::Kind::KAKAN) {
                kans++;
            }
        }
        if (kans >= 4) {
            addPao(seat, from, "四杠子");
        }
    }
}

void Round::addPao(int seat, int payer, const std::string &yaku) {
    for (const Pao &p : pao[static_cast<size_t>(seat)]) {
        if (p.yaku == yaku) {
            return;                        // 幂等：同一个役只登记一次
        }
    }
    pao[static_cast<size_t>(seat)].push_back(Pao{payer, yaku});
}

// ================================================================= 选项 / 观测 / 询问

std::vector<Option> Round::turnOptions(int seat, int drawn, bool rinshan) const {
    TurnAsk a;
    a.seat = seat;
    a.drawnId = drawn;
    a.hand = concealCounts(seat);
    a.handIds = hand[static_cast<size_t>(seat)];
    a.melds = melds[static_cast<size_t>(seat)];
    a.drawnKind = drawn >= 0 ? kindOf(drawn) : -1;
    a.rinshan = rinshan;
    a.atLastLive = wall.atLastLiveTile();
    a.menzen = menzen[static_cast<size_t>(seat)];
    a.riichi = riichi[static_cast<size_t>(seat)];
    a.doubleRiichi = doubleRiichi[static_cast<size_t>(seat)];
    a.ippatsu = ippatsu[static_cast<size_t>(seat)];
    a.scores = scores;
    a.roundWind = roundWind;
    a.kyoku = kyoku;
    a.honba = honba;
    a.dealer = dealer;
    a.tilesLeft = wall.tilesLeft();
    a.deadWallLeft = wall.rinshanLeft();
    a.kanCount = kanCount;
    a.anyCall = anyCall;
    a.playerDraws = playerDraws[static_cast<size_t>(seat)];
    a.forbiddenKinds = forbiddenDiscard;
    a.doraKinds = wall.doraIndicators();
    a.uraKinds = wall.uraIndicators();
    a.rules = rules;
    return turnOptionsOf(a);
}

std::vector<Option> Round::claimOptions(int seat, int from, int tileId) const {
    ClaimAsk a;
    a.seat = seat;
    a.from = from;
    a.calledId = tileId;
    a.calledKind = kindOf(tileId);
    a.houtei = wall.atLastLiveTile();
    a.hand = concealCounts(seat);
    a.handIds = hand[static_cast<size_t>(seat)];
    a.melds = melds[static_cast<size_t>(seat)];
    a.menzen = menzen[static_cast<size_t>(seat)];
    a.riichi = riichi[static_cast<size_t>(seat)];
    a.doubleRiichi = doubleRiichi[static_cast<size_t>(seat)];
    a.ippatsu = ippatsu[static_cast<size_t>(seat)];
    a.fromRiichi = riichi[static_cast<size_t>(from)];
    a.fromDiscardsSinceRiichi = discardsSinceRiichi[static_cast<size_t>(from)];
    a.playerDraws = playerDraws[static_cast<size_t>(seat)];
    a.anyCall = anyCall;
    a.dealer = dealer;
    a.roundWind = roundWind;
    a.discardKindsEver = furiten.ever[static_cast<size_t>(seat)];
    a.furitenTemp = furiten.temp[static_cast<size_t>(seat)];
    a.furitenPerm = furiten.perm[static_cast<size_t>(seat)];
    a.deadWallLeft = wall.rinshanLeft();
    a.kanCount = kanCount;
    a.doraKinds = wall.doraIndicators();
    a.uraKinds = wall.uraIndicators();
    a.rules = rules;
    return claimOptionsOf(a);
}

Observation Round::makeObservation(int seat, const std::string &kind, const std::vector<Option> &opts,
                                   int drawnId, bool rinshan, int from, bool hasCalledTile,
                                   const std::string &calledTile, bool hasWinNote,
                                   const std::string &winNote) const {
    Observation o;
    o.seat = seat;
    o.kind = kind;
    for (int id : hand[static_cast<size_t>(seat)]) {
        const int k = kindOf(id);
        o.hand[static_cast<size_t>(k)]++;
        if (isRedId(id)) {
            o.handRed[static_cast<size_t>(k)] = true;
        }
    }
    if (drawnId >= 0) {
        o.hasDrawn = true;
        o.drawn = tileToStr(drawnId);
    }
    o.playerDraws = playerDraws[static_cast<size_t>(seat)];
    o.menzen = menzen[static_cast<size_t>(seat)];
    o.selfRiichi = riichi[static_cast<size_t>(seat)];
    // ⚠ 自家回合**不能**走完整的 `isFuriten()`：14 张时它与两个标志位**完全等价**
    //   （`Agari.waits` 对 14 张恒返回空集），却会白跑 34 次向听 DFS
    o.furiten = (kind == "turn")
            ? (furiten.temp[static_cast<size_t>(seat)] || furiten.perm[static_cast<size_t>(seat)])
            : isFuriten(seat);
    o.melds.resize(4);
    o.discards.resize(4);
    for (int s = 0; s < 4; s++) {
        for (const Meld &m : melds[static_cast<size_t>(s)]) {
            MeldJson mj;
            mj.kind = meldKindWire(m.kind);
            mj.from = m.from;
            mj.calledTile = tileToStr(m.calledId);
            for (int i = 0; i < m.tileCount; i++) {
                const int id = m.tiles[static_cast<size_t>(i)];
                mj.tiles.push_back(tileToStr(id));
                mj.aka[static_cast<size_t>(i)] = isRedId(id);
            }
            o.melds[static_cast<size_t>(s)].push_back(mj);
        }
        for (int id : discards[static_cast<size_t>(s)]) {
            o.discards[static_cast<size_t>(s)].push_back(tileToStr(id));
        }
    }
    for (int k : wall.doraIndicators()) {
        o.doraIndicators.push_back(kindToStr(k, false));
    }
    // 可见牌统计走**唯一**实现（`Visible`）；⚠ 里宝指示牌不算可见
    o.visible = visibleCounts(discards, melds, wall.doraIndicators());
    o.riichi = riichi;
    o.ippatsu = ippatsu;
    o.scores = scores;
    o.kuitan = rules.kuitan;
    o.roundWind = roundWind;
    o.kyoku = kyoku;
    o.honba = honba;
    o.sticks = sticks;
    o.dealer = dealer;
    o.tilesLeft = wall.tilesLeft();
    o.deadWallLeft = wall.rinshanLeft();
    o.totalDiscards = totalDiscards;
    o.kanCount = kanCount;
    o.anyCall = anyCall;
    o.haitei = wall.atLastLiveTile() && kind == "turn";
    o.houtei = wall.atLastLiveTile() && kind == "claim";
    o.rinshan = rinshan;
    o.from = from;
    o.hasCalledTile = hasCalledTile;
    o.calledTile = calledTile;
    o.hasWinNote = hasWinNote;
    o.winNote = winNote;
    o.legal = enumerateOptions(opts);
    return o;
}

Cmd Round::ask(int seat, const std::string &kind, const Observation &obs,
               const std::vector<Option> &opts, int calledTileId) {
    // 训练端没有网络：四个座位恒为机器人（SelfPlay 对每个座位 addBot），
    // 所以 Java `ask()` 里的 "掉线托管" 与 "下发报文 + awaitAction" 两条分支都到不了。
    const std::string roundKey
        = std::to_string(roundWind) + "-" + std::to_string(kyoku) + "-" + std::to_string(honba);
    // `opts` / `calledTileId` 一起传下去：Java 的 `Decision` 带着 `round` / `options` / `extra`，
    // teacher（`Bot.decide`）按**原始选项**取舍（见 policies.hpp 的 Decision）。
    return table->decideBot(seat, obs, kind, roundKey, this, opts, calledTileId);
}

}  // namespace trainer
