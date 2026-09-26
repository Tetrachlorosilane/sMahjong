package mahjong.ai;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import mahjong.core.Meld;
import mahjong.core.Tiles;
import mahjong.game.Round;
import mahjong.rules.Visible;
import mahjong.util.Json;

/**
 * 一个座位的**信息集观测**（训练接口的输入）。
 *
 * <p><b>这个类唯一的职责是：只暴露该座位合法可见的信息。</b>
 * 观测里出现的每一个字段都必须能从「自己手牌 + 四家牌河/副露 + 宝牌指示牌 + 公开状态」
 * 推出来。以下东西**永远不许读**（读了就是 AI 作弊，训练与评测同时失效）：
 * <ul>
 *   <li>别家手牌 {@code Round.hand[other]}；</li>
 *   <li>牌山顺序 {@code Round.wallOrder()} / {@code Wall.debugAllTiles()}；</li>
 *   <li>里宝指示牌 {@code Round.uraIndicators()}（宝牌指示牌是公开的，里宝不是）；</li>
 *   <li>别家振听 {@code Round.furitenTemp[other]} / {@code furitenPerm[other]}
 *       —— 服务端 {@code stateFor} 也只回自己那一项，理由相同（临时振听等价于"他听牌"）。</li>
 * </ul>
 * 回归：{@code SelfTest.trainingObservationTests} 里有一条**置换不变式**——
 * 把别家手牌与牌山整体换掉后，同一座位的观测 JSON 必须逐字节相同。
 *
 * <p>字段含义与 {@code docs/PROTOCOL.md} 的「训练接口」一节一致；那里是权威描述。
 */
public final class Observation {

    /** 观测格式版本：字段增删要 +1（数据集复用靠它判断兼容性）。 */
    public static final int VERSION = 2;

    public final int seat;
    /** {@code "turn"} = 自家摸打询问；{@code "claim"} = 别家舍张的鸣牌询问。 */
    public final String kind;

    // ---------------------------------------------------------------- 自己
    /** 暗牌计数（长度 34）。自家回合时**含刚摸到的那张**（14 张），鸣牌询问时 13 张。 */
    public final int[] hand;
    /** 暗牌里是否持有该牌种的**赤五**（长度 34）。 */
    public final boolean[] handRed;
    /** 刚摸到的牌码（{@code "0m"} 表示赤五）；非自家回合为 {@code null}。 */
    public final String drawn;
    /** 自己第几次摸牌（1 起）。 */
    public final int playerDraws;
    public final boolean menzen;
    public final boolean selfRiichi;
    /** 自己是否振听（只可能是自己的；见类注释）。 */
    public final boolean furiten;

    // ---------------------------------------------------------------- 公开
    /** 四家副露（每家一个列表，元素是 {@link Meld#toJson()} 的形状）。 */
    public final List<List<Map<String, Object>>> melds;
    /** 四家牌河（牌码；被鸣走的已由服务端移除）。 */
    public final List<List<String>> discards;
    public final List<String> doraIndicators;
    /** 四家立直状态（公开）。 */
    public final boolean[] riichi;
    /** 四家一发状态（公开：立直后一巡且无人鸣牌）。 */
    public final boolean[] ippatsu;
    public final int[] scores;
    /**
     * 本局是否**允许食い断**（规则取舍里唯一被特征用到的位；打点粗估的断幺项要它）。
     *
     * <p>规则集整体**不进观测**（训练数据是在固定预设下产生的），但这一位会影响
     * {@code HandEval.estimatedHan} 的结果 —— 不带上它，同一份 obs 在"食い断 on/off"
     * 两种规则下会被算成两个不同的输入（推理与离线两条通路就会漂）。
     */
    public final boolean kuitan;
    public final int roundWind;
    public final int kyoku;
    public final int honba;
    public final int sticks;
    public final int dealer;
    public final int tilesLeft;
    public final int deadWallLeft;
    /** 全场已打出的总张数（公开的巡目代理量）。 */
    public final int totalDiscards;
    public final int kanCount;
    /** 本局是否已有人鸣牌（公开，用于「双立直 / 九种九牌」判断）。 */
    public final boolean anyCall;
    /** 34 维**可见牌**计数 = 四家牌河 + 四家副露 + 宝牌指示牌（公开信息的派生量，省得训练侧重算）。 */
    public final int[] visible;

