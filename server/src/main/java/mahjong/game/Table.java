package mahjong.game;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;

import mahjong.core.Rules;
import mahjong.replay.ReplayRecorder;
import mahjong.replay.ReplayStore;
import mahjong.rules.YakuCodes;
import mahjong.net.Session;
import mahjong.util.Json;
import mahjong.util.Log;

import static mahjong.util.Json.intList;

/**
 * 一张牌桌 = 一个房间。持有 4 个座位、一个游戏线程，串行推进整场半庄。
 *
 * <p>所有状态修改都发生在 {@link #run()} 线程内，因此无需加锁；网络线程只往
 * 队列里投递消息。
 */
public final class Table implements Runnable {

    /** 座位。 */
    public static final class Seat {
        public final int index;
        public Session session;
        public long pid;
        public String name = "";
        public boolean bot;
        public boolean ready;
        public int score;
        /** 剩余总额外思考时长（毫秒）；每半庄开始时重置。 */
        public int timeBankMs;
        // ⚠ 这里曾经有个 per-seat `inbox` 队列，但客户端命令走的是 {@link #submit}→responses。
        //   结果 drainConfirm() 轮询了一个永远为空的队列，confirm 从来没被消费过
        //   （详见 drainConfirm 的注释）。删掉它，从根上避免再有人读错队列。

        Seat(int index) {
            this.index = index;
        }

        public boolean occupied() {
            return bot || session != null;
        }
    }

    public final String id;
    public String name;
    public final Rules rules;
    public final Seat[] seats = new Seat[4];
    public final List<Session> spectators = new CopyOnWriteArrayList<>();

    /** 每个房间的观战人数上限（AUDIT S-19）。 */
    public static final int MAX_SPECTATORS = 8;
    public final ConcurrentHashMap<Integer, Long> pendingAsk = new ConcurrentHashMap<>();
    /**
     * 座位命令队列（客户端 → 牌桌线程）。
     *
     * <p>**有界**：一条连接灌 1 MB 的报文（`{"cmd":"action",…}` 加一堆垃圾字段）会被解析成
     * 几十万个 Map，无界队列等于把这条放大路径直接接到堆上（AUDIT S-19）。溢出时丢掉
     * **这一条**——牌局照常推进（轮到它时该代打就代打），只是那条命令没生效。
     */
    private final BlockingQueue<Object[]> responses = new ArrayBlockingQueue<>(RESPONSE_CAPACITY);
    /** 见 {@link #responses}。 */
    private static final int RESPONSE_CAPACITY = 1024;

    public volatile Round currentRound;
    public volatile boolean playing;
    private volatile boolean stop;
    private Thread thread;
    public long hostPid;
    /**
     * 牌局种子基准。
     *
     * <p>默认取**密码学随机数**：原来是 `System.nanoTime()`，而每局用的是
     * `seedBase, seedBase+1, seedBase+2 …` —— 只要某一局的种子被离线穷举出来
     * （nanoTime 的不确定窗口很小），**后面每一局**的手牌与摸牌顺序就都能推算，
     * 等于开图（AUDIT F5）。
     *
     * <p>自检会显式写死它（`t.seedBase = …`）来复现整场模拟，所以每局种子必须是
     * `seedBase` 的**纯函数**。
     */
    public long seedBase = new java.security.SecureRandom().nextLong();

    /**
     * 每一小局都**重取一次**的种子来源（用户要求：同一房间不能整场只有一条种子链）。
     *
     * <p>原来每局种子是 `mixSeed(seedBase + 局序号)` —— 同一个 `seedBase` 推出来的一整场
     * 是**确定序列**，只要一局的牌山被还原，后面每一局都能顺着推（`mixSeed` 只是打散了
     * 相邻性，没有引入新的熵）。现在每局都从"当前时刻毫秒数"重新起步：
     * 相邻两局的种子不再有可推导的关系。
     *
     * <p>自检要"给定一局种子就能复现那一局"，所以走 {@link #debugDeterministicSeed} 那条岔路
     * （`mixSeed(seedBase + roundIndex)`）—— 两条路径互不干扰，生产环境永远是时刻种子。
     */
    private long roundSeedClock = System.currentTimeMillis();

    /**
     * 自检开关：为真时每局种子退回 `mixSeed(seedBase + 局序号)`，使一整场可复现。
     *
     * <p>模拟类自检（点数守恒 / 岭上账 / 杠上限）的第一件事就是写死 {@link #seedBase}；
     * 换成时刻种子后它们会变成不可复现的随机样本 —— 那是**自检质量**的下降，不是需求的本意。
     */
    public boolean debugDeterministicSeed;

