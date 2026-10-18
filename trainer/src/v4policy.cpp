// v4 前向与加载 —— 逐句移植 Java `mahjong.ai.V4Policy`（+ `NetWeights` 的分派口）。
//
// 纪律（与 `net.cpp` 同源）：
//   · **算子必须与 Java 同序**：单累加器、正序、`float` —— 编译已加 `-ffp-contract=off`（禁 FMA），
//     否则最后一位舍入会变、argmax 会翻；
//   · `linear` **别名安全**（Java 踩过：`linear(h,h,…)` 原地覆盖 ⇒ logits 整体偏 0.0044）；
//   · `gruStep` **返回新数组**（`gh` 要读整条旧 `h`）；
//   · 加载期就把"少张量 / 多张量 / 改名 / 形状不符 / 块清单乱序 / 指纹不符 / 尾部多字节"全部炸掉，
//     不留到对局里算出个奇怪的牌（= Java 构造器 + `bindAll` 的做法）。
#include "v4policy.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <limits>
#include <map>
#include <mutex>
#include <thread>

#if defined(__AVX2__)
#include <immintrin.h>
#endif

#include "net.hpp"        // netArgmax（= Java `Logits.argmaxOf`）
#include "sha256.hpp"     // `sha256Hex`（权重整段字节的摘要 —— `V4Cache` 槽键的一部分）
#include "v4features.hpp"

namespace trainer {
namespace {

/**
 * **标量口径开关**（诊断 / 对拍用）：`TRAINER_V4_SCALAR=1` 时禁用 SIMD 与 token 批处理，
 * 走与本次优化之前**逐字相同**的标量双循环（但保留"零分配 + 权重单副本"）。
 *
 * <p>两个用途，都写进 docs/TRAINER-CPP.md：
 *   ① **分档归因**：同一份代码开关一次就能量出"档 1（零分配/权重单副本）"与"档 2（SIMD +
 *      批处理）"各占多少，不用维护两份源码；
 *   ② **位级判据的负向对照**：两条路必须给出同一批比特 —— 判据是
 *      `trainer v4net` 的输出逐字节相同。
 */
const bool kScalarMode = std::getenv("TRAINER_V4_SCALAR") != nullptr;
constexpr float kLnEps = 1e-5f;

// ---------------------------------------------------------------- A−（topoId=3）的结构常量
//
// 唯一规格 = `python/mahjong_ml/v4/model.py`（Python 是语义权威）。这些值**不是超参**：
// arch 段里若写了别的（或写 ReLU/post-LN）⇒ 加载期就报错（"文件在说谎"）。
/** 段嵌入的 3 段（`N_SEG` / `SEG_TILE, SEG_EVT, SEG_MEM`）。 */
constexpr int kNSeqA = 3;
constexpr int kSegTile = 0;
constexpr int kSegEvt = 1;
constexpr int kSegMem = 2;
/** 95 = 34 tile + 60 evt + 1 `h_evt`（第 95 个 token）。 */
constexpr int kJointTokens = kKindCount + kKEvt + 1;
/** `danger` 进 policy 的**0 参数固定通道**（`AHeads.forward`：`logit += COEF·log(mean_k σ(d)+eps)`）。 */
constexpr float kDangerCoef = 1.0f;
constexpr float kDangerEps = 1e-6f;
/**
 * `AMlp` 的**死维/恒等维首层掩码**（`model.py` 的 `CTX_DEAD_DIMS` / `CAND_DEAD_DIMS`）。
 *
 * <p>⚠ 这两个表**不在 `net.bin` 里**（`persistent=False` 的 buffer），但它是 A− 前向的一部分：
 * Python 在 LN **前后各掩一次** ⇒ 这些列对 `fc1` 的输入恒 0。C++ 不掩 ⇒ 首层读到的是
 * `LN(bias)` 而不是 0 ⇒ 与 Python 分叉（不是"少一点信息"，是**另一个函数**）。
 * 判据 = G2 的逐行 argmax/max|Δ|。
 */
constexpr int kCtxDeadDims[] = {17, 35, 37, 58, 59, 60, 61, 62, 63};
constexpr int kCandDeadDimsFirst = 96;     // = 96..127（`C_CAND` 的后 32 列）

/** 小端读取器：**显式按字节拼**（不依赖宿主字节序，也不做未对齐访问）。 */
struct Reader {
    const uint8_t *p = nullptr;
    size_t n = 0;
    size_t pos = 0;
    bool bad = false;

    bool need(size_t k) {
        if (bad || pos + k > n) {
            bad = true;
            return false;
        }
        return true;
    }
    uint8_t u8() {
        if (!need(1)) {
            return 0;
        }
        return p[pos++];
    }
    uint16_t u16() {
        if (!need(2)) {
            return 0;
        }
        const uint16_t v = static_cast<uint16_t>(static_cast<uint16_t>(p[pos])
                                                | (static_cast<uint16_t>(p[pos + 1]) << 8));
        pos += 2;
        return v;
    }
    uint32_t u32() {
        if (!need(4)) {
            return 0;
        }
        const uint32_t v = static_cast<uint32_t>(p[pos]) | (static_cast<uint32_t>(p[pos + 1]) << 8)
                | (static_cast<uint32_t>(p[pos + 2]) << 16)
                | (static_cast<uint32_t>(p[pos + 3]) << 24);
        pos += 4;
        return v;
    }
    int32_t i32() { return static_cast<int32_t>(u32()); }
    float f32() {
        const uint32_t bits = u32();
        float v = 0.f;
        std::memcpy(&v, &bits, sizeof(v));
        return v;
    }
    void bytes(void *dst, size_t k) {
        if (!need(k)) {
            return;
        }
        std::memcpy(dst, p + pos, k);
        pos += k;
    }
    size_t remaining() const { return bad ? 0 : n - pos; }
};

bool readWholeFile(const std::string &path, std::vector<uint8_t> &out, std::string &err) {
    std::FILE *f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) {
        err = "打不开权重文件：" + path;
        return false;
    }
    out.clear();
    uint8_t buf[65536];
    for (;;) {
        const size_t got = std::fread(buf, 1, sizeof(buf), f);
        if (got > 0) {
            out.insert(out.end(), buf, buf + got);
        }
        if (got < sizeof(buf)) {
            break;
        }
    }
    std::fclose(f);
    return true;
}

std::string joinNames(const std::vector<std::string> &names, size_t limit) {
    std::string s = "[";
    for (size_t i = 0; i < names.size() && i < limit; i++) {
        if (i > 0) {
            s += ", ";
        }
        s += names[i];
    }
    s += "]";
    return s;
}

/**
 * 建 `dataT`（转置 + 行数补到 8 的倍数）—— **加载期一次**，之后只读。
 *
 * <p>为什么不懒建：懒建就要在热路径上加锁/原子，而一份权重是**全进程共享只读**的
 * （`policies.hpp` 的工厂把同一个 `shared_ptr<const V4Policy>` 发给所有 worker）。
 * 内存代价 = 每份网络多一份 float32 副本（g08：5.4 MB → 10.9 MB）；换来的是前向里
 * 权重按 `c` 升序**流式**读、8 个输出行一条指令（见 `linearFast`）。
 */
void buildTransposed(V4Policy &p) {
    for (auto &kv : p.mats) {
        V4Policy::Mat &m = kv.second;
        const int R = m.rows;
        const int C = m.cols;
        const int Rp = (R + 7) & ~7;
        m.rowsPad = Rp;
        m.dataT.assign(static_cast<size_t>(C) * static_cast<size_t>(Rp), 0.f);
        for (int r = 0; r < R; r++) {
            const float *src = m.data.data() + static_cast<size_t>(r) * static_cast<size_t>(C);
            for (int c = 0; c < C; c++) {
                m.dataT[static_cast<size_t>(c) * static_cast<size_t>(Rp) + static_cast<size_t>(r)]
                        = src[c];
            }
        }
    }
}

/** 构造收尾：形状核对（`bindAll`）+ "有张量没人读"（= Java 构造器末尾那两件事）。 */
bool finishPolicy(V4Policy &p, std::string &err) {
    p.used.clear();
    if (!p.bindAll(err)) {
        return false;
    }
    buildTransposed(p);
    if (p.used.size() != p.allNames.size()) {
        std::vector<std::string> unused;
        for (const std::string &n : p.allNames) {
            if (p.used.find(n) == p.used.end()) {
                unused.push_back(n);
            }
        }
        std::sort(unused.begin(), unused.end());
        err = "权重里有 " + std::to_string(unused.size()) + " 个张量没有任何层读它："
              + joinNames(unused, 6) + " —— 导出器与本前向不同版本";
        return false;
    }
    // A− 的固定 sin/cos 位置编码（0 参数、非持久）—— 只有这张图才有；在加载期算一次。
    if (p.arch == kTopoA) {
        p.peEvt.assign(static_cast<size_t>(kKEvt) * static_cast<size_t>(p.dModel), 0.f);
        const int d = p.dModel;
        for (int n = 0; n < kKEvt; n++) {
            for (int i = 0; i < d; i += 2) {
                // `div = exp(-ln(10000) · i / d)`，i = 0,2,4,…（= `_sincos_pe`；全程 float32）
                const float div = std::exp(-std::log(10000.0f) * static_cast<float>(i)
                                           / static_cast<float>(d));
                const float pos = static_cast<float>(n);
                p.peEvt[static_cast<size_t>(n) * d + i] = std::sin(pos * div);
                if (i + 1 < d) {
                    p.peEvt[static_cast<size_t>(n) * d + (i + 1)] = std::cos(pos * div);
                }
            }
        }
    }
    return true;
}

// ---------------------------------------------------------------- 算子（顺序固定 = 与 Java 逐位可比）

/**
 * `out[r] = (b ? b[rowOff + r] : 0) + Σ_c W[rowOff + r][c]·x[c]`（`r < R`）——
 * **逐位等于**原来的标量双循环。
 *
 * <p>口径（⛔ 改这里等于改判据，见 `Mat::dataT` 与文件顶部纪律）：
 *   · 一个输出行 = **一个累加器**，沿 `c` **升序**；`b` 非空时累加器**从 bias 起**
 *     （与原 `linear` 一致）；`b == nullptr` 时从 0 起（= 原 `rowDot`，`gruStep` 要那一支：
 *     `bias + rowDot(...)` 与 `linear` 的"bias 起累加"**不是**同一个浮点序列）。
 *   · 乘、加**分两步**（`_mm256_mul_ps` + `_mm256_add_ps`），**不用 FMA**：FMA 只舍入一次，
 *     与标量的两次舍入不同位（`-ffp-contract=off` 的同一条理由）。
 *   · SIMD 只铺在**输出行**方向（8 行/指令）⇒ 每个输出行的求和顺序与标量逐字相同。
 *
 * <p>为什么快：`W` 在加载期已转置（`dataT`），内层循环沿 `c` 流式读权重、一次算 8 个输出行；
 * 再把输出行 4 路展开（4 条互相独立的累加链）⇒ 单核 matvec 从"每 MAC 2 条标量指令"降到
 * "每 8 个 MAC 2 条向量指令"，而**数值口径一位不变**。
 */
void linearFast(float *out, const float *x, size_t xn, const V4Policy::Mat &w, const float *b,
                int rowOff, int R) {
    int r = 0;
#if defined(__AVX2__)
    if (!kScalarMode && !w.dataT.empty() && static_cast<int>(xn) == w.cols) {
    const int Rp = w.rowsPad;
    const float *WT = w.dataT.data() + static_cast<size_t>(rowOff);
    for (; r + 32 <= R; r += 32) {                       // 4 路展开：4 条独立累加链
        __m256 a0 = b == nullptr ? _mm256_setzero_ps() : _mm256_loadu_ps(b + rowOff + r);
        __m256 a1 = b == nullptr ? _mm256_setzero_ps() : _mm256_loadu_ps(b + rowOff + r + 8);
        __m256 a2 = b == nullptr ? _mm256_setzero_ps() : _mm256_loadu_ps(b + rowOff + r + 16);
        __m256 a3 = b == nullptr ? _mm256_setzero_ps() : _mm256_loadu_ps(b + rowOff + r + 24);
        const float *w0 = WT + r;
        const float *w1 = w0 + 8;
        const float *w2 = w0 + 16;
        const float *w3 = w0 + 24;
        for (size_t c = 0; c < xn; c++) {
            const __m256 xv = _mm256_set1_ps(x[c]);
            a0 = _mm256_add_ps(a0, _mm256_mul_ps(_mm256_loadu_ps(w0), xv));
            a1 = _mm256_add_ps(a1, _mm256_mul_ps(_mm256_loadu_ps(w1), xv));
            a2 = _mm256_add_ps(a2, _mm256_mul_ps(_mm256_loadu_ps(w2), xv));
            a3 = _mm256_add_ps(a3, _mm256_mul_ps(_mm256_loadu_ps(w3), xv));
            w0 += Rp;
            w1 += Rp;
            w2 += Rp;
            w3 += Rp;
        }
        _mm256_storeu_ps(out + r, a0);
        _mm256_storeu_ps(out + r + 8, a1);
        _mm256_storeu_ps(out + r + 16, a2);
        _mm256_storeu_ps(out + r + 24, a3);
    }
    for (; r + 8 <= R; r += 8) {                         // 1 路：8 个输出行
        __m256 a0 = b == nullptr ? _mm256_setzero_ps() : _mm256_loadu_ps(b + rowOff + r);
        const float *w0 = WT + r;
        for (size_t c = 0; c < xn; c++) {
            a0 = _mm256_add_ps(a0, _mm256_mul_ps(_mm256_loadu_ps(w0), _mm256_set1_ps(x[c])));
            w0 += Rp;
        }
        _mm256_storeu_ps(out + r, a0);
    }
    }
#endif
    for (; r < R; r++) {                                 // 尾部（R%8）、标量档与无 AVX2 整机回退
        float s = b == nullptr ? 0.f : b[rowOff + r];
        const float *row
                = w.data.data() + static_cast<size_t>(rowOff + r) * static_cast<size_t>(w.cols);
        for (size_t c = 0; c < xn; c++) {
            s += row[c] * x[c];
        }
        out[r] = s;
    }
}

/**
 * **一批 token 行共用一个权重矩阵**（`B` 行一起算）—— 与逐行 `linearFast` **逐位相同**。
 *
 * <p>唯一的区别是**循环次序**：输出行按 32 行分块放在外层、批内 token 放在内层 —— 于是那 32 行的
 * 权重切片（`xn × 32 × 4 B` ≈ 24 KB @ xn=192）常驻 L1，被 `B` 个 token 复用，权重流量从
 * **`B × |W|` 降到 `|W|`**。
 *
 * <p>为什么这条比"少几条指令"重要得多：8 worker 共享 L3，而本前向每决策要扫 ~180 MB 权重
 * （g08 的权重集只有 10.9 MB ⇒ 同一批 cacheline 被反复搬运）。批处理把每次调用扫过的权重
 * 从 `B` 遍压到 1 遍；实测这一步在 8 worker 下的收益**显著大于**单线程下的收益
 * （docs/TRAINER-CPP.md 的实测表）。
 */
void linearBatchFast(float *out, int outStride, const float *x, int xStride, int B, size_t xn,
                     const V4Policy::Mat &w, const float *b, int rowOff, int R) {
    if (!kScalarMode && !w.dataT.empty() && static_cast<int>(xn) == w.cols) {
        constexpr int kBlock = 32;                       // 4 个 8 行组 ⇒ 切片 ≈ L1
        for (int rb = 0; rb < R; rb += kBlock) {
            const int len = std::min(kBlock, R - rb);
            for (int bi = 0; bi < B; bi++) {
                linearFast(out + static_cast<size_t>(bi) * outStride + rb,
                           x + static_cast<size_t>(bi) * xStride, xn, w,
                           b == nullptr ? nullptr : b, rowOff + rb, len);
            }
        }
        return;
    }
    for (int bi = 0; bi < B; bi++) {                     // 标量档 / 未建 dataT：逐行同原口径
        const float *xb = x + static_cast<size_t>(bi) * xStride;
        float *ob = out + static_cast<size_t>(bi) * outStride;
        for (int r = 0; r < R; r++) {
            float s = b == nullptr ? 0.f : b[rowOff + r];
            const float *row = w.data.data()
                    + static_cast<size_t>(rowOff + r) * static_cast<size_t>(w.cols);
            for (size_t c = 0; c < xn; c++) {
                s += row[c] * xb[c];
            }
            ob[r] = s;
        }
    }
}

// ---- 逐元素算子（都是**下标升序、单累加器**，与原来的 `std::vector` 版逐位同值；
//      改成裸指针只是为了让中间量落在 `V4Scratch` 的扁平缓冲里 —— 热路径零堆分配）

void reluBuf(float *x, size_t n) {
    for (size_t i = 0; i < n; i++) {
        if (x[i] < 0.f) {
            x[i] = 0.f;
        }
    }
}

/**
 * **erf 版 GELU**（`F.gelu(x, approximate="none")`）—— A− 全塔唯一的激活。
 *
 * <p>⛔ 不许换成 tanh 近似、也不许换成 ReLU（`A-REFACTOR-IMPL-PLAN.md` 风险 8：tanh 近似的
 * 末位就不同 ⇒ 长 carry 行逐层放大；而 ReLU 会造假零、判据 1 的 `frac(u==0)<10%` 当场红）。
 * <p>口径：`0.5·x·(1+erf(x/√2))`，`std::erf` 走 `double` 再降 float（与 torch 的 float32
 * erf 差在 1 ulp 量级；三端判据是 `max|Δ|`，不是逐位）。
 */
void geluBuf(float *x, size_t n) {
    // ⚠ 全程 **float32**（`std::erf(float)` 返回 float）：torch 的 `F.gelu(approximate="none")`
    //   在 float32 张量上就是这么算的 —— 用 double 反而会与它在最后一位分叉（我们比的是
    //   `max|Δ|`，不是"谁的 erf 更准"）。
    constexpr float kInvSqrt2 = 0.70710678118654752440f;
    for (size_t i = 0; i < n; i++) {
        const float v = x[i];
        x[i] = 0.5f * v * (1.f + std::erf(v * kInvSqrt2));
    }
}

void sigmoidBuf(float *x, size_t n) {
    for (size_t i = 0; i < n; i++) {
        const double e = std::exp(-static_cast<double>(x[i]));
        x[i] = static_cast<float>(1.0 / (1.0 + e));
    }
}

void tanhBuf(float *x, size_t n) {
    for (size_t i = 0; i < n; i++) {
        x[i] = static_cast<float>(std::tanh(static_cast<double>(x[i])));
    }
}

/** 原地 LayerNorm（**有偏方差**，eps = 1e-5，与 torch 缺省一致）。 */
void layerNormBuf(float *x, int n, const float *g, const float *b) {
    float mean = 0.f;
    for (int i = 0; i < n; i++) {
        mean += x[i] / static_cast<float>(n);
    }
    float var = 0.f;
    for (int i = 0; i < n; i++) {
        const float d = x[i] - mean;
        var += d * d / static_cast<float>(n);
    }
    const float inv = static_cast<float>(1.0 / std::sqrt(static_cast<double>(var + kLnEps)));
    for (int i = 0; i < n; i++) {
        x[i] = (x[i] - mean) * inv * g[i] + b[i];
    }
}

/** 原地 softmax（减去最大值再 exp）—— 只动前 `n` 个。 */
void softmaxBuf(float *x, int n) {
    float max = -std::numeric_limits<float>::infinity();
    for (int i = 0; i < n; i++) {
        if (x[i] > max) {
            max = x[i];
        }
    }
    float sum = 0.f;
    for (int i = 0; i < n; i++) {
        x[i] = static_cast<float>(std::exp(static_cast<double>(x[i] - max)));
        sum += x[i];
    }
    if (sum > 0.f) {
        for (int i = 0; i < n; i++) {
            x[i] /= sum;
        }
    }
}

/** `Σ a[i]·b[i]`（从 0 起、下标升序 —— 与原来的 `dot` 同一个浮点序列）。 */
float dotN(const float *a, const float *b, int n) {
    float s = 0.f;
    for (int i = 0; i < n; i++) {
        s += a[i] * b[i];
    }
    return s;
}

/**
 * `out = W·x + b`（W 行主序 `[out][in]`，**单累加器顺序求和** —— 别改成并行规约）。
 *
 * <p>⚠ **别名安全**：`out` 与 `x` 是同一块内存时先复制一份（Java `linear` 的同名注意事项）。
 *
 * <p>走了 `dataT` 那条 SIMD 路径（逐位等价，见 `linearFast`）；`dataT` 没建（未经 `finishPolicy`
 * 的 `Mat`）或 `xn != w.cols` 时退回原来的标量循环 —— 两条路必须给出同一批比特。
 */
void linear(std::vector<float> &out, const float *x, size_t xn, const V4Policy::Mat &w,
            const std::vector<float> &b) {
    std::vector<float> copy;
    if (out.data() == x) {
        copy.assign(x, x + xn);
        x = copy.data();
    }
    if (!w.dataT.empty() && static_cast<int>(xn) == w.cols) {
        linearFast(out.data(), x, xn, w, b.data(), 0, static_cast<int>(out.size()));
        return;
    }
    for (size_t r = 0; r < out.size(); r++) {
        float s = b[r];
        const float *row = w.data.data() + r * static_cast<size_t>(w.cols);
        for (size_t c = 0; c < xn; c++) {
            s += row[c] * x[c];
        }
        out[r] = s;
    }
}

}  // namespace

// ---------------------------------------------------------------- 取张量

const V4Policy::Mat *V4Policy::mat(const std::string &name, int rows, int cols,
                                   std::string &err) const {
    const auto it = mats.find(name);
    if (it == mats.end()) {
        err = "权重缺张量：" + name;
        return nullptr;
    }
    const Mat &m = it->second;
    if (m.rows != rows || (rows > 0 && m.cols != cols)) {
        err = "张量 " + name + " 形状 [" + std::to_string(m.rows) + ","
              + std::to_string(m.rows > 0 ? m.cols : 0) + "] != [" + std::to_string(rows) + ","
              + std::to_string(cols) + "]";
        return nullptr;
    }
    return &m;
}

const std::vector<float> *V4Policy::vec(const std::string &name, int n, std::string &err) const {
    const auto it = vecs.find(name);
    if (it == vecs.end()) {
        err = "权重缺张量：" + name;
        return nullptr;
    }
    if (static_cast<int>(it->second.size()) != n) {
        err = "张量 " + name + " 长度 " + std::to_string(it->second.size()) + " != "
              + std::to_string(n);
        return nullptr;
    }
    return &it->second;
}

/**
 * 把每一层要用的张量都取一遍（= Java `V4Policy.bindAll`）—— 前向要把每个张量读一遍，
 * 读的时候顺手钉住形状，最后 `used` 与文件里的名字集合比对。
 *
 * <p>段 3b 起按 `arch` 分派（两套表都是**闭集**，多余张量由 `finishPolicy` 抓）。
 */
bool V4Policy::bindAll(std::string &err) {
    if (arch == kTopoA) {
        return bindAllA(err);
    }
    if (arch == kTopoV4) {
        return bindAllV4(err);
    }
    err = "内部错误：未设置 arch（加载器必须先定 topoId）";
    return false;
}

bool V4Policy::bindAllV4(std::string &err) {
    used.clear();
    const int dm = dModel;
    const int td = tileD;
    const int vb = valueBins;
    const int hd = 3 * dm;
    // 这两个包装只多做一件事：**记下读过的张量名**（前向路径里不记 —— 见 hpp 的线程安全说明）
    auto M = [&](const char *name, int rows, int cols) {
        if (mat(name, rows, cols, err) != nullptr) {
            used.insert(name);
        }
    };
    auto V = [&](const char *name, int n) {
        if (vec(name, n, err) != nullptr) {
            used.insert(name);
        }
    };
    M("tile.enc.0.weight", td, kCTile);
    V("tile.enc.0.bias", td);
    M("tile.enc.2.weight", td, td);
    V("tile.enc.2.bias", td);
    M("tile.attn.in_proj_weight", 3 * td, td);
    V("tile.attn.in_proj_bias", 3 * td);
    M("tile.attn.out_proj.weight", td, td);
    V("tile.attn.out_proj.bias", td);
    V("tile.norm.weight", td);
    V("tile.norm.bias", td);
    M("tile.proj.weight", dm, td);
    V("tile.proj.bias", dm);

    M("event.enc.0.weight", dm, kCEvt);
    V("event.enc.0.bias", dm);
    M("event.enc.2.weight", dm, dm);
    V("event.enc.2.bias", dm);
    M("event.cell.weight_ih", 3 * dm, dm);
    M("event.cell.weight_hh", 3 * dm, dm);
    V("event.cell.bias_ih", 3 * dm);
    V("event.cell.bias_hh", 3 * dm);
    M("event.tr.layers.0.self_attn.in_proj_weight", hd, dm);
    V("event.tr.layers.0.self_attn.in_proj_bias", hd);
    M("event.tr.layers.0.self_attn.out_proj.weight", dm, dm);
    V("event.tr.layers.0.self_attn.out_proj.bias", dm);
    M("event.tr.layers.0.linear1.weight", 2 * dm, dm);
    V("event.tr.layers.0.linear1.bias", 2 * dm);
    M("event.tr.layers.0.linear2.weight", dm, 2 * dm);
    V("event.tr.layers.0.linear2.bias", dm);
    V("event.tr.layers.0.norm1.weight", dm);
    V("event.tr.layers.0.norm1.bias", dm);
    V("event.tr.layers.0.norm2.weight", dm);
    V("event.tr.layers.0.norm2.bias", dm);

    M("ctx.net.0.weight", dm, kCCtx);
    V("ctx.net.0.bias", dm);
    M("ctx.net.2.weight", dm, dm);
    V("ctx.net.2.bias", dm);
    M("cand.net.0.weight", dm, kCCand);
    V("cand.net.0.bias", dm);
    M("cand.net.2.weight", dm, dm);
    V("cand.net.2.bias", dm);

    M("fusion.tile_proj.weight", dm, td);
    V("fusion.tile_proj.bias", dm);
    M("fusion.q_tile.in_proj_weight", hd, dm);
    V("fusion.q_tile.in_proj_bias", hd);
    M("fusion.q_tile.out_proj.weight", dm, dm);
    V("fusion.q_tile.out_proj.bias", dm);
    M("fusion.q_evt.in_proj_weight", hd, dm);
    V("fusion.q_evt.in_proj_bias", hd);
    M("fusion.q_evt.out_proj.weight", dm, dm);
    V("fusion.q_evt.out_proj.bias", dm);
    M("fusion.gate.weight", dm, 2 * dm);
    V("fusion.gate.bias", dm);
    M("fusion.write.weight", dm, dm);
    V("fusion.write.bias", dm);
    M("fusion.film.weight", dm, dm);
    V("fusion.film.bias", dm);
    M("fusion.out.net.0.weight", dm, 3 * dm);
    V("fusion.out.net.0.bias", dm);
    M("fusion.out.net.2.weight", dm, dm);
    V("fusion.out.net.2.bias", dm);

    // 长程记忆门控（W1，第六十轮）：`fusion.mem.weight [dm,dm]` + `fusion.mem.bias [dm]`。
    //   **可缺** —— v1.14.1 及以前训练出来的网里没有它 ⇒ 补 0 ⇒ `g ≡ 1` ⇒ 旧网逐位不变。
    //   ⚠ 必须**在绑定期**把名字记进 `used`：`finishPolicy` 的"有张量没人读"校验只看这里
    //     （前向路径按 hpp 的线程安全说明**不写**任何状态），漏了就是"有 mem 的网加载期就报错"。
    //   ⚠ "缺张量"与"形状不符"是两件事：形状不符必须炸掉（= Java `bindMemGate` 抛异常），
    //     所以直接查表，而不是用 `mat()` / `vec()` 的 `err` 探测（那会把形状错误当成"没有"）。
    {
        const auto mw = mats.find("fusion.mem.weight");
        if (mw != mats.end()) {
            if (mw->second.rows != dm || (dm > 0 && mw->second.cols != dm)) {
                err = "张量 fusion.mem.weight 形状 [" + std::to_string(mw->second.rows) + ","
                      + std::to_string(mw->second.rows > 0 ? mw->second.cols : 0) + "] != ["
                      + std::to_string(dm) + "," + std::to_string(dm) + "]";
                return false;
            }
            used.insert("fusion.mem.weight");
        }
        const auto mb = vecs.find("fusion.mem.bias");
        if (mb != vecs.end()) {
            if (static_cast<int>(mb->second.size()) != dm) {
                err = "张量 fusion.mem.bias 长度 " + std::to_string(mb->second.size()) + " != "
                      + std::to_string(dm);
                return false;
            }
            used.insert("fusion.mem.bias");
        }
    }

    // ★ B3（第一百零四轮）：`u` 的 LN + 零初始化逐维残差门 —— 三组**可缺**张量（各 `[dm]`）。
    //   `fusion.u_norm.weight` / `fusion.u_norm.bias` / `fusion.u_res`；缺 ⇒ `tanh(0)=0` ⇒ 跳过整段
    //   ⇒ 旧网逐位不变（与 `fusion.mem` 同一兼容套路）。⚠ 形状不符必须炸掉，所以查表不用 `vec()`。
    {
        for (const char *n : {"fusion.u_norm.weight", "fusion.u_norm.bias", "fusion.u_res"}) {
            const auto it = vecs.find(n);
            if (it == vecs.end()) {
                continue;
            }
            if (static_cast<int>(it->second.size()) != dm) {
                err = std::string("张量 ") + n + " 长度 " + std::to_string(it->second.size())
                      + " != " + std::to_string(dm);
                return false;
            }
            used.insert(n);
        }
    }

    // ⚠ **policy 宽度兼容**（第五十五轮定稿）：
    //   · `[1, dm]` = 正常（第五十五轮起的门控版、以及第一季之前的旧网）；
    //   · `[1, dm+3]` = 第五十轮那版"拼接 sigmoid(belief_tenpai)" —— **截断**掉后 3 列。
    //     截断不是权宜近似：那 3 列乘的是**逐行常数**（同一行候选共用同一组 bt）⇒ 对
    //     argmax/softmax 无贡献（红证见 NOTES §6.5）。于是三种网共用同一条前向代码。
    {
        const Mat *w = mat("heads.policy.weight", 1, dm, err);
        if (w == nullptr && !err.empty()) {
            err.clear();                        // 不是正常宽 ⇒ 再试第五十轮那版
            const Mat *old = mat("heads.policy.weight", 1, dm + 3, err);
            if (old != nullptr) {
                Mat cut;
                cut.rows = 1;
                cut.cols = dm;
                cut.data.assign(static_cast<size_t>(dm), 0.f);
                for (int j = 0; j < dm; j++) {
                    cut.data[static_cast<size_t>(j)] = old->data[static_cast<size_t>(j)];
                }
                mats["heads.policy.weight"] = std::move(cut);
                used.insert("heads.policy.weight");
                err.clear();
            }
        } else if (w != nullptr) {
            used.insert("heads.policy.weight");
        }
    }
    // 逐候选门控（第五十五轮）：**可缺** —— 旧网/第二季的网都没有 ⇒ 全 0 ⇒ `g ≡ 1`（恒等）。
    {
        const Mat *gw = mat("heads.policy_gate.weight", dm, 3, err);
        if (gw != nullptr) {
            used.insert("heads.policy_gate.weight");
        } else {
            err.clear();                        // 缺它不是错：按全 0 处理
        }
        const std::vector<float> *gb = vec("heads.policy_gate.bias", dm, err);
        if (gb != nullptr) {
            used.insert("heads.policy_gate.bias");
        } else {
            err.clear();
        }
    }
    V("heads.policy.bias", 1);
    M("heads.value.weight", vb, dm);
    V("heads.value.bias", vb);
    M("heads.placement.weight", 4, dm);
    V("heads.placement.bias", 4);
    M("heads.belief_hand.weight", 3 * kKindCount, dm);
    V("heads.belief_hand.bias", 3 * kKindCount);
    M("heads.belief_tenpai.weight", 3, dm);
    V("heads.belief_tenpai.bias", 3);
    M("heads.danger.weight", 4, dm);
    V("heads.danger.bias", 4);
    M("heads.effect.weight", 3, dm);
    V("heads.effect.bias", 3);

    // ★ #0（第一百零六轮，段 3b 补齐 C++ 读端）：`tile.tile_id` **可缺**（旧包没有 ⇒ `tile+0`
    //   逐位不变）。⚠ 与 `flags` 的 bit1 **双向**校验：文件说"有"就必须真在、说"没有"就不许在
    //   （单向校验会让"导出器忘了写 flag"静默漏读一整块身份嵌入）。
    const bool hasTileId = mats.count("tile.tile_id") != 0;
    if (hasTileId) {
        M("tile.tile_id", kKindCount, kCTile);
    }
    if (hasTileId != ((flags & kFlagTileId) != 0)) {
        err = std::string("arch 段的 `flags` 与张量表自相矛盾：bit1(hasTileId)=")
              + ((flags & kFlagTileId) != 0 ? "1" : "0") + "，而 `tile.tile_id` "
              + (hasTileId ? "在" : "不在") + " —— 导出器与读端不同版本";
        return false;
    }
    // B3（第一百零四轮）：三张量**必须整组出现或整组缺席**（半组 = 文件坏了）。
    {
        const bool hasW = vecs.count("fusion.u_norm.weight") != 0;
        const bool hasB = vecs.count("fusion.u_norm.bias") != 0;
        const bool hasR = vecs.count("fusion.u_res") != 0;
        if (hasW != hasB || hasW != hasR) {
            err = "B3 张量不成组（fusion.u_norm.weight/bias/u_res）—— 文件坏了";
            return false;
        }
        if (hasR != ((flags & kFlagB3) != 0)) {
            err = std::string("arch 段的 `flags` 与张量表自相矛盾：bit0(hasB3)=")
                  + ((flags & kFlagB3) != 0 ? "1" : "0") + "，而 B3 三张量 "
                  + (hasR ? "在" : "不在");
            return false;
        }
    }
    if ((flags & kFlagFiLM) != 0) {
        err = "arch 段的 `flags` 声明了 FiLM（bit2）—— 本前向不实现 FiLM，⛔ 不猜语义";
        return false;
    }
    {
        const bool hasMem = mats.count("fusion.mem.weight") != 0;
        if (hasMem != ((flags & kFlagMemGate) != 0)) {
            err = std::string("arch 段的 `flags` 与张量表自相矛盾：bit3(hasMemGate)=")
                  + ((flags & kFlagMemGate) != 0 ? "1" : "0") + "，而 `fusion.mem.weight` "
                  + (hasMem ? "在" : "不在");
            return false;
        }
    }
    return err.empty();
}

