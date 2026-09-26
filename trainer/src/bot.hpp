// 补位机器人 / teacher —— `server/src/main/java/mahjong/bot/Bot.java`（1,443 行）的**逐句移植**。
//
// 为什么必须逐句：它是行为克隆的**标签来源**（AGENTS §6.6「改 teacher ＝ 改训练标签」），
// 而判据是 `docs/TRAINER-CPP.md` §2 铁律 2 的「同种子 → 同轨迹（逐字节）」——
// 任何一处"这样更好"的改动都会让 C++ 产出的 `g*.jsonl` 与 Java 不再同源（而且**不报错**）。
//
// 移植纪律（每一处都按 Java 的行号照抄）：
//   · 权重 / 阈值 / 排序 / 破平顺序 / 集合的插入序一律照抄，**不改**；
//   · 算术类型逐处对应（`double` vs `float` vs `int`）；整数除法、`Math.round`、`Math.abs`
//     这些细节按 Java 的语义写（见 `javaRoundToInt` 的注释）；
//   · 有疑问的地方在注释里写清"照抄自 Bot.java 第 N 行"。
//
// 与 Java 的**唯一**结构性差异（都不改行为）：
//   ① Java 的 `Bot.decide` 有 `catch (RuntimeException) → fallback(options)` 兜底；
//      训练端的 `trainer/build.ps1` 编译带 `-fno-exceptions`，所以那一层没有对应物 ——
//      本文件里所有下标都经过显式边界判断（Bot 自己不会抛），兜底只剩"选项认不出"那几条路；
//   ② Java 的 13 个 `debug*` 计数器是 `static long`（并行自对弈下本身就是竞态的）；
//      C++ 用 `std::atomic<long long>`（relaxed）—— 计数只用于诊断，不参与决策。
#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "counts.hpp"
#include "danger.hpp"
#include "meld.hpp"
#include "options.hpp"
#include "policies.hpp"

namespace trainer {

class Round;

/**
 * teacher 的五层取舍（牌效 / 押し引き / 打点与役 / 开杠 / 顺位与终局）+ 对手模型（危险度）。
 *
 * 形状与 Java `mahjong.bot.Bot` 一一对应：
 *   `Bot::decide` = `Bot.decide(Round, seat, kind, options, extra)`；
 *   `Bot::HandState` = 那份"只有公开信息 + 自己手牌"的视图（Java 的嵌套类 `Bot.HandState`）。
 *
 * ⚠ 允许读 `Round`（teacher 不是 ML 策略，见 AGENTS §6.5 的例外），但**只读**：
 *   本文件里没有任何一处写 `Round` 的状态。
 */
struct Bot {
    // ============================================================== 自检计数器
    //
    // Java `Bot.debugXxxCount`（13 个）—— 「判据写了但实战一次没走到」的假绿防线。
    // 训练端并行跑，所以用原子；`relaxed` 足够（只统计，不做同步）。

    /** 弃和次数。 */
    inline static std::atomic<long long> debugFoldCount{0};
    /** 默听（ダマテン）次数。 */
    inline static std::atomic<long long> debugDamaCount{0};
    /** 因为"鸣完没役"而放掉的鸣牌次数。 */
    inline static std::atomic<long long> debugNoYakuRefuseCount{0};
    /** 真的开了杠的次数（暗杠 / 加杠 / 大明杠）。 */
    inline static std::atomic<long long> debugKanCount{0};
    /** 因为"杠完会丢听牌 / 向听倒退"而放掉的杠。 */
    inline static std::atomic<long long> debugKanRefuseWait{0};
    /** 因为"正在弃和"而放掉的杠。 */
    inline static std::atomic<long long> debugKanRefusePressure{0};
    /** 因为"第 4 个杠会把本局打散（四杠散了）"而放掉的杠。 */
    inline static std::atomic<long long> debugKanRefuseFourKan{0};
    /** 因为"这张牌对他家太危险（加杠会被抢杠）"而放掉的**加杠**。 */
    inline static std::atomic<long long> debugKanRefuseDanger{0};
    /** 因为"门清鸣了不值（不听牌又不到 2 番）"而放掉的副露。 */
    inline static std::atomic<long long> debugCallValueRefuseCount{0};
    /** 押し引き判定为**推进**（期望值过门槛）的次数。 */
    inline static std::atomic<long long> debugPushCount{0};
    /** 因为"顺位门槛"（`pushThreshold`）而**改了结论**的次数。 */
    inline static std::atomic<long long> debugPlacementFlipCount{0};
    /** 终局见逃（`shouldDeclineRon`）的次数。 */
    inline static std::atomic<long long> debugRonDeclineCount{0};
    /** 因为"没人立直、但有人鸣开得很明显"而弃和的次数（对手模型那条闸门）。 */
    inline static std::atomic<long long> debugOpenFoldCount{0};

