package mahjong.ai;

import java.util.Random;

/**
 * 逐候选 logit 上的两个**共享**算子：贪心取最大、按温度采样。
 *
 * <p>抽出来的理由：v3 的 {@link NeuralPolicy}（定长 MLP）与 v4 的 {@link V4Policy}（三塔 + 多头）
 * 是两套前向，但"拿到 logits 之后怎么选"必须**逐位一致** —— 否则同一份 P4 采样/ P5b 先验配置
 * 在两代权重上会有不同的并列破平与浮点尾巴行为。抽出来之前这段逻辑在 {@code NeuralPolicy} 里，
 * 现在两代共用它（判据：原有的 `samplingPolicyTests` / `hybridPolicyTests` 全部不变绿）。
 */
public final class Logits {

    /**
     * A− tie-break 的并列阈值（`ARCH-REDESIGN` §2.6）：有 130× 余量 —— 三端 `max|Δlogits|`
     * 实测 7.7e-6（A− 段 3 实测见 `ARCH-REDESIGN` §12）。
     */
    public static final float TAU = 1e-3f;

    private Logits() {
    }

    /**
     * 取最大值下标；并列取**最小下标**。
     *
     * <p>⚠ 原来是"严格大于才更新"（所以并列天然取最小下标）—— 别改成随机破平或 `>=`：
     * 自对弈"同种子逐事件可复现"会立刻失效。
     */
    public static int argmaxOf(float[] out) {
        int best = -1;
        float bestVal = Float.NEGATIVE_INFINITY;
        for (int i = 0; i < out.length; i++) {
            if (out[i] > bestVal) {
                bestVal = out[i];
                best = i;
            }
        }
        return best;
    }

    /**
     * **A− 的确定性 tie-break**（`ARCH-REDESIGN` §2.6 / `A-REFACTOR-IMPL-PLAN.md` 风险 2）。
     *
     * <p>规格与 `python/mahjong_ml/v4/policy.py:choose_index` **逐条同义**：
     * <ol>
     *   <li>并列 = `top1 − logit < tau` **且**与 argmax **同动作族**（`cand[0:9].argmax()`）；</li>
     *   <li>并列内部依次比较：① `effect` 的**进张数**（第 2 维，越大越好）② **牌码**
     *       （`cand[9:46].argmax()`，越小越好）③ 最小合法下标；</li>
     *   <li>没有任何并列 ⇒ 退回 {@link #argmaxOf}（`tau<=0` / 缺 `cand`/`effect` 时也走它）。</li>
     * </ol>
     *
     * <p>⛔ **不许**用浮点末位/随机破平：`torch.argmax` 的"第一个最大"是实现细节，不是规格。
     * ⚠ **归属 = caller**（`Heads` 拿不到候选语义）—— 所以这个 helper 只吃 logits + `cand` +
     * `effect`，不碰前向内部。
     *
     * @param logits `[n]` 策略头 logits（顺序 = `obs.legal`）
     * @param cand   `[n][C_CAND]` 候选特征（type one-hot 0..8、tile one-hot 9..45）
     * @param effect `[n*3]` 或 `[n][3]` 展平后的 `heads.effect`（第 2 维 = 进张数）
     * @param tau    并列阈值（A− 用 `1e-3`；有 130× 余量——三端 `max|Δlogits|` 实测 7.7e-6）
     */
    public static int argmaxTieBreak(float[] logits, float[][] cand, float[] effect, float tau) {
        int idx = argmaxOf(logits);
        if (idx < 0 || cand == null || effect == null || !(tau > 0f) || logits.length < 2) {
            return idx;
        }
        float top = logits[idx];
        int famBest = familyOf(cand[idx]);
        int codeBest = tileCodeOf(cand[idx]);
        double drawBest = effect.length >= (idx + 1) * 3 ? effect[idx * 3 + 1] : 0.0;
        int best = idx;
        for (int c = 0; c < logits.length && c < cand.length; c++) {
            if (!((top - logits[c]) < tau) || familyOf(cand[c]) != famBest) {
                continue;
            }
            double draw = effect.length >= (c + 1) * 3 ? effect[c * 3 + 1] : 0.0;
            int code = tileCodeOf(cand[c]);
            boolean better = draw > drawBest
                    || (draw == drawBest && (code < codeBest
                    || (code == codeBest && c < best)));
            if (better) {
                best = c;
                drawBest = draw;
                codeBest = code;
            }
        }
        return best;
    }

    /** 动作族 = `cand` 的 type one-hot（0..8）取 argmax；越界 ⇒ 0。 */
    static int familyOf(float[] cand) {
        return argmaxRange(cand, 0, Math.min(9, cand.length));
    }

    /** 牌码 = `cand` 的 tile one-hot（9..46）取 argmax；越界 ⇒ 0。 */
    static int tileCodeOf(float[] cand) {
        return argmaxRange(cand, 9, Math.min(46, cand.length)) - 9;
    }

    /** `[from, to)` 段的 argmax（**严格大于** ⇒ 并列取最小下标，与 {@link #argmaxOf} 同口径）。 */
    static int argmaxRange(float[] a, int from, int to) {
        int best = from;
        float bestVal = Float.NEGATIVE_INFINITY;
        for (int i = from; i < to; i++) {
            if (a[i] > bestVal) {
                bestVal = a[i];
                best = i;
            }
        }
        return best;
    }

    /**
     * 从 `softmax(logits / temp)` 采一个下标（数值稳定版：**先减去最大值**再 exp）。
     *
     * <p>为什么不用 Gumbel-max：这条路径要能在自检里被"逐概率对拍"（两候选差 d 时非贪心比例
     * ≈ sigmoid(d/T)），累积分布反变换最直白、也最容易验证。
     *
     * <p>兜底：全 `-inf`（mask 约定允许）/ NaN / 和为 0 时**退回 argmax** ——
     * 采样参数写错不该变成"随机乱打"。
     */
    public static int sampleSoftmax(float[] logits, float temp, Random rng) {
        int n = logits.length;
        if (n == 0) {
            return -1;
        }
        float max = Float.NEGATIVE_INFINITY;
        for (float v : logits) {
            if (v > max) {
                max = v;
            }
        }
        double[] w = new double[n];
        double sum = 0;
        for (int i = 0; i < n; i++) {
            double e = Math.exp((logits[i] - (double) max) / temp);
            if (Double.isNaN(e)) {
                e = 0;                                  // -inf - (-inf) = NaN：当成权重 0
            }
            w[i] = e;
            sum += e;
        }
        if (!(sum > 0)) {
            int fb = argmaxOf(logits);
            return fb < 0 ? 0 : fb;                     // 全 NaN 时 argmaxOf 给 -1：退回第 0 条
        }
        double r = rng.nextDouble() * sum;
        for (int i = 0; i < n; i++) {
            r -= w[i];
            if (r <= 0) {
                return i;
            }
        }
        return n - 1;                                   // 浮点尾巴：返回最后一条
    }
}
