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
    /** `--rotate-perm`：按场号枚举 **24 个全排列** 分配座位（`--rotate` 的循环移位只覆盖 4 个排列、
     *  且保持循环序 ⇒ 一代之内各策略相对彼此的**方位固定**）。与 `--rotate` 同时给时 **perm 优先**。 */
    bool rotatePerm = false;
    std::string preset;
    std::string outDir;
    int sampleEvery = 1;
    bool recordClaims = true;
    int maxHands = 0;
    bool teacherLabel = false;
    /** `--aux`：额外落标签侧 `g*.aux.npz`（对手手牌/听牌、放铳、和了、顺位）。 */
    bool aux = false;
    // ---- 确定批前向（opt-in；缺省全关 ⇒ 旧路径逐位不变，见 `batchforward.hpp`） ----
    /** `--batch-forward K`：K > 0 才开；批组成 = 屏障时刻未决键里最小的 K 个。 */
    int batchForward = 0;
    /** `--batch-games L`：同时在场（= 开几条 worker 线程）的场数，缺省 = K。 */
    int batchGames = 0;
    /** `--batch-tail keep|drop|error`（缺省 keep，与 `tools/v4-batch-serve.py` 同缺省）。 */
    std::string batchTail = "keep";
    /** `--batch-order canonical|arrival`：`arrival` 是**故意红的负向开关**。 */
    std::string batchOrder = "canonical";
    /** `--batch-plan <文件>`：批计划 + 指纹落盘（JSON）。 */
    std::string batchPlan;
};

