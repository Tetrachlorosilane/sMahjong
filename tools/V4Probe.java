package tools;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import mahjong.ai.V4Features;
import mahjong.ai.V4Policy;
import mahjong.util.Json;
import mahjong.util.Log;

/**
 * v4 前向的 Java 侧**只读探针**（两种模式）：
 *
 * <pre>
 *   javac -encoding UTF-8 -cp server/build/mahjong-server.jar -d tools/build tools/V4Probe.java
 *
 *   # ① 对拍载荷：逐决策打 logits（供 C++ 的 `trainer v4net` 逐行比）
 *   java -cp "server/build/mahjong-server.jar;tools/build" tools.V4Probe &lt;net.bin&gt; &lt;corpus.jsonl&gt; …
 *
 *   # ② 自检：拿 golden 夹具核对"特征拼装 + 前向"
 *   java -cp "server/build/mahjong-server.jar;tools/build" tools.V4Probe --golden &lt;夹具&gt; [--tol 1e-4]
 * </pre>
 *
 * <p>与 {@link NetProbe} 同一套纪律：探针**只调公开入口**（{@code V4Features.assemble} /
 * {@code V4Policy.forwardAll}），绝不自己重写前向或特征 —— 探针里再实现一遍就等于给
 * "同样写错的 C++ 侧"发假阳性通行证。
 *
 * <p>`--golden` 的判据（与 `SelfTest.v4ForwardTests`、C++ 侧同一把尺子）：
 * ① 四张量逐元素 ≤ tol（**特征侧**：Java 实时派生量 vs 离线 sidecar）；
 * ② 四个推理头逐元素 ≤ tol、`policy` 的 argmax 逐条相同（**前向侧**）；
 * ③ 红证：`heads.policy.bias` 整体 +1 后每条 logit 必须恰好 +1 —— 否则"对拍通过"另有原因
 *   （例如权重压根没读进去、或读了另一个张量）；
 * ④ **失败要指名道姓**：打出超差最多的那几格（`tile[k][c]` / `evt[row][field]` / `cand[row][col]`…），
 *   否则"maxΔ=1.0"这种输出等于没说。
 */
public final class V4Probe {

    private static final int EXIT_USAGE = 2;
    private static final int EXIT_ERROR = 1;
    private static final int GOLDEN_MAGIC = 0x4D4A3447;      // "MJ4G"

    private V4Probe() {
    }

    public static void main(String[] args) throws IOException {
        System.setErr(new java.io.PrintStream(System.err, true, StandardCharsets.UTF_8));
        Log.quiet = true;
        if (args.length == 0) {
            System.err.println("用法：tools.V4Probe <net.bin> <corpus.jsonl> …"
                    + " | tools.V4Probe --golden <夹具> [--tol 1e-4]");
            System.exit(EXIT_USAGE);
        }
        if ("--golden".equals(args[0])) {
            System.exit(golden(args));
        }
        if ("--bench".equals(args[0])) {
            System.exit(bench(args));
        }
        if ("--cache".equals(args[0])) {
            System.exit(cache(args));
        }
        System.exit(payload(args));
    }

