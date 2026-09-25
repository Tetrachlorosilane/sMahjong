# AGENTS.md — 立直麻将（日本麻将）联网对战

给在本仓库工作的 AI agent / 新同事的操作手册。**先读这份，再动代码。**

---

## 0. 怎么用这份文档

**两份文件，一套章节号**：

| 文件 | 定位 | 什么时候读 |
| --- | --- | --- |
| **`AGENTS.md`**（本文件） | **手册**：铁律、判据、命令、不变量、速查表 | **动代码前必读**：§2 铁律 → §3 构建 → §4 验证 → §6 约定 |
| **`NOTES.md`**（根目录） | **细节分册**：完整案例与红证、几何推导、全量症状表、素材管线、已知限制 | **需要时查**（章节号与本文件一一对应） |

> ⚠ **本文件必须"每次会话都读得完"**：它是自动加载的工作区指令，而**指令预算只有 64 KB** ——
> 超了会被**静默截断**（后面的章节等于不存在，而且不报错）。所以**判据留下、细节进 `NOTES.md`**（见 §8）。

**按轻重缓急读**：① §2 铁律（违反必出 bug，改代码前先扫一遍）→ ② §3 构建 / §4 验证五层
（"编译通过"不算验证）→ ③ §6 代码约定（按主题分组的硬约束）→ ④ §7 症状表（报障时按症状查）
→ 其余（§5 目录、§8 文档维护）需要时再翻。

文档地图：`docs/PROTOCOL.md`（协议契约 —— **改协议先改它**）· `docs/DESIGN.md`（架构与规则取舍）·
`docs/AUDIT.md`（审计与修复清单，条目号 `S-nn`）· `docs/THEME.md`（材质包/设置文件）·
`docs/THIRD-PARTY.md`（第三方许可与分发义务）· `docs/DEPLOY.md`（Ubuntu 部署）·
`docs/BOT-AI.md`（**机器人 AI 包格式**）·
`client/README.md`（客户端构建与链接方式）· `README.md`（**面向玩家**）· `NOTES.md`（**细节分册**）·
`docs/TRAINING.md`（机器学习训练方案 · **P0–P4 与 P5b 已落地**；P5 只落了最小可用子集、P6 未做）。
### 2.3 十四条曾经踩过的坑（同类问题会再犯）

> 这里只有**判据**（"再遇到同类问题，代码该怎么写"）。每条的**报障原文 / 根因推导 / 红证数据**
> 在 **`NOTES.md` §2.3**（同编号）。**新踩的坑请按同样格式加在这里 + 详情写进 NOTES。**

1. **`riichi` 事件里绝不能标记"牌河最后一张"为横置**：服务端**先广播 `riichi` 再广播 `discard`**，
   那一刻最后一张还是宣言牌**之前**那张 → 一家两张横置。客户端**只按 `discard.sideways` 画**。（NOTES §2.3-1）
2. **`Round` 构造时必须把 `menzen[]` 初始化为 `true`。** Java `boolean[]` 默认 false，
   漏了会让**所有门前役**在实局失效，而单测因为显式传 `ctx.menzen=true` 照样通过。（NOTES §2.3-2）
3. **向听 DFS 里 `melds + partials` 必须封顶到 4**，否则 `1112223334445m` 这类会被误判成和了形。（NOTES §2.3-3）
4. **庄家的第 14 张在配牌时就发了，第一巡不能再摸。** 第一巡要用 `openingTile` 当"本次摸到的牌"
   （保住自摸/暗杠/天和判定），且**不重复下发 `draw`**（客户端已从 `round_start` 收到 14 张）。（NOTES §2.3-4）
5. **Qt 的 SVG 渲染器不支持 CSS 类选择器**（Illustrator 导出的 `<style>`+`class=` 会被忽略 → 只剩黑白轮廓）。
   新素材先跑 `tools/inline-svg-style.ps1` 内联成表现属性。（NOTES §2.3-5）
