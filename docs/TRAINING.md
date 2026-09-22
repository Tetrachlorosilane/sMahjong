# 立直麻将 · 机器学习训练方案（以 teacher 为基模 → 自对抗进化）

> **状态：规划文档（尚未实施）** —— 本文只描述"打算怎么做、怎么算过、怎么算没过"。
> 实现落地后，把**实测数字与红证**补进对应小节（本仓库的规矩：判据留文档、证据留数据）。
>
> 相关文档：`docs/PROTOCOL.md` §8（**训练接口的权威契约**：观测字段 / 动作空间 / 自对弈 CLI / 数据格式）·
> `AGENTS.md` §6.5（训练接口的红线）· `NOTES.md` §6.6（teacher 的策略与"改 teacher = 改标签"）·
> `docs/DESIGN.md`「teacher（内置机器人）」· `NOTES.md` §10.4（训练侧已知限制）。

---

## 0. 目标与红线

**目标**：以服务端内置的启发式 `Bot`（teacher）为基模，训练一个**深度策略网络**作为玩家，
再通过**自对抗（对抗式数据 + 联赛自对弈）**持续进化；全流程在一台个人 PC 上可跑。

**四条红线**（前三条来自 `AGENTS.md` §6.5，第四条是本文档的评测纪律）：

1. **训练侧只实现 `ActionPolicy`**（拿不到 `Round`）—— 结构上不可能作弊。想读别家手牌都没有入口。
2. **不给服务端加第三方依赖**（Maven / ONNX / PyTorch）。最终形态是：Python 训练 → 导出权重 →
   **纯 Java 手写前向**（`java -jar` 自包含是硬约束）。
3. **训练期间 teacher 冻结**：改 teacher 的行为＝改训练标签＝**旧数据集全部作废**。
   teacher 只作为"基模 / 标注器 / 评测锚点"，不参与调参。
4. **评测先行**：没有配对评测与显著性口径之前，任何"变强了"的说法都不成立（见 §7）。
5. **遵守 §0.1 的三条机器纪律**（数据只落 T 盘 / GPU ≤80% / CPU ≤75% 的核）——
   它们是**使用者的硬约束**，不是性能建议：越界就停，宁可少跑几代。

---

## 0.1 训练环境硬约束（用户制定 —— 越界即停）

> 采集、训练、评测、联赛**全部环节**都受这三条约束。它们优先于本文档后续任何"更快/更省事"的做法。

