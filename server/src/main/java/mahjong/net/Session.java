package mahjong.net;

import java.io.BufferedInputStream;
import java.io.BufferedWriter;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;

import mahjong.game.Table;
import mahjong.replay.ReplayRecorder;
import mahjong.replay.ReplayStore;
import mahjong.util.Json;
import mahjong.util.Log;

/** 一个客户端连接。读线程解析 cmd，写线程串行下行。 */
public final class Session {

    private final Server server;
    private final Socket socket;
    private final BufferedInputStream in;
    private final BufferedWriter out;
    private final BlockingQueue<Map<String, Object>> outbox = new ArrayBlockingQueue<>(OUTBOX_CAPACITY);

    /**
     * 下行队列容量。**必须有界**：对端只要不读，无界队列就会一直长，
     * 一个卡住的客户端足以把整个进程的堆拖垮（AUDIT S-19）。
     */
    private static final int OUTBOX_CAPACITY = 512;
    /** 聊天频率闸：同一窗口内最多这么多条（超出直接丢弃，不再广播）。 */
    private static final int CHAT_MAX_PER_WINDOW = 5;
    private static final long CHAT_WINDOW_MS = 5000;
    private long chatWindowStart;
    private int chatCount;
    private volatile boolean closed;
    private Thread reader;
    private Thread writer;

    /** 单条报文的字节上限（1 MB）。 */
    private static final int MAX_LINE_BYTES = 1024 * 1024;
    /** 上一次 {@link #readBoundedLine()} 是不是因为超限而中断的。 */
    private boolean lineTooLong;

    public long pid;
    public String token = "";
    public String name = "玩家";
    public volatile Table table;
    public volatile int seat = -1;
    public volatile boolean spectator;
    public volatile boolean welcomed;

    public Session(Server server, Socket socket) throws IOException {
        this.server = server;
        this.socket = socket;
        socket.setTcpNoDelay(true);
        this.in = new BufferedInputStream(socket.getInputStream(), 64 * 1024);
        this.out = new BufferedWriter(new OutputStreamWriter(socket.getOutputStream(), StandardCharsets.UTF_8));
    }

    public void start() {
        writer = new Thread(this::writeLoop, "session-writer");
        writer.setDaemon(true);
        writer.start();
        reader = new Thread(this::readLoop, "session-reader");
        reader.setDaemon(true);
        reader.start();
    }

    public void send(Map<String, Object> ev) {
        if (closed) {
            return;
        }
        // 队列满 = 这个客户端根本没在读。丢掉这一条而不是无限堆积：
        // 牌局照常推进（轮到它时该代打就代打），服务端的堆则是有界的。
        if (!outbox.offer(ev)) {
            Log.warn("下行队列已满（" + OUTBOX_CAPACITY + "），丢弃一条事件（pid=" + pid + "）");
        }
    }

    /** 聊天频率闸：窗口内超量就丢弃（返回 false）。 */
    public boolean allowChat() {
        final long now = System.currentTimeMillis();
        if (now - chatWindowStart > CHAT_WINDOW_MS) {
            chatWindowStart = now;
            chatCount = 0;
        }
        chatCount++;
        if (chatCount > CHAT_MAX_PER_WINDOW) {
            Log.warn("聊天频率超限，丢弃（pid=" + pid + "）");
            return false;
        }
        return true;
    }

    /**
     * 发错误事件。**只发 ASCII**：`code` 是稳定错误码，`msg` 是**可选的 ASCII 参数**
     * （例如 `unknown_cmd` 带上那个命令名），显示文本由客户端查语言文件翻
     * （`client/assets/i18n/*.json` 的 `error.*`）。见 `docs/PROTOCOL.md` §0。
     */
    public void sendError(String code, String arg) {
        Map<String, Object> ev = Json.obj("ev", "error", "code", code);
        if (arg != null && !arg.isEmpty()) {
            ev.put("arg", arg);
        }
        send(ev);
    }

    /** 无参数的错误（最常用）。 */
    public void sendError(String code) {
        sendError(code, null);
    }

