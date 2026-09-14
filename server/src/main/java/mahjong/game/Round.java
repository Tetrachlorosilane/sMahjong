package mahjong.game;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import mahjong.bot.Bot;
import mahjong.core.Meld;
import mahjong.core.Rules;
import mahjong.core.Tiles;
import mahjong.core.Wall;
import mahjong.rules.Agari;
import mahjong.rules.Evaluator;
import mahjong.rules.Payments;
import mahjong.rules.Shanten;
import mahjong.rules.YakuCodes;
import mahjong.rules.WinContext;
import mahjong.util.Json;

import static mahjong.util.Json.intList;

/** 一局（kyoku）的完整状态机：配牌 → 摸切 → 鸣牌 → 和了/流局。 */
public final class Round {

    /** 牌山中可摸的牌数（136 - 14 王牌）。 */
    /**
     * 岭上牌张数 = **一局开杠次数上限**（权威定义在 {@link Wall#RINSHAN_TILES}）。
     *
     * <p>这里保留同名的再导出：它是**规则的名称**，自检与文档都按 `Round.RINSHAN_TILES`
     * 引用它；换成 `Wall.RINSHAN_TILES` 反而让「这条规则归谁管」变得含糊。
     */
    public static final int RINSHAN_TILES = Wall.RINSHAN_TILES;

    public final Table table;
    public final Rules rules;

    public final int roundWind;   // 0=东 1=南 2=西 3=北
    public final int kyoku;       // 1..4
    public final int honba;
    public final int dealer;
    public final int[] scores;
    public int sticks;

    /** 牌山 + 王牌：洗牌、配牌、摸牌、岭上、宝牌的账全在它里面（见 {@link Wall}）。 */
    private final Wall wall;
    /** 上一张打出的牌是否发生在开杠之后（古役「杠振」）。 */
    private boolean kanJustHappened;

    public final List<Integer>[] hand = new List[4];
    public final List<Meld>[] melds = new List[4];
    public final List<Integer>[] discards = new List[4];
    public final boolean[] menzen = new boolean[4];
    public final boolean[] riichi = new boolean[4];
    public final boolean[] doubleRiichi = new boolean[4];
    public final boolean[] ippatsu = new boolean[4];
    public final boolean[] furitenTemp = new boolean[4];
    public final boolean[] furitenPerm = new boolean[4];
    public final int[] playerDraws = new int[4];
    public final int[] discardsSinceRiichi = new int[4];
    public final int[] paoSeat = {-1, -1, -1, -1};
    public final boolean[] hadDiscardCalled = new boolean[4];

    public boolean anyCall;
    public int kanCount;
    public final int[] kanByPlayer = new int[4];
    public int totalDiscards;
    public int lastDiscardSeat = -1;
    public int lastDiscardTile = -1;
    public boolean lastDiscardTsumogiri;

    /**
     * 牌河里需要横置的那张的下标（-1 = 无）。
     * 立直麻将里只有**立直宣言牌**横置；若它被鸣走，横置顺延到该家下一张打出的牌，
     * 再被鸣走就继续顺延，直到某张牌没被鸣走为止。
     */
    private final int[] sidewaysDiscard = {-1, -1, -1, -1};
    /** 横置标记处于「等待下一张打牌」的状态（宣言牌刚被鸣走）。 */
    private final boolean[] sidewaysPending = new boolean[4];

    private final Set<Integer> forbiddenDiscard = new LinkedHashSet<>();
    private long askSeq;

    // ================================================================= 结果

    public static final class Result {
        public boolean agari;
        public boolean abortive;
        public String abortReason = "";
        public final boolean[] tenpai = new boolean[4];
        public final int[] delta = new int[4];
        public int sticksLeft;
        public boolean dealerRenchan;
        public boolean nagashi;
        public int winner = -1;
        public int loser = -1;
        public boolean tsumo;
    }

    @SuppressWarnings("unchecked")
    public Round(Table table, int roundWind, int kyoku, int honba, int dealer, int[] scores, int sticks, long seed) {
        this.table = table;
        this.rules = table.rules;
        this.roundWind = roundWind;
        this.kyoku = kyoku;
        this.honba = honba;
        this.dealer = dealer;
        this.scores = scores.clone();
        this.sticks = sticks;
        // 牌山在构造时就准备好（而不是等 play()）：自检会拿一个没开打过的 Round
        // 去问 canKan() / deadWallLeft()，那时也得有账可查。
        this.wall = new Wall(seed, this.rules);
        for (int i = 0; i < 4; i++) {
            hand[i] = new ArrayList<>();
            melds[i] = new ArrayList<>();
            discards[i] = new ArrayList<>();
            menzen[i] = true;
        }
    }

    // ================================================================= 初始化

    /**
     * 庄家起手多发的那张（第 14 张）。
     *
     * <p>配牌时就把庄家的第 14 张发进手牌了（见 {@link #setup()}），
     * 所以第一巡**不能再摸一张**——否则起手 15 张。这张同时充当「本次摸到的牌」，
     * 让自摸 / 暗杠 / 天和的判定照常可用。
     */
    private int openingTile = -1;

    private void setup() {
        for (int r = 0; r < 13; r++) {
            for (int s = 0; s < 4; s++) {
                hand[(dealer + s) % 4].add(wall.deal());
            }
        }
        // 庄家的第 14 张随配牌一起发出（第一巡不再摸），它同时充当本次摸到的牌
        openingTile = wall.deal();
        hand[dealer].add(openingTile);
        wall.finishDealing();
        sortHands();
    }

    private void sortHands() {
        for (List<Integer> h : hand) {
            h.sort(Round::compareTile);
        }
    }

    private static int compareTile(int a, int b) {
        int ka = Tiles.kind(a);
        int kb = Tiles.kind(b);
        if (ka != kb) {
            return ka - kb;
        }
        boolean ra = Tiles.isRedId(a);
        boolean rb = Tiles.isRedId(b);
        if (ra != rb) {
            return ra ? -1 : 1;
        }
        return a - b;
    }

    // ================================================================= 主循环

    public Result play() {
        setup();
        table.currentRound = this;
        sendRoundStart();

        int turn = dealer;
        boolean needDraw = true;
        // 庄家第一巡：第 14 张已随配牌发出，不能再摸，也不能重复下发 draw 事件
        boolean dealerOpening = true;
        boolean rinshanNext = false;

        while (true) {
            if (table.stopped()) {
                return new Result();
            }
            int drawn = -1;
            boolean isRinshan = false;
            boolean haitei = false;
            if (needDraw) {
                isRinshan = rinshanNext;
                rinshanNext = false;
                final boolean opening = dealerOpening;
                dealerOpening = false;
                if (opening) {
                    // 庄家起手第 14 张：配牌时已入手、客户端也已从 round_start 收到，
                    // 这里只把它当作「本次摸到的牌」交给后续判定。
                    drawn = openingTile;
                } else if (isRinshan) {
                    if (wall.rinshanLeft() <= 0) {
                        return abort("四杠散了");
                    }
                    drawn = wall.drawRinshan();
                } else {
                    if (wall.tilesLeft() <= 0) {
                        return exhaustive();
                    }
                    drawn = wall.draw();
                    haitei = wall.atLastLiveTile();
                }
                if (!opening) {
                    hand[turn].add(drawn);       // 起手那张已经在手里，不能再 add
                }
                playerDraws[turn]++;             // 天和判定需要它为 1
                sortHands();
                if (!opening) {
                    broadcastDraw(turn, drawn, isRinshan);
                }
                furitenTemp[turn] = false;
            }

            // ---------- 询问
            List<Map<String, Object>> opts = turnOptions(turn, drawn, isRinshan);
            Map<String, Object> turnExtra = null;
            if (drawn >= 0 && !hasOption(opts, "tsumo")) {
                String why = winBlockReason(turn, drawn, true);
                if (why != null) {
                    turnExtra = Json.obj("win_note", why);
                }
            }
            Map<String, Object> act = ask(turn, "turn", opts, turnExtra);
            String type = act == null ? "discard" : Json.str(act, "type", "discard");

            if ("tsumo".equals(type) && drawn >= 0) {
                Evaluator.HandScore sc = checkWin(turn, drawn, true, isRinshan, haitei, false, false);
                if (sc != null) {
                    return agariTsumo(turn, drawn, sc);
                }
                type = "discard";
                act = null;
            }
            if ("kyuushu".equals(type)) {
                return abort("九种九牌");
            }
            if ("kan".equals(type)) {
                final int kanBefore = kanCount;
                Result r = turnKan(turn, act, drawn);
                if (r != null) {
                    return r;
                }
                if (kanCount == kanBefore) {
                    // ⚠ 杠**没成立**（牌不在手里 / 动作过期 / 名额已满）：绝不能顺着往下发岭上牌。
                    //   旧写法无条件 `rinshanNext = true`，于是每一条非法的 `kan` 都会从王牌里
                    //   **白抽走一张岭上牌**，还吃掉一次开杠名额 —— `rinshanPos` 跑到了 `kanCount`
                    //   前面，第 4 次真杠反而被挡住（而「一局最多 4 次杠」正是靠这两条闸门守的）。
                    //   与「立直不成立」同一条兜底：退回默认摸切，绝不替玩家打出一张他没选的牌
                    //   （`act.tile` 是他想杠的牌，不是想打的牌）。
                    type = "discard";
                    act = null;
                } else {
                    needDraw = true;
                    rinshanNext = true;
                    continue;
                }
            }

            // ---------- 打牌
            boolean declareRiichi = false;
            boolean badRiichi = false;
            int discardId = -1;
            if ("riichi".equals(type)) {
                int want = resolveTile(act, "tile", turn);
                if (want >= 0 && canRiichi(turn, want)) {
                    declareRiichi = true;
                    discardId = want;
                } else {
                    // ⚠ 立直不成立时**绝不能**把「宣言牌」当普通打牌用（旧代码就是这么退化的）。
                    //   连点立直按钮、或过期回包落到这一巡，都会走到这里；而这条动作里的
                    //   `tile` 是**为立直挑的宣言牌**，不是玩家想打出的牌 —— 打出去等于
                    //   替玩家扔掉一张他自己没选的牌（报障：重复点击立直按钮行为异常）。
                    //   退回默认摸切，与超时代打完全一致。
                    badRiichi = true;
                }
            }
            if (!declareRiichi) {
                if (badRiichi) {
                    discardId = defaultDiscardId(turn, drawn);
                } else {
                    discardId = (act == null) ? -1 : resolveTile(act, "tile", turn);
                    if (discardId < 0 || !hand[turn].contains(discardId)
                            || (drawn >= 0 && riichi[turn] && discardId != drawn)) {
                        discardId = defaultDiscardId(turn, drawn);
                    }
                }
            }
            boolean tsumogiri = drawn >= 0 && discardId == drawn;
            if (declareRiichi) {
                doRiichi(turn, discardId);
            }
            hand[turn].remove((Integer) discardId);
            sortHands();
            final boolean sideways = declareRiichi || sidewaysPending[turn];
            sendDiscard(turn, discardId, tsumogiri, declareRiichi, sideways);
            discards[turn].add(discardId);
            noteDiscard(turn, declareRiichi);
            totalDiscards++;
            lastDiscardSeat = turn;
            lastDiscardTile = discardId;
            lastDiscardTsumogiri = tsumogiri;
            chankanKoyaku = kanJustHappened;
            kanJustHappened = false;
            forbiddenDiscard.clear();
            incRiichiDiscard(turn);

            // ---------- 鸣牌询问
            Claim cl = claimPhase(turn, discardId, declareRiichi);
            if (cl != null && cl.type == ClaimType.RON) {
                return agariRon(cl.multiRon, turn, discardId);
            }
            if (cl == null) {
                if (rules.fourRiichiAbort && riichi[0] && riichi[1] && riichi[2] && riichi[3]) {
                    return abort("四家立直");
                }
                if (rules.fourWindAbort && !anyCall && totalDiscards == 4
                        && discards[0].size() == 1 && discards[1].size() == 1
                        && discards[2].size() == 1 && discards[3].size() == 1) {
                    int k0 = Tiles.kind(discards[0].get(0));
                    if (Tiles.isWind(k0) && Tiles.kind(discards[1].get(0)) == k0
                            && Tiles.kind(discards[2].get(0)) == k0
                            && Tiles.kind(discards[3].get(0)) == k0) {
                        return abort("四风连打");
                    }
                }
                if (fourKanAbortNow()) {
                    return abort("四杠散了");
                }
            }

            if (cl == null) {
                turn = (turn + 1) % 4;
                needDraw = true;
                rinshanNext = false;
                continue;
            }
            // 鸣牌成立
            applyMeld(cl, turn, discardId);
            // `cl.type == KAN` 是**第 4 次杠本身**（大明杠）：那一手还要摸岭上牌，
            // 岭上开花/放铳的豁免可能发生，所以这一刻不能判流局 —— 等那张牌落地再看。
            if (cl.type != ClaimType.KAN && fourKanAbortNow()) {
                return abort("四杠散了");
            }
            if (cl.type == ClaimType.KAN) {
                needDraw = true;
                rinshanNext = true;
            } else {
                needDraw = false;
                rinshanNext = false;
            }
            turn = cl.seat;
        }
    }