struct GameRow {
    int decisions = 0;
    int ryukyoku = 0;
    std::array<int, 4> finalScores{};
    std::array<int, 4> placement{};
    std::array<double, 4> rankPoints{};
    /**
     * 每场每席的"风格轴"账（**下标 = 座位号**，与 `finalScores` / `placement` 同一套约定）。
     *
     * 为什么要 per-game 粒度：闸门对风格轴（打点 / 放铳率 / 和了率）做的是**逐场配对差分**
     * （`Δ = 候选 − 现任` 的 CI），而整份汇总的比率取不到"每一场每一席"的值 —— 没有它就只能
     * 拿两个独立汇总相减，那不是配对检验。
     *
     * ⚠ 口径与 `by_policy` 那张表**逐项相同**（累加后必须逐位相等）：
     *   · `wins` 只认 `h.winner == i`（多家荣和时 `winner` 只有离放铳者最近那家 —— 与表同）；
     *   · `winPoints` 累加 `h.delta[i]`，而表的 `avg_win_score` 累加的正是同一个数
     *     （收点 + 本场棒 + 供託一起算，别在这里另立"纯打点"口径，否则对不上表）；
     *   · `deals` / `dealPoints` 是同一本账的**镜像**：`h.loser == i` 的那些小局，付出 = `-h.delta[i]`。
     */
    std::array<int, 4> wins{};
    std::array<int, 4> winPoints{};
    std::array<int, 4> deals{};
    std::array<int, 4> dealPoints{};
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

/** `{0,1,2,3}` 的 24 个全排列，**按字典序**手写列全（`kPerm24[g % 24]`）。
 *  ⚠ 刻意不用运行时生成、也不用随机数：同种子必须逐位可复现，且 `g % 24` 的取值要一眼可查。
 *  ⚠ **不改变 `--rotate-perm` 缺省关闭时的任何行为**（那条路径不读这张表）。 */
static const int kPerm24[24][4] = {
    {0, 1, 2, 3}, {0, 1, 3, 2}, {0, 2, 1, 3}, {0, 2, 3, 1}, {0, 3, 1, 2}, {0, 3, 2, 1},
    {1, 0, 2, 3}, {1, 0, 3, 2}, {1, 2, 0, 3}, {1, 2, 3, 0}, {1, 3, 0, 2}, {1, 3, 2, 0},
    {2, 0, 1, 3}, {2, 0, 3, 1}, {2, 1, 0, 3}, {2, 1, 3, 0}, {2, 3, 0, 1}, {2, 3, 1, 0},
    {3, 0, 1, 2}, {3, 0, 2, 1}, {3, 1, 0, 2}, {3, 1, 2, 0}, {3, 2, 0, 1}, {3, 2, 1, 0},
};

/** 单场（Java `SelfPlay.oneGame`）。 */
GameRow oneGame(const Config &c, const std::vector<PolicyFactory> &factories, int g,
                const std::string &outDir, std::string &fatal) {
    const int64_t seed = seedFor(c.seedBase, g);
    Rules rules;                                  // `Rules.defaults()` = mleague 预设
    if (!c.preset.empty()) {
        rules.applyPreset(c.preset);
    }
    // 确定批：本场在场登记交给 **lane 作用域**（`--batch-games` 那条线程把分给自己的场
    // 全部跑完才销账，见 `batchforward.hpp` 的 `GameScope` —— 按"场"销账会让 180 场只跑出 32 场）。
    // 开关关着时不构造任何东西 ⇒ 旧路径逐位不变。
    Table t(rules);
    t.debugMaxHands = c.maxHands;
    t.seedBase = seed;
    std::array<std::string, 4> labels{};
    // 座位分配：`--rotate-perm` 时按 **场号枚举 24 个全排列**（一代之内 4! 种方位关系都平均到）；
    // 否则 **逐位保持旧口径**（`--rotate` 的循环移位 / 不轮转），历季结果要能原样复现。
    const int *perm = c.rotatePerm ? kPerm24[static_cast<size_t>(g % 24)] : nullptr;
    for (int i = 0; i < 4; i++) {
        const int src = perm ? perm[i] : (i + (c.rotatePolicies ? g : 0)) % 4;
        labels[static_cast<size_t>(i)]
            = c.seatPolicy[static_cast<size_t>(src) % c.seatPolicy.size()];
        t.policy[static_cast<size_t>(i)]
            = factories[static_cast<size_t>(src)](i, seed);   // 每场每席一份实例
        t.addBot(i);
    }
    const bool keepDecisions = !outDir.empty();
    TraceRecorder rec(g, seed, outDir, labels, rules.startScore, c.sampleEvery, c.recordClaims,
                      keepDecisions, c.aux);
    t.debugChoiceTap = [&rec](const Observation &obs, const std::string &kind, const Action &cmd,
                              const std::string &roundKey, const Round &round) {
        rec.onChoice(obs, kind, cmd, roundKey, round);
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
    // 每场每席的和了 / 放铳 / 打点账（`per_game[]` 的四个新数组）。
    // 唯一数据源就是小局行里的 `winner` / `loser` / `delta` —— 与 `run()` 里给 `by_policy`
    // 累计的那三分支**同一把尺子**，所以"按场累加 == 表里那一行"是构造出来的，不是巧合。
    for (const HandRow &h : row.hands) {
        for (int i = 0; i < 4; i++) {
            if (h.winner == i) {
                row.wins[static_cast<size_t>(i)]++;
                row.winPoints[static_cast<size_t>(i)] += h.delta[static_cast<size_t>(i)];
            }
            if (h.loser == i) {
                row.deals[static_cast<size_t>(i)]++;
                row.dealPoints[static_cast<size_t>(i)] += -h.delta[static_cast<size_t>(i)];
            }
        }
    }
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
    int workers = c.workers > 0
            ? (c.workers < (games > 0 ? games : 1) ? c.workers : (games > 0 ? games : 1))
            : 1;
    // 确定批：并发度**由批模式决定**（同时在场几条线程 = 收集器屏障的宽度），
    // 缺省 = K（K 越大越需要更多在场场数，否则每波都只能凑出短批）。
    if (c.batchForward > 0) {
        const int cap = games > 0 ? games : 1;
        const int want = c.batchGames > 0 ? c.batchGames : c.batchForward;
        workers = want < cap ? want : cap;
        if (workers < 1) {
            workers = 1;
        }
    }
    // 确定批的收集器（**建线程之前**装好；之后只读，见 `batchforward.hpp` 的单例约定）。
    std::unique_ptr<batchfw::Collector> collector;
    if (c.batchForward > 0) {
        if (c.batchOrder != "canonical" && c.batchOrder != "arrival") {
            fatal = "--batch-order 只认 canonical / arrival，收到 " + c.batchOrder;
            return Summary{};
        }
        if (c.batchTail != "keep" && c.batchTail != "drop" && c.batchTail != "error") {
            fatal = "--batch-tail 只认 keep / drop / error，收到 " + c.batchTail;
            return Summary{};
        }
        const batchfw::Tail tail = c.batchTail == "keep" ? batchfw::Tail::Keep
                : (c.batchTail == "drop" ? batchfw::Tail::Drop : batchfw::Tail::Error);
        const batchfw::Order order = c.batchOrder == "canonical" ? batchfw::Order::Canonical
                                                                 : batchfw::Order::Arrival;
        // 批路径只接 v4 权重：`net:` 的其它形态（teacher / pass / v3 权重）在批模式下没有键，
        // 会静默地少提交行 ⇒ 显式拒绝，不做"部分座位走批"的混合口径。
        for (const std::string &p : c.seatPolicy) {
            if (p.find("net:") == std::string::npos) {
                fatal = "批前向只接 `net:` 策略（v4 权重）；策略串里的 `" + p + "` 没有 net:";
                return Summary{};
            }
        }
        collector = std::make_unique<batchfw::Collector>(c.batchForward, order, tail);
        collector->setEngine(&batchfw::engineRowLoop);
        collector->setLive(workers);
        batchfw::g_collector = collector.get();
    }
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
    if (batchfw::active()) {
        // **确定批的调度**（唯一与旧路径不同的地方）：场号**静态轮转**分给 L 条线程
        // （线程 i 跑 i, i+L, i+2L, …）。为什么不用 `next.fetch_add` 抢号：谁先抢到下一场
        // 取决于**时序**，而"屏障时刻哪些场在场"直接决定批组成 ⇒ 抢号会让"同种子 ⇒ 同批计划"
        // 当场失效（判据④）。静态分配下"线程 i 在第 w 波跑哪一场"只由 g 与 L 决定 ⇒ 完全确定。
        // ⚠ 每条线程跑完一场**立刻**取下一场（不 reunite），所以存活场数在尾部会自然下降。
        // ⚠ **不许在这里另立一个块内局部量当步长**（`const int lanes = workers;`）：lambda 按引用
        //    捕获，而 `join()` 在本 `if` 块**之外** ⇒ 块一退出那个栈槽就失效，线程读到的是垃圾
        //    （实测 `lanes` 读成 232 ⇒ 每条 lane 只跑 1 场、180 场只出 32 场，且不报错）。
        //    所以步长直接用**函数作用域**的 `workers`。
        for (int w = 0; w < workers; w++) {
            pool.emplace_back([&, w]() {
                // lane 作用域：**整条 lane 退出时才销账**（不是每场销一次，见 `GameScope` 的注释）
                batchfw::GameScope lane;
                for (int g = w; g < games; g += workers) {
                    if (std::getenv("MAHJONG_BATCH_DEBUG") != nullptr) {
                        std::fprintf(stderr, "[batch-debug] lane=%d g=%d games=%d lanes=%d\n", w, g,
                                     games, workers);
                    }
                    lane.setGame(g);
                    rows[static_cast<size_t>(g)]
                            = oneGame(c, factories, g, c.outDir, rowFatal[static_cast<size_t>(g)]);
                }
            });
        }
    } else {
        for (int w = 0; w < workers; w++) {
            pool.emplace_back([&]() {
                int g;
                while ((g = next.fetch_add(1)) < games) {
                    rows[static_cast<size_t>(g)]
                            = oneGame(c, factories, g, c.outDir, rowFatal[static_cast<size_t>(g)]);
                }
            });
        }
    }
    for (std::thread &t : pool) {
        t.join();
    }
    // ---- 确定批：批计划 + 指纹（**所有 worker 已收工**，收集器只读） ----
    if (collector != nullptr) {
        const batchfw::Plan bp = collector->plan();
        std::printf("[batch] K=%d 场/波=%d tail=%s order=%s ⇒ %zu 批 / %d 行 / 尾批 %d 批"
                    "（drop 记账 %d 行）\n",
                    c.batchForward, workers, c.batchTail.c_str(), c.batchOrder.c_str(),
                    bp.batches.size(), bp.rows,
                    static_cast<int>(std::count_if(bp.batches.begin(), bp.batches.end(),
                                                   [](const batchfw::BatchRecord &b) {
                                                       return b.tail;
                                                   })),
                    bp.dropped);
        std::printf("[batch] 批计划指纹 = %s\n", bp.fingerprint.c_str());
        if (!bp.error.empty()) {
            std::fprintf(stderr, "[batch] 守卫/前向错误：%s\n", bp.error.c_str());
        }
        if (!c.batchPlan.empty()) {
            std::string o = "{\n \"K\": ";
            o += std::to_string(c.batchForward);
            o += ",\n \"lanes\": ";
            o += std::to_string(workers);
            o += ",\n \"tail\": ";
            o += jsonQuoted(c.batchTail);
            o += ",\n \"order\": ";
            o += jsonQuoted(c.batchOrder);
            o += ",\n \"games\": ";
            o += std::to_string(games);
            o += ",\n \"batches\": ";
            o += std::to_string(bp.batches.size());
            o += ",\n \"rows\": ";
            o += std::to_string(bp.rows);
            o += ",\n \"dropped\": ";
            o += std::to_string(bp.dropped);
            o += ",\n \"fingerprint\": ";
            o += jsonQuoted(bp.fingerprint);
            o += ",\n \"error\": ";
            o += jsonQuoted(bp.error);
            o += ",\n \"batch_keys\": [\n";
            for (size_t i = 0; i < bp.batches.size(); i++) {
                const batchfw::BatchRecord &b = bp.batches[i];
                o += "  {\"b\": " + std::to_string(b.index) + ", \"count\": "
                     + std::to_string(b.count) + ", \"tail\": " + (b.tail ? "true" : "false")
                     + ", \"keys\": [";
                for (size_t j = 0; j < b.keys.size(); j++) {
                    o += (j ? ", " : "") + jsonQuoted(batchfw::keyText(b.keys[j]));
                }
                o += "]}";
                o += (i + 1 == bp.batches.size() ? "\n" : ",\n");
            }
            o += " ]\n}\n";
            std::FILE *f = std::fopen(c.batchPlan.c_str(), "wb");
            if (f == nullptr) {
                std::fprintf(stderr, "[batch] 计划文件写不出来：%s\n", c.batchPlan.c_str());
            } else {
                std::fwrite(o.data(), 1, o.size(), f);
                std::fclose(f);
                std::printf("[batch] 计划 → %s\n", c.batchPlan.c_str());
            }
        }
        batchfw::g_collector = nullptr;
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
            // 四个风格轴数组（下标 = 座位号）：闸门按场配对做 CI 时读它们。
            // 键序只在这里定一次 —— 新键插在 `rank_points` 与 `hands` 之间（追加在尾部会打乱
            // "每场一行的可读性"，而这两个数正是同一件事的两个粒度：场级 vs 小局级）。
            o += ",\"wins\":";
            jsonIntArray(o, row.wins.data(), 4);
            o += ",\"win_points\":";
            jsonIntArray(o, row.winPoints.data(), 4);
            o += ",\"deals\":";
            jsonIntArray(o, row.deals.data(), 4);
            o += ",\"deal_points\":";
            jsonIntArray(o, row.dealPoints.data(), 4);
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
                 "                                [--rotate] [--rotate-perm] [--sample K] [--no-claims]\n"
                 "                                [--preset NAME]\n"
                 "                                [--out DIR]\n"
                 "  --workers K   并行工作线程数：K 个线程从 g=0..games-1 里抢场号，各写各的 g<g>.jsonl；\n"
                 "                **缺省 / 0 = 1**（不按核数自动并发），钳制到 [1, games]，与 Java 同式\n"
                 "                `max(1, min(workers, max(1, games)))`。并行只改调度：结果汇总在\n"
                 "                join 之后按 g 升序合并，所以 `g*.jsonl` 与 `--workers 1` **逐字节相同**\n"
                 "                （summary.json 的 `workers` 字段是钳制后的线程数）。\n"
                 "  --policy P    四家策略，逗号分隔（teacher|pass|first|random|net:<权重文件>[@α][#T]\n"
                 "                |net:<A>!net:<B>@<cond>）；域掩码形态 = cond 为真走 B、为假**逐位**走 A\n"
                 "                （cond 只读公开观测：kind / draw / opp_riichi 等，`&`/`|`/括号组合；\n"
                 "                 见 docs/PROTOCOL.md §8.4）\n"
                 "                缺省 teacher。⚠ `net:` 的 `@α`（teacher 先验）**没接** → 显式报错\n"
                 "                （见 docs/TRAINER-CPP.md §6.16 与 §6.17）\n"
                 "  --seed S      基准种子（缺省 20260101）；每场种子只与 (S, 场号) 有关\n"
                 "  --hands H     每场最多 H 小局（0 = 完整半庄）；缺省 0\n"
                 "  --rotate      按场轮转座位（循环移位：只覆盖 4 个排列、且**保持循环序** ⇒\n"
                 "                一代之内各策略相对彼此的**方位是固定的**）\n"
                 "  --rotate-perm 座位按 **24 个全排列**（字典序 `kPerm24[场号 % 24]`）分配 —— 一次训练里\n"
                 "                4! 种座次关系各占 1/24（相对偏移 +1/+2/+3 各 1/3）。**缺省关**（不开时\n"
                 "                代码路径与旧口径逐位相同）；与 `--rotate` 同时给时 **本开关优先**\n"
                 "  --sample K    每 K 次决策记 1 条（缺省 1 = 全记）\n"
                 "  --no-claims   不记录鸣牌决策\n"
                 "  --aux         额外落**标签侧**文件 `g*.aux.npz`（对手手牌/听牌、放铳、和了、顺位；\n"
                 "                轨迹不动）—— 与 Java 侧同一份格式与口径（逐元素对拍见\n"
                 "                `tools/trainer-aux-parity.mjs`）\n"
                 "  --preset X    规则预设（mleague|tenhou|majsoul|custom）；缺省 mleague\n"
                 "  --out DIR     轨迹输出目录（g<场号>.jsonl + summary.json）\n"
                 "  --batch-forward K   **确定批前向**（缺省关 = 0）：每 K 行成一批（opt-in）\n"
                 "                批组成 = **屏障时刻未决键里字典序最小的 min(K,|未决|) 个**，键 =\n"
                 "                `(场号, 该场第几次询问, 座位)`；⛔ 绝不按到达顺序凑批\n"
                 "                （`--batch-order arrival` 是故意红的负向开关，守卫必拦）\n"
                 "                只接 `net:`（v4）策略；引擎 = 进程内逐行（GPU 服务见 §6.26）\n"
                 "  --batch-games L     同时在场（= 开 L 条 worker 线程、静态轮转分场）的场数；缺省 = K\n"
                 "  --batch-tail T      尾批（|未决| < K）：keep（缺省，与批服务同）/drop/error\n"
                 "  --batch-order O     canonical（缺省）/ arrival（负向）\n"
                 "  --batch-plan F      批计划 + 指纹落成 JSON（F）\n");
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
    // `--aux`：标签侧文件（`g*.aux.npz`）—— 训练端**没接**（Java 侧已落地），显式报错不静默降级
    bool auxLabel = false;
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
        } else if (a == "--rotate-perm") {
            c.rotatePerm = true;
        } else if (a == "--sample") {
            c.sampleEvery = std::atoi(next("--sample"));
        } else if (a == "--no-claims") {
            c.recordClaims = false;
        } else if (a == "--preset") {
            c.preset = next("--preset");
        } else if (a == "--teacher-label") {
            teacherLabel = true;
        } else if (a == "--aux") {
            c.aux = true;
        } else if (a == "--batch-forward") {
            c.batchForward = std::atoi(next("--batch-forward"));
        } else if (a == "--batch-games") {
            c.batchGames = std::atoi(next("--batch-games"));
        } else if (a == "--batch-tail") {
            c.batchTail = next("--batch-tail");
        } else if (a == "--batch-order") {
            c.batchOrder = next("--batch-order");
        } else if (a == "--batch-plan") {
            c.batchPlan = next("--batch-plan");
        } else if (a == "--help" || a == "-h") {
            selfplayUsage(stdout);
            return 0;
        } else {
            std::fprintf(stderr, "[trainer] 未知参数：%s\n", a.c_str());
            return 2;
        }
    }
    if (teacherLabel) {
        // Java 有 `--teacher-label`（DAgger）：学生座位的决策行要额外记 `teacher` / `teacher_index`
        // 两列。⚠ teacher 本体**已经移植了**（docs/TRAINER-CPP.md §6.17）—— 这里缺的只是
        // `TraceRecorder` 多记那两列（`trace.*` + 本文件的接线）。
        // ⚠ 显式报错，**不静默降级**（AGENTS §6.5：能力缺失要报错）。
        std::fprintf(stderr, "[trainer] --teacher-label（DAgger 的老师标注）训练端还没接：teacher 本体"
                             "已可用（§6.17），缺的是记录器的 `teacher`/`teacher_index` 两列（§5 M3 的 ⏳）\n");
        return 2;
    }
    if (auxLabel) {
        // 占位：`--aux` 已落地（`trainer/src/npzwriter.hpp` + `TraceRecorder` 的标签侧行），
        // 这一支不该再被执行 —— 留着是为了让"旧拒绝分支被误改回来"当场可见。
        std::fprintf(stderr, "[trainer] 内部错误：--aux 的旧拒绝分支不该被执行\n");
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
