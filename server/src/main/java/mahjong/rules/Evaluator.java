package mahjong.rules;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import mahjong.core.Meld;
import mahjong.core.Rules;
import mahjong.core.Tiles;

/**
 * 役种判定 + 符数计算 + 高点法取最优（番/符/基本点）。
 *
 * <p>流程：对 {@link Agari#decompose} 给出的每一种解释分别算役与符，取打点最高者。
 */
public final class Evaluator {

    private Evaluator() {
    }

    /** 单个役。 */
    public static final class Yaku {
        public final String name;
        public final int han;
        /** 役满倍数（0 表示普通役）。 */
        public final int yakuman;

        public Yaku(String name, int han, int yakuman) {
            this.name = name;
            this.han = han;
            this.yakuman = yakuman;
        }

        public static Yaku normal(String name, int han) {
            return new Yaku(name, han, 0);
        }

        public static Yaku yakuman(String name, int mult) {
            return new Yaku(name, 0, mult);
        }

        /**
         * 结算报文里上报的番数。
         *
         * <p>役满役**本身没有番数**（内部 {@code han} 恒为 0），但报文里不能写 0 ——
         * 客户端会把 han 原样显示成「0 番」。按「役满 = 13 番等价」折算成
         * {@code 13 × 倍数}，与 {@link HandScore#totalHan()} 同一套约定
         * （这样逐役之和 == 合计 han，见 PROTOCOL §3.7）。
         *
         * <p>⚠ 结算界面**不显示**这个数：役满的量纲是「几倍役满」，由 `yakuman` 字段决定
         * （见 ResultDialog::agariHtml）。它只是给不认识 `yakuman` 的老客户端一个合理退路。
         */
        public int equivalentHan() {
            return yakuman > 0 ? 13 * yakuman : han;
        }
    }

    /** 评价结果。 */
    public static final class HandScore {
        public final List<Yaku> yaku = new ArrayList<>();
        /** 役的番数合计（不含宝牌）。 */
        public int hanYaku;
        /** 含宝牌的番数合计。 */
        public int han;
        public int fu;
        public int yakuman;
        public int dora;
        public int ura;
        public int aka;
        public int base;
        public String limit = "";
        /** 有役且满足番缚。 */
        public boolean valid;
        public String reason = "";
        public Agari.Form form;

        public boolean isYakuman() {
            return yakuman > 0;
        }

        public int totalHan() {
            return isYakuman() ? 13 * yakuman : han;
        }
    }

    /** 对外主入口。 */
    public static HandScore evaluate(WinContext ctx, int[] concealed, List<Meld> melds, int winKind) {
        HandScore best = null;
        for (Agari.Form f : Agari.decompose(concealed, melds, winKind)) {
            HandScore s = evaluateForm(ctx, concealed, melds, f);
            if (best == null || better(s, best)) {
                best = s;
            }
        }
        if (best == null) {
            HandScore s = new HandScore();
            s.reason = "不是和了形";
            return s;
        }
        return best;
    }

    /** 高点法比较：先比基本点，再比番，最后比符。 */
    private static boolean better(HandScore a, HandScore b) {
        if (a.base != b.base) {
            return a.base > b.base;
        }
        if (a.totalHan() != b.totalHan()) {
            return a.totalHan() > b.totalHan();
        }
        return a.fu > b.fu;
    }

    // ------------------------------------------------------------------ 单形评价

    private static HandScore evaluateForm(WinContext ctx, int[] concealed, List<Meld> melds, Agari.Form f) {
        HandScore s = new HandScore();
        s.form = f;
        Rules r = ctx.rules;

        // 全局牌型信息
        int[] allCounts = concealed.clone();
        for (Meld m : melds) {
            for (int t : m.tiles) {
                allCounts[Tiles.kind(t)]++;
            }
        }
        boolean allYaochu = true;
        boolean allTerminal = true;
        boolean allHonor = true;
        boolean allGreen = true;
        boolean allSimple = true;
        boolean hasHonor = false;
        int[] suitCount = new int[3];
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (allCounts[k] == 0) {
                continue;
            }
            if (!Tiles.isYaochu(k)) {
                allYaochu = false;
            }
            if (!Tiles.isTerminal(k)) {
                allTerminal = false;
            }
            if (!Tiles.isHonor(k)) {
                allHonor = false;
            }
            if (!Tiles.isGreen(k)) {
                allGreen = false;
            }
            if (!Tiles.isSimple(k)) {
                allSimple = false;
            }
            if (Tiles.isHonor(k)) {
                hasHonor = true;
            } else {
                suitCount[Tiles.suit(k)]++;
            }
        }
        int suitsUsed = (suitCount[0] > 0 ? 1 : 0) + (suitCount[1] > 0 ? 1 : 0) + (suitCount[2] > 0 ? 1 : 0);
        boolean menzen = ctx.menzen;

