# 文档总索引（板块 · 快速索引）

> **这是文档的唯一入口。** 按**板块**列文档、按**任务**给快捷方式。
> 规矩不变（`AGENTS.md` §8）：**判据留 `AGENTS.md`、细节留 `NOTES.md`**，本文件只管"去哪儿找"。
> ⚠ 本索引**自带校验**：`node tools/doc-refs-check.mjs` 会检查「这里列出的路径都存在」且
> 「`docs/` 下每份 `.md` 都被列出」——**加文档忘了登记会被抓出来**。

## 0. 三十秒版：我要……去哪读

| 我要…… | 入口 | 关键锚点 |
| --- | --- | --- |
| **新会话接上 / 看当前进度** | **`docs/HANDOVER.md`** | 十秒速览（**§1 是唯一的"当前下一步"**）· 环境命令 · 操作纪律 · 待决问题；⚠ §3 是**历史规划快照**，不是当前下一步；⚠ §2 与 §7 的**会话日志已搬进 `NOTES.md` §13**（HANDOVER 只留状态与唯一下一步） |
| 动任何代码 | `AGENTS.md` | §2 铁律 → §3 构建 → §4 验证五层 → §6 约定 |
| 改协议字段 / 报文 | `docs/PROTOCOL.md`（★ **先改它**） | `AGENTS.md` §2.2（字段判据）· §6.4（文案/结算纪律） |
| 改规则判定（役种/符/点/振听/流局…） | `server/src/main/java/mahjong/rules/` + `docs/日本麻将.md` | `AGENTS.md` §2.1 · §6.3 · `server/.../test/SelfTest.java` |
| 改客户端布局 / 绘制 | `client/src/ui/` | `AGENTS.md` §6.2（**不变量清单**）→ 必须跑 L2 并**看图** |
| 换素材 / 字体 / 音效 | `docs/THEME.md` | `AGENTS.md` §9 · `NOTES.md` §9 |
| 报障：「这症状先怀疑什么」 | `AGENTS.md` §7（高频） | `NOTES.md` §7（全量 ~70 条） |
| 跑一轮训练 / 改训练工作流 | `python/README.md` | `docs/TRAINING.md` §4（P0–P4 现状）· `NOTES.md` §6.5（口径与坑） |
| **阀门与夹具的工作体系** | **`docs/VALVES-AND-FIXTURES.md`** | 开训前跑哪些自检 / 训练中只跑哪些阀门 / 各工作包 W1–W4 |
| 训练端 C++ 引擎 | `docs/TRAINER-CPP.md` | `trainer/README.md` · `AGENTS.md` §6.5 末（生产者可切） |
| **做 v4 特征 / 网络（下一步）** | **`docs/FEATURES-V4.md`**（规范） | **`docs/TRAINING-V4.md`**（设计：架构/多头/评价/阶段） |
| 打包 / 换机器人 AI | `docs/BOT-AI.md` | `AGENTS.md` §6.6 · `python -m mahjong_ml.packbot` |
| 发一版 release | `NOTES.md` §9.5 | `tools/package-release.ps1` · `docs/DEPLOY.md` |
| 牌谱导出 / 喂复盘器 | `docs/input-json.md` | `NOTES.md` §9.6 · `tools/tenhou-log-check.mjs` |
| 文档怎么维护 | `AGENTS.md` §8 | `tools/doc-refs-check.mjs`（预算 + 章节号 + §引用 + 本索引） |
| 玩家怎么玩 | `README.md` | — |

## 1. 六个板块

### 板块 ① 规则与引擎（服务端 = 唯一权威方）

| 项 | 内容 |
| --- | --- |
| 定位 | 洗牌/配牌/摸切/鸣牌/和牌判定/役种·符·点/振听/流局/连庄/精算 —— **只在这里判**（`AGENTS.md` §2.1） |
| 入口 | `server/src/main/java/mahjong/{core,rules,game}/` · `docs/日本麻将.md`（规则原文，**权威依据**） |
| 文档 | `docs/DESIGN.md`（架构与规则取舍）· `docs/AUDIT.md`（审计条目 `S-nn`） |
| 判据锚点 | `AGENTS.md` §2.1（规则只在服务端）· §6.3（牌桌行为与资源上限）· §6.4⑤（王牌/岭上/杠） |
| 回归 | L1 `--selftest`（项数见 `AGENTS.md` §4）；`AGENTS.md` §4 L1 |
| 细节 | `NOTES.md` §6.4（并发/时序/探针）· §7（症状表） |

### 板块 ② 协议与两端契约

