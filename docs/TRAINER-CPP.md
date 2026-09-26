# 训练端 C++ 自对弈引擎（`trainer/`）—— 设计与对拍契约

> **目标**：把训练回路（数据生成）里原本由服务端 Java 承担的那部分算法，用现代 C++ 重写成一个
> **独立的**数据生成器，把数据速率拉上去。
> **红线**：**发布版服务端完全不变** —— `server/` 的 jar、协议、房间行为一个字都不动；
> 训练侧要换，就换"谁在采数据"，不换"线上跑什么"。

状态：**M0（牌山/洗牌逐字节对拍）已落地**；M1–M4 见 §5。

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
- 配牌顺序与 `Round.setup()` 一致：`13 巡 × 4 家（从庄家起）` → 庄家第 14 张（`openingTile`）→ `finishDealing`；
- `tools/WallProbe.java`（只读探针）+ `tools/trainer-parity-check.mjs`：
  **同一 (seed, aka, dealer) → 136 张牌山 + 四家配牌 + 表/里宝指示牌 + 4 张岭上，逐个整数比对**。

跑法（`trainer/README.md` 有完整说明）：

```powershell
pwsh -File trainer\build.ps1
node tools\trainer-parity-check.mjs 64        # 64 组种子 × 4 种 aka/dealer 组合
```

结果：**PASS（0 处不一致）** —— 见 §6 的实测输出。

---

## 5. 里程碑（M1 起待做）

| 里程碑 | 内容 | 完成判据 |
| --- | --- | --- |
| **M1 向听/进张/和了**（**收益最大的一步**） | 查表式向听（suits 表 + 4 面子 1 雀头）、`Agari`（和了形/听牌/进张/好形听）、`HandEval` 的 8 维派生特征 | ① 与 Java `Shanten.min`/`Agari.waits`/`HandEval.of` 在**百万级随机手牌 + 全部边界形**上逐值相等；② `tools/BenchForward.java` 那套微基准在 C++ 上重跑，**向听/进张路径 ≥ 10×** |
| **M2 规则与牌局流程** | `Tiles/Meld/Rules`、`Round`（摸打/鸣牌仲裁/立直/杠/流局/连庄）、`Evaluator`（役种/符数/点数）、`Payments`、`Danger` | 同 (seedBase, 策略串) → C++ 的 `g*.jsonl` 与 Java **逐字节相同**（先 100 场，再 2000 场） |
| **M3 策略与网络** | `teacher`（五层取舍，与 Java 逐决策一致）、`first/pass/random`、`NeuralPolicy` 前向（float32 权重直读）、`PolicyFactory` 的每局实例化语义 | ① teacher 决策序列与 Java 相同（同 seed 同场）；② 网络 logits 与 Java 逐元素 ≤1e-4（golden 夹具）；③ `selfplay-check.mjs` PASS |
| **M4 性能与工程化** | 线程池（`--workers`）、AVX2 向听表、批量前向（同巡多候选一次 GEMM）、轨迹写入与 `summary.json`、CLI 与 `python/mahjong_ml/online.py` 对接 | ① **同等核数下决策/秒 ≥ Java 的 3×**（基线：24 核 1172 决策/秒、单核 88）；② 产出数据直接喂通 P3/P4 管线不改一行 Python |

**非目标**（明确不做，避免范围失控）：网络对战（TCP/NDJSON）、房间/等待室/身份/投票、
回放与牌谱导出、客户端相关的一切、以及**除 M.League 默认预设以外**的规则预设
（训练只跑这一套，见 `docs/TRAINING.md` §0；真要换口径再单独加）。

---

## 6. 实测（M0）

```
$ pwsh -File trainer\build.ps1
==> 编译器   D:\Program Files\LLVM\bin\clang++.exe
==> clang version 22.1.7
==> 源文件   2 个 .cpp（另有 3 个头文件参与指纹）→ trainer\build\trainer.exe
  [ok]   洗牌结果是 0..135 的排列
  [ok]   同 seed 两次洗牌逐张相同
  [ok]   aka=0 → 无赤五
  [ok]   aka=3 → 恰好 3 张赤五
  [ok]   配牌后余 69 张可摸
  [ok]   开杠 → tilesLeft 减 1
  [ok]   岭上摸牌不动 tilesLeft
  [ok]   岭上摸牌只减 rinshanLeft
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
2. **配牌顺序不是"每人 13 张发完再给庄家"**：`Round.setup()` 是
   `13 巡 × 4 家（从庄家起）` → 庄家第 14 张（`openingTile` 同时是"本次摸到的牌"）→ `finishDealing()`；
   且手牌按 `Round.compareTile` 排序（**先 kind，再"赤五在前"，最后比 id**）。
   探针第一版漏了"把第 14 张加进庄家手牌"，对拍当场红。

⚠ **沙箱坑**：Node 不能用管道接子进程输出（`spawnSync … EPERM`，命名管道被禁）。
对拍脚本因此让子进程**直接写文件描述符**（`stdio: ['ignore', fd, 'inherit']`）再读文件 ——
等价于重定向，不碰管道。

---

## 7. 目录与构建

```
trainer/
├─ README.md            怎么构建、怎么跑、怎么对拍
├─ build.ps1            找 clang++（PATH → 常见安装位置）→ -O3 -march=native -std=c++23
├─ src/
│  ├─ java_rand.hpp     java.util.Random + Collections.shuffle 的逐位等价
│  ├─ tiles.hpp         牌码/kind/copy/赤五（与 Java Tiles 同一套编码）
│  ├─ wall.hpp/.cpp     牌山 + 王牌（账与 Java 同构）
│  └─ main.cpp          CLI：wall / shanten / selfplay（M1 起逐步填）
└─ build/               产物（**不进仓库**，已 gitignore）
```

工具链：本机实测 **`clang++ 22.1.7`**（`D:\Program Files\LLVM\bin`）+ `cmake` + `.qt/Tools/Ninja`；
`build.ps1` 按「显式参数 → PATH → 常见安装位置」解析，找不到就报明确错误（不静默降级）。
