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

**训练相第一步（2026-10-16，`NOTES.md` §6.5 第九十轮）—— 只做「实现 + 管线可证」，⛔ 未跑正式两臂、未跑闸门、未采集、未发布：**

- ✅ **§1 的唯一自变量已实现**（`MV1-FEASIBILITY.md` §4 的 11 项逐项落地）：外部头 `OwnWinHead`（`pretrain.py`，`ssl_head` 同族）读 `forward()` 新增的 `state` 键；`--aux-own-win-weight`（**缺省 0.0 = 关** ⇒ 不建头 / 不进优化器 / 不进 ckpt / `compute_loss(aux_own=None)` 一行都不走）；`--aux-own-win-rows` 只作**次口径报告**（`own_win_student`，⛔ 不进 `total`）；`>0` 而缺 `aux_win_flag` 列 ⇒ fail-closed `SystemExit`。⛔ 不进 `HEAD_SPECS`/`net.bin`/三端头表/推理路径；⛔ 数据侧零改动。
- ✅ **§3② 的负向对照已做**（同数据 / 同 seed / 同 `--init` / 同步数 4 步 / 同一 label）：`--aux-own-win-weight 0` vs **完全不传** ⇒ ① `net.bin` sha256 相同（`474346914D9A…C1A1`）② **整份 stdout 逐字节相同**（含 4 行 `[val] step`）③ **数据顺序指纹相同**（`perm_sha256=f05b2010…25c0`）；另 78/78 参数张量逐位相同、`_state_sha256` 相同、`metrics.json` 只差 `wall_seconds`。
- ⚠ **偏离 ①（照实）**：正冒烟的 `§3①(a)` 阈值（末值 < 边缘 BCE **0.5291**）**不是**在预注册配方（头 lr = `--lr 1e-3` × `--head-lr-mult 3.0` = 3e-3）下跨过的 —— 该配方 4 步只有 **0.6453**（头随机初始化 + Adam 每步只把 bias 推 ≈3e-3 ⇒ 跨阈值约需 300+ 步）。为在**极短**预算里证明"这一项真能把损失压到边缘基线之下"，本批把**只影响头 lr** 的 `--head-lr-mult` 抬到 **20**（**smoke-only**）跑 4/8/24/48/47 步：累积均值 0.601307 → 0.553748 → 0.539145 → 0.524134，**第 48 步精确末值 0.46985**（由 `(S48−S47)/512` 还原）< 0.5291。⛔ **正式两臂仍用缺省 `--head-lr-mult 3.0`**（本偏离不进入任何判决口径）。
- ⚠ **偏离 ②**：`save_checkpoint` 多一个 `own_win_head=None` 关键字参数 —— 关着时 ckpt 里**没有**这个键（不是"存在且为 None"），否则 `base` 臂的 ckpt 字节会变。
- ⚠ **偏离 ③（口径落点）**：负向对照的"逐字节"落在 **`net.bin` sha256 ∧ `_state_sha256` ∧ 逐张量逐位 ∧ stdout 逐字节**上，⛔ **不落在 `model.pt` 的文件哈希**上 —— `torch.save` 写 zip 条目时打了当前时间戳，两次跑的文件哈希**必然**不同（实测 A/B 两次 `model.pt` 哈希不同而 78/78 张量逐位相同）。
- ⚠ **偏离 ④**：`selfcheck` **929/0 → 935/0**（+6 条：把 FEASIBILITY §4.2 第 6 条改写成"**开关关 ⇒ 不喂**"+正向对照，并新增 `state` 键、`loss_weights()` 逐字对照、**辅助头 BCE 梯度真到主干**及其负向"输入换常数 ⇒ 主干梯度恒 0"）。
- ⏭ **仍未做（属下一批）**：§2 的两臂正式训练（含 §2 的 `KL(step)` 标定曲线）、§4 的 24,000 场闸门、§7 的 `ceiling-gate`。本批只到"管线可证"。

**训练相第二步（2026-10-16，`NOTES.md` §6.5 第九十一轮）—— MV-1 第一刀的「两臂正式训练」（`base` vs `aux`，600 步）；⛔ 未跑闸门、未采集、未发布：**