6. **`--shot` 必须抓"活动顶层窗口"**，不能直接 `grab()` 主窗口：结算/大厅是**独立顶层窗口**。（NOTES §2.3-6）
7. **改文件用 `edit` 工具，别用 PowerShell 做多行替换**：CRLF 与 `` `n `` 不匹配时会**静默不生效**
   （极易被当成"改过了"）。真要脚本替换：先确认 LF，且只用单行替换/按行号拼接。（NOTES §2.3-7）
8. **服务端只有一条命令队列 `responses`**：`confirm` 必须从**`submit()` 写入的那条队列**里消费
   （`drainConfirm`），而且**认领答复时必须确认它是动作**（有 `type`）——否则局间残留的 `confirm`
   会被下一巡当作出牌答复（症状：**点了确认键后，下一局第一巡自动出牌**）。（NOTES §2.3-8）
9. **「重复点击」= 同一个询问发出两条动作，客户端必须自己先锁住**：所有动作只能从
   `ActionBar::actionCmd()` 组包（带 `ask_id`）、出牌/立直只能走 `MainWindow::onActionReady()`
   （先 `clearAsk()`）；服务端兜底：**立直不成立退回默认摸切**，绝不把宣言牌当普通打牌执行。（NOTES §2.3-9）
10. **鸣牌仲裁三条一体**：① 优先级「荣和 > 杠 = 碰 > 吃」同级看座次，这把尺子在 `RoundClaims.rankOf/
    canBeat` 与 `claimPhase` 仲裁循环里**必须同一把**；② 提前收工（`shouldStop`：最优鸣牌没人能压过
    就收工；`askedBestRank` 缺项时**保守继续等**）；③ **队列卫生**：收工后仍在 `asked` 的要
    `cancelAsk` + `dropReplies`，认领处校验 `type ∈ 本次 option.type`。（NOTES §2.3-10）
11. **「哪张是刚摸到的」只能由服务端点名**（`round_start.drawn`，仅庄家），客户端不许猜；
    **出牌取牌只认牌码**（`Round.pickDiscardId`）；⚠ **手切要从"除摸牌位之外"的暗手里取**
    （同码牌在按 id 排序的 `hand[]` 里谁先撞上纯属偶然）——否则动画/牌谱的手切被演成摸切。
    客户端对账也按牌码，顺序不能反。（NOTES §2.3-11）
12. **振听有三种，且「自己打出过的牌」≠「牌河」**：舍张振听要算上**被鸣走的舍牌**
    （唯一记账点 `Round.recordDiscard`，账在 `discardKindsEver`）；同巡振听在被给 `ron` 却见逃
    （含超时未答）时置位、自家下次摸牌解除；立直见逃 → `furitenPerm` 到本局结束。
    ⚠ **测试不许绕过记账点**（老用例直接往 `discards[]` 里塞牌＝给 bug 背书）。（NOTES §2.3-12）
13. **掉线 ≠ 换机器人。** 掉线/退房只标 `Seat.away`（**不置 `bot`**）：该家回合**自动摸切**、
    鸣牌**连 `ask` 都不发**；托管的座位**仍算被占**（`occupied()` 含 `away`，别人抢不走）、
    但 `readyToStart()` 不认它；四人全掉线才回收房间；房主掉线自动转给在场第一位真人。
    同一 uuid 重连**接回原座位**（§6.8）。判据只看 `Seat.away`。（NOTES §2.3-13）
14. **"投票通过"发生在别的线程上，牌局线程必须能被叫醒**：`finishVoteLocked(passed)` 要
    `voteEnded = true` **且** `wake()`（投一个**没有 `type`** 的哨兵进命令队列）；牌局线程在
    **五个检查点**看这个标志（`play()` 主循环开头 / `ask()` 后 / `claimPhase()` 后与它的等待循环 /
    `Table.awaitAction` 循环开头），局间由 `sleepMs`（切片）与 `awaitRoundConfirm` 提前醒。
    漏一个就是"票都通过了，牌桌还在等"。（NOTES §2.3-14）
## 1. 这是什么

一套完整可运行的四人立直麻将联网游戏：

- **服务端** `server/` —— Java 21，**零第三方依赖**（只用 JDK 标准库，没有 Maven/Gradle）。
  跑在 Ubuntu 上，是**唯一权威方**：洗牌、配牌、摸切、鸣牌、和牌判定、役种/符数/点数、振听、流局、连庄、精算。
- **客户端** `client/` —— Qt 6 Widgets + C++17，MinGW 构建，跑在 Windows 上。
  只负责牌桌绘制、操作收集、事件反馈、网络收发。**不做任何规则判定**（§2.1）。
- **规则依据** `docs/日本麻将.md`（2026-09 版，逐节标注《雀魂》《天凤》与 **M.League** 的差异）；
  **规则预设** `rules.preset` = `mleague`（默认）/ `tenhou` / `majsoul` / `custom`：预设先铺一整套、
  报文里的单项字段再覆盖。⚠ 新增取舍项要**五处一起改**（字段 / 三套预设 / `fromJson`+`toJson` /
  `clampToSane` / 断言），且**取舍类断言必须两侧都显式传规则集**（只测默认值 = 在测"默认值恰好是什么"）。
  取舍清单见 `docs/DESIGN.md`，字段表见 `docs/PROTOCOL.md` §5。
- **对局记录/回放** `server/.../replay/` + 客户端 `ReplayWindow`/`WallView`：整场下行报文按 `seq` 记，
  终局**先落盘再广播 `game_end`**（否则结算界面点「看本局回放」查不到）；回放 ID **先校验形状再拼路径**
  （PROTOCOL §3.11 与 DESIGN「对局记录与回放」）。
- **接口契约** `docs/PROTOCOL.md` —— 两端唯一的接口定义。数据流：
  `Qt 客户端 ──TCP/NDJSON──> Java 服务端`（一条 TCP 连接一个玩家，一行一个 JSON）。

---

## 2. 铁律（违反必出 bug）

### 2.1 规则只在服务端

客户端**绝不能**自己判断役种、番数、符数、点数、振听、听牌是否合法。
唯一例外：`TableModel::waits()` 为「显示便利」做的纯形式听牌提示，仅用于 UI 提示，不参与任何决策。

新增任何"能不能和 / 能不能吃碰"的逻辑，一律加在服务端并通过 `ask.options` 下发。

### 2.2 协议字段是权威，不要靠猜

> 这里只留**"这条字段该怎么用"的一句话判据**；**为什么必须有它、当初怎么踩的**在
> **`NOTES.md` §6.4.1**（逐字段明细）。改代码前先看注释。

| 字段 | 位置 | 规则 |
| --- | --- | --- |
| `tsumogiri` | `discard` | 「手切 / 摸切」**只能用这个字段**，不能靠"牌的 kind 是否等于摸到的牌"去猜（会多出一张）。 |
| `sideways` | `discard` | 牌河**只有立直宣言牌横置**（被鸣走就顺延）；客户端**只按它画**，不要自己推断（§2.3-1）。 |
| `called_index` | `meld` | 被鸣那张在**原牌河**的下标：鸣牌是「移动」，客户端要 `removeAt()`。 |
| `win_note` | `ask` | 能和却不能和的**原因**（`furiten` / `no_yaku`）；没有这个字段就不要瞎猜。 |
| `ask_id` | `ask` | 回包原样带回。⚠ 三道闸门：老客户端的**无 `ask_id` 回包也认** → 认领时必须确认**是动作**（§2.3-8）；`type ∈ 本次 option.type`（§2.3-10）；客户端**只许从 `ActionBar::actionCmd()` 组包**（§2.3-9）。 |
| `confirm` | `cmd` | 局间「看完了」。**没有 `ask_id`**，必须由 `Table.awaitRoundConfirm` 从**同一条队列**消费（§2.3-8）。 |
| `base_ms` / `bank_ms` | `ask` | 本巡基本 / 剩余额外时长。**扣减与取整由服务端算**；规格写作「额外+每巡」（`20+5`，别写反）。 |
| `yaku[].code` / `.tile` | `agari` | 役种**只发 ASCII 码**（参数化役种另带牌码），**没有 `name`**；认不出的码原样显示（§6.4）。 |
| `limit` / `reason` / `error.code` | 结算 / `error` | 同样只发码，`error.arg` 是可选的 ASCII 参数。报文里**只有** `name`/`text`/`msg` 允许非 ASCII。 |
| `dead_wall_left` | `draw` / `round_start` / `state` | 剩余岭上数，**每次摸牌都要带**（`tiles_left` 在岭上摸牌时不动，只能看它）。 |
| `drawn` | `round_start`（仅庄家） | 本巡「刚摸到的那张」的牌码。`hand` 是**已排序**下发的，推不出来（§2.3-11）。 |
| `away` | `room.seats[]` | **掉线托管的唯一判据**；别拿 `bot` 或 session 空不空去猜（§2.3-13）。 |
| `uuid` / `issued` | `uuid_ask` / `uuid_ok` | `issued:true` = 服务端刚生成 → 客户端**必须落盘**；uuid 不进任何其它报文（§6.8）。 |
| `need` / `total` | `vote_*` | 门槛与分母（`need = total/2+1`，**严格过半**）；**客户端不许自己算**（§6.9）。 |

### 2.4 别做危险操作

- **不要按进程名批量杀 `node`。** DSH 的 Web GUI（`127.0.0.1:3080`）就跑在 node 上。
  要清理自己起的测试进程，**按端口查 PID** 再精确结束。
- 只改工作区 `C:\Users\HP\source\games\mahjong` 内的文件。

---

## 3. 环境与构建

### 3.1 工具链：脚本自己找，不写死路径

构建脚本按「显式参数 → 环境变量 → PATH → 常见安装位置 → 仓库内缓存 → **自动下载**」解析依赖，
所以**不必预装 Qt**（见 §3.3）。**本机实测可用的位置清单见 `NOTES.md` §3.1**（仅供参考，不是硬编码）。

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

**Qt 既不预装也不进仓库**：找不到就从 download.qt.io 自动取（qtbase+qtsvg ≈ 22 MB → 仓库根 `.qt/`，
已 gitignore；MinGW/CMake/Ninja 缺失时同样取），实现在 `tools/qt-provision.ps1`。
开关 `-Provision always|never` / `-QtDir` / `-Plan`，细节见 `client/README.md`。

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
下面是**要跑什么**；**每个脚本的注解**（耗时、退出码 2 的含义、为什么要那样采样、踩过的坑）
在 **`NOTES.md` §4**。

### L1 规则引擎（秒级，最常跑）

```powershell
java -jar server\build\mahjong-server.jar --selftest
# 期望：通过 N 项，失败 0 项 / SELFTEST PASS（当前 1370 项）
```

覆盖：牌编解码、向听、听牌、役种、符数、**完整打点表逐格比对**、授受守恒、包牌、不听罚符、振听、
立直条件、和牌选项下发、横置顺延、**杠后岭上摸牌的账**、**身份/托管/投票**、
**训练接口的全部不变式**（§6.5）、**teacher 的取舍**（§6.6），以及 **3 次「4 机器人整场半庄」**的
点数守恒。**改了 `rules/` / `game/` 下任何东西都要重跑。**

> ⏱ 全量约 **110~130 秒**（慢的是十来个"整场模拟"用例，不是断言数）。
> 新用例**优先用 `Table.debugMaxHands` 限小局数**；要"定向局面"就用现成钩子
> （`debugClaimOutcome` / `debugRonDeltas` / `debugTurnKan` / `debugDoRiichi` /
> `debugDrainWallTo` / `debugPushDiscard`），别靠发牌运气。

### L2 客户端自检（秒级）

```powershell
client\dist\mahjong-client.exe --selftest client\build\st
# 期望：检查项 N，失败 0 / SELFTEST PASS（当前 816 项）；并产出 tiles.png / table.png / river_overflow.png
```

覆盖：牌码↔kind 双向、NDJSON 编解码、`TableModel` 事件应用、手切/摸切、横置张数、
**风盘布局全部断言**（尺寸/居中共面/名牌不重叠/牌河固定左缘/超 18 张仍逐行）、
**身份与投票界面**（按钮可见性、组包、冷却）、掉线标记、并出图。

> ⚠ **看 PNG 不要用 `-platform offscreen`**：那个插件在 Windows 上没字体库，中文会画成空心方框。

### L3 协议端到端（e2e 几分钟，其余几十秒）

```powershell
java -jar server\build\mahjong-server.jar --port 10086   # 另开一个终端；要快可加 --fast
node tools\e2e-test.mjs 127.0.0.1 10086          # E2E PASS（整场东风战；含报文 ASCII 审计与点数守恒）
node tools\timeout-test.mjs 127.0.0.1 10086      # 超时摸切 + 真人全掉线后房间回收
node tools\uuid-test.mjs 127.0.0.1 10086         # 身份：连接即问/同 uuid 同一玩家/坏形状按无记录/**掉线接回原座位**
node tools\away-test.mjs 127.0.0.1 10086         # 托管：away=true·bot=false / 托管后只摸切 / 0 次鸣牌 / 牌局不结束
node tools\vote-test.mjs 127.0.0.1 10086         # 投票：分母=在场真人 / 够票→game_end{reason:vote} / 冷却 wait_ms
node tools\discard-align-test.mjs 127.0.0.1 10086 # 出牌对齐（幽灵手牌）：drawn 必带 + 错报摸切按牌码取牌
node tools\seat-swap-test.mjs 127.0.0.1 10086    # 换座/洗座：准备落在自己座位 + 座位表恒为四家排列
node tools\claim-priority-test.mjs 127.0.0.1 10086 3000  # 鸣牌优先级 + 废包不漏到下一巡（⚠ 采样不到 → 退出码 2，重跑）
node tools\clock-test.mjs 127.0.0.1 10086        # 思考时间：基本/额外扣减 + 鸣牌也扣
node tools\firstturn-test.mjs 127.0.0.1 10086 3000  # 第一巡实测等待 = 声明 deadline
node tools\riichi-stale-test.mjs 127.0.0.1 10086 3000  # 作废的立直不得替玩家打牌
node tools\spectate-test.mjs 127.0.0.1 10086     # 观战：公开快照 + 持续公开事件 + action 被拒（跑满 1~2 分钟）
node tools\utf8-test.mjs 127.0.0.1 10086         # 中文/代理对往返 + 截断不切坏字符
node tools\replay-test.mjs 127.0.0.1 10086       # 对局记录：写入/分页/守恒/路径穿越/限速（--no-game 只验读取）
node tools\bot-ai-test.mjs 127.0.0.1 10086       # 机器人 AI：清单/建房 bot_ai/换一代/未知名字与路径串被拒/非房主被拒
```

**静态检查（不用起服务端，改文案/码表必跑）**：

```powershell
node tools\check-i18n.mjs        # 服务端每个码都有客户端译文（词表哨兵）
node tools\i18n-scan.mjs --check # 源码里不许剩中文字面量
node tools\i18n-gen.mjs --check  # 映射表 ↔ 语言文件一致（不漏 key）
node tools\selfplay-check.mjs <轨迹目录>   # 训练数据集校验（独立实现，见 §6.5）
node tools\doc-refs-check.mjs   # 文档自检：AGENTS 预算 + 章节号完整 + 全仓 §引用可解（见 §8）
```

> ⏱ `e2e-test` 跑完整场要 4~8 分钟（机器人每步 ~800ms、局间 1.2s，**刻意放慢**）；
> 想快就 `--fast`，或临时调小 `Table.botDelayMs` / `roundDelayMs`。

### L4 GUI 定点复现（发现 UI bug 时首选）

**不要靠运气等牌**——用假服务端把客户端推进到目标状态再截图（`--shot` 用 Qt 的 `grab()`，不受屏幕裁剪影响）：

```powershell
node tools\mock-server.mjs 10999 turn|claim|note|river|agari|yakuman|kan|twoturn|sticks|hand2meld|allmeld|sfx|sfxburst|vote
# turn 自摸/立直/杠 · claim 荣和/碰/跳过 · note 无役提示 · river 横置顺延（顺带压测 5 张宝牌栏）
# agari 结算 + round_wait 倒计时 · yakuman 须写「2倍役满」不得出现「0 番」 · kan 岭上 4→3→2
# twoturn 跨局首巡不得只剩 1 秒 · sticks 立直棒按座位/供託居中 · hand2meld 自己 N 副露
# allmeld 四家都有副露（横置张与另两张**底边齐平** + 四角名牌互不重叠）
# sfx / sfxburst 音效场景驱动器（配 MAHJONG_SFX_TRACE=1）
# vote 结束对局投票界面（「同意/不同意」可点 + 状态行票数 + 「结束对局」置灰）
client\dist\mahjong-client.exe --demo 127.0.0.1 10999 --bots 3 --no-answer --shot client\build\shot.png --after 6
```

假服务端也带**身份握手**（连接即发 `uuid_ask`、收到 `uuid` 回 `uuid_ok{issued:true}`）——
顺带验证"客户端会把身份落盘"。**模式清单之外的细节见 NOTES §4 L4。**

### L5 真机联调（最贵，改动涉网络/流程时跑）

```powershell
client\dist\mahjong-client.exe --autoplay 127.0.0.1 10086 --name 联调 --timeout 200
# 结果写在 client\dist\autoplay.log，期望 AUTOPLAY PASS
```

`--autoplay` 会记录**每次 ask 的选项类型**，排查"某个按钮没出现"非常有用。
**客户端全部命令行模式**（`--selftest` / `--lobbytest` / `--autoplay` / `--demo` / `--replay` /
`--gentiles` / `--fontprobe`）的说明见 **NOTES §4 末表**。
## 5. 目录结构

```
mahjong/
├─ docs/             日本麻将.md 规则原文（权威依据）· PROTOCOL.md 协议契约 ★ 改协议先改这里 ·
│                    DESIGN.md 架构与取舍 · AUDIT.md 审计条目（S-nn）· DEPLOY.md Ubuntu 部署 ·
│                    TRAINING.md 机器学习训练方案（P0–P4 已落地）· **BOT-AI.md 机器人 AI 包格式**（§6.6）·
│                    THEME.md 材质包/设置 · THIRD-PARTY.md 许可义务 · images/ 截图资产
├─ server/           build.sh·run.sh（Ubuntu）/ build.ps1（Windows）+ src/main/java/mahjong/
│                    util(Json/Log) · core(Tiles Meld Rules Wall) · rules(Shanten Agari Evaluator
│                    Payments Visible HandEval) · game(Round Table + WinCheck/RoundOptions/
│                    RoundClaims/RoundScoring 这些纯判据) · replay(Replay Store Recorder)
│                    **player(PlayerStore —— 玩家档案，见 §6.8)** · net(Server Session)
│                    bot(Bot=牌效 AI/teacher) · ai(★训练接口：PolicyFactory/Observation/Action/
│                    **BotAis 机器人 AI 注册表**) · train(SelfPlay/TraceRecorder) ·
│                    test(SelfTest ★ 改规则在这里加断言)
├─ client/           build.ps1 + assets/{tiles(38 个 <牌码>.svg), i18n(<locale>.json), fonts} + src/
│                    src：main.cpp(入口/命令行模式) · SelfTest.cpp · AutoPlay.cpp · i18n/Lang
│                    net(NetClient/Protocol) · model(Tile TableModel AutoPolicy Settings Theme)
│                    ui(TileRenderer TableView ActionBar AutoBar LobbyDialog ResultDialog
│                       SettingsDialog MainWindow ReplayWindow WallView)
├─ tools/           联调脚本 + 静态检查 + 打包。**完整清单见 glob tools/\***（名字自解释）：
│                    *-test.mjs = 真 socket 回归（§4 L3）· i18n-* = 文案三件套 · mock-server = L4 假服务端 ·
│                    test-client.mjs = 联调共用小客户端 · doc-refs-check.mjs = 文档引用自检 ·
│                    package-release.ps1 + make-zip.mjs = 发布打包（NOTES §9.5）
└─ 运行时数据（**都不进仓库**，见 .gitignore）：`replays/` 对局记录 · `players/` 玩家档案 ·
                     `bot-ai/` 机器人 AI 包（与 jar/start.sh 同层，启动时自动挂载）
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
  **文件名 = 牌码**与 `viewBox="0 0 300 400"`，**重启客户端即生效、无需重编译**（占位符见 NOTES §9.1）。