        // ---------------- 役满判定
        List<Yaku> yk = new ArrayList<>();
        // 天和 / 地和 / 人和 是**独立的役满**，与手牌形无关 —— 所以「国士 + 天和」是
        // **2 倍役满**（文档 §役满 L1239「不同的役满仍可以复合」）。
        // ⚠ 旧实现把它们写在 `else`（非国士）分支里，于是天和国士**白丢一个役满**（AUDIT S-59）。
        if (ctx.tenhou) {
            yk.add(Yaku.yakuman("天和", 1));
        }
        if (ctx.chiihou) {
            yk.add(Yaku.yakuman("地和", 1));
        }
        if (ctx.renhou && "yakuman".equals(r.renhou)) {
            yk.add(Yaku.yakuman("人和", 1));
        }
        if (f.type == Agari.TYPE_KOKUSHI) {
            // 「13 面」是一个**独立的役**（高目取代国士无双），加不加倍是取值问题：
            // 《雀魂》计 2 倍，《天凤》与 M.League 计 1 倍（docs/日本麻将.md §两倍役满）。
            // ⚠ 不能在不加倍时把它改名成「国士无双」—— 那是把"值"的取舍写成了"役种"的取舍，
            // 玩家和牌界面会看不到自己做出的是十三面。
            //
            // ⚠ 还有一条**规则口径**：`docs/日本麻将.md` L1149「《雀魂》中，**天和**时如果成立
            //   国士无双，视作成立国士无双十三面」→ "是不是十三面"不能只看牌型
            //   （`f.kokushi13` = 和牌前那 13 张含全部幺九种），还要看 `rules.kokushiTenhou13` + 天和。
            //   只有天和算（地和不算），本来就是十三面时也不会重复计。
            final boolean thirteen = f.kokushi13 || (r.kokushiTenhou13 && ctx.tenhou);
            if (thirteen) {
                yk.add(Yaku.yakuman("国士无双十三面", r.doubleYakuman ? 2 : 1));
            } else {
                yk.add(Yaku.yakuman("国士无双", 1));
            }
        } else {
            int windTriplets = 0;
            int dragonTriplets = 0;
            int quads = 0;
            int concealedTriplets = 0;
            int runs = 0;
            for (int i = 0; i < f.nSets; i++) {
                if (f.setType[i] == Agari.SET_RUN) {
                    runs++;
                } else {
                    int k = f.setStart[i];
                    boolean concealedSet = f.setConcealed[i] && !(!ctx.tsumo && f.winSet == i);
                    if (f.setConcealed[i] && !(!ctx.tsumo && f.winSet == i)) {
                        concealedTriplets++;
                    }
                    if (f.setType[i] == Agari.SET_QUAD) {
                        quads++;
                    }
                    if (Tiles.isWind(k)) {
                        windTriplets++;
                    }
                    if (Tiles.isDragon(k)) {
                        dragonTriplets++;
                    }
                    if (concealedSet) {
                        // 已计入
                    }
                }
            }
            if (dragonTriplets == 3) {
                yk.add(Yaku.yakuman("大三元", 1));
            }
            if (windTriplets == 4) {
                yk.add(Yaku.yakuman("大四喜", r.doubleYakuman ? 2 : 1));
            } else if (windTriplets == 3 && f.pair >= 0 && Tiles.isWind(f.pair)) {
                yk.add(Yaku.yakuman("小四喜", 1));
            }
            // 大七星（古役）= **只有字牌的七对子**（文档 §两倍役满）：「在不承认古役的规则下
            // 只成立字一色，为役满」→ 成立时**取代**字一色，不再叠加
            //（旧实现两者都加 → 3 倍役满，与原文的"两倍役满"矛盾，AUDIT S-61）。
            final boolean daichishin = r.koyaku && f.type == Agari.TYPE_CHIITOITSU && allHonor;
            if (allHonor && !daichishin) {
                yk.add(Yaku.yakuman("字一色", 1));
            }
            if (allGreen) {
                yk.add(Yaku.yakuman("绿一色", 1));
            }
            if (allTerminal) {
                yk.add(Yaku.yakuman("清老头", 1));
            }
            if (quads == 4) {
                yk.add(Yaku.yakuman("四杠子", 1));
            }
            if (concealedTriplets == 4 && menzen) {
                // 同国士十三面：单骑是独立役种（高目取代四暗刻），加倍与否只影响取值
                if (f.winSet < 0) {
                    yk.add(Yaku.yakuman("四暗刻单骑", r.doubleYakuman ? 2 : 1));
                } else {
                    yk.add(Yaku.yakuman("四暗刻", 1));
                }
            }
            if (menzen && suitsUsed == 1 && !hasHonor) {
                int ck = checkChuuren(allCounts, ctx.winKind);
                if (ck == 2) {
                    yk.add(Yaku.yakuman("纯正九莲宝灯", r.doubleYakuman ? 2 : 1));
                } else if (ck >= 1) {
                    yk.add(Yaku.yakuman("九莲宝灯", 1));
                }
            }
            // 古役役满
            if (r.koyaku) {
                if (ctx.doubleRiichi && (ctx.haitei || ctx.houtei)) {
                    yk.add(Yaku.yakuman("石上三年", 1));
                }
                if (f.type == Agari.TYPE_CHIITOITSU) {
                    if (allHonor) {
                        yk.add(Yaku.yakuman("大七星", r.doubleYakuman ? 2 : 1));
                    } else {
                        int su = -1;
                        boolean ok = true;
                        for (int k = 0; k < 27; k++) {
                            if (allCounts[k] == 0) {
                                continue;
                            }
                            int n = Tiles.num(k);
                            if (n < 2 || n > 8) {
                                ok = false;
                                break;
                            }
                            if (su == -1) {
                                su = Tiles.suit(k);
                            } else if (su != Tiles.suit(k)) {
                                ok = false;
                                break;
                            }
                        }
                        if (ok && su >= 0) {
                            yk.add(Yaku.yakuman(su == 0 ? "大数邻" : su == 1 ? "大车轮" : "大竹林", 1));
                        }
                    }
                }
            }
        }

