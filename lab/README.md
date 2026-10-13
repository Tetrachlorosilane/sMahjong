# `lab/` —— 实验文档与脚本归档（数据根 `w-ladder` 的可入库部分）

> **这份文件是"数据根里的实验档案"的入口。** 数据根 `S:\mahjong-training\w-ladder` 里那 3.4 GB
> 绝大部分是**用完即弃的大件**（`g*.jsonl` 轨迹、`*.feat.bin`、`compact`/`ckpt`），**不进仓库**；
> 真正有长期价值的是**报告 / 预注册 / 判决 / 小判决件 / 参数化脚本** —— 它们 2026-10-13 被搬进这里。
>
> - **判据与轮次记录**仍在 `NOTES.md` §6.5（本目录**不重复**判据，只做"实验 → 结论 → 原件"的索引）。
> - **脚本**在 `tools/lab/`（索引与"数据根可覆盖性"见 `tools/lab/README.md`）。
> - ⚠ 本目录是**归档**：里面的文档是**当时**的原文（数字/结论一字未改），**不是现行判据**。
>   现行判据一律以 `AGENTS.md` / `NOTES.md` / `docs/` 为准；两者冲突时以后者为准。
> - ⛔ 本目录**不占** `docs/` 的预算与索引纪律（`docs/INDEX.md` 只管 `docs/` 下的 `.md`）；
>   但"全仓 `§引用`必须可解"这条**照样适用于这里**（`node tools/doc-refs-check.mjs`）。

---

## 1. 目录结构

```
lab/
├── README.md                  ← 本文件（总索引）
├── p3-01/          (+evidence/)   P3-01 数据新鲜度（结案）
├── p3-pkg/         (+evidence/)   P3-PKG 打包闸门
├── bl13/  bl14/    (+evidence/)   BL-13 / BL-14 训练轮次
├── mslt/           (+evidence/)   多段链式训练
├── overlap/        (+evidence/)   采集 ‖ 训练 重叠（B1/B2/B3b）
├── axis-p2-11/                    P2-11 轴/域普查（domains · formal · minimal）
├── routing/        (+evidence/)   路由 / 多专家 ceiling（否定）
├── style/          (+evidence/)   风格控制：四根可推轴 + 两根判死轴
├── style-vector-per-gen/          风格向量逐代读数（76 份 md）
├── gpu/                           GPU 自对弈与迁移（未投）
├── housekeeping/   (+evidence/)   清理 / 轮次表 / 复算 / 稳定性
└── backlog/        (+evidence/)   想法与待办（catch-all）
```

`style/evidence/` 下的分组含义：`audit`（逐 tag 审计账）· `paired`（配对评测 POST）·
`invariance`（不变性自证）· `negative`（负向对照）· `shadow`（影子复算）·
`smoke`（冒烟）· `pdf`（`_profile-work` 的 N on/off）· 根级 = 轴的读数件。

`evidence/` 内的**子目录名沿用数据根原目录名**（`b1`/`b2`/`b3b`/`ab`/`gate`/`logs`…），便于与原件对号。

---

## 2. 实验索引（每行：一句话结论 → 判据在哪 → 原件在哪）

> 「判据在哪」一栏指向 `NOTES.md` §6.5 的**轮次**（§6.5 是训练接口的细节分册）。
> 「原件」一栏是**数据根**里的路径（根 = `S:\mahjong-training\w-ladder`）。

