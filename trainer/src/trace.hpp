// 单场轨迹记录器 —— 与 Java `mahjong.train.TraceRecorder` **逐字段、逐键序**一致。
//
// 产出契约（`g<g>.jsonl`）的三条硬性质（见 `docs/TRAINER-CPP.md` §2 与
// `server/src/main/java/mahjong/train/TraceRecorder.java` 顶部注释）：
//   ① **三段拼接、不按时间交错**：全部 `decision` 行（按发生顺序、跨小局连续）→ 全部 `hand`
//      行（按 `round_end` 顺序）→ **恰好一行** `game` 行；
//   ② 行分隔符 = Java `Files.write(Iterable)` 的语义 = `System.lineSeparator()` →
//      **Windows 上 CRLF，最后一行也有**；UTF-8 无 BOM、无空格、无缩进；
//   ③ 奖励是**事后回填**的：`hand_*` 在小局结束时补、`final_scores`/`placement` 在整场结束时补
//      —— 因为文件是最后一次性写出的，只要按最终值输出即可（键序仍是"新键追加在 obs 之后"）。
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "observation.hpp"
#include "table.hpp"

namespace trainer {

/** 小局结算行（Java `TraceRecorder.onEvent` 里那条 `hand` 行的字段）。 */
struct HandRow {
    int handNo = 0;
    std::string handKey;
    int roundWind = 0;
    int kyoku = 1;
    int honba = 0;
    int sticks = 0;
    std::array<int, 4> scoresAfter{};
    std::array<int, 4> delta{};
    bool agari = false;
    bool abortive = false;
    std::string reason;
    bool renchan = false;
    int winner = -1;
    int loser = -1;
    bool tsumo = false;
    bool nagashi = false;
    std::array<bool, 4> tenpai{};
    bool hasResult = true;                 // `res == null` → `tenpai` 写空数组
};

class TraceRecorder {
public:
    TraceRecorder(int gameIndex, int64_t seed, std::string dir,
                  const std::array<std::string, 4> &labels, int startScore, int sampleEvery,
                  bool recordClaims, bool keepDecisions)
        : gameIndex_(gameIndex), seed_(seed), dir_(std::move(dir)), labels_(labels),
          startScore_(startScore), sampleEvery_(sampleEvery < 1 ? 1 : sampleEvery),
          recordClaims_(recordClaims), keepDecisions_(keepDecisions) {
        runningScores_.fill(startScore);
    }

    /** 一次决策（Java `TraceRecorder.onChoice`；挂在唯一的决策漏斗上）。 */
    void onChoice(const Observation &obs, const std::string &kind, const Action &cmd,
                  const std::string &roundKey);

    /** 一条 `round_end`（Java `TraceRecorder.onEvent` 只认广播的那一条）。 */
    void onRoundEnd(const RoundEndEvent &ev);

    /** 整场结束：回填顺位并落盘（Java `TraceRecorder.finish`）。 */
    void finish(const std::array<int, 4> &finalScores);

    /** 本场实际发生的决策次数（**不受采样影响**；`summary.decisions` 用它）。 */
    int decisionCount() const { return observed_; }

    /** 小局结算行（统计用；Java `handRows()`）。 */
    const std::vector<HandRow> &handRows() const { return hands_; }

    /** 决策行数（= `game.decisions`，**采样之后**）。 */
    int rowCount() const { return static_cast<int>(decisions_.size()); }

private:
    struct DecisionRow {
        int handNo = 0;
        std::string handKey;
        int step = 0;
        int seat = 0;
        std::string policy;
        std::string kind;
        std::vector<std::string> legal;
        std::string chosen;
        int chosenIndex = -1;
        std::string obsJson;
        int handIdx = -1;                  // 指向 `hands_`；-1 = 没被任何小局回填过
    };

    void rollHand(const std::string &key);

    int gameIndex_;
    int64_t seed_;
    std::string dir_;                      // 空 = 只统计、不落盘
    std::array<std::string, 4> labels_;
    int startScore_;
    int sampleEvery_;
    bool recordClaims_;
    bool keepDecisions_;

    std::vector<DecisionRow> decisions_;
    std::vector<HandRow> hands_;
    /** 记录器自己维护的分数账（`Round.scores` 在局内会被立直扣点改动，不能当"局前分"用）。 */
    std::array<int, 4> runningScores_{};

    int handNo_ = -1;
    std::string handKey_;
    int step_ = 0;
    int observed_ = 0;
};

}  // namespace trainer
