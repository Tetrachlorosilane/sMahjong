// 役种判定 + 符数 + 高点法 —— 与 Java `mahjong.rules.Evaluator` **逐分支同口径**。
//
// 这一层是训练数据的"钱袋子"：番/符/点数算错，轨迹里的 `score` / `hand_delta` / 奖励全废，
// 而且**不会报错**（数据看着都正常）。所以：
//   ① 役种名保留中文原样（Java 内部也用中文名，只有发报文时才翻成码）；
//   ② 高点法的比较顺序（基本点 → 番 → 符）与"并列取先出现的"必须一致 —— 它决定写进轨迹的役种列表；
//   ③ 规则的默认集是 **M.League 预设**（不是字段初始值），见 `rules.hpp` 顶部注释。
#pragma once

#include <string>
#include <vector>

#include "agari.hpp"
#include "meld.hpp"
#include "rules.hpp"

namespace trainer {

/** 单个役。 */
struct Yaku {
    std::string name;
    int han = 0;
    int yakuman = 0;

    static Yaku normal(std::string n, int h) { return Yaku{std::move(n), h, 0}; }
    static Yaku yk(std::string n, int mult) { return Yaku{std::move(n), 0, mult}; }

    /** 结算报文里上报的番数：役满按「13 × 倍数」折算（`han` 不能是 0）。 */
    int equivalentHan() const { return yakuman > 0 ? 13 * yakuman : han; }
};

/** 评价结果（与 Java `Evaluator.HandScore` 同字段）。 */
struct HandScore {
    std::vector<Yaku> yaku;
    int hanYaku = 0;      // 役的番数合计（不含宝牌）
    int han = 0;          // 含宝牌的番数合计
    int fu = 0;
    int yakuman = 0;
    int dora = 0;
    int ura = 0;
    int aka = 0;
    int base = 0;
    std::string limit;
    bool valid = false;
    std::string reason;
    Form form;
    bool hasForm = false;

    bool isYakuman() const { return yakuman > 0; }
    int totalHan() const { return isYakuman() ? 13 * yakuman : han; }
};

/** 和牌时的上下文（与 Java `WinContext` 同字段）。 */
struct WinContext {
    Rules rules{};
    int seat = 0;
    int dealerSeat = 0;
    int roundWind = 27;                       // 27=东 28=南 29=西 30=北
    bool tsumo = false;
    bool riichi = false;
    bool doubleRiichi = false;
    bool ippatsu = false;
    bool chankan = false;
    bool rinshan = false;
    bool haitei = false;
    bool houtei = false;
    bool tenhou = false;
    bool chiihou = false;
    bool renhou = false;
    bool tsubame = false;
    bool kanburi = false;
    bool nagashi = false;
    int winKind = 0;
    std::vector<int> doraIndicators;           // kind 列表（含杠宝牌）
    std::vector<int> uraIndicators;            // kind 列表
    std::vector<int> allTileIds;               // 手牌 + 副露的全部牌 id（数赤宝牌）
    bool menzen = false;

    int seatWind() const { return 27 + ((seat - dealerSeat + 4) % 4); }
};

/** 对外主入口：在所有和了形解释里取打点最高者。 */
HandScore evaluate(const WinContext &ctx, const Counts &concealed, const std::vector<Meld> &melds,
                   int winKind);

/** 番数 + 符数 → 基本点（含满贯以上各档、累计役满取舍、切上满贯）。 */
int basePoints(int han, int fu, const Rules &rules);

/** 符数计算（与 Java `Evaluator.calcFu` 同分支）。 */
int calcFu(const WinContext &ctx, const Form &f, bool menzen, const Rules &rules);

/** 平和判定。 */
bool isPinfu(const WinContext &ctx, const Form &f, bool menzen);

}  // namespace trainer
