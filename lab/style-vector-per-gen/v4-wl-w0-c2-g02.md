[自检③] 正常命令行含 --rotate-perm：True；故意去掉后 assert：触发（自检③失败：命令行缺 --rotate-perm：['trainer.exe', 'selfplay', '1', '--workers', '1', '--policy', 'teacher', '--seed', '1', '--out', '.']）
# 风格向量读数（从**轨迹**算；⛔ 不是判决判据 —— 判决只看 `mahjong_ml/v4/gate.py`）

- 输入目录：S:\mahjong-training\w-ladder\traces\v4-wl-w0-c2-g02（共 1 个；按策略名**先累加整数分子/分母再相除**合并）
- 轨迹 obs 最低版本：3（`avg_riichi_turn` 需要 obs v3 的 `riichi_turn`）

### 定义表（每维的分子 / 分母 / 来源字段）

| 维度 | 含义 | 分子 | 分母 | 来源字段 | 口径注 |
| --- | --- | --- | --- | --- | --- |
| `riichi_rate` | 立直率 | 该策略**宣言过立直**的小局数（同一小局宣言多次只算 1） | 该策略打过的小局数（= seat_hands） | `obs.riichi[seat] / obs.riichi_turn[seat] > 0 / chosen 以 riichi: 开头（三者取或）` | 分母含流局与途中流局；宣言后放铳/被荣和照样计入（它确实立直了）。 |
| `meld_rate` | 副露率 | 该策略**有副露**的小局数（吃 / 碰 / 暗杠 / 加杠 / 大明杠任一，同局多次只算 1） | 该策略打过的小局数（= seat_hands） | `obs.melds[seat] 非空（四家副露是公开状态）/ chosen 以 chi: / pon: / kan: 开头 / events 里的 meld·kan` | 暗杠也算「有副露」（口径 = 吃碰杠任一次）；它不是门前清，但门清与否另有 menzen 位。 |
| `win_rate` | 和了率 | 该策略和了的小局数（多家荣和时只算离放铳者最近那家 = hand.winner 同口径） | 该策略打过的小局数（= seat_hands） | `hand 行：agari == true 且 winner == 该席` | ⚠ 必须与 summary.by_policy.win_rate 逐项相等（自检 ①）；流局满贯不计（winner = -1）。 |
| `deal_rate` | 放铳率 | 该策略放铳的小局数 | 该策略打过的小局数（= seat_hands） | `hand 行：loser == 该席` | ⚠ 必须与 summary.by_policy.deal_in_rate 逐项相等（自检 ①）。 |
| `avg_win_score` | 平均打点 | 该策略所有和了小局的 delta[winner] 之和（**含本场棒与供託**，与 summary 同一本账） | 该策略的和了次数（= wins，**不是** seat_hands） | `hand 行：delta[winner]（= trainer/src/selfplay.cpp 的 winPoints）` | ⚠ 必须与 summary.by_policy.avg_win_score 逐项相等（自检 ①）；0 次和了时输出 null（不写 0 —— 那是「没有数」不是「打点 0」）。 |
| `avg_win_turn` | 平均和了巡目 | 每个和了小局的「和了巡目」之和 | **能定位到和了巡目的**和了次数（覆盖不到的在报告里单独计数，不塞进分母） | `该席在和了那一小局里 chosen ∈ {tsumo, ron} 的决策行的 obs.player_draws` | 巡目 = 和了者**到和了那一刻为止已摸过几次牌**（PROTOCOL §8.2 与 riichi_turn 同一把尺子）：自摸 = 本次摸牌序号；荣和 = 该家已摸次数（它还没摸下一张）。⛔ 不是「全场第几巡」。 |
| `avg_riichi_turn` | 平均立直巡目 | 每个立直小局的 riichi_turn 之和 | **能取到立直巡目的**立直小局数 | `obs.riichi_turn[seat]（obs v3 字段；若立直就是本局最后一次询问，回退到宣言那行的 obs.player_draws —— PROTOCOL §8.2 判据 1 保证两者是同一个数）` | obs v2 轨迹**没有**这个字段 ⇒ 该维输出 null 并在 unavailable 里写明缺哪个字段（⛔ 不用相近字段冒充）。 |
| `dama_rate` | 默听率（和了但本局未立直） | 和了且该小局**未立直**的和了次数（含「没听牌就自摸」的平和手 —— 口径是「未立直」，不是「故意默听」） | 该策略的和了次数（= wins） | `hand 行 winner == 该席 且 该小局该席 obs.riichi == false` | 与 riichi_win_rate 配对，用来刻画**默听/立直取向**；0 次和了时输出 null。⚠ 它量不出「听得见却故意不立直」（那需要逐巡的听牌形，轨迹里没有和了前的听牌集合），所以它是**上界意义**的默听率：把「没听牌就自摸」也算进去了。 |
| `riichi_win_rate` | 立直后和了率 | 立直过且**最终和了**的小局数 | 该策略立直过的小局数（= riichi_rate 的分子） | `hand 行 winner == 该席 且 该小局该席 obs.riichi == true` | 立直小局为 0 时输出 null（分母为 0。⛔ 不写 0.00%：那会被读成「立直了但一次没和」）。 |

### 读数表

