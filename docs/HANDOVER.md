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
| v4 状态 | **P0 收口** + **P5 前向三端落地** + **§7.5 审计已做** + **PPO 回路已落地且数值上稳了**（§2.15/§2.16，四道闸门）+ **增量事件缓存已上（L2，§2.17）** + **价值头审计已做（§2.18）** + **critic 已有结论（§2.19）** + **训练端脱离 Java（§2.20：C++ 补 `--aux`，`v4 loop` 编排）**。**下一件要紧事**：① **换奖励粒度**（小局级 RWR/AWR 跑在线多轮；critic 要做先过 EV ≥ 0.4 —— 见 §3 ①），**现在可以用 `python -m mahjong_ml.v4 loop --no-java` 起轮次了**；② 性能继续（L1 张量级缓存 / 并行 / int8 —— 见 §3 ③）；③ 放量（2+2 下检出 Δ=2.0 要 ~5,400 场） |
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
- ⚠ **性能实测**：特征 ≈4.6 ms + 前向 ≈35–40 ms / 决策（单线程）——当时那句耗时"预算"
  **是不现实的指标，已从所有文档删除**（`FEATURES-V4.md` §8）；性能仍列下一轮第一件事（**只求快，不设门槛**）。

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
> 当 bot 可以、当自对弈采集主力不行。所以第一件事是把前向压快（**没有时间指标，见 §2.17 与
> `FEATURES-V4.md` §8**）。

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

**这一轮要做的事**（**顺序已按 `docs/TRAINING-V4.md` §14 的重新规划改过**）：

⓪ **先量**（P0，Python-only）：跑**消融矩阵**（逐头/逐块关掉 + 2+2 配对 CI，回答"哪个头值钱"）
   + 值头**温度缩放**（一个标量，把覆盖率拉回 ≤3pp）。规划见 §14，代码事实见 `NOTES.md` §6.5 第十六轮。
   ✅ **已落地并跑过一轮**（§2.22 / `TRAINING-V4.md` §14.6）：消融表 + 校准都出来了；
   ⏳ 还差的：`--repeats ≥ 3` 才能给辅助头下结论；温度要升级成 loc-scale 两参数。
① **换回报与信用分配**（P1，Python-only，证据最强）：奖励 = **小局收支 + 顺位点**（对齐评测口径），
   **手级 GAE**（`(game,hand_no,seat)` 构造 `next_index`，λ<1 + bootstrap），`V = 已滚入 + 残差头`。
   判据：`value-audit --strict`（⚠ 原文写 "EV ≥ 0.4 + 覆盖率 ≤3pp" —— 第十九轮证明**合法天花板只有
   0.069**、谁都过不去 0.4；已改成 `EV ≥ 0.8 × 合法天花板` + `std(A_raw)/std(目标) < 1` + 覆盖率 ≤3pp）。
   ✅ **机制已全部落地**（`--advantage gae-hand` + 冻结优势、`rank_points` 列 + `--backfill-rank`）；
   ⚠ **实测未过门槛**（EV 0.094；`std(A)` 还涨了 24%）⇒ **下一步是"把 critic 的时间尺度压到一小局"**，
   而不是继续调 λ。
   ✅ **P1b 也做了**（§2.23）：小局口径 `EV(delta) = 0.055`、`std(A)/std(delta) = 2.671×`（量纲事故，
   第十九轮修成 0.998×）、冻主干只训值头更是**过拟合**（val CE 高于边缘）。
   ❌ **第十九轮的结论把这一条整段推翻**（§2.24）：信息**不在**（合法天花板 0.0693，网络已拿 80%），
   **读出头不是瓶颈 ⇒ P4-lite 取消**。`delta`/`rtg` 这类实现值目标上 critic 最多买 ~5% 方差削减。
② **补 PPO 口径**（P2，Python-only）：优势**冻结**（现在每步重算 = 移动基线）、`grad-clip`、KL 早停、
   全局优势标准化、`ppo` 时禁 RWR、双口径 KL。
③ **多头分层与危险头稠密化**（P3）→ **三端架构**（P4：接 `h_evt`、专用 state token、候选 Q 头）→
   **小步多轮在线**（P5，用 `v4 loop --no-java`）。
④ **放量 + 算法升级**（`#0.5` 与 `rtg` 已在 §2.16 落地并判过：两处修**消掉了退步但只到打平**）——
   按现有 `sd(Δ)≈53`，要证明"比 teacher 强 2 顺位点"需要 **~5,400 场 2+2**
   （一次评测 ~2 小时；v4 前向 22.5 ms/决策是瓶颈 ⇒ 与 ⑤ 绑在一起）。可选方向：
   （a）**放量到 5,000+ 场**（只买精度，不买强度）；
   （b）**在线多轮**（每轮采→训→换行为策略）——v3 上唯一打出过 CI 排除 0 的就是在线那一套
   （`ppo-v3-g01-g05` +2.66 [+0.14,+5.18]），v4 缺的是"多轮"；粒度按 ① 的结论走小局；
   （c）**P4 的对手池/联赛**（v4 目前只有 teacher 对练；v3 的自对抗续训虽无增益，但对手多样性没试过）；
⑤ **性能：把 v4 前向压快**（**不是验收项**，耗时指标已删）——**增量事件缓存（L2）已上**（§2.17：
   38.12 → 22.50 ms/决策 = 1.70×），**剩下 ~18.9 ms 全是不可复用的稠密计算**，按性价比：
   ① **逐候选/逐头并行**（同一决策里各头/各候选彼此独立，是一眼可见的并行度）；
   ② **L1 张量级缓存**（`tileMatrix` 的派生量，特征 3.55 ms 的大头）；
   ③ **C++ 侧镜像同一套缓存**（自对弈采集在 C++，训练/评测的墙钟收益比服务端更大）；
   ④ int8 量化；⑤ 必要时缩 `dModel`（要重训）。
   ⚠ **"去分配"不是大头**（实测 GC 只占墙钟 **0.03%**，`NOTES.md` §6.5 第十一轮）——
   扁平 `float[]` + 行偏移 + scratch 复用只算 ①④ 的地基，别把收益挂在这上面；
   **谁都不承诺到达某个数字**（耗时指标已从文档删除）。
⑥ **更难的自监督**（掩码事件类型一致率 0.968 ≈ 白给，**已按结论关掉**）：
   若要再用，得换"连 `tile_kind` 一起重建"或"掩码整段窗口"，否则把权重让给 policy。
⑦ **放量**：P3 的 6M 决策规模下，数据集构建（现 8 进程 ≈3,500 决策/s）与磁盘（v4 数据集 ≈22 KB/决策）
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
- **第 12 轮 ✅**：**v4 增量事件缓存落地（L2）**（§2.17）：38.12 → **22.50 ms/决策（1.70×）**，
  三档深度七头逐位相同、命中 677/721、陈旧 0；判据 = `v4CacheTests`（1443/0）+ 
  `tools/v4-cache-check.mjs`（同种子开/关缓存 ⇒ 轨迹逐字节相同，红证已做）。
- **第 13 轮 ✅**：**价值头审计**（§2.18）—— §8.2 的分布判据落成工具 `v4 value-audit`：
  整场口径的值头**弱但真**（EV 0.44–0.57、ρ 0.66–0.76）但**覆盖率不达标**（95% 只覆盖 82–87%）；
  ⛔ **`rtg` 口径（`v4-ppo-002`）的值头是塌的**（EV 0.006、CE 只剩 0.019 nats 高于边缘基线）
  ⇒ PPO 的 critic 没在工作。自检 +16 项（含两条红证，抓出了 `explained_variance` 的一个实现 bug）。