| # | 约束 | 判据（本机口径） | 一句话落地 |
| --- | --- | --- | --- |
| ① | **训练数据只许落在 T 盘** | `T:\` = 卷标 **TrainingData**，实测 **57.66 GB 可用**（已用 0.09 GB）；唯一数据根 **`T:\mahjong-training\`** | 见 §0.1.1 |
| ② | **GPU 负载 ≤ 80%** | **利用率**（`utilization.gpu`）与**显存占用**两条都算 | 见 §0.1.3 |
| ③ | **CPU 不超过 75% 的核** | 本机 32 逻辑核 → **所有进程加起来的活跃线程 ≤ 24** | 见 §0.1.2 |

### 0.1.1 数据根与配额（约束 ①）

```
T:\mahjong-training\
├─ raw\        采集下来的 jsonl 原样（最大的一坨）
├─ compact\    jsonl 转出的紧凑数组（训练直接读它）
├─ ckpt\       检查点（最新 N 个 + 历史最好）
├─ league\     联赛种群（每代一个 checkpoint）
├─ logs\       训练/评测日志与 summary
└─ probe\      冒烟测试的小样本（用完即删）
```

- **实测体积**：`--selfplay 48 --workers 24` → **53.5 MB / 48 场 = 1.12 MB/场**（703 决策/场、1.59 KB/决策）。
- **配额闸门（写之前先查剩余，超了滚动淘汰最旧的，raw 先淘汰）**：
  `raw ≤ 30 GB`、`compact ≤ 10 GB`、`ckpt + league ≤ 3 GB`、`logs ≤ 1 GB`，
  并且**任何时刻保留 ≥ 10 GB 余量**（jsonl 写满盘会让采集静默失败）。
- ⚠ **仓库里不留训练数据**：`server\build\` 下的探针目录只在冒烟时用、**用完即删**；
  要长期留的小样本（≤ 50 MB）也放 `T:\mahjong-training\probe\`。
- ⚠ **落盘失败是"静默降级"**：`SelfPlay` 建不出目录时只打一条
  `WARN 轨迹目录创建失败，改为不落盘` 就继续跑 —— 采集"看起来成功了"却一个文件都没有。
  所以：**采集前先验证 T: 可写**（写个探针文件再删），**采集后立刻看文件数**并跑
  `node tools\selfplay-check.mjs <dir>`。
- ⚠ **沙箱/权限（本环境实测）**：在受限工作区策略下（DSH `workspace-write`），Java/Python **子进程**写
  `T:\` 会被直接拒绝（`java.nio.file.AccessDeniedException` → 上面那条 WARN）。
  此时必须在**放宽权限的会话**里跑，或由使用者在本机直接跑；
  ⛔ **不要**改成写仓库目录来"绕开"（那就是违反约束 ①）。

### 0.1.2 CPU 预算：≤ 24 / 32 核（约束 ③）

| 环节 | 设置 | 为什么 |
| --- | --- | --- |
| Java 自对弈采集 | `--selfplay … --workers 24` | ⚠ **必须显式传**：默认值 = `availableProcessors()` = **32** → 一跑就越界 |
| 兜底（改不动命令行时） | `-XX:ActiveProcessorCount=24` | 用 JVM 参数给任何 Java 进程封顶 |
| Python 训练 | `torch.set_num_threads(4)` + `OMP_NUM_THREADS=4`、`MKL_NUM_THREADS=4`、DataLoader `num_workers=4` | torch 默认会吃满所有核 |
| 并行纪律 | **生成与训练不要同时跑** | 24 + 4 = 28 > 24 会越界；确要并行就按"总活跃线程 ≤ 24"重分（例：生成 16 + 训练 8） |

- **约束下的实测吞吐**：`--workers 24` → **1.95 场/秒、1407 决策/秒**
  （未受限的 32 workers 是 2.18 场/秒，见 §1）→ **≈ 7.0k 场/小时 ≈ 4.9M 决策/小时 ≈ 7.8 GB/小时（`--sample 1`）**。
- 观测：`Get-Counter '\Processor(_Total)\% Processor Time'`（32 核上 75% ≈ 24 核满载）。

### 0.1.3 GPU 预算：≤ 80%（约束 ②）

- **利用率**：小网络在 8 GB 卡上通常本来就打不满；真要压，就在训练循环里做**占空比节流**
  （每步后采样 `nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader`，超 80% 就 sleep），
  ⛔ **不要**用 `nvidia-smi -lgc` 锁频 —— 那是**全局设置**，会影响机器上其它程序。
- **显存**：`torch.cuda.set_per_process_memory_fraction(0.8)` 是"相对整卡"的比例，但
  **本机空闲时就已有约 1.7 GB 被别的进程占用**（实测 `1734 MiB / 8151 MiB`）→
  训练进程应把 batch 定在"**自己 ≤ 4.5 GB**"以内；更稳的做法是用 `torch.cuda.mem_get_info()` 动态选 batch。
- 观测：`nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 5`（每 5 秒一行）。

**这三条要变成代码，不是靠自觉**：`python/mahjong_ml/paths.py`（数据根 + 配额闸门）与
`python/mahjong_ml/guard.py`（线程数封顶 + CPU/GPU 采样节流 + 越界即停）是**第一批要写的模块**，
P0 的验收里含一条"越界自检"（见 §4）。本机探针数据留在 `T:\mahjong-training\probe\`。

---

## 1. 实测算力预算（本机 · 2026-09）

一次 `--selfplay` 探针（`--hands` 未限、完整半庄）。**参考值**（未受限的 32 workers）：

```powershell
java '-Dstdout.encoding=UTF-8' -jar server\build\mahjong-server.jar `
     --selfplay 64 --workers 32 --rotate --seed 1 --out server\build\sp-probe
# == 自对弈汇总：64 场 / 745 小局 / 703 决策/场，用时 29.3s（2.18 场/秒，1536 决策/秒），workers=32
#    流局率 11.0%   平均每场 11.6 小局
```

**§0.1 约束下的实际跑法**（`--workers 24` + 数据落 T 盘）——**以后一律用这条**：

```powershell
java '-Dstdout.encoding=UTF-8' -jar server\build\mahjong-server.jar `
     --selfplay 48 --workers 24 --rotate --seed 2 --out T:\mahjong-training\raw\seed2