    /** 供自检：读回当前每局种子来源（确认时刻种子确实在推进）。 */
    public long debugRoundSeedClock() {
        return roundSeedClock;
    }

    /** 供自检：验证"同一毫秒开的两局也拿不到同一个种子"。 */
    public long debugNextRoundSeed() {
        return nextRoundSeed();
    }

    /**
     * 取本小局的种子。
     *
     * <p>生产路径：**当前时刻毫秒数**（打散后）与 {@code seedBase} 混合，并在取用后 +1ms ——
     * 于是「同一毫秒内连开两局」也不会撞出同一副牌，而下一场的种子与这一场无关。
     * `seedBase` 是建桌时取的 CSPRNG，保证"两台服务器同一毫秒"也不会同牌。
     */
    private long nextRoundSeed() {
        roundSeedClock += 1;
        if (debugDeterministicSeed) {
            return mixSeed(seedBase + roundSeedIndex++);
        }
        return mixSeed(mixSeed(seedBase) ^ roundSeedClock);
    }

    /** 见 {@link #debugDeterministicSeed}。 */
    private int roundSeedIndex;

    /**
     * 把「基准 + 局序号」打散成真正的种子（SplitMix64 终混）。
     *
     * <p>不打散的话相邻两局只差 1，一旦推出一局就能推出全部；打散之后每局种子
     * 与基准之间没有可用的线性关系，而同样的 `seedBase` 仍然得到同样的整场序列。
     */
    private static long mixSeed(long x) {
        x += 0x9E3779B97F4A7C15L;
        x = (x ^ (x >>> 30)) * 0xBF58476D1CE4E5B9L;
        x = (x ^ (x >>> 27)) * 0x94D049BB133111EBL;
        return x ^ (x >>> 31);
    }

    /**
     * 机器人思考延时 / 每局之间的停顿（毫秒）。
     *
     * <p>默认刻意放慢（机器人像人在想、局间给人看结算的时间）；自测置 0 加速，
     * 服务端的 `--fast` 也是同一套开关（自动化测试用，别在生产局里开）。
     */
    public static volatile long DEFAULT_BOT_DELAY_MS = 800;
    /**
     * 每小局之间的停顿（毫秒）——**在广播 `round_end` 之后、`round_wait` 之前**。
     *
     * <p>用户要求「结算动画时间太短，把每小局结算界面时长延长到 10 秒」：这段时间里
     * 结算弹窗停在屏幕上（客户端的文字还在依次浮现），10 秒后才广播 `round_wait`
     * 让客户端开始倒计时确认。客户端点「确定」可以立刻跳过（`confirm` 提前开下一局）。
     *
     * <p>自测把 {@link #roundDelayMs} 置 0 跑完整场，所以 L1 不受这个值影响；
     * `--fast` 也是同一个开关。
     */
    public static volatile long DEFAULT_ROUND_DELAY_MS = 10000;

    public volatile long botDelayMs = DEFAULT_BOT_DELAY_MS;
    public volatile long roundDelayMs = DEFAULT_ROUND_DELAY_MS;
    /** 房间内已无真人时由 Server 回收。 */
    public volatile Runnable onEmpty;
    private int lastScores[] = new int[4];

    public Table(String id, String name, Rules rules) {
        this.id = id;
        this.name = name;
        this.rules = rules;
        for (int i = 0; i < 4; i++) {
            seats[i] = new Seat(i);
            seats[i].score = rules.startScore;
            lastScores[i] = rules.startScore;
        }
    }

    // ------------------------------------------------------------- 生命周期

    public void start() {
        thread = new Thread(this, "table-" + id);
        thread.setDaemon(true);
        thread.start();
    }

    public void shutdown() {
        stop = true;
        if (thread != null) {
            thread.interrupt();
        }
    }

    public boolean stopped() {
        return stop;
    }

    public Seat seat(int i) {
        return seats[i];
    }

    public int playerCount() {
        int n = 0;
        for (Seat s : seats) {
            if (s.occupied()) {
                n++;
            }
        }
        return n;
    }

    public boolean hasHumans() {
        for (Seat s : seats) {
            if (s.session != null) {
                return true;
            }
        }
        return false;
    }

    public Session[] sessions() {
        List<Session> l = new ArrayList<>();
        for (Seat s : seats) {
            if (s.session != null) {
                l.add(s.session);
            }
        }
        return l.toArray(new Session[0]);
    }

    // ------------------------------------------------------------- IO

