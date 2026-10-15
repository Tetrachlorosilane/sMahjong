# AGENTS.md 按上下文智能切片 — 只读设计（2026-10-15）
> ⛔ **本批只读**：未移动 / 未修改任何现有文件、未提交。执行另批，按**本文** §8 顺序**逐节原子**做、每步跑**本文** §7 的检查。目标：主 `AGENTS.md` 只留**项目基本规范**（跨域稳定判据），其余按**域**切片；主文件顶部有**焦点块**指向当前主线（现 = 训练）。

## 1. 取证（每条给可复核出处）
| # | 结论 | 原始出处 |
| --- | --- | --- |
| 1.1 | **自动加载只认精确文件名** `AGENTS.md` / `CLAUDE.md` + overlay `AGENTS.local.md` / `CLAUDE.local.md`，每目录各一次（项目根 `.git` → cwd），**没有任何 glob** ⇒ `AGENTS-*.md` 永不自动加载；子目录同名候选不进基线，但 fs 工具成功触达更深目录后会追加 `Additional instructions from: <path>`（延迟注入）⇒ ⛔ 切片不得叫 `AGENTS.md`，也不得放进 cwd 链 | `app.asar` → `node_modules/@deepseek-ai/dsh-agent-instructions`：`DEFAULT_INSTRUCTION_FILE_CANDIDATES = ["AGENTS.md", "CLAUDE.md"]`（asar 偏移 14305587）、`allExistingInstructionFiles()` 逐名 `join(dir, candidate)` 精确 `stat`（偏移 14325865，无通配）、`discoverInstructionFiles()` 只走 `ancestorChain(projectRoot, cwd)`（偏移 14326538）、追加模板（偏移 14294160） |
| 1.2 | 预算：渲染消息上限 `maxBytes`，**实时生效值 65536** ⇒ 文件字节硬线 **65,244**（余 292 B 是包裹层）。本仓自动加载面 = 根 `AGENTS.md` **一个文件**：无嵌套 `AGENTS.md`、无 `CLAUDE.md`/`AGENTS.local.md`、`$DSH_HOME/AGENTS.md` 不存在 | `C:\Users\HP\.dsh\.agent-presets\router-standard\agent.cordis.yml:50`、`router-spec\agent.cordis.yml:45`；`tools/doc-refs-check.mjs:29-30`；`Get-ChildItem -Recurse -Include AGENTS*.md` 仅得 `AGENTS.md`(64,367 B) 与 `lab/housekeeping/AGENTS-COMPRESS-APPENDIX.md`（非候选名）；`Test-Path C:\Users\HP\.dsh\AGENTS.md` = False |
| 1.3 | `doc-refs-check` 三处会因搬家而红：②按 `\n{i}. **` 查 §2.3-1..15、按**字面**查 21 个章节标题；③按 `^#{2,6} +(?:§)?([0-9.]+)` 收号做 §引用解析；`SAME_TOPIC` 要求两侧标题**字面片段**。另两个校验器**不受影响**（前提：不搬 `NOTES.md §6.5` 正文、不动 HANDOVER） | `tools/doc-refs-check.mjs:71-81, 84-96, 122-141, 160-176`；`tools/round-index.mjs:24,38,81`（只读 `NOTES.md`）；`tools/handover-facts.mjs:42,99,162`（只读 `docs/HANDOVER.md`，把 `\| **下一步（唯一清单）** \|` 当保留字） |

⇒ **命名结论**：`AGENTS-<域>.md` 安全（不在候选表、不匹配任何 glob、不触发延迟注入）；⛔ 禁用 `AGENTS.local.md`（是 overlay 候选）与 `CLAUDE*`。

## 2. 切片地图（字节 = 现 § 正文；引用 = 全仓计数，`A/N` = 写成 `AGENTS §x` / `NOTES §x` 的条数）
全仓 §引用 **909** 条 · `AGENTS.md` **64,367 B** · 逐节合计 64,345 B。
| § 节 | 字节 | 引用 A/N | 目标文件 |
| --- | --- | --- | --- |
| §6.2 牌桌布局与绘制 | 4,699 | 11 / 12 | `AGENTS-CLIENT.md` |
| §6.3 牌桌行为、规则与资源 | 3,170 | 6 / 1 | `AGENTS-SERVER.md` |
| §6.4 协议、文案与结算 | 4,921 | 12 / 9 | `AGENTS-PROTO.md` |
| §6.8 身份（uuid）· §6.9 托管与投票 | 1,950 + 1,555 | 4/2 · 0/0 | `AGENTS-PROTO.md` |
| §6.6 teacher 五层取舍 · §7 常见症状 | 1,403 · 4,913 | 10/11 · 5/3 | `AGENTS-TRAIN.md` · `AGENTS-SYMPTOM.md` |

