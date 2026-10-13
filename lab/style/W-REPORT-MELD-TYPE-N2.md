# W-REPORT：`pon` 轴 n=2 复现 + 伴随质量量的**方向性**判据（2026-10-11 第五轮）

> 预注册 `PREREGISTRATION-MELD-TYPE.md` §6（**写在看到本轮结果之前**）+ §7 追记 · 模板追加
> `PREREGISTRATION-TEMPLATE.md` 判据 5 · `NOTES.md` §6.5 末（3 行）· 提交 `f9de698`（已推 `Training`）
> ⛔ 未加第 3 条链、未跑闸门、未建 QD；⛔ 未动 `server/`、`AGENTS.md`、`docs/`、`run-league.ps1`、
> `seat-balance.py`、`style-profile.py`、`style-vector.py`、`gate.py`。

## 1 (A) 方向性判据

**实现点**：`tools/w-ladder.py` 的 `companion_verdict`（+ 新增 `COMPANION_BAD_WHEN` /
`COMPANION_TOL_BY_METRIC` / `COMPANION_UNIT_LABEL` / `COMPANION_WRITTEN` / `_companion_direction` /
`_companion_tol` / `_companion_unit`），`COMPANION_METRICS` 由 4 个扩到 **8 个**（补进立直率 / 副露率 /
**放铳率** / 打点 —— 原先这四个只能手搓越界判定）。

- 规则：`down`（越多越好）⇒ 越界 ⇔ `hi < 0` **或** `Δ < −tol`；`up`（越少越好）⇒ 越界 ⇔ `lo > 0`
  **或** `Δ > +tol`。`bad_when` 表**必须覆盖**每个量，查不到**当场报错**（⛔ 不给"缺省向下"的静默兜底）。
- **为什么方向必须按轴意图定**：判据问的是"买到效果却付出代价吗"，而**代价有符号** —— 同一个
  Δ=−0.99pp 落在和了率上是要买的、落在放铳率上是白赚的。方向无关的规则 = **把"变好"也判越界** ⇒
  误杀一切"同时改善防守"的轴。
- 容忍带**随单位走**（`avg_win_score` = **150 点**，依据闸门侧 `win_points:150`；⛔ 不套 2.0pp）。

**四条自证**（`_quality-direction-selfcheck.py` / `.log`，总判 PASS）：

| 案例 | 量 | Δ / SE | 旧规则（方向无关） | 新规则 | 期望 |
| --- | --- | --- | --- | --- | --- |
| ① 真数据 | `deal_rate` | −0.9915 / 0.4365 | **越界**（`hi=−0.136<0`） | **不越界** | 不再报 ✓ |
| ② 合成 | `win_rate` | −2.0 / 0.4 | 越界 | **越界** | 报 ✓ |
| ③ 合成 | `deal_rate` | +2.0 / 0.4 | 不越界 | **越界** | 报 ✓ |
| ④ 合成 | `win_rate` | 0 / 5.0 | 不越界 | **不越界** | 不报 ✓ |

同 `tol` 逐格对拍（11 量 × 33 Δ × 6 SE）：`down` 类差异 **0** 格、`up` 类差异 **280** 格。
本轮合并读数上假警同样会发生：合并 `deal_rate` Δ=−0.6316 CI[−1.2577,−0.0054] ⇒ 旧规则 `hi<0` **越界**、
新规则 `bad_when=up` **不越界**（`_quality-direction-contrast-n2.py`）。

**缺省口径逐字节回归**（`_quality-byte-regress.py` / `.log`，总判 **PASS**）：
`git show HEAD:tools/w-ladder.py` 现取旧版 vs 新版，同一份 `paired/*.json` × HEAD 注册的 4 个量
⇒ **45/45 组 `--out` 文件逐字节相同**（rc 也相同）。既有 `companion-quality*.json` 13 份（能反查到输入）：
**10 份逐字节相同** · 1 份 provenance-only（`combine/rows/sources` 不同 —— 原产物走 `--pre/--post`、
轨迹已删，这里用 `--paired` 重建；**判据字段逐字节相同**）· 2 份**陈旧产物**
（`companion-quality.json`（`ok=true`→新版/HEAD `false`）、`companion-quality-dama.json`
（`false`→`true`）—— 它们写于 **2026-10-10 判据修正之前** 那版 `ok ⇔ CI 下界 ≥ −tol`，
**HEAD 现在也给出同样读数** ⇒ 与本批改动无关，⛔ 未改这两个文件）。