- **第 14 轮 ✅**：**修 critic**（§2.19）—— `--freeze-trunk` / `--only-heads` 落地（自检成对判据）后实测：
  **整场口径 EV 0.4115 ✅ 达标；`rtg` 口径 0.002 ⛔ 判死**（放开主干也只到 0.010、还打坏策略头）；
  参照测量（`--ceiling`）说**可学的粒度是"这一小局"（0.632）而不是"剩下的整场"（0.090）**
  ⇒ **PPO 路线改为小局级（RWR/AWR）**，critic 要做就先过闸门（⚠ 当时的 "EV ≥ 0.4" 已废，见 §2.24）。
  ⚠ 第十九轮补注：0.632 与 0.090 **两栏都含结局列/隐藏真值**，合法列是 0.069 vs 0.014 ——
  **"小局 ≫ 整场"这个对比结论不变**，但绝对数不要再引用。

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

### 2.17 v4 增量事件缓存落地（L2；2026-09-28）

- **做了什么**：`mahjong/ai/V4Cache.java` + `V4Policy.forwardCached()`（**默认开**；`--no-v4-cache` 关）。
  **按座位分槽**（同一份权重常同时挂在两个学生席上，不分槽会互相顶掉），缓存**逐行**的三样：
  窗口 token 行（校验用）· 事件编码器输出 · 注意力 `in_proj`（`LayerNorm1` 之后）；
  窗口左移时幸存行**按引用平移**，只有新增 `delta` 行要算；增量 `h` 只在建槽时重放全部事件。
- **实测**（`tools.V4Probe --cache`，721 条真实决策 / 8 小局 / v4-p3-001）：
  **38.12 → 22.50 ms/决策（1.70×）**，前向 34.7 → ~18.9（1.83×）；
  归因：只增量 `h` 25.76 → 再复用编码行 24.35 → 再复用 `in_proj` **22.50**；
  命中 677 / 建槽 44 / **陈旧 0**，复用 19,516 行 vs 编码 2,287 行（89.5%）；
  三档深度的七个头**逐位相同**。
- **判据**：`SelfTest.v4CacheTests`（L1 **1443/0**：真实 obs 序列三档逐位 + **真的命中** +
  窗口 token 行 == `eventMatrix` + 改坏事件流必须判陈旧 + 关缓存命中 0）·
  **`node tools/v4-cache-check.mjs <net.bin>`**（同种子开/关缓存 ⇒ 整场轨迹**逐字节相同**；
  ⚠ 红证实测：把缓存故意做旧 ⇒ 立刻 FAIL）。
- ⚠ **踩到的坑（比收益本身值钱）**：第一版窗口位置公式写成 `i-(seen-len)`（只在窗口满时对），
  于是**每小局前 K 条事件全被判成"陈旧"**：600 决策里命中 31 / 退回 533，
  而"增量 == 全量"那条判据**照样全绿**（退回全量当然等于全量）⇒
  **缓存类判据必须成对：`相等` 且 `真的命中`**（详见 `NOTES.md` §6.5 第十一轮）。
- ⚠ **`h` 口径分歧（已知、已断言钉住）**：增量 `h` = 跨决策 carry（全小局事件，= `v4/cache.py` 语义），
  全量/训练侧 `h` = 窗口 60 token 冷启动；**当前没有任何头消费 `h`**（`Fusion` 收了 `h_evt` 不用），
  所以七头仍逐位相同。自检断言"**h 不同、七头相同**"——将来把 `h` 接进融合时这条会红，
  那时必须让训练也改成 carry 语义。
- **还没做**：L1 张量级缓存（要动 `tileMatrix` 的派生量）· 扁平 `float[]` + 行偏移（**不是大头**：
  GC 实测占墙钟 0.03%）· int8 · **C++ 侧镜像**（自对弈采集在 C++，收益更大）· 必要时缩 `dModel`（要重训）。

### 2.18 价值头审计（2026-09-28，`python -m mahjong_ml.v4 value-audit`）

- **做了什么**：把 §8.2 那套分布判据（CE / 边缘基线 / **EV** / CRPS / 气候学基线 / 覆盖率 / 逐点与小局 ρ）
  落成可复现工具 `v4/value_audit.py`（只读，`--strict` 可用作闸门），并给 4 个 v4 权重各量了一遍。
- **实测**（判据口径 = 该权重训练时的值头目标；表在 `docs/TRAINING-V4.md` §8.2）：
  ① 整场口径的值头**弱但真**：`v4-bc-004` EV 0.44 / ρ 0.66、`v4-p3-001` EV 0.57 / ρ 0.76、
  `v4-ppo-001` EV 0.46 —— CRPS 比气候学基线好 25–38%、十分位校准单调；
  ② ⚠ **分布形状一直不对**：只有 `v4-bc-004` 在**它自己的** val 上三条覆盖率都在 3pp 内，
  自对弈口径下 95% 区间只覆盖 82–87% ⇒ §8.2 的"≤3pp"**当前不通过**；
  ③ ⛔ **`v4-ppo-002`（`--value-target rtg`）的值头是塌的**：CE 3.6485 vs 边缘 3.6674（只剩 0.019 nats）、
  预测的逐点标准差 **1.66 千点**（目标 15.15）、**EV 0.006** ⇒ 优势 ≈ 原始回报，
  **PPO 退化成没有 baseline 的 REINFORCE**（与第五/六轮"两处修只到打平"一致）。
- ⚠ **更值钱的是目标侧诊断**：`rtg` 在 `(game, hand_no, seat)` 内是常量（逐决策本来没有可分的东西）；
  而 `Var(value−V)=238 > Var(rtg)=229.5` ⇒ **"整场值头 − 已滚入"这种构造数学上就赢不过 0 EV**
  （要赢需 `EV_value > 0.44`）；连"知道是哪一小局"的 oracle 也只解释 ~3% 的 rtg 方差。
  ⇒ **per-decision 的 suffix-sum 是坏 critic 目标**（详见 `NOTES.md` §6.5 第十二轮）。
- **判据**：`python/selfcheck.py` 新增「价值头审计」组（**21 项**，全套 **511/0**，纯合成夹具 + 红证）；
  ⚠ 红证当场抓出两个错：合成真值没落在箱中心（假红）与我第一版的 `explained_variance`
  用了**未去中心**的 MSE（常数预测报 EV=−3.7）。
- **下一步（当时的顺序）**：先把值头单独训到 EV ≥ 0.4（已在 §2.19 做完：整场口径 0.4115 ✅）；
  **最新的顺序见 §3 与 §14**（先量 → 换回报与手级 GAE → 补 PPO 口径 → 多头分层/架构 → 小步多轮）。

### 2.19 修 critic 一轮：`value` 口径达标、`rtg` 口径判死（2026-09-28）

- **做了什么**：给 `v4/pretrain.py` 加 `--freeze-trunk`（整轮冻主干）与 `--only-heads value`
  （只训点名的头：其余头权重清零 + 冻结；**绕过 `STAGE_WEIGHTS`** —— 阶段 a 里 `value` 的注册权重就是 0，
  不绕过会"跑满一轮什么都没学"）。自检 4 项，判据**成对**：主干逐位不变 + `heads.value.*` 真的变了 +
  其余头逐位不变 + 未知头名当场报错（`selfcheck` **519/0**）。
