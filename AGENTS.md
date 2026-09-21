# AGENTS.md — 立直麻将（日本麻将）联网对战

给在本仓库工作的 AI agent / 新同事的操作手册。**先读这份，再动代码。**

---

## 0. 怎么用这份文档

**这份是开发手册；面向玩家（怎么玩、规则取舍、常见问题）的说明在根目录 `README.md`。**

按轻重缓急读：**① §2 铁律**（违反必出 bug，改代码前先扫一遍）→ **② §3 构建 / §4 验证五层**
（"编译通过"不算验证）→ **③ §6 代码约定**（按主题分组的硬约束）→ **④ §7 症状表**（报障时按症状查）
→ 其余（§5 目录、§8 状态与限制、§9 素材与字体）需要时再翻。

文档地图：`docs/PROTOCOL.md`（协议契约 —— **改协议先改它**）· `docs/DESIGN.md`（架构与规则取舍）·
`docs/AUDIT.md`（审计与修复清单）· `docs/THEME.md`（材质包/设置文件）· `docs/THIRD-PARTY.md`（第三方许可与分发义务）· `docs/DEPLOY.md`（Ubuntu 部署）·
`client/README.md`（客户端构建与链接方式）· `README.md`（**面向玩家**）。

---

## 1. 这是什么

一套完整可运行的四人立直麻将联网游戏：

- **服务端** `server/` —— Java 21，**零第三方依赖**（只用 JDK 标准库，没有 Maven/Gradle）。
  跑在 Ubuntu 上，是**唯一权威方**：洗牌、配牌、摸切、鸣牌、和牌判定、役种/符数/点数、振听、流局、连庄、精算。
- **客户端** `client/` —— Qt 6 Widgets + C++17，MinGW 构建，跑在 Windows 上。
  只负责牌桌绘制、操作收集、事件反馈、网络收发。**不做任何规则判定。**
- **规则依据** `docs/日本麻将.md`（1494 行，2026-09 版；逐节标注了《雀魂》《天凤》与 **M.League** 的差异）。
- **规则预设** `rules.preset`：`mleague`（默认）/ `tenhou` / `majsoul` / `custom` —— `Rules.applyPreset()` 先铺一整套，
  报文里的单项字段再覆盖；新增取舍项要**五处一起改**（字段 / 三套预设 / `fromJson`+`toJson` / `clampToSane` / 断言）。
  ⚠ **取舍类断言必须两侧都显式传规则集**（`preset(name)` + `evalCtx(..., Rules)` 重载）：只测默认值等于在测
  "默认值恰好是什么"，默认从《雀魂》换成 M.League 时自检一次红了 8 条就是这么来的（`mleagueRulesTests`）。
  ⚠ 且**役种名 ≠ 取值**：不加倍役满时国士十三面/四暗刻单骑/纯正九莲仍是各自的役种名（旧代码不加倍就改名成
  「国士无双」）。取舍清单见 `docs/DESIGN.md`，字段表见 `docs/PROTOCOL.md` §5。
- **对局记录/回放** `server/.../replay/` + 客户端 `ReplayWindow`/`WallView`：整场下行报文按 `seq` 记下、
  终局**先落盘再广播 `game_end`**（否则结算界面上点「看本局回放」查不到）；接口 `replay_list`/`replay_get`，
  回放 ID **先校验形状再拼路径**。回放的**动画策略**（只有"前进一个操作"播动画）与
  **牌山布局**（一条抓牌顺序序列、绝不按玩家分行）见 DESIGN「对局记录与回放」。细节见 PROTOCOL §3.11。
- **接口契约** `docs/PROTOCOL.md` —— 两端唯一的接口定义。

数据流：`Qt 客户端 ──TCP/NDJSON──> Java 服务端`。一条 TCP 连接一个玩家，一行一个 JSON。

---

## 2. 铁律（违反必出 bug）

### 2.1 规则只在服务端

客户端**绝不能**自己判断役种、番数、符数、点数、振听、听牌是否合法。
唯一例外：`TableModel::waits()` 为「显示便利」做的纯形式听牌提示，仅用于 UI 提示，不参与任何决策。

新增任何"能不能和 / 能不能吃碰"的逻辑，一律加在服务端并通过 `ask.options` 下发。

### 2.2 协议字段是权威，不要靠猜

这几条都是踩过坑总结出来的，**改代码前先看注释**：

| 字段 | 位置 | 规则 |
| --- | --- | --- |
| `tsumogiri` | `discard` 事件 | 判断「手切 / 摸切」**只能用这个字段**。绝不能靠「牌的 kind 是否等于摸到的牌」去猜——手里已有 5m 又摸到 5m 而手切原来那张时，猜法会把摸牌当成打出去的，导致手牌数多出一张。 |
| `sideways` | `discard` 事件 | 牌河里**只有立直宣言牌横置**，其余一律 false。宣言牌被鸣走后服务端会把横置**顺延**到该家下一张打出的牌（再被鸣走就继续顺延）。客户端**只按这个字段画**，不要自己推断。 |
| `called_index` | `meld` 事件 | 被鸣走的那张在**原牌河**中的下标。鸣牌是「移动」不是「复制」：服务端会把它从牌河移除，客户端也要 `removeAt(called_index)`，否则一人牌河会多出一张、且后续下标全部错位。 |
| `win_note` | `ask` 事件 | `furiten` / `no_yaku`：能听牌但不能和的原因，用于界面提示。没这个字段时不要瞎猜。 |
| `ask_id` | `ask` 事件 | 回包要原样带回，服务端用它丢弃过期回包。**没有 `ask_id` 的消息也可能是「老客户端」的答复**（见 `RoundClaims.acceptsReply`），所以认领时必须**再确认它是个动作**（有 `type`）——否则局间残留的 `confirm` 会被当成出牌答复，玩家没动就被摸切。还有第三道闸：**`type` 必须是本次询问下发过的 `option.type`**（挡被取消询问的迟到回包，见 §2.3-10）。**客户端侧**：所有动作都必须带 `ask_id`，且只能从 `ActionBar::actionCmd()` 这一个入口组包（见 §2.3-9）。 |
| `confirm` | `cmd`（客户端→服务端） | 局间「我看完了，进下一局」。**没有 `ask_id`**。它必须由局间等待（`Table.awaitRoundConfirm`）从 `submit()` 写入的那个队列里消费掉；一旦漏到下一巡，就会被当成出牌答复（症状：**点了确认键后，下一局第一巡自动出牌**）。 |
| `base_ms` / `bank_ms` | `ask` 事件（turn 与 claim） | 思考时间明细：本巡基本时长 / 剩余总额外时长。**扣减与取整由服务端算，回合与鸣牌都扣，且额外时长每小局重置**。规格写作「**额外+每巡**」：`20+5` = 额外 20s + 每巡 5s（**别写反**，每巡一般远小于额外）。见 PROTOCOL §5.1。 |
| `yaku[].code` / `yaku[].tile` | `agari` 事件 | 役种**只发 ASCII 码**（`"riichi"`），参数化役种（役牌/场风/自风）另带一张牌码 `tile`。**没有 `name` 字段**——中文在客户端语言文件里。认不出的码由客户端原样显示（见 §6）。 |
| `limit` / `reason` / `error.code` | `agari` / `ryuukyoku` / `error` | 同样是 ASCII 码（`"mangan"` / `"exhaustive"` / `"no_room"`）。**`error.arg` 是可选的 ASCII 参数**（如 `unknown_cmd` 带命令名）。报文里**只有** `name`/`text`/`msg` 三个字段允许非 ASCII。 |
| `dead_wall_left` | `draw` / `round_start` / `state` | 剩余**岭上**牌数。⚠ **每次摸牌都要带**：杠后那张取自王牌，`tiles_left` 在**岭上摸牌时不动**（开杠那一刻牌山末尾一张移进王牌才减 1），所以「岭上有没有被摸走」只能看它。一局 4 张：4→3→2→1。见 §6「王牌/岭上」与 PROTOCOL §3.4。 |
| `drawn` | `round_start`（**仅庄家**） | 本巡「刚摸到的那张」的牌码（= 14 张里的第 14 张）。**必须有**：`hand` 是**已排序**下发的，位置推不出来，客户端只能猜 —— 猜错就是幽灵手牌（见 §2.3-11）。客户端摆摸牌位只认它；缺这个字段的老服务端会退回"最后一张是摸到的"（会认错）。 |

### 2.3 十二条曾经踩过的坑（同类问题会再犯）

1. **`riichi` 事件到达时，绝不能把"牌河最后一张"标成横置。**
   服务端顺序是**先广播 `riichi`、再广播 `discard`**，此刻牌河最后一张还是宣言牌**之前**那张，标它就等于一人牌河两张横置。
2. **`menzen` 必须在 `Round` 构造时初始化为 `true`。**
   Java `boolean[]` 默认 false；漏了会导致实局里**所有门前役**（立直/平和/一杯口/门前清自摸和/四暗刻/九莲）全部失效，而单测因为显式传 `ctx.menzen=true` 照样通过。
3. **向听 DFS 里 `melds + partials` 必须封顶到 4。**
   不封顶会把 `1112223334445m` 这类牌型误判为和了形。
4. **庄家的第 14 张在配牌时就发了，第一巡不能再摸。**
   `setup()` 给庄家多发一张、`livePos = p + 1` 也正确跳过了它；但出牌循环开头
   `needDraw = true` 会让庄家**再摸一张 → 起手 15 张**。修法是第一巡用 `openingTile`
   充当「本次摸到的牌」（保住自摸/暗杠/天和判定），且**不重复下发 `draw` 事件**
   （客户端已从 `round_start` 收到 14 张）。
5. **Qt 的 SVG 渲染器不支持 CSS 类选择器。**
   Qt 只实现 SVG Tiny：Illustrator 导出的 SVG 把颜色写在 `<style>` 里、用 `class="st0"` 引用
   （**没有 `fill=` 属性**），Qt 忽略 class → 颜色全丢、只剩轮廓。
   先跑 `tools/inline-svg-style.ps1` 内联成表现属性（症状：素材是彩色，画出来是黑白线稿）。
6. **`--shot` 必须抓「活动顶层窗口」，不能直接 grab 主窗口。**
   结算/大厅是**独立顶层窗口**，不在主窗口渲染树里，`window.grab()` 拍不到（曾据此误判"弹窗没弹出"）。