    // ---------------------------------------------------------------- 局面标记
    public final boolean haitei;
    public final boolean houtei;
    public final boolean rinshan;

    // ---------------------------------------------------------------- 鸣牌询问专用
    /** 打出被询问那张牌的座位；自家回合为 -1。 */
    public final int from;
    /** 被鸣 / 被荣的那张牌码；自家回合为 {@code null}。 */
    public final String calledTile;
    /** 自己能听牌但不能和的原因（{@code furiten} / {@code no_yaku}），无则为 {@code null}。 */
    public final String winNote;

    /** 本次询问的**全部合法动作**（由 {@code options} 展开，顺序一致）。 */
    public final List<Action> legal;

    private Observation(Round r, int seat, String kind, List<Map<String, Object>> options,
                        String drawn, boolean rinshan, int from, String calledTile, String winNote) {
        this.seat = seat;
        this.kind = kind;
        this.drawn = drawn;
        this.rinshan = rinshan;
        this.from = from;
        this.calledTile = calledTile;
        this.winNote = winNote;

        this.hand = new int[Tiles.KIND_COUNT];
        this.handRed = new boolean[Tiles.KIND_COUNT];
        for (int id : r.hand[seat]) {
            int k = Tiles.kind(id);
            this.hand[k]++;
            if (Tiles.isRedId(id)) {
                this.handRed[k] = true;
            }
        }
        this.playerDraws = r.playerDraws[seat];
        this.menzen = r.menzen[seat];
        this.selfRiichi = r.riichi[seat];
        // ⚠ 自家回合**不能**调 r.isFuriten()：它内部会走 waitKinds → 34 次向听 DFS，
        //   而自家回合的手牌是 **14 张**，`Agari.waits` 对 14 张恒返回空集（它按 13 张定义：
        //   逐张补一张看是否和了形，14 张补完是 15 张，永不成和），所以
        //   `isFuriten() ≡ furitenTemp || furitenPerm` 在那一刻**完全等价**，白跑一次 DFS
        //   纯属浪费（实测拖慢整场自对弈约 90%）。
        //   鸣牌询问是 13 张，那里才用完整判据 —— 而且 claimOptions 刚刚问过，缓存是热的。
        final boolean turnAsk = "turn".equals(kind);
        this.furiten = turnAsk ? (r.furitenTemp[seat] || r.furitenPerm[seat]) : r.isFuriten(seat);

        this.melds = new ArrayList<>(4);
        this.discards = new ArrayList<>(4);
        for (int s = 0; s < 4; s++) {
            List<Map<String, Object>> ms = new ArrayList<>();
            for (Meld m : r.melds[s]) {
                ms.add(m.toJson());
            }
            this.melds.add(ms);
            List<String> ds = new ArrayList<>();
            for (int id : r.discards[s]) {
                ds.add(Tiles.toStr(id));
            }
            this.discards.add(ds);
        }
        List<String> dora = new ArrayList<>();
        for (int k : r.doraIndicators()) {
            dora.add(Tiles.kindToStr(k));
        }
        this.doraIndicators = dora;
        // 可见牌统计走规则层的**唯一**实现（`Visible`）—— 训练侧的危险度/进张枚数也用同一份，
        // 各算一份迟早会漂。⚠ 里宝指示牌**不算**可见（`Visible` 只收宝牌指示牌）。
        this.visible = Visible.counts(r.discards, r.melds, r.doraIndicators());

        this.riichi = r.riichi.clone();
        this.ippatsu = r.ippatsu.clone();
        this.scores = r.scores.clone();
        this.kuitan = r.rules == null || r.rules.kuitan;
        this.roundWind = r.roundWind;
        this.kyoku = r.kyoku;
        this.honba = r.honba;
        this.sticks = r.sticks;
        this.dealer = r.dealer;
        this.tilesLeft = r.tilesLeft();
        this.deadWallLeft = r.deadWallLeft();
        this.totalDiscards = r.totalDiscards;
        this.kanCount = r.kanCount;
        this.anyCall = r.anyCall;
        this.haitei = r.atLastLiveTile() && "turn".equals(kind);
        this.houtei = r.atLastLiveTile() && "claim".equals(kind);
        this.legal = List.copyOf(Action.enumerate(options));
    }

