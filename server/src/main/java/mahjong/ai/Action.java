package mahjong.ai;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import mahjong.core.Tiles;
import mahjong.util.Json;

/**
 * 一个**可执行动作**的规范表示 —— 训练接口的动作空间。
 *
 * <p>三件事在这一层钉死，之后不许改（数据集与网络输出头都依赖它）：
 * <ol>
 *   <li>{@link #key()} —— 稳定字符串键。跨版本可复用，数据集里存的就是它。</li>
 *   <li>{@link #index()} —— 固定头的整数下标（{@code 0..FIXED_ACTIONS-1}）。吃/杠是参数化的，
 *       下标不固定，用 {@link #key()}（见下）。</li>
 *   <li>{@link #toCmd()} —— 交回给状态机的回包**形状**，与客户端报文逐字段一致
 *       （见 {@code docs/PROTOCOL.md}）。</li>
 * </ol>
 *
 * <h2>为什么打牌只需要牌码</h2>
 * 「摸切 / 手切」不必由策略声明：{@code Round.pickDiscardId} 是**按牌码**取牌的
 * （见 AGENTS §2.3-11），两张同码牌物理上等价。{@link #tsumogiri} 只是可选声明，
 * 不声明就走纯牌码查找 —— 策略永远不会因为"猜错摸切"而打出别的牌。
 *
 * <h2>固定头布局</h2>
 * <pre>
 *   0 .. 36   打牌（34 种牌 + 赤 5m/5p/5s 三个槽）  key = discard:&lt;码&gt;
 *   37 .. 73  立直宣言（同上的 37 个槽）            key = riichi:&lt;码&gt;
 *   74        自摸     75 荣和     77 过     78 九种九牌     （76 保留：历史上是碰）
 * </pre>
 * 碰 / 吃 / 杠是**参数化**动作（一次询问里最多各有几种取法），所以做成"按本次 {@code options}
 * 枚举 + 掩码"、{@link #index()} 返回 {@code -1}，标识仍用 {@link #key()}。
 *
 * <p>⚠ **碰与大明杠的键里必须带"从手里取哪几张"**（{@code pon:5p+5p} /
 * {@code kan:daiminkan:5s+5s+0s}）：手里同时有赤五与普通五时，服务端为两种取法**各下发一条**选项，
 * 它们是两个不同的合法动作。旧写法把两者都折成裸 {@code pon}，于是 {@code legal} 里出现重复键、
 * {@code chosen_index} 无从分辨（2026-09 被 {@code tools/selfplay-check.mjs} 抓出来）。
 * 裸 {@code pon} 只作为**老客户端 / 老数据集**的兼容形态保留：它按"普通牌优先"的默认取法执行，
 * 也就是本次 {@code legal} 里的第一条 pon（见 {@link #resolve}）。
 */
public final class Action {

    public static final String DISCARD = "discard";
    public static final String RIICHI = "riichi";
    public static final String TSUMO = "tsumo";
    public static final String RON = "ron";
    public static final String PON = "pon";
    public static final String CHI = "chi";
    public static final String KAN = "kan";
    public static final String KYUUSHU = "kyuushu";
    public static final String PASS = "pass";

    /** 打牌头的槽数（34 种牌 + 赤 5m/5p/5s）。 */
    public static final int TILE_SLOTS = 37;
    public static final int DISCARD_BASE = 0;
    public static final int RIICHI_BASE = TILE_SLOTS;          // 37
    public static final int TSUMO_ID = 2 * TILE_SLOTS;         // 74
    public static final int RON_ID = TSUMO_ID + 1;
    public static final int PON_ID = TSUMO_ID + 2;
    public static final int PASS_ID = TSUMO_ID + 3;
    public static final int KYUUSHU_ID = TSUMO_ID + 4;
    /** 固定头大小（吃/杠不在其中，它们是按询问枚举的）。 */
    public static final int FIXED_ACTIONS = KYUUSHU_ID + 1;    // 79

