# 会话交接（HANDOVER）—— 2026-09-27

> **这份文件是"会话状态转移"**：新会话/新同事从它接上，不必重读整段对话。
> 它**只记状态与下一步**（判据与规范在 `AGENTS.md` / `docs/TRAINING-V4.md` / `docs/FEATURES-V4.md`），
> 每次交接**整段重写**（不要追加成历史堆）。索引：`docs/INDEX.md`。

---

## 1. 十秒速览

| 项 | 值 |
| --- | --- |
| 分支 / HEAD | `Training` · **本文件所在提交**（`git log -1 --oneline` 取真值）；其父提交 `8b069fa`（obs v3 + v4 两轮预训练），再往前 `06d9ebd`（本文件首次落地）|
| 远端 | `Tetrachlorosilane/sMahjong@Training` 头 = **`8446ea5b`（= v1.12.0 的 tag）**，其 tree `6c746e4c…` **== 本地 `8b069fa` 的 tree** ⇒ **已同步**（§2.11）。⚠ 走 REST 推送时**本地与远端的提交 sha 本来就不同**，判据只有 tree |
| 发布 | **v1.12.0 已上线**（release **#397670321**，2 个资产：client + server；**不带机器人包** —— v4 权重还不能导出）；`bot-ai/` 沿用 v1.11.0 三个（已开包核对 `sd=615/cd=96` 与服务端同规格）|
| 数据盘 | `S:\mahjong-training` = **11.37 GB**（`raw` 1.64 · `compact` 9.66 · `ckpt` 0.08 · `league` 0.001）；S 盘可用 **218.3 GB / 231.5 GB**（本轮做过全清，见 §2.3）。v4 两轮：`raw\v4-bc-001` 0.45 / `v4-bc-002` 1.19、`compact\v4-bc-001` 2.13 / `v4-bc-002` 5.71 GB |
| 训练进度 | **v3 谱系已到平台**；**v4 已完成两轮 teacher 预训练**：教师一致率 **0.608 → 0.619**（首合法基线 0.165），**所有辅助头都收敛**（`value` 6.63→3.59、`belief_tenpai` 0.173→0.140、`danger` 0.328→0.297），见 §2.9/§2.10 |
| v4 状态 | **P0 全部收口**（`v4 check` PASS、`v4 plan` **12/13 绿**、selfcheck **419/0**）：obs v3 三端 + sidecar v3 + 数据集层硬闸门 + **标签侧 `aux.npz`**；下一步是 **P1 收尾**（§7.5 均衡审计 + 更难的自监督 + 放量，见 §3） |
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

---

## 3. 下一步：**P1 收尾（均衡审计 + 更难的自监督）+ 放量**

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

**这一轮（P1 收尾）要做的四件**（按优先级，前两条是判据要求的）：
① **§7.5 均衡审计还没跑过**（判据⑩要求：座位偏差 / 对手族 CV / 亲家 / 结局）——
   `harness.balance` 已有骨架，要在 `v4-bc-002` 的**采集轨迹**上出一份 `balance.json`；
② **更难的自监督**（现在的掩码事件类型一致率 0.968 ≈ 白给）：换"连 `tile_kind` 一起重建"
   或"掩码整段窗口"，或者按结论砍掉 SSL、把权重让给 policy；
③ **放量**：P3 的 6M 决策规模下，数据集构建（现 8 进程 ≈3,500 决策/s）与磁盘（v4 数据集 ≈22 KB/决策）
   都要再核一遍预算；
④ **P5 的前半**（可选）：v4 前向的 Java/C++ 落地 —— 在此之前 checkpoint 只能离线评估、打不了对局。

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

**下一轮的起点**：从 §3 的四件里挑 **① §7.5 均衡审计**（判据⑩，至今没跑过）——
它是唯一"判据要求但从未执行"的一项；harness 骨架在 `v4/harness.py`，数据用 `v4-bc-002` 的采集轨迹。

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
pwsh -File server\build.ps1 ; java -jar server\build\mahjong-server.jar --selftest   # L1（当前 1403 项）
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
