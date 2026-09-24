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
     * `net:<权重文件>@<α>` = **P5b 混合**（学生 logits + α·老师先验，见 {@link #hybrid}）；
     * `net:<权重文件>@<α>#<T>` = **P4 探索**（再按温度 T 从 softmax 采样，见
     * {@link NeuralPolicy#chooseSampled}）。两个后缀都可省：α 缺省 0 = 纯网络、
     * T 缺省 0 = 贪心 —— 与加这个语法之前**逐决策相同**。
     */
    public static PolicyFactory byName(String name) {
        String n = name == null ? "" : name;
        if (n.regionMatches(true, 0, "net:", 0, 4)) {
            String rest = n.substring(4);
            // 先剥温度（`#T`）再剥先验（`@α`）：文法是 net:<权重文件>[@<α>][#<T>]，
            // **顺序固定**，因为权重文件的 Windows 路径里可能带 `#`（把 `#` 放后面才唯一）
            float temp = 0f;
            int hash = rest.lastIndexOf('#');
            if (hash > 0 && hash < rest.length() - 1) {
                try {
                    temp = Float.parseFloat(rest.substring(hash + 1).trim());
                } catch (NumberFormatException e) {
                    throw new IllegalArgumentException("采样温度不是数："
                            + rest.substring(hash + 1).trim()
                            + "（写法 net:<权重文件>[@<α>][#<T>]）", e);
                }
                rest = rest.substring(0, hash);
            }
            int at = rest.lastIndexOf('@');
            if (at > 0 && at < rest.length() - 1) {
                float alpha;
                try {
                    alpha = Float.parseFloat(rest.substring(at + 1).trim());
                } catch (NumberFormatException e) {
                    throw new IllegalArgumentException("先验权重不是数："
                            + rest.substring(at + 1) + "（写法 net:<权重文件>@<α>）", e);
                }
                return net(rest.substring(0, at), alpha, temp);
            }
            return net(rest, 0f, temp);
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
        return net(weightsPath, 0f, 0f);
    }

    /** 带先验权重的网络策略（`α <= 0` 时**走原来那条路**，保证 `net:x@0` 与 `net:x` 行为一致）。 */
    public static PolicyFactory net(String weightsPath, float alpha) {
        return net(weightsPath, alpha, 0f);
    }

    /**
     * 网络策略的完整形态：**先验 α + 采样温度 T**（`net:<权重文件>[@<α>][#<T>]`）。
     *
     * <p>`T <= 0` ⇒ 两条老的返回路径**原样保留**（纯网 / 混合），一个字节都不变；
     * `T > 0` ⇒ 每局用 `(seat, gameSeed)` 派生一个 {@link Random} 走温度采样（P4 的探索口）。
     * ⚠ 随机源必须**每局新建**：跨局共享会被并行 worker 的调度顺序影响，
     * "同种子可复现"立刻失效（见 {@link PolicyFactory}）。
     */
    public static PolicyFactory net(String weightsPath, float alpha, float temp) {
        final NeuralPolicy policy;
        try {
            policy = NeuralPolicy.load(java.nio.file.Path.of(weightsPath));
        } catch (Exception e) {
            throw new IllegalArgumentException("加载神经网络权重失败：" + weightsPath
                    + " —— " + e.getMessage(), e);
        }
        if (!(temp > 0f)) {
            if (!(alpha > 0f)) {
                return (seat, gameSeed) -> fromAction(policy);
            }
            final Policy combined = hybrid(policy, alpha);
            return (seat, gameSeed) -> combined;
        }
        return (seat, gameSeed) -> sampled(policy, alpha, temp, new Random(mixSeed(gameSeed, seat)));
    }

    /**
     * 温度采样版的 {@link Policy}（`α > 0` 时同时带 teacher 先验）。
     *
     * <p>兜底与 {@link #hybrid} 同口径：网络返回 null / 非法 / 抛异常 → 用老师这一次的回包
     * （`α = 0` 时就是重新问一次 {@code Bot}），**绝不给牌桌线程抛异常**（AGENTS §6.5）。
     */
    static Policy sampled(NeuralPolicy net, float alpha, float temp, Random rng) {
        return d -> {
            Action teacher = null;
            Map<String, Object> tcmd = null;
            if (alpha > 0f) {
                try {
                    tcmd = Bot.decide(d.round, d.obs.seat, d.kind, d.options, d.extra);
                    teacher = Action.resolve(tcmd, d.legal());
                } catch (RuntimeException e) {
                    tcmd = null;                                // 老师都炸了：只能靠网络
                }
            }
            try {
                Action a = net.chooseSampled(d, teacher, alpha, temp, rng);
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

    /**
     * `(gameSeed, seat)` → 采样随机源种子（SplitMix 混合）。
     *
     * <p>为什么不用 `gameSeed * 31 + seat` 那种线性式：相邻的 gameSeed（自对弈就是逐场 +1 递推的）
     * 在 `java.util.Random` 里只差一个常数偏移，头几个输出相关性明显 —— 采样探索最怕这个
     * （"每一局的探索模式一模一样"会让 on-policy 数据失去多样性）。混合一下代价可忽略。
     */
    public static long mixSeed(long gameSeed, int seat) {
        long z = gameSeed * 0x9E3779B97F4A7C15L + seat * 0xBF58476D1CE4E5B9L;
        z = (z ^ (z >>> 30)) * 0xBF58476D1CE4E5B9L;
        z = (z ^ (z >>> 27)) * 0x94D049BB133111EBL;
        return z ^ (z >>> 31);
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