/**
 * **A−（topoId=3）**的张量表（= `export._expected_shapes_a`，93 个张量，含可缺的 `tile.tile_id`）。
 *
 * <p>⛔ 与 v4 表**互不相交**：A− 没有 `tile.enc.*` / `fusion.q_tile|q_evt|gate|write|film|mem` /
 * `event.tr.*`；v4 没有 `tile.stem.*` / `fusion.joint|cross|seg`。名字写错 ⇒ 这里报"缺张量"，
 * 反向的多余张量由 `finishPolicy` 报 ⇒ 两个方向都 fail-loud。
 */
bool V4Policy::bindAllA(std::string &err) {
    const int dm = dModel;
    const int td = tileD;
    const int vb = valueBins;
    auto M = [&](const char *name, int rows, int cols) {
        if (mat(name, rows, cols, err) != nullptr) {
            used.insert(name);
        }
    };
    auto V = [&](const char *name, int n) {
        if (vec(name, n, err) != nullptr) {
            used.insert(name);
        }
    };
    // 三维卷积权重：**必须**走 `convs`（`mat()` 只认 1/2 维）
    auto C = [&](const char *name, int out, int in, int k) {
        const auto it = convs.find(name);
        if (it == convs.end()) {
            err = "权重缺张量：" + std::string(name);
            return;
        }
        const Conv &c = it->second;
        if (c.out != out || c.in != in || c.k != k) {
            err = std::string("张量 ") + name + " 形状 [" + std::to_string(c.out) + ","
                  + std::to_string(c.in) + "," + std::to_string(c.k) + "] != [" + std::to_string(out)
                  + "," + std::to_string(in) + "," + std::to_string(k) + "]";
            return;
        }
        used.insert(name);
    };

    // ---- #0（可缺）+ tile 塔
    const bool hasTileId = mats.count("tile.tile_id") != 0;
    if (hasTileId) {
        M("tile.tile_id", kKindCount, kCTile);
    }
    C("tile.stem.weight", td, kCTile, 5);
    V("tile.stem.bias", td);
    V("tile.stem_norm.weight", td);
    V("tile.stem_norm.bias", td);
    for (int b = 0; b < 3; b++) {
        const std::string pre = "tile.res." + std::to_string(b);
        V((pre + ".norm.weight").c_str(), td);
        V((pre + ".norm.bias").c_str(), td);
        C((pre + ".conv.weight").c_str(), td, td, b == 0 ? 5 : 3);   // 三维张量只看 `convs`
        V((pre + ".conv.bias").c_str(), td);
    }
    V("tile.attn_norm.weight", td);
    V("tile.attn_norm.bias", td);
    M("tile.attn.in_proj_weight", 3 * td, td);
    V("tile.attn.in_proj_bias", 3 * td);
    M("tile.attn.out_proj.weight", td, td);
    V("tile.attn.out_proj.bias", td);
    M("tile.proj.weight", dm, td);
    V("tile.proj.bias", dm);
    V("tile.proj_norm.weight", dm);
    V("tile.proj_norm.bias", dm);

    // ---- 事件塔（Linear + GRUCell + 2×Conv1d(k=3)）
    M("event.enc.weight", dm, kCEvt);
    V("event.enc.bias", dm);
    M("event.cell.weight_ih", 3 * dm, dm);
    M("event.cell.weight_hh", 3 * dm, dm);
    V("event.cell.bias_ih", 3 * dm);
    V("event.cell.bias_hh", 3 * dm);
    C("event.conv1.weight", dm, dm, 3);
    V("event.conv1.bias", dm);
    V("event.norm1.weight", dm);
    V("event.norm1.bias", dm);
    C("event.conv2.weight", dm, dm, 3);
    V("event.conv2.bias", dm);
    V("event.norm2.weight", dm);
    V("event.norm2.bias", dm);

    // ---- ctx / cand 编码器（首层 LN + 死维掩码）
    V("ctx.norm.weight", kCCtx);
    V("ctx.norm.bias", kCCtx);
    M("ctx.fc1.weight", dm, kCCtx);
    V("ctx.fc1.bias", dm);
    M("ctx.fc2.weight", dm, dm);
    V("ctx.fc2.bias", dm);
    V("cand.norm.weight", kCCand);
    V("cand.norm.bias", kCCand);
    M("cand.fc1.weight", dm, kCCand);
    V("cand.fc1.bias", dm);
    M("cand.fc2.weight", dm, dm);
    V("cand.fc2.bias", dm);

    // ---- fusion：段嵌入 3 + 95-token joint + FFN + cross + out(384→128) + LN(384)
    M("fusion.seg", kNSeqA, dm);
    V("fusion.joint_norm.weight", dm);
    V("fusion.joint_norm.bias", dm);
    M("fusion.joint.in_proj_weight", 3 * dm, dm);
    V("fusion.joint.in_proj_bias", 3 * dm);
    M("fusion.joint.out_proj.weight", dm, dm);
    V("fusion.joint.out_proj.bias", dm);
    V("fusion.ffn_norm.weight", dm);
    V("fusion.ffn_norm.bias", dm);
    M("fusion.ffn1.weight", dm, dm);
    V("fusion.ffn1.bias", dm);
    M("fusion.ffn2.weight", dm, dm);
    V("fusion.ffn2.bias", dm);
    M("fusion.cross.in_proj_weight", 3 * dm, dm);
    V("fusion.cross.in_proj_bias", 3 * dm);
    M("fusion.cross.out_proj.weight", dm, dm);
    V("fusion.cross.out_proj.bias", dm);
    M("fusion.out.weight", dm, 3 * dm);
    V("fusion.out.bias", dm);
    V("fusion.out_norm.weight", 3 * dm);
    V("fusion.out_norm.bias", 3 * dm);

    // ---- 头（26,054）：`policy_gate` 与 B3 在 A− 里是**拓扑必需**（不是"可缺"）
    M("heads.policy.weight", 1, dm);
    V("heads.policy.bias", 1);
    M("heads.policy_gate.weight", dm, 3);
    V("heads.policy_gate.bias", dm);
    M("heads.value.weight", vb, dm);
    V("heads.value.bias", vb);
    M("heads.placement.weight", 4, dm);
    V("heads.placement.bias", 4);
    M("heads.belief_hand.weight", 3 * kKindCount, dm);
    V("heads.belief_hand.bias", 3 * kKindCount);
    M("heads.belief_tenpai.weight", 3, dm);
    V("heads.belief_tenpai.bias", 3);
    M("heads.danger.weight", 4, dm);
    V("heads.danger.bias", 4);
    M("heads.effect.weight", 3, dm);
    V("heads.effect.bias", 3);
    if (!err.empty()) {
        return false;
    }

    // ---- flags ↔ 张量表（双向）
    if (hasTileId != ((flags & kFlagTileId) != 0)) {
        err = std::string("arch 段的 `flags` 与张量表自相矛盾：bit1(hasTileId)=")
              + ((flags & kFlagTileId) != 0 ? "1" : "0") + "，而 `tile.tile_id` "
              + (hasTileId ? "在" : "不在");
        return false;
    }
    if ((flags & kFlagB3) == 0 || vecs.count("fusion.u_norm.weight") == 0
            || vecs.count("fusion.u_norm.bias") == 0 || vecs.count("fusion.u_res") == 0) {
        err = "A− 的 B3（`fusion.u_norm.*` + `fusion.u_res`）是**拓扑必需**："
              "缺张量或 `flags` 的 bit0 没置 ⇒ 文件与拓扑不符（⛔ 不补 0 / 不猜）";
        return false;
    }
    if (vecs.count("fusion.u_norm.weight") != 0) {
        V("fusion.u_norm.weight", dm);
        V("fusion.u_norm.bias", dm);
        V("fusion.u_res", dm);
    }
    if ((flags & (kFlagFiLM | kFlagMemGate)) != 0) {
        err = "A− 的 arch 段声明了 FiLM 或 mem 门（flags bit2/bit3）—— 这两块在 T1 图里"
              "**不存在**（`h_evt` 已是第 95 个 token）⇒ 文件与拓扑不符";
        return false;
    }
    return err.empty();
}

// ---------------------------------------------------------------- arch 段（格式 3）
//
// 布局 = `A-REFACTOR-IMPL-PLAN.md` §3 的字段表（段 2 的落地读数见 `ARCH-REDESIGN.md` §11.1：
// `archLen = 2048` 整段、核心 96 B、保留尾 1,952 B 必须全 0）。**块表偏移 = 64 + archLen**。
//
// ⛔ 读端口径：arch 段是**唯一拓扑判据**（`topoId`）；张量名反推只当一致性断言（本训练端
// 不做反推 —— 名字表就是 `bindAllV4/bindAllA`，多余的张量由 `finishPolicy` 抓）。

/** arch 段的解析结果（字段全部保留：`describe` / 报错 / 前向分支都要用）。 */
struct ArchInfo {
    int archLen = 0;
    int archVersion = 0;
    int topoId = 0;
    int dTile = 0, dEvt = 0, dFuse = 0, dHead = 0, dCand = 0, headDim = 0;
    int nTileConv = 0, nTileAttn = 0, nJointLayer = 0;
    int actId = 0, normId = 0, segEmbMode = 0, memTokenMode = 0, flags = 0;
    int valueBins = 0, nHeads = 0;
    float valueRange = 0.f, lnEps = 0.f;
    int tileKernel[4] = {0, 0, 0, 0};
    int nSeg = 0;
    int featVersion = 0, obsVersion = 0, derivedVersion = 0;
    int cTile = 0, cEvt = 0, kEvt = 0, cCtx = 0, cCand = 0;
};

/** 该拓扑在给定宽度下的**期望**字段（= `export.arch_profile`，逐字段同值）。 */
struct ArchExpect {
    int topoId = 0;
    int dTile = 0, dEvt = 0, dFuse = 0, dHead = 0, dCand = 0, headDim = 0;
    int nTileConv = 0, nTileAttn = 0, nJointLayer = 0;
    int actId = 0, normId = 0, segEmbMode = 0, memTokenMode = 0;
    int valueBins = 0, nHeads = 0;
    float valueRange = 0.f, lnEps = 1e-5f;
    int tileKernel[4] = {0, 0, 0, 0};
    int nSeg = 0;
    int cTile = kCTile, cEvt = kCEvt, kEvt = kKEvt, cCtx = kCCtx, cCand = kCCand;
};

bool archExpectOf(int topo, int dModel, int tileD, int nHeads, int valueBins, ArchExpect &e,
                  std::string &err) {
    e.topoId = topo;
    e.dTile = tileD;
    e.dEvt = dModel;
    e.dFuse = dModel;
    e.dHead = dModel;
    e.dCand = kCCand;
    e.headDim = tileD / nHeads;
    e.valueBins = valueBins;
    e.nHeads = nHeads;
    if (topo == kTopoA) {
        e.nTileConv = 4;
        e.nTileAttn = 1;
        e.nJointLayer = 1;
        e.actId = kActGelu;
        e.normId = kNormPre;
        e.segEmbMode = 1;
        e.memTokenMode = 1;
        e.valueRange = 60.f;
        e.tileKernel[0] = 5;
        e.tileKernel[1] = 5;
        e.tileKernel[2] = 3;
        e.tileKernel[3] = 3;
        e.nSeg = kNSeqA;
        return true;
    }
    if (topo == kTopoV4) {
        e.nTileConv = 0;                 // v4 的 tile 塔没有 conv
        e.nTileAttn = 1;
        e.nJointLayer = 0;
        e.actId = kActReLU;
        e.normId = kNormPost;
        e.segEmbMode = 0;
        e.memTokenMode = 0;
        e.valueRange = 30.f;
        e.nSeg = 0;
        return true;
    }
    err = "topoId=" + std::to_string(topo) + " 未注册（已注册：2 = v4、3 = A−/T1）";
    return false;
}

inline uint16_t rd16(const uint8_t *p) {
    return static_cast<uint16_t>(static_cast<uint16_t>(p[0]) | (static_cast<uint16_t>(p[1]) << 8));
}
inline uint32_t rd32(const uint8_t *p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8)
            | (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}
inline float rdF32(const uint8_t *p) {
    const uint32_t bits = rd32(p);
    float v = 0.f;
    std::memcpy(&v, &bits, sizeof(v));
    return v;
}

/**
 * 解析 `[64, 64+archLen)` 的 arch 段并**逐条 fail-loud**（§3 的清单 + §11.2 的六条）。
 *
 * <p>⛔ 六条里每一条都对应一次"静默算错"：valueBins/valueRange 不核 ⇒ 值头宽度错位；
 * `flags` 与张量表不核 ⇒ B3/#0 静默漏读；截断不查 ⇒ 读出垃圾；保留位不查 ⇒ 未来字段被
 * 当成现在语义；topoId 不查 ⇒ 拿 v4 的代码跑 A− 的权重。
 */
bool parseArchSegment(const std::vector<uint8_t> &raw, const std::string &what, int dModel,
                      int tileD, int nHeads, int valueBins, int featVer, int obsVer,
                      int derivedVer, ArchInfo &out, std::string &err) {
    if (raw.size() < static_cast<size_t>(kV4HeaderBytes) + 4) {
        err = "权重文件被截断：" + std::to_string(raw.size()) + " B —— 连 arch 段的 `archLen` 都读不到："
              + what;
        return false;
    }
    const uint8_t *base = raw.data() + kV4HeaderBytes;
    const int alen = static_cast<int>(rd32(base));
    if (alen < kV4ArchCoreBytes || alen > kV4ArchSegBytes || (alen % 4) != 0) {
        err = "archLen=" + std::to_string(alen) + " 越界：必须在 [" + std::to_string(kV4ArchCoreBytes)
              + ", " + std::to_string(kV4ArchSegBytes) + "] 且为 4 的倍数（§3：96 B 核心 + 保留尾）："
              + what;
        return false;
    }
    if (raw.size() < static_cast<size_t>(kV4HeaderBytes) + static_cast<size_t>(alen)) {
        err = "权重文件被截断：arch 段声明 " + std::to_string(alen) + " B，文件只剩 "
              + std::to_string(raw.size() - static_cast<size_t>(kV4HeaderBytes)) + " B：" + what;
        return false;
    }
    out.archLen = alen;
    out.archVersion = rd16(base + 4);
    out.topoId = rd16(base + 6);
    out.dTile = rd16(base + 8);
    out.dEvt = rd16(base + 10);
    out.dFuse = rd16(base + 12);
    out.dHead = rd16(base + 14);
    out.dCand = rd16(base + 16);
    out.headDim = rd16(base + 18);
    out.nTileConv = base[20];
    out.nTileAttn = base[21];
    out.nJointLayer = base[22];
    out.actId = base[23];
    out.normId = base[24];
    out.segEmbMode = base[25];
    out.memTokenMode = base[26];
    out.flags = base[27];
    out.valueBins = rd16(base + 28);
    out.nHeads = rd16(base + 30);
    out.valueRange = rdF32(base + 32);
    out.lnEps = rdF32(base + 36);
    for (int i = 0; i < 4; i++) {
        out.tileKernel[i] = base[40 + i];
    }
    out.nSeg = base[44];
    out.featVersion = static_cast<int>(rd32(base + 48));
    out.obsVersion = static_cast<int>(rd32(base + 52));
    out.derivedVersion = static_cast<int>(rd32(base + 56));
    out.cTile = rd16(base + 60);
    out.cEvt = rd16(base + 62);
    out.kEvt = rd16(base + 64);
    out.cCtx = rd16(base + 66);
    out.cCand = rd16(base + 68);

    if (out.archVersion != 1) {
        err = "archVersion=" + std::to_string(out.archVersion) + " 不支持（本实现只认 1）：" + what;
        return false;
    }
    ArchExpect e;
    if (!archExpectOf(out.topoId, dModel, tileD, nHeads, valueBins, e, err)) {
        err += "：" + what;
        return false;
    }
    // 枚举字段：**先查"注册没注册"**（未注册 ⇒ 报错），再查"与拓扑一致"
    if (out.actId != kActReLU && out.actId != kActGelu) {
        err = "arch 段的 `actId`=" + std::to_string(out.actId) + " 未注册（0 = ReLU、1 = GELU-erf）："
              + what;
        return false;
    }
    if (out.normId != kNormPost && out.normId != kNormPre) {
        err = "arch 段的 `normId`=" + std::to_string(out.normId) + " 未注册（0 = post-LN、1 = pre-LN）："
              + what;
        return false;
    }
    if (out.segEmbMode > 1 || out.memTokenMode > 1) {
        err = "arch 段的 `segEmbMode/memTokenMode`=" + std::to_string(out.segEmbMode) + "/"
              + std::to_string(out.memTokenMode) + " 未注册（只认 0/1）：" + what;
        return false;
    }
    if ((out.flags & kFlagReserved) != 0) {
        char buf[96];
        std::snprintf(buf, sizeof(buf), "arch 段的 `flags`=%#04x 用了**保留位**（bit4–7 必须为 0）",
                      out.flags);
        err = std::string(buf) + "：" + what;
        return false;
    }
    // 头 ↔ arch 段：三版本号 + 两个宽度必须相等（文件不许自相矛盾）
    if (out.featVersion != featVer || out.obsVersion != obsVer
            || out.derivedVersion != derivedVer) {
        err = "头与 arch 段的版本三件套不一致：头 " + std::to_string(featVer) + "/"
              + std::to_string(obsVer) + "/" + std::to_string(derivedVer) + "，arch "
              + std::to_string(out.featVersion) + "/" + std::to_string(out.obsVersion) + "/"
              + std::to_string(out.derivedVersion) + "（文件自相矛盾）：" + what;
        return false;
    }
    if (out.valueBins != valueBins) {
        err = "`valueBins` 三处不一致：头 " + std::to_string(valueBins) + " / arch "
              + std::to_string(out.valueBins) + "（张量表里的 `heads.value.weight` 行数由 bindAll 核）："
              + what;
        return false;
    }
    if (out.nHeads != nHeads) {
        err = "头与 arch 段的 `nHeads` 不一致：头 " + std::to_string(nHeads) + " / arch "
              + std::to_string(out.nHeads) + "：" + what;
        return false;
    }
    if (out.dFuse != dModel || out.dTile != tileD) {
        err = "arch 段的 `dFuse/dTile` = " + std::to_string(out.dFuse) + "/"
              + std::to_string(out.dTile) + " 与头部 `dModel/tileD` = " + std::to_string(dModel)
              + "/" + std::to_string(tileD) + " 不一致：" + what;
        return false;
    }
    if (out.dEvt != e.dEvt || out.dHead != e.dHead || out.dCand != e.dCand
            || out.headDim != e.headDim || out.valueRange != e.valueRange) {
        err = "arch 段的宽度/量纲与 `" + std::string(out.topoId == kTopoA ? "a-" : "v4")
              + "` 拓扑不符：dEvt=" + std::to_string(out.dEvt) + "（应 " + std::to_string(e.dEvt)
              + "）dHead=" + std::to_string(out.dHead) + "（应 " + std::to_string(e.dHead)
              + "）dCand=" + std::to_string(out.dCand) + "（应 " + std::to_string(e.dCand)
              + "）headDim=" + std::to_string(out.headDim) + "（应 " + std::to_string(e.headDim)
              + "）valueRange=" + std::to_string(out.valueRange) + "（应 "
              + std::to_string(e.valueRange) + "）：" + what;
        return false;
    }
    // 其余"由拓扑决定"的字段：逐字段等于期望（⛔ 不许"文件说 GELU、代码建 ReLU"）
    std::vector<std::string> bad;
    auto chk = [&](const char *name, int got, int want) {
        if (got != want) {
            bad.push_back(std::string(name) + "=" + std::to_string(got) + "（应 "
                          + std::to_string(want) + "）");
        }
    };
    chk("nTileConv", out.nTileConv, e.nTileConv);
    chk("nTileAttn", out.nTileAttn, e.nTileAttn);
    chk("nJointLayer", out.nJointLayer, e.nJointLayer);
    chk("actId", out.actId, e.actId);
    chk("normId", out.normId, e.normId);
    chk("segEmbMode", out.segEmbMode, e.segEmbMode);
    chk("memTokenMode", out.memTokenMode, e.memTokenMode);
    for (int i = 0; i < 4; i++) {
        chk((std::string("tileKernel") + std::to_string(i)).c_str(), out.tileKernel[i],
            e.tileKernel[i]);
    }
    chk("nSeg", out.nSeg, e.nSeg);
    chk("cTile", out.cTile, e.cTile);
    chk("cEvt", out.cEvt, e.cEvt);
    chk("kEvt", out.kEvt, e.kEvt);
    chk("cCtx", out.cCtx, e.cCtx);
    chk("cCand", out.cCand, e.cCand);
    if (!bad.empty()) {
        err = "arch 段与 `" + std::string(out.topoId == kTopoA ? "a-" : "v4") + "` 拓扑不符（"
              + std::to_string(bad.size()) + " 条）：" + joinNames(bad, 8) + "：" + what;
        return false;
    }
    // 保留：pad 两字节 + 核心 70..95 + 保留尾 96..archLen 必须全 0
    if (base[45] != 0 || base[46] != 0 || base[47] != 0) {
        err = "arch 段的 pad 字节非 0（" + std::to_string(base[45]) + "/" + std::to_string(base[46])
              + "/" + std::to_string(base[47]) + "）：" + what;
        return false;
    }
    for (int i = 70; i < kV4ArchCoreBytes; i++) {
        if (base[i] != 0) {
            err = "arch 段的保留区（偏移 70–95，26 B）非 0（archVersion=1 必须全 0）：" + what;
            return false;
        }
    }
    for (int i = kV4ArchCoreBytes; i < alen; i++) {
        if (base[i] != 0) {
            err = "arch 段的保留尾（偏移 96–" + std::to_string(alen - 1) + "）非 0 —— 写端必须全 0："
                  + what;
            return false;
        }
    }
    return true;
}

// ---------------------------------------------------------------- 加载

