// 一张牌桌 = 一整场（半庄）的推进器 —— 与 Java `mahjong.game.Table` 的
// `playGame()` / `decideBot()` 同口径；只保留训练端用得上的那部分。
//
// 与 Java 的差异（都是**训练端不存在的东西**，不是行为差异）：
//   · 没有网络/座位/房间/回放/投票 —— 四个座位恒为机器人（`addBot`），决策一律走
//     `decideBot`（= 唯一的决策漏斗，AGENTS §6.5）；
//   · 没有报文，只有一个 `debugEventTap`（轨迹记录器只认 `round_end`，与 Java 的
//     `TraceRecorder.onEvent` 认的是同一件事）；
//   · 局间没有 `sleepMs` / `awaitRoundConfirm`（Java 那两步对轨迹的**唯一**影响是
//     `round_wait` 事件，而记录器不认它；四个机器人本来就"视为立即确认"）。
#pragma once

#include <array>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <optional>
#include <string>

#include "java_rand.hpp"
#include "observation.hpp"
#include "policies.hpp"
#include "round.hpp"
#include "roundscoring.hpp"
#include "rules.hpp"
#include "seed.hpp"
#include "yaku_codes.hpp"

namespace trainer {

/** 座位（Java `Table.Seat` 的训练端子集）。 */
struct Seat {
    int index = 0;
    int score = 25000;
    bool bot = true;
    int timeBankMs = 20000;
};

/** `round_end` 报文里轨迹需要的那些字段（Java `Table.playGame` 广播的那一条）。 */
struct RoundEndEvent {
    int roundWind = 0;                 // `round.bakaze` 的数字形式（0=E）
    int kyoku = 1;
    int honba = 0;
    int sticks = 0;                    // `round.riichi_sticks` = `res.sticksLeft`
    std::array<int, 4> scores{};       // 本局结算后的四家点数
    bool agari = false;
    bool abortive = false;
    std::string reason;                // 已是码（`YakuCodes.reasonOf(res.abortReason)`）
    bool renchan = false;
    const RoundResult *result = nullptr;   // = `Table.lastResult`
};

class Table {
public:
    explicit Table(const Rules &r) : rules(r) {
        for (int i = 0; i < 4; i++) {
            seats[static_cast<size_t>(i)].index = i;
            seats[static_cast<size_t>(i)].score = r.startScore;
            seats[static_cast<size_t>(i)].bot = true;
            seats[static_cast<size_t>(i)].timeBankMs = r.thinkingBankMs;
        }
    }

    Rules rules;
    std::array<Seat, 4> seats;
    /** 每场种子基准（`SelfPlay.oneGame` 写死它，`--seed`）。 */
    int64_t seedBase = 0;
    /** 自对弈恒为真（Java `SelfPlay.oneGame`）：每局种子走 `mixSeed(seedBase + 局序号)`。 */
    bool debugDeterministicSeed = true;
    /** `--hands n`（0 = 打完整场）：跑满 n 小局就收尾，**仍然走终局余棒分配**。 */
    int debugMaxHands = 0;
    /** 按座位注入的策略（每场一份实例，见 `PolicyFactory`）。 */
    std::array<Policy, 4> policy;
    /** 唯一的决策漏斗（Java `Table.decideBot`）。 */
    std::function<void(const Observation &, const std::string &kind, const Action &cmd,
                       const std::string &roundKey)>
        debugChoiceTap;
    /** 出站报文旁路（训练端只有 `round_end`）。 */
    std::function<void(const RoundEndEvent &)> debugEventTap;
    Round *currentRound = nullptr;
    /** 最近一局的结算结果（轨迹记录器在 `round_end` 的钩子里读它）。 */
    RoundResult lastResult;
    /** 策略层违规（Java 会退回内置机器人，训练端没有 Bot）→ 上层据此报错退出。 */
    std::string fatal;
    /** 本场第几次决策（1 起）：报错信息里带上，便于按"场号 + 步号"复现。 */
    int decisionOrdinal = 0;
    /** "自家回合 legal 为空"的次数（Java 侧同样为空 → 不是分歧，见 `decideBot` 的注释）。 */
    int engineFallbacks = 0;

    void addBot(int at) {
        if (at < 0 || at >= 4) {
            return;
        }
        seats[static_cast<size_t>(at)].bot = true;
        seats[static_cast<size_t>(at)].score = rules.startScore;
    }

    Seat &seat(int i) { return seats[static_cast<size_t>(i)]; }
    const Seat &seat(int i) const { return seats[static_cast<size_t>(i)]; }

