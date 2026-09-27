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
