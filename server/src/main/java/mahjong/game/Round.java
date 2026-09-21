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
    /**
     * 一条**包牌责任**：`payer` 打出的牌让本家确定了 `yaku` 这一役满。
     *
     * <p>存的是**役种名**而不是"花了多少点"：同一个役的倍数取决于和牌那一刻的规则
     * （大四喜在《雀魂》是 2 倍、M.League 1 倍），到算分时再按 {@code HandScore} 折算。
     */
    public static final class Pao {
        public final int payer;
        public final String yaku;

        public Pao(int payer, String yaku) {
            this.payer = payer;
            this.yaku = yaku;
        }

        @Override
        public String toString() {
            return yaku + "@" + payer;
        }
    }

    /**
     * 每个座位的**包牌责任列表**（按副露成立的先后）。
     *
     * <p>⚠ 这里原来是一个单槽 `int[4] paoSeat`，装不下"两个役满分别由两人包"：
     * 大三元由 A 的舍张鸣成、四杠子由 B 的舍张大明杠完成时，单槽只会留下**最后那一个**，
     * 于是把两份责任都算到最后一家头上（AUDIT S-52）。列表是**有限**的 ——
     * 能被包的役满只有大三元 / 大四喜 / 四杠子三种，实际至多 2 条
     * （大三元与大四喜要 7 个面子，不可能共存）。
     */
    public final List<Pao>[] pao = new List[4];
    public final boolean[] hadDiscardCalled = new boolean[4];

    /**
     * 每座位**曾经打出过**的牌种计数（34 维）—— 舍张振听的判据。
     *
     * <p>⚠ 它**不等于** {@link #discards}：被他家吃/碰/杠走的舍牌会从牌河里**移除**
     * （鸣牌是"移动"不是"复制"，见 AGENTS §2.2 的 `called_index`），但按规则
     * 「**舍张振听**的条件是当前所听的牌中至少有一种是自己曾经打出的牌，**包括听牌前打出的牌，
     * 以及后来被他家吃、碰或杠走的舍牌**」（`docs/日本麻将.md` §振听）——
     * 所以振听必须看这份"曾经打出过"的账，而不是牌河。
     *
     * <p>原来只看牌河，于是「打出去被人碰走的那张」事后能荣和回去（规则漏洞）。
     * 回归：`SelfTest.furitenRuleTests`。
     */
    private final int[][] discardKindsEver = new int[4][Tiles.KIND_COUNT];

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
            pao[i] = new ArrayList<>();
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
        // 配牌按**现实麻将的抓牌顺序**：从庄家起每人一次抓 4 张、共 3 轮（每人 12 张），
        // 然后每人再各抓 1 张补齐 13 张。
        // ⚠ 这段顺序决定「牌山第 k 张给了谁」——回放的牌山视图（PROTOCOL §3.11）直接按它标归属，
        //   所以改这里必须同步改那一段与客户端 `ReplayModel` 的映射。
        for (int r = 0; r < 3; r++) {
            for (int s = 0; s < 4; s++) {
                List<Integer> h = hand[(dealer + s) % 4];
                for (int t = 0; t < 4; t++) {
                    h.add(wall.deal());
                }
            }
        }
        for (int s = 0; s < 4; s++) {
            hand[(dealer + s) % 4].add(wall.deal());
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
                    // ⚠ 这里原来是「岭上余 0 → abort("四杠散了")」—— 那是**死代码且理由码不对**：
                    //   能不能摸岭上由 `canKan()` 的四个闸门先判（rinshanLeft>0 && kanCount<4），
                    //   真到了四杠散了也是"杠之后那张牌落地且没人和"时判（见 fourKanAbortNow），
                    //   跟"岭上没了"不是一回事。留着它只会让后来人以为还有第二条流局判据（AUDIT S-58）。
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
            Map<String, Object> act = ask(turn, "turn", opts, turnExtra, drawn, isRinshan);
            String type = act == null ? "discard" : Json.str(act, "type", "discard");

            if ("tsumo".equals(type) && drawn >= 0) {
                Evaluator.HandScore sc = checkWin(turn, drawn, true, isRinshan, haitei, false, false);
                if (sc != null) {
                    return agariTsumo(turn, drawn, sc);
                }
                type = "discard";
                act = null;
            }
            // 立直振听：**能给自摸却见逃**（走到这里就说明没有和）→ 本局之内不能再荣和。
            //   只在立直家成立 —— 规则把"放弃自摸和"归在立直振听里（docs/日本麻将.md §振听）。
            if (riichi[turn] && drawn >= 0 && hasOption(opts, "tsumo")) {
                furitenPerm[turn] = true;
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
                    // ⚠ 「摸切」必须按客户端下发的 `tsumogiri` 判定，**绝不能靠牌种去猜**。
                    //   两种取牌方式对应的是两张不同的牌 id：
                    //     · 摸切 → 取刚摸到的那一张（`drawn`）；
                    //     · 手切 → 必须在**暗手**里找，找不到才算非法。
                    //   猜法（"牌种等于摸到的牌就是摸切"）在「手里已有 5m、又摸到 5m、
                    //   玩家点的是手里那张」时必然猜错：服务端会去动摸牌位，而客户端按手切
                    //   扣了暗牌 —— 两端手牌从此各差一张，越打越歪（幽灵手牌）。
                    //   赤五与普通五同 kind，更是注定猜不出（0m vs 5m）。
                    final boolean wantTsumogiri = drawn >= 0 && Json.bool(act, "tsumogiri", false);
                    discardId = (act == null) ? -1
                                              : resolveDiscardId(act, turn, wantTsumogiri, drawn);
                    // ⚠ 选项列表**不是安全边界**：客户端可以发一条手工报文声称要打任意一张。
                    //   所以服务端必须自己再判一次（在手里 / 立直后只摸切 / 未食替），
                    //   不合法就退回默认摸切 —— 与「立直不成立」同一条兜底，绝不放行。
                    if (!discardAllowed(hand[turn], riichi[turn], drawn,
                                        forbiddenDiscard, rules.kuikae, discardId)) {
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
            recordDiscard(turn, discardId, declareRiichi);
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
            if (cl != null && cl.abortReason != null) {
                return abort(cl.abortReason);      // 三家和了：中途流局
            }
            if (cl != null && cl.type == ClaimType.RON) {
                // `declareRiichi` = 被荣和的这张就是**本次**宣言的立直牌 → 燕返（见 agariRon）
                return agariRon(cl.multiRon, turn, discardId, false, declareRiichi);
            }
            // 四杠散了：**成立即流局**，而且必须在"鸣牌落地"**之前**判
            //（旧实现把它放在 `cl == null` 分支里 → 第 4 次杠的岭上舍张被吃/碰时会先把
            //  鸣牌广播 + 移牌落地、再流局，客户端/回放看到的是"碰完立刻流局"；AUDIT S-58）。
            // 三种豁免（第 4 次杠后岭上开花 / 被抢杠 / 岭上牌放铳）由**和了路径先 return** 天然满足，
            // 所以这里只可能落在"那张牌已经落地、且没人因它和"的时候 —— 见 fourKanAbortNow()。
            if (fourKanAbortNow()) {
                return abort("四杠散了");
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

    /**
     * 自测钩子：往牌河放一张牌（**走与生产完全同一条记账**：牌河 + "曾经打出过" + 横置）。
     *
     * <p>自测要构造"打出去又被鸣走"的局面，而生产路径的那三行代码埋在出牌循环里，
     * 所以这里复用它 —— 两处各写一份的话，记账迟早会漂。
     */
    public void debugPushDiscard(int seat, String code, boolean declareRiichi) {
        recordDiscard(seat, Tiles.id(Tiles.parseKind(code), 0), declareRiichi);
    }

    /** 自测钩子：完整模拟「牌河第 index 张被鸣走」（含移除 + 横置顺延）。 */
    public void debugRemoveCalledFromRiver(int from, int index) {
        removeCalledFromRiver(from, index);
    }

    /** 出牌的**唯一**记账点：牌河 + 曾经打出过（振听）+ 横置。 */
    private void recordDiscard(int seat, int id, boolean declareRiichi) {
        discards[seat].add(id);
        discardKindsEver[seat][Tiles.kind(id)]++;
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
        // 回放：把这一小局的**牌山快照**（136 张，按抓牌顺序）记在事件流最前面。
        // 只给回放看，不发给客户端 —— 正常报文里凭空多 136 个牌 id 是白花的下行。
        table.noteRoundWall(roundWindName(), kyoku, honba, dealer, wallOrder());
        // 庄家起手的第 14 张 = 他"刚摸到"的那张（第一巡不再摸，见 play()）
        lastDrawer = dealer;
        for (int s = 0; s < 4; s++) {
            table.send(s, roundStartEvent(s));
        }
        // 观战者没有座位，`round_start` 是**按座位**发的（带该家的暗牌），所以到不了他们手里。
        // 这里补一份**公开快照**：他们在新一局开局就能把牌桌摆对（点数/场次/宝牌/各家张数）。
        table.sendSpectators(table.stateFor(-1));
    }

    /** 构造座位 {@code s} 的 `round_start` 报文（拆出来是为了让自检能直接断言内容）。 */
    private Map<String, Object> roundStartEvent(int s) {
        Map<String, Object> ev = Json.obj(
                "ev", "round_start",
                "round", roundJson(),
                "seat", s,
                "dealer", dealer,
                "scores", intList(scores),
                "hand", tileStrs(hand[s]),
                "dora_indicators", kindsToStrs(doraIndicators()),
                "tiles_left", tilesLeft(),
                "dead_wall_left", deadWallLeft(),
                "cans", Json.obj("riichi", canRiichiAny(s), "kyuushu", false));
        // 庄家的第 14 张（配牌时就入手、第一巡不再摸）必须**点名**告诉客户端是哪一张。
        // `hand` 是**已排序**的 14 张，客户端从里面挑不出"刚摸到的那张"：它若按"最后一张"
        // 认，就会拿排序最大的那张当摸牌位 —— 与服务端的 `openingTile` 几乎总是不同一张，
        // 于是玩家点摸牌位时 `discard.tsumogiri` 的牌码对不上，服务端打 A、客户端扣 B，
        // 两端手牌张数相同、内容差一张（幽灵手牌）。见 PROTOCOL §3.3 与 §2.2。
        if (s == dealer && openingTile >= 0 && hand[s].contains(openingTile)) {
            ev.put("drawn", Tiles.toStr(openingTile));
        }
        return ev;
    }

    /** 供自检：拿到座位 {@code s} 的 `round_start` 报文（只构造，不发送）。 */
    public Map<String, Object> debugRoundStartEvent(int s) {
        sortHands();
        return roundStartEvent(s);
    }

    /** 供自检：跑一遍配牌 —— 配牌在 {@link #play()} 里，构造函数不配牌。 */
    public void debugSetup() {
        setup();
    }

    /** 供自检：庄家起手多发的那张（第 14 张）。 */
    public int debugOpeningTile() {
        return openingTile;
    }

    private Map<String, Object> roundJson() {
        return Json.obj(
                "bakaze", roundWindName(),
                "kyoku", kyoku,
                "honba", honba,
                "riichi_sticks", sticks);
    }

    private String roundWindName() {
        return new String[]{"E", "S", "W", "N"}[Math.max(0, Math.min(3, roundWind))];
    }

    /**
     * 整副牌山（136 张）的**抓牌顺序**：{@code [0,52)} 是配牌（含庄家第 14 张）、
     * {@code [52,122)} 是可摸牌山、末尾 14 张是王牌（4 岭上 + 5 表宝牌 + 5 里宝）。
     *
     * <p>回放的牌山视图与"这一张是什么时候被谁抓走的"都靠它。
     */
    public int[] wallOrder() {
        return wall.debugAllTiles();
    }

    /**
     * 最后一个「刚摸到牌」的座位（庄家开局第 14 张算他摸到的）——公开信息。
     *
     * <p>谁手里有 14 张是**看得见的**（张数），所以这不算泄密；它的用途是让
     * **半场进入的观战者/重连者**把各家张数一次摆对（客户端按
     * {@code 13 − 3×副露 + (drawn_seat == 该家)} 算张数，见 `TableModel::concealedCount`）。
     */
    public int lastDrawer = -1;

    private void broadcastDraw(int seat, int tile, boolean rinshan) {
        lastDrawer = seat;
        // 公开部分先建好：观战者要看到「谁摸了一张」（否则他们的牌桌整局不动），
        // 但**看不到牌面** —— `tile` 只发给摸牌的那一家。
        Map<String, Object> pub = Json.obj(
                "ev", "draw",
                "seat", seat,
                "tiles_left", tilesLeft(),
                // ⚠ 岭上剩余张数**必须随每次摸牌下发**：杠后这张是从王牌摸的
                //   （livePos/liveEnd 都不动），所以 `tiles_left` 看不出它少了没有，
                //   界面上的「岭上 N」只能靠这个字段更新。漏了它 → 整局都显示 4。
                "dead_wall_left", deadWallLeft(),
                "rinshan", rinshan);
        for (int s = 0; s < 4; s++) {
            Map<String, Object> ev = new java.util.LinkedHashMap<>(pub);
            if (s == seat) {
                ev.put("tile", Tiles.toStr(tile));
            }
            table.send(s, ev);
        }
        table.sendSpectators(pub);
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

    /**
     * 自测 / 训练钩子：把宝牌指示牌**钉死**成指定的牌种（{@code null} = 用牌山真实的那些）。
     *
     * <p>为什么要它：teacher 有两条取舍要吃打点（押し引き的期望值、副露前的价值粗估），
     * 而"宝牌是多少"是**发牌决定的** —— 想让"同一手牌 + 有宝牌 / 没宝牌"成为一个对照实验，
     * 就只能把指示牌钉住，否则断言会跟着发牌运气飘（试过：同一手牌恰好撞上宝牌就红）。
     * 它只影响 {@link #doraIndicators()} 的**读**，不碰牌山、不影响任何规则判定。
     */
    private List<Integer> debugDoraOverride;

    /** 见 {@link #debugDoraOverride}；传空表 = 这一局没有宝牌。 */
    public void debugSetDora(List<Integer> kinds) {
        this.debugDoraOverride = kinds == null ? null : List.copyOf(kinds);
    }

    public List<Integer> doraIndicators() {
        return debugDoraOverride != null ? debugDoraOverride : wall.doraIndicators();
    }

    public List<Integer> uraIndicators() {
        return wall.uraIndicators();
    }

    /**
     * 牌山是否只剩最后一张（本巡即海底 / 本次舍张即河底）。
     *
     * <p>训练接口要用它区分海底摸月 / 河底捞鱼；它本来就是**公开信息**
     * （余牌数通过 {@code draw}' 的 {@code tiles_left} 一直广播），所以暴露不泄漏任何东西。
     */
    public boolean atLastLiveTile() {
        return wall.atLastLiveTile();
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

    /**
     * 自己**曾经**打出的牌种集合 —— 舍张振听的判据。
     *
     * <p>⚠ 含**被他家吃/碰/杠走**的舍牌（它们已从 {@link #discards} 移除，见
     * {@link #discardKindsEver}）。所以这个集合**不是**牌河的镜像：牌河用于显示与
     * `called_index`，这个集合用于振听。
     */
    public Set<Integer> ownDiscardKinds(int seat) {
        Set<Integer> s = new LinkedHashSet<>();
        for (int k = 0; k < Tiles.KIND_COUNT; k++) {
            if (discardKindsEver[seat][k] > 0) {
                s.add(k);
            }
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

    /**
     * 自测/训练钩子：返回自家回合的**完整选项**（不是只有类型）。
     *
     * <p>训练接口的信息集不变式要在"不真的打一局"的前提下验证观测（
     * 见 {@code SelfTest.trainingInterfaceTests}），而观测是从 {@code options} 展开动作空间的
     * —— 所以需要拿到真选项。它只是把私有生成器暴露出来，不做任何额外判定。
     */
    public List<Map<String, Object>> debugTurnOptions(int seat, int drawn) {
        return turnOptions(seat, drawn, false);
    }

    /** 自测/训练钩子：返回鸣牌询问的完整选项。 */
    public List<Map<String, Object>> debugClaimOptions(int seat, int from, int tileId) {
        return claimOptions(seat, from, tileId);
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
        // 岭上牌只有 4 张，用完就不能再开杠（一局最多 4 次，见 canKan）。
        // ⚠ 还有一条：**摸到海底牌之后不能再开杠**（`docs/日本麻将.md` §副露 L296
        //   「不可以吃、碰、杠河底牌，摸到海底牌后也不可以开杠」）——
        //   否则可以靠"海底暗杠 → 摸岭上 → 岭上开花"绕开海底的限制，
        //   还会把「一局 4 次杠」的账往后挪。河底牌那侧（大明杠）由鸣牌段自己挡。
        if (canKan() && !haitei) {
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
     * 出牌合法性的**服务端权威判据** —— 与 {@link RoundOptions#discardChoices} 下发的选项
     * 是**同一把尺子**（下发的是它的子集；收包时再用它判一次）。
     *
     * <p>为什么必须再判一次：选项列表只是"给好客户端的提示"，**不是安全边界**。
     * 一条手工报文（或改造过的客户端）可以声称要打任意一张牌；只查「在手里」的话，
     * 「吃 3m 打 6m」「吃 3m 打现物 3m」「立直后打手里别的牌」全都能过，
     * 而 `docs/PROTOCOL.md` §7 明文把「违反食替」列为非法动作、且非法动作不得改变状态。
     *
     * <p>抽成静态纯函数是为了让自检能**不构造牌桌**逐条断言，并且与
     * {@code RoundOptions.discardChoices} 做"接受集合 == 下发集合"的同源性对照。
     *
     * @param hand      该家手牌（牌 id）
     * @param riichi    该家是否已立直（立直后只允许摸切）
     * @param drawn     本巡摸到的牌 id；{@code < 0} 表示本巡没摸牌（鸣牌之后）
     * @param forbidden 禁打的**牌种**集合（食替，见 {@link RoundOptions#kuikaeForbidden}）
     * @param kuikae    {@code rules.kuikae}：关掉食替禁止时该集合不生效
     * @param id        客户端声称要打出的那张牌的 id
     */
    public static boolean discardAllowed(List<Integer> hand, boolean riichi, int drawn,
                                         Set<Integer> forbidden, boolean kuikae, int id) {
        if (id < 0 || !hand.contains(id)) {
            return false;                       // 不在手里（含 -1 "没解析出来"）
        }
        if (riichi && drawn >= 0 && id != drawn) {
            return false;                       // 立直后只能摸切（与旧判据一致）
        }
        return !(kuikae && forbidden.contains(Tiles.kind(id)));
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
        return findByCode(hand[seat], s);
    }

    /**
     * 按**牌码**在一摞牌里找那张：先按 kind 缩小，再按"要不要赤五"精确匹配
     * （`0m` 要赤、`5m` 要普通）；要普通却只有赤五时退回赤五（否则玩家点 `5m` 会变成非法动作）。
     *
     * <p>抽成静态方法是为了让出牌判据（{@link #pickDiscardId}）能被自检直接调用，
     * 不必先把牌桌跑起来。
     */
    static int findByCode(List<Integer> pile, String s) {
        int kind = Tiles.parseKind(s);
        if (kind < 0) {
            return -1;
        }
        boolean wantRed = Tiles.isRedStr(s);
        int fallback = -1;
        for (int id : pile) {
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

    /**
     * 出牌用的**精确**取牌：按客户端下发的 `tile` 牌码取，`tsumogiri` 只决定去哪一摞里找。
     *
     * <p>规则只有一条（见 {@link #pickDiscardId}）：**牌码决定打哪张**。
     * 声明摸切且牌码就是刚摸到的那张 → 取摸牌位那张；其余一律按牌码在暗手里找
     * （刚摸到的那张也在 `hand` 里，照常参与查找）。
     *
     * <p>⚠ 为什么"声明了摸切但牌码对不上"**不能**直接退回默认摸切（旧写法就是那样）：
     * 默认摸切打的是**刚摸到的那张**，而那正是玩家没点的那张牌。庄家第一巡的旧客户端
     * 把**已排序**的 14 张配牌的最后一张当成摸牌位（服务端的 `drawn` 其实是第 14 张
     * `openingTile`，排序后通常在中间），牌码必然对不上 —— 于是服务端打 A、客户端按点击
     * 扣掉 B，两端手牌**张数相同、内容差一张**（幽灵手牌，且不会自愈：下一步又会按
     * "手里有这张吗"去猜，越打越歪）。现在改为按牌码取，服务端打的就一定是玩家点的那张。
     *
     * @param wantTsumogiri 客户端声明的摸切标记（老客户端不带这个字段时为 false）
     * @param drawn         本巡摸到的牌 id；{@code < 0} 表示本巡没有摸牌
     */
    private int resolveDiscardId(Map<String, Object> act, int seat, boolean wantTsumogiri, int drawn) {
        String s = Json.str(act, "tile", null);
        return s == null ? -1 : pickDiscardId(hand[seat], drawn, s, wantTsumogiri);
    }

    /**
     * 出牌取牌的**纯判据**（不依赖牌桌状态，自检可直接调）：返回要打出的牌 id，{@code -1} = 兑现不了。
     *
     * <p>刻意做成静态方法：这条规则是「幽灵手牌」的唯一防线，必须能脱离网络与整场对局
     * 逐条断言（见 `SelfTest.discardAlignTests`）。
     */
    public static int pickDiscardId(List<Integer> hand, int drawn, String code, boolean claimTsumogiri) {
        if (claimTsumogiri && drawn >= 0 && code.equals(Tiles.toStr(drawn))) {
            return drawn;                       // 声明摸切且牌码吻合：铁证，就是摸牌位那张
        }
        return findByCode(hand, code);           // 其余一律按牌码取（含"摸切声明对不上"的情况）
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
                                    Map<String, Object> extra, int drawn, boolean rinshan) {
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
        // 自检旁路：机器人座位的询问不走网络（下面直接走策略），只能在这里观察选项
        if (table.debugAskTap != null) {
            table.debugAskTap.accept(kind, options);
        }
        final long askedAt = System.currentTimeMillis();
        if (table.seat(seat).bot) {
            sleepBot(seat);
            // 训练接口：观测**无条件**构造（生产与训练共用同一段决策路径）
            return table.decideBot(seat, new mahjong.ai.Decision(
                    mahjong.ai.Observation.ofTurn(this, seat, options, drawn, rinshan,
                            Json.str(ev, "win_note", null)),
                    this, kind, options, ev));
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
            // **国士抢暗杠**（《雀魂》，`docs/日本麻将.md` §抢杠 L957：「《雀魂》中，国士无双
            // 可以抢暗杠。《天凤》和 M.League 不允许」）。
            //
            // ⚠ 判在**杠成立之前**：被抢时这次杠整个不成立 —— 那 4 张仍在杠主手里、
            //   不翻杠宝牌、不打断一发（`clearIppatsu()` 在下面）、`kanCount` 也不 +1，
            //   本局直接以荣和收局（与加杠被抢杠走同一套结算）。
            if (rules.kokushiAnkan) {
                List<Integer> rob = chankanRon(seat, picked.get(0), true);
                if (!rob.isEmpty()) {
                    return agariRon(rob, seat, picked.get(0), true, false);
                }
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
        sendMeld(seat, m, -1);
        // 抢杠：**先判抢杠，杠成立之后才翻杠宝牌**。
        // ⚠ 顺序反了会算错分：被抢杠时这次杠并没有成立，对应的宝牌指示牌不能翻开
        //   （docs/日本麻将.md §宝牌：加杠被抢和时，这次杠的宝牌指示牌不翻开）。
        // ⚠ 而**一发**也必须等到"杠真的成立"再打断：文档 §一发 L901「吃、碰、杠（包括暗杠）
        //   都会打断一发。**抢杠发生在加杠成立之前，可以与一发复合**」——
        //   旧实现先 `clearIppatsu()` 再判抢杠，于是抢杠白丢一发（少 1 番；AUDIT S-57①）。
        List<Integer> ron = chankanRon(seat, addId, false);
        if (!ron.isEmpty()) {
            return agariRon(ron, seat, addId, true, false);   // 抢杠：不是燕返
        }
        clearIppatsu();                  // 杠真的成立了，这才打断一发
        revealKanDora();
        return null;
    }

    /**
     * 抢杠的荣和者（按「距杠主由近到远」，含振听过滤）；加杠与暗杠两条路共用。
     *
     * @param kokushiOnly 暗杠时**只认国士无双**（《雀魂》的国士抢暗杠）：
     *                    别的听牌即使能荣和这张牌也不能抢暗杠 —— 那 4 张在杠主手里，
     *                    「等着那张」是合法局面，口子只开给国士。
     */
    private List<Integer> chankanRon(int kanSeat, int tileId, boolean kokushiOnly) {
        List<Integer> ron = new ArrayList<>();
        for (int d = 1; d < 4; d++) {
            int s = (kanSeat + d) % 4;
            if (isFuriten(s)) {
                continue;
            }
            Evaluator.HandScore sc = checkWin(s, tileId, false, false, false, true, false);
            if (sc == null || (kokushiOnly && !isKokushiScore(sc))) {
                continue;
            }
            ron.add(s);
        }
        // 多家抢杠同样受**头跳**约束（文档 §头跳：头跳 / 多家和了同样适用于抢杠；AUDIT S-57②）
        if (rules.headBump && ron.size() > 1) {
            return new ArrayList<>(ron.subList(0, 1));
        }
        return ron;
    }

    /**
     * 这一手是不是**国士无双**（含十三面）。
     *
     * <p>判据取 {@code Agari} 拆出来的和了型，而不是役种名 —— 役种名会随规则改名
     * （不加倍役满时十三面仍叫十三面，见 `Evaluator`），牌型不会。
     */
    private static boolean isKokushiScore(Evaluator.HandScore sc) {
        return sc.form != null && sc.form.type == Agari.TYPE_KOKUSHI;
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
        /**
         * 客户端为**碰 / 大明杠**指定的精确牌码（用于副露赤宝选择，用户要求）。
         *
         * <p>形如 `["0m","5m"]`：写 `0m` = 用赤五、写 `5m` = 用普通五。为空 = 老客户端，
         * 走"手里同 kind 取前 n 张"的旧行为。
         */
        List<String> wantTiles;
        List<Integer> multiRon;
        /**
         * 非空表示这次鸣牌阶段得出的结论是**中途流局**（目前只有「三家和了」）。
         *
         * <p>为什么不能直接在这里 {@code return abort(...)}：{@code abort()} 要组一条
         * {@code ryuukyoku} 报文并回一个 {@code Result}，而 {@code claimPhase} 的返回类型是
         * {@code Claim}。所以只把"结论"递回给调用方，由它走与其它中途流局同一条
         * `abort()` 路径（本场 +1 / 庄家连庄 / 立直棒留至下一局全在那边）。
         */
        String abortReason;
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
                // ⚠ 鸣牌段**不走 ask()**（它自己组询问、自己收尾），所以策略漏斗在这里是第二处入口；
                //   任何埋点/记录都必须两处都挂（只挂 debugAskTap 会看不到鸣牌选项）。
                Map<String, Object> a = table.decideBot(s, new mahjong.ai.Decision(
                        mahjong.ai.Observation.ofClaim(this, s, opts, from,
                                Tiles.toStr(tileId), Json.str(extra, "win_note", null)),
                        this, "claim", opts, extra));
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

        // 同巡振听 / 立直振听：**被给过荣和选项却没和**（含超时未答）= 见逃。
        //   · 同巡振听：本巡之内不能再荣和，自家下一次摸牌时解除（见上面 `furitenTemp[turn] = false`）；
        //   · 立直振听：立直状态下见逃，**持续到本局结束**（`furitenPerm`）。
        //   （`docs/日本麻将.md` §振听。原来这两个标志**只有清除、从来没人置位** ——
        //     于是"放过一张荣和牌之后马上又能荣和同一张"，整条见逃/振听博弈都不存在。）
        for (int d = 1; d < 4; d++) {
            final int s = (from + d) % 4;
            final Set<String> types = askedTypes.get(s);
            if (types == null || !types.contains("ron")) {
                continue;                       // 本次没给过他荣和选项 → 谈不上见逃
            }
            final Map<String, Object> a = answers.get(s);
            if (a != null && "ron".equals(Json.str(a, "type", ""))) {
                continue;                       // 和了
            }
            if (isFuriten(s)) {
                continue;                       // 本来就在振听（给了选项也和不成立），不重复记账
            }
            furitenTemp[s] = true;
            if (riichi[s]) {
                furitenPerm[s] = true;
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
            // ⚠ 顺序有意义：**头跳先归约**。头跳一旦成立，"三家同时荣和"这个前提就不存在了
            //   （只认最近那家），所以两个开关同时打开时以头跳为准。三套预设互斥
            //   （mleague：头跳；天凤：三家和了；雀魂：两个都没有），这条只在自定义规则下可见。
            if (rules.headBump && ronSeats.size() > 1) {
                ronSeats = new ArrayList<>(ronSeats.subList(0, 1));
            } else if (rules.sanchaAbort && ronSeats.size() >= 3) {
                // 三家和了：三家**同时**荣和 → 流局（`docs/日本麻将.md` §头跳 L526-534；
                //   《天凤》采用、《雀魂》没有这条、M.League 用头跳）。
                //   中途流局不做不听罚符、庄家连庄、本场 +1、立直棒留至下一局
                //   —— 全由 `abort()` 与 `RoundScoring.nextHonba` 负责，这里只递结论。
                Claim c = new Claim();
                c.abortReason = "三家和了";
                return c;
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
                    // 副露赤宝选择：客户端可指定用哪几张（`["0m","0m","5m"]` 这种）
                    c.wantTiles = Json.strList(a, "tiles");
                    return c;
                }
                if (t == ClaimType.PON && "pon".equals(ty)) {
                    Claim c = new Claim();
                    c.type = ClaimType.PON;
                    c.seat = s;
                    c.wantTiles = Json.strList(a, "tiles");
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
        for (int i = 0; i < 2; i++) {
            final String s = want.get(i);
            final int k = (i == 0) ? k0 : k1;
            // 副露赤宝选择：客户端写 `0m` 表示"用赤五"，写 `5m` 表示"用普通五"；
            // 服务端照它挑（挑不到就作废这次吃，绝不替玩家拿错一张）。
            final int id = findHandTile(seat, k, Tiles.isRedStr(s), new ArrayList<>());
            if (id < 0) {
                return null;
            }
            ids[i] = id;
            // 两张不同 kind（上面已校验），所以不必再防"同一张用两次"
        }
        return ids;
    }

    /**
     * 从手里找一张「牌种 = kind、赤/普通 = red」的牌，跳过 {@code used} 里已占用的。
     *
     * <p>用于**副露的赤宝选择**（用户要求：副露能不能选赤宝）：
     * 赤五（`0m`）与普通五（`5m`）**同 kind 不同价值**（赤宝牌多一番），
     * 所以"用哪一张去鸣牌"是玩家该决定的事 —— 客户端用 `0m`/`5m` 这种精确牌码表达偏好，
     * 服务端照它挑。挑不到（客户端要赤五但手里只有普通五）返回 -1，由调用方按"没这张"处理。
     *
     * @param used 已选中的牌 id 列表（同一张不能被选两次）
     */
    private int findHandTile(int seat, int kind, boolean red, List<Integer> used) {
        for (int id : hand[seat]) {
            if (Tiles.kind(id) != kind || Tiles.isRedId(id) != red) {
                continue;
            }
            boolean taken = false;
            for (int u : used) {
                if (u == id) {
                    taken = true;
                    break;
                }
            }
            if (!taken) {
                return id;
            }
        }
        return -1;
    }

    /**
     * 按客户端给的**精确牌码**在手里挑 n 张（用于碰 / 大明杠的赤宝选择）。
     *
     * <p>`want` 为空时返回 {@code null} —— 调用方退回"取前 n 张"的旧行为（老客户端）。
     * 形状不对（个数不符 / 牌种不符 / 找了赤五手里没有）一律返回 {@code null}：
     * 宁可退回默认取法，也不替玩家拿错一张牌。
     *
     * @param tileId 被鸣的那张（它的牌种是基准）
     */
    private List<Integer> pickHandTiles(int seat, int tileId, List<String> want, int n) {
        if (want == null || want.isEmpty() || want.size() != n) {
            return null;
        }
        final int kind = Tiles.kind(tileId);
        List<Integer> out = new ArrayList<>(n);
        for (String s : want) {
            if (Tiles.parseKind(s) != kind) {
                return null;                       // 牌种必须与被鸣的那张一致
            }
            final int id = findHandTile(seat, kind, Tiles.isRedStr(s), out);
            if (id < 0) {
                return null;                       // 手里没有这一种（例如要赤五却只有普通五）
            }
            out.add(id);
        }
        return out;
    }

    /**
     * 供自检：直接走一遍「吃」的取牌校验（不合法返回 {@code null}）。
     * 见 {@link #pickChiTiles}：顺子成立与否**必须**由服务端自己验，不能只查手里有没有那张。
     */
    public int[] debugPickChiTiles(int seat, int tileId, List<String> want) {
        return pickChiTiles(seat, tileId, want);
    }

    /** 供自检：没被点名时的自动取牌（普通牌优先，见 {@link #pickAuto}）。 */
    public int[] debugPickAuto(int seat, int kind, int n) {
        List<Integer> l = pickAuto(seat, kind, n);
        int[] out = new int[l.size()];
        for (int i = 0; i < out.length; i++) {
            out[i] = l.get(i);
        }
        return out;
    }

    /** 供自检：按精确牌码挑 n 张（副露赤宝选择）。 */
    public int[] debugPickHandTiles(int seat, int tileId, List<String> want, int n) {
        List<Integer> l = pickHandTiles(seat, tileId, want, n);
        if (l == null) {
            return null;
        }
        int[] a = new int[l.size()];
        for (int i = 0; i < a.length; i++) {
            a[i] = l.get(i);
        }
        return a;
    }

    /** 供自检：按 kind + 赤/普通 找一张手里的牌（-1 = 没有）。 */
    public int debugFindHandTile(int seat, int kind, boolean red) {
        return findHandTile(seat, kind, red, new ArrayList<>());
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
            // 副露赤宝选择：**手里同 kind 能凑出的取法全列出来**（每种带精确牌码）。
            // 于是一个"碰 5p"可能下发两条：`tiles:["5p","5p"]`（不用赤五）与
            // `tiles:["0p","5p"]`（用赤五）—— 客户端按 `tiles` 里有没有赤牌画不同按钮。
            // ⚠ 旧协议下 pon 不带 `tiles`，客户端也就**没法选**，只能由服务端"取前两张"……
            //   那正是报障「副露无法区分红五与普通五」的根因（见 PROTOCOL §3.6）。
            for (List<String> variant : akaVariants(seat, kind, 2)) {
                opts.add(Json.obj("type", "pon", "tiles", new ArrayList<Object>(variant)));
            }
        }
        // 大明杠：**立直后不可**（它一定会改变手牌构成，而暗杠才可能"听牌不变"）。
        // 立直是门前状态，大明杠还会把 menzen 打掉；任何规则都不允许，所以这里直接挡掉。
        if (canDaiminkan(seat, kind)) {
            List<Object> kans = new ArrayList<>();
            for (List<String> variant : akaVariants(seat, kind, 3)) {
                kans.add(Json.obj("kind", "daiminkan", "tile", Tiles.kindToStr(kind),
                        "tiles", new ArrayList<Object>(variant)));
            }
            opts.add(Json.obj("type", "kan", "kans", kans));
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

    /**
     * 副露赤宝选择：手里同 {@code kind} 的牌能凑出的**取法**（每种一组精确牌码）。
     *
     * <p>只在"真的有得选"时才给出两条：手里既有赤五又有普通五，且够凑出这一副。
     * 顺序固定为**不用赤 → 用赤**（客户端按钮顺序也就稳定）：
     * <ul>
     *   <li>{@code ["5p","5p"]}：普通牌优先（默认取法，见 {@link #pickAuto}）；</li>
     *   <li>{@code ["0p","5p"]}：明确用赤五（赤五比普通五值钱，所以必须是玩家点名才用）。</li>
     * </ul>
     * 只有一种取法时只给一条（但**仍然带 `tiles`**，客户端才有依据显示"这一副会用到赤五"）。
     *
     * @param need 这一副要用几张（碰 2 / 大明杠 3）
     */
    private List<List<String>> akaVariants(int seat, int kind, int need) {
        List<Integer> plain = new ArrayList<>();      // 普通牌（按手里的顺序）
        List<Integer> red = new ArrayList<>();
        for (int id : hand[seat]) {
            if (Tiles.kind(id) != kind) {
                continue;
            }
            if (Tiles.isRedId(id)) {
                red.add(id);
            } else {
                plain.add(id);
            }
        }
        List<List<String>> out = new ArrayList<>();
        if (plain.size() >= need) {
            out.add(codesOf(plain.subList(0, need)));
        }
        if (!red.isEmpty() && plain.size() + red.size() >= need) {
            List<Integer> mix = new ArrayList<>();
            mix.add(red.get(0));
            for (int i = 0; i < need - 1 && i < plain.size(); i++) {
                mix.add(plain.get(i));
            }
            if (mix.size() == need) {
                List<String> codes = codesOf(mix);
                if (!out.contains(codes)) {
                    out.add(codes);
                }
            }
        }
        if (out.isEmpty()) {
            // 理论上到不了这里（能鸣就说明手里够），留一条"按手里顺序"的兜底。
            List<Integer> all = new ArrayList<>(plain);
            all.addAll(red);
            if (all.size() >= need) {
                out.add(codesOf(all.subList(0, need)));
            }
        }
        return out;
    }

    private static List<String> codesOf(List<Integer> ids) {
        List<String> out = new ArrayList<>(ids.size());
        for (int id : ids) {
            out.add(Tiles.toStr(id));
        }
        return out;
    }

    /**
     * 没被点名时的自动取牌：**普通牌优先**（赤五是资源，别在玩家没要求时顺手用掉）。
     *
     * <p>报障「副露无法区分红五与普通五」的另一半：旧实现是"按手牌顺序取前 n 张"，
     * 而手牌是按 {@link #compareTile} 排过序的（**赤在前**），于是"碰 5p"会**悄悄吃掉赤五**。
     */
    private List<Integer> pickAuto(int seat, int kind, int n) {
        List<Integer> out = new ArrayList<>();
        for (int id : hand[seat]) {
            if (Tiles.kind(id) == kind && !Tiles.isRedId(id) && out.size() < n) {
                out.add(id);
            }
        }
        for (int id : hand[seat]) {
            if (Tiles.kind(id) == kind && out.size() < n && !out.contains(id)) {
                out.add(id);
            }
        }
        return out;
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
            // 同样是赤宝选择：客户端可指定用哪三张
            kanPicked = pickHandTiles(seat, tileId, cl.wantTiles, 3);
            if (kanPicked == null) {
                kanPicked = pickAuto(seat, kind, 3);     // 普通牌优先（别顺手吃赤五）
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
                    // ⚠ 参与运算的必须是**牌种 kind**，不是牌 id：`tiles[]` 里存的是 id，
                    //   而 `Tiles.suit()` 是按 kind 定义的（kind/9），`forbiddenDiscard` 也是按
                    //   kind 消费的（RoundOptions.discardChoices 里 `contains(Tiles.kind(id))`）。
                    //   以前直接拿 id 做减法和花色判断：`d = a - b` 变成了"两张牌的 copy 之差"，
                    //   于是禁打集合里混进 id、真正的筋替（kind 差 1/2）反而检测不出来，
                    //   下发的出牌选项既漏真禁张又多做无谓禁张（AUDIT F13）。
                    //   现在整份集合抽成纯函数 `RoundOptions.kuikaeForbidden`（可单测），
                    //   并且修掉「被吃的那张在顺子上边」时漏判筋食替的那一半（AUDIT S-49）。
                    forbiddenDiscard.addAll(RoundOptions.kuikaeForbidden(
                            kind, Tiles.kind(cl.tiles[0]), Tiles.kind(cl.tiles[1])));
                }
                break;
            }
            case PON: {
                // 副露赤宝选择：客户端给了精确牌码就照它挑（要赤五给赤五、要普通五给普通五）；
                // 没给（老客户端）走"同 kind 取前两张"的旧行为。
                List<Integer> picked = pickHandTiles(seat, tileId, cl.wantTiles, 2);
                if (picked == null) {
                    picked = pickAuto(seat, kind, 2);    // 普通牌优先（别顺手吃赤五）
                }
                if (picked.size() < 2) {
                    return;   // 与大明杠同理：状态改之前就挡住（防越界 / 防伪造报文改分）
                }                if (picked.size() < 2) {
                    return;   // 与大明杠同理：状态改之前就挡住（防越界 / 防伪造报文改分）
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
            addPao(seat, from, "大三元");
        }
        if (Tiles.isWind(m.baseKind()) && winds == 4) {
            addPao(seat, from, "大四喜");
        }
        // 四杠子包牌：**只有 M.League** 有，且必须是"由他家的舍张大明杠完成第 4 个杠"。
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
                addPao(seat, from, "四杠子");
            }
        }
    }

    /**
     * 登记一条包牌责任（**同一个役只登记一次**）。
     *
     * <p>幂等是必须的：`updatePao` 的判据是"**此刻**三元/风牌副露数 == 3/4**且**这次
     * 副露本身是那种牌"，而**加杠**会让计数停在 3/4 —— 真实路径里加杠走 `turnKan`
     * 不经过这里，但自测钩子与将来的入口不该能重复登记。
     */
    private void addPao(int seat, int payer, String yaku) {
        for (Pao p : pao[seat]) {
            if (p.yaku.equals(yaku)) {
                return;
            }
        }
        pao[seat].add(new Pao(payer, yaku));
    }

    /** 该座位**已登记的包牌责任者**（按成立先后，去重）—— 报文里的 `pao.seats`。 */
    public List<Integer> paoSeatsOf(int seat) {
        List<Integer> out = new ArrayList<>();
        for (Pao p : pao[seat]) {
            if (!out.contains(p.payer)) {
                out.add(p.payer);
            }
        }
        return out;
    }

    /** 第一位包牌者（-1 = 无）—— 报文里的 `pao.seat`（兼容旧客户端）与旧读法。 */
    public int paoSeatOf(int seat) {
        List<Integer> all = paoSeatsOf(seat);
        return all.isEmpty() ? -1 : all.get(0);
    }

    /** 该座位的包牌责任清单（形如 {@code "大三元@2"}）—— 自检与日志用。 */
    public List<String> paoDebugLines(int seat) {
        List<String> out = new ArrayList<>();
        for (Pao p : pao[seat]) {
            out.add(p.toString());
        }
        return out;
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
        final int[] c = winCounts(seat, winTileId, tsumo);
        if (c == null) {
            return null;
        }
        final int aka = redCount(seat) + (!tsumo && Tiles.isRedId(winTileId) ? 1 : 0);
        return scoreWith(seat, c, winTileId, tsumo, rinshan, haitei, chankan, houtei,
                riichi[seat], true, aka);
    }

    /**
     * 训练 / 评测用的**打点查询**：假设该家此刻和了 {@code winKind} 这张牌能得多少分。
     *
     * <p>与实局判定的区别只有一处：**不算里宝牌**。里宝只有和牌那一刻才翻开，
     * 决策时（以及训练特征里）不可知 —— 把它算进去等于让 AI 看见未来。
     * 赤宝牌与宝牌照常算（它们本来就公开）。
     *
     * <p>⚠ **张数契约**（与实局一致，张数不符返回 {@code null}，见 {@link WinCheck#counts}）：
     * {@code tsumo=false} 时该家暗牌必须是 **13 张形态**（13−3×副露，和了牌**不在**手里），
     * {@code tsumo=true} 时必须是 **14 张形态**（和了牌**已在**手里）。自家回合手上是 14 张，
     * 想查"打掉某张之后荣和值多少"，用下面那个**指定暗牌**的重载（先减掉要打的那张）。
     *
     * @param assumeRiichi 是否按"已立直"计入立直这一役；传 {@code false} 可以问
     *                     "这手**不立直**能不能和、值多少"（默听判断要用）
     * @return 不能和（无役 / 番缚不够 / 牌型不成立 / 张数不对）返回 {@code null}
     */
    public Evaluator.HandScore scoreIfWin(int seat, int winKind, boolean tsumo, boolean assumeRiichi) {
        if (seat < 0 || seat > 3) {
            return null;
        }
        return scoreIfWin(seat, concealCounts(seat), winKind, tsumo, assumeRiichi, redCount(seat));
    }

    /** 同上，按**当前是否立直**（最常用的那一种）。 */
    public Evaluator.HandScore scoreIfWin(int seat, int winKind, boolean tsumo) {
        return scoreIfWin(seat, winKind, tsumo, riichi[seat]);
    }

    /**
     * 打点查询的**指定暗牌**版本：不读牌桌上的手牌，直接对一份暗牌计数求值。
     *
     * <p>为什么要它：teacher 想比较"打这张还是那张值多少"，那几份暗牌**只存在于计算里**
     * （手里是 14 张，"打掉某张之后"的 13 张从来没有真的被写回过牌桌）。
     *
     * @param concealed      暗牌计数（{@code tsumo=false} 时 13 张形态、{@code true} 时 14 张形态）
     * @param akaInConcealed 这份暗牌 + 和了牌里有几张**赤五**（牌码在这一层已经丢了，
     *                       所以由调用方给；保守起见可以传 0）。⚠ 副露里的赤五不用算，那是真实 id
     */
    public Evaluator.HandScore scoreIfWin(int seat, int[] concealed, int winKind, boolean tsumo,
                                          boolean assumeRiichi, int akaInConcealed) {
        if (seat < 0 || seat > 3 || winKind < 0 || winKind >= Tiles.KIND_COUNT
                || concealed == null) {
            return null;
        }
        // 假想和了牌一律按**非赤**（copy=3）：赤五的 +1 番取决于具体那张牌，
        // "我要不要立直"这类取舍不该赌在赤牌上。
        final int winTileId = Tiles.id(winKind, 3);
        final int[] c = WinCheck.counts(concealed, melds[seat].size(), winTileId, tsumo);
        if (c == null) {
            return null;
        }
        return scoreWith(seat, c, winTileId, tsumo, false, false, false, false,
                assumeRiichi, false, akaInConcealed);
    }

    /** 该家暗牌里有几张赤五。 */
    private int redCount(int seat) {
        int n = 0;
        for (int id : hand[seat]) {
            if (Tiles.isRedId(id)) {
                n++;
            }
        }
        return n;
    }

    /**
     * 打点计算的实际实现（实局判定与查询共用同一份上下文构造）。
     *
     * @param winCounts    已并入和了牌的暗牌计数（见 {@link WinCheck#counts}）
     * @param assumeRiichi 立直/两立直/一发按此值填（{@code false} = 假设没立直）
     * @param includeUra   是否计入里宝指示牌；**查询必须为 false**
     * @param akaInConcealed 暗牌 + 和了牌里的赤五张数（{@code Evaluator} 靠牌 id 数赤宝，
     *                       所以这里合成这么多张赤五 id 补进去；副露用真实 id）
     */
    private Evaluator.HandScore scoreWith(int seat, int[] winCounts, int winTileId, boolean tsumo,
                                          boolean rinshan, boolean haitei, boolean chankan,
                                          boolean houtei, boolean assumeRiichi, boolean includeUra,
                                          int akaInConcealed) {
        final int winKind = Tiles.kind(winTileId);
        WinContext ctx = new WinContext();
        ctx.rules = rules;
        ctx.seat = seat;
        ctx.dealerSeat = dealer;
        ctx.roundWind = 27 + roundWind;
        ctx.tsumo = tsumo;
        ctx.riichi = assumeRiichi && !doubleRiichi[seat];
        ctx.doubleRiichi = assumeRiichi && doubleRiichi[seat];
        ctx.ippatsu = assumeRiichi && ippatsu[seat] && riichi[seat];
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
        ctx.uraIndicators = includeUra ? uraIndicators() : List.of();
        ctx.allTileIds = akaTiles(seat, winTileId, akaInConcealed);
        ctx.menzen = menzen[seat];
        Evaluator.HandScore s = Evaluator.evaluate(ctx, winCounts, melds[seat], winKind);
        return s.valid ? s : null;
    }

    /** 给 {@code Evaluator} 数赤宝用的牌 id 列表：副露真实 id + 和了牌 + 合成的赤五。 */
    private List<Integer> akaTiles(int seat, int winTileId, int akaInConcealed) {
        List<Integer> ids = new ArrayList<>();
        for (Meld m : melds[seat]) {
            for (int t : m.tiles) {
                ids.add(t);
            }
        }
        if (winTileId >= 0) {
            ids.add(winTileId);
        }
        for (int i = 0; i < akaInConcealed; i++) {
            final int kind = i % 3 == 0 ? Tiles.AKA_M : (i % 3 == 1 ? Tiles.AKA_P : Tiles.AKA_S);
            ids.add(Tiles.id(kind, 0));
        }
        return ids;
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

    /**
     * 本手牌里各包牌者各承担多少基本点（结算列表）。
     *
     * <p>《天凤》的 `paoCoversAll` 是"包牌涉及**复合后的全部役满得点**"；《雀魂》/ M.League
     * 只包被包的那一役（`docs/日本麻将.md` §包牌 L812-824）。**多个责任者**时本作口径：
     * 退回"各包各的" —— 见下面的注释。
     */
    private List<Payments.Pao> paoPaysFor(int seat, Evaluator.HandScore sc) {
        List<Payments.Pao> out = new ArrayList<>();
        if (!rules.pao || sc == null || pao[seat].isEmpty()) {
            return out;
        }
        // 《天凤》：包牌涉及**复合后的全部役满得点**（包牌者全额承担，其他人一分不付）。
        // ⚠ 这条只在**一位**责任者时才有意义：有两个责任者时"整手牌"不可能同时归两个人
        //   —— 那样和牌者会收双份、授受不再守恒。此时退回"各包各的"（每人只为自己造成的
        //   那一役负责）。这个组合在三套预设里都到不了（《天凤》没有四杠子包牌、
        //   大三元与大四喜不可能共存），只有自定义规则会碰到，所以口径写在这里与 DESIGN。
        if (rules.paoCoversAll && paoSeatsOf(seat).size() == 1) {
            out.add(new Payments.Pao(pao[seat].get(0).payer, Math.max(0, sc.base)));
            return out;
        }
        for (Pao p : pao[seat]) {
            int base = yakumanBaseOf(sc, p.yaku);
            if (base > 0) {
                out.add(new Payments.Pao(p.payer, base));
            }
        }
        return out;
    }

    /** 这一手成立 `yaku` 这一役满时它是多少基本点（0 = 这一手没有这个役满）。 */
    private static int yakumanBaseOf(Evaluator.HandScore sc, String yaku) {
        int base = 0;
        for (Evaluator.Yaku y : sc.yaku) {
            if (y.yakuman > 0 && yaku.equals(y.name)) {
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
        List<Payments.Pao> paos = paoPaysFor(seat, sc);
        Payments.Result pay = Payments.compute(sc, seat, -1, dealer, honba, sticks, true, paos);
        applyDelta(r, pay.delta);
        r.sticksLeft = 0;
        r.dealerRenchan = RoundScoring.winBy(dealer, seat);
        r.tenpai[seat] = true;
        sendAgari(seat, -1, true, tileId, sc, pay, paoSeatsOf(seat));
        return r;
    }

    private Result agariRon(List<Integer> winners, int from, int tileId) {
        return agariRon(winners, from, tileId, false, false);
    }

    /**
     * 荣和结算。
     *
     * @param chankan       这张牌是不是**抢杠**的那张（加杠被荣和）
     * @param riichiDiscard 这张牌是不是**首次放置的立直宣言牌**（燕返，见下）
     */
    private Result agariRon(List<Integer> winners, int from, int tileId,
                            boolean chankan, boolean riichiDiscard) {
        Result r = new Result();
        r.agari = true;
        r.loser = from;
        r.tsumo = false;
        if (winners == null || winners.isEmpty()) {
            r.winner = -1;
            return r;
        }
        // ---------- 燕返：荣和的正是**首次放置的**立直宣言牌 → 立直不成立，1000 点退回
        //
        // `docs/日本麻将.md` §立直 L893：「《雀魂》中，立直宣言牌放铳（燕返）的情况下，
        // 认为立直不成立，不加收 1000 点。《天凤》和 M.League 也采用相同的规定」。
        // 旧实现是 `doRiichi()` 在宣言牌**落地之前**就扣掉 1000 并 `sticks++`，
        // 随后 `agariRon` 把供託全给和牌者 —— 三套预设都错、每次宣言被荣和都触发。
        //
        // ⚠ 只对**首次放置**的宣言牌成立（同节 L1291-1293）：宣言牌被鸣走后横置会顺延到
        //   该家下一张打出的牌，那张被荣和**不算**燕返。顺延牌对应的 `declareRiichi`
        //   恒为 false（走的是 `sidewaysPending` 那条路），所以这里只看"本次出牌是不是宣言本身"。
        int riichiVoid = -1;
        if (riichiDiscard && riichi[from]) {
            riichiVoid = from;
            riichi[from] = false;
            doubleRiichi[from] = false;
            ippatsu[from] = false;
            sticks = Math.max(0, sticks - 1);        // 那根立直棒退回去，别让和牌者白收
            int[] refund = new int[4];
            refund[from] = 1000;
            applyDelta(r, refund);                   // 经 applyDelta：`r.delta` 与 `scores` 同一份账
        }
        r.winner = winners.get(0);
        int sticksLeft = sticks;
        // ⚠ 先把**所有**赢家结算完，再逐家发报文（见下面的 sendAgari 循环）。
        //   `agari` 里带的是 `scores_after`，而边算边发的话第一条报文里的分数**还没有后面
        //   几家的收支**（多家荣和时的"中途快照"，见 AUDIT S-53）：客户端按顺序处理、取最后
        //   一条所以看不出来，但回放 / 重连若取中间那条就是错的账。
        List<Object[]> settled = new ArrayList<>();      // {winner, HandScore, Payments.Result, pao seats}
        for (int i = 0; i < winners.size(); i++) {
            int w = winners.get(i);
            Evaluator.HandScore sc = checkWin(w, tileId, false, false, false, chankan, wall.atLastLiveTile());
            if (sc == null) {
                continue;
            }
            List<Integer> paoSeats = paoSeatsOf(w);
            List<Payments.Pao> paos = paoPaysFor(w, sc);
            int useSticks = (i == 0) ? sticks : 0;
            // ⚠ 供託与**本场加点**都只归 `winners.get(0)`，而它就是**离放铳者最近**的那家
            //   （`claimPhase` 按 `(from + d) % 4`、d = 1..3 的顺序收集 `ronSeats`）。
            //   文档 §和牌 L794：「《天凤》二家和了时，立直棒和本场加点均由距放铳者最近的
            //   和牌者取得」。旧实现把立直棒只给第一家、**本场却给每一家** ——
            //   双响 + n 本场时放铳者多付 300×n（总额仍守恒，所以只有"归属"错）。
            //   （M.League 走头跳，压根到不了这里；《雀魂》文档未给另一套口径。）
            int useHonba = (i == 0) ? honba : 0;
            Payments.Result pay = Payments.compute(sc, w, from, dealer, useHonba, useSticks,
                                                   false, paos);
            sticksLeft -= useSticks;
            applyDelta(r, pay.delta);
            settled.add(new Object[]{w, sc, pay, paoSeats});
        }
        r.sticksLeft = Math.max(0, sticksLeft);
        r.dealerRenchan = RoundScoring.winBy(dealer, winners);
        for (int w : winners) {
            r.tenpai[w] = true;
        }
        // 分数到此已是最终值，再按"距放铳者由近到远"逐家发（顺序与旧实现一致）
        for (Object[] s : settled) {
            @SuppressWarnings("unchecked")
            List<Integer> seats = (List<Integer>) s[3];
            sendAgari((Integer) s[0], from, false, tileId, (Evaluator.HandScore) s[1],
                    (Payments.Result) s[2], seats, riichiVoid);
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
                           Evaluator.HandScore sc, Payments.Result pay, List<Integer> pao) {
        sendAgari(winner, from, tsumo, tileId, sc, pay, pao, -1);
    }

    /**
     * @param pao        包牌责任者（可能不止一个，见 {@link #pao}）：报文里
     *                   `pao.seat` = 第一位（兼容旧客户端）、`pao.seats` = 全部。
     * @param riichiVoid 燕返：这一家刚宣告的立直被判为**不成立**（{@code -1} = 无）。
     *                   报文里带出去，客户端据此清掉那家的立直标记与那根供託 ——
     *                   「立直成不成立」是规则判定，客户端不许自己推断（AGENTS §2.1）。
     */
    private void sendAgari(int winner, int from, boolean tsumo, int tileId,
                           Evaluator.HandScore sc, Payments.Result pay, List<Integer> pao,
                           int riichiVoid) {
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
                // 包牌责任者：`seat` = 第一位（**兼容旧客户端**的老字段）、`seats` = 全部，
                // 按责任成立先后。一手牌可以由两人各包一个役满（AUDIT S-52），
                // 老字段装不下，所以权威读法是 `seats`（无包牌 = 空数组）。
                "pao", Json.obj("seat", pao.isEmpty() ? -1 : pao.get(0),
                        "seats", new ArrayList<Object>(pao)),
                // 燕返（-1 = 无）：这一家刚宣告的立直不成立，客户端清掉它的立直标记与供託
                "riichi_void", riichiVoid);
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
            r.dealerRenchan = RoundScoring.nagashiBy(dealer, tenpai);
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

    /**
     * 自测钩子：按**当前手牌**直接跑一次鸣牌仲裁，返回结论摘要。
     *
     * <p>为什么要这个钩子：多家荣和 / 头跳 / 三家和了只取决于"同时有几家能和这张"，
     * 靠整场模拟随机撞出来既慢又不可复现（自检里也拿不到"三家同时听同一张"的牌）。
     * 这里把结论压成一个字符串，测试只需摆好手牌再问一次：
     * <ul>
     *   <li>{@code "none"}：没人鸣；</li>
     *   <li>{@code "abort:三家和了"}：判成中途流局；</li>
     *   <li>{@code "ron:[1, 2]"}：荣和，方括号里是**按距放铳者由近到远**的赢家；</li>
     *   <li>{@code "PON:2"} / {@code "CHI:1"} / {@code "KAN:3"}：其它鸣牌成立的座位。</li>
     * </ul>
     * 参与的座位必须都是机器人（`addBot`）——真人座位的答复来自网线，测试里喂不进去。
     */
    public String debugClaimOutcome(int from, int tileId) {
        Claim c = claimPhase(from, tileId, false);
        if (c == null) {
            return "none";
        }
        if (c.abortReason != null) {
            return "abort:" + c.abortReason;
        }
        if (c.type == ClaimType.RON) {
            return "ron:" + c.multiRon;
        }
        return c.type + ":" + c.seat;
    }

    /**
     * 自测钩子：直接跑一次自家回合的暗杠 / 加杠（**含抢杠判定**）。
     *
     * <p>抢杠那条路只有"手里有第 4 张 + 有人正好听那张"才会走到，靠整场模拟撞太慢；
     * 这里把 {@code turnKan} 暴露出来，测试摆好牌就能问。返回非 {@code null} 表示本局以抢杠结束。
     *
     * @param kindStr {@code "ankan"} / {@code "kakan"}
     */
    public Result debugTurnKan(int seat, String kindStr, String tileStr) {
        return turnKan(seat, Json.obj("kind", kindStr, "tile", tileStr), -1);
    }

    /**
     * 自测钩子：直接跑一次荣和结算，返回四家的点数增减。
     *
     * <p>多家荣和时调用方按「距放铳者由近到远」传 {@code winners}
     * （与 {@link #debugClaimOutcome} 返回的顺序一致）。整局的 `scores` 会被真的改动，
     * 所以每条断言都用**新构造的 Round**。
     */
    public int[] debugRonDeltas(List<Integer> winners, int from, int tileId) {
        return debugRonDeltas(winners, from, tileId, false);
    }

    /** 同 {@link #debugRonDeltas(List, int, int)}，但可指定这张牌是不是立直宣言牌（燕返）。 */
    public int[] debugRonDeltas(List<Integer> winners, int from, int tileId, boolean riichiDiscard) {
        return agariRon(winners, from, tileId, false, riichiDiscard).delta;
    }

    /**
     * 自测钩子：直接宣告立直 —— 走的是**生产的** {@link #doRiichi}（置位 + 扣 1000 +
     * 供託 +1 + 广播），这样燕返那一侧测的就是真实账面，不是测试自己摆的状态。
     */
    public void debugDoRiichi(int seat, int discardTile) {
        doRiichi(seat, discardTile);
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
