# 训练端 C++ 自对弈引擎（`trainer/`）—— 设计与对拍契约

> **目标**：把训练回路（数据生成）里原本由服务端 Java 承担的那部分算法，用现代 C++ 重写成一个
> **独立的**数据生成器，把数据速率拉上去。
> **红线**：**发布版服务端完全不变** —— `server/` 的 jar、协议、房间行为一个字都不动；
> 训练侧要换，就换"谁在采数据"，不换"线上跑什么"。

状态：**M0（牌山/洗牌）与 M1（向听/进张/听牌形）已落地**，均与 Java 逐字节/逐字段对拍通过；
**M2 进行中**（打点内核已完成：役种/符数/点数/授受）；其余见 §5。

---

## 1. 为什么值得做：瓶颈是**一个函数**

JFR（`-XX:StartFlightRecording=…,settings=profile`，JDK 21）跑 40 场 teacher 自对弈（单 worker，
362 决策/场，5912 个 `jdk.ExecutionSample`），**按顶层帧**统计：

| 占比 | 次数 | 顶层帧 |
| --- | --- | --- |
| **99.0%** | 5851 | **`mahjong.rules.Shanten.dfs(int[],int,int,int,int,int[])`** |
| 0.3% | 17 | `mahjong.rules.Shanten.standard(int[],int)` |
| 0.1% | 8 | `mahjong.rules.Agari.waits(int[],int)` |
| 0.1% | 3 | `mahjong.game.Round.play()` |
| 其余 | ≤2 each | `HandEval.of` / `Danger.*` / `Bot.*` / `Round.canRiichi*` … |

也就是说：**训练端的 CPU 瓶颈不是"引擎太大"，而是"向听 DFS 被调用得太多"**。
它被两条路径同时压：

- **teacher**（内置牌效机器人）：每个候选打牌都要看向听 / 进张 / 听牌；
- **特征工程**（`ObsFeatures.perCandidate` → `HandEval.of`）：一次决策要对 34 种进张**各跑一次**
  `Shanten.min`（实测 ≈0.5～0.8 ms/候选，一次决策 5～8 ms，占整条通路九成，见
  `docs/TRAINING.md` §3.3）。

推论（决定了本工程的路线）：
1. **先把"向听/进张"这条路径做快**，收益最大 —— 换语言只是其中一半，
   **换算法**（DFS → 查表/位棋盘）才是数量级的那一半；
2. 引擎其余部分（`Round` 流程、`Evaluator`、`Payments`、`Danger`、网络前向）加起来不到 1%，
   但它们决定**数据是否可信**（规则必须一模一样），所以要**按对拍契约逐步搬**，不能图快乱写；
3. 对拍契约不能是"看起来一样"，只能是**同种子 → 同轨迹（逐字节）**。

---

## 2. 与 Java 的关系：三条铁律

| # | 铁律 | 判据 |
| --- | --- | --- |
| 1 | **发布版服务端不变** | 本工程只新增 `trainer/` 与 `tools/` 里的探针/对拍脚本；`server/` 的源码只在**读**的方向被引用（探针 import 它的 jar），不改一行、不重打包 |
| 2 | **同种子 → 同轨迹** | 同一个 `seedBase` / 同一串策略名 → C++ 产出的 `g*.jsonl` 与 Java 产出的**逐字节相同**（先逐行逐字段，最后整文件 sha256） |
| 3 | **口径的唯一权威仍是 Java** | 规则/特征/编码有歧义时以 Java 实现为准；C++ 侧任何"更聪明"的做法都必须**先证明产出等价**（差分测试）再启用 |

> 为什么这么严：数据集是**被消费的契约**（`AGENTS.md` §6.5）。规则差一点（少算一种振听、
> 符数取整不同、洗牌差一张），训练数据就悄悄换了分布 —— 而且**不会报错**，
> 只会在几百万条样本之后表现为"模型变差了"。这个项目已经踩过同类坑（数据盘掉线期间
> "顺手重跑同名 run"，半截目录静默混进结果，见 NOTES §6.5）。

---

## 3. 复用现有的验证设施（不另造一套）

对拍**不新增判据**，全部复用已经存在的三层检查，只是把"Java 产出"换成"C++ 产出"：

| 设施 | 用来验什么 |
| --- | --- |
| `tools/selfplay-check.mjs <dir>` | 轨迹格式/观测白名单/动作键文法/张数账/点数守恒（**独立实现**，与 Java 无关） |
| `python/selfcheck.py` + `dataset.py` | 紧凑集契约（列、切分、`max_legal`、teacher 标注） |
| `SelfTest`（L1，1370 项） | 规则语义的**权威口径**：C++ 侧遇到不一致时，用它判谁对 |
| **新增** `tools/trainer-parity-check.mjs` | Java 与 C++ 的**差分对拍**（本工程的核心判据） |
| **新增** `tools/WallProbe.java` | Java 侧牌山/配牌的**只读探针**（`Wall.debugAllTiles()`），供差分对拍取真值 |
| **新增** `tools/RuleProbe.java` | Java 侧向听/进张/听牌形的**只读探针**（`Shanten.min` / `HandEval.of` / `afterDiscard`） |
| **新增** `tools/trainer-rule-parity.mjs` | 上一条的对拍驱动器（确定性语料 → 两边逐行逐字段比对，含数组哈希） |
| **新增** `tools/ScoreProbe.java` | Java 侧打点**只读探针**（`Rules.applyPreset` + `Evaluator.evaluate` + `Payments.compute`） |
| **新增** `tools/trainer-score-parity.mjs` | 打点对拍驱动器（19 万和了手 + 上下文 → 役种/符/点数/授受逐字段比对） |
| **新增** `tools/SettleProbe.java` | Java 侧**精算/连庄判据/种子链/鸣牌仲裁判据**只读探针（`RoundScoring.*` / `RoundClaims.*` / `SelfPlay.seedFor`） |
| **新增** `tools/trainer-settle-parity.mjs` | 上一条的对拍驱动器（顺位点按**位模式**比对，见 §6.3） |
| **新增** `tools/ActionProbe.java` | Java 侧**动作空间**只读探针（`Action` 的键/下标/回包/落位） |
| **新增** `tools/trainer-action-parity.mjs` | 上一条的对拍驱动器（含全部 37 个牌槽与一批**非法键**，见 §6.4） |