    /**
     * 供自检用：每条**出站报文**都额外喂给这个回调（默认 {@code null}，生产路径零开销）。
     *
     * <p>为什么需要它：有些字段「只在某个瞬间发一次」，光看状态没法验证服务端**到底发了什么**。
     * 典型就是 `dead_wall_left`（杠后岭上剩余数）—— 它只体现在报文里，
     * 所以自检必须能抓到整场模拟的真实报文体才能断言（见 {@code SelfTest.rinshanTests}）。
     * 事件照常发给真实 session，这里只是旁路观察。
     */
    public java.util.function.BiConsumer<Integer, Map<String, Object>> debugEventTap;

    /**
     * 供自检用：每次**询问**都把 (kind, options) 交给这个回调（默认 {@code null}）。
     *
     * <p>与 {@link #debugEventTap} 的区别：那个看的是**出站报文**，而**机器人座位的询问根本
     * 不走网络**（{@code Round.ask()} 直接调 {@code Bot.decide}）—— 所以整场机器人模拟里
     * 一条 `ask` 事件都没有，光靠报文看不到"下发了哪些选项"。
     * 「开满 4 次杠之后不许再下发 kan 选项」这条必须在这里才验证得到。
     */
    public java.util.function.BiConsumer<String, java.util.List<Map<String, Object>>> debugAskTap;

    public void send(int seat, Map<String, Object> ev) {
        if (debugEventTap != null) {
            debugEventTap.accept(seat, ev);
        }
        if (replay != null) {
            // 先记后发：记录的先后顺序就是下行的先后顺序（聊天与操作共用一条序号）
            replay.add(seat, ev);
        }
        Session s = seats[seat].session;
        if (s != null) {
            s.send(ev);
        }
    }

    public void broadcast(Map<String, Object> ev) {
        if (debugEventTap != null) {
            debugEventTap.accept(-1, ev);
        }
        if (replay != null) {
            replay.add(-1, ev);
        }
        broadcastRaw(ev);
    }

    /**
     * 只发不记。给「已经手工记过」的报文用（目前只有终局的 `game_end`：它必须
     * **先落盘再下发**，否则客户端一收到结算就点"看回放"会查不到 —— 见 {@link #sendGameEnd}）。
     */
    private void broadcastRaw(Map<String, Object> ev) {
        for (Seat s : seats) {
            if (s.session != null) {
                s.session.send(ev);
            }
        }
        for (Session sp : spectators) {
            sp.send(ev);
        }
    }

    /**
     * 记一条**只给回放看**的条目（不发给任何客户端）。
     *
     * <p>用途：小局边界上的牌山快照。要塞进正常报文就等于每小局给所有客户端多发 136 个牌 id，
     * 而这份信息只有回放需要。
     */
    void replayNote(int to, Map<String, Object> body) {
        if (replay != null) {
            replay.note(to, body);
        }
    }

    /** 当前这一小局的录制器（没有录制时为 null）。 */
    public ReplayRecorder replay() {
        return replay;
    }

    /** 录制器：整场一个（{@code playGame} 开头建、结束时落盘）。未启用回放时为 null。 */
    private ReplayRecorder replay;

    /** 供 {@link Round} 在小局开头记下这一小局的牌山快照（见 {@code Replay.EV_ROUND}）。 */
    void noteRoundWall(String bakaze, int kyoku, int honba, int dealer, int[] wallOrder) {
        if (replay != null) {
            replay.noteRound(bakaze, kyoku, honba, dealer, wallOrder);
        }
    }

    public void submit(int seat, Map<String, Object> msg) {
        if (!responses.offer(new Object[]{seat, msg})) {
            // 有界队列溢出：丢掉**这一条**（见 responses 的说明），绝不无限堆积。
            Log.warn("牌桌 " + id + " 命令队列已满（" + RESPONSE_CAPACITY + "），丢弃座位 " + seat + " 的一条命令");
        }
    }

