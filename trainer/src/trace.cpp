#include "trace.hpp"

#include <algorithm>
#include <cstdio>
#include <filesystem>
#include <string>
#include <vector>

#include "jsonw.hpp"
#include "npzwriter.hpp"
#include "obffeatures.hpp"
#include "round.hpp"
#include "seed.hpp"
#include "shanten.hpp"

namespace trainer {
namespace {

/** 标签侧文件格式版本（字段增删要 +1；Python 侧同一条判据在 `v4/dataset.py`）。 */
constexpr int kAuxVersion = 1;

/** 动作键的"类型"部分（`discard:1m` → `discard`；无冒号则整串）。 */
std::string keyType(const std::string &key) {
    const size_t pos = key.find(':');
    return pos == std::string::npos ? key : key.substr(0, pos);
}

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
                             const std::string &roundKey, const Round &round) {
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
    if (auxEnabled_) {
        // 标签侧与决策行**同序同长**（采样/`--no-claims` 之后才追加，与 Java 侧同一位置）
        AuxRow ar;
        ar.seat = obs.seat;
        ar.handIdx = row.handIdx;
        FeatureView v;
        JVal obsJson;
        if (jsonParse(row.obsJson, obsJson) && featureViewOfObs(obsJson, v)) {
            const std::string type = keyType(chosen);
            const bool usesCandidate = (type == "discard" || type == "riichi" || type == "chi"
                                        || type == "pon" || type == "kan");
            // ⚠ 与 Java 逐字同口径：候选分支用 perCandidate 的**原始**向听判听牌、只对落盘值夹到 0；
            //   非候选分支（和了/过）手上没变，用当前向听。
            const int sh = usesCandidate ? perCandidate(v, chosen)[0]
                                         : shantenMin(v.hand, static_cast<int>(v.melds.size()));
            ar.ownShantenAfter = std::max(0, sh);
            ar.ownTenpai = sh <= 0 ? 1 : 0;
        }
        // 真·上帝视角：三家对手（相对方位 0=下家/1=対面/2=上家）的暗牌计数与听牌
        for (int j = 0; j < kSeatCount; j++) {
            const int s = (ar.seat + 1 + j) % 4;
            const Counts c = round.concealCounts(s);
            for (int k = 0; k < kKindCount; k++) {
                ar.oppHand[static_cast<size_t>(j * kKindCount + k)]
                        = static_cast<uint8_t>(c[static_cast<size_t>(k)]);
            }
            ar.oppTenpai[static_cast<size_t>(j)] = round.waitKinds(s).empty() ? 0 : 1;
        }
        aux_.push_back(ar);
    }
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
    // 标签侧回填（Java `TraceRecorder.onEvent` 同一件事）：放铳 / 和了 / 本小局收支
    if (auxEnabled_) {
        const HandRow &h = hands_.back();
        for (AuxRow &ar : aux_) {
            if (ar.handIdx != static_cast<int>(hands_.size()) - 1) {
                continue;
            }
            for (int j = 0; j < kSeatCount; j++) {
                const int opp = (ar.seat + 1 + j) % 4;
                ar.oppDealin[static_cast<size_t>(j)] = (h.winner == opp && h.loser == ar.seat) ? 1 : 0;
            }
            ar.winFlag = (h.winner == ar.seat) ? 1 : 0;
            ar.handDelta = (ar.seat >= 0 && ar.seat < 4) ? h.delta[static_cast<size_t>(ar.seat)] : 0;
        }
    }
}

void TraceRecorder::finish(const std::array<int, 4> &finalScores) {
    const std::array<int, 4> placement = placementOf(finalScores);
    // 标签侧的顺位回填（Java `finish` 里那一段）：整场结束才知道
    for (AuxRow &ar : aux_) {
        ar.placement = (ar.seat >= 0 && ar.seat < 4)
                ? placement[static_cast<size_t>(ar.seat)] : 0;
    }
    if (dir_.empty()) {
        return;                            // 只统计不落盘（`--out` 缺省）
    }
    if (auxEnabled_) {
        writeAux();
    }
    // 逐决策 reward-to-go（点）：`R(h,s) = Σ_{h' ≥ h} delta[h'][s] + 终局余棒[s]`。
    // 口径必须与 Java `TraceRecorder.rewardToGo` **逐字一致**（同种子两份轨迹逐字节相同）：
    //   ① 含本小局（打出去就结算了）；② 加终局余棒（末局结算后供託那批立直棒归末局 1 位）；
    //   ③ 于是第 0 小局 == `final_scores − 起点`（= 值头旧口径的 value），末局 == 本局收支 + 余棒。
    int maxNo = -1;
    for (const HandRow &h : hands_) {
        maxNo = std::max(maxNo, h.handNo);
    }
    std::vector<std::array<int, 4>> rtg(static_cast<size_t>(maxNo + 1), std::array<int, 4>{});
    std::array<int, 4> bonus{};
    if (!hands_.empty()) {
        for (int s = 0; s < 4; s++) {
            bonus[static_cast<size_t>(s)] = finalScores[static_cast<size_t>(s)]
                    - hands_.back().scoresAfter[static_cast<size_t>(s)];
        }
    }
    std::array<int, 4> acc{};
    for (int h = static_cast<int>(hands_.size()) - 1; h >= 0; h--) {
        const HandRow &hr = hands_[static_cast<size_t>(h)];
        for (int s = 0; s < 4; s++) {
            acc[static_cast<size_t>(s)] += hr.delta[static_cast<size_t>(s)];
            rtg[static_cast<size_t>(hr.handNo)][static_cast<size_t>(s)] =
                    acc[static_cast<size_t>(s)] + bonus[static_cast<size_t>(s)];
        }
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
        // ---- 事后回填：逐决策回报（整场结束才知道；键序与 Java 一致：在 hand_agari 之后）----
        o += ",\"reward_to_go\":";
        if (r.handNo >= 0 && r.handNo <= maxNo && r.seat >= 0 && r.seat < 4) {
            o += std::to_string(rtg[static_cast<size_t>(r.handNo)][static_cast<size_t>(r.seat)]);
        } else {
            o += "0";
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

/**
 * 落盘 `g<n>.aux.npz`（Java `TraceRecorder.writeAux` 的镜像）。
 *
 * <p>形状与 trace 的决策行**一一对应**（同序同长）：`(n,)` / `(n,3)` / `(n,3,34)`。
 * `meta` 里带版本与溯源（aux/obs 版本、种子、场号、策略串）—— 版本不符时读侧直接报错。
 *
 * <p>⚠ 标签与输入是两套文件：推理路径永不读它（`FEATURES-V4.md` §5.2 的硬闸门）。
 */
void TraceRecorder::writeAux() {
    const size_t n = aux_.size();
    std::string ownShanten(n, '\0');
    std::string ownTenpai(n, '\0');
    std::string winFlag(n, '\0');
    std::string placement(n, '\0');
    std::string oppTenpai(n * kSeatCount, '\0');
    std::string oppDealin(n * kSeatCount, '\0');
    std::string oppHand(n * kSeatCount * kKindCount, '\0');
    std::vector<int> handDelta(n, 0);
    for (size_t i = 0; i < n; i++) {
        const AuxRow &r = aux_[i];
        ownShanten[i] = static_cast<char>(r.ownShantenAfter);
        ownTenpai[i] = static_cast<char>(r.ownTenpai);
        winFlag[i] = static_cast<char>(r.winFlag);
        placement[i] = static_cast<char>(r.placement);
        handDelta[i] = r.handDelta;
        for (int j = 0; j < kSeatCount; j++) {
            oppTenpai[i * kSeatCount + static_cast<size_t>(j)]
                    = static_cast<char>(r.oppTenpai[static_cast<size_t>(j)]);
            oppDealin[i * kSeatCount + static_cast<size_t>(j)]
                    = static_cast<char>(r.oppDealin[static_cast<size_t>(j)]);
            for (int k = 0; k < kKindCount; k++) {
                oppHand[(i * kSeatCount + static_cast<size_t>(j)) * kKindCount
                        + static_cast<size_t>(k)]
                        = static_cast<char>(r.oppHand[static_cast<size_t>(j * kKindCount + k)]);
            }
        }
    }
    std::string meta = "{\"aux_version\":";
    meta += std::to_string(kAuxVersion);
    meta += ",\"obs_version\":";
    meta += std::to_string(kObservationVersion);
    meta += ",\"game\":";
    meta += std::to_string(gameIndex_);
    meta += ",\"seed\":";
    meta += std::to_string(seed_);
    meta += ",\"n\":";
    meta += std::to_string(n);
    meta += ",\"policies\":[";
    for (int i = 0; i < 4; i++) {
        if (i > 0) {
            meta.push_back(',');
        }
        jsonStr(meta, labels_[static_cast<size_t>(i)]);
    }
    meta += "],\"note\":";
    jsonStr(meta, "labels only; inference must read g*.feat.bin, never this file");
    meta.push_back('}');
    NpzWriter npz;
    npz.putBytes("own_shanten_after", "<i1", {static_cast<int>(n)}, ownShanten);
    npz.putBytes("own_tenpai", "<u1", {static_cast<int>(n)}, ownTenpai);
    npz.putBytes("opp_tenpai", "<u1", {static_cast<int>(n), kSeatCount}, oppTenpai);
    npz.putBytes("opp_hand", "<u1", {static_cast<int>(n), kSeatCount, kKindCount}, oppHand);
    npz.putBytes("opp_dealin", "<u1", {static_cast<int>(n), kSeatCount}, oppDealin);
    npz.putBytes("win_flag", "<u1", {static_cast<int>(n)}, winFlag);
    npz.putI32("hand_delta", handDelta, {static_cast<int>(n)});
    npz.putBytes("placement", "<u1", {static_cast<int>(n)}, placement);
    npz.putJson("meta", meta);
    npz.write(dir_ + "/g" + std::to_string(gameIndex_) + ".aux.npz");
}

}  // namespace trainer