**搬走合计 22,611 B**。**主文件保留清单**（= 跨域稳定规范）：标题+序言 174 · §0 怎么用 857（2 引用）· §2.3 十六条坑 5,451（29/30）· §1 这是什么 1,660（1）· §2 铁律 35 · §2.1 379（12）· §2.2 协议字段 2,855（12）· §2.4 危险操作 283 · §3 环境与构建 2,310（§3 1 · §3.1 1/3 · §3.3 1 · §3.4 0/1）· §4 验证五层 6,823（37/26）· §5 目录结构 1,116 · §6.1 通用流程 1,921（8/1）· **§6.5 训练接口 13,861（143/371）** · §6.7 无用代码清理 1,119（4/1）· §8 文档维护 2,275（14）· §9 素材管线 615（3/5）= **41,734 B**。
⇒ **主文件 = 41,734 + 焦点块/路由表/7 条指针行（新写 ≈2,100 B）≈ 43.8 KB**，距硬线 **余 21.3 KB**（不再需要"为腾 1 KB 搬家"）。⚠ **§6.5 整段留主文件**：它是当前焦点域的 ⛔ 硬判据集（31/69 行含 ⛔ = 9,170 B）、143 条引用指着它；⛔ 不要按"含 ⛔ 的行"机械拆（多行 bullet 的续行不含 ⛔，会**拆断句子**）。训练端"三分"是**索引层**（见本文 §6），不复制判据。

## 3. 命名方案（为什么不冲突）
| 文件 | 内容 | 安全性 |
| --- | --- | --- |
| `AGENTS.md` | 项目基本规范 + 焦点块 + 域路由表 + 7 条指针标题 | 唯一候选名（1.1） |
| `AGENTS-SERVER.md` · `AGENTS-CLIENT.md` · `AGENTS-PROTO.md` · `AGENTS-SYMPTOM.md` | 服务端/规则 · Qt 客户端/布局/素材 · 协议/身份/托管/投票（"通信端"）· 症状速查 | 精确名表外、无 glob（1.1）⇒ 永不自动加载，也不会像 `AGENTS.md` 那样被延迟注入 |
| `AGENTS-TRAIN.md` + `-CURRENT` / `-HISTORY` / `-FIXES` | 训练族入口（族内路由 + 三分边界）/ 现行上下文 / 既往历史 / 关键纠错 | 同上；⛔ **不得**取名 `AGENTS.local.md` |
| `lab/housekeeping/AGENTS-SLICING-APPENDIX.md`（**执行阶段新建**） | 被**改写/删除**行（非逐字搬走段）的逐字留档 | `lab/**` 不在默认候选链；文件名非候选名 |

全部落在**仓库根**（与主文件同目录 ⇒ 相对路径一写就对），且**不进 `docs/`** ⇒ 不触发 `docs/INDEX.md` 登记义务（`doc-refs-check.mjs:189-192` 只管 `docs/`）。

