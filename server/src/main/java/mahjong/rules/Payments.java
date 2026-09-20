package mahjong.rules;

import java.util.List;

/** 授受点数计算。 */
public final class Payments {

    private Payments() {
    }

    public static int ceil100(int v) {
        if (v <= 0) {
            return 0;
        }
        return ((v + 99) / 100) * 100;
    }

    /** 一次和牌的收支。 */
    public static final class Result {
        public final int[] delta = new int[4];
        /** 和牌者总收入（含本场棒与立直棒）。 */
        public int winnerGain;
        /** 和牌者从点数中获得的收入（不含立直棒）。 */
        public int winnerPoints;
        public int riichiTaken;
    }

    /**
     * 一条**包牌责任**（责任支付）：座位 {@code seat} 包了某一役满，承担基本点 {@code base}。
     *
     * <p>为什么是**列表**而不是单个座位：一手牌可以同时成立两个**各有责任者**的役满
     * （大三元由 A 的舍张鸣成、四杠子由 B 的舍张大明杠完成），这时两人各自承担自己
     * 造成的那一役（AUDIT S-52；口径与理由见 `docs/DESIGN.md`「包牌」）。
     * 同一个座位出现在多条里也是合法的（两张舍张各包一个役满），摊派时**按座位累加**。
     *
     * <p>列表长度是**有限**的：能被包的役满只有大三元 / 大四喜 / 四杠子三种，
     * 所以一手牌至多 3 条（实际上至多 2 条 —— 大三元与大四喜要 7 个面子，不可能共存）。
     */
    public static final class Pao {
        public final int seat;
        public final int base;

        public Pao(int seat, int base) {
            this.seat = seat;
            this.base = base;
        }

        @Override
        public String toString() {
            return "pao(" + seat + "," + base + ")";
        }
    }

    /** 没有包牌：传它比传 {@code null} 明确（`compute` 也容忍 {@code null}，当空表处理）。 */
    public static final List<Pao> NO_PAO = List.of();

    /**
     * 计算授受点数。
     *
     * @param score   和牌评价
     * @param winner  和牌者座位
     * @param loser   放铳者座位（自摸时传 -1）
     * @param dealer  庄家座位
     * @param honba   本场数
     * @param sticks  立直棒数量
     * @param tsumo   是否自摸
     * @param paos    包牌责任列表（空 = 没有包牌；传 {@link #NO_PAO}，也容忍 {@code null}）：
     *                每条 = (责任者, 该责任者承担的基本点)。空基本点的条目会被忽略，
     *                责任者是和牌者本人的条目也会被忽略（防御性）。
     */
    public static Result compute(Evaluator.HandScore score, int winner, int loser, int dealer,
                                 int honba, int sticks, boolean tsumo, List<Pao> paos) {
        Result res = new Result();
        final int totalBase = score.base;
        // 归并成"每个包牌者一共包了多少基本点"：同一家可以包两个役满（两张舍张各包一个），
        // 摊派时就是他那一份之和 —— 与"合成一条"必须**逐位相同**。
        // `left` 是分给包牌者之后剩下的基本点（≈ 手牌基本点 − Σ 被包役满），
        // 顺带做了夹取：正常构造下 Σ 被包役满 ≤ 手牌基本点，不为真时按登记顺序削到 0。
        final int[] paoOf = new int[4];
        int left = totalBase;
        if (paos != null) {
            for (Pao p : paos) {
                if (p == null || p.seat < 0 || p.seat == winner || p.base <= 0) {
                    continue;
                }
                final int take = Math.min(p.base, left);
                paoOf[p.seat] += take;
                left -= take;
            }
        }
        final int restBase = left;
        // 责任者的**出场顺序**（按列表，去重）：本场棒的余数给靠前那位（见下面的注释）
        final int[] payers = new int[4];
        int payerCount = 0;
        for (Pao p : (paos == null ? List.<Pao>of() : paos)) {
            if (p == null || p.seat < 0 || p.seat == winner || paoOf[p.seat] <= 0) {
                continue;
            }
            boolean seen = false;
            for (int i = 0; i < payerCount; i++) {
                if (payers[i] == p.seat) {
                    seen = true;
                    break;
                }
            }
            if (!seen) {
                payers[payerCount++] = p.seat;
            }
        }
        boolean winnerDealer = winner == dealer;

        if (tsumo) {
            // 每个非和牌者的基准份额（只含**未被包**的那部分基本点）
            for (int s = 0; s < 4; s++) {
                if (s == winner) {
                    continue;
                }
                res.delta[s] -= ceil100(tsumoMult(winnerDealer, s, dealer) * restBase);
            }
            // 被包的那一役：由责任者**全额**承担（三家份额都算在他头上），
            // 所以他还照付自己那份 restBase（下面这轮只加不减之前那轮）
            for (int i = 0; i < payerCount; i++) {
                int amount = 0;
                for (int s = 0; s < 4; s++) {
                    if (s == winner) {
                        continue;
                    }
                    amount += ceil100(tsumoMult(winnerDealer, s, dealer) * paoOf[payers[i]]);
                }
                res.delta[payers[i]] -= amount;
            }
        } else {
            int mult = winnerDealer ? 6 : 4;
            res.delta[loser] -= ceil100(mult * restBase);
            for (int i = 0; i < payerCount; i++) {
                int paoTotal = ceil100(mult * paoOf[payers[i]]);
                int half = (paoTotal / 2 / 100) * 100;
                if (payers[i] == loser) {
                    // 包牌者即放铳者：全额
                    res.delta[loser] -= paoTotal;
                } else {
                    res.delta[payers[i]] -= half;
                    res.delta[loser] -= paoTotal - half;
                }
            }
        }
        honba(res, winner, loser, honba, tsumo, payers, payerCount);

        int gain = 0;
        for (int s = 0; s < 4; s++) {
            if (s != winner) {
                gain -= res.delta[s];
            }
        }
        res.delta[winner] += gain;
        res.winnerPoints = gain;
        res.riichiTaken = 1000 * sticks;
        res.delta[winner] += res.riichiTaken;
        res.winnerGain = gain + res.riichiTaken;
        return res;
    }

