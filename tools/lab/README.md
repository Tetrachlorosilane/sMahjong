# `tools/lab/` —— 数据根 `w-ladder` 的实验脚本归档

> 这里是 `S:\mahjong-training\w-ladder` 下那批**实验脚本**的入库副本（2026-10-13 搬运，原件仍在数据根）。
> 文档侧索引在 **`lab/README.md`**；判据与轮次记录在 **`NOTES.md` §6.5**。
>
> ⚠ **定位**：这些是**一次性实验的复算工具**，不是本仓的常规工具链 ——
> 常规工具（`tools/w-ladder.py` · `tools/style-vector.py` · `tools/overlap-orchestrator.py` ·
> `tools/ceiling-gate.py` · `tools/v4-forward-gpu.py`…）仍在 `tools/` 下，**本目录不取代它们**。
> 多数归档脚本**反过来依赖**它们：常见形态是 `SPEC = importlib.spec_from_file_location(REPO/"tools"/"w-ladder.py")`
> 或直接 `subprocess` 调 `tools/style-vector.py`；所以搬动本目录时**别改它们对 `tools/` 的相对假设**。

---

## 1. 数据根可覆盖性（每份脚本都在文件头标注了，格式统一）

搬运时对每个脚本 `grep` 了 `S:\` / `E:\`，按"二选一 + 如实标注"落在文件头（Python 用 `#` 行，
PowerShell 用裸字符串，位置在 `param()` 之后）。三类含义：

| 标注 | 数量 | 含义 | 搬去别的机器要做什么 |
| --- | --- | --- | --- |
| **① 可覆盖** | 16 → **17**（2026-10-16 本批 +1：`mv1-preflight.py`） | 数据根默认 S:，但**有命令行/参数**指定输入目录（`--work` / `--raw-dir` / `--dir` / PS 的 `param(...)`） | 把残留的 `S:` 字面路径换成你的数据根，或直接传参 |
| **② 可覆盖** | 25 | 文件里**不含** `S:`/`E:` 绝对路径：路径由调用方/工作目录/参数决定 | 换工作目录即可，通常不用改代码 |
| **③ ⚠ 硬编码路径** | 33 | 数据根**写死在字面路径里**，未参数化 | ⛔ 仅供本机复现；换机器必须先改那些字面路径 |

> ①/② 合计 41 份是"参数化或可覆盖"的；③ 的 33 份**照实标注**、不假装能移植（多数是一次性探针）。

---

## 2. 清单（**77 份** `*.py` / `*.ps1` —— 2026-10-16 实测；下表的 ①/②/③ 计数是 2026-10-13 搬运时的登记 + 本批 +1 份 ①）

### 2.0 MV-1 前置检查（本批新增）

| 文件 | 可覆盖性 | 作用 |
| --- | --- | --- |
| `mv1-preflight.py` | ① | **辅助头 `own_win` 的标签前置检查**（只读 · fail-closed · 零训练）：`aux_win_flag` 在不在 / 标量 / 覆盖率 / 非零比 / `(小局,座位)` 内常数 / 每局至多 1 家和了 / 该家 == 该局 `delta` 最大者 / 不是 `delta>0` 的别名；`--selftest` = 6 组判据自检（1 正向 + 5 负向，纯内存不读盘）。判据与预注册见 `lab/mv1/MV1-{PLAN,FEASIBILITY}.md` |

### 2.1 重叠编排 / 轮次（B1 · B2 · B3b · rerun）

