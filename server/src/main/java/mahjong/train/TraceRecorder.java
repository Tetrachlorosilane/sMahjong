package mahjong.train;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import mahjong.ai.Action;
import mahjong.ai.Decision;
import mahjong.ai.ObsFeatures;
import mahjong.bot.Bot;
import mahjong.game.Round;
import mahjong.game.Table;
import mahjong.rules.Agari;
import mahjong.rules.Shanten;
import mahjong.util.Json;
import mahjong.util.Log;

/**
 * 单局轨迹记录器：一条决策一行、一行小局结算、一行整场结算（JSONL）。
 *
 * <p>设计约束：
 * <ul>
 *   <li><b>一局一个文件</b>（{@code <out>/g<序号>.jsonl}）—— 并行 worker 各写各的，
 *       零锁、零交错、可续跑；</li>
 *   <li><b>先缓冲、后回填</b>：决策发生时还不知道这一手乃至这一场的结果，所以策略行先存内存，
 *       小局结束时补 {@code hand_*}，整场结束时补 {@code placement/final_scores}
 *       —— 奖励是"事后才知道"的，这个顺序决定了标签能不能用；</li>
 *   <li><b>决策行带 {@code chosen_index}</b>（= 在本次 {@code legal} 里的下标）：直接就是
 *       "按询问枚举 + 掩码"的策略头要的监督信号；</li>
 *   <li>只要统计不要数据时（{@code keepDecisions=false}）不建决策行，只留小局结算行，
 *       内存与开销都接近零。</li>
 * </ul>
 */
final class TraceRecorder {

    private static final String[] WINDS = {"E", "S", "W", "N"};

    private final int gameIndex;
    private final long seed;
    private final Path dir;
    private final int sampleEvery;
    private final boolean recordClaims;
    private final boolean keepDecisions;
    private final String[] labels;
    private final int startScore;
    /** DAgger：对学生座位**额外**记一次"老师在同一 obs 上会怎么打"。 */
    private final boolean teacherLabel;
    /**
     * {@code --aux}：**额外**落一份标签侧文件 {@code g<n>.aux.npz}（`FEATURES-V4.md` §5.2）。
     *
     * <p>⚠ 它**不改轨迹**：同一颗种子 + 同一个策略串，开不开 `--aux` 产出的 `g*.jsonl`
     * 必须**逐字节相同**（自检里钉着这条）—— 标签只许进另一个文件。
     */
    private final boolean aux;

    /** 决策行（待回填）。 */
    private final List<Map<String, Object>> decisions = new ArrayList<>();
    /** 小局结算行（统计与奖励回填的来源）。 */
    private final List<Map<String, Object>> hands = new ArrayList<>();

    /**
     * 逐决策的**标签侧**行（与 {@link #decisions} **同序同长**）。
     *
     * <p>这就是"上帝视角"那一半：对手手牌/听牌、有没有放铳、顺位 —— 只在自对弈里存在，
     * 落到**另一个文件**里（推理路径永不读它，`FEATURES-V4.md` §5.2 的硬闸门）。
     */
    private final List<AuxRow> auxRows = new ArrayList<>();

    /** 一条标签侧记录（字段与 `FEATURES-V4.md` §5.2 的表一一对应）。 */
    private static final class AuxRow {
        int seat = -1;
        /** 这一手做完之后的向听（听牌记 0；与 sidecar 的逐候选同一把尺子）。 */
        int ownShantenAfter;
        /** 这一手做完之后是否听牌。 */
        int ownTenpai;
        /** 三家对手**当前**是否听牌（**隐藏真值**）。 */
        final int[] oppTenpai = new int[3];
        /** 三家对手的暗牌计数（**隐藏真值**，长度 `3 × 34`）。 */
        final int[] oppHand = new int[3 * 34];
        /** 本小局我有没有放铳给第 j 家（**事后回填**）。 */
        final int[] oppDealin = new int[3];
        /** 本小局我有没有和了（**事后回填**）。 */
        int winFlag;
        /** 本小局我的收支（**事后回填**）。 */
        int handDelta;
        /** 终局顺位 1..4（**整场结束才回填**）。 */
        int placement;
    }

