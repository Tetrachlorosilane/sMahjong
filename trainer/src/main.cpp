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

#include "action.hpp"
#include "claimoptions.hpp"
#include "agari.hpp"
#include "counts.hpp"
#include "evaluator.hpp"
#include "features.hpp"
#include "furiten.hpp"
#include "handeval.hpp"
#include "java_rand.hpp"
#include "meld.hpp"
#include "payments.hpp"
#include "roundclaims.hpp"
#include "roundoptions.hpp"
#include "roundscoring.hpp"
#include "roundstate.hpp"
#include "rules.hpp"
#include "seed.hpp"
#include "selfplay.hpp"
#include "shanten.hpp"
#include "tiles.hpp"
#include "turnoptions.hpp"
#include "visible.hpp"
#include "wall.hpp"
#include "yaku_codes.hpp"

namespace {

void printIntArray(const char* name, const std::vector<int>& v, bool last) {
    std::printf("\"%s\":[", name);
    for (size_t i = 0; i < v.size(); i++) {
        std::printf("%s%d", i ? "," : "", v[i]);
    }
    std::printf("]%s", last ? "" : ",");
}

// 与 Java `Round.setup()` **同序**配牌（⚠ 2026-09 修正：原来是"13 巡 × 4 家 × 1 张"，
// 那是错的 —— Java 是"3 轮 × 4 家 × **每人一次抓 4 张**"，再各补 1 张，最后庄家第 14 张）：
//   3 轮 × 4 家 × 4 张（共 12）→ 每人再 1 张（13）→ 庄家第 14 张（openingTile）→ finishDealing → 排序
std::vector<std::vector<int>> dealHands(trainer::Wall& wall, int dealer) {
    std::vector<std::vector<int>> hands(4);
    for (int r = 0; r < 3; r++) {
        for (int s = 0; s < 4; s++) {
            auto& h = hands[static_cast<size_t>((dealer + s) % 4)];
            for (int t = 0; t < 4; t++) {
                h.push_back(wall.deal());
            }
        }
    }
    for (int s = 0; s < 4; s++) {
        hands[static_cast<size_t>((dealer + s) % 4)].push_back(wall.deal());
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

    // ⑧ 打点定点（**手算可验**，不依赖 Java 探针）：役种 / 符数 / 基本点 / 授受
    {
        // 手牌串 → (牌 id 列表, 34 维计数)；同种牌按 copy 0..3 顺序发（赤五因此自然出现）
        const auto parseHand = [](const std::string &s, std::vector<int> &ids, trainer::Counts &c) {
            int nextCopy[trainer::kKindCount] = {0};
            std::vector<int> digits;
            for (char ch : s) {
                if (ch >= '0' && ch <= '9') {
                    digits.push_back(ch - '0');
                    continue;
                }
                const int base = ch == 'm' ? 0 : (ch == 'p' ? 9 : (ch == 's' ? 18 : (ch == 'z' ? 27 : -1)));
                if (base >= 0) {
                    for (int d : digits) {
                        const int k = base + d - 1;
                        if (k >= 0 && k < trainer::kKindCount
                                && nextCopy[k] < 4) {
                            ids.push_back(trainer::idOf(k, nextCopy[k]));
                            c[static_cast<size_t>(k)]++;
                            nextCopy[k]++;
                        }
                    }
                }
                digits.clear();
            }
        };
        // 一次"门清 + M.League 默认规则"的评价（winKind 取手牌串里指定的那张）
        const auto evalHand = [&](const std::string &hand, int winKind, bool tsumo, int seat,
                                  int dealer) {
            trainer::WinContext ctx;
            std::vector<int> ids;
            trainer::Counts c{};
            parseHand(hand, ids, c);
            ctx.rules.applyPreset("mleague");
            ctx.seat = seat;
            ctx.dealerSeat = dealer;
            ctx.roundWind = 27;
            ctx.tsumo = tsumo;
            ctx.winKind = winKind;
            ctx.menzen = true;
            ctx.allTileIds = ids;
            return trainer::evaluate(ctx, c, {}, winKind);
        };

        // 平和 + 门前清自摸和：20 符 2 番 → 基本点 20 × 2^4 = 320
        // ⚠ 手牌里**不要有 5m/5p/5s**：`parseHand` 从 copy 0 发牌，而 copy 0 就是赤五 → 会多算赤宝牌
        {
            const trainer::HandScore s = evalHand("123m678m234p678p44s", trainer::parseKind("6m"),
                                                  true, 1, 0);
            check("平和+门清自摸：2 番 20 符 320 点",
                  s.valid && s.han == 2 && s.fu == 20 && s.base == 320 && s.hanYaku == 2);
        }
        // 无役：荣和且没有役 → invalid / reason = 无役
        {
            const trainer::HandScore s = evalHand("111m456m789p234s55z", trainer::parseKind("4m"),
                                                 false, 1, 0);
            check("无役荣和 → invalid（无役）", !s.valid && s.reason == "无役" && s.base == 0);
        }
        // 七对子 + 混老头（全幺九）：4 番 25 符 → 25 × 2^6 = 1600
        {
            const trainer::HandScore s = evalHand("11m99m11p99p11s99s11z", trainer::parseKind("1z"),
                                                 false, 1, 0);
            check("七对子+混老头：4 番 25 符 1600 点",
                  s.valid && s.han == 4 && s.fu == 25 && s.base == 1600);
        }
        // 大三元：役满 1 倍 → 基本点 8000、limit = 役满、番数按 13 折算
        {
            const trainer::HandScore s = evalHand("234m55m555z666z777z", trainer::parseKind("5z"),
                                                 false, 0, 0);
            check("大三元：役满 1 倍 / 8000 点", s.valid && s.yakuman == 1 && s.base == 8000
                    && s.hanYaku == 13 && s.limit == "役满");
            // 授受：庄家荣和役满 = 48000
            const trainer::PaymentResult p = trainer::paymentsCompute(s, 0, 1, 0, 0, 0, false, {});
            check("庄家荣和役满：放铳者 −48000", p.delta[1] == -48000 && p.delta[0] == 48000);
            // 授受：闲家荣和役满 = 32000
            const trainer::PaymentResult p2 = trainer::paymentsCompute(s, 1, 2, 0, 0, 0, false, {});
            check("闲家荣和役满：放铳者 −32000", p2.delta[2] == -32000 && p2.delta[1] == 32000);
            // 授受：闲家自摸役满 = 庄家 16000 + 闲家各 8000
            const trainer::PaymentResult p3 = trainer::paymentsCompute(s, 1, -1, 0, 0, 0, true, {});
            check("闲家自摸役满：16000 / 8000 / 8000",
                  p3.delta[0] == -16000 && p3.delta[2] == -8000 && p3.delta[3] == -8000
                  && p3.delta[1] == 32000);
            // 授受：1 本场荣和 → 放铳者多付 300
            const trainer::PaymentResult p4 = trainer::paymentsCompute(s, 1, 2, 0, 1, 0, false, {});
            check("1 本场荣和：放铳者 −32300", p4.delta[2] == -32300 && p4.delta[1] == 32300);
            // 授受：1 根立直棒归和牌者
            const trainer::PaymentResult p5 = trainer::paymentsCompute(s, 1, 2, 0, 0, 1, false, {});
            check("1 根立直棒：和牌者 +33000", p5.delta[1] == 33000 && p5.riichiTaken == 1000);
        }
        // 不听罚符：3000 / 1 家听牌 → 听牌者 +3000、三家各 −1000（收付严格相等）
        {
            const std::array<bool, 4> tenpai = {true, false, false, false};
            const std::array<int, 4> d = trainer::notenPenalty(tenpai, 3000);
            check("不听罚符 3000 / 1 家听：+3000 与 −1000×3",
                  d[0] == 3000 && d[1] == -1000 && d[2] == -1000 && d[3] == -1000);
            // 非 100 整除的总量也不能凭空生灭（AUDIT F11）
            const std::array<bool, 4> t3 = {true, true, true, false};
            const std::array<int, 4> d3 = trainer::notenPenalty(t3, 1000);
            int sum = 0;
            for (int v : d3) {
                sum += v;
            }
            check("不听罚符 1000 / 3 家听：收付和为 0", sum == 0 && d3[0] == 300 && d3[3] == -900);
        }
    }

    // ⑨ 精算 / 连庄判据 / 种子链的定点（**手算与文档例子可验**，不依赖 Java 探针）
    {
        // 文档《日本麻将.md》§精算点数 的例子：53600/28600/20000/−2200
        {
            const std::array<int, 4> scores = {53600, 28600, 20000, -2200};
            trainer::Rules ml;
            ml.applyPreset("mleague");
            const trainer::Settlement s = trainer::settle(scores, ml);
            check("M.League 精算：+73.6 / +8.6 / −20 / −62.2",
                  std::llround(s.point[0] * 10) == 736 && std::llround(s.point[1] * 10) == 86
                  && std::llround(s.point[2] * 10) == -200 && std::llround(s.point[3] * 10) == -622);
            trainer::Rules ms;
            ms.applyPreset("majsoul");
            const trainer::Settlement s2 = trainer::settle(scores, ms);
            check("《雀魂》精算：+43.6 / +8.6 / −10 / −42.2",
                  std::llround(s2.point[0] * 10) == 436 && std::llround(s2.point[2] * 10) == -100
                  && std::llround(s2.point[3] * 10) == -422);
        }
        // 同点拆分：三家同分时尾数归**更接近起家**者（0.1 分单位）
        {
            const std::array<int, 4> scores = {30000, 30000, 30000, 10000};
            trainer::Rules ml;
            ml.applyPreset("mleague");
            const trainer::Settlement s = trainer::settle(scores, ml);
            check("三家同点：同顺位（rank 0/0/0/3）", s.rank[0] == 0 && s.rank[1] == 0
                    && s.rank[2] == 0 && s.rank[3] == 3);
            check("三家同点：头名赏尾数归更接近起家者（6.8/6.6/6.6）",
                  std::llround(s.oka[0] * 10) == 68 && std::llround(s.oka[1] * 10) == 66
                  && std::llround(s.oka[2] * 10) == 66);
            check("三家同点：顺位点 16.8/16.6/16.6/−50",
                  std::llround(s.point[0] * 10) == 168 && std::llround(s.point[1] * 10) == 166
                  && std::llround(s.point[3] * 10) == -500);
        }
        // 四家同点：马点为 0、头名赏四人均分 → 顺位点全 0
        {
            const std::array<int, 4> scores = {25000, 25000, 25000, 25000};
            trainer::Rules ml;
            ml.applyPreset("mleague");
            const trainer::Settlement s = trainer::settle(scores, ml);
            check("四家同点 25000：顺位点全 0", s.point[0] == 0.0 && s.point[1] == 0.0
                    && s.point[2] == 0.0 && s.point[3] == 0.0);
        }
        // 终局余棒：四家同点的 1 根立直棒 → 400/200/200/200（100 点为单位 + 尾数归起家）
        {
            const std::array<int, 4> scores = {25000, 25000, 25000, 25000};
            const std::array<int, 4> add = trainer::endGameSticks(scores, 1);
            check("终局余棒 1 根 / 四家同点：400/200/200/200",
                  add[0] == 400 && add[1] == 200 && add[2] == 200 && add[3] == 200);
            const std::array<int, 4> add2 =
                    trainer::endGameSticks(std::array<int, 4>{30000, 29000, 20000, 1000}, 2);
            check("终局余棒 2 根 / 无并列：全给 1 位", add2[0] == 2000 && add2[1] == 0);
        }
        // 本场数真值表（+1 / 清零）
        {
            check("本场数：连庄 +1", trainer::nextHonba(2, true, true, false) == 3);
            check("本场数：荒牌流局轮庄也 +1", trainer::nextHonba(2, false, false, false) == 3);
            check("本场数：闲家和了清零", trainer::nextHonba(2, false, true, false) == 0);
            check("本场数：流局满贯按和了清零", trainer::nextHonba(2, false, false, true) == 0);
        }
        // 和了止 / 听牌止：M.League 关着 → 永不中止；《天凤》庄家 1 位且达 30000 → 中止
        {
            const std::array<bool, 4> tenpai = {true, false, false, false};
            const std::array<int, 4> scores = {32000, 25000, 25000, 18000};
            trainer::Rules ml;
            ml.applyPreset("mleague");
            trainer::Rules th;
            th.applyPreset("tenhou");
            check("和了止：M.League 关 → false",
                  !trainer::stopAtAllLast(0, true, false, tenpai, scores, ml));
            check("和了止：《天凤》庄家 1 位且 32000 → true",
                  trainer::stopAtAllLast(0, true, false, tenpai, scores, th));
            check("听牌止：《天凤》庄家听牌也算",
                  trainer::stopAtAllLast(0, false, false, tenpai, scores, th));
            check("流局满贯不算听牌止",
                  !trainer::stopAtAllLast(0, false, true, tenpai, scores, th));
            const std::array<int, 4> low = {29000, 25000, 25000, 18000};
            check("和了止：庄家没到 30000 → false",
                  !trainer::stopAtAllLast(0, true, false, tenpai, low, th));
        }
        // 延长战：门槛是 requiredPoints，且场风上限 lastWind + 1（没有北入）
        {
            trainer::Rules th;
            th.applyPreset("tenhou");
            th.westExtension = true;
            check("西入：半庄 lastWind=1、南 4 轮庄后 nw=2 且 top<30000 → true",
                  trainer::keepPlayingWest(th, 25000, 2, 1));
            check("西入：nw=3（北）永远不进 → false", !trainer::keepPlayingWest(th, 25000, 3, 1));
            check("西入：top 已达 30000 → false", !trainer::keepPlayingWest(th, 30000, 2, 1));
        }
        // 种子链（golden 取自 Java 探针的输出，钉住"同种子 → 同轨迹"的地基）
        {
            check("RoundSeed 0 = mixSeed(seedBase)",
                  trainer::roundSeedAt(20260101, 0) == trainer::mixSeed(20260101));
            check("种子序列 golden（前 3 个）",
                  trainer::roundSeedAt(20260101, 0) == -960161659727720390LL
                  && trainer::roundSeedAt(20260101, 1) == 3330228915103503152LL
                  && trainer::roundSeedAt(20260101, 2) == 7773727536108528963LL);
            check("SelfPlay.seedFor golden（第 0/1 场）",
                  trainer::seedFor(20260101, 0) == -7695544463733805304LL
                  && trainer::seedFor(20260101, 1) == -960161659727720390LL);
            const std::array<int, 4> p =
                    trainer::placementOf(std::array<int, 4>{1, 1, 2, 2});
            check("顺位：同点按座次（1,1,2,2 → 3,4,1,2）",
                  p[0] == 3 && p[1] == 4 && p[2] == 1 && p[3] == 2);
        }
    }

    // ⑩ 动作空间定点（键 / 下标 / 落位 —— 轨迹里的 `legal` / `chosen` 就是这些键）
    {
        bool ok = false;
        const trainer::Action d = trainer::actionParse("discard:5m/tsumogiri", ok);
        check("discard 键：解析 + 下标 4（5m 的 kind）", ok && d.key() == "discard:5m/tsumogiri"
                && d.index() == 4 && d.tsumogiri);
        const trainer::Action r = trainer::actionParse("riichi:0p", ok);
        check("riichi 赤五：键与下标 37+35", ok && r.key() == "riichi:0p"
                && r.index() == trainer::kActionRiichiBase + 35);
        const trainer::Action p = trainer::actionParse("pon:5p+0p", ok);
        check("碰（带取法）：参数化动作 → 下标 −1", ok && p.key() == "pon:5p+0p" && p.index() == -1);
        const trainer::Action pn = trainer::actionParse("pon", ok);
        check("裸 pon（老数据）：固定槽位 76", ok && pn.index() == trainer::kActionPonId);
        const trainer::Action k = trainer::actionParse("kan:daiminkan:5s+5s+0s", ok);
        check("大明杠（带取法）：键保留取法", ok
                && k.key() == "kan:daiminkan:5s+5s+0s" && k.index() == -1);
        const trainer::Action kj = trainer::actionParse("kan:ankan:5s+5s+5s", ok);
        check("暗杠键里带 + 也会被规范化成取法形态（与 Java 同判）", ok
                && k.tiles.size() == 3);
        check("牌码槽位：0p=35 / 5p=13 / 5z=31",
                trainer::actionTileIndex("0p") == 35 && trainer::actionTileIndex("5p") == 13
                && trainer::actionTileIndex("5z") == 31);
        check("槽位逆映射：35→0p、13→5p", trainer::actionTileCode(35) == "0p"
                && trainer::actionTileCode(13) == "5p");
        int badAccepted = 0;
        for (const char *bad : {"discard:", "discard:0z", "pon:5p", "chi:1m", "kan:ankan:0z",
                                "nosuch", ""}) {
            ok = true;
            trainer::actionParse(bad, ok);
            if (ok) {
                badAccepted++;
            }
        }
        check("非法键全部判 null（7 个）", badAccepted == 0);
        // 落位：精确匹配优先；裸 pon 退让到"第一条"；打牌必须精确（不在 legal 就是 null）
        const std::vector<std::string> legalPon = {"pon:5p+5p", "pon:5p+0p"};
        check("落位：裸 pon → legal 第一条",
                trainer::actionResolveKey("type=pon", legalPon, ok) == "pon:5p+5p" && ok);
        check("落位：精确匹配优先",
                trainer::actionResolveKey("type=pon;tiles=5p+0p", legalPon, ok) == "pon:5p+0p" && ok);
        const std::vector<std::string> legalTurn = {"discard:1m", "riichi:1m"};
        ok = true;
        trainer::actionResolveKey("type=discard;tile=9m", legalTurn, ok);
        check("落位：打牌不许退让（不在 legal → null）", !ok);
    }

    // ⑪ 鸣牌仲裁判据定点（等级 / 压过 / 荣和收齐 / 收工 / 回包认领）
    {
        check("等级：荣和 3 > 杠 2 > 碰 1 > 吃 0 > pass −1",
              trainer::claimRankOf("ron") == 3 && trainer::claimRankOf("kan") == 2
              && trainer::claimRankOf("pon") == 1 && trainer::claimRankOf("chi") == 0
              && trainer::claimRankOf("pass") == -1 && trainer::claimRankOf("tsumo") == -1);
        check("压过：高等级赢", trainer::claimCanBeat(true, {1, 2}, 2, 0));
        check("压过：同级看座次距离（近的赢）",
              trainer::claimCanBeat(true, {1, 2}, 1, 1) && !trainer::claimCanBeat(true, {1, 1}, 1, 2));
        check("压过：pass 永远压不过", !trainer::claimCanBeat(false, {}, -1, 1));
        check("荣和收齐：没有荣和者时返回 false（还要等碰/吃）",
              !trainer::claimAllRonAnswered({}, {0, 1}));
        check("荣和收齐：都答了才 true",
              trainer::claimAllRonAnswered({0}, {0, 1}) && !trainer::claimAllRonAnswered({0, 2}, {0}));
        // 收工：荣和没答完 → 继续等；低优先级压不过 → 收工；缺 askedBestRank → 保守继续等
        const std::map<int, int> dist = {{0, 1}, {1, 2}, {2, 3}};
        check("收工：asked 空 → true", trainer::claimShouldStop({}, {0}, {}, 5000, false, {}, {}, {}));
        check("收工：超时 → true",
              trainer::claimShouldStop({1}, {}, {}, 0, false, {}, {{1, 0}}, dist));
        check("收工：荣和没答完 → false",
              !trainer::claimShouldStop({0, 1}, {0}, {}, 5000, false, {}, {{1, 0}}, dist));
        check("收工：荣和已答完而无人能压过 → true",
              trainer::claimShouldStop({1}, {0}, {0}, 5000, false, {}, {{1, 0}}, dist));
        // ⚠ 要走到 canBeat 那一步，`ronCapable` 里必须**有未答、但又不在 asked 里**的座位
        //（否则要么前面 `allRonAnswered` 直接收工、要么循环里因为"可能荣和"继续等）
        check("收工：手上有更大鸣牌 → false",
              !trainer::claimShouldStop({1}, {0}, {}, 5000, true, {1, 2}, {{1, 2}}, dist));
        check("收工：askedBestRank 缺项 → 保守继续等",
              !trainer::claimShouldStop({1}, {0}, {}, 5000, false, {}, {}, dist));
        check("收工：都没人能压过 → true",
              trainer::claimShouldStop({1}, {0}, {}, 5000, true, {2, 2}, {{1, 1}}, dist));
        check("回包认领：没被问 / 没挂起 / ask_id 过期都丢",
              !trainer::claimAcceptsReply(false, true, 7, false, 0)
              && !trainer::claimAcceptsReply(true, false, 0, false, 0)
              && !trainer::claimAcceptsReply(true, true, 7, true, 8));
        check("回包认领：没带 ask_id 时按座位认（兼容老客户端）",
              trainer::claimAcceptsReply(true, true, 7, false, 0));
    }

    // ⑫ 振听记账定点（三种振听 + 唯一记账点）
    {
        trainer::FuritenState st;
        st.recordDiscard(0, 3);
        st.recordDiscard(0, 6);
        st.recordDiscard(1, 3);
        const std::vector<int> own0 = st.ownDiscardKinds(0);
        check("曾经打出过：按座位分开记（0 家 = 3m/6m）",
              own0 == std::vector<int>({3, 6}) && st.ownDiscardKinds(1) == std::vector<int>({3}));
        check("舍张振听：听的牌里有自己打过的 → 振听", st.isFuriten(0, {6, 9}));
        check("舍张振听：听的牌都没打过 → 不振听", !st.isFuriten(0, {9, 10}));
        check("舍张振听：被鸣走的舍牌也算（记账点在出牌那一刻，与牌河无关）",
              st.isFuriten(1, {3}));
        st.onRonPassed(0, false);
        check("同巡振听：见逃即置位", st.isFuriten(0, {9}));
        st.onOwnDraw(0);
        check("同巡振听：自家摸牌解除", !st.isFuriten(0, {9}));
        st.onRonPassed(1, true);
        st.onOwnDraw(1);
        check("立直后见逃：摸牌也不解除（到本局结束）", st.isFuriten(1, {9}));
    }

    // ⑬ 可见牌统计 + 和了形纯判断定点
    {
        std::array<std::vector<int>, 4> rivers;
        std::array<std::vector<trainer::Meld>, 4> melds;
        rivers[0] = {trainer::idOf(0, 0), trainer::idOf(0, 1)};       // 1m 两张
        rivers[1] = {trainer::idOf(27, 0)};                            // 1z 一张
        const int chiIds[3] = {trainer::idOf(9, 0), trainer::idOf(10, 0), trainer::idOf(11, 0)};
        melds[2].emplace_back(trainer::Meld::Kind::CHI, chiIds, 3, 0, 0);
        const trainer::Counts vis =
                trainer::visibleCounts(rivers, melds, std::vector<int>{8, 27});
        check("可见 = 牌河 + 副露 + 宝牌指示牌",
              vis[0] == 2 && vis[8] == 1 && vis[9] == 1 && vis[10] == 1 && vis[11] == 1
              && vis[27] == 2 && trainer::sumOf(vis) == 8);
        trainer::Counts own{};
        own[0] = 1;
        const trainer::Counts un = trainer::visibleUnseen(vis);
        const trainer::Counts dr = trainer::visibleDrawable(vis, own);
        check("unseen = 4 − 可见（下限 0）", un[0] == 2 && un[8] == 3 && un[27] == 2);
        check("drawable 再减自己手里（1m：4−2−1=1）", dr[0] == 1 && dr[8] == 3);
        check("挡和原因：荣和 + 振听 → furiten；其余 → no_yaku",
              trainer::winBlockReason(false, true) == "furiten"
              && trainer::winBlockReason(true, true) == "no_yaku"
              && trainer::winBlockReason(false, false) == "no_yaku");
        trainer::Counts concealed{};
        for (int k : {1, 2, 3, 4, 5, 15, 16, 17, 18, 18, 18, 28, 28}) {
            concealed[static_cast<size_t>(k)]++;
        }
        trainer::Counts merged{};
        check("和了牌并入 + 张数校验（14−3×副露）",
              trainer::winCounts(concealed, 0, 3, false, merged) && trainer::sumOf(merged) == 14
              && merged[3] == 2
              && !trainer::winCounts(concealed, 0, 3, true, merged)      // 自摸时已含那张 → 13 ≠ 14
              && !trainer::winCounts(concealed, 1, 3, false, merged));   // 一副露要求 11 张 → 不符

        // ---- `RoundOptions`（选项生成的第一层，纯函数）
        {
            // 可打牌：未立直去重；已立直只剩摸切一张；振听禁打按**牌种**过滤
            //   id 0/1/2 = 1m×3，4 = 2m，8/9 = 3m×2，13 = **4m**（kind = id>>2），108/109 = 1z×2
            const std::vector<int> hand = {0, 1, 2, 4, 8, 9, 13, 108, 109};
            check("可打牌：去重后按插入序（1m,2m,3m,4m,1z）",
                  trainer::discardChoices(hand, false, -1, {}) == std::vector<std::string>({"1m", "2m", "3m", "4m", "1z"}));
            check("可打牌：赤五 id 打成 0p 而不是 5p",
                  trainer::discardChoices({52}, false, -1, {}) == std::vector<std::string>({"0p"}));
            check("可打牌：已立直只剩摸切那一张",
                  trainer::discardChoices(hand, true, 8, {}) == std::vector<std::string>({"3m"}));
            check("可打牌：振听禁打按牌种（禁 1m → 三张同种一起去掉）",
                  trainer::discardChoices(hand, false, -1, {0}) == std::vector<std::string>({"2m", "3m", "4m", "1z"}));
            check("可打牌：已立直 + drawn<0 退化成手牌去重",
                  trainer::discardChoices(hand, true, -1, {}).size() == 5);

            // 食替：两侧都要禁（吃 5m 配 3m4m 禁 {5m,2m}；吃 2m 禁 {2m,5m}）；坎张只有現物
            check("食替：吃 5m 用 3m4m → 禁 {5m,2m}",
                  trainer::kuikaeForbidden(4, 2, 3) == std::vector<int>({4, 1}));
            check("食替：吃 2m 用 3m4m → 禁 {2m,5m}",
                  trainer::kuikaeForbidden(1, 2, 3) == std::vector<int>({1, 4}));
            check("食替：坎张（4m6m 吃 5m）只有現物",
                  trainer::kuikaeForbidden(4, 3, 5) == std::vector<int>({4}));
            check("食替：1m 的向下补不存在（吃 1m 用 2m3m → 只有 {1m,4m}）",
                  trainer::kuikaeForbidden(0, 1, 2) == std::vector<int>({0, 3}));
            check("食替：吃 9m 用 7m8m → 禁 {9m, 6m}（两侧都要算）",
                  trainer::kuikaeForbidden(8, 6, 7) == std::vector<int>({8, 5}));
            check("食替：字牌只有現物",
                  trainer::kuikaeForbidden(31, 31, 31) == std::vector<int>({31}));
            check("食替：跨花色不补（吃 1p 用 8m9m → 只有 {1p}）",
                  trainer::kuikaeForbidden(9, 7, 8) == std::vector<int>({9}));

            // 立直后杠：听牌形必须**完全不变**（调用前提：手里真的有 4 张，与 Java 调用点一致）
            //   下面两条的真值都来自 Java（`ropts kanauto` 对拍，见 tools/trainer-settle-parity.mjs）：
            //   ① 6666m + 1s（3 副露）杠 6m → 杠前杠后都单骑 1s → **允许**
            trainer::Counts kanHand{};
            kanHand[5] = 4;                              // 6m×4
            kanHand[18] = 1;                             // 1s
            trainer::Counts before = kanHand;
            before[5] = 3;                               // 「杠前听牌」要先把刚摸到的那张减掉（14 张口径）
            check("立直后杠：听牌形不变 → 允许（6666m+1s / 3 副露 杠 6m，仍单骑 1s）",
                  trainer::kanAllowedAfterRiichi(trainer::waits(before, 3), kanHand, 3, 5));
            //   ② 8p×4 但摸的是 9m：杠 8p 会改变听牌 → **不许**
            trainer::Counts kanHand2{};
            kanHand2[0] = 3;
            kanHand2[1] = 2;
            kanHand2[2] = 3;
            kanHand2[8] = 1;
            kanHand2[16] = 4;                            // 8p×4
            kanHand2[32] = 1;
            trainer::Counts before2 = kanHand2;
            before2[8] = 0;
            check("立直后杠：听牌形变了 → 不许（8p×4，摸的是 9m）",
                  !trainer::kanAllowedAfterRiichi(trainer::waits(before2, 0), kanHand2, 0, 16));
            check("立直后杠：手里不足 4 张 → 挡在门口（Java 侧会算出负数并 StackOverflow）",
                  !trainer::kanAllowedAfterRiichi({}, kanHand2, 0, 5));
            trainer::Counts noWait{};                    // 4m5m + 5m×4：杠完不听牌
            noWait[2] = 1;
            noWait[3] = 1;
            noWait[4] = 4;
            check("立直后杠：杠完不听牌 → 不许",
                  !trainer::kanAllowedAfterRiichi({}, noWait, 0, 4));

            // 吃搭子：三种组合的顺序 = {-2,-1} {-1,1} {1,2}；字牌为空；越界不串花色
            trainer::Counts chi{};
            chi[1] = 1;                                  // 2m
            chi[2] = 1;                                  // 3m
            chi[3] = 1;                                  // 4m
            chi[4] = 1;                                  // 5m
            const auto sets = trainer::chiSets(chi, 3);  // 吃 4m → {2m3m}、{3m5m}（6m 不在手里）
            check("吃搭子：吃 4m → 两副（2m3m / 3m5m），顺序与 Java 一致",
                  sets.size() == 2 && sets[0][0] == "2m" && sets[0][1] == "3m"
                  && sets[1][0] == "3m" && sets[1][1] == "5m");
            check("吃搭子：字牌为空", trainer::chiSets(chi, 31).empty());
            trainer::Counts cross{};                     // 8m9m + 2p3p：吃 1p 不得串到万子去
            cross[7] = 1;
            cross[8] = 1;
            cross[10] = 1;
            cross[11] = 1;
            const auto cs = trainer::chiSets(cross, 9);
            check("吃搭子：不吃到相邻花色（8m9m 在手里也算不进 1p 的搭子）",
                  cs.size() == 1 && cs[0][0] == "2p" && cs[0][1] == "3p");
        }

        // ---- 自家回合的询问内容（`turnOptions`）
        {
            // 牌 id 速查：id = (kind<<2)|copy；赤五 = kind∈{4,13,22} 且 copy 0（下面一律用 copy!=0）
            auto ask = [](std::vector<int> ids, int drawnKind, int drawnId) {
                trainer::TurnAsk a;
                a.seat = 0;
                a.roundWind = 0;
                a.kyoku = 1;
                a.dealer = 0;
                a.scores = {25000, 25000, 25000, 25000};
                a.tilesLeft = 69;
                a.deadWallLeft = 4;
                a.playerDraws = 3;
                a.menzen = true;
                a.handIds = std::move(ids);
                for (int id : a.handIds) {
                    a.hand[static_cast<size_t>(trainer::kindOf(id))]++;
                }
                a.drawnKind = drawnKind;
                a.drawnId = drawnId;
                return a;
            };
            auto has = [](const std::string &s, const std::string &sub) {
                return s.find(sub) != std::string::npos;
            };

            // ① 自摸闸门：123m456m789m 11p 999s 摸 9s → 一気通貫（门前 2 番）→ 给 tsumo
            {
                trainer::TurnAsk a = ask({3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 39, 107, 107, 107},
                                         26, 107);
                const std::string txt = trainer::turnOptionsText(a);
                check("自家回合：门前一気通貫自摸 → 选项里有 tsumo", has(txt, ";tsumo") || has(txt, "tsumo"));
                check("自家回合：选项顺序 = discard 在最前", txt.rfind("discard=", 0) == 0);
            }
            // ② 无役自摸（鸣了一副 123m，剩 456p789p11s999s）→ 不给 tsumo
            {
                trainer::TurnAsk a = ask({51, 55, 59, 63, 67, 71, 75, 75, 107, 107, 107}, 26, 107);
                trainer::Meld chi;
                chi.kind = trainer::Meld::Kind::CHI;
                chi.tiles = {3, 7, 11};
                chi.tileCount = 3;
                chi.from = 3;
                chi.calledId = 3;
                a.melds.push_back(chi);
                a.menzen = false;
                const std::string txt = trainer::turnOptionsText(a);
                check("自家回合：副露无役自摸 → 选项里没有 tsumo", !has(txt, "tsumo"));
            }
            // ③ 立直门槛：123m456m789m 11p 99s + 摸 5z → 可以宣（打 5z 后听 9s）
            {
                trainer::TurnAsk a = ask({3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 39, 107, 107, 127},
                                         31, 127);
                const std::string txt = trainer::turnOptionsText(a);
                check("自家回合：打 5z 后听 9s → 选项里有 riichi=5z", has(txt, "riichi=5z"));
                // ⚠ M.League：摸到海底后**不许**立直（`riichiNoHaitei`）
                a.atLastLive = true;
                check("自家回合：海底那一巡不给 riichi（M.League 的 riichiNoHaitei）",
                      !has(trainer::turnOptionsText(a), "riichi="));
            }
            // ④ 杠：手里 4 张 1z（非立直）→ ankan；PON 了 5z 且手里还有 5z → kakan
            {
                trainer::TurnAsk a = ask({108, 109, 110, 111, 3, 7, 11, 15, 19, 23, 27, 31, 35, 39},
                                         9, 39);
                const std::string txt = trainer::turnOptionsText(a);
                check("自家回合：暗杠候选（手里真有 4 张）→ kan=ankan:1z", has(txt, "kan=ankan:1z"));
                a.melds.clear();
                trainer::Meld pon;
                pon.kind = trainer::Meld::Kind::PON;
                pon.tiles = {125, 126, 127};
                pon.tileCount = 3;
                pon.from = 2;
                pon.calledId = 127;
                a.melds.push_back(pon);
                a.handIds.push_back(124);                       // 手里还有一张 5z
                a.hand[31]++;
                const std::string t2 = trainer::turnOptionsText(a);
                check("自家回合：暗杠在前、加杠在后（同一个 kan 选项里）→ ankan:1z,kakan:5z",
                      has(t2, "ankan:1z,kakan:5z"));
                // ⚠ 海底那一巡不给杠（`!haitei`）
                a.atLastLive = true;
                check("自家回合：海底那一巡不给 kan", !has(trainer::turnOptionsText(a), "kan="));
            }
            // ⑤ 九种九牌：M.League 关着（`kyuushuAbort=false`）→ 永远不给；《天凤》开着才给
            {
                trainer::TurnAsk a = ask({3, 35, 39, 71, 75, 107, 111, 115, 119, 123, 127, 131, 135,
                                          0},
                                         0, 0);
                a.playerDraws = 1;
                check("自家回合：九种九牌在 M.League 下不给（kyuushuAbort=false）",
                      !has(trainer::turnOptionsText(a), "kyuushu"));
                a.rules.applyPreset("tenhou");
                check("自家回合：换成《天凤》预设（kyuushuAbort=true）才给 kyuushu",
                      has(trainer::turnOptionsText(a), "kyuushu"));
            }
        }

        // ---- 鸣牌询问（`claimOptions`）
        {
            auto has = [](const std::string &s, const std::string &sub) {
                return s.find(sub) != std::string::npos;
            };
            auto claim = [](std::vector<int> ids, int calledKind, int from) {
                trainer::ClaimAsk a;
                a.seat = 0;
                a.from = from;
                a.calledKind = calledKind;
                a.calledId = calledKind < 0 ? -1 : trainer::idOf(calledKind, 3);
                a.dealer = 0;
                a.roundWind = 0;
                a.handIds = std::move(ids);
                for (int id : a.handIds) {
                    a.hand[static_cast<size_t>(trainer::kindOf(id))]++;
                }
                a.deadWallLeft = 4;
                return a;
            };
            // ① pass 永远在最后（且无役时只有 pass）
            {
                trainer::ClaimAsk a = claim({51, 55, 59, 63, 67, 71, 75, 75, 107, 107}, 26, 3);
                const std::string txt = trainer::claimOptionsText(a);
                check("鸣牌：pass 永远在最后", !txt.empty() && txt.rfind("pass") == txt.size() - 4);
                check("鸣牌：无役不能荣和 → 没有 ron 段", !has(txt, "ron"));
            }
            // ② 役牌荣和 → ron 在最前（111z 234p 567p 678s + 9s 单骑，荣 9s）
            //    id 速查：kind<<2|copy；1z = 108..111、2p=40..43、7p=60..63、6s=92..95、9s=104..107
            {
                trainer::ClaimAsk a = claim({108, 109, 110, 43, 47, 51, 55, 59, 63, 95, 99, 103, 107},
                                            26, 2);
                check("鸣牌：役牌（場風東）可以荣和 → ron 在最前",
                      trainer::claimOptionsText(a).rfind("ron", 0) == 0);
                // ③ 舍张振听：听牌里有自己打出过的牌种 → 不给 ron
                a.discardKindsEver[26] = 1;                 // 自己打出过 9s
                check("鸣牌：舍张振听（打出过这张）→ 没有 ron",
                      !has(trainer::claimOptionsText(a), "ron"));
                check("鸣牌：isFuriten 认定成立", trainer::isFuritenClaim(a));
            }
            // ④ 碰的取法：两张普通五 → 一条；赤五 + 两张普通五 → 两条（普通在前）
            {
                trainer::ClaimAsk a = claim({55, 54, 3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43}, 13, 2);
                check("鸣牌：碰只有一种取法 → pon=5p+5p",
                      has(trainer::claimOptionsText(a), "pon=5p+5p"));
                trainer::ClaimAsk b = claim({52, 55, 54, 3, 7, 11, 15, 19, 23, 27, 31, 35, 39}, 13, 2);
                check("鸣牌：赤五 + 普通五 → 两条取法（普通在前、用赤在后；各自一条选项）",
                      has(trainer::claimOptionsText(b), "pon=5p+5p;pon=0p+5p"));
            }
            // ⑤ 大明杠：手里 3 张 → 给；立直后只可能荣和或过
            {
                trainer::ClaimAsk a = claim({108, 109, 110, 3, 7, 11, 15, 19, 23, 27, 31, 35, 39}, 27, 2);
                check("鸣牌：手里 3 张 → 给大明杠",
                      has(trainer::claimOptionsText(a), "kan=daiminkan:1z:1z+1z+1z"));
                a.riichi = true;
                const std::string t = trainer::claimOptionsText(a);
                check("鸣牌：立直后没有 pon/kan/chi（只能荣和或过）",
                      !has(t, "pon=") && !has(t, "kan=") && !has(t, "chi=") && has(t, "pass"));
            }
            // ⑥ 河底：吃碰杠全部挡掉
            {
                trainer::ClaimAsk a = claim({55, 56, 60, 64, 68, 3, 7, 11, 15, 19, 23, 27, 31}, 13, 3);
                a.houtei = true;
                const std::string t = trainer::claimOptionsText(a);
                check("鸣牌：河底那一张不给吃碰杠（只可能荣和或过）",
                      !has(t, "pon=") && !has(t, "kan=") && !has(t, "chi=") && has(t, "pass"));
            }
            // ⑦ 吃：只有下家能吃
            {
                trainer::ClaimAsk a = claim({1, 2, 3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43}, 0, 3);
                check("鸣牌：下家（seat 0 ← from 3）能吃 → 有 chi=",
                      has(trainer::claimOptionsText(a), "chi="));
                trainer::ClaimAsk b = claim({1, 2, 3, 7, 11, 15, 19, 23, 27, 31, 35, 39, 43}, 0, 1);
                check("鸣牌：不是下家打的牌不给 chi",
                      !has(trainer::claimOptionsText(b), "chi="));
            }
        }
    }

    std::printf(fails == 0 ? "TRAINER SELFTEST PASS\n" : "TRAINER SELFTEST FAIL（%d）\n", fails);
    return fails == 0 ? 0 : 1;
}

// ---------------------------------------------------------------- 打点对拍（`score`）
//
// 语料格式（`tools/trainer-score-parity.mjs` 生成，Java 侧 `tools/ScoreProbe.java` 同样解析）：
//   每行 15 个空格分隔的字段：
//     preset winKind flags roundWind seat dealerSeat menzen honba sticks loser dora ura pao hand melds
//   · `flags` = 位掩码：0 tsumo / 1 riichi / 2 doubleRiichi / 3 ippatsu / 4 chankan / 5 rinshan /
//     6 haitei / 7 houtei / 8 tenhou / 9 chiihou / 10 renhou / 11 tsubame / 12 kanburi / 13 nagashi
//   · `preset` 可带 `+koyaku` / `+kazoe` / `+dbl` 后缀（先铺预设再点这几个开关，两边同义）
//   · 列表字段：`-` = 空；`dora`/`ura` 是 kind；`pao` = `seat:base;…`；`hand` = 牌 id
//   · `melds` = `ro:4,5,6;tc:52,53,54;…`（type ∈ r/t/q，open ∈ o/c，随后是牌 id）
//
// 输出每行 17 个字段（与 Java 探针**逐字节**可比，见 `tools/trainer-score-parity.mjs`）。
struct ScoreRow {
    std::string preset;
    int winKind = 0;
    int flags = 0;
    int roundWind = 27;
    int seat = 0;
    int dealerSeat = 0;
    int menzen = 1;
    int honba = 0;
    int sticks = 0;
    int loser = -1;
    std::vector<int> dora;
    std::vector<int> ura;
    std::vector<trainer::Pao> paos;
    std::vector<int> hand;
    std::vector<trainer::Meld> melds;
};

std::vector<std::string> splitOn(const std::string &s, char sep) {
    std::vector<std::string> out;
    size_t start = 0;
    while (true) {
        const size_t pos = s.find(sep, start);
        if (pos == std::string::npos) {
            out.push_back(s.substr(start));
            break;
        }
        out.push_back(s.substr(start, pos - start));
        start = pos + 1;
    }
    return out;
}

std::vector<int> parseIntList(const std::string &s) {
    std::vector<int> out;
    if (s.empty() || s == "-") {
        return out;
    }
    for (const std::string &item : splitOn(s, ',')) {
        if (!item.empty()) {
            out.push_back(std::atoi(item.c_str()));
        }
    }
    return out;
}

bool readScoreRow(const std::string &line, ScoreRow &row) {
    std::vector<std::string> f;
    size_t start = 0;
    while (start <= line.size()) {
        const size_t pos = line.find(' ', start);
        if (pos == std::string::npos) {
            f.push_back(line.substr(start));
            break;
        }
        if (pos > start) {
            f.push_back(line.substr(start, pos - start));
        }
        start = pos + 1;
    }
    if (f.size() < 15) {
        return false;
    }
    row.preset = f[0];
    row.winKind = std::atoi(f[1].c_str());
    row.flags = std::atoi(f[2].c_str());
    row.roundWind = std::atoi(f[3].c_str());
    row.seat = std::atoi(f[4].c_str());
    row.dealerSeat = std::atoi(f[5].c_str());
    row.menzen = std::atoi(f[6].c_str());
    row.honba = std::atoi(f[7].c_str());
    row.sticks = std::atoi(f[8].c_str());
    row.loser = std::atoi(f[9].c_str());
    row.dora = parseIntList(f[10]);
    row.ura = parseIntList(f[11]);
    row.paos.clear();
    if (f[12] != "-") {
        for (const std::string &item : splitOn(f[12], ';')) {
            const size_t c = item.find(':');
            if (c == std::string::npos) {
                continue;
            }
            trainer::Pao p;
            p.seat = std::atoi(item.substr(0, c).c_str());
            p.base = std::atoi(item.substr(c + 1).c_str());
            row.paos.push_back(p);
        }
    }
    row.hand = parseIntList(f[13]);
    row.melds.clear();
    if (f[14] != "-") {
        for (const std::string &item : splitOn(f[14], ';')) {
            const size_t c = item.find(':');
            if (c < 2) {
                continue;
            }
            trainer::Meld m;
            switch (item[0]) {
                case 'r': m.kind = trainer::Meld::Kind::CHI; break;            // 顺子（只可能明）
                case 't': m.kind = trainer::Meld::Kind::PON; break;            // 碰
                case 'q': m.kind = (item[1] == 'c') ? trainer::Meld::Kind::ANKAN
                                                    : trainer::Meld::Kind::DAIMINKAN;
                          break;
                case 'k': m.kind = trainer::Meld::Kind::KAKAN; break;          // 加杠（明）
                default: continue;
            }
            const std::vector<int> ids = parseIntList(item.substr(c + 1));
            m.tileCount = static_cast<int>(ids.size());
            for (size_t i = 0; i < ids.size() && i < 4; i++) {
                m.tiles[i] = ids[i];
            }
            row.melds.push_back(m);
        }
    }
    return true;
}

/** 预设串：`mleague` / `tenhou` / `majsoul`，可带 `+koyaku` / `+kazoe` / `+dbl` / `+renhou[y]` 后缀。 */
trainer::Rules rulesOfPreset(const std::string &spec) {
    std::string base = spec;
    bool koyaku = false;
    bool kazoe = false;
    bool dbl = false;
    bool west = false;
    std::string renhou;
    while (true) {
        const size_t pos = base.find('+');
        if (pos == std::string::npos) {
            break;
        }
        const std::string opt = base.substr(pos + 1);
        if (opt == "koyaku") {
            koyaku = true;
        } else if (opt == "kazoe") {
            kazoe = true;
        } else if (opt == "dbl") {
            dbl = true;
        } else if (opt == "renhou") {
            renhou = "mangan";
        } else if (opt == "renhouy") {
            renhou = "yakuman";
        } else if (opt == "west") {
            west = true;
        }
        base = base.substr(0, pos);
    }
    trainer::Rules r;
    r.applyPreset(base.empty() ? "mleague" : base);
    if (koyaku) {
        r.koyaku = true;
    }
    if (kazoe) {
        r.kazoeYakuman = true;
    }
    if (dbl) {
        r.doubleYakuman = true;
    }
    if (west) {
        r.westExtension = true;
    }
    if (!renhou.empty()) {
        r.renhou = renhou;
    }
    return r;
}

void winContextOf(const ScoreRow &row, trainer::WinContext &ctx) {
    ctx.rules = rulesOfPreset(row.preset);
    ctx.seat = row.seat;
    ctx.dealerSeat = row.dealerSeat;
    ctx.roundWind = row.roundWind;
    ctx.tsumo = (row.flags & (1 << 0)) != 0;
    ctx.riichi = (row.flags & (1 << 1)) != 0;
    ctx.doubleRiichi = (row.flags & (1 << 2)) != 0;
    ctx.ippatsu = (row.flags & (1 << 3)) != 0;
    ctx.chankan = (row.flags & (1 << 4)) != 0;
    ctx.rinshan = (row.flags & (1 << 5)) != 0;
    ctx.haitei = (row.flags & (1 << 6)) != 0;
    ctx.houtei = (row.flags & (1 << 7)) != 0;
    ctx.tenhou = (row.flags & (1 << 8)) != 0;
    ctx.chiihou = (row.flags & (1 << 9)) != 0;
    ctx.renhou = (row.flags & (1 << 10)) != 0;
    ctx.tsubame = (row.flags & (1 << 11)) != 0;
    ctx.kanburi = (row.flags & (1 << 12)) != 0;
    ctx.nagashi = (row.flags & (1 << 13)) != 0;
    ctx.winKind = row.winKind;
    ctx.doraIndicators = row.dora;
    ctx.uraIndicators = row.ura;
    ctx.menzen = row.menzen != 0;
    ctx.allTileIds = row.hand;
    for (const trainer::Meld &m : row.melds) {
        for (int i = 0; i < m.tileCount; i++) {
            ctx.allTileIds.push_back(m.tiles[static_cast<size_t>(i)]);
        }
    }
}

const char *reasonTag(const std::string &reason) {
    if (reason.empty()) {
        return "-";
    }
    if (reason == "不是和了形") {
        return "not_agari";
    }
    if (reason == "无役") {
        return "no_yaku";
    }
    if (reason == "番缚不足") {
        return "han_shibari";
    }
    return "?";
}

void appendFormSig(std::string &out, const trainer::HandScore &s) {
    if (!s.hasForm) {
        out += "-";
        return;
    }
    const trainer::Form &f = s.form;
    out += std::to_string(f.type);
    out += ':';
    out += std::to_string(f.pair);
    out += ':';
    out += std::to_string(f.winSet);
    out += ':';
    out += std::to_string(f.waitType);
    out += ':';
    out += std::to_string(f.nSets);
    for (int i = 0; i < f.nSets; i++) {
        out += ':';
        out += (f.setType[static_cast<size_t>(i)] == trainer::kSetRun)
                       ? 'r'
                       : (f.setType[static_cast<size_t>(i)] == trainer::kSetTriplet ? 't' : 'q');
        out += std::to_string(f.setStart[static_cast<size_t>(i)]);
        out += f.setConcealed[static_cast<size_t>(i)] ? 'c' : 'o';
        out += f.setFromMeld[static_cast<size_t>(i)] ? 'm' : '-';
    }
}

// ---------------------------------------------------------------- 连庄判据 / 精算对拍（`settle`）
//
// 语料（`tools/trainer-settle-parity.mjs` 生成，Java 侧 `tools/SettleProbe.java` 同样解析）：
//   每行以**类型**开头，字段用空格分隔：
//     settle <preset> <s0> <s1> <s2> <s3>            精算（顺位/名次/顺位点/马点/头名赏）
//     sticks <s0..s3> <sticks>                       终局余棒分配
//     honba  <honba> <renchan> <agari> <nagashi>     下一局本场数
//     alllast <preset> <dealer> <agari> <nagashi> <tenpaiBits> <s0..s3>   和了止 / 听牌止
//     west   <preset> <top> <nextWind> <lastWind>    要不要进延长战
//     nagashi <winner> <dealer>                      流局满贯的支付
//     ngelig <calledFrom> <idsCsv|->                  流局满资格
//     split  <total> <n> <unit>                      通用拆分（尾数归第一家）
//     seeds  <seedBase> <n>                          每局种子序列（mixSeed 岔路）
//     seedfor <seedBase> <games>                     每场种子（SelfPlay.seedFor）
//     place  <s0..s3>                                顺位（同点按座次）
// 输出：每行一次结果，**浮点打印原始位模式**（`%016llx`）—— 与 Java 的
// `Double.doubleToLongBits` 逐位可比，避免两边十进制格式化的差异。
std::string doubleBits(double v) {
    const uint64_t bits = std::bit_cast<uint64_t>(v);
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%016llx", static_cast<unsigned long long>(bits));
    return buf;
}

std::string intsCsv(const int *v, int n) {
    std::string out;
    for (int i = 0; i < n; i++) {
        if (i > 0) {
            out += ',';
        }
        out += std::to_string(v[i]);
    }
    return out;
}

/** `1,2` → set（`-` = 空集）。 */
std::set<int> intSetOf(const std::string &s) {
    std::set<int> out;
    for (int v : parseIntList(s)) {
        out.insert(v);
    }
    return out;
}

/** `1:2;3:1` → map（`-` = 空表）。 */
std::map<int, int> intMapOf(const std::string &s) {
    std::map<int, int> out;
    if (s.empty() || s == "-") {
        return out;
    }
    for (const std::string &item : splitOn(s, ';')) {
        const size_t c = item.find(':');
        if (c == std::string::npos) {
            continue;
        }
        out[std::atoi(item.substr(0, c).c_str())] = std::atoi(item.substr(c + 1).c_str());
    }
    return out;
}

// ---------------------------------------------------------------- 动作空间对拍（`action`）
//
// 语料（`tools/trainer-action-parity.mjs` 生成，Java 侧 `tools/ActionProbe.java` 同样解析）：
//   key     <cmdTokens>              token 串 → 动作 → key/index/cmdTokens
//   parse   <key>                    key → 动作（键的逆）
//   tile    <code>                   牌码 ↔ 槽位
//   resolve <cmdTokens> <legalCsv>   回包落位到 legal 里的那一个（碰/杠才允许"同类型第一条"）
// 输出：每行 `key index cmdTokens`（`parse`/`key`）、`index code`（`tile`）、`resolvedKey`（`resolve`）。
int cmdAction(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr, "用法：trainer action <corpus> <out>\n");
        return 2;
    }
    std::FILE *in = std::fopen(argv[1], "rb");
    if (in == nullptr) {
        std::fprintf(stderr, "读不到语料：%s\n", argv[1]);
        return 2;
    }
    std::FILE *out = std::fopen(argv[2], "wb");
    if (out == nullptr) {
        std::fprintf(stderr, "写不了输出：%s\n", argv[2]);
        std::fclose(in);
        return 2;
    }
    char line[8192];
    long long rows = 0;
    while (std::fgets(line, sizeof(line), in) != nullptr) {
        std::string text(line);
        while (!text.empty() && (text.back() == '\n' || text.back() == '\r')) {
            text.pop_back();
        }
        if (text.empty()) {
            continue;
        }
        const size_t sp = text.find(' ');
        const std::string kind = sp == std::string::npos ? text : text.substr(0, sp);
        const std::string rest = sp == std::string::npos ? std::string() : text.substr(sp + 1);
        std::string outLine;
        if (kind == "key") {
            bool ok = false;
            const trainer::Action a = trainer::actionFromCmdTokens(rest, ok);
            outLine = ok ? (a.key() + " " + std::to_string(a.index()) + " " + a.cmdTokens())
                         : "- - -";
        } else if (kind == "parse") {
            bool ok = false;
            const trainer::Action a = trainer::actionParse(rest, ok);
            outLine = ok ? (a.key() + " " + std::to_string(a.index()) + " " + a.cmdTokens())
                         : "- - -";
        } else if (kind == "tile") {
            const int idx = trainer::actionTileIndex(rest);
            const std::string code = trainer::actionTileCode(idx);
            outLine = std::to_string(idx) + " " + (code.empty() ? "-" : code);
        } else if (kind == "resolve") {
            const size_t sp2 = rest.find(' ');
            const std::string cmd = sp2 == std::string::npos ? rest : rest.substr(0, sp2);
            const std::string legalText = sp2 == std::string::npos ? "-" : rest.substr(sp2 + 1);
            std::vector<std::string> legal;
            if (legalText != "-") {
                legal = splitOn(legalText, ',');
            }
            bool ok = false;
            const std::string resolved = trainer::actionResolveKey(cmd, legal, ok);
            outLine = ok ? resolved : "-";
        } else {
            std::fprintf(stderr, "认不出的语料行：%s\n", text.c_str());
            std::fclose(in);
            std::fclose(out);
            return 2;
        }
        outLine += '\n';
        std::fwrite(outLine.data(), 1, outLine.size(), out);
        rows++;
    }
    std::fclose(in);
    std::fclose(out);
    std::fprintf(stderr, "[trainer] action 语料 %lld 行\n", rows);
    return 0;
}

