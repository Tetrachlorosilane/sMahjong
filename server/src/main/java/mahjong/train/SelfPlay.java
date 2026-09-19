package mahjong.train;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import mahjong.ai.Policies;
import mahjong.ai.PolicyFactory;
import mahjong.core.Rules;
import mahjong.game.Round;
import mahjong.game.Table;
import mahjong.util.Json;
import mahjong.util.Log;

/**
 * 无网络自对弈 runner：训练数据的来源，同时是**评测场（arena）**。
 *
 * <p>三条硬性质（"结果可信"的前提）：
 * <ol>
 *   <li><b>同种子逐事件可复现</b>：每场种子由 {@code seedBase + 场号} **预先**算出（不是"跑到哪算哪"），
 *       每个座位的策略按 {@code (座位, 种子)} 新建实例，桌面固定
 *       {@code debugDeterministicSeed} + {@code botDelay=0/roundDelay=0}。
 *       与并行度无关：{@code --workers} 从 1 改成 8，同一场结果逐字节不变。</li>
 *   <li><b>座位轮转</b>（{@link Config#rotatePolicies}）：同一批牌山下让每个策略把四个座位都坐一遍，
 *       消掉"谁坐庄多、谁牌好"的运气 —— 配对比较才有意义。</li>
 *   <li><b>奖励事后回填</b>：策略行先落内存，小局结束时补 {@code hand_*}，整场结束时补 {@code placement}
 *       （见 {@link TraceRecorder}）。</li>
 * </ol>
 *
 * <p>吞吐实测（4 机器人、单核、{@code botDelay=0}）：**约 2 秒一场半庄**（约 13 小局 / 680 次决策）
 * → 单核约 340–450 决策/秒；8 核一天约数十万场量级。
 */
public final class SelfPlay {

    private SelfPlay() {
    }

    /** 自对弈配置。 */
    public static final class Config {
        /** 跑多少场半庄。 */
        public int games = 1;
        /** 基准种子；第 g 场的种子见 {@link SelfPlay#seedFor}。 */
        public long seedBase = 20260101L;
        /** 并行线程数（默认 = CPU 核数）。 */
        public int workers = Runtime.getRuntime().availableProcessors();
        /** 四家策略名（teacher / first / pass / random）。 */
        public String[] seatPolicy = {"teacher", "teacher", "teacher", "teacher"};
        /** 按局轮转座位（评测务必打开；纯采数据可关）。 */
        public boolean rotatePolicies;
        /** 规则预设（null = {@link Rules#defaults()} 的默认预设）。 */
        public String preset;
        /** 轨迹输出目录（null = 只统计、不落盘）。 */
        public String outDir;
        /** 每 k 次决策记录 1 条（省磁盘；1 = 全记）。 */
        public int sampleEvery = 1;
        /** 是否记录鸣牌决策。 */
        public boolean recordClaims = true;
        /** 每场最多打几个小局（0 = 打完整场；冒烟测试用）。 */
        public int maxHands;
    }

    /** 单个策略的统计（按策略标签聚合，跨座位）。 */
    public static final class PolicyStat {
        /** 参与的小局·座位数（= 该策略"打过的每一手"，比率的分母）。 */
        public int seatHands;
        /** 场数（平均顺位的分母）。 */
        public int games;
        public long placeSum;
        public int wins;
        public int dealIns;
        public long deltaSum;
        public long winScoreSum;

        public double avgPlace() {
            return games == 0 ? 0 : (double) placeSum / games;
        }

        public double winRate() {
            return seatHands == 0 ? 0 : (double) wins / seatHands;
        }

        public double dealInRate() {
            return seatHands == 0 ? 0 : (double) dealIns / seatHands;
        }

        public double avgDelta() {
            return seatHands == 0 ? 0 : (double) deltaSum / seatHands;
        }

        public double avgWinScore() {
            return wins == 0 ? 0 : (double) winScoreSum / wins;
        }
    }

    /** 汇总。 */
    public static final class Summary {
        public int games;
        public int hands;
        public int decisions;
        public int ryukyokuHands;
        public double seconds;
        public long seedBase;
        public int workers;
        public final Map<String, PolicyStat> byPolicy = new LinkedHashMap<>();
        /** 每场一行（含种子与四家顺位）—— 配对显著性检验要它。 */
        public final List<Map<String, Object>> perGame = new ArrayList<>();

        public double gamesPerSecond() {
            return seconds <= 0 ? 0 : games / seconds;
        }

        public double decisionsPerSecond() {
            return seconds <= 0 ? 0 : decisions / seconds;
        }

        public double ryukyokuRate() {
            return hands == 0 ? 0 : (double) ryukyokuHands / hands;
        }

        public double handsPerGame() {
            return games == 0 ? 0 : (double) hands / games;
        }
    }

