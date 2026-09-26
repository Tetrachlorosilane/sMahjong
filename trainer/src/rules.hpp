// 规则配置 —— 与 Java `mahjong.core.Rules` **同字段同默认**。
//
// ⚠ 关键口径（别以为是"抄漏了"）：`Rules()` 构造时**先铺预设再保留**，所以
//   `Rules.defaults()`（= `new Rules()`）**等于** `applyPreset("mleague")` ——
//   训练数据全程跑的就是这一套（`docs/TRAINING.md` §0；`SelfPlay.oneGame` 用的就是 `Rules.defaults()`）。
//   也就是说字段初始值（`doubleYakuman = true` 等）会被 mleague 覆盖成
//   `doubleYakuman = false` / `kazoeYakuman = false` / `kiriageMangan = true` / `doubleWindPairFu = 2`。
//   谁要是只照抄字段初始值，打点表会**整片**对不上。
#pragma once

#include <array>
#include <string>

namespace trainer {

struct Rules {
    std::string length = "hanchan";
    int aka = 3;
    bool kuitan = true;
    bool ura = true;
    bool kanDora = true;
    bool doubleYakuman = true;
    std::string renhou = "off";                 // "off" / "mangan" / "yakuman"
    bool headBump = false;
    bool sanchaAbort = false;
    bool fourRiichiAbort = true;
    bool fourKanAbort = true;
    bool fourWindAbort = true;
    bool kyuushuAbort = true;
    bool nagashiMangan = true;
    bool tobi = true;
    bool agariyame = true;
    bool westExtension = false;
    bool kuikae = true;
    bool pao = true;
    bool koyaku = false;
    int notenPenalty = 3000;
    int startScore = 25000;
    int returnScore = 30000;
    std::array<int, 4> uma = {15, 5, -5, -15};
    int thinkingBaseMs = 5000;
    int thinkingBankMs = 20000;
    int thinkingMs = 5000;
    int minHan = 1;
    int requiredPoints = 0;
    std::string preset = "mleague";

    // ---- 与 M.League 的差异项（默认值 = 字段初始值，构造时会被预设覆盖）----
    bool kiriageMangan = false;
    bool kazoeYakuman = true;
    int doubleWindPairFu = 4;
    int riichiMinScore = 1000;
    int riichiMinTilesLeft = 4;
    bool riichiNoHaitei = false;
    bool ankanKeepsShape = false;
    bool tieSplitPoint = false;
    bool paoFourKan = false;
    bool kokushiTenhou13 = false;
    bool kokushiAnkan = false;
    bool paoCoversAll = false;

    Rules() { applyPreset(preset); }

    /** 铺一套预设值（不改思考时间）；`"custom"` 表示不铺。 */
    void applyPreset(const std::string &name) {
        preset = name.empty() ? "custom" : name;
        if (preset == "mleague") {
            // 赤 3、食断 + 后付、里宝/杠宝、无古役、常时一番缚
            aka = 3; kuitan = true; ura = true; kanDora = true; koyaku = false; minHan = 1;
            // 4 种役满不加倍；无中途流局（含三家和了 → 头跳）；无流局满贯
            doubleYakuman = false; renhou = "off"; headBump = true; sanchaAbort = false;
            fourRiichiAbort = false; fourKanAbort = false; fourWindAbort = false; kyuushuAbort = false;
            nagashiMangan = false;
            // 无击飞、无和了止、无西入；无一位必要点数
            tobi = false; agariyame = false; westExtension = false; requiredPoints = 0;
            // 食い替え禁止、包牌（含四杠子；暗杠计入大三元/大四喜的个数判定）
            kuikae = true; pao = true; paoFourKan = true; paoCoversAll = false;
            // 25000 配给 / 30000 返还、马点 10-30 + 头名赏、同点平分加点
            notenPenalty = 3000; startScore = 25000; returnScore = 30000;
            uma = {30, 10, -10, -30}; tieSplitPoint = true;
            // 计分：切上满贯、无累计役满、连风雀头 2 符
            kiriageMangan = true; kazoeYakuman = false; doubleWindPairFu = 2;
            // 立直：无 1000 点/残牌要求，但摸到海底后不可立直；暗杠要求面子构成不变
            riichiMinScore = 0; riichiMinTilesLeft = 0; riichiNoHaitei = true; ankanKeepsShape = true;
        } else if (preset == "tenhou") {
            aka = 3; kuitan = true; ura = true; kanDora = true; koyaku = false; minHan = 1;
            doubleYakuman = false; renhou = "off"; headBump = false; sanchaAbort = true;
            fourRiichiAbort = true; fourKanAbort = true; fourWindAbort = true; kyuushuAbort = true;
            nagashiMangan = true; tobi = true; agariyame = true; westExtension = false;
            requiredPoints = 30000;
            kuikae = true; pao = true; paoFourKan = false; paoCoversAll = true;
            notenPenalty = 3000; startScore = 25000; returnScore = 30000;
            uma = {20, 10, -10, -20}; tieSplitPoint = false;
            kiriageMangan = false; kazoeYakuman = true; doubleWindPairFu = 4;
            riichiMinScore = 1000; riichiMinTilesLeft = 4; riichiNoHaitei = false; ankanKeepsShape = false;
        } else if (preset == "majsoul") {
            aka = 3; kuitan = true; ura = true; kanDora = true; koyaku = false; minHan = 1;
            doubleYakuman = true; renhou = "off"; headBump = false; sanchaAbort = false;
            fourRiichiAbort = true; fourKanAbort = true; fourWindAbort = true; kyuushuAbort = true;
            nagashiMangan = true; tobi = true; agariyame = true; westExtension = false;
            requiredPoints = 30000;
            kokushiTenhou13 = true;
            kokushiAnkan = true;
            kuikae = true; pao = true; paoFourKan = false; paoCoversAll = false;
            notenPenalty = 3000; startScore = 25000; returnScore = 25000;
            uma = {15, 5, -5, -15}; tieSplitPoint = false;
            kiriageMangan = false; kazoeYakuman = true; doubleWindPairFu = 4;
            riichiMinScore = 1000; riichiMinTilesLeft = 4; riichiNoHaitei = false; ankanKeepsShape = false;
        }
        // "custom"：不铺，保留当前各项（与 Java 的 default 分支一致）
        // ⚠ 与 Java 一样，预设**只覆盖它自己列出的字段** —— 例如 `kokushiTenhou13`/`kokushiAnkan`
        //   只有 majsoul 分支会置 true，之后切别的预设**不会**清回 false（Java 也是这样）。
        //   训练路径每次都是新建 `Rules`，所以碰不到；但别拿同一个对象二次 `applyPreset`。
    }

    static Rules defaults() { return Rules(); }
};

}  // namespace trainer
