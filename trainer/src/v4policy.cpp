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
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <map>

#if defined(__AVX2__)
#include <immintrin.h>
#endif

#include "net.hpp"        // netArgmax（= Java `Logits.argmaxOf`）
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
 */
bool V4Policy::bindAll(std::string &err) {
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
    return err.empty();
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
    r.i32();                                              // derivedVersion（本前向用不到）
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
    if (fmt != kV4FormatVersion) {
        err = "权重格式版本 " + std::to_string(fmt) + " != 本训练端 "
              + std::to_string(kV4FormatVersion)
              + "（格式 1 是 v3 的定长 MLP，由 loadNet 加载）：" + what;
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
    const int32_t nBlocks = r.i32();
    const int32_t nTensors = r.i32();
    uint8_t fpB[16];
    r.bytes(fpB, 16);
    std::string fingerprint(reinterpret_cast<const char *>(fpB), 16);
    r.i32();                                              // params（日志用，不信它）

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

    // ---- 张量表（名字升序，但顺序不参与语义；逐条核对维度数）
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
        if (nd == 2) {
            V4Policy::Mat m;
            m.rows = shape[0];
            m.cols = shape[1];
            m.data = std::move(data);
            p.mats[name] = std::move(m);
        } else if (nd == 1) {
            p.vecs[name] = std::move(data);
        } else {
            err = "张量 " + name + " 是 " + std::to_string(nd) + " 维（只支持 1/2 维）";
            return false;
        }
    }
    if (r.remaining() != 0) {
        err = "权重文件尾部多了 " + std::to_string(r.remaining()) + " 字节（张量表与头部不符）";
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
    if (fmt != 1 && fmt != kV4FormatVersion) {
        err = "权重格式版本 " + std::to_string(fmt) + " 不认识（v3 = 1，v4 = 2）—— 重新导出权重："
              + path;
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
    for (const auto &kv : p.vecs) {
        total += static_cast<long long>(kv.second.size());
    }
    return total;
}

std::string v4Describe(const V4Policy &p) {
    return "V4Policy（" + std::to_string(v4ParamCount(p)) + " 参数，格式 "
           + std::to_string(kV4FormatVersion) + "，dims={d_model=" + std::to_string(p.dModel)
           + ", tile_d=" + std::to_string(p.tileD) + ", n_heads=" + std::to_string(p.nHeads)
           + ", value_bins=" + std::to_string(p.valueBins) + "}，块 "
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
         const std::string &prefix, int cin, int dm, float *out, V4Scratch &sc, std::string &err) {
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
    reluBuf(out, static_cast<size_t>(B) * static_cast<size_t>(dm));
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

/** `event.enc` 的两层（**末尾没有 ReLU** —— 与 `tile.enc` 不同，见 hpp 顶部纪律①）。 */
struct EventEnc {
    const V4Policy::Mat *w1 = nullptr;
    const std::vector<float> *b1 = nullptr;
    const V4Policy::Mat *w2 = nullptr;
    const std::vector<float> *b2 = nullptr;

    /** 绑定一次、逐行复用：逐行重放是热路径，没必要每行再查 4 次表 + 核形状。 */
    bool bind(const V4Policy &p, std::string &err) {
        const int dm = p.dModel;
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
bool encodeWindow(const V4Policy &p, const V4Tensors &t, const EventEnc &enc, V4Scratch &sc,
                  std::string &err) {
    if (sc.eRows.size() < static_cast<size_t>(kKEvt) * static_cast<size_t>(p.dModel)) {
        sc.eRows.resize(static_cast<size_t>(kKEvt) * static_cast<size_t>(p.dModel));
        sc.eTokens.resize(static_cast<size_t>(kKEvt) * static_cast<size_t>(p.dModel));
    }
    enc.rows(t.evt[0].data(), kKEvt, kCEvt, sc.eRows.data(), sc);
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
        if (!v4EventRow(*ev[static_cast<size_t>(i)], seat, sc.tok.data(), err)) {
            return false;
        }
        enc.row(sc.tok.data(), sc.rowTmp.data(), sc);
        gruStepBuf(sc.rowTmp.data(), sc.carry.data(), gru, dm, sc);
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

}  // namespace

bool V4Policy::forwardAll(const JVal &obs, std::map<std::string, std::vector<float>> &outAll,
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
    } else if (!eventTowerRound(*this, obs, t, sc, err)) {
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
    linearBatchFast(ht, tileD, t.tile[0].data(), kCTile, kKindCount, static_cast<size_t>(kCTile), *w1,
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
                 u + static_cast<size_t>(i) * dm, sc, err)) {
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
    const bool pass = feat.worst <= tol && headCmp.worst <= tol && argmaxOk == nCases
            && worstRed <= 1e-3f && worstH0 > 1e-4f;
    if (!pass) {
        feat.dump("特征侧");
        headCmp.dump("前向侧");
    }
    std::printf("golden cases=%d tol=%s 特征 maxΔ=%.3g 前向 maxΔ=%.3g argmax=%d/%d 红证 maxΔ=%.3g"
                " h0红证 maxΔ=%.3g → %s\n",
                nCases, javaPercentG(static_cast<double>(tol)).c_str(),
                static_cast<double>(feat.worst), static_cast<double>(headCmp.worst), argmaxOk,
                nCases, static_cast<double>(worstRed), static_cast<double>(worstH0),
                pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}

}  // namespace trainer