    /** 动作类型。 */
    public final String type;
    /** 牌码（{@code "5m"} / {@code "0p"}）；吃与九种九牌等为 {@code null}。 */
    public final String tile;
    /** 吃 / 碰 / 大明杠用的那几张**手里牌**（按槽位升序）；其余为 {@code null}。 */
    public final List<String> tiles;
    /** 杠的种类：{@code ankan} / {@code kakan} / {@code daiminkan}；非杠为 {@code null}。 */
    public final String kanKind;
    /** 仅打牌有意义：声明摸切。默认不声明（服务端按牌码取牌，见类注释）。 */
    public final boolean tsumogiri;

    private Action(String type, String tile, List<String> tiles, String kanKind, boolean tsumogiri) {
        this.type = type;
        this.tile = tile;
        this.tiles = tiles == null ? null : List.copyOf(tiles);
        this.kanKind = kanKind;
        this.tsumogiri = tsumogiri;
    }

    // ------------------------------------------------------------------ 构造

    public static Action of(String type) {
        return new Action(type, null, null, null, false);
    }

    public static Action discard(String tile) {
        return new Action(DISCARD, tile, null, null, false);
    }

    public static Action discard(String tile, boolean tsumogiri) {
        return new Action(DISCARD, tile, null, null, tsumogiri);
    }

    public static Action riichi(String tile) {
        return new Action(RIICHI, tile, null, null, false);
    }

    public static Action chi(List<String> two) {
        return new Action(CHI, null, two, null, false);
    }

    /** 碰：{@code two} = 从手里取的两张（按槽位升序；赤五写作 {@code "0p"}）。 */
    public static Action pon(List<String> two) {
        return new Action(PON, null, two, null, false);
    }

    public static Action kan(String kanKind, String tile) {
        return new Action(KAN, tile, null, kanKind, false);
    }

    /** 大明杠：{@code handTiles} = 从手里取的三张（按槽位升序）；暗杠/加杠传 {@code null}。 */
    public static Action kan(String kanKind, String tile, List<String> handTiles) {
        return new Action(KAN, tile, handTiles, kanKind, false);
    }

    // ------------------------------------------------------------------ 枚举

    /**
     * 把一次询问下发的 {@code options} 展开成**具体动作列表**（顺序与 options 一致）。
     *
     * <p>这就是"合法动作集 + 掩码"：训练侧不需要知道任何规则，展开出来的每一项都是合法的，
     * 且 {@link #toCmd()} 一定被状态机接受。
     */
    public static List<Action> enumerate(List<Map<String, Object>> options) {
        List<Action> out = new ArrayList<>();
        if (options == null) {
            return out;
        }
        for (Map<String, Object> o : options) {
            String type = Json.str(o, "type", "");
            switch (type) {
                case DISCARD: {
                    List<Object> ts = Json.list(o, "tiles");
                    if (ts != null) {
                        for (Object t : ts) {
                            out.add(discard(String.valueOf(t)));
                        }
                    }
                    break;
                }
                case RIICHI: {
                    List<Object> ts = Json.list(o, "tiles");
                    if (ts != null) {
                        for (Object t : ts) {
                            out.add(riichi(String.valueOf(t)));
                        }
                    }
                    break;
                }
                case KAN: {
                    List<Object> ks = Json.list(o, "kans");
                    if (ks != null) {
                        for (Object k : ks) {
                            Map<String, Object> km = Json.asObj(k);
                            if (km == null) {
                                continue;
                            }
                            // 大明杠会带 `tiles`（手里取哪三张）；暗杠/加杠不带（那两种没有取法可挑）
                            out.add(kan(Json.str(km, "kind", "ankan"), Json.str(km, "tile", ""),
                                    canonical(Json.strList(km, "tiles"), 3)));
                        }
                    }
                    break;
                }
                case CHI: {
                    List<Object> sets = Json.list(o, "sets");
                    if (sets != null) {
                        for (Object s : sets) {
                            List<Object> pair = Json.asArr(s);
                            if (pair == null || pair.size() != 2) {
                                continue;
                            }
                            out.add(chi(List.of(String.valueOf(pair.get(0)), String.valueOf(pair.get(1)))));
                        }
                    }
                    break;
                }
                case PON: {
                    // 取法进键：手里有赤五时，"用普通五碰"与"用赤五碰"是两个不同的合法动作。
                    // `tiles` 缺失 = 老服务端下发的裸 pon（兼容形态，键就是 "pon"）。
                    List<String> two = canonical(Json.strList(o, "tiles"), 2);
                    out.add(two == null ? of(PON) : pon(two));
                    break;
                }
                case TSUMO:
                case RON:
                case PASS:
                case KYUUSHU:
                    out.add(of(type));
                    break;
                default:
                    // 认不出的选项**不进动作空间**（新协议字段不该让老训练器乱猜）
                    break;
            }
        }
        return out;
    }

