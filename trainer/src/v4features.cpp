// v4 四张量的拼装 —— 逐句移植 Java `mahjong.ai.V4Features`（+ 它读的 `ObsFeatures`）。
//
// 移植纪律（与 `net.cpp` 同源，见 `docs/TRAINER-CPP.md` §2）：
//   · **Java 是权威**：通道顺序、归一化系数（`/4`、`/100`、`/18`…）、四家旋转、one-hot 槽位、
//     事件的窗口对齐（新的在尾部）都不许"顺手优化"；
//   · **派生量只读 `obffeatures.hpp`**：那是 `ObsFeatures`/`Danger`/`HandEval` 的镜像，
//     已经与 Java 逐字节对拍过（sidecar）；这里再算一遍 = 两套实现必然漂移；
//   · 拼装里没有浮点求和顺序问题（最"敏感"的只是 `x / 4f` 这类逐元素除法），
//     真正敏感的是 `v4policy.cpp` 的点积累加 —— 编译的 `-ffp-contract=off` 是给那次准备的。
#include "v4features.hpp"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>

#include "net.hpp"          // `netCandidateVector`（v4 的 cand 前 96 列沿用 v3 的 Features.candidate）
#include "obffeatures.hpp"  // `featureViewOfObs` / `perSeat` / `perCandidate`

namespace trainer {

// ---------------------------------------------------------------- 布局表（= Java 的 public static 表）
//
// 顺序即内存布局；宽度合计必须 == 张量宽度（Java 在 static 块里断言，这里在 .cpp 末尾断言）。
const char *const kTileChannels[26] = {
    "own_count", "own_aka", "own_drawn",
    "river_all", "river_before_riichi", "river_after_riichi",
    "river_tedashi", "river_tsumogiri", "meld_count",
    "safety_genbutsu", "safety_suji", "danger_all", "danger_riichi",
    "visible", "unseen", "drawable", "dora_indicator",
    "is_dora", "is_aka", "is_yakuhai", "is_terminal", "is_honor",
    "suit_m", "suit_p", "suit_s", "reserved",
};
const int kTileWidths[26] = {
    1, 1, 1, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3,
};
const char *const kEvtFields[13] = {
    "type", "tile_kind", "tile_aka", "called_kind", "called_aka", "actor", "from",
    "meld_kind", "turn", "tsumogiri", "sideways", "rip_phase", "seq_delta",
};
const int kEvtWidths[13] = {8, 34, 1, 34, 1, 4, 4, 5, 1, 1, 1, 1, 1};
const char *const kEvtTypes[8] = {
    "draw", "discard", "meld", "riichi", "kan", "dora_flip", "agari", "ryuukyoku",
};
const char *const kMeldKindsV4[5] = {"chi", "pon", "ankan", "kakan", "daiminkan"};
const char *const kCtxGroups[7] = {"round", "points", "wall", "self", "seats", "ask", "reserved"};
const int kCtxWidths[7] = {8, 12, 8, 10, 12, 8, 6};
const char *const kBlockIds[16] = {
    "tile.own", "tile.per_opp", "tile.safety", "tile.danger", "tile.global", "evt.stream",
    "ctx.round", "ctx.points", "ctx.wall", "ctx.self", "ctx.seats", "ctx.ask", "ctx.reserved",
    "cand.base", "cand.derived", "cand.reserved",
};
const int kBlockWidths[16] = {
    3, 18, 6, 6, 15, kCEvt, 8, 12, 8, 10, 12, 8, 6, kCandBase, kCandDerived, kCandReserved,
};
const char *const kWinNotesV4[2] = {"furiten", "no_yaku"};

namespace {

constexpr int kKind = kKindCount;                 // 34
constexpr const char *kWindNamesV4 = "ESWN";

// ---------------------------------------------------------------- sha256（只为本文件的指纹用）
//
// 为什么自己写一个小实现而不是引第三方：服务端/训练端的口径是**零第三方依赖**
// （`AGENTS.md` §6.5：不给服务端加 ML 依赖），而这里只需要"算 16 个十六进制字符"这一个用途。
// 算法 = FIPS 180-4 的 SHA-256，与 Java `MessageDigest.getInstance("SHA-256")` 逐位同值。
struct Sha256 {
    uint32_t h[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                     0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u};
    uint8_t buf[64] = {};
    size_t bufLen = 0;
    uint64_t total = 0;

    static uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

