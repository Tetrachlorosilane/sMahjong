// **派生特征**（sidecar 的内容） —— 与 Java `mahjong.ai.ObsFeatures` **逐字段同值**。
//
// 为什么放在这里而不是让 Python 去算：这些量在**推理时也必须算**（服务端策略 / 将来任何
// 在线前向），而它们的权威实现是 `HandEval` / `Danger` / `Shanten` —— 在别处再写一份
// 就是"两套实现、必然漂移"。所以口径是：**一处算一次，别处只读**（Java 类注释同一条纪律）。
//
// 布局（自检/`docs/TRAINING.md` §5/Python 侧三处对齐）：
//   逐决策 PER_DECISION = 68：
//     [0,34)   danger_worst[k]    —— 对四家最坏的放铳危险度
//     [34,68)  danger_riichi[k]   —— 只对立直家最坏（弃和口径）
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
inline constexpr int kFeatureVersion = 1;
/** 逐决策段长度（= Java `ObsFeatures.PER_DECISION`）。 */
inline constexpr int kPerDecision = 2 * kKindCount;
/** 逐候选段长度（= Java `ObsFeatures.PER_CANDIDATE`）。 */
inline constexpr int kPerCandidate = 8;

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
};

/** 从**轨迹里的 `obs`** 填视图（= Java `ObsFeatures.ofObs`）。形状不对返回 false。 */
bool featureViewOfObs(const JVal &obs, FeatureView &v);

/** 68 维：两套逐张危险度（= Java `ObsFeatures.perDecision`）。 */
std::array<int, kPerDecision> perDecision(const FeatureView &v);

/** 8 维：这一手做完（该打的也打完）之后的形态（= Java `ObsFeatures.perCandidate`）。 */
std::array<int, kPerCandidate> perCandidate(const FeatureView &v, const std::string &key);

/** 手牌 + 副露里的宝牌张数（= Java `HandEval.doraCount`）。 */
int doraCount(const Counts &counts, const std::vector<Meld> &melds, const std::vector<int> &dora);

/** 布局自述（与 Java `ObsFeatures.describe()` 同文，日志用）。 */
std::string featureLayout();

}  // namespace trainer