    // ------------------------------------------------------------------ 键与下标

    /** 稳定字符串键（数据集 / 外部训练器 / 断言都用它）。 */
    public String key() {
        switch (type) {
            case DISCARD:
                return tsumogiri ? "discard:" + tile + "/tsumogiri" : "discard:" + tile;
            case RIICHI:
                return "riichi:" + tile;
            case PON:
                return tiles == null ? PON : "pon:" + tiles.get(0) + "+" + tiles.get(1);
            case KAN:
                return tiles == null ? "kan:" + kanKind + ":" + tile
                                     : "kan:" + kanKind + ":" + String.join("+", tiles);
            case CHI:
                return "chi:" + tiles.get(0) + "+" + tiles.get(1);
            default:
                return type;
        }
    }

    /** 固定头下标；碰 / 吃 / 杠返回 {@code -1}（它们是按询问枚举的参数化动作）。 */
    public int index() {
        int t = tileIndex(tile);
        switch (type) {
            case DISCARD:
                return t < 0 ? -1 : DISCARD_BASE + t;
            case RIICHI:
                return t < 0 ? -1 : RIICHI_BASE + t;
            case TSUMO:
                return TSUMO_ID;
            case RON:
                return RON_ID;
            case PON:
                // 带取法的碰是参数化动作（一次询问里可能 1~2 种取法），没有固定槽；
                // 裸 pon 只来自老客户端/老数据集，保留历史槽位 76
                return tiles == null ? PON_ID : -1;
            case PASS:
                return PASS_ID;
            case KYUUSHU:
                return KYUUSHU_ID;
            default:
                return -1;
        }
    }

    /** 牌码 → 槽位（34 种牌 + 赤 5m/5p/5s = 0..36）；认不出返回 -1。 */
    public static int tileIndex(String code) {
        if (code == null || code.length() != 2) {
            return -1;
        }
        int kind = Tiles.parseKind(code);
        if (kind < 0) {
            return -1;
        }
        if (Tiles.isRedStr(code)) {
            // "0m"/"0p"/"0s" 的 kind 与普通 5 相同，所以另给三个槽位
            return 34 + Tiles.suit(kind);
        }
        return kind;
    }

    /** 槽位 → 牌码（{@link #tileIndex} 的逆）。 */
    public static String tileCode(int slot) {
        if (slot < 0 || slot >= TILE_SLOTS) {
            return null;
        }
        if (slot < 34) {
            return Tiles.kindToStr(slot);
        }
        return "0" + new char[]{'m', 'p', 's'}[slot - 34];
    }

    // ------------------------------------------------------------------ 回包与解析