7. **改源文件一律用 edit 工具，不要用 PowerShell 做多行字符串替换。**
   行尾一旦是 CRLF，PowerShell 里 `` `n `` 是 LF，`.Replace()` 会**静默不匹配**——
   不报错、不生效，极易被当成"改过了"。已被这个坑绊了三次（Java 源码、CMakeLists、SVG 生成器）。
8. **服务端只有一条命令队列 `responses`；局间确认必须从那一条队列里消费。**
   `submit()` 把**所有**客户端命令（`action` / `confirm`）写进 `responses`。
   曾经 `drainConfirm()` 去轮询 `seats[i].inbox` —— 那个队列**全仓没人写**，
   于是 `confirm` 从来没被消费：① 「全员确认就提前开下一局」永远不生效（永远干等 5 秒）；
   ② 它一路留到下一巡，被 `awaitAction()` 当成出牌答复（**没有 `ask_id` 的消息在旧代码里被直接 return**），
   出牌循环再把它默认成 `"discard"` → **玩家没动就被自动摸切**。
   症状：**点了结算确认键后，下一局第一巡自动出牌**。
   教训：**认领答复时必须确认它是个动作**（有 `type`），并且在**写入的那个队列**上消费。

9. **「重复点击」= 同一个询问发出两条动作，客户端必须自己先锁住。**
   旧客户端点立直/出牌后**直接 `sendCommand`**：没走统一入口 `onActionReady()`（那里才 `clearAsk()`）、
   回包也不带 `ask_id`。于是连点两下发出**两条**动作，第二条服务端判不了过期，一路留到**下一巡**
   被 `awaitAction` 当成本巡答复 → 玩家没动就被代打；立直按钮还是"看不见状态"的开关（连点第二下
   把模式关掉，标题却仍写「立直：请点击要打出的宣言牌」，点手牌发出的其实是普通弃牌）；
   而服务端对不成立的 `riichi` 会退化成 `discard`，`riichi.tile` 是**为立直挑的宣言牌**
   —— 打出去等于替玩家扔掉一张他没选的牌。
   修法（四条一起才闭环）：① 客户端**所有动作都从 `ActionBar::actionCmd()` 组包**（带 `ask_id`）；
   ② 出牌/立直走 `MainWindow::onActionReady()`（先 `clearAsk()`，第二下时 `actionCmd()` 已因
   `m_valid=false` 返回空对象）；③ 标题统一由 `refreshTitle()` 产生、立直按钮 `checkable`；
   ④ 服务端兜底：立直不成立退回**默认摸切**，绝不把宣言牌当普通打牌执行。
   回归：`client --selftest` 的 ActionBar 组 + `node tools\riichi-stale-test.mjs`。

10. **鸣牌仲裁：高优先级成立后**不必再等**低优先级的那几家，而且他们的回包不能留在队列里。**
    一张舍张会同时问多家（`Round.claimPhase`）。优先级是「荣和 > 杠 = 碰 > 吃」，
    **同级时离打牌者近的赢**。旧实现只知道「都答完 / 超时 / 荣和者都答了」三种收工条件，
    于是：能碰的那家已经回了，却因为另一家"能吃但没点"而要**干等满整个鸣牌窗口**
    （实测 3000ms 的窗口就白等 3000ms，鸣牌成立被推迟到那一刻）。
    修法是让 `RoundClaims.shouldStop` 多一条：**已到手的最优鸣牌如果没人能压过，立刻收工**。
    ⚠ 判据里的「压过」必须与实际仲裁**同一把尺子**（等级 → 座次），否则提前收工会**改变赢家**。

    收工之后还有**队列卫生**这一步（用户点名的那半）——被取消询问的那家可能回包已经在路上：
    - 已经在队列里的 → `Table.dropReplies(seat, cancelledAskId)` 摘掉
      （判据：同一座位 + 是动作 + `ask_id` 对得上**或根本没带**）；
    - 还在网线上的 → 认领处再校验 **`type` 必须属于本次询问下发过的 `option.type`**
      （`Table.awaitAction(..., allowedTypes)` 与 claimPhase 的 `askedTypes`）。
      废包的 `chi`/`pon` 不可能出现在出牌询问的选项里，所以它被丢弃、玩家照样拿到完整 deadline。

    不处理这两条的症状：**玩家没动就被代打**（废包被 `awaitAction` 当成本巡答复）；
    或下一次鸣牌询问被废包"先答了"，真答复被顶掉。
    回归：`SelfTest.roundClaimsTests/dropRepliesTests` + `node tools\claim-priority-test.mjs`。

11. **「哪张是刚摸到的」只能由服务端点名，客户端不许猜；出牌取牌只认牌码。**
    庄家第一巡的 14 张配牌是**已排序**下发的，客户端若按"最后一张 = 刚摸到的"去认摸牌位，
    就会与服务端的第 14 张（`openingTile`）认成两张不同的牌 —— 玩家点摸牌位时
    `discard.tsumogiri` 的牌码对不上，而旧服务端此时会**退回默认摸切**（打出刚摸到的那张，
    恰恰是玩家没点的那张）。两端于是各留一张不同的牌：**张数相同、内容差一张**，
    而且不会自愈（下一巡又按"手里有没有这张"去猜，越打越歪）—— 这就是「幽灵手牌」。
    修法三条一起才闭环（**只加 `tsumogiri` 字段是不够的**，那正是第一版修完仍然复发的原因）：
    - 服务端 `round_start` **点名** `drawn`（= 第 14 张），客户端按它摆摸牌位；
    - 服务端出牌取牌**牌码决定打哪张**（`Round.pickDiscardId`）：声明摸切**且**牌码吻合才取摸牌位，
      其余一律按牌码在暗手里找 —— **绝不**把"摸切声明对不上"兜底成摸切；
    - 客户端对账也按**牌码**：只有"声明摸切且摸牌位那张的牌码就是事件里的 `tile`"才清摸牌位，
      否则扣暗牌里那一张、再把摸牌位的牌并入暗牌（顺序不能反，反了坏报文会让手牌涨到 14 张）。
    回归：`SelfTest.discardAlignTests`（逐条判据 + 庄家 `round_start` 必带 `drawn`）
    + `client --selftest` 的两组（`drawn` 点名 / 错配摸切按牌码对账）。

12. **振听有三种，而且「自己曾经打出过的牌」≠「牌河」。**
    一次审计里三条**全都不成立**（`furitenTemp`/`furitenPerm` 只有清除、从来没人置位；
    舍张振听读的是 `discards[]`，而被他家吃碰杠走的牌**已经从牌河移除**）：
    - **舍张振听**要算上**被他家吃碰杠走的舍牌**（`docs/日本麻将.md` §振听 明文："包括听牌前打出的牌，
      以及后来被他家吃、碰或杠走的舍牌"）→ 单独记一份账 `Round.discardKindsEver[4][34]`，
      出牌时在**唯一**的记账点 `Round.recordDiscard` 累加。
      ⚠ `ownDiscardKinds` 只服务振听，**不是**牌河的镜像。
    - **同巡振听**：被给了 `ron` 却没和（**含超时未答**）= 见逃 → 本巡之内不能再荣和，
      自家下一次摸牌时解除。记账在 `claimPhase` 仲裁之后逐座位做。
    - **立直振听**：立直状态下见逃荣和**或见逃自摸** → `furitenPerm`，持续到本局结束。
    ⚠ 影响的不止服务端：**自检里两条老用例原本是"直接往 `discards[]` 里塞一张"来造振听的**
    —— 那正好把 bug 当成了规格（`SelfTest.furitenTests` 的舍张/立直两条），
    所以它们必须改成走 `debugPushDiscard`（= 生产的记账）。**测试里绕过记账点就是在给 bug 背书。**
    已知偏差（无役见逃不触发同巡振听）与实现位置见 `docs/DESIGN.md`「振听：三种都要记」。
    回归：`SelfTest.furitenRuleTests`（含"确实造出过见逃机会"的非空转断言）。

### 2.4 别做危险操作

- **不要按进程名批量杀 `node`。** DSH 的 Web GUI（`127.0.0.1:3080`）就跑在 node 上。
  要清理自己起的测试进程，**按端口查 PID** 再精确结束。
- 只改工作区 `C:\Users\HP\source\games\mahjong` 内的文件。

---

## 3. 环境与构建

### 3.1 工具链：脚本自己找，不写死路径

构建脚本按「显式参数 → 环境变量 → PATH → 常见安装位置 → 仓库内缓存 → **自动下载**」解析依赖，
所以**不必预装 Qt**（见 §3.3）。本机实测可用位置（**仅供参考，不是硬编码**）：
JDK 21 `C:\Program Files\Microsoft\jdk-21.0.10.7-hotspot`、Qt 6.11.2 `D:\Dependencies\Qt\6.11.2\mingw_64`、
MinGW 13.1 `D:\Dependencies\Qt\Tools\mingw1310_64\bin`、CMake `C:\Program Files\CMake\bin`、Ninja `D:\Dependencies\Qt\Tools\Ninja`。

> **系统 PATH 里没有 g++**，构建脚本自己拼 PATH（`client/build.ps1` / `server/build.ps1` 已处理）。

### 3.2 服务端

```powershell
# Windows 开发
pwsh -File server\build.ps1           # → server\build\mahjong-server.jar
java -jar server\build\mahjong-server.jar --selftest   # 规则引擎回归（必须全绿）
java -jar server\build\mahjong-server.jar --port 10086
```

```bash
# Ubuntu（目标环境）
cd server && ./build.sh && ./run.sh   # 启动后打印 LISTENING 0.0.0.0:10086
```

- 默认**双栈监听**（`0.0.0.0` + `[::]`），WSL/容器场景必需，别改回只绑 IPv4。

### 3.3 客户端

```powershell
pwsh -File client\build.ps1 -Deploy    # 编译 + 把 Qt 运行时拷到 exe 旁边
client\dist\mahjong-client.exe         # 可直接双击，无需装 Qt / 配 PATH
```

**这套 Qt 是 shared 构建，物理上无法静态链接**（`qconfig.pri` 里 `static` 是 disabled feature）。
本项目用「构建后把 Qt DLL + 插件拷到 exe 同级目录」达成等价的绿色版，`build.ps1` 已自动化。

**Qt 既不预装也不进仓库**：`client/build.ps1` 找不到 Qt 时会从 download.qt.io 自动取
（只取 qtbase+qtsvg ≈ 22 MB，解包到仓库根 `.qt/`，已被 `.gitignore` 忽略；MinGW/CMake/Ninja 缺失时同样取）。
实现在 `tools/qt-provision.ps1`（下载链 iwr→curl→node、逐个校验官方 `.sha1`、用 bsdtar 解 Qt 的 `.7z`）。
`-Provision always` = 只用脚本自己那份（可复现），`-Provision never` = 禁止下载，`-QtDir` = 用指定的，
`-Plan` = 只打印解析结果不动手。

### `build\` 与 `dist\` 的关系（两者**不应该**整目录相同）

| 目录 | 内容 | 何时更新 |
| --- | --- | --- |
| `client\build\` | 构建目录：exe + Qt 运行时 + `CMakeFiles/`、`mj_moc/`、`*.obj`、`st/` 等中间产物 | **每次**构建 |
| `client\dist\` | 发布目录：**只含运行必需**（exe + Qt DLL + platforms/styles/tls + tiles/ + fonts/） | **只有带 `-Deploy`** 时 |

所以「一致」只应针对**可交付子集**（上表 dist 那几类），比对方法：
```powershell
# 只在 build  /  只在 dist  /  两边都有但内容不同
pwsh -File client\build.ps1 -Deploy          # 先同步，再比；期望三项都是 0
```
⚠ 判据**不要用 exe 哈希**：每次构建都 clean 重建 + 重新链接，PE 头时间戳不同 →
同一份源码的哈希也会变，比哈希会次次报警（等于没有信号）。
`build.ps1` 现在会在**不带 `-Deploy`** 且 `dist\` 早于最新源文件时打印提醒（语义判据，不会误报）。

### 3.4 构建避坑

- **AUTOMOC 在受限沙箱会失败或缓存陈旧**（`AutoMoc subprocess error` / `mocs_compilation.cpp` 规则缺失）：
  `build.ps1` 检测到就清空 build 目录、改用配置期预生成 moc 重试。
- **exe 在运行会锁住文件**（脚本会先结束 `mahjong-client`）；`windeployqt` 起不来时脚本回退手动拷贝。

---

## 4. 验证：五层，从便宜到贵

**改完必须跑对应层，"编译通过"不算验证。** 这个项目里绝大多数 bug 是编译期发现不了的。

### L1 规则引擎（秒级，最常跑）

```powershell
java -jar server\build\mahjong-server.jar --selftest
# 期望：通过 N 项，失败 0 项 / SELFTEST PASS
```

覆盖：牌编解码、向听、听牌、役种、符数、**完整打点表逐格比对**、授受守恒、包牌、不听罚符、振听、
立直条件、和牌选项下发、横置顺延、**杠后岭上摸牌的账**、**训练接口的全部不变式**（见 §6.5）、
**teacher 的取舍**（牌效/押し引き/打点与役/开杠/副露打分/默听/顺位与终局/对手模型，见 §6.6），
以及 **3 次「4 机器人整场半庄」**的点数守恒。改了 `rules/` / `game/` 下任何东西都要重跑。

> ⏱ 全量自检约 **110~125 秒**（2026-09 teacher 档 A/B/C 之后 **1149** 项；档 B 时是 1091 项）
> —— 慢的是里面那十来个"整场模拟"用例（teacher 变聪明了，每步算得更多），不是断言数。
> 新加自检用例时**优先用 `Table.debugMaxHands` 限制小局数**（`SelfTest.PROBE_HANDS`），
> 否则一个用例就是 2~3 秒。**需要"定向局面"时别靠发牌运气**：直接摆手牌 + 用现成的钩子问结论
> （`Round.debugClaimOutcome` 问鸣牌仲裁、`debugRonDeltas` 跑荣和结算、`debugTurnKan` 跑
> 暗杠/加杠（**抢杠那条路只有这个钩子测得到**，含《雀魂》的国士抢暗杠）、`debugDoRiichi` 走生产的立直宣告、
> `debugDrainWallTo` 推进牌山、`debugPushDiscard` 走生产记账）—— 比"多跑几百场碰运气"可靠得多。

### L2 客户端自检（秒级）

```powershell
client\dist\mahjong-client.exe --selftest client\build\st
# 期望：检查项 N，失败 0 / SELFTEST PASS；并产出 tiles.png / table.png / river_overflow.png
```

覆盖：牌字符串↔kind 双向、NDJSON 编解码、`TableModel` 事件应用、出牌手切/摸切、横置张数、
**风盘布局全部断言**（牌河/副露尺寸、盘近正方、宝牌行含于四家得点框内、四家点数等距且框同尺寸居中、
立直棒按座位、供託居中、场次不与宝牌行相交、河区不压手牌、牌河超 18 张仍逐行）、并出图。

> ⚠️ **不要用 `-platform offscreen` 看 PNG。** Qt 的 offscreen 插件在 Windows 上没有字体库，
> 文字会画成空心方框（平台插件行为，不是 bug）。要看图就用默认 windows 平台。

### L3 协议端到端（e2e 要几分钟，其余几十秒）

```powershell
java -jar server\build\mahjong-server.jar --port 10086   # 另开一个终端
node tools\e2e-test.mjs 127.0.0.1 10086                  # 期望 E2E PASS
node tools\timeout-test.mjs 127.0.0.1 10086              # 超时摸切 + 断线立即回收
node tools\clock-test.mjs 127.0.0.1 10086                # 思考时间：基本时长/额外时长扣减 + 鸣牌也扣
node tools\firstturn-test.mjs 127.0.0.1 10086 3000       # 「每局第一巡」的实测等待 = 声明 deadline
node tools\riichi-stale-test.mjs 127.0.0.1 10086 3000     # 作废的立直不得替玩家打牌 + 重复包被丢弃
node tools\claim-priority-test.mjs 127.0.0.1 10086 3000   # 鸣牌优先级：高优先级成立后不必等低优先级 + 废包不漏到下一巡
                                                          # ⚠ 依赖发牌运气：取不到样本时**退出码 2**（不是失败），重跑即可
node tools\discard-align-test.mjs 127.0.0.1 10086        # 出牌对齐（幽灵手牌）：庄家 round_start 必带 drawn +
                                                          # 「错报摸切也按牌码取牌」+「我报哪张就打哪张」不变式
node tools\seat-swap-test.mjs 127.0.0.1 10086            # 换座/洗座：被换走那家的「准备」必须落在自己座位上 +
                                                          # 并发 churn 下座位表恒为四家的排列 + 局中 take_seat 被忽略
node tools\spectate-test.mjs 127.0.0.1 10086            # 观战（对局中入局）：spectate + 公开快照（seat=-1/带 dealer、
                                                          # drawn_seat/不带暗牌与振听）+ 持续收到公开事件 + action 被拒
                                                          # ⚠ 依赖"对局进行中"的时机：拿不到样本时退出码 2（不是失败）
node tools\utf8-test.mjs 127.0.0.1 10086                  # 报文编码：中文/代理对原样往返 + 截断不切坏字符
node tools\replay-test.mjs 127.0.0.1 10086                # 对局记录：写入/列表/分页/出牌守恒/路径穿越/限速
                                                          #（加 --no-game 只验读取路径，几秒跑完）
