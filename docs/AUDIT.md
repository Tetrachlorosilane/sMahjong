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

### 1.5 规则校勘（对照 `docs/日本麻将.md`，并与公开资料复核）

| 规则 | 原文口径 | 代码现状 |
| --- | --- | --- |
| **四杠散了** | 「一局中**由 2 名及以上的玩家开杠了 4 次**，且**第 4 次杠后取得的岭上牌没有放铳**」→ 强制流局；**同一人**开满 4 次则成立四杠子、不流局；且「岭上开花 / 被抢杠 / 岭上牌放铳」三种情况**都不流局**（原文第 501-509 行） | ✅ 一致。判据 = `kanCount == 4 && !allKansByOnePlayer()`；三种豁免由**和了路径先 `return`** 天然满足（判据只在"那张牌已经落地且没人因它和牌"时被问到）。**审计 F14 属误报** —— 它没有把原文的豁免条款算进去 |
| **食替（kuikae）** | 分**现物食替**（打出与所吃/所碰相同的那张）与**筋食替**（打出所吃顺子另一边的牌），多数规则禁止（第 349-351 行） | ✅ 一致：`applyMeld` 里 `forbiddenDiscard` = 現物 + 按 **kind** 算出的筋替（S-12 修掉了"拿 id 当 kind 用"） |
| **不听罚符** | 共计 3000：1 家听 → 另 3 家各付 1000；2 家听 → 各付 1500；3 家听 → 剩 1 家付 3000；4 家全听 → 不罚（第 595-602 行） | ✅ 默认值**逐位一致**；且任意非默认罚符值都严格零和（S-13） |
| **赤宝牌** | 四人麻将通常 3 张（0m 0s 0p）或 4 张（0m 0s 0p 0p）（第 395 行） | ✅ `aka = 0 / 3` 均已实现（S-21）；`aka = 4` 受 id 编码限制仍按 3 处理，见 S-26 与 AGENTS §8 |

公开资料（与原文口径一致）：[四槓算了（麻將 Wiki）](https://mahjong.fandom.com/zh/wiki/%E5%9B%9B%E6%A7%93%E7%AE%97%E4%BA%86)、
[流局（萌娘百科）](http://mzh.moegirl.tw/%E6%B5%81%E5%B1%80)、[四開槓（麻雀ローカルルールWiki）](https://w.atwiki.jp/mahjlocal/pages/65.html)。

### 1.4 明确验证过**没有**问题的地方（免得后人重复怀疑）

- **观战者拿不到暗牌**：`stateFor(-1)` 手牌为空；`round_start`/`draw` 是**逐座位** `send` 的，从不广播。
- **默认规则下授受守恒**：`Payments.compute` 按构造零和（含包牌分支）。
- **杠的四道闸门**都走 `canKan()`；被拒的杠退回默认摸切（§2.3 的旧修复是完整的）。
- **`menzen` 初值 / 庄家第 14 张 / 横置顺延** 三条历史 bug 均已确认仍然正确。
- **非法 UTF-8**：`InputStreamReader` 用 REPLACE 解码，畸形字节退化成 `bad_json`，没有异常路径。

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

| 层 | 命令 | 结果 |
| --- | --- | --- |
| L1 规则引擎 | `java -jar server/build/mahjong-server.jar --selftest` | **414 项全绿**（原 387：+12「吃」的合法性、+7 不听罚符零和、+4 四杠散了真值表、+4 赤宝牌张数） |
| L2 客户端自检 | `client\dist\mahjong-client.exe --selftest client\build\st` | **401 项全绿** |
| L3 协议端到端 | `node tools\e2e-test.mjs`（整场东风战 + 逐条 ASCII 审计 + 岭上账） | **E2E PASS** |
| L3 其余 | `timeout` / `utf8` / `clock` / `firstturn` / `riichi-stale` | 全部 PASS |
| L3 静态三件套 | `check-i18n` / `i18n-scan --check` / `i18n-gen --check` | 全绿（293 条文案 / `ui.*` 173 条 / 源码 0 处残留中文） |
| 交付物一致性 | `build\` ↔ `dist\` 的 6 个可交付子目录比对 | 全 0 差异 |

> 注：`claim-priority-test.mjs` 需要"有人能碰、同一张舍张上另有人只能吃且拖着不回"这种罕见局面，
> 样本不足时会打印「无法判定：样本不足」并**返回非 0**（不是失败，也不代表通过）——
> 它是概率性定性工具，重跑即可，见 `AGENTS.md` §4。
>
> 本次审计里的 **F14（四杠散了时序）经规则校勘后判定为误报**，已在 §1.5 说明；
> 上报"误报"本身也是审计的正常产出 —— 结论以 `docs/日本麻将.md` 的原文口径为准。