int cmdSettle(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr, "用法：trainer settle <corpus> <out>\n");
        return 2;
    }
    std::FILE *in = std::fopen(argv[1], "rb");
    if (in == nullptr) {
        std::fprintf(stderr, "读不到语料：%s\n", argv[1]);
        return 2;
    }
    std::FILE *out = std::fopen(argv[2], "wb");
    if (out == nullptr) {
        std::fprintf(stderr, "写不了输出：%s\n", argv[2]);
        std::fclose(in);
        return 2;
    }
    char line[16384];
    long long rows = 0;
    long long checksum = 0;
    while (std::fgets(line, sizeof(line), in) != nullptr) {
        std::string text(line);
        while (!text.empty() && (text.back() == '\n' || text.back() == '\r')) {
            text.pop_back();
        }
        if (text.empty()) {
            continue;
        }
        std::vector<std::string> f;
        {
            size_t start = 0;
            while (start <= text.size()) {
                const size_t pos = text.find(' ', start);
                if (pos == std::string::npos) {
                    f.push_back(text.substr(start));
                    break;
                }
                if (pos > start) {
                    f.push_back(text.substr(start, pos - start));
                }
                start = pos + 1;
            }
        }
        if (f.empty()) {
            continue;
        }
        std::string outLine;
        const std::string &kind = f[0];
        if (kind == "settle" && f.size() >= 6) {
            std::array<int, 4> scores{};
            for (int i = 0; i < 4; i++) {
                scores[static_cast<size_t>(i)] = std::atoi(f[static_cast<size_t>(2 + i)].c_str());
            }
            const trainer::Rules rules = rulesOfPreset(f[1]);
            const trainer::Settlement st = trainer::settle(scores, rules);
            outLine = intsCsv(st.order.data(), 4);
            outLine += ' ';
            outLine += intsCsv(st.rank.data(), 4);
            for (int i = 0; i < 4; i++) {
                outLine += ' ';
                outLine += doubleBits(st.point[static_cast<size_t>(i)]);
            }
            for (int i = 0; i < 4; i++) {
                outLine += ' ';
                outLine += doubleBits(st.uma[static_cast<size_t>(i)]);
            }
            for (int i = 0; i < 4; i++) {
                outLine += ' ';
                outLine += doubleBits(st.oka[static_cast<size_t>(i)]);
            }
            checksum += static_cast<long long>(st.point[0] * 10);
        } else if (kind == "sticks" && f.size() >= 6) {
            std::array<int, 4> scores{};
            for (int i = 0; i < 4; i++) {
                scores[static_cast<size_t>(i)] = std::atoi(f[static_cast<size_t>(1 + i)].c_str());
            }
            const std::array<int, 4> add =
                    trainer::endGameSticks(scores, std::atoi(f[5].c_str()));
            outLine = intsCsv(add.data(), 4);
            checksum += add[0];
        } else if (kind == "honba" && f.size() >= 5) {
            const int v = trainer::nextHonba(std::atoi(f[1].c_str()), std::atoi(f[2].c_str()) != 0,
                                             std::atoi(f[3].c_str()) != 0,
                                             std::atoi(f[4].c_str()) != 0);
            outLine = std::to_string(v);
            checksum += v;
        } else if (kind == "alllast" && f.size() >= 10) {
            std::array<bool, 4> tenpai{};
            const int bits = std::atoi(f[5].c_str());
            for (int i = 0; i < 4; i++) {
                tenpai[static_cast<size_t>(i)] = (bits & (1 << i)) != 0;
            }
            std::array<int, 4> scores{};
            for (int i = 0; i < 4; i++) {
                scores[static_cast<size_t>(i)] = std::atoi(f[static_cast<size_t>(6 + i)].c_str());
            }
            const trainer::Rules rules = rulesOfPreset(f[1]);
            const bool v = trainer::stopAtAllLast(std::atoi(f[2].c_str()),
                                                  std::atoi(f[3].c_str()) != 0,
                                                  std::atoi(f[4].c_str()) != 0, tenpai, scores, rules);
            outLine = v ? "1" : "0";
            checksum += v ? 1 : 0;
        } else if (kind == "west" && f.size() >= 5) {
            const trainer::Rules rules = rulesOfPreset(f[1]);
            const bool v = trainer::keepPlayingWest(rules, std::atoi(f[2].c_str()),
                                                    std::atoi(f[3].c_str()),
                                                    std::atoi(f[4].c_str()));
            outLine = v ? "1" : "0";
            checksum += v ? 1 : 0;
        } else if (kind == "nagashi" && f.size() >= 3) {
            const std::array<int, 4> d = trainer::nagashiPayments(std::atoi(f[1].c_str()),
                                                                  std::atoi(f[2].c_str()));
            outLine = intsCsv(d.data(), 4);
        } else if (kind == "ngelig" && f.size() >= 3) {
            const bool v = trainer::nagashiEligible(parseIntList(f[2]), std::atoi(f[1].c_str()) != 0);
            outLine = v ? "1" : "0";
            checksum += v ? 1 : 0;
        } else if (kind == "split" && f.size() >= 4) {
            const std::array<int, 2> sr = trainer::splitRemainder(std::atoi(f[1].c_str()),
                                                                  std::atoi(f[2].c_str()),
                                                                  std::atoi(f[3].c_str()));
            outLine = std::to_string(sr[0]) + " " + std::to_string(sr[1]);
        } else if (kind == "seeds" && f.size() >= 3) {
            const int n = std::atoi(f[2].c_str());
            outLine.clear();
            for (int i = 0; i < n; i++) {
                if (i > 0) {
                    outLine += ',';
                }
                outLine += std::to_string(trainer::roundSeedAt(std::stoll(f[1]), i));
            }
        } else if (kind == "seedfor" && f.size() >= 3) {
            const int n = std::atoi(f[2].c_str());
            outLine.clear();
            for (int i = 0; i < n; i++) {
                if (i > 0) {
                    outLine += ',';
                }
                outLine += std::to_string(trainer::seedFor(std::stoll(f[1]), i));
            }
        } else if (kind == "place" && f.size() >= 5) {
            std::array<int, 4> scores{};
            for (int i = 0; i < 4; i++) {
                scores[static_cast<size_t>(i)] = std::atoi(f[static_cast<size_t>(1 + i)].c_str());
            }
            const std::array<int, 4> p = trainer::placementOf(scores);
            outLine = intsCsv(p.data(), 4);
        } else if (kind == "rank" && f.size() >= 2) {
            outLine = std::to_string(trainer::claimRankOf(f[1]));
        } else if (kind == "beat" && f.size() >= 4) {
            bool hasBest = f[1] != "-";
            trainer::Claim best;
            if (hasBest) {
                const std::vector<std::string> bd = splitOn(f[1], ':');
                best.rank = std::atoi(bd[0].c_str());
                best.seatDist = bd.size() > 1 ? std::atoi(bd[1].c_str()) : 0;
            }
            const bool v = trainer::claimCanBeat(hasBest, best, std::atoi(f[2].c_str()),
                                                 std::atoi(f[3].c_str()));
            outLine = v ? "1" : "0";
        } else if (kind == "allron" && f.size() >= 3) {
            const bool v = trainer::claimAllRonAnswered(parseIntList(f[1]), intSetOf(f[2]));
            outLine = v ? "1" : "0";
        } else if (kind == "stop" && f.size() >= 8) {
            bool hasBest = f[5] != "-";
            trainer::Claim best;
            if (hasBest) {
                const std::vector<std::string> bd = splitOn(f[5], ':');
                best.rank = std::atoi(bd[0].c_str());
                best.seatDist = bd.size() > 1 ? std::atoi(bd[1].c_str()) : 0;
            }
            const bool v = trainer::claimShouldStop(parseIntList(f[1]), parseIntList(f[2]),
                                                    intSetOf(f[3]), std::stoll(f[4]), hasBest, best,
                                                    intMapOf(f[6]), intMapOf(f[7]));
            outLine = v ? "1" : "0";
        } else if (kind == "furiten" && f.size() >= 7) {
            // furiten <mc> <handKindsCsv> <discardKindsCsv|-> <temp> <perm> <seat>
            // 输出：ownKindsCsv waitsCsv temp perm isFuriten
            const int mc = std::atoi(f[1].c_str());
            const std::vector<int> handKinds = parseIntList(f[2]);
            const std::vector<int> discards = parseIntList(f[3]);
            const int temp = std::atoi(f[4].c_str());
            const int perm = std::atoi(f[5].c_str());
            const int seat = std::atoi(f[6].c_str());
            trainer::Counts counts{};
            for (int k : handKinds) {
                counts[static_cast<size_t>(k)]++;
            }
            trainer::FuritenState st;
            st.temp[static_cast<size_t>(seat)] = temp != 0;
            st.perm[static_cast<size_t>(seat)] = perm != 0;
            for (int k : discards) {
                st.recordDiscard(seat, k);
            }
            const std::vector<int> own = st.ownDiscardKinds(seat);
            const std::vector<int> waits = trainer::agariWaits(counts, mc);
            outLine = own.empty() ? "-" : intsCsv(own.data(), static_cast<int>(own.size()));
            outLine += ' ';
            outLine += waits.empty() ? "-" : intsCsv(waits.data(), static_cast<int>(waits.size()));
            outLine += ' ';
            outLine += std::to_string(temp != 0 ? 1 : 0);
            outLine += ' ';
            outLine += std::to_string(perm != 0 ? 1 : 0);
            outLine += ' ';
            outLine += st.isFuriten(seat, waits) ? "1" : "0";
        } else if (kind == "visible" && f.size() >= 7) {
            // visible <river0> <river1> <river2> <river3> <meldsSpec|-> <doraCsv|->
            // 输出：34 维可见计数（csv）+ total
            std::array<std::vector<int>, 4> discards;
            std::array<std::vector<trainer::Meld>, 4> melds;
            for (int s = 0; s < 4; s++) {
                discards[static_cast<size_t>(s)] = parseIntList(f[static_cast<size_t>(1 + s)]);
            }
            if (f[5] != "-") {
                for (const std::string &item : splitOn(f[5], ';')) {
                    const size_t c1 = item.find(':');
                    if (c1 == std::string::npos) {
                        continue;
                    }
                    const int seat = std::atoi(item.substr(0, c1).c_str());
                    if (seat < 0 || seat > 3) {
                        continue;
                    }
                    const size_t c2 = item.find(':', c1 + 1);
                    if (c2 == std::string::npos) {
                        continue;
                    }
                    trainer::Meld m;
                    switch (item[c1 + 1]) {
                        case 'r': m.kind = trainer::Meld::Kind::CHI; break;
                        case 't': m.kind = trainer::Meld::Kind::PON; break;
                        case 'q': m.kind = (item[c1 + 2] == 'c') ? trainer::Meld::Kind::ANKAN
                                                                 : trainer::Meld::Kind::DAIMINKAN;
                                  break;
                        case 'k': m.kind = trainer::Meld::Kind::KAKAN; break;
                        default: continue;
                    }
                    const std::vector<int> ids = parseIntList(item.substr(c2 + 1));
                    m.tileCount = static_cast<int>(ids.size());
                    for (size_t i = 0; i < ids.size() && i < 4; i++) {
                        m.tiles[i] = ids[i];
                    }
                    melds[static_cast<size_t>(seat)].push_back(m);
                }
            }
            const trainer::Counts vis = trainer::visibleCounts(discards, melds,
                                                               parseIntList(f[6]));
            int visArr[trainer::kKindCount];
            for (int k = 0; k < trainer::kKindCount; k++) {
                visArr[k] = vis[static_cast<size_t>(k)];
            }
            outLine = intsCsv(visArr, trainer::kKindCount);
            outLine += ' ';
            outLine += std::to_string(trainer::sumOf(vis));
        } else if (kind == "vis" && f.size() >= 3) {
            // vis <visibleCsv34> <ownCsv34> → unseen34 drawable34
            trainer::Counts vis{};
            trainer::Counts own{};
            {
                const std::vector<int> v = parseIntList(f[1]);
                const std::vector<int> o = parseIntList(f[2]);
                for (size_t i = 0; i < v.size() && i < trainer::kKindCount; i++) {
                    vis[i] = static_cast<uint8_t>(v[i]);
                }
                for (size_t i = 0; i < o.size() && i < trainer::kKindCount; i++) {
                    own[i] = static_cast<uint8_t>(o[i]);
                }
            }
            const trainer::Counts un = trainer::visibleUnseen(vis);
            const trainer::Counts dr = trainer::visibleDrawable(vis, own);
            int unArr[trainer::kKindCount];
            int drArr[trainer::kKindCount];
            for (int k = 0; k < trainer::kKindCount; k++) {
                unArr[k] = un[static_cast<size_t>(k)];
                drArr[k] = dr[static_cast<size_t>(k)];
            }
            outLine = intsCsv(unArr, trainer::kKindCount);
            outLine += ' ';
            outLine += intsCsv(drArr, trainer::kKindCount);
        } else if (kind == "block" && f.size() >= 3) {
            outLine = trainer::winBlockReason(std::atoi(f[1].c_str()) != 0,
                                              std::atoi(f[2].c_str()) != 0);
        } else if (kind == "wcounts" && f.size() >= 5) {
            // wcounts <mc> <concealedCsv> <winKind> <tsumo> → 并入后的计数（张数不符 → -）
            trainer::Counts concealed{};
            {
                const std::vector<int> v = parseIntList(f[2]);
                for (size_t i = 0; i < v.size() && i < trainer::kKindCount; i++) {
                    concealed[i] = static_cast<uint8_t>(v[i]);
                }
            }
            trainer::Counts merged{};
            const bool okCnt = trainer::winCounts(concealed, std::atoi(f[1].c_str()),
                                                  std::atoi(f[3].c_str()), std::atoi(f[4].c_str()) != 0,
                                                  merged);
            if (!okCnt) {
                outLine = "-";
            } else {
                int arr[trainer::kKindCount];
                for (int k = 0; k < trainer::kKindCount; k++) {
                    arr[k] = merged[static_cast<size_t>(k)];
                }
                outLine = intsCsv(arr, trainer::kKindCount);
            }
        } else if (kind == "ropts" && f.size() >= 4) {
            // ropts <mode> …  —— `RoundOptions` 的四个纯函数（选项生成的第一层）：
            //   discard    <riichi> <drawn> <handIdsCsv> <forbiddenKindsCsv|->
            //   kuikae     <calledKind> <k0> <k1>
            //   kanriichi  <waitsBeforeCsv|-> <concealed34Csv> <meldCount> <kind>
            //   kanauto    <concealed14Csv> <meldCount> <kind> <drawnKind|-1>（真实调用点口径）
            //   chi        <concealed34Csv> <calledKind>
            const std::string mode = f[1];
            if (mode == "discard" && f.size() >= 6) {
                const bool riichi = std::atoi(f[2].c_str()) != 0;
                const int drawn = std::atoi(f[3].c_str());
                const std::vector<int> hand = parseIntList(f[4]);
                const std::vector<int> forb = parseIntList(f[5]);
                const std::vector<std::string> v = trainer::discardChoices(hand, riichi, drawn, forb);
                for (size_t i = 0; i < v.size(); i++) {
                    outLine += (i ? "," : "");
                    outLine += v[i];
                }
            } else if (mode == "kuikae" && f.size() >= 5) {
                const std::vector<int> v = trainer::kuikaeForbidden(std::atoi(f[2].c_str()),
                                                                    std::atoi(f[3].c_str()),
                                                                    std::atoi(f[4].c_str()));
                for (size_t i = 0; i < v.size(); i++) {
                    outLine += (i ? "," : "");
                    outLine += std::to_string(v[i]);
                }
            } else if (mode == "kanriichi" && f.size() >= 6) {
                const std::vector<int> waitsBefore = parseIntList(f[2]);
                trainer::Counts concealed{};
                {
                    const std::vector<int> v = parseIntList(f[3]);
                    for (size_t i = 0; i < v.size() && i < trainer::kKindCount; i++) {
                        concealed[i] = static_cast<uint8_t>(v[i]);
                    }
                }
                outLine = trainer::kanAllowedAfterRiichi(waitsBefore, concealed,
                                                         std::atoi(f[4].c_str()),
                                                         std::atoi(f[5].c_str())) ? "1" : "0";
            } else if (mode == "kanauto" && f.size() >= 6) {
                // **真实调用点**语义（`Round.kanAllowedByRiichi`）：计数是**自己回合的 14 张**，
                // 「杠前听牌」要先把刚摸到的那张减掉再算，而传给 `kanAllowedAfterRiichi` 的仍是 14 张的
                trainer::Counts cc{};
                {
                    const std::vector<int> v = parseIntList(f[2]);
                    for (size_t i = 0; i < v.size() && i < trainer::kKindCount; i++) {
                        cc[i] = static_cast<uint8_t>(v[i]);
                    }
                }
                const int mc = std::atoi(f[3].c_str());
                const int kk = std::atoi(f[4].c_str());
                const int dk = std::atoi(f[5].c_str());
                trainer::Counts before = cc;
                if (dk >= 0 && before[static_cast<size_t>(dk)] > 0) {
                    before[static_cast<size_t>(dk)] =
                        static_cast<uint8_t>(before[static_cast<size_t>(dk)] - 1);
                }
                const std::vector<int> wa = trainer::waits(before, mc);
                outLine = trainer::kanAllowedAfterRiichi(wa, cc, mc, kk) ? "1" : "0";
            } else if (mode == "chi" && f.size() >= 4) {
                trainer::Counts concealed{};
                {
                    const std::vector<int> v = parseIntList(f[2]);
                    for (size_t i = 0; i < v.size() && i < trainer::kKindCount; i++) {
                        concealed[i] = static_cast<uint8_t>(v[i]);
                    }
                }
                const auto sets = trainer::chiSets(concealed, std::atoi(f[3].c_str()));
                for (size_t i = 0; i < sets.size(); i++) {
                    outLine += (i ? ";" : "");
                    outLine += sets[i][0] + "," + sets[i][1];
                }
                if (sets.empty()) {
                    outLine = "-";
                }
            } else {
                std::fprintf(stderr, "认不出的 ropts 行：%s\n", text.c_str());
                return 2;
            }
        } else if (kind == "rinit" && f.size() >= 8) {
            // rinit <preset> <seed> <dealer> <sticks> <s0> <s1> <s2> <s3>
            // 输出（**构造后 / 配牌后**两组）：
            //   menzenBits tempBits permBits doubleRiichiBits ippatsuBits tilesLeftBefore
            //   tilesLeft deadWallLeft openingTile canKan handSizes(d0..3) hand0;hand1;hand2;hand3
            //   doraKinds uraKinds playerDraws discardsSinceRiichi
            const trainer::Rules rules = rulesOfPreset(f[1]);
            const int64_t seed = std::stoll(f[2]);
            const int dealer = std::atoi(f[3].c_str());
            std::array<int, 4> scores = {25000, 25000, 25000, 25000};
            trainer::RoundState rs(rules, 0, 1, 0, dealer, scores, std::atoi(f[4].c_str()), seed);
            int menzenBits = 0;
            int tempBits = 0;
            int permBits = 0;
            int dblBits = 0;
            int ippatsuBits = 0;
            for (int i = 0; i < 4; i++) {
                if (rs.menzen[static_cast<size_t>(i)]) {
                    menzenBits |= 1 << i;
                }
                if (rs.furiten.temp[static_cast<size_t>(i)]) {
                    tempBits |= 1 << i;
                }
                if (rs.furiten.perm[static_cast<size_t>(i)]) {
                    permBits |= 1 << i;
                }
                if (rs.doubleRiichi[static_cast<size_t>(i)]) {
                    dblBits |= 1 << i;
                }
                if (rs.ippatsu[static_cast<size_t>(i)]) {
                    ippatsuBits |= 1 << i;
                }
            }
            const int tilesLeftBefore = rs.tilesLeft();
            rs.setup();
            outLine = std::to_string(menzenBits) + " " + std::to_string(tempBits) + " "
                    + std::to_string(permBits) + " " + std::to_string(dblBits) + " "
                    + std::to_string(ippatsuBits) + " " + std::to_string(tilesLeftBefore) + " "
                    + std::to_string(rs.tilesLeft()) + " " + std::to_string(rs.deadWallLeft()) + " "
                    + std::to_string(rs.openingTile) + " " + (rs.canKan() ? "1" : "0");
            std::string sizes;
            std::string hands;
            for (int i = 0; i < 4; i++) {
                sizes += (i ? "," : "");
                sizes += std::to_string(rs.hand[static_cast<size_t>(i)].size());
                hands += (i ? ";" : "");
                hands += intsCsv(rs.hand[static_cast<size_t>(i)].data(),
                                 static_cast<int>(rs.hand[static_cast<size_t>(i)].size()));
            }
            outLine += " " + sizes + " " + hands;
            const std::vector<int> dora = rs.wall.doraIndicators();
            const std::vector<int> ura = rs.wall.uraIndicators();
            outLine += " " + (dora.empty() ? "-" : intsCsv(dora.data(), static_cast<int>(dora.size())));
            outLine += " " + (ura.empty() ? "-" : intsCsv(ura.data(), static_cast<int>(ura.size())));
            outLine += " " + intsCsv(rs.playerDraws.data(), 4);
            outLine += " " + intsCsv(rs.discardsSinceRiichi.data(), 4);
        } else if (kind == "accept" && f.size() >= 5) {
            const bool v = trainer::claimAcceptsReply(
                    std::atoi(f[1].c_str()) != 0, f[2] != "-", std::stoll(f[2] == "-" ? "0" : f[2]),
                    std::atoi(f[3].c_str()) != 0, std::stoll(f[4]));
            outLine = v ? "1" : "0";
        } else {
            std::fprintf(stderr, "认不出的语料行：%s\n", text.c_str());
            std::fclose(in);
            std::fclose(out);
            return 2;
        }
        outLine += '\n';
        std::fwrite(outLine.data(), 1, outLine.size(), out);
        rows++;
    }
    std::fclose(in);
    std::fclose(out);
    std::fprintf(stderr, "[trainer] settle 语料 %lld 行 校验和 %lld\n", rows, checksum);
    return 0;
}