- **本批冻写的 base 配方（逐字；两臂只差一个开关）**：`python\.venv\Scripts\python.exe -u -m mahjong_ml.v4.pretrain --data E:\mahjong-training\compact\ov-A-s00 --label mv1-s91-<臂> --epochs 12 --max-steps 50 --batch 512 --lr 1e-3 --head-lr-mult 3.0 --seed 20260927 --init <仓库>\tools\build\v4-expert-defrep-g10\net.bin --objective ppo --behaviour <同一份> --behaviour-temp 0.5 --value-target delta --advantage hand --rank-weight 0.2 --baseline-fit scale --stage-a 0 --stage-b 0 --ref-beta 0.5 --grad-clip 0.5 --kl-early-stop 1 --kl-min-steps 100 --threads 8`；`aux` 臂**只多** `--aux-own-win-weight 0.3`（head lr = 缺省 3.0 ⇒ 3e-3；⛔ 未用上批 smoke 的 `--head-lr-mult 20`）。**两臂总步数 = 600**（50 步/轮 × 12 轮，落在 §2 的 400–600 内且取上限）；顺序跑、⛔ 不并发。
- ⚠ **偏离 ⑤（KL 阈值标定，照实）**：按 §2 用**同配方短跑**量 `KL(step)`（`--kl-early-stop 0`、12 个 50 步窗口、`total_steps` 与两臂同为 600）：KL(π_old‖π_new) **在第一个窗口即达峰**（窗口均值 **0.1061**），随后**衰减**（0.0675 / 0.0765 / 0.0640 / 0.0567 / 0.0414 / 0.0259 / 0.0295 / 0.0327 / 0.0257 / 0.0223 / **0.0236**；`ref_kl` 0.0841→0.0187）⇒ **§2 倾向的 0.10 会在 600 步内被越过（两臂都会被截断 ⇒ 按 §6 该臂作废）**，不可用。再以 `--kl-early-stop 0.30` **同配方跑满 600 步**：**未触发**（`stop_reason` 空）⇒ **实测峰值 ≤ 0.30** ⇒ 取 **T = 1.0**（= 实测窗口均值峰的 **9.4×**、= 实测峰值上界的 **≥3.3×**、= §2 倾向值的 10×）⇒ 两臂均**跑满 600 步**、`stop_reason` 皆空。⚠ 这**不是**"关掉早停"（T 是正的、且实测不绑定）；"别乱动"由 `--ref-beta 0.5` 的**现任锚**承担 —— 那正是 W3 的既有设计（放宽 KL 预算 + 对现任加锚）。
- ⚠ **偏离 ⑥**：`--kl-min-steps 100` 对 50 步/轮**够不着** ⇒ 被 `effective_kl_min_steps` 钳到 **10**（轮长的 1/5）—— 与第八十二轮 BL-13 同一现象（那里 120→24）。两臂**同一份**，不构成不等算力。
- ⚠ **偏离 ⑦（配方来源）**：第九十轮的负向对照**没有归档命令行**（`.tmp/mv1/**` 已按纪律删除）⇒ 本批的 base 配方**由本批自行冻写**（取值 = BL-13 冻结配方 + 本批的 `--batch 512` 与定步方式）；⛔ 因此**不声称"与今天逐字节相同的那条路径"**（那需要上一批的命令行）。
- ⚠ **偏离 ⑧**：`--stage-a 0 --stage-b 0`（= 全程阶段 c、余弦降 lr，与 BL-13 冻结配方同）**覆盖了 CLI 缺省的 0.25/0.35** —— 理由：缺省阶段 b 会**冻结主干**，而本刀的判据 ①(c) 正是"辅助头的梯度真到主干"，冻结段会让该读数在 100–240 步上人为归零。
- **操作检查（逐条原文见 `NOTES.md` 第九十一轮；§6：缺一条即该臂作废）**：
  - ①(a) `aux` 臂 `parts["own_win"]` 12 个窗口**逐窗落在 0.4437–0.5225 并整体下行**，**末窗 0.4437 < 边缘 BCE 0.5291**（val 边缘率 0.221687 的常数预测子 BCE），**且** < train 切分更严的 **0.523745** ⇒ 过。
  - ①(b) 头参数非退化（逐张量）：`own_win_head.proj.weight` shape **[1,192]**、**max|W| = 0.497751**（‖W‖₂ = 1.6363、非零比 1.0）· `bias` max|b| = 0.050049 ⇒ 过（⛔ 非表级常量）。
  - ①(c) **梯度真到主干**：base vs aux 的 `state_dict()` **78/78 张量全不同**（主干 **62/62**、‖Δ‖₂ = **13.562**；头 16/16、‖Δ‖₂ = 6.772；最大 `event.cell.weight_hh` ‖Δ‖₂ = 7.183 / max|Δ| = 0.1238）⇒ 主干**确实被辅助头推动** ⇒ 过。
  - ② 两臂 `stop_reason` **均为空**（`metrics.json` 里 = `null`）· `epochs=12` · `steps_per_epoch=50` ⇒ **跑满 600 步**（无段末早停存档）⇒ 过。
  - ③ 两臂**都写出权重**：`tools\build\mv1-s91-base\net.bin` **5,594,680 B** sha256 **A8D4CA76D274B3784743001F8BDB63EDF8D8AEE65F65340109219A638148DCF2**；`aux` **5,594,680 B** sha256 **317EB2C25A8A5E3CAB548AF450B43499059CF3DB39C04438C24F1AC98D3B93F5**（⛔ `own_win_head` **不进** `net.bin`：两臂字节数相同，差异全在主干/在线头权重上）⇒ 过。