    /**
     * 现在是否该判「四杠散了」。
     *
     * <p>规则（`docs/日本麻将.md`「四杠散了」）：一局里**由 2 名及以上玩家开满 4 次杠**、
     * 且**第 4 次杠后取得的岭上牌没有放铳**时强制流局。三种情况**都不流局**：
     * ① 摸到岭上牌成立**岭上开花**；② 第 4 次杠是加杠时被人**抢杠**；③ 岭上牌打出后**放铳**。
     *
     * <p>所以这个判据只在「第 4 次杠之后那张牌已经落地、且没人因它和牌」的时刻问 ——
     * 三种豁免都在和了路径里 `return` 掉了，天然走不到这里。判据本身只回答
     * 「杠数够不够 4、是不是同一个人开的」，**谁和了由调用点保证**。
     *
     * <p>抽成具名判据是为了能脱离牌桌直接断言（构造一局真实的 4 次杠不现实）。
     */
    public boolean fourKanAbortNow() {
        return rules.fourKanAbort && kanCount == 4 && !allKansByOnePlayer();
    }

    private boolean allKansByOnePlayer() {
        for (int s = 0; s < 4; s++) {
            if (kanByPlayer[s] == 4) {
                return true;
            }
        }
        return false;
    }

    /**
     * 记录「某家刚打出一张牌」，并决定这张是否横置。
     *
     * @return 这张牌是否需要横置
     */
    private boolean noteDiscard(int seat, boolean declareRiichi) {
        final boolean sideways = declareRiichi || sidewaysPending[seat];
        if (sideways) {
            sidewaysDiscard[seat] = discards[seat].size() - 1;
            sidewaysPending[seat] = false;
        }
        return sideways;
    }

    /**
     * 某家牌河里第 index 张被鸣走：横置标记要么前移一位，要么顺延到下一张打牌。
     */
    private void noteCalledFromRiver(int from, int index) {
        if (from < 0 || from > 3 || index < 0) {
            return;
        }
        if (sidewaysDiscard[from] == index) {
            sidewaysDiscard[from] = -1;
            sidewaysPending[from] = true;   // 顺延给下一张打出的牌
        } else if (sidewaysDiscard[from] > index) {
            sidewaysDiscard[from]--;
        }
    }

    /** 自测钩子：模拟一次打牌后的横置记录。 */
    public void debugNoteDiscard(int seat, boolean declareRiichi) {
        noteDiscard(seat, declareRiichi);
    }

    /** 自测钩子：模拟一次牌河被鸣走。 */
    public void debugNoteCalledFromRiver(int from, int index) {
        noteCalledFromRiver(from, index);
    }

    /** 该家牌河第 index 张是否横置（供 UI / 自测）。 */
    public boolean isSidewaysDiscard(int seat, int index) {
        return sidewaysDiscard[seat] == index;
    }

    /** 该家是否处于「横置顺延」状态（宣言牌被鸣走、等下一张打牌）。 */
    public boolean isSidewaysPending(int seat) {
        return sidewaysPending[seat];
    }

    private void incRiichiDiscard(int seat) {
        if (riichi[seat]) {
            discardsSinceRiichi[seat]++;
            if (discardsSinceRiichi[seat] >= 2) {
                ippatsu[seat] = false;
            }
        }
    }

    // ================================================================= 事件

    private void sendRoundStart() {
        sortHands();
        for (int s = 0; s < 4; s++) {
            table.send(s, Json.obj(
                    "ev", "round_start",
                    "round", roundJson(),
                    "seat", s,
                    "dealer", dealer,
                    "scores", intList(scores),
                    "hand", tileStrs(hand[s]),
                    "dora_indicators", kindsToStrs(doraIndicators()),
                    "tiles_left", tilesLeft(),
                    "dead_wall_left", deadWallLeft(),
                    "cans", Json.obj("riichi", canRiichiAny(s), "kyuushu", false)));
        }
    }

    private Map<String, Object> roundJson() {
        return Json.obj(
                "bakaze", new String[]{"E", "S", "W", "N"}[roundWind],
                "kyoku", kyoku,
                "honba", honba,
                "riichi_sticks", sticks);
    }

    private void broadcastDraw(int seat, int tile, boolean rinshan) {
        for (int s = 0; s < 4; s++) {
            Map<String, Object> ev = Json.obj(
                    "ev", "draw",
                    "seat", seat,
                    "tiles_left", tilesLeft(),
                    // ⚠ 岭上剩余张数**必须随每次摸牌下发**：杠后这张是从王牌摸的
                    //   （livePos/liveEnd 都不动），所以 `tiles_left` 看不出它少了没有，
                    //   界面上的「岭上 N」只能靠这个字段更新。漏了它 → 整局都显示 4。
                    "dead_wall_left", deadWallLeft(),
                    "rinshan", rinshan);
            if (s == seat) {
                ev.put("tile", Tiles.toStr(tile));
            }
            table.send(s, ev);
        }
    }

    private void sendDiscard(int seat, int tile, boolean tsumogiri, boolean riichiFlag,
                             boolean sideways) {
        table.broadcast(Json.obj(
                "ev", "discard",
                "seat", seat,
                "tile", Tiles.toStr(tile),
                "tsumogiri", tsumogiri,
                "riichi", riichiFlag,
                "riichi_stick", riichiFlag,
                // 只有立直宣言牌（或其顺延牌）横置
                "sideways", sideways));
    }

    private void sendMeld(int seat, Meld m, int calledIndex) {
        List<Object> akas = new ArrayList<>();
        for (int t : m.tiles) {
            akas.add(Tiles.isRedId(t));
        }
        Map<String, Object> ev = Json.obj(
                "ev", "meld",
                "seat", seat,
                "kind", m.kind.wire(),
                "tiles", tileStrs(intArrayToList(m.tiles)),
                "aka", akas,
                "from", m.from,
                "called_tile", Tiles.toStr(m.calledId));
        if (calledIndex >= 0) {
            ev.put("called_index", calledIndex);
        }
        table.broadcast(ev);
    }

    private void sendDora() {
        table.broadcast(Json.obj("ev", "dora_reveal", "dora_indicators", kindsToStrs(doraIndicators())));
    }

    // ================================================================= 工具

    private static List<Integer> intArrayToList(int[] a) {
        List<Integer> l = new ArrayList<>(a.length);
        for (int v : a) {
            l.add(v);
        }
        return l;
    }

    private static List<Object> tileStrs(List<Integer> ids) {
        List<Integer> copy = new ArrayList<>(ids);
        copy.sort(Round::compareTile);
        List<Object> l = new ArrayList<>(copy.size());
        for (int id : copy) {
            l.add(Tiles.toStr(id));
        }
        return l;
    }

    /** kind 列表 → 只含非赤 5 的 id 列表。 */
    private static List<Integer> kindsToIds(List<Integer> kinds) {
        List<Integer> ids = new ArrayList<>();
        for (int k : kinds) {
            ids.add(Tiles.id(k, 1));
        }
        return ids;
    }

    private static List<Object> kindsToStrs(List<Integer> kinds) {
        List<Object> l = new ArrayList<>();
        for (int k : kinds) {
            l.add(Tiles.kindToStr(k));
        }
        return l;
    }

    public List<Integer> doraIndicators() {
        return wall.doraIndicators();
    }