int cmdScore(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr, "用法：trainer score <corpus> <out>\n");
        return 2;
    }
    std::FILE *in = std::fopen(argv[1], "rb");
    if (in == nullptr) {
        std::fprintf(stderr, "读不到语料：%s\n", argv[1]);
        return 2;
    }
    std::FILE *out = std::fopen(argv[2], "wb");
    if (out == nullptr) {
        std::fprintf(stderr, "写不了输出：%s\n", argv[2]);
        std::fclose(in);
        return 2;
    }
    char line[8192];
    long long rows = 0;
    long long checksum = 0;
    const auto t0 = std::chrono::steady_clock::now();
    while (std::fgets(line, sizeof(line), in) != nullptr) {
        std::string text(line);
        while (!text.empty() && (text.back() == '\n' || text.back() == '\r')) {
            text.pop_back();
        }
        if (text.empty()) {
            continue;
        }
        ScoreRow row;
        if (!readScoreRow(text, row)) {
            continue;
        }
        trainer::WinContext ctx;
        winContextOf(row, ctx);
        trainer::Counts concealed{};
        for (int id : row.hand) {
            concealed[static_cast<size_t>(trainer::kindOf(id))]++;
        }
        const trainer::HandScore s = trainer::evaluate(ctx, concealed, row.melds, row.winKind);
        const trainer::PaymentResult p = trainer::paymentsCompute(
                s, row.seat, row.loser, row.dealerSeat, row.honba, row.sticks, ctx.tsumo, row.paos);

        checksum += s.base + p.winnerGain;
        std::string outLine;
        outLine += s.valid ? '1' : '0';
        outLine += ' ';
        outLine += std::to_string(s.hanYaku);
        outLine += ' ';
        outLine += std::to_string(s.han);
        outLine += ' ';
        outLine += std::to_string(s.fu);
        outLine += ' ';
        outLine += std::to_string(s.yakuman);
        outLine += ' ';
        outLine += std::to_string(s.dora);
        outLine += ' ';
        outLine += std::to_string(s.ura);
        outLine += ' ';
        outLine += std::to_string(s.aka);
        outLine += ' ';
        outLine += std::to_string(s.base);
        outLine += ' ';
        const std::string limitCode = trainer::limitCodeOf(s.limit);
        outLine += limitCode.empty() ? "-" : limitCode;
        outLine += ' ';
        outLine += reasonTag(s.reason);
        outLine += ' ';
        appendFormSig(outLine, s);
        outLine += ' ';
        if (s.yaku.empty()) {
            outLine += '-';
        } else {
            for (size_t i = 0; i < s.yaku.size(); i++) {
                if (i > 0) {
                    outLine += ',';
                }
                const trainer::Yaku &y = s.yaku[i];
                const std::string code = trainer::yakuCodeOf(y.name);
                outLine += code;
                outLine += ':';
                outLine += std::to_string(y.equivalentHan());
                outLine += ':';
                outLine += std::to_string(y.yakuman);
                outLine += ':';
                outLine += trainer::yakuTileOf(y.name);
            }
        }
        outLine += ' ';
        for (int i = 0; i < 4; i++) {
            if (i > 0) {
                outLine += ',';
            }
            outLine += std::to_string(p.delta[static_cast<size_t>(i)]);
        }
        outLine += ' ';
        outLine += std::to_string(p.winnerGain);
        outLine += ' ';
        outLine += std::to_string(p.winnerPoints);
        outLine += ' ';
        outLine += std::to_string(p.riichiTaken);
        outLine += '\n';
        std::fwrite(outLine.data(), 1, outLine.size(), out);
        rows++;
    }
    std::fclose(in);
    std::fclose(out);
    const double sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    std::fprintf(stderr, "[trainer] score 语料 %lld 行，用时 %.3f s（%.0f 行/秒） 校验和 %lld\n",
                 rows, sec, rows / sec, checksum);
    return 0;
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

