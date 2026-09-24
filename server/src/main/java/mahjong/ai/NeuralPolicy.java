package mahjong.ai;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Random;

import mahjong.util.Log;

/**
 * **进程内神经网络策略**（B 形态）：纯 Java 手写前向，零第三方依赖。
 *
 * <p>权重由 Python 导出（`python -m mahjong_ml.export weights --ckpt … --out …`），格式见
 * `mahjong_ml/export.py` 的 docstring：定长小端、张量按**固定顺序**排（trunk 各层 W/b → 打分头 W/b → 输出 W/b，
 * 全部 float32 行主序）。**顺序与形状都写进头部**，所以维度不符会在**构造期**就报出来
 * （Python 侧换结构后忘了重新导出，会立刻被发现，而不是静默算出一堆垃圾）。
 *
 * <p>前向结构（与 `nets.CandidateScorer` 一一对应）：
 * <pre>
 *   h = ReLU(W₂·ReLU(W₁·x + b₁) + b₂)          # trunk，x = Features.state(...)（607 维）
 *   logit_i = o·ReLU(H·[h ‖ cand_i] + b_h) + b_o   # 逐候选打分，cand_i 见 Features.candidate
 * </pre>
 *
 * <p>一致性由 `SelfTest.neuralForwardTests` 用 Python 导出的 golden 夹具逐元素钉住（容差 1e-4）：
 * 特征拼装 + 前向 两侧必须一样。**这是"训练与推理是同一个东西"的唯一保证。**
 */
public final class NeuralPolicy implements ActionPolicy {

    /** 权重文件魔数（"MJNN"）。 */
    public static final int MAGIC = 0x4D4A4E4E;
    /** 格式版本（与 `export.py` 的 `FORMAT_VERSION` 同号）。 */
    public static final int FORMAT_VERSION = 1;

    private final int stateDim;
    private final int candDim;
    private final int hidden;
    private final int headDim;
    private final int trunkLayers;

    /** trunk：每层 `[out][in]` 权重与 `[out]` 偏置。 */
    private final float[][][] tw;
    private final float[][] tb;
    /** 打分头：`[headDim][hidden + candDim]`、`[headDim]`；输出层 `[headDim]`、标量。 */
    private final float[] hw;
    private final float[] hb;
    private final float[] ow;
    private final float ob;

    private NeuralPolicy(int stateDim, int candDim, int hidden, int headDim, int trunkLayers,
                         float[][][] tw, float[][] tb, float[] hw, float[] hb, float[] ow, float ob) {
        this.stateDim = stateDim;
        this.candDim = candDim;
        this.hidden = hidden;
        this.headDim = headDim;
        this.trunkLayers = trunkLayers;
        this.tw = tw;
        this.tb = tb;
        this.hw = hw;
        this.hb = hb;
        this.ow = ow;
        this.ob = ob;
    }

    // ------------------------------------------------------------------ 加载

    public static NeuralPolicy load(Path path) throws IOException {
        return loadBytes(Files.readAllBytes(path), path.toString());
    }

    /** 从内存字节加载（自检读 golden 夹具里的权重时用；路径只用于日志）。 */
    public static NeuralPolicy loadBytes(byte[] raw, String what) throws IOException {
        ByteBuffer bb = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        if (raw.length < 28) {
            throw new IOException("权重文件太短：" + what);
        }
        int magic = bb.getInt();
        int ver = bb.getInt();
        if (magic != MAGIC) {
            throw new IOException(String.format("权重文件魔数不对：%#x（期望 %#x）", magic, MAGIC));
        }
        if (ver != FORMAT_VERSION) {
            throw new IOException("权重格式版本 " + ver + " != 本服务端 " + FORMAT_VERSION);
        }
        int sd = bb.getInt();
        int cd = bb.getInt();
        int hid = bb.getInt();
        int hd = bb.getInt();
        int tl = bb.getInt();
        if (sd != Features.STATE || cd != Features.CAND) {
            throw new IOException("权重维度 (" + sd + "," + cd + ") != 本服务端特征维度 ("
                    + Features.STATE + "," + Features.CAND + ") —— 重新导出权重"
                    + "（特征版本 " + Features.VERSION + "）");
        }
        if (tl < 1 || hid < 1 || hd < 1) {
            throw new IOException("权重头部非法：trunkLayers=" + tl + " hidden=" + hid + " head=" + hd);
        }
        float[][][] tw = new float[tl][][];
        float[][] tb = new float[tl][];
        int in = sd;
        for (int l = 0; l < tl; l++) {
            tw[l] = new float[hid][in];
            tb[l] = new float[hid];
            for (int o = 0; o < hid; o++) {
                for (int i = 0; i < in; i++) {
                    tw[l][o][i] = bb.getFloat();
                }
            }
            for (int o = 0; o < hid; o++) {
                tb[l][o] = bb.getFloat();
            }
            in = hid;
        }
        int headIn = hid + cd;
        float[] hw = new float[hd * headIn];
        for (int i = 0; i < hw.length; i++) {
            hw[i] = bb.getFloat();
        }
        float[] hb = new float[hd];
        for (int o = 0; o < hd; o++) {
            hb[o] = bb.getFloat();
        }
        float[] ow = new float[hd];
        for (int i = 0; i < hd; i++) {
            ow[i] = bb.getFloat();
        }
        float ob = bb.getFloat();
        NeuralPolicy p = new NeuralPolicy(sd, cd, hid, hd, tl, tw, tb, hw, hb, ow, ob);
        Log.info("神经网络策略已加载：" + what + "（" + p.params() + " 参数，"
                + Features.describe() + "）");
        return p;
    }

