package mahjong.net;

import java.io.BufferedInputStream;
import java.io.BufferedWriter;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.TimeUnit;

import mahjong.game.Table;
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
                server.broadcastRooms();
                break;
            }
            case "leave_room": {
                Table t = table;
                if (t == null) {
                    return;
                }
                if (seat >= 0) {
                    if (t.playing) {
                        t.seat(seat).session = null;
                        t.seat(seat).bot = true;
                        t.seat(seat).ready = true;
                    } else {
                        t.seat(seat).session = null;
                        t.seat(seat).pid = 0;
                        t.seat(seat).name = "";
                        t.seat(seat).ready = false;
                    }
                } else {
                    t.spectators.remove(this);
                }
                table = null;
                seat = -1;
                spectator = false;
                send(Json.obj("ev", "left_room"));
                t.broadcastRoom();
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
                t.seat(seat).ready = Json.bool(msg, "ready", true);
                t.broadcastRoom();
                break;
            }
            case "add_bot": {
                Table t = table;
                if (t == null || pid != t.hostPid) {
                    sendError("not_host");
                    return;
                }
                if (t.playing) {
                    // 牌局进行中不增删座位：中途空出来的座位会被 join_room 顶掉，
                    // 而 `Round` 里的手牌/牌河还在，接任者拿不到任何本局状态（AUDIT F10）。
                    Log.warn("忽略牌局进行中的 add_bot");
                    return;
                }
                t.addBot(Json.i(msg, "seat", -1));
                t.broadcastRoom();
                break;
            }
            case "remove_bot": {
                Table t = table;
                if (t == null || pid != t.hostPid) {
                    sendError("not_host");
                    return;
                }
                if (t.playing) {
                    Log.warn("忽略牌局进行中的 remove_bot");
                    return;
                }
                t.removeBot(Json.i(msg, "seat", -1));
                t.broadcastRoom();
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
