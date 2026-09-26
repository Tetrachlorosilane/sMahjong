# trainer/ —— 训练端 C++ 自对弈引擎

**这是什么**：把训练回路（数据生成）里原本由服务端 Java 承担的那部分算法，用现代 C++ 重写成
一个**独立的数据生成器**，用来把**数据速率**拉上去。
**红线**：**发布版服务端完全不变** —— `server/` 的 jar、协议、房间行为一个字都不动。

设计与对拍契约、里程碑、实测证据：**`docs/TRAINER-CPP.md`**（先读它）。

---

## 为什么是"重写"而不是"给服务端打补丁"

JFR 剖面（40 场 teacher 自对弈、5912 个样本、**按顶层帧**）：

| 占比 | 顶层帧 |
| --- | --- |
| **99.0%** | **`mahjong.rules.Shanten.dfs(...)`** |
| 0.3% | `mahjong.rules.Shanten.standard(...)` |
| 0.1% | `mahjong.rules.Agari.waits(...)` |
| 0.1% | `mahjong.game.Round.play()` |
| 其余 | 每个 ≤2 个样本（`HandEval.of` / `Danger.*` / `Bot.*` …） |

即"CPU 瓶颈"就是**向听 DFS 被调用得太多**：teacher 每个候选打牌要看向听，
`ObsFeatures.perCandidate` 又对 34 种进张各跑一次 `Shanten.min`。
所以路线是：**先把这条路径做快**（换算法为主、换语言为辅），
**其余部分按对拍契约逐块搬**（它们不到 1% 的时间，但决定数据是否可信）。

---

## 构建

```powershell
pwsh -File trainer\build.ps1            # → trainer\build\trainer.exe（增量；源码/头文件变过才重编）
pwsh -File trainer\build.ps1 -Clean     # 清 build/ 重编
pwsh -File trainer\build.ps1 -Dbg       # -O1 -g（⚠ 别用 -Debug：那是 PowerShell 的通用参数）
pwsh -File trainer\build.ps1 -San       # ASan/UBSan（跑对拍时用）
```

本机实测：**`clang++ 22.1.7`**（`D:\Program Files\LLVM\bin`）；`build.ps1` 按
「显式参数 → PATH → 常见安装位置」找编译器，找不到**明确报错**（不静默换编译器 ——
口径不同会让对拍结论失效）。编译用 `-std=c++23 -O3 -march=native`。

## 跑

```powershell
trainer\build\trainer.exe --selftest                      # 自身一致性（排列/可复现/赤五/岭上账）
trainer\build\trainer.exe wall 20260101 3 0               # 牌山+配牌+指示牌+岭上（JSON，供对拍）
trainer\build\trainer.exe rng  20260101 10                # java.util.Random.nextInt(136) 前 10 个
```

## 与 Java 对拍（**核心判据**）

```powershell
node tools\trainer-parity-check.mjs 24      # 24 组种子 × {aka 3/0} × {dealer 0/2} = 96 组
node tools\trainer-parity-check.mjs 128     # 512 组（慢：每组要起一个 JVM，约 2~3 分钟）
```

对拍用的是 **`tools/WallProbe.java`** —— Java 侧**只读探针**（只 import jar 的公开 API，
不修改服务端）。比较内容：**136 张牌山 + 四家配牌（含庄家第 14 张）+ 表/里宝指示牌 + 4 张岭上**，
逐个整数比对。

⚠ 沙箱坑：Node 不能用管道接子进程输出（`spawnSync … EPERM`），所以脚本让子进程
**直接写文件描述符**再读文件（等价于重定向）。

---

## 现状（M0 已完成）

| 里程碑 | 状态 | 内容 |
| --- | --- | --- |
| **M0 牌山/配牌** | ✅ | `java.util.Random` + `Collections.shuffle` 逐位复刻；`Wall` 的账（可摸 122 / 王牌 14 = 岭上 4 + 表宝 5 + 里宝 5）；配牌顺序与 `Round.setup()` 同序；**对拍 96/96 组逐整数一致** |
| **M1 向听/进张/和了** | ⏳ 下一步（收益最大） | 查表式向听 + `Agari` + `HandEval` 8 维派生；与 Java 百万级随机手牌逐值相等 + ≥10× 提速 |
| M2 规则与牌局流程 | ⏳ | `Round`/`Evaluator`/`Payments`/`Danger`；同种子 → 轨迹**逐字节相同** |
| M3 策略与网络 | ⏳ | teacher / first/pass/random / `NeuralPolicy` 前向；轨迹直接喂通现有 Python 管线 |
| M4 性能与工程化 | ⏳ | 线程池、AVX2 向听表、批量前向；**同核数下决策/秒 ≥ Java 3×** |

**非目标**：网络对战、房间/身份/投票、回放与牌谱导出、客户端相关的一切，
以及除 **M.League 默认预设**以外的规则预设（训练只跑这一套，见 `docs/TRAINING.md` §0）。

## 目录

```
trainer/
├─ build.ps1        构建（见上）
├─ src/
│  ├─ java_rand.hpp java.util.Random + Collections.shuffle 的逐位等价实现
│  ├─ tiles.hpp     牌码编码（与 Java Tiles 同一套：id = (kind<<2)|copy，赤五 = copy 0）
│  ├─ wall.hpp/.cpp 牌山 + 王牌（两个账分开记：开杠动 liveEnd、岭上摸牌动 rinshan）
│  └─ main.cpp      CLI：wall / rng / --selftest
└─ build/           产物（已 gitignore）
```
