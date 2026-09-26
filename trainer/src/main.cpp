// 训练端 C++ 引擎 —— CLI 入口。
//
//   trainer wall   <seed> [aka] [dealer]   打印牌山/配牌/指示牌/岭上（与 tools/WallProbe.java 同格式）
//   trainer rng    <seed> <n>              打印 n 个 java.util.Random.nextInt(136)（RNG 对拍用）
//   trainer rules  <corpus> <mode> <out>   读语料 → 逐行写向听/进张/听牌形（与 tools/RuleProbe.java 对拍）
//                                          mode = shanten | of | discard
//   trainer bench  <corpus> <mode> <reps>  同一语料跑 reps 遍并计时（性能基准）
//   trainer --selftest                     内部一致性自检（含"快表 vs 参考 DFS"交叉验证）
//
// 设计约束见 docs/TRAINER-CPP.md：口径以 Java 为准、同种子逐字节等价、发布版服务端不动。
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "counts.hpp"
#include "handeval.hpp"
#include "java_rand.hpp"
#include "shanten.hpp"
#include "tiles.hpp"
#include "wall.hpp"

namespace {

void printIntArray(const char* name, const std::vector<int>& v, bool last) {
    std::printf("\"%s\":[", name);
    for (size_t i = 0; i < v.size(); i++) {
        std::printf("%s%d", i ? "," : "", v[i]);
    }
    std::printf("]%s", last ? "" : ",");
}

// 与 Java `Round.setup()` 同序配牌：
//   13 巡 × 4 家（从庄家起，每人一张）→ 庄家第 14 张（openingTile）→ finishDealing → 各家按 compareTile 排序
std::vector<std::vector<int>> dealHands(trainer::Wall& wall, int dealer) {
    std::vector<std::vector<int>> hands(4);
    for (int r = 0; r < 13; r++) {
        for (int s = 0; s < 4; s++) {
            hands[static_cast<size_t>((dealer + s) % 4)].push_back(wall.deal());
        }
    }
    const int opening = wall.deal();
    hands[static_cast<size_t>(dealer)].push_back(opening);
    wall.finishDealing();

    // Java `Round.compareTile`：先 kind，再"赤五在前"，最后比 id
    for (auto& h : hands) {
        std::sort(h.begin(), h.end(), [](int a, int b) {
            const int ka = trainer::kindOf(a);
            const int kb = trainer::kindOf(b);
            if (ka != kb) {
                return ka < kb;
            }
            const bool ra = trainer::isRedId(a);
            const bool rb = trainer::isRedId(b);
            if (ra != rb) {
                return ra;
            }
            return a < b;
        });
    }
    return hands;
}

int cmdWall(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "用法：trainer wall <seed> [aka] [dealer]\n");
        return 2;
    }
    const int64_t seed = std::strtoll(argv[2], nullptr, 10);
    const int aka = argc > 3 ? std::atoi(argv[3]) : 3;
    const int dealer = argc > 4 ? std::atoi(argv[4]) : 0;

    // ⚠ 先取王牌副本再配牌：`doraIndicators/uraIndicators` 读的是 dead_，与配牌无关；
    //    岭上要消耗 rinshanPos，所以放在最后。
    trainer::Wall wall(seed, aka);
    const std::vector<int> all = wall.allTiles();
    const std::vector<std::vector<int>> hands = dealHands(wall, dealer);
    const std::vector<int> dora = wall.doraIndicators();
    const std::vector<int> ura = wall.uraIndicators();
    std::vector<int> rinshan;
    for (int i = 0; i < trainer::kRinshanTiles; i++) {
        rinshan.push_back(wall.drawRinshan());
    }

    std::printf("{\"seed\":%lld,\"aka\":%d,\"dealer\":%d,", static_cast<long long>(seed), aka, dealer);
    printIntArray("wall", all, false);
    std::printf("\"hands\":[");
    for (size_t i = 0; i < hands.size(); i++) {
        std::printf("%s[", i ? "," : "");
        for (size_t j = 0; j < hands[i].size(); j++) {
            std::printf("%s%d", j ? "," : "", hands[i][j]);
        }
        std::printf("]");
    }
    std::printf("],");
    printIntArray("dora", dora, false);
    printIntArray("ura", ura, false);
    printIntArray("rinshan", rinshan, true);
    std::printf("}\n");
    return 0;
}