| 策略 | 场·席 | 打过的局 | 立直率 | 副露率 | 和了率 | 放铳率 | 平均打点 | 平均和了巡 | 平均立直巡 | 默听率 | 立直后和了率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-w0-c2-g01\net.bin@0#0.5 | 1000 | 11396 | 29.39% | 25.31% | 20.31% | 18.36% | 7112 | 11.02 | 9.40 | 23.29% | 53.00% |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin | 1000 | 11396 | 33.96% | 21.32% | 23.23% | 16.38% | 7245 | 11.17 | 9.73 | 23.65% | 52.22% |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin | 1000 | 11396 | 37.79% | 16.63% | 23.15% | 15.37% | 7469 | 11.37 | 9.80 | 16.03% | 51.43% |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin | 1000 | 11396 | 35.92% | 21.24% | 23.95% | 16.22% | 7370 | 11.26 | 9.64 | 21.07% | 52.63% |

### 分子 / 分母（整数；比率对不上时先看这里）

| 策略 | wins | win_points | deals | riichi_hands | meld_hands | riichi_wins | dama_wins | win_turn 已知/缺 | riichi_turn 已知/缺 | 无决策小局 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin | 2638 | 19703700 | 1752 | 4307 | 1895 | 2215 | 423 | 2638/0 | 4307/0 | 0 |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin | 2729 | 20113700 | 1849 | 4093 | 2421 | 2154 | 575 | 2729/0 | 4093/0 | 0 |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin | 2647 | 19177300 | 1867 | 3870 | 2430 | 2021 | 626 | 2647/0 | 3870/0 | 0 |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-w0-c2-g01\net.bin@0#0.5 | 2314 | 16456300 | 2092 | 3349 | 2884 | 1775 | 539 | 2314/0 | 3349/0 | 0 |

### 座位直方图（`--rotate-perm` 的数据侧体检）

每个策略在四个座位上出现的场数；「允许残余」= Σ(场数 % 24) = **16**（`--rotate-perm` 按场号枚举 24 个全排列，多出来的场只能落在部分排列上）。

| 策略 | 座位0 | 座位1 | 座位2 | 座位3 | 极差 | 理想值/座 | 最大偏差 | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin | 250 | 250 | 250 | 250 | 0 | 250 | 0 | 完全平衡（`--rotate-perm` 的迹象） |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin | 246 | 250 | 252 | 252 | 6 | 250 | 4 | 平衡（残余 4 ≤ 场数余数 16，来自「场数不是 24 的整数倍」） |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin | 252 | 250 | 249 | 249 | 3 | 250 | 2 | 平衡（残余 2 ≤ 场数余数 16，来自「场数不是 24 的整数倍」） |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-w0-c2-g01\net.bin@0#0.5 | 252 | 250 | 249 | 249 | 3 | 250 | 2 | 平衡（残余 2 ≤ 场数余数 16，来自「场数不是 24 的整数倍」） |

### 自检原始输出

| 自检项 | 结果 | 原始输出 |
| --- | --- | --- |
| ③ 命令行必须含 --rotate-perm（活体自证） | ✅ 通过 | 正常命令行含 --rotate-perm：True；故意去掉后 assert：触发（自检③失败：命令行缺 --rotate-perm：['trainer.exe', 'selfplay', '1', '--workers', '1', '--policy', 'teacher', '--seed', '1', '--out', '.']） |
| [v4-wl-w0-c2-g02] ② Σ(每策略 seat_hands) == 4 × summary.hands | ✅ 通过 | Σ seat_hands = 45584，4 × summary.hands(11396) = 45584（差 0） |
| [v4-wl-w0-c2-g02] ②b Σ(逐场读到的小局行数) == summary.hands | ✅ 通过 | 读到 11396 行小局 vs summary.hands = 11396（共 1000 场） |
| [v4-wl-w0-c2-g02] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11396 vs 11396；games 1000 vs 1000；wins 2638 vs 2638；deals 1752 vs 1752 |
| [v4-wl-w0-c2-g02] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.23148473148473148 vs 0.23148473148473148（|diff| = 0）；deal_rate 0.15373815373815375 vs 0.15373815373815375（|diff| = 0）；avg_win_score 7469.181197877179 vs 7469.181197877179，|diff| = 0 |
| [v4-wl-w0-c2-g02] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11396 vs 11396；games 1000 vs 1000；wins 2729 vs 2729；deals 1849 vs 1849 |
| [v4-wl-w0-c2-g02] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.23946998946998946 vs 0.23946998946998946（|diff| = 0）；deal_rate 0.16224991224991225 vs 0.16224991224991225（|diff| = 0）；avg_win_score 7370.355441553683 vs 7370.355441553683，|diff| = 0 |
| [v4-wl-w0-c2-g02] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11396 vs 11396；games 1000 vs 1000；wins 2647 vs 2647；deals 1867 vs 1867 |
| [v4-wl-w0-c2-g02] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.23227448227448227 vs 0.23227448227448227（|diff| = 0）；deal_rate 0.16382941382941382 vs 0.16382941382941382（|diff| = 0）；avg_win_score 7244.918775972799 vs 7244.918775972799，|diff| = 0 |
| [v4-wl-w0-c2-g02] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-w0-c2-g01\net.bin@0#0.5 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11396 vs 11396；games 1000 vs 1000；wins 2314 vs 2314；deals 2092 vs 2092 |
| [v4-wl-w0-c2-g02] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-w0-c2-g01\net.bin@0#0.5 win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.20305370305370304 vs 0.20305370305370304（|diff| = 0）；deal_rate 0.18357318357318358 vs 0.18357318357318358（|diff| = 0）；avg_win_score 7111.62489196197 vs 7111.62489196197，|diff| = 0 |

**总判定：✅ 自检全通过**

[落盘] S:\mahjong-training\w-ladder\style-vector-per-gen\v4-wl-w0-c2-g02.json
STYLE-VECTOR PASS