bool v4LoadPolicyBytes(const std::vector<uint8_t> &raw, const std::string &what, V4Policy &out,
                       std::string &err) {
    err.clear();
    if (raw.size() < static_cast<size_t>(kV4HeaderBytes)) {
        err = "权重文件太短：" + what + "（" + std::to_string(raw.size()) + " B）";
        return false;
    }
    Reader r{raw.data(), raw.size(), 0, false};
    const uint32_t magic = r.u32();
    const int32_t fmt = r.i32();
    const int32_t featVer = r.i32();
    const int32_t obsVer = r.i32();
    const int32_t derivedVer = r.i32();
    const int32_t dModel = r.i32();
    const int32_t tileD = r.i32();
    const int32_t nHeads = r.i32();
    const int32_t valueBins = r.i32();
    if (magic != kV4Magic) {
        char buf[128];
        std::snprintf(buf, sizeof(buf), "权重文件魔数不对：%#x（期望 %#x）",
                      static_cast<unsigned>(magic), kV4Magic);
        err = std::string(buf) + "：" + what;
        return false;
    }
    if (fmt == 2) {
        // ⛔ **硬切**（`A-REFACTOR-IMPL-PLAN.md` §3）：格式 2 = A− 之前的老包，**没有 arch 段**
        //   ⇒ 本前向无法知道这是 v4 图还是 A− 图。⛔ 不自动升级、不补 0、不猜 arch。
        err = "权重格式版本 2（= A− 之前的老包，没有 arch 段）已经退役：format 3 起是 v4/A− 的"
              "统一容器。只有**冻结快照**能读它：`lab/mv1/frozen-format2/`（jar `3E7D0ADC…` / "
              "trainer.exe `F6AAA307…`）—— ⛔ 不会自动升级、不补 0、不猜 arch：" + what;
        return false;
    }
    if (fmt == 1) {
        err = "权重格式版本 1 是 v3 的定长 MLP（由 loadNet 加载），不是 v4 的容器：" + what;
        return false;
    }
    if (fmt != kV4FormatVersion) {
        err = "权重格式版本 " + std::to_string(fmt) + " != 本训练端 "
              + std::to_string(kV4FormatVersion) + "（v3 = 1，v4/A− = 3；2 已退役）：" + what;
        return false;
    }
    if (featVer != kV4FeatureVersion) {
        err = "权重特征版本 " + std::to_string(featVer) + " != 本训练端 "
              + std::to_string(kV4FeatureVersion) + " —— 重新导出权重（"
              + "`python -m mahjong_ml.v4.export`）：" + what;
        return false;
    }
    if (obsVer != kV4ObsVersion) {
        err = "权重是按 obs v" + std::to_string(obsVer) + " 训练的，本训练端特征要 obs v"
              + std::to_string(kV4ObsVersion) + " —— 重新导出权重：" + what;
        return false;
    }
    if (dModel <= 0 || tileD <= 0 || nHeads <= 0 || valueBins <= 0 || dModel % nHeads != 0
            || tileD % nHeads != 0) {
        err = "权重头部非法：dModel=" + std::to_string(dModel) + " tileD=" + std::to_string(tileD)
              + " nHeads=" + std::to_string(nHeads) + " valueBins=" + std::to_string(valueBins)
              + "：" + what;
        return false;
    }

    // ---- arch 段（**块表之前**，偏移 64，长度 = 段首 u32）——格式 3 的唯一"拓扑判据"
    const int32_t nBlocks = r.i32();
    const int32_t nTensors = r.i32();
    uint8_t fpB[16];
    r.bytes(fpB, 16);
    std::string fingerprint(reinterpret_cast<const char *>(fpB), 16);
    const int32_t paramsHeader = r.i32();     // 格式 3 起是**校验位**（⛔ 不再"读了不用"）
    ArchInfo ai;
    if (!parseArchSegment(raw, what, dModel, tileD, nHeads, valueBins, featVer, obsVer, derivedVer,
                          ai, err)) {
        return false;
    }
    r.pos = static_cast<size_t>(kV4HeaderBytes) + static_cast<size_t>(ai.archLen);
    r.bad = false;

    // ---- 块清单：id 已注册 / 宽度相符 / 顺序递增（= Java 的三条检查）
    std::set<std::string> missing;
    for (int i = 0; i < 16; i++) {
        missing.insert(kBlockIds[i]);
    }
    std::vector<std::string> blocks;
    int lastIndex = -1;
    for (int i = 0; i < nBlocks; i++) {
        const uint16_t ln = r.u16();
        std::vector<char> idb(ln);
        if (ln > 0) {
            r.bytes(idb.data(), ln);
        }
        const std::string bid(idb.begin(), idb.end());
        const int32_t width = r.i32();
        const int idx = v4IndexOfBlock(bid);
        if (idx < 0) {
            err = "权重里的块 id 未注册：" + bid + "（本训练端有 16 个注册块，见 kBlockIds）";
            return false;
        }
        if (kBlockWidths[idx] != width) {
            err = "块 " + bid + " 宽度 " + std::to_string(width) + " != 本训练端 "
                  + std::to_string(kBlockWidths[idx]) + " —— 张量布局变过，重新导出权重";
            return false;
        }
        if (idx <= lastIndex) {
            err = "权重里的块顺序/重复不对：" + bid + "（注册表顺序必须递增）";
            return false;
        }
        lastIndex = idx;
        blocks.push_back(bid);
        missing.erase(bid);
    }
    std::vector<int> widths;
    if (!v4WidthsOf(blocks, widths, err)) {
        return false;
    }
    const std::string wantFp = v4Fingerprint(blocks, widths);
    if (wantFp != fingerprint) {
        err = "块清单指纹不符：文件里 " + fingerprint + "，按清单重算 " + wantFp
              + " —— 权重被改过或导出器与本训练端不同版本";
        return false;
    }
    if (missing.empty() && v4Fingerprint() != fingerprint) {
        err = "权重声明了全部块，但指纹 " + fingerprint + " 与本训练端注册表 " + v4Fingerprint()
              + " 不符";
        return false;
    }

    V4Policy p;
    p.dModel = dModel;
    p.tileD = tileD;
    p.nHeads = nHeads;
    p.valueBins = valueBins;
    p.fingerprint = fingerprint;
    p.blocks = blocks;
    p.missingBlocks.assign(missing.begin(), missing.end());
    p.format = static_cast<int>(fmt);
    p.arch = ai.topoId;
    p.flags = ai.flags;
    p.valueRange = ai.valueRange;
    p.dTile = ai.dTile;
    p.dEvt = ai.dEvt;
    p.dFuse = ai.dFuse;
    p.dHead = ai.dHead;
    p.nSeg = ai.nSeg;
    for (int i = 0; i < 4; i++) {
        p.tileKernels[i] = ai.tileKernel[i];
    }
    // 权重整段字节的 sha256（`V4Cache` 槽键的一部分）。⚠ 算在**原始字节**上：改一字节就换键。
    p.weightSha = sha256Hex(raw.data(), raw.size(), 16);
    // 块/张量数的上界（§3 的夹逼：⛔ 别让一个坏头把内存吃完）
    if (nBlocks < 0 || nBlocks > 10000 || nTensors < 0 || nTensors > 10000) {
        err = "权重的块数/张量数越界：nBlocks=" + std::to_string(nBlocks) + " nTensors="
              + std::to_string(nTensors) + "（上限 10,000）：" + what;
        return false;
    }

    // ---- 张量表（名字升序，但顺序不参与语义；逐条核对维度数）
    long long paramsSeen = 0;
    for (int i = 0; i < nTensors; i++) {
        const uint16_t ln = r.u16();
        std::vector<char> nb(ln);
        if (ln > 0) {
            r.bytes(nb.data(), ln);
        }
        const std::string name(nb.begin(), nb.end());
        const int nd = r.u8();
        int shape[8] = {0, 0, 0, 0, 0, 0, 0, 0};
        long long total = 1;
        for (int j = 0; j < nd; j++) {
            const int32_t s = r.i32();
            if (j < 8) {
                shape[j] = s;
            }
            total *= s;
        }
        if (r.bad) {
            err = "权重文件被截断（读完张量表之前就到底了）：" + what;
            return false;
        }
        if (total < 0 || total > 200000000LL) {
            err = "张量 " + name + " 的形状非法（元素数 " + std::to_string(total) + "）";
            return false;
        }
        std::vector<float> data(static_cast<size_t>(total), 0.f);
        for (long long j = 0; j < total; j++) {
            data[static_cast<size_t>(j)] = r.f32();
        }
        if (r.bad) {
            err = "权重文件被截断（读完张量 " + name + " 之前就到底了）：" + what;
            return false;
        }
        if (!p.allNames.insert(name).second) {
            err = "权重里有重复张量名：" + name;
            return false;
        }
        paramsSeen += total;
        if (nd == 2) {
            V4Policy::Mat m;
            m.rows = shape[0];
            m.cols = shape[1];
            m.data = std::move(data);
            p.mats[name] = std::move(m);
        } else if (nd == 1) {
            p.vecs[name] = std::move(data);
        } else if (nd == 3) {
            // A− 的 `Conv1d` 权重 `[out][in][k]`（= PyTorch 布局，行主序拍平即可）
            V4Policy::Conv c;
            c.out = shape[0];
            c.in = shape[1];
            c.k = shape[2];
            c.data = std::move(data);
            p.convs[name] = std::move(c);
        } else {
            err = "张量 " + name + " 是 " + std::to_string(nd) + " 维（只支持 1/2/3 维）";
            return false;
        }
    }
    if (r.remaining() != 0) {
        err = "权重文件尾部多了 " + std::to_string(r.remaining()) + " 字节（张量表与头部不符）："
              + what;
        return false;
    }
    // `params` = **校验位**（§3 fail-loud ⑤；C++/Java 以前"读了不用"）
    if (static_cast<long long>(paramsHeader) != paramsSeen) {
        err = "头部的 `params`=" + std::to_string(paramsHeader) + " != 张量表实算 "
              + std::to_string(paramsSeen) + "（校验位不符：文件被改过或写端有 bug）：" + what;
        return false;
    }
    if (!finishPolicy(p, err)) {
        return false;
    }
    out = std::move(p);
    return true;
}

bool v4LoadPolicy(const std::string &path, V4Policy &out, std::string &err) {
    std::vector<uint8_t> raw;
    if (!readWholeFile(path, raw, err)) {
        return false;
    }
    return v4LoadPolicyBytes(raw, path, out, err);
}

bool v4WeightFormatOf(const std::string &path, int &format, std::string &err) {
    std::vector<uint8_t> raw;
    if (!readWholeFile(path, raw, err)) {
        return false;
    }
    if (raw.size() < 8) {
        err = "权重文件太短：" + path + "（" + std::to_string(raw.size()) + " B）";
        return false;
    }
    Reader r{raw.data(), raw.size(), 0, false};
    const uint32_t magic = r.u32();
    const int32_t fmt = r.i32();
    if (magic != kV4Magic) {
        char buf[128];
        std::snprintf(buf, sizeof(buf), "权重文件魔数不对：%#x（期望 %#x）",
                      static_cast<unsigned>(magic), kV4Magic);
        err = std::string(buf) + "：" + path;
        return false;
    }
    if (fmt == 2) {
        err = "权重格式版本 2（= A− 之前的老包，没有 arch 段）已经退役：只有**冻结快照**能读它"
              "（`lab/mv1/frozen-format2/`，jar `3E7D0ADC…` / trainer.exe `F6AAA307…`）—— "
              "⛔ 不自动升级、不补 0、不猜 arch：" + path;
        return false;
    }
    if (fmt != 1 && fmt != kV4FormatVersion) {
        err = "权重格式版本 " + std::to_string(fmt) + " 不认识（v3 = 1，v4/A− = 3；2 已退役）—— "
              "重新导出权重：" + path;
        return false;
    }
    format = fmt;
    return true;
}

bool v4DebugOutputBiasShift(const V4Policy &p, float delta, V4Policy &out, std::string &err) {
    err.clear();
    out = p;
    const auto it = out.vecs.find("heads.policy.bias");
    if (it == out.vecs.end()) {
        err = "权重缺张量：heads.policy.bias";
        return false;
    }
    it->second[0] += delta;
    return finishPolicy(out, err);
}

long long v4ParamCount(const V4Policy &p) {
    long long total = 0;
    for (const auto &kv : p.mats) {
        total += static_cast<long long>(kv.second.data.size());
    }
    for (const auto &kv : p.convs) {
        total += static_cast<long long>(kv.second.data.size());
    }
    for (const auto &kv : p.vecs) {
        total += static_cast<long long>(kv.second.size());
    }
    return total;
}

std::string V4Policy::cacheTag() const {
    // ⚠ 版本号写进键里（段 3b：`CACHE_VERSION 1→2`）—— **键的语义**变了（现在含网络身份），
    //   不 bump 的话"旧进程里存下的槽"与"新语义"无法区分；跨进程本就没有槽，所以这里的作用
    //   只是让**代码版本**也进键（判据：换 arch / 换权重 / 换实现都必须 miss）。
    return "v2|f" + std::to_string(format) + "|t" + std::to_string(arch) + "|" + weightSha;
}

std::string v4Describe(const V4Policy &p) {
    const char *topo = p.arch == kTopoA ? "a-" : (p.arch == kTopoV4 ? "v4" : "?");
    return "V4Policy（" + std::to_string(v4ParamCount(p)) + " 参数，格式 "
           + std::to_string(p.format) + "，arch=" + topo + "（topoId="
           + std::to_string(p.arch) + "），dims={d_model=" + std::to_string(p.dModel)
           + ", tile_d=" + std::to_string(p.tileD) + ", n_heads=" + std::to_string(p.nHeads)
           + ", value_bins=" + std::to_string(p.valueBins) + "}，flags="
           + std::to_string(p.flags) + "，权重 sha=" + p.weightSha.substr(0, 8) + "，块 "
           + std::to_string(p.blocks.size()) + " / 注册表 16，指纹 " + p.fingerprint + "）";
}

// ---------------------------------------------------------------- 前向

namespace {

/**
 * `Mlp`（= `Linear + ReLU + Linear + ReLU`，注意**末尾那个 ReLU**）—— **`B` 行一起算**。
 *
 * <p>`x` / `out` 都是扁平 `[B][行距]`；两层线性走 `linearBatchFast`（同一批权重只扫一遍），
 * 激活逐元素 —— 与原来逐行调用**逐位相同**。
 */
bool mlp(const V4Policy &p, const float *x, int xStride, int B, size_t xn,
         const std::string &prefix, int cin, int dm, float *out, V4Scratch &sc, std::string &err,
         bool finalRelu = true) {
    const V4Policy::Mat *w0 = p.mat(prefix + ".0.weight", dm, cin, err);
    const std::vector<float> *b0 = p.vec(prefix + ".0.bias", dm, err);
    const V4Policy::Mat *w2 = p.mat(prefix + ".2.weight", dm, dm, err);
    const std::vector<float> *b2 = p.vec(prefix + ".2.bias", dm, err);
    if (!err.empty()) {
        return false;
    }
    std::vector<float> &h = sc.mlpHid;
    if (h.size() < static_cast<size_t>(B) * static_cast<size_t>(dm)) {
        h.resize(static_cast<size_t>(B) * static_cast<size_t>(dm));
    }
    linearBatchFast(h.data(), dm, x, xStride, B, xn, *w0, b0->data(), 0, dm);
    reluBuf(h.data(), static_cast<size_t>(B) * static_cast<size_t>(dm));
    linearBatchFast(out, dm, h.data(), dm, B, static_cast<size_t>(dm), *w2, b2->data(), 0, dm);
    if (finalRelu) {
        reluBuf(out, static_cast<size_t>(B) * static_cast<size_t>(dm));
    }
    return true;
}

/**
 * 多头注意力（单样本、无 mask）：`q` 对 `kv` 做注意力（= Java `V4Policy.mha`）。
 *
 * <p>`q` / `kv` / `out` 全是**扁平** `[行数][d]`（行距 = `d`）；q/k/v 投影与 out 投影都走
 * `linearBatchFast` —— 与原来逐行 `projRows` **逐位相同**（每个输出行仍是单累加器 + c 升序，
 * 变的只是循环次序）。注意力那两段归约**保持标量**：它们是"单个输出元素沿 kv 维求和"，
 * 沿归约维做 SIMD 会改求和顺序 ⇒ 改比特，判据不允许。
 */
bool mha(const V4Policy &p, const float *q, int lq, const float *kv, int lk, int d,
         const std::string &prefix, float *out, V4Scratch &sc, std::string &err) {
    const V4Policy::Mat *inW = p.mat(prefix + ".in_proj_weight", 3 * d, d, err);
    const std::vector<float> *inB = p.vec(prefix + ".in_proj_bias", 3 * d, err);
    const V4Policy::Mat *outW = p.mat(prefix + ".out_proj.weight", d, d, err);
    const std::vector<float> *outB = p.vec(prefix + ".out_proj.bias", d, err);
    if (!err.empty()) {
        return false;
    }
    const int hd = d / p.nHeads;
    const float scale = static_cast<float>(1.0 / std::sqrt(static_cast<double>(hd)));
    if (sc.qp.size() < static_cast<size_t>(lq) * static_cast<size_t>(d)) {
        sc.qp.resize(static_cast<size_t>(lq) * static_cast<size_t>(d));
    }
    if (sc.kp.size() < static_cast<size_t>(lk) * static_cast<size_t>(d)) {
        sc.kp.resize(static_cast<size_t>(lk) * static_cast<size_t>(d));
    }
    if (sc.vp.size() < static_cast<size_t>(lk) * static_cast<size_t>(d)) {
        sc.vp.resize(static_cast<size_t>(lk) * static_cast<size_t>(d));
    }
    if (sc.ctx.size() < static_cast<size_t>(lq) * static_cast<size_t>(d)) {
        sc.ctx.resize(static_cast<size_t>(lq) * static_cast<size_t>(d));
    }
    if (sc.scores.size() < static_cast<size_t>(std::max(lq, lk))) {
        sc.scores.resize(static_cast<size_t>(std::max(lq, lk)));
    }
    float *qp = sc.qp.data();
    float *kp = sc.kp.data();
    float *vp = sc.vp.data();
    float *ctx = sc.ctx.data();
    // 与 Java 同序：q 取 in_proj 的 [0,d) 行、k 取 [d,2d)、v 取 [2d,3d)（逐个输出行独立累加）
    linearBatchFast(qp, d, q, d, lq, static_cast<size_t>(d), *inW, inB->data(), 0, d);
    linearBatchFast(kp, d, kv, d, lk, static_cast<size_t>(d), *inW, inB->data(), d, d);
    linearBatchFast(vp, d, kv, d, lk, static_cast<size_t>(d), *inW, inB->data(), 2 * d, d);
    for (int t = 0; t < lq; t++) {
        for (int hh = 0; hh < p.nHeads; hh++) {
            const int base = hh * hd;
            for (int s = 0; s < lk; s++) {
                float sum = 0.f;
                for (int i = 0; i < hd; i++) {
                    sum += qp[static_cast<size_t>(t) * d + static_cast<size_t>(base + i)]
                           * kp[static_cast<size_t>(s) * d + static_cast<size_t>(base + i)];
                }
                sc.scores[static_cast<size_t>(s)] = sum * scale;
            }
            softmaxBuf(sc.scores.data(), lk);
            for (int i = 0; i < hd; i++) {
                float sum = 0.f;
                for (int s = 0; s < lk; s++) {
                    sum += sc.scores[static_cast<size_t>(s)]
                           * vp[static_cast<size_t>(s) * d + static_cast<size_t>(base + i)];
                }
                ctx[static_cast<size_t>(t) * d + static_cast<size_t>(base + i)] = sum;
            }
        }
    }
    linearBatchFast(out, d, ctx, d, lq, static_cast<size_t>(d), *outW, outB->data(), 0, d);
    return true;
}

/**
 * `nn.TransformerEncoderLayer(d, nhead, dim_feedforward=2d, dropout=0, norm_first=True)` 一层
 * （= Java `V4Policy.transformerLayer`）：`x = x + attn(norm1(x))`，再 `x = x + linear2(relu(linear1(norm2(x))))`。
 *
 * <p>`x` / `out` 是扁平 `[len][d]`；中间量全在 `sc` 上（**热路径零分配**）。
 */
bool transformerLayer(const V4Policy &p, const float *x, int len, const std::string &prefix,
                      float *out, V4Scratch &sc, std::string &err) {
    const int d = p.dModel;
    const std::vector<float> *n1g = p.vec(prefix + ".norm1.weight", d, err);
    const std::vector<float> *n1b = p.vec(prefix + ".norm1.bias", d, err);
    if (!err.empty()) {
        return false;
    }
    const size_t ld = static_cast<size_t>(len) * static_cast<size_t>(d);
    if (sc.xn.size() < ld) {
        sc.xn.resize(ld);
        sc.att.resize(ld);
        sc.yv.resize(ld);
        sc.res.resize(ld);
    }
    if (sc.hid.size() < static_cast<size_t>(2) * ld) {
        sc.hid.resize(static_cast<size_t>(2) * ld);
    }
    float *xn = sc.xn.data();
    for (int i = 0; i < len; i++) {
        std::memcpy(xn + static_cast<size_t>(i) * d, x + static_cast<size_t>(i) * d,
                    sizeof(float) * static_cast<size_t>(d));
        layerNormBuf(xn + static_cast<size_t>(i) * d, d, n1g->data(), n1b->data());
    }
    if (!mha(p, xn, len, xn, len, d, prefix + ".self_attn", sc.att.data(), sc, err)) {
        return false;
    }
    const std::vector<float> *n2g = p.vec(prefix + ".norm2.weight", d, err);
    const std::vector<float> *n2b = p.vec(prefix + ".norm2.bias", d, err);
    const V4Policy::Mat *f1W = p.mat(prefix + ".linear1.weight", 2 * d, d, err);
    const std::vector<float> *f1B = p.vec(prefix + ".linear1.bias", 2 * d, err);
    const V4Policy::Mat *f2W = p.mat(prefix + ".linear2.weight", d, 2 * d, err);
    const std::vector<float> *f2B = p.vec(prefix + ".linear2.bias", d, err);
    if (!err.empty()) {
        return false;
    }
    float *yv = sc.yv.data();
    for (int i = 0; i < len; i++) {
        for (int j = 0; j < d; j++) {
            yv[static_cast<size_t>(i) * d + j]
                    = x[static_cast<size_t>(i) * d + j] + sc.att[static_cast<size_t>(i) * d + j];
        }
        std::memcpy(xn + static_cast<size_t>(i) * d, yv + static_cast<size_t>(i) * d,
                    sizeof(float) * static_cast<size_t>(d));
        layerNormBuf(xn + static_cast<size_t>(i) * d, d, n2g->data(), n2b->data());
    }
    linearBatchFast(sc.hid.data(), 2 * d, xn, d, len, static_cast<size_t>(d), *f1W, f1B->data(), 0,
                    2 * d);
    reluBuf(sc.hid.data(), static_cast<size_t>(2) * ld);
    linearBatchFast(sc.res.data(), d, sc.hid.data(), 2 * d, len, static_cast<size_t>(2 * d), *f2W,
                    f2B->data(), 0, d);
    for (int i = 0; i < len; i++) {
        for (int j = 0; j < d; j++) {
            out[static_cast<size_t>(i) * d + j]
                    = yv[static_cast<size_t>(i) * d + j] + sc.res[static_cast<size_t>(i) * d + j];
        }
    }
    return true;
}

/** 头里那个"用 state 打一个宽度为 width 的线性层"（= Java `V4Policy.head`）。 */
bool head(const V4Policy &p, const std::vector<float> &state, const std::string &name, int width,
          std::vector<float> &out, std::string &err) {
    const V4Policy::Mat *w = p.mat("heads." + name + ".weight", width, p.dModel, err);
    const std::vector<float> *b = p.vec("heads." + name + ".bias", width, err);
    if (!err.empty()) {
        return false;
    }
    out.assign(static_cast<size_t>(width), 0.f);
    linear(out, state.data(), state.size(), *w, *b);
    return true;
}

// ---------------------------------------------------------------- 事件塔（W1：carry 也进融合）
//
// 为什么把事件塔单独拆出来：W1 起 `h_evt` 是融合的输入（`fusion.mem` 门控），而"窗口冷启动"
// 与"整手 carry"是**两个不同的量**（窗口 K=60 覆盖不到一整手）—— 生产全量路径必须给出与增量
// 缓存槽里的 `s.h` **逐位同源**的那个 carry（= Java `eventTowerRound`）。三条路线：
//   ① 生产全量 = `eventTowerRound`（整手 carry）；② 增量 = 槽里的 `s.h`（Java 侧，C++ 无缓存）；
//   ③ golden 夹具 = `eventTowerWindow`（`h0` 由 Python 显式给出，只喂窗口里的真实行）。

/** `event.enc` 的两层（v4：**末尾没有 ReLU**；A−：**只有一层 Linear + GELU**）。 */
struct EventEnc {
    const V4Policy::Mat *w1 = nullptr;
    const std::vector<float> *b1 = nullptr;
    const V4Policy::Mat *w2 = nullptr;
    const std::vector<float> *b2 = nullptr;
    /** `topoId`：A− 的编码器是单个 `Linear(C_EVT→d)` + GELU（`AEventTower.enc`）。 */
    int arch = kTopoV4;

    /** 绑定一次、逐行复用：逐行重放是热路径，没必要每行再查 4 次表 + 核形状。 */
    bool bind(const V4Policy &p, std::string &err) {
        const int dm = p.dModel;
        arch = p.arch;
        if (arch == kTopoA) {
            w1 = p.mat("event.enc.weight", dm, kCEvt, err);
            b1 = p.vec("event.enc.bias", dm, err);
            return err.empty();
        }
        w1 = p.mat("event.enc.0.weight", dm, kCEvt, err);
        b1 = p.vec("event.enc.0.bias", dm, err);
        w2 = p.mat("event.enc.2.weight", dm, dm, err);
        b2 = p.vec("event.enc.2.bias", dm, err);
        return err.empty();
    }

    /** 一条 token 行 → 事件编码器输出（第二个线性层与输入**别名** ⇒ 借 `sc.encTmp` 转一手）。 */
    void row(const float *tok, float *out, V4Scratch &sc) const;
    /**
     * **`len` 行一起算**（事件窗口 60 行、重放里的单行都走它）：第一个线性层按 token 批处理
     * （权重只扫一遍，见 `linearBatchFast`），激活逐元素 ⇒ 与逐行调用**逐位相同**。
     */
    void rows(const float *tok, int len, int tokStride, float *out, V4Scratch &sc) const;
};

/** `event.cell`（GRU）的四个张量 —— 同样绑一次、逐行复用。 */
struct EventGru {
    const V4Policy::Mat *wih = nullptr;
    const V4Policy::Mat *whh = nullptr;
    const std::vector<float> *bih = nullptr;
    const std::vector<float> *bhh = nullptr;