| 项 | 内容 |
| --- | --- |
| 定位 | `Qt 客户端 ──TCP/NDJSON──> Java 服务端`；一份契约两端实现 |
| 入口 | `docs/PROTOCOL.md`（★ **改协议先改它**）· `docs/input-json.md`（牌谱导出格式） |
| 判据锚点 | `AGENTS.md` §2.2（逐字段一句话判据）· §6.4①（只发 ASCII 码）· §6.4②（役满写倍数） |
| 身份/托管/投票 | `AGENTS.md` §6.8 · §6.9（协议 §2.0 / §2.5 / §3.13） |
| 回归 | L3 真 socket 全家桶（`AGENTS.md` §4 L3）· `node tools/check-i18n.mjs` / `i18n-gen --check` |
| 细节 | `NOTES.md` §6.4.1（逐字段为什么）· §6.4.2（局间时序三条） |

### 板块 ③ 客户端（Qt6 Widgets / 素材 / 音效）

| 项 | 内容 |
| --- | --- |
| 定位 | 只做绘制/收操作/反馈/收发，**不做任何规则判定** |
| 入口 | `client/src/`（`ui/` `model/` `net/`）· `client/README.md`（构建与链接方式） |
| 文档 | `docs/THEME.md`（材质包/设置文件）· `docs/THIRD-PARTY.md`（LGPLv3 分发义务） |
| 判据锚点 | `AGENTS.md` §6.2（**布局不变量**：固定左缘/区带/副露几何/牌河行数…）· §6.3（三个自动开关）· §9（素材/字体/音效/打包） |
| 回归 | L2 `--selftest`（**必须看图**；⚠ 项数只在 `AGENTS.md` §4 记一处，本索引不重复数字）· L4 `mock-server` 定点截图 · L5 `--autoplay` |
| 细节 | `NOTES.md` §6.2（几何推导）· §6.3 · §9（素材管线） |

### 板块 ④ 训练（v3 现状 → v4 目标）

| 项 | 内容 |
| --- | --- |
| 定位 | 外面训练 → 导出权重 → 服务端**纯 Java 手写前向**；训练侧只实现 `ActionPolicy`（拿不到 `Round`） |
| **v4 框架（已落地，待 obs v3）** | `python/mahjong_ml/v4/`：`spec` 块注册表 · `blocks` 四张量拼装 · `cache` 两级缓存 · `model` 多头 · `harness` 封闭红证 + 均衡审计；命令 `python -m mahjong_ml.v4 check\|plan\|balance` |
| 现状（v3 谱系） | `docs/TRAINING.md`（P0–P4/P5b 与全部实测）· `python/README.md`（跑法）· `NOTES.md` §6.5（口径/坑/红证） |
| **下一步（v4）** | **`docs/FEATURES-V4.md`**（规范：信息边界/张量维度/版本契约/消融/验收）· **`docs/TRAINING-V4.md`**（设计：架构/多头/评价/阶段 P0–P5） |
| 训练端引擎（C++） | `docs/TRAINER-CPP.md`（与 Java 逐字节同源）· `trainer/README.md` |
| 机器人 AI 包 | `docs/BOT-AI.md`（包格式/清单/α 口径）· `AGENTS.md` §6.6 |
| 判据锚点 | `AGENTS.md` §6.5（红线：唯一漏斗/可复现/观测白名单/`is_student` 掩码…）· §6.6（teacher 五层取舍） |
| 回归 | `tools/selfplay-check.mjs` · `tools/trainer-*-parity.mjs` · `python/selfcheck.py`（项数见 `AGENTS.md` §4） |
| 数据与配额 | `NOTES.md` §6.5（时间预算/缓存档/配额回收 `S:\mahjong-training`） |

### 板块 ⑤ 运维与发布

| 项 | 内容 |
| --- | --- |
| 部署 | `docs/DEPLOY.md`（Ubuntu / JDK 17+ / 五个运维脚本） |
| 打包 | `tools/package-release.ps1`（客户端 zip + 服务端 zip，`.sh` 保留可执行位）· `tools/make-zip.mjs` |
| 发布流程 | `NOTES.md` §9.5（tag 先建、资产走 `uploads.github.com`、原地重发的坑） |
| 许可义务 | `docs/THIRD-PARTY.md`（Qt LGPLv3 动态链接：**不要删 licenses/、不要给 DLL 加校验**） |
| 运行时数据 | `replays/` · `players/` · `bot-ai/`（**都不进仓库**，启动时自动挂载 `bot-ai/`） |

### 板块 ⑥ 验证与文档纪律