## 2 (B) n=2 复现

配方与 c1 **逐字相同**，只换 seed 族与链标签：
`probe --w 5400 --axis pon --pair no_deal --mode decision --label v4-wl-meldtype --labels v4-wl-meldtype-c2 --seeds 24620211 --generations 1`
（= `v4-expert-defrep-g10` + 风格桌 def/atk/win + `-NoGate` + `--rotate-perm` + 1 代 × 1000 场 + producer=cpp）。

`_meldtype-repro-n2.py` / `.log` 原始读数：

```
=== ① 配对自证 ===
  v4-wl-meldtype-c1  games=1000 seed_mismatch=0 student_seat_mismatch=0 ok=True seed_base=24620112/24620112
  v4-wl-meldtype-c2  games=1000 seed_mismatch=0 student_seat_mismatch=0 ok=True seed_base=24620212/24620212
=== ② 行级对账（基准 = 当前奖励代码重算）===
  c1 reconciled=True audit_is_recomputed=True  冻结账差 {meld/pon/action/chi/kan/paid/blocked/n_used} 全 0
     pre  决策 174247 / 鸣牌 3681 / 碰 2275 / 吃 1094 / 杠 312 / 付奖 1817 / 拦下 458
     post 决策 172564 / 鸣牌 4163 / 碰 2701 / 吃 1111 / 杠 351 / 付奖 2225 / 拦下 476
  c2 reconciled=True audit_is_recomputed=True  冻结账差 全 0
     pre  决策 173866 / 鸣牌 3697 / 碰 2217 / 吃 1171 / 杠 309 / 付奖 1782 / 拦下 435
     post 决策 170553 / 鸣牌 4023 / 碰 2734 / 吃  950 / 杠 339 / 付奖 2239 / 拦下 495
=== ③ 主判据 pon_type_rate（行级 · 方向为升）===
  v4-wl-meldtype-c1  61.6578% → 65.9647%  Δ=+4.3069pp SE=1.1115 t=+3.87 CI[+2.1284,+6.4854] n=948
  v4-wl-meldtype-c2  60.6755% → 68.5128%  Δ=+7.8374pp SE=1.1201 t=+7.00 CI[+5.6421,+10.0327] n=942
  **逆方差合并**（2 链）Δ=+6.0586pp SE=0.7889 t=+7.68 CI[+4.5122,+7.6049]
  等权平均（对照）    Δ=+6.0722pp SE=0.7890 CI[+4.5258,+7.6185]
  **链间极差** |Δ₁−Δ₂| = 3.5304pp（= 合并 SE 的 4.47×）
  复现判据（§6.1 ④）：两链同号=True 合并 CI 排除 0=True（正向且排除 0 = True）⇒ **已复现**
  辅 pon_rows_per_game：c1 Δ=+0.4260 CI[+0.3123,+0.5397] ｜ c2 Δ=+0.5170 CI[+0.3953,+0.6387]
=== ④ 六个伴随量（新方向判据）===
  riichi_rate       down  tol 2    c1 +0.457[-0.466,+1.380] OK    c2 -0.911[-1.950,+0.128] OK    合并 -0.146[-0.837,+0.544] OK
  meld_rate         down  tol 2    c1 +2.733[+1.775,+3.691] OK    c2 +2.915[+1.893,+3.936] OK    合并 +2.818[+2.119,+3.517] OK
  win_rate          down  tol 2    c1 +1.174[+0.353,+1.995] OK    c2 +0.375[-0.486,+1.236] OK    合并 +0.793[+0.199,+1.387] OK
  deal_rate         up    tol 2    c1 -0.992[-1.847,-0.136] OK    c2 -0.216[-1.135,+0.702] OK    合并 -0.632[-1.258,-0.005] OK
  avg_win_score     down  tol 150  c1 -17.995[-229.284,+193.293] OK  c2 -130.133[-375.172,+114.906] OK  合并 -65.816[-225.832,+94.201] OK
  riichi_win_rate   down  tol 2    c1 +1.820[-0.445,+4.084] OK    c2 +0.012[-2.404,+2.428] OK    合并 +0.974[-0.678,+2.627] OK
=== ⑤ 止损量 deals ===
  c1 Δ=-0.0099/场/席  c2 Δ=-0.0022/场/席（阈 +0.10）⇒ 未破
=== 止损判定（§6.3）：未触发 ===
```