    bool bind(const V4Policy &p, std::string &err) {
        const int dm = p.dModel;
        wih = p.mat("event.cell.weight_ih", 3 * dm, dm, err);
        whh = p.mat("event.cell.weight_hh", 3 * dm, dm, err);
        bih = p.vec("event.cell.bias_ih", 3 * dm, err);
        bhh = p.vec("event.cell.bias_hh", 3 * dm, err);
        return err.empty();
    }
};

/**
 * 一步 GRU（原地：`h` 既是旧态也是新态）—— 六路门先全部算完再写 `h`，所以原地是安全的。
 *
 * <p>两处"不能省"的口径（= Java `V4Policy.gruStep`）：
 *   ① `ir = bih[i] + rowDot(wih, i, x)`：`rowDot` **从 0 起**累加、随后再加 bias —— 与
 *      `linear` 的"从 bias 起累加"**不是**同一个浮点序列 ⇒ 这里传 `b = nullptr`；
 *   ② `gh` 必须读**整条旧 h** ⇒ 先把 `gh` 算进 `sc.gates` 的后半，再逐元素写 `out`。
 */
void gruStepBuf(const float *x, float *h, const EventGru &gru, int d, V4Scratch &sc) {
    const size_t n3 = static_cast<size_t>(3) * static_cast<size_t>(d);
    if (sc.gates.size() < 2 * n3) {
        sc.gates.resize(2 * n3);
    }
    float *gx = sc.gates.data();
    float *gh = sc.gates.data() + n3;
    linearFast(gx, x, static_cast<size_t>(d), *gru.wih, nullptr, 0, 3 * d);
    linearFast(gh, h, static_cast<size_t>(d), *gru.whh, nullptr, 0, 3 * d);
    for (int i = 0; i < d; i++) {
        const float ir = (*gru.bih)[static_cast<size_t>(i)] + gx[static_cast<size_t>(i)];
        const float iz = (*gru.bih)[static_cast<size_t>(d + i)] + gx[static_cast<size_t>(d + i)];
        const float in
                = (*gru.bih)[static_cast<size_t>(2 * d + i)] + gx[static_cast<size_t>(2 * d + i)];
        const float hr = (*gru.bhh)[static_cast<size_t>(i)] + gh[static_cast<size_t>(i)];
        const float hz = (*gru.bhh)[static_cast<size_t>(d + i)] + gh[static_cast<size_t>(d + i)];
        const float hn
                = (*gru.bhh)[static_cast<size_t>(2 * d + i)] + gh[static_cast<size_t>(2 * d + i)];
        const float r
                = static_cast<float>(1.0 / (1.0 + std::exp(-static_cast<double>(ir + hr))));
        const float z
                = static_cast<float>(1.0 / (1.0 + std::exp(-static_cast<double>(iz + hz))));
        const float nn = static_cast<float>(std::tanh(static_cast<double>(in + r * hn)));
        const float old = h[static_cast<size_t>(i)];
        h[static_cast<size_t>(i)] = (1.f - z) * nn + z * old;
    }
}

void EventEnc::row(const float *tok, float *out, V4Scratch &sc) const {
    rows(tok, 1, kCEvt, out, sc);
}

void EventEnc::rows(const float *tok, int len, int tokStride, float *out, V4Scratch &sc) const {
    const int dm = w1->rows;
    linearBatchFast(out, dm, tok, tokStride, len, static_cast<size_t>(kCEvt), *w1, b1->data(), 0,
                    dm);
    if (arch == kTopoA) {
        // A−：`GELU(Linear(C_EVT→d))`，**没有第二层**（`AEventTower.enc`）
        geluBuf(out, static_cast<size_t>(len) * static_cast<size_t>(dm));
        return;
    }
    reluBuf(out, static_cast<size_t>(len) * static_cast<size_t>(dm));
    // 第二个线性层与 out 别名：先整块拷进 `sc.encTmp`（与 Java 的"别名先复制"同一条纪律）
    const size_t need = static_cast<size_t>(len) * static_cast<size_t>(dm);
    if (sc.encTmp.size() < need) {
        sc.encTmp.resize(need);
    }
    std::memcpy(sc.encTmp.data(), out, need * sizeof(float));
    linearBatchFast(out, dm, sc.encTmp.data(), dm, len, static_cast<size_t>(dm), *w2, b2->data(), 0,
                    dm);
}

/**
 * 窗口 60 行的两条产物：`e[60][dm]`（`event.enc` 之后的 GRU 输入）与 Transformer 表示。
 *
 * <p>两条 carry 路线在这里是**同一份代码**（Java 里那两段也是逐行同构）—— 所以"给定同一个 h、
 * 同一个窗口 ⇒ 同一个结果"是构造出来的，不是靠两处实现碰巧一样。
 */
/**
 * `Conv1d`（`stride=1`、`padding=k/2`，两端补 0）：
 * `y[o][t] = b[o] + Σ_c Σ_j W[o][c][j]·x[c][t+j-pad]`。
 *
 * <p>`x` 是 **[cin][len]**（通道在前 = PyTorch `conv1d` 的输入布局），`y` 是 **[cout][len]**。
 * `W` 的布局就是 `Conv1d.weight` 的 `[out][in][k]` 拍平 ⇒ 不用重建权重。
 * ⚠ 单累加器、`c` 升序再 `j` 升序、`bias` 起累加（与全仓其它算子的"别改求和顺序"同一条纪律；
 *   三端判据是 `max|Δ|`，卷积在 torch 侧的求和顺序本来就不保证，所以这里只保证**确定性**）。
 */
void conv1dBuf(float *y, const float *x, int len, const V4Policy::Conv &w, const float *bias,
               int pad, int cout) {
    const int cin = w.in;
    const int k = w.k;
    for (int o = 0; o < cout; o++) {
        for (int t = 0; t < len; t++) {
            float s = bias[o];
            for (int c = 0; c < cin; c++) {
                const float *row = w.data.data()
                        + (static_cast<size_t>(o) * static_cast<size_t>(cin)
                           + static_cast<size_t>(c)) * static_cast<size_t>(k);
                const float *xc = x + static_cast<size_t>(c) * static_cast<size_t>(len);
                for (int j = 0; j < k; j++) {
                    const int pos = t + j - pad;
                    if (pos >= 0 && pos < len) {
                        s += row[j] * xc[pos];
                    }
                }
            }
            y[static_cast<size_t>(o) * static_cast<size_t>(len) + t] = s;
        }
    }
}

/**
 * A− 的事件窗口卷积塔：`GELU(LN(conv1(GELU(LN(e))))) → conv2`（`AEventTower._tokens`）。
 *
 * <p>输入/输出都是**token 在前** `[len][d]`；中间为卷积转成 `[d][len]`。
 * ⚠ 卷积**不改变 GRU 的 carry**（carry 在 `e` 上推进）—— 这是增量缓存契约不变的前提
 * （`AEventTower` 的 docstring 明写：别把卷积放到 GRU 之前，否则 `h` 依赖窗口起点）。
 */
bool eventConvTower(const V4Policy &p, const float *e, int len, float *out, V4Scratch &sc,
                    std::string &err) {
    const int d = p.dModel;
    const size_t ld = static_cast<size_t>(len) * static_cast<size_t>(d);
    // 取张量（形状在 bindAllA 已核过；这里只取指针）
    const auto cv = [&](const char *n, int outCh, int inCh, int k) -> const V4Policy::Conv * {
        const auto it = p.convs.find(n);
        if (it == p.convs.end() || it->second.out != outCh || it->second.in != inCh
                || it->second.k != k) {
            err = std::string("张量 ") + n + " 缺失或形状不符";
            return nullptr;
        }
        return &it->second;
    };
    const V4Policy::Conv *conv1 = cv("event.conv1.weight", d, d, 3);
    const V4Policy::Conv *conv2 = cv("event.conv2.weight", d, d, 3);
    const auto vv = [&](const char *n, int w) -> const std::vector<float> * {
        const auto it = p.vecs.find(n);
        if (it == p.vecs.end() || static_cast<int>(it->second.size()) != w) {
            err = std::string("张量 ") + n + " 缺失或长度不符";
            return nullptr;
        }
        return &it->second;
    };
    const std::vector<float> *b1 = vv("event.conv1.bias", d);
    const std::vector<float> *g1 = vv("event.norm1.weight", d);
    const std::vector<float> *bb1 = vv("event.norm1.bias", d);
    const std::vector<float> *b2 = vv("event.conv2.bias", d);
    const std::vector<float> *g2 = vv("event.norm2.weight", d);
    const std::vector<float> *bb2 = vv("event.norm2.bias", d);
    if (conv1 == nullptr || conv2 == nullptr || b1 == nullptr || g1 == nullptr || bb1 == nullptr
            || b2 == nullptr || g2 == nullptr || bb2 == nullptr) {
        return false;
    }
    if (sc.aBuf.size() < ld) {
        sc.aBuf.resize(ld);
        sc.aBuf2.resize(ld);
        sc.xn.resize(ld);
    }
    float *tok = sc.xn.data();       // [len][d]
    float *ch = sc.aBuf.data();      // [d][len]
    float *ch2 = sc.aBuf2.data();    // [d][len]
    // ① GELU(LN(e))（逐 token，LN 在特征维上）
    for (int t = 0; t < len; t++) {
        std::memcpy(tok + static_cast<size_t>(t) * d, e + static_cast<size_t>(t) * d,
                    sizeof(float) * static_cast<size_t>(d));
        layerNormBuf(tok + static_cast<size_t>(t) * d, d, g1->data(), bb1->data());
    }
    geluBuf(tok, ld);
    // ② 转置 → conv1
    for (int t = 0; t < len; t++) {
        for (int j = 0; j < d; j++) {
            ch[static_cast<size_t>(j) * len + t] = tok[static_cast<size_t>(t) * d + j];
        }
    }
    conv1dBuf(ch2, ch, len, *conv1, b1->data(), 1, d);
    // ③ 转置 → GELU(LN(·)) → 转置 → conv2
    for (int t = 0; t < len; t++) {
        for (int j = 0; j < d; j++) {
            tok[static_cast<size_t>(t) * d + j] = ch2[static_cast<size_t>(j) * len + t];
        }
        layerNormBuf(tok + static_cast<size_t>(t) * d, d, g2->data(), bb2->data());
    }
    geluBuf(tok, ld);
    for (int t = 0; t < len; t++) {
        for (int j = 0; j < d; j++) {
            ch[static_cast<size_t>(j) * len + t] = tok[static_cast<size_t>(t) * d + j];
        }
    }
    conv1dBuf(ch2, ch, len, *conv2, b2->data(), 1, d);
    // ④ 转回 token 在前
    for (int t = 0; t < len; t++) {
        for (int j = 0; j < d; j++) {
            out[static_cast<size_t>(t) * d + j] = ch2[static_cast<size_t>(j) * len + t];
        }
    }
    return true;
}

bool encodeWindow(const V4Policy &p, const V4Tensors &t, const EventEnc &enc, V4Scratch &sc,
                  std::string &err) {
    if (sc.eRows.size() < static_cast<size_t>(kKEvt) * static_cast<size_t>(p.dModel)) {
        sc.eRows.resize(static_cast<size_t>(kKEvt) * static_cast<size_t>(p.dModel));
        sc.eTokens.resize(static_cast<size_t>(kKEvt) * static_cast<size_t>(p.dModel));
    }
    enc.rows(t.evt[0].data(), kKEvt, kCEvt, sc.eRows.data(), sc);
    if (p.arch == kTopoA) {
        return eventConvTower(p, sc.eRows.data(), kKEvt, sc.eTokens.data(), sc, err);
    }
    return transformerLayer(p, sc.eRows.data(), kKEvt, "event.tr.layers.0", sc.eTokens.data(), sc,
                            err);
}

/**
 * 窗口里**真实事件**的行数（前部是零 padding，**不喂** GRU）= Java `V4Policy.realRows`。
 *
 * <p>判据靠"整行是否全 0"：真实事件 token 的 `type` 段是 one-hot，恒有一位为 1 ⇒ 全 0 行只可能
 * 是 padding。还要确认零行**都在真实行之前**（否则窗口形状就坏了）—— 不一致当场报错，
 * 而不是算出一个"看起来能用"的 carry。为什么不能连 padding 一起喂：`GRUCell(0,h) ≠ h`，
 * 历史实测那会让两条路线的 carry 差出 1e-2 量级。
 */
bool realRows(const V4Tensors &t, int &out, std::string &err) {
    int real = 0;
    for (int row = 0; row < kKEvt; row++) {
        bool nz = false;
        for (float x : t.evt[static_cast<size_t>(row)]) {
            if (x != 0.f) {
                nz = true;
                break;
            }
        }
        if (nz) {
            real++;
        } else if (real > 0) {
            err = "事件窗口的零行出现在真实事件之后（形状坏了）";
            return false;
        }
    }
    out = real;
    return true;
}

/** 负向判据注入（缺省关；只有 `--v4-carry-bug` 会打开，见 hpp）。 */
enum class CarryBug { None, SkipFirst, StaleKey, NoClear };
CarryBug g_carryBug = CarryBug::None;
const char *g_carryBugName = "";
bool g_carryEnabledDefault = true;

/**
 * **逐行事件编码的唯一入口** —— `v4EventRow` 自带清零（纪律③：调用方复用同一支缓冲，
 * 不清零就是"前 i 条事件按位或"的静默累积，Java 第六十轮踩过）。
 *
 * <p>`noclear` 负向注入：把"本次没写的格"从上一行搬回来 —— 那正是"没清零"时缓冲里实际留着的
 * 东西（写 0 与没写区分不了）。它同时作用于**整手重放**与增量路径的逐行编码 ⇒ 两条路会分叉，
 * 判据必然红（这正是"不清零"必须被抓住的理由）。
 */
bool encodeRowTok(const JVal &e, int seat, V4Scratch &sc, std::string &err) {
    if (!v4EventRow(e, seat, sc.tok.data(), err)) {
        return false;
    }
    if (g_carryBug == CarryBug::NoClear) {
        if (sc.bugTok.size() != static_cast<size_t>(kCEvt)) {
            sc.bugTok.assign(static_cast<size_t>(kCEvt), 0.f);
        }
        for (int c = 0; c < kCEvt; c++) {
            if (sc.tok[static_cast<size_t>(c)] == 0.f) {
                sc.tok[static_cast<size_t>(c)] = sc.bugTok[static_cast<size_t>(c)];
            }
        }
        std::copy(sc.tok.begin(), sc.tok.begin() + kCEvt, sc.bugTok.begin());
    }
    return true;
}

/**
 * 把 `ev[0, upto)` 这 `upto` 条**真实**事件行喂进 GRU（从 0 起）= Java `replayCarry`。
 *
 * <p>⚠ 每条事件的 token 行都过 `v4EventRow`（**自带清零**）：这里复用同一支缓冲逐事件重放，
 * 不清零就是**静默累积**（第 i 行 = 前 i 条事件按位或）—— Java 侧刚踩过，见 NOTES §6.5 第六十轮。
 * ⚠ 单行编码用 `sc.rowTmp`（**不能借 `sc.eRows`**：窗口那 60 行 `e` 待会儿还要用）。
 */
bool replayCarry(const V4Policy &p, const std::vector<const JVal *> &ev, int seat, int upto,
                 const EventEnc &enc, const EventGru &gru, V4Scratch &sc, std::string &err) {
    const int dm = p.dModel;
    for (int i = 0; i < upto; i++) {
        if (!encodeRowTok(*ev[static_cast<size_t>(i)], seat, sc, err)) {
            return false;
        }
        enc.row(sc.tok.data(), sc.rowTmp.data(), sc);
        gruStepBuf(sc.rowTmp.data(), sc.carry.data(), gru, dm, sc);
    }
    return true;
}

/**
 * **整手重放的后半段**（窗口那 60 行**已经编码好**）：先逐行重放 `events[0, max(0, n-K))`，
 * 再喂窗口里的**真实**行 ⇒ "整手 carry"（= 增量路径槽里的 `s.h`）= Java `eventTowerRound`。
 *
 * <p>拆出来只是为了让"没有 round 信息 ⇒ 不缓存"那条路**不用把窗口再编码一遍**；
 * 算子顺序与拆分前**逐句相同**（判据：同种子轨迹逐字节）。
 */
bool roundAdvance(const V4Policy &p, const JVal &obs, const V4Tensors &t, const EventEnc &enc,
                  const EventGru &gru, V4Scratch &sc, std::string &err) {
    const std::vector<const JVal *> ev = v4EventsOf(obs);
    const int events = static_cast<int>(ev.size());
    // obs 的 `seat` 是**数字**（`v4Assemble` 已核过它在 0..3）：这里只要那一条语义。
    const JVal *seatVal = obs.find("seat");
    const int seat = seatVal == nullptr ? 0 : seatVal->asInt(0);
    std::fill(sc.carry.begin(), sc.carry.end(), 0.f);
    if (!replayCarry(p, ev, seat, std::max(0, events - kKEvt), enc, gru, sc, err)) {
        return false;
    }
    const int dm = p.dModel;
    const int real = std::min(events, kKEvt);
    for (int i = kKEvt - real; i < kKEvt; i++) {
        gruStepBuf(sc.eRows.data() + static_cast<size_t>(i) * dm, sc.carry.data(), gru, dm, sc);
    }
    return true;
}

/**
 * **窗口内**推进：从 `h0`（**窗口之前**的 carry）推进窗口里的**真实**行 = Java `eventTowerWindow`。
 *
 * <p>golden 夹具（格式 2）走这条：三端拿同一个 `h0` 与同一个窗口，`h_evt` 必须逐位相同。
 * `<p>进入前 `sc.carry` 必须是 `h0`。
 */
bool eventTowerWindow(const V4Policy &p, const V4Tensors &t, V4Scratch &sc, std::string &err) {
    EventEnc enc;
    EventGru gru;
    if (!enc.bind(p, err) || !gru.bind(p, err)) {
        return false;
    }
    if (!encodeWindow(p, t, enc, sc, err)) {
        return false;
    }
    int real = 0;
    if (!realRows(t, real, err)) {
        return false;
    }
    const int dm = p.dModel;
    for (int i = kKEvt - real; i < kKEvt; i++) {
        gruStepBuf(sc.eRows.data() + static_cast<size_t>(i) * dm, sc.carry.data(), gru, dm, sc);
    }
    return true;
}

/**
 * **整手**推进（生产全量路径）：先重放 `events[0, max(0, n-K))`，再喂窗口里的真实行
 * ⇒ "整手 carry"（= 增量路径槽里的 `s.h`）= Java `eventTowerRound`。
 *
 * <p>⛔ 不能退回"窗口 K 行冷启动"：那与槽里的 carry 是两个不同的量，而 W1 起它被融合消费
 * （两条生产路径会分叉、三端也会分叉）。
 */
bool eventTowerRound(const V4Policy &p, const JVal &obs, const V4Tensors &t, V4Scratch &sc,
                     std::string &err) {
    EventEnc enc;
    EventGru gru;
    if (!enc.bind(p, err) || !gru.bind(p, err)) {
        return false;
    }
    if (!encodeWindow(p, t, enc, sc, err)) {
        return false;
    }
    return roundAdvance(p, obs, t, enc, gru, sc, err);
}

}  // namespace

// ⚠ 从这里起是**具名** namespace：`V4CarryCache` 必须是 `trainer::V4CarryCache`（头文件里
//   `V4Scratch` 的 `shared_ptr<V4CarryCache>` 指的就是它）—— 匿名 namespace 里的同名类型是
//   **另一个类型**，那正是"shared_ptr 类型不匹配 / 成员访问到不完整类型"的来源。

// ---------------------------------------------------------------- 增量事件 GRU carry（缓存）
//
// 为什么做：生产全量路径每决策都要从事件 0 重放**整手**（`events[0, n-K)` 逐行编码 + GRU），
// 固定 `nlegal` 解耦后实测占前向 **18.6%**（NOTES §6.5 第十一/六十一轮）。而"一条事件的 token 行 /
// 它的编码行 / GRU 的一步"都只依赖 `(那条事件, 自己座位)` ⇒ 只要"小局身份没变 + 前缀逐位没变"，
// carry 就可以**增量推进**：每决策只喂新增的 delta 行（实测中位数 1–2 行）。
//
// 语义与 Java `V4Policy.eventTowerCached`（`V4Cache`）**同一套**：按座位分槽 · 键 = 小局身份 ·
// 前缀逐行校验 · 对不上就丢槽退回整手重放。⛔ 四条不许省（每条都对应一次真事故或一条判据）：
//   ① **键换了必须重建**（每小局 carry 清零）—— 拿上一局的 carry 接着推是静默错；
//   ② **前缀必须逐位校验**（不信"应该是它"）：任何一格不等 ⇒ 丢槽重建（计 stale）；
//   ③ 逐行编码走 `v4EventRow`（**自带清零**）—— 复用同一支缓冲不清零 = 静默累积（第六十轮）；
//   ④ **只喂真实事件行**（前部零 padding 不喂）：`GRUCell(0,h) ≠ h`，喂了两条路差 1e-2。

/** 一个座位的 carry 槽（= Java `V4Cache.Slot`：只留"增量 carry"必需的那两样）。 */
struct CarrySlot {
    std::string key;          // 小局身份（场风-局-本场@座位）
    int seen = 0;             // 已经推进（编码 + GRU）的**绝对**事件条数
    int len = 0;              // 上一次窗口里的真实事件行数
    bool valid = false;
    std::vector<float> h;     // dModel：`events[0, seen)` 的 carry
    /**
     * 上一次的**窗口 token 行**（`kKEvt × kCEvt` 扁平；行 p = 绝对下标 `seen-K+p`）——
     * 只给前缀校验用（Java `Slot.tok`）。⛔ 校验必须逐位，不信"应该是它"。
     */
    std::vector<float> win;
};

/** 四个槽 + 计数器（= Java `V4Cache` 的状态部分；`V4Scratch` 里持一份 `shared_ptr`）。 */
struct V4CarryCache {
    CarrySlot slots[4];
    V4CarryStats st;
};

/** 座位 → 槽（`floorMod`；= Java `V4Cache.slot`）。 */
inline int carrySlotOf(int seat) {
    const int m = seat % 4;
    return m < 0 ? m + 4 : m;
}

/** 事件窗口的**重叠区间**逐位比对（= Java `V4Cache.overlapOk`，判据与它同一把尺子）。 */
bool carryOverlapOk(const CarrySlot &s, const V4Tensors &t, int events) {
    if (s.win.size() != static_cast<size_t>(kKEvt) * static_cast<size_t>(kCEvt)) {
        return false;
    }
    const int lo = std::max(0, events - kKEvt);
    const int hi = std::min(s.seen, events);
    for (int i = lo; i < hi; i++) {
        // ⚠ 行位置恒为 `绝对下标 - seen + K`（窗口贴着事件流尾巴：前部 padding、新事件在尾部）。
        //   写成 `i-(seen-len)` 只在窗口满时才对 ⇒ 每小局前 K 条事件全被判"陈旧"、缓存形同虚设
        //   （Java 第十一轮踩过：600 决策里退回 533 次，而"增量 == 全量"照样全绿）。
        const int oldRow = i - s.seen + kKEvt;
        const int newRow = i - events + kKEvt;
        if (oldRow < 0 || oldRow >= kKEvt || newRow < 0 || newRow >= kKEvt) {
            return false;
        }
        const float *a = s.win.data() + static_cast<size_t>(oldRow) * kCEvt;
        const float *b = t.evt[static_cast<size_t>(newRow)].data();
        if (std::memcmp(a, b, static_cast<size_t>(kCEvt) * sizeof(float)) != 0) {
            return false;                     // 逐位：NaN / -0.0 也不会被放过
        }
    }
    return true;
}

/**
 * 把 `ev[from, upto)` 喂进 GRU（`h` 原地推进）。
 *
 * <p>行从哪来：绝对下标 ≥ `events-K` 的行**直接取窗口的批编码行**（`sc.eRows`）—— 与整手重放
 * 走的是**同一批比特**（`encodeWindow` 已经算好）；更早的行逐行编码（`v4EventRow` + `enc.row`，
 * 与 `replayCarry` 同一条）。
 */
bool advanceCarry(const V4Policy &p, const std::vector<const JVal *> &ev, int seat, int from,
                  int upto, int events, const EventEnc &enc, const EventGru &gru, float *h,
                  V4Scratch &sc, V4CarryCache &cc, bool rebuilt, std::string &err) {
    const int dm = p.dModel;
    const int winBase = events - kKEvt;
    for (int i = from; i < upto; i++) {
        const float *row = nullptr;
        if (i >= winBase) {
            row = sc.eRows.data() + static_cast<size_t>(i - winBase) * dm;
            cc.st.rowsWindow++;
        } else {
            if (!encodeRowTok(*ev[static_cast<size_t>(i)], seat, sc, err)) {
                return false;
            }
            enc.row(sc.tok.data(), sc.rowTmp.data(), sc);
            row = sc.rowTmp.data();
            cc.st.rowsEncoded++;
        }
        if (rebuilt) {
            cc.st.rowsReplayed++;      // 建槽那一次是把整手重放一遍（两个来源都算）
        }
        gruStepBuf(row, h, gru, dm, sc);
    }
    return true;
}

/**
 * **增量路径的事件塔**（生产缺省）= Java `V4Policy.eventTowerCached`。
 *
 * <p>每决策：① 窗口那 60 行照常批编码（Transformer 的输入**必须**重算）；② carry 只推进
 * `[seen, events)` 的新事件；③ 把本决策的窗口行存进槽里当**下次**的前缀校验基准。
 * 产出的 carry 与整手重放**逐位相同**（判据：`trainer v4cache` 的逐位比对）。
 */
bool eventTowerCached(const V4Policy &p, const JVal &obs, const V4Tensors &t, V4Scratch &sc,
                      std::string &err) {
    EventEnc enc;
    EventGru gru;
    if (!enc.bind(p, err) || !gru.bind(p, err)) {
        return false;
    }
    if (!encodeWindow(p, t, enc, sc, err)) {
        return false;
    }
    const std::vector<const JVal *> ev = v4EventsOf(obs);
    const int events = static_cast<int>(ev.size());
    const JVal *seatVal = obs.find("seat");
    const int seat = seatVal == nullptr ? 0 : seatVal->asInt(0);
    std::string key;
    int keySeat = seat;
    if (!v4CarryHandKeyOf(obs, keySeat, key)) {
        // 没有 `round` 信息 ⇒ **不缓存**，但仍是整手 carry（宁可不省，也不能拿上一局的当这一局的）
        return roundAdvance(p, obs, t, enc, gru, sc, err);
    }
    // ★ 段 3b（`A-REFACTOR-IMPL-PLAN.md` 风险 5）：槽键 = **网络身份** + 小局身份。
    //   旧键只有 `handKey` ⇒ 同进程换网/换 arch 后，旧槽会被当成"这一局的 carry"静默复用
    //   （carry 宽度/语义都变了）。判据：`trainer v4cache --swap-net`（换网后 B 的增量必须
    //   仍与它自己的整手重放逐位相同）。
    //   ⚠ 负向注入 `TRAINER_V4_OLD_KEY=1` ⇒ 回到旧口径（只给判据用，证明判据有牙齿；
    //     ⛔ 采集与训练里绝不能设 —— 它会让换网后的 carry 静默错）。
    if (std::getenv("TRAINER_V4_OLD_KEY") == nullptr) {
        key = p.cacheTag() + "|" + key;
    }
    if (sc.carryCache == nullptr) {
        sc.carryCache = std::make_shared<V4CarryCache>();
    }
    V4CarryCache &cc = *sc.carryCache;
    CarrySlot &s = cc.slots[static_cast<size_t>(carrySlotOf(keySeat))];
    cc.st.decisions++;
    bool rebuild = !s.valid || s.key != key;
    const bool trustSlot = g_carryBug == CarryBug::StaleKey;
    if (trustSlot) {
        // 负向注入：**不认小局键、也不做前缀校验**（= "缓存做旧但结果看起来正常"那条失败模式）
        rebuild = !s.valid;
    }
    if (!rebuild && !trustSlot && (events < s.seen || !carryOverlapOk(s, t, events))) {
        cc.st.stale++;
        rebuild = true;                     // 前缀对不上：丢掉整个槽（Java 同一条）
    }
    if (rebuild) {
        cc.st.cold++;
        s.key = key;        s.seen = 0;
        s.len = 0;
        s.valid = true;
        if (s.h.size() != static_cast<size_t>(p.dModel)) {
            s.h.assign(static_cast<size_t>(p.dModel), 0.f);      // 每小局从 0 起（纪律①）
        } else {
            std::fill(s.h.begin(), s.h.end(), 0.f);
        }
    } else {
        cc.st.hits++;
    }
    float *h = s.h.data();
    int from = s.seen;
    if (!rebuild && g_carryBug == CarryBug::SkipFirst) {
        from = std::min(events, s.seen + 1);   // 负向注入：漏喂第一条新事件
    }
    if (!advanceCarry(p, ev, keySeat, rebuild ? 0 : from, events, events, enc, gru, h, sc, cc,
                      rebuild, err)) {
        return false;
    }
    s.seen = events;
    s.len = std::min(events, kKEvt);
    // 存下本决策的窗口行（**下一次**的前缀校验基准）。⚠ 必须**整窗**存（含前部零 padding）：
    // 行的绝对位置 = `i - seen + K`，只存真实行会让"窗口没满"时下标对不上（Java 每决策刷新
    // padding 行是同一条理由）。
    s.win.resize(static_cast<size_t>(kKEvt) * static_cast<size_t>(kCEvt));
    std::memcpy(s.win.data(), t.evt[0].data(),
                static_cast<size_t>(kKEvt) * static_cast<size_t>(kCEvt) * sizeof(float));
    // carry 交回 `sc.carry`（融合从它读 `h_evt`；槽里那份是权威、这份是本次前向的入口）
    std::copy(s.h.begin(), s.h.end(), sc.carry.begin());
    return true;
}

/** 事件塔的**生产入口**：缓存开（缺省）走增量，关走整手重放（= 判据的基准路径）。 */
bool eventTowerProduction(const V4Policy &p, const JVal &obs, const V4Tensors &t, V4Scratch &sc,
                          std::string &err) {
    if (!sc.carryEnabled) {
        return eventTowerRound(p, obs, t, sc, err);
    }
    return eventTowerCached(p, obs, t, sc, err);
}

// ---------------------------------------------------------------- 增量 carry 的对外口（开关 / 读数 / 键）

bool v4CarryEnabledDefault() { return g_carryEnabledDefault; }

void v4SetCarryEnabled(bool on) { g_carryEnabledDefault = on; }

bool v4SetCarryBug(const std::string &mode, std::string &err) {
    if (mode.empty() || mode == "off" || mode == "none") {
        g_carryBug = CarryBug::None;
        g_carryBugName = "";
        return true;
    }
    if (mode == "skip1") {
        g_carryBug = CarryBug::SkipFirst;
        g_carryBugName = "skip1";
        return true;
    }
    if (mode == "stale") {
        g_carryBug = CarryBug::StaleKey;
        g_carryBugName = "stale";
        return true;
    }
    if (mode == "noclear") {
        g_carryBug = CarryBug::NoClear;
        g_carryBugName = "noclear";
        return true;
    }
    err = "未知的 --v4-carry-bug 模式：" + mode
          + "（只有 skip1 / stale / noclear；⛔ 这是**负向判据**开关，采集与训练里绝不能给）";
    return false;
}

const char *v4CarryBugName() { return g_carryBugName; }

bool v4CarryHandKeyOf(const JVal &obs, int &seat, std::string &key) {
    // `seat` 是**出参**：键里带座位（同一份 scratch 也可能服务多个座位 —— Java `V4Cache` 同式）。
    const JVal *seatVal = obs.find("seat");
    seat = seatVal == nullptr ? 0 : seatVal->asInt(0);
    const JVal *rnd = obs.find("round");
    if (rnd == nullptr || !rnd->isObj()) {
        key.clear();
        return false;
    }
    const JVal *b = rnd->find("bakaze");
    const JVal *ky = rnd->find("kyoku");
    const JVal *hb = rnd->find("honba");
    key = (b != nullptr && b->isStr() ? b->strVal : std::string("?")) + "-"
          + std::to_string(ky == nullptr ? 0 : ky->asInt(0)) + "-"
          + std::to_string(hb == nullptr ? 0 : hb->asInt(0)) + "@" + std::to_string(seat);
    return true;
}

