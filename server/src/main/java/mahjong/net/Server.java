package mahjong.net;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;

import mahjong.core.Rules;
import mahjong.game.Table;
import mahjong.util.Json;
import mahjong.util.Log;

/** TCP 服务器：接受连接、管理房间。 */
public final class Server {

    private final String host;
    private final int port;
    private ServerSocket serverSocket;
    private volatile boolean running;
    private final AtomicLong pidSeq = new AtomicLong(1000);
    private final Map<String, Table> tables = new ConcurrentHashMap<>();
    // 用并发 Set 而不是 CopyOnWriteArrayList：接受连接时每加一个会话都要整表复制，
    // n 个连接就是 O(n²) 的数组拷贝，而且全在 accept 线程上（AUDIT F18）。
    private final Set<Session> sessions = ConcurrentHashMap.newKeySet();
    private final Random rnd = new Random();

    public Server(String host, int port) {
        this.host = host;
        this.port = port;
    }

    public long nextPid() {
        return pidSeq.incrementAndGet();
    }

    public Table table(String id) {
        return id == null ? null : tables.get(id.toUpperCase());
    }

    public Session sessionByPid(long pid) {
        for (Session s : sessions) {
            if (s.pid == pid) {
                return s;
            }
        }
        return null;
    }

    /**
     * 找"这个 uuid 正掉线托管在哪张桌上"（找不到返回 {@code null}）。
     *
     * <p>用途：同一个 uuid = 同一个玩家 —— 他重连时应该**接回原座位**而不是从大厅重新入座
     * （见 {@code Session.tryResumeSeat}）。房间数量是个位数，线性扫足够。
     */
    public Table tableWithAwaySeat(String uuid) {
        if (uuid == null || uuid.isEmpty()) {
            return null;
        }
        for (Table t : tables.values()) {
            if (t.seatOfUuid(uuid) >= 0) {
                return t;
            }
        }
        return null;
    }

    public void replaceSession(Session old, Session now) {
        sessions.remove(old);
        sessions.add(now);
    }

    public Table createTable(String name, Map<String, Object> rulesJson, Session creator) {
        String id = null;
        for (int i = 0; i < 50; i++) {
            String cand = randomId();
            if (!tables.containsKey(cand)) {
                id = cand;
                break;
            }
        }
        if (id == null) {
            return null;
        }
        Rules rules = Rules.fromJson(rulesJson);
        Table t = new Table(id, name, rules);
        t.onEmpty = () -> {
            if (tables.remove(t.id) != null) {
                Log.info("回收房间 " + t.id);
                broadcastRooms();
            }
            t.shutdown();
        };
        tables.put(id, t);
        Log.info("创建房间 " + id + " (" + name + ") by " + creator.name);
        return t;
    }

    private String randomId() {
        String chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < 4; i++) {
            sb.append(chars.charAt(rnd.nextInt(chars.length())));
        }
        return sb.toString();
    }

    public Map<String, Object> roomsEvent() {
        List<Object> list = new ArrayList<>();
        for (Table t : tables.values()) {
            list.add(Json.obj(
                    "id", t.id,
                    "name", t.name,
                    "players", t.playerCount(),
                    "seats", 4,
                    "playing", t.playing));
        }
        return Json.obj("ev", "rooms", "rooms", list);
    }

    public void broadcastRooms() {
        Map<String, Object> ev = roomsEvent();
        for (Session s : sessions) {
            if (s.table == null) {
                s.send(ev);
            }
        }
    }

    public void onClosed(Session s) {
        sessions.remove(s);
        Table t = s.table;
        if (t != null) {
            t.onSessionClosed(s);
            serverCleanup(t);
        }
    }

    private void serverCleanup(Table t) {
        if (!t.hasHumans() && !t.playing) {
            if (tables.remove(t.id) != null) {
                t.shutdown();
                Log.info("回收空房间 " + t.id);
            }
        }
        broadcastRooms();
    }

    public void start() throws IOException {
        serverSocket = bind();
        running = true;
        System.out.println("LISTENING " + host + ":" + port);
        System.out.flush();
        Log.info("立直麻将服务端已启动 " + serverSocket.getLocalSocketAddress()
                + (serverSocket.getInetAddress() instanceof java.net.Inet6Address
                        ? "（双栈：IPv4 与 IPv6 都可连）" : "（IPv4）"));
        while (running) {
            try {
                Socket sock = serverSocket.accept();
                // 连接数封顶：每个连接两条线程 + 一个 fd，而 accept 之前不需要任何认证 ——
                // 不封顶的话，开一堆空闲连接就能把线程/fd 耗光（AUDIT F3）。
                if (sessions.size() >= MAX_SESSIONS) {
                    Log.warn("连接数已达上限 " + MAX_SESSIONS + "，拒绝 " + sock.getRemoteSocketAddress());
                    try {
                        sock.close();
                    } catch (IOException ignored) {
                        // 忽略
                    }
                    continue;
                }
                Session s = new Session(this, sock);
                sessions.add(s);
                s.start();
                Log.debug("新连接 " + sock.getRemoteSocketAddress());
            } catch (IOException e) {
                if (running) {
                    Log.error("accept 失败", e);
                }
            }
        }
    }

    /** 同时在线连接数上限（一桌 4 人 + 观战，256 足够正常运营）。 */
    public static final int MAX_SESSIONS = 256;

    /**
     * 绑定监听端口。
     *
     * <p>优先用 IPv6 通配地址并关闭 {@code IPV6_V6ONLY}，这样同一个端口在
     * {@code 0.0.0.0}（IPv4）与 {@code [::]}（IPv6）上都能连——IPv4 连接会以
     * v4-mapped 形式到达。这一点在 WSL / 容器里很关键：只绑 IPv4 时，
     * 宿主机侧的 localhost 转发可能只暴露 IPv6 回环，客户端填 {@code 127.0.0.1}
     * 就会「连接被拒绝」。
     *
     * <p>若运行环境不支持 IPv6，自动回退为纯 IPv4 绑定。
     */
    private ServerSocket bind() throws IOException {
        IOException last = null;
        if (!host.contains(":") || host.equals("0.0.0.0") || host.equals("::")) {
            try {
                ServerSocket ss = new ServerSocket();
                ss.setReuseAddress(true);
                ss.bind(new InetSocketAddress(java.net.InetAddress.getByName("::"), port));
                if (ss.getInetAddress() instanceof java.net.Inet6Address) {
                    return ss;
                }
                ss.close();
            } catch (IOException e) {
                last = e;
                Log.warn("IPv6 双栈绑定失败（" + e.getMessage() + "），回退为 IPv4");
            }
        }
        ServerSocket ss = new ServerSocket();
        ss.setReuseAddress(true);
        String h = host.equals("::") ? "0.0.0.0" : host;
        try {
            ss.bind(new InetSocketAddress(h, port));
            return ss;
        } catch (IOException e) {
            if (last != null) {
                e.addSuppressed(last);
            }
            throw e;
        }
    }

    public void stop() {
        running = false;
        try {
            if (serverSocket != null) {
                serverSocket.close();
            }
        } catch (IOException ignored) {
            // 忽略
        }
        for (Table t : tables.values()) {
            t.shutdown();
        }
        for (Session s : sessions) {
            s.close();
        }
        Log.info("服务端已停止");
    }
}
