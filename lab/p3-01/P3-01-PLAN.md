# P3-01 执行计划（**开跑前**冻写 · 权威 = `PREREGISTRATION-P3-01.md`，本文件只抄不新造）

> 唯一的"口径来源"是 `PREREGISTRATION-P3-01.md`；本文件把它冻成**逐字命令行 + 计时口径**。开跑后不改，偏离只追加到预注册 §6。
> 结论：**与预注册零偏离**（含 `--workers 6` / `OMP/MKL=6` —— 预注册 §4 逐字；任务书另写 `--workers 8`，但预注册自述"跑之前不许改"且 §2 命令行标"逐字"，故以预注册为准，差异记在 RESULT §残余）。

## 1 逐字命令行（变量 `<...>`；两臂**只差** `<i>`/`<g>`/`<s>`/`<tag>`/`<init>`）
`$py = S:\mahjong-training\tools\py312\python.exe` · `PYTHONPATH=<repo>\python;<repo>\python\.venv\Lib\site-packages` ·
`MAHJONG_DATA_ROOT=S:\mahjong-training` · `MAHJONG_TRAINER=S:\mahjong-training\tools\trainer\trainer.exe` ·
`MAHJONG_PRODUCER=cpp` · `OMP_NUM_THREADS=6` · `MKL_NUM_THREADS=6`
```
$C = -m mahjong_ml.v4 loop --generations 1 --gen-offset <i> --games <g> --seed <s> --init <init>
     --label <tag> --workers 6 --eval-workers 6 --objective ppo --value-target delta --advantage hand
     --rank-weight 0.2 --max-steps 2000 --epochs 1 --kl-early-stop 0.10 --kl-min-steps 60
     --critic-steps 0 --ref-beta 0.5 --val-every 100 --val-patience 5 --il-weight 0 --val-metric policy
     --eval-games 200 --eval-vs prev --no-java --rotate-full --style def
     --style-pool def=<B>\v4-expert-def7-g10\net.bin --style-pool atk=<B>\v4-expert-atk3-g10\net.bin
     --style-pool win=<B>\v4-expert-win2-g10\net.bin
A 臂：<i>=0  <g>=12000  <s>=52920001  <tag>=v4-wl-p301-A  <init>=<B>\v4-expert-defrep-g10\net.bin
B 臂：<i>=0..5  <g>=2000  <s>=52920011  <tag>=v4-wl-p301-B  <init>=起点网(i=0) / <B>\v4-wl-p301-B-g<i+1>\net.bin
读数（⛔**只取读数、不做采纳/回滚判决**；判据 seed 两臂共享）：
     $py -m mahjong_ml.v4.gate --incumbent <B>\v4-wl-p301-A-g01\net.bin --candidate <B>\v4-wl-p301-B-g06\net.bin
        --seeds 52930001,52930002 --games 6000 --block 6000 --workers 6 --line def
        --out S:\mahjong-training\gate --tag v4-wl-p301
```
（`<B>` = `<repo>\tools\build`；采集 seed 由 `loop` 派生 = A 52920002 / B 52920012..17，与判据族 52930001-2 不相交）

## 2 起点网与固定量（跑前实测）
起点 `tools\build\v4-expert-defrep-g10\net.bin` = 5,594,680 B，sha256 `167A7002A7A0FA60CCEE9344553AFCE78D6080ADA50EF4623D92257127AB87DF`
风格池 sha256：`def7-g10` `8349D0C0…` · `atk3-g10` `CAC8381E…` · `win2-g10` `01F2EACD…`
每臂 = A 1 轮 × 12,000 场；B 6 轮 × 2,000 场（**总场次相同**）；每轮同值 `--max-steps 2000`（KL 早停 0.10 / 最小 60 步）

## 3 `T_arm` 计时口径（两臂**同一把尺**）
起点 = 该臂**第一条** `v4 loop` 进程启动前一刻；终点 = 该臂**末轮** `net.bin` 落盘后一刻（秒表取自进程墙钟）。
**含**：采集 + 派生特征 + 轨迹校验 + 紧凑集 + 训练 + 导出 + 审计 + 2+2 评测 + 每轮启停/落盘（= 台账 `seconds` 全部相）。
**不含**：判据闸门（两臂共享的一次读数，跑在 `T_arm` 之外）。阶段拆分取自 `S:\mahjong-training\league\<tag>-v4.json` 的 `seconds`。

## 4 主判据与止损（照预注册 §3/§6，⛔ 不改）
主 = 共享判据 seed 逐场配对的 **Δ顺位点/场/席**（Δ = B − A）；**进度 = Δ ÷ `T_arm`**（顺位点/小时）+ 比值(B/A) 的 **CI**（对 Δ÷T 用 Fieller）。
止损：CI 下界 > 0 ⇒ **B 优**；CI 上界 < 0 ⇒ **A 优**；CI 含 0 ⇒ **该分辨率下 A ≈ B**，取 `T_arm` 低者。功效 `SE(N)≈1.199√(2000/N)` ⇒ 12,000 场/臂 ⇒ MDE ≈ **0.96** 顺位点。
条件性：单套/少数牌山 ⇒ 结论只标"**条件于该 seed 族**"。

## 5 成本预算（**区间**，按 `COLLECT-PROFILE.md` 实测）
采集 @w=6：1000 场 = 347 s ⇒ 12,000 场 ≈ **69 min**（受控 w=4 外推上界 ≈ 120 min）⇒ 每臂采集 **1.2–2.0 h**；
每轮紧凑集+训练+落盘 **6–9 min** ⇒ A ≈ 1.4–2.2 h、B ≈ 1.5–2.3 h ⇒ 两臂 **2.9–4.5 h**；判据 12,000 场另 **1.0–1.5 h**（⛔ 串行，不并行）
⇒ 总计 **3.9–6.0 h** vs **硬上限 300 min** ⇒ 风险显式：逼近上限时优先**做完 A 臂**、其次**做完 B 臂**，读数没跑完照实报"缺什么"。

## 6 卫生与不变式（跑前实测基线）
一次只跑一个重负载任务 · 每轮 `net.bin` 落盘后删该轮 `raw`/`compact`（**保留**端点权重/台账/闸门 summary）· 逐手轨迹用完全删
WHEA 近 24h = **19** · `hs_err_pid*.log`（仓库）= **31** · powercfg = 高性能（**不改**）· `--batch-forward` **不启用**（缺省 0）
