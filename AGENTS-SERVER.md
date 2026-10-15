# AGENTS-SERVER.md —— 服务端域（Java 规则引擎与牌桌行为）

> 本文件是 `AGENTS.md` 的**服务端域切片**：只装**服务端 / 规则引擎**判据。通用铁律（§2）/
> 验证五层（§4）仍在主 `AGENTS.md`；**细节与踩坑经过**在 `NOTES.md` §6.3。
> ⚠ 不在 harness 自动加载候选表 ⇒ **永不自动加载**，需要时**显式读它**。

## 路由：我要…… ⇒ 读哪一节

| 我要…… | 读这里 |
| --- | --- |
| 改牌桌行为 / 自动开关 / 吃碰校验 / 资源上限 / 局间时序 | §6.3（本文件） |
| 改协议字段或报文语义 | `AGENTS-PROTO.md`（**不属本域**） |
| 改役种/符数/点数/振听（`rules/`） | 主 `AGENTS.md` §2.1 + §4 L1（跑 `--selftest`） |
| 改训练接口（`Table.decideBot` / Observation） | `AGENTS-TRAIN.md`（**不属本域**） |
| 身份 uuid / 托管 / 投票 | `AGENTS-PROTO.md`（**不属本域**） |
| 我要动**别的域** | 回 `AGENTS.md` 顶部路由表 |

> **何时切回主 `AGENTS.md`**（触发条件，非建议）：① 本域任务**收尾**（改动已提交 / 已跑完本域回归）；② 改动**跨出**上表清单；③ 要动的路径**连续 ≥2 个工作轮**全部落在服务端域（`server/**`）之外；④ 需要确认 §2 铁律 / §4 验证五层 / §8 文档纪律时 —— 满足任一即**先回主文件**读 §2 与 <FOCUS:BEGIN> 块的改指判据。

---

### 6.3 牌桌行为、规则与资源

> 判据在这里；**当初怎么踩的 / 为什么是两次清零**见 **`NOTES.md` §6.3**。

- **牌桌外的三个自动开关（`AutoBar` + `autopolicy::decide`）：自动胡了 / 不吃碰杠 / 自动摸切**。
  按钮在牌桌下方（**不进 ActionBar** —— 那里按钮动态增删、高度固定），文案全在 `ui.auto.*`。
  判据**只看 `ask.options`**（客户端不自己算听牌，见 §2.1），且**和牌永远优先**：options 里有
  `tsumo`/`ron` 时**只有「自动胡了」**能替玩家动 —— 自动摸切绝不切出能胡的那张牌、
  不吃碰杠绝不把到手荣和 `pass` 掉。自动动作也必须走
  `ActionBar::actionCmd/discardCmd`（带 `ask_id`；询问失效时返回空对象 → 自动动作自动作废）。
  **每小局结束（`round_end`）立刻 `resetAutoFlags()` 全关**，且**每小局开始（`round_start`）
  再复位一次**（局间结算期间点开的自动不许带进新的一局）。⚠ 两次都清是**刻意**的（口径改过）：
  看到 `round_start` 里那句 `resetAutoFlags()`，**不要当成 bug 删掉**。回归：`client --selftest` 的「自动开关」两组。
- **「吃」必须由服务端自己校验顺子**（`Round.pickChiTiles`）：`want` 来自客户端，⛔ 不许只查"手里有没有
  这两张" —— `Evaluator` 是按 `Meld.baseKind()+isRun()` **重建**面子形状算役的，假顺子会按真顺子计分
  （一条报文就能改分）；`want` 必须**恰好两张**（越界写 `int[2]` 会 AIOOBE = 整场半庄**静默死亡**）。
  回归：`SelfTest.chiValidationTests`；`Table.playGame` 的异常兜底必须**广播终局**再收尾。
- **接收侧的资源上限**：单条报文 1 MB（`Session.readBoundedLine` **边读边判**，绝不能等
  `readLine()` 收完再判）、同时在线上限 `Server.MAX_SESSIONS`、`fill_bots` ≤ 4、
  客户端传来的 `rules` 一律过 `Rules.clampToSane()`；**发牌种子**用 `SecureRandom` 基准 +
  每局 SplitMix 打散，且**每小局都从"当前时刻毫秒数"重新起步**（`Table.nextRoundSeed()`）——
  别退回"`mixSeed(seedBase + 局序号)`"那种"推出一局即推出整场"。自检要可复现，所以它显式打开
  `Table.debugDeterministicSeed` 走旧的确定岔路；**生产路径永远是时刻种子**。
- **局间时序：服务端先等满 5 秒（或所有人确认），再开下一局**：
  `round_end` → `sleepMs(roundDelayMs)` → `awaitRoundConfirm()`（广播 `round_wait{ms:5000}` 后等）
  → 下一局 `round_start`。**服务端两条铁律**：`drainConfirm()` 只从 `submit()` 写的**那条队列**取
  （非 confirm 的原序放回）；`awaitAction()` 认领时必须**确认它是动作**（否则残留 `confirm` 被当出牌）。
  **客户端三条配合**：`round_wait` 起倒计时、到点自动关弹窗（= `confirm`）；`round_start`/`game_end`
  强制关弹窗但**不发 `confirm`**（`closeResultDialog(confirm=false)` 的唯一用途）；弹窗已关才立刻 `confirm`。
  ⚠ 三条都踩过（§2.3-8）；明细见 `NOTES.md` §6.4.2。

