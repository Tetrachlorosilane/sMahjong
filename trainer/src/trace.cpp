#include "trace.hpp"

#include <cstdio>
#include <filesystem>
#include <string>

#include "jsonw.hpp"
#include "seed.hpp"

namespace trainer {
namespace {

/** `round` 子对象（`round_end` 报文里的那一个；Java `TraceRecorder` 直接搬它）。 */
std::string roundJson(int roundWind, int kyoku, int honba, int sticks) {
    static const char *kWindNames[4] = {"E", "S", "W", "N"};
    const int w = roundWind < 0 ? 0 : (roundWind > 3 ? 3 : roundWind);
    std::string o = "{\"bakaze\":";
    jsonStr(o, kWindNames[w]);
    o += ",\"kyoku\":";
    o += std::to_string(kyoku);
    o += ",\"honba\":";
    o += std::to_string(honba);
    o += ",\"riichi_sticks\":";
    o += std::to_string(sticks);
    o.push_back('}');
    return o;
}

}  // namespace

void TraceRecorder::rollHand(const std::string &key) {
    if (key != handKey_) {
        handKey_ = key;
        handNo_++;
    }
}

void TraceRecorder::onChoice(const Observation &obs, const std::string &kind, const Action &cmd,
                             const std::string &roundKey) {
    observed_++;
    // 小局编号必须每次都推进（统计行也要用它），所以先 roll 再判是否记录
    rollHand(roundKey);
    if (!keepDecisions_) {
        return;
    }
    if (sampleEvery_ > 1 && (observed_ % sampleEvery_) != 0) {
        return;
    }
    if (!recordClaims_ && kind == "claim") {
        return;
    }
    // `resolve` 而不是 `fromCmd`：策略可以回**部分指定**的包（裸 pon / 不带 tiles 的大明杠），
    // 而服务端按"普通牌优先"的默认取法执行 —— 那正是本次 legal 里的第一条。
    bool ok = false;
    const std::string chosen = actionResolveKey(cmd.cmdTokens(), obs.legalKeys(), ok);
    if (!ok) {
        return;                            // 认不出的回包**不编造标签**（宁可少一条样本）
    }
    DecisionRow row;
    row.handNo = handNo_;
    row.handKey = handKey_;
    row.step = step_++;
    row.seat = obs.seat;
    row.policy = labels_[static_cast<size_t>(obs.seat)];
    row.kind = kind;
    row.legal = obs.legalKeys();
    row.chosen = chosen;
    row.chosenIndex = obs.indexOfKey(chosen);
    row.obsJson = obs.toJson();
    // 将要写出的那一条 `hand` 行的下标（小局行是一条一条按 `round_end` 顺序追加的）
    row.handIdx = static_cast<int>(hands_.size());
    decisions_.push_back(std::move(row));
}

void TraceRecorder::onRoundEnd(const RoundEndEvent &ev) {
    // 小局键的格式必须与 `onChoice` 那侧**同格式**，否则连庄会被当成换局
    rollHand(std::to_string(ev.roundWind) + "-" + std::to_string(ev.kyoku) + "-"
             + std::to_string(ev.honba));
    std::array<int, 4> delta{};
    for (int i = 0; i < 4; i++) {
        delta[static_cast<size_t>(i)] = ev.scores[static_cast<size_t>(i)]
                - runningScores_[static_cast<size_t>(i)];
        runningScores_[static_cast<size_t>(i)] = ev.scores[static_cast<size_t>(i)];
    }
    HandRow row;
    row.handNo = handNo_;
    row.handKey = handKey_;
    row.roundWind = ev.roundWind;
    row.kyoku = ev.kyoku;
    row.honba = ev.honba;
    row.sticks = ev.sticks;
    row.scoresAfter = ev.scores;
    row.delta = delta;
    row.agari = ev.agari;
    row.abortive = ev.abortive;
    row.reason = ev.reason;
    row.renchan = ev.renchan;
    if (ev.result != nullptr) {
        row.winner = ev.result->winner;
        row.loser = ev.result->loser;
        row.tsumo = ev.result->tsumo;
        row.nagashi = ev.result->nagashi;
        row.tenpai = ev.result->tenpai;
        row.hasResult = true;
    } else {
        row.hasResult = false;             // Java：`res == null` → `tenpai` 是空数组
    }
    hands_.push_back(std::move(row));
}

void TraceRecorder::finish(const std::array<int, 4> &finalScores) {
    const std::array<int, 4> placement = placementOf(finalScores);
    if (dir_.empty()) {
        return;                            // 只统计不落盘（`--out` 缺省）
    }
    std::string out;
    // ---- 第一段：全部 decision 行（按发生顺序、跨小局连续）----
    for (const DecisionRow &r : decisions_) {
        std::string o = "{\"type\":\"decision\",\"game\":";
        o += std::to_string(gameIndex_);
        o += ",\"hand_no\":";
        o += std::to_string(r.handNo);
        o += ",\"hand\":";
        jsonStr(o, r.handKey);
        o += ",\"step\":";
        o += std::to_string(r.step);
        o += ",\"seat\":";
        o += std::to_string(r.seat);
        o += ",\"policy\":";
        jsonStr(o, r.policy);
        o += ",\"kind\":";
        jsonStr(o, r.kind);
        o += ",\"legal\":";
        jsonStrArray(o, r.legal);
        o += ",\"chosen\":";
        jsonStr(o, r.chosen);
        o += ",\"chosen_index\":";
        o += std::to_string(r.chosenIndex);
        o += ",\"obs\":";
        o += r.obsJson;
        // ---- 事后回填：本小局的奖励（小局结束时才知道）----
        if (r.handIdx >= 0 && r.handIdx < static_cast<int>(hands_.size())) {
            const HandRow &h = hands_[static_cast<size_t>(r.handIdx)];
            o += ",\"hand_delta\":";
            jsonIntArray(o, h.delta.data(), 4);
            o += ",\"hand_winner\":";
            o += std::to_string(h.winner);
            o += ",\"hand_loser\":";
            o += std::to_string(h.loser);
            o += ",\"hand_agari\":";
            jsonBool(o, h.agari);
        }
        // ---- 事后回填：整场的顺位（整场结束时才知道）----
        o += ",\"final_scores\":";
        jsonIntArray(o, finalScores.data(), 4);
        o += ",\"placement\":";
        jsonIntArray(o, placement.data(), 4);
        o += "}\r\n";
        out += o;
    }
    // ---- 第二段：全部 hand 行（按 `round_end` 顺序）----
    for (const HandRow &h : hands_) {
        std::string o = "{\"type\":\"hand\",\"game\":";
        o += std::to_string(gameIndex_);
        o += ",\"hand_no\":";
        o += std::to_string(h.handNo);
        o += ",\"hand\":";
        jsonStr(o, h.handKey);
        o += ",\"round\":";
        o += roundJson(h.roundWind, h.kyoku, h.honba, h.sticks);
        o += ",\"scores_after\":";
        jsonIntArray(o, h.scoresAfter.data(), 4);
        o += ",\"delta\":";
        jsonIntArray(o, h.delta.data(), 4);
        o += ",\"agari\":";
        jsonBool(o, h.agari);
        o += ",\"abortive\":";
        jsonBool(o, h.abortive);
        o += ",\"reason\":";
        jsonStr(o, h.reason);
        o += ",\"renchan\":";
        jsonBool(o, h.renchan);
        o += ",\"winner\":";
        o += std::to_string(h.winner);
        o += ",\"loser\":";
        o += std::to_string(h.loser);
        o += ",\"tsumo\":";
        jsonBool(o, h.tsumo);
        o += ",\"nagashi\":";
        jsonBool(o, h.nagashi);
        o += ",\"tenpai\":";
        if (h.hasResult) {
            jsonBoolArray(o, h.tenpai.data(), 4);
        } else {
            o += "[]";
        }
        o += "}\r\n";
        out += o;
    }
    // ---- 第三段：恰好一行 game 行 ----
    {
        std::string o = "{\"type\":\"game\",\"game\":";
        o += std::to_string(gameIndex_);
        o += ",\"seed\":";
        o += std::to_string(seed_);
        o += ",\"policies\":[";
        for (int i = 0; i < 4; i++) {
            if (i > 0) {
                o.push_back(',');
            }
            jsonStr(o, labels_[static_cast<size_t>(i)]);
        }
        o += "],\"start_score\":";
        o += std::to_string(startScore_);
        o += ",\"hands\":";
        o += std::to_string(hands_.size());
        o += ",\"decisions\":";
        o += std::to_string(decisions_.size());
        o += ",\"sampled_every\":";
        o += std::to_string(sampleEvery_);
        o += ",\"final_scores\":";
        jsonIntArray(o, finalScores.data(), 4);
        o += ",\"placement\":";
        jsonIntArray(o, placement.data(), 4);
        o += "}\r\n";
        out += o;
    }
    std::error_code ec;
    std::filesystem::create_directories(dir_, ec);
    const std::string path = dir_ + "/g" + std::to_string(gameIndex_) + ".jsonl";
    std::FILE *f = std::fopen(path.c_str(), "wb");     // ⚠ 二进制模式：行尾由我们自己写 CRLF
    if (f == nullptr) {
        return;
    }
    std::fwrite(out.data(), 1, out.size(), f);
    std::fclose(f);
}

}  // namespace trainer
