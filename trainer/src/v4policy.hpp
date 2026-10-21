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
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "jsonscan.hpp"

namespace trainer {

/** 与 v3 同一个魔数（`kNetMagic`），用 `format` 区分代号。 */
inline constexpr uint32_t kV4Magic = 0x4D4A4E4EU;
/**
 * v4 = **格式 3**（64 B 头 + **2,048 B arch 段** + 块表 + 张量表）。
 *
 * <p>段 2（第一百零八轮）把 `net.bin` 从格式 2 升到 3；段 3b（本段）让 C++ 读端认 3。
 * ⚠ `format==2` 是**A− 之前的老包**、只有冻结快照能读 ⇒ 见 `v4LoadPolicyBytes` 的专用错误
 * （⛔ 不自动升级、不补 0、不猜 arch）。
 */
inline constexpr int kV4FormatVersion = 3;
/** 头部字节数（9 个 u32 + 2 个 u32 + 16 B 指纹 + 1 个 u32）。 */
inline constexpr int kV4HeaderBytes = 64;
/** arch 段：核心 96 B + 1,952 B 保留尾 ⇒ `archLen = 2,048`（块表偏移 = `64 + archLen`）。 */
inline constexpr int kV4ArchCoreBytes = 96;
inline constexpr int kV4ArchSegBytes = 2048;

// ---- arch 段的注册值（唯一规格 = `python/mahjong_ml/v4/export.py` 的 `arch_profile`）
/** `topoId`：2 = 现有 v4 图；3 = A−（T1）。未注册 ⇒ 报错。 */
inline constexpr int kTopoV4 = 2;
inline constexpr int kTopoA = 3;
/** `actId`：0 = ReLU（v4）、1 = GELU-erf（A−）。 */
inline constexpr int kActReLU = 0;
inline constexpr int kActGelu = 1;
/** `normId`：0 = post-LN（v4）、1 = pre-LN（A−）。 */
inline constexpr int kNormPost = 0;
inline constexpr int kNormPre = 1;
/** `flags` 位：bit0 B3 · bit1 `tile.tile_id` · bit2 FiLM（恒 0）· bit3 mem 门。 */
inline constexpr int kFlagB3 = 0x01;
inline constexpr int kFlagTileId = 0x02;
inline constexpr int kFlagFiLM = 0x04;
inline constexpr int kFlagMemGate = 0x08;
inline constexpr int kFlagReserved = 0xF0;

/**
 * **推理必需**的头 = 前向路径**实际读取**的头（⛔ 不是"训练过"的头）—— 与 Python
 * `model.ONLINE_HEADS`、Java `V4Policy.ONLINE_HEADS` **同名同值**，唯一规格 = `docs/TRAINER-CPP.md` §6.25。
 *
 * <p>判据（三端同一把尺子，机械判据）：把该头的张量**清零**后重跑同一次前向 ⇒ 策略头 logits
 * **逐位不变** ⇔ 它不在推理路径上。C++ 侧的实测在 `trainer v4golden` 那行的 `dValue/dBelief/dGate`
 * （①清零 `heads.value.*` 必须恰好 0；②清零 `heads.belief_tenpai.*` 必须 > 0；③清零
 * `heads.policy_gate.*` 必须 > 0 —— ③ 是 ② 的哨兵，门控恒等时 ② 会**假绿**）。
 *
 * <p>⚠ `belief_tenpai` 在线的**原因**：逐候选门控 `gate = 1 + tanh(policy_gate(sigmoid(bt)))`
 * （第五十五轮）**乘在逐候选表示上** ⇒ 真的改 argmax。而 `heads.policy_gate.*` 是**可缺张量**
 * （缺 = 恒等门控，旧网走 `normalize_state` 补 0）⇒ 老网的在线集合退化成 `{policy}`：
 * 这是"**每个 checkpoint 的性质**"，不是代码常量（换 checkpoint 要重跑判据）。
 *
 * <p>⚠ 本常量 = **两个 arch 的并集**：v4 只经 gate 读 `belief_tenpai`；**a− 的 `AHeads.forward`
 * 还读 `danger`**（0 参数固定通道）⇒ 逐 arch 的准确集合 = Python `online_heads_declared(arch)`
 * （第一百二十九轮修正；旧声明在 a− 上漏了 `danger`）。
 */
inline const std::vector<std::string> ONLINE_HEADS = {"policy", "belief_tenpai", "danger"};

/**
 * **训练与契约必需**的头：必须训练 + 过 parity + 进消融与加载器契约（⛔ 与"上线"**无关**）。
 *
 * <p>= 唯一规格 `docs/TRAINER-CPP.md` §6.25 的第二张表：训练必需要从来**推导不出**"影响决策"
 * （两者不能合并 —— 用户裁决）。`placement` / `belief_hand` / `effect` 三个头只训练、不上线。
 */
inline const std::vector<std::string> TRAIN_HEADS = {"policy", "value", "belief_tenpai", "danger"};

struct V4Scratch;
/** 增量 carry 缓存的实现细节（定义在 `v4policy.cpp`：只有那里拿得到 `kCEvt` / `kKEvt`）。 */
struct V4CarryCache;

/** 一份加载好的 v4 权重（字段与 Java `V4Policy` 同构）。 */
struct V4Policy {    int dModel = 0;
    int tileD = 0;
    int nHeads = 0;
    int valueBins = 0;
    std::string fingerprint;
    /** 文件格式（只可能是 3；留成字段是为了日志与 `describe` 能如实打印）。 */
    int format = 0;
    /** 拓扑：2 = v4（ReLU/post-LN）、3 = A−/T1（GELU-erf/pre-LN + 95-token joint）。 */
    int arch = 0;
    /** arch 段里的 `flags`（B3 / #0 / FiLM / mem 门）与 `valueRange`（诊断用）。 */
    int flags = 0;
    /** ★ S1（第一百一十六轮）：arch 段 `evtReadout=1` ⇒ 有逐候选事件读出（`fusion.evt_kv`/`evt_gamma`）。 */
    bool evtReadout = false;
    /** ★ P4（第一百一十六轮）：arch 段 `evtLn=1` ⇒ `evt` 首层有 LayerNorm（`event.norm0.*`）。 */
    bool evtLn = false;
    float valueRange = 0.f;
    /** arch 段里"文件声明的"结构字段（与代码常量交叉校验过才留在这里）。 */
    int dTile = 0;
    int dEvt = 0;
    int dFuse = 0;
    int dHead = 0;
    int nSeg = 0;
    int tileKernels[4] = {0, 0, 0, 0};
    /**
     * **权重整段字节的 sha256 前 16 位**（= `A-REFACTOR-IMPL-PLAN.md` 风险 5 的"权重 sha 段"）。
     *
     * <p>用途只有一个：`V4Cache` 槽键——`handKey` 只是"小局身份"，它**不含网络身份**
     * ⇒ 同进程换网（或换 arch）后旧槽会被静默复用（carry 是另一张图的）。键里带上它 + `arch`
     * + `format` ⇒ 换网必 miss。⛔ 别拿"块清单指纹"顶替：块清单与拓扑无关（两图指纹相同）。
     */
    std::string weightSha;
    /** 文件里的块清单（按注册表顺序，子集 = 消融训练的权重）。 */
    std::vector<std::string> blocks;
    /** 注册表里有、权重里没有的块（**训练时就没用** ⇒ 推理必须置 0，规范 §7）。 */
    std::vector<std::string> missingBlocks;