int cmdRng(int argc, char** argv) {
    if (argc < 4) {
        std::fprintf(stderr, "用法：trainer rng <seed> <n>\n");
        return 2;
    }
    const int64_t seed = std::strtoll(argv[2], nullptr, 10);
    const int n = std::atoi(argv[3]);
    trainer::JavaRandom rnd(seed);
    std::printf("[");
    for (int i = 0; i < n; i++) {
        std::printf("%s%d", i ? "," : "", rnd.nextInt(136));
    }
    std::printf("]\n");
    return 0;
}

// 只做"自己跟自己"的一致性检查：对拍（与 Java 比）在 tools/trainer-parity-check.mjs / trainer-rule-parity.mjs。
int selftest() {
    int fails = 0;
    auto check = [&](const char* name, bool ok) {
        std::printf("  %s %s\n", ok ? "[ok]  " : "[FAIL]", name);
        if (!ok) {
            fails++;
        }
    };

    // ① 洗牌是 0..135 的一个排列（且 136 张齐全）
    {
        trainer::Wall w(20260101, 3);
        std::vector<int> t = w.allTiles();
        std::sort(t.begin(), t.end());
        bool perm = t.size() == 136;
        for (int i = 0; i < 136 && perm; i++) {
            perm = t[static_cast<size_t>(i)] == i;
        }
        check("洗牌结果是 0..135 的排列", perm);
    }
    // ② 同一 seed 两次构造完全一致（可复现）
    {
        trainer::Wall a(12345, 3);
        trainer::Wall b(12345, 3);
        check("同 seed 两次洗牌逐张相同", a.allTiles() == b.allTiles());
    }
    // ③ aka == 0 时没有赤五；aka == 3 时恰好 3 张
    {
        int red0 = 0;
        trainer::Wall w0(777, 0);
        for (int id : w0.allTiles()) {
            if (trainer::isRedId(id)) {
                red0++;
            }
        }
        int red3 = 0;
        trainer::Wall w3(777, 3);
        for (int id : w3.allTiles()) {
            if (trainer::isRedId(id)) {
                red3++;
            }
        }
        check("aka=0 → 无赤五", red0 == 0);
        check("aka=3 → 恰好 3 张赤五", red3 == 3);
    }
    // ④ 配牌 53 张 + 岭上账：tilesLeft 从 122−53=69 起步，开杠只动 liveEnd
    {
        trainer::Wall w(20260101, 3);
        (void)dealHands(w, 0);
        const int before = w.tilesLeft();
        w.onKan();
        const int after = w.tilesLeft();
        const int rinshan0 = w.rinshanLeft();
        (void)w.drawRinshan();
        check("配牌后余 69 张可摸", before == 69);
        check("开杠 → tilesLeft 减 1", after == before - 1);
        check("岭上摸牌不动 tilesLeft", w.tilesLeft() == after);
        check("岭上摸牌只减 rinshanLeft", w.rinshanLeft() == rinshan0 - 1);
    }
    // ⑤ 快表向听 == 参考 DFS（Java `Shanten.dfs` 的逐行移植）：随机手牌交叉验证
    {
        trainer::JavaRandom rnd(20260101);
        int bad = 0;
        long cases = 0;
        for (int t = 0; t < 20000; t++) {            const int mc = t % 5;
            const int size = 13 - 3 * mc + (t % 3 == 0 ? 1 : 0);   // 13/14 张两种
            trainer::Counts c{};
            int placed = 0;
            while (placed < size) {
                const int k = rnd.nextInt(trainer::kKindCount);
                if (c[static_cast<size_t>(k)] >= 4) {
                    continue;
                }
                c[static_cast<size_t>(k)]++;
                placed++;
            }
            cases++;
            if (trainer::shantenStandard(c, mc) != trainer::shantenStandardRef(c, mc)) {
                bad++;
            }
        }
        check("快表向听 == 参考 DFS（20000 手 × 副露 0~4）", bad == 0 && cases == 20000);
    }
    // ⑥ 七对子 / 国士 / 一般型的定点值（与 Java 同口径，手算可验）
    {
        // 七对子 6 对 + 1 单张 → 0 向听（听牌）
        trainer::Counts chiitoi{};
        for (int k = 0; k < 6; k++) {
            chiitoi[static_cast<size_t>(k)] = 2;
        }
        chiitoi[10] = 1;
        // 国士 12 种 + 1 对（13 张）：11 种各 1 张 + 1m 一对
        trainer::Counts kokushi{};
        for (int k : {8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32}) {
            kokushi[static_cast<size_t>(k)] = 1;
        }
        kokushi[0] = 2;
        check("七对子 6 对 + 单张 → 听牌（min = 0）", trainer::shantenMin(chiitoi, 0) == 0);
        check("国士 12 种 + 1 对 → 听牌（min = 0）", trainer::shantenMin(kokushi, 0) == 0);
        check("七对子向听公式：6 对 + 1 单 → 0", trainer::shantenChiitoi(chiitoi) == 0);
        check("国士向听公式：12 种 + 对 → 0", trainer::shantenKokushi(kokushi) == 0);
        // 三种牌型的**和了形**（14 张）都要是 -1：钉住"min 只在 meldCount == 0 时才合并特型"
        trainer::Counts winStd{};                           // 111m 555p 111z 555z + 99m（14 张）
        winStd[0] = 3;                                      // 1m 刻
        winStd[13] = 3;                                     // 5p 刻
        winStd[27] = 3;                                     // 1z 刻
        winStd[31] = 3;                                     // 5z 刻
        winStd[8] = 2;                                      // 9m 雀头
        trainer::Counts winChiitoi{};
        for (int k = 0; k < 7; k++) {
            winChiitoi[static_cast<size_t>(k)] = 2;
        }
        trainer::Counts winKokushi{};
        for (int k : {0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33}) {
            winKokushi[static_cast<size_t>(k)] = 1;
        }
        winKokushi[0] = 2;
        // 只可能是七对子的手（七对：奇数牌的孤对，做不成一般型）—— 用来验"有副露就不再看特型"
        trainer::Counts chiitoiOnly{};
        for (int k : {0, 2, 4, 6, 8, 9, 11}) {
            chiitoiOnly[static_cast<size_t>(k)] = 2;
        }
        check("一般型和了形 → -1（14 张）", trainer::shantenMin(winStd, 0) == -1);
        check("七对子和了形 → -1", trainer::shantenMin(winChiitoi, 0) == -1);
        check("国士和了形 → -1", trainer::shantenMin(winKokushi, 0) == -1);
        check("有副露时不再看七对子/国士",
              trainer::shantenMin(chiitoiOnly, 0) == -1 && trainer::shantenMin(chiitoiOnly, 1) != -1);
    }
    // ⑦ 听牌形：两面 / 嵌张 / 边张 / 双碰 / 单骑 各一个定点
    {
        // 234m 56m + 789p + 111s + 22z（13 张）→ 听 4m/7m（两面）
        trainer::Counts a{};
        a[1] = 1; a[2] = 1; a[3] = 1;      // 2m3m4m
        a[4] = 1; a[5] = 1;                // 5m6m
        for (int k : {15, 16, 17}) {
            a[static_cast<size_t>(k)] = 1; // 7p8p9p
        }
        a[18] = 3;                         // 1s1s1s
        a[28] = 2;                         // 2z2z
        trainer::Counts a14 = a;
        a14[3]++;                          // 摸 4m → 和了
        const int shape4m = trainer::bestWaitType(a14, 0, 3);
        a14 = a;
        a14[6]++;                          // 摸 7m → 和了
        const int shape7m = trainer::bestWaitType(a14, 0, 6);
        check("234m56m 听 4m → 两面", shape4m == trainer::kWaiRyanmen);
        check("234m56m 听 7m → 两面", shape7m == trainer::kWaiRyanmen);
        // ⚠ 234m56m 是**三面**听（1m/4m/7m），不是两面——别按"两搭子"直觉写成 {4m,7m}
        check("waits() 恰好 {1m,4m,7m}", trainer::waits(a, 0) == std::vector<int>({0, 3, 6}));
    }

    std::printf(fails == 0 ? "TRAINER SELFTEST PASS\n" : "TRAINER SELFTEST FAIL（%d）\n", fails);
    return fails == 0 ? 0 : 1;
}

