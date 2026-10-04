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

struct V4Scratch;

/** 一份加载好的 v4 权重（字段与 Java `V4Policy` 同构）。 */
struct V4Policy {    int dModel = 0;
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
        /**
         * `data` 的**转置**：`dataT[c * rowsPad + r] == data[r * cols + c]`（行数补到 8 的倍数）。
         *
         * <p>为什么：热路径的 matvec 要**沿输出行**做 SIMD（8 个输出行一条指令），而沿归约维
         * （`c`）做 SIMD 会改变求和顺序 ⇒ 改最后一位舍入 ⇒ argmax 可能翻（编译期已为同一条
         * 理由开着 `-ffp-contract=off`）。转置之后：内层沿 `c` **升序**流式读，每个输出行仍是
         * "单累加器、`c` 升序、mul+add 两步舍入" ⇒ **与标量路径逐位相同**。
         * 加载期（`finishPolicy`）建一次，前向里只读。
         */
        std::vector<float> dataT;
        /** `dataT` 的行距（= `rows` 补齐到 8 的倍数）。 */
        int rowsPad = 0;
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

    /**
     * 全部七个头（自检与 golden 对拍用）= Java `V4Policy.forwardAll`（**只读**，可多线程共享）。
     *
     * @param h0 事件塔在**窗口之前**的 carry（长度 = `dModel`）。`nullptr`（缺省）= 生产路径的
     *           **整手 carry**（从 0 起重放 `obs.events[0, n-K)` 再喂窗口里的真实行）；
     *           给了 `h0` = 只从它起推进窗口里的真实行（golden 夹具那条路）。
     *           ⚠ 两者**不是**同一个量（W1 起 carry 被融合消费，混用 ⇒ 三端分叉）。
     * @param scr 前向的中间缓冲（见 `V4Scratch`）。`nullptr` = 就地分配（CLI / 自检 / golden
     *           那些一次性路径）；策略实例传自己那一份 ⇒ 热路径**零堆分配**。
     */
    bool forwardAll(const JVal &obs, std::map<std::string, std::vector<float>> &out,
                    std::string &err, const std::vector<float> *h0 = nullptr,
                    V4Scratch *scr = nullptr) const;

    /** 策略头 logits（逐候选，顺序 = `obs.legal`）= Java `V4Policy.logits`。 */
    bool logits(const JVal &obs, std::vector<float> &out, std::string &err,
                V4Scratch *scr = nullptr) const;
};

/**
 * **一次前向的中间缓冲**（扁平 `float` + 步长；只增不缩）。
 *
 * <p>⛔ **不能放进 `V4Policy`**：一份权重是全进程（多 worker 线程）共享只读的，
 * 往里塞可变缓冲就是数据竞争。所以缓冲跟着**策略实例**走 —— 一个实例只服务一个座位、
 * 一场、一个线程（`policies.hpp` 的工厂语义）。
 *
 * <p>为什么要有它：改之前每决策要新建 ~800 个 `std::vector`（`vector<vector<float>>`
 * 的中间量），实测那部分只占 ~5%，真正的收益是**它让"按 token 批处理"成为可能** ——
 * 批处理把每决策的权重流量从 ~180 MB 压到 ~25 MB（见 `linearBatchFast`）。
 */
struct V4Scratch {
    /** 一维缓冲（按需 `resize`，只增不缩）。 */
    std::vector<float> tileH;      // kKindCount × tileD（tile.enc 输出）
    std::vector<float> tileAt;     // kKindCount × tileD（tile.attn 输出）
    std::vector<float> eRows;      // kKEvt × dModel（event.enc 输出 = GRU 输入）
    std::vector<float> eTokens;    // kKEvt × dModel（Transformer 输出）
    std::vector<float> carry;      // dModel（事件塔的 GRU 隐状态）
    std::vector<float> tok;        // kCEvt（一条事件 token 行）
    std::vector<float> rowTmp;     // dModel（单行事件编码输出）
    std::vector<float> encTmp;     // 事件编码器第二层的别名中转
    std::vector<float> aliasTmp;   // 任意"输出与输入同一块"的批量线性层的中转
    std::vector<float> mlpHid;     // B × dModel（`mlp` 的中间层）
    std::vector<float> ctxEmb;     // dModel（`ctx.net` 输出）
    std::vector<float> xn;         // max(事件窗口, tile 行数) × dModel（norm 之后）
    std::vector<float> att;        // 同上（注意力输出）
    std::vector<float> yv;         // 同上（残差前的 y）
    std::vector<float> hid;        // kKEvt × 2·dModel（FFN 中间）
    std::vector<float> res;        // 同上（FFN 输出）
    std::vector<float> candEmb;    // n × dModel
    std::vector<float> kvTile;     // kKindCount × dModel
    std::vector<float> uTile;      // n × dModel
    std::vector<float> uEvt;       // n × dModel
    std::vector<float> u;          // n × dModel
    std::vector<float> danger;     // n × 4
    std::vector<float> effect;     // n × 3
    std::vector<float> qp;         // mha：q 投影
    std::vector<float> kp;         // mha：k 投影
    std::vector<float> vp;         // mha：v 投影
    std::vector<float> ctx;        // mha：上下文
    std::vector<float> scores;     // mha：注意力分数（长度 ≥ 最大 kv 行数）
    std::vector<float> gates;      // 6·dModel（GRU 六路门，先 x 后 h）
    std::vector<float> pool;       // tileD
    std::vector<float> hTilePool;  // dModel
    std::vector<float> gu;         // dModel
    std::vector<float> memG;       // dModel
    std::vector<float> gate;       // dModel
    std::vector<float> state;      // dModel
    std::vector<float> film;       // dModel
    std::vector<float> write;      // dModel
    std::vector<float> cat;        // 2·dModel
    std::vector<float> cat3;       // 3·dModel
    std::vector<float> btProb;     // 3
    std::vector<float> policy;     // n
    std::vector<float> meanU;      // dModel
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
bool v4Logits(const V4Policy &p, const JVal &obs, std::vector<float> &out, std::string &err,
              V4Scratch *scr = nullptr);

/** 全部七个头（自检 / golden 对拍用）= Java `V4Policy.forwardAll`。 */
bool v4ForwardAll(const V4Policy &p, const JVal &obs,
                  std::map<std::string, std::vector<float>> &out, std::string &err);

/**
 * 全部七个头，**显式给"窗口之前"的 carry**（= Java `V4Policy.forwardAll(Tensors, h0)`）。
 *
 * <p>golden 夹具（格式 2）走这条：`h0` 由 Python 显式给出，三端拿同一个 `h0` 才能判
 * "同 h ⇒ 同输出"。`h0` 长度必须是 `dModel` —— 对不上**当场报错**（既不截断也不补零：
 * 静默退化会让"同 h ⇒ 同输出"这条判据变成空转）。
 */
bool v4ForwardAllH0(const V4Policy &p, const JVal &obs, const std::vector<float> &h0,
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
 * `trainer v4bench <net.bin> <轨迹.jsonl> [条数上限]`：把 JSON 解析 / 特征拼装 / 网络前向
 * **分开计时**（与 Java `tools.V4Probe --bench` 同一件事）。只报实测，不写死验收数字。
 */
int v4BenchCli(int argc, char **argv);

/**
 * `trainer v4golden <夹具> [--tol 1e-4]`：读 `tools/V4Probe.java --golden` 那一份夹具，
 * 核对 ① 四张量逐元素 ② 四个推理头逐元素 + policy argmax 逐条 ③ 红证（策略头偏置 +1）。
 */
int v4GoldenCli(int argc, char **argv);

}  // namespace trainer