---

## 4. M0：牌山与配牌（已完成）

**为什么从这里开始**：牌山是整条链的输入端，也是"错了不报错"的典型（`Wall.java` 的注释原话）。
它依赖两个必须逐位复刻的东西：

1. **`java.util.Random`**（48 位 LCG + `nextInt(bound)` 的取模拒绝分支）；
2. **`Collections.shuffle`** 的 `RandomAccess` 分支：`for (i = size; i > 1; i--) swap(i-1, nextInt(i))`。

实现与判据：

- `trainer/src/java_rand.hpp` —— `JavaRandom`（`setSeed/next/nextInt` 逐分支等价）+ `javaShuffle`；
- `trainer/src/wall.{hpp,cpp}` —— `Wall(seed, aka)`：洗 0..135 → `stripRedFives`（`aka == 0` 时
  把赤五写成普通五）→ 切 `dead[122..135]`；`deal/finishDealing/draw/drawRinshan/onKan/revealDora`；
- 配牌顺序与 `Round.setup()` 一致：**3 轮 × 4 家 × 一次抓 4 张（共 12）** → 每人补 1 张（13）
  → 庄家第 14 张（`openingTile`）→ `finishDealing`；
  ⚠ **2026-09 修正**：原来是"13 巡 × 4 家 × 1 张"，**是错的**（见 §6.0 的契约细节 2）；
- `tools/WallProbe.java`（只读探针，**取真实的 `Round`**）+ `tools/trainer-parity-check.mjs`：
  **同一 (seed, aka, dealer) → 136 张牌山 + 四家配牌 + 表/里宝指示牌 + 4 张岭上，逐个整数比对**。

跑法（`trainer/README.md` 有完整说明）：

```powershell
pwsh -File trainer\build.ps1
node tools\trainer-parity-check.mjs 64        # 64 组种子 × 4 种 aka/dealer 组合
```

结果：**PASS（0 处不一致）** —— 见 §6 的实测输出。

---

## 5. 里程碑

| 里程碑 | 内容 | 状态 / 完成判据 |
| --- | --- | --- |
| **M1 向听/进张/和了**（**收益最大的一步**） | 查表式向听（花色分组 + 位并行合并）、`Agari`（和了形/听牌/进张/好形听）、`HandEval` 的派生特征 | ✅ **已完成**：① 与 Java `Shanten.min`/`HandEval.of`/`afterDiscard` **1,000,000 手向听 + 217,000 手派生评估（其中打牌后评估 523,413 行）逐字段相等**；② 向听路径 **31.5×**、进张 **19×**、听牌形 **44×**（同机同口径，见 §6.1） |
| **M2 规则与牌局流程** | `Tiles/Meld/Rules`、`Evaluator`（役种/符数/点数）、`Payments`、`Round`（摸打/鸣牌仲裁/立直/杠/流局/连庄）、`Danger` | 🔄 **进行中**：① 打点内核 ✅ 20 万行逐字段一致、61 个役种码全覆盖（§6.2）；② 种子链 + 精算/连庄判据 ✅ 21 万行逐位一致（§6.3）；③ 动作空间 ✅ 369 行逐字符一致（§6.4）；④ 鸣牌仲裁判据 ✅ 1.26 万行（§6.5）；⑤ 振听记账（三种振听）✅ 354 行（§6.6）；⑥ 可见牌统计 + 和了形纯判断 ✅ 1.37 万行（§6.8）；⑦ 配牌顺序的假阳性已修正并重验 512/512（§6.7）；⑧ `Round` 状态容器 + 配牌 ✅ 1.4 万行（含 260 行 `rinit`，§6.9）；`Round` 摸打/鸣牌/立直/杠/流局循环 ⏳（判据：同 (seedBase, 策略串) → `g*.jsonl` 与 Java **逐字节相同**，先 100 场再 2000 场） |
| **M3 策略与网络** | `teacher`（五层取舍，与 Java 逐决策一致）、`first/pass/random`、`NeuralPolicy` 前向（float32 权重直读）、`PolicyFactory` 的每局实例化语义 | ① teacher 决策序列与 Java 相同（同 seed 同场）；② 网络 logits 与 Java 逐元素 ≤1e-4（golden 夹具）；③ `selfplay-check.mjs` PASS |
| **M4 性能与工程化** | 线程池（`--workers`）、AVX2 向听表、批量前向（同巡多候选一次 GEMM）、轨迹写入与 `summary.json`、CLI 与 `python/mahjong_ml/online.py` 对接 | ① **同等核数下决策/秒 ≥ Java 的 3×**（基线：24 核 1172 决策/秒、单核 88）；② 产出数据直接喂通 P3/P4 管线不改一行 Python |

