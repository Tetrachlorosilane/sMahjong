// 网络前向与"观测 → 网络输入"的拼装 —— 逐句移植 Java `mahjong.ai.Features` + `NeuralPolicy`。
//
// 纪律（与 `docs/TRAINER-CPP.md` §2 的三条铁律同源）：
//   · **Java 是权威**：段顺序、归一化系数、one-hot 槽位、累加顺序、`ReLU` 的写法都不许"顺手优化"；
//   · **全程 float**（Java 也是 float）：拼装里除了 `meldTiles/river/dora` 那几处 ×0.25 之外
//     没有浮点求和顺序问题（那些求和都是整数量级），真正敏感的是前向的点积累加 ——
//     编译加了 `-ffp-contract=off`（禁 FMA），否则最后一位舍入会变、argmax 会翻。
//   · 认不出的输入**报错**，不猜（`netLogits` 用 `err` 报出来；调用方决定是"退出"还是"这手算失败"）。
#include "net.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>

#include "action.hpp"
#include "java_rand.hpp"
#include "obffeatures.hpp"
#include "tiles.hpp"

namespace trainer {
namespace {

constexpr float kDerivedDangerScale = 100.f;
/** 与 Python `DERIVED_SCALE_CAND` / Java `Features.DERIVED_CAND_SCALE` 逐位一致。 */
constexpr float kDerivedCandScale[8] = {8.f, 34.f, 136.f, 34.f, 136.f, 34.f, 136.f, 4.f};
constexpr const char *kActionTypes[9] = {"discard", "riichi", "pon", "chi", "kan",
                                         "tsumo", "ron", "pass", "kyuushu"};
constexpr const char *kKanKinds[3] = {"ankan", "kakan", "daiminkan"};
constexpr const char *kMeldKinds[5] = {"chi", "pon", "ankan", "kakan", "daiminkan"};
constexpr const char *kWinNotes[2] = {"furiten", "no_yaku"};
constexpr int kTileSlots = 37;
constexpr const char *kWindNames = "ESWN";

int indexOfArr(const char *const *arr, int n, const std::string &s) {
    for (int i = 0; i < n; i++) {
        if (s == arr[i]) {
            return i;
        }
    }
    return -1;
}

bool hasKey(const std::string &key, const char *suffix) {
    const size_t n = std::strlen(suffix);
    return key.size() >= n && key.compare(key.size() - n, n, suffix) == 0;
}

/** Java `Features.boolAt(List, i)`：越界/非布尔 → false。 */
bool boolAt(const JVal *arr, int i) {
    return arr != nullptr && arr->isArr() && i < static_cast<int>(arr->arr.size())
            && arr->arr[i].type == JVal::Type::Bool && arr->arr[i].boolVal;
}

/** Java `ObsFeatures.ints(Object, n)`：非数组/越界 → `def`。 */
int intAt(const JVal *arr, int i, int def = 0) {
    if (arr == nullptr || !arr->isArr() || i >= static_cast<int>(arr->arr.size())) {
        return def;
    }
    const JVal &v = arr->arr[i];
    return v.type == JVal::Type::Num ? static_cast<int>(v.intVal) : def;
}

/** Java `Features.numAt(List, i, def)`（用于 `scores`）—— 我们的 JSON 里数字都是整数。 */
float numAt(const JVal *arr, int i, float def) {
    if (arr == nullptr || !arr->isArr() || i >= static_cast<int>(arr->arr.size())) {
        return def;
    }
    const JVal &v = arr->arr[i];
    return v.type == JVal::Type::Num ? static_cast<float>(v.intVal) : def;
}

bool boolField(const JVal &o, const char *k, bool def) {
    const JVal *v = o.find(k);
    if (v == nullptr || v->type != JVal::Type::Bool) {
        return def;
    }
    return v->boolVal;
}

int intField(const JVal &o, const char *k, int def) {
    const JVal *v = o.find(k);
    return v == nullptr ? def : v->asInt(def);
}

std::string strField(const JVal &o, const char *k, const char *def) {
    const JVal *v = o.find(k);
    if (v == nullptr || !v->isStr()) {
        return def;
    }
    return v->strVal;
}

/** Java `Features.str(Object)`：**非空**字符串才算"有"（空串 = null）。 */
const std::string *nonEmptyStr(const JVal *v) {
    return (v != nullptr && v->isStr() && !v->strVal.empty()) ? &v->strVal : nullptr;
}

/** Java `Features.windIndex(bakaze)`：`"ESWN"` 的**首字符**、大小写不敏感、钳到 0..3。 */
int windIndexOf(const std::string &bakaze) {
    int idx = -1;
    if (!bakaze.empty()) {
        char c = bakaze[0];
        if (c >= 'a' && c <= 'z') {
            c = static_cast<char>(c - 'a' + 'A');
        }
        for (int i = 0; i < 4; i++) {
            if (kWindNames[i] == c) {
                idx = i;
                break;
            }
        }
    }
    const int clamped = idx < 0 ? 0 : idx;
    return std::max(0, std::min(3, clamped));
}

/** 牌码 → 槽位（0..36；赤五占 34/35/36）= Java `Features.tileSlot`。 */
int tileSlotOf(const std::string &code) {
    const int k = parseKind(code);
    if (k < 0) {
        return -1;
    }
    if (code.size() == 2 && code[0] == '0') {
        return 34 + k / 9;
    }
    return k;
}

// ------------------------------------------------------------------ state（607）

/** Java `Features.state(obs, view)`。**行序、系数、槽位都不许动。** */
std::vector<float> stateOf(const JVal &obs, const FeatureView &v) {
    std::vector<float> f(kNetState, 0.f);
    int i = 0;
    const int seat = intField(obs, "seat", 0);

    const JVal *hand = obs.find("hand");
    for (int k = 0; k < kKindCount; k++) {
        f[static_cast<size_t>(i++)] = static_cast<float>(intAt(hand, k)) * 0.25f;
    }
    const JVal *handRed = obs.find("hand_red");
    for (int k = 0; k < kKindCount; k++) {
        f[static_cast<size_t>(i++)] = boolAt(handRed, k) ? 1.f : 0.f;
    }

    // 四家副露：种类计数（不缩放）+ 牌种计数（×0.25）
    std::vector<float> meldKind(4 * 5, 0.f);
    std::vector<float> meldTiles(4 * kKindCount, 0.f);
    const JVal *melds = obs.find("melds");
    if (melds != nullptr && melds->isArr()) {
        for (int s = 0; s < 4 && s < static_cast<int>(melds->arr.size()); s++) {
            const JVal &row = melds->arr[static_cast<size_t>(s)];
            if (!row.isArr()) {
                continue;
            }
            for (const JVal &mo : row.arr) {
                if (!mo.isObj()) {
                    continue;
                }
                const int ki = indexOfArr(kMeldKinds, 5, strField(mo, "kind", ""));
                if (ki >= 0) {
                    meldKind[static_cast<size_t>(s * 5 + ki)] += 1.f;
                }
                const JVal *tiles = mo.find("tiles");
                if (tiles == nullptr || !tiles->isArr()) {
                    continue;
                }
                for (const JVal &code : tiles->arr) {
                    const int k = parseKind(code.asStr());
                    if (k >= 0) {
                        meldTiles[static_cast<size_t>(s * kKindCount + k)] += 1.f;
                    }
                }
            }
        }
    }
    for (float x : meldKind) {
        f[static_cast<size_t>(i++)] = x;
    }
    for (float &x : meldTiles) {
        x *= 0.25f;
    }
    for (float x : meldTiles) {
        f[static_cast<size_t>(i++)] = x;
    }

    std::vector<float> river(4 * kKindCount, 0.f);
    const JVal *discards = obs.find("discards");
    if (discards != nullptr && discards->isArr()) {
        for (int s = 0; s < 4 && s < static_cast<int>(discards->arr.size()); s++) {
            const JVal &row = discards->arr[static_cast<size_t>(s)];
            if (!row.isArr()) {
                continue;
            }
            for (const JVal &code : row.arr) {
                const int k = parseKind(code.asStr());
                if (k >= 0) {
                    river[static_cast<size_t>(s * kKindCount + k)] += 1.f;
                }
            }
        }
    }
    for (float &x : river) {
        x *= 0.25f;
    }
    for (float x : river) {
        f[static_cast<size_t>(i++)] = x;
    }

    std::vector<float> dora(kKindCount, 0.f);
    const JVal *doraInd = obs.find("dora_indicators");
    if (doraInd != nullptr && doraInd->isArr()) {
        for (const JVal &code : doraInd->arr) {
            const int k = parseKind(code.asStr());
            if (k >= 0) {
                dora[static_cast<size_t>(k)] += 0.25f;
            }
        }
    }
    for (float x : dora) {
        f[static_cast<size_t>(i++)] = x;
    }

    const JVal *riichi = obs.find("riichi");
    for (int s = 0; s < 4; s++) {
        f[static_cast<size_t>(i++)] = boolAt(riichi, s) ? 1.f : 0.f;
    }
    const JVal *ippatsu = obs.find("ippatsu");
    for (int s = 0; s < 4; s++) {
        f[static_cast<size_t>(i++)] = boolAt(ippatsu, s) ? 1.f : 0.f;
    }
    const JVal *scores = obs.find("scores");
    for (int s = 0; s < 4; s++) {
        f[static_cast<size_t>(i++)] = (numAt(scores, s, 25000.f) - 25000.f) / 1000.f;
    }

    const JVal *rndPtr = obs.find("round");
    const bool hasRound = rndPtr != nullptr && rndPtr->isObj();
    const JVal &rnd = hasRound ? *rndPtr : JVal{};
    f[static_cast<size_t>(i++)] = static_cast<float>(
            windIndexOf(strField(rnd, "bakaze", "E"))) / 4.f;
    f[static_cast<size_t>(i++)] = static_cast<float>(intField(rnd, "kyoku", 1)) / 4.f;
    f[static_cast<size_t>(i++)] = static_cast<float>(intField(rnd, "honba", 0)) / 10.f;
    f[static_cast<size_t>(i++)] = static_cast<float>(
            (intField(rnd, "dealer", 0) - seat + 4) % 4) / 3.f;
    f[static_cast<size_t>(i++)] = static_cast<float>(intField(rnd, "riichi_sticks", 0)) / 4.f;

    f[static_cast<size_t>(i++)] = static_cast<float>(intField(obs, "tiles_left", 0)) / 70.f;
    f[static_cast<size_t>(i++)] = static_cast<float>(intField(obs, "dead_wall_left", 0)) / 4.f;
    f[static_cast<size_t>(i++)] = static_cast<float>(intField(obs, "total_discards", 0)) / 72.f;
    f[static_cast<size_t>(i++)] = static_cast<float>(intField(obs, "kan_count", 0)) / 4.f;
    f[static_cast<size_t>(i++)] = boolField(obs, "any_call", false) ? 1.f : 0.f;

    f[static_cast<size_t>(i++)] = static_cast<float>(intField(obs, "player_draws", 0)) / 18.f;
    f[static_cast<size_t>(i++)] = boolField(obs, "menzen", false) ? 1.f : 0.f;
    f[static_cast<size_t>(i++)] = boolField(obs, "self_riichi", false) ? 1.f : 0.f;
    f[static_cast<size_t>(i++)] = boolField(obs, "furiten", false) ? 1.f : 0.f;

    const std::string kind = strField(obs, "kind", "turn");
    f[static_cast<size_t>(i++)] = kind == "turn" ? 1.f : 0.f;
    f[static_cast<size_t>(i++)] = boolField(obs, "haitei", false) ? 1.f : 0.f;
    f[static_cast<size_t>(i++)] = boolField(obs, "houtei", false) ? 1.f : 0.f;
    f[static_cast<size_t>(i++)] = boolField(obs, "rinshan", false) ? 1.f : 0.f;

    const JVal *visible = obs.find("visible");
    for (int k = 0; k < kKindCount; k++) {
        f[static_cast<size_t>(i++)] = static_cast<float>(intAt(visible, k)) * 0.25f;
    }

    // drawn / called_tile：各占 37 个槽位（赤五占 34/35/36）
    {
        const std::string *s = nonEmptyStr(obs.find("drawn"));
        if (s != nullptr) {
            const int slot = tileSlotOf(*s);
            if (slot >= 0) {
                f[static_cast<size_t>(i + slot)] = 1.f;
            }
        }
        i += kTileSlots;
    }
    {
        const std::string *s = nonEmptyStr(obs.find("called_tile"));
        if (s != nullptr) {
            const int slot = tileSlotOf(*s);
            if (slot >= 0) {
                f[static_cast<size_t>(i + slot)] = 1.f;
            }
        }
        i += kTileSlots;
    }

    // from：**只有 claim 询问**才写（自家回合的 `from` 恒 -1）
    if (kind == "claim") {
        const int from = intField(obs, "from", -1);
        if (from >= 0) {
            f[static_cast<size_t>(i + ((from - seat + 4) % 4))] = 1.f;
        }
    }
    i += 4;

    // win_note：furiten / no_yaku / 都没有（第 3 槽）
    {
        const std::string *wn = nonEmptyStr(obs.find("win_note"));
        const int idx = wn == nullptr ? -1 : indexOfArr(kWinNotes, 2, *wn);
        f[static_cast<size_t>(i + (idx >= 0 ? idx : 2))] = 1.f;
    }
    i += 3;

    const std::array<int, kPerDecision> dec = perDecision(v);
    for (int x : dec) {
        f[static_cast<size_t>(i++)] = static_cast<float>(x) / kDerivedDangerScale;
    }
    return f;
}

// ------------------------------------------------------------------ cand（96）

/** Java `Features.candidate(key, derived)`。 */
std::vector<float> candidateOf(const std::string &key,
                              const std::array<int, kPerCandidate> &derived) {
    std::vector<float> f(kNetCand, 0.f);
    int i = 0;
    bool ok = false;
    const Action a = actionParse(key, ok);
    const std::string type = ok ? a.type : std::string();

    const int ti = indexOfArr(kActionTypes, 9, type);
    if (ti >= 0) {
        f[static_cast<size_t>(i + ti)] = 1.f;
    }
    i += 9;
    if (ok && a.hasTile) {
        const int s = tileSlotOf(a.tile);
        if (s >= 0) {
            f[static_cast<size_t>(i + s)] = 1.f;
        }
    }
    i += kTileSlots;
    if (ok && a.hasTiles) {
        for (const std::string &code : a.tiles) {
            const int k = parseKind(code);
            if (k >= 0) {
                f[static_cast<size_t>(i + k)] += 1.f;
            }
        }
    }
    i += kTileSlots;
    f[static_cast<size_t>(i++)] = hasKey(key, "/tsumogiri") ? 1.f : 0.f;
    const int ki = ok ? indexOfArr(kKanKinds, 3, a.kanKind) : -1;
    if (ki >= 0) {
        f[static_cast<size_t>(i + ki)] = 1.f;
    }
    i += 3;
    f[static_cast<size_t>(i++)] = (type == "tsumo" || type == "ron" || type == "pass"
                                   || type == "kyuushu") ? 1.f : 0.f;
    for (int x = 0; x < kPerCandidate; x++) {
        f[static_cast<size_t>(i++)] = static_cast<float>(derived[static_cast<size_t>(x)])
                / kDerivedCandScale[x];
    }
    return f;
}

// ------------------------------------------------------------------ 前向

std::vector<float> trunkOf(const Net &net, const std::vector<float> &x) {
    std::vector<float> h = x;
    for (int l = 0; l < net.trunkLayers; l++) {
        std::vector<float> out(static_cast<size_t>(net.hidden), 0.f);
        for (int o = 0; o < net.hidden; o++) {
            float s = net.tb[static_cast<size_t>(l)][static_cast<size_t>(o)];
            const std::vector<float> &w = net.tw[static_cast<size_t>(l)][static_cast<size_t>(o)];
            for (size_t i = 0; i < h.size(); i++) {
                s += w[i] * h[i];                        // ⚠ 单累加器、正序 —— 与 Java 逐位同序
            }
            out[static_cast<size_t>(o)] = s > 0 ? s : 0.f;
        }
        h = out;
    }
    return h;
}

float logitOf(const Net &net, const std::vector<float> &h, const std::vector<float> &cand) {
    const size_t in = h.size() + cand.size();
    std::vector<float> z(in, 0.f);
    std::memcpy(z.data(), h.data(), h.size() * sizeof(float));
    std::memcpy(z.data() + h.size(), cand.data(), cand.size() * sizeof(float));
    std::vector<float> a(static_cast<size_t>(net.headDim), 0.f);
    for (int o = 0; o < net.headDim; o++) {
        float s = net.hb[static_cast<size_t>(o)];
        const size_t base = static_cast<size_t>(o) * in;
        for (size_t i = 0; i < in; i++) {
            s += net.hw[base + i] * z[i];
        }
        a[static_cast<size_t>(o)] = s > 0 ? s : 0.f;
    }
    float out = net.ob;
    for (int i = 0; i < net.headDim; i++) {
        out += net.ow[static_cast<size_t>(i)] * a[static_cast<size_t>(i)];
    }
    return out;
}

// ------------------------------------------------------------------ 读权重

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
    int32_t i32() {
        if (!need(4)) {
            return 0;
        }
        const uint32_t v = static_cast<uint32_t>(p[pos]) | (static_cast<uint32_t>(p[pos + 1]) << 8)
                | (static_cast<uint32_t>(p[pos + 2]) << 16)
                | (static_cast<uint32_t>(p[pos + 3]) << 24);
        pos += 4;
        return static_cast<int32_t>(v);
    }
    float f32() {
        const uint32_t bits = static_cast<uint32_t>(i32());
        float v = 0.f;
        std::memcpy(&v, &bits, sizeof(v));
        return v;
    }
};

}  // namespace

