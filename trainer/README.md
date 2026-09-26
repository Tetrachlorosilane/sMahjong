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
pwsh -File trainer\build.ps1             # → trainer\build\trainer.exe（增量；源码/头文件变过才重编）
pwsh -File trainer\build.ps1 -Clean      # 清**构建产物**重编（⚠ 不删 build/ 下的轨迹目录）
pwsh -File trainer\build.ps1 -Dbg        # -O1 -g（⚠ 别用 -Debug：那是 PowerShell 的通用参数）
pwsh -File trainer\build.ps1 -San        # ASan/UBSan（跑对拍时用）
pwsh -File trainer\build.ps1 -NoSelfTest # 编完**不跑** --selftest（工作流/CI 省时间）
                                          # 等价环境变量：$env:TRAINER_NO_SELFTEST = '1'
```

⚠ `-NoSelfTest` / `TRAINER_NO_SELFTEST=1` 会进**构建指纹**（`build\.stamp` 里的 `selftest=True/False`），
所以"带不带它"会触发一次重编 —— 这是**刻意**的：一眼就能看出当前 exe 到底是"编完自检过"的哪一代。
环境变量只在**没给** `-NoSelfTest` 时才被看（`-NoSelfTest:$false` 掰不回来）。

本机实测：**`clang++ 22.1.7`**（`D:\Program Files\LLVM\bin`）；`build.ps1` 按
「显式参数 → PATH → 常见安装位置」找编译器，找不到**明确报错**（不静默换编译器 ——
口径不同会让对拍结论失效）。编译用 `-std=c++23 -O3 -march=native`。

## 跑

```powershell
trainer\build\trainer.exe --selftest                      # 自身一致性（牌山 + 快表向听 vs 参考 DFS + 听牌形定点）
trainer\build\trainer.exe wall 20260101 3 0               # 牌山+配牌+指示牌+岭上（JSON，供对拍）
trainer\build\trainer.exe rng  20260101 10                # java.util.Random.nextInt(136) 前 10 个
trainer\build\trainer.exe rules <corpus> shanten <out>    # 语料 → 向听（供对拍）
trainer\build\trainer.exe rules <corpus> of|discard <out> # 语料 → 进张/听牌形快照（供对拍）
trainer\build\trainer.exe bench <corpus> of 20            # 同语料跑 20 遍并计时（不写输出）
trainer\build\trainer.exe score <corpus> <out>            # 语料 → 役种/符/点数/授受（供对拍）
trainer\build\trainer.exe settle <corpus> <out>           # 语料 → 顺位点/余棒/连庄判据/种子链（供对拍）
trainer\build\trainer.exe action <corpus> <out>           # 语料 → 动作键/下标/回包/落位（供对拍）
trainer\build\trainer.exe turnopts <javaCorpus> <out>     # 真实牌局的自家回合询问 → 选项文本（供对拍）