// ---------------------------------------------------------------- 自家回合询问（`turnopts`）
//
// 语料由 `tools/RoundProbe.java` 产出（**真实牌局**，不是构造局面）：每次自家回合询问一行
//   <state> @ <javaOptions>
// 本命令只读 `@` 左边那半（局面），用 `turnOptionsText` 重算一遍选项，与 Java 那半逐字符比
// （比较在 `tools/trainer-opts-parity.mjs` 里做）。
std::vector<std::string> splitWs(const std::string &s) {
    std::vector<std::string> out;
    size_t i = 0;
    while (i < s.size()) {
        while (i < s.size() && (s[i] == ' ' || s[i] == '\t' || s[i] == '\r')) {
            i++;
        }
        if (i >= s.size()) {
            break;
        }
        const size_t start = i;
        while (i < s.size() && s[i] != ' ' && s[i] != '\t' && s[i] != '\r') {
            i++;
        }
        out.push_back(s.substr(start, i - start));
    }
    return out;
}

/** `chi:2:52:51,52,53;pon:1:8:8,8,8` → 副露列表（`-` = 无）。 */
bool parseMelds(const std::string &spec, std::vector<trainer::Meld> &out) {
    if (spec == "-" || spec.empty()) {
        return true;
    }
    for (const std::string &one : splitOn(spec, ';')) {
        const std::vector<std::string> parts = splitOn(one, ':');
        if (parts.size() != 4) {
            return false;
        }
        trainer::Meld m;
        const std::string &w = parts[0];
        if (w == "chi") {
            m.kind = trainer::Meld::Kind::CHI;
        } else if (w == "pon") {
            m.kind = trainer::Meld::Kind::PON;
        } else if (w == "ankan") {
            m.kind = trainer::Meld::Kind::ANKAN;
        } else if (w == "kakan") {
            m.kind = trainer::Meld::Kind::KAKAN;
        } else if (w == "daiminkan") {
            m.kind = trainer::Meld::Kind::DAIMINKAN;
        } else {
            return false;
        }
        m.from = std::atoi(parts[1].c_str());
        m.calledId = std::atoi(parts[2].c_str());
        const std::vector<int> tiles = parseIntList(parts[3]);
        m.tileCount = static_cast<int>(tiles.size());
        for (size_t i = 0; i < tiles.size() && i < 4; i++) {
            m.tiles[i] = tiles[i];
        }
        out.push_back(m);
    }
    return true;
}