    /** 参数个数（日志/自检用）。 */
    public long params() {
        long n = 0;
        int in = stateDim;
        for (int l = 0; l < trunkLayers; l++) {
            n += (long) hidden * in + hidden;
            in = hidden;
        }
        n += (long) headDim * (hidden + candDim) + headDim;
        n += headDim + 1;
        return n;
    }

    // ------------------------------------------------------------------ 前向

    /** 只算 trunk（同一决策的所有候选共用），返回 hidden 维向量。 */
    private float[] trunk(float[] x) {
        float[] h = x;
        for (int l = 0; l < trunkLayers; l++) {
            float[] out = new float[hidden];
            for (int o = 0; o < hidden; o++) {
                float s = tb[l][o];
                float[] w = tw[l][o];
                for (int i = 0; i < h.length; i++) {
                    s += w[i] * h[i];
                }
                out[o] = s > 0 ? s : 0f;                 // ReLU
            }
            h = out;
        }
        return h;
    }

    /** `h ‖ cand` → 打分头的 logit。 */
    public float logit(float[] h, float[] cand) {
        final int in = h.length + cand.length;
        float[] z = new float[in];
        System.arraycopy(h, 0, z, 0, h.length);
        System.arraycopy(cand, 0, z, h.length, cand.length);
        float[] a = new float[headDim];
        for (int o = 0; o < headDim; o++) {
            float s = hb[o];
            int base = o * in;
            for (int i = 0; i < in; i++) {
                s += hw[base + i] * z[i];
            }
            a[o] = s > 0 ? s : 0f;
        }
        float out = ob;
        for (int i = 0; i < headDim; i++) {
            out += ow[i] * a[i];
        }
        return out;
    }

    /**
     * 一次询问的全部 logit（顺序与 `legal` 一致）—— golden 对拍与策略决策都走这里。
     *
     * <p>输入统一走**观测的 JSON 形态**（`Observation.toJson()`）：与轨迹/golden 夹具同一条拼装路径，
     * 多一次 `toJson()` 只要几十微秒。
     */
    public float[] logits(mahjong.ai.Observation obs) {
        return logits(obs.toJson(), obs.legalKeys());
    }

    /** `obs` JSON + 合法动作键 → logits（golden 对拍直接用这条）。 */
    public float[] logits(Map<String, Object> json, List<String> keys) {
        ObsFeatures.View v = ObsFeatures.ofObs(json);
        float[] h = trunk(Features.state(json, v));
        float[] out = new float[keys.size()];
        for (int i = 0; i < keys.size(); i++) {
            out[i] = logit(h, Features.candidate(keys.get(i), ObsFeatures.perCandidate(v, keys.get(i))));
        }
        return out;
    }

    @Override
    public Action choose(Decision d) {
        return chooseWithPrior(d, null, 0f);
    }

    /**
     * **带 teacher 先验的决策**（P5b 混合，见 `docs/TRAINING.md` §4 P5b）：
     * `logits = student(obs) + α · 1[该候选 == 老师动作]` 之后取 argmax。
     *
     * <p>为什么是"加在 logit 上"而不是"按概率混合"：这样**上位集合性质是构造出来的** ——
     * α 足够大时 argmax 必然是老师那一条（只要它在本次 `legal` 里），即
     * {@code hybrid(@∞) ≡ teacher}，退化成老师的行为是**可证的**，不是"大概会"。
     * 而 α = 0 时**逐决策与纯网络完全相同**（连浮点路径都一样：加的 0 不改变比较结果）。
     *
     * <p>⚠ 先验只可能来自**老师对同一信息集的动作**（`Bot.decide(d.round, …)`）：老师自己只用
     * `Round` 的公开辅助方法，所以这不是作弊面。真正的 ML 策略（{@link ActionPolicy}）仍然**拿不到
     * `Round`** —— 混合是 {@link Policy} 级的组合，见 {@link Policies#hybrid}。
     *
     * @param teacher 老师在本信息集的动作；{@code null} 或不在 `legal` 里 = 无先验（不惩罚任何人）
     * @param alpha   先验权重（logit 单位）。0 = 纯网络；≳ 几个 logit 差就足以压过学生自己的偏好
     */
    public Action chooseWithPrior(Decision d, Action teacher, float alpha) {
        List<Action> legal = d.legal();
        if (legal.isEmpty()) {
            return Action.of(Action.PASS);
        }
        int prior = (teacher == null || alpha <= 0f) ? -1 : d.obs.indexOf(teacher);
        int best = chooseIndex(d.obs.toJson(), d.obs.legalKeys(), prior, alpha);
        return best < 0 ? Action.of(Action.PASS) : legal.get(best);
    }