    public Object[] pollResponse(long timeoutMs) {
        try {
            return responses.poll(Math.max(1, timeoutMs), TimeUnit.MILLISECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        }
    }

    /**
     * 丢弃某座位**针对已取消询问**的答复。
     *
     * <p>高优先级鸣牌一旦成立（或超时兜底），低优先级的询问会被立刻取消
     * （见 {@link Round#claimPhase}）。但玩家的回包可能**已经躺在队列里**了
     * —— 他点得不比仲裁慢，只是优先级低。这条消息既不属于任何新询问，
     * 又因为**没带 ask_id** 会被 {@link RoundClaims#acceptsReply} 的
     * 「按座位认领」放行，于是：
     *
     * <ul>
     *   <li>留着 → 下一次鸣牌询问把它当成该座位的应答（凭空按了碰/吃）；</li>
     *   <li>更常见的是 → 下一巡的 {@link #awaitAction} 把它当成出牌答复，
     *       <b>玩家没动就被代打</b>（与 §2.3-8 的 confirm 漏包同一类症状）。</li>
     * </ul>
     *
     * <p>所以取消询问时必须把这类消息从队列里**摘掉**。判据要**同时**满足：
     * 同一座位、是动作（有 {@code type}）、且 {@code ask_id} 等于被取消的那个
     * **或根本没带 ask_id**（老客户端）。
     *
     * <p>其余消息**按原顺序放回**（不丢、不改内容），与本类的 {@code drainConfirm} 同一套做法。
     * ⚠ 队列无「按条件删除」的 API，只能整体取出再放回；取出与放回之间到达的新消息
     * 会排在放回的那些之前。这一点与既有的 {@code drainConfirm} 一致：游戏逻辑只按
     * 座位与 {@code ask_id} 认领，不依赖到达顺序。
     *
     * @param seat            被取消询问的座位
     * @param cancelledAskId  被取消的那次询问号
     * @return 摘掉的消息条数（便于自检与排查）
     */
    public int dropReplies(int seat, long cancelledAskId) {
        java.util.List<Object[]> keep = new java.util.ArrayList<>();
        int dropped = 0;
        Object[] r;
        while ((r = responses.poll()) != null) {
            boolean drop = false;
            if (((Integer) r[0]) == seat) {
                @SuppressWarnings("unchecked")
                Map<String, Object> m = (Map<String, Object>) r[1];
                Object aid = m.get("ask_id");
                final boolean isAction = m.get("type") instanceof String;
                final boolean sameAsk = (aid instanceof Number)
                        && ((Number) aid).longValue() == cancelledAskId;
                final boolean noAskId = !(aid instanceof Number);
                // 只丢「这个座位的动作」，且确实属于被取消的那次询问
                // （带 ask_id 的对得上，或干脆没带 —— 后者正是老客户端的回包）。
                drop = isAction && (sameAsk || noAskId);
            }
            if (drop) {
                dropped++;
            } else {
                keep.add(r);
            }
        }
        for (Object[] k : keep) {
            responses.offer(k);
        }
        return dropped;
    }

    public Map<String, Object> awaitAction(int seat, long askId, long timeoutMs) {
        return awaitAction(seat, askId, timeoutMs, null);
    }

    /**
     * 同 {@link #awaitAction(int, long, long)}，外加一条**类型校验**：
     * 只认领这次询问真正给过的动作类型。
     *
     * <p>为什么必须有：被取消的鸣牌询问（高优先级鸣牌先成立 → 低优先级家收到 {@code ask_cancel}）
     * 的迟到回包**没法**用 {@code ask_id} 识别 —— 老客户端的回包不带 {@code ask_id}，
     * 而「按座位认领」（见 {@link RoundClaims#acceptsReply}）会把它放行。
     * 它的 type 是 {@code chi}/{@code pon}，而这一巡（turn）根本没给过这些选项，
     * 于是「这个 type 属于本次询问吗」就是唯一可靠的判据：
     *
     * <ul>
     *   <li>类型不属于本次询问 → **丢弃并继续等**（玩家照样拿到完整的 deadline，
     *       不会被一条废包顶掉 —— 否则就是"没动就被代打"，见 AGENTS §2.3-9）；</li>
     *   <li>属于本次询问 → 照常认领（老客户端兼容不受影响）。</li>
     * </ul>
     *
     * @param allowedTypes 本次询问下发过的 {@code option.type}；{@code null} 表示不校验
     */
    public Map<String, Object> awaitAction(int seat, long askId, long timeoutMs,
                                           java.util.Set<String> allowedTypes) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (true) {
            if (seats[seat].bot || seats[seat].session == null) {
                // 掉线转托管：立即按超时处理
                return null;
            }
            long remain = deadline - System.currentTimeMillis();
            if (remain <= 0) {
                return null;
            }
            Object[] r = pollResponse(remain);
            if (r == null) {
                return null;
            }
            int s = (Integer) r[0];
            if (s != seat) {
                continue;
            }
            @SuppressWarnings("unchecked")
            Map<String, Object> m = (Map<String, Object>) r[1];
            Object aid = m.get("ask_id");
            if (aid instanceof Number && ((Number) aid).longValue() != askId) {
                continue;   // 过期回包（上一询的答复）
            }
            // ⚠ 没有 ask_id 时**必须再确认它是个动作**（老客户端允许不带 ask_id 的答复，故不能一律拒）。
            //   曾经直接 return：局间残留的 `confirm`（既没有 ask_id、也没有 type）被当成本巡答复，
            //   出牌循环再把它默认成 "discard" —— 玩家没动就被自动摸切。
            //   症状：**点了结算确认键后，下一局第一巡自动出牌**（那条 confirm 正是下一个 ask 拾到的第一条消息）。
            if (!(aid instanceof Number) && !(m.get("type") instanceof String)) {
                continue;
            }
            // 最后一道闸：类型必须是本次询问给过的。被取消的鸣牌询问那条废包
            // （没有 ask_id 的 chi/pon）就死在这里，而不是被当成本巡的出牌答复。
            if (allowedTypes != null && !allowedTypes.contains((String) m.get("type"))) {
                continue;
            }
            return m;
        }
    }

