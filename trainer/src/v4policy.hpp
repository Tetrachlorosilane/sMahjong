// **v4 网络的纯 C++ 手写前向** —— 逐句移植 Java `mahjong.ai.V4Policy`（那一份是权威实现）。
//
// 权重格式 = `net.bin` **格式 2**（带块清单与张量表，逐字段见 `python/mahjong_ml/v4/export.py`
// 的模块 docstring 与 `docs/FEATURES-V4.md` §6）。与 v3 的 `net.hpp`（格式 1 / 定长 MLP）
// **共用同一个魔数 `MJNN`**，靠 `format` 字段区分 ⇒ 拿错了一律加载期报错（两侧都不"尽力而为"）。
//
// 拓扑（`python/mahjong_ml/v4/model.py`，逐层对应）：
//   tile[34,48] → 逐牌种 MLP → 自注意力 → LayerNorm → proj            → h_tile[34,tileD] + pool[d]
//   evt [60,96] → MLP → GRUCell ×60（冷启动）→ TransformerEncoder×1 → e_tokens[60,d] + h_evt[d]
//   ctx [64] / cand[n,128] → MLP（末尾**也有** ReLU）
//   融合：候选当 query 对 tile/evt 做注意力；delta→state 门控写回；state→delta 用 FiLM 调制；
//         拼接 [u_tile, evt_mod, ctx] → MLP（末尾 ReLU）→ u[n,d]
//   头：policy/effect/danger 用 u；value/placement/belief_* 用 mean_i(u)
//
// ⚠ 三处"看起来多余但**不能省**"的地方（省了就是静默偏移，Java 侧的原注释）：
//   ① `tile.enc` 第二个线性层之后**有** ReLU，而 `event.enc` 第二个之后**没有**；
//   ② `Mlp` 末端的 ReLU（ctx/cand/fusion.out 都有）；
//   ③ 事件塔冷启动要**喂满 K=60 个 token**（含前面全 0 的 padding）。
// ⚠ 还有两条是踩出来的坑：
//   ④ `linear` 必须**别名安全**（Java 踩过：`linear(h,h,…)` 原地覆盖导致 logits 整体偏 0.0044）；
//   ⑤ `gruStep` 必须**返回新数组**（`gh` 要读整条旧 `h`）。
#pragma once

#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <vector>

#include "jsonscan.hpp"

namespace trainer {

/** 与 v3 同一个魔数（`kNetMagic`），用 `format` 区分代号。 */
inline constexpr uint32_t kV4Magic = 0x4D4A4E4EU;
/** v4 = 格式 2（带块清单）；v3 = 1。 */
inline constexpr int kV4FormatVersion = 2;
/** 头部字节数（9 个 u32 + 2 个 u32 + 16 B 指纹 + 1 个 u32）。 */
inline constexpr int kV4HeaderBytes = 64;

/** 一份加载好的 v4 权重（字段与 Java `V4Policy` 同构）。 */
struct V4Policy {
    int dModel = 0;
    int tileD = 0;
    int nHeads = 0;
    int valueBins = 0;
    std::string fingerprint;
    /** 文件里的块清单（按注册表顺序，子集 = 消融训练的权重）。 */
    std::vector<std::string> blocks;
    /** 注册表里有、权重里没有的块（**训练时就没用** ⇒ 推理必须置 0，规范 §7）。 */
    std::vector<std::string> missingBlocks;

    /** 二维张量：行主序紧凑（`rows × cols`），与 Java 的 `float[rows][cols]` 同序。 */
    struct Mat {
        int rows = 0;
        int cols = 0;
        std::vector<float> data;
    };

    std::map<std::string, Mat> mats;
    std::map<std::string, std::vector<float>> vecs;
    /** 文件里出现过的全部张量名（用来判"有张量没人读"）。 */
    std::set<std::string> allNames;
    /** `bindAll` 读过的张量名（构造期与 `allNames` 比对；**前向不改它** —— 见下）。 */
    std::set<std::string> used;

