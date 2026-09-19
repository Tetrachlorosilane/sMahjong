# 安全 / 逻辑 / 性能审计报告

对服务端（Java 21，~7900 行）与客户端（Qt6 C++17，~8300 行）的一次**只读审计 + 修复**记录。

- 审计方式：两路独立审计各自逐行读代码（服务端一路、客户端一路），**每条结论都要给出
  `文件:行` 与决定性代码**，并显式区分「**已确认**」（把调用链走通）与「**存疑**」（需运行时验证）。
- 修复原则：只修**已确认**的问题；每条修复都要能过既有的回归网（L1 服务端自检 / L2 客户端自检 /
  L3 协议端到端），**新增规则判定必须补断言**。
- 状态含义：`已修` = 本次改掉且有回归覆盖；`已缓解` = 改了但只挡住主要途径；`已知` = 记录不改（附原因）。

---

## 0. 一句话结论

> **客户端不再是"随便发的报文"的信任对象** —— 审计里最要命的两条都在服务端接收侧：
> 一条畸形 `chi` 能**崩掉整桌**（异常冒到牌桌线程，四个客户端永远挂在牌桌上），
> 一条构造出来的 `chi` 能**改分**（面子形状被 `Evaluator` 按 kind 重建，假顺子被当真的算役）。
> 两条都在本次修掉并补了断言。
>
> 其次是**资源类**问题：服务端读一行不设上限（一个连接就能 OOM）、两个方向的队列无界、
> 连接数无上限、`fill_bots` 是个无界循环；以及**可预测的发牌种子**（`nanoTime` 递增）。
> 这些也都已处理。

---

## 1. 服务端

### 1.1 严重（已修）

| id | 严重度 | 问题 | 位置 | 触发与影响 | 修复 |
| --- | --- | --- | --- | --- | --- |
| **S-01** | **critical** | 「吃」取牌不查下标 → `ArrayIndexOutOfBoundsException` | `game/Round.java` `pickChiTiles()` | 任意一家在能被吃时回 `{"type":"chi","tiles":[三张]}`：`int[2]` 越界。异常冒到牌桌线程，`Table.playGame` 的 `catch` 直接 `return`，**不发 `round_end`/`game_end`** → 整场半庄静默死亡，四个客户端挂在牌桌上 | `want` 必须恰好 2 张，其余一律作废；并在 `Table.playGame` 的兜底里改为**广播终局**再收尾 |
| **S-02** | **critical** | 「吃」不校验顺子成立 → **算分被客户端控制** | `game/Round.java` `pickChiTiles()` | 只查"手里有没有这两张"。手里有 1m、5m 就能把 `{1m,3m,5m}` 当顺子吃下去；而 `Evaluator` 是按 `Meld.baseKind()+isRun()` **重建**面子形状算役的 → 假顺子被当成真 1m2m3m，役种/番数/点数全失真（`PROTOCOL.md` 明写"非法动作不得改变状态"） | 自己校验：两张不同 kind、同花色、都是数牌、与所吃那张恰好连成三张；否则返回 `null`（按"没吃"处理） |
| **S-03** | high | 读一行**先收完再判长度** → 单连接可 OOM | `net/Session.java` `readLoop()` | `BufferedReader.readLine()` 内部 StringBuilder 无上限，1 MB 的检查在整行收完**之后**才跑。对端一直不发 `\n` 就能撑爆堆 | 改成 `BufferedInputStream` + `readBoundedLine()`：**边读边判** 1 MB 上限，超限回 `too_large` 后断开；顺带保留 CRLF、空行、EOF 语义 |
| **S-04** | high | 连接数无上限（每连接两条线程 + 一个 fd） | `net/Server.java` accept 循环 | accept 之前无需任何认证，开一堆空闲连接即可耗光线程/fd | `MAX_SESSIONS = 256`，超限直接关掉新连接 |
| **S-05** | high | `fill_bots` 无界循环 | `net/Session.java` `create_room` | `{"fill_bots":2147483647}`：`addBot(-1)` 座位满了会早返回，但**循环本身**仍跑 21 亿次（每次扫 4 个座位）→ 一条 60 字节报文占满一个核几十秒 | 钳到 0…4 |
| **S-06** | high | 发牌种子可预测 | `game/Table.java` | 原来是 `System.nanoTime()`，且每局用 `seedBase, +1, +2 …`：推出一局就能推出一整场（四家手牌 + 摸牌顺序） | 基准改成 `SecureRandom`；每局种子走 SplitMix64 终混（`mixSeed(seedBase + 局序号)`）—— 自检写死 `seedBase` 仍能复现整场 |

### 1.2 中等（已修）

