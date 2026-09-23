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
| ① | **训练数据只许落在 T 盘** | `T:\` = 卷标 **TrainingData**（NTFS），实测 **57.61 GB 可用**；唯一数据根 **`T:\mahjong-training\`**（六个子目录已建） | 见 §0.1.1 |
| ② | **GPU 负载 ≤ 80%** | **时间平均**利用率 ≤80%（瞬时尖峰 98% 是常态，无法用软件保证）+ 显存**自己 ≤4.5 GB** | 见 §0.1.3（机制已实测：节流后稳态 46%） |
| ③ | **CPU 不超过 75% 的核** | 本机 32 逻辑核 → **所有进程加起来的活跃线程 ≤ 24**（实测 `--workers` 默认是 32，**会越界**） | 见 §0.1.2 |
| — | **一键自检** | `python\.venv\Scripts\python.exe python\verify_env.py` → 期望 `VERIFY PASS`（当前 19 通过 / 1 警告 / 0 失败） | 见 §1 末 |

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

- **判据口径 = 时间平均**（实测结论）：`utilization.gpu` 是**瞬时**值、抖动极大 ——
  同一段小网络训练里 **均值 36% / 瞬时峰值 98%**。所以"≤80%"只能按窗口均值守，
  瞬时尖峰无法用软件保证（要硬压只能限功率 `nvidia-smi -pl`，那是设备级设置，动它要慎重）。
- **机制 = 占空比节流**（**已实测有效**）：常驻一个 `nvidia-smi ... -l 1` 子进程采利用率
  （⚠ 别每步 fork 一个：spawn 一次几百毫秒、比训练一步还贵，而且只会拿到尖峰），
  控制环看**最近 3 秒的均值**、自适应插 sleep。
  实测：稳态均值 **46%（≤80% ✓）**，代价是吞吐 470 → **371 步/秒**（约 21%）。
  ⛔ **不要**用 `nvidia-smi -lgc` 锁频 —— 那是**全局设置**，会影响机器上其它程序。
- **显存**：`torch.cuda.set_per_process_memory_fraction(0.8)` 是"相对整卡"的比例，但
  **本机空闲时就已有 ~1.1 GB 被别的进程占用**（实测 `总 7.93 GB / 已占 1.14 GB`）→
  训练进程把 batch 定在"**自己 ≤ 4.5 GB**"以内（实测 0.57M 参数 / batch 4096 只用 **0.07 GB**）；
  更稳的做法是用 `torch.cuda.mem_get_info()` 动态选 batch。
- 观测：`nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv -l 5`。
- **这些已经写成代码并自检**：`python/verify_env.py`（见 §1 末）逐项验证上面的机制，
  里面就有一段**节流验证**（带节流跑 10 秒，判据"最近 3 秒均值 ≤80%"）。
  `python/mahjong_ml/paths.py`（数据根 + 配额闸门）与 `guard.py`（线程封顶 + 采样节流 + 越界即停）
  是**第一批要写的模块**，可以直接复用 `verify_env.py` 里的 `GpuMonitor` / `DutyCycle`。
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
| 对局吞吐（**§0.1 约束下的短跑值**） | **1.95～2.40 场/秒**（`--workers 24`）= 1400～1650 决策/秒 |
| 对局吞吐（**写到 T 盘的实测值**） | **1.41 场/秒**（800 场连跑、`--sample 2`→ 435 MB / 9.5 分钟）——⚠ 见下面第 1 条 |
| 轨迹体积 | 703 决策/场、**1.59 KB/决策** → **1.12 MB/场**（48 场实测 53.5 MB）→ `--sample 1` 约 **7.8 GB/小时**（写本机盘） |
| 顺位方差 | 256 个座位-场：placement 均值 2.5、**σ = 1.118** |

**三条派生结论**：

1. ⚠ **写 T 盘会把采集吞吐砍掉约四成**（实测同一台机器、同样 24 workers：
   **写工作区 2.40 场/秒 → 写 T 盘 1.41 场/秒**）。顺序大块写在 T 盘能跑到 **50.7 MB/s**，
   所以掉速不是"盘慢"，而是**每场一个文件的小文件写**（外加沙箱对工作区外写入的过滤）。
   → **采集按 1.4 场/秒 ≈ 5.1k 场/小时规划**；`--sample 2` 时约 **2.8 GB/小时**，
   而 T 盘 `raw` 配额 30 GB（§0.1.1）≈ 10 小时连采 —— 采完**立刻**转紧凑格式再删 raw。
2. **GPU 侧无压力**：0.3～3M 参数的小网络在 8 GB 上 batch 4096 绰绰有余，
   一个 epoch 过 5M 样本是秒~分钟级 —— 因此走"**多轮生成 + 多轮训练**"（DAgger / 联赛）的路线，
   而不是一次采完一个巨型数据集。
3. **样本量必须预先算**（否则"进化"全是噪声，见 §7）。

**环境准备（✅ 已实测通过，2026-09）**：RTX 5060 是 Blackwell，需要 **CUDA ≥ 12.8** 的 PyTorch 轮子；
本机系统 Python 是 3.14（没有官方 wheel）→ 用 uv 单独装 3.12。

```powershell
# ⚠ 受限沙箱下 uv 的两个默认目录都在工作区外、会被拒（实测 "Failed to initialize cache ...
#   拒绝访问 (os error 5)"）→ 必须显式指到仓库内（已在 .gitignore 里）：
$env:UV_CACHE_DIR        = 'C:\Users\HP\source\games\mahjong\.uv-cache'
$env:UV_PYTHON_INSTALL_DIR = 'C:\Users\HP\source\games\mahjong\.uv-python'

