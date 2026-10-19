# P1 · a− 第一次强度闸门（2026-10-20；条件于 `53610001/2/3` 三族，⛔ 不跨族外推）

> 判据 = `AGENTS.md` §6.5 判 24 + 判 23（C8-v2 只当训练准入）· 工具 `tools/ceiling-gate.py` · 证据 `tools/build/mv1-s35-evidence/`（2 份 verdict + 6 份 `summary-*.json`）· ⛔ 未训练/未发布、`MV1-PLAN §1–§9` 未动。

## 1. 设计（项目方裁决）
- δ = **2.0 顺位点**（score 同尺 = 2,000 点）；预算 **3 h**；`--metric score` 主 + `--family` 复算 `rank_points`。
- 被测 = 段 4 在线网 `3DB904FF…D0054493`（format 3 / a- / 959,078 参数）；基线 = `tools/build/v4-expert-defrep-g10/net.bin`（原 sha `167A7002…`）。对手 = `def7/win2/atk3-g10`（`lab/mv1/OPPONENT-PINS.json`）；两臂**不同桌**。

## 2. R-4 前置：format 2 → 3 容器转换
- 新 `trainer.exe` sha256 **`3DC6C08A…3BF146`** 对 format 2 **硬切**；基线/三对手原为 format 2、`lab/mv1/frozen-format2/` 本机不存在 ⇒ 冻结旧读端 + 当前 `save_net(arch=v4)` 转容器（只加 2,048 B arch 段，**张量 82/82 逐位相同**）；pin 加 `format3_mirrors`（原 sha 保留）。不转则一场也跑不了。

## 3. ⭐ pilot（500 场/族/臂 = 3,000 场；**1,184.5 s** = 2.53 场/s）
| 口径 | sd（池化） | SE | 逐族 sd（f1/f2/f3） | 逐族 Δ（f1/f2/f3） |
| --- | --- | --- | --- | --- |
| `rank_points` | **54.824** | 1.416 | 52.23 / 55.49 / 56.74 | −2.944 / −3.752 / −1.357 |
| `score` | **22,665** | 585.2 | 21,345 / 23,767 / 22,848 | −1324 / −1292 / −317 |
- `n = ceil((2.394·sd/δ)²)`：rank **4,307 对**（score 737 对，不约束）⇒ **每族 1,440 场/臂**（=60×24）⇒ **总 8,640 场**；预测 **3,411 s ≈ 0.95 h < 3 h** ⇒ 跑正式（先报 n/预算再采，未偷跑）。

## 4. 正式闸门
```powershell
python tools\ceiling-gate.py --collect --wall 53610001 --wall 53610002 --wall 53610003 --games 1440 --base-net .tmp\mv1-s35\nets\v4-expert-defrep-g10\net.bin --cand-net .tmp\mv1-s30\mv1-s30-online.bin --table .tmp\mv1-s35\nets\v4-expert-def7-g10\net.bin,.tmp\mv1-s35\nets\v4-expert-win2-g10\net.bin,.tmp\mv1-s35\nets\v4-expert-atk3-g10\net.bin --workers 8 --python python\.venv\Scripts\python.exe --trainer trainer\build\trainer.exe --collect-out .tmp\mv1-s35\collect --tag p1 --out tools\build\mv1-s35-evidence\verdict.json --metric score --min-walls 3 --opponent-pin lab\mv1\OPPONENT-PINS.json --adm-peak-gb 2.0 --budget-min 180
```
- 墙钟 **3,289.3 s（54.8 min）**；**8,640 场**（3×1,440×2）；R-2 每相通过、无杀相；内存峰值 `trainer*` **165.3 MB** / `python*` **1,971.9 MB**（含闸门工具）/ `java*` 0 / `node*` 0。

## 5. 测量有效性（先于强度；判据 24）
- `seat_perm` **24/24**（每种 **60** 场，门槛 48）· `arm_isolation` 0 同桌 · `opponent_pool` 3/3 白名单 · `adjacency` vacuous 不 suspect · `measurement_validity.ok=true` ⇒ 进入强度判决。

## 6. 双口径判决（条件于这三族）
- `score`：Δ **−828.3**，SE 354.5，t=−2.34，CI **[−1517.6, −108.0]**，MDE(2.394)=**848.7**（gate 80% 功效 993.2）。
- `rank_points`：Δ **−2.162**，SE 0.851，t=−2.54，CI **[−3.817, −0.437]**，MDE(2.394)=**2.038**（gate 2.385）。
- 逐族：score −964.0/−788.1/−732.9 · rank −2.811/−2.392/−1.282（**三族全负**）⇒ **判决 = 反向（候选显著更差）**；gate = **不过（exit 3）**；两口径 CI 上界均 <0，无口径冲突。

## 7. 护栏 5 条 + 安慰剂
- `self_win_rate` / `meld_rate` = **null**（summary 无 tsumo/副露计数，`available:false`；⛔ 不伪造）；`deal_in_rate` 0.1949 vs 0.1828；`avg_hand_value` 7047.9 vs 7120.1。
- `wins` Δ +0.023 [−0.029,+0.075] **未破**；`deals` Δ **−0.163 [−0.215,−0.111]**（tol 0.1 ≥ MDE 0.074 ⇒ **可用否决、确凿越界**）；`win_points` −102 [−563,+373]（tol/MDE=0.23 ⇒ 不得作否决）。
- 安慰剂：score/rank 四席恒和（max|Σ|=0 / 1.4e−14）⇒ `usable_for_veto=false`；`wins` 对照 −0.035 不显著。

## 8. 局限
- **条件于 `53610001/2/3` 三族；⛔ 不跨族外推**（score 族间散布 231 / CI 半宽 705 = 0.33×；rank 1.53 / 1.69 = 0.91×）。
- 裁决②：**无需跨架构可比** ⇒ 不与 v4 历史闸门并排；基线/对手经 format 3 容器转换（张量逐位相同），仍是本批内部对照。
- 8,640 场只买到 rank MDE(2.394)=2.04 / score MDE=849；真阴性需两口径 MDE_Bonf ≤ δ（本批已显著为负，不适用）。
