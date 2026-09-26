// **网络策略 `net:<权重文件>` 的前向** —— 与 Java `mahjong.ai.NeuralPolicy` 逐字段同值。
//
// 为什么需要它：P5 的对抗训练（联赛世代）**每一席都跑 `net:<ckpt>`**（`online.py` 的采集命令
// 就是 `--policy net:...@0#1.0`），而训练端存在的意义是"同核数下快一个量级"。没有这一段，
// 换到 C++ 生产者就只能跑 `first/pass/random` 那三条基线臂。
//
// 权重格式（`mahjong_ml/export.py` 的 `weights` 导出，`NeuralPolicy.loadBytes` 是逐字段权威）：
//   小端；7 个 int32 头 = magic `0x4D4A4E4E`("MJNN")、版本 1、state_dim、cand_dim、hidden、
//   head_dim、trunk_layers；随后 float32 依次是：每层 trunk 的 `W[hidden][in]` 行主序 + `b[hidden]`
//   （`in` 首层是 state_dim、之后是 hidden），最后 `head W[head_dim*(hidden+cand_dim)]`、
//   `head b[head_dim]`、`out W[head_dim]`、标量 `ob`。
//
// 前向（与 `nets.CandidateScorer` / `NeuralPolicy` 一一对应）：
//   x    = Features.state(obs)                  607 = 539 基础 + 68 派生（danger / 100）
//   cand = Features.candidate(key, perCand)      96 = 88 基础 + 8 派生（各自 / DERIVED_CAND_SCALE）
//   h    = ReLU(W₂·ReLU(W₁·x + b₁) + b₂)                    # trunk，同一次询问的所有候选共用
//   logit_i = o·ReLU(H·[h ‖ cand_i] + b_h) + b_o            # 逐候选打分
//
// ⚠ **全程 `float`**（Java 那边也是 float，不是 double）：拼装的归一化系数、累加顺序、
//   `ReLU` 的 `s > 0 ? s : 0f` 都必须逐句对应。为了让"同种子 → 同轨迹"这条硬性质成立，
//   编译时加了 `-ffp-contract=off`（禁 FMA 收缩 —— 它只改最后一位舍入，而 1e-4 的逐元素容差
//   **挡不住** argmax 翻转；本项目的判据是逐字节轨迹，不是"差不多"）。
#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "jsonscan.hpp"

namespace trainer {

/** 编译期常量（= Java `mahjong.ai.Features.STATE` / `.CAND`）。 */
inline constexpr int kNetState = 607;
inline constexpr int kNetCand = 96;
/** 权重魔数（"MJNN"）与格式版本（= Java `NeuralPolicy.MAGIC` / `FORMAT_VERSION`）。 */
inline constexpr uint32_t kNetMagic = 0x4D4A4E4EU;
inline constexpr int kNetFormatVersion = 1;

/** 一份加载好的网络权重（字段与 Java `NeuralPolicy` 同构）。 */
struct Net {
    int stateDim = 0;
    int candDim = 0;
    int hidden = 0;
    int headDim = 0;
    int trunkLayers = 0;
    /** trunk 每层 `[out][in]` 权重与 `[out]` 偏置。 */
    std::vector<std::vector<std::vector<float>>> tw;
    std::vector<std::vector<float>> tb;
    /** 打分头：`[headDim][hidden + candDim]`、`[headDim]`；输出层 `[headDim]`、标量。 */
    std::vector<float> hw;
    std::vector<float> hb;
    std::vector<float> ow;
    float ob = 0.f;

    /** 参数个数（日志/自检用 = Java `NeuralPolicy.params()`）。 */
    long long params() const;
};

/** 从文件加载权重；失败填 `err` 并返回 false（错误文案与 Java 同义）。 */
bool loadNet(const std::string &path, Net &out, std::string &err);

/** 从内存字节加载（自检/golden 夹具用；`what` 只用于日志）。 */
bool loadNetBytes(const std::vector<uint8_t> &raw, const std::string &what, Net &out,
                  std::string &err);

/**
 * 观测 JSON + 合法动作键 → logits（顺序与 `keys` 一致；写进 `out`）。
 *
 * 等价于 Java `NeuralPolicy.logits(Map json, List<String> keys)`：**一次询问的全部候选**
 * （trunk 只算一次）。`keys` 为空时 `out` 为空表并返回 true（Java 同：随后回 `PASS`）。
 * 观测形状不对（`ObsFeatures.ofObs` 失败）时填 `err` 并返回 false —— **不静默给一堆 0**。
 */
bool netLogits(const Net &net, const JVal &obs, const std::vector<std::string> &keys,
               std::vector<float> &out, std::string &err);

/** 取最大值下标；**并列取最小下标**（= Java `NeuralPolicy.argmaxOf`：严格大于才更新）。 */
int netArgmax(const std::vector<float> &logits);

/**
 * `softmax(logits / temp)` 采样（= Java `NeuralPolicy.sampleSoftmax`）：
 * 先减最大值、`exp` 在 **double** 域算、NaN 权重记 0、`r = nextDouble() * sum` 后累积相减。
 * 全 NaN/和为 0 时退回 argmax；`temp <= 0` 由调用方走 argmax 路径（Java 同）。
 */
int netSampleSoftmax(const std::vector<float> &logits, float temp, class JavaRandom &rng);

/** `(gameSeed, seat)` → 采样随机源种子（= Java `Policies.mixSeed`，SplitMix 混合）。 */
int64_t netMixSeed(int64_t gameSeed, int seat);

/** 布局自述（日志用，与 Java `Features.describe()` 同义）。 */
std::string netDescribe();

/**
 * `trainer net <net.bin> <轨迹1.jsonl> [更多...]`：逐条 decision 行打印 logits，
 * 格式与 `tools/NetProbe.java` **逐字符相同**（对拍闸门按行比对）：
 *   `step=<step> n=<n> argmax=<i> logits=<v0>,<v1>,...`
 * 外加每个文件一行 `# <文件名> <条数> 条`。值用 `%.9g`（float32 往返精度足够）。
 */
int netCli(int argc, char **argv);

}  // namespace trainer