    /** 记录器自己维护的分数账：{@code Round.scores} 在局内会被立直扣点改动，不能当"局前分"用。 */
    private final int[] runningScores = new int[4];

    private int handNo = -1;
    private String handKey = "";
    private int step;
    private int observed;
    /** 当前小局的决策行在 {@link #decisions} 里的起始下标（回填用）。 */
    private int handRowFrom;

    TraceRecorder(int gameIndex, long seed, Path dir, String[] labels, int startScore,
                  int sampleEvery, boolean recordClaims, boolean keepDecisions) {
        this(gameIndex, seed, dir, labels, startScore, sampleEvery, recordClaims, keepDecisions,
                false);
    }

    TraceRecorder(int gameIndex, long seed, Path dir, String[] labels, int startScore,
                  int sampleEvery, boolean recordClaims, boolean keepDecisions,
                  boolean teacherLabel) {
        this(gameIndex, seed, dir, labels, startScore, sampleEvery, recordClaims, keepDecisions,
                teacherLabel, false);
    }

    TraceRecorder(int gameIndex, long seed, Path dir, String[] labels, int startScore,
                  int sampleEvery, boolean recordClaims, boolean keepDecisions,
                  boolean teacherLabel, boolean aux) {
        this.gameIndex = gameIndex;
        this.seed = seed;
        this.dir = dir;
        this.labels = labels;
        this.startScore = startScore;
        this.sampleEvery = Math.max(1, sampleEvery);
        this.recordClaims = recordClaims;
        this.keepDecisions = keepDecisions;
        this.teacherLabel = teacherLabel;
        this.aux = aux;
        for (int i = 0; i < 4; i++) {
            runningScores[i] = startScore;
        }
    }

    /** 这个座位的策略本来就是 teacher / 内置机器人时不必再标一遍：它的 `chosen` 就是老师动作。 */
    private static boolean isTeacherPolicy(String label) {
        return label == null || label.equalsIgnoreCase("teacher") || label.equalsIgnoreCase("bot");
    }

    /** 小局结算行（统计用）。 */
    List<Map<String, Object>> handRows() {
        return hands;
    }

    /** 本场实际发生的决策次数（**不受采样影响**；统计吞吐要用它）。 */
    int decisionCount() {
        return observed;
    }

    // ------------------------------------------------------------------ 决策

    void onChoice(Decision d, Map<String, Object> cmd) {
        observed++;
        // 小局编号必须每次都推进（统计行也要用它），所以先 roll 再判是否记录
        rollHand(roundKey(d.round));
        if (!keepDecisions) {
            return;
        }
        if (sampleEvery > 1 && (observed % sampleEvery) != 0) {
            return;
        }
        if (!recordClaims && "claim".equals(d.kind)) {
            return;
        }
        // `resolve` 而不是 `fromCmd`：策略可以回**部分指定**的包（裸 pon / 不带 tiles 的大明杠），
        // 而服务端按"普通牌优先"的默认取法执行 —— 那正是本次 legal 里的第一条。
        // 记成裸键会与 legal（只含带取法的键）对不上（PROTOCOL §8.3 的裸 pon 规则）。
        final Action a = Action.resolve(cmd, d.obs.legal);
        if (a == null) {
            // 认不出的回包**不编造标签**（宁可少一条样本）
            return;
        }
        Map<String, Object> row = Json.obj(
                "type", "decision",
                "game", gameIndex,
                "hand_no", handNo,
                "hand", handKey,
                "step", step++,
                "seat", d.obs.seat,
                "policy", labels[d.obs.seat],
                "kind", d.kind,
                "legal", d.obs.legalKeys(),
                "chosen", a.key(),
                "chosen_index", d.obs.indexOf(a),
                "obs", d.obs.toJson());
        // DAgger（`--teacher-label`）：对**学生**座位再问一次老师"这一手你会怎么打"。
        // 座位本身就是 teacher 时不必问（`chosen` 已经是老师动作，见 isTeacherPolicy）。
        // ⚠ 这会多跑一次 `Bot.decide`：只在采集时开、且只对学生座位付出这个代价。
        if (teacherLabel && !isTeacherPolicy(labels[d.obs.seat])) {
            Action t = Action.resolve(
                    Bot.decide(d.round, d.obs.seat, d.kind, d.options, d.extra), d.obs.legal);
            if (t != null) {
                row.put("teacher", t.key());
                row.put("teacher_index", d.obs.indexOf(t));
            }
        }
        decisions.add(row);
        if (aux) {
            auxRows.add(auxRow(d, a));
        }
    }

