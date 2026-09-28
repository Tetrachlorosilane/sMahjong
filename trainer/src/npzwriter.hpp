// 极简 **`.npz` 写出**（标签侧，训练专用）—— Java `mahjong.train.NpzWriter` 的镜像。
//
// 为什么要 C++ 也写一份：`--selfplay --aux` 一直只有 Java 能产，`producer.py` 里对 C++ 显式报错
// （`--aux` 在 `CPP_MISSING` 里），于是"训练端脱离 Java"就卡在这一步。npz = "一个 zip、成员是 .npy"，
// 照抄比引第三方 zip/npy 库便宜（本工程训练端也尽量零依赖）。
//
// 确定性（与 Java 侧**同三条**，这条是判据的一部分 —— 同种子两份产物要能逐字节比）：
//   · 成员**按插入序**写，且全部 `STORED`（不压缩）；
//   · zip 条目的时间戳钉成 **DOS 1980-01-01 00:00**（`ZipEntry.setTime(0)` 的等价物）——
//     取当前时间的话"同种子产物逐字节相同"在 aux 上永远不成立；
//   · `.npy` 一律 **v1.0**、字典键序固定、头部按 **64 字节**对齐补空格（numpy 的硬要求）。
//
// ⚠ 标签与输入是两套文件：推理路径只读 `g*.feat.bin` / `net.bin`，永不读 `.aux.npz`。
#pragma once

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace trainer {

namespace npzdetail {

/** CRC-32（IEEE 802.3，与 `java.util.zip.CRC32` 同表）。 */
inline uint32_t crc32(const uint8_t *data, size_t n) {
    static uint32_t table[256];
    static bool init = false;
    if (!init) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) {
                c = (c & 1u) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
            }
            table[i] = c;
        }
        init = true;
    }
    uint32_t c = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
        c = table[(c ^ data[i]) & 0xFFu] ^ (c >> 8);
    }
    return c ^ 0xFFFFFFFFu;
}

inline void putU16(std::string &o, uint32_t v) {
    o.push_back(static_cast<char>(v & 0xFFu));
    o.push_back(static_cast<char>((v >> 8) & 0xFFu));
}

inline void putU32(std::string &o, uint32_t v) {
    o.push_back(static_cast<char>(v & 0xFFu));
    o.push_back(static_cast<char>((v >> 8) & 0xFFu));
    o.push_back(static_cast<char>((v >> 16) & 0xFFu));
    o.push_back(static_cast<char>((v >> 24) & 0xFFu));
}

/** 一个小整数按十进制写进字符串（`.npy` 头里的 shape 用）。 */
inline std::string dec(int v) { return std::to_string(v); }

}  // namespace npzdetail

/**
 * 成员名 → `.npy` 字节（`std::vector` 保插入序 = 写出序，与 Java 的 `LinkedHashMap` 同理）。
 */
class NpzWriter {
public:
    /** 一个 `.npy`（v1.0，C 序，64 字节对齐）。`descr` 例如 `"<u1"` / `"<i1"` / `"<i4"`。 */
    static std::string npy(const std::string &descr, const std::vector<int> &shape,
                           const std::string &raw) {
        static const char kMagic[6] = {'\x93', 'N', 'U', 'M', 'P', 'Y'};
        std::string sb = "{'descr': '" + descr + "', 'fortran_order': False, 'shape': (";
        for (size_t i = 0; i < shape.size(); i++) {
            if (i > 0) {
                sb += ", ";
            }
            sb += npzdetail::dec(shape[i]);
        }
        if (shape.size() == 1) {
            sb += ",";                       // numpy 的单元素 tuple 要尾逗号
        }
        sb += "), }";
        const size_t prefix = 6 + 2 + 2;      // magic + major/minor + headerLen(uint16)
        size_t pad = 64 - ((prefix + sb.size() + 1) % 64);
        if (pad == 64) {
            pad = 0;
        }
        sb.append(pad, ' ');
        sb += "\n";
        std::string out;
        out.reserve(prefix + sb.size() + raw.size());
        out.append(kMagic, 6);
        out.push_back(1);                     // major
        out.push_back(0);                     // minor
        npzdetail::putU16(out, static_cast<uint32_t>(sb.size()));
        out += sb;
        out += raw;
        return out;
    }

    /** uint8 / int8：`descr` 决定语义（字节内容一样）。 */
    void putBytes(const std::string &name, const std::string &descr,
                  const std::vector<int> &shape, const std::string &raw) {
        members_.push_back({name, npy(descr, shape, raw)});
    }

    /** int32 小端。 */
    void putI32(const std::string &name, const std::vector<int> &vals,
                const std::vector<int> &shape) {
        std::string raw;
        raw.reserve(vals.size() * 4);
        for (int v : vals) {
            npzdetail::putU32(raw, static_cast<uint32_t>(v));
        }
        members_.push_back({name, npy("<i4", shape, raw)});
    }

