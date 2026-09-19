package mahjong.ai;

import java.util.List;
import java.util.Map;

import mahjong.game.Round;

/**
 * 一次**决策机会**：合法信息集观测 + 本次询问的原始选项 + 合法动作集。
 *
 * <p>{@link #round} 只给两类使用者：① 内置 teacher（{@code Bot}）—— 它按 {@code Round} 的公开
 * 辅助方法（{@code concealCounts} / {@code melds} / {@code waitKinds}）算牌效；
 * ② 记录 / 调试。**外部策略（尤其 ML 策略）不许读它** —— 那是绕过信息集作弊；
 * 而 {@link ActionPolicy} 这个更干净的接口根本拿不到它（见 {@link Policies#fromAction}）。
 */
public final class Decision {

    /** 合法信息集观测：策略的**唯一**合法输入。 */
    public final Observation obs;
    /** ⚠ 含全部隐藏信息（别家手牌、牌山顺序），仅供内置 teacher 与记录用。 */
    public final Round round;
    /** {@code "turn"} / {@code "claim"}（= {@link Observation#kind}）。 */
    public final String kind;
    /** 服务端下发的原始选项（动作空间的来源）。 */
    public final List<Map<String, Object>> options;
    /** 询问附带数据：自家回合 = 整条 ask 报文；鸣牌 = {@code {from,tile,win_note?}}。 */
    public final Map<String, Object> extra;

    public Decision(Observation obs, Round round, String kind,
                    List<Map<String, Object>> options, Map<String, Object> extra) {
        this.obs = obs;
        this.round = round;
        this.kind = kind;
        this.options = options == null ? List.of() : options;
        this.extra = extra;
    }

    public int seat() {
        return obs.seat;
    }

    /** 本次询问的全部合法动作（= {@link Action#enumerate}）。 */
    public List<Action> legal() {
        return obs.legal;
    }

    public boolean isLegal(Action a) {
        return a != null && obs.indexOf(a) >= 0;
    }

    @Override
    public String toString() {
        return "Decision(seat=" + obs.seat + ", kind=" + kind + ", legal=" + obs.legal + ")";
    }
}
