# 阶段 2 —— 特权 critic（2a）+ 蒸馏进主网 value 头（2b）

> 口径 = `lab/mv1/STAGE-PLAN.md` A–J（**A=①**：特权只进 critic；**B**：①他家手牌 ②牌山顺序 ③里宝；**J**：内化只走蒸馏/校准）。
> 证据 = gitignored `.tmp/mv1-s37/`（原始日志 / `distill.log` / `a-distilled.test.npz` / 逐位对拍）。⛔ 未采集训练以外数据 / 未跑强度闸门 / 未发布。

## 一、可行性审计（⚠ 逐字段查，不假设）

| 特权量 | 轨迹 `g*.jsonl` | `g*.aux.npz` | compact | 结论 |
| --- | --- | --- | --- | --- |
| ① 他家手牌（3×34 计数） | ✗ | **✓ 逐决策**（`opp_hand`，Java `TraceRecorder.auxRow` 从内存 `d.round.concealCounts` 读） | **✓** `aux_opp_hand` | **已可得** |
| ② 牌山剩余顺序 | ✗ | ✗ | ✗ | 只有 `Round.wallOrder()` → **回放**（`replay.noteRound`），⛔ 不是训练数据 |
| ③ 里宝指示牌 | ✗ | ✗ | ✗ | 只有 `Round.uraIndicators()`（服务端内存）；`round_end` 的 `ura_indicators` 只在和了时公开 |

⇒ 结论 **(b) 最小特权 sidecar**（②③ 逐决策都没有；① 已有）· **不需要 T2**：sidecar 是**独立文件**，obs/compact 布局与 `FEATURE_VERSION` 都不变；
只有**旧语料**无法补 ②③ ⇒ 阶段 2 的语料是**新采集**（同一条 compact 管线，不是重建全量）。

**sidecar 规格**（`server/.../TraceRecorder.writePriv`，`--priv` 开关；读端 `python/mahjong_ml/v4/privside.py`，fail-closed）：
`g<n>.priv.npz` = `opp_hand[n,3,34]` + `wall_next[n,14]`（剩余顺序前 14 张，不足补 255）+ `ura[n,5]`（1..5 张，未翻开 255）
+ 行键 `game/hand_no/step/seat`。**实测 200 个文件**：行键与决策行逐行相同 · `priv.opp_hand == aux.opp_hand`（max\|Δ\|=**0**）·
`wall_next` 有效长度 == `min(14, tiles_left)` · **live draw 146/146 命中下一张预测**（4 条小局首行无前驱）· `ura` 每槽一旦翻开即恒定 ·
255 只在尾部。`--priv` 不改轨迹：同种子 `g*.jsonl` 与 `g*.aux.npz` **sha256 相同**（实测）。

## 二、2a 独立小网（≤200k 参数）

- 输入 = **公开摘要 360 维**（自家手牌 34 + 可见 34 + 四家牌河 136 + 点数/局/巡 30 + 宝牌 34 + A 段逐决策 71[`danger`×68+向听+打点+点值] + 询问 3 + 向听派生 3）+ **特权 309 维**（对手暗牌 102 + `opp_tenpai` 3 + 牌山按种计数 34 + 接下来 4 张 one-hot 136 + 里宝计数 34）。
- 结构 = MLP(64) → 81 bin（**与主网 value 头同 bin/量纲**：±60 千点），HL-Gauss **高斯核** σ=1.5、dropout 0.3、wd 1e-3、按场早停。
- 参数：公开基线 **28,369** · critic 全 309 **48,145** / 窄 71 **32,913** / 只听牌 3 **28,561**（都 ≤200k）。
- 数据：teacher 自对弈 **655 场 / 450,585 决策**（3 个种子目录）；按场切 train 360,332 / val 44,693（早停）/ test 45,560（**66 场**，互不重叠）。
- 读数（test）：标签 `final`：公开 EV **0.4541** / critic **0.4473** ⇒ ΔEV **−0.0073** CI[−0.0138,−0.0017]；标签 `delta`
  （= `--value-target delta`，本小局收支）：公开 **0.0717** / critic **0.0661** ⇒ Δ **−0.0052** CI[−0.0127,+0.0022]；
  窄块 Δ **−0.0010** CI[−0.0053,+0.0039]；只听牌 Δ **−0.0037**。⇒ **特权特征在本样本量下没有可测正增益**（上界 ≈ +0.002～+0.004），如实报，⛔ 不调阈值。