    /** 三维张量（只有 A− 的 `Conv1d` 权重是 3 维）：`[out][in][k]` 行主序（= PyTorch 布局）。 */
    struct Conv {
        int out = 0;
        int in = 0;
        int k = 0;
        std::vector<float> data;
    };

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
    std::map<std::string, Conv> convs;
    std::map<std::string, std::vector<float>> vecs;
    /**
     * A− 的**固定 sin/cos 位置编码** `[K_EVT][dModel]`（0 参数、非持久 buffer ⇒ 不在 `net.bin`
     * 里）。加载期按 `dModel` 算一次：它是网络拓扑的一部分（A− 才有），⛔ 别蒸腾成权重。
     */
    std::vector<float> peEvt;
    /** 文件里出现过的全部张量名（用来判"有张量没人读"）。 */
    std::set<std::string> allNames;
    /** `bindAll` 读过的张量名（构造期与 `allNames` 比对；**前向不改它** —— 见下）。 */
    std::set<std::string> used;

    /** 缓存键前缀（`format/topoId/weightSha`）：网络身份变了 ⇒ `V4Cache` 必须 miss。 */
    std::string cacheTag() const;

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
     *
     * <p>段 3b 起按 `arch` 分派：`topoId=2`（v4）走原表 + `tile.tile_id`；`topoId=3`（A−/T1）走
     * 卷积/轴向/joint 表。两表都是**闭集**：文件里出现任何一处没被 bind 的张量名 ⇒ 构造期报错
     * （=`finishPolicy` 的 `used==allNames`），⛔ 不静默忽略。
     */
    bool bindAll(std::string &err);
    /** `topoId=2` 的表（现有 v4 图 + 可缺的 `tile.tile_id`）。 */
    bool bindAllV4(std::string &err);
    /** `topoId=3` 的表（A−/T1：卷积 + 轴向 + joint/cross + 段嵌入）。 */
    bool bindAllA(std::string &err);

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

