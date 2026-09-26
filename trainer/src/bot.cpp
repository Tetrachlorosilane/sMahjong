// teacher（内置牌效机器人）的**逐句移植** —— 对照 `server/src/main/java/mahjong/bot/Bot.java`。
//
// 阅读顺序与 Java 一致：公开信息视图（`HandState`）→ 决策入口（`decide`）→ 自家回合
// （`decideTurn` / `chooseDiscard` / 押し引き）→ 顺位与终局 → 对手模型 → 鸣牌（`decideClaim` /
// `hasYakuPlan`）→ 开杠（`shouldKan`）。
//
// ⚠ 一处**刻意**的"看起来重复"：`scoreIfWinOf` 与 `round.cpp` 的 `Round::scoreWith` 是同一段
//   上下文拼装。Java 那边 `Bot` 调的是 `Round.scoreIfWin`（public），而训练端的 `scoreWith`
//   是 `Round` 的 private 成员 —— 为了不动 `Round` 的公开面（那是另一条对拍链的接口），
//   这里照抄一份。改任何一处都必须同步改另一处（`docs/TRAINER-CPP.md` §2 铁律 3：
//   Java 是权威；对拍驱动器 = `tools/trainer-selfplay-parity.mjs`）。
//
// 与 Java 的差异（**不改行为**，都在这里点明）：
//   ① Java `Bot.decide` 外层有 `catch (RuntimeException) → fallback(options)`；训练端编译带
//      `-fno-exceptions`，那一层没有对应物 —— 本文件所有下标都过了显式边界判断；
//   ② Java 里可为 `null` 的容器（`options` / `counts` / `melds` / `meldCounts` / `scores`）
//      在 C++ 用值语义或 `std::optional`；`HandState::of` 恒把 `meldCounts` 填满，
//      所以 Java 那几处 `meldCounts == null` 的分支**不可达**，已按"空表"的等价行为省略；
//   ③ Java 的 13 个 `debug*Count` 是 `static long`（并行下本身竞态），C++ 用原子（见 bot.hpp）。
#include "bot.hpp"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <limits>
#include <string>
#include <vector>

#include "handeval.hpp"
#include "java_rand.hpp"
#include "obffeatures.hpp"
#include "payments.hpp"
#include "round.hpp"
#include "roundscoring.hpp"
#include "shanten.hpp"
#include "table.hpp"
#include "visible.hpp"
#include "wall.hpp"

