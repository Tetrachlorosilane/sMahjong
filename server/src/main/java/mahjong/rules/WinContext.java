package mahjong.rules;

import java.util.List;

import mahjong.core.Rules;

/** 和牌时的上下文（不影响牌型、但影响役种成立的信息）。 */
public final class WinContext {

    public Rules rules = Rules.defaults();

    /** 和牌者座位 0..3。 */
    public int seat;
    public int dealerSeat;
    /** 场风 kind：27=东 28=南 29=西 30=北。 */
    public int roundWind = 27;

    public boolean tsumo;
    public boolean riichi;
    public boolean doubleRiichi;
    public boolean ippatsu;
    public boolean chankan;
    public boolean rinshan;
    public boolean haitei;
    public boolean houtei;
    public boolean tenhou;
    public boolean chiihou;
    public boolean renhou;
    /** 燕返：和了牌是放铳者的立直宣言牌（古役）。 */
    public boolean tsubame;
    /** 杠振：和了牌是他人开杠后打出的牌（古役）。 */
    public boolean kanburi;
    /** 流局满贯。 */
    public boolean nagashi;

    public int winKind;

    /** 宝牌指示牌 kind 列表（含杠宝牌）。 */
    public List<Integer> doraIndicators;
    /** 里宝牌指示牌 kind 列表。 */
    public List<Integer> uraIndicators;
    /** 手牌 + 副露的全部牌 id（用于数赤宝牌）。 */
    public List<Integer> allTileIds;

    public boolean menzen;

    public int seatWind() {
        return 27 + ((seat - dealerSeat + 4) % 4);
    }
}