- **实测**（`compact/v4-sp-004`，init = `tools/build/v4-p3-001/net.bin`，判据 = `v4 value-audit`）：
  ① 冻主干 + 只训值头，**整场**口径 6 epoch ⇒ CE 3.504 / 边缘 3.661、**EV 0.4115** ✅（`v4-critic-final-001`）；
  ② 同配置 **`rtg`** 口径 ⇒ **EV 0.002**；③ **放开主干**（主干 5e-5、头 1e-3）3 epoch ⇒ **EV 0.010**，
  而且**教师一致率 0.882 → 0.546**（把策略头带坏）⇒ 不是训练不足。
- **参照证据**（新增 `value-audit --ceiling`：引擎真值线性读出，train 拟合 / val 计量）：
  **本小局收支 `delta` EV 0.632** · `rtg` 0.090 · 整场 0.059（再给别家真手牌也不涨）
  ⇒ **可学的粒度是"这一小局"，不是"剩下的整场"**。
  ⚠ 第十九轮查明：这三行都含**结局列（`win_flag`）/别家隐藏真值** ⇒ 是**作弊上界**；
  **合法**列是 `delta` **0.0693** · `rtg` 0.0138 · `value` 0.4490（后者大半是 `ctx.points` 的恒等式）。
  "小局 ≫ 整场"的对比仍然成立（同批列），但**绝对数不能再当门槛用**。
- ⛔ **结论（PPO 路线要改）**：`rtg` critic 停掉（λ=1 GAE 的目标正是它 ⇒ 优势 = 原始回报减常数，
  **没有 baseline**；换整场口径的 baseline 更糟：`Var(value−V)=238 > Var(rtg)=229.5`）。
  改用**小局级**目标（仓库已有的 RWR/AWR 的奖励就是"本小局收支"）；真要 critic 就做两段分解
  （`本小局收支 + 其后的后缀`）并**先过闸门**（⚠ "EV ≥ 0.4" 已废 —— 第十九轮证明合法天花板 0.069，
  现改为"EV ≥ 0.8 × 合法天花板 + `std(A_raw)/std(目标) < 1`"，见 §2.24）。
  细节见 `NOTES.md` §6.5 第十三轮 / 第十九轮、`docs/TRAINING-V4.md` §8.2 / §14.8。

### 2.20 训练端脱离 Java：C++ 补标签侧 + v4 回路 Python 化（2026-09-28）

- **卡点**：`--selfplay`/`--features`/v4 前向早就镜像完了，唯一剩的是**标签侧 `--aux`**（只有 Java 能产）
  ⇒ v4 的信念/危险头监督逼着整轮起 JVM（慢十几倍）。
- **做了四件事**：
  ① **C++ 写真正的 npz**（`trainer/src/npzwriter.hpp`，镜像 Java `NpzWriter`：STORED + 固定时间戳 +
     `.npy` v1.0 64 字节对齐）；
  ② **标签侧行**（`trace.hpp/cpp`：`AuxRow` + 采集 + 小局/整场回填 + `writeAux()`），
     决策钩子多带 `const Round&`（上帝视角，输入侧拿不到）；
  ③ **v4 回路**（`python/mahjong_ml/v4/loop.py` + `python -m mahjong_ml.v4 loop`）：八相编排
     （计划/采集 C++/派生特征 C++/校验/紧凑集/训练/导出/评测+判据+台账 `league/<label>-v4.json`），
     `--dry-run` 与 `--no-java`；
  ④ **守卫**：`MAHJONG_NO_JAVA=1` ⇒ 选到 java 生产者当场报错（防"又能跑了、只是慢十几倍"的静默回退）。
- **判据（实测）**：
  · `node tools/trainer-aux-parity.mjs 5 4 teacher 20260101 --rotate --selfcheck` ⇒ `g*.aux.npz`
    **连 zip 容器一起逐字节相同**（负向对照：翻一个字节必须报出是哪个成员）；
    混合策略 `net:…,teacher,first,random` 3 场 × 3 小局同样逐字节；
  · 端到端冒烟 `v4 loop --label v4-smoke --games 4 --hands 2 --epochs 1 --no-java` 一轮走通
    （紧凑集里 `aux_opp_hand/opp_tenpai/win_flag/opp_dealin` 非空；PPO 口径闸门 `KL≈3e-06`）；
  · `selfcheck` **528/0**（+9，含"命令里没有 JVM"与 `CPP_MISSING` 不再有 `--aux`）；
    `trainer --selftest` PASS。
- ⚠ **仍然只有 Java 能做的两件事**：`--teacher-label`（DAgger）、`net:` 的 `@α` 先验（C++ 显式报错）。
- **细节与两个坑**（JDK zip 的 EFS 位 + 9 字节扩展时间戳 extra；`loop` 的 cwd 与 argparse 转参）
  见 `NOTES.md` §6.5 第十四/十五轮、`docs/TRAINER-CPP.md` §6.23、`docs/TRAINING-V4.md` §7.2。

---

### 2.21 PPO 与多头的重新规划（2026-09-28，分析轮，代码未改）

- **触发**：用户点名"重新规划 PPO 与多头体系、分析可改进的地方"。规划落在
  **`docs/TRAINING-V4.md` §14**（三层 + 优先级表 + 判据门槛），代码事实逐条记在 `NOTES.md` §6.5 第十六轮。
- **六条代码事实**（都可 grep 验证）：① 优势用**当前** V 重算（移动基线）而 `logp_old` 是冻结的；
  ② 优势是**批内 z-score**；③ **没有 grad-clip / KL 早停**（v3 有 0.5 / 0.03）；
  ④ **推理端只消费策略头**（value/belief/danger 只过 parity）；⑤ 危险头只在**实际打出的那一个候选**上算 BCE
  （1/L 监督）；⑥ `Fusion` **收了 `h_evt` 不用**（GRU 是死重）+ 训练回报是点数差而**评测口径是 `rank_points`**。
- **规划（三层）**：**回报与信用分配**（奖励 = 小局收支 + 顺位点；**手级 GAE**：以 `(game,hand_no,seat)`
  构造 `next_index`，λ<1 + bootstrap；`V = 已滚入 + 残差头`）→ **critic/PPO 口径**（优势冻结、grad-clip、
  KL 早停、全局标准化、禁 RWR、双口径 KL、值头温度缩放）→ **多头分层**（上线层 / 训练信号层 / 诊断层；
  危险头稠密化）。
- **执行顺序**：P0 **先量**（消融矩阵 + 值头温度校准）→ P1 换回报与手级 GAE → P2 补 PPO 口径 →
  P3 多头分层/危险头稠密化 → P4 三端架构（接 `h_evt`、专用 state token、Q 头）→ P5 小步多轮在线。
- **纪律**：critic **没过** `python -m mahjong_ml.v4 value-audit --strict`（EV ≥ 0.4、覆盖率 ≤3pp）**不进 PPO**；
  每个改动必须配一条**能红**的判据。

**下一轮的起点（顺序已按 §14 改过）**：① **P0 先量**（消融矩阵 + 值头温度校准，Python-only）；
② **P1 换回报与信用分配**（小局级 GAE，Python-only，证据最强）；③ P2 补 PPO 口径；
④ 之后才谈 P3/P4（多头分层与三端架构）。

### 2.22 P0/P1 落地（2026-09-28）：工具齐了，手级 GAE 没过 critic 门槛

- **P0 消融矩阵已能一键出表**（`python -m mahjong_ml.v4 ablate`）：实测三条 ——
  **`cand.derived` 是策略命脉**（关掉 top1 0.871 → **0.480**、policy CE 0.35 → 1.52）；
  **`ctx.points` 主要喂 critic**（关掉 value CE 3.459 → 3.783）；**五个辅助头在当前预算下量不出边际价值**
  （点估计全在噪声内 ⇒ 要 `--repeats ≥ 3` + 更长预算或 2+2）。
