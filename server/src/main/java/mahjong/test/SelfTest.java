package mahjong.test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.LinkedHashSet;

import mahjong.core.Meld;
import mahjong.core.Rules;
import mahjong.core.Tiles;
import mahjong.bot.Bot;
import mahjong.game.RoundClaims;
import mahjong.game.RoundOptions;
import mahjong.game.RoundScoring;
import mahjong.game.Round;
import mahjong.game.WinCheck;
import mahjong.game.Table;
import mahjong.rules.Agari;
import mahjong.rules.Evaluator;
import mahjong.rules.Payments;
import mahjong.rules.Shanten;
import mahjong.rules.WinContext;
import mahjong.rules.YakuCodes;
import mahjong.replay.Replay;
import mahjong.replay.ReplayRecorder;
import mahjong.replay.ReplayStore;
import mahjong.util.Json;

/** 规则引擎回归自测。运行：java -cp <classes> mahjong.test.SelfTest */
public final class SelfTest {

    private static int pass;
    private static int fail;
    private static final List<String> failures = new ArrayList<>();

    private SelfTest() {
    }

    public static int run() {
        pass = 0;
        fail = 0;
        failures.clear();
        System.out.println("=== 立直麻将服务端自测 ===");
        tileTests();
        shantenTests();
        waitsTests();
        yakuTests();
        fuTests();
        furitenTests();
        stateVisibilityTests();
        optionTests();
        sidewaysTests();
        timeControlTests();
        winCheckTests();
        roundOptionsTests();
        chiValidationTests();
        kuikaeTests();
        multiRonTests();
        tsubameTests();
        endGameTests();
        koyakuAndChankanTests();
        roundClaimsTests();
        dropRepliesTests();
        jsonEncodingTests();
        yakuCodesTests();
        roundScoringTests();
        roundSeedTests();
        scoreTableTests();
        fuOpenTests();
        paymentTests();
        notenPenaltyTests();
        fourKanAbortTests();
        mleagueRulesTests();
        replayTests();
        akaRuleTests();
        meldAkaPickTests();
        seatSwapTests();
        discardAlignTests();
        nagashiLivePathTest();
        simulationTest();
        rinshanTests();
        kanLimitTests();
        furitenRuleTests();
        handEvalTests();
        teacherTests();
        trainingInterfaceTests();
        System.out.println();
        System.out.println("通过 " + pass + " 项，失败 " + fail + " 项");
        if (fail > 0) {
            for (String s : failures) {
                System.out.println("  ✗ " + s);
            }
            System.out.println("SELFTEST FAIL");
            return 1;
        }
        System.out.println("SELFTEST PASS");
        return 0;
    }

    // ------------------------------------------------------------- 断言

    private static void check(String name, boolean ok) {
        if (ok) {
            pass++;
        } else {
            fail++;
            failures.add(name);
        }
    }

    private static void eq(String name, Object got, Object want) {
        boolean ok = got == null ? want == null : got.equals(want);
        if (!ok) {
            failures.add(name + " 期望=" + want + " 实际=" + got);
            fail++;
        } else {
            pass++;
        }
    }

    // ------------------------------------------------------------- 工具

    private static List<Integer> parse(String s) {
        List<Integer> ids = new ArrayList<>();
        List<String> toks = new ArrayList<>();
        String t = s.trim();
        if (t.contains(" ") || t.contains("\t")) {
            for (String x : t.split("\\s+")) {
                if (!x.isEmpty()) {
                    toks.add(x);
                }
            }
        } else {
            for (int i = 0; i + 1 < t.length(); i += 2) {
                toks.add(t.substring(i, i + 2));
            }
        }
        for (String tk : toks) {
            int k = Tiles.parseKind(tk);
            if (k < 0) {
                throw new IllegalArgumentException("非法牌: " + tk);
            }
            int copy = Tiles.isRedStr(tk) ? 0 : 1;
            while (containsKindCopy(ids, k, copy)) {
                copy++;
            }
            ids.add(Tiles.id(k, copy));
        }
        return ids;
    }

    private static boolean containsKindCopy(List<Integer> ids, int kind, int copy) {
        for (int id : ids) {
            if (id == Tiles.id(kind, copy)) {
                return true;
            }
        }
        return false;
    }

    private static int[] counts(String s) {
        int[] c = new int[Tiles.KIND_COUNT];
        for (int id : parse(s)) {
            c[Tiles.kind(id)]++;
        }
        return c;
    }

    /** 纯门前和牌评价。 */
    private static Evaluator.HandScore evalClosed(String hand, String win, boolean tsumo,
                                                  int seat, int dealer, int roundWind) {
        return evalCtx(hand, win, tsumo, seat, dealer, roundWind, false);
    }

    private static Evaluator.HandScore evalCtx(String hand, String win, boolean tsumo,
                                               int seat, int dealer, int roundWind, boolean riichi) {
        return evalCtx(hand, win, tsumo, seat, dealer, roundWind, riichi, Rules.defaults());
    }

    /**
     * 指定规则集的和牌评价。
     *
     * <p>**凡是"规则取舍"型的判据都必须显式传规则集**：默认预设现在是 M.League
     * （`Rules.defaults()` → `new Rules()` → `applyPreset("mleague")`），
     * 直接依赖默认值等于在断言"默认值恰好是某个值"，换个默认预设就误报。
     * 取舍项（加倍役满 / 累计役满 / 切上满贯 / 连风符 / 立直门槛…）一律两边都钉住。
     */
    private static Evaluator.HandScore evalCtx(String hand, String win, boolean tsumo,
                                               int seat, int dealer, int roundWind, boolean riichi,
                                               Rules rules) {
        int[] c = counts(hand);
        WinContext ctx = new WinContext();
        ctx.rules = rules;
        ctx.seat = seat;
        ctx.dealerSeat = dealer;
        ctx.roundWind = 27 + roundWind;
        ctx.tsumo = tsumo;
        ctx.menzen = true;
        ctx.riichi = riichi;
        ctx.winKind = Tiles.parseKind(win);
        ctx.doraIndicators = new ArrayList<>();
        ctx.uraIndicators = new ArrayList<>();
        ctx.allTileIds = parse(hand);
        return Evaluator.evaluate(ctx, c, new ArrayList<>(), ctx.winKind);
    }

    /** 预设规则集（`mleague` / `tenhou` / `majsoul`），供"取舍"类断言两边钉住。 */
    private static Rules preset(String name) {
        Rules r = Rules.defaults();
        r.applyPreset(name);
        return r;
    }

    private static boolean hasYaku(Evaluator.HandScore s, String name) {
        for (Evaluator.Yaku y : s.yaku) {
            if (y.name.equals(name)) {
                return true;
            }
        }
        return false;
    }

    /** 某个役在**结算报文里**上报的番数（役满役 = 13 × 倍数）。找不到返回 -1。 */
    private static int reportedHan(Evaluator.HandScore s, String name) {
        for (Evaluator.Yaku y : s.yaku) {
            if (y.name.equals(name)) {
                return y.equivalentHan();
            }
        }
        return -1;
    }

    private static String yakuNames(Evaluator.HandScore s) {
        StringBuilder sb = new StringBuilder();
        for (Evaluator.Yaku y : s.yaku) {
            if (sb.length() > 0) {
                sb.append('+');
            }
            sb.append(y.name);
        }
        return sb.toString();
    }

    // ------------------------------------------------------------- 牌