V4CarryStats v4CarryStatsOf(const V4Scratch &scr) {
    V4CarryStats out;
    if (scr.carryCache != nullptr) {
        out = scr.carryCache->st;
    }
    return out;
}

void v4CarryResetStats(V4Scratch &scr) {
    if (scr.carryCache != nullptr) {
        scr.carryCache->st = V4CarryStats{};
    }
}

/**
 * **A−（topoId=3）前向** —— 语义权威 = `python/mahjong_ml/v4/model.py`（`ATileTower` /
 * `AEventTower` / `AFusion` / `AHeads`）。
 *
 * <p>层次（`ARCH-REDESIGN.md` §2.2 的 T1 表；括号里是 Python 的类）：
 *   tile：`Conv1d(48→160,k=5)+LN+GELU` → `1×k5 + 2×k3` pre-LN 残差卷积 → 轴向 MHA(4,d=160)
 *         （pre-LN、残差）→ `GELU → Linear(160→128) → LN`；`[34,48]` **转成 `[48,34]`** 做卷积
 *         （⛔ 不重建数据）；**#0 `tile_id` 在转置之前加**；
 *   evt ：`Linear(96→128)+GELU` → `GRUCell(128,128)`（**carry 契约不变**）→ `2×Conv1d(k=3)+LN+GELU`；
 *   fusion：`95 = 34 tile + 60 evt + 1 h_evt` token 的 joint self-attn（段嵌入 3 种 + evt 的
 *         sin/cos 相对位置）→ `FFN(128→128→128)` → `cross(cand→95)` → `LN(384) → Linear(384→128)`
 *         → B3；
 *   heads：`value` 81 bin（±60）· `danger` 经 **0 参数固定通道**进 policy · `effect` 只给 caller
 *         做 tie-break。
 *
 * <p>⚠ 三处与 v4 **结构性不同**、漏一处就是静默换语义：① 激活是 **GELU-erf**（不是 ReLU）；
 * ② `fusion.out` 之前是 **LN(384)（pre-LN，带参数）**；③ `h_evt` 是**第 95 个 token**（不再是
 * 逐维门控）。
 */
bool V4Policy::forwardA(const JVal &obs, std::map<std::string, std::vector<float>> &outAll,
                        std::string &err, const std::vector<float> *h0, V4Scratch *scr) const {
    err.clear();
    V4Tensors t;
    if (!v4Assemble(obs, missingBlocks, t, err)) {
        return false;
    }
    V4Scratch localScr;
    V4Scratch &sc = scr != nullptr ? *scr : localScr;
    const int dm = dModel;
    const int td = tileD;
    const int n = static_cast<int>(t.cand.size());
    const int maxCand = std::max(1, n);

    // ---- 事件塔（与 v4 同一套 carry 契约：窗口喂真实行、`h_evt` = 窗口之后的 carry）
    if (sc.carry.size() < static_cast<size_t>(dm)) {
        sc.carry.assign(static_cast<size_t>(dm), 0.f);
        sc.tok.assign(static_cast<size_t>(kCEvt), 0.f);
        sc.rowTmp.assign(static_cast<size_t>(dm), 0.f);
    }
    if (h0 != nullptr) {
        if (static_cast<int>(h0->size()) != dm) {
            err = "h0 长度 " + std::to_string(h0->size()) + " != d_model " + std::to_string(dm)
                  + "（既不截断也不补零）";
            return false;
        }
        std::copy(h0->begin(), h0->end(), sc.carry.begin());
        if (!eventTowerWindow(*this, t, sc, err)) {
            return false;
        }
    } else if (!eventTowerProduction(*this, obs, t, sc, err)) {
        return false;
    }
    const float *eTokens = sc.eTokens.data();
    const float *hEvt = sc.carry.data();

    // 取张量的小包装（形状在 bindAllA 已钉死；这里只取指针 + 防呆）
    const auto matOf = [&](const char *name, int rows, int cols) -> const Mat * {
        return mat(name, rows, cols, err);
    };
    const auto vecOf = [&](const char *name, int len) -> const std::vector<float> * {
        return vec(name, len, err);
    };
    const auto convOf = [&](const char *name, int outCh, int inCh, int k) -> const Conv * {
        const auto it = convs.find(name);
        if (it == convs.end() || it->second.out != outCh || it->second.in != inCh
                || it->second.k != k) {
            err = std::string("张量 ") + name + " 缺失或形状不符";
            return nullptr;
        }
        return &it->second;
    };

    // ---- 牌种塔：`[34,48] → [48,34]`（卷积）→ … → `[34,128]` + pool
    std::vector<float> xin(static_cast<size_t>(kCTile) * static_cast<size_t>(kKindCount));
    const Mat *tid = mats.count("tile.tile_id") ? &mats.at("tile.tile_id") : nullptr;
    for (int k = 0; k < kKindCount; k++) {
        for (int c = 0; c < kCTile; c++) {
            float v = t.tile[static_cast<size_t>(k)][static_cast<size_t>(c)];
            if (tid != nullptr) {
                v += tid->data[static_cast<size_t>(k) * kCTile + c];   // ★ #0：**先加身份**
            }
            xin[static_cast<size_t>(c) * kKindCount + k] = v;
        }
    }
    const size_t tileN = static_cast<size_t>(kKindCount) * static_cast<size_t>(td);
    std::vector<float> tok(tileN, 0.f);        // [34][td]
    std::vector<float> ch(static_cast<size_t>(td) * kKindCount, 0.f);    // [td][34]
    std::vector<float> ch2(static_cast<size_t>(td) * kKindCount, 0.f);   // [td][34]
    std::vector<float> acc(tileN, 0.f);        // [34][td]（残差/注意力输出）
    {
        const Conv *stem = convOf("tile.stem.weight", td, kCTile, 5);
        const std::vector<float> *stemB = vecOf("tile.stem.bias", td);
        const std::vector<float> *snG = vecOf("tile.stem_norm.weight", td);
        const std::vector<float> *snB = vecOf("tile.stem_norm.bias", td);
        if (stem == nullptr || stemB == nullptr || snG == nullptr || snB == nullptr) {
            return false;
        }
        conv1dBuf(ch.data(), xin.data(), kKindCount, *stem, stemB->data(), 2, td);
        for (int k = 0; k < kKindCount; k++) {
            for (int o = 0; o < td; o++) {
                tok[static_cast<size_t>(k) * td + o]
                        = ch[static_cast<size_t>(o) * kKindCount + k];
            }
            layerNormBuf(tok.data() + static_cast<size_t>(k) * td, td, snG->data(), snB->data());
        }
        geluBuf(tok.data(), tileN);
    }
    for (int b = 0; b < 3; b++) {
        const std::string pre = "tile.res." + std::to_string(b);
        const int ksz = b == 0 ? 5 : 3;
        const Conv *cv = convOf((pre + ".conv.weight").c_str(), td, td, ksz);
        const std::vector<float> *cvB = vecOf((pre + ".conv.bias").c_str(), td);
        const std::vector<float> *nG = vecOf((pre + ".norm.weight").c_str(), td);
        const std::vector<float> *nB = vecOf((pre + ".norm.bias").c_str(), td);
        if (cv == nullptr || cvB == nullptr || nG == nullptr || nB == nullptr) {
            return false;
        }
        // pre-LN + GELU（逐 token），再转成通道在前做卷积；**残差先存起来**（LN 是原地改写）
        for (int k = 0; k < kKindCount; k++) {
            std::memcpy(acc.data() + static_cast<size_t>(k) * td,
                        tok.data() + static_cast<size_t>(k) * td,
                        sizeof(float) * static_cast<size_t>(td));
            layerNormBuf(tok.data() + static_cast<size_t>(k) * td, td, nG->data(), nB->data());
        }

        geluBuf(tok.data(), tileN);
        for (int k = 0; k < kKindCount; k++) {
            for (int o = 0; o < td; o++) {
                ch[static_cast<size_t>(o) * kKindCount + k]
                        = tok[static_cast<size_t>(k) * td + o];
            }
        }
        conv1dBuf(ch2.data(), ch.data(), kKindCount, *cv, cvB->data(), ksz / 2, td);
        for (int k = 0; k < kKindCount; k++) {
            for (int o = 0; o < td; o++) {
                const float a = ch2[static_cast<size_t>(o) * kKindCount + k];
                const size_t idx = static_cast<size_t>(k) * td + o;
                tok[idx] = acc[idx] + a;               // 残差在**通道**上（不是时间轴）
            }
        }
    }
    // 轴向注意力（pre-LN、残差）：34 个 token 之间的全局混合
    {
        const std::vector<float> *anG = vecOf("tile.attn_norm.weight", td);
        const std::vector<float> *anB = vecOf("tile.attn_norm.bias", td);
        if (anG == nullptr || anB == nullptr) {
            return false;
        }
        if (sc.xn.size() < tileN) {
            sc.xn.resize(tileN);
        }
        for (int k = 0; k < kKindCount; k++) {
            std::memcpy(sc.xn.data() + static_cast<size_t>(k) * td, tok.data() + static_cast<size_t>(k) * td,
                        sizeof(float) * static_cast<size_t>(td));
            layerNormBuf(sc.xn.data() + static_cast<size_t>(k) * td, td, anG->data(), anB->data());
        }
        if (!mha(*this, sc.xn.data(), kKindCount, sc.xn.data(), kKindCount, td, "tile.attn",
                 acc.data(), sc, err)) {
            return false;
        }
        for (size_t i = 0; i < tileN; i++) {
            tok[i] += acc[i];
        }
    }
    // `GELU → Linear(160→128) → LN(128)`
    if (sc.candEmb.size() < static_cast<size_t>(kKindCount) * static_cast<size_t>(dm)) {
        sc.candEmb.resize(static_cast<size_t>(kKindCount) * static_cast<size_t>(dm));
    }
    geluBuf(tok.data(), tileN);
    {
        const Mat *pw = matOf("tile.proj.weight", dm, td);
        const std::vector<float> *pb = vecOf("tile.proj.bias", dm);
        const std::vector<float> *pnG = vecOf("tile.proj_norm.weight", dm);
        const std::vector<float> *pnB = vecOf("tile.proj_norm.bias", dm);
        if (pw == nullptr || pb == nullptr || pnG == nullptr || pnB == nullptr) {
            return false;
        }
        linearBatchFast(sc.candEmb.data(), dm, tok.data(), td, kKindCount, static_cast<size_t>(td),
                        *pw, pb->data(), 0, dm);
        for (int k = 0; k < kKindCount; k++) {
            layerNormBuf(sc.candEmb.data() + static_cast<size_t>(k) * dm, dm, pnG->data(),
                         pnB->data());
        }
    }
    float *hTile = sc.candEmb.data();          // [34][dm]（借这一块，随后单独拷出 pool）
    if (sc.pool.size() < static_cast<size_t>(dm)) {
        sc.pool.assign(static_cast<size_t>(dm), 0.f);
    } else {
        std::fill(sc.pool.begin(), sc.pool.end(), 0.f);
    }
    for (int k = 0; k < kKindCount; k++) {
        for (int j = 0; j < dm; j++) {
            sc.pool[static_cast<size_t>(j)] += hTile[static_cast<size_t>(k) * dm + j]
                    / static_cast<float>(kKindCount);
        }
    }

    // ---- ctx / cand 编码器（A−：首层 LN + **死维掩码** + GELU，**末尾无激活**）
    const auto amLp = [&](const float *x, int xStride, int B, int cin, const char *prefix,
                          const int *dead, int ndead, float *out) -> bool {
        const std::vector<float> *nG = vecOf((std::string(prefix) + ".norm.weight").c_str(), cin);
        const std::vector<float> *nB = vecOf((std::string(prefix) + ".norm.bias").c_str(), cin);
        const Mat *f1 = matOf((std::string(prefix) + ".fc1.weight").c_str(), dm, cin);
        const std::vector<float> *f1b = vecOf((std::string(prefix) + ".fc1.bias").c_str(), dm);
        const Mat *f2 = matOf((std::string(prefix) + ".fc2.weight").c_str(), dm, dm);
        const std::vector<float> *f2b = vecOf((std::string(prefix) + ".fc2.bias").c_str(), dm);
        if (nG == nullptr || nB == nullptr || f1 == nullptr || f1b == nullptr || f2 == nullptr
                || f2b == nullptr) {
            return false;
        }
        std::vector<float> row(static_cast<size_t>(cin), 0.f);
        std::vector<float> hid(static_cast<size_t>(std::max(1, B)) * static_cast<size_t>(dm), 0.f);
        for (int b = 0; b < B; b++) {
            for (int c = 0; c < cin; c++) {
                row[static_cast<size_t>(c)] = x[static_cast<size_t>(b) * xStride + c];
            }
            for (int i = 0; i < ndead; i++) {
                row[static_cast<size_t>(dead[i])] = 0.f;      // LN **前**掩一次
            }
            layerNormBuf(row.data(), cin, nG->data(), nB->data());
            for (int i = 0; i < ndead; i++) {
                row[static_cast<size_t>(dead[i])] = 0.f;      // LN **后**再掩（否则 LN 的 bias 会漏进去）
            }
            linearFast(hid.data() + static_cast<size_t>(b) * dm, row.data(),
                       static_cast<size_t>(cin), *f1, f1b->data(), 0, dm);
        }
        geluBuf(hid.data(), static_cast<size_t>(B) * static_cast<size_t>(dm));
        linearBatchFast(out, dm, hid.data(), dm, B, static_cast<size_t>(dm), *f2, f2b->data(), 0,
                        dm);
        return true;
    };
    if (sc.ctxEmb.size() < static_cast<size_t>(dm)) {
        sc.ctxEmb.assign(static_cast<size_t>(dm), 0.f);
    }
    if (!amLp(t.ctx.data(), kCCtx, 1, kCCtx, "ctx", kCtxDeadDims,
              static_cast<int>(sizeof(kCtxDeadDims) / sizeof(kCtxDeadDims[0])),
              sc.ctxEmb.data())) {
        return false;
    }
    if (sc.uTile.size() < static_cast<size_t>(maxCand) * static_cast<size_t>(dm)) {
        sc.uTile.resize(static_cast<size_t>(maxCand) * static_cast<size_t>(dm));
        sc.uEvt.resize(static_cast<size_t>(maxCand) * static_cast<size_t>(dm));
        sc.u.resize(static_cast<size_t>(maxCand) * static_cast<size_t>(dm));
    }
    // cand 的编码器输出写进 sc.uEvt（用完即可覆盖），cross 的 q 直接用它做 0 参数 LN
    int candDead[32];
    int nCandDead = 0;
    for (int c = kCandDeadDimsFirst; c < kCCand; c++) {
        candDead[nCandDead++] = c;
    }
    if (n > 0) {
        if (!amLp(t.cand[0].data(), kCCand, n, kCCand, "cand", candDead, nCandDead,
                  sc.uEvt.data())) {
            return false;
        }
        // `q = layer_norm(cand)`（**无仿射**，eps=1e-5）—— pre-LN 但不占参数账
        for (int i = 0; i < n; i++) {
            float *row = sc.uEvt.data() + static_cast<size_t>(i) * dm;
            double mean = 0.0;
            for (int j = 0; j < dm; j++) {
                mean += static_cast<double>(row[j]);
            }
            mean /= static_cast<double>(dm);
            double var = 0.0;
            for (int j = 0; j < dm; j++) {
                const double dd = static_cast<double>(row[j]) - mean;
                var += dd * dd;
            }
            var /= static_cast<double>(dm);
            const double inv = 1.0 / std::sqrt(var + static_cast<double>(kLnEps));
            for (int j = 0; j < dm; j++) {
                row[j] = static_cast<float>((static_cast<double>(row[j]) - mean) * inv);
            }
        }
    }

    // ---- 融合：95 token（34 tile + 60 evt + h_evt 第 95 个）+ 段嵌入 + evt 的 sin/cos
    const int nTok = kJointTokens;
    const size_t tokBytes = static_cast<size_t>(nTok) * static_cast<size_t>(dm);
    if (sc.yv.size() < tokBytes) {
        sc.yv.resize(tokBytes);
        sc.att.resize(tokBytes);
        sc.xn.resize(tokBytes);
    }
    float *tokens = sc.yv.data();
    {
        const Mat *seg = matOf("fusion.seg", kNSeqA, dm);
        if (seg == nullptr) {
            return false;
        }
        for (int k = 0; k < kKindCount; k++) {
            for (int j = 0; j < dm; j++) {
                tokens[static_cast<size_t>(k) * dm + j]
                        = hTile[static_cast<size_t>(k) * dm + j]
                        + seg->data[static_cast<size_t>(kSegTile) * dm + j];
            }
        }
        for (int row = 0; row < kKEvt; row++) {
            const size_t off = static_cast<size_t>(kKindCount + row) * dm;
            for (int j = 0; j < dm; j++) {
                tokens[off + j] = eTokens[static_cast<size_t>(row) * dm + j]
                        + seg->data[static_cast<size_t>(kSegEvt) * dm + j]
                        + peEvt[static_cast<size_t>(row) * dm + j];
            }
        }
        for (int j = 0; j < dm; j++) {
            tokens[static_cast<size_t>(kJointTokens - 1) * dm + j]
                    = hEvt[static_cast<size_t>(j)]
                    + seg->data[static_cast<size_t>(kSegMem) * dm + j];
        }
    }
    // joint self-attn（pre-LN + 残差）
    {
        const std::vector<float> *jnG = vecOf("fusion.joint_norm.weight", dm);
        const std::vector<float> *jnB = vecOf("fusion.joint_norm.bias", dm);
        if (jnG == nullptr || jnB == nullptr) {
            return false;
        }
        for (int row = 0; row < nTok; row++) {
            std::memcpy(sc.xn.data() + static_cast<size_t>(row) * dm,
                        tokens + static_cast<size_t>(row) * dm, sizeof(float) * static_cast<size_t>(dm));
            layerNormBuf(sc.xn.data() + static_cast<size_t>(row) * dm, dm, jnG->data(), jnB->data());
        }
        if (!mha(*this, sc.xn.data(), nTok, sc.xn.data(), nTok, dm, "fusion.joint", sc.att.data(),
                 sc, err)) {
            return false;
        }
        for (size_t i = 0; i < tokBytes; i++) {
            tokens[i] += sc.att[i];
        }
    }
    // FFN(128→128→128)（pre-LN + 残差）
    {
        const std::vector<float> *fnG = vecOf("fusion.ffn_norm.weight", dm);
        const std::vector<float> *fnB = vecOf("fusion.ffn_norm.bias", dm);
        const Mat *w1 = matOf("fusion.ffn1.weight", dm, dm);
        const std::vector<float> *b1 = vecOf("fusion.ffn1.bias", dm);
        const Mat *w2 = matOf("fusion.ffn2.weight", dm, dm);
        const std::vector<float> *b2 = vecOf("fusion.ffn2.bias", dm);
        if (fnG == nullptr || fnB == nullptr || w1 == nullptr || b1 == nullptr || w2 == nullptr
                || b2 == nullptr) {
            return false;
        }
        if (sc.gu.size() < static_cast<size_t>(dm)) {
            sc.gu.assign(static_cast<size_t>(dm), 0.f);
        }
        if (sc.memG.size() < static_cast<size_t>(dm)) {
            sc.memG.assign(static_cast<size_t>(dm), 0.f);
        }
        for (int row = 0; row < nTok; row++) {
            const size_t off = static_cast<size_t>(row) * dm;
            std::memcpy(sc.rowTmp.data(), tokens + off, sizeof(float) * static_cast<size_t>(dm));
            layerNormBuf(sc.rowTmp.data(), dm, fnG->data(), fnB->data());
            linearFast(sc.gu.data(), sc.rowTmp.data(), static_cast<size_t>(dm), *w1, b1->data(), 0,
                       dm);
            geluBuf(sc.gu.data(), static_cast<size_t>(dm));
            linearFast(sc.memG.data(), sc.gu.data(), static_cast<size_t>(dm), *w2, b2->data(), 0,
                       dm);
            for (int j = 0; j < dm; j++) {
                tokens[off + static_cast<size_t>(j)] += sc.memG[static_cast<size_t>(j)];
            }
        }
    }
    // cross：候选（0 参数 LN 之后）对 95 个 token 取一次
    if (!mha(*this, sc.uEvt.data(), n, tokens, nTok, dm, "fusion.cross", sc.uTile.data(), sc,
             err)) {
        return false;
    }
    // out：`LN(384) → Linear(384→128)`，再 B3（`u + tanh(u_res)·LN(u)`）
    {
        const std::vector<float> *onG = vecOf("fusion.out_norm.weight", 3 * dm);
        const std::vector<float> *onB = vecOf("fusion.out_norm.bias", 3 * dm);
        const Mat *ow = matOf("fusion.out.weight", dm, 3 * dm);
        const std::vector<float> *ob = vecOf("fusion.out.bias", dm);
        const std::vector<float> *unG = vecOf("fusion.u_norm.weight", dm);
        const std::vector<float> *unB = vecOf("fusion.u_norm.bias", dm);
        const std::vector<float> *ures = vecOf("fusion.u_res", dm);
        if (onG == nullptr || onB == nullptr || ow == nullptr || ob == nullptr || unG == nullptr
                || unB == nullptr || ures == nullptr) {
            return false;
        }
        if (sc.cat3.size() < static_cast<size_t>(3 * dm)) {
            sc.cat3.assign(static_cast<size_t>(3 * dm), 0.f);
        }
        if (sc.gu.size() < static_cast<size_t>(dm)) {
            sc.gu.assign(static_cast<size_t>(dm), 0.f);
        }
        std::vector<float> uResT(static_cast<size_t>(dm), 0.f);
        for (int j = 0; j < dm; j++) {
            uResT[static_cast<size_t>(j)] = static_cast<float>(
                std::tanh(static_cast<double>((*ures)[static_cast<size_t>(j)])));
        }
        for (int i = 0; i < n; i++) {
            const float *cross = sc.uTile.data() + static_cast<size_t>(i) * dm;
            for (int j = 0; j < dm; j++) {
                sc.cat3[static_cast<size_t>(j)] = cross[static_cast<size_t>(j)];
                sc.cat3[static_cast<size_t>(dm + j)] = sc.pool[static_cast<size_t>(j)];
                sc.cat3[static_cast<size_t>(2 * dm + j)] = sc.ctxEmb[static_cast<size_t>(j)];
            }
            layerNormBuf(sc.cat3.data(), 3 * dm, onG->data(), onB->data());
            float *ui = sc.u.data() + static_cast<size_t>(i) * dm;
            linearFast(ui, sc.cat3.data(), static_cast<size_t>(3 * dm), *ow, ob->data(), 0, dm);
            // B3：`u ← u + tanh(u_res) ⊙ LN(u)`（逐维、逐候选）
            std::memcpy(sc.gu.data(), ui, sizeof(float) * static_cast<size_t>(dm));
            layerNormBuf(sc.gu.data(), dm, unG->data(), unB->data());
            for (int j = 0; j < dm; j++) {
                ui[j] += uResT[static_cast<size_t>(j)] * sc.gu[static_cast<size_t>(j)];
            }
        }
    }

    // ---- 头（A−：`danger` 经 0 参数固定通道进 policy；`effect` 只给 caller）
    const Mat *polW = matOf("heads.policy.weight", 1, dm);
    const std::vector<float> *polB = vecOf("heads.policy.bias", 1);
    const Mat *pgW = matOf("heads.policy_gate.weight", dm, 3);
    const std::vector<float> *pgB = vecOf("heads.policy_gate.bias", dm);
    const Mat *danW = matOf("heads.danger.weight", 4, dm);
    const std::vector<float> *danB = vecOf("heads.danger.bias", 4);
    const Mat *effW = matOf("heads.effect.weight", 3, dm);
    const std::vector<float> *effB = vecOf("heads.effect.bias", 3);
    if (polW == nullptr || polB == nullptr || pgW == nullptr || pgB == nullptr || danW == nullptr
            || danB == nullptr || effW == nullptr || effB == nullptr) {
        return false;
    }
    if (sc.meanU.size() < static_cast<size_t>(dm)) {
        sc.meanU.assign(static_cast<size_t>(dm), 0.f);
    } else {
        std::fill(sc.meanU.begin(), sc.meanU.end(), 0.f);
    }
    if (sc.danger.size() < static_cast<size_t>(maxCand) * 4) {
        sc.danger.resize(static_cast<size_t>(maxCand) * 4);
        sc.effect.resize(static_cast<size_t>(maxCand) * 3);
    }
    float *danger = sc.danger.data();
    float *effect = sc.effect.data();
    if (n > 0) {
        linearBatchFast(danger, 4, sc.u.data(), dm, n, static_cast<size_t>(dm), *danW, danB->data(),
                        0, 4);
        linearBatchFast(effect, 3, sc.u.data(), dm, n, static_cast<size_t>(dm), *effW, effB->data(),
                        0, 3);
    }
    const float denom = static_cast<float>(std::max(1, n));
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < dm; j++) {
            sc.meanU[static_cast<size_t>(j)] +=
                    sc.u[static_cast<size_t>(i) * dm + j] / denom;
        }
    }
    // 门控：`1 + tanh(W_g·sigmoid(bt) + b_g)`（A− 里 `policy_gate` **必有**）
    if (sc.btProb.size() < 3) {
        sc.btProb.assign(3, 0.f);
    }
    {
        std::vector<float> btLogits;
        if (!head(*this, sc.meanU, "belief_tenpai", 3, btLogits, err)) {
            return false;
        }
        for (int j = 0; j < 3; j++) {
            sc.btProb[static_cast<size_t>(j)]
                    = 1.f / (1.f + std::exp(-btLogits[static_cast<size_t>(j)]));
        }
    }
    if (sc.film.size() < static_cast<size_t>(dm)) {
        sc.film.assign(static_cast<size_t>(dm), 0.f);
    }
    float *pgate = sc.film.data();
    for (int j = 0; j < dm; j++) {
        float acc2 = (*pgB)[static_cast<size_t>(j)];
        for (int k = 0; k < 3; k++) {
            acc2 += pgW->data[static_cast<size_t>(j) * 3 + static_cast<size_t>(k)]
                    * sc.btProb[static_cast<size_t>(k)];
        }
        pgate[j] = 1.f + std::tanh(acc2);
    }
    if (sc.gu.size() < static_cast<size_t>(dm)) {
        sc.gu.assign(static_cast<size_t>(dm), 0.f);
    }
    if (sc.policy.size() < static_cast<size_t>(maxCand)) {
        sc.policy.resize(static_cast<size_t>(maxCand));
    }
    float *policy = sc.policy.data();
    for (int i = 0; i < n; i++) {
        const float *ui = sc.u.data() + static_cast<size_t>(i) * dm;
        for (int j = 0; j < dm; j++) {
            sc.gu[static_cast<size_t>(j)] = ui[j] * pgate[j];
        }
        float logit = (*polB)[0] + dotN(polW->data.data(), sc.gu.data(), dm);
        // ★ 固定通道（0 参数）：`logit += 1.0 · log(mean_k σ(danger_k) + 1e-6)`（全程 float32）
        float sig = 0.f;
        for (int k = 0; k < 4; k++) {
            const float z = danger[static_cast<size_t>(i) * 4 + k];
            sig += 1.f / (1.f + std::exp(-z));
        }
        sig /= 4.f;
        logit += kDangerCoef * std::log(sig + kDangerEps);
        policy[i] = logit;
    }

    outAll.clear();
    outAll["policy"].assign(policy, policy + n);
    std::vector<float> value;
    if (!head(*this, sc.meanU, "value", valueBins, value, err)) {
        return false;
    }
    outAll["value"] = std::move(value);
    std::vector<float> placement;
    if (!head(*this, sc.meanU, "placement", 4, placement, err)) {
        return false;
    }
    outAll["placement"] = std::move(placement);
    std::vector<float> beliefHand;
    if (!head(*this, sc.meanU, "belief_hand", 3 * kKindCount, beliefHand, err)) {
        return false;
    }
    outAll["belief_hand"] = std::move(beliefHand);
    std::vector<float> beliefTenpai;
    if (!head(*this, sc.meanU, "belief_tenpai", 3, beliefTenpai, err)) {
        return false;
    }
    outAll["belief_tenpai"] = std::move(beliefTenpai);
    outAll["danger"].assign(danger, danger + static_cast<size_t>(n) * 4);
    outAll["effect"].assign(effect, effect + static_cast<size_t>(n) * 3);
    outAll["h_evt"].assign(hEvt, hEvt + dm);
    return true;
}

/**
 * 前向入口：**按 `arch` 分派**到 `forwardV4` / `forwardA`（段 3b）。
 *
 * <p>为什么不在一个函数里 if：两套图连"token 是什么"都不同（v4 的融合是候选当 query 对两条
 * 记忆做注意力；A− 是 95 个 token 的联合自注意力 + 一次 cross）。合在一个函数里只会写出
 * "看起来共用、实际每行都要分支"的代码 —— 而且 A− 的 `u` 尺度/头口径（danger 进 policy）
 * 与 v4 不同，混着写必然把某一条约束漏到另一条上。
 */