    /** 自摸时该付款者的份额倍数：庄家 2、闲家 1；和牌者是庄家时三家都 2。 */
    private static int tsumoMult(boolean winnerDealer, int payer, int dealer) {
        if (winnerDealer) {
            return 2;
        }
        return payer == dealer ? 2 : 1;
    }

    /**
     * 本场棒。
     *
     * <p>`docs/日本麻将.md` §包牌 L802：「触发包牌规则时，**包牌者需要支付所有的本场棒**
     * （而不是放铳者）」—— 常规是自摸三家各 100/本场（合计 300）、荣和放铳者 300/本场，
     * 包牌时这 300/本场**整体**改由包牌者出（和牌者的收入不变，只有"谁出"变了）。
     *
     * <p>多个包牌者时按 **100 点为单位**平摊，余数给**列表里靠前**的那位（先成立的责任者）——
     * 本场棒是 100 的整数倍，而 300 除以 2 不是 100 的倍数，所以必须定一个归属；
     * 这里与「余数归先成立的责任者」保持同一把尺子。
     */
    private static void honba(Result res, int winner, int loser, int honba, boolean tsumo,
                              int[] payers, int payerCount) {
        if (honba <= 0) {
            return;
        }
        final int honbaPer = 100 * honba;
        if (payerCount == 0) {
            if (tsumo) {
                for (int s = 0; s < 4; s++) {
                    if (s != winner) {
                        res.delta[s] -= honbaPer;
                    }
                }
            } else {
                res.delta[loser] -= 3 * honbaPer;
            }
            return;
        }
        int total = 3 * honbaPer;
        int each = (total / payerCount / 100) * 100;
        int rem = total - each * payerCount;          // 100 的整数倍
        for (int i = 0; i < payerCount; i++) {
            int share = each;
            if (rem >= 100) {
                share += 100;
                rem -= 100;
            }
            res.delta[payers[i]] -= share;
        }
    }

    /**
     * 不听罚符。
     *
     * <p>{@code total} 是**本次转移的总量**：听牌者一共收 {@code total}，不听者一共付
     * {@code total}（n 家听牌 → 每家收 total/n；4−n 家不听 → 分摊 total）。
     *
     * <p>⚠ 收付两边各自向下取整到 100 会让点数**凭空生灭**：total=1000、n=3 时
     * 收方每家 300（共 900），付方那一家却要付 1000 —— 100 点不见了；n=1 时反过来
     * 凭空多出点数（AUDIT F11，而 `rules.noten_penalty` 是建房间时客户端传的，
     * 自检只跑过默认的 3000，所以一直没露头）。
     *
     * <p>所以改成**先定"每家收多少"，再让付方严格凑出同一个总量**：付方按 100 一点
     * 均匀分摊，余数（100 的整数倍）分给靠前的几家 —— 收方总和恒等于付方总和。
     * 默认 3000 时结果与旧实现**逐位相同**（3 家收 1000 / 1 家付 3000 等）。
     */
    public static int[] notenPenalty(boolean[] tenpai, int total) {
        int[] d = new int[4];
        int n = 0;
        for (boolean b : tenpai) {
            if (b) {
                n++;
            }
        }
        if (n == 0 || n == 4 || total <= 0) {
            return d;
        }
        final int receivers = n;
        final int payers = 4 - n;
        final int get = (total / receivers / 100) * 100;   // 收方每家（罚符以 100 为单位）
        if (get <= 0) {
            return d;                                       // 总量太小，取整后为 0：谁也不动
        }
        final int need = get * receivers;                   // 收方一共要收这么多
        int base = (need / payers / 100) * 100;             // 付方各自的基础额
        int rest = need - base * payers;                    // 余数：按 100 一份分给靠前的付方
        for (int s = 0; s < 4; s++) {
            if (tenpai[s]) {
                d[s] = get;
            } else {
                final int extra = Math.min(rest, 100);
                d[s] = -(base + extra);
                rest -= extra;
            }
        }
        return d;
    }
}
