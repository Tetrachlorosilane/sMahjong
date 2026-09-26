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
| **M2 规则与牌局流程** | `Tiles/Meld/Rules`、`Evaluator`（役种/符数/点数）、`Payments`、`Round`（摸打/鸣牌仲裁/立直/杠/流局/连庄）、`Danger` | ✅ **已完成**：① 打点内核 ✅ 20 万行逐字段一致、61 个役种码全覆盖（§6.2）；② 种子链 + 精算/连庄判据 ✅ 21 万行逐位一致（§6.3）；③ 动作空间 ✅ 369 行逐字符一致（§6.4）；④ 鸣牌仲裁判据 ✅ 1.26 万行（§6.5）；⑤ 振听记账（三种振听）✅ 354 行（§6.6）；⑥ 可见牌统计 + 和了形纯判断 ✅ 1.37 万行（§6.8）；⑦ 配牌顺序的假阳性已修正并重验 512/512（§6.7）；⑧ `Round` 状态容器 + 配牌 ✅ 1.4 万行（含 260 行 `rinit`，§6.9）；⑨ 选项生成第一层 `RoundOptions`（可打牌/食替/立直后杠/吃搭子）✅ 320 行（§6.10）；⑩ 自家回合 `turnOptions`（选项顺序 + riichi/tsumo/kan 闸门）✅ 四种策略 24 局 × 40 场 = 9.77 万次（§6.11）；⑪ 鸣牌询问 `claimOptions`（ron/pon/kan/chi + 赤五取法 + 振听）✅ 1.85 万次（§6.12）—— ⑩+⑪ 合计 **116,166 次逐字符一致**；⑫ `Round` 摸打/鸣牌/立直/杠/流局循环 ✅ **完整半庄与 Java 逐字节相同**（官方闸门 300 场 × 2 小局 `--cpp-workers 8`、100 场 × 8 小局、150 场完整半庄；独立复算 **200 场完整半庄 200/200** = 155,464 决策 / 1,761 小局两侧相等；**soak 500 场 × {`first`,`random`} 各 500/500**；§6.13 / §6.14 / §6.15）。⚠ 判据里原来写的"先 100 场"**不够**：食替退化局面**跟策略走**（**实测** `first` **3/500 场 ≈ 1/167**、`random` **0/500**，且短局碰不到），完整半庄验收**至少 200 场** —— 见 §6.15 |
| **M3 策略与网络** | `teacher`（五层取舍，与 Java 逐决策一致）、`first/pass/random`、`NeuralPolicy` 前向（float32 权重直读）、`PolicyFactory` 的每局实例化语义 | 🔄 **大部分完成**：① `first`/`pass`/`random` ✅ **与 Java 逐字节相同**（含 `random` 的 `java.util.Random` 逐位复刻；`random` 50 场 / `pass` 100 场完整半庄 100%）；② **`net:<权重文件>[@α][#T]` 前向 ✅ 已落地**（f32 直读 + `Features.state 607`/`candidate 96` 拼装）：golden 夹具 **Java↔C++ maxΔ = 0**、真实权重 36,181 条决策 **maxΔ = 1e-6 且 argmax 全同**、端到端 **`net:` 100 场完整半庄逐字节 100/100（24w 同核数 20.1×）**、`@0#1.0` 采样 20 场逐字节 20/20（§6.16）；③ 轨迹 + 派生特征直接喂通现有 Python 管线 ✅（`takeover-check` PASS、`dataset build` 出 81 / 671 条、state 607 / cand 96 / float16）；④ `selfplay-check` ✅ DATASET PASS（含 200 场完整半庄）；⑤ **`teacher`（五层取舍）✅ 已移植**（§6.17）：**200 场完整半庄 0/200 不一致**、13 个取舍计数器与 Java 逐项相等、tenhou 预设 120/120（覆盖 `kyuushu` + `botRng`）、**24 核吞吐 94,998 决策/秒 ≈ Java 的 91×** ⇒ 含 teacher 席的 **P5 世代已能整条走 C++**（§6.18）。⏳ 只剩 `net:` 的 **`@α` 先验**（P5b 混合臂）未接 —— 它要调 `Bot.decide`（现在有了），**显式报错**、不静默降级 |
| **M4 性能与工程化** | 线程池（`--workers`）、AVX2 向听表、批量前向（同巡多候选一次 GEMM）、轨迹写入与 `summary.json`、CLI 与 `python/mahjong_ml/online.py` 对接 | ✅ **已完成（并行 + 对接）**：① `--workers` 真并行落地（`selfplay` + `features`），**产出逐字节不变**（300 场 × 2 小局 1 vs 8 逐字节、**200 场完整半庄 1 vs 24 逐字节 200/200**）；② **同等核数下决策/秒 = Java 的 16.7×（100 场）/ 18.7×（200 场）**（基线：24 核 1172 决策/秒、单核 88；目标 3× 超出 5 倍以上，§6.14）；③ 产出数据直接喂通 P3/P4 管线不改一行 Python（`MAHJONG_PRODUCER=cpp` + `trainer-takeover-check.mjs` PASS）；④ `-NoSelfTest` / `TRAINER_NO_SELFTEST=1` 供工作流省掉编后自检。⏳ AVX2 向听表、批量前向未做（§6.14 也说明了为什么 `-flto`/`-fno-rtti` 不留） |

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

### 6.10 M2（下半之六）：选项生成的第一层 `RoundOptions`（已完成）

`roundoptions.hpp` —— Java `mahjong.game.RoundOptions` 的四个**纯函数**。它们值得单独钉，是因为
输出**同时**是三处：① 下发给客户端的选项；② 服务端的合法性校验；③ 训练端策略的动作空间
（`Action.enumerate` 的展开序就是 `chosen_index` 的分母）。三处一旦各算一遍，迟早漂。

