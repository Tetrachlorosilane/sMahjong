// **v4 观测 → 四张量**（`tile[34,48] / evt[60,96] / ctx[64] / cand[n,128]`）——
// 逐行镜像 Java `mahjong.ai.V4Features`（那一份是权威实现，已通过 golden 夹具对拍）。
//
// 为什么训练端要有一份：v4 权重（`net.bin` **格式 2**）要能在训练端直接跑
// （`--policy net:<v4 net.bin>,teacher,teacher,teacher`），而"训练输入 == 推理输入"这条
// 硬性质只有靠**逐元素对拍**才成立 —— 判据有三条，缺一条都等于没做：
//   ① `trainer v4golden <夹具>`（特征 + 前向 + 红证，对齐 Python 的 `blocks.assemble`）；
//   ② `trainer v4net` 与 `tools/V4Probe.java` 逐行比 logits（n / argmax / 逐元素 |Δ|）；
//   ③ `--policy net:<v4>` 能在自对弈里跑起来（`policies.hpp` 按 format 分派）。
//
// ⚠ 三条纪律（照抄 Java 的原话）：
//   · **布局表就是契约**：下面几张表的顺序与宽度与 Java **一字不差**，它同时是块的宽度契约
//     （消融开关）；改宽度 = 改 featureVersion，老权重构造期就会被拒。
//   · **派生量只从 `obffeatures.hpp` 读**（那是 Java `ObsFeatures`/`Danger`/`HandEval` 的镜像，
//     已按 sidecar 逐字节对拍过）—— 本文件不许再写第二套危险度/向听/进张。
//   · **认不出的输入报错，不猜**：obs 版本 < 3（老轨迹没有 `events`/`riichi_turn`）与未知块 id
//     一律 `err` 出去；**绝不静默填 0**（填 0 = 悄悄少给信息，训出来才发现）。
//
// 两个已踩过的坑（Java 侧的原注释，这里照抄做法）：
//   ① `hand_red` 在 obs JSON 里是**布尔数组**（"这个牌种有没有赤五"），不是计数 ——
//      用 `ints()` 去读会整列变 0（2026-09-27 对拍抓到：`tile[k][1]` 整列差 1.0）；
//   ② 四家块**旋转到自己为下标 0**（`(abs - seat) % 4`）；逐家 sidecar 段的
//      下标 0 = 下家 / 1 = 対面 / 2 = 上家（`kSeatCount` 那一套）。
#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

#include "jsonscan.hpp"
#include "tiles.hpp"

