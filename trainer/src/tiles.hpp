// 牌码编码 —— 与 Java `mahjong.core.Tiles` **同一套**（改这里等于改协议，别动）。
//
//   id = (kind << 2) | copy        kind ∈ [0,34)  copy ∈ [0,4)
//   赤五 = kind ∈ {4(5m), 13(5p), 22(5s)} 且 copy == 0
//
// 为什么把编码单独拎出来：整条链（牌山/手牌/牌河/副露/动作键/报文/特征）都建立在这个
// 双射上；两端只要差一位，就会出现"牌数对、内容差一张"那种最难查的错位。
#pragma once

#include <array>
#include <cstdint>
#include <string>

namespace trainer {

inline constexpr int kKindCount = 34;
inline constexpr int kTileCount = 136;
inline constexpr int kAkaM = 4;    // 赤 5m 的 kind
inline constexpr int kAkaP = 13;   // 赤 5p
inline constexpr int kAkaS = 22;   // 赤 5s

inline constexpr int kindOf(int id) { return id >> 2; }
inline constexpr int copyOf(int id) { return id & 3; }
inline constexpr int idOf(int kind, int copy) { return (kind << 2) | (copy & 3); }

inline constexpr bool isRedId(int id) {
    const int k = kindOf(id);
    return (k == kAkaM || k == kAkaP || k == kAkaS) && (id & 3) == 0;
}

// 与 Java `Tiles.kindToStr(kind, red)` 逐字一致（赤五写 0，如 "0m"）
inline std::string kindToStr(int kind, bool red) {
    if (kind < 0 || kind >= kKindCount) {
        return "??";
    }
    if (kind < 27) {
        const int suit = kind / 9;
        const int num = kind % 9 + 1;
        const char sc = suit == 0 ? 'm' : (suit == 1 ? 'p' : 's');
        if (red && num == 5) {
            return std::string("0") + sc;
        }
        return std::to_string(num) + sc;
    }
    static const std::array<const char*, 7> kHonors = {"1z", "2z", "3z", "4z", "5z", "6z", "7z"};
    return kHonors[static_cast<size_t>(kind - 27)];
}

inline std::string tileToStr(int id) { return kindToStr(kindOf(id), isRedId(id)); }

// ---------------------------------------------------------------- 种类判定（与 Java `Tiles` 同名同义）

inline constexpr int suitOf(int kind) { return kind < 27 ? kind / 9 : -1; }     // 0=m 1=p 2=s，字牌 -1
inline constexpr int numOf(int kind) { return kind < 27 ? kind % 9 + 1 : kind - 26; }
inline constexpr bool isHonor(int kind) { return kind >= 27; }
inline constexpr bool isWind(int kind) { return kind >= 27 && kind <= 30; }
inline constexpr bool isDragon(int kind) { return kind >= 31 && kind <= 33; }
inline constexpr bool isTerminal(int kind) { return kind < 27 && (kind % 9 == 0 || kind % 9 == 8); }
inline constexpr bool isYaochu(int kind) { return isHonor(kind) || isTerminal(kind); }
inline constexpr bool isSimple(int kind) { return !isYaochu(kind); }

/** 绿一色用牌：2s3s4s6s8s + 发(6z)。 */
inline constexpr bool isGreen(int kind) {
    return kind == 19 || kind == 20 || kind == 21 || kind == 23 || kind == 25 || kind == 32;
}

/** 指示牌 → 宝牌 kind（与 Java `Tiles.doraFrom` 逐分支一致）。 */
inline constexpr int doraFrom(int indicatorKind) {
    if (indicatorKind < 27) {
        const int suit = indicatorKind / 9;
        const int num = indicatorKind % 9;
        return suit * 9 + (num + 1) % 9;
    }
    if (indicatorKind < 31) {
        return 27 + (indicatorKind - 27 + 1) % 4;
    }
    return 31 + (indicatorKind - 31 + 1) % 3;
}

/** 解析 kind 字符串（可带赤：0m/0p/0s）；非法返回 -1。 */inline int parseKind(const std::string &s) {
    if (s.size() != 2) {
        return -1;
    }
    const char c0 = s[0];
    const char c1 = s[1];
    if (c0 == '0') {
        if (c1 == 'm') {
            return kAkaM;
        }
        if (c1 == 'p') {
            return kAkaP;
        }
        if (c1 == 's') {
            return kAkaS;
        }
        return -1;
    }
    if (c0 < '1' || c0 > '9') {
        return -1;
    }
    const int n = c0 - '0';
    switch (c1) {
        case 'm': return n - 1;
        case 'p': return 9 + n - 1;
        case 's': return 18 + n - 1;
        case 'z': return (n >= 1 && n <= 7) ? 27 + n - 1 : -1;
        default: return -1;
    }
}

/** 中文名（日志/调试/役种名拼装用）—— 与 Java `Tiles.cnName` 逐字一致。 */
inline std::string cnName(int kind) {
    if (kind < 0 || kind >= kKindCount) {
        return "?";
    }
    static const std::array<const char *, 9> kNums = {"一", "二", "三", "四", "五", "六", "七", "八", "九"};
    if (kind < 9) {
        return std::string(kNums[static_cast<size_t>(kind % 9)]) + "万";
    }
    if (kind < 18) {
        return std::string(kNums[static_cast<size_t>(kind % 9)]) + "筒";
    }
    if (kind < 27) {
        return std::string(kNums[static_cast<size_t>(kind % 9)]) + "索";
    }
    static const std::array<const char *, 7> kHonors = {"东", "南", "西", "北", "白", "发", "中"};
    return kHonors[static_cast<size_t>(kind - 27)];
}

}  // namespace trainer
