package mahjong.replay;

import java.security.SecureRandom;
import java.util.List;
import java.util.Map;

import mahjong.util.Json;

/**
 * 一场半庄的录制器：挂在 {@code Table} 上，把**客户端实际收到的报文**按序记下来。
 *
 * <p>三条硬约束（都对应"安全与性能"）：
 * <ol>
 *   <li><b>容量有上限</b>：条数与字节数双封顶，超了就置 {@code truncated} 并停止续记
 *       —— 绝不因为一场异常长的对局把堆吃光（聊天刷屏、拖时间都算在内）；</li>
 *   <li><b>不阻塞牌桌线程</b>：只做一次浅拷贝 + 追加，不落盘、不加锁（落盘在牌局结束时一次性做，
 *       见 {@link ReplayStore#put}）；</li>
 *   <li><b>序号稳定</b>：{@link #add} 的调用点就是下行点，聊天与操作共用一条序号，
 *       所以"谁在哪一步说了什么"是确定的。</li>
 * </ol>
 */
public final class ReplayRecorder {

    /** 单场最多记录多少条（约 3 局/小局的几百条 × 十几小局，留足余量）。 */
    public static final int MAX_ENTRIES = 20000;
    /** 单场最多记录多少字节（估算值，按 JSON 串长度算）。 */
    public static final int MAX_BYTES = 8 * 1024 * 1024;
    /** 单条聊天正文上限（与协议一致：200 码点，这里按字节再兜一层）。 */
    private static final int MAX_CHAT_BYTES = 1024;

    private static final char[] ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ".toCharArray();
    private static final SecureRandom RANDOM = new SecureRandom();

    private final Replay replay;
    private final long startMs;
    /** 条数 / 字节双上限（构造函数可覆盖，自检用小的上限验证截断行为）。 */
    private final int maxEntries;
    private final int maxBytes;
    private int bytes;
    private int roundNo;

    public ReplayRecorder(Map<String, Object> rules, List<String> names) {
        this(rules, names, MAX_ENTRIES, MAX_BYTES);
    }

    /** 自定义上限（自检用：把上限压小才能在同一条用例里看到"超限即截断"）。 */
    public ReplayRecorder(Map<String, Object> rules, List<String> names, int maxEntries, int maxBytes) {
        this.replay = new Replay(newId(), System.currentTimeMillis(), rules, names);
        this.startMs = System.currentTimeMillis();
        this.maxEntries = Math.max(1, maxEntries);
        this.maxBytes = Math.max(64, maxBytes);
    }

    /**
     * 生成一个**不可猜**的回放 ID：10 位 Crockford 风格 base32（去掉 I/L/O/U，避免抄错），
     * 约 50 bit 熵。用 {@link SecureRandom} 而不是 {@code Math.random()}（后者只有 48 位状态，
     * 见 AUDIT S-16 的同款教训）。
     */
    public static String newId() {
        StringBuilder sb = new StringBuilder(10);
        for (int i = 0; i < 10; i++) {
            sb.append(ALPHABET[RANDOM.nextInt(ALPHABET.length)]);
        }
        return sb.toString();
    }

    /** ID 的合法形状（也是**路径安全**的判据：绝不允许把任意字符串拼进文件名）。 */
    public static boolean validId(String id) {
        if (id == null || id.length() != 10) {
            return false;
        }
        for (int i = 0; i < id.length(); i++) {
            char c = id.charAt(i);
            boolean ok = false;
            for (char a : ALPHABET) {
                if (a == c) {
                    ok = true;
                    break;
                }
            }
            if (!ok) {
                return false;
            }
        }
        return true;
    }

    public String id() {
        return replay.id;
    }

    /**
     * 记录本体（只读用途：牌桌用它写下 `roomId`，见 {@code Table.playGame}）。
     *
     * <p>之所以不另开一个 `setRoom()`：房间号只是元信息里的一个字段，
     * 走记录本体的公开字段比再加一层转调更不容易漏。
     */
    public Replay replay() {
        return replay;
    }

    public boolean truncated() {
        return replay.truncated;
    }

    public int size() {
        return replay.entries.size();
    }

    /**
     * 记一条**下行报文**。{@code to < 0} 表示广播。
     *
     * <p>会跳过 {@link Replay#SKIP} 里的事件；聊天正文再兜一层长度上限
     * （协议侧已经按码点截断，这里是防止 body 里塞了别的东西）。
     *
     * <p>⚠ **`synchronized`**：录制原本只在牌桌线程上发生，但**聊天**（各连接的读线程）
     * 与**投票事件**（同样来自各连接的读线程）也会经 {@code Table.broadcast} 走到这里 ——
     * 两个线程同时往 `entries` 这个 ArrayList 里追加，序号与内容都可能错乱
     * （而"回放与实时同一条渲染路径"正是这个功能的前提）。
     */
    public synchronized void add(int to, Map<String, Object> body) {
        if (body == null) {
            return;
        }
        String ev = Json.str(body, "ev", "");
        if (ev.isEmpty() || Replay.SKIP.contains(ev)) {
            return;
        }
        // 摸牌是**按座位单发**的（`broadcastDraw` 给四家各发一份），但只有摸牌那一份带 `tile`，
        // 其余三份是"谁摸了 + 剩余张数"的同一个事实。回放只需要留一份：
        // 留三份既让"下一步"要按三次，又让体积翻三倍（实测占整场的三成以上）。
        if ("draw".equals(ev) && !body.containsKey("tile")) {
            return;
        }
        if (replay.truncated || replay.entries.size() >= maxEntries) {
            replay.truncated = true;
            return;
        }
        String json = Json.write(body);
        int cost = json.length() + 48;
        if (bytes + cost > maxBytes) {
            replay.truncated = true;
            return;
        }
        bytes += cost;
        replay.entries.add(new Replay.Entry(replay.entries.size(),
                System.currentTimeMillis() - startMs, to, body));
    }

    /** 记一条**只给回放看**的条目（不发给任何客户端）。 */
    public synchronized void note(int to, Map<String, Object> body) {
        add(to, body);
    }

    /**
     * 记一小局的牌山快照。必须在这一小局的事件之前调用（客户端按这个条目切小局）。
     *
     * @param wallOrder 136 张、**按抓牌顺序**（配牌 52 → 可摸牌山 → 王牌 14）
     */
    public void noteRound(String bakaze, int kyoku, int honba, int dealer, int[] wallOrder) {
        if (wallOrder == null || wallOrder.length != 136) {
            return;
        }
        replay.rounds.add(replay.entries.size());
        replay.walls.add(wallOrder.clone());
        roundNo++;
        note(-1, Json.obj("ev", Replay.EV_ROUND,
                "index", replay.rounds.size() - 1,
                "bakaze", bakaze, "kyoku", kyoku, "honba", honba, "dealer", dealer,
                "wall", Json.intList(wallOrder)));
    }

    /** 当前小局序号（0 起），供日志/断言。 */
    public int roundNo() {
        return roundNo;
    }

    /** 收尾：返回可落盘的不可变记录（之后不再续记）。 */
    public synchronized Replay finish() {
        bytes = 0;
        return replay;
    }

    /** 记录里的小局数。 */
    public int rounds() {
        return replay.rounds.size();
    }

    /** 供自检：直接看记录（不落盘）。 */
    public Replay peek() {
        return replay;
    }
}
