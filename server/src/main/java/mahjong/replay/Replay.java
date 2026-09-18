package mahjong.replay;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import mahjong.util.Json;

/**
 * 一场**半庄**的完整记录（内存形态；落盘格式见 {@link ReplayStore}）。
 *
 * <p>记录的是**客户端实际收到的报文**（{@code ev} + 原样的 body，外加收件座位 {@code to}），
 * 而不是另搞一套"回放专用事件"。这样做有两个好处：
 * <ul>
 *   <li>回放 = 把同一串事件按顺序喂给客户端的 {@code TableModel}，与实时对局**同一条代码路径**
 *       —— 不会出现"实时能看、回放拼不出来"的信息；</li>
 *   <li>以后新增事件类型时，回放**自动**支持（除非它带隐藏信息，那种要单独处理，见
 *       {@link #HIDDEN}）。</li>
 * </ul>
 *
 * <p>稳定顺序：每条 entry 有单调递增的 {@link Entry#seq}，**聊天与操作共用同一条序号**
 * （聊天也走 {@code Table.broadcast}），所以"某句话发生在第几步之后"是确定的
 * —— 客户端按 seq 切分即可（需求里的"以玩家操作分割"就是这么落地的）。
 */
public final class Replay {

    /** 小局边界条目名：它带着这一小局的**牌山快照**（136 张，按抓牌顺序）。 */
    public static final String EV_ROUND = "replay_round";

    /**
     * 记录时**要丢掉**的事件：它们要么是可由事件流重建的派生快照，要么是牌桌之外的噪音。
     *
     * <ul>
     *   <li>{@code state} —— 重连/旁观快照。它本来就只是"当前状态"的另一种表达，
     *       记下来会让体积翻几倍，而回放从事件流重建更准；</li>
     *   <li>{@code rooms} / {@code room} —— 大厅与房间列表状态，与牌局内容无关
     *       （房间名与规则已经在 meta 里有一份）；</li>
     *   <li>{@code error} —— 与牌局无关的请求错误（例如重放 ID 打错）。</li>
     * </ul>
     */
    public static final java.util.Set<String> SKIP =
            java.util.Set.of("state", "rooms", "room", "error");

    /**
     * 带隐藏信息的事件：回放是**上帝视角**（四家手牌、牌山顺序都可见），但这些事件里的
     * 字段是"只发给某一家"的。回放接口按 {@link Entry#to} 原样给出，客户端做上帝视角时
     * 需要自己合并 —— 这里只登记，便于以后加断言/文档。
     */
    public static final java.util.Set<String> HIDDEN =
            java.util.Set.of("round_start", "draw", "ask", "win_note");

    /** 一条记录：收件座位 + 报文 body（与实时下行完全同形）。 */
    public static final class Entry {
        public final int seq;
        /** 相对开局的时间（毫秒），用于展示"这一步花了多久"。 */
        public final long t;
        /** 收件座位；{@code -1} = 广播（含聊天）。 */
        public final int to;
        public final Map<String, Object> body;

        public Entry(int seq, long t, int to, Map<String, Object> body) {
            this.seq = seq;
            this.t = t;
            this.to = to;
            this.body = body;
        }

        public String ev() {
            return Json.str(body, "ev", "");
        }

        public Map<String, Object> toJson() {
            return Json.obj("seq", seq, "t", t, "to", to, "b", body);
        }

        public static Entry fromJson(Map<String, Object> m) {
            return new Entry(Json.i(m, "seq", 0), Json.l(m, "t", 0), Json.i(m, "to", -1),
                    Json.map(m, "b"));
        }
    }

    public final String id;
    public final long created;
    public final Map<String, Object> rules;
    public final List<String> names;
    /** 每个小局的牌山（136 张 id，**按抓牌顺序**：0..51 配牌、52.. 可摸牌山、末尾 14 张王牌）。 */
    public final List<int[]> walls;
    /** 每个小局在 {@link #entries} 里的起始下标（与该小局的 {@code replay_round} 条目同号）。 */
    public final List<Integer> rounds;
    public final List<Entry> entries;
    /** 超出容量上限被截断过（后面的操作没记）。 */
    public boolean truncated;
    /** 落盘后的字节数（未落盘时为 0）。 */
    public long bytes;
    /**
     * 这一场属于哪个**房间**（用户要求：房间保存牌谱 —— 有了房间号，大厅的回放列表
     * 就能按房间找"刚才那一桌打的那几场"，而不只是看到四个玩家名）。
     * 老记录里没有这个字段，读出时为空串（向前兼容）。
     */
    public String roomId = "";

    public Replay(String id, long created, Map<String, Object> rules, List<String> names) {
        this.id = id;
        this.created = created;
        this.rules = rules == null ? Json.obj() : rules;
        this.names = names == null ? new ArrayList<>() : names;
        this.walls = new ArrayList<>();
        this.rounds = new ArrayList<>();
        this.entries = new ArrayList<>();
    }

    /** 元信息（列表接口用，**不含**操作与牌山 —— 列表要小、要快）。 */
    public Map<String, Object> metaJson() {
        return Json.obj(
                "id", id,
                "created", created,
                "names", names,
                "room", roomId,
                "preset", Json.str(rules, "preset", "custom"),
                "rounds", rounds.size(),
                "entries", entries.size(),
                "bytes", bytes,
                "truncated", truncated);
    }

    /** 完整头（取单场回放时回给客户端的那一份）。 */
    public Map<String, Object> headerJson() {
        Map<String, Object> m = metaJson();
        m.put("rules", rules);
        List<Object> ws = new ArrayList<>(walls.size());
        for (int[] w : walls) {
            ws.add(Json.intList(w));
        }
        m.put("walls", ws);
        m.put("round_at", new ArrayList<Object>(rounds));
        return m;
    }

    /** 落盘用：头一行。 */
    public Map<String, Object> fileHeaderJson() {
        return headerJson();
    }

    /** 按小局切分：第 i 个小局的条目区间 {@code [rounds[i], rounds[i+1])}。 */
    public int roundOf(int entryIndex) {
        int r = 0;
        for (int i = 0; i < rounds.size(); i++) {
            if (rounds.get(i) <= entryIndex) {
                r = i;
            } else {
                break;
            }
        }
        return r;
    }

    public int count() {
        return entries.size();
    }

    /**
     * 从 {@code from} 取 {@code count} 条（分块取，客户端不必一次性拉完整场）。
     *
     * <p>越界一律钳制而不是报错：客户端翻页时正好赶上最后一页是常态。
     */
    public List<Object> sliceJson(int from, int count) {
        int lo = Math.max(0, Math.min(from, entries.size()));
        int hi = Math.max(lo, Math.min(lo + Math.max(0, count), entries.size()));
        List<Object> out = new ArrayList<>(hi - lo);
        for (int i = lo; i < hi; i++) {
            out.add(entries.get(i).toJson());
        }
        return out;
    }

    /** 牌山的**形状**：4 行 × 34 列（{@code index -> row/col}，见 PROTOCOL §3.11）。 */
    public static int wallRow(int index) {
        return index % 4;
    }

    public static int wallCol(int index) {
        return index / 4;
    }

    /** 供自检：把整场按"牌 → 去处"重建一遍时用的可变状态（见 SelfTest.replayTests）。 */
    public Map<String, Object> statsJson() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("entries", entries.size());
        m.put("rounds", rounds.size());
        m.put("walls", walls.size());
        m.put("truncated", truncated);
        return m;
    }
}
