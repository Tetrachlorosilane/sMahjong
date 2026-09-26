// 一局（kyoku）的完整状态机 —— 与 Java `mahjong.game.Round` **逐句对应**。
//
// 为什么这一层必须逐句抄：它是"轨迹"的**唯一来源**。`docs/TRAINER-CPP.md` §2 的判据是
// "同 (seedBase, 策略串) ⇒ `g*.jsonl` 与 Java 逐字节相同"，而决策行是**发生顺序**的产物 ——
// 选项顺序、鸣牌仲裁顺序、见逃记账的时点、杠没成立就退回摸切……任何一处"差不多"都会让
// 行数或行内容漂移，而且**不会报错**。
//
// 结构上与 Java 的对应关系：
//   `Round extends RoundState`（状态容器 + 配牌，已单独对拍过，见 roundstate.hpp）；
//   选项生成走 `turnoptions.hpp` / `claimoptions.hpp` 的**结构化**入口（文本/动作空间同源）；
//   和了/流局/连庄判据走 `evaluator.hpp` / `payments.hpp` / `roundscoring.hpp`（都已对拍）。
//
// ⚠ 训练端的**已知简化**（都在本文件里显式标注）：
//   ① 四个座位恒为机器人（`SelfPlay` 对每个座位 `addBot`）→ Java 的"掉线托管"与"网络等待"
//      两条分支到不了（`awaySeat` ≡ `!bot`，而 `bot` 恒真）；
//   ② 没有投票 / 没有回放 / 没有报文（只有 `round_end` 事件喂给轨迹记录器）。
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "counts.hpp"
#include "evaluator.hpp"
#include "handeval.hpp"
#include "meld.hpp"
#include "observation.hpp"
#include "options.hpp"
#include "payments.hpp"
#include "policies.hpp"
#include "roundstate.hpp"
#include "rules.hpp"
#include "tiles.hpp"
#include "visible.hpp"
#include "wall.hpp"

namespace trainer {

class Table;

/** 一局的结算结果（Java `Round.Result` 同字段）。 */
struct RoundResult {
    bool agari = false;
    bool abortive = false;
    std::string abortReason;
    std::array<bool, 4> tenpai{};
    std::array<int, 4> delta{};
    int sticksLeft = 0;
    bool dealerRenchan = false;
    bool nagashi = false;
    int winner = -1;
    int loser = -1;
    bool tsumo = false;
};

class Round : public RoundState {
public:
    /**
     * 一条**包牌责任**（Java `Round.Pao`：`payer` 打出的牌让本家确定了 `yaku` 这一役满）。
     * ⚠ 与 `payments.hpp` 的 `trainer::Pao`（责任**支付**，带基本点）是两件事，所以嵌在这里。
     */
    struct Pao {
        int payer = 0;
        std::string yaku;
    };

    /**
     * 鸣牌阶段的结论（Java `Round.Claim`；`isNull` = 这一张没人鸣）。
     * ⚠ 也嵌在这里：`roundclaims.hpp` 里的 `trainer::Claim` 是**仲裁判据**用的
     * "等级 + 座次距离"，同名不同物（那一个已单独对拍过）。
     */
    struct Claim {
        bool isNull = true;
        enum class Type { RON, PON, CHI, KAN };
        Type type = Type::RON;
        int seat = -1;
        /** 吃：从手里取出的两张 id。 */
        std::array<int, 4> tiles{};
        int tileCount = 0;
        /** 碰 / 大明杠：客户端指定的精确牌码（空 = 老客户端，走"普通牌优先"的默认取法）。 */
        std::vector<std::string> wantTiles;
        bool hasWantTiles = false;
        /** 多家荣和：按"距放铳者由近到远"。 */
        std::vector<int> multiRon;
        /** 非空表示本次鸣牌阶段的结论是**中途流局**（三家和了）。 */
        std::string abortReason;
        bool hasAbort = false;
    };

    Round(Table *tbl, int wind, int ky, int hb, int dl, const std::array<int, 4> &sc, int st,
          int64_t seed)
        : RoundState(tableRules(tbl), wind, ky, hb, dl, sc, st, seed), table(tbl) {}