long long Net::params() const {
    long long n = 0;
    int in = stateDim;
    for (int l = 0; l < trunkLayers; l++) {
        n += static_cast<long long>(hidden) * in + hidden;
        in = hidden;
    }
    n += static_cast<long long>(headDim) * (hidden + candDim) + headDim;
    n += headDim + 1;
    return n;
}

bool loadNetBytes(const std::vector<uint8_t> &raw, const std::string &what, Net &out,
                  std::string &err) {
    if (raw.size() < 28) {
        err = "权重文件太短：" + what;
        return false;
    }
    Reader r{raw.data(), raw.size(), 0, false};
    const int32_t magic = r.i32();
    const int32_t ver = r.i32();
    if (magic != static_cast<int32_t>(kNetMagic)) {
        char buf[128];
        std::snprintf(buf, sizeof(buf), "权重文件魔数不对：%#x（期望 %#x）",
                      static_cast<unsigned>(magic), kNetMagic);
        err = std::string(buf) + "：" + what;
        return false;
    }
    if (ver != kNetFormatVersion) {
        err = "权重格式版本 " + std::to_string(ver) + " != 本训练端 "
                + std::to_string(kNetFormatVersion) + "：" + what;
        return false;
    }
    const int sd = r.i32();
    const int cd = r.i32();
    const int hid = r.i32();
    const int hd = r.i32();
    const int tl = r.i32();
    if (sd != kNetState || cd != kNetCand) {
        err = "权重维度 (" + std::to_string(sd) + "," + std::to_string(cd) + ") != 本训练端特征维度 ("
                + std::to_string(kNetState) + "," + std::to_string(kNetCand) + ") —— 重新导出权重：" + what;
        return false;
    }
    if (tl < 1 || hid < 1 || hd < 1) {
        err = "权重头部非法：trunkLayers=" + std::to_string(tl) + " hidden=" + std::to_string(hid)
                + " head=" + std::to_string(hd) + "：" + what;
        return false;
    }
    out = Net{};
    out.stateDim = sd;
    out.candDim = cd;
    out.hidden = hid;
    out.headDim = hd;
    out.trunkLayers = tl;
    out.tw.assign(static_cast<size_t>(tl), {});
    out.tb.assign(static_cast<size_t>(tl), {});
    int in = sd;
    for (int l = 0; l < tl; l++) {
        out.tw[static_cast<size_t>(l)].assign(static_cast<size_t>(hid),
                                             std::vector<float>(static_cast<size_t>(in), 0.f));
        out.tb[static_cast<size_t>(l)].assign(static_cast<size_t>(hid), 0.f);
        for (int o = 0; o < hid; o++) {
            for (int i = 0; i < in; i++) {
                out.tw[static_cast<size_t>(l)][static_cast<size_t>(o)][static_cast<size_t>(i)] =
                        r.f32();
            }
        }
        for (int o = 0; o < hid; o++) {
            out.tb[static_cast<size_t>(l)][static_cast<size_t>(o)] = r.f32();
        }
        in = hid;
    }
    const int headIn = hid + cd;
    out.hw.assign(static_cast<size_t>(hd) * static_cast<size_t>(headIn), 0.f);
    for (size_t i = 0; i < out.hw.size(); i++) {
        out.hw[i] = r.f32();
    }
    out.hb.assign(static_cast<size_t>(hd), 0.f);
    for (int o = 0; o < hd; o++) {
        out.hb[static_cast<size_t>(o)] = r.f32();
    }
    out.ow.assign(static_cast<size_t>(hd), 0.f);
    for (int i = 0; i < hd; i++) {
        out.ow[static_cast<size_t>(i)] = r.f32();
    }
    out.ob = r.f32();
    if (r.bad) {
        err = "权重文件被截断（读完头部/张量前就到底了）：" + what;
        out = Net{};
        return false;
    }
    return true;
}