- ⚠ **偏离 ⑨（采样口径）**：`overlap-orchestrator.resolve_real_pid(allow_self=True)` 在本机**抢跑**（第一个 0.4 s 探针看不到后代 ⇒ 判"本 pid 即真身"）⇒ 把 47,104 B 的 **venv 蹦床**当成训练真身，读出 **1.3 MB / 7.4 MB** 的假峰值；本批改为**扫描全部 `python*` 镜像取 committed 最大者**，并先用**已知负载标定**（名义 500 MB ⇒ 实测 **506.8 MB**，+1.4%）。⚠ 另：`Win32_OperatingSystem.FreeVirtualMemory` 本机读数为 **0.01 / TotalVirtualMemorySize 0.03 GB**（明显坏值）⇒ 本批一律用 `GlobalMemoryStatusEx.ullAvailPageFile`（= Commit Limit − Committed）。
- ⏭ **仍未做（属下一批）**：§4 的 24,000 场闸门（3 族 × 4,000/臂 = n 12,000 配对）与 §7 的 `ceiling-gate` 硬前置（**它吃两臂的 net.bin ⇒ 本批产出后才跑得动**）。

**闸门前置 A（2026-10-17，`NOTES.md` §6.5 第九十二轮）—— `tools\ceiling-gate.py` 的零训练预检（`--collect`，独立于 §4 的 24,000 场）；⛔ 未跑 B、未训练、未发布、⛔ §1–§9 一字不改：**