# == 自对弈汇总：48 场 / 561 小局 / 720 决策/场，用时 24.6s（1.95 场/秒，1407 决策/秒），workers=24
#    落盘 49 个文件 / 53.5 MB
```

| 项 | 实测 |
| --- | --- |
| 机器 | 32 逻辑核 / 工作区盘空闲 **103.7 GB** / **数据盘 `T:`（TrainingData）空闲 57.66 GB** / **RTX 5060 Laptop 8 GB**（Blackwell sm_120，空闲时已占 1734 MiB）/ Python 3.14.3（仅 numpy）+ uv 0.11.7 |
| 对局吞吐（**未受限参考**） | **2.18 场/秒**（`--workers 32`，`--rotate`）= 1536 决策/秒 ≈ 7.9k 场/小时 ≈ 5.5M 决策/小时 |
| 对局吞吐（**§0.1 约束下的实际值**） | **1.95 场/秒**（`--workers 24`）= 1407 决策/秒 ≈ **7.0k 场/小时 ≈ 4.9M 决策/小时** |
| 轨迹体积 | 703 决策/场、**1.59 KB/决策** → **1.12 MB/场**（48 场实测 53.5 MB）→ 约束下 **≈ 7.8 GB/小时**（`--sample 1`） |
| 顺位方差 | 256 个座位-场：placement 均值 2.5、**σ = 1.118** |

**三条派生结论**：

1. **对局生成本身不是瓶颈，T 盘配额与评测才是**：约束下满速采集 1 小时就是 **7.8 GB**，
   而 T 盘的 `raw` 配额是 30 GB（§0.1.1）→ 所以默认 `--sample 4`～`8`，或落盘后**立刻**
   转紧凑格式（uint8 计数 + float16 特征，体积降 5～8 倍）再删 raw，
   **滚动窗口 ≈ 10～25M 决策**足够一轮训练。
2. **GPU 侧无压力**：0.3～3M 参数的小网络在 8 GB 上 batch 4096 绰绰有余，
   一个 epoch 过 5M 样本是秒~分钟级 —— 因此走"**多轮生成 + 多轮训练**"（DAgger / 联赛）的路线，
   而不是一次采完一个巨型数据集。
3. **样本量必须预先算**（否则"进化"全是噪声，见 §7）。

**环境准备（待验证项）**：RTX 5060 是 Blackwell，需要 **CUDA ≥ 12.8** 的 PyTorch 轮子；
本机 Python 是 3.14，官方 wheel 未必覆盖 → 用 uv 单独装一个 3.12：

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe torch --index-url https://download.pytorch.org/whl/cu128
# ⚠ 装完必须验证"真的在用 GPU"，否则会静默回退 CPU（训练慢 100 倍而看不出来）：
.venv\Scripts\python.exe -c "import torch;print(torch.__version__, torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0))"
```

---

## 2. 架构决策：两种接法，什么时候用哪个

`PROTOCOL.md` §8 定义了两种接法，本方案**两条都用**，按阶段切换：

| | **A. 外部进程当玩家** | **B. 进程内策略注入** |
| --- | --- | --- |
| 信息集 | 既有下行报文（结构上不可能作弊） | `mahjong.ai.Observation` 白名单 |
| Java 改动 | **零** | `Policies.byName` 加 `net:<权重文件>` + 纯 Java 前向 + 特征抽取 |
| 自对弈速度 | 受 TCP/思考时限约束，慢 | 与 teacher 同速（2.18 场/秒） |
| 配对评测 | 需要自己实现（同 seed 不易保证） | `--selfplay --rotate` 直接给配对数据 |
| 现成通道 | `create_room {fill_bots:3}` + `ready` → **一个 Python 进程立刻能和 3 个 teacher 打完一场** | `--policy net:a,teacher,net:a,teacher` |
| 适合 | 研究期（P1/P2 起步、换架构随便试） | 生产推理 + 大批量自对弈（P3 之后必须） |

**路线**：P1/P2 用 A（零改动、当天出基线）→ **P3 之前落地 B**（否则 RL 的样本吞吐不够）。

**B 形态要落的 Java 改动清单**（每项都要配 `SelfTest` 断言）：

| 改动 | 位置 | 断言 |
| --- | --- | --- |
| 纯 Java 前向（权重加载 + trunk + 打分头） | 新增 `server/.../ai/NeuralPolicy.java` | 与 Python 的 golden 输出逐值对拍（容差 1e-4） |
| **特征抽取**（obs → 输入向量，与 Python 同一规格） | 同上（或 `ai/Features.java`） | 用 golden obs 双侧对拍（见 §3 末） |
| `--policy net:<路径>` | `ai/Policies.java` 的 `byName` | `SelfTest` 断言未知名字仍抛错、坏文件退回 teacher |
| DAgger 标注字段（`teacher` 动作） | `train/TraceRecorder.java` | `selfplay-check.mjs` 的字段白名单同步 |
| 顺位点统计（马点+头名赏） | `train/SelfPlay.java` | 与 `RoundScoring.settle` 对拍 |

