#include "features.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "jsonscan.hpp"
#include "obffeatures.hpp"

namespace trainer {
namespace {

/** "MJFT"。 */
constexpr uint32_t kFeatureMagic = 0x4D4A4654;

struct FeatStat {
    int files = 0;
    long long decisions = 0;
    long long candidates = 0;
    long long bytes = 0;
};

/** `g<序号>.jsonl` 的序号；不是这个形状返回 -1（Java `Files.list` 的 `g\d+\.jsonl` 同口径）。 */
int seqOfName(const std::string &name) {
    if (name.size() < 8 || name[0] != 'g') {
        return -1;
    }
    size_t i = 1;
    long long v = 0;
    while (i < name.size() && name[i] >= '0' && name[i] <= '9') {
        v = v * 10 + (name[i] - '0');
        if (v > 0x7FFFFFFFLL) {
            return -1;                                    // Java `Integer.parseInt` 会抛 → 这里直接不算
        }
        i++;
    }
    if (i == 1) {
        return -1;
    }
    if (name.compare(i, std::string::npos, ".jsonl") != 0) {
        return -1;
    }
    return static_cast<int>(v);
}

/** `g0.jsonl` → `g0.feat.bin`（Java `String.replace` 替换**全部**出现）。 */
std::string featureFileName(const std::string &name) {
    const std::string from = ".jsonl";
    const std::string to = ".feat.bin";
    std::string out;
    size_t start = 0;
    while (true) {
        const size_t pos = name.find(from, start);
        if (pos == std::string::npos) {
            out += name.substr(start);
            break;
        }
        out += name.substr(start, pos - start);
        out += to;
        start = pos + from.size();
    }
    return out;
}

bool readWholeFile(const std::string &path, std::string &out) {
    std::FILE *f = std::fopen(path.c_str(), "rb");
    if (f == nullptr) {
        return false;
    }
    out.clear();
    char buf[1 << 16];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof(buf), f)) > 0) {
        out.append(buf, n);
    }
    const bool ok = std::ferror(f) == 0;
    std::fclose(f);
    return ok;
}

void putI32(std::string &out, uint32_t v) {
    out.push_back(static_cast<char>(v & 0xFF));
    out.push_back(static_cast<char>((v >> 8) & 0xFF));
    out.push_back(static_cast<char>((v >> 16) & 0xFF));
    out.push_back(static_cast<char>((v >> 24) & 0xFF));
}

/** int16 小端（Java `Grow.putShort` 的等价物：先低字节）。 */
void putI16(std::string &out, int v) {
    const uint16_t u = static_cast<uint16_t>(v & 0xFFFF);
    out.push_back(static_cast<char>(u & 0xFF));
    out.push_back(static_cast<char>((u >> 8) & 0xFF));
}

/**
 * 富化单个文件（Java `TraceFeatures.oneFile`）。
 *
 * ⚠ 三段分开攒、最后按 header + A + B + C **顺序**写盘 —— Python 侧才能"每段一次 memmap"
 *   （交错写就得逐行 Python 循环找偏移）。
 */
bool oneFile(const std::string &dir, const std::string &name, FeatStat &st) {
    std::string text;
    if (!readWholeFile(dir + "/" + name, text)) {
        return false;
    }
    std::string secA;
    std::string secB;
    std::string secC;
    long long decisions = 0;
    long long candidates = 0;
    // 按 `\n` 切行（Java `text.split("\n")` 的语义：**最后一段也算一行**，即使没有结尾换行）
    size_t pos = 0;
    while (true) {
        const size_t nl = text.find('\n', pos);
        const size_t end = nl == std::string::npos ? text.size() : nl;
        const std::string line = text.substr(pos, end - pos);
        const bool last = nl == std::string::npos;
        if (last) {
            pos = text.size() + 1;
        } else {
            pos = nl + 1;
        }
        if (line.find_first_not_of(" \t\r\n") == std::string::npos) {
            if (last) {
                break;
            }
            continue;                                     // Java `line.isBlank()`
        }
        JVal row;
        if (!jsonParse(line, row) || !row.isObj()) {
            if (last) {
                break;
            }
            continue;                                     // Java `Json.asObj(Json.tryParse(...))` == null
        }
        const JVal *type = row.find("type");
        const JVal *obs = row.find("obs");
        if (type != nullptr && type->isStr() && type->strVal == "decision" && obs != nullptr
                && obs->isObj()) {
            FeatureView v;
            if (featureViewOfObs(*obs, v)) {
                for (int x : perDecision(v)) {
                    secA.push_back(static_cast<char>(std::max(0, std::min(255, x))));
                }
                const JVal *legal = row.find("legal");
                const int n = legal != nullptr && legal->isArr()
                        ? static_cast<int>(legal->arr.size()) : 0;
                putI16(secB, n);
                if (legal != nullptr && legal->isArr()) {
                    for (const JVal &key : legal->arr) {
                        for (int x : perCandidate(v, key.asStr())) {
                            putI16(secC, x);
                        }
                    }
                }
                decisions++;
                candidates += n;
            }
        }
        if (last) {
            break;
        }
    }
    std::string out;
    out.reserve(20 + secA.size() + secB.size() + secC.size());
    putI32(out, kFeatureMagic);
    putI32(out, static_cast<uint32_t>(kFeatureVersion));
    putI32(out, static_cast<uint32_t>(decisions));
    putI32(out, static_cast<uint32_t>(kPerDecision));
    putI32(out, static_cast<uint32_t>(kPerCandidate));
    out += secA;
    out += secB;
    out += secC;

    const std::string outPath = dir + "/" + featureFileName(name);
    std::FILE *f = std::fopen(outPath.c_str(), "wb");     // 二进制模式；无行尾、无 BOM
    if (f == nullptr) {
        return false;
    }
    const size_t written = std::fwrite(out.data(), 1, out.size(), f);
    const bool ok = written == out.size();
    std::fclose(f);
    if (!ok) {
        return false;
    }
    st.files = 1;
    st.decisions = decisions;
    st.candidates = candidates;
    st.bytes = static_cast<long long>(out.size());
    return true;
}