- **结算界面走内嵌字体 + OpenType**：`I.MahjongJP.otf` 的 `liga` 连字默认生效、**无需任何 OpenType API**；
  字体取不到或串含非法字元时**整块不显示**、回退文字（详见 NOTES §9）。
- **UI 扁平化**：纯色 + 1px 描边，**牌片单层外框、不画厚度层**，不用渐变/阴影/发光（`TileRenderer` 的程序化绘制只作**回退**）。
- **文档分工**：`README.md` 面向玩家（操作、规则取舍、常见问题），**不含技术细节**；技术内容一律进
  AGENTS 与 `docs/`。**改了玩家看得见的行为（操作、开关、规则取舍、常见问题）就要同步 README。**
- **第三方许可**：客户端以 **LGPLv3 动态链接**使用 Qt，`client/licenses/` 随 `dist/` 一起分发
  （内含 LGPLv3 + GPLv3 全文与版权声明）。**不要删它，也不要给 Qt 的 DLL 加校验/签名锁定**
  —— 那会阻止用户替换 Qt，直接违反 LGPLv3。新增 Qt 模块或第三方库时同步 `docs/THIRD-PARTY.md`、
  `licenses/NOTICE.txt` 与 `build.ps1` 的必需 DLL 清单。

### 6.2 牌桌布局与绘制（客户端 —— 最容易被改坏的一块）