    /** 交回状态机的回包（形状 = 客户端 cmd）。 */
    public Map<String, Object> toCmd() {
        Map<String, Object> cmd = Json.obj("type", type);
        switch (type) {
            case DISCARD:
                cmd.put("tile", tile);
                if (tsumogiri) {
                    cmd.put("tsumogiri", true);
                }
                break;
            case RIICHI:
                cmd.put("tile", tile);
                break;
            case KAN:
                cmd.put("kind", kanKind);
                cmd.put("tile", tile);
                if (tiles != null) {
                    cmd.put("tiles", new ArrayList<Object>(tiles));   // 大明杠的取法
                }
                break;
            case PON:
                if (tiles != null) {
                    cmd.put("tiles", new ArrayList<Object>(tiles));   // 碰的取法（赤五与否）
                }
                break;
            case CHI:
                cmd.put("tiles", new ArrayList<Object>(tiles));
                break;
            default:
                break;
        }
        return cmd;
    }

    /**
     * 回包 → 动作（记录 teacher 的选择、以及把任意 {@link Policy} 的输出翻成动作键时用）。
     *
     * <p>认不出的一律返回 {@code null}（**不要**猜一个默认动作：记录下来的标签必须是
     * 策略真的选了的那一个，宁可标记成"无标签"）。
     */
    public static Action fromCmd(Map<String, Object> cmd) {
        if (cmd == null) {
            return null;
        }
        String type = Json.str(cmd, "type", null);
        if (type == null) {
            return null;
        }
        switch (type) {
            case DISCARD: {
                String t = Json.str(cmd, "tile", null);
                return t == null || tileIndex(t) < 0 ? null
                        : discard(t, Json.bool(cmd, "tsumogiri", false));
            }
            case RIICHI: {
                String t = Json.str(cmd, "tile", null);
                return t == null || tileIndex(t) < 0 ? null : riichi(t);
            }
            case KAN: {
                String kind = Json.str(cmd, "kind", null);
                String t = Json.str(cmd, "tile", null);
                if (kind == null || t == null || tileIndex(t) < 0) {
                    return null;
                }
                return kan(kind, t, canonical(Json.strList(cmd, "tiles"), 3));
            }
            case PON: {
                // 不带 `tiles` = 老客户端/内置机器人的兼容形态；它真正对应的动作由 resolve 落位
                List<String> two = canonical(Json.strList(cmd, "tiles"), 2);
                return two == null ? of(PON) : pon(two);
            }
            case CHI: {
                List<String> two = Json.strList(cmd, "tiles");
                if (two == null || two.size() != 2 || tileIndex(two.get(0)) < 0
                        || tileIndex(two.get(1)) < 0) {
                    return null;
                }
                List<String> sorted = new ArrayList<>(two);
                sorted.sort((a, b) -> Integer.compare(tileIndex(a), tileIndex(b)));
                return chi(sorted);
            }
            case TSUMO:
            case RON:
            case PASS:
            case KYUUSHU:
                return of(type);
            default:
                return null;
        }
    }

    /** {@link #key()} 的逆；非法键返回 {@code null}。 */
    public static Action parse(String key) {
        if (key == null) {
            return null;
        }
        switch (key) {
            case TSUMO:
            case RON:
            case PON:
            case PASS:
            case KYUUSHU:
                return of(key);
            default:
                break;
        }
        if (key.startsWith("discard:")) {
            String rest = key.substring("discard:".length());
            boolean tsumo = rest.endsWith("/tsumogiri");
            String code = tsumo ? rest.substring(0, rest.length() - "/tsumogiri".length()) : rest;
            if (tileIndex(code) < 0) {
                return null;
            }
            return discard(code, tsumo);
        }
        if (key.startsWith("riichi:")) {
            String code = key.substring("riichi:".length());
            return tileIndex(code) < 0 ? null : riichi(code);
        }
        if (key.startsWith("pon:")) {
            List<String> two = canonical(splitPlus(key.substring("pon:".length())), 2);
            return two == null ? null : pon(two);
        }
        if (key.startsWith("kan:")) {
            String[] p = key.split(":");
            if (p.length != 3) {
                return null;
            }
            // 大明杠的第三段是"手里取哪三张"（带 `+`）；暗杠/加杠是单个牌码
            List<String> hand = canonical(splitPlus(p[2]), 3);
            if (hand != null) {
                return kan(p[1], Tiles.kindToStr(Tiles.parseKind(hand.get(0))), hand);
            }
            return tileIndex(p[2]) < 0 ? null : kan(p[1], p[2]);
        }
        if (key.startsWith("chi:")) {
            String[] p = key.substring("chi:".length()).split("\\+");
            if (p.length != 2 || tileIndex(p[0]) < 0 || tileIndex(p[1]) < 0) {
                return null;
            }
            return chi(List.of(p[0], p[1]));
        }
        return null;
    }

