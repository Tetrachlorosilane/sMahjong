// 极简 JSON **写出**（不做解析）—— 口径与 Java `mahjong.util.Json.write` 逐分支一致。
//
// 为什么要自己写而不是拉个库：轨迹（`g<g>.jsonl`）是**逐字节契约**（docs/TRAINER-CPP.md §2）——
// 键序、转义、数字格式、有没有空格，任何一处与 Java 不同都算"不一致"，而 Java 那一侧只有
// 六十行（`Json.writeTo` / `Json.escape`）。照抄最便宜，也最不容易在细节上"看起来差不多"。
//
// 对齐的三条：
//   ① **键序 = 插入序**（Java 用 `LinkedHashMap`）→ 调用方按 Java 的 `Json.obj(...)` 顺序拼；
//   ② 字符串只转义 `"` `\` `\n` `\r` `\t` `\b` `\f` 与 < 0x20（其余原样，非 ASCII 也不转义）；
//   ③ 整值 double 写成**整数**（`(long) d`），其余走最短往返表示 —— 解析回来是同一个 double。
#pragma once

#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace trainer {

/** Java `Json.escape` 的等价物（含外层的两个引号）。 */
inline void jsonEscaped(std::string &out, const std::string &s) {
    out.push_back('"');
    for (const char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x",
                                  static_cast<unsigned>(static_cast<unsigned char>(c)));
                    out += buf;
                } else {
                    out.push_back(c);
                }
        }
    }
    out.push_back('"');
}

inline void jsonStr(std::string &out, const std::string &s) { jsonEscaped(out, s); }
inline void jsonNull(std::string &out) { out += "null"; }
inline void jsonStrOrNull(std::string &out, bool has, const std::string &s) {
    if (has) {
        jsonEscaped(out, s);
    } else {
        out += "null";
    }
}
inline void jsonBool(std::string &out, bool b) { out += b ? "true" : "false"; }
inline void jsonInt(std::string &out, long long v) { out += std::to_string(v); }

/**
 * `Json.writeTo` 的 double 分支：
 * `d == Math.rint(d)` 且有限 → `(long) d`；否则最短往返（= `Double.toString` 的数值口径）。
 *
 * ⚠ 这里只需要**数值**相等：对拍脚本把两边都 `JSON.parse` 之后再比 ——
 * 所以"73.6"与"73.599999999999994"只要解析成同一个 double 就算一致。
 * 用最短表示是为了让 summary.json 肉眼可读（Java 也是最短）。
 */
inline void jsonDouble(std::string &out, double d) {
    if (std::isfinite(d) && d == std::rint(d) && std::fabs(d) < 9.0e18) {
        out += std::to_string(static_cast<long long>(d));
        return;
    }
    char buf[48];
    const std::to_chars_result res = std::to_chars(buf, buf + sizeof(buf), d);
    if (res.ec == std::errc{}) {
        out.append(buf, res.ptr);
        return;
    }
    char fb[48];
    std::snprintf(fb, sizeof(fb), "%.17g", d);
    out += fb;
}

inline void jsonIntArray(std::string &out, const int *p, int n) {
    out.push_back('[');
    for (int i = 0; i < n; i++) {
        if (i > 0) {
            out.push_back(',');
        }
        out += std::to_string(p[i]);
    }
    out.push_back(']');
}

inline void jsonU8Array(std::string &out, const uint8_t *p, int n) {
    out.push_back('[');
    for (int i = 0; i < n; i++) {
        if (i > 0) {
            out.push_back(',');
        }
        out += std::to_string(static_cast<int>(p[i]));
    }
    out.push_back(']');
}

inline void jsonBoolArray(std::string &out, const bool *p, int n) {
    out.push_back('[');
    for (int i = 0; i < n; i++) {
        if (i > 0) {
            out.push_back(',');
        }
        out += p[i] ? "true" : "false";
    }
    out.push_back(']');
}

inline void jsonStrArray(std::string &out, const std::vector<std::string> &v) {
    out.push_back('[');
    for (size_t i = 0; i < v.size(); i++) {
        if (i > 0) {
            out.push_back(',');
        }
        jsonEscaped(out, v[i]);
    }
    out.push_back(']');
}

}  // namespace trainer