    /** `topoId=2` 的前向（现有 v4 图）—— `forwardAll` 按 `arch` 分派到它。 */
    bool forwardV4(const JVal &obs, std::map<std::string, std::vector<float>> &out,
                   std::string &err, const std::vector<float> *h0, V4Scratch *scr) const;
    /**
     * `topoId=3` 的前向（A−/T1）：tile 34 轴卷积 + 轴向注意力 · evt GRU + 2 层 k=3 卷积 ·
     * **95-token joint attention**（34 tile + 60 evt + `h_evt` 第 95 个）· cross(cand→95) ·
     * B3；heads 带 **danger → policy 的 0 参数固定通道**。
     */
    bool forwardA(const JVal &obs, std::map<std::string, std::vector<float>> &out,
                  std::string &err, const std::vector<float> *h0, V4Scratch *scr) const;

    /** 策略头 logits（逐候选，顺序 = `obs.legal`）= Java `V4Policy.logits`。 */
    bool logits(const JVal &obs, std::vector<float> &out, std::string &err,
                V4Scratch *scr = nullptr) const;
};

/**
 * 增量事件 carry 缓存的**只读读数**（自检 / CLI / 判据要看得见"到底命中没有"）。
 *
 * <p>⛔ 只有"增量 == 全量（逐位）"一条是**假绿**：缓存从不命中也永远相等 —— 所以命中计数
 * 与逐位比对**成对**出现（AGENTS §6.5）。
 */
struct V4CarryStats {
    long long decisions = 0;     /**< 走缓存路径的决策次数。 */
    long long hits = 0;          /**< 前缀校验通过 ⇒ 只推进新事件。 */
    long long cold = 0;          /**< 建槽（首见该小局 / 换小局）。 */
    long long stale = 0;         /**< 前缀对不上 ⇒ 丢槽整手重放。 */
    long long rowsReplayed = 0;  /**< 建槽时重放的事件行数。 */
    long long rowsEncoded = 0;   /**< 逐行编码（`event.enc` + GRU）的事件行数。 */
    long long rowsWindow = 0;    /**< 直接取窗口批编码行喂 GRU 的事件行数。 */
};

/** 增量事件 carry 的**进程级缺省**（`--no-v4-cache` 置 false；= Java `V4Policy.cacheEnabled`）。 */
bool v4CarryEnabledDefault();
/** 改进程级缺省（`main` 解析 `--no-v4-cache` 后调一次）。 */
void v4SetCarryEnabled(bool on);
/**
 * **负向判据注入**（`--v4-carry-bug skip1|stale|noclear`）：⛔ 只许判据用 —— 采集 / 训练里
 * 绝不能给（给了产出的轨迹就是错的）。未知模式 ⇒ false 且填 `err`（不静默忽略）。
 */
bool v4SetCarryBug(const std::string &mode, std::string &err);
/** 当前注入的模式名（没注入 = 空串）。 */
const char *v4CarryBugName();

/**
 * 小局身份（增量缓存的失效键）= Java `V4Features.handKey`：`场风-几局-本场@座位`。
 *
 * <p>`round` 缺失 ⇒ false（**不使用缓存**：宁可不省，也不能拿上一局的窗口当这一局的）。
 * 单独暴露出来是为了让判据能在**没有权重**的情况下钉住"换局必须换键"。
 */
bool v4CarryHandKeyOf(const JVal &obs, int &seat, std::string &key);

/** 读某个 scratch 上的增量 carry 计数器（没建过缓存 ⇒ 全 0）。 */
V4CarryStats v4CarryStatsOf(const V4Scratch &scr);
/** 复位计数器（判据要量"这一段"的命中率时用；**不动槽里的 carry**）。 */
void v4CarryResetStats(V4Scratch &scr);

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
    std::vector<float> tileIn;     // kKindCount × kCTile（加了 #0 `tile.tile_id` 之后的输入）
    std::vector<float> tileH;      // kKindCount × tileD（tile.enc 输出）
    std::vector<float> tileAt;     // kKindCount × tileD（tile.attn 输出）
    std::vector<float> eRows;      // kKEvt × dModel（event.enc 输出 = GRU 输入）
    std::vector<float> eConv;      // dModel × kKEvt（A− 的事件卷积：**通道在前**）
    std::vector<float> eTokens;    // kKEvt × dModel（Transformer 输出 / A− 的卷积输出）
    std::vector<float> carry;      // dModel（事件塔的 GRU 隐状态）
    std::vector<float> tok;        // kCEvt（一条事件 token 行）
    /** ★ P4（第一百一十六轮）：`evt` 首层 LN 的输入中转（len × kCEvt；掩码前后各一次）。 */
    std::vector<float> evtLnTmp;
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
    /** ★ S1（第一百一十六轮）：`fusion.evt_kv` 的 K/V（(kev+1) × 2·dModel）。 */
    std::vector<float> kvEvt;
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
    /** 只给**负向注入** `--v4-carry-bug noclear` 用的"上一行"缓冲（正常路径不碰它）。 */
    std::vector<float> bugTok;
    /** A− 的通用中转缓冲（卷积是"通道在前"、LN 是"特征在后" ⇒ 每层要转两次）。 */
    std::vector<float> aBuf;
    std::vector<float> aBuf2;