**非目标**（明确不做，避免范围失控）：网络对战（TCP/NDJSON）、房间/等待室/身份/投票、
回放与牌谱导出、客户端相关的一切、以及**除 M.League 默认预设以外**的规则预设
（训练只跑这一套，见 `docs/TRAINING.md` §0；真要换口径再单独加）。

---

## 6. 实测

### 6.0 M0：牌山与配牌

```
$ pwsh -File trainer\build.ps1
==> 编译器   D:\Program Files\LLVM\bin\clang++.exe
==> clang version 22.1.7
==> 源文件   4 个 .cpp（另有 6 个头文件参与指纹）→ trainer\build\trainer.exe
  [ok]   洗牌结果是 0..135 的排列
  [ok]   同 seed 两次洗牌逐张相同
  [ok]   aka=0 → 无赤五
  [ok]   aka=3 → 恰好 3 张赤五
  [ok]   配牌后余 69 张可摸
  [ok]   开杠 → tilesLeft 减 1
  [ok]   岭上摸牌不动 tilesLeft
  [ok]   岭上摸牌只减 rinshanLeft
  …（M1 另加 13 条：快表向听 == 参考 DFS（20000 手）/ 七对子·国士·一般型的听牌与和了定点 /
    听牌形定点 —— 共 **21 条**）
TRAINER SELFTEST PASS

$ node tools\trainer-parity-check.mjs 24
[trainer-parity] 用例：24 组种子 × {aka 3/0} × {dealer 0/2} = 96 组
  [ok]   seed=20260101 aka=3 dealer=0  牌山 136 / 配牌 4 家 / 指示牌 1+1 / 岭上 4 全部一致
  ...
[trainer-parity] PASS：96/96 组逐整数一致（牌山 + 配牌 + 表里宝指示牌 + 岭上）
```

### M0 期间踩到的两个"契约细节"（都值一条判据）

1. **`Wall` 的指示牌访问器返回的是 `kind`，不是 `id`**（`Wall.indicators()` 里 `Tiles.kind(dead[...])`），
   而且**张数 = 已翻开的张数**（开局 1；`uraIndicators()` 与表宝**同长**）。
   照抄成"raw id + 固定 5 张"就会 100% 对不上 —— C++ 侧因此拆成
   `deadTile(i)`（原始 id，M2 计宝牌用）与 `doraIndicators()/uraIndicators()`（与 Java 同口径）。
2. **配牌顺序不是"每人一张轮 13 圈"，而是"一次抓 4 张、抓 3 轮"**：`Round.setup()` 是
   `3 轮 × 4 家 × 4 张（共 12）` → 每人补 1 张（13）→ 庄家第 14 张（`openingTile`）→ `finishDealing()`；
   且手牌按 `Round.compareTile` 排序（**先 kind，再"赤五在前"，最后比 id**）。
   ⚠ **2026-09 修正（一次真实的假阳性）**：探针第一版自己写成了"13 巡 × 4 家 × 1 张"，
   而 C++ 侧照着探针写 —— 于是**两边一起错、还互相印证了 512 组**。
   判据因此加了一条：**探针只准调 Java 自己的入口**（这里改成 `new Round(...)` + `debugSetup()`
   再读 `hand[]`），**不许在探针里重实现规则** —— 否则"对拍"退化成"和我的重实现比"。

⚠ **沙箱坑**：Node 不能用管道接子进程输出（`spawnSync … EPERM`，命名管道被禁）。
对拍脚本因此让子进程**直接写文件描述符**（`stdio: ['ignore', fd, 'inherit']`）再读文件 ——
等价于重定向，不碰管道。

### 6.1 M1：向听 / 进张 / 听牌形（已完成）

**怎么验的**：`tools/RuleProbe.java`（Java 的 `Shanten.min` / `HandEval.of` / `HandEval.afterDiscard`）
与 `trainer rules <corpus> <mode> <out>` 跑**同一份确定性语料**，然后逐行逐字段比对。
语料 = 手写边界手（国士十三面 / 七对子 / 九莲 / `1112223334445m` / 三面听 1m4m7m / 四张同种 …）
+ 随机构造（一半"拼出和了形再拆一张"的听牌手 → 专打 `waitShapes`；一半随机暗手 → 专打 `advanceKinds`），
副露数 0..4 均匀；`of`/`discard` 每行还比两个 34 维数组的 FNV-1a 哈希（数组级差异也逃不掉）。

| 语料 | 规模 | Java | C++ | 倍率 |
| --- | --- | --- | --- | --- |
| `shanten`（`Shanten.min`，13−3k 张） | 1,000,000 手 | 6.96 µs/手（143,773 手/秒） | **221 ns/手（4,523,243 手/秒）** | **31.5×** |
| `of` 随机暗手（= 进张路径） | 20,000 手 | 103 µs/手 | 5.4 µs/手 | **19.1×** |
| `of` 听牌手（= 听牌形路径） | 20,000 手 | 512 µs/手 | 11.7 µs/手 | **43.9×** |
| `of` 混合 | 100,000 手 | 129 µs/手 | 4.95 µs/手 | **26.1×** |
| `discard`（14 张 × 每个可打牌种一次 `afterDiscard`） | 50,000 手 → **349,686 行** | 143 µs/行 | 4.8 µs/行 | **29.7×** |
| 合计 | **1,000,000 + 217,000 手 / 523,413 行派生评估** | — | — | 0 处不一致 |

