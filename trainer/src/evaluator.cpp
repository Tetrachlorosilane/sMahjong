#include "evaluator.hpp"

#include <algorithm>
#include <array>
#include <map>
#include <string>

#include "tiles.hpp"

namespace trainer {
namespace {

/** 高点法比较：先比基本点，再比番，最后比符（Java `Evaluator.better`）。 */
bool better(const HandScore &a, const HandScore &b) {
    if (a.base != b.base) {
        return a.base > b.base;
    }
    if (a.totalHan() != b.totalHan()) {
        return a.totalHan() > b.totalHan();
    }
    return a.fu > b.fu;
}

bool hasSanshokuDoujun(const std::vector<std::array<int, 2>> &runs) {
    for (int n = 0; n < 9; n++) {
        bool a = false, b = false, c = false;
        for (const auto &r : runs) {
            if (r[1] == n) {
                if (r[0] == 0) {
                    a = true;
                } else if (r[0] == 1) {
                    b = true;
                } else {
                    c = true;
                }
            }
        }
        if (a && b && c) {
            return true;
        }
    }
    return false;
}

bool hasSanshokuDoukou(const Form &f) {
    for (int n = 1; n <= 9; n++) {
        bool a = false, b = false, c = false;
        for (int i = 0; i < f.nSets; i++) {
            if (f.setType[static_cast<size_t>(i)] == kSetRun) {
                continue;
            }
            const int k = f.setStart[static_cast<size_t>(i)];
            if (isHonor(k) || numOf(k) != n) {
                continue;
            }
            const int s = suitOf(k);
            if (s == 0) {
                a = true;
            } else if (s == 1) {
                b = true;
            } else {
                c = true;
            }
        }
        if (a && b && c) {
            return true;
        }
    }
    return false;
}

bool hasIttsu(const std::vector<std::array<int, 2>> &runs) {
    for (int s = 0; s < 3; s++) {
        bool a = false, b = false, c = false;
        for (const auto &r : runs) {
            if (r[0] != s) {
                continue;
            }
            if (r[1] == 0) {
                a = true;
            } else if (r[1] == 3) {
                b = true;
            } else if (r[1] == 6) {
                c = true;
            }
        }
        if (a && b && c) {
            return true;
        }
    }
    return false;
}

bool hasSanrenkou(const Form &f) {
    for (int s = 0; s < 3; s++) {
        for (int n = 0; n <= 6; n++) {
            bool a = false, b = false, c = false;
            for (int i = 0; i < f.nSets; i++) {
                if (f.setType[static_cast<size_t>(i)] == kSetRun) {
                    continue;
                }
                const int k = f.setStart[static_cast<size_t>(i)];
                if (isHonor(k) || suitOf(k) != s) {
                    continue;
                }
                const int nn = numOf(k) - 1;
                if (nn == n) {
                    a = true;
                } else if (nn == n + 1) {
                    b = true;
                } else if (nn == n + 2) {
                    c = true;
                }
            }
            if (a && b && c) {
                return true;
            }
        }
    }
    return false;
}

bool hasGodenki(const Counts &allCounts) {
    bool m = false, p = false, s = false, wind = false, dragon = false;
    for (int k = 0; k < kKindCount; k++) {
        if (allCounts[static_cast<size_t>(k)] == 0) {
            continue;
        }
        if (isHonor(k)) {
            if (isWind(k)) {
                wind = true;
            } else {
                dragon = true;
            }
        } else {
            const int su = suitOf(k);
            if (su == 0) {
                m = true;
            } else if (su == 1) {
                p = true;
            } else {
                s = true;
            }
        }
    }
    return m && p && s && wind && dragon;
}

/** 九莲宝灯判定：0=不是；1=九莲宝灯；2=纯正九莲宝灯。 */
int checkChuuren(const Counts &allCounts, int winKind) {
    const int suit = suitOf(winKind);
    if (suit < 0) {
        return 0;
    }
    const int base = suit * 9;
    const std::array<int, 9> need = {3, 1, 1, 1, 1, 1, 1, 1, 3};
    for (int i = 0; i < 9; i++) {
        if (allCounts[static_cast<size_t>(base + i)] < need[static_cast<size_t>(i)]) {
            return 0;
        }
    }
    int total = 0;
    for (int i = 0; i < 9; i++) {
        total += allCounts[static_cast<size_t>(base + i)];
    }
    if (total != 14) {
        return 0;
    }
    Counts pre = allCounts;
    pre[static_cast<size_t>(winKind)]--;
    for (int i = 0; i < 9; i++) {
        if (pre[static_cast<size_t>(base + i)] != need[static_cast<size_t>(i)]) {
            return 1;
        }
    }
    return 2;
}

HandScore evaluateForm(const WinContext &ctx, const Counts &concealed, const std::vector<Meld> &melds,
                       const Form &f) {
    HandScore s;
    s.form = f;
    s.hasForm = true;
    const Rules &r = ctx.rules;

    // 全局牌型信息
    Counts allCounts = concealed;
    for (const Meld &m : melds) {
        for (int i = 0; i < m.tileCount; i++) {
            allCounts[static_cast<size_t>(kindOf(m.tiles[static_cast<size_t>(i)]))]++;
        }
    }
    bool allYaochu = true;
    bool allTerminal = true;
    bool allHonor = true;
    bool allGreen = true;
    bool allSimple = true;
    bool hasHonor = false;
    std::array<int, 3> suitCount{};
    for (int k = 0; k < kKindCount; k++) {
        if (allCounts[static_cast<size_t>(k)] == 0) {
            continue;
        }
        if (!isYaochu(k)) {
            allYaochu = false;
        }
        if (!isTerminal(k)) {
            allTerminal = false;
        }
        if (!isHonor(k)) {
            allHonor = false;
        }
        if (!isGreen(k)) {
            allGreen = false;
        }
        if (!isSimple(k)) {
            allSimple = false;
        }
        if (isHonor(k)) {
            hasHonor = true;
        } else {
            suitCount[static_cast<size_t>(suitOf(k))]++;
        }
    }
    const int suitsUsed = (suitCount[0] > 0 ? 1 : 0) + (suitCount[1] > 0 ? 1 : 0)
                        + (suitCount[2] > 0 ? 1 : 0);
    const bool menzen = ctx.menzen;

    // ---------------- 役满判定
    std::vector<Yaku> yk;
    if (ctx.tenhou) {
        yk.push_back(Yaku::yk("天和", 1));
    }
    if (ctx.chiihou) {
        yk.push_back(Yaku::yk("地和", 1));
    }
    if (ctx.renhou && r.renhou == "yakuman") {
        yk.push_back(Yaku::yk("人和", 1));
    }
    if (f.type == kTypeKokushi) {
        const bool thirteen = f.kokushi13 || (r.kokushiTenhou13 && ctx.tenhou);
        if (thirteen) {
            yk.push_back(Yaku::yk("国士无双十三面", r.doubleYakuman ? 2 : 1));
        } else {
            yk.push_back(Yaku::yk("国士无双", 1));
        }
    } else {
        int windTriplets = 0;
        int dragonTriplets = 0;
        int quads = 0;
        int concealedTriplets = 0;
        for (int i = 0; i < f.nSets; i++) {
            if (f.setType[static_cast<size_t>(i)] == kSetRun) {
                continue;
            }
            const int k = f.setStart[static_cast<size_t>(i)];
            const bool concealedSet = f.setConcealed[static_cast<size_t>(i)]
                    && !(!ctx.tsumo && f.winSet == i);
            if (concealedSet) {
                concealedTriplets++;
            }
            if (f.setType[static_cast<size_t>(i)] == kSetQuad) {
                quads++;
            }
            if (isWind(k)) {
                windTriplets++;
            }
            if (isDragon(k)) {
                dragonTriplets++;
            }
        }
        if (dragonTriplets == 3) {
            yk.push_back(Yaku::yk("大三元", 1));
        }
        if (windTriplets == 4) {
            yk.push_back(Yaku::yk("大四喜", r.doubleYakuman ? 2 : 1));
        } else if (windTriplets == 3 && f.pair >= 0 && isWind(f.pair)) {
            yk.push_back(Yaku::yk("小四喜", 1));
        }
        const bool daichishin = r.koyaku && f.type == kTypeChiitoitsu && allHonor;
        if (allHonor && !daichishin) {
            yk.push_back(Yaku::yk("字一色", 1));
        }
        if (allGreen) {
            yk.push_back(Yaku::yk("绿一色", 1));
        }
        if (allTerminal) {
            yk.push_back(Yaku::yk("清老头", 1));
        }
        if (quads == 4) {
            yk.push_back(Yaku::yk("四杠子", 1));
        }
        if (concealedTriplets == 4 && menzen) {
            if (f.winSet < 0) {
                yk.push_back(Yaku::yk("四暗刻单骑", r.doubleYakuman ? 2 : 1));
            } else {
                yk.push_back(Yaku::yk("四暗刻", 1));
            }
        }
        if (menzen && suitsUsed == 1 && !hasHonor) {
            const int ck = checkChuuren(allCounts, ctx.winKind);
            if (ck == 2) {
                yk.push_back(Yaku::yk("纯正九莲宝灯", r.doubleYakuman ? 2 : 1));
            } else if (ck >= 1) {
                yk.push_back(Yaku::yk("九莲宝灯", 1));
            }
        }
        // 古役役满
        if (r.koyaku) {
            if (ctx.doubleRiichi && (ctx.haitei || ctx.houtei)) {
                yk.push_back(Yaku::yk("石上三年", 1));
            }
            if (f.type == kTypeChiitoitsu) {
                if (allHonor) {
                    yk.push_back(Yaku::yk("大七星", r.doubleYakuman ? 2 : 1));
                } else {
                    int su = -1;
                    bool ok = true;
                    for (int k = 0; k < 27; k++) {
                        if (allCounts[static_cast<size_t>(k)] == 0) {
                            continue;
                        }
                        const int n = numOf(k);
                        if (n < 2 || n > 8) {
                            ok = false;
                            break;
                        }
                        if (su == -1) {
                            su = suitOf(k);
                        } else if (su != suitOf(k)) {
                            ok = false;
                            break;
                        }
                    }
                    if (ok && su >= 0) {
                        yk.push_back(Yaku::yk(su == 0 ? "大数邻" : (su == 1 ? "大车轮" : "大竹林"), 1));
                    }
                }
            }
        }
    }

    if (!yk.empty()) {
        for (const Yaku &y : yk) {
            s.yaku.push_back(y);
            s.yakuman += y.yakuman;
        }
        s.hanYaku = 13 * s.yakuman;
        s.han = s.hanYaku;
        s.fu = 0;
        s.base = 8000 * s.yakuman;
        s.limit = s.yakuman == 1 ? "役满" : (s.yakuman == 2 ? "两倍役满"
                : (s.yakuman == 3 ? "三倍役满" : std::to_string(s.yakuman) + "倍役满"));
        s.valid = true;
        return s;
    }

    // ---------------- 普通役
    std::vector<Yaku> ys;
    const int kuisagari = menzen ? 0 : 1;

    if (ctx.doubleRiichi) {
        ys.push_back(Yaku::normal("两立直", 2));
    } else if (ctx.riichi) {
        ys.push_back(Yaku::normal("立直", 1));
    }
    if (ctx.ippatsu && (ctx.riichi || ctx.doubleRiichi)) {
        ys.push_back(Yaku::normal("一发", 1));
    }
    if (menzen && ctx.tsumo) {
        ys.push_back(Yaku::normal("门前清自摸和", 1));
    }
    if (ctx.chankan) {
        ys.push_back(Yaku::normal("抢杠", 1));
    }
    if (ctx.rinshan) {
        ys.push_back(Yaku::normal("岭上开花", 1));
    }
    const bool iipin = r.koyaku && ctx.haitei && ctx.tsumo && ctx.winKind == parseKind("1p");
    const bool chuupin = r.koyaku && ctx.houtei && !ctx.tsumo && ctx.winKind == parseKind("9p");
    if (ctx.haitei && ctx.tsumo && !iipin) {
        ys.push_back(Yaku::normal("海底摸月", 1));
    }
    if (ctx.houtei && !ctx.tsumo && !chuupin) {
        ys.push_back(Yaku::normal("河底捞鱼", 1));
    }
    if (r.koyaku) {
        if (ctx.tsubame) {
            ys.push_back(Yaku::normal("燕返", 1));
        }
        if (ctx.kanburi) {
            ys.push_back(Yaku::normal("杠振", 1));
        }
        if (ctx.renhou && r.renhou == "mangan") {
            ys.push_back(Yaku::normal("人和", 5));
        }
    }

    int runs = 0;
    int quads = 0;
    int concealedTriplets = 0;
    bool pairYakuhai = false;
    std::vector<std::array<int, 2>> runList;
    for (int i = 0; i < f.nSets; i++) {
        const int t = f.setType[static_cast<size_t>(i)];
        const int st = f.setStart[static_cast<size_t>(i)];
        if (t == kSetRun) {
            runs++;
            runList.push_back({suitOf(st), numOf(st) - 1});
        } else {
            if (t == kSetQuad) {
                quads++;
            }
            const bool concealedSet = f.setConcealed[static_cast<size_t>(i)]
                    && !(!ctx.tsumo && f.winSet == i);
            if (concealedSet) {
                concealedTriplets++;
            }
        }
    }
    // 役牌
    const int roundWind = ctx.roundWind;
    const int seatWind = ctx.seatWind();
    for (int i = 0; i < f.nSets; i++) {
        if (f.setType[static_cast<size_t>(i)] == kSetRun) {
            continue;
        }
        const int k = f.setStart[static_cast<size_t>(i)];
        if (isDragon(k)) {
            ys.push_back(Yaku::normal(std::string("役牌 ") + cnName(k), 1));
        }
        if (k == roundWind) {
            ys.push_back(Yaku::normal(std::string("场风 ") + cnName(k), 1));
        }
        if (k == seatWind) {
            ys.push_back(Yaku::normal(std::string("自风 ") + cnName(k), 1));
        }
    }
    if (f.pair >= 0) {
        pairYakuhai = isDragon(f.pair) || f.pair == roundWind || f.pair == seatWind;
    }
    // 断幺九
    if (allSimple && (menzen || r.kuitan)) {
        ys.push_back(Yaku::normal("断幺九", 1));
    }
    // 平和
    if (menzen && f.type == kTypeStandard && runs == 4 && !pairYakuhai
            && f.waitType == kWaitRyanmen) {
        ys.push_back(Yaku::normal("平和", 1));
    }
    // 一杯口 / 二杯口 / 一色三顺
    if (f.type == kTypeStandard) {
        std::map<int, int> runGroups;
        for (const auto &rr : runList) {
            runGroups[rr[0] * 10 + rr[1]]++;
        }
        int dupGroups = 0;
        bool triple = false;
        for (const auto &kv : runGroups) {
            if (kv.second >= 2) {
                dupGroups++;
            }
            if (kv.second >= 3) {
                triple = true;
            }
        }
        if (r.koyaku && triple) {
            ys.push_back(Yaku::normal("一色三顺", 3 - kuisagari));
        } else if (menzen && dupGroups >= 2) {
            ys.push_back(Yaku::normal("二杯口", 3));
        } else if (menzen && dupGroups == 1) {
            ys.push_back(Yaku::normal("一杯口", 1));
        }
    }
    if (hasSanshokuDoujun(runList)) {
        ys.push_back(Yaku::normal("三色同顺", 2 - kuisagari));
    }
    if (hasSanshokuDoukou(f)) {
        ys.push_back(Yaku::normal("三色同刻", 2));
    }
    if (hasIttsu(runList)) {
        ys.push_back(Yaku::normal("一气通贯", 2 - kuisagari));
    }
    if (f.type == kTypeStandard && runs == 0) {
        ys.push_back(Yaku::normal("对对和", 2));
    }
    if (concealedTriplets == 3) {
        ys.push_back(Yaku::normal("三暗刻", 2));
    }
    if (quads == 3) {
        ys.push_back(Yaku::normal("三杠子", 2));
    }
    if (f.type == kTypeChiitoitsu) {
        ys.push_back(Yaku::normal("七对子", 2));
    }
    // 小三元
    {
        int dt = 0;
        for (int i = 0; i < f.nSets; i++) {
            if (f.setType[static_cast<size_t>(i)] != kSetRun && isDragon(f.setStart[static_cast<size_t>(i)])) {
                dt++;
            }
        }
        if (dt == 2 && f.pair >= 0 && isDragon(f.pair)) {
            ys.push_back(Yaku::normal("小三元", 2));
        }
    }
    // 全带类
    if (f.type == kTypeStandard) {
        bool allSetsYaochu = true;
        for (int i = 0; i < f.nSets; i++) {
            if (f.setType[static_cast<size_t>(i)] == kSetRun) {
                const int st = f.setStart[static_cast<size_t>(i)];
                if (!(st % 9 == 0 || st % 9 == 6)) {
                    allSetsYaochu = false;
                    break;
                }
            } else if (!isYaochu(f.setStart[static_cast<size_t>(i)])) {
                allSetsYaochu = false;
                break;
            }
        }
        if (allSetsYaochu && f.pair >= 0 && !isYaochu(f.pair)) {
            allSetsYaochu = false;
        }
        if (allSetsYaochu && runs >= 1) {
            bool hasHonorTile = false;
            bool hasTerminalTile = false;
            for (int k = 0; k < kKindCount; k++) {
                if (allCounts[static_cast<size_t>(k)] == 0) {
                    continue;
                }
                if (isHonor(k)) {
                    hasHonorTile = true;
                }
                if (isTerminal(k)) {
                    hasTerminalTile = true;
                }
            }
            if (!hasHonorTile && hasTerminalTile) {
                ys.push_back(Yaku::normal("纯全带幺九", 3 - kuisagari));
            } else {
                ys.push_back(Yaku::normal("混全带幺九", 2 - kuisagari));
            }
        }
    }
    // 混老头
    if (allYaochu) {
        ys.push_back(Yaku::normal("混老头", 2));
    }
    // 染手
    if (suitsUsed == 1) {
        if (hasHonor) {
            ys.push_back(Yaku::normal("混一色", 3 - kuisagari));
        } else {
            ys.push_back(Yaku::normal("清一色", 6 - kuisagari));
        }
    }
    // 古役
    if (r.koyaku) {
        if (hasGodenki(allCounts)) {
            ys.push_back(Yaku::normal("五门齐", 2));
        }
        if (hasSanrenkou(f)) {
            ys.push_back(Yaku::normal("三连刻", 2));
        }
        bool allOpenRuns = true;
        for (int i = 0; i < f.nSets; i++) {
            if (!f.setFromMeld[static_cast<size_t>(i)] || f.setConcealed[static_cast<size_t>(i)]) {
                allOpenRuns = false;
                break;
            }
        }
        if (allOpenRuns && f.nSets == 4 && f.winSet < 0) {
            ys.push_back(Yaku::normal("十二落抬", 1));
        }
        if (iipin) {
            ys.push_back(Yaku::normal("一筒摸月", 5));
        }
        if (chuupin) {
            ys.push_back(Yaku::normal("九筒捞鱼", 5));
        }
    }
    // 流局满贯
    if (ctx.nagashi) {
        ys.push_back(Yaku::normal("流局满贯", 5));
    }

    // ---------------- 宝牌
    int dora = 0;
    int ura = 0;
    int aka = 0;
    for (int ind : ctx.doraIndicators) {
        dora += allCounts[static_cast<size_t>(doraFrom(ind))];
    }
    if ((ctx.riichi || ctx.doubleRiichi) && r.ura) {
        for (int ind : ctx.uraIndicators) {
            ura += allCounts[static_cast<size_t>(doraFrom(ind))];
        }
    }
    for (int id : ctx.allTileIds) {
        if (isRedId(id)) {
            aka++;
        }
    }

    int hanYaku = 0;
    for (const Yaku &y : ys) {
        hanYaku += y.han;
    }
    s.hanYaku = hanYaku;
    s.han = hanYaku + dora + ura + aka;
    s.dora = dora;
    s.ura = ura;
    s.aka = aka;
    for (const Yaku &y : ys) {
        s.yaku.push_back(y);
    }
    if (dora > 0) {
        s.yaku.push_back(Yaku::normal("宝牌", dora));
    }
    if (ura > 0) {
        s.yaku.push_back(Yaku::normal("里宝牌", ura));
    }
    if (aka > 0) {
        s.yaku.push_back(Yaku::normal("赤宝牌", aka));
    }

    // 累计役满：M.League 以三倍满（6000）为普通役上限
    if (s.han >= 13) {
        s.base = basePoints(s.han, s.fu, r);
        s.limit = r.kazoeYakuman ? "累计役满" : "三倍满";
        s.fu = 0;
        s.valid = hanYaku >= r.minHan;
        if (!s.valid) {
            s.reason = "番缚不足";
        }
        return s;
    }

    // ---------------- 符
    s.fu = calcFu(ctx, f, menzen, r);

    // ---------------- 基本点
    if (hanYaku < r.minHan) {
        s.valid = false;
        s.reason = hanYaku == 0 ? "无役" : "番缚不足";
        s.base = 0;
        return s;
    }
    s.valid = true;
    if (s.han >= 5) {
        if (s.han >= 11) {
            s.limit = "三倍满";
        } else if (s.han >= 8) {
            s.limit = "倍满";
        } else if (s.han >= 6) {
            s.limit = "跳满";
        } else {
            s.limit = "满贯";
        }
        s.base = basePoints(s.han, s.fu, r);
    } else {
        s.base = basePoints(s.han, s.fu, r);
        if (s.base >= 2000) {
            s.limit = "满贯";
        }
    }
    return s;
}

}  // namespace

HandScore evaluate(const WinContext &ctx, const Counts &concealed, const std::vector<Meld> &melds,
                   int winKind) {
    HandScore best;
    bool has = false;
    for (const Form &f : decompose(concealed, melds, winKind)) {
        HandScore s = evaluateForm(ctx, concealed, melds, f);
        if (!has || better(s, best)) {
            best = s;
            has = true;
        }
    }
    if (!has) {
        HandScore s;
        s.reason = "不是和了形";
        return s;
    }
    return best;
}

int basePoints(int han, int fu, const Rules &rules) {
    if (han >= 13) {
        return rules.kazoeYakuman ? 8000 : 6000;
    }
    if (han >= 11) {
        return 6000;
    }
    if (han >= 8) {
        return 4000;
    }
    if (han >= 6) {
        return 3000;
    }
    if (han >= 5) {
        return 2000;
    }
    const int b = fu * (1 << (2 + han));
    if (b > 2000 || (rules.kiriageMangan && b >= 1920)) {
        return 2000;
    }
    return b;
}

int calcFu(const WinContext &ctx, const Form &f, bool menzen, const Rules &rules) {
    if (f.type == kTypeChiitoitsu) {
        return 25;
    }
    if (f.type == kTypeKokushi) {
        return 0;
    }
    const bool pinfu = isPinfu(ctx, f, menzen);
    if (pinfu && ctx.tsumo) {
        return 20;
    }
    int fu = 20;
    if (menzen && !ctx.tsumo) {
        fu += 10;
    }
    if (ctx.tsumo) {
        fu += 2;
    }
    for (int i = 0; i < f.nSets; i++) {
        const int t = f.setType[static_cast<size_t>(i)];
        if (t == kSetRun) {
            continue;
        }
        const int k = f.setStart[static_cast<size_t>(i)];
        const bool yaochu = isYaochu(k);
        const bool con = f.setConcealed[static_cast<size_t>(i)] && !(!ctx.tsumo && f.winSet == i);
        if (t == kSetQuad) {
            fu += con ? (yaochu ? 32 : 16) : (yaochu ? 16 : 8);
        } else {
            fu += con ? (yaochu ? 8 : 4) : (yaochu ? 4 : 2);
        }
    }
    if (f.pair >= 0) {
        const int p = f.pair;
        if (isDragon(p)) {
            fu += 2;
        }
        const bool isRound = (p == ctx.roundWind);
        const bool isSeat = (p == ctx.seatWind());
        if (isRound && isSeat) {
            fu += rules.doubleWindPairFu;
        } else if (isRound || isSeat) {
            fu += 2;
        }
    }
    switch (f.waitType) {
        case kWaitKanchan:
        case kWaitPenchan:
        case kWaitTanki:
            fu += 2;
            break;
        default:
            break;
    }
    if (fu < 30) {
        return 30;
    }
    return ((fu + 9) / 10) * 10;
}

bool isPinfu(const WinContext &ctx, const Form &f, bool menzen) {
    if (!menzen || f.type != kTypeStandard || f.waitType != kWaitRyanmen) {
        return false;
    }
    int runs = 0;
    for (int i = 0; i < f.nSets; i++) {
        if (f.setType[static_cast<size_t>(i)] != kSetRun) {
            return false;
        }
        runs++;
    }
    if (runs != 4 || f.pair < 0) {
        return false;
    }
    const int p = f.pair;
    if (isDragon(p) || p == ctx.roundWind || p == ctx.seatWind()) {
        return false;
    }
    return true;
}

}  // namespace trainer