    // ---------------------------------------------------------------- ③ 性能实测（⚠ 不设指标）
    /**
     * `--bench <net.bin> <corpus.jsonl> [n]`：把**特征拼装**与**网络前向**分开计时。
     *
     * <p>为什么必须分开：**特征**与**网络**的优化手段完全不同（特征是引擎派生量 → 增量缓存/少算；
     * 网络是稠密矩阵向量 → 增量事件 / 循环推理），混在一起报一个数就没法判断该动哪一边。
     *
     * <p>⚠ **这里只报实测**：文档里那些单决策耗时的"验收数字"已删除（`docs/FEATURES-V4.md` §8），
     * **别把它写回输出里**。
     */
    private static int bench(String[] args) throws IOException {
        if (args.length < 3) {
            System.err.println("用法：tools.V4Probe --bench <net.bin> <corpus.jsonl> [条数上限]");
            return EXIT_USAGE;
        }
        int limit = args.length > 3 ? Integer.parseInt(args[3]) : 200;
        V4Policy net = V4Policy.load(Path.of(args[1]));
        java.util.List<Map<String, Object>> obs = new ArrayList<>();
        try (BufferedReader r = Files.newBufferedReader(Path.of(args[2]), StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null && obs.size() < limit) {
                Map<String, Object> row = Json.asObj(Json.tryParse(line.trim()));
                if (row == null || !"decision".equals(Json.str(row, "type", ""))) {
                    continue;
                }
                Map<String, Object> o = Json.map(row, "obs");
                if (o != null) {
                    obs.add(o);
                }
            }
        }
        if (obs.isEmpty()) {
            System.err.println("[V4Probe] 语料里没有 decision 行");
            return EXIT_ERROR;
        }
        // 预热（JIT + 派生特征里的一次性类加载），再计时
        for (int i = 0; i < Math.min(20, obs.size()); i++) {
            V4Features.assemble(obs.get(i));
            net.forwardAll(obs.get(i));
        }
        long t0 = System.nanoTime();
        java.util.List<V4Features.Tensors> pre = new ArrayList<>();
        for (Map<String, Object> o : obs) {
            pre.add(V4Features.assemble(o));
        }
        long t1 = System.nanoTime();
        for (V4Features.Tensors t : pre) {
            net.forwardAll(t);
        }
        long t2 = System.nanoTime();
        int n = obs.size();
        double feat = (t1 - t0) / 1e6 / n;
        double fwd = (t2 - t1) / 1e6 / n;
        System.out.println(String.format(Locale.ROOT,
                "bench n=%d 特征 %.2f ms/决策 | 前向净 %.2f ms/决策 | 单线程 %.1f 决策/秒",
                n, feat, fwd, 1000.0 / (feat + fwd)));
        return 0;
    }