| id | 问题 | 位置 | 修复 |
| --- | --- | --- | --- |
| **S-07** | `rejoin` 只给旧连接置 `closed` 标志、不真关 → 每次重连永久泄漏一个线程 + 一个 fd | `net/Session.java` | 改成 `old.close()`（`close()` 幂等，socket 真正关闭 → 阻塞在 `readLine` 的旧读线程退出） |
| **S-08** | 自己 `rejoin` 自己：`old.table = null` 会把**本连接的** table 也清掉，连接从此发不出任何动作 | `net/Session.java` | `old == this` → `bad_token` 直接拒 |
| **S-09** | `start_game` 不查房主：任何在座的人都能把四家 `ready` 全翻成 true 并补满机器人 | `net/Session.java` | 要求 `pid == hostPid`，且 `playing` 时忽略 |
| **S-10** | 牌局进行中可 `add_bot`/`remove_bot`（空出的座位会被 `join_room` 顶掉，接任者拿不到本局状态） | `net/Session.java` | `playing` 时忽略这两条命令 |
| **S-11** | 深嵌套 JSON → `StackOverflowError`（`Error` 不是 `RuntimeException`，`tryParse` 接不住）把读线程带走 | `util/Json.java` | 解析递归加**深度上限 32**；`tryParse` 额外兜 `StackOverflowError` |
| **S-12** | 食替（kuikae）判据**把牌 id 当 kind 用**：`Tiles.suit()`/减法都按 kind 定义，禁打集合又按 kind 消费 → 真禁张漏检、无谓禁张混入 | `game/Round.java` `applyMeld()` | 统一在 **kind** 上运算（`kinds[]` 数组），現物食替 + 筋替都按 kind 入集 |
| **S-13** | 不听罚符**不零和**：收付两边各自向下取整到 100，`noten_penalty=1000` 时 3 家听牌会凭空少 100 点（`rules` 是客户端传的，自检只跑过默认 3000） | `rules/Payments.java` | 改成"**先定每家收多少，再让付方严格凑出同一总量**"；默认 3000 时结果与旧实现逐位相同。补 7 条断言（含 `[1000, -400, -300, -300]` 这种余数分摊） |
| **S-14** | 客户端传的规则数值不校验：`thinking_base_ms=2147483647` → 服务端真的等约 24 天；`start_score` 溢出 → 终局顺位按负数排；`uma` 元素类型不对会抛 `ClassCastException` | `core/Rules.java` | `Rules.clampToSane()` 逐项钳制（aka/minHan/罚符/起返点/思考时长），`uma` 元素类型检查 |
| **S-15** | `addBot(seat)` 不查 `idx < 4` → `{"add_bot","seat":99}` 越界 | `game/Table.java` | 补 `idx >= 4` 判断 |
| **S-16** | 重连令牌由 `Math.random()` 派生（48 位状态、非 CSPRNG），而 `pid` 是公开的、房间号只有 4 字符 | `net/Session.java` | 改用 `SecureRandom` |
| **S-17** | `Server.sessions` 用 `CopyOnWriteArrayList`：每次 accept 整表复制 → n 个连接 O(n²) | `net/Server.java` | 改 `ConcurrentHashMap.newKeySet()` |
| **S-27** | **重连/旁观快照泄露他家振听**：`state.furiten` 原来把四家的振听原样下发。**临时振听**（放过一张能和牌的张）等价于「他听牌了」—— 而"谁在听牌"正是日麻最核心的隐藏信息。改造过的客户端只要反复 `rejoin` 刷快照就能读出来（客户端自己只读 `furiten(mySeat)` 那一项） | `game/Table.java` `stateFor()` | 只填**请求者自己**那一项，其余恒 `false`（旁观者 `seat=-1` 四项全 false）；数组长度仍保持 4，老客户端零改动。补 10 条断言 `SelfTest.stateVisibilityTests`（含旁观者、以及「`state.hand` 不含他家牌」）。**红证**：把 `stateFor` 改回旧写法，自检立刻报 3 条失败并以 1 退出。契约写进 `PROTOCOL.md` §3.8 / §3.10（新增「信息可见性：服务端绝不下发的东西」一节） |
| **S-28** | **立直后的暗杠是死代码**：`kanAllowedByRiichi` 拿 `waitKinds(seat)` 当"杠前听牌"，而自己回合里 `hand[seat]` 是 **14 张**（含刚摸到的那张）—— `Agari.waits()` 的语义是"13 张手的听牌"，对 14 张恒返回**空集**，于是"杠前听牌 == 杠后听牌"永不成立 → **立直后暗杠一次也下发不出来**（选项里没有，玩家也就当"本来就不能杠"） | `game/Round.java` `kanAllowedByRiichi(seat, kind, drawn)` | 先把刚摸到的那张从计数里减掉再求听牌（传给 `RoundOptions.kanAllowedAfterRiichi` 的计数仍是 14 张那份，它内部要 `-= 4`）。补 4 条断言（文档那 4 个例子里可判定的 2 个 + 非立直不受限） |
| **S-29** | **大明杠三道判据不一致 + 可被打崩**：① 鸣牌选项只查 `c[kind] >= 3 && canKan()`，**立直玩家照样被下发大明杠**（立直是门前状态，任何规则都不允许）；② 仲裁只查 `canKan()`；③ `applyMeld` 的 `case KAN` 直接 `picked.get(0..2)`，手里不足 3 张时 **AIOOBE** —— 异常冒到牌桌线程 → 整场半庄静默死亡（与 `pickChiTiles` 那个审计同类） | `game/Round.java` | 新增 `canDaiminkan(seat, kind)`（未立直 + 名额 + 手里正好 ≥3 张），**选项 / 仲裁 / 落地**三处共用；`applyMeld` 把"取 3 张"的校验提到任何状态改动**之前**，不满足直接返回 |
| **S-30** | **加杠被抢杠时仍翻杠宝牌**：`turnKan` 的加杠分支先 `revealKanDora()` 再判抢杠 —— 而规则是「加杠被抢和时，这次杠的宝牌指示牌**不翻开**」 | `game/Round.java` `turnKan` | 抢杠判定提到 `revealKanDora()` **之前**（抢杠成立即 `return`，那张宝牌不再翻） |
| **S-31** | **役满不加倍时把"值"的取舍写成了"役种"的取舍**：`if (f.kokushi13 && r.doubleYakuman)` 之类，于是 M.League /《天凤》下国士无双十三面被改名成「国士无双」、四暗刻单骑→「四暗刻」、纯正九莲宝灯→「九莲宝灯」。这三者（连同大四喜）是**独立役种**，不加倍只是**取值**为 1 倍 | `rules/Evaluator.java` | 役种名与取值分开：名字照报，倍数取 `r.doubleYakuman ? 2 : 1`。补断言（M.League 仍报「国士无双十三面」/「四暗刻单骑」/「纯正九莲宝灯」且 `base = 8000`） |
| **S-32** | **包牌两处缺口**：① 四杠子包牌（**M.League 独有**：由他家的舍张大明杠完成第 4 个杠）从未实现（`paoBaseFor` 里已经算了四杠子，但 `paoSeat` 永远设不上）；② 《天凤》的包牌要承担**复合后的全部役满得点**，而实现只包"被包的那一役"，没有规则开关 | `core/Rules.java`、`game/Round.java` | 新增 `pao_four_kan`（仅 mleague）/ `pao_covers_all`（仅 tenhou）两个字段；`updatePao` 里加四杠子分支（**只认大明杠**）；`paoBaseFor` 在 `paoCoversAll` 时返回 `sc.base`。补断言：大三元含暗杠的判定、四杠子包牌（M.League 有 / 天凤无 / 加杠不算）、以及**用文档 §包牌 的 M.League 例子逐位比对**（庄家荣和：包牌者 24000、放铳者 72000；天凤各 48000） |
| **S-34** | **终局顺序：先广播 `game_end` 后落盘回放** → 客户端一收到结算就给出「看本局回放」按钮，而那一刻记录还没进库，点下去只会得到"找不到该记录"。真机 L3 上实测到：`replay_list` 少了刚打完的那一场（同一次运行里 `replay_get` 却能读到，因为文件已经写下去了） | `game/Table.java` `sendGameEnd()` | 改成 **先记 + 落盘、再下发**（新增 `broadcastRaw()`：只发不记，避免重复记录）。现在「收到 `game_end` 的那一刻记录一定能取」是硬保证，写在 PROTOCOL §3.11 |
| **S-35** | **回放记录里的摸牌存了三份无用副本**：`broadcastDraw` 给四家各发一份报文，只有摸牌者那份带 `tile`，其余三份只是"谁摸了 + 剩余张数"。全记下来不仅体积翻三成以上，客户端"下一步"还要按四次才过一个操作 | `replay/ReplayRecorder.java` | 录制时丢掉不带 `tile` 的 `draw`（其余字段在摸牌者那份里都有）。实测一场东风战：1613 条 → **806 条 / 384 KB** |
| **S-36** | **牌山视图把"整局会不会被拿走"当成了"现在有没有被拿走"**：配牌 52 张没设 `takenAt`（永远画成牌背），其余按整局着色（一开始就画成已拿走）。真机截图上是"前半背面、后半全部翻开"，与当前步完全不符 | `client/model/ReplayModel.cpp` + `ui/WallView.cpp` | 配牌的 `takenAt` 取小局边界；着色一律与**当前步**比较；"已拿走 N/122"也按当前步统计。另外"小局跳转"落在**最后一条 `round_start`**（配牌完成）而不是边界条目，否则开局画面是四个空手牌 |
| **S-37** | **回放每走一步就把前几巡的弃牌动画重播一遍**（四家同时"重新打出"） | `client/ui/ReplayWindow.cpp` | 跳转一律"从小局开头重放"，而 `TableModel` 每次都会 `emit discarded` → 飞牌动画。修法：`TableModel::setSilent()` 静音开关 + `ReplayWindow::replayTo(index, animate)` —— **只有"前进一个操作"**（目标恰好是当前步的下一步）才开动画，且只对落在**最后一步里**的事件放开；其余跳转（上一步/换巡/换小局/跳巡目/点列表/换视角/载入）全程静默。⚠ 判据必须带 `target > m_cursor`：少了它，走到末尾时 `nextStepEntry()` 返回当前条，"整个小局都算最后一步" → 点一下把整局动画播完 |
| **S-38** | **牌山视图按玩家分行**：把 136 张排成"一行一家（从庄家起）"，但**副露会改变下一个摸牌的人**，所以四家的牌在序列里根本不连续 —— 该排法是错的（用户直接点出） | `client/ui/WallView.{h,cpp}` | 改成**一条抓牌顺序序列**：每列 4 张、自上而下读，每 4 列留一个阅读空隙（与玩家无关）；末尾 14 张王牌仍在序列里，只换底色。归属改用**每张牌底部的四色细线**表达（四家混排也认得出）。旧版"岭上/表宝牌/里宝/每行一家"那几个 i18n key 一并废弃 |
| **S-39** | **「看本局回放」在每一小局结算都出现**：牌局未结束时记录**还没落盘**（服务端终局才写），按钮点下去只会查不到；用户也明确要求"未结束时不该提供回放按钮" | `client/ui/MainWindow.{h,cpp}` | `showResultDialog(..., bool offerReplay)` 显式开关，**只有 `game_end` 那条传 true**；其余（`agari` / `ryuukyoku`）即使手上已有 `m_replay_id` 也不下发按钮 |
| **S-40** | **回放里没有小局结算、操作记录也不分小局**：一场半庄上千步挤在一条列表里；每小局结束后无从查看该局结算（只有终局顺位） | `client/model/ReplayModel.{h,cpp}` + `ui/ReplayWindow.cpp` | 新增 `roundResultEntry(round)`（定位该小局的 `agari`/`ryuukyoku`）；右侧操作列表**按小局切割**（只装当前小局，标题写明是小局几）；新增「本局结算」按钮，且**播放跨小局时自动弹出并暂停播放**——结算弹窗在回放里**不调 `startCountdown()`**，必须等玩家点确认才进下一局 |
| **S-41** | **回放看不清别家手牌、切视角要点下拉框**：回放天然是上帝视角（`walls` + 四家 `round_start`/`draw` 都在），但界面只画自家 | `client/ui/TableView.cpp` + `ui/ReplayWindow.cpp` | 新增 `TableView::setGodHands(hands, drawn)`（别家也画牌面，摸牌仍单列一格，数据来自 `ReplayModel::godState()` 新增的 `drawn[4]`）+ 工具栏「显示他家手牌」切换键；`TableView::seatClicked(seat)` 让**点名牌就能切视角**（与下拉框同一条路径，两边同步） |
| **S-42** | **「显示他家手牌」一开，别家的张数、理牌、副露去向、出牌动画起点全不对**：① `ReplayModel::buildGod` 的副露扣牌写成"凡等于 `called_tile` 就跳过"，而碰的三张牌码**完全相同** → 一张都不扣（副露的牌同时留在手里）；加杠同理会把四张全扣掉。② 别家手牌用 `QStringList::sort()`（字典序）→ 赤五 `0m` 被甩到 `1m` 前面，看着像没理牌。③ 上帝手牌只存在 `TableView` 里，而**布局**按 `TableModel::concealedCount()`（13−3×副露）算 → 张数与画出来的牌各说各话。④ 别家出牌动画走"随机挑一格"的兜底，不是这张牌待着的位置 | `client/model/ReplayModel.cpp` + `model/TableModel.{h,cpp}` + `model/Tile.{h,cpp}` + `ui/TableLayout.cpp` + `ui/TableView.{h,cpp}` | ① 扣牌改成与自家手牌**同一套算法**（`TableModel` 是权威实现）：吃/碰/大明杠先摘掉**一张** `called_tile`，暗杠不摘，加杠只扣第 4 张。② 抽出 `mj::sortTiles()`（`TableModel::sortHand()` 也改调它），上帝手牌在 `setGodHand()` 里就理好牌。③ 上帝手牌**存进 `TableModel`**（`setGodHand/clearGodHands`），`concealedCount()`、`seatHasDrawnTile()`、`godHand()` 全部同源 → 布局与绘制不可能不一致。④ 手切动画起点改用**理牌后**的格子下标（`handSlotLocal`），摸切仍是摸牌槽 |
| **S-43** | **上面 ④ 的修法在真机上根本没生效**：`TableModel::reset()` 里 `clearGodHands()`，而回放**每一次跳转都是 `reset()` + 从头重放一遍**（`ReplayWindow::replayTo`）→ 重放到"出牌"那一步时模型里已经没有四家暗牌 → 动画仍然退回随机兜底（用户第二次报障："仍然是从手牌中随机一个位置打出"）。⚠ **当时的自检照样全绿**：它自己 `setGodHand()` 再 `applyEvent()`，**绕过了 `replayTo`** | `client/model/TableModel.cpp` + `ui/ReplayWindow.{h,cpp}` + `ui/TableView.{h,cpp}` | ① `reset()` **不再清**上帝手牌 —— 它是"事件流之外"的数据，保留上一帧那份正好就是**出牌前**的手牌（换记录时才由 `ReplayWindow` 显式清）。② 「显示他家手牌」开关改成只决定**画不画牌面**（`TableView::setGodVisible`），真实暗牌**始终**交给模型 —— 布局与动画起点都要按真实张数算，这与"给不给玩家看牌面"是两回事。③ 只要手牌已知（自家用上一帧画出来的那份、别家用模型里的真实暗牌）就**一律**从这张牌真正待着的格子起飞，同码多张取第一张（不再随机）。④ 自检新增**真路径**回归：`ReplayWindow::loadForTest()` + `nextOpForTest()`（与点「下一步」同一个槽）走完 `replayTo` 再断言起点；**红证**：把 `clearGodHands()` 加回 `reset()`，自检立刻报「出牌动画没按真实手牌定位 / 7m 应起飞自第 6 格，实际 10」 |

