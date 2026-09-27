# 会话交接（HANDOVER）—— 2026-09-27

> **这份文件是"会话状态转移"**：新会话/新同事从它接上，不必重读整段对话。
> 它**只记状态与下一步**（判据与规范在 `AGENTS.md` / `docs/TRAINING-V4.md` / `docs/FEATURES-V4.md`），
> 每次交接**整段重写**（不要追加成历史堆）。索引：`docs/INDEX.md`。

---

## 1. 十秒速览

| 项 | 值 |
| --- | --- |
| 分支 / HEAD | `Training` · **本文件所在提交**（`git log -1 --oneline` 取真值）；其父提交 `8b069fa`（obs v3 + v4 两轮预训练），再往前 `06d9ebd`（本文件首次落地）|
| 远端 | `Tetrachlorosilane/sMahjong@Training` 头 = **`daacdc68`**（P5 的第二个提交；其 tree `5b56699a…` **== 本地 HEAD 的 tree**）⇒ **已同步**（§2.12）。⚠ 走 REST 推送时**本地与远端的提交 sha 本来就不同**，判据只有 tree；⚠⚠ **二进制文件（`forward-v4.bin`）进不了 `github_commit_files`**（它只收文本），要走 `POST /git/blobs`（base64）→ `POST /git/trees`（inline `sha`）→ `POST /git/commits` → `PATCH /git/refs`，见 §2.12 |
| 发布 | **v1.13.0 已上线**（release **#397762262** → <https://github.com/Tetrachlorosilane/sMahjong/releases/tag/v1.13.0>，tag `v1.13.0` → 远端 `4d7cb9ec`，6 个资产）：**第一次随包发布 v4 的 3 代网络**（`v4-bc-004` / `v4-p3-001` / `v4-ppo-001`，格式 2 权重）+ 逐决策 reward-to-go 契约。上一版 v1.12.0（#397670321）只带 client + server（那时 v4 还不能导出）。逐资产 sha256 / asset id 见 `NOTES.md` §9.5 |
| 数据盘 | `S:\mahjong-training` ≈ **17 GB**（`compact` 又多了 `v4-bc-003` 5.7 GB）；S 盘可用 ≈ **212 GB**。数据集：`raw\v4-bc-002` 1.19 GB → `compact\v4-bc-003`（**修好 `cand` 派生段后重建**，262,095 训练 / 14,965 验证）；checkpoint `ckpt\v4-bc-004`（教师一致率 **0.903**）与 `v4-bc-003`（0.165，阶段 c 塌掉的那份，留作对照） |
| 训练进度 | **v3 谱系已到平台**；v4：teacher 预训练 0.608 → 0.619 → **0.903**（§2.12）；P3 开局 RWR（§2.14，−1.96 / −3.08 证不出）；**PPO 一轮**（§2.15：2,000 场 2+2 **−3.54 [−5.84,−1.24]** ⇒ 略低于 teacher）；**第六轮**（§2.16）修掉两处 NaN + 降到 `#0.5` + 逐决策 `rtg` 优势 ⇒ **−1.06 [−3.34,+1.29] / vs 起点 +0.39 [−1.93,+2.72]** ⇒ **退步消掉、但只是打平** |
| v4 状态 | **P0 收口** + **P5 前向三端落地** + **§7.5 审计已做** + **PPO 回路已落地且数值上稳了**（§2.15/§2.16，四道闸门）。**下一件要紧事**：① 放量（2+2 下检出 Δ=2.0 要 ~5,400 场；v4 前向 40 ms/决策让这件事很贵 ⇒ 与 ② 绑在一起）；② 前向性能（40 ms → 1.5 ms，增量缓存） |
| 并行工作 | ⚠ **有另一个会话在同一仓库工作**（见 §5 第 1 条：推送要串行 + 比 tree sha）；本次推送前其改动已在基线里（§2.11） |

---

## 2. 本会话已完成（可判据的）

### 2.1 发布 v1.11.0（已上线）

- tag `v1.11.0` → `cff0def`，release **#397557867**，8 个资产（digest 抽查一致）。
- 随附 3 个机器人包：`ppo-v3-g01-g05`（阶梯 θ 最高、唯一 2+2 排除 0 的一代）· `ppo-v3-g01-g06` ·
  `ppo-v3-g02-g03`；一律 α=0 纯网络。
- 本版 `client/`、`server/` **源码没变**（改动全在 `python/` 与文档），玩家可见增量只有包。
- 记录：`release/RELEASE-v1.11.0.md`（本地，gitignore）+ `NOTES.md` §9.5。

### 2.2 文档重整 + 索引化

- 新增 **`docs/INDEX.md`**（六个板块 · 快速索引 · 目录树 · 文档清单）；`AGENTS.md` 从 64,166 B（已超提醒线）
  压到 **61,345 B**（提醒线 61,440 / 硬上限 65,536 —— **余量只剩 95 B：再加内容必须先往 NOTES 搬，或先瘦身**）。
- `NOTES.md` 目录按板块分组；18 份文档加"板块头"。
- `tools/doc-refs-check.mjs` 新增 **④ 索引完整性**（负向对照实测过：漏登记/坏路径都会红）。

### 2.3 S 盘全清（为 v4 腾空，**不可逆**）

- 删 **96 项 / 119 GB**：v2 代全部权重与轨迹 + **全部 v3 数据集**（compact/raw）+ `*-src` + v2 台账。
- 保留：`compact/bc-v3-001`（**不可重建**，`paths.KEEP_NAMES`）· `ckpt/` 13 个 v3 权重
  （`bc-v3-001` · `iql-v3-001` · `ppo-v3-g01-g01…g08` · `ppo-v3-g02-g01…g03`，**v1.11.0 三个包的源权重**）·
  `league/{ppo-v3-g01,ppo-v3-g02}`（阶梯/2+2/筛选/release 配对的 summary）。
- 代价：**v3 谱系不能再续训/重训**（权重只能打对局或当 v4 的基准对手）；文档里 `ckpt/awr-002`、
  `compact/ppo-v3-g01-g08` 之类路径已是历史记录。记录在 `NOTES.md` §6.5「第三次清理」。

### 2.4 v4 框架落地（训练准备 P0，7/12 绿）

新增 `python/mahjong_ml/v4/`（**复用** `budget`/`paths`/`producer`/`eval`/`league`，一行没改）：

| 模块 | 内容 |
| --- | --- |
| `spec.py` | 块注册表（id/宽度/依赖 obs 字段/版本）+ **块清单指纹 `e1f5dd0fc1e9aba8`**；断言块宽度铺满四张量 |
| `blocks.py` | `tile[34,48] / evt[60,96] / ctx[64] / cand[n,128]` 拼装 + 消融开关 + obs v2 逐块记名降级 |
| `cache.py` | L1 牌河增量 · L2 `EventStream`（GRU 增量，`recompute()` 为基准路径） |
| `model.py` | 三塔 + 关键张注意力 + 状态⇄增量两轮双向 + 多头（1,360,168 参数 ≤3M） |
| `harness.py` | 封闭红证（标签不进输入 / 奖励不含隐藏量 / aux 分离）+ 场外均衡审计（`balance.json`，1% / CV 5%） |
| `cli.py` | `python -m mahjong_ml.v4 {check\|plan\|spec\|fingerprint\|balance}` |