| 函数 | 钉住的口径 | 对拍行数 |
| --- | --- | --- |
| `discardChoices` | 已立直 → **只剩摸切那一张**；未立直 → 手牌去重（**赤五是 "0m"、与 "5m" 两条**）；振听禁打按**牌种**过滤 | 45 |
| `kuikaeForbidden` | 食替**两侧都要禁**（吃 3m4m 配 5m 禁 {5m,2m}；配 2m 禁 {2m,5m}）；坎张只有現物；花色边界不越界 | 76 |
| `kanAllowedAfterRiichi` | 听牌集合必须**完全一致**（`after = c − 4 张`、`meldCount + 1` 再比集合） | 125 |
| `chiSets` | 三种组合的顺序固定 `{-2,-1}{-1,1}{1,2}`；越界用**花色区间**（8m9m 在手里也不会串进 1p 的搭子） | 74 |

- **`kanauto` 语料行走的是 Java 的真实调用点**（`Round.kanAllowedByRiichi`）：计数是**自己回合的
  14 张**，「杠前听牌」要**先把刚摸到的那张减掉**再算 —— 直接拿 14 张求听牌会**恒为空集**，
  于是"立直后的暗杠一次也发不出来"（Java 侧注释专门记了这条）。真值分布：允许 10 / 不许 52。

⚠ **一条实测出来的硬约束**：被杠的牌不在手里（不足 4 张）时，Java 的 `after[kind] -= 4` 会得到
**负数**，`Shanten.dfs` 直接 **StackOverflowError**（本轮对拍真炸过；C++ 侧计数是 `uint8_t`，
会绕回 255 → 两个引擎会看到不同输入）。所以 C++ 在函数门口就挡住这种输入，语料也只在
"手里真有 4 张"时生成。

**总计 14,319 行逐字节一致**（含 `ropts` 320 行）。

### 6.11 M2（下半之七）：自家回合的询问内容 `turnOptions`（已完成）

`turnoptions.hpp` —— Java `Round.turnOptions` 的等价物。**选项顺序是协议的一部分**：
`Action.enumerate` 按 `discard → riichi → tsumo → kan → kyuushu` 展开，而 `legal` 的下标就是
轨迹里的 `chosen_index` —— 少一个选项或换个顺序，训练标签会整体错位而且**不报错**。

对拍走**真实牌局**：`tools/RoundProbe.java` 用 `Table.playGame()` + 真实策略（`teacher`/`first`/
`pass`/`random`）推进牌局，每次自家回合把「局面 + Java 自己的 `Decision.options`」写成一行；
C++ 只吃局面那半、重算选项文本，逐字符比（`node tools/trainer-opts-parity.mjs`）。

| 覆盖面（`teacher` 12 局 × 20 场 = 10,204 次询问） | 计数 |
| --- | --- |
| `discard=` 只有打牌候选 | 9,730 |
| 带 `riichi=`（含"打 5z 后听 9s"这类真听牌形） | 384 |
| 带 `tsumo`（门前一気通貫等真和了形） | 54 |
| 带 `kan=`（`ankan:` 143 / `kakan:` 36） | 42 |
| 食替禁打非空的行（吃/碰之后那一巡） | 140 |
| **差异** | **0** |

**放大到四种策略 × 24 局 × 40 场 = 97,686 次**自家回合**询问，仍然 0 处差异**（`teacher` 21,694 /
`first` 28,536 / `random` 24,916 / `pass` 22,540）：`riichi=` 1,001 行、`tsumo` 125 行、
`kan=` 1,425 行，组合形（`riichi=;tsumo`、`riichi=;kan=`、`tsumo;kan=`）也都有。

钉住的几条（自检里也各有断言）：

- **`discard` 永远存在且第一**；已立直时**只剩摸切那一张**；**食替禁打按牌种**过滤。
- **`riichi` 候选**：`riichiAllowed`（门前 / 未立直 / 分数 / 残牌 / **海底禁立直**）→
  对每个 **id** 试算"去掉这一张还听不听"（⚠ 去的是**这一张 id**，不是整个牌种）。
- **`tsumo` 闸门**就是 `checkWin(..., tsumo=true, ...)`：张数契约（14 − 3×副露、自摸时和了牌已在手里）
  → `WinContext`（`riichi/doubleRiichi/ippatsu` 的组合口径、`tenhou/chiihou`、天地人、
  `doraIndicators` + **`includeUra=true`**（实局判定算里宝）→ `allTileIds` = 副露真实 id +
  和了牌 + **合成的赤五 id**（`i%3` 轮转 `5m/5p/5s`，条数 = 暗牌里的赤五数）。
- **杠**：闸门 `canKan() && !haitei`（海底那一巡不给杠）；候选 = 暗杠（牌种升序，
  手里真有 4 张且过 `kanAllowedByRiichi`：①听牌集合不变 ②M.League 的**面子构成不变**）+
  加杠（`PON` 副露且手里还有该种），**暗杠在前、加杠在后，同一个 `kan` 选项里**。
- **九种九牌**：M.League 的 `kyuushuAbort=false` ⇒ 这条分支在训练口径下**永不亮**；
  换成《天凤》预设才亮（自检两条都断言了，所以"永不亮"是**被验证过的**，不是漏了）。

### 6.12 M2（下半之八）：鸣牌询问 `claimOptions`（已完成）

`claimoptions.hpp` —— Java `Round.claimOptions` 的等价物，与 `turnoptions.hpp` 对称。
顺序（协议的一部分）：`ron` →（河底 / 已立直时到此为止）→ `pon`×取法 → `kan`(大明杠) → `chi`(仅下家) → `pass`。

与自家回合的**三处口径差异**（都钉在对拍里）：

- 手牌是 **13 张形态**（被鸣那张在别人牌河），荣和判定要把那张**加进来**（`winCounts(..., tsumo=false)`）；
- **燕返**与**河底**只有这条路上才有：`tsubame` 比的是"打牌者刚立直那张宣言牌"
  （`discardsSinceRiichi == 1`），荣和时赤五数 = 暗牌里的 + **和了牌本身若是赤五**；
- **振听**走完整判据（同巡 / 立直后 / **舍张振听**：听牌里有自己打出过的牌种）——
  账（`discardKindsEver`，private）由探针反射读出后交给 C++ 自己算 `isFuriten`，
  而不是让 Java 喂结论。

**取法（`akaVariants`）**：普通牌优先、**用赤五那条在后**；够凑出这一副才给第二条。
于是"碰 5p"可能下发两条选项（`pon=5p+5p;pon=0p+5p`）—— 客户端据此画不同按钮
（旧协议不带 `tiles` 正是"副露分不清赤五"那次的根因）。