    @Override
    public String toString() {
        return key();
    }

    // ------------------------------------------------------------------ 取法与回包落位

    /**
     * 把"从手里取哪几张"规范化：**按槽位升序**、张数与牌码都必须合法，否则返回 {@code null}。
     *
     * <p>顺序固定下来键才唯一 —— 服务端下发 `tiles` 的顺序（"不用赤"在前）与策略回包的顺序
     * 都不该改变动作身份。
     */
    private static List<String> canonical(List<String> codes, int need) {
        if (codes == null || codes.size() != need) {
            return null;
        }
        List<String> out = new ArrayList<>(codes);
        for (String c : out) {
            if (tileIndex(c) < 0) {
                return null;
            }
        }
        out.sort((a, b) -> Integer.compare(tileIndex(a), tileIndex(b)));
        return out;
    }

    private static List<String> splitPlus(String s) {
        return new ArrayList<>(List.of(s.split("\\+")));
    }

    /**
     * 把策略的**回包**解析成"本次 {@code legal} 里那一个"动作 —— 轨迹记录的 `chosen` 用它。
     *
     * <p>为什么不能只用 {@link #fromCmd}：策略可以回**部分指定**的包（裸 {@code pon}、
     * 不带 `tiles` 的大明杠），服务端随后按**普通牌优先的默认取法**执行
     * （{@code Round.pickAuto}）—— 而那恰好就是本次选项里的**第一条**（{@code Round.akaVariants}
     * 的顺序是"不用赤 → 用赤"）。若记成裸键，它会与 `legal`（只含带取法的键）对不上，
     * 于是 `chosen ∈ legal` 与 `chosen_index` 两条不变式同时失真。
     *
     * <p>规则：**先精确匹配；匹配不上才按"同类型的第一条"落位**，且只对**碰 / 杠**开这个口子
     * —— 其余动作必须精确匹配，绝不"挑一个像的"（那会把错标签写进数据集）。
     *
     * @return 本次 {@code legal} 里的动作；无法归位返回 {@code null}（记"无标签"，不猜）
     */
    public static Action resolve(Map<String, Object> cmd, List<Action> legal) {
        if (cmd == null || legal == null || legal.isEmpty()) {
            return null;
        }
        final Action exact = fromCmd(cmd);
        if (exact != null) {
            for (Action a : legal) {
                if (a.key().equals(exact.key())) {
                    return a;
                }
            }
        }
        final String type = Json.str(cmd, "type", null);
        if (!PON.equals(type) && !KAN.equals(type)) {
            return null;
        }
        // 一次询问只针对**一张**舍张，所以同 kind 的杠只可能有一种；取第一条即默认取法
        final String kind = KAN.equals(type) ? Json.str(cmd, "kind", null) : null;
        for (Action a : legal) {
            if (!type.equals(a.type)) {
                continue;
            }
            if (kind != null && !kind.equals(a.kanKind)) {
                continue;
            }
            return a;
        }
        return null;
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof Action && key().equals(((Action) o).key());
    }

    @Override
    public int hashCode() {
        return key().hashCode();
    }
}