namespace trainer {
namespace {

/** 顺位偏置（点）：1 位..4 位各给押し引き的期望值**门槛**加多少（Java `PLACEMENT_BIAS`）。 */
constexpr double kPlacementBias[4] = {800, 200, -300, -1000};

/** `Integer.MAX_VALUE`（Java 的 `bestDanger` 初值）。 */
constexpr int kIntMax = std::numeric_limits<int>::max();

/** Java `Math.round(double)` → int：`floor(x + 0.5)`（**不是** C 的 `std::round` 半远离零）。 */
int javaRoundToInt(double x) { return static_cast<int>(std::floor(x + 0.5)); }

/** Java 的 `debugXxxCount++`：计数只做诊断，`relaxed` 足够（见 bot.hpp 顶部 ②）。 */
void bump(std::atomic<long long> &c) { c.fetch_add(1, std::memory_order_relaxed); }

/** 四家牌河（牌 id）→ 每家的**牌种计数** —— Java `Danger.riverCounts`。 */
std::array<Counts, 4> riverCountsOf(const std::array<std::vector<int>, 4> &discards) {
    std::array<Counts, 4> out{};
    for (int s = 0; s < 4; s++) {
        for (int id : discards[static_cast<size_t>(s)]) {
            out[static_cast<size_t>(s)][static_cast<size_t>(kindOf(id))]++;
        }
    }
    return out;
}

/** 该家暗牌里有几张赤五（Java `Round.redCount`）。 */
int redCountOf(const Round &r, int seat) {
    int n = 0;
    for (int id : r.hand[static_cast<size_t>(seat)]) {
        if (isRedId(id)) {
            n++;
        }
    }
    return n;
}

/** 给 `Evaluator` 数赤宝的牌 id 列表（Java `Round.akaTiles`）。 */
std::vector<int> akaTilesOf(const Round &r, int seat, int winTileId, int akaInConcealed) {
    std::vector<int> ids;
    for (const Meld &m : r.melds[static_cast<size_t>(seat)]) {
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

/**
 * Java `Round.scoreIfWin(seat, concealed, winKind, tsumo, assumeRiichi, akaInConcealed)`。
 *
 * <p>与实局判定的差别只有一处：**不算里宝**（`includeUra = false`）；rinshan / haitei /
 * chankan / houtei 四个上下文一律 false（正是 Java 那条重载传进去的值）。其余上下文
 * （天地人 / 燕返 / 杠振 / 赤宝）**照抄 `Round.scoreWith`**，一个字不改。
 *
 * @return false = 不能和（无役 / 番缚不够 / 牌型不成立 / 张数不对）
 */
bool scoreIfWinOf(const Round &r, int seat, const Counts &concealed, int winKind, bool tsumo,
                  bool assumeRiichi, int akaInConcealed, HandScore &out) {
    if (seat < 0 || seat > 3 || winKind < 0 || winKind >= kKindCount) {
        return false;
    }
    // 假想和了牌一律按**非赤**（copy=3）：赤五的 +1 番取决于具体那张牌
    const int winTileId = idOf(winKind, 3);
    Counts merged{};
    if (!winCounts(concealed, static_cast<int>(r.melds[static_cast<size_t>(seat)].size()), winKind,
                   tsumo, merged)) {
        return false;
    }
    const size_t s = static_cast<size_t>(seat);
    WinContext ctx;
    ctx.rules = r.rules;
    ctx.seat = seat;
    ctx.dealerSeat = r.dealer;
    ctx.roundWind = 27 + r.roundWind;
    ctx.tsumo = tsumo;
    ctx.riichi = assumeRiichi && !r.doubleRiichi[s];
    ctx.doubleRiichi = assumeRiichi && r.doubleRiichi[s];
    ctx.ippatsu = assumeRiichi && r.ippatsu[s] && r.riichi[s];
    ctx.chankan = false;
    ctx.rinshan = false;
    ctx.haitei = false;
    ctx.houtei = false;
    ctx.tenhou = tsumo && seat == r.dealer && r.playerDraws[s] == 1 && !r.anyCall;
    ctx.chiihou = tsumo && seat != r.dealer && r.playerDraws[s] == 1 && !r.anyCall;
    ctx.renhou = !tsumo && seat != r.dealer && r.playerDraws[s] == 0 && !r.anyCall;
    ctx.tsubame = !tsumo && r.lastDiscardSeat >= 0 && r.lastDiscardSeat != seat
                  && r.lastDiscardTile == winTileId
                  && r.riichi[static_cast<size_t>(r.lastDiscardSeat)]
                  && r.discardsSinceRiichi[static_cast<size_t>(r.lastDiscardSeat)] == 1;
    ctx.kanburi = !tsumo && r.chankanKoyaku;
    ctx.winKind = winKind;
    ctx.doraIndicators = r.wall.doraIndicators();
    ctx.uraIndicators = {};                     // 查询**不算**里宝（只有和牌那一刻才翻开）
    ctx.allTileIds = akaTilesOf(r, seat, winTileId, akaInConcealed);
    ctx.menzen = r.menzen[s];
    out = evaluate(ctx, merged, r.melds[s], winKind);
    return out.valid;
}

/** Java `Round.scoreIfWin(seat, winKind, tsumo, assumeRiichi)`（用**当前**暗牌的 13 张形态版）。 */
bool scoreIfWinNow(const Round &r, int seat, int winKind, bool tsumo, bool assumeRiichi,
                   HandScore &out) {
    return scoreIfWinOf(r, seat, r.concealCounts(seat), winKind, tsumo, assumeRiichi,
                        redCountOf(r, seat), out);
}

/** 和了这一手**自己**能进多少点（含本场棒与供託）—— 走生产的 `Payments`（Java `winGain`）。 */
int winGain(const Round &r, int seat, const HandScore &sc, bool tsumo) {
    const int loser = tsumo ? -1 : (seat + 1) % 4;
    const PaymentResult pay = paymentsCompute(sc, seat, loser, r.dealer, r.honba, r.sticks, tsumo,
                                              std::vector<Pao>{});
    return pay.winnerGain;
}

/** `scores` 里把 `seat` 那家加上 `gain`（Java `gainAt`）。 */
std::array<int, 4> gainAt(const std::array<int, 4> &scores, int seat, int gain) {
    std::array<int, 4> out = scores;
    out[static_cast<size_t>(seat)] += gain;
    return out;
}

/** Java `Bot.placementOf`（数组版；`null` / 长度不足在调用点已挡掉）。 */
int placementOfScores(const std::array<int, 4> &scores, int seat) {
    int place = 1;
    for (int s = 0; s < 4; s++) {
        if (s == seat) {
            continue;
        }
        if (scores[static_cast<size_t>(s)] > scores[static_cast<size_t>(seat)]
                || (scores[static_cast<size_t>(s)] == scores[static_cast<size_t>(seat)] && s < seat)) {
            place++;
        }
    }
    return place;
}

// ---------------------------------------------------------------- 选项查找 / 回包构造

/** Java `find(options, type)`：按类型取**第一条**。 */
const Option *findOption(const std::vector<Option> &options, const std::string &type) {
    for (const Option &o : options) {
        if (o.type == type) {
            return &o;
        }
    }
    return nullptr;
}

Cmd cmdOf(const std::string &type) {
    Cmd c;
    c.valid = true;
    c.action = actionOf(type);
    return c;
}

Cmd cmdDiscard(const std::string &tile) {
    Cmd c;
    c.valid = true;
    c.action = actionOf(kActDiscard);
    c.action.tile = tile;
    c.action.hasTile = true;
    return c;
}

Cmd cmdRiichi(const std::string &tile) {
    Cmd c;
    c.valid = true;
    c.action = actionOf(kActRiichi);
    c.action.tile = tile;
    c.action.hasTile = true;
    return c;
}

Cmd cmdKan(const std::string &kanKind, const std::string &tile) {
    Cmd c;
    c.valid = true;
    c.action = actionOf(kActKan);
    c.action.kanKind = kanKind;
    c.action.tile = tile;
    c.action.hasTile = true;
    return c;
}

Cmd cmdChi(const std::array<std::string, 2> &set) {
    Cmd c;
    c.valid = true;
    c.action = actionOf(kActChi);
    c.action.tiles = {set[0], set[1]};
    c.action.hasTiles = true;
    return c;
}

/** Java `Bot.firstKan`：取 `kans` 的**第一条**（暗杠在前、加杠在后）；没有则 `valid=false`。 */
Cmd firstKan(const Option &kanOption) {
    if (kanOption.kans.empty()) {
        return Cmd{};
    }
    const KanEntry &m = kanOption.kans.front();
    const std::string tile = Bot::debugKanTileOverride.empty() ? m.tile : Bot::debugKanTileOverride;
    return cmdKan(m.kind, tile);
}

/**
 * Java `Bot.fallback(options)`：认不出任何可用选项时的兜底 —— Java 只在
 * `catch (RuntimeException)` 里用它，而训练端编译带 `-fno-exceptions`（没有那条路），
 * 所以这里保留同形的实现但标 `[[maybe_unused]]`（不删：它是 Java 那条兜底的对照物）。
 */
[[maybe_unused]] Cmd fallback(const std::vector<Option> &options) {
    if (findOption(options, kActPass) != nullptr) {
        return cmdOf(kActPass);
    }
    const Option *discard = findOption(options, kActDiscard);
    if (discard != nullptr && !discard->tiles.empty()) {
        return cmdDiscard(discard->tiles.front());
    }
    return cmdOf(kActPass);
}

/** 打点粗估用：暗牌 + 副露里有没有这一种刻子（Java `Bot.hasTriplet`）。 */
bool hasTriplet(const Counts &counts, const std::vector<Meld> &melds, int kind) {
    if (counts[static_cast<size_t>(kind)] >= 3) {
        return true;
    }
    for (const Meld &m : melds) {
        if (!m.isRun() && m.baseKind() == kind) {
            return true;
        }
    }
    return false;
}

/** Java `Bot.hasHonor`：暗牌或副露里有没有字牌。 */
bool hasHonor(const Counts &counts, const std::vector<Meld> &melds) {
    for (int k = 27; k < kKindCount; k++) {
        if (counts[static_cast<size_t>(k)] > 0) {
            return true;
        }
    }
    for (const Meld &m : melds) {
        for (int i = 0; i < m.tileCount; i++) {
            if (kindOf(m.tiles[static_cast<size_t>(i)]) >= 27) {
                return true;
            }
        }
    }
    return false;
}

/** Java `Bot.noChi`：一副吃都没有。 */
bool noChi(const std::vector<Meld> &melds) {
    for (const Meld &m : melds) {
        if (m.kind == Meld::Kind::CHI) {
            return false;
        }
    }
    return true;
}

/** 暗牌 + 副露**全是中张（2..8）**（Java `Bot.allSimplesOf`）。 */
bool allSimplesOf(const Counts &counts, const std::vector<Meld> &melds) {
    for (int k = 0; k < kKindCount; k++) {
        if (counts[static_cast<size_t>(k)] > 0 && !isSimple(k)) {
            return false;
        }
    }
    for (const Meld &m : melds) {
        for (int i = 0; i < m.tileCount; i++) {
            if (!isSimple(kindOf(m.tiles[static_cast<size_t>(i)]))) {
                return false;
            }
        }
    }
    return true;
}

/** 暗牌 + 副露只占**一种花色**（可含字牌）时返回该花色，否则 -1（Java `Bot.singleSuitOf`）。 */
int singleSuitOf(const Counts &counts, const std::vector<Meld> &melds) {
    int suit = -1;
    for (int k = 0; k < 27; k++) {
        if (counts[static_cast<size_t>(k)] > 0) {
            const int s = suitOf(k);
            if (suit >= 0 && s != suit) {
                return -1;
            }
            suit = s;
        }
    }
    for (const Meld &m : melds) {
        for (int i = 0; i < m.tileCount; i++) {
            const int k = kindOf(m.tiles[static_cast<size_t>(i)]);
            if (k >= 27) {
                continue;
            }
            const int s = suitOf(k);
            if (suit >= 0 && s != suit) {
                return -1;
            }
            suit = s;
        }
    }
    return suit;
}

/** Java `Bot.yaochuKinds`：自家暗牌里有几种幺九（九种九牌判据）。 */
int yaochuKinds(const Round &r, int seat) {
    const Counts c = r.concealCounts(seat);
    int n = 0;
    for (int k = 0; k < kKindCount; k++) {
        if (c[static_cast<size_t>(k)] > 0 && isYaochu(k)) {
            n++;
        }
    }
    return n;
}

/** 越"孤立"的牌越优先打出（Java `Bot.isolateScore`）。 */
int isolateScore(const std::string &t) {
    const int k = parseKind(t);
    if (k < 0) {
        return -1;
    }
    if (isHonor(k)) {
        return 0;
    }
    const int n = numOf(k);
    return std::min(std::abs(n - 5), 4);
}

/** Java `Bot.betterTieBreak`（`b == null` 那一支由调用点的第一个析取项天然覆盖）。 */
bool betterTieBreak(const std::string &a, const std::string &b) { return isolateScore(b) < isolateScore(a); }

// ---- 鸣牌相关的私有判据（Java 里都是 private static，顺序按 Java 的调用关系排）----

/** 鸣完（暗牌 `afterCall` + 新面子 `meld`）能不能比以前更接近和了（Java `betterAfterCall`）。 */
bool betterAfterCall(const Bot::HandState &st, const Counts &afterCall, int cur) {
    return Bot::bestShantenAfterCall(afterCall, st.meldCount + 1, 13 - 3 * st.meldCount - 1) < cur;
}

/** 暗牌 + 副露全是中张（2..8），把"这次要鸣的面子"也算进去（Java `allSimples`）。 */
bool allSimples(const Bot::HandState &st, const Counts &afterCall, const Meld &meld) {
    std::vector<Meld> ms = st.melds;
    ms.push_back(meld);
    return allSimplesOf(afterCall, ms);
}

/** 暗牌 + 副露只占一种花色（外加字牌）（Java `singleSuit`）。 */
bool singleSuit(const Bot::HandState &st, const Counts &afterCall, const Meld &meld) {
    std::vector<Meld> ms = st.melds;
    ms.push_back(meld);
    return singleSuitOf(afterCall, ms) >= 0;
}

/** 两条"无论如何都别开"的闸门（弃和中 / 第 4 个杠会四杠散了）—— Java `kanGuardRefuse`。 */
bool kanGuardRefuse(const Bot::HandState &st) {
    if (st.opponentRiichi() && shantenMin(st.counts, st.meldCount) >= 2) {
        bump(Bot::debugKanRefusePressure);
        return true;
    }
    if (st.fourKanAbort && st.kanCount == 3 && st.myKans() < st.kanCount) {
        bump(Bot::debugKanRefuseFourKan);
        return true;
    }
    return false;
}

/** Java `Bot.pickKan`：从下发的杠选项里挑一个**该开**的；都不该开返回 `valid=false`。 */
Cmd pickKan(const Bot::HandState &st, const Option &kanOption) {
    if (Bot::debugAlwaysKan) {
        return firstKan(kanOption);              // 自检专用：强制开杠（岭上那条路径）
    }
    for (const KanEntry &m : kanOption.kans) {
        const int kind = parseKind(m.tile);
        if (kind < 0 || !Bot::shouldKan(st, m.kind, kind)) {
            continue;
        }
        return cmdKan(m.kind, m.tile);
    }
    return Cmd{};
}

/** Java `Bot.threatScore`：对**威胁家**的最大危险度分。 */
int threatScore(const Bot::HandState &st, int kind, const std::array<bool, 4> &threats) {
    int worst = 0;
    for (int s = 0; s < 4; s++) {
        if (!threats[static_cast<size_t>(s)]) {
            continue;
        }
        worst = std::max(worst,
                         dangerScore(kind, st.visible, &st.rivers[static_cast<size_t>(s)], true,
                                     st.turn));
    }
    return worst;
}

/** Java `Bot.safestAgainstThreats`：在"不增加向听"的一组里挑对威胁家最安全的。 */
std::string safestAgainstThreats(const Bot::HandState &st, const std::vector<std::string> &codes,
                                 const std::vector<int> &kinds, const std::vector<int> &shantens,
                                 int minSh, int used) {
    const std::array<bool, 4> threats = Bot::threatSeats(st);
    bool any = false;
    for (bool b : threats) {
        any |= b;
    }
    std::string best;
    int bestDanger = kIntMax;
    for (int i = 0; i < used; i++) {
        if (shantens[static_cast<size_t>(i)] != minSh) {
            continue;
        }
        const int d = any
                ? threatScore(st, kinds[static_cast<size_t>(i)], threats)
                : dangerWorstAgainstRiichi(kinds[static_cast<size_t>(i)], st.visible, st.rivers,
                                           st.riichi, st.turn, st.seat);
        if (best.empty() || d < bestDanger
                || (d == bestDanger && betterTieBreak(codes[static_cast<size_t>(i)], best))) {
            best = codes[static_cast<size_t>(i)];
            bestDanger = d;
        }
    }
    return best;
}

// ---- 决策入口的两个私有分支（Java `decideTurn` / `decideClaim`）----
Cmd decideClaim(const Round &r, int seat, const std::vector<Option> &options, int calledTileId);

/** Java `Bot.decideTurn`。 */
Cmd decideTurn(const Round &r, int seat, const std::vector<Option> &options) {
    if (findOption(options, kActTsumo) != nullptr) {
        return cmdOf(kActTsumo);
    }
    // 公开信息视图：杠与打牌都要用，先算一次（它只读公开字段，见 HandState::of）
    const Bot::HandState st = Bot::HandState::of(r, seat);
    // 开杠（暗杠/加杠）：判据见 shouldKan —— 不丢听牌、不后退、危险/弃和/四杠散了都不开
    const Option *kanOpt = findOption(options, kActKan);
    if (kanOpt != nullptr) {
        const Cmd pick = pickKan(st, *kanOpt);
        if (pick.valid) {
            return pick;
        }
    }
    if (findOption(options, kActKyuushu) != nullptr) {
        const int n = yaochuKinds(r, seat);
        // 随机源由 Table 从 seedBase 派生（不是 Math.random）：自对弈/评测要同种子可复现。
        // Java `r.table.botRng().nextDouble() < 0.5` —— 惰性创建、**整场共享**（见 table.hpp）。
        if (n >= 10 || (n == 9 && r.table->botRng().nextDouble() < 0.5)) {
            return cmdOf(kActKyuushu);
        }
    }
    const Option *discardOpt = findOption(options, kActDiscard);
    const std::vector<std::string> candidates
            = discardOpt == nullptr ? std::vector<std::string>{} : discardOpt->tiles;
    if (candidates.empty()) {
        return cmdOf(kActPass);
    }
    const std::string bestTile = Bot::chooseDiscard(st, candidates);
    if (bestTile.empty()) {                  // Java：`bestTile == null`
        return cmdDiscard(candidates.front());
    }
    // 立直：先问"不立直能不能和、值多少"（ダマテン判断），见 shouldDeclareRiichi
    const Option *riichi = findOption(options, kActRiichi);
    if (riichi != nullptr) {
        const bool contains = std::find(riichi->tiles.begin(), riichi->tiles.end(), bestTile)
                              != riichi->tiles.end();
        if (contains && Bot::shouldDeclareRiichi(r, seat, st, bestTile)) {
            return cmdRiichi(bestTile);
        }
    }
    return cmdDiscard(bestTile);
}

/** Java `Bot.decideClaim`。 */
Cmd decideClaim(const Round &r, int seat, const std::vector<Option> &options, int calledTileId) {
    // 公开信息视图：荣和（见逃）与鸣牌都要用，先算一次
    const Bot::HandState st = Bot::HandState::of(r, seat);
    // Java：`Tiles.parseKind(Json.str(extra, "tile", ""))`，而 `extra.tile = Tiles.toStr(tileId)`
    // —— `toStr` 再 `parseKind` 回来的 kind 与 `kindOf(tileId)` 恒等（赤五 "0p" 亦然）。
    const int kind = calledTileId < 0 ? -1 : kindOf(calledTileId);
    // 和牌永远优先 —— 唯一的例外是**终局见逃**（这一手荣和抬不动顺位、自摸才抬得动）
    if (findOption(options, kActRon) != nullptr
            && (kind < 0 || !Bot::shouldDeclineRon(r, seat, st, kind))) {
        return cmdOf(kActRon);
    }
    // 自检专用：能大明杠就杠（默认关；见 bot.hpp）
    if (Bot::debugAlwaysKan && findOption(options, kActKan) != nullptr) {
        return cmdOf(kActKan);
    }
    const int cur = shantenMin(st.counts, st.meldCount);
    if (kind < 0) {
        return cmdOf(kActPass);
    }
    // 有人立直时，除非鸣完立刻是"役牌"这种硬役，否则不跟着上
    const bool underPressure = st.opponentRiichiCount() >= 1;
    // 门清 vs 已经鸣过牌：门清鸣牌的代价（放弃立直/门清）要算进价值比较
    const bool menzenBefore = st.meldCount == 0;

    // 大明杠：本作口径 **门清手不大明杠**；已经鸣过牌的手只要还有役、不违两条硬闸门、
    // 向听不倒退就杠（Java `decideClaim` 的同一段）
    const Option *kan = findOption(options, kActKan);
    if (kan != nullptr && st.counts[static_cast<size_t>(kind)] >= 3) {
        Counts c = st.counts;
        c[static_cast<size_t>(kind)] -= 3;
        const int tiles[4] = {idOf(kind, 1), idOf(kind, 2), idOf(kind, 3), idOf(kind, 0)};
        const Meld meld(Meld::Kind::DAIMINKAN, tiles, 4, -1, idOf(kind, 0));
        const bool open = st.meldCount > 0;
        const bool yaku = Bot::hasYakuPlan(st, c, meld);
        const bool noRegress = shantenMin(c, st.meldCount + 1) <= cur;
        if (open && !yaku) {
            bump(Bot::debugNoYakuRefuseCount);
        }
        if (open && yaku && noRegress && !kanGuardRefuse(st)) {
            bump(Bot::debugKanCount);
            return firstKan(*kan);
        }
    }

    // 碰
    const Option *pon = findOption(options, kActPon);
    if (pon != nullptr && st.counts[static_cast<size_t>(kind)] >= 2) {
        Counts c = st.counts;
        c[static_cast<size_t>(kind)] -= 2;
        const int tiles[3] = {idOf(kind, 1), idOf(kind, 2), idOf(kind, 3)};
        const Meld meld(Meld::Kind::PON, tiles, 3, -1, idOf(kind, 0));
        const bool better = betterAfterCall(st, c, cur);
        const bool yaku = Bot::hasYakuPlan(st, c, meld);
        if (better && !yaku) {
            bump(Bot::debugNoYakuRefuseCount);
        }
        if (better && yaku && !(underPressure && !Bot::isYakuhai(st, kind))) {
            if (menzenBefore && !Bot::callWorthForMenzen(st, c, meld)) {
                bump(Bot::debugCallValueRefuseCount);   // 门清 + 鸣了不值 → 不鸣
            } else {
                // ⚠ 裸 `pon`（不带取法）—— Java 也这么回；取法由引擎的 `pickAuto` 决定
                return cmdOf(kActPon);
            }
        }
    }

    // 吃
    const Option *chi = findOption(options, kActChi);
    if (chi != nullptr) {
        const std::array<std::string, 2> *bestSet = nullptr;
        int bestShanten = 99;
        for (const std::array<std::string, 2> &pair : chi->sets) {
            Counts c = st.counts;
            bool ok = true;
            int tiles[3] = {0, 0, 0};
            int nt = 0;
            for (const std::string &t : pair) {
                const int k = parseKind(t);
                if (k < 0 || c[static_cast<size_t>(k)] <= 0) {
                    ok = false;
                    break;
                }
                c[static_cast<size_t>(k)]--;
                tiles[nt++] = idOf(k, 1);
            }
            if (!ok) {
                continue;
            }
            tiles[2] = idOf(kind, 0);
            std::sort(tiles, tiles + 3);                  // Java `Arrays.sort(tiles)`
            const Meld meld(Meld::Kind::CHI, tiles, 3, -1, idOf(kind, 0));
            const int after = Bot::bestShantenAfterCall(c, st.meldCount + 1,
                                                        13 - 3 * st.meldCount - 1);
            if (after >= cur || after >= bestShanten) {
                continue;                                 // 不改善向听 / 不如已找到的最优
            }
            if (!Bot::hasYakuPlan(st, c, meld)) {
                bump(Bot::debugNoYakuRefuseCount);        // 鸣完没役 → 这手吃下去也永远和不了
                continue;
            }
            if (menzenBefore && !Bot::callWorthForMenzen(st, c, meld)) {
                bump(Bot::debugCallValueRefuseCount);     // 门清 + 吃下去不值 → 不吃
                continue;
            }
            if (underPressure) {
                continue;                                 // 有人立直时不跟着吃
            }
            bestShanten = after;
            bestSet = &pair;
        }
        if (bestSet != nullptr) {
            return cmdChi(*bestSet);
        }
    }
    return cmdOf(kActPass);
}

}  // namespace

// ================================================================= 计数器

namespace {

/**
 * 对拍用出口：`TRAINER_BOT_COUNTERS=1` 时在进程退出前把 13 个计数器打到 **stderr**（一行、机器可读）。
 *
 * <p>为什么要有它：`docs/TRAINER-CPP.md` 的判据只要求"同种子轨迹逐字节相同"，那当然也覆盖
 * 计数器的**成因**；但"这些取舍真的被走到过"本身是一条独立判据（Java 侧 `SelfTest.teacherTests`
 * 就是拿它们做非空转断言）。训练端 `selfplay.cpp` / `main.cpp` 不在本次移植的文件范围内，
 * 所以这里用一个静态出口把它暴露出来 —— 对拍方式是：
 * `java -cp jar jshell`（`SelfPlay.run` + 读 `Bot.debugXxxCount`）与
 * `TRAINER_BOT_COUNTERS=1 trainer.exe selfplay … --workers 1` 跑**同一批**场次，逐项比。
 */
struct BotCounterDumper {
    ~BotCounterDumper() {
        const char *on = std::getenv("TRAINER_BOT_COUNTERS");
        if (on == nullptr || on[0] == '\0' || on[0] == '0') {
            return;
        }
        const Bot::DebugCounts d = Bot::debugCounts();
        std::fprintf(stderr,
                     "[trainer] bot-counters fold=%lld dama=%lld no_yaku_refuse=%lld kan=%lld"
                     " kan_refuse_wait=%lld kan_refuse_pressure=%lld kan_refuse_four_kan=%lld"
                     " kan_refuse_danger=%lld call_value_refuse=%lld push=%lld placement_flip=%lld"
                     " ron_decline=%lld open_fold=%lld\n",
                     d.fold, d.dama, d.noYakuRefuse, d.kan, d.kanRefuseWait, d.kanRefusePressure,
                     d.kanRefuseFourKan, d.kanRefuseDanger, d.callValueRefuse, d.push,
                     d.placementFlip, d.ronDecline, d.openFold);
    }
};

BotCounterDumper g_botCounterDumper;

}  // namespace

Bot::DebugCounts Bot::debugCounts() {
    DebugCounts d;
    d.fold = debugFoldCount.load(std::memory_order_relaxed);
    d.dama = debugDamaCount.load(std::memory_order_relaxed);
    d.noYakuRefuse = debugNoYakuRefuseCount.load(std::memory_order_relaxed);
    d.kan = debugKanCount.load(std::memory_order_relaxed);
    d.kanRefuseWait = debugKanRefuseWait.load(std::memory_order_relaxed);
    d.kanRefusePressure = debugKanRefusePressure.load(std::memory_order_relaxed);
    d.kanRefuseFourKan = debugKanRefuseFourKan.load(std::memory_order_relaxed);
    d.kanRefuseDanger = debugKanRefuseDanger.load(std::memory_order_relaxed);
    d.callValueRefuse = debugCallValueRefuseCount.load(std::memory_order_relaxed);
    d.push = debugPushCount.load(std::memory_order_relaxed);
    d.placementFlip = debugPlacementFlipCount.load(std::memory_order_relaxed);
    d.ronDecline = debugRonDeclineCount.load(std::memory_order_relaxed);
    d.openFold = debugOpenFoldCount.load(std::memory_order_relaxed);
    return d;
}

void Bot::debugResetCounts() {
    debugFoldCount.store(0, std::memory_order_relaxed);
    debugDamaCount.store(0, std::memory_order_relaxed);
    debugNoYakuRefuseCount.store(0, std::memory_order_relaxed);
    debugKanCount.store(0, std::memory_order_relaxed);
    debugKanRefuseWait.store(0, std::memory_order_relaxed);
    debugKanRefusePressure.store(0, std::memory_order_relaxed);
    debugKanRefuseFourKan.store(0, std::memory_order_relaxed);
    debugKanRefuseDanger.store(0, std::memory_order_relaxed);
    debugCallValueRefuseCount.store(0, std::memory_order_relaxed);
    debugPushCount.store(0, std::memory_order_relaxed);
    debugPlacementFlipCount.store(0, std::memory_order_relaxed);
    debugRonDeclineCount.store(0, std::memory_order_relaxed);
    debugOpenFoldCount.store(0, std::memory_order_relaxed);
}

// ================================================================= 公开信息视图

Bot::HandState Bot::HandState::of(const Round &r, int seat) {
    HandState st;
    const size_t s = static_cast<size_t>(seat);
    for (int id : r.hand[s]) {
        st.counts[static_cast<size_t>(kindOf(id))]++;
        if (isRedId(id)) {
            st.akaInHand++;
        }
    }
    st.melds = r.melds[s];
    st.meldCount = static_cast<int>(r.melds[s].size());
    st.visible = visibleCounts(r.discards, r.melds, r.wall.doraIndicators());
    st.rivers = riverCountsOf(r.discards);
    st.riichi = r.riichi;
    st.selfRiichi = r.riichi[s];
    st.kuitan = r.rules.kuitan;
    st.seat = seat;
    st.dealer = r.dealer;
    st.roundWind = r.roundWind;
    st.turn = r.totalDiscards / 4;
    st.doraIndicators = r.wall.doraIndicators();
    st.kanCount = r.kanCount;
    st.fourKanAbort = r.rules.fourKanAbort;
    st.tilesLeft = r.tilesLeft();
    st.scores = r.scores;
    st.sticks = r.sticks;
    st.kyoku = r.kyoku;
    // 「最后一局」= 场风已到本赛制上限 **且** 是第 4 局（延长战的场风会超过上限，
    // 那时西 4 局同样是终局；判据同源：`RoundScoring.lastWind`）
    st.allLast = r.kyoku == 4 && r.roundWind >= lastWindOf(r.rules);
    for (int i = 0; i < 4; i++) {
        st.meldCounts[static_cast<size_t>(i)]
                = static_cast<int>(r.melds[static_cast<size_t>(i)].size());
    }
    return st;
}

int Bot::HandState::myKans() const {
    int n = 0;
    for (const Meld &m : melds) {
        if (m.kind == Meld::Kind::ANKAN || m.kind == Meld::Kind::KAKAN
                || m.kind == Meld::Kind::DAIMINKAN) {
            n++;
        }
    }
    return n;
}

bool Bot::HandState::opponentRiichi() const {
    for (int s = 0; s < 4; s++) {
        if (s != seat && riichi[static_cast<size_t>(s)]) {
            return true;
        }
    }
    return false;
}

int Bot::HandState::opponentRiichiCount() const {
    int n = 0;
    for (int s = 0; s < 4; s++) {
        if (s != seat && riichi[static_cast<size_t>(s)]) {
            n++;
        }
    }
    return n;
}

// ================================================================= 决策入口

Cmd Bot::decide(const Round &r, int seat, const std::string &kind,
                const std::vector<Option> &options, int calledTileId) {
    // Java：`try { ... } catch (RuntimeException) { return fallback(options); }`
    // 训练端 `-fno-exceptions`（trainer/build.ps1），而本文件所有下标都过了显式边界判断
    // → Bot 自己不会抛，所以那一层没有对应物（见 bot.cpp 顶部 ①）。
    if (kind == "turn") {
        return decideTurn(r, seat, options);
    }
    return decideClaim(r, seat, options, calledTileId);
}

// ================================================================= 打点粗估 / 押し引き

int Bot::estimatedHan(const HandState &st, const Counts &counts, const std::vector<Meld> &melds) {
    // Java 在这里对 `counts == null` / `melds == null` 有兜底（换成全 0 / 空表）；
    // 训练端是值语义，两个调用点都传真值 —— 行为相同。
    const Counts &c = counts;
    const std::vector<Meld> &ms = melds;
    const bool open = !ms.empty();
    int han = doraCount(c, ms, st.doraIndicators) + st.akaInHand;
    if (!open && !st.selfRiichi) {
        han += 1;                                   // 立直
    }
    for (int k = 27; k < kKindCount; k++) {
        if (isYakuhai(st, k) && hasTriplet(c, ms, k)) {
            han += 1;
        }
    }
    if (st.kuitan && allSimplesOf(c, ms)) {
        han += 1;
    }
    if (singleSuitOf(c, ms) >= 0) {
        han += hasHonor(c, ms) ? (open ? 2 : 3) : (open ? 5 : 6);
    }
    if (noChi(ms)) {
        // 对对和的**粗判**：没有吃、且暗牌里一张"单张"都没有（全是刻子/对子）
        int pairs = 0;
        bool single = false;
        for (int k = 0; k < kKindCount; k++) {
            if (c[static_cast<size_t>(k)] == 1) {
                single = true;
            } else if (c[static_cast<size_t>(k)] >= 2) {
                pairs++;
            }
        }
        if (!single && pairs >= 2) {
            han += 2;                               // 对对和
        }
    }
    return han;
}

int Bot::hanToPoints(int han) {
    if (han >= 13) {
        return 32000;
    }
    if (han >= 11) {
        return 24000;
    }
    if (han >= 8) {
        return 16000;
    }
    if (han >= 6) {
        return 12000;
    }
    if (han >= 5) {
        return 8000;
    }
    if (han >= 4) {
        return 7700;
    }
    if (han >= 3) {
        return 3900;
    }
    if (han >= 2) {
        return 2000;
    }
    return han >= 1 ? 1000 : 0;
}

double Bot::winProbability(int needTiles, int tilesLeft, int turnsLeft, int steps) {
    if (needTiles <= 0 || tilesLeft <= 0 || turnsLeft <= 0 || steps <= 0) {
        return 0;
    }
    const double perTurn = std::min(0.5, static_cast<double>(needTiles) / tilesLeft);
    const double p = (1 - std::pow(1 - perTurn, static_cast<double>(turnsLeft))) / steps;
    return std::max(0.0, std::min(0.9, p));
}

double Bot::dealProbability(int dangerScore) {
    return std::min(0.30, std::max(0, dangerScore) / 100.0 * 0.30);
}

double Bot::pushEv(double winProb, int winPoints, double dealProb) {
    return winProb * winPoints - dealProb * kAvgDealPoints;
}

bool Bot::shouldPush(double winProb, int winPoints, double dealProb) {
    return shouldPush(winProb, winPoints, dealProb, 0);
}

bool Bot::shouldPush(double winProb, int winPoints, double dealProb, double threshold) {
    return pushEv(winProb, winPoints, dealProb) > threshold;
}

// ================================================================= 顺位与终局（档 C）

int Bot::placementOf(const std::optional<std::array<int, 4>> &scores, int seat) {
    if (!scores.has_value() || seat < 0 || seat > 3) {
        return 0;
    }
    return placementOfScores(*scores, seat);
}

double Bot::pushThreshold(const HandState &st) {
    const int place = placementOf(st.scores, st.seat);
    if (place == 0) {
        return 0;
    }
    const double v = kPlacementBias[place - 1];
    return st.allLast ? v * kAllLastScale : v;
}

// ------------------------------------------------------------- 对手模型（只吃公开信息）

double Bot::openThreat(const HandState &st) {
    // Java：`st.meldCounts == null → 0`。`HandState::of` 恒填满，而默认构造的全 0 表
    // 走下面的循环同样得 0 —— 等价，所以不另设分支。
    double best = 0;
    for (int s = 0; s < 4; s++) {
        if (s == st.seat || st.riichi[static_cast<size_t>(s)]
                || st.meldCounts[static_cast<size_t>(s)] <= 0) {
            continue;
        }
        const double w = 0.25 + 0.20 * (st.meldCounts[static_cast<size_t>(s)] - 1) + 0.03 * st.turn;
        best = std::max(best, std::min(0.9, w));
    }
    return best;
}

std::array<bool, 4> Bot::threatSeats(const HandState &st) {
    std::array<bool, 4> out{};
    bool any = false;
    for (int s = 0; s < 4; s++) {
        if (s != st.seat && st.riichi[static_cast<size_t>(s)]) {
            out[static_cast<size_t>(s)] = true;
            any = true;
        }
    }
    if (any) {
        return out;                        // Java 另有 `|| st.meldCounts == null`（等价，见 openThreat）
    }
    for (int s = 0; s < 4; s++) {
        if (s != st.seat && st.meldCounts[static_cast<size_t>(s)] > 0) {
            out[static_cast<size_t>(s)] = true;
        }
    }
    return out;
}

int Bot::dealScore(const HandState &st, int kind) {
    return dealScore(st, kind,
                     dangerWorstReport(kind, st.visible, st.rivers, st.riichi, st.turn, st.seat));
}

int Bot::dealScore(const HandState &st, int kind, const DangerReport &base) {
    // Java 另有 `base == null || st.meldCounts == null` 两条：前者的实参来自 `Danger.worst`
    // （永不返回 null），后者由 `HandState::of` 恒填满 —— 两条都不可达，故按等价行为省略。
    if (base.level >= kDangerDangerous) {
        return base.score;
    }
    const double t = openThreat(st);
    if (t <= 0) {
        return base.score;
    }
    int asIfRiichi = 0;
    for (int s = 0; s < 4; s++) {
        if (s == st.seat || st.riichi[static_cast<size_t>(s)]
                || st.meldCounts[static_cast<size_t>(s)] <= 0) {
            continue;
        }
        asIfRiichi = std::max(asIfRiichi,
                              dangerScore(kind, st.visible, &st.rivers[static_cast<size_t>(s)], true,
                                          st.turn));
    }
    const int blended = javaRoundToInt(base.score + t * (asIfRiichi - base.score));
    return std::max(base.score, blended);   // 抬档只许往上，绝不让某张牌"变安全"
}

// ------------------------------------------------------------- 终局见逃

bool Bot::shouldDeclineRon(const Round &r, int seat, const HandState &st, int winKind) {
    if (!st.allLast || st.selfRiichi || winKind < 0 || winKind >= kKindCount) {
        return false;
    }
    if (!st.scores.has_value() || st.tilesLeft < kRonDeclineMinTiles) {
        return false;
    }
    HandScore ron{};
    if (!scoreIfWinNow(r, seat, winKind, false, false, ron)) {
        return false;                                // 不能和（不该走到这里）→ 照和
    }
    // 自摸：这一次询问手上是 13 张，把和了牌并进去才是 `tsumo = true` 要的 14 张形态
    Counts withWin = st.counts;
    withWin[static_cast<size_t>(winKind)]++;
    HandScore tsumo{};
    const bool tsumoOk = scoreIfWinOf(r, seat, withWin, winKind, true, false, st.akaInHand, tsumo);
    const int ronGain = winGain(r, seat, ron, false);
    const int tsumoGain = tsumoOk ? winGain(r, seat, tsumo, true) : 0;
    const int now = placementOfScores(*st.scores, seat);
    const int afterRon = placementOfScores(gainAt(*st.scores, seat, ronGain), seat);
    const int afterTsumo = placementOfScores(gainAt(*st.scores, seat, tsumoGain), seat);
    if (afterRon < now || afterTsumo >= afterRon) {
        return false;                                // 荣和能抬顺位 / 自摸也抬不动 → 照和
    }
    bump(debugRonDeclineCount);
    return true;
}

// ================================================================= 立直还是ダマテン

bool Bot::shouldDeclareRiichi(const Round &r, int seat, const HandState &st,
                              const std::string &tileCode) {
    const int kind = parseKind(tileCode);
    if (kind < 0) {
        return true;
    }
    // ⚠ 自家回合的 `st.counts` 是 **14 张**（含刚摸到的那张），要先把它减掉才是"打这张
    //   之后的 13 张"。传进来 13 张也容忍（减了会变成 12 张 → 听牌表恒为空 → 误判成"必须立直"）
    Counts after = st.counts;
    if (sumOf(after) >= 14 && kind >= 0 && after[static_cast<size_t>(kind)] > 0) {
        after[static_cast<size_t>(kind)]--;
    }
    int best = 0;
    for (int wk : waits(after, st.meldCount)) {
        // 对**计算出来的**那份 13 张暗牌求值（牌桌手上还是 14 张）
        HandScore s{};
        if (scoreIfWinOf(r, seat, after, wk, false, false, st.akaInHand, s)) {
            best = std::max(best, s.totalHan());
        }
    }
    if (best < 4) {
        return true;
    }
    bump(debugDamaCount);
    return false;                            // 默听只在"已经够大"时用
}

// ================================================================= 打哪张（teacher 的核心）

std::string Bot::chooseDiscard(const HandState &st, const std::vector<std::string> &candidates) {
    // ① 便宜的一遍：向听（1 次 DFS）+ 危险度 + 宝牌 —— 先圈定"向听最小的那一组"。
    //    ⚠ 危险度这里**一律按进攻口径**（四家取最坏 + 鸣き手威胁抬档，见 dealScore）
    const int n = static_cast<int>(candidates.size());
    std::vector<std::string> codes(static_cast<size_t>(n));
    std::vector<int> kinds(static_cast<size_t>(n), 0);
    std::vector<int> shantens(static_cast<size_t>(n), 0);
    std::vector<int> dangers(static_cast<size_t>(n), 0);
    std::vector<int> doras(static_cast<size_t>(n), 0);
    int used = 0;
    int minSh = 99;
    for (const std::string &code : candidates) {
        const int kind = parseKind(code);
        if (kind < 0 || st.counts[static_cast<size_t>(kind)] <= 0) {
            continue;
        }
        Counts after = st.counts;
        after[static_cast<size_t>(kind)]--;
        const int sh = shantenMin(after, st.meldCount);
        const DangerReport danger
                = dangerWorstReport(kind, st.visible, st.rivers, st.riichi, st.turn, st.seat);
        codes[static_cast<size_t>(used)] = code;
        kinds[static_cast<size_t>(used)] = kind;
        shantens[static_cast<size_t>(used)] = sh;
        dangers[static_cast<size_t>(used)] = dealScore(st, kind, danger);
        doras[static_cast<size_t>(used)] = doraCount(after, st.melds, st.doraIndicators);
        used++;
        if (sh < minSh) {
            minSh = sh;
        }
    }
    // ② 贵的一遍：只在"向听最小"的那一组里比效率 / 听牌形 / 打点 / 安全度
    std::string bestTile;                    // 空串 = Java 的 `null`
    double bestScore = -std::numeric_limits<double>::infinity();
    int bestDanger = kIntMax;
    Counts bestAfter{};
    Snapshot bestSnap{};
    for (int i = 0; i < used; i++) {
        const size_t ui = static_cast<size_t>(i);
        if (shantens[ui] != minSh) {
            continue;
        }
        Counts after = st.counts;
        after[static_cast<size_t>(kinds[ui])]--;
        const Snapshot snap = evalOf(after, st.meldCount, st.visible);
        double score = snap.tenpai
                ? 200 + snap.goodWaitTiles * 4.0 + snap.waitTiles + doras[ui] * 3.0
                : snap.advanceTiles + doras[ui] * 3.0;
        score -= dangers[ui] * 0.05;                   // 同效率时略偏好安全牌
        const bool better = bestTile.empty()
                || score > bestScore + 1e-9
                || (std::fabs(score - bestScore) <= 1e-9 && dangers[ui] < bestDanger)
                || (std::fabs(score - bestScore) <= 1e-9 && dangers[ui] == bestDanger
                    && betterTieBreak(codes[ui], bestTile));
        if (better) {
            bestTile = codes[ui];
            bestScore = score;
            bestDanger = dangers[ui];
            bestAfter = after;
            bestSnap = snap;
        }
    }
    if (bestTile.empty()) {
        return std::string();
    }
    // ③ 押し引き（有人立直、或"没人立直但有人鸣开得很明显"时才谈）
    if (st.opponentRiichi() || openThreat(st) >= kOpenThreatGate) {
        // Java 在这里对 `bestSnap == null` 有兜底；走到这里 `bestTile != null`
        // ⇒ `bestSnap` 必已被赋值（第一个候选就会落进 better 分支）—— 等价。
        const int needTiles = bestSnap.tenpai ? bestSnap.waitTiles : bestSnap.advanceTiles;
        const int turnsLeft = std::max(1, st.tilesLeft / 4);
        const double winP = winProbability(needTiles, st.tilesLeft, turnsLeft, minSh + 1);
        const int winPts = hanToPoints(estimatedHan(st, bestAfter, st.melds));
        const double dealP = dealProbability(bestDanger);
        const double ev = pushEv(winP, winPts, dealP);
        const double threshold = pushThreshold(st);
        const bool push = ev > threshold;
        if (push != (ev > 0)) {
            bump(debugPlacementFlipCount);               // 顺位门槛真的改了结论
        }
        if (push) {
            bump(debugPushCount);
        } else {
            bump(debugFoldCount);
            if (!st.opponentRiichi()) {
                bump(debugOpenFoldCount);                // 对手模型那条闸门（档 C）
            }
            return safestAgainstThreats(st, codes, kinds, shantens, minSh, used);
        }
    }
    return bestTile;
}

// ================================================================= 副露打分 / 鸣き役

int Bot::bestShantenAfterCall(const Counts &countsAfterCall, int meldCount, int handSize) {
    const int extra = handSize - (13 - 3 * meldCount);
    int best = 99;
    if (extra <= 0) {
        return shantenMin(countsAfterCall, meldCount);
    }
    Counts c = countsAfterCall;
    for (int k = 0; k < kKindCount; k++) {
        if (c[static_cast<size_t>(k)] == 0) {
            continue;
        }
        c[static_cast<size_t>(k)]--;
        best = std::min(best, shantenMin(c, meldCount));
        c[static_cast<size_t>(k)]++;
    }
    return best;
}

bool Bot::callWorthForMenzen(const HandState &st, const Counts &afterCall, const Meld &meld) {
    if (bestShantenAfterCall(afterCall, st.meldCount + 1, 13 - 3 * st.meldCount - 1) == 0) {
        return true;                                 // 鸣完直接听牌
    }
    std::vector<Meld> ms = st.melds;
    ms.push_back(meld);
    return estimatedHan(st, afterCall, ms) >= 2;
}

bool Bot::isYakuhai(const HandState &st, int kind) {
    if (isDragon(kind)) {
        return true;
    }
    if (!isWind(kind)) {
        return false;
    }
    const int roundWindKind = 27 + st.roundWind;
    const int seatWindKind = 27 + ((st.seat - st.dealer + 4) % 4);
    return kind == roundWindKind || kind == seatWindKind;
}

bool Bot::hasYakuPlan(const HandState &st, const Counts &afterCall, const Meld &meld) {
    const int calledKind = kindOf(meld.calledId);
    // 碰 / 大明杠 / 加杠 / 暗杠都是"刻子级"的面子；只有吃不是
    const bool isTriplet = meld.kind != Meld::Kind::CHI;
    // ① 役牌：碰役牌立刻成刻；或者暗牌里已有役牌的暗刻
    if (isTriplet && isYakuhai(st, calledKind)) {
        return true;
    }
    for (int k = 27; k < kKindCount; k++) {
        if (afterCall[static_cast<size_t>(k)] >= 3 && isYakuhai(st, k)) {
            return true;
        }
    }
    // ② 断幺九（只在食断成立的规则下算役）
    if (st.kuitan && allSimples(st, afterCall, meld)) {
        return true;
    }
    // ③ 混一色 / 清一色
    if (singleSuit(st, afterCall, meld)) {
        return true;
    }
    // ④ 对对和：手上全是碰/杠，且暗牌里还有两组"刻子或对子"可用
    if (isTriplet) {
        bool allTriplets = true;
        for (const Meld &m : st.melds) {
            if (m.kind == Meld::Kind::CHI) {
                allTriplets = false;
            }
        }
        if (allTriplets) {
            int pairs = 0;
            for (int k = 0; k < kKindCount; k++) {
                if (afterCall[static_cast<size_t>(k)] >= 2) {
                    pairs++;
                }
            }
            if (pairs >= 2) {
                return true;
            }
        }
    }
    return false;
}

// ================================================================= 开杠

bool Bot::shouldKan(const HandState &st, const std::string &kanKind, int kind) {
    if (kind < 0 || kKindCount <= kind) {
        return false;
    }
    const bool kakan = kanKind != "ankan";
    const int cur = shantenMin(st.counts, st.meldCount);
    // ① 弃和中不开
    if (st.opponentRiichi() && cur >= 2) {
        bump(debugKanRefusePressure);
        return false;
    }
    // ② 别把本局打散（第 4 个杠会四杠散了）
    if (st.fourKanAbort && st.kanCount == 3 && st.myKans() < st.kanCount) {
        bump(debugKanRefuseFourKan);
        return false;
    }
    // ③ 加杠先看会不会被抢（无筋中张 = DANGEROUS）
    if (kakan) {
        const DangerReport d
                = dangerWorstReport(kind, st.visible, st.rivers, st.riichi, st.turn, st.seat);
        if (d.level >= kDangerDangerous) {
            bump(debugKanRefuseDanger);
            return false;
        }
    }
    // ④ 杠完不能丢听牌、也不能倒退
    Counts after = st.counts;
    after[static_cast<size_t>(kind)] = static_cast<uint8_t>(
            after[static_cast<size_t>(kind)]
            - std::min(kakan ? 1 : 4, static_cast<int>(after[static_cast<size_t>(kind)])));
    const int afterMelds = kakan ? st.meldCount : st.meldCount + 1;
    if (shantenMin(after, afterMelds) > cur) {
        bump(debugKanRefuseWait);
        return false;
    }
    bump(debugKanCount);
    return true;
}

// ================================================================= 外部小工具

std::vector<int> Bot::waitsOf(const Round &r, int seat) {
    // Java 返回 `LinkedHashSet<Integer>`（升序去重）；`waits` 本身就是升序去重的，故直接给数组
    return waits(r.concealCounts(seat),
                 static_cast<int>(r.melds[static_cast<size_t>(seat)].size()));
}

// ================================================================= 策略适配

Policy makeTeacherPolicy() {
    // Java `Policies.TEACHER = d -> Bot.decide(d.round, d.obs.seat, d.kind, d.options, d.extra)`
    // —— **不**经过 `fromAction` 的三道保护（不查 legal、不退回），这里保持一致。
    return [](const Decision &d) {
        if (d.round == nullptr || d.obs == nullptr) {
            // 不可达（Decision 由 `Round::ask` 构造）；只为 `-Wall` 与防御留一支，
            // 行为等价于 `Bot.fallback` 在空选项上的结果。
            return cmdOf(kActPass);
        }
        const std::vector<Option> none;
        Cmd c = Bot::decide(*d.round, d.obs->seat, d.kind,
                            d.options == nullptr ? none : *d.options, d.calledTileId);
        // 标记"来自内置 Bot"：漏斗据此**跳过** `legal` 校验（Java 的 `TEACHER` 也一样，
        // 校验只活在 `Policies.fromAction` 里），见 `Cmd::fromBot`。
        c.fromBot = true;
        return c;
    };
}

}  // namespace trainer