| 覆盖面（四种策略 × 24 局 × 40 场） | 计数 |
| --- | --- |
| **总询问数**（自家回合 + 鸣牌） | **116,166** |
| 鸣牌询问 | 18,480 |
| 吃（`chi=`） | 11,449 |
| 碰（`pon=`，含**两种取法** 23 行） | 6,076 |
| 荣和（`ron`） | 315+ |
| 大明杠（`kan=daiminkan:`） | 708 |
| 河底 / 自家已立直那条早返回 | 191（其中**仍带吃碰杠 = 0**） |
| **差异** | **0** |

⚠ 一条**结构性覆盖缺口**（值得记住）：`claimPhase` 只把"有得选"的座位算作 eligible
（只有 `pass` 一条的**不问**），所以「只有 `pass`」这种行在真实牌局里**根本不会出现** ——
河底/立直那两条早返回只能通过"能荣和"的行被观察到。自检里因此另配了构造用例
（河底 → 不给吃碰杠；立直 → 只给荣和或过；不是下家 → 不给吃）。

---

### 6.13 M2 收官：C++ 自对弈跑通（逐字节）+ 性能实测（已完成）

`round.hpp/.cpp` + `table.hpp` + `trace.hpp/.cpp` + `selfplay.hpp/.cpp` + `observation.hpp` /
`options.hpp` / `policies.hpp` / `jsonw.hpp` —— `Round.play()` 主循环（摸打 / 鸣牌仲裁 / 立直 / 杠 /
和了 / 流局）与 `Table.playGame()` 的局间节奏（本场 / 连庄 / 轮庄 / 终局余棒）逐句移植。

| 判据 | 结果 |
| --- | --- |
| `trainer-selfplay-parity.mjs 1 1 pass 20260101` | `g0.jsonl` **134,375 B 逐字节一致**（SHA256 两边同为 `5385a50a978fd777…`；83 行 = 81 decision + 1 hand + 1 game），`summary.json` 除计时/核数外逐字段一致 |
| 加层（每层都重跑） | 4 场 `pass` / 三策略 × `--rotate` / `2 3 first` / `2 2 random` / **完整半庄** `1 0 first 8888`（1,689,740 B）/ `--sample` / `--no-claims` / **三套预设**（含九种九牌流局、立直+终局余棒、自摸和了） |
| 少量场次检验 | `40 场 × 2 小局 × first × seed 777` → **40/40 逐字节一致** |
| **性能（同工作量，本机）** | Java 单核 **786** 决策/秒 → C++ 单核 **16,630**（**21.2×**）；Java 8 workers **3,690** → C++ 8 workers **17,230**（**4.7×**） |

⚠ 上面的两组是**短局**（2 小局）且含 JVM 启动开销，会**放大**倍数；换成**真实工作量**（100 场**完整半庄** × `first`，
24 workers）后：Java **10,558** 决策/秒（7.4 s）vs C++ **20,508**（3.8 s，**单线程**）—— 即**单核已快过 Java 24 线程约 2×**，
而这 100 场同样**逐字节 100/100 一致**。口径差异说明：绝对数随策略/局数变（`teacher` 比 `first` 重得多），只有**同工作量比值**有意义。
`--workers` 当时**串行**（8/24 workers 与单核同速）—— 真并行已落地，实测见 §6.14；
**目标里的"同核数 ≥ 3×"在没有做任何性能优化前就已经超过**（基线：Java 24 核 1172 / 单核 88）。

⚠ **后来补的教训**：上面那句"100 场 100/100"是真的，但**不够** —— 同一台引擎在 `seed 31337`
的第 **119** 场就会踩到一个**实测约 1/167 场**才出现的退化局面（食替把暗手每种牌都禁打 → `legal` 为空，
见 **§6.15**）。完整半庄的验收从此改为**至少 200 场** + 必须同时与 Java 参考对拍。

---

### 6.14 M4（并行与工程化）：`--workers` 真并行（逐字节不变）+ 性能实测（已完成）

**要解决的问题**：`selfplay` 与 `features` 的 `--workers` 一直是"只接受、串行跑"。
而"同核数下决策/秒 ≥ Java 3×"这条 M4 目标，在单线程下已经靠 §6.13 的算法优势超了；
真并行是把**绝对吞吐**再抬一个量级（工作流里 300 场从"几分钟"变"几十秒"）。

**并行模型（与 Java 同一套）**：

| 子命令 | 调度 | `--workers` 缺省 / 0 | 钳制 |
| --- | --- | --- | --- |
| `selfplay <games>` | K 个 `std::thread` 用 `std::atomic<int>` 抢场号 `g`，各写各的 `g<g>.jsonl` | **1**（不按核数自动并发） | `max(1, min(K, games))`（= Java `SelfPlay.run` 同一个式子） |
| `features <dir>` | 同样抢 `g*.jsonl` 列表（`AtomicInteger` 的等价物） | `max(1, 核数 × 3/4)`（= Java `TraceFeatures.defaultWorkers`） | `max(1, min(K, 文件数))` |

⚠ **两处缺省口径不同是刻意的**（都写进了 `trainer --help` 与 `trainer selfplay --help`）：
`selfplay` 侧保持"缺省 = 1"是**行为兼容**（省略 `--workers` 时与以前逐字节同速同产物），
`features` 侧跟 Java 的 `×3/4` 口径。`summary.json` 的 `workers` 字段写的是**钳制后的线程数**
（与 Java 同口径；它不在逐字节比较范围内）。

**为什么并行不改产出（三条，缺一不可）**：

1. **每场的种子只与 `(seedBase, g)` 有关**（`SelfPlay.seedFor`），每场每席的策略实例是
   `create(seat, gameSeed)` 现造（`policies.hpp` 的 `PolicyFactory`）—— 场与场之间**没有**顺序依赖；
2. **结果合并在 join 之后按 g 升序做**：`summary` 的整数累加、`by_policy` 的插入序、
   `per_game` 的数组、`rankPointSum` 的浮点求和顺序全部与串行**逐位相同**（浮点加法不交换！）；