node tools\check-i18n.mjs                                 # **静态**核对：服务端每个码都有客户端译文（不用起服务端）
node tools\selfplay-check.mjs <轨迹目录>                    # 训练数据集校验（独立实现；不用起服务端；见 §6.5）
node tools\i18n-scan.mjs --check                          # 界面文案必须都在语言文件里（源码里不留中文；见 §6）
node tools\i18n-gen.mjs --check                           # 映射表 ↔ 语言文件一致（新增文案不漏 key）
```

> **`firstturn-test.mjs` 怎么测**（见 §2.3-8）：① 收到 `round_wait` 后 200ms 发 `confirm`（**没有 `ask_id`**），
> 量下一局多久开始（正常 ~50ms，未修 ~4800ms）；② 每局第一巡**故意不答**，量「询问 → 代打」间隔，
> 必须 = `ask.deadline_ms`（未修时 **0ms**）。其余询问立刻应答。**「首巡偏短 / 自动出牌」的第一道定性工具。**

> ⏱ **耗时提醒**：机器人每步约 800ms、局间 1200ms（刻意放慢），`e2e-test` 跑完整场东风战要 **4~8 分钟**；
> 想快就临时调小 `Table.botDelayMs` / `roundDelayMs`（自测里的整场模拟已置 0）。

### L4 GUI 定点复现（发现 UI bug 时首选）

**不要靠运气等牌**——用假服务端把客户端直接推进到目标状态，再截图看：

```powershell
node tools\mock-server.mjs 10999 turn|claim|note|river|agari|yakuman|kan|twoturn
# turn 自摸/立直/杠 · claim 荣和/碰/跳过 · note 无役提示 · river 横置顺延（顺带压测 5 张宝牌栏）
# agari 结算 + round_wait(5000) 倒计时 · yakuman 须写「2倍役满」不得出现「0 番」
# kan 「嶺上 M」跟着减（4→3→2） · twoturn 跨局首巡不得只剩 1 秒
client\dist\mahjong-client.exe --demo 127.0.0.1 10999 --bots 3 --no-answer `
    --shot client\build\shot.png --after 6
```

`--shot` 用 Qt 自己的 `grab()` 出图（不受屏幕裁剪影响；要看清细节就裁切放大）。

### L5 真机联调（最贵，改动涉网络/流程时跑）

```powershell
node tools\mock-server.mjs / 或真服务端
client\dist\mahjong-client.exe --autoplay 127.0.0.1 10086 --name 联调 --timeout 200
# 结果写在 client\dist\autoplay.log，期望 AUTOPLAY PASS
```

`--autoplay` 会打日志记录**每次 ask 的选项类型**，排查"某个按钮没出现"非常有用。

### 客户端全部命令行模式

| 模式 | 用途 |
| --- | --- |
| `--selftest [outdir]` | 自检 + 出图 |
| `--lobbytest <host> <port>` | 大厅 UI 回归：连上后「建房间」按钮是否可用，并真的点它建房 |
| `--autoplay <host> <port> [--name 名] [--timeout 秒]` | 真连服务端自走一整场，写 `autoplay.log` |
| `--demo <host> <port> [--bots N] [--no-answer] [--shot png] [--after 秒]` | 起 GUI 自动进房；`--no-answer` = 建房但不自动应答（截图用）；`--shot` 定时出图后退出（**优先抓活动顶层窗口**，否则拍不到弹窗） |
| `--replay <host> <port> [回放ID] [--wall] [--god] [--step N] [--result] [--export-tenhou out.txt] [--shot png]` | 打开回放窗口（`--wall` 牌山 / `--god` 他家手牌 / `--step N` 第 N 步 / `--result` 本局结算 / `--export-tenhou` 导出天鳳牌谱后退出）：复盘与 L4 截图都用它 |
| `--gentiles <outdir> [字体路径]` | 用字体把 Unicode 麻将牌字形**轮廓化**成 SVG 素材（见 §9） |
| `--fontprobe <out.png> [字体路径] [轮次]` | 渲染候选输入串，**实测字体的连字语法**；轮次 1=总览 / 2=组合符放大 |

---

## 5. 目录结构

```
mahjong/
├─ docs/
│  ├─ 日本麻将.md      规则原文（权威依据）
│  ├─ PROTOCOL.md      网络协议契约 ★ 改协议先改这里
│  ├─ DESIGN.md        架构设计
│  ├─ AUDIT.md THIRD-PARTY.md   审计与修复清单 / 第三方许可与分发义务
│  ├─ DEPLOY.md        Ubuntu 部署（systemd/防火墙/WSL 端口转发）
│  └─ images/          截图资产
├─ server/
│  ├─ build.sh run.sh  Ubuntu
│  ├─ build.ps1        Windows 开发
│  └─ src/main/java/mahjong/
│     ├─ util/         Json（自写零依赖）、Log
│     ├─ core/         Tiles Meld Rules Wall(牌山+王牌账)
│     ├─ rules/        Shanten Agari Evaluator(役种+符+高点法) Payments
│     │                 Visible(可见牌统计) HandEval(进张/听牌形 —— AI 用的评估判据，不参与判定)
│     ├─ game/         Round(一局状态机) Table(房间/半庄/线程)
│     │                 WinCheck/RoundOptions/RoundClaims/RoundScoring(纯判据，可单独单测)
│     ├─ replay/       Replay Store Recorder（对局记录：录制 / 落盘 / 容量淘汰）
│     ├─ net/          Server Session
│     ├─ bot/          Bot(牌效 AI，补位用；同时是训练用的 teacher)
│     ├─ ai/           ★ 训练接口：Policy/ActionPolicy/PolicyFactory(接缝) Observation(合法信息集)
│     │                 Action(动作空间) Decision Policies(内置策略与适配器) —— 见 §6.5
│     ├─ train/        ★ 自对弈：SelfPlay(并行/种子/统计) TraceRecorder(轨迹 JSONL)
│     └─ test/         SelfTest ★ 改规则必须在这里加断言
├─ client/
│  ├─ build.ps1
│  ├─ assets/
│  │  ├─ tiles/        牌面矢量素材（<牌码>.svg ×38，可直接替换；见 §9）
│  │  ├─ tiles.qrc     兜底资源清单（tools/gen-tile-placeholders.mjs 生成）
│  │  ├─ i18n/         语言文件 <locale>.json（zh_CN = 全部界面文案，键 = <族>.<码>）
│  │  ├─ i18n.qrc      i18n 的兜底资源清单（目录删掉也能出文案）
│  │  └─ fonts/        结算界面用的牌面字体 I.MahjongJP.otf（M+ 授权，见 §9）
│  └─ src/
│     ├─ main.cpp          入口（各命令行模式；**尽早 lang::load()**）
│     ├─ SelfTest.cpp      自检 + 出图
│     ├─ AutoPlay.cpp      自走联调
│     ├─ i18n/             Lang（语言文件加载 + t()/code()/yakuText()/tileName()）
│     ├─ net/              NetClient(含 IPv4/IPv6 自动回退) Protocol
│     ├─ model/            Tile TableModel(纯数据状态机) AutoPolicy(自动应答判据) Settings Theme(材质包)
│     └─ ui/               TileRenderer TableView ActionBar AutoBar LobbyDialog ResultDialog SettingsDialog MainWindow
│                           ReplayWindow(回放) WallView(牌山 136 张)
└─ tools/              联调与静态检查：e2e-test / timeout / clock / firstturn / riichi-stale /
                       claim-priority / discard-align / seat-swap / utf8 / replay-test /
                       selfplay-check(训练数据集校验，见 §6.5) / check-i18n / i18n-scan / i18n-map
                       + i18n-apply + i18n-gen（见 §6）/ qt-provision.ps1 / mock-server
                       / gen-tile-placeholders / inline-svg-style / dump-otf-features
                       / gen-sfx + gen-sfx-qrc（见 §9.3）/ package-release.ps1（发布打包，见 §9.5）
                       / gh-push-payload.ps1（git 通道不通时的 REST 推送载荷，见 §9.5）
```

---

## 6. 代码约定

### 6.1 通用流程与产物

- **注释用中文**，解释「为什么」而不是「做了什么」。
- **协议改动流程**：先改 `docs/PROTOCOL.md` → 再改服务端 → 再改客户端 → 补两侧测试。
- **新增规则判定**：写进 `rules/`，在 `server/src/main/java/mahjong/test/SelfTest.java` 加断言。
  **两侧断言参数顺序不同，别写混**：

  ```java
  // 服务端：check(名称, 条件)  /  eq(名称, 实际, 期望)
  check("自摸时下发 tsumo 选项: " + list, list.contains("tsumo"));
  eq("连风雀头符", s.fu, 40);
  ```

  ```cpp
  // 客户端：check(条件, 名称)  /  checkEq(实际, 期望, 名称)
  check(tiles.size() == 37, QStringLiteral("牌种数量应为 37，实际 %1").arg(tiles.size()));
  checkEq(QString::number(nSide), QStringLiteral("1"), QStringLiteral("一人牌河横置张数"));
  ```
- **牌面素材走 SVG，可替换**：`client/assets/tiles/<牌码>.svg`（38 个 = 37 种牌 + `back.svg`），构建时拷到
  exe 同级 `tiles/`；加载顺序 **目录 → qrc → 程序化绘制**（删掉整个 `tiles/` 也不白屏）。替换只需保持
  **文件名 = 牌码**与 `viewBox="0 0 300 400"`，**重启客户端即生效、无需重编译**（占位符见 §9.1）。
- **结算界面走内嵌字体 + OpenType**：`I.MahjongJP.otf` 的 `liga` 连字默认生效、**无需任何 OpenType API**；
  字体取不到或串含非法字元时**整块不显示**、回退文字（详见 §9）。
- **UI 扁平化**：纯色 + 1px 描边，**牌片单层外框、不画厚度层**，不用渐变/阴影/发光（`TileRenderer` 的程序化绘制只作**回退**）。
- **文档分工**：`README.md` 面向玩家（操作、规则取舍、常见问题），**不含技术细节**；技术内容一律进
  AGENTS 与 `docs/`。**改了玩家看得见的行为（操作、开关、规则取舍、常见问题）就要同步 README。**
- **第三方许可**：客户端以 **LGPLv3 动态链接**使用 Qt，`client/licenses/` 随 `dist/` 一起分发
  （内含 LGPLv3 + GPLv3 全文与版权声明）。**不要删它，也不要给 Qt 的 DLL 加校验/签名锁定**
  —— 那会阻止用户替换 Qt，直接违反 LGPLv3。新增 Qt 模块或第三方库时同步 `docs/THIRD-PARTY.md`、
  `licenses/NOTICE.txt` 与 `build.ps1` 的必需 DLL 清单。

### 6.2 牌桌布局与绘制（客户端 —— 最容易被改坏的一块）

- **手牌区不能抖**：`TableView::layoutHand()` 的手牌左缘锚点按**满手 13 张**算（与当前张数无关）
  → 摸牌 / 打牌都不位移；**摸牌紧贴手牌**（独立间隔 `0.26 × 牌宽`），手牌因副露变短时摸牌一起左收；
  副露钉在**各家自己的右下角**（`+u` 行末）：**最旧的在最右、之后依次向左**（从右到左 = 旧→新），
  顺序算在 `meldLeftsOf()` 里（自检有断言），**不要**在绘制处改成从左往右排 —— 那个写法与
  「`meldRight` 固定、`meldLeft = meldRight − groupW`」的约定矛盾（踩过）。一副副露**内部**仍左→右，
  被鸣那张按来源方位落位；整块若会压到副露则**一起左移**（避让优先级最高，绝不能被角落钳制的负值推回），
  绝不重新居中。名牌沿对角线向内收进拐角空隙（`1.9 × 牌高`）。
  `ActionBar` 同理固定高度，否则选项数变化会改控件高度、导致整桌重新布局。
- **风盘尺寸必须由内容反推，不能先定一个「好看的方块」**（牌河过小的根因）：
  `computeLayout()` 先按**盘内**要放的东西（宝牌指示牌行 = 5 张、四家点数、场次、立直棒）
  与**盘外**要留的东西（四家牌河各 3 行 + 一点余量）算出 `cw/ch`，**然后**才钳制、**最后**把 `ch`
  补到接近 `cw`（宽度被「牌河一行 6 列」撑着，纯按内容算出的高度总偏矮 → 盘偏扁）。
  补高只影响盘内留白与牌河起步距离，预算里已算进 `riverExtent`，**不会挤到手牌**（自检有断言）。
  河区与手牌之间的余量 `riverPad` 用 `0.35 × 牌河牌高`（曾用 0.55，白吃掉几十 px 高度、是盘偏扁的元凶之一）。
  尺寸比例（相对手牌宽 `tw`）：
  | 元素 | 比例 | 说明 |
  | --- | --- | --- |
  | 手牌 | 1.00 | 基准 |
  | 牌河 / 副露 | **0.798**（`0.84 × 0.95`） | 读牌清楚；曾被旧逻辑压到 0.45 |
  | 宝牌指示牌 | **0.76**（`0.95 × 0.8`） | 与牌河同量级；`0.95` 那版用户反馈太大 |
- **牌河一行最多 1 张横置牌**：只有立直宣言牌横置，且每家至多 1 张。所以最坏一行 =
  横置牌 + 5×普通牌 + 5×列间距，**不能按「整行全横置」估**（会多要一倍宽度、把牌河压掉一半）。
  列间距 `kRiverColGap = 0.09`、行距 `kRiverRowGap = 0.08`（都相对牌河牌宽/高）——
  这两个常量在 `computeLayout()` 的钳制与两处绘制里**必须用同一份**，否则预留范围与实际排布会错位。
  ⚠ **行数不封顶**：一行 6 列，18 张（3 行）排满后第 19 张起继续排第 4、5 行（`riverRowsFor(n)`）。
  曾经绘制处取 `qMin(kMaxRiverRows, …)` → 第 19 张及以后的牌**整张不画**（报障）。
  布局用**同一函数**按四家最大值预留（`m_riverRows`）—— 两者必须同源，否则要么丢牌要么压牌。