        if (!yk.isEmpty()) {
            s.yaku.addAll(yk);
            for (Yaku y : yk) {
                s.yakuman += y.yakuman;
            }
            s.hanYaku = 13 * s.yakuman;
            s.han = s.hanYaku;
            s.fu = 0;
            s.base = 8000 * s.yakuman;
            s.limit = s.yakuman == 1 ? "役满" : (s.yakuman == 2 ? "两倍役满" : (s.yakuman == 3 ? "三倍役满"
                    : s.yakuman + "倍役满"));
            s.valid = true;
            return s;
        }

        // ---------------- 普通役
        List<Yaku> ys = new ArrayList<>();
        int kuisagari = menzen ? 0 : 1;

        if (ctx.doubleRiichi) {
            ys.add(Yaku.normal("两立直", 2));
        } else if (ctx.riichi) {
            ys.add(Yaku.normal("立直", 1));
        }
        if (ctx.ippatsu && (ctx.riichi || ctx.doubleRiichi)) {
            ys.add(Yaku.normal("一发", 1));
        }
        if (menzen && ctx.tsumo) {
            ys.add(Yaku.normal("门前清自摸和", 1));
        }
        if (ctx.chankan) {
            ys.add(Yaku.normal("抢杠", 1));
        }
        if (ctx.rinshan) {
            ys.add(Yaku.normal("岭上开花", 1));
        }
        // 一筒摸月 / 九筒捞鱼 是**海底摸月 / 河底捞鱼 的同一次和牌加上特定牌**（文档 §古役：
        // 「指海底摸月时海底牌为 1p」「指河底捞鱼时河底牌为 9p」），原文明确
        // 「一筒摸月、九筒捞鱼**实际上计 5 番**，故为满贯」—— 所以它们**取代**那 1 番，
        // 不是叠加（叠加会变成 6 番跳满，与"5 番满贯"矛盾，AUDIT S-61）。
        final boolean iipin = r.koyaku && ctx.haitei && ctx.tsumo
                && ctx.winKind == Tiles.parseKind("1p");
        final boolean chuupin = r.koyaku && ctx.houtei && !ctx.tsumo
                && ctx.winKind == Tiles.parseKind("9p");
        if (ctx.haitei && ctx.tsumo && !iipin) {
            ys.add(Yaku.normal("海底摸月", 1));
        }
        if (ctx.houtei && !ctx.tsumo && !chuupin) {
            ys.add(Yaku.normal("河底捞鱼", 1));
        }
        if (r.koyaku) {
            if (ctx.tsubame) {
                ys.add(Yaku.normal("燕返", 1));
            }
            if (ctx.kanburi) {
                ys.add(Yaku.normal("杠振", 1));
            }
            if (ctx.renhou && "mangan".equals(r.renhou)) {
                // 人和（满贯级）：用 5 番近似
                ys.add(Yaku.normal("人和", 5));
            }
        }

