package mahjong.player;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

import mahjong.util.Json;
import mahjong.util.Log;

/**
 * 玩家档案库：**uuid → 玩家信息**（落盘，重启不丢）。
 *
 * <p>需求（2026-09）：连接后服务端向客户端要一个保存过的 uuid，客户端应答；
 * 没有就由服务端生成并回发给客户端保存；服务端没有这个 uuid 的记录就建一份初始档案。
 * **同一个 uuid = 同一个玩家**，显示仍以昵称为准；每次登录更新 `last_login`；
 * 定期清理超过 2 个月没登录的档案。uuid 将来要当**主键**保存玩家的其它信息，
 * 所以这一层的设计要点是"能长大"：
 *
 * <ol>
 *   <li><b>向前兼容</b>：每条档案里**认不出的键原样保留**（别的版本、或以后加的战绩字段
 *       不会因为跑了一次旧服务端就被抹掉）—— 与客户端 {@code Settings.extra} 同一套做法；</li>
 *   <li><b>原子落盘</b>：先写 {@code .tmp} 再 {@code ATOMIC_MOVE}，写一半崩了不会留下半个 JSON
 *       （否则下次启动整份档案全丢，那是"身份"这种数据最不能接受的失败方式）；</li>
 *   <li><b>形状先校验</b>：uuid 必须是 36 字符的 8-4-4-4-12 十六进制，**先校验再进表**；</li>
 *   <li><b>写放大有界</b>：登录是热路径（每次连接一次），所以落盘做**节流**
 *       （{@link #SAVE_MIN_INTERVAL_MS}）；真值始终在内存里，落盘只是稍后一点。</li>
 * </ol>
 *
 * <p>线程模型：{@code touch} 来自各连接的读线程，{@code purge}/{@code flush} 来自维护线程，
 * 所以公开方法一律 {@code synchronized}（档案不在牌桌热路径上，粗粒度锁足够）。
 */
public final class PlayerStore {

    private static final int FORMAT_VERSION = 1;
    private static final String FILE_NAME = "players.json";

    /** 缺省 TTL：**60 天**（需求里的"2 个月"）。 */
    public static final long DEFAULT_TTL_MS = 60L * 24 * 60 * 60 * 1000;

    /** 落盘节流：登录很频繁，但没必要每次都写文件（脏标志仍在，flush() 会补上）。 */
    private static final long SAVE_MIN_INTERVAL_MS = 2000;

    /** 档案里"我们管"的键；其余键原样保留（见 {@link Player#extra}）。 */
    private static final Set<String> KNOWN_KEYS =
            Set.of("uuid", "name", "created", "last_login", "logins");

    /**
     * 一份玩家档案。
     *
     * <p>字段刻意保持最小：`uuid`（主键）/ `name`（最后用的昵称）/ `created` / `last_login` /
     * `logins`。**其余一切以后加** —— 加字段时不必改这里：{@link #extra} 会把不认识的键原样带回去。
     */
    public static final class Player {
        public final String uuid;
        public String name = "";
        public long created;
        public long lastLogin;
        public int logins;
        /** 认不得的其它键（原样保留，向前兼容）。 */
        public final Map<String, Object> extra = new LinkedHashMap<>();

        Player(String uuid) {
            this.uuid = uuid;
        }

        /** 序列化：先铺认不得的键，再盖我们管的（保证我们的键是权威值）。 */
        public Map<String, Object> toJson() {
            Map<String, Object> o = new LinkedHashMap<>(extra);
            o.put("uuid", uuid);
            o.put("name", name);
            o.put("created", created);
            o.put("last_login", lastLogin);
            o.put("logins", logins);
            return o;
        }

        static Player fromJson(String uuid, Map<String, Object> m) {
            Player p = new Player(uuid);
            p.name = Json.str(m, "name", "");
            p.created = Json.l(m, "created", 0);
            p.lastLogin = Json.l(m, "last_login", 0);
            p.logins = Json.i(m, "logins", 0);
            for (Map.Entry<String, Object> e : m.entrySet()) {
                if (!KNOWN_KEYS.contains(e.getKey())) {
                    p.extra.put(e.getKey(), e.getValue());   // 认不得的键原样留着
                }
            }
            return p;
        }
    }

    /** 一次登录的结果。 */
    public static final class Login {
        public final Player player;
        /** 服务端此前**没有**这个 uuid 的档案（这次刚建的）。 */
        public final boolean isNew;

        Login(Player player, boolean isNew) {
            this.player = player;
            this.isNew = isNew;
        }
    }

    private final Path dir;
    private final Path file;
    private final long ttlMs;
    private final boolean enabled;
    private final Map<String, Player> players = new LinkedHashMap<>();
    private boolean dirty;
    private long lastSaveMs;