    /**
     * 回放接口的频率闸：**与聊天分开计**，而且比聊天宽松得多（翻页要连发几次）。
     *
     * <p>为什么回放也要限速：{@code replay_list} / {@code replay_get} 每次都要读磁盘或翻缓存，
     * 一个循环发请求的客户端就能把磁盘 I/O 打满、把所有人的对局卡住。
     * 这里每 10 秒最多 60 次（正常翻页 + 列表刷新远远够用）。
     */
    private static final int REPLAY_MAX_PER_WINDOW = 60;
    private static final long REPLAY_WINDOW_MS = 10_000;
    private long replayWindowStart;
    private int replayCount;

    private boolean allowReplay() {
        long now = System.currentTimeMillis();
        if (now - replayWindowStart > REPLAY_WINDOW_MS) {
            replayWindowStart = now;
            replayCount = 0;
        }
        replayCount++;
        return replayCount <= REPLAY_MAX_PER_WINDOW;
    }

    /**
     * 重连令牌用**密码学**随机数。
     *
     * <p>原来是 `Math.random()` 派生的：`java.util.Random` 只有 48 位状态、不是 CSPRNG，
     * 而 `pid` 是公开的（每个 `room` 事件里都带），房间号也只有 4 个字符 32 进制 ——
     * 令牌一旦能被离线推算，任何人都能 `rejoin` 成别人、顶掉他的座位（AUDIT F20）。
     */
    private static final java.security.SecureRandom TOKEN_RNG = new java.security.SecureRandom();

    public void close() {
        if (closed) {
            return;
        }
        closed = true;
        try {
            socket.close();
        } catch (IOException ignored) {
            // 忽略
        }
        if (writer != null) {
            writer.interrupt();
        }
        server.onClosed(this);
    }

    public boolean isClosed() {
        return closed;
    }