    public List<Integer> uraIndicators() {
        return wall.uraIndicators();
    }

    public int tilesLeft() {
        return wall.tilesLeft();
    }

    /**
     * 王牌里还剩几张岭上牌（{@code 4 - 已摸走的岭上牌数}）。
     *
     * <p>报文里的 `dead_wall_left` 就是它。**每次摸牌都要带**：杠后从岭上摸一张，
     * 这个数就少一张 —— 只在小局开始时报一次的话，界面上的「岭上 N」会整局不动
     * （报障：「杠后岭上牌并没有减少」）。小局开始、重连全量同步也用它。
     */
    public int deadWallLeft() {
        return wall.rinshanLeft();
    }

    /**
     * 本局**还能不能再开杠**：一局最多 4 次。
     *
     * <p>规则的来源是死数：每开一次杠都要从王牌摸一张岭上牌，而王牌里只有
     * {@link #RINSHAN_TILES} 张 —— 所以第 5 次杠**连选项都不该下发**
     * （客户端也就画不出那个按钮）。
     *
     * <p>两个条件都要看，缺一不可：
     * <ul>
     *   <li>{@code rinshanPos < 4}：王牌里还有岭上牌可摸（真正约束规则的那条）；</li>
     *   <li>{@code kanCount < 4}：已经开的杠还没到 4 次。</li>
     * </ul>
     * 正常路径上两者同步递增；分开写是为了**任何一条被拒绝的杠都不会让它们跑偏**
     * （见 {@code play()} 里「杠没成立就退回摸切」的兜底 —— 曾经的写法会让一条
     * 非法的 `kan` 白摸一张岭上牌，从而吃掉一次开杠名额）。
     */
    public boolean canKan() {
        return wall.rinshanLeft() > 0 && kanCount < RINSHAN_TILES;
    }

    /** 供自检：直接把「已摸走的岭上牌数」设成 n，用来验证岭上用完后开杠闸门会关上。 */
    public void debugSetRinshanUsed(int n) {
        wall.debugSetRinshanUsed(n);
    }

    /**
     * 自测钩子：把可摸牌山消耗到只剩 {@code leave} 张。
     *
     * <p>用于「残牌门槛（一般规则 ≥4 张）」与「摸到海底牌之后不可立直」这两条立直条件
     * —— 它们取决于牌山账，不可能靠配牌凑出来。只推进 {@code livePos}，不动任何人的手牌。
     */
    public void debugDrainWallTo(int leave) {
        while (wall.tilesLeft() > leave) {
            wall.draw();
        }
    }

    public int[] concealCounts(int seat) {
        int[] c = new int[Tiles.KIND_COUNT];
        for (int id : hand[seat]) {
            c[Tiles.kind(id)]++;
        }
        return c;
    }

    public Set<Integer> ownDiscardKinds(int seat) {
        Set<Integer> s = new LinkedHashSet<>();
        for (int id : discards[seat]) {
            s.add(Tiles.kind(id));
        }
        return s;
    }

    /**
     * 听牌 kinds 的**记忆化**。
     *
     * <p>`Agari.waits` 要跑 34 次向听 DFS，而一次舍张的仲裁里 `isFuriten()` 会被问 5~8 次
     * （每个候选座位的 `claimOptions`、`win_note`、荣和仲裁），每次都重算是纯浪费 ——
     * 这是每张舍张都走的热路径。
     *
     * <p>键是「暗牌计数 + 副露数」的**精确**摘要（不是哈希：这是参与判分的缓存，
     * 宁可贵一点也绝不赌碰撞）。手牌一变键就变，所以**不需要手工失效** ——
     * 也就不会出现"忘了在某处加一行导致缓存过期"的经典 bug。
     */
    @SuppressWarnings("unchecked")
    private final String[] waitKey = new String[4];
    @SuppressWarnings("unchecked")
    private final List<Integer>[] waitVal = new List[4];

    public List<Integer> waitKinds(int seat) {
        final String key = waitKeyOf(seat);
        final List<Integer> cached = waitVal[seat];
        if (cached != null && key.equals(waitKey[seat])) {
            return cached;
        }
        final List<Integer> v = Agari.waits(concealCounts(seat), melds[seat].size());
        waitKey[seat] = key;
        waitVal[seat] = v;
        return v;
    }