bool loadNet(const std::string &path, Net &out, std::string &err) {
    std::FILE *f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) {
        err = "打不开权重文件：" + path;
        return false;
    }
    std::vector<uint8_t> raw;
    uint8_t buf[65536];
    for (;;) {
        const size_t got = std::fread(buf, 1, sizeof(buf), f);
        if (got > 0) {
            raw.insert(raw.end(), buf, buf + got);
        }
        if (got < sizeof(buf)) {
            break;
        }
    }
    std::fclose(f);
    return loadNetBytes(raw, path, out, err);
}

bool netLogits(const Net &net, const JVal &obs, const std::vector<std::string> &keys,
               std::vector<float> &out, std::string &err) {
    out.clear();
    if (keys.empty()) {
        return true;                                     // Java：空 legal → 空 logits（随后回 PASS）
    }
    FeatureView v;
    if (!featureViewOfObs(obs, v)) {
        err = "观测形状不对（ObsFeatures.ofObs 失败）—— 这一行的 obs 不是合法观测";
        return false;
    }
    const std::vector<float> h = trunkOf(net, stateOf(obs, v));
    out.resize(keys.size());
    for (size_t i = 0; i < keys.size(); i++) {
        out[i] = logitOf(net, h, candidateOf(keys[i], perCandidate(v, keys[i])));
    }
    return true;
}