// ---------------------------------------------------------------- 语料对拍 / 基准
//
// 语料格式（`tools/trainer-rule-parity.mjs` 生成）：
//   每行 69 个整数：`meldCount c0..c33 v0..v33`（c = 暗手牌计数，v = 可见牌计数）
// 输出（`mode` 决定）：
//   shanten → `shanten`
//   of      → `shanten tenpai advanceTypes advanceTiles waitTypes waitTiles goodWT goodTiles advHash shapeHash`
//   discard → 对 14 张语料的每个可打牌种一行：`k <同 of 的十个字段>`
// `*Hash` 是两个 34 维数组的 FNV-1a（把"数组级"差异也压进同一条判据里）。
struct Row {
    int mc = 0;
    trainer::Counts c{};
    trainer::Counts v{};
};

std::vector<Row> readCorpus(const char* path) {
    std::vector<Row> rows;
    std::FILE* f = std::fopen(path, "rb");
    if (f == nullptr) {
        std::fprintf(stderr, "读不到语料：%s\n", path);
        std::exit(2);
    }
    char line[4096];
    while (std::fgets(line, sizeof(line), f) != nullptr) {
        int vals[69];
        int n = 0;
        char* p = line;
        while (n < 69) {
            char* end = nullptr;
            const long x = std::strtol(p, &end, 10);
            if (end == p) {
                break;
            }
            vals[n++] = static_cast<int>(x);
            p = end;
        }
        if (n < 69) {
            continue;
        }
        Row r;
        r.mc = vals[0];
        for (int k = 0; k < trainer::kKindCount; k++) {
            r.c[static_cast<size_t>(k)] = static_cast<uint8_t>(vals[1 + k]);
            r.v[static_cast<size_t>(k)] = static_cast<uint8_t>(vals[35 + k]);
        }
        rows.push_back(r);
    }
    std::fclose(f);
    return rows;
}

