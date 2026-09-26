// `Round` 的**状态容器 + 配牌** —— 与 Java `mahjong.game.Round` 的构造函数与 `setup()` 同口径。
//
// 这是 `Round` 本体的第一块。两条被踩过/被点名的不变量在这里钉住：
//   ① **`menzen[]` 必须初始化为 `true`**（Java `boolean[]` 默认 false —— 漏了会让**所有门前役**
//      在实局失效，而单测因为显式传 `ctx.menzen=true` 照样通过，AGENTS §2.3-2）；
//   ② 配牌顺序是 **3 轮 × 4 家 × 一次抓 4 张**（共 12）→ 每人补 1 张（13）→ 庄家第 14 张
//      （`openingTile`，同时充当"本次摸到的牌"）→ `finishDealing` → 按 `compareTile` 排序。
//      ⚠ 别写成"13 巡 × 4 家 × 1 张"（2026-09 的假阳性就是这么来的，见 docs/TRAINER-CPP.md §6.7）。
#pragma once

#include <algorithm>
#include <array>
#include <cstdint>
#include <vector>

#include "furiten.hpp"
#include "meld.hpp"
#include "rules.hpp"
#include "tiles.hpp"
#include "wall.hpp"

namespace trainer {

struct RoundState {
    trainer::Rules rules{};
    int roundWind = 0;
    int kyoku = 1;
    int honba = 0;
    int dealer = 0;
    int sticks = 0;
    std::array<int, 4> scores{};
    trainer::Wall wall;
    std::array<std::vector<int>, 4> hand;
    std::array<std::vector<Meld>, 4> melds;
    std::array<std::vector<int>, 4> discards;
    std::array<bool, 4> menzen{};
    std::array<bool, 4> riichi{};
    std::array<bool, 4> doubleRiichi{};
    std::array<bool, 4> ippatsu{};
    std::array<bool, 4> hadDiscardCalled{};
    std::array<int, 4> playerDraws{};
    std::array<int, 4> discardsSinceRiichi{};
    FuritenState furiten;
    int openingTile = -1;
    int kanCount = 0;

    RoundState(const trainer::Rules &r, int wind, int ky, int hb, int dl,
               const std::array<int, 4> &sc, int st, int64_t seed)
        : rules(r), roundWind(wind), kyoku(ky), honba(hb), dealer(dl), sticks(st), scores(sc),
          wall(seed, r.aka) {        // Java `Round` 构造函数：四家的容器建好、**`menzen` 全 true**、牌山在构造时就备好
        // （自检会拿一个没开打过的 Round 去问 `canKan()` / `deadWallLeft()`）。
        for (int i = 0; i < 4; i++) {
            menzen[static_cast<size_t>(i)] = true;
        }
    }

    /** Java `Round.compareTile`：先 kind，再"赤五在前"，最后比 id。 */
    static bool compareTile(int a, int b) {
        const int ka = kindOf(a);
        const int kb = kindOf(b);
        if (ka != kb) {
            return ka < kb;
        }
        const bool ra = isRedId(a);
        const bool rb = isRedId(b);
        if (ra != rb) {
            return ra;                 // 赤五在前
        }
        return a < b;
    }

    /** Java `Round.setup()`：配牌 + 庄家第 14 张 + `finishDealing` + 排序。 */
    void setup() {
        for (int r = 0; r < 3; r++) {
            for (int s = 0; s < 4; s++) {
                auto &h = hand[static_cast<size_t>((dealer + s) % 4)];
                for (int t = 0; t < 4; t++) {
                    h.push_back(wall.deal());
                }
            }
        }
        for (int s = 0; s < 4; s++) {
            hand[static_cast<size_t>((dealer + s) % 4)].push_back(wall.deal());
        }
        openingTile = wall.deal();
        hand[static_cast<size_t>(dealer)].push_back(openingTile);
        wall.finishDealing();
        for (auto &h : hand) {
            std::sort(h.begin(), h.end(), compareTile);
        }
    }

    int tilesLeft() const { return wall.tilesLeft(); }

    /** 报文里的 `dead_wall_left`：王牌里还剩几张岭上牌（= 4 − 已摸走的岭上数）。 */
    int deadWallLeft() const { return wall.rinshanLeft(); }

    /** 本局还能不能再开杠：两个条件都要看（还有岭上牌 **且** 已开的杠 < 4）。 */
    bool canKan() const { return wall.rinshanLeft() > 0 && kanCount < kRinshanTiles; }

    /** 自家摸牌：同巡振听解除（Java 主循环里的 `furitenTemp[turn] = false`）。 */
    void onOwnDraw(int seat) { furiten.onOwnDraw(seat); }
};

}  // namespace trainer
