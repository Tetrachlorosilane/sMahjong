// 训练端 C++ 引擎 —— CLI 入口。
//
//   trainer wall <seed> [aka] [dealer]     打印牌山/配牌/指示牌/岭上（与 tools/WallProbe.java 同格式）
//   trainer rng  <seed> <n>                打印 n 个 java.util.Random.nextInt(136)（RNG 对拍用）
//   trainer --selftest                     内部一致性自检（不做对拍，对拍见 tools/trainer-parity-check.mjs）
//
// 设计约束见 docs/TRAINER-CPP.md：口径以 Java 为准、同种子逐字节等价、发布版服务端不动。
#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "java_rand.hpp"
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

// 只做"自己跟自己"的一致性检查：对拍（与 Java 比）在 tools/trainer-parity-check.mjs。
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
    std::printf(fails == 0 ? "TRAINER SELFTEST PASS\n" : "TRAINER SELFTEST FAIL（%d）\n", fails);
    return fails == 0 ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr,
                     "用法：trainer <wall|rng|--selftest> …\n"
                     "  wall <seed> [aka] [dealer]   牌山/配牌/指示牌/岭上（JSON）\n"
                     "  rng  <seed> <n>              java.util.Random.nextInt(136) 前 n 个\n");
        return 2;
    }
    const std::string cmd = argv[1];
    if (cmd == "wall") {
        return cmdWall(argc, argv);
    }
    if (cmd == "rng") {
        return cmdRng(argc, argv);
    }
    if (cmd == "--selftest") {
        return selftest();
    }
    std::fprintf(stderr, "未知子命令：%s\n", cmd.c_str());
    return 2;
}