```
$ node tools\trainer-rule-parity.mjs all 1000000
[rule-parity] shanten：语料 1000000 行 …
  [ok]   1000000/1000000 行逐字段一致（java 8557 ms / cpp 2066 ms → 4.15×）
[rule-parity] of：语料 100000 行 …
  [ok]   100000/100000 行逐字段一致（java 14357 ms / cpp 615 ms → 23.34×）
[rule-parity] discard：语料 50000 行 …
  [ok]   349686/349686 行逐字段一致（java 51402 ms / cpp 1781 ms → 28.86×）
[rule-parity] PASS：向听 / 进张 / 听牌形与 Java 逐字段一致
```

（表中倍率用**两边各自报的纯计算时间**；括号里那个"→ x×"是**含进程启动**的墙钟比值，
C++ 侧那 2 秒里九成是 `strtol` 逐个数解析 100 万行语料、Java 侧是 JVM 启动 ——
都不是被测路径，所以不以它为准。语料解析慢这件事只影响对拍脚本，不影响引擎。）

#### M1 的实现要点：位并行合并（才是"数量级"的那一半）

朴素的查表法是"每组算出一个 `(面子, 搭子, 雀头)` 可达集，再四个组两两做三重循环卷积"——
那是 **2500 次迭代/次合并**，四次合并就比 Java 的 DFS 还慢（实测第一版只有 3.9×）。真正快的是：

- 每组的可达集压成**一个 64 位掩码**，位号 `s*10 + q`（`s` = 面子数、`q` = 搭子数，**乘 10 不是乘 5**）；
  雀头单独一个掩码（`p0` / `p1`）；
- 合并两组 = `out |= (b & rectDom[s][q]) << (s*10 + q)`：因为 `q_a + q_b ≤ 4 < 10`，
  **位移不会把 `q` 进位到 `s` 位**，一次移位+与就是一次"和卷积"（`rectDom` 是预计算的
  "`s+q ≤ 4` 允许域"表）；
- 雀头是**或**不是和（两边都出雀头也只算一个），所以 `p1' = p0·p1 | p1·p0 | p1·p1`。

于是"合并"从 2500 次迭代降到**每组合并 ~25 次位移**，向听再快 ~8 倍。这一层与 Java 的
`Shanten.dfs` 的**选择集**逐条对应（刻子/顺子/对子作雀头/对子作搭子/两面/嵌张/孤张丢弃 + `melds+partials<4`），
自检里还有一条"快表 == 参考 DFS（Java 逐行移植）"在 2 万手上交叉验证，**双保险**。

#### M1 期间踩到的两个"契约细节"

1. **Java `Agari.addWinVariants` 在 `melds == null` 时会 NPE**（`for (Meld m : melds)` 没有兜底，
   而同方法开头的 `meldCount = melds == null ? 0 : melds.size()` 又说明它本该容忍 null）。
   服务端两个调用点传的都是列表，所以**线上打不到** —— 但探针一开始传 `null` 就当场炸。
   口径：探针照生产走**空列表**（`Collections.nCopies(0, …)`），这条隐患记在 NOTES §6.5。
2. **`waitShapes` 的"没有听牌张"必须编码成 `-1`，不能留 0**：Java 的 `Snapshot.waitShapes`
   未听牌时是 `null`，哈希按全 `-1` 走；C++ 第一版只有听牌时才 `fill(-1)`，未听牌时是
   `std::array{}`（全 0）→ 138/200 行对不上，**而 8 个标量字段全都一样**（差异只藏在哈希里）。
   修法是让 `evalOf` **一开始就 `fill(-1)`**（与 Java `waitShapes` 的约定一致）。

### 6.2 M2（前半）：役种 / 符数 / 打点 / 授受（已完成）

**为什么先做这一块**：`Round` 流程再复杂，错了也只是"这局怎么走"；而打点错了是
**数据集里的钱不对**（`score` / `hand_delta` / 奖励全歪），且照常写出"看起来正常"的轨迹。

**怎么验的**：`tools/ScoreProbe.java`（`Rules.applyPreset` + `Evaluator.evaluate` + `Payments.compute`）
与 `trainer score <corpus> <out>` 跑同一份确定性语料，逐行比 **17 个字段**：
`valid / 役番 / 总番 / 符 / 役满倍数 / 宝牌 / 里宝 / 赤宝 / 基本点 / 打点档位 / 原因标签 /
和了形签名（含枚举顺序）/ 役种列表（码:折算番:倍数:牌）/ 四家收支 / 和牌者收入 / 点数部分 / 立直棒`
—— 役种列表里带**顺序**，所以"同分不同解释"也会被抓出来。

语料 = 手写特型（七对子 / 国士十三面与普通国士 / 九莲与纯正 / 字一色 / 绿一色 / 清老头 / 混老头 /
大三元 / 小三元 / 大四喜 / 小四喜 / 三色同顺同刻 / 一气 / 二杯口 / 三连刻 / 一色三顺 / 大数邻·大车轮·
大竹林·大七星 / 一筒摸月 / 九筒捞鱼 / 带副露的碰·吃·大明杠·暗杠·加杠 …）
+ 随机构造（0~4 副露、和了牌随机取手里一张；20% 把一张换成别的牌 → **不是和了形**那条路）
+ 上下文（自摸/荣和、立直/两立直/一发、海底/河底、抢杠/岭上、天和/地和/人和、燕返/杠振、流局满贯、
  包牌责任、本场棒、立直棒、赤五按 copy 0 分配）