⚠ **回退纪律**：任何一种"加载失败 / 维度不符 / 动作非法"都必须退回内置 teacher
（`Policies.fromAction` 已有三道保护；`net` 的加载失败要发生在**构造期**并把原因打到日志）。

---

## 3. 模型与特征设计

### 3.1 动作建模：候选打分（不是固定 79 路 softmax）

一次询问里的合法动作由服务端给全（`Observation.legal`），所以：

```
trunk(obs)                 →  h ∈ R^d
score(h, candidate_emb)    →  每个合法动作一个标量   →  softmax over legal（+ 掩码）
```

理由：`chi` / `kan` 是**参数化**的（一次询问里最多各几种），
`PROTOCOL.md` §8.3 的固定头 79 槽只覆盖打牌/立直/无参动作；候选打分天然统一处理全部动作，
并且**逐条对齐轨迹里的 `chosen_index`**（"枚举 + 掩码"的监督信号）。
候选特征至少含：动作类型 one-hot、牌码（若参数化）、该动作之后的向听/进张/危险度、
`tsumogiri` 声明位、（吃/碰/杠）鸣后向听与是否有役计划。

> 兼容做法：打牌/立直仍可走固定 37 槽（打牌 37 + 立直 37）作为辅助头，便于与 teacher 的动作直接对齐。

### 3.2 输入特征（**封闭集合**，只许含合法信息）

| 组 | 内容 |
| --- | --- |
| 自家手牌 | 34 维计数（或 4 层 one-hot）、赤五标记、`drawn`（刚摸到的牌） |
| 副露 | 四家 × 各副露（kind + 牌 + from + 赤），建议编码成 4×34 的"明牌计数"+ 类型通道 |
| 牌河 | 四家 × 34 计数 **+ 巡目分层**（早期/立直后/摸切 vs 手切，后两者对读牌极有用） |
| 公开状态 | `riichi`/`ippatsu`、`dora_indicators`、`scores`、`round{bakaze,kyoku,honba,dealer,riichi_sticks}`、`tiles_left`/`dead_wall_left`、`total_discards`、`kan_count`、`any_call`、`haitei`/`houtei`/`rinshan` |
| 询问上下文 | `kind`（turn / claim）、`from` / `called_tile`、`win_note`（`furiten`/`no_yaku`）、`legal` 掩码 |
| **派生特征**（强烈建议） | `HandEval.shanten / advanceKinds / advanceTiles / waitShapes / of`、`Visible.drawable`、四家逐张 `Danger`（级别 + 分数 + 理由码）**+ teacher 在同一 obs 上的动作 one-hot** |

**为什么必须带派生特征**：这些正是 teacher 自己吃的东西。把它们显式喂进去，
等于让网络"从 teacher 的视角起步"，收敛速度快一个量级；也顺带成为 P5b 混合策略的接口。

**teacher 动作 one-hot 入特征**是"以 teacher 为基模"的最直接落地：
网络只需学**增量**（`logits = f(obs) + α · teacher_onehot`），最坏情况退化成 teacher，不会比基模差。

### 3.3 规模与吞吐建议

| 项 | 建议 | 理由 |
| --- | --- | --- |
| 参数量 | **0.3～3M** | 显存预算"自己 ≤ 4.5 GB"（§0.1.3，卡上已有 ~1.7 GB 被占）+ 纯 Java 前向跑得动（目标 < 100 µs/决策） |
| 结构 | MLP / 小 ResNet（1D conv 沿 34 维），d=128～512 | 34 维计数 + 少量通道，不需要大 Transformer |
| 混合精度 | bf16 训练，导出 float32（或 int8 量化） | 显存余量与 Java 侧速度；batch 用 `mem_get_info()` 动态定，别写死 4096 |
| 归一化 | 点数 `/1000`、巡目 `/18`、余牌 `/70`、计数 `/4` | 与 Java 侧**逐字一致**（见下） |

### 3.4 特征契约（train / serve 一致性）——最容易静默出错的地方

- 规格**单一来源**：`python/mahjong_ml/features.py` 是权威；Java 侧是它的**移植**。
- **golden 对拍**：从真实对局导出 N（≥200）个 obs（覆盖 turn / claim / 立直 / 鸣牌 / 杠 / 流局边界），
  Python 与 Java 各算一遍特征，**逐元素比对**（写入 `python/tests/golden/`，双向各一条自检）。
  这条不过，训练出来的模型在 Java 里就是另一个东西。