| 项 | 内容 |
| --- | --- |
| 验证五层 | `AGENTS.md` §4（L1 规则引擎 → L2 客户端 → L3 协议 e2e → L4 GUI 定点 → L5 真机联调）· 注解 `NOTES.md` §4 |
| 训练/对拍 | `tools/trainer-*-parity.mjs` · `tools/selfplay-check.mjs` · `python/selfcheck.py` |
| 文档纪律 | `AGENTS.md` §8（判据/命令/不变量 ↔ 案例/注解/推导）· 本索引 |
| 文档自检 | `node tools/doc-refs-check.mjs`：AGENTS **字节预算**（⚠ 实测截断点 65,244 B）· 章节号完整 · 全仓 `§引用`可解 · **本索引完整性**；另两条同类：`tools/handover-facts.mjs --check`（HANDOVER §1 的 git 事实）、`tools/round-index.mjs --check`（NOTES §6.5 轮次索引表）；**训练侧头表**：`tools/head-tables-check.mjs`（三端 + 文档同名同值，规格见 `docs/TRAINER-CPP.md` §6.25） |
| 人工手册 | `AGENTS.md`（每次会话自动加载）· `NOTES.md`（细节分册，章节号与 AGENTS 一一对应） |

## 2. 按角色查

| 角色 | 建议阅读顺序 |
| --- | --- |
| **AI agent（本仓库干活）** | `AGENTS.md` §0 → §2 铁律 → §3/§4（构建与验证）→ 本索引 §0 按任务跳 |
| **新同事（改引擎）** | `README.md` → `docs/DESIGN.md` → `docs/日本麻将.md`（相关章节）→ `docs/PROTOCOL.md` §1–§5 → `AGENTS.md` §2/§6.3 |
| **新同事（改客户端）** | `client/README.md` → `AGENTS.md` §6.2 + §9 → `docs/THEME.md` → L2/L4 自检 |
| **训练侧** | `python/README.md` → `docs/TRAINING.md` §0–§4 → `NOTES.md` §6.5 → **v4：`docs/FEATURES-V4.md` + `docs/TRAINING-V4.md`** |
| **运维 / 发布** | `docs/DEPLOY.md` → `NOTES.md` §9.5 → `docs/THIRD-PARTY.md` |
| **玩家** | `README.md`（**不含技术细节**） |

## 3. 目录树（按板块标注）

```
mahjong/
├─ docs/          ← 契约、方案、素材与运维文档（下表逐份说明）
├─ server/        板块① 规则与引擎：Java 21，零第三方依赖
│                  util(Json/Log) · core(Tiles Meld Rules Wall) · rules(Shanten Agari Evaluator
│                  Payments Visible HandEval Danger) · game(Round Table + WinCheck/RoundOptions/
│                  RoundClaims/RoundScoring 纯判据) · replay(Replay Store Recorder) ·
│                  player(PlayerStore) · net(Server Session) · bot(Bot=teacher) ·
│                  ai(训练接口：PolicyFactory/Observation/Action/BotAis) · train(SelfPlay/TraceRecorder) ·
│                  test(SelfTest ★ 改规则在这里加断言) + pack/(五个运维脚本)
├─ client/        板块③ 客户端：build.ps1 + assets/{tiles,i18n,fonts,sfx} + src/{ui,model,net,i18n}
├─ python/        板块④ 训练侧（Python）：mahjong_ml/(features dataset bc dagger ppo offline_rl awr
│                  online budget paths league eval producer packbot) + selfcheck.py + verify_env.py
├─ trainer/       板块④ 训练端 C++ 自对弈引擎（与 Java 逐字节同源；build/ 不进仓库）
├─ tools/         板块⑥ 验证与打包：*-test.mjs（真 socket）· i18n-*（文案三件套）· mock-server（L4）·
│                  trainer-*-parity.mjs（对拍）· selfplay-check.mjs · tenhou-log-check.mjs ·
│                  doc-refs-check.mjs / handover-facts.mjs / round-index.mjs / head-tables-check.mjs（文档与头表自检）· package-release.ps1 + make-zip.mjs
└─ 运行时数据（都不进仓库，见 .gitignore）：replays/ · players/ · bot-ai/（与 jar/start.sh 同层）
```

## 4. 文档清单（谁该读、什么时候更新）