3. **共享可变状态只有向听缓存一处**，已经消除数据竞争（见下）。

**并行化时唯一需要处理的共享状态**：`shanten.cpp` 的 `suitTable()` / `honorTable()`
（花色 195 万 + 字牌 7.8 万个 `Reach` 槽，惰性填充）。原实现是"检查 `p0 != 0` → 没有就 DFS 填槽"，
两个线程可能同时填同一个槽 —— `Reach` 是 16 字节非原子对象，读侧会看到
"`p0` 已是新值、`p1` 还是 0" 的撕裂组合（UB，且**向听会静默错**）。
现在"检查 → 计算 → 填槽"整段在一把 `std::mutex` 里（锁内**再查一次**），命中路径仍免锁：
只有持锁线程会写那个槽，而 `p0` 是最后被写的发布位（`(s=0,q=0)` 恒可达）。
调试构建里还有一条 `NDEBUG` 之外的断言钉住"算完的槽 `p0 != 0`"。

**验收（每一条都在本机跑过）**：

| # | 判据 | 结果 |
| --- | --- | --- |
| 1 | `--workers 1` vs `--workers 8`（300 场 × 2 小局 × `first` × seed 20260101） | **300/300 个 `g*.jsonl` 逐字节相同**；`summary.json` 除计时/核数外逐字段相同；文件集相同（301） |
| 2 | C++ 并行 vs **Java**（同命令，Java 侧 `--workers 1`） | **300/300 逐字节相同**（`--cpp-workers 8`）；另用 Java 直接跑 `--workers 24` 的 300 场与 C++ 并行产物逐字节比 → **0 处不同**（`g0` 两侧 sha256 `9FED9A5E1F17B58C…`，352,597 B） |
| 3 | `features` 串行 vs 并行（300 个轨迹） | **300 个 `g*.feat.bin` 逐字节相同**（合计 10,154,992 B，合并 sha256 `06ffab9519e1659d…`）；用时 4.1 s → **0.6 s（8 workers，6.8×）** |
| 4 | `features` C++ vs Java（300 个 sidecar，24 workers） | **逐字节相同**（`tools/trainer-features-parity.mjs`）；另 **200 场完整半庄**的 200 个 sidecar 同样逐字节相同 |
| 5 | `node tools/selfplay-check.mjs trainer\build\wk-8` | **DATASET PASS**（300 场 / 600 小局 / **52,856** 决策；`discard=46930 chi=3789 pon=2109 ron=28`） |
| 6 | `node tools/trainer-takeover-check.mjs 1 1 pass 20260101` | **PASS**（采集 → 逐字节比轨迹 → sidecar → 逐字节比 → selfplay-check → `dataset build` 整条链） |
| 7 | `--selftest` | **135 项全绿** |

**规模复核（在 §6.15 的修复之后重跑一遍，全部逐字节）**：

| 判据 | 结果 |
| --- | --- |
| 官方闸门 `300 2 first 20260101 --cpp-workers 8` / `100 8 first 20260101` / `150 0 first 31337` | 三条都 **PASS**（`g*.jsonl` + `summary.json` 逐字段） |
| **200 场完整半庄** Java 24 workers vs C++ 24 workers（`first` / seed 31337） | **200/200 逐字节一致**（155,464 决策 / 1,761 小局，两侧相等） |
| 同 200 场 C++ `--workers 1` vs `--workers 24` | **200/200 逐字节一致**（并行不变性，完整半庄规模） |
| `random` 50 场 / `pass` 100 场完整半庄 | **50/50 · 100/100 逐字节一致**（`random` 覆盖逐位随机源那条路） |
| `node tools/selfplay-check.mjs <200 场目录>` | **DATASET PASS**（155,463 条决策行 = Java 的 `decisions` 少那 1 条被丢掉的兜底行，§6.15） |
| **soak：500 场完整半庄 × {`first`,`random`}**（`tools/trainer-soak-check.mjs 500 first,random 20260101 8 24`） | 与 Java **500/500**、与 `--workers 1` **500/500**、失败场次 **0**；`first` 387,148 决策 / 4,392 小局、`random` 363,179 / 4,072；「引擎兜底出牌」计数 8w 与 1w **完全相同**（3/3、0/0）⇒ 退化只由种子决定 |
| **同核数 500 场复核**：Java 24 workers vs C++ **8** workers | `g0..g499` **0 处不同**；Java 自身计时 **30.17 s**（12,833 决策/秒）vs C++ 8w 墙钟 **2.6 s**（≈148,000 决策/秒）⇒ **只开 8 个 worker 就已快 11.6×**（24 workers 见上表 16.7×） |

**性能（同工作量，本机 32 逻辑核 / Windows）**：100 场**完整半庄** × `--policy first` × seed 20260101
（**78,279 决策**，两侧同一个数——它由种子决定，与并行度无关）：

| 跑法 | 墙钟 | 决策/秒 | 相对 Java 24 workers |
| --- | --- | --- | --- |
| **Java 24 workers**（`--out`） | **8.82 s**（自身计时 8.56 s） | **9,140**（自身计时口径） | 1.0×（基线） |
| C++ 1 worker（`--out`） | 3.84 s | **20,553** | **2.2×**（单线程就超 Java 24 线程） |
| **C++ 24 workers**（`--out`） | **0.53 s** | **153,000**（三次 158,716 / 148,366 / 144,680） | **16.7×** |
| C++ 24 workers（**不写 `--out`**，纯引擎） | 0.44 s | ~167,000 | ~18× |
| C++ 24 workers vs C++ 1 worker | 3.84 s → 0.53 s | — | **7.3× 并行加速** |

**独立复算（我这边另跑的一次，200 场完整半庄 × `first` × seed 31337，155,464 决策）**：
Java 24 workers **17.9 s 墙钟**（自身计时 17.61 s，8,827 决策/秒）vs C++ 24 workers **1.0 s**
（169,455 决策/秒）⇒ **17.6×（Java 自身计时口径）/ 18.7×（墙钟口径）**；C++ 1 worker 7.5 s
⇒ **并行加速 7.82×**。两次数（100 场 / 200 场）互相印证。

