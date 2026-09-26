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
```

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

---

## 现状（M0 / M1 已完成，M2 进行中）

| 里程碑 | 状态 | 内容 |
| --- | --- | --- |
| **M0 牌山/配牌** | ✅ | `java.util.Random` + `Collections.shuffle` 逐位复刻；`Wall` 的账（可摸 122 / 王牌 14 = 岭上 4 + 表宝 5 + 里宝 5）；配牌顺序与 `Round.setup()` 同序；**对拍 512/512 组逐整数一致** |
| **M1 向听/进张/和了** | ✅ | 查表式向听（花色分组 + **位并行合并**）+ 进张/听牌/听牌形；**100 万手向听 + 21.7 万手派生评估逐字段一致**；向听 **31.5×** / 进张 **19×** / 听牌形 **44×**（纯计算口径，见 `docs/TRAINER-CPP.md` §6.1） |
| **M2 规则与打点** | 🔄 打点内核 ✅ / 精算与种子链 ✅ / 动作空间 ✅ / 鸣牌仲裁判据 ✅ | ① 役种/符数/基本点/授受/不听罚符：**20 万行逐字段一致、61 个役种码全覆盖**（§6.2）；② 种子链 + 顺位点精算/连庄判据/终局余棒：**21 万行逐位一致**（§6.3）；③ 动作键/下标/回包/落位：**369 行逐字符一致**（§6.4）；④ 鸣牌仲裁判据（等级/压过/收工/回包认领）：**1.26 万行一致**（§6.5）；⑤ 振听记账（三种振听，用**真实 Round** 驱动）：**354 行一致**（§6.6）；⑥ 可见牌统计 + 和了形纯判断：**1.37 万行一致**（§6.8）；⑦ 配牌顺序的假阳性已修正（探针改用真实 `Round`）并重验 **512/512**（§6.7）；⑧ `Round` 状态容器 + 配牌（`menzen` 全真 / 牌山 122→69 / 庄家第 14 张）：**1.4 万行一致，含 260 行 `rinit`**（§6.9）；⑨ 选项生成第一层 `RoundOptions`（可打牌 / 食替 / 立直后杠 / 吃搭子）：**320 行一致**（§6.10）；⑩ 自家回合 `turnOptions`（选项顺序 + riichi/tsumo/kan 闸门）：**11.6 万次真实询问逐字符一致**（§6.11/§6.12，含鸣牌段）；`Round` 摸打/鸣牌/流局循环 ⏳ |
| M3 策略与网络 | ⏳ | teacher / first/pass/random / `NeuralPolicy` 前向；轨迹直接喂通现有 Python 管线 |
| M4 性能与工程化 | ⏳ | 线程池、AVX2 向听表、批量前向；**同核数下决策/秒 ≥ Java 3×** |

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
│  └─ main.cpp        CLI：wall / rng / rules / bench / score / settle / action / --selftest
└─ build/             产物（已 gitignore）
```