> **不变量清单**（改这里必须守住的东西）。**几何推导与"为什么是这个数"** —— 例如固定左缘是怎么定的、
> 区带怎么反推、踩过哪三次 —— 见 **`NOTES.md` §6.2**。自检里有一整套断言钉住下面每一条，
> **改完必须重跑 `client --selftest` 并看图**。

- **手牌区不能抖**：`TableView::layoutHand()` 的手牌左缘锚点按**满手 13 张**算（与当前张数无关）；
  摸牌**紧贴手牌**（间隔 `0.26 × 牌宽`），手牌因副露变短时摸牌一起左收。
- **副露钉在自家右下角**（`+u` 行末），**最旧的在最右、依次向左**（顺序只在 `meldLeftsOf()` 里算，
  绘制处**不要**改成从左往右）；一副副露**内部**仍左→右，被鸣那张按来源方位落位；整块会压到副露时
  **一起左移**（避让优先级最高），绝不重新居中。名牌沿对角线内收 `1.9 × 牌高`。
  **`ActionBar` 与投票条高度固定**（否则选项数/文案一变整桌重排）。
- **风盘尺寸由内容反推**：`computeLayout()` 先按**盘内**（5 张宝牌指示牌 / 四家点数 / 场次 / 立直棒）
  与**盘外**（四家牌河各 3 行）算 `cw/ch` → 钳制 → **最后把 `ch` 补到接近 `cw`**（漏这步盘会偏扁；
  补高只影响盘内留白与牌河起步距离，**不会挤到手牌**）。比例（相对手牌宽）：手牌 `1.00` /
  **牌河·副露 `0.798`** / **宝牌指示牌 `0.76`**；`riverPad = 0.35 × 牌河牌高`。
- **牌河一行最多 1 张横置牌**：最坏一行 = 横置 + 5×普通 + 5×列间距（**别按"整行全横置"估**）；
  `kRiverColGap = 0.09` / `kRiverRowGap = 0.08` 在**钳制与两处绘制**里必须用**同一份**。
  ⚠ **行数不封顶**：一行 6 列，18 张排满后继续排 4、5 行（`riverRowsFor(n)`，布局与绘制**同源**）。
- **牌河横向起点 = 固定左缘**（报障过两次）：按"一行 6 张普通牌 + 5 个列间距"算最左沿，
  **与已打几张无关**；⛔ **不把横置牌的额外宽度算进去**（否则每行右侧空一截）。
  绘制与飞行动画终点都读 `riverFullRowExtent()` / `riverLeftU()`。
- **风盘区带四边同构**：盘边一条**立直棒带** + 内侧一条**点数带**；四家点数到盘边**处处等距**、
  四个得点框**同尺寸且沿各自那条边居中**。立直棒画在**各家自己面前**（左右两家竖放），
  多余的供託画在盘中央「供託 N」下（**别塞给某一家**）；`kStickRatio = 4.4`。
- **宝牌指示牌行只占中间列**、**不画「宝牌」字样**、宽度不够时**宽高同缩**
  （`tw = min(行高/1.36, 可用宽/张数)`）→ 5 张永远落在四家分数框以内。中间「场次」块顶边
  `midTop = qMax(対面点数下沿, 宝牌行下沿)`；⚠ **左右两条竖排点数带不跟着取这个 `qMax`**。
- **河区预留 = 风盘向外等距扩一圈**（`riverExtent`，**只算布局不绘制**）：
  ⚠ **删那圈线框时不要连预算一起删**（预算没了四家的河会压上手牌）。
- **牌河不得压到邻家的河**：界限用 `max(cw,ch)/2 + kGap`（**不是 min**）。
- **副露几何只有一份**（`meldSlotRects()`，绘制与自检共用）：三张**底边齐平**
  （横置那张顶边 = `my + (riverH − riverW)`，**不是** `(riverH − riverW)/2`）；加杠第 4 张**叠在第 1 格**；
  ⚠ 牌河里那张横置牌**不改**（它是网格里的一格，按高度居中）。
- **名牌（ID 框）四角轮转一位**：自家**右下**，其余三家跟着转（下家→右上、对家→左上、上家→左下）；
  四个角**必须各占一个**（只挪自家会与下家重叠）。
- **「手牌 + 摸牌」块的边界避让：一次算完 + 右移封顶**（同一个坑踩过三次，别简化）：
  `layoutHand()` 里只允许**一个** `over`，先取 `max`（副露 / 角落名牌）再让行首角落让步；
  右移量**封顶**且同时看两边；两个边界都满足不了时**宁可压名牌，也绝不压副露**。