    /**
     * **增量事件 GRU carry**（默认开；`--no-v4-cache` 走进程级缺省置 false）。
     *
     * <p>为什么放在 scratch 而不是 `V4Policy`：权重是**多 worker 共享只读**的，缓存是**可变态**
     * ⇒ 挂在权重上就是数据竞争（与 `V4Scratch` 存在的理由同一条）。而 C++ 的策略实例本来就是
     * "每场每席一份"，所以"一份 scratch = 一个槽位集合"与 Java 的"按座位分槽"同义
     * （槽由 `V4CarryCache` 按 `obs.seat` 分，因为同一份 scratch 也可能被逐行回放工具用）。
     */
    bool carryEnabled = v4CarryEnabledDefault();
    std::shared_ptr<V4CarryCache> carryCache;
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
 * @param format 出参：1 = v3 定长 MLP（`net.hpp` 那条路），**3 = v4/A−（本文件）**
 * @return false 且填 `err`：魔数不对 / `format==2`（**A− 之前的老包** ⇒ 只有冻结快照能读，
 *         专用错误里写明冻结快照路径与 sha，⛔ 不自动升级）/ 其它不认识的版本号
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

/**
 * `trainer v4cache <net.bin> <轨迹1.jsonl|目录> … [--workers K] [--limit N] [--h0-rows N]`：
 * **增量 carry 的硬判据**（Java 侧没有对应物 —— 那一侧的判据是 `SelfTest.v4CacheTests`）。
 *
 * <p>逐条真实决策行上跑两条事件塔路径并**逐位**比对七个头 + `h_evt`：
 *   ① 增量 carry（缓存开）== 整手重放（缓存关）—— 这就是"增量 == 全量"；
 *   ② `h_evt` 的那一格同时就是"**缓存 carry == 整手重放**"（W1 起的第二条判据）；
 *   ③ 前 `--h0-rows` 行额外做"**前缀 carry + 窗口 == 整手重放**"（`forwardAll(h0)` 那条路）。
 *
 * <p>⛔ 只有"相等"是假绿 ⇒ 命中数 / 建槽 / 陈旧退回与**命中率**一起打印；命中率 0 ⇒ 退出码非 0。
 */
int v4CacheCli(int argc, char **argv);

}  // namespace trainer