| **S-44** | **出牌动画轻微卡顿**（用户观察）：`QSvgRenderer::render()` **每次调用都重新栅格化矢量**，而动画 16ms 一帧要重画整张桌子（四家手牌 + 四家牌河 + 副露 + 宝牌行，一百多张牌；牌山窗口 136 张）→ 实测 **28~32 ms/帧**，超了 60fps 的 16.7ms 预算，于是掉帧 | `client/ui/TileRenderer.{h,cpp}` | 按「牌码 + 目标**设备像素**尺寸」缓存栅格化好的位图（`pixCache()`，上限 512 张、满了整体换代），每帧只 blit。三个必须做对的点：① 键用**设备像素**（`deviceTransform()` 取缩放），高 DPI 下才不会拿到低分辨率图；② **旋转不进键** —— 位图一律正着栅格化，由调用方画笔的旋转变换整体转过去（90° 整数倍无损）；③ 贴回去要用**设备像素比**（`setDevicePixelRatio` + `drawPixmap(QPointF, pm)`），**不能** `drawPixmap(rect, pm, srcRect)` —— 后者按逻辑尺寸再缩放一次、差零点几像素就重采样。实测 **28.45 → 8.43 ms/帧（3.4×）**，缓存只占 15 张。自检里做**同机同轮 A/B**（`setAssetCacheEnabledForTest(false)` 复现优化前），并断言"缓存热了以后一帧零未命中"；`MAHJONG_NO_TILE_CACHE=1` 可在真机对照 |

| **S-45** | **振听三条全都不成立**（对照 `docs/日本麻将.md` §振听 逐条核出来的）：① **舍张振听**读的是 `discards[]`，而被他家吃碰杠走的舍牌**已经不在牌河里**（鸣牌是"移动"不是"复制"）→ 打出去被人碰走的听牌张，事后又能荣和回去；规则明文要求「包括…后来被他家吃、碰或杠走的舍牌」。② **同巡振听**（见他家打出能和牌却没和 → 本巡之内不能再荣和）—— `furitenTemp` **只有清除、从来没人置位**。③ **立直振听**（立直状态下见逃荣和**或见逃自摸** → 持续到本局结束）—— `furitenPerm` 同样从未置位。合起来：**见逃与振听博弈整个不存在**，而它是日麻防守的核心 | `game/Round.java` | ① 新增"曾经打出过"的账 `discardKindsEver[4][34]`，出牌走**唯一**记账点 `recordDiscard()`（牌河 + 那份账 + 横置），`ownDiscardKinds()` 改为读它；② `claimPhase` 仲裁后逐座位记账"被给过 `ron` 却最终没和"（含超时未答）→ `furitenTemp[s]`，立直家同时 `furitenPerm[s]`；③ 出牌段"能手牌和却没和"（`turnOptions` 给了 `tsumo` 而最终没和）→ 立直家 `furitenPerm[turn]`。回归：`SelfTest.furitenRuleTests`（① 含"改手牌后解除"；②③ 用"见逃策略"跑整场检查不变式，并断言**确实造出过**见逃机会 —— `[覆盖] 12 场单局、1007 次询问，见逃荣和 10 次、立直见逃自摸 1 次`）。**红证**：把 `ownDiscardKinds` 改回"只看牌河"，自检**恰好红那两条**新断言。⚠ 顺带发现两条**老用例把 bug 当成了规格**（直接往 `discards[]` 里塞牌来造振听），已改成走生产的记账点 `debugPushDiscard`。已知偏差（无役见逃不触发同巡振听 / 已在振听里的立直家不记立直振听）写在 `docs/DESIGN.md`「振听：三种都要记」 |

### 1.3 排期完成项（原「已知未修」）
| id | 问题 | 处理 |
| --- | --- | --- |
| **S-18** | 四杠散了的时序（审计怀疑"第 4 次杠的岭上自摸会抢在流局前成立"） | **校勘规则后确认原实现是对的**，并抽出具名判据 `Round.fourKanAbortNow()` + 4 条真值表断言（见 §1.5） |
| **S-20** | `claimOptions` 在同一舍张上算两次；`waitKinds`（34 次向听 DFS）每次重算，而一次舍张要问 5~8 次 | 已修：同一舍张的选项只算一次（`optsBySeat`）；`waitKinds` 按「暗牌计数 + 副露数」的**精确**摘要记忆化 —— 内容一变键就变，**不需要手工失效**，因此不会出现"忘了在某处加一行导致缓存过期"的经典 bug |
| **S-21** | `rules.aka = 0` 仍会发赤五、仍记赤宝牌番数（`akaKinds()` 全仓无调用者） | 已修：`Wall` 现在接收 `Rules`，`aka = 0` 时**在发牌阶段就把赤五换成普通五**（`stripRedFives`）→ "看到的"与"计分的"一致。补 4 条断言，含「每种牌恒 4 张」（换掉赤五不能改变牌张构成） |
| **S-19** | 两个方向的队列无界、聊天无频率限制、观战无人数上限 | 已修：`Session.outbox` → `ArrayBlockingQueue(512)`、`Table.responses` → `ArrayBlockingQueue(1024)`，**溢出丢弃并记日志**（绝不把这条放大路径接到堆上）；聊天 5 条 / 5 秒闸；每房观战上限 8 |

### 1.4 仍未做（记录在案）

| id | 严重度 | 问题 | 为什么先不改 |
| --- | --- | --- | --- |
| S-22 | low | `Json.i()` 静默截断越界值；`\u` 解析接受 `+/-`、允许落单代理；重复键后者胜 | 都是畸形输入路径；唯一能到数组下标的地方（`addBot`）已修 |
| S-23 | low | `round_start` 的 `cans.kyuushu` 硬编码 `false`，而 `turnOptions` 会下发 `kyuushu` | 该字段协议标注"仅供参考"，改动零风险但优先级最低 |
| S-24 | nit | `Bot.debugAlwaysKan`、`YakuCodes.misses` 等**静态可变**自检钩子在并行测试下不安全 | 要引入可注入的 `DebugProbe`，属结构性改动，见 §3.2 |
| S-25 | nit | `debugSetRinshanUsed` 在 `Round` 与 `Wall` 各有一个入口 | 是自检门面，保留；不必再扩散 |
| S-26 | medium | `Rules.aka = 4`（两张赤五筒）无法表达 | 牌 id 编码把赤五固定在 copy 0，要支持得先改编码（会动牌码↔id 的全部映射），收益极低 |

