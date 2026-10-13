# RERUN 预注册：BL-13 的 P3-01 型对照 —— **打开信任域（`--kl-early-stop 0.10`）后重跑** · 2026-10-13 · **跑前冻结**

> 依据 = `BL13-PLAN.md`/`BL13-RESULT.md`（第八十二轮）· `BL14-PLAN.md` · `_bl13\**`（尤其 `kl-curve-B-frozen.json` = 阈值标定曲线）。事实：BL-13 用 `0.03` ⇒ 单段只吸收 13–17 步 ⇒ 两臂实测只训 **30 vs 13** 步 ⇒ **自变量被截断 ⇒ 判「测不出」**。曲线：`0.03 ⇒ 13–17 步` · **`0.10 ⇒ 跑满 120 步`** · `0.30 ⇒ 跑满 120 步` ⇒ **用 0.10 重跑**才能让自变量真正活动。
> 跑后**只追加**「偏离」一节，⛔ 不改下面任何数字。窗口 2026-10-13 17:40 → 20:40（墙钟 ≤180 min），到点按现状收尾照实报。

## 0 唯一自变量 = **数据新鲜度**（等算力）；⭐ 唯一变化 = `--kl-early-stop 0.03 → 0.10`

| | **臂 A（新鲜 · 多轮）** | **臂 B（冻结 · 少轮长训）** |
|---|---|---|
| 采集 | k=2 轮 × **700** 场，**先训后采**：D_1@θ_0 → 训 → **D_2@θ_1** | **一次** 1,400 场，全程 θ_0 |
| 训练 | 每轮在其**当时新数据**上训 **M=100** 步（`--max-steps 100`） | 冻结数据上**一段**训 **k·M=200** 步（`--stop-after-steps 200 --save-optimizer`） |
| 合计 | **1,400 场 / 200 步** | **1,400 场 / 200 步** |
| θ_0 | 两臂**同一**起点网 `tools\build\v4-expert-defrep-g10\net.bin`（sha256 `167a7002…87df`） | 同左 |

recipe 其余**逐字** = `_bl13\ab\spec-A-r1.json` / `spec-B-s01.json`（batch 256 · lr 1e-4 · rank-weight 0.2 · ref-beta 0.5 · grad-clip 0.5 · baseline-fit scale · workers/threads 8 · `--rho-max 100` · `--stale-kl-budget 0.30` · `--behaviour-multi` · `--behaviour-temp 0.5` · 4 策略池 `<θ_i>@0#0.5 + def7 + win2 + atk3` · `--kl-min-steps 60`）；**只动阈值与步数预算**。
⚠ **顺序**：臂 A 第 i 轮 = **先训 D_i → 导出 θ_i → 再用 θ_i 采 D_{i+1}**（⛔ 不用 `pair --overlap`：BL-13 已证它只能拿 θ_i）。

## 1 操作检查（**必须先给再谈判决**；①②③ 任一不成立 ⇒ 判「自变量仍不可判」并停，⛔ 不跑闸门）

① **新鲜度真实现**：逐轮 `raw\**\g*.jsonl` 的 `policy=` **必须含本轮 θ_i**（贴原文；⛔ 不许出现 θ_{i-1}`）。② **信任域不再绑定**：逐段**实际步数 == 预算步数** ∧ `stop_reason` 为空（逐段贴「实际步数 / 是否撞早停 / KL 终值」）。③ **算力对账**：总场数相等 ∧ 总步数相等 ∧ 两臂**总墙钟 ±10%**。
**预写死的补救（仅②不成立时触发，且必须在报告里逐字说明）**：臂 B 若在 <200 步撞早停 ⇒ 从 θ_0 重跑**合法分段**版（2 段 × 100 步；切点 = `--stop-after-steps` 的正常收尾 ∧ `--save-optimizer`/`--resume-optimizer`）。⛔ 撞早停的存档**不许续**（AGENTS §6.5）；补救版任一段仍撞停 ⇒ 判「自变量仍不可判」并停。

## 2 判决口径（**复用 BL-13 的 3 个牌山族与种子** `52940001/2/3`，便于与 0.03 那次直接对照）

```
python -m mahjong_ml.v4.gate --incumbent <A> --candidate <B> --seeds 52940001,52940002,52940003 \
  --games 2000 --block 2000 --workers 8 --guard-metric deals:1.0 --guard-metric wins:0.05 --guard-metric win_points:300
```
⇒ 3 族 × 2,000 场/臂 = **6,000 场/臂** · 预测 **MDE ≈1.36** ⇒ ⛔ 报告里明写「**只有 ≥1.4 顺位点的效应可判**」（p301 量级 ≈2.5 属可判区）。判据 = 主口径 `rank_points` 的 **Bonferroni（3 族 ⇒ α=0.01667）修正后 CI 排除 0** ∧ **逐族同号** ∧ 方向 ∧ 护栏（`tol/MDE` **<1 者不得作否决**）∧ 安慰剂（打乱 B 的 seed→逐场值映射）∧ ρ 可识别性（`--rotate-perm` ⇒ 学生席口径，非零和对开）。
**四种结果（跑前写死）**：① Δ(A−B) BonfCI 排除 0 且为正 ⇒ **新鲜度胜**；② CI 含 0 ⇒ **等算力下新鲜度不是瓶颈**（报 MDE 与所需 N）；③ BonfCI 排除 0 且为负 ⇒ **数据量胜**；④ 护栏破 / 合规链断 / 族间异号 / 某臂预算没训满 ⇒ **测不出**（报断在哪、需要多少 N）。

## 3 `ceiling-gate.py` 为何不适用 + 替代的便宜前置（照 BL-13 §3 写法）

它的前置对象是**已经存在的候选网**（"这枚网值不值得开训"）。本轮两臂**只在训练后才存在** ⇒ 没有可喂的 `net.bin`，硬套 = 跑空。**替代（跑前 ≤2 min）**：① recipe 指纹核对（θ_0 + 3 风格网 + `trainer.exe` sha256 与 BL-13 逐字一致）；② `--init` == 数据生成策略（闸门当场验 `KL(π_old‖π_new)≈0`）；③ 分片完好（`meta.json` 可读 + `behaviour.table` 逐项 `verified`）；④ 闸门预演 `overlap-orchestrator check`（fail-closed 枚举自检 + G1–G5 全绿）。

## 4 纪律（跑前写死）

零绕闸门 · `--workers ≤8` · `OMP/MKL_NUM_THREADS=8` · **一次一个重负载** · `commit_free <8 GB 立即停` · 内存峰值**只认 committed**（ws 另报；⛔ 不采到 venv 蹦床 `Scripts\python.exe`）· 紧凑集/轨迹/ckpt 落**工作区内** `tmp\rerun\**`（子进程写 E:/S: 会 `PermissionError`，已知；⛔ 别改 `paths`、⛔ 不用 junction）· 崩溃/掉盘/WHEA 照实记 · 常驻 `paddleocr_mcp.exe` **不杀**、量化其 ΔCPU（本轮实测**未在跑**）。
⛔ 不动 `raw/**`、`style-reward/**`、`compact\v4-heads-g01`、`traces\v4-wl-fold-c1-g01`、`tools\build\**` 端点、`E:\…\compact\ov-A-s00` · ⛔ 不改 `powercfg`/页面文件、⛔ 不重启 · 逐手轨迹/临时紧凑**用完即删**（只留 `summary.json`/`verdict.json`/日志）· ⛔ **本批不发布、不打包**（候选网保留）· ρ `fail` 若在首个 batch 撞上限 ⇒ 该相改 `account` 重跑并**逐字记录**（预先声明的偏离）。
