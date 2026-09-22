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
import mahjong.game.Round;
import mahjong.game.Table;
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

    /** 决策行（待回填）。 */
    private final List<Map<String, Object>> decisions = new ArrayList<>();
    /** 小局结算行（统计与奖励回填的来源）。 */
    private final List<Map<String, Object>> hands = new ArrayList<>();

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
        this.gameIndex = gameIndex;
        this.seed = seed;
        this.dir = dir;
        this.labels = labels;
        this.startScore = startScore;
        this.sampleEvery = Math.max(1, sampleEvery);
        this.recordClaims = recordClaims;
        this.keepDecisions = keepDecisions;
        for (int i = 0; i < 4; i++) {
            runningScores[i] = startScore;
        }
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
        decisions.add(row);
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
        for (int i = handRowFrom; i < decisions.size(); i++) {
            Map<String, Object> dr = decisions.get(i);
            dr.put("hand_delta", Json.intList(delta));
            dr.put("hand_winner", row.get("winner"));
            dr.put("hand_loser", row.get("loser"));
            dr.put("hand_agari", row.get("agari"));
        }
    }

    // ------------------------------------------------------------------ 收尾

    /** 整场结束：回填顺位。必须在 {@code Table.playGame()} 返回之后调用。 */
    void finish(Table t) {
        int[] finalScores = new int[4];
        for (int i = 0; i < 4; i++) {
            finalScores[i] = t.seat(i).score;
        }
        List<Object> placement = placementOf(finalScores);
        for (Map<String, Object> row : decisions) {
            row.put("final_scores", Json.intList(finalScores));
            row.put("placement", placement);
        }
        if (dir == null) {
            return;
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