    /**
     * 带先验的 argmax（golden 夹具与自检直接喂 json+keys，不必造一个 {@link Observation}）。
     *
     * @param priorIndex 先验候选的下标（<0 = 无先验；越界视为无先验 —— 防御，不是"加在最后一条上"）
     */
    public int chooseIndex(Map<String, Object> json, List<String> keys, int priorIndex, float alpha) {
        float[] out = logits(json, keys);
        if (priorIndex >= 0 && priorIndex < out.length && alpha > 0f) {
            out[priorIndex] += alpha;
        }
        return argmaxOf(out);
    }

    /**
     * 取最大值下标；并列取**最小下标**。
     *
     * <p>⚠ 从 {@link #chooseIndex} 里原样抽出来的那段循环 —— 抽它只是为了让"采样"与"贪心"共用
     * 一份兜底路径，**行为必须逐位不变**（原来是"严格大于才更新"，所以并列天然取最小下标；
     * 别改成随机破平或 `>=`）。
     */
    private static int argmaxOf(float[] out) {
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
     * **按温度采样的决策**（P4 在线自对弈 RL 的探索口，见 `docs/TRAINING.md` §4 P4）：
     * `logits = student(obs) + α·1[老师那条]` → 从 `softmax(logits / T)` 里抽一条。
     *
     * <p>`T <= 0` 时**逐决策与 {@link #chooseWithPrior} 完全相同**（走同一条 argmax 路径，
     * 连浮点比较都一模一样）—— 这就是"加这个语法之前"的默认行为，既有轨迹一位不变。
     *
     * <p>⚠ 随机源由**调用方**给：{@link Policies#net(String, float, float)} 在**每局**用
     * `(seat, gameSeed)` 派生一个新 {@link Random}。自对弈"同种子逐事件可复现"这条硬性质
     * （AGENTS §6.5）就靠它 —— 跨局共享一个 RNG、或按 worker 线程共享，都会破坏它。
     *
     * @param temp 采样温度（logit 单位）。T→0⁺ 趋近贪心；T 越大越接近均匀抽样
     */
    public Action chooseSampled(Decision d, Action teacher, float alpha, float temp, Random rng) {
        List<Action> legal = d.legal();
        if (legal.isEmpty()) {
            return Action.of(Action.PASS);
        }
        int prior = (teacher == null || alpha <= 0f) ? -1 : d.obs.indexOf(teacher);
        int pick = sampleIndex(d.obs.toJson(), d.obs.legalKeys(), prior, alpha, temp, rng);
        return pick < 0 ? Action.of(Action.PASS) : legal.get(pick);
    }

    /**
     * 带先验的**温度采样**（golden 夹具与自检直接喂 json+keys，不必造 {@link Observation}）。
     *
     * @param temp {@code <= 0} ⇒ 与 {@link #chooseIndex} 完全同一条路径（argmax）
     */
    public int sampleIndex(Map<String, Object> json, List<String> keys, int priorIndex, float alpha,
                           float temp, Random rng) {
        float[] out = logits(json, keys);
        if (priorIndex >= 0 && priorIndex < out.length && alpha > 0f) {
            out[priorIndex] += alpha;
        }
        if (!(temp > 0f)) {
            return argmaxOf(out);
        }
        return sampleSoftmax(out, temp, rng);
    }

    /**
     * 从 `softmax(logits / temp)` 采一个下标（数值稳定版：**先减去最大值**再 exp）。
     *
     * <p>为什么不用 Gumbel-max：这条路径要能在自检里被"逐概率对拍"（两候选差 d 时非贪心比例
     * ≈ sigmoid(d/T)），累积分布反变换最直白、也最容易验证。
     *
     * <p>兜底：全 `-inf`（不可能走到，但 mask 约定允许）/ NaN / 和为 0 时**退回 argmax** ——
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

    /**
     * 自检用：返回一份"输出偏置整体偏移 delta"的副本（**红证**用）。
     *
     * <p>为什么用输出偏置而不是随机挑一个权重：ReLU 下大约一半隐藏单元是死的，扰动它们
     * 不会改变任何输出 —— 那样"红证"可能因为挑到死单元而假绿。偏置偏移是**必然**的：
     * 每条 logit 都恰好 +delta。
     */
    public NeuralPolicy debugOutputBiasShift(float delta) {
        return new NeuralPolicy(stateDim, candDim, hidden, headDim, trunkLayers, tw, tb, hw, hb,
                ow, ob + delta);
    }
}