        int runs = 0;
        int triplets = 0;
        int quads = 0;
        int concealedTriplets = 0;
        boolean pairYakuhai = false;
        List<int[]> runList = new ArrayList<>();
        for (int i = 0; i < f.nSets; i++) {
            int t = f.setType[i];
            int st = f.setStart[i];
            if (t == Agari.SET_RUN) {
                runs++;
                runList.add(new int[]{Tiles.suit(st), Tiles.num(st) - 1});
            } else {
                triplets++;
                if (t == Agari.SET_QUAD) {
                    quads++;
                }
                boolean concealedSet = f.setConcealed[i] && !(!ctx.tsumo && f.winSet == i);
                if (concealedSet) {
                    concealedTriplets++;
                }
            }
        }
        // 役牌
        int roundWind = ctx.roundWind;
        int seatWind = ctx.seatWind();
        for (int i = 0; i < f.nSets; i++) {
            if (f.setType[i] == Agari.SET_RUN) {
                continue;
            }
            int k = f.setStart[i];
            if (Tiles.isDragon(k)) {
                ys.add(Yaku.normal("役牌 " + Tiles.cnName(k), 1));
            }
            if (k == roundWind) {
                ys.add(Yaku.normal("场风 " + Tiles.cnName(k), 1));
            }
            if (k == seatWind) {
                ys.add(Yaku.normal("自风 " + Tiles.cnName(k), 1));
            }
        }
        if (f.pair >= 0) {
            pairYakuhai = Tiles.isDragon(f.pair) || f.pair == roundWind || f.pair == seatWind;
        }