- **实际参数名与取值（⛔ 以工具 `--help` 为准）**：`--collect --tag mv1-s91-precheck --wall 53610001 --wall 53610002 --wall 53610003 --games 500 --workers 8 --budget-min 30 --base-net tools\build\mv1-s91-base\net.bin --cand-net tools\build\mv1-s91-aux\net.bin --table tools\build\v4-expert-def7-g10\net.bin,tools\build\v4-expert-win2-g10\net.bin,tools\build\v4-expert-atk3-g10\net.bin --trainer S:\mahjong-training\tools\trainer\trainer.exe --collect-out .tmp\mv1-s91-precheck\collect --out tools\build\mv1-s91-evidence\mv1-s91-precheck-verdict.json`。⚠ 与批给的占位写法对照：`--wall <族seed>` 与 `<table>` **都是工具真参数**（前者 `action="append"` ⇒ **可重复 ⇒ 支持多族**，后者逗号分隔三个对手 net.bin）⇒ **不存在"只能单族"**；真正需要补的是 `--trainer`（缺省 `"."` ⇒ 探针当场红）与 `--collect-out`（缺省落 `<数据根>\raw\ceiling-gate`，本轮 ⛔ 不动 `raw/**` ⇒ 改私有轮次路径）。
- ⚠ **偏离 ⑩（n 的口径，照实）**：`--games` 的真语义 = **每族每臂场数** ⇒ 3 族 × 500 = **n 1,500 配对/臂（3,000 场）**，**不是** 500/臂。理由：`--wall` 可重复（工具支持多族）、§4 的判决本就按 3 族；退成单族会让"套间散布 / 逐族同号"整块缺席。
- **读数（`verdict.json` 原文）**：`verdict="测不出"` · `exit_code=4`（0 = 过 / 3 = 不过 / 4 = 测不出 / 2 = ERROR）；主口径 `rank_points` Δ=**1.8783333**、SE=1.3327755、t=1.4093396、CI=**[−0.6963733, 4.5674667]**、n=1500、sd=51.6181726（751 胜 / 685 负 / 64 平）；**实际 MDE = 3.7338841**（`method` = 反解既有 `eval.required_n`，α=0.05、power=0.8）；3 族 Δ=[0.3572, 2.5468, 2.7310]、`spread`=2.3738、`ci_half_width`=2.6319、`spread_over_ci_half`=0.9019、`positive_every_wall=true`、`all_same_sign=true`、`arm_pair_consistent=true`（唯一臂对 = base/aux）、随机效应 μ=1.8887255 CI=[−0.7217537, 4.4992046] τ=0 q=0.6464268 df=2；安慰剂（非学生席同策略）`significant=false`（取最不利那条 atk3-g10：Δ=−1.1851333 CI=[−3.7280533, 1.4063200]；def7 −1.0053333、win2 +0.3121333，三条皆不显著）；`mean_reversion.identified=true`、**ρ=−0.5653004**（同一座位 / 同一副牌山 / 只换权重）；**`crosscheck`=null** —— 该口径只在 `--gate-dir` 复算模式下跑（对旧 `verdict.json` 逐族 Δ 对账），本轮是 `--collect` ⇒ **无旧 verdict 可对账**（照抄字段，⛔ 不当作"对上了"）；`errors=[]`；`wall_clock_s`=952.13 ≤ `wall_clock_budget_s`=1800、`wall_clock_ok=true`。
- ⚠ **偏离 ⑪（三条护栏全部"不得作否决依据"）**：容忍带**都 < 实测 MDE** ⇒ 工具按第七十一轮判据标 `usable_as_veto=false`：`wins` tol 0.10 / MDE **0.1086592**（`tol_over_mde`=0.9203084）· `deals` tol 0.10 / MDE **0.1159634**（0.8623414）· `win_points` tol 150 / MDE **947.5510363**（0.1583028）；三条 `ok=true`（本轮未越带）。对照 §4 表：同为这几条护栏，n=12,000 时 `tol/MDE_80` 本应是 2.33 / 2.78（可用）⇒ **A 的样本量根本量不动容忍带**。
- ⚠ **A 的能力边界（照实）**：n=1,500/臂 的实测 MDE = **3.734 顺位点**，是 §3 预注册 B（n=12,000）**`MDE_95`=0.973** 的 **3.84×**（同 sd 外推：51.618/√12000×1.960 = **0.924**；工具口径 `MDE_80` 对应 51.618/√12000×2.802 = **1.320**），也是 §5③"判无效所需的 ≤1.0 顺位点"分辨率的 3.7× ⇒ **A 只能当"有没有大问题的哨兵"**，⛔ **不许读成"过/有效"**（本轮它也没说"过"）。
- **§7 的硬闸门仍未过（照实）**：§7 写"只有 `verdict == "过"`（退出码 0）才放行" ⇒ 本轮 `测不出` = **不放行**。A 的**唯一**授权结论 = **没发现大问题**（点估计 +1.88 为正 · 3/3 逐族为正 · 安慰剂不显著 · ρ 可识别 · 无护栏越界 · 两臂权重 sha256 钉住 · 零训练自证干净 · 墙钟 ≤ 预算）⇒ 既不构成"开 B 的正当理由"，也**不构成对 B 的拦阻** —— **判决在 B**。
- ⚠ **偏离 ⑫（采样口径，A 版）**：本相真正的重负载是 `trainer.exe`（C++ 自对弈，峰值 **committed 149.8 MB / ws 153.7 MB**），而⑨那句"扫描全部 `python*` 镜像取 committed 最大者"在本相取到的是 **`ceiling-gate.py` 自己那条 python（committed 1,207.2 MB / ws 524.6 MB）** ⇒ 单报"python\* 最大"会把**闸门工具**当成本相峰值。故本批**同时**报三种口径（尚未标定、不作为跨轮比较的量）。
- ⏭ **仍未做**：§4 的 24,000 场闸门（3 族 × 4,000/臂 = n 12,000 配对）—— 它才是判决；本轮只跑了它的前置 A。