- 归一化常数、字段顺序、缺省值（`drawn=null`、`from=-1`）都在 golden 里钉死。

---

## 4. 阶段计划（每阶段都有"过/不过"的判据）

> 顺序是**串行可交付**的：每一步都能单独验收，失败就停在这一步，不要往下堆。

### P0 · 评测口径（先做，半天～1 天）
- **做法**：`--rotate` + 配对同种子；从 `summary.json` 的 `per_game` 做自助法置信区间；
  **补上 `SelfPlay` 的顺位点指标**（马点 30/10/−10/−30 + 头名赏 20，同点按座次）——
  `NOTES.md` §10.4 与 `DESIGN.md`「档 C 的收尾」点名过这个缺口。
- **判据**：teacher vs teacher 的自对弈在 95% 置信区间内 **包含 0**（评测器本身不偏袒任何座位）；
  同一 seed 重跑结果**逐字节一致**。
- **环境纪律判据（§0.1）**：`paths.py` 的配额闸门能被触发（故意把 `raw` 配额调小 → 必须拒绝写入并滚动淘汰）；
  `guard.py` 在 32 核机器上把活跃线程**封在 24 以内**、GPU 采样超 80% 时真的 sleep；
  **T 盘不可写时必须报错退出**，而不是像 `SelfPlay` 那样打条 WARN 继续跑。

### P1 · 行为克隆（基模）
- **做法**：teacher 自对弈采轨迹 → 候选打分网络监督学习（`chosen_index`）。
- **改动**：**零 Java 改动**（A 形态即可评测）。**算力**：5～20M 决策（1～4 小时采集）+ 分钟级/epoch。
- **判据**：① top-1 一致率与 KL 达到"可用的模仿"（先测基线，再定阈值，别拍脑袋）；
  ② **A 形态配对阵 vs teacher 胜率 ≥ 45%**（BC 略弱于老师是正常的；低于 40% 说明特征或动作建模有问题）。

### P2 · 对抗式数据生成（DAgger：让学生暴露自己的弱点）
- **做法**：用当前网络实际打（A 或 B 形态），把它**访问到的状态**拿 teacher 标一遍，混入数据重训；
  重复 2～3 轮。这一步专治 BC 的**分布偏移/复利误差**。
- **改动**：小改 Java —— `TraceRecorder` 在每次决策上**额外记录 teacher 的动作**（同一 obs）。
- **判据**：每轮之后对 teacher 的配对阵胜率**单调上升**；且验证集上的"学生状态分布"覆盖率上升。

### P3 · 离线强化学习（IQL / CQL）—— 首选的价值学习
- **做法**：用自对弈数据 + 回填奖励学 Q/V（不需要在线采样）。目标：让价值头替代 teacher 里的三个粗模型
  （`AVG_DEAL_POINTS=5200`、`winProbability` 的 `/steps` 折、`dealProbability` 的 0.30 上限）。
- **改动**：零 Java 改动（奖励字段已在轨迹里）。
- **判据**：① 价值头校准（预测 vs 实际收支的相关系数，按小局聚合）；
  ② 用价值头替换粗模型后**对 teacher 胜率不下降**（这是"学到的价值比粗常数更好"的必要条件）。

### P4 · 在线自对弈 RL（PPO / A2C）
- **三条保命措施（缺一不可）**：① BC/IQL 初始化；② 奖励塑形（小局收支 + 顺位点 + 和了番数，
  不能只给终局）；③ 联赛对手（见 P5），不要只跟自己最新版打。
- **改动**：**B 形态必须已落地**（Java `net` 策略 + `PolicyFactory` 每局一实例 + `debugDeterministicSeed`）。
- **判据**：Elo 相对 teacher **单调上升**且对脚本基线（`first`/`pass`/`random`）的胜率不掉；
  出现"对 teacher 变强、对 random 变弱"就是过拟合到自己的策略，必须回退。

### P5 · 自对抗进化（联赛 / PSRO-lite + 虚拟对局）
- **做法**：维护 checkpoint 池（teacher + 历史网络 + 脚本基线）；每代训**最佳响应**，
  对手按**对当前模型的胜率加权采样**（谁克我就多跟谁打）；用平均策略/噪声自对弈（NFSP 思路）
  防止 A→B→A 的循环相克。Elo 阶梯用于选种。
