# 清理台账 3 —— raw 枚举 + B 六轮删除（2026-10-12 第三轮）

> 纪律：逐个**绝对路径** → 查引用（grep 仓库 + `w-ladder\*.md`）→ 查占用进程（`python|trainer|java|node|mahjong` **全无**）→ 记账才删。⛔ 无通配符一把梭；⛔ 零重负载（未跑训练/采集/闸门/selftest）。
## 1 `S:\mahjong-training\raw` 一级子目录（删前）· 归属 · 引用判断 · 处置
| 子目录 | 文件/GB | 归属 | 引用（脚本/报告） | 处置 |
| --- | --- | --- | --- | --- |
| `v4-wl-p301-A-g01` | 36,001 / 36.38 | **A 臂 12,000 场** seed_base 52920002 | 预注册 §2 要它当起点网 | **保留** |
| `v4-wl-p301-B-g01..g06` | 36,006 / 36.605 | B 六轮 12,000 场 seed_base 52920011..16 | 仅 `_p301-evidence\p301_diag{250,300}.ps1` 的 `$src` + `p301_shard.py verify --source` 历史调用记录 | **删** |
| `v4-wl-fold-any-c1-c1-g01` | 120 / 0.16 | R1/R2 语料副本（60 jsonl + 60 aux） | `NOTES.md:3848/3859` 当对拍夹具逐字引用 | 保留 |
| `v4-golden` | 5 / 0.01 | 2026-10-01 老 fixture | `python\tests\golden\forward-v4.json:12` | 保留 |
| `v4-wl-win-cal-c1-g01` | 1,001 / 0.33 | win-cal（jsonl 早已删，余 1,000 `.feat.bin`） | `\_win-cal-{count,diag,final,probe-read3}.py` 的目录常量 | 保留 |
| `v4-ceiling-common` | 5 / 0.00 | 5 臂 ceiling（余 summary.json） | `REPORT-ROUTING-CEILING-COMMON-RECHECK.md:3` + `style-vector-per-gen\CEIL-*` | 保留 |
| `expA-div`·`p2-11`·`p2-11b`·`p2-11c` | 2/2/2/9 · 均 ≤0.01 | 实验 A 分歧臂 / P2-11 家族（余 summary.json） | `EXPERIMENT-A-DIVERGENCE.md`、`_p2-11*run.ps1` 的 `--summary-*` 复用入口 | 保留 |
> 删前 `raw` 共 **24,062** 个 `g*.jsonl` = A 12,000 + B 12,000 + fold-any 60 + golden 2（**恒等，无遗漏目录**）。
## 2 逐项删除（落点自证 `StartsWith('S:\mahjong-training\raw\v4-wl-p301-B-g0')`）
| # | 绝对路径 | 文件 | GB | 依据 |
| --- | --- | ---: | ---: | --- |
| 1–6 | `S:\...\raw\v4-wl-p301-B-g0{1..6}` | 6,001 × 6 | 6.146/6.040/6.085/6.134/6.095/6.106 | `P3-01-RESULT.md §8` 结案 + 自变量错（六轮**全用同一固定学生** `net:v4-expert-defrep-g10@0#0.5`）⇒ 对本预注册不可用 |
|  | **合计释放** | **36,006** | **36.605** |  |
> 删前核对读数与端点**全在盘**：`_p301-evidence\{p301-reading-AB-rank_points,-AB-deals,-AB-FINAL,-A-vs-init}.json` + `p301-gate-AB.log` + `p301-B-round-summaries.json` + `p301-roster-v4-wl-p301-B-g0{1..6}.json`；端点 `tools\build\v4-wl-p301-B-g0{1..6}\net.bin`（各 5.34 MB，10-12 05:50–08:33）。
## 3 三盘前后（GiB）
| 盘 | 前 | 后 | 净 |
| --- | ---: | ---: | ---: |
| S: | 124.29 | **160.98** | **+36.69** |
| C: | 131.94 | 131.94 | 0 |
| E: | 550.88 | 550.88 | 0 |
## 4 保留清单（⛔ 一字未动）
A 臂 raw（12,000 / seed_base 52920002）· `v4-wl-fold-any-c1-c1-g01` · `v4-golden` · `v4-wl-win-cal-c1-g01` · `v4-ceiling-common` · `expA-div` · `p2-11`/`p2-11b`/`p2-11c` · 所有 `summary.json`/报告/台账 · `w-ladder\traces\v4-wl-fold-c1-g01`（1,001）· `style-reward\**` · `compact\v4-heads-g01` · `tools\build\**` · `S:\...\gate\**`。
## 5 超范围未删（仅登记）+ 命中说明
本批「raw 内诊断/smoke 且无引用」口径命中 **0 项**（raw 余下 9 个子目录**逐个都有引用**）⇒ 只删 B 六轮。超范围仅登记：`S:\...\gate\selfcheck-strong-to-weak`（201 文件/**799.7 MB**，`gate.py:347` 的 selfcheck 会自己重建它、`REPORT.md:202` 说明它不是实验数据）· `E:\mahjong-p301`（**空目录**）· `E:\p301-smoke`/`E:\mahjong-training`（**已不存在**）· `style-reward\**`（他人产物）。