    /**
     * 一条标签侧记录（`FEATURES-V4.md` §5.2）。
     *
     * <p>两半来源不同，别混：
     * <ul>
     *   <li><b>决策那一刻就能算的</b>：自家"这一手做完"的向听/听牌 —— 用 {@link ObsFeatures}
     *       对**这一条 obs + 这次动作**重算（自家手牌不是隐藏信息，所以这不算作弊），
     *       与 sidecar 的逐候选派生量**同一份引擎实现**；</li>
     *   <li><b>真·上帝视角</b>：三家对手的暗牌计数与听牌 —— 只能从内存里的 {@link Round} 读
     *       （`d.round`），这正是"标签与输入必须物理分离"的理由。</li>
     * </ul>
     */
    private AuxRow auxRow(Decision d, Action a) {
        AuxRow r = new AuxRow();
        r.seat = d.obs.seat;
        ObsFeatures.View v = ObsFeatures.ofObservation(d.obs);
        final String t = a.type == null ? "" : a.type;
        if ("discard".equals(t) || "riichi".equals(t) || "chi".equals(t) || "pon".equals(t)
                || "kan".equals(t)) {
            // 第 0 维就是"这一手做完（该打的也打完）"的向听；和了形给 -1，标签侧夹到 0
            int sh = ObsFeatures.perCandidate(v, a.key())[0];
            r.ownShantenAfter = Math.max(0, sh);
            r.ownTenpai = sh <= 0 ? 1 : 0;
        } else {
            // 终局动作 / 过（摸切之外的 pass）：手上没变 → 用当前向听
            int sh = Math.max(0, Shanten.min(v.hand, v.melds.size()));
            r.ownShantenAfter = sh;
            r.ownTenpai = sh <= 0 ? 1 : 0;
        }
        Round rr = d.round;
        if (rr != null) {
            for (int j = 0; j < 3; j++) {
                final int s = (r.seat + 1 + j) % 4;
                final int[] c = rr.concealCounts(s);
                for (int k = 0; k < 34; k++) {
                    r.oppHand[j * 34 + k] = c[k];
                }
                r.oppTenpai[j] = Agari.waits(c, rr.melds[s].size()).isEmpty() ? 0 : 1;
            }
        }
        return r;
    }

    // ------------------------------------------------------------------ 事件

