// **SHA-256（FIPS 180-4）** —— 训练端只要"算一段字节的十六进制摘要"这一个用途。
//
// 为什么自己写而不是引第三方：训练端口径是**零第三方依赖**（`AGENTS.md` §6.5 的同一理由），
// 而本文件最初只是 `v4features.cpp` 里为实现"块清单指纹"抄的一份（与 Java
// `MessageDigest.getInstance("SHA-256")` / Python `hashlib.sha256` 逐位同值）。
// 段 3b 起 `v4policy.cpp` 也要算"**权重整段字节的 sha256**"（`V4Cache` 槽键的一部分，
// `A-REFACTOR-IMPL-PLAN.md` 风险 5）⇒ 抽成具名 namespace 的头文件，两处共用一份实现
// （AGENTS §6.5 判据④：跨头文件引用的类型必须定义在**具名** namespace）。
//
// ⚠ 本文件**不改变**任何已有输出：函数体与原 `v4features.cpp` 里的匿名 struct 逐字相同。
#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>

namespace trainer {

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
            const size_t take = n < sizeof(buf) - bufLen ? n : sizeof(buf) - bufLen;
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

/** 一段字节的 sha256 前 `chars` 个十六进制字符（`chars` 必须是偶数且 ≤ 64）。 */
inline std::string sha256Hex(const void *data, size_t n, int chars = 16) {
    Sha256 s;
    s.update(reinterpret_cast<const uint8_t *>(data), n);
    uint8_t h[32];
    s.finish(h);
    static const char *kHex = "0123456789abcdef";
    std::string out;
    out.reserve(static_cast<size_t>(chars));
    for (int i = 0; i < chars; i++) {
        const uint8_t b = h[i / 2];
        out.push_back(kHex[(i % 2 == 0) ? (b >> 4) : (b & 0x0F)]);
    }
    return out;
}

}  // namespace trainer
