package mahjong.ai;

import java.util.List;
import java.util.Random;

import mahjong.bot.Bot;

/** 内置策略与适配器（训练接口的"标准件"）。 */
public final class Policies {

    private Policies() {
    }

    /**
     * 生产默认策略 = 内置牌效机器人。
     *
     * <p>它就是"用现有电脑玩家逻辑做预训练"里的 teacher：自对弈采数据时，用它产生行为标签
     * （行为克隆）。它自己**不读**别家手牌（只用 {@code Round} 的自家辅助方法），是诚实的。
     */
    public static final Policy TEACHER =
            d -> Bot.decide(d.round, d.obs.seat, d.kind, d.options, d.extra);

    /** 恒选第一个合法动作（最小的可跑基线；鸣牌询问里"第一个"往往是荣和/碰）。 */
    public static ActionPolicy firstLegal() {
        return d -> d.legal().isEmpty() ? Action.of(Action.PASS) : d.legal().get(0);
    }

    /** 能过就过，否则选第一个合法动作（"不鸣牌"基线）。 */
    public static ActionPolicy passFirst() {
        return d -> {
            for (Action a : d.legal()) {
                if (Action.PASS.equals(a.type)) {
                    return a;
                }
            }
            return d.legal().isEmpty() ? Action.of(Action.PASS) : d.legal().get(0);
        };
    }

    /**
     * 均匀随机（可复现）。
     *
     * <p>随机源在构造时给定：自对弈里必须**每局新建**一个实例，否则同一个实例跨局推进、
     * 并行 worker 下的取数顺序会变，同一 seed 就不再可复现（见 {@link PolicyFactory}）。
     */
    public static ActionPolicy random(long seed) {
        final Random rng = new Random(seed);
        return d -> {
            List<Action> legal = d.legal();
            return legal.isEmpty() ? Action.of(Action.PASS) : legal.get(rng.nextInt(legal.size()));
        };
    }

    // ------------------------------------------------------------------ 适配器

    /**
     * {@link ActionPolicy} → {@link Policy}：**这是训练侧唯一推荐的注入方式**。
     *
     * <p>三道保护：① 非法动作（不在本次 {@code legal} 里）退回内置机器人；
     * ② 返回 {@code null} 退回内置机器人；③ 抛异常退回内置机器人。
     * 三道都只影响"这一手"，不会让牌桌线程死掉。
     */
    public static Policy fromAction(ActionPolicy ap) {
        return d -> {
            Action a = null;
            try {
                a = ap.choose(d);
            } catch (RuntimeException e) {
                return Bot.decide(d.round, d.obs.seat, d.kind, d.options, d.extra);
            }
            if (a == null || !d.isLegal(a)) {
                return Bot.decide(d.round, d.obs.seat, d.kind, d.options, d.extra);
            }
            return a.toCmd();
        };
    }

    // ------------------------------------------------------------------ 工厂

    /** 每局一个策略实例（并行自对弈下"同 seed 可复现"的前提，见 {@link PolicyFactory}）。 */
    public static PolicyFactory teacher() {
        return (seat, gameSeed) -> TEACHER;
    }

    public static PolicyFactory firstLegalFactory() {
        return (seat, gameSeed) -> fromAction(firstLegal());
    }

    public static PolicyFactory passFirstFactory() {
        return (seat, gameSeed) -> fromAction(passFirst());
    }

    public static PolicyFactory randomFactory() {
        return (seat, gameSeed) -> fromAction(random(gameSeed * 31 + seat));
    }

    /** 按名字取内置工厂（CLI 用）。 */
    public static PolicyFactory byName(String name) {
        switch (name == null ? "" : name.toLowerCase()) {
            case "teacher":
            case "bot":
                return teacher();
            case "first":
                return firstLegalFactory();
            case "pass":
                return passFirstFactory();
            case "random":
                return randomFactory();
            default:
                throw new IllegalArgumentException("未知策略名: " + name);
        }
    }
}