- **P0 温度校准部分达标**（`value-audit --calibrate`）：T=1.628、CRPS 10.295 → 10.153、
  覆盖率 0.367→0.447 / 0.674→0.763 / 0.853→0.872（50%/95% 仍差 5.3/7.9pp）⇒ 单标量不够，要 loc-scale。
- **P1 手级 GAE 已落地**（`--advantage gae-hand`，优势预计算并冻结；`rank_points` 列 + `--backfill-rank`）：
  ⚠ **方差没降**（`std(A_raw)/std(rtg)` = **1.239×**，因为 `V_old` 是整场口径的、与 `rtg` 相关只有 0.042）；
  ⚠ **EV +0.0944 < 0.4（门槛未过）**，但 CE(3.626) **低于**边缘基线(3.697)（`rtg` 口径是高于的）、
  CRPS 优于气候学、覆盖率差 2.9/3.7/4.1pp（原来 12–13pp）⇒ **有信号了，但远不够当 baseline**。
  ⚠ `EV 0.094 ≈ 引擎真值线性对 rtg 的 0.090` ⇒ **"多手后缀和"这一族目标上限 ~0.1**，换 λ 救不出来。
  ⚠ 第十九轮补注：那个 0.090（以及下面那个 0.632）都是**含结局列**的作弊上界，合法列是 0.014 / 0.069；
  机理判断不变，绝对数不要再引用。
- **⇒ 下一轮第一件事**：critic 的时间尺度**压到"一小局"**（目标 = 本小局收支；
  bootstrap 不跨小局），`--rank-weight` 作终局项单独加；先量该口径的 EV 门槛（⚠ 门槛已改成
  "≥ 0.7 × 合法天花板"，见 §2.24）。
- **判据**：`python/selfcheck.py` **553/0**（+25：手级链/GAE/奖励、块偏移与消融、温度缩放、rank 回填三条不变式）；
  `docs/TRAINING-V4.md` §14.6 有完整三张表；`NOTES.md` §6.5 第十七轮记了三个坑
  （局部变量遮蔽模块 spec、`std(A)` 归一化假象、Windows 同名文件 memmap 冲突）。

### 2.23 P1b：小局口径也不行 —— **瓶颈是读出头**（2026-09-28）

- **做了什么**：把 critic 的时间尺度压到**一小局**（`--advantage hand --value-target delta`：
  `A = 本小局收支 − V(s)`、无跨小局 bootstrap、值头目标 = 本小局收支）；顺位点项只进优势。
  校准从"温度单参数"升级为 **loc-scale（温度 + 平移）**（`fit_calibration`/`shift_distribution`）。
- **判据（实测，`v4-hand-001/002`，PPO 2 epoch）**：`EV(delta) = +0.055`（门槛 0.4 ❌）；
  CE 2.550 < 边缘 2.602 ✅；CRPS 2.476 < 气候学 2.559 ✅；覆盖率 50/80/95% = 0.530/0.781/0.936
  （差 3.0/1.9/1.4pp）⇒ 80/95% ✅；MAE 3.216 **略差于常数基线** 3.185（分布收缩：pred_std 1.30 vs 真值 5.21）。
- ⛔ **方差依然没降**：`std(A_raw)/std(delta) = 2.671×`。⚠ **第十九轮查明是量纲事故**（§2.24）：
  行为策略 `v4-p3-001` 的值头学的是**整场**收支（`V_old std 12.392` 千点）、奖励是**小局**收支
  （`std 5.047`）⇒ `A = r − V` 被放大到 √(5²+12²)≈13。原文把根因写成"必须用行为策略的旧口径 V"
  是**错的**：基线是状态函数，线性重标定不改梯度期望、只改方差。
- ⛔ **冻主干只训值头反而更差**（6 epoch）：val CE **3.49 > 边缘 2.60**、`EV −4.34`、`pred_std 11.07`
  ⇒ **train CE ↓ / val CE ↑ = 在 `mean(u)` 上过拟合**。
- ⇒ ~~结论：瓶颈是读出头 ⇒ P4-lite（三端）~~ **该结论已被第十九轮推翻**（§2.24）：
  合法天花板只有 **0.0693**，而网络已拿到 **0.0552 = 80%**；池化/读出深度都试过，**P4-lite 取消**。
- **校准**：loc-scale 在 `delta` 口径上 T≈0.95 + 平移 −0.10 格 ⇒ 覆盖率差 **2.8/3.4/2.2pp**（三档里两档达标）；
  合成"系统性偏低"用例上 CRPS 1.911 → **0.381**（温度单用反而 4.85 —— 位置偏差温度修不了）。
- **判据**：`python/selfcheck.py` **561/0**（+8）；`docs/TRAINING-V4.md` §14.7 + `NOTES.md` §6.5 第十八轮。

### 2.24 第十九轮：天花板合法性 + 读出头探针 + 基线尺度对齐（2026-09-28）

**动三端（P4-lite）之前先验"信息到底在不在"** —— 三个探针，只花一次 CPU 前向的时间。

1. **`--ceiling` 按合法性分组（推翻 0.632）**：老的"10 列 state 参照"里 `win_flag` 是**本小局结局本身**
   （单这一项就贡献 EV(`delta`) **0.5088**）、`opp_*` 是**别家隐藏真值** ⇒ 那是**作弊上界**。
   合法列（公开状态 64 维 + 逐候选牌效聚合 + 自家向听/听牌 + 引擎牌效，共 92 列）的
   `delta` 天花板 = **0.0693**；只给"自家向听/听牌 + 引擎牌效"6 列 = 0.0347。
   顺带查清：整场值头那个 `EV(value) 0.44–0.57` 大半是 `ctx.points` 的**恒等式**（`legit_ctx` 单独 0.4457）。
2. **读出头探针 `value-audit --readout`（冻结躯干，train 40k / val 20k 行，标准化岭回归）**：
   `mean_all`（现行）**0.0513**、`mean` 0.0675、`max` 0.0681、`std` 0.0494、`tile_pool` 0.0071、
   `h_evt` **0.0142**、五块拼起来 **0.0701**；非线性 2×256 MLP：`mean_all` 0.0143 / 960 维上 **−0.3984**。
   ⇒ 池化全换只值 **+0.019**、读出加深**反而过拟合**、`h_evt` 几乎没信息；
   而**合法特征天花板 0.0693 ≈ 躯干最好池化 0.0701** ⇒ **读出头不是瓶颈，P4-lite 取消**。
   值头 `pred_std 1.30` vs 真值 `5.21` 是低信息目标下的**最优收缩**，不是 bug。
3. **基线尺度对齐 `--baseline-fit {scale,mean,none}`（缺省 `scale`）**：在学生行上对奖励回归
   `[1, V]`（闭式最小二乘）取 `α+β·V`。基线是**状态函数** ⇒ 线性重标定**不改梯度期望、只改方差**；
   `β=0` 是嵌套特例 ⇒ 不可能比"只减均值"差（自检红证 + `SystemExit` 守卫）。
   实测 `β=0.080`（理论 0.083）⇒ `std(A_raw)/std(delta)` **2.671× → 0.998×**（≈√(1−ρ²)）。