    // ------------------------------------------------------------- 房间事件

    public void broadcastRoom() {
        List<Object> seatList = new ArrayList<>();
        for (Seat s : seats) {
            if (!s.occupied()) {
                seatList.add(null);
                continue;
            }
            seatList.add(Json.obj(
                    "seat", s.index,
                    "pid", s.pid,
                    "name", s.name,
                    "ready", s.ready,
                    "bot", s.bot,
                    "score", s.score));
        }
        broadcast(Json.obj(
                "ev", "room",
                "id", id,
                "name", name,
                "host", hostPid,
                "playing", playing,
                "rules", rules.toJson(),
                "seats", seatList));
    }

    public int firstEmptySeat() {
        for (Seat s : seats) {
            if (!s.occupied()) {
                return s.index;
            }
        }
        return -1;
    }

    public void addBot(int at) {
        int idx = at >= 0 ? at : firstEmptySeat();
        // `idx >= 4` 也要挡：`{"cmd":"add_bot","seat":99}` 会直接 seats[99] 越界（AUDIT F19）。
        if (idx < 0 || idx >= 4 || seats[idx].occupied()) {
            return;
        }
        Seat s = seats[idx];
        s.bot = true;
        s.pid = 0;
        s.ready = true;
        s.score = rules.startScore;
        int n = 1;
        for (Seat o : seats) {
            if (o.bot && o != s) {
                n++;
            }
        }
        s.name = "CPU-" + n;
    }

    public void removeBot(int idx) {
        if (idx >= 0 && idx < 4 && seats[idx].bot) {
            seats[idx].bot = false;
            seats[idx].pid = 0;
            seats[idx].name = "";
            seats[idx].ready = false;
        }
    }

    /**
     * 房间内已无真人时立即回收：停掉牌局线程并交回 Server 释放。
     *
     * <p>否则 4 个机器人会一直把当前这局（甚至下一局）打完，白烧 CPU。
     */
    public void recycleIfEmpty() {
        if (hasHumans() || stop)
            return;
        stop = true;
        Runnable cb = onEmpty;
        if (cb != null)
            cb.run();
    }

    /** 玩家掉线：游戏中转为机器人代打。 */
    public void onSessionClosed(Session session) {
        spectators.remove(session);
        for (Seat s : seats) {
            if (s.session == session) {
                s.session = null;
                if (playing) {
                    s.bot = true;
                    s.ready = true;
                    s.name = s.name + "(托管)";
                    Log.info("座位 " + s.index + " 掉线，转为机器人");
                } else {
                    s.ready = false;
                }
                broadcastRoom();
                recycleIfEmpty();
                return;
            }
        }
    }

    // ------------------------------------------------------------- 主循环