    public PlayerStore(Path dir, long ttlMs, boolean enabled) {
        this.dir = dir;
        this.file = dir.resolve(FILE_NAME);
        this.ttlMs = Math.max(0, ttlMs);
        this.enabled = enabled;
        if (enabled) {
            load();
        }
    }

    // ================================================================= 单例

    /**
     * 进程级单例（由 {@code Main} 按命令行配置；未配置 = 关闭）。
     *
     * <p>与 {@code ReplayStore} 同一套理由：握手发生在各连接的读线程，而它不该去看
     * "哪个 Server 实例"—— 玩家档案本来就是进程级的。
     */
    private static volatile PlayerStore current;

    public static void install(PlayerStore store) {
        current = store;
    }

    public static PlayerStore current() {
        return current;
    }

    public boolean enabled() {
        return enabled;
    }

    public Path dir() {
        return dir;
    }

    // ================================================================= 形状

    /**
     * 生成一个新 uuid：**密码学随机**（{@code UUID.randomUUID} 用 SecureRandom）。
     *
     * <p>为什么不能用 {@code Math.random()} 派生：uuid 是**身份凭据** —— 靠它才能接回原座位
     * （见 PROTOCOL §2.0）。可预测的 uuid 等于谁都能算出来、进而顶掉别人。
     * 同一个教训见 `Session` 里的重连令牌（AUDIT F20）。
     */
    public static String newUuid() {
        return UUID.randomUUID().toString();
    }

    /**
     * uuid 的合法形状：36 字符、位置 8/13/18/23 是 `-`、其余是十六进制。
     *
     * <p>大小写都收（输入宽进），但对外一律**小写**（输出严出，见 {@link #normalize}）。
     * 形状不对**不报错**，调用方按"客户端没有记录"处理 —— 老客户端/手改过的设置文件
     * 不该把连接卡住。
     */
    public static boolean validUuid(String s) {
        if (s == null || s.length() != 36) {
            return false;
        }
        for (int i = 0; i < 36; i++) {
            char c = s.charAt(i);
            if (i == 8 || i == 13 || i == 18 || i == 23) {
                if (c != '-') {
                    return false;
                }
                continue;
            }
            boolean hex = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
            if (!hex) {
                return false;
            }
        }
        return true;
    }

    /** 规范成小写；不合法返回 {@code null}。 */
    public static String normalize(String s) {
        if (!validUuid(s)) {
            return null;
        }
        return s.toLowerCase(Locale.ROOT);
    }

    // ================================================================= 登录 / 清理

    /**
     * 记一次登录：没有档案就建一份，有就更新 `last_login`（并 +1 次数）。
     *
     * @param uuid  已规范化的 uuid（调用方保证形状合法）
     * @param name  昵称；**空串表示"这次还不知道"**（`hello` 还没到）—— 那就保留旧昵称
     * @param nowMs 当前时刻（由调用方给，便于自检构造"两个月前登录过"的档案）
     * @return 登录结果；库未启用或 uuid 不合法时返回 {@code null}
     */
    public synchronized Login touch(String uuid, String name, long nowMs) {
        if (!enabled || uuid == null || !validUuid(uuid)) {
            return null;
        }
        Player p = players.get(uuid);
        boolean isNew = false;
        if (p == null) {
            p = new Player(uuid);
            p.created = nowMs;
            players.put(uuid, p);
            isNew = true;
        }
        p.lastLogin = nowMs;
        p.logins++;
        if (name != null && !name.isEmpty()) {
            p.name = name;
        }
        dirty = true;
        // 节流落盘：登录是热路径，但"已经写进内存"才是真值，文件稍后补上（flush/purge/退出时）。
        if (nowMs - lastSaveMs >= SAVE_MIN_INTERVAL_MS) {
            save();
        } else {
            Log.debug("玩家档案延后落盘（节流）：" + uuid);
        }
        if (isNew) {
            Log.info("新玩家档案 " + uuid + "（昵称 " + p.name + "）");
        }
        return new Login(p, isNew);
    }

    /**
     * 只更新昵称（档案不存在 / 名字没变时什么都不做）。
     *
     * <p>用在"uuid 比 hello 先到"的顺序上：认领时还不知道昵称，`hello` 到了再补。
     * **不动** `logins` 与 `last_login` —— 那是"一次连接 = 一次登录"的账。
     */
    public synchronized boolean rename(String uuid, String name) {
        if (!enabled || uuid == null || name == null || name.isEmpty()) {
            return false;
        }
        Player p = players.get(uuid);
        if (p == null || p.name.equals(name)) {
            return false;
        }
        p.name = name;
        dirty = true;
        return true;
    }

