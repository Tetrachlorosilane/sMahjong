# MV-1 第一刀预注册 —— **辅助子目标多头：二值 `own_win`**（跑前冻写；跑后只在 §10 追加「偏离」）

> 冻写提交 sha：**`git log --format=%H -1 -- lab/mv1/MV1-PLAN.md`**（本文件与 `lab/mv1/MV1-FEASIBILITY.md` **同一提交**冻存 = 冻写点；跑后正文一字不改；副本 = `S:\mahjong-training\w-ladder\MV1-PLAN.md`）。
> 依据（⛔ 不重测）：`lab/nextphase/NEXTPHASE-{PLAN,SCREEN}.md` + `evidence/nextphase-probe-full.json` —— 同一表示只换 y，
> **`own_win` 合法上限 R² = 0.13939 = 现标量 `delta`（0.06643）的 ×2.10，唯一不受天花板压**；`own_win_delta` ΔR²=+0.0110 CI 含 0 已出局，`hand_agg`/`winner_delta`/`hand_norm`/`dealin_sum` 亦出局。
> 零训练可判性核查（本文的输入）：`lab/mv1/MV1-FEASIBILITY.md` §1–§4。判据出处：`AGENTS.md` §6.5 第 17 条 · `NOTES.md` §6.5 第八十一~八十七轮。

## 1 唯一自变量（⛔ 定死，不许换成别的目标）
**新增一个辅助头 `own_win`，其目标 y = 既有的二值 `aux_win_flag`（自家本小局和了，0/1）**；其余（数据 sha / 观测 / 主干 / 配方 / 预算 / seed / 起点网 / 头表 / `net.bin` 格式 / 导出契约）**逐位不变**。
⛔ **不做打点回归变体**（`own_win_delta` 已出局；那是"若第一刀有效"之后的第二刀）。⚠ `aux_win_flag` **已在紧凑集里**（核查①）⇒ **零扩列、零重采**。
**落点 = 外部头**（与 `ssl_head` 同族，`pretrain.py:1846/1573-1575/1988`）：`own_win_head = nn.Linear(d_model, 1)` 读 `model.forward()` 新增返回的 `state`（`= u.mean(1)`，`model.py:275`），损失 `BCE`；权重 `--aux-own-win-weight`（**缺省 0.0 = 关**：不建头、不进优化器、不进 ckpt、`compute_loss(aux=None)` 一行都不走）。
**行口径**：`state` 级头走**全部行**（与 `value`/`placement`/`belief_*` 同一条规矩，⛔ 不吃 `row_keep`/RWR）；`--aux-own-win-rows student` 只作**次口径报告**，⛔ 不改主判据。

## 2 两臂与等算力
- **`base`** = 现行配方原样（今天逐字节的那条路径）。**`aux`** = **只**加该辅助头（权重预注册 **0.3**，与 `danger`/`effect` 同族量级）。
- 等算力口径：**同数据 sha256**（紧凑集逐文件 sha 打印冻存）· 同 `--init` 起点网 · 同 seed · 同 `--batch`/`--workers` · 同 `--target-minutes`（走 `v4 loop`）/ 同 **`--max-steps`**（走 `pretrain`）· 同 `--kl-early-stop`。**单进程一口气跑，⛔ 不分段**。
- ⚠ **两臂 KL 截断点不同 ⇒ 该臂作废**（第八十二轮：同数据同网，信任域**不是常数**、随阈值变 —— 0.03⇒13–17 步 / 0.10⇒120 步）。
  **规避**：训练相第 0 步先在 base 配方上量一条 `KL(step)` **标定曲线**，据此选一个**两臂都到不了的阈值（0.10）**，并把 `--max-steps` 固定在曲线上 KL 远未越线的步数 ⇒ 两臂**都跑满**。⛔ 段末撞早停的存档不许续。