    @Override
    public void run() {
        int emptyTicks = 0;
        try {
            while (!stop) {
                if (!hasHumans()) {
                    // 给「刚创建还未入座」留出宽限期，避免房间被误回收
                    if (++emptyTicks > 15) {
                        Runnable cb = onEmpty;
                        if (cb != null) {
                            stop = true;
                            cb.run();
                        }
                        return;
                    }
                    Thread.sleep(200);
                    continue;
                }
                emptyTicks = 0;
                if (!readyToStart()) {
                    Thread.sleep(200);
                    continue;
                }
                try {
                    playGame();
                } catch (Exception e) {
                    Log.error("牌局异常", e);
                }
                playing = false;
                for (Seat s : seats) {
                    s.ready = s.bot;
                }
                broadcastRoom();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (Exception e) {
            Log.error("牌桌线程异常", e);
        }
    }

    private boolean readyToStart() {
        for (Seat s : seats) {
            if (!s.occupied()) {
                return false;
            }
            if (!s.ready && !s.bot) {
                return false;
            }
        }
        return true;
    }

    private int lastWind() {
        return "tonpuu".equals(rules.length) ? 0 : 1;
    }

    /** 跑完一整场（自测直接调用）。 */
    public void playGame() {
        playing = true;
        int[] scores = new int[4];
        for (int i = 0; i < 4; i++) {
            scores[i] = rules.startScore;
            seats[i].score = rules.startScore;
            seats[i].timeBankMs = rules.thinkingBankMs;   // 初值；实际每小局开头都会再重置一次
            lastScores[i] = rules.startScore;
        }
        int roundWind = 0;
        int kyoku = 1;
        int honba = 0;
        int dealer = 0;
        int sticks = 0;
        long roundSeed = seedBase;
        int roundIndex = 0;
        // 回放录制：整场一个录制器（未启用回放时是 null，零开销）。
        // 记的是**客户端实际收到的报文**，所以回放与实时对局走同一条渲染路径。
        ReplayStore store = ReplayStore.current();
        if (store != null && store.enabled()) {
            List<String> names = new ArrayList<>(4);
            for (Seat s : seats) {
                names.add(s.name);
            }
            replay = new ReplayRecorder(rules.toJson(), names);
        }

        for (Seat s : seats) {
            send(s.index, Json.obj(
                    "ev", "game_start",
                    "rules", rules.toJson(),
                    "seats", seatInfo(),
                    "replay_id", replay == null ? "" : replay.id(),
                    "round", Json.obj("bakaze", "E", "kyoku", 1, "honba", 0)));
        }
        broadcastRoom();

        boolean gameOver = false;
        while (!stop && !gameOver) {
            // 额外思考时长**每小局重置**（不是每半庄共用）：
            // 一局打完后，四家的总额外时长都回到 rules.thinkingBankMs。
            for (int i = 0; i < 4; i++) {
                seats[i].timeBankMs = rules.thinkingBankMs;
            }
            Round r = new Round(this, roundWind, kyoku, honba, dealer, scores, sticks,
                    nextRoundSeed());
            Round.Result res;
            try {
                res = r.play();
            } catch (Exception e) {
                // 一局里抛异常时**绝不能静默 return**：那样 round_end / game_end 都不发，
                // 四个客户端会一直挂在牌桌上等一个永远不会来的事件（AUDIT F1 的真实现象）。
                // 这里把整场按异常终止收尾，客户端至少能拿到结算、体面退出。
                Log.error("一局异常，按终局收尾", e);
                sendGameEnd(scores);
                saveReplay();
                return;
            }
            currentRound = null;
            scores = r.scores;
            sticks = res.sticksLeft;
            for (int i = 0; i < 4; i++) {
                seats[i].score = scores[i];
            }
            lastScores = scores.clone();
            broadcast(Json.obj(
                    "ev", "round_end",
                    "round", Json.obj(
                            "bakaze", new String[]{"E", "S", "W", "N"}[roundWind],
                            "kyoku", kyoku,
                            "honba", honba,
                            "riichi_sticks", sticks),
                    "scores", intList(scores),
                    "agari", res.agari,
                    "abortive", res.abortive,
                    "reason", YakuCodes.reasonOf(res.abortReason),
                    "renchan", res.dealerRenchan,
                    "game_over", false));
            sleepMs(roundDelayMs);
            // 小局之间：等**所有玩家确认**，或最多等 ROUND_CONFIRM_MS（5 秒）。
            // 两者取先到者，所以四个人都点确认时不必干等满 5 秒。
            awaitRoundConfirm();
            if (stop) {
                return;
            }
            // 击飞
            if (rules.tobi) {
                for (int v : scores) {
                    if (v < 0) {
                        gameOver = true;
                    }
                }
            }
            if (!gameOver) {
                if (res.dealerRenchan) {
                    // 中途流局（九种九牌/四风连打/四家立直/四杠散了）连庄但本场不增加
                    if (!res.abortive) {
                        honba++;
                    }
                    if (rules.agariyame && kyoku == 4 && roundWind == lastWind() && res.agari) {
                        int top = -1;
                        for (int i = 0; i < 4; i++) {
                            top = Math.max(top, scores[i]);
                        }
                        if (scores[dealer] >= top) {
                            gameOver = true;
                        }
                    }
                } else {
                    honba = 0;
                    int nd = (dealer + 1) % 4;
                    int nw = roundWind;
                    int nk;
                    if (nd == 0) {
                        nw = roundWind + 1;
                        nk = 1;
                    } else {
                        nk = nd + 1;
                    }
                    if (nw > lastWind()) {
                        int top = -1;
                        for (int i = 0; i < 4; i++) {
                            top = Math.max(top, scores[i]);
                        }
                        if (rules.westExtension && nw <= 3 && top < rules.returnScore) {
                            roundWind = nw;
                            kyoku = nk;
                            dealer = nd;
                        } else {
                            gameOver = true;
                        }
                    } else {
                        roundWind = nw;
                        kyoku = nk;
                        dealer = nd;
                    }
                }
            }
        }
        // 终局时供托中的立直棒归 1 位，保证点数守恒
        if (sticks > 0) {
            int top = 0;
            for (int i = 1; i < 4; i++) {
                if (scores[i] > scores[top]) {
                    top = i;
                }
            }
            scores[top] += sticks * 1000;
            seats[top].score = scores[top];
            lastScores = scores.clone();
        }
        sendGameEnd(scores);
        saveReplay();
    }

    /**
     * 整场结束：把录制好的记录交给回放库落盘（原子写 + 容量淘汰，见 {@link ReplayStore}）。
     *
     * <p>落盘放在**牌局结束之后**、不在热路径上：牌桌线程只在最后做一次文件写，
     * 不阻塞任何一次摸切。异常一律吞掉并记日志 —— 回放失败绝不能影响对局收尾。
     */
    private void saveReplay() {
        if (replay == null) {
            return;
        }
        ReplayStore store = ReplayStore.current();
        if (store != null) {
            try {
                store.put(replay.finish());
            } catch (RuntimeException e) {
                Log.warn("回放保存失败：" + e.getMessage());
            }
        }
        replay = null;
    }

    private List<Object> seatInfo() {
        List<Object> l = new ArrayList<>();
        for (Seat s : seats) {
            l.add(Json.obj("seat", s.index, "name", s.name, "score", s.score, "bot", s.bot));
        }
        return l;
    }

    private void sendGameEnd(int[] scores) {
        // 精算（点数 → 马点/头名赏 → 精算点数）是纯函数，放在 RoundScoring 里可单独单测：
        // 见 RoundScoring.settle 的注释与 docs/日本麻将.md §精算点数（2026-09-14 版）。
        RoundScoring.Settlement st = RoundScoring.settle(scores, rules);
        List<Object> finalList = new ArrayList<>();
        for (int i = 0; i < 4; i++) {
            int s = st.order[i];
            finalList.add(Json.obj(
                    "seat", s,
                    "name", seats[s].name,
                    "score", scores[s],
                    "uma", Math.round(st.uma[i] * 10) / 10.0,
                    "oka", Math.round(st.oka[i] * 10) / 10.0,
                    "rank", i + 1,
                    "point", Math.round(st.point[s] * 10) / 10.0));
        }
        List<Object> ranking = new ArrayList<>();
        for (int s : st.order) {
            ranking.add(s);
        }
        Map<String, Object> end = Json.obj(
                "ev", "game_end",
                "scores", intList(scores),
                "ranking", ranking,
                "replay_id", replay == null ? "" : replay.id(),
                "final", finalList);
        // ⚠ 顺序：**先记并落盘，再下发**。客户端收到 `game_end` 就会在结算界面上给
        //   「看本局回放」按钮 —— 那一刻回放必须已经能取，否则用户点了就是"找不到该记录"。
        //   （这条顺序在真机 L3 上踩过：先广播后落盘时，`replay_list` 少了刚打完的这一场。）
        if (replay != null) {
            replay.add(-1, end);
            saveReplay();
        }
        broadcastRaw(end);
        Log.info("牌桌 " + id + " 终局：" + java.util.Arrays.toString(scores));
    }

    private static void sleepMs(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    // ================================================================= 局间确认

    /** 小局之间的间隔上限：到点一定开下一局，避免有人挂机卡住整桌。 */
    public static final long ROUND_CONFIRM_MS = 5000;

    /** 本局间已确认的座位。 */
    private final boolean[] roundConfirmed = new boolean[4];

    /**
     * 小局之间等「所有玩家确认」或最多 {@link #ROUND_CONFIRM_MS}。
     *
     * <p>机器人视为立即确认（否则人机对局永远要干等满 5 秒）。
     * 先广播 {@code round_wait} 让客户端显示倒计时/确认按钮。
     */
    private void awaitRoundConfirm() {
        for (int i = 0; i < 4; i++) {
            roundConfirmed[i] = seats[i].bot;
        }
        final long deadline = System.currentTimeMillis() + ROUND_CONFIRM_MS;
        broadcast(Json.obj("ev", "round_wait", "ms", ROUND_CONFIRM_MS));
        while (!stopped() && System.currentTimeMillis() < deadline) {
            drainConfirm();
            if (allConfirmed()) {
                return;
            }
            sleepMs(50);
        }
    }

    /**
     * 收一遍客户端命令队列，取出 {@code confirm}。
     *
     * <p>⚠ 必须从 {@link #submit} **真正写入的那个队列**（{@code responses}）里取。
     * 这里曾经轮询 {@code seats[i].inbox} —— 而全仓没有任何代码往那个队列写过，
     * 于是 {@code confirm} 从来没被消费过：既导致「全员确认就提前开下一局」永远不生效
     * （永远干等满 5 秒），又让它一路留到下一巡，被 {@code awaitAction} 当成出牌答复
     * （症状：点了结算确认键后，下一局第一巡自动出牌）。
     *
     * <p>非 confirm 的消息**放回队列**（不丢，且保持先后顺序），交给 awaitAction 按 ask_id 校验——
     * 局间不该悄悄吞掉玩家命令。
     */
    private void drainConfirm() {
        List<Object[]> keep = new ArrayList<>();
        Object[] r;
        while ((r = responses.poll()) != null) {
            final int s = (Integer) r[0];
            @SuppressWarnings("unchecked")
            final Map<String, Object> m = (Map<String, Object>) r[1];
            if ("confirm".equals(Json.str(m, "cmd", ""))) {
                if (s >= 0 && s < 4) {
                    roundConfirmed[s] = true;
                }
            } else {
                keep.add(r);
            }
        }
        for (Object[] k : keep) {
            responses.offer(k);
        }
    }

    private boolean allConfirmed() {
        for (int i = 0; i < 4; i++) {
            if (!roundConfirmed[i]) {
                return false;
            }
        }
        return true;
    }

    /** 供重连使用的当前状态快照。 */
    public Map<String, Object> stateFor(int seat) {
        Round r = currentRound;
        if (r == null) {
            return Json.obj("ev", "state", "phase", "idle", "scores", intList(lastScores));
        }
        List<Object> meldsAll = new ArrayList<>();
        List<Object> discardsAll = new ArrayList<>();
        List<Object> riichiAll = new ArrayList<>();
        List<Object> furitenAll = new ArrayList<>();
        for (int s = 0; s < 4; s++) {
            List<Object> ms = new ArrayList<>();
            for (mahjong.core.Meld m : r.melds[s]) {
                ms.add(m.toJson());
            }
            meldsAll.add(ms);
            List<Object> ds = new ArrayList<>();
            for (int id : r.discards[s]) {
                ds.add(mahjong.core.Tiles.toStr(id));
            }
            discardsAll.add(ds);
            riichiAll.add(r.riichi[s]);
            // ⚠ 振听**只回请求者自己那一项**，其余恒 false（旁观者 seat = -1 → 四项全 false）。
            //   临时振听（放过一张能和牌的张）等价于「他听牌了」—— 那是别家不该拿到的信息，
            //   而改造过的客户端只要反复 rejoin 就能把这个快照刷出来。
            //   客户端本来也只读自己那一项（TableModel::furiten(mySeat)），数组长度仍保持 4。
            furitenAll.add(s == seat && r.isFuriten(s));
        }
        List<Object> dora = new ArrayList<>();
        for (int k : r.doraIndicators()) {
            dora.add(mahjong.core.Tiles.kindToStr(k));
        }
        return Json.obj(
                "ev", "state",
                "phase", "playing",
                "seat", seat,
                "round", Json.obj(
                        "bakaze", new String[]{"E", "S", "W", "N"}[r.roundWind],
                        "kyoku", r.kyoku,
                        "honba", r.honba,
                        "riichi_sticks", r.sticks),
                "scores", intList(r.scores),
                "hand", handStrs(r, seat),
                "melds", meldsAll,
                "discards", discardsAll,
                "riichi", riichiAll,
                "furiten", furitenAll,
                "dora_indicators", dora,
                "tiles_left", r.tilesLeft(),
                "dead_wall_left", r.deadWallLeft());
    }

    private List<Object> handStrs(Round r, int seat) {
        List<Object> l = new ArrayList<>();
        if (seat < 0 || seat > 3) {
            return l;
        }
        List<Integer> h = new ArrayList<>(r.hand[seat]);
        h.sort(Comparator.comparingInt(mahjong.core.Tiles::kind));
        for (int id : h) {
            l.add(mahjong.core.Tiles.toStr(id));
        }
        return l;
    }
}