    /**
     * 出站报文旁路（只认广播）。
     *
     * <p>小局结算必须从 {@code round_end} 拿：那时 {@code Table.lastResult} 已经就绪，
     * 而报文里的 {@code scores} 是**本局结算后**的分数。
     */
    void onEvent(int recipient, Map<String, Object> ev, Table t) {
        if (recipient != -1 || !"round_end".equals(Json.str(ev, "ev", ""))) {
            return;
        }
        rollHand(roundKey(ev));
        List<Object> after = Json.list(ev, "scores");
        int[] now = new int[4];
        for (int i = 0; i < 4 && after != null && i < after.size(); i++) {
            now[i] = ((Number) after.get(i)).intValue();
        }
        int[] delta = new int[4];
        for (int i = 0; i < 4; i++) {
            delta[i] = now[i] - runningScores[i];
            runningScores[i] = now[i];
        }
        Round.Result res = t.lastResult;
        Map<String, Object> row = Json.obj(
                "type", "hand",
                "game", gameIndex,
                "hand_no", handNo,
                "hand", handKey,
                "round", ev.get("round"),
                "scores_after", Json.intList(now),
                "delta", Json.intList(delta),
                "agari", ev.get("agari"),
                "abortive", ev.get("abortive"),
                "reason", Json.str(ev, "reason", ""),
                "renchan", ev.get("renchan"),
                "winner", res == null ? -1 : res.winner,
                "loser", res == null ? -1 : res.loser,
                "tsumo", res != null && res.tsumo,
                "nagashi", res != null && res.nagashi,
                "tenpai", res == null ? List.of() : Json.boolList(res.tenpai));
        hands.add(row);
        // 回填本小局的决策行（奖励事后才知道，所以只能在这一刻补）
        final int winner = res == null ? -1 : res.winner;
        final int loser = res == null ? -1 : res.loser;
        for (int i = handRowFrom; i < decisions.size(); i++) {
            Map<String, Object> dr = decisions.get(i);
            dr.put("hand_delta", Json.intList(delta));
            dr.put("hand_winner", row.get("winner"));
            dr.put("hand_loser", row.get("loser"));
            dr.put("hand_agari", row.get("agari"));
        }
        // 标签侧同样回填（放铳 / 和了 / 收支）
        for (int i = handRowFrom; i < auxRows.size() && i < decisions.size(); i++) {
            AuxRow ar = auxRows.get(i);
            for (int j = 0; j < 3; j++) {
                final int opp = (ar.seat + 1 + j) % 4;
                ar.oppDealin[j] = (winner == opp && loser == ar.seat) ? 1 : 0;
            }
            ar.winFlag = winner == ar.seat ? 1 : 0;
            ar.handDelta = ar.seat >= 0 && ar.seat < 4 ? delta[ar.seat] : 0;
        }
    }

    // ------------------------------------------------------------------ 收尾

    /** 整场结束：回填顺位与逐决策 reward-to-go。必须在 {@code Table.playGame()} 返回之后调用。 */
    void finish(Table t) {
        int[] finalScores = new int[4];
        for (int i = 0; i < 4; i++) {
            finalScores[i] = t.seat(i).score;
        }
        List<Object> placement = placementOf(finalScores);
        final int[][] rtg = rewardToGo(finalScores);
        for (Map<String, Object> row : decisions) {
            // 逐决策回报（点）：λ=1 的 GAE 目标 = 从这个局面起的还行收入之和。
            // 顺序必须与 C++ 侧一致（`trainer/src/trace.cpp`）—— 同种子两份轨迹要逐字节相同。
            final int hn = ((Number) row.get("hand_no")).intValue();
            final int seat = ((Number) row.get("seat")).intValue();
            row.put("reward_to_go",
                    hn >= 0 && hn < rtg.length && seat >= 0 && seat < 4 ? rtg[hn][seat] : 0);
            row.put("final_scores", Json.intList(finalScores));
            row.put("placement", placement);
        }
        for (AuxRow ar : auxRows) {
            ar.placement = ar.seat >= 0 && ar.seat < 4
                    ? ((Number) placement.get(ar.seat)).intValue() : 0;
        }
        if (dir == null) {
            return;
        }
        if (aux) {
            writeAux();
        }
        List<String> lines = new ArrayList<>(decisions.size() + hands.size() + 1);
        for (Map<String, Object> row : decisions) {
            lines.add(Json.write(row));
        }
        for (Map<String, Object> row : hands) {
            lines.add(Json.write(row));
        }
        lines.add(Json.write(Json.obj(
                "type", "game",
                "game", gameIndex,
                "seed", seed,
                "policies", List.of(labels),
                "start_score", startScore,
                "hands", hands.size(),
                "decisions", decisions.size(),
                "sampled_every", sampleEvery,
                "final_scores", Json.intList(finalScores),
                "placement", placement)));
        try {
            Files.write(dir.resolve("g" + gameIndex + ".jsonl"), lines, StandardCharsets.UTF_8);
        } catch (IOException e) {
            Log.warn("轨迹写入失败 " + dir + "：" + e);
        }
    }