bool V4Policy::forwardAll(const JVal &obs, std::map<std::string, std::vector<float>> &outAll,
                          std::string &err, const std::vector<float> *h0, V4Scratch *scr) const {
    if (arch == kTopoA) {
        return forwardA(obs, outAll, err, h0, scr);
    }
    if (arch == kTopoV4) {
        return forwardV4(obs, outAll, err, h0, scr);
    }
    err = "内部错误：前向时 arch 未设置（加载器必须先定 topoId）";
    return false;
}

bool V4Policy::forwardV4(const JVal &obs, std::map<std::string, std::vector<float>> &outAll,
                         std::string &err, const std::vector<float> *h0, V4Scratch *scr) const {
    err.clear();
    V4Tensors t;
    if (!v4Assemble(obs, missingBlocks, t, err)) {
        return false;
    }
    V4Scratch localScr;
    V4Scratch &sc = scr != nullptr ? *scr : localScr;
    const int dm = dModel;
    const int n = static_cast<int>(t.cand.size());
    const int maxCand = std::max(1, n);
    // ---- 事件塔：**先**定下 carry 与窗口表示（W1 起两者都进融合）
    //   h0 == nullptr ⇒ 生产路径的**整手 carry**（重放 events[0, n-K) 再喂窗口里的真实行）；
    //   给了 h0 ⇒ golden 夹具那条（只从 h0 起喂窗口里的真实行）。
    //   ⛔ 别把这两条合成"窗口冷启动"：那与整手 carry 是两个量，融合消费它之后就会分叉。
    if (sc.carry.size() < static_cast<size_t>(dm)) {
        sc.carry.assign(static_cast<size_t>(dm), 0.f);
        sc.tok.assign(static_cast<size_t>(kCEvt), 0.f);
        sc.rowTmp.assign(static_cast<size_t>(dm), 0.f);
    }
    if (h0 != nullptr) {
        if (static_cast<int>(h0->size()) != dm) {
            err = "h0 长度 " + std::to_string(h0->size()) + " != d_model " + std::to_string(dm)
                  + "（既不截断也不补零：静默凑合会让'同 h ⇒ 同输出'变成空转）";
            return false;
        }
        std::copy(h0->begin(), h0->end(), sc.carry.begin());
        if (!eventTowerWindow(*this, t, sc, err)) {
            return false;
        }
    } else if (!eventTowerProduction(*this, obs, t, sc, err)) {
        return false;
    }
    const float *eTokens = sc.eTokens.data();
    const float *hEvt = sc.carry.data();

    // ---- 牌种塔
    if (sc.tileH.size() < static_cast<size_t>(kKindCount) * static_cast<size_t>(tileD)) {
        sc.tileH.resize(static_cast<size_t>(kKindCount) * static_cast<size_t>(tileD));
        sc.tileAt.resize(static_cast<size_t>(kKindCount) * static_cast<size_t>(tileD));
        sc.aliasTmp.resize(static_cast<size_t>(kKindCount) * static_cast<size_t>(tileD));
    }
    const Mat *w1 = mat("tile.enc.0.weight", tileD, kCTile, err);
    const std::vector<float> *b1 = vec("tile.enc.0.bias", tileD, err);
    const Mat *w2 = mat("tile.enc.2.weight", tileD, tileD, err);
    const std::vector<float> *b2 = vec("tile.enc.2.bias", tileD, err);
    if (!err.empty()) {
        return false;
    }
    float *ht = sc.tileH.data();
    // ★ #0（第一百零六轮）：`tile.tile_id` **加在输入侧**（enc 之前）。旧包没有它 ⇒ `tile+0`。
    const Mat *tileId = mats.count("tile.tile_id") ? &mats.at("tile.tile_id") : nullptr;
    const float *tileIn = t.tile[0].data();
    if (tileId != nullptr) {
        if (sc.tileIn.size() < static_cast<size_t>(kKindCount) * static_cast<size_t>(kCTile)) {
            sc.tileIn.assign(static_cast<size_t>(kKindCount) * static_cast<size_t>(kCTile), 0.f);
        }
        for (int k = 0; k < kKindCount; k++) {
            for (int c = 0; c < kCTile; c++) {
                sc.tileIn[static_cast<size_t>(k) * kCTile + c]
                        = t.tile[static_cast<size_t>(k)][static_cast<size_t>(c)]
                        + tileId->data[static_cast<size_t>(k) * kCTile + c];
            }
        }
        tileIn = sc.tileIn.data();
    }
    linearBatchFast(ht, tileD, tileIn, kCTile, kKindCount, static_cast<size_t>(kCTile), *w1,
                    b1->data(), 0, tileD);
    reluBuf(ht, static_cast<size_t>(kKindCount) * static_cast<size_t>(tileD));
    // 第二个线性层与输入别名：先整块转一手（= Java `linear` 的别名保护）
    std::memcpy(sc.aliasTmp.data(), ht,
                static_cast<size_t>(kKindCount) * static_cast<size_t>(tileD) * sizeof(float));
    linearBatchFast(ht, tileD, sc.aliasTmp.data(), tileD, kKindCount, static_cast<size_t>(tileD), *w2,
                    b2->data(), 0, tileD);
    reluBuf(ht, static_cast<size_t>(kKindCount) * static_cast<size_t>(tileD));
    if (!mha(*this, ht, kKindCount, ht, kKindCount, tileD, "tile.attn", sc.tileAt.data(), sc, err)) {
        return false;
    }
    const std::vector<float> *normG = vec("tile.norm.weight", tileD, err);
    const std::vector<float> *normB = vec("tile.norm.bias", tileD, err);
    if (!err.empty()) {
        return false;
    }
    float *tileAt = sc.tileAt.data();
    if (sc.pool.size() < static_cast<size_t>(tileD)) {
        sc.pool.assign(static_cast<size_t>(tileD), 0.f);
    } else {
        std::fill(sc.pool.begin(), sc.pool.end(), 0.f);
    }
    float *pool = sc.pool.data();
    for (int k = 0; k < kKindCount; k++) {
        float *row = ht + static_cast<size_t>(k) * tileD;
        const float *arow = tileAt + static_cast<size_t>(k) * tileD;
        for (int i = 0; i < tileD; i++) {
            row[i] += arow[i];
        }
        layerNormBuf(row, tileD, normG->data(), normB->data());
        for (int i = 0; i < tileD; i++) {
            pool[i] += row[i] / static_cast<float>(kKindCount);      // mean over tokens
        }
    }
    if (sc.hTilePool.size() < static_cast<size_t>(dm)) {
        sc.hTilePool.assign(static_cast<size_t>(dm), 0.f);
    }
    {
        const Mat *tpW = mat("tile.proj.weight", dm, tileD, err);
        const std::vector<float> *tpB = vec("tile.proj.bias", dm, err);
        if (!err.empty()) {
            return false;
        }
        linearFast(sc.hTilePool.data(), pool, static_cast<size_t>(tileD), *tpW, tpB->data(), 0, dm);
    }

    // ---- ctx / cand 编码器
    if (sc.ctxEmb.size() < static_cast<size_t>(dm)) {
        sc.ctxEmb.assign(static_cast<size_t>(dm), 0.f);
    }
    if (!mlp(*this, t.ctx.data(), kCCtx, 1, static_cast<size_t>(kCCtx), "ctx.net", kCCtx, dm,
             sc.ctxEmb.data(), sc, err)) {
        return false;
    }
    if (sc.candEmb.size() < static_cast<size_t>(maxCand) * static_cast<size_t>(dm)) {
        sc.candEmb.resize(static_cast<size_t>(maxCand) * static_cast<size_t>(dm));
    }
    if (n > 0) {
        if (!mlp(*this, t.cand[0].data(), kCCand, n, static_cast<size_t>(kCCand), "cand.net", kCCand,
                 dm, sc.candEmb.data(), sc, err)) {
            return false;
        }
    }

    // ---- 融合
    if (sc.kvTile.size() < static_cast<size_t>(kKindCount) * static_cast<size_t>(dm)) {
        sc.kvTile.resize(static_cast<size_t>(kKindCount) * static_cast<size_t>(dm));
    }
    {
        const Mat *tpW = mat("fusion.tile_proj.weight", dm, tileD, err);
        const std::vector<float> *tpB = vec("fusion.tile_proj.bias", dm, err);
        if (!err.empty()) {
            return false;
        }
        linearBatchFast(sc.kvTile.data(), dm, ht, tileD, kKindCount, static_cast<size_t>(tileD),
                        *tpW, tpB->data(), 0, dm);
    }
    if (sc.uTile.size() < static_cast<size_t>(maxCand) * static_cast<size_t>(dm)) {
        sc.uTile.resize(static_cast<size_t>(maxCand) * static_cast<size_t>(dm));
        sc.uEvt.resize(static_cast<size_t>(maxCand) * static_cast<size_t>(dm));
        sc.u.resize(static_cast<size_t>(maxCand) * static_cast<size_t>(dm));
    }
    if (!mha(*this, sc.candEmb.data(), n, sc.kvTile.data(), kKindCount, dm, "fusion.q_tile",
             sc.uTile.data(), sc, err)) {
        return false;
    }
    if (!mha(*this, sc.candEmb.data(), n, eTokens, kKEvt, dm, "fusion.q_evt", sc.uEvt.data(), sc,
             err)) {
        return false;
    }
    const Mat *gateW = mat("fusion.gate.weight", dm, 2 * dm, err);
    const std::vector<float> *gateB = vec("fusion.gate.bias", dm, err);
    const Mat *writeW = mat("fusion.write.weight", dm, dm, err);
    const std::vector<float> *writeB = vec("fusion.write.bias", dm, err);
    const Mat *filmW = mat("fusion.film.weight", dm, dm, err);
    const std::vector<float> *filmB = vec("fusion.film.bias", dm, err);
    if (!err.empty()) {
        return false;
    }
    if (sc.cat.size() < static_cast<size_t>(2 * dm)) {
        sc.cat.assign(static_cast<size_t>(2 * dm), 0.f);
    }
    if (sc.cat3.size() < static_cast<size_t>(3 * dm)) {
        sc.cat3.assign(static_cast<size_t>(3 * dm), 0.f);
    }
    if (sc.state.size() < static_cast<size_t>(dm)) {
        sc.state.assign(static_cast<size_t>(dm), 0.f);
    }
    // ⚠ `gate` / `write` / `film` **必须在进候选循环之前**就备好：n 可能为 0（legal 为空），
    // 那时循环体一次都不执行，而后面"逐候选门控"直接用 `film` 当 `pgate` —— 空缓冲 + `fill`
    // 就是堆损坏。缓冲区大小与"循环跑没跑"不能耦合。
    if (sc.gate.size() < static_cast<size_t>(dm)) {
        sc.gate.assign(static_cast<size_t>(dm), 0.f);
    }
    if (sc.write.size() < static_cast<size_t>(dm)) {
        sc.write.assign(static_cast<size_t>(dm), 0.f);
    }
    if (sc.film.size() < static_cast<size_t>(dm)) {
        sc.film.assign(static_cast<size_t>(dm), 0.f);
    }
    float *cat = sc.cat.data();
    float *cat3 = sc.cat3.data();
    float *state = sc.state.data();
    float *uTile = sc.uTile.data();
    float *uEvt = sc.uEvt.data();
    float *u = sc.u.data();
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < dm; j++) {
            cat[j] = uEvt[static_cast<size_t>(i) * dm + j];
            cat[dm + j] = uTile[static_cast<size_t>(i) * dm + j];
        }
        float *g = sc.gate.data();
        linearFast(g, cat, static_cast<size_t>(2 * dm), *gateW, gateB->data(), 0, dm);
        sigmoidBuf(g, static_cast<size_t>(dm));
        float *wv = sc.write.data();
        linearFast(wv, uEvt + static_cast<size_t>(i) * dm, static_cast<size_t>(dm), *writeW,
                   writeB->data(), 0, dm);
        for (int j = 0; j < dm; j++) {
            state[j] = sc.hTilePool[static_cast<size_t>(j)] + g[j] * wv[j];
        }
        float *f = sc.film.data();
        linearFast(f, state, static_cast<size_t>(dm), *filmW, filmB->data(), 0, dm);
        tanhBuf(f, static_cast<size_t>(dm));
        for (int j = 0; j < dm; j++) {
            f[j] = uEvt[static_cast<size_t>(i) * dm + j] * (1.f + f[j]);
        }
        for (int j = 0; j < dm; j++) {
            cat3[j] = uTile[static_cast<size_t>(i) * dm + j];
            cat3[dm + j] = f[j];
            cat3[2 * dm + j] = sc.ctxEmb[static_cast<size_t>(j)];
        }
        if (!mlp(*this, cat3, 3 * dm, 1, static_cast<size_t>(3 * dm), "fusion.out.net", 3 * dm, dm,
                 u + static_cast<size_t>(i) * dm, sc, err, /*finalRelu=*/false)) {
            return false;
        }
    }

    // 长程记忆门控（W1）：`g_mem[j] = 1 + tanh(dot(memW[j], hEvt) + memB[j])`，`u_i ⊙ g_mem`（**逐候选**）。
    //   ⛔ 为什么不能把 `hEvt` 拼进候选 / 当逐行常数加进去：那给同一行所有候选加的是**同一个量**
    //     ⇒ softmax/argmax 数学上不变、那几列梯度恒为 0（第五十轮就是这么栽的）。门控乘在
    //     `u_i` 上而 `u_i` 各不相同 ⇒ 真能改判。
    //   ⚠ 门控在**所有头之前**（danger/effect/meanU 一起看到长程记忆），不只是 policy。
    //   ⚠ 缺张量 ⇒ `g_mem ≡ 1` ⇒ 与"没有门控"逐位相同（旧网兼容）；累加器用 `double`（= Java 那份）。
    //   ⚠ 查表而不是用 `mat()` 探测：探测会污染 `err`，而且**维度不符**必须炸掉、不能当成"没有"。
    {
        const Mat *memW = mats.count("fusion.mem.weight") ? &mats.at("fusion.mem.weight") : nullptr;
        const std::vector<float> *memB = vecs.count("fusion.mem.bias")
                                                 ? &vecs.at("fusion.mem.bias") : nullptr;
        if (memW != nullptr) {
            if (sc.memG.size() < static_cast<size_t>(dm)) {
                sc.memG.assign(static_cast<size_t>(dm), 1.f);
            }
            float *memG = sc.memG.data();
            for (int j = 0; j < dm; j++) {
                double acc = 0.0;
                const float *row = memW->data.data() + static_cast<size_t>(j) * static_cast<size_t>(dm);
                for (int q = 0; q < dm; q++) {
                    acc += static_cast<double>(row[q]) * static_cast<double>(hEvt[static_cast<size_t>(q)]);
                }
                const double b = memB == nullptr ? 0.0 : static_cast<double>((*memB)[static_cast<size_t>(j)]);
                memG[static_cast<size_t>(j)] = static_cast<float>(1.0 + std::tanh(acc + b));
            }
            for (int i = 0; i < n; i++) {
                for (int j = 0; j < dm; j++) {
                    u[static_cast<size_t>(i) * dm + j] *= memG[static_cast<size_t>(j)];
                }
            }
        }
    }

    // ---- ★ B3（第一百零四轮）：`u_i ← u_i + tanh(u_res) ⊙ LN(u_i)`（逐候选、逐维）
    //   位置与 Java 一致：mem 门**之后**、所有头**之前**。LN 把尺度从
    //   `fusion.out → 三处 tanh 门 → 线性 policy` 这条链里解耦；残差门保留原 `u` ⇒ 只做逐维校正。
    //   ⚠ 缺 `fusion.u_res` ⇒ `tanh(0)=0` ⇒ 跳过整段 ⇒ 旧网逐位不变。
    {
        const auto urw = vecs.find("fusion.u_res");
        if (urw != vecs.end()) {
            const auto un = vecs.find("fusion.u_norm.weight");
            const auto ub = vecs.find("fusion.u_norm.bias");
            std::vector<float> urT(static_cast<size_t>(dm));
            for (int j = 0; j < dm; j++) {
                urT[static_cast<size_t>(j)] = static_cast<float>(
                    std::tanh(static_cast<double>((urw->second)[static_cast<size_t>(j)])));
            }
            std::vector<float> r(static_cast<size_t>(dm));
            for (int i = 0; i < n; i++) {
                float *ui = u + static_cast<size_t>(i) * dm;
                double mean = 0.0;
                for (int j = 0; j < dm; j++) {
                    mean += static_cast<double>(ui[j]);
                }
                mean /= static_cast<double>(dm);
                double var = 0.0;
                for (int j = 0; j < dm; j++) {
                    const double d = static_cast<double>(ui[j]) - mean;
                    var += d * d;
                }
                var /= static_cast<double>(dm);            // **有偏**方差（与 torch LN 一致）
                const double inv = 1.0 / std::sqrt(var + 1e-5);
                for (int j = 0; j < dm; j++) {
                    const double g = un == vecs.end() ? 1.0
                                                      : static_cast<double>((un->second)[static_cast<size_t>(j)]);
                    const double b = ub == vecs.end() ? 0.0
                                                      : static_cast<double>((ub->second)[static_cast<size_t>(j)]);
                    r[static_cast<size_t>(j)] = static_cast<float>(
                        (static_cast<double>(ui[j]) - mean) * inv * g + b);
                }
                for (int j = 0; j < dm; j++) {
                    ui[j] += urT[static_cast<size_t>(j)] * r[static_cast<size_t>(j)];
                }
            }
        }
    }

    // ---- 头
    if (sc.danger.size() < static_cast<size_t>(maxCand) * 4) {
        sc.danger.resize(static_cast<size_t>(maxCand) * 4);
        sc.effect.resize(static_cast<size_t>(maxCand) * 3);
    }
    // ⚠ **belief 用"逐候选门控"接进 policy**（第五十五轮）：`g = 1 + tanh(W_g·sigmoid(bt) + b_g)`，
    //   `policy_i = W·(u_i ⊙ g) + b`。⛔ 第五十轮那版"把 sigmoid(bt) 拼在每个候选后面"是**空操作**
    //   （逐行常数 ⇒ argmax/softmax 不变、梯度恒 0，红证见 NOTES §6.5）；门控逐候选 ⇒ 真能改判。
    const Mat *polW = mat("heads.policy.weight", 1, dm, err);
    const std::vector<float> *polB = vec("heads.policy.bias", 1, err);
    const Mat *danW = mat("heads.danger.weight", 4, dm, err);
    const std::vector<float> *danB = vec("heads.danger.bias", 4, err);
    const Mat *effW = mat("heads.effect.weight", 3, dm, err);
    const std::vector<float> *effB = vec("heads.effect.bias", 3, err);
    if (!err.empty()) {
        return false;
    }
    // 门控张量**可缺**（旧网没有）：缺 ⇒ 权重当全 0、偏置当全 0 ⇒ `g ≡ 1`（恒等）。
    // ⚠ 这里用 `err` 探测会污染它，所以查表而不是查 `err`。
    const Mat *pgW = mats.count("heads.policy_gate.weight") ? &mats.at("heads.policy_gate.weight")
                                                            : nullptr;
    const std::vector<float> *pgB = vecs.count("heads.policy_gate.bias")
                                            ? &vecs.at("heads.policy_gate.bias") : nullptr;
    if (sc.meanU.size() < static_cast<size_t>(dm)) {
        sc.meanU.assign(static_cast<size_t>(dm), 0.f);
    } else {
        // ⚠ 必须**每次清零**：`meanU` 是累加量（原实现每决策新建一个全 0 的向量）。
        // 只 resize 不清零 ⇒ 跨决策累积、logits 全错（这一步改错过一次）。
        std::fill(sc.meanU.begin(), sc.meanU.end(), 0.f);
    }
    float *meanU = sc.meanU.data();
    float *danger = sc.danger.data();
    float *effect = sc.effect.data();
    const float polBias = (*polB)[0];
    if (n > 0) {
        linearBatchFast(danger, 4, u, dm, n, static_cast<size_t>(dm), *danW, danB->data(), 0, 4);
        linearBatchFast(effect, 3, u, dm, n, static_cast<size_t>(dm), *effW, effB->data(), 0, 3);
    }
    const float denom = static_cast<float>(std::max(1, n));
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < dm; j++) {
            meanU[j] += u[static_cast<size_t>(i) * dm + j] / denom;
        }
    }
    // n == 0 时 meanU 保持全 0（Java 显式重赋值一次，效果相同）

    // 先算 `belief_tenpai` 的**概率**（喂门控），再逐候选出 policy。
    // ⚠ 输出的 `belief_tenpai` 仍然是 **logits**（下面 `head(...)` 重新算一遍）—— 别搞混。
    if (sc.btProb.size() < 3) {
        sc.btProb.assign(3, 0.f);
    }
    {
        std::vector<float> btLogits;
        if (!head(*this, sc.meanU, "belief_tenpai", 3, btLogits, err)) {
            return false;
        }
        for (int j = 0; j < 3; j++) {
            sc.btProb[static_cast<size_t>(j)]
                    = 1.f / (1.f + std::exp(-btLogits[static_cast<size_t>(j)]));
        }
    }
    // 逐候选门控：`g[j] = 1 + tanh(Σ_k pgW[j][k]·btProb[k] + pgB[j])`（缺门控 ⇒ g ≡ 1）
    float *pgate = sc.film.data();
    if (pgW != nullptr) {
        for (int j = 0; j < dm; j++) {
            float acc = pgB != nullptr ? (*pgB)[static_cast<size_t>(j)] : 0.f;
            for (int k = 0; k < 3; k++) {
                acc += pgW->data[static_cast<size_t>(j) * 3 + static_cast<size_t>(k)]
                        * sc.btProb[static_cast<size_t>(k)];
            }
            pgate[j] = 1.f + std::tanh(acc);
        }
    } else {
        std::fill(pgate, pgate + dm, 1.f);
    }
    if (sc.gu.size() < static_cast<size_t>(dm)) {
        sc.gu.assign(static_cast<size_t>(dm), 0.f);
    }
    // ⚠ `maxCand` 会随决策变（legal 条数 1..15）⇒ 这里必须**单独**按当前上界涨，
    // 不能跟 `gu` 挤在同一个 if 里（挤在一起就是"第二次 legal 更多时越界写" ⇒ 堆损坏）。
    if (sc.policy.size() < static_cast<size_t>(maxCand)) {
        sc.policy.resize(static_cast<size_t>(maxCand));
    }
    float *gu = sc.gu.data();
    float *policy = sc.policy.data();
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < dm; j++) {
            gu[j] = u[static_cast<size_t>(i) * dm + j] * pgate[j];
        }
        policy[i] = polBias + dotN(polW->data.data(), gu, dm);
    }

    outAll.clear();
    outAll["policy"].assign(policy, policy + n);
    std::vector<float> value;
    if (!head(*this, sc.meanU, "value", valueBins, value, err)) {
        return false;
    }
    outAll["value"] = std::move(value);
    std::vector<float> placement;
    if (!head(*this, sc.meanU, "placement", 4, placement, err)) {
        return false;
    }
    outAll["placement"] = std::move(placement);
    std::vector<float> beliefHand;
    if (!head(*this, sc.meanU, "belief_hand", 3 * kKindCount, beliefHand, err)) {
        return false;
    }
    outAll["belief_hand"] = std::move(beliefHand);
    std::vector<float> beliefTenpai;
    if (!head(*this, sc.meanU, "belief_tenpai", 3, beliefTenpai, err)) {
        return false;
    }
    outAll["belief_tenpai"] = std::move(beliefTenpai);
    outAll["danger"].assign(danger, danger + static_cast<size_t>(n) * 4);
    outAll["effect"].assign(effect, effect + static_cast<size_t>(n) * 3);
    // W1 起把**事件塔的隐状态**也交出来：它现在是融合的输入（`fusion.mem` 门控），于是"三端同值"
    // 可以直接比这个向量（比只看七个头灵敏得多 —— 夹具格式 2 就是为此加了这一列）。
    outAll["h_evt"].assign(hEvt, hEvt + dm);
    return true;
}

bool V4Policy::logits(const JVal &obs, std::vector<float> &out, std::string &err,
                      V4Scratch *scr) const {
    std::map<std::string, std::vector<float>> all;
    if (!forwardAll(obs, all, err, nullptr, scr)) {
        return false;
    }
    const auto it = all.find("policy");
    if (it == all.end()) {
        err = "前向没有产出 policy 头";
        return false;
    }
    out = it->second;
    return true;
}

bool v4Logits(const V4Policy &p, const JVal &obs, std::vector<float> &out, std::string &err,
              V4Scratch *scr) {
    return p.logits(obs, out, err, scr);
}

bool v4ForwardAll(const V4Policy &p, const JVal &obs,
                  std::map<std::string, std::vector<float>> &out, std::string &err) {
    return p.forwardAll(obs, out, err);
}

bool v4ForwardAllH0(const V4Policy &p, const JVal &obs, const std::vector<float> &h0,
                    std::map<std::string, std::vector<float>> &out, std::string &err) {
    // 长度核对在 `forwardAll` 里（那里才知道 dModel）；这里不预先截断/补零 —— 静默凑合会让
    // "同 h0 ⇒ 同输出"这条判据变成空转。
    return p.forwardAll(obs, out, err, &h0);
}

// ---------------------------------------------------------------- CLI：v4net

namespace {

/** 读整个文本文件（失败返回 false）。 */
bool readTextFile(const std::string &path, std::string &out) {
    std::FILE *f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) {
        return false;
    }
    out.clear();
    char buf[65536];
    for (;;) {
        const size_t got = std::fread(buf, 1, sizeof(buf), f);
        if (got > 0) {
            out.append(buf, got);
        }
        if (got < sizeof(buf)) {
            break;
        }
    }
    std::fclose(f);
    return true;
}

/**
 * Java `String.format("%.9g", (float) v)` 的**逐字符**等价物。
 *
 * <p>为什么要自己排：Java 的 dtoa 在十进制**恰好一半**时向绝对值大的方向进位（half-up），
 * 而 C 的 `printf` 是 ties-to-even ⇒ 打印串会在 `759.8515625f` 这类值上差 1 个末位。
 * 2026-09-27 实测：`v4-bc-004` 的 718 行里有 9 格出现这种差（`-759.8515625` → Java
 * `-759.851563`、glibc `-759.851562`），而两边的 **float32 位模式完全相同** ——
 * 也就是说那是纯粹的十进制排版差异，却会让"逐字符对拍"这条判据变红。
 * 非 tie 的值两边本来就一样，所以这里只做"取 51 位精确十进制 → 按 half-up 截到 9 位
 * → 按 `%g` 的规则排版（指数 < -4 或 >= 9 用科学计数法，否则定点并保留尾随零）"。
 * 已对 23 个 Java 参考值（0 / -0 / 半值 tie / 进位翻十年 / NaN / ±Inf / 次正规 / 极值）逐一核过。
 */
std::string javaG9(float v) {
    if (std::isnan(v)) {
        return "NaN";
    }
    if (std::isinf(v)) {
        return v > 0.f ? "Infinity" : "-Infinity";
    }
    const bool neg = std::signbit(v) != 0;
    const double av = std::fabs(static_cast<double>(v));
    char sci[96];
    std::snprintf(sci, sizeof(sci), "%.50e", av);
    const char *ep = std::strchr(sci, 'e');
    const int exp0 = ep == nullptr ? 0 : std::atoi(ep + 1);
    std::string digits;
    for (const char *q = sci; *q != '\0' && *q != 'e'; q++) {
        if (*q != '.') {
            digits.push_back(*q);
        }
    }
    digits.resize(51, '0');
    std::string kept = digits.substr(0, 9);
    int exp = exp0;
    if (digits[9] >= '5') {                       // half-up：>半 进位；==半（tie）也进位（Java 口径）
        int i = 8;
        for (; i >= 0; i--) {
            if (kept[static_cast<size_t>(i)] == '9') {
                kept[static_cast<size_t>(i)] = '0';
            } else {
                kept[static_cast<size_t>(i)] = static_cast<char>(kept[static_cast<size_t>(i)] + 1);
                break;
            }
        }
        if (i < 0) {                              // 999999999 + 1 → 进位翻十年
            kept = "1" + kept.substr(1);
            exp++;
        }
    }
    std::string out;
    if (exp < -4 || exp >= 9) {
        char eb[8];
        std::snprintf(eb, sizeof(eb), "%02d", exp < 0 ? -exp : exp);
        out = kept.substr(0, 1) + "." + kept.substr(1) + "e" + (exp < 0 ? "-" : "+") + eb;
    } else if (exp >= 0) {
        out = (exp + 1 >= 9) ? kept : kept.substr(0, static_cast<size_t>(exp + 1)) + "."
                                       + kept.substr(static_cast<size_t>(exp + 1));
    } else {
        out = "0." + std::string(static_cast<size_t>(-exp - 1), '0') + kept;
    }
    return neg ? "-" + out : out;
}

}  // namespace