- **算力**：一代 ≈ 采集 20k 场（约 2.5 小时）+ 训练 20～30 分钟 → **一天 8～10 代**。
- **判据**：Elo 阶梯整体抬升且**没有策略被挤出种群**（多样性下限：任何一代的"最弱对最强"胜率 > 20%）。

### P5b · 混合 / 蒸馏（建议与 P2 并行，性价比最高）
- **做法**：`logits = student(obs) + α · teacher_prior`，或把 teacher 的 EV 门槛数值换成学出来的价值头。
- **判据**：最坏情况（α 极大）退化为 teacher 的行为（上位集合性质），
  且随训练逐步超过 teacher —— 这让"进化"有了保底，不会练废。

### P6 · 搜索增强（可选，最后再谈）
- 确定化 ISMCTS / AlphaZero-lite：需要信念采样（对手手牌 + 牌山）+ 浅搜索。
  PC 上现实的做法只有 2～8 个确定化 × 1～2 层。
- **建议**：要么砍掉，要么**只把它当"更强的标签源"**（搜索产标签 → 蒸馏回小网），
  而不是在线搜索（在线搜索会让 Java 侧前向与自对弈成本爆炸）。

---

## 5. 数据管线

- **采集**：`--selfplay <n> --workers 24 --rotate --sample <k> --out T:\mahjong-training\raw\<标签>`
  （`PROTOCOL.md` §8.4；⚠ **workers 必须显式写 24**，见 §0.1.2）。
  - **落 T 盘 + 配额闸门**（§0.1.1）：写前查剩余、超配额滚动淘汰最旧的；`compact/` 转完就删 `raw/`。
  - **采集前后各一次校验**：前 = T 盘可写探针（防"静默不落盘"），后 = 目录文件数非 0 +
    `node tools/selfplay-check.mjs <dir>`。
  - ⚠ 采集时**混入脚本基线对手**（`--policy teacher,first,random,pass`）以避免数据分布单一。

> ✅ **已修（2026-09，AUDIT S-73）**：teacher 的 48 场轨迹曾报 **13 处不一致**，逐条定位后是
> **一类真 gap + 一类校验器口径**，两条都已改掉，现在同一命令 **DATASET PASS**：
>
> | 现象 | 数量 | 定性 | 修法 |
> | --- | --- | --- | --- |
> | `legal 里有重复动作`（**全是 `pon` ×2**） | 11 | **真 gap**：碰 / 大明杠的**赤五取法没有动作键**（裸 `pon` 不带参数），`Action.enumerate` 把"用普通五碰"与"用赤五碰"折成**同一个键** → `legal` 重复、`chosen_index` 语义歧义。实测 **11/11 都发生在手里有赤五时** | 键文法扩成 `pon:<码>+<码>` / `kan:daiminkan:<码>+<码>+<码>`（与 `chi:<码>+<码>` 对齐）；`index()` 对参数化碰返回 `-1`（固定头槽 76 保留）；新增 `Action.resolve` 把"部分指定"的回包（裸 `pon`）落到**实际执行**的那一条（= legal 第一条 = 普通五优先）。`PROTOCOL.md` §8.3/§8.4/§8.5 同步，`SelfTest` +12 条断言 |
> | `最后一局的 scores_after 与终局分数不一致` | 2 | **校验器口径（假红）**：差额**恰为 `sticks × 1000` 且给了末局 1 位**（实测 g6：`2000,0,0,0`、末局 2 根棒；g37：`0,0,0,2000`）—— 那是**终局余棒归 1 位**（`RoundScoring.endGameSticks`，见 `DESIGN.md`），校验器没建模这一步 | 校验器改成**独立复算终局账**：差额之和 = 末局 `round.riichi_sticks × 1000`、只归末局 1 位（并列则 100 点为单位平分、尾数归更接近起家） |
>
> **验证**：`--selfplay 48 --workers 24 --rotate --seed 7` + `selfplay-check` → **PASS**（35242 条决策）；
> 新数据里真的出现了 `pon:5m+0m` / `pon:5p+0p` / `pon:5s+0s` —— 正是修复前**不可分辨**的那些行。
> ⚠ **旧数据集（裸 `pon` / 单码 `kan:daiminkan:<码>`）已作废**：校验器故意不放行旧键（旧轨迹重采即可）。
- **切分**：**按 seed / 小局切**训练集与验证集 —— 按决策随机切会把同一局的未来信息漏进验证集，
  指标虚高（这是最容易被忽略的泄漏）。
- **校验**：`node tools/selfplay-check.mjs <dir>`（观测字段白名单防泄漏 + `chosen ∈ legal` + 张数账 + 收支账）。
  **每次采集后跑**，它失败就别训练。