+ 规则（mleague / tenhou / majsoul × {+koyaku, +kazoe, +dbl, +renhou, +renhouy}）。

| 规模 | 结果 | Java | C++ |
| --- | --- | --- | --- |
| **200,000 行**（131,113 手有役和了 / 8,358 手役满 / 3,846 不是和了形） | **0 处不一致**，役种码 **61/61 全覆盖** | 77,662 行/秒 | 122,139 行/秒 |

```
$ node tools\trainer-score-parity.mjs 200000
[ScoreProbe] 语料 200000 行，用时 2.575 s（77662 行/秒） 校验和 2317805240
[trainer] score 语料 200000 行，用时 1.637 s（122139 行/秒） 校验和 2317805240
  [ok]   200000/200000 行逐字段一致（java 4450 ms / cpp 1653 ms → 2.69×）
[score-parity] PASS：役种 / 符数 / 打点 / 授受与 Java 逐字段一致
```

**实现要点（都是"不这么做就会静默错"的地方）**：

1. **规则的默认集是 M.League 预设，不是字段初始值**：`Rules()` 构造时先铺 `preset`（默认
   `"mleague"`）→ `Rules.defaults() ≡ applyPreset("mleague")`。字段初始值里
   `doubleYakuman = true` / `kazoeYakuman = true` / `kiriageMangan = false` / `doubleWindPairFu = 4`
   **全部会被覆盖成** `false / false / true / 2`。只照抄字段初始值，打点表会整片对不上。
2. **和了形的枚举顺序是接口**：`Evaluator` 用高点法在**所有解释**里取最优，并列时取**先出现的**
   （`better()` 严格大于才算更好）。顺序一变，"番符一样、役种列表不同"的同分解释就互换 →
   轨迹里的 `yaku[]` 与 Java 不同。所以 `Agari.decompose` 的枚举顺序（雀头升序、面子"刻子在前顺子在后"、
   和了牌归属"雀头→面子顺序"）在 C++ 里逐行照抄，并在对拍输出里带**和了形签名**。
3. **役种名保留中文**：Java 内部用中文名（`役牌 白` / `场风 东`），只在发报文时经 `YakuCodes` 翻成码。
   C++ 同样保留中文名，另把 `YakuCodes` 的表（含参数化三码 + 打点档位 + 流局原因）一起搬过来 ——
   这样轨迹里的码与 Java 同源，也能顺带验证码表没漏。
4. **包牌是列表、本场棒归包牌者、不听罚符先定收方**：`Payments` 的三条"看起来能简化"的地方
   （多个责任者按座位累加 / 包牌者出全部本场棒并按 100 点平摊余数给靠前的 / 不听罚符按"每家收多少"
   反推付方）全部照抄，`--selftest` 里有 12 条手算定点钉住（含役满授受 48000/32000/16000+8000×2、
   1 本场 +300、立直棒 1000、不听罚符 1000/3 家听时收付和为 0）。

### 6.3 M2（中段）：种子链 + 精算 / 连庄判据（已完成）

**为什么先做这一块**：`rank_points`（顺位点）是**奖励函数的直接输入**
（`python/mahjong_ml/rewards.py` 直接读它，从不自己重算 uma）—— 算错一位，整条 RL 的奖励就悄悄偏了；
而它是由一串"看起来都很简单"的判据堆出来的：连庄 / 本场数 / 和了止 / 延长战 / 同点拆分 /
终局余棒。这些判据在 Java 侧原来**散在五个地方各写一行**（AUDIT S-46 / S-54 / S-55 / S-64），
每一处写错都只表现为"分数算错但不报错"。同时把"同种子 → 同轨迹"的地基（`mixSeed` /
`nextRoundSeed` 的确定性岔路 / `SelfPlay.seedFor` / 顺位）一起钉住。

**怎么验的**：`tools/SettleProbe.java` 与 `trainer settle <corpus> <out>` 跑同一份语料
（`settle` / `sticks` / `honba` / `alllast` / `west` / `nagashi` / `ngelig` / `split` /
`seeds` / `seedfor` / `place` 十一种行），逐行比对。

⚠ **浮点比的是原始位模式**（两边都打印 `%016x`）：两边十进制格式化的差异（Java 最短往返 vs
`printf %.17g`）会让"值相同但文本不同"，而顺位点是要写进数据集的 —— 位模式一致才算同源。

| 规模 | 结果 |
| --- | --- |
| **21 万行**（精算 5 万 + 余棒 5 万 + 本场真值表 / 和了止 / 延长战 / 流局满贯 / 拆分 / 种子链全覆盖） | **0 处不一致** |

**文档例子逐字复现**（这也是自检里的定点）：

| 终局点数 | M.League | 《雀魂》 |
| --- | --- | --- |
| 53600 / 28600 / 20000 / −2200 | **+73.6 / +8.6 / −20 / −62.2** | **+43.6 / +8.6 / −10 / −42.2** |

