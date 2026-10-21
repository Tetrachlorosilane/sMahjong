# 7 头设计轻量审查（Luna · 2026-10-21）

> 只读审查；未训练/未采集/未跑闸门。实测数字标来源；文献只用于机制，不作证据。
> 依据：`lab/mv1/{PRIV-LABEL-AUDIT,HEAD-TRAINING,ARCH-REDESIGN §2.5,TRI-DISCUSS-2026-10}.md`、`AGENTS-TRAIN.md` 现行上下文、`NOTES.md §6.5` 第 121–127 轮（第 128 轮尚未落档；`lab/mv1/JUDGE-HEADS.md` 不存在，均已核对）；代码核对 `python/mahjong_ml/v4/model.py:381-392/862-879/977-1013`、`v4/dataset.py:21-30`、`v4/pretrain.py:1493-1520`、`server/src/main/java/mahjong/train/TraceRecorder.java:353-362/439-446`。

## 1 逐头裁决

| 头 | 裁决 | 一句话理由（挂实测或设计事实） |
| --- | --- | --- |
| policy | 合理 | 唯一必读头、也是唯一有强度闸门口径的头（ARCH §2.5）；top1 0.69–0.75 只是与行为策略的一致率，不能反读强弱（AGENTS §6.5「贴标签的读数不是强弱判据」）。 |
| value | 合理（读数需换口径） | 标签=事后终局分（`dataset.py:23`），设计标准；MSE 138–154 千点²，按真值 sd 21.14 复算 R²≈0.66–0.69，但未扣「终局=当前分」的持续性基线；ECE 3.27 是千点口径（探针 `probe_heads.py:112-147` 的 `reg_ece`，单位随 pred），不能与分类头的 0.39 并列比较。 |
| placement | 存疑 | 标签每场只有一个（1,851 验证行 ≠ 1,851 场），有效样本远低于 ARCH §2.5 的 ≥1,000 场；init ECE 0.058 与「近均匀预测 + 近均匀基率」一致（ECE 定义使然），不是技能；1,200 步后 0.39 说明置信度上升而准确率没跟上。 |
| belief_hand | 存疑 | 神谕标签但只诊断；标签被二值化成「有无该牌种」（`pretrain.py:13`），头名却写「手牌 3×34」；AUC 0.61–0.63 把 102 格混算、各牌种基率不同，只能读作「略高于先验」。 |
| belief_tenpai | 不合理（当前接线） | 标签本身合格（决策时刻的隐藏听牌，`TraceRecorder.java:353-362`）；但 `bt` 均值 26.5、BCE 11.9 ≈ 常数基率 0.42–0.44 的 28×，sigmoid(bt)∈[0.9996,1] ⇒ gate=1+tanh(W·1) 近似与状态无关；B 置换 KL≤6.9e−5 才是「状态无关」的证据，A 清零 0.14–0.43% 只证明「训练出的常数≠1」。 |
| danger | 不合理 | 标签是「整手是否放铳给 j」，回填到该手该家的每一行（`TraceRecorder.java:439-446`），却只在打出的候选上算 BCE（`pretrain.py:1497-1505`）⇒ 监督的是「这手结局」，不是「这张牌的危险度」；AUC 0.47–0.53≈先验，186 正例。 |
| effect | 合理（低价值） | 标签正是引擎的向听/进张种数/枚数（`dataset.py:21`），确实在监督它声称的「进张有效性」；但只做 tie-break，MSE 是三列按 `EFFECT_SCALE 8/34/136` 归一后的均值，0.0021→0.0039 不可解释（应报逐列 R²/ρ）。 |

## 2 存疑/不合理头的改进 + 标签是否在监督它声称的东西

### 2.1 placement（存疑）

- **标签审**：监督的是「整场终局顺位」，头声称「顺位概率」——字面一致，但**粒度错**：每场一个标签被复制到该场所有决策行，有效样本=场数而非行数。用行级 ECE 评价它，等于把同一场的几十行当独立样本。
- **改进**：① 与 value 合并——用四席 value 输出排序得顺位（信息重复，省一个头）；② 保留则只在终局行算 loss、按场加权；③ 判据改成 CE vs 两个基线（常数基率、顺位=当前顺位），过不了就停 loss。`contract=False`，删除损失而不动 `net.bin` 的成本很低。

### 2.2 belief_hand（存疑）