        // 断幺九
        if (allSimple && (menzen || r.kuitan)) {
            ys.add(Yaku.normal("断幺九", 1));
        }
        // 平和
        if (menzen && f.type == Agari.TYPE_STANDARD && runs == 4 && !pairYakuhai
                && f.waitType == Agari.WAIT_RYANMEN) {
            ys.add(Yaku.normal("平和", 1));
        }
        // 一杯口 / 二杯口（**门前役**）与一色三顺（**副露也成立**：文档 §古役「副露减一番」，
        // 即 3−kuisagari = 2 番；旧实现整块包在 `menzen` 里 → 副露型永远拿不到，AUDIT S-60）
        if (f.type == Agari.TYPE_STANDARD) {
            Map<Integer, Integer> runGroups = new HashMap<>();
            for (int[] rr : runList) {
                int key = rr[0] * 10 + rr[1];
                runGroups.merge(key, 1, Integer::sum);
            }
            int dupGroups = 0;
            boolean triple = false;
            for (int v : runGroups.values()) {
                if (v >= 2) {
                    dupGroups++;
                }
                if (v >= 3) {
                    triple = true;
                }
            }
            if (r.koyaku && triple) {
                // 一色三顺是一杯口的上位替代（成立时不计一杯口 / 二杯口）
                ys.add(Yaku.normal("一色三顺", 3 - kuisagari));
            } else if (menzen && dupGroups >= 2) {
                ys.add(Yaku.normal("二杯口", 3));
            } else if (menzen && dupGroups == 1) {
                ys.add(Yaku.normal("一杯口", 1));
            }
        }
        // 三色同顺
        if (hasSanshokuDoujun(runList)) {
            ys.add(Yaku.normal("三色同顺", 2 - kuisagari));
        }
        // 三色同刻
        if (hasSanshokuDoukou(f)) {
            ys.add(Yaku.normal("三色同刻", 2));
        }
        // 一气通贯
        if (hasIttsu(runList)) {
            ys.add(Yaku.normal("一气通贯", 2 - kuisagari));
        }
        // 对对和
        if (f.type == Agari.TYPE_STANDARD && runs == 0) {
            ys.add(Yaku.normal("对对和", 2));
        }
        // 三暗刻
        if (concealedTriplets == 3) {
            ys.add(Yaku.normal("三暗刻", 2));
        }
        // 三杠子
        if (quads == 3) {
            ys.add(Yaku.normal("三杠子", 2));
        }
        // 七对子
        if (f.type == Agari.TYPE_CHIITOITSU) {
            ys.add(Yaku.normal("七对子", 2));
        }
        // 小三元
        {
            int dt = 0;
            for (int i = 0; i < f.nSets; i++) {
                if (f.setType[i] != Agari.SET_RUN && Tiles.isDragon(f.setStart[i])) {
                    dt++;
                }
            }
            if (dt == 2 && f.pair >= 0 && Tiles.isDragon(f.pair)) {
                ys.add(Yaku.normal("小三元", 2));
            }
        }
        // 全带类
        if (f.type == Agari.TYPE_STANDARD) {
            boolean allSetsYaochu = true;
            for (int i = 0; i < f.nSets; i++) {
                if (f.setType[i] == Agari.SET_RUN) {
                    int st = f.setStart[i];
                    if (!(st % 9 == 0 || st % 9 == 6)) {
                        allSetsYaochu = false;
                        break;
                    }
                } else if (!Tiles.isYaochu(f.setStart[i])) {
                    allSetsYaochu = false;
                    break;
                }
            }
            if (allSetsYaochu && f.pair >= 0 && !Tiles.isYaochu(f.pair)) {
                allSetsYaochu = false;
            }
            if (allSetsYaochu && runs >= 1) {
                boolean hasHonorTile = false;
                boolean hasTerminalTile = false;
                for (int k = 0; k < Tiles.KIND_COUNT; k++) {
                    if (allCounts[k] == 0) {
                        continue;
                    }
                    if (Tiles.isHonor(k)) {
                        hasHonorTile = true;
                    }
                    if (Tiles.isTerminal(k)) {
                        hasTerminalTile = true;
                    }
                }
                if (!hasHonorTile && hasTerminalTile) {
                    ys.add(Yaku.normal("纯全带幺九", 3 - kuisagari));
                } else {
                    ys.add(Yaku.normal("混全带幺九", 2 - kuisagari));
                }
            }
        }
        // 混老头
        if (allYaochu) {
            ys.add(Yaku.normal("混老头", 2));
        }
        // 染手
        if (suitsUsed == 1) {
            if (hasHonor) {
                ys.add(Yaku.normal("混一色", 3 - kuisagari));
            } else {
                ys.add(Yaku.normal("清一色", 6 - kuisagari));
            }
        }
        // 古役
        if (r.koyaku) {
            if (hasGodenki(allCounts)) {
                ys.add(Yaku.normal("五门齐", 2));
            }
            if (hasSanrenkou(f)) {
                ys.add(Yaku.normal("三连刻", 2));
            }
            boolean allOpenRuns = true;
            for (int i = 0; i < f.nSets; i++) {
                // 十二落抬：4 副**明**面子 —— 顺子 / 明刻 / **明杠**都算，**暗杠不算**
                //（文档 §古役：「已经副露了 4 个顺子或明刻子（**包括明杠子，不包括暗杠子**）」）。
                // `setFromMeld` = 来自副露，`setConcealed` = 该面子是暗的（暗刻 / 暗杠）
                // →「来自副露**且**是明的」正好把暗杠排除、把大明杠放行。
                // 旧实现用 `setType == QUAD` 一律排除 → 明杠被误判（AUDIT S-60）。
                if (!f.setFromMeld[i] || f.setConcealed[i]) {
                    allOpenRuns = false;
                    break;
                }
            }
            if (allOpenRuns && f.nSets == 4 && f.winSet < 0) {
                ys.add(Yaku.normal("十二落抬", 1));
            }
            if (iipin) {
                ys.add(Yaku.normal("一筒摸月", 5));
            }
            if (chuupin) {
                ys.add(Yaku.normal("九筒捞鱼", 5));
            }
        }
        // 流局满贯
        if (ctx.nagashi) {
            ys.add(Yaku.normal("流局满贯", 5));
        }

