# `dama` 轴 · **判据对齐**（`decline_rate`）· 1 链 × 1 代 · 报告（2026-10-10 第二轮）

→ 预注册 `PREREGISTRATION-DAMA.md` §5（**写在看结果之前**）｜链 `v4-wl-dama5400b-c1-c1`
（`w=5400`、`dama=5400:no_deal`、`--mode decision`、seed 24620061 / 采集 seed_base 24620062、1 代 × 1000 场、`-NoGate`）
→ 产物：`audit/v4-wl-dama5400b-c1-c1-g01.json`、`paired/v4-wl-dama5400b-c1-c1-g01-POST.json`、
`style-vector-per-gen/v4-wl-dama5400b-c1-c1-g01{,-POST}.json`、`_align-reconcile-g01.{log,json}`、
`_align-verdict-selfcheck.{py,log}`、`companion-quality-dama-align.json`、`_align-regress/`
→ 代码：`tools/w-ladder.py`（`DECLINE_KEYS` / `_student_per_game` 的行级记账 / `_verify_decline_totals` /
`PAIR_AXES` / `COMPANION_METRICS` / `companion_verdict` 新规则）、`python/selfcheck.py`（风格奖励组尾）、`NOTES.md` §6.5

## 1. 判据对齐（本任务的核心）

**`decline_rate`（能立而不立率，行级）**：分子 = 该席该行 `legal` 含 `riichi:*` **且** `chosen` 不是它；
分母 = 该席该行 `legal` 含 `riichi:*`。
**独立实现位置**：`tools/w-ladder.py` 的测量路径（`DECLINE_KEYS` / `_student_per_game` / `_decline_totals` /
`_decline_selfcheck` / `_verify_decline_totals`）—— ⛔ **没有**动 `tools/style-vector.py`（手级口径的独立实现，
两处互不引用才有对账价值）。
**配对量**：主 = 每场每席"能立而不立"**行数**（`decline_rows_per_game`，分母 = 场数）；辅 = 聚合 `decline_rate`。

**三个来源逐项对账（必须相等）** —— 1000 场 / 学生席 171,622 决策行：

```
① 判据侧（w-ladder 行级记账，与审计账对账 reconciled=true）：
   decision_rows 171622  legal_rows 3863  decline_rows 667  paid 579  blocked 88  missing 0
   结构检查 0 / 0     μ_行 0.00388644812436634     ✓ 与审计账逐项相等
② 奖励侧审计账 audit/v4-wl-dama5400b-c1-c1-g01.json：
   n_used 171622  legal_rows 3863  legal_chosen_rows 3196  declined_rows 667 == action_rows 667
   paid_rows 579  blocked_rows 88  missing_legal_rows 0  μ 0.00388644812436634
③ 第三方独立复核 _dama-reconcile.py（⛔ 不 import style_reward）：
     declined_rows  复核 667 vs 审计 667 ✓ | paid_rows 579 ✓ | blocked_rows 88 ✓
     legal_rows 3863 ✓ | legal_chosen_rows 3196 ✓ | missing_legal_rows 0 ✓ | μ_行 ✓
     dama_wins 506 / wins 2216 / riichi_wins 1710 / seat_hands 11331 == style-vector 逐项 ✓
```

**判据与奖励对齐 ⇒ 同一个集合**：`decline_rows 667`（判据侧行级）== `action_rows/declined_rows 667`（奖励侧付奖行候选）。
`decline_rows == paid + blocked`（667 = 579 + 88）✓。

**语义对账（为什么副读数仍然没杠杆）**：506 个 `dama_wins` 里只有 **62** 个有"能立而不立"行，
其余 **379 副露 / 65 门前无立直机会** ⇒ 手级 `dama_wins` 与行级付奖行**只有 12% 重叠**（上一轮 8%，
同一结论、新种子复现）。

## 2. 质量护栏假警修法（自证原始输出：`_align-verdict-selfcheck.log`）