inline uint32_t fnv1a(const uint8_t* p, size_t n, uint32_t h = 2166136261u) {
    for (size_t i = 0; i < n; i++) {
        h ^= p[i];
        h *= 16777619u;
    }
    return h;
}

void writeSnapshot(std::FILE* out, const trainer::Snapshot& s, int k) {
    const uint32_t ha = fnv1a(s.advanceKinds.data(), s.advanceKinds.size());
    uint8_t shapes[34];
    for (int i = 0; i < trainer::kKindCount; i++) {
        shapes[i] = static_cast<uint8_t>(s.waitShapes[static_cast<size_t>(i)]);
    }
    const uint32_t hs = fnv1a(shapes, sizeof(shapes));
    if (k < 0) {
        std::fprintf(out, "%d %d %d %d %d %d %d %d %u %u\n", s.shanten, s.tenpai ? 1 : 0,
                     s.advanceTypes, s.advanceTiles, s.waitTypes, s.waitTiles, s.goodWaitTypes,
                     s.goodWaitTiles, ha, hs);
    } else {
        std::fprintf(out, "%d %d %d %d %d %d %d %d %d %u %u\n", k, s.shanten, s.tenpai ? 1 : 0,
                     s.advanceTypes, s.advanceTiles, s.waitTypes, s.waitTiles, s.goodWaitTypes,
                     s.goodWaitTiles, ha, hs);
    }
}