- **牌河的横向起点是「固定左缘」，不是「按当前张数居中」**（用户报障过**两次**，别再把这两件事混为一谈）：
  - 第一次改的是**行与行之间**不齐（旧版每行按自己的宽度居中，行与行参差、读牌费眼）→ 所有行共享同一左缘；
  - 第二次改的是**左缘本身会动**：按「本帧已打出的最大列数」算左缘，只打 1 张时那张落在牌河带**中间**，
    每多打一张整条牌河往左挪一点，打满 6 张才落到最左 —— 第一张的位置自己会走，看着就是"没左对齐"。
  - 现在的口径：**先假定一行排满**（**6 张普通牌 + 5 个列间距**，⛔ **不把横置牌的额外宽度算进左缘**），
    算出它的最最左沿，所有行、所有张数都从那条线**向右**排。实现在 `TableLayout::riverFullRowExtent()` /
    `riverLeftU()`，与已经打了几张**无关**；绘制（`paintRiver`）与飞行动画终点（`riverSlotScreen`）
    必须读**同一个** `riverLeftU()`。
    - ⚠ 「横置牌不算进左缘」是用户二次明确的口径：横置宣言牌占的是**牌高**（≈1.36 倍牌宽），
      算进去的话**每一行**（哪怕整局没人立直）右侧都会空出那一截、整条牌河看着偏左。
      代价是**立直那一行会向右多出 (牌高 − 牌宽)** —— 这是**允许**的（一行最多 1 张横置，偏移量很小，
      且只影响立直家自己的河）。`computeLayout` 里的预留（`riverRowNeed`）**仍按最坏情况**算，
      所以这截溢出落在盘外那圈预留里、不会压到别家。
  - 自检用**局部坐标**断言（`TableView::riverSlotLocalForTest`）：屏幕坐标带着四家的旋转，
    「左沿」在左右两家那里其实是上下方向（`pos=1` 的 `toScreen` 把局部 u 映射到屏幕 y）。
    回归：`client --selftest` 的「牌河固定左缘」组（1/3/6/7/13/18 张左缘不变 + 三行行首同源 +
    普通行正好铺满 + 立直行恰好多出 (牌高 − 牌宽) + 含旧位置的红证）。
- **风盘区带：四边同构**「立直棒带（贴盘边）+ 点数带（在其内侧）」+ **宝牌行** + 中间（场次·余牌·供託）。
  - **四家点数到风盘边缘的距离必须处处相等**（= `pad + stickH + stickGap`，自检四条断言钉住），
    所以上下两条横排带、左右两条竖排带的**外侧**都留了同样厚的一条立直棒带。
  - **四家的得点框必须一样大、并且沿各自那条边居中**：上下两条带横跨盘内整个宽度、
    左右两条是细长竖排带，按各自的带长画框会差一倍 —— 框长统一取
    「四家里最长的分数文本 + 内边距（7px×2）+ **三位数字的宽度**」，四边都用它
    （横排两家与竖排两家只是朝向不同；再留三位数字是用户要求，点数涨到十几万也不挤字）。
    「居中」= 上下两家水平居中于盘中轴、左右两家**垂直**居中于盘的水平中轴。
    ⚠ 左右两家能被宝牌行顶偏：宝牌行只占**中间列**，与左右竖排带在 x 上不相交，
    所以左右两条带要**用满**「上点数带下沿 → 下点数带上沿」（两端各留同样的 6px），
    带中心才落在盘中轴上。自检断言四个框尺寸完全相等 + 四条居中断言。
  - **立直棒放在各家自己面前**（报障：原来把四家的棒统统堆在盘底，看着像都是自家的）：
    按座位取 `m_stickBand[pos]` 画，左右两家的棒**竖放**（与那两家竖排点数的朝向一致）。
    多出来的（= 上一局留下、**不属于任何一家**的供託，客户端拿不到"上一局是谁立的直"）
    画在盘中央「供託 N」那行下面，**别塞给某一家**；一排摆不下就少画几根（别伸出中间列）。
    棒的长厚比 `kStickRatio = 4.4`（**各家面前的棒与盘中央供託棒共用**，3.6 那版用户反馈偏短）。
  - 区带矩形在 `computeLayout()` 里算好并存成数组（`m_doraRect` / `m_scoreBand[4]` /
    `m_stickBand[4]` / `m_centerBand`，下标与座位方位同号 0=下 1=右 2=上 3=左），
    `paintCenter()` 只负责按带中心画 —— **不要在绘制处再按 pad 现算**，否则带一变点数就漂。
  - ⚠ 宝牌行在**対面点数下方**，所以**中间「场次」块的顶边**（`midTop`）必须取
    `qMax(対面点数带下沿, 宝牌行下沿)`；只改区带顺序而不改这一处，场次首行（東1局）会被宝牌牌面盖住
    —— 这正是「宝牌行搬到対面点数下方」时踩到的坑（自检有「场次区带不得与宝牌行相交」断言兜底）。
    但**左右两条竖排点数带不要跟着取这个 `qMax`**：宝牌行只占中间列，与竖排带在 x 上不相交，
    跟着取会把两条竖排带往下顶、左右两家点数看起来偏下（用户报障过）。
  - **宝牌指示牌行只占「中间列」**（左右两条竖排点数带之间），**并且不再画「宝牌」字样**：
    旧版那行横跨整个宽度、左边还挂标签，5 张牌会伸到四家框外。
    现在整行居中于中间列，列不够宽时**宽高同缩**（`dTileH = min(标称, 纵向余量, 列宽 × 1.36)`），
    所以「五张指示牌永远包含在四家分数框以内」。风盘宽度据此反推：
    `innerNeed = max(牌河一行宽, 2 × (棒 + 间距 + 点数 + 6) + 五张指示牌宽)`，再按 `pad = 3%` 折算 `cw`。
  - `chContent`（风盘高度由内容反推）里**上下各要算一条「棒 + 点数」带**：只算一条的话，
    加了立直棒带就会去挤宝牌行（`doraAvail` 同一笔账）。
- **宝牌指示牌在风盘内、紧贴対面点数下方**（不再占屏幕左上角），一局最多 5 张
  （宝牌 1 + 四个杠各 1）。尺寸 = `手牌宽 × 0.76`（与牌河解耦，见上面的比例表）。
  绘制时**宽高同缩**才保得住牌的形状：`tw = min(行高/1.36, 可用宽/张数)`，`th = tw × 1.36`。
  只压宽度会把它拉成瘦长条（用户实测「不符合牌的形状」）。
- **河区预留范围 = 风盘向外等距扩一圈**（**只算布局，不绘制** —— 那圈浅白线框已按需求删除）：
  `riverExtent = kGap + (riverRows−1)×行距 + 牌河牌高 + riverPad`（`riverRows` 见上一条：正常 3，
  有家超过 18 张就更大），四边加**同一个量**，所以它与风盘各边等距、且恰好套住这么多行牌河。
  它算好存成 `m_riverArea`，受外圈预算约束：
  `盘半 + riverExtent ≤ 屏半 − 内边距 − 手牌厚`，**绝不能压到四家手牌**（自检有断言）。
  ⚠ **删线框时不要连预算一起删**：牌河真的占那块地方，预算没了风盘就会长到让四家的河压上手牌。
- **牌河不得压到邻家的河**：相邻河从「盘半 + 间距」起步，两者相交要**两个方向同时**重叠，
  所以界限用 `max(cw,ch)/2 + kGap`（**不是 min** —— 盘做成宽扁形后用 min 会平白再压小一轮）；
  最坏一行的算法同前（横置牌 + 5×普通牌 + 5×间距）。这条钳制现在只是**兜底**：
  正常尺寸下按上面的反推，牌河能拿到标称大小。
- **副露的几何只有一份**（`TableView::meldSlotRects()`）：**绘制与自检共用**它
  （`meldSlotRectForTest`）。两条用户口径：
  - **三张牌底部齐平**：横置那张（宽 = 牌河牌高、高 = 牌河牌宽）的顶边是
    `my + (riverH − riverW)`，**不是** `(riverH − riverW)/2` —— 后者让它"浮"在副露中间。
    ⚠ **牌河里的横置牌不改**：那是网格里的一格（`paintRiver` 里按高度居中，注释写明了原因）。
  - 加杠第 4 张**叠在第 1 格**（不占新槽位），宽度按三格算。
- **名牌（ID 框）四角轮转一位**（`TableLayout::computeLayout()`，2026-09 用户口径）：
  自家在**右下**，其余三家跟着转一格（下家→右上、对家→左上、上家→左下）。
  ⚠ 四个角**必须各占一个**：只挪自家会与下家的名牌重叠。角落预留 `plateReserve` 在
  **同一端**，所以名牌挪到哪、牌河/副露就在哪让开（自检断言四家名牌互不重叠 + 各自象限）。
- **音效是"池子 + allowOverlap"，不是"一个对象反复 stop+play"**（`model/Sound.cpp`）：
  每个音效 3 个 `QSoundEffect`，优先用**空闲**实例（绝大多数情况**根本不需要 stop**）；
  `allowOverlap=false`（摸牌那条路）时**整个音效还在响就跳过**。
  ⚠ 报障「只有第一小局有音效」的现场实测是：客户端**每局都在播**（10 局 80 次 play，
  Qt 侧 status=Ready、`isPlaying()=1`），所以剩下最可疑的就是旧实现那条
  `stop()+play()` 热路径（而且它**忽略了 `allowOverlap` 参数**——调用方明确要求"别叠"）。
  排查工具：`MAHJONG_SFX_TRACE=1` 跑一局，stderr 会打出每个音效的
  开关/可用/音量/池子状态/`play()` 之后是否真的 playing。
- **「手牌 + 摸牌」块的边界避让：一次算完 + 右移封顶**（同一个坑踩了三次，别简化）：
  - `layoutHand()` 里只允许**一个** `over`，且必须先取 `max`（①副露 ②角落名牌）再让 ③行首角落让步。
    分成两段钳制时，后一段会算出**负的 over**，`handLeft -= over` 等于把整块往右推，把前一段让出的空间又吃回去。
  - ③ 的右移量必须**封顶**，且封顶要同时看两边：`room = uLimit − 行末端`，
    有副露时再取 `min(room, meldLeft − 行末端 − meldGap)`（副露优先级最高）。
    否则这一推会把刚让开的空间又吃回去（副露盖住摸牌、牌盖住宝牌都是这么来的）。
  - 块宽到两个边界都满足不了时，**宁可压名牌，也绝不压副露**。
- **座位方位**：`pos = (seat - mySeat + 4) % 4` → `0=下(自己) 1=右(下家) 2=上(对家) 3=左(上家)`。
  每家在自己**局部坐标系**里绘制再整体旋转到屏幕——这样侧家的牌自然横置、
  「横置以牌主视角判定」自动成立（邻家非横置牌在本家看来是横的，横置牌是竖的）。

### 6.3 牌桌行为、规则与资源

- **牌桌外的三个自动开关（`AutoBar` + `autopolicy::decide`）：自动胡了 / 不吃碰杠 / 自动摸切**。
  按钮在牌桌下方（**不进 ActionBar** —— 那里按钮动态增删、高度固定），文案全在 `ui.auto.*`。
  判据**只看 `ask.options`**（客户端不自己算听牌，见 §2.1），且**和牌永远优先**：options 里有
  `tsumo`/`ron` 时**只有「自动胡了」**能替玩家动 —— 自动摸切绝不切出能胡的那张牌、
  不吃碰杠绝不把到手荣和 `pass` 掉（用户点名的要求）。自动动作也必须走
  `ActionBar::actionCmd/discardCmd`（带 `ask_id`；询问失效时它们返回空对象 → 自动动作自动作废）。
  **每小局结束（`round_end`）立刻 `resetAutoFlags()` 全关**，且**每小局开始（`round_start`）
  再复位一次** —— 一小局两次（用户要求：局间结算期间点开的自动不许带进新的一局）。
  ⚠ 这条**口径被用户改过**：原文是"只在 `round_end` 清，别改到 `round_start`"（担心吞掉
  玩家在局间为下一局准备的设置）。现在是**刻意**两次都清；再看到 `round_start` 里那句
  `resetAutoFlags()`，不要当成 bug 删掉。
  回归：`client --selftest` 的「自动开关」两组（含命令钩子抓真实报文）。
- **「吃」必须由服务端自己校验顺子**（`Round.pickChiTiles`）：`want` 来自客户端，
  只查"手里有没有这两张"的话 1m+5m 能配 3m 吃下去，而 `Evaluator` 是按 `Meld.baseKind()+isRun()`
  **重建**面子形状算役的 → 假顺子按真顺子计分（一条报文就能改分）。`want` 还必须**恰好两张**：
  越界写 `int[2]` 会 AIOOBE，异常冒到牌桌线程会让整场半庄**静默死亡**（连 `round_end` 都不发）。
  回归：`SelfTest.chiValidationTests`。同理，`Table.playGame` 的异常兜底必须**广播终局**再收尾。
- **接收侧的资源上限**：单条报文 1 MB（`Session.readBoundedLine` **边读边判**，绝不能等
  `readLine()` 收完再判）、同时在线上限 `Server.MAX_SESSIONS`、`fill_bots` ≤ 4、
  客户端传来的 `rules` 一律过 `Rules.clampToSane()`；**发牌种子**用 `SecureRandom` 基准 +
  每局 SplitMix 打散，且**每小局都从"当前时刻毫秒数"重新起步**（`Table.nextRoundSeed()`）——
  原来每局只有 `mixSeed(seedBase + 局序号)`，同一个 `seedBase` 推出来的一整场是确定序列
  （推出一局即推出整场）。自检要可复现，所以它显式打开 `Table.debugDeterministicSeed`
  走旧的确定岔路；**生产路径永远是时刻种子**，别把那条岔路当默认行为。