    /** 一场的结果（worker 写、主线程汇总）。 */
    private static final class GameRow {
        int decisions;
        int ryukyoku;
        int[] finalScores;
        List<Object> placement;
        String[] labels;
        List<Map<String, Object>> hands;
    }

    /** 单场种子：只与 (seedBase, 场号) 有关，**与并行顺序无关**。 */
    public static long seedFor(long seedBase, int game) {
        long x = seedBase + game * 0x9E3779B97F4A7C15L;
        x = (x ^ (x >>> 30)) * 0xBF58476D1CE4E5B9L;
        x = (x ^ (x >>> 27)) * 0x94D049BB133111EBL;
        return x ^ (x >>> 31);
    }

    /**
     * 顺位（1 = 最高分）。
     *
     * <p>同点时按**座次先后**（M.League 的起家优先）拆开，保证顺位恒为 1..4 的一个排列 ——
     * 平均顺位这类指标只有在"每人恰好一个名次"时才有意义（否则同点会挤掉一个名次）。
     */
    public static List<Object> placementOf(int[] scores) {
        List<Integer> idx = new ArrayList<>(List.of(0, 1, 2, 3));
        idx.sort((a, b) -> scores[a] != scores[b] ? Integer.compare(scores[b], scores[a])
                                                  : Integer.compare(a, b));
        int[] place = new int[4];
        for (int rank = 0; rank < 4; rank++) {
            place[idx.get(rank)] = rank + 1;
        }
        return Json.intList(place);
    }