| 文件 | 定位 | 什么时候必须更新 |
| --- | --- | --- |
| `AGENTS.md` | **手册**：铁律/命令/不变量/速查（每次会话自动加载，**实测截断点 65,244 B** —— 见 `tools/doc-refs-check.mjs` 的 `BUDGET`） | 新坑、新命令、新不变量；**加内容前先想能不能进 NOTES** |
| `NOTES.md` | **细节分册**：案例/红证/推导/全量症状/已知限制（章节号与 AGENTS 一一对应） | 判据背后的**理由与数据**；新坑的详情 |
| `README.md` | **面向玩家**：操作、规则取舍、常见问题（**不含技术细节**） | 玩家看得见的行为变了 |
| `AGENTS.md`→`docs/` | 技术文档区（下列各份） | 见各行 |
| `docs/PROTOCOL.md` | **两端唯一契约**（报文/字段/CLI/训练接口 §8） | **改协议先改它** |
| `docs/日本麻将.md` | 规则原文（逐节标注 雀魂/天凤/M.League 差异） | 规则取舍新增项 |
| `docs/DESIGN.md` | 架构与规则取舍（含 AI 取舍、回放设计） | 架构/取舍变化 |
| `docs/AUDIT.md` | 审计与修复清单（条目 `S-nn`） | 每轮审计后 |
| `docs/TRAINING.md` | 训练方案 **v3 现状**（P0–P4/P5b，含全部实测） | 训练工作流或判据变化 |
| `docs/FEATURES-V4.md` | **特征 v4 规范**（信息边界/维度/版本/消融/验收） | 张量布局或信息边界变化（**五处一起改**） |
| `docs/TRAINING-V4.md` | **v4 设计**（架构/多头/评价/阶段 P0–P5/风险） | 架构或评价体系变化 |
| `docs/HANDOVER.md` | **会话交接**：状态（§1，含**唯一下一步**那一行）/ 历史规划快照（§3）/ 环境命令 / 操作纪律 / 待决问题；⚠ **§2 与 §7 的会话日志已整段搬进 `NOTES.md` §13**（原文一字未改），HANDOVER 只留**状态 + 唯一下一步**（**每次交接整段重写**） | 每次会话收尾 |
| `docs/TRAINER-CPP.md` | 训练端 C++ 引擎：设计、口径、全部实测 | 训练端引擎变化 |
| `docs/BOT-AI.md` | 机器人 AI **包格式**与随发布清单 | 包格式/清单/发布口径变化 |
| `docs/VALVES-AND-FIXTURES.md` | **阀门与夹具的工作体系**：开训前跑哪些自检、训练中只跑哪些阀门、工作包怎么切 | 自检清单或工作包划分变化 |
| `docs/input-json.md` | 牌谱导出 JSON 格式（喂复盘器） | 导出格式变化 |
| `docs/THEME.md` | 材质包与设置文件格式 | 素材/设置格式变化 |
| `docs/DEPLOY.md` | Ubuntu 部署 | 部署方式变化 |
| `docs/THIRD-PARTY.md` | 第三方许可与分发义务 | 新增 Qt 模块/第三方库 |
| `docs/EXPERT-PLAN.md` | **多风格路线实验的完整记录 + 下一步规划 + 玩家体验维度**（§0–§10 计划原文 / §11 三线终点结论 / §12 架构与算力效率规划 / §13 一页简报 / §14 肯定性审计与翻盘条件 / **§15 玩家体验评价体系** / **§16 风格候选与体验画像**） | 多风格路线推进、判据口径变化或做体验评价时 |
| `client/README.md` | 客户端构建与链接（Qt 自动获取） | 构建方式变化 |
| `python/README.md` | 训练侧跑法（环境/命令/复现） | 训练 CLI 变化 |
| `trainer/README.md` | 训练端引擎构建与对拍 | 引擎构建变化 |
| `tools/upstream-parse-check/README.md` | 上游解析器探针（导出格式验收） | 探针用法变化 |

> ⚠ 本表**不记体量**：体积是**必然腐坏**的元数据（改一次内容就过期），检查器也不该为它增加维护面。
> 这里只有两条可机械校验的判据 —— **路径存在** 与 **`docs/` 下每份 `.md` 都被登记**（见 §5）。

## 5. 本索引的自动校验

`tools/doc-refs-check.mjs` 对本文件做两条检查（负向对照：删掉任一条目/路径，必须红）：

1. **列出的路径都真的存在**（`docs/*.md` 与各 README）；
2. **`docs/` 下每份 `.md` 都被本文件列出** —— 加文档忘了登记会被抓出来。

⚠ 本文件**只做导航**：任何**判据**写进 `AGENTS.md`，任何**案例/数据**写进 `NOTES.md`；
在这里复述判据 = 迟早跟正文不一致（那时两处都错）。
