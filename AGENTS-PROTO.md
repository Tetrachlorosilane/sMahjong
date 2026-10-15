# AGENTS-PROTO.md —— 通信端域（协议 / 文案 / 结算 / 身份 / 托管 / 投票）

> 本文件是 `AGENTS.md` 的**通信端域切片**：装协议契约纪律（§6.4）+ 身份（§6.8）+ 托管与投票（§6.9）。
> 通用铁律（§2）/ 验证五层（§4）仍在主 `AGENTS.md`。
> ⚠ 不在 harness 自动加载候选表 ⇒ **永不自动加载**，需要时**显式读它**。

## 路由：我要…… ⇒ 读哪一节

| 我要…… | 读这里 |
| --- | --- |
| 改协议字段 / 报文语义 / 码表 / i18n 文案 / 结算界面 | §6.4（本文件）；契约原文 `docs/PROTOCOL.md` |
| 改身份 uuid / 玩家档案 / 接回座位 | §6.8（本文件） |
| 改掉线托管 / 结束对局投票 | §6.9（本文件）+ 主 `AGENTS.md` §2.3-13 / §2.3-14 |
| 改规则判定本身（役种/符数/点数） | `AGENTS-SERVER.md`（**不属本域**） |
| 改牌桌绘制 | `AGENTS-CLIENT.md`（**不属本域**） |
| 我要动**别的域** | 回 `AGENTS.md` 顶部路由表 |

> **何时切回主 `AGENTS.md`**（触发条件，非建议）：① 本域任务**收尾**（改动已提交 / 已跑完本域回归）；② 改动**跨出**上表清单；③ 要动的路径**连续 ≥2 个工作轮**全部落在通信端域（`docs/PROTOCOL.md` + 两侧 `net/`）之外；④ 需要确认 §2 铁律 / §4 验证五层 / §8 文档纪律时 —— 满足任一即**先回主文件**读 §2 与 <FOCUS:BEGIN> 块的改指判据。

---

### 6.4 协议、文案与结算

> 这里是**判据**（改协议/文案/结算时必须守的）。**完整纪律、样例报文与踩坑经过**见 **`NOTES.md` §6.4**。

**① 报文里不带中文 —— 词汇性文本一律只发 ASCII 码，中文只存在于客户端语言文件里。**
两端都显式钉死 UTF-8（服务端 `StandardCharsets.UTF_8`、客户端 `QJsonDocument`）。

- **只有三类字段是用户数据**：`seats[].name` / `room.name`·`hello_ok.name` / `chat.text`；其余全是**码**
  （完整清单见附录 E）。判据一律 ASCII，**客户端不许拿中文串做逻辑判断**。
- **码表的唯一映射处** = `server/.../rules/YakuCodes.java`（发报文时才翻码）。
  ⚠ **新增役种必须在那里登记**，否则整场自检会因 `YakuCodes.misses() > 0` 红。
- **语言文件** `client/assets/i18n/<locale>.json`（键 = `<族>.<码>`）：加载顺序与 `tiles/` 同约定
  （**exe 同级 `i18n/` → qrc → 码本身**）⇒ **改文案不用重编译**。⚠ 认不出的码**原样显示码本身**；
  码为空串时回退老字段（`yaku[].name` / `error.msg`）。
- **界面固定文案全在 `ui.*`**（源码里不留中文字面量，例外用 `// i18n-keep`，且**必须与它豁免的字面量同行** —— `i18n-scan` 按行取豁免）。
  **新文案走三件套**：`i18n-map.mjs`（唯一数据源）→ `i18n-apply.mjs` → `i18n-gen.mjs`（只补缺）—— **先写字面量再加进 map**；改文案直接编辑 json。
- **截断按「码点」不按 UTF-16 码元**（`Json.clampCodePoints`）。
- ⚠ **Java 陷阱**：注释里也不能写「反斜杠 + u」。⚠ **客户端陷阱**：中文一律 `QStringLiteral` /
  `QString::fromUtf8`，别用 `QLatin1String`。
