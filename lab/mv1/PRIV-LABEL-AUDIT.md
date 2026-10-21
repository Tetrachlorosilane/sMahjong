# 神谕标签依赖审计（`belief_tenpai` / 逐头）—— 只报事实，⛔ 不裁定规范

> 2026-10-21 · 零训练 / 零采集长轨 / 未跑强度闸门 / 未发布 · 证据 = `.tmp/mv1-s48/`（探针脚本 `tools/lab/priv-label-probe.py`）。
> 语料 = `.tmp/mv1-s37/compact` **train 39,709 决策**（≥3,083；`aux` 真值齐全）；主网 = `bot-ai/wl-p301b-g06/net.bin`
> （在役、`policy_gate` 非退化 max|w|=0.0617）；另复核 `wl-riichi` / `wl-dama` / `v4-expert-win2-g10`。

## 一、逐头标签来源（⚠ 逐字段查，不假设）

| 头 | 标签来源（文件:行 / 字段） | 神谕？ | 进 policy 路径？ |
| --- | --- | --- | --- |
| `policy` | 自对弈所选动作 + PPO 优势（`pretrain.py:1427-1455`；回报由 `TraceRecorder.finish` 回填） | 否（回报） | **是（它就是策略）** |
| `value` | `final_scores[seat] − 起点`（`dataset.py:23`；`TraceRecorder.finish:421-424`） | 否（事后结果） | 否（critic） |
| `placement` | 整场顺位（`dataset.py:25`；`TraceRecorder.finish:425-426`） | 否（事后结果） | 否 |
| `belief_hand` | `aux_opp_hand` ← `rr.concealCounts(s)`（`TraceRecorder.java:317-320`；`trace.cpp:142-146`；`dataset.py:28`） | **是（隐藏手牌）** | 否（只诊断；`ONLINE_HEADS` 不含它） |
| `belief_tenpai` | `aux_opp_tenpai` ← `Agari.waits(concealCounts(s), melds)`（`TraceRecorder.java:321`；`trace.cpp:147`；`dataset.py:29`；损失 `pretrain.py:1494`） | **是（隐藏听牌）** | **是**：`gate = 1+tanh(policy_gate(sigmoid(bt)))` 乘在候选表示上（`model.py:992-997`；a− 同式 `model.py:872-876`） |
| `danger` | `aux_opp_dealin` ← 小局结束按 `winner/loser` 回填（`TraceRecorder.java:396-403`；`trace.cpp:200-205`；`dataset.py:30`；损失 `pretrain.py:1497-1511`） | 否（**事后**放铳结果，非隐藏状态） | **v4 否**；**a− 是**（`AHeads` 固定系数 `DANGER_COEF`，`model.py:878-879`） |
| `effect` | 引擎 `HandEval.afterDiscard` 的逐候选派生量（`dataset.py:21`；`pretrain.py:1512-1520`） | 否（自家手牌 + 公开量） | 仅 **tie-break**（`gap<1e-3` 同族时，`policy.py:75-80`） |

⚠ 结论里的 **"神谕"= 读隐藏状态**（`opp_hand` / `opp_tenpai`）；`value`/`placement`/`danger` 是**事后结果**标签，
与"隐藏状态"不是同一类，别混。

## 二、三个探针（同一张网、同一批状态、确定性；n=39,709）

| 探针 | 主网 `wl-p301b-g06` | `wl-riichi` | `wl-dama` | `expert-win2-g10` |
| --- | --- | --- | --- | --- |
| A gate 清零：argmax 变化 | 97（0.244%） | 66（0.166%） | 170（0.428%） | 55（0.139%） |
| A `KL(π原‖π零)` 均值 | **0.001743** | **0.000400** | **0.001665** | **0.004818** |
| B 输入逐行置换：变化 / KL | 4（0.010%）/ 2.55e−5 | 1 / 7.12e−6 | 7 / 2.62e−5 | 1 / 6.89e−5 |
| B 输入置零：变化 / KL | 85（0.214%）/ 0.000973 | 49 / 0.000343 | 155 / 0.000972 | 36 / 0.003898 |

⚠ 主网 `belief_tenpai` 输出**饱和**：`bt` 均值 **26.5**、min 7.9（`sigmoid≈1`，三席均值 1.000/0.99999/1.000）；
B 置换能把 `bt` 改到 max|Δ|=112，但门控输入仍在饱和区 ⇒ 这解释 B 几乎为零。

**C（只评估，不重训；主网读数，按三席 j=0/1/2）**：神谕基率 13.9/14.8/15.6%，
现网 `AUC=0.849/0.837/0.826`；`BCE=11.9/11.7/11.6`（**不可当校准读数**：概率饱和，常数基率 BCE 仅 0.42~0.44）。
公开代理：① **该对手 3 次摸牌内宣告立直**（含已立直）基率 30.6/31.0/32.4%，
`AUC(代理→神谕)=0.687/0.683/0.679`、`corr=0.281/0.281/0.278`、`P(神谕=1|代理=1)=0.285/0.297/0.302` vs `|0=0.074/0.081/0.087`；
② **本家放铳给该家**（事后公开事件）基率 5.2/4.8/4.8%，`AUC(代理→神谕)=0.529/0.537/0.526`、`corr=0.09~0.12`。
⇒ 代理标签本身对神谕只有 **AUC≈0.68 / 0.53**（远弱于现网 0.83–0.85）；`AUC(现网→代理)=0.61 / 0.58–0.62`。

## 三、事实结论（⚠ 规范留给项目方）

- 按**冻写判据**（A 或 B：argmax 变化 >0 **且** KL ≥0.02）：**未达"功能依赖"** —— 四张网的 A/B `KL` 全部 **<0.02**
  （最大 0.0048），B 置换几乎为 0。
- 但**不是零影响**：A 清 gate 后 argmax 仍变 **0.14–0.43%**（55–170/39,709），B 置零 **0.09–0.39%**；
  `belief_tenpai` 的**输入**被大幅扰动（`bt` max|Δ| 86–125）时决策只动这一点 ⇒ 通路存在但**功能权重很小**。
- **待裁定**：① `belief_tenpai` 用神谕 `opp_tenpai` 作标签（输入公开、标签隐藏）是否允许；
  ② `belief_hand`（神谕、只诊断）与 a− 的 `danger`（事后结果、经固定系数进 policy）是否同属"标签可用特权"的边界；
  ③ 若以公开代理标签替换，按 C 的代理→神谕 AUC（0.68/0.53）是否够用。⛔ 本审计不写"合规/违规"结论。
