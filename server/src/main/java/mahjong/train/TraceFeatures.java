package mahjong.train;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

import mahjong.ai.ObsFeatures;
import mahjong.util.Json;
import mahjong.util.Log;

/**
 * 轨迹**富化**：给每个 {@code g<序号>.jsonl} 生成同名的 {@code g<序号>.feat.bin}
 * （{@link ObsFeatures} 的派生特征 sidecar）。
 *
 * <h2>为什么是 sidecar，而不是改 trace 本身</h2>
 * 轨迹（`g*.jsonl`）是**被消费的契约**（`PROTOCOL.md` §8.4），改它要动三处 + 旧数据集作废。
 * 派生特征是 obs 的**纯函数**，所以单独放一个二进制 sidecar：
 * 轨迹不动、校验器照旧、富化可重跑可缓存（而且二进制比 JSON 省 ~4 倍体积）。
 *
 * <h2>文件格式（小端）</h2>
 * <pre>
 *   header: magic(4)=0x4D4A4654 "MJFT" · featureVersion(4) · nDec(4) · perDec(4) · perCand(4)
 *   A: nDec × perDec            int16   逐决策派生量（ObsFeatures.perDecision）
 *   B: nDec × int16             nLegal  每条决策的候选数（与 jsonl 里的决策行**同序**）
 *   C: ΣnLegal × perCand        int16   逐候选派生量（ObsFeatures.perCandidate，按 legal 顺序）
 * </pre>
 * ⚠ v2 起 A 段从 `uint8` 改成 `int16`：逐决策段不再只有危险度（0..100），
 * 还带上了向听/进张与打点粗估（点数最大 32000）——uint8 会把它截成 mod 256。
 * 读侧按 `int16` 解析（`mahjong_ml/dataset.py` 的 `load_sidecar`）。
 * Python 侧按固定 dtype 内存映射读取，不用逐行解析（`mahjong_ml/dataset.py`）。
 */
public final class TraceFeatures {

    /** "MJFT"。 */
    public static final int MAGIC = 0x4D4A4654;

    private TraceFeatures() {
    }

    /** 默认并行度：**≤75% 的核**（与 `docs/TRAINING.md` §0.1.2 的纪律一致）。 */
    public static int defaultWorkers() {
        return Math.max(1, Runtime.getRuntime().availableProcessors() * 3 / 4);
    }

    /** 富化结果。 */
    public static final class Report {
        public int files;
        public long decisions;
        public long candidates;
        public double seconds;
        public long bytes;

        @Override
        public String toString() {
            return String.format(
                    "== 派生特征富化：%d 个轨迹文件 / %d 条决策 / %d 个候选，用时 %.1fs（%.1f MB）%n"
                            + "   %s",
                    files, decisions, candidates, seconds, bytes / 1024.0 / 1024.0,
                    ObsFeatures.describe());
        }
    }

