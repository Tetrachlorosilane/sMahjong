#include "selfplay.hpp"

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <string>
#include <thread>
#include <vector>

#include "jsonw.hpp"
#include "policies.hpp"
#include "roundscoring.hpp"
#include "rules.hpp"
#include "seed.hpp"
#include "table.hpp"
#include "trace.hpp"

namespace trainer {
namespace {

struct PolicyStat {
    int seatHands = 0;
    int games = 0;
    long long placeSum = 0;
    int wins = 0;
    int dealIns = 0;
    long long deltaSum = 0;
    long long winScoreSum = 0;
    double rankPointSum = 0;

    double avgPlace() const { return games == 0 ? 0 : static_cast<double>(placeSum) / games; }
    double avgRankPoints() const {
        return games == 0 ? 0 : rankPointSum / games;
    }
    double winRate() const {
        return seatHands == 0 ? 0 : static_cast<double>(wins) / seatHands;
    }
    double dealInRate() const {
        return seatHands == 0 ? 0 : static_cast<double>(dealIns) / seatHands;
    }
    double avgDelta() const {
        return seatHands == 0 ? 0 : static_cast<double>(deltaSum) / seatHands;
    }
    double avgWinScore() const {
        return wins == 0 ? 0 : static_cast<double>(winScoreSum) / wins;
    }
};

/** 自对弈配置（Java `SelfPlay.Config` 的子集）。 */
struct Config {
    int games = 1;
    int64_t seedBase = 20260101;
    int workers = 0;
    std::vector<std::string> seatPolicy{"teacher", "teacher", "teacher", "teacher"};
    bool rotatePolicies = false;
    std::string preset;
    std::string outDir;
    int sampleEvery = 1;
    bool recordClaims = true;
    int maxHands = 0;
    bool teacherLabel = false;
};

struct GameRow {
    int decisions = 0;
    int ryukyoku = 0;
    std::array<int, 4> finalScores{};
    std::array<int, 4> placement{};
    std::array<double, 4> rankPoints{};
    std::array<std::string, 4> labels{};
    std::vector<HandRow> hands;
};

struct Summary {
    int games = 0;
    int hands = 0;
    int decisions = 0;
    int ryukyokuHands = 0;
    double seconds = 0;
    int64_t seedBase = 0;
    int workers = 1;
    std::vector<std::pair<std::string, PolicyStat>> byPolicy;
    std::vector<std::string> perGame;      // 每场一行的 JSON（含种子与四家顺位）