| 实验 | 一句话结论 | 判据在哪 | 原件（数据根） |
| --- | --- | --- | --- |
| **P3-01** 数据新鲜度 | **按预注册跑不出来**（自变量"数据新鲜度"未实现 + 两臂口径被动过）⇒ ⛔ 不是"无差"结论 | `NOTES.md` §6.5（第六十七轮起） | `P3-01-{PLAN,RESULT,AMENDMENT-B}.md` · `_p301-evidence\**` |
| **P3-PKG** 打包闸门 | 首个过线强度候选 `wl-p301b-g06`；⚠ 口径已动 ⇒ 只作**探索性端点**、⛔ 不跨族外推 | `NOTES.md` §6.5（第七十/七十一轮） | `P3-PKG-RESULT.md` · `p3pkg-*.json` · `ceiling-gate\p3pkg-B-g06-vs-defrep-verdict.json` |
| **BL-13** 多段/重叠训练 | **测不出**（自变量退化：`Δ(A−B)=−1.53` CI[−2.90,−0.14]、Bonferroni 含 0）；单段信任域随 `--kl-early-stop` **变**（0.03⇒13–17 步 / 0.10⇒120 步） | `NOTES.md` §6.5（第八十二轮） | `BL13-{PLAN,RESULT}.md` · `BL13-AGENTS-SUGGESTED.txt` · `_bl13\**` |
| **BL-14** 异步/全重叠 | **异步 ≠ 不可行**：`--init` 与全部 `behaviour_id` 脱钩仍须 `SystemExit`；陈旧度降级为预算/记账 | `NOTES.md` §6.5（第八十一轮） | `BL14-PLAN.md` · `_bl14\**` |
| **MSLT** 链式续训 | 续跑**逐位 == 一口气跑**；负向 = 清零动量必不同；外来 `--init` 须 exit 1 | `NOTES.md` §6.5 附录 E | `MSLT-{PLAN,PLAN-AMENDMENT,RESULT}.md` · `_mslt-tmp\mslt-compress-check.py` |
| **Overlap B1/B2/B3b** 采集‖训练 | 重叠**省 25–28%**（429.7→322.1 s 与 465.4→336.2 s **同号**）、读数相捕获率 100% | `NOTES.md` §6.5 附录 E | `B1-OVERLAP-RESULT.md` · `B3B-OVERLAP-RESULT.md` · `OVERLAP-EXPLORATION.md` · `_overlap\**` |
| **P2-11 轴/域普查** | 轴与域的可分辨性普查（domains/formal/minimal 三版）；**方向选择**族由此被否 | `NOTES.md` §6.5 附录 E | `P2-11-*.md` · `_axis-census-*.md` · `_p2-11{c,d}-domains.md` |
| **路由 / 多专家 ceiling** | **原理性不可行**：可学习 ceiling 仅无限制神谕的 **1.0%**、CI 含 0、7 种分层全 `\|t\|<2`；唯一例外 = 立直臂显著更强（+6.21 马点/场） | `NOTES.md` §6.5 附录 E | `ROUTING-CEILING-{COMMON,ROUGH}.md` · `REPORT-ROUTING-CEILING-COMMON-RECHECK.md` · `AXIS-{CENSUS,CANDIDATES}.md` · `ceiling-gate\BL03-family-AB-verdict.json` |
| **风格控制（四推轴）** | `riichi` **+2.95pp**（唯一走完强度闸门 ⇒ 采纳）· `meld` +13.30~+27.80pp（代价 = 打点 −360~−410）· `dama` +0.1859 · `pon` +6.06pp | `NOTES.md` §6.5（第七十六轮起） | `STYLE-CONTROL-STATUS.md` · `PREREGISTRATION-*.md` · `W-REPORT-*.md` · `companion-quality-*.json` |
| **风格控制（两根判死轴）** | `win`（结果量 ⇒ 行级奖励退化成重复加权）· `fold`（状态条件方向轴 ⇒ 两口径 CI 均含 0） | `NOTES.md` §6.5 附录 E | `PREREGISTRATION-FOLD*.md` · `W-REPORT-FOLD-*.md` · `DEFENSE-EFFECTIVENESS.md` |
| **方向族（封存）** | "状态条件下的方向选择"**不可学**（AUC **0.4799** CI 含 0.5、低于最强安慰剂 0.0491、ρ=−0.694；四条独立证据同向） | `NOTES.md` §6.5 | `PROBE-CONTRAST-{PLAN,RESULT}.md` · `EXPERIMENT-A-DIVERGENCE.md` |
| **强度闸门（dec6170-4g）** | `deals` +0.061、**护栏顺位点 +3.26 [+2.29,+4.26]** ⇒ 不拿强度换；⚠ 套间散布 = CI 半宽 **3.9×** ⇒ 条件于那 4 套牌山 | `NOTES.md` §6.5 附录 E | `GATE-dec6170-4g-strength-verdict.md` · `PROBE-dec6170*.md` · `paired\**` |
| **风格向量逐代** | 逐代风格剖面读数（CEIL-*/EXPA-*/P211-* 等 76 份）；工具 = `tools/style-vector.py` | `NOTES.md` §6.5 | `style-vector-per-gen\*.md`（同名 `*.json` 是原始向量，⛔ 不进仓库） |
| **GPU 自对弈 / 迁移** | 可行性已评估、**未投**（增量事件缓存复用是唯一入口） | `NOTES.md` §6.5 附录 E | `GPU-SELFPLAY-{FEASIBILITY,PLAN}.md` · `GPU-MIGRATION-PLAN.md` |
| **本机稳定性** | WHEA ID 19（核 corrected machine check）⇒ 缓解 = BIOS/微码 + 只降并行度（一次一个重负载 · `--workers` ≤9） | `NOTES.md` §6.5（低压力策略） | `HOUSEKEEPING-WLADDER.md` · `CLEANUP-LOG*.md` · `LOG-*.txt` |
| **轮次表 / 复算** | 轮次索引与 RERUN 合规（⛔ 撞 KL 早停的存档不许续） | `NOTES.md` §6.5 附录 E | `ladder-report.md` · `ladder-tables.md` · `RERUN-PLAN.md` · `_rerun\**` |
| **待办 / 想法** | `IMPROVEMENTS.md`（逐条：名称｜效果｜依据｜出处）· `BACKLOG.md` · `IDEAS-COMPLEMENTARY-AND-ARCH.md` | — | `IMPROVEMENTS.md` · `BACKLOG.md` · `IDEAS-COMPLEMENTARY-AND-ARCH.md` |

