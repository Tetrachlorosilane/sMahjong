# -*- coding: utf-8 -*-
"""**风格奖励塑形**（`--style-bonus`）：把"过程轴"做成可配置、可关闭、可审计的一路奖励。

## 这个模块治的是什么病

上一轮实测：默认奖励下训练**总是往"打点"漂**（两条不同名的线都出现"打点 ↑、和了率 ↓"）。
根因不是"奖励写错了"，而是**方差不均**：小局收支（`delta`）的点数量纲 std 在 5 千点量级，
而"立直了没有"这种 0/1 指示量的 std 只有 ~0.36（p≈0.2）—— 同一个优势里混着两把量纲完全
不同的尺子，梯度自然被大方差那一轴支配（第十九轮量到过同类事故：`std(A)/std(delta)=2.671×`）。
所以本模块的**全部要点**就是：把要推的那一轴**白化**（减均值、除标准差）之后，再按权重加进奖励。

    r_hand' = r_hand + Σ_i w_i · z_i,      z_i = (x_i − μ_i) / σ_i

## 口径的合同（与 `tools/style-vector.py` 的**独立实现**对齐，⛔ 这里不许 import 它）

本仓的既定分工：**判据独立实现**。所以 `tools/style-vector.py` 是"测量"那一份（判据），
本模块是"训练"那一份（被塑形的对象），两边各自实现同一套分子/分母口径，由对账钉住：

| 轴 | 分子 | 分母（= 一个小局 × 一个座位） | 与 `style-vector.py` 的对应 |
| --- | --- | --- | --- |
| `riichi` | 该席本小局**宣言过立直**（`obs.riichi[seat]` / `obs.riichi_turn[seat]>0` / `chosen` 以 `riichi:` 开头，三者取或） | 该席本小局**在轨迹里有决策行** | `riichi_rate` |
| `meld` | 该席本小局**有副露**（`obs.melds[seat]` 非空 / `chosen` 以 `chi:`/`pon:`/`kan:` 开头） | 同上 | `meld_rate` |
| `win` | `hand_winner == seat` 且本行 `hand_agari` | 同上 | `win_rate` |
| `deal` | `hand_loser == seat` | 同上 | `deal_rate` |
| `no_deal` | `hand_loser != seat`（含流局） | 同上 | `1 − deal_rate` |
| `win_points` | `hand_winner == seat` 时 `hand_delta[seat]`，否则 0（点） | 同上 | `avg_win_score` 的分子（÷ 和了次数才是比率） |

⚠ **分母的一处刻意差异**（唯一一处，写在这里免得以后当 bug 查）：`style-vector.py` 的分母是
"场号 × 席位标签 × 该场小局行数"（**含**一次询问都没有的小局，例如庄家第一张就被荣和）；
本模块的分母是"轨迹里**真的有决策行**的 (小局, 座位)"。差集只有"整局没有任何决策行"的
(小局, 座位) —— 那些行在数据集里**根本不存在**，对梯度没有任何贡献（不存在 = 塑形不到），
所以拿它们去算 μ/σ 只会让白化参数偏离真正参与训练的那个分布。判据留在 `selfcheck.py`。

## 成对质量轴（⛔ 防"奖励被钻空子"）

`--style-bonus 'riichi=w:PAIR'` 里的 `PAIR` 是一条**伴随质量量**：**只有它为真时才付这笔奖励**。
为什么要它：奖励"立直率"而不管质量，等价于告诉策略"立直就行"—— 而"无脑立直"（听个边张也宣言、
拿着无役愚形也宣言）在立直麻将里是**负收益**打法 ⇒ 奖励会被钻空子，训练出来的是"爱立直"而不是
"会立直"。所以预注册（`S:\\mahjong-training\\style-reward\\PREREGISTRATION.md`）用的是
`riichi=1.0:no_deal`：**立直且没有放铳**才拿得到这笔钱 —— 它把"立直"与"立直之后别点炮"
绑成一条质量轴，同时仍然只推"立直率"这一个**方向**。

⛔⛔ **绝不支持 `PAIR = 恒真`**：`riichi=1.0:true` 会被 `parse_bonus` 直接拒绝（见那里的注释）。
理由：那正是"只按立直了付奖励"，是这个模块**唯一**要防的事。

## 白化的口径（硬要求）

* **本数据集实测**：μ/σ 从**这一代这一份数据集**上现算（不是历史常数）—— 每一代的策略分布、
  桌上对手都不一样，拿上一代的 μ/σ 去白化这一代，等于白化到错的分布上。
* **总体选择**（`--style-bonus-whiten`）：`student`（缺省，给了 `--student` 时）=
  只用**学生座位**的小局（训练里真正进梯度的那些行）；`all` = 全部座位。
  ⚠ 用"全部座位"会把 teacher/对手的行为分布混进 μ/σ（学生行只占 1/4），白化就不准了 ——
  所以缺省取 student，且**必须**能在日志/meta 里看出来用的是哪一份。
* **统计对象**：Bernoulli 指示量用 `p`、`√(p(1−p))`（与 `x.std(ddof=0)` 逐位一致）；
  连续量（`win_points`）用样本均值与 `x.std(ddof=0)`。
  ⚠ **σ ≤ 0 必须报错**（不许 `1/0`，也不许悄悄跳过一个轴 —— "静默少一个轴"会让白化失效且不报错）。
* 白化后必须在数据集上核对 **均值 ≈ 0、方差 ≈ 1**（`audit_whitening`；数字进日志与 meta）。

## 付奖模式：`hand`（缺省）/ `decision`（`--style-bonus-mode`，2026-10-10 加）

**动机（上一轮的实证，见 `S:\\mahjong-training\\w-ladder\\PROBE-w14000.md`）**：`hand` 口径下奖励是
**小局级常数、该小局四行同值** ⇒ 对手级行为（"要不要立直"）**几乎没有信度分配**：同一小局里
学生那十几行决策拿到**同一个**增量，PPO 只被"整手结局"间接带动。实测把塑形 std 抬到结果奖励的
**2.52×**（`w=14000`）立直率仍只动 **+0.39pp**（同配方 g01 噪声地板 0.85pp）⇒ **不是尺度问题**。
于是要问的是：**把信度分配到"立直那一手"之后，立直率能不能被推上去**。

所以两种模式的差别**只有一处**：那笔奖励**落在哪一行**。

| | `hand`（缺省，老口径**逐位不变**） | `decision` |
| --- | --- | --- |
| 付奖行 | 该小局的**每一行**（常数） | **只有真的执行了该轴动作、且我就是被记录的那一家的那一行**（`chosen` 以该轴的动作键开头；见下面"多家荣和"），其余行 = **0** |
| 白化的总体 | 小局（四席取或成一份） | **学生席的决策行**（逐行；见下面"为什么"） |
| 伴随质量轴 `PAIR` | 按该小局判定 | **完全一样**（仍按该小局判定，⛔ 不改成"按那一行"） |
| 动作行找不到 | 不适用 | ⛔ **当场报错**（`BonusSpecError`），**绝不**退化成 `hand` |

**为什么 `decision` 要把白化换成"逐行"（这是本模式唯一一处不显然的口径）**：
`hand` 模式下 `std(塑形列) ≈ w`（白化后那一轴的标准差就是 `w`）⇒ 上一轮说的"`w=6170` = 真实 1:1 尺度"
就是"**std(塑形列) ≈ std(delta)**"。若 `decision` 模式**沿用**手级 μ/σ（μ≈0.286、σ≈0.452），
付奖只落在 ~1/15 的行上 ⇒ 列的 std 掉到 `w` 的 ~0.2× ⇒ **剂量**被顺手改掉，读数会因为"钱变少了"
而不是"信度分配变了"变平（那是这类实验最经典的混杂）。**逐行白化**让 `1[这一行做了动作]` 先归一化，
付奖行拿到 `w·(1−μ_row)/σ_row`，于是 `std(塑形列) ≈ w` **两种模式同一个量级** ⇒ 唯一被改动的变量
是**信度落在哪一行**。两条 std 都会打进日志与审计账（⛔ 不靠"看起来差不多"）。

**`decision` 模式只对"行级动作"的轴成立**：`riichi` / `meld` / `win`（动作键见 `PROTOCOL.md` §8.3）。
`deal` / `no_deal` / `win_points` 是**结局量**、没有"哪一行做了它" ⇒ 拿它们当**付奖轴**一律**报错**
（⛔ 不猜一行、也不静默退回 `hand`）。它们当**伴随质量轴**（`PAIR`）照常可用 —— 质量轴一直是
小局级判定的。

### ⚠ 多家荣和（双响）的语义陷阱 —— `win` 轴的行级判据必须多一条合取

**手级** `AXES['win']` 的口径 = "该席是**被记录的和了者**"（`hand_winner == seat`；生产端只写
`winners[0]` = **离放铳者最近那家**，`round.cpp`/`Round.agariRon` + `headBump`）。
**行级**若只判"这一行执行了 `tsumo`/`ron`"，那么**双响时两家都为真** —— 而其中只有一家是
`hand_winner`（另一家也真的收到点棒，但供託/本场只归 `winners[0]`）。实测 **30 / 11,258 小局
（0.27%）** 对不上：手级指示量 = 0（不是被记录的那家）却有动作行 ⇒ 白化那一遍就抛
`BonusSpecError`（"取不到哪一行做了这个动作"）。所以行级判据是

    1[这一行做了轴 i 的动作]  ∧  1[我就是被记录的那家（`ROW_SELF_SEAT`，只有 `win` 需要）]

**语义后果（写清，别当 bug 查）**：双响里**非"最近那家"的宣和席拿不到这笔奖励** —— 学生和了行里
约 **1.3%** 属于这种。所以这笔奖励是**软信号少付**（把"和了"这件事付给真正被记账的那家），
**不是"判据换了尺子"**：⛔ **判据侧一律仍用"手级被记录和了者"**（`tools/style-vector.py` 的
`win_rate` 与 `AXES['win']` 一个字都不许动）—— 换尺子会让"训练奖励"和"测量口径"错位。
（备选方案 (a)：把生产端的 `hand_winner` 改成"多家都算"—— 那要同时改 `style-vector.py` 与
服务端 `winners[0]` 口径，属于**未来的语义升级**，本轮不做。）

## `dama` 轴（**能立而不立**，2026-10-10 加）：付奖行必须是**真实的行为选择**

**动机（`win` 轴的教训，实测写在 `S:\\mahjong-training\\w-ladder\\PROBE-dec6170-4g.md` 一族里）**：
`win=5000:no_deal` 的付奖行 = "和了那一行"，而**和了者的 `no_deal` 恒真**
（审计账实测 `action_rows == paid_rows == 2230`、`blocked_rows == 0`）⇒ 伴随质量轴形同虚设，
奖励退化成**对结果（和了）的重复加权**；实测和了率 **−1.02pp**、立直率 **−1.60pp**。
**一般判据**：可奖励的轴必须对应**真实的行为选择**；结果量（和了/放铳/打点）用行级动作奖励去推，
只会变成对同一个结果的重复加权。所以本模块新增 `dama` 轴时付奖行**不是结局行**：

| | `dama` 轴 |
| --- | --- |
| 行级付奖行（**唯一**口径） | `legal` 里有 `riichi:*` **且**这一行**没选** `riichi:*`（= 能立而不立的那一手；`ROW_ACTION_DECLINE`） |
| 手级指示量（`AXES['dama']`） | 该席本小局**未立直**（`dama_wins` 的分子谓词，与 `style-vector.py` 的 `dama_rate` 同源） |
| 伴随质量轴 | `no_deal`（该小局未放铳）—— 付奖行**不是和了行** ⇒ **不退化**（放铳者当小局照样能被拦下，实测 `blocked_rows > 0`） |
| 判据侧口径 | ⛔ 一个字不动：`tools/style-vector.py` 的 `dama_rate = dama_wins / wins` |

⚠ **两套口径是刻意的不同对象**（判据侧量"未立直而和了"这个**结果**、奖励侧量"能立而不立"这个**决策**）
⇒ 所以 `dama` **没有**"手级=1 必有动作行"那条蕴含（`decision_violations` 对它换成两条**可证伪**的结构检查：
付奖行那一刻 `obs.riichi[seat]` 必须还是 false、且必须发生在该席第一次宣言**之前**）。
两边由**对账**钉住（见 `S:\\mahjong-training\\w-ladder\\_dama-reconcile.py`），⛔ 不靠改判据侧去凑。

⛔ **`dama` 只允许在 `decision` 模式下当付奖轴**（`DECISION_ONLY_AXES`）：`hand` 模式的合并量是
"四席取或的未立直"，**不是**"能立而不立" —— 付出去的奖励与轴名无关（那是静默换轴），所以**当场报错**。

## `riichi_turn` 轴（**早立直**：宣言巡目 ≤ T 才付奖，2026-10-11 加）

**动机（`AXIS-CENSUS.md` 的路线 1 第一根轴）**：`riichi` 轴只问"立直了没有"，完全不问**什么时候**立直 ——
而这个**时机**维度在同一份轨迹上是可识别、可测量、可配对的（普查：机会行 2.23% / 付奖行 1.91%、
学生席立直巡目 均值 9.28 / 中位数 9；伴随量 `no_deal` 89.2% 真门）。

| | `riichi_turn` 轴 |
| --- | --- |
| 行级付奖行（**唯一**口径） | **宣言立直的那一行**（与 `riichi` 轴同一行规则：`chosen` 以 `riichi:` 开头）**且**该行 `obs.player_draws` ≤ T（`ROW_ACTION_TURN` / `ROW_TURN_LIMIT`，T=8） |
| 手级指示量（`AXES['riichi_turn']`） | 该席本小局**有过一次"巡目 ≤ T 的立直宣言"**（`HandState.early_decl`；与行级判据**同一把尺子**，`decision_violations` 的蕴含才成立） |
| 伴随质量轴 | `no_deal`（该小局未放铳）—— ⛔ **不是恒真**：立直之后**仍然可能放铳**（普查实测 `no_deal` 89.2% 真门）。判据由 `ROW_PAIR_MUST_BLOCK` 的**实测**闸门钉住：`blocked_rows` 必须 > 0 |
| 方向 | ⛔ **单向：越早越好** —— 巡目 > T 的宣言行那一侧**一分钱都不付**（不做负奖、也不做"越晚越少"：那两件事都会把"晚立直"也变成一个被优化的方向） |
| 判据侧 | `tools/style-vector.py` 的 `avg_riichi_turn`（聚合，辅）+ `tools/w-ladder.py` 的逐场 `riichi_turn_mean`（主，**方向为降**） |

⚠ **与 `riichi` 轴共存**：这是**同一决策点**的两个维度（做不做 / 早不早），两轴可以在**同一条轨迹**
上分别开（`--style-bonus 'riichi=14000:no_deal,riichi_turn=5400:no_deal'` 是合法的；`riichi_turn` 自己开也合法）。
⛔ 但 **`hand` 模式下不许用它**（`DECISION_ONLY_AXES`）：`hand` 的合并量是"这一小局有没有人早立直"，
**不是**"宣言那一行" ⇒ 与 `dama` 同理，那等于静默换轴。

## `fold` 轴（**被威胁时的降り率**：切"对**至少一家**威胁家安全"的牌，2026-10-11 加）

**动机（`AXIS-CENSUS.md` 的路线 2 核心轴）**：A 轴是普查里**唯一"高频（机会行 15.30%）且真有杠杆"**的轴
（付奖行 6.14%；`deal` 付奖行 28.7% vs 对照行 21.0% = 判别力 **+7.7pp**）。⚠ 但强口径的引擎危险度
（`rules/Danger.java`）**不在 `obs` 里** ⇒ 本轴只能走**弱口径 = 現物（genbutsu）**。

⚠ **安全口径改过一轮（2026-10-11 第三轮，本批）**：旧口径 = "对**每一个**威胁家都是現物"（`all`），
付奖行率仅 **1.0394%/行**（刚过 1% 门槛）、一代配对 SE **0.170pp**、CI 含 0 ⇒ 判"测不动"；
新口径 = "对**至少一家**威胁家是現物"（`any`），普查实测付奖行率 ≈ **8.6%/行**。
改动点三处一起（⛔ 缺一处就是两套尺子）：`fold_row_ok`（`all`→`any`）、`threat_missing | fold_row_ok`
的 `seat` **必填**（威胁集只数**别家**；付钱路 `_decision_rule` 也要传行的 `seat`）、
判据侧 `tools/w-ladder.py` 的 `_student_per_game` / `FOLD_KEYS`。

| | `fold` 轴 |
| --- | --- |
| 「被威胁」（**只用公开信息**） | 该行 `obs.riichi[s]` 为真（`s` 是**这一行的行动者以外的**座位）的 `s` 至少一个 —— 立直宣言已发生；⚠ **⛔ 只数别家**（行动者自己立直不算威胁：立直后只能摸切、没有选择权，那几行不是降り决策） |
| 付奖行（**唯一**口径） | **该行是出牌行**（`chosen` 以 `discard:` 开头）∧ **被威胁** ∧ 打出的牌对**至少一家**威胁家是**現物** |
| 現物（弱口径安全牌） | 重建规则见 `_genbutsu_from_events`：**只用** `obs.events` —— 取该家 `type=="riichi"` 那条事件**之后**的 `discard` 事件牌 **＋ 宣言牌本身**（`sideways`，与 `PROTOCOL.md` §8.2 判据 2「宣言那张算立直**之前**」同一把尺子）。⚠ 只用公开事件；不读别家手牌 / 牌山 / 里宝 / 别家听牌 |
| 方向 | ⛔ **单向：降り率 ↑** —— 只对"选了安全牌"的那些行付奖，**不安全那一侧一分钱都不付**（不做负奖、也不做"越安全越多"） |
| 手级指示量 | 该席本小局**至少有一行**被判成付奖行（与行级**同一把尺子** ⇒ `decision_violations` 的蕴含成立；⛔ 它**不**是"降り率"这个比值） |
| 伴随质量轴 | `no_deal`（该小局未放铳）—— ⛔ **不是恒真**：切了現物**照样可能放铳**（自摸/被别家荣和），判据由 `ROW_PAIR_MUST_BLOCK` 的**实测**闸门钉住：`blocked_rows` 必须 > 0 |
| 判据侧 | `tools/w-ladder.py` 的**行级** `fold_rate`（= 付奖行 / 被威胁行）+ 每场每席付奖行数（**独立实现**，⛔ 不进 `style-vector.py`） |

⚠ **「被威胁」按行取**（`obs.riichi` 是**当行**的公开快照）：某家立直**之前**的行不是被威胁行
（那时他还没宣言），立直**之后**的行才是 —— ⛔ 不能拿"本小局曾经有人立直"当判据（那会把宣言前的行也算进去）。

⚠ **重建规则只许用公开事件**（本仓铁律）：「威胁家有没有和了 / 被鸣 / 流局」**不单独重建** ——
它在**结构上**不是一个问题：`obs` 只在**询问那一刻**构造（PROTOCOL §8.2 判据 3），和了/流局之后**不再有询问**
⇒ 出现"某家已立直"的行本身就意味着**这一小局还没结束、那个威胁仍然活着**。这就是"威胁活跃性"的全部重建。

## `pon` 轴（**副露种类 = 碰取向**，2026-10-11 加）

**动机（`AXIS-CENSUS.md` 路线 1 的第二根轴：`D 副露种类`）**：`meld` 轴只问"**鸣不鸣**"，完全不问
**鸣哪一种** —— 而吃/碰是两种代价不同的行为（碰能碰役牌/宝牌、吃常为速度）。普查（学生席）实测：
鸣牌行里 **碰 2150 / 吃 1107 / 杠 308**；占决策行 **1.257% / 0.647% / 0.180%**
⇒ ⛔ **`chi` 与 `kan` 单独立轴过不了 1%/行 门槛**（`AXIS-CENSUS.md` §"D 内部细分"），
所以本轴只做**一个方向**：**碰**（量最大的那一侧）。付奖行率实测见
`S:\\mahjong-training\\w-ladder\\PREREGISTRATION-MELD-TYPE.md` §1（含判据侧同口径的复核）。

| | `pon` 轴 |
| --- | --- |
| 付奖行（**唯一**口径） | **鸣牌动作行**（`chosen` 以 `chi:`/`pon:`/`kan:` 开头）**∧** `chosen` 以 **`pon:`** 开头 |
| 手级指示量 | 该席本小局**至少有一行**是碰（`HandState.pon_hit`；与行级**同一把尺子**） |
| 方向 | ⛔ **单向：只推"碰"** —— 吃/杠那一侧**一分钱都不付**（不做负奖、也不做"越碰越多"） |
| 伴随质量轴 | `no_deal`（该小局未放铳）—— ⛔ **不是恒真**：碰完照样可能放铳（普查 `D:pon`×`no_deal` 81.3%）⇒ 由 `ROW_PAIR_MUST_BLOCK` 的**实测**闸门钉住 `blocked_rows > 0` |
| 判据侧 | `tools/w-ladder.py` 的**行级** `pon_type_rate` = 碰的行 / 鸣牌行（+ 每场每席碰的行数；**独立实现**，⛔ 不进 `style-vector.py`） |

⚠ **与既有 `meld` 轴正交、可共存**：两轴在同一份轨迹上分别开时，`meld` 管**手级**（有没有副露、
`obs.melds[seat]`），`pon` 管**行级**（这一手是不是碰，动作键前缀）；
⛔ 缺省（不给 `--style-bonus`）与既有各轴**逐位不变**（新轴的四个数只在这类轴的审计账里出现）。

⛔ **`pon` 只允许在 `decision` 模式下当付奖轴**（`DECISION_ONLY_AXES`）：`hand` 模式的合并量是
"四席取或的'这一小局有人碰过吗'"，**不是**"这一手是不是碰" ⇒ 与 `dama`/`riichi_turn`/`fold` 同理，
那不叫同一根轴 ⇒ 当场报错。⚠ 范围里**一行鸣牌行都没有**时由 `MELD_TYPE_ERR` **当场硬拒**
（分母取不到 ⇒ 付奖行率与 `pon_type_rate` 都无定义，⛔ 不静默付 0）。

## ⛔ 退化成恒真的组合：当场报错（`IMPLIED_PAIRS`）

**判据**：若伴随质量轴的指示量**被付奖行本身蕴含**，则它在付奖行上恒真 ⇒ 等于 `PAIR = none`
（= "只按动作付奖"，正是本模块唯一要防的事）。所以 `parse_bonus` 对这类组合**当场报错**：

* `win` + `no_deal`：和了者不可能是放铳者（实测 `blocked_rows == 0`，记账见上）；
* `win` + `win`：付奖行本身要求"我就是被记录的和了者"。

`dama` + `no_deal` **不在**这张表里：付奖行是打牌行（该小局可能以**学生自己放铳**结束）
⇒ 伴随量真的会拦下东西（实测；预注册里写明这条闸门是"付奖行数 > 被拦下的行数"）。

## 列与"叠加一次"的纪律

数据集里存一列 `style_bonus`（float32，**点**，逐决策行）。⚠ 它的**行内结构随模式变**：
`hand` 模式同一小局四行同值（小局级常数）；`decision` 模式**只有动作行非 0**。
奖励**只在一处**叠加上去：`pretrain._hand_advantage` 的 `adv = (delta + style_bonus + 顺位点) − V(s)`。
⛔ 不把 bonus 折进 `delta` 列本身 —— 那样会波及 `_row_weights`（RWR/权重）与 `audit` 的口径，
"叠了一次还是两次"就再也说不清了。

## 逐位不变的判据（默认关闭）

`--style-bonus ''`（缺省）⇒ 不建 `style_bonus` 列（`load_split` 给 `None`）、
`loop` 的命令行里**一个字符都不多**、`pretrain` 一行都不走 ⇒ 与加这个功能之前**逐位相同**。
判据在 `python/selfcheck.py`（列不存在 + 命令逐字 + 权重哈希）。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

#: **持久审计目录**的环境变量名（2026-10-09 加）。
#: 为什么需要：奖励账默认落在 `<紧凑集>/style-reward.json`（= `S:\mahjong-training\compact\<tag>\`），
#: 而 `tools\run-league.ps1` **每代结束会把 `compact\<tag>` 整目录轮换删掉** ⇒ 账跟着没了
#: （实测：上一轮 4 代的账一份都没活下来，只能从 `release\<tag>.log` 里摘文字）。
#: ⛔ `run-league.ps1` 不许改（它决定哈希基线）⇒ 改在**工具侧**：给了这个环境变量就**另写一份**
#: 到 `<dir>/<紧凑集目录名>.json`（紧凑集目录名就是 `tag`），并把**它**当作 `meta.audit_path`。
#: 副本与 compact 里那份**逐字节相同**（同一份 payload），不是第二套口径。
AUDIT_DIR_ENV = "MAHJONG_STYLE_AUDIT_DIR"

#: 一座桌几席（与 `style-vector.py` 的 `SEATS_PER_TABLE` 同一个数；写死在这里让"席位数"唯一）。
SEATS_PER_TABLE = 4

#: 动作键前缀（`docs/PROTOCOL.md` §8.3 的文法）—— 与 `style-vector.py` 同口径。
RIICHI_PREFIX = "riichi:"
MELD_PREFIXES = ("chi:", "pon:", "kan:")

#: ★★ **副露种类轴**（`pon` = **碰取向**，2026-10-11 加）：轴 → **付奖那一侧**的动作键前缀。
#: 付奖行 = **鸣牌动作行**（`chosen` 以 `chi:`/`pon:`/`kan:` 开头）**且** `chosen` 以它开头。
#: ⛔ **方向单向**：另一侧（吃 / 杠）**一分钱都不付** —— 不做负奖、也不做"越碰越多"。
#: 选 `pon` 而不是 `chi` 的**理由是数据**（普查 + 本批 50 场冒烟实测，见
#: `S:\\mahjong-training\\w-ladder\\PREREGISTRATION-MELD-TYPE.md` §1）：`pon` 行占了鸣牌行的
#: **六成**（`chi` 只有约三成），而 `chi` 单独的行占比 **0.65%/行** **过不了 1%/行 门槛**
#: （`AXIS-CENSUS.md` §4/§"D 内部细分"）⇒ 单独立 `chi` 轴会被噪声地板吃掉。
#: ⚠ 它与既有 `meld` 轴（只数"鸣不鸣"）**正交**：同一份轨迹上两轴分别开时各管一个维度
#: （`meld` = 手级"有没有副露"、`pon` = 行级"这一手是不是碰"）；⛔ 缺省与既有各轴**逐位不变**。
MELD_TYPE_PREFIX: dict[str, str] = {"pon": "pon:"}

#: 数据集里那一列的名字（唯一字面量：`dataset.py` / `pretrain.py` / 自检都引用它）。
COLUMN = "style_bonus"

# ---------------------------------------------------------------------------
# 付奖模式（`--style-bonus-mode`）
# ---------------------------------------------------------------------------

#: `hand`（缺省）= 这个功能从 2026-10-09 起的**老口径**：奖励是**小局级常数**、该小局四行同值。
MODE_HAND = "hand"
#: `decision` = **逐决策**付奖：奖励只落在"真的做了那个动作"的那一行，其余行为 0。见模块 docstring。
MODE_DECISION = "decision"
MODES: tuple[str, ...] = (MODE_HAND, MODE_DECISION)

#: 模式的**环境变量**入口（与 `--style-bonus` 同理：`tools/run-league.ps1` 不许改，
#: 所以"不改那个脚本也能换模式"这件事只能由环境变量 + `loop.py` 的缺省值完成）。
MODE_ENV = "MAHJONG_STYLE_MODE"


def check_mode(mode: str) -> str:
    """模式名的**唯一**校验点（未登记的模式一律当场报错，⛔ 不猜、不近似、不默默当 `hand`）。"""
    m = (mode or MODE_HAND).strip()
    if m not in MODES:
        raise BonusSpecError(f"付奖模式只认 {'|'.join(MODES)}（收到 {mode!r}）")
    return m


#: 轴 → "**这一行**是不是那个动作"的**前缀**判据（`STRICT_PREFIX` 模式）。
ROW_ACTION_PREFIX: dict[str, tuple[str, ...]] = {
    "riichi": (RIICHI_PREFIX,),
    "meld": MELD_PREFIXES,
    # ★★ **副露种类轴**（`pon` = 碰取向）：付奖行 = 鸣牌动作行 ∧ `chosen` 以 `pon:` 开头。
    #   ⛔ 另一侧（`chi:`/`kan:`）**一分钱不付**（单向）；与 `meld` 轴正交（那只问"鸣不鸣"）。
    "pon": (MELD_TYPE_PREFIX["pon"],),
}
#: 轴 → "**这一行**是不是那个动作"的**整串相等**判据（`tsumo` / `ron` 是无参数动作）。
ROW_ACTION_EXACT: dict[str, tuple[str, ...]] = {
    "win": ("tsumo", "ron"),
}
#: ★ **"能而不为"型**的行级判据（`dama`，2026-10-10 加）：轴 → **本来可以选、这一行偏偏没选**的动作前缀。
#: 判据 = "这一行的 `legal` 里有该前缀的动作" **∧** "这一行的 `chosen` 不是它"。
#: 为什么必须是这一条（而不是"手级未立直"）：付奖行要么对应**真实的行为选择**，要么就是把一个
#: 结果量换个名字重复加权（`win` 轴的实测教训，见模块 docstring）——"能立而不立"正是那个选择本身。
ROW_ACTION_DECLINE: dict[str, str] = {"dama": RIICHI_PREFIX}

#: ★★ **巡目阈值轴**（`riichi_turn` = **早立直**，2026-10-11 加）：轴 → 宣言巡目的**上限 T**。
#: 付奖行 = **宣言立直的那一行**（与 `riichi` 轴同一行规则）**且**该行的巡目 ≤ T。
#: ⛔ **方向单向**：巡目 > T 的宣言行**一分钱都不付** —— 既不做负奖，也不做"越晚越少"
#: （那两种写法都会把"晚立直"也变成一个被优化的方向，与"越早越好"这条约定自相矛盾）。
#: T=8 的来由（普查：立直巡目 均值 9.28 / 中位数 9 ⇒ 均值量级）与剂量换算写在
#: `S:\mahjong-training\w-ladder\PREREGISTRATION-RIICHI-TURN.md`。
#: ⚠ 手级镜像（`HandState.early_decl`）**只实现了这一条**巡目轴；再加一条（不同的 T）就必须同时加
#: 它的镜像，否则两条口径漂移（`row_is_axis_action` 与手级指示量会各说各话）。
ROW_TURN_LIMIT: dict[str, int] = {"riichi_turn": 8}
#: 轴 → "这一行是不是那个**带巡目门槛**的动作"的**前缀**判据（`row_is_action` 的第三个分支）。
ROW_ACTION_TURN: dict[str, str] = {"riichi_turn": RIICHI_PREFIX}

#: ★★ **"被威胁时切了安全牌"型**的行级判据（`fold` = 降り率，2026-10-11 加）：轴 → **出牌**的动作前缀。
#: 付奖行 = **出牌行** ∧ **被威胁**（该行 `obs.riichi` 里**别家**有真）∧ 打出的牌对**至少一家**威胁家
#: 是**現物**（重建规则见 `_genbutsu_from_events`）。
#: ★ 2026-10-11 第三轮（本批）：安全口径由"对**每一个**威胁家都是現物"（`all`）**放宽**为
#: "对**至少一家**威胁家是現物"（`any`）—— 旧口径付奖行率仅 1.0394%/行（刚过门槛）、一代配对
#: SE 0.170pp ⇒ CI 含 0；新口径普查实测 ≈8.6%/行。判据侧同步（`tools/w-ladder.py` 的 `FOLD_KEYS`）。
#: ⛔ **方向单向**：不安全那一侧（"押し"）**一分钱都不付** —— 不做负奖、也不做"越安全越多"。
#: ⛔ **seat 口径写死**：威胁集只数**别家**（`fold_row_ok(obs, chosen, seat)` 的 `seat` 必填），
#: 因为立直之后只能摸切、没有选择权 ⇒ 那几行不是降り决策（上一轮"字面口径"的教训见 `fold_row_ok`）。
#: ⚠ 弱口径的来由：引擎危险度（`rules/Danger.java`）**不在 `obs` 里** ⇒ 只能用**公开舍牌**算的**現物**
#: 当"安全"的弱代理（`AXIS-CENSUS.md` 的"⛔ 取不到的量"）。剂量与判决见
#: `S:\mahjong-training\w-ladder\PREREGISTRATION-FOLD-ANY.md`。
ROW_ACTION_FOLD: dict[str, str] = {"fold": "discard:"}

#: 能在 `decision` 模式下当**付奖轴**的那些轴（含"能而不为" / "带巡目门槛" / "被威胁时降り"那三条）。
ROW_ACTION_AXES: tuple[str, ...] = tuple(sorted(set(ROW_ACTION_PREFIX) | set(ROW_ACTION_EXACT)
                                                 | set(ROW_ACTION_DECLINE)
                                                 | set(ROW_ACTION_TURN)
                                                 | set(ROW_ACTION_FOLD)))

#: ⛔ **退化成恒真的组合**（付奖行**蕴含**了伴随量 ⇒ 质量轴形同虚设 = `PAIR_NEVER`）。
#: 键 = 轴，值 = 该轴上**恒真**的伴随量（`parse_bonus` 见到就报错，⛔ 不静默放行）。
#: 加这一条的直接原因：`win=5000:no_deal` 在真数据上实测 `action_rows == paid_rows == 2230`
#: 且 `blocked_rows == 0`（和了者不可能是放铳者）⇒ 奖励退化成"对和了的重复加权"。
IMPLIED_PAIRS: dict[str, tuple[str, ...]] = {
    "win": ("no_deal", "win"),
}

#: ⛔ **只在 `decision` 模式成立**的付奖轴：它们的手级合并量**不是**那个行为（见模块 docstring）
#: ⇒ `hand` 模式下用它们等于**静默换轴**，所以 `check_mode_axes` 当场报错。
#: ★ `riichi_turn`：`hand` 的合并量 = "这一小局有没有人早立直"（四席取或），**不是**"宣言立直的那一行"。
#: ★★ `fold`（2026-10-11）：`hand` 的合并量 = "这一小局四席取或的降り行有过吗"，**不是**"这一行" ——
#: 而本轴的全部意义就是**逐行**把钱落到"被威胁时切了安全牌"那一手（弱口径、单向）⇒ `hand` 模式下用它
#: 等于静默换轴，当场报错。
DECISION_ONLY_AXES: tuple[str, ...] = ("dama", "riichi_turn", "fold", "pon")

#: ⛔★ **实测层面的"恒真"闸门**（比 `IMPLIED_PAIRS` 的静态表更硬）：轴 → 必须在真数据上**真的拦下过行**
#: 的那些伴随量。`IMPLIED_PAIRS` 只能登记"结构上必然蕴含"的组合（`win` 那两条），而 `riichi_turn`+`no_deal`
#: **不蕴含**（立直之后照样可能放铳）—— 所以只能靠**实测**：`blocked_rows == 0` ⇒ 伴随量在付奖行上恒真
#: ⇒ 质量轴形同虚设 = 对同一件事重复加权（`win=w:no_deal` 的病）⇒ 当场报错（⛔ 不静默放行）。
ROW_PAIR_MUST_BLOCK: dict[str, tuple[str, ...]] = {"riichi_turn": ("no_deal",),
                                                   "fold": ("no_deal",),
                                                   # ★ 碰取向：碰完**照样可能放铳**（普查 `D:pon`×`no_deal`
                                                   #  81.3% 真门）⇒ 实测必须真的拦下过行。
                                                   "pon": ("no_deal",)}

#: ⛔★ **"取不到鸣牌行"⇒ 硬拒**（`pon` 轴的负向判据）。
#: 为什么必须硬拒而不是"μ=0 于是 σ=0 报错就算了"：这一轴的**第一条判据**就是"碰的行有多少"
#: （≥1%/行门槛 + 主判据 `pon_type_rate` 的分母），而"统计范围里一行鸣牌都没有"意味着
#: **分母取不到** —— 那时任何读数都无定义。⛔ 不静默付 0、也不退化成"另一种副露"，
#: 而是当场报错（要么轨迹里根本没有鸣牌决策行，要么 `--style-bonus-whiten`/`--student` 的
#: 范围与轨迹对不上）。⚠ 只对**真的用了**这根轴的配置生效 ⇒ 别的轴 / 缺省路径一字不动。
MELD_TYPE_ERR = ("副露种类轴（`pon`）：统计范围里**一行鸣牌决策行都没有**（`chosen` 以 "
                 "`chi:`/`pon:`/`kan:` 开头的行 = 0）⇒ 这一轴的**分母取不到**、付奖行率与"
                 "`pon_type_rate` 都无定义。⛔ 不静默付 0（那等于把这条轴悄悄关掉）；"
                 "要么这份轨迹里有鸣牌决策行，要么别用这条轴")

#: ⛔★ **"取不到威胁状态 / 現物"⇒ 硬拒**（比"静默按 False 付 0"硬；`fold` 轴的负向判据）。
#: 为什么必须硬拒而不是跳过那几行：`obs.riichi` 取不到 = "这一行**有没有**被威胁"这件事**无定义**，
#: 按 False 处理会把付奖行率**静默压低**（而这一轴的第一个判据恰恰是**付奖行率**）；反过来，
#: `obs.riichi[s]` 为真却在 `obs.events` 里**找不到** `s` 的 `riichi` 事件 = 現物**重建不出来**
#: ⇒ "打出的牌对它安全吗"无定义，静默当不安全同样是把付奖行率压低。
#: ⛔ 只对**真的用了**这根轴的配置生效（`_threat_gate_needed`）—— 别的轴 / 缺省路径**一字不动**。
FOLD_GENBUTSU_ERR = ("被威胁时的降り（`fold` 轴）：这些行**取不到威胁状态或現物**（"
                     "`obs.riichi` 缺字段 / 该威胁家在 `obs.events` 里没有 `riichi` 事件 ⇒ "
                     "現物重建不出来）。⛔ 按 False 静默跳过 = 把**付奖行率**（本轴的第一条判据）"
                     "悄悄压低 ⇒ 当场报错；要么这份轨迹带 `obs.riichi` + `obs.events`（obs v3），"
                     "要么别用这条轴")

#: ★ **多家荣和的语义陷阱**（2026-10-10 加，见模块 docstring 同名小节）：行级判据除了"这一行
#: 执行了该轴的动作"，有些轴还要"**我就是被记录的那家**"。只有 `win` 需要它 —— 双响时两家都
#: 执行了 `ron`，而手级口径（`AXES['win']` + 生产端 `winners[0]`）只记**离放铳者最近那家**。
#: ⛔ 不加这条合取，手级与行级必然漂移（实测 30/11258 小局），白化那一遍就会抛 `BonusSpecError`。
ROW_SELF_SEAT: dict[str, str] = {"win": "winner"}

#: 违规**样例**最多留几条（⚠ 只影响"列几条给日志看"，**不影响**报出来的总数）。
#: 上一轮的事故：`if bad and len(viol) < 8: viol.append(...)` 之后拿 `len(viol)` 当"共 N 个小局"
#: 打印 ⇒ 日志**恒说 8**（真实是 30）—— 把"样例上限"当成了计数。
VIOL_SAMPLES = 8


def legal_has(legal, prefix: str) -> bool:
    """这一行的 `legal` 里有没有以 `prefix` 开头的动作（`dama` 轴的行级判据要的那一半）。

    ⚠ `legal` 取不到（`None`）**不是** False：那是"这一行的合法动作集在轨迹里根本没有"，
    对"能立而不立"这种**依赖 legal** 的判据等于**无定义** ⇒ 由 `row_is_action` 当场报错
    （⛔ 不猜、也不静默按 False 处理 —— 那会让整条轴悄悄付 0）。
    """
    if legal is None:
        raise BonusSpecError(
            "这一行的 `legal` 取不到，而当前轴的行级判据**必须**知道'合法动作里有没有它'"
            "（`dama` = 能立而不立，见 `ROW_ACTION_DECLINE`）。⛔ 不猜、不静默按 False 付 0："
            "要么这份轨迹带 `legal` 字段（PROTOCOL §8.3），要么别用这条轴")
    return any(str(k).startswith(prefix) for k in legal)


def tile_kind(code) -> str:
    """牌码 → **牌种**键（`"0m"`（赤五）与 `"5m"` 归一成同一个键；`None`/空 → `""`）。

    ⚠ 为什么现物要在**牌种**层面比：赤五与普通五是**同一张牌种**（`現物` 的语义就是牌种），
    而 `obs.drawn` / `events[].tile` 里赤五写作 `"0m"` ⇒ 不归一的话"打出的赤五恰好是现物"
    会被判成**不安全**（静默压低付奖行率）。
    """
    t = str(code or "")
    if not t:
        return ""
    return ("5" + t[-1]) if t[:-1] == "0" else t


def genbutsu_from_events(events) -> tuple[dict[int, set[str]], dict[int, str]]:
    """★ **只用公开事件**重建“每一个威胁家的現物集合”（`AXIS-CENSUS.md` 的弱口径安全牌）。

    规则（**麻将标准口径：进过河就不可能与荣**）：
      * `type == "riichi"` 的那条事件 = 该 `actor` 的**宣言点**；
      * 該 `actor` 的現物 = 宣言点**之后**的 `discard` 事件牌 **＋ 宣言牌本身**
        （`discard.sideways == true` 的那张 —— `PROTOCOL.md` §8.2：`sideways` = 立直宣言那张，
        **顺延牌不算** ⇒ 用 `sideways` 认它，⛔ 不猜）。

    ⚠ 「顺延」也被这条规则覆盖：横置那格被鸣走时 `events` 里仍是原先那条 `discard`
    （牌码 = 宣言被鸣那张，它**进过河**）⇒ 算現物；**顺延之后**打的那张（`sideways=false`）同样在
    宣言点之后 ⇒ 也进集合。⛔ 这里**不读** `obs.discards`（那是“现在剩下的河”，与“当时进过河的牌”
    不是一回事），也**不读**别家手牌/牌山/里宝。

    @return `(現物集合, 宣言牌)` —— ⛔ **只在真的见到 `riichi` 事件之后**才有这个键；
        没见到的 actor **不在**返回里（那是“現物重建不出来”，由调用方**硬拒**，见 `FOLD_GENBUTSU_ERR`）。
    """
    ev = [e for e in (events or []) if isinstance(e, dict)]
    # ★★ **锚点 = `riichi` 事件的下标**（不是"`sideways` 那张牌"）——
    #   实测反例（`g0.jsonl` hand 0）：别家的**宣言牌**在 `events` 里排在他自己那条 `riichi`
    #   事件**之前**（服务端先广播 `discard` 再广播 `riichi`）⇒ 拿"`sideways`"    #   当锚会把**之前**的舍牌也算进現物集合（那些牌他**没有**在立直后打过
    #   ⇒ 把危险牌误认成安全牌）。
    #   用 `riichi` 事件作锚时，它**自己的**宣言牌只会落在锚点之后（实测
    #   `riichi_idx < sideways_idx` 恒成立）⇒ 那张自然进集合；而别家在他之前打的牌
    #   不会被算进来。
    ri_idx: dict[int, int] = {}
    for i, e in enumerate(ev):
        a = e.get("actor")
        if e.get("type") == "riichi" and isinstance(a, int) and a not in ri_idx:
            ri_idx[a] = i
    after: dict[int, set[str]] = {a: set() for a in ri_idx}
    decl_tile: dict[int, str] = {}
    for i, e in enumerate(ev):
        a = e.get("actor")
        if not isinstance(a, int):
            continue
        if e.get("type") == "discard":
            if a in ri_idx and i >= ri_idx[a]:
                tk = tile_kind(e.get("tile"))
                if tk:
                    after[a].add(tk)        # 锚点之后的舍牌（含自己的宣言牌与顺延牌）
            if e.get("sideways") and a not in decl_tile:
                # ★ 宣言牌本身也算現物（麻将标准口径：进过河不与荣）。
                #   ⚠⚠ **不加 `i >= ri_idx[a]`**：服务端先广播 `discard` 再广播 `riichi`
                #   ⇒ 别家观察到的那条 `sideways` 事件可能在 `riichi` 之前；而 `sideways`
                #   按 PROTOCOL §8.2 恰好只有两种：宣言牌 / 宣言牌被鸣后的顺延牌
                #   —— **两者都进过河、都是現物**（与判据侧逐字同口径）。
                decl_tile[a] = tile_kind(e.get("tile"))
    return after, decl_tile


def threat_seats(obs: dict, seat: int | None = None) -> tuple[bool, tuple[int, ...]]:
    """★ **被威胁判定 + 取不到状态**（只用公开信息）：这一行有几个威胁家。

    ⚠ **自己已立直 ⇒ 不算"被威胁"（返回空威胁集）**：立直之后只能模切、**没有
    选择权** ⇒ 那几行**不是降り决策**（把它们算进付奥行 = 把"被迫模切"当成"会降り"）；
    而且实测（1000 场 `g01`）那 300 行正好是"两套口径对不上"的全部来源。这一条必须与判据侧
    （`tools/w-ladder.py` 的 `others = [s for s in threats if s != stu]`）**逐字同口径** —— 否则两边数的不是同一个集合。

    @return `(available, threats)`：
      * `available=False` ⇒ 这一行的 `obs.riichi` **取不到** ⇒ "有没有被威胁"**无定义**
        （⛔ 由调用方硬拒，见 `FOLD_GENBUTSU_ERR`；绝不按"没威胁"静默处理）；
      * `threats` = 该数组里为真的**别家**座位号（`seat` 给了就排掉自己；给 `None`
        则四家取真 —— 仅给"看一眼整体有没有人立直"用）。
    """
    ri = (obs or {}).get("riichi")
    if not isinstance(ri, (list, tuple)):
        return False, ()
    return True, tuple(s for s in range(min(SEATS_PER_TABLE, len(ri)))
                       if bool(ri[s]) and (seat is None or s != int(seat)))


def genbutsu_sets(obs: dict, threats) -> tuple[bool, dict[int, frozenset]]:
    """这一行**每一个威胁家**的現物集合（`(available, {seat: 集合})`）。

    `available=False` ⇒ 至少有一个威胁家在 `obs.events` 里**找不到 `riichi` 事件** ⇒ 它的現物
    **重建不出来**（"打出的牌对它安全吗"无定义）。⛔ 不猜、也不静默当不安全（那会把付奖行率压低）。
    """
    after, _decl = genbutsu_from_events((obs or {}).get("events"))
    out: dict[int, frozenset] = {}
    for s in threats:
        if s not in after:
            return False, {}
        out[s] = frozenset(after[s])
    return True, out


def fold_row_ok(obs: dict, chosen, seat: int) -> bool:
    """★ `fold` 轴的**唯一**行级判据：**这一行**是"出牌 ∧ 被威胁 ∧ 对**至少一家**威胁家是現物"吗。

    ⛔ **单向**（见 `ROW_ACTION_FOLD`）：不安全那一侧返回 False 一分钱都不付。
    ⛔ 取不到威胁状态 / 現物 ⇒ 由 `threat_missing` 的**硬拒**负责（本函数在这里按 False 返回，
    但调用方（`HandState.feed`）**同一行**就会把它记进 `row_threat_missing` 并当场报错）。

    ★★ **`seat` = 这一行的行动者**（必填、且由调用方**显式**传入；⛔ 不从 `obs["seat"]` 里读）：
    威胁集 = `obs.riichi` 里为真的**别家**座位（`s != seat`）。为什么必须写死：立直之后只能摸切、
    **没有选择权** ⇒ 那几行不是降り决策。上一轮链实测的教训 —— 付钱那条路（`_row_obs` 不带 seat）
    偷偷按"含行动者自己"的**字面口径**算（冻结账比 HEAD 多 threat +4892 / fold +96 / push +4796），
    所以现在把座位做成**必填参数**：两条路（`HandState.feed` / `BonusTracker._decision_rule`）
    都只能从**行的 `seat`** 取，⛔ 没有"缺字段就退回四席取真"的岔路。

    ★★ **安全口径（2026-10-11 第三轮，本批改动）= 「对至少一家威胁家是現物」**（`any`）：
    旧口径是"对**每一个**威胁家都是現物"（`all`）。放宽的依据与代价见
    `S:/mahjong-training/w-ladder/PREREGISTRATION-FOLD-ANY.md`：旧口径付奖行率仅 1.0394%/行
    （刚过 1% 门槛）⇒ 一代配对 SE 0.170pp、CI 含 0；新口径普查实测付奖行率 ≈ 8.6%/行。
    ⚠ 判据侧（`tools/w-ladder.py` 的 `_student_per_game` / `FOLD_KEYS`）**同步改成 `any`**，
    由 ≤50 场**口径冒烟测试**逐键 + 逐行集合对账（⛔ "命令行一样"不算）。

    ⚠ 威胁判定**按行取**（当行的 `obs.riichi` 快照），不是"本小局曾经有人立直" —— 见模块 docstring。
    """
    t = str(chosen or "")
    if not t.startswith(ROW_ACTION_FOLD["fold"]):
        return False                                   # 不是出牌行（鸣牌询问 / pass / riichi / win）
    avail, threats = threat_seats(obs, seat)
    if not avail or not threats:
        return False
    ok, sets = genbutsu_sets(obs, threats)
    if not ok:
        return False
    tk = tile_kind(t.split(":", 1)[1])
    if not tk:
        return False
    return any(tk in sets[s] for s in threats)          # ★★ 「对至少一家威胁家是現物」（旧 = all）


def threat_missing(obs: dict, chosen, seat: int) -> bool:
    """★ **硬拒判据**（见 `FOLD_GENBUTSU_ERR`）：这一行是**出牌行**却取不到"被威胁 / 現物"吗。

    ⚠ 只在**出牌行**上判：鸣牌询问行（`chosen` 是 `pass`/`chi:`/…）与和了行没有"降り"这件事，
    对它们要求 `obs.riichi`/`events` 只是白白硬拒（而它们本来就不可能是付奖行）。
    ⚠ `seat` 与 `fold_row_ok` **同一个**（这一行的行动者；见那里的 ★★ 段）。
    """
    t = str(chosen or "")
    if not t.startswith(ROW_ACTION_FOLD["fold"]):
        return False
    avail, threats = threat_seats(obs, seat)
    if not avail:
        return True
    if not threats:
        return False
    ok, _sets = genbutsu_sets(obs, threats)
    return not ok


def declared_turn(obs: dict) -> int | None:
    """这一行的**宣言巡目**（`obs.player_draws`）；取不到返回 `None`（⛔ 不猜 0）。

    ⚠ 为什么是 `obs.player_draws` 而不是 `obs.riichi_turn[seat]`：后者是"**已经**宣言过"之后的公开位
    （服务端先广播 `riichi` 再广播 `discard`）⇒ **宣言那一手它还是 0**。`tools/style-vector.py` 的
    `avg_riichi_turn` 也是这么回退的（`riichi_turn` 为 0 时用宣言那一行的 `player_draws`）——
    两边**同一把尺子**，否则判据侧与奖励侧量的是两个数。
    ⛔ 取不到时**返回 None**（不返回 0）：0 必然 ≤ T ⇒ 那等于把"取不到"静默读成"最早立直"。
    """
    v = (obs or {}).get("player_draws")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def row_is_action(axis: str, chosen: str, legal=None, turn: int | None = None) -> bool:
    """**这一行**（`chosen` = 实际执行的动作键）是不是轴 `axis` 的那个动作。

    ⛔ 没有行级语义的轴（`deal` / `no_deal` / `win_points`）在这里**报错**，不返回 False ——
    返回 False 会让"这个轴在 decision 模式下付不出奖"变成**静默的 0**（正是本仓最忌讳的降级）。

    ★ `dama`（`ROW_ACTION_DECLINE`）要**多一个入参** `legal`：它的判据是"合法的里**有**它、
    这一行**偏不选**它"（`legal_has(legal, RIICHI_PREFIX) and not chosen.startswith(...)`）。

    ★ `riichi_turn`（`ROW_ACTION_TURN`）要**多一个入参** `turn`（= 这一行的 `obs.player_draws`）：
    判据是"这一行**是**宣言行 ∧ 该行巡目 ≤ T"。非宣言行**不需要**巡目（直接 False，⛔ 不报错）；
    是宣言行却取不到巡目 ⇒ **当场报错**（猜 0 = 它必然 ≤ T = 把"取不到"读成"最早立直"）。

    ★ `fold`（`ROW_ACTION_FOLD`）**不走这个函数**：它的行级判据要**整份 `obs`**（`obs.riichi` +
    `obs.events` 重建的現物），不是"`chosen` 长什么样"这一个字符串 ⇒ 唯一落点是 `fold_row_ok`
    （由 `row_is_axis_action` 转过去）。⛔ 在这里按前缀返回 True 会把"被威胁时切了安全牌"偷换成
    "随便打了一张牌"（那正是**判据换尺子**）。下面是**显式拒绝**，不静默。

    ⚠ 这只是行级判据的**一半**：`win` 轴还要过 `ROW_SELF_SEAT`（我就被记录的那家）那一关，
    两者合起来才是 `row_is_axis_action`。别在别处直接用这个函数决定"要不要付奖"。
    """
    chosen = str(chosen or "")
    if axis in ROW_ACTION_FOLD:
        raise BonusSpecError(
            f"轴 {axis!r} 的行级判据**不是**'`chosen` 长什么样'，而是"
            f"`fold_row_ok(obs, chosen, seat)`（出牌 ∧ 被威胁（**别家**立直）∧ 对**至少一家**威胁家"
            f"是現物）—— 它要**整份 `obs`**（`obs.riichi` + `obs.events` 重建的現物）与这一行的"
            f"**行动者座位**。⛔ 不在这里按前缀返回 True"
            f"（那等于把'被威胁时切了安全牌'偷换成'随便打了一张牌'）")
    if axis in ROW_ACTION_PREFIX:
        return chosen.startswith(ROW_ACTION_PREFIX[axis])
    if axis in ROW_ACTION_EXACT:
        return chosen in ROW_ACTION_EXACT[axis]
    if axis in ROW_ACTION_DECLINE:
        pref = ROW_ACTION_DECLINE[axis]
        return legal_has(legal, pref) and not chosen.startswith(pref)
    if axis in ROW_ACTION_TURN:
        # ★ **早立直**（见 `ROW_TURN_LIMIT`）：先看这一行是不是宣言行 —— 不是 ⇒ 直接 False
        #   （非宣言行没有"宣言巡目"这件事，**不该**因为取不到巡目而报错）；
        #   是宣言行却取不到巡目 ⇒ ⛔ 当场报错（不猜 0：猜 0 = 必然 ≤ T = 把"取不到"读成"最早立直"）。
        if not chosen.startswith(ROW_ACTION_TURN[axis]):
            return False
        if turn is None:
            raise BonusSpecError(
                f"轴 {axis!r} 的付奖行是「**宣言立直的那一行**」，判据要**这一行自己的巡目**"
                f"（`obs.player_draws`），但这一行取不到 ⇒ ⛔ 不猜 0（猜 0 = 它必然 ≤ T，"
                f"等于把'取不到'静默读成'最早立直'）。要么这份轨迹带 `obs.player_draws`，"
                f"要么别用这条轴")
        return int(turn) <= int(ROW_TURN_LIMIT[axis])
    raise BonusSpecError(
        f"轴 {axis!r} 在 `decision` 模式下没有'哪一行做了它'的定义（它是**结局量**，不是行级动作）。"
        f"能当付奖轴的只有：{', '.join(ROW_ACTION_AXES)}；它当**伴随质量轴**（`PAIR`）仍然可用。"
        f"⛔ 不猜一行、也不静默退回 `hand` 模式")


def row_is_axis_action(axis: str, seat: int, chosen: str, st: HandState, idx: int | None = None) -> bool:
    """★ `decision` 模式的**唯一**行级判据：这一行**执行了该轴动作** ∧ **我就是被记录的那家**。

    `ROW_SELF_SEAT` 里登记过的轴（现在只有 `win`）带上第二条合取：多家荣和时**两家都执行了
    `ron`**，但被记录的和了者只有一家（`winners[0]` = 离放铳者最近那家）⇒ 只有**那一家**的
    和了行拿钱。⛔ 判据侧（`AXES`/`style-vector.py`）仍用"手级被记录和了者"，一个字都不动 ——
    这里是**软信号少付**（学生和了行里约 1.3%），不是"换了尺子"。

    ★ `dama`（"能而不为"）用 `st.row_legal_riichi[idx]`（喂行时按 `legal` 存下来的那个布尔）：
    合法动作里有 `riichi:*` 且这一行没选它 ⇒ 付奖行。取不到（`None`）⇒ `legal_has` 当场报错。
    """
    if axis in ROW_ACTION_DECLINE:
        pref = ROW_ACTION_DECLINE[axis]
        if pref != RIICHI_PREFIX:
            raise BonusSpecError(
                f"轴 {axis!r} 的'能而不为'前缀是 {pref!r}，但 `HandState` 只按行存了 "
                f"`{RIICHI_PREFIX}` 那一份 legal 标志 —— 加新前缀就要同时加它的存法（⛔ 不猜）")
        has = None
        if idx is not None and 0 <= idx < len(st.row_legal_riichi):
            has = st.row_legal_riichi[idx]
        if has is None:
            return row_is_action(axis, chosen, None)      # 唯一报错点（`legal_has` 里）
        return bool(has) and not str(chosen or "").startswith(pref)
    if axis in ROW_ACTION_TURN:
        # ★ **早立直**：巡目从 `HandState` 的**逐行**记录里取（`feed` 时按同一行的 `obs.player_draws`
        #   存下来的那一份）—— ⛔ 不在这里重读 `row`（两条来源必然漂移）。取不到 ⇒ `row_is_action` 报错。
        t = None
        if idx is not None and 0 <= idx < len(st.row_declared_turn):
            t = st.row_declared_turn[idx]
        return row_is_action(axis, chosen, None, t)
    if axis in ROW_ACTION_FOLD:
        # ★★ **被威胁时的降り**（`fold`）：判据要**这一行自己的 `obs`**（当行的 `obs.riichi` 快照 +
        #   `obs.events` 重建的現物）⇒ 从 `HandState` 的**逐行**记录里取那一份
        #   （`feed` 时按**同一个** `fold_row_ok` 判出来的布尔）。⛔ 不在这里重读 `row` 的 obs：
        #   两条来源（预扫的 `HandState` vs 写列时的 `row`）各算一套必然漂移 —— 那是本仓最经典的
        #   静默错位。取不到（行数对不上）⇒ **当场报错**，不按 False 付 0。
        if idx is None or not (0 <= idx < len(st.row_fold)):
            raise BonusSpecError(
                f"轴 {axis!r} 的付奖行判据要**这一行自己的**公开状态（`obs.riichi` + `obs.events` 重建的"
                f"現物），但取不到第 {idx!r} 行的记录（`HandState.row_fold`）—— 两条来源漂移了")
        return bool(st.row_fold[idx])
    if not row_is_action(axis, chosen):
        return False
    if ROW_SELF_SEAT.get(axis) == "winner":
        return st.winner == seat
    return True


def check_mode_axes(spec: BonusSpec, mode: str) -> None:
    """⛔ 付奖轴与付奖模式的**配对闸门**（`dama` 只在 `decision` 模式成立，见模块 docstring）。

    为什么要有它：`hand` 模式付的是**小局级合并量**（四席取或），而 `dama` 的合并量是
    "桌上有没有人没立直"——**根本不是**"能立而不立"。那样跑出来的实验与轴名无关（静默换轴），
    正是本仓最忌讳的降级 ⇒ 报错，不猜、不近似、不偷偷退回 `decision`。
    """
    m = check_mode(mode)
    if m == MODE_DECISION:
        return
    bad = [a.name for a in spec.axes if a.name in DECISION_ONLY_AXES]
    if bad:
        raise BonusSpecError(
            f"这些轴只在 `decision` 模式成立：{', '.join(bad)} —— 它们的行级判据是'**能而不为**'"
            f"（`legal` 里有它、这一行没选它），而 `hand` 模式付的是小局级合并量，**不是**那个行为。"
            f"⛔ 不静默换轴：要么加 `--style-bonus-mode decision`，要么换一条轴")



# ---------------------------------------------------------------------------
# 轴定义
# ---------------------------------------------------------------------------

#: **成对质量轴**（`PAIR`）的名字表：**只有它为真时才付奖**。
#: 表里**没有**、也**不允许**出现"恒真"（见 `parse_bonus`）—— 那正是"只按立直了付奖"的写法。
PAIR_KEYS: dict[str, tuple[str, str]] = {
    "no_deal": ("该小局**未放铳**（`hand_loser != seat`，含流局）",
                "把'立直'与'立直之后别点炮'绑成一条质量轴：奖励立直率时不会去换放铳率"),
    "win": ("该小局**和了**（`hand_winner == seat`）",
            "质量轴 = 立直且和了（更严；但'立直后被别人自摸'也会被判成没质量，会更偏向早立直）"),
    "deal": ("该小局**放铳**（⚠ 反向质量轴：只有放铳才付奖 —— 只留给负向对照，别用于正推）",
             "负向对照用；正推里用它等于奖励放铳"),
}

#: 轴定义表（**唯一数据源**：`parse_bonus` 校验用它、审计打印用它、自检引用它）。
#: `kind` 只决定白化用哪套 μ/σ（`bernoulli` = `p, √(p(1−p))`；`continuous` = 样本均值/标准差）。
AXES: dict[str, dict] = {
    "riichi": {
        "kind": "bernoulli",
        "meaning": "立直率（该席本小局宣言过立直）",
        "src": "obs.riichi[seat] / obs.riichi_turn[seat] > 0 / chosen 以 riichi: 开头（三者取或）",
        "style_vector": "riichi_rate = riichi_hands / seat_hands",
    },
    "meld": {
        "kind": "bernoulli",
        "meaning": "副露率（该席本小局有吃/碰/杠任一，含暗杠）",
        "src": "obs.melds[seat] 非空 / chosen 以 chi:/pon:/kan: 开头",
        "style_vector": "meld_rate = meld_hands / seat_hands",
    },
    "win": {
        "kind": "bernoulli",
        "meaning": "和了率（该席本小局是和了者）",
        "src": "决策行的 hand_agari 且 hand_winner == seat",
        "style_vector": "win_rate = wins / seat_hands",
    },
    "deal": {
        "kind": "bernoulli",
        "meaning": "放铳率（该席本小局是放铳者）",
        "src": "决策行的 hand_loser == seat",
        "style_vector": "deal_rate = deals / seat_hands",
    },
    "no_deal": {
        "kind": "bernoulli",
        "meaning": "未放铳率（放铳率的补）",
        "src": "决策行的 hand_loser != seat（含流局）",
        "style_vector": "1 − deal_rate",
    },
    "win_points": {
        "kind": "continuous",
        "meaning": "小局打点（和了就取 `hand_delta[seat]`，没和就是 0；**点**）",
        "src": "决策行的 hand_delta[seat]（和了者）否则 0",
        "style_vector": "avg_win_score 的分子口径（win_points）",
    },
    "dama": {
        "kind": "bernoulli",
        "meaning": ("默听（**能立而不立**）：行级付奖行 = 立直是合法选项却没选它的那一行；"
                    "手级指示量 = 该席本小局**未立直**（只用于伴随/对账语义，见模块 docstring）"),
        "src": ("行级：该行 `legal` 里有 `riichi:*` 且 `chosen` 不是 `riichi:*`（`ROW_ACTION_DECLINE`）；"
                "手级：`_seat_riichi` 取或为假"),
        "style_vector": "dama_rate = dama_wins / wins（dama_wins = 和了且该小局未立直）",
    },
    "riichi_turn": {
        "kind": "bernoulli",
        "meaning": ("早立直（**宣言巡目 ≤ T 的立直**）：行级付奖行 = 宣言立直的那一行且该行巡目 ≤ T；"
                    "手级指示量 = 该席本小局有过一次这样的宣言"),
        "src": ("行级：`chosen` 以 `riichi:` 开头 ∧ `obs.player_draws` ≤ T"
                "（`ROW_ACTION_TURN` / `ROW_TURN_LIMIT`，T = 8）；"
                "手级：`HandState.early_decl`（同一个 `declared_turn(obs)` + 同一个 T）"),
        "style_vector": "avg_riichi_turn = riichi_turn_sum / riichi_turn_known（判据侧的**聚合**读数；"
                        "逐场主读数在 `tools/w-ladder.py` 的 `riichi_turn_mean`）",
    },
    "fold": {
        "kind": "bernoulli",
        "meaning": ("被威胁时的降り（**切了对至少一家威胁家安全的現物**）：行级付奖行 = 出牌行 ∧ "
                    "被威胁（该行 `obs.riichi` 里**别家**有真）∧ 打出的牌对**至少一家**威胁家"
                    "（⛔ 只数别家）是現物；手级指示量 = 该席本小局至少有一行这样的"
                    "（只服务结构不变量，⛔ 不是降り率）"),
        "src": ("行级：`fold_row_ok(obs, chosen, seat)`（出牌前缀 + `obs.riichi` 当行快照 + "
                "`obs.events` 重建的現物，見 `genbutsu_from_events`；仅公开信息）；"
                "手级：`HandState.fold_hit`（同一个 `fold_row_ok`）"),
        "style_vector": ("⛔ 不在 `tools/style-vector.py` 里（那是**手级**口径的独立实现）："
                         "本轴的判据是 `tools/w-ladder.py` 的**行级** `fold_rate` = 付奖行 / 被威胁行"),
    },
    "pon": {
        "kind": "bernoulli",
        "meaning": ("副露种类（**碰取向**：用碰而不是吃）：行级付奖行 = **鸣牌动作行** ∧ "
                    "`chosen` 以 `pon:` 开头；手级指示量 = 该席本小局至少有一行这样的"
                    "（只服务结构不变量）"),
        "src": ("行级：`chosen` 以 `pon:` 开头（`ROW_ACTION_PREFIX['pon']` / `MELD_TYPE_PREFIX`；"
                "⛔ 吃/杠那一侧一分钱不付）；手级：`HandState.pon_hit`（同一个前缀判据）"),
        "style_vector": ("⛔ 不在 `tools/style-vector.py` 里（那是**手级**口径的独立实现）："
                         "本轴的判据是 `tools/w-ladder.py` 的**行级** `pon_type_rate` = "
                         "碰的行 / 鸣牌行（+ 每场每席碰的行数）"),
    },
}

#: `PAIR` 里被允许的两个"恒假"哨兵（**只用于红证/负向对照**，不参与任何正推）：
#: `none` = 永不付奖（用来证明"成对质量轴确实在拦"）。
PAIR_NEVER = "none"


class BonusSpecError(SystemExit):
    """`--style-bonus` 写法错误 —— 一律**当场报错**，不静默降级（静默降级 = 塑形没了但不报）。"""


# ---------------------------------------------------------------------------
# ① 解析
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BonusAxis:
    """一个轴的完整配置（`name=w:PAIR`）。"""

    name: str
    weight: float
    pair: str = ""

    def describe(self) -> str:
        return f"{self.name}={self.weight:g}" + (f":{self.pair}" if self.pair else "")


@dataclass(frozen=True)
class BonusSpec:
    """`--style-bonus` 的解析结果（**不可变**；`axes` 的顺序 = 命令行里的顺序）。"""

    axes: tuple[BonusAxis, ...]
    raw: str = ""

    def __bool__(self) -> bool:
        return bool(self.axes)

    def describe(self) -> str:
        return "; ".join(a.describe() for a in self.axes)


def parse_bonus(text: str | None) -> BonusSpec:
    """`'riichi=1.0:no_deal'`（多轴用 `,` 分隔）→ `BonusSpec`。

    文法（每个轴）：`NAME[=WEIGHT][:PAIR]`
      · `NAME` ∈ `AXES`（未登记的轴**报错**，⛔ 不猜、不近似）；
      · `WEIGHT` 缺省 1.0；必须是有限实数（`nan`/`inf` 一律报错）；
      · `PAIR` ∈ `PAIR_KEYS` 或 `none`（`none` = 恒假，只给红证用）。

    ⛔ **没有 `PAIR` 就不许用**（`riichi=1.0` 直接报错）—— 见模块 docstring 的"成对质量轴"：
    只按"立直了"付奖励会学出无脑立直，那是这个模块唯一要防的事。要"不设质量轴"请显式写
    `:none` 并**说清为什么**（正推里那等于关掉防钻空子的那一层）。
    """
    text = (text or "").strip()
    if not text:
        return BonusSpec(axes=())
    out: list[BonusAxis] = []
    seen: set[str] = set()
    for item in text.split(","):
        item = item.strip()
        if not item:
            raise BonusSpecError(f"--style-bonus 里有空项（{text!r}）—— 逗号分隔的每一项都要写成 NAME=w:PAIR")
        head, sep, pair = item.partition(":")
        name, eq, wtext = head.partition("=")
        name = name.strip()
        if name not in AXES:
            raise BonusSpecError(
                f"--style-bonus 里有未登记的轴 {name!r}；已登记：{', '.join(sorted(AXES))}"
                f"（⛔ 不静默忽略 —— 忽略等于「这个轴没生效」却不报错）")
        if name in seen:
            raise BonusSpecError(f"--style-bonus 里轴 {name!r} 出现了两次（权重会互相覆盖，别这么写）")
        seen.add(name)
        weight = 1.0
        if eq:
            try:
                weight = float(wtext.strip())
            except ValueError:
                raise BonusSpecError(f"轴 {name!r} 的权重不是数：{wtext!r}") from None
        if not math.isfinite(weight):
            raise BonusSpecError(f"轴 {name!r} 的权重必须是有限实数（收到 {weight!r}）")
        if not sep:
            raise BonusSpecError(
                f"轴 {name!r} 没有写**成对质量轴**（`{name}=w:PAIR`）。⛔ 只按 `{name}` 付奖励会学出"
                f"无脑打法（那是本模块唯一要防的事）。要质量轴请挑一个：{', '.join(sorted(PAIR_KEYS))}；"
                f"确实要不设质量轴就显式写 `{name}={weight:g}:{PAIR_NEVER}` 并在预注册里说明理由")
        pair = pair.strip()
        if pair not in PAIR_KEYS and pair != PAIR_NEVER:
            raise BonusSpecError(
                f"轴 {name!r} 的成对质量轴 {pair!r} 不认识；可选：{', '.join(sorted(PAIR_KEYS))}"
                f"（或 {PAIR_NEVER} = 恒假，只给红证用）")
        # ⛔ **退化成恒真**的组合：付奖行本身蕴含了伴随量 ⇒ 质量轴一行都拦不下（= `PAIR_NEVER`）。
        if pair in IMPLIED_PAIRS.get(name, ()):
            raise BonusSpecError(
                f"轴 {name!r} 配伴随质量轴 {pair!r} 会**退化成恒真**（付奖行蕴含了它）"
                f"⇒ 质量轴形同虚设、奖励变成「只按 {name} 付奖」= 对同一件事重复加权。"
                f"实测（`win=5000:no_deal`）：`action_rows == paid_rows == 2230`、`blocked_rows == 0`，"
                f"和了率 −1.02pp / 立直率 −1.60pp。⛔ 当场报错，不静默放行"
                f"（`IMPLIED_PAIRS` 里登记过的都不许用；要负向对照请显式写 `:{PAIR_NEVER}`）")
        out.append(BonusAxis(name=name, weight=weight, pair=pair))
    return BonusSpec(axes=tuple(out), raw=text)


# ---------------------------------------------------------------------------
# ② 逐小局提取指示量（**必须从轨迹的事件算**：见 `docs/PROTOCOL.md` §8.4）
# ---------------------------------------------------------------------------

def _seat_riichi(obs: dict, chosen: str) -> bool:
    """该席本小局**是否宣言过立直**：三个来源取或（与 `style-vector.py` 同一口径）。

    ⚠ 为什么要三个来源：`obs.riichi[seat]` 是"**已经**宣言过"的公开状态位，
    **宣言那一手的 obs 它还是 false**（服务端先广播 `riichi` 再广播 `discard` 那套时序）；
    而 `style-vector.py` 也是这么取或的 —— 少一个来源就会丢掉"本局最后一次询问就是立直"的那批。
    """
    if chosen.startswith(RIICHI_PREFIX):
        return True
    riichi = obs.get("riichi") or [False] * SEATS_PER_TABLE
    rt = obs.get("riichi_turn") or [0] * SEATS_PER_TABLE
    seat = int(obs.get("seat", -1))
    if 0 <= seat < SEATS_PER_TABLE:
        if seat < len(riichi) and bool(riichi[seat]):
            return True
        if seat < len(rt) and int(rt[seat]) > 0:
            return True
        return False
    # `obs.seat` 缺字段时退化成"四家取或"（宁可多算，不可漏算）
    return bool(any(riichi)) or any(int(x) > 0 for x in rt)


def _riichi_turn(obs: dict) -> int:
    seat = int(obs.get("seat", -1))
    rt = obs.get("riichi_turn") or [0] * SEATS_PER_TABLE
    if 0 <= seat < len(rt):
        return int(rt[seat])
    return int(max(rt)) if rt else 0


@dataclass
class HandState:
    """一个 (game, hand_no) 上的**逐席公开状态**（只装本模块要用的量）。"""

    riichi: list[bool] = field(default_factory=lambda: [False] * SEATS_PER_TABLE)
    meld: list[bool] = field(default_factory=lambda: [False] * SEATS_PER_TABLE)
    riichi_turn: list[int] = field(default_factory=lambda: [0] * SEATS_PER_TABLE)
    #: 逐席本小局的收支（点；来自决策行的 `hand_delta`，四家数组）
    delta: list[int] = field(default_factory=lambda: [0] * SEATS_PER_TABLE)
    agari: bool = False
    winner: int = -1
    loser: int = -1
    rows: int = 0
    #: 逐席这一小局的策略串（从决策行的 `policy` 字段来）—— 只用来判"这个座位是不是学生"，
    #: 从而决定白化的**统计总体**（见 `whitening_stats` 的 `student_policies`）。
    policy: list[str] = field(default_factory=lambda: [""] * SEATS_PER_TABLE)
    #: ★ **逐决策行**的最小记录 `(seat, chosen)`（`decision` 模式要回答"**这一行**做了什么"）。
    #: ⚠ 只存这两样：手级指示量已经在上面那几列里，多存一份 obs 只是白占内存（一手 ~60 行）。
    row_seat: list[int] = field(default_factory=list)
    row_chosen: list[str] = field(default_factory=list)
    #: ★★ 逐行：**这一行**的 `step`（轨迹里的决策行计数器；`None`/缺失 ⇒ `-1`）。
    #: ⚠⚠ **必须有它**：`step` 是**每一场（game）**从头数的计数器（实测 `g0.jsonl`：hand 0 的
    #: 首行 `step=0`、hand 1 的首行 `step=76`、hand 2 是 `139`…），而 `rows_in_scope` 给的是
    #: **每一小局的行内下标**（每个小局都从 0 开始）。付钱那条路（`_decision_value`）手上只有
    #: `row["step"]` ⇒ 存行内下标当键会**两套坐标系混用**：实测（1000 场折叠账）本该付 1578 行，
    #: 实际只有 **150** 行拿到钱（≈9.5%，纯属下标巧合）—— 即上一轮那条 `fold` 链的**剂量只有
    #: 名义值的十分之一**。现在按 `step` 建集合（与 `_decision_value` 同一坐标系）。
    row_step: list[int] = field(default_factory=list)
    #: ★★ 逐行："这一行的 `legal` 里有 `riichi:*` 吗"（`dama` 轴的付奖行判据要的那一半）。
    #: `None` = 这一行的轨迹里**没有** `legal` ⇒ 对 `dama` 是无定义（`legal_has` 当场报错，不猜）。
    #: ⚠ 只存这个**布尔**、不存整份 `legal`：判据只有一条（"能立而不立"），存全量只是白占内存。
    row_legal_riichi: list[bool | None] = field(default_factory=list)
    #: ★ 逐行的**公开状态位**（`obs.riichi[seat]` / `obs.riichi_turn[seat]`）：`dama` 的结构检查 ①
    #: 要问"这一行这一席**是不是已经立直了**"—— 手级的 `riichi[seat]`（取或）回答不了这个问题
    #: （宣言之后的每一行它都是 true，而那正是"能立而不立"最需要区分的地方）。
    row_riichi_seen: list[bool] = field(default_factory=list)
    row_riichi_turn: list[int] = field(default_factory=list)
    #: ★★ 逐行的**宣言巡目**（`obs.player_draws`，`None` = 这一行取不到）：`riichi_turn` 轴的付奖行判据
    #: 要"这一行自己的巡目"。与 `row_riichi_turn`（`obs.riichi_turn[seat]`，宣言那一手还是 0）**不是**
    #: 同一个量 —— 后者是"已经宣言过"之后的公开位，拿它当宣言那一行的巡目必然读成 0。
    row_declared_turn: list[int | None] = field(default_factory=list)
    #: ★★ 逐席：本小局**有没有一次"巡目 ≤ T 的立直宣言"**（`AXES['riichi_turn']` 的**手级**指示量）。
    #: ⚠ 它与行级判据必须是**同一把尺子**（同一个 `ROW_TURN_LIMIT`、同一个 `player_draws`），否则
    #: `decision_violations` 的蕴含（手级=1 ⇔ 有动作行）会漂移。
    early_decl: list[bool] = field(default_factory=lambda: [False] * SEATS_PER_TABLE)
    #: ★★ 逐行：**这一行**是不是 `fold` 轴的付奖行（= 出牌 ∧ 被威胁（**别家**立直）∧
    #: 打出的牌对**至少一家**威胁家是現物；`fold_row_ok(obs, chosen, seat)`，seat 必填）。
    #: ⚠ 与 `row_legal_riichi` / `row_declared_turn` 同理：**存判据的结果**、不在别处重算一遍
    #: （预扫的 `HandState` 与写列时的 `row` 各算一套必然漂移）。
    row_fold: list[bool] = field(default_factory=list)
    #: ★★ 逐行：**这一行**是"被威胁的出牌行"吗（= 出牌 ∧ 当行 `obs.riichi` 里有真）。
    #: 它是降り率的**分母**来源（`fold_rate = fold_rows / threat_rows`，行级、在判据侧另算一遍）。
    row_threat: list[bool] = field(default_factory=list)
    #: ★★ 逐行：**这一行**取不到"威胁状态 / 現物"吗（`obs.riichi` 缺字段 / 威胁家在 `events` 里
    #: 没有 `riichi` 事件）⇒ 由 `whitening_stats` **硬拒**（见 `FOLD_GENBUTSU_ERR`），
    #: ⛔ 绝不按"没被威胁"或"不安全"静默处理（那会把付奖行率压低）。
    row_threat_missing: list[bool] = field(default_factory=list)
    #: ★★ 逐行的**公开切片**（`obs.riichi` + `obs.events`）—— `fold` 的現物重建只要这两样，
    #: 但**写列那条路**（`BonusTracker._decision_rule` 里再算一遍付奖行号集合）**也要**它们。
    #: ⚠ 存的是**引用**（同一个 dict/list，不深拷贝）⇒ 内存不翻倍；随小局结束整份丢掉。
    #: ⛔ 不存整份 `obs`（`hand`/`visible` 那些对本轴没用，白占内存）。
    row_obs_public: list[dict] = field(default_factory=list)
    #: ★★ 逐席：本小局**有没有至少一行**被判成 `fold` 付奖行（`AXES['fold']` 的**手级**指示量）。
    #: ⚠ 它与行级判据**同一把尺子**（同一个 `fold_row_ok`）⇒ `decision_violations` 的蕴含成立。
    #: ⛔ 它**不是**"降り率"（那是个比值、在 `tools/w-ladder.py` 的判据侧）—— 手级量在这里只服务
    #: "手级=1 ⇔ 有动作行"这条结构不变量。
    fold_hit: list[bool] = field(default_factory=lambda: [False] * SEATS_PER_TABLE)
    #: ★★ 逐行：这一行的**副露种类前缀**（`chi:` / `pon:` / `kan:`；不是鸣牌行 = `""`）。
    #: `feed` 时按**同一个** `MELD_PREFIXES` 判出来存下来 —— `meld_type_row_numbers` 与
    #: 写列那条路都读它（⛔ 不在别处按 `chosen` 重算一遍：两条来源必然漂移）。
    row_meld_type: list[str] = field(default_factory=list)
    #: ★★ 逐席：本小局**有没有至少一行**是 `pon` 轴的付奖行（= 真的碰了；`AXES['pon']` 的**手级**
    #: 指示量）。⚠ 它与行级判据**同一把尺子**（同一个 `MELD_TYPE_PREFIX`）⇒ `decision_violations`
    #: 的"手级=1 ⇔ 有动作行"这条蕴含成立。⛔ 它**不是** `pon_type_rate`（那是判据侧的比值）。
    pon_hit: list[bool] = field(default_factory=lambda: [False] * SEATS_PER_TABLE)

    def seat_is_student(self, seat: int, student_policies: set[str]) -> bool:
        """该席本小局是不是"学生"（= 白化统计要不要算它）。

        ⚠ 判据是**本小局自己的策略串**（不是全局 seat 集合）：风格桌上学生那一席
        由 `--rotate-perm` 在四家轮转 ⇒ 拿一个固定 seat 集合去筛会整份筛错（还不报错）。
        """
        return 0 <= seat < SEATS_PER_TABLE and self.policy[seat] in student_policies

    def feed(self, row: dict) -> None:
        """吃一条 `decision` 行（**唯一**累积点：所有指示量都从这里来）。"""
        obs = row.get("obs")
        if not isinstance(obs, dict):
            raise BonusSpecError(f"决策行没有 obs（game={row.get('game')} hand={row.get('hand_no')}）"
                                 f"—— 没有 obs 就算不出风格量，不猜")
        seat = int(row.get("seat", obs.get("seat", -1)))
        chosen = str(row.get("chosen") or "")
        # ★ 逐决策行的记录（`decision` 模式唯一的"哪一行做了动作"来源）。
        #   ⚠ 与 `st.riichi[seat]` 这类**手级**状态分开：后者是"本小局宣言过没有"，
        #   前者是"**这一行**是不是宣言那一行"（服务端先广播 `riichi` 再广播 `discard`
        #   ⇒ 宣言之后的每一行 `obs.riichi[seat]` 都是 true，不能拿它当行级判据）。
        self.row_seat.append(seat)
        self.row_chosen.append(chosen)
        # ★ 逐行的 `step`（**付钱路的坐标系**：`_decision_value` 只有 `row["step"]`；见 `row_step`）。
        _stp = row.get("step")
        self.row_step.append(int(_stp) if isinstance(_stp, int) and not isinstance(_stp, bool) else -1)
        # ★★ 逐行的**副露种类**（`pon` 轴的付奖行判据与判据侧记账的**唯一**来源）：
        #   ⛔ 只按动作键前缀判（`chi:`/`pon:`/`kan:`），不读 `obs.melds` 的形状 —— 后者是
        #   **手级公开状态**（"这一席有没有副露"），答不了"**这一手**鸣的是哪一种"。
        self.row_meld_type.append(next((p for p in MELD_PREFIXES if chosen.startswith(p)), ""))
        # ★ 逐行的"这一行能立直吗"（`dama` 轴的付奖行判据）。行上的 `legal` 与 `obs.legal`
        #   必须一致（`dataset.py` 会硬校验这一条）⇒ 优先取行上的，行上没有再看 obs，都没有 = None。
        legal = row.get("legal")
        if legal is None:
            legal = obs.get("legal")
        self.row_legal_riichi.append(None if legal is None else legal_has(legal, RIICHI_PREFIX))
        # 逐行的公开状态位（`dama` 的结构检查 ①）：`obs.riichi[seat]` 是"**已经**宣言过"，
        # 宣言那一手它还是 false（服务端先广播 riichi 再广播 discard）—— 这正是我们要的那一位。
        _ri = obs.get("riichi") or []
        _rt = obs.get("riichi_turn") or []
        self.row_riichi_seen.append(bool(_ri[seat]) if 0 <= seat < len(_ri) else False)
        self.row_riichi_turn.append(int(_rt[seat]) if 0 <= seat < len(_rt) else 0)
        # ★★ 逐行的**宣言巡目**（`riichi_turn` 轴的付奖行判据）与手级镜像（`early_decl`）：
        #   ⚠ 两者必须由**同一个** `ROW_TURN_LIMIT['riichi_turn']` 与**同一个** `declared_turn(obs)` 决定
        #   （任何一处另写一份判据，手级与行级就会漂移，而症状是 `decision_violations` 报"两套口径漂移"）。
        _dt = declared_turn(obs)
        self.row_declared_turn.append(_dt)
        if 0 <= seat < SEATS_PER_TABLE and chosen.startswith(RIICHI_PREFIX) \
                and _dt is not None and int(_dt) <= int(ROW_TURN_LIMIT["riichi_turn"]):
            self.early_decl[seat] = True
        # ★★ **被威胁时的降り**（`fold` 轴）的逐行判据 —— 与 `row_is_axis_action` 的 `ROW_ACTION_FOLD`
        #   分支**同一个函数**（唯一落点）：出牌 ∧ 被威胁（**别家**立直）∧ 对**至少一家**威胁家現物。
        #   ⚠ 这里只**记**（预扫那条路）：`row_threat_missing` 非 0 ⇒ 由 `whitening_stats` 当场硬拒
        #   （"取不到威胁状态 / 現物"⇒ 按 False 跳过会把付奖行率静默压低，见 `FOLD_GENBUTSU_ERR`）。
        #   ⚠ 四个数（被威胁 / 降り / 押し / 取不到）各自独立数出来 ⇒ "降り行数 == 审计账 action_rows"
        #   才是**对账**而不是同义反复。
        #   ⚠⚠ `seat` **显式传入**（= 这一行的行动者；`fold_row_ok` / `threat_missing` 都是必填参数）
        #   ⇒ 坐位口径写死成"只数别家"，⛔ 不再有"obs 里没 seat 就四席取真"的字面口径岔路。
        _is_discard = str(chosen or "").startswith(ROW_ACTION_FOLD["fold"])
        _avail, _threats = threat_seats(obs, seat)
        # ⚠ `threat_rows` = **被威胁的出牌行**（至少一家**别家** `obs.riichi` 为真 ∧ 出牌）。
        #   ⛔ "取不到威胁状态 / 現物"的那几行不进这一格（它们本连"有没有被威胁"都无定义），
        #   而是进 `row_threat_missing` → 由 `whitening_stats` **当场硬拒**（见 `FOLD_GENBUTSU_ERR`）。
        _threat_row = bool(_is_discard and _avail and _threats)
        _missing = bool(_is_discard and threat_missing(obs, chosen, seat))
        _fold = fold_row_ok(obs, chosen, seat)
        # ★ 逐行记"公开切片"（只 `riichi` / `events` 两样）—— `row_is_axis_action('fold')` 与
        #   `BonusTracker._decision_rule` 都靠它，⛔ 不在别处重读 `row`（两条来源必然漂移）。
        self.row_obs_public.append({"riichi": obs.get("riichi"),
                                    "events": obs.get("events")})
        self.row_fold.append(_fold)
        self.row_threat.append(_threat_row)
        self.row_threat_missing.append(_missing)
        if _fold and 0 <= seat < SEATS_PER_TABLE:
            self.fold_hit[seat] = True
        if 0 <= seat < SEATS_PER_TABLE:
            self.policy[seat] = str(row.get("policy") or "")
            if _seat_riichi(obs, chosen):
                self.riichi[seat] = True
            self.riichi_turn[seat] = max(self.riichi_turn[seat], _riichi_turn(obs))
            if chosen.startswith(MELD_PREFIXES):
                self.meld[seat] = True
            # ★★ **碰取向**（`pon`）的**手级**指示量：这一行真的碰了（与行级判据同一个前缀表）。
            if self.row_meld_type[-1] == MELD_TYPE_PREFIX["pon"]:
                self.pon_hit[seat] = True
        # 副露是**公开状态**：随便哪一席的 obs 都能读出别家（`--no-claims` 只丢鸣牌决策行）
        melds = obs.get("melds") or []
        for s in range(min(SEATS_PER_TABLE, len(melds))):
            if melds[s]:
                self.meld[s] = True
        # 小局级结局：四家数组，逐行重复出现 ⇒ 直接覆盖（同一小局内恒定）
        hd = row.get("hand_delta")
        if isinstance(hd, list) and len(hd) == SEATS_PER_TABLE:
            self.delta = [int(x) for x in hd]
        self.agari = bool(row.get("hand_agari"))
        w = row.get("hand_winner")
        self.winner = int(w) if isinstance(w, int) else -1
        lo = row.get("hand_loser")
        self.loser = int(lo) if isinstance(lo, int) else -1
        self.rows += 1


def indicator_values(st: HandState) -> list[dict[str, float]]:
    """一个小局 → **四席各一份**指示量字典（值 = 该席在该轴上的原始量）。

    ⚠ 分母口径 = "该席在这一小局里**真的有决策行**"由调用方保证（调用方只把出现在轨迹里的
    小局喂进来；`style-vector.py` 那一处刻意差异见模块 docstring）。
    """
    out: list[dict[str, float]] = []
    for s in range(SEATS_PER_TABLE):
        win = bool(st.agari) and st.winner == s
        deal = st.loser == s
        vals = {
            "riichi": 1.0 if st.riichi[s] else 0.0,
            "meld": 1.0 if st.meld[s] else 0.0,
            "win": 1.0 if win else 0.0,
            "deal": 1.0 if deal else 0.0,
            "no_deal": 0.0 if deal else 1.0,
            "win_points": float(st.delta[s]) if win else 0.0,
            # ★ `dama` 的**手级**指示量 = 该席本小局**未立直**（与 `style-vector.py` 的 `dama_wins`
            #   分子谓词同源）。⚠ 它**不是**付奖行判据（那是"能立而不立"）—— 见模块 docstring
            #   与 `ROW_ACTION_DECLINE`：判据侧量结果、奖励侧量决策，两边由对账钉住。
            "dama": 0.0 if st.riichi[s] else 1.0,
            # ★ `riichi_turn` 的**手级**指示量 = 本小局有过一次"巡目 ≤ T 的立直宣言"（见 `early_decl`）。
            #   ⚠ 它与行级判据**同源同尺**（`ROW_TURN_LIMIT` + `declared_turn`）⇒ `decision_violations`
            #   的"手级=1 ⇔ 有动作行"这条蕴含成立（行级那边没有 `riichi` 轴那套"手级=1 却找不到行"的坑）。
            "riichi_turn": 1.0 if st.early_decl[s] else 0.0,
            # ★ `fold` 的**手级**指示量 = 该席本小局**至少有一行**是"被威胁时切了安全牌"
            #   （与行级判据**同一把尺子** ⇒ `decision_violations` 的蕴含成立）。
            #   ⛔ 它**不是**"降り率"（比值）—— 那是判据侧 `tools/w-ladder.py` 的 `fold_rate`。
            "fold": 1.0 if st.fold_hit[s] else 0.0,
            # ★★ **碰取向**（`pon`，副露种类轴）的**手级**指示量 = 该席本小局真的碰过
            #   （与行级判据**同一把尺子**：同一个 `MELD_TYPE_PREFIX`）⇒ `decision_violations`
            #   的"手级=1 ⇔ 有动作行"这条蕴含成立。⛔ 它**不是** `pon_type_rate`（那在判据侧）。
            "pon": 1.0 if st.pon_hit[s] else 0.0,
        }
        out.append(vals)
    return out


def pair_ok(pair: str, vals: dict[str, float]) -> bool:
    """**成对质量轴**是否为真（`none` 恒假 —— 只给红证用）。"""
    if pair == PAIR_NEVER:
        return False
    if pair == "no_deal":
        return vals["no_deal"] > 0.5
    if pair == "win":
        return vals["win"] > 0.5
    if pair == "deal":
        return vals["deal"] > 0.5
    raise BonusSpecError(f"成对质量轴 {pair!r} 没有实现（`PAIR_KEYS` 与 `pair_ok` 漂移了）")


def paid_indicator(ax: BonusAxis, vals: dict[str, float]) -> float:
    """**付奖用的指示量**：`x` 当且仅当伴随质量量为真，否则 0。

    ⚠ 这是"成对质量轴"的**唯一落点**：白化的对象是 `x`（未加质量门），付奖的对象是
    `1[PAIR]·x`。两者必须分开 —— 混在一起（把质量门折进白化）会让 μ/σ 变成
    "只在那批小局上"的统计，白化就不是对边际分布做的了。
    """
    return vals[ax.name] if pair_ok(ax.pair, vals) else 0.0


def iter_hands(path: Path):
    """流式产出 `(game, hand_no, HandState)` —— **一行一行读，不把整个文件读进内存**（2.7 MB/场）。"""
    key = None
    st: HandState | None = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "decision":
                continue
            k = (int(row.get("game", -1)), int(row.get("hand_no", -1)))
            if k != key:
                if st is not None and st.rows:
                    yield (*key, st)                        # type: ignore[misc]
                key, st = k, HandState()
            st.feed(row)                                     # type: ignore[union-attr]
    if st is not None and st.rows and key is not None:
        yield (*key, st)


# ---------------------------------------------------------------------------
# ③ 白化（μ/σ 从**本数据集**实测）+ 付奖
# ---------------------------------------------------------------------------

@dataclass
class Whitening:
    """一个轴的白化参数（μ/σ 都来自**本数据集**实测）。"""

    name: str
    kind: str
    mu: float
    sd: float
    n: int
    n_all: int

    def to_json(self) -> dict:
        return {"axis": self.name, "kind": self.kind, "mu": self.mu, "sd": self.sd,
                "n_used": self.n, "n_seen": self.n_all}


@dataclass
class Audit:
    """付奖的**可审计**账（每轴：w / μ / σ / 付奖小局数 / 付奖总额）。"""

    axes: list[dict] = field(default_factory=list)
    hands_total: int = 0
    hands_eligible: int = 0
    whiten_scope: str = ""
    spec: str = ""
    #: 付奖模式（`hand` / `decision`）—— 账里**必须**有它：同样一份 `μ/σ/付奖` 读数在两种模式下
    #: 的含义不同（`decision` 的 μ/σ 是**逐行**的），不写下来就没法复判。
    mode: str = MODE_HAND
    #: `decision` 模式：进入白化统计的**行**数（`hand` 模式下 = 0，不写进账）。
    rows_used: int = 0
    #: 逐轴：付奖的 (小局, 座位) 数（**带上限**，免得一个 1000 场的采集把 JSON 撑爆）
    paid_max_rows: int = 100000

    def to_json(self) -> dict:
        return {"spec": self.spec, "mode": self.mode, "whiten_scope": self.whiten_scope,
                "hands_total": self.hands_total, "hands_eligible": self.hands_eligible,
                "rows_used": self.rows_used,
                "axes": self.axes}


def indicator_cells(st: HandState, student_policies: set[str] | None):
    """一个小局 → **参与统计的 (席, 指示量字典)** 列表。

    ★ **唯一**的"总体"定义（`whitening_stats` / `audit_whitening` / `paid_accounting` 都调它）：
    `student_policies is None` ⇒ 四席全要；否则只留 `policy` 命中学生串的那些席。
    为什么要这么严：白化的 μ/σ 与"自证均值 0/方差 1"必须**在同一份样本上**，
    两处各写一份筛选条件必然漂移（而漂移的症状是"自证过了、白化其实是错的"这种静默失真）。
    """
    vals_all = indicator_values(st)
    if student_policies is None:
        return [(s, vals_all[s]) for s in range(SEATS_PER_TABLE)]
    return [(s, vals_all[s]) for s in range(SEATS_PER_TABLE)
            if st.seat_is_student(s, student_policies)]


# ---------------------------------------------------------------------------
# ②b `decision` 模式：逐行动作 / 逐行白化总体 / 逐行付奖
# ---------------------------------------------------------------------------

def rows_in_scope(st: HandState, student_policies: set[str] | None):
    """这一小局里**在统计范围内**的决策行：`[(行下标, seat, chosen), ...]`。

    ⚠ 与 `indicator_cells` 共用**同一套**范围判据（`seat_is_student`）—— 行级白化的 μ/σ 与
    "这一行该不该付奖"必须是同一份总体，两处各写一份筛选必然漂移。
    ★ 行下标是**必须**的：`dama` 轴的行级判据要用这一行自己的 `legal`（`st.row_legal_riichi[idx]`），
    只给 `(seat, chosen)` 就取不到"这一行能不能立直"（那就只能猜 —— 本仓最忌讳的降级）。
    """
    out = []
    for i, (s, c) in enumerate(zip(st.row_seat, st.row_chosen)):
        if not (0 <= s < SEATS_PER_TABLE):
            continue
        if student_policies is None or st.seat_is_student(s, student_policies):
            out.append((i, s, c))
    return out


def require_row_axes(spec: BonusSpec) -> None:
    """`decision` 模式的前置闸门：每个**付奖轴**都必须有"哪一行做了它"的定义。

    ⛔ 这里**必须报错**（不是打印告警、也不是退回 `hand`）：一个没有行级语义的付奖轴在
    `decision` 模式下**付不出任何奖**，静默跑完等于"这次实验什么都没做"。
    """
    bad = [a.name for a in spec.axes if a.name not in ROW_ACTION_AXES]
    if bad:
        raise BonusSpecError(
            f"`--style-bonus-mode decision` 不支持这些付奖轴：{', '.join(bad)}"
            f"（它们是**结局量**，没有'哪一行做了它'）。能当付奖轴的只有："
            f"{', '.join(ROW_ACTION_AXES)}。⛔ 不猜一行、也不静默退回 `hand` 模式")


def axis_row_action_counts(spec: BonusSpec, st: HandState,
                           student_policies: set[str] | None) -> dict[str, int]:
    """逐轴：这一小局在范围内**真的执行了该轴动作**（且 `win` 轴还要"我就是被记录的那家"）的行数。"""
    rows = rows_in_scope(st, student_policies)
    return {ax.name: sum(1 for i, s, c in rows if row_is_axis_action(ax.name, s, c, st, i))
            for ax in spec.axes}


def decline_row_numbers(spec: BonusSpec, st: HandState, student_policies: set[str] | None) -> dict:
    """★ **"能立而不立"的四个数**（需求 ② 的原始读数，`dama` 这类"能而不为"轴专用）。

    口径（每一行只落在其中一格，四格**互不重叠**）：
      * `legal_rows` = 范围内 `legal` 里有 `riichi:*` 的行数（"立直当时是合法选项"）；
      * `legal_chosen_rows` = 其中**真的选了** `riichi:*` 的行数（= 宣言行）；
      * `declined_rows` = 其中**没选**立直的行数（= `legal_rows − legal_chosen_rows` = 付奖行候选）；
      * `missing_legal_rows` = `legal` 取不到的行数（⛔ 非 0 就对"能而不为"轴报错，见 `legal_has`）。

    ⚠ 这是**审计用**的独立累加点（与 `row_is_axis_action` 同一把尺子，但四个数各自独立数出来
    ⇒ "`declined_rows` == 审计账的 `action_rows`"这件事才是**对账**、而不是同义反复）。
    """
    out = {"legal_rows": 0, "legal_chosen_rows": 0, "declined_rows": 0, "missing_legal_rows": 0}
    decl = [a for a in spec.axes if a.name in ROW_ACTION_DECLINE]
    if not decl:
        return out
    pref = ROW_ACTION_DECLINE[decl[0].name]
    for i, _s, c in rows_in_scope(st, student_policies):
        has = st.row_legal_riichi[i] if 0 <= i < len(st.row_legal_riichi) else None
        if has is None:
            out["missing_legal_rows"] += 1
            continue
        if not has:
            continue
        out["legal_rows"] += 1
        if str(c or "").startswith(pref):
            out["legal_chosen_rows"] += 1
        else:
            out["declined_rows"] += 1
    return out


def riichi_turn_row_numbers(spec: BonusSpec, st: HandState, student_policies: set[str] | None) -> dict:
    """★ **早立直轴的四个数**（`riichi_turn` 这类"带巡目门槛的动作"轴专用；与 `decline_row_numbers` 对称）。

    口径（每一行只落在其中一格，四格**互不重叠**）：
      * `decl_rows` = 范围内 **宣言立直** 的行数（`chosen` 以 `riichi:` 开头）；
      * `decl_turn_rows` = 其中巡目 ≤ T 的（= **付奖行候选** = 奖励侧的"动作行"）；
      * `decl_late_rows` = 其中巡目 > T 的（⛔ **不付奖**的那一侧 —— 它必须真的存在，否则 T 没起作用）；
      * `decl_missing_turn_rows` = 宣言行里巡目取不到的（⛔ 非 0 就是"取不到宣言行巡目"，
        由 `row_is_action` 当场报错；这里独立数一遍是为了让账能自证，而不是替它兜底）。

    ⚠ `row_is_axis_action` 与这里的 `decl_turn_rows` 是**两处独立计数**（同一个尺子、各自数）
    ⇒ "`decl_turn_rows == 审计账 action_rows`"才是一条**对账**，而不是同义反复。
    """
    out = {"decl_rows": 0, "decl_turn_rows": 0, "decl_late_rows": 0, "decl_missing_turn_rows": 0}
    na = [a for a in spec.axes if a.name in ROW_ACTION_TURN]
    if not na:
        return out
    if len(na) > 1:
        raise BonusSpecError(
            f"一次开了 {len(na)} 条巡目轴（{', '.join(a.name for a in na)}），但逐行巡目的**存法**只有一份"
            f"（`HandState.row_declared_turn`）⇒ 加第二条巡目轴必须同时加它的存法（⛔ 不猜）")
    ax = na[0]
    pref = ROW_ACTION_TURN[ax.name]
    lim = int(ROW_TURN_LIMIT[ax.name])
    for i, _s, c in rows_in_scope(st, student_policies):
        if not str(c or "").startswith(pref):
            continue
        out["decl_rows"] += 1
        t = st.row_declared_turn[i] if 0 <= i < len(st.row_declared_turn) else None
        if t is None:
            out["decl_missing_turn_rows"] += 1
        elif int(t) <= lim:
            out["decl_turn_rows"] += 1
        else:
            out["decl_late_rows"] += 1
    return out


def _row_obs(st: HandState, idx: int) -> dict:
    """第 `idx` 行**当时**的公开切片（`obs.riichi` + `obs.events`）—— `fold` 的現物重建要它。

    ⚠ 为什么由 `HandState` 按行存：`row_is_axis_action('fold', …)` 在**预扫**之后仍会被
    `BonusTracker._decision_rule`（写列那条路）用到，而那条路手上只有**行号**；
    "两条来源各算一套"正是手级/行级漂移的经典成因 ⇒ 只保一份记录、一个 `fold_row_ok`。
    """
    if 0 <= idx < len(st.row_obs_public):
        return st.row_obs_public[idx]
    return {}


def fold_row_numbers(spec: BonusSpec, st: HandState, student_policies: set[str] | None) -> dict:
    """★ **被威胁时降り轴的四个数**（`fold` 专用；与 `decline_row_numbers` / `riichi_turn_row_numbers` 对称）。

    口径（每一行只落在其中一格：`threat_rows == fold_rows + push_rows`；另有一格
    `threat_missing_rows` = **无定义的那些行** —— 它们不进分母，而是**当场硬拒**）：
      * `threat_rows` = 范围内 **出牌行 ∧ 至少一家 `obs.riichi` 为真** 的行数（= 降り率的**分母**）；
      * `fold_rows` = 其中**打出的牌对至少一家威胁家是現物**的（= **付奖行候选** = 奖励侧的
        "动作行"；⚠ 2026-10-11 第三轮由 `all` 放宽为 `any`）；
      * `push_rows` = 其中**不安全**的（= **押し**那一侧；⛔ 一分钱都不付 —— 方向单向）；
      * `threat_missing_rows` = 其中 `obs.riichi` / `obs.events` 取不到、**現物重建不出来**的
        （⛔ 非 0 就对 `fold` 轴**当场硬拒**，见 `FOLD_GENBUTSU_ERR`；不静默当"没被威胁"或"不安全"）。

    ⚠ 它读的是 `HandState` 的**逐行**记录（`row_threat` / `row_fold` / `row_threat_missing`，
    都在 `feed` 里按**同一个** `fold_row_ok` / `threat_missing` 存下来的）—— 与
    `row_is_axis_action('fold', …)` 是**两处独立计数**（同一个尺子、各自数）
    ⇒ "`fold_rows == 审计账 action_rows`"才是一条**对账**，而不是同义反复。
    ⚠ 只看**范围内**的行（与 `axis_row_action_counts` 同一份总体；`HandState` 里那份是**全席**的）。
    """
    out = {"threat_rows": 0, "fold_rows": 0, "push_rows": 0, "threat_missing_rows": 0}
    if not any(a.name in ROW_ACTION_FOLD for a in spec.axes):
        return out
    for i, _s, _c in rows_in_scope(st, student_policies):
        if not (0 <= i < len(st.row_threat)):
            continue
        if st.row_threat_missing[i]:
            # ⛔ "取不到威胁状态 / 現物"的出牌行：**独立计一格**，不进 `threat_rows`
            #   （它本连"有没有被威胁"都无定义）—— 由 `whitening_stats` 当场硬拒。
            out["threat_missing_rows"] += 1
            continue
        if not st.row_threat[i]:
            continue
        out["threat_rows"] += 1
        if st.row_fold[i]:
            out["fold_rows"] += 1
        else:
            out["push_rows"] += 1
    return out


def meld_type_row_numbers(spec: BonusSpec, st: HandState, student_policies: set[str] | None) -> dict:
    """★ **副露种类轴的四个数**（`pon` 专用；与 `decline_row_numbers` / `fold_row_numbers` 对称）。

    口径（每一行只落在其中一格；四格**互不重叠**、加总 = `meld_rows`）：
      * `meld_rows` = 范围内 **鸣牌动作行**（`chosen` 以 `chi:`/`pon:`/`kan:` 开头）= **主判据**
        `pon_type_rate` 的**分母**（= 判据侧 `tools/w-ladder.py` 的 `pt_meld_rows`）；
      * `pon_rows` = 其中**碰**的（= 奖励侧 `action_rows` = **付奖行候选**）；
      * `chi_rows` / `kan_rows` = 其中吃 / 杠的（⛔ **一分钱不付**的那一侧；它们必须真的存在，
        否则"种类"这个维度塌成了 `meld` 轴）。

    ⚠ 它读的是 `HandState.row_meld_type`（`feed` 里按**同一个** `MELD_PREFIXES` 存下来的）——
    与 `row_is_axis_action('pon', …)` 是**两处独立计数**（同一个尺子、各自数）
    ⇒ "`pon_rows == 审计账 action_rows`"才是一条**对账**，而不是同义反复。
    """
    out = {"meld_rows": 0, "pon_rows": 0, "chi_rows": 0, "kan_rows": 0}
    if not any(a.name in MELD_TYPE_PREFIX for a in spec.axes):
        return out
    for i, _s, c in rows_in_scope(st, student_policies):
        if not (0 <= i < len(st.row_meld_type)):
            raise BonusSpecError(
                f"副露种类轴（`pon`）的付奖行判据要**这一行自己的**动作键，但取不到第 {i!r} 行的"
                f"记录（`HandState.row_meld_type`）—— 两条来源漂移了（⛔ 不按非鸣牌行静默处理）")
        mt = st.row_meld_type[i]
        if not mt:
            continue
        out["meld_rows"] += 1
        _k = {"pon:": "pon_rows", "chi:": "chi_rows", "kan:": "kan_rows"}.get(mt)
        if _k is None:
            raise BonusSpecError(f"副露种类轴：认不出的鸣牌前缀 {mt!r}（`MELD_PREFIXES` 漂移了）")
        out[_k] += 1
    return out


def spec_axis_pay(spec: BonusSpec, stats: dict[str, Whitening], ax: BonusAxis) -> float:
    """`decision` 模式里**动作行**拿到的那笔钱（点）：`w·(1−μ)/σ`（**唯一落点**，别处再算一遍必漂移）。"""
    w = stats[ax.name]
    return float(ax.weight * (1.0 - w.mu) / w.sd)


def decision_bonus(spec: BonusSpec, stats: dict[str, Whitening], st: HandState,
                   seat: int, chosen: str, own_policies: set[str] | None = None,
                   row_idx: int | None = None) -> tuple[float, dict[str, float]]:
    """`decision` 模式：**一行**决策 → 奖励（点）+ 逐轴明细。

    口径：`Σ_i w_i · 1[PAIR_i(这一小局)] · 1[这一行做了轴 i 的动作] · (1 − μ_i)/σ_i`，其余行 = **0**。

    * `1[PAIR_i]` 按**小局**判定（与 `hand` 模式逐字相同：`_merge_cells` + `pair_ok`）—— 需求就是
      "伴随质量轴 `no_deal` 仍按该小局是否放铳判定"，⛔ 不是"按那一行判"。
    * `(1 − μ_i)/σ_i` 里 μ/σ 是**逐行**白化参数（`whitening_stats(..., mode="decision")`）——
      见模块 docstring"为什么 `decision` 要把白化换成逐行"。⚠ 非动作行**不付**那个 `−μ/σ` 的负偏移
      （付了就等于"每一行都付奖"，`付奖行数 == 动作行数` 这条判据当场失效）。
    * ★ 行级判据 = `row_is_axis_action`（不是 `row_is_action`）：`win` 轴还要"**我就是被记录的那家**"
      （多家荣和时两家都执行了 `ron`，只有 `winners[0]` 那家算 —— 见模块 docstring 同名小节）。
    * 席位是否在范围内由调用方保证（`rows_in_scope`）；这里**不**再判一次。
    """
    total = 0.0
    detail: dict[str, float] = {}
    for ax in spec.axes:
        cells = [v for _s, v in indicator_cells(st, own_policies)]
        merged = _merge_cells(cells)
        hit = pair_ok(ax.pair, merged) and row_is_axis_action(ax.name, seat, chosen, st, row_idx)
        c = spec_axis_pay(spec, stats, ax) if hit else 0.0
        detail[ax.name] = c
        total += c
    return total, detail


def decision_violations(spec: BonusSpec, st: HandState,
                        student_policies: set[str] | None) -> list[tuple[str, str]]:
    """`decision` 模式的**自救判据**（需求 ④）：两手口径必须逐手对上。

    * 手级指示量 = 1 但**找不到**动作行 ⇒ "哪一行做了它"取不到 ⇒ **必须报错**（⛔ 不许退化成 `hand`）；
    * 手级指示量 = 0 却有动作行 ⇒ 两套口径漂移（同样是错，不能静默）。

    ★ 行级判据用 `row_is_axis_action`（含 `win` 的"**我就是被记录的那家**"合取）。上一轮只判
    "这一行执行了动作" ⇒ 双响里非 `winners[0]` 那家的 `ron` 行被判成动作行，而手级指示量是 0
    ⇒ 实测 30/11258 小局被这条**误判**成"口径漂移"（修法见模块 docstring 同名小节）。

    @return `[(轴, 原因)]`（空列表 = 这一小局没问题）。
    """
    rows = rows_in_scope(st, student_policies)
    if not rows:
        return []
    merged = _merge_cells([v for _s, v in indicator_cells(st, student_policies)])
    out: list[tuple[str, str]] = []
    for ax in spec.axes:
        # ★ `dama` 这类"能而不为"轴**不套**下面两条蕴含（手级=结果量、行级=决策，谁都不蕴含谁）
        #   ⇒ 换成两条**可证伪**的结构检查（见 `_decline_violations`）。
        if ax.name in ROW_ACTION_DECLINE:
            out.extend(_decline_violations(ax.name, st, rows))
            continue
        hit = [(i, c) for i, s, c in rows if row_is_axis_action(ax.name, s, c, st, i)]
        hx = merged[ax.name] > 0.5
        if hx and not hit:
            out.append((ax.name, "手级指示量=1（本小局发生过这个动作），但**范围内一行都没有**执行它"
                                  "—— 取不到'哪一行做了它'"))
        elif hit and not hx:
            out.append((ax.name, f"范围内 {len(hit)} 行执行了这个动作，手级指示量却是 0"
                                  f"—— 两套口径漂移了"))
    return out


def _decline_violations(axis: str, st: HandState, rows) -> list[tuple[str, str]]:
    """`dama` 的两条**可证伪**的结构检查（见 `decision_violations` 的 ★★ 段）：

      ① 付奖行那一刻公开状态位必须还是"没立直"（服务端**不会**给已经在立直的人再下发 `riichi`
         ⇒ 若这条不成立，是轨迹/判据坏了，不是"口径差异"）；
      ② 付奖行必须发生在该席**第一次宣言之前**（宣言之后不可能再有"能立而不立"）。

    ⛔ 别为了"让它像别的轴"而给 `dama` 编一条"手级=1 ⇒ 有动作行"的蕴含：手级量的是
    "未立直"这个**结果**、行级量的是"能立而不立"这个**决策**，两者本来就不互相蕴含 ——
    硬焊在一起等于把"两套口径互相独立"这条对账基础拆掉。
    """
    pref = ROW_ACTION_DECLINE[axis]
    first_decl = None
    for i, _s, c in rows:
        if str(c or "").startswith(pref):
            first_decl = i
            break
    bad: list[tuple[str, str]] = []
    for i, s, c in rows:
        if not row_is_axis_action(axis, s, c, st, i):
            continue
        seen = st.row_riichi_seen[i] if 0 <= i < len(st.row_riichi_seen) else False
        rt = st.row_riichi_turn[i] if 0 <= i < len(st.row_riichi_turn) else 0
        if seen or rt > 0:
            bad.append((axis, f"第 {i} 行（seat={s}）的 `legal` 里有 `{pref}`，但公开状态位说这一席"
                              f"**已经在立直**（obs.riichi={seen} / obs.riichi_turn={rt}）"
                              f" —— 服务端不会给已立直的人再下发立直，轨迹/判据对不上"))
        if first_decl is not None and i >= first_decl:
            bad.append((axis, f"第 {i} 行（seat={s}）被算成'能立而不立'，却在第 {first_decl} 行"
                              f"（同一席**已经宣言**立直）之后 —— 两套口径对不上"))
    return bad


def whitening_stats(spec: BonusSpec, hands, *, student_policies: set[str] | None = None,
                    quiet: bool = False, mode: str = MODE_HAND):
    """扫一遍 `(game, hand_no, HandState)`，算每个轴的 μ/σ + 付奖账。

    @param student_policies `None` = 全部座位（`--style-bonus-whiten all`）；否则只用这些策略串
        坐的座位（`--style-bonus-whiten student` = 训练里真正进梯度的那些行）。
        ⚠ 用"全部座位"会把 teacher/对手的行为分布混进 μ/σ（风格桌上学生只占 1/4），
        白化就不准了 ⇒ 缺省取 student，且**必须**能在日志/meta 里看出来用的是哪一份。
    @param mode 付奖模式（`hand` 缺省 = 老口径逐位不变；`decision` = 逐行付奖 + **逐行**白化）。
        ⚠ `hand` 分支的每一行代码都没动过 ⇒ 与加这个参数之前**逐位相同**。
    @return `(stats: dict[str, Whiten], audit: Audit)`
    """
    mode = check_mode(mode)
    check_mode_axes(spec, mode)             # ⛔ `dama` 这类"能而不为"轴只在 decision 模式成立
    if mode == MODE_DECISION:
        require_row_axes(spec)              # ⛔ 前置闸门：付奖轴必须能定位到行
    acc: dict[str, list[float]] = {a.name: [] for a in spec.axes}
    paid: dict[str, dict] = {a.name: {"hands": 0, "cells": 0, "raw_sum": 0.0}
                             for a in spec.axes}
    #: ★ `decision` 模式的逐轴账（**只在那个模式下才有数字**）：
    #: `action_rows` = 范围内真的执行了该动作的行数；`paid_rows` = 其中通过质量轴的；
    #: `blocked_rows` = 被质量轴拦下的（**独立累加**，用来和 `action_rows − paid_rows` 对账）；
    #: `hands_hit` = 手级指示量为 1 的小局数（= "立直小局数"，需求里要拿它当分母报比率）。
    dav: dict[str, dict] = {a.name: {"action_rows": 0, "paid_rows": 0, "blocked_rows": 0,
                                    "hands_hit": 0} for a in spec.axes}
    #: ★ **"能而不为"轴的四个数**（`dama`）：`legal_rows` / `legal_chosen_rows` / `declined_rows`
    #: / `missing_legal_rows`（口径见 `decline_row_numbers`）。⛔ 只对这类轴累加 ⇒ riichi/meld
    #: 的审计账**一个键都不多**（"其它轴逐位不变"这条判据才是真的）。
    dnum: dict[str, dict] = {a.name: {"legal_rows": 0, "legal_chosen_rows": 0,
                                     "declined_rows": 0, "missing_legal_rows": 0}
                             for a in spec.axes}
    #: ★★ **巡目阈值轴**（`riichi_turn`）的四个数（口径见 `riichi_turn_row_numbers`）。
    #: ⛔ 只对这类轴累加 ⇒ riichi/meld/dama 的审计账**一个键都不多**。
    tnum: dict[str, dict] = {a.name: {"decl_rows": 0, "decl_turn_rows": 0,
                                      "decl_late_rows": 0, "decl_missing_turn_rows": 0}
                             for a in spec.axes}
    #: ★★ **被威胁时降り轴**（`fold`）的四个数（口径见 `fold_row_numbers`）：
    #: `threat_rows`（分母）/ `fold_rows`（= 动作行）/ `push_rows`（⛔ 不付奖那一侧）/ `threat_missing_rows`。
    #: ⛔ 只对这类轴累加 ⇒ riichi/meld/dama/riichi_turn 的审计账**一个键都不多**。
    fnum: dict[str, dict] = {a.name: {"threat_rows": 0, "fold_rows": 0,
                                      "push_rows": 0, "threat_missing_rows": 0}
                             for a in spec.axes}
    #: ★★ **副露种类轴**（`pon`）的四个数（口径见 `meld_type_row_numbers`）：
    #: `meld_rows`（分母）/ `pon_rows`（= 动作行）/ `chi_rows` / `kan_rows`（⛔ 不付奖那一侧）。
    #: ⛔ 只对这类轴累加 ⇒ riichi/meld/dama/riichi_turn/fold 的审计账**一个键都不多**。
    mtnum: dict[str, dict] = {a.name: {"meld_rows": 0, "pon_rows": 0,
                                      "chi_rows": 0, "kan_rows": 0}
                              for a in spec.axes}
    #: ★★ `pon` 的**硬拒计数**（见 `MELD_TYPE_ERR`）：统计范围里的鸣牌行数（= 0 ⇒ 当场报错）。
    #: ⛔ 非 0 才对；只在真的用了这根轴时检查（别的轴 / 缺省路径一字不动）。
    meld_rows_total = 0
    #: ★★ `fold` 的**硬拒计数**（见 `FOLD_GENBUTSU_ERR`）：出牌行里"取不到威胁状态 / 現物"的行数。
    #: ⛔ 非 0 ⇒ 当场报错（**只在真的用了 `fold` 轴时**检查；别的轴 / 缺省路径一字不动）。
    fold_missing_total = 0
    #: `decision` 模式下顺带记一份**手级** μ/σ（**只作参照**，⛔ 不参与付奖）——
    #: 它让"剂量有没有被换掉"这件事在日志里当场可比（两条 std 都打出来）。
    hand_acc: dict[str, list[float]] = {a.name: [] for a in spec.axes}
    #: 违规的**前若干条样例**（`VIOL_SAMPLES` 封顶，只给日志看）。
    viol: list[tuple] = []
    #: ★ 违规的**真实小局数**（每一个违规小局都 +1，**与 `len(viol)` 无关**）。
    #: ⚠ 上一轮就是拿 `len(viol)` 当"共 N 个小局"打印 ⇒ 日志恒说 8（真实 30）。计数与样例必须分开。
    n_viol = 0

    n_hands = 0
    n_used = 0
    n_rows = 0
    for _g, _h, st in hands:
        n_hands += 1
        cells = [vals for _s, vals in indicator_cells(st, student_policies)]
        if not cells:
            continue
        # ⚠ 统计单位是**小局**（与付奖单位一致，见 `hand_bonus` 的 ★★ 注释）：
        #   四席取或成一份之后，白化参数与"付奖的分布"才是同一个东西。
        vals = _merge_cells(cells)
        n_used += 1
        rows = None
        acts: dict[str, int] = {}
        if mode == MODE_DECISION:
            # ★ 逐行白化的总体 = 范围内（学生席）的**每一行**；分子 = "这一行做了这个动作"。
            rows = rows_in_scope(st, student_policies)
            n_rows += len(rows)
            acts = axis_row_action_counts(spec, st, student_policies)
            # ★ "能而不为"轴的四个数（`dama`）：与 `acts` 同一份总体、但**独立数**出来
            #   ⇒ `declined_rows == action_rows` 才有对账价值（见 `decline_row_numbers`）。
            dn = decline_row_numbers(spec, st, student_policies)
            for ax in spec.axes:
                if ax.name in ROW_ACTION_DECLINE:
                    for k, v in dn.items():
                        dnum[ax.name][k] += v
            # ★ **巡目阈值轴**的四个数（与 `acts` 同一份总体、但**独立数**出来 ⇒ 对账才有意义）。
            tn = riichi_turn_row_numbers(spec, st, student_policies)
            for ax in spec.axes:
                if ax.name in ROW_ACTION_TURN:
                    for k, v in tn.items():
                        tnum[ax.name][k] += v
            # ★★ **被威胁时降り轴**的四个数（与 `acts` 同一份总体、但**独立数**出来 ⇒ 对账才有意义）。
            fn = fold_row_numbers(spec, st, student_policies)
            for ax in spec.axes:
                if ax.name in ROW_ACTION_FOLD:
                    for k, v in fn.items():
                        fnum[ax.name][k] += v
                    fold_missing_total += fn["threat_missing_rows"]
            # ★★ **副露种类轴**的四个数（与 `acts` 同一份总体、但**独立数**出来 ⇒ 对账才有意义）。
            mn = meld_type_row_numbers(spec, st, student_policies)
            for ax in spec.axes:
                if ax.name in MELD_TYPE_PREFIX:
                    for k, v in mn.items():
                        mtnum[ax.name][k] += v
                    meld_rows_total += mn["meld_rows"]
            bad = decision_violations(spec, st, student_policies)
            if bad:
                # ★ 计数**无条件** +1；样例才受上限约束（`len(viol)` 不是总数 —— 见 `n_viol` 的注释）。
                n_viol += 1
                if len(viol) < VIOL_SAMPLES:
                    viol.append((_g, _h, bad))
            for ax in spec.axes:
                hand_acc[ax.name].append(vals[ax.name])
                dav[ax.name]["action_rows"] += acts[ax.name]
                if vals[ax.name] > 0.5:
                    dav[ax.name]["hands_hit"] += 1
        for ax in spec.axes:
            if mode == MODE_DECISION:
                acc[ax.name].extend([1.0] * acts[ax.name]
                                    + [0.0] * (len(rows) - acts[ax.name]))
            else:
                acc[ax.name].append(vals[ax.name])
            if pair_ok(ax.pair, vals):
                paid[ax.name]["cells"] += 1
                paid[ax.name]["raw_sum"] += vals[ax.name]
                if vals[ax.name] > 0:
                    paid[ax.name]["hands"] += 1
                if mode == MODE_DECISION:
                    dav[ax.name]["paid_rows"] += acts[ax.name]
            elif mode == MODE_DECISION:
                dav[ax.name]["blocked_rows"] += acts[ax.name]
    if n_viol:
        # ⛔ 需求 ④：取不到"哪一行做了它" ⇒ **当场报错**，绝不退化成 `hand` 模式。
        #   ⚠ 报的是 `n_viol`（真实总数），不是 `len(viol)`（样例条数，封顶 `VIOL_SAMPLES`）。
        head = "；".join(f"game={g} hand={h}: " + "，".join(f"[{a}] {r}" for a, r in bad)
                         for g, h, bad in viol[:4])
        raise BonusSpecError(
            f"`decision` 模式取不到'哪一行做了这个动作'（共 **{n_viol}** 个小局；"
            f"下面只列前 {len(viol)} 条样例）：{head}。"
            f"⛔ **不退化**成 `hand` 模式：行级信度分配是这个模式的全部意义。"
            f"要么这份轨迹里没有记下那个动作键，要么范围（`--style-bonus-whiten` / `--student`）"
            f"与轨迹对不上")

    # ★★ **`pon` 的硬拒**（见 `MELD_TYPE_ERR`）：统计范围里一行鸣牌决策行都没有 ⇒
    #   这一轴的**分母取不到**（付奖行率 / `pon_type_rate` 都无定义）⇒ 当场报错。
    #   ⚠⚠ 必须放在**白化的 σ 检查之前**：没有鸣牌行 ⇒ μ = 0 ⇒ σ = 0，那条通用闸门会先炸，
    #   报出来的是"这一轴是常数"（症状相同但**诊断错位** —— 真正的原因是**分母取不到**）。
    #   ⛔ 不静默付 0（那等于把这条轴悄悄关掉）；只在**真的用了**这根轴时检查。
    if any(a.name in MELD_TYPE_PREFIX for a in spec.axes) and not meld_rows_total:
        raise BonusSpecError(f"{MELD_TYPE_ERR}（本次实测：范围内 {n_rows} 行决策、"
                             f"鸣牌行为 0）")

    stats: dict[str, Whitening] = {}
    for ax in spec.axes:
        x = acc[ax.name]
        n = len(x)
        if n == 0:
            raise BonusSpecError(f"轴 {ax.name!r} 在统计范围里一个 (小局×座位) 样本都没有（n=0）"
                                 f"—— 白化无从谈起。检查 `--style-bonus-whiten student` 与 "
                                 f"`--student` 的策略串是否对得上")
        mu = float(sum(x) / n)
        if AXES[ax.name]["kind"] == "bernoulli":
            # Bernoulli：σ = √(p(1−p))（与 `x.std(ddof=0)` 逐位一致 —— 自检钉住这条）
            var = mu * (1.0 - mu)
        else:
            var = float(sum((v - mu) ** 2 for v in x) / n)
        sd = math.sqrt(max(var, 0.0))
        if not (sd > 0.0):
            # ⛔ 不静默跳过这个轴：跳过 = 那一轴的塑形**没了**，而训练照跑（正是本仓最忌讳的静默降级）。
            raise BonusSpecError(
                f"轴 {ax.name!r} 的白化 σ = {sd:g}（μ={mu:g}, n={n}）—— 这一轴在本数据集上是常数，"
                f"白化无从谈起（1/σ 会炸）。要么换一个在本桌上真的会变的轴，要么把它从 "
                f"`--style-bonus` 里去掉（**不许**默默跳过）")
        stats[ax.name] = Whitening(name=ax.name, kind=str(AXES[ax.name]["kind"]),
                                   mu=mu, sd=sd, n=n, n_all=n)
    # ★ `decision` 模式的**第二条**对账（需求 ② 的算术形式）：
    #   `付奖行数 + 被质量轴拦下的行数 == 动作行数`。两个加数**各自独立累加**
    #   （`paid_rows` 在 `pair_ok` 为真那一支、`blocked_rows` 在 else 支）⇒ 这条等式真的在查东西。
    if mode == MODE_DECISION:
        # ★★ **`fold` 的硬拒**（见 `FOLD_GENBUTSU_ERR`）：出牌行里"取不到威胁状态 / 現物"⇒ 当场报错。
        #   ⛔ 不按"没被威胁"/"不安全"静默处理：那两种默认都会把**付奖行率**（本轴的第一条判据）
        #   悄悄压低 —— 而"付奖行率够不够 1%/行"正是这条轴到底做不做得成的前提。
        #   ⚠ 只在**真的用了** `fold` 轴时检查 ⇒ 别的轴 / 缺省路径一字不动。
        if any(a.name in ROW_ACTION_FOLD for a in spec.axes) and fold_missing_total:
            raise BonusSpecError(
                f"{FOLD_GENBUTSU_ERR}（本次实测：出牌行里**取不到**的 {fold_missing_total} 行"
                f"，共 {n_rows} 行决策）")
        # ★★ **`pon` 的硬拒**（见 `MELD_TYPE_ERR`）：统计范围里一行鸣牌决策行都没有 ⇒
        #   这一轴的**分母取不到**（付奖行率 / `pon_type_rate` 都无定义）⇒ 当场报错。
        #   ⛔ 不静默付 0（那等于把这条轴悄悄关掉）；只在**真的用了**这根轴时检查。
        #   ⚠ 这道闸门在上面（白化的 σ 检查**之前**）已经查过一次 —— 这里**不重复**。
        for ax in spec.axes:
            d = dav[ax.name]
            if d["paid_rows"] + d["blocked_rows"] != d["action_rows"]:
                raise BonusSpecError(
                    f"轴 {ax.name!r} 的付奖账对不上：付奖行 {d['paid_rows']} + 拦下 "
                    f"{d['blocked_rows']} != 动作行 {d['action_rows']} —— 付奖行/拦下行的累计点"
                    f"有漏（那是本仓最忌讳的静默错位）")
            if d["action_rows"] and not d["paid_rows"]:
                raise BonusSpecError(
                    f"轴 {ax.name!r}：动作行有 {d['action_rows']} 行，付奖行 **0** —— 质量轴 "
                    f"`{ax.pair}` 把所有动作行都拦下了。这多半不是想要的口径（先把质量轴的问题"
                    f"看清再跑，别拿一个'付奖恒 0'的模式去跑实验）")
            # ★ **实测层面的"恒真"闸门**（`ROW_PAIR_MUST_BLOCK`）：被登记的轴在**真数据**上必须真的
            #   有行被伴随量拦下。`blocked_rows == 0` ⇒ 伴随量在付奖行上恒真 ⇒ 质量轴形同虚设
            #   （= 对同一件事重复加权，`win=w:no_deal` 的病）⇒ 当场报错，⛔ 不静默放行。
            if (ax.pair in ROW_PAIR_MUST_BLOCK.get(ax.name, ()) and d["action_rows"]
                    and not d["blocked_rows"]):
                raise BonusSpecError(
                    f"轴 {ax.name!r} 配伴随质量轴 `{ax.pair}` 在**真数据**上退化成恒真：动作行 "
                    f"{d['action_rows']} 行、**被拦下 0 行** ⇒ 质量轴形同虚设 = 对同一件事重复加权"
                    f"（`win=w:no_deal` 的病）。实测判据见 `ROW_PAIR_MUST_BLOCK`；⛔ 当场报错、"
                    f"不静默放行（要负向对照请改质量轴或显式写 `:none` 并说明理由）")
    # 付奖总额 = Σ 1[PAIR]·w·(x − μ)/σ —— 只需 `Σ 1[PAIR]·x`（= raw_sum）与付奖格数
    paid_sums: dict[str, float] = {}
    for ax in spec.axes:
        w = stats[ax.name]
        p = paid[ax.name]
        paid_sums[ax.name] = ax.weight * (p["raw_sum"] - p["cells"] * w.mu) / w.sd
    audit = Audit(
        spec=spec.describe(),
        whiten_scope=("all" if student_policies is None else "student"),
        hands_total=n_hands, hands_eligible=n_used,
        mode=mode, rows_used=n_rows,
        axes=[{**stats[ax.name].to_json(), "weight": ax.weight, "pair": ax.pair,
               "pair_meaning": (PAIR_KEYS.get(ax.pair) or ("（恒假：只给红证用）", ""))[0],
               "paid_cells": paid[ax.name]["cells"],
               "paid_hands": paid[ax.name]["hands"],
               "paid_sum": paid_sums[ax.name],
               "raw_sum": paid[ax.name]["raw_sum"],
               # `decision` 模式才有意义的**五项**（`hand` 模式下这些键**根本不出现** ——
               # ⛔ 不是"出现但为 None"：那样 `hand` 模式的审计账会多一个键，
               #    "其它轴/缺省逐位不变"就只能靠嘴说；现在是**逐字节**相同）。
               "granularity": ("row" if mode == MODE_DECISION else "hand"),
               # ★ `win` 轴的行级判据多一条合取（"我就是被记录的那家"）—— 账里必须留下这件事，
               #   否则同一份 `action_rows` 在"有没有这条合取"下含义不同（多家荣和）。
               **({"row_self_seat": ROW_SELF_SEAT.get(ax.name)}
                  if mode == MODE_DECISION else {}),
               "action_rows": (dav[ax.name]["action_rows"] if mode == MODE_DECISION else None),
               "paid_rows": (dav[ax.name]["paid_rows"] if mode == MODE_DECISION else None),
               "blocked_rows": (dav[ax.name]["blocked_rows"] if mode == MODE_DECISION else None),
               "hands_indicator": (dav[ax.name]["hands_hit"] if mode == MODE_DECISION else None),
               # ★ **"能而不为"轴的四个数**（`dama`）：只在**这类轴**上进账 ⇒ riichi/meld 的
               #   审计账逐字节不变（"其它轴逐位不变"这条判据不是靠嘴说的）。
               **({"legal_rows": dnum[ax.name]["legal_rows"],
                   "legal_chosen_rows": dnum[ax.name]["legal_chosen_rows"],
                   "declined_rows": dnum[ax.name]["declined_rows"],
                   "missing_legal_rows": dnum[ax.name]["missing_legal_rows"],
                   "row_rule": f"legal 含 {ROW_ACTION_DECLINE[ax.name]}* 且 chosen 不是它"}
                  if (mode == MODE_DECISION and ax.name in ROW_ACTION_DECLINE) else {}),
                # ★ **巡目阈值轴**（`riichi_turn`）的四个数 + 阈值：同样**只在这类轴上进账**
                #   ⇒ 既有轴的审计账逐字节不变。判据侧（`tools/w-ladder.py`）拿这几个键对账
                #   "付奖行数 == `declared_turn ≤ T` 的宣言行数"。
                **({"decl_rows": tnum[ax.name]["decl_rows"],
                    "decl_turn_rows": tnum[ax.name]["decl_turn_rows"],
                    "decl_late_rows": tnum[ax.name]["decl_late_rows"],
                    "missing_turn_rows": tnum[ax.name]["decl_missing_turn_rows"],
                    "turn_limit": int(ROW_TURN_LIMIT[ax.name]),
                    "turn_src": "obs.player_draws（宣言那一行的巡目；与 style-vector 的回退同一把尺子）",
                    "row_rule": (f"chosen 以 {ROW_ACTION_TURN[ax.name]}* 开头 且 obs.player_draws ≤ "
                                 f"{int(ROW_TURN_LIMIT[ax.name])}（⛔ 越晚越不付：> T 的一侧付 0）")}
                   if (mode == MODE_DECISION and ax.name in ROW_ACTION_TURN) else {}),
                # ★★ **被威胁时降り轴**（`fold`）的四个数 + 口径：同样**只在这类轴上进账**
                #   ⇒ 既有轴的审计账逐字节不变。判据侧（`tools/w-ladder.py`）拿这几个键对账
                #   "付奖行数 == 被威胁行里切了現物的行数"，以及降り率 = `fold_rows / threat_rows`。
                **({"threat_rows": fnum[ax.name]["threat_rows"],
                    "fold_rows": fnum[ax.name]["fold_rows"],
                    "push_rows": fnum[ax.name]["push_rows"],
                    "missing_genbutsu_rows": fnum[ax.name]["threat_missing_rows"],
                    "threat_src": ("该行 `obs.riichi` 里**别家**（`s != 这一行的行动者`）为真"
                                   "（当行公开快照；⛔ 不是'本小局曾经有人立直'，"
                                   "⛔ 也不含行动者自己 —— seat 口径写死：立直后只能摸切）"),
                    "genbutsu_src": ("`obs.events` 里该家 `type==\"riichi\"` 那条事件**之后**的 "
                                     "`discard` 牌 **＋ 宣言牌本身**（`sideways`；麻将标准口径："
                                     "进过河不与荣）；仅公开信息"),
                    "row_rule": ("`chosen` 以 `discard:` 开头 ∧ 被威胁（**别家**立直）∧ 打出的牌对"
                                 "**至少一家**威胁家是現物（⛔ 不安全那一侧一分钱不付）")}
                   if (mode == MODE_DECISION and ax.name in ROW_ACTION_FOLD) else {}),
                # ★★ **副露种类轴**（`pon`）的四个数 + 口径：同样**只在这类轴上进账**
                #   ⇒ 既有轴的审计账逐字节不变。判据侧（`tools/w-ladder.py`）拿这几个键对账
                #   "付奖行数 == 碰的行数"，并且**分母**（`meld_rows`）与判据侧同版同源。
                **({"meld_rows": mtnum[ax.name]["meld_rows"],
                    "pon_rows": mtnum[ax.name]["pon_rows"],
                    "chi_rows": mtnum[ax.name]["chi_rows"],
                    "kan_rows": mtnum[ax.name]["kan_rows"],
                    "type_prefix": MELD_TYPE_PREFIX[ax.name],
                    "row_rule": (f"`chosen` 以 {MELD_TYPE_PREFIX[ax.name]} 开头（= 这一手是**碰**；"
                                 "⛔ `chi:`/`kan:` 那一侧一分钱不付 —— 方向单向）"),
                    "type_src": ("动作键前缀直接区分（`docs/PROTOCOL.md` §8.3）；分母 `meld_rows` = "
                                 "`chi:`/`pon:`/`kan:` 的**全部**鸣牌行（判据侧独立实现同一把尺子）")}
                   if (mode == MODE_DECISION and ax.name in MELD_TYPE_PREFIX) else {})}
              for ax in spec.axes],
    )
    if not quiet:
        print(f"风格奖励：白化统计（**本数据集实测**，范围 = {audit.whiten_scope}；"
              f"{n_hands} 个小局，其中 {n_used} 个进入统计（= 该小局在统计范围里有决策行））")
        for row in audit.axes:
            print(f"  轴 {row['axis']:<10} w={row['weight']:g}  成对质量轴={row['pair'] or '（无）'}  "
                  f"μ={row['mu']:.6f}  σ={row['sd']:.6f}  付奖小局 {row['paid_hands']}  "
                  f"付奖总额 {row['paid_sum']:+.1f}")
        if mode == MODE_DECISION:
            # ★ 需求 ②③ 的**原始输出落点**：付奖行数 / 动作行数 / 质量轴拦下 / 立直小局数，
            #   以及"逐行白化 vs 手级白化"两条 μ/σ（剂量有没有被换掉，当场可比）。
            print(f"风格奖励：**逐决策（decision）付奖** —— 奖励只落在'真的做了那个动作'那一行；"
                  f"白化总体 = {audit.whiten_scope} 席的**决策行**（{n_rows} 行）")
            for ax in spec.axes:
                d = dav[ax.name]
                hx = hand_acc[ax.name]
                hmu = float(sum(hx) / len(hx)) if hx else float("nan")
                hsd = math.sqrt(max(hmu * (1 - hmu), 0.0)) if hx else float("nan")
                w = stats[ax.name]
                ratio = (d["paid_rows"] / d["hands_hit"]) if d["hands_hit"] else float("nan")
                print(f"  轴 {ax.name}：动作行（`{ax.name}` 的动作键）{d['action_rows']}  "
                      f"付奖行 {d['paid_rows']}  质量轴 `{ax.pair}` 拦下 {d['blocked_rows']}  "
                      f"⇒ 付奖行 +拦下 == 动作行 "
                      f"({'✓' if d['paid_rows'] + d['blocked_rows'] == d['action_rows'] else '✗'})")
                print(f"     白化（逐行，**本模式实际使用**）μ={w.mu:.6f} σ={w.sd:.6f} n={w.n} 行  "
                      f"⇒ 动作行拿到的钱 w·(1−μ)/σ = {ax.weight * (1 - w.mu) / w.sd:+.1f} 点")
                print(f"     ⚠ 手级白化（**仅作参照**，本模式不用）：μ={hmu:.6f} σ={hsd:.6f}"
                      f"；『立直小局数』（手级指示量=1）{d['hands_hit']}"
                      f" ⇒ 付奖行数 / 立直小局数 = {ratio:.3f}")
                if ax.name in ROW_ACTION_DECLINE:
                    # ★ **四个数**（需求 ② 的原始输出）：legal 里有它 / 其中未选 / 真付奖 / 被伴随量拦下。
                    dn = dnum[ax.name]
                    print(f"     ★ 能而不为（`{ax.name}`）：`legal` 里有 "
                          f"`{ROW_ACTION_DECLINE[ax.name]}*` 的行 {dn['legal_rows']}  "
                          f"（其中**真的选了**它 {dn['legal_chosen_rows']}）"
                          f"｜**未选**它 {dn['declined_rows']}（= 付奖行候选）"
                          f"｜**真付奖** {d['paid_rows']}｜被伴随量 `{ax.pair}` 拦下 {d['blocked_rows']}"
                          f"｜`legal` 取不到的行 {dn['missing_legal_rows']}")
                    print(f"     ★ 对账：未选它 {dn['declined_rows']} == 动作行 {d['action_rows']}"
                          f"（{'✓' if dn['declined_rows'] == d['action_rows'] else '✗ **对不上**'}）"
                          f"；白化总体 = 该轴的**决策行**（{w.n} 行）")
                if ax.name in ROW_ACTION_TURN:
                    # ★ **早立直**的四个数（需求 ② 的原始输出）：宣言行 / 其中 ≤T / 其中 >T / 取不到巡目。
                    tn = tnum[ax.name]
                    print(f"     ★ 早立直（`{ax.name}`，T={ROW_TURN_LIMIT[ax.name]}）：宣言立直的行 "
                          f"{tn['decl_rows']}｜其中巡目 ≤ T 的 {tn['decl_turn_rows']}（= 动作行）"
                          f"｜巡目 > T 的 {tn['decl_late_rows']}（⛔ 不付奖的一侧）"
                          f"｜真付奖 {d['paid_rows']}｜被伴随量 `{ax.pair}` 拦下 {d['blocked_rows']}"
                          f"｜巡目取不到的行 {tn['decl_missing_turn_rows']}")
                    print(f"     ★ 对账：巡目 ≤ T 的宣言行 {tn['decl_turn_rows']} == 动作行 "
                          f"{d['action_rows']}（{'✓' if tn['decl_turn_rows'] == d['action_rows'] else '✗ **对不上**'}）"
                          f"；付奖行 + 拦下 == 动作行 "
                          f"({'✓' if d['paid_rows'] + d['blocked_rows'] == d['action_rows'] else '✗'}）")
                if ax.name in ROW_ACTION_FOLD:
                    # ★★ **被威胁时降り**的四个数（需求 ②③ 的原始输出）：被威胁行 / 其中降り /
                    #   其中押し / 取不到（現物重建不出来）。
                    fn = fnum[ax.name]
                    _rate = (d["action_rows"] / fn["threat_rows"]) if fn["threat_rows"] else float("nan")
                    print(f"     ★ 被威胁时降り（`{ax.name}`，弱口径 = 現物，"
                          f"安全口径 = **对至少一家**威胁家安全）：**被威胁的出牌行** "
                          f"{fn['threat_rows']}｜其中**切了对至少一家威胁家安全的現物** "
                          f"{fn['fold_rows']}（= 付奖行候选）｜**押し**（对谁都不安全）{fn['push_rows']}"
                          f"｜現物取不到 {fn['threat_missing_rows']}")
                    print(f"     ★ 对账：降り行 {fn['fold_rows']} == 动作行 {d['action_rows']}"
                          f"（{'✓' if fn['fold_rows'] == d['action_rows'] else '✗ **对不上**'}）"
                          f"；付奖行 {d['paid_rows']} + 拦下 {d['blocked_rows']} == 动作行 "
                          f"（{'✓' if d['paid_rows'] + d['blocked_rows'] == d['action_rows'] else '✗'}）"
                          f"；threat == fold + push + missing "
                          f"({'✓' if fn['threat_rows'] == fn['fold_rows'] + fn['push_rows'] + fn['threat_missing_rows'] else '✗'}）")
                    print(f"     ★ **降り率** = 降り行 / 被威胁行 = {fn['fold_rows']}/{fn['threat_rows']}"
                          f" = {100 * _rate:.3f}%；白化总体 = 该轴的**决策行**（{w.n} 行）")
                if ax.name in MELD_TYPE_PREFIX:
                    # ★★ **副露种类**的四个数（原始输出）：鸣牌行 / 其中碰 / 吃 / 杠。
                    mn = mtnum[ax.name]
                    _tr = (d["action_rows"] / mn["meld_rows"]) if mn["meld_rows"] else float("nan")
                    print(f"     ★ 副露种类（`{ax.name}` = **碰取向**，动作键前缀 "
                          f"`{MELD_TYPE_PREFIX[ax.name]}`）：**鸣牌动作行** {mn['meld_rows']}"
                          f"（⛔ 主判据的分母）｜其中**碰** {mn['pon_rows']}（= 付奖行候选）"
                          f"｜**吃** {mn['chi_rows']}｜**杠** {mn['kan_rows']}"
                          f"（⛔ 吃/杠那一侧一分钱不付 —— 方向单向）")
                    print(f"     ★ 对账：碰的行 {mn['pon_rows']} == 动作行 {d['action_rows']}"
                          f"（{'✓' if mn['pon_rows'] == d['action_rows'] else '✗ **对不上**'}）"
                          f"；付奖行 {d['paid_rows']} + 拦下 {d['blocked_rows']} == 动作行 "
                          f"（{'✓' if d['paid_rows'] + d['blocked_rows'] == d['action_rows'] else '✗'}）"
                          f"；鸣牌行 == 碰 + 吃 + 杠 "
                          f"({'✓' if mn['meld_rows'] == mn['pon_rows'] + mn['chi_rows'] + mn['kan_rows'] else '✗'}）")
                    print(f"     ★ **碰的行 / 鸣牌行** = {mn['pon_rows']}/{mn['meld_rows']}"
                          f" = {100 * _tr:.3f}%；白化总体 = 该轴的**决策行**（{w.n} 行）"
                          f"｜付奖行 / 决策行 = {100 * d['action_rows'] / max(w.n, 1):.3f}%")
    return stats, audit



def bonus_of(spec: BonusSpec, stats: dict[str, Whitening],
             vals: dict[str, float]) -> tuple[float, dict[str, float]]:
    """**一格**（小局 × 座位）→ 奖励（点）+ 逐轴明细。

    口径：`Σ_i w_i · 1[PAIR_i] · (x_i − μ_i)/σ_i`（⚠ μ/σ 是**本数据集**实测值，见 `whitening_stats`）。
    """
    total = 0.0
    detail: dict[str, float] = {}
    for ax in spec.axes:
        w = stats[ax.name]
        z = (paid_indicator(ax, vals) - w.mu) / w.sd
        c = ax.weight * z
        detail[ax.name] = c
        total += c
    return total, detail


def hand_bonus(spec: BonusSpec, stats: dict[str, Whitening], st: HandState,
               own_policies: set[str] | None = None) -> tuple[float, dict[str, float], int, dict]:
    """**一个小局** → 该小局的奖励（点）+ 逐轴明细。

    ★★ 为什么奖励是**小局级**而不是"逐席各一份"（2026-10-09 改，第一版就在这里错了）：

    训练侧的信用分配是**小局级**的 —— `pretrain._hand_advantage` 先把逐行的 `delta`/`style_bonus`
    **取那一小局的第一行**当成这一小局的价值（`reward_rows`），再对整小局共享。所以：

    * 如果奖励按**席位**给（四席四个值），那么"哪一席的值被取到"就变成**行序的偶然**：
      实测（3 场小冒烟）学生席拿到的塑形 std = **0.0**（每一小局取到的都是同一个教师席的值）；
    * 按**小局**给一份（下面 `own_policies` 指定的"自己"那一席的风格量），四行同值、
      取哪一行都一样 ⇒ 口径唯一、可复现。

    "自己"是谁（`own_policies`）：
    * `None`（`--style-bonus-whiten all`）= **四席取或**：这一小局**任何一家**立直且未放铳都算 ——
      读作"这一局桌上出现了立直，且（某个）立直的人没放铳"，方向仍是"多立直、别点炮"；
    * 给了策略串集合（`--style-bonus-whiten student` 缺省）= 只看**学生席位**的风格量 ——
      与"被训练的那条策略"严格对齐（奖励的是"**你**立直了没有"）。

    @return `(总奖励, 逐轴明细, 参与统计的席位数, 参与统计的席位量字典)`
    """
    cells = [vals for s, vals in indicator_cells(st, own_policies)]
    if not cells:
        # 这一小局学生一次询问都没有（庄家第一张就被荣和那种）⇒ 它不进梯度，奖励天然是 0。
        return 0.0, {}, 0, {}
    # 多席合成一份（风格桌上学生可能就是 1 席；`all` 时是四席）—— 取或见下面注释。
    merged = _merge_cells(cells)
    total, detail = bonus_of(spec, stats, merged)
    return total, detail, len(cells), merged


def _merge_cells(cells: list[dict[str, float]]) -> dict[str, float]:
    """把若干席的风格量合成"这一小局"的一份（**取或**，不是取均值）。

    为什么取或：奖励问的是"**这一小局有没有出现这个行为**"（`style-vector.py` 的分子口径也是
    "该小局宣言过立直"、同一小局多次只算 1）。
    为什么 `no_deal` **不能**跟着 `max`：三家没放铳、一家放铳时 `max(no_deal) = 1` —— 那等于把
    放铳洗掉了。所以它按"**这一小局有没有人放铳**"归一：有人放铳 ⇒ 0。
    """
    any_deal = any(v["deal"] > 0.5 for v in cells)
    # ⚠ `dama` 也走 `max`（四席取或）：合并量只是"这一小局桌上有没有人没立直"这类**描述性**读数，
    #   ⛔ 它**不是**付奖行判据（"能立而不立"只逐行判）—— `dama` 在 `hand` 模式下被
    #   `check_mode_axes` 直接拒掉，就是为了不让这个合并量被当成付奖口径（静默换轴）。
    # ★ `fold` 同理（四席取或 = "这一小局有人降り过吗"）：它也只在 `decision` 模式成立
    #   （`DECISION_ONLY_AXES`）—— 合并量**不是**付奖行判据，但 `decision_violations` 的
    #   "手级=1 ⇔ 有动作行"这条蕴含需要它（否则 `merged['fold']` 取不到 = KeyError，而那是静默崩）。
    out = {k: max(v[k] for v in cells) for k in ("riichi", "meld", "win", "win_points", "dama",
                                                 "riichi_turn", "fold", "pon")}
    out["deal"] = 1.0 if any_deal else 0.0
    out["no_deal"] = 0.0 if any_deal else 1.0
    return out


def audit_whitening(spec: BonusSpec, stats: dict[str, Whitening], hands, *,
                    student_policies: set[str] | None = None,
                    mean_tol: float = 1e-9, var_tol: float = 1e-9,
                    mode: str = MODE_HAND) -> dict:
    """**白化正确性自证**：白化后的指示量在数据集上**均值 ≈ 0、方差 ≈ 1**。

    ⚠ 这里的"白化后"= `(x − μ)/σ`（**不加质量门**）—— 因为质量门 `1[PAIR]` 是**付奖**那一层，
    不是白化那一层（见 `paid_indicator` 的注释）。两者都要报：均值方差归零归一验的是**白化**，
    付奖小局数/总额验的是**付奖规则**。

    ⚠ **总体必须与 `whitening_stats` 一致**（`hand`：`indicator_cells` + `_merge_cells`、以**小局**
    为单位；`decision`：`rows_in_scope` 的**每一行**）：拿"全部座位逐席"去验一个按小局算出来的
    μ/σ，会得到 ≠0 的均值 —— 那不是容差问题，是真的错。
    """
    mode = check_mode(mode)
    check_mode_axes(spec, mode)
    acc: dict[str, list[float]] = {a.name: [] for a in spec.axes}
    for _g, _h, st in hands:
        cells = [vals for _s, vals in indicator_cells(st, student_policies)]
        if not cells:
            continue
        if mode == MODE_DECISION:
            for _i, _s, chosen in rows_in_scope(st, student_policies):
                for ax in spec.axes:
                    w = stats[ax.name]
                    # ★ 与 `whitening_stats` **同一把尺子**（含 `win` 的"我就是被记录的那家"合取、
                    #   含 `dama` 的"能立而不立"）
                    x = 1.0 if row_is_axis_action(ax.name, _s, chosen, st, _i) else 0.0
                    acc[ax.name].append((x - w.mu) / w.sd)
            continue
        vals = _merge_cells(cells)
        for ax in spec.axes:
            w = stats[ax.name]
            acc[ax.name].append((vals[ax.name] - w.mu) / w.sd)
    out: dict[str, dict] = {}
    ok_all = True
    for ax in spec.axes:
        x = acc[ax.name]
        n = len(x)
        mu = float(sum(x) / n) if n else float("nan")
        var = float(sum((v - mu) ** 2 for v in x) / n) if n else float("nan")
        good = bool(n) and abs(mu) <= mean_tol and abs(var - 1.0) <= var_tol
        ok_all = ok_all and good
        out[ax.name] = {"n": n, "mean": mu, "var": var, "ok": good}
    out["_all_ok"] = {"ok": ok_all}
    return out


# ---------------------------------------------------------------------------
# ④ 数据集里的落点：逐行 bonus（`dataset.build` 用）
# ---------------------------------------------------------------------------

class BonusTracker:
    """**逐文件**预扫 → 逐行 `style_bonus`（点）。

    ## 为什么必须先预扫（而不是"边走边算"）

    `style_bonus` 是**小局级**量，而它依赖的是**整小局**的状态（"这一小局立直过没有 / 放铳没有"）。
    所以"边走边算"必然写不出第一行 —— 第一行出现时这一小局的结局还不知道。要在行写出之后回填，
    就得把行号缓冲起来（多一份状态、多一个可能漂移的地方）。

    预扫把这件事变成**纯查表**：先扫一遍这个文件、把**每个小局**的 bonus 算出来
    （一个小局一个值，四行同值 —— 见 `hand_bonus` 的 ★★ 注释），
    之后每一行只做一次 `dict` 查（`value(row)`）。判据三条：
      ① 与"缓冲行号再回填"**逐位等价**（同一份 `HandState` + 同一个 `bonus_of`）；
      ② 行序无关（`HandState.feed` 全是 OR / max / 覆盖 ⇒ 与喂入顺序无关）；
      ③ 查不到就是**报错**（不是 0）—— 静默给 0 = 那一小局的奖励悄悄没了。

    成本：多扫一遍 `g*.jsonl`（2.7 MB/场）。那是 `json.loads` 的活，实测 1000 场 ≈ 几十秒，
    相对于紧凑集构建（~115 s/1000 场）可以忽略；换来的是"奖励值精确"而不是"近似"。

    ## `mode="decision"`（2026-10-10 加）

    预扫仍然只做一次（每个小局一次），但缓存的东西从"**一个数**"变成"**这一小局的付奖规则**"：
    `(范围席位, 逐轴(质量轴成立?, 动作行拿多少点))`；`value(row)` 再按**这一行自己的 `chosen`**
    决定要不要付。两件事因此是分开的、也都是可核的：
      * **质量轴**（`PAIR`）按小局判 —— 与 `hand` 模式逐字相同；
      * **落在哪一行**按行判 —— `chosen` 以该轴的动作键开头。
    ⛔ 非动作行**付 0**（不是 `−μ/σ` 的负偏移）：付了就等于"每一行都付奖"，
    "付奖行数 == 动作行数"这条判据当场失效（需求 ②）。
    """

    def __init__(self, spec: BonusSpec | None, stats: dict[str, Whitening] | None,
                 own_policies: set[str] | None = None, mode: str = MODE_HAND):
        self.spec = spec
        self.stats = stats or {}
        #: "自己"是谁（见 `hand_bonus`）：`None` = 四席取或；否则只看这些策略串坐的席位。
        self.own_policies = own_policies
        self.mode = check_mode(mode)
        check_mode_axes(self.spec, self.mode)     # ⛔ 与 `whitening_stats` 同一道"轴×模式"闸门
        if self.mode == MODE_DECISION and self.spec:
            require_row_axes(self.spec)          # ⛔ 与 `whitening_stats` 同一道前置闸门
        #: `(game, hand_no) -> bonus`（**一个小局一个值**，四行同值；`hand` 模式）
        self.cache: dict[tuple[int, int], float] = {}
        #: `(game, hand_no) -> (范围内席位, {轴: (质量轴成立, 动作行拿多少点)}, 被记录的和了者,
        #: `fold` 付奖行的 **`step`** 集合)`（`decision` 模式）
        #: ⚠ 第四个分量装的是 **`step`**（每场计数器；与 `_decision_value` 的 `row["step"]` 同坐标系）
        #: —— ⛔ 不是行内下标（混用会让付奖只落在少数巧合行上，见 `HandState.row_step`）。
        self.dcache: dict[tuple[int, int],
                          tuple[frozenset, dict[str, tuple[bool, float]], int,
                                frozenset]] = {}
        self.hands_done = 0
        self.paid_hands = 0
        self.paid_sum = 0.0
        self.paid_rows = 0
        self.rows = 0
        self.cache_hits = 0
        #: 本文件开始时的 `cache_hits`（`prebuild` 记）—— 每文件的查表判据按它做差
        self.hits0 = 0
        self.prebuilt_files = 0
        self.hands_no_cells = 0
        #: `decision` 模式的逐轴账（动作行 / 付奖行 / 被质量轴拦下）—— 需求 ② 的**原始输出**来源
        self.dec: dict[str, dict] = {a.name: {"action_rows": 0, "paid_rows": 0, "blocked_rows": 0}
                                     for a in (spec.axes if spec else ())}

    @property
    def enabled(self) -> bool:
        return bool(self.spec)

    def prebuild(self, path: Path) -> None:
        """扫一遍这个文件、把**每个小局**的 bonus 算进 `cache`（**幂等**，同一个 key 只算一次）。

        ⚠ 同时记下"本文件开始时 `cache_hits` 是多少"（`hits0`）—— 查表计数是**每文件**的判据
        （同一个 tracker 会被串行分支跨文件复用，累积计数对不上"本文件写了多少行"）。
        """
        if not self.enabled:
            return
        self.prebuilt_files += 1
        self.hits0 = self.cache_hits
        for g, h, st in iter_hands(path):
            k = (int(g), int(h))
            if self.mode == MODE_DECISION:
                if k in self.dcache:
                    continue
                self.dcache[k] = self._decision_rule(int(g), int(h), st)
                self.hands_done += 1
                continue
            if k in self.cache:
                continue
            total, _detail, n_cells, _vals = hand_bonus(self.spec, self.stats, st,
                                                        self.own_policies)
            if n_cells == 0:
                self.hands_no_cells += 1
            if total != 0.0:
                self.paid_hands += 1
                self.paid_sum += total
            self.cache[k] = total
            self.hands_done += 1

    def _decision_rule(self, g: int, h: int, st: HandState):
        """这一小局的付奖规则（`decision` 模式）：`(范围内席位, {轴: (质量轴, 动作行金额)}, 被记录的和了者)`。

        ⚠ 需求 ④ 的**第二道闸门**在这里：`prebuild` 也走一遍 `decision_violations`
        （`whitening_stats` 是第一道）—— 因为 `prebuild` 是**真正写列**的那条路，
        只在白化那一步查的话，"白化过了、写列时口径漂了"就没人管。

        ★ 第三个分量 `winner` 是 `win` 轴行级判据要的"**我就是被记录的那家**"（`HandState.winner`，
        与 `whitening_stats`/`axis_row_action_counts` 同一个来源）—— 写列时**不重读** `row` 里的
        `hand_winner`（两个来源必然漂移）。
        """
        bad = decision_violations(self.spec, st, self.own_policies)
        if bad:
            raise BonusSpecError(
                f"`decision` 模式在 game={g} hand={h} 取不到'哪一行做了这个动作'："
                + "，".join(f"[{a}] {r}" for a, r in bad)
                + "。⛔ **不退化**成 `hand` 模式（那会让这次实验什么都没测）")
        cells = [v for _s, v in indicator_cells(st, self.own_policies)]
        if not cells:
            return (frozenset(), {}, int(st.winner), frozenset())
        merged = _merge_cells(cells)
        seats = frozenset(s for _i, s, _c in rows_in_scope(st, self.own_policies))
        # ★★ `fold` 的付奖行**集合**（预扫那条路算出来的、判据口径唯一的一份）——
        #   写列时按行号查（⛔ 不重算：两条实现各算一套必然漂移，那是本仓最经典的静默错位）。
        #   ⚠⚠ `seat` = 这一行的**行动者**（`rows_in_scope` 给的那个 `_s`），**显式传入**
        #   ⇒ 付钱路与记账路（`HandState.feed` 里那份 `row_fold`）是**同一把尺子**。
        #   上一轮的坑：`_row_obs` 不带 `seat` ⇒ 这里按"含行动者自己"的**字面口径**算，
        #   而记账路按"只数别家"算 ⇒ 真正付出去的钱与审计账不同源（冻结账 +4892/+96/+4796）。
        #   ⚠⚠⚠ **键必须是 `step`（每场计数器），不是行内下标**（`rows_in_scope` 的 `i`）：
        #   `_decision_value` 手上只有 `row["step"]` ⇒ 拿行内下标当键 = 两套坐标系混用，
        #   付奖只会落在"下标恰好等于 step"的少数行上（实测 1000 场：本该 1578 行、实际 **150** 行
        #   ⇒ 上一轮那条链的**剂量只有名义值的 ≈9.5%**）。契约由 `smoke` 与 `selfcheck` 的行集合判据钉住。
        fold_steps: list[int] = []
        for i, _s, c in rows_in_scope(st, self.own_policies):
            if 0 <= i < len(st.row_step) and st.row_step[i] >= 0 \
                    and fold_row_ok(_row_obs(st, i), c, _s):
                fold_steps.append(st.row_step[i])
        fold_rows = frozenset(fold_steps)
        per: dict[str, tuple[bool, float]] = {}
        for ax in self.spec.axes:
            ok = pair_ok(ax.pair, merged)
            per[ax.name] = (ok, spec_axis_pay(self.spec, self.stats, ax))
        n_act = axis_row_action_counts(self.spec, st, self.own_policies)
        for ax in self.spec.axes:
            self.dec[ax.name]["action_rows"] += n_act[ax.name]
            if per[ax.name][0]:
                self.dec[ax.name]["paid_rows"] += n_act[ax.name]
            else:
                self.dec[ax.name]["blocked_rows"] += n_act[ax.name]
        return (seats, per, int(st.winner), fold_rows)

    def value(self, row: dict) -> float:
        """这一行的 `style_bonus`（点）。没开塑形时恒 0.0（一行都不多走）。"""
        if not self.enabled:
            return 0.0
        self.rows += 1
        k = (int(row.get("game", -1)), int(row.get("hand_no", -1)))
        if self.mode == MODE_DECISION:
            return self._decision_value(k, row)
        total = self.cache.get(k)
        if total is None:
            # ⛔ 查不到**必须是错**：静默给 0 = 这一行的奖励悄悄没了（而训练照跑）。
            raise BonusSpecError(
                f"`{COLUMN}` 查不到小局 {k} —— 预扫 `prebuild` 漏了这个文件/小局。"
                f"要么是拼接调用方没按文件喂，要么是 `game`/`hand_no` 字段缺失。不猜、不填 0")
        self.cache_hits += 1
        v = float(total)
        if v != 0.0:
            self.paid_rows += 1
        return v

    def _decision_value(self, k: tuple[int, int], row: dict) -> float:
        """`decision` 模式：**这一行**的奖励（只在该行真的做了动作、且该小局质量轴成立时非 0）。

        ★ 行级判据与预扫**同一把尺子**：`row_is_axis_action` 需要 `HandState`，而这里只有 `row`
        ⇒ 用预扫存下来的 **`winner`**（该小局被记录的和了者），**不**重读 `row["hand_winner"]`；
        `dama` 用的是**这一行自己的 `legal`**（`row["legal"]`，缺失时退到 `obs.legal`；两处都缺
        ⇒ `legal_has` 当场报错，⛔ 不按 False 静默付 0）。
        """
        ent = self.dcache.get(k)
        if ent is None:
            raise BonusSpecError(
                f"`{COLUMN}`（decision 模式）查不到小局 {k} —— 预扫 `prebuild` 漏了这个文件/小局。"
                f"不猜、不填 0")
        self.cache_hits += 1
        seats, per, winner, fold_rows = ent
        seat = int(row.get("seat", -1))
        if seat not in seats:
            return 0.0                     # 不在统计范围内（不是学生席）⇒ 一行都不付
        chosen = str(row.get("chosen") or "")
        step = int(row.get("step", -1))
        total = 0.0
        for ax in self.spec.axes:
            ok, pay = per[ax.name]
            if not ok:
                continue
            if ax.name in ROW_ACTION_FOLD:
                # ★★ **被威胁时的降り**：判据要"这一行**当时**的公开状态"（`obs.riichi` 当行快照 +
                #   `obs.events` 重建的現物），而写列这条路手上只有 `HandState` 的**行号**
                #   ⇒ 只认预扫算出来的那份集合（`_decision_rule` 里由 `fold_row_ok` 算出）。
                #   ⛔ 这里**不重算** `fold_row_ok(row["obs"], chosen, seat)`：那会造出第二条实现，
                #   而"两条路各算一套"正是本仓最经典的静默错位。
                #   ⚠⚠ 集合里装的是 **`step`**（每场计数器，= 这里的 `step`）—— ⛔ 不是行内下标。
                if step not in fold_rows:
                    continue
                total += pay
                continue
            if ax.name in ROW_ACTION_DECLINE:
                legal = row.get("legal")
                if legal is None:
                    legal = (row.get("obs") or {}).get("legal")
                if not row_is_action(ax.name, chosen, legal):
                    continue
                total += pay
                continue
            if ax.name in ROW_ACTION_TURN:
                # ★ **早立直**：巡目取自**这一行自己的** `obs.player_draws`（与预扫那一遍
                #   `HandState.feed` 用的是**同一个**字段、同一个 helper）⇒ 两条路不会各算一套。
                #   ⚠ 非宣言行 ⇒ `row_is_action` 直接 False（**不会**因为取不到巡目报错）；
                #   是宣言行却取不到巡目 ⇒ 当场报错（⛔ 不猜 0）。
                if not row_is_action(ax.name, chosen, None,
                                     declared_turn(row.get("obs") or {})):
                    continue
                total += pay
                continue
            if not row_is_action(ax.name, chosen):
                continue
            if ROW_SELF_SEAT.get(ax.name) == "winner" and winner != seat:
                continue                   # 多家荣和：只有被记录的那家拿得到这笔钱
            total += pay
        v = float(total)
        if v != 0.0:
            self.paid_rows += 1
        return v

    def audit(self) -> dict:
        out = {"hands": self.hands_done, "paid_hands": self.paid_hands,
               "paid_rows": self.paid_rows, "paid_sum": self.paid_sum,
               "rows": self.rows, "cache_hits": self.cache_hits,
               "hands_no_cells": self.hands_no_cells,
               "prebuilt_files": self.prebuilt_files, "mode": self.mode}
        if self.mode == MODE_DECISION:
            out["per_axis"] = {k: dict(v) for k, v in self.dec.items()}
        return out


def paid_accounting(spec: BonusSpec, stats: dict[str, Whitening], hands,
                    own_policies: set[str] | None = None, mode: str = MODE_HAND) -> dict:
    """扫一遍全部小局算**付奖账**（父进程写 meta 用；逐轴：付奖小局数 / 付奖总额 / 原始分子和）。

    ⚠ 与 `BonusTracker` 走**同一份** `hand_bonus` / `_merge_cells` 口径（不是第二套实现）——
    两套实现漂移是这类量最经典的静默 bug。`decision` 模式下**同一份 `decision_bonus`**，
    并且额外汇总"动作行 / 付奖行 / 被质量轴拦下"（与 `BonusTracker.dec` 同一个口径）。
    """
    mode = check_mode(mode)
    check_mode_axes(spec, mode)
    if mode == MODE_DECISION:
        require_row_axes(spec)
    paid_hands: dict[str, int] = {a.name: 0 for a in spec.axes}
    paid_sum: dict[str, float] = {a.name: 0.0 for a in spec.axes}
    raw_sum: dict[str, float] = {a.name: 0.0 for a in spec.axes}
    dec: dict[str, dict] = {a.name: {"action_rows": 0, "paid_rows": 0, "blocked_rows": 0}
                            for a in spec.axes}
    n_hands = 0
    n_cells = 0
    paid_total = 0
    tot_sum = 0.0
    for _g, _h, st in hands:
        n_hands += 1
        cells = [vals for _s, vals in indicator_cells(st, own_policies)]
        if not cells:
            continue
        vals = _merge_cells(cells)
        if mode == MODE_DECISION:
            n_cells += 1
            total = 0.0
            for i, s, chosen in rows_in_scope(st, own_policies):
                t, _d = decision_bonus(spec, stats, st, s, chosen, own_policies, row_idx=i)
                total += t
            tot_sum += total
            if total != 0.0:
                paid_total += 1
            n_act = axis_row_action_counts(spec, st, own_policies)
            for ax in spec.axes:
                if pair_ok(ax.pair, vals) and vals[ax.name] > 0:
                    paid_hands[ax.name] += 1
                    paid_sum[ax.name] += n_act[ax.name] * spec_axis_pay(spec, stats, ax)
                    raw_sum[ax.name] += vals[ax.name]
                dec[ax.name]["action_rows"] += n_act[ax.name]
                if pair_ok(ax.pair, vals):
                    dec[ax.name]["paid_rows"] += n_act[ax.name]
                else:
                    dec[ax.name]["blocked_rows"] += n_act[ax.name]
            continue
        total, detail, _n, _v = hand_bonus(spec, stats, st, own_policies)
        n_cells += 1
        tot_sum += total
        if total != 0.0:
            paid_total += 1
        for ax in spec.axes:
            if pair_ok(ax.pair, vals) and vals[ax.name] > 0:
                paid_hands[ax.name] += 1
                paid_sum[ax.name] += detail[ax.name]
                raw_sum[ax.name] += vals[ax.name]
    out = {"hands": n_hands, "hands_counted": n_cells, "paid_hands_total": paid_total,
           "paid_sum_total": tot_sum, "mode": mode,
           "per_axis": {a.name: {"paid_hands": paid_hands[a.name],
                                 "paid_sum": paid_sum[a.name],
                                 "raw_sum": raw_sum[a.name]} for a in spec.axes}}
    if mode == MODE_DECISION:
        for a in spec.axes:
            out["per_axis"][a.name].update(dec[a.name])
    return out


def write_audit(out_dir: Path, audit: Audit, whitening: dict, extra: dict | None = None) -> Path:
    """把奖励账落盘成 `<紧凑集>/style-reward.json`（**可审计**：w/μ/σ/付奖小局数/付奖总额）。

    ★ 持久副本（`AUDIT_DIR_ENV`）：设了 `MAHJONG_STYLE_AUDIT_DIR` 时**同时**写一份到
    `<dir>/<紧凑集目录名>.json`，并**返回那一份的路径**（`meta.audit_path` 指向持久路径）。
    理由见 `AUDIT_DIR_ENV` 的注释：`run-league.ps1` 每代会把 `compact\<tag>` 删掉。
    """
    payload = {
        "tool": "mahjong_ml/v4/style_reward.py",
        "note": "风格奖励塑形的账。判据（强弱）⛔ 不看这里 —— 只看 mahjong_ml/v4/gate.py；"
                "本文件回答的是「奖励到底付了多少钱、按什么口径付的」。",
        "spec": audit.spec,
        "whiten_scope": audit.whiten_scope,
        "hands_total": audit.hands_total,
        "hands_eligible": audit.hands_eligible,
        "axes": audit.axes,
        "whitening_check": whitening,
        "source": extra or {},
    }
    p = Path(out_dir) / "style-reward.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    p.write_text(text, encoding="utf-8")
    # ★ 持久副本（见 `AUDIT_DIR_ENV`）：`compact\<tag>` 会被 `run-league.ps1` 每代轮换删掉，
    #   所以给了目录就**另写一份逐字节相同**的账，并把它当成 `meta.audit_path` 返回。
    persist = (os.environ.get(AUDIT_DIR_ENV) or "").strip()
    if persist:
        q = Path(persist) / f"{Path(out_dir).name}.json"
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(text, encoding="utf-8")
        return q
    return p


#: 当前这条链的 `w` 从哪读（**只用于日志措辞**）—— 与 `loop.py` 的 `--style-bonus` 缺省同一来源。
WEIGHT_ENV = "MAHJONG_STYLE_BONUS"


def current_weight(axis: str | None = None) -> float | None:
    """从环境变量读回**当前这一路的 `w`**（只给日志措辞用；读不到就返回 `None`，⛔ 不猜）。

    ⚠ 为什么需要它（2026-10-10 修正一处**误标定**的根因）：`pretrain.py` 打的那行尺度读数是
    `base_std / std(塑形列)`，而塑形列**本身已经乘过 `w`** ⇒ 那个数
    `= (base_std/std(z)) / w = w*/w`，是**放大倍数**，**不是"该设的 w"**。
    上一轮把它读成"要 1:1 大约要 `w=2`"，于是三档里最高档（`w=2800`）被当成"已经 1:1"
    （实际只有 0.45× std、真实 `w* ≈ 6170`）。
    要打出真正的"所需 w"就必须知道**当前 `w`**：`所需 w = 当前 w × 放大倍数`。
    """
    spec = parse_bonus(os.environ.get(WEIGHT_ENV))
    axes = [a for a in spec.axes if axis is None or a.name == axis]
    return float(axes[0].weight) if axes else None


def parity_note(mult: float, axis: str | None = None) -> str:
    """`w_for_parity` 那行的**措辞**（唯一落点）：明确区分「**放大倍数** `w*/w`」与「**所需 w**」。

    @param mult `base_std / std(塑形列)` = 把**当前这笔**塑形抬到与结果奖励 1:1 的**倍数**
        （⚠ 不是"该设的 `w`" —— 见 `current_weight` 的注释）。
    """
    w_now = current_weight(axis)
    head = f"**放大倍数 w*/w = {mult:.2f}**（⛔ 不是「该设的 w」）"
    if w_now is None:
        return (f"{head}；**所需 w** = 当前 w × 该倍数（⚠ 当前 w 读不到：`{WEIGHT_ENV}` "
                f"没设或不含这一轴 ⇒ ⛔ 不拿倍数当 w 用）")
    return (f"{head} ⇒ **所需 w**（= 当前 w {w_now:g} × {mult:.2f}）≈ **{w_now * mult:.0f}**")


def stats_to_json(stats: dict[str, Whitening]) -> dict:
    return {k: v.to_json() for k, v in stats.items()}


def stats_from_json(obj: dict) -> dict[str, Whitening]:
    """`meta.json` 里的白化参数 → `Whitening`（子进程/训练端读回用 —— 只有一份解析）。"""
    out: dict[str, Whitening] = {}
    for k, v in (obj or {}).items():
        out[k] = Whitening(name=str(v.get("axis", k)), kind=str(v.get("kind", "bernoulli")),
                           mu=float(v["mu"]), sd=float(v["sd"]),
                           n=int(v.get("n_used", v.get("n", 0))),
                           n_all=int(v.get("n_seen", v.get("n", 0))))
    return out