- **标签审**：不是它声称东西的完整版。监督的是 `aux_opp_hand > 0`（二值 presence，`pretrain.py:13` / 探针 `probe_heads.py:160`），不是计数；「手牌 3×34」容易被读成计数或完整手牌。
- **改进**：① 把 claim 改成「持牌指示」，报逐牌种 AUC/校准 vs 该牌种基率（现在 102 格混算，稀有牌主导）；② 若只诊断就删——它不进推理、成本是一个头加一路梯度；③ 若想当表示塑形，判据必须是 policy 侧同宽安慰剂（随机 102 列）而不是自身 AUC。已有 `C-unseen1` 因 top1 比 0.985 落选（HEAD-TRAINING §2），说明这条塑形路没走通。

### 2.3 belief_tenpai（不合理：当前接线）

- **标签审**：合格。`Agari.waits(concealCounts(s))` 在决策时刻取，是 nowcast 不是未来泄漏；按「特权只作监督」的字面规则合规（是否把「特权标签训出的头」也算违规，是 TRI §6.1 的待裁定项）。争议不在合规，在它现在**没在工作**。
- **为什么坏**：BCE 11.9 说明 logits 均值被推到 26.5；sigmoid(bt) 的变化只有 3.7e−4 ⇒ gate 的输入近似常数，gate 输出也近似常数（每个候选同乘一个固定向量）。此时门控对 policy 只是一次固定重标定，不引入任何状态信息（`policy-gate-check.mjs` 的 `max|w|=0.0617` 只证明权重非零，不证明输入未饱和）；B 置换 KL 2.55e−5、置零 KL 9.7e−4（PRIV §二）与之一致。
- **读数卫生**：HEAD-TRAINING §3 报 AUC 0.9708（1,851 验证行）、PRIV §二 C 报 0.826–0.849（39,709 决策）—— 行集不同，引用时必须带来源，不能并列。
- **改进**：① gate 输入改中心化/相对 logit（`bt − logit(基率)`，或三家 softmax/相对值），去掉 tanh 或改 `1+clip(s,0,1)`，或在 gate 前加温度 T（`sigmoid(bt/T)`）把饱和拉回动态区；② 头先过校准闸门——`BCE ≤ 常数基率 BCE（0.42–0.44）` 且 `ECE≤0.05` 才许接线，用 label smoothing/weight decay/降 head lr（现在 9e−3）先修饱和；③ 若只诊断，直接删 gate，别再用 A 清零读数说「功能权重小」。
- **一句**：这是「拿一个坏掉的概率去乘唯一重要的头」，不是「神谕标签该不该用」。

### 2.4 danger（不合理）

- **标签审：不一致，且是最严重的一处。** 三个错位：① 手级结果 vs 候选级风险（回填到该手该家的每一行，不只是放铳那一手）；② 只在被选动作上有标签 ⇒ 选择偏差、没有反事实；③ 结果由对手后续摸打/自摸决定，不是这张牌的属性。第 4 通道「任一家」只是三家标签的 OR，信息重复。
- **改进（按代价）**：a) 删（见 §5）；b) 换状态标签（对手听牌/立直/副露 + 听牌覆盖率）——但这与 `belief_tenpai` 重叠，等于合并；c) 若坚持结果标签，至少只在该小局真正放铳的那一手算，目标改成「打后是否立即被荣和」，并报选择偏差/IPW；d) 别用固定 `DANGER_COEF` 把近常数输出塞进 policy（`model.py:878-879`）：先量逐候选 std 与去掉该项的 argmax 翻转率，为 0 就是常数/噪声。
- **判据**：`AUC(danger→其标签)` 必须 > `AUC(belief_tenpai→同一标签)`。现有读数方向相反（belief_tenpai 对放铳代理 0.58–0.62 > danger 对自己标签 0.47–0.53，PRIV §二 C），但两者切片/口径不同，需同批复算确认；方向已足够发起一次对比。

## 3 冗余与冲突

| 对 | 判据 | 现状/预判 |
| --- | --- | --- |
| value × placement | 用四席 value 排序预测顺位，held-out acc ≥ placement 头；或 per-game 标签相关 >0.9 | 同源 `final_scores`、同粒度场末 ⇒ 高度可疑；placement 多花一个头只学 value 的序数粗化 |
| belief_tenpai × danger | ① 交叉 AUC；② 增量 AUC：`deal-in ~ bt + danger` vs `bt`，ΔAUC≤0.01 ⇒ danger 无增量 | 方向：danger 被支配（0.47–0.53 vs 0.58–0.62，需同批复算） |
| belief_hand × belief_tenpai | 给定 hand 预测，tenpai 标签的条件熵；或 hand→tenpai 的确定性映射（`Agari.waits`） | tenpai 是 hand 的确定性函数 ⇒ 同一隐藏量的粗细两个头，最多留一个 |
| effect × policy | tie-break 翻转数；翻转样本在对手立直时的实际放铳率/期望失点 | 短视牌效在 gap<1e−3 处压过 policy，恰是 policy 最不确定处；可能冲突 |
| 4 个不参与选点的头 × policy | 同宽安慰剂（随机列/打乱标签）下的 policy 增益 | 五臂 EV 随 `d_in` 严格反向（0.45414→0.44379，TRI C5）⇒ 加维本身可能有害，没安慰剂不能归因「头有用」 |