⚠ 口径说明：Java 的"自身计时"从 `run()` 起算（不含 JVM 启动），C++ 的 `seconds` 从线程起跑算；
上表 Java 用**自身计时口径**（对 Java 最有利），"相对倍数"因此是最保守的那个数。
`--out` 的轨迹总量约 1.1 GB/100 场，所以"写盘"确实占掉一部分（0.53 s vs 0.44 s）；
不写 `--out` 那行只是用来把"引擎吞吐"与"写盘"分开看，**不是**生产用法。

**任务 C（编译/缓存优化）的实测结论 —— 两条都是"试过、没收益/有风险，已回退"**：

| 试的东西 | 结果 | 结论 |
| --- | --- | --- |
| `-flto=thin -fuse-ld=lld`（`-O3` 之上，只多一个 LTO） | `--selftest` 135 项全绿、决策数与基线同为 78,279；24 workers 三次 **158,490 / 154,123 / 150,496** 决策/秒 vs 基线 **158,716 / 148,366 / 144,680** —— **在噪声内无差异**，且编译从 ~6 s 涨到 **30.5 s**、还多一个"必须找到 lld"的构建依赖 | **不加**（收益不可测、代价确定） |
| `-fno-rtti` | `llvm-nm` 已经在基线 exe 里查不到任何 `RTTI` / `vtable` / `type_info` 符号（代码里没有 `dynamic_cast`/`typeid`） | **无需加**（没有可省的东西；加了也只是"打标记"） |
| 并行化本身（Task A） | 上表：24 workers **153,000** 决策/秒 vs 单线程 **20,553** | **保留**（这才是这一轮真正的性能来源） |
| 向听缓存的锁（Task A 的副作用） | 命中路径仍免锁；锁定只在"槽第一次被填"时进入（实测吞吐见上表，与加锁前同量级） | **保留**（消 UB，代价可忽略） |

**结论：这一轮的性能来自"算法 + 并行"，不来自编译选项** —— 同核数下 **≈ Java 的 17–19×**
（目标 3×，超出 5 倍以上）。

---

### 6.15 契约条目：自家回合的 `legal` 为**空**（食替退化）—— Java 是权威，训练端逐字复现

**现象**（**实测**：`first` **3/500 场 ≈ 1/167**、`random` **0/500** —— 它**跟策略走**，`first` 会贪心吃成
食替锁死的形；且与 `--workers` 无关，8 workers 与 1 worker 的计数完全相同）：`selfplay` 跑到某一场突然报

```
[trainer] 自对弈失败：策略回包不在本次 legal 里（座位 1，键 pass）—— 训练端没有内置 Bot 兜底
```

并以 exit 2 退出；因为轨迹是**整场结束才落盘**的，失败那一场什么都不写（只写出前 N-1 个文件）。

**根因 —— 不是 C++ 的分歧，两侧状态完全一致**：食替（`RoundOptions.kuikaeForbidden`）可能把暗手里
**每一种牌**都禁打。原案（`seed 20260101` / `--policy first` / 完整半庄 / **g66 的第 300 次决策**）：
座位 1 用 6s+7s 吃 5s 组成 `567s`（第 4 副露），此后暗手只剩 `88s`；而
`kuikaeForbidden(called=5s, k0=6s, k1=7s)` = `{5s（現物）, 8s（hi+1，筋食替）}`
⇒ `RoundOptions.discardChoices` 返回**空** ⇒ 本次询问的 `legal` 就是**空表**。
`Policies.firstLegal()` / `passFirst()`（以及 `policies.hpp` 的同一份移植）在空表上只能回 `Action.PASS`。

**Java 侧怎么收场（三件事，训练端必须逐条对齐）**：

1. `Policies.fromAction` 见"回包不在本次 `legal` 里" → **退回内置 Bot**（`Bot.decide(Round, …)`）；
2. Bot 的回包被 `Round.discardAllowed` 判非法（空 `legal` 意味着暗手每一张都被禁打，
   回非打牌动作同样被拒）→ **强制 `defaultDiscardId` 兜底出牌**（原案打出 `8s` = `hand[seat].get(0)`）
   ⇒ **这条链的净效果与 Bot 的取舍无关**（这也是训练端**敢于**在没有 Bot 的情况下复现它的依据）；
3. `TraceRecorder.onChoice` 里 `Action.resolve(cmd, legal=[])` 为 null → **这一行不进轨迹**
   （`observed` 仍 +1、`step` 不动）—— 所以 Java 的 `summary.json` 里 `decisions`
   会比数据集里的决策行**多 1**（200 场原案：155,464 vs 155,463）。

**训练端的处置**（`table.hpp::decideBot` 里**唯一**的例外）：只对「`kind == "turn"` 且 `legal` 为空」放行 ——
把策略的原回包**原样**交给引擎，`round.cpp` 的回合循环会走**同一条**
`discardAllowed → defaultDiscardId` 兜底链（那两行本来就是 Java 的同名移植）；
**其余任何**"回包不可解析"仍然**硬报错**（那才是真需要内置 Bot 的情形，训练端**绝不伪造标签**）。
每次兜底在 stderr 打一行 `[trainer] 引擎兜底出牌（自家回合 legal 为空…）` —— 便于计数，**不是错误**。

**红证**：

| 步骤 | 结果 |
| --- | --- |
| 修前：`selfplay 100 --policy first --seed 20260101 --hands 0` | 第 **66** 场退出（exit 2），写出 67 个文件；换 `seed 31337` 则第 **119** 场退出 |
| 修后：同一命令 | **100 场跑完（exit 0）**，兜底恰好触发 **1 次**（g66 第 300 次决策） |
| 修后：`g0..g66` vs Java 参考轨迹（`trainer\build\java-g66`） | **67/67 逐字节一致** |
| 修后：**200 场完整半庄**（`seed 31337`，即此前第 119 场失败的那条） | Java 24 workers vs C++ 24 workers **200/200 逐字节一致**；sidecar 200/200 |
| 修后：`random` 50 场 / `pass` 100 场完整半庄 | **50/50 · 100/100**（`random` 在空表上**不消耗**随机源：`n == 0 ? PASS : legal[nextInt(n)]` 与 Java 的三元短路同式） |
| 修后：**soak 500 场 × {`first`,`random`} 完整半庄** | 与 Java **500/500**、与串行 **500/500**、失败 **0**；兜底频率实测 `first` **3/500**（≈1/167）、`random` **0/500**，且 8w 与 1w 计数相同 |