- **音效是"池子 + 时长对账"**（`model/Sound.cpp`）：每个音效 3 个实例，优先用**真正空闲**的。
  ⚠ **"还在播"不能只信 `QSoundEffect::isPlaying()`**（设备异常后它恒为真 → 会把该音效**永久静音**），
  要与 **WAV 时长**对账判"卡死"。判据抽成**纯函数** `sound::pickSlot(playing[], ageMs[], durMs,
  allowOverlap)`（自检直接喂合成输入）：卡死 → `stop()` 后复用；`allowOverlap=false` 的"别叠"
  只对真在播生效；池子都在真播时**放弃这一次**。排查：`MAHJONG_SFX_TRACE=1`。
- **座位方位**：`pos = (seat − mySeat + 4) % 4` → `0=下(自己) 1=右 2=上 3=左`。每家在自己**局部坐标系**
  里绘制再整体旋转到屏幕 —— 侧家的牌自然横置、"横置以牌主视角判定"自动成立。
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
  → 下一局 `round_start`。**服务端两条铁律**：`drainConfirm()` 只从 `submit()` 写的**那条队列**取
  （非 confirm 的原序放回）；`awaitAction()` 认领时必须**确认它是动作**（否则残留 `confirm` 被当出牌）。
  **客户端三条配合**：`round_wait` 起倒计时、到点自动关弹窗（= `confirm`）；`round_start`/`game_end`
  强制关弹窗但**不发 `confirm`**（`closeResultDialog(confirm=false)` 的唯一用途）；弹窗已关才立刻 `confirm`。
  ⚠ 三条都踩过（§2.3-8），明细见 `NOTES.md` §6.4.2。

### 6.4 协议、文案与结算

> 这里是**判据**（改协议/文案/结算时必须守的）。**完整纪律、样例报文与踩坑经过**见 **`NOTES.md` §6.4**。

**① 报文里不带中文 —— 词汇性文本一律只发 ASCII 码，中文只存在于客户端语言文件里。**
两端都显式钉死 UTF-8（服务端 `StandardCharsets.UTF_8`、客户端 `QJsonDocument`）。

- **只有三类字段是用户数据**：`seats[].name` / `room.name`·`hello_ok.name` / `chat.text`；
  其余全是**码**：事件名、`type`、`win_note`、牌码、`yaku[].code`（+ 参数化役种的 `tile`）、
  `limit`、`reason`、`error.code` / `error.arg`。判据一律用 ASCII，**客户端不许拿中文串做逻辑判断**
  （那等于把服务端文案当协议常量）。
- **码表的唯一映射处** = `server/.../rules/YakuCodes.java`：`Evaluator` 内部继续用中文名（日志好读），
  只有发报文时才翻成码。⚠ **新增役种必须在那里登记**，否则整场模拟自检会因 `YakuCodes.misses() > 0` 红。
- **语言文件** `client/assets/i18n/<locale>.json`（键 = `<族>.<码>`）：加载顺序与 `tiles/` 同约定
  （**exe 同级 `i18n/` → qrc → 码本身**）→ **改文案不用重编译**。⚠ 认不出的码**原样显示码本身**
  （一眼可见"服务端加了码、语言文件没跟上"）；码为空串时回退老字段（`yaku[].name` / `error.msg`）。
- **界面固定文案全在 `ui.*`**（源码里不留中文字面量，例外用 `// i18n-keep`，如牌面字形「萬」「東」）。
  **新文案走三件套**：`tools/i18n-map.mjs`（唯一数据源）→ `i18n-apply.mjs` → `i18n-gen.mjs`（只补缺）
  —— **先写字面量再加进 map**，直接写 key 会漏掉 json 条目；改文案直接编辑 json。
- **截断按「码点」不按 UTF-16 码元**（`Json.clampCodePoints`：`substring` 会把代理对切成半个 → `?`）。
- ⚠ **Java 陷阱**：注释里也不能写「反斜杠 + u」（词法分析前就处理 Unicode 转义）。
  ⚠ **客户端陷阱**：中文一律 `QStringLiteral` / `QString::fromUtf8`，别用 `QLatin1String`。
- **回归**：`SelfTest.jsonEncodingTests` / `yakuCodesTests` + L2 的 Lang 组 + `check-i18n` /
  `i18n-gen --check` + `utf8-test` + `e2e-test`（逐条非 ASCII 审计，白名单只有 `name`/`text`/`msg`）。
  完整纪律与样例见 `NOTES.md` §6.4。

**② 结算界面：役满写「n 倍役满」，不写番数**（役满**没有番这个量纲**）。

- 报文里 `yaku[].han` / 合计 `han` **不能是 0**（老客户端会显示「0 番」）：按「役满 = 13 番等价」折算
  （`13 × 倍数`），于是**逐役之和 == 合计**；真正的量纲在 `yakuman` 字段。
- **累计役满**（番数 ≥13 但没有役满役，`yakuman == 0`）**不是役满**：照常显示番数与 `limit`。
- 界面判据**一律看 `yakuman` 字段**，不要用 `han >= 13` 去猜。
  回归：L2 的 ResultDialog 组 + `node tools\mock-server.mjs … yakuman` 截图。

**③ 结算界面：能用图形表示的就不再重复文字** —— `agariHtml()` 只在 `schematicRenderable(schematicOf(...))`
为假（字体缺失 / 牌码串非法）时才补「手牌 / 宝牌指示牌 / 里宝指示牌」三行文字；
该判据与构造函数显示图形块的判据**必须共用同一个函数**（否则会出现"文字删了、图形也没画"的信息真空）。
**数字类信息（宝牌/赤宝/里宝张数、番符、点数收支）照常保留**（图形给不出这些数）。

**④ 鸣牌仲裁：优先级、提前收工、队列卫生**（三条一体，判据见 §2.3-10）。实现位置：
`RoundClaims.rankOf/canBeat`（等级 → 座次，**与仲裁循环同一把尺子**）· `RoundClaims.shouldStop`
（`askedBestRank` 缺项时**保守继续等**）· `Round.claimPhase` 末尾的 `cancelAsk` + `dropReplies`
+ `askedTypes` 校验。

**⑤ 王牌/岭上与杠**：王牌 14 张 = 4 岭上 + 5 表宝牌指示牌 + 5 里宝指示牌；**开杠那一刻**牌山末尾移进王牌
（`tiles_left` −1），**岭上摸牌**不动 `tiles_left`（只看 `dead_wall_left`，每次摸牌都要带）；
一局最多 4 次杠（四个闸门统一走 `Round.canKan()`）；**被拒绝的杠绝不能摸岭上牌**；
四杠散了在"那张牌落地且没人和"时才判（三种豁免由和了路径先 return 天然满足）。
⚠ 自检看**选项**用 `debugAskTap`、要连**鸣牌**一起看只能用 `debugChoiceTap`（前者挂在 `ask()` 上，
而鸣牌段不走 `ask()`）。**细节见 NOTES §6.4。**

**⑥ 服务端并发模型**：**每桌一个线程串行推进状态机**，对局内的命令只往队列投消息 → **对局逻辑内部无需加锁**。
⚠ 但**等待室命令是各连接自己的线程直接执行的**（`create/join/leave/ready/take_seat/shuffle_seats/
start_game/add_bot/remove_bot`），读写的却是同一份座位数组 → 房间状态有**一把锁** `Table.roomLock`：
把「判据 + 改座位 + `broadcastRoom`」放进 `synchronized (t.roomLock())`，牌桌线程在**同一把锁**里置
`playing = true`（否则 `take_seat` 与"开始发牌"交错 → 按座位号发的牌落到别人连接上）。
⚠ 另一条独立坑：换座必须 `resyncSessionSeats()`，且等待室命令按 `Table.seatOfSession(this)` **反查**
自己的座位（不信 `session.seat`）—— 否则「准备」会写到别人座位上（牌局永远开不了）。
**细节与探针记录见 NOTES §6.4。**

