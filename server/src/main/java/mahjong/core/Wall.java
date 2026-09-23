package mahjong.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Random;

/**
 * 一局的**牌山 + 王牌**：只管「牌从哪来、还剩多少」，不含任何规则判定。
 *
 * <p>从 {@code Round} 抽出来的第一块职责。抽它的理由很直接：牌山的账是这个项目里最容易被
 * 写错、而且**错了也不报错**的一块 —— 少一张 / 多一张都不会编译失败，只会让点数在整场
 * 模拟里悄悄漂移。抽成独立的类之后，「账」只有一处实现，可以脱离牌桌直接断言
 * （见 {@code SelfTest.wallTests}）。
 *
 * <h2>两张牌山的账必须分开记</h2>
 * <pre>
 *   tiles[0 .. 121]   可摸牌山（122 张）
 *   tiles[122 .. 135] 王牌（14 张）＝ dead[0..3] 岭上 ＋ dead[4..8] 表宝牌 ＋ dead[9..13] 里宝
 * </pre>
 *
 * <ul>
 *   <li><b>开杠的那一刻</b>：牌山末尾一张被移进王牌补位（{@link #onKan()} → {@code liveEnd--}），
 *       所以 {@link #tilesLeft()} 减 1；</li>
 *   <li><b>杠后岭上摸牌</b>：那张取自王牌（{@link #drawRinshan()}），
 *       <b>{@link #tilesLeft()} 不动</b>，只有 {@link #rinshanLeft()} 减 1。</li>
 * </ul>
 *
 * 所以「岭上牌有没有被摸走」**只能看 {@link #rinshanLeft()}**（报文里的 {@code dead_wall_left}）——
 * 曾经的 bug 就是只在小局开始时下发一次它，界面上的「岭上 N」整局停在 4（用户报障）。
 *
 * <p>岭上只有 {@link #RINSHAN_TILES} 张，因此**一局最多开 4 次杠**；这条规则的两个闸门
 * （{@code rinshanLeft() > 0} 且 {@code kanCount < 4}）在 {@code Round.canKan()} 里合流。
 *
 * <p>非线程安全：一局只由一张牌桌的线程串行推进（见 AGENTS §6.4 ⑥ 服务端并发模型）。
 */
public final class Wall {

    /** 可摸牌山张数：136 − 14（王牌）。 */
    public static final int LIVE_TILES = 122;

    /** 岭上牌张数，同时也是**一局开杠次数上限**。 */
    public static final int RINSHAN_TILES = 4;

    /** 王牌张数。 */
    public static final int DEAD_TILES = 14;

    /** 宝牌指示牌最多翻几张：1 张开局 + 4 次杠各 1 张。 */
    public static final int DORA_MAX = 5;

    // 王牌里的三段。下标含义固定，改这里会同时影响宝牌与岭上，别单改一处。
    private static final int SEG_RINSHAN = 0;   // [0,4)   岭上
    private static final int SEG_DORA = 4;      // [4,9)   表宝牌指示牌
    private static final int SEG_URA = 9;       // [9,14)  里宝指示牌

    private final int[] tiles;                  // 洗好的 136 张
    private final int[] dead = new int[DEAD_TILES];

    /** 配牌阶段已经按顺序取走的张数（配牌不走可摸牌山）。 */
    private int dealt;
    /** 下一张可摸的牌山下标。 */
    private int livePos;
    /** 可摸牌山的实际末尾：每开一次杠，末尾前移一位（那张被移进王牌补位）。 */
    private int liveEnd = LIVE_TILES;
    /** 已经摸走的岭上牌张数。 */
    private int rinshanPos;
    /** 已翻开的宝牌指示牌张数（开局 1 张）。 */
    private int doraRevealed = 1;

    /**
     * 洗牌并切出王牌。
     *
     * @param seed  牌局种子；同一 seed 得到完全相同的牌山（自检与重连复现都靠它）
     * @param rules 本局规则；只用来决定**赤宝牌张数**（见 {@link #stripRedFives}）
     */
    public Wall(long seed, Rules rules) {
        List<Integer> all = new ArrayList<>(Tiles.TILE_COUNT);
        for (int id = 0; id < Tiles.TILE_COUNT; id++) {
            all.add(id);
        }
        Collections.shuffle(all, new Random(seed));
        tiles = new int[Tiles.TILE_COUNT];
        for (int i = 0; i < all.size(); i++) {
            tiles[i] = all.get(i);
        }
        stripRedFives(tiles, rules);
        System.arraycopy(tiles, LIVE_TILES, dead, 0, DEAD_TILES);
    }

