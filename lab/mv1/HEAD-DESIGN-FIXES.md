# 判定头四处修正（第一百二十九轮 · 2026-10-21）

> 依据 = `lab/mv1/JUDGE-HEADS.md`（A1→A3）+ `lab/research/HEAD-REVIEW-{gemini,luna}.md`；综合 = `HEAD-REVIEW-SYNTHESIS.md`。
> 纪律：⛔ 零采集长轨 / 未跑强度闸门 / 未发布 / 未改 `net.bin` 格式或 arch；⛔ 未动 `raw/**`、`style-reward/**`、`tools/build/**`、`Training`/`main`。
> 证据 = `.tmp/mv1-s50/`（`platt_bake.{py,log,json}`、`detach_danger_smoke.py`、`base600.log`、`post600.log`、`selfcheck.log`）。
## 1 ⭐ `belief_tenpai` 的 Platt 烘进最后一个 Linear

- **公式**（`model.py:1319` `fit_platt` / `model.py:1344` `bake_platt_`）：原头 `z_j = W_j·state + c_j`；校准
  `sigmoid(a_j·z_j + b_j) = sigmoid((a_j·W_j)·state + (a_j·c_j + b_j))` ⇒ **`W_j ← a_j·W_j`、`c_j ← a_j·c_j + b_j`**。
  ⇒ **张量形状 / 参数个数 / `net.bin` 布局全不变**（只改数值），三端前向零改动。
- **拟合**（`v4-wl-dama5400c-c1-c1-g01`、`v4-heads-g01/val` n=14,831，逐席）：`a` = **0.1911 / 0.2291 / 0.2073**、
  `b` = **−7.9966 / −7.3980 / −7.3108**；**BCE 13.462/13.027/13.177 → 0.304/0.330/0.374**（2 折 OOF **0.3084/0.3357/0.3784**，
  与 A1b 的 0.308/0.336/0.378 逐位一致）；**AUC 0.8750/0.8486/0.8003 → 0.8750/0.8486/0.8003**（差 ≤1.5e-8）。
- **烘入校验 + 开关**：`max|head(state) − (a·bt+b)| = 5.36e-6`、整网复算 `5.48e-6`（fp32）；`base.bin`/`baked.bin` **同大小 5,605,654 B**。开关 = `--calibrate-belief {off|platt}`（`pretrain.py:3890`，缺省 **off**；`pretrain.py:3490-3538` 训练结束在 val 拟合后烘入；⛔ 与 `--save-optimizer/--resume-optimizer` 同用会 `SystemExit`）。
## 2 ⭐ 判定头进 policy/gate 前 `detach`

- 实现：`p_bt = torch.sigmoid(bt); p_bt = p_bt.detach() if BELIEF_GATE_DETACH else p_bt`（v4 `model.py:1060`、a− `model.py:926`）；
  开关 = `--belief-gate-detach`（`pretrain.py:3894`；**缺省 off ⇒ 梯度图逐位不变**）。
- **证据**（512 行，`.tmp/mv1-s50/detach_danger.json`）：`policy_gate` 输入 `requires_grad` **off=True / grad_fn=`SigmoidBackward0`**；
  **on=False / grad_fn=None**。policy loss 对该头的 `max|g|`：**off 1.85e-3 → on 0.0**；同一次反向 `policy_gate` 仍有 **1.92e-3**。
- **belief 自身 loss 仍能训**：on 时 BCE 反传到头 **0.0463**、到主干 **0.1572**；只训该头 **100 步 BCE 0.3899 → 0.0678**（单调降）。
## 3 ⭐ `danger` 的 logits 项改为有界

- **旧**（缺省，逐位不变）：`logit += DANGER_COEF·log(mean_k σ(danger_k) + 1e-6)`，`p→0` 时到 **−13.8**（无界）。
- **新**（`--danger-form bounded`，`model.py:933-938`）：`risk = mean_k σ(d) − mean_i mean_k σ(d_i,k) ∈ [−1,1]`，
  `logit += DANGER_COEF·risk`。逐行减掉的常数对 softmax/argmax 是**数学空操作** ⇒ 只留"相对其它候选的危险度"。
- **参数账**：**0 参数**（`heads.danger` 仍 516 = 4×128+4）；`net.bin` 形状/大小不变。
- **出生影响**（`.tmp/mv1-s50/detach_danger.json`）：随机初始化 512 行，`|risk|` p99 **0.0031**（旧式 risk ∈ [−0.662,−0.594]）；对 argmax：新式改 **59/512**、旧式改 **88/512**（基线 = 不加 danger 项）。⚠ 出生时 `top1−top2` 中位仅 **0.0025**（<risk 差）⇒ 任何有信息量的通道都会翻近邻；**训练过的 A0** 上：新式改 **0/512**、旧式 1/512 ⇒ 实际部署形态下 ≈0。
- ⚠ **尚未三端同步**（Java/C++ 仍旧式）⇒ `bounded` 只给训练/离线实验，⛔ 该形态训出的网不得按"可直接上线"解读。
## 4 ⭐ `ONLINE_HEADS` 按 arch 修对

- `ONLINE_HEADS_BY_ARCH = {v4: (policy, belief_tenpai), a-: (policy, belief_tenpai, danger)}`（`model.py:444`，
  `online_heads_declared()` `model.py:451`）；三端字面量取**并集**（`policy,belief_tenpai,danger`），
  `head-tables-check` **PASS**（Python/Java/C++/§6.25 同值）。
- `policy-gate-check` 新增 `danger 通道` 列：a− ⇒ **✅ 在线（0 参数通道）**、v4 ⇒ **—（前向不读）**。
  扫 `bot-ai/` 10 个包：**在线 5 / 退化 4 / 非 v4 1**，与改动前**同**（现有包全是 v4 format 2，没有 a−）⇒ **没有包从在线变退化**；
  显式查 a− 网 `A8.bin`：`danger 通道 ✅ 在线`、`belief_tenpai` gate `max|w|=3.55` 在线。
## 5 缺省逐位不变 + 权重表

- **600 步 pre/post**（同命令、`.tmp/mv1-s38/corpus-compact`、`--init A0`、`--arch a-`、`batch 64×15ep`、seed 20261201）：
  `model` 段 sha256 **`14e970ab…4339` 两次相同**；导出 **992,166 参数 / 3,973,715 B**（`post600.bin`，arch=a-）不变。
- **损失权重**（`--head-loss-weights` 表达，⛔ 不硬编码；缺省注册表一字未改）：

| 头 | 注册默认 | 建议（Σaux ≤0.8） |
| --- | ---: | ---: |
| policy | 1.0 | 1.0 |
| value | 0.5 | 0.5 |
| placement | 0.3 | **0.0** |
| belief_hand | 0.2 | **0.0** |
| belief_tenpai | 0.2 | **0.1** |
| danger | 0.3 | **0.1** |
| effect | 0.3 | **0.1** |
| **Σ(non-policy)** | **1.8** | **0.8** |

- **命令 + 备注**：`--head-loss-weights value=0.5,placement=0.0,belief_hand=0.0,belief_tenpai=0.1,danger=0.1,effect=0.1`（policy 不传 = 1.0）。⚠ 旧文"Σaux=1.6"与注册表算术（**1.8**；去掉 value = **1.3**）对不上 ⇒ 本表只认显式逐项。
- **影响**：缺省命令/`net.bin`/三端前向**一个字节没变**；启用修正要显式加三个开关（Platt / detach / bounded）。Platt 与 detach **不改推理语义**（前者数值等价、后者只改梯度）；`danger bounded` 是**训练侧待三端同步**的候选形态。
