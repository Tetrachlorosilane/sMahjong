package mahjong.rules;

import mahjong.core.Tiles;

/**
 * 危险度：某张牌打出去对某一家有多危险 —— **启发式**，不是规则判定。
 *
 * <p>麻将 AI 最欠的一块就是押し引き（push/fold），而规则引擎答不了它："
 * 「这张牌会不会放铳」不是规则，是估计。但估计必须有**可解释的依据**，
 * 否则训练侧拿它当特征、评测拿它解释"这手为什么打得差"都无从谈起。
 *
 * <p>判据（都在同一份「可见牌」上算，见 {@link Visible}）：
 * <table border="1">
 *   <caption>危险度分级</caption>
 *   <tr><th>级别</th><th>条件</th><th>道理</th></tr>
 *   <tr><td>{@link #SAFE}</td><td><b>现物</b>：该家牌河里已有这个牌种</td>
 *       <td>他若听这张就自振听，荣和不可能成立 —— 这是**唯一**的硬保证</td></tr>
 *   <tr><td>{@link #RELATIVELY_SAFE}</td><td><b>筋</b>（同花色 ±3 已在他牌河里）
 *       或<b>壁</b>（该牌种已见 ≥3 张）</td>
 *       <td>两面/双碰的搭子被拆掉了大半，但**不是保证**（嵌张/单骑/国士仍在）</td></tr>
 *   <tr><td>{@link #SUSPICIOUS}</td><td>以上皆非，且没有立直迹象</td><td>无信息</td></tr>
 *   <tr><td>{@link #DANGEROUS}</td><td>以上皆非，而该家**立直**了</td>
 *       <td>立直家的手牌固定且已听牌，中张无筋牌是最危险的</td></tr>
 * </table>
 *
 * <p>⛔ 别把这套东西用去判"能不能打"：它只影响**取舍**（打哪张、要不要弃和），
 * 与 `Round` 里的合法性判定（振听禁打、食替、立直后摸切）毫无关系。
 */
public final class Danger {

    /** 现物：对他 100% 安全（振听规则保证）。 */
    public static final int SAFE = 0;
    /** 筋 / 壁：相对安全，但不是保证。 */
    public static final int RELATIVELY_SAFE = 1;
    /** 无信息。 */
    public static final int SUSPICIOUS = 2;
    /** 立直家的无筋中张。 */
    public static final int DANGEROUS = 3;

    public static final String CODE_GENBUTSU = "genbutsu";
    public static final String CODE_SUJI = "suji";
    public static final String CODE_SUJI_WALL = "suji_wall";
    public static final String CODE_WALL = "wall";
    public static final String CODE_RIICHI = "riichi";
    public static final String CODE_UNKNOWN = "unknown";

    /** 各级别的基准分（0..100 的连续量，给"取最安全的那张"排序用）。 */
    private static final int[] BASE = {0, 12, 30, 65};

    private Danger() {
    }

    /** 一次危险度判断的结果（级别 + 分数 + 首要理由）。 */
    public static final class Report {
        public final int level;
        /** 0..100：级别基准分 + 巡目深度（越到后面越危险）。 */
        public final int score;
        /** 首要理由码（ASCII，可直接当特征/日志）。 */
        public final String code;
        public final boolean genbutsu;
        public final boolean suji;
        public final boolean wall;

        Report(int level, int score, String code, boolean genbutsu, boolean suji, boolean wall) {
            this.level = level;
            this.score = score;
            this.code = code;
            this.genbutsu = genbutsu;
            this.suji = suji;
            this.wall = wall;
        }

        @Override
        public String toString() {
            return code + "(" + score + ")";
        }
    }