**⑦ 赤宝牌张数**：`aka == 0` **支持**（发牌期就把赤五换成普通五，136 张不变）；`aka == 4` 受牌 id 编码
限制**不支持**（按 3 处理）→ 见 NOTES §10 与 `SelfTest.akaRuleTests`。
### 6.5 训练接口（机器学习 / 自对弈）

**权威描述在 `docs/PROTOCOL.md` §8**（字段表、动作键文法、CLI、数据格式）；**完整实现说明见
`NOTES.md` §6.5**。这里只留"改代码时必须守"的几条：

- **只有一个决策漏斗**：`Table.decideBot(seat, Decision)`。自家摸打（`Round.ask`）与鸣牌段都汇到它；
  `policy[seat] == null` 时走内置 `Bot`。**不要再往第三个地方直接调 `Bot.decide`**。
- ⚠ **鸣牌段不走 `Round.ask()`** → `debugAskTap` **看不到鸣牌**（这个坑真绊过一次）；
  任何"每次询问都要做的事"都挂在漏斗上（用 `debugChoiceTap` 才看得到两段）。
- **策略不许把异常抛给牌桌线程**：`decideBot` 必须兜底（异常 / 返回 `null` → 内置机器人），
  否则一局异常会让**整场半庄静默死亡**。
- **训练侧只实现 `ActionPolicy`（拿不到 `Round`）**：`Round` 里有别家手牌、牌山顺序、里宝 ——
  进程内一不留神就训出**作弊**模型，且训练与评测同时失效、还查不出来。
- **观测只许含合法信息**：新增字段必须三处一起改 —— ① 确认公开可见；② `Observation` 字段白名单
  （`SelfTest.trainingInterfaceTests`）+ `tools/selfplay-check.mjs` 的 `OBS_KEYS`；③ PROTOCOL §8.2 的表。
  ⚠ 自家回合**别调 `Round.isFuriten()`**（14 张时等价于两个标志位，却会白跑 34 次向听 DFS）。
- **可复现是硬要求**：`debugDeterministicSeed=true` + **每局一份策略实例**（`PolicyFactory`）+
  每场种子预先算好（`SelfPlay.seedFor`）；策略跨局带状态会让同 seed 不再产出同一轨迹。
- **奖励是事后回填的**：`hand_delta` / `placement` 在小局、整场结束时才补；`placement` 必须是
  `1..4` 的排列（同点按座次拆开），否则"平均顺位"没有意义。
- **不给服务端加 ML 依赖**（Maven/ONNX/PyTorch）：外面训练、导出权重、**纯 Java 手写前向**。
- **改产出格式就三处一起改**：`TraceRecorder` / PROTOCOL §8.4 / `tools/selfplay-check.mjs`，并重跑
  `node tools\selfplay-check.mjs <dir>`。
- **P2 DAgger 轮的对比口径**（判据）：`--teacher-label`（学生座位额外记 `teacher` / `teacher_index`）
  + `python -m mahjong_ml.dagger` 编排一轮。⚠ 三条不许省：① **对比只能在"两个模型都没训过"的
  场次上做**（受控切分：评测集取混合集 val ∩ 来源、全 val 建集，跑时有硬闸门）；② 显著性**按场聚类**
  （同场决策不独立，逐行 bootstrap 会假显著）；③ 必须带**同数据量的 BC-only 对照臂**，否则
  "数据变多了"会被记成"DAgger 有用"。细节见 `NOTES.md` §6.5 与 `docs/TRAINING.md` §4 P2。
- **P5b 混合（teacher 先验）**：策略串 `net:<权重文件>@<α>` = `argmax(student + α·1[该候选 == 老师动作])`。
  ① **先验只能加在 logit 上**（α 极大 ⇒ argmax 必是老师那条 ⇒ "不会比老师差"是**构造出来的**，
  不是"大概"）；② 混合必须是 **`Policy` 级组合**（与 `TEACHER` 同级）而**不是 `ActionPolicy`** ——
  先验要调 `Bot.decide(Round, …)`，而 `ActionPolicy` 是故意拿不到 `Round` 的（反作弊口径）；
  ③ α 随**训练过的网**的 logit 尺度走，别照抄常数（未训练的网 α=0.25 就 100% 让位）——
  用 `python -m mahjong_ml.hybrid` 量"让位曲线"再选。回归：`SelfTest.hybridPolicyTests`。
- **P4 探索口**：策略串 `net:<权重文件>[@<α>][#<T>]` = 按温度从 `softmax(logits/T)` 采样
  （`T≤0`/省略 = argmax，与加它之前**逐决策相同**）。⚠ 随机源必须**每局按 `(seat, gameSeed)` 派生**
  （`Policies.mixSeed`），跨局共享会破坏"同种子可复现"。在线 PPO 另两条硬口径：**λ=1**（奖励只在
  末决策记一次，λ<1 是系统性偏差；λ=1 时 GAE 恰好塌成 `A=R−V`）、**优势只在 `is_student==1` 的行上
  归一化**（对手行不进策略损失）。回归：`SelfTest.samplingPolicyTests` + `python/selfcheck.py` 的 P4 组。
- 回归：`SelfTest.trainingInterfaceTests` + `tools\selfplay-check.mjs`。
### 6.6 teacher（内置机器人）的五层取舍

`Bot` 同时是**补位机器人**和**训练用的 teacher**：它怎么打，直接决定自对弈数据集里的标签质量
（**改 teacher ＝ 改训练标签**）。它的取舍分五层：**牌效 / 押し引き / 打点与役 / 开杠 / 副露打分**，
外加**默听**、**顺位与终局**、**对手模型**（危险度）。

⚠ **改 teacher 之后**：① L1 里 `teacherTests` 的取舍统计会变（那里是"它至少做过这些决定"的非空转断言）；
② 之前用 `--selfplay --out` 出的轨迹与新 teacher 不再同源，**别混着训练**；
③ 危险度判据在 `rules/Danger.java`（纯函数，可单测）。

**完整策略说明（每层的判据、权重与理由）见 `NOTES.md` §6.6。**
配套：`docs/DESIGN.md` 的「AI 取舍」、`§8.2` 的特征判据表（`HandEval` / `Danger` / `Visible` / `scoreIfWin`）。

**机器人用哪一代 AI**（`mahjong.ai.BotAis`，2026-09）：房间可选机器人座位用**哪一代**
（内置 teacher / 各代训练网络），但**客户端只传注册表里的名字** —— `net:<路径>` 是**服务器本机文件**，
让房间指定路径就等于开放"读服务器任意文件"。清单来自**启动时自动挂载的 `bot-ai/` 包目录**
（与 `replays/` 同层；一个一级子目录 = 一个包 = `bot.json` + 载荷，**深度模型与启发搜索同一套接口**），
`--bot-ai` / `--bot-ai-reg` / `--bot-ai-dir` 可覆盖（后面的赢）。**格式与打包见 `docs/BOT-AI.md`**；
装配每场一次、只装机器人座位、坏包只跳过它一个（细节见 `NOTES.md` §6.6）。
回归：`SelfTest.botAiTests` + `client --selftest` 的「机器人 AI」组 + `node tools\bot-ai-test.mjs`。
### 6.7 无用代码清理（可重复的判据）