# ★ 自对弈（训练端的主要用途）：写出与 Java `--selfplay` **逐字节相同**的轨迹
trainer\build\trainer.exe selfplay 300 --workers 8 --policy first --seed 20260101 --hands 0 --out DIR
trainer\build\trainer.exe features DIR --workers 8        # 轨迹目录 → 派生特征 sidecar g*.feat.bin
trainer\build\trainer.exe net NET.BIN TRACE.jsonl …       # 逐条 decision 行打印网络 logits（与 tools/NetProbe.java 对拍）
```

`selfplay` 的开关（`trainer.exe selfplay --help`）：

| 开关 | 含义 | 缺省 |
| --- | --- | --- |
| `--workers K` | K 个线程抢场号 `g`；**并行只改调度**，`g*.jsonl` 与 `--workers 1` 逐字节相同 | **1**（不按核数自动并发），钳制到 `[1, games]` |
| `--policy P` | 四家策略，逗号分隔：`teacher`（缺省）/ `pass` / `first` / `random` / **`net:<权重文件>[@α][#T]`**（`#T` = 从 `softmax(logits/T)` 采样，`T≤0` = argmax） | 缺省 = `teacher`（已实现）；`@α`（teacher 先验）**显式报错** |
| `--seed S` | 基准种子；每场种子只与 `(S, 场号)` 有关 | `20260101` |
| `--hands H` | 每场最多 H 小局 | **0 = 完整半庄** |
| `--out DIR` | 轨迹输出目录（`g<场号>.jsonl` + `summary.json`）；不给就只算不写 | — |
| `--sample K` / `--no-claims` / `--rotate` / `--preset X` | 与 Java 同名开关同义 | `1` / 关 / 关 / `mleague` |

`features` 的 `--workers` 缺省是 **核数 × 3/4**（= Java `TraceFeatures.defaultWorkers`；与 `selfplay`
的缺省 1 不同是刻意的，见 `docs/TRAINER-CPP.md` §6.14）。

## 与 Java 对拍（**核心判据**）

```powershell
node tools\trainer-parity-check.mjs 24      # M0：24 组种子 × {aka 3/0} × {dealer 0/2} = 96 组
node tools\trainer-parity-check.mjs 128     # M0：512 组（慢：每组要起一个 JVM，约 2~3 分钟）

node tools\trainer-rule-parity.mjs all      # M1：向听 / 进张 / 听牌形（默认 20 万 + 2 万 + 5 千行）
node tools\trainer-rule-parity.mjs shanten 1000000        # 100 万手向听
node tools\trainer-rule-parity.mjs of 20000 --random-only # 只量"进张"那条路
node tools\trainer-rule-parity.mjs of 20000 --tenpai-only # 只量"听牌形"那条路

node tools\trainer-score-parity.mjs         # M2：役种 / 符数 / 打点 / 授受（默认 20 万行）
node tools\trainer-settle-parity.mjs        # M2：顺位点精算 / 余棒 / 连庄判据 / 种子链（默认 2 万）
node tools\trainer-action-parity.mjs        # M2：动作键 / 下标 / 回包 / 落位（369 行，含非法键）
node tools\trainer-opts-parity.mjs 12 teacher 20   # M2：自家回合询问内容（真实牌局，默认 4 局 × 3 策略）
```

⚠ `trainer-opts-parity.mjs` 的**采样**参数是 `<每场小局数> <策略逗号列表> <场数>`：`teacher` 才是最
"会打"的那一个（会立直、会杠、会副露），`first/pass/random` 主要用来压"只有打牌"的那条路。

对拍用的是五个 Java 侧**只读探针**（只 import jar 的公开 API，不修改服务端）：

- **`tools/WallProbe.java`**（M0）：136 张牌山 + 四家配牌（含庄家第 14 张）+ 表/里宝指示牌 + 4 张岭上，
  **逐个整数**比对；
- **`tools/RuleProbe.java`**（M1）：`Shanten.min` / `HandEval.of` / `HandEval.afterDiscard`，
  **逐行逐字段**比对（含两个 34 维数组的 FNV-1a 哈希）；
- **`tools/ScoreProbe.java`**（M2）：`Rules.applyPreset` + `Evaluator.evaluate` + `Payments.compute`，
  **逐行 17 个字段**比对（含和了形签名与**有序**役种列表）；
- **`tools/SettleProbe.java`**（M2）：`RoundScoring.*` + `RoundClaims.*` + `SelfPlay.seedFor` ——
  顺位点按**原始位模式**比对（浮点十进制格式化的差异也算不同），另含鸣牌仲裁判据；
- **`tools/ActionProbe.java`**（M2）：`Action` 的键 / 固定头下标 / 回包 / 落位 —— 轨迹里的
  `legal` / `chosen` 就是这些键。

⚠ 沙箱坑：Node 不能用管道接子进程输出（`spawnSync … EPERM`），所以脚本让子进程
**直接写文件描述符**再读文件（等价于重定向）。

### 自对弈 / 特征 sidecar 的对拍闸门（M2 之后的**硬判据**）

```powershell
node tools\trainer-selfplay-parity.mjs 300 2 first 20260101 --cpp-workers 8  # 轨迹 g*.jsonl + summary 逐字节
node tools\trainer-workers-check.mjs 300 2 first 20260101 1 8               # 并行不变性：C++ 1 vs 8 逐字节
node tools\trainer-features-parity.mjs <dir> 24 [--self-check]              # sidecar 逐字节（--self-check 两边都跑 Java）
node tools\trainer-features-workers-check.mjs <dir> 1 8                     # sidecar 串行 vs 并行逐字节
node tools\trainer-jsonl-diff.mjs <javaDir> <cppDir> [g]                    # 不一致时做**字段级**定位
node tools\trainer-features-synth.mjs                                       # 合成边界语料（真实轨迹到不了的支路）
node tools\trainer-net-parity.mjs <net.bin> <轨迹目录|jsonl>                # 网络 logits：Java ↔ C++ 逐元素（tol 1e-4）
node tools\trainer-net-parity.mjs --golden                                  # 仓库内 golden 小网夹具（maxΔ 应为 0）
node tools\trainer-net-parity.mjs <net.bin> <corpus> --selfcheck            # 闸门自检（负向对照必须报 FAIL）
node tools\trainer-takeover-check.mjs 1 1 pass 20260101                     # 整条链：采集 → 比 → sidecar → 比 → dataset build
node tools\selfplay-check.mjs <dir>                                         # 独立数据集校验器（Python 侧同一份）
```

⚠ `trainer-selfplay-parity.mjs` 的位置参数是 `<场数> <小局数> <策略> <种子>`（**不是** workers），
并行只给 C++ 侧加 `--cpp-workers K`，Java 参考侧恒 `--workers 1`。
**场数不够会漏 bug**：`--hands 0`（完整半庄）下"食替退化"这类局面**实测 `first` 约 1/167 场**才出现一次
（`random` 下 0/500 —— 它**跟策略走**），
所以完整半庄验收**至少 200 场**（`docs/TRAINER-CPP.md` §6.15 有原案）。

---

## 接进 Python 训练工作流（`MAHJONG_PRODUCER`）

**开关**：`MAHJONG_PRODUCER=java|cpp`（**缺省 `java`**）—— 采集（`online.py` / `dagger.py` 的 4 处调用）
与派生特征（`--features`）两处都走 `python/mahjong_ml/producer.py`，**命令由它自己组**，所以
"换生产者"只是换一个环境变量，Python 侧一行不改。

```powershell
$env:MAHJONG_PRODUCER = 'cpp'
$env:TRAINER_NO_SELFTEST = '1'      # 工作流里省掉"编完自检"（只在构建时生效）
cd python; .\.venv\Scripts\python.exe -m mahjong_ml.dataset build <dir> <out>
```

**判据不是"能跑"，是"同种子产物逐字节相同"**：`g*.jsonl`、`g*.feat.bin`、`summary.json`（除计时/核数）
三样都要与 Java 一致 —— 用上面的 `trainer-selfplay-parity` / `trainer-features-parity` / `takeover-check` 守。
实测（2026-09）：**200 场完整半庄（155,464 决策）Java 24 workers vs C++ 24 workers 逐字节 200/200**，
sidecar 200/200，`selfplay-check` DATASET PASS。

**已经支持 / 还不支持**（⚠ 不支持的一律**显式报错**，绝不悄悄降级成另一种数据集）：

| 能力 | Java | C++ |
| --- | --- | --- |
| `--policy pass` / `first` / `random` | ✅ | ✅（`random` 的 `java.util.Random` 逐位复刻） |
| `--policy net:<权重文件>` / `net:…@0#<T>`（温度采样） | ✅ | ✅（logits maxΔ=1e-6、argmax 全同；端到端逐字节，见 `docs/TRAINER-CPP.md` §6.16） |
| `--policy teacher` | ✅ | ✅（1,443 行五层取舍逐句移植；**200 场完整半庄 0/200 不一致**、13 个取舍计数器与 Java 逐项相等、24 核约 **91×**，见 `docs/TRAINER-CPP.md` §6.17） |
| `--policy net:…@<α>`（teacher 先验，P5b 混合臂） | ✅ | ❌ **显式报错**（先验要调 `Bot.decide`——现在有了，但那一支尚未接） |
| `--teacher-label`（DAgger 标注） | ✅ | ❌ 未实现（`producer.py` 在 cpp 下直接拒绝） |
| `--sample` / `--hands` / `--rotate` / `--no-claims` / `--preset` | ✅ | ✅（`--sample` 已逐字节验过） |

**C++ 侧现在覆盖**：`pass` / `first` / `random` / `net:<权重文件>[@0][#T]` / **`teacher`** + `--features` + `--sample` 等，
都已逐字节验过。**还差两项**：`net:…@<α>`（P5b 混合臂，要调 `Bot.decide`）与 `--teacher-label`（DAgger 标注）。
缺省仍是 `java`（发布版权威实现），但 **P5 的联赛世代已可整条切到 `cpp`**（见 `docs/TRAINER-CPP.md` §6.18）。

**日志里那一行 `[trainer] 引擎兜底出牌…` 是什么**：某些局面下 Java 引擎自己的"可打牌"集合会是**空**的
（食替把暗手每种牌都禁打），两侧状态一致，Java 会退回内置 Bot 并被强制兜底出牌、**且这条决策不进轨迹**
（所以 `summary.json` 的 `decisions` 比数据集里的决策行**多 1**）。C++ 复现同一条兜底链，并在 stderr
打一行说明 —— 它**不是错误**，但值得计数（实测：`first` 约 1/167 场、`random` 0/500）。详见 `docs/TRAINER-CPP.md` §6.15。

---

## 现状（M0–M2、M4 已完成；M3 部分完成）

| 里程碑 | 状态 | 内容 |
| --- | --- | --- |
| **M0 牌山/配牌** | ✅ | `java.util.Random` + `Collections.shuffle` 逐位复刻；`Wall` 的账（可摸 122 / 王牌 14 = 岭上 4 + 表宝 5 + 里宝 5）；配牌顺序与 `Round.setup()` 同序；**对拍 512/512 组逐整数一致** |
| **M1 向听/进张/和了** | ✅ | 查表式向听（花色分组 + **位并行合并**）+ 进张/听牌/听牌形；**100 万手向听 + 21.7 万手派生评估逐字段一致**；向听 **31.5×** / 进张 **19×** / 听牌形 **44×**（纯计算口径，见 `docs/TRAINER-CPP.md` §6.1） |
| **M2 规则与打点** | ✅ 全部完成 | ① 役种/符数/基本点/授受/不听罚符：**20 万行逐字段一致、61 个役种码全覆盖**（§6.2）；② 种子链 + 顺位点精算/连庄判据/终局余棒：**21 万行逐位一致**（§6.3）；③ 动作键/下标/回包/落位：**369 行逐字符一致**（§6.4）；④ 鸣牌仲裁判据（等级/压过/收工/回包认领）：**1.26 万行一致**（§6.5）；⑤ 振听记账（三种振听，用**真实 Round** 驱动）：**354 行一致**（§6.6）；⑥ 可见牌统计 + 和了形纯判断：**1.37 万行一致**（§6.8）；⑦ 配牌顺序的假阳性已修正（探针改用真实 `Round`）并重验 **512/512**（§6.7）；⑧ `Round` 状态容器 + 配牌（`menzen` 全真 / 牌山 122→69 / 庄家第 14 张）：**1.4 万行一致，含 260 行 `rinit`**（§6.9）；⑨ 选项生成第一层 `RoundOptions`（可打牌 / 食替 / 立直后杠 / 吃搭子）：**320 行一致**（§6.10）；⑩ 自家回合 `turnOptions`（选项顺序 + riichi/tsumo/kan 闸门）：**11.6 万次真实询问逐字符一致**（§6.11/§6.12，含鸣牌段）；⑪ `Round` 摸打/鸣牌/流局循环：**完整半庄与 Java 逐字节一致**（300×2 / 100×8 / 150 完整半庄 / **200 场完整半庄 200/200**，另 `random` 50 场、`pass` 100 场同样 100%，**soak 500 场 × 2 策略各 500/500**；§6.13/§6.14/§6.15）。⚠ 完整半庄验收**至少 200 场**（soak 已到 **500 场 × 2 策略**）：食替退化局面实测 `first` 约 1/167 场、`random` 0/500（§6.15） |
| M3 策略与网络 | 🔄 大部分完成 | `first`/`pass`/`random` ✅ 与 Java 逐字节一致（含 `random` 的 `java.util.Random` 逐位复刻）；**`net:<权重文件>[@α][#T]` 前向 ✅**：golden 夹具 Java↔C++ **maxΔ=0（逐位相同）**、真实权重（266,753 参数）36,181 条决策 **maxΔ=1e-6 且 argmax 全同**、端到端 `net:` **100 场完整半庄逐字节 100/100（24w 同核数 20.1×）**、`@0#1.0` 采样 20 场逐字节 20/20（详见 `docs/TRAINER-CPP.md` §6.16）；轨迹 + 派生特征直接喂通现有 Python 管线 ✅；⏳ 只剩 `net:` 的 **`@α` 先验**（P5b 混合臂）与 `--teacher-label`（DAgger 标注）未接 —— 两者都**显式报错**。**`teacher` 已移植**：1,443 行五层取舍逐句移植，**200 场完整半庄 0/200 不一致**、13 个取舍计数器与 Java 逐项相等、tenhou 预设 120/120（覆盖九种九牌 + `botRng`）、24 核吞吐约 **91×**（§6.17） |
| M4 性能与工程化 | ✅ 并行已落地 | `--workers` 真并行（`selfplay` + `features`，**产出逐字节不变**：C++ 1 vs 24 workers 200/200）✅；**同 24 核下决策/秒 ≈ Java 的 16.7–18.7×**（100 / 200 场完整半庄，目标 3× 已超 5 倍，§6.14）✅；⏳ AVX2 向听表、批量前向未做（§6.14 说明了为什么 `-flto` 也不留） |

**非目标**：网络对战、房间/身份/投票、回放与牌谱导出、客户端相关的一切，
以及除 **M.League 默认预设**以外的规则预设（训练只跑这一套，见 `docs/TRAINING.md` §0）。

## 目录

```
trainer/
├─ build.ps1          构建（见上）
├─ src/
│  ├─ java_rand.hpp   java.util.Random + Collections.shuffle 的逐位等价实现
│  ├─ tiles.hpp       牌码编码（与 Java Tiles 同一套：id = (kind<<2)|copy，赤五 = copy 0）
│  ├─ meld.hpp        副露（吃/碰/大明杠/暗杠/加杠）
│  ├─ rules.hpp       规则集 + 三套预设（⚠ 默认 = M.League 预设）
│  ├─ seed.hpp        种子链（mixSeed / 每局种子 / SelfPlay.seedFor / 顺位）
│  ├─ roundscoring.hpp/.cpp 顺位点精算 + 连庄/本场/和了止/延长战/终局余棒
│  ├─ action.hpp/.cpp 动作空间（键/下标/回包/落位）
│  ├─ roundclaims.hpp 鸣牌仲裁判据（等级/压过/收工/回包认领）
│  ├─ furiten.hpp     振听记账（三种振听 + 唯一记账点）
│  ├─ visible.hpp     可见牌统计 + 和了形纯判断（观测特征的底座）
│  ├─ roundstate.hpp  `Round` 状态容器 + 构造函数 + `setup()` 配牌（M2 打牌循环的地基）
│  ├─ roundoptions.hpp 选项生成的第一层：可打牌 / 食替 / 立直后杠 / 吃搭子（= Java `RoundOptions`）
│  ├─ turnoptions.hpp 自家回合的询问内容：discard→riichi→tsumo→kan→kyuushu 的闸门与顺序
│  ├─ claimoptions.hpp 鸣牌询问内容：ron/pon/kan/chi/pass + 赤五取法（= Java `Round.claimOptions`）
│  ├─ counts.hpp      34 维计数 + 幺九判定
│  ├─ wall.hpp/.cpp   牌山 + 王牌（两个账分开记：开杠动 liveEnd、岭上摸牌动 rinshan）
│  ├─ shanten.hpp/.cpp 查表向听（位并行合并）+ 参考 DFS（自检交叉验证）
│  ├─ handeval.hpp/.cpp 进张 / 听牌 / 听牌形 / 快照（= Java `HandEval`）
│  ├─ agari.hpp/.cpp  和了形分解（枚举顺序是接口）+ 听牌 + 和了判定
│  ├─ evaluator.hpp/.cpp 役种 / 符数 / 基本点 / 高点法（= Java `Evaluator`）
│  ├─ payments.hpp/.cpp 授受点数 + 不听罚符（= Java `Payments`）
│  ├─ yaku_codes.hpp/.cpp 役种名 → ASCII 码（= Java `YakuCodes`）
│  ├─ round.hpp/.cpp  一整局的完整状态机（摸打/鸣牌仲裁/立直/杠/和了/流局 = Java `Round.play()`）
│  ├─ table.hpp       一整场的推进器 + **唯一的决策漏斗** `decideBot`（= Java `Table.playGame/decideBot`）
│  ├─ trace.hpp/.cpp  轨迹记录器（`g*.jsonl` 三段式：decision → hand → game = Java `TraceRecorder`）
│  ├─ selfplay.hpp/.cpp 自对弈编排（每场种子/策略实例/每场一文件/`summary.json` = Java `SelfPlay`）
│  ├─ observation.hpp 观测（只含合法信息；字段白名单见 PROTOCOL §8.2）
│  ├─ options.hpp     询问内容 → 动作空间的展开（`Action.enumerate`）
│  ├─ policies.hpp    `pass` / `first` / `random`（teacher / net 未实现 → 显式报错）
│  ├─ jsonw.hpp       手写 JSON 写出器（键序 = 插入序、无浮点噪声、CRLF 由 `trace.cpp` 定）
│  ├─ danger.hpp      危险度（teacher 与特征都用 = Java `Danger`）
│  ├─ obffeatures.hpp/.cpp 每个候选的派生特征（= Java `ObsFeatures`）
│  ├─ jsonscan.hpp/.cpp    读回 `g*.jsonl` 的极简扫描器（`features` 用）
│  ├─ features.hpp/.cpp    `features` 子命令：轨迹 → `g*.feat.bin`（= Java `TraceFeatures`）
│  └─ main.cpp        CLI：wall / rng / rules / bench / score / settle / action / turnopts /
│                     selfplay / features / --selftest
└─ build/             产物（已 gitignore）。**轨迹目录也在 `build/` 下时，`-Clean` 只删构建产物**
```
