# Java 服务端 ↔ v4 模型接口 只读审计（2026-10-13）

判据：`AGENTS.md §6.5`「头是否上线 = 前向是否读取（⛔ 非"训练过"）」· `docs/TRAINER-CPP.md §6.25`。结论：**❌ 真缺 = 无**，差异全为设计性（🟡）。未改任何代码/配置、未训练、未采集。

## A. 对局内推理（Java 手写前向）

| 模型侧头（`python/mahjong_ml/v4/model.py:51-62`） | Java 前向 | 状态 |
| --- | --- | --- |
| `policy [L]` | 取张量 `V4Policy.java:950` · 算 `:993` · 决策 `:1029` | ✅ 已实现且被读 |
| `belief_tenpai [3]` | `:967`→`:972` sigmoid→`:976-987` 逐候选门控→`:991-993` | ✅ 被读；**按 checkpoint 退化**，见下 |
| `value[51]`/`placement[4]`/`belief_hand[3,34]`/`danger[L,4]`/`effect[L,3]` | 计算 `:1000`/`:1001`/`:1002`/`:1004`/`:1005`；绑定 `:476-487` | 🟡 已实现但**不进决策**（server main 无消费者；仅 `SelfTest.java:4092-4094` 与 `tools/V4Probe` 探针读） |
| 非头 `e_tokens`/`h_evt` | `:879`(q_evt) / `:921-938`(fusion.mem 逐候选门控) | ✅ 被读（`fusion.mem.*` 可缺 ⇒ 恒等） |

- 三端表一致：`ONLINE_HEADS={policy,belief_tenpai}`·`TRAIN_HEADS={policy,value,belief_tenpai,danger}`（`V4Policy.java:68-73` ↔ `model.py:65,77` ↔ `trainer/src/v4policy.hpp`）⇒ `tools/head-tables-check.mjs` **PASS**（实测）。
- ⚠ **逐 ckpt 门控非退化性**（`AGENTS §6.5` 要求，否则假绿）：直接解析 `net.bin` 张量表取 max|w| ⇒ `bot-ai/v4-bc-004` 与 `bot-ai/v4-ppo-001` 的 `heads.policy_gate.weight/bias` **全 0** ⇒ 该 ckpt 的 `belief_tenpai` **不在推理路径**（退化为 `{policy}`，与 `V4Policy.java:63-66`「每个 checkpoint 的性质」相符）；`bot-ai/wl-dama` `policy_gate.weight max|w|=0.0519 / bias 0.0410` + `fusion.mem.weight 0.0193` ⇒ 真在线。
- **观测输入**（`V4Features.java:60-99` 通道表 ↔ `docs/FEATURES-V4.md §4` ↔ `python/.../v4/blocks.py`）：四张量 `tile[34,48]`(`:266-343`)·`evt[60,96]`(`:411-473`)·`ctx[64]`(`:502-604`)·`cand[n,128]`(`:608-618`) **全块覆盖**；Java 推理**实时自己算**派生量（`tile.safety/danger` ← `ObsFeatures.perSeat` `:316-319`；v3 的 615 维同理 `NeuralPolicy.java:205`）⇒ sidecar 只是离线缓存，不需要 Java 读。
- 刻意留 0 且两侧同值（🟡 设计性）：`ctx.self` 第 8/10 维（`V4Features.java:567,569` ↔ `blocks.py:419,421`）、`cand` 派生 ⑨⑩⑪（`FEATURES-V4.md:209` ↔ `blocks.py:473-475`）。
- **增量事件缓存**：`V4Cache.java` + `V4Policy.eventTowerCached:580-670`；开关 `Main.java:189-192`；判据 `SelfTest.v4CacheTests`（`SelfTest.java:4199-4426`，13 位点：level 1/2/3 `:4294`、真命中 `:4299`、小局切换 `:4302`、`h` carry 逐位 `:4371`、陈旧 `:4417`、红证 `:4425`）✅
- **动作空间**：只给 `obs.legal` 打分（`V4Features.legalOf:193-202`→`candMatrix:608-618`→`logits:1029`），`legal=Action.enumerate`（`Observation.java:204,226-232`），键文法 `Action.key():208-224` 与 `PROTOCOL §8.3:1176-1186` + `selfplay-check.mjs:178` 正则一一对应 ⇒ **训练侧有、Java 无的键：无**（裸 `pon` 为老数据兼容形态，轨迹由 `Action.resolve` 落位 `TraceRecorder.java:170`）。⚠ 唯一 fail-open（非缺口）：未知 option type 静默丢弃（`Action.java:197-199`）。