- **奖励**（事后回填，`PROTOCOL.md` §8.4）：`hand_delta`（四家收支）、`hand_winner`/`hand_loser`、
  `placement`（整场顺位，1..4 的排列）。RL 里至少用"小局收支 + 顺位点"两项塑形。
- **格式**：jsonl 只当落地格式，训练前转紧凑数组（`obs` 各段定长 + `chosen_index` + 奖励），
  memmap 随机读；`--sample 4` 起步。

---

## 6. 自对抗进化的具体形式（对应 P5）

```
种群 = {teacher(冻结) } ∪ {net-v1 … net-vN} ∪ {first, pass, random}
每一代 g：
  ① 评估：当前 best 与池中每个对手打配对对局（rotate）→ 胜率矩阵 / Elo
  ② 采样对手：按 (1 − 对当前 best 的胜率)^p 加权（**打不赢的多打**）
  ③ 采集：best vs 加权对手的轨迹（含当前 best 自身的状态分布）
  ④ 训练：BC/DAgger 标注 + IQL/PPO 更新（保底项：对整个池子的最坏情况不能退化）
  ⑤ 入库：新 checkpoint 进池（保留历史版本，别覆盖）
```

**防退化三件套**：① 对手池只增不减（保留"曾经赢过我"的版本）；② 多样性下限（见 P5 判据）；
③ 对 teacher 与脚本基线的胜率作为**回归测试**，每次入库都跑。

---

## 7. 评测口径与显著性

`σ(placement) = 1.118`（实测）时，未配对、单侧 95%、CI 半宽 = Δ 所需的**每臂座位-场数**：

| 想检出的顺位差 Δ | 每臂座位-场 | 折算场次（4 座位相关，取保守） |
| --- | --- | --- |
| 0.05 | ≈ 3.8k | ≈ 1k～4k 场 |
| 0.10 | ≈ 960 | ≈ 240～960 场 |
| 0.20 | ≈ 240 | ≈ 60～240 场 |

- **配对阵（同 seed、`--rotate`）能把方差压下来**（同一副牌山比两个策略），是省算力的关键；
  显著性用 `per_game` 做**配对**检验（符号检验 / 自助法），不要拿总胜率硬比。
- **胜率 ≠ 顺位点**：本作是顺位制，评测报告必须同时给 **avg_place / 顺位点 / 1位率 / 放铳率**。
  P0 就是为这件事存在的。
- **报告纪律**：每个结论都带"场次 + 置信区间 + seed 基准"，否则不进结论。

---

## 8. 风险与对策