- **回归**：`SelfTest.jsonEncodingTests` / `yakuCodesTests` + L2 的 Lang 组 + `check-i18n` /
  `i18n-gen --check` + `utf8-test` + `e2e-test`（逐条非 ASCII 审计，白名单只有 `name`/`text`/`msg`）。

**② 结算界面：役满写「n 倍役满」，不写番数**（役满**没有番这个量纲**）。

- 报文里 `yaku[].han` / 合计 `han` **不能是 0**：按「役满 = 13 番等价」折算（`13 × 倍数`），于是
  **逐役之和 == 合计**；真正的量纲在 `yakuman` 字段。
- **累计役满**（番数 ≥13 但没有役满役，`yakuman == 0`）**不是役满**：照常显示番数与 `limit`。
- 界面判据**一律看 `yakuman` 字段**，不要用 `han >= 13` 去猜（回归：L2 ResultDialog 组 + `mock-server … yakuman` 截图）。

**③ 能用图形表示的就不再重复文字** —— `agariHtml()` 只在 `schematicRenderable(schematicOf(...))` 为假
（字体缺失 / 牌码串非法）时才补「手牌 / 宝牌指示牌 / 里宝指示牌」三行文字；**该判据与构造函数显示图形块的
判据必须共用同一个函数**。**数字类信息（宝牌/赤宝/里宝张数、番符、点数收支）照常保留**。

**④ 鸣牌仲裁：优先级、提前收工、队列卫生**（三条一体，判据见 §2.3-10）。实现位置：
`RoundClaims.rankOf/canBeat`（等级 → 座次，**与仲裁循环同一把尺子**）· `RoundClaims.shouldStop` · `Round.claimPhase` 末尾的 `cancelAsk` + `dropReplies`
+ `askedTypes` 校验。

**⑤ 王牌/岭上与杠**：王牌 14 张 = 4 岭上 + 5 表宝牌指示牌 + 5 里宝指示牌；**开杠那一刻**牌山末尾移进王牌
（`tiles_left` −1），**岭上摸牌**不动 `tiles_left`（只看 `dead_wall_left`，每次摸牌都要带）；
一局最多 4 次杠（四个闸门统一走 `Round.canKan()`）；**被拒绝的杠绝不能摸岭上牌**；
四杠散了在"那张牌落地且没人和"时才判。⚠ 自检看**选项**用 `debugAskTap`，要连**鸣牌**一起看只能用
`debugChoiceTap`。**细节见 NOTES §6.4。**

**⑥ 服务端并发模型**：**每桌一个线程串行推进状态机**，对局内的命令只往队列投消息 → **对局逻辑内部无需加锁**；
⚠ 但**等待室命令是各连接自己的线程直接执行的**（清单见 NOTES §6.4），读写同一份座位数组 ⇒ 房间状态有**一把锁**
`Table.roomLock`：「判据 + 改座位 + `broadcastRoom`」放进 `synchronized (t.roomLock())`，牌桌线程在**同一把锁**里置 `playing = true`。
⚠ 换座必须 `resyncSessionSeats()`，等待室命令按 `Table.seatOfSession(this)` **反查**座位（不信 `session.seat`）—— 否则「准备」写到别人座位。**细节见 NOTES §6.4。**

**⑦ 赤宝牌张数**：合法值只有 **0 / 3**（0 = 发牌期就把赤五换成普通五，136 张不变）；**1/2/4 一律
归一化到 0/3 并记一条 WARN**（1/2 说不出"少放哪几张"、4 没有码位）——⛔ 绝不静默保留（否则就是 §2.3-11
那个"牌山 3 张、牌谱声明 0 张"的自相矛盾）。见 NOTES §10 与 `SelfTest.akaRuleTests`。

### 6.8 身份（uuid）与玩家档案