int netArgmax(const std::vector<float> &logits) {
    int best = -1;
    float bestVal = -std::numeric_limits<float>::infinity();
    for (size_t i = 0; i < logits.size(); i++) {
        if (logits[i] > bestVal) {                       // 严格大于 ⇒ 并列取最小下标（Java 同）
            bestVal = logits[i];
            best = static_cast<int>(i);
        }
    }
    return best;
}

int netSampleSoftmax(const std::vector<float> &logits, float temp, JavaRandom &rng) {
    const int n = static_cast<int>(logits.size());
    if (n == 0) {
        return -1;
    }
    float max = -std::numeric_limits<float>::infinity();
    for (float v : logits) {
        if (v > max) {
            max = v;
        }
    }
    std::vector<double> w(static_cast<size_t>(n), 0.0);
    double sum = 0.0;
    for (int i = 0; i < n; i++) {
        double e = std::exp((static_cast<double>(logits[static_cast<size_t>(i)])
                             - static_cast<double>(max)) / static_cast<double>(temp));
        if (std::isnan(e)) {
            e = 0.0;                                     // -inf - (-inf) = NaN：当成权重 0
        }
        w[static_cast<size_t>(i)] = e;
        sum += e;
    }
    if (!(sum > 0.0)) {
        const int fb = netArgmax(logits);
        return fb < 0 ? 0 : fb;
    }
    double r = rng.nextDouble() * sum;
    for (int i = 0; i < n; i++) {
        r -= w[static_cast<size_t>(i)];
        if (r <= 0.0) {
            return i;
        }
    }
    return n - 1;
}