    void block(const uint8_t *p) {
        static const uint32_t k[64] = {
            0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u,
            0x923f82a4u, 0xab1c5ed5u, 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
            0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u, 0xe49b69c1u, 0xefbe4786u,
            0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
            0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u,
            0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
            0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u, 0xa2bfe8a1u, 0xa81a664bu,
            0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
            0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au,
            0x5b9cca4fu, 0x682e6ff3u, 0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
            0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u};
        uint32_t w[64];
        for (int i = 0; i < 16; i++) {
            w[i] = (static_cast<uint32_t>(p[i * 4]) << 24)
                    | (static_cast<uint32_t>(p[i * 4 + 1]) << 16)
                    | (static_cast<uint32_t>(p[i * 4 + 2]) << 8)
                    | static_cast<uint32_t>(p[i * 4 + 3]);
        }
        for (int i = 16; i < 64; i++) {
            const uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            const uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3];
        uint32_t e = h[4], f = h[5], g = h[6], hh = h[7];
        for (int i = 0; i < 64; i++) {
            const uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            const uint32_t ch = (e & f) ^ (~e & g);
            const uint32_t t1 = hh + s1 + ch + k[i] + w[i];
            const uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            const uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            const uint32_t t2 = s0 + maj;
            hh = g; g = f; f = e; e = d + t1;
            d = c; c = b; b = a; a = t1 + t2;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d;
        h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }

    void update(const uint8_t *p, size_t n) {
        total += n;
        while (n > 0) {
            const size_t take = std::min(n, sizeof(buf) - bufLen);
            std::memcpy(buf + bufLen, p, take);
            bufLen += take;
            p += take;
            n -= take;
            if (bufLen == sizeof(buf)) {
                block(buf);
                bufLen = 0;
            }
        }
    }

    void finish(uint8_t out[32]) {
        const uint64_t bits = total * 8;
        const uint8_t pad = 0x80;
        update(&pad, 1);
        const uint8_t zero = 0x00;
        while (bufLen != 56) {
            update(&zero, 1);
        }
        uint8_t len[8];
        for (int i = 0; i < 8; i++) {
            len[i] = static_cast<uint8_t>(bits >> (56 - 8 * i));
        }
        update(len, 8);                       // 这一句会正好把最后一块冲出去
        for (int i = 0; i < 8; i++) {
            out[i * 4] = static_cast<uint8_t>(h[i] >> 24);
            out[i * 4 + 1] = static_cast<uint8_t>(h[i] >> 16);
            out[i * 4 + 2] = static_cast<uint8_t>(h[i] >> 8);
            out[i * 4 + 3] = static_cast<uint8_t>(h[i]);
        }
    }
};

/** `[[id,width],…]` 的 sha256 前 16 位十六进制 —— 与 Java/Python **逐字节同算法**。 */
std::string fingerprintOf(const std::vector<std::string> &ids, const std::vector<int> &widths) {
    std::string payload;
    for (size_t i = 0; i < ids.size(); i++) {
        payload += (i == 0 ? "[" : ",");
        payload += "[\"";
        payload += ids[i];
        payload += "\",";
        payload += std::to_string(widths[i]);
        payload += "]";
    }
    payload += "]";
    Sha256 s;
    s.update(reinterpret_cast<const uint8_t *>(payload.data()), payload.size());
    uint8_t h[32];
    s.finish(h);
    char hex[17];
    for (int i = 0; i < 8; i++) {
        std::snprintf(hex + i * 2, 3, "%02x", h[i]);
    }
    return std::string(hex, 16);
}

// ---------------------------------------------------------------- obs 取值小工具（= Java 的 i/d/bool/str）
int jInt(const JVal *v, int def) {
    if (v == nullptr) {
        return def;
    }
    if (v->type == JVal::Type::Num) {
        return static_cast<int>(v->intVal);
    }
    if (v->type == JVal::Type::Bool) {
        return v->boolVal ? 1 : 0;
    }
    if (v->type == JVal::Type::Str && !v->strVal.empty()) {
        const char *b = v->strVal.c_str();
        while (*b == ' ' || *b == '\t' || *b == '\n' || *b == '\r') {
            b++;
        }
        char *end = nullptr;
        const long x = std::strtol(b, &end, 10);
        if (end == b || end == nullptr || *end != '\0') {
            return def;
        }
        return static_cast<int>(x);
    }
    return def;
}

bool jBool(const JVal *v) {
    if (v == nullptr) {
        return false;
    }
    if (v->type == JVal::Type::Bool) {
        return v->boolVal;
    }
    if (v->type == JVal::Type::Num) {
        return v->intVal != 0;
    }
    return false;
}

double jDouble(const JVal *v, double def) {
    return (v != nullptr && v->type == JVal::Type::Num) ? static_cast<double>(v->intVal) : def;
}

/** Java `V4Features.str(Object)`（`null`/非串 → 空串；本文件只拿它与定串比较和 `parseKind`）。 */
std::string jStr(const JVal *v) {
    return (v != nullptr && v->type == JVal::Type::Str) ? v->strVal : std::string();
}

/** 对象里的字段（Java `e.get(k)`；缺键 → `nullptr`）。 */
const JVal *field(const JVal &o, const char *k) { return o.find(k); }

/** Java `o != null`：**JSON null 也算"没有"**（`Json` 把 null 解析成 Java null）。 */
bool present(const JVal *v) { return v != nullptr && v->type != JVal::Type::Null; }

/** Java `str(x).startsWith("0")`（赤五牌码以 `0` 开头：0m/0p/0s）。 */
bool startsWithZero(const std::string &s) { return !s.empty() && s[0] == '0'; }

/** Java `ObsFeatures.ints(list, n)`：只认**数字**（JSON 布尔一律 0）。 */
void intsOf(const JVal *arr, int n, int *out) {
    for (int i = 0; i < n; i++) {
        out[i] = 0;
    }
    if (arr == nullptr || arr->type != JVal::Type::Arr) {
        return;
    }
    for (int i = 0; i < n && i < static_cast<int>(arr->arr.size()); i++) {
        const JVal &v = arr->arr[static_cast<size_t>(i)];
        if (v.type == JVal::Type::Num) {
            out[i] = static_cast<int>(v.intVal);
        }
    }
}

/** obs 里的**布尔数组**（`hand_red` 这种）→ 0/1 的 int 数组（顺带认 0/1 数字）= `V4Features.boolFlags`。 */
void boolFlagsOf(const JVal *o, int size, int *out) {
    for (int i = 0; i < size; i++) {
        out[i] = 0;
    }
    if (o == nullptr || o->type != JVal::Type::Arr) {
        return;
    }
    for (int k = 0; k < size && k < static_cast<int>(o->arr.size()); k++) {
        const JVal &v = o->arr[static_cast<size_t>(k)];
        if (v.type == JVal::Type::Bool) {
            out[k] = v.boolVal ? 1 : 0;
        } else if (v.type == JVal::Type::Num) {
            out[k] = v.intVal != 0 ? 1 : 0;
        }
    }
}

/** 牌码序列 → 34 维计数（= `V4Features.countsOf`）。 */
void countsOf(const std::vector<std::string> &codes, int *out) {
    for (int k = 0; k < kKind; k++) {
        out[k] = 0;
    }
    for (const std::string &c : codes) {
        const int k = parseKind(c);
        if (k >= 0 && k < kKind) {
            out[k]++;
        }
    }
}

/** `m.arr` 里的**字符串**（Java `String.valueOf(o)`：非串也照样 stringify；这里只用到牌码）。 */
std::vector<std::string> codesOf(const JVal *arr) {
    std::vector<std::string> out;
    if (arr == nullptr || arr->type != JVal::Type::Arr) {
        return out;
    }
    out.reserve(arr->arr.size());
    for (const JVal &c : arr->arr) {
        out.push_back(jStr(&c));
    }
    return out;
}

/** 四家牌河（**恒 4 行**；缺的填空）= `V4Features.discardsOf`。 */
std::array<std::vector<std::string>, kNPlayersV4> discardsOf(const JVal &obs) {
    std::array<std::vector<std::string>, kNPlayersV4> out;
    const JVal *raw = obs.find("discards");
    if (raw == nullptr || raw->type != JVal::Type::Arr) {
        return out;
    }
    for (int s = 0; s < kNPlayersV4 && s < static_cast<int>(raw->arr.size()); s++) {
        out[static_cast<size_t>(s)] = codesOf(&raw->arr[static_cast<size_t>(s)]);
    }
    return out;
}

/** 事件流（只保留**对象**元素，保持原顺序）= `V4Features.eventsOf`。 */
std::vector<const JVal *> eventsOf(const JVal &obs) {
    std::vector<const JVal *> out;
    const JVal *raw = obs.find("events");
    if (raw == nullptr || raw->type != JVal::Type::Arr) {
        return out;
    }
    for (const JVal &e : raw->arr) {
        if (e.type == JVal::Type::Obj) {
            out.push_back(&e);
        }
    }
    return out;
}

/** 四家副露（**恒 4 行**）= `V4Features.meldsOf`（只保留对象元素）。 */
std::array<std::vector<const JVal *>, kNPlayersV4> meldsOf(const JVal &obs) {
    std::array<std::vector<const JVal *>, kNPlayersV4> out;
    const JVal *raw = obs.find("melds");
    if (raw == nullptr || raw->type != JVal::Type::Arr) {
        return out;
    }
    for (int s = 0; s < kNPlayersV4 && s < static_cast<int>(raw->arr.size()); s++) {
        const JVal &row = raw->arr[static_cast<size_t>(s)];
        if (row.type != JVal::Type::Arr) {
            continue;
        }
        for (const JVal &m : row.arr) {
            if (m.type == JVal::Type::Obj) {
                out[static_cast<size_t>(s)].push_back(&m);
            }
        }
    }
    return out;
}

/** 某家副露的牌种计数（= `V4Features.meldCounts`）。 */
void meldCountsOf(const JVal &obs, int absSeat, int *out) {
    for (int k = 0; k < kKind; k++) {
        out[k] = 0;
    }
    if (absSeat < 0 || absSeat >= kNPlayersV4) {
        return;
    }
    const auto melds = meldsOf(obs);
    for (const JVal *m : melds[static_cast<size_t>(absSeat)]) {
        const JVal *tiles = field(*m, "tiles");
        if (tiles == nullptr || tiles->type != JVal::Type::Arr) {
            continue;
        }
        for (const JVal &code : tiles->arr) {
            const int k = parseKind(jStr(&code));
            if (k >= 0 && k < kKind) {
                out[k]++;
            }
        }
    }
}

/** 指示牌 → 宝牌牌种（数牌 +1 循环、风 E→S→W→N、三元 白→発→中）= `V4Features.doraKinds`。 */
void doraKindsOf(const std::vector<std::string> &indicators, bool *out) {
    for (int k = 0; k < kKind; k++) {
        out[k] = false;
    }
    for (const std::string &code : indicators) {
        const int k = parseKind(code);
        if (k < 0) {
            continue;
        }
        const int d = doraFrom(k);
        if (d >= 0 && d < kKind) {
            out[d] = true;
        }
    }
}

std::vector<std::string> legalOf(const JVal &obs) {
    const JVal *raw = obs.find("legal");
    if (raw == nullptr || raw->type != JVal::Type::Arr) {
        return {};
    }
    std::vector<std::string> out;
    out.reserve(raw->arr.size());
    for (const JVal &k : raw->arr) {
        out.push_back(jStr(&k));
    }
    return out;
}

// ---------------------------------------------------------------- tile 的写入原语（= Java put/putCol）
using TileMat = std::array<std::array<float, kCTile>, kKind>;

/** 找通道的起始列（= Java `put` 里那个扫描；找不到返回 -1 并由调用方报错）。 */
int tileOffOf(const char *channel) {
    int off = 0;
    for (int i = 0; i < 26; i++) {
        if (std::strcmp(kTileChannels[i], channel) == 0) {
            return off;
        }
        off += kTileWidths[i];
    }
    return -1;
}

bool putTile(TileMat &m, const char *channel, const float *values, std::string &err) {
    const int off = tileOffOf(channel);
    if (off < 0) {
        err = std::string("没有这个 tile 通道：") + channel;
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        m[static_cast<size_t>(k)][static_cast<size_t>(off)] = values[k];
    }
    return true;
}

bool putTileCol(TileMat &m, const char *channel, int col, const float *values, std::string &err) {
    const int off = tileOffOf(channel);
    if (off < 0) {
        err = std::string("没有这个 tile 通道：") + channel;
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        m[static_cast<size_t>(k)][static_cast<size_t>(off + col)] = values[k];
    }
    return true;
}

/** `counts[k] / div`（= Java `col(int[], float)`；**逐元素 float 除法**，不是乘倒数）。 */
void colOf(const int *counts, float div, float *out) {
    for (int k = 0; k < kKind; k++) {
        out[k] = static_cast<float>(counts[k]) / div;
    }
}

/** `values[k] * mul`（= Java `scale(int[], float)`；`mul` 是 `1f/100f` 那个编译期常量）。 */
void scaleOf(const uint8_t *values, float mul, float *out) {
    for (int k = 0; k < kKind; k++) {
        out[k] = static_cast<float>(values[k]) * mul;
    }
}

/** 位图 → 0/1（**字节内 LSB 在前**，与 Java/Python 同一约定）。 */
void bitmapOf(const uint8_t *packed, int bytes, float *out) {
    for (int k = 0; k < kKind; k++) {
        out[k] = 0.f;
    }
    for (int k = 0; k < kKind && k < bytes * 8; k++) {
        out[k] = ((packed[k / 8] >> (k % 8)) & 1) != 0 ? 1.f : 0.f;
    }
}

// ---------------------------------------------------------------- evt / ctx 的偏移
int evtOffOf(const char *name, std::string &err) {
    int off = 0;
    for (int i = 0; i < 13; i++) {
        if (std::strcmp(kEvtFields[i], name) == 0) {
            return off;
        }
        off += kEvtWidths[i];
    }
    err = std::string("没有这个 evt 字段：") + name;
    return -1;
}

int ctxOffOf(const char *name, std::string &err) {
    int off = 0;
    for (int i = 0; i < 7; i++) {
        if (std::strcmp(kCtxGroups[i], name) == 0) {
            return off;
        }
        off += kCtxWidths[i];
    }
    err = std::string("没有这个 ctx 通道组：") + name;
    return -1;
}

/** Java `Math.floorMod(a, 4)`（a 为负也回绕到 0..3）。 */
int floorMod4(int a) { return ((a % 4) + 4) % 4; }

/** 布局自洽（= Java 的 static 块）：少一列 = 有通道没人写；多一列 = 张量装不下。 */
const char *layoutError() {
    int s = 0;
    for (int i = 0; i < 26; i++) {
        s += kTileWidths[i];
    }
    if (s != kCTile) {
        return "TILE 布局宽度合计 != 48";
    }
    s = 0;
    for (int i = 0; i < 13; i++) {
        s += kEvtWidths[i];
    }
    if (s != kCEvt) {
        return "EVT 布局宽度合计 != 96";
    }
    s = 0;
    for (int i = 0; i < 7; i++) {
        s += kCtxWidths[i];
    }
    if (s != kCCtx) {
        return "CTX 布局宽度合计 != 64";
    }
    s = 0;
    for (int i = 0; i < 16; i++) {
        s += kBlockWidths[i];
    }
    if (s != kCTile + kCEvt + kCCtx + kCCand) {
        return "块宽度合计 != 四张量合计";
    }
    return nullptr;
}

}  // namespace

// ---------------------------------------------------------------- 指纹 / 块表

int v4IndexOfBlock(const std::string &bid) {
    for (int i = 0; i < 16; i++) {
        if (bid == kBlockIds[i]) {
            return i;
        }
    }
    return -1;
}

bool v4WidthsOf(const std::vector<std::string> &ids, std::vector<int> &out, std::string &err) {
    out.clear();
    for (const std::string &id : ids) {
        const int idx = v4IndexOfBlock(id);
        if (idx < 0) {
            err = "未注册的块 id：" + id;
            return false;
        }
        out.push_back(kBlockWidths[idx]);
    }
    return true;
}

std::string v4Fingerprint() {
    std::vector<std::string> ids;
    std::vector<int> widths;
    for (int i = 0; i < 16; i++) {
        ids.emplace_back(kBlockIds[i]);
        widths.push_back(kBlockWidths[i]);
    }
    return fingerprintOf(ids, widths);
}

std::string v4Fingerprint(const std::vector<std::string> &ids, const std::vector<int> &widths) {
    return fingerprintOf(ids, widths);
}

// ---------------------------------------------------------------- tile
namespace {

/** `[34][48]`，四家块**旋转到自己为下标 0**（0=自己 / 1=下家 / 2=対面 / 3=上家）。 */
bool tileMatrix(const JVal &obs, const FeatureView &view, const PerSeatDerived &ps, TileMat &m,
                std::string &err) {
    const int seat = jInt(obs.find("seat"), 0);
    // ⚠ `hand_red` 是**布尔数组**（这个牌种有没有赤五），不是计数 —— 见文件头第 ① 条。
    int handRed[kKind];
    boolFlagsOf(obs.find("hand_red"), kKind, handRed);
    int hand[kKind];
    for (int k = 0; k < kKind; k++) {
        hand[k] = view.hand[static_cast<size_t>(k)];
    }
    const int drawnKind = parseKind(jStr(obs.find("drawn")));
    float buf[kKind];
    colOf(hand, 4.0f, buf);
    if (!putTile(m, "own_count", buf, err)) {
        return false;
    }
    colOf(handRed, 1.0f, buf);
    if (!putTile(m, "own_aka", buf, err)) {
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        buf[k] = 0.f;
    }
    if (drawnKind >= 0) {
        buf[drawnKind] = 1.f;
    }
    if (!putTile(m, "own_drawn", buf, err)) {
        return false;
    }

    const auto discards = discardsOf(obs);
    const auto events = eventsOf(obs);
    for (int j = 1; j < kNPlayersV4; j++) {
        const int abs = (seat + j) % kNPlayersV4;
        const int ch = j - 1;
        int cnt[kKind];
        countsOf(discards[static_cast<size_t>(abs)], cnt);
        colOf(cnt, 4.f, buf);
        if (!putTileCol(m, "river_all", ch, buf, err)) {
            return false;
        }
        std::vector<std::string> before, after, tsumo, tedashi;
        for (const JVal *e : events) {
            if (jStr(field(*e, "type")) != "discard" || jInt(field(*e, "actor"), -1) != abs) {
                continue;
            }
            const std::string code = jStr(field(*e, "tile"));
            if (jBool(field(*e, "rip_phase"))) {
                after.push_back(code);
            } else {
                before.push_back(code);
            }
            if (jBool(field(*e, "tsumogiri"))) {
                tsumo.push_back(code);
            } else {
                tedashi.push_back(code);
            }
        }
        countsOf(before, cnt);
        colOf(cnt, 4.f, buf);
        if (!putTileCol(m, "river_before_riichi", ch, buf, err)) {
            return false;
        }
        countsOf(after, cnt);
        colOf(cnt, 4.f, buf);
        if (!putTileCol(m, "river_after_riichi", ch, buf, err)) {
            return false;
        }
        countsOf(tedashi, cnt);
        colOf(cnt, 4.f, buf);
        if (!putTileCol(m, "river_tedashi", ch, buf, err)) {
            return false;
        }
        countsOf(tsumo, cnt);
        colOf(cnt, 4.f, buf);
        if (!putTileCol(m, "river_tsumogiri", ch, buf, err)) {
            return false;
        }
        meldCountsOf(obs, abs, cnt);
        colOf(cnt, 4.f, buf);
        if (!putTileCol(m, "meld_count", ch, buf, err)) {
            return false;
        }
        // 逐家安全/危险度：**相对方位**下标（0=下家/1=対面/2=上家）与 `ch` 完全同序
        const auto &gb = ps.genbutsu[static_cast<size_t>(ch)];
        bitmapOf(gb.data(), kBitmapBytes, buf);
        if (!putTileCol(m, "safety_genbutsu", ch, buf, err)) {
            return false;
        }
        const auto &sj = ps.suji[static_cast<size_t>(ch)];
        bitmapOf(sj.data(), kBitmapBytes, buf);
        if (!putTileCol(m, "safety_suji", ch, buf, err)) {
            return false;
        }
        // `1f / 100f` 是编译期常量：与 Java `scale(..., 1f/100f)` 逐位同值（不是写 `/100f`）
        scaleOf(ps.danger[static_cast<size_t>(ch)].data(), 1.f / 100.f, buf);
        if (!putTileCol(m, "danger_all", ch, buf, err)) {
            return false;
        }
        scaleOf(ps.dangerRiichi[static_cast<size_t>(ch)].data(), 1.f / 100.f, buf);
        if (!putTileCol(m, "danger_riichi", ch, buf, err)) {
            return false;
        }
    }

    int visible[kKind];
    intsOf(obs.find("visible"), kKind, visible);
    float unseen[kKind];
    float drawable[kKind];
    for (int k = 0; k < kKind; k++) {
        unseen[k] = std::max(0.f, 4.f - static_cast<float>(visible[k])) / 4.f;
        drawable[k] = std::max(0.f, 4.f - static_cast<float>(visible[k])
                                          - static_cast<float>(hand[k])) / 4.f;
    }
    bool dora[kKind];
    const std::vector<std::string> indicators = codesOf(obs.find("dora_indicators"));
    doraKindsOf(indicators, dora);
    colOf(visible, 4.f, buf);
    if (!putTile(m, "visible", buf, err)) {
        return false;
    }
    if (!putTile(m, "unseen", unseen, err)) {
        return false;
    }
    if (!putTile(m, "drawable", drawable, err)) {
        return false;
    }
    int doraCnt[kKind];
    countsOf(indicators, doraCnt);
    colOf(doraCnt, 4.f, buf);
    if (!putTile(m, "dora_indicator", buf, err)) {
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        buf[k] = dora[k] ? 1.f : 0.f;
    }
    if (!putTile(m, "is_dora", buf, err)) {
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        buf[k] = (k == kAkaM || k == kAkaP || k == kAkaS) ? 1.f : 0.f;
    }
    if (!putTile(m, "is_aka", buf, err)) {
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        buf[k] = k >= 27 ? 1.f : 0.f;
    }
    if (!putTile(m, "is_yakuhai", buf, err)) {
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        buf[k] = isTerminal(k) ? 1.f : 0.f;
    }
    if (!putTile(m, "is_terminal", buf, err)) {
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        buf[k] = isHonor(k) ? 1.f : 0.f;
    }
    if (!putTile(m, "is_honor", buf, err)) {
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        buf[k] = k < 9 ? 1.f : 0.f;
    }
    if (!putTile(m, "suit_m", buf, err)) {
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        buf[k] = (k >= 9 && k < 18) ? 1.f : 0.f;
    }
    if (!putTile(m, "suit_p", buf, err)) {
        return false;
    }
    for (int k = 0; k < kKind; k++) {
        buf[k] = (k >= 18 && k < 27) ? 1.f : 0.f;
    }
    if (!putTile(m, "suit_s", buf, err)) {
        return false;
    }
    return true;                              // reserved 3 列保持 0
}

// ---------------------------------------------------------------- evt
/** `[60][96]`：最近 K 条公开事件，**新的在尾部**（与增量缓存同序）。 */
bool eventMatrix(const JVal &obs, std::array<std::array<float, kCEvt>, kKEvt> &out,
                 std::string &err) {
    const auto events = eventsOf(obs);
    const int seat = jInt(obs.find("seat"), 0);
    const int m = std::min(static_cast<int>(events.size()), kKEvt);
    int offType = 0, offTileKind = 0, offTileAka = 0, offCalledKind = 0, offCalledAka = 0;
    int offActor = 0, offFrom = 0, offMeldKind = 0, offTurn = 0, offTsumogiri = 0;
    int offSideways = 0, offRipPhase = 0, offSeqDelta = 0;
    // 逐字段取偏移（照 Java `evtOff` 的扫描；找不到就报错，不猜）
    offType = evtOffOf("type", err);
    offTileKind = evtOffOf("tile_kind", err);
    offTileAka = evtOffOf("tile_aka", err);
    offCalledKind = evtOffOf("called_kind", err);
    offCalledAka = evtOffOf("called_aka", err);
    offActor = evtOffOf("actor", err);
    offFrom = evtOffOf("from", err);
    offMeldKind = evtOffOf("meld_kind", err);
    offTurn = evtOffOf("turn", err);
    offTsumogiri = evtOffOf("tsumogiri", err);
    offSideways = evtOffOf("sideways", err);
    offRipPhase = evtOffOf("rip_phase", err);
    offSeqDelta = evtOffOf("seq_delta", err);
    if (!err.empty()) {
        return false;
    }
    for (int x = 0; x < m; x++) {
        const JVal &e = *events[events.size() - static_cast<size_t>(m - x)];
        auto &row = out[static_cast<size_t>(kKEvt - m + x)];
        const std::string t = jStr(field(e, "type"));
        for (int i = 0; i < 8; i++) {
            if (t == kEvtTypes[i]) {
                row[static_cast<size_t>(offType + i)] = 1.f;
            }
        }
        const int tk = parseKind(jStr(field(e, "tile")));
        if (tk >= 0) {
            row[static_cast<size_t>(offTileKind + tk)] = 1.f;
        }
        const int ck = parseKind(jStr(field(e, "called_tile")));
        if (ck >= 0) {
            row[static_cast<size_t>(offCalledKind + ck)] = 1.f;
        }
        const JVal *actor = field(e, "actor");
        if (present(actor)) {
            row[static_cast<size_t>(offActor + floorMod4(jInt(actor, 0) - seat))] = 1.f;
        }
        const JVal *src = field(e, "from");
        if (present(src)) {
            row[static_cast<size_t>(offFrom + floorMod4(jInt(src, 0) - seat))] = 1.f;
        }
        const std::string mk = jStr(field(e, "meld_kind"));
        for (int i = 0; i < 5; i++) {
            if (mk == kMeldKindsV4[i]) {
                row[static_cast<size_t>(offMeldKind + i)] = 1.f;
            }
        }
        row[static_cast<size_t>(offTileAka)] = startsWithZero(jStr(field(e, "tile"))) ? 1.f : 0.f;
        row[static_cast<size_t>(offCalledAka)]
                = startsWithZero(jStr(field(e, "called_tile"))) ? 1.f : 0.f;
        row[static_cast<size_t>(offTurn)] = static_cast<float>(jInt(field(e, "turn"), 0)) / 18.f;
        row[static_cast<size_t>(offTsumogiri)] = jBool(field(e, "tsumogiri")) ? 1.f : 0.f;
        row[static_cast<size_t>(offSideways)] = jBool(field(e, "sideways")) ? 1.f : 0.f;
        row[static_cast<size_t>(offRipPhase)] = jBool(field(e, "rip_phase")) ? 1.f : 0.f;
        // ⚠ 这里是 double 域算完再落 float（照 Java：`Math.min(d,8.0)/8.0` 然后 cast）
        row[static_cast<size_t>(offSeqDelta)] = static_cast<float>(
                std::min(jDouble(field(e, "seq_delta"), 1.0), 8.0) / 8.0);
    }
    return true;
}

// ---------------------------------------------------------------- ctx
/** `[64]`：每次重算（便宜）。 */
void ctxVector(const JVal &obs, std::array<float, kCCtx> &v) {
    const int seat = jInt(obs.find("seat"), 0);
    const JVal *rndPtr = obs.find("round");
    const bool hasRound = rndPtr != nullptr && rndPtr->type == JVal::Type::Obj;
    const JVal *rnd = hasRound ? rndPtr : nullptr;

    std::string bakaze = jStr(rnd == nullptr ? nullptr : rnd->find("bakaze"));
    if (bakaze.empty()) {
        bakaze = "E";
    }
    for (char &c : bakaze) {                  // `.toUpperCase(Locale.ROOT)`：只可能是 ASCII 字母
        if (c >= 'a' && c <= 'z') {
            c = static_cast<char>(c - 'a' + 'A');
        }
    }

    int off = 0;
    for (int w = 0; w < 4; w++) {
        // Java：`bakaze.equals(WINDS[w])` 比较的是**整串**（不是首字符；`windIndex` 才取首字符）
        const std::string wind(1, kWindNamesV4[w]);
        v[static_cast<size_t>(off++)] = bakaze == wind ? 1.f : 0.f;
    }
    v[static_cast<size_t>(off++)]
            = static_cast<float>(rnd == nullptr ? 1 : jInt(rnd->find("kyoku"), 1)) / 4.f;
    v[static_cast<size_t>(off++)]
            = static_cast<float>(rnd == nullptr ? 0 : jInt(rnd->find("honba"), 0)) / 10.f;
    v[static_cast<size_t>(off++)]
            = static_cast<float>(rnd == nullptr ? 0 : jInt(rnd->find("riichi_sticks"), 0)) / 4.f;
    v[static_cast<size_t>(off++)] = static_cast<float>(
            floorMod4((rnd == nullptr ? 0 : jInt(rnd->find("dealer"), 0)) - seat)) / 3.f;

    int scores[kNPlayersV4];
    intsOf(obs.find("scores"), kNPlayersV4, scores);
    const int self = scores[seat];
    int othersSum = 0;
    int rank = 1;
    int minUp = std::numeric_limits<int>::max();
    int minDown = std::numeric_limits<int>::max();
    for (int j = 1; j < kNPlayersV4; j++) {
        const int s = scores[(seat + j) % kNPlayersV4];
        othersSum += s;
        if (s > self) {
            rank++;
            minUp = std::min(minUp, s - self);
        } else if (s < self) {
            minDown = std::min(minDown, self - s);
        }
        v[static_cast<size_t>(off + j)]
                = (static_cast<float>(s) - 25000.f) / 1000.f;   // 下标 0 = 自己（旋转后）
    }
    v[static_cast<size_t>(off)] = (static_cast<float>(self) - 25000.f) / 1000.f;
    off += kNPlayersV4;
    for (int r = 1; r <= kNPlayersV4; r++) {
        v[static_cast<size_t>(off++)] = rank == r ? 1.f : 0.f;
    }
    v[static_cast<size_t>(off++)]
            = (static_cast<float>(self) - static_cast<float>(othersSum) / 3.f) / 1000.f;
    v[static_cast<size_t>(off++)] = (static_cast<float>(self) - 25000.f) / 1000.f;
    v[static_cast<size_t>(off++)]
            = static_cast<float>(minUp == std::numeric_limits<int>::max() ? 0 : minUp) / 1000.f;
    v[static_cast<size_t>(off++)]
            = static_cast<float>(minDown == std::numeric_limits<int>::max() ? 0 : minDown) / 1000.f;

    v[static_cast<size_t>(off++)] = static_cast<float>(jInt(obs.find("tiles_left"), 0)) / 70.f;
    v[static_cast<size_t>(off++)]
            = static_cast<float>(jInt(obs.find("dead_wall_left"), 0)) / 4.f;
    v[static_cast<size_t>(off++)]
            = static_cast<float>(jInt(obs.find("total_discards"), 0)) / 72.f;
    v[static_cast<size_t>(off++)] = static_cast<float>(jInt(obs.find("kan_count"), 0)) / 4.f;
    v[static_cast<size_t>(off++)] = jBool(obs.find("any_call")) ? 1.f : 0.f;
    v[static_cast<size_t>(off++)] = jBool(obs.find("haitei")) ? 1.f : 0.f;
    v[static_cast<size_t>(off++)] = jBool(obs.find("houtei")) ? 1.f : 0.f;
    v[static_cast<size_t>(off++)] = jBool(obs.find("rinshan")) ? 1.f : 0.f;

    const auto melds = meldsOf(obs);
    const int ownMelds
            = seat < kNPlayersV4 ? static_cast<int>(melds[static_cast<size_t>(seat)].size()) : 0;
    const auto discards = discardsOf(obs);
    const int ownRiver
            = seat < kNPlayersV4 ? static_cast<int>(discards[static_cast<size_t>(seat)].size()) : 0;
    int riichiTurn[kNPlayersV4];
    intsOf(obs.find("riichi_turn"), kNPlayersV4, riichiTurn);
    const std::string drawn = jStr(obs.find("drawn"));
    v[static_cast<size_t>(off++)]
            = static_cast<float>(jInt(obs.find("player_draws"), 0)) / 18.f;
    v[static_cast<size_t>(off++)] = jBool(obs.find("menzen")) ? 1.f : 0.f;
    v[static_cast<size_t>(off++)] = jBool(obs.find("self_riichi")) ? 1.f : 0.f;
    v[static_cast<size_t>(off++)] = jBool(obs.find("furiten")) ? 1.f : 0.f;
    v[static_cast<size_t>(off++)] = static_cast<float>(ownMelds) / 4.f;
    v[static_cast<size_t>(off++)] = startsWithZero(drawn) ? 1.f : 0.f;
    v[static_cast<size_t>(off++)] = static_cast<float>(riichiTurn[seat]) / 18.f;
    v[static_cast<size_t>(off++)] = 0.f;      // 自家舍牌摸切率（v3 的逐张属性，暂留 0）
    v[static_cast<size_t>(off++)] = static_cast<float>(ownRiver) / 18.f;
    v[static_cast<size_t>(off++)] = 0.f;

    // ⚠ `riichi` / `ippatsu` 与 `hand_red` 一样是**布尔数组**（用 `intsOf` 读会整段变 0）——
    //   2026-09-27 修正：C++ 镜像对拍时发现 Java 侧原来写的是 `ObsFeatures.ints`，而 Python
    //   `blocks.ctx_vector` 走真值判断 ⇒ `ctx.seats` 的 riichi/ippatsu 8 个通道两端差 1.0。
    //   当时夹具的 7 个用例恰好都**没人立直**，所以没暴露。现在**三端一致**：
    //   Java `boolFlags` / Python 真值判断 / 本函数 `boolFlagsOf` —— 布尔数组读，
    //   真值 → 1，缺字段或非布尔 → 0。夹具也已重做并带覆盖闸门（`REQUIRED_TAGS` 含 `riichi_any`）。
    int riichi[kNPlayersV4];
    int ippatsu[kNPlayersV4];
    boolFlagsOf(obs.find("riichi"), kNPlayersV4, riichi);
    boolFlagsOf(obs.find("ippatsu"), kNPlayersV4, ippatsu);
    for (int j = 0; j < kNPlayersV4; j++) {
        v[static_cast<size_t>(off + j)] = riichi[(seat + j) % kNPlayersV4] != 0 ? 1.f : 0.f;
    }
    off += kNPlayersV4;
    for (int j = 0; j < kNPlayersV4; j++) {
        v[static_cast<size_t>(off + j)]
                = static_cast<float>(riichiTurn[(seat + j) % kNPlayersV4]) / 18.f;
    }
    off += kNPlayersV4;
    for (int j = 0; j < kNPlayersV4; j++) {
        v[static_cast<size_t>(off + j)] = ippatsu[(seat + j) % kNPlayersV4] != 0 ? 1.f : 0.f;
    }
    off += kNPlayersV4;

    const std::string kind = jStr(obs.find("kind"));
    const std::string calledTile = jStr(obs.find("called_tile"));
    v[static_cast<size_t>(off++)] = kind == "turn" ? 1.f : 0.f;
    v[static_cast<size_t>(off++)] = (!calledTile.empty() && calledTile[0] == '0') ? 1.f : 0.f;
    for (int j = 0; j < kNPlayersV4; j++) {
        v[static_cast<size_t>(off + j)] = 0.f;
    }
    if (kind == "claim" && present(obs.find("from"))) {
        v[static_cast<size_t>(off + floorMod4(jInt(obs.find("from"), 0) - seat))] = 1.f;
    }
    off += kNPlayersV4;
    const std::string wn = jStr(obs.find("win_note"));
    for (int i = 0; i < 2; i++) {
        v[static_cast<size_t>(off++)] = wn == kWinNotesV4[i] ? 1.f : 0.f;
    }
    // reserved 6 维保持 0
}

// ---------------------------------------------------------------- cand
/** `[n][128]` = 基础 88（沿用 v3）+ 派生 8（v3）+ v4 新 3（恒 0）+ 预留 29。 */
bool candMatrix(const FeatureView &view, const std::vector<std::string> &legal,
                std::vector<std::array<float, kCCand>> &out) {
    out.assign(legal.size(), std::array<float, kCCand>{});
    for (size_t i = 0; i < legal.size(); i++) {
        // `Features.candidate(key, perCandidate(view, key))` = 96 列（88 基础 + 8 派生），
        // 这里按 Java `System.arraycopy(base, 0, out[i], 0, min(96, 128))` 拷前 96 列。
        const std::vector<float> base = netCandidateVector(legal[i], perCandidate(view, legal[i]));
        const size_t n = std::min(base.size(), static_cast<size_t>(kCCand));
        for (size_t c = 0; c < n; c++) {
            out[i][c] = base[c];
        }
    }
    return true;
}

// ---------------------------------------------------------------- 消融
bool zeroBlock(const std::string &bid, V4Tensors &t, std::string &err) {
    auto zeroTileChannels = [&t](std::initializer_list<const char *> names) {
        for (const char *ch : names) {
            int off = 0;
            for (int i = 0; i < 26; i++) {
                if (std::strcmp(kTileChannels[i], ch) == 0) {
                    for (int k = 0; k < kKind; k++) {
                        for (int c = 0; c < kTileWidths[i]; c++) {
                            t.tile[static_cast<size_t>(k)][static_cast<size_t>(off + c)] = 0.f;
                        }
                    }
                    break;
                }
                off += kTileWidths[i];
            }
        }
    };
    auto zeroCandCols = [&t](int from, int to) {
        for (auto &row : t.cand) {
            for (int c = from; c < to; c++) {
                row[static_cast<size_t>(c)] = 0.f;
            }
        }
    };
    if (bid == "tile.own") {
        zeroTileChannels({"own_count", "own_aka", "own_drawn"});
    } else if (bid == "tile.per_opp") {
        zeroTileChannels({"river_all", "river_before_riichi", "river_after_riichi",
                          "river_tedashi", "river_tsumogiri", "meld_count"});
    } else if (bid == "tile.safety") {
        zeroTileChannels({"safety_genbutsu", "safety_suji"});
    } else if (bid == "tile.danger") {
        zeroTileChannels({"danger_all", "danger_riichi"});
    } else if (bid == "tile.global") {
        zeroTileChannels({"visible", "unseen", "drawable", "dora_indicator", "is_dora", "is_aka",
                          "is_yakuhai", "is_terminal", "is_honor", "suit_m", "suit_p", "suit_s",
                          "reserved"});
    } else if (bid == "evt.stream") {
        for (auto &row : t.evt) {
            row.fill(0.f);
        }
    } else if (bid == "cand.base") {
        zeroCandCols(0, kCandBase);
    } else if (bid == "cand.derived") {
        zeroCandCols(kCandBase, kCandBase + kCandDerived);
    } else if (bid == "cand.reserved") {
        zeroCandCols(kCandBase + kCandDerived, kCCand);
    } else if (bid.rfind("ctx.", 0) == 0) {
        const std::string group = bid.substr(4);
        const int off = ctxOffOf(group.c_str(), err);
        if (off < 0) {
            err = "块 " + bid + " 没有对应的 ctx 通道组";
            return false;
        }
        int w = 0;
        for (int i = 0; i < 7; i++) {
            if (group == kCtxGroups[i]) {
                w = kCtxWidths[i];
                break;
            }
        }
        for (int j = 0; j < w; j++) {
            t.ctx[static_cast<size_t>(off + j)] = 0.f;
        }
    } else {
        err = "块 " + bid + " 不知道怎么清零";
        return false;
    }
    return true;
}

}  // namespace

// ---------------------------------------------------------------- 总装

bool v4Assemble(const JVal &obs, const std::vector<std::string> &ablate, V4Tensors &out,
                std::string &err) {
    if (const char *bad = layoutError()) {
        err = std::string("v4 布局表不自洽：") + bad;
        return false;
    }
    const int v = jInt(obs.find("v"), 2);
    if (v < kV4ObsVersion) {
        err = "v4 特征需要 obs v" + std::to_string(kV4ObsVersion) + "（拿到 v" + std::to_string(v)
              + "）—— 老轨迹没有 events/riichi_turn，不能静默填 0";
        return false;
    }
    for (const std::string &bid : ablate) {
        if (v4IndexOfBlock(bid) < 0) {
            err = "未注册的块 id：" + bid;
            return false;
        }
    }
    const int seat = jInt(obs.find("seat"), 0);
    if (seat < 0 || seat >= kNPlayersV4) {
        // Java 在这里会 AIOOBE（`scores[seat]`）；C++ 越界是 UB，所以显式报错（同样的"不猜"）。
        err = "obs.seat 越界：" + std::to_string(seat) + "（v4 特征要求 0.."
              + std::to_string(kNPlayersV4 - 1) + "）";
        return false;
    }
    FeatureView view;
    if (!featureViewOfObs(obs, view)) {
        err = "观测形状不对（ObsFeatures.ofObs 失败）—— 这一行的 obs 不是合法观测";
        return false;
    }
    const PerSeatDerived ps = perSeat(view);

    out = V4Tensors{};
    if (!tileMatrix(obs, view, ps, out.tile, err)) {
        return false;
    }
    if (!eventMatrix(obs, out.evt, err)) {
        return false;
    }
    ctxVector(obs, out.ctx);
    out.legal = legalOf(obs);
    if (!candMatrix(view, out.legal, out.cand)) {
        return false;
    }
    for (const std::string &bid : ablate) {
        if (!zeroBlock(bid, out, err)) {
            return false;
        }
    }
    return true;
}

std::string v4FeatureLayout() {
    return "V4Features v" + std::to_string(kV4FeatureVersion) + "（obs>="
           + std::to_string(kV4ObsVersion) + "） tile[" + std::to_string(kKind) + ","
           + std::to_string(kCTile) + "] evt[" + std::to_string(kKEvt) + ","
           + std::to_string(kCEvt) + "] ctx[" + std::to_string(kCCtx) + "] cand[n,"
           + std::to_string(kCCand) + "] blocks=16 fingerprint=" + v4Fingerprint();
}

}  // namespace trainer