int v4NetCli(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr,
                     "用法：trainer v4net <net.bin> <轨迹1.jsonl> [轨迹2.jsonl ...]\n"
                     "  逐条 decision 行打印 v4 策略头 logits（顺序与文件内出现的顺序一致）：\n"
                     "    step=<step> n=<n> argmax=<i> logits=<v0>,<v1>,...\n"
                     "  值用 %%.9g（float32 往返精度足够）；一条决策都没有的文件打一行 `# <文件名> 0 条`。\n"
                     "  本子命令与 tools/V4Probe.java 的输出**逐字符相同**，供对拍闸门比对。\n");
        return 2;
    }
    V4Policy net;
    std::string err;
    if (!v4LoadPolicy(argv[1], net, err)) {
        std::fprintf(stderr, "[trainer] %s\n", err.c_str());
        return 1;
    }
    std::fprintf(stderr, "[trainer] v4 网络已加载：%s（%s）\n", argv[1], v4Describe(net).c_str());

    long long total = 0;
    for (int a = 2; a < argc; a++) {
        const std::string path = argv[a];
        std::string text;
        if (!readTextFile(path, text)) {
            std::fprintf(stderr, "[trainer] 找不到 corpus 文件：%s\n", path.c_str());
            return 1;
        }
        long long count = 0;
        size_t pos = 0;
        while (pos <= text.size()) {
            const size_t nl = text.find('\n', pos);
            const size_t end = nl == std::string::npos ? text.size() : nl;
            const std::string line = text.substr(pos, end - pos);
            const bool last = nl == std::string::npos;
            pos = last ? text.size() + 1 : nl + 1;
            if (line.find_first_not_of(" \t\r\n") == std::string::npos) {
                if (last) {
                    break;
                }
                continue;
            }
            JVal row;
            if (!jsonParse(line, row) || !row.isObj()) {
                std::fprintf(stderr, "[trainer] %s：这一行不是合法 JSON 对象\n", path.c_str());
                return 1;
            }
            const JVal *type = row.find("type");
            if (type == nullptr || !type->isStr() || type->strVal != "decision") {
                if (last) {
                    break;                                    // 非 decision 行跳过
                }
                continue;
            }
            const JVal *obs = row.find("obs");
            if (obs == nullptr || !obs->isObj()) {
                std::fprintf(stderr, "[trainer] %s：decision 行缺 obs\n", path.c_str());
                return 1;
            }
            const JVal *legal = row.find("legal");
            if (legal == nullptr || !legal->isArr()) {
                std::fprintf(stderr, "[trainer] %s：decision 行缺 legal\n", path.c_str());
                return 1;
            }
            std::vector<float> logits;
            if (!v4Logits(net, *obs, logits, err)) {
                std::fprintf(stderr, "[trainer] %s（文件 %s，第 %lld 条决策）\n", err.c_str(),
                             path.c_str(), count + 1);
                return 1;
            }
            const JVal *step = row.find("step");
            const long long stepVal
                    = (step != nullptr && step->type == JVal::Type::Num) ? step->intVal : -1;
            std::printf("step=%lld n=%zu argmax=%d logits=", stepVal, logits.size(),
                        netArgmax(logits));
            for (size_t i = 0; i < logits.size(); i++) {
                if (i > 0) {
                    std::printf(",");
                }
                // 用 `javaG9`（而不是直接 `%.9g`）：两者的差别只在"十进制恰好一半"的舍入方向上，
                // 但那是 Java Formatter（half-up）与 glibc printf（ties-to-even）的差别，
                // 会让逐字符对拍出现假红 —— 判据本身不放松，见 `javaG9` 的注释。
                const std::string s = javaG9(logits[i]);
                std::printf("%s", s.c_str());
            }
            std::printf("\n");
            count++;
            total++;
            if (last) {
                break;
            }
        }
        if (count == 0) {
            std::printf("# %s 0 条\n", path.c_str());
        }
    }
    std::fflush(stdout);
    std::fprintf(stderr, "[trainer] 共 %lld 条决策的 v4 前向完成\n", total);
    return 0;
}

// ---------------------------------------------------------------- CLI：v4bench

/**
 * `trainer v4bench <net.bin> <轨迹.jsonl> [条数上限]`：把**特征拼装**与**网络前向**分开计时。
 *
 * <p>为什么必须分开：特征与网络的优化手段完全不同（特征是引擎派生量 → 少算/复用；网络是稠密
 * 矩阵向量 → SIMD/循环推理）。混成一个数就不知道该动哪一边。与 Java
 * `tools.V4Probe --bench` 同一件事（那一份是 Java 侧的口径），但这里**不写死任何验收数字**：
 * 只报本机实测。
 */
int v4BenchCli(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr,
                     "用法：trainer v4bench <net.bin> <轨迹.jsonl> [条数上限]\n"
                     "  把 JSON 解析 / v4 特征拼装 / 网络前向分开计时（每决策毫秒）。\n");
        return 2;
    }
    const int limit = argc > 3 ? std::atoi(argv[3]) : 200;
    V4Policy net;
    std::string err;
    if (!v4LoadPolicy(argv[1], net, err)) {
        std::fprintf(stderr, "[trainer] %s\n", err.c_str());
        return 1;
    }
    std::string text;
    if (!readTextFile(argv[2], text)) {
        std::fprintf(stderr, "[trainer] 找不到 corpus 文件：%s\n", argv[2]);
        return 1;
    }
    std::vector<std::string> lines;
    {
        size_t pos = 0;
        while (pos <= text.size() && static_cast<int>(lines.size()) < limit) {
            const size_t nl = text.find('\n', pos);
            const size_t end = nl == std::string::npos ? text.size() : nl;
            std::string line = text.substr(pos, end - pos);
            const bool last = nl == std::string::npos;
            pos = last ? text.size() + 1 : nl + 1;
            if (line.find_first_not_of(" \t\r\n") != std::string::npos) {
                lines.push_back(std::move(line));
            }
            if (last) {
                break;
            }
        }
    }
    // ① JSON 解析（只留 decision 行的 `obs`）
    std::vector<JVal> obs;
    const auto t0 = std::chrono::steady_clock::now();
    for (const std::string &line : lines) {
        JVal row;
        if (!jsonParse(line, row) || !row.isObj()) {
            continue;
        }
        const JVal *type = row.find("type");
        if (type == nullptr || !type->isStr() || type->strVal != "decision") {
            continue;
        }
        const JVal *o = row.find("obs");
        if (o != nullptr && o->isObj()) {
            obs.push_back(*o);
        }
    }
    const auto t1 = std::chrono::steady_clock::now();
    if (obs.empty()) {
        std::fprintf(stderr, "[trainer] 语料里没有 decision 行\n");
        return 1;
    }
    // ② 特征拼装（消融表为空 = 生产口径）
    const std::vector<std::string> ablate;
    V4Tensors t;
    for (int w = 0; w < 5 && w < static_cast<int>(obs.size()); w++) {   // 预热（首次触页 / 分支预测）
        if (!v4Assemble(obs[static_cast<size_t>(w)], ablate, t, err)) {
            std::fprintf(stderr, "[trainer] %s\n", err.c_str());
            return 1;
        }
    }
    const auto t2 = std::chrono::steady_clock::now();
    for (const JVal &o : obs) {
        if (!v4Assemble(o, ablate, t, err)) {
            std::fprintf(stderr, "[trainer] %s\n", err.c_str());
            return 1;
        }
    }
    const auto t3 = std::chrono::steady_clock::now();
    // ③ 前向（七个头；内部**自己再拼一次特征** ⇒ 净前向 = ③ − ②）
    std::map<std::string, std::vector<float>> all;
    for (const JVal &o : obs) {
        if (!net.forwardAll(o, all, err)) {
            std::fprintf(stderr, "[trainer] %s\n", err.c_str());
            return 1;
        }
    }
    const auto t4 = std::chrono::steady_clock::now();
    const double n = static_cast<double>(obs.size());
    const double msParse = std::chrono::duration<double, std::milli>(t1 - t0).count() / n;
    const double msFeat = std::chrono::duration<double, std::milli>(t3 - t2).count() / n;
    const double msAll = std::chrono::duration<double, std::milli>(t4 - t3).count() / n;
    std::printf("bench n=%.0f 解析 %.3f ms/决策 | 特征 %.3f ms/决策 | 前向(含特征) %.3f ms/决策"
                " | 前向净 %.3f ms/决策 | 单线程 %.1f 决策/秒\n",
                n, msParse, msFeat, msAll, msAll - msFeat, 1000.0 / (msParse + msAll));
    return 0;
}

// ---------------------------------------------------------------- CLI：v4golden

namespace {

/** 逐元素比较的收集器：记最大差 + 超差最多的前 N 格（带标签）= Java `V4Probe.Cmp`。 */
struct Cmp {
    float tol = 1e-4f;
    size_t limit = 12;
    float worst = 0.f;
    std::vector<std::string> bad;

    void add(const std::string &label, float diff) {
        worst = std::max(worst, diff);
        if (diff > tol && bad.size() < limit) {
            char buf[64];
            std::snprintf(buf, sizeof(buf), "%.4g", static_cast<double>(diff));
            bad.push_back(label + " Δ=" + buf);
        }
    }
    void dump(const char *what) const {
        if (bad.empty()) {
            return;
        }
        std::string line;
        for (size_t i = 0; i < bad.size(); i++) {
            if (i > 0) {
                line += " | ";
            }
            line += bad[i];
        }
        std::printf("  %s 超差格子：%s\n", what, line.c_str());
    }
};

void cmpMat(Cmp &c, const std::string &name, const float *a, const float *b, int rows, int cols,
            const char *l0, const char *l1) {
    for (int i = 0; i < rows; i++) {
        for (int j = 0; j < cols; j++) {
            c.add(name + "[" + l0 + std::to_string(i) + "][" + l1 + std::to_string(j) + "]",
                  std::fabs(a[static_cast<size_t>(i) * static_cast<size_t>(cols)
                              + static_cast<size_t>(j)]
                            - b[static_cast<size_t>(i) * static_cast<size_t>(cols)
                                + static_cast<size_t>(j)]));
        }
    }
}

void cmpVec(Cmp &c, const std::string &name, const float *a, const float *b, int n,
            const char *l0) {
    for (int i = 0; i < n; i++) {
        c.add(name + "[" + l0 + std::to_string(i) + "]", std::fabs(a[i] - b[i]));
    }
}

constexpr uint32_t kGoldenMagic = 0x4D4A3447U;   // "MJ4G"
/** 夹具格式（= `python/mahjong_ml/v4/export.py` 的 `GOLDEN_FORMAT`）。 */
constexpr int32_t kGoldenFormat = 2;

/**
 * Java `String.format("%g", (float) x)`（精度 6）的等价物。
 *
 * <p>为什么不能直接用 C 的 `%g`：C 会**抹掉尾随零**（1e-4 → `0.0001`），而 Java 保留它们
 * （→ `0.000100000`）。汇总行要跟 `tools/V4Probe.java --golden` 逐字符一样，就只能自己排。
 * ⚠ 指数必须**按四舍五入到 6 位有效数字之后**算：`1e-4f` 的 double 值是 9.9999997e-05，
 * 直接取 `log10` 会得到 -5（Java 报的是 -4）。
 */
std::string javaPercentG(double v, int precision = 6) {
    char sci[64];
    std::snprintf(sci, sizeof(sci), "%.*e", precision - 1, v);
    const char *e = std::strchr(sci, 'e');
    const int exp = e == nullptr ? 0 : std::atoi(e + 1);
    if (exp < -4 || exp >= precision) {
        return sci;
    }
    char buf[64];
    const int decimals = precision - 1 - exp;
    std::snprintf(buf, sizeof(buf), "%.*f", decimals > 0 ? decimals : 0, v);
    return buf;
}

/** `{"a","b"}` → `"a,b"`（头表的汇总行；与 Java `String.join(",", …)` 同形）。 */
std::string joinNames(const std::vector<std::string> &names) {
    std::string s;
    for (size_t i = 0; i < names.size(); i++) {
        if (i > 0) {
            s += ",";
        }
        s += names[i];
    }
    return s;
}

/** 两条 logits 的最大绝对差（头表判据用；"逐位不变"就是这里**恰好 0**）= Java `maxAbsDelta`。 */
float maxAbsDelta(const std::vector<float> &a, const std::vector<float> &b) {
    const size_t n = std::min(a.size(), b.size());
    float w = 0.f;
    for (size_t i = 0; i < n; i++) {
        w = std::max(w, std::fabs(a[i] - b[i]));
    }
    return w;
}

/**
 * 深拷贝一份权重，并把 `heads.<head>.*` 的**全部**张量清零（= Java `V4Policy.debugZeroHead`）——
 * 头表判据（§6.25）的探针：清零后重跑前向，看策略头 logits 变不变。
 *
 * <p>⚠ ① 只在**副本**上动刀：调用方的夹具权重一次都不改（后面的用例还要用）。
 * <p>⚠ ② `Mat` 有**两份**存储（`data` 与加载期建好的转置副本 `dataT`），**两份都要清** ——
 * 只清 `data` 的话 `linear`/`linearFast` 仍从 `dataT` 读到原值，探针会测出一个**假绿**的 Δ=0。
 * <p>⚠ ③ 前缀带点（`heads.<head>.`）⇒ 清零 `policy` 不会误伤 `policy_gate`（后者是**策略路径**
 * 的一部分，清零它必须改变 logits）。
 *
 * @param hit 出参：是否真的清到了张量（`policy_gate` 是**可缺张量**，老网没有它）。
 */
void zeroHeadInto(const V4Policy &src, const std::string &head, V4Policy &out, bool &hit) {
    out = src;
    const std::string prefix = "heads." + head + ".";
    hit = false;
    for (auto &kv : out.mats) {
        if (kv.first.compare(0, prefix.size(), prefix) == 0) {
            std::fill(kv.second.data.begin(), kv.second.data.end(), 0.f);
            std::fill(kv.second.dataT.begin(), kv.second.dataT.end(), 0.f);
            hit = true;
        }
    }
    for (auto &kv : out.vecs) {
        if (kv.first.compare(0, prefix.size(), prefix) == 0) {
            std::fill(kv.second.begin(), kv.second.end(), 0.f);
            hit = true;
        }
    }
}

}  // namespace

int v4GoldenCli(int argc, char **argv) {
    std::string file;
    float tol = 1e-4f;
    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        if (a == "--tol" && i + 1 < argc) {
            char *end = nullptr;
            const float v = std::strtof(argv[++i], &end);
            if (end == nullptr || *end != '\0') {
                std::fprintf(stderr, "[trainer] --tol 不是数：%s\n", argv[i]);
                return 2;
            }
            tol = v;
        } else if (file.empty()) {
            file = a;
        }
    }
    if (file.empty()) {
        std::fprintf(stderr, "用法：trainer v4golden <夹具> [--tol 1e-4]\n");
        return 2;
    }
    std::vector<uint8_t> raw;
    std::string err;
    if (!readWholeFile(file, raw, err)) {
        std::fprintf(stderr, "[trainer] %s\n", err.c_str());
        return 1;
    }
    Reader r{raw.data(), raw.size(), 0, false};
    const uint32_t magic = r.u32();
    const int32_t fmt = r.i32();
    const int32_t nCases = r.i32();
    const int32_t netLen = r.i32();
    if (magic != kGoldenMagic) {
        std::fprintf(stderr, "[trainer] 夹具魔数不对：%#x（期望 %#x）\n",
                     static_cast<unsigned>(magic), kGoldenMagic);
        return 1;
    }
    // ⛔ **格式必须硬校验**：格式 1 的用例里没有 `h0` / `h_evt` 两列，按本文件的读法会整份错位，
    //    "尽力兼容"的后果是拿垃圾数据比对出一个假 PASS（比报错危险得多）。
    if (fmt != kGoldenFormat) {
        std::fprintf(stderr, "[trainer] 夹具格式 %d != %d（格式 1 是 W1 之前的布局，不兼容）"
                             "—— 重新生成：python -m mahjong_ml.v4.export golden …\n",
                     fmt, kGoldenFormat);
        return 1;
    }
    if (netLen < 0 || r.bad) {
        std::fprintf(stderr, "[trainer] 夹具头部坏了（netLen=%d）\n", netLen);
        return 1;
    }
    std::vector<uint8_t> netBytes(static_cast<size_t>(netLen));
    if (netLen > 0) {
        r.bytes(netBytes.data(), static_cast<size_t>(netLen));
    }
    V4Policy net;
    if (!v4LoadPolicyBytes(netBytes, file, net, err)) {
        std::fprintf(stderr, "[trainer] 夹具里的权重加载失败：%s\n", err.c_str());
        return 1;
    }
    const int vb = net.valueBins;
    // 先把块清单指纹打出来（= Java `V4Policy.describe()` 里的那一个）：它是 sha256 实现的判据 ——
    // 与 Java 的 `e1f5dd0fc1e9aba8` 一致，才说明"块清单 → 指纹"这条链在 C++ 侧也对。
    std::printf("夹具 %s：%s 注册表指纹 %s\n", file.c_str(), v4Describe(net).c_str(),
                v4Fingerprint().c_str());

    Cmp feat;
    feat.tol = tol;
    Cmp headCmp;
    headCmp.tol = tol;
    int argmaxOk = 0;
    std::vector<JVal> obsList;
    // 第一个用例的 obs 与 h0 —— W1 的"扰动 h0"红证要用（见循环之后）。
    JVal firstObs;
    std::vector<float> firstH0;
    bool haveFirst = false;
    for (int c = 0; c < nCases; c++) {
        const int32_t obsLen = r.i32();
        std::vector<char> ob(static_cast<size_t>(std::max(0, obsLen)));
        if (obsLen > 0) {
            r.bytes(ob.data(), static_cast<size_t>(obsLen));
        }
        JVal obs;
        if (!jsonParse(std::string(ob.begin(), ob.end()), obs) || !obs.isObj()) {
            std::fprintf(stderr, "[trainer] 夹具第 %d 个用例的 obs 不是合法 JSON 对象\n", c);
            return 1;
        }
        obsList.push_back(obs);
        const uint16_t n = r.u16();
        for (int i = 0; i < n; i++) {
            const uint16_t kl = r.u16();
            r.need(kl);                                       // 键只用于人读，不参与比较
            r.pos += kl;
        }
        // 格式 2 起每用例多一个 `h0`（**窗口之前**的 GRU carry）：三端拿同一个 `h0` 才能判
        // "同 h ⇒ 同输出"（W1）。它在四张量**之前**。
        std::vector<float> h0(static_cast<size_t>(net.dModel));
        for (float &v : h0) {
            v = r.f32();
        }
        std::vector<float> tile(static_cast<size_t>(kKindCount) * kCTile);
        std::vector<float> evt(static_cast<size_t>(kKEvt) * kCEvt);
        std::vector<float> ctx(kCCtx);
        std::vector<float> cand(static_cast<size_t>(n) * kCCand);
        std::vector<float> logits(n);
        std::vector<float> value(static_cast<size_t>(vb));
        std::vector<float> belief(3);
        std::vector<float> danger(static_cast<size_t>(n) * 4);
        std::vector<float> hEvt(static_cast<size_t>(net.dModel));   // 窗口**之后**的 carry（最后）
        for (float &v : tile) {
            v = r.f32();
        }
        for (float &v : evt) {
            v = r.f32();
        }
        for (float &v : ctx) {
            v = r.f32();
        }
        for (float &v : cand) {
            v = r.f32();
        }
        for (float &v : logits) {
            v = r.f32();
        }
        for (float &v : value) {
            v = r.f32();
        }
        for (float &v : belief) {
            v = r.f32();
        }
        for (float &v : danger) {
            v = r.f32();
        }
        for (float &v : hEvt) {
            v = r.f32();
        }
        if (r.bad) {
            std::fprintf(stderr, "[trainer] 夹具在第 %d 个用例处被截断\n", c);
            return 1;
        }
        if (c == 0) {
            firstObs = obs;
            firstH0 = h0;
            haveFirst = true;
        }

        V4Tensors t;
        if (!v4Assemble(obs, {}, t, err)) {
            std::fprintf(stderr, "[trainer] 夹具第 %d 个用例：特征拼装失败：%s\n", c, err.c_str());
            return 1;
        }
        if (static_cast<int>(t.cand.size()) != static_cast<int>(n)) {
            std::fprintf(stderr, "[trainer] 夹具第 %d 个用例：候选数对不上（obs.legal %zu != 夹具 %u）\n",
                         c, t.cand.size(), static_cast<unsigned>(n));
            return 1;
        }
        const std::string tag = "c" + std::to_string(c) + ".";
        std::vector<float> myTile;
        for (int k = 0; k < kKindCount; k++) {
            for (int ch = 0; ch < kCTile; ch++) {
                myTile.push_back(t.tile[static_cast<size_t>(k)][static_cast<size_t>(ch)]);
            }
        }
        std::vector<float> myEvt;
        for (int row = 0; row < kKEvt; row++) {
            for (int f = 0; f < kCEvt; f++) {
                myEvt.push_back(t.evt[static_cast<size_t>(row)][static_cast<size_t>(f)]);
            }
        }
        std::vector<float> myCand;
        for (const auto &row : t.cand) {
            for (float x : row) {
                myCand.push_back(x);
            }
        }
        cmpMat(feat, tag + "tile", myTile.data(), tile.data(), kKindCount, kCTile, "k", "ch");
        cmpMat(feat, tag + "evt", myEvt.data(), evt.data(), kKEvt, kCEvt, "row", "f");
        cmpVec(feat, tag + "ctx", t.ctx.data(), ctx.data(), kCCtx, "i");
        cmpMat(feat, tag + "cand", myCand.data(), cand.data(), n, kCCand, "row", "col");

        std::map<std::string, std::vector<float>> o;
        // ⚠ **用显式 h0 的那条入口**（不是整手 carry）：夹具的 h0 是 Python 按"窗口之前"的口径
        //   算出来的，拿整手 carry 去比就是两个量（W1 之前这里蒙对了，因为那时窗口 60 行正好
        //   覆盖全部真实事件；一旦一小局超过 60 条事件就不再成立）。
        if (!v4ForwardAllH0(net, obs, h0, o, err)) {
            std::fprintf(stderr, "[trainer] 夹具第 %d 个用例：前向失败：%s\n", c, err.c_str());
            return 1;
        }
        if (o["policy"].size() != static_cast<size_t>(n)
                || o["value"].size() != static_cast<size_t>(vb)
                || o["h_evt"].size() != static_cast<size_t>(net.dModel)) {
            std::fprintf(stderr, "[trainer] 夹具第 %d 个用例：前向输出宽度与夹具不符"
                                 "（policy %zu != %u，value %zu != %d，h_evt %zu != %d）\n",
                         c, o["policy"].size(), static_cast<unsigned>(n), o["value"].size(), vb,
                         o["h_evt"].size(), net.dModel);
            return 1;
        }
        cmpVec(headCmp, tag + "policy", o["policy"].data(), logits.data(), n, "i");
        cmpVec(headCmp, tag + "value", o["value"].data(), value.data(), vb, "i");
        cmpVec(headCmp, tag + "belief_tenpai", o["belief_tenpai"].data(), belief.data(), 3, "i");
        cmpMat(headCmp, tag + "danger", o["danger"].data(), danger.data(), n, 4, "row", "col");
        // W1 新增：事件塔的隐状态（融合的输入之一）。只看七个头的话，`h0` 的影响会被随机初始化的
        // GRU 洗到 3e-8（判据看不见）—— 所以直接比这个向量。
        cmpVec(headCmp, tag + "h_evt", o["h_evt"].data(), hEvt.data(), net.dModel, "i");
        if (netArgmax(o["policy"]) == netArgmax(logits)) {
            argmaxOk++;
        }
    }
    // 红证：策略头偏置 +1 ⇒ 每条 logit 恰好 +1
    float worstRed = 0.f;
    if (!obsList.empty()) {
        V4Policy shifted;
        if (!v4DebugOutputBiasShift(net, 1.0f, shifted, err)) {
            std::fprintf(stderr, "[trainer] 红证构造失败：%s\n", err.c_str());
            return 1;
        }
        std::vector<float> a, b;
        if (!net.logits(obsList[0], a, err) || !shifted.logits(obsList[0], b, err)) {
            std::fprintf(stderr, "[trainer] 红证前向失败：%s\n", err.c_str());
            return 1;
        }
        for (size_t i = 0; i < a.size(); i++) {
            worstRed = std::max(worstRed, std::fabs((b[i] - a[i]) - 1.f));
        }
    }
    // 红证（W1）：**`h0` 真的被消费** —— 把第一个用例的 h0 整体 +0.5 再跑一遍，策略头必须变。
    //   为什么必须要这条：`fusion.mem` 缺张量时 `g_mem ≡ 1`、或者"算完 carry 就丢掉"这两种写法
    //   都能让上面所有比较**全绿**（前者本来就该绿，后者在旧夹具上也能绿）—— 只有"扰动 h ⇒ 输出变"
    //   能证明这条链真的接上了。夹具把更新门偏置刻意抬大，所以这个差在 1e-2 量级。
    float worstH0 = 0.f;
    if (haveFirst) {
        std::vector<float> h0b = firstH0;
        for (float &v : h0b) {
            v += 0.5f;
        }
        std::map<std::string, std::vector<float>> oa, ob;
        if (!v4ForwardAllH0(net, firstObs, firstH0, oa, err)
                || !v4ForwardAllH0(net, firstObs, h0b, ob, err)) {
            std::fprintf(stderr, "[trainer] h0 红证前向失败：%s\n", err.c_str());
            return 1;
        }
        const size_t cnt = std::min(oa["policy"].size(), ob["policy"].size());
        for (size_t i = 0; i < cnt; i++) {
            worstH0 = std::max(worstH0, std::fabs(ob["policy"][i] - oa["policy"][i]));
        }
    }
    // ---- 头表判据（唯一规格 `docs/TRAINER-CPP.md` §6.25；与 Python `v4 check` ⑦/⑦′、Java 侧同一条线）
    //
    //   "头是否上线" = **前向路径是否读取**（⛔ 不是"训练过"）：把该头的张量**清零**后重跑同一次前向
    //   （同一个用例、同一个 `h0`）⇒ 策略头 logits **逐位不变** ⇔ 它不在推理路径上。
    //   · ① **负向** `heads.value.*`（离线头）⇒ 必须**恰好 0**；
    //   · ② **正向** `heads.belief_tenpai.*`（经 `policy_gate` 逐候选门控进 policy）⇒ 必须 > 0；
    //   · ③ **哨兵** `heads.policy_gate.*`（本身就在策略路径上）⇒ 必须 > 0 —— 门控恒等时 ② 会**假绿**
    //     （零初始化门控的原设计就是"起步恒等"），所以 ③ 必须有牙齿，否则整条判据是空转。
    //   ⚠ 参照与三次探针都走**夹具那条入口**（显式 `h0`），且只用**第一个用例** —— 与 Java
    //     `V4Probe --golden` 逐字同口径（三个数字要能跨端直接比）。
    float dValue = 0.f;
    float dBelief = 0.f;
    float dGate = 0.f;
    // 门控"是活的"的判据 = 前向自己的那个条件（`heads.policy_gate.weight` 在不在）。
    const bool gatePresent = net.mats.count("heads.policy_gate.weight") != 0;
    if (haveFirst) {
        std::map<std::string, std::vector<float>> base;
        if (!v4ForwardAllH0(net, firstObs, firstH0, base, err)) {
            std::fprintf(stderr, "[trainer] 头表判据的参照前向失败：%s\n", err.c_str());
            return 1;
        }
        const std::vector<float> &refPolicy = base["policy"];
        struct Probe {
            const char *head;
            float *out;
        };
        const Probe probes[3] = {{"value", &dValue},
                                 {"belief_tenpai", &dBelief},
                                 {"policy_gate", &dGate}};
        for (const Probe &pr : probes) {
            V4Policy zeroed;
            bool hit = false;
            zeroHeadInto(net, pr.head, zeroed, hit);
            if (!hit) {
                // `value` / `belief_tenpai` 是契约头（`bindAll` 已强制存在）⇒ 这里只可能是
                // `policy_gate` 这个**可缺张量**；Δ 恰为 0 是**结构性**的，不是测量结果。
                std::printf("  （%s 无张量可清零：可缺张量缺失 ⇒ 该头不在前向路径上）\n", pr.head);
                *pr.out = 0.f;
                continue;
            }
            std::map<std::string, std::vector<float>> o;
            if (!v4ForwardAllH0(zeroed, firstObs, firstH0, o, err)) {
                std::fprintf(stderr, "[trainer] 头表判据（清零 heads.%s.*）前向失败：%s\n", pr.head,
                             err.c_str());
                return 1;
            }
            *pr.out = maxAbsDelta(refPolicy, o["policy"]);
        }
    }
    // ③ 的两种"Δ=0"要分开说（⛔ 别混成一句"没测到"）：
    //   · **张量缺失** = 老网（`normalize_state`：缺 ⇒ 恒等门控）⇒ `belief_tenpai` 本来就不在推理
    //     路径上，在线集合退化成 `{policy}`（§6.25 明文）⇒ ②③ 不参与判红，只**明确报出来**；
    //   · **张量在、但全 0** = 夹具空转（`export.py` 那个"零初始化张量一律扰动"没生效）⇒ 判红。
    const bool gateVacuouslyIdentity = !gatePresent;
    if (gateVacuouslyIdentity || dGate == 0.f) {
        std::fprintf(stderr,
                     "[trainer] ⚠ 门控恒等：%s\n",
                     gateVacuouslyIdentity
                             ? "夹具里没有 `heads.policy_gate.weight`（可缺张量 = 老网口径）⇒ 门控恒等，"
                               "`belief_tenpai` 不在推理路径上、ONLINE_HEADS 退化成 {policy}；②③ 不参与判红"
                             : "夹具里 `heads.policy_gate.*` 全是 0（扰动没生效）⇒ 门控恒等、② 是假绿："
                               "这是**夹具空转**，按 FAIL 处理（重新生成夹具，见 export.py 的扰动规则）");
    }
    // ② 的期望值随门控是否在线而变：门控恒等时 `btProb` 怎么变都不影响 policy ⇒ 期望**恰好 0**。
    const bool headsOk = dValue == 0.f
            && (gateVacuouslyIdentity ? dBelief == 0.f : (dBelief > 0.f && dGate > 0.f));
    const bool pass = feat.worst <= tol && headCmp.worst <= tol && argmaxOk == nCases
            && worstRed <= 1e-3f && worstH0 > 1e-4f && headsOk;
    if (!pass) {
        feat.dump("特征侧");
        headCmp.dump("前向侧");
    }
    std::printf("golden cases=%d tol=%s 特征 maxΔ=%.3g 前向 maxΔ=%.3g argmax=%d/%d 红证 maxΔ=%.3g"
                " h0红证 maxΔ=%.3g → %s"
                " heads online=%s contract=%s dValue=%s dBelief=%s dGate=%s\n",
                nCases, javaPercentG(static_cast<double>(tol)).c_str(),
                static_cast<double>(feat.worst), static_cast<double>(headCmp.worst), argmaxOk,
                nCases, static_cast<double>(worstRed), static_cast<double>(worstH0),
                pass ? "PASS" : "FAIL",
                // ⚠ `online=` 必须按 **checkpoint** 打印（§6.25：这是"每个 checkpoint 的性质"）：
                //   门控缺失（老网）时真实在线集合是 `{policy}` —— 那时仍打印常量就等于说谎，
                //   而且会与 Java 侧同口径的 `online=policy` 对不上（两端要逐字符可比）。
                (gatePresent ? joinNames(ONLINE_HEADS) : std::string("policy")).c_str(),
                joinNames(TRAIN_HEADS).c_str(),
                // ⚠ 三个 d* 必须走 `javaPercentG(…, 3)` 而不是 C 的 `%.3g`：Java 的 `%g` 对 0 保留
                //   尾随零（`0.00`），C 的抹掉（`0`）—— 两端的汇总行要**逐字符**一样（Java 侧就是
                //   `String.format("%.3g", …)`），差一个字符就等于"没有可比性"。
                javaPercentG(static_cast<double>(dValue), 3).c_str(),
                javaPercentG(static_cast<double>(dBelief), 3).c_str(),
                javaPercentG(static_cast<double>(dGate), 3).c_str());
    return pass ? 0 : 1;
}