⚠ **判据教训（值一条）**：这个局面**每 100 场左右才出现一次**，所以
"100 场 100/100"这种验收**会漏掉它**（§6.13 当时那句 100/100 是真的，只是样本不够），
而且它**只在"整场跑满"时才会出现**（`--hands 2` 这种短局几乎碰不到）。
⇒ 完整半庄的验收**至少 200 场**，且**必须**同时跑 Java 侧参考 ——
"对拍 C++ 自己的串行/并行"永远发现不了它（`--workers` 确实没改产出，是**引擎**要修）。

**为什么不改规则**：Java 那边这条其实也是引擎的一处退化（`discardChoices` 允许为空），
但**训练端不改规则** —— Java 是权威（§2 铁律 ③），改了就再也不可能逐字节相同；
训练端要做的只是**把 Java 的收场方式复现出来**。

---

### 6.16 M3（后半）：网络策略 `net:<权重文件>[@α][#T]` 前向（已完成）

**为什么必须做**：P5 的对抗训练（联赛世代）**每一席都跑 `net:<ckpt>`** —— `online.py` 的采集命令就是
`--policy net:<权重文件>@0#1.0`。C++ 侧只有 `first/pass/random` 的话，换到 C++ 生产者只能跑基线臂，
"进一步对抗训练"就无从谈起。

**三段拼装（与 Java 严格同序、同类型）**：

| 段 | 来源 | 说明 |
| --- | --- | --- |
| 权重 | `export.py` 的 `weights` 格式 | 小端；7 个 int32 头（magic `0x4D4A4E4E` / 版本 1 / state / cand / hidden / head / trunkLayers）+ trunk 各层 `W[out][in]`、`b[out]` + head `W[head][hidden+cand]`、`b` + `out W` + 标量 `ob`。`net.cpp::loadNet` 在**构造期**校验魔数/版本/维度（文案与 Java 同义），坏了立刻报错 |
| 输入 | `Features.state` 607 / `Features.candidate` 96 | 607 = 539 基础 + 68 派生（danger / 100）；96 = 88 基础 + 8 派生（各自 / 固定 scale）。⚠ 这两层**原先只有 Java/Python 有**（C++ 的 sidecar 是 int 段），是本轮新写的；它们依赖的 `perDecision` / `perCandidate` 早已在 C++ 且逐字节验过（sidecar 200/200） |
| 前向 | `NeuralPolicy` | `h = ReLU(W₂·ReLU(W₁·x + b₁) + b₂)`；`logit_i = o·ReLU(H·[h ‖ cand_i] + b_h) + b_o`。全程 **float**、**单累加器正序** |

**两条硬口径（缺一条判据就不成立）**：

1. **`-ffp-contract=off`**（`trainer/build.ps1`）：FMA 收缩只改最后一位舍入，而 1e-4 的逐元素容差
   **挡不住** argmax 翻转 —— 本项目的判据是"同种子 → **逐字节轨迹**"，不是"数值接近"。禁掉收缩后
   前向与 Java 是逐位同序的。
2. **破平取最小下标**（严格 `>` 才更新）+ **`#T` 的随机源每局每席新建**
   `JavaRandom(mixSeed(gameSeed, seat))`（= `Policies.net` 的那一条；`random` 策略用的是另一条
   `gameSeed * 31 + seat`，**两者不能混**）。`nextDouble` 已按 JDK 逐位补进 `java_rand.hpp`。

**判据与实测**（命令：`tools/trainer-net-parity.mjs`，闸门 + `--golden` + `--selfcheck`）：

| 判据 | 结果 |
| --- | --- |
| **golden 夹具**（`python/tests/golden/forward.bin`：小网 hidden 32 / head 16 / 6 条用例） | Java↔Python maxΔ **3.0e-8**；**Java↔C++ maxΔ = 0（逐位相同）**；6 条 argmax 全同 |
| **真实权重** `ckpt/ppo2-g04/net.bin`（266,753 参数）× 100 个真实轨迹 | **36,181 条决策 maxΔ = 1.0e-6**（float32 1 ulp）、`n` 与 **argmax 全部相同** → PASS |
| **端到端（贪心）** `--policy net:<ckpt>` | **100 场完整半庄：Java 24w vs C++ 24w 逐字节 100/100**；Java 85.6 s vs C++ **4.2 s = 20.1×** |
| **端到端（采样）** `net:<ckpt>@0#1.0`（世代采集的形状） | **20 场完整半庄逐字节 20/20**（`exp` / `nextDouble` / CDF 累积全部一致） |
| `--sample` 组合（世代采集的另一半形状） | `50 场 first --sample 7`、`50 场 net:<ckpt> --sample 7` 均逐字节 PASS |
| `@α`（P5b 的 teacher 先验） | **显式报错**：先验要调 `Bot.decide(Round, …)`，而 teacher 未移植 —— **不静默降级** |
| 闸门自身 | `--selfcheck` 3/3（未改坏 → PASS；某 logit +1e-3 → FAIL；argmax 6→0 → FAIL），另用假 C++ 验过全链路出口码 |

**本轮的坑（都值得记）**：

- **`%.9g` 的文本必然不同**（Java 保留尾随零 `-554.013000`、C 的 `%g` 去掉 `-554.013`；`NaN`/`Infinity`
  拼写也不同）→ 闸门按**数值 + 容差**比、文本差异只当 info。**不要**为了"文本一致"去模拟 Java 的 Formatter。
- **Windows 命令行上限 32,767 字符**：整个 `bc-001`（800 个路径）一次传给探针会**根本起不来**
  （Node 报 `status=null`，看着像被信号杀）→ 闸门按长度自动分批（`NET_PARITY_MAX_ARG_CHARS` 可强制小批）。