/** 默认并行度：**≤75% 的核**（与 `docs/TRAINING.md` §0.1.2 的纪律、Java `defaultWorkers` 一致）。 */
int defaultWorkers() {
    const unsigned hw = std::thread::hardware_concurrency();
    if (hw == 0) {
        return 1;
    }
    return std::max(1, static_cast<int>(hw) * 3 / 4);
}

}  // namespace

int featuresCli(int argc, char **argv) {
    // argv[0] = "features"，argv[1] = 轨迹目录
    std::string dir;
    int workers = 0;
    int i = 1;
    if (i < argc && argv[i][0] != '-') {
        dir = argv[i++];
    }
    for (; i < argc; i++) {
        const std::string a = argv[i];
        if (a == "--workers" && i + 1 < argc) {
            workers = std::atoi(argv[++i]);
        } else {
            std::fprintf(stderr, "[trainer] 未知参数：%s\n", a.c_str());
            return 2;
        }
    }
    if (dir.empty()) {
        std::fprintf(stderr, "[trainer] features 需要一个轨迹目录：trainer features <dir> "
                             "[--workers K]\n");
        return 2;
    }
    std::error_code ec;
    if (!std::filesystem::is_directory(dir, ec)) {
        std::fprintf(stderr, "[trainer] 不是目录：%s\n",
                     std::filesystem::absolute(dir, ec).string().c_str());
        return 2;
    }
    // 文件集与顺序：`g<数字>.jsonl`，按序号升序（与 Java 的 `matches` + 数字排序同口径）
    std::vector<std::pair<int, std::string>> files;
    std::filesystem::directory_iterator it(dir, ec);
    if (ec) {
        std::fprintf(stderr, "[trainer] 目录读不了：%s\n", dir.c_str());
        return 2;
    }
    for (const auto &ent : it) {
        if (!ent.is_regular_file(ec)) {
            continue;
        }
        const std::string name = ent.path().filename().string();
        const int seq = seqOfName(name);
        if (seq >= 0) {
            files.emplace_back(seq, name);
        }
    }
    if (files.empty()) {
        std::fprintf(stderr, "[trainer] 目录里没有 g*.jsonl：%s\n",
                     std::filesystem::absolute(dir, ec).string().c_str());
        return 2;
    }
    std::sort(files.begin(), files.end(), [](const auto &a, const auto &b) {
        return a.first < b.first;
    });
    const int w = std::max(1, std::min(workers <= 0 ? defaultWorkers() : workers,
                                       static_cast<int>(files.size())));
    FeatStat total;
    std::mutex mu;
    std::atomic<int> next{0};
    const auto t0 = std::chrono::steady_clock::now();
    std::vector<std::thread> pool;
    pool.reserve(static_cast<size_t>(w));
    for (int t = 0; t < w; t++) {
        pool.emplace_back([&]() {
            int idx;
            while ((idx = next.fetch_add(1)) < static_cast<int>(files.size())) {
                FeatStat st;
                if (oneFile(dir, files[static_cast<size_t>(idx)].second, st)) {
                    std::lock_guard<std::mutex> lk(mu);
                    total.files += st.files;
                    total.decisions += st.decisions;
                    total.candidates += st.candidates;
                    total.bytes += st.bytes;
                } else {
                    std::fprintf(stderr, "[trainer] 富化失败：%s\n",
                                 files[static_cast<size_t>(idx)].second.c_str());
                }
            }
        });
    }
    for (std::thread &t : pool) {
        t.join();
    }
    const double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    std::printf("== 派生特征富化：%d 个轨迹文件 / %lld 条决策 / %lld 个候选，用时 %.1fs（%.1f MB）\n",
                total.files, total.decisions, total.candidates, sec,
                static_cast<double>(total.bytes) / 1024.0 / 1024.0);
    std::printf("   %s\n", featureLayout().c_str());
    if (total.files != static_cast<int>(files.size())) {
        std::fprintf(stderr, "[trainer] 有 %d 个文件没富化成功\n",
                     static_cast<int>(files.size()) - total.files);
        return 2;
    }
    return 0;
}

}  // namespace trainer
