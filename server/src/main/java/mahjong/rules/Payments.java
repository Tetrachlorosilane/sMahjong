package mahjong.rules;

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
     * 计算授受点数。
     *
     * @param score   和牌评价
     * @param winner  和牌者座位
     * @param loser   放铳者座位（自摸时传 -1）
     * @param dealer  庄家座位
     * @param honba   本场数
     * @param sticks  立直棒数量
     * @param tsumo   是否自摸
     * @param paoSeat 包牌者座位（-1 表示无）
     * @param paoBase 被包牌役满部分的基本点（0 表示全部由 paoSeat 承担外的常规处理）
     */
    public static Result compute(Evaluator.HandScore score, int winner, int loser, int dealer,
                                 int honba, int sticks, boolean tsumo, int paoSeat, int paoBase) {
        Result res = new Result();
        int totalBase = score.base;
        if (paoSeat >= 0) {
            paoBase = Math.min(paoBase, totalBase);
        } else {
            paoBase = 0;
        }
        int restBase = totalBase - paoBase;
        boolean winnerDealer = winner == dealer;

        if (tsumo) {
            // 每个非和牌者的基准份额
            for (int s = 0; s < 4; s++) {
                if (s == winner) {
                    continue;
                }
                boolean isDealerPayer = s == dealer;
                int mult = isDealerPayer ? 2 : 1;
                if (winnerDealer) {
                    mult = 2;
                }
                res.delta[s] -= ceil100(mult * restBase);
            }
            if (paoSeat >= 0 && paoBase > 0) {
                int paoAmount = 0;
                for (int s = 0; s < 4; s++) {
                    if (s == winner) {
                        continue;
                    }
                    int mult = winnerDealer ? 2 : (s == dealer ? 2 : 1);
                    paoAmount += ceil100(mult * paoBase);
                }
                res.delta[paoSeat] -= paoAmount;
            }
            // 本场棒
            int honbaPer = 100 * honba;
            for (int s = 0; s < 4; s++) {
                if (s != winner) {
                    res.delta[s] -= honbaPer;
                }
            }
            if (paoSeat >= 0 && honba > 0) {
                // 包牌者承担全部本场棒：把别人付的还给别人
                for (int s = 0; s < 4; s++) {
                    if (s != winner && s != paoSeat) {
                        res.delta[s] += honbaPer;
                        res.delta[paoSeat] -= honbaPer;
                    }
                }
            }
        } else {
            int mult = winnerDealer ? 6 : 4;
            int rest = ceil100(mult * restBase);
            res.delta[loser] -= rest;
            if (paoSeat >= 0 && paoBase > 0) {
                int paoTotal = ceil100(mult * paoBase);
                int half = (paoTotal / 2 / 100) * 100;
                if (paoSeat == loser) {
                    // 包牌者即放铳者：全额
                    res.delta[loser] -= paoTotal;
                } else {
                    res.delta[paoSeat] -= half;
                    res.delta[loser] -= paoTotal - half;
                }
            }
            int honbaPay = 300 * honba;
            int honbaPayer = (paoSeat >= 0) ? paoSeat : loser;
            res.delta[honbaPayer] -= honbaPay;
        }

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