- Java 探针成本 ≈ **5.9 ms/条**（瓶颈是 Java 侧的特征拼装，不是前向本身）：整目录 29 万条 ≈ 28 分钟。
- **`teacher` 仍未移植 ⇒ 含 teacher 席的 P5 世代仍只能在 Java 生产者上跑**（联赛设计里老师常驻一席）。
  这是"对抗训练完全走 C++"的**最后一块**，见 §5 M3 的 ⏳。
- `producer.py` 的 `CPP_MISSING` 里曾挂着 `--sample`（其实早就支持且逐字节验过）—— 那条多余的门
  正好挡住世代采集（`online.py` 阶梯/评测默认 `--sample 64`），已删除。

---

### 6.17 M3（收尾）：`teacher`（内置机器人）的五层取舍（已完成）

**为什么它是"对抗训练上 C++"的最后一块**：P5 的世代采集席固定是「2 席自己（`net:`）+ 2 席对手」，
而 `online.py` 里**老师常驻一席**（`if "teacher" not in picked: picked[-1] = "teacher"`，
注释写着它是"没有变弱"的基准），第一代更是 `["teacher","teacher"]` —— 没有 `teacher`，世代只能在 Java 上跑。

**移植口径**：`trainer/src/bot.cpp`（≈1.4k 行）= `server/.../bot/Bot.java`（1,443 行）**逐句**移植，
含嵌套的 `HandState`。它只依赖 `Round` / `Meld` / `Tiles` / `Agari` / `Danger` / `Evaluator` /
`HandEval` / `Payments` / `Shanten` / `Visible` / `RoundScoring` —— 这些训练端**全都已有**，
所以没有引入任何新依赖。

**四处必须改的接缝**（都不是"顺手改"，缺一条就出不来逐字节）：

| 接缝 | 为什么必须 |
| --- | --- |
| `Round::ask(seat, kind, obs, opts, calledTileId)` + `Table::decideBot(…, Round*, opts, calledTileId)` | Java 的 `Decision` 带着 `round` / `options` / `extra`，`Bot.decide` 按**原始选项**取舍（`first`/`pass`/`random`/`net` 仍只用 `obs.legal`）。包含关系只能是 `bot.hpp → policies.hpp`，所以 `makeTeacherPolicy()` 定义在 `bot.cpp`、`policies.hpp` 只前置声明 `Round` |
| `Decision.fromBot` | ⚠ Java 的 `Policies.TEACHER` **不经过** `fromAction` 那条"回包必须在本次 `legal` 里"的校验（合法性由 `Round` 自己判：`discardAllowed` / `pickAuto` / 杠校验）。训练端的漏斗必须**同样放行内置 Bot 的回包**，否则会出现 Java 根本没有的 `fatal`（这正是 §6.15 那条的延伸：teacher 在食替锁死时会回 `pass`，而本次询问的 `legal` 仍非空） |
| `Table::botRng` | Bot 全类**只有一处**随机源（九种九牌 `n == 9` 时 `nextDouble() < 0.5`），必须与 Java 同一条流：`new java.util.Random(seedBase * 0x2545F4914F6CDD1DL + 0x9E3779B9L)`，**整场共享、惰性创建** |
| `danger.hpp` 改成 `dangerOf(...) → {level, score}` | teacher 有两处闸门看**级别**（`dealScore` 的"已经是 DANGEROUS 就不再抬档"、加杠危险闸门），而特征工程只看**分数**；两处必须同一份实现，否则"抬档"与"特征里的危险度"迟早各算一遍。分数仍从这个函数派生 ⇒ sidecar 逐字节不变（见下） |

**判据与实测**：

| 判据 | 结果 |
| --- | --- |
| `--selftest` | **135 项全绿** |
| 官方闸门 `1 1 teacher` / `5 2 teacher` / `30 0 teacher` | 全部 **PASS**（逐字节） |
| **200 场完整半庄**（私有目录：Java 24w vs C++ 16w） | **0/200 不一致**（两侧 2,325 小局 / 714 决策每场） |
| **独立复算**（本节作者另跑：30 场完整半庄，两侧 24w） | **30/30 逐字节一致**（346 小局 / 21,304 决策，两侧相等） |
| **tenhou 预设** 120 场 × 1 小局 | **120/120**，且覆盖 `kyuushu` 与 `botRng` 随机流（g17 真的走了九种九牌） |
| **13 个取舍计数器**（弃和 / 默听 / 无役拒绝鸣牌 / 开杠与各种"不开" / 顺位翻转 / 终局见逃 / 对手模型弃和） | 与 Java **逐项相等**（3 场与 30 场两批完全相同）—— 这是"判据真的接上了"而不是"看起来对"的证据 |
| 两条 1400 场都不触发的开杠闸门（`kan_refuse_four_kan` / `kan_refuse_danger`） | 用**定向构造探针**（`build/bot-probe.cpp` + Java 侧同批局面直接填 `HandState`）→ `shouldKan` 与四个计数器增量 **7/7 逐行相同**（含 c2-fourkan / c4-danger-kakan 与它们的反例） |
| 接口改造**没有动到其他策略** | 独立复核：`first` 30/30 · `pass` 10/10 · `random` 10/10 · `net:` 10/10 逐字节一致 |
| `danger.hpp` 重构**没有动到特征** | `trainer-features-parity.mjs` 在 teacher 轨迹上 **30 个 sidecar 逐字节一致** |
| **吞吐**（100 场完整半庄 × `teacher`） | C++ 1 worker **9,004** / 24 workers **94,998** 决策/秒 vs Java 24 workers **1,046** ⇒ **≈91×**（teacher 是 Java 侧最慢的策略：每个候选都要跑向听 + 危险度，而 C++ 的查表向听把这一块整个拿掉） |

**坑（都值得记）**：

- **稀有分支靠"计数器相等"而不是"跑得多"**：终局见逃在 `seed 31337` 的 200 场里只出现 **1** 条
  （`g146 step=670 seat=3 chosen=pass legal=["ron","pass"]`，`bakaze=S kyoku=4 tiles_left=41`），
  两侧计数器都是 1 —— 全目录里"给了 ron 却见逃"的行**恰好 1 条**，与计数器一致。