    /** Java `Round.play()`：配牌 → 摸切 → 鸣牌 → 和了/流局。 */
    RoundResult play();

    Table *table;

    // ---------------------------------------------------------------- 局面查询
    /** 暗牌计数（长度 34）。 */
    Counts concealCounts(int seat) const {
        Counts c{};
        for (int id : hand[static_cast<size_t>(seat)]) {
            c[static_cast<size_t>(kindOf(id))]++;
        }
        return c;
    }

    /** 自己**曾经**打出的牌种（升序）—— 舍张振听的判据（含被鸣走的舍牌）。 */
    std::vector<int> ownDiscardKinds(int seat) const { return furiten.ownDiscardKinds(seat); }

    /** 听牌种类（`Agari.waits` 的等价物；Java 有记忆化，训练端直接算）。 */
    std::vector<int> waitKinds(int seat) const {
        return waits(concealCounts(seat),
                     static_cast<int>(melds[static_cast<size_t>(seat)].size()));
    }

    /** 三种振听合一（Java `Round.isFuriten`）。 */
    bool isFuriten(int seat) const { return furiten.isFuriten(seat, waitKinds(seat)); }

    /** 和了牌并入暗牌并校验张数（Java `WinCheck.counts`；张数不符返回 false）。 */
    bool winCountsOf(int seat, int winTileId, bool tsumo, Counts &out) const {
        return winCounts(concealCounts(seat),
                         static_cast<int>(melds[static_cast<size_t>(seat)].size()),
                         kindOf(winTileId), tsumo, out);
    }

    /** **实局**和了判定（Java `Round.checkWin`）：返回是否和了，分数写在 `out`。 */
    bool checkWin(int seat, int winTileId, bool tsumo, bool rinshan, bool haitei, bool chankan,
                  bool houtei, HandScore &out) const;

    /**
     * 和了被挡住的原因（Java `Round.winBlockReason`）：
     * `false` = 没挡住（要么能和、要么本来就不是和了形）；`true` 时 `out` 是
     * `"furiten"` / `"no_yaku"`。
     */
    bool winBlockReason(int seat, int winTileId, bool tsumo, std::string &out) const;

    /** 自家回合的选项（Java `Round.turnOptions`）。 */
    std::vector<Option> turnOptions(int seat, int drawn, bool rinshan) const;

    /** 鸣牌询问的选项（Java `Round.claimOptions`）。 */
    std::vector<Option> claimOptions(int seat, int from, int tileId) const;

    /** 观测构造（Java `Observation.ofTurn` / `ofClaim`）。 */
    Observation makeObservation(int seat, const std::string &kind, const std::vector<Option> &opts,
                                int drawnId, bool rinshan, int from, bool hasCalledTile,
                                const std::string &calledTile, bool hasWinNote,
                                const std::string &winNote) const;

    /** `wall.atLastLiveTile()`：摸到 / 打出它之后就荒牌流局（海底 / 河底）。 */
    bool atLastLiveTile() const { return wall.atLastLiveTile(); }

    // ---------------------------------------------------------------- 状态（Java 同名字段）
    /** 上一张打出的牌是否发生在开杠之后（古役「杠振」）。 */
    bool kanJustHappened = false;
    /** 这张刚打出的牌是不是杠振牌（`chankanKoyaku`）。 */
    bool chankanKoyaku = false;
    std::array<std::vector<Pao>, 4> pao;
    bool anyCall = false;
    std::array<int, 4> kanByPlayer{};
    int totalDiscards = 0;
    int lastDiscardSeat = -1;
    int lastDiscardTile = -1;
    bool lastDiscardTsumogiri = false;
    /** 牌河里需要**横置**的那张的下标（-1 = 无；只有立直宣言牌或其顺延牌）。 */
    std::array<int, 4> sidewaysDiscard{-1, -1, -1, -1};
    std::array<bool, 4> sidewaysPending{};
    std::vector<int> forbiddenDiscard;
    /** 被鸣走那张牌在原牌河中的下标（-1 = 无）。 */
    std::array<int, 4> discardCalledIndex{-1, -1, -1, -1};

private:
    static Rules tableRules(Table *t);