同点拆分（`tieSplitPoint`，只在 M.League 开）：30000/30000/30000/10000 → 三家**同顺位**（rank 0/0/0/3），
头名赏按 **0.1 分**为单位拆、尾数归**更接近起家**者 → 6.8/6.6/6.6，顺位点 16.8/16.6/16.6/−50；
四家同点 25000 → 顺位点**全 0**（马点相互抵消、头名赏四人均分）。
终局余棒（只有"最后一局是流局"才走）按 **100 点**为单位拆、尾数同样归起家：1 根 / 四家同点 →
**400/200/200/200**（与 M.League 原文的"3 人 1000 → 400/300/300"同一把尺子，只是人数不同）。

**实现要点**：

1. **种子链三个函数必须分开照抄，不能"顺手统一"**：`Table.mixSeed`（SplitMix64 终混，**先加黄金比**）、
   `SelfPlay.seedFor`（**不加**黄金比，只做两轮 xor-mul-shift）、`nextRoundSeed` 的确定性岔路
   （`mixSeed(seedBase + 局序号)`）。三者长得像、用途不同，差一步整场牌就换了。
2. **所有移位运算必须在无符号域做**：Java 的 `>>>` 是逻辑右移、`long` 溢出是**有定义**的回绕，
   而 C++ 的**有符号**溢出是 UB —— 用 `uint64_t` 算完再转回 `int64_t`，语义与 Java 逐位一致。
3. **`splitRemainder` 必须向下取整**（Java `Math.floorDiv`）：末位的顺位点是**负**的，
   C++/Java 的截断除法会给出负余数、尾数就分不完（`each × n + rest` 不再等于原值）。
4. **`stopAtAllLast` 的"和了止"与"听牌止"是两件事**：`agari` 为真时**无论是否听牌**都成立；
   `tenpaiAbort` 只在 `!nagashi` 时算（流局满贯按和了结算，不算听牌止）。门槛是
   `requiredPoints`（一位必要点数），**不是** `returnScore`（返点 = 精算基准）——《雀魂》
   正是"30000 vs 25000"。

### 6.4 M2（下半之一）：动作空间（已完成）

**为什么先做它**：轨迹里的 `legal` / `chosen` 写的就是 `Action.key()` —— 它是**数据集的动作空间**，
也是外部训练器唯一需要认的字串。而键的构造规则里有三条踩过坑的细节：

1. **碰与大明杠的键必须带"从手里取哪几张"**（`pon:5p+5p` / `kan:daiminkan:5s+5s+0s`）：
   手里同时有赤五与普通五时，服务端为两种取法**各下发一条**选项，是两个不同的合法动作；
   折成裸 `pon` 会让 `legal` 出现重复键、`chosen_index` 无从分辨（2026-09 被
   `tools/selfplay-check.mjs` 抓出来）。裸 `pon` 只作为**老数据**的兼容形态保留，落位走
   `resolve` 的"同类型第一条"。
2. **牌码槽位是 37 个**（34 种牌 + 赤 5m/5p/5s 三个槽）：`"0p"` 与 `"5p"` 的 **kind 相同但槽位不同**
   （35 vs 13），固定头下标必须按槽位算。
3. **合法动作集 = 展开后的具体动作**：`enumerate(options)` 出来每一项都保证被状态机接受，
   训练侧不需要知道任何规则；而 `resolve(cmd, legal)` 只在**碰/杠**上允许"同类型（同杠种）第一条"
   的退让 —— 其余动作必须精确匹配，**绝不挑一个像的**（那会把错标签写进数据集）。

**怎么验的**：`tools/ActionProbe.java` 与 `trainer action <corpus> <out>` 跑同一份语料，
逐行比对（键 / 固定头下标 / 回包 token 串 / 落位结果）。

| 覆盖面 | 结果 |
| --- | --- |
| **369 行**：全部 37 槽 × {打牌, 摸切打牌, 立直} + 单类型动作 + 吃/碰（含赤五两种取法）+ 三种杠（含大明杠取法+顺序规范化）+ **18 个非法键** + 落位（精确/裸 pon 退让/打牌不许退让） | **0 处不一致** |

⚠ **浮点之外的第二个"文本口径"坑**：回包的 **JSON 字段顺序**在两边可能不同（`toCmd()` 的插入顺序
vs 探针自己拼的顺序），于是"同一份回包"打印出不同 token 串、把纯粹的顺序差异误报成不一致。
现在两边都**按类型固定顺序**（discard/riichi → type;tile[;tsumogiri]、kan → type;kind;tile[;tiles]、
pon → type[;tiles]、chi → type;tiles）。

### 6.5 M2（下半之二）：鸣牌仲裁判据（已完成）

**为什么单独做**：这一层决定"什么时候可以不再等别家的应答"，而它最容易写错的地方是
**提前收工改变赢家**（AGENTS §2.3-10）。四条判据（`RoundClaims`）全是纯函数：

1. **等级尺子**：`荣和 3 > 杠 2 > 碰 1 > 吃 0 > pass −1` —— 它必须与真实仲裁循环**同一把**；
2. **同级看座次距离**（离打牌者近的赢）—— 所以 `canBeat` 必须**同时**看等级与距离；
3. **荣和要收齐**（多荣和）：`ronCapable` 还有人没答就绝不能收工；反过来"谁都不能荣和"时
   这条不成立（否则会立刻收工、把碰/吃的机会整片丢掉）；