```
case                                                       Δ     SE               CI 95%     旧规则     新规则  rc
① 假警格（本任务实测的那个形状）                                 +0.30   1.32 [ -2.287, +2.887]      越界     不越界   0   **不再报**
② 显著为负（幅度也越过 tol）                                   -3.00   0.50 [ -3.980, -2.020]      越界      越界   3   **仍报**
③ 题面数值 Δ=−1/SE=3                                      -1.00   3.00 [ -6.880, +4.880]      越界     不越界   0
③′ 题面**描述**的那一格：点估计没到 −2 但 CI 上界 < 0                 -1.00   0.30 [ -1.588, -0.412]     不越界      越界   3   **仍报**
④ 边界：点估计恰 = −tol（且 CI 全负）                            -2.00   0.10 [ -2.196, -1.804]      越界      越界   3   （由 ① 触发）
```
⚠ **题面 ③ 的数值与它的描述不自洽**：Δ=−1、SE=**3** 的 CI 上界是 **+4.880**（不可能 < 0），
所以按写死的规则它**不报**（旧规则反而会报）。题面**描述**的那一格（"点估计没到 −2 但 CI 上界 < 0"）
= **③′（SE=0.3）**，它**仍报** ⇒ 规则的第 ① 条**确实活着**。两格都贴出来，一字不改。
**实测数据上的两规则对照**（`_align-verdict-realdata.{py,log}`；⛔ 两栏都**现算**，不读存档里的 `ok`）：

```
companion-quality-dama.json        dama_rate       Δ= +0.299pp SE=1.32 CI[-2.289,+2.888] | 旧=越界 rc3 | 新=不越界 rc0  ← 上一轮的假警
companion-quality-dama-align.json  dama_rate       Δ= -0.403pp SE=1.30 CI[-2.951,+2.146] | 旧=越界 rc3 | 新=不越界 rc0  ← 本轮又躲一次
_quality-regress-c2.json           riichi_win_rate Δ= -1.494pp SE=0.71 CI[-2.893,-0.095] | 旧=越界 rc3 | 新=越界   rc3  ← 判决不变（逐字节）
companion-quality.json             win_rate        Δ= -1.018pp SE=0.45 CI[-1.907,-0.129] | 旧=不越界 rc0 | 新=越界   rc3  ← **旧规则漏报**
```
**缺省口径回归（逐字节）**：`quality --metric riichi_win_rate --paired <3 份 dec6170>` ⇒ 与
`_quality-regress-c2.json` **IDENTICAL**（SHA256 `2E247D78…`，新规则下判据行一字不动）；
同一命令用**旧规则**也 IDENTICAL ⇒ **加 `decline_rate` 到注册表**这一步**惰性**（对既有口径零影响）。
⚠ `companion-quality.json` 里存的是 **`win_rate`**（不是缺省口径），它新规则下唯一变化 =
`"ok": true → false`（CI 上界 −0.129 < 0）—— 这是旧规则的**漏报**、不是新规则的假警；
因此该文件**按设计不再逐字节相同**，⛔ **未覆盖它**（要逐字节回归的是**缺省口径**那一份）。

## 3. 复跑读数（1 链 × 1 代；配对自证：seed 不一致 **0**、学生席不一致 **0**，1000 场；seed_base 前后都 24620062）