int64_t netMixSeed(int64_t gameSeed, int seat) {
    uint64_t z = static_cast<uint64_t>(gameSeed) * 0x9E3779B97F4A7C15ULL
            + static_cast<uint64_t>(static_cast<int64_t>(seat)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return static_cast<int64_t>(z ^ (z >> 31));
}

std::string netDescribe() {
    return "Features v2 state=" + std::to_string(kNetState) + "（base 539 + derived 68） cand="
            + std::to_string(kNetCand) + "（base 88 + derived 8）";
}

// ------------------------------------------------------------------ CLI

int netCli(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr,
                     "用法：trainer net <net.bin> <轨迹1.jsonl> [轨迹2.jsonl ...]\n"
                     "  逐条 decision 行打印 logits（顺序与文件内出现的顺序一致）：\n"
                     "    step=<step> n=<n> argmax=<i> logits=<v0>,<v1>,...\n"
                     "  值用 %%.9g（float32 往返精度足够）；另有每个文件一行 `# <文件名> <条数> 条`。\n"
                     "  本子命令与 tools/NetProbe.java 的输出**逐字符相同**，供对拍闸门比对。\n");
        return 2;
    }
    Net net;
    std::string err;
    if (!loadNet(argv[1], net, err)) {
        std::fprintf(stderr, "[trainer] %s\n", err.c_str());
        return 1;
    }
    std::fprintf(stderr, "[trainer] 网络已加载：%s（%lld 参数，%s）\n", argv[1], net.params(),
                 netDescribe().c_str());

    long long total = 0;
    for (int a = 2; a < argc; a++) {
        const std::string path = argv[a];
        std::FILE *f = std::fopen(path.c_str(), "rb");
        if (f == nullptr) {
            std::fprintf(stderr, "[trainer] 打不开轨迹文件：%s\n", path.c_str());
            return 1;
        }
        std::string text;
        char buf[65536];
        for (;;) {
            const size_t got = std::fread(buf, 1, sizeof(buf), f);
            if (got > 0) {
                text.append(buf, got);
            }
            if (got < sizeof(buf)) {
                break;
            }
        }
        std::fclose(f);

        const std::string name = path.substr(path.find_last_of("/\\") == std::string::npos
                                                     ? 0
                                                     : path.find_last_of("/\\") + 1);
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
                if (last) {
                    break;
                }
                continue;
            }
            const JVal *type = row.find("type");
            const JVal *obs = row.find("obs");
            if (type == nullptr || !type->isStr() || type->strVal != "decision" || obs == nullptr
                    || !obs->isObj()) {
                if (last) {
                    break;
                }
                continue;
            }
            std::vector<std::string> keys;
            const JVal *legal = row.find("legal");
            if (legal != nullptr && legal->isArr()) {
                for (const JVal &k : legal->arr) {
                    keys.push_back(k.asStr());
                }
            }
            std::vector<float> logits;
            if (!netLogits(net, *obs, keys, logits, err)) {
                std::fprintf(stderr, "[trainer] %s（文件 %s，第 %lld 条决策）\n", err.c_str(),
                             path.c_str(), count + 1);
                return 1;
            }
            const JVal *step = row.find("step");
            const long long stepVal = step == nullptr ? -1 : step->intVal;
            std::printf("step=%lld n=%zu argmax=%d logits=", stepVal, keys.size(),
                        netArgmax(logits));
            for (size_t i = 0; i < logits.size(); i++) {
                if (i > 0) {
                    std::printf(",");
                }
                std::printf("%.9g", static_cast<double>(logits[i]));
            }
            std::printf("\n");
            count++;
            total++;
            if (last) {
                break;
            }
        }
        std::printf("# %s %lld 条\n", name.c_str(), count);
    }
    std::fflush(stdout);
    std::fprintf(stderr, "[trainer] 共 %lld 条决策的前向完成\n", total);
    return 0;
}

}  // namespace trainer