- **局间时序：服务端先等满 5 秒（或所有人确认），再开下一局**：
  `round_end` → `sleepMs(roundDelayMs)` → `awaitRoundConfirm()`（广播 `round_wait{ms:5000}` 后等）
  → 下一局 `round_start`。**服务端侧两条铁律**（都踩过，见 §2.3-8）：
  - `drainConfirm()` 必须从 `submit()` 写入的**那一条队列**（`responses`）里取 `confirm`，
    取到就 `roundConfirmed[s] = true`，非 confirm 的消息**按原顺序放回**；
  - `awaitAction()` 认领答复时必须**确认它是动作**（有 `type`），否则残留的 `confirm` 会被当成出牌。
  客户端配合：
  - 结算弹窗收到 `round_wait` 时启动**倒计时**并在盘面上显示「N 秒后自动开始下一局」；
    到点**自动关闭弹窗**（关闭即 `confirm`），不必等玩家点按钮。
  - `round_start` / `game_end` 到达时**强制关闭**残留的结算弹窗，但**不能发 `confirm`**
    （服务端已经推进了；多发的那条会留到下一次局间被消费，把那次 5 秒等待直接吞掉）。
    这就是 `closeResultDialog(confirm=false)` 的用途。
  - 弹窗已经关掉时才立刻替玩家 `confirm`（看完了不必干等；现在这条真的能提前开下一局）。

### 6.4 协议、文案与结算

- **报文里不带中文：词汇性文本一律只发 ASCII 码，中文文案只存在于客户端语言文件里**。
  两端都显式指定字符集（服务端 `StandardCharsets.UTF_8`、客户端 `QJsonDocument` 的 UTF-8），
  不依赖平台默认 —— 这一条仍然要守，因为**用户数据可以是任意文本**：
  - **只有三类字段是用户数据**：`seats[].name`（玩家名）、`room.name` / `hello_ok.name`、`chat.text`。
    其余全部是码：`yaku[].code`（+ 参数化役种的 `yaku[].tile`）、`limit`、`reason`、`error.code`、
    `error.arg`、事件名、`type`、`win_note`、牌码。
  - **码表在 `server/.../rules/YakuCodes.java`**（中文名 → 码的**唯一**映射处，含役种/打点/流局原因）。
    `Evaluator` 内部仍旧用中文名（日志、自测好读），只有 `Round`/`Table` 发报文时才翻成码。
    ⚠ **新增役种必须在这里登记**，否则自检的整场模拟会因 `YakuCodes.misses() > 0` 失败
    （表长还有一条精确断言兜底）。
  - **客户端绝不能拿中文串做逻辑判断**（那是把服务端文案当协议常量）：判据一律用 ASCII ——
    事件名、`type`、`win_note`、牌码、`yakuman`（数字）。做多语言/换文案只改资源文件。
  - **客户端语言文件** `client/assets/i18n/<locale>.json`（键 = `<族>.<码>`）：加载器
    `client/src/i18n/Lang.{h,cpp}` 提供 `lang::t()`（文案）/`lang::code()`（协议码）/`mj::tileLabel()`（牌名）。
    加载顺序与 `tiles/`、`fonts/` 同约定（**exe 同级 `i18n/` → qrc → 返回码**）→ **改文案不用重编译**。
    ⚠ 认不出的码**原样显示码本身**（不是 `yaku.xxx` 裸键、也不是空串），这样"服务端加了码、语言文件没跟上"一眼可见；
    码为空串时回退老字段（`yaku[].name` / 原样 `limit` / `error.msg`），新旧两端混跑不显示空白。
  - **界面固定文案也全在 `ui.*`（289 条）**：按钮/标题/标签/tooltip/结算 HTML 的中文都在语言文件里，
    代码里只留 `lang::t("ui.…")`，**源码里不该再有中文字面量**。例外用 `// i18n-keep` 就地豁免
    （牌面字形「萬」「東」、立直标记「立」、默认玩家名、隐藏的测量按钮）；日志与自检输出不进语言文件。
    搬运三件套（同一份映射，**不会漂移**）：`i18n-map.mjs`（字面量 → key 的**唯一数据源**）→
    `i18n-apply.mjs`（把 `QStringLiteral("…")` 换成 `lang::t("…")`，匹配不到会报未命中）→
    `i18n-gen.mjs`（写进 `zh_CN.json`，**只补缺**）。**改文案直接编辑 json**，别反过来改代码。
    校验：`i18n-scan --check`（源码不许剩中文）+ `i18n-gen --check`（不漏 key）+ `check-i18n`（词表不漏）。
  - **截断按「码点」不按 UTF-16 码元**：玩家名 24 / 聊天 200 走 `Json.clampCodePoints()`。
    直接 `substring` 会把代理对（`𠮷`、`🀄` 这类 BMP 之外的字符）切成半个 →
    写出时**静默变成 `?`**（实测：199×`a` + 🀄 按码元截 200 就切在这里）。
  - ⚠ Java 陷阱：**注释里也不能写「反斜杠 + u」**（哪怕是想举例 `\uXXXX`）——
    javac 在词法分析**之前**就处理 Unicode 转义，非法序列直接编译失败（踩过一次，见 `SelfTest` 里那段注释）。
  - ⚠ 改客户端源码时**不要用 `QLatin1String` 接中文字面量**（会把 UTF-8 字节按 Latin-1 解成乱码）；
    中文一律 `QStringLiteral` / `QString::fromUtf8`。
  回归：`SelfTest.jsonEncodingTests` / `yakuCodesTests` + `client --selftest` 的 Lang 组
  + `node tools\check-i18n.mjs`（词表静态核对）+ `node tools\utf8-test.mjs`（真 socket 往返）
  + `node tools\e2e-test.mjs`（逐条报文的非 ASCII 审计，白名单只有 `name`/`text`/`msg`）。
- **结算界面：役满写「n倍役满」，不写番数**。役满**没有番这个量纲** —— 规则是
  「成立 n 种役满役则基本点 = 8000n」，符数与普通番数全部失效（`docs/日本麻将.md`）。
  所以：役种行里带 `yakuman` 的写成 `n倍役满`，**合计那行也只报倍数**（不写「0 番 0 符」）。
  - 服务端报文里 `yaku[].han` / 合计 `han` **不能是 0**（老客户端会显示成「0 番」）：
    按「役满 = 13 番等价」折算成 `13 × 倍数`（`Evaluator.Yaku.equivalentHan()` /
    `HandScore.totalHan()`），于是**逐役之和 == 合计**；真正的量纲在 `yakuman` 字段。
  - **累计役满**（番数 ≥13 但没有役满役，`yakuman == 0`）**不是役满**：照常显示番数与 `limit`。
  - 界面判据一律看 `yakuman` 字段，**不要**用 `han >= 13` 去猜（累计役满会被误判）。
  回归：`client --selftest` 的 ResultDialog 组（4 种情形）+ `node tools\mock-server.mjs … yakuman` 截图。
- **结算界面：能用图形表示的就不再重复文字**。`agariHtml()` 只在
  `schematicRenderable(schematicOf(...))` 为假（字体缺失 / 牌码串非法）时，
  才补「手牌 / 宝牌指示牌 / 里宝指示牌」三行文字。
  该判据与构造函数显示图形块的判据**必须共用同一个函数** —— 否则会出现
  「文字删了、图形也没画」的信息真空。**数字类信息（宝牌/赤宝/里宝 张数、番符、点数收支）照常保留**
  （图形给不出这些数）。
- **鸣牌仲裁：优先级、提前收工、队列卫生**（三条一体，见 §2.3-10）。
  优先级 = 「荣和 > 杠 = 碰 > 吃」，**同级看座次**（离打牌者近的赢）——
  这一把尺子在两处**必须一致**：`RoundClaims.rankOf/canBeat` 与 `Round.claimPhase` 末尾的仲裁循环。
  - **提前收工**：`RoundClaims.shouldStop` 除了「都答完 / 超时 / 荣和都答了」，
    还要看「已到手的最优鸣牌没人能压过」→ 立刻收工，绝不为了等一家"能吃但没点"而拖满整个窗口。
    - ⚠ 判据里 `askedBestRank` 缺项时**保守地继续等**（旧行为），不能当成"没人能压过"。
  - **队列卫生**：收工后仍在 `asked` 里的座位要 `cancelAsk` + `pendingAsk.remove` +
    **`table.dropReplies(seat, cancelledAskId)`**（摘掉已在队列里的废包）。
  - **类型校验**：`Table.awaitAction(..., allowedTypes)` 与 claimPhase 的 `askedTypes`
    都要求 `type ∈ 本次询问下发过的 option.type` —— 这是唯一能识别"没有 ask_id 的废包"的判据。
  回归：`SelfTest.roundClaimsTests` / `dropRepliesTests` + `node tools\claim-priority-test.mjs`。
- **王牌 14 张 = 4 张岭上 + 5 张表宝牌指示牌 + 5 张里宝指示牌**（`dead[0..3]` / `dead[4..8]` / `dead[9..13]`）。
  每开一次杠从岭上摸一张（一局最多 4 次，`dead_wall_left` 4→3→2→1），同时翻一张新宝牌指示牌。
  **两条账必须分清**：
  - **开杠的那一刻**：牌山末尾一张被移进王牌补位（`liveEnd--`）→ `tiles_left` 减 1；
  - **杠后岭上摸牌**：那张来自王牌，**`tiles_left` 不动**。
  所以「岭上牌有没有被摸走」**只能看 `dead_wall_left`**（见 §2.2），而它**必须每次摸牌都下发**——
  只在小局开始发一次的话，界面上的「岭上 N」会整局停在 4（报障：「杠后岭上牌并没有减少」）。
  - ⚠ **机器人现在会开杠了**（`Bot.shouldKan`，2026-09 补的策略层；见 §6.6），
    但 `Bot.debugAlwaysKan = true` 仍然是**强制开杠**的自检开关：`rinshanTests` / `kanLimitTests`
    靠它构造"三种杠各开几次 / 废杠不白拿"，与策略无关地覆盖整条岭上路径。
    自检用 `Table.debugEventTap` **抓真实报文**断言（`SelfTest.rinshanTests`：三种杠各出现几次 /
    各自之后摸到几次岭上牌）。
  - **一局最多 4 次杠**：岭上只有 4 张，第 4 次之后 `Round.canKan()` 为假 → 出牌段与鸣牌段
    **都不再下发 `kan` 选项**（客户端按 options 画按钮，不下发就点不出来）。
    判据两条一起看：`rinshanPos < 4`（王牌里还有岭上）**且** `kanCount < 4`（本局杠数没到 4）
    —— 四个闸门（出牌选项 / 鸣牌选项 / 仲裁等级 / 认领校验）统一走 `canKan()`，别各写各的。
  - ⚠ **被拒绝的杠绝不能摸到岭上牌**：`"kan"` 是一条要兑现的动作（牌不在手里 / 动作过期 /
    名额已满），没兑现就必须退回**默认摸切**（与「立直不成立」同一条兜底）。
    曾经无条件 `rinshanNext = true` → 每条废杠都从王牌白抽一张，还把 4 次名额提前吃掉
    （`rinshanPos` 跑到 `kanCount` 前面，第 4 次真杠反被挡住）。红证：把兜底改回去，
    自检立刻报「岭上摸牌 46 次 vs 有效杠 11 次」。
  - 回归：`SelfTest.rinshanTests`（岭上账）+ `SelfTest.kanLimitTests`（4 次上限 + 废杠不白拿）+
    `client --selftest` 的岭上组 + `node tools\mock-server.mjs <port> kan`（截图看「岭上 N」）+
    `node tools\e2e-test.mjs`（真 socket 的岭上账审计；脚本客户端会主动开杠以覆盖这条路径）。
  - ⚠ 自检要看**选项**时用 `Table.debugAskTap`（它挂在 `Round.ask()` 里）：**机器人座位的询问不走网络**，
    整场机器人模拟里一条 `ask` 报文都没有 —— 用 `debugEventTap` 抓 `ask` 会**静默空转**（这个坑真绊过一次）。
    ⚠ 但它**看不到鸣牌**（鸣牌段不走 `ask()`）；两段都要看就用 `Table.debugChoiceTap`（挂在决策漏斗上，见 §6.5）。
  - **四杠散了**（`Round.fourKanAbortNow()`）= 「本局杠数已 4 **且**不是同一人所开」。三种豁免
    （第 4 次杠后**岭上开花** / 被**抢杠** / 岭上牌**放铳**）由**和了路径先 `return`** 天然满足，
    所以判据只在"那张牌已经落地、且没人因它和牌"时被问到 —— **别把它提前到开杠那一刻**，
    那会把三种豁免一起吞掉（细则见 `docs/日本麻将.md` §四杠散了）。
  - **赤宝牌张数**：`Wall(seed, rules)` 在 `rules.aka == 0` 时**发牌期就把赤五换成普通五**
    （`stripRedFives`），于是"看到的"与"计分的"一致；`aka == 4`（两张赤五筒）受牌 id 编码
    限制仍按 3 张处理（见 §8）。
- **服务端并发模型**：**每桌一个线程串行推进状态机**，对局内的客户端命令只往队列投消息，所以
  **对局逻辑内部**无需加锁。⚠ **但这句只管对局内**：等待室的命令（`create/join/leave/ready/
  take_seat/shuffle_seats/start_game/add_bot/remove_bot`）是**各连接自己的线程直接执行的**，
  它们读写的却是同一份座位数组 —— 所以房间状态有**一把锁** `Table.roomLock`（可重入）：
  - 等待室命令把「判据 + 改座位 + `broadcastRoom`」放进 `synchronized (t.roomLock())`；
  - 牌桌线程在**同一把锁**里把 `playing` 置真（`Table.run`），这样 `take_seat` 的
    `if (playing)` 与真正的换座之间**不可能**插进"开始发牌"（否则按座位号发的牌会落到别人连接上）；
  - `swapSeats` / `shuffleSeats` / `broadcastRoom` 内部也各自持锁（锁可重入，嵌套调用没问题）。
  ⚠ 另一条独立 bug（不是竞态）：换座只更新**发起者**的 `session.seat`，被换走的那家还是旧值 ——
  于是他点「准备」会把**别人**设成已准备（探针实测：B 的准备落在 A 的座位上，B 自己一直不准备，
  牌局永远开不了）。修法：`Table.resyncSessionSeats()`（改过座位住户的地方都调它）
  + 等待室命令按 `Table.seatOfSession(this)` **反查**自己的真实座位，不信 `session.seat`。
  回归：`node tools\seat-swap-test.mjs`（换座/洗座后的准备落位 + 并发 churn 下的座位表排列不变式
  + 牌局中 take_seat 被忽略）。