        // ---------------- 宝牌
        int dora = 0;
        int ura = 0;
        int aka = 0;
        if (ctx.doraIndicators != null) {
            for (int ind : ctx.doraIndicators) {
                dora += allCounts[Tiles.doraFrom(ind)];
            }
        }
        if (ctx.uraIndicators != null && (ctx.riichi || ctx.doubleRiichi) && r.ura) {
            for (int ind : ctx.uraIndicators) {
                ura += allCounts[Tiles.doraFrom(ind)];
            }
        }
        if (ctx.allTileIds != null) {
            for (int id : ctx.allTileIds) {
                if (Tiles.isRedId(id)) {
                    aka++;
                }
            }
        }

        int hanYaku = 0;
        for (Yaku y : ys) {
            hanYaku += y.han;
        }
        s.hanYaku = hanYaku;
        s.han = hanYaku + dora + ura + aka;
        s.dora = dora;
        s.ura = ura;
        s.aka = aka;
        s.yaku.addAll(ys);
        if (dora > 0) {
            s.yaku.add(Yaku.normal("宝牌", dora));
        }
        if (ura > 0) {
            s.yaku.add(Yaku.normal("里宝牌", ura));
        }
        if (aka > 0) {
            s.yaku.add(Yaku.normal("赤宝牌", aka));
        }

        // 累计役满：M.League 不采用 —— 番数 ≥13 且没有役满役时只按**三倍满**（6000）计
        // （docs/日本麻将.md：M.League 以三倍满为普通役的上限）
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
        s.fu = calcFu(ctx, f, concealed, melds, menzen, r);

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

    /**
     * 番数 + 符数 → **基本点**（满贯以上各档 + 累计役满的规则取舍 + 切上满贯）。
     *
     * <p>为什么单独暴露：打点表的"逐格比对"（`SelfTest.scoreTableTests`）原来在测试里**抄了一份**
     * 这个映射，于是 `Evaluator` 真正用的档位映射（6〜12 番：跳满 / 倍满 / 三倍满）**从来没被断言过**
     * —— 副本与实现漂移也发现不了。现在实局判定与打点表共用这一份。
     *
     * <p>⚠ `han` 是**总番数**（含宝牌）；`rules` 影响两处：13 番档（累计役满 8000 / 三倍满 6000）
     * 与切上满贯（3 番 60 符 / 4 番 30 符按满贯）。所以取舍类断言必须**显式传规则集**。
     * ⚠ 役满**不走这里**（役满是 `8000 × 倍数`，符数与番数全部失效，见上面的役满分支）。
     */
    public static int basePoints(int han, int fu, Rules rules) {
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
        int b = fu * (1 << (2 + han));
        if (b > 2000 || (rules.kiriageMangan && b >= 1920)) {
            return 2000;
        }
        return b;
    }

    // ------------------------------------------------------------------ 符