## 4. 焦点匹配机制 + 改指/转交协议
主 `AGENTS.md` 顶部（紧随标题）放**唯一**焦点块，**≤700 B / ≤10 行**，⛔ 不放轮次号/日期/权重路径（易变物一律进 `AGENTS-TRAIN-CURRENT.md`）：
```markdown
<!-- FOCUS:BEGIN —— 只改这一段（改指协议：见本文 §4；执行时落在主文件第 8 节下**拟新增**的子节） -->
### 焦点：当前主线 = **训练**
- **生效域**：`python/**` · `trainer/**` · `docs/{TRAINING,TRAINING-V4,TRAINER-CPP,FEATURES-V4,BOT-AI}.md` · `lab/**` · `NOTES.md §6.5`
- **先读**：`AGENTS-TRAIN.md` → 现行 `AGENTS-TRAIN-CURRENT.md` / 历史 `AGENTS-TRAIN-HISTORY.md` / 纠错 `AGENTS-TRAIN-FIXES.md`
- **非当前域**：`AGENTS-SERVER.md` · `AGENTS-CLIENT.md` · `AGENTS-PROTO.md` · `AGENTS-SYMPTOM.md`
- **改指判据**（三条**同时**成立）：① 连续 ≥2 个工作轮触碰的路径**全部**落在同一个非当前域；② 这 2 轮**一次都没碰**当前域路径；③ 该域已有切片文件（见路由表）。
- **改完必跑**：`node tools\doc-refs-check.mjs`；若同时改了 `docs/HANDOVER.md` 的链接，再跑 `node tools\handover-facts.mjs --check`。
<!-- FOCUS:END -->
```
**触发条件** = 上面那三条判据（⛔ "上下文变了一下"不算，必须 ①②③ 同时成立）。**改哪几行**（4 处，3 处在块内）：① `### 焦点` 的域名；② `先读` 行的族文件名；③ `生效域` 的路径集合；④ 在 `AGENTS-TRAIN-HISTORY.md` **纯追加**一行"焦点 X→Y（日期）"。**改完必跑**：`doc-refs-check.mjs`（预算+编号+§引用+INDEX）PASS；动了 HANDOVER 再加 `handover-facts.mjs --check`。⚠ 焦点块**不是**"下一步"：⛔ 不写 `| **下一步（唯一清单）** |` 之类措辞（那是 `docs/HANDOVER.md §1` 的保留字，`handover-facts.mjs:162`）。

## 5. 索引设计（**每个**切片文件开头都有，≤12 行/文件）
```markdown
## 路由：我要…… ⇒ 读哪一节
| 我要…… | 读这里 |
| --- | --- |
| <本域最高频任务 A> | §x.y / `docs/…` |
| 我要动**别的域** | 回 `AGENTS.md` 焦点块 → 路由表 |
> **何时切回主 `AGENTS.md`**：本域任务收尾、或改动跨出上面这份清单时 —— 先读主文件 §2 铁律，再按 `<FOCUS:BEGIN>` 的改指判据决定是否转交。
```
（本域任务的"完整清单"≤8 行，模板照抄即可。）新增域只在**主文件路由表**加一行 + 建一个切片文件；⛔ 切片之间**不互相复制**判据，只写"见 `<文件> §x.y`"。

## 6. 训练端三分（边界 + 迁移来源）
| 类 | 边界（收什么 / ⛔ 不收什么） | 迁移来源 |
| --- | --- | --- |
| **既往历史** `AGENTS-TRAIN-HISTORY.md` | 已结案轮次的结论与日期、被推翻的旧口径；⛔ 不收判据、不收数字推导 | `NOTES.md §6.5` 轮次索引（75 小节，`round-index.mjs` 管）· `lab/**` 归档目录 · `docs/HANDOVER.md §3` 历史快照 |
| **现行上下文** `AGENTS-TRAIN-CURRENT.md` | 正在跑的轮次、当前口径选择、权重/数据根路径；⛔ 不收历史结论 | `docs/HANDOVER.md §1`（唯一下一步）· `lab/{bl13,bl14,mslt,nextphase,gpu,overlap}/` · `docs/{TRAINING-V4,FEATURES-V4}.md` |
| **关键纠错** `AGENTS-TRAIN-FIXES.md` | **训练域专属**纠错集，编号 `T-nn`，每条三段：症状 → 判据 → 回归命令 | 新踩的坑；`NOTES.md §6.5 附录 A–F` 只索引不复制；`lab/housekeeping/AGENTS-COMPRESS-APPENDIX.md` 里训练相关片段 |

⚠ **不重复**：`T-nn` 只收**不在** `AGENTS.md §2.3` 与 `§6.5` 判据里的项，重合的一律写成"见 `AGENTS.md §2.3-n`"。`T-nn` 不被 `doc-refs-check` 的 §引用正则匹配（前缀必须正好是 `AGENTS`/`NOTES`），故不污染 909 条口径。

## 7. 校验器改动方案（为保住 909 条引用）
**推荐（零改校验器）= 主文件留"指针标题"**：每个搬走的节在原位置留一行标题、**含原标题字面片段**，例：`### 6.2 牌桌布局与绘制（客户端 —— 最容易被改坏的一块）⤴ 正文见 AGENTS-CLIENT.md`。`sectionsOf()` 只认标题里的号（L89）⇒ `aSec` 仍含 `6.2/6.3/6.4/6.6/6.8/6.9/7`，143+48 条引用继续解出；② 的 21 个章节字面（含 `## 7. `、`### 6.2`…）与 `SAME_TOPIC` 的 AGENTS 侧片段（L128-134）**原样保住**。**回归判据**：`node tools\doc-refs-check.mjs` **PASS**，③ 行打印的 `N 条 §引用全部解得出目标` 满足 `N ≥ 909` 且 `bad == 0`；`round-index.mjs --check` / `handover-facts.mjs --check` 输出与基线**逐字相同**。**备选（连指针标题也不留 ⇒ 主文件更瘦）**：在 `doc-refs-check.mjs` 加 `SLICE_MAP = { '6.2':'AGENTS-CLIENT.md', … }`，`aSec` 取 `union(aSec, keys(SLICE_MAP))`，并**新加断言**"映射文件存在"且"映射文件里真有该节号"（防映射说谎）；否则等于把"引用可解"降级成一张无人校验的表。

## 8. 零丢失证明 · 迁移顺序 · 回滚
**零丢失（逐节三段）**：① **逐字搬走的段**：切片内该段逐字 == `git show HEAD:AGENTS.md` 抽出的原文（按段 sha256，必须相等）；② **被改写/删除的行**：逐字落 `lab/housekeeping/AGENTS-SLICING-APPENDIX.md`，并跑**三组机械核对**（判据句 CJK bigram / 代码 token / 数字常量，**"未找到"必须 0**，口径照抄 `AGENTS-COMPRESS-APPENDIX.md:267-278`）；③ **字节守恒对账**：逐节登记（搬走 N B ⇒ 目标文件 +N B、主文件 −N B；指针行 +M B 单独记账），`Σ搬走 == Σ目标增量`。**顺序（每步 `doc-refs-check` PASS 才进下一步；任何中途状态都不许"引用悬空"）**：① 冻结基线（`git rev-parse HEAD` + `sha256 AGENTS.md`）→ ② **先建全部切片文件 + 留档件**（纯新增；跑检查：引用计数不变、PASS）→ ③ 逐节**一次 `edit` 内原子完成**"删正文 + 留指针标题"（顺序：§7 → §6.6 → §6.9 → §6.8 → §6.3 → §6.2 → §6.4，先做引用最少的）→ ④ 加焦点块 + 路由表 + 改写主文件第 8 节（文档纪律）→ ⑤ 全量回归。**回滚**：单节失败 ⇒ `git checkout -- AGENTS.md` 后**重放已成功的节**（切片是纯新增，可留）；整体失败 ⇒ `git checkout -- AGENTS.md` + 删新建切片（新增文件对既有引用零影响）。

## 9. 风险与缓解（逐条）
① 静默截断：主文件降到 ≈43.8 KB（余 21.3 KB），每次改完跑本文 §7 的预算判据 → ② 引用断裂：§7 推荐方案（指针标题含原字面），回归判据 `N ≥ 909 且 bad == 0` → ③ 自动加载语义误判：本文 §1 已实测精确名 + 无 glob，命名全走 `AGENTS-*`，验收清单加"候选名不匹配"断言。
④ 切片后**判据被 LLM 漏读**：判据（§2.3/§6.5）**整段留自动加载面**；每切片开头有 ≤12 行路由表 + "何时切回主 AGENTS"；主文件焦点块给"先读/生效域"，并把"何时必读"写成**触发条件**而不是建议。
⑤ 与 `NOTES.md`/`lab/**` 重复：切片只放**判据与路由**，案例/红证/实测/推导一律指 `NOTES.md`；`lab/**` 保持"归档、非现行判据"（`lab/README.md` 已声明）⇒ 不造重复落点。⑥ 未来 harness 加 glob 候选：切片名不是候选表任一字面；改名前先跑 `doc-refs-check` 并人工核对候选表。

## 10. 执行阶段验收清单（≤10 条，全部机械可核验）
1. `git status --porcelain` 只列**新增**切片文件 + 被改的 `AGENTS.md`，⛔ 无其他改动。 2. `node tools\doc-refs-check.mjs` **PASS**：§引用计数 `≥ 909`（新增文档的合法引用会让它变大，**不是**错）且 `bad == 0`，**且每个新建切片文件里的每条 §引用都指向现行**存在的**章节号**（本批实测踩过：设计件里写了**尚不存在**的 `AGENTS.md` 第 8.1 节 ⇒ 916 条里 1 条解不出 ⇒ 全仓 FAIL；⛔ 拟新增号一律写成"第 x.y 节（拟定）"这类**不带 § 号**的措辞）。
3. `node tools\round-index.mjs --check` 与 `node tools\handover-facts.mjs --check` 输出与基线**逐字相同**。 4. 逐节字节对账：`Σ(搬走 B) == Σ(目标文件增量 B)`，误差 0。
5. 逐字段落 sha256 全等（`git show HEAD:AGENTS.md` 抽段 vs 切片内该段）= **0 处不等**。 6. 留档件三组机械核对的"未找到" = **0**。
7. 主文件字节 `< 61,200`（留 4 KB 余量），焦点块 `≤ 700 B / ≤ 10 行`。 8. 每个切片文件都含"路由表（≤12 行）+ 何时切回主 AGENTS"一句（逐文件 `grep`）。
9. 新文档里无 `AGENTS.local.md` / `CLAUDE*` 误用；无新建 `docs/**` 文件（否则要登记 `docs/INDEX.md`）。 10. ⛔ 未提交（等批准）；`raw/**`、`style-reward/**`、`tools/build/**`、`compact\v4-heads-g01`、`traces\v4-wl-fold-c1-g01` 的 mtime 未变。