## 3 操作检查（先给再谈判决）
① **辅助头真的在训**（三条，缺一条即该臂作废）：(a) `parts["own_win"]` 随 step **降到边缘基线之下**（基线 = 只报边缘率 `0.2217` 的 BCE = **0.5291**，判据 = 末值 < 0.5291）；
   (b) 头参数**非退化**（`max|W| > 0`、`|W|` 不塌 0）—— 用 **`tools/policy-gate-check.mjs` 同族方法**（解析张量/张量表量 `max|w|`）对 ckpt 的 `own_win_head.*` 判，⛔ 表级常量不算；
   (c) 梯度**真的到主干**（零训练可判：随机初始化 + 合成 batch，`‖∂L_aux/∂主干参数‖ > 0`）⇒ 已进 `selfcheck` 断言块（FEASIBILITY §4）。
② **负向对照（训练相第一步）**：`--aux-own-win-weight 0` 的 ckpt 与 `base` **逐字节相同**（判据 = `_state_sha256` 相同 ∧ 前向 logits 逐位相同）。
   ⚠ 本条**本批不做**（要一次短训练）；本批以**静态论证 + `selfcheck` 断言**覆盖（`pretrain.py:1100-1160` 的 `ref=None`/`il=None`/`rho=None` 同款口径：不传就一行都不走）。
③ **两臂都跑满**（`stop_reason` 空 ∧ 无段末早停存档）。

## 4 闸门（**3 族 × 4,000 场/臂 = n 12,000 配对**）· 主口径 `rank_points` · 护栏 `deals`/`win_points`/`wins`（`gate.QUALITY_TOL` = 0.10 / 150 / 0.10，单侧 `CI 下界 ≥ −tol`）
| 指标 | 实测 SE(n=12,000) | `MDE_95`=1.960·SE | `MDE_80`=2.802·SE | tol | tol/`MDE_80` | `usable_as_veto` |
| --- | --- | --- | --- | --- | --- | --- |
| `rank_points`（主） | 0.4967 | **0.973** | 1.391 | — | — | — |
| `wins` | 0.015347 | 0.0301 | 0.0430 | 0.10 | **2.33** | ✅ |
| `deals` | 0.012827 | 0.0251 | 0.0359 | 0.10 | **2.78** | ✅ |
| `win_points` | 136.10 | 266.8 | 381.3 | 150 | **0.393** | ❌ **不得作否决依据** |
- **输入** = `S:\mahjong-training\w-ladder\ceiling-gate\p3pkg-B-g06-vs-defrep-verdict.json`（同 N、同族数、同工具的真实读数）；**算法** = `eval.required_n` 反解（α=0.05、功效 0.8）与 CI 半宽。**Bonferroni(3 族) ⇒ z=2.394 ⇒ `MDE_Bonf` = 1.189**。
- `usable_as_veto` = `tol ≥ MDE_80`（工具口径）**且** `tol ≥ MDE_95`（更保守）；两条都过才 true。`win_points` 两条都 ❌ ⇒ **只记账、不得否决**；要它可作否决需 n ≥ **77,500**（80% 功效）/ **37,970**（95%CI）。
- 判决另需：**逐族同号**（3 族同向）· **安慰剂**（假臂 / 打乱 seed 映射，须不显著）· **ρ 可识别性**（`--rotate-perm` 24 排列 = 学生席口径；退化成 2+2 零和对开 ⇒ ρ 恒 −1 ⇒ 只记"不可识别"、不作判据）。
- ⚠ sd 是**臂对**的性质：上表是**预测量**；跑后以本批 `verdict.json` 的 `mde`/`tol_over_mde`/`usable_as_veto` 为准重算并照报（比值翻转即按跑后值判）。