判据：`v4 check` PASS · `python/selfcheck.py` **365 项 / 0 失败**（原 313 + 49 + obs v3 的 3 条）· `doc-refs-check` PASS。
过程中判据抓出并修掉 **2 个真 bug**：① GRU 增量把头 56 个 padding token 也喂进 GRU（Δ=1.76e-2，改后 0）；
② HL-Gauss 软标签把长度=bins 的索引向量喂 `scatter_`（行和远大于 1）。另修好契约文档 4 处陈旧数字
（`state 617 = 544 + 73` → **615 = 544 + 71**）。

### 2.5 obs v3 第 1 轮：Java 权威实现 + 契约（2026-09-27）

清单第 **1~3** 步 ✅（下一步见 §3）：

- **`game/Round.java`**：`List<Event> events` 有序公开事件日志（只追加、整局累积），四类各一个漏斗
  （`recordDiscard()` / `sendMeld()`（吃碰杠的唯一出口）/ `doRiichi()` / `sendDora()`）；
  `int[] riichiTurn`（**按 `playerDraws` 计** —— 与 `events[].turn` 同一把尺子）。
  `draw` / `agari` / `ryuukyoku` **刻意不发**（理由写进 `PROTOCOL.md` §8.2）。
- **`ai/Observation.java`**：`VERSION 2→3`；`events[]`（类型 + 牌码 + 逐张属性）+ `riichi_turn[]`。
  隐藏量红证扩到 **牌山摸牌顺序 + 里宝指示牌**（新增两个只动隐藏量的自测钩子）。
- **契约**：`PROTOCOL.md` §8.2 加了事件字段表与三条语义判据；`tools/selfplay-check.mjs` 的 `OBS_KEYS`
  **按 `obs.v` 分档**（v2 老轨迹照样 PASS），并**独立重建牌河**、对 `riichi_turn` ↔ riichi 事件对账。
- **判据**：L1 `--selftest` **1388 项 / 0 失败**（+18 条：《事件流 ↔ 广播报文逐条同源》《牌河可由事件流重建》
  《同小局只追加》《`riichi_turn` 与事件一致》+ 五类事件覆盖闸门）；`node tools\selfplay-check.mjs`
  对新采 2 场轨迹 **DATASET PASS**（4 条注入式负向对照各自变红）；`python\selfcheck.py` **365/0**
  （顺手修掉 `v4/cache.py` 只认 `meld` 不认 `kan` 的 `meld_count` 漏账 —— 它会静默破坏判据④）。
- **代价（实测）**：`events` 按小局累积 ⇒ 轨迹 JSONL **×2.4 ~ ×2.8**（1,608 → 3,798 B/决策；
  可直接对照的一对：`1 0 pass 20260101` 的 `g0.jsonl` **1,119,860 B → 3,134,525 B**，见 `NOTES.md` §6.5）。
  想压回去就要把 `events` 改成"只发增量"（消费侧攒），**下一轮想清楚再说**（见 §6 待决问题）。

### 2.6 obs v3 第 2 轮：C++ 训练端镜像 + 三套 parity（硬闸门，2026-09-27）

清单第 **5 步的 obs 那一半** ✅（sidecar 那一半留到第 3 轮）：

- **新增 `trainer/src/event.hpp`**：`Event`（Java `Round.Event` 的镜像）+ `eventsJson()`（键序与
  "没有的字段整个键都不出现"逐条照抄）。⚠ 单独一个头是因为 `Observation` 也要持有它、而
  `round.hpp` 已 include `observation.hpp`（嵌在 `Round` 里会成环）。
- **`round.{hpp,cpp}`**：`events` + `riichiTurn`；四类事件各一个漏斗 —— `recordDiscard()`（+ `tsumogiri`）、
  **新增 `placeMeld()`**（吃/碰/大明杠/暗杠 + 加杠替换共用一个出口）、`doRiichi()`、`revealKanDora(seat)`；
  `meldKindWire` 挪进 `meld.hpp` 共用。**`observation.hpp`**：`2 → 3`；`events`/`riichiTurn` 插在
  `visible` 与 `haitei` 之间；`makeObservation()` 构造期快照。
- **判据（本机实测）**：`trainer-selfplay-parity.mjs` **200 场完整半庄**（`teacher,first,random,pass
  --rotate --cpp-workers 8`，2,613 小局 / 206,830 决策）**逐字节 200/200**；分层 8 例 8/8；
  `trainer-opts-parity.mjs` 2,075 行一致；`trainer-features-parity.mjs` sidecar 逐字节；
  **C++ 产出直接过 `node tools\selfplay-check.mjs`（DATASET PASS，含事件结构 + 独立重建牌河）**；
  `trainer --selftest` PASS；Java 侧 L1 仍 **1388/0**（无回退）。