    /** 自家摸打询问的观测。 */
    public static Observation ofTurn(Round r, int seat, List<Map<String, Object>> options,
                                     int drawnId, boolean rinshan, String winNote) {
        return new Observation(r, seat, "turn", options,
                drawnId < 0 ? null : Tiles.toStr(drawnId), rinshan, -1, null, winNote);
    }

    /**
     * 别家舍张的鸣牌询问的观测。
     *
     * @param calledTileCode 被鸣 / 被荣那张的**牌码**（不是牌 id）—— 必须原样保留
     *                       {@code "0m"} 这类赤五写法，所以这里收字符串而不是 id
     */
    public static Observation ofClaim(Round r, int seat, List<Map<String, Object>> options,
                                      int from, String calledTileCode, String winNote) {
        return new Observation(r, seat, "claim", options, null, false, from, calledTileCode, winNote);
    }

    /** 合法动作的键列表（给"按本次枚举 + 掩码"的参数化头用）。 */
    public List<String> legalKeys() {
        List<String> ks = new ArrayList<>(legal.size());
        for (Action a : legal) {
            ks.add(a.key());
        }
        return ks;
    }

    /** 该动作在本次合法集里的下标；不合法返回 -1。 */
    public int indexOf(Action a) {
        for (int i = 0; i < legal.size(); i++) {
            if (legal.get(i).key().equals(a.key())) {
                return i;
            }
        }
        return -1;
    }

    /**
     * 观测的 JSON 表示（训练数据的输入侧；字段表见 {@code docs/PROTOCOL.md}）。
     *
     * <p>刻意用**定长数组**而不是稀疏列表：34 维计数直接就是网络输入，
     * 也让"置换不变式"能逐字节比对。
     */
    public Map<String, Object> toJson() {
        List<Object> bools = new ArrayList<>(Tiles.KIND_COUNT);
        for (boolean b : handRed) {
            bools.add(b);
        }
        return Json.obj(
                "v", VERSION,
                "seat", seat,
                "kind", kind,
                "hand", hand,
                "hand_red", bools,
                "drawn", drawn,
                "player_draws", playerDraws,
                "menzen", menzen,
                "self_riichi", selfRiichi,
                "furiten", furiten,
                "melds", melds,
                "discards", discards,
                "dora_indicators", doraIndicators,
                "riichi", Json.boolList(riichi),
                "ippatsu", Json.boolList(ippatsu),
                "scores", Json.intList(scores),
                "kuitan", kuitan,
                "round", Json.obj(
                        "bakaze", new String[]{"E", "S", "W", "N"}[roundWind],
                        "kyoku", kyoku,
                        "honba", honba,
                        "dealer", dealer,
                        "riichi_sticks", sticks),
                "tiles_left", tilesLeft,
                "dead_wall_left", deadWallLeft,
                "total_discards", totalDiscards,
                "kan_count", kanCount,
                "any_call", anyCall,
                "visible", visible,
                "haitei", haitei,
                "houtei", houtei,
                "rinshan", rinshan,
                "from", from,
                "called_tile", calledTile,
                "win_note", winNote,
                "legal", legalKeys());
    }
}
