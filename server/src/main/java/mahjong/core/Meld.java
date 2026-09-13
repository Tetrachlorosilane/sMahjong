package mahjong.core;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import mahjong.util.Json;

/** 副露（面子）。 */
public final class Meld {

    public enum Kind {
        CHI, PON, DAIMINKAN, ANKAN, KAKAN;

        public static Kind of(String s) {
            if (s == null) {
                return null;
            }
            switch (s.toLowerCase()) {
                case "chi": return CHI;
                case "pon": return PON;
                case "daiminkan": return DAIMINKAN;
                case "ankan": return ANKAN;
                case "kakan": return KAKAN;
                default: return null;
            }
        }

        public String wire() {
            return name().toLowerCase();
        }
    }

    public final Kind kind;
    /** 面子中的牌 id（顺子已按升序排列）。 */
    public final int[] tiles;
    /** 被鸣牌的玩家座位；暗杠时等于自己。 */
    public final int from;
    /** 被鸣的那张牌 id（加杠时为加上的第 4 张）。 */
    public final int calledId;

    public Meld(Kind kind, int[] tiles, int from, int calledId) {
        this.kind = kind;
        this.tiles = tiles;
        this.from = from;
        this.calledId = calledId;
    }

    /** 是否为明面（破坏门前清）。 */
    public boolean open() {
        return kind != Kind.ANKAN;
    }

    public boolean isKan() {
        return kind == Kind.DAIMINKAN || kind == Kind.ANKAN || kind == Kind.KAKAN;
    }

    /** 顺子？ */
    public boolean isRun() {
        return kind == Kind.CHI;
    }

    /** 面子对应的 kind（顺子取最小牌；刻子/杠取刻子牌）。 */
    public int baseKind() {
        int min = 99;
        for (int t : tiles) {
            min = Math.min(min, Tiles.kind(t));
        }
        return min;
    }

    public int kindCount() {
        return tiles.length;
    }

    public Map<String, Object> toJson() {
        List<Object> ts = new ArrayList<>();
        List<Object> akas = new ArrayList<>();
        for (int t : tiles) {
            ts.add(Tiles.toStr(t));
            akas.add(Tiles.isRedId(t));
        }
        return Json.obj(
                "kind", kind.wire(),
                "tiles", ts,
                "from", from,
                "called_tile", Tiles.toStr(calledId),
                "aka", akas);
    }
}