**索引格式样例**（新增实验照这三行写）：

```markdown
| **P3-01** 数据新鲜度 | **按预注册跑不出来**（自变量未实现）⇒ ⛔ 不是"无差" | `NOTES.md` §6.5（第六十七轮起） | `P3-01-RESULT.md` · `_p301-evidence\**` |
| **BL-13** 多段训练 | 单段信任域随 `--kl-early-stop` **变**（0.03⇒13–17 步） | `NOTES.md` §6.5（第八十二轮） | `BL13-RESULT.md` · `_bl13\**` |
| **路由 / 多专家** | 可学习 ceiling = 神谕的 1.0%、CI 含 0 ⇒ **否定** | `NOTES.md` §6.5 附录 E | `ROUTING-CEILING-COMMON.md` · `ceiling-gate\*.json` |
```

---

## 3. 没搬的东西（原件仍在数据根，需要时回去取）

| 类别 | 规模 | 为什么不搬 |
| --- | --- | --- |
| `traces\**`（`g*.jsonl` 逐场轨迹） | **2.87 GB** / 1,056 件 | 大件；且已按"删轨迹、留摘要"清理过（`NOTES.md` §6.5 第八十二轮） |
| `_overlap\**\*.jsonl`（重叠采集） | **0.63 GB** / 37 件 | 同上；只留 `.json`/`.csv`/`.md` 读数件 |
| `style-vector-per-gen\*.json`（逐代向量原始件） | **0.11 GB** / 99 件 | 大件；`.md` 摘要在 `lab/style-vector-per-gen/` |
| `traces-post\**` · `_bl13\*.jsonl` · `_bl14\*.jsonl` · `_p301-evidence\*.jsonl` | **~11 MB** | ⛔ `*.jsonl` 一律不搬（体积闸门） |
| 各目录里 **>2 MB 的单文件**（如 `_bl13\*\summary.json` 1.1 MB 级、`style-ladder.json` 465 KB 已搬） | — | 单文件上限；需要时按原件路径取 |
| `logs\**` 的逐实验 stdout（85 个 `v4-wl-*.log` + 19 个 `post-*.log`） | 1.4 MB | 是**过程日志**不是判决；判决已在 `paired\**\*-POST.json` 与 `audit\**` |
| `*.npz` / `*.feat.bin` / `compact` / `ckpt` / `__pycache__` / `*.pyc` | — | ⛔ 体积闸门明令不搬 |

**体积闸门**：本目录 + `tools/lab/` 合计 **618 件 / 3.78 MB**（目标 ≤5 MB）。
口径 = **文档 N 份 + 脚本 M 份 + 小判决件 K 份**；单文件 >2 MB 与 `*.jsonl` 一律只留索引。

---

## 4. 须知

- ⛔ **本批未改** `NOTES.md` 与 `AGENTS.md`（另一任务正在写 `NOTES` 第八十三轮）——
  本索引里记的**待入档判据**（BL-14 的"异步不可行"三条、`_bl13` 证据束"保留 vs 删"的冲突待裁、
  以及 `IMPROVEMENTS.md` 里 110 条 `AGENTS §6.5` 引用是否要收编）**尚未进 NOTES**，见下条。
- ⚠ **需要拍板的两件事**（原件在数据根，本目录只留指针）：
  1. `HOUSEKEEPING-WLADDER.md` §「冲突待裁」：`NOTES.md` §6.5 第八十二轮残留行字面写"保留 `_bl13\**`"，
     与"删轨迹、留摘要"冲突 ⇒ 删前请对这条拍板。
  2. `IMPROVEMENTS.md` 是**逐条待办**（含 110 条对本仓文档的引用），是否收编进 `NOTES.md` 由后续会话定。
- **脚本只收**"参数化或可覆盖数据根"的；已被取代的一次性脚本**不收**（名单与取代者见
  `tools/lab/README.md` §3）。
- 本目录的文档是**当时快照**：里面的"下一步/待办"可能**早已做完**，⛔ 别当现行任务清单 ——
  现行"下一步"只有 `docs/HANDOVER.md` §1 的「下一步（唯一清单）」。