    /**
     * `rules.aka == 0` 时**在发牌阶段就把赤五换成普通五**。
     *
     * <p>牌 id 编码把「赤五」固定在 copy 0（见 {@link Tiles#isRedId}），所以"这一局不发赤五"
     * 只能把那张的 id 改写成同 kind 的普通五。改写后同一 kind 会出现两个相同的 id ——
     * 对本仓库的所有用法都安全：手牌/牌河只按值增删（`remove((Integer) id)` 去掉一个），
     * 判役只按 kind 看，客户端也只按牌码显示 —— 于是**"看到的"与"计分的"一致**，
     * 不会出现"画着赤五却不给赤宝牌番数"的那种自相矛盾。
     *
     * <p>`aka == 4`（两张赤五筒）用这套编码表达不了，仍按 3 张处理（见 NOTES §10 已知限制）。
     */
    private static void stripRedFives(int[] tiles, Rules rules) {
        if (rules == null || rules.aka > 0) {
            return;
        }
        for (int i = 0; i < tiles.length; i++) {
            if (Tiles.isRedId(tiles[i])) {
                tiles[i] = Tiles.id(Tiles.kind(tiles[i]), 1);
            }
        }
    }

    // ================================================================= 配牌

    /**
     * 配牌：按顺序取一张。
     *
     * <p>配牌**不经过可摸牌山**（{@link #livePos} 不动），否则会与第一巡的摸牌撞车。
     */
    public int deal() {
        return tiles[dealt++];
    }

    /**
     * 配牌结束：把可摸牌山的起点钉在配牌之后。
     *
     * <p>庄家起手多发的那张（第 14 张）也算在 {@link #dealt} 里 —— 它已经进手牌了，
     * 第一巡不能再摸（否则起手 15 张，见 AGENTS §2.3-4）。
     */
    public void finishDealing() {
        livePos = dealt;
    }

    // ================================================================= 摸牌

    /** 从牌山摸一张（普通摸牌）。调用方负责先确认还有牌。 */
    public int draw() {
        return tiles[livePos++];
    }

    /**
     * 从岭上摸一张（杠后）。
     *
     * <p>⚠ 摸完 {@link #tilesLeft()} **不动** —— 这张来自王牌，不在可摸牌山里。
     * 「岭上还有没有」只看 {@link #rinshanLeft()}。
     */
    public int drawRinshan() {
        return dead[SEG_RINSHAN + rinshanPos++];
    }

    /**
     * 开一次杠：牌山末尾一张被移进王牌补位。
     *
     * <p>⚠ 只动可摸牌山（{@code liveEnd--}），**不动** {@link #rinshanLeft()} ——
     * 岭上那张是在 {@link #drawRinshan()} 时才少一张的。两条账分开记。
     */
    public void onKan() {
        liveEnd--;
    }

    /** 开杠后翻一张新的宝牌指示牌（是否翻由规则开关决定，5 张封顶在这里）。 */
    public void revealDora() {
        if (doraRevealed < DORA_MAX) {
            doraRevealed++;
        }
    }

    // ================================================================= 账

    /** 可摸牌山还剩几张（报文里的 {@code tiles_left}）。 */
    public int tilesLeft() {
        return Math.max(0, liveEnd - livePos);
    }

    /** 这一张是不是海底/河底（摸到 / 打出它之后就荒牌流局）。 */
    public boolean atLastLiveTile() {
        return livePos >= liveEnd;
    }

    /**
     * 王牌里还剩几张岭上牌（报文里的 {@code dead_wall_left}，4→3→2→1）。
     *
     * <p>**每次摸牌都要下发**：只在小局开始发一次的话，界面上的「岭上 N」整局不动。
     */
    public int rinshanLeft() {
        return RINSHAN_TILES - rinshanPos;
    }

    /** 已翻开的宝牌指示牌张数（1 ~ {@link #DORA_MAX}）。 */
    public int doraCount() {
        return doraRevealed;
    }

    /** 表宝牌指示牌（按已翻开的张数）。 */
    public List<Integer> doraIndicators() {
        return indicators(SEG_DORA);
    }

    /** 里宝指示牌（张数与表宝牌一致）。 */
    public List<Integer> uraIndicators() {
        return indicators(SEG_URA);
    }

    private List<Integer> indicators(int base) {
        List<Integer> l = new ArrayList<>(doraRevealed);
        for (int i = 0; i < doraRevealed; i++) {
            l.add(Tiles.kind(dead[base + i]));
        }
        return l;
    }

    // ================================================================= 自检钩子

    /** 供自检：把「已摸走的岭上牌数」直接设成 n（验证岭上摸完后开杠闸门会关上）。 */
    public void debugSetRinshanUsed(int n) {
        rinshanPos = Math.max(0, Math.min(RINSHAN_TILES, n));
    }

    /** 供自检：读王牌原始一张（校验 4 张岭上 / 5 张表宝牌 / 5 张里宝的切分）。 */
    int debugDeadTile(int i) {
        return dead[i];
    }

    /**
     * 供自检：整副牌山（136 张，含王牌）的拷贝。
     * 用来钉住「牌张构成」类不变量 —— 赤五张数、以及每种牌恒为 4 张。
     */
    public int[] debugAllTiles() {
        return tiles.clone();
    }
}