## 4 损失权重与「辅助头短训变差」：梯度争夺？

- **名义份额**：非 policy 权重和 = 0.5+0.3+0.2+0.2+0.3+0.3 = **1.8**，policy = 1.0（名义 64% 对 36%）。但七路损失没有梯度归一化，BCE 饱和时梯度小、value 的 81-bin CE 与 PPO 梯度尺度不同 ⇒ **名义份额不能当结论**，必须先量梯度。
- **head lr ×3 不进 trunk**：`pretrain.py:2634-2636` 的 `heads` 参数组与 trunk 分开，head lr 只放大读出（9e−3）。所以「9e−3 把主干带坏」不成立，但「9e−3 让读出过冲/遗忘」成立——这是 H2，不是 H1。
- **12 点扫描不能判**：单 seed、1,200 步；权重臂与 lr 臂混在同一张表里比较，没有 2×2 分解；且以 `KL/100step` 最高为选择准则。KL 是「动了多少」不是「变好多少」；`value×2` 的 0.1489 可能只是把 trunk 推得更远。用最大位移选权重 = selection-on-movement，会系统性偏向扰动大的臂。
- **三个竞争假设**：H1 梯度冲突（`cos(g_policy,g_aux)<0`）；H2 优化（head lr 过大/头欠拟合）；H3 表示漂移（trunk 为 policy 改写，旧读出失配）。现有「四头变差」对三者都兼容，不能归因。
- **可判实验（≤30 min、零采集、同数据/同 init/3 seeds）**：
  - **E1 2×2**：aux 权重和 {1.8, 0.6} × head lr {×3, ×0.3}，policy 固定 1.0。主判据 = held-out policy top1 比 init（≥0.99）与 policy NLL；次 = 四头指标。降 head lr 救回 aux 且 policy 不降 ⇒ H2；只有降 aux 权重才救回 ⇒ H1。
  - **E2 冻 trunk、只训头 1,200 步**：aux 恢复到 init 或更好 ⇒ 漂移/冲突；仍变差 ⇒ H2 或标签不可学。
  - **E3（机制，可选）**：每 100 步记录各头对共享参数的 grad norm 与 `cos(g_policy,g_aux)`；冲突率 = `cos<0 且 ||g_aux||>||g_policy||` 的步占比。>30% 再做 PCGrad/GradNorm 一臂（文献：Yu et al. 2020；Chen et al. 2018）。
  - **预注册**：同数据序、三 seed 配对、报 CI；任一臂 top1 比 <0.99 即回退；⛔ 不许再用 KL 选权重。
- **更便宜的解释先排除**：验证集只有 1,851 行（placement 的有效 n≈场数），aux「变差」可能只是没训够/验证太薄。先看 train vs val 曲线与常数基线，再谈冲突。
- **对「进度计」原意**：头若在训练中变差，就不是单调比较器；要当比较器必须冻 trunk 单独训到收敛，否则读数与 policy 强度反相关。

## 5 一句话

最不合理的是 **danger 的标签构造**——把整手放铳结果回填到该手该家的每一行、却只在被选中的候选上算 BCE，头声称的「候选×目标家危险度」从未被监督；最该删的也是 **danger**（`contract=True` 且 a− 已接线 ⇒ 真删=net.bin 格式变更；近期最省 = 权重 0 + 去掉 `DANGER_COEF` 通道，下次 format bump 再删张量），次选 **placement**（与 value 重复、`contract=False`，可直接停 loss）。

## 真实访问过的 URL

- https://arxiv.org/abs/2001.06782 — *Gradient Surgery for Multi-Task Learning*（NeurIPS 2020；多任务梯度冲突与投影）
- https://arxiv.org/abs/1711.02257 — *GradNorm: Gradient Normalization for Adaptive Loss Balancing in Deep Multitask Networks*（ICML 2018）
- https://arxiv.org/abs/1706.04599 — *On Calibration of Modern Neural Networks*（ICML 2017；深度网普遍过度自信，温度缩放可修）
- https://arxiv.org/abs/1706.05098 — *An Overview of Multi-Task Learning in Deep Neural Networks*（辅助任务选择与负迁移）
