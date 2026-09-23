package mahjong.ai;

import java.util.List;
import java.util.Map;
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

    /** 按名字取内置工厂（CLI 用）。
     *
     * <p>`net:<权重文件>` = 进程内神经网络（B 形态，见 {@link NeuralPolicy}）；
     * `net:<权重文件>@<α>` = **P5b 混合**（学生 logits + α·老师先验，见 {@link #hybrid}）。
     * α 缺省 0 = 纯网络（与加这个语法之前**逐决策相同**）。
     */
    public static PolicyFactory byName(String name) {
        String n = name == null ? "" : name;
        if (n.regionMatches(true, 0, "net:", 0, 4)) {
            String rest = n.substring(4);
            int at = rest.lastIndexOf('@');
            if (at > 0 && at < rest.length() - 1) {
                float alpha;
                try {
                    alpha = Float.parseFloat(rest.substring(at + 1).trim());
                } catch (NumberFormatException e) {
                    throw new IllegalArgumentException("先验权重不是数："
                            + rest.substring(at + 1) + "（写法 net:<权重文件>@<α>）", e);
                }
                return net(rest.substring(0, at), alpha);
            }
            return net(rest);
        }
        switch (n.toLowerCase()) {
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

    /**
     * 进程内神经网络策略：加载权重（**构造期**就校验魔数/格式/特征维度 —— 坏了立刻报错，
     * 而不是打到一半才发现），之后每局复用同一实例（它无状态）。
     *
     * <p>动作仍走 {@link #fromAction} 的三道保护：不在本次 `legal` 里 / 返回 null / 抛异常，
     * 一律退回内置教师机器人。
     */
    public static PolicyFactory net(String weightsPath) {
        return net(weightsPath, 0f);
    }

    /** 带先验权重的网络策略（`α <= 0` 时**走原来那条路**，保证 `net:x@0` 与 `net:x` 行为一致）。 */
    public static PolicyFactory net(String weightsPath, float alpha) {
        final NeuralPolicy policy;
        try {
            policy = NeuralPolicy.load(java.nio.file.Path.of(weightsPath));
        } catch (Exception e) {
            throw new IllegalArgumentException("加载神经网络权重失败：" + weightsPath
                    + " —— " + e.getMessage(), e);
        }
        if (!(alpha > 0f)) {
            return (seat, gameSeed) -> fromAction(policy);
        }
        final Policy combined = hybrid(policy, alpha);
        return (seat, gameSeed) -> combined;
    }

    /**
     * **P5b 混合策略**：`argmax(student(obs) + α·1[该候选 == 老师动作])`。
     *
     * <p>⚠ 它是 {@link Policy} 级组合（与 {@link #TEACHER} 同级），而**不是** {@link ActionPolicy}：
     * teacher 先验必须调 {@code Bot.decide(Round, …)}，而 {@code ActionPolicy} 是**故意拿不到
     * Round** 的（AGENTS §6.5 的反作弊口径）。把组合放在 Policy 这一层，网络本身仍然只看
     * {@link Observation}；老师自己只用 Round 的公开辅助方法，所以两边都没开新的作弊口子。
     *
     * <p>兜底：网络返回 null / 非法 / 抛异常时**直接用老师这一次的回包**（不再多算一遍 Bot）。
     */
    public static Policy hybrid(NeuralPolicy net, float alpha) {
        return d -> {
            Action teacher = null;
            Map<String, Object> tcmd;
            try {
                tcmd = Bot.decide(d.round, d.obs.seat, d.kind, d.options, d.extra);
                teacher = Action.resolve(tcmd, d.legal());
            } catch (RuntimeException e) {
                tcmd = null;                                    // 老师都炸了：只能靠网络
            }
            try {
                Action a = net.chooseWithPrior(d, teacher, alpha);
                if (a != null && d.isLegal(a)) {
                    return a.toCmd();
                }
            } catch (RuntimeException e) {
                // 落到下面的兜底
            }
            if (tcmd != null) {
                return tcmd;
            }
            return Bot.decide(d.round, d.obs.seat, d.kind, d.options, d.extra);
        };
    }
}
