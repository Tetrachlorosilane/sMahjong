# 预注册：P3-01「多轮短采 vs 少轮长训」· 总采集预算相同 · 主判据 = 每单位墙钟的进度
> **写在跑之前，跑之前不许改**（跑完只在末尾 §6 追加偏离，⛔ §1–§5 一字不改）。⛔ **本文件只是预注册：不跑训练 / 不跑采集 / 不跑闸门 / 不跑自检**。依据（本轮实测）：KL 早停 **78–102 步** vs 跑满 **5144 步**，`top1` 只差 **0.004** ⇒ **一轮的价值在"重新采一批"**，不在"多跑几步"（`NOTES.md` §6.5 第二十一/二十二轮 · `docs/TRAINING-V4.md` §14.10 · `CONTINGENCY.md` §2.3 上游旋钮）。
## 1 两臂（唯一自变量 = 轮数 × 每轮场次；其余口径逐字相同）
- **A = 1 轮长跑**：`--generations 1 --gen-offset 0 --games 12000`（12,000 场**一次采完、一次训完**，1 批）。
- **B = k 轮短跑，k = 6**：`--generations 1 --gen-offset {0..5} --games 2000`（**每轮 2,000 场、6 轮共 12,000 场**；第 i 轮 `--init` = 第 i−1 轮终点 `net.bin`，i=0 用与 A **同一份**起点网）。
- **总采集预算**：**场次口径 = 12,000 场/臂（相同）**；**墙钟口径** `T_arm` = 每臂「采集+训练+落盘」总秒数（两臂各测一次、如实记录，含 B 的 6 次启动/落盘开销；⛔ 不假定相等，预测见 §5）。**进度 = Δ / T_arm**。
- **逐字相同**：起点网 `tools\build\v4-expert-defrep-g10\net.bin` · 风格桌（`--style def`：学生 1 席 + `def`/`atk`/`win` 各 1 席）· `--rotate-full`（= C++ `--rotate-perm` 24 排列平均；给风格桌自动开）· **每轮同值** `--max-steps 2000 --kl-early-stop 0.10 --kl-min-steps 60 --epochs 1`（与 `tools\run-league.ps1` 缺省逐字相同）· `MAHJONG_PRODUCER=cpp`。
- **seed 族规则**：每臂一条**连续族**、族间不相交、**都不与判据族/历次族相交**（历次 = 24310011..32 / 24310041 / 24310091-92 / 24620011..24620212 / 40970001-4 / 77760001-2 / 51027301 / 52870101）；**判据牌山由两臂共享**（配对在判据侧做，⛔ 不要求两臂采集牌山相同）。
## 2 命令级配方（⚠ 变量 = `{A|B}`、`$i`、`$INIT`、`$SEED`、`--games`；其余固定，可整段复制）
```powershell
$repo='C:\Users\HP\source\games\mahjong'; $B="$repo\tools\build"; $S='S:\mahjong-training'; $py="$S\tools\py312\python.exe"; $INIT="$B\v4-expert-defrep-g10\net.bin"
$env:PYTHONPATH="$repo\python;$repo\python\.venv\Lib\site-packages"; $env:PYTHONIOENCODING='utf-8'; $env:MAHJONG_DATA_ROOT=$S; $env:MAHJONG_TRAINER="$S\tools\trainer\trainer.exe"
$env:MAHJONG_PRODUCER='cpp'; $env:OMP_NUM_THREADS='6'; $env:MKL_NUM_THREADS='6'
$C=@('-m','mahjong_ml.v4','loop','--generations','1','--workers','6','--eval-workers','6','--objective','ppo','--value-target','delta','--advantage','hand','--rank-weight','0.2','--max-steps','2000','--epochs','1','--kl-early-stop','0.10','--kl-min-steps','60','--critic-steps','0','--ref-beta','0.5','--val-every','100','--val-patience','5','--il-weight','0','--val-metric','policy','--eval-games','200','--eval-vs','prev','--no-java','--rotate-full','--style','def','--style-pool',"def=$B\v4-expert-def7-g10\net.bin",'--style-pool',"atk=$B\v4-expert-atk3-g10\net.bin",'--style-pool',"win=$B\v4-expert-win2-g10\net.bin")
& $py @C --label v4-wl-p301-A --init $INIT --gen-offset 0 --games 12000 --seed 52920001                    # A：1 轮 × 12,000 场（采集 seed 52920002）
foreach ($i in 0..5) { & $py @C --label v4-wl-p301-B --init $INIT --gen-offset $i --games 2000 --seed 52920011; $INIT="$B\v4-wl-p301-B-g$($i+1)\net.bin" }   # B：6 轮 × 2,000 场（采集 seed 52920012..52920017）
& $py -m mahjong_ml.v4.gate --incumbent "$B\v4-wl-p301-A-g01\net.bin" --candidate "$B\v4-wl-p301-B-g06\net.bin" --seeds 52930001,52930002 --games 6000 --block 6000 --workers 6 --line def --out "$S\gate" --tag v4-wl-p301   # 判据：共享族 2×6000 = 12,000 场
```
## 3 主判据（每单位墙钟的进度）+ 配对与功效**先算**
- **量**：**共享判据 seed 的逐场配对读数** —— 主 = **Δ顺位点/场/席**（= `v4.gate --line def` 口径，⛔ 不用"贴标签"读数）；次 = **净得失点/小局**（同 seed 配对；`_ceiling-common.json` 实测 `hand_sd ≈ 5100–5317` 点、`hand_se ≈ 52–54` 点/小局 @1000 场）。**进度 = Δ / T_arm**（单位 = 顺位点/小时，A、B 各一份）。
- **配对自证（硬）**：两侧 `seed_base`/场数相同且 `seed 不一致 0 · 学生席不一致 0 · 另三席策略表不一致 0`；`--seeds`/`--block` 分块照 `run-league.ps1` 口径。
- **功效（先算；由已实测的分辨率反推）**：闸门 **n=2000 场 ⇒ ±2.35 顺位点**（`tools\run-league.ps1` L153 注释，实测）⇒ `SE(N) ≈ 1.199·√(2000/N)` ⇒ **MDE ≈ 1.96·SE**：N=1,000/臂 ⇒ **3.32**；N=3,000 ⇒ **1.92**；**N=12,000 ⇒ 0.96 顺位点**。⚠ **同配方链间散布本身就是 1~3 顺位点**（`CONTINGENCY.md` §2.2 实测）⇒ **1,000 场/臂分不清 A/B** ⇒ **每臂需 12,000 场**，能分辨 **≥1.0 顺位点**的效应。
## 4 CPU 压力约束（本机硬件条件下；依据 `NOTES.md` §6.5 低 CPU 压力运行策略）
- `--workers 6 --eval-workers 6`（⛔ 不用历次的 12）；`OMP_NUM_THREADS=6` / `MKL_NUM_THREADS=6`（见上）。
- **一次只跑一臂**（⛔ A 与 B 不并行、⛔ 不与判据闸门并行）；**短跑 + 频繁落盘**（B 的 6 轮天然分段落盘；A 靠 `--val-every 100` 周期落盘）；**跑前先 `git push`**（保证仓库侧无未推送改动）；每轮记录 `hs_err_pid*.log` 的**新增**文件数（见 `hs_err` + 退出码 1 ⇒ **先重跑**，⛔ 不改代码）。
## 5 成本（⚠ **降载后墙钟未知**，按更慢估）
- 1 臂 1,000 场 ≈ **208 s** ⇒ 12,000 场采集 ≈ **42 min/臂**；1 轮训练+落盘 ≈ **8–9 min** ⇒ **预测 A ≈ B ≈ 90 min/臂**（A 单轮 batch 是 B 的 6 倍 ⇒ 它那一轮更慢、但只跑 1 次）⇒ 两臂 ≈ **3 h**；判据闸门 12,000 场 ≈ **36–45 min** ⇒ **合计 ≈ 3.5–4 h（降载前）**。⛔ 若只放行 **3,000 场/臂** ⇒ 按 §3 降级（MDE 1.92），结论只能写"≥2 顺位点的差"，⛔ 不许写成"无差"。
## 6 止损 / 分支（⛔ 预先写死，事后不许改判据）
- **B 优**：`Δ(B−A)` 的 95% CI **下界 > 0** ⇒ 判 B 优 ⇒ 处置：把「多轮短采」写成训练口径缺省（`--games 2000 × k 轮`）并在**下一个自变量**上直接用；⛔ 不做重复确认（已过 MDE）。**A 优**：CI **上界 < 0** ⇒ 保留「少轮长训」为缺省，并把"一轮的价值在重新采一批"**降级**为"只对 `top1`/行为口径成立、对**强度**不成立"（写进 `NOTES.md` §6.5）。**测不出**：CI 含 0 ⇒ ⛔ 不改判据、⛔ 不加剂量；① 若在降级档 ⇒ **补场次**到 MDE 达标；② 若已在 12,000 场/臂仍含 0 ⇒ 结案「**该分辨率下 A ≈ B（差 < 1 顺位点）**」，处置 = **取 `T_arm` 更低的那一臂**，且"重新采一批"只留作**效率**结论。
- **中断处理**：**A 只有一轮** ⇒ 打断即**整轮作废重跑**（⛔ 不许把半个 batch 当一轮）；**B 每轮独立** ⇒ 从**最后一个完整轮**的 `net.bin` 续（`--init` 指它、`--gen-offset` = 已完成轮数）。**作废判据**：崩在**采集**中 ⇒ 该轮 `raw\<tag>` + `compact\<tag>` **全部作废重采**（seed 按场号派生，续采会错位）；崩在**训练**中（采集已完成、compact 完整）⇒ 只重训该轮。**记录**：本文件末尾追加 `轮次 / 完成到哪 / 重跑次数 / 每次原始失败签名（退出码 + 新增 hs_err_pid）`，⛔ **重试不算通过**（沿用 `EXPERIMENT-A-DIVERGENCE.md` §2 口径）。