    private static void tileTests() {
        eq("parse 1m", Tiles.parseKind("1m"), 0);
        eq("parse 9s", Tiles.parseKind("9s"), 26);
        eq("parse 1z", Tiles.parseKind("1z"), 27);
        eq("parse 7z", Tiles.parseKind("7z"), 33);
        eq("parse 0m", Tiles.parseKind("0m"), 4);
        eq("parse 0p", Tiles.parseKind("0p"), 13);
        eq("parse 0s", Tiles.parseKind("0s"), 22);
        eq("fmt 5m", Tiles.kindToStr(4), "5m");
        eq("fmt 0p", Tiles.kindToStr(13, true), "0p");
        eq("fmt 7z", Tiles.kindToStr(33), "7z");
        check("red 5m", Tiles.isRedId(Tiles.id(4, 0)));
        check("not red 5m", !Tiles.isRedId(Tiles.id(4, 1)));
        check("not red 4m", !Tiles.isRedId(Tiles.id(3, 0)));
        eq("dora 9m→1m", Tiles.doraFrom(8), 0);
        eq("dora 9p→1p", Tiles.doraFrom(17), 9);
        eq("dora 4z→1z", Tiles.doraFrom(30), 27);
        eq("dora 7z→5z", Tiles.doraFrom(33), 31);
        eq("dora 5z→6z", Tiles.doraFrom(31), 32);
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            String s = Tiles.kindToStr(k);
            if (Tiles.parseKind(s) != k) {
                check("roundtrip " + s, false);
            }
        }
        pass++;
    }

    // ------------------------------------------------------------- 向听

    private static void shantenTests() {
        eq("和了形 -1", Shanten.standard(counts("1m1m1m2m2m2m3m3m3m4m4m4m5m5m"), 0), -1);
        eq("听牌 0", Shanten.standard(counts("1m1m1m2m2m2m3m3m3m4m4m4m5m"), 0), 0);
        eq("听牌(暗刻+单张) 0", Shanten.standard(counts("1m1m1m2m2m2m3m3m3m4m4m5m6m"), 0), 0);
        eq("一向听 1", Shanten.standard(counts("1m1m1m3m3m3m5m5m5m7m7m9m2p"), 0), 1);
        eq("国士和了", Shanten.kokushi(counts("1m1m1p9p1s9s1z2z3z4z5z6z7z9m")), -1);
        eq("国士13面听", Shanten.kokushi(counts("1m9m1p9p1s9s1z2z3z4z5z6z7z")), 0);
        eq("七对和了", Shanten.chiitoitsu(counts("1m1m3m3m5m5m7m7m9m9m1p1p3p3p")), -1);
        eq("七对听牌", Shanten.chiitoitsu(counts("1m1m3m3m5m5m7m7m9m9m1p1p3p")), 0);
        eq("副露和了", Shanten.standard(counts("1m2m3m4m5m6m7m7m"), 2), -1);
        eq("全孤立 8", Shanten.standard(counts("1m4m7m1p4p7p1s4s7s1z3z5z7z"), 0), 8);
        eq("非和了(1111型)", Shanten.standard(counts("1m1m1m1m2m2m2m3m3m3m4m4m4m5m"), 0), 0);
    }

    // ------------------------------------------------------------- 听牌

    private static void waitsTests() {
        List<Integer> w = Agari.waits(counts("1m1m1m2m2m2m3m3m3m4m4m4m5p"), 0);
        eq("单骑听5p", w, Arrays.asList(13));
        List<Integer> w0 = Agari.waits(counts("1m1m1m2m2m2m3m3m3m4m4m4m5m"), 0);
        eq("1112223334445m 五面听", w0, Arrays.asList(1, 2, 3, 4, 5));
        List<Integer> w2 = Agari.waits(counts("2m3m4m5m6m7m8m9m9m9m1p2p3p"), 0);
        check("两面听1m/4m", w2.contains(0) && w2.contains(3));
        List<Integer> w3 = Agari.waits(counts("1m9m1p9p1s9s1z2z3z4z5z6z7z"), 0);
        eq("国士13面", w3.size(), 13);
        List<Integer> w4 = Agari.waits(counts("1m1m2m2m3m3m4m4m5m5m6m6m7m"), 0);
        check("七对听牌含7m", w4.contains(6));
    }

    // ------------------------------------------------------------- 役种

    private static void yakuTests() {
        // 平和
        Evaluator.HandScore s = evalClosed("2m3m4m5m6m7m2p3p4p5s6s7s9s9s", "4m", false, 1, 0, 0);
        check("平和成立: " + yakuNames(s), hasYaku(s, "平和"));
        eq("平和 1番", s.han, 1);
        eq("平和荣和30符", s.fu, 30);

        // 平和自摸 20 符
        Evaluator.HandScore s2 = evalClosed("2m3m4m5m6m7m2p3p4p5s6s7s9s9s", "4m", true, 1, 0, 0);
        check("平和自摸含自摸役", hasYaku(s2, "门前清自摸和") && hasYaku(s2, "平和"));
        eq("平和自摸20符", s2.fu, 20);

        // 二杯口 vs 七对子（高点法）
        Evaluator.HandScore s3 = evalClosed("2m2m3m3m4m4m5p5p6p6p7p7p9s9s", "9s", false, 1, 0, 0);
        check("二杯口成立: " + yakuNames(s3), hasYaku(s3, "二杯口"));
        check("二杯口时不计七对子", !hasYaku(s3, "七对子"));

        // 断幺九 + 三色同顺 + 一杯口（高点法取 4 番）
        Evaluator.HandScore s4 = evalClosed("2m2m3m3m4m4m5m5m2p3p4p2s3s4s", "3m", false, 1, 0, 0);
        check("三色同顺成立: " + yakuNames(s4), hasYaku(s4, "三色同顺"));
        eq("高点法取4番", s4.han, 4);

        // 七对子
        Evaluator.HandScore s5 = evalClosed("1m1m4m4m8m8m9m9m4p4p2s2s4z4z", "4z", false, 1, 0, 0);
        check("七对子成立: " + yakuNames(s5), hasYaku(s5, "七对子"));
        eq("七对子25符", s5.fu, 25);

        // 国士无双 / 十三面
        Evaluator.HandScore s6 = evalClosed("1m1m1p9p1s9s1z2z3z4z5z6z7z9m", "9m", false, 1, 0, 0);
        eq("国士无双役满", s6.yakuman, 1);
        check("国士无双名: " + yakuNames(s6), hasYaku(s6, "国士无双"));
        // 结算报文里役满役的番数 = 13 × 倍数（**绝不能是 0**：界面会显示成「0 番」）
        eq("国士无双上报 13 番（役满等价）", reportedHan(s6, "国士无双"), 13);
        eq("国士无双合计番数 = 13 × 1", s6.totalHan(), 13);

        // ── 「两倍役满」：大四喜 / 国士无双十三面 / 四暗刻单骑 / 纯正九莲宝灯 这 4 种
        //    在《雀魂》计 2 倍，《天凤》与 **M.League 计 1 倍**（docs/日本麻将.md §两倍役满）。
        //    两类规则都显式钉住，不依赖默认值。
        //    ⚠ 约定：加倍与否只改**取值**，不改役种名 —— 十三面/单骑/纯正在任何规则下都是独立役。
        Rules dbl = preset("majsoul");   // doubleYakuman = true
        Rules ml = preset("mleague");    // doubleYakuman = false
        check("《雀魂》预设加倍役满", dbl.doubleYakuman);
        check("M.League 预设不加倍", !ml.doubleYakuman);

        Evaluator.HandScore s7 = evalCtx("1m9m1p9p1s9s1z2z3z4z5z6z7z1m", "1m", false, 1, 0, 0, false, dbl);
        check("国士十三面双倍: " + yakuNames(s7), s7.yakuman == 2 && hasYaku(s7, "国士无双十三面"));
        eq("国士十三面上报 26 番（13 × 2）", reportedHan(s7, "国士无双十三面"), 26);
        eq("两倍役满合计番数 = 26", s7.totalHan(), 26);
        eq("两倍役满基本点 = 16000", s7.base, 16000);
        // M.League：同一个役、只算 1 倍 —— 名字必须还是「国士无双十三面」
        Evaluator.HandScore s7m = evalCtx("1m9m1p9p1s9s1z2z3z4z5z6z7z1m", "1m", false, 1, 0, 0, false, ml);
        check("M.League 仍报「国士无双十三面」（只是不加倍）: " + yakuNames(s7m),
                hasYaku(s7m, "国士无双十三面") && !hasYaku(s7m, "国士无双"));
        eq("M.League 国士十三面 = 1 倍", s7m.yakuman, 1);
        eq("M.League 国士十三面合计 13 番", s7m.totalHan(), 13);
        eq("M.League 国士十三面基本点 = 8000", s7m.base, 8000);
        eq("M.League 国士十三面打点标签", s7m.limit, "役满");

        // 四暗刻单骑
        Evaluator.HandScore s8 = evalCtx("1m1m1m1p1p1p4p4p4p3s3s3s1z1z", "1z", true, 1, 0, 0, false, dbl);
        check("四暗刻单骑: " + yakuNames(s8), hasYaku(s8, "四暗刻单骑") && s8.yakuman == 2);
        eq("四暗刻单骑上报 26 番", reportedHan(s8, "四暗刻单骑"), 26);
        Evaluator.HandScore s8m = evalCtx("1m1m1m1p1p1p4p4p4p3s3s3s1z1z", "1z", true, 1, 0, 0, false, ml);
        check("M.League 仍是「四暗刻单骑」1 倍: " + yakuNames(s8m),
                hasYaku(s8m, "四暗刻单骑") && s8m.yakuman == 1 && s8m.base == 8000);
        // 普通役的上报番数就是它自己的番（不能被役满那套折算改到）
        eq("普通役上报番数不变（三色同顺 2 番）", reportedHan(s4, "三色同顺"), 2);
        eq("非役满合计番数不变", s4.totalHan(), s4.han);

        // 四暗刻（荣和刻子则不成立，应为三暗刻+对对和）
        Evaluator.HandScore s9 = evalClosed("1m1m1m1p1p1p4p4p4p3s3s3s1z1z", "1m", false, 1, 0, 0);
        check("荣和第三张刻子→三暗刻: " + yakuNames(s9), !hasYaku(s9, "四暗刻") && hasYaku(s9, "三暗刻"));

        // 大三元
        Evaluator.HandScore s10 = evalClosed("5z5z5z6z6z6z7z7z7z6s7s8s5m5m", "5m", true, 1, 0, 0);
        check("大三元: " + yakuNames(s10), hasYaku(s10, "大三元"));

        // 小三元
        Evaluator.HandScore s11 = evalClosed("5z5z5z6z6z6z7z7z6s7s8s8m8m8m", "8m", false, 1, 0, 0);
        check("小三元: " + yakuNames(s11), hasYaku(s11, "小三元"));

        // 九莲宝灯 / 纯正九莲宝灯（同一副牌，和了牌不同）
        Evaluator.HandScore s12 = evalClosed("1m1m1m2m2m3m4m5m6m7m8m9m9m9m", "1m", false, 1, 0, 0);
        check("九莲宝灯: " + yakuNames(s12), hasYaku(s12, "九莲宝灯") && s12.yakuman == 1);

        Evaluator.HandScore s13 = evalCtx("1m1m1m2m2m3m4m5m6m7m8m9m9m9m", "2m", false, 1, 0, 0, false, dbl);
        check("纯正九莲: " + yakuNames(s13), hasYaku(s13, "纯正九莲宝灯") && s13.yakuman == 2);
        Evaluator.HandScore s13m = evalCtx("1m1m1m2m2m3m4m5m6m7m8m9m9m9m", "2m", false, 1, 0, 0, false, ml);
        check("M.League 仍是「纯正九莲宝灯」1 倍: " + yakuNames(s13m),
                hasYaku(s13m, "纯正九莲宝灯") && s13m.yakuman == 1 && s13m.base == 8000);

        // 清一色 + 二杯口 + 纯全 + 平和 = 累计役满（番数 ≥13 但没有役满役）
        Evaluator.HandScore s14 = evalClosed("1p1p1p1p2p2p3p3p7p7p8p8p9p9p", "1p", false, 1, 0, 0);
        check("累计役满: " + yakuNames(s14) + " han=" + s14.han, s14.han >= 13);
        check("含清一色", hasYaku(s14, "清一色"));
        check("含纯全带幺九", hasYaku(s14, "纯全带幺九"));
        check("含平和", hasYaku(s14, "平和"));
        // ⚠ 累计役满也是"规则取舍"：**M.League 不采用**，普通役的上限就是三倍满
        //   （docs/日本麻将.md §役满：「M.League 以三倍满为普通役的上限」/ §M.League 规则）
        eq("M.League 13 番无役满役 → 三倍满", s14.limit, "三倍满");
        eq("M.League 13 番 → 基本点 6000（不是 8000）", s14.base, 6000);
        Evaluator.HandScore s14kz = evalCtx("1p1p1p1p2p2p3p3p7p7p8p8p9p9p", "1p", false, 1, 0, 0, false,
                preset("tenhou"));
        eq("《天凤》13 番 → 累计役满", s14kz.limit, "累计役满");
        eq("《天凤》13 番 → 基本点 8000", s14kz.base, 8000);

        // 绿一色
        Evaluator.HandScore s15 = evalClosed("2s2s2s3s3s3s4s4s4s6s6s6s8s8s", "8s", true, 1, 0, 0);
        check("绿一色: " + yakuNames(s15), hasYaku(s15, "绿一色"));

        // 字一色
        Evaluator.HandScore s16 = evalClosed("1z1z1z2z2z2z3z3z3z4z4z4z5z5z", "5z", true, 1, 0, 0);
        check("字一色: " + yakuNames(s16), hasYaku(s16, "字一色"));

        // 清老头
        Evaluator.HandScore s17 = evalClosed("1m1m1m9m9m9m1p1p1p9p9p9p1s1s", "1s", true, 1, 0, 0);
        check("清老头: " + yakuNames(s17), hasYaku(s17, "清老头"));

        // 大四喜
        Evaluator.HandScore s18 = evalClosed("1z1z1z2z2z2z3z3z3z4z4z4z5z5z", "5z", true, 1, 0, 0);
        check("大四喜复合字一色", hasYaku(s18, "大四喜") && hasYaku(s18, "字一色"));

        // 役牌
        Evaluator.HandScore s19 = evalClosed("1z1z1z2m3m4m5m6m7m2p3p4p9s9s", "1z", false, 0, 0, 0);
        check("场风东+自风东（连风2番）: " + yakuNames(s19),
                hasYaku(s19, "场风 东") && hasYaku(s19, "自风 东"));

        // 混一色 / 一气通贯
        Evaluator.HandScore s20 = evalClosed("1m2m3m4m5m6m7m8m9m1m1m1z1z1z", "9m", true, 1, 0, 0);
        check("一气通贯", hasYaku(s20, "一气通贯"));
        check("混一色", hasYaku(s20, "混一色"));

        // 无役判定
        Evaluator.HandScore s21 = evalClosed("1m2m3m5m6m7m1p2p3p5p6p7p9s9s", "9s", false, 1, 0, 0);
        check("无役不可和: " + yakuNames(s21), !s21.valid);

        // 三色同刻
        Evaluator.HandScore s22 = evalClosed("6m6m6m6p6p6p6s6s6s7s8s9s9m9m", "6s", false, 1, 0, 0);
        check("三色同刻: " + yakuNames(s22), hasYaku(s22, "三色同刻"));

        // 混老头
        Evaluator.HandScore s23 = evalClosed("6z6z6z2z2z2z9s9s9s1m1p1p1m1m", "1m", false, 1, 0, 0);
        check("混老头: " + yakuNames(s23), hasYaku(s23, "混老头"));
    }

    // ------------------------------------------------------------- 符

    private static void fuTests() {
        // 门前荣和 + 中张暗刻 + 单骑
        Evaluator.HandScore s = evalClosed("1m2m3m4m5m6m7m8m9m3p3p3p5s5s", "5s", false, 1, 0, 0);
        // 20 + 10(门前) + 4(暗刻3p 中张) + 2(单骑) = 36 → 40
        eq("符 36→40", s.fu, 40);

        // 门清自摸（幺九暗刻 + 单骑）：20+2+8+2 = 32 → 40
        Evaluator.HandScore s2 = evalClosed("1m1m1m2m3m4m5m6m7m2p3p4p9s9s", "9s", true, 1, 0, 0);
        check("自摸符 = 40: " + s2.fu, s2.fu == 40);

        // 连风雀头（自风 = 场风）：一般规则 4 符，**M.League 只算 2 符**。
        // 牌型要挑在**进位线**上，否则 4 与 2 都进位成同一个值、断言测不出差别：
        //   1m1m1m(幺九暗刻 8) + 234m + 567m + 67p/8p(两面) + 1z1z(东，自家是东、场风也是东)
        //   4 符：20 + 10(门前荣和) + 8 + 4 = 42 → 50    （docs/日本麻将.md §符：连风雀头 4 符）
        //   2 符：20 + 10 + 8 + 2     = 40 → 40          （M.League：连风雀头 2 符）
        Evaluator.HandScore lf4 = evalCtx("1m1m1m2m3m4m5m6m7m6p7p8p1z1z", "8p", false, 0, 0, 0, true,
                preset("tenhou"));
        eq("连风雀头 4 符 → 50", lf4.fu, 50);
        Evaluator.HandScore lf2 = evalCtx("1m1m1m2m3m4m5m6m7m6p7p8p1z1z", "8p", false, 0, 0, 0, true,
                preset("mleague"));
        eq("M.League 连风雀头 2 符 → 40", lf2.fu, 40);

        // 幺九暗刻 8 符
        Evaluator.HandScore s4 = evalCtx("1m1m1m2m3m4m5m6m7m2p3p4p9s9s", "9s", false, 1, 0, 0, true);
        // 20 + 10 + 8(幺九暗刻) + 2(单骑) = 40
        eq("幺九暗刻符", s4.fu, 40);

        // 嵌张 +2
        Evaluator.HandScore s5 = evalCtx("1m1m1m2m3m4m5m6m7m2p3p4p9s9s", "3m", false, 1, 0, 0, true);
        // 111m 暗刻(8) + 嵌张(2)：20+10+8+2 = 40
        eq("嵌张符 40", s5.fu, 40);

        // 切上满贯（3 番 60 符 / 4 番 30 符 → 满贯）见 mleagueRulesTests()：
        // 它与"连风符""累计役满"同属**规则取舍**，放同一处两边钉住更清楚。
    }

    // ------------------------------------------------------------- 振听 / 立直

    private static mahjong.game.Round newRound() {
        return newRound(Rules.defaults());
    }

    /** 指定规则集的一局（座位 0 是庄）。规则取舍类断言一律显式传规则集，别依赖默认预设。 */
    private static mahjong.game.Round newRound(Rules rules) {
        Table t = new Table("T", "t", rules);
        return new mahjong.game.Round(t, 0, 1, 0, 0,
                new int[]{25000, 25000, 25000, 25000}, 0, 7);
    }

    // ------------------------------------------------- 重连快照的信息可见性

    /**
     * {@code state}（重连 / 旁观快照）**不能**泄露别家的隐藏信息。
     *
     * <p>回归的是一个真实存在过的漏洞：`state.furiten` 原来把四家的振听原样下发，
     * 而**临时振听**（放过一张能和牌的张）等价于「他听牌了」—— 改造过的客户端只要
     * 反复 rejoin 刷新快照，就能读出「谁在听牌」。客户端其实只读自己那一项，
     * 所以修法是只填请求者自己那一项（数组长度保持 4，老客户端不受影响）。
     */
    private static void stateVisibilityTests() {
        Table t = new Table("T", "t", Rules.defaults());
        mahjong.game.Round r = new mahjong.game.Round(t, 0, 1, 0, 0,
                new int[]{25000, 25000, 25000, 25000}, 0, 7);
        t.currentRound = r;
        r.hand[0].addAll(parse("1m2m3m4m5m6m7m8m9m1p2p3p4p"));
        r.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p"));
        r.furitenPerm[1] = true;   // 座位 1：舍张/立直振听
        r.furitenTemp[2] = true;   // 座位 2：同巡振听（这一条最能暴露「他在听牌」）
        check("自检前提：座位 1 振听", r.isFuriten(1));
        check("自检前提：座位 2 振听", r.isFuriten(2));

        java.util.Map<String, Object> st0 = t.stateFor(0);
        java.util.Map<String, Object> st1 = t.stateFor(1);
        java.util.Map<String, Object> stSp = t.stateFor(-1);   // 旁观者

        check("state 不把座位 1 的振听给座位 0", !stateFlag(st0, "furiten", 1));
        check("state 不把座位 2 的振听给座位 0", !stateFlag(st0, "furiten", 2));
        check("state 仍把振听给本人（座位 1 自己那项）", stateFlag(st1, "furiten", 1));
        check("state 旁观者四项全 false", !stateFlag(stSp, "furiten", 1) && !stateFlag(stSp, "furiten", 2));
        eq("state.furiten 长度仍为 4（老客户端兼容）", stateList(st0, "furiten").size(), 4);
        check("state.hand 只有自己那 13 张", stateList(st0, "hand").size() == 13);
        check("state.hand 不含别家的牌", !stateList(st0, "hand").contains("5p"));
        check("state 旁观者没有手牌", stateList(stSp, "hand").isEmpty());
    }

    private static java.util.List<?> stateList(java.util.Map<String, Object> m, String key) {
        Object v = m.get(key);
        return (v instanceof java.util.List) ? (java.util.List<?>) v : java.util.Collections.emptyList();
    }

    private static boolean stateFlag(java.util.Map<String, Object> m, String key, int i) {
        java.util.List<?> l = stateList(m, key);
        return i < l.size() && Boolean.TRUE.equals(l.get(i));
    }

    private static void furitenTests() {
        // 听 5p，自家牌河里已有 5p → 舍张振听
        mahjong.game.Round r1 = newRound();
        r1.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p"));
        eq("听5p", r1.waitKinds(1), Arrays.asList(13));
        check("未见5p时非振听", !r1.isFuriten(1));
        // ⚠ 必须走**生产的记账**（`recordDiscard`）：舍张振听的判据是"曾经打出过的牌"，
        //   而不是"现在牌河里有的牌" —— 直接往 `discards[1]` 里塞会绕过那份账。
        r1.debugPushDiscard(1, "5p", false);
        check("舍张振听", r1.isFuriten(1));

        // 同巡振听
        mahjong.game.Round r2 = newRound();
        r2.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p"));
        r2.furitenTemp[1] = true;
        check("同巡振听", r2.isFuriten(1));

        // 立直振听（永久）
        mahjong.game.Round r3 = newRound();
        r3.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p"));
        r3.furitenPerm[1] = true;
        check("立直振听", r3.isFuriten(1));

        // 立直门槛是**规则取舍**，两侧都要钉住规则集（docs/日本麻将.md §立直 / M.League 规则）：
        //   一般规则：门前 + 点数 ≥ 1000 + 剩余牌 ≥ 4 + 打后听牌
        //   M.League：**取消点数与残牌两项要求**，但「摸到海底牌之后不可立直」
        mahjong.game.Round r5 = newRound(preset("tenhou"));
        r5.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p5p"));
        r5.scores[1] = 900;
        check("点数不足不可立直（一般规则）", !r5.canRiichi(1, Tiles.id(13, 1)));
        mahjong.game.Round r5m = newRound();
        r5m.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p5p"));
        r5m.scores[1] = 900;
        check("M.League 点数不足也可立直", r5m.canRiichi(1, Tiles.id(13, 1)));

        // 残牌门槛：留 3 张可摸牌 → 一般规则不可立直，M.League 可以
        mahjong.game.Round r5b = newRound(preset("tenhou"));
        r5b.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p5p"));
        r5b.debugDrainWallTo(3);
        check("残牌不足 4 张不可立直（一般规则）", !r5b.canRiichi(1, Tiles.id(13, 1)));
        mahjong.game.Round r5c = newRound();
        r5c.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p5p"));
        r5c.debugDrainWallTo(3);
        check("M.League 残牌 3 张也可立直", r5c.canRiichi(1, Tiles.id(13, 1)));

        // 摸到海底牌之后（可摸牌山见底）不可立直 —— M.League 的立直要求"还有下一次摸牌"
        mahjong.game.Round r5d = newRound();
        r5d.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p5p"));
        r5d.debugDrainWallTo(0);
        check("M.League 摸到海底后不可立直", !r5d.canRiichi(1, Tiles.id(13, 1)));
        mahjong.game.Round r5e = newRound(preset("tenhou"));
        r5e.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p5p"));
        r5e.debugDrainWallTo(4);
        check("一般规则下残牌 4 张仍可立直", r5e.canRiichi(1, Tiles.id(13, 1)));
        mahjong.game.Round r4 = newRound();
        r4.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p5p"));
        check("两面打后听牌可立直", r4.canRiichi(1, Tiles.id(13, 1)));
        mahjong.game.Round r4b = newRound();
        r4b.hand[1].addAll(parse("1m3m5m7m9m1p3p5p7p9p1s3s5s5s"));
        boolean anyRiichi = false;
        for (int id : new ArrayList<>(r4b.hand[1])) {
            if (r4b.canRiichi(1, id)) {
                anyRiichi = true;
            }
        }
        check("完全不成形时不可立直", !anyRiichi);
        r4.menzen[1] = false;
        check("副露后不可立直", !r4.canRiichi(1, Tiles.id(13, 1)));

        // 食替禁止
        mahjong.game.Round r6 = newRound();
        r6.hand[2].addAll(parse("4m5m1p2p3p4p5p6p7p8p9p9s9s"));
        r6.menzen[2] = false;
        check("他人手牌立直判定不受影响", !r6.canRiichi(2, Tiles.id(12, 1)));

        // 流局满贯：全部幺九牌
        mahjong.game.Round r7 = newRound();
        for (String s : new String[]{"1m", "9m", "1p", "1z", "2z"}) {
            r7.discards[0].add(Tiles.id(Tiles.parseKind(s), 1));
        }
        boolean allYaochu = true;
        for (int id : r7.discards[0]) {
            if (!Tiles.isYaochu(Tiles.kind(id))) {
                allYaochu = false;
            }
        }
        check("流局满贯牌河全幺九", allYaochu);
    }

    // ------------------------------------------------------------- 和牌选项下发

    private static void optionTests() {
        // 自摸选项：门前 + 和了形 → 必须下发 tsumo
        mahjong.game.Round r1 = newRound();
        r1.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p5p"));
        java.util.List<String> t1 = r1.debugTurnOptionTypes(1, Tiles.id(13, 1));
        check("自摸时下发 tsumo 选项: " + t1, t1.contains("tsumo"));
        check("自摸时也下发 discard 选项", t1.contains("discard"));

        // 未和了形时不应下发 tsumo
        mahjong.game.Round r2 = newRound();
        r2.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p6p"));
        java.util.List<String> t2 = r2.debugTurnOptionTypes(1, Tiles.id(13, 1));
        check("未和了形不下发 tsumo: " + t2, !t2.contains("tsumo"));

        // 荣和选项：立直听牌 → 必须下发 ron
        mahjong.game.Round r3 = newRound();
        r3.hand[2].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p"));
        r3.riichi[2] = true;
        r3.hand[2].remove((Integer) Tiles.id(13, 1));
        r3.hand[2].add(Tiles.id(13, 2));
        java.util.List<String> c1 = r3.debugClaimOptionTypes(2, 1, Tiles.id(13, 1));
        check("听牌他舍时下发 ron 选项: " + c1, c1.contains("ron"));

        // 振听时不应下发 ron
        mahjong.game.Round r4 = newRound();
        r4.hand[2].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p"));
        r4.riichi[2] = true;
        r4.debugPushDiscard(2, "5p", false);       // 走生产记账（曾经打出过，见 furitenRuleTests）
        java.util.List<String> c2 = r4.debugClaimOptionTypes(2, 1, Tiles.id(13, 1));
        check("振听时不下发 ron: " + c2, !c2.contains("ron"));
    }
    // ------------------------------------------------------------- 横置牌顺延

    private static void sidewaysTests() {
        // 立直宣言牌横置；被鸣走后顺延到下一张打出的牌；再被鸣走继续顺延
        mahjong.game.Round r = newRound();
        r.discards[1].add(Tiles.id(0, 1));           // 第 0 张：立直宣言牌
        r.debugNoteDiscard(1, true);
        check("宣言牌横置", r.isSidewaysDiscard(1, 0));

        r.discards[1].add(Tiles.id(1, 1));           // 第 1 张：普通打牌
        r.debugNoteDiscard(1, false);
        check("后序牌不横置", !r.isSidewaysDiscard(1, 1));

        // 宣言牌被鸣走 → 横置顺延
        r.debugNoteCalledFromRiver(1, 0);
        r.discards[1].remove(0);
        check("宣言牌被鸣走后进入顺延态", r.isSidewaysPending(1));

        r.discards[1].add(Tiles.id(2, 1));           // 顺延牌
        r.debugNoteDiscard(1, false);
        check("顺延牌横置", r.isSidewaysDiscard(1, 1));
        check("顺延后退出顺延态", !r.isSidewaysPending(1));

        // 顺延牌又被鸣走 → 继续顺延
        r.debugNoteCalledFromRiver(1, 1);
        r.discards[1].remove(1);
        check("顺延牌被鸣走后再次顺延", r.isSidewaysPending(1));
        r.discards[1].add(Tiles.id(3, 1));
        r.debugNoteDiscard(1, false);
        check("第二次顺延牌横置", r.isSidewaysDiscard(1, 1));
    }
    // ------------------------------------------------------------- 思考时间（额外时长）

    private static void timeControlTests() {
        // 20s 额外 + 5s 基本：7.3s 出牌 → 超出 2.3s → 剩余 17.7s 向上去整为 18s
        mahjong.game.Round r = newRound();
        r.rules.thinkingBaseMs = 5000;
        r.rules.thinkingBankMs = 20000;
        r.table.seat(1).timeBankMs = 20000;

        r.debugChargeThinkingTime(1, 3000);          // 基本时长内 → 不扣
        eq("基本时长内不扣额外时长", r.table.seat(1).timeBankMs, 20000);

        r.debugChargeThinkingTime(1, 7300);          // 超出 2.3s → 17.7s → 18s
        eq("超出部分扣除且剩余向上去整", r.table.seat(1).timeBankMs, 18000);

        r.debugChargeThinkingTime(1, 5000);          // 恰好用满基本时长 → 不扣
        eq("恰好用满基本时长不扣", r.table.seat(1).timeBankMs, 18000);

        r.debugChargeThinkingTime(1, 18001);         // 超出 13.001s → 剩 4.999s → 向上取整 5s
        eq("剩余 4999ms 向上取整为 5s", r.table.seat(1).timeBankMs, 5000);
        r.debugChargeThinkingTime(1, 60000);         // 超出远超剩余 → 归零
        eq("额外时长耗尽归零", r.table.seat(1).timeBankMs, 0);

        // 剩余刚好 1ms 也应向上取整成 1s（偏向玩家）
        r.table.seat(2).timeBankMs = 2000;
        r.debugChargeThinkingTime(2, 6000);          // 超出 1s → 剩 1s
        eq("剩余 1s", r.table.seat(2).timeBankMs, 1000);
        r.table.seat(2).timeBankMs = 2000;
        r.debugChargeThinkingTime(2, 5999);          // 超出 0.999s → 剩 1000.001 → 2s（向上）
        eq("剩余 1000.001ms 向上取整为 2s", r.table.seat(2).timeBankMs, 2000);
    }
    // ------------------------------------------------------------- 和了形纯函数

    /** 由牌串生成 34 长度的暗牌计数。 */
    private static int[] countsOf(String tiles) {
        int[] c = new int[34];
        for (int id : parse(tiles)) {
            c[Tiles.kind(id)]++;
        }
        return c;
    }

    private static void winCheckTests() {
        // 张数不符 → null（10 张、无副露，怎么算都不是和了形张数）
        check("张数不符时 counts 返回 null",
                WinCheck.counts(countsOf("1m2m3m4m5m6m7m8m9m1p"), 0, Tiles.id(13, 0), false) == null);

        // 荣和：13 张 + 一张和了牌 = 14 张，且成和了形
        int[] c = WinCheck.counts(countsOf("1m1m1m2m2m2m3m3m3m4m4m4m5p"), 0, Tiles.id(13, 0), false);
        check("荣和并入后张数为 14", c != null && Tiles.sum(c) == 14);
        check("荣和并入后成和了形", WinCheck.isComplete(c, 0));

        // 自摸：暗牌里已含和了牌，不能重复加（14 张传入仍是 14）
        int[] t2 = WinCheck.counts(countsOf("1m1m1m2m2m2m3m3m3m4m4m4m5p5p"), 0, Tiles.id(13, 0), true);
        check("自摸不重复加牌", t2 != null && Tiles.sum(t2) == 14);

        // 一副露时暗牌应为 11 张
        // 一副露时：和了前暗牌 10 张，并入和了牌后 11 张（14 - 3）
        check("有一副露时和了形张数为 11",
                WinCheck.counts(countsOf("1m1m1m2m2m2m3m3m3m4m"), 1, Tiles.id(4, 1), false) != null);

        // 挡住的原因
        check("荣和 + 振听 → furiten", "furiten".equals(WinCheck.blockReason(false, true)));
        check("荣和 + 非振听 → no_yaku", "no_yaku".equals(WinCheck.blockReason(false, false)));
        check("自摸不看振听 → no_yaku", "no_yaku".equals(WinCheck.blockReason(true, true)));
    }
    // ------------------------------------------------------------- 选项生成纯函数

    /** 把选项列表拼成 "a,b,c" 便于断言。 */
    private static String joinOpts(List<Object> l) {
        StringBuilder sb = new StringBuilder();
        for (Object o : l) {
            if (sb.length() > 0) {
                sb.append(',');
            }
            sb.append(String.valueOf(o).replace(" ", ""));   // 对 JSON 工具的空白格式不敏感
        }
        return sb.toString();
    }

    private static void roundOptionsTests() {
        final List<Integer> hand = parse("1m1m2m3m3m3m");
        final Set<Integer> none = new LinkedHashSet<>();

        // 未立直：手牌去重后列出
        eq("未立直时手牌去重", joinOpts(RoundOptions.discardChoices(hand, false, -1, none)), "1m,2m,3m");

        // 振听禁打的牌种要排除（2m）
        final Set<Integer> forbid = new LinkedHashSet<>();
        forbid.add(Tiles.kind(Tiles.id(1, 0)));
        eq("振听禁打的牌种不出现", joinOpts(RoundOptions.discardChoices(hand, false, -1, forbid)), "1m,3m");

        // 已立直：只能摸切，候选只有刚摸到的那张
        eq("立直后只能打出刚摸到的牌",
                joinOpts(RoundOptions.discardChoices(hand, true, Tiles.id(13, 1), none)), "5p");

        // 吃：枚举所有可行搭子
        int[] c34 = new int[34];
        c34[Tiles.kind(Tiles.id(0, 0))] = 1;   // 1m
        c34[Tiles.kind(Tiles.id(1, 0))] = 1;   // 2m
        c34[Tiles.kind(Tiles.id(3, 0))] = 1;   // 4m
        c34[Tiles.kind(Tiles.id(4, 0))] = 1;   // 5m
        eq("吃 3m 的三种组合（1m2m / 2m4m / 4m5m）",
                joinOpts(RoundOptions.chiSets(c34, Tiles.kind(Tiles.id(2, 0)))),
                "[1m,2m],[2m,4m],[4m,5m]");

        // 边界：吃 1m 不能串到 9m/1p
        int[] cEdge = new int[34];
        cEdge[Tiles.kind(Tiles.id(7, 0))] = 1;   // 9m（故意放一张，验证不会跨花色配成 9m1m2m）
        cEdge[Tiles.kind(Tiles.id(1, 0))] = 1;   // 2m
        cEdge[Tiles.kind(Tiles.id(2, 0))] = 1;   // 3m
        eq("吃 1m 不跨花色（只剩 2m3m 组合）",
                joinOpts(RoundOptions.chiSets(cEdge, Tiles.kind(Tiles.id(0, 0)))), "[2m,3m]");

        // 字牌不能吃
        eq("字牌不能吃", joinOpts(RoundOptions.chiSets(c34, Tiles.kind(Tiles.id(27, 0)))), "");
        // 立直但没摸牌（不该发生）：退化为列出全部可打
        eq("立直未摸牌时退化为常规列表",
                joinOpts(RoundOptions.discardChoices(hand, true, -1, none)), "1m,2m,3m");
    }
    // ------------------------------------------------------------- 鸣牌收工判据

    private static void roundClaimsTests() {
        List<Integer> asked = Arrays.asList(1, 2);
        List<Integer> noRon = Arrays.asList();
        List<Integer> ronAt2 = Arrays.asList(2);
        Set<Integer> none = new LinkedHashSet<>();
        Set<Integer> only2 = new LinkedHashSet<>(Arrays.asList(2));

        // 都答完了 → 收工
        check("都答完了就该收工",
                RoundClaims.shouldStop(Arrays.asList(), ronAt2, none, 5000,
                        null, new HashMap<>(), new HashMap<>()));

        // 没人能荣和时，不能因为"荣和者都答完了"就收工（否则丢掉碰/吃）
        check("无荣和者时不得提前收工",
                !RoundClaims.shouldStop(asked, noRon, none, 5000,
                        null, new HashMap<>(), new HashMap<>()));

        // 有荣和者、且他已应答 → 不必再等其他人
        check("荣和者已应答就不再等",
                RoundClaims.shouldStop(asked, ronAt2, only2, 5000,
                        null, new HashMap<>(), new HashMap<>()));

        // 有荣和者但他还没答 → 继续等
        check("荣和者未答则继续等",
                !RoundClaims.shouldStop(asked, ronAt2, none, 5000,
                        null, new HashMap<>(), new HashMap<>()));

        // 回包认领：认错人 / 过期回包必须丢弃
        check("本轮被问且 ask_id 对得上 → 认领",
                RoundClaims.acceptsReply(true, 7L, true, 7L));
        check("ask_id 对不上（过期回包）→ 丢弃",
                !RoundClaims.acceptsReply(true, 7L, true, 6L));
        check("本轮没被问的座位 → 丢弃",
                !RoundClaims.acceptsReply(false, null, true, 7L));
        check("没有挂起询问 → 丢弃",
                !RoundClaims.acceptsReply(true, null, true, 7L));
        check("回包不带 ask_id → 按座位认领（兼容老客户端）",
                RoundClaims.acceptsReply(true, 7L, false, -1L));
        // 超时兜底：哪怕没人应答也要收工
        check("超时必须兜底收工",
                RoundClaims.shouldStop(asked, noRon, none, 0,
                        null, new HashMap<>(), new HashMap<>()));

        // ---------------- 优先级：胡 > 杠 = 碰 > 吃 ----------------
        eq("等级表：吃", RoundClaims.rankOf("chi"), RoundClaims.RANK_CHI);
        eq("等级表：碰", RoundClaims.rankOf("pon"), RoundClaims.RANK_PON);
        eq("等级表：杠", RoundClaims.rankOf("kan"), RoundClaims.RANK_KAN);
        eq("等级表：胡", RoundClaims.rankOf("ron"), RoundClaims.RANK_RON);
        eq("等级表：pass 无等级", RoundClaims.rankOf("pass"), RoundClaims.RANK_NONE);
        check("胡 > 杠", RoundClaims.RANK_RON > RoundClaims.RANK_KAN);
        check("杠 = 碰（同级，由座次决）", RoundClaims.RANK_KAN == RoundClaims.RANK_PON + 1);
        check("碰 > 吃", RoundClaims.RANK_PON > RoundClaims.RANK_CHI);

        // 同级看座次：近的赢
        RoundClaims.Claim ponFar = new RoundClaims.Claim(RoundClaims.RANK_PON, 3);
        check("同级更近者压过更远者",
                RoundClaims.canBeat(ponFar, RoundClaims.RANK_PON, 1));
        check("同级更远者压不过更近者",
                !RoundClaims.canBeat(new RoundClaims.Claim(RoundClaims.RANK_PON, 1),
                        RoundClaims.RANK_PON, 3));
        check("高等级压过低等级",
                RoundClaims.canBeat(new RoundClaims.Claim(RoundClaims.RANK_CHI, 1),
                        RoundClaims.RANK_PON, 3));
        check("低等级压不过高等级（吃压不过碰）",
                !RoundClaims.canBeat(new RoundClaims.Claim(RoundClaims.RANK_PON, 3),
                        RoundClaims.RANK_CHI, 1));
        check("pass 永远压不过别人",
                !RoundClaims.canBeat(new RoundClaims.Claim(RoundClaims.RANK_CHI, 1),
                        RoundClaims.RANK_NONE, 1));

        // 高优先级到手 → 不再等低优先级（这就是"无须再等"的核心）
        Map<Integer, Integer> rankOfAsked = new HashMap<>();
        Map<Integer, Integer> dist = new HashMap<>();
        List<Integer> asked12 = Arrays.asList(1, 2);
        dist.put(1, 1);
        dist.put(2, 2);
        rankOfAsked.put(1, RoundClaims.RANK_CHI);
        rankOfAsked.put(2, RoundClaims.RANK_CHI);
        check("已到手「碰」，还在等的只能「吃」→ 立刻收工，不再等",
                RoundClaims.shouldStop(asked12, noRon, none, 5000,
                        new RoundClaims.Claim(RoundClaims.RANK_PON, 1), rankOfAsked, dist));
        check("已到手「杠」，还在等的只能「碰/吃」→ 立刻收工",
                RoundClaims.shouldStop(asked12, noRon, none, 5000,
                        new RoundClaims.Claim(RoundClaims.RANK_KAN, 2), rankOfAsked, dist));
        check("已到手「吃」，还在等的能「碰」→ 必须继续等",
                !RoundClaims.shouldStop(asked12, noRon, none, 5000,
                        new RoundClaims.Claim(RoundClaims.RANK_CHI, 1),
                        new HashMap<>(Map.of(1, RoundClaims.RANK_PON, 2, RoundClaims.RANK_CHI)),
                        dist));
        check("已到手「碰(远)」，还在等的能「碰(近)」→ 必须继续等（同级看座次）",
                !RoundClaims.shouldStop(asked12, noRon, none, 5000,
                        new RoundClaims.Claim(RoundClaims.RANK_PON, 2),
                        new HashMap<>(Map.of(1, RoundClaims.RANK_PON, 2, RoundClaims.RANK_CHI)),
                        dist));
        check("已到手「碰(近)」，还在等的只能「碰(远)」→ 立刻收工",
                RoundClaims.shouldStop(asked12, noRon, none, 5000,
                        new RoundClaims.Claim(RoundClaims.RANK_PON, 1),
                        new HashMap<>(Map.of(1, RoundClaims.RANK_CHI, 2, RoundClaims.RANK_PON)),
                        dist));
        check("已到手「碰」，还有人可能「荣和」→ 必须继续等",
                !RoundClaims.shouldStop(asked12, Arrays.asList(2), none, 5000,
                        new RoundClaims.Claim(RoundClaims.RANK_PON, 1),
                        new HashMap<>(Map.of(1, RoundClaims.RANK_CHI, 2, RoundClaims.RANK_RON)),
                        dist));
    }

    // ------------------------------------------------ 「吃」必须由服务端校验顺子
    /**
     * 「吃」的合法性：服务端必须**自己**校验顺子成立，而且客户端多给/少给牌都不能崩。
     *
     * <p>两条都是审计出来的真问题：
     * <ul>
     *   <li>曾经只按 {@code int[2]} 下标写、不查边界 —— 客户端给三张牌就 AIOOBE，
     *       异常冒到牌桌线程，整场半庄静默死亡（连 round_end 都不发）；</li>
     *   <li>曾经只查"手里有没有这两张"、不查是否连成顺子 —— 于是 1m + 5m 可以配 3m 吃，
     *       而 {@code Evaluator} 是按 {@code Meld.baseKind() + isRun()} 重建面子形状来算役的，
     *       这副假顺子会被当成真 1m2m3m 计分（一条报文就能改分）。</li>
     * </ul>
     */
    private static void chiValidationTests() {
        final int m3 = Tiles.id(2, 0);          // 被吃的 3m
        mahjong.game.Round r = newRound();
        r.hand[1].clear();
        r.hand[1].addAll(parse("1m2m4m5m1p2p1z2z"));

        check("吃：1m+2m 配 3m 成立", r.debugPickChiTiles(1, m3, Arrays.asList("1m", "2m")) != null);
        check("吃：2m+4m 配 3m 成立（3m 在中间）",
                r.debugPickChiTiles(1, m3, Arrays.asList("2m", "4m")) != null);
        check("吃：4m+5m 配 3m 成立（3m 在低端）",
                r.debugPickChiTiles(1, m3, Arrays.asList("4m", "5m")) != null);
        check("吃：1m+5m 不连 → 必须作废",
                r.debugPickChiTiles(1, m3, Arrays.asList("1m", "5m")) == null);
        check("吃：两张同 kind → 必须作废",
                r.debugPickChiTiles(1, m3, Arrays.asList("2m", "2m")) == null);
        check("吃：跨花色 → 必须作废",
                r.debugPickChiTiles(1, m3, Arrays.asList("1m", "2p")) == null);
        check("吃：字牌不能吃 → 必须作废",
                r.debugPickChiTiles(1, Tiles.id(27, 0), Arrays.asList("1z", "2z")) == null);
        check("吃：手里没有那张 → 必须作废",
                r.debugPickChiTiles(1, m3, Arrays.asList("6m", "7m")) == null);
        check("吃：给三张（曾经 AIOOBE 崩掉整桌）→ 必须作废",
                r.debugPickChiTiles(1, m3, Arrays.asList("1m", "2m", "3m")) == null);
        check("吃：给一张 → 必须作废",
                r.debugPickChiTiles(1, m3, Arrays.asList("1m")) == null);
        check("吃：给空列表 → 必须作废",
                r.debugPickChiTiles(1, m3, new ArrayList<>()) == null);

        int[] ids = r.debugPickChiTiles(1, m3, Arrays.asList("1m", "2m"));
        check("吃：取回的确实是手里那两张", ids != null && ids.length == 2
                && Tiles.kind(ids[0]) == 0 && Tiles.kind(ids[1]) == 1);
    }

    // ------------------------------------------------------------- 食替（服务端权威）

    /**
     * 食替（`docs/日本麻将.md` §食替）：吃/碰之后不能马上打出
     * 「所吃/所碰的那张」（**現物食替**）或「这副顺子里从手里拿出的那两张还能配成的
     * **另一副**顺子的第三张」（**筋食替**）。
     *
     * <p>这里钉住两层，两层原来都漏：
     * <ol>
     *   <li><b>禁打集合本身</b>：旧实现只算「较大那张 +1」这一侧，而牌种数组是**升序**的，
     *       于是「被吃的那张在顺子上边」（手牌 3m4m 吃 5m）永远漏判 2m；</li>
     *   <li><b>服务端校验</b>：旧实现只在**下发的选项**里过滤，出牌段只查「在手里 /
     *       立直只摸切」→ 一条手工报文就能食替（`PROTOCOL.md` §7 明文属非法动作）。</li>
     * </ol>
     */
    private static void kuikaeTests() {
        final int m1 = Tiles.parseKind("1m");
        final int m2 = Tiles.parseKind("2m");
        final int m3 = Tiles.parseKind("3m");
        final int m4 = Tiles.parseKind("4m");
        final int m5 = Tiles.parseKind("5m");
        final int m6 = Tiles.parseKind("6m");
        final int m7 = Tiles.parseKind("7m");
        final int m8 = Tiles.parseKind("8m");
        final int m9 = Tiles.parseKind("9m");

        // ---------- ① 禁打集合的内容：两侧都要算（这一对是同一个集合，旧实现只对了一半）
        Set<Integer> chiTop = RoundOptions.kuikaeForbidden(m5, m3, m4);   // 手牌 3m4m 吃 5m
        check("吃 5m（3m4m）：現物 5m 禁打", chiTop.contains(m5));
        check("吃 5m（3m4m）：筋 2m 禁打（旧实现漏判这一半）", chiTop.contains(m2));
        eq("吃 5m（3m4m）：禁打集合恰好 {2m,5m}", new java.util.TreeSet<>(chiTop).toString(),
                new java.util.TreeSet<>(Arrays.asList(m2, m5)).toString());

        Set<Integer> chiBottom = RoundOptions.kuikaeForbidden(m2, m3, m4);  // 手牌 3m4m 吃 2m
        eq("吃 2m（3m4m）：两侧对称，同样是 {2m,5m}",
                new java.util.TreeSet<>(chiBottom).toString(),
                new java.util.TreeSet<>(Arrays.asList(m2, m5)).toString());

        // 坎张吃没有第二个完成形（唯一的另一张就是被吃的那张本身）
        eq("坎张吃 5m（4m6m）：只禁現物 5m",
                new java.util.TreeSet<>(RoundOptions.kuikaeForbidden(m5, m4, m6)).toString(),
                new java.util.TreeSet<>(Arrays.asList(m5)).toString());
        // 边张吃：向下补越界，向上补就是被吃的那张
        eq("边张吃 3m（1m2m）：只禁現物 3m",
                new java.util.TreeSet<>(RoundOptions.kuikaeForbidden(m3, m1, m2)).toString(),
                new java.util.TreeSet<>(Arrays.asList(m3)).toString());
        // ⚠ 花色边界：9m 的"向上补"是 1p 的牌种号，绝不能越过花色边界混进来
        eq("边张吃 7m（8m9m）：不得把 1p 当成筋食替",
                new java.util.TreeSet<>(RoundOptions.kuikaeForbidden(m7, m8, m9)).toString(),
                new java.util.TreeSet<>(Arrays.asList(m7)).toString());
        check("字牌（吃不到）不炸：只返回現物",
                RoundOptions.kuikaeForbidden(Tiles.parseKind("1z"), m1, m2)
                        .equals(new LinkedHashSet<>(Arrays.asList(Tiles.parseKind("1z")))));

        // ---------- ② 出牌校验：服务端权威判据
        List<Integer> hand = parse("2m3m4m5m6m7m8m1p2p3p4p5p6p");   // 13 张
        int id2m = -1;
        int id3m = -1;
        int id5m = -1;
        int id6m = -1;
        for (int id : hand) {
            if (Tiles.kind(id) == m2) {
                id2m = id;
            } else if (Tiles.kind(id) == m3) {
                id3m = id;
            } else if (Tiles.kind(id) == m5) {
                id5m = id;
            } else if (Tiles.kind(id) == m6) {
                id6m = id;
            }
        }
        Set<Integer> fb = RoundOptions.kuikaeForbidden(m5, m3, m4);   // {2m, 5m}
        check("食替禁打：手里的 2m 被服务端拒绝",
                !Round.discardAllowed(hand, false, -1, fb, true, id2m));
        check("食替禁打：手里的 5m 被服务端拒绝",
                !Round.discardAllowed(hand, false, -1, fb, true, id5m));
        check("食替之外：手里的 3m 照常放行",
                Round.discardAllowed(hand, false, -1, fb, true, id3m));
        check("规则关掉食替禁止（kuikae=false）→ 同一张放行",
                Round.discardAllowed(hand, false, -1, fb, false, id2m));
        // 不在手里的牌（含 -1 "没解析出来"）一律拒绝
        final int id9s = Tiles.id(Tiles.parseKind("9s"), 0);
        check("不在手里的牌被拒（幽灵出牌）",
                !hand.contains(id9s) && !Round.discardAllowed(hand, false, -1, Set.of(), true, id9s));
        check("-1（牌码没解析出来）被拒",
                !Round.discardAllowed(hand, false, -1, Set.of(), true, -1));
        // 立直后只能摸切
        check("立直后打手里别的牌被拒",
                !Round.discardAllowed(hand, true, id6m, Set.of(), true, id3m));
        check("立直后摸切放行", Round.discardAllowed(hand, true, id6m, Set.of(), true, id6m));

        // ---------- ③ 同源性：下发选项 == 校验接受集合（同一把尺子）
        boolean sameRuler = true;
        final Set<Integer> none = Set.of();
        final Set<Integer> only2m = Set.of(m2);
        final Set<Integer> both = new LinkedHashSet<>(fb);
        final Set<Integer> many = Set.of(m2, m3, m4, m5, m6, m7, m8);
        for (Set<Integer> kinds : Arrays.asList(none, only2m, both, many)) {
            Set<Integer> listed = new LinkedHashSet<>();
            for (Object s : RoundOptions.discardChoices(hand, false, -1, kinds)) {
                listed.add(Tiles.parseKind((String) s));
            }
            for (int id : hand) {
                if (Round.discardAllowed(hand, false, -1, kinds, true, id)
                        != listed.contains(Tiles.kind(id))) {
                    sameRuler = false;
                }
            }
        }
        check("出牌校验与下发选项同源（4 种禁打集合 × 13 张手牌）", sameRuler);

        // ---------- ④ 红证 / 非空转：**恶意策略**在真实对局里故意食替，服务端必须拒绝
        // 策略只在"自家回合且手里确实有禁张"时出手（其余一律返回 null → 交给内置 teacher），
        // 所以对局照常推进（teacher 本来就会吃碰），而每一次出手都是一条"手工报文"。
        final int[] attempts = {0};
        final int[] honored = {0};                 // 服务端照打出去的次数（必须为 0）
        final Map<Integer, Integer> pendingCheat = new HashMap<>();
        mahjong.ai.Policy evil = d -> {
            if ("claim".equals(d.kind)) {
                // 主动吃/碰：食替只可能出现在「鸣牌之后必须出牌」那一步，不主动鸣就永远造不出来。
                // 其余（含荣和）一律 pass，保持对局干净。
                for (Map<String, Object> o : d.options) {
                    Object ty = o.get("type");
                    if ("chi".equals(ty)) {
                        List<Object> sets = Json.list(o, "sets");
                        if (sets != null && !sets.isEmpty()) {
                            return Json.obj("type", "chi", "tiles",
                                    new ArrayList<>(Json.asArr(sets.get(0))));
                        }
                    } else if ("pon".equals(ty)) {
                        return Json.obj("type", "pon");
                    }
                }
                return Json.obj("type", "pass");
            }
            if (!"turn".equals(d.kind) || d.round.riichi[d.seat()]) {
                return null;                       // 立直那一路只下发摸切那一张，不是食替判据
            }
            Set<Integer> listed = new LinkedHashSet<>();
            for (Map<String, Object> o : d.options) {
                if ("discard".equals(o.get("type"))) {
                    for (String s : Json.strList(o, "tiles")) {
                        listed.add(Tiles.parseKind(s));
                    }
                }
            }
            if (listed.isEmpty()) {
                return null;                       // 全是禁张（极端牌型）：兜底那一侧另有断言
            }
            final List<Integer> h = d.round.hand[d.seat()];
            for (int id : h) {
                if (!listed.contains(Tiles.kind(id))) {
                    attempts[0]++;
                    pendingCheat.put(d.seat(), Tiles.kind(id));
                    return Json.obj("type", "discard", "tile", Tiles.toStr(id), "tsumogiri", false);
                }
            }
            return null;
        };
        for (long seed : new long[]{20260901L, 20260902L}) {
            Table ct = new Table("CHEAT" + seed, "食替作弊桌", Rules.defaults());
            ct.botDelayMs = 0;
            ct.roundDelayMs = 0;
            ct.debugDeterministicSeed = true;
            ct.debugMaxHands = PROBE_HANDS;
            ct.seedBase = seed;
            for (int i = 0; i < 4; i++) {
                ct.policy[i] = evil;
                ct.addBot(i);
            }
            ct.debugEventTap = (recipient, ev) -> {
                if (recipient != -1 || !"discard".equals(Json.str(ev, "ev", ""))) {
                    return;
                }
                Integer cheat = pendingCheat.remove((Integer) Json.i(ev, "seat", -1));
                if (cheat != null && Tiles.parseKind(Json.str(ev, "tile", "")) == cheat) {
                    honored[0]++;                  // 服务端把禁张打出去了 —— 这就是 S-49 的红证
                }
            };
            ct.playGame();
        }
        System.out.println("  [覆盖] 食替：恶意策略在真实对局里造出 " + attempts[0] + " 次食替机会，"
                + "其中被服务端照打 " + honored[0] + " 次（必须为 0）");
        check("恶意食替被服务端拒绝（" + attempts[0] + " 次尝试 / " + honored[0] + " 次被照打）",
                attempts[0] > 0 && honored[0] == 0);
    }

    // ------------------------------------------------- 多家荣和 / 头跳 / 三家和了

    /**
     * 一张舍张同时被多家荣和时的三条口径（`docs/日本麻将.md` §头跳 L232-236、§和牌 L794）：
     * <ol>
     *   <li>**头跳**：只认离放铳者最近的那家（M.League）；</li>
     *   <li>**三家和了**：三家**同时**荣和 → 中途流局（《天凤》）；</li>
     *   <li>**二家荣和成立**时（《雀魂》/《天凤》）：立直棒与**本场加点**只归最近那家。</li>
     * </ol>
     *
     * <p>为什么必须**定向摆牌**：三家同时听同一张在随机模拟里几乎撞不到（撞到也不可复现），
     * 而这条规则的三种结论全由"同时有几家能和"决定。所以这里直接摆三家
     * 「役牌暗刻 + 单骑 5p」的听牌形，用 `debugClaimOutcome` 问一次仲裁结论 ——
     * 三种预设 + 两个开关同时打开，一共五格真值表，逐格钉住。
     */
    private static void multiRonTests() {
        eq("三家和了的流局原因码", YakuCodes.reasonOf("三家和了"), "triple_ron");

        // 头跳（M.League）：三家都能荣和 → 只认最近的一家
        eq("头跳（M.League）：三家荣和只认最近一家",
                ronOutcome(preset("mleague"), true, true, true), "ron:[1]");
        // 三家和了（天凤）：三家同时荣和 → 流局；二家荣和**不**流局
        eq("三家和了（天凤）：三家同时荣和 → 流局",
                ronOutcome(preset("tenhou"), true, true, true), "abort:三家和了");
        eq("三家和对二家荣和不生效（天凤允许二家和了）",
                ronOutcome(preset("tenhou"), true, true, false), "ron:[1, 2]");
        eq("单家荣和照常（天凤）", ronOutcome(preset("tenhou"), true, false, false), "ron:[1]");
        // 《雀魂》：既无头跳也无三家和了 → 三家全额结算
        eq("《雀魂》：无头跳无三家和了 → 三家都成立",
                ronOutcome(preset("majsoul"), true, true, true), "ron:[1, 2, 3]");
        // 两个开关同时打开（自定义规则）时**头跳优先**：头跳一旦归约，"三家同时"就不存在了
        Rules both = preset("majsoul");
        both.headBump = true;
        both.sanchaAbort = true;
        eq("头跳与三家和了同时打开 → 头跳优先", ronOutcome(both, true, true, true), "ron:[1]");

        // ---------- 供託与本场加点的归属（文档 §和牌 L794）----------
        final int honba = 2;
        final Rules ms = preset("majsoul");                 // 无头跳 → 二家都成立，才看得到归属
        int[] two = ronDeltas(ms, honba, 0, true, true, false);
        int[] one1 = ronDeltas(ms, honba, 0, true, false, false);
        int[] one2 = ronDeltas(ms, honba, 0, false, true, false);
        // 二家荣和时放铳者**只多付一次** 300×本场（旧实现每家各收一次 → 这里会是 0）
        eq("多家荣和：放铳者只多付一次本场加点",
                two[0] - (one1[0] + one2[0]), 300 * honba);
        // 第二家收不到本场 —— 与"同一手牌在 0 本场时单独荣和"逐位相同；
        // 反向对照：它若**单独**荣和（就是最近那家）则照收 300×本场，可见差别确实来自归属。
        eq("多家荣和：第二家只拿基本点，不收本场（= 0 本场时的同一手）",
                two[2], ronDeltas(ms, 0, 0, false, true, false)[2]);
        eq("对照：同一手单独荣和（最近那家）时会收到本场", one2[2] - two[2], 300 * honba);
        eq("多家荣和：最近那家照常收本场", two[1], one1[1]);
        check("多家荣和结算仍然零和（无供託时）",
                two[0] + two[1] + two[2] + two[3] == 0);

        // 立直棒：两根都归最近那家，第二家一根不拿
        int[] sticks2 = ronDeltas(ms, honba, 2, true, true, false);
        int[] sticks1 = ronDeltas(ms, honba, 2, true, false, false);
        eq("多家荣和：立直棒全归最近那家（第二家不加）", sticks2[2], two[2]);
        eq("多家荣和：最近那家多拿的是全部供託", sticks2[1] - two[1], 2000);
        eq("单家荣和拿全部供託", sticks1[1] - one1[1], 2000);

        // ---------- S-53：每条 agari 报文里的 `scores_after` 都必须是**最终**分数 ----------
        // 旧实现边结算边发 → 多家荣和时先发的那条是"中途快照"（还没有后面几家的收支）。
        List<Map<String, Object>> agariEvents = new ArrayList<>();
        Table t53 = claimTable(ms);
        t53.debugEventTap = (recipient, ev) -> {
            if (recipient == -1 && "agari".equals(Json.str(ev, "ev", ""))) {
                agariEvents.add(new HashMap<>(ev));
            }
        };
        Round r53 = new Round(t53, 0, 1, honba, 0, new int[]{25000, 25000, 25000, 25000}, 0,
                20260901L);
        List<Integer> win53 = new ArrayList<>();
        for (int s : new int[]{1, 2}) {
            r53.hand[s].addAll(parse(new String[]{
                    "2z2z2z1m1m1m2m2m2m3m3m3m5p",
                    "3z3z3z4m4m4m5m5m5m6m6m6m5p"}[s - 1]));
            win53.add(s);
        }
        int[] d53 = r53.debugRonDeltas(win53, 0, Tiles.id(Tiles.parseKind("5p"), 2));
        check("两家都真有收支（否则下面那条断言是空转）", d53[1] > 0 && d53[2] > 0);
        eq("两家荣和发出 2 条 agari 报文", agariEvents.size(), 2);
        final String finalScores = Json.intList(r53.scores).toString();
        for (int i = 0; i < agariEvents.size(); i++) {
            eq("第 " + (i + 1) + " 条 agari 的 scores_after 就是最终分数（不是中途快照）",
                    String.valueOf(agariEvents.get(i).get("scores_after")), finalScores);
        }
    }

    /**
     * 摆好"哪几家单骑 5p"，问一次 5p 舍张的鸣牌仲裁结论。
     *
     * <p>三家的听牌形都是「本家自风的役牌暗刻 + 单骑 5p」（东1局：1=南 2=西 3=北），
     * 所以三家都真有役；不需要荣和的那家只把末尾的 `5p` 换成 `9p`（改听 9p，和不了 5p），
     * 于是"能不能荣和"只由参数决定，牌数也都在 4 张以内。
     */
    private static String ronOutcome(Rules rules, boolean w1, boolean w2, boolean w3) {
        final String[] waits = {
                "2z2z2z1m1m1m2m2m2m3m3m3m5p",
                "3z3z3z4m4m4m5m5m5m6m6m6m5p",
                "4z4z4z7m7m7m8m8m8m9m9m9m5p",
        };
        Table t = claimTable(rules);
        Round r = newRoundLike(t);
        final boolean[] want = {false, w1, w2, w3};
        for (int s = 1; s <= 3; s++) {
            String h = waits[s - 1];
            r.hand[s].addAll(parse(want[s] ? h : h.substring(0, h.length() - 2) + "9p"));
        }
        return r.debugClaimOutcome(0, Tiles.id(Tiles.parseKind("5p"), 1));
    }

    /** 跑一次荣和结算，返回四家点数增减。{@code winners} 按"距放铳者由近到远"（= 座位 1→3）。 */
    private static int[] ronDeltas(Rules rules, int honba, int sticks,
                                   boolean w1, boolean w2, boolean w3) {
        final String[] waits = {
                "2z2z2z1m1m1m2m2m2m3m3m3m5p",
                "3z3z3z4m4m4m5m5m5m6m6m6m5p",
                "4z4z4z7m7m7m8m8m8m9m9m9m5p",
        };
        Table t = claimTable(rules);
        Round r = new Round(t, 0, 1, honba, 0, new int[]{25000, 25000, 25000, 25000}, sticks,
                20260901L);
        final boolean[] want = {false, w1, w2, w3};
        List<Integer> winners = new ArrayList<>();
        for (int s = 1; s <= 3; s++) {
            String h = waits[s - 1];
            if (want[s]) {
                r.hand[s].addAll(parse(h));
                winners.add(s);
            } else {
                r.hand[s].addAll(parse(h.substring(0, h.length() - 2) + "9p"));
            }
        }
        return r.debugRonDeltas(winners, 0, Tiles.id(Tiles.parseKind("5p"), 1));
    }

    /**
     * 多家荣和用的牌桌：四家都是机器人 + 一条**只认荣和**的哑策略
     * （其它一律 `pass`，保证结论只由"几家能荣和"决定）。
     */
    private static Table claimTable(Rules rules) {
        Table t = new Table("MR", "多家荣和桌", rules);
        t.botDelayMs = 0;
        t.roundDelayMs = 0;
        for (int i = 0; i < 4; i++) {
            t.addBot(i);
            t.policy[i] = d -> {
                for (Map<String, Object> o : d.options) {
                    if ("ron".equals(o.get("type"))) {
                        return Json.obj("type", "ron");
                    }
                }
                return Json.obj("type", "pass");
            };
        }
        return t;
    }

    /** 与 {@link #newRound} 同样的局面（座位 0 是庄），但用给定的牌桌。 */
    private static Round newRoundLike(Table t) {
        return new Round(t, 0, 1, 0, 0, new int[]{25000, 25000, 25000, 25000}, 0, 20260901L);
    }

    // ------------------------------------------------------------- 燕返（立直宣言牌放铳）

    /**
     * 燕返：荣和的正是**首次放置的立直宣言牌** → 立直不成立、那 1000 点退回
     * （`docs/日本麻将.md` §立直 L893：「《雀魂》中，立直宣言牌放铳（燕返）的情况下，
     * 认为立直不成立，不加收 1000 点。《天凤》和 M.League 也采用相同的规定」）。
     *
     * <p>旧实现是 `doRiichi()` 在宣言牌落地**之前**就扣 1000 并 `sticks++`，
     * 随后 `agariRon` 把供託全给和牌者 —— 放铳者白扣、和牌者白收，每次宣言被荣和都触发。
     *
     * <p>这里的状态**由生产的 `doRiichi` 造**（不是测试自己摆的账面），
     * 结算也走生产的 `agariRon`（经 `debugRonDeltas`），两侧都是真代码。
     */
    private static void tsubameTests() {
        final int k5p = Tiles.parseKind("5p");
        final int decl = Tiles.id(k5p, 3);          // 座位 1 手里那张 5p（宣言牌）

        // ---------- ① 燕返：荣和的正是刚宣告的立直宣言牌 ----------
        Round a = tsubameTable(0);
        a.debugDoRiichi(1, decl);
        eq("立直宣言后自家扣 1000", a.scores[1], 24000);
        eq("立直宣言后供託 +1", a.sticks, 1);
        int[] da = ronOn5p(a, true);
        eq("燕返：立直被撤销", a.riichi[1], false);
        eq("燕返：一発标志一并清掉", a.ippatsu[1], false);
        eq("燕返：供託退回、不许带进下一局", a.sticks, 0);

        // ---------- ② 对照：同一张牌、同一个和牌者，但**不是**宣言牌 → 立直照样成立 ----------
        Round b = tsubameTable(0);
        b.debugDoRiichi(1, Tiles.id(Tiles.parseKind("1m"), 3));   // 宣言的是 1m
        int[] db = ronOn5p(b, false);
        check("对照：荣和普通舍张时立直仍然成立", b.riichi[1]);
        eq("对照：供託不退回", b.sticks, 1);
        // 两条互为镜像的判据 —— 这就是"那 1000 点"的全部去向
        eq("燕返让放铳者**少付 1000**", da[1] - db[1], 1000);
        eq("燕返让和牌者**少收 1000**（那根立直棒不存在了）", da[2] - db[2], -1000);
        eq("燕返后立直者比对照多 1000（差的就是退回来的供託）", a.scores[1] - b.scores[1], 1000);

        // ---------- ③ 上一局留下的供託不动，只退本次宣言那一根 ----------
        Round c = tsubameTable(1);                  // 构造给 1 根（= 上一局留下的），宣言再 +1 → 2
        c.debugDoRiichi(1, decl);
        eq("燕返前供託 = 2（上一局留下的 1 根 + 本次宣言的 1 根）", c.sticks, 2);
        int[] dc = ronOn5p(c, true);
        eq("燕返只退本次那一根（上一局留下的仍是供託）", c.sticks, 1);
        eq("燕返退回的仍是 1000（与 ① 相同）", dc[1] - da[1], 0);
        eq("燕返时和牌者照样收走留下的那根（+1000）", dc[2] - da[2], 1000);
    }

    /** 燕返用的局面：座位 1 = 立直宣言者，座位 2 = 单骑 5p 的荣和者（自风役牌）。 */
    private static Round tsubameTable(int sticks) {
        Table t = claimTable(Rules.defaults());
        Round r = new Round(t, 0, 1, 0, 0, new int[]{25000, 25000, 25000, 25000}, sticks,
                20260901L);
        r.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p"));
        r.hand[2].addAll(parse("3z3z3z4m4m4m5m5m5m6m6m6m5p"));
        return r;
    }

    /** 座位 2 荣和座位 1 打出的 5p；`riichiDiscard` = 这张是不是刚宣告的立直宣言牌。 */
    private static int[] ronOn5p(Round r, boolean riichiDiscard) {
        return r.debugRonDeltas(new ArrayList<>(Arrays.asList(2)), 1,
                Tiles.id(Tiles.parseKind("5p"), 2), riichiDiscard);
    }

    // ------------------------------------------------- 终局：余棒分配 / 和了止·听牌止 / 一位必要点数

    /**
     * 批次三的三条终局口径（`docs/日本麻将.md` L116/L143/L145/L157 + M.League 原文）：
     * <ol>
     *   <li>**终局余棒**：流局结束时立直棒归 1 位；**并列第一**时由相关者均分，
     *       按 100 点为单位、尾数归更接近起家者（原文 3 人 1000 → 400/300/300、2000 → 800/600/600）；</li>
     *   <li>**和了止 / 听牌止**：All Last 庄家达到一位必要点数且为 1 位时，和了**或荒牌流局庄家听牌**都结束；</li>
     *   <li>**一位必要点数**：延长战的门槛是它，**不是精算基准**（《雀魂》= 30000 vs 25000）。</li>
     * </ol>
     */
    private static void endGameTests() {
        // ---------- ① 终局余棒：与 M.League 原文逐字对照 ----------
        int[] s1 = {31000, 29000, 20000, 20000};
        int[] a1 = RoundScoring.endGameSticks(s1, 1);
        eq("1 位只有一家 → 全部余棒归它", a1[0], 1000);
        eq("余棒只给 1 位（其余三家 0）", a1[1] + a1[2] + a1[3], 0);

        // 3 人并列第一：原文「将 1000 点分为 400、300、300」
        int[] s3 = {30000, 30000, 30000, 10000};
        int[] a3 = RoundScoring.endGameSticks(s3, 1);
        eq("3 人并列 1 位、1 根：更接近起家者拿 400", a3[0], 400);
        eq("3 人并列 1 位、1 根：其余各 300", a3[1] + a3[2], 600);
        eq("3 人并列：两根 → 800/600/600", RoundScoring.endGameSticks(s3, 2)[0], 800);
        eq("3 人并列：两根其余各 600",
                RoundScoring.endGameSticks(s3, 2)[1] + RoundScoring.endGameSticks(s3, 2)[2], 1200);
        // 2 人并列：1000 能整除 → 500/500（尾数为 0）
        int[] s2 = {25000, 10000, 25000, 10000};
        int[] a2 = RoundScoring.endGameSticks(s2, 1);
        eq("2 人并列 1 位 → 均分 500", a2[0], 500);
        eq("2 人并列的另一家同样 500", a2[2], 500);
        // 尾数归**更接近起家**的那家：并列的是座次 1/2/3，额外那 200 点应给座次 1
        //（而不是无脑给"座次 0"或别家 —— 座次 0 在这组里根本没并列）
        int[] s123 = {10000, 30000, 30000, 30000};
        int[] a123 = RoundScoring.endGameSticks(s123, 2);
        eq("余棒尾数归更接近起家者（并列 1/2/3，座次 1 拿 800 而非 600）", a123[1], 800);
        eq("并列的其余两家各 600", a123[2] + a123[3], 1200);
        eq("没并列的座次 0 一分不加", a123[0], 0);
        // 守恒：加出去的总量恒等于 sticks × 1000
        int sum = 0;
        for (int v : a3) {
            sum += v;
        }
        eq("余棒分配零和（总数 = 根数 × 1000）", sum, 1000);
        eq("根数为 0 时不动分数", RoundScoring.endGameSticks(s3, 0)[0], 0);

        // ---------- ② 和了止 / 听牌止（三个条件一起看）----------
        final Rules th = preset("tenhou");                 // agariyame=true, requiredPoints=30000
        final int[] top = {31000, 20000, 20000, 20000};    // 庄家（座位 0）= 1 位且达 30000
        final boolean[] tenpaiDealer = {true, false, false, false};
        final boolean[] notenDealer = {false, true, true, true};
        check("和了止：庄家和了 + 1 位 + 达一位必要点数 → 结束",
                RoundScoring.stopAtAllLast(0, true, false, tenpaiDealer, top, th));
        check("听牌止：荒牌流局且**庄家听牌** + 1 位 + 达门槛 → 结束",
                RoundScoring.stopAtAllLast(0, false, false, tenpaiDealer, top, th));
        check("荒牌流局但庄家**不听** → 不结束（轮庄）",
                !RoundScoring.stopAtAllLast(0, false, false, notenDealer, top, th));
        check("闲家和了 → 谈不上和了止",
                !RoundScoring.stopAtAllLast(0, false, false, notenDealer, top, th));
        check("流局满贯**不算**听牌止（原文只说荒牌流局）",
                !RoundScoring.stopAtAllLast(0, false, true, tenpaiDealer, top, th));
        // 没达到一位必要点数 / 不是 1 位
        check("庄家只有 29000（未达一位必要点数）→ 不结束",
                !RoundScoring.stopAtAllLast(0, true, false, tenpaiDealer,
                        new int[]{29000, 29000, 21000, 21000}, th));
        check("庄家达门槛但不是 1 位 → 不结束",
                !RoundScoring.stopAtAllLast(0, true, false, tenpaiDealer,
                        new int[]{31000, 40000, 10000, 10000}, th));
        check("M.League 预设关掉和了止 → 永远不中止",
                !RoundScoring.stopAtAllLast(0, true, false, tenpaiDealer, top, preset("mleague")));

        // ---------- ③ 一位必要点数：延长战门槛用 requiredPoints，不是精算基准 ----------
        // ⚠ 必须挑**两个数不一样**的预设才验得出来：《雀魂》= 一位必要点数 30000 + 精算基准 25000；
        //   《天凤》两者都是 30000（那一组换回 returnScore 也照样通过 → 等于没测）。
        Rules west = preset("majsoul");
        west.westExtension = true;
        eq("门槛取自一位必要点数（不是精算基准）", west.requiredPoints, 30000);
        eq("同一预设的精算基准是另一个数", west.returnScore, 25000);
        check("1 位 27000（> 精算基准 25000 但 < 一位必要点数 30000）→ 继续西入",
                RoundScoring.keepPlayingWest(west, 27000, 2, 1));
        check("1 位 30000 → 结束（达到一位必要点数）",
                !RoundScoring.keepPlayingWest(west, 30000, 2, 1));
        check("没开延长战开关 → 结束",
                !RoundScoring.keepPlayingWest(preset("tenhou"), 27000, 2, 1));
        // S-54：场风上限 = lastWind + 1（东风战 → 南入、半庄 → 西入），**没有北入**
        check("半庄：西入（场风 2）允许", RoundScoring.keepPlayingWest(west, 10000, 2, 1));
        check("半庄：**北入（场风 3）不允许**（旧实现写死 nw<=3 会放行）",
                !RoundScoring.keepPlayingWest(west, 10000, 3, 1));
        check("东风战：南入（场风 1）允许", RoundScoring.keepPlayingWest(west, 10000, 1, 0));
        check("东风战：**西场（场风 2）不允许**（东风战只延长到南场）",
                !RoundScoring.keepPlayingWest(west, 10000, 2, 0));
        check("《雀魂》预设 = 一位必要点数 30000 而精算基准 25000（两个字段不是一回事）",
                preset("majsoul").requiredPoints == 30000 && preset("majsoul").returnScore == 25000);

        // ---------- ④ 默认思考时间统一为 20+5（额外 20s + 每巡 5s）----------
        Rules def = Rules.defaults();
        eq("默认每巡基本时长 = 5000ms", def.thinkingBaseMs, 5000);
        eq("默认额外时长总额 = 20000ms", def.thinkingBankMs, 20000);
        eq("默认 preset 是 mleague", def.preset, "mleague");
        eq("三套预设都不改思考时间（都继承同一组默认值）",
                preset("majsoul").thinkingBaseMs + "/" + preset("majsoul").thinkingBankMs,
                "5000/20000");
        eq("旧字段 thinking_ms 与 base 同步", def.thinkingMs, def.thinkingBaseMs);
        // 钳制仍然挡着荒谬值（否则牌桌线程真的会等约 24 天）
        Rules crazy = Rules.fromJson(Json.obj("thinking_base_ms", 2147483647,
                "thinking_bank_ms", 2147483647, "required_points", 99999999));
        check("思考时间被钳到合理区间", crazy.thinkingBaseMs == 60000 && crazy.thinkingBankMs == 600000);
        eq("一位必要点数也被钳制", crazy.requiredPoints, 1000000);

        // ---------- ⑤ 实局路径（非空转）：1 小局就以**流局**结束、桌上留着 1 根立直棒 ----------
        // 种子 19 是**实测定出来的**（4 机器人、debugMaxHands=1、确定性种子）：那一局以荒牌流局
        // 结束，三家并列 1 位、桌上 1 根立直棒 → 正是"流局结束 + 并列 1 位"那一支。
        // 最后一条 round_end 的分数就是**分配余棒之前**的分数，两者之差 = 本次分掉的余棒。
        RoundScoring.debugResetCounts();
        Table et = new Table("ENDSTICK", "终局余棒桌", Rules.defaults());
        et.botDelayMs = 0;
        et.roundDelayMs = 0;
        et.debugDeterministicSeed = true;
        et.debugMaxHands = 1;
        et.seedBase = 19;
        final List<Integer> beforeSticks = new ArrayList<>();
        final int[] sticksOnTable = {0};
        et.debugEventTap = (recipient, ev) -> {
            if (recipient == -1 && "round_end".equals(Json.str(ev, "ev", ""))) {
                beforeSticks.clear();
                for (Object o : Json.list(ev, "scores")) {
                    beforeSticks.add(((Number) o).intValue());
                }
                sticksOnTable[0] = Json.i(Json.map(ev, "round"), "riichi_sticks", 0);
            }
        };
        for (int i = 0; i < 4; i++) {
            et.addBot(i);
        }
        et.playGame();
        check("终局余棒分配在实局路径上被调用（不是死代码）：" + RoundScoring.debugCallCounts(),
                RoundScoring.debugCallCounts().contains("endSticks=1"));
        eq("实局：最后一条 round_end 带回了分配前的分数", beforeSticks.size(), 4);
        eq("实局：这一局桌上正好 3 根立直棒（三家立直后荒牌流局）", sticksOnTable[0], 3);
        int totalAfter = 0;
        int diffSum = 0;
        final List<Integer> diff = new ArrayList<>();
        for (int i = 0; i < 4; i++) {
            totalAfter += et.seat(i).score;
            final int d = et.seat(i).score - beforeSticks.get(i);
            diff.add(d);
            diffSum += d;
        }
        eq("实局：余棒分配后总分守恒", totalAfter, 100000);
        eq("实局：分掉的余棒总量 = 桌上根数 × 1000", diffSum, sticksOnTable[0] * 1000);
        List<Integer> sortedDiff = new ArrayList<>(diff);
        sortedDiff.sort(java.util.Comparator.reverseOrder());
        // 3 根 / 3 家并列 1 位：3000 能被 3 整除到 100 点 → 每家 1000（没有尾数）。
        // ⚠ 尾数那一支（1 根 → 400/300/300、2 根 → 800/600/600）在 ①② 里逐字钉着。
        eq("实局：3 根余棒 / 3 家并列 → 每家 1000", sortedDiff.toString(),
                Arrays.asList(1000, 1000, 1000, 0).toString());
        // 同一份"分配前分数 + 根数"喂给判据，必须逐位一致（证明实局确实走的是它）
        int[] before = new int[4];
        for (int i = 0; i < 4; i++) {
            before[i] = beforeSticks.get(i);
        }
        eq("实局余棒分配与判据逐位一致", diff.toString(),
                Json.intList(RoundScoring.endGameSticks(before, sticksOnTable[0])).toString());
    }

    // ---------------------------------------- 古役 / 役满复合 / 抢杠（下一轮审计）

    /**
     * 下一轮审计里的四项（AUDIT S-57 / S-59 / S-60 / S-61）。都只在 `rules.koyaku` 打开时可见
     * （抢杠除外，它是一般役），所以每条断言都**显式打开古役**再验。
     */
    private static void koyakuAndChankanTests() {
        final Rules koy = preset("mleague");
        koy.koyaku = true;
        final Rules koyD = preset("majsoul");          // doubleYakuman = true
        koyD.koyaku = true;
        final int k5p = Tiles.parseKind("5p");

        // ---------- S-59：不同役满可以复合 → 天和 / 地和 + 国士十三面 = 2 倍 ----------
        final String kokushi14 = "1m9m1p9p1s9s1z1z2z3z4z5z6z7z";
        Evaluator.HandScore kTenhou = evalKoyaku(kokushi14, new ArrayList<>(), "1z", true, koy, "tenhou");
        check("国士十三面成立", hasYaku(kTenhou, "国士无双十三面"));
        check("天和与国士**复合**（旧实现白丢天和）", hasYaku(kTenhou, "天和"));
        eq("天和 + 国士十三面 = 2 倍役满", kTenhou.yakuman, 2);
        Evaluator.HandScore kChiihou = evalKoyaku(kokushi14, new ArrayList<>(), "1z", false, koy, "chiihou");
        check("地和与国士复合", hasYaku(kChiihou, "地和"));
        eq("地和 + 国士十三面 = 2 倍役满", kChiihou.yakuman, 2);
        // 对照：没有天和/地和的同一手牌仍然只 1 倍（证明上面多出来的那 1 倍来自复合）
        eq("同一手牌无天和/地和 → 只 1 倍",
                evalKoyaku(kokushi14, new ArrayList<>(), "1z", true, koy, "").yakuman, 1);

        // ---------- 《雀魂》特有：天和时国士无双视作国士无双十三面（文档 L1149）----------
        // 同一手牌（13 种幺九 + 1z 一对）换一个和了牌就换了听牌形：
        //   · 和 1z → 和牌前 13 张含全部 13 种 → **十三面听**；
        //   · 和 7z → 和牌前手里只有 12 种（1z 是雀头）→ **国士无双**（单骑）。
        final String kokushi14b = "1m9m1p9p1s9s1z1z2z3z4z5z6z7z";
        final Rules msK = preset("majsoul");             // kokushiTenhou13 = true, doubleYakuman = true
        final Rules thK = preset("tenhou");              // 同为两倍役满规则，但**没有**这条
        Evaluator.HandScore msSingle = evalKoyaku(kokushi14b, new ArrayList<>(), "7z", true, msK, "tenhou");
        check("《雀魂》天和 + 国士无双 → 视作**国士无双十三面**",
                hasYaku(msSingle, "国士无双十三面") && !hasYaku(msSingle, "国士无双"));
        // 升级后这一手 = 十三面 2 倍 + 天和 1 倍 = **3 倍**（不同役满可以复合，S-59）
        eq("《雀魂》：十三面 2 倍（不再是国士无双 1 倍）", reportedHan(msSingle, "国士无双十三面"), 26);
        eq("《雀魂》：天和照旧复合", reportedHan(msSingle, "天和"), 13);
        eq("《雀魂》：合计 3 倍役满", msSingle.yakuman, 3);
        // 对照一：《天凤》同一条规则集里没有这一条 → 还是国士无双（1 倍 + 天和 1 倍 = 2 倍）
        Evaluator.HandScore thSingle = evalKoyaku(kokushi14b, new ArrayList<>(), "7z", true, thK, "tenhou");
        check("《天凤》同样的牌 → 仍是国士无双（不升级）",
                hasYaku(thSingle, "国士无双") && !hasYaku(thSingle, "国士无双十三面"));
        eq("《天凤》：合计 2 倍役满（国士无双 1 + 天和 1）", thSingle.yakuman, 2);
        // 对照二：只有**天和**算，**地和**不算（原文只写天和）——
        // 同一手牌、同一个《雀魂》规则集，只把"天和"换成"地和"，就差这一级
        Evaluator.HandScore msChiihou = evalKoyaku(kokushi14b, new ArrayList<>(), "7z", false, msK, "chiihou");
        check("《雀魂》**地**和 + 国士无双 → **不**升级（原文只说天和）",
                hasYaku(msChiihou, "国士无双") && !hasYaku(msChiihou, "国士无双十三面"));
        eq("《雀魂》地和那一手 = 2 倍（国士无双 1 + 地和 1）", msChiihou.yakuman, 2);
        // 对照三：本来就是十三面听时不会重复计（还是一个役种）
        Evaluator.HandScore msAlready = evalKoyaku(kokushi14b, new ArrayList<>(), "1z", true, msK, "tenhou");
        eq("本来就是十三面 → 仍然只报一个役满役",
                (hasYaku(msAlready, "国士无双十三面") ? 1 : 0) + (hasYaku(msAlready, "国士无双") ? 1 : 0), 1);
        eq("本来就是十三面 → 3 倍（十三面 2 + 天和 1，与升级后相同）", msAlready.yakuman, 3);
        // 对照四：M.League 预设也不采用这条
        check("M.League 预设不采用（= false）", !preset("mleague").kokushiTenhou13);
        check("三套预设里只有《雀魂》打开", preset("majsoul").kokushiTenhou13
                && !preset("tenhou").kokushiTenhou13 && !preset("mleague").kokushiTenhou13);


        // ---------- S-60①：一色三顺**副露也成立**（副露减一番）----------
        List<Meld> issMelds = new ArrayList<>();
        issMelds.add(chi("2m", "3m", "4m"));
        Evaluator.HandScore issOpen = evalKoyaku("2m3m4m2m3m4m6m7m8m8s8s", issMelds, "4m", false, koy, "");
        check("一色三顺：副露型也成立（旧实现整块在 menzen 里 → 永远拿不到）",
                hasYaku(issOpen, "一色三顺"));
        eq("一色三顺：副露减一番 → 2 番", reportedHan(issOpen, "一色三顺"), 2);
        check("一色三顺成立时不计一杯口（上位替代）", !hasYaku(issOpen, "一杯口"));
        Evaluator.HandScore issMenzen = evalKoyaku("2m3m4m2m3m4m2m3m4m6m7m8m8s8s", new ArrayList<>(),
                "8s", false, koy, "");
        // 门前时同一手牌还能读成「三连刻 + 三暗刻」= 4 番 > 一色三顺 3 番 ——
        // 文档 §古役 明确要求**门前时用高点法取高者**，所以这里不该出现一色三顺。
        check("门前同一手牌由高点法取更高的一套（三连刻 + 三暗刻 > 一色三顺）",
                hasYaku(issMenzen, "三连刻") && !hasYaku(issMenzen, "一色三顺"));
        eq("门前那套里的三连刻 2 番", reportedHan(issMenzen, "三连刻"), 2);

        // ---------- S-60②：十二落抬含**明杠**、不含**暗杠** ----------
        List<Meld> fourOpen = new ArrayList<>();
        fourOpen.add(chi("1m", "2m", "3m"));
        fourOpen.add(chi("4m", "5m", "6m"));
        fourOpen.add(pon("7z"));
        fourOpen.add(kan("daiminkan", "9s"));           // 明杠 → 算
        check("十二落抬：4 副明面子（含明杠）单骑 → 成立",
                hasYaku(evalKoyaku("5s5s", fourOpen, "5s", false, koy, ""), "十二落抬"));
        List<Meld> withAnkan = new ArrayList<>(fourOpen);
        withAnkan.set(3, kan("ankan", "9s"));           // 换成暗杠 → 不算
        check("十二落抬：含**暗杠** → 不成立",
                !hasYaku(evalKoyaku("5s5s", withAnkan, "5s", false, koy, ""), "十二落抬"));

        // ---------- S-61①：一筒摸月 / 九筒捞鱼**取代**海底摸月 / 河底捞鱼 ----------
        List<Meld> twoChi = new ArrayList<>();
        twoChi.add(chi("2m", "3m", "4m"));
        twoChi.add(chi("5m", "6m", "7m"));
        Evaluator.HandScore iipin = evalKoyaku("2p3p4p5p6p7p1p1p", twoChi, "1p", true, koy, "haitei");
        check("一筒摸月成立", hasYaku(iipin, "一筒摸月"));
        check("一筒摸月**取代**海底摸月（旧实现两者都加 → 6 番跳满）",
                !hasYaku(iipin, "海底摸月"));
        eq("一筒摸月这一手只有它一个役 → 5 番", iipin.han, 5);
        eq("原文「实际上计 5 番，故为满贯」", iipin.base, 2000);
        Rules noKoy = preset("mleague");
        Evaluator.HandScore haiteiOnly = evalKoyaku("2p3p4p5p6p7p1p1p", twoChi, "1p", true, noKoy, "haitei");
        check("关掉古役 → 只有海底摸月 1 番",
                hasYaku(haiteiOnly, "海底摸月") && !hasYaku(haiteiOnly, "一筒摸月"));
        Evaluator.HandScore chuupin = evalKoyaku("2p3p4p5p6p7p9p9p", twoChi, "9p", false, koy, "houtei");
        check("九筒捞鱼取代河底捞鱼",
                hasYaku(chuupin, "九筒捞鱼") && !hasYaku(chuupin, "河底捞鱼"));
        eq("九筒捞鱼也是 5 番", reportedHan(chuupin, "九筒捞鱼"), 5);

        // ---------- S-61②：大七星**取代**字一色（不是叠加成 3 倍）----------
        final String daichi14 = "1z1z2z2z3z3z4z4z5z5z6z6z7z7z";
        Evaluator.HandScore daichi = evalKoyaku(daichi14, new ArrayList<>(), "7z", false, koyD, "");
        check("大七星成立", hasYaku(daichi, "大七星"));
        check("大七星取代字一色（旧实现两者都加 → 3 倍役满）", !hasYaku(daichi, "字一色"));
        eq("大七星（加倍役满规则）= 2 倍役满", daichi.yakuman, 2);
        Rules noKoy2 = preset("majsoul");
        Evaluator.HandScore ziisou = evalKoyaku(daichi14, new ArrayList<>(), "7z", false, noKoy2, "");
        check("关掉古役 → 只成立字一色（1 倍）",
                hasYaku(ziisou, "字一色") && !hasYaku(ziisou, "大七星"));
        eq("字一色 1 倍役满", ziisou.yakuman, 1);

        // ---------- S-57①：抢杠发生在加杠成立之前，**可以与一发复合** ----------
        List<Map<String, Object>> ckAgari = new ArrayList<>();
        Round ck = newRound();
        ck.melds[0].add(pon("5p"));                              // 座位 0 碰过 5p
        ck.hand[0].add(Tiles.id(k5p, 3));                        // 手里第 4 张 → 加杠
        ck.hand[1].addAll(parse("1m2m3m4m5m6m7m8m9m1p2p3p5p"));  // 立直听 5p（4 顺子 + 5p 单骑）
        ck.riichi[1] = true;
        ck.ippatsu[1] = true;
        ck.table.debugEventTap = (recipient, ev) -> {
            if (recipient == -1 && "agari".equals(Json.str(ev, "ev", ""))) {
                ckAgari.add(new HashMap<>(ev));
            }
        };
        mahjong.game.Round.Result ckr = ck.debugTurnKan(0, "kakan", "5p");
        check("抢杠成立（加杠被荣和）", ckr != null && ckr.agari && ckr.winner == 1);
        eq("抢杠发出一条 agari 报文", ckAgari.size(), 1);
        check("抢杠与一发**复合**（旧实现先 clearIppatsu → 白丢 1 番）："
                        + (ckAgari.isEmpty() ? "<无报文>" : agariCodes(ckAgari.get(0))),
                !ckAgari.isEmpty() && agariCodes(ckAgari.get(0)).contains("ippatsu"));

        // ---------- S-57②：多家抢杠同样受**头跳**约束 ----------
        List<Map<String, Object>> mlAgari = new ArrayList<>();
        Round mlCk = newRound(preset("mleague"));                // headBump = true
        mlCk.melds[0].add(pon("5p"));
        mlCk.hand[0].add(Tiles.id(k5p, 3));
        mlCk.hand[1].addAll(parse("2z2z2z1m1m1m2m2m2m3m3m3m5p")); // 南（役牌）
        mlCk.hand[2].addAll(parse("3z3z3z4m4m4m5m5m5m6m6m6m5p")); // 西（役牌）
        mlCk.table.debugEventTap = (recipient, ev) -> {
            if (recipient == -1 && "agari".equals(Json.str(ev, "ev", ""))) {
                mlAgari.add(new HashMap<>(ev));
            }
        };
        mahjong.game.Round.Result mlCkr = mlCk.debugTurnKan(0, "kakan", "5p");
        check("多家抢杠：头跳只认最近那家（旧实现两家都结算）",
                mlCkr != null && mlCkr.agari && mlAgari.size() == 1 && mlCkr.winner == 1);
        // 《雀魂》无头跳 → 两家都能抢杠
        List<Map<String, Object>> msAgari = new ArrayList<>();
        Round msCk = newRound(preset("majsoul"));
        msCk.melds[0].add(pon("5p"));
        msCk.hand[0].add(Tiles.id(k5p, 3));
        msCk.hand[1].addAll(parse("2z2z2z1m1m1m2m2m2m3m3m3m5p"));
        msCk.hand[2].addAll(parse("3z3z3z4m4m4m5m5m5m6m6m6m5p"));
        msCk.table.debugEventTap = (recipient, ev) -> {
            if (recipient == -1 && "agari".equals(Json.str(ev, "ev", ""))) {
                msAgari.add(new HashMap<>(ev));
            }
        };
        mahjong.game.Round.Result msCkr = msCk.debugTurnKan(0, "kakan", "5p");
        check("多家抢杠：《雀魂》无头跳 → 两家都成立",
                msCkr != null && msCkr.agari && msAgari.size() == 2);

        // ---------- 《雀魂》特有：**国士无双可以抢暗杠**（文档 §抢杠 L957）----------
        //
        // 判在"杠成立之前"：被抢时这次杠**整个不成立** —— 那 4 张仍在杠主手里、不翻杠宝牌、
        // 不打断一发、`kanCount` 不 +1，本局直接以荣和收局。所以除了"谁赢了多少"，
        // 下面还要把杠主那一侧的状态一起钉住（这正是"抢走了杠"与"杠成立后被和"的区别）。
        List<Map<String, Object>> kaAgari = new ArrayList<>();
        Round kaMs = kokushiAnkanRound(preset("majsoul"));
        kaMs.riichi[1] = true;
        kaMs.ippatsu[1] = true;                  // 立直 + 一发：杠没成立就不该被打断（同 S-57①）
        kaMs.table.debugEventTap = (recipient, ev) -> {
            if (recipient == -1 && "agari".equals(Json.str(ev, "ev", ""))) {
                kaAgari.add(new HashMap<>(ev));
            }
        };
        mahjong.game.Round.Result kaR = kaMs.debugTurnKan(0, "ankan", "1z");
        check("《雀魂》：国士无双抢暗杠成立", kaR != null && kaR.agari && kaR.winner == 1);
        eq("国士抢暗杠发出一条 agari 报文", kaAgari.size(), 1);
        check("抢下来的是国士无双: " + (kaAgari.isEmpty() ? "<无报文>" : agariCodes(kaAgari.get(0))),
                !kaAgari.isEmpty() && agariCodes(kaAgari.get(0)).contains("kokushi"));
        check("被抢时**暗杠不成立**：没记进副露", kaMs.melds[0].isEmpty());
        eq("被抢时 `kanCount` 不 +1（那次杠没发生）", kaMs.kanCount, 0);
        eq("被抢时那 4 张仍在杠主手里（没被挪走）", kaMs.hand[0].size(), 14);
        check("被抢时**不打断一发**（与 S-57① 同一条理由）", kaMs.ippatsu[1]);
        check("被抢时不算「鸣牌」（人和等门前役的前提不变）", !kaMs.anyCall);
        eq("国士无双 1 倍役满：放铳的庄家付 32000", kaR == null ? 0 : -kaR.delta[0], 32000);
        eq("和牌者收 32000", kaR == null ? 0 : kaR.delta[1], 32000);

        // 对照一：《天凤》/ M.League 不允许 → 暗杠照常成立（那 4 张真的被挪进副露）
        Round kaTh = kokushiAnkanRound(preset("tenhou"));
        mahjong.game.Round.Result kaThR = kaTh.debugTurnKan(0, "ankan", "1z");
        check("《天凤》不允许国士抢暗杠 → 暗杠照常成立", kaThR == null);
        eq("《天凤》：暗杠记进副露", kaTh.melds[0].size(), 1);
        eq("《天凤》：杠主手里少 4 张", kaTh.hand[0].size(), 10);
        eq("《天凤》：`kanCount` +1", kaTh.kanCount, 1);
        check("三套预设里只有《雀魂》打开这条", preset("majsoul").kokushiAnkan
                && !preset("tenhou").kokushiAnkan && !preset("mleague").kokushiAnkan);

        // 对照二：口子**只开给国士** —— 同样听 1z 的普通手（对对和 + 役牌）抢不了暗杠
        Round kaOther = kokushiAnkanRound(preset("majsoul"));
        kaOther.hand[1].clear();
        kaOther.hand[1].addAll(parse("2z2z2z3z3z3z4z4z4z5z5z5z1z"));
        check("口子只开给国士：听同一张的普通手抢不了暗杠",
                kaOther.debugTurnKan(0, "ankan", "1z") == null && kaOther.melds[0].size() == 1);

        // 对照三：振听的国士也抢不了（抢暗杠是一次荣和，荣和的前提照旧）
        Round kaFuriten = kokushiAnkanRound(preset("majsoul"));
        kaFuriten.furitenPerm[1] = true;
        check("振听的国士抢不了暗杠",
                kaFuriten.debugTurnKan(0, "ankan", "1z") == null && kaFuriten.melds[0].size() == 1);

        // 对照四：抢暗杠走的是**荣和**那条路 → 人和（文档 §人和 L1375「在国士无双抢暗杠时
        // 人和也成立」）与国士复合 = 2 倍役满。人和在三套预设里都是 off，这里显式打开。
        Rules kaRenhou = preset("majsoul");
        kaRenhou.renhou = "yakuman";
        List<Map<String, Object>> krAgari = new ArrayList<>();
        Round kaRr = kokushiAnkanRound(kaRenhou);
        kaRr.table.debugEventTap = (recipient, ev) -> {
            if (recipient == -1 && "agari".equals(Json.str(ev, "ev", ""))) {
                krAgari.add(new HashMap<>(ev));
            }
        };
        mahjong.game.Round.Result kaRrR = kaRr.debugTurnKan(0, "ankan", "1z");
        check("国士抢暗杠时**人和也成立**（原文点名的那一条）: "
                        + (krAgari.isEmpty() ? "<无报文>" : agariCodes(krAgari.get(0))),
                !krAgari.isEmpty() && agariCodes(krAgari.get(0)).contains("renhou"));
        eq("国士无双 + 人和 = 2 倍役满", kaRrR == null ? -1 : -kaRrR.delta[0], 64000);
        Rules kaRenhouOff = preset("tenhou");     // 不允许抢暗杠 + 人和开着
        kaRenhouOff.renhou = "yakuman";
        Round kaRrOff = kokushiAnkanRound(kaRenhouOff);
        check("对照：不允许抢暗杠时，人和不可能以这条路径成立",
                kaRrOff.debugTurnKan(0, "ankan", "1z") == null && kaRrOff.melds[0].size() == 1);

        // 对照五：多家抢暗杠与加杠共用**同一把尺子**（`rules.headBump`）——
        // 《雀魂》本身无头跳，所以两家国士都成立；把开关打开就只认最近那家。
        // （三套预设里 headBump 与 kokushiAnkan 没有同时为真的组合，只能自己拼一套。）
        Rules kaBump = preset("majsoul");
        kaBump.headBump = true;
        List<Map<String, Object>> kbAgari = new ArrayList<>();
        Round kaB = kokushiAnkanRound(kaBump);
        kaB.hand[2].addAll(parse("9m9m1p9p1s9s2z3z4z5z6z7z1m"));   // 另一家也单骑 1z
        kaB.table.debugEventTap = (recipient, ev) -> {
            if (recipient == -1 && "agari".equals(Json.str(ev, "ev", ""))) {
                kbAgari.add(new HashMap<>(ev));
            }
        };
        mahjong.game.Round.Result kaBR = kaB.debugTurnKan(0, "ankan", "1z");
        check("多家抢暗杠：开了头跳就只认最近那家",
                kaBR != null && kaBR.winner == 1 && kbAgari.size() == 1);
        List<Map<String, Object>> kb2Agari = new ArrayList<>();
        Round kaB2 = kokushiAnkanRound(preset("majsoul"));        // 无头跳
        kaB2.hand[2].addAll(parse("9m9m1p9p1s9s2z3z4z5z6z7z1m"));
        kaB2.table.debugEventTap = (recipient, ev) -> {
            if (recipient == -1 && "agari".equals(Json.str(ev, "ev", ""))) {
                kb2Agari.add(new HashMap<>(ev));
            }
        };
        mahjong.game.Round.Result kaB2R = kaB2.debugTurnKan(0, "ankan", "1z");
        check("多家抢暗杠：《雀魂》无头跳 → 两家国士都成立",
                kaB2R != null && kb2Agari.size() == 2);
    }

    /**
     * 国士抢暗杠用的局面：座位 0（庄）手里 4 张 1z 准备暗杠，座位 1 国士单骑 1z。
     *
     * <p>摆牌而不是等发牌：这条规则要"杠主手里正好 4 张 + 他家正好听那一张"，概率极低。
     * 座位 1 那 12 种幺九 + 1m 雀头 = 13 张，和了 1z 即 13 种 + 一对 → 国士无双。
     */
    private static Round kokushiAnkanRound(Rules rules) {
        Round r = newRound(rules);
        r.hand[0].addAll(parse("1z1z1z1z1m2m3m4m5m6m7m8m9m9p"));   // 14 张，含 4 张 1z
        r.hand[1].addAll(parse("1m1m9m1p9p1s9s2z3z4z5z6z7z"));
        return r;
    }

    /** 一条 `agari` 报文里的役种码（用 `,` 连起来，便于 `contains` 判据）。 */
    private static String agariCodes(Map<String, Object> ev) {
        StringBuilder sb = new StringBuilder();
        for (Object o : Json.list(ev, "yaku")) {
            Map<String, Object> y = Json.asObj(o);
            if (sb.length() > 0) {
                sb.append(',');
            }
            sb.append(Json.str(y, "code", "?"));
        }
        return sb.toString();
    }

    /**
     * 古役 / 役满复合用的评价：与 `evalCtx` 同一套构造，额外给出**副露**与
     * 海底 / 河底 / 天和 / 地和这些"偶然役"开关（`flags` 里出现哪个词就开哪个）。
     *
     * @param melds 空 = 门前（`ctx.menzen = true`）
     */
    private static Evaluator.HandScore evalKoyaku(String hand, List<Meld> melds, String win,
                                                  boolean tsumo, Rules rules, String flags) {
        int[] c = counts(hand);
        WinContext ctx = new WinContext();
        ctx.rules = rules;
        ctx.seat = 1;
        ctx.dealerSeat = 0;
        ctx.roundWind = 27;
        ctx.tsumo = tsumo;
        ctx.menzen = melds.isEmpty();
        ctx.winKind = Tiles.parseKind(win);
        ctx.doraIndicators = new ArrayList<>();
        ctx.uraIndicators = new ArrayList<>();
        ctx.allTileIds = parse(hand);
        ctx.haitei = flags.contains("haitei");
        ctx.houtei = flags.contains("houtei");
        ctx.tenhou = flags.contains("tenhou");
        ctx.chiihou = flags.contains("chiihou");
        return Evaluator.evaluate(ctx, c, melds, ctx.winKind);
    }

    // ------------------------------------------------ 被取消询问的回包必须摘掉
    // ------------------------------------------------ 四杠散了 / 赤宝牌张数
    /**
     * 四杠散了的判据（`docs/日本麻将.md`「四杠散了」）：一局里**2 名及以上玩家开满 4 次杠**
     * → 强制流局；同一人开满 4 次则是**四杠子**，不流局。
     *
     * <p>三种豁免（第 4 次杠后岭上开花 / 被抢杠 / 岭上牌放铳）由**和了路径先 return** 保证，
     * 所以判据本身只回答"杠数与人头数"，这里把那张真值表钉住。
     */
    private static void fourKanAbortTests() {
        // 四杠散了是**规则取舍**：M.League **没有中途流局**（`fourKanAbort=false`），
        // 所以断言必须显式钉住"开着这条规则"的规则集 —— 用默认预设会随预设变化而误报。
        mahjong.game.Round r = newRound(preset("tenhou"));
        r.kanCount = 3;
        r.kanByPlayer[0] = 3;
        check("杠 3 次：不流局", !r.fourKanAbortNow());

        r.kanCount = 4;
        r.kanByPlayer[0] = 2;
        r.kanByPlayer[1] = 2;
        check("2 家共开 4 次杠 → 四杠散了成立", r.fourKanAbortNow());

        mahjong.game.Round solo = newRound(preset("tenhou"));
        solo.kanCount = 4;
        solo.kanByPlayer[2] = 4;
        check("同一人开满 4 次杠（四杠子）→ 不流局", !solo.fourKanAbortNow());

        mahjong.game.Round off = newRound(preset("tenhou"));
        off.rules.fourKanAbort = false;
        off.kanCount = 4;
        off.kanByPlayer[0] = 2;
        off.kanByPlayer[3] = 2;
        check("规则关掉四杠散了 → 不流局", !off.fourKanAbortNow());

        // M.League：2 家共开 4 次杠**不流局**（继续打），这是与上面那条同一个判据的另一侧
        mahjong.game.Round ml = newRound();
        ml.kanCount = 4;
        ml.kanByPlayer[0] = 2;
        ml.kanByPlayer[1] = 2;
        check("M.League 2 家共开 4 次杠 → 不流局（无中途流局）", !ml.fourKanAbortNow());
    }

    // ------------------------------------------- M.League 规则取舍（docs/日本麻将.md 2026-09 版）

    /**
     * M.League 与一般规则（《天凤》/《雀魂》）在**取舍项**上的差异 —— 每一条都两侧钉住。
     *
     * <p>依据 `docs/日本麻将.md`（2026-09 版，含 M.League 规则说明）：
     * 役满不加倍、无累计役满（普通役上限三倍满）、切上满贯、连风雀头 2 符、
     * 立直无点数/残牌门槛但摸到海底后不可立直、立直后暗杠要求面子构成不变、
     * 无中途流局、头跳、精算 =（点数 − 返点）/1000 + 马点 + 头名赏（同点平分）。
     *
     * <p>为什么**不能**只断言默认预设：默认预设本身会变（现在是 M.League），
     * 只测默认值等于在测"默认值恰好是什么"，换个预设就整片误报。所以两侧都显式取预设。
     */
    private static void mleagueRulesTests() {
        Rules ml = preset("mleague");
        Rules th = preset("tenhou");
        Rules ms = preset("majsoul");

        // ── 预设本身
        eq("默认预设 = M.League", Rules.defaults().preset, "mleague");
        check("M.League 无中途流局（四种全关）",
                !ml.fourKanAbort && !ml.fourRiichiAbort && !ml.fourWindAbort && !ml.kyuushuAbort);
        check("M.League 三家和了关 + 头跳开", !ml.sanchaAbort && ml.headBump);
        check("M.League 无击飞 / 无和了止 / 无流局满贯 / 无西入",
                !ml.tobi && !ml.agariyame && !ml.nagashiMangan && !ml.westExtension);
        check("M.League 无古役 + 常时一番缚", !ml.koyaku && ml.minHan == 1);
        check("M.League 食断 + 后付、里宝 + 杠宝", ml.kuitan && ml.ura && ml.kanDora);
        eq("M.League 赤宝牌 3 张", ml.aka, 3);
        check("M.League 立直放宽 + 摸海底禁立直 + 暗杠保面子",
                ml.riichiMinScore == 0 && ml.riichiMinTilesLeft == 0 && ml.riichiNoHaitei && ml.ankanKeepsShape);
        // ── 新取舍项的报文面（`fromJson` / `toJson` 是同一对，名字写错就是静默失效）
        check("《雀魂》国士抢暗杠的预设值：只有它打开",
                ms.kokushiAnkan && !ml.kokushiAnkan && !th.kokushiAnkan);
        check("报文单项可覆盖预设（两个方向）",
                Rules.fromJson(Json.obj("preset", "tenhou", "kokushi_ankan", true)).kokushiAnkan
                        && !Rules.fromJson(Json.obj("preset", "majsoul", "kokushi_ankan", false)).kokushiAnkan);
        check("toJson 带出这个字段", Boolean.TRUE.equals(ms.toJson().get("kokushi_ankan")));

        // ── 预设先铺、单字段再覆盖（协议里的 rules 就是这么用的）
        Map<String, Object> m = new java.util.LinkedHashMap<>();
        m.put("preset", "mleague");
        m.put("kazoe_yakuman", true);
        m.put("aka", 0);
        Rules over = Rules.fromJson(m);
        check("预设 + 单字段覆盖：累计役满打开", over.kazoeYakuman);
        eq("预设 + 单字段覆盖：赤宝牌 0 张", over.aka, 0);
        check("未覆盖的项仍是 M.League（切上满贯 / 不加倍 / 同点平分）",
                over.kiriageMangan && !over.doubleYakuman && over.tieSplitPoint);

        // ── 切上满贯：3 番 60 符 与 4 番 30 符（基本点都是 1920，M.League 按满贯）
        //   3 番 60 符：111m 999m 111p（三个幺九暗刻 8×3）+ 234s + 5s5s，配立直凑役
        //     役：立直 1 + 三暗刻 2 = 3 番（⚠ 役牌**雀头**只给符、不给役，别拿它凑番）
        //     符：20 + 10(门前荣和) + 8+8+8 = 54 → 60
        Evaluator.HandScore k3 = evalCtx("1m1m1m9m9m9m1p1p1p2s3s4s5s5s", "4s", false, 1, 0, 0, true, ml);
        eq("切上满贯牌型：3 番", k3.han, 3);
        eq("切上满贯牌型：60 符", k3.fu, 60);
        eq("M.League 3 番 60 符 → 满贯", k3.limit, "满贯");
        eq("M.League 3 番 60 符基本点 = 2000", k3.base, 2000);
        Evaluator.HandScore k3t = evalCtx("1m1m1m9m9m9m1p1p1p2s3s4s5s5s", "4s", false, 1, 0, 0, true, th);
        eq("《天凤》3 番 60 符基本点 = 1920（不切上）", k3t.base, 1920);
        check("《天凤》3 番 60 符不带满贯标签", !"满贯".equals(k3t.limit));

        //   4 番 30 符：三色同顺 2 + 断幺九 1 + 平和 1，门前荣和 20+10 = 30 符
        Evaluator.HandScore k4 = evalCtx("2m3m4m2p3p4p2s3s4s5p6p7p8s8s", "4s", false, 1, 0, 0, false, ml);
        eq("切上满贯牌型：4 番", k4.han, 4);
        eq("切上满贯牌型：30 符", k4.fu, 30);
        eq("M.League 4 番 30 符 → 满贯", k4.limit, "满贯");
        eq("M.League 4 番 30 符基本点 = 2000", k4.base, 2000);
        Evaluator.HandScore k4t = evalCtx("2m3m4m2p3p4p2s3s4s5p6p7p8s8s", "4s", false, 1, 0, 0, false, th);
        eq("《天凤》4 番 30 符基本点 = 1920（不切上）", k4t.base, 1920);

        // 5 番起走档位，两种规则都是满贯（切上满贯只动 3/4 番那两个 1920 的格子）
        Evaluator.HandScore k5 = evalCtx("2m3m4m2m3m4m2p3p4p2s3s4s5s5s", "4s", false, 1, 0, 0, false, ml);
        eq("5 番牌型：三色同顺 + 一杯口 + 断幺九 + 平和 = 5 番", k5.han, 5);
        eq("M.League 5 番 → 满贯 2000", k5.base, 2000);
        Evaluator.HandScore k5t = evalCtx("2m3m4m2m3m4m2p3p4p2s3s4s5s5s", "4s", false, 1, 0, 0, false, th);
        eq("《天凤》5 番 → 满贯 2000", k5t.base, 2000);

        // ── 精算点数（docs/日本麻将.md §精算点数 的两个例子逐位比对，点数都不变）
        int[] sc = {53600, 28600, 20000, -2200};
        RoundScoring.Settlement mlS = RoundScoring.settle(sc, ml);
        eq("M.League 精算 1 位", round1(mlS.point[0]), 73.6);
        eq("M.League 精算 2 位", round1(mlS.point[1]), 8.6);
        eq("M.League 精算 3 位", round1(mlS.point[2]), -20.0);
        eq("M.League 精算 4 位", round1(mlS.point[3]), -62.2);
        eq("M.League 头名赏 = (30000−25000)×4/1000 = 20", round1(mlS.oka[0]), 20.0);
        eq("M.League 马点原样：1 位 +30", round1(mlS.uma[0]), 30.0);
        eq("M.League 马点原样：4 位 −30", round1(mlS.uma[3]), -30.0);
        eq("M.League 名次顺序（按点数降序）", Arrays.toString(mlS.order), "[0, 1, 2, 3]");
        RoundScoring.Settlement msS = RoundScoring.settle(sc, ms);
        eq("《雀魂》精算 1 位（精算基准 = 25000，无头名赏）", round1(msS.point[0]), 43.6);
        eq("《雀魂》精算 2 位", round1(msS.point[1]), 8.6);
        eq("《雀魂》精算 3 位", round1(msS.point[2]), -10.0);
        eq("《雀魂》精算 4 位", round1(msS.point[3]), -42.2);
        eq("《雀魂》无头名赏", round1(msS.oka[0]), 0.0);

        // ── 同点：M.League 拆分对应名次的马点与头名赏，且**尾数归更接近起家者**、并列的**同顺位**
        int[] tie = {30000, 30000, 25000, 15000};
        RoundScoring.Settlement tieMl = RoundScoring.settle(tie, ml);
        eq("M.League 同点：1 位马点平分 (30+10)/2", round1(tieMl.uma[0]), 20.0);
        eq("M.League 同点：头名赏平分 20/2", round1(tieMl.oka[0]), 10.0);
        eq("M.League 同点：两个头名同分", round1(tieMl.point[0]), 30.0);
        eq("M.League 同点：另一个头名同分", round1(tieMl.point[1]), 30.0);
        eq("M.League 同点：4 位照自己那套马点", round1(tieMl.point[3]), -45.0);
        // 2 人同分 → **同顺位**（1 位），两人的名次号一样
        eq("M.League 同点：两家的名次都是 1 位", tieMl.rank[0] + "/" + tieMl.rank[1], "0/0");
        eq("M.League 同点：第 3 家是 3 位", tieMl.rank[2], 2);
        RoundScoring.Settlement tieTh = RoundScoring.settle(tie, th);
        eq("《天凤》同点：座次靠前者吃掉 1 位加点", round1(tieTh.point[0]), 40.0);
        eq("《天凤》同点：另一家只是 2 位", round1(tieTh.point[1]), 10.0);
        eq("《天凤》同点：4 位照自己那套马点", round1(tieTh.point[3]), -35.0);
        eq("《天凤》同点：名次严格 1/2（同分也按起家座次拆开）",
                tieTh.rank[0] + "/" + tieTh.rank[1], "0/1");

        // ── 3 人同分：**尾数归更接近起家者**（M.League 原文）──────────────
        // 马点 30+10−10 = 30 → 10.0/10.0/10.0（正好整除，没有尾数）；
        // 头名赏 20+0+0 = 20 → 20/3 = 6.66… → 以 **0.1 分**为单位拆：
        //   6.6/6.6/6.6 + 尾数 0.2 全给**更接近起家**的那家 → 6.8/6.6/6.6。
        int[] tie3 = {30000, 30000, 30000, 10000};
        RoundScoring.Settlement t3 = RoundScoring.settle(tie3, ml);
        eq("3 人同分：马点平分 (30+10−10)/3 = 10", round1(t3.uma[0]), 10.0);
        eq("3 人同分：头名赏尾数归更接近起家者 → 6.8", round1(t3.oka[0]), 6.8);
        eq("3 人同分：第二家 6.6", round1(t3.oka[1]), 6.6);
        eq("3 人同分：第三家 6.6", round1(t3.oka[2]), 6.6);
        eq("3 人同分：三家的头名赏总额没变（还是 20）",
                round1(t3.oka[0] + t3.oka[1] + t3.oka[2]), 20.0);
        eq("3 人同分：精算点数也按同一顺序（16.8 / 16.6 / 16.6）",
                round1(t3.point[0]) + "/" + round1(t3.point[1]) + "/" + round1(t3.point[2]),
                "16.8/16.6/16.6");
        eq("3 人同分：三人**同顺位**（都是 1 位）",
                t3.rank[0] + "/" + t3.rank[1] + "/" + t3.rank[2], "0/0/0");
        eq("3 人同分：第 4 家 4 位", t3.rank[3], 3);
        // 尾数判据不依赖"座次 0 一定并列"：让 1/2/3 号座并列 1 位（座次 0 垫底）。
        // ⚠ `Settlement.uma/oka` 是**按名次**索引的（不是按座位 —— AUDIT S-64 把这条 javadoc
        //   记成过"按座位"），所以这里看 `oka[0..2]`；要看"哪一家"用 `order[名次]`。
        int[] tie123 = {10000, 30000, 30000, 30000};
        RoundScoring.Settlement t123 = RoundScoring.settle(tie123, ml);
        eq("3 人并列 1 位：名次 0 是座次 1（更接近起家）", t123.order[0], 1);
        eq("尾数给更接近起家的那家 → 名次 0 拿 6.8", round1(t123.oka[0]), 6.8);
        eq("并列的另两家各 6.6", round1(t123.oka[1]) + "/" + round1(t123.oka[2]), "6.6/6.6");
        eq("垫底的座次 0（名次 3）不拿头名赏", round1(t123.oka[3]), 0.0);
        // 负数（3/4 位）也要"尾数归更接近起家者、总额一分不差"：座次 1/2/3 并列末三位时
        // 马点 = 10 + (−10) + (−31) = −31 → −31/3 = −10.33… → 以 0.1 分为单位：
        //   −10.4 / −10.4 / −10.4，尾数 +0.2 给更接近起家者 → −10.2 / −10.4 / −10.4。
        Rules odd = preset("mleague");
        odd.uma = new int[]{30, 10, -10, -31};
        int[] tieLow = {30000, 10000, 10000, 10000};
        RoundScoring.Settlement tlow = RoundScoring.settle(tieLow, odd);
        eq("负数顺位点：更接近起家者拿得更多（−10.2 而非 −10.4）", round1(tlow.uma[1]), -10.2);
        eq("负数顺位点：另两家各 −10.4",
                round1(tlow.uma[2]) + "/" + round1(tlow.uma[3]), "-10.4/-10.4");
        eq("负数顺位点：总额仍是 −31（一分不差）",
                round1(tlow.uma[1] + tlow.uma[2] + tlow.uma[3]), -31.0);
        eq("负数顺位点：并列三家同顺位（名次 1）",
                tlow.rank[1] + "/" + tlow.rank[2] + "/" + tlow.rank[3], "1/1/1");

        // ── 立直后暗杠：M.League 追加「面子构成不变」（用文档那 4 个例子里可判定的 2 个）
        //   ① 8p 与顺子无关（同花色 ±2 内没有牌）→ 两种规则都可以
        mahjong.game.Round k8 = newRound(ml);
        k8.hand[1].addAll(parse("1m1m1m2m2m3m3m3m8p8p8p6z6z8p"));
        k8.riichi[1] = true;
        check("M.League 立直后暗杠 8p（面子构成不变）可以",
                k8.debugTurnOptionTypes(1, Tiles.id(16, 0)).contains("kan"));
        //   ② 2p：听牌不变、但 222p 刻子变杠 → 面子构成变：《天凤》可以，M.League 不行
        mahjong.game.Round k2 = newRound(ml);
        k2.hand[1].addAll(parse("7m7m2p2p2p3p3p3p4p4p4p6s7s2p"));
        k2.riichi[1] = true;
        check("M.League 立直后暗杠 2p（面子构成变）不行",
                !k2.debugTurnOptionTypes(1, Tiles.id(10, 0)).contains("kan"));
        mahjong.game.Round k2t = newRound(th);
        k2t.hand[1].addAll(parse("7m7m2p2p2p3p3p3p4p4p4p6s7s2p"));
        k2t.riichi[1] = true;
        check("《天凤》同一手牌可以暗杠 2p（只要求听牌不变）",
                k2t.debugTurnOptionTypes(1, Tiles.id(10, 0)).contains("kan"));
        //   ③ 非立直不受这条限制
        mahjong.game.Round k2r = newRound(ml);
        k2r.hand[1].addAll(parse("7m7m2p2p2p3p3p3p4p4p4p6s7s2p"));
        check("未立直时暗杠 2p 不受面子构成限制",
                k2r.debugTurnOptionTypes(1, Tiles.id(10, 0)).contains("kan"));

        // ── 立直后不可大明杠（任何规则；立直是门前状态）
        mahjong.game.Round dm = newRound(ml);
        dm.hand[1].addAll(parse("3p3p3p1m2m3m4m5m6m7m8m9m5s5s"));
        dm.riichi[1] = true;
        check("立直后不下发大明杠选项", !dm.canDaiminkan(1, 11));   // 11 = 3p
        mahjong.game.Round dm2 = newRound(ml);
        dm2.hand[1].addAll(parse("3p3p3p1m2m3m4m5m6m7m8m9m5s5s"));
        check("未立直可以大明杠（手里 3 张 + 舍张）", dm2.canDaiminkan(1, 11));

        // ── 包牌一：大三元 / 大四喜的判定**计入已经公开的暗杠**（M.League 明文），
        //    但暗杠本身不会成为包牌者（它不是"他家的舍张"）
        // ⚠ 顺序要与真实路径一致：真实代码是 `melds[seat].add(m)` **之后**才 updatePao，
        //   所以计数里必须已经含有这一次副露本身。
        mahjong.game.Round pd = newRound(ml);
        pd.melds[1].add(new Meld(Meld.Kind.PON, new int[]{124, 125, 126}, 0, 124));       // 白白白（碰 0 家）
        pd.melds[1].add(new Meld(Meld.Kind.ANKAN, new int[]{128, 129, 130, 131}, 1, 128)); // 發發發發（自家暗杠）
        Meld chuun = new Meld(Meld.Kind.PON, new int[]{132, 133, 134}, 2, 132);            // 中中中（碰 2 家）
        pd.melds[1].add(chuun);
        pd.debugUpdatePao(1, 2, chuun);
        eq("大三元含暗杠：第 3 个三元副露那家包牌", pd.paoSeat[1], 2);
        mahjong.game.Round pdSelf = newRound(ml);
        pdSelf.melds[1].add(new Meld(Meld.Kind.PON, new int[]{124, 125, 126}, 0, 124));
        pdSelf.melds[1].add(new Meld(Meld.Kind.PON, new int[]{132, 133, 134}, 2, 132));
        Meld hatsu = new Meld(Meld.Kind.ANKAN, new int[]{128, 129, 130, 131}, 1, 128);
        pdSelf.melds[1].add(hatsu);
        pdSelf.debugUpdatePao(1, -1, hatsu);
        eq("暗杠凑齐第 3 个三元牌 → 没人包牌（from = -1）", pdSelf.paoSeat[1], -1);

        // ── 包牌二：四杠子包牌**只有 M.League**，且必须是"他家的舍张 → 大明杠完成第 4 个杠"
        Meld kan1 = new Meld(Meld.Kind.ANKAN, new int[]{0, 1, 2, 3}, 1, 0);
        Meld kan2 = new Meld(Meld.Kind.ANKAN, new int[]{4, 5, 6, 7}, 1, 4);
        Meld kan3 = new Meld(Meld.Kind.KAKAN, new int[]{8, 9, 10, 11}, 1, 8);
        Meld kan4 = new Meld(Meld.Kind.DAIMINKAN, new int[]{12, 13, 14, 15}, 2, 12);
        mahjong.game.Round pk = newRound(ml);
        pk.melds[1].add(kan1);
        pk.melds[1].add(kan2);
        pk.melds[1].add(kan3);
        pk.melds[1].add(kan4);
        pk.debugUpdatePao(1, 2, kan4);
        eq("M.League 四杠子：大明杠完成第 4 个杠 → 那家包牌", pk.paoSeat[1], 2);
        mahjong.game.Round pkTh = newRound(th);
        pkTh.melds[1].add(kan1);
        pkTh.melds[1].add(kan2);
        pkTh.melds[1].add(kan3);
        pkTh.melds[1].add(kan4);
        pkTh.debugUpdatePao(1, 2, kan4);
        eq("《天凤》没有四杠子包牌", pkTh.paoSeat[1], -1);
        // 加杠完成第 4 个杠不算（不是"他家的舍张"）
        Meld kan4k = new Meld(Meld.Kind.KAKAN, new int[]{12, 13, 14, 15}, 2, 12);
        mahjong.game.Round pkK = newRound(ml);
        pkK.melds[1].add(kan1);
        pkK.melds[1].add(kan2);
        pkK.melds[1].add(kan3);
        pkK.melds[1].add(kan4k);
        pkK.debugUpdatePao(1, 2, kan4k);
        eq("加杠完成第 4 个杠不触发包牌", pkK.paoSeat[1], -1);

        // ── 包牌三：包"被包的那一役"还是包"全部役满"（大四喜 1 倍 + 字一色 1 倍，庄家 A 荣和 C）
        //   文档 §包牌 的 M.League 例子：B 包大四喜 → B 付 24000、C 付 72000（合计 96000）
        //   《天凤》包牌涉及复合后的全部役满 → B、C 各付 48000
        Evaluator.HandScore fourBig = new Evaluator.HandScore();
        fourBig.yakuman = 2;
        fourBig.base = 16000;
        fourBig.valid = true;
        Payments.Result ronMl = Payments.compute(fourBig, 0, 2, 0, 0, 0, false, 3, 8000);
        eq("M.League 包牌只包被包役满：包牌者付一半 24000", -ronMl.delta[3], 24000);
        eq("M.League 包牌只包被包役满：放铳者付其余 72000", -ronMl.delta[2], 72000);
        eq("M.League 包牌：和牌者收满 96000", ronMl.delta[0], 96000);
        Payments.Result ronTh = Payments.compute(fourBig, 0, 2, 0, 0, 0, false, 3, 16000);
        eq("《天凤》包牌承担全部役满：包牌者付一半 48000", -ronTh.delta[3], 48000);
        eq("《天凤》包牌承担全部役满：放铳者也付一半 48000", -ronTh.delta[2], 48000);
    }

    /** 精算点数只到 0.1，比较时先四舍五入到 1 位小数（浮点直接比会因 1e-16 误报）。 */
    private static double round1(double v) {
        return Math.round(v * 10) / 10.0;
    }

    // ================================================================= 对局记录回放

    /**
     * 回放：记录器（序号/过滤/截断/ID 形状）→ 整场录制 → 落盘与分块读取 → 容量淘汰 → 重启加载。
     *
     * <p>为什么值得这么多断言：回放是**离线**功能，出错不会有人当场发现（用户翻到某一步才发现
     * "牌对不上"）。所以这里既钉"能存能取"，也钉**语义不变量** ——
     * 例如"每条 `discard` 的牌必须是该家之前摸到过的"（牌张守恒的弱形式），
     * 一旦记录漏事件/顺序错乱，这条会立刻红。
     */
    private static void replayTests() {
        Rules rules = Rules.defaults();
        List<String> names = Arrays.asList("甲", "乙", "丙", "丁");

        // ---------- ① 记录器：序号、过滤、聊天与操作共用一条序号 ----------
        ReplayRecorder rec = new ReplayRecorder(rules.toJson(), names);
        rec.add(-1, Json.obj("ev", "game_start", "rules", rules.toJson()));
        rec.add(0, Json.obj("ev", "draw", "seat", 0, "tile", "1m"));
        rec.add(-1, Json.obj("ev", "chat", "seat", 1, "name", "乙", "text", "碰！"));
        rec.add(0, Json.obj("ev", "discard", "seat", 0, "tile", "1m", "tsumogiri", false));
        // 这些必须被丢掉：派生快照 / 大厅噪音 / 请求错误
        rec.add(-1, Json.obj("ev", "state", "seat", 0));
        rec.add(-1, Json.obj("ev", "rooms", "items", Arrays.asList()));
        rec.add(-1, Json.obj("ev", "room", "id", "ABCD"));
        rec.add(0, Json.obj("ev", "error", "code", "bad_json"));
        Replay rp = rec.peek();
        eq("回放：跳过派生/噪音事件后的条数", rp.entries.size(), 4);
        boolean seqOk = true;
        for (int i = 0; i < rp.entries.size(); i++) {
            seqOk = seqOk && rp.entries.get(i).seq == i;
        }
        check("回放：序号 0..n-1 严格递增", seqOk);
        eq("回放：聊天按到达顺序插在两次操作之间（上一操作）", rp.entries.get(2).ev(), "chat");
        eq("回放：聊天之后才是出牌（稳定先后顺序）", rp.entries.get(3).ev(), "discard");
        eq("回放：广播条目的收件座位 = -1", rp.entries.get(2).to, -1);
        eq("回放：私有条目的收件座位 = 0", rp.entries.get(1).to, 0);

        // ---------- ② ID 形状（不可猜 + 路径安全） ----------
        String id = ReplayRecorder.newId();
        check("回放：新 ID 合法（10 位 base32）：" + id, ReplayRecorder.validId(id));
        check("回放：新 ID 互不相同", !ReplayRecorder.newId().equals(ReplayRecorder.newId()));
        check("回放：拒绝短 ID", !ReplayRecorder.validId("ABC"));
        check("回放：拒绝路径穿越", !ReplayRecorder.validId("../../etc/p"));
        check("回放：拒绝字母表外的字符（I/L/O/U）", !ReplayRecorder.validId("IIIIIIIIII"));
        check("回放：拒绝小写", !ReplayRecorder.validId(id.toLowerCase()));

        // ---------- ③ 截断：条数上限到了就停记并置标记 ----------
        ReplayRecorder small = new ReplayRecorder(rules.toJson(), names, 3, 1024 * 1024);
        for (int i = 0; i < 6; i++) {
            small.add(-1, Json.obj("ev", "discard", "seat", i % 4, "tile", "1m"));
        }
        eq("回放：条数上限生效", small.peek().entries.size(), 3);
        check("回放：超限后标记 truncated", small.peek().truncated);
        ReplayRecorder tiny = new ReplayRecorder(rules.toJson(), names, 1000, 200);
        for (int i = 0; i < 20; i++) {
            tiny.add(-1, Json.obj("ev", "discard", "seat", 0, "tile", "1m", "pad", "xxxxxxxxxx"));
        }
        check("回放：字节上限生效（截断且条数远小于 20）", tiny.peek().truncated && tiny.size() < 20);

        // ---------- ④ 一小局的牌山快照 ----------
        ReplayRecorder wallRec = new ReplayRecorder(rules.toJson(), names);
        int[] wall = new int[Tiles.TILE_COUNT];
        for (int i = 0; i < wall.length; i++) {
            wall[i] = i;
        }
        wallRec.noteRound("E", 1, 0, 0, wall);
        wallRec.add(-1, Json.obj("ev", "round_end"));
        eq("回放：小局快照条目名", wallRec.peek().entries.get(0).ev(), Replay.EV_ROUND);
        eq("回放：小局索引指向快照条目", wallRec.peek().rounds.get(0).intValue(), 0);
        eq("回放：小局数 = 牌山快照数", wallRec.peek().walls.size(), 1);
        eq("回放：牌山长度 136", wallRec.peek().walls.get(0).length, Tiles.TILE_COUNT);
        eq("回放：牌山形状 4 行 × 34 列（index → row/col）", Replay.wallRow(5) * 34 + Replay.wallCol(5) * 1, 1 * 34 + 1);

        // ---------- ⑤ 真实整场录制 + 落盘 + 分块读取 + 重启加载 ----------
        java.nio.file.Path dir = null;
        ReplayStore prev = ReplayStore.current();
        try {
            dir = java.nio.file.Files.createTempDirectory("mj-replay");
            ReplayStore store = new ReplayStore(dir, 3, 16 * 1024 * 1024, true);
            ReplayStore.install(store);

            Table t = new Table("REPLAY", "回放桌", rules);
            t.botDelayMs = 0;
            t.roundDelayMs = 0;
            t.seedBase = 20260914L;
            t.debugDeterministicSeed = true;   // 自检要可复现；生产路径是每局重取时刻种子
            for (int i = 0; i < 4; i++) {
                t.addBot(i);
            }
            t.playGame();

            eq("回放：整场结束后库里恰好 1 场", store.count(), 1);
            List<Object> list = store.list(0, 10);
            eq("回放：列表返回 1 条元信息", list.size(), 1);
            @SuppressWarnings("unchecked")
            Map<String, Object> meta = (Map<String, Object>) list.get(0);
            String rid = Json.str(meta, "id", "");
            check("回放：列表里的 ID 合法：" + rid, ReplayRecorder.validId(rid));
            check("回放：元信息不含操作（列表要小）", !meta.containsKey("entries")
                    || meta.get("entries") instanceof Number);
            check("回放：元信息带局数：" + meta.get("rounds"),
                    Json.i(meta, "rounds", 0) >= 1);
            // 房间牌谱（用户要求）：列表元信息里要有房间号，玩家才能按"哪一桌"找那一场
            eq("回放：元信息带房间号（房间保存牌谱）", Json.str(meta, "room", ""), "REPLAY");

            Map<String, Object> head = store.header(rid);
            check("回放：能取到头信息", head != null);
            List<Object> walls = Json.list(head, "walls");
            eq("回放：牌山快照数 = 小局数", walls.size(), Json.i(head, "rounds", 0));
            boolean wallOk = true;
            for (Object o : walls) {
                List<Object> w = Json.asArr(o);
                boolean[] seen = new boolean[Tiles.TILE_COUNT];
                for (Object x : w) {
                    int v = ((Number) x).intValue();
                    if (v < 0 || v >= Tiles.TILE_COUNT || seen[v]) {
                        wallOk = false;
                    } else {
                        seen[v] = true;
                    }
                }
                if (w.size() != Tiles.TILE_COUNT) {
                    wallOk = false;
                }
            }
            check("回放：每小局牌山都是 136 张且 id 不重复", wallOk);

            int total = Json.i(head, "entries", 0);
            check("回放：整场条数处于合理区间（> 100）：" + total, total > 100);
            // 分页取全（服务端单次上限 2000 条，客户端就按这个翻页）
            List<Object> full = new ArrayList<>();
            for (int from = 0; from < total; from += 2000) {
                full.addAll(store.slice(rid, from, 2000));
            }
            eq("回放：分页取全 == 总条数", full.size(), total);
            eq("回放：单次分块上限 2000（钳制而不是报错）", store.slice(rid, 0, 99999).size(),
                    Math.min(2000, total));
            List<Object> chunkA = store.slice(rid, 0, 7);
            List<Object> chunkB = store.slice(rid, 7, 7);
            boolean chunkOk = chunkA.size() == 7;
            for (int i = 0; i < chunkA.size(); i++) {
                chunkOk = chunkOk && Json.write(chunkA.get(i)).equals(Json.write(full.get(i)));
            }
            for (int i = 0; i < chunkB.size(); i++) {
                chunkOk = chunkOk && Json.write(chunkB.get(i))
                        .equals(Json.write(full.get(7 + i)));
            }
            check("回放：分块取与整体取逐条一致（翻页不会错位）", chunkOk);
            eq("回放：越界分块返回空而不是报错", store.slice(rid, total + 100, 50).size(), 0);
            check("回放：非法 ID 一律查不到（路径安全）",
                    store.header("../../x") == null && store.slice("short", 0, 10) == null);

            // 语义不变量：每条 discard 的牌必须是该家**之前拿到的**。
            // ⚠ 起点不能只算 `draw`：配牌那 13 张（庄家 14 张）**没有 draw 事件**，
            //   它们只出现在该座位自己的 `round_start.hand` 里（见 AGENTS §2.3-4）。
            int[] perSeat = new int[4 * Tiles.TILE_COUNT];
            int discards = 0;
            int badDiscard = -1;
            int draws = 0;
            for (Object o : full) {
                @SuppressWarnings("unchecked")
                Map<String, Object> entry = (Map<String, Object>) o;
                @SuppressWarnings("unchecked")
                Map<String, Object> b = (Map<String, Object>) entry.get("b");
                String ev = Json.str(b, "ev", "");
                int seat = Json.i(b, "seat", -1);
                if ("round_start".equals(ev) && seat >= 0) {
                    for (String ts : Json.strList(b, "hand")) {
                        int k = Tiles.parseKind(ts);
                        if (k >= 0) {
                            perSeat[seat * Tiles.TILE_COUNT + k]++;
                        }
                    }
                    continue;
                }
                String tile = Json.str(b, "tile", null);
                if ("draw".equals(ev) && seat >= 0 && tile != null) {
                    int k = Tiles.parseKind(tile);
                    if (k >= 0) {
                        perSeat[seat * Tiles.TILE_COUNT + k]++;
                        draws++;
                    }
                } else if ("discard".equals(ev) && seat >= 0 && tile != null) {
                    int k = Tiles.parseKind(tile);
                    discards++;
                    if (k < 0 || perSeat[seat * Tiles.TILE_COUNT + k] <= 0) {
                        badDiscard = seat;
                    } else {
                        perSeat[seat * Tiles.TILE_COUNT + k]--;
                    }
                }
            }
            check("回放：整场记到了摸牌（" + draws + " 次）", draws > 50);
            check("回放：记到了出牌（" + discards + " 次）", discards > 50);
            eq("回放：每条出牌的牌都是该家之前拿到的（含配牌）", badDiscard, -1);

            // ---------- ⑥ 重启加载（索引重建：只读每个文件的第一行） ----------
            // 先塞两场"只有 1 条"的假记录，把库填到上限（3）——整场那场仍在库里。
            for (int i = 0; i < 2; i++) {
                Replay extra = new Replay(ReplayRecorder.newId(), System.currentTimeMillis() + i,
                        rules.toJson(), names);
                extra.entries.add(new Replay.Entry(0, 0, -1, Json.obj("ev", "round_end")));
                store.put(extra);
            }
            eq("回放：塞到上限后仍是 3 场", store.count(), 3);
            ReplayStore reloaded = new ReplayStore(dir, 3, 16 * 1024 * 1024, true);
            eq("回放：重启后仍能列出 3 场", reloaded.count(), 3);
            // 列表里挑**有牌山的那一场**（两场假记录只有 1 条、没有小局）
            String fid = "";
            for (Object o : reloaded.list(0, 10)) {
                @SuppressWarnings("unchecked")
                Map<String, Object> m = (Map<String, Object>) o;
                if (Json.i(m, "rounds", 0) >= 1) {
                    fid = Json.str(m, "id", "");
                    break;
                }
            }
            check("回放：重启后列表里能找到整场记录", !fid.isEmpty());
            eq("回放：重启后仍能取到操作（" + fid + "）", reloaded.slice(fid, 0, 5).size(), 5);
            check("回放：重启后仍能取到牌山",
                    reloaded.wall(fid, 0) != null && reloaded.wall(fid, 0).size() == Tiles.TILE_COUNT);

            // ---------- ⑦ 容量淘汰（在 reloaded 上做，免得把上面两处要用的记录删掉） ----------
            Replay third = new Replay(ReplayRecorder.newId(), System.currentTimeMillis() + 99,
                    rules.toJson(), names);
            third.entries.add(new Replay.Entry(0, 0, -1, Json.obj("ev", "round_end")));
            reloaded.put(third);
            eq("回放：场数上限生效（max 3）", reloaded.count(), 3);
            check("回放：最旧的一场被淘汰（头信息已查不到）", reloaded.header(rid) == null);
            check("回放：淘汰会删文件", !java.nio.file.Files.exists(dir.resolve(rid + ".replay")));
            check("回放：新记录仍在（淘汰的是最旧的）", reloaded.header(third.id) != null);

            // ---------- ⑧ 关闭时零开销 ----------
            ReplayStore off = new ReplayStore(dir, 3, 1024 * 1024, false);
            check("回放：关闭态不落盘、不列表、不读取",
                    off.put(new Replay(ReplayRecorder.newId(), 0, rules.toJson(), names)) == null
                            && off.list(0, 10).isEmpty() && off.slice(fid, 0, 5) == null);
        } catch (java.io.IOException e) {
            failures.add("回放用例 IO 异常: " + e);
            fail++;
        } finally {
            ReplayStore.install(prev);
            if (dir != null) {
                try (java.util.stream.Stream<java.nio.file.Path> walk = java.nio.file.Files.walk(dir)) {
                    walk.sorted(java.util.Comparator.reverseOrder()).forEach(p -> {
                        try {
                            java.nio.file.Files.deleteIfExists(p);
                        } catch (java.io.IOException ignore) {
                            // 清理失败不影响自检结论
                        }
                    });
                } catch (java.io.IOException ignore) {
                    // 同上
                }
            }
        }
    }

    /**
     * 赤宝牌张数：默认 3 张；`rules.aka = 0` 时**整副牌山都不含赤五**。
     *
     * <p>审计出来的问题正是"注释与实现不一致"：`Rules.akaKinds()` 全仓无调用者，
     * 设 `aka = 0` 仍会发赤五、仍记赤宝牌番数（AGENTS §8 那句"0 或 3 张"对 0 不成立）。
     * 换掉赤五不能改变牌张构成，所以顺带钉住"每种牌恒 4 张"。
     */
    /**
     * 开局前的**自选座位 / 随机洗座**（用户要求：门风 = 座次，可自选或随机）。
     *
     * <p>三条不变量：
     *   ① `take_seat` 是**互换**——两家的住户信息整体对调，谁也不被挤掉；
     *   ② 换过座位的两家 `ready` 都要清掉（座位变了，"准备好了"不再指同一个位置）；
     *   ③ `shuffle_seats` 之后**四家的住户正好是原来那四家**（一个不多一个不少），
     *      否则洗座会凭空产生或吞掉一个玩家（这是最容易写错的地方：逐字段交换漏一项）。
     */
    private static void seatSwapTests() {
        Table t = new Table("SEAT", "座位桌", Rules.defaults());
        // 四个座位放四个"人"（名字/pid 各不相同，便于查住户是否整套搬过去）。
        // ⚠ pid 不能用 0：`Seat.occupied()` 把 pid==0 当成"空位"（机器人也用 0），
        //   用 0 会让"按 pid 反查座位"恒返回 -1。
        for (int i = 0; i < 4; i++) {
            t.seats[i].name = "P" + i;
            t.seats[i].pid = 101 + i;
            t.seats[i].score = 25000 + i;
            t.seats[i].ready = true;
        }
        t.swapSeats(0, 2);
        eq("换座：座位 0 的住户搬到 2", t.seats[2].name, "P0");
        eq("换座：座位 2 的住户搬到 0", t.seats[0].name, "P2");
        eq("换座：pid 跟着走", t.seats[0].pid, 103L);
        eq("换座：分数跟着走", t.seats[0].score, 25002);
        eq("换座：两家都要重新准备（0）", t.seats[0].ready, false);
        eq("换座：两家都要重新准备（2）", t.seats[2].ready, false);
        eq("换座：没动的座位不受影响", t.seats[1].name, "P1");
        // 按 pid 反查座位（洗座后各连接靠它重新认领）
        eq("按 pid 反查座位", t.seatOfPid(103L), 0);
        eq("按 pid 查不到时返回 -1", t.seatOfPid(999L), -1);
        // 非法参数不改变任何状态
        t.swapSeats(0, 0);
        t.swapSeats(-1, 2);
        t.swapSeats(0, 9);
        eq("非法换座参数不改状态", t.seats[0].name, "P2");

        // 洗座：住户集合不变（只是换了位置）
        t.shuffleSeats();
        String[] names = new String[4];
        for (int i = 0; i < 4; i++) {
            names[i] = t.seats[i].name;
        }
        java.util.Arrays.sort(names);
        eq("洗座后四家住户一个不多一个不少",
                String.join(",", names), "P0,P1,P2,P3");
        boolean allReady = false;   // 人类座位（这里都是"人"）必须重新准备
        for (int i = 0; i < 4; i++) {
            allReady = allReady || t.seats[i].ready;
        }
        eq("洗座后人类座位重新准备", allReady, false);
    }

    /**
     * 副露的**赤宝选择**（用户要求：副露能不能选赤宝）。
     *
     * <p>赤五（`0m`）与普通五（`5m`）**同 kind 不同价值**，所以"拿哪一张去碰/吃"是
     * 玩家该决定的事。客户端用精确牌码表达（`0m` = 赤、`5m` = 普通），服务端照它挑。
     *
     * <p>四条不变量：
     *   ① 按客户端指定的赤/普通取到对应的那一张；
     *   ② 客户端要的牌手里没有（要两张赤五却只有一张）→ 作废，**绝不拿普通五顶上**；
     *   ③ 牌种对不上（拿 6m 的码去碰 5m）→ 作废；
     *   ④ 个数不符 → 作废（调用方退回旧行为）。
     */
    private static void meldAkaPickTests() {
        Round r = newRound();
        final int aka5 = Tiles.id(Tiles.AKA_M, 0);       // 赤五固定 copy 0
        final int norm5a = Tiles.id(Tiles.AKA_M, 1);
        final int norm5b = Tiles.id(Tiles.AKA_M, 2);
        final int called5 = Tiles.id(Tiles.AKA_M, 3);    // 被鸣的那张
        r.hand[0].clear();
        r.hand[0].add(aka5);
        r.hand[0].add(norm5a);
        r.hand[0].add(norm5b);

        int[] wantNorm = r.debugPickHandTiles(0, called5, Arrays.asList("5m", "5m"), 2);
        check("赤宝选择：要普通五就取普通五", wantNorm != null
                && !Tiles.isRedId(wantNorm[0]) && !Tiles.isRedId(wantNorm[1]));
        int[] wantMix = r.debugPickHandTiles(0, called5, Arrays.asList("0m", "5m"), 2);
        int reds = 0;
        if (wantMix != null) {
            for (int id : wantMix) {
                if (Tiles.isRedId(id)) {
                    reds++;
                }
            }
        }
        eq("赤宝选择：赤+普通各一张", reds, 1);
        eq("赤宝选择：要两张赤五但只有一张 → 作废",
                r.debugPickHandTiles(0, called5, Arrays.asList("0m", "0m"), 2), null);
        eq("赤宝选择：牌种对不上要作废",
                r.debugPickHandTiles(0, called5, Arrays.asList("6m", "5m"), 2), null);
        eq("赤宝选择：个数不符要作废",
                r.debugPickHandTiles(0, called5, Arrays.asList("5m"), 2), null);
        eq("赤宝选择：按赤/普通能精确取到那一张",
                r.debugFindHandTile(0, Tiles.AKA_M, true), aka5);
    }

    /**
     * 出牌对齐（幽灵手牌的正面防线）：**牌码决定打哪张，`tsumogiri` 只决定去哪一摞里找**。
     *
     * <p>报障现象是「庄家第一巡点手里的牌/摸牌位，服务端却打了另一张」，之后两端手牌
     * **张数相同、内容差一张**（越打越歪）。根因两条：
     * <ol>
     *   <li>庄家第一巡的 14 张配牌是**已排序**发下去的，客户端按"最后一张 = 刚摸到的"
     *       去认摸牌位，而服务端的 `drawn` 是第 14 张 `openingTile`（排序后通常在中间）——
     *       两端对"摸到的是哪张"认知不同；</li>
     *   <li>旧的服务端在「声明了摸切但牌码对不上」时退回**默认摸切**，也就是打出刚摸到的
     *       那张 —— 恰恰是玩家没点的那张牌，而客户端已经按自己的点击扣了牌。</li>
     * </ol>
     * 这里把第 2 条的判据逐条钉死；第 1 条由「庄家 `round_start` 必须点名 `drawn`」钉死。
     */
    private static void discardAlignTests() {
        final int aka5 = Tiles.id(Tiles.AKA_M, 0);       // 赤五（copy 0）
        final int norm5a = Tiles.id(Tiles.AKA_M, 1);
        final int norm5b = Tiles.id(Tiles.AKA_M, 2);
        final int p7 = Tiles.id(15, 0);                  // 7p
        final int s9 = Tiles.id(26, 0);                  // 9s
        List<Integer> hand = new ArrayList<>(List.of(s9, norm5a, aka5, p7, norm5b));

        // ① 声明摸切且牌码吻合 → 就是摸牌位那张
        eq("出牌对齐：摸切且牌码吻合 → 取摸牌位那张",
                Round.pickDiscardId(hand, norm5b, "5m", true), norm5b);
        // ② **声明摸切但牌码对不上** → 按牌码当手切（绝不退回"打刚摸到的那张"）
        //    这正是客户端把已排序手牌的最后一张当成摸牌位时的情形。
        eq("出牌对齐：摸切声明对不上 → 按牌码取，绝不打摸到的那张",
                Round.pickDiscardId(hand, norm5b, "9s", true), s9);
        eq("出牌对齐：摸切声明对不上（要的是中间那张）→ 按牌码取",
                Round.pickDiscardId(hand, norm5b, "7p", true), p7);
        // ③ 不提摸切 → 同样按牌码
        eq("出牌对齐：手切按牌码", Round.pickDiscardId(hand, norm5b, "7p", false), p7);
        // ④ 赤五与普通五必须分得开（同 kind 不同牌码）
        eq("出牌对齐：要普通 5m → 普通那张", Round.pickDiscardId(hand, -1, "5m", false), norm5a);
        eq("出牌对齐：要赤 0m → 赤那张", Round.pickDiscardId(hand, -1, "0m", false), aka5);
        // ⑤ 手里只剩赤五时要 5m → 退回赤五（否则玩家点这张就成了非法动作）
        eq("出牌对齐：只有赤五时要 5m → 退回赤五",
                Round.pickDiscardId(new ArrayList<>(List.of(aka5)), -1, "5m", false), aka5);
        // ⑥ 牌码不在手里 → -1（调用方这才退回默认摸切）
        eq("出牌对齐：牌码不在手里 → -1", Round.pickDiscardId(hand, -1, "1z", false), -1);
        eq("出牌对齐：声称摸切但牌码也不在手里 → -1",
                Round.pickDiscardId(hand, norm5b, "1z", true), -1);
        // ⑦ 本巡没摸牌（drawn < 0）时"声明摸切"没有意义 → 仍按牌码
        eq("出牌对齐：无摸牌时按牌码", Round.pickDiscardId(hand, -1, "7p", true), p7);

        // ⑧ 庄家 `round_start` 必须点名 `drawn`，且就是第 14 张；闲家不带这个字段
        Round r = newRound();                       // 座位 0 是庄
        r.debugSetup();                             // 配牌在 play() 里，这里手动跑一遍
        Map<String, Object> dealerEv = r.debugRoundStartEvent(r.dealer);
        List<?> dealerHand = (List<?>) dealerEv.get("hand");
        Object drawn = dealerEv.get("drawn");
        eq("出牌对齐：庄家配牌 14 张", dealerHand.size(), 14);
        check("出牌对齐：庄家 round_start 必须带 drawn", drawn != null);
        eq("出牌对齐：drawn 就是第 14 张 openingTile",
                String.valueOf(drawn), Tiles.toStr(r.debugOpeningTile()));
        check("出牌对齐：drawn 必须真的在 hand 里", dealerHand.contains(drawn));
        Map<String, Object> otherEv = r.debugRoundStartEvent((r.dealer + 1) % 4);
        check("出牌对齐：闲家 round_start 不带 drawn", otherEv.get("drawn") == null);
        eq("出牌对齐：闲家配牌 13 张", ((List<?>) otherEv.get("hand")).size(), 13);

        // ⑨ 兜底自愈：牌码**永远**决定服务端打哪张（哪怕客户端错报了摸切）
        //    —— 只要牌码还在手里，两端就不会各留一张不同的牌。
        //    ⚠ 牌码与"刚摸到的那张"**同码**时（这里是 5m ≡ norm5b）声明摸切是对的，
        //      不在本条覆盖范围：那种情况取摸牌位那张，牌码相同，不影响两端一致。
        for (int code : new int[]{s9, aka5, p7}) {
            String s = Tiles.toStr(code);
            eq("出牌对齐：报 " + s + " 就一定打 " + s,
                    Round.pickDiscardId(hand, norm5b, s, true), code);
        }
    }

    private static void akaRuleTests() {
        int[] def = new mahjong.core.Wall(20240914L, Rules.defaults()).debugAllTiles();
        int red = 0;
        for (int id : def) {
            if (Tiles.isRedId(id)) {
                red++;
            }
        }
        eq("默认（aka=3）牌山里的赤五张数", red, 3);

        Rules noAka = new Rules();
        noAka.aka = 0;
        int[] none = new mahjong.core.Wall(20240914L, noAka).debugAllTiles();
        int red2 = 0;
        int[] perKind = new int[Tiles.KIND_COUNT];
        for (int id : none) {
            if (Tiles.isRedId(id)) {
                red2++;
            }
            perKind[Tiles.kind(id)]++;
        }
        eq("aka=0 时牌山里的赤五张数", red2, 0);
        eq("aka=0 时牌山仍是 136 张", none.length, 136);
        boolean fourEach = true;
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (perKind[k] != 4) {
                fourEach = false;
            }
        }
        check("aka=0 时每种牌仍是 4 张（换掉赤五不改变牌张构成）", fourEach);
    }

    private static void dropRepliesTests() {
        Table t = new Table("selftest-drop", "自检", new Rules());

        // ⚠ 两条「都是 pass」的消息内容会相等，Map.equals 分不出是哪一条 ——
        //   所以各带一个 tag，断言才真的在断"哪一条被摘了"。
        Map<String, Object> lateNoId = Json.obj("cmd", "action", "type", "pass", "tag", "no-id");
        Map<String, Object> lateSameAsk =
                Json.obj("cmd", "action", "type", "pass", "ask_id", 7L, "tag", "same-ask");
        Map<String, Object> otherAsk =
                Json.obj("cmd", "action", "type", "pass", "ask_id", 9L, "tag", "other-ask");
        Map<String, Object> otherSeat = Json.obj("cmd", "action", "type", "pass", "tag", "seat2");
        Map<String, Object> notAnAction = Json.obj("cmd", "confirm", "tag", "confirm");

        t.submit(1, lateNoId);       // 该摘：老客户端，没带 ask_id
        t.submit(1, lateSameAsk);    // 该摘：ask_id 正是被取消的那个
        t.submit(1, otherAsk);       // 该留：属于另一次询问
        t.submit(2, otherSeat);      // 该留：别的座位
        t.submit(1, notAnAction);    // 该留：不是动作（confirm 有自己的消费者）

        eq("dropReplies 摘掉 2 条", t.dropReplies(1, 7L), 2);

        List<Map<String, Object>> rest = new ArrayList<>();
        Object[] r;
        while ((r = t.pollResponse(2)) != null) {
            @SuppressWarnings("unchecked")
            Map<String, Object> m = (Map<String, Object>) r[1];
            rest.add(m);
        }
        eq("队列里还剩 3 条", rest.size(), 3);
        check("属于另一次询问的回包没被误删", rest.contains(otherAsk));
        check("别的座位的回包没被误删", rest.contains(otherSeat));
        check("非动作消息（confirm）没被误删", rest.contains(notAnAction));
        check("该摘的两条确实没了",
                !rest.contains(lateNoId) && !rest.contains(lateSameAsk));
    }

    // ------------------------------------------------- 报文编码（UTF-8 / 中文）
    /**
     * 报文**确实会带中文**（役种名、limit、流局 reason、玩家名、聊天都是原样文本），
     * 所以编码链必须钉死 UTF-8，且截断不能切坏代理对。这里把 Json 这一层钉住。
     */
    private static void jsonEncodingTests() {
        // 1) 中文**原样写出**，不做 Unicode 转义（PROTOCOL：全程 UTF-8）
        //    ⚠ 注释里也别写「反斜杠 + u」——javac 在词法分析**之前**就处理 Unicode 转义，
        //      注释里的非法序列同样报「非法 Unicode 转义」（这个坑刚踩过）。
        String cn = "立直・门前清自摸和・満貫";
        String w = Json.write(Json.obj("name", cn));
        check("中文原样写出（不转义成 \\uXXXX）: " + w, w.contains(cn));

        // 2) 往返一致，且**代理对**（BMP 之外）不被破坏
        String smp = "\uD842\uDFB7\uD83C\uDC04";      // 𠮷(U+20BB7) + 🀄(U+1F004)
        eq("代理对的码点数（按字符算，不是码元）", smp.codePointCount(0, smp.length()), 2);
        eq("代理对往返一致",
                Json.str(Json.asObj(Json.parse(Json.write(Json.obj("t", smp)))), "t", ""), smp);

        // 3) 解析 Unicode 转义（别的实现 / 老客户端可能这么发）
        eq("解析 \\u4e2d → 中",
                Json.str(Json.asObj(Json.parse("{\"t\":\"\\u4e2d\"}")), "t", ""), "中");

        // 4) 按码点截断：**绝不能**把代理对切成半个（切了会编码成 '?'）
        eq("clampCodePoints 不切代理对", Json.clampCodePoints("a\uD83C\uDC04b", 2), "a\uD83C\uDC04");
        eq("clampCodePoints 中文按字符计", Json.clampCodePoints("一二三四五", 3), "一二三");
        eq("clampCodePoints 未超限原样返回", Json.clampCodePoints("一二", 5), "一二");
        // 旧实现按 UTF-16 码元 substring：199 个 a + 🀄 会被切成 199a + 半个 emoji
        StringBuilder longText = new StringBuilder();
        for (int i = 0; i < 199; i++) {
            longText.append('a');
        }
        longText.append('\uD83C').append('\uDC04');
        for (int i = 0; i < 20; i++) {
            longText.append('b');
        }
        String cut = Json.clampCodePoints(longText.toString(), 200);
        check("截断后不含孤立代理（否则 UTF-8 编码成 '?'）",
                cut.codePointCount(0, cut.length()) == 200 && cut.endsWith("\uD83C\uDC04"));
    }

    // ------------------------------------------------- 协议词汇表（中文 → ASCII 码）
    /**
     * 报文里不出现中文（PROTOCOL §0）：`Evaluator` 内部用中文名，上报前经 {@link YakuCodes} 换成码。
     * 这里钉两件事：① 词汇表覆盖全部役种（含参数化那三种）；② 码本身是 ASCII。
     * ③ 整场模拟跑完 `misses() == 0`（有中文名漏登记就会 >0）。
     */
    private static void yakuCodesTests() {
        Map<String, String> vocab = YakuCodes.vocabulary();
        // 58 = Evaluator 里全部**字面**役种名；「役牌/场风/自风 X」是参数化的，走前缀不在表内。
        // 定成**精确值**当绊线：新增役种忘了登记 → 这里先红。
        eq("役种词汇表规模（新增役种必须登记）", vocab.size(), 58);

        // 参数化役种：码是角色、牌码另给
        eq("役牌 → 码", YakuCodes.codeOf("役牌 白"), YakuCodes.YAKUHAI);
        eq("役牌 → 牌码", YakuCodes.tileOf("役牌 白"), "5z");
        eq("场风 → 码", YakuCodes.codeOf("场风 东"), YakuCodes.ROUND_WIND);
        eq("场风 → 牌码", YakuCodes.tileOf("场风 东"), "1z");
        eq("自风 → 码", YakuCodes.codeOf("自风 北"), YakuCodes.SEAT_WIND);
        eq("自风 → 牌码", YakuCodes.tileOf("自风 北"), "4z");
        eq("普通役种没有牌码", YakuCodes.tileOf("立直"), null);

        // 全部中文名（含古役）都必须有码，且码必须是 ASCII
        for (Map.Entry<String, String> e : vocab.entrySet()) {
            String code = YakuCodes.codeOf(e.getKey());
            check("役种「" + e.getKey() + "」→ " + code,
                    code != null && !YakuCodes.UNKNOWN.equals(code) && isAscii(code));
        }

        // 打点档位 / 流局原因
        eq("档位 满贯", YakuCodes.limitOf("满贯"), "mangan");
        eq("档位 累计役满", YakuCodes.limitOf("累计役满"), "kazoe_yakuman");
        eq("档位 两倍役满 → yakuman（倍数在 yakuman 字段）", YakuCodes.limitOf("两倍役满"), "yakuman");
        eq("档位 空", YakuCodes.limitOf(""), "");
        eq("原因 荒牌流局", YakuCodes.reasonOf("荒牌流局"), "exhaustive");
        eq("原因 四杠散了", YakuCodes.reasonOf("四杠散了"), "four_kans");
        eq("认不出的原因回空串", YakuCodes.reasonOf("???"), "");
    }

    /** 字符串是否纯 ASCII（协议文本的硬要求）。 */
    private static boolean isAscii(String s) {
        for (int i = 0; i < s.length(); i++) {
            if (s.charAt(i) > 0x7F) {
                return false;
            }
        }
        return true;
    }

    // ------------------------------------------------------------- 连庄判据

    /**
     * 每小局的种子必须**重新取**（用户报障：「同一房间每个半庄种子一样」）。
     *
     * <p>原来每局是 `mixSeed(seedBase + 局序号)`：同一个 `seedBase` 下整场是一条确定序列，
     * 推出一局就能推出一整场。现在生产路径每局从"当前时刻毫秒数"重新起步。
     *
     * <p>同时钉住**自检那条岔路**：`debugDeterministicSeed` 打开时必须完全可复现，
     * 否则模拟类自检会变成随机样本（那是自检质量下降，不是需求本意）。
     */
    private static void roundSeedTests() {
        // ① 生产路径：同一张桌子连取几局，种子各不相同（哪怕都在同一毫秒内）
        Table a = new Table("SEED", "种子桌", Rules.defaults());
        long s1 = a.debugNextRoundSeed();
        long s2 = a.debugNextRoundSeed();
        long s3 = a.debugNextRoundSeed();
        check("每局种子互不相同： " + s1 + "/" + s2 + "/" + s3,
                s1 != s2 && s2 != s3 && s1 != s3);
        check("每局种子来源（时刻）在推进", a.debugRoundSeedClock() > 0);

        // ② 两张**基准不同**的桌子不会撞出同一副牌（seedBase 是 CSPRNG）
        Table b = new Table("SEED2", "种子桌2", Rules.defaults());
        b.seedBase = a.seedBase ^ 0x5DEECE66DL;
        check("不同基准的桌子种子不同", a.debugNextRoundSeed() != b.debugNextRoundSeed());

        // ③ 自检岔路：写死基准 + deterministic ⇒ 整场可复现
        Table c = new Table("SEED3", "种子桌3", Rules.defaults());
        c.seedBase = 777L;
        c.debugDeterministicSeed = true;
        Table d = new Table("SEED4", "种子桌4", Rules.defaults());
        d.seedBase = 777L;
        d.debugDeterministicSeed = true;
        boolean same = true;
        for (int i = 0; i < 5; i++) {
            if (c.debugNextRoundSeed() != d.debugNextRoundSeed()) {
                same = false;
            }
        }
        check("自检岔路：同基准同序号 → 整场可复现", same);

        // ④ 但同一张桌子上相邻两局仍然不同（不是退化成同一个种子）
        Table e = new Table("SEED5", "种子桌5", Rules.defaults());
        e.seedBase = 777L;
        e.debugDeterministicSeed = true;
        check("自检岔路内部也逐局不同", e.debugNextRoundSeed() != e.debugNextRoundSeed());
    }

    private static void roundScoringTests() {
        // 和了：庄家和了才连庄
        check("庄家自摸/荣和 → 连庄", RoundScoring.winBy(0, 0));
        check("闲家和了 → 轮庄", !RoundScoring.winBy(0, 2));
        check("多家和了含庄家 → 连庄",
                RoundScoring.winBy(1, Arrays.asList(3, 1)));
        check("多家和了不含庄家 → 轮庄",
                !RoundScoring.winBy(1, Arrays.asList(3, 2)));

        // 流局满贯的连庄：**按庄家是否听牌**（文档 L1139），不是"成立者里有没有庄家"。
        // ⚠ 这两条断言原来是按错的行为写的（`nagashiBy(dealer, 成立者列表)`），
        //   AUDIT S-55 修行为时一并改过来 —— 这正是"断言把 bug 写成规格"的又一例。
        boolean[] ngTp = {true, false, false, false};
        boolean[] ngNoten = {false, true, true, true};
        check("流局满贯：庄家成立且听牌 → 连庄", RoundScoring.nagashiBy(0, ngTp));
        check("流局满贯：庄家成立但**不听** → 轮庄（旧实现会连庄）",
                !RoundScoring.nagashiBy(0, ngNoten));
        check("流局满贯：庄家没成立但听牌 → 照样连庄（旧实现会轮庄）",
                RoundScoring.nagashiBy(0, new boolean[]{true, false, false, false}));

        // 荒牌流局：庄家听牌才连庄
        boolean[] tp = {true, false, false, false};
        check("荒牌流局庄家听牌 → 连庄", RoundScoring.exhaustiveBy(0, tp));
        boolean[] tp2 = {false, true, false, false};
        check("荒牌流局庄家不听 → 轮庄", !RoundScoring.exhaustiveBy(0, tp2));

        // 流局满贯成立：全部幺九、且牌河未被鸣走
        List<Integer> yaochu = Arrays.asList(
                Tiles.id(0, 0), Tiles.id(8, 0), Tiles.id(9, 0), Tiles.id(27, 0));
        check("全幺九且未被鸣 → 成立", RoundScoring.nagashiEligible(yaochu, false));
        check("牌河被鸣走过 → 不成立", !RoundScoring.nagashiEligible(yaochu, true));
        check("打过中张 → 不成立",
                !RoundScoring.nagashiEligible(Arrays.asList(Tiles.id(0, 0), Tiles.id(4, 0)), false));
        check("空牌河 → 不成立", !RoundScoring.nagashiEligible(Arrays.asList(), false));

        // 流局满贯支付：庄家 4000 全付；闲家 庄付 4000、闲各付 2000；四家和恒为 0
        int[] dw = RoundScoring.nagashiPayments(0, 0);
        eq("庄家满贯：三家各付 4000", Arrays.toString(dw), "[12000, -4000, -4000, -4000]");
        int[] pw = RoundScoring.nagashiPayments(2, 0);
        eq("闲家满贯：庄付 4000、闲各付 2000", Arrays.toString(pw), "[-4000, -2000, 8000, -2000]");
        check("满贯支付守恒（和为 0）",
                dw[0] + dw[1] + dw[2] + dw[3] == 0 && pw[0] + pw[1] + pw[2] + pw[3] == 0);
        // 形式听牌：只看牌形，不看役/振听
        check("123456789m + 444 + 5m 听牌",
                RoundScoring.tenpai(countsOf("1m1m1m2m2m2m3m3m3m4m4m4m5m"), 0));
        check("全孤张不听牌",
                !RoundScoring.tenpai(countsOf("1m3m5m7m9m1p3p5p7p9p1s3s5s"), 0));
        check("一副露时按 11 张判定听牌",
                RoundScoring.tenpai(countsOf("1m1m1m2m2m2m3m3m3m4m4m"), 1));
        // 途中流局一律连庄
        check("途中流局 → 连庄", RoundScoring.abortive());

        // ---------- 本场数真值表（审计 S-46：原来中途流局不加、荒牌流局轮庄反而清零）
        eq("和了连庄 → 本场 +1", RoundScoring.nextHonba(0, true, true, false), 1);
        eq("和了连庄（已有本场）→ 本场 +1", RoundScoring.nextHonba(3, true, true, false), 4);
        eq("闲家和了轮庄 → 本场清零", RoundScoring.nextHonba(2, false, true, false), 0);
        eq("荒牌流局庄家不听轮庄 → 本场 +1（**不清零**）",
                RoundScoring.nextHonba(0, false, false, false), 1);
        eq("荒牌流局庄家不听轮庄（已有本场）→ 本场 +1",
                RoundScoring.nextHonba(2, false, false, false), 3);
        eq("中途流局（连庄）→ 本场 +1", RoundScoring.nextHonba(1, true, false, false), 2);
        eq("流局满贯轮庄 → 本场清零（按和了处理）", RoundScoring.nextHonba(2, false, false, true), 0);

        // ---------- 端到端：Table 真的按这条判据推进本场（接线检查，不是重算公式）
        // 只看"流局之后那一局"：本场**必须 +1**（旧实现会清零）。这条断言不复用被测函数，
        // 而是把规则原样写出来，所以能抓到"判据写对了但没接上"。
        List<List<int[]>> games = new ArrayList<>();   // 每场一份 {本场, 是否和了}
        for (int g = 0; g < 2; g++) {
            final List<int[]> honbaSeq = new ArrayList<>();
            games.add(honbaSeq);
            Table t = new Table("HONBA" + g, "本场桌", Rules.defaults());
            t.botDelayMs = 0;
            t.roundDelayMs = 0;
            t.debugDeterministicSeed = true;
            t.debugMaxHands = 6;
            t.seedBase = 31337L + g * 104729L;
            for (int i = 0; i < 4; i++) {
                // ⚠ 用**弱策略**而不是 teacher：teacher 现在很少流局（不鸣无役手 + 留宝牌），
                //   几局里碰不到一次流局，这条"非空转"断言就会红（第一次跑就是这样）。
                //   `firstLegal` 每巡打第一张合法牌 → 流局成常态，且顺带覆盖策略注入那条路。
                t.policy[i] = mahjong.ai.Policies.fromAction(mahjong.ai.Policies.firstLegal());
                t.addBot(i);
            }
            t.debugEventTap = (recipient, ev) -> {
                if (recipient == -1 && "round_end".equals(Json.str(ev, "ev", ""))) {
                    Map<String, Object> rd = Json.map(ev, "round");
                    honbaSeq.add(new int[]{Json.i(rd, "honba", 0),
                            Json.bool(ev, "agari", false) ? 1 : 0});
                }
            };
            t.playGame();
        }
        int drawPairs = 0;
        boolean honbaOk = true;
        // ⚠ **逐场**比对：跨场的相邻两条不是同一局序列（上一场最后一局之后是新的一场，本场从 0 起）。
        //   第一次写成"全部场次拼成一条"时就踩了这个：报「流局之后本场应为 6，实际 0」。
        for (List<int[]> honbaSeq : games) {
            for (int i = 0; i + 1 < honbaSeq.size(); i++) {
                int[] cur = honbaSeq.get(i);
                int[] next = honbaSeq.get(i + 1);
                if (cur[1] == 0) {                    // 本局不是和了（流局）
                    drawPairs++;
                    if (next[0] != cur[0] + 1) {
                        honbaOk = false;
                        failures.add("流局之后本场应为 " + (cur[0] + 1) + "，实际 " + next[0]);
                        fail++;
                    } else {
                        pass++;
                    }
                }
            }
        }
        System.out.println("  [覆盖] 本场推进：检查了 " + drawPairs + " 次「流局 → 下一局」");
        check("样本里确实出现过流局（否则上面那条是空转）", drawPairs > 0);
        check("流局之后本场 +1（端到端）", honbaOk);
    }
    // ------------------------------------------------------------- 打点表

    /**
     * 打点表用的规则集：**《天凤》**（累计役满开、切上满贯关）。
     *
     * <p>⚠ 这张表的期望值是按《天凤》口径列的（13 番 = 累计役满 32000、3 番 60 符 = 7700 不切上），
     * 所以必须**显式**声明用哪套规则 —— 否则默认预设一换（现在是 M.League），
     * 这张表就会在"13 番该是 32000 还是 24000"上悄悄改变含义。
     */
    private static final Rules TABLE_RULES = preset("tenhou");

    /**
     * 番数 + 符数 → 基本点。
     *
     * <p>⚠ 它**不再**在这里抄一份映射，而是调 {@link Evaluator#basePoints}：
     * 原来那份副本让 `Evaluator` 真正的档位映射（6〜12 番）成了测试盲区（审计 S-68）。
     */
    private static int base(int han, int fu) {
        return Evaluator.basePoints(han, fu, TABLE_RULES);
    }

    private static Evaluator.HandScore fake(int han, int fu) {
        Evaluator.HandScore s = new Evaluator.HandScore();
        s.han = han;
        s.fu = fu;
        s.base = base(han, fu);
        s.valid = true;
        return s;
    }

    private static int ron(int han, int fu, int winner, int dealer) {
        return -Payments.compute(fake(han, fu), winner, (winner + 1) % 4, dealer, 0, 0, false, -1, 0)
                .delta[(winner + 1) % 4];
    }

    /** 闲家自摸的「庄付/闲付」两个金额（绝对值），形如 {@code "2000/1000"} 便于逐格比对。 */
    private static String tsumoNon(int han, int fu) {
        Payments.Result p = Payments.compute(fake(han, fu), 1, -1, 0, 0, 0, true, -1, 0);
        return (-p.delta[0]) + "/" + (-p.delta[2]);
    }

    /** 庄家自摸的「每家付」金额。 */
    private static int tsumoDealer(int han, int fu) {
        Payments.Result p = Payments.compute(fake(han, fu), 0, -1, 0, 0, 0, true, -1, 0);
        return -p.delta[1];
    }

    /** 指定规则集 + 宝牌指示牌的和牌评价（打点表里"档位"要真手牌覆盖时用）。 */
    private static Evaluator.HandScore evalDora(String hand, String win, boolean tsumo,
                                                boolean riichi, String doraInd, Rules rules) {
        int[] c = counts(hand);
        WinContext ctx = new WinContext();
        ctx.rules = rules;
        ctx.seat = 1;
        ctx.dealerSeat = 0;
        ctx.roundWind = 27;
        ctx.tsumo = tsumo;
        ctx.menzen = true;
        ctx.riichi = riichi;
        ctx.winKind = Tiles.parseKind(win);
        ctx.doraIndicators = new ArrayList<>();
        for (int id : parse(doraInd)) {
            ctx.doraIndicators.add(Tiles.kind(id));
        }
        ctx.uraIndicators = new ArrayList<>();
        ctx.allTileIds = parse(hand);
        return Evaluator.evaluate(ctx, c, new ArrayList<>(), ctx.winKind);
    }

    // ------------------------------------------------------------- 带副露的符（S-70）

    /** 碰（明刻）。 */
    private static Meld pon(String tile) {
        int k = Tiles.parseKind(tile);
        return new Meld(Meld.Kind.PON, new int[]{Tiles.id(k, 1), Tiles.id(k, 2), Tiles.id(k, 3)},
                0, Tiles.id(k, 0));
    }

    /** 吃（顺子，`tiles` 三张牌码）。 */
    private static Meld chi(String a, String b, String c) {
        int[] t = {Tiles.id(Tiles.parseKind(a), 1), Tiles.id(Tiles.parseKind(b), 1),
                   Tiles.id(Tiles.parseKind(c), 1)};
        java.util.Arrays.sort(t);
        return new Meld(Meld.Kind.CHI, t, 0, t[0]);
    }

    /** 杠：`wire` = `daiminkan` / `ankan`。 */
    private static Meld kan(String wire, String tile) {
        int k = Tiles.parseKind(tile);
        Meld.Kind kind = Meld.Kind.of(wire);
        return new Meld(kind, new int[]{Tiles.id(k, 0), Tiles.id(k, 1), Tiles.id(k, 2),
                                        Tiles.id(k, 3)}, kind == Meld.Kind.ANKAN ? -1 : 0,
                Tiles.id(k, 0));
    }

    /** 带副露的和牌评价（`hand` 是**含和了牌**的暗牌，副露另算）。 */
    private static Evaluator.HandScore evalOpen(String hand, List<Meld> melds, String win,
                                                boolean tsumo) {
        int[] c = counts(hand);
        WinContext ctx = new WinContext();
        ctx.rules = Rules.defaults();
        ctx.seat = 1;
        ctx.dealerSeat = 0;
        ctx.roundWind = 27;
        ctx.tsumo = tsumo;
        ctx.menzen = false;
        ctx.winKind = Tiles.parseKind(win);
        ctx.doraIndicators = new ArrayList<>();
        ctx.uraIndicators = new ArrayList<>();
        ctx.allTileIds = parse(hand);
        return Evaluator.evaluate(ctx, c, melds, ctx.winKind);
    }

    private static void scoreTableTests() {
        // 闲家荣和
        eq("闲1番30符荣", ron(1, 30, 1, 0), 1000);
        eq("闲1番40符荣", ron(1, 40, 1, 0), 1300);
        eq("闲1番50符荣", ron(1, 50, 1, 0), 1600);
        // 注：文档打点表的「闲1番60符」一栏自相矛盾（表里写 2900，与它自己的自摸栏 500/1000 不符）。
        // 按公式：60 × 2^(2+1) = 480 → 闲家荣和 480×4 = 1920 → 切上 100 → **2000**（与自摸栏自洽）。
        // ⚠ 这一行曾经被行内的字面 `n 吞进注释里（审计 S-69），等于这一格从来没测过。
        eq("闲1番60符荣", ron(1, 60, 1, 0), 2000);
        eq("闲1番70符荣", ron(1, 70, 1, 0), 2300);
        eq("闲1番80符荣", ron(1, 80, 1, 0), 2600);
        eq("闲1番90符荣", ron(1, 90, 1, 0), 2900);
        eq("闲1番100符荣", ron(1, 100, 1, 0), 3200);
        eq("闲1番110符荣", ron(1, 110, 1, 0), 3600);
        eq("闲2番25符荣", ron(2, 25, 1, 0), 1600);
        eq("闲2番30符荣", ron(2, 30, 1, 0), 2000);
        eq("闲2番40符荣", ron(2, 40, 1, 0), 2600);
        eq("闲2番50符荣", ron(2, 50, 1, 0), 3200);
        eq("闲2番60符荣", ron(2, 60, 1, 0), 3900);
        eq("闲2番70符荣", ron(2, 70, 1, 0), 4500);
        eq("闲2番80符荣", ron(2, 80, 1, 0), 5200);
        eq("闲2番90符荣", ron(2, 90, 1, 0), 5800);
        eq("闲2番100符荣", ron(2, 100, 1, 0), 6400);
        eq("闲2番110符荣", ron(2, 110, 1, 0), 7100);
        eq("闲3番25符荣", ron(3, 25, 1, 0), 3200);
        eq("闲3番30符荣", ron(3, 30, 1, 0), 3900);
        eq("闲3番40符荣", ron(3, 40, 1, 0), 5200);
        eq("闲3番50符荣", ron(3, 50, 1, 0), 6400);
        eq("闲3番60符荣", ron(3, 60, 1, 0), 7700);
        eq("闲3番70符满贯", ron(3, 70, 1, 0), 8000);
        eq("闲4番25符荣", ron(4, 25, 1, 0), 6400);
        eq("闲4番30符荣", ron(4, 30, 1, 0), 7700);
        eq("闲4番40符满贯", ron(4, 40, 1, 0), 8000);
        eq("闲5番满贯", ron(5, 30, 1, 0), 8000);
        eq("闲6番跳满", ron(6, 30, 1, 0), 12000);
        eq("闲7番跳满", ron(7, 30, 1, 0), 12000);
        eq("闲8番倍满", ron(8, 30, 1, 0), 16000);
        eq("闲10番倍满", ron(10, 30, 1, 0), 16000);
        eq("闲11番三倍满", ron(11, 30, 1, 0), 24000);
        eq("闲12番三倍满", ron(12, 30, 1, 0), 24000);
        eq("闲13番累计役满", ron(13, 30, 1, 0), 32000);

        // 庄家荣和
        eq("庄1番30符荣", ron(1, 30, 0, 0), 1500);
        eq("庄1番40符荣", ron(1, 40, 0, 0), 2000);
        eq("庄1番50符荣", ron(1, 50, 0, 0), 2400);
        eq("庄1番60符荣", ron(1, 60, 0, 0), 2900);
        eq("庄1番70符荣", ron(1, 70, 0, 0), 3400);
        eq("庄1番80符荣", ron(1, 80, 0, 0), 3900);
        eq("庄1番90符荣", ron(1, 90, 0, 0), 4400);
        eq("庄1番100符荣", ron(1, 100, 0, 0), 4800);
        eq("庄1番110符荣", ron(1, 110, 0, 0), 5300);
        eq("庄2番25符荣", ron(2, 25, 0, 0), 2400);
        eq("庄2番30符荣", ron(2, 30, 0, 0), 2900);
        eq("庄2番40符荣", ron(2, 40, 0, 0), 3900);
        eq("庄2番50符荣", ron(2, 50, 0, 0), 4800);
        eq("庄2番60符荣", ron(2, 60, 0, 0), 5800);
        eq("庄2番70符荣", ron(2, 70, 0, 0), 6800);
        eq("庄2番80符荣", ron(2, 80, 0, 0), 7700);
        eq("庄2番90符荣", ron(2, 90, 0, 0), 8700);
        eq("庄2番100符荣", ron(2, 100, 0, 0), 9600);
        eq("庄2番110符荣", ron(2, 110, 0, 0), 10600);
        eq("庄3番25符荣", ron(3, 25, 0, 0), 4800);
        eq("庄3番30符荣", ron(3, 30, 0, 0), 5800);
        eq("庄3番40符荣", ron(3, 40, 0, 0), 7700);
        eq("庄3番50符荣", ron(3, 50, 0, 0), 9600);
        eq("庄3番60符荣", ron(3, 60, 0, 0), 11600);
        eq("庄4番25符荣", ron(4, 25, 0, 0), 9600);
        eq("庄4番30符荣", ron(4, 30, 0, 0), 11600);
        eq("庄4番40符满贯", ron(4, 40, 0, 0), 12000);
        eq("庄5番满贯", ron(5, 30, 0, 0), 12000);
        eq("庄6番跳满", ron(6, 30, 0, 0), 18000);
        eq("庄8番倍满", ron(8, 30, 0, 0), 24000);
        eq("庄11番三倍满", ron(11, 30, 0, 0), 36000);
        eq("庄13番累计役满", ron(13, 30, 0, 0), 48000);

        // 自摸
        Payments.Result p1 = Payments.compute(fake(3, 40), 1, -1, 0, 0, 0, true, -1, 0);
        eq("闲3番40符自摸(闲)", -p1.delta[2], 1300);
        eq("闲3番40符自摸(庄)", -p1.delta[0], 2600);
        Payments.Result p2 = Payments.compute(fake(2, 20), 1, -1, 0, 0, 0, true, -1, 0);
        eq("闲2番20符自摸(闲)", -p2.delta[2], 400);
        eq("闲2番20符自摸(庄)", -p2.delta[0], 700);
        Payments.Result p3 = Payments.compute(fake(2, 20), 0, -1, 0, 0, 0, true, -1, 0);
        eq("庄2番20符自摸", -p3.delta[1], 700);
        Payments.Result p4 = Payments.compute(fake(4, 30), 0, -1, 0, 0, 0, true, -1, 0);
        eq("庄4番30符自摸", -p4.delta[1], 3900);
        Payments.Result p5 = Payments.compute(fake(5, 30), 1, -1, 0, 0, 0, true, -1, 0);
        eq("闲满贯自摸(闲)", -p5.delta[2], 2000);
        eq("闲满贯自摸(庄)", -p5.delta[0], 4000);
        Payments.Result p6 = Payments.compute(fake(13, 30), 0, -1, 0, 0, 0, true, -1, 0);
        eq("庄役满自摸", -p6.delta[1], 16000);

        // ---------- 自摸逐格（原来只有 6 格，实战最常见的 1〜3 番 30〜50 符反而没测）
        eq("闲1番30符自摸", tsumoNon(1, 30), "500/300");
        eq("闲2番25符自摸", tsumoNon(2, 25), "800/400");
        eq("闲2番40符自摸", tsumoNon(2, 40), "1300/700");
        eq("闲3番20符自摸", tsumoNon(3, 20), "1300/700");
        eq("闲3番25符自摸", tsumoNon(3, 25), "1600/800");
        eq("闲3番30符自摸", tsumoNon(3, 30), "2000/1000");
        eq("闲3番50符自摸", tsumoNon(3, 50), "3200/1600");
        eq("闲4番20符自摸", tsumoNon(4, 20), "2600/1300");
        eq("闲4番25符自摸", tsumoNon(4, 25), "3200/1600");
        eq("闲1番40符自摸", tsumoNon(1, 40), "700/400");
        eq("庄1番40符自摸", tsumoDealer(1, 40), 700);
        eq("庄3番30符自摸", tsumoDealer(3, 30), 2000);
        eq("庄3番25符自摸", tsumoDealer(3, 25), 1600);
        eq("庄6番30符自摸", tsumoDealer(6, 30), 6000);
        eq("庄13番自摸", tsumoDealer(13, 30), 16000);

        // ---------- 满贯以上各档：**自摸**也要逐档（跳满/倍满/三倍满/累计役满）
        // ⚠ 满贯以上「自摸」= 亲 2N / 闲 N（N = 基本点），**不是**把荣和的数字对半 ——
        //   一开始我把这几格按"对半"写，自检立刻报 9 条（那正是这批断言的价值：它抓的是我）。
        eq("闲6番跳满自摸", tsumoNon(6, 30), "6000/3000");
        eq("闲7番跳满自摸", tsumoNon(7, 30), "6000/3000");
        eq("闲8番倍满自摸", tsumoNon(8, 30), "8000/4000");
        eq("闲10番倍满自摸", tsumoNon(10, 30), "8000/4000");
        eq("闲11番三倍满自摸", tsumoNon(11, 30), "12000/6000");
        eq("闲12番三倍满自摸", tsumoNon(12, 30), "12000/6000");
        eq("闲13番累计役满自摸", tsumoNon(13, 30), "16000/8000");
        eq("庄8番倍满自摸", tsumoDealer(8, 30), 8000);
        eq("庄11番三倍满自摸", tsumoDealer(11, 30), 12000);

        // ---------- 档位映射直接对拍（K 档位取决于规则集，两侧都钉住）
        eq("基本点：13 番 + 累计役满开 → 8000", Evaluator.basePoints(13, 30, TABLE_RULES), 8000);
        eq("基本点：13 番 + M.League（无累计役满）→ 三倍满 6000",
                Evaluator.basePoints(13, 30, preset("mleague")), 6000);
        eq("基本点：3 番 60 符 + 切上满贯关（天凤）→ 1920",
                Evaluator.basePoints(3, 60, TABLE_RULES), 1920);
        eq("基本点：3 番 60 符 + 切上满贯开（M.League）→ 满贯 2000",
                Evaluator.basePoints(3, 60, preset("mleague")), 2000);
        eq("基本点：6 番 → 跳满 3000", Evaluator.basePoints(6, 30, TABLE_RULES), 3000);
        eq("基本点：8 番 → 倍满 4000", Evaluator.basePoints(8, 30, TABLE_RULES), 4000);
        eq("基本点：11 番 → 三倍满 6000", Evaluator.basePoints(11, 30, TABLE_RULES), 6000);

        // ---------- 满贯以上各档：**真实手牌**走完整链路（役种 → 番数 → 符 → 基本点）
        // 清一色（门清 6）+ 平和（1）= 7 番 → 跳满（这手**不含** 789m，所以没有一気通貫）
        Evaluator.HandScore t1 = evalClosed("1m2m2m3m3m3m4m4m4m5m5m6m9m9m", "6m", false, 1, 0, 0);
        eq("真实手牌：清一色+平和 = 7 番", t1.han, 7);
        eq("真实手牌：7 番 → 跳满基本点 3000", t1.base, 3000);
        check("真实手牌：limit = 跳满（" + t1.limit + "）", "跳满".equals(t1.limit));
        // 清一色（6）+ 一気通貫（2）+ 平和（1）= 9 番 → 倍满
        Evaluator.HandScore t2 = evalClosed("1m2m3m4m5m6m7m8m9m2m3m4m5m5m", "4m", false, 1, 0, 0);
        eq("真实手牌：清一色+一気通貫+平和 = 9 番", t2.han, 9);
        eq("真实手牌：9 番 → 倍满基本点 4000", t2.base, 4000);
        // 清一色（6）+ 二杯口（3）等 = 12 番 → 三倍满（实测 12：另有一杯口系与断幺的复合见 yaku 列表）
        Evaluator.HandScore t3 = evalDora("1m1m2m2m3m3m4m4m5m5m6m6m7m7m", "7m", true, true, "",
                TABLE_RULES);
        eq("真实手牌：清一色+二杯口+立直+自摸 = 12 番", t3.han, 12);
        eq("真实手牌：12 番 → 三倍满基本点 6000", t3.base, 6000);
        // 再加 2 张宝牌 = 14 番 → 累计役满（《天凤》开）/ 三倍满（M.League 关）
        Evaluator.HandScore t4 = evalDora("1m1m2m2m3m3m4m4m5m5m6m6m7m7m", "7m", true, true, "2m",
                TABLE_RULES);
        eq("真实手牌：12 番 + 宝牌 2 = 14 番", t4.han, 14);
        eq("真实手牌：14 番 + 累计役满开 → 8000（天凤）", t4.base, 8000);
        check("真实手牌：limit = 累计役满（" + t4.limit + "）", "累计役满".equals(t4.limit));
        Evaluator.HandScore t5 = evalDora("1m1m2m2m3m3m4m4m5m5m6m6m7m7m", "7m", true, true, "2m",
                preset("mleague"));
        eq("真实手牌：同 14 番在 M.League → 三倍满 6000", t5.base, 6000);
        check("真实手牌：M.League 的 limit = 三倍满（" + t5.limit + "）",
                "三倍满".equals(t5.limit));

        // ---------- 番缚（minHan）：宝牌不算番缚
        Rules minHan2 = preset("tenhou");
        minHan2.minHan = 2;
        // 手役只有役牌中 1 番（中中中）+ 双碰 5p/7z
        Evaluator.HandScore m1 = evalDora("2m3m4m5m6m7m2p3p4p5p5p7z7z7z", "7z", false, false, "",
                minHan2);
        check("一番缚不足（手役只有役牌 1 番）→ 不和", !m1.valid);
        check("番缚不足的原因码（" + m1.reason + "）", "番缚不足".equals(m1.reason));
        // 同一手牌在默认一番缚下可以和
        check("同一手牌在 minHan=1 下成立",
                evalDora("2m3m4m5m6m7m2p3p4p5p5p7z7z7z", "7z", false, false, "", TABLE_RULES).valid);

        // 守恒
        for (int i = 0; i < 4; i++) {
            for (int h = 0; h < 4; h++) {
                for (int f : new int[]{25, 30, 40, 70}) {
                    for (boolean t : new boolean[]{true, false}) {
                        Payments.Result r = Payments.compute(fake(Math.max(1, h), f), i, (i + 1) % 4,
                                i, 0, 0, t, -1, 0);
                        int sum = 0;
                        for (int d : r.delta) {
                            sum += d;
                        }
                        if (sum != 0) {
                            check("授受守恒 " + i + "/" + h + "/" + f + "/" + t, false);
                        }
                    }
                }
            }
        }
        pass++;
    }

    /**
     * **带副露的符**（审计 S-70：原来 `evalCtx` 恒传空 `melds` → 明刻/明杠/暗杠/加杠符、
     * 食い平和、副露 + 自摸符、荣和补刻按明刻，全部零断言）。
     *
     * <p>设计要点：符最后会**进位到 10**，所以选局面时要让"错的算法"跨过进位线 ——
     * 否则 2 符与 4 符的差别会被进位吃掉、断言测不出东西。
     */
    private static void fuOpenTests() {
        // 中张明刻（2 符）：副露 5m 碰 + 门清以外的 20 符底 → 20 + 2 = 22 → 30
        Evaluator.HandScore a1 = evalOpen("2m3m4m6m7m8m2p3p4p5p5p", List.of(pon("5m")), "4p", false);
        eq("中张明刻：20 + 2 = 22 → 30", a1.fu, 30);
        // 同样的牌型把刻子放在手里（暗刻 4 符 + 门前荣和 10）：20 + 10 + 4 = 34 → 40
        Evaluator.HandScore a2 = evalClosed("5m5m5m2m3m4m6m7m8m2p3p4p5p5p", "4p", false, 1, 0, 0);
        eq("同样的形、刻子在手里：20 + 10 + 4 = 34 → 40", a2.fu, 40);
        // 幺九明刻（4 符）→ 20 + 4 = 24 → 30；幺九暗刻（8）+ 门前 → 20 + 10 + 8 = 38 → 40
        Evaluator.HandScore a3 = evalOpen("2m3m4m6m7m8m2p3p4p5p5p", List.of(pon("1m")), "4p", false);
        eq("幺九明刻：20 + 4 = 24 → 30", a3.fu, 30);
        Evaluator.HandScore a4 = evalClosed("1m1m1m2m3m4m6m7m8m2p3p4p5p5p", "4p", false, 1, 0, 0);
        eq("幺九暗刻 + 门前：20 + 10 + 8 = 38 → 40", a4.fu, 40);
        // 杠：中张明杠 8 / 幺九明杠 16 / 中张暗杠 16 / 幺九暗杠 32（都不带门前荣和 10）
        eq("中张明杠：20 + 8 = 28 → 30",
                evalOpen("2m3m4m6m7m8m2p3p4p5p5p", List.of(kan("daiminkan", "5m")), "4p", false).fu,
                30);
        eq("幺九明杠：20 + 16 = 36 → 40",
                evalOpen("2m3m4m6m7m8m2p3p4p5p5p", List.of(kan("daiminkan", "1m")), "4p", false).fu,
                40);
        eq("中张暗杠：20 + 16 = 36 → 40",
                evalOpen("2m3m4m6m7m8m2p3p4p5p5p", List.of(kan("ankan", "5m")), "4p", false).fu,
                40);
        eq("幺九暗杠：20 + 32 = 52 → 60",
                evalOpen("2m3m4m6m7m8m2p3p4p5p5p", List.of(kan("ankan", "1m")), "4p", false).fu,
                60);
        // 食い平和（副露 + 荣和）：一个符都没有 → 20，但**最低 30 符**（docs/日本麻将.md §符）
        Evaluator.HandScore a5 = evalOpen("4m5m6m7m8m9m2p3p4p5p5p", List.of(chi("1m", "2m", "3m")),
                "4p", false);
        eq("食い平和（副露荣和）：20 → 最低 30 符", a5.fu, 30);
        // 荣和补刻（シャボロン）按**明刻**算：双碰等张，和了牌补出的那组算明刻。
        // 副露吃牌 + 幺九暗刻 9m + 双碰 5p/7p，和 7p：
        //   20（底）+ 0（双碰）+ 2（7p 明刻 中张）+ 8（9m 幺九暗刻）= 30 → 30
        Evaluator.HandScore a6 = evalOpen("4m5m6m9m9m9m5p5p7p7p7p",
                List.of(chi("1m", "2m", "3m")), "7p", false);
        eq("荣和补刻按明刻：20 + 2 + 8 = 30 → 30（若误按暗刻 = 32 → 40）", a6.fu, 30);
        // 同一手牌自摸：7p 此时是**暗刻**（4）且多 2 符自摸 → 20 + 2 + 4 + 8 = 34 → 40
        Evaluator.HandScore a7 = evalOpen("4m5m6m9m9m9m5p5p7p7p7p",
                List.of(chi("1m", "2m", "3m")), "7p", true);
        eq("同形自摸（暗刻 + 自摸符 2）：20 + 2 + 4 + 8 = 34 → 40", a7.fu, 40);
        // 副露 + 自摸符：单独把自摸那 2 符卡在进位线上
        //   副露 + 幺九暗刻 8 + 单骑 2 = 30 → 30；再加自摸 2 → 32 → 40
        //   ⚠ 一手带一副露时，暗牌（含和了牌）必须是 **11 张**（14 − 3），少了会被判不成和了形（fu=0）
        eq("副露单骑荣和：20 + 8 + 2 = 30 → 30",
                evalOpen("4m5m6m9m9m9m2p3p4p5s5s", List.of(chi("1m", "2m", "3m")), "5s", false).fu, 30);
        eq("副露单骑自摸（多 2 符自摸）：20 + 2 + 8 + 2 = 32 → 40",
                evalOpen("4m5m6m9m9m9m2p3p4p5s5s", List.of(chi("1m", "2m", "3m")), "5s", true).fu, 40);
    }

    private static void paymentTests() {
        // 本场棒
        Payments.Result r1 = Payments.compute(fake(1, 30), 1, 2, 0, 2, 0, false, -1, 0);
        eq("2本场荣和支付", -r1.delta[2], 1000 + 600);
        eq("2本场荣和收入", r1.delta[1], 1600);
        // 立直棒
        Payments.Result r2 = Payments.compute(fake(1, 30), 1, 2, 0, 0, 3, false, -1, 0);
        eq("3根立直棒", r2.delta[1], 1000 + 3000);
        // 自摸本场
        Payments.Result r3 = Payments.compute(fake(2, 30), 1, -1, 0, 1, 0, true, -1, 0);
        int sum = -r3.delta[2] - r3.delta[3] - r3.delta[0];
        int expect = 500 + 100 + 1000 + 100 + 500 + 100;
        eq("1本场自摸总收入", sum, expect);
        // 不听罚符
        int[] d = Payments.notenPenalty(new boolean[]{true, false, false, false}, 3000);
        eq("1人听牌罚符", d[0], 3000);
        eq("1人听牌罚符-闲", d[1], -1000);
        int[] d2 = Payments.notenPenalty(new boolean[]{true, true, false, false}, 3000);
        eq("2人听牌罚符", d2[0], 1500);
        eq("2人听牌罚符-闲", d2[2], -1500);
        int[] d3 = Payments.notenPenalty(new boolean[]{true, true, true, false}, 3000);
        eq("3人听牌罚符", d3[2], 1000);
        eq("3人听牌罚符-闲", d3[3], -3000);
        int[] d4 = Payments.notenPenalty(new boolean[]{true, true, true, true}, 3000);
        eq("4人听牌无罚符", d4[0], 0);
        // 包牌（大三元：包牌者自摸全额）
        Evaluator.HandScore ys = new Evaluator.HandScore();
        ys.yakuman = 1;
        ys.base = 8000;
        ys.valid = true;
        Payments.Result r4 = Payments.compute(ys, 1, -1, 0, 0, 0, true, 3, 8000);
        eq("包牌自摸：包牌者付全额(闲)", -r4.delta[3], 8000 + 16000 + 8000);
        eq("包牌自摸：他家不付", -r4.delta[2], 0);
        Payments.Result r5 = Payments.compute(ys, 1, 2, 0, 0, 0, false, 3, 8000);
        eq("包牌荣和：包牌者一半", -r5.delta[3], 16000);
        eq("包牌荣和：放铳者一半", -r5.delta[2], 16000);
        // 包牌 + 本场：**本场棒全部由包牌者出**（原来这条路径零断言，见审计 S-72）
        Payments.Result r6 = Payments.compute(ys, 1, 2, 0, 2, 0, false, 3, 8000);
        eq("包牌荣和 + 2 本场：放铳者仍只付一半", -r6.delta[2], 16000);
        eq("包牌荣和 + 2 本场：包牌者付另一半 + 全部本场棒", -r6.delta[3], 16000 + 600);
        eq("包牌荣和 + 2 本场：和牌者全额收", r6.delta[1], 32600);
        Payments.Result r7 = Payments.compute(ys, 1, -1, 0, 1, 0, true, 3, 8000);
        eq("包牌自摸 + 1 本场：包牌者付全额 + 全部本场（3×100）", -r7.delta[3], 32000 + 300);
        eq("包牌自摸 + 1 本场：闲家一分不付", r7.delta[2], 0);
    }

    /**
     * 不听罚符必须**精确零和**：任何 {@code rules.noten_penalty} 与听牌家数组合都不能
     * 凭空生灭点数（AUDIT F11 —— 旧实现两边各自向下取整到 100，1000 点时能少掉 100 点）。
     */
    private static void notenPenaltyTests() {
        final int[][] cases = {
            {3000, 1}, {3000, 2}, {3000, 3},
            {1000, 1}, {1000, 2}, {1000, 3},
            {1500, 1}, {1500, 2}, {1500, 3},
            {600, 1}, {600, 2}, {600, 3},
            {100, 1}, {100, 3}, {0, 1}, {0, 3},
        };
        boolean allZeroSum = true;
        for (int[] c : cases) {
            boolean[] tp = new boolean[4];
            for (int i = 0; i < c[1]; i++) {
                tp[i] = true;
            }
            int[] d = Payments.notenPenalty(tp, c[0]);
            int sum = 0;
            for (int v : d) {
                sum += v;
            }
            if (sum != 0) {
                allZeroSum = false;
                System.out.println("  [零和失败] total=" + c[0] + " n=" + c[1]
                        + " → " + Arrays.toString(d));
            }
        }
        check("不听罚符：全部罚符值 × 听牌家数组合都严格零和", allZeroSum);

        eq("不听罚符 3000 / 1 家听牌",
                Arrays.toString(Payments.notenPenalty(new boolean[]{true, false, false, false}, 3000)),
                "[3000, -1000, -1000, -1000]");
        eq("不听罚符 3000 / 2 家听牌",
                Arrays.toString(Payments.notenPenalty(new boolean[]{true, true, false, false}, 3000)),
                "[1500, 1500, -1500, -1500]");
        eq("不听罚符 3000 / 3 家听牌",
                Arrays.toString(Payments.notenPenalty(new boolean[]{true, true, true, false}, 3000)),
                "[1000, 1000, 1000, -3000]");
        eq("不听罚符 1000 / 3 家听牌（旧实现会丢掉 100 点）",
                Arrays.toString(Payments.notenPenalty(new boolean[]{true, true, true, false}, 1000)),
                "[300, 300, 300, -900]");
        eq("不听罚符 1000 / 1 家听牌（余数由付方分摊）",
                Arrays.toString(Payments.notenPenalty(new boolean[]{true, false, false, false}, 1000)),
                "[1000, -400, -300, -300]");
        eq("不听罚符 0 → 无人转移",
                Arrays.toString(Payments.notenPenalty(new boolean[]{true, false, false, false}, 0)),
                "[0, 0, 0, 0]");
    }

    /** 点亮流局满贯的实局路径：定向构造一条全幺九牌河，再跑真实结算。 */
    private static void nagashiLivePathTest() {
        mahjong.game.Round r = newRound();
        r.rules.nagashiMangan = true;
        // 0 号位整条牌河全幺九、且没被鸣过 → 满贯成立；其余三家空牌河 → 不成立
        r.discards[0].clear();
        r.discards[0].add(Tiles.id(0, 0));    // 1m
        r.discards[0].add(Tiles.id(8, 0));    // 9m
        r.discards[0].add(Tiles.id(27, 0));   // 东
        for (int s = 1; s < 4; s++) {
            r.discards[s].clear();
        }
        RoundScoring.debugResetCounts();
        YakuCodes.resetMisses();
        mahjong.game.Round.Result res = r.debugExhaustive();
        check("全幺九牌河 → 流局满贯成立", res.nagashi);
        check("满贯支付在实局路径上被调用", RoundScoring.debugCallCounts().contains("nagashiPay=1"));
        // 实局结算出的增减必须与纯函数一致
        int[] want = RoundScoring.nagashiPayments(0, r.dealer);
        boolean same = true;
        for (int i = 0; i < 4; i++) {
            if (res.delta[i] != want[i]) {
                same = false;
                break;
            }
        }
        check("实局满贯增减与纯函数一致（立直棒为 0 时）", same && res.sticksLeft == 0);
    }
    // ------------------------------------------------------------- 整场模拟

    private static void simulationTest() {
        RoundScoring.debugResetCounts();
        YakuCodes.resetMisses();
        for (int trial = 0; trial < 3; trial++) {
            Table t = new Table("TEST", "自测桌", Rules.defaults());
            t.botDelayMs = 0;      // 模拟时不要机器人延时
            t.roundDelayMs = 0;
            t.seedBase = 12345L + trial * 777;
            t.debugDeterministicSeed = true;
            for (int i = 0; i < 4; i++) {
                t.addBot(i);
            }
            try {
                t.playGame();
            } catch (RuntimeException e) {
                failures.add("整场模拟异常: " + e);
                fail++;
                return;
            }
            int sum = 0;
            for (int i = 0; i < 4; i++) {
                sum += t.seat(i).score;
            }
            eq("整场点数和守恒(第" + (trial + 1) + "次)", sum, 100000);
        }
        // 接线验证：抽出的判据必须在实局路径上真的被调用过，否则就是死代码
        System.out.println("  [覆盖] " + RoundScoring.debugCallCounts());
        check("整场模拟确实调用了抽出的判据（非死代码）", RoundScoring.debugTotalCalls() > 0);
        pass++;
        // 词汇表覆盖：整场模拟里出现过的每个役种名都必须能换成 ASCII 码
        // （漏登记 → codeOf 计一次 miss；这是"新增役种忘了登记"的主要兜底）
        eq("整场模拟里没有未登记的役种名（YakuCodes.misses）", YakuCodes.misses(), 0);
    }

    // ------------------------------------------------------------- 规则层评估判据

    /** 把 {@code Bot} 原来那份私有进张实现抄进来对拍（行为等价的证据）。 */
    private static int oldUkeire(int[] counts, int meldCount) {
        int cur = Shanten.min(counts, meldCount);
        if (cur <= 0) {
            return 0;
        }
        int total = 0;
        int[] c = counts.clone();
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (c[k] >= 4) {
                continue;
            }
            c[k]++;
            int sh = Shanten.min(c, meldCount);
            c[k]--;
            if (sh < cur) {
                total += 4 - c[k];
            }
        }
        return total;
    }

    /**
     * 规则层的**评估判据**（`rules/Visible` 与 `rules/HandEval`）。
     *
     * <p>它们是"给 AI 用"的观测量（可见牌统计、进张、听牌形），不参与任何规则判定。
     * 三条要点：① 可见牌统计与手牌张数必须自洽；② 进张枚数与 `Bot` 原来那份私有实现**等价**
     * （否则"把私有实现抽成公共判据"就悄悄改了教师的行为）；③ 听牌形要认得出两面/嵌张/边张/单骑/双碰。
     */
    private static void handEvalTests() {
        // ---------- ① 可见牌统计
        Table t = new Table("EVAL", "评估桌", Rules.defaults());
        t.debugDeterministicSeed = true;
        t.seedBase = 31L;
        for (int i = 0; i < 4; i++) {
            t.addBot(i);
        }
        Round r = new Round(t, 0, 1, 0, 0, new int[]{25000, 25000, 25000, 25000}, 0, 818181L);
        r.debugSetup();
        int[] vis = mahjong.rules.Visible.counts(r.discards, r.melds, r.doraIndicators());
        eq("可见牌统计：刚配牌时只有宝牌指示牌是可见的",
                mahjong.rules.Visible.total(vis), r.doraIndicators().size());
        int[] draw = mahjong.rules.Visible.drawable(vis, new int[Tiles.KIND_COUNT]);
        int total = 0;
        for (int v : draw) {
            total += v;
        }
        eq("「可摸张数」= 4×34 减去已经看见的牌（配牌后只有宝牌指示牌）",
                total, 136 - r.doraIndicators().size());

        // ---------- ② 进张判据与旧私有实现等价（教师行为不变的证据）
        // 手牌一律用 13 张（或 13−3×副露 张）：14 张的"向听"是另一回事，
        // 拿 14 张去问"进张"会得到全 0（向听已经 <= 0），那是**测错了对象**。
        String[] hands = {"1m1m1m2m2m2m3m3m3m5m7m9m2p", "1m1m1m2m2m2m5m7m9m2p"};
        for (int meldCount = 0; meldCount < hands.length; meldCount++) {
            int[] probe = counts(hands[meldCount]);
            int[] kinds = mahjong.rules.HandEval.advanceKinds(probe, meldCount);
            eq("进张枚数与旧实现等价（副露 " + meldCount + "）",
                    mahjong.rules.HandEval.advanceTiles(kinds, probe, null),
                    oldUkeire(probe, meldCount));
            check("进张判据非空（副露 " + meldCount + "，实际 " + mahjong.rules.HandEval
                    .advanceTypes(kinds) + " 种）",
                    mahjong.rules.HandEval.advanceTypes(kinds) > 0);
        }
        // 已听牌时不谈"进张"（该看听牌表）
        int[] tenpai13 = new int[Tiles.KIND_COUNT];
        for (int id : parse("1m1m1m2m2m2m3m3m3m4m4m4m5p")) {
            tenpai13[Tiles.kind(id)]++;
        }
        eq("已听牌时进张为空（向听不再下降）",
                mahjong.rules.HandEval.advanceTypes(
                        mahjong.rules.HandEval.advanceKinds(tenpai13, 0)), 0);
        check("已听牌（向听 0）", mahjong.rules.HandEval.tenpai(tenpai13, 0));
        eq("听牌表", mahjong.rules.HandEval.waits(tenpai13, 0).toString(), "[13]");

        // ---------- ③ 听牌形：两面 / 嵌张 / 边张 / 单骑 / 双碰
        eq("两面：23m 听 1m/4m",
                shapeOf("1m1m1m2m2m2m3m3m3m2p3p5p5p", "1p", "4p"),
                mahjong.rules.Agari.WAIT_RYANMEN);
        eq("嵌张：13m 听 2m",
                shapeOf("1m1m1m2m2m2m3m3m3m1p3p5p5p", "2p"),
                mahjong.rules.Agari.WAIT_KANCHAN);
        eq("边张：12m 听 3m",
                shapeOf("1m1m1m2m2m2m3m3m3m1p2p5p5p", "3p"),
                mahjong.rules.Agari.WAIT_PENCHAN);
        eq("单骑：听雀头",
                shapeOf("1m1m1m2m2m2m3m3m3m1p2p3p5p", "5p"),
                mahjong.rules.Agari.WAIT_TANKI);
        eq("双碰：11p+55p 听两张",
                shapeOf("1m1m1m2m2m2m3m3m3m1p1p5p5p", "1p", "5p"),
                mahjong.rules.Agari.WAIT_SHANPON);
        check("良形只有两面",
                mahjong.rules.HandEval.isGoodShape(mahjong.rules.Agari.WAIT_RYANMEN)
                        && !mahjong.rules.HandEval.isGoodShape(mahjong.rules.Agari.WAIT_KANCHAN));
        check("形的优劣序：两面 < 双碰 < 嵌张 < 边张 < 单骑",
                mahjong.rules.HandEval.shapeRank(mahjong.rules.Agari.WAIT_RYANMEN)
                        < mahjong.rules.HandEval.shapeRank(mahjong.rules.Agari.WAIT_SHANPON)
                        && mahjong.rules.HandEval.shapeRank(mahjong.rules.Agari.WAIT_SHANPON)
                        < mahjong.rules.HandEval.shapeRank(mahjong.rules.Agari.WAIT_KANCHAN)
                        && mahjong.rules.HandEval.shapeRank(mahjong.rules.Agari.WAIT_KANCHAN)
                        < mahjong.rules.HandEval.shapeRank(mahjong.rules.Agari.WAIT_PENCHAN)
                        && mahjong.rules.HandEval.shapeRank(mahjong.rules.Agari.WAIT_PENCHAN)
                        < mahjong.rules.HandEval.shapeRank(mahjong.rules.Agari.WAIT_TANKI));

        // ---------- ④ 快照：打一张之后听牌 / 枚数按可见牌扣减
        // 打掉 9p 之后是 `111m 222m 333m 23p 55p` → 两面听 1p/4p
        int[] c14 = counts("1m1m1m2m2m2m3m3m3m2p3p5p5p9p");
        mahjong.rules.HandEval.Snapshot snap =
                mahjong.rules.HandEval.afterDiscard(c14, List.of(), Tiles.parseKind("9p"), null);
        check("打完一张后听牌：" + snap, snap.tenpai);
        eq("听 1p/4p 两面（2 种）", snap.waitTypes, 2);
        eq("良形种类 = 2", snap.goodWaitTypes, 2);
        eq("看不见任何牌时两面共 8 枚", snap.goodWaitTiles, 8);
        int[] vis2 = new int[Tiles.KIND_COUNT];
        vis2[Tiles.parseKind("1p")] = 3;
        vis2[Tiles.parseKind("4p")] = 1;
        mahjong.rules.HandEval.Snapshot snap2 =
                mahjong.rules.HandEval.afterDiscard(c14, List.of(), Tiles.parseKind("9p"), vis2);
        eq("可见牌把枚数扣干净（1p 剩 1 张、4p 剩 3 张 → 4 枚）", snap2.goodWaitTiles, 4);
    }

    /** 造 13 张手牌，取指定听牌张的形；认不出返回 -1。 */
    private static int shapeOf(String hand, String... waits) {
        int[] c = counts(hand);
        int[] shapes = mahjong.rules.HandEval.waitShapes(c, List.of());
        int best = -1;
        for (String w : waits) {
            int k = Tiles.parseKind(w);
            if (shapes[k] < 0) {
                return -100 - k;      // 根本没听这张：返回一个明显不在取值域里的数
            }
            best = best < 0 ? shapes[k] : mahjong.rules.HandEval.betterShape(best, shapes[k]);
        }
        return best;
    }

    // ------------------------------------------------------------- 振听（规则）
    /** 见逃策略：把「荣和」与「立直家的自摸」一律放过，其余交给内置机器人。 */
    private static final class PassWinPolicy implements mahjong.ai.ActionPolicy {
        /** {seat, kind, handKey, offeredRon, passedWin, furitenBefore, selfRiichi} */
        final List<Object[]> trace = new ArrayList<>();

        @Override
        public mahjong.ai.Action choose(mahjong.ai.Decision d) {
            final List<mahjong.ai.Action> legal = d.legal();
            boolean offeredRon = false;
            boolean offeredTsumo = false;
            for (mahjong.ai.Action a : legal) {
                offeredRon |= "ron".equals(a.type);
                offeredTsumo |= "tsumo".equals(a.type);
            }
            // 荣和一律放过；立直家的自摸也放过（触发立直振听）
            final boolean passRon = offeredRon;
            final boolean passTsumo = offeredTsumo && d.obs.selfRiichi;
            trace.add(new Object[]{d.obs.seat, d.kind,
                    d.obs.roundWind + "-" + d.obs.kyoku + "-" + d.obs.honba,
                    offeredRon, passRon || passTsumo, d.obs.furiten, d.obs.selfRiichi});
            if (!passRon && !passTsumo) {
                return mahjong.ai.Action.fromCmd(
                        Bot.decide(d.round, d.obs.seat, d.kind, d.options, d.extra));
            }
            if (passRon) {
                return mahjong.ai.Action.of("pass");
            }
            for (mahjong.ai.Action a : legal) {
                if ("discard".equals(a.type)) {
                    return a;
                }
            }
            return legal.isEmpty() ? mahjong.ai.Action.of("pass") : legal.get(0);
        }
    }

    /**
     * 振听三条（`docs/日本麻将.md` §振听）—— 这三条原来**都不成立**：
     * <ol>
     *   <li><b>舍张振听</b>只看牌河，而被他家吃碰杠走的舍牌已从牌河移除 →
     *       打出去被人碰走的听牌张事后能荣和回去；</li>
     *   <li><b>同巡振听</b>（见逃一张荣和牌后本巡之内不能再荣和）—— `furitenTemp` **只有清除、
     *       从来没人置位**；</li>
     *   <li><b>立直振听</b>（立直后见逃，持续到本局结束）—— `furitenPerm` 同样从未置位。</li>
     * </ol>
     * 修法见 `Round.discardKindsEver` / `claimPhase` 的见逃记账 / 出牌段的立直振听记账。
     */
    private static void furitenRuleTests() {
        // ---------- ① 舍张振听（含被他家鸣走的舍牌）
        Table t = new Table("FURITEN", "振听桌", Rules.defaults());
        t.debugDeterministicSeed = true;
        t.seedBase = 99L;
        for (int i = 0; i < 4; i++) {
            t.addBot(i);
        }
        Round r = new Round(t, 0, 1, 0, 0, new int[]{25000, 25000, 25000, 25000}, 0, 424242L);
        r.debugSetup();
        final int ich = Tiles.parseKind("1z");
        r.hand[0].clear();
        for (String c : new String[]{"1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
                "1p", "2p", "3p", "1z"}) {
            r.hand[0].add(Tiles.id(Tiles.parseKind(c), 0));
        }
        eq("构造：听 1z 单骑", r.waitKinds(0).toString(), "[" + ich + "]");
        check("没打过听牌张时不振听", !r.isFuriten(0));
        check("没打过的牌不进「曾经打出过」的账", !r.ownDiscardKinds(0).contains(ich));

        r.debugPushDiscard(0, "1z", false);
        eq("debugPushDiscard 走了生产的记账（牌河 +1）", r.discards[0].size(), 1);
        check("舍张振听：河里打过的听牌张 → 振听", r.isFuriten(0));

        r.debugRemoveCalledFromRiver(0, 0);
        eq("被鸣走后牌河为空（客户端也会 removeAt）", r.discards[0].size(), 0);
        check("舍张振听必须算上「被他家吃碰杠走的舍牌」", r.isFuriten(0));
        check("…且这一条记在「曾经打出过」的账上（不是牌河）",
                r.ownDiscardKinds(0).contains(ich));

        // 规则明文：改变手牌后，若所听的牌都没被自己打出过，振听解除
        r.hand[0].set(12, Tiles.id(Tiles.parseKind("2z"), 0));
        eq("构造：改手牌后听 2z 单骑", r.waitKinds(0).toString(),
                "[" + Tiles.parseKind("2z") + "]");
        check("改手牌后（听的牌不是自己打过的）舍张振听解除", !r.isFuriten(0));

        // ---------- ②③ 同巡振听 / 立直振听：整场里按不变式检查
        // 单局一场、多种子扫，**凑够机会就提前收工**：见逃荣和很常见，"立直家摸到自己的
        // 和了牌"却很稀有（要立直之后在剩余几巡里自摸到），跑满固定场次纯属浪费 L1 时间。
        final int maxSeeds = 40;
        int passRon = 0;
        int passTsumo = 0;
        int checked = 0;
        int used = 0;
        boolean invariantOk = true;
        String bad = "";
        for (int g = 0; g < maxSeeds; g++) {
            used = g + 1;
            Table tt = new Table("FUR" + g, "见逃桌", Rules.defaults());
            tt.botDelayMs = 0;
            tt.roundDelayMs = 0;
            tt.debugDeterministicSeed = true;
            tt.debugMaxHands = 1;
            tt.seedBase = 5150L + g * 7919L;
            PassWinPolicy pol = new PassWinPolicy();
            for (int i = 0; i < 4; i++) {
                tt.policy[i] = mahjong.ai.Policies.fromAction(pol);
                tt.addBot(i);
            }
            tt.playGame();
            // 逐座位走一遍：见逃 → 本巡之内必须一直振听；立直见逃 → 本局之内一直振听
            for (int seat = 0; seat < 4; seat++) {
                boolean pendingTemp = false;
                boolean pendingPerm = false;
                String handKey = "";
                for (Object[] rec : pol.trace) {
                    if (((Number) rec[0]).intValue() != seat) {
                        continue;
                    }
                    String hand = String.valueOf(rec[2]);
                    if (!hand.equals(handKey)) {
                        handKey = hand;
                        pendingTemp = false;
                        pendingPerm = false;
                    }
                    boolean furiten = (Boolean) rec[5];
                    boolean claim = "claim".equals(rec[1]);
                    if (claim && (pendingTemp || pendingPerm) && !furiten) {
                        invariantOk = false;
                        bad = (pendingPerm ? "立直振听" : "同巡振听") + " 未生效 seed=" + g
                                + " seat=" + seat + " hand=" + hand;
                    }
                    checked++;
                    if (!claim) {
                        pendingTemp = false;     // 自家摸牌 → 同巡振听解除
                    }
                    if ((Boolean) rec[4]) {
                        if ((Boolean) rec[3]) {
                            passRon++;
                            pendingTemp = true;
                            if ((Boolean) rec[6]) {
                                pendingPerm = true;   // 立直家见逃荣和 → 立直振听
                            }
                        } else {
                            passTsumo++;
                            pendingPerm = true;       // 立直家见逃自摸 → 立直振听
                        }
                    }
                }
            }
            if (passRon > 0 && passTsumo > 0 && checked >= 300) {
                break;
            }
        }
        System.out.println("  [覆盖] 振听不变式：" + used + " 场单局、" + checked + " 次询问，见逃荣和 "
                + passRon + " 次、立直见逃自摸 " + passTsumo + " 次");
        check("同巡/立直振听的不变式在整场里处处成立" + (bad.isEmpty() ? "" : "（" + bad + "）"),
                invariantOk);
        check("确实造出了「见逃荣和」的机会（否则上面那条是空转）", passRon > 0);
        check("确实造出了「立直见逃自摸」的机会", passTsumo > 0);
    }

    // ------------------------------------------------------------- 危险度 / teacher

    /** 造一份「只有公开信息」的 teacher 视图，手牌用牌码串给。 */
    private static Bot.HandState state(String hand14, int meldCount) {
        Bot.HandState st = new Bot.HandState();
        for (int id : parse(hand14)) {
            st.counts[Tiles.kind(id)]++;
        }
        st.meldCount = meldCount;
        st.kuitan = true;
        st.seat = 0;
        st.dealer = 0;
        st.turn = 6;
        return st;
    }

    /** 让某家河里有这些牌（现物/筋判据）。 */
    private static void river(Bot.HandState st, int seat, String kinds) {
        for (int id : parse(kinds)) {
            st.rivers[seat][Tiles.kind(id)]++;
        }
    }

    /**
     * 危险度判据（`rules/Danger`）与 teacher 的三层取舍（牌效 / 押し引き / 打点与役）。
     *
     * <p>这组断言全是**纯函数对拍**：直接构造局面（{@link Bot.HandState}）问"该打哪张""能不能鸣"，
     * 不跑整局 —— 否则只能靠运气等牌型出现。最后再用计数钩子确认这三条在**实局里真的被走到**
     * （纯函数对拍 + 实战覆盖，两条一起才算接上）。
     */
    private static void teacherTests() {
        // ---------- ① Danger：现物 / 筋 / 壁 / 立直
        int[] vis = new int[Tiles.KIND_COUNT];
        int[] riverM = new int[Tiles.KIND_COUNT];
        riverM[Tiles.parseKind("3m")] = 1;
        eq("现物 → SAFE",
                mahjong.rules.Danger.of(Tiles.parseKind("3m"), vis, riverM, true, 6).level,
                mahjong.rules.Danger.SAFE);
        eq("现物的分数必须是 0（唯一硬保证）",
                mahjong.rules.Danger.of(Tiles.parseKind("3m"), vis, riverM, true, 6).score, 0);
        eq("筋：他打过 3m ⇒ 6m 相对安全（同花色 ±3）",
                mahjong.rules.Danger.of(Tiles.parseKind("6m"), vis, riverM, true, 6).level,
                mahjong.rules.Danger.RELATIVELY_SAFE);
        eq("筋的理由码", mahjong.rules.Danger.of(Tiles.parseKind("6m"), vis, riverM, true, 6).code,
                mahjong.rules.Danger.CODE_SUJI);
        check("字牌没有筋", !mahjong.rules.Danger.isSuji(Tiles.parseKind("5z"), riverM));
        check("±3 越界不算筋（1m 的 −3 不存在）",
                !mahjong.rules.Danger.isSuji(Tiles.parseKind("1m"), riverM));
        int[] visWall = new int[Tiles.KIND_COUNT];
        visWall[Tiles.parseKind("7p")] = 3;
        eq("壁：该牌种已见 3 张 ⇒ 相对安全",
                mahjong.rules.Danger.of(Tiles.parseKind("7p"), visWall, riverM, true, 6).level,
                mahjong.rules.Danger.RELATIVELY_SAFE);
        eq("立直家 + 无筋无壁 ⇒ DANGEROUS",
                mahjong.rules.Danger.of(Tiles.parseKind("4p"), vis, riverM, true, 6).level,
                mahjong.rules.Danger.DANGEROUS);
        eq("没人立直 + 无信息 ⇒ SUSPICIOUS",
                mahjong.rules.Danger.of(Tiles.parseKind("4p"), vis, riverM, false, 6).level,
                mahjong.rules.Danger.SUSPICIOUS);
        check("巡目越深分数越高（级别不变）",
                mahjong.rules.Danger.of(Tiles.parseKind("4p"), vis, riverM, true, 15).score
                        > mahjong.rules.Danger.of(Tiles.parseKind("4p"), vis, riverM, true, 1).score);

        // ---------- ② 押し引き：有人立直且自己还远 → 打现物；没人立直 → 打牌效
        // 手牌（**必须正好 14 张**）：234m 234p + 55p + 6 张互不相连的浮牌 → 3 向听
        final String hand = "2m3m4m2p3p4p5p5p1z2z3z4z1s4s";
        Bot.HandState st = state(hand, 0);
        st.rivers[2][Tiles.parseKind("4s")] = 1;         // 下家河里有 4s
        List<Object> cands = new ArrayList<>();
        for (int id : parse(hand)) {
            String c = Tiles.kindToStr(Tiles.kind(id));
            if (!cands.contains(c)) {
                cands.add(c);
            }
        }
        eq("自检前提：手牌正好 14 张", st.counts.length == Tiles.KIND_COUNT
                ? mahjong.rules.Visible.total(st.counts) : -1, 14);
        check("自检前提：3 向听（押し引き的弃和侧），实际 "
                + mahjong.rules.HandEval.shanten(st.counts, 0),
                mahjong.rules.HandEval.shanten(st.counts, 0) >= 2);
        final String offensive = Bot.chooseDiscard(st, cands);
        st.riichi[2] = true;                             // 下家立直
        final String defensive = Bot.chooseDiscard(st, cands);
        check("没人立直时不为了安全扔掉 4s（实际打 " + offensive + "）", !"4s".equals(offensive));
        eq("有人立直且自己还远 → 打现物 4s（贝塔弃和）", defensive, "4s");

        // ---------- ③ 打点：同等效率时留宝牌
        // 宝牌指示牌 7z(中) → 宝牌是 5z(白)；1z 与 5z 的效率完全相同，只有"留不留宝牌"不同
        final String hand2 = "2m3m4m5m6m7m2p3p4p5p5p1z5z9s";
        Bot.HandState st2 = state(hand2, 0);
        st2.doraIndicators = List.of(Tiles.parseKind("7z"));
        List<Object> cands2 = new ArrayList<>();
        for (int id : parse(hand2)) {
            String c = Tiles.kindToStr(Tiles.kind(id));
            if (!cands2.contains(c)) {
                cands2.add(c);
            }
        }
        eq("自检前提：7z 指示牌 ⇒ 宝牌是 5z", Tiles.doraFrom(Tiles.parseKind("7z")),
                Tiles.parseKind("5z"));
        eq("1z 与 5z 效率相同时留下宝牌（打 1z）", Bot.chooseDiscard(st2, cands2), "1z");
        eq("宝牌计数：手里那张 5z", mahjong.rules.HandEval.doraCount(
                st2.counts, List.of(), st2.doraIndicators), 1);

        // ---------- ④ 鸣き役：鸣完还有没有役
        Bot.HandState yaku = state("2m3m4m5m6m7m2p3p4p5p5p1z2z3z4z1s4s", 0);
        check("碰白（三元牌）→ 有役", Bot.hasYakuPlan(yaku, yaku.counts.clone(),
                new Meld(Meld.Kind.PON,
                        new int[]{Tiles.id(Tiles.parseKind("5z"), 1),
                                  Tiles.id(Tiles.parseKind("5z"), 2),
                                  Tiles.id(Tiles.parseKind("5z"), 3)},
                        -1, Tiles.id(Tiles.parseKind("5z"), 0))));
        check("碰东（自风/场风）→ 有役", Bot.isYakuhai(yaku, Tiles.parseKind("1z")));
        check("碰南（不是自风也不是场风）→ 不是役牌",
                !Bot.isYakuhai(yaku, Tiles.parseKind("2z")));
        // 断幺九：全是中张 + 食断成立 → 有役；带一张幺九 → 没役
        Bot.HandState simples = state("2m3m4m5m6m7m2p3p4p5p5p2s3s4s5s6s7s", 0);
        check("全中张 + 食断 → 碰 2s 有役", Bot.hasYakuPlan(simples, simples.counts.clone(),
                new Meld(Meld.Kind.PON,
                        new int[]{Tiles.id(Tiles.parseKind("2s"), 1),
                                  Tiles.id(Tiles.parseKind("2s"), 2),
                                  Tiles.id(Tiles.parseKind("2s"), 3)},
                        -1, Tiles.id(Tiles.parseKind("2s"), 0))));
        Bot.HandState withTerminal = state("2m3m4m5m6m7m2p3p4p1p1p2s3s4s5s6s7s", 0);
        check("手里有 1p → 断幺不成立、碰 2s 无役（" + "被放掉）",
                !Bot.hasYakuPlan(withTerminal, withTerminal.counts.clone(),
                        new Meld(Meld.Kind.PON,
                                new int[]{Tiles.id(Tiles.parseKind("2s"), 1),
                                          Tiles.id(Tiles.parseKind("2s"), 2),
                                          Tiles.id(Tiles.parseKind("2s"), 3)},
                                -1, Tiles.id(Tiles.parseKind("2s"), 0))));
        Bot.HandState noKuitan = state("2m3m4m5m6m7m2p3p4p5p5p2s3s4s5s6s7s", 0);
        noKuitan.kuitan = false;
        check("食断关闭时同样的牌没有役（取舍随规则走）",
                !Bot.hasYakuPlan(noKuitan, noKuitan.counts.clone(),
                        new Meld(Meld.Kind.PON,
                                new int[]{Tiles.id(Tiles.parseKind("2s"), 1),
                                          Tiles.id(Tiles.parseKind("2s"), 2),
                                          Tiles.id(Tiles.parseKind("2s"), 3)},
                                -1, Tiles.id(Tiles.parseKind("2s"), 0))));
        // 混一色：全是万子 + 字牌
        Bot.HandState flush = state("1m1m2m3m4m5m6m7m8m9m9m1z2z3z", 0);
        check("全万子 + 字牌 → 混一色计划成立", Bot.hasYakuPlan(flush, flush.counts.clone(),
                new Meld(Meld.Kind.PON,
                        new int[]{Tiles.id(Tiles.parseKind("1z"), 1),
                                  Tiles.id(Tiles.parseKind("1z"), 2),
                                  Tiles.id(Tiles.parseKind("1z"), 3)},
                        -1, Tiles.id(Tiles.parseKind("1z"), 0))));

        // ---------- ⑤ 立直 vs 默听（用真牌桌问打点，因为要跑 Evaluator）
        // ⚠ 自家回合的手牌是 **14 张**：多带一张 9s 当"要打掉的那张"，判据内部会把它减掉
        // 清一色门清两面听（123m 456m 789m 11m 23m 听 1m/4m）+ 9s：不立直也有 6 番以上 → 默听
        Round rBig = newRound();
        rBig.hand[1].addAll(parse("1m2m3m4m5m6m7m8m9m1m1m2m3m9s"));
        // `scoreIfWin` 的张数契约：荣和要传 **13 张形态**（和了牌不在手里）→ 另起一张桌子
        Round rBig13 = newRound();
        rBig13.hand[1].addAll(parse("1m2m3m4m5m6m7m8m9m1m1m2m3m"));
        int[] bigAfter = rBig13.concealCounts(1);
        check("自检前提：打掉 9s 后是听牌", mahjong.rules.HandEval.tenpai(bigAfter, 0));
        int bigHan = 0;
        for (int wk : Agari.waits(bigAfter, 0)) {
            mahjong.rules.Evaluator.HandScore s = rBig13.scoreIfWin(1, wk, false, false);
            bigHan = Math.max(bigHan, s == null ? 0 : s.totalHan());
        }
        check("自检前提：不立直也有 ≥4 番（实际 " + bigHan + " 番）", bigHan >= 4);
        Bot.HandState bigState = Bot.HandState.of(rBig, 1);
        check("不立直也有 ≥4 番 → 默听", !Bot.shouldDeclareRiichi(rBig, 1, bigState, "9s"));
        // 只有平和+断幺的普通手（2 番）：不立直不够大 → 立直
        // ⚠ 别用带 123m456m789m 的牌型：那是**一気通貫**（+2 番），会不小心变成 4 番
        Round rSmall = newRound();
        rSmall.hand[1].addAll(parse("2m3m4m5m6m7m2p3p4p5p5p6p7p9s"));
        Round rSmall13 = newRound();
        rSmall13.hand[1].addAll(parse("2m3m4m5m6m7m2p3p4p5p5p6p7p"));
        int[] smallAfter = rSmall13.concealCounts(1);
        check("自检前提：普通手打掉 9s 后是听牌",
                mahjong.rules.HandEval.tenpai(smallAfter, 0));
        int smallHan = 0;
        for (int wk : Agari.waits(smallAfter, 0)) {
            mahjong.rules.Evaluator.HandScore s = rSmall13.scoreIfWin(1, wk, false, false);
            smallHan = Math.max(smallHan, s == null ? 0 : s.totalHan());
        }
        check("自检前提：不立直只有小牌（实际 " + smallHan + " 番）", smallHan > 0 && smallHan < 4);
        Bot.HandState smallState = Bot.HandState.of(rSmall, 1);
        check("不立直只有小牌 → 立直", Bot.shouldDeclareRiichi(rSmall, 1, smallState, "9s"));

        // ---------- ⑥ HandState 只读公开信息（置换不变式，与观测同一条纪律）
        Round r = newRound();
        r.debugSetup();
        Bot.HandState a = Bot.HandState.of(r, 0);
        for (int s = 1; s < 4; s++) {
            r.hand[s].clear();
            for (int i = 0; i < 13; i++) {
                r.hand[s].add(Tiles.id((i * 7 + 3) % 34, i / 34));
            }
            r.furitenPerm[s] = true;
        }
        Bot.HandState b = Bot.HandState.of(r, 0);
        eq("置换别家手牌后 teacher 视图不变", json(a), json(b));

        // ---------- ⑦ 实战覆盖：三条取舍必须真的被走到过（否则上面全是死代码）
        Bot.debugResetCounts();
        for (int g = 0; g < 3; g++) {
            Table tt = new Table("TCH" + g, "teacher桌", Rules.defaults());
            tt.botDelayMs = 0;
            tt.roundDelayMs = 0;
            tt.debugDeterministicSeed = true;
            tt.debugMaxHands = 2;
            tt.seedBase = 90210L + g * 7919L;
            for (int i = 0; i < 4; i++) {
                tt.addBot(i);
            }
            tt.playGame();
        }
        System.out.println("  [覆盖] teacher 取舍：弃和 " + Bot.debugFoldCount + " 次、默听 "
                + Bot.debugDamaCount + " 次、因无役放掉鸣牌 " + Bot.debugNoYakuRefuseCount + " 次");
        check("实局里走过「有人立直 → 弃和」这条", Bot.debugFoldCount > 0);
        check("实局里走过「鸣完没役 → 放掉」这条", Bot.debugNoYakuRefuseCount > 0);
    }

    /** teacher 视图的稳定文本表示（置换不变式对比用）。 */
    private static String json(Bot.HandState st) {
        StringBuilder sb = new StringBuilder();
        sb.append(Arrays.toString(st.counts)).append('|').append(st.meldCount).append('|');
        sb.append(Arrays.toString(st.visible)).append('|');
        for (int[] rv : st.rivers) {
            sb.append(Arrays.toString(rv)).append(',');
        }
        sb.append(Arrays.toString(st.riichi)).append('|').append(st.seat).append('|')
                .append(st.dealer).append('|').append(st.roundWind).append('|').append(st.turn)
                .append('|').append(st.doraIndicators).append('|').append(st.kuitan);
        return sb.toString();
    }

    // ------------------------------------------------------------- 训练接口

    /** 探针对局的小局数（见 {@link #runProbe}：只需够短，不必整场）。 */
    private static final int PROBE_HANDS = 4;

    /** 一场探针对局的结果。 */
    private static final class GameProbe {
        final List<String> choices = new ArrayList<>();
        final Map<String, Integer> kinds = new HashMap<>();
        final List<Map<String, Object>> hands = new ArrayList<>();
        final int[] finalScores = new int[4];

        int sum() {
            int s = 0;
            for (int v : finalScores) {
                s += v;
            }
            return s;
        }
    }

    /**
     * 用同一份策略跑几个小局，记录每次决策的(类型, 动作键)与终局分数。
     *
     * <p>只跑 {@link #PROBE_HANDS} 个小局：整场半庄要 2~3 秒，而这里要跑好几个（可复现、
     * 注入生效、非法动作不打断），攒起来会把 L1 从 67 秒拖到近 90 秒。几个小局足够——
     * 鸣牌询问每小局出现约 5 次，决策序列也足够长到能区分两个策略。
     *
     * @param policy 四家共用的策略；{@code null} = 内置牌效机器人
     */
    private static GameProbe runProbe(long seed, mahjong.ai.Policy policy) {
        Table t = new Table("PROBE", "探针桌", Rules.defaults());
        t.botDelayMs = 0;
        t.roundDelayMs = 0;
        t.debugDeterministicSeed = true;
        t.debugMaxHands = PROBE_HANDS;
        t.seedBase = seed;
        GameProbe p = new GameProbe();
        for (int i = 0; i < 4; i++) {
            t.policy[i] = policy;
            t.addBot(i);
        }
        t.debugChoiceTap = (d, cmd) -> {
            p.kinds.merge(d.kind, 1, Integer::sum);
            mahjong.ai.Action a = mahjong.ai.Action.fromCmd(cmd);
            p.choices.add(d.kind + "|" + (a == null ? "<非法回包>" : a.key()));
        };
        t.debugEventTap = (recipient, ev) -> {
            if (recipient == -1 && "round_end".equals(Json.str(ev, "ev", ""))) {
                p.hands.add(ev);
            }
        };
        t.playGame();
        for (int i = 0; i < 4; i++) {
            p.finalScores[i] = t.seat(i).score;
        }
        return p;
    }

    /**
     * 训练接口（{@code mahjong.ai} / {@code mahjong.train}）的不变式。
     *
     * <p>这一组的核心是**反作弊 + 可复现**两条：
     * <ol>
     *   <li><b>观测只含合法信息</b>：置换别家手牌 / 别家振听后观测必须逐字节不变，
     *       同时公开信息（他家立直）变化必须被反映 —— 后者是**正向对照**，
     *       否则"脏观测被冻住"也能让不变式通过（假绿）。</li>
     *   <li><b>同种子可复现 + 策略注入真的生效</b>：同一种子两次的决策序列必须一致，
     *       而换一个策略必须得到**不同**的决策序列（否则"注入"根本没接上）。</li>
     * </ol>
     * 以及策略的三种失败方式（非法动作 / 抛异常 / 返回 null）都不得打断对局。
     */
    private static void trainingInterfaceTests() {
        // ---------- ① 动作空间：槽位 ↔ 牌码双射、键解析往返、回包形状
        eq("固定动作头大小", mahjong.ai.Action.FIXED_ACTIONS, 79);
        boolean slotsOk = true;
        for (int slot = 0; slot < mahjong.ai.Action.TILE_SLOTS; slot++) {
            String code = mahjong.ai.Action.tileCode(slot);
            if (mahjong.ai.Action.tileIndex(code) != slot) {
                slotsOk = false;
            }
        }
        check("37 个牌码槽位往返一致", slotsOk);
        check("赤五与普通五占不同槽位",
                mahjong.ai.Action.tileIndex("0m") != mahjong.ai.Action.tileIndex("5m"));
        eq("打牌头下标 9m", mahjong.ai.Action.discard("9m").index(), 8);
        eq("立直头紧接打牌头", mahjong.ai.Action.riichi("9m").index(), 37 + 8);
        eq("固定头最后一个槽是九种九牌", mahjong.ai.Action.of("kyuushu").index(), 78);
        eq("吃/杠不在固定头里", mahjong.ai.Action.chi(List.of("1m", "2m")).index(), -1);
        boolean parseOk = true;
        for (String k : new String[]{"discard:5m", "discard:0p/tsumogiri", "riichi:1z", "tsumo",
                "ron", "pon", "pass", "kyuushu", "kan:ankan:7z", "chi:1m+2m"}) {
            mahjong.ai.Action a = mahjong.ai.Action.parse(k);
            if (a == null || !k.equals(a.key())) {
                parseOk = false;
                failures.add("动作键解析往返失败: " + k);
            }
        }
        check("动作键解析往返（含赤五/摸切/吃/杠）", parseOk);
        // 从选项展开动作集：这是"合法动作 + 掩码"的唯一来源
        List<Map<String, Object>> demoOpts = new ArrayList<>();
        demoOpts.add(Json.obj("type", "discard", "tiles", Json.arr("1m", "9p")));
        demoOpts.add(Json.obj("type", "riichi", "tiles", Json.arr("1m")));
        demoOpts.add(Json.obj("type", "kan",
                "kans", Json.arr(Json.obj("kind", "ankan", "tile", "7z"))));
        demoOpts.add(Json.obj("type", "kyuushu"));
        List<mahjong.ai.Action> expanded = mahjong.ai.Action.enumerate(demoOpts);
        eq("选项展开数量（2 打牌 + 1 立直 + 1 杠 + 九种九牌）", expanded.size(), 5);
        eq("选项展开保序", expanded.get(0).key(), "discard:1m");
        eq("选项展开保序（末项）", expanded.get(4).key(), "kyuushu");
        eq("吃回包原样两张", Json.write(mahjong.ai.Action.chi(List.of("2m", "3m")).toCmd()),
                "{\"type\":\"chi\",\"tiles\":[\"2m\",\"3m\"]}");
        eq("杠回包带 kind/tile", Json.write(mahjong.ai.Action.kan("daiminkan", "5s").toCmd()),
                "{\"type\":\"kan\",\"kind\":\"daiminkan\",\"tile\":\"5s\"}");
        eq("摸切只在声明时出现", Json.write(mahjong.ai.Action.discard("5m").toCmd()),
                "{\"type\":\"discard\",\"tile\":\"5m\"}");

        // ---------- ② 观测：字段白名单 + 反作弊不变式（含正向对照）
        Table t = new Table("OBS", "观测桌", Rules.defaults());
        t.debugDeterministicSeed = true;
        t.seedBase = 4242L;
        for (int i = 0; i < 4; i++) {
            t.addBot(i);
        }
        Round r = new Round(t, 0, 1, 0, 0, new int[]{25000, 25000, 25000, 25000}, 0, 1234567L);
        r.debugSetup();
        int drawn = r.debugOpeningTile();
        List<Map<String, Object>> opts = r.debugTurnOptions(0, drawn);
        check("自家回合选项里必有打牌", !mahjong.ai.Action.enumerate(opts).isEmpty());
        mahjong.ai.Observation o1 =
                mahjong.ai.Observation.ofTurn(r, 0, opts, drawn, false, null);
        Map<String, Object> j1 = o1.toJson();
        Set<String> want = new java.util.TreeSet<>(Arrays.asList("v", "seat", "kind", "hand",
                "hand_red", "drawn", "player_draws", "menzen", "self_riichi", "furiten", "melds",
                "discards", "dora_indicators", "riichi", "ippatsu", "scores", "round",
                "tiles_left", "dead_wall_left", "total_discards", "kan_count", "any_call",
                "visible", "haitei", "houtei", "rinshan", "from", "called_tile", "win_note",
                "legal"));
        eq("观测字段白名单（新增字段必须显式登记）", new java.util.TreeSet<>(j1.keySet()), want);

        // 置换**全部隐藏信息**：别家手牌、别家门清标记、别家振听
        for (int s = 1; s < 4; s++) {
            r.hand[s].clear();
            for (int i = 0; i < 13; i++) {
                r.hand[s].add(Tiles.id(33 - (i % 34), i / 34));
            }
            r.menzen[s] = false;
        }
        r.furitenPerm[1] = true;
        r.furitenTemp[2] = true;
        mahjong.ai.Observation o2 =
                mahjong.ai.Observation.ofTurn(r, 0, opts, drawn, false, null);
        eq("置换别家隐藏信息后观测逐字节不变", Json.write(o2.toJson()), Json.write(j1));
        // 正向对照：公开信息变化必须被反映（否则"不变"可能是观测整体失效的假绿）
        r.riichi[1] = true;
        check("公开信息（他家立直）变化必须反映到观测",
                !Json.write(mahjong.ai.Observation.ofTurn(r, 0, opts, drawn, false, null).toJson())
                        .equals(Json.write(j1)));
        r.riichi[1] = false;
        // 自家信息变化也必须反映（第二个正向对照）
        r.furitenPerm[0] = true;
        check("自家振听变化必须反映到观测",
                !Json.write(mahjong.ai.Observation.ofTurn(r, 0, opts, drawn, false, null).toJson())
                        .equals(Json.write(j1)));
        r.furitenPerm[0] = false;

        // ---------- ③ 策略适配器：合法动作原样下发，非法/异常/空一律退回内置机器人
        mahjong.ai.Decision dec =
                new mahjong.ai.Decision(o1, r, "turn", opts, null);
        check("合法动作原样下发",
                Json.write(mahjong.ai.Policies.fromAction(d -> d.legal().get(0)).decide(dec))
                        .equals(Json.write(o1.legal.get(0).toCmd())));
        mahjong.ai.Action notLegal = null;
        for (int k = 0; k < Tiles.KIND_COUNT && notLegal == null; k++) {
            mahjong.ai.Action cand = mahjong.ai.Action.discard(Tiles.kindToStr(k));
            if (o1.indexOf(cand) < 0) {
                notLegal = cand;
            }
        }
        final mahjong.ai.Action bogus = notLegal;
        eq("非法动作被挡下并退回内置机器人",
                Json.write(mahjong.ai.Policies.fromAction(d -> bogus).decide(dec)),
                Json.write(Bot.decide(r, 0, "turn", opts, null)));
        eq("返回 null 也退回内置机器人",
                Json.write(mahjong.ai.Policies.fromAction(d -> null).decide(dec)),
                Json.write(Bot.decide(r, 0, "turn", opts, null)));
        eq("策略抛异常也退回内置机器人",
                Json.write(mahjong.ai.Policies.fromAction(d -> {
                    throw new IllegalStateException("boom");
                }).decide(dec)),
                Json.write(Bot.decide(r, 0, "turn", opts, null)));

        // ---------- ④ 策略注入真的生效 + 同种子可复现 + 鸣牌段也在漏斗里
        GameProbe p1 = runProbe(777L, null);
        GameProbe p2 = runProbe(777L, null);
        eq("同种子两次的决策序列逐条一致", p1.choices, p2.choices);
        eq("同种子两次的终局分数一致", Arrays.toString(p1.finalScores),
                Arrays.toString(p2.finalScores));
        eq("整场点数和守恒", p1.sum(), 100000);
        check("漏斗在自家回合被调用", p1.kinds.getOrDefault("turn", 0) > 0);
        // ⚠ 这条钉住一个真实的坑：鸣牌询问**不走 Round.ask()**，所以 debugAskTap 看不见它，
        //   只有新漏斗（debugChoiceTap）两段都能看见。改回"只挂 askTap"就会红。
        check("漏斗在鸣牌段也被调用（debugAskTap 看不见鸣牌）", p1.kinds.getOrDefault("claim", 0) > 0);
        eq("鸣牌决策与自家回合决策都能被记录",
                p1.choices.size(), p1.kinds.getOrDefault("turn", 0) + p1.kinds.getOrDefault("claim", 0));

        GameProbe pLast = runProbe(777L, mahjong.ai.Policies.fromAction(
                d -> d.legal().get(d.legal().size() - 1)));
        check("注入的策略确实改变了行为（与内置机器人决策序列不同）",
                !pLast.choices.equals(p1.choices));
        eq("注入策略的终局也守恒", pLast.sum(), 100000);

        // 三种失败方式在**漏斗层**验证（不必再打整场，省 L1 时间）：
        // 策略抛异常 / 返回 null，都必须退回内置机器人，而不是把异常抛给牌桌线程。
        Table funnel = new Table("FUNNEL", "漏斗桌", Rules.defaults());
        mahjong.ai.Decision fdec = new mahjong.ai.Decision(o1, r, "turn", opts, null);
        final String botCmd = Json.write(Bot.decide(r, 0, "turn", opts, null));
        funnel.policy[0] = d -> {
            throw new IllegalStateException("故意炸");
        };
        eq("漏斗兜底：策略抛异常 → 内置机器人", Json.write(funnel.decideBot(0, fdec)), botCmd);
        funnel.policy[0] = d -> null;
        eq("漏斗兜底：策略返回 null → 内置机器人", Json.write(funnel.decideBot(0, fdec)), botCmd);
        funnel.policy[0] = null;
        eq("未注入策略时就是内置机器人", Json.write(funnel.decideBot(0, fdec)), botCmd);
        // 乱发动作类型的策略：状态的兜底（立直/杠不成立退回摸切、吃/碰校验）各有既有回归，
        // 这里只钉住"**整场不会被打断**"这一条（异常冒到牌桌线程会让整场静默死亡）。
        GameProbe pIllegal = runProbe(777L, d -> Json.obj("type", "chi",
                "tiles", Json.arr("1m", "9m")));
        eq("发非法动作的策略不会打断整场", pIllegal.sum(), 100000);

        // ---------- ⑤ runner：种子可复现、顺位是排列、统计自洽
        long base = 20260101L;
        check("每场种子互不相同",
                mahjong.train.SelfPlay.seedFor(base, 0) != mahjong.train.SelfPlay.seedFor(base, 1)
                        && mahjong.train.SelfPlay.seedFor(base, 1)
                           != mahjong.train.SelfPlay.seedFor(base, 2));
        eq("同一场种子可复现", mahjong.train.SelfPlay.seedFor(base, 5),
                mahjong.train.SelfPlay.seedFor(base, 5));
        eq("顺位：同点按座次拆开",
                Json.write(mahjong.train.SelfPlay.placementOf(
                        new int[]{25000, 25000, 25000, 25000})), "[1,2,3,4]");
        eq("顺位：按分数降序",
                Json.write(mahjong.train.SelfPlay.placementOf(
                        new int[]{10000, 30000, 30000, 20000})), "[4,1,2,3]");

        mahjong.train.SelfPlay.Config cfg = new mahjong.train.SelfPlay.Config();
        cfg.games = 1;
        cfg.workers = 1;
        cfg.seedBase = base;
        cfg.maxHands = PROBE_HANDS;
        mahjong.train.SelfPlay.Summary sum = mahjong.train.SelfPlay.run(cfg);
        eq("runner 跑了 1 场", sum.games, 1);
        check("runner 打出了小局", sum.hands > 0);
        check("runner 统计了决策数", sum.decisions > 0);
        int placeSum = 0;
        for (Object v : (List<?>) sum.perGame.get(0).get("placement")) {
            placeSum += ((Number) v).intValue();
        }
        eq("每场顺位之和 = 1+2+3+4", placeSum, 10);
        mahjong.train.SelfPlay.PolicyStat st = sum.byPolicy.get("teacher");
        check("按策略聚合了逐手统计", st != null && st.seatHands == 4 * sum.hands);
        check("和了率在 0..1 之间", st.winRate() >= 0 && st.winRate() <= 1);
    }

    // ------------------------------------------------------------- 王牌 / 岭上


    /**
     * 岭上牌（杠后从王牌摸的那 4 张）在**报文层面**的账要算对。
     *
     * <p>报障：「杠后摸的牌应该从岭上摸，但岭上牌并没有减少」。根因是 `dead_wall_left`
     * 只在小局开始时发过一次，杠后摸牌不重发 → 界面上的「岭上 N」整局都停在 4。
     * 这条账只体现在**报文**里（`tiles_left` 看不出它：岭上摸牌不动 livePos/liveEnd），
     * 所以这里用 {@link Table#debugEventTap} 抓整场机器人模拟的真实报文来断言：
     * <ol>
     *   <li>每条 `draw` 都必须带 `dead_wall_left`，且小局内**单调不增**；</li>
     *   <li>`rinshan=true` 的摸牌（杠后摸）必须让这个数**逐次递减 1**（4 → 3 → 2 → 1）；</li>
     *   <li>整场模拟里至少要出现一次杠（否则上面的递减根本没被走过，等于没测）。</li>
     * </ol>
     */
    private static void rinshanTests() {
        // 抓「每局的岭上账」：轮次标记 → 该局每条 draw 的 (rinshan, dead_wall_left)
        List<int[]> draws = new ArrayList<>();      // {rinshan?1:0, deadWallLeft}
        List<Integer> roundStarts = new ArrayList<>();
        // 每局的 draw 序列（用小局开始切分）
        List<List<int[]>> perRound = new ArrayList<>();
        perRound.add(new ArrayList<>());
        // 三种杠各自出现几次 / 各自之后摸到几次岭上牌（下标 0=暗杠 1=加杠 2=大明杠）
        int[] kanSeen = new int[3];
        int[] kanFollowedByRinshan = new int[3];
        int[] pendingKan = {-1};                    // 刚宣布的杠（等待紧随其后的岭上摸牌）

        int rinshanSeen = 0;
        int gamesRun = 0;
        final int maxGames = 12;                    // 兜底上限：三种杠都见过了就提前收工
        // ⚠ 机器人**默认从不开杠**（那样整条岭上路径在模拟里一次都走不到 —— 这正是
        //   这个 bug 藏了这么久的原因）。这里临时打开「一有机会就开杠」。
        final boolean savedAlwaysKan = Bot.debugAlwaysKan;
        Bot.debugAlwaysKan = true;
        try {
            for (int game = 0; game < maxGames; game++) {
                if (rinshanSeen > 0 && kanSeen[0] > 0 && kanSeen[1] > 0 && kanSeen[2] > 0) {
                    break;                          // 三种杠都覆盖到了，不必再跑
                }
                gamesRun++;
                Table t = new Table("RINSHAN", "岭上自测桌", Rules.defaults());
                t.botDelayMs = 0;
                t.roundDelayMs = 0;
                t.seedBase = 90000L + game * 4099;
                t.debugDeterministicSeed = true;
                for (int i = 0; i < 4; i++) {
                    t.addBot(i);
                }
                t.debugEventTap = (recipient, ev) -> {
                    String name = String.valueOf(ev.get("ev"));
                    // ⚠ 逐座位发送的事件（round_start / draw）对 4 个座位各发一次 → 只认座位 0；
                    //   广播事件（meld / dora_reveal …）只发一次 → 认 -1。合起来每个事件恰好一份。
                    boolean perSeat = "round_start".equals(name) || "draw".equals(name);
                    if (perSeat ? (recipient != 0) : (recipient != -1)) {
                        return;
                    }
                    if ("round_start".equals(name)) {
                        Object dwl = ev.get("dead_wall_left");
                        roundStarts.add(dwl == null ? -1 : ((Number) dwl).intValue());
                        perRound.add(new ArrayList<>());
                    } else if ("draw".equals(name)) {
                        Object dwl = ev.get("dead_wall_left");
                        int left = dwl == null ? -1 : ((Number) dwl).intValue();
                        boolean rinshan = Boolean.TRUE.equals(ev.get("rinshan"));
                        perRound.get(perRound.size() - 1).add(new int[]{rinshan ? 1 : 0, left});
                        draws.add(new int[]{rinshan ? 1 : 0, left});
                        if (rinshan && pendingKan[0] >= 0) {
                            kanFollowedByRinshan[pendingKan[0]]++;
                            pendingKan[0] = -1;
                        }
                    } else if ("meld".equals(name)) {
                        String kind = String.valueOf(ev.get("kind"));
                        int idx = "ankan".equals(kind) ? 0 : ("kakan".equals(kind) ? 1
                                : ("daiminkan".equals(kind) ? 2 : -1));
                        if (idx >= 0) {
                            kanSeen[idx]++;
                            pendingKan[0] = idx;
                        }
                    }
                };
                try {
                    t.playGame();
                } catch (RuntimeException e) {
                    failures.add("岭上自测的整场模拟异常: " + e);
                    fail++;
                    return;
                }
                for (int[] d : draws) {
                    if (d[0] == 1) {
                        rinshanSeen++;
                    }
                }
            }
        } finally {
            Bot.debugAlwaysKan = savedAlwaysKan;
        }

        // ① 小局开始时报 4
        int kanRoundCount = 0;
        for (List<int[]> round : perRound) {
            for (int[] d : round) {
                if (d[0] == 1) {
                    kanRoundCount++;
                    break;
                }
            }
        }
        System.out.println("  [岭上] 抓到 " + roundStarts.size() + " 局 / " + draws.size()
                + " 次摸牌（跑了 " + gamesRun + " 场），其中岭上摸牌 " + rinshanSeen
                + " 次（含杠的小局 " + kanRoundCount
                + " 个）—— 机器人临时开了「一有机会就开杠」，否则这条路径一次都走不到");
        boolean startOk = !roundStarts.isEmpty();
        for (int v : roundStarts) {
            startOk &= (v == Round.RINSHAN_TILES);
        }
        check("岭上：每局开始都报 4 张（实际抓到 " + roundStarts.size() + " 局）", startOk);

        // ② 每条 draw 都带这个字段，且小局内不增
        boolean monotone = true;
        int missing = 0;
        for (List<int[]> round : perRound) {
            int prev = Round.RINSHAN_TILES;
            for (int[] d : round) {
                if (d[1] < 0) {
                    missing++;
                } else if (d[1] > prev) {
                    monotone = false;
                } else {
                    prev = d[1];
                }
            }
        }
        eq("岭上：每条 draw 都带 dead_wall_left（缺失数）", missing, 0);
        check("岭上：小局内 dead_wall_left 单调不增（只减不增，不会自己涨回去）", monotone);

        // ③ 杠后摸牌必须把岭上数逐次减 1
        boolean stepOk = true;
        int checkedRounds = 0;
        StringBuilder bad = new StringBuilder();
        for (List<int[]> round : perRound) {
            int used = 0;
            boolean sawKan = false;
            for (int[] d : round) {
                if (d[0] == 1) {
                    used++;
                    sawKan = true;
                    if (d[1] != Round.RINSHAN_TILES - used) {
                        stepOk = false;
                        if (bad.length() < 120) {
                            bad.append("第 ").append(checkedRounds + 1).append(" 局第 ").append(used)
                               .append(" 次岭上摸牌报 ").append(d[1])
                               .append("（应为 ").append(Round.RINSHAN_TILES - used).append("）；");
                        }
                    }
                }
            }
            if (sawKan) {
                checkedRounds++;
            }
        }
        check("岭上：杠后摸牌把剩余数逐次减 1（4→3→2→1，验了 " + checkedRounds + " 局）" + bad,
                stepOk);
        check("岭上：整场模拟里确实开过杠（否则上面的递减没被走到）", rinshanSeen > 0);
        // ④ 「无论明杠、加杠、暗杠，都要从岭上摸」：出现过的每一种杠，都必须紧接着摸到岭上牌
        String[] kanKindNames = {"暗杠", "加杠", "大明杠"};
        for (int k = 0; k < 3; k++) {
            if (kanSeen[k] > 0) {
                check("岭上：" + kanKindNames[k] + "之后确实从岭上摸牌"
                              + "（杠 " + kanSeen[k] + " 次，其中 " + kanFollowedByRinshan[k] + " 次紧随岭上摸牌）",
                        kanFollowedByRinshan[k] > 0);
            }
        }
        System.out.println("  [岭上] 杠的种类：暗杠 " + kanSeen[0] + " → 岭上 " + kanFollowedByRinshan[0]
                + "；加杠 " + kanSeen[1] + " → 岭上 " + kanFollowedByRinshan[1]
                + "；大明杠 " + kanSeen[2] + " → 岭上 " + kanFollowedByRinshan[2]
                + "（不被抢杠/流局打断的杠才会紧接着摸岭上）");
        pass++;
    }

    // ------------------------------------------------------------- 杠的上限

    /**
     * **一局最多 4 次杠**：第 4 次之后不许再开（连 `kan` 选项都不下发）。
     *
     * <p>上限的来源是死数：王牌里只有 4 张岭上牌，每开一次杠要摸走一张
     * （{@code Round.RINSHAN_TILES}）。这条规则有**两条**要守的线，模拟里都查：
     * <ol>
     *   <li>**选项线**：一局里已经开了 4 次杠之后，任何一次 `ask` 的 options 里都不得再出现
     *       `type == "kan"`（客户端就是按 options 画按钮的，不下发 = 点不出来）；</li>
     *   <li>**报文线**：一局内杠的 `meld` 事件不超过 4 个，岭上摸牌次数不超过杠数
     *       （杠数是分子、岭上牌是分母，任何"没开杠却摸岭上"都是漏）。</li>
     * </ol>
     * 再用一个**非法杠**（不存在的牌码）压一遍：服务端绝不能白摸岭上牌、也不能吃掉名额
     * —— 旧写法正是在这里漏的（无条件 `rinshanNext = true`）。
     */
    private static void kanLimitTests() {
        // ---------- ① 闸门（确定性断言）：开满 4 次之后不下发 kan 选项 ----------
        // 手里 4 张 1m（可暗杠）+ 3 张 5p（可大明杠），直接看选项里有没有 kan
        mahjong.game.Round g1 = newRound();
        g1.hand[1].addAll(parse("1m1m1m1m5p5p5p2m3m4m6m7m8m9m"));
        check("刚开局可以开杠（canKan）", g1.canKan());
        java.util.List<String> k0 = g1.debugTurnOptionTypes(1, Tiles.id(27, 1));
        check("手里 4 张同牌 → 出牌段下发 kan 选项: " + k0, k0.contains("kan"));

        g1.kanCount = 3;
        check("开过 3 次杠还能再开（第 4 次）", g1.canKan());
        g1.kanCount = Round.RINSHAN_TILES;              // 已经开满 4 次
        check("开满 " + Round.RINSHAN_TILES + " 次杠之后 canKan 为假", !g1.canKan());
        java.util.List<String> k4 = g1.debugTurnOptionTypes(1, Tiles.id(27, 1));
        check("开满 " + Round.RINSHAN_TILES + " 次杠之后**不再下发** kan 选项（禁止再开杠）: " + k4,
                !k4.contains("kan"));
        java.util.List<String> c4 = g1.debugClaimOptionTypes(1, 3, Tiles.id(13, 1));   // 别人打 5p
        check("开满 " + Round.RINSHAN_TILES + " 次杠之后大明杠也不下发 kan 选项: " + c4,
                !c4.contains("kan"));

        // 岭上牌那条线：4 张全摸走之后，同样禁止再开杠（即使 kanCount 还没到 4）
        mahjong.game.Round g3 = newRound();
        g3.hand[1].addAll(parse("1m1m1m1m5p5p5p2m3m4m6m7m8m9m"));
        g3.debugSetRinshanUsed(Round.RINSHAN_TILES);
        check("岭上牌用完（deadWallLeft = " + g3.deadWallLeft() + "）后 canKan 为假",
                g3.deadWallLeft() == 0 && !g3.canKan());
        check("岭上牌用完后出牌段不再下发 kan 选项",
                !g3.debugTurnOptionTypes(1, Tiles.id(27, 1)).contains("kan"));
        check("岭上牌用完后大明杠也不下发 kan 选项",
                !g3.debugClaimOptionTypes(1, 3, Tiles.id(13, 1)).contains("kan"));

        // 反向：次数没用完时，大明杠选项要在
        mahjong.game.Round g2 = newRound();
        g2.hand[1].addAll(parse("5p5p5p2m3m4m6m7m8m9m1m1m1m3m"));
        java.util.List<String> c1 = g2.debugClaimOptionTypes(1, 3, Tiles.id(13, 1));
        check("手里 3 张 5p + 别人打 5p → 下发大明杠 kan 选项: " + c1, c1.contains("kan"));

        // 海底那条线：摸到海底牌之后**不可以开杠**（`docs/日本麻将.md` §副露 L296）。
        // 不挡的话可以「海底暗杠 → 摸岭上 → 岭上开花」绕开海底的限制，
        // 而且岭上那张还会被 `atLastLiveTile()` 误判成海底摸月（牌山此时已经见底）。
        mahjong.game.Round g5 = newRound();
        g5.hand[1].addAll(parse("1m1m1m1m5p5p5p2m3m4m6m7m8m9m"));
        check("牌山还有牌时下发 kan 选项", g5.debugTurnOptionTypes(1, Tiles.id(27, 1)).contains("kan"));
        g5.debugDrainWallTo(0);
        check("牌山见底（atLastLiveTile）", g5.atLastLiveTile());
        check("摸到海底牌后 canKan 仍为真（闸门在选项层，按「海底」这一条单独判）", g5.canKan());
        java.util.List<String> kk = g5.debugTurnOptionTypes(1, Tiles.id(27, 1));
        check("摸到海底牌后**不再下发** kan 选项（暗杠/加杠都不行）: " + kk, !kk.contains("kan"));
        check("海底仍然可以自摸/打牌（只是不能杠）", kk.contains("discard"));

        // ---------- ② 整场模拟：报文层面也不许越界 ----------
        List<String> violations = new ArrayList<>();
        int roundsChecked = 0;
        int maxKansInRound = 0;
        int roundsWithFour = 0;
        int totalKans = 0;
        int totalRinshan = 0;
        List<int[]> perRound = new ArrayList<>();           // {杠数, 岭上摸牌数}
        int[] curKan = {0};
        int[] maxDora = {1};                                // 场上最多同时有几张宝牌指示牌

        final boolean savedAlwaysKan = Bot.debugAlwaysKan;
        Bot.debugAlwaysKan = true;
        try {
            for (int game = 0; game < 10; game++) {
                Table t = new Table("KANLIMIT", "杠上限自测桌", Rules.defaults());
                t.botDelayMs = 0;
                t.roundDelayMs = 0;
                t.seedBase = 51000L + game * 613;
                t.debugDeterministicSeed = true;
                for (int i = 0; i < 4; i++) {
                    t.addBot(i);
                }
                t.debugEventTap = (recipient, ev) -> {
                    String name = String.valueOf(ev.get("ev"));
                    if ("round_start".equals(name)) {
                        if (recipient != 0) {
                            return;
                        }
                        curKan[0] = 0;
                        perRound.add(new int[]{0, 0});
                    } else if ("meld".equals(name)) {
                        if (recipient != -1) {
                            return;
                        }
                        if (String.valueOf(ev.get("kind")).endsWith("kan")) {
                            curKan[0]++;
                            perRound.get(perRound.size() - 1)[0]++;
                            if (curKan[0] > Round.RINSHAN_TILES) {
                                violations.add("第 " + perRound.size() + " 局出现了第 " + curKan[0]
                                        + " 次杠（上限 " + Round.RINSHAN_TILES + "）");
                            }
                        }
                    } else if ("draw".equals(name)) {
                        if (recipient != 0) {
                            return;
                        }
                        if (Boolean.TRUE.equals(ev.get("rinshan"))) {
                            perRound.get(perRound.size() - 1)[1]++;
                        }
                    } else if ("dora_reveal".equals(name)) {
                        if (recipient != -1) {
                            return;
                        }
                        // 每开一次杠翻一张新宝牌指示牌：1 + 杠数 ≤ 5（王牌里只有 5 个位置）
                        Object arr = ev.get("dora_indicators");
                        if (arr instanceof List) {
                            maxDora[0] = Math.max(maxDora[0], ((List<?>) arr).size());
                        }
                    }
                };
                // ⚠ 机器人座位的询问**不走网络**，所以选项要看 debugAskTap 而不是报文
                t.debugAskTap = (kind, options) -> {
                    if (curKan[0] >= Round.RINSHAN_TILES && optionsOfferKan(options)) {
                        violations.add("第 " + perRound.size() + " 局已开 " + curKan[0]
                                + " 次杠，" + kind + " 询问里还在下发 kan 选项");
                    }
                };
                t.playGame();
            }
        } catch (RuntimeException e) {
            failures.add("杠上限自测异常: " + e);
            fail++;
            return;
        } finally {
            Bot.debugAlwaysKan = savedAlwaysKan;
        }

        for (int[] pair : perRound) {
            roundsChecked++;
            maxKansInRound = Math.max(maxKansInRound, pair[0]);
            totalKans += pair[0];
            totalRinshan += pair[1];
            if (pair[0] >= Round.RINSHAN_TILES) {
                roundsWithFour++;
            }
            // 岭上牌是「杠」的分子：**每次杠最多摸一张**，绝不许多出来
            // （少了是允许的：加杠被抢杠 / 四杠散了 都会在摸岭上之前就结束本局）
            if (pair[1] > pair[0]) {
                violations.add("某局岭上摸牌 " + pair[1] + " 次 > 杠 " + pair[0] + " 次");
            }
        }
        System.out.println("  [杠上限] 跑了 10 场共 " + roundsChecked + " 局：杠 " + totalKans
                + " 次 / 岭上摸牌 " + totalRinshan + " 次；单局最多 " + maxKansInRound
                + " 次杠（开满 " + Round.RINSHAN_TILES + " 次的有 " + roundsWithFour + " 局）");
        check("杠上限：宝牌指示牌最多 " + (Round.RINSHAN_TILES + 1) + " 张"
                      + "（1 张开局 + 每次杠翻 1 张；王牌里只有 5 个位置），实际最多 " + maxDora[0],
                maxDora[0] <= Round.RINSHAN_TILES + 1);
        check("杠上限：整场模拟里单局杠数不超过 " + Round.RINSHAN_TILES + " 次，"
                + "且开满之后 ask 不再下发 kan 选项"
                + (violations.isEmpty() ? "" : "（" + violations.get(0) + "）"),
                violations.isEmpty());

        // ---------- ③ 非法/过期的杠动作：不许白摸岭上牌 ----------
        // 把出牌段上报的 `tile` 换成不存在的牌码 → turnKan 必然失败。
        // 旧写法无条件 `rinshanNext = true`，于是每条废杠都会从王牌白抽一张
        // （`rinshanPos` 跑到 `kanCount` 前面，第 4 次真杠反被挡住）。
        int[] badRinshan = {0};
        int[] badKans = {0};
        int[] badRounds = {0};
        int[] badAttempts = {0};   // 出牌段 offer 了 kan 的次数（= 一定会被拒绝的杠尝试）
        final boolean savedKan = Bot.debugAlwaysKan;
        final String savedOverride = Bot.debugKanTileOverride;
        Bot.debugAlwaysKan = true;
        Bot.debugKanTileOverride = "9z";        // 根本不存在的牌码
        try {
            // ⚠ 机器人的决策里有 Math.random()（未播种），单次会话不保证一定出现"废杠尝试"，
            //   所以这里跑到**确实构造出至少一次**为止（上限 8 局，实测 3 局内必然出现）。
            for (int game = 0; game < 8 && badAttempts[0] == 0; game++) {
                Table t = new Table("BADKAN", "非法杠自测桌", Rules.defaults());
                t.botDelayMs = 0;
                t.roundDelayMs = 0;
                // 与岭上用例同一组种子：已知那里会出现暗杠/加杠的 offer
                t.seedBase = 90000L + game * 4099;
                t.debugDeterministicSeed = true;
                for (int i = 0; i < 4; i++) {
                    t.addBot(i);
                }
                t.debugEventTap = (recipient, ev) -> {
                    String name = String.valueOf(ev.get("ev"));
                    if ("round_start".equals(name)) {
                        if (recipient == 0) {
                            badRounds[0]++;
                        }
                    } else if ("meld".equals(name) && recipient == -1
                            && String.valueOf(ev.get("kind")).endsWith("kan")) {
                        badKans[0]++;
                    } else if ("draw".equals(name) && recipient == 0
                            && Boolean.TRUE.equals(ev.get("rinshan"))) {
                        badRinshan[0]++;
                    }
                };
                t.debugAskTap = (kind, options) -> {
                    if ("turn".equals(kind) && optionsOfferKan(options)) {
                        // 出牌段给了 kan 选项 → 机器人（debugAlwaysKan）一定拿那个非法牌码去杠
                        // → turnKan 必然失败。旧写法就在这里白摸岭上牌。
                        badAttempts[0]++;
                    }
                };
                t.playGame();
            }
        } catch (RuntimeException e) {
            failures.add("非法杠自测异常: " + e);
            fail++;
            return;
        } finally {
            Bot.debugAlwaysKan = savedKan;
            Bot.debugKanTileOverride = savedOverride;
        }
        System.out.println("  [杠上限] 非法杠压测：" + badRounds[0] + " 局、被拒绝的杠尝试 "
                + badAttempts[0] + " 次、有效杠 " + badKans[0] + " 次（只有不走覆盖的大明杠会成立）、"
                + "岭上摸牌 " + badRinshan[0] + " 次");
        check("非法杠：确实构造出了被拒绝的杠尝试（否则下面那条断言是空转）："
                + badAttempts[0] + " 次", badAttempts[0] > 0);
        check("非法杠：岭上摸牌次数 == 有效杠次数（没有一条废杠白拿岭上牌）："
                      + badRinshan[0] + " vs " + badKans[0],
                badRinshan[0] == badKans[0]);
        check("非法杠：整局照常推进、没有卡死（跑了 " + badRounds[0] + " 局）", badRounds[0] > 0);
        pass++;
    }
    /** 这份选项列表里有没有 `type == "kan"`。 */
    private static boolean optionsOfferKan(List<Map<String, Object>> opts) {
        if (opts == null) {
            return false;
        }
        for (Map<String, Object> o : opts) {
            if ("kan".equals(o.get("type"))) {
                return true;
            }
        }
        return false;
    }

    /** 供调试：打印一手牌的评价。 */
    public static void dump(String hand, String win, boolean tsumo) {
        Evaluator.HandScore s = evalClosed(hand, win, tsumo, 1, 0, 0);
        System.out.println("手牌: " + hand + " 和了牌: " + win);
        System.out.println("  役: " + yakuNames(s));
        System.out.println("  番=" + s.han + " 符=" + s.fu + " 基本点=" + s.base
                + " 役满=" + s.yakuman + " valid=" + s.valid + " (" + s.reason + ")");
    }

    /** 供调试：统计向听。 */
    public static void dumpShanten(String hand) {
        int[] c = counts(hand);
        System.out.println(hand + " → 向听 " + Shanten.min(c, 0)
                + " 听牌 " + Agari.waits(c, 0));
    }

    static Map<String, Object> unusedMeld() {
        return new Meld(Meld.Kind.PON, new int[]{0, 0, 0}, 0, 0).toJson();
    }
}