**连接后服务端立刻问客户端要一个保存过的 uuid**，客户端应答；客户端没有就由服务端生成并回发
（客户端必须存下来）；服务端没有这个 uuid 的档案就建一份**初始玩家**。**同一个 uuid = 同一个玩家**，
显示仍以昵称为准。uuid 将来是**玩家信息的主键**。协议见 PROTOCOL §2.0，需求由来与 TTL 见 NOTES §6.8。

- 服务端：`server/.../player/PlayerStore.java`（进程级单例）。
  落盘 `<--player-dir>/players.json`（默认 `players/`，`--uuid-ttl-days` 默认 60，`--no-player-store` 关掉）。
  - **档案里认不出的键原样保留** —— 它就是为"以后要当主键存更多东西"准备的（同 `Settings.extra` 的做法）。
  - **原子写**（`.tmp` + ATOMIC_MOVE）+ **落盘节流**（登录是热路径，2 秒内的多次登录合并成一次写）。
  - 清理：**启动时一次**，之后维护线程每 6 小时一次；判据是**严格大于** TTL（`ttl=0` 也能对）。
- 客户端：`Settings.uuid`（`settings.json`；形状不对就清空 = "我还没有身份"）+
  `MainWindow` 的 `uuid_ask` / `uuid_ok` 两个分支（**`issued:true` 时必须落盘**，否则下次又变成新玩家）。
- **接回座位**：uuid 正**托管**在某个座位上时（§6.9/§2.3-13），新连接可接回它
  （`Session.tryResumeSeat`：锁内二次确认"那一格确实空着"→ 沿用**原 pid**）。⛔ 判据是 **`rejoin{pid,token}` 对得上**
  （凭据断开后**保留到 TTL 2 分钟**，`Server.retired`），**光有 uuid 不算**（`settings.json` 可被复制 ⇒ 谁复制谁顶位）；
  没有凭据就走普通 `hello`，收到 `bad_token` 清凭据重发 `hello` 自愈。协议细节见 PROTOCOL §2.4。
- 回归：`SelfTest.playerStoreTests`（形状/记账/TTL/向前兼容/坏文件）+ `node tools\uuid-test.mjs`（真 socket）。


### 6.9 掉线托管 与 结束对局投票

两件事都写在协议里（PROTOCOL §2.5 / §3.13），共同点是**状态不在牌局线程上**：

- **托管**（`Seat.away`）：判据、行为与坑见 §2.3-13。实现三处：
  `Table.markAway`（掉线/退房都走它，含**房主转移**）、`Round.awaySeat`（那一家的回合走"返回 null →
  默认摸切"，鸣牌段**连选项都不算**）、`Table.releaseAwaySeats`。
  ⚠ 「托管」与「空位」必须一直分开：`occupied()` 含 `away`（座位不会被抢），但 `readyToStart()` 不认它
  （托管的人不能算"已准备"）。
- **投票**（`vote_*`）：`Table.requestVoteEnd/castVote/onVoteTimeout`，状态机在 `voteLock` 下，
  事件**从各连接的读线程广播**（所以 `ReplayRecorder.add` 是 `synchronized`、`Table.replay` 是 `volatile`）。
  - 门槛 `votesNeeded(total) = total/2 + 1`，分母 `eligibleVoters()` = **在场真人**（不数机器人、不数托管）；
  - **发起人算同意**（点「结束对局」就是在表态）；
  - 够票→`passed`（→`voteEnded` + `wake()`，见 §2.3-14）；不够票→`impossible`；到点→`timeout`；
  - 一次结论之后**全员冷却 5 分钟**（`VOTE_COOLDOWN_MS`，**服务端记时**：拒绝时回 `wait_ms`）；
  - 通过之后 `playGame` 走**正常终局路径**，所以**点数仍然守恒**（`SelfTest`/`vote-test` 都断言 100000）。
- 回归：`SelfTest.voteTests`（含"开线程投票叫醒牌局线程"那条）+ `node tools\vote-test.mjs`。