    double gamesPerSecond() const { return seconds <= 0 ? 0 : games / seconds; }
    double decisionsPerSecond() const { return seconds <= 0 ? 0 : decisions / seconds; }
    double ryukyokuRate() const {
        return hands == 0 ? 0 : static_cast<double>(ryukyokuHands) / hands;
    }
    double handsPerGame() const {
        return games == 0 ? 0 : static_cast<double>(hands) / games;
    }
};

/** Java `Math.round(double)`：`floor(x + 0.5)` 取长整型（**不是** C 的 round 半远离零）。 */
inline long long javaRound(double x) { return static_cast<long long>(std::floor(x + 0.5)); }

/** 顺位点 → 数组（**按 0.1 分取整**：与界面显示精度一致，也避免浮点噪声写进数据集）。 */
std::string rankPointList(const std::array<double, 4> &pts) {
    std::string o = "[";
    for (int i = 0; i < 4; i++) {
        if (i > 0) {
            o.push_back(',');
        }
        jsonDouble(o, static_cast<double>(javaRound(pts[static_cast<size_t>(i)] * 10)) / 10.0);
    }
    o.push_back(']');
    return o;
}

/** 单场（Java `SelfPlay.oneGame`）。 */
GameRow oneGame(const Config &c, const std::vector<PolicyFactory> &factories, int g,
                const std::string &outDir, std::string &fatal) {
    const int64_t seed = seedFor(c.seedBase, g);
    Rules rules;                                  // `Rules.defaults()` = mleague 预设
    if (!c.preset.empty()) {
        rules.applyPreset(c.preset);
    }
    Table t(rules);
    t.debugMaxHands = c.maxHands;
    t.seedBase = seed;
    std::array<std::string, 4> labels{};
    for (int i = 0; i < 4; i++) {
        const int src = (i + (c.rotatePolicies ? g : 0)) % 4;
        labels[static_cast<size_t>(i)]
            = c.seatPolicy[static_cast<size_t>(src) % c.seatPolicy.size()];
        t.policy[static_cast<size_t>(i)]
            = factories[static_cast<size_t>(src)](i, seed);   // 每场每席一份实例
        t.addBot(i);
    }
    const bool keepDecisions = !outDir.empty();
    TraceRecorder rec(g, seed, outDir, labels, rules.startScore, c.sampleEvery, c.recordClaims,
                      keepDecisions);
    t.debugChoiceTap = [&rec](const Observation &obs, const std::string &kind, const Action &cmd,
                              const std::string &roundKey) {
        rec.onChoice(obs, kind, cmd, roundKey);
    };
    t.debugEventTap = [&rec](const RoundEndEvent &ev) { rec.onRoundEnd(ev); };
    t.playGame();
    std::array<int, 4> finalScores{};
    for (int i = 0; i < 4; i++) {
        finalScores[static_cast<size_t>(i)] = t.seat(i).score;
    }
    rec.finish(finalScores);
    if (!t.fatal.empty() && fatal.empty()) {
        fatal = t.fatal;
    }

    GameRow row;
    row.labels = labels;
    row.hands = rec.handRows();
    row.decisions = rec.decisionCount();
    for (const HandRow &h : row.hands) {
        if (!h.agari) {
            row.ryukyoku++;
        }
    }
    row.finalScores = finalScores;
    row.placement = placementOf(finalScores);
    // 顺位点：**复用生产的精算**（同点拆分/马点/头名赏一把尺子），不在这里另写一份公式
    const Settlement st = settle(finalScores, rules);
    row.rankPoints = st.point;
    return row;
}

Summary run(const Config &c, std::string &fatal) {
    const int games = c.games < 0 ? 0 : c.games;
    // 并行度钳制与 Java `SelfPlay.run` **同一个式子**：`max(1, min(workers, max(1, games)))`。
    // ⚠ `--workers` 缺省 / 0 = **1**（C++ 侧的既定口径：不按核数偷偷并发 —— 同一台机器上
    //    `--workers` 省略时跑得跟以前一样；Java 的缺省是 `availableProcessors()`）。
    const int workers = c.workers > 0
            ? (c.workers < (games > 0 ? games : 1) ? c.workers : (games > 0 ? games : 1))
            : 1;
    std::vector<PolicyFactory> factories(4);
    std::string err;
    if (c.seatPolicy.empty()) {
        fatal = "--policy 不能是空串（Java 那边 `split(\",\")` 也不会给出空数组）";
        return Summary{};
    }
    for (int i = 0; i < 4; i++) {
        factories[static_cast<size_t>(i)]
            = policyFactoryByName(c.seatPolicy[static_cast<size_t>(i) % c.seatPolicy.size()], err);
        if (!factories[static_cast<size_t>(i)]) {
            fatal = err;
            return Summary{};
        }
    }
    std::error_code ec;
    if (!c.outDir.empty()) {
        std::filesystem::create_directories(c.outDir, ec);
    }
    const int slots = games > 0 ? games : 1;
    std::vector<GameRow> rows(static_cast<size_t>(slots));
    // 每场的失败原因单独收（**不共享字符串**）：合并时按 g 顺序取第一个，
    // 所以"哪一场先报错"与调度无关 —— 并行与串行的报错文本也一致。
    std::vector<std::string> rowFatal(static_cast<size_t>(slots));
    const auto t0 = std::chrono::steady_clock::now();
    // ⚠ 并行是**纯调度**：每场的种子只与 `(seedBase, g)` 有关（`seedFor`），每场每席的策略实例
    //   也是 `create(seat, gameSeed)` 现造 → 场与场之间没有任何共享可变状态（共享的只有向听表，
    //   它在 `shanten.cpp` 里按槽加锁后只写一次、之后只读）。所以"谁先跑哪一场"不影响内容：
    //   `g<g>.jsonl` 与串行时**逐字节相同**（判据见 docs/TRAINER-CPP.md 的 M4 并行一节）。
    //   结果合并（summary / by_policy / per_game）**全部在 join 之后按 g 升序做** ——
    //   浮点求和顺序、`by_policy` 的插入序、`per_game` 的行序因此都与串行一致。
    std::vector<std::thread> pool;
    pool.reserve(static_cast<size_t>(workers));
    std::atomic<int> next{0};
    for (int w = 0; w < workers; w++) {
        pool.emplace_back([&]() {
            int g;
            while ((g = next.fetch_add(1)) < games) {
                rows[static_cast<size_t>(g)]
                        = oneGame(c, factories, g, c.outDir, rowFatal[static_cast<size_t>(g)]);
            }
        });
    }
    for (std::thread &t : pool) {
        t.join();
    }
    for (int g = 0; g < games; g++) {
        if (!rowFatal[static_cast<size_t>(g)].empty()) {
            fatal = rowFatal[static_cast<size_t>(g)];
            return Summary{};
        }
    }
    const double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();

    Summary s;
    s.games = games;
    s.seconds = sec;
    s.seedBase = c.seedBase;
    s.workers = workers;
    for (const std::string &name : c.seatPolicy) {
        bool seen = false;
        for (const auto &e : s.byPolicy) {
            if (e.first == name) {
                seen = true;
                break;
            }
        }
        if (!seen) {
            s.byPolicy.emplace_back(name, PolicyStat{});
        }
    }
    for (int g = 0; g < games; g++) {
        const GameRow &row = rows[static_cast<size_t>(g)];
        s.decisions += row.decisions;
        s.ryukyokuHands += row.ryukyoku;
        s.hands += static_cast<int>(row.hands.size());
        {
            std::string o = "{\"game\":";
            o += std::to_string(g);
            o += ",\"seed\":";
            o += std::to_string(seedFor(c.seedBase, g));
            o += ",\"policies\":[";
            for (int i = 0; i < 4; i++) {
                if (i > 0) {
                    o.push_back(',');
                }
                jsonStr(o, row.labels[static_cast<size_t>(i)]);
            }
            o += "],\"final_scores\":";
            jsonIntArray(o, row.finalScores.data(), 4);
            o += ",\"placement\":";
            jsonIntArray(o, row.placement.data(), 4);
            o += ",\"rank_points\":";
            o += rankPointList(row.rankPoints);
            o += ",\"hands\":";
            o += std::to_string(row.hands.size());
            o += ",\"ryukyoku\":";
            o += std::to_string(row.ryukyoku);
            o.push_back('}');
            s.perGame.push_back(o);
        }
        // 顺位按策略标签累计（顺位点按**座位**索引）
        for (int i = 0; i < 4; i++) {
            for (auto &e : s.byPolicy) {
                if (e.first == row.labels[static_cast<size_t>(i)]) {
                    e.second.games++;
                    e.second.placeSum += row.placement[static_cast<size_t>(i)];
                    e.second.rankPointSum += row.rankPoints[static_cast<size_t>(i)];
                    break;
                }
            }
        }
        // 逐手把"和了 / 放铳 / 收支"记到对应策略上
        for (const HandRow &h : row.hands) {
            for (int i = 0; i < 4; i++) {
                for (auto &e : s.byPolicy) {
                    if (e.first != row.labels[static_cast<size_t>(i)]) {
                        continue;
                    }
                    PolicyStat &st = e.second;
                    st.seatHands++;
                    const int d = h.delta[static_cast<size_t>(i)];
                    st.deltaSum += d;
                    if (h.winner == i) {
                        st.wins++;
                        st.winScoreSum += d;
                    }
                    if (h.loser == i) {
                        st.dealIns++;
                    }
                    break;
                }
            }
        }
    }
    return s;
}

/** 汇总 → JSON（`SelfPlay.toJson` 的等价物，键序一致）。 */
std::string summaryJson(const Summary &s) {
    std::string o = "{\"games\":";
    o += std::to_string(s.games);
    o += ",\"hands\":";
    o += std::to_string(s.hands);
    o += ",\"decisions\":";
    o += std::to_string(s.decisions);
    o += ",\"seconds\":";
    jsonDouble(o, s.seconds);
    o += ",\"seed_base\":";
    o += std::to_string(s.seedBase);
    o += ",\"workers\":";
    o += std::to_string(s.workers);
    o += ",\"hands_per_game\":";
    jsonDouble(o, s.handsPerGame());
    o += ",\"ryukyoku_rate\":";
    jsonDouble(o, s.ryukyokuRate());
    o += ",\"games_per_second\":";
    jsonDouble(o, s.gamesPerSecond());
    o += ",\"decisions_per_second\":";
    jsonDouble(o, s.decisionsPerSecond());
    o += ",\"by_policy\":{";
    for (size_t i = 0; i < s.byPolicy.size(); i++) {
        if (i > 0) {
            o.push_back(',');
        }
        jsonStr(o, s.byPolicy[i].first);
        const PolicyStat &st = s.byPolicy[i].second;
        o += ":{\"games\":";
        o += std::to_string(st.games);
        o += ",\"seat_hands\":";
        o += std::to_string(st.seatHands);
        o += ",\"avg_place\":";
        jsonDouble(o, st.avgPlace());
        o += ",\"avg_rank_points\":";
        jsonDouble(o, st.avgRankPoints());
        o += ",\"win_rate\":";
        jsonDouble(o, st.winRate());
        o += ",\"deal_in_rate\":";
        jsonDouble(o, st.dealInRate());
        o += ",\"avg_delta\":";
        jsonDouble(o, st.avgDelta());
        o += ",\"avg_win_score\":";
        jsonDouble(o, st.avgWinScore());
        o.push_back('}');
    }
    o += "},\"per_game\":[";
    for (size_t i = 0; i < s.perGame.size(); i++) {
        if (i > 0) {
            o.push_back(',');
        }
        o += s.perGame[i];
    }
    o += "]}";
    return o;
}

void writeSummary(const Summary &s, const std::string &outDir) {
    if (outDir.empty()) {
        return;
    }
    std::error_code ec;
    std::filesystem::create_directories(outDir, ec);
    const std::string path = outDir + "/summary.json";
    std::FILE *f = std::fopen(path.c_str(), "wb");     // 无行尾（Java `Files.writeString`）
    if (f == nullptr) {
        return;
    }
    const std::string text = summaryJson(s);
    std::fwrite(text.data(), 1, text.size(), f);
    std::fclose(f);
}

/** 人类可读汇总（Java `SelfPlay.format` 的等价物；不参与对拍）。 */
void printFormat(const Summary &s) {
    std::printf("== 自对弈汇总：%d 场 / %d 小局 / %.0f 决策/场，用时 %.1fs（%.2f 场/秒，%.0f 决策/秒），workers=%d\n",
                s.games, s.hands, s.games == 0 ? 0.0 : static_cast<double>(s.decisions) / s.games,
                s.seconds, s.gamesPerSecond(), s.decisionsPerSecond(), s.workers);
    std::printf("   流局率 %.1f%%   平均每场 %.1f 小局\n", 100 * s.ryukyokuRate(), s.handsPerGame());
    std::printf("%-10s %6s %10s %11s %9s %9s %10s %10s\n", "策略", "场数", "平均顺位",
                "平均顺位点", "和了率", "放铳率", "平均收支", "平均打点");
    for (const auto &e : s.byPolicy) {
        const PolicyStat &st = e.second;
        std::printf("%-10s %6d %10.3f %11.2f %8.1f%% %8.1f%% %10.0f %10.0f\n", e.first.c_str(),
                    st.games, st.avgPlace(), st.avgRankPoints(), 100 * st.winRate(),
                    100 * st.dealInRate(), st.avgDelta(), st.avgWinScore());
    }
}

/** `selfplay` 自己的用法（`--help` 时打；顶层 `trainer` 无参时打的是更简略的一份）。 */
void selfplayUsage(std::FILE *out) {
    std::fprintf(out,
                 "用法：trainer selfplay <games> [--workers K] [--policy P] [--seed S] [--hands H]\n"
                 "                                [--rotate] [--sample K] [--no-claims] [--preset NAME]\n"
                 "                                [--out DIR]\n"
                 "  --workers K   并行工作线程数：K 个线程从 g=0..games-1 里抢场号，各写各的 g<g>.jsonl；\n"
                 "                **缺省 / 0 = 1**（不按核数自动并发），钳制到 [1, games]，与 Java 同式\n"
                 "                `max(1, min(workers, max(1, games)))`。并行只改调度：结果汇总在\n"
                 "                join 之后按 g 升序合并，所以 `g*.jsonl` 与 `--workers 1` **逐字节相同**\n"
                 "                （summary.json 的 `workers` 字段是钳制后的线程数）。\n"
                 "  --policy P    四家策略，逗号分隔（pass|first|random）；缺省 teacher（训练端未实现）\n"
                 "  --seed S      基准种子（缺省 20260101）；每场种子只与 (S, 场号) 有关\n"
                 "  --hands H     每场最多 H 小局（0 = 完整半庄）；缺省 0\n"
                 "  --rotate      按场轮转座位\n"
                 "  --sample K    每 K 次决策记 1 条（缺省 1 = 全记）\n"
                 "  --no-claims   不记录鸣牌决策\n"
                 "  --preset X    规则预设（mleague|tenhou|majsoul|custom）；缺省 mleague\n"
                 "  --out DIR     轨迹输出目录（g<场号>.jsonl + summary.json）\n");
}

}  // namespace

int selfplayCli(int argc, char **argv) {
    // argv[0] = "selfplay"，argv[1] = 场数
    Config c;
    int i = 1;
    if (i < argc && argv[i][0] != '-') {
        c.games = std::atoi(argv[i++]);
    }
    bool teacherLabel = false;
    for (; i < argc; i++) {
        const std::string a = argv[i];
        auto next = [&](const char *what) -> const char * {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "[trainer] %s 需要一个参数\n", what);
                std::exit(2);
            }
            return argv[++i];
        };
        if (a == "--workers") {
            c.workers = std::atoi(next("--workers"));
        } else if (a == "--policy") {
            const std::string p = next("--policy");
            c.seatPolicy.clear();
            size_t start = 0;
            while (true) {
                const size_t pos = p.find(',', start);
                if (pos == std::string::npos) {
                    c.seatPolicy.push_back(p.substr(start));
                    break;
                }
                c.seatPolicy.push_back(p.substr(start, pos - start));
                start = pos + 1;
            }
        } else if (a == "--seed") {
            c.seedBase = std::strtoll(next("--seed"), nullptr, 10);
        } else if (a == "--hands") {
            c.maxHands = std::atoi(next("--hands"));
        } else if (a == "--out") {
            c.outDir = next("--out");
        } else if (a == "--rotate") {
            c.rotatePolicies = true;
        } else if (a == "--sample") {
            c.sampleEvery = std::atoi(next("--sample"));
        } else if (a == "--no-claims") {
            c.recordClaims = false;
        } else if (a == "--preset") {
            c.preset = next("--preset");
        } else if (a == "--teacher-label") {
            teacherLabel = true;
        } else if (a == "--help" || a == "-h") {
            selfplayUsage(stdout);
            return 0;
        } else {
            std::fprintf(stderr, "[trainer] 未知参数：%s\n", a.c_str());
            return 2;
        }
    }
    if (teacherLabel) {
        // Java 有 `--teacher-label`（DAgger）：它要跑一次 `Bot.decide`，而 teacher 还没移植。
        // ⚠ 显式报错，**不静默降级**（AGENTS §6.5：能力缺失要报错）。
        std::fprintf(stderr, "[trainer] --teacher-label 需要内置 teacher（Bot.decide），"
                             "训练端这一轮还没实现（docs/TRAINER-CPP.md §5 M3）\n");
        return 2;
    }
    std::string fatal;
    const Summary s = run(c, fatal);
    if (!fatal.empty()) {
        std::fprintf(stderr, "[trainer] 自对弈失败：%s\n", fatal.c_str());
        return 2;
    }
    printFormat(s);
    writeSummary(s, c.outDir);
    if (!c.outDir.empty()) {
        std::printf("轨迹目录：%s\n", c.outDir.c_str());
    }
    return 0;
}

}  // namespace trainer