- **两条开杠闸门在 7 个种子 × 200 场 = 1,400 场里恒 0**（逐字节轨迹**覆盖不到**）→ 必须**构造局面**
  才验得到（上面的定向探针）。
- **并发验证会互相踩**：`tools/trainer-selfplay-parity.mjs` 曾**硬编码** `build/sp-java` / `sp-cpp`
  两个目录，两个进程同时跑就会互相覆盖（本项目真踩过一次：一次"30/50 不一致"的假红就是这么来的）。
  现已改成**按进程唯一化输出目录**（见 §4 的工具清单）。

---

## 7. 目录与构建

```
trainer/
├─ README.md            怎么构建、怎么跑、怎么对拍、**怎么接进 Python 工作流（MAHJONG_PRODUCER）**
├─ build.ps1            找 clang++（PATH → 常见安装位置）→ -O3 -march=native -std=c++23
│                       `-Clean` 只删构建产物（build/ 下的轨迹目录保留）；
│                       `-NoSelfTest` / `TRAINER_NO_SELFTEST=1` 跳过编后自检（进构建指纹 `.stamp`）
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
│  ├─ roundoptions.hpp  选项生成的第一层（可打牌 / 食替 / 立直后杠 / 吃搭子 = Java `RoundOptions`）
│  ├─ turnoptions.hpp   自家回合的询问内容（discard→riichi→tsumo→kan→kyuushu 的闸门与顺序）
│  ├─ claimoptions.hpp  鸣牌询问内容（ron/pon/kan/chi/pass + 赤五取法 = Java `Round.claimOptions`）
│  ├─ roundstate.hpp    `Round` 状态容器 + 构造 + 配牌（`setup()`）
│  ├─ counts.hpp        34 维计数 + 幺九判定的小工具
│  ├─ wall.hpp/.cpp     牌山 + 王牌（账与 Java 同构）
│  ├─ shanten.hpp/.cpp  查表向听（花色分组 + 位并行合并）+ "参考 DFS"（Java 逐行移植，自检用）
│  ├─ handeval.hpp/.cpp 进张 / 听牌 / 听牌形 / `HandEval.Snapshot` 的等价物
│  ├─ agari.hpp/.cpp    和了形分解（**枚举顺序是接口**）+ 听牌 + 和了判定
│  ├─ evaluator.hpp/.cpp 役种 / 符数 / 基本点 / 高点法（= Java `Evaluator`）
│  ├─ payments.hpp/.cpp 授受点数 + 不听罚符（= Java `Payments`）
│  ├─ yaku_codes.hpp/.cpp 役种名/档位/流局原因 → ASCII 码（= Java `YakuCodes`）
│  ├─ round.hpp/.cpp    **一整局的完整状态机**（摸打/鸣牌仲裁/立直/杠/和了/流局 = Java `Round.play()`）
│  ├─ table.hpp         **一整场的推进器** + 唯一的决策漏斗 `decideBot`（= Java `Table.playGame/decideBot`；
│  │                    唯一允许"绕过内置 Bot"的例外见 §6.15）
│  ├─ observation.hpp   观测（只含合法信息；字段白名单 = `PROTOCOL.md` §8.2）
│  ├─ options.hpp       询问内容 → 动作空间的展开（= Java `Action.enumerate`）
│  ├─ policies.hpp      `pass` / `first` / `random` / **`net:<权重文件>[@α][#T]`**（= Java `Policies`；
│  │                    `@α` 先验与 `teacher` 未实现 → 显式报错）
│  ├─ net.hpp/.cpp      **网络前向**：`net.bin` 加载 + `Features.state(607)`/`candidate(96)` 拼装 +
│  │                    argmax/温度采样（= Java `NeuralPolicy` + `Features`；`net` 子命令供对拍，§6.16）
│  ├─ jsonw.hpp         手写 JSON 写出器（键序 = 插入序、整数/定点、无浮点噪声）
│  ├─ trace.hpp/.cpp    轨迹记录器 `g*.jsonl`（三段式 + 丢行规则 = Java `TraceRecorder`）
│  ├─ selfplay.hpp/.cpp 自对弈编排（每场种子/策略实例/**K 线程抢场号**/`summary.json` = Java `SelfPlay`）
│  ├─ danger.hpp        危险度（teacher 与派生特征共用 = Java `Danger`）
│  ├─ obffeatures.hpp/.cpp 每个候选的派生特征（= Java `ObsFeatures`）
│  ├─ jsonscan.hpp/.cpp 读回 `g*.jsonl` 的极简扫描器（`features` / `net` 用）
│  ├─ features.hpp/.cpp `features <dir>`：轨迹 → `g*.feat.bin`（= Java `TraceFeatures`）
│  └─ main.cpp          CLI：wall / rng / rules / bench / score / settle / action / turnopts /
│                       selfplay / features / **net** / --selftest
└─ build/               产物（**不进仓库**，已 gitignore）
```

对拍工具（都在 `tools/`，命令见 §4）：`trainer-parity-check.mjs`（牌山）·`trainer-rule-parity.mjs`（向听/进张/听牌形）·
`trainer-score-parity.mjs`（打点）·`trainer-settle-parity.mjs`（精算/种子链）·`trainer-action-parity.mjs` +
`trainer-opts-parity.mjs`（动作空间/询问内容）·`trainer-selfplay-parity.mjs`（轨迹逐字节，支持 `--cpp-workers`）·
`trainer-workers-check.mjs`（并行不变性）·`trainer-features-parity.mjs` + `trainer-features-workers-check.mjs`
（sidecar）·**`trainer-net-parity.mjs` + `NetProbe.java`**（网络 logits，含 `--golden`/`--selfcheck`）·
`trainer-takeover-check.mjs`（整条 Python 链）·`selfplay-check.mjs`（独立数据集校验器）·`trainer-jsonl-diff.mjs`
（字段级差异定位）·`trainer-soak-check.mjs` / `trainer-perf.mjs`（大规模与计时）。

工具链：本机实测 **`clang++ 22.1.7`**（`D:\Program Files\LLVM\bin`）+ `cmake` + `.qt/Tools/Ninja`；
`build.ps1` 按「显式参数 → PATH → 常见安装位置」解析，找不到就报明确错误（不静默降级）。
