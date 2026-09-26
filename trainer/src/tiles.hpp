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

}  // namespace trainer