    /**
     * 取二维张量（形状核对）= Java `V4Policy.mat`。
     *
     * <p>⚠ 比 Java 多一条约束：这两个查询**是 `const` 且不改任何状态**。
     * Java 的 `mat`/`vec` 会顺手 `used.add(name)`，而 C++ 这边一份权重会被
     * **多个自对弈 worker 线程共享**（`policies.hpp` 的工厂返回同一个 `shared_ptr`），
     * 前向里改集合就是数据竞争 —— 所以"记 `used`"只在 `bindAll`（构造期、单线程）里做。
     */
    const Mat *mat(const std::string &name, int rows, int cols, std::string &err) const;
    /** 取一维张量（长度核对）= Java `V4Policy.vec`。 */
    const std::vector<float> *vec(const std::string &name, int n, std::string &err) const;

    /**
     * 把每一层要用的张量都取一遍 —— **同时**完成"形状核对 + 无多余张量"两件事
     * （= Java `V4Policy.bindAll` + 构造器末尾的 `used != allNames` 检查）。
     */
    bool bindAll(std::string &err);

    /** 全部七个头（自检与 golden 对拍用）= Java `V4Policy.forwardAll`（**只读**，可多线程共享）。 */
    bool forwardAll(const JVal &obs, std::map<std::string, std::vector<float>> &out,
                    std::string &err) const;

    /** 策略头 logits（逐候选，顺序 = `obs.legal`）= Java `V4Policy.logits`。 */
    bool logits(const JVal &obs, std::vector<float> &out, std::string &err) const;
};

/** 从文件加载（失败填 `err`）= Java `V4Policy.load`。 */
bool v4LoadPolicy(const std::string &path, V4Policy &out, std::string &err);
/** 从内存字节加载（golden 夹具里的内嵌权重走这条）= Java `V4Policy.loadBytes`。 */
bool v4LoadPolicyBytes(const std::vector<uint8_t> &raw, const std::string &what, V4Policy &out,
                       std::string &err);

/**
 * 策略头 logits（逐候选，顺序 = obs 的 `legal`）= Java `V4Policy.logits`。
 *
 * <p>自由函数版接口（内部就是 `p.logits(...)`）：调用方拿一份 `const V4Policy&` 就能用，
 * 与 `policies.hpp` 的 `net:` 分派、`v4net` CLI 共用同一条路径。
 */
bool v4Logits(const V4Policy &p, const JVal &obs, std::vector<float> &out, std::string &err);

/** 全部七个头（自检 / golden 对拍用）= Java `V4Policy.forwardAll`。 */
bool v4ForwardAll(const V4Policy &p, const JVal &obs,
                  std::map<std::string, std::vector<float>> &out, std::string &err);

/**
 * 只读权重文件头 8 字节判格式（= Java `NetWeights.loadBytes` 的分派口）。
 *
 * @param format 出参：1 = v3 定长 MLP（`net.hpp` 那条路），2 = v4（本文件）
 */
bool v4WeightFormatOf(const std::string &path, int &format, std::string &err);

/** 自检用：返回一份"策略头输出偏置整体偏移 `delta`"的副本（红证：每条 logit 必须恰好 +delta）。 */
bool v4DebugOutputBiasShift(const V4Policy &p, float delta, V4Policy &out, std::string &err);

/** 参数个数（= Java `V4Policy.paramCount`）。 */
long long v4ParamCount(const V4Policy &p);

/** 一句话自述（= Java `V4Policy.describe`）。 */
std::string v4Describe(const V4Policy &p);

/**
 * `trainer v4net <net.bin> <轨迹1.jsonl> [更多...]`：逐条 decision 行打印 logits，
 * 格式与 `tools/V4Probe.java` **逐字符相同**（对拍闸门按行比对）：
 *   `step=<step> n=<n> argmax=<i> logits=<v0>,<v1>,...`
 * 外加"一条决策都没有的文件"一行 `# <文件名> 0 条`。值用 `%.9g`。
 */
int v4NetCli(int argc, char **argv);

/**
 * `trainer v4golden <夹具> [--tol 1e-4]`：读 `tools/V4Probe.java --golden` 那一份夹具，
 * 核对 ① 四张量逐元素 ② 四个推理头逐元素 + policy argmax 逐条 ③ 红证（策略头偏置 +1）。
 */
int v4GoldenCli(int argc, char **argv);

}  // namespace trainer