4. **判据修订**：闸门 `EV ≥ 0.4` → **`EV ≥ 0.7 × 合法天花板`**（`--ev-ref legit` 现算，随数据集滚动）+
   **`std(A_raw)/std(目标) < 1`** + 覆盖率 ≤3pp（不变）。比例取 0.7 的理由：线性参照的抽样噪声 ±7%，
   0.7 能把"学到信号"（实测 0.72–0.80 ×）与"塌成均值"（0.3 ×）分开。
   **复判**：`v4-hand-001` = 0.80 ×、`v4-hand-003`（对齐基线的重跑）= 0.72 × ⇒ **值头已到天花板**；
   `v4-hand-003` 覆盖率三档全过（2.1/2.5/1.6pp）。
   推论：**`delta`/`rtg` 这类实现值目标上 critic 最多买到 ~5% 的方差削减**（`EV 0.055`）。
5. **判据**：`python/selfcheck.py` **577/0**（+16，含三个"期望值写错"被红证抓出的修正）；
   `docs/TRAINING-V4.md` §14.8（含四张表）+ `NOTES.md` §6.5 第十九轮。

### 2.25 第二十轮：逐决策 shaping（路线 A）也被判死（2026-09-28）

- **做了什么**：用户选定"换更密的时间尺度"。**先量再写代码** —— 新加 `v4/adv.py`
  `decision_chain`（逐决策链）+ `pbrs_return`（PBRS 回报）与 `value-audit --shaping`
  （势函数 × θ 扫描）。判据 = **绝对量纲**下的残差比 `std(y−ŷ)/std(R_c)`（不能用"shaping 目标的 EV"，
  那会被状态已知成分刷高）。
- **实测（train 40k / val 20k，`std(R_c)`=5.174 千点）**：残差比在 **θ≈0 最小、两侧都变差**；
  最好一档（`tenpai`、θ=−2/−4）只到 **0.933**（降 3.2%），`shanten` 最好 0.948 ⇒ 远不够进训练回路。
- **定理**：PBRS 有 `Q'=Q−Φ`、`V'=V−Φ` ⇒ **`A'=A`**（自检数值红证 `max|ΔA|=7.1e-15`）
  ⇒ "让 critic 有东西可学"在原则上不成立，只能省近似误差 —— 而实测那点收益被 `Φ_end` 的方差吃掉。
- ⛔ **结论**：**P4-lite 与 `--shaping-*` 都不做**。剩下两条路（二选一或并行）：
  ① **偏置 shaping**（`r = 小局收支 + w·逐决策进展`，非 PBRS ⇒ 真会改变 A、真能降方差，
  但**改变最优策略**，只能用 2+2 对局强度判成败）；
  ② **完全不碰 critic**：补 PPO 数值口径（`grad-clip` / KL 早停 / 全局优势标准化 / 禁 RWR）
  + 样本量（`sd(Δ)≈53` ⇒ 检出 Δ=2 要 ~5,400 场）。
- **判据**：`python/selfcheck.py` **593/0**（+9：逐决策链、望远镜相消、θ=0 退化、θ 线性、
  Φ≡0 时 θ 无效、未来势增大方差、优势不变性、`Q'=Q−Φ`）；`docs/TRAINING-V4.md` §14.9 +
  `NOTES.md` §6.5 第二十轮。⚠ 三个写错的测试夹具被红证当场抓出（θ 传错、γ 混比、夹具里没有同小局多决策）。

### 2.26 第二十一轮：PPO 数值口径（路线 B）—— 量级问题比预想大一个数量级（2026-09-28）

- **做了什么**（`v4/pretrain.py`）：`--grad-clip`（缺省 **0.5**，v3 口径；返回**裁剪前**范数）、
  `--kl-early-stop`（缺省 **0.03**）+ `--kl-min-steps`（缺省 100，判据抽成纯函数 `kl_stop_hit`）、
  **双口径 KL**（`kl` = `KL(π_old‖π_new)`、`kl_inv` = `KL(π_new‖π_old)`）、
  `assert_ppo_excludes_rwr`（PPO 与 `--rwr-beta>0` 硬拒）、优势**只归一化一次**
  （手级路径已全局 z-score ⇒ 训练里 `norm="none"`）。
- **实测（`v4-hand-004` vs `v4-hand-003`：同数据、同行为策略、同 lr，只加数值口径）**：

  | 量 | `v4-hand-003` | `v4-hand-004` |
  | --- | --- | --- |
  | 实际步数 | 5144（跑满 2 epoch） | **102**（KL 早停 step 101） |
  | `\|g\|` 裁剪前 | 未记录 | **6.33** ⇒ 每步被裁到 0.5（**12.7×**） |
  | 触发步 KL / `kl_inv` | — | **0.0707** / **−0.0116** |
  | 学生行 val top1 | 0.897 | **0.893（102 步就到）** |

- ⇒ **前几轮的 PPO 跑得远超 KL 预算**：单批 KL 在第一处生效点就 0.0707 > 0.03，而 **102 步**
  就把学生行 top1 推到 0.893（跑满 5144 步才 0.897）⇒ 多出来的步数买不到一致率。
  另外**裁剪前梯度范数 6.33**（上限 0.5）说明没有 `grad-clip` 时实际步长是设定值的十几倍 ——
  这是"KL 看着不大却把策略带歪"那类现象的力学解释。
- ⇒ **口径改成"多轮 × 每轮短"**（每轮 ~100 步 + **重新采一批**），把"epoch 数"这条杠杆换成
  "新轨迹 = 新行为策略分布"，与 §3 的在线多轮合并。
- **判据**：`python/selfcheck.py` **600/0**（+7）；`docs/TRAINING-V4.md` §14.10 +
  `NOTES.md` §6.5 第二十一轮。

### 2.27 第二十二轮：训练口径进回路 + 多轮端到端冒烟（2026-09-28）

- **做了什么**：§2.26 的结论是"一轮的价值在**重新采一批**"，但 `v4 loop` 当时转发不了这些开关
  （`--value-target` 只认 `final|rtg`、没有 `--advantage`/`--max-steps`/数值口径）⇒ **口径只活在
  shell 历史里**。补齐：`--value-target {final,rtg,delta}`、`--advantage {auto,gae-hand,hand}`、
  `--rank-weight`、`--baseline-fit`、`--grad-clip`/`--kl-early-stop`/`--kl-min-steps`（显式进命令）、
  **`--max-steps`**（「每轮短」，缺省 0 = 不限）。
- **端到端冒烟（`smoke-short2`，2 代 × 12 场，`--no-java`）**：每代 采集 23–26s → 特征 → 校验 →
  紧凑集 7s → 训练 22s → 导出 3s → 2+2 评测 57–66s → 台账。第 2 代采集用的是**第 1 代导出的 net.bin**
  （链起来了）；两代都打出 `基线重标定 β=0.039/0.063`、`std(A_raw)/std(delta)=0.995×/0.992×`
  ⇒ 小局口径 + 数值口径在回路里真的生效。判据 `Δ=−9.91 (p=1.000,n=40)` / `Δ=−3.25 (p=0.636)`
  —— `required_n` 是 4,289–5,621，**40 场本来就测不出强度**（冒烟只验管道）。
- ⚠ **坑**：`--hands 2` 截断半庄 ⇒ `reward_to_go` 退化成整场结果 ⇒ **`selfplay-check` 当场判红、
  回路中止**（那条检查是对的）。小冒烟用 `--hands 0` 或 ≥8 小局。
- **判据**：`python/selfcheck.py` **606/0**（+6）；`docs/TRAINING-V4.md` §7.2 +
  `NOTES.md` §6.5 第二十二轮。

### 2.28 第二十三轮：值头闸门进回路（2026-09-28）

- **问题**：目标写的是"判据 = `value-audit` EV/覆盖率"，但回路**只看 2+2 顺位点** ⇒ 一轮跑完
  **没人验值头**（"覆盖率 ≤3pp"断在回路之外）。
