# 风格向量读数（从**轨迹**算；⛔ 不是判决判据 —— 判决只看 `mahjong_ml/v4/gate.py`）

- 生产命令行（自检 ③：含 `--rotate-perm`）：`S:\mahjong-training\tools\trainer\trainer.exe selfplay 1000 --workers 12 --policy net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400-c1-g01\net.bin@0#0.5,net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin,net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin,net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin --seed 24620052 --rotate-perm --out S:\mahjong-training\w-ladder\traces-post\v4-wl-dama5400-c1-g01`

- 输入目录：S:\mahjong-training\w-ladder\traces-post\v4-wl-dama5400-c1-g01（共 1 个；按策略名**先累加整数分子/分母再相除**合并）
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
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400-c1-g01\net.bin@0#0.5 | 1000 | 11408 | 29.36% | 22.67% | 19.78% | 18.56% | 6869 | 11.02 | 9.51 | 23.27% | 51.69% |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin | 1000 | 11408 | 36.90% | 20.35% | 23.88% | 16.64% | 7306 | 11.20 | 9.68 | 19.90% | 51.84% |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin | 1000 | 11408 | 34.34% | 21.34% | 23.72% | 16.62% | 7109 | 11.26 | 9.70 | 24.61% | 52.07% |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin | 1000 | 11408 | 37.65% | 17.22% | 23.34% | 15.47% | 7481 | 11.37 | 9.77 | 16.26% | 51.92% |

### 分子 / 分母（整数；比率对不上时先看这里）

| 策略 | wins | win_points | deals | riichi_hands | meld_hands | riichi_wins | dama_wins | win_turn 已知/缺 | riichi_turn 已知/缺 | 无决策小局 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin | 2663 | 19921800 | 1765 | 4295 | 1965 | 2230 | 433 | 2663/0 | 4295/0 | 0 |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin | 2724 | 19902000 | 1898 | 4209 | 2321 | 2182 | 542 | 2724/0 | 4209/0 | 0 |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin | 2706 | 19237800 | 1896 | 3918 | 2435 | 2040 | 666 | 2706/0 | 3918/0 | 0 |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400-c1-g01\net.bin@0#0.5 | 2256 | 15496700 | 2117 | 3349 | 2586 | 1731 | 525 | 2256/0 | 3349/0 | 0 |

### 座位直方图（`--rotate-perm` 的数据侧体检）

每个策略在四个座位上出现的场数；「允许残余」= Σ(场数 % 24) = **16**（`--rotate-perm` 按场号枚举 24 个全排列，多出来的场只能落在部分排列上）。

| 策略 | 座位0 | 座位1 | 座位2 | 座位3 | 极差 | 理想值/座 | 最大偏差 | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin | 246 | 250 | 252 | 252 | 6 | 250 | 4 | 平衡（残余 4 ≤ 场数余数 16，来自「场数不是 24 的整数倍」） |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin | 252 | 250 | 249 | 249 | 3 | 250 | 2 | 平衡（残余 2 ≤ 场数余数 16，来自「场数不是 24 的整数倍」） |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin | 250 | 250 | 250 | 250 | 0 | 250 | 0 | 完全平衡（`--rotate-perm` 的迹象） |
| net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400-c1-g01\net.bin@0#0.5 | 252 | 250 | 249 | 249 | 3 | 250 | 2 | 平衡（残余 2 ≤ 场数余数 16，来自「场数不是 24 的整数倍」） |

### 自检原始输出

| 自检项 | 结果 | 原始输出 |
| --- | --- | --- |
| ③ 命令行必须含 --rotate-perm（活体自证） | ✅ 通过 | 正常命令行含 --rotate-perm：True；故意去掉后 assert：触发（自检③失败：命令行缺 --rotate-perm：['trainer.exe', 'selfplay', '1', '--workers', '1', '--policy', 'teacher', '--seed', '1', '--out', '.']） |
| [v4-wl-dama5400-c1-g01] ② Σ(每策略 seat_hands) == 4 × summary.hands | ✅ 通过 | Σ seat_hands = 45632，4 × summary.hands(11408) = 45632（差 0） |
| [v4-wl-dama5400-c1-g01] ②b Σ(逐场读到的小局行数) == summary.hands | ✅ 通过 | 读到 11408 行小局 vs summary.hands = 11408（共 1000 场） |
| [v4-wl-dama5400-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11408 vs 11408；games 1000 vs 1000；wins 2663 vs 2663；deals 1765 vs 1765 |
| [v4-wl-dama5400-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-atk3-g10\net.bin win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.23343267882187937 vs 0.23343267882187937（|diff| = 0）；deal_rate 0.15471598877980364 vs 0.15471598877980364（|diff| = 0）；avg_win_score 7480.961321817499 vs 7480.961321817499，|diff| = 0 |
| [v4-wl-dama5400-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11408 vs 11408；games 1000 vs 1000；wins 2724 vs 2724；deals 1898 vs 1898 |
| [v4-wl-dama5400-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-def7-g10\net.bin win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.23877980364656382 vs 0.23877980364656382（|diff| = 0）；deal_rate 0.16637447405329595 vs 0.16637447405329595（|diff| = 0）；avg_win_score 7306.167400881057 vs 7306.167400881057，|diff| = 0 |
| [v4-wl-dama5400-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11408 vs 11408；games 1000 vs 1000；wins 2706 vs 2706；deals 1896 vs 1896 |
| [v4-wl-dama5400-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-expert-win2-g10\net.bin win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.23720196353436185 vs 0.23720196353436185（|diff| = 0）；deal_rate 0.1661991584852735 vs 0.1661991584852735（|diff| = 0）；avg_win_score 7109.312638580931 vs 7109.312638580931，|diff| = 0 |
| [v4-wl-dama5400-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400-c1-g01\net.bin@0#0.5 整数计数（wins/deals/games/seat_hands） | ✅ 通过 | seat_hands 11408 vs 11408；games 1000 vs 1000；wins 2256 vs 2256；deals 2117 vs 2117 |
| [v4-wl-dama5400-c1-g01] ① net:C:\Users\HP\source\games\mahjong\tools\build\v4-wl-dama5400-c1-g01\net.bin@0#0.5 win_rate / deal_rate / avg_win_score（≤ 1e-09） | ✅ 通过 | win_rate 0.19775596072931276 vs 0.19775596072931276（|diff| = 0）；deal_rate 0.18557152875175315 vs 0.18557152875175315（|diff| = 0）；avg_win_score 6869.104609929078 vs 6869.104609929078，|diff| = 0 |

**总判定：✅ 自检全通过**