    /**
     * 自检专用开关：一有机会就开杠（Java `Bot.debugAlwaysKan`，默认 false）。
     *
     * <p>训练端**从不开**它（正常对局里机器人只在"已经鸣过牌 + 有役 + 不倒退"时开杠）；
     * 留着是为了与 Java 同名同构、便于将来在 C++ 自检里覆盖「开杠 → 岭上摸牌」那条路。
     */
    inline static bool debugAlwaysKan = false;

    /** 自检专用：把出牌段开杠上报的 `tile` 换成这个牌码（Java 同名；缺省空 = 不换）。 */
    inline static std::string debugKanTileOverride;

    /** 13 个计数器的快照（等价于直接读 Java 的 `Bot.debugXxxCount`）。 */
    struct DebugCounts {
        long long fold = 0;
        long long dama = 0;
        long long noYakuRefuse = 0;
        long long kan = 0;
        long long kanRefuseWait = 0;
        long long kanRefusePressure = 0;
        long long kanRefuseFourKan = 0;
        long long kanRefuseDanger = 0;
        long long callValueRefuse = 0;
        long long push = 0;
        long long placementFlip = 0;
        long long ronDecline = 0;
        long long openFold = 0;
    };

    static DebugCounts debugCounts();
    static void debugResetCounts();

    // ============================================================== 公开信息视图

    /**
     * teacher 决策所需的一切 —— **只有公开信息 + 自己的手牌**（Java `Bot.HandState`）。
     *
     * <p>与 Java 的字段一一对应；两处类型差异都是"Java 里可为 null"的显式化：
     *   · `scores` 用 `std::optional` 表示 Java 的 `int[] scores == null`（点数未知）；
     *   · `melds` / `doraIndicators` / `meldCounts` 都是值语义（Java 那边 `List.of()` 的等价物）。
     */
    struct HandState {
        /** 自家暗牌计数（自家回合是 14 张，含刚摸到的那张）。 */
        Counts counts{};
        int meldCount = 0;
        std::vector<Meld> melds;
        /** 可见牌计数（`Visible.counts`）。 */
        Counts visible{};
        /** 四家牌河的**牌种计数**（现物/筋判据）。 */
        std::array<Counts, 4> rivers{};
        std::array<bool, 4> riichi{};
        bool selfRiichi = false;
        /** 食断（喰いタン）是否成立 —— 决定"鸣完还有没有役"里断幺九算不算数。 */
        bool kuitan = false;
        int seat = 0;
        int dealer = 0;
        int roundWind = 0;
        /** 巡目（已打出的总张数 / 4），只用于危险度打分。 */
        int turn = 0;
        std::vector<int> doraIndicators;
        /** 自家暗牌里有几张**赤五**。 */
        int akaInHand = 0;
        /** 本局**全场**已经开过的杠数。 */
        int kanCount = 0;
        /** 规则开关：四杠散了会不会强制流局。 */
        bool fourKanAbort = false;
        /** 牌山剩余可摸张数。 */
        int tilesLeft = 0;
        /** 四家点数；**`nullopt` = 点数未知**（自检直接构造的局面）→ 顺位类判据一律不施加权重。 */
        std::optional<std::array<int, 4>> scores;
        /** 供託里的立直棒根数。 */
        int sticks = 0;
        /** 本局是第几局（1..4）。 */
        int kyoku = 0;
        /** 是否**终局**（オーラス）。 */
        bool allLast = false;
        /** 四家副露数。 */
        std::array<int, 4> meldCounts{};

        /** 从牌桌取公开信息（Java `HandState.of`，唯一读 `Round` 的地方）。 */
        static HandState of(const Round &r, int seat);