### 1.5 规则校勘（对照 `docs/日本麻将.md`）

> 规则原文已于 **2026-09-14 重新抓取**（该版逐节补上了《雀魂》《天凤》与 **M.League** 的差异说明，
> 见其开头「本页面介绍常见的日本麻将规则，并在有差异的地方补充…」）。下表的口径引用改为**按节名**
> 而不是行号 —— 上一次抓取后就因为行号整体位移而指错了地方。

| 规则 | 原文口径（§节名） | 代码现状 |
| --- | --- | --- |
| **四杠散了** | §四杠散了：「一局中**由 2 名及以上的玩家开杠了 4 次**，且**第 4 次杠后取得的岭上牌没有放铳**」→ 强制流局；**同一人**开满 4 次则成立四杠子、不流局；「岭上开花 / 被抢杠 / 岭上牌放铳」三种情况**都不流局** | ✅ 一致。判据 = `kanCount == 4 && !allKansByOnePlayer()`；三种豁免由**和了路径先 `return`** 天然满足（判据只在"那张牌已经落地且没人因它和牌"时被问到）。**审计 F14 属误报**。⚠ M.League 明文「不采用中途流局」，所以这套判据只在 `rules.four_kan_abort` 为真（《天凤》/《雀魂》预设）时生效 |
| **食替（kuikae）** | §食替：分**现物食替**（打出与所吃/所碰相同的那张）与**筋食替**（打出所吃顺子另一边的牌），多数规则禁止 | ✅ 一致：`applyMeld` 里 `forbiddenDiscard` = 現物 + 按 **kind** 算出的筋替（S-12 修掉了"拿 id 当 kind 用"） |
| **不听罚符** | §荒牌流局：共计 3000：1 家听 → 另 3 家各付 1000；2 家听 → 各付 1500；3 家听 → 剩 1 家付 3000；4 家全听 → 不罚 | ✅ 默认值**逐位一致**；且任意非默认罚符值都严格零和（S-13） |
| **赤宝牌** | §赤宝牌：四人麻将通常 3 张（0m 0s 0p）或 4 张（0m 0s 0p 0p）；M.League 采用 3 张 | ✅ `aka = 0 / 3` 均已实现（S-21）；`aka = 4` 受 id 编码限制仍按 3 处理，见 S-26 与 AGENTS §8 |
| **两倍役满** | §役满 / §两倍役满：「大四喜、国士无双十三面、四暗刻单骑、纯正九莲宝灯这 4 种…《雀魂》中会作为两倍役满…《天凤》和 M.League 中，这 4 种均计**一倍**役满；不同役满仍可以复合」 | ✅ `rules.double_yakuman`（majsoul true / mleague、tenhou false）；**役种名与取值分开**（S-31）。文档给出的复合例子（四杠子+四暗刻单骑 = 2 倍；字一色+大四喜+四杠子+四暗刻单骑 = 4 倍）由"逐役累加倍数"自然成立 |
| **累计役满** | §役满：「《雀魂》《天凤》采用累计役满，**M.League 则以三倍满为普通役的上限**」；§打点表前的说明：「M.League 将 3 番 60 符、4 番 30 符计为满贯，13 番及以上普通役仍计三倍满」 | ✅ `rules.kazoe_yakuman` + `rules.kiriage_mangan`；`Evaluator` 的 ≥13 番分支按开关给 8000/"累计役满" 或 6000/"三倍满"，基本点 1920 的格子按开关切上满贯 |
| **连风雀头符** | §符：「《雀魂》《天凤》中自风牌与场风牌可以叠加，即连风牌计 4 符；**M.League 的连风牌计 2 符**」 | ✅ `rules.doubleWindPairFu`（mleague 2 / 其余 4）。断言挑在**进位线**上（4 符→50 / 2 符→40），否则两种规则会进位成同一个值、测不出来 |
| **立直条件** | §立直：「《雀魂》《天凤》中，如果自己的点数不足 1000 点，或剩余可摸的牌不到 4 张，则不能立直。M.League 允许持点不足 1000 点或没有下一次摸牌机会时立直，**但摸到海底牌后不再接受立直**」 | ✅ `rules.riichi_min_score` / `riichi_min_tiles_left` / `riichi_no_haitei`，判据集中在 `Round.riichiAllowed()`（`canRiichiAny`/`canRiichi` 共用）。断言覆盖四种组合 |
| **立直后暗杠** | §立直：「立直后虽然可以暗杠，但暗杠不能改变所听的牌。《雀魂》《天凤》按所听的牌种是否变化来判定，并禁止送杠；**M.League 还要求面子构成不变**，但允许役种增减」，并给出 4 个例子（4m5m5m5m…暗杠 5m 不行；7m7m2p2p2p…暗杠 2p/3p/4p 天凤可、M.League 不可；1m1m1m2m2m3m3m3m8p8p8p6z6z 暗杠 1m/3m 不可而**暗杠 8p 可以**；5m5m5m0m6m7m…摸 8m 后暗杠 5m 是送杠） | ✅ `rules.ankan_keeps_shape` + `Round.ankanKeepsShape()`（保守判据：这 4 张牌在手里没有同花色 ±1/±2 的邻居）+ `RoundOptions.kanAllowedAfterRiichi`（听牌不变，任何规则都查）。**S-28 修掉了"这套判据其实一次也没被跑到"** |
| **头跳 / 三家和了** | §头跳：「《雀魂》中没有头跳规则，也没有三家和了流局。《天凤》允许二家和了，三家和了则流局；**M.League 采用头跳**」 | ⚠ **半对**：`head_bump` 已实现（mleague 开 / 其余关）；但 **`sancha_abort` 是死字段**（只有声明 / 预设 / 收发，游戏逻辑零消费者）→ 天凤预设下三家同时荣和会**三家全额结算**而不是流局。见 **S-47** |
| **包牌** | §包牌：「《雀魂》《天凤》对大三元、大四喜采用包牌；**M.League 还对四杠子采用包牌**，即由他家的舍牌使玩家以大明杠完成第 4 个杠。M.League 判定大三元、大四喜的包牌时，也**计入已经公开的暗杠**」「M.League 的包牌只涉及被包的役满部分，《天凤》则涉及复合后的全部役满得点」「触发包牌规则时，包牌者需要支付所有的本场棒」 | ✅ 大三元/大四喜的判定本来就遍历全部副露（含暗杠）；本场棒由包牌者支付（`Payments` 既有）。**S-32 补齐了四杠子包牌与"只包一役 / 包全部"的开关** |
| **精算点数** | §精算点数：`精算点数 = (点数 − 返点)/1000 + 马点 + 头名赏（仅1位）`，`头名赏 = (返点 − 原点)/1000 × 人数`；M.League 例子 53600/28600/20000/−2200 → +73.6/+8.6/−20/−62.2；《雀魂》同点数 → +43.6/+8.6/−10/−42.2；**同点时《天凤》按起家东南西北顺序、M.League 平分对应名次的加点** | ✅ 抽成纯函数 `RoundScoring.settle()`，两个文档例子逐位断言（`SelfTest.mleagueRulesTests`）；同点平分由 `rules.tie_split_point` 控制。**S-33 修掉了《雀魂》把"精算基准"取成 30000 的问题**（应为 25000 → 无头名赏） |
| **无击飞 / 无和了止 / 无西入 / 无流局满贯** | §终局：「M.League 不使用击飞规则，也没有西入等延长战，比赛会持续到南 4 局庄家轮庄为止」；§流局满贯：「M.League 不采用流局满贯」 | ✅ 四个开关全关（`tobi` / `agariyame` / `westExtension` / `nagashiMangan`），M.League 预设即"打到南 4 局庄家轮庄" |
| **杠宝牌的翻开时机** | §杠宝牌：「M.League 在杠成立后立即翻开；《天凤》暗杠立即翻开，明杠、加杠在随后打牌时或继续摸岭上牌之前翻开。**加杠被抢和时，这次杠的宝牌指示牌不翻开**」 | ✅ 统一"杠成立即翻"（M.League 口径）。加杠被抢杠不翻由 **S-30** 修正。⚠ 《天凤》的"明杠/加杠延后翻"未区分：只影响信息公开时机，不影响算分 |