| 风险 | 表现 | 对策 |
| --- | --- | --- |
| **CPU 越界（>75% 的核）** | 机器卡顿 / 违反约束 ③ | `--workers 24` **显式传**（默认 32 会越界）、`torch.set_num_threads(4)`、**生成与训练不并行**（§0.1.2） |
| **GPU 越界（>80%）** | 违反约束 ② / 影响机器上别的程序 | 占空比节流（不锁频）；显存按"自己 ≤4.5 GB"定 batch（卡上已有 ~1.7 GB 被占）(§0.1.3) |
| **数据写到 T 盘之外 / 写满 T 盘** | 违反约束 ① / 采集中断 | 唯一数据根 `T:\mahjong-training\`；`paths.py` 配额闸门 + 滚动淘汰 + 留 ≥10 GB 余量（§0.1.1） |
| **T 盘写入被拒（沙箱）** | `SelfPlay` 只打一条 WARN，采集"成功"却零文件 | 采集前写探针文件验证；受限策略下必须在放宽权限的会话里跑；⛔ 不许改存仓库目录（§0.1.1） |
| **特征偏斜** | 训练指标好、Java 里表现差 | golden obs 双侧对拍（§3.4）；归一化常数单一来源 |
| **非法动作** | 服务端拒绝 / 退回 teacher | 只实现 `ActionPolicy` + 始终掩码 `legal`；`Policies.fromAction` 兜底 |
| **不可复现** | 同 seed 轨迹不同 | `PolicyFactory` 每局一实例；`debugDeterministicSeed`；策略不许跨局带状态 |
| **作弊模型** | 评测分数离谱 | 只用 `obs` 白名单；`selfplay-check.mjs` 当必过门；绝不喂 `Round.hand[other]` / 牌山 / 里宝 |
| **奖励稀疏 / 高方差** | Elo 乱跳、学不动 | 奖励塑形 + 配对评测 + 预先算样本量（§7）；先离线 RL 再在线 RL |
| **循环相克** | A 赢 B、B 赢 C、C 赢 A | 联赛 + 平均策略（NFSP 思路）+ 多样性下限 |
| **过拟合自己** | 对 random/first 反而变弱 | 回归测试（对基线的胜率不许掉） |
| **碰 teacher** | 旧数据集作废 | teacher 冻结；只改奖励与网络（红线 3） |
| **PyTorch 装不上 / 静默 CPU** | 训练极慢 | uv + Python 3.12 + cu128 轮子；装完**必须**验证 `torch.cuda.get_device_capability` |
| **磁盘爆** | 采集中断 | `--sample 4~8` + 紧凑格式 + 滚动窗口 ≤30 GB |
| **8 GB 显存** | OOM | ≤3M 参数、batch ≤4096、bf16、梯度检查点（必要时） |

---

## 9. 计划中的交付物与目录

```
python/                        # 训练侧（本仓库内；torch 走本地 .venv，不进仓库）
├─ README.md                   # 环境准备（uv + 3.12 + cu128）、跑法、复现命令、**§0.1 纪律怎么设**
├─ pyproject.toml
├─ mahjong_ml/
│  ├─ paths.py                 # 数据根固定 T:\mahjong-training + 配额闸门 + 滚动淘汰（§0.1.1）
│  ├─ guard.py                 # 进程纪律：线程数封顶 / CPU·GPU 采样与节流 / 越界即停（§0.1.2-3）
│  ├─ features.py              # obs → 特征：**唯一规格来源**（Java 侧照它移植）
│  ├─ dataset.py               # jsonl → 紧凑数组；按 seed/小局切分；奖励回填对齐
│  ├─ nets.py                  # trunk + 候选打分头（+ value 头 / 对手模型头）
│  ├─ bc.py                    # P1 行为克隆
│  ├─ dagger.py                # P2 对抗式数据生成（学生状态 + teacher 标注）
│  ├─ offline_rl.py            # P3 IQL / CQL
│  ├─ ppo.py                   # P4 在线自对弈
│  ├─ league.py                # P5 种群 / 加权对手采样 / Elo 阶梯
│  ├─ client.py                # A 形态：NDJSON 客户端（当玩家，含 4 座自对弈）
│  └─ eval.py                  # P0 配对显著性 + 顺位点 + 置信区间
└─ tests/golden/               # golden obs + Python/Java 双侧特征对拍数据

server/.../ai/                 # B 形态（P3 之前落地）
├─ NeuralPolicy.java           # 纯 Java 前向（权重加载 + trunk + 打分头）
└─ Policies.java               # byName 增加 net:<路径>
```

**里程碑**：P0（1 天，含 §0.1 的越界自检）→ P1（2～3 天）→ P2（3～5 天）→ **B 形态**（2～3 天）→ P3（1 周）→
P4/P5（持续迭代）。每个里程碑都必须带回"命令 + 场次 + 置信区间"的证据。

---

## 10. 未决问题（记录在案，别当成漏做）

1. **P4 的算法选择**：PPO（简单、方差大）vs 离线 RL + 少量在线微调（稳、但上限受限）。
   建议先做 P3 拿到价值头，再用它决定要不要上 PPO。
2. **是否引入搜索**（P6）：收益不确定，成本明确（Java 侧前向 + 搜索会让自对弈慢一个量级）。
   倾向"搜索只做标签源"。
3. **观测版本 `v`**：如果要把 `teacher` 动作、逐张 `Danger` 等塞进 `Observation`，
   必须同步 `PROTOCOL.md` §8.2、`SelfTest.trainingInterfaceTests`、
   `tools/selfplay-check.mjs` 的 `OBS_KEYS` **三处一起改**（`AGENTS.md` §6.5）。
   本文档的默认方案是**不扩协议**：派生特征在训练侧自己算（Python/Java 同规格）。
4. **`SelfPlay` 的顺位点指标**（P0）需要改 Java 统计口径，属于"改评测不改规则" ——
   改完要跑 `--selftest` 确认既有断言不受影响。
5. ~~**`pon` 的赤五歧义 + `selfplay-check` 的假红**~~ **已解决（2026-09，AUDIT S-73）**：
   键文法扩成 `pon:<码>+<码>` / `kan:daiminkan:<码>+<码>+<码>`、新增 `Action.resolve` 做回包落位、
   校验器独立复算终局余棒 —— 详见 §5 的"已修"表。**旧数据集（裸 `pon`）作废，需重采。**