    /**
     * 清理"超过 {@code ttlMs} 没登录"的档案（需求：2 个月）。
     *
     * <p>判据是**严格大于**：`ttl = 0` 时"本毫秒刚登录过"的档案仍然留着
     * （自检与 `--uuid-ttl-days 0` 的运维用法都靠这条）。
     *
     * @param nowMs 当前时刻
     * @return 清掉的条数
     */
    public synchronized int purge(long nowMs) {
        if (!enabled) {
            return 0;
        }
        List<String> dead = new ArrayList<>();
        for (Map.Entry<String, Player> e : players.entrySet()) {
            if (nowMs - e.getValue().lastLogin > ttlMs) {
                dead.add(e.getKey());
            }
        }
        for (String u : dead) {
            Player p = players.remove(u);
            long days = p == null ? 0 : (nowMs - p.lastLogin) / 86400000L;
            Log.info("玩家档案过期清理：" + u + "（" + days + " 天未登录）");
        }
        if (!dead.isEmpty()) {
            dirty = true;
            save();
        }
        return dead.size();
    }

    /** 把内存里的真值落盘（节流用；没什么可写就什么都不做）。 */
    public synchronized boolean flush() {
        if (!enabled || !dirty) {
            return true;
        }
        return save();
    }

    public synchronized int count() {
        return players.size();
    }

    /** 取一份档案（只读；找不到返回 null）。 */
    public synchronized Player get(String uuid) {
        return uuid == null ? null : players.get(uuid);
    }

    // ================================================================= 落盘 / 加载

    /** 原子落盘：先写 `.tmp` 再改名。返回是否成功（失败只记日志，绝不影响对局）。 */
    public synchronized boolean save() {
        if (!enabled) {
            return false;
        }
        Path tmp = dir.resolve(FILE_NAME + ".tmp");
        try {
            Files.createDirectories(dir);
            Map<String, Object> root = new LinkedHashMap<>();
            root.put("v", FORMAT_VERSION);
            Map<String, Object> list = new LinkedHashMap<>();
            for (Player p : players.values()) {
                list.put(p.uuid, p.toJson());
            }
            root.put("players", list);
            try (BufferedWriter w = Files.newBufferedWriter(tmp, StandardCharsets.UTF_8)) {
                w.write(Json.write(root));
                w.write('\n');
                w.flush();
            }
            Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING,
                    StandardCopyOption.ATOMIC_MOVE);
            dirty = false;
            lastSaveMs = System.currentTimeMillis();
            return true;
        } catch (IOException | RuntimeException e) {
            Log.warn("玩家档案落盘失败 " + file + "：" + e.getMessage());
            try {
                Files.deleteIfExists(tmp);
            } catch (IOException ignore) {
                // 清理失败无所谓
            }
            return false;
        }
    }

    /**
     * 启动加载。文件不存在 = 空库（**不报错**，第一次跑就是这样）；损坏 = 备份成 `.bak`
     * 后从空库继续（绝不因为一份坏档案让服务端起不来）。
     */
    private void load() {
        try {
            Files.createDirectories(dir);
        } catch (IOException e) {
            Log.warn("玩家档案目录创建失败 " + dir + "：" + e.getMessage());
            return;
        }
        if (!Files.isRegularFile(file)) {
            return;
        }
        String raw;
        try (BufferedReader r = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) {
                sb.append(line);
                if (sb.length() > 32 * 1024 * 1024) {
                    throw new IOException("档案文件过大");
                }
            }
            raw = sb.toString();
        } catch (IOException e) {
            Log.warn("玩家档案读取失败 " + file + "：" + e.getMessage());
            return;
        }
        Map<String, Object> root = Json.asObj(Json.tryParse(raw));
        if (root == null) {
            Log.warn("玩家档案损坏，备份后从空库继续：" + file);
            try {
                Files.move(file, dir.resolve(FILE_NAME + ".bak"),
                        StandardCopyOption.REPLACE_EXISTING);
            } catch (IOException e) {
                Log.warn("坏档案备份失败：" + e.getMessage());
            }
            return;
        }
        Map<String, Object> list = Json.map(root, "players");
        for (Map.Entry<String, Object> e : list.entrySet()) {
            String uuid = normalize(e.getKey());
            Map<String, Object> m = Json.asObj(e.getValue());
            if (uuid == null || m == null) {
                continue;      // 形状不对的条目直接跳过（不因一行坏数据丢掉整份档案）
            }
            players.put(uuid, Player.fromJson(uuid, m));
        }
        if (!players.isEmpty()) {
            Log.info("玩家档案已加载 " + players.size() + " 份（" + file + "）");
        }
    }

    /** 供日志/自检：一行摘要。 */
    public synchronized String summary() {
        return players.size() + " 份档案（" + file + "，TTL " + (ttlMs / 86400000L) + " 天）";
    }
}