**主判据**
```
decline_rows_per_game（行/场/席）  0.6670 → 0.8230   Δ = **+0.1560**  SE 0.0547  t=+2.85  CI[+0.0489,+0.2631]  n=1000
   ⇒ **CI 排除 0 且为正** ⇒ 一代就把"能立而不立"推上去了（+23% 相对；条件率 17.27% → 19.84%）
decline_rate（逐场配对）          Δ = +2.3436pp  SE 0.8232  t=+2.85  CI[+0.7301,+3.9571]  n=946
聚合 decline_rate                 667/3863 = 17.2664% → 823/4148 = 19.8409%   Δ = +2.575pp
```
**副读数（口径不动，说明"为什么它没杠杆"）**
```
dama_wins_per_game   0.506 → 0.556   Δ = +0.0500  SE 0.0255  t=+1.96  CI[+0.0001,+0.0999]  n=1000（勉强排除 0）
dama_rate            25.53% → 25.13% Δ = −0.4026pp SE 1.3002  t=−0.31  CI[−2.9511,+2.1458] n=835（含 0）
```
**伴随量（五个 + quality）**
```
riichi_rate     28.46% → 29.30%  Δ = +0.8426pp [−0.1575,+1.8428] 含 0
meld_rate       26.78% → 26.59%  Δ = −0.1905pp [−1.1223,+0.7414] 含 0
win_rate        19.76% → 20.74%  Δ = +0.9788pp [+0.1262,+1.8314] **为正**（改善）
deal_rate       19.63% → 19.68%  Δ = +0.0418pp [−0.8318,+0.9154] 含 0
avg_win_score   6927.6 → 6933.6  Δ = +6.07 点   [−235.9,+248.0]  含 0
riichi_win_rate 51.66% → 52.19%  Δ = +0.5264pp [−1.8151,+2.8679] 含 0
quality --metric decline_rate  ⇒ **退出码 0（不越界）**，Δ=+2.344pp CI[+0.730,+3.957]
quality --metric dama_rate     ⇒ **退出码 0**（Δ=−0.40pp、SE=1.30 —— 旧规则会因 CI 下界 −2.95 < −2 **假警**）
quality --metric riichi_win_rate ⇒ 退出码 0
```
**止损**：没有任何伴随量显著退化（`win_rate` 还 **+0.98pp**；`wins` 折算 +0.111 场/席 > +0.10、
`deals` +0.005 场/席 ⇒ **都不构成退化**）⇒ 未触发止损，不作"走偏"结论。

## 4. 一句话结论

**用对齐后的判据，这个轴一代有位移**（主判据 `decline_rows_per_game` +0.1560 [+0.0489,+0.2631]，t=2.85、
CI 排除 0 且为正，且伴随量无退化）⇒ **该轴不该收**；但**手级 `dama_rate` 仍然没位移**（−0.40pp [−2.95,+2.15]），
因为它的 88% 来自**根本不能立直**的副露手 ⇒ **要换的是判据（已换），不是奖励定义**；若把它当"提高默听率
（手级）"的工具，则该轴不适用（那需要一条付奖行覆盖"未立直而和了"的口径，而那正是 `win` 的退化陷阱）。

## 5. 算力账 / 残留 / git

- 链 **11.5 min**（manifest `seconds=690.4`，rc=0，1000 场 / 2.82 GB）；g01 风格向量 ≈ **3.5 min**
  （与链尾训练**重叠**跑）；`post` 补采 + 配对 + 对账 ≈ **4.9 min**（15:10:50 → 15:15:42）；
  第三方全量复核 ≈ **0.2 min**（页缓存热 —— `post` 刚把同一批轨迹读了两遍）；`selfcheck.py` ×2 ≈ **5 min**；
  三条自证 + 四份回归 + 20 文件预检 ≈ **1.5 min** ⇒ **算力 ≈ 26 min**（上限 ≈35）。
  墙钟 ≈ **40 min**（14:52 → ≈15:32，上限 90）。
- 残留 `g*.jsonl`：我的目录 **0**（`w-ladder/traces` 0、`traces-post` 0、`raw/v4-wl-dama5400b-c1-c1-g01`
  已删）；别人的 **8000（style-reward/traces 8×1000）/ 400（gate/selfcheck-* 2×200）/ 2（raw/v4-golden）
  + 4（style-reward/_probe）一字未动**。
- `git status --porcelain`：` M NOTES.md` / ` M python/mahjong_ml/v4/style_reward.py` / ` M python/selfcheck.py`
  / ` M tools/w-ladder.py`（**未 commit、未 push**；`style_reward.py` 那 315 行是**上一轮**留下的未提交改动，
  本轮一个字没动它）。
- 文档判据四连：`doc-refs-check.mjs` **PASS** · `round-index.mjs --check` **PASS** ·
  `handover-facts.mjs --check` **PASS** · `python/selfcheck.py` **805 项 / 失败 0 / SELFCHECK PASS**
  （797 → 805 = 本轮在**风格奖励组末尾**新增 8 条断言；`NOTES.md` §6.5 末尾只加了 3 行）。
  ⚠ 自检里加载 `tools/w-ladder.py` 时显式 `sys.dont_write_bytecode = True`（否则每次跑自检都会在
  `tools/__pycache__/` 留一个未跟踪的 `.pyc`、污染 `git status`）。