    private void writeLoop() {
        try {
            long lastWrite = System.currentTimeMillis();
            while (!closed) {
                Map<String, Object> ev = outbox.poll(2, TimeUnit.SECONDS);
                if (ev == null) {
                    if (System.currentTimeMillis() - lastWrite > 20000) {
                        write(Json.obj("ev", "pong"));
                        lastWrite = System.currentTimeMillis();
                    }
                    continue;
                }
                write(ev);
                lastWrite = System.currentTimeMillis();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (IOException e) {
            // 连接断开
        } finally {
            close();
        }
    }

    private void write(Map<String, Object> ev) throws IOException {
        String line = Json.write(ev);
        synchronized (out) {
            out.write(line);
            out.write('\n');
            out.flush();
        }
    }

    /**
     * 读一行（不含换行符），**边读边判**上限 {@link #MAX_LINE_BYTES}。
     *
     * <p>⚠ 不能用 {@code BufferedReader.readLine()}：它会先把整行收完再返回，
     * 事后再判长度已经晚了 —— 对端只要一直不发 {@code '\n'}，那个内部 StringBuilder
     * 就会一直长，**一个连接**就能把堆撑爆（AUDIT F3）。这里每读一个字节就查一次上限。
     *
     * @return 行内容（可能为空串 = 空行）；{@code null} = 连接结束**或**该行超限
     *         （超限时 {@link #lineTooLong} 为 true，由调用方回一条 too_large 再断开）
     */
    private String readBoundedLine() throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream(256);
        while (true) {
            int c = in.read();
            if (c < 0 || c == '\n') {
                byte[] b = buf.toByteArray();
                if (c < 0 && b.length == 0) {
                    return null;                    // 连接正常结束
                }
                int len = b.length;
                if (len > 0 && b[len - 1] == '\r') {
                    len--;                          // 去掉 CRLF 的 '\r'（readLine 本来就会去）
                }
                return new String(b, 0, len, StandardCharsets.UTF_8);
            }
            if (buf.size() >= MAX_LINE_BYTES) {
                lineTooLong = true;
                return null;
            }
            buf.write(c);
        }
    }

    private void readLoop() {
        try {
            String line;
            while (!closed && (line = readBoundedLine()) != null) {
                if (line.isEmpty()) {
                    continue;
                }
                Object parsed = Json.tryParse(line);
                Map<String, Object> msg = Json.asObj(parsed);
                if (msg == null) {
                    sendError("bad_json");
                    continue;
                }
                try {
                    handle(msg);
                } catch (RuntimeException e) {
                    Log.error("处理 cmd 失败", e);
                    sendError("internal");
                }
            }
            if (lineTooLong) {
                sendError("too_large");
            }
        } catch (IOException e) {
            // 正常断开
        } finally {
            close();
        }
    }

    private void handle(Map<String, Object> msg) {
        String cmd = Json.str(msg, "cmd", "");
        if (!welcomed && !"hello".equals(cmd) && !"rejoin".equals(cmd)) {
            sendError("need_hello");
            return;
        }
        switch (cmd) {
            case "hello": {
                if (welcomed) {
                    // 同一条连接上重复 hello 会换发新的 pid/token，而 Seat.pid 仍是旧的 →
                    // 房主权限静默失效（pid != hostPid）、rejoin 也再找不到人。
                    // 重连必须新开连接走 rejoin，这里直接忽略这次 hello（不改任何状态）。
                    Log.warn("忽略重复的 hello（pid=" + pid + "）");
                    return;
                }
                // ⚠ 按**码点**截断：报文全程 UTF-8，substring 按 UTF-16 码元切会把
                //   代理对（𠮷 / 🀄 这类）切成半个字符，写出时变成 '?'（见 Json.clampCodePoints）。
                name = Json.clampCodePoints(Json.str(msg, "name", "玩家"), 24);
                pid = server.nextPid();
                token = Long.toHexString(TOKEN_RNG.nextLong());
                welcomed = true;
                send(Json.obj("ev", "hello_ok", "pid", pid, "token", token, "name", name, "ver", 1));
                break;
            }
            case "rejoin": {
                long wantPid = Json.l(msg, "pid", 0);
                Session old = server.sessionByPid(wantPid);
                if (old == null || !old.token.equals(Json.str(msg, "token", ""))) {
                    sendError("bad_token");
                    return;
                }
                if (old == this) {
                    // 自己 rejoin 自己：下面那段会在 old == this 时把**本连接的** table
                    // 置成 null，于是这条连接再也发不出任何动作（每次都被判 no_room），
                    // 而牌桌还把它当成那个座位的 session 一直广播（AUDIT F8）。
                    sendError("bad_token");
                    return;
                }
                welcomed = true;
                pid = old.pid;
                token = old.token;
                name = old.name;
                server.replaceSession(old, this);
                if (old.table != null) {
                    table = old.table;
                    seat = old.seat;
                    spectator = old.spectator;
                    old.table = null;
                    if (seat >= 0) {
                        table.seat(seat).session = this;
                        table.seat(seat).pid = pid;
                        table.seat(seat).name = name;
                        table.seat(seat).bot = false;
                    } else {
                        table.spectators.add(this);
                    }
                    send(Json.obj("ev", "hello_ok", "pid", pid, "token", token, "name", name, "ver", 1));
                    table.broadcastRoom();
                    send(table.stateFor(seat));
                } else {
                    send(Json.obj("ev", "hello_ok", "pid", pid, "token", token, "name", name, "ver", 1));
                }
                // 旧连接必须**真正关掉**，不能只置 closed 标志：置标志的话它的读线程
                // 还阻塞在 readLine 上、socket 也不关，每 rejoin 一次就永久泄漏
                // 一个线程 + 一个 fd（而且已经从 sessions 里摘掉，谁也回收不了，AUDIT F7）。
                old.close();
                break;
            }
            case "ping":
                send(Json.obj("ev", "pong"));
                break;
            case "list_rooms":
                send(server.roomsEvent());
                break;
            case "create_room": {
                if (table != null) {
                    sendError("in_room");
                    return;
                }
                String rname = Json.str(msg, "name", name + " 的房间");
                Map<String, Object> rulesJson = Json.map(msg, "rules");
                Table t = server.createTable(rname, rulesJson, this);
                if (t == null) {
                    sendError("no_room");
                    return;
                }
                // 房间状态类命令一律在桌子锁里做（见 Table.roomLock 的说明）：
                // 等待室命令跑在各连接自己的线程上，判据 + 改座位 + 广播必须是一个原子步。
                synchronized (t.roomLock()) {
                    table = t;
                    seat = 0;
                    t.seat(0).session = this;
                    t.seat(0).pid = pid;
                    t.seat(0).name = name;
                    t.hostPid = pid;
                    // 补机器人数量必须钳制：`{"fill_bots":2147483647}` 会让这个循环空转
                    // 21 亿次（每次都要扫 4 个座位），一条 60 字节的报文就能把一个核占满几十秒（AUDIT F6）。
                    int bots = Math.max(0, Math.min(Json.i(msg, "fill_bots", 0), 4));
                    for (int i = 0; i < bots; i++) {
                        t.addBot(-1);
                    }
                    t.start();
                    send(Json.obj("ev", "room_joined", "room", t.id, "seat", 0));
                    t.broadcastRoom();
                }
                server.broadcastRooms();
                break;
            }
            case "join_room": {
                if (table != null) {
                    sendError("in_room");
                    return;
                }
                Table t = server.table(Json.str(msg, "room", ""));
                if (t == null) {
                    sendError("no_room");
                    return;
                }
                synchronized (t.roomLock()) {
                    int idx = t.firstEmptySeat();
                    if (idx < 0 || t.playing) {
                        // 牌局进行中不接新座位：本局手牌/牌河已经发完，中途入座既拿不到状态，
                        // 也会让轮到的座位被一个不知情的客户端占住（AUDIT F10）。观战则有人数上限，
                        // 否则 N 个客户端进来互相广播聊天就是 O(N²) 的分配放大（AUDIT S-19）。
                        if (t.spectators.size() >= Table.MAX_SPECTATORS) {
                            sendError("no_room");
                            return;
                        }
                        t.spectators.add(this);
                        table = t;
                        seat = -1;
                        spectator = true;
                        send(Json.obj("ev", "spectate", "room", t.id));
                        send(t.stateFor(-1));
                        return;
                    }
                    table = t;
                    seat = idx;
                    t.seat(idx).session = this;
                    t.seat(idx).pid = pid;
                    t.seat(idx).name = name;
                    send(Json.obj("ev", "room_joined", "room", t.id, "seat", idx));
                    t.broadcastRoom();
                }
                server.broadcastRooms();
                break;
            }
            case "leave_room": {
                Table t = table;
                if (t == null) {
                    return;
                }
                synchronized (t.roomLock()) {
                    // ⚠ 用**座位表里的真实座位**而不是 `this.seat`：换座只更新发起者的
                    //   `seat`（另见 `Table.resyncSessionSeats`），万一两者还不一致，
                    //   以座位表为准才不会把别人的座位清掉。
                    final int mine = t.seatOfSession(this) >= 0 ? t.seatOfSession(this) : seat;
                    if (mine >= 0 && mine < 4) {
                        if (t.playing) {
                            t.seat(mine).session = null;
                            t.seat(mine).bot = true;
                            t.seat(mine).ready = true;
                        } else {
                            t.seat(mine).session = null;
                            t.seat(mine).pid = 0;
                            t.seat(mine).name = "";
                            t.seat(mine).ready = false;
                        }
                    } else {
                        t.spectators.remove(this);
                    }
                    table = null;
                    seat = -1;
                    spectator = false;
                    send(Json.obj("ev", "left_room"));
                    t.broadcastRoom();
                }
                t.onSessionClosed(this);
                t.recycleIfEmpty();
                server.broadcastRooms();
                break;
            }
            case "ready": {
                Table t = table;
                if (t == null || seat < 0) {
                    sendError("no_room");
                    return;
                }
                final boolean wantReady = Json.bool(msg, "ready", true);
                synchronized (t.roomLock()) {
                    // ⚠ 按座位表反查自己的座位，而不是信 `this.seat`：
                    //   别人换座把自己换到别处时，旧代码会把"我已准备"写到**别人**的座位上
                    //   （实测：B 的准备落在 A 的座位，B 自己一直不准备，牌局永远开不了）。
                    final int mine = t.seatOfSession(this);
                    if (mine < 0) {
                        sendError("no_room");
                        return;
                    }
                    t.seat(mine).ready = wantReady;
                    seat = mine;          // 顺手把连接自己的座位号对齐
                    t.broadcastRoom();
                }
                break;
            }
            case "add_bot": {
                Table t = table;
                if (t == null || pid != t.hostPid) {
                    sendError("not_host");
                    return;
                }
                final int wantSeat = Json.i(msg, "seat", -1);
                synchronized (t.roomLock()) {
                    if (t.playing) {
                        // 牌局进行中不增删座位：中途空出来的座位会被 join_room 顶掉，
                        // 而 `Round` 里的手牌/牌河还在，接任者拿不到任何本局状态（AUDIT F10）。
                        Log.warn("忽略牌局进行中的 add_bot");
                        return;
                    }
                    t.addBot(wantSeat);
                    t.broadcastRoom();
                }
                break;
            }
            case "remove_bot": {
                Table t = table;
                if (t == null || pid != t.hostPid) {
                    sendError("not_host");
                    return;
                }
                final int wantSeat = Json.i(msg, "seat", -1);
                synchronized (t.roomLock()) {
                    if (t.playing) {
                        Log.warn("忽略牌局进行中的 remove_bot");
                        return;
                    }
                    t.removeBot(wantSeat);
                    t.broadcastRoom();
                }
                break;
            }
            case "take_seat": {
                // 开局前**自选座位**（用户要求）：把自己的 session 与目标座位上的住户**互换**。
                // 门风就是座次（东=0/南=1/西=2/北=3），所以"选座位"等价于"选门风"。
                //
                // 三条边界：
                //   · 只有**入座**的人能换（观战者 seat=-1 不能）；
                //   · 牌局进行中不许换（`Round` 里手牌/牌河已经按座位发完，换座会让两家错位）；
                //   · 目标是**机器人**时直接顶掉它（机器人没有连接，不需要跟它交换）。
                Table t = table;
                if (t == null || seat < 0) {
                    sendError("no_room");
                    return;
                }
                if (t.playing) {
                    Log.warn("忽略牌局进行中的 take_seat");
                    return;
                }
                final int want = Json.i(msg, "seat", -1);
                if (want < 0 || want >= 4 || want == seat) {
                    sendError("bad_seat");
                    return;
                }
                synchronized (t.roomLock()) {
                    // ⚠ 判 `playing` 与换座必须在**同一把锁**里：牌桌线程也在锁里把 playing 置真
                    //   （见 Table.run）。分开写就是 check-then-act —— 换座可以正好落在
                    //   "发牌已开始、座位表正在被按座位号读"的那一瞬间。
                    if (t.playing) {
                        Log.warn("忽略牌局进行中的 take_seat");
                        return;
                    }
                    // 用座位表反查我现在的座位（`this.seat` 理论上已被 resyncSessionSeats 对齐，
                    // 这里再查一次是为了不依赖"理论上"）。
                    final int mine = t.seatOfSession(this);
                    if (mine < 0) {
                        sendError("no_room");
                        return;
                    }
                    t.swapSeats(mine, want);
                    seat = want;
                    send(Json.obj("ev", "room_joined", "room", t.id, "seat", want));
                    t.broadcastRoom();
                }
                break;
            }
            case "shuffle_seats": {
                // 房主一键**随机洗座**（随机门风）。洗座会打乱准备状态，所以洗完全部取消准备，
                // 让四家重新确认 —— 否则"准备了却被换到别的风"会让人以为点错了。
                Table t = table;
                if (t == null || pid != t.hostPid) {
                    sendError("not_host");
                    return;
                }
                synchronized (t.roomLock()) {
                    if (t.playing) {
                        Log.warn("忽略牌局进行中的 shuffle_seats");
                        return;
                    }
                    t.shuffleSeats();
                    // 自己的座位号可能变了，重新认领（`shuffleSeats` 内部已 resync 过全体，
                    // 这里再取一次是为了把本连接的 seat 也写准）
                    seat = t.seatOfPid(pid);
                    if (seat >= 0) {
                        send(Json.obj("ev", "room_joined", "room", t.id, "seat", seat));
                    }
                    t.broadcastRoom();
                }
                break;
            }
            case "start_game": {
                Table t = table;
                if (t == null || seat < 0) {
                    sendError("no_room");
                    return;
                }
                // 只有房主能开局。原来任何在座的人都能发这条命令，顺手把四个座位
                // 的 ready 全部改成 true 并补满机器人 —— 一个路人就能把整桌拖进对局（AUDIT F9）。
                if (pid != t.hostPid) {
                    sendError("not_host");
                    return;
                }
                synchronized (t.roomLock()) {
                    if (t.playing) {
                        return;
                    }
                    for (int i = 0; i < 4; i++) {
                        if (!t.seat(i).occupied()) {
                            t.addBot(i);
                        }
                        t.seat(i).ready = true;
                    }
                    t.broadcastRoom();
                }
                break;
            }
            case "chat": {
                Table t = table;
                if (t == null) {
                    return;
                }
                if (!allowChat()) {
                    return;                 // 频率超限：静默丢弃，不做全文广播
                }
                String text = Json.clampCodePoints(Json.str(msg, "text", ""), 200);
                if (text.isEmpty()) {
                    return;
                }
                t.broadcast(Json.obj("ev", "chat", "seat", seat, "name", name, "text", text));
                break;
            }
            case "replay_list": {
                if (!allowReplay()) {
                    sendError("replay_rate_limited");
                    return;
                }
                ReplayStore store = ReplayStore.current();
                if (store == null || !store.enabled()) {
                    send(Json.obj("ev", "replay_list", "total", 0, "items", new ArrayList<>()));
                    return;
                }
                int offset = Json.i(msg, "offset", 0);
                int limit = Json.i(msg, "limit", 30);
                send(Json.obj("ev", "replay_list",
                        "total", store.count(),
                        "items", store.list(offset, limit)));
                break;
            }
            case "replay_get": {
                if (!allowReplay()) {
                    sendError("replay_rate_limited");
                    return;
                }
                ReplayStore store = ReplayStore.current();
                String rid = Json.str(msg, "id", "");
                // ⚠ ID 先过形状校验（10 位 base32）再拼路径：否则 "../../x" 就是目录穿越
                if (store == null || !store.enabled() || !ReplayRecorder.validId(rid)) {
                    sendError("replay_not_found");
                    return;
                }
                Map<String, Object> head = store.header(rid);
                List<Object> slice = store.slice(rid, Json.i(msg, "from", 0), Json.i(msg, "count", 600));
                if (head == null || slice == null) {
                    sendError("replay_not_found");
                    return;
                }
                int from = Json.i(msg, "from", 0);
                send(Json.obj("ev", "replay_get",
                        "id", rid,
                        "meta", head,
                        "from", from,
                        "total", Json.i(head, "entries", 0),
                        "entries", slice));
                break;
            }
            case "action": {
                Table t = table;
                if (t == null || seat < 0) {
                    sendError("no_room");
                    return;
                }
                t.submit(seat, msg);
                break;
            }
            case "confirm": {
                // 小局之间的「确认进入下一局」：投进该座位的收件箱，
                // 由 Table 在局间等待（最多 5 秒）里读取。房间不在对局中时直接忽略。
                Table t = table;
                if (t == null || seat < 0) {
                    sendError("no_room");
                    return;
                }
                t.submit(seat, msg);
                break;
            }
            default:
                sendError("unknown_cmd", cmd);
        }
    }
}