**`node tools/deadcode-scan.mjs [java|cpp]`** 扫出"只在声明/定义处出现"的函数 —— 2026-09 的一次全仓清理
就是靠它：服务端删了 **27** 个、客户端删了 **10** 个（另清掉 6 条随之失效的 `import`）。
**删掉的完整名单、以及"为什么它们确实没人调"见 `NOTES.md` §6.7。**

⚠ 它给的是**候选**，不是判决书 —— 反射、宏、Qt 元对象（信号槽）、虚函数/接口实现、以及"故意留给外部使用者"
的公开 API 都会漏判。删之前按这个顺序过一遍：

1. 读一眼那段代码，确认没有副作用（初始化、注册、JNI/反射入口）；
2. `override` / `virtual` / `signals:` / `slots:` / `Q_INVOKABLE` 段里的一律**别删**；
3. **文档里写成契约的不删**：例 `Visible.unseen` 只被 PROTOCOL §8 的观测字段表引用（代码里没人调），
   那也算"在用"，扫描器照样会报它 —— 这类要**留着并在代码注释里写清为什么**；
4. 删完**编译 + 跑对应层自检**（服务端 `--selftest`、客户端 `--selftest`）—— 这一步才是判据：
   有测试引用就会被抓出来（本次清理的实测数字见 `NOTES.md` §6.7）。

顺带：`server/build/unused-imports.mjs` 一类的临时脚本不必进仓库；真正可复用的（扫描器）放 `tools/`。

### 6.8 身份（uuid）与玩家档案

需求（2026-09）：**连接后服务端立刻问客户端要一个保存过的 uuid**，客户端应答；客户端没有就由服务端
生成并回发（客户端必须存下来）；服务端没有这个 uuid 的档案就建一份**初始玩家**。
**同一个 uuid = 同一个玩家**，显示仍以昵称为准；每个 uuid 记一个时间戳、登录时更新；
定期清理**超过 2 个月**没登录的。uuid 将来是**玩家信息的主键**。协议见 PROTOCOL §2.0。

- 服务端：`server/.../player/PlayerStore.java`（进程级单例，和 `ReplayStore` 一个套路）。
  落盘 `<--player-dir>/players.json`（默认 `players/`，`--uuid-ttl-days` 默认 60，`--no-player-store` 关掉）。
  - **档案里认不出的键原样保留** —— 它就是为"以后要当主键存更多东西"准备的（同 `Settings.extra`）。
  - **原子写**（`.tmp` + ATOMIC_MOVE）+ **落盘节流**（登录是热路径，2 秒内的多次登录合并成一次写）。
  - 清理：**启动时一次**，之后维护线程每 6 小时一次；判据是**严格大于** TTL（`ttl=0` 也能对）。
- 客户端：`Settings.uuid`（`settings.json`；形状不对就清空 = "我还没有身份"）+
  `MainWindow` 的 `uuid_ask` / `uuid_ok` 两个分支（**`issued:true` 时必须落盘**，否则下次又变成新玩家）。
- **接回座位**：uuid 此刻若正**托管**在某个座位上（§6.9/§2.3-13），新连接直接接回那个座位
  （`Session.tryResumeSeat`：锁内二次确认"那一格确实空着"→ 沿用**原 pid** → `room_joined` + `state`）。
  ⚠ 判据是"座位上没有别的活连接"，不是"uuid 认得" —— 否则重放一份设置文件就成了踢人手段。
- 回归：`SelfTest.playerStoreTests`（形状/记账/TTL/向前兼容/坏文件）+ `node tools\uuid-test.mjs`（真 socket）。

### 6.9 掉线托管 与 结束对局投票

两件事都写在协议里（PROTOCOL §2.5 / §3.13），共同点是**状态不在牌局线程上**：

- **托管**（`Seat.away`）：判据、行为与坑见 §2.3-13。实现三处：
  `Table.markAway`（掉线/退房都走它，含**房主转移**）、`Round.awaySeat`（那一家的回合走"返回 null →
  默认摸切"，鸣牌段**连选项都不算**）、`Table.releaseAwaySeats`（整场结束后释放托管座位）。
  ⚠ 「托管」与「空位」必须一直分开：`occupied()` 含 `away`（座位不会被抢），但 `readyToStart()` 不认它
  （托管的人不能算"已准备"）。
- **投票**（`vote_*`）：`Table.requestVoteEnd/castVote/onVoteTimeout`，状态机在 `voteLock` 下，
  事件**从各连接的读线程广播**（所以 `ReplayRecorder.add` 是 `synchronized`、`Table.replay` 是 `volatile`）。
  - 门槛 `votesNeeded(total) = total/2 + 1`，分母 `eligibleVoters()` = **在场真人**（不数机器人、不数托管）；
  - **发起人算同意**（点「结束对局」就是在表态）；
  - 够票→`passed`（→`voteEnded` + `wake()`，见 §2.3-14）；不够票→`impossible`；到点→`timeout`；
  - 一次结论之后**全员冷却 5 分钟**（`VOTE_COOLDOWN_MS`，**服务端记时**：拒绝时回 `wait_ms`）；
  - 通过之后 `playGame` 走**正常终局路径**（发 `game_end{reason:"vote"}`、按规则分掉供託、落盘回放），
    所以**点数仍然守恒**（`SelfTest`/`vote-test` 都断言 100000）。
- 回归：`SelfTest.voteTests`（含"真的开线程投票把牌局线程叫醒"那条）+ `node tools\vote-test.mjs`。

## 7. 常见症状 → 先查哪里

> **本节是"高频速查"**（改代码/报障时最常撞上的那些）。**全量症状表（约 70 条，含训练接口、
> 素材字体、布局细节等专题）见 `NOTES.md` §7** —— 那条表里每个症状都有完整的"首先怀疑"说明。