    /** UTF-8 JSON 存成 uint8 数组（Python 侧 `npz["meta"].tobytes().decode()` 读）。 */
    void putJson(const std::string &name, const std::string &json) {
        members_.push_back({name, npy("<u1", {static_cast<int>(json.size())}, json)});
    }

    /** 打成 zip：**STORED + 固定时间戳**（与 Java `ZipOutputStream` 同布局）。 */
    std::string toBytes() const {
        // ⚠ JDK `ZipOutputStream` 的两个"自带"细节（不照抄就对不上字节，实测逐项比出来的）：
        //   ① `flags = 0x0800`（EFS：条目名按 UTF-8）+ `version = 10`；
        //   ② 因为 `ZipEntry.setTime(0)` 设了 mtime，JDK 会挂一个 **9 字节的"扩展时间戳"extra**
        //      （`55 54 05 00 01 + 4 字节 mtime` = 'UT' + size=5 + flags=1 + 0），**本地头与
        //      中央目录各挂一次** —— 9 个成员正好差 162 字节（= 9 × 18），就是这么来的。
        static const char kExtra[9] = {0x55, 0x54, 0x05, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00};
        std::string out;
        std::vector<uint32_t> offsets;
        offsets.reserve(members_.size());
        for (const auto &m : members_) {
            const std::string &body = m.second;
            const uint32_t crc = npzdetail::crc32(
                    reinterpret_cast<const uint8_t *>(body.data()), body.size());
            offsets.push_back(static_cast<uint32_t>(out.size()));
            const std::string name = m.first + ".npy";
            npzdetail::putU32(out, 0x04034b50u);          // local file header
            npzdetail::putU16(out, 10);                   // version needed（JDK 写 10）
            npzdetail::putU16(out, 0x0800);               // flags = EFS
            npzdetail::putU16(out, 0);                    // method = STORED
            npzdetail::putU16(out, 0);                    // time = 0（DOS 1980-01-01 00:00）
            npzdetail::putU16(out, 0x0021);               // date = 1980-01-01
            npzdetail::putU32(out, crc);
            npzdetail::putU32(out, static_cast<uint32_t>(body.size()));
            npzdetail::putU32(out, static_cast<uint32_t>(body.size()));
            npzdetail::putU16(out, static_cast<uint32_t>(name.size()));
            npzdetail::putU16(out, 9);                    // extra len
            out += name;
            out.append(kExtra, 9);
            out += body;
        }
        const uint32_t cdStart = static_cast<uint32_t>(out.size());
        for (size_t i = 0; i < members_.size(); i++) {
            const std::string &body = members_[i].second;
            const uint32_t crc = npzdetail::crc32(
                    reinterpret_cast<const uint8_t *>(body.data()), body.size());
            const std::string name = members_[i].first + ".npy";
            npzdetail::putU32(out, 0x02014b50u);          // central directory header
            npzdetail::putU16(out, 10);                   // version made by
            npzdetail::putU16(out, 10);                   // version needed
            npzdetail::putU16(out, 0x0800);               // flags = EFS
            npzdetail::putU16(out, 0);
            npzdetail::putU16(out, 0);
            npzdetail::putU16(out, 0x0021);
            npzdetail::putU32(out, crc);
            npzdetail::putU32(out, static_cast<uint32_t>(body.size()));
            npzdetail::putU32(out, static_cast<uint32_t>(body.size()));
            npzdetail::putU16(out, static_cast<uint32_t>(name.size()));
            npzdetail::putU16(out, 9);                    // extra
            npzdetail::putU16(out, 0);                    // comment
            npzdetail::putU16(out, 0);                    // disk start
            npzdetail::putU16(out, 0);                    // internal attrs
            npzdetail::putU32(out, 0);                    // external attrs
            npzdetail::putU32(out, offsets[i]);
            out += name;
            out.append(kExtra, 9);
        }
        const uint32_t cdSize = static_cast<uint32_t>(out.size()) - cdStart;
        npzdetail::putU32(out, 0x06054b50u);              // end of central directory
        npzdetail::putU16(out, 0);
        npzdetail::putU16(out, 0);
        npzdetail::putU16(out, static_cast<uint32_t>(members_.size()));
        npzdetail::putU16(out, static_cast<uint32_t>(members_.size()));
        npzdetail::putU32(out, cdSize);
        npzdetail::putU32(out, cdStart);
        npzdetail::putU16(out, 0);
        return out;
    }

    /** 成员名清单（自检用；不读内容）。 */
    std::vector<std::string> memberNames() const {
        std::vector<std::string> out;
        for (const auto &m : members_) {
            out.push_back(m.first);
        }
        return out;
    }

    /** 写文件（失败返回 false，不抛）。 */
    bool write(const std::string &path) const {
        const std::string bytes = toBytes();
        std::FILE *f = std::fopen(path.c_str(), "wb");
        if (f == nullptr) {
            return false;
        }
        const size_t n = std::fwrite(bytes.data(), 1, bytes.size(), f);
        std::fclose(f);
        return n == bytes.size();
    }

private:
    std::vector<std::pair<std::string, std::string>> members_;
};

}  // namespace trainer
