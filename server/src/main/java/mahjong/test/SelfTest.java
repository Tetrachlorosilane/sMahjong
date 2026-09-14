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
        roundClaimsTests();
        dropRepliesTests();
        jsonEncodingTests();
        yakuCodesTests();
        roundScoringTests();
        scoreTableTests();
        paymentTests();
        notenPenaltyTests();
        fourKanAbortTests();
        akaRuleTests();
        nagashiLivePathTest();
        simulationTest();
        rinshanTests();
        kanLimitTests();
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
        int[] c = counts(hand);
        WinContext ctx = new WinContext();
        ctx.rules = Rules.defaults();
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
        Evaluator.HandScore s7 = evalClosed("1m9m1p9p1s9s1z2z3z4z5z6z7z1m", "1m", false, 1, 0, 0);
        check("国士十三面双倍: " + yakuNames(s7), s7.yakuman == 2 && hasYaku(s7, "国士无双十三面"));
        eq("国士十三面上报 26 番（13 × 2）", reportedHan(s7, "国士无双十三面"), 26);
        eq("两倍役满合计番数 = 26", s7.totalHan(), 26);

        // 四暗刻单骑
        Evaluator.HandScore s8 = evalClosed("1m1m1m1p1p1p4p4p4p3s3s3s1z1z", "1z", true, 1, 0, 0);
        check("四暗刻单骑: " + yakuNames(s8), hasYaku(s8, "四暗刻单骑") && s8.yakuman == 2);
        eq("四暗刻单骑上报 26 番", reportedHan(s8, "四暗刻单骑"), 26);
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

        Evaluator.HandScore s13 = evalClosed("1m1m1m2m2m3m4m5m6m7m8m9m9m9m", "2m", false, 1, 0, 0);
        check("纯正九莲: " + yakuNames(s13), hasYaku(s13, "纯正九莲宝灯") && s13.yakuman == 2);

        // 清一色 + 二杯口 + 纯全 + 平和 = 累计役满
        Evaluator.HandScore s14 = evalClosed("1p1p1p1p2p2p3p3p7p7p8p8p9p9p", "1p", false, 1, 0, 0);
        check("累计役满: " + yakuNames(s14) + " han=" + s14.han, s14.han >= 13);
        check("含清一色", hasYaku(s14, "清一色"));
        check("含纯全带幺九", hasYaku(s14, "纯全带幺九"));
        check("含平和", hasYaku(s14, "平和"));

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

        // 连风雀头 4 符（配立直凑役）
        Evaluator.HandScore s3 = evalCtx("1z1z1m2m3m4m5m6m7m8m9m2p3p4p", "4p", false, 0, 0, 0, true);
        // 20 + 10(门前) + 4(连风雀头) + 2(单骑) = 36 → 40
        eq("连风雀头符", s3.fu, 40);

        // 幺九暗刻 8 符
        Evaluator.HandScore s4 = evalCtx("1m1m1m2m3m4m5m6m7m2p3p4p9s9s", "9s", false, 1, 0, 0, true);
        // 20 + 10 + 8(幺九暗刻) + 2(单骑) = 40
        eq("幺九暗刻符", s4.fu, 40);

        // 嵌张 +2
        Evaluator.HandScore s5 = evalCtx("1m1m1m2m3m4m5m6m7m2p3p4p9s9s", "3m", false, 1, 0, 0, true);
        // 111m 暗刻(8) + 嵌张(2)：20+10+8+2 = 40
        eq("嵌张符 40", s5.fu, 40);
    }

    // ------------------------------------------------------------- 振听 / 立直

    private static mahjong.game.Round newRound() {
        Table t = new Table("T", "t", Rules.defaults());
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
        r1.discards[1].add(Tiles.id(13, 1));
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

        // 立直条件：门前 + 点数 >= 1000 + 剩余 >= 4 + 打后听牌
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
        mahjong.game.Round r5 = newRound();
        r5.hand[1].addAll(parse("1m1m1m2m2m2m3m3m3m4m4m4m5p5p"));
        r5.scores[1] = 900;
        check("点数不足不可立直", !r5.canRiichi(1, Tiles.id(13, 1)));

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
        r4.discards[2].add(Tiles.id(13, 1));
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
        mahjong.game.Round r = newRound();
        r.kanCount = 3;
        r.kanByPlayer[0] = 3;
        check("杠 3 次：不流局", !r.fourKanAbortNow());

        r.kanCount = 4;
        r.kanByPlayer[0] = 2;
        r.kanByPlayer[1] = 2;
        check("2 家共开 4 次杠 → 四杠散了成立", r.fourKanAbortNow());

        mahjong.game.Round solo = newRound();
        solo.kanCount = 4;
        solo.kanByPlayer[2] = 4;
        check("同一人开满 4 次杠（四杠子）→ 不流局", !solo.fourKanAbortNow());

        mahjong.game.Round off = newRound();
        off.rules.fourKanAbort = false;
        off.kanCount = 4;
        off.kanByPlayer[0] = 2;
        off.kanByPlayer[3] = 2;
        check("规则关掉四杠散了 → 不流局", !off.fourKanAbortNow());
    }

    /**
     * 赤宝牌张数：默认 3 张；`rules.aka = 0` 时**整副牌山都不含赤五**。
     *
     * <p>审计出来的问题正是"注释与实现不一致"：`Rules.akaKinds()` 全仓无调用者，
     * 设 `aka = 0` 仍会发赤五、仍记赤宝牌番数（AGENTS §8 那句"0 或 3 张"对 0 不成立）。
     * 换掉赤五不能改变牌张构成，所以顺带钉住"每种牌恒 4 张"。
     */
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

    private static void roundScoringTests() {
        // 和了：庄家和了才连庄
        check("庄家自摸/荣和 → 连庄", RoundScoring.winBy(0, 0));
        check("闲家和了 → 轮庄", !RoundScoring.winBy(0, 2));
        check("多家和了含庄家 → 连庄",
                RoundScoring.winBy(1, Arrays.asList(3, 1)));
        check("多家和了不含庄家 → 轮庄",
                !RoundScoring.winBy(1, Arrays.asList(3, 2)));

        // 流局满贯：成立者含庄家才连庄
        check("流局满贯含庄家 → 连庄", RoundScoring.nagashiBy(0, Arrays.asList(0)));
        check("流局满贯不含庄家 → 轮庄", !RoundScoring.nagashiBy(0, Arrays.asList(1, 3)));

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
    }
    // ------------------------------------------------------------- 打点表

    private static int base(int han, int fu) {
        if (han >= 13) {
            return 8000;
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
        return Math.min(fu * (1 << (2 + han)), 2000);
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

    private static void scoreTableTests() {
        // 闲家荣和
        eq("闲1番30符荣", ron(1, 30, 1, 0), 1000);
        eq("闲1番40符荣", ron(1, 40, 1, 0), 1300);
        eq("闲1番50符荣", ron(1, 50, 1, 0), 1600);
        // 注：文档表格「闲1番60符=2900」与其自摸栏(500/1000)矛盾，按公式应为 1920→2000`n        eq("闲1番60符荣", ron(1, 60, 1, 0), 2000);
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
