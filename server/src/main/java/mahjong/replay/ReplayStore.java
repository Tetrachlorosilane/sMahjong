package mahjong.replay;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import mahjong.util.Json;
import mahjong.util.Log;

/**
 * 回放仓库：**内存索引 + 落盘**（重启不丢），带容量上限与热缓存。
 *
 * <p>为什么要落盘：用户要求"重启后还能看"。但落盘绝不意味着"随便写"——
 * 这一层同时负责三件事：
 *
 * <ol>
 *   <li><b>容量</b>：最多 {@code maxGames} 场 / {@code maxBytes} 总字节，超出按创建时间**淘汰最旧**
 *       （同时删文件），所以磁盘占用有硬上限；</li>
 *   <li><b>安全</b>：ID 必须匹配 {@link ReplayRecorder#validId}（10 位 base32），
 *       **先校验再拼路径** —— 否则 {@code ../../etc/passwd} 这种 id 就是目录穿越。
 *       同时读接口只读、不提供删除/改名；</li>
 *   <li><b>性能</b>：列表只回元信息；取单场支持**分块**（{@code from/count}）；
 *       最近看过的几场留在热缓存里（按总字节限制），冷场从磁盘流式读一次。</li>
 * </ol>
 *
 * <p>线程模型：{@code Table} 线程调用 {@link #put}，各连接的读线程调用 {@link #list}/{@link #slice}，
 * 所以公开方法一律 {@code synchronized}（回放不在牌桌热路径上，粗粒度锁足够）。
 */
public final class ReplayStore {

    /** 落盘文件名后缀。 */
    private static final String EXT = ".replay";
    private static final int FORMAT_VERSION = 1;

    private final Path dir;
    private final int maxGames;
    private final long maxBytes;
    private final boolean enabled;

    /** 索引：id → 头信息（不含操作）。按创建时间倒序维护在 {@link #order} 里。 */
    private final Map<String, Map<String, Object>> index = new LinkedHashMap<>();
    private final List<String> order = new ArrayList<>();
    private long totalBytes;

    /** 热缓存：整个 entry 列表（按总字节上限淘汰）。 */
    private final Map<String, List<Replay.Entry>> hot = new LinkedHashMap<>(8, 0.75f, true);
    private long hotBytes;

    public ReplayStore(Path dir, int maxGames, long maxBytes, boolean enabled) {
        this.dir = dir;
        this.maxGames = Math.max(1, maxGames);
        this.maxBytes = Math.max(1024 * 1024, maxBytes);
        this.enabled = enabled;
        if (enabled) {
            load();
        }
    }

    /**
     * 进程级单例（由 {@code Main} 按命令行配置；未配置 = 关闭）。
     *
     * <p>为什么要单例：录制发生在 {@code Table} 线程、读取发生在各连接的读线程，
     * 而两者都不该去看"哪个 Server 实例"—— 回放库本来就是进程级的。
     * 自检会临时装一个指向临时目录的小容量库，跑完再装回去。
     */
    private static volatile ReplayStore current;

    public static void install(ReplayStore store) {
        current = store;
    }

    public static ReplayStore current() {
        return current;
    }

    public boolean enabled() {
        return enabled;
    }

    public Path dir() {
        return dir;
    }

    // ================================================================= 启动加载

    /**
     * 扫描目录重建索引：**只读每个文件的第一行**（头），不把整场读进内存。
     * 头读不出来的文件（半截写入 / 人为损坏）直接跳过并记一条 warn，不影响启动。
     */
    private void load() {
        try {
            Files.createDirectories(dir);
        } catch (IOException e) {
            Log.warn("回放目录创建失败 " + dir + "：" + e.getMessage());
            return;
        }
        List<Map<String, Object>> metas = new ArrayList<>();
        try (DirectoryStream<Path> ds = Files.newDirectoryStream(dir, "*" + EXT)) {
            for (Path p : ds) {
                Map<String, Object> m = readHeader(p);
                if (m != null) {
                    metas.add(m);
                }
            }
        } catch (IOException e) {
            Log.warn("回放目录扫描失败：" + e.getMessage());
            return;
        }
        metas.sort(Comparator.comparingLong(m -> -Json.l(m, "created", 0)));
        for (Map<String, Object> m : metas) {
            String id = Json.str(m, "id", "");
            if (!ReplayRecorder.validId(id)) {
                continue;
            }
            index.put(id, m);
            order.add(id);
            totalBytes += Json.l(m, "bytes", 0);
        }
        if (!index.isEmpty()) {
            Log.info("回放库已加载 " + index.size() + " 场（" + (totalBytes / 1024) + " KB）");
        }
        enforceCaps();
    }