// ---------------------------------------------------------------- CLI：v4cache（增量 carry 的硬判据）

namespace {

/** 两条前向的逐位比对（**逐头逐元素**；NaN / -0.0 也按比特比，绝不放过大）。 */
struct HeadDiff {
    int badHeads = 0;
    int badElems = 0;
    std::string firstHead;
    size_t firstIdx = 0;
    float a = 0.f;
    float b = 0.f;
};

bool bitsEqual(float x, float y) {
    return std::memcmp(&x, &y, sizeof(float)) == 0;
}

void diffHeads(const std::map<std::string, std::vector<float>> &x,
               const std::map<std::string, std::vector<float>> &y, HeadDiff &out) {
    for (const auto &kv : x) {
        const auto it = y.find(kv.first);
        if (it == y.end() || it->second.size() != kv.second.size()) {
            out.badHeads++;
            if (out.firstHead.empty()) {
                out.firstHead = kv.first + "（形状不一致）";
            }
            continue;
        }
        int bad = 0;
        size_t firstBad = 0;
        for (size_t i = 0; i < kv.second.size(); i++) {
            if (!bitsEqual(kv.second[i], it->second[i])) {
                if (bad == 0) {
                    firstBad = i;
                }
                bad++;
            }
        }
        if (bad > 0) {
            out.badHeads++;
            out.badElems += bad;
            if (out.firstHead.empty()) {
                out.firstHead = kv.first;
                out.firstIdx = firstBad;
                out.a = kv.second[firstBad];
                out.b = it->second[firstBad];
            }
        }
    }
}

/** 一个 worker 的累计读数（线程内累加，最后合并）。 */
struct CacheRun {
    long long rows = 0;
    long long badRows = 0;
    long long badHeads = 0;
    long long badElems = 0;
    long long h0Rows = 0;
    long long h0BadRows = 0;
    long long eventsTotal = 0;      // 逐决策的 `events` 之和（= 整手重放要喂的行数）
    long long grnRows = 0;          // 增量路径实际喂进 GRU 的行数（编码 + 窗口）
    double secFull = 0;
    double secCached = 0;
    V4CarryStats st;
    std::string firstBad;           // 首个不符的定位（file:line head[i] a vs b）
};

/** 一个文件里的 decision 行逐条对拍（`maxRows < 0` = 不限条数；`maxRows` 是**本文件**的条数上限）。 */
bool oneFile(const V4Policy &net, const std::string &path, int maxRows, int h0BudgetLeft,
             int &h0Used, CacheRun &run, std::string &err) {
    std::string text;
    if (!readTextFile(path, text)) {
        err = "读不了文件：" + path;
        return false;
    }
    // ⚠ 两个 scratch **每个文件新建**（= 生产语义：每场一份策略实例 ⇒ 每场一份 scratch），
    //   于是"换小局必须重建"这条路径被逐场真实走过；跨文件复用留给 `batchforward` 那种时序。
    V4Scratch scrFull;
    scrFull.carryEnabled = false;
    V4Scratch scrCached;
    scrCached.carryEnabled = true;
    V4Scratch scrH0;
    scrH0.carryEnabled = false;
    size_t pos = 0;
    int lineNo = 0;
    int rowsThisFile = 0;       // ⚠ 必须按**本文件**计数：拿 `run.rows`（线程累计）去比"全局余额"会在
                                //   余额小于累计时**立刻 break**，于是外层循环把剩下的文件空跑一遍
                                //   （本轮实测：`--limit 20000 --workers 1` 空转了 11,985 个文件、
                                //   最终只数到 10,148 行）。
    while (pos <= text.size()) {
        const size_t nl = text.find('\n', pos);
        const size_t end = nl == std::string::npos ? text.size() : nl;
        const std::string line = text.substr(pos, end - pos);
        const bool last = nl == std::string::npos;
        pos = last ? text.size() + 1 : nl + 1;
        lineNo++;
        if (line.find_first_not_of(" \t\r\n") == std::string::npos) {
            if (last) {
                break;
            }
            continue;
        }
        JVal row;
        if (!jsonParse(line, row) || !row.isObj()) {
            continue;
        }
        const JVal *type = row.find("type");
        if (type == nullptr || !type->isStr() || type->strVal != "decision") {
            continue;
        }
        const JVal *obs = row.find("obs");
        if (obs == nullptr || !obs->isObj()) {
            err = path + "：decision 行缺 obs";
            return false;
        }
        if (maxRows >= 0 && rowsThisFile >= maxRows) {
            break;
        }
        rowsThisFile++;
        // ① 全量（基准）：整手重放
        std::map<std::string, std::vector<float>> outFull;
        const auto t0 = std::chrono::steady_clock::now();
        if (!net.forwardAll(*obs, outFull, err, nullptr, &scrFull)) {
            err = path + "：" + err;
            return false;
        }
        const auto t1 = std::chrono::steady_clock::now();
        // ② 增量（被测）：只推进新事件
        std::map<std::string, std::vector<float>> outCached;
        if (!net.forwardAll(*obs, outCached, err, nullptr, &scrCached)) {
            err = path + "：" + err;
            return false;
        }
        const auto t2 = std::chrono::steady_clock::now();
        run.secFull += std::chrono::duration<double>(t1 - t0).count();
        run.secCached += std::chrono::duration<double>(t2 - t1).count();
        HeadDiff d;
        diffHeads(outFull, outCached, d);
        run.rows++;
        if (d.badHeads > 0) {
            run.badRows++;
            run.badHeads += d.badHeads;
            run.badElems += d.badElems;
            if (run.firstBad.empty()) {
                run.firstBad = path + ":" + std::to_string(lineNo) + "  头 " + d.firstHead + "["
                        + std::to_string(d.firstIdx) + "]  全量=" + std::to_string(d.a)
                        + " 增量=" + std::to_string(d.b) + "（本行 " + std::to_string(d.badHeads)
                        + " 个头不符）";
            }
        }
        // ③ 第三条逐位判据（W1）：前缀 carry + 窗口 == 整手重放
        const std::vector<const JVal *> ev = v4EventsOf(*obs);
        run.eventsTotal += static_cast<long long>(ev.size());
        if (h0BudgetLeft != 0) {          // 0 = 本文件不再跑；<0 = 不限；>0 = 还允许这么多次
            const int dm = net.dModel;
            const JVal *seatVal = obs->find("seat");
            const int seat = seatVal == nullptr ? 0 : seatVal->asInt(0);
            EventEnc enc;
            EventGru gru;
            if (!enc.bind(net, err) || !gru.bind(net, err)) {
                return false;
            }
            if (scrH0.carry.size() < static_cast<size_t>(dm)) {
                scrH0.carry.assign(static_cast<size_t>(dm), 0.f);
                scrH0.tok.assign(static_cast<size_t>(kCEvt), 0.f);
                scrH0.rowTmp.assign(static_cast<size_t>(dm), 0.f);
            }
            std::fill(scrH0.carry.begin(), scrH0.carry.end(), 0.f);
            if (!replayCarry(net, ev, seat, std::max(0, static_cast<int>(ev.size()) - kKEvt), enc, gru,
                             scrH0, err)) {
                err = path + "：" + err;
                return false;
            }
            const std::vector<float> h0(scrH0.carry.begin(), scrH0.carry.end());
            std::map<std::string, std::vector<float>> outH0;
            if (!net.forwardAll(*obs, outH0, err, &h0, &scrH0)) {
                err = path + "：" + err;
                return false;
            }
            HeadDiff d0;
            diffHeads(outFull, outH0, d0);
            run.h0Rows++;
            h0Used++;
            if (d0.badHeads > 0) {
                run.h0BadRows++;
                if (run.firstBad.empty()) {
                    run.firstBad = path + ":" + std::to_string(lineNo) + "  h0 路 头 " + d0.firstHead
                            + "[" + std::to_string(d0.firstIdx) + "]  全量=" + std::to_string(d0.a)
                            + " 前缀+窗口=" + std::to_string(d0.b);
                }
            }
            if (h0BudgetLeft > 0) {
                h0BudgetLeft--;         // 预算用完 ⇒ 本文件余下行不再跑第三条（⛔ 别写成 `-1`：
                                        //   `-1` 在本函数里的含义是"**不限**"，写反了就是把整份语料
                                        //   全都跑第三条 —— 本轮实测踩到，205,416 行全跑了 h0 路）
            }
        }
        if (last) {
            break;
        }
    }
    const V4CarryStats st = v4CarryStatsOf(scrCached);
    run.st.decisions += st.decisions;
    run.st.hits += st.hits;
    run.st.cold += st.cold;
    run.st.stale += st.stale;
    run.st.rowsReplayed += st.rowsReplayed;
    run.st.rowsEncoded += st.rowsEncoded;
    run.st.rowsWindow += st.rowsWindow;
    run.grnRows += st.rowsEncoded + st.rowsWindow;
    return true;
}

/**
 * `--swap-net` 的**缓存键负向判据**：同一份 `V4Scratch`（= 生产里的"每场一份策略实例"）
 * 先用 A 预热，再让 B 在上面逐条跑；B 的**增量 carry 必须与它自己的整手重放逐位相同**。
 *
 * <p>为什么这样判：旧槽键只有 `handKey`（小局身份）⇒ 换网后第一张网的 carry 会被第二张网
 * 当成"这一局的 carry"直接复用。⚠ 这种复用在**计数上完全看不出来**（同一份语料、同样的
 * (小局, 座位) 序列 ⇒ hits/cold 一模一样），也不会有任何报错；唯一能抓住它的是**语义**：
 * B 的增量 `h_evt` 会等于 A 的 GRU 递推结果，与 B 自己的整手重放**不同** ⇒ 逐位红。
 * （前缀校验也帮不上忙：token 行不依赖网络，前缀当然对得上。）
 *
 * <p>强负向：`TRAINER_V4_OLD_KEY=1` 回到旧键（只给本判据用）⇒ 这一段必须报红。
 */
bool cacheSwapNetDemo(const V4Policy &a, const V4Policy &b, const std::string &file,
                      std::string &report, std::string &err) {
    auto rows = [&](std::vector<JVal> &obs) -> bool {
        std::string text;
        if (!readTextFile(file, text)) {
            err = "读不了文件：" + file;
            return false;
        }
        size_t pos = 0;
        while (pos <= text.size() && obs.size() < 400) {
            const size_t nl = text.find('\n', pos);
            const size_t end = nl == std::string::npos ? text.size() : nl;
            const std::string line = text.substr(pos, end - pos);
            const bool last = nl == std::string::npos;
            pos = last ? text.size() + 1 : nl + 1;
            if (line.find_first_not_of(" \t\r\n") == std::string::npos) {
                if (last) {
                    break;
                }
                continue;
            }
            JVal row;
            if (!jsonParse(line, row) || !row.isObj()) {
                continue;
            }
            const JVal *type = row.find("type");
            if (type == nullptr || !type->isStr() || type->strVal != "decision") {
                continue;
            }
            const JVal *obsJ = row.find("obs");
            if (obsJ != nullptr && obsJ->isObj()) {
                obs.push_back(*obsJ);
            }
            if (last) {
                break;
            }
        }
        return true;
    };
    std::vector<JVal> obs;
    if (!rows(obs)) {
        return false;
    }
    if (obs.empty()) {
        err = file + " 里没有 decision 行";
        return false;
    }
    V4Scratch warm;
    warm.carryEnabled = true;
    V4Scratch full;
    full.carryEnabled = false;
    std::map<std::string, std::vector<float>> out;
    // ① A 只预热**前半段**（`[0, half)`）：这很关键 —— 若 A 把整个文件都跑完，槽里的
    //    `seen` 停在文件末尾，B 从第 0 行开始时 `events < s.seen` ⇒ **陈旧退回**，旧键也照样
    //    重建（第一版就写成这样，负向控制红不了）。从 `half` 开始，B 的首个决策事件前缀
    //    恰好接得上 A 的 `seen` ⇒ 旧键下**真的会复用** A 的 carry。
    const size_t half = obs.size() / 2;
    if (half == 0) {
        err = "语料太短（<2 条决策），无法做换网判据";
        return false;
    }
    for (size_t i = 0; i < half; i++) {
        if (!a.forwardAll(obs[i], out, err, nullptr, &warm)) {
            return false;
        }
    }
    const V4CarryStats sa = v4CarryStatsOf(warm);
    // ② B 在 A 预热过的 scratch 上跑后半段：**增量 vs 整手重放逐位**。
    //    ⛔ 这才是判据的正确形态：如果槽键不含网络身份，B 的第一个决策会拿到 A 的 carry
    //    （token 行相同 ⇒ 前缀校验**过得去**），于是 B 的增量 h_evt 与它自己的整手重放不同
    //    ⇒ 逐位红。计数（hits/cold）在这里**不构成判据** —— 同一份语料下两张网的计数相同。
    v4CarryResetStats(warm);                      // 只清计数器，槽保留
    long long rowsDone = 0;
    long long badRows = 0;
    std::string firstBad;
    for (size_t i = half; i < obs.size(); i++) {
        std::map<std::string, std::vector<float>> oc, of;
        if (!b.forwardAll(obs[i], oc, err, nullptr, &warm)) {
            return false;
        }
        if (!b.forwardAll(obs[i], of, err, nullptr, &full)) {
            return false;
        }
        HeadDiff d;
        diffHeads(oc, of, d);
        rowsDone++;
        if (d.badHeads > 0) {
            badRows++;
            if (firstBad.empty()) {
                firstBad = "后半段第 " + std::to_string(rowsDone) + " 行 头 " + d.firstHead + "["
                        + std::to_string(d.firstIdx) + "] 增量=" + std::to_string(d.a)
                        + " 全量=" + std::to_string(d.b);
            }
        }
    }
    const V4CarryStats sb = v4CarryStatsOf(warm);
    char buf[768];
    std::snprintf(buf, sizeof(buf),
                  "swap-net：A 键 %s · B 键 %s\n"
                  "  A 预热：决策 %lld / 命中 %lld / 建槽 %lld（命中率 %.1f%%）\n"
                  "  B 在 A 预热过的槽上：决策 %lld / 命中 %lld / 建槽 %lld / 陈旧 %lld ·"
                  " 增量 vs 整手重放 不符 %lld 行\n",
                  a.cacheTag().c_str(), b.cacheTag().c_str(),
                  static_cast<long long>(sa.decisions), static_cast<long long>(sa.hits),
                  static_cast<long long>(sa.cold),
                  sa.decisions == 0 ? 0.0 : 100.0 * static_cast<double>(sa.hits)
                                              / static_cast<double>(sa.decisions),
                  static_cast<long long>(sb.decisions), static_cast<long long>(sb.hits),
                  static_cast<long long>(sb.cold), static_cast<long long>(sb.stale), badRows);
    report = buf;
    if (!firstBad.empty()) {
        report += "  首个不符：" + firstBad + "\n";
    }
    if (sa.decisions <= 1 || sa.hits == 0) {
        err = "阶段①（A 预热）缓存没热起来（hits=0）⇒ 这条判据是空转";
        return false;
    }
    if (sb.hits == 0) {
        err = "阶段②（B 自己）缓存一次都没命中 ⇒ 判据空转";
        return false;
    }
    if (badRows != 0) {
        err = "换网后 B 的增量 carry 与自己的整手重放不符 ⇒ 槽键没带网络身份"
              "（复跑请设 TRAINER_V4_OLD_KEY=1 看旧键下的红）";
        return false;
    }
    return true;
}

}  // namespace

int v4CacheCli(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr,
                     "用法：trainer v4cache <net.bin> <轨迹1.jsonl|目录> [更多…]\n"
                     "                 [--workers K] [--limit N] [--h0-rows N] [--swap-net <另一张网>]\n"
                     "  逐条真实决策行跑两条事件塔路径并**逐位**比对七个头 + `h_evt`：\n"
                     "    ① 增量 carry（缓存开）== 整手重放（缓存关）   ← 判据「增量 == 全量」\n"
                     "    ② `h_evt` 那一格同时就是「缓存 carry == 整手重放」（W1 起 h_evt 被融合消费）\n"
                     "    ③ 前 `--h0-rows`（缺省 2000）行额外判「前缀 carry + 窗口 == 整手重放」\n"
                     "  ⛔ 只有「相等」是假绿 ⇒ 命中率一起打印；命中率 0 ⇒ 退出码非 0。\n"
                     "  `--limit N` = 总条数上限（0/缺省 = 不限）；`--workers` 缺省 1（按文件切）。\n"
                     "  `--swap-net B` = **缓存键负向判据**：同一份 scratch 先跑 A、再跑 B、再跑 A，\n"
                     "    换网必须全 miss（槽键含 arch/format/权重 sha）。\n");
        return 2;
    }
    V4Policy net;
    std::string err;
    if (!v4LoadPolicy(argv[1], net, err)) {
        std::fprintf(stderr, "[trainer] %s\n", err.c_str());
        return 1;
    }
    std::vector<std::string> files;
    int workers = 1;
    long long limit = 0;
    long long h0Rows = 2000;
    std::string swapNet;
    int i = 2;
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
            workers = std::atoi(next("--workers"));
        } else if (a == "--limit") {
            limit = std::atoll(next("--limit"));
        } else if (a == "--h0-rows") {
            h0Rows = std::atoll(next("--h0-rows"));
        } else if (a == "--swap-net") {
            swapNet = next("--swap-net");
        } else if (!a.empty() && a[0] == '-') {
            std::fprintf(stderr, "[trainer] 未知参数：%s\n", a.c_str());
            return 2;
        } else {
            std::error_code ec;
            if (std::filesystem::is_directory(a, ec)) {
                std::filesystem::directory_iterator it(a, ec);
                if (ec) {
                    std::fprintf(stderr, "[trainer] 目录读不了：%s\n", a.c_str());
                    return 2;
                }
                for (const auto &ent : it) {
                    if (!ent.is_regular_file(ec)) {
                        continue;
                    }
                    const std::string name = ent.path().filename().string();
                    if (name.size() > 6 && name.rfind("g") == 0
                        && name.substr(name.size() - 6) == ".jsonl") {
                        files.push_back(ent.path().string());
                    }
                }
            } else {
                files.push_back(a);
            }
        }
    }
    if (files.empty()) {
        std::fprintf(stderr, "[trainer] 没有可查的 g*.jsonl\n");
        return 2;
    }
    std::sort(files.begin(), files.end());
    // ---- `--swap-net`：缓存键负向判据（跑完就返回，不走逐位对拍那套）
    if (!swapNet.empty()) {
        V4Policy net2;
        if (!v4LoadPolicy(swapNet, net2, err)) {
            std::fprintf(stderr, "[trainer] %s\n", err.c_str());
            return 1;
        }
        std::fprintf(stderr, "[trainer] swap-net：A=%s（%s）· B=%s（%s）\n", argv[1],
                     v4Describe(net).c_str(), swapNet.c_str(), v4Describe(net2).c_str());
        std::string report;
        if (!cacheSwapNetDemo(net, net2, files[0], report, err)) {
            std::printf("%s", report.c_str());
            std::fprintf(stderr, "[trainer] v4cache --swap-net FAIL：%s\n", err.c_str());
            return 1;
        }
        std::printf("%s", report.c_str());
        std::printf("v4cache --swap-net PASS（换网/换 arch ⇒ V4Cache 全 miss）\n");
        return 0;
    }
    const int w = std::max(1, std::min(workers, static_cast<int>(files.size())));
    std::fprintf(stderr, "[trainer] v4cache：%s（%s）· %d 个文件 · workers=%d · limit=%lld · h0-rows=%lld%s\n",
                 argv[1], v4Describe(net).c_str(), static_cast<int>(files.size()), w, limit, h0Rows,
                 v4CarryBugName()[0] == '\0' ? "" : "  ⚠ --v4-carry-bug 已打开（负向判据专用）");
    const auto t0 = std::chrono::steady_clock::now();
    CacheRun total;
    std::mutex mu;
    std::atomic<int> nextFile{0};
    std::atomic<long long> rowsSoFar{0};
    std::atomic<int> h0Used{0};
    std::vector<std::thread> pool;
    pool.reserve(static_cast<size_t>(w));
    for (int t = 0; t < w; t++) {
        pool.emplace_back([&]() {
            CacheRun local;
            int idx;
            while ((idx = nextFile.fetch_add(1)) < static_cast<int>(files.size())) {
                // ⚠ `left` 是**全局剩余**（不是线程本地）：`rowsSoFar` 只在整文件结束后更新 ⇒ 8 条线程
                //   会过冲一轮（最多 workers × 每文件条数）。⛔ 这里必须判 `<= 0` —— 写成 `== 0` 时
                //   一旦过冲成负数就会被当成"-1 = 不限"，于是**把整个目录跑完**（本轮实测踩到：
                //   `--limit 5000 --workers 8` 于 12,000 个文件的目录上跑了 10 分钟还没停）。
                //   ⚠ 传给 `oneFile` 的必须是**每文件上限**（文件内单独计数），不是"线程累计"。
                if (limit > 0 && rowsSoFar.load() >= limit) {
                    break;
                }
                const long long left = limit <= 0 ? -1 : (limit - rowsSoFar.load());
                const long long before = local.rows;
                int used = static_cast<int>(h0Used.load());
                std::string e2;
                const int budget = h0Rows <= 0 ? -1
                                               : std::max(0, static_cast<int>(h0Rows) - used);
                if (!oneFile(net, files[static_cast<size_t>(idx)],
                             left < 0 ? -1 : static_cast<int>(left), budget, used, local, e2)) {
                    std::lock_guard<std::mutex> lk(mu);
                    std::fprintf(stderr, "[trainer] %s\n", e2.c_str());
                    std::exit(1);
                }
                h0Used.store(used);
                rowsSoFar.fetch_add(local.rows - before);
            }
            std::lock_guard<std::mutex> lk(mu);
            total.rows += local.rows;
            total.badRows += local.badRows;
            total.badHeads += local.badHeads;
            total.badElems += local.badElems;
            total.h0Rows += local.h0Rows;
            total.h0BadRows += local.h0BadRows;
            total.eventsTotal += local.eventsTotal;
            total.grnRows += local.grnRows;
            total.secFull += local.secFull;
            total.secCached += local.secCached;
            total.st.decisions += local.st.decisions;
            total.st.hits += local.st.hits;
            total.st.cold += local.st.cold;
            total.st.stale += local.st.stale;
            total.st.rowsReplayed += local.st.rowsReplayed;
            total.st.rowsEncoded += local.st.rowsEncoded;
            total.st.rowsWindow += local.st.rowsWindow;
            if (total.firstBad.empty()) {
                total.firstBad = local.firstBad;
            }
        });
    }
    for (std::thread &th : pool) {
        th.join();
    }
    const double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    const double hitRate = total.st.decisions == 0
            ? 0.0
            : static_cast<double>(total.st.hits) / static_cast<double>(total.st.decisions);
    const double reuse = total.grnRows == 0
            ? 0.0
            : static_cast<double>(total.eventsTotal) / static_cast<double>(total.grnRows);
    std::printf("v4cache 决策行=%lld（逐位不符行 %lld / 不符头 %lld / 不符格 %lld）\n", total.rows,
                total.badRows, total.badHeads, total.badElems);
    std::printf("  命中 %lld / 建槽 %lld / 陈旧退回 %lld ⇒ 命中率 %.4f\n", total.st.hits,
                total.st.cold, total.st.stale, hitRate);
    std::printf("  喂进 GRU 的事件行：编码 %lld + 窗口 %lld = %lld（其中建槽重放 %lld，净增量 %lld）；"
                "整手重放需 %lld 行 ⇒ 少喂 %.2f×\n",
                total.st.rowsEncoded, total.st.rowsWindow, total.grnRows, total.st.rowsReplayed,
                total.grnRows - total.st.rowsReplayed, total.eventsTotal, reuse);
    std::printf("  判据③（前缀 carry + 窗口 == 整手重放）：%lld 行，不符 %lld 行\n", total.h0Rows,
                total.h0BadRows);
    std::printf("  单线程每决策：整手重放 %.4f ms / 增量 %.4f ms（含特征拼装）⇒ %+.1f%%\n",
                total.rows == 0 ? 0.0 : 1000.0 * total.secFull / static_cast<double>(total.rows),
                total.rows == 0 ? 0.0 : 1000.0 * total.secCached / static_cast<double>(total.rows),
                total.secFull <= 0 ? 0.0 : 100.0 * (total.secCached - total.secFull) / total.secFull);
    std::printf("  端到端（%d workers）%.2fs / %.0f 行每秒；取文件 %d/%d（`--limit` 是**软上限**："
                "全局余额只在整文件结束后刷新 ⇒ 最多过冲 workers × 每文件条数）\n",
                w, sec, sec <= 0 ? 0.0 : static_cast<double>(total.rows) / sec,
                std::min(nextFile.load(), static_cast<int>(files.size())),
                static_cast<int>(files.size()));
    if (!total.firstBad.empty()) {
        std::printf("  首个不符：%s\n", total.firstBad.c_str());
    }
    // ⛔ 假绿守卫：只有"相等"不够 —— 缓存从不命中时"增量 == 全量"永远成立。
    const bool hitOk = total.st.decisions <= 1 || total.st.hits > 0;
    const bool pass = total.rows > 0 && total.badRows == 0 && total.h0BadRows == 0 && hitOk;
    if (!hitOk) {
        std::printf("  ⛔ 命中 0（决策 %lld 条）：缓存**根本没被用上** ⇒ 这条绿色是空转，判 FAIL\n",
                    total.st.decisions);
    }
    std::printf("v4cache %s\n", pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}

}  // namespace trainer
