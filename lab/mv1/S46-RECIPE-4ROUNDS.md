# S46 配方：4 轮在线 PPO（A8 → A9）+ δ=2.0 复测（**新牌山族**）

> 本文件 = `NOTES.md` §6.5 第一百二十五轮的细节页（≤60 行）。证据 = gitignored `.tmp/mv1-s46/`；⛔ 未发布。
## 1 残留盘点（上一批 `mv1-s45`，被中止、无报告）

- 只有脚本（`m1.ps1`/`gate.ps1`/`pilot_sd.py`/`guard.ps1`）+ `args/collect.txt` + 一份**半轮采集**（341/1200 场 `jsonl`+`aux.npz`，**无** `feat.bin`/compact/`ckpt/`/`metrics.json`/闸门证据）⇒ **第 1 轮未完成 ⇒ 丢弃重跑**（删前逐项复核解析后绝对路径 `.tmp\mv1-s45\collect`，判据 19；其余 s45 产物保留）。
- 上一批**未提交**：`lab/mv1/S45-RECIPE-TRAIN.md` **不存在**；`NOTES.md` §6.5 第一百二十五轮**只在工作区**（`git show HEAD:NOTES.md` 里没有），且它引用的 S45 细节页缺失 ⇒ 本轮**重写**该小节（⛔ 不重复提交、不继承它的读数）。复用 `guard.ps1`/`round_probe.py`（拷进 s46）。
## 2 M0（三处**同时**写，⛔ 不关闭）

`AGENTS-TRAIN.md` 现行上下文 · `docs/HANDOVER.md` §1 · `NOTES.md` §6.5 第一百二十五轮，三处同两句：**事件顺序：缺少阶段成果（未交付）** —— **判据 3 仍红**；**已查明输入定义不足**：事件流 **97% 为 discard** ⇒ 一阶顺序上界 **0.0008 nats**（`H(next)=0.1491` vs `H(next|cur)=0.1483`）、三网打乱 KL **0.0034–0.0057** ≪ 0.02、下一事件辅头两臂 Δ=**−0.0009 nats**。⛔ 不写"已关闭/架构无缺陷/不算缺陷"。
## 3 M1 四轮（**配方逐轮不变**；持续 warm-start：r1 `--init`=A8，r>1 = 上一轮产物）

配方：`--objective ppo --arch a- --lr 3e-3 --lr-schedule constant --head-lr-mult 3.0 --head-loss-weights value=1.0 --grad-clip 0.5 --kl-early-stop 1.0 --kl-min-steps 100 --epochs 12 --max-steps 100 --batch 512 --stage-a 0 --stage-b 0 --cache-ref-logits`（+ `--behaviour <该轮 init> --behaviour-temp 1.0 --value-target final`）；采集 `trainer selfplay 1200 --workers 8 --rotate --aux --seed 20261031+r --policy net:<init>#1.0,def7,atk3,A7#1.0`（学生 25% / **行外+历史 75%**）。

| 轮 | `--init` sha256 | 场次 | 决策 | `KL/100step` 首→末 | top1 比 | `frac(u==0)` | `tile_id max\|emb\|` | `C8-v2` | 墙钟 采/训 | 产物 sha256 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| r1 | `F060B7AA…320FCDCD`（A8） | 1,200 | 1,200 | 0.0165 → **0.0497** | 0.994 | **0** | 1.157 | **PASS** 6/6 | 914 s / 308 s | `C032AEDD…158EDE44` |
| r2 | `C032AEDD…`（A9-r1） | 1,200 | 1,200 | 0.0206 → **0.0204** | 0.994 | **0** | 1.296 | **PASS** 6/6 | 925 s / 320 s | `18BBCC69…5B64B2BD` |
| r3 | `18BBCC69…`（A9-r2） | 1,200 | 1,200 | 0.0205 → **0.0176** | 0.991 | **0** | 1.267 | **FAIL（仅 M1）** | 915 s / 300 s | `1368BC7D…AFB145C` |
| r4 | `1368BC7D…`（A9-r3） | 1,200 | 1,200 | 0.0167 → **0.0197** | 0.998 | **0** | 1.239 | **FAIL（仅 M1）** | 926 s / 306 s | `B8B9613C…601F1F9A` |

- **终态 A9 = r4 产物**：`B8B9613C32B496F5187D8C3E285EFDFD255CD0F81A6AE1437074DBEA601F1F9A` · **3,973,715 B** · **992,166 参数** · format 3 / `a-`。
- `C8-v2`：M2 跑满 1,200/1,200、M3 无早停、M4 0.991–0.998、M5 **0**、M6 1.16–1.30；**M1 只有 r1/r2 过**（r3 0.0176、r4 0.0197 < ε=0.020）⇒ 按 F **只记账、不停**（与阶段 3 同口径）。PPO 口径自检每轮 `KL(π_old‖π_new)` ≈ **3.7e-8**（`--init` == 该轮数据策略）。
- 训练相合计墙钟 = **1 h 34 min**（采集 3,681 s + 特征 43 s + dataset 491 s + 训练 1,234 s + 导出/探针/C8 81 s）≤ **2.2 h**。
## 4 M2 复测（**只一次**；候选 A9 · 基线 A8 · **新牌山族 `53620001/2/3`**）

