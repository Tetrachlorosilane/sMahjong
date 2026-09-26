#include "jsonscan.hpp"

#include <cstdlib>

namespace trainer {
namespace {

/** 递归下降解析器（不做转义归一之外的任何"容错"：非法就整段失败）。 */
struct Parser {
    const std::string &s;
    size_t i = 0;
    bool ok = true;

    explicit Parser(const std::string &text) : s(text) {}

    void skipWs() {
        while (i < s.size()) {
            const char c = s[i];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                i++;
            } else {
                break;
            }
        }
    }

    void fail() { ok = false; }

    /** 4 位十六进制（`\uXXXX` 的载荷）。 */
    bool hex4(unsigned &out) {
        if (i + 4 > s.size()) {
            return false;
        }
        unsigned v = 0;
        for (int n = 0; n < 4; n++) {
            const char c = s[i + static_cast<size_t>(n)];
            unsigned d;
            if (c >= '0' && c <= '9') {
                d = static_cast<unsigned>(c - '0');
            } else if (c >= 'a' && c <= 'f') {
                d = static_cast<unsigned>(c - 'a' + 10);
            } else if (c >= 'A' && c <= 'F') {
                d = static_cast<unsigned>(c - 'A' + 10);
            } else {
                return false;
            }
            v = (v << 4) | d;
        }
        i += 4;
        out = v;
        return true;
    }

    static void appendUtf8(std::string &out, unsigned cp) {
        if (cp < 0x80) {
            out.push_back(static_cast<char>(cp));
        } else if (cp < 0x800) {
            out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else if (cp < 0x10000) {
            out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else {
            out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        }
    }

    /** 从 `"` 读到收尾 `"`（含 `\uXXXX` 与代理对）。 */
    void parseString(std::string &out) {
        out.clear();
        i++;                                          // 越过开引号
        while (i < s.size()) {
            const unsigned char c = static_cast<unsigned char>(s[i++]);
            if (c == '"') {
                return;
            }
            if (c != '\\') {
                out.push_back(static_cast<char>(c));
                continue;
            }
            if (i >= s.size()) {
                fail();
                return;
            }
            const char e = s[i++];
            switch (e) {
                case '"': out.push_back('"'); break;
                case '\\': out.push_back('\\'); break;
                case '/': out.push_back('/'); break;
                case 'b': out.push_back('\b'); break;
                case 'f': out.push_back('\f'); break;
                case 'n': out.push_back('\n'); break;
                case 'r': out.push_back('\r'); break;
                case 't': out.push_back('\t'); break;
                case 'u': {
                    unsigned cp = 0;
                    if (!hex4(cp)) {
                        fail();
                        return;
                    }
                    if (cp >= 0xD800 && cp <= 0xDBFF && i + 6 <= s.size() && s[i] == '\\'
                            && s[i + 1] == 'u') {
                        const size_t save = i;
                        i += 2;
                        unsigned lo = 0;
                        if (hex4(lo) && lo >= 0xDC00 && lo <= 0xDFFF) {
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        } else {
                            i = save;                 // 不是低代理 → 当作独立的码点
                        }
                    }
                    appendUtf8(out, cp);
                    break;
                }
                default: fail(); return;
            }
        }
        fail();                                       // 没等到收尾引号
    }

    void parseNumber(JVal &v) {
        const size_t start = i;
        if (i < s.size() && (s[i] == '-' || s[i] == '+')) {
            i++;
        }
        while (i < s.size()) {
            const char c = s[i];
            if ((c >= '0' && c <= '9') || c == '.' || c == 'e' || c == 'E' || c == '+'
                    || c == '-') {
                i++;
            } else {
                break;
            }
        }
        if (i == start) {
            fail();
            return;
        }
        const std::string tok = s.substr(start, i - start);
        v.type = JVal::Type::Num;
        // 小数/指数一律截断成整数（本层用到的量全是整数；与 Java `Json.i` 的 intValue 同口径）
        v.intVal = std::strtoll(tok.c_str(), nullptr, 10);
    }

    void parseArr(JVal &v, int depth) {
        v.type = JVal::Type::Arr;
        i++;                                          // '['
        skipWs();
        if (i < s.size() && s[i] == ']') {
            i++;
            return;
        }
        while (ok) {
            JVal item;
            parseValue(item, depth + 1);
            if (!ok) {
                return;
            }
            v.arr.push_back(std::move(item));
            skipWs();
            if (i < s.size() && s[i] == ',') {
                i++;
                continue;
            }
            if (i < s.size() && s[i] == ']') {
                i++;
                return;
            }
            fail();
            return;
        }
    }

    void parseObj(JVal &v, int depth) {
        v.type = JVal::Type::Obj;
        i++;                                          // '{'
        skipWs();
        if (i < s.size() && s[i] == '}') {
            i++;
            return;
        }
        while (ok) {
            skipWs();
            if (i >= s.size() || s[i] != '"') {
                fail();
                return;
            }
            std::string key;
            parseString(key);
            if (!ok) {
                return;
            }
            skipWs();
            if (i >= s.size() || s[i] != ':') {
                fail();
                return;
            }
            i++;
            JVal item;
            parseValue(item, depth + 1);
            if (!ok) {
                return;
            }
            v.obj.emplace_back(std::move(key), std::move(item));
            skipWs();
            if (i < s.size() && s[i] == ',') {
                i++;
                continue;
            }
            if (i < s.size() && s[i] == '}') {
                i++;
                return;
            }
            fail();
            return;
        }
    }

    void parseValue(JVal &v, int depth) {
        if (!ok) {
            return;
        }
        if (depth > 128) {                            // 深嵌套防御（轨迹只有两层）
            fail();
            return;
        }
        skipWs();
        if (i >= s.size()) {
            fail();
            return;
        }
        switch (s[i]) {
            case '{': parseObj(v, depth); return;
            case '[': parseArr(v, depth); return;
            case '"': v.type = JVal::Type::Str; parseString(v.strVal); return;
            case 't':
                if (s.compare(i, 4, "true") == 0) {
                    i += 4;
                    v.type = JVal::Type::Bool;
                    v.boolVal = true;
                    return;
                }
                fail();
                return;
            case 'f':
                if (s.compare(i, 5, "false") == 0) {
                    i += 5;
                    v.type = JVal::Type::Bool;
                    v.boolVal = false;
                    return;
                }
                fail();
                return;
            case 'n':
                if (s.compare(i, 4, "null") == 0) {
                    i += 4;
                    v.type = JVal::Type::Null;
                    return;
                }
                fail();
                return;
            default: parseNumber(v); return;
        }
    }
};

}  // namespace

bool jsonParse(const std::string &text, JVal &out) {
    out = JVal{};
    Parser p(text);
    p.parseValue(out, 0);
    p.skipWs();
    if (!p.ok || p.i != text.size()) {
        out = JVal{};
        return false;
    }
    return true;
}

}  // namespace trainer