- **覆盖闸门**：五种副露（chi/pon/ankan/kakan/**daiminkan**）与五类事件在逐字节对拍里都真的出现过 ——
  `first`/`pass` 从不杠，所以专门跑了 teacher 12 场（daiminkan 50）与 random 12 场（daiminkan 282）。
- **整条链（`trainer-takeover-check.mjs 4 1 teacher 20260101`）PASS**：`MAHJONG_PRODUCER=cpp` 的采集 +
  派生（都与 Java 逐字节）+ `selfplay-check` + **`dataset build` 出紧凑集**（state 615 / cand 96）
  ⇒ obs v3 轨迹**不改一行 Python 就能喂进现有 v3 管线**；`trainer-workers-check.mjs` 60/60（1 vs 8 核）。
- 细节与完整数字：`docs/TRAINER-CPP.md` §6.20。

### 2.7 obs v3 第 3 轮：sidecar v3（逐家四段）+ 数据集层硬闸门（P0 收口，2026-09-27）

清单第 **4/5b/6 步**全部 ✅ —— **P0 六步到此收口**：

- **Java**：`ai/ObsFeatures.java` `FEATURE_VERSION 2 → 3` + `PerSeat`/`perSeat()`：
  `danger_per_seat[3][34]`（实际立直状态）/ `danger_riichi_per_seat[3][34]`（**假设该家已立直**）/
  `genbutsu_per_seat[3][5]` / `suji_per_seat[3][5]`（位图，**字节内 LSB 在前**；方位**相对**自己：
  0=下家/1=対面/2=上家）。`train/TraceFeatures.java` 写 **D~G 四段**（sidecar 从三段→七段）。
- **C++**：`danger.hpp` 的 `DangerReport` 补 `genbutsu`/`suji`/`wall`（原来"没人读"所以没搬 ——
  位图要读了；**同一份 `dangerOf` 出三个判据**，不在特征里重算）+ `obffeatures.perSeat()`
  + `features.cpp` 写 D~G。
- **Python**：`features.py` `DERIVED_VERSION 3` + 段长常量；`dataset.load_sidecar` 按七段算偏移并
  校验**总长度**；**新增 `mahjong_ml/v4/traces.py`** = v4 数据集层唯一入口（obs v2 / 老 sidecar **硬拒绝**
  + `sidecar_dict()` 把打包位图展开成 34 宽喂 `blocks.assemble`）。
- **判据（本机实测）**：L1 `--selftest` **1392/0**（+4 条：逐家段两通路一致 / 非全 0 / 位图互斥 /
  **换视角就挪格**）· `trainer-features-parity.mjs` **3/3 sidecar 逐字节** ·
  `trainer-selfplay-parity.mjs`（`DangerReport` 改动后再验）**PASS** ·
  `trainer-takeover-check.mjs` **PASS**（采集/派生逐字节 + `selfplay-check` + `dataset build` 出紧凑集）·
  `python selfcheck.py` **381/0** · `v4 check` **PASS**（新增「数据集层」组）· `v4 plan` **11/13 绿**。
- **顺手修掉一个真 bug**：`v4/blocks.py` 的 `sc.get(key) or []` 在 numpy 数组上抛
  `truth value of an array is ambiguous` —— 真实 sidecar 第一次接进来才炸（此前测试都传 `None`）。
- **体积（实测 1,951 条决策）**：sidecar **518.5 B/决策** ⇒ 一轮 6M 决策 ≈ **3.1 GB**
  （规范原估 ~340 B / 2.0 GB **少算了 D/E 两段 int8**，已按实测改）。
- 细节与完整数字：`docs/TRAINER-CPP.md` §6.21 · `NOTES.md` §6.5。

### 2.8 标签侧 `aux.npz`（P0 最后一块，2026-09-27）

- **Java**：`train/NpzWriter.java`（**零依赖 npz 写出**：JDK `ZipOutputStream` STORED + 固定时间戳
  ⇒ 同数据两次写出逐字节相同；`.npy` v1.0 头 64 字节对齐）+ `TraceRecorder` 收集 8 类标签
  （自家"这一手做完"的向听/听牌、三家对手**隐藏真值**手牌/听牌、放铳/和了/收支/**顺位**回填）
  + `--aux` 开关（`Main` / `SelfPlay.Config`）。
- **Python**：新增 `mahjong_ml/auxlabels.py`（读侧 + `check_dir` 四条硬拒 + `python -m mahjong_ml.auxlabels check <dir>`）；
  `dataset build --aux` 并成 6 个**独立标签列**（`aux_*`；`hand_delta`/`placement` 已能从轨迹派生，不重复落）。
- **C++**：`trainer selfplay --aux` **显式报错**（npz 写出未移植）—— `producer.py` 的 `CPP_MISSING`
  同步登记，要标签就用 `MAHJONG_PRODUCER=java`（与 `--teacher-label` 同一条纪律）。
- **判据（本机实测）**：L1 `--selftest` **1403/0**（+11：**开/不开 `--aux` 的 `g*.jsonl` 逐字节相同**、
  npz 成员/形状/合法 `.npy` 头、顺位 ∈1..4、三家暗牌 1..14、四类标签都出现过非零）·
  `python selfcheck.py` **398/0**（+16：读侧四条硬拒 + **开/不开 `--aux` 的紧凑集输入列逐字节相同** +
  缺标签报错 + cpp 生产者报错）· 端到端：`--selfplay --aux` → `aux check` PASS →
  `dataset build --aux` 出 6 列（实测 2 场 / 308 决策）。
- ⚠ 已知缺口（不是 bug）：**C++ 生产者不产 aux**（P1/P2 若要整条走 C++ 得补 npz 写出）；
  另外 `opp_dealin` 沿用服务端 `Result.winner/loser` 的**单一赢家**口径（多家荣和只记第一位，与轨迹的 `hand_winner` 同源）。

### 2.9 第一轮 teacher 预训练（P1/P2 雏形，2026-09-27）

新增两个模块（都在 `python/mahjong_ml/v4/`）：

- **`dataset.py`**：轨迹（obs + sidecar + `aux.npz`）→ **v4 四张量 + 监督标签**
  （`tile[34,48] / evt[60,96] / ctx[64] / cand[n,128]` float16 + `label`/`label_type`/`effect`/`value`/
  `placement`/`seat`/`game` + 6 个 `aux_*` 真值列），`meta.json` 带块指纹与版本；四条硬闸门
  （obs v3、`obs.legal == 行 legal`、`sidecar.nLegal == len(legal)`、`aux` 缺文件报错）。
- **`pretrain.py`**：**教师模仿为主 + aux 真值辅助头**（policy CE ← teacher 动作；
  value = HL-Gauss ← `final_scores − 起点`；placement CE；belief_hand/tenpai ← `aux` 真值；
  danger BCE 只算**被选中的那个候选**；effect MSE ← sidecar 逐候选派生量）。
  权重一律取 `model.loss_weights()`（注册表），可复现 = 固定洗牌种子。

**第一轮实测**（150 场 teacher / 98,103 训练 + 5,994 验证 / 6 epoch / batch 128 / **270 s** on RTX 5060）：

| 指标 | ep1 | ep6 |
| --- | --- | --- |
| **教师动作一致率（val top-1）** | 0.528 | **0.608**（首合法基线 0.168） |
| policy CE | 1.418 | 1.192 |
| discard 类 top-1 | 0.40 | **0.50**（n=4,525，平均 8.8 候选） |
| riichi / pass / ron / tsumo | 0.82 / 1.00 / 1.00 / 1.00 | 0.86 / 1.00 / 1.00 / 1.00 |
| pon / chi / kan（样本少） | 0.00 / 0.00 / 0.75 | 0.06 / 0.07 / 0.62 |
| value CE（辅助头） | 3.87 | **6.63** ⚠ 不降反升 |
| belief_hand / belief_tenpai / danger | 0.597 / 0.144 / 0.313 | 0.608 / 0.173 / 0.328 ⚠ 略升 |

- **结论**：策略头学到了（3.6× 基线），**辅助头这轮没收敛**（多任务权重冲突的典型样子，不是 bug）⇒
  P1 要处理：按头分层学习率 / 先冻主干只训头 / 或把 aux 头挪到第二阶段。
- 数据在 `S:\mahjong-training\raw\v4-bc-001`（396 MB）+ `compact\v4-bc-001`（v4 数据集）；
  checkpoint `ckpt\v4-bc-001\model.pt`（5.5 MB，**还不能上线**：要 P5 的 Java/C++ v4 前向）。
- 判据：`python/selfcheck.py` **410/0**（含"同种子两次训练 `history` 逐字段相同"）。
- ⚠ 采集 150 场只花 112 s，但 **v4 数据集构建是单进程**（≈380 决策/s，98k 决策 ≈ 4.5 min）；
  放量到 P3 的规模前要给它加并行（照 `dataset.py` 的文件分组子进程那套）。

### 2.10 第二轮：分阶段训练 + 掩码事件重建（2026-09-27）

第一轮的问题是**辅助头不收敛**（`value` CE 不降反升）。这一轮三件事一起做并**全部达标**：

- **数据 2.7×**：400 场 teacher（262,095 训练 / 14,965 验证）；**v4 数据集构建并行化**
  （8 进程 75 s，与串行**逐字节相同** —— `selfcheck` 钉着）。
- **分阶段训练**（`v4/pretrain.py`）：`a` 只训 policy+effect → `b` **冻主干只训头** → `c` 联合微调
  （余弦降 lr）；头 lr = 主干 ×3。
- **P1 掩码事件重建**：真实事件 token 置 0、重建类型（pretext 头只训练、不进推理）。
- **结果（10 epoch / 851 s）**：教师一致率 **0.608 → 0.619**（首合法基线 0.165）；
  **所有辅助头都收敛了**：`value 6.63 → 3.59`、`belief_hand 0.608 → 0.582`、
  `belief_tenpai 0.173 → 0.140`、`danger 0.328 → 0.297`、`effect 0.026 → 0.021`。
  `pon` 从 0.06 涨到 0.28。
- **两条要记住的**：① `b` 段（冻主干）是辅助头收敛的关键一步；② ⚠ **train loss 不能跨阶段比**
  （b 段把随机初始化头的损失算进总损失，train 反而涨），**只有 val 的逐头曲线可比**。
- ⚠ **pretext 太容易**：掩码类型重建一致率 **0.968**（chance 0.125）⇒ 对表示几乎没贡献；
  下一轮要么换更难的目标，要么砍掉、把权重让给 policy。
- checkpoint：`S:\mahjong-training\ckpt\v4-bc-002\model.pt`；数据 `compact\v4-bc-002`。
- 完整表与读数：`docs/TRAINING-V4.md`「第二轮：分阶段训练 + 掩码事件重建」。

### 2.11 提交与发布 v1.12.0（2026-09-27）

- **提交**：`8b069fa`（本地）—— obs v3 三端 + sidecar v3 + 标签侧 `--aux` + v4 训练回路两轮预训练；
  工作树 **0 改动**；改动清单 = **4 个提交 / 50 个文件**（37 改 + 13 增 + 0 删）。
- **推送（REST）**：远端头 `034dde58` 的 tree `4d3f50ed…` **== 本地 `d8ac9ae` 的 tree**
  ⇒ 远端内容就是那个本地提交，缺的是它之后的 4 个提交；`git diff --name-status d8ac9ae HEAD`
  一把算出 50 个文件，用 **`github_commit_files` 一次带过去** ⇒ 远端 `8446ea5b`，
  其 tree `6c746e4c…` **== 本地 `HEAD` 的 tree**（判据达成）。
  ⚠ `github_git_push` 本次**走不通**：git 通道（broker）对 github.com 返回 **502**，REST 正常 —— 两条通道互不相干。
- **发布**：tag `v1.12.0` → `8446ea5b` → release **#397670321**（2 个资产，digest 与本地 sha256 逐一核对一致）：
  `sMahjong-client-v1.12.0-win64.zip` 38,848,421 B `5b260841…`（asset 593034214）·
  `sMahjong-server-v1.12.0.zip` 368,820 B `3c6095e0…`（593034070）。
- **本版性质**：改动全在训练侧（服务端训练接口 + C++ 镜像 + Python v4 回路）与文档；
  客户端只有版本号 `1.11.0 → 1.12.0`（`-Deploy` 重建，exe 内 UTF-16 串实测 1.12.0），**玩家可见增量 = 无**。
- **不带机器人包**：v4 权重还不能导出（要等 P5 的 Java/C++ v4 前向）⇒ `bot-ai/` 继续用 v1.11.0 的三个；
  已**开包读权重头**确认同规格（`magic=MJNN ver=1 sd=615 cd=96`，服务端 `Features.STATE = 544 + 71 = 615`）。
- 发布前实测：L1 **1403/0** · L2 **854/0** · `selfcheck.py` **419/0** · `v4 check` PASS ·
  trainer 三套 parity PASS（200 场逐字节）· `doc-refs-check` PASS · 服务端 zip 5 个 `.sh` 均 `-rwxr-xr-x`、
  `VERSION` = 1.12.0。
- 顺手修 **`Features.java` / `NeuralPolicy.java` 注释里 3 处陈旧数字**（`617 = 544 + 73` / `// 607`
  → `615 = 544 + 71`）：`docs/` 前一轮已改对，**代码注释里的漏了**。发布资产**不重出**
  （tag 指向的提交就是打包时那份源码，注释与行号变了、类文件语义不变）。
- 本地草稿与摘要表：`release\RELEASE-v1.12.0.md`（`release\` 已 gitignore）。

### 2.12 P5：v4 前向三端落地 + 两个"静默"真 bug（2026-09-27）

**主线**（用户要求"编写 P5 的 Java/C++ v4 前向"）：

- **`net.bin` 格式 2**（`python/mahjong_ml/v4/export.py`）：`MJNN` + `format=2` + 块清单 + 张量表
  （74 个张量、名字升序、float32）；`weights` 与 `golden` 两个子命令；同权重两次导出**逐字节相同**。
- **Java**：`ai/V4Features.java`（obs → 四张量，实时算 `perSeat`/`perCandidate` 派生量）、
  `ai/V4Policy.java`（三塔 + 融合 + 七头手写前向）、`ai/NetWeights.java`（按 `format` 分派两代）、
  `ai/Logits.java` + `LogitPolicy`（v3/v4 共用"argmax/采样/先验"一份实现）；
  `SelfTest.v4ForwardTests` 用夹具逐元素钉住（L1 1403 → **1423/0**）。
- **C++**：`trainer/src/v4features.*` / `v4policy.*` + `v4net` / `v4golden` 子命令 +
  `policies.hpp` 按 `format` 分派（v3 路径一位不变）。
- **夹具与对拍**：`python/tests/golden/forward-v4.bin`（小网络 32/16/2/5，10 个用例，**带覆盖闸门**）；
  `tools/V4Probe.java` + `tools/trainer-v4-parity.mjs`
  （`--golden` / `--selfcheck` / 逐行对拍，退出码 0/1/2/3）。
- **判据（实测）**：Java golden `特征 maxΔ=5.96e-08 / 前向 maxΔ=5.03e-08 / argmax 10/10 / 红证 0`；
  C++ 同数字；**Java↔C++ 1,885 条决策逐字符相同、maxΔ=0**；Java 端到端 8 场自对弈 +
  `selfplay-check` DATASET PASS；`packbot` 能打 v4 包并在服务端挂载成功。
- ⚠ **性能没达标**：特征 ≈4.6 ms + 前向 ≈35–40 ms / 决策（单线程）≫ 预算 2 + 1.5 ms ⇒ **下一轮第一件事**。

**过程中抓到的两个真 bug**（详见 `NOTES.md` §6.5 第七轮）：

1. **`cand[88:128]` 40 列全 0**（`sidecar_dict` 没给逐候选 C 段 + `dataset` 不查 `Tensors.degraded`）
   ⇒ 修好后同数据同超参：教师一致率 **0.619 → 0.903**（`ckpt\v4-bc-004`）。旧数据集 `v4-bc-001/002` 作废。
2. **阶段 c 把策略头练塌**（top1 → 首合法基线 0.165）⇒ 新增 `--stage-c-lr-mult`（用 0.1）。

**顺带修的**（都是"对拍/夹具"抓出来的）：obs 里 `hand_red`/`riichi`/`ippatsu` 是**布尔数组**
（Java 曾把后两个读成 0，8 个通道恒 0 —— 旧夹具恰好没人立直所以没暴露）；`linear()` 的**别名安全**；
`gruStep` 必须返回新数组；C++ 的 `%.9g` 排版要按 Java 的 dtoa 口径（`javaG9`）。

**推送（本地 `ae065d8` / tree `5b56699a…`，37 个文件 = 24 改 + 13 增）**：
① **文本 36 个**走 `github_commit_files`（一次带过去，基线 `cf53c76d`）→ 远端 `7f6a4a67`；
② ⚠ **二进制（`python/tests/golden/forward-v4.bin`，573,336 B）它不收**（只收文本）⇒
   单独走四步：`POST /git/blobs`（`content` = base64、`encoding: base64`；**大 payload 用 `bodyFile`
   落盘再发**，别塞进工具参数）→ `POST /git/trees`（`base_tree` = 上一步的 tree + 该文件的
   `sha` 条目）→ `POST /git/commits`（parent = 上一步提交）→ `PATCH /git/refs/heads/Training`
   （`force: false`）⇒ 远端 `daacdc68`，其 tree `5b56699a…` **== 本地 HEAD 的 tree**（判据达成）。
   ⚠ 上传前用 `git hash-object <file>` 对一下 blob sha（本次 `deee0bcd…` 两侧一致）。

---

## 3. 下一步：**性能（增量缓存）→ 再 P1 收尾 → 放量**

> 优先级变了：P5 之前"checkpoint 只能离线评估"，现在**能上线了但太慢** —— 40 ms/决策的网
> 当 bot 可以、当自对弈采集主力不行。所以第一件事是把前向压回预算内。

**清单在 `docs/TRAINING-V4.md` §「P0 剩余工作」**（六步**全部 ✅**）+ **§9 P1 设计**。摘要：

| # | 端 | 文件 | 状态 |
| --- | --- | --- | --- |
| 1~3 | Java + 契约 | `Round` / `Observation` / `PROTOCOL` §8.2 · `selfplay-check` | ✅ |
| 4 | Java | `ai/ObsFeatures.java`（sidecar v3 四段） | ✅ |
| 5 | C++ | `event.hpp` / `round.*` / `observation.hpp` / `danger.hpp` / `obffeatures.*` / `features.cpp` | ✅ |
| 6 | Python | `v4/traces.py`（数据集层硬拒绝） | ✅ |
| 7 | Java+Python | 标签侧 `aux.npz`（§2.8）+ `v4 plan` 里原有的那一条 ⚠️ | ✅ |

**开工前要定的两条**（都在 §6 待决问题里）：
① `events` 的**累积 vs 增量**口径 —— 现在两侧都按"累积"实现了，改就要两侧一起改；
② 一轮的算力/磁盘预算 —— sidecar 实测 **518.5 B/决策**（6M 决策 ≈ 3.1 GB）+ 轨迹 ×2.4~2.8。

**P0 没有剩下的了**（`v4 plan` 12/13：唯一没绿的 ⛔ 是 P1/P2/P3 三个阶段本身）。
唯一的**已知能力缺口**：C++ 生产者不产 `aux.npz`（训练端 `--aux` 显式报错）——
第一轮的教师模仿不受影响（标签来自轨迹的 `chosen_index`），P2 若要整条走 C++ 得补 npz 写出。

**这一轮要做的事**（按优先级）：

① **放量 + 算法升级**（`#0.5` 与 `rtg` 已在 §2.16 落地并判过：两处修**消掉了退步但只到打平**）——
   **下一步不是再调一个旋钮**：按现有 `sd(Δ)≈53`，要证明"比 teacher 强 2 顺位点"需要 **~5,400 场 2+2**
   （一次评测 ~3 小时；v4 前向 40 ms/决策是瓶颈 ⇒ 与 ② 绑在一起）。可选方向：
   （a）**放量到 5,000+ 场**（只买精度，不买强度）；
   （b）**在线 PPO**（真正的多次迭代：每轮采→训→换行为策略，而不是"离线一次 PPO"）——
   在线那一套在 v3 上是唯一打出过 CI 排除 0 的（`ppo-v3-g01-g05` +2.66 [+0.14,+5.18]），
   v4 侧缺的就是"多轮"；
   （c）**P4 的对手池/联赛**（v4 目前只有 teacher 对练；v3 的自对抗续训虽无增益，但对手多样性没试过）；
② **性能：把 v4 前向压回预算**（P5 验收里唯一没达标的项，实测 4.6 + 40 ms/决策）——
   按性价比：**增量事件缓存**（把 `v4/cache.py` 的 `EventStream` 语义搬到 Java：GRU 隐状态跨决策复用，
   `recompute()` 仍是基准路径，判据仍是"增量 == 全量"）→ 稠密循环优化（扁平 `float[]` + 行偏移、
   去逐行方法调用）→ 必要时缩 `dModel`（那要重训）。
③ **更难的自监督**（掩码事件类型一致率 0.968 ≈ 白给，**已按结论关掉**）：
   若要再用，得换"连 `tile_kind` 一起重建"或"掩码整段窗口"，否则把权重让给 policy。
④ **放量**：P3 的 6M 决策规模下，数据集构建（现 8 进程 ≈3,500 决策/s）与磁盘（v4 数据集 ≈22 KB/决策）
   都要再核一遍预算；

**三条纪律（不许省）**：
① **两侧的 obs/sidecar 都到 v3 了**，切 `MAHJONG_PRODUCER=cpp` 只影响速度 ——
但切之前先把 §6 的第 1 条（`events` 累积 vs 增量）定了；
② **老轨迹/老 sidecar 一律硬拒绝**（obs v2 在 `v4/traces.iter_decisions` 报错、`derived_version != 3`
在 `dataset.load_sidecar` 报错）——重采 / 重跑 `--features` 即可；
③ 每步都跑对应层自检（L1 / `selfplay-check` / `trainer-*-parity` / `v4 check` / `selfcheck`），**不许只看编译过**。

**执行记录（每轮都收在"全绿"上）**：
- **第 1 轮 ✅**：Java 权威实现 + 契约（`SelfTest.obsEventStreamTests` + `selfplay-check` 独立重建牌河）。
- **第 2 轮 ✅**：C++ obs v3 镜像 + 三套 parity（200 场完整半庄逐字节 200/200）。
- **第 3 轮 ✅**：sidecar v3（Java+C++）+ Python 数据集层硬闸门。
- **第 4 轮 ✅**：标签侧 `aux.npz`（§2.8，P0 收口）。
- **第 5 轮 ✅**：第一轮 teacher 预训练（§2.9，`v4/dataset.py` + `v4/pretrain.py`）。
- **第 6 轮 ✅**：分阶段训练（辅助头收敛）+ 掩码事件重建 + 数据集并行化（§2.10）。
- **第 7 轮 ✅**：提交 + 发布 **v1.12.0**（§2.11：远端 tree == 本地 tree 为判据，2 个资产 digest 逐一核对）。
- **第 8 轮 ✅**：**P5 三端 v4 前向**（§2.12）+ 修掉两个静默 bug（`cand` 派生段全 0 / 阶段 c 塌）+ 教师一致率 0.903。
- **第 9 轮 ✅**（收在"**结论明确**"上，不是全绿）：§7.5 均衡审计首次执行（§2.14）+ P3 开局一轮 RWR ——
  两条 2+2 配对**都证不出差别**（这本身是结论：上一代已在 teacher 水平，200 场 RWR 推不动）。
- **第 10 轮 ✅**：P3 加强版 —— 1,000 场自对弈（689,693 决策）+ **PPO 落地**（§2.15）；
  学生行 CE 0.861 → 0.601、KL 0.069 → 0.021（全程在信任域内）；判决 +0.66 / +1.47（**方向翻正、仍证不出**）。
- **第 11 轮 ✅**：发布 **v1.13.0**（v4 的 3 代网络随包 + 逐决策 reward-to-go 三端落地，§2.16）；
  PPO 两处 NaN 修掉 + 四道闸门 + 重训 **`v4-ppo-002`** ⇒ **2,000 场 2+2：vs teacher −1.06 [−3.34,+1.29]
  （第五轮是 −3.54 [−5.84,−1.24]）** ⇒ **退步消掉、但仍只是打平**。

**下一轮的起点**：§3 的 ① **放量 + 算法升级**（`#0.5` 与 `rtg` 已就位；要打出差别得按 5,400 场的量级
或换更强的信号）→ ② **性能**（把前向压回 1.5 ms 预算，先做增量事件缓存）——
两者的关系是：**放量贵在前向慢**（v4 40 ms/决策 ⇒ 一次 2,000 场 2+2 评测要 ~1 小时、5,400 场要 ~3 小时）。

### 2.14 P3 开局一轮（自对弈 + RWR）+ §7.5 均衡审计（2026-09-27）

- **§7.5 均衡审计（判据⑩，首次执行）**：`raw/v4-sp-001`（C++ 400 场 / 292,724 决策）→
  **座位最大偏差 0.000%**、配席 net/teacher/random = 400/800/400 与 `--expect` 一致 ⇒
  **均衡 ✓**、`balance.json` 落盘。⚠ 首跑报了**假绿**（审计把决策行判成老形状 ⇒ 0 决策也算过），
  修法：两种形状都认 + 空数据/无配席判红 + 夹具改成真形状（一场一个文件）+ 单位改成**座位场**。
- **P3 开局**：数据 `raw/v4-sp-002`（Java + `--aux`，200 场 / 143,612 决策）→ `compact/v4-sp-002`
  （学生行 **26.1%**，新增 `is_student`/`delta` 两列）→ RWR 训练 10 epoch（470 s）→
  `ckpt/v4-p3-001`（学生行 top1 0.763 → 0.847；⚠ danger 头本轮被 RWR 误加权，下一轮起只吃掩码）。
- **结论（2+2 同牌山配对，n=800 / 每策略 1,600 席）**：vs teacher **Δ=−1.96 [−5.59,+1.69] p=0.358**；
  vs `v4-bc-004` **Δ=−3.08 [−6.71,+0.69] p=0.322** ⇒ **两条都证不出差别**（点估计略负）。
  ⇒ 这一轮**没有可测改进**（上一代已经在 teacher 水平）；要检出 Δ=2.0 需 **≈5,500 场**。
- **下一轮三处改动**：① 量级 1,000–2,000 场（Java + `--aux` 24 workers ≈ 2,600 s/千场；
  用 C++ 就得先把"aux 只覆盖部分行"的掩码做出来）；② 值头当 critic 的 **PPO**（或"只学赢的小局"的过滤臂）；
  ③ 评估预算 ≥2,000 场 + 预先注册 Δ。

### 2.15 P3 加强版：1,000 场自对弈 + PPO（2026-09-27）

- **采集**：`raw/v4-sp-003` = **1,000 场 / 689,693 决策**（Java + `--aux`，24 workers，1,602 + 273 s）；
  学生 2 席（`net:.../v4-bc-004/net.bin#1.0`）+ teacher 2 席；**§7.5 审计 0.000% / 配席 2,000/2,000 ✓**。
- **数据集**：`compact/v4-sp-003`（654,414 / 35,279，学生行 **50.0%**，lmax 29）。
- **PPO**（`--objective ppo`）：`log π_old` 用 `--behaviour` 现场重算（温度从 `student` 串的 `#T` 解析）、
  优势 `A=R−E[V]` **只在学生行归一化**、截断替代项只算学生行、`--init` 从上一代起步、`lr 1e-4`。
  4 epoch / 776 s：学生行 CE 0.861 → **0.601**、**KL 0.069 → 0.021**、截断比例 0.135 → 0.062 ✓。
- **判决**（3 路同场配对 n=1,200）：vs 上一代 **+0.66 [−3.52,+4.84] p=0.908**、vs teacher
  **+1.47 [−2.68,+5.68] p=0.583** ⇒ 方向比第四轮翻正，但仍证不出。
  ⚠ **预先注册的 2,000 场 2+2 主判据给出相反结论**：PPO vs teacher **Δ=−3.54，95%CI [−5.84, −1.24]**
  （均值自助法**排除 0**）、符号检验 p=0.283（975 胜/1024 负）⇒ **PPO 这一轮没有改进，更强的测量说它略微变差**
  （上一代与 teacher 持平：+0.81 [−3.07,+4.81]）。
- ⚠ **三条要记住的**：① **探索的代价**——采集期里 `#1.0` 的学生比 teacher 低 7.6 顺位点（贪心的上一代持平）
  ⇒ 下一轮把温度降到 `#0.5`；② **优势太粗**——`A = R_整场 − E[V]` 没有逐决策信用分配（要做 GAE 得先在
  引擎侧记 reward-to-go）；③ **离线指标向好 ≠ 变强**（学生行 CE 0.861→0.601、top1 0.845→0.886，强度 −3.54）；
  ④ 采样设计：四路各一席 `sd(Δ)=73–74` 是 2+2（≈53）的 ~2 倍 ⇒ 同样预算优先 2+2。

### 2.16 发布 v1.13.0 + 第六轮：逐决策 reward-to-go 三端落地（2026-09-28）

- **发布 v1.13.0**（release **#397762262**，6 个资产；逐资产 sha256 / asset id 见 `NOTES.md` §9.5）：
  **第一次带 v4 的机器人包**（`v4-bc-004` / `v4-p3-001` / `v4-ppo-001`，`net.bin` 格式 2）+ v3 的三个
  （v4 谱系目前还没有一代打出"比 teacher 强"的结论，所以老包继续随附）+ client/server。
  ⚠ **"包能挂上"与"包能打"分开验**：启动日志的清单、`bot-ai-test.mjs`（20/0）、
  以及**真打一场**（`--selfplay 1 --policy net:<包里的 net.bin>`，8 小局 / 483 决策 / 23.1 s）。
- **第六轮（契约 + 三端）**：轨迹决策行新增 **`reward_to_go`**（点）= `Σ_{本局及其后} delta + 终局余棒`
  ⇒ 离线 PPO 的 **λ=1 GAE 目标**（`A = R_tg − E[V(s)]`）现成可算，不再只有整场结果。
  - **Java** `TraceRecorder.rewardToGo`（后缀和 + 余棒，按 `hand_no` 显式对齐）+
    `SelfTest.rewardToGoTests`（6 项：守恒 / 独立重算的后缀和 / 真逐决策 / 末局口径）；
  - **C++** `trainer/src/trace.cpp` 同口径 ⇒ `trainer-selfplay-parity.mjs` 2 场 × 6 小局**逐字节一致**，
    且 C++ 轨迹过 `selfplay-check.mjs` 独立检查器 PASS；
  - **检查器** `tools/selfplay-check.mjs` 独立重算（红证实测：改一行 `reward_to_go` → 2 条报错、退出码 1）；
  - **Python**：数据集新增 `rtg` 列（千点；老轨迹写 **NaN**，不填 0）+ `--value-target final|rtg`
    （同时决定值头目标与优势的 `R`，两处必须同源）；`pretrain` 在 NaN 数据上硬拒。
  - 判据：L1 **1429/0**、`selfcheck` **488/0**（+20）、trainer `--selftest` PASS、`v4 check` PASS。
- ⚠⚠ **第一次用 `#0.5` 跑 PPO 撞上两个真 bug（本轮最值钱的产出）**：
  **① 温度只作用一侧**：`logp_new` 忘了用同一把温度尺子 ⇒ `ρ = exp(65)`、策略损失 2.5e11、
  **一个 step 整网 NaN**，而训练**照跑完 4 个 epoch、还落盘了一份废 checkpoint**（只有 `val top1` =
  首合法基线 0.156 是线索）。⚠ 这个 bug 在 `#1.0` 采集下**完全不可见**（`logits/1.0 == logits`）。
  **② 非学生行的 `inf × 0`**（修完①之后第 757 步又 NaN，指纹完全不同：`parts["policy"]=nan` 而
  `kl/clip/gap` 全有限、`|θ|max` 不动）：teacher/随机 的动作在学生网下是零概率 ⇒ `ρ` 溢出成 inf，
  而优势在那些行上的定义值正是 0 ⇒ `inf × 0 = NaN`。
  **修 + 四道闸门**：`_policy_logits(out, T)`（两处同尺）· `LOG_RATIO_CLAMP = ±4` + 未选中行显式置 0 ·
  初始化 `KL(π_old‖π_new) > 1e-2` ⇒ 退出（口径闸门）· loss 非有限 ⇒ `SystemExit`（绝不落盘废 checkpoint）；
  另加 `logp_gap` 诊断与"`--behaviour-temp` 必须与 meta `#T` 一致"的闸门。
  详情与定位手法见 `NOTES.md` §6.5 第十轮、判据见 `AGENTS.md` §6.5 / `docs/TRAINING-V4.md`「第六轮」。
- **PPO 重训成功（`v4-ppo-002`）**：4 epoch / 685 s，`train 2.4412 → 2.3314`、
  `val top1(教师一致) 0.867 → 0.879`、**学生行 top1 0.888 → 0.900**、学生行 CE 0.653 → 0.583、
  **KL 0.0657 → 0.0105**、截断 0.171 → 0.059、`logp_gap` 2.60 → 1.04（全程有限、稳在信任域内）。
- **判决（预先注册，各 2,000 场 2+2 同牌山配对；`raw/eval-ppo2-vs-teacher` / `-vs-p3`）**：

  | 对比 | Δ（顺位点） | 95% CI | p（符号检验） | 胜/负/平 | sd(Δ) |
  | --- | --- | --- | --- | --- | --- |
  | `v4-ppo-002` vs teacher（**主判据**） | **−1.06** | **[−3.34, +1.29]** | 0.395 | 978/1017/5 | 52.91 |
  | `v4-ppo-002` vs `v4-p3-001`（起点） | **+0.39** | **[−1.93, +2.72]** | 0.771 | 993/1007/0 | 52.38 |
  | （参照）`v4-ppo-001` vs teacher（第五轮同设计） | −3.54 | [−5.84, −1.24] | 0.283 | 975/1024/1 | 52.75 |

  ⇒ **两处修把第五轮那个"可测的退步"消掉了**（CI 从排除 0 → 跨 0），与起点也证不出差别；
  ⚠ **但点估计都没转正：这一轮没打出"比 teacher 强"，只是回到"打平"**
  （v4 三代 bc-004 / p3-001 / ppo-002 全在 teacher 水平；检出 Δ=2.0 要 ≈5,400 场）。
  ⚠ **本轮不发新版机器人包**：`v4-ppo-002` 与已发布的三个包**证不出差别**，没有换包的理由
  （要换也是等某一代 CI 排除 0）。

- **采集（`#0.5`）**：`raw/v4-sp-004` = 1,000 场 / 11,430 小局 / **693,187 决策**（1,470 s，472 决策/s）；
  学生 2 席（`net:tools\build\v4-p3-001\net.bin@0#0.5`）+ teacher 2 席；`selfplay-check` PASS、
  §7.5 审计 **0.000% / 500×4 席 ✓**；`compact/v4-sp-004` = 658,449 / 34,738，**rtg 覆盖 100%**。
  ⚠ **探索成本的直接对照**（同一把尺子：同批对局里的平均顺位）：
  `#1.0` 学生 −3.78 顺位点（avg_place 2.5915 vs teacher 2.4085）→ `#0.5` **−1.26**（2.520 vs 2.481）
  ⇒ 降温度按预期把探索代价压掉约 2/3。


---

## 4. 环境与命令备忘（本机实测）

```powershell
# Python 侧（⚠ 系统 `python` 没有 torch，必须用 venv）
cd C:\Users\HP\source\games\mahjong\python
.venv\Scripts\python.exe selfcheck.py              # ← 必须在 python\ 下跑（子进程按 cwd 找模块）
.venv\Scripts\python.exe -m mahjong_ml.v4 check    # v4 训练准备体检
.venv\Scripts\python.exe -m mahjong_ml.v4 plan     # P0 状态表（当前 12/13 绿）
.venv\Scripts\python.exe -m mahjong_ml.features    # 权威特征规格（v3: state 615 / cand 96）

# 服务端 / 客户端
pwsh -File server\build.ps1 ; java -jar server\build\mahjong-server.jar --selftest   # L1（当前 1429 项）
pwsh -File client\build.ps1 -Deploy                                                  # 发布前必做
node tools\doc-refs-check.mjs                                                        # 文档自检（含索引完整性）

# 轨迹（自对弈 → 校验；obs v3 起自检里会核事件流；--aux 另落标签侧 g*.aux.npz）
java -jar server\build\mahjong-server.jar --selfplay 2 --workers 1 --policy teacher,teacher,teacher,teacher --seed 7 --aux --out .tmp-trace
node tools\selfplay-check.mjs .tmp-trace                                             # DATASET PASS（v2/v3 都收，按 obs.v 分档）
cd python
.venv\Scripts\python.exe -m mahjong_ml.auxlabels check ..\.tmp-trace                       # 标签侧：版本/行数/obs 版本对齐
.venv\Scripts\python.exe -m mahjong_ml.dataset build ..\.tmp-trace ..\.tmp-compact --aux   # 6 个 aux_* 标签列

# v4 训练回路（P1/P2：轨迹 → 四张量 → teacher 预训练；§2.9）
.venv\Scripts\python.exe -m mahjong_ml.v4.dataset S:\mahjong-training\raw\v4-bc-001 S:\mahjong-training\compact\v4-bc-001 --aux
.venv\Scripts\python.exe -m mahjong_ml.v4.pretrain --data S:\mahjong-training\compact\v4-bc-001 --label v4-bc-001 --epochs 6

# P5：v4 权重导出（net.bin **格式 2**）+ 三端对拍 + 性能（§2.12）
.venv\Scripts\python.exe -m mahjong_ml.v4.export weights --ckpt S:\mahjong-training\ckpt\v4-bc-004\model.pt --out ..\tools\build\v4-bc-004\net.bin
.venv\Scripts\python.exe -m mahjong_ml.v4.export golden --trace S:\mahjong-training\raw\v4-bc-002 --out tests\golden\forward-v4.bin --cases 10   # 覆盖闸门要求 ippatsu 等标签
node tools\trainer-v4-parity.mjs --golden                              # Java 与 C++ **各自**与 Python 夹具比（特征+前向+红证）
node tools\trainer-v4-parity.mjs --selfcheck                           # 比较器负向对照（扰动必须 FAIL）
node tools\trainer-v4-parity.mjs tools\build\v4-bc-004\net.bin .tmp-v4fix   # Java↔C++ 逐行（实测 1,885 条 maxΔ=0）
java -cp "server/build/mahjong-server.jar;tools/build" tools.V4Probe --bench tools\build\v4-bc-004\net.bin .tmp-v4fix\g0.jsonl 200

# 第六轮：逐决策 reward-to-go（λ=1 的 GAE 目标；§2.16）—— 一轮 PPO 的完整回路
java -jar server\build\mahjong-server.jar --selfplay 1000 --workers 24 --aux --seed 20260928 --out S:\mahjong-training\raw\v4-sp-004 --policy "teacher,net:tools\build\v4-p3-001\net.bin@0#0.5,teacher,net:tools\build\v4-p3-001\net.bin@0#0.5"
node tools\selfplay-check.mjs S:\mahjong-training\raw\v4-sp-004       # 含 reward_to_go 的独立重算
.venv\Scripts\python.exe -m mahjong_ml.v4 balance --dir S:\mahjong-training\raw\v4-sp-004 --expect "teacher=2,net:...=2" --equal-shares
java -jar server\build\mahjong-server.jar --features S:\mahjong-training\raw\v4-sp-004 --workers 24.venv\Scripts\python.exe -m mahjong_ml.v4.dataset S:\mahjong-training\raw\v4-sp-004 S:\mahjong-training\compact\v4-sp-004 --aux --student "net:tools\build\v4-p3-001\net.bin@0#0.5"
.venv\Scripts\python.exe -m mahjong_ml.v4.pretrain --data S:\mahjong-training\compact\v4-sp-004 --label v4-ppo-002 --objective ppo --value-target rtg --behaviour ..\tools\build\v4-p3-001\net.bin --behaviour-temp 0.5 --init S:\mahjong-training\ckpt\v4-p3-001\model.pt --epochs 4

# 训练端 C++（改了 trainer/ 之后必跑；判据见 §2.6/§2.7 与 docs\TRAINER-CPP.md §6.20/§6.21）
pwsh -File trainer\build.ps1                                                          # 增量编译 + 编后自检
node tools\trainer-selfplay-parity.mjs 1 1 pass 20260101                               # 轨迹逐字节（秒级）
node tools\trainer-opts-parity.mjs 4 first,pass,random 2                               # 询问内容逐字符
node tools\trainer-features-parity.mjs <轨迹目录> 4                                    # sidecar 逐字节（sidecar v3 七段）
node tools\selfplay-check.mjs trainer\build\sp-<tag>-cpp                               # C++ 产出过独立校验器（语义腿）
# 完整半庄验收（≥200 场；约 10 分钟）：$env:SP_TAG='big' 可固定产物目录名
node tools\trainer-selfplay-parity.mjs 200 0 teacher,first,random,pass 20260101 --rotate --cpp-workers 8

# 训练（v4 采集在 C++ 到位前必须用 java 生产者）
$env:MAHJONG_PRODUCER='java'                       # 本会话实测：cpp 用于 v3 采集（35 场/s）
.venv\Scripts\python.exe -m mahjong_ml.online run --init <ckpt> --label <label> --generations N  # 缺省 20 分钟/轮、缓存档硬闸门
```

- **数据根**：`S:\mahjong-training`（配额 200 GB，回收机制在 `paths.py`，`MIN_FREE_GB=10`）。
- `probe/` 有 **3 个 ACL 残留目录**（`cmp-par4` / `cmp-par5` / `mj-diag-pru96cdj`，0 B）——**删不掉，别再当"清理没做完"**。
- `client/dist` 只在 `-Deploy` 时更新；**发布前先看 `client/` 有没有改过**。

---

## 5. 操作纪律（本会话踩过/确认过的）

1. **推送（与并行会话共存）**：原生 `git fetch/push` 在本机不通（schannel 取不到凭据），
   `github_git_push` 也不一定行 —— 它走的是 broker 的 `git/…` 通道，**本次对 github.com 返回 502**
   （REST 通道同时正常；两条通道互不相干）。可靠的只有 **`github_commit_files`（REST）**。
   **判据是"远端提交的 tree == 本地 HEAD 的 tree"**，不是"推送成功"（REST 建的提交 sha 与本地**天然不同**，
   永远别指望 sha 相等）。**算法**（本次实测最省事）：
   ① `GET /git/ref/heads/Training` 拿真实远端头；② `GET /git/commits/<头>` 读它的 **tree sha**；
   ③ 与本地 `git log --format='%h %T %s'` 逐行比 —— 命中哪个本地提交，就知道远端内容等于哪一代；
   ④ `git diff --name-status <那个提交> HEAD` 得出**要推的文件**（含删除项）。
   ⛔ 别用 `GET /git/trees/<sha>?recursive=1` 列全仓：本仓 352 个文件，**响应会被工具截断**。
   一次带上**全部**改动文件，避免交错时覆盖对方改过的同名文件。
2. **多行提交信息不要塞进 PowerShell 引号**（`"` 会提前结束字符串）⇒ 用 `write` 落一个文件 + `git commit -F`。
3. **改文件一律用 `edit`/`write` 工具**，别用 PowerShell 做多行替换（CRLF 与 `` `n `` 不匹配会静默不生效）。
4. **`AGENTS.md` 预算**：**61,345 B**（提醒线 61,440 / 硬上限 65,536 —— **只剩 95 B**）——
   新内容先想能不能进 NOTES，改完必跑 `doc-refs-check`。
5. **v4 契约以代码为准**：`python -m mahjong_ml.v4 spec` 现场打印块清单；文档与代码冲突时改文档。

---

## 6. 待决问题（留给下一轮定）

1. ~~要不要推那几个本地提交~~ ✅ **已推 + 已发布 v1.12.0**（§2.11）—— 下一轮的推送照 §5 第 1 条走。
2. **v3 谱系是否彻底封存**：数据集已删，若想再要"更强的 v3 基准对手"，只能重采重训（同种子可复现）。
3. **步长/epoch 实验**（`--epochs 4→2` 把 PPO 读盘 5 遍压到 3 遍）—— 本会话实测"KL 撞墙但无增益"，
   留作 v4 落地后的对照项，不是当务之急。
4. **`AGENTS.md` 是否再瘦身**（把 §2.3-15 / §6.7 的流程细节搬进 NOTES）—— 目前余量仅 **95 B**，
   下一轮只要往 §2.3/§6 加一段就会越过提醒线。
5. **`events[]` 的累积口径要不要改成"只发增量"**（obs v3 第 1 轮新出）：累积让轨迹 ×2.4~2.8
   （1,608 → 3,798 B/决策），增量能把体积压回 ~1.1×，但 `blocks.tile_matrix` /
   `RiverState.full_recompute` 都得改成"消费侧按小局攒"。**现在 Java 与 C++ 两侧都已按累积实现**
   （C++ 已过 200 场逐字节），所以改的代价是**两侧一起改 + 重跑 parity**；
   ⚠ 前提变了：v4 数据集**已经不是零**（`v4-bc-001/002` 已建）⇒ 改口径还要**重采 + 重建数据集**。
6. ~~`aux.npz`（标签侧）何时落盘~~ ✅ 已落地（§2.8，`v4 plan` 12/13）。
7. **v4 的 `belief` 头是否进推理**（设计里标 ✅：与危险头一起驱动押し引き）—— P1 校准达标后再定权重。