    public static Summary run(Config c) {
        final int games = Math.max(0, c.games);
        final int workers = Math.max(1, Math.min(c.workers, Math.max(1, games)));
        final PolicyFactory[] factories = new PolicyFactory[4];
        for (int i = 0; i < 4; i++) {
            factories[i] = Policies.byName(c.seatPolicy[i % c.seatPolicy.length]);
        }
        Path dir = openDir(c.outDir);
        final Path outDir = dir;

        final GameRow[] rows = new GameRow[Math.max(1, games)];
        final AtomicInteger next = new AtomicInteger();
        final long t0 = System.nanoTime();
        Thread[] pool = new Thread[workers];
        for (int w = 0; w < workers; w++) {
            pool[w] = new Thread(() -> {
                int g;
                while ((g = next.getAndIncrement()) < games) {
                    rows[g] = oneGame(c, factories, g, outDir);
                }
            }, "selfplay-" + w);
            pool[w].setDaemon(true);
        }
        for (Thread t : pool) {
            t.start();
        }
        for (Thread t : pool) {
            try {
                t.join();
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }
        final double sec = (System.nanoTime() - t0) / 1e9;

        Summary s = new Summary();
        s.games = games;
        s.seconds = sec;
        s.seedBase = c.seedBase;
        s.workers = workers;
        for (String name : c.seatPolicy) {
            s.byPolicy.computeIfAbsent(name, k -> new PolicyStat());
        }
        for (int g = 0; g < games; g++) {
            GameRow row = rows[g];
            if (row == null) {
                continue;
            }
            s.decisions += row.decisions;
            s.ryukyokuHands += row.ryukyoku;
            s.hands += row.hands.size();
            s.perGame.add(Json.obj(
                    "game", g,
                    "seed", seedFor(c.seedBase, g),
                    "policies", List.of(row.labels),
                    "final_scores", Json.intList(row.finalScores),
                    "placement", row.placement,
                    "hands", row.hands.size(),
                    "ryukyoku", row.ryukyoku));
            // 顺位按策略标签累计
            for (int i = 0; i < 4; i++) {
                PolicyStat st = s.byPolicy.get(row.labels[i]);
                st.games++;
                st.placeSum += ((Number) row.placement.get(i)).intValue();
            }
            // 逐手把"和了 / 放铳 / 收支"记到对应策略上
            for (Map<String, Object> h : row.hands) {
                int winner = Json.i(h, "winner", -1);
                int loser = Json.i(h, "loser", -1);
                List<Object> delta = Json.list(h, "delta");
                for (int i = 0; i < 4; i++) {
                    PolicyStat st = s.byPolicy.get(row.labels[i]);
                    st.seatHands++;
                    int d = delta == null || i >= delta.size() ? 0 : ((Number) delta.get(i)).intValue();
                    st.deltaSum += d;
                    if (winner == i) {
                        st.wins++;
                        st.winScoreSum += d;
                    }
                    if (loser == i) {
                        st.dealIns++;
                    }
                }
            }
        }
        return s;
    }

    private static Path openDir(String outDir) {
        if (outDir == null || outDir.isEmpty()) {
            return null;
        }
        Path dir = Paths.get(outDir);
        try {
            Files.createDirectories(dir);
            return dir;
        } catch (IOException e) {
            Log.warn("轨迹目录创建失败，改为不落盘：" + e);
            return null;
        }
    }

    private static GameRow oneGame(Config c, PolicyFactory[] factories, int g, Path outDir) {
        final long seed = seedFor(c.seedBase, g);
        final Rules rules = Rules.defaults();
        if (c.preset != null && !c.preset.isEmpty()) {
            rules.applyPreset(c.preset);
        }
        Table t = new Table("SP" + g, "selfplay", rules);
        t.botDelayMs = 0;
        t.roundDelayMs = 0;
        t.debugDeterministicSeed = true;
        t.debugMaxHands = c.maxHands;
        t.seedBase = seed;
        final String[] labels = new String[4];
        for (int i = 0; i < 4; i++) {
            final int src = (i + (c.rotatePolicies ? g : 0)) % 4;
            labels[i] = c.seatPolicy[src % c.seatPolicy.length];
            t.policy[i] = factories[src].create(i, seed);
            t.addBot(i);
        }
        TraceRecorder rec = new TraceRecorder(g, seed, outDir, labels, rules.startScore,
                c.sampleEvery, c.recordClaims, outDir != null);
        t.debugChoiceTap = rec::onChoice;
        t.debugEventTap = (recipient, ev) -> rec.onEvent(recipient, ev, t);
        t.playGame();
        rec.finish(t);

        GameRow row = new GameRow();
        row.labels = labels;
        row.hands = rec.handRows();
        row.decisions = rec.decisionCount();
        for (Map<String, Object> h : row.hands) {
            if (!Json.bool(h, "agari", false)) {
                row.ryukyoku++;
            }
        }
        row.finalScores = new int[4];
        for (int i = 0; i < 4; i++) {
            row.finalScores[i] = t.seat(i).score;
        }
        row.placement = TraceRecorder.placementOf(row.finalScores);
        return row;
    }

    /** 人类可读汇总（CLI）。 */
    public static String format(Summary s) {
        StringBuilder sb = new StringBuilder();
        sb.append(String.format(
                "== 自对弈汇总：%d 场 / %d 小局 / %.0f 决策/场，用时 %.1fs"
                        + "（%.2f 场/秒，%.0f 决策/秒），workers=%d%n",
                s.games, s.hands, s.games == 0 ? 0 : (double) s.decisions / s.games, s.seconds,
                s.gamesPerSecond(), s.decisionsPerSecond(), s.workers));
        sb.append(String.format("   流局率 %.1f%%   平均每场 %.1f 小局%n",
                100 * s.ryukyokuRate(), s.handsPerGame()));
        sb.append("---------------------------------------------------------------------------\n");
        sb.append(String.format("%-10s %6s %10s %9s %9s %10s %10s%n",
                "策略", "场数", "平均顺位", "和了率", "放铳率", "平均收支", "平均打点"));
        for (Map.Entry<String, PolicyStat> e : s.byPolicy.entrySet()) {
            PolicyStat st = e.getValue();
            sb.append(String.format("%-10s %6d %10.3f %8.1f%% %8.1f%% %10.0f %10.0f%n",
                    e.getKey(), st.games, st.avgPlace(), 100 * st.winRate(),
                    100 * st.dealInRate(), st.avgDelta(), st.avgWinScore()));
        }
        return sb.toString();
    }

    /** 汇总 → JSON（写 {@code summary.json}）。 */
    public static Map<String, Object> toJson(Summary s) {
        Map<String, Object> out = Json.obj(
                "games", s.games,
                "hands", s.hands,
                "decisions", s.decisions,
                "seconds", s.seconds,
                "seed_base", s.seedBase,
                "workers", s.workers,
                "hands_per_game", s.handsPerGame(),
                "ryukyoku_rate", s.ryukyokuRate(),
                "games_per_second", s.gamesPerSecond(),
                "decisions_per_second", s.decisionsPerSecond());
        Map<String, Object> byPolicy = Json.obj();
        for (Map.Entry<String, PolicyStat> e : s.byPolicy.entrySet()) {
            PolicyStat st = e.getValue();
            byPolicy.put(e.getKey(), Json.obj(
                    "games", st.games,
                    "seat_hands", st.seatHands,
                    "avg_place", st.avgPlace(),
                    "win_rate", st.winRate(),
                    "deal_in_rate", st.dealInRate(),
                    "avg_delta", st.avgDelta(),
                    "avg_win_score", st.avgWinScore()));
        }
        out.put("by_policy", byPolicy);
        out.put("per_game", new ArrayList<Object>(s.perGame));
        return out;
    }

    /** 写 {@code summary.json}（带 outDir 时）。 */
    public static void writeSummary(Summary s, String outDir) {
        if (outDir == null || outDir.isEmpty()) {
            return;
        }
        try {
            Files.createDirectories(Paths.get(outDir));
            Files.writeString(Paths.get(outDir, "summary.json"), Json.write(toJson(s)),
                    StandardCharsets.UTF_8);
        } catch (IOException e) {
            Log.warn("summary.json 写入失败：" + e);
        }
    }
}