    // ---------------------------------------------------------------- ② 增量事件缓存
    /**
     * `--cache <net.bin> <corpus.jsonl> [n]`：**增量 == 全量**的判据 + 三档缓存的耗时（性能实测项）。
     *
     * <p>判据分两半，缺一不可：
     * <ul>
     *   <li>**正确性**：按**决策顺序**喂同一份语料，缓存路径的七个头与无缓存路径**逐位**相同
     *       （用 `Float.floatToIntBits` 比，不是"≤1e-5"—— 同一份权重、同一批浮点值，差一位就是有 bug）；</li>
     *   <li>**有效性**：`ms/决策` 要有可比的下降，且缓存计数要**真的命中**（全 miss 的缓存等于没写）。</li>
     * </ul>
     * 三档（`V4Policy.setCacheLevel`）用来归因：1 = 只增量隐状态、2 = 再复用事件编码行、
     * 3 = 再复用注意力 `in_proj`。三档的输出都必须与基准逐位相同。
     */
    private static int cache(String[] args) throws IOException {
        if (args.length < 3) {
            System.err.println("用法：tools.V4Probe --cache <net.bin> <corpus.jsonl> [条数上限]");
            return EXIT_USAGE;
        }
        int limit = args.length > 3 ? Integer.parseInt(args[3]) : 2000;
        V4Policy net = V4Policy.load(Path.of(args[1]));
        java.util.List<Map<String, Object>> obs = new ArrayList<>();
        try (BufferedReader r = Files.newBufferedReader(Path.of(args[2]), StandardCharsets.UTF_8)) {
            String line;
            while ((line = r.readLine()) != null && obs.size() < limit) {
                Map<String, Object> row = Json.asObj(Json.tryParse(line.trim()));
                if (row == null || !"decision".equals(Json.str(row, "type", ""))) {
                    continue;
                }
                Map<String, Object> o = Json.map(row, "obs");
                if (o != null) {
                    obs.add(o);
                }
            }
        }
        if (obs.isEmpty()) {
            System.err.println("[V4Probe] 语料里没有 decision 行");
            return EXIT_ERROR;
        }
        // 基准：无缓存路径（判据④的左半边）
        net.setCacheEnabled(false);
        for (int i = 0; i < Math.min(20, obs.size()); i++) {
            net.forwardAll(obs.get(i));                          // 预热（JIT + 类加载）
        }
        java.util.List<Map<String, float[]>> base = new ArrayList<>(obs.size());
        long t0 = System.nanoTime();
        for (Map<String, Object> o : obs) {
            base.add(net.forwardAll(o));
        }
        long t1 = System.nanoTime();
        double baseMs = (t1 - t0) / 1e6 / obs.size();
        System.out.println(String.format(Locale.ROOT,
                "基准（无缓存）%.2f ms/决策（n=%d），七头逐位比较如下：", baseMs, obs.size()));
        boolean allOk = true;
        for (int level = 1; level <= 3; level++) {
            net.setCacheEnabled(true);
            net.setCacheLevel(level);
            net.resetCacheStats();
            for (int i = 0; i < Math.min(20, obs.size()); i++) {
                net.forwardCached(obs.get(i));
            }
            net.resetCacheStats();
            long bad = 0;
            double worst = 0;
            String where = "";
            long t2 = System.nanoTime();
            for (int i = 0; i < obs.size(); i++) {
                Map<String, float[]> got = net.forwardCached(obs.get(i));
                Map<String, float[]> want = base.get(i);
                for (String head : want.keySet()) {
                    float[] a = want.get(head);
                    float[] b = got.get(head);
                    if (a.length != b.length) {
                        bad++;
                        where = head + " 长度 " + b.length + " != " + a.length;
                        continue;
                    }
                    for (int j = 0; j < a.length; j++) {
                        if (Float.floatToIntBits(a[j]) != Float.floatToIntBits(b[j])) {
                            bad++;
                            double d = Math.abs((double) a[j] - b[j]);
                            if (d > worst) {
                                worst = d;
                                where = head + "[" + j + "]";
                            }
                        }
                    }
                }
            }
            long t3 = System.nanoTime();
            double ms = (t3 - t2) / 1e6 / obs.size();
            boolean ok = bad == 0;
            allOk = allOk && ok;
            System.out.println(String.format(Locale.ROOT,
                    "  level %d：%.2f ms/决策（%.1f×）| 逐位不一致 %d 处%s | %s",
                    level, ms, baseMs / ms, bad,
                    bad == 0 ? "" : "（最大 Δ=" + String.format(Locale.ROOT, "%.3g", worst)
                            + " @" + where + "）",
                    net.cacheStats()));
        }
        System.out.println(allOk ? "[V4Probe] 增量 == 全量（逐位）PASS"
                : "[V4Probe] 增量 == 全量 FAIL");
        return allOk ? 0 : EXIT_ERROR;
    }

    // ---------------------------------------------------------------- ① 对拍载荷
    private static int payload(String[] args) throws IOException {
        if (args.length < 2) {
            System.err.println("用法：tools.V4Probe <net.bin> <corpus1.jsonl> [corpus2.jsonl …]");
            return EXIT_USAGE;
        }
        Path netPath = Path.of(args[0]);
        if (!Files.isRegularFile(netPath)) {
            System.err.println("[V4Probe] 找不到权重文件：" + netPath);
            return EXIT_ERROR;
        }
        V4Policy net;
        try {
            net = V4Policy.load(netPath);
        } catch (IOException e) {
            System.err.println("[V4Probe] 权重加载失败：" + netPath + " :: " + e.getMessage());
            return EXIT_ERROR;
        }
        BufferedWriter out = new BufferedWriter(new OutputStreamWriter(System.out, StandardCharsets.UTF_8));
        int files = 0;
        int total = 0;
        for (int a = 1; a < args.length; a++) {
            try {
                total += probe(out, net, args[a]);
            } catch (IOException | RuntimeException e) {
                out.flush();
                System.err.println("[V4Probe] " + e.getMessage());
                return EXIT_ERROR;
            }
            files++;
        }
        out.flush();
        System.err.println("[V4Probe] " + files + " 个文件 / " + total + " 条决策（" + net.describe() + "）");
        return 0;
    }

