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
 *   74        自摸     75 荣和     76 碰     77 过     78 九种九牌
 * </pre>
 * 吃（{@code chi:1m+2m}）与杠（{@code kan:ankan:7z}）是**参数化**动作：一次询问里最多各几种，
 * 所以做成"按本次 {@code options} 枚举 + 掩码"，标识仍用 {@link #key()}。
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
    /** 吃用的另外两张（按牌种升序）；其余为 {@code null}。 */
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

    public static Action kan(String kanKind, String tile) {
        return new Action(KAN, tile, null, kanKind, false);
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
                            out.add(kan(Json.str(km, "kind", "ankan"), Json.str(km, "tile", "")));
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
                case TSUMO:
                case RON:
                case PON:
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
            case KAN:
                return "kan:" + kanKind + ":" + tile;
            case CHI:
                return "chi:" + tiles.get(0) + "+" + tiles.get(1);
            default:
                return type;
        }
    }

    /** 固定头下标；吃/杠返回 {@code -1}（它们是按询问枚举的参数化动作）。 */
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
                return PON_ID;
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
                return kind == null || t == null || tileIndex(t) < 0 ? null : kan(kind, t);
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
            case PON:
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
        if (key.startsWith("kan:")) {
            String[] p = key.split(":");
            if (p.length != 3 || tileIndex(p[2]) < 0) {
                return null;
            }
            return kan(p[1], p[2]);
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

    @Override
    public boolean equals(Object o) {
        return o instanceof Action && key().equals(((Action) o).key());
    }

    @Override
    public int hashCode() {
        return key().hashCode();
    }
}