// argv 已被 main() 前移一位（argv[0] == 子命令名），所以 corpus 在 argv[1]、mode 在 argv[2]。
int cmdRules(int argc, char** argv, bool bench) {
    const int need = bench ? 3 : 4; // bench: <corpus> <mode> [reps] · rules: <corpus> <mode> <out>
    if (argc < need) {
        std::fprintf(stderr, bench ? "用法：trainer bench <corpus> <shanten|of|discard> [reps]\n"
                                   : "用法：trainer rules <corpus> <shanten|of|discard> <out>\n");
        return 2;
    }
    const std::string mode = argv[2];
    const std::vector<Row> rows = readCorpus(argv[1]);
    std::FILE* out = stdout;
    int reps = 1;
    if (bench) {
        reps = argc > 3 ? std::atoi(argv[3]) : 1;
        if (reps < 1) {
            reps = 1;
        }
    } else {
        out = std::fopen(argv[3], "wb");
        if (out == nullptr) {
            std::fprintf(stderr, "写不了输出：%s\n", argv[3]);
            return 2;
        }
    }

    const auto t0 = std::chrono::steady_clock::now();
    long long counter = 0;
    for (int rep = 0; rep < reps; rep++) {
        for (const Row& r : rows) {
            if (mode == "shanten") {
                const int sh = trainer::shantenMin(r.c, r.mc);
                counter += sh;
                if (!bench) {
                    std::fprintf(out, "%d\n", sh);
                }
            } else if (mode == "of") {
                const trainer::Snapshot s = trainer::evalOf(r.c, r.mc, r.v);
                counter += s.shanten + s.advanceTypes + s.waitTypes;
                if (!bench) {
                    writeSnapshot(out, s, -1);
                }
            } else if (mode == "discard") {
                for (int k = 0; k < trainer::kKindCount; k++) {
                    if (r.c[static_cast<size_t>(k)] == 0) {
                        continue;
                    }
                    const trainer::Snapshot s = trainer::evalAfterDiscard(r.c, r.mc, k, r.v);
                    counter += s.shanten + s.advanceTypes + s.waitTypes;
                    if (!bench) {
                        writeSnapshot(out, s, k);
                    }
                }
            } else {
                std::fprintf(stderr, "未知 mode：%s\n", mode.c_str());
                return 2;
            }
        }
    }
    const double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    if (out != stdout) {
        std::fclose(out);
    }
    std::fprintf(stderr, "[trainer] %s 语料 %zu 行 × %d 遍，用时 %.3f s（%.0f 行/秒） 校验和 %lld\n",
                 mode.c_str(), rows.size(), reps, sec, rows.size() * static_cast<double>(reps) / sec,
                 counter);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr,
                     "用法：trainer <wall|rng|rules|bench|--selftest> …\n"
                     "  wall  <seed> [aka] [dealer]      牌山/配牌/指示牌/岭上（JSON）\n"
                     "  rng   <seed> <n>                 java.util.Random.nextInt(136) 前 n 个\n"
                     "  rules <corpus> <mode> <out>      语料 → 逐行结果（mode = shanten|of|discard）\n"
                     "  bench <corpus> <mode> <reps>     同语料计时（性能基准）\n");
        return 2;
    }
    const std::string cmd = argv[1];
    if (cmd == "wall") {
        return cmdWall(argc, argv);
    }
    if (cmd == "rng") {
        return cmdRng(argc, argv);
    }
    if (cmd == "rules") {
        return cmdRules(argc - 1, argv + 1, false);
    }
    if (cmd == "bench") {
        return cmdRules(argc - 1, argv + 1, true);
    }
    if (cmd == "--selftest") {
        return selftest();
    }
    std::fprintf(stderr, "未知子命令：%s\n", cmd.c_str());
    return 2;
}