    /** Java `awaySeat`：训练端四个座位恒为机器人 → `!bot` 恒假（见类注释 ①）。 */
    bool awaySeat(int seat) const;

    void sortHands();
    /** 「出牌」的**唯一**记账点：牌河 + 曾经打出过（振听）+ 横置。 */
    void recordDiscard(int seat, int id, bool declareRiichi);
    bool noteDiscard(int seat, bool declareRiichi);
    void noteCalledFromRiver(int from, int index);
    void removeCalledFromRiver(int from, int calledIndex);
    void incRiichiDiscard(int seat);
    void doRiichi(int seat, int discardTile);
    void clearIppatsu();
    void revealKanDora();

    int redCount(int seat) const;
    bool scoreWith(int seat, const Counts &merged, int winTileId, bool tsumo, bool rinshan,
                   bool haitei, bool chankan, bool houtei, bool assumeRiichi, bool includeUra,
                   int akaInConcealed, HandScore &out) const;
    std::vector<int> akaTiles(int seat, int winTileId, int akaInConcealed) const;

    bool riichiAllowed(int seat) const;
    bool canRiichiSeat(int seat, int tileId) const;
    bool kanAllowedByRiichi(int seat, int kind, int drawn) const;
    bool ankanKeepsShape(int seat, int kind) const;
    int defaultDiscardId(int seat, int drawn) const;
    int resolveTile(const Cmd &act, int seat) const;
    int resolveDiscardId(const Cmd &act, int seat, bool wantTsumogiri, int drawn) const;
    bool canKyuushu(int seat) const;
    bool canDaiminkan(int seat, int kind) const;

    RoundResult turnKan(int seat, const Cmd &act, int drawn, bool &ended);
    std::vector<int> chankanRon(int kanSeat, int tileId, bool kokushiOnly) const;
    bool isKokushiScore(const HandScore &sc) const;
    RoundResult agariTsumo(int seat, int tileId, const HandScore &sc);
    RoundResult agariRon(const std::vector<int> &winners, int from, int tileId, bool chankan,
                         bool riichiDiscard);
    void applyDelta(RoundResult &r, const std::array<int, 4> &d);
    std::vector<trainer::Pao> paoPaysFor(int seat, const HandScore &sc) const;
    std::vector<int> paoSeatsOf(int seat) const;
    static int yakumanBaseOf(const HandScore &sc, const std::string &yaku);
    RoundResult exhaustive();
    RoundResult abort(const std::string &reason);
    bool fourKanAbortNow() const;
    bool allKansByOnePlayer() const;

    Claim claimPhase(int from, int tileId, bool riichiDiscard);
    bool pickChiTiles(int seat, int tileId, const std::vector<std::string> &want,
                      std::array<int, 2> &out) const;
    int findHandTile(int seat, int kind, bool red, const std::vector<int> &used) const;
    bool pickHandTiles(int seat, int tileId, const std::vector<std::string> &want, int n,
                       std::vector<int> &out) const;
    std::vector<int> pickAuto(int seat, int kind, int n) const;
    void applyMeld(const Claim &cl, int from, int tileId);
    void updatePao(int seat, int from, const Meld &m);
    void addPao(int seat, int payer, const std::string &yaku);

    /**
     * 喂给策略的一次询问（四个座位恒为机器人 → 直接走 `Table::decideBot`）。
     *
     * @param opts        本次询问的**原始选项**（Java `Decision.options`）—— teacher 的取舍
     *                    按它走（`first`/`pass`/`random`/`net` 只用 `obs.legal`）
     * @param calledTileId 鸣牌询问的"被鸣那张牌 id"（= Java `extra.tile` 的来源）；自家回合传 -1
     */
    Cmd ask(int seat, const std::string &kind, const Observation &obs,
            const std::vector<Option> &opts, int calledTileId);
};

}  // namespace trainer