    /**
     * 对**一家**打 {@code kind} 的危险度。
     *
     * @param visible      {@link Visible#counts}（可见牌计数）
     * @param theirRivers  该家牌河的牌种计数（长度 34；被鸣走的不算 —— 那些牌不在他面前）
     * @param theirRiichi  该家是否立直
     * @param turn         巡目（已打出总张数 / 4 之类），只影响分数不影响级别
     */
    public static Report of(int kind, int[] visible, int[] theirRivers, boolean theirRiichi,
                            int turn) {
        boolean genbutsu = theirRivers != null && kind >= 0 && kind < Tiles.KIND_COUNT
                && theirRivers[kind] > 0;
        if (genbutsu) {
            return new Report(SAFE, 0, CODE_GENBUTSU, true, false, false);
        }
        boolean suji = isSuji(kind, theirRivers);
        boolean wall = visible != null && kind >= 0 && kind < Tiles.KIND_COUNT
                && visible[kind] >= 3;
        int level;
        String code;
        if (suji && wall) {
            level = RELATIVELY_SAFE;
            code = CODE_SUJI_WALL;
        } else if (suji) {
            level = RELATIVELY_SAFE;
            code = CODE_SUJI;
        } else if (wall) {
            level = RELATIVELY_SAFE;
            code = CODE_WALL;
        } else if (theirRiichi) {
            level = DANGEROUS;
            code = CODE_RIICHI;
        } else {
            level = SUSPICIOUS;
            code = CODE_UNKNOWN;
        }
        final int t = Math.max(0, Math.min(turn, 18));
        return new Report(level, Math.min(100, BASE[level] + t), code, false, suji, wall);
    }

    /**
     * 筋：同花色 ±3 的牌已在他牌河里。
     *
     * <p>道理：两面搭子听的是 {@code {a, a+3}}（手里是 {@code a+1, a+2}）。他既然打过 {@code a+3}，
     * 就不可能正听 {@code a}（那样他打掉的正是自己的和了牌 → 自振听）。反过来同理。
     * ⚠ 只排除了**两面**，嵌张/边张/单骑/双碰都不受影响 —— 所以只是"相对安全"。
     * 字牌没有筋。
     */
    public static boolean isSuji(int kind, int[] theirRivers) {
        if (theirRivers == null || kind < 0 || kind >= 27) {
            return false;
        }
        final int suit = Tiles.suit(kind);
        final int num = Tiles.num(kind);
        for (int d : new int[]{-3, 3}) {
            final int n = num + d;
            if (n < 1 || n > 9) {
                continue;
            }
            final int partner = suit * 9 + (n - 1);
            if (theirRivers[partner] > 0) {
                return true;
            }
        }
        return false;
    }

    /**
     * 对**四家**取最危险的那一家（排除自己）。
     *
     * @param rivers  四家牌河的牌种计数
     * @param riichi  四家立直状态
     */
    public static Report worst(int kind, int[] visible, int[][] rivers, boolean[] riichi,
                               int turn, int selfSeat) {
        Report worst = null;
        for (int s = 0; s < 4; s++) {
            if (s == selfSeat) {
                continue;
            }
            Report rep = of(kind, visible, rivers == null ? null : rivers[s],
                    riichi != null && riichi[s], turn);
            if (worst == null || rep.score > worst.score) {
                worst = rep;
            }
        }
        return worst == null ? of(kind, visible, null, false, turn) : worst;
    }

    /** 四家牌河（牌 id 列表）→ 每家的牌种计数。 */
    public static int[][] riverCounts(java.util.List<Integer>[] discards) {
        int[][] out = new int[4][Tiles.KIND_COUNT];
        for (int s = 0; s < 4 && discards != null && s < discards.length; s++) {
            if (discards[s] == null) {
                continue;
            }
            for (int id : discards[s]) {
                out[s][Tiles.kind(id)]++;
            }
        }
        return out;
    }

    /**
     * 只对**立直家**取最危险 —— 弃和（ベタオリ）时该用这一支。
     *
     * <p>为什么不能对四家取最坏：没人立直的对手手里是什么样**无从判断**，把他们算进来
     * 会让每一张牌都"一样危险"、把立直家现物那份**真正有价值**的安全度淹掉
     * （实测：只有一家立直时，四家最坏会让现物和筋的分一样、弃和就选不出安全牌）。
     * 能立刻惩罚你的只有已经听牌、而且亮明了的那几家。
     *
     * <p>没有任何立直家时回退成 {@link #worst}（保守）。
     */
    public static Report worstAgainstRiichi(int kind, int[] visible, int[][] rivers,
                                           boolean[] riichi, int turn, int selfSeat) {
        Report worst = null;
        for (int s = 0; s < 4; s++) {
            if (s == selfSeat || riichi == null || !riichi[s]) {
                continue;
            }
            Report rep = of(kind, visible, rivers == null ? null : rivers[s], true, turn);
            if (worst == null || rep.score > worst.score) {
                worst = rep;
            }
        }
        return worst == null ? worst(kind, visible, rivers, riichi, turn, selfSeat) : worst;
    }
}
