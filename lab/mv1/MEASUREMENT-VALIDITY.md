# 测量有效性：项目方两条要求 → 机检判据（2026-10-19）

> 判据本体 = `AGENTS.md` §6.5 判第 24 条；工具 = `tools/ceiling-gate.py`（**退出码 5 = 测量有效性拒判**）；
> 详情 = `NOTES.md` §6.5 第一百一十四轮。⛔ 本文件不定义第二套统计口径。

## 1. 原话 → 判据

| 项目方原话（逐字） | 落成的机检判据 | 字段 / 退出码 |
| --- | --- | --- |
| "一定要保证座次平均化到 A^4_4=24 种情况" | 每族每臂 24 种**全排列**都出现；每种场次 ≥ `max(1, floor(n/24×0.8))`；`achievable<24`（如 `a,a,b,b` 只有 6 种）也算不足 | `seat_perm.coverage=k/24`、`counts`、`min/max`、`min_count_required`、`usable`；不足 ⇒ **5** |
| "尽量避免模型之间出现'共轭'现象" | ① candidate 与 baseline **绝不同桌**；② 对手池**冻结 + sha256 白名单 + 非本族/本产品线**；③ **同支邻家顺位优势 ≤ 非同支邻家的 1.2×**（胜率同） | `arm_isolation` / `opponent_pool` / `adjacency_conditional`；违规 ⇒ **5** |
| "同批模型或同支模型相互喂牌以提高顺位点" | 新增护栏：`self_win_rate` / `deal_in_rate` / `meld_rate` / `avg_hand_value` / `adjacency_conditional` 必须进 `verdict.json` | `measurement_guards`；缺原料记 `null` + `available:false` + warning |
| （防"取不到就当通过"） | 旧证据无 `per_game[].policies` ⇒ `seat_perm=null` + 判决警告"座次覆盖不可证"，⛔ 不许判「过」 | `measurement_validity.ok=false` ⇒ **5** |

**为什么门槛是 0.8**：24 种排列、场数不是 24 的整数倍时有余数抖动；0.8 允许抖动，但不允许某排列只出现 1–2 次
就被四席平均掩盖。`max(1, …)` 保证 n<24 的小样本不会被降成 0 门槛。

## 2. 现状核查（先查再改）

- `--rotate-perm` = **24 全排列**：`trainer/src/selfplay.cpp:151-156`（`kPerm24` 24 行）、`:175`（`kPerm24[g%24]`）；
  `tools/style-vector.py:253-258` 对采集命令行**硬断言**含 `--rotate-perm`。
- 既有 A 相 6 份 `summary.json`（`tools/build/mv1-s25-evidence/summaries/`，各 849 场）实测：**24/24**，
  每种 **35–36** 场（门槛 28）⇒ **A 相座次覆盖是完整的**。
- ⚠ **历史 `--gate-dir` 不完整**：`python/mahjong_ml/v4/arena.py:136-150` 的 `run_pair` 用 `a,a,b,b` 2+2
  对开 ⇒ 只有 2 个不同策略、座位排列天花板 **6**，且 candidate 与 baseline **同桌**。新判据下这类读数
  **拒判（5）**，只能当旧口径历史读数，⛔ 不许当 24 排列读数引用。
- A 相共轭现状：candidate/baseline **不同桌**（0/1698），对手池 `def7/win2/atk3-g10` **冻结**
  （sha256 `8349d0c0…` / `01f2eacd…` / `cac8381e…`），三条均非本族（line `v4-expert-*` vs `mv1-s24`）。
- 缺口：`summary.json` 无 `tsumo` / 副露计数 ⇒ `self_win_rate` / `meld_rate` 记 `null`（不重采；要该轴须在
  新采集摘要里加字段）。`deal_in_rate` / `avg_hand_value` 可由 `by_policy` 提供。

## 3. 用法

```powershell
# 自检（离线；含 4 循环拒判、同支邻家 2× 判嫌疑、2+2 拒判、旧证据不可证）
python tools\ceiling-gate.py --selftest

# 既有采集逐族配对 + 冻结对手池白名单（P1 / A 相复算）
python tools\ceiling-gate.py --family "f1=<base目录>;<cand目录>" `
  --opponent-pin lab\mv1\OPPONENT-PINS.json --min-walls 3 --out <verdict.json>
```

## 4. 训练侧（P1 增补，**只写计划不实现训练**）

自对弈采集的对手组成 **≥50% 必须来自历史/行外网**（本族 ≤50%）；验收必须报"**对行外池的增益**"；
若增益只在族内对手上 ⇒ **共轭嫌疑**、不算通过。⛔ 不训练、不采集、不跑强度闸门、不发布。
