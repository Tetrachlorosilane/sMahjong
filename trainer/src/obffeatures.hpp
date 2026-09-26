// **派生特征**（sidecar 的内容） —— 与 Java `mahjong.ai.ObsFeatures` **逐字段同值**。
//
// 为什么放在这里而不是让 Python 去算：这些量在**推理时也必须算**（服务端策略 / 将来任何
// 在线前向），而它们的权威实现是 `HandEval` / `Danger` / `Shanten` —— 在别处再写一份
// 就是"两套实现、必然漂移"。所以口径是：**一处算一次，别处只读**（Java 类注释同一条纪律）。
//
// 布局（自检/`docs/TRAINING.md` §5/Python 侧三处对齐）：
//   逐决策 PER_DECISION = 71：
//     [0,34)   danger_worst[k]    —— 对四家最坏的放铳危险度
//     [34,68)  danger_riichi[k]   —— 只对立直家最坏（弃和口径）
//     [68]     shanten_now        —— 当前手牌的向听数（听牌记 0；与逐候选同一根轴）
//     [69]     value_han          —— 打点粗估番数（`Bot::estimatedHan`，与押し引き同一把尺子）
//     [70]     value_points       —— 打点粗估点数（`Bot::hanToPoints`）
//   ⚠ 归一化分母**逐维不同**（见 `kDerivedDecisionScale`）：危险度 0..100，而向听 0..8、
//     番数 0..13、点数 0..32000 —— 用一个常数除会把后几维压成 0。
//   ⚠ **不把"当前进张种数/枚数"放进状态段**（v3 设计时试过）：那要跑一次完整的
//     `HandEval.of`（34 次 `Shanten.min`），Java 实测 ≈0.95 ms/决策，把状态段开销抬了 20 倍
//     （C++ 侧同一条 A/B 只慢 ≈3%，但布局跟 Java 走，两边必须同规格）；
//     而"打完这张之后"的进张本来就在**逐候选段**里，决策时真正要的是"候选之间比大小"。
//     这里只跑一次 `Shanten.min`（≈30 µs）。
//   逐候选 PER_CANDIDATE = 8（顺序固定）：
//     shanten, advance_types, advance_tiles, wait_types, wait_tiles,
//     good_wait_types, good_wait_tiles, dora_count
//
// ⚠ 逐候选的快照统一是"这一手做完、该打的那张也打完"之后的形态（目标张数 13 − 3×新副露数）：
//   吃/碰/加杠之后本来还要打一张，暗杠/大明杠之后不用。需要挑一张打时取**向听最小**的那张，
//   同向听取**牌种下标最小**的（确定性）。
#pragma once

#include <array>
#include <string>
#include <vector>

#include "counts.hpp"
#include "jsonscan.hpp"
#include "meld.hpp"

namespace trainer {

/** 派生特征的版本（变了就要 +1：数据集靠它判兼容）。 */
inline constexpr int kFeatureVersion = 2;
/** 逐决策段长度（71 = 68 危险度 + 3 自家牌力/打点；= Java `ObsFeatures.PER_DECISION`）。 */
inline constexpr int kPerDecision = 2 * kKindCount + 3;
/** 逐候选段长度（= Java `ObsFeatures.PER_CANDIDATE`）。 */
inline constexpr int kPerCandidate = 8;
/** 危险度那一半的分母（= Java `Features.DERIVED_DANGER_SCALE`）。 */
inline constexpr int kDerivedDangerScale = 100;

/**
 * 逐决策段**每一维的归一化分母**（= Java `ObsFeatures.DERIVED_DECISION_SCALE`）。
 *
 * <p>⚠ v3 起这一段不再同量纲：前 68 维是危险度（0..100），后 3 维是向听/打点。
 * 用一个常数去除，后 3 维会被压成 0（等于白加）。
 */
constexpr std::array<int, kPerDecision> makeDecisionScale() {
    std::array<int, kPerDecision> s{};
    for (int i = 0; i < 2 * kKindCount; i++) {
        s[static_cast<size_t>(i)] = kDerivedDangerScale;
    }
    s[2 * kKindCount] = 8;                 // 向听
    s[2 * kKindCount + 1] = 13;            // 打点粗估番数
    s[2 * kKindCount + 2] = 32000;         // 打点粗估点数
    return s;
}

/** 逐维分母（表体见 `makeDecisionScale`）。 */
inline constexpr std::array<int, kPerDecision> kDerivedDecisionScale = makeDecisionScale();

/** 算派生特征所需的全部输入（**只用公开信息 + 自家手牌**）。 */
struct FeatureView {
    Counts hand{};
    std::vector<Meld> melds;
    Counts visible{};
    std::array<Counts, 4> rivers{};
    std::array<bool, 4> riichi{};
    /** 宝牌指示牌的**牌种 kind**（与 `Round.doraIndicators()` 同一口径，**不是**牌 id）。 */
    std::vector<int> dora;
    int turn = 0;
    int seat = 0;
    /** 被鸣 / 被荣那张的**牌码**（鸣牌询问才有；`chi` 合成面子时需要它才知道第三张是什么）。 */
    std::string calledTile;
    /** 自家是否已立直（打点粗估：门清且未立直时按"会立直"加一根）。 */
    bool selfRiichi = false;
    /** 自家手里的**赤五**张数。 */
    int akaInHand = 0;
    /** 场风（0=东）与庄家座位：打点粗估算役牌的自风要用。 */
    int roundWind = 0;
    int dealer = 0;
    /** 规则是否允许食い断（打点粗估的断幺项；轨迹里没有规则字段时用三套预设的默认值 true）。 */
    bool kuitan = true;
};

/** 从**轨迹里的 `obs`** 填视图（= Java `ObsFeatures.ofObs`）。形状不对返回 false。 */
bool featureViewOfObs(const JVal &obs, FeatureView &v);

/** 71 维：两套逐张危险度（68）+ 自家牌力与打点（3，= Java `ObsFeatures.perDecision`）。 */
std::array<int, kPerDecision> perDecision(const FeatureView &v);

/** 8 维：这一手做完（该打的也打完）之后的形态（= Java `ObsFeatures.perCandidate`）。 */
std::array<int, kPerCandidate> perCandidate(const FeatureView &v, const std::string &key);

/** 手牌 + 副露里的宝牌张数（= Java `HandEval.doraCount`）。 */
int doraCount(const Counts &counts, const std::vector<Meld> &melds, const std::vector<int> &dora);

/** 布局自述（与 Java `ObsFeatures.describe()` 同文，日志用）。 */
std::string featureLayout();

}  // namespace trainer