    /** 富化一个目录里的全部轨迹（{@code g*.jsonl} → 同名 {@code .feat.bin}）。 */
    public static Report run(String dir, int workers) throws IOException {
        Path d = Paths.get(dir);
        if (!Files.isDirectory(d)) {
            throw new IOException("不是目录：" + d.toAbsolutePath());
        }
        List<Path> files = new ArrayList<>();
        try (var it = Files.list(d)) {
            it.filter(p -> p.getFileName().toString().matches("g\\d+\\.jsonl"))
                    .sorted((a, b) -> Integer.compare(seq(a), seq(b)))
                    .forEach(files::add);
        }
        if (files.isEmpty()) {
            throw new IOException("目录里没有 g*.jsonl：" + d.toAbsolutePath());
        }
        final int w = Math.max(1, Math.min(workers <= 0 ? defaultWorkers() : workers, files.size()));
        final Report rep = new Report();
        final AtomicInteger next = new AtomicInteger();
        final long t0 = System.nanoTime();
        Thread[] pool = new Thread[w];
        for (int i = 0; i < w; i++) {
            pool[i] = new Thread(() -> {
                int idx;
                while ((idx = next.getAndIncrement()) < files.size()) {
                    try {
                        long[] r = oneFile(files.get(idx));
                        synchronized (rep) {
                            rep.files++;
                            rep.decisions += r[0];
                            rep.candidates += r[1];
                            rep.bytes += r[2];
                        }
                    } catch (IOException e) {
                        Log.warn("富化失败：" + files.get(idx).getFileName() + " —— " + e);
                    }
                }
            }, "feat-" + i);
            pool[i].setDaemon(true);
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
        rep.seconds = (System.nanoTime() - t0) / 1e9;
        return rep;
    }

    private static int seq(Path p) {
        String s = p.getFileName().toString();
        return Integer.parseInt(s.substring(1, s.indexOf('.')));
    }

    /** 富化单个文件，返回 `{决策数, 候选数, 字节数}`。 */
    private static long[] oneFile(Path jsonl) throws IOException {
        Path out = jsonl.resolveSibling(jsonl.getFileName().toString()
                .replace(".jsonl", ".feat.bin"));
        long decisions = 0;
        long candidates = 0;
        // 三段分开攒，最后按 header + A + B + C **顺序**写盘 ——
        // Python 侧才能"每段一次 memmap"（交错写就得逐行 Python 循环找偏移，几十万条会很难看）。
        Grow secA = new Grow(1 << 16);      // nDec × perDec  int16
        Grow secB = new Grow(1 << 12);      // nDec ×          int16
        Grow secC = new Grow(1 << 16);      // ΣnLegal × perCand int16
        try (FileChannel in = FileChannel.open(jsonl, StandardOpenOption.READ)) {
            java.nio.ByteBuffer all = java.nio.ByteBuffer.allocate((int) in.size());
            while (all.hasRemaining() && in.read(all) >= 0) {
                // 读满
            }
            all.flip();
            String text = StandardCharsets.UTF_8.decode(all).toString();
            for (String line : text.split("\n")) {
                if (line.isBlank()) {
                    continue;
                }
                Map<String, Object> row = Json.asObj(Json.tryParse(line));
                if (row == null || !"decision".equals(Json.str(row, "type", ""))) {
                    continue;
                }
                Map<String, Object> obs = Json.map(row, "obs");
                if (obs == null) {
                    continue;
                }
                ObsFeatures.View v = ObsFeatures.ofObs(obs);
                for (int x : ObsFeatures.perDecision(v)) {
                    // ⚠ int16：v3 起这一段混了危险度（0..100）与打点粗估（点数最大 32000），
                    //   原来按 uint8 写会把后几维**截断成 mod 256**（实测 golden 对拍直接红）。
                    secA.putShort((short) Math.max(Short.MIN_VALUE, Math.min(Short.MAX_VALUE, x)));
                }
                List<Object> legal = Json.list(row, "legal");
                int n = legal == null ? 0 : legal.size();
                secB.putShort((short) n);
                for (int i = 0; i < n; i++) {
                    for (int x : ObsFeatures.perCandidate(v, String.valueOf(legal.get(i)))) {
                        secC.putShort((short) x);
                    }
                }
                decisions++;
                candidates += n;
            }
        }
        try (FileChannel ch = FileChannel.open(out, StandardOpenOption.CREATE,
                StandardOpenOption.WRITE, StandardOpenOption.TRUNCATE_EXISTING)) {
            ByteBuffer head = ByteBuffer.allocate(20).order(ByteOrder.LITTLE_ENDIAN);
            head.putInt(MAGIC).putInt(ObsFeatures.FEATURE_VERSION)
                    .putInt((int) decisions).putInt(ObsFeatures.PER_DECISION)
                    .putInt(ObsFeatures.PER_CANDIDATE);
            head.flip();
            writeAll(ch, head);
            writeAll(ch, ByteBuffer.wrap(secA.buf, 0, secA.size));
            writeAll(ch, ByteBuffer.wrap(secB.buf, 0, secB.size));
            writeAll(ch, ByteBuffer.wrap(secC.buf, 0, secC.size));
            return new long[]{decisions, candidates, ch.size()};
        }
    }

    private static void writeAll(FileChannel ch, ByteBuffer buf) throws IOException {
        while (buf.hasRemaining()) {
            ch.write(buf);
        }
    }

    /** 小端可增长字节缓冲（不引第三方库，也不用大端序的 `DataOutputStream`）。 */
    private static final class Grow {
        byte[] buf;
        int size;

        Grow(int cap) {
            buf = new byte[cap];
        }

        void ensure(int extra) {
            if (size + extra <= buf.length) {
                return;
            }
            int cap = buf.length;
            while (cap < size + extra) {
                cap <<= 1;
            }
            buf = java.util.Arrays.copyOf(buf, cap);
        }

        void put(byte b) {
            ensure(1);
            buf[size++] = b;
        }

        void putShort(short v) {
            ensure(2);
            buf[size++] = (byte) (v & 0xFF);
            buf[size++] = (byte) ((v >> 8) & 0xFF);
        }
    }
}
