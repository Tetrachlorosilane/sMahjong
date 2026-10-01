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
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>

#include "net.hpp"        // netArgmax（= Java `Logits.argmaxOf`）
#include "v4features.hpp"

namespace trainer {
namespace {

/** `LayerNorm` 的 eps（= Java `V4Policy.LN_EPS`，与 torch 缺省一致）。 */
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

/** 构造收尾：形状核对（`bindAll`）+ "有张量没人读"（= Java 构造器末尾那两件事）。 */
bool finishPolicy(V4Policy &p, std::string &err) {
    p.used.clear();
    if (!p.bindAll(err)) {
        return false;
    }
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
 * `out = W·x + b`（W 行主序 `[out][in]`，**单累加器顺序求和** —— 别改成并行规约）。
 *
 * <p>⚠ **别名安全**：`out` 与 `x` 是同一块内存时先复制一份（Java `linear` 的同名注意事项）。
 */
void linear(std::vector<float> &out, const float *x, size_t xn, const V4Policy::Mat &w,
            const std::vector<float> &b) {
    std::vector<float> copy;
    if (out.data() == x) {
        copy.assign(x, x + xn);
        x = copy.data();
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

float dot(const std::vector<float> &a, const std::vector<float> &b) {
    float s = 0.f;
    for (size_t i = 0; i < a.size(); i++) {
        s += a[i] * b[i];
    }
    return s;
}

void relu(std::vector<float> &x) {
    for (float &v : x) {
        if (v < 0.f) {
            v = 0.f;
        }
    }
}

void sigmoid(std::vector<float> &x) {
    for (float &v : x) {
        const double e = std::exp(-static_cast<double>(v));
        v = static_cast<float>(1.0 / (1.0 + e));
    }
}

void tanhVec(std::vector<float> &x) {
    for (float &v : x) {
        v = static_cast<float>(std::tanh(static_cast<double>(v)));
    }
}

/** 原地 LayerNorm（**有偏方差**，eps = 1e-5，与 torch 缺省一致）。 */
void layerNorm(std::vector<float> &x, const std::vector<float> &g, const std::vector<float> &b) {
    const int n = static_cast<int>(x.size());
    float mean = 0.f;
    for (float v : x) {
        mean += v / static_cast<float>(n);
    }
    float var = 0.f;
    for (float v : x) {
        const float d = v - mean;
        var += d * d / static_cast<float>(n);
    }
    const float inv = static_cast<float>(1.0 / std::sqrt(static_cast<double>(var + kLnEps)));
    for (int i = 0; i < n; i++) {
        x[static_cast<size_t>(i)]
                = (x[static_cast<size_t>(i)] - mean) * inv * g[static_cast<size_t>(i)]
                  + b[static_cast<size_t>(i)];
    }
}

/** 原地 softmax（减去最大值再 exp）。 */
void softmax(std::vector<float> &x) {
    float max = -std::numeric_limits<float>::infinity();
    for (float v : x) {
        if (v > max) {
            max = v;
        }
    }
    float sum = 0.f;
    for (float &v : x) {
        v = static_cast<float>(std::exp(static_cast<double>(v - max)));
        sum += v;
    }
    if (sum > 0.f) {
        for (float &v : x) {
            v /= sum;
        }
    }
}

float rowDot(const V4Policy::Mat &w, int rowIndex, const std::vector<float> &x) {
    float s = 0.f;
    const float *row = w.data.data() + static_cast<size_t>(rowIndex) * static_cast<size_t>(w.cols);
    for (size_t i = 0; i < x.size(); i++) {
        s += row[i] * x[i];
    }
    return s;
}

/**
 * GRU 一步（PyTorch `nn.GRUCell` 的 r/z/n 三段顺序）。
 *
 * <p>⚠ **返回新数组**，不复用 `h`：`gh` 要读**整条旧 h**（Java 原注释，错得很隐蔽）。
 */
std::vector<float> gruStep(const std::vector<float> &x, const std::vector<float> &h,
                           const V4Policy::Mat &wih, const V4Policy::Mat &whh,
                           const std::vector<float> &bih, const std::vector<float> &bhh) {
    const int d = static_cast<int>(x.size());
    std::vector<float> out(static_cast<size_t>(d), 0.f);
    for (int i = 0; i < d; i++) {
        const float ir = bih[static_cast<size_t>(i)] + rowDot(wih, i, x);
        const float iz = bih[static_cast<size_t>(d + i)] + rowDot(wih, d + i, x);
        const float in = bih[static_cast<size_t>(2 * d + i)] + rowDot(wih, 2 * d + i, x);
        const float hr = bhh[static_cast<size_t>(i)] + rowDot(whh, i, h);
        const float hz = bhh[static_cast<size_t>(d + i)] + rowDot(whh, d + i, h);
        const float hn = bhh[static_cast<size_t>(2 * d + i)] + rowDot(whh, 2 * d + i, h);
        const float r = static_cast<float>(
                1.0 / (1.0 + std::exp(-static_cast<double>(ir + hr))));
        const float z = static_cast<float>(
                1.0 / (1.0 + std::exp(-static_cast<double>(iz + hz))));
        const float nn = static_cast<float>(
                std::tanh(static_cast<double>(in + r * hn)));
        out[static_cast<size_t>(i)] = (1.f - z) * nn + z * h[static_cast<size_t>(i)];
    }
    return out;
}

/** `in_proj` 第 `rowOff..rowOff+d` 行（= Java `V4Policy.projRows`）。 */
void projRows(std::vector<float> &out, const float *x, size_t xn, const V4Policy::Mat &w,
              const std::vector<float> &b, int rowOff, int d) {
    for (int i = 0; i < d; i++) {
        float s = b[static_cast<size_t>(rowOff + i)];
        const float *row = w.data.data()
                + static_cast<size_t>(rowOff + i) * static_cast<size_t>(w.cols);
        for (size_t c = 0; c < xn; c++) {
            s += row[c] * x[c];
        }
        out[static_cast<size_t>(i)] = s;
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

    M("heads.policy.weight", 1, dm);
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

/** `Mlp`（= `Linear + ReLU + Linear + ReLU`，注意**末尾那个 ReLU**）。 */
bool mlp(const V4Policy &p, const float *x, size_t xn, const std::string &prefix, int cin, int dm,
         std::vector<float> &out, std::string &err) {
    const V4Policy::Mat *w0 = p.mat(prefix + ".0.weight", dm, cin, err);
    const std::vector<float> *b0 = p.vec(prefix + ".0.bias", dm, err);
    const V4Policy::Mat *w2 = p.mat(prefix + ".2.weight", dm, dm, err);
    const std::vector<float> *b2 = p.vec(prefix + ".2.bias", dm, err);
    if (!err.empty()) {
        return false;
    }
    std::vector<float> h(static_cast<size_t>(dm), 0.f);
    linear(h, x, xn, *w0, *b0);
    relu(h);
    out.assign(static_cast<size_t>(dm), 0.f);
    linear(out, h.data(), h.size(), *w2, *b2);
    relu(out);
    return true;
}

/** 多头注意力（单样本、无 mask）：`q` 对 `kv` 做注意力（= Java `V4Policy.mha`）。 */
bool mha(const V4Policy &p, const std::vector<std::vector<float>> &q,
         const std::vector<std::vector<float>> &kv, int d, const std::string &prefix,
         std::vector<std::vector<float>> &out, std::string &err) {
    const V4Policy::Mat *inW = p.mat(prefix + ".in_proj_weight", 3 * d, d, err);
    const std::vector<float> *inB = p.vec(prefix + ".in_proj_bias", 3 * d, err);
    const V4Policy::Mat *outW = p.mat(prefix + ".out_proj.weight", d, d, err);
    const std::vector<float> *outB = p.vec(prefix + ".out_proj.bias", d, err);
    if (!err.empty()) {
        return false;
    }
    const int lq = static_cast<int>(q.size());
    const int lk = static_cast<int>(kv.size());
    const int hd = d / p.nHeads;
    const float scale = static_cast<float>(1.0 / std::sqrt(static_cast<double>(hd)));
    std::vector<std::vector<float>> qp(static_cast<size_t>(lq), std::vector<float>(d, 0.f));
    std::vector<std::vector<float>> kp(static_cast<size_t>(lk), std::vector<float>(d, 0.f));
    std::vector<std::vector<float>> vp(static_cast<size_t>(lk), std::vector<float>(d, 0.f));
    for (int t = 0; t < lq; t++) {
        projRows(qp[static_cast<size_t>(t)], q[static_cast<size_t>(t)].data(),
                 q[static_cast<size_t>(t)].size(), *inW, *inB, 0, d);
    }
    for (int s = 0; s < lk; s++) {
        projRows(kp[static_cast<size_t>(s)], kv[static_cast<size_t>(s)].data(),
                 kv[static_cast<size_t>(s)].size(), *inW, *inB, d, d);
        projRows(vp[static_cast<size_t>(s)], kv[static_cast<size_t>(s)].data(),
                 kv[static_cast<size_t>(s)].size(), *inW, *inB, 2 * d, d);
    }
    std::vector<float> scores(static_cast<size_t>(lk), 0.f);
    std::vector<std::vector<float>> ctx(static_cast<size_t>(lq), std::vector<float>(d, 0.f));
    for (int t = 0; t < lq; t++) {
        for (int hh = 0; hh < p.nHeads; hh++) {
            const int base = hh * hd;
            for (int s = 0; s < lk; s++) {
                float sum = 0.f;
                for (int i = 0; i < hd; i++) {
                    sum += qp[static_cast<size_t>(t)][static_cast<size_t>(base + i)]
                           * kp[static_cast<size_t>(s)][static_cast<size_t>(base + i)];
                }
                scores[static_cast<size_t>(s)] = sum * scale;
            }
            softmax(scores);
            for (int i = 0; i < hd; i++) {
                float sum = 0.f;
                for (int s = 0; s < lk; s++) {
                    sum += scores[static_cast<size_t>(s)]
                           * vp[static_cast<size_t>(s)][static_cast<size_t>(base + i)];
                }
                ctx[static_cast<size_t>(t)][static_cast<size_t>(base + i)] = sum;
            }
        }
    }
    out.assign(static_cast<size_t>(lq), std::vector<float>(d, 0.f));
    for (int t = 0; t < lq; t++) {
        linear(out[static_cast<size_t>(t)], ctx[static_cast<size_t>(t)].data(),
               static_cast<size_t>(d), *outW, *outB);
    }
    return true;
}

/**
 * `nn.TransformerEncoderLayer(d, nhead, dim_feedforward=2d, dropout=0, norm_first=True)` 一层
 * （= Java `V4Policy.transformerLayer`）：`x = x + attn(norm1(x))`，再 `x = x + linear2(relu(linear1(norm2(x))))`。
 */
bool transformerLayer(const V4Policy &p, const std::vector<std::vector<float>> &x,
                      const std::string &prefix, std::vector<std::vector<float>> &out,
                      std::string &err) {
    const int len = static_cast<int>(x.size());
    const int d = p.dModel;
    const std::vector<float> *n1g = p.vec(prefix + ".norm1.weight", d, err);
    const std::vector<float> *n1b = p.vec(prefix + ".norm1.bias", d, err);
    if (!err.empty()) {
        return false;
    }
    std::vector<std::vector<float>> xn(static_cast<size_t>(len), std::vector<float>(d, 0.f));
    for (int i = 0; i < len; i++) {
        xn[static_cast<size_t>(i)] = x[static_cast<size_t>(i)];
        layerNorm(xn[static_cast<size_t>(i)], *n1g, *n1b);
    }
    std::vector<std::vector<float>> att;
    if (!mha(p, xn, xn, d, prefix + ".self_attn", att, err)) {
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
    out.assign(static_cast<size_t>(len), std::vector<float>(d, 0.f));
    for (int i = 0; i < len; i++) {
        std::vector<float> y(static_cast<size_t>(d), 0.f);
        for (int j = 0; j < d; j++) {
            y[static_cast<size_t>(j)] = x[static_cast<size_t>(i)][static_cast<size_t>(j)]
                    + att[static_cast<size_t>(i)][static_cast<size_t>(j)];
        }
        std::vector<float> y2 = y;
        layerNorm(y2, *n2g, *n2b);
        std::vector<float> hid(static_cast<size_t>(2 * d), 0.f);
        linear(hid, y2.data(), y2.size(), *f1W, *f1B);
        relu(hid);
        std::vector<float> res(static_cast<size_t>(d), 0.f);
        linear(res, hid.data(), hid.size(), *f2W, *f2B);
        for (int j = 0; j < d; j++) {
            out[static_cast<size_t>(i)][static_cast<size_t>(j)]
                    = y[static_cast<size_t>(j)] + res[static_cast<size_t>(j)];
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

void flatten(const std::vector<std::vector<float>> &rows, int cols, std::vector<float> &out) {
    out.assign(rows.size() * static_cast<size_t>(cols), 0.f);
    for (size_t i = 0; i < rows.size(); i++) {
        for (int c = 0; c < cols; c++) {
            out[i * static_cast<size_t>(cols) + static_cast<size_t>(c)]
                    = rows[i][static_cast<size_t>(c)];
        }
    }
}

}  // namespace

bool V4Policy::forwardAll(const JVal &obs, std::map<std::string, std::vector<float>> &outAll,
                          std::string &err) const {
    err.clear();
    V4Tensors t;
    if (!v4Assemble(obs, missingBlocks, t, err)) {
        return false;
    }
    const int n = static_cast<int>(t.cand.size());
    const int dm = dModel;

    // ---- 牌种塔
    std::vector<std::vector<float>> ht(kKindCount, std::vector<float>(tileD, 0.f));
    const Mat *w1 = mat("tile.enc.0.weight", tileD, kCTile, err);
    const std::vector<float> *b1 = vec("tile.enc.0.bias", tileD, err);
    const Mat *w2 = mat("tile.enc.2.weight", tileD, tileD, err);
    const std::vector<float> *b2 = vec("tile.enc.2.bias", tileD, err);
    if (!err.empty()) {
        return false;
    }
    for (int k = 0; k < kKindCount; k++) {
        linear(ht[static_cast<size_t>(k)], t.tile[static_cast<size_t>(k)].data(), kCTile, *w1, *b1);
        relu(ht[static_cast<size_t>(k)]);
        linear(ht[static_cast<size_t>(k)], ht[static_cast<size_t>(k)].data(),
               static_cast<size_t>(tileD), *w2, *b2);
        relu(ht[static_cast<size_t>(k)]);
    }
    std::vector<std::vector<float>> at;
    if (!mha(*this, ht, ht, tileD, "tile.attn", at, err)) {
        return false;
    }
    const std::vector<float> *normG = vec("tile.norm.weight", tileD, err);
    const std::vector<float> *normB = vec("tile.norm.bias", tileD, err);
    if (!err.empty()) {
        return false;
    }
    std::vector<float> pool(static_cast<size_t>(tileD), 0.f);
    for (int k = 0; k < kKindCount; k++) {
        for (int i = 0; i < tileD; i++) {
            ht[static_cast<size_t>(k)][static_cast<size_t>(i)]
                    += at[static_cast<size_t>(k)][static_cast<size_t>(i)];
        }
        layerNorm(ht[static_cast<size_t>(k)], *normG, *normB);
        for (int i = 0; i < tileD; i++) {
            pool[static_cast<size_t>(i)]
                    += ht[static_cast<size_t>(k)][static_cast<size_t>(i)]
                       / static_cast<float>(ht.size());          // mean over tokens
        }
    }
    std::vector<float> hTilePool(static_cast<size_t>(dm), 0.f);
    {
        const Mat *tpW = mat("tile.proj.weight", dm, tileD, err);
        const std::vector<float> *tpB = vec("tile.proj.bias", dm, err);
        if (!err.empty()) {
            return false;
        }
        linear(hTilePool, pool.data(), pool.size(), *tpW, *tpB);
    }

    // ---- 事件塔
    std::vector<std::vector<float>> e(kKEvt, std::vector<float>(static_cast<size_t>(dm), 0.f));
    const Mat *ew1 = mat("event.enc.0.weight", dm, kCEvt, err);
    const std::vector<float> *eb1 = vec("event.enc.0.bias", dm, err);
    const Mat *ew2 = mat("event.enc.2.weight", dm, dm, err);
    const std::vector<float> *eb2 = vec("event.enc.2.bias", dm, err);
    if (!err.empty()) {
        return false;
    }
    for (int i = 0; i < kKEvt; i++) {
        linear(e[static_cast<size_t>(i)], t.evt[static_cast<size_t>(i)].data(), kCEvt, *ew1, *eb1);
        relu(e[static_cast<size_t>(i)]);
        // ⚠ 这里**没有** ReLU（与训练一致）：`event.enc` 的第二个线性层之后不接激活
        linear(e[static_cast<size_t>(i)], e[static_cast<size_t>(i)].data(),
               static_cast<size_t>(dm), *ew2, *eb2);
    }
    const Mat *gih = mat("event.cell.weight_ih", 3 * dm, dm, err);
    const Mat *ghh = mat("event.cell.weight_hh", 3 * dm, dm, err);
    const std::vector<float> *gbi = vec("event.cell.bias_ih", 3 * dm, err);
    const std::vector<float> *gbh = vec("event.cell.bias_hh", 3 * dm, err);
    if (!err.empty()) {
        return false;
    }
    std::vector<float> h(static_cast<size_t>(dm), 0.f);          // 冷启动 h=0，喂满 K 个 token
    for (int i = 0; i < kKEvt; i++) {
        h = gruStep(e[static_cast<size_t>(i)], h, *gih, *ghh, *gbi, *gbh);
    }
    std::vector<std::vector<float>> x2;
    if (!transformerLayer(*this, e, "event.tr.layers.0", x2, err)) {
        return false;
    }
    const std::vector<std::vector<float>> &eTokens = x2;

    // ---- ctx / cand 编码器
    std::vector<float> ctxEmb;
    if (!mlp(*this, t.ctx.data(), t.ctx.size(), "ctx.net", kCCtx, dm, ctxEmb, err)) {
        return false;
    }
    std::vector<std::vector<float>> candEmb(static_cast<size_t>(n));
    for (int i = 0; i < n; i++) {
        if (!mlp(*this, t.cand[static_cast<size_t>(i)].data(), kCCand, "cand.net", kCCand, dm,
                 candEmb[static_cast<size_t>(i)], err)) {
            return false;
        }
    }

    // ---- 融合
    std::vector<std::vector<float>> kvTile(kKindCount, std::vector<float>(static_cast<size_t>(dm), 0.f));
    {
        const Mat *tpW = mat("fusion.tile_proj.weight", dm, tileD, err);
        const std::vector<float> *tpB = vec("fusion.tile_proj.bias", dm, err);
        if (!err.empty()) {
            return false;
        }
        for (int k = 0; k < kKindCount; k++) {
            linear(kvTile[static_cast<size_t>(k)], ht[static_cast<size_t>(k)].data(),
                   static_cast<size_t>(tileD), *tpW, *tpB);
        }
    }
    std::vector<std::vector<float>> uTile;
    std::vector<std::vector<float>> uEvt;
    if (!mha(*this, candEmb, kvTile, dm, "fusion.q_tile", uTile, err)) {
        return false;
    }
    if (!mha(*this, candEmb, eTokens, dm, "fusion.q_evt", uEvt, err)) {
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
    std::vector<std::vector<float>> u(static_cast<size_t>(n),
                                      std::vector<float>(static_cast<size_t>(dm), 0.f));
    std::vector<float> cat(static_cast<size_t>(2 * dm), 0.f);
    std::vector<float> state(static_cast<size_t>(dm), 0.f);
    std::vector<float> cat3(static_cast<size_t>(3 * dm), 0.f);
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < dm; j++) {
            cat[static_cast<size_t>(j)] = uEvt[static_cast<size_t>(i)][static_cast<size_t>(j)];
            cat[static_cast<size_t>(dm + j)]
                    = uTile[static_cast<size_t>(i)][static_cast<size_t>(j)];
        }
        std::vector<float> g(static_cast<size_t>(dm), 0.f);
        linear(g, cat.data(), cat.size(), *gateW, *gateB);
        sigmoid(g);
        std::vector<float> w(static_cast<size_t>(dm), 0.f);
        linear(w, uEvt[static_cast<size_t>(i)].data(), static_cast<size_t>(dm), *writeW, *writeB);
        for (int j = 0; j < dm; j++) {
            state[static_cast<size_t>(j)] = hTilePool[static_cast<size_t>(j)]
                    + g[static_cast<size_t>(j)] * w[static_cast<size_t>(j)];
        }
        std::vector<float> f(static_cast<size_t>(dm), 0.f);
        linear(f, state.data(), state.size(), *filmW, *filmB);
        tanhVec(f);
        for (int j = 0; j < dm; j++) {
            f[static_cast<size_t>(j)] = uEvt[static_cast<size_t>(i)][static_cast<size_t>(j)]
                    * (1.f + f[static_cast<size_t>(j)]);
        }
        for (int j = 0; j < dm; j++) {
            cat3[static_cast<size_t>(j)] = uTile[static_cast<size_t>(i)][static_cast<size_t>(j)];
            cat3[static_cast<size_t>(dm + j)] = f[static_cast<size_t>(j)];
            cat3[static_cast<size_t>(2 * dm + j)] = ctxEmb[static_cast<size_t>(j)];
        }
        if (!mlp(*this, cat3.data(), cat3.size(), "fusion.out.net", 3 * dm, dm,
                 u[static_cast<size_t>(i)], err)) {
            return false;
        }
    }

    // ---- 头
    std::vector<float> policy(static_cast<size_t>(n), 0.f);
    std::vector<std::vector<float>> danger(static_cast<size_t>(n), std::vector<float>(4, 0.f));
    std::vector<std::vector<float>> effect(static_cast<size_t>(n), std::vector<float>(3, 0.f));
    // ⚠ **policy 的输入是 `[u ; sigmoid(belief_tenpai)]`（宽度 dm+3）**（2026-09-30 第四十九轮）：
    //   与 Java `V4Policy` / Python `model.py` 的 `Heads.forward` **逐位同序**（u 在前、3 个概率在后）。
    //   为什么：实测 `belief_tenpai` 的 AUC 0.978，但它原先与 policy 不互通（各头各算、算完即丢）。
    //   ⇒ bt 必须在 policy 之前算（先 meanU，再 bt，最后 policy）。
    const Mat *polW = mat("heads.policy.weight", 1, dm + 3, err);
    const std::vector<float> *polB = vec("heads.policy.bias", 1, err);
    const Mat *danW = mat("heads.danger.weight", 4, dm, err);
    const std::vector<float> *danB = vec("heads.danger.bias", 4, err);
    const Mat *effW = mat("heads.effect.weight", 3, dm, err);
    const std::vector<float> *effB = vec("heads.effect.bias", 3, err);
    if (!err.empty()) {
        return false;
    }
    std::vector<float> meanU(static_cast<size_t>(dm), 0.f);
    const float polBias = (*polB)[0];
    for (int i = 0; i < n; i++) {
        linear(danger[static_cast<size_t>(i)], u[static_cast<size_t>(i)].data(),
               static_cast<size_t>(dm), *danW, *danB);
        linear(effect[static_cast<size_t>(i)], u[static_cast<size_t>(i)].data(),
               static_cast<size_t>(dm), *effW, *effB);
        const float denom = static_cast<float>(std::max(1, n));
        for (int j = 0; j < dm; j++) {
            meanU[static_cast<size_t>(j)]
                    += u[static_cast<size_t>(i)][static_cast<size_t>(j)] / denom;
        }
    }
    // n == 0 时 meanU 保持全 0（Java 显式重赋值一次，效果相同）

    // 先算 `belief_tenpai` 的**概率**（只喂 policy，见上面的注释），再逐候选出 policy。
    // ⚠ 输出的 `belief_tenpai` 仍然是 **logits**（下面 `head(...)` 重新算一遍）—— 别搞混。
    std::vector<float> btProb(3, 0.f);
    {
        std::vector<float> btLogits;
        if (!head(*this, meanU, "belief_tenpai", 3, btLogits, err)) {
            return false;
        }
        for (int j = 0; j < 3; j++) {
            btProb[static_cast<size_t>(j)]
                    = 1.f / (1.f + std::exp(-btLogits[static_cast<size_t>(j)]));
        }
    }
    std::vector<float> polVec(static_cast<size_t>(dm) + 3, 0.f);
    for (int i = 0; i < n; i++) {
        std::copy(u[static_cast<size_t>(i)].begin(), u[static_cast<size_t>(i)].end(),
                  polVec.begin());
        polVec[static_cast<size_t>(dm)] = btProb[0];
        polVec[static_cast<size_t>(dm) + 1] = btProb[1];
        polVec[static_cast<size_t>(dm) + 2] = btProb[2];
        policy[static_cast<size_t>(i)] = polBias + dot(polW->data, polVec);
    }

    outAll.clear();
    outAll["policy"] = std::move(policy);
    std::vector<float> value;
    if (!head(*this, meanU, "value", valueBins, value, err)) {
        return false;
    }
    outAll["value"] = std::move(value);
    std::vector<float> placement;
    if (!head(*this, meanU, "placement", 4, placement, err)) {
        return false;
    }
    outAll["placement"] = std::move(placement);
    std::vector<float> beliefHand;
    if (!head(*this, meanU, "belief_hand", 3 * kKindCount, beliefHand, err)) {
        return false;
    }
    outAll["belief_hand"] = std::move(beliefHand);
    std::vector<float> beliefTenpai;
    if (!head(*this, meanU, "belief_tenpai", 3, beliefTenpai, err)) {
        return false;
    }
    outAll["belief_tenpai"] = std::move(beliefTenpai);
    std::vector<float> dangerFlat;
    flatten(danger, 4, dangerFlat);
    outAll["danger"] = std::move(dangerFlat);
    std::vector<float> effectFlat;
    flatten(effect, 3, effectFlat);
    outAll["effect"] = std::move(effectFlat);
    return true;
}

bool V4Policy::logits(const JVal &obs, std::vector<float> &out, std::string &err) const {
    std::map<std::string, std::vector<float>> all;
    if (!forwardAll(obs, all, err)) {
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

bool v4Logits(const V4Policy &p, const JVal &obs, std::vector<float> &out, std::string &err) {
    return p.logits(obs, out, err);
}

bool v4ForwardAll(const V4Policy &p, const JVal &obs,
                  std::map<std::string, std::vector<float>> &out, std::string &err) {
    return p.forwardAll(obs, out, err);
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
    r.i32();                                                  // 夹具格式号
    const int32_t nCases = r.i32();
    const int32_t netLen = r.i32();
    if (magic != kGoldenMagic) {
        std::fprintf(stderr, "[trainer] 夹具魔数不对：%#x（期望 %#x）\n",
                     static_cast<unsigned>(magic), kGoldenMagic);
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
        std::vector<float> tile(static_cast<size_t>(kKindCount) * kCTile);
        std::vector<float> evt(static_cast<size_t>(kKEvt) * kCEvt);
        std::vector<float> ctx(kCCtx);
        std::vector<float> cand(static_cast<size_t>(n) * kCCand);
        std::vector<float> logits(n);
        std::vector<float> value(static_cast<size_t>(vb));
        std::vector<float> belief(3);
        std::vector<float> danger(static_cast<size_t>(n) * 4);
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
        if (r.bad) {
            std::fprintf(stderr, "[trainer] 夹具在第 %d 个用例处被截断\n", c);
            return 1;
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
        if (!v4ForwardAll(net, obs, o, err)) {
            std::fprintf(stderr, "[trainer] 夹具第 %d 个用例：前向失败：%s\n", c, err.c_str());
            return 1;
        }
        if (o["policy"].size() != static_cast<size_t>(n)
                || o["value"].size() != static_cast<size_t>(vb)) {
            std::fprintf(stderr, "[trainer] 夹具第 %d 个用例：前向输出宽度与夹具不符"
                                 "（policy %zu != %u，value %zu != %d）\n",
                         c, o["policy"].size(), static_cast<unsigned>(n), o["value"].size(), vb);
            return 1;
        }
        cmpVec(headCmp, tag + "policy", o["policy"].data(), logits.data(), n, "i");
        cmpVec(headCmp, tag + "value", o["value"].data(), value.data(), vb, "i");
        cmpVec(headCmp, tag + "belief_tenpai", o["belief_tenpai"].data(), belief.data(), 3, "i");
        cmpMat(headCmp, tag + "danger", o["danger"].data(), danger.data(), n, 4, "row", "col");
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
    const bool pass = feat.worst <= tol && headCmp.worst <= tol && argmaxOk == nCases
            && worstRed <= 1e-3f;
    if (!pass) {
        feat.dump("特征侧");
        headCmp.dump("前向侧");
    }
    std::printf("golden cases=%d tol=%s 特征 maxΔ=%.3g 前向 maxΔ=%.3g argmax=%d/%d 红证 maxΔ=%.3g"
                " → %s\n",
                nCases, javaPercentG(static_cast<double>(tol)).c_str(),
                static_cast<double>(feat.worst), static_cast<double>(headCmp.worst), argmaxOk,
                nCases, static_cast<double>(worstRed), pass ? "PASS" : "FAIL");
    return pass ? 0 : 1;
}

}  // namespace trainer