- **pilot** 500/族/臂 = 3,000 场（1,532 s；判据 24 四项全过）：score `sd=22,408.3` ⇒ `n=ceil((2.394·sd/2000)²)=720` 对 ⇒ 每族 240 ⇒ **1,440 场**（预计 12.4 min ≤1.5 h ⇒ **不是预算不足**）；rank `sd=55.01` ⇒ `n=4,336`（8,784 场 ≈76 min）⇒ **不另采**，按项目方口径在同一批 summary 上用 `--family` **复算** `rank_points`（⛔ 归档不重采）。正式 1,440 场 = 740 s，6 份 `summary.json` 与 `verdict.json` **同目录**（判据 21）。
- **判据 24 先判**：`seat_perm` **24/24** · `arm_isolation` **cooccur=0**（每族 480 场）· `opponent_pool` **3/3**（def7/win2/atk3-g10，sha256 全在白名单，`same_branch=false`）· `adjacency_conditional` **非 suspect**（无同支邻家 ⇒ vacuous）⇒ `measurement_validity.ok=true`，**未触发 exit 5**。
- **双口径**：score Δ **−954.4** CI[−2647.2, **+718.5**] MDE **2387.8**；rank_points Δ **−2.232** CI[−6.307, **+1.723**] MDE **5.697**；逐族 score −930.8/−2687.9/**+755.4**（spread/CI半宽 **2.05**）、rank −3.35/−5.02/+1.67。
- **判决 = 测不出（增量太小）**：**score MDE 2387.8 = 1.19×δ（未过 2×）、rank MDE 5.697 = 2.85×δ（过 2×）**，且 CI 含 0、逐族不同号 ⇒ ⛔ 不换基线、⛔ 不加场次（理由：CI 含 0 ∧ 逐族不同号 ∧ 唯一较大的 MDE 使该判断力不足以分辨）。⚠ **条件于这三族新牌山**：⛔ 不跨族、⛔ 不与 P1/阶段 3（`53610001/2/3`、9,504 场）比。
- **护栏**：`wins` −0.128 [−0.249,−0.010] tol .1 · `deals` −0.088 [−0.207,+0.028] tol .1 · `win_points` −265 [−1441,+902] tol 150 ⇒ `tol/MDE` **0.59/0.60/0.09 < 1** ⇒ **不可作否决依据**；`self_win_rate`/`meld_rate` = **null + 警告**（summary 无此字段，⛔ 不伪造）；`deal_in_rate` 0.1999/0.1971/0.1861 vs 0.1985/0.1811/0.1892；`avg_hand_value` 7956/7666/7839 vs 7745/7445/7406。
- **安慰剂**：零和下 `usable_for_veto=false`（|安慰剂|/|主口径|=2.23 = 再分配）；改看非零和 `wins` 的安慰剂 **显著**（+0.249 [+0.13,+0.37]；逐行 win2 **+2131.9** [+536.5,+3713.5]、atk3 −1192.8 ns、def7 +15.3 ns）⇒ 主口径那点差有**对照级别**解释 ⇒ **测不出**。
## 5 验收 / 资源 / 纪律

- 验收：`frac(u==0)` **0** · `--strict-current` **退 0** · 三端 argmax **3,083/3,083**（A9，Java↔C++ maxΔ **8.2e−5** ≤1e−4）· `java --selftest` **1595/0** · `trainer --selftest` PASS · venv `selfcheck` **1034/0** · `doc-refs`/`round-index`/`handover-facts` PASS · FLOPs **76.46 ≤103.3**（拓扑未变）。⚠ `tools/trainer-v4-parity.mjs --golden`（仓库 **c2/v4** 夹具）在 format-2 硬切后 **FAIL**（`heads online=policy,belief_tenpai` vs 夹具 contract 4 头）—— **既有状态、非本批引入**（本批未改任何代码）。
- 资源（判据 20，committed/ws 峰）：`python*` **7,345.8/19,599.8 MB** · `trainer*` **150.2/154.2 MB** · `pwsh*` 291.4/707.9 · `node*` 23.3/40.4 · `java*` 0；R-2 准入逐相 PASS，`commit_free_min` **13.6 GB**（硬地板 2.0）。
- 纪律：⚠ `trainer selfplay` **不支持 `--priv`**（只有 Java 生产者有）⇒ 本批采集**无 priv sidecar**（PPO 不用它），如实记账；每轮数据/ckpt 按轮清理（只留 summary/metadata，判据 19）；⛔ 未发布、⛔ 未碰 `raw/**`/`style-reward/**`/`tools/build/**`/`Training`/`main`/`MV1-PLAN §1–§9`。