## 三、2b 蒸馏（只更新 `heads.value`）

- 数据 = `.tmp/mv1-s37/compact`（58 场 / 41,560 决策，`dataset build --aux`）的 **29,001 蒸馏行**；compact↔critic 行键逐行对齐、`|Δlabel|max=0`。
- 目标 = critic 的 81-bin 分布（软标签 CE）；**冻结其余全部参数**。CE **3.781 → 3.573**；改动张量恰为 `heads.value.{weight,bias}`（断言）；
  参数 **992,166 不变**、`net.bin` **3,973,715 B**、format 3 / `topoId=3` / 97 张量。
- **test（13 场 / 8,752 行，按场 bootstrap 2000 次）**：主网蒸馏前 EV **0.3910** / CRPS 6.584 → 后 **0.4060 / 6.521**；critic 0.3867 / 6.686；公开基线 0.3831 / 6.662。

## 四、三条判据（预注册阈值）

1. **判据 ①（policy 逐位不变）**：R-4 语料 **3,083 决策** 四份策略 logits —— 旧包 / 新包 / **特权 sidecar 置 0** / **打乱** ——
   `sha256` **全等** `D9EDD5ED…C52EF0E`（max\|Δ\|=**0**）；三端 py↔java↔cpp **argmax 3,083/3,083 · 真分歧 0**（max\|Δ\| 7.25e-5 / 6.48e-5）。
   ⚠ 这是**接线级**证明：主网前向只吃公开四张量（`grep priv server/.../ai trainer/src` = **0**），特权量只在训练期被 critic 读。
2. **判据 ②（内化）**：阈值 ρ ≥ **0.5** ∧ EV ≥ **0.7× critic**（AGENTS §6.5 的 0.7 口径）。实测 **ρ = 0.952**、**EV 比 1.0498** ⇒ **PASS**；
   主网 − critic 的 ΔEV +0.0193 CI[−0.0025,+0.0516]。
3. **判据 ③（胜过公开基线）**：同规模公开小网 EV 0.3831 → 主网 0.4060，**Δ +0.0229 CI[+0.0024,+0.0437]，P(>0)=0.986**；CRPS **−0.141**（6.521 vs 6.662）⇒ **PASS**（⚠ 只有 13 场，CI 下界擦线）。

## 五、不退化 / 资源 / 局限

- `frac(u==0)`（LN 前）**0.0** · 非零维 **128/128** · `--strict-current` 退 **0** · `java --selftest` **1,595/0** · `trainer --selftest` **PASS** · venv `selfcheck` **1,029/0**。
- 资源（判据 20，按角色 committed 峰 / ws 峰）：数据 655 场 **4,316.6 / 3,177.1 MB** · 训练 **7,365.5 / 5,444.1 MB** · 蒸馏 **6,850.9 / 5,240.5 MB**；采样器自身 76.3 MB；`commit_free_min` 3.594 GB（R-2 准入 PASS，越线只记账）。
- **局限**：① 判据 ③ 的 test 只有 13 场（8,752 行）⇒ 区间宽；② "无特权增益"是**本样本量下的零结果**（有效样本 ≈2.6 万 hand-seat），不是"证明没有"；
  ③ 语料是 teacher 分布、蒸馏用 `h=0` 窗口冷启动（compact 未带 `h0`）；④ `wall_next` 只存前 14 张（摘要，非整条牌山）。