## B. 采集/训练接口（`docs/PROTOCOL.md §8`）

- **§8.2 白名单四处一致**（33 键逐个相同）：`PROTOCOL.md:1079-1102` ↔ `Observation.java:294-332` ↔ `SelfTest.java:6963-6968` ↔ `selfplay-check.mjs:30-37`。Python 侧实读并集 32 键，**差 `kuitan`**（Python 通读无引用；只被 Java/C++ 引擎内打点粗估消费 `PROTOCOL.md:1093`）⇒ 不是缺口。
- **Java 产出 ✅**：`hand_delta/hand_winner/hand_loser/hand_agari/reward_to_go/final_scores/placement`（回填 `TraceRecorder.java:309-347`）· `teacher/teacher_index`（`:188-198`）· `hand` 役种轴 `:297-304` · `game.policies/seed` `:365-375` · `aux.npz` 8 成员 `:464-472` ↔ `auxlabels.py:34-43` · `summary.json.rank_points`（`SelfPlay.java:272`）。**Python 消费而 Java 不产出：无**（`behaviour_id`/`behaviour.table[]` 由构建器从 `game.policies` 现算 + sha256 自算：`dataset.py:570-596`、`:543-567`）。
- **同一字段两处口径**：`selfplay-check.mjs` 独立重建牌河 `:226-283`、`riichi_turn :284-294`、`visible :207-219`、役种轴 `:342-428`，与 Java 同源（唯一权威 `PROTOCOL §8.2/§8.4`）；`seq_delta` 双方按「缺省=1、本实现不发」(`:1128`)；`policy` = 该行座位策略串（`TraceRecorder.java:182`），无第二份指纹实现。
- **可复现**：`SelfPlay.seedFor:190` · `debugDeterministicSeed=true:335` · 每局一份实例 `PolicyFactory` + `Policies.net:200-216`/`mixSeed:258`。
- **接口状态**：`net:<path>[@α][#T]` Java **已实现**（解析 `Policies.java:128-155`、组合 `:200-216`、`hybrid:275-298`、`sampled:224-249`；自检 `hybridPolicyTests:7470`/采样组 `7607+`）；`--teacher-label` Java **已实现**（`Main.java:152-155` + `TraceRecorder.java:188-198`）。对照 C++：两者**显式报错**（`trainer/src/policies.hpp:843-844`、`selfplay.cpp:724-728`、`producer.py:43-46,80-87` 拒跑）。

## C. 工具覆盖与盲区

| 工具 | 覆盖 | 证什么 |
| --- | --- | --- |
| `trainer-v4-parity.mjs` | golden 三端特征+前向+`h0` 红证；真实权重 Java↔C++ 逐行 logits | A（未跑，需 C++/javac） |
| `v4-cache-check.mjs` | 同种子开/关缓存 ⇒ `g*.jsonl` 逐字节 | A4（**实测 PASS**） |
| `trainer-{parity,rule,score,settle,action,opts}-parity` | 牌山/规则/打点/精算/动作键·下标/询问顺序 | A5·B7 |
| `trainer-{selfplay,aux,features,net}-parity` | 轨迹/标签 npz/sidecar/v3 前向 逐字节 | B6·B7·B8 |
| `selfplay-check.mjs` | 独立实现的数据集契约（白名单·事件流重建·役种轴·奖励不变式） | B |
| `python/selfcheck.py` | 训练侧纯函数 + sidecar 七段守卫 + DAgger 行读取(`:456-458`) | B7·B8（Python 侧） |
| `SelfTest` | `v4ForwardTests`(18 位点) · `v4CacheTests`(13) · `trainingInterfaceTests`(键/白名单/置换) · aux 组 `3577-3685` · `rewardToGoTests` | A·B |

**盲区（无任何工具覆盖）**：① Java `--teacher-label` **正向产出**（无 `SelfTest` 配置、C++ 只有负向报错、`producer.py:126` 只组命令；现有证据仅代码 + `selfcheck.py:456-458` 合成行）；② `PROTOCOL §8.2` 文档表 ↔ 三处清单**无机械比对**；③ `Action.enumerate` 未知 type 静默丢弃无负向用例；④ `seq_delta` 恒缺省无断言；⑤ `cand` ⑨⑩⑪ 恒 0 无「三端同改」断言。