4. **`askedBestRank` 缺项 → 保守继续等**（"继续等"只是慢一点，"收工"却可能丢掉他的碰/杠）。

外加**回包认领**（`acceptsReply`）：没被问 / 没有挂起询问 / `ask_id` 过期的回包一律丢弃 ——
否则上一轮的迟到回包会被当成本轮应答（症状："莫名其妙按了别家上一巡的选择出牌"）。

**怎么验的**：在 `trainer settle` / `SettleProbe.java` 里加了五种语料行
（`rank` / `beat` / `allron` / `stop` / `accept`），穷举小域 + 随机组合。

| 覆盖面 | 结果 |
| --- | --- |
| **12,567 行**（含 `beat` 800 行两个分支各半、`stop` 2000 行 true:false ≈ 2:1、`accept` 24 行、`allron` 35 行） | **0 处不一致** |

⚠ **顺手补了个休眠覆盖**：延长战那条分支原来在语料里**全是 0 值**（三套预设的 `westExtension`
都是关的），等于没比过。现在加了 `+west` 预设后缀（两边同义），`west` 用例里 36/240 为 true。

### 6.6 M2（下半之三）：振听记账（已完成）

**三种振听**（`docs/日本麻将.md` §振听；AGENTS §2.3-12）——`furiten.hpp` 与 Java `Round` 的
`furitenTemp`/`furitenPerm`/`discardKindsEver` + `isFuriten` 同口径：

1. **舍张振听**：当前所听的牌里有一种是自己**曾经打出过**的 —— 含听牌前打出的，
   也含**后来被他家吃/碰/杠走**的舍牌。所以账记在"曾经打出过"上，**不是**牌河的镜像
   （牌河要移除被鸣走的那张，见 AGENTS §2.2 的 `called_index`）；
2. **同巡振听**：本巡被给荣和却见逃（含超时未答）→ 自家**下一次摸牌**时解除；
3. **立直后振听**：立直状态下见逃 → **持续到本局结束**（摸牌也不解除）。

⚠ **唯一的记账点是"出牌"那一刻**（Java `Round.recordDiscard`）：测试不许绕过它。

**怎么验的**（这次是**真差分**，不是"自己跟自己比"）：`SettleProbe.java` 用**真实的 `Round`**
驱动 —— 牌桌只构造一次，每行**新建一个 Round**（`discardKindsEver` 是按局累计的私有账，
复用会让上一行的舍张污染下一行 —— 第一次就踩了这个坑，354 行里错了 333 行），
然后通过**公开字段**塞入手牌/副露 + `debugPushDiscard`（那唯一的记账点）重放舍张序列，
读回 Java 自己的 `ownDiscardKinds` / `waitKinds` / `isFuriten` 与 C++ 比。

| 覆盖面 | 结果 |
| --- | --- |
| **354 行**（4 种听牌形 × 6 种舍张序列 × temp/perm 组合 + 随机手牌；`isFuriten=true` 202、`temp` 127、`perm` 94） | **0 处不一致**（含 `waitKinds` —— 顺带又交叉验证了一次 `Agari.waits` 的移植） |

### 6.7 M0 修正：配牌顺序的**假阳性**（2026-09，最值钱的一条教训）

M2 走到 `Round` 门口、去读 `Round.setup()` 时发现：**M0 的"配牌逐整数一致"是假阳性**。

- 真实顺序（`Round.setup()`）：**3 轮 × 4 家 × 一次抓 4 张**（共 12）→ 每人补 1 张（13）
  → 庄家第 14 张（`openingTile`）→ `finishDealing()`；
- 探针与 C++ 侧当时都写成：**13 巡 × 4 家 × 1 张** —— 两边**一起错**，
  于是 512 组"逐整数一致"照常全绿。

**修法（两层）**：① C++ 的 `dealHands` 改成真实顺序；② **探针改成调用 Java 自己的入口** ——
`new Round(table, …)` + `debugSetup()`，再读 `round.hand[i]`（而不是在探针里重写配牌循环）。

**判据（已写进 §4 的契约与 NOTES）**：**探针只准调 Java 自己的入口，不许重实现规则。**
一旦探针里出现"我也写一遍这个算法"，对拍就从"和 Java 比"退化成"和我的重实现比" ——
而重实现最容易犯的错，恰恰是它本来要抓的那些错（配牌顺序、记账口径、优先级）。
同一条教训在振听那一轮也出现过（当时及时改成"用真实 `Round` 驱动"）。

修正后：`node tools\trainer-parity-check.mjs 128` → **512/512 组逐整数一致**（含庄家第 14 张）。

### 6.8 M2（下半之四）：可见牌统计 + 和了形纯判断（已完成）

`visible.hpp` 与 Java `mahjong.rules.Visible` / `mahjong.game.WinCheck` 同口径。为什么这块值得单独钉：

- **它是观测特征（M3）的底座**：`Observation.visible`、进张枚数、危险度三者必须**同源**
  （都是这一份账），否则"同样一张牌桌上还剩几张"会三处各算一遍、迟早漂；
- **「可见」的边界容易记错**：四家牌河 + 四家副露（含被鸣那张）+ **宝牌指示牌**；
  ⛔ **里宝指示牌不算可见**（只有和牌者自己看得到）；
- **`unseen = 4 − 可见` 与 `drawable = 4 − 可见 − 自己手里` 是两个量** ——
  混用是牌效统计里最常见的错（Java 的注释专门点了这一条）；