    // ------------------------------------------------------------------ 工具

    /**
     * 逐决策 **reward-to-go**（点）：`R(h, s) = Σ_{h' ≥ h} delta[h'][s] + 终局余棒[s]`。
     *
     * <p>为什么要它（`docs/TRAINING-V4.md` §「第六轮」）：离线 PPO 原来只能拿**整场**结果
     * （{@code final_scores − 起点}）当回报，那个量对所有局面都一样 ⇒ 优势
     * {@code A = R − E[V(s)]} 里没有"这一手之后发生了什么"的信息，信用分配粗到整场
     * （实测 2,000 场 2+2 配对 Δ=−3.54 顺位点，见 §「第五轮」）。有了逐决策回报，
     * **λ=1 的 GAE 目标就现成可算**（{@code A = R_tg − V(s)}），不需要在线交互。
     *
     * <p>口径（三条，与 {@code trainer/src/trace.cpp} 逐字一致）：
     * <ul>
     *   <li>回报 = **本小局及其后**该家的收支之和 —— 决策的后果包含本小局（打出去就结算了）；</li>
     *   <li>外加**终局余棒**（末局结算后供託里那批立直棒按规则归末局第 1 位，`AGENTS.md` §6.4）：
     *       不加它的话第 0 小局的回报会比 {@code value} 少一个 0~3000 点的常数，
     *       于是值头（若也用 rtg 当目标）与旧口径对不上；</li>
     *   <li>所以有不变式：**第 0 小局的决策的 {@code reward_to_go} == {@code final_scores[seat] − 起点}**
     *       （点）—— 自检与 {@code tools/selfplay-check.mjs} 都钉着它。</li>
     * </ul>
     *
     * @return {@code rtg[hand_no][seat]}；没有小局结算行时返回空数组（那种轨迹本来就不该用）
     */
    private int[][] rewardToGo(int[] finalScores) {
        final int n = hands.size();
        int maxNo = -1;
        for (Map<String, Object> h : hands) {
            maxNo = Math.max(maxNo, ((Number) h.get("hand_no")).intValue());
        }
        final int[][] rtg = new int[maxNo + 1][4];
        int[] bonus = new int[4];
        if (n > 0) {
            List<Object> lastAfter = Json.list(hands.get(n - 1), "scores_after");
            for (int s = 0; s < 4 && lastAfter != null && s < lastAfter.size(); s++) {
                bonus[s] = finalScores[s] - ((Number) lastAfter.get(s)).intValue();
            }
        }
        int[] acc = new int[4];
        for (int h = n - 1; h >= 0; h--) {
            List<Object> d = Json.list(hands.get(h), "delta");
            final int hn = ((Number) hands.get(h).get("hand_no")).intValue();
            for (int s = 0; s < 4; s++) {
                final int add = d != null && s < d.size() ? ((Number) d.get(s)).intValue() : 0;
                acc[s] += add;
                rtg[hn][s] = acc[s] + bonus[s];
            }
        }
        return rtg;
    }

