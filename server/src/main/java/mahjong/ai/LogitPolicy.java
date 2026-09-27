package mahjong.ai;

import java.util.List;
import java.util.Map;
import java.util.Random;

/**
 * "给定 obs，逐候选打分"的**共同接口**（v3 的 {@link NeuralPolicy} 与 v4 的 {@link V4Policy} 都实现它）。
 *
 * <p>为什么要有它：{@link Policies} 的 P5b 混合（teacher 先验 α）与 P4 温度采样（T）是**策略级**
 * 组合，不该知道底下是几层网络。抽成接口之后 `net:<权重文件>[@α][#T]` 这一条策略串对
 * **两代权重同样成立**，而且"选哪一条"的破平/采样细节只有 {@link Logits} 一份实现。
 *
 * <p>⚠ 实现方必须是 {@link ActionPolicy} 语义：只看 {@link Observation}（`json` + `keys`），
 * 拿不到 {@link Decision#round}。这是 AGENTS §6.5 的反作弊口径，别在这里加"看一眼手牌"的钩子。
 */
public interface LogitPolicy extends ActionPolicy {

    /** 带先验的 argmax（具体实现在 {@link NeuralPolicy} / {@link V4Policy} 里）。 */
    int chooseIndex(Map<String, Object> json, List<String> keys, int priorIndex, float alpha);

    /** 带先验的温度采样（`temp <= 0` 时实现方**必须**走 argmax，与 {@link #chooseIndex} 同一条路）。 */
    int sampleIndex(Map<String, Object> json, List<String> keys, int priorIndex, float alpha,
                    float temp, Random rng);

    /**
     * 决策级入口：`argmax(logits + α·1[候选 == 老师动作])`。
     *
     * <p>契约：返回 {@code null} 表示"这一次没有可用选择"（由调用方兜底），
     * 但**合法候选为空**时返回 {@code pass}（与 v3 原行为一致 —— 空 legal 不该抛异常）。
     */
    default Action chooseWithPrior(Decision d, Action teacher, float alpha) {
        List<Action> legal = d.legal();
        if (legal.isEmpty()) {
            return Action.of(Action.PASS);
        }
        int prior = (teacher == null || alpha <= 0f) ? -1 : d.obs.indexOf(teacher);
        int best = chooseIndex(d.obs.toJson(), d.obs.legalKeys(), prior, alpha);
        return best < 0 ? Action.of(Action.PASS) : legal.get(best);
    }

    /**
     * 决策级入口：按温度采样（同时带 teacher 先验）。
     *
     * <p>⚠ 随机源由**调用方**给：{@link Policies#net(String, float, float)} 在**每局**用
     * `(seat, gameSeed)` 派生一个新 {@link Random}。自对弈"同种子逐事件可复现"这条硬性质
     * （AGENTS §6.5）就靠它 —— 跨局共享一个 RNG、或按 worker 线程共享，都会破坏它。
     */
    default Action chooseSampled(Decision d, Action teacher, float alpha, float temp, Random rng) {
        List<Action> legal = d.legal();
        if (legal.isEmpty()) {
            return Action.of(Action.PASS);
        }
        int prior = (teacher == null || alpha <= 0f) ? -1 : d.obs.indexOf(teacher);
        int pick = sampleIndex(d.obs.toJson(), d.obs.legalKeys(), prior, alpha, temp, rng);
        return pick < 0 ? Action.of(Action.PASS) : legal.get(pick);
    }
}
