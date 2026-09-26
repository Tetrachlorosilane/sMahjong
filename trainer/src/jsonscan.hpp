// 极简 JSON **读取** —— 与 `jsonw.hpp`（只写不读）成对。
//
// 为什么自己写而不是拉个库：轨迹格式是**逐字节契约**（docs/TRAINER-CPP.md §2），而 `features`
// 是这份契约的**读**方。第三方库会自带第二套"什么算合法 JSON"的口径（数字形式、重复键、
// 转义表），届时"两边读到的东西不一样"会变成最难查的一类差异。这里只做最小子集：
// 对象 / 数组 / 字符串 / 数字 / true / false / null；数字按**整数**保存
// （派生特征用到的量全是整数 —— 张数、巡目、牌种下标）。
#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace trainer {

/** 一个 JSON 值（只读到派生特征需要的那些形状）。 */
struct JVal {
    enum class Type : uint8_t { Null, Bool, Num, Str, Arr, Obj };

    Type type = Type::Null;
    bool boolVal = false;
    long long intVal = 0;
    std::string strVal;
    std::vector<JVal> arr;
    std::vector<std::pair<std::string, JVal>> obj;

    /** 对象取键（不是对象或缺键返回 `nullptr`）。 */
    const JVal *find(const char *key) const {
        if (type != Type::Obj) {
            return nullptr;
        }
        for (const auto &kv : obj) {
            if (kv.first == key) {
                return &kv.second;
            }
        }
        return nullptr;
    }

    bool isArr() const { return type == Type::Arr; }
    bool isObj() const { return type == Type::Obj; }
    bool isStr() const { return type == Type::Str; }

    /** 整数取值（非数字 → `def`；**与 Java `Json.i` 同一口径**：绝不抛）。 */
    int asInt(int def = 0) const { return type == Type::Num ? static_cast<int>(intVal) : def; }

    /** 字符串取值（非字符串 → 空串；调用方的 `parseKind("")` 自然返回 -1）。 */
    const std::string &asStr() const {
        static const std::string kEmpty;
        return type == Type::Str ? strVal : kEmpty;
    }
};

/**
 * 一段 JSON 文本 → 值。**要求整段恰好是一个值**（前后只允许空白）——
 * 失败返回 `false`，调用方按 Java `Json.tryParse` 的 `null` 处理（跳过这一行）。
 */
bool jsonParse(const std::string &text, JVal &out);

}  // namespace trainer