- **做了什么**（`v4/loop.py`）：新增 `audit` 相（`export` 与 `eval` 之间）跑
  `value-audit --strict --ev-ref legit`，把 `ev / ev_ref / ev_need / cov0.5·0.8·0.95 / ok`
  写进台账；口径由 `AUDIT_TARGET`（`final→value` / `rtg→rtg` / `delta→delta`）映射；
  **读不到审计文件按"未过"**；`--strict-gate` 才拦住回路（缺省只记账）。`_run` 加 `allow_fail`
  （判据相用退出码 2 表示"没过"，要记账而不是抛异常）。
- **实测（`smoke-gate`，1 代 × 8 场）**：七个相全跑通（含 **audit 18.8s**），审计**如实判 FAIL**
  （玩具量级下 EV −5.82、覆盖率 0.45/0.58/0.74）⇒ **闸门不会因"数据太小/模型太烂"静默放行**。
- **判据**：`python/selfcheck.py` **614/0**（+8，含闸门正反两面与"读不到按未过"）；
  `docs/TRAINING-V4.md` §7.2 + `NOTES.md` §6.5 第二十三轮。

### 2.29 第二十四轮：战役当场抓出真 bug —— 顺位点项 vs 基线拟合对象（2026-09-28）

- **现象**：`v4-mr01`（3 代 × 120 场、`--rank-weight 0.2`）第 1 代训练**被第二十轮的守卫拦下**：
  `基线重标定后优势方差不降反升（1.1604× > 1.0000×）`。**守卫是对的，代码是错的。**
- **根因**：顺位点项**只加在链末小局**，而基线拿 **`delta`** 拟合 —— 拟合的不是"要减掉基线的那份奖励"
  （`delta + θ·顺位点`）。两个后果：① 最小二乘最优性不成立（β 差 24%）；② **守卫的参照也错**：
  它拿 `min(1.0, 未重标定)` 当上限，而**目标自身方差本来就 > `std(delta)`**（实测 rw=0.2 时
  `std_ratio_flat` **1.2098×**）⇒ 只要 rank 的方差不小，**最优基线也会被判违规**。
- **修法**：`fit_target = d_row + rank_row`（含顺位点），**按它拟合**也按它减；守卫改成
  `raw ≤ min(std_ratio_flat, std_ratio_unfit)`（`flat` = 常数基线参照，同样对 `fit_target` 算）；
  日志并列打三条参照。⚠ **诚实读数**：残差只降**二阶**（1e-4 量级），一阶收益是**不再误杀合法配置**。
- **判据**：`python/selfcheck.py` **617/0**（+3）；
  `NOTES.md` §6.5 第二十四轮（含"红证第一版被实测打回、改成斜率不同才成立"的教训）。

### 2.30 第二十五轮：第一场「多轮 × 每轮短」战役实测（2026-09-28）

`v4-mr01`（**3 代 × 120 场**、每轮 `--max-steps 100 --epochs 1`、`--value-target delta
--advantage hand --rank-weight 0.2`、每轮 `audit` + 2+2 150 场、`--no-java`）跑通，台账逐轮：

| 代 | KL | `\|g\|` | 学生 top1 | 值头 EV（线） | 闸门 | 2+2 Δ |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.0794 | 5.13 | **0.890** | −0.075 (0.027) | ❌ | −5.22 (p=0.46) |
| 2 | 0.0131 | 3.84 | **0.798** | −0.004 (0.025) | ❌ | +0.26 (p=0.94) |
| 3 | 0.0085 | 3.95 | **0.785** | +0.016 (0.049) | ❌ | +4.07 (p=1.00) |

- ⛔ **KL 早停三代一次没触发**：`--max-steps 100` + `--kl-min-steps 100` ⇒ 判据只在 `step ≥ 100`
  时看，而一轮最多 100 步。**已修**（`effective_kl_min_steps`：钳到轮长 1/5 + 日志提示）。
- ⛔ **学生行 top1 逐轮掉**（0.890 → 0.798 → 0.785，n≈17k）：每轮 100 步联合微调、KL 又没管住、
  行为策略每轮换 ⇒ 三代累积磨掉"像老师"的部分。**值头在 100 步里也学不动**（EV ≈0，闸门三代全 FAIL）。
- ⇒ **下一轮的配方**：`--max-steps` 要给够（几百步 / `--epochs ≥ 2`）或**分段**（先冻躯干只训值头、
  再放策略）；`kl-min` 必须按轮长给。**"每轮短"必须配"kl-min 短"**，否则短得没有意义。
- **判据**：`python/selfcheck.py` **619/0**（+2）；`NOTES.md` §6.5 第二十五轮。

### 2.31 第二十六/二十七轮：值头相（`critic`）与它的正确位置（2026-09-28）

- **对照实验**：`v4-mr02`（值头给够步数 + 分阶段）与 `v4-mr01-g01` **同 seed** ⇒ 同牌山、同数据，
  换配方 = 干净 A/B。结果：**KL 早停真的触发了**（step 60，`KL=0.0338 > 0.03`），
  而 `stop_reason=None` 的坏配方跑满 100 步、KL 到 0.0794 —— 两边的学生 top1 是 0.894 / 0.890，
  但**早停那份的 policy loss 明显更好**（0.402 vs 0.533）。
  ⇒ 结论：**KL 预算被阶段 a 吃掉**，值头（要等 stage b/c）**一步没轮到**。
- **`critic` 相**（`--critic-steps N`，冻主干 + `--only-heads value` ⇒ **KL ≡ 0**、不占预算）。
  旁证：`v4-mr02` 与 `v4-mr03` 的 2+2 Δ **完全相同（−8.29）** ⇒ 值头相确实**没碰策略头**。
- ⛔ **顺序错了（实测）**：值头在**冻住的表示**上学好之后，PPO 阶段一推动主干它就失效
  （同一次运行 `val_loss_value` **3.62 → 5.30**）。⇒ 改成 **`train` 之后**跑 critic，
  且 `export` 导 critic 那份（`PHASES` / `--init` / `export` 三处一起改，自检钉住）。
- ⛔ **lr**：`--critic-lr 1e-3` 太大 —— 310 步把值头训到**输出尺度发散**
  （audit：EV **−6.20**、CRPS **9.35** vs 气候学 2.47、覆盖率 0.33/0.62/0.82）。
  下一轮用 **1e-4** 重跑。
- **顺带修的真 bug**：`--init` 的 help 写着"ckpt 目录或 net.bin"，底层只收**文件**
  ⇒ 给目录会报 `--behaviour 找不到文件：<目录>`（**参数名还指错**）。新增
  `resolve_weight_path()`：目录按 `<dir>/model.pt` 解（自检三条：目录 / 文件 / 两种坏输入）。
- **判据**：`python/selfcheck.py` **631/0**；`NOTES.md` §6.5 第二十六/二十七轮。

### 2.32 第二十八轮：值头相跑通后的结论 —— 两个真 bug + 决定性负面（2026-09-28）

- **两个真 bug（都修了）**：
  ① **执行顺序有两份**：`PHASES`（跑哪些相）与 `run_round` 里另写的元组（按什么顺序跑）漂移 ——
     `--dry-run` 顺序对、真跑把 critic 放到 `train` 前 ⇒ 拿不到 PPO 的 ckpt。修法：
     `phase_order(r)` 唯一真相 + `PHASE_WHAT` 只管标签，`run_round` 单循环。
  ② **闸门判的不是要上线的那份权重**：`audit` 写死 `<tag>`、`export` 导 `<tag>-critic`
     （实测 EV 差 −2.93 vs −6.03）。修法：`final_ckpt_label()` 一处决定，两者都读它。
