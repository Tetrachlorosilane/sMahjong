package tools;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import mahjong.ai.NeuralPolicy;
import mahjong.util.Json;
import mahjong.util.Log;

/**
 * Java 侧**只读探针**：把 `NeuralPolicy` 的逐决策 logits 按**固定格式**打到 stdout，
 * 供训练端 C++ 引擎的神经网络前向做逐行对拍（`tools/trainer-net-parity.mjs`）。
 *
 * <pre>
 *   javac -encoding UTF-8 -cp server/build/mahjong-server.jar -d tools/build tools/NetProbe.java
 *   java -cp "server/build/mahjong-server.jar;tools/build" tools.NetProbe &lt;net.bin&gt; &lt;corpus1.jsonl&gt; [corpus2.jsonl …]
 * </pre>
 *
 * 每行的格式（**逐字符固定**，C++ 侧必须打印同一形状）：
 *
 * <pre>
 *   step=&lt;该行 step 字段，缺省 -1&gt; n=&lt;legal 条数&gt; argmax=&lt;最大值的下标；并列取最小下标&gt; logits=&lt;v0&gt;,&lt;v1&gt,…
 * </pre>
 *
 * 约定与理由：
 *
 * <ul>
 *   <li><b>只调 Java 自己的公开入口</b> `NeuralPolicy.logits(json, keys)` —— 探针里**不重写前向、
 *       不重算特征**。判据同 `WallProbe`：探针自己重实现一遍，就会与"同样写错的 C++ 侧"假阳性一致
 *       （2026-09 配牌那次踩过，见 WallProbe 注释）。</li>
 *   <li>`logits` 的值用 `String.format(Locale.ROOT, "%.9g", v)`：9 位有效数字足以**往返 float32**。
 *       ⚠ 显式 `Locale.ROOT`：默认 locale 会换小数点/数字形状，输出就不是"逐字符固定"了。</li>
 *   <li>`argmax` 用与 `NeuralPolicy.argmaxOf` **同一把尺子**（严格大于才更新 ⇒ 并列取最小下标；
 *       空输入 = -1），不在这里发明随机破平。</li>
 *   <li>stdout 只有载荷：加载权重时 `Log.info` 会往 stdout 打一行日志，所以先 `Log.quiet = true`。
 *       进度/统计走 stderr。</li>
 *   <li>语料行形状 = `TraceRecorder` 的 decision 行（`{"type":"decision","step":…,"obs":{…},"legal":[…]}`）；
 *       空行与非 decision 行跳过。**一条决策都没有的文件**打一行 `# &lt;文件名&gt; 0 条`（注释行，
 *       让两侧的文件划分能按行对齐）。</li>
 *   <li>参数缺失 → 打印用法后 exit 2；权重/corpus 读不到或 decision 行缺 `obs`/`legal` →
 *       明确报错 exit 1（**不静默跳过整个文件**）。</li>
 * </ul>
 */
public final class NetProbe {

    /** 用法错误 / 参数缺失。 */
    private static final int EXIT_USAGE = 2;
    /** 权重加载失败、语料读不到、decision 行形状不对。 */
    private static final int EXIT_ERROR = 1;

    private NetProbe() {
    }