    public static int calcFu(WinContext ctx, Agari.Form f, int[] concealed, List<Meld> melds, boolean menzen,
                             Rules rules) {
        if (f.type == Agari.TYPE_CHIITOITSU) {
            return 25;
        }
        if (f.type == Agari.TYPE_KOKUSHI) {
            return 0;
        }
        boolean pinfu = isPinfu(ctx, f, menzen);
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
            int t = f.setType[i];
            if (t == Agari.SET_RUN) {
                continue;
            }
            int k = f.setStart[i];
            boolean yaochu = Tiles.isYaochu(k);
            boolean con = f.setConcealed[i] && !(!ctx.tsumo && f.winSet == i);
            if (t == Agari.SET_QUAD) {
                fu += con ? (yaochu ? 32 : 16) : (yaochu ? 16 : 8);
            } else {
                fu += con ? (yaochu ? 8 : 4) : (yaochu ? 4 : 2);
            }
        }
        if (f.pair >= 0) {
            int p = f.pair;
            if (Tiles.isDragon(p)) {
                fu += 2;
            }
            boolean isRound = (p == ctx.roundWind);
            boolean isSeat = (p == ctx.seatWind());
            if (isRound && isSeat) {
                // 连风牌（场风 = 自风）：M.League 只算 2 符，其余规则自风 2 + 场风 2 = 4 符
                fu += rules.doubleWindPairFu;
            } else if (isRound || isSeat) {
                fu += 2;
            }
        }
        switch (f.waitType) {
            case Agari.WAIT_KANCHAN:
            case Agari.WAIT_PENCHAN:
            case Agari.WAIT_TANKI:
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

    public static boolean isPinfu(WinContext ctx, Agari.Form f, boolean menzen) {
        if (!menzen || f.type != Agari.TYPE_STANDARD || f.waitType != Agari.WAIT_RYANMEN) {
            return false;
        }
        int runs = 0;
        for (int i = 0; i < f.nSets; i++) {
            if (f.setType[i] != Agari.SET_RUN) {
                return false;
            }
            runs++;
        }
        if (runs != 4 || f.pair < 0) {
            return false;
        }
        int p = f.pair;
        if (Tiles.isDragon(p) || p == ctx.roundWind || p == ctx.seatWind()) {
            return false;
        }
        return true;
    }

    // ------------------------------------------------------------------ 役种辅助

    private static boolean hasSanshokuDoujun(List<int[]> runs) {
        for (int n = 0; n < 9; n++) {
            boolean a = false;
            boolean b = false;
            boolean c = false;
            for (int[] r : runs) {
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

    private static boolean hasSanshokuDoukou(Agari.Form f) {
        for (int n = 1; n <= 9; n++) {
            boolean a = false;
            boolean b = false;
            boolean c = false;
            for (int i = 0; i < f.nSets; i++) {
                if (f.setType[i] == Agari.SET_RUN) {
                    continue;
                }
                int k = f.setStart[i];
                if (Tiles.isHonor(k) || Tiles.num(k) != n) {
                    continue;
                }
                int s = Tiles.suit(k);
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

    private static boolean hasIttsu(List<int[]> runs) {
        for (int s = 0; s < 3; s++) {
            boolean a = false;
            boolean b = false;
            boolean c = false;
            for (int[] r : runs) {
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

    private static boolean hasSanrenkou(Agari.Form f) {
        for (int s = 0; s < 3; s++) {
            for (int n = 0; n <= 6; n++) {
                boolean a = false;
                boolean b = false;
                boolean c = false;
                for (int i = 0; i < f.nSets; i++) {
                    if (f.setType[i] == Agari.SET_RUN) {
                        continue;
                    }
                    int k = f.setStart[i];
                    if (Tiles.isHonor(k) || Tiles.suit(k) != s) {
                        continue;
                    }
                    int nn = Tiles.num(k) - 1;
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

    private static boolean hasGodenki(int[] allCounts) {
        boolean m = false;
        boolean p = false;
        boolean s = false;
        boolean wind = false;
        boolean dragon = false;
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (allCounts[k] == 0) {
                continue;
            }
            if (Tiles.isHonor(k)) {
                if (Tiles.isWind(k)) {
                    wind = true;
                } else {
                    dragon = true;
                }
            } else {
                int su = Tiles.suit(k);
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

    /**
     * 九莲宝灯判定。
     *
     * @return 0=不是；1=九莲宝灯；2=纯正九莲宝灯
     */
    private static int checkChuuren(int[] allCounts, int winKind) {
        int suit = Tiles.suit(winKind);
        if (suit < 0) {
            return 0;
        }
        int base = suit * 9;
        int[] need = {3, 1, 1, 1, 1, 1, 1, 1, 3};
        for (int i = 0; i < 9; i++) {
            if (allCounts[base + i] < need[i]) {
                return 0;
            }
        }
        int total = 0;
        for (int i = 0; i < 9; i++) {
            total += allCounts[base + i];
        }
        if (total != 14) {
            return 0;
        }
        // 纯正：去掉和了牌后正好是 1112345678999
        int[] pre = allCounts.clone();
        pre[winKind]--;
        boolean pure = true;
        for (int i = 0; i < 9; i++) {
            if (pre[base + i] != need[i]) {
                pure = false;
                break;
            }
        }
        return pure ? 2 : 1;
    }
}