- ⛔ **决定性负面**：直接审"要上线"的 critic 权重（`v4-mr04-g01-critic`）：**EV −6.03**、
  CRPS **9.09**（气候学 2.47）、**`pred_std 13.35` vs `true_std 5.23`**、覆盖率 0.357/0.653/0.822
  —— 比**不训**更差。而日志里 `val_loss_value` 是**变好**的（5.09 → 3.84；lr 1e-3 那轮 5.30 → 3.62）。
  ⇒ **HL-Gauss 的 CE 与 EV/CRPS/覆盖率在这里方向相反**：CE 对着**宽软标签**优化出**过散**的头。
  ⇒ `--critic-steps` **保持缺省 0**；要用 critic 得先改**目标**（直接优化 CRPS / 均值-方差匹配），
  或者就用 p3 那份值头不动。**别拿 `val_loss_value` 当"critic 变好"的证据。**
- **四轮值头闸门汇总**（同 seed、同数据，只换配方）：EV −0.075 / −2.934 / −3.330 / −2.934，
  **四轮全 FAIL**，2+2 Δ 全在噪声里（n=150，`required_n`≈4,500–5,200）。
- **判据**：`python/selfcheck.py` **636/0**（+5）；`NOTES.md` §6.5 第二十八轮。

### 2.33 第二十九轮：**闸门在完整规模的一轮上通过了**（2026-09-28）

- **复验**（`compact/v4-sp-004` 658k 行 / 3.5 万 val；`ckpt/v4-hand-003`：2 epoch PPO、
  `--advantage hand --value-target delta --baseline-fit scale`）：
  `value-audit --target delta --strict --ev-ref legit` ⇒ **退出码 0**：
  `EV(delta) +0.0496 ≥ 0.7 × 0.0693 = 0.0485`、`CE 2.5647 < 边缘 2.6019`、
  覆盖率 **2.1 / 2.5 / 1.6pp**（三档全过）、`CRPS 2.4902 < 气候学 2.5585`。
- ⇒ **判据可达且已达成过一次**；第二十八轮那四轮 FAIL 的根因是**规模**（120 场 / 60~100 步
  学不动值头），不是判据或损失函数。
- ⇒ 第二十一轮"每轮短"的结论**收范围**：`--kl-early-stop 0.03` 对长轮太紧（60 步越界），
  而通过的那条路是**没有 KL 闸门、跑满 5144 步**。两种口径各有用途：
  短轮适合"多轮 × 每轮短"，**但不能训值头**；长轮能训出可用值头、过闸门，但离行为策略远。
- **本轮实测**：400 场 + `--kl-early-stop 0.15 --max-steps 600 --epochs 2`（台账 `v4-r8`），
  看闸门能否在**轮的出口**上过。

### 2.34 第三十轮：值头要多少数据 —— 一条曲线（2026-09-28）

`v4-r8`（400 场、`--max-steps 600 --epochs 2`、`--kl-early-stop 0.15` ⇒ **1200 步跑满**、
值头 loss 2.739 → **2.545**）的闸门：EV **+0.0157**（线 0.0422 = 0.7×0.0603）❌、
CE 过 ✅、覆盖率 **2.9 / 1.1 / 0.3pp 全过** ✅、CRPS 2.386 vs 气候学 2.378 ❌（差 0.008）。

| 一轮的规模 | 步数 | 值头 EV(delta) | EV / 天花板 | 覆盖率差 | 闸门 |
| --- | --- | --- | --- | --- | --- |
| 120 场（79k 行） | 61~100 | −0.075 ~ −3.33 | ≤0 | 14~29pp | ❌ |
| 400 场（263k 行） | 1200 | **+0.0157** | 0.26× | 2.9/1.1/0.3pp | ❌（差 0.026） |
| 1000 场（658k 行） | 5144 | **+0.0496** | **0.72×** | 2.1/2.5/1.6pp | ✅ **通过** |

- ⇒ **EV 随规模单调上升；闸门需要 ≳1000 场/轮**。400 场已经"学得动"（EV 转正、**校准三档全过**），
  差的是**信号量**，不是校准。
- ⇒ 瓶颈是**每轮的数据量**（不是判据 / 损失 / 读出头 —— 那三条在 §2.24/§2.32 都已判过）。
- ⚠ `--kl-early-stop 0.03` 在长轮里等于"值头一步学不到"（60 步越界）；长轮要用 **0.15**
  （KL 稳定在 0.035、不触发）。短轮（0.03）只适合动策略。
- **判据**：`python/selfcheck.py` **636/0**（本轮只加文档）；台账 `league/v4-r8-v4.json`。

### 2.35 第三十一/三十二轮：闸门要"两个轴"都够（2026-09-28）

- **第三十一轮**：把数据量曲线做成**守门函数** `value_gate_feasible()`（+回路的警句与台账字段），
  免得下次拿小轮去读闸门结论、把"数据不够"读成"模型不行"。
- **第三十二轮：我上一轮"≥1000 场就够"被自己的实测打回。** `v4-r9`（**1000 场**、
  `--max-steps 600 --epochs 2` ⇒ 1200 步、`--kl-early-stop 0.15` 跑满、学生 top1 0.892 → 0.900）：

| 规模 | 步数 | EV(delta) | EV/天花板 | 覆盖率差 | 闸门 |
| --- | --- | --- | --- | --- | --- |
| 120 场 | ~100 | ≤0 | ≤0 | 14~29pp | ❌ |
| 400 场 | 1200 | +0.0157 | 0.26× | 2.9/1.1/0.3pp | ❌ |
| **1000 场** | **1200** | **+0.0338** | **0.44×** | **1.5/0.0/0.1pp** | ❌（只差 EV 0.020） |
| 1000 场 | **5144** | **+0.0496** | **0.72×** | 2.1/2.5/1.6pp | ✅ 通过 |

- ⇒ **EV 同时吃数据量与步数两个轴**：1000 场把步数从 1200 提到 5144，才从 0.44× 到 0.72×。
  守门函数已改成双轴（`games ≥ 1000` **且** `steps ≥ 3000`），不可读时说清是哪一轴不够。
- ⇒ **要过闸门的轮次配方**：`--games ≥ 1000` **且** `--max-steps × epochs ≥ 3000`
  （例 `--max-steps 2000 --epochs 2`）+ `--kl-early-stop 0.15`（否则 60 步就被 KL 掐掉）。
- **判据**：`python/selfcheck.py` **641/0**（+5）；`NOTES.md` §6.5 第三十一/三十二轮。

### 2.36 第三十三轮：多轮战役开跑（`--eval-vs prev` + S 盘轮换）（2026-09-28）

- **用户点名**：跑 5 轮，保持 S 盘阈值，可轮换删除已用的大规模数据集与过时数据。
- **新增 `--eval-vs {teacher,prev}`**：多轮战役真正的问题是"**这一轮比上一轮强了吗**"，所以
  `prev` 把上一轮的 net.bin 放对面 ⇒ 2+2 **同牌山配对**（`net:本轮×2 vs net:上一轮×2`）。
  第 1 代退回 teacher；真跑时缺上一轮的 net **当场报错**（不静默退 teacher，否则两种口径混进同一条曲线）。
- **配方**（前两轮量出来的两个轴）：`--games 1000` + `--max-steps 2000 --epochs 2`（⇒ 4000 步）
  + `--kl-early-stop 0.15` + `--critic-steps 0`；评测 200 场/轮 + `--eval-vs prev`，seed 固定。