    public static void main(String[] args) throws IOException {
        // stderr 显式 UTF-8：错误信息是中文，而 Windows 默认 stderr 编码是本地代码页（GBK）——
        // 调用方（tools/trainer-net-parity.mjs，自身输出是 UTF-8）与 DSH 的 UTF-8 控制台都会把它显示成乱码。
        System.setErr(new java.io.PrintStream(System.err, true, StandardCharsets.UTF_8));
        if (args.length < 2) {
            System.err.println("用法：java -cp \"server/build/mahjong-server.jar;tools/build\" tools.NetProbe"
                    + " <net.bin> <corpus1.jsonl> [corpus2.jsonl ...]");
            System.exit(EXIT_USAGE);
        }
        // ⚠ `NeuralPolicy.loadBytes` 会 `Log.info("神经网络策略已加载：…")` → stdout。
        //    探针的 stdout 是逐行对拍的载荷，必须先静音（WARN/ERROR 仍照常）。
        Log.quiet = true;

        Path netPath = Path.of(args[0]);
        if (!Files.isRegularFile(netPath)) {
            System.err.println("[NetProbe] 找不到权重文件：" + netPath);
            System.exit(EXIT_ERROR);
        }
        NeuralPolicy net;
        try {
            net = NeuralPolicy.load(netPath);
        } catch (IOException e) {
            System.err.println("[NetProbe] 权重加载失败：" + netPath + " :: " + e.getMessage());
            System.exit(EXIT_ERROR);
            return;
        }

        BufferedWriter out = new BufferedWriter(new OutputStreamWriter(System.out, StandardCharsets.UTF_8));
        int files = 0;
        int total = 0;
        try {
            for (int a = 1; a < args.length; a++) {
                total += probe(out, net, args[a]);
                files++;
            }
        } catch (IOException | RuntimeException e) {
            out.flush();
            System.err.println("[NetProbe] " + e.getMessage());
            System.exit(EXIT_ERROR);
            return;
        }
        out.flush();
        System.err.println("[NetProbe] " + files + " 个文件 / " + total + " 条决策（权重 " + netPath + "）");
    }

    /**
     * 逐行跑一个 corpus 文件，返回本次打出的决策条数。
     *
     * <p>文件级失败（读不到 / 行形状不对）直接抛 —— 由 `main` 统一报错并 exit 1。
     */
    private static int probe(BufferedWriter out, NeuralPolicy net, String file) throws IOException {
        Path p = Path.of(file);
        if (!Files.isRegularFile(p)) {
            throw new IOException("找不到 corpus 文件：" + file);
        }
        int count = 0;
        int lineNo = 0;
        try (BufferedReader r = Files.newBufferedReader(p, StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null) {
                lineNo++;
                String t = line.trim();
                if (t.isEmpty()) {
                    continue;                              // 空行：跳过
                }
                Map<String, Object> row = Json.asObj(Json.tryParse(t));
                if (row == null) {
                    throw new IOException(file + " 第 " + lineNo + " 行不是合法 JSON 对象");
                }
                if (!"decision".equals(Json.str(row, "type", ""))) {
                    continue;                              // 非 decision 行（hand/game）：跳过
                }
                Map<String, Object> obs = Json.map(row, "obs");
                if (obs == null) {
                    throw new IOException(file + " 第 " + lineNo + " 行：decision 行缺 obs");
                }
                if (Json.list(row, "legal") == null) {
                    throw new IOException(file + " 第 " + lineNo + " 行：decision 行缺 legal");
                }
                List<String> legal = Json.strList(row, "legal");
                int step = Json.i(row, "step", -1);

                float[] logits = net.logits(obs, legal);
                StringBuilder sb = new StringBuilder(64 + logits.length * 12);
                sb.append("step=").append(step)
                  .append(" n=").append(logits.length)
                  .append(" argmax=").append(argmaxOf(logits))
                  .append(" logits=");
                for (int i = 0; i < logits.length; i++) {
                    if (i > 0) {
                        sb.append(',');
                    }
                    sb.append(String.format(Locale.ROOT, "%.9g", logits[i]));
                }
                out.write(sb.toString());
                out.write('\n');
                count++;
            }
        }
        if (count == 0) {
            // 注释行（`#` 前缀）：让脚本按行对齐两侧的文件划分
            out.write("# " + file + " 0 条\n");
        }
        return count;
    }

    /**
     * 取最大值下标；并列取**最小下标**、空输入 = -1、NaN 不参与比较。
     *
     * <p>这是 `NeuralPolicy.argmaxOf`（private）的**同行为**副本：那边原来是"严格大于才更新"，
     * 所以并列天然取最小下标。对拍要求两边同一把尺子 —— 别改成 `>=` 或随机破平。
     */
    private static int argmaxOf(float[] out) {
        int best = -1;
        float bestVal = Float.NEGATIVE_INFINITY;
        for (int i = 0; i < out.length; i++) {
            if (out[i] > bestVal) {
                bestVal = out[i];
                best = i;
            }
        }
        return best;
    }
}
