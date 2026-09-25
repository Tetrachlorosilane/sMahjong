// 推理开销微基准：把「一次决策」拆成 特征工程 / trunk / 打分头 三段量（单线程、真实 obs 与真实候选条数）。
//
//   java -cp server\build\mahjong-server.jar tools\BenchForward.java <net.bin> <trace.jsonl> [行数]
//   例：java -cp server\build\mahjong-server.jar tools\BenchForward.java ^
//          bot-ai\ppo2-g04\net.bin S:\mahjong-training\raw\bc-001\g0.jsonl 300
//
// 结论口径（2026-09 实测，见 docs/TRAINING.md §3.3）：网络只占一次决策的 ~7%，
// 真正的开销是**逐候选的进张/向听统计**（`HandEval.of` × 34 种进张），约 0.5 ms/候选。
// 用 JDK 的单文件源码模式直接跑，不需要编译；轨迹里只要 `obs` + `legal` 两个字段。
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import mahjong.ai.Features;
import mahjong.ai.NeuralPolicy;
import mahjong.ai.ObsFeatures;
import mahjong.util.Json;

public final class BenchForward {
    interface Op { void run(); }

    static double time(Op op, int iters) {
        for (int i = 0; i < 3000; i++) {
            op.run();
        }
        long t = System.nanoTime();
        for (int i = 0; i < iters; i++) {
            op.run();
        }
        return (System.nanoTime() - t) / 1000.0 / iters;
    }

    public static void main(String[] args) throws Exception {
        Path netPath = Path.of(args[0]);
        Path trace = Path.of(args[1]);
        int rows = args.length > 2 ? Integer.parseInt(args[2]) : 300;

        NeuralPolicy net = NeuralPolicy.load(netPath);
        System.out.printf("net=%s params=%,d%n", netPath.getFileName(), net.params());

        List<Map<String, Object>> obsList = new ArrayList<>();
        List<List<String>> legalList = new ArrayList<>();
        for (String line : Files.readAllLines(trace, StandardCharsets.UTF_8)) {
            if (line.isBlank() || obsList.size() >= rows) {
                continue;
            }
            Map<String, Object> row = Json.asObj(Json.parse(line));
            if (row == null || row.get("obs") == null) {
                continue;
            }
            obsList.add(Json.asObj(row.get("obs")));
            legalList.add(Json.strList(row, "legal"));
        }
        int maxLegal = 0;
        long sum = 0;
        for (List<String> l : legalList) {
            maxLegal = Math.max(maxLegal, l.size());
            sum += l.size();
        }
        double avgLegal = sum / (double) obsList.size();
        System.out.printf("样本 %d 条：合法动作 平均 %.1f / 最多 %d%n", obsList.size(), avgLegal, maxLegal);

        final int iters = 20000;
        final int[] k = {0};
        final List<String> one = List.of(legalList.get(0).get(0));
        final List<String> fat = new ArrayList<>();
        for (int i = 0; i < 34; i++) {
            fat.add(legalList.get(0).get(i % legalList.get(0).size()));
        }

        double tOfObs = time(() -> ObsFeatures.ofObs(obsList.get((k[0]++) % obsList.size())), iters);
        double tState = time(() -> {
            int i = (k[0]++) % obsList.size();
            Map<String, Object> o = obsList.get(i);
            Features.state(o, ObsFeatures.ofObs(o));
        }, iters);
        double tState1Cand = time(() -> {
            int i = (k[0]++) % obsList.size();
            Map<String, Object> o = obsList.get(i);
            ObsFeatures.View v = ObsFeatures.ofObs(o);
            Features.state(o, v);
            Features.candidates(v, one);
        }, iters);
        double tStateAllCand = time(() -> {
            int i = (k[0]++) % obsList.size();
            Map<String, Object> o = obsList.get(i);
            ObsFeatures.View v = ObsFeatures.ofObs(o);
            Features.state(o, v);
            Features.candidates(v, legalList.get(i));
        }, iters);
        double tLogits1 = time(() -> net.logits(obsList.get((k[0]++) % obsList.size()), one), iters);
        double tLogitsAvg = time(() -> {
            int i = (k[0]++) % obsList.size();
            net.logits(obsList.get(i), legalList.get(i));
        }, iters);
        double tLogitsFat = time(() -> net.logits(obsList.get(0), fat), iters);

        System.out.printf("ofObs（obs JSON → 视图）              : %7.1f us%n", tOfObs);
        System.out.printf("+ state（607 维，含 68 派生）         : %7.1f us%n", tState);
        System.out.printf("+ 1 个候选的 cand（96 维，含 8 派生） : %7.1f us%n", tState1Cand);
        System.out.printf("+ 全部 %.1f 个候选                     : %7.1f us（每候选 ≈ %.1f us）%n",
                avgLegal, tStateAllCand, (tStateAllCand - tState) / avgLegal);
        System.out.println("---");
        System.out.printf("整条 logits（1 候选）                 : %7.1f us%n", tLogits1);
        System.out.printf("整条 logits（真实 %.1f 候选）          : %7.1f us  → %.0f 决策/秒/核%n",
                avgLegal, tLogitsAvg, 1e6 / tLogitsAvg);
        System.out.printf("整条 logits（34 候选，最坏）           : %7.1f us  → %.0f 决策/秒/核%n",
                tLogitsFat, 1e6 / tLogitsFat);
        System.out.printf("推算：trunk+打分头 ≈ %.1f us/决策（1 候选）；每多一个候选 +%.1f us%n",
                tLogits1 - tState1Cand, (tLogitsAvg - tLogits1) / Math.max(1, avgLegal - 1));
    }
}