    /**
     * 机器人专用随机源：**只用来打破平局**，不参与任何规则判定（Java `Table.botRng()`）。
     *
     * <p>必须由 `seedBase` 派生而不是 `std::random_device`：自测/自对弈把 `seedBase` 写死并打开
     * `debugDeterministicSeed` 时，整场（含机器人的随机选择）必须完全可复现 —— 那是数据可复现与
     * "配对同牌山评测"的前提（`Bot` 的九种九牌分支原来用 `Math.random()`，就是靠这里修掉的）。
     *
     * <p>逐位照抄 Java：**惰性创建、整场共享**，种子 =
     * `seedBase * 0x2545F4914F6CDD1DL + 0x9E3779B9L`（long 溢出按回绕处理 → C++ 在无符号域算）。
     * ⚠ 是**整场**一份（不是每局一份）：同一桌的多次询问共用同一条流。
     */
    JavaRandom &botRng() {
        if (!botRng_.has_value()) {
            const uint64_t z = static_cast<uint64_t>(seedBase) * 0x2545F4914F6CDD1DULL
                               + 0x9E3779B9ULL;
            botRng_.emplace(static_cast<int64_t>(z));
        }
        return *botRng_;
    }

    /** 唯一的决策漏斗：策略 → （异常/null → 内置机器人）；训练端没有 Bot，违规即报错（一个例外见下）。 */
    Cmd decideBot(int seatIdx, const Observation &obs, const std::string &kind,
                  const std::string &roundKey, const Round *r, const std::vector<Option> &opts,
                  int calledTileId) {
        Decision d;
        d.obs = &obs;
        d.kind = kind;
        // Java `Decision(obs, this, kind, opts, extra)` 的另外三项：
        //   `round` 只给内置 teacher 与记录用（外部策略不许读它，见 Observation 顶部注释）；
        //   `options` / `extra.tile` 同理（teacher 的取舍要按**原始选项**走）。
        d.round = r;
        d.options = &opts;
        d.calledTileId = calledTileId;
        Cmd cmd;
        const int ordinal = ++decisionOrdinal;
        if (policy[static_cast<size_t>(seatIdx)]) {
            cmd = policy[static_cast<size_t>(seatIdx)](d);
        }
        if (!cmd.valid) {
            fatal = "策略没有返回回包（座位 " + std::to_string(seatIdx)
                    + "）—— 训练端没有内置 Bot 兜底（见 policies.hpp 顶部注释）";
        } else if (cmd.fromBot) {
            // 内置 Bot（teacher）的回包：Java `Policies.TEACHER` **不经过** `fromAction` 的三道
            // 保护，回包原样交给状态机（合法性由 `Round` 自己判）。所以这里**不做 legal 校验**。
            // §6.15 的退化局面（自家回合 legal 为空）在这里走同一条兜底链（`round.cpp` 会在
            // `discardAllowed` 失败时调 `defaultDiscardId`）—— 仍然计数 + 打一行便于对照。
            if (obs.legal.empty() && kind == "turn") {
                engineFallbacks++;
                std::fprintf(stderr,
                             "[trainer] 引擎兜底出牌（teacher，自家回合 legal 为空：与 Java 同走 "
                             "defaultDiscardId，且这一条决策不进轨迹）座位 %d，本场第 %d 次决策\n",
                             seatIdx, ordinal);
            }
        } else if (obs.indexOfKey(cmd.action.key()) < 0 && cmd.action.type != kActPon
                   && cmd.action.type != kActKan) {
            // Java `Policies.fromAction` 在"回包不在本次 legal 里"时退回**内置机器人**；训练端没有 Bot。
            //
            // 唯一的例外：**自家回合的 legal 为空**。这不是"策略回错了"，而是 Java 引擎自己的退化局面 ——
            // 食替（`RoundOptions.kuikaeForbidden`）可能把暗手里每一种牌都禁打（例：吃 5s6s7s 时用
            // 6s+7s 吃 5s，之后手里只剩 88s，而 8s = hi+1 是筋食替禁打），于是
            // `discardChoices` 返回空 → `Policies.first/pass` 只能回 `Action.PASS`。
            // 这个局面**两侧的状态完全一致**（不是分歧），后续也完全确定：
            //   · Java：`fromAction` 退回内置 Bot → Bot 的回包被 `Round.discardAllowed` 判非法 →
            //     强制 `defaultDiscardId` 兜底出牌。这条链与 Bot 的取舍**无关**：空 legal 意味着
            //     手里每一张都被禁打，Bot 回哪张都会被判非法（回非打牌动作同样被拒）。
            //   · 轨迹：记录器的 `Action.resolve(cmd, legal=[])` 也解不出来 → **这一行不进轨迹**
            //     （Java 同：`observed` 仍 +1，`step` 不动）。
            // 所以训练端把策略的原回包**原样**交给引擎、走同一条兜底链即可逐字节复现
            // （`round.cpp` 的回合循环已经在 `discardAllowed` 失败时调 `defaultDiscardId`）。
            if (obs.legal.empty() && kind == "turn") {
                engineFallbacks++;
                std::fprintf(stderr,
                             "[trainer] 引擎兜底出牌（自家回合 legal 为空：与 Java 同走 "
                             "defaultDiscardId，且这一条决策不进轨迹）座位 %d，本场第 %d 次决策\n",
                             seatIdx, ordinal);
            } else {
                fatal = "策略回包不在本次 legal 里（本场第 " + std::to_string(ordinal) + " 次决策，座位 "
                        + std::to_string(seatIdx) + "，kind=" + kind + "，键 " + cmd.action.key()
                        + "，legal=" + std::to_string(obs.legal.size())
                        + " 条）—— 训练端没有内置 Bot 兜底";
            }
        }
        if (debugChoiceTap) {
            debugChoiceTap(obs, kind, cmd.action, roundKey);
        }
        return cmd;
    }