- 和了形纯判断里那条 **`14 − 3×副露数`** 的张数校验：自摸时暗牌里**已经含**和了牌，不能重复加。

| 覆盖面 | 结果 |
| --- | --- |
| **13,739 行**（`visible` 461 行含四家牌河/两种副露串/四种宝牌串、`vis` 333 行、`wckounts` 20 行（一半故意张数错一位）、`block` 4 行两个取值） | **0 处不一致** |

### 6.9 M2（下半之五）：`Round` 状态容器 + 配牌（已完成）

`roundstate.hpp` —— `Round` 本体的第一块：状态容器（手牌/副露/牌河/`menzen`/`riichi`/`furiten`/
`playerDraws`/`discardsSinceRiichi`/`openingTile`/`kanCount`）+ 构造函数 + `setup()`
+ `tilesLeft`/`deadWallLeft`/`canKan`，与 Java `mahjong.game.Round` 同口径。

**对拍仍走 Java 自己的入口**（`SettleProbe` 的 `rinit` 语料行）：`new Round(…)` → 读一遍字段 →
`debugSetup()` → 再读一遍，**探针里不重实现配牌循环**（§6.7 的教训）。

| 覆盖面 | 结果 |
| --- | --- |
| **13,999 行**（含新增 **260 行 `rinit`**：3 套预设 × 4 个庄家 × 5 个边界种子（含 `Long.MAX_VALUE`/0/−1）+ 随机种子） | **0 处不一致** |

钉住的几条：

- **`menzen[]` 全 `true`**（打进 `rinit` 行是 `15`）—— Java `boolean[]` 默认 false，漏了会让
  **所有门前役**在实局失效而单测照样通过（AGENTS §2.3-2）。现在它是**对拍里可见的**一项；
- **`furitenTemp`/`furitenPerm`/`doubleRiichi`/`ippatsu` 初值全 0** —— 新的一局不能带上一局的振听；
- **牌山账**：构造后 `tilesLeft = 122`（没开打过的 `Round` 也能答 `canKan()`/`deadWallLeft()`，
  所以牌山在构造期就备好）、配牌后 **69**、`deadWallLeft = 4`、`canKan() = true`；
- **庄家第 14 张**（`openingTile`）既进了庄家手牌、又就是"本次摸到的那张"（配牌后手牌数
  13/13/13/**14**）；
- 手牌按 `compareTile`（先 kind、赤五在前、最后比 id）排序 —— 逐张 id 比对。

---

## 7. 目录与构建

```
trainer/
├─ README.md            怎么构建、怎么跑、怎么对拍
├─ build.ps1            找 clang++（PATH → 常见安装位置）→ -O3 -march=native -std=c++23
├─ src/
│  ├─ java_rand.hpp     java.util.Random + Collections.shuffle 的逐位等价
│  ├─ tiles.hpp         牌码/kind/copy/赤五/种类判定/宝牌推导（与 Java Tiles 同一套）
│  ├─ meld.hpp          副露（吃/碰/大明杠/暗杠/加杠）
│  ├─ rules.hpp         规则集 + 三套预设（⚠ 默认 = M.League 预设，不是字段初始值）
│  ├─ seed.hpp          种子链（`mixSeed` / 每局种子 / `SelfPlay.seedFor` / 顺位）
│  ├─ roundscoring.hpp/.cpp 顺位点精算 + 连庄/本场/和了止/延长战/终局余棒（= Java `RoundScoring`）
│  ├─ action.hpp/.cpp   动作空间（键 / 固定头下标 / 回包 / 落位 = Java `Action`）
│  ├─ roundclaims.hpp   鸣牌仲裁判据（等级/压过/收工/回包认领 = Java `RoundClaims`）
│  ├─ furiten.hpp       振听记账（三种振听 + 唯一记账点 = Java `Round` 的那几个字段）
│  ├─ visible.hpp       可见牌统计 + 和了形纯判断（= Java `Visible` / `WinCheck`）
│  ├─ counts.hpp        34 维计数 + 幺九判定的小工具
│  ├─ wall.hpp/.cpp     牌山 + 王牌（账与 Java 同构）
│  ├─ shanten.hpp/.cpp  查表向听（花色分组 + 位并行合并）+ "参考 DFS"（Java 逐行移植，自检用）
│  ├─ handeval.hpp/.cpp 进张 / 听牌 / 听牌形 / `HandEval.Snapshot` 的等价物
│  ├─ agari.hpp/.cpp    和了形分解（**枚举顺序是接口**）+ 听牌 + 和了判定
│  ├─ evaluator.hpp/.cpp 役种 / 符数 / 基本点 / 高点法（= Java `Evaluator`）
│  ├─ payments.hpp/.cpp 授受点数 + 不听罚符（= Java `Payments`）
│  ├─ yaku_codes.hpp/.cpp 役种名/档位/流局原因 → ASCII 码（= Java `YakuCodes`）
│  └─ main.cpp          CLI：wall / rng / rules / bench / score / settle / action / --selftest
└─ build/               产物（**不进仓库**，已 gitignore）
```

工具链：本机实测 **`clang++ 22.1.7`**（`D:\Program Files\LLVM\bin`）+ `cmake` + `.qt/Tools/Ninja`；
`build.ps1` 按「显式参数 → PATH → 常见安装位置」解析，找不到就报明确错误（不静默降级）。