    private static int probe(BufferedWriter out, V4Policy net, String file) throws IOException {
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
                    continue;
                }
                Map<String, Object> row = Json.asObj(Json.tryParse(t));
                if (row == null) {
                    throw new IOException(file + " 第 " + lineNo + " 行不是合法 JSON 对象");
                }
                if (!"decision".equals(Json.str(row, "type", ""))) {
                    continue;
                }
                Map<String, Object> obs = Json.map(row, "obs");
                if (obs == null) {
                    throw new IOException(file + " 第 " + lineNo + " 行：decision 行缺 obs");
                }
                if (Json.list(row, "legal") == null) {
                    throw new IOException(file + " 第 " + lineNo + " 行：decision 行缺 legal");
                }
                int step = Json.i(row, "step", -1);
                float[] logits = net.logits(obs);
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
            out.write("# " + file + " 0 条\n");
        }
        return count;
    }

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

    // ---------------------------------------------------------------- ② golden 自检
    /** 逐元素比较的收集器：记最大差 + 超差最多的前 N 格（带标签）。 */
    private static final class Cmp {
        final float tol;
        final int limit;
        float worst;
        final List<String> bad = new ArrayList<>();

        Cmp(float tol, int limit) {
            this.tol = tol;
            this.limit = limit;
        }

        void add(String label, float diff) {
            worst = Math.max(worst, diff);
            if (diff > tol && bad.size() < limit) {
                bad.add(label + " Δ=" + String.format(Locale.ROOT, "%.4g", diff));
            }
        }

        void dump(String what) {
            if (bad.isEmpty()) {
                return;
            }
            System.out.println("  " + what + " 超差格子：" + String.join(" | ", bad));
        }
    }

    private static void cmpMat(Cmp c, String name, float[][] a, float[][] b, String l0, String l1) {
        for (int i = 0; i < a.length; i++) {
            for (int j = 0; j < a[i].length; j++) {
                c.add(name + "[" + l0 + i + "][" + l1 + j + "]", Math.abs(a[i][j] - b[i][j]));
            }
        }
    }

    private static void cmpVec(Cmp c, String name, float[] a, float[] b, String l0) {
        for (int i = 0; i < a.length; i++) {
            c.add(name + "[" + l0 + i + "]", Math.abs(a[i] - b[i]));
        }
    }

    private static int golden(String[] args) throws IOException {
        String file = null;
        float tol = 1e-4f;
        for (int i = 1; i < args.length; i++) {
            if ("--tol".equals(args[i]) && i + 1 < args.length) {
                tol = Float.parseFloat(args[++i]);
            } else if (file == null) {
                file = args[i];
            }
        }
        if (file == null) {
            System.err.println("用法：tools.V4Probe --golden <夹具> [--tol 1e-4]");
            return EXIT_USAGE;
        }
        byte[] raw = Files.readAllBytes(Path.of(file));
        ByteBuffer bb = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        int magic = bb.getInt();
        if (magic != GOLDEN_MAGIC) {
            System.err.println("[V4Probe] 夹具魔数不对：" + Integer.toHexString(magic));
            return EXIT_ERROR;
        }
        bb.getInt();                                            // 夹具格式号
        int nCases = bb.getInt();
        int netLen = bb.getInt();
        byte[] netBytes = new byte[netLen];
        bb.get(netBytes);
        V4Policy net = V4Policy.loadBytes(netBytes, file);
        int vb = net.valueBins();

        Cmp feat = new Cmp(tol, 12);
        Cmp head = new Cmp(tol, 12);
        int argmaxOk = 0;
        List<Map<String, Object>> obsList = new ArrayList<>();
        for (int c = 0; c < nCases; c++) {
            int obsLen = bb.getInt();
            byte[] ob = new byte[obsLen];
            bb.get(ob);
            Map<String, Object> obs = Json.asObj(Json.tryParse(new String(ob, StandardCharsets.UTF_8)));
            obsList.add(obs);
            int n = bb.getShort() & 0xFFFF;
            for (int i = 0; i < n; i++) {
                int kl = bb.getShort() & 0xFFFF;
                bb.position(bb.position() + kl);
            }
            float[][] tile = readMat(bb, V4Features.KIND_COUNT, V4Features.C_TILE);
            float[][] evt = readMat(bb, V4Features.K_EVT, V4Features.C_EVT);
            float[] ctx = readVec(bb, V4Features.C_CTX);
            float[][] cand = readMat(bb, n, V4Features.C_CAND);
            float[] logits = readVec(bb, n);
            float[] value = readVec(bb, vb);
            float[] belief = readVec(bb, 3);
            float[][] danger = readMat(bb, n, 4);

            V4Features.Tensors t = V4Features.assemble(obs);
            String tag = "c" + c + ".";
            cmpMat(feat, tag + "tile", t.tile, tile, "k", "ch");
            cmpMat(feat, tag + "evt", t.evt, evt, "row", "f");
            cmpVec(feat, tag + "ctx", t.ctx, ctx, "i");
            cmpMat(feat, tag + "cand", t.cand, cand, "row", "col");

            Map<String, float[]> o = net.forwardAll(obs);
            cmpVec(head, tag + "policy", o.get("policy"), logits, "i");
            cmpVec(head, tag + "value", o.get("value"), value, "i");
            cmpVec(head, tag + "belief_tenpai", o.get("belief_tenpai"), belief, "i");
            cmpMat(head, tag + "danger", reshape(o.get("danger"), n, 4), danger, "row", "col");
            if (argmaxOf(o.get("policy")) == argmaxOf(logits)) {
                argmaxOk++;
            }
        }
        // 红证：策略头偏置 +1 ⇒ 每条 logit 恰好 +1
        float worstRed = 0f;
        if (!obsList.isEmpty()) {
            V4Policy shifted = net.debugOutputBiasShift(1.0f);
            float[] a = net.logits(obsList.get(0));
            float[] b = shifted.logits(obsList.get(0));
            for (int i = 0; i < a.length; i++) {
                worstRed = Math.max(worstRed, Math.abs((b[i] - a[i]) - 1f));
            }
        }
        boolean pass = feat.worst <= tol && head.worst <= tol && argmaxOk == nCases && worstRed <= 1e-3f;
        if (!pass) {
            feat.dump("特征侧");
            head.dump("前向侧");
        }
        System.out.println(String.format(Locale.ROOT,
                "golden cases=%d tol=%g 特征 maxΔ=%.3g 前向 maxΔ=%.3g argmax=%d/%d 红证 maxΔ=%.3g → %s",
                nCases, tol, feat.worst, head.worst, argmaxOk, nCases, worstRed, pass ? "PASS" : "FAIL"));
        return pass ? 0 : EXIT_ERROR;
    }

    private static float[][] readMat(ByteBuffer bb, int rows, int cols) {
        float[][] m = new float[rows][cols];
        for (int r = 0; r < rows; r++) {
            for (int c = 0; c < cols; c++) {
                m[r][c] = bb.getFloat();
            }
        }
        return m;
    }

    private static float[] readVec(ByteBuffer bb, int n) {
        float[] v = new float[n];
        for (int i = 0; i < n; i++) {
            v[i] = bb.getFloat();
        }
        return v;
    }

    private static float[][] reshape(float[] flat, int rows, int cols) {
        float[][] m = new float[rows][cols];
        for (int r = 0; r < rows; r++) {
            System.arraycopy(flat, r * cols, m[r], 0, cols);
        }
        return m;
    }
}