- **轮换**：`release/run-campaign.ps1` 每轮成功后删 `raw/<tag>`（3 GB）、`compact/<tag>`（**15 GB**）、
  `raw/eval-<tag>`（0.4 GB），只留 `ckpt/<tag>`、`tools/build/<tag>/net.bin`、台账与审计 JSON
  ⇒ 峰值 ≈ 一轮（19 GB），5 轮滚动。⚠ 外层 `python -m` 必须带 `PYTHONPATH=<repo>/python`
  （第一次跑就栽在这条：`No module named 'mahjong_ml'`）。
- **判据**：`python/selfcheck.py` **644/0**（+3：`prev` 的标签/策略串、第 1 代退回 teacher、缺文件语义）；
  台账 `league/v4-camp01-v4.json` 逐轮追加。

### 2.37 第三十五轮：5 轮战役跑完（值头过关、强度无证据）（2026-09-28）

`v4-camp01`：5 轮 × 1000 场 × 4000 步、`--kl-early-stop 0.15`、`--critic-steps 0`、
评测 200 场 `--eval-vs prev`（同牌山）、逐轮链上一轮；**2.35 h**；S 盘全程恒定 138 GB（每轮轮换）。

| 轮 | 值头 EV | 线 | 闸门 | cov 50/80/95 | KL | 学生 top1 |
| --- | --- | --- | --- | --- | --- | --- |
| g01 | +0.0507 | 0.0584 | ❌ | 0.500/0.788/0.939 | 0.0395 | 0.8905 |
| g02 | +0.0623 | 0.0494 | ✅ | 0.517/0.796/0.950 | 0.0232 | 0.8833 |
| g03 | +0.0675 | 0.0576 | ✅ | 0.517/0.788/0.948 | 0.0211 | 0.8789 |
| g04 | **+0.0738** | 0.0565 | ✅ | 0.496/0.790/0.946 | 0.0170 | 0.8697 |
| g05 | +0.0648 | 0.0416 | ✅ | 0.521/0.791/0.933 | 0.0114 | **0.8682** |

- ✅ **值头闸门 4/5 通过**（g01 差 0.0077）+ 覆盖率每轮都在 3pp 内 ⇒ §2.34 那条
  "1000 场 × 4000 步"的配方在真实轮次里成立。⚠ 判据线逐轮不同（各自的合法天花板不同）
  ⇒ 跨轮要读 `EV/线`：0.87 / 1.26 / 1.17 / 1.31 / 1.56。
- ⛔ **强度无证据**：逐轮 2+2（同牌山 n=200、`sd≈53`）Δ = +6.11 (p=0.104) / +0.67 / −3.06 / −2.29，
  **四个 CI 全跨 0、符号不一致**；合并 ≈ +0.4（SE ~1.7）。检出 Δ=2 要 ~5,000 场/次。
- ⛔ **单调信号：学生行 top1 逐轮掉 0.8905 → 0.8682（−2.2pp，n≈16k/轮）**，而 KL 一直远低于闸门
  ⇒ 5 轮各让开一步、累计"不像老师"，不是崩溃（比短轮配方的 3 轮 −10.5pp 缓得多）。
- **⇒ 判据**：这一轮的价值头口径**站住了**；**强度问题不能靠 200 场/轮回答**。
- 欠账：`audit` 相没把 CE/CRPS 写进台账（只在 stdout）。

### 2.38 发布 v1.14.0（2026-09-29，已上线）

- **性质**：① 服务端 v4 前向走**增量事件缓存**（38.12 → **22.50 ms/决策，1.70×**，七头逐位相同）；
  ② 训练侧（`python/`，不改变对局行为）：判据相进回路、值头相、数据量守门、`--eval-vs prev`、
  5 轮战役结论（见 §2.28–§2.37）。
- ⚠ **客户端源码自 v1.13.0 起没变**（只改版本号两处）；**机器人包与 v1.13.0 逐字节相同**
  （sha256 全等 ⇒ 不新增 AI 包，v1.13.0 的强度结论沿用）。
- **发布前实测**：L1 **1443/0**、`selfcheck.py` **646/0**、`v4 check` PASS、`trainer --selftest` PASS、
  `doc-refs-check` PASS、服务端 zip 5 个 `.sh` 均 `-rwxr-xr-x`、`VERSION` = 1.14.0、
  exe 内 UTF-16 串含 1.14.0、客户端 zip 不含 `settings.json`。
- ⚠ **L2 本会话 851 通过 / 13 失败**（13 条全在音效组，`QSoundEffect` 到不了 `Ready`）：
  **对照**上一版未改动的 exe 在同一会话里同样 851/13 ⇒ 环境（非交互会话的音频初始化），
  不是代码回归；干净读数要在有音频会话的桌面上跑。
- ⚠ **本会话 shell 被限制在仓库内**（写 `S:`/`%TEMP%` 被拒）⇒ 跑自检要
  `MAHJONG_DATA_ROOT=<repo>\python\.tmp\selfcheck-root`（`.tmp` 已 gitignore）。
  顺手修掉 `selfcheck.py` 里一条**写死 `S:/mahjong-training`** 的断言（换盘/换机本来会假红），
  并把 `docs/HANDOVER.md` 里错位的 §2.21–§2.37 整块挪回 §2.20 之后（行号单调性现在自检可见）。
- 记录与摘要表：`NOTES.md` §9.5 的 v1.14.0 条目 + `release/RELEASE-v1.14.0.md`。
- **已上线**：release id **398826351**（<https://github.com/Tetrachlorosilane/sMahjong/releases/tag/v1.14.0>），
  tag `v1.14.0` → 远端 `17e15338`（tree == 本地 HEAD tree）；6 个资产的 sha256 与 GitHub `digest` 逐一核对一致。
  ⚠ 推送通道：`github_git_push` 仍是 **broker 502**（git → github.com 不通）；
  `github_commit_files` 本次多次 `建 tree → HTTP 400`（重试即过，**逐个文件推**更稳）；
  `docs/HANDOVER.md` 工作区是 **CRLF** ⇒ 必须带 `normalizeEol: true`（否则工具直接报错告诉你原因）。

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

# P5：v4 权重导出（net.bin **格式 2**）+ 三端对拍 + 性能（§2.12 / §2.17）
.venv\Scripts\python.exe -m mahjong_ml.v4.export weights --ckpt S:\mahjong-training\ckpt\v4-bc-004\model.pt --out ..\tools\build\v4-bc-004\net.bin
java -cp "server/build/mahjong-server.jar;tools/build" tools.V4Probe --bench tools\build\v4-p3-001\net.bin S:\mahjong-training\raw\v4-sp-004\g0.jsonl 400   # 特征 / 前向分开计时
java -cp "server/build/mahjong-server.jar;tools/build" tools.V4Probe --cache tools\build\v4-p3-001\net.bin S:\mahjong-training\raw\v4-sp-004\g0.jsonl 733   # 三档缓存 + 逐位对拍
node tools\v4-cache-check.mjs tools\build\v4-p3-001\net.bin 2 4 424242               # 同种子开/关缓存 ⇒ 轨迹逐字节（需先 build 服务端）
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
4. **`AGENTS.md` 预算**：**61,421 B**（提醒线 61,440 / 硬上限 65,536 —— **只剩 19 B**）——
   新内容先想能不能进 NOTES，改完必跑 `doc-refs-check`。⚠ **几乎没有余量了**：下次要往 AGENTS 加判据，
   先在同一节里删掉等量文字。
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