## ❌ 真缺清单

**无真缺**。设计性差异三条：① `value/placement/belief_hand/danger/effect` 计算但不进决策（`V4Policy.java:57-73` 的 `ONLINE_HEADS`/`TRAIN_HEADS` 拆分）；② `cand` 派生 ⑨⑩⑪=0（`FEATURES-V4.md:209`）；③ `kuitan` 仅 Java/C++ 引擎侧消费（`PROTOCOL.md:1093`）。**不会**造成 Python↔Java 产物/动作不一致（离线读 sidecar、推理实时算，由 golden 夹具与 `features-parity` 钉住）。

## 实测 / 未验证

- 实测：`--selftest` = **通过 1507 项、失败 0 项 / SELFTEST PASS**（日志 `tools/build/_audit-selftest.txt`）；`head-tables-check.mjs` PASS；`v4-cache-check.mjs bot-ai/v4-bc-004/net.bin 1 2 424242` PASS（`g0.jsonl` 188732 B 逐字节一致）。⚠ `--selftest` 自身会在 `tools/release-fixtures/` 落测试夹具（该命令的环境副产物，非我改的源码/配置）。
- 未验证（按禁令未跑）：`trainer-v4-parity --golden`、各 `trainer-*-parity`、`python/selfcheck.py`、`selfplay-check.mjs`（需真轨迹）；门控只对 3 份权重做张量级取证，未跑 `python -m mahjong_ml.v4 check` ⑦/⑦′。

---

# 收尾（2026-10-13 第二轮）：零 gate 判据 + 五处盲区处置

同一天的第二轮：补断言/补工具（**零训练 · 零采集**），把上一轮的"下一步建议"逐条落地。

## F. 零 gate 实测表（`node tools/policy-gate-check.mjs`，新增）

判据（一行，可复用）：**`heads.policy_gate.weight` 的 `max|w| == 0`（或缺张量，旧网补 0）⇒ 该 ckpt 的
`belief_tenpai` 实际不在推理路径（`gate = 1 + tanh(0·sigmoid(bt) + bias)`，与 `bt` 无关 ⇒ 退化为
`{policy}`）；`> 0` ⇒ 在线，报量级。** `fusion.mem.weight` 同理管事件塔隐状态 `h_evt`。
⚠ 这是**逐 ckpt 的性质**（`docs/TRAINER-CPP.md` §6.25 已写明），表级常量（`ONLINE_HEADS`）只说明"代码读它"。