    /** 跑完一整场（Java `Table.playGame` 的训练端等价物）。 */
    void playGame() {
        std::array<int, 4> scores{};
        for (int i = 0; i < 4; i++) {
            scores[static_cast<size_t>(i)] = rules.startScore;
            seats[static_cast<size_t>(i)].score = rules.startScore;
            seats[static_cast<size_t>(i)].timeBankMs = rules.thinkingBankMs;
        }
        int roundWind = 0;
        int kyoku = 1;
        int honba = 0;
        int dealer = 0;
        int sticks = 0;
        int roundIndex = 0;                // `Table.roundSeedIndex`
        bool gameOver = false;
        int handsPlayed = 0;
        while (!gameOver && (debugMaxHands <= 0 || handsPlayed < debugMaxHands)) {
            handsPlayed++;
            // 额外思考时长**每小局重置**（训练端不用它，但账要与 Java 一致）
            for (int i = 0; i < 4; i++) {
                seats[static_cast<size_t>(i)].timeBankMs = rules.thinkingBankMs;
            }
            // `Table.nextRoundSeed()` 的**确定性岔路**（debugDeterministicSeed = true）：
            // 第 i 局的种子 = `mixSeed(seedBase + i)`，i 从 0 起（生产路径是时刻种子，训练端不用）
            const int64_t seed = roundSeedAt(seedBase, roundIndex++);
            Round r(this, roundWind, kyoku, honba, dealer, scores, sticks, seed);
            const RoundResult res = r.play();
            currentRound = nullptr;
            scores = r.scores;
            sticks = res.sticksLeft;
            for (int i = 0; i < 4; i++) {
                seats[static_cast<size_t>(i)].score = scores[static_cast<size_t>(i)];
            }
            lastResult = res;                              // 必须在 `round_end` **之前**赋值
            if (debugEventTap) {
                RoundEndEvent ev;
                ev.roundWind = roundWind;
                ev.kyoku = kyoku;
                ev.honba = honba;
                ev.sticks = sticks;
                ev.scores = scores;
                ev.agari = res.agari;
                ev.abortive = res.abortive;
                ev.reason = reasonCodeOf(res.abortReason);  // ⚠ 认不出的（含 ""）→ 空串
                ev.renchan = res.dealerRenchan;
                ev.result = &lastResult;
                debugEventTap(ev);
            }
            // 局间：Java 会 `sleepMs(roundDelayMs)` + `awaitRoundConfirm()`；两者都不产生
            // 轨迹记录器认得的事件（四个机器人视为立即确认），所以训练端直接跳过。
            if (rules.tobi) {                              // 击飞
                for (int v : scores) {
                    if (v < 0) {
                        gameOver = true;
                    }
                }
            }
            if (!gameOver) {
                honba = nextHonba(honba, res.dealerRenchan, res.agari, res.nagashi);
                if (res.dealerRenchan) {
                    // 和了止 / 听牌止（三条件：`agariyame` 开、庄家和了或庄家听牌、庄家 1 位且达门槛）
                    if (kyoku == 4 && roundWind == lastWindOf(rules)
                            && stopAtAllLast(dealer, res.agari, res.nagashi, res.tenpai, scores,
                                             rules)) {
                        gameOver = true;
                    }
                } else {
                    const int nd = (dealer + 1) % 4;
                    int nw = roundWind;
                    int nk;
                    if (nd == 0) {
                        nw = roundWind + 1;
                        nk = 1;
                    } else {
                        nk = nd + 1;
                    }
                    if (nw > lastWindOf(rules)) {
                        int top = -1;
                        for (int v : scores) {
                            top = v > top ? v : top;
                        }
                        // 延长战（南入 / 西入）：门槛是 `requiredPoints`，**不是** `returnScore`
                        if (keepPlayingWest(rules, top, nw, lastWindOf(rules))) {
                            roundWind = nw;
                            kyoku = nk;
                            dealer = nd;
                        } else {
                            gameOver = true;
                        }
                    } else {
                        roundWind = nw;
                        kyoku = nk;
                        dealer = nd;
                    }
                }
            }
        }
        // 终局时供托中的立直棒按 M.League 原文分给 1 位（只有"最后一局是流局"才走这里）
        if (sticks > 0) {
            const std::array<int, 4> add = endGameSticks(scores, sticks);
            for (int i = 0; i < 4; i++) {
                if (add[static_cast<size_t>(i)] != 0) {
                    scores[static_cast<size_t>(i)] += add[static_cast<size_t>(i)];
                    seats[static_cast<size_t>(i)].score = scores[static_cast<size_t>(i)];
                }
            }
        }
        // sendGameEnd / saveReplay：训练端不需要（记录器只认 `round_end`）
    }

private:
    /** Java `Table.botRng` 字段（private，惰性创建；见上面的访问器）。 */
    std::optional<JavaRandom> botRng_;
};

}  // namespace trainer
