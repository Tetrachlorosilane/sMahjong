# MSLT-PLAN 修订（**主训练之前冻写**）· 2026-10-13

> 本文件是 `MSLT-PLAN.md` 的修订条款。位置在"数据已建、机制抽验已做完、**主训练还没开跑**"之后。
> 修订完**不再改 §1–§6**，跑后只追加 `MSLT-PLAN.md` §7。

## R1 数据来源（把 §1 的"取子集"落成可核的一条链）
- `raw\v4-wl-p301-A-g01`（12,000 场，A 臂数据，**原样不动**）。
- 子集 = **`p301-roster-A.json` 的 shard 0**（A 臂确定性排列，seed=52921001，1,000 场/片）。
  实测核对：numpy `default_rng(52921001).permutation(12000)` 的前 1,000 项游戏号 **与既有
  `E:\mahjong-training\compact\ov-A-s00\meta.json` 的 1,000 场 `SET IDENTICAL = True`**；
  首三项 `[1924,10558,2158]`、末三项 `[7971,10456,3431]` 与 `p301-A-run-attempt1.log` 逐字相同。
- 分片源 = `S:\mahjong-training\raw-shard\ov-A-s00`（同一批 1,000 场的既有分片，3,001 文件）。
- **学生席策略串**（从 raw 逐行统计，与上表互证）：`net:…\v4-expert-defrep-g10\net.bin@0#0.5`
  ⇒ **数据生成策略 = 起点网 `v4-expert-defrep-g10`（T=0.5）**，⛔ 不是 `v4-wl-p301-A-g01`。
- 紧凑集 = `tmp\mahjong-mslt\compact\mslt-s00`；**与 `ov-A-s00` 的 48 个张量逐字节相同**
  （48/48 identical，只有 `meta.json` 的 `src` 路径不同）⇒ 复用 BL-04 已验收的那份数据。

## R2 预算与步骤数（如实改，仅"步数"一项）
- 实测（本机、本次）：固定开销 **≈180–216 s/段**（读盘 + `logp_old` 重算 + `V_old` 重算 + KL 锚冻结），
  **≈0.4 s/步**。⇒ 段数是主要成本，步数几乎免费。
- 主训练改为 **3 段 / 各 ≤100 步 = ≤300 步**（原写"3 段 × 150 步"，理由见 R3）。
- 墙钟上限仍为 3 h；到点在当前状态收尾并如实报。

## R3 ⚠ **实测发现的硬约束（写在这里，因为它改变了可行设计）**
1. ★ **同类段必须在同一个 epoch 内切，续跑才逐位等价**。实测：把 20 步切成"10 + 10"且**跨 epoch 边界**
   （段 1 跑满 epoch 1、段 2 从 epoch 2 起）⇒ `net.bin` sha256 **不同**、epoch 汇总行 32 个字段全不同
   （`train_loss 2.1243 vs 2.1234`、`val_engine_top1_student 0.5662 vs 0.5697`）。这不是 bug：
   **epoch 边界会重洗数据**（`perm_sha256` 变）。⇒ 本次**不主张**"跨 epoch 切也等价"（无证据）。
   本批主训练的 3 段**全部落在 epoch 1 内**（epoch 1 = 644 步），与 BL-04 §17.4 的两种切法同构。
2. ★ **KL 早停会在本 recipe 下真的触发**：第一版 3×150 的段 1 在 **step 101**
   以 `KL(π_old‖π_new)=0.1237 > 0.1`（`kl_inv=-0.0455`）**早停**，`gstep=101`、`stop_reason` 记为早停。
   ⇒ ⛔ **早停存档不许续跑**（BL-04 fail-closed：早停那一支在 `gstep += 1` 之前 break，续跑会重跑同一步）。
   ⛔ **本批不关掉 `--kl-early-stop`、不放宽阈值**（预注册 §2 判据 3：照实报触发点，不许改口径）。
   ⇒ 处置 = 把**每段刹车点定在早停阈值之下**：`--stop-after-steps` = **100 / 200 / 300**，
   段长 ≤100 步。**阈值一字未改**（仍是 `--kl-early-stop 0.10`）。早停若**仍然**触发，在 §7 照实追加。
3. ⚠ **`--stop-after-steps K` 不带 `--max-steps` 会跑满整轮**（644 步），把存档的 `epoch` 推到 2
   ⇒ 再用 `--epochs 1` 续跑会被 fail-closed 拒（"存档已经跑满 `--epochs 1`"）。
   ⇒ recipe 用 `--epochs 2 --max-steps 150`（`epochs`/`max_steps`/`stop_after_steps` 都**不在**
   `_resume_recipe` 的核对键里，逐段可不同；其余 34 键逐字相同）。

## R4 ⚠ 环境偏离（数据根位置）
- **本会话的 Python 子进程被沙箱限定为"只能写工作区内"**：实测 `open()` 对
  `E:\mahjong-training`、`S:\mahjong-training`、`D:\`、`C:\Users\HP`、`%TEMP%` 全部 `PermissionError [Errno 13]`；
  只有工作区内（`C:\Users\HP\source\games\mahjong\**`）可写。PowerShell 自己可写 S:/E:，但 Python 子进程不行。
- 预注册 §4 原计划的"紧凑集落 E: 独立根"因此**做不到**（那是本仓 `paths.ensure_root` 明写的已知行为：
  "受限沙箱下子进程写工作区外路径会被拒"）。
- ⇒ 本次数据根 = `C:\Users\HP\source\games\mahjong\tmp\mahjong-mslt`（工作区内、**不是 junction**、
  通过 `MAHJONG_DATA_ROOT` 指向，⛔ 未改 `paths.DATA_ROOT` 的缺省值、⛔ 未在仓库里新增数据目录的缺省路径）。
  C: 余量 114 GB（紧凑集 14.1 GB）。**空间无风险**。
- ⛔ 未改 `powercfg`、未改页面文件、未重启、未用 junction、未用 `--ignore-…` 类开关。

## R5 判据补充（不放松，只补定义）
- 判据 2 ②**负向**：本批实测"清零 Adam 动量（2,438,276 / 2,799,072 个非零元素 → 0）⇒ `net.bin` 不同"
  ⇒ **"相等"不是假绿**（负向成立）。
- 判据 2 ①**在本批主训练的结构上成立**：主训练的段长（≤100 步）与刹车点（epoch 1 内）与
  机制抽验同构；抽验用 `--max-steps 20`（更小），主训练用 `--max-steps 150`（同一 epoch 内）。
  ⛔ 若主训练的续跑日志出现 `perm_sha256` 不一致或闸门硬拒，照实报为"判据 2 不成立"。
- 判据 4 的 Bonferroni：3 族 ⇒ `α=0.05/3`、`z=2.3939798`（**声明**：本批 3 族的实际族数，
  ⛔ 不沿用 P3-PKG 2 族的 2.2414）。
- 判据 4 的两种闸门口径都跑：**制度化的便宜闸门**（`tools\ceiling-gate.py --collect`，学生席口径、
  3 族 × 4,000 场/臂、含 placebo 与 ρ）+ **正式强度闸门**（`v4.gate`，逐族 2+2 对开、3 族 × 4,000 场/族）。