int cmdTurnOpts(int argc, char **argv) {
    if (argc < 3) {
        std::fprintf(stderr, "用法：trainer turnopts <RoundProbe 语料> <out>\n");
        return 2;
    }
    std::FILE *in = std::fopen(argv[1], "rb");
    if (!in) {
        std::fprintf(stderr, "读不到语料：%s\n", argv[1]);
        return 2;
    }
    std::FILE *out = std::fopen(argv[2], "wb");
    if (!out) {
        std::fprintf(stderr, "写不了输出：%s\n", argv[2]);
        std::fclose(in);
        return 2;
    }
    char line[16384];
    long rows = 0;
    long long checksum = 0;
    while (std::fgets(line, sizeof line, in)) {
        std::string text = line;
        while (!text.empty() && (text.back() == '\n' || text.back() == '\r')) {
            text.pop_back();
        }
        if (text.empty()) {
            continue;
        }
        const size_t at = text.find(" @ ");
        if (at == std::string::npos) {
            std::fprintf(stderr, "语料行没有 ` @ `：%s\n", text.c_str());
            std::fclose(in);
            std::fclose(out);
            return 2;
        }
        const std::vector<std::string> f = splitWs(text.substr(0, at));
        if (f.empty()) {
            continue;
        }
        if (f[0] == "c") {
            // c seat from calledCode houtei handIds melds menzen riichi dbl ippatsu fromRiichi
            //   fromDiscards playerDraws anyCall dealer roundWind tileId ever temp perm
            //   deadWallLeft kanCount dora ura preset   → 25 个 token
            if (f.size() < 25) {
                std::fprintf(stderr, "认不出的鸣牌语料行（%zu 个 token）\n", f.size());
                std::fclose(in);
                std::fclose(out);
                return 2;
            }
            trainer::ClaimAsk a;
            a.seat = std::atoi(f[1].c_str());
            a.from = std::atoi(f[2].c_str());
            a.calledKind = trainer::parseKind(f[3]);
            a.calledId = a.calledKind < 0 ? -1 : trainer::idOf(a.calledKind, 3);
            a.houtei = std::atoi(f[4].c_str()) != 0;
            a.handIds = parseIntList(f[5]);
            for (int id : a.handIds) {
                a.hand[static_cast<size_t>(trainer::kindOf(id))]++;
            }
            if (!parseMelds(f[6], a.melds)) {
                std::fprintf(stderr, "认不出的副露：%s\n", f[6].c_str());
                std::fclose(in);
                std::fclose(out);
                return 2;
            }
            a.menzen = std::atoi(f[7].c_str()) != 0;
            a.riichi = std::atoi(f[8].c_str()) != 0;
            a.doubleRiichi = std::atoi(f[9].c_str()) != 0;
            a.ippatsu = std::atoi(f[10].c_str()) != 0;
            a.fromRiichi = std::atoi(f[11].c_str()) != 0;
            a.fromDiscardsSinceRiichi = std::atoi(f[12].c_str());
            a.playerDraws = std::atoi(f[13].c_str());
            a.anyCall = std::atoi(f[14].c_str()) != 0;
            a.dealer = std::atoi(f[15].c_str());
            a.roundWind = std::atoi(f[16].c_str());
            // 被鸣那张的真实牌 id（燕返比的是 id；赤五要能区分）
            {
                const int id = std::atoi(f[17].c_str());
                a.calledId = id;
                if (id >= 0) {
                    a.calledKind = trainer::kindOf(id);
                }
            }
            // 舍张振听的账：`kind:cnt;…`
            if (f[18] != "-") {
                for (const std::string &pair : splitOn(f[18], ';')) {
                    const std::vector<std::string> kv = splitOn(pair, ':');
                    if (kv.size() == 2) {
                        const int k = std::atoi(kv[0].c_str());
                        if (k >= 0 && k < trainer::kKindCount) {
                            a.discardKindsEver[static_cast<size_t>(k)] =
                                static_cast<uint8_t>(std::atoi(kv[1].c_str()));
                        }
                    }
                }
            }
            a.furitenTemp = std::atoi(f[19].c_str()) != 0;
            a.furitenPerm = std::atoi(f[20].c_str()) != 0;
            a.deadWallLeft = std::atoi(f[21].c_str());
            a.kanCount = std::atoi(f[22].c_str());
            a.doraKinds = parseIntList(f[23]);
            a.uraKinds = parseIntList(f[24]);
            a.rules = rulesOfPreset(f.size() > 25 ? f[25] : "mleague");
            const std::string res = trainer::claimOptionsText(a);
            std::fprintf(out, "%s\n", res.c_str());
            rows++;
            for (char ch : res) {
                checksum = checksum * 131 + static_cast<unsigned char>(ch);
            }
            continue;
        }
        // t seat drawn rinshan atLastLive handIds melds menzen riichi dbl ippatsu scores
        //   roundWind kyoku honba dealer tilesLeft deadWallLeft kanCount anyCall playerDraws
        //   doraKinds uraKinds forbidden preset   → 25 个 token
        if (f.size() < 25 || f[0] != "t") {
            std::fprintf(stderr, "认不出的语料行（%zu 个 token）：%s\n", f.size(),
                         text.substr(0, 40).c_str());
            std::fclose(in);
            std::fclose(out);
            return 2;
        }
        trainer::TurnAsk a;
        a.seat = std::atoi(f[1].c_str());
        if (f[2] != "-") {
            const int k = trainer::parseKind(f[2]);
            const bool red = !f[2].empty() && f[2][0] == '0';
            a.drawnKind = k;
            a.drawnId = k < 0 ? -1 : trainer::idOf(k, red ? 0 : 3);
        }
        a.rinshan = std::atoi(f[3].c_str()) != 0;
        a.atLastLive = std::atoi(f[4].c_str()) != 0;
        a.handIds = parseIntList(f[5]);
        for (int id : a.handIds) {
            a.hand[static_cast<size_t>(trainer::kindOf(id))]++;
        }
        if (!parseMelds(f[6], a.melds)) {
            std::fprintf(stderr, "认不出的副露：%s\n", f[6].c_str());
            std::fclose(in);
            std::fclose(out);
            return 2;
        }
        a.menzen = std::atoi(f[7].c_str()) != 0;
        a.riichi = std::atoi(f[8].c_str()) != 0;
        a.doubleRiichi = std::atoi(f[9].c_str()) != 0;
        a.ippatsu = std::atoi(f[10].c_str()) != 0;
        {
            const std::vector<int> sc = parseIntList(f[11]);
            for (size_t i = 0; i < sc.size() && i < 4; i++) {
                a.scores[i] = sc[i];
            }
        }
        a.roundWind = std::atoi(f[12].c_str());
        a.kyoku = std::atoi(f[13].c_str());
        a.honba = std::atoi(f[14].c_str());
        a.dealer = std::atoi(f[15].c_str());
        a.tilesLeft = std::atoi(f[16].c_str());
        a.deadWallLeft = std::atoi(f[17].c_str());
        a.kanCount = std::atoi(f[18].c_str());
        a.anyCall = std::atoi(f[19].c_str()) != 0;
        a.playerDraws = std::atoi(f[20].c_str());
        a.doraKinds = parseIntList(f[21]);
        a.uraKinds = parseIntList(f[22]);
        a.forbiddenKinds = parseIntList(f[23]);
        a.rules = rulesOfPreset(f[24]);

        const std::string res = trainer::turnOptionsText(a);
        std::fprintf(out, "%s\n", res.c_str());
        rows++;
        for (char ch : res) {
            checksum = checksum * 131 + static_cast<unsigned char>(ch);
        }
    }
    std::fclose(in);
    std::fclose(out);
    std::fprintf(stderr, "[trainer] turnopts %ld 行 校验和 %lld\n", rows, checksum);
    return 0;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr,
                     "用法：trainer <wall|rng|rules|bench|score|settle|action|turnopts|selfplay|features|--selftest> …\n"
                     "  wall  <seed> [aka] [dealer]      牌山/配牌/指示牌/岭上（JSON）\n"
                     "  rng   <seed> <n>                 java.util.Random.nextInt(136) 前 n 个\n"
                     "  rules <corpus> <mode> <out>      语料 → 逐行结果（mode = shanten|of|discard）\n"
                     "  bench <corpus> <mode> <reps>     同语料计时（性能基准）\n"
                     "  score <corpus> <out>             语料 → 役种/符/点数/授受（与 ScoreProbe.java 对拍）\n"
                     "  settle <corpus> <out>            语料 → 顺位点/余棒/连庄判据（与 SettleProbe.java 对拍）\n"
                     "  action <corpus> <out>            语料 → 动作键/下标/回包（与 ActionProbe.java 对拍）\n"
                     "  turnopts <javaCorpus> <out>       真实牌局的自家回合询问 → 选项文本（与 RoundProbe.java 对拍）\n"
                     "  selfplay <games> [--workers K] [--policy P] [--seed S] [--hands H] [--out DIR]\n"
                     "                                   [--rotate] [--sample K] [--no-claims] [--preset NAME]\n"
                     "                                   自对弈并写出轨迹（与 Java --selfplay 逐字节对拍）\n"
                     "  features <dir> [--workers K]      轨迹目录 → 派生特征 sidecar `g*.feat.bin`\n"
                     "                                   （= Java --features，逐字节对拍）\n");
        return 2;
    }
    const std::string cmd = argv[1];
    if (cmd == "selfplay") {
        return trainer::selfplayCli(argc - 1, argv + 1);
    }
    if (cmd == "features" || cmd == "--features") {
        // 与 Java CLI 同名同义（`--features` 也认，省得两个生产者要记两套写法）
        return trainer::featuresCli(argc - 1, argv + 1);
    }
    if (cmd == "wall") {
        return cmdWall(argc, argv);
    }
    if (cmd == "rng") {
        return cmdRng(argc, argv);
    }
    if (cmd == "score") {
        return cmdScore(argc - 1, argv + 1);
    }
    if (cmd == "action") {
        return cmdAction(argc - 1, argv + 1);
    }
    if (cmd == "settle") {
        return cmdSettle(argc - 1, argv + 1);
    }
    if (cmd == "turnopts") {
        return cmdTurnOpts(argc - 1, argv + 1);
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