| 包 | 格式 | dm | `policy_gate.weight` max\|w\| | bias max\|b\| | 形状 | `fusion.mem.w` max\|w\| | 判定 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ppo-v3-g01-g01` | 1 | — | — | — | — | — | v3 格式 1，不适用 |
| `v4-bc-004` | 2 | 192 | **0** | **0** | 192x3 | **0（缺张量）** | 🟡 退化 |
| `v4-league-g08` | 2 | 192 | **0** | **0** | 192x3 | **0（缺张量）** | 🟡 退化 |
| `v4-p3-001` | 2 | 192 | **0** | **0** | 192x3 | **0（缺张量）** | 🟡 退化 |
| `v4-ppo-001` | 2 | 192 | **0** | **0** | 192x3 | **0（缺张量）** | 🟡 退化 |
| `wl-dama` | 2 | 192 | 0.0519 | 0.0410 | 192x3 | 0.0193 | ✅ 在线 |
| `wl-meld` | 2 | 192 | 0.0716 | 0.0711 | 192x3 | 0.0307 | ✅ 在线 |
| `wl-p301b-g06` | 2 | 192 | 0.0617 | 0.0450 | 192x3 | 0.0243 | ✅ 在线 |
| `wl-pon` | 2 | 192 | 0.0509 | 0.0414 | 192x3 | 0.0202 | ✅ 在线 |
| `wl-riichi` | 2 | 192 | 0.0642 | 0.0808 | 192x3 | 0.0309 | ✅ 在线 |

**合计 10 个 `net.bin`：在线 5 · 退化 4 · 非 v4 1 · 解析失败 0。**

- ⚠ **比上一轮多两条**：上一轮只点名了 `v4-bc-004` / `v4-ppo-001`；逐包全跑后又查出
  **`v4-league-g08` / `v4-p3-001` 同样全 0**（上一轮是抽样取证，不是全跑）⇒ 四个 `v4-*` 包**全部退化**。
- ⚠ **`fusion.mem.*` 在四个 `v4-*` 包里整组缺失**（= 旧网补 0）⇒ 这些包的 `h_evt` 也**不进**（逐候选门控）推理路径；
  5 个 `wl-*` 包两者都在线。
- 负向/正向自测：`--strict bot-ai/v4-bc-004/net.bin` ⇒ 退出码 **1** + `--strict FAIL：1 个包的 policy_gate 退化`；
  `--strict bot-ai/wl-dama/net.bin` ⇒ 退出码 **0**（同一份代码两个方向都实测过）。
- 用法：`node tools/policy-gate-check.mjs [<net.bin>...] [--strict]`；无参数 = 扫 `bot-ai/*/net.bin`
  （一个都没找到 ⇒ 退出码 2，⛔ 不许当"全都非退化"）。

## G. 五处盲区处置（逐条：改了什么 + 断言名 + 实跑结果）

| 盲区 | 处置 | 落点（改动） | 断言 / 判据 | 结果 |
| --- | --- | --- | --- | --- |
| ① `--teacher-label` 正向产出 | **新增端到端断言** | `SelfTest.java` 新方法 `teacherLabelTests()` + 辅助 `selfPlayRows()`；`run()` 注册 | 4 条：「学生座位每一条决策都带 teacher」「老师座位一条都不带」「teacher ∈ legal 且 teacher_index == 它的下标」「关掉开关后 0 行带 teacher」（正向对照，同种子同座位策略） | ✅ 通过 |
| ② §8.2 白名单 ↔ 三处清单 | **新增机械比对工具** | `tools/obs-keys-check.mjs`（新） | 四处（PROTOCOL §8.2 表 / `Observation.toJson` / `SelfTest.want` / `selfplay-check.OBS_KEYS`）**同集合同序**；解析失败即 FAIL | ✅ PASS（33 键；故意造的两种不一致都红，见 §H） |
| ③ `Action.enumerate` 未知 type 静默丢弃 | **保留 fail-open，但改为可观测 + 记数**（理由见 `Action` 类注释：`enumerate` 的输出就是 `obs.legal`，fail-closed 会把"服务端多下发一个 option 类型"变成整场半庄静默死亡；丢弃只让训练侧少一个动作） | `Action.java`：新增 `droppedOptionCount/droppedOptionTypes/resetDroppedOptions` + `enumerate` 逐 option 记数 | 6 条：「未知类型被丢弃且不产出动作（3 条）」「未知类型不进 legal」「丢弃条数被记数（=2）」「丢弃的选项类型被点名」+ 正向对照「全部已知类型 ⇒ 计数为 0」「9 条已知选项各展开一个动作」；另 2 条 `null` 表用例 | ✅ 通过（红证见 §H） |
| ④ `seq_delta` 恒缺省 | **加断言**（缺省语义 + 非缺省行为，按代码现状定义） | `SelfTest.java`（①e 组）+ `tools/v4-derived-check.mjs`（跨端） | 5 条：「缺省 == 显式 1（逐位）」「非缺省只改一个通道」「通道下标 = 95」「缺省 ⇒ 0.125」「=4 ⇒ 0.5」「=20 ⇒ 夹到 1.0」；另 1 条「观测事件**从不**发 `seq_delta`」；跨端 = 三端 `min(缺省 1, 8)/8` + 末槽宽 1 + 两份文档口径 | ✅ 通过（红证见 §H） |
| ⑤ `cand` ⑨⑩⑪ 恒 0（`blocks.py` ↔ `FEATURES-V4.md` ↔ Java/C++） | **加「三端同改」机械断言** | `tools/v4-derived-check.mjs`（新，风格照 `head-tables-check.mjs`：抽源码字面量比对）+ `SelfTest.java` 新方法 `candDerivedTests()` 钉 Java 侧行为 | 脚本：三段宽度 88/11/29/128、已实现 8 维、比例尺 8 值逐位、`blocks.py` 按 `DERIVED_CANDIDATE` 截断 + 锚点注释、文档 §4.3 的 88/11/29/128 + 「⑨⑩⑪ 恒 0」；自检：**派生 ①–⑧ 真的非 0（正向对照）**、⑨⑩⑪（列 96–98）恒 0、预留段恒 0 | ✅ 通过（红证见 §H） |

**⑤ 的附加发现**：把 Java 的 `cand[96]` 人为置 1 后，`v4ForwardTests` 的 4 条逐位判据
（`v4 golden：三个头逐元素一致` / `四个输出头` / `h_evt` / `argmax 与 Python 一致`）**一起红**
⇒ 那 3 列**确实被前向读取**（只是生产路径永远喂 0）。所以"三端同改"是真的承重：
只要一端开始填这 3 列、另一端不填，前向就会不一致。

## H. 红证（故意造不一致 ⇒ 变红 ⇒ 复原）

| 造的不一致 | 结果（逐字摘要） |
| --- | --- |
| `selfplay-check.mjs` 的 `OBS_KEYS` 多加 `'red_proof_bogus'` | `[obs-keys-check] FAIL …` `✗ ① selfplay-check OBS_KEYS 与 PROTOCOL §8.2 表 不一致：缺 [] 多 [red_proof_bogus]`（退出码 1）；复原后 33 键 PASS |
| 把 `docs/PROTOCOL.md` 的 `### 8.2` 标题改成 `### 8.2-bogus`（解析失败方向） | `✗ docs/PROTOCOL.md 里找不到 \`### 8.2\` 小节标题`（退出码 1）⇒ **解析不到 = FAIL**，⛔ 不是"空集也一致"；复原后 PASS |
| `trainer/src/obffeatures.hpp` 的 `kPerCandidate = 8 → 9` | `✗ cand 已实现派生维数（①–⑧）：C++ obffeatures.hpp = 9 != Python features.py = 8`（退出码 1）；复原后 PASS |
| Java 篡改三处（`Action` 停止记数 · `seq_delta` 缺省 1→2.0 · `cand[96]=1`） | `--selftest` **1529 通过 / 9 失败**：`丢弃条数被记数（…= 2）期望=2 实际=0`、`丢弃的选项类型被点名`、`seq_delta 缺省 == 显式 1`、`缺省 = 1 ⇒ 通道值 1/8 = 0.125`、`cand 派生 ⑨⑩⑪（列 96–98）恒 0` + 上述 4 条 golden 前向；复原后 **1538 / 0** |

## 收尾后的实测 / 下一步

- 实测：`--selftest` = **通过 1538 项、失败 0 项 / SELFTEST PASS**（基线 1507 + 本批 31）；
  `obs-keys-check.mjs` PASS（33 键）· `v4-derived-check.mjs` PASS · `policy-gate-check.mjs` OK（10 包全表）
  · `head-tables-check.mjs` PASS · `doc-refs-check.mjs` / `round-index.mjs --check` / `handover-facts.mjs --check` PASS。
- 仍未验证（按禁令未跑）：`trainer-v4-parity --golden`、各 `trainer-*-parity`、`python/selfcheck.py`、
  `selfplay-check.mjs`（需真轨迹）、`python -m mahjong_ml.v4 check` ⑦/⑦′。
- 下一步：① 每个**待发布** ckpt 用 `v4 check` ⑦/⑦′ + `policy-gate-check.mjs --strict` 双判；
  ② 4 个退化的 `v4-*` 包若要参与"头在线"的结论，必须**重训到非退化门控**（或在结论里写明它退化为 `{policy}`）；
  ③ `fusion.mem.*` 整组缺失那 4 个包同理。

## 建议入档 `AGENTS.md` §6.5 的判据行（逐字，**本批未改 `AGENTS.md`**，余量仅 632 B）

> ⛔ 头"上线"= 前向实际读取 **且带非退化 `policy_gate`**：`heads.policy_gate.weight` 全 0（或缺张量）⇒ 该 ckpt 的 `belief_tenpai` 不在推理路径（`gate ≡ 1+tanh(bias)` 与 `bt` 无关，退化为 `{policy}`）。逐 ckpt 量：`node tools/policy-gate-check.mjs`（⛔ 表级常量不算）。

## 原始"下一步建议"（第一轮，已完成项已勾掉）

1. ~~加一条 `teacherLabel=true` 的采集断言~~ ✅ §G①。2. ~~把 §8.2 文档表 ↔ 三处清单做机械比对~~ ✅ §G②
（`tools/obs-keys-check.mjs`）。3. `Action.enumerate` 未知 type：**保留 fail-open 但改为可观测 + 记数**
（不改成 fail-closed，理由见 `Action` 类注释）✅ §G③。4. 每个待发布 ckpt 用 `v4 check` ⑦/⑦′ 重跑
`ONLINE_HEADS` 实测（表级常量不能代替逐 ckpt 判据）+ `policy-gate-check.mjs --strict` —— **仍未做**（需训练侧环境）。