uv venv --python 3.12 python\.venv
uv pip install --python python\.venv\Scripts\python.exe torch --index-url https://download.pytorch.org/whl/cu128
uv pip install --python python\.venv\Scripts\python.exe numpy        # torch 不会自动带它，数据集要用
```

**实测结果**（`python\verify_env.py` 的原始输出）：

| 项 | 实测值 |
| --- | --- |
| Python / torch | **3.12.13**（uv 从 python-build-standalone 取）/ **torch 2.11.0+cu128**（CUDA build 12.8）/ numpy 2.5.3 |
| GPU 真的可用 | `NVIDIA GeForce RTX 5060 Laptop GPU`、**capability sm_120**、`arch_list` 含 sm_120 → **没有静默回退 CPU**；GPU 上 matmul 实算通过 |
| 显存 | 总 **7.93 GB**，别家占 1.14 GB → 留给训练 ≈ **4.50 GB**（我们的上限） |
| 训练吞吐（0.57M 参数 / batch 4096） | **≈470–490 步/秒 = ≈2.0M 样本/秒**，峰值显存 **0.07 GB** |
| GPU 利用率 | 未节流：均值 36% / 瞬时峰值 **98%**；节流后稳态均值 **46%（≤80% ✓）**，吞吐代价 ≈21% |
| CPU | 32 核 → 上限 24；`torch.set_num_threads(4)` 生效；`--workers` 默认 32 **会越界**（必须显式 24） |
| 数据盘 | `T:\`（NTFS、卷标 TrainingData、可写、六个子目录齐备）剩余 **57.61 GB** ≥ 10 GB 门槛 |

> **两条环境事实（实测踩到的）**：
> ① **`tempfile` 在本沙箱下不可用**：系统临时目录不可写，而且 `tempfile.mkdtemp` /
>    `TemporaryDirectory` 建出来的目录**自己也写不进去**（在里面再 `mkdir` → `WinError 5`），
>    手写 `Path.mkdir(parents=True)` 正常 → Python 侧的 scratch 一律放工作区内（`python/.tmp/`，已 gitignore）。
> ② **numpy 要单独装**：`uv pip install torch` 不会带它，而数据集/特征/评测都要用（`eval.py` 就依赖它）。

> **一条命令重跑全部检查**：`python\.venv\Scripts\python.exe python\verify_env.py`
> （判据 PASS/WARN/FAIL + 末尾 `VERIFY PASS`；**只有真问题才 FAIL**，
> 受限沙箱下"写 T 盘被拒"是 WARN —— 那正是采集**静默不落盘**的那条闸门）。

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
| ✅ 纯 Java 前向（权重加载 + trunk + 打分头） | `ai/NeuralPolicy.java`（**已落地**） | `SelfTest.neuralForwardTests`：与 Python 的 golden 输出**逐元素**对拍（状态/候选 <1e-4、logits <1e-4、argmax 一致）+ 红证（输出偏置 +1 → 每条 logit 恰好 +1） |
| ✅ **特征抽取**（obs → 输入向量，与 Python 同一规格） | `ai/Features.java`（基础 539/88）+ `ai/ObsFeatures.java`（派生 68/8） | 同上的 golden 夹具（含 `hand`/`visible` 是 `int[]` 还是 `List` 这类"两条通路不同形态"的坑） |
| ✅ `--policy net:<权重文件>` | `ai/Policies.java` 的 `byName` | 构造期校验魔数/格式/**特征维度**；运行期仍过 `fromAction` 三道保护 |
| ✅ DAgger 标注字段（`teacher` / `teacher_index`） | `train/TraceRecorder.java` + `SelfPlay --teacher-label` | `PROTOCOL.md` §8.4 已写；`selfplay-check.mjs` 字段白名单与合法性校验同步（2026-09） |
| ✅ 顺位点统计（马点+头名赏） | `train/SelfPlay.java` | 与 `RoundScoring.settle` 对拍 |

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
| **派生特征**（**已落地** = 特征 v2） | `mahjong/ai/ObsFeatures.java`：逐张危险度 `danger_worst[34]` + `danger_riichi[34]`（68 维 → 进**状态**）；逐候选 `[shanten, advance_types, advance_tiles, wait_types, wait_tiles, good_wait_types, good_wait_tiles, dora_count]`（8 维 → 进**候选**）。**由 Java 算、Python 只读**（`--features` 写 sidecar），两侧靠 `SelfTest.obsFeaturesTests` 的 golden 对拍钉住（obs 通路 == Round 通路，带红证） |
| （可选，未做） | teacher 在同一 obs 上的**动作 one-hot**、四家逐张危险度的**理由码**（`genbutsu`/`suji`/…）、鸣后"有没有役计划" |

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

### P0 · 评测口径（先做，半天～1 天）—— ✅ **已完成（2026-09）**
- **做法**：`--rotate` + 配对同种子；从 `summary.json` 的 `per_game` 做自助法置信区间；
  **补上 `SelfPlay` 的顺位点指标**（马点 30/10/−10/−30 + 头名赏 20，同点按座次）——
  `NOTES.md` §10.4 与 `DESIGN.md`「档 C 的收尾」点名过这个缺口。
- **已落地的三块**：
  1. 服务端 `SelfPlay`：`by_policy.avg_rank_points` + `per_game[].rank_points`，公式**复用生产的
     `RoundScoring.settle`**（不另写一份）；`PROTOCOL.md` §8.4 同步；`tools/selfplay-check.mjs`
     **独立复核**两条不变式（`Σ rank_points == 0`、最高顺位点恰是末局 1 位）+ 与 `by_policy` 的均值对账。
  2. `python/mahjong_ml/eval.py`：顺位点/顺位两个指标、自助法 95%CI（**固定种子可复现**）、
     **符号检验**精确 p 值、按实测 `sd(Δ)` 反推"还要打多少场"；两种配对：
     **组内**（同一 run 里不同策略，同一副牌山）与**跨 run 按座位**（比较两个不同策略的正解，
     `--labels net,teacher`）。
  3. `python/mahjong_ml/paths.py`（数据根 + 配额闸门）与 `guard.py`（线程封顶 + GPU 采样节流）。
- **实测证据**：
  - `--selfplay 32 --workers 24 --rotate --seed 1 --policy teacher,teacher,first,random` →
    teacher **+44.52**【+40.0,+49.0】/ first −35.37 / random −53.67（顺位点）；
    组内配对 teacher vs first Δ=**+79.88** p<0.001（31 胜 1 负）、teacher vs random Δ=+98.19 p<0.001。
  - 跨 run 按座位（同 `--seed 777`，`ab-first` vs `ab-random`，`--labels first,random`）：
    n=48、Δ=**+3.45**、95%CI=[−5.60,+12.55]、**p=0.471** —— **正确地判为"证不出差别"**
    （这正是 P0 存在的意义：不配对会把这个 +3.45 当成"变强了"）。
  - `python/selfcheck.py`（Python 侧自检）**28 项全过**；`--selftest` 全绿（见 §4 末的验证表）。
- **环境纪律判据（§0.1）**：`paths.py` 的配额闸门能被触发（故意把 `raw` 配额调小 → 滚动淘汰最旧的，
  自检里有这条）；`guard.py` 把训练线程封在 4、采集 `--workers` 封在 24；
  **T 盘不可写时 `paths.ensure_root()` 抛 `DataRootError`**（自检覆盖），不像 `SelfPlay` 那样打条 WARN 继续跑。

### P1 · 行为克隆（基模）—— ✅ **冒烟已跑通（2026-09）**
- **做法**：teacher 自对弈采轨迹 → 候选打分网络监督学习（`chosen_index`）。
- **已落地的四块**（都在 `python/`）：
  `features.py`（539 维状态 + 88 维候选，**唯一规格来源**；`python -m mahjong_ml.features` 打印分段偏移，
  Java 侧照它移植）· `dataset.py`（jsonl → 紧凑数组：**按整场切分**、候选存 uint8、内存映射落盘）·
  `nets.py`（候选打分头 + `export_weights` 给纯 Java 前向用）·
  `bc.py`（训练循环自带封线程 / GPU 节流 / checkpoint 落 T 盘）。
- **实测**（800 场 teacher 自对弈、`--sample 2` → 25 万训练条 / 2.9 万验证条、80 场留出；
  0.25M 参数、60 epoch = 3660 步、165.9 秒）：

| epoch | train top-1 | val top-1 | val 类型准确率 | val nll |
| --- | --- | --- | --- | --- |
| 1 | 0.412 | 0.457 | 0.977 | 1.632 |
| 10 | 0.566 | 0.572 | 0.977 | 1.382 |
| 20 | 0.638 | 0.612 | 0.977 | 1.247 |
| 30 | 0.676 | 0.615 | 0.977 | **1.240** |
| 40 | 0.707 | **0.616** | 0.977 | 1.265 |
| 60 | 0.748 | 0.604 | 0.974 | 1.375 |

- **同粒度基线**（这次特意把粒度写清楚，免得自己骗自己）：随机 **0.116**、
  **总是选第一个合法动作 0.156**、多数**动作类型** 0.755（类型粒度）；
  而模型的**类型准确率 0.977** —— 所以 0.616 的"精确动作一致率"是**真的学到了**，不是基线白送。
- **过拟合迹象**：ep40 之后 train 继续升（0.707 → 0.748）而 val 反而降（0.616 → 0.604）
  —— 250k 条 / 0.25M 参数，下一步该加数据或正则（dropout / weight decay / early stop）。
- **可复现（硬要求）**：同 `--seed` 跑两次 → 每 epoch 的 `train_loss`/`val_top1` **逐位相同**，
  且 `model.pt` 的 **sha256 相同**（连权重都一样，不只是指标接近）。
- ⚠ **它离 teacher 还有多远、为什么**：这一版特征**只吃 `obs` 的原始字段**，**没有派生量** ——
  而 teacher 正是靠 `HandEval`（向听 / 进张枚数 / 听牌形）与 `Danger`（逐张危险度）决策的。
  不给这些，网络得从 34 维计数里自己"悟"出向听，样本效率当然差。**这就是下一个增量**（见下）。

#### P1 增量：派生特征到位后的对比（✅ 2026-09，特征 v2）

派生量**由 Java 的权威实现算**（新增 `mahjong/ai/ObsFeatures.java`：逐张危险度 68 维 +
逐候选 8 个量，直接复用 `HandEval` / `Danger`），离线用 `--features` 写成二进制 sidecar
（`g*.feat.bin`，**轨迹格式不变**），Python **只读、绝不重算**。特征 **539+88 → 607+96**。

| epoch | v1 val top-1（无派生） | **v2 val top-1（带派生）** | v2 类型准确率 | v2 nll |
| --- | --- | --- | --- | --- |
| 1 | 0.457 | 0.480 | 0.972 | 1.565 |
| 5 | 0.474 | 0.557 | 0.979 | 1.260 |
| 10 | 0.572 | 0.687 | 0.983 | 0.907 |
| 20 | 0.612 | 0.752 | 0.984 | 0.701 |
| 30 | 0.615 | 0.774 | 0.983 | 0.641 |
| 40 | 0.616 | 0.782 | 0.982 | **0.632** |
| 60 | 0.604 | **0.795** | 0.982 | 0.667 |

- **峰值 0.619（@ep32）→ 0.795（@ep60）：+0.176 绝对**（相对 +28%）；nll 1.24 → 0.63。
- 同粒度基线不变：随机 **0.116**、总是选第一个合法动作 **0.156**、多数**动作类型** 0.755。
- **唯一变量就是特征**：同 800 场、同切分（同 `--split-seed`）、同 seed、同 hidden/head/batch/lr。
- 采集侧代价：富化 279,518 条决策 **6.6 分钟**（24 workers），sidecar 共 **55 MB**（轨迹 JSONL 的 ~13%）。
- ep60 仍在涨（nll 从 ep40 起略回升 = 轻度过拟合）→ 下一步可选：加数据 / 正则，
  或继续接更多派生量，或直接进 **P2 DAgger**（让学生把自己的状态分布暴露出来）。

### P2 · 对抗式数据生成（DAgger）—— ✅ **一轮已跑通，且净效应为负（2026-09）**
- **做法**：用当前网络实际打（B 形态：`net:<net.bin>` 纯 Java 前向），把它**访问到的状态**拿 teacher 标一遍，
  与 BC 数据聚合成混合集重训；重复 2～3 轮。这一步专治 BC 的**分布偏移 / 复利误差**。
- **改动（已完成）**：
  - Java：`TraceRecorder` 对**学生座位**的每条决策**额外问一次 teacher**（同一 obs），写 `teacher` /
    `teacher_index`（`--teacher-label` 打开；teacher 座位跳过 —— 它的 `chosen` 本身就是老师动作）；
  - 校验：`selfplay-check.mjs` 校验 `teacher ∈ legal` 且 `teacher_index == legal.indexOf(teacher)`；
  - Python：`dataset.py` 多来源合并 + `--label-source auto|chosen|teacher`（`auto` = 有标注用标注）。
- **一条命令跑完（编排器）**：

  ```powershell
  python -m mahjong_ml.dagger --student T:\mahjong-training\ckpt\bc-002 `
      --bc-src T:\mahjong-training\raw\bc-001 --bc-compact T:\mahjong-training\compact\bc-001 `
      --label bc-003 --round 1 --games 300 --workers 24 --epochs 60
  # 采集 → --features → selfplay-check → dagger 集 + 混合集 → 重训 → 对比 → ckpt\bc-003\dagger-r1.json
  ```

- **判据（本轮定为纪律）**：对比必须落在**学生自己的状态**上，基线与新模型评**同一批行**；
  显著性用**按场聚类**的配对 bootstrap —— 同场决策高度相关，逐行 bootstrap 会把 CI 缩窄到假显著
  （`selfcheck.py` 有一条红证：同一份数据逐行 lo>0、按场 lo=0）。
- **⚠ 必须带"同数据量的 BC-only 对照臂"**（`--control-games N`）：只比"基线 vs 新模型"会把
  **数据量**与**数据分布**两个因素混在一起。第一轮就撞上了：新模型在**学生状态**上 +0.071、
  在**老师状态**上也 +0.064 —— 两者几乎一样，说明涨幅主要来自"数据多了 63%"，
  **不能**把这一轮的增益记在 DAgger 头上。`--control-games` 采 N 场纯 teacher 轨迹、训一个同量
  BC-only 模型，`对照 → 新模型` 那一对差值才是 DAgger 的**净效应**。
- **⚠ 这不是"变强"的证据**：它只是"更贴近老师"。强度只能由 §7 的实战顺位检验（`eval.py`）给出。
- **第一轮实测（2026-09，round 1）** —— 学生 `bc-002`（v2，27.9 万条）→ `bc-003`（混合集 44.2 万条）：

  | 量 | 值 |
  | --- | --- |
  | 采集（300 场，学生坐 2 席、逐场轮转） | 3424 小局 / 389 s = **0.77 场/秒**（纯 teacher 1.41 场/秒 → **多付 1.8×**） |
  | 该批轨迹的顺位点（600 席场/家，仅供健全性检查） | 学生 **−0.8**、teacher **+0.8**（差 0.054 顺位，σ≈0.046 → 噪声内） |
  | `--features` 富化 | 208,909 条决策 / 1,800,891 行候选 / 262 s / sidecar 41 MB |
  | `selfplay-check` | **DATASET PASS**（含 `teacher ∈ legal`、`teacher_index` 一致性） |
  | 混合集 | 训 441,981 条（990 场）/ 验 46,446 条；其中**老师标注 96,415 条** |
  | 训练 | 60 epoch / **419.6 s**；mix val top-1 **0.861**（v2 基线在同口径下 0.795） |
  | 评测集（学生状态，无泄漏） | 16,092 条 / 24 场（8,041 条带老师标注） |
  | 评测集（老师状态，无泄漏） | 4,730 条 / 14 场 |
  | **学生状态一致率** | 基线 0.7893 → 新 **0.8602**，Δ **+0.0709**（聚类 CI +0.0638..+0.0776） |
  | 老师状态一致率 | 基线 0.7924 → 新 0.8564，Δ **+0.0641**（聚类 CI +0.0562..+0.0725） |

  **读法（重要）**：两个分布的涨幅几乎一样（+0.071 vs +0.064）→ 这轮增益的主因是**数据量 +63%**，
  **不是** DAgger 的分布纠正。所以必须上对照臂。
  ⚠ 另外：基线在学生状态上只掉了 **0.003**（0.795 → 0.789）—— 说明**这个学生的状态分布本来就很贴近老师**，
  DAgger 赖以成立的前提（大的协变量偏移）在本例里**很弱**，这也预示净效应不会大。

- **三臂对照（决定性的一组数，2026-09 round 1）** —— 三臂同超参、同 seed、同评测集（学生状态 16,092 条 /
  24 场，老师状态 4,730 条 / 14 场，**都无泄漏**）：

  | 臂 | 训练数据（**训练行数**） | 学生状态 top-1 | 老师状态 top-1 |
  | --- | --- | --- | --- |
  | 基线 `bc-002` | BC **250,000** 行（紧凑集建时 `--max-decisions 250000` 截断） | 0.7893 | 0.7924 |
  | DAgger `bc-003` | BC + 学生轨迹 → **441,981** 行（含 96,415 条老师标注） | 0.8602 | 0.8564 |
  | **配量对照 `bc-005`** | BC + 纯 teacher 新数据 → **441,981** 行（`--control-max-decisions` 裁到同量） | **0.8653** | **0.8607** |
  | 未配量对照 `bc-004` | BC + 纯 teacher → 478,041 行（多 8.2%） | 0.8676 | 0.8691 |

  | 配对差 | 学生状态 | 老师状态 |
  | --- | --- | --- |
  | dagger − baseline | +0.0709（+0.0638..+0.0776） | +0.0641（+0.0562..+0.0725） |
  | **dagger − control（严格同量）** | **−0.0051（−0.0127..+0.0019，P(涨)=0.086）** | **−0.0042（−0.0130..+0.0043，P(涨)=0.155）** |
  | dagger − control（未配量，仅参考） | −0.0074（−0.0130..−0.0017） | −0.0127（−0.0222..−0.0032） |

  **P2 的裁定（写进计划）**：**DAgger 的学生状态数据没有可测的增益** —— 严格同量下配对差
  **−0.0051、95% CI 跨 0**，点估计略偏 BC-only（用学生数据不如拿同量的老师数据）。
  ⚠ 口径必须写清：未配量那一版的 −0.0074 **不能**当成"DAgger 有害"的证据，因为那对照臂还多训了
  **8.2%** 的行；裁到同量后差距减到 −0.0051 且不再显著（约 1/3 来自数据量）。
  与"学生状态分布本来就贴近老师（基线在学生状态上只掉 0.003）"一致 —— DAgger 的前提（大协变量偏移）
  在本例里不成立：**数据量才是主因**（BC-only 250k → 0.789、442k → 0.865、478k → 0.868）。
  → **不再迭代 DAgger 轮次**；要 DAgger 有意义，得先有一个**明显更弱/更偏**的学生
  （训练不足或输入受限），否则优先加数据（纯 teacher 轨迹最便宜）、加容量/正则，或直接上 P5b。
  ⚠ 这仍是**离线**证据：胜负只能由 §7 的实战顺位检验给出；上述结论只说明"更像老师"这件事上
  DAgger 没有优势。

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

### P5b · 混合 / 蒸馏 —— ✅ **混合已落地（2026-09）**
- **做法**：`logits = student(obs) + α · teacher_prior`（先验 = 老师在同一信息集的动作）。
- **已落地**：策略串 `net:<权重文件>@<α>`（`PROTOCOL.md` §8.4），α 缺省 0 = 纯网络（与旧行为逐决策相同）；
  实现是 **`Policy` 级组合**（`Policies.hybrid`，与 `TEACHER` 同级）—— 先验要调 `Bot.decide(Round, …)`，
  而 `ActionPolicy` 是故意拿不到 `Round` 的（反作弊口径），网络本身仍只看 `Observation`。
- **判据①（可证）**：α 极大 ⇒ argmax 必是老师那条 ⇒ **`hybrid(@∞) ≡ teacher`**。
  `SelfTest.hybridPolicyTests` 在**整局决策序列 + 终局分数**上断言这一点，并配一条非空转对照
  （纯网在同一探针上与 teacher 差 323/333 处 —— 否则"两边都等于 teacher"什么也证明不了）。
- **判据②（α 怎么选）**：α 与**训练过的网**的 logit 尺度绑定，**别照抄常数**——
  同一套代码在**未训练**的 golden 小网上 α=0.25 就 100% 让位。用
  `python -m mahjong_ml.hybrid` 量"让位曲线"：`margin` = (除老师那条外的最大 logit) − 老师那条的 logit，
  `margin < α` ⇔ 该行被老师接管。**bc-003 在 `dagger-r1[val]`（n=16,092）上**：
  margin 中位 **−4.08** / p90 **0.63**；让位 α=0.25 → **87.8%**、α=1 → 91.7%、α=2 → 94.8%、
  α=4 → **97.7%**、α=32 → 100%（推荐 α=4 @ 让位 95%）。
  ⚠ 这条曲线**只说明"让位多少"，不说明"更强"**：数据集里的真值就是老师，让位即"对"。
- **判据③（强度）**：只能靠同牌山配对实战（`mahjong_ml.eval`）—— 见下表。
- **判据④：配对实战（2026-09 round 1）** —— 2400 场同牌山配对（`--rotate`、两臂各 2 席、
  `seed 777001`、`--sample 64`；**54 分 02 秒 ≈ 0.74 场/秒**；整批过 `selfplay-check` = DATASET PASS）：

  | 配对 | Δ 顺位点 | 95% CI | p | 胜/负/平 | sd(Δ) | 检出 Δ=2 需要 |
  | --- | --- | --- | --- | --- | --- | --- |
  | **`hybrid@1` − 纯网**（2400 场） | **+1.95** | **[−0.21, +4.10]** | 0.045 | 1248 / 1149 / 3 | 53.11 | ~2709 场 |
  | **`hybrid@1` − `teacher`**（1200 场） | +1.20 | [−1.76, +4.12] | **0.977** | 600 / 598 / 2 | 52.31 | ~2628 场 |
  | （参照）纯网 − `teacher`（300 场，更早一轮） | −1.61 | [−7.65, +4.50] | 0.817 | 147 / 152 / 1 | 54.53 | ~2856 场 |

  同场汇总（臂 A：2400 场 = 每臂 4800 席场）：`hybrid@1` 平均顺位 **2.479** / 顺位点 **+0.97** /
  和了 22.5% / 放铳 16.1%；纯网 **2.521** / **−0.97** / 22.4% / 16.4%。
  （臂 B：`hybrid@1` 2.482 / **+0.60** vs `teacher` 2.518 / **−0.60**，和了 22.4%/22.6%、
  放铳 16.7%/16.0% —— 胜/负 600:598，**完全打平**。）

- **裁定（round 1）**：三条比较的**点估计都在 ±2 顺位点内、CI 全部跨 0** —— 用现有样本
  **分不出** hybrid / 纯网 / teacher 三者的强弱。唯一像信号的是对纯网的 **+1.95（p=0.045）**，
  但它正好卡在 0.05 上、且 `eval.py` 自己算出的"检出 Δ=2 约需 2709 场"说明我们**就在分辨率边缘**；
  对 teacher 干脆是完全打平（p=0.977，胜 600 负 598）。
  → 可下的结论只有两条：① **混合机制正确且可证安全**（上位集合性质 + 2400 场零非法动作 + 整批
  DATASET PASS）；② **它的强度效应尚未确立**（要钉住 +2 还需再跑 ~2700 场，合并 n≈5100 把 CI 压到 ±1.5）。
  ⚠ 而且从机制上就能预判：α=1 时 **91.7% 的决策本来就听老师**，所以这一臂的天花板是"老师"，
  它测的是"学生那 5.7% 的自主决策是不是亏"，**不是**"混合产生了新能力"。
- **去向**：① 不再在"推理期把老师请回来"上继续调 α（天花板 = 老师，而天花板已经够到了）；
  ② 把混合**留作未来在线 RL 的保底**（α 取大 ⇒ 逐决策 ≡ teacher，可证不会更差）；
  ③ 真想超过老师只能靠**价值/胜负信号** → 回到 **P3（IQL/CQL）**；
  ④ "蒸馏"那一半（把"老师选了哪个候选"做成输入特征、让学生学会**选择性**听）留到 P3 之后再评估
  ——它是特征版本 +1 的改动，现在的证据不足以支持。
- **⚠ 与 DAgger 同一个陷阱**：学生状态分布的偏移本来就很小（基线在学生状态上只掉 0.003），
  所以"老师接管一部分决策"能换来多少强度，本身就该是**小效应**；要检出动它得几千场（见 §7）。
- **还没做的一半（蒸馏）**：把"老师选了哪个候选"做成**输入特征**再重训（让学生学会**选择性地**听老师）。
  那是特征规格改动（+1 维 → 特征版本 +1、golden 夹具重导），**先看混合实测有没有信号再决定**。

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
- **派生特征富化**：`--features <轨迹目录> [--workers 24]` → 每个 `g*.jsonl` 生成同名
  `g*.feat.bin`（**轨迹契约不变**）。派生量由 `mahjong/ai/ObsFeatures.java` 用**权威实现**
  （`HandEval` / `Danger`）算，Python 只读 —— 两侧由 `SelfTest.obsFeaturesTests`
  （obs 通路 == Round 通路，逐元素 + 红证）与 `python/selfcheck.py`（格式契约、缺 sidecar 报错）钉住。
  实测：279,518 条决策 **6.6 分钟**（24 workers）、sidecar **55 MB**（轨迹的 ~13%）。
- **紧凑化**：`python -m mahjong_ml.dataset build <轨迹目录> <紧凑目录>` —— 特征在这里算一次，
  **按整场**切训练/验证（`--val-frac` / `--split-seed`），`state` 存 float16、`cand` 存 uint8
  （派生候选量存**原始整数**，归一化只在 `bc._batch` 一处做），**缺 sidecar 直接报错**（不悄悄填 0）。

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
- **DAgger 轮（P2）**：采集时 `--teacher-label`（学生座位额外记老师动作）→ `dataset build` 的
  `--label-source auto` 自动优先用老师标注。**两个紧凑集**是刻意分开的：
  - `compact/dagger-rN`（只有学生轨迹）：**对比用** —— 它的 val 切分就是"学生的状态分布"，
    基线与新模型都评它，才能回答"这一轮到底有没有更贴近老师"；
  - `compact/mix-rN`（BC + 学生轨迹）：**训练用** —— 保留 BC 的覆盖，避免只用学生分布把老师的能力丢掉。
  老师标注覆盖率记在 meta（`teacher_labeled_train|val`）里，报告里要写出来（没覆盖到 = 结论无效）。
- **校验**：`node tools/selfplay-check.mjs <dir>`（观测字段白名单防泄漏 + `chosen ∈ legal` + 张数账 + 收支账
  + `teacher ∈ legal` / `teacher_index` 一致性）。**每次采集后跑**，它失败就别训练。
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
- **顺位点（rank_points）的尺子**（实测 `sd(Δ) ≈ 54.5`，来自 300 场 `net vs teacher` 的配对差）：
  95% 置信 + 80% 功效下，要检出 Δ 顺位点约需
  `n ≈ (2.8 × 54.5 / Δ)²` 场 —— **Δ=2 → ~5.8k 场、Δ=3 → ~2.6k 场、Δ=5 → ~0.9k 场**。
  ⚠ 所以"P4/P5 里的一次对比"动辄是**几千场**：按 0.7 场/秒（24 workers、写轨迹）算，
  2.6k 场 ≈ **1 小时**。**做评测跑不需要全量轨迹** —— 加 `--sample 64`（~11 行/场）
  只影响落盘行数、不影响 outcome 与 `summary.json`，实测同样 0.7 场/秒（瓶颈是每文件开销，不是字节数）。
- **离线指标（对老师的一致率）也要按场聚类**：`python -m mahjong_ml.bc eval --data <紧凑集> --ckpt <模型>`
  给的是逐行口径；DAgger 那种"新旧模型比同一批学生状态"的场景走 `mahjong_ml.dagger.compare_on`，
  它用**按场聚类**的配对 bootstrap —— 一局里的决策高度相关，逐行 CI 会把 ±0.02 的事说成显著
  （红证在 `selfcheck.py`）。**离线一致率只说明"更像老师"，不说明"更强"。**
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
├─ verify_env.py               # ★ 环境自检（✅ 已就绪）：GPU/torch/numpy、显存预算、训练吞吐、
│                              #   GPU 利用率与**节流验证**、CPU 线程封顶、采集器与 T 盘闸门
├─ selfcheck.py                # ★ Python 侧自检（✅ 126 项）：统计/配对/纪律/特征/数据集/网络/BC/DAgger/混合
├─ inspect_trace.py            # 看一条真实轨迹的字段类型（核对 features.py 的 schema 假设）
├─ pyproject.toml              # ✅ 依赖 + PyTorch cu128 索引（uv sync 可复现环境）
├─ mahjong_ml/
│  ├─ paths.py                 # ✅ 数据根固定 T:\mahjong-training + 配额闸门 + 滚动淘汰（§0.1.1）
│  ├─ guard.py                 # ✅ 进程纪律：线程数封顶 / CPU·GPU 采样与节流 / 越界即停（§0.1.2-3）
│  ├─ eval.py                  # ✅ P0 配对显著性 + 顺位点 + 置信区间
│  ├─ features.py              # ✅ obs → 特征：**唯一规格来源**（607 状态 + 96 候选）
│  ├─ dataset.py               # ✅ jsonl + sidecar → 紧凑数组；**按整场**切分；内存映射落盘
│  ├─ nets.py                  # ✅ trunk + 候选打分头
│  ├─ bc.py                    # ✅ P1 行为克隆（封线程 + GPU 节流 + checkpoint 落 T 盘；含 `eval` 子命令）
│  ├─ export.py                # ✅ 导出纯 Java 可读的二进制权重 + golden 夹具
│  ├─ dagger.py                # ✅ P2 一轮编排：采集→富化→校验→受控建集→重训→**多臂**（基线/DAgger/配量对照）+ 按场聚类对比
│  ├─ hybrid.py                # ✅ P5b 的 α 选择：离线“让位曲线”（margin 分布 → 让位比例 → 推荐 α）
│  ├─ offline_rl.py            # P3 IQL / CQL
│  ├─ ppo.py                   # P4 在线自对弈
│  ├─ league.py                # P5 种群 / 加权对手采样 / Elo 阶梯
│  ├─ client.py                # A 形态：NDJSON 客户端（当玩家，含 4 座自对弈）
└─ tests/golden/               # ✅ forward.bin：Python↔Java 的**前向 golden 夹具**（SelfTest 读它）

server/.../ai/                 # B 形态（✅ 已落地）
├─ ObsFeatures.java            # ✅ 派生特征（obs → 68 维危险度 + 8 维逐候选）—— 权威实现，golden 对拍
├─ Features.java               # ✅ 基础段拼装（539/88，与 features.py 同规格）
├─ NeuralPolicy.java           # ✅ 纯 Java 前向（加载权重 + trunk + 打分头 + 掩码 argmax）
└─ Policies.java               # ✅ byName 接 net:<权重文件>
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