| 症状 | 首先怀疑 |
| --- | --- |
| 编译不过 / 链接失败 | exe 是否在运行（锁文件）；AUTOMOC 缓存陈旧 → `-Clean` |
| 客户端连不上（`Connection refused`） | 服务端没起 / 端口错 / **WSL 只转发到 `[::1]`**（客户端已自动 IPv4↔IPv6 回退；WSL 填 `localhost`） |
| 大厅按钮是灰的 | `MainWindow::onConnected()` 必须调 `m_lobby->setConnected(true)`（曾漏过） |
| **掉线后那一格变成机器人（会吃碰杠/胡牌）**，或掉线就没法继续 | `Table.markAway` 是不是把 `bot` 置真了？托管只该置 `away`（§2.3-13）。定性：`node tools\away-test.mjs` |
| **对局中有人掉线，其他三家突然看到"准备/开始游戏"** | 对局中的 `room` 事件被当成"回等待室"了：`MainWindow` 的 `room` 分支要看 `playing`（L2 有断言） |
| **掉线的人重连没回原座位 / 每次连上都是"新玩家"** | ① `uuid_ok{issued:true}` 有没有**落盘**（`MainWindow::saveIdentity`）？② 入座/认领时有没有把 uuid 写到座位上（`Session.claimIdentity`）？定性：`node tools\uuid-test.mjs` |
| **投票通过了牌局还在等出牌/还在打** | §2.3-14：`finishVoteLocked` 要 `wake()`，且**五个检查点**都要看 `voteEnded`。定性：`node tools\vote-test.mjs` |
| 点了「结束对局」没反应 / 一直显示冷却 | 看 `vote_denied.reason`（`not_playing` / `running` / `cooldown`）；冷却**只在服务端**记时 |
| 手牌数量对不上 | `tsumogiri` 用了吗？有没有靠 kind 猜？（§2.3-11） |
| **庄家第一巡点牌却打出另一张 / 张数对但内容差一张（幽灵手牌）** | `round_start.drawn` 有没有发？出牌取牌是不是按**牌码**？客户端对账是不是也按牌码？见 §2.3-11；定性：`node tools\discard-align-test.mjs` |
| 一人牌河两张横置 | `riichi` 事件里是不是又去标"最后一张"了？横置**只认 `discard.sideways`**（§2.3-1） |
| 鸣牌后牌河对不上 | `meld` 的 `called_index` 有没有 `removeAt`（鸣牌是"移动"不是"复制"） |
| 换座后点「准备」没反应 / 把别人设成已准备 | 被换走那家的 `session.seat` 没跟着改 → 必须 `resyncSessionSeats()` + 用 `seatOfSession(this)` 反查（§6.3）。定性：`node tools\seat-swap-test.mjs` |
| **某家"没动就被代打"** | 废包（被取消询问的迟到回包 / 局间残留的 `confirm`）被当成本巡答复：`dropReplies` + `type ∈ option.type` + 认领要确认"是动作"（§2.3-10、§2.3-8） |
| 门前役全不生效 | `menzen[]` 是不是漏了初始化？（§2.3-2） |
| 和了形误判 | 向听 DFS 里 `melds + partials` 没封顶到 4（§2.3-3） |
| 放过一张荣和牌后马上又能荣和同一张 | 振听三种记在哪、有没有被鸣走的舍牌账（§2.3-12） |
| 杠后「岭上」数不减 | `dead_wall_left` 必须**每次摸牌都带**（`tiles_left` 在岭上摸牌时不动，见 PROTOCOL §3.4） |
| 役满显示成「0 番」 / 结算既画牌又写字 | 界面判据一律看 `yakuman` 字段；图形块与文字块共用 `schematicRenderable()`（§6.4） |
| **界面显示成 `yaku.riichi` 这种裸键** | 语言文件缺 key（`check-i18n` / `i18n-gen --check`）；认不出的码**原样显示码**是刻意的 |
| 中文/玩家名变乱码或 `?` | 显式 UTF-8；截断按**码点**（`Json.clampCodePoints`）；别用 `QLatin1String` 接中文 |
| 改了 `zh_CN.json` 界面还是旧文案 | 客户端要重启（语言文件是**运行时**从 exe 同级 `i18n/` 读的） |
| 新增界面文案忘了搬 | 三件套流程：字面量 → `i18n-map.mjs` → `i18n-apply.mjs` → `i18n-gen.mjs`（§6.4） |
| 牌河/副露牌太小、盘偏扁、立直棒叠点数 | §6.2 的不变量（比例、区带、固定左缘）；改完**必须看图**（L2 出 `table.png`） |
| `dist\` 里的 exe 不是最新 | 带 `-Deploy` 重新构建（`build\` 每次更新，`dist\` 只在 `-Deploy` 时更新） |
| **训练盘（`S:`）采集/训练中途开始报 `AccessDeniedException`、读报 `ERROR_IO_DEVICE`** | **先去查盘符还在不在**（`[System.IO.DriveInfo]::GetDrives()`）：卷掉了也长这样（目录一度还能枚举、余量正常），⛔ 别误诊成沙箱权限去放宽沙箱或改路径。判据与事故记录：`NOTES.md` §6.5、`docs/TRAINING.md` §4 P4 第二轮 |
## 8. 文档维护约定（AGENTS.md / NOTES.md 的分工）

**本文件是"每次会话自动加载"的工作区指令，而指令预算只有 64 KB —— 一旦超过，后面的章节会被
静默截断（不报错、也没人发现）。** 所以：

| 写进 `AGENTS.md` | 写进 `NOTES.md` |
| --- | --- |
| **判据**：再遇到同类问题代码该怎么写（§2 铁律、§6 约定的"不许/必须"） | **案例**：报障原文、根因推导过程、红证输出、审计来源 |
| **命令**：要跑什么、期望看到什么（§3 构建、§4 五层） | **注解**：为什么这样采样、耗时、退出码 2 的含义、踩过的坑 |
| **不变量**：改这块必须守住的东西（§6.2 布局、§6.4 文案纪律） | **推导**：几何怎么反推出来的、数值是怎么试出来的 |
| **速查表**：症状 → 首先怀疑（§7）、目录地图（§5） | **全量清单**：约 70 条症状的长解释、素材/字体/发布管线细节 |
| **指针**：一句话说明"细节在哪" | **已知限制**：未支持 / 刻意取舍 / 欠断言（NOTES §10） |

规矩三条：① **新坑先加 §2.3**（一条一判据、**编号连续**），详情写进 NOTES **同编号** ——
`§2.3-n` 被代码注释与 docs 大量引用（`grep '§2\.3-'`），**不要重排**；② **章节号两份文件一一对应**，
跨文件引用写全（`AGENTS.md §x.y` / `NOTES.md §x.y`），改了号要 `grep -rn 'AGENTS §\|NOTES §'` 一起改；
⚠ **`NOTES.md` 独有**的章节一律取编号 **≥10**（AGENTS 里同号常是别的主题：AGENTS §8 是文档维护，
而"已知限制"原来也叫 §8，于是「见 §8」指不明白 —— 现已改号为 **NOTES §10**）；
③ **改完跑 `node tools\doc-refs-check.mjs`**：它会查 AGENTS 的**字节预算**、章节号**完整性**、
   以及全仓 `§引用`是否**都解得出目标**（拆分/搬章节时最该跑的一条）。

其它：`README.md` 面向玩家（改了玩家看得见的行为就同步）；`docs/PROTOCOL.md` 两端唯一契约
（**改协议先改它**）；`docs/DESIGN.md` 架构与取舍；`docs/AUDIT.md` 审计条目（`S-nn`）；`docs/DEPLOY.md` 部署。
## 9. 素材与字体管线（牌面 SVG + 结算字体 + 音效 + 打包）

**要改素材/字体/音效或发布打包时才需要读这一节**（细节见 `NOTES.md` §9）。速记：

- **牌面 = `client/assets/tiles/<牌码>.svg`**（38 个 = 37 种牌 + `back.svg`），构建时拷到 exe 同级 `tiles/`；
  加载顺序 **目录 → qrc → 程序化绘制**（删掉整个 `tiles/` 也不白屏）。换素材只要保持
  **文件名 = 牌码** 与 `viewBox="0 0 300 400"`，**重启客户端即生效、不用重编译**。
- **结算界面走内嵌字体 + OpenType 连字**：`client/assets/fonts/I.MahjongJP.otf`（`liga` 默认生效，
  不需要任何 OpenType API）；字体取不到或串含非法字元时**整块不显示**、回退文字。
- **UI 扁平化**：纯色 + 1px 描边，牌片**单层外框、不画厚度层**，不用渐变/阴影/发光。
- **音效是离线合成的 WAV**（`tools/gen-sfx.mjs` + `gen-sfx-qrc.mjs`），加载顺序与素材同一套约定；
  运行时行为（池子/时长对账）见 §6.2 最后几条。
- **发布打包**：`tools/package-release.ps1`（服务端用 `tools/make-zip.mjs` 手写 zip ——
  ⚠ **必须保留 Unix 可执行位**，`.NET Compress-Archive` 写出来的 zip 在 Linux 上 `./start.sh` 会
  `Permission denied`）。服务端包结构、两个资产的命名与 release 流程见 **NOTES §9.5**。