## 5 四种结果解释（**预先写死**）
① **有效** = BonfCI(3) 下界 > 0 ∧ 逐族同号为正 ∧ 可用护栏（`wins`/`deals`）全不破 ∧ 安慰剂不显著 ⇒ 采纳，开第二刀（打点回归变体）。
② **反向** = BonfCI 上界 < 0 ⇒ 记"该元启发在本域**有害**"；⛔ 不许靠加训翻盘。
③ **无效（真阴性）** = BonfCI 含 0 **且** 实测 `MDE_Bonf` ≤ **1.0 顺位点**（分辨率足够）⇒ 判"无效应"。
④ **测不出** = BonfCI 含 0 **且**（实测 `MDE_Bonf` > 1.0 **∨** 逐族符号翻转 **∨** 安慰剂不排除 0 **∨** ρ 不可识别）。⛔ ②③④ 都不许写成"有效"，也不许拿"接近显著"当结论。

## 6 停止规则
撞 KL 早停（`stop_reason` 非空 / 段末早停）⇒ **该臂作废**（不续、不凑步数、不判）。BonfCI 含 0 ⇒ 判"**测不出**"（除非同时满足 §5③ 的 MDE 条件 ⇒ 判"无效"）。任一 `usable_as_veto=true` 的护栏越带 ⇒ **否决**；`usable_as_veto=false` 的越带只记账。操作检查① 任一子条不过 ⇒ 该臂作废（"没在训"与"没效果"必须分开）。

## 7 前置硬闸门（⛔ 必须先过才允许开训）
`python tools\ceiling-gate.py`（**≤30 min、零训练、fail-closed**；接口见 `tools/AUTOMATION.md` §7）：只有 `verdict.json` 的 `verdict == "过"`（退出码 0）才放行，`不过`/`测不出`/`ERROR` 一律停下先报。
⚠ 它吃**已有两臂的权重/summary**（`--family base=…;cand=…`）⇒ **aux 臂 ckpt 产出后才跑得动**；在那之前的零训练替代 = `python selfcheck.py` 的 MV-1 断言块 + `tools/lab/mv1-preflight.py`（标签在不在 / 覆盖 / 非零比 / 对齐，fail-closed）。

## 8 成本（逐相；沿台账 · 本批实测）
| 相 | 墙钟 | 显存 | 内存 |
| --- | --- | --- | --- |
| 前置核查（本批已做，零训练） | ≈ 0（探针各秒级） | — | commit_free 最低 **9.92 GB**（纪律线 8 GB 未触及） |
| 标定曲线（训练相第 0 步，单臂短跑） | ≈ 10–20 min | 训练相台账：**≈3.7 GB committed / 17 GB ws / ≈1.0 核** | 同上 |
| 两臂各一次训练（同 `--max-steps`） | 2 × 同预算 | 同上 | 同上 |
| 闸门采集 3×4,000×2 = **24,000 场** | ≈ **9,840 s ≈ 2.7 h**@workers 8（台账 410 s/1000 场） | — | `ceiling-gate` 1 s 栅格 + 越线树杀 + exit 7 |
| 紧凑集 | **0 s（⛔ 不重采）** | — | — |
⇒ 与 `NEXTPHASE-PLAN.md` §4·E1 的关键差别：**打点列不存在**，但**二值 `own_win` 列已在手**（核查①）⇒ 本刀零扩列、零重采，成本只剩"两臂训练 + 一次闸门"。

## 9 诚实的失败出口
若阴性 ⇒ 结论 = "**KataGo 的『预测目标分量』元启发在本规模 / 本域不迁移**"，并给下一个该问的问题（⛔ **不是"再加训"**）：
① 先问"**合法上限的 ×2.10 能不能转成可学份额**" —— 在 aux 臂上量 `own_win` 头的样本外 AUC/BCE，与探针读的 R² 0.06343（合法上限 0.13939）对照；
② 或问"**分量目标该进 critic 而不是 policy 主干**"（MV-2 特权 critic 路线）；
③ ⛔ 不做"按 `own_win` 分层加权"式第三变体（那是第二刀，须先有①的正结果）。

## 10 偏离（跑后只追加；上文一字不改）
（待跑）
