# 风格向量读数（从**轨迹**算；⛔ 不是判决判据 —— 判决只看 `mahjong_ml/v4/gate.py`）

- 生产命令行（自检 ③：含 `--rotate-perm`）：`S:\mahjong-training\tools\trainer\trainer.exe selfplay 1000 --workers 12 --policy net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400c-c1-c1-g01\net.bin@0#0.5,net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin,net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin,net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin --seed 24620072 --rotate-perm --out S:\mahjong-training\w-ladder\traces-post\v4-wl-dama5400c-c1-c1-g01`

- 输入目录：S:\mahjong-training\w-ladder\traces-post\v4-wl-dama5400c-c1-c1-g01（共 1 个；按策略名**先累加整数分子/分母再相除**合并）
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
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400c-c1-c1-g01\net.bin@0#0.5 | 1000 | 11387 | 27.49% | 27.20% | 19.41% | 19.09% | 7017 | 10.87 | 9.27 | 22.94% | 54.41% |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin | 1000 | 11387 | 35.32% | 21.32% | 23.66% | 16.44% | 7422 | 11.24 | 9.67 | 21.57% | 52.54% |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin | 1000 | 11387 | 34.13% | 21.26% | 24.05% | 15.90% | 7084 | 11.24 | 9.75 | 23.29% | 54.07% |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin | 1000 | 11387 | 38.18% | 17.08% | 23.61% | 16.12% | 7425 | 11.25 | 9.75 | 17.22% | 51.21% |

### 分子 / 分母（整数；比率对不上时先看这里）

| 策略 | wins | win_points | deals | riichi_hands | meld_hands | riichi_wins | dama_wins | win_turn 已知/缺 | riichi_turn 已知/缺 | 无决策小局 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin | 2689 | 19967100 | 1836 | 4347 | 1945 | 2226 | 463 | 2689/0 | 4347/0 | 0 |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin | 2694 | 19995800 | 1872 | 4022 | 2428 | 2113 | 581 | 2694/0 | 4022/0 | 0 |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin | 2739 | 19403300 | 1811 | 3886 | 2421 | 2101 | 638 | 2739/0 | 3886/0 | 0 |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400c-c1-c1-g01\net.bin@0#0.5 | 2210 | 15507500 | 2174 | 3130 | 3097 | 1703 | 507 | 2210/0 | 3130/0 | 0 |

### 座位直方图（`--rotate-perm` 的数据侧体检）

每个策略在四个座位上出现的场数；「允许残余」= Σ(场数 % 24) = **16**（`--rotate-perm` 按场号枚举 24 个全排列，多出来的场只能落在部分排列上）。

| 策略 | 座位0 | 座位1 | 座位2 | 座位3 | 极差 | 理想值/座 | 最大偏差 | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin | 246 | 250 | 252 | 252 | 6 | 250 | 4 | 平衡（残余 4 ≤ 场数余数 16，来自「场数不是 24 的整数倍」） |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin | 252 | 250 | 249 | 249 | 3 | 250 | 2 | 平衡（残余 2 ≤ 场数余数 16，来自「场数不是 24 的整数倍」） |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin | 250 | 250 | 250 | 250 | 0 | 250 | 0 | 完全平衡（`--rotate-perm` 的迹象） |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400c-c1-c1-g01\net.bin@0#0.5 | 252 | 250 | 249 | 249 | 3 | 250 | 2 | 平衡（残余 2 ≤ 场数余数 16，来自「场数不是 24 的整数倍」） |

### 自检原始输出

| 自检项 | 结果 | 原始输出 |
| --- | --- | --- |
| ③ 命令行必须含 --rotate-perm（活体自证） | ✅ 通过 | 正常命令行含 --rotate-perm：True；故意去掉后 assert：触发（自检③失败：命令行缺 --rotate-perm：['trainer.exe', 'selfplay', '1', '--workers', '1', '--policy', 'teacher', '--seed', '1', '--out', '.']） |
| [v4-wl-dama5400c-c1-c1-g01] ② Σ(每策略 seat_hands) == 4 × summary.hands | ✅ 通过 | Σ seat_hands = 45548，4 × summary.hands(11387) = 45548（差 0） |
| [v4-wl-dama5400c-c1-c1-g01] ②b Σ(逐场读到的小局行数) == summary.hands | ✅ 通过 | 读到 11387 行小局 vs summary.hands = 11387（共 1000 场） |
| [v4-wl-dama5400c-c1-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11387 vs 11387；games 1000 vs 1000；wins 2689 vs 2689；deals 1836 vs 1836 |
| [v4-wl-dama5400c-c1-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.23614648283129885 vs 0.23614648283129885（|diff| = 0）；deal_rate 0.1612364977606042 vs 0.1612364977606042（|diff| = 0）；avg_win_score 7425.47415396058 vs 7425.47415396058，|diff| = 0 |
| [v4-wl-dama5400c-c1-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11387 vs 11387；games 1000 vs 1000；wins 2694 vs 2694；deals 1872 vs 1872 |
| [v4-wl-dama5400c-c1-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.2365855800474225 vs 0.2365855800474225（|diff| = 0）；deal_rate 0.16439799771669447 vs 0.16439799771669447（|diff| = 0）；avg_win_score 7422.345953971789 vs 7422.345953971789，|diff| = 0 |
| [v4-wl-dama5400c-c1-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11387 vs 11387；games 1000 vs 1000；wins 2739 vs 2739；deals 1811 vs 1811 |
| [v4-wl-dama5400c-c1-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.24053745499253534 vs 0.24053745499253534（|diff| = 0）；deal_rate 0.15904101167998594 vs 0.15904101167998594（|diff| = 0）；avg_win_score 7084.081781672143 vs 7084.081781672143，|diff| = 0 |
| [v4-wl-dama5400c-c1-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400c-c1-c1-g01\net.bin@0#0.5 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11387 vs 11387；games 1000 vs 1000；wins 2210 vs 2210；deals 2174 vs 2174 |
| [v4-wl-dama5400c-c1-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400c-c1-c1-g01\net.bin@0#0.5 win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.1940809695266532 vs 0.1940809695266532（|diff| = 0）；deal_rate 0.1909194695705629 vs 0.1909194695705629（|diff| = 0）；avg_win_score 7016.968325791855 vs 7016.968325791855，|diff| = 0 |

**总判定：✅ 自检全通过**