        /** 自家已经开过的杠数（暗杠 / 加杠 / 大明杠）。 */
        int myKans() const;

        int kindCount(int kind) const { return counts[static_cast<size_t>(kind)]; }

        /** 是否有别家立直。 */
        bool opponentRiichi() const;

        /** 除自己外，有几家立直。 */
        int opponentRiichiCount() const;
    };

    // ============================================================== 决策入口

    /**
     * Java `Bot.decide(Round r, int seat, String kind, List<Map> options, Object extra)`。
     *
     * @param calledTileId 鸣牌询问的"被鸣那张牌 id"（= Java `extra.get("tile")` 的牌码解析回来）；
     *                     自家回合传 {@code -1}（`decideTurn` 不看 extra）
     */
    static Cmd decide(const Round &r, int seat, const std::string &kind,
                      const std::vector<Option> &options, int calledTileId);

    // ---- 以下都是 Java 里 `public static` 的取舍判据（自检可直接断言）----

    static bool shouldDeclareRiichi(const Round &r, int seat, const HandState &st,
                                    const std::string &tileCode);
    static std::string chooseDiscard(const HandState &st,
                                     const std::vector<std::string> &candidates);
    static bool callWorthForMenzen(const HandState &st, const Counts &afterCall, const Meld &meld);
    static int bestShantenAfterCall(const Counts &countsAfterCall, int meldCount, int handSize);
    static bool isYakuhai(const HandState &st, int kind);
    static bool hasYakuPlan(const HandState &st, const Counts &afterCall, const Meld &meld);
    static bool shouldKan(const HandState &st, const std::string &kanKind, int kind);
    static bool shouldDeclineRon(const Round &r, int seat, const HandState &st, int winKind);
    static int estimatedHan(const HandState &st, const Counts &counts,
                            const std::vector<Meld> &melds);
    static int hanToPoints(int han);
    static double winProbability(int needTiles, int tilesLeft, int turnsLeft, int steps);
    static double dealProbability(int dangerScore);
    static double pushEv(double winProb, int winPoints, double dealProb);
    static bool shouldPush(double winProb, int winPoints, double dealProb);
    static bool shouldPush(double winProb, int winPoints, double dealProb, double threshold);
    static int placementOf(const std::optional<std::array<int, 4>> &scores, int seat);
    static double pushThreshold(const HandState &st);
    static double openThreat(const HandState &st);
    static std::array<bool, 4> threatSeats(const HandState &st);
    static int dealScore(const HandState &st, int kind);
    static int dealScore(const HandState &st, int kind, const DangerReport &base);

    /** Java `Bot.waits`（听牌种类，供外部调试）。 */
    static std::vector<int> waitsOf(const Round &r, int seat);

    // ---- 常量（Java 里是 `public static final`，值一字不改）----

    /** 放铳的**平均失点**（本作口径）。 */
    static constexpr int kAvgDealPoints = 5200;
    /** 终局（オーラス）把顺位偏置**放大**。 */
    static constexpr double kAllLastScale = 2.0;
    /** 鸣き手威胁到多少就按"疑似听牌"权衡押し引き。 */
    static constexpr double kOpenThreatGate = 0.6;
    /** 见逃至少要还剩这么多张可摸（≈ 4 巡）。 */
    static constexpr int kRonDeclineMinTiles = 16;
};

/**
 * Java `Policies.TEACHER`（`d -> Bot.decide(d.round, d.obs.seat, d.kind, d.options, d.extra)`）
 * 在训练端的等价物。
 *
 * ⚠ 与 `first` / `pass` / `random` 不同，teacher **不做**"回包不在 legal 里就退回"这一步 ——
 *   Java 的 `TEACHER` 直接调 `Bot.decide`、把回包原样交给状态机（校验交给 `Round`），
 *   这里保持一致（裸 `pon` / 不带取法的大明杠就是靠这条才落到"默认取法"上）。
 *
 * 定义在 `bot.cpp`；`policies.hpp` 只前置声明它（这样 `policies.hpp` 不必包含本头文件，
 * 避免 `round.hpp → policies.hpp → bot.hpp → round.hpp` 的循环）。
 */
Policy makeTeacherPolicy();

}  // namespace trainer