namespace trainer {

/** 张量布局版本（= Java `V4Features.FEATURE_VERSION` / Python `spec.FEATURE_VERSION_V4`）。 */
inline constexpr int kV4FeatureVersion = 4;
/** 需要的 obs 版本（v4 要事件流与逐张属性；= Java `V4Features.OBS_VERSION`）。 */
inline constexpr int kV4ObsVersion = 3;

// 张量形状（= Java `V4Features.C_TILE/C_EVT/C_CTX/C_CAND/K_EVT`，与 Python `spec` 逐字对应）
inline constexpr int kCTile = 48;
inline constexpr int kCEvt = 96;
inline constexpr int kCCtx = 64;
inline constexpr int kCCand = 128;
inline constexpr int kKEvt = 60;

/** 候选段：v3 的基础 88 + 派生 11（**v4 新 3 维目前恒 0**）+ 预留 29。 */
inline constexpr int kCandBase = 88;
inline constexpr int kCandDerived = 11;
inline constexpr int kCandReserved = kCCand - kCandBase - kCandDerived;

inline constexpr int kNPlayersV4 = 4;

// ---------------------------------------------------------------- 布局表
//
// ⚠ 下面这几张表**就是契约**（`docs/FEATURES-V4.md` §4 的逐通道表）：定义在 .cpp，
//   在这里 `extern` 出来供自检/工具按同一份顺序核对（Java 侧也是 public 的，同一条理由）。
/** `tile` 的通道分组（顺序即内存布局；宽度合计必须 == `kCTile`）。 */
extern const char *const kTileChannels[26];
extern const int kTileWidths[26];
/** `evt` 的字段布局（顺序即内存布局）。 */
extern const char *const kEvtFields[13];
extern const int kEvtWidths[13];
/** 事件类型字符串（one-hot 顺序；`draw/agari/ryuukyoku` 服务端不发，但槽位留着）。 */
extern const char *const kEvtTypes[8];
/** 副露类型（与 `Tiles`/Python `MELD_KINDS` 同序）。 */
extern const char *const kMeldKindsV4[5];
/** `ctx` 的通道分组。 */
extern const char *const kCtxGroups[7];
extern const int kCtxWidths[7];
/** 块注册表（**顺序 = 规范里的块顺序**；`net.bin` 格式 2 的清单据此核对）。 */
extern const char *const kBlockIds[16];
extern const int kBlockWidths[16];
/** 场风字母表（obs 里是字母，**不是数字**）。 */
extern const char *const kWinNotesV4[2];

/** 一次决策的四张量（`legal` 与 `cand` 的行序一一对应）= Java `V4Features.Tensors`。 */
struct V4Tensors {
    std::array<std::array<float, kCTile>, kKindCount> tile{};
    std::array<std::array<float, kCEvt>, kKEvt> evt{};
    std::array<float, kCCtx> ctx{};
    std::vector<std::array<float, kCCand>> cand;
    /** obs 的 `legal`（顺序 = `cand` 的行序）。 */
    std::vector<std::string> legal;
};

/** 块 id → 注册表下标；未注册返回 -1（= Java `V4Features.indexOfBlock`）。 */
int v4IndexOfBlock(const std::string &bid);

/** 块 id 列表 → 宽度列表（加载器核对文件里的清单用）= Java `V4Features.widthsOf`。 */
bool v4WidthsOf(const std::vector<std::string> &ids, std::vector<int> &out, std::string &err);

/** 块清单指纹（与 Java/Python **逐字节同算法**：`[[id,width],…]` 的 sha256 前 16 位十六进制）。 */
std::string v4Fingerprint();
/** **任意一份块清单**的指纹（`net.bin` 格式 2 里存的就是它）= Java `fingerprint(ids, widths)`。 */
std::string v4Fingerprint(const std::vector<std::string> &ids, const std::vector<int> &widths);

/**
 * 按 obs 拼四张量；`ablate` 里的块**整块置 0**（规范 §7）。
 *
 * @return false 且填 `err`：obs 版本不够 / 未知块 id / obs 形状不对（**不静默降级**）
 */
bool v4Assemble(const JVal &obs, const std::vector<std::string> &ablate, V4Tensors &out,
                std::string &err);

/**
 * 事件流（只保留**对象**元素，保持原顺序）= Java `V4Features.eventsOf`。
 *
 * <p>给 `V4Policy` 的"整手 carry"用：它要按顺序重放 `events[0, n-K)`，而 `v4Assemble` 只交出
 * 尾部 K 行的张量。返回的指针指向 `obs` 内部，**obs 必须活得比它久**。
 */
std::vector<const JVal *> v4EventsOf(const JVal &obs);

/**
 * **一条事件**的 token 行（`eventMatrix` 的逐行版本 = Java `V4Features.eventRow`）。
 *
 * <p>`row` 由调用方提供（长度 `kCEvt`）。⚠ **本函数自带清零**：调用方逐事件重放时会复用同一支
 * 缓冲，不清零就是"前 i 条事件按位或"的静默累积（Java 侧刚修过这条）。
 *
 * @return false 且填 `err`：事件字段布局表不自洽（不猜）
 */
bool v4EventRow(const JVal &e, int seat, float *row, std::string &err);

/** 布局自述（日志用，与 Java `V4Features.describe()` 同义）。 */
std::string v4FeatureLayout();

}  // namespace trainer