### 6.5 训练接口（机器学习 / 自对弈）

**权威描述在 `docs/PROTOCOL.md` §8**（字段表、动作键文法、CLI、数据格式）。这里只列"改代码时必须守"的几条。

- **只有一个决策漏斗**：`Table.decideBot(seat, Decision)`。自家摸打（`Round.ask`）与鸣牌段
  （`Round.claimPhase` 里那段 bot 分支）都汇到它，`Table.policy[seat] == null` 时走内置 `Bot`。
  **不要**再往第三个地方直接调 `Bot.decide` —— 那样注入的策略就漏了一段。
- ⚠ **鸣牌段不走 `Round.ask()`**（它自己组询问、自己收尾）。所以：
  - `Table.debugAskTap` **看不到鸣牌**（它只挂在 `ask()` 里）。要看两段必须用 `debugChoiceTap`；
    这个坑真绊过一次：探针第一次跑就报"鸣牌询问 0 次"，差点被当成"机器人不鸣牌"。
  - 任何"每次询问都要做的事"（记录、埋点、统计）都得挂在漏斗上，别挂在 `ask()` 上。
- **策略不许把异常抛给牌桌线程**：`decideBot` 必须兜底（异常 / 返回 `null` → 内置机器人）。
  一局里异常冒到牌桌线程会让**整场半庄静默死亡**（连 `round_end` 都不发，见 §6.3）。
- **训练侧只实现 `ActionPolicy`（拿不到 `Round`）**：`Round` 里能读到别家手牌、牌山顺序、
  里宝指示牌 —— 进程内一不留神就训出**作弊**模型，而且训练与评测**同时**失效、还查不出来。
  `Policies.fromAction` 还会把"不在本次 `legal` 里的动作"挡回内置机器人。
- **观测只许含合法信息**，新增字段必须：① 确认它公开可见；② 加进 `Observation` 的字段白名单断言
  （`SelfTest.trainingInterfaceTests`）**和** `tools/selfplay-check.mjs` 的 `OBS_KEYS`；
  ③ 同步 PROTOCOL §8.2 的表。三处都改才算改完。
  - ⚠ 自家回合**别调 `Round.isFuriten()`**：14 张手牌时它恒等于 `furitenTemp || furitenPerm`，
    但内部会白跑 34 次向听 DFS（热路径）。
- **可复现是硬要求**：自对弈/评测必须 `debugDeterministicSeed=true` + **每局一份策略实例**
  （`PolicyFactory`）+ 每场种子**预先算好**（`SelfPlay.seedFor`）。策略实例跨局带状态
  （随机源、缓存）会让同一 seed 不再产出同一轨迹，配对评测随之失效。
- **奖励是事后回填的**：新加统计量时记住 `hand_delta`/`placement` 是在小局/整场结束时补的，
  不是决策那一刻就有。`placement` 必须是 `1..4` 的排列（同点按座次拆开），否则"平均顺位"没有意义。
- **不要给服务端加 ML 依赖**（Maven/ONNX/PyTorch）：训练在外面做，权重导进来用**纯 Java 手写前向**。
- **改了产出格式就三处一起改**：`TraceRecorder` / `docs/PROTOCOL.md` §8.4 / `tools/selfplay-check.mjs`，
  并重跑 `node tools\selfplay-check.mjs <dir>`（它会用独立实现核对；红证：把 `chosen` 改成非法动作、
  删掉一个 `hand_delta`，都必须判 FAIL）。
- 回归：`SelfTest.trainingInterfaceTests`（动作空间往返、观测反作弊不变式 + 正向对照、
  策略三种失败方式兜底、同种子可复现、注入真的改变行为、runner 统计自洽）+ `tools\selfplay-check.mjs`。

### 6.6 teacher（内置机器人）的五层取舍

详细设计见 `docs/DESIGN.md`「teacher（内置机器人）的取舍」；这里只列改它时必须守的：

- **改 teacher = 改训练标签**。行为克隆学的是它的行为：teacher 一改，**之前生成的数据集就作废**
  （要重新 `--selfplay --out`）。所以改它的行为要单独说明，别混在"顺手重构"里。
- **取舍判据必须是只吃公开信息的静态纯函数**（`Bot.chooseDiscard` / `hasYakuPlan` /
  `shouldDeclareRiichi`，输入是 `Bot.HandState`）。理由：① 自检能**直接构造局面**断言取舍
  （"有人立直时该打现物""无役的碰要放掉"），不必跑一整局碰运气；② `HandState.of` 是唯一读
  `Round` 的入口，从类型上挡住"顺手读别家手牌"（回归：置换不变式）。
- **弃和只看立直家**（`Danger.worstAgainstRiichi`）：没人立直的对手"手里是什么样"无从判断，
  对四家取最坏会让每张牌一样危险、把现物的价值淹掉。进攻才用 `Danger.worst`。
- **鸣牌前必须查役**（`hasYakuPlan`）：鸣牌打掉门清=打掉立直，鸣完无役这手永远和不了。
  只认四种可靠计划：役牌 / 断幺九（食断规则下）/ 混一色·清一色 / 对对和；形状役故意不查。
- **性能**：良形分类要对每个听牌张做一次和了形分解。`chooseDiscard` 因此**分两遍** ——
  先用便宜的判据（向听/危险度/宝牌）圈定"向听最小的那一组"，贵的评估只跑这一组。
  别把 `HandEval.of` 摊回每个候选（实测单核自对弈会从 4.96 秒/场涨到 6.2 秒/场）。
- **非空转**：`Bot.debugFoldCount / debugDamaCount / debugNoYakuRefuseCount` 是**实战计数钩子**
  （与 `RoundScoring.debugCallCounts()` 同一个套路）。新加一条取舍就加一个计数，并在
  `SelfTest.teacherTests` 里断言它 > 0 —— 只测纯函数会出现"判据对、实战一次没走到"的假绿。
- **`scoreIfWin` 的张数契约**：荣和要 13 张形态、自摸要 14 张形态。自家回合是 14 张，
  要问"打掉某张之后值多少"就用**指定暗牌**的重载（`Round.scoreIfWin(seat, concealed, ...)`）。
- **开杠**（`Bot.shouldKan`，2026-09 补的第四条）：暗杠/加杠要**不丢听牌、不倒退**
  （`shanten(暗牌−4张, 面子+1) ≤ shanten(现在)`，一条尺子同时挡"拆听牌"和"杠散七对子"）；
  加杠还要过 `Danger.worst`（无筋中张＝会被抢杠＝直接放铳）；**弃和中**与
  **第 4 个杠会四杠散了**（`four_kan_abort` 且本局杠数 3、不全是他自己开的）时一律不开。
  **大明杠只在已经鸣过牌的手上开**（门清手留着暗刻的符/三暗刻/四暗刻/立直；这是本作口径）。
  ⚠ 改这条＝改 teacher＝**改训练标签**，旧数据集作废。
  ⚠ `hasYakuPlan` 的役牌那一支判的是"**非吃即刻子**"——大明杠也走这条，别再写回 `kind == PON`。
- **押し引き是期望值，不是开关**（`Bot.shouldPush` / `pushEv` / `winProbability` /
  `dealProbability`，2026-09 档 B）：`P(和了)×和了点 − P(放铳)×平均放铳失点(5200) > 0` 才推，
  期望为负才改弃和。三个输入都是**粗模型** —— 改其中任何一个常数（`AVG_DEAL_POINTS`、
  概率映射的 0.30 上限、`/steps` 那一折）都等于改 teacher 的行为与训练标签。
  ⚠ **别把两处危险度口径"统一"掉**：算期望用 `Danger.worst`（四家最坏＝"我打算打的那张有多危险"），
  真弃和时才用 `Danger.worstAgainstRiichi`（只看立直家）—— 理由见 §6.6 与 DESIGN。
- **打点有"粗估"和"精确"两条路，别互相顶替**：押し引き的期望值与副露价值用
  `Bot.estimatedHan`（数形状 + 宝牌 + 赤五，纯函数、每巡都跑得起）；**立直/默听**用
  `Round.scoreIfWin`（精确，含符数与里宝期望）。⚠ 副露取舍**不能**拿 `scoreIfWin` 顶替：
  它读的是牌桌上真实的 `melds`/`menzen`（鸣牌还没发生），照抄会把平和/门清符/一杯口白算进去。
- **副露打分**（`Bot.callWorthForMenzen`，2026-09 档 B）：门清手要么鸣完**直接听牌**，
  要么鸣完**至少 2 番**（否则留着门清做立直）；已经鸣过牌的手不受这条约束。
- **顺位与终局**（2026-09 档 C，`Bot.pushThreshold` / `placementOf` / `shouldDeclineRon`）：
  - 押し引き的判据是 `期望值 > 顺位门槛`，门槛表在 `Bot.PLACEMENT_BIAS`（1 位 +800 守、
    4 位 −1000 抢、终局 ×2）。⚠ **点数未知（`HandState.scores == null`）时门槛必须是 0**：
    自检构造的纯形状局面不该被隐式顺位假设污染，改这条会一次红一片。
  - **顺位与终局精算同一把尺子**：同点按座次（seat 0 = 起家）。自检逐座位对拍
    `RoundScoring.settle().rank` —— 改 `placementOf` 的并列口径就等于改精算口径。
  - 「终局」= `kyoku == 4 && roundWind >= RoundScoring.lastWind(rules)`（含南入 / 西入）。
    `lastWind` 已提到 `RoundScoring`，`Table` 与 teacher **共用一份**，别再各写一份。
  - 见逃四条闸门（缺一不可）：终局 + **自己没立直**（立直见逃＝立直振听）+ 荣和抬不动顺位 +
    自摸抬得动 + 剩余牌数 ≥ `RON_DECLINE_MIN_TILES`。点数用生产的 `Evaluator` + `Payments` 算，
    **不要**换成粗表（差 100 点就翻结论）。
- **对手模型**（2026-09 档 C，`Bot.openThreat` / `dealScore` / `threatSeats`）：
  未立直但已鸣牌的他家按 `min(0.9, 0.25 + 0.2×(副露−1) + 0.03×巡目)` 计威胁，
  过 `OPEN_THREAT_GATE`(0.6) 就进押し引き（没人立直也可能该弃和）。
  - **抬档只许往上**：`dealScore` 把威胁家"当作已立直"再算一次危险度，插值后要 `Math.max(base, …)`
    —— 不夹住就会把"对别家最坏"的分算低（现物/筋/壁仍是硬判据，自检钉着）。
  - `threatSeats`（威胁家集合）被**弃和选牌与危险度抬档共用**：各写一份就会漂成
    "弃和按 A 家算、期望值按 B 家算"。
  - ⚠ **范围边界**：对手模型只管**打牌**；鸣牌段的 `underPressure` 仍是**立直专用**（别顺手合并）。
- ⚠ **自检里凡是用到"宝牌是什么"的断言，都用 `Round.debugSetDora(...)` 把指示牌钉死**：
  宝牌由发牌决定，靠运气会出现"同一手牌恰好撞上宝牌"的假红（这条真踩过）。
- ⚠ `SelfTest.endGameTests` 的种子（现在是 **55**）是**扫出来的**：改 teacher 的行为之后它会换结果，
  那时**重新扫一个**（临时探针跑一批 seeds，看 `round_end.round.riichi_sticks`），别把断言改松。

---

## 7. 常见症状 → 先查哪里

> **最常查的 5 条**：编译/链接失败 · 手牌数量对不上（`tsumogiri`）· 一人牌河两张横置（`discard.sideways`）·
> 界面显示成裸键/裸码（语言文件）· `dist` 里的 exe 不是最新（要 `-Deploy`）。

