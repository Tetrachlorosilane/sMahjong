// java.util.Random + Collections.shuffle 的**逐位等价**实现。
//
// 为什么必须逐位等价：牌山是 `Collections.shuffle(list, new Random(seed))` 洗出来的
// （server/.../core/Wall.java）。任何一个字节不同，整场牌就换了 —— 而训练数据必须以
// Java 口径为准（见 docs/TRAINER-CPP.md §2 铁律 2）。所以这里照抄 JDK 的算法，
// **不做任何"更漂亮"的优化**：LCG 常数、掩码位数、`nextInt` 的取模拒绝分支都保持原样。
#pragma once

#include <algorithm>
#include <cstdint>
#include <vector>

namespace trainer {

class JavaRandom {
public:
    explicit JavaRandom(int64_t seed) { setSeed(seed); }

    void setSeed(int64_t seed) {
        state_ = (static_cast<uint64_t>(seed) ^ kMultiplier) & kMask;
    }

    // protected int next(int bits)
    int32_t next(int bits) {
        state_ = (state_ * kMultiplier + kAddend) & kMask;
        return static_cast<int32_t>(state_ >> (48 - bits));
    }

    // public int nextInt(int bound) —— 逐分支照抄 JDK（含 bound 为 2 的幂时的乘法快路）
    int32_t nextInt(int32_t bound) {
        if (bound <= 0) {
            return 0;                       // JDK 抛 IllegalArgumentException；内部调用点保证 >0
        }
        int32_t r = next(31);
        const int32_t m = bound - 1;
        if ((bound & m) == 0) {
            r = static_cast<int32_t>((static_cast<int64_t>(bound) * r) >> 31);
            return r;
        }
        int32_t u = r;
        for (;;) {
            r = u % bound;
            if (u - r + m >= 0) {
                return r;
            }
            u = next(31);
        }
    }

    // public long nextLong()（M2 起用到；先按 JDK 的合成方式写死）
    int64_t nextLong() {
        const int64_t hi = static_cast<int64_t>(next(32));
        const int64_t lo = static_cast<int64_t>(next(32));
        return (hi << 32) + lo;
    }

private:
    static constexpr uint64_t kMultiplier = 0x5DEECE66DULL;
    static constexpr uint64_t kAddend = 0xBULL;
    static constexpr uint64_t kMask = (1ULL << 48) - 1;
    uint64_t state_{};
};

// java.util.Collections.shuffle(List, Random) 的 RandomAccess 分支：
//     for (int i = size; i > 1; i--) swap(list, i - 1, rnd.nextInt(i));
template <typename T>
inline void javaShuffle(std::vector<T>& v, JavaRandom& rnd) {
    for (int i = static_cast<int>(v.size()); i > 1; --i) {
        const int j = rnd.nextInt(i);
        std::swap(v[static_cast<size_t>(i - 1)], v[static_cast<size_t>(j)]);
    }
}

}  // namespace trainer