    /** {@link #waitKinds} 的缓存键：暗牌计数（只记非零项）+ 副露数。 */
    private String waitKeyOf(int seat) {
        final int[] c = concealCounts(seat);
        final StringBuilder sb = new StringBuilder(48);
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (c[k] > 0) {
                sb.append(k).append('x').append(c[k]).append(',');
            }
        }
        sb.append('#').append(melds[seat].size());
        return sb.toString();
    }

    public boolean isFuriten(int seat) {
        if (furitenTemp[seat] || furitenPerm[seat]) {
            return true;
        }
        Set<Integer> own = ownDiscardKinds(seat);
        for (int k : waitKinds(seat)) {
            if (own.contains(k)) {
                return true;
            }
        }
        return false;
    }

    /**
     * 立直的门槛条件（与「打哪张能听」无关）。
     *
     * <p>《雀魂》《天凤》：点数 ≥ 1000 且剩余可摸牌 ≥ 4；**M.League 两条都不要求**，
     * 但摸到海底牌之后不能再立直（`rules.riichiNoHaitei`）—— 见 docs/日本麻将.md §立直。
     */
    private boolean riichiAllowed(int seat) {
        if (!menzen[seat] || riichi[seat]) {
            return false;
        }
        if (scores[seat] < rules.riichiMinScore || tilesLeft() < rules.riichiMinTilesLeft) {
            return false;
        }
        return !(rules.riichiNoHaitei && wall.atLastLiveTile());
    }

    private boolean canRiichiAny(int seat) {
        if (!riichiAllowed(seat)) {
            return false;
        }
        for (int id : hand[seat]) {
            if (canRiichi(seat, id)) {
                return true;
            }
        }
        return false;
    }

    public boolean canRiichi(int seat, int tileId) {
        if (!riichiAllowed(seat)) {
            return false;
        }
        if (!hand[seat].contains(tileId)) {
            return false;
        }
        int[] c = new int[Tiles.KIND_COUNT];
        for (int id : hand[seat]) {
            if (id == tileId) {
                continue;
            }
            c[Tiles.kind(id)]++;
        }
        return !Agari.waits(c, melds[seat].size()).isEmpty();
    }

    private int countYaochuKinds(int seat) {
        int[] c = concealCounts(seat);
        int n = 0;
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (c[k] > 0 && Tiles.isYaochu(k)) {
                n++;
            }
        }
        return n;
    }

    // ================================================================= 选项

    /**
     * 和牌被挡住的原因（用于给客户端明确提示）：
     * {@code null} = 没挡住（要么能和、要么本来就不是和了形）；
     * {@code "furiten"} = 是振听所以不能荣和；{@code "no_yaku"} = 和了形但没有役。
     */
    public String winBlockReason(int seat, int winTileId, boolean tsumo) {
        final int[] c = winCounts(seat, winTileId, tsumo);
        if (c == null) {
            return null;                       // 根本不是和了形
        }
        if (!WinCheck.isComplete(c, melds[seat].size())) {
            return null;
        }
        return WinCheck.blockReason(tsumo, isFuriten(seat));
    }
    /** 自测/调试钩子：返回该玩家在该摸牌下可用的动作类型列表。 */
    public List<String> debugTurnOptionTypes(int seat, int drawn) {
        List<String> out = new ArrayList<>();
        for (Map<String, Object> o : turnOptions(seat, drawn, false)) {
            out.add(String.valueOf(o.get("type")));
        }
        return out;
    }

    /** 自测/调试钩子：返回该玩家对某张舍张可用的动作类型列表。 */
    public List<String> debugClaimOptionTypes(int seat, int from, int tileId) {
        List<String> out = new ArrayList<>();
        for (Map<String, Object> o : claimOptions(seat, from, tileId)) {
            out.add(String.valueOf(o.get("type")));
        }
        return out;
    }

    private List<Map<String, Object>> turnOptions(int seat, int drawn, boolean rinshan) {
        List<Map<String, Object>> opts = new ArrayList<>();
        // 可打的牌（纯计算，见 RoundOptions）
        List<Object> discards = RoundOptions.discardChoices(
                hand[seat], riichi[seat], drawn, forbiddenDiscard);
        opts.add(Json.obj("type", "discard", "tiles", discards));

        if (!riichi[seat]) {
            List<Object> riichiTiles = new ArrayList<>();
            Set<String> seen2 = new LinkedHashSet<>();
            for (int id : hand[seat]) {
                if (canRiichi(seat, id) && seen2.add(Tiles.toStr(id))) {
                    riichiTiles.add(Tiles.toStr(id));
                }
            }
            if (!riichiTiles.isEmpty()) {
                opts.add(Json.obj("type", "riichi", "tiles", riichiTiles));
            }
        }
        boolean haitei = wall.atLastLiveTile();
        if (drawn >= 0) {
            Evaluator.HandScore sc = checkWin(seat, drawn, true, rinshan, haitei, false, false);
            if (sc != null) {
                opts.add(Json.obj("type", "tsumo"));
            }
        }
        List<Object> kans = new ArrayList<>();
        int[] c = concealCounts(seat);
        // 岭上牌只有 4 张，用完就不能再开杠（一局最多 4 次，见 canKan）
        if (canKan()) {
            for (int k = 0; k < Tiles.KIND_COUNT; k++) {
                if (c[k] == 4 && kanAllowedByRiichi(seat, k, drawn)) {
                    kans.add(Json.obj("kind", "ankan", "tile", Tiles.kindToStr(k)));
                }
            }
            for (Meld m : melds[seat]) {
                if (m.kind == Meld.Kind.PON && c[Tiles.kind(m.calledId)] > 0) {
                    kans.add(Json.obj("kind", "kakan", "tile", Tiles.kindToStr(Tiles.kind(m.calledId))));
                }
            }
        }
        if (!kans.isEmpty()) {
            opts.add(Json.obj("type", "kan", "kans", kans));
        }
        if (rules.kyuushuAbort && !anyCall && playerDraws[seat] == 1 && countYaochuKinds(seat) >= 9) {
            opts.add(Json.obj("type", "kyuushu"));
        }
        return opts;
    }

    /**
     * 立直后可不可以杠。
     *
     * <p>两条门槛：①「听牌不变」（{@link RoundOptions#kanAllowedAfterRiichi}）；
     * ② M.League 追加的「面子构成不变」（{@link #ankanKeepsShape}）。
     *
     * <p>⚠ 关键在 `drawn`：自己回合里 `hand[seat]` 是 **14 张**（含刚摸到的那张），
     * 而「行杠前的听牌」是**13 张**手牌的听牌。早先这里直接拿 {@code waitKinds(seat)}
     * （对 14 张求听牌）当"杠前听牌" —— 那个集合**恒为空**，于是与杠后听牌永不相等，
     * **立直后的暗杠一次也下发不出来**（静默失效：选项里没有，玩家也就"本来就不能杠"）。
     * 所以必须把刚摸到的那张减掉再算；而传给 {@code kanAllowedAfterRiichi} 的
     * 计数仍是那张 14 张的（它内部要 {@code -= 4} 把这 4 张拿走）。
     */
    /**
     * 该座位能不能对 {@code kind} 这张舍张**大明杠**（手里正好 3 张、名额没满、未立直）。
     *
     * <p>三个入口共用同一个判据：鸣牌选项下发、鸣牌仲裁、{@code applyMeld} 的落地校验。
     * 各写各的就会出现"选项给了但落地崩"或"立直了还能大明杠"。
     */
    public boolean canDaiminkan(int seat, int kind) {
        return kind >= 0 && !riichi[seat] && canKan() && concealCounts(seat)[kind] >= 3;
    }

    private boolean kanAllowedByRiichi(int seat, int kind, int drawn) {
        if (!riichi[seat]) {
            return true;
        }
        final int[] c = concealCounts(seat);
        final int[] before = c.clone();
        if (drawn >= 0) {
            int dk = Tiles.kind(drawn);
            if (before[dk] > 0) {
                before[dk]--;
            }
        }
        final List<Integer> waitsBefore = Agari.waits(before, melds[seat].size());
        if (!RoundOptions.kanAllowedAfterRiichi(waitsBefore, c, melds[seat].size(), kind)) {
            return false;
        }
        // M.League 追加：立直后的暗杠还要求**面子构成不变**（允许役种增减）
        return !rules.ankanKeepsShape || ankanKeepsShape(seat, kind);
    }

    /**
     * M.League 的「面子构成不变」判据（只在立直后、且 `rules.ankanKeepsShape` 时问）。
     *
     * <p>规则原文与 4 个例子见 `docs/日本麻将.md` §立直（2026-09-14 版）：
     * <ul>
     *   <li>{@code 4m5m5m5m2p2p3p3p4p4p6s7s8s} 暗杠 5m → 听牌变化 → 任何规则都不行；</li>
     *   <li>{@code 7m7m2p2p2p3p3p3p4p4p4p6s7s} 暗杠 2p/3p/4p → 听牌不变但面子构成变（平和没了）
     *       → 《雀魂》《天凤》可以，**M.League 不行**；</li>
     *   <li>{@code 1m1m1m2m2m3m3m3m8p8p8p6z6z} 暗杠 1m/3m 不行、暗杠 **8p 可以**（"不会改变牌型"）；</li>
     *   <li>{@code 5m5m5m0m6m7m...} 摸 8m 后暗杠 5m = **送杠**，面子构成变 → 任何规则都不行。</li>
     * </ul>
     *
     * <p>把 4 个例子归纳出来的判据是：**这 4 张牌在手里没有任何同花色的"邻居"**
     * （±1、±2 之内没有同花色的牌）—— 也就是它根本不可能被当成顺子的一部分。
     * 例：8p 旁边没有 6p/7p/9p → 可以；2p 旁边有 3p/4p → 不行。
     *
     * <p>这是**保守**判据：宁可多禁掉个别罕见形状下的暗杠，也绝不放行会改变面子构成的暗杠。
     */
    private boolean ankanKeepsShape(int seat, int kind) {
        if (Tiles.isHonor(kind)) {
            return true;   // 字牌不可能进顺子
        }
        int[] c = concealCounts(seat);
        for (int d = -2; d <= 2; d++) {
            if (d == 0) {
                continue;
            }
            int k = kind + d;
            if (k < 0 || k >= Tiles.KIND_COUNT || Tiles.suit(k) != Tiles.suit(kind)) {
                continue;
            }
            if (c[k] > 0) {
                return false;
            }
        }
        return true;
    }

    /**
     * 代打/兜底出牌：**优先摸切**（打出刚摸到的那张）。
     *
     * <p>超时无人应答时，按「摸切」处理——这也是牌理上最保守的选择，
     * 而且能让客户端通过 {@code tsumogiri} 字段精确还原手牌，不会出现数量对不上。
     */
    private int defaultDiscardId(int seat, int drawn) {
        if (drawn >= 0 && !forbiddenDiscard.contains(Tiles.kind(drawn))) {
            return drawn;
        }
        if (riichi[seat] && drawn >= 0) {
            return drawn;
        }
        for (int id : hand[seat]) {
            if (!forbiddenDiscard.contains(Tiles.kind(id))) {
                return id;
            }
        }
        return drawn >= 0 ? drawn : hand[seat].get(0);
    }

    int resolveTileStr(String s, int seat) {
        int kind = Tiles.parseKind(s);
        if (kind < 0) {
            return -1;
        }
        boolean wantRed = Tiles.isRedStr(s);
        int fallback = -1;
        for (int id : hand[seat]) {
            if (Tiles.kind(id) != kind) {
                continue;
            }
            if (wantRed) {
                if (Tiles.isRedId(id)) {
                    return id;
                }
            } else {
                if (!Tiles.isRedId(id)) {
                    return id;
                }
                fallback = id;
            }
        }
        return fallback;
    }

    private int resolveTile(Map<String, Object> act, String field, int seat) {
        String s = Json.str(act, field, null);
        return s == null ? -1 : resolveTileStr(s, seat);
    }

    // ================================================================= 询问

    /**
     * 向某玩家发起询问。
     *
     * <p>计时规则（国际象棋钟模型）：每巡先给 {@code thinking_base_ms} 的**基本时长**，
     * 这段内出牌不扣额外时长；超出部分从**总额外时长** `timeBankMs` 里扣。
     * 扣减以 1 秒为粒度离散化，**剩余量向上去整**（偏向玩家多给一点）。
     * 鸣牌询问不扣额外时长，用较短的固定窗口。
     */
    private Map<String, Object> ask(int seat, String kind, List<Map<String, Object>> options,
                                    Map<String, Object> extra) {
        long id = ++askSeq;
        final boolean timed = "turn".equals(kind) && rules.thinkingBaseMs > 0;
        final int bank = timed ? Math.max(0, table.seat(seat).timeBankMs) : 0;
        long deadlineMs = timed
                ? (long) rules.thinkingBaseMs + bank
                : Math.max(500, Math.min(rules.thinkingBaseMs > 0 ? rules.thinkingBaseMs
                                                                  : rules.thinkingMs, 12000));
        deadlineMs = Math.max(500, deadlineMs);

        Map<String, Object> ev = Json.obj(
                "ev", "ask",
                "ask_id", id,
                "seat", seat,
                "kind", kind,
                "deadline_ms", deadlineMs,
                "base_ms", rules.thinkingBaseMs,
                "bank_ms", bank,
                "options", options);
        if (extra != null) {
            ev.putAll(extra);
        }
        // 自检旁路：机器人座位的询问不走网络（下面直接 Bot.decide），只能在这里观察选项
        if (table.debugAskTap != null) {
            table.debugAskTap.accept(kind, options);
        }
        final long askedAt = System.currentTimeMillis();
        if (table.seat(seat).bot) {
            sleepBot(seat);
            return Bot.decide(this, seat, kind, options, ev);
        }
        table.send(seat, ev);
        // 只认本次询问真正给过的动作类型：被取消的鸣牌询问的迟到回包
        // （老客户端那条不带 ask_id 的 chi/pon）会被这里挡下，玩家照样拿到完整 deadline。
        java.util.Set<String> allowedTypes = new java.util.HashSet<>();
        for (Map<String, Object> o : options) {
            if (o.get("type") instanceof String) {
                allowedTypes.add((String) o.get("type"));
            }
        }
        Map<String, Object> act = table.awaitAction(seat, id, deadlineMs, allowedTypes);
        if (timed) {
            chargeThinkingTime(seat, System.currentTimeMillis() - askedAt);
        }
        return act;
    }

    /**
     * 结算一次思考对总额外时长的消耗。
     *
     * <p>扣减离散到 1 秒，且**剩余量向上去整**：例如 20s 额外时长下思考 7.3s
     * （基本 5s，超出 2.3s），剩余 17.7s → 记为 18s。
     */
    private void chargeThinkingTime(int seat, long elapsedMs) {
        if (rules.thinkingBaseMs <= 0) {
            return;
        }
        long over = elapsedMs - rules.thinkingBaseMs;
        if (over <= 0) {
            return;                      // 基本时长内，不扣
        }
        long remain = (long) table.seat(seat).timeBankMs - over;
        int newBank;
        if (remain <= 0) {
            newBank = 0;
        } else {
            newBank = (int) (((remain + 999) / 1000) * 1000);   // 向上去整（偏向玩家）
        }
        table.seat(seat).timeBankMs = newBank;
    }

    /** 供自测：直接验证扣减与取整规则。 */
    public void debugChargeThinkingTime(int seat, long elapsedMs) {
        chargeThinkingTime(seat, elapsedMs);
    }

    private void sleepBot(int seat) {
        try {
            Thread.sleep(Math.max(0, table.botDelayMs) + (table.botDelayMs > 0 ? (seat * 53L) % 220 : 0));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void cancelAsk(int seat) {
        table.send(seat, Json.obj("ev", "ask_cancel", "seat", seat));
    }

    private void doRiichi(int seat, int discardTile) {
        riichi[seat] = true;
        if (!anyCall && playerDraws[seat] == 1 && discards[seat].isEmpty()) {
            doubleRiichi[seat] = true;
        }
        ippatsu[seat] = true;
        discardsSinceRiichi[seat] = 0;
        scores[seat] -= 1000;
        sticks++;
        table.broadcast(Json.obj("ev", "riichi", "seat", seat, "stick_index", sticks - 1,
                "sticks", sticks, "scores", intList(scores)));
    }

    // ================================================================= 杠

    /** 自己回合的暗杠 / 加杠。返回非 null 表示本局结束（抢杠荣和）。 */
    private Result turnKan(int seat, Map<String, Object> act, int drawn) {
        if (act == null) {
            return null;
        }
        String kindStr = Json.str(act, "kind", "ankan");
        String tileStr = Json.str(act, "tile", null);
        int kind = Tiles.parseKind(tileStr);
        if (kind < 0) {
            return null;
        }
        List<Integer> picked = new ArrayList<>();
        for (int id : hand[seat]) {
            if (Tiles.kind(id) == kind) {
                picked.add(id);
            }
        }
        if ("ankan".equals(kindStr)) {
            if (picked.size() < 4) {
                return null;
            }
            // ⚠ 选项列表不是安全边界：立直后的暗杠必须在这里**再校验一次**
            //   （听牌不变 + M.League 的面子构成不变）。没兑现就返回 null →
            //   调用方退回默认摸切，绝不白拿一张岭上牌。
            if (!kanAllowedByRiichi(seat, kind, drawn)) {
                return null;
            }
            int[] tiles = new int[4];
            for (int i = 0; i < 4; i++) {
                tiles[i] = picked.get(i);
                hand[seat].remove((Integer) picked.get(i));
            }
            Meld m = new Meld(Meld.Kind.ANKAN, tiles, seat, tiles[0]);
            melds[seat].add(m);
            anyCall = true;
            kanCount++;
            kanByPlayer[seat]++;
            wall.onKan();
            kanJustHappened = true;
            clearIppatsu();
            sendMeld(seat, m, -1);
            revealKanDora();
            return null;
        }
        // 加杠
        Meld target = null;
        for (Meld m : melds[seat]) {
            if (m.kind == Meld.Kind.PON && Tiles.kind(m.calledId) == kind) {
                target = m;
                break;
            }
        }
        if (target == null || picked.isEmpty()) {
            return null;
        }
        int addId = picked.get(0);
        hand[seat].remove((Integer) addId);
        int[] tiles = new int[4];
        System.arraycopy(target.tiles, 0, tiles, 0, 3);
        tiles[3] = addId;
        Meld m = new Meld(Meld.Kind.KAKAN, tiles, target.from, addId);
        melds[seat].set(melds[seat].indexOf(target), m);
        anyCall = true;
        kanCount++;
        kanByPlayer[seat]++;
        wall.onKan();
        kanJustHappened = true;
        clearIppatsu();
        sendMeld(seat, m, -1);
        // 抢杠：**先判抢杠，杠成立之后才翻杠宝牌**。
        // ⚠ 顺序反了会算错分：被抢杠时这次杠并没有成立，对应的宝牌指示牌不能翻开
        //   （docs/日本麻将.md §宝牌：加杠被抢和时，这次杠的宝牌指示牌不翻开）。
        List<Integer> ron = new ArrayList<>();
        for (int d = 1; d < 4; d++) {
            int s = (seat + d) % 4;
            if (isFuriten(s)) {
                continue;
            }
            Evaluator.HandScore sc = checkWin(s, addId, false, false, false, true, false);
            if (sc != null) {
                ron.add(s);
            }
        }
        if (!ron.isEmpty()) {
            return agariRon(ron, seat, addId, true);
        }
        revealKanDora();
        return null;
    }

    private void clearIppatsu() {
        for (int i = 0; i < 4; i++) {
            ippatsu[i] = false;
        }
    }

    private void revealKanDora() {
        if (rules.kanDora) {
            wall.revealDora();
            sendDora();
        }
    }

    // ================================================================= 鸣牌

    private enum ClaimType { RON, PON, CHI, KAN }

    private static final class Claim {
        ClaimType type;
        int seat;
        int[] tiles = new int[0];
        List<Integer> multiRon;
    }

    private Claim claimPhase(int from, int tileId, boolean riichiDiscard) {
        // 同一张舍张的选项**只算一次**：claimOptions 里要跑完整的和了判定 + 振听扫描，
        // 而「谁能鸣」和「给谁发什么选项」要的是同一份结果（这里没有任何状态在两次之间变化）。
        final Map<Integer, List<Map<String, Object>>> optsBySeat = new java.util.HashMap<>();
        List<Integer> eligible = new ArrayList<>();
        for (int d = 1; d < 4; d++) {
            int s = (from + d) % 4;
            List<Map<String, Object>> o = claimOptions(s, from, tileId);
            optsBySeat.put(s, o);
            if (o.size() > 1 || (o.size() == 1 && !"pass".equals(o.get(0).get("type")))) {
                eligible.add(s);
            }
        }
        if (eligible.isEmpty()) {
            return null;
        }
        // 鸣牌询问也走同一套计时：每巡基本时长 + 各自的剩余额外时长。
        // 被问到的每个人各自按自己的时钟算，全局等待取最长者。
        final boolean timedClaim = rules.thinkingBaseMs > 0;
        final long claimNow = System.currentTimeMillis();
        long globalDeadline = claimNow;
        Map<Integer, Long> askedAt = new java.util.HashMap<>();
        Map<Integer, Long> answeredAt = new java.util.HashMap<>();
        Map<Integer, Map<String, Object>> answers = new java.util.HashMap<>();
        List<Integer> asked = new ArrayList<>();
        Set<Integer> ronCapable = new LinkedHashSet<>();
        // 「胡 > 杠 = 碰 > 吃」的**提前收工**依据（见 RoundClaims.shouldStop）：
        // 记下每个仍在等待的座位**最高能做什么**，以及距打牌者几步（同级时近的赢）。
        Map<Integer, Integer> askedBestRank = new java.util.HashMap<>();
        Map<Integer, Integer> seatDist = new java.util.HashMap<>();
        // 每个被问座位**本次询问给过的动作类型** —— 用来挡下不属于本次询问的迟到回包
        Map<Integer, java.util.Set<String>> askedTypes = new java.util.HashMap<>();
        // 已到手的最优鸣牌（按「等级 → 座次」取最优，与实际仲裁顺序同一把尺子）
        RoundClaims.Claim best = null;
        for (int s : eligible) {
            List<Map<String, Object>> opts = optsBySeat.get(s);
            Map<String, Object> extra = Json.obj("from", from, "tile", Tiles.toStr(tileId));
            if (!hasOption(opts, "ron")) {
                String why = winBlockReason(s, tileId, false);
                if (why != null) {
                    extra.put("win_note", why);
                }
            }
            int myBest = RoundClaims.RANK_NONE;
            java.util.Set<String> myTypes = new java.util.HashSet<>();
            for (Map<String, Object> o : opts) {
                if ("ron".equals(o.get("type"))) {
                    ronCapable.add(s);
                }
                if (o.get("type") instanceof String) {
                    myTypes.add((String) o.get("type"));
                }
                myBest = Math.max(myBest, RoundClaims.rankOf((String) o.get("type")));
            }
            askedTypes.put(s, myTypes);
            final int dist = (s - from + 4) % 4;
            seatDist.put(s, dist);
            askedBestRank.put(s, myBest);
            if (table.seat(s).bot) {
                sleepBot(s);
                Map<String, Object> a = Bot.decide(this, s, "claim", opts, extra);
                if (a != null) {
                    answers.put(s, a);
                    final int rank = realizableClaimRank(s, a, tileId);
                    if (RoundClaims.canBeat(best, rank, dist)) {
                        best = new RoundClaims.Claim(rank, dist);
                    }
                }
                continue;
            }
            final int bank = timedClaim ? Math.max(0, table.seat(s).timeBankMs) : 0;
            final long seatDeadlineMs = timedClaim
                    ? (long) rules.thinkingBaseMs + bank
                    : Math.max(500, Math.min(rules.thinkingMs, 12000));
            long id = ++askSeq;
            table.send(s, Json.obj(
                    "ev", "ask", "ask_id", id, "seat", s, "kind", "claim",
                    "deadline_ms", seatDeadlineMs,
                    "base_ms", rules.thinkingBaseMs,
                    "bank_ms", bank,
                    "from", from, "tile", Tiles.toStr(tileId), "options", opts));
            asked.add(s);
            table.pendingAsk.put(s, id);
            askedAt.put(s, claimNow);
            globalDeadline = Math.max(globalDeadline, claimNow + seatDeadlineMs);
        }
        while (!asked.isEmpty()) {
            // 收工判据（都答完 / 荣和者都答了 / **没人能压过已到手的最优** / 超时）
            // —— 纯函数，见 RoundClaims.shouldStop
            long remain = globalDeadline - System.currentTimeMillis();
            if (RoundClaims.shouldStop(asked, ronCapable, answers.keySet(), remain,
                                       best, askedBestRank, seatDist)) {
                break;
            }
            Object[] resp = table.pollResponse(remain);
            if (resp == null) {
                break;
            }
            int s = (Integer) resp[0];
            @SuppressWarnings("unchecked")
            Map<String, Object> msg = (Map<String, Object>) resp[1];
            Object aid = msg.get("ask_id");
            final boolean hasId = aid instanceof Number;
            final long replyId = hasId ? ((Number) aid).longValue() : -1L;
            // 非动作消息（典型：局间残留的 confirm）**不算答复** ——
            // 否则它会被当成「已应答」而静默把鸣牌机会放过（与 awaitAction 同一类坑，见 Table）。
            if (!(msg.get("type") instanceof String)) {
                continue;
            }
            // 类型也必须是本次询问给过的：被取消的那一轮的迟到回包（老客户端不带 ask_id）
            // 会在这里被挡下，而不是当成这个座位的鸣牌决定（否则它会**顶掉**真答复）。
            if (!askedTypes.getOrDefault(s, java.util.Set.of())
                    .contains((String) msg.get("type"))) {
                continue;
            }
            // 认领判据：认错人 / 过期回包都要丢（纯函数，见 RoundClaims）
            if (!RoundClaims.acceptsReply(asked.contains(s), table.pendingAsk.get(s),
                                          hasId, replyId)) {
                continue;
            }
            asked.remove((Integer) s);
            table.pendingAsk.remove(s);
            answers.put(s, msg);
            answeredAt.put(s, System.currentTimeMillis());
            // 高优先级一旦到手，shouldStop 下一轮就会收工 —— 低优先级的**不必再等**
            final int rank = realizableClaimRank(s, msg, tileId);
            if (RoundClaims.canBeat(best, rank, seatDist.getOrDefault(s, Integer.MAX_VALUE))) {
                best = new RoundClaims.Claim(rank, seatDist.getOrDefault(s, Integer.MAX_VALUE));
            }
        }
        for (int s : asked) {
            Long cancelled = table.pendingAsk.get(s);
            cancelAsk(s);
            table.pendingAsk.remove(s);
            // ⚠ 光取消不够：他的回包可能**已经在队列里**（点得不比仲裁慢）。
            //   不摘掉的话，这条没有 ask_id 的动作会被下一次询问 / 下一巡的
            //   awaitAction 当成答复 —— 玩家没动就被代打（见 Table.dropReplies）。
            if (cancelled != null) {
                table.dropReplies(s, cancelled);
            }
        }

        // 鸣牌询问同样消耗额外时长：对每个被问过的真人结算（未应答者算到此刻）
        if (timedClaim) {
            final long end = System.currentTimeMillis();
            for (Map.Entry<Integer, Long> e : askedAt.entrySet()) {
                long until = answeredAt.getOrDefault(e.getKey(), end);
                chargeThinkingTime(e.getKey(), until - e.getValue());
            }
        }

        // 荣和 > 杠 > 碰 > 吃
        List<Integer> ronSeats = new ArrayList<>();
        for (int d = 1; d < 4; d++) {
            int s = (from + d) % 4;
            Map<String, Object> a = answers.get(s);
            if (a != null && "ron".equals(a.get("type")) && !isFuriten(s)) {
                boolean houtei = wall.atLastLiveTile();
                Evaluator.HandScore sc = checkWin(s, tileId, false, false, false, false, houtei);
                if (sc != null) {
                    ronSeats.add(s);
                }
            }
        }
        if (!ronSeats.isEmpty()) {
            if (rules.headBump && ronSeats.size() > 1) {
                ronSeats = new ArrayList<>(ronSeats.subList(0, 1));
            }
            Claim c = new Claim();
            c.type = ClaimType.RON;
            c.seat = ronSeats.get(0);
            c.multiRon = ronSeats;
            return c;
        }
        for (ClaimType t : new ClaimType[]{ClaimType.KAN, ClaimType.PON, ClaimType.CHI}) {
            for (int d = 1; d < 4; d++) {
                int s = (from + d) % 4;
                Map<String, Object> a = answers.get(s);
                if (a == null) {
                    continue;
                }
                String ty = Json.str(a, "type", "pass");
                if (t == ClaimType.KAN && "kan".equals(ty) && canDaiminkan(s, Tiles.kind(tileId))) {
                    Claim c = new Claim();
                    c.type = ClaimType.KAN;
                    c.seat = s;
                    return c;
                }
                if (t == ClaimType.PON && "pon".equals(ty)) {
                    Claim c = new Claim();
                    c.type = ClaimType.PON;
                    c.seat = s;
                    return c;
                }
                if (t == ClaimType.CHI && "chi".equals(ty)) {
                    int[] ids = pickChiTiles(s, tileId, Json.strList(a, "tiles"));
                    if (ids != null) {
                        Claim c = new Claim();
                        c.type = ClaimType.CHI;
                        c.seat = s;
                        c.tiles = ids;
                        return c;
                    }
                }
            }
        }
        return null;
    }

    /**
     * 从手里取出「吃」需要的两张。
     *
     * <p>⚠ 这里必须**自己校验顺子成立**，不能只查"手里有没有这两张"：
     * {@code want} 来自客户端，只查存在性的话，手里有 1m 和 5m 就能把 {@code {1m,3m,5m}}
     * 当成顺子吃下去。而 {@code Evaluator} 是按 {@code Meld.baseKind() + isRun()}
     * **重建**面子形状来算役的（不逐张读 melds 里的牌），于是这副假顺子会被当成
     * 真正的 1m2m3m 计分 —— 役种 / 番数 / 点数全部失真，客户端一条报文就能改分。
     *
     * <p>同时这里曾经只 `for (String s : want)` 往 {@code int[2]} 里写、不查下标：
     * 客户端给三张牌就 ArrayIndexOutOfBoundsException，异常一路冒到牌桌线程，
     * 整场半庄静默死亡（不发 round_end / game_end，四个客户端就一直挂在牌桌上）。
     *
     * @return 两张手牌的 id（长度 2）；任何不合法的情况一律返回 {@code null}（按"没吃"处理）
     */
    private int[] pickChiTiles(int seat, int tileId, List<String> want) {
        if (want == null || want.size() != 2) {
            return null;                    // 多给 / 少给一律作废
        }
        final int called = Tiles.kind(tileId);
        final int k0 = Tiles.parseKind(want.get(0));
        final int k1 = Tiles.parseKind(want.get(1));
        if (k0 < 0 || k1 < 0) {
            return null;
        }
        if (k0 == k1) {
            return null;                    // 两张必须不同（挡"两张 1m 当顺子"）
        }
        if (called >= 27 || k0 >= 27 || k1 >= 27) {
            return null;                    // 字牌不能吃
        }
        if (Tiles.suit(k0) != Tiles.suit(called) || Tiles.suit(k1) != Tiles.suit(called)) {
            return null;                    // 必须同花色
        }
        final int lo = Math.min(called, Math.min(k0, k1));
        final int hi = Math.max(called, Math.max(k0, k1));
        if (hi - lo != 2) {
            return null;                    // 必须恰好连成三张
        }
        int[] ids = new int[2];
        int idx = 0;
        for (int wantKind : new int[]{k0, k1}) {
            int found = -1;
            for (int id : hand[seat]) {
                if (Tiles.kind(id) == wantKind && !containsId(ids, idx, id)) {
                    found = id;
                    break;
                }
            }
            if (found < 0) {
                return null;                // 手里没有（或同一张不能用两次）
            }
            ids[idx++] = found;
        }
        return ids;
    }

    /**
     * 供自检：直接走一遍「吃」的取牌校验（不合法返回 {@code null}）。
     * 见 {@link #pickChiTiles}：顺子成立与否**必须**由服务端自己验，不能只查手里有没有那张。
     */
    public int[] debugPickChiTiles(int seat, int tileId, List<String> want) {
        return pickChiTiles(seat, tileId, want);
    }

    /**
     * 一条鸣牌应答**实际能兑现**的等级（{@code pass} / 兑现不了的吃 记 {@link RoundClaims#RANK_NONE}）。
     *
     * <p>用于「胡 &gt; 杠 = 碰 &gt; 吃」的提前收工：只有**真能兑现**的动作才配当"已到手的最优"，
     * 否则一条非法的吃会白白把后面的机会掐掉。
     * ⚠ 判据必须与下面仲裁循环里的条件**逐字一致**（杠受 {@link #canKan()} 限制、
     * 吃要能真的取出那两张），不然提前收工会改变赢家。
     */
    private int realizableClaimRank(int seat, Map<String, Object> a, int tileId) {
        final String ty = Json.str(a, "type", "pass");
        if ("ron".equals(ty)) {
            return RoundClaims.RANK_RON;
        }
        if ("kan".equals(ty)) {
            return canKan() ? RoundClaims.RANK_KAN : RoundClaims.RANK_NONE;
        }
        if ("pon".equals(ty)) {
            return RoundClaims.RANK_PON;
        }
        if ("chi".equals(ty)) {
            return pickChiTiles(seat, tileId, Json.strList(a, "tiles")) != null
                    ? RoundClaims.RANK_CHI
                    : RoundClaims.RANK_NONE;
        }
        return RoundClaims.RANK_NONE;
    }

    private static boolean hasOption(List<Map<String, Object>> opts, String type) {
        for (Map<String, Object> o : opts) {
            if (type.equals(o.get("type"))) {
                return true;
            }
        }
        return false;
    }

    private static boolean containsId(int[] arr, int len, int v) {
        for (int i = 0; i < len; i++) {
            if (arr[i] == v) {
                return true;
            }
        }
        return false;
    }

    List<Map<String, Object>> claimOptions(int seat, int from, int tileId) {
        List<Map<String, Object>> opts = new ArrayList<>();
        int kind = Tiles.kind(tileId);
        boolean houtei = wall.atLastLiveTile();
        if (!isFuriten(seat)) {
            Evaluator.HandScore sc = checkWin(seat, tileId, false, false, false, false, houtei);
            if (sc != null) {
                opts.add(Json.obj("type", "ron"));
            }
        }
        if (houtei) {
            opts.add(Json.obj("type", "pass"));
            return opts;
        }
        int[] c = concealCounts(seat);
        // 立直后只能荣和，不能鸣牌
        if (riichi[seat]) {
            opts.add(Json.obj("type", "pass"));
            return opts;
        }
        if (c[kind] >= 2) {
            opts.add(Json.obj("type", "pon"));
        }
        // 大明杠：**立直后不可**（它一定会改变手牌构成，而暗杠才可能"听牌不变"）。
        // 立直是门前状态，大明杠还会把 menzen 打掉；任何规则都不允许，所以这里直接挡掉。
        if (canDaiminkan(seat, kind)) {
            opts.add(Json.obj("type", "kan",
                    "kans", Json.arr(Json.obj("kind", "daiminkan", "tile", Tiles.kindToStr(kind)))));
        }
        // 吃：只有下家能吃（见 PROTOCOL），组合枚举是纯计算，见 RoundOptions
        if (seat == (from + 1) % 4) {
            List<Object> sets = RoundOptions.chiSets(c, kind);
            if (!sets.isEmpty()) {
                opts.add(Json.obj("type", "chi", "sets", sets));
            }
        }
        opts.add(Json.obj("type", "pass"));
        return opts;
    }

    private void applyMeld(Claim cl, int from, int tileId) {
        int seat = cl.seat;
        int kind = Tiles.kind(tileId);
        // ⚠ 大明杠要**先校验再改状态**：手里不足 3 张就什么都不做。
        //   旧写法在 case KAN 里直接 `picked.get(0..2)` —— 客户端伪造一条 `type:"kan"`
        //   （或本可被认领的废包）就会 AIOOBE，异常冒到牌桌线程 → 整场半庄静默死亡，
        //   与 `pickChiTiles` 那个审计项是同一类问题。
        List<Integer> kanPicked = null;
        if (cl.type == ClaimType.KAN) {
            kanPicked = new ArrayList<>();
            for (int id : hand[seat]) {
                if (Tiles.kind(id) == kind && kanPicked.size() < 3) {
                    kanPicked.add(id);
                }
            }
            if (kanPicked.size() < 3) {
                return;
            }
        }
        menzen[seat] = false;
        anyCall = true;
        clearIppatsu();
        forbiddenDiscard.clear();
        int calledIndex = -1;
        if (seat < 4 && !discards[from].isEmpty()) {
            calledIndex = discards[from].size() - 1;
            hadDiscardCalled[from] = true;
        }
        switch (cl.type) {
            case CHI: {
                int[] tiles = new int[3];
                tiles[0] = tileId;
                tiles[1] = cl.tiles[0];
                tiles[2] = cl.tiles[1];
                java.util.Arrays.sort(tiles);
                for (int id : cl.tiles) {
                    hand[seat].remove((Integer) id);
                }
                Meld m = new Meld(Meld.Kind.CHI, tiles, from, tileId);
                melds[seat].add(m);
                sendMeld(seat, m, calledIndex);
                removeCalledFromRiver(from, calledIndex);
                if (rules.kuikae) {
                    // ⚠ 这里参与运算的必须是**牌种 kind**，不是牌 id：`tiles[]` 里存的是 id，
                    //   而 `Tiles.suit()` 是按 kind 定义的（kind/9），`forbiddenDiscard` 也是按
                    //   kind 消费的（RoundOptions.discardChoices 里 `contains(Tiles.kind(id))`）。
                    //   以前直接拿 id 做减法和花色判断：`d = a - b` 变成了"两张牌的 copy 之差"，
                    //   于是禁打集合里混进 id、真正的筋替（kind 差 1/2）反而检测不出来，
                    //   下发的出牌选项既漏真禁张又多做无谓禁张（AUDIT F13）。
                    forbiddenDiscard.add(kind);                  // 現物食替：刚吃的那张不能马上打
                    final int[] kinds = {Tiles.kind(tiles[0]), Tiles.kind(tiles[1]), Tiles.kind(tiles[2])};
                    for (int i = 0; i < 3; i++) {
                        for (int j = i + 1; j < 3; j++) {
                            if (kinds[i] == kind || kinds[j] == kind) {
                                continue;                        // 只看"留在手里那两张"
                            }
                            int a = kinds[i];
                            int b = kinds[j];
                            if (a >= 27 || Tiles.suit(a) != Tiles.suit(b)) {
                                continue;                        // 字牌不成顺
                            }
                            int d = a - b;
                            int cand = -1;
                            if (d == -1 || d == 1) {
                                cand = (a < b) ? b + 1 : a + 1;
                            } else if (d == -2) {
                                cand = a + 1;
                            } else if (d == 2) {
                                cand = b + 1;
                            }
                            // 筋替：留下的这两张还能和 cand 配成一副顺子，所以 cand 也不能打
                            if (cand >= 0 && cand < 27 && Tiles.suit(cand) == Tiles.suit(a)) {
                                forbiddenDiscard.add(cand);
                            }
                        }
                    }
                }
                break;
            }
            case PON: {
                List<Integer> picked = new ArrayList<>();
                for (int id : hand[seat]) {
                    if (Tiles.kind(id) == kind && picked.size() < 2) {
                        picked.add(id);
                    }
                }
                for (int id : picked) {
                    hand[seat].remove((Integer) id);
                }
                int[] tiles = {picked.get(0), picked.get(1), tileId};
                java.util.Arrays.sort(tiles);
                Meld m = new Meld(Meld.Kind.PON, tiles, from, tileId);
                melds[seat].add(m);
                sendMeld(seat, m, calledIndex);
                removeCalledFromRiver(from, calledIndex);
                updatePao(seat, from, m);
                if (rules.kuikae) {
                    forbiddenDiscard.add(kind);
                }
                break;
            }
            case KAN: {
                List<Integer> picked = kanPicked;
                for (int id : picked) {
                    hand[seat].remove((Integer) id);
                }
                int[] tiles = {picked.get(0), picked.get(1), picked.get(2), tileId};
                java.util.Arrays.sort(tiles);
                Meld m = new Meld(Meld.Kind.DAIMINKAN, tiles, from, tileId);
                melds[seat].add(m);
                sendMeld(seat, m, calledIndex);
                removeCalledFromRiver(from, calledIndex);
                kanCount++;
                kanByPlayer[seat]++;
                wall.onKan();
                kanJustHappened = true;
                updatePao(seat, from, m);
                revealKanDora();
                break;
            }
            default:
                break;
        }
        sortHands();
    }

    /**
     * 鸣牌成立后把被鸣走的那张牌从牌河里**移除**（它被物理移动到副露里）。
     *
     * <p>这同时影响舍张振听：不在牌河里的牌不再导致振听，与真实规则一致。
     *
     * @param from        被鸣牌者座位
     * @param calledIndex 该牌在原牌河中的下标（&lt;0 表示无，如暗杠）
     */
    private void removeCalledFromRiver(int from, int calledIndex) {
        if (calledIndex < 0 || from < 0 || from > 3)
            return;
        if (calledIndex < discards[from].size())
            discards[from].remove(calledIndex);
        discardCalledIndex[from] = calledIndex;
        noteCalledFromRiver(from, calledIndex);
    }

    /** 被鸣走那张牌在原牌河中的下标（-1 表示无）；供 UI/调试参考。 */
    public final int[] discardCalledIndex = {-1, -1, -1, -1};

    /**
     * 自测钩子：只跑一次包牌判定（不动手牌 / 牌河 / 副露）。
     *
     * <p>包牌的三条触发条件（大三元、大四喜、四杠子）都要求"某家副露成立的**那一刻**"，
     * 靠单局状态机凑出来极难（要精确喂出 4 个杠子、4 个风牌…），所以留一个只跑判据的入口。
     */
    public void debugUpdatePao(int seat, int from, Meld m) {
        updatePao(seat, from, m);
    }

    /** 包牌判定：大三元 / 大四喜 / 四杠子的最后一次副露。 */
    private void updatePao(int seat, int from, Meld m) {
        if (!rules.pao || from < 0) {
            return;
        }
        int dragons = 0;
        int winds = 0;
        for (Meld x : melds[seat]) {
            if (x.isRun()) {
                continue;
            }
            int k = x.baseKind();
            if (Tiles.isDragon(k)) {
                dragons++;
            }
            if (Tiles.isWind(k)) {
                winds++;
            }
        }
        // 大三元 / 大四喜：造成第 3 个三元牌 / 第 4 个风牌副露的那家包牌。
        // ⚠ 这里**自然计入暗杠**（暗杠也是 melds 的一项）：M.League 明文「判定大三元、
        //   大四喜的包牌时，也计入已经公开的暗杠」——已经暗杠的三元牌/风牌算进个数，
        //   但暗杠本身不是"他家的舍牌"，所以它不会成为包牌者（`from < 0` 直接返回）。
        if (Tiles.isDragon(m.baseKind()) && dragons == 3) {
            paoSeat[seat] = from;
        }
        if (Tiles.isWind(m.baseKind()) && winds == 4) {
            paoSeat[seat] = from;
        }
        // 四杠子包牌：**只有 M.League** 有，且必须是"由他家的舍牌大明杠完成第 4 个杠"。
        // 所以判据是「这一次副露本身是大明杠」+「含它正好 4 个杠子」。
        if (rules.paoFourKan && m.kind == Meld.Kind.DAIMINKAN) {
            int kans = 0;
            for (Meld x : melds[seat]) {
                if (x.kind == Meld.Kind.ANKAN || x.kind == Meld.Kind.DAIMINKAN
                        || x.kind == Meld.Kind.KAKAN) {
                    kans++;
                }
            }
            if (kans >= 4) {
                paoSeat[seat] = from;
            }
        }
    }

    // ================================================================= 和了

    /**
     * 「和了牌加入之后的暗牌计数」，并校验牌数是否符合和了形张数。
     *
     * <p>实际判断在 {@link WinCheck#counts}（无状态纯函数，可单独单测）。
     * 这里只负责把本局的状态喂进去，保证 {@link #checkWin} 与 {@link #winBlockReason}
     * 用的是同一份前提判断。
     *
     * @return 张数不符时返回 {@code null}
     */
    private int[] winCounts(int seat, int winTileId, boolean tsumo) {
        return WinCheck.counts(concealCounts(seat), melds[seat].size(), winTileId, tsumo);
    }

    private Evaluator.HandScore checkWin(int seat, int winTileId, boolean tsumo, boolean rinshan,
                                         boolean haitei, boolean chankan, boolean houtei) {
        final int winKind = Tiles.kind(winTileId);
        final int[] c = winCounts(seat, winTileId, tsumo);
        if (c == null) {
            return null;
        }
        WinContext ctx = new WinContext();
        ctx.rules = rules;
        ctx.seat = seat;
        ctx.dealerSeat = dealer;
        ctx.roundWind = 27 + roundWind;
        ctx.tsumo = tsumo;
        ctx.riichi = riichi[seat] && !doubleRiichi[seat];
        ctx.doubleRiichi = doubleRiichi[seat];
        ctx.ippatsu = ippatsu[seat] && riichi[seat];
        ctx.chankan = chankan;
        ctx.rinshan = rinshan;
        ctx.haitei = haitei && tsumo;
        ctx.houtei = houtei && !tsumo;
        ctx.tenhou = tsumo && seat == dealer && playerDraws[seat] == 1 && !anyCall;
        ctx.chiihou = tsumo && seat != dealer && playerDraws[seat] == 1 && !anyCall;
        ctx.renhou = !tsumo && seat != dealer && playerDraws[seat] == 0 && !anyCall;
        ctx.tsubame = !tsumo && lastDiscardSeat >= 0 && lastDiscardSeat != seat
                && lastDiscardTile == winTileId && riichi[lastDiscardSeat]
                && discardsSinceRiichi[lastDiscardSeat] == 1;
        ctx.kanburi = !tsumo && chankanKoyaku;
        ctx.winKind = winKind;
        ctx.doraIndicators = doraIndicators();
        ctx.uraIndicators = uraIndicators();
        ctx.allTileIds = allTileIds(seat, winTileId, tsumo);
        ctx.menzen = menzen[seat];
        Evaluator.HandScore s = Evaluator.evaluate(ctx, c, melds[seat], winKind);
        return s.valid ? s : null;
    }

    /** 上一张打出的牌是否是在他家开杠之后（杠振）。 */
    private boolean chankanKoyaku;

    private List<Integer> allTileIds(int seat, int winTileId, boolean tsumo) {
        List<Integer> ids = new ArrayList<>(hand[seat]);
        if (!tsumo) {
            ids.add(winTileId);
        }
        for (Meld m : melds[seat]) {
            for (int t : m.tiles) {
                ids.add(t);
            }
        }
        return ids;
    }

    private int paoBaseFor(int seat, Evaluator.HandScore sc) {
        if (paoSeat[seat] < 0 || !rules.pao) {
            return 0;
        }
        // 《天凤》：包牌涉及**复合后的全部役满得点**；《雀魂》/ M.League 只包被包的那一役
        // （docs/日本麻将.md §包牌：M.League「只涉及被包的役满部分」，《天凤》「全部」；
        //   雀魂的例子也是"只包大四喜部分"，所以它与 M.League 同侧）。
        if (rules.paoCoversAll) {
            return Math.max(0, sc.base);
        }
        int base = 0;
        for (Evaluator.Yaku y : sc.yaku) {
            if (y.yakuman > 0 && ("大三元".equals(y.name) || "大四喜".equals(y.name)
                    || "四杠子".equals(y.name))) {
                base += 8000 * y.yakuman;
            }
        }
        return base;
    }

    private Result agariTsumo(int seat, int tileId, Evaluator.HandScore sc) {
        Result r = new Result();
        r.agari = true;
        r.winner = seat;
        r.loser = -1;
        r.tsumo = true;
        int pao = paoSeat[seat];
        int paoBase = paoBaseFor(seat, sc);
        Payments.Result pay = Payments.compute(sc, seat, -1, dealer, honba, sticks, true, pao, paoBase);
        applyDelta(r, pay.delta);
        r.sticksLeft = 0;
        r.dealerRenchan = RoundScoring.winBy(dealer, seat);
        r.tenpai[seat] = true;
        sendAgari(seat, -1, true, tileId, sc, pay, pao);
        return r;
    }

    private Result agariRon(List<Integer> winners, int from, int tileId) {
        return agariRon(winners, from, tileId, false);
    }

    private Result agariRon(List<Integer> winners, int from, int tileId, boolean chankan) {
        Result r = new Result();
        r.agari = true;
        r.loser = from;
        r.tsumo = false;
        if (winners == null || winners.isEmpty()) {
            r.winner = -1;
            return r;
        }
        r.winner = winners.get(0);
        int sticksLeft = sticks;
        for (int i = 0; i < winners.size(); i++) {
            int w = winners.get(i);
            Evaluator.HandScore sc = checkWin(w, tileId, false, false, false, chankan, wall.atLastLiveTile());
            if (sc == null) {
                continue;
            }
            int pao = paoSeat[w];
            int paoBase = paoBaseFor(w, sc);
            int useSticks = (i == 0) ? sticks : 0;
            Payments.Result pay = Payments.compute(sc, w, from, dealer, honba, useSticks, false, pao, paoBase);
            sticksLeft -= useSticks;
            applyDelta(r, pay.delta);
            sendAgari(w, from, false, tileId, sc, pay, pao);
        }
        r.sticksLeft = Math.max(0, sticksLeft);
        r.dealerRenchan = RoundScoring.winBy(dealer, winners);
        for (int w : winners) {
            r.tenpai[w] = true;
        }
        return r;
    }

    private void applyDelta(Result r, int[] d) {
        for (int i = 0; i < 4; i++) {
            r.delta[i] += d[i];
            scores[i] += d[i];
        }
    }

    private void sendAgari(int winner, int from, boolean tsumo, int tileId,
                           Evaluator.HandScore sc, Payments.Result pay, int pao) {
        boolean showUra = (riichi[winner] || doubleRiichi[winner]) && rules.ura;
        List<Object> yaku = new ArrayList<>();
        for (Evaluator.Yaku y : sc.yaku) {
            // ⚠ 报文里**不发中文**（PROTOCOL §0）：只发 ASCII 码 `code`，
            //   参数化役种（役牌/场风/自风）另带一个 ASCII 牌码 `tile`，
            //   显示文本由客户端查语言文件拼（`Evaluator` 内部仍用中文名，日志/自检可读）。
            Map<String, Object> yj = Json.obj("code", YakuCodes.codeOf(y.name));
            String ytile = YakuCodes.tileOf(y.name);
            if (ytile != null) {
                yj.put("tile", ytile);
            }
            if (y.yakuman > 0) {
                // 役满役按「役满 = 13 番等价」折算番数（PROTOCOL §3.7）。
                // ⚠ 这里**不能发 0**：老客户端会把 han 原样显示成「0 番」，
                //   而役满的量纲根本不是番（结算界面写「n倍役满」）。
                //   逐役 han 之和 == 合计 han（totalHan()），两边保持一致。
                yj.put("han", y.equivalentHan());
                yj.put("yakuman", y.yakuman);
            } else {
                yj.put("han", y.han);
            }
            yaku.add(yj);
        }
        List<Object> meldList = new ArrayList<>();
        for (Meld m : melds[winner]) {
            meldList.add(m.toJson());
        }
        Map<String, Object> ev = Json.obj(
                "ev", "agari",
                "winner", winner,
                "from", tsumo ? -1 : from,
                "tsumo", tsumo,
                "hand", tileStrs(hand[winner]),
                "melds", meldList,
                "winning_tile", Tiles.toStr(tileId),
                "dora_indicators", kindsToStrs(doraIndicators()),
                "ura_indicators", showUra ? kindsToStrs(uraIndicators()) : new ArrayList<>(),
                "yaku", yaku,
                // 合计番数：役满时用 13 × 倍数（HandScore.totalHan()，与逐役之和一致），
                // 不是 0 —— 「0 番」在结算界面会被显示成役满 0 番。
                "han", sc.totalHan(),
                "fu", sc.fu,
                "yakuman", sc.yakuman,
                "dora", sc.dora,
                "ura", sc.ura,
                "aka", sc.aka,
                "limit", YakuCodes.limitOf(sc.limit),
                "base_points", sc.base,
                "score_delta", intList(pay.delta),
                "scores_after", intList(scores),
                "pao", Json.obj("seat", pao));
        table.broadcast(ev);
    }

    // ================================================================= 流局

    private Result exhaustive() {
        Result r = new Result();
        boolean[] tenpai = new boolean[4];
        List<Object> handList = new ArrayList<>();
        for (int s = 0; s < 4; s++) {
            tenpai[s] = RoundScoring.tenpai(concealCounts(s), melds[s].size());
            r.tenpai[s] = tenpai[s];
            handList.add(tenpai[s] ? tileStrs(hand[s]) : null);
        }
        // 流局满贯
        List<Integer> nagashi = new ArrayList<>();
        if (rules.nagashiMangan) {
            for (int s = 0; s < 4; s++) {
                boolean ok = RoundScoring.nagashiEligible(discards[s], hadDiscardCalled[s]);
                if (ok) {
                    nagashi.add(s);
                }
            }
        }
        if (!nagashi.isEmpty()) {
            r.nagashi = true;
            for (int i = 0; i < nagashi.size(); i++) {
                int s = nagashi.get(i);
                int[] d = RoundScoring.nagashiPayments(s, dealer);
                if (i == 0 && sticks > 0) {
                    d[s] += sticks * 1000;
                }
                applyDelta(r, d);
            }
            r.sticksLeft = nagashi.isEmpty() ? sticks : 0;
            r.dealerRenchan = RoundScoring.nagashiBy(dealer, nagashi);
            table.broadcast(Json.obj(
                    "ev", "ryuukyoku",
                    "type", "exhaustive",
                    "reason", YakuCodes.reasonOf("流局满贯"),
                    "nagashi", intList(toIntArray(nagashi)),
                    "tenpai", boolList(tenpai),
                    "hands", handList,
                    "score_delta", intList(r.delta),
                    "scores_after", intList(scores)));
            return r;
        }
        int[] d = Payments.notenPenalty(tenpai, rules.notenPenalty);
        applyDelta(r, d);
        r.dealerRenchan = RoundScoring.exhaustiveBy(dealer, tenpai);
        // 立直棒留到下一局
        r.sticksLeft = sticks;
        table.broadcast(Json.obj(
                "ev", "ryuukyoku",
                "type", "exhaustive",
                "reason", YakuCodes.reasonOf("荒牌流局"),
                "tenpai", boolList(tenpai),
                "hands", handList,
                "nagashi", intList(new int[]{-1, -1, -1, -1}),
                "score_delta", intList(r.delta),
                "scores_after", intList(scores)));
        return r;
    }

    /** 自测钩子：直接跑一次荒牌流局结算（点亮 nagashiMangan 的实局路径）。 */
    public Result debugExhaustive() {
        return exhaustive();
    }

    private static int[] toIntArray(List<Integer> l) {
        int[] a = new int[l.size()];
        for (int i = 0; i < a.length; i++) {
            a[i] = l.get(i);
        }
        return a;
    }

    private static List<Object> boolList(boolean[] b) {
        List<Object> l = new ArrayList<>(b.length);
        for (boolean v : b) {
            l.add(v);
        }
        return l;
    }

    private Result abort(String reason) {
        Result r = new Result();
        r.abortive = true;
        r.abortReason = reason;
        r.sticksLeft = sticks;
        r.dealerRenchan = RoundScoring.abortive();
        table.broadcast(Json.obj(
                "ev", "ryuukyoku",
                "type", "abortive",
                "reason", YakuCodes.reasonOf(reason),
                "tenpai", boolList(new boolean[]{true, true, true, true}),
                "hands", Json.arr(null, null, null, null),
                "nagashi", intList(new int[]{-1, -1, -1, -1}),
                "score_delta", intList(r.delta),
                "scores_after", intList(scores)));
        return r;
    }
}