| 文件 | 可覆盖性 | 作用 |
| --- | --- | --- |
| `_rt-critcheck.py` | ① | 判据侧口径自检：拿 30 场真轨迹把 `w-ladder.py` 的 `_student_per_game` 与 `style-vector.py` 的 `counts` 逐项对账 |
| `_rt-reconcile.py` | ② | 立直轴读数对账（→ `_rt-reconcile-g01.json`） |
| `_rt-invariance.py` | ③ | 立直轴不变性自证 |
| `_rt-negcheck.py` | ③ | 立直轴负向对照 |
| `_rerun\`（3 份，见 `lab/housekeeping/evidence/rerun/`） | ② | 轮次复算驱动（`rerun_driver/readout/compliance.py`） |

### 2.2 `dama` 轴（能立而不立）

| 文件 | 可覆盖性 | 作用 |
| --- | --- | --- |
| `_dama-reconcile.py` | ② | 从轨迹**重数**"能立而不立"（⛔ 不 import `style_reward`，独立实现），与审计账与风格向量对账 |
| `_dama-invariance.py` | ③ | dama 轴不变性自证（含 `_dama-shadow` 影子拷贝对拍） |
| `_dama-negcheck.py` | ③ | dama 轴负向对照（打乱/置换标签） |

### 2.3 `fold` 轴（被威胁时的降り率）

| 文件 | 可覆盖性 | 作用 |
| --- | --- | --- |
| `_fold-selfcheck-block.py` | ② | dama / fold 判据与奖励的**同口径**冒烟（`BonusSpecError` 负向） |
| `_fold-any-smoke.py` | ③ | "任意威胁"口径的冒烟（16900 B，含停损） |
| `_fold-invariance.py` / `_fold-negcheck.py` | ③ | 不变性自证 / 负向对照 |
| `_fold-strata.py` | ③ | 分层读数（`|t|<2` 的七种分层来源） |
| `_fold-paired-partial.py` | ③ | 配对评测的部分池化读数 |
| `_fold-recollect-verify.py` | ③ | 重采后逐场复现自证 |
| `_fold-verify.py` | ③ | 判决复算 |

### 2.4 `meld` / `meldtype` 轴

| 文件 | 可覆盖性 | 作用 |
| --- | --- | --- |
| `meld-collect.py` | ① | 鸣牌剂量臂的采集驱动（低剂量 1515/3030） |
| `meld-dose-check.py` | ② | 剂量对账（实际付奖行率 vs 名义剂量） |
| `meld-tables.py` / `meld-lowdose-tables.py` | ① | 生成 `meld-tables.md` / `LOWDOSE-*.txt` 表（读 `style-vector-per-gen/*.json` + `paired/*-POST.json`） |
| `_meldtype-smoke.py` | ③ | 鸣牌**类型**轴冒烟 |
| `_meldtype-invariance.py` / `_meldtype-negcheck.py` | ③ | 不变性 / 负向 |
| `_meldtype-repro-n2.py` | ② | n=2 复现 |

### 2.5 复现 / 配对

| 文件 | 可覆盖性 | 作用 |
| --- | --- | --- |
| `repro-pairs.py` | ① | 同 seed 逐场配对表生成 |
| `repro-tables.py` | ① | 配对表读数 → `repro-tables.md` |
| `_repro-combine.py` | ② | 多批配对结果合并（跨批次拼合的禁令判据在此） |
| `verify-dec6170-4g.py` | ③ | **独立复算** `final-dec6170-4g` 闸门读数（只读 `summary.json`，不读 `gate.py` 结论） |

### 2.6 P2-11 轴/域普查（含 A 路实验）

| 文件 | 可覆盖性 | 作用 |
| --- | --- | --- |
| `axis-census.py` | ② | 轴普查（→ `AXIS-CENSUS.md` / `_axis-census-{head,table,tail}.md`） |
| `_p2-11-{collect.ps1,run.ps1,analyze.py}` | ①①② | P2-11 基线的采集/驱动/读数 |
| `_p2-11b-{collect.ps1,run.ps1,hardself.ps1,analyze.py,extract.py,dforward.py,domdiff.py}` | ①③③②②②② | P2-11b 域内替换网 |
| `_p2-11c-{collect.ps1,run.ps1,sweep.ps1,analyze.py,extract.py,dforward.py,domdiff.py,cond.py,cond-selftest.py}` | ①①①②②②②②② | P2-11c 域条件化分叉 |
| `_p2-11d-{collect.ps1,run.ps1,recollect.ps1}` | ①①③ | P2-11d 域探针 |
| `_expA-{collect.ps1,run.ps1,analyze.py,extract.py,logits.py}` | ①③②②② | 实验 A（路由/多专家）复核：抽取 → 双 net 前向 → 分歧域 + 交叉拟合路由 |
| `_probe-contrast-{extract,fit,fwdcheck}.py` | ②②② | 同状态**对比式**优势探针（"方向选择不可学"的四条证据之一） |

### 2.7 风格控制 / 质量伴随量

| 文件 | 可覆盖性 | 作用 |
| --- | --- | --- |
| `_prep-style.ps1` / `_prep-style-c2.ps1` | ③ | 风格桌准备（24 排列座次） |
| `_profile-collect.ps1` | ① | N on/off 画像采集（→ `_profile-work/`） |
| `_quality-byte-regress.py` | ③ | 逐字节回归（产物与基线逐字节比） |
| `_quality-direction-selfcheck.py` / `_quality-direction-contrast-n2.py` | ③ | 质量方向自检 / n=2 对比 |
| `_align-verdict-selfcheck.py` / `_align-verdict-realdata.py` | ② | 对齐判决：自检口径 + 真数据口径 |
| `_win-cal-final.py` | ③ | 和率轴校准读数 |

### 2.8 路由 / ceiling / 打包闸门 / 前向探针

| 文件 | 可覆盖性 | 作用 |
| --- | --- | --- |
| `_ceiling-rough.py` | ③ | 便宜 ceiling 的粗糙版读数（`tools/ceiling-gate.py` 的**前身**） |
| `_p3pkg-mount-test.ps1` | ① | P3-PKG 包挂载闸门（起服务端 + 客户端 peek） |
| `_p3pkg-readout.py` | ③ | P3-PKG 读数 |
| `_p301-readout.py` | ③ | P3-01 读数 |
| `.dsh-verify-{ceiling,final,seat,seat2}.py` | ③③③③ | 子代理**独立复核**脚本（ceiling / 终局 / 座位口径），属过程审计 |

---

## 3. ⛔ 不收的一次性脚本（原件在数据根，**已被取代**）

| 脚本（数据根原件） | 被谁取代 | 为什么不留 |
| --- | --- | --- |
| `b1-overlap.py`（43 KB） | **`tools/overlap-orchestrator.py`**（`tools/AUTOMATION.md` §8） | 重叠编排已收敛成单一编排器；留着会让人以为还要按老流程跑 |
| `b2-overlap.py`（26 KB） | 同上 | 同上 |
| `b3b-overlap.py`（22 KB） | 同上 | 同上 |
| `b3b-memprobe.py` / `b3b-phases.py` / `b3b-specs.py` / `b3b-summary.py` | 同上（内存/相位/规格都被编排器吸收） | 一次性拼装件；口径（已提交内存 vs 工作集、退出码 5>7>6>0）已进 `NOTES.md` §6.5 第七十六轮 |
| `_ceiling-probe.py` / `_ceiling-probe2.py` / `_ceiling-probe3.py` / `_ceiling-show.py` / `_ceiling-dims.py` / `_ceiling-crosschain.py` | **`tools/ceiling-gate.py`**（`tools/AUTOMATION.md` §7） | 探针被正式闸门取代（判据：≤30 min、零训练、fail-closed） |
| `_fold-inspect.py` / `_fold-rate.py` / `_fold-readout-print.py` / `_fold-reward-recheck.py` / `_fold-caliber-30.py` / `_fold-selfriichi-probe.py` / `_fold-any-readout.py` / `_fold-any-stoploss.py` | 被 `_fold-strata.py` / `_repro-combine.py` / `repro-pairs.py` 的读数路径吸收 | 逐个小口径的临时读数脚本；结论已在 `lab/style/` 的报告与 `W-REPORT-*` 里 |
| `_meldtype-readout.py` / `_meldtype-notes-tidy.py` | 被 `meld-*-tables.py` + `_quality-byte-regress.py` 吸收 | 临时读物与"文档整理"脚本，非判据工具 |
| `_dama-inspect.py` | 被 `_dama-reconcile.py` 取代 | 只做抽查，无判据 |
| `_align-prefight.py` / `_align-summary.py` | 被 `_align-verdict-{selfcheck,realdata}.py` 取代 | 前置检查/汇总并入判决脚本 |
| `_rt-report-read.py` | 被 `_rt-reconcile.py` 取代 | 同上 |
| `_p2-11-forward.py` / `_p2-11b-diagdiff.py` / `_p2-11c-cond.py` | 被同族 `-dforward.py` / `-domdiff.py` / `-cond-selftest.py` 取代 | 旧接口的同功能实现 |
| `_win-cal-count.py` / `_win-cal-diag.py` / `_win-cal-probe-read3.py` | 被 `_win-cal-final.py` 取代 | 中间读数 |
| `_p2-11d-probe.ps1` | — | 纯拼装壳（把 `_p2-11d-*.ps1` 串起来）；其**读数件**已收进 `lab/style/evidence/` |

---

## 4. 与仓库既有工具的关系（别重复造）

| 本目录的脚本 | 依赖 / 对位的仓库工具 |
| --- | --- |
| `_rt-critcheck.py` · `meld-*-tables.py` · `_rt-reconcile.py` | **`tools/w-ladder.py`**（判据侧口径）、**`tools/style-vector.py`**（风格量与 `counts`） |
| `_quality-*.py` · `_align-verdict-*.py` | **`tools/w-ladder.py`** 的 `post`/`measure` 产物（`paired/*-POST.json`、`style-vector-per-gen/*.json`） |
| `_ceiling-rough.py` · `_p3pkg-*` | **`tools/ceiling-gate.py`**（BL-03 便宜闸门） |
| `_expA-*.py` · `_p2-11*.ps1` | **`tools/v4-forward-gpu.py`** / **`tools/v4dforward-gpu.py`**（v4 前向）、`_p2-11d-probe.ps1` 的挂载链 |
| `_profile-collect.ps1` | **`tools/style-profile.py`**（8 桌画像） |

> ⚠ 归档脚本**跑起来仍会写数据根**（`S:\mahjong-training\w-ladder` 的 `raw/` `traces/` `paired/`…）——
> 它们**不是只读工具**，别在无意中往数据根落新东西。要复现请先看清楚脚本的 `REPO` / `STAGE` 常量。