| 症状 | 首先怀疑 |
| --- | --- |
| 编译不过 / 链接失败 | exe 是否在运行（锁文件）；AUTOMOC 缓存陈旧 → 加 `-Clean` |
| `Connection refused` | 服务端没起 / 端口错 / **WSL 只转发到 `[::1]`**（客户端已自动回退 IPv4↔IPv6；WSL 填 `localhost`） |
| 大厅按钮是灰的 | `MainWindow::onConnected()` 必须调 `m_lobby->setConnected(true)`（曾漏过） |
| **自选座位：点过的那一格一直灰着（"上一个按钮不会弹起"），反向点却正常** | 同一趟循环里**既更新又读**派生状态：`updateWaitingRoom()` 曾在 0→3 的循环里一边 `setMySeat(pid 命中的那格)` 一边用 `mySeat()` 决定按钮 enabled —— 座位号变**大**时，先被处理的正是"我刚离开的那一格"，读到的还是旧值 → 它被判成"我坐着"而永远置灰；号变**小**时新座位排在前面，就恰好正常。修法：**先用一趟把"我在哪一格"定下来，第二趟再画**。回归：`client --selftest` 的「自选座位」组（正反两向 + 开局后全灰） |
| 手牌数量对不上 | `tsumogiri` 用了吗？有没有靠 kind 猜？ |
| **换座之后点「准备」没反应 / 把别人设成已准备 / 牌局永远开不了** | 被换走的那一家 `session.seat` 没跟着改 → 「我已准备」写到了**别人**的座位上。见 §6.3「服务端并发模型」：`Table.resyncSessionSeats()` + 用 `Table.seatOfSession(this)` 反查。定性：`node tools\seat-swap-test.mjs <host> <port>` |
| **多人同时换座/洗座后座位表错乱（同一人占两格、某人消失）** | 等待室命令跑在各连接线程上、没有互斥：`Table.roomLock` 有没有把「判 playing + 改座位 + broadcastRoom」包成一步？`swapFieldsOnly` 是逐字段交换，被切开就会写坏。见 §6.3 |
| **庄家第一巡点了牌却打出另一张 / 手牌张数对得上但内容与服务端差一张（幽灵手牌）** | 「哪张是刚摸到的」被猜了：① 服务端 `round_start` 有没有发 `drawn`（仅庄家）？② 出牌取牌是不是按**牌码**（`Round.pickDiscardId`）？③ 客户端的 `discard` 分支是不是按牌码对账（而不是只信 `tsumogiri` 标记）？见 §2.3-11。回归：`SelfTest.discardAlignTests` + `client --selftest` 的两组 |
| 一人牌河两张横置 | `riichi` 事件里是不是又去标"最后一张"了？横置只认 `discard.sideways` |
| 鸣牌后牌河对不上 | `called_index` 有没有 `removeAt`？ |
| 门前役全不生效 | `Round` 构造里 `menzen[i] = true` 还在吗？ |
| 和了形误判 | 向听 DFS 的 `melds + partials < 4` 封顶还在吗？ |
| 和牌按钮不出现 | 服务端 `debugTurnOptionTypes` / `debugClaimOptionTypes` 钩子查下发；客户端用 `--demo --no-answer` + 假服务端截图 |
| **自对弈里"注入的策略根本没被调用"** | 座位是不是 `bot`？只有 `seats[seat].bot` 为真的座位才走 `Table.decideBot`（真人座位的动作从网线上来）。注入策略必须配 `addBot`（见 §6.5） |
| **训练数据里鸣牌决策一条都没有** | 埋点挂在了 `Table.debugAskTap` 上 —— 它只覆盖自家回合，鸣牌段**不走** `Round.ask()`。改挂 `debugChoiceTap`（见 §6.5） |
| **同一种子两次跑出的轨迹不一样 / 配对评测结果飘** | ① `debugDeterministicSeed` 开了吗？② 策略实例跨局复用了吗（随机源/缓存带状态）—— 必须 `PolicyFactory` 每局新建；③ 是不是又有人用了 `Math.random()`（`Bot` 的九种九牌分支就踩过） |
| **数据集校验报"观测里有未登记字段"** | 往 `Observation` 加了字段却没同步三处（`SelfTest` 白名单断言 / `selfplay-check.mjs` 的 `OBS_KEYS` / PROTOCOL §8.2）。**这是防泄漏的设计**，别把白名单放宽了事 |
| **自对弈比预期慢很多** | 先用**批量**（几十场）量，别用几场判 —— JIT 预热会把头几场放大 2~3 倍。真慢就查观测里有没有调 `Round.isFuriten()` 这类会跑向听 DFS 的东西（见 §6.5） |
| **放过一张荣和牌之后马上又能荣和同一张 / 立直见逃没有代价** | 振听三种有没有**真的记账**？`furitenTemp` / `furitenPerm` 若"只有清除、没人置位"，见逃与振听博弈就整个不存在。见 §2.3-12 与 `docs/DESIGN.md` 的振听表；回归 `SelfTest.furitenRuleTests` |
| **打出去被碰走的听牌张，事后又能荣和回来** | 舍张振听读的是 `discards[]`，而被鸣走的牌已被 `removeCalledFromRiver` 移除。判据必须是"曾经打出过"（`discardKindsEver`），不是牌河。见 §2.3-12 |
| **机器人放铳率离谱 / 立直了还在打危险牌** | teacher 的弃和分支没接上：`Danger.worstAgainstRiichi`（**只看立直家**）有没有被 `chooseDiscard` 用？用 `Danger.worst`（对四家）会把现物的安全度淹掉。见 §6.6 |
| **机器人鸣出一手永远和不了的牌** | `hasYakuPlan` 有没有在 pon/chi 前查？鸣牌打掉门清=打掉立直，鸣完无役就和不了。见 §6.6 |
| **机器人无脑立直（明明已经満貫以上）** | `shouldDeclareRiichi` 的默听分支：用 `scoreIfWin(..., assumeRiichi=false)` 问"不立直值多少番"。⚠ 它的暗牌必须是**14 张形态**（自家回合），内部会减掉要打的那张 |
| **`scoreIfWin` 总是返回 null** | 张数契约：荣和要 13 张形态、自摸要 14 张形态（`WinCheck.counts` 校验）。自家回合手上是 14 张，查"打完某张之后"要用**指定暗牌**的重载（`Round.scoreIfWin(seat, concealed, winKind, ...)`） |
| 中文字画成空心方框 | 用了 `-platform offscreen`（该插件无字体库），换默认平台 |
| 结算界面的牌面是文字不是牌图 | 内嵌字体没加载（查 exe 同级 `fonts/I.MahjongJP.otf`），或示意串含非法字元被校验挡下（见 §9） |
| 素材是彩色、客户端却画成黑白线稿 | Illustrator 的 `<style>`+`class` 上色 Qt 不认 → 跑 `tools\inline-svg-style.ps1` 内联（见 §2.3-5） |
| 弹窗在截图里看不到 | `--shot` 要抓**活动顶层窗口**而非主窗口（见 §2.3-6）；或弹窗压根没弹出来 |
| **牌河/副露牌太小** | 风盘"先定尺寸再塞牌河"了？（见 §6）河区界限要用 `max(cw,ch)`；最坏行按「一行最多 1 张横置」估 |
| **第 19 张起牌河不显示** | 绘制处行数被封顶。`riverRowsFor(n)` 不封顶，**布局预留与绘制必须同源**（见 §6） |
| **牌河的起点会漂**（只打 1~5 张时落在牌河带中间，越打越往左，打满才到最左） | 左缘是不是按「本帧已打出的最大列数」算的？必须**先假定一行排满**取那条最左沿（`TableLayout::riverLeftU()`），绘制与飞行动画终点读**同一份**。见 §6「牌河的横向起点是固定左缘」；回归：`client --selftest` 的「牌河固定左缘」组 |
| **一路牌「莫名」被盖住**（宝牌/摸牌/副露） | 多半是**画了、被后画的盖住**——先按坐标确认；再查 §6「边界避让」：`over` 是不是负的、③ 右移有没有封顶 |
| **宝牌指示牌被压扁、不像牌** | 绘制处是不是只压了宽度？必须 `th = tw × 1.36` **宽高同缩**（见 §6） |
| **立直棒和点数叠在一起** | 立直棒有没有自己的区带（`m_stickBand`）？曾经和自家点数共用一行（`c.bottom()-pad-sh`） |
| **牌河压到手上/四家河互相重叠** | `m_riverArea` 的预算（`盘半 + riverExtent ≤ 屏半 − 内边距 − 手牌厚`）是不是被当"画线框用的"删了 |
| **改了风盘尺寸后第一帧错位** | `paintEvent` 里 `computeLayout()` 是不是在 `paintBackground()` **之前** |
| **结算弹窗不自己关、下一局已经在它后面开打了** | 没在 `round_wait` 启动倒计时/到点没 `accept()`；且 `round_start`/`game_end` 必须 `closeResultDialog(false)`（见 §6「局间时序」） |
| **局间的 5 秒等待被吃掉、直接开下一局** | 有没有在服务端已经推进后还发 `confirm`？（残留到下一轮被 `drainConfirm()` 收走）|
| **结算界面既画了牌又写了字** | 文字只应在 `schematicRenderable()` 为假时补；查是否有两份 `宝牌指示牌` 文本 |
| **中文/玩家名显示成乱码或 `?`** | 见 §6「报文里不带中文」；定性：`node tools\utf8-test.mjs <host> <port>` |
| **界面显示成 `yaku.riichi` / `riichi` 这种裸键裸码** | 裸键 = 语言文件没载入或 key 拼错（尽早 `lang::load()`、查 exe 同级 `i18n/`、自检 Lang 组、`check-i18n`）；裸码 = **设计如此**（服务端加了码而语言文件没加） |
| **改了 `zh_CN.json` 但界面还是旧文案** | ① 改的是不是**跑的那个** exe 旁边的 `i18n/`（只有 `-Deploy` 同步到 dist）；② 语言文件**启动时载入一次**，改完要重启 |
| **新增界面文案忘了搬进语言文件** | 跑 `node tools\i18n-scan.mjs --check`；按 §6 三件套搬（映射 → `i18n-apply` → `i18n-gen`） |
| **报文里出现了中文**（非 `name`/`text`/`msg`） | ① 展示字段有没有过 `YakuCodes` 翻码；② `e2e-test` 会逐条审计并判失败；③ 新役种有没有登记（自检 `misses()`） |
| **立直棒全堆在盘底 / 四家点数不等距 / 得点框不一样大或不居中 / 宝牌指示牌伸出四框之外** | 见 §6「风盘区带」（按座位画棒带、四边同构等距、框同理、宝牌行只占中间列）。有自检断言 |
| **自动摸切把能胡的牌打掉 / 开关没随小局复位** | 见 §6「三个自动开关」（和牌优先、走 `actionCmd/discardCmd`、复位在 `round_end`） |
| **第 5 次杠还能点出来 / 杠的次数没封顶** | `Round.canKan()` 必须同时看「岭上还有」与「本局杠数 < 4」，且**四个闸门**都走它；被拒绝的杠不能算数（否则废杠吃掉名额）。回归：`SelfTest.kanLimitTests`、§6「王牌/岭上」 |
| **杠后「岭上」数不减** | 岭上摸牌**不动 `tiles_left`**，只能看 `dead_wall_left`：① 服务端是否每次 `draw`（含重连 `state`）都带它；② 客户端 `draw` 分支是否更新。回归：`SelfTest.rinshanTests`、`e2e-test` 岭上账 |
| **役满显示成「0 番」** | 役满没有番这个量纲：① 界面按 `yakuman` 显示（别用 `han>=13` 猜）；② `yaku[].han` 应为 `13 × 倍数`。回归：`client --selftest` ResultDialog 组 |
| **`dist\` 里的 exe 不是最新的 / `build\` 有某个 Qt 插件而 `dist\` 没有** | `dist\` 只在 `-Deploy` 时更新（跑 `pwsh -File client\build.ps1 -Deploy`）；`Deploy-QtRuntime` 的「已就绪就跳过」必须覆盖它负责的**全部**文件。⚠ 别拿 exe 哈希当判据 |
| **副露的「新旧顺序」反了**（应右边最旧） | 绘制必须走 `meldLeftsOf()`（最早的在最右），别从 `meldLeft` 向右排；这与「一副副露内部左→右」是两件事 |
| **「非首局第一巡」时间偏短 / 点确认后下一局第一巡自动出牌** | 服务端队列 bug（见 §2.3-8）。**定性：`node tools\firstturn-test.mjs <host> <port> 3000`**（未修时「confirm 后 ~4800ms」+「首巡 0ms」）；绿了再查客户端 |
| **重复点击按钮出现怪行为**（连点立直像没立直 / 下一巡自己动了） | 见 §2.3-9：组包走 `actionCmd()`、提交走 `onActionReady()`、标题由 `refreshTitle()` 统一产生、立直不成立退回摸切。定性：`riichi-stale-test.mjs` |
| **鸣牌「等了很久才成立」** | 见 §2.3-10：`RoundClaims.shouldStop` 漏了「最优鸣牌没人能压过 → 立刻收工」。定性：`claim-priority-test.mjs`（修好后 ≈0ms；**样本不足会报"无法判定"**） |
| **服务端被一条报文/空闲连接拖死** | 单条上限是否**边读边判**（`readBoundedLine`）？`MAX_SESSIONS` / `fill_bots` / `clampToSane()` 还在吗（见 §6） |
| **一条报文就把整桌打崩 / 四家挂着不动** | 有异常冒到牌桌线程：`Table.playGame` 的兜底要**广播终局**；`pickChiTiles` 是否校验 `want` 恰好两张（见 §6） |
| **点数凭空生灭 / 不听罚符对不上** | 罚符收付不能各自向下取整：`Payments.notenPenalty` 必须"收方定额、付方凑齐"（`SelfTest.notenPenaltyTests` 覆盖 16 种组合） |
| **某家"没动就被代打"**（尤其发生在刚有人鸣牌/有人的鸣牌询问被取消之后） | 废包漏进了队列：① 取消询问时有没有 `table.dropReplies(seat, cancelledAskId)`？② `awaitAction` / claimPhase 有没有校验「`type` 属于本次询问的选项」？见 §2.3-10 与 §2.2 的 `ask_id` 行。⚠ 只做 `cancelAsk` 不摘队列是**不够**的 |
| **对局中入局的人进了"未定义的观战状态"**（看到一张空牌桌 / 以为自己是东家却没手牌） | `spectate` 事件在客户端**没有分支**、而 `state.seat = -1` 被 `qBound` 夹成座位 0。修法与语义见 §6.2/PROTOCOL §3.9：`TableModel::spectating()` + 四家一律牌背 + 视角可切；服务端另补公开快照与 `draw` 公开版（`Table.sendSpectators`）。定性：`node tools\spectate-test.mjs <host> <port>` |
| **鸣牌时看不出/选不了用赤五还是普通五** | `pon`/`kan(daiminkan)` 的选项必须带 `tiles`（赤五 `0p`），默认取法**普通牌优先**（`Round.pickAuto`）。见 PROTOCOL §3.6 与 `SelfTest.meldAkaPickTests` |
| **只有第一小局有音效** | 先跑 `MAHJONG_SFX_TRACE=1 client --demo ...` 看 stderr：每一条都会打出开关/可用/音量/池子状态/`play()` 后是否 playing。客户端实测**每局都在播**，所以重点查旧实现那条 `stop()+play()`（已改成实例池 + `allowOverlap`），见 §6.2 |
| **副露里横置的那张"浮"在中间** | 横置牌顶边必须是 `my + (riverH − riverW)`（底部与另两张齐平），见 §6.2 与 `TableView::meldSlotRects` |
| **名牌（ID 框）位置不对** | 四角**轮转一位**：自家右下、下家右上、对家左上、上家左下，见 §6.2 与 `TableLayout::computeLayout` |

---

**实测通过**：服务端自检 **1166** 项（含训练接口不变式 + 振听三条 + teacher 的五层取舍（牌效 / 押し引き期望值 + 顺位门槛 /
打点与役 / 开杠 / 副露打分，外加终局见逃与对手模型）+ 批次一的口径修复 + 《雀魂》的国士抢暗杠 / 天和国士 + 包牌多责任者列表 +
副露赤宝选择与观战快照的公开字段）、客户端自检 **740** 项、§4 的全部 L3 工具（含 `replay-test`、
`discard-align-test` 与 `seat-swap-test`），外加 Qt 客户端↔Java 服务端真机对局（含 GUI 实拍）。L1 里另有三组"跑整场/整表"的账：
**杠后岭上摸牌**（`rinshanTests`）、**一局最多 4 次杠 + 废杠不白拿岭上**（`kanLimitTests`）
与**开局前自选/随机座位**（`seatSwapTests`）；**出牌对齐**另有 `discardAlignTests`（判据逐条 + 庄家 `drawn`），
**牌河固定左缘**另有客户端自检的「牌河固定左缘」组（17 条：1/3/6/7/13/18 张左缘不变、三行行首同源、
普通行正好铺满预留线、立直行恰好多出 (牌高 − 牌宽)；并在旧实现上验过会红：第 1 张 −30.39 → 打到 3 张 −79.10，
而固定左缘是 **−144.12**）。

**未做 / 妥协**：

- **安全 / 逻辑 / 性能审计见 `docs/AUDIT.md`**：已修 6 条严重/高（客户端一条畸形 `chi` 崩掉整桌、
  假顺子改分、读行不设上限、连接数与 `fill_bots` 无界、发牌种子可预测）与二十余条中低，排期项亦已逐条落地。
  **仍未做的**只有低优先项：`Json` 数值强转与落单代理、静态自检钩子、`aka=4` 的编码限制 —— 动之前先看那一份。
- **规则侧**（AUDIT §1.5 有逐条校勘表）：M.League 的**程序性判罚**（扣 20/60 点、诈立判罚、
  点棒授受订正）属线下裁量，不实现；**形式听牌**的两种口径（《天凤》只看手牌 / M.League 连同副露）
  未区分，本项目只按"`Agari.waits` 非空即听牌"处理；《雀魂》的「国士可抢暗杠」「天和时国士视作十三面」
  未实现；杠宝牌的翻开时机统一按 M.League「杠成立即翻」（《天凤》明杠/加杠延后翻的那套不区分）。
- 服务端只在**本机 Windows + JDK 21** 验证过；**未在真实 Ubuntu 上跑过**（本环境无可用 WSL 发行版）。
  代码是纯标准库字节码、`.sh` 已是 LF 无 BOM，预期直接可用，但请在目标机跑一次 `./build.sh && --selftest`。
- 赤宝牌支持 **0 / 3 张**（`rules.aka = 4` 即"两张赤五筒"受牌 id 编码限制，仍按 3 张处理）。
- 古役默认关闭（`rules.koyaku`）。
- 断线重连 `rejoin` 协议已实现，UI 未暴露入口。
- **副露的赤宝选择：协议 / 服务端 / 客户端三层都已就绪**（1.8.0 起）。服务端对"用赤 / 不用赤"
  两种取法**各下发一条选项**（`ask.options[].tiles` = 精确牌码，见 PROTOCOL §2.2 与 §3.6），
  客户端把它们渲染成**两个按钮**（`碰` / `碰 赤五筒`，大明杠同理）并原样回带 `tiles`；
  不给 `tiles` 的老客户端 / 机器人走 `Round.pickAuto` = **普通牌优先**（旧实现"取前 n 张"
  等于"碰五必吃赤五"）。回归：`SelfTest.meldAkaPickTests` + `client --selftest` 的 ActionBar 组。
  `chi` 的选项里赤五本来就以 `0m` 列出（选它即用赤五）。
- **观战（对局中入局）有专门 UI**（1.8.0 起）：牌桌上方一条观战栏 + 「视角」下拉，
  点别家名牌也能切视角（与回放同一套做法）；牌桌本身复用，暗牌区留空。
  协议与快照策略见 PROTOCOL §3.9，回归 `node tools\spectate-test.mjs <host> <port>`。
- 字体**不打进 exe 资源**（同级 `fonts/` 够用）；**5 饼中央红点是刻意美术**，别当 bug。

---

## 9. 素材与字体管线（牌面 SVG + 结算字体）

### 9.1 牌局中：SVG 矢量素材

- 位置 `client/assets/tiles/<牌码>.svg`（38 个：37 种牌 + `back.svg`），构建时拷到 exe 同级 `tiles/`。
- 加载顺序：**exe 同级 `tiles/` → qrc `:/tiles/` → 程序化绘制**（删掉整个 `tiles/` 也不会白屏）。
- 替换只要求：**文件名 = 牌码**、`viewBox="0 0 300 400"`。**改完重启客户端即生效，无需重新编译。**
- ⚠ **Illustrator 导出的 SVG 必须内联 CSS**：`pwsh -File tools\inline-svg-style.ps1 -Dir client\assets\tiles`
  （原因见 §2.3-5）。原件会备份到 `client/assets/tiles-replaced/pre-inline/`。

### 9.2 结算界面：内嵌牌面字体 + OpenType 连字

- 字体 `client/assets/fonts/I.MahjongJP.otf`（142 KB，**M+ 授权**：可自由使用/分发/修改 → 随程序分发合规），
  构建时拷到 exe 同级 `fonts/`；加载器 `client/src/ui/TileFont.{h,cpp}` 是单例，**目录优先 → qrc 兜底**。
- 该字体只有 **`liga`** 一个 GSUB 特性且**默认生效** → **不需要任何 `QFont::setFeature` 调用**
  （`node tools/dump-otf-features.mjs <字体>` 可复查）。
- **输入语法（用 `--fontprobe` 实测得出，不要靠猜）**：

  | 形式 | 含义 |
  | --- | --- |
  | `<1-9><m\|p\|s\|z>` | 一张牌（`5z` = 白，渲染成空白牌） |
  | 后缀 `a` | 给该牌加**赤标记**（`3ma`/`4za`/`5za` 都成立；`8za`/`9za` 不成立，`a` 会变字面） |
  | `0m` / `0p` / `0s` | **赤五简写**，与 `5ma`/`5pa`/`5sa` 渲染完全相同 |
  | `-`（**后置**） | 把**前一张**牌横置 → **副露横置就靠它** |
  | `=`（中置） | 把相邻两张并成一格（重叠）；**本项目用不到** |
  | 空格 | 普通间距（**字宽 ≠ 一个牌位**，别用它凑"空一格"） |
  | 连续码 | 字体自动打包（2 张并排 / 4 张成 2×2） |

- `ResultDialog::schematicOf()` 生成示意串，用**控制字符**编码多行结构：

  | 字符 | 用途 |
  | --- | --- |
  | `U+001F` | 行间（手牌 / 宝牌指示牌 / 里宝指示牌） |
  | `U+001E` | 行内「标签」与「牌串」之间 |
  | `U+001D` | 同行旁挂之间（手牌↔和了牌↔副露） |

  渲染时**每个旁挂之间插一个 `setFixedWidth(44)` 的间隔**（= 一个整牌位），
  所以「和了牌空一格摆在一旁」是**精确一格**，不依赖字体空格宽度。
- **数据来源全部是既有协议字段**：`agari` 事件的 `hand` / `winning_tile` / `dora_indicators` /
  `ura_indicators`，副露取 `model->melds(winner)`。**没有新增协议字段。**
- **安全兜底**：`tilefont::isRenderableSchematic()` 用正则卡住牌码串，
  **任何一段含非法字元就整块不显示**，回退到原文字表示。
  宁可退回文字，也绝不让字体画出「看着像牌、其实是别的牌」的图。

### 9.3 音效（离线合成 WAV + 后端分层）

- **素材**：`client/assets/sfx/<名字>.wav` ×8（吃/碰/杠/立直/自摸/荣和/提示/摸牌），
  由 `node tools/gen-sfx.mjs` **离线合成**（纯 PCM 加法合成，脚本进仓库 ⇒ 可复现）。
  ⚠ 改了音效名要跑 `node tools/gen-sfx-qrc.mjs` 重生成 `assets/sfx.qrc`。
- **为什么不用 MIDI**：Qt 没有 MIDI 合成器（Windows 上 QtMultimedia 只有 WMF/FFmpeg 后端），
  自带 SoundFont 合成器要引第三方库 —— 与本项目"客户端只依赖 Qt"冲突。所以是**预渲染 PCM**。
- **后端：优先 Qt Multimedia（`QSoundEffect`）**，跨平台统一走 Qt API（用户 2026-09 拍板：
  客户端未来要支持多平台，体积不是约束）。**兜底分层**（三条路都编得过，见
  `client/CMakeLists.txt` 与 `client/src/model/Sound.h`）：没有 Qt6::Multimedia 时 Windows 退回
  `winmm` 的 `PlaySound`；再没有则静默（设置里开关置灰并写明原因）。
  ⚠ 走 Multimedia 会让分发多出 `Qt6Multimedia.dll` + FFmpeg 后端（约 20 MB）与
  `plugins/multimedia/`，`build.ps1` 已把这三样纳入必需清单与"已就绪"判断；
  **许可义务同步更新**（Qt Multimedia 仍是 LGPLv3，但它捆绑的 FFmpeg 是另一家的组件）：
  `build.ps1` 会把上游 Qt 安装根 `Licenses/` 的文本拷到 `dist/licenses/qt/` —— 见
  `docs/THIRD-PARTY.md` §1/§3 与 `client/licenses/NOTICE.txt`（1b 节），**别删那个目录**。
- **加载顺序与牌面同约定**：材质包 `sfx/` → exe 同级 `sfx/` → qrc → 静音。
  ⚠ `assets/sfx.qrc` 的 **prefix 与条目名必须与源码里的 `:/sfx/<名字>.wav` 逐字对齐**
  （现在 = `prefix="/"` + `<file>sfx/<名字>.wav</file>`）：对不上时"qrc 兜底"整条静默失效，
  自检里有一条断言专钉这件事。
- 开关/音量在 `settings.json`（`sfx` / `sfx_volume`），关掉时**不去读文件**。
- 触发点在 `MainWindow::onEvent`（按事件与 `meld.kind` 分派），不在服务端 —— 音效是纯客户端表现。

### 9.4 相关工具一览

`tools/gen-tile-placeholders.mjs`（生成占位符 SVG，不覆盖已有）· `tools/inline-svg-style.ps1`（把 CSS 内联成表现属性）·
`tools/dump-otf-features.mjs`（列 GSUB 特性）/ `--gentiles` / `--fontprobe`。

### 9.5 发布打包

`pwsh -File tools\package-release.ps1 [-Version x.y.z]` → 在 `release\`（已 gitignore）下产出两个 zip：

| 包 | 内容 | 要点 |
| --- | --- | --- |
| `sMahjong-client-v<版本>-win64.zip` | `client\dist` 全部内容 **去掉 `settings.json`** + 自带 `README.txt` | 自带 Qt 运行时与 `licenses\`（LGPLv3 要求），解压即用 |
| `sMahjong-server-v<版本>.zip` | `mahjong-server.jar` + `build.sh`/`run.sh` + `DEPLOY.md` + `README.txt` | 目标机只要 JDK 17+ |

- ⚠ **版本号有两处，脚本会两边一起核对**：`client/CMakeLists.txt` 的 `project(... VERSION)` 与
  `client/src/main.cpp` 的 `setApplicationVersion()`。不一致直接报错 —— 「包名 v1.6.0、程序自称 1.5.0」
  正是 v1.6.0 首次发布漏掉的一处，只能事后重新打包。
- 打包前必须先 `client\build.ps1 -Deploy`（`dist\` 只在 `-Deploy` 时更新，见 §3.3），脚本会校验 exe 在不在。
- 上传 release 资产时注意 GitHub 的两个坑：**api 主机与上传主机是两个 origin**
  （`api.github.com` vs `uploads.github.com`），且**同名资产要先
  `DELETE /repos/{owner}/{repo}/releases/assets/{id}`**；blob/tree/commit/ref 与 release 元数据走 API，
  资产二进制走上传主机。
- ⚠ **原地重打包（tag 不变、只换资产）会踩到 GitHub 的一个坑：删掉 release 的 tag 会把它打回 draft。**
  `DELETE /git/refs/tags/<tag>` 之后该 release 变成 `draft: true`（`tarball_url` 变 `null`、
  资产下载 URL 变 `https://…/releases/download/untagged-…/…`）；重新 `POST /git/refs` 建好 tag
  **并不会**自动恢复关联 —— 必须再 `PATCH /repos/{o}/{r}/releases/{id}` 带上 **`"draft": false`
  与 `tag_name`** 才会重新发布并关联（URL 变回 `/releases/download/<tag>/…`）。
  完整链路：`DELETE` tag → `POST` tag（指向新提交）→ `DELETE` 旧资产 → 上传新资产
  → `PATCH {draft:false, tag_name, body}`。别漏最后一步，否则 release 会静默变成草稿（只有自己看得见）。
- **提交推送**：优先 `gh` / git 通道；**git 通道不通时**（本机实测：broker 到 `github.com:443` 被拦，
  `git push` / `ls-remote` 都失败，而 `api.github.com` 通）走 **dsh-github 插件的 REST 通道** +
  `tools\gh-push-payload.ps1` 生成 blobs/tree/commit/ref 载荷（脚本会**自算 commit sha** 与本地比对）。
  三条硬要求：`bodyFile` 用**绝对路径**；`PATCH /git/refs/…` 的 body 也走文件（内联字符串会被当字符串发走、
  422 `is not an object`）；提交对象的 message **必须带尾随换行**，否则远端 commit sha 与本地不同
  （内容一样、对象不一样 → 本地与远端分叉）。
- **`client\dist` 与 release 的关系**：`dist` 只在 `-Deploy` 时更新，所以**发布前先看客户端源码有没有
  在 v<上个版本> 之后改过**（`git diff --name-only v1.6.0..HEAD -- client/`）—— 改过就必须重出 `dist`，
  否则包里是旧 exe（2026-09 档 C 发布时就撞到：LobbyDialog / TableModel 变了而 dist 还是旧的）。