公开资料（与原文口径一致）：[四槓算了（麻將 Wiki）](https://mahjong.fandom.com/zh/wiki/%E5%9B%9B%E6%A7%93%E7%AE%97%E4%BA%86)、
[流局（萌娘百科）](http://mzh.moegirl.tw/%E6%B5%81%E5%B1%80)、[四開槓（麻雀ローカルルールWiki）](https://w.atwiki.jp/mahjlocal/pages/65.html)。

### 1.6 明确验证过**没有**问题的地方（免得后人重复怀疑）

- **观战者拿不到暗牌**：`stateFor(-1)` 手牌为空；`round_start`/`draw` 是**逐座位** `send` 的，从不广播。
- **默认规则下授受守恒**：`Payments.compute` 按构造零和（含包牌分支）。
- **杠的四道闸门**都走 `canKan()`；被拒的杠退回默认摸切（§2.3 的旧修复是完整的）。
- **`menzen` 初值 / 庄家第 14 张 / 横置顺延** 三条历史 bug 均已确认仍然正确。
- **非法 UTF-8**：`InputStreamReader` 用 REPLACE 解码，畸形字节退化成 `bad_json`，没有异常路径。

---

## 1.7 规则覆盖审计（2026-09 第二轮，全面对照 `docs/日本麻将.md`）

**怎么做的**：把规则原文按「役种 / 流程 / 算分」三块，各自与实现逐条对照（役种列表 §865-1417 的
**55 个役** + 流程 11 个类目 + 符数/点数/精算/测试覆盖）。每条都带 `file:line` 证据。
结论：**没有整条缺失的役**（55 个役全部有判定），缺口集中在下面这些**口径不一致与死字段**上。

> ⚠ 已知取舍（`DESIGN.md`「不做 / 妥协」）**不算缺口**，本轮逐条确认过：
> M.League 程序性判罚/诈立、形式听牌"自家已用尽等待牌"不区分、雀魂的国士抢暗杠与天和国士视作十三面、
> 天凤的明杠/加杠杠宝牌延后翻、`Bot` 从不开杠。

### 1.7.1 真缺口（未实现 / 死字段）—— 按影响排序

| id | 严重度 | 问题 | 证据 | 影响 |
| --- | --- | --- | --- | --- |
| **S-46** | **high** | **流局时本场数不 +1**：中途流局不加（`if (!res.abortive) honba++`），**荒牌流局庄家不听 → `honba = 0`**（应「+1 并轮庄」） | 文档 §连庄/§流局（L110/L534/L623「流局轮庄时本场数仍会增加」「中途流局…本场数增加」）；`Table.java:1029-1044`；`RoundScoring.java:80-86` 的 javadoc **自己写的是「本场 +1」**（自相矛盾） | **默认 M.League 预设就有**：每次流局之后所有和牌少算 300 点/本场（自摸 100/家），界面本场数也错。修法两行 |
| **S-47** | **high** | **三家和了流局未实现**：`Rules.sanchaAbort` 全仓**零消费者**（只在 `Rules.java:21/134/182/272` 声明、预设、收发），`Round.claimPhase` 只在 `headBump` 时取头跳 | 文档 §头跳（L526-534）；`Round.java:1571-1592` | 天凤预设下三家同时荣和会**三家全额结算**（本场/连庄/点数全错）。修法：`ronSeats.size()>=3 && rules.sanchaAbort` → `abort("三家和了")` + 断言。**顺带修掉 §1.5 里那条误标的 ✅** |
| **S-48** | **high** | **燕返（立直宣言牌放铳）时立直不成立、不该收那 1000 点**：`doRiichi()` 在宣言牌**落地之前**就扣 1000 + `sticks++`，随后 `agariRon` 把供託全给和牌者 | 文档 §立直（**L893**：「立直宣言牌放铳（燕返）…认为立直不成立，不加收 1000 点。《天凤》和 M.League 也采用相同的规定」）；`Round.java:332-334`、`1255-1266`、`2299-2301` | 三套预设都错、局面常见（每次立直宣言被荣和即触发）：放铳者白扣 1000、和牌者白收 1000。与 `tsubame` 古役语义纠缠，越晚修越容易两边都改错 |
| **S-49** | **high** | **食替（kuikae）只在选项里过滤、服务端不校验**：生成端有（現物 + 筋替，S-12 已按 kind 修），**出牌段只查「在手里 / 立直只摸切」** | `Round.java:325-330`（缺 `forbiddenDiscard` 判据）；`forbiddenDiscard` 只被 `RoundOptions.java:50`、`defaultDiscardId:1062,1069` 消费；`PROTOCOL.md` §7 明文「`chi`：…违反食替」「非法动作不得改变状态」 | **服务端权威被绕开**：改造客户端（或一条手工报文）就能稳定"吃 3m 打 6m / 打现物"。与 S-01/S-02 同一类"把选项列表当安全边界"的错误 |
| **S-50** | medium | **多家荣和时本场加点归每一家**：每个 winner 各收 `300×本场` | 文档 §和牌（L794：「《天凤》二家和了时，立直棒和**本场加点**均由距放铳者最近的和牌者取得」）；`Payments.java:106` + `Round.java:2300` | 天凤/雀魂预设（`headBump=false`）下双响+本场时**放铳者多付 300×本场×(赢家数−1)**，本场归属错（总额仍守恒）。修法：只让 `winners.get(0)` 收本场 |
| **S-51** | medium | **摸到海底牌后仍可开杠**：`turnOptions` 的 `kan` 闸门只有 `canKan()`，无「海底」判据（大明杠在河底已挡） | 文档 §副露（L296）「不可以吃、碰、杠河底牌，**摸到海底牌后也不可以开杠**」；`Round.java:943-967`、`turnKan:1271-1358` | 海底牌后还能暗杠/加杠 → 接着摸岭上（可用岭上开花收尾），并改动「一局 4 次杠」的账 |
| **S-52** | low | **两个被包役满、责任副露分属两人**：`paoSeat[4]` 只有单槽 | `Round.java:62`、`updatePao:2026-2067` | 复合包牌（如大三元+大四喜分别由两人喂成）只会记一个人。文档未明确 → 疑似欠缺而非取舍 |

### 1.7.2 口径不一致（实现与文档不同侧）

| id | 点 | 文档 | 实现 | 备注 |
| --- | --- | --- | --- | --- |
| **S-53** | **和了止** | §终局（L116）：天凤是「All Last 庄家达到**一位必要点数**且为 1 位时，**和了止 + 听牌止**」 | `Table.java:1034-1042`：只做和了止（`res.agari`），**无听牌止**、也**不查一位必要点数**（只看 `scores[dealer] >= top`） | tenhou/majsoul 预设下行为不符 |
| **S-54** | **西入** | §终局（L145）：半庄最多到西 4 局，**大部分规则没有北风场，因而也没有北入** | `Table.java:1059` `nw <= 3` 允许 `nw==3`（北场）→ 西 4 轮庄后可进**北 1 局**；东风战 `lastWind()=0` 也会被允许进到**西场** | 三套预设 `westExtension` 全关 → **休眠缺陷**，但开关一开就错 |
| **S-55** | **流局满贯的连庄判据** | §流局满贯（L1139）：天凤口径「庄家是否连庄仍按**是否听牌**判断」 | `Round.java:2408` + `RoundScoring.nagashiBy`：按「成立者**含庄家**」 | 仅 tenhou/majsoul 生效（M.League 关闭）。⚠ `SelfTest:1870` 把现行为写成了断言 → 改行为要同时改断言 |
| **S-56** | **食い平和（副露）自摸的符数** | 文档只规定荣和 30 符（L665），**自摸未写** | 实现 30 符（`Evaluator.java:656-658 → 696-699`）；《天凤》《雀魂》实际是 **20 符** | 需要定口径（要么补文档、要么按 20 符修 Evaluator） |
| **S-57** | **抢杠的两处偏差** | §抢杠：可与**一发**复合；头跳/多家和了同样适用 | ① `clearIppatsu()` 跑在抢杠判定**之前** → 抢杠丢一发（少 1 番，`Round.java:1337` vs `1342-1355`）；② 抢杠**自动成立、不给见逃机会**，且多家抢杠**不受 `head_bump` 约束**（`agariRon:2280-2311`） | ① 是算分错误（小）；② 是行为口径 |
| **S-58** | **四杠散了与鸣牌时序** | §四杠散了：成立即强制流局 | `Round.java:380-385`：第 4 次杠的岭上舍张被吃/碰时**先把鸣牌落地**（广播 + 移牌）再 abort | 客户端/回放会看到"碰完立刻流局"。另 `Round.java:220-222`「岭上余 0 → abort("四杠散了")」是**死代码且理由码不对** |
| **S-59** | **天和/地不与国士复合** | §役满（L1239）「不同的役满仍可以复合」 | `Evaluator.java:166-175` 对 `TYPE_KOKUSHI` **提前 return**，丢掉 `tenhou/chiihou` | 概率天文数字级，登记即可 |
| **S-60** | **两条默认关闭的古役口径** | §古役：十二落抬**含明杠**、只排除暗杠（L1301-1305）；一色三顺**副露减一番**（L1325-1349） | ① `Evaluator.java:518-527`：任何杠都置 `allOpenRuns=false`（明杠被误排除）；② `Evaluator.java:395,411-412`：整块在 `menzen` 内 → **副露型拿不到** | 都只在 `koyaku=true` 时可见 |
| **S-61** | **大七星 / 一筒摸月·九筒捞鱼 的叠加** | 文档只说"计 5 番，故为满贯"（L1351-1365）、未写大七星与字一色的取代关系（L1411-1417） | `Evaluator.java:528-533` 加 5 番 → 与海底/河底 1 番叠加 = **6 番（跳满）**；大七星与字一色叠加 = **3 倍役满** | 存疑，需按原文再定一次口径 |

### 1.7.3 契约 / 文档层面的不一致（我另行复核过）

| id | 问题 | 证据 |
| --- | --- | --- |
| **S-62** | **`koyaku` 不在 `PROTOCOL.md` §5 的 rules 字段表里**（38/39 个字段有文档，只漏它）→ 脚本客户端不知道能传古役开关 | 机械化对拍 `Rules` 39 个字段 vs §5 的表格/JSON 例子；`Rules.java:32`、`fromJson:193`、`toJson:283` 都有 |
| **S-63** | **默认思考时间有两套口径**：`Rules` 字段初值 = base **15000**/bank **0**（即 `0+15`），`applyPreset` 明确「不改思考时间」；而客户端大厅默认选 **`20+5`**（`LobbyDialog.cpp:100 setCurrentIndex(0)`），`PROTOCOL.md` §5.1 又把 `20+5` 标成"（默认）" | `Rules.java:50,55`、`Rules.java:104-105`、`client/src/ui/LobbyDialog.cpp:89,100`、`PROTOCOL.md:672`。后果：**纯机器人牌桌（自检 / `--selfplay`）与真人建房走的是不同钟模型** |
| **S-64** | **`Settlement.uma/oka` 的 javadoc 说"按座位索引"，实际按名次**（`Table.java:1130` 也按名次读）→ 今天不误算，但 `st.uma[seat]` 是坑 | `RoundScoring.java:152-163` vs `191,204-210` |
| **S-65** | **终局余棒归 1 位**是自定义口径（天凤是供託不返还）：代码自称"保证点数守恒"，但 `DESIGN.md` 的取舍清单里没登记 | `Table.java:1074-1085` |
| **S-66** | `DESIGN.md` 把"形式听牌不区分《天凤》/M.League"描述成「更接近 M.League 的**宽松**侧」——**方向反了**：M.League 是**连同副露**计算（更严的一侧） | `DESIGN.md`「不做/妥协」第 2 条 vs 文档 §形式听牌（L613） |
| **S-67** | **验证过的"没有问题"**（免得后人重复怀疑）：`tonpuu` 拼写在 PROTOCOL / 客户端 / 全部 `tools/*.mjs` 里**完全一致**（曾怀疑 `tonpuu` vs `tonpu` 不匹配 → 不存在）；`大数邻/大车轮/大竹林` **确实已实现**（`Evaluator.java:278` 的三元表达式，机械扫 `Yaku.normal/yakuman("…")` 会漏掉它们） | 反例排除 |

### 1.8 测试覆盖盲区（算分侧，`SelfTest.java`）

| id | 盲区 | 证据 | 为什么要紧 |
| --- | --- | --- | --- |
| **S-68** | **「完整打点表逐格比对」是自我镜像**：`scoreTableTests` 用 `fake()` 里 `base()` 的**副本**算基本点再喂 `Payments` → `Evaluator` 的 **6〜12 番档（跳满/倍满/三倍满）从未被真实手牌断言过** | `SelfTest.java:1907-1938` | 副本与实现漂移测不出来；"逐格比对"名不副实 |
| **S-69** | **一条断言被注释吞掉**：`SelfTest.java:1945` 整行是注释，行内字面 `` `n `` 把 `eq("闲1番60符荣", …, 2000)` 一起吞进 `//` → **闲家 1 番 60 符根本没测**（注释还写"文档 2900"，现文档 L746 是 2000） | `SelfTest.java:1945` | 1 番 60 符整格缺测 |
| **S-70** | **带副露的符断言全缺**：`evalCtx` 恒传空 melds → 明刻/明杠/加杠符、食い平和 30 符、副露+自摸符、荣和补刻按明刻，**全部零断言** | `SelfTest.java:201` | 副露是常见路径 |
| **S-71** | 逐格覆盖偏门前荣和：**自摸只 6 格**（闲 20符2番/40符3番/30符5番，庄 20符2番/30符4番/30符13番）；25 符自摸全缺、20 符自摸 3/4 番缺、1〜3 番 30〜50 符自摸缺、6〜12 番自摸缺 | `SelfTest.scoreTableTests` | 实战最常见的自摸档位反而没测 |
| **S-72** | 其他零断言：`minHan ≥ 2`（二番缚）、包牌 + 本场、**全部多家荣和 / 头跳行为**（只有 flag 与 `RoundScoring.winBy` 纯函数）、三家和了路径、精算的三人同点与负头名赏边角 | `SelfTest.java:1040,2081-2086` 等 | 头跳/多响是刚发现的 S-50 所在区域 |

> **本轮已修（批次二）**：S-68 / S-69 / S-70 / S-71 全部闭合，S-72 部分闭合（`minHan≥2`、包牌+本场已补；
> **多家荣和 / 头跳的行为断言留到批次一** —— 它要跟 S-50 的修复一起做，因为 `Payments` 没有"多赢家"入口、
> 多响的拆分在 `Round.agariRon` 里，且要做成非空转就得先对种子/局面做定向构造）。
> 具体做法：`Evaluator.basePoints(han, fu, rules)` 成为**唯一**的档位映射（打点表不再抄副本、
> 实局判定也调它）；打点表显式声明用《天凤》规则集（累计役满开、切上满贯关），并新增
> **真实手牌**覆盖 7/9/12/14 番四档（跳满/倍满/三倍满/累计役满，含 M.League 三倍满对照）；
> 新增 `fuOpenTests()` 13 条**带副露**的符断言（明刻/暗刻/明杠/暗杠 × 中张/幺九、食い平和 30 符、
> 荣和补刻按明刻 + 其自摸对照、副露+自摸符卡在进位线上）；补齐 ~24 格**自摸**（1〜4 番各符、
> 25/20 符、满贯以上各档）与 `minHan≥2`、包牌+本场。
> 自检 709 → **774 项**（L1 用时 96.8 → 104.5 秒）。

### 1.9 本轮审计的取证方式（可复核）

- 三个**只读**子审计（役种 / 流程 / 算分），要求每条结论带「文档行号 + 实现 `file:line`」，
  并明确区分「未实现」与「已声明取舍」；
- 我另行复核过：**S-47**（全仓 grep `sanchaAbort` → 只有 Rules 与一条预设断言）、
  **S-48 / S-55** 的文档原文（L893 / L1139）、**S-62 / S-63**（机械化对拍 `Rules` ↔ PROTOCOL ↔ 客户端）、
  **S-67** 的两条反例（`tonpuu` 拼写、三个古役其实已实现 → 排除误报）；
- 一次性分析脚本（未进仓库）：`tmp/rules-audit.mjs`（字段四路径）、`tmp/protocol-rules-diff.mjs`
  （契约 ↔ 实现）、`tmp/yaku-diff.mjs`（文档役种 ↔ 码表）、`tmp/yaku-producer.mjs`（码表 ↔ 生产者，**有漏报，仅作参考**）。

---

## 2. 客户端

### 2.1 高 / 中（已修）

| id | 严重度 | 问题 | 位置 | 修复 |
| --- | --- | --- | --- | --- |
| **C-01** | high | 每次 `draw` 事件都往暗牌里追加一张、**无上限** → 对端连发 `draw` 就能让内存与每帧绘制无限增长 | `model/TableModel.cpp` | 按「手牌 + 摸牌位 ≤ 13」判据收下（**不是** `m_hand.size() < 13` —— 正常一巡本来就是手牌 13 张再摸第 14 张，写死会把那张吞掉）；`round_start.hand` 也钳到 14 |
| **C-02** | medium | 服务端来的数组一律不过长度校验（手牌/宝牌/副露/牌河/询问选项）→ 一条 1 MB 的 `round_start` 就能让首帧卡死、之后每帧持续 100% CPU（每个畸形牌码还会做两次文件探测 + 永久占一条缓存） | `net/Protocol.cpp` 及各调用点 | 在**边界**设上限：手牌 14 / 宝牌 5 / 一副副露 4 张 / 每座 4 副 / 每家牌河 60 / 询问选项 16，超出截断 + `qWarning` |
| **C-03** | medium | 结算界面把**玩家名**直接插进 HTML（`setHtml` 渲染）→ 改名成 `<img src="file:…">` 可注入标记、并让 `QTextDocument` 去读本地文件；聊天早已 `toHtmlEscaped()`，这条路没有 | `ui/ResultDialog.cpp`（另 `MainWindow` 分数面板同一问题） | 座位名统一经 `seatName()` 转义后入 HTML |
| **C-04** | medium | 每个服务端事件把「听牌」重算至多 4 次（`changed→updateScorePanel` 与 `onEvent` 末尾各一次；`isFuritenLocal()` 内部又调一次 `waits()`，而 `isWinningForm` 每次堆拷贝一份计数数组） | `ui/MainWindow.cpp`、`model/TableModel.cpp` | 去掉重复触发，每次刷新只算一次 `waits()` 并复用 |
| **C-05** | medium | 拆包是 O(n²)：每切一行都 `m_buffer.remove(0, n)` 把剩余缓冲整体前移 → 对端一次塞几千条短行就能让 GUI 线程长时间忙在 memmove | `net/NetClient.cpp` | 改成扫描下标累计，循环结束后**只 remove 一次** |
| **C-06** | low | `--lang` / 环境变量直接当路径拼进 `i18n/<locale>.json`，且整文件 `readAll()` 无上限 | `i18n/Lang.cpp` | locale 白名单（`^[A-Za-z_]{2,8}(_[A-Za-z]{2})?$`）+ 读取封顶 1 MiB |
| **C-07** | low-medium | 协议里的牌码**直接当文件名**拼 `tiles/<code>.svg`（可路径穿越），且 `QHash<QString,QSvgRenderer*>` 缓存无上限、永不释放 | `ui/TileRenderer.cpp` | 拼路径前先校验是合法牌码（否则走程序化回退）；缓存封顶 |
| **C-08** | low | 服务端数字直接 `static_cast<qint64>(double)`（越界/NaN 是 UB），`deadline_ms` 又直接加到当前时刻上 | `net/Protocol.cpp`、`ui/ActionBar.cpp` | `deadline_ms` 钳到 (0, 600000]；`ask_id` 只接受有限、整数、≤ 2^53 的值 |
| **C-09** | low | `ActionBar::actionCmd()` 不校验 `type` 属于本次询问的选项，而两个"自动应答"调用点（260ms 的自动开关、1200ms 的 `--demo`）可能拿**旧动作**配上**新 ask_id** | `ui/ActionBar.cpp` | 要求 `type ∈ m_options`，否则返回空对象（与"询问已失效"同一条路径）；`--demo` 的自动应答也改成走 `actionCmd()` |
| **C-10** | low | `isFuritenLocal()` 在客户端**自己判振听**（只看自家牌河），与 §2.1「规则只在服务端」矛盾，可能与服务端结论相反 | `model/TableModel.cpp` | 删掉本地推断；改用服务端下发的 `state.furiten[]` / `win_note:"furiten"`（模型只在服务端这么说时置位） |
| **C-11** | low | 第二次弹结算框时**不关**第一个：旧框留在屏幕上、其 `finished` 回调还能再发一条 `confirm`（会被下一次局间吃掉） | `ui/MainWindow.cpp` | 建新框前先 `closeResultDialog(false)` |
| **C-12** | low | `dealer` 不钳制（其它座位字段都钳了） | `model/TableModel.cpp` | `qBound(0,…,3)` |
| **C-13** | low | 命令行数字不校验：`--after 2147484` 溢出、`--after -5` 让 `singleShot` 收负间隔（Qt 拒绝 → `--shot` 永不触发、进程挂住） | `main.cpp`、`AutoPlay.cpp` | 统一 `boundedIntArg()`（`toInt(&ok)` + 钳制 1…3600） |
| **C-14** | nit | 每帧重建 `QFont`/字体族列表（牌桌约 11 次/帧；回退绘制里每个字形一次） | `ui/TableView.cpp`、`ui/TileRenderer.cpp` | 回退绘制路径的字体已缓存（牌桌那半边留给 TableLayout 重构一起做） |
| **C-15** | nit | 暗杠的横向宽度按"必有一张横置"估 → 多算约 0.36 张牌宽的空白（与绘制处不是同一条判据） | `ui/TableView.cpp` `meldWidthOf()` | 改成按 `meldRotatedIndex()` 判有没有横置张；顺手把 `ownerSeat` 传进来，**预留与绘制共用同一判据** |

### 2.2 明确验证过**没有**问题的地方

- 行缓冲**不是**无界的（有 1 MiB 无换行保护）；`called_index` 在 `removeAt` 前有范围检查；
  `m_discards`/`m_discardSide` 三条路径都保持等长。
- 所有 `QJsonArray/QVector/QStringList::at()` 都有守卫；**没有** `operator[]` 直取 JSON；
  客户端里没有 `assert/abort/qFatal`。
- 每个 `connect`/`singleShot` 都带上下文对象，各定时器在结束路径上都会停；没发现析构期 UAF。
- `AutoPolicy::decide` 只读 `ask.options`，且 `tsumo`/`ron` 绝对优先 ——
  「自动摸切绝不切出能和的牌」这条不变量**成立**。

### 2.3 存疑 → 已处理

- **C-S1**：`QJsonDocument` 是否会把 `1e400`/`NaN` 交给客户端，取决于 Qt 6.11 的数值校验；
  已按"可能拿到"处理（钳制），无论 Qt 行为如何都安全。
- **C-S2 · 已修**：`TableView::setModel()` 现在复位 `m_seatDataValid`（换模型即作废帧缓存），
  否则下一次 `onDiscarded` 会拿**上一个模型**的帧数据算动画。
- **C-S3 · 已修**：`onDiscarded()` 的手切起点改为**按当前布局现算**（`handSlotLocal(pos, pick, false)`），
  不再混用上一帧缓存的手牌矩形；`draw`+`discard` 同批到达时不会从已不存在的槽位起飞。
  顺带删掉了因此变成死代码的 `m_handLocalRects`。

---

## 3. 框架结构评估（重构进展）

### 3.1 已经做的

| 项 | 说明 |
| --- | --- |
| **抽出 `core/Wall`** | 牌山 + 王牌的账（122 可摸 / 14 王牌 / 岭上 / 宝牌指示牌）从 `Round`（1834 行）里独立出来，成为**纯账目类**，可脱离牌桌直接断言。过去这块账"错了也不报错，只会让点数悄悄漂移" |
| **去掉重复的报文构造** | `Round.intList` 与 `Table.intList` 是两份一模一样的实现 → 合并为 `Json.intList`，两边用 `import static` 引用（调用点零改动） |
| **`Rules` 有了归一化入口** | `clampToSane()` 把"客户端传来的规则"收进一个地方，规则不再散落在各处裸用 |
| **修好两处"预留与绘制不同源"** | 客户端 `meldWidthOf`（暗杠宽度）与服务端罚符分摊，都属于同一类错误：**同一件事有两把尺子** |
| **判据具名化** | `Round.fourKanAbortNow()`：把「四杠散了」从两处内联条件变成可脱离牌桌断言的真值表（4 条断言） |
| **`Wall` 收了规则** | `Wall(seed, rules)`：`aka = 0` 时在**发牌期**换掉赤五，于是显示与计分一致（4 条断言） |
| **热路径记忆化** | 同一舍张的 `claimOptions` 只算一次；`waitKinds` 用「暗牌计数 + 副露数」的**内容寻址**缓存（内容变则键变，无需手工失效 —— 因此不会出现"忘了失效"的经典 bug） |
| **资源上限** | 出/入两个队列改为有界（溢出丢弃 + 记日志）、聊天频率闸、每房观战上限（见 S-19） |
| **客户端帧缓存清理** | `setModel()` 复位 `m_seatDataValid`；手切动画起点改为按当前布局现算；删掉因此变死的 `m_handLocalRects` |
| **客户端布局层已拆分** | `ui/TableView.cpp` 1204 行 → **717 行**（只剩绘制/交互/动画），几何全部搬进新的 `ui/TableLayout.{h,cpp}`（414 + 147 行，**无 QWidget、无 QPainter、不存 model 指针**）。`SelfTest.cpp` **一字未改**（时间戳与内容都没动），401 条断言全绿；自检出图 `table.png` 的 **SHA256 与重构前完全一致** —— 纯搬家，不是重写 |

### 3.2 建议的下一步（按价值排序）

> **第 1 条已完成**（见上表「客户端布局层已拆分」）：`ui/TableLayout.{h,cpp}` 已落地，
> `TableView` 只负责绘制/交互/动画，`SelfTest.cpp` 未改动，出图逐字节一致。

1. ~~客户端 `TableView` 拆出纯几何层~~ —— **已完成**。后续可选：再把 8 个绘制函数抽成
   `ui/TablePainter.{h,cpp}`（签名收 `(QPainter&, const TableLayout&, const TableModel&, PaintState&)`），
   让 `TableView` 只剩下事件与动画；这一步收益小于上一步，且 `SelfTest` 要跟着微调访问器归属。
2. **服务端 `Round` 拆出报文层** —— `Round` 同时是状态机、选项生成器、结算器、**JSON 报文构造器**、
   计时器宿主和自检钩子宿主。先把「中文名 → ASCII 码 → 报文」这条链收到一个序列化器里
   （现在 `Meld.toJson` / `Round.sendMeld` / `Table.stateFor` 是**三份**同形状的 meld JSON，迟早漂移）。
3. **`claimPhase` 的 7 个并列集合收成一条记录** —— 现在一轮鸣牌仲裁横跨
   `asked/askedAt/answeredAt/answers/askedBestRank/seatDist/askedTypes`，靠注释维持同步；
   收成 `ClaimPending` 记录后这些不变量变成**结构性的**。
4. **自检钩子集中**：`Bot.debugAlwaysKan`、`Table.debugEventTap/debugAskTap`、`RoundScoring.calls*`、
   `YakuCodes.misses`、`Round/Wall.debugSetRinshanUsed` 都是**生产类里的静态可变状态**，
   并行测试不安全；收进一个可注入的 `DebugProbe`。
5. **客户端 ask 状态双份**：`TableModel` 与 `ActionBar` 各自存一份询问与选项，各自解析一次
   （`parseAsk` 在 `askOptions()` 里每次重跑）。改为单一所有者 + 一次解析。

---

## 4. 本次验证记录

> 本节记录 **2026-09-15「回放渲染与视角」这一轮**的验证（上一轮「对局记录回放」见 §4.1）。

| 层 | 命令 | 结果 |
| --- | --- | --- |
| L1 规则引擎 | `java -jar server/build/mahjong-server.jar --selftest` | **554 项全绿**（本轮**未动服务端**，故与上一轮同数） |
| L2 客户端自检 | `client\dist\mahjong-client.exe --selftest client\build\st` | **468 项全绿**（449 → +19：`roundResultEntry`、上帝视角 `drawn[4]`、**碰只扣 2 张 / 加杠只扣 1 张**、**自动理牌**、**布局按真实张数**、**出牌动画起点**、**`reset()` 不清上帝手牌**、**走真路径的「下一步」动画回归**、回放窗口两个新按钮、i18n 计数 368 / `ui.*` 246） |
| L3 对局记录端到端 | `node tools\replay-test.mjs <host> <port>` | **REPLAY PASS（26 项）**（本轮未改协议/服务端，回归确认） |
| L3 其余协议用例 | `e2e` / `timeout` / `clock` / `firstturn` / `riichi-stale` / `utf8` | 全部 **PASS** |
| L3 静态检查 | `check-i18n` / `i18n-scan --check` / `i18n-gen --check` | 全部 **PASS**（新增 8 条回放文案，废弃 5 条牌山旧 key 后语言文件 368 条） |
| L4 GUI 实拍 | `client --replay <host> <port> <id> --step N [--god] [--result] [--wall] --shot …` | 四张实拍：`replay-window.png`（导航含两个新按钮 + **按小局切割的操作列表**）、`replay-god.png`（四家手牌全明、**按真实张数排布且已理牌**）、`replay-wall.png`（**一条抓牌顺序序列**、每列 4 张、四色归属线、王牌在序列末尾）、`replay-result.png`（本局结算，只有「确定」、无倒计时） |
| L4 上帝视角逐张核对 | 临时给 `paintSeat` 加一行 `MAHJONG_DEBUG_HAND` 开关的打印，跑 `--god` 抓"实际画出来的四家手牌" | 与按同一套规则离线重放真实回放的结果**逐张一致**（13/13/13/10；座位 2 碰 9p 后 10 张且只剩 1 张 9p）—— 排除了"截图上认错牌"的误判 |
| L2 真路径红证 | 把 `clearGodHands()` 加回 `TableModel::reset()`，重跑 L2 | **自检立刻变红**：「回放「下一步」：出牌动画没按真实手牌定位」+「7m 应起飞自第 6 格，实际 10」—— 证明这条回归测的正是真机那条路径（而不是又一次绕过它） |

### 4.1 上一轮（对局记录回放）

| 层 | 命令 | 结果 |
| --- | --- | --- |
| L1 规则引擎 | `java -jar server/build/mahjong-server.jar --selftest` | **554 项全绿**（507 → +47：记录器序号/过滤/截断/ID 形状、整场录制的牌山与出牌守恒、落盘/分页/重启加载/容量淘汰/关闭态零开销） |
| L2 客户端自检 | `client\dist\mahjong-client.exe --selftest client\build\st` | **439 项全绿**（402 → +37：`ReplayModel` 的小局/巡/步索引、牌山 136 张的归属映射、上帝视角手牌、两个回放入口按钮） |
| L3 对局记录端到端 | `node tools\replay-test.mjs <host> <port>` | **REPLAY PASS（26 项）**：真跑一场东风战后**立即**能查（S-34 的回归）、分页一致、牌山 136 张不重复、每小局边界与 `replay_round` 对齐、**每条出牌的牌都是该家之前拿到的**、越界 / 非法 ID / 路径穿越、限速 |
| L3 其余协议用例 | `e2e` / `timeout` / `clock` / `firstturn` / `riichi-stale` / `utf8` | 全部 **PASS**（`claim-priority` 仍是概率性工具，样本不足时会报"无法判定"） |
| L4 GUI 实拍 | `client --replay <host> <port> <id> [--wall] --shot …` | 回放窗口（导航 / 操作列表 / 聊天 / 记录列表）与**牌山视图**各出一图；据此发现并修掉 S-36 |

### 4.2 本轮改动面

纯客户端（服务端一行未改）：`model/ReplayModel`（`GodState.drawn[4]`、`roundResultEntry`、
**副露扣牌算法对齐自家手牌**）、`model/TableModel`（`setSilent()` 静音开关、**上帝手牌存进模型**
（`setGodHand/clearGodHands/hasGodHand/godHand/godDrawn` + `concealedCount` 同源））、
`model/Tile`（`mj::sortTiles()` 统一理牌）、`ui/TableLayout`（`seatHasDrawnTile` 认上帝摸牌）、
`ui/TableView`（`setGodHands/clearGodHands`、`seatClicked()`、**出牌动画起点按理牌后的格子**、
`lastFlightFromIndexForTest()`）、`ui/WallView`（重写为**一条抓牌顺序序列**）、
`ui/ReplayWindow`（动画策略 / 上帝视角开关 / 点名牌切视角 / 按小局切割的操作列表 / 本局结算）、
`ui/MainWindow`（`offerReplay` 只在 `game_end`）、
`main.cpp`（`--god` / `--step N` / `--result` + `replayLoaded` 信号），
`SelfTest.cpp`（+22）、i18n（+8 条、废弃 5 条）、`docs/{DESIGN,PROTOCOL,AUDIT}.md`、`README.md`、`.gitignore`。

### 4.3 上一轮改动面（对局记录回放）

服务端新增 `replay/{Replay,ReplayRecorder,ReplayStore}.java`，
`Table`（录制挂点、`replay_id`、**先落盘再广播终局**、`broadcastRaw`）、`Round`（配牌顺序改为「每人一次抓 4 张」、
牌山快照）、`Session`（`replay_list` / `replay_get` + 限速）、`Main`（`--replay-dir` / `--replay-max` /
`--replay-max-mb` / `--no-replay` / `--fast`）、`SelfTest`（+47）；
客户端新增 `model/ReplayModel.{h,cpp}`、`ui/{ReplayWindow,WallView}.{h,cpp}`，
`main.cpp`（`--replay`）、`MainWindow`（两个入口 + `replay_id`）、`LobbyDialog`/`ResultDialog`（入口按钮）、
`SelfTest.cpp`（+37）、i18n（+62 条 `ui.replay.*` / `error.replay_*`）、`tools/replay-test.mjs`。

> ⚠ 本轮在**测试脚本**上踩了一个坑，值得记下来：`tools/replay-test.mjs` 的假客户端把"已经被 `wait()`
> 命中过"的消息留在 inbox 里，于是"先问列表（0 场）→ 打完再问列表"的第二问命中了第一条旧回复，
> 把服务端**正确**的行为报成了失败 —— S-34 的真相因此多花了两轮长跑才看清。
> 现在被等待者取走的消息会从 inbox 摘掉，谓词也带 `from`/`id` 精确匹配。
> **测试工具本身也是代码，也会骗人。**



> 注：`claim-priority-test.mjs` 需要"有人能碰、同一张舍张上另有人只能吃且拖着不回"这种罕见局面，
> 样本不足时会打印「无法判定：样本不足」并**返回非 0**（不是失败，也不代表通过）——
> 它是概率性定性工具，重跑即可，见 `AGENTS.md` §4。
>
> 本次审计里的 **F14（四杠散了时序）经规则校勘后判定为误报**，已在 §1.5 说明；
> 上报"误报"本身也是审计的正常产出 —— 结论以 `docs/日本麻将.md` 的原文口径为准。