    private Map<String, Object> readHeader(Path p) {
        try (BufferedReader r = Files.newBufferedReader(p, StandardCharsets.UTF_8)) {
            String line = r.readLine();
            if (line == null || line.isEmpty()) {
                return null;
            }
            Object o = Json.tryParse(line);
            if (!(o instanceof Map)) {
                return null;
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> m = (Map<String, Object>) o;
            if (Json.i(m, "v", 0) != FORMAT_VERSION) {
                return null;
            }
            return m;
        } catch (IOException | RuntimeException e) {
            Log.warn("回放文件损坏（跳过）：" + p.getFileName());
            return null;
        }
    }

    // ================================================================= 写入

    /**
     * 落盘一场（原子：先写 {@code .tmp} 再 move），并维护容量上限。
     *
     * @return 落盘后的记录（{@code bytes} 已填好）；未落盘（被禁用）时返回 null
     */
    public synchronized Replay put(Replay r) {
        if (!enabled || r == null || !ReplayRecorder.validId(r.id)) {
            return null;
        }
        Path tmp = dir.resolve(r.id + EXT + ".tmp");
        Path dst = dir.resolve(r.id + EXT);
        try {
            Files.createDirectories(dir);
            long written;
            try (BufferedWriter w = Files.newBufferedWriter(tmp, StandardCharsets.UTF_8)) {
                // 头一行：v + 完整头（含牌山与小局索引）——重建索引只需读这一行
                Map<String, Object> head = r.fileHeaderJson();
                head.put("v", FORMAT_VERSION);
                w.write(Json.write(head));
                w.write('\n');
                for (Replay.Entry e : r.entries) {
                    w.write(Json.write(e.toJson()));
                    w.write('\n');
                }
                w.flush();
            }
            written = Files.size(tmp);
            Files.move(tmp, dst, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            r.bytes = written;
            // 索引里存**完整头**（含每小局的牌山与小局索引）：`header()` 要用它，
            // 而列表接口会把重的字段摘掉（见 `list()`）—— 一份数据，两种粒度。
            Map<String, Object> m = r.fileHeaderJson();
            m.put("v", FORMAT_VERSION);
            index.put(r.id, m);
            order.remove(r.id);
            order.add(0, r.id);
            totalBytes += written;
            Log.info("回放已保存 " + r.id + "：" + r.entries.size() + " 条 / "
                    + (written / 1024) + " KB（共 " + order.size() + " 场）");
            enforceCaps();
            return r;
        } catch (IOException | RuntimeException e) {
            Log.warn("回放落盘失败 " + r.id + "：" + e.getMessage());
            try {
                Files.deleteIfExists(tmp);
            } catch (IOException ignore) {
                // 清理失败无所谓
            }
            return null;
        }
    }

    /** 名额 / 字节双上限：超出就淘汰最旧的（同时删文件与热缓存）。 */
    private void enforceCaps() {
        while (order.size() > maxGames || (totalBytes > maxBytes && order.size() > 1)) {
            String victim = order.remove(order.size() - 1);
            Map<String, Object> m = index.remove(victim);
            if (m != null) {
                totalBytes -= Json.l(m, "bytes", 0);
            }
            hot.remove(victim);
            try {
                Files.deleteIfExists(dir.resolve(victim + EXT));
                Log.info("回放淘汰（容量上限）：" + victim);
            } catch (IOException e) {
                Log.warn("淘汰回放文件失败 " + victim + "：" + e.getMessage());
            }
        }
        if (totalBytes < 0) {
            totalBytes = 0;
        }
    }

    // ================================================================= 读取

    /** 列表（按创建时间倒序，只回元信息）。 */
    public synchronized List<Object> list(int offset, int limit) {
        List<Object> out = new ArrayList<>();
        if (!enabled) {
            return out;
        }
        int lo = Math.max(0, offset);
        int n = Math.max(0, Math.min(limit, 100));
        for (int i = lo; i < order.size() && out.size() < n; i++) {
            Map<String, Object> m = index.get(order.get(i));
            if (m != null) {
                // 列表只回**元信息**：牌山（每小局 136 个数）与规则表不进列表报文，
                // 否则拉一次列表就是几百 KB。
                Map<String, Object> brief = new LinkedHashMap<>(m);
                brief.remove("walls");
                brief.remove("round_at");
                brief.remove("rules");
                brief.remove("v");
                out.add(brief);
            }
        }
        return out;
    }

    public synchronized int count() {
        return order.size();
    }

    /** 取头（含牌山与小局索引）。找不到返回 null。 */
    public synchronized Map<String, Object> header(String id) {
        if (!enabled || !ReplayRecorder.validId(id)) {
            return null;
        }
        List<Replay.Entry> entries = entriesOf(id);
        if (entries == null) {
            return null;
        }
        Map<String, Object> m = index.get(id);
        return m == null ? null : new LinkedHashMap<>(m);
    }

    /**
     * 分块取操作。
     *
     * @return {@code null} = 找不到；否则 {@code entries} 是 {@code [[seq,t,to,body],...]} 的原始形态
     */
    public synchronized List<Object> slice(String id, int from, int count) {
        if (!enabled || !ReplayRecorder.validId(id)) {
            return null;
        }
        List<Replay.Entry> entries = entriesOf(id);
        if (entries == null) {
            return null;
        }
        int lo = Math.max(0, Math.min(from, entries.size()));
        int hi = Math.max(lo, Math.min(lo + Math.max(0, Math.min(count, 2000)), entries.size()));
        List<Object> out = new ArrayList<>(hi - lo);
        for (int i = lo; i < hi; i++) {
            out.add(entries.get(i).toJson());
        }
        return out;
    }

    /** 取某场的牌山（小局 i 的 136 张；缺省第 0 小局）。 */
    public synchronized List<Object> wall(String id, int round) {
        if (!enabled || !ReplayRecorder.validId(id)) {
            return null;
        }
        List<Replay.Entry> entries = entriesOf(id);
        if (entries == null) {
            return null;
        }
        for (Replay.Entry e : entries) {
            if (Replay.EV_ROUND.equals(e.ev()) && Json.i(e.body, "index", -1) == round) {
                return Json.list(e.body, "wall");
            }
        }
        return new ArrayList<>();
    }

    /** 整场（热缓存命中，或从磁盘读一次）。找不到返回 null。 */
    private List<Replay.Entry> entriesOf(String id) {
        List<Replay.Entry> cached = hot.get(id);
        if (cached != null) {
            return cached;
        }
        Path p = dir.resolve(id + EXT);
        if (!Files.isRegularFile(p)) {
            index.remove(id);
            order.remove(id);
            return null;
        }
        List<Replay.Entry> out = new ArrayList<>();
        try (BufferedReader r = Files.newBufferedReader(p, StandardCharsets.UTF_8)) {
            String line = r.readLine();   // 头，跳过
            if (line == null) {
                return null;
            }
            while ((line = r.readLine()) != null) {
                if (line.isEmpty()) {
                    continue;
                }
                Object o = Json.tryParse(line);
                if (o instanceof Map) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> m = (Map<String, Object>) o;
                    out.add(Replay.Entry.fromJson(m));
                }
            }
        } catch (IOException | RuntimeException e) {
            Log.warn("回放读取失败 " + id + "：" + e.getMessage());
            return null;
        }
        long bytes = 0;
        for (Replay.Entry e : out) {
            bytes += Json.write(e.body).length() + 48;
        }
        if (bytes <= maxBytes) {
            hot.put(id, out);
            hotBytes += bytes;
            while (hotBytes > maxBytes && hot.size() > 1) {
                String victim = hot.keySet().iterator().next();
                List<Replay.Entry> drop = hot.remove(victim);
                if (drop != null) {
                    long b = 0;
                    for (Replay.Entry e : drop) {
                        b += Json.write(e.body).length() + 48;
                    }
                    hotBytes -= b;
                }
            }
            if (hotBytes < 0) {
                hotBytes = 0;
            }
        }
        return out;
    }
}