    /**
     * 写标签侧文件 `g<n>.aux.npz`（`FEATURES-V4.md` §5.2；**训练专用，推理路径永不读它**）。
     *
     * <p>形状与 trace 的决策行**一一对应**（同序同长）：`(n,)` / `(n,3)` / `(n,3,34)`。
     * `meta` 里带版本与溯源（obs/aux 版本、种子、场号、策略串）—— 版本不符时读侧直接报错。
     */
    private void writeAux() {
        final int n = auxRows.size();
        final byte[] ownShanten = new byte[n];
        final byte[] ownTenpai = new byte[n];
        final byte[] oppTenpai = new byte[n * 3];
        final byte[] oppHand = new byte[n * 3 * 34];
        final byte[] oppDealin = new byte[n * 3];
        final byte[] winFlag = new byte[n];
        final int[] handDelta = new int[n];
        final byte[] placement = new byte[n];
        for (int i = 0; i < n; i++) {
            final AuxRow r = auxRows.get(i);
            ownShanten[i] = (byte) r.ownShantenAfter;
            ownTenpai[i] = (byte) r.ownTenpai;
            winFlag[i] = (byte) r.winFlag;
            handDelta[i] = r.handDelta;
            placement[i] = (byte) r.placement;
            for (int j = 0; j < 3; j++) {
                oppTenpai[i * 3 + j] = (byte) r.oppTenpai[j];
                oppDealin[i * 3 + j] = (byte) r.oppDealin[j];
                for (int k = 0; k < 34; k++) {
                    oppHand[(i * 3 + j) * 34 + k] = (byte) r.oppHand[j * 34 + k];
                }
            }
        }
        NpzWriter npz = new NpzWriter();
        npz.putI8("own_shanten_after", ownShanten, n);
        npz.putU8("own_tenpai", ownTenpai, n);
        npz.putU8("opp_tenpai", oppTenpai, n, 3);
        npz.putU8("opp_hand", oppHand, n, 3, 34);
        npz.putU8("opp_dealin", oppDealin, n, 3);
        npz.putU8("win_flag", winFlag, n);
        npz.putI32("hand_delta", handDelta, n);
        npz.putU8("placement", placement, n);
        npz.putJson("meta", Json.write(Json.obj(
                "aux_version", AUX_VERSION,
                "obs_version", mahjong.ai.Observation.VERSION,
                "game", gameIndex,
                "seed", seed,
                "n", n,
                "policies", List.of(labels),
                "note", "labels only; inference must read g*.feat.bin, never this file")));
        try {
            Files.write(dir.resolve("g" + gameIndex + ".aux.npz"), npz.toBytes());
        } catch (IOException e) {
            Log.warn("标签侧写入失败 " + dir + "：" + e);
        }
    }

    /** 标签侧文件格式版本（字段增删要 +1；Python 侧 `mahjong_ml.aux.AUX_VERSION` 同步）。 */
    static final int AUX_VERSION = 1;

    /** 小局键（与 {@link #roundKey(Map)} 必须同格式，否则连庄会被当成换局）。 */
    private static String roundKey(Round r) {
        return r.roundWind + "-" + r.kyoku + "-" + r.honba;
    }

    /** 小局键（从 `round_end` 报文里取）。 */
    private static String roundKey(Map<String, Object> ev) {
        Map<String, Object> round = Json.map(ev, "round");
        String bakaze = Json.str(round, "bakaze", "E");
        int wind = 0;
        for (int i = 0; i < WINDS.length; i++) {
            if (WINDS[i].equals(bakaze)) {
                wind = i;
            }
        }
        return wind + "-" + Json.i(round, "kyoku", 1) + "-" + Json.i(round, "honba", 0);
    }

    private void rollHand(String key) {
        if (!key.equals(handKey)) {
            handKey = key;
            handNo++;
            handRowFrom = decisions.size();
        }
    }

    /**
     * 顺位（1 = 最高分）—— 实现在 {@link SelfPlay#placementOf}（自测要直接验它，
     * 而本类是包内可见的）。
     */
    static List<Object> placementOf(int[] scores) {
        return SelfPlay.placementOf(scores);
    }
}