`quality`（两链逆方差合并，新方向判据）：六个量 **rc 全 0**（不越界）⇒
`companion-quality-meldtype-n2-{riichi_rate,meld_rate,win_rate,deal_rate,avg_win_score,riichi_win_rate}.json`。

**一句话**：**复现了** —— 两链同号为正（+4.31 / +7.84）、合并 **+6.06pp CI[+4.51,+7.61] 排除 0**、
六伴随量按新判据无一越界、止损未触发 ⇒ 处置 = 预注册 §6.4 **分支 1：建议进 QD 或 n=3**（本轮不做）。

## 3 四条流程判据（模板）

① **口径冒烟**（`_meldtype-smoke-c2.log`，50 场，**本轮轨迹上重跑**）**PASS**：逐键相等
`decision 8204 / meld 173 / pon 98 / chi 62 / kan 13 / action 98 / paid 79 / blocked 19`、`μ` 两侧相等、
行集合（记账路 == 付钱路 == `value()` 非零 == 判据侧）对称差 **0**、四格自洽、**单向**（非 `pon:` 拿钱 = 0）。
② **门槛与主判据同口径**：碰/鸣牌 **56.65%**（主判据分母）、碰/决策行 **1.1945%** —— 两口径都过 1%/行。
③ **对账基准同版代码** = `_reward_meldtype_account`（当前奖励代码重算），冻结账差 **0**（逐项）。
④ **逃生阈值**：`post` **6.5 min**（≤10 min）+ 逐场 seed/学生席不一致 **0** + 逐项对账相等 ⇒ 可执行；
⛔ 未手搓任何读数（判据只有 `w-ladder.py` 一处实现，脚本 import 它）。

## 4 算力账与残留

| 项 | 耗时 |
| --- | --- |
| c2 链（1 代 × 1000 场） | **12.6 min** |
| `measure`（新 tag 风格向量） | ≈ 0.8 min |
| `post` 补采（1000 场，末代权重） | **6.5 min** |
| 口径冒烟 / 自证 / 对拍 / 合并读数 | ≈ 0.5 min |
| **合计** | **≈ 20.4 min**（≤30 min 预算） |

残留：`traces/v4-wl-meldtype-c2-g01` 与 `traces-post/v4-wl-meldtype-c2-g01` **各只留 `summary.json`**
（jsonl 已删，共 ≈5.4 GB）；`traces/v4-wl-fold-c1-g01` 的 **1000 个 jsonl 原样保留**（别人的产物）；
新增产物 = `paired/v4-wl-meldtype-c2-g01-POST.json` · 6 份 `companion-quality-meldtype-n2-*.json` ·
`style-vector-per-gen/v4-wl-meldtype-c2-g01{,-POST}.{json,md}` · `v4-wl-meldtype-c2-manifest.json` ·
`audit/v4-wl-meldtype-c2-g01.json` · 本轮 `_meldtype-*.py/.log`。S 盘剩余 **196.7 GB**。
⚠ `traces/v4-wl-meldtype-c1-g01` 与它的 `traces-post` **不存在**（上一轮把整目录删了 —— 本轮只需
`paired/*.json` + 风格向量即可合并，未受影响，但这条**没有重犯**：c2 两侧摘要都在）。

## 5 提交

`f9de698`（`tools/w-ladder.py` +144/−28 · `NOTES.md` +5 行）⇒ 推送前 `git fetch` **成功**、
远端头 `713ba2d` **== 本地父提交**（`git merge-base --is-ancestor` 通过 ⇒ 纯快进，⛔ 无 force）；
推送后 `ls-remote` + **GitHub API** `GET /repos/Tetrachlorosilane/sMahjong/git/ref/heads/Training`
两路都是 `f9de698b864cbb82bd57e1a7da1f5801cff022c7` == 本地 `HEAD`，未推送 **0**；
`node tools\handover-facts.mjs --check` **PASS**（推前推后各一次）；`git status --porcelain` **空**。
