# AGENTS.md — 立直麻将（日本麻将）联网对战

<!-- FOCUS:BEGIN —— 只改这里（判据=第 8.1 节） -->
- **焦点：当前主线 = 训练**
- **生效域**：`python/**`·`trainer/**`·`lab/**`·`docs/TRAINING*.md`·`docs/TRAINER-CPP.md`·`docs/FEATURES-V4.md`·`docs/BOT-AI.md`·`NOTES.md §6.5`
- **先读**：`AGENTS-TRAIN.md` → 现行上下文；历史+纠错 `AGENTS-TRAIN-LEDGER.md`
- **非当前域**：`AGENTS-SERVER.md`·`AGENTS-CLIENT.md`·`AGENTS-PROTO.md`·`AGENTS-SYMPTOM.md`
- **改指判据**（三条**同时**）：① ≥2 个工作轮全落在同一非当前域；② 这 2 轮没碰当前域；③ 该域已有切片
- **改完必跑**：`node tools\doc-refs-check.mjs`（第 8.1 节）
<!-- FOCUS:END -->

## 路由：我要…… ⇒ 读这里（域切片；⛔ 判据本体不复制，只指路）

| 我要…… | 读这里 |
| --- | --- |
| 管**文档纪律 / 指令预算 / 切片与路由协议** | 本文件 §8（唯一权威） |
| 查**高频症状 → 先怀疑哪里** | `AGENTS-SYMPTOM.md`（§7；全量 70 条见 `NOTES.md` §7） |
| 改**客户端**牌桌几何 / 绘制 / 素材 / 音效 | `AGENTS-CLIENT.md`（§6.2） |
| 改**服务端**牌桌行为 / 规则资源 / 局间时序 | `AGENTS-SERVER.md`（§6.3） |
| 改**协议 / 文案 i18n / 结算 / 身份 / 托管 / 投票** | `AGENTS-PROTO.md`（§6.4 + §6.8 + §6.9） |
| 改**训练 / 自对弈 / teacher / 权重** | `AGENTS-TRAIN.md` → `AGENTS-TRAIN-LEDGER.md`；判据见本文件 §6.5 |
| 我要动**别的域**（上表没有） | 先读本文件 §2 铁律，再按 `<FOCUS:BEGIN>` 改指判据决定是否转交 |


给在本仓库工作的 AI agent / 新同事的操作手册。**先读这份，再动代码。**

---

## 0. 怎么用这份文档

**三份文件，一套章节号**：**本文件 = 手册**（判据 / 命令 / 不变量 / 速查表，**动代码前必读**）·
**`NOTES.md` = 细节分册**（案例 / 红证 / 推导 / 全量症状 / 已知限制，**章节号一一对应**）·
**`docs/INDEX.md` = 文档总索引**（六个板块 · 目录树 · 文档清单；玩家看 `README.md`）·
⚠ **接手/续接会话先读 `docs/HANDOVER.md`**（进度 · 判据 · 下一步 · 环境命令 · 待决问题）。

> ⚠ **指令预算只有 64 KB**：超了会被**静默截断**（后面章节等于不存在、不报错）⇒ **判据留下、细节进
> `NOTES.md`**（见 §8）。

**阅读顺序**：① §2 铁律（违反必出 bug）→ ② §3/§4 构建与验证五层 → ③ §6 代码约定 → ④ §7 症状表 → 其余需要时再翻。
### 2.3 十六条曾经踩过的坑（同类问题会再犯）

> 这里只有**判据**（"再遇到同类问题，代码该怎么写"）。每条的**报障原文 / 根因推导 / 红证数据**
> 在 **`NOTES.md` §2.3**（同编号）。**新踩的坑请按同样格式加在这里 + 详情写进 NOTES。**

1. **`riichi` 事件里绝不能标记"牌河最后一张"为横置**（服务端**先 `riichi` 后 `discard`**）：客户端**只按 `discard.sideways` 画**。（NOTES §2.3-1）
2. **`Round` 构造时必须把 `menzen[]` 初始化为 `true`**：漏了**所有门前役**在实局失效，而单测显式传 `ctx.menzen=true` 照样通过。（NOTES §2.3-2）
3. **向听 DFS 里 `melds + partials` 必须封顶到 4**，否则 `1112223334445m` 这类会被误判成和了形。（NOTES §2.3-3）
4. **庄家的第 14 张在配牌时就发了，第一巡不能再摸**：用 `openingTile` 当"本次摸到的牌"（保住自摸/暗杠/天和），且**不重复下发 `draw`**。（NOTES §2.3-4）
5. **Qt 的 SVG 渲染器不支持 CSS 类选择器**（`<style>`+`class=` 被忽略 → 只剩黑白轮廓）：新素材先跑 `tools/inline-svg-style.ps1` 内联成表现属性。（NOTES §2.3-5）
6. **`--shot` 必须抓"活动顶层窗口"**，不能直接 `grab()` 主窗口（结算/大厅是**独立顶层窗口**）。（NOTES §2.3-6）
7. **改文件用 `edit` 工具，别用 PowerShell 做多行替换**（CRLF 与 `` `n `` 不匹配会**静默不生效**）；真要脚本替换：先确认 LF，只用单行替换/按行号拼接。（NOTES §2.3-7）
8. **服务端只有一条命令队列 `responses`**：`confirm` 必须从**`submit()` 写入的那条队列**里消费（`drainConfirm`），且**认领答复时必须确认它是动作**（有 `type`）—— 否则残留 `confirm` 被下一巡当出牌答复（症状见 §7）。（NOTES §2.3-8）
9. **「重复点击」= 同一个询问发出两条动作，客户端必须自己先锁住**：所有动作只能从 `ActionBar::actionCmd()` 组包（带 `ask_id`）、出牌/立直只能走 `MainWindow::onActionReady()`；服务端兜底：**立直不成立退回默认摸切**，绝不把宣言牌当普通打牌执行。（NOTES §2.3-9）
10. **鸣牌仲裁三条一体**：① 优先级「荣和 > 杠 = 碰 > 吃」同级看座次，同一把尺子必须在 `RoundClaims.rankOf/canBeat` 与 `claimPhase` 仲裁循环里；② 提前收工（`shouldStop`：最优鸣牌没人能压过就收工；`askedBestRank` 缺项时**保守继续等**）；③ **队列卫生**：收工后仍在 `asked` 的要 `cancelAsk` + `dropReplies`，认领处校验 `type ∈ 本次 option.type`。（NOTES §2.3-10）
11. **「哪张是刚摸到的」只能由服务端点名**（`round_start.drawn`，仅庄家），客户端不许猜；**出牌取牌只认牌码**（`Round.pickDiscardId`）；⚠ **手切要从"除摸牌位之外"的暗手里取**，客户端对账也按牌码、顺序不能反。⚠ **牌谱导出同一条**：庄家第 14 张取 `drawn`，**鸣牌串必须写真实牌码**；⛔ `rule` 里 `aka` 与 `aka51/52/53` **必须说同一件事**（都指"**每种**赤五几张"）。判据：`tools/tenhou-log-check.mjs` 第 ⑧ 条（⑨–⑬ 见 NOTES §9.6.4~§9.6.6）。（NOTES §2.3-11）
12. **振听有三种，且「自己打出过的牌」≠「牌河」**：舍张振听要算上**被鸣走的舍牌**（唯一记账点 `Round.recordDiscard`，账在 `discardKindsEver`）；同巡振听在被给 `ron` 却见逃（含超时未答）时置位、自家下次摸牌解除；立直见逃 → `furitenPerm` 到本局结束。⚠ **测试不许绕过记账点**（往 `discards[]` 里塞牌＝给 bug 背书）。（NOTES §2.3-12）
13. **掉线 ≠ 换机器人**：只标 `Seat.away`（**不置 `bot`**）：该家回合**自动摸切**、鸣牌**连 `ask` 都不发**；托管座位**仍算被占**（`occupied()` 含 `away`）、但 `readyToStart()` 不认它；四人全掉线才回收房间；房主掉线转给在场第一位真人；同 uuid 重连**接回原座位**（§6.8）。判据只看 `Seat.away`。（NOTES §2.3-13）
14. **"投票通过"发生在别的线程上，牌局线程必须能被叫醒**：`finishVoteLocked(passed)` 要 `voteEnded = true` **且** `wake()`（投一个**没有 `type`** 的哨兵进命令队列）；牌局线程在**五个检查点**看这个标志（五处清单见 NOTES §2.3-14），局间由 `sleepMs` 与 `awaitRoundConfirm` 提前醒。漏一个就是"票都通过了，牌桌还在等"。（NOTES §2.3-14）
15. **规则预设决定"能不能喂复盘器"：M.League 独有的合法动作会被天鳳规则的复盘器整份拒收** —— 《天鳳》《雀魂》要求「持点 ≥1000 **且** 剩余可摸牌 ≥4 张」才能立直，**M.League 两者都不要**；Mortal 按**天鳳规则**重放 ⇒ 判**规则违规、整份拒收**。判据：`tools/tenhou-log-check.mjs` 第 ⑬ 条（导出时 `problems` 里的码见 NOTES §2.3-15）。**要复盘就用 tenhou 预设建房**。（NOTES §2.3-15、§9.6.6）
16. **`tasklist /FO CSV /V` 在本机没有命令行列** ⇒ 靠解析它实现的规则**静默永不生效**。判据：命令行只能从 **`Win32_Process.CommandLine`** 取（每 pid 最多一次）+ 探针"已调用 / 取到过"状态；**进程还活着却取不到 ⇒ 拒跑**，⛔ 不许当成"机器很安静"。（NOTES §2.3-16、§6.5 第七十六轮）
## 1. 这是什么

一套完整可运行的四人立直麻将联网游戏：

- **服务端** `server/` —— Java 21，**零第三方依赖**（只用 JDK 标准库，没有 Maven/Gradle）。跑在 Ubuntu 上，
  是**唯一权威方**（洗牌/配牌/摸切/鸣牌/和牌/役种/符数/点数/振听/流局/连庄/精算全在它这边，见 `docs/日本麻将.md`）。
- **客户端** `client/` —— Qt 6 Widgets + C++17，MinGW 构建，跑在 Windows 上。只负责牌桌绘制、操作收集、
  事件反馈、网络收发。**不做任何规则判定**（§2.1）。
- **规则依据** `docs/日本麻将.md`（逐节标注《雀魂》《天凤》与 **M.League** 的差异）；**规则预设**
  `rules.preset` = `mleague`（默认）/ `tenhou` / `majsoul` / `custom`：预设先铺一整套、报文里的单项字段再覆盖。
  ⚠ 新增取舍项要**五处一起改**（字段 / 三套预设 / `fromJson`+`toJson` / `clampToSane` / 断言），
  且**取舍类断言必须两侧都显式传规则集**（只测默认值 = 在测"默认值恰好是什么"）。取舍清单见 `docs/DESIGN.md`。
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

> 只留**"这条字段该怎么用"的一句话判据**；**为什么必须有它、当初怎么踩的**在 **`NOTES.md` §6.4.1**。

| 字段 | 位置 | 规则 |
| --- | --- | --- |
| `tsumogiri` | `discard` | 「手切 / 摸切」**只能用这个字段**，不能靠"kind 是否等于摸到的牌"去猜（会多出一张）。 |
| `sideways` | `discard` | 牌河**只有立直宣言牌横置**；客户端**只按它画**（§2.3-1）。 |
| `called_index` | `meld` | 被鸣那张在**原牌河**的下标：鸣牌是「移动」，客户端要 `removeAt()`。 |
| `win_note` | `ask` | 能和却不能和的**原因**（`furiten` / `no_yaku`），别瞎猜。 |
| `ask_id` | `ask` | 回包原样带回。⚠ 三道闸门：**无 `ask_id` 的老客户端回包也认** → 认领时必须确认**是动作**（§2.3-8）；`type ∈ 本次 option.type`（§2.3-10）；**只许从 `ActionBar::actionCmd()` 组包**（§2.3-9）。 |
| `confirm` | `cmd` | 局间「看完了」。**没有 `ask_id`**，必须由 `Table.awaitRoundConfirm` 从**同一条队列**消费（§2.3-8）。 |
| `kind` | `ask` | `turn` / `claim` / **`chankan`**（抢杠：选项**只有** `ron`+`pass`）。⛔ 见逃（含超时未答）= 置**同巡振听**且**那次杠照常成立**；⚠ **决策漏斗的 kind 恒为 `claim`**（训练口径）。见 PROTOCOL §3.6 |
| `base_ms` / `bank_ms` | `ask` | 本巡基本 / 剩余额外时长。**扣减与取整由服务端算**；规格写「额外+每巡」（`20+5`，别写反）。 |
| `yaku[].code` / `.tile` | `agari` | 役种**只发 ASCII 码**，**没有 `name`**；认不出的码原样显示（§6.4）。 |
| `limit` / `reason` / `error.code` | 结算 / `error` | 同样只发码，`error.arg` 是可选的 ASCII 参数。报文里**只有** `name`/`text`/`msg` 允许非 ASCII。 |
| `dead_wall_left` | `draw` / `round_start` / `state` | 剩余岭上数，**每次摸牌都要带**（岭上摸牌不动 `tiles_left`，只能看它）。 |
| `drawn` | `round_start`（仅庄家） | 本巡「刚摸到的那张」的牌码。`hand` 是**已排序**下发的，推不出来（§2.3-11）。 |
| `turn` / `drawn_seat` | `state` | **两件事**：`turn` = **当前行动者**、`drawn_seat` = **最后摸牌者**；「谁有 14 张」= `turn == drawn_seat` 那一家（否则**无人**）。混起来 = 重连/观战错一家（PROTOCOL §3.8） |
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

构建脚本按「参数 → 环境变量 → PATH → 常见安装位置 → 仓库内缓存 → **自动下载**」解析依赖（**不写死路径**），
所以**不必预装 Qt**（见 §3.3）。本机实测位置清单见 `NOTES.md` §3.1。

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

**Qt 是 shared 构建、无法静态链接**（`static` 是 disabled feature）：绿色版靠「构建后把 Qt DLL + 插件
拷到 exe 同级目录」达成，`build.ps1` 已自动化。**Qt 不预装、不进仓库**：找不到就自动取到仓库根 `.qt/`
（已 gitignore）。参数与"哪些能自动取"见 `client/README.md`。

### 3.4 `build\` 与 `dist\` 的关系

**判据**：`build\` **每次**构建都更新；`dist\` **只有带 `-Deploy`** 时更新 ⇒ **发布前必须先 `-Deploy`**
（否则包里是旧 exe）。⚠ **不要比 exe 哈希**：clean 重建 + 重新链接会改 PE 头时间戳，比哈希次次报警 —— 比**可交付子集**（exe + Qt DLL + platforms/styles/tls + tiles/ + fonts/）的有无与内容。
注解见 `NOTES.md` §3.4。

### 3.5 构建避坑

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
# 期望：通过 N 项，失败 0 项 / SELFTEST PASS（当前 1507 项）
```

覆盖：规则引擎全部不变量（含 **3 次「4 机器人整场半庄」**点数守恒、§6.5 训练接口、§6.6 teacher 取舍）；
逐项清单与注解见 NOTES §4。**改了 `rules/` / `game/` 下任何东西都要重跑。**

> 新用例**优先用 `Table.debugMaxHands` 限小局数**；要"定向局面"就用现成钩子
> （`debugClaimOutcome` / `debugRonDeltas` / `debugTurnKan` / `debugDoRiichi` /
> `debugDrainWallTo` / `debugPushDiscard`），别靠发牌运气。⏱ 耗时与慢用例见 NOTES §4。

### L2 客户端自检（秒级）

```powershell
client\dist\mahjong-client.exe --selftest client\build\st
# 期望：检查项 N，失败 0 / SELFTEST PASS（当前 899 项）；并产出 tiles.png / table.png / river_overflow.png
```

覆盖：牌码↔kind 双向 / NDJSON 编解码 / `TableModel` 事件应用 / 手切·摸切 / 横置张数 / 风盘布局全部断言 /
身份与投票界面 / 掉线标记 / 并出图（逐项清单见 NOTES §4）。

> ⚠ **看 PNG 不要用 `-platform offscreen`**：那个插件在 Windows 上没字体库，中文会画成空心方框。

### L3 协议端到端（e2e 几分钟，其余几十秒）

```powershell
java -jar server\build\mahjong-server.jar --port 10086   # 另开一个终端；要快可加 --fast
node tools\e2e-test.mjs 127.0.0.1 10086          # E2E PASS（整场东风战；含报文 ASCII 审计与点数守恒）
node tools\timeout-test.mjs 127.0.0.1 10086      # 超时摸切 + 真人全掉线后房间回收
node tools\uuid-test.mjs 127.0.0.1 10086         # 身份：连接即问/同 uuid 同一玩家/坏形状按无记录/**接回原座位**
node tools\away-test.mjs 127.0.0.1 10086         # 托管：away=true·bot=false / 只摸切 / 0 次鸣牌 / 牌局不结束
node tools\vote-test.mjs 127.0.0.1 10086         # 投票：分母=在场真人 / 够票→game_end{reason:vote} / 冷却 wait_ms
node tools\discard-align-test.mjs 127.0.0.1 10086 # 出牌对齐（幽灵手牌）：drawn 必带 + 错报摸切按牌码取牌
node tools\seat-swap-test.mjs 127.0.0.1 10086    # 换座/洗座：准备落在自己座位 + 座位表恒为四家排列
node tools\claim-priority-test.mjs 127.0.0.1 10086 3000  # 鸣牌优先级 + 废包不漏到下一巡（⚠ 采样不到 → 退出码 2）
node tools\clock-test.mjs 127.0.0.1 10086        # 思考时间：基本/额外扣减 + 鸣牌也扣
node tools\firstturn-test.mjs 127.0.0.1 10086 3000  # 第一巡实测等待 = 声明 deadline
node tools\riichi-stale-test.mjs 127.0.0.1 10086 3000  # 作废的立直不得替玩家打牌
node tools\spectate-test.mjs 127.0.0.1 10086     # 观战：公开快照 + 持续公开事件 + action 被拒
node tools\utf8-test.mjs 127.0.0.1 10086         # 中文/代理对往返 + 截断不切坏字符
node tools\replay-test.mjs 127.0.0.1 10086       # 对局记录：写入/分页/守恒/路径穿越/限速（--no-game 只验读取）
node tools\bot-ai-test.mjs 127.0.0.1 10086       # 机器人 AI：清单/建房 bot_ai/换一代/未知名字与路径串被拒/非房主被拒
```

**静态检查（不用起服务端，改文案/码表必跑）**：

```powershell
node tools\check-i18n.mjs        # 服务端每个码都有客户端译文（词表哨兵）
node tools\i18n-scan.mjs --check # 源码里不许剩中文字面量
node tools\i18n-gen.mjs --check  # 映射表 ↔ 语言文件一致（不漏 key）
python\selfcheck.py              # 训练侧自检（当前 **867** 项；必须在 python\ 下跑）
node tools\selfplay-check.mjs <轨迹目录>   # 训练数据集校验（独立实现，见 §6.5）；v4 前向/缓存 = trainer-v4-parity.mjs / v4-cache-check.mjs
node tools\trainer-parity-check.mjs 24    # 训练端 C++ 引擎 ↔ Java：牌山/配牌**逐整数**对拍（见 docs/TRAINER-CPP.md）
node tools\trainer-rule-parity.mjs        # 同上：向听/进张/听牌形
node tools\trainer-score-parity.mjs       # 同上：役种/符数/点数/授受
node tools\trainer-settle-parity.mjs      # 同上：精算/连庄/种子链
node tools\trainer-action-parity.mjs      # 同上：动作键/下标/回包/落位；trainer-opts-parity.mjs = 询问内容
node tools\tenhou-log-check.mjs <导出.json> # 牌谱导出校验（按 docs/input-json.md 再解一遍，见 NOTES §9.6）
# 改过导出格式再拿**上游真解析器**验一遍（探针 / `mjai-reviewer --no-review`）：
#   tools\upstream-parse-check\README.md（判据与负向对照见 NOTES §9.6.2）
node tools\doc-refs-check.mjs   # 文档自检：AGENTS 预算 + 章节号完整 + 全仓 §引用可解 + `docs/INDEX.md` 完整性（见 §8）
node tools\handover-facts.mjs --check   # `docs/HANDOVER.md` §1 的 git 事实（基线 sha 是 HEAD 祖先 / 远端 sha 是头或其祖先 / ⛔ 未推送>0 不许写"已同步"）
node tools\round-index.mjs --check      # `NOTES.md` §6.5 的轮次索引表覆盖全部轮次小节（⛔ 不重排正文）
python tools\overlap-orchestrator.py selftest  # 编排器自检 8/8（含 5 条负向；⛔ 不采集/不训练；见 tools/AUTOMATION.md §8）
```

> ⏱ `e2e-test` 耗时与加速开关见 NOTES §4。

### L4 GUI 定点复现（发现 UI bug 时首选）

**不要靠运气等牌**——用假服务端把客户端推进到目标状态再截图（`--shot` 用 Qt 的 `grab()`，不受屏幕裁剪影响）：

```powershell
node tools\mock-server.mjs 10999 turn|claim|note|river|agari|yakuman|kan|twoturn|sticks|hand2meld|allmeld|sfx|sfxburst|vote
client\dist\mahjong-client.exe --demo 127.0.0.1 10999 --bots 3 --no-answer --shot client\build\shot.png --after 6
```

每个模式推到的局面见 `tools/mock-server.mjs` 头部注释；验收判据与假服务端自带的**身份握手**见
**NOTES §4 L4**。⚠ 无交互桌面时会卡住（不出图）。

### L5 真机联调（最贵，改动涉网络/流程时跑）

```powershell
client\dist\mahjong-client.exe --autoplay 127.0.0.1 10086 --name 联调 --timeout 200
# 结果写在 client\dist\autoplay.log，期望 AUTOPLAY PASS
```

`--autoplay` 会记录**每次 ask 的选项类型**，排查"某个按钮没出现"非常有用。
**客户端全部命令行模式**（`--selftest` / `--lobbytest` / `--autoplay` / `--demo` / `--replay` /
`--gentiles` / `--fontprobe`）的说明见 **NOTES §4 末表**。
## 5. 目录结构（板块地图）

**完整目录树、文档清单与"哪个文件属于哪个板块"见 `docs/INDEX.md`**。一句话版：

| 板块 | 主要落点 |
| --- | --- |
| ① 规则与引擎（唯一权威方） | `server/src/main/java/mahjong/{core,rules,game}/` · `docs/{日本麻将,DESIGN,AUDIT}.md` |
| ② 协议与两端契约 | `docs/PROTOCOL.md` ★（改协议先改它）· `docs/input-json.md` |
| ③ 客户端（Qt6 Widgets） | `client/src/{ui,model,net}/` · `client/README.md` · `docs/THEME.md` |
| ④ 训练（v3 现状 → v4 目标） | `python/mahjong_ml/`（v4 在 `v4/`） · `trainer/` · `docs/{TRAINING,FEATURES-V4,TRAINING-V4,TRAINER-CPP,BOT-AI}.md` |
| ⑤ 运维与发布 | `docs/DEPLOY.md` · `docs/THIRD-PARTY.md` · `tools/package-release.ps1` + `make-zip.mjs` |
| ⑥ 验证与文档纪律 | `tools/*-test.mjs` · `tools/*-parity*.mjs` · `python/selfcheck.py` · `AGENTS.md` · `NOTES.md` · `docs/INDEX.md` |

运行时数据（**都不进仓库**，见 `.gitignore`）：`replays/` 对局记录 · `players/` 玩家档案 · `bot-ai/` 机器人 AI 包。

---

## 6. 代码约定

### 6.1 通用流程与产物

- **注释用中文**，解释「为什么」而不是「做了什么」。
- **协议改动流程**：先改 `docs/PROTOCOL.md` → 再改服务端 → 再改客户端 → 补两侧测试。
- **新增规则判定**：写进 `rules/`，在 `server/src/main/java/mahjong/test/SelfTest.java` 加断言。
  ⚠ **两侧断言参数顺序不同，别写混**：服务端是 `check(名称, 条件)` / `eq(名称, 实际, 期望)`；
  客户端是 `check(条件, 名称)` / `checkEq(实际, 期望, 名称)`。样例见 NOTES §6.1。
- **牌面素材走 SVG，可替换**：`client/assets/tiles/<牌码>.svg`（38 个 = 37 种牌 + `back.svg`），构建时拷到
  exe 同级 `tiles/`；加载顺序 **目录 → qrc → 程序化绘制**（删掉整个 `tiles/` 也不白屏）。替换只需保持
  **文件名 = 牌码**与 `viewBox="0 0 300 400"`，**重启客户端即生效、无需重编译**（占位符见 NOTES §9.1）。
- **结算界面走内嵌字体 + OpenType**：`I.MahjongJP.otf` 的 `liga` 连字默认生效、**无需任何 OpenType API**；
  字体取不到或串含非法字元时**整块不显示**、回退文字（详见 NOTES §9）。
- **UI 扁平化**：纯色 + 1px 描边，**牌片单层外框、不画厚度层**，不用渐变/阴影/发光。
- **文档分工**：`README.md` 面向玩家（操作、规则取舍、常见问题），**不含技术细节**；技术内容一律进
  AGENTS 与 `docs/`。**改了玩家看得见的行为就要同步 README。**
- **第三方许可**：客户端以 **LGPLv3 动态链接**使用 Qt，`client/licenses/` 随 `dist/` 一起分发。
  **不要删它，也不要给 Qt 的 DLL 加校验/签名锁定**（那会阻止用户替换 Qt，直接违反 LGPLv3）。
  新增 Qt 模块或第三方库时同步 `docs/THIRD-PARTY.md`、`licenses/NOTICE.txt` 与 `build.ps1` 的必需 DLL 清单。

### 6.2 牌桌布局与绘制（客户端 —— 最容易被改坏的一块）⤴ 正文已移至 `AGENTS-CLIENT.md`
### 6.3 牌桌行为、规则与资源 ⤴ 正文已移至 `AGENTS-SERVER.md`
### 6.4 协议、文案与结算 ⤴ 正文已移至 `AGENTS-PROTO.md`
### 6.5 训练接口（机器学习 / 自对弈）

**权威描述在 `docs/PROTOCOL.md` §8**（字段表、动作键文法、CLI、数据格式）；**完整实现说明见
`NOTES.md` §6.5**。这里只留"改代码时必须守"的几条：

> ⛔ **本节只留判据**：**实测数字 / 红证 / 原始输出 / 事故经过**已逐字搬进 **`NOTES.md` §6.5 附录 C/E**。

- ⛔ **主线裁决（2026-10-15 项目方批准）**：命题 = 无人类数据、只靠训练策略分配；**特权/完美信息只进 critic（MV-2）**、⛔ 不走强特权→policy 蒸馏；PSRO/QD 在四人非零和下排除；顺序 MV-1→MV-2→MV-3。见 `NOTES.md` §6.5。
- **只有一个决策漏斗**：`Table.decideBot(seat, Decision)`。自家摸打（`Round.ask`）与鸣牌段都汇到它；
  `policy[seat] == null` 时走内置 `Bot`。**不要再往第三个地方直接调 `Bot.decide`**。
- ⚠ **鸣牌段不走 `Round.ask()`** → `debugAskTap` **看不到鸣牌**；任何"每次询问都要做的事"都挂在漏斗上
  （用 `debugChoiceTap` 才看得到两段）。
- **策略不许把异常抛给牌桌线程**：`decideBot` 必须兜底（异常 / 返回 `null` → 内置机器人），
  否则一局异常会让**整场半庄静默死亡**。
- **训练侧只实现 `ActionPolicy`（拿不到 `Round`）**：`Round` 里有别家手牌、牌山顺序、里宝 ——
  进程内一不留神就训出**作弊**模型。
- **观测只许含合法信息**：新增字段必须三处一起改 —— ① 确认公开可见；② `Observation` 字段白名单（`SelfTest.trainingInterfaceTests`）+ `tools/selfplay-check.mjs` 的 `OBS_KEYS`；③ PROTOCOL §8.2 的表。⚠ 自家回合**别调 `Round.isFuriten()`**。
- **可复现是硬要求**：`debugDeterministicSeed=true` + **每局一份策略实例**（`PolicyFactory`）+
  每场种子预先算好（`SelfPlay.seedFor`）；策略跨局带状态会破坏同 seed 可复现。
- **奖励是事后回填的**：`hand_delta` / `placement` 在小局、整场结束时才补；`placement` 必须是 `1..4` 的排列。
- **不给服务端加 ML 依赖**（Maven/ONNX/PyTorch）：外面训练、导出权重、**纯 Java 手写前向**；**改产出格式就三处一起改**：`TraceRecorder` / PROTOCOL §8.4 / `tools/selfplay-check.mjs`。
- **P2 DAgger 轮**：`--teacher-label`（学生座位另记 `teacher`/`teacher_index`）+ `python -m mahjong_ml.dagger`。
  ⚠ 三条不许省：① 对比只用"两个模型都没训过"的场次；② 显著性**按场聚类**；③ 带同数据量的 BC-only 对照臂。
- **P5b 混合（teacher 先验）**：`net:<权重文件>@<α>` = `argmax(student + α·1[该候选 == 老师动作])`。
  ① 先验**只加在 logit 上**；② 必须是 **`Policy` 级组合**；③ α 随**训练过的网**的 logit 尺度走。
- **P4 探索口**：策略串 `net:<权重文件>[@<α>][#<T>]` = 按温度从 `softmax(logits/T)` 采样（`T≤0`/省略 = argmax，与加它之前**逐决策相同**）。⚠ 随机源必须**每局按 `(seat, gameSeed)` 派生**（`Policies.mixSeed`）。在线 PPO 另三条硬口径：**λ=1**、**优势只在 `is_student==1` 的行上归一化**、**两边同一个 `softmax(logits/T)`**；⚠ **跨代对局**用 `dataset build --student <这一轮学生的确切策略串>`。
- **特征 v3**：state **615** = 544 + 71（`VERSION=3`）；三条硬判据：① 四家块**旋转到自己为下标 0**；
  ② 状态段只放**便宜且别处没有**的量；③ sidecar 逐决策段 **int16**，老 607 维**构造期拒绝**。
- ⛔ **"一轮"由时间预算定**（缺省 `--target-minutes 20`）且**只跑缓存档**（`--cache-gb`=0.8×内存）：场次由
  `budget.py` 反推 + 闸门夹逼（**点名谁限制的**）。
- ⛔ **风格线用"风格桌"**：四席 = 学生 1 + **每个风格各 1**（座位要 `--rotate-perm` 的 **24 排列**平均，循环移位不够）；⛔ 别用 `-PoolSeed`。
- **v4 前向的增量事件缓存**（默认开，`--no-v4-cache` 关）：判据必须**成对** —— **增量 == 全量（逐位）**
  *且* **真的命中**（只有"相等"是假绿）。回归：`SelfTest.v4CacheTests` + `tools\v4-cache-check.mjs`。
- ⛔ **v4 事件 GRU 增量 carry 的五条永久判据**（C++ 采集端默认开；红证见 NOTES §6.5 附录 E）：① 逐位判据 = **≥20 万真实决策行上七个头 + `h_evt` 逐位 0 不符** 且**命中率 >0**；另两条逐位 = **缓存 carry == 整手重放**、**前缀 carry + 窗口 == 整手重放**；② ⛔ **"整场轨迹逐字节相同"绝不能替代逐位判据**；③ **"0=关 / <0=不限"式标志位必须写 `<= 0`，预算用尽要置 0（不是 −1）**；④ **跨头文件引用的类型必须定义在具名 namespace**；⑤ **比较脚本先断文件存在**（都缺 ⇒ 判成"相同"= 假绿）。回归：`--selftest` + `v4-cache-check.mjs` + `trainer-v4-parity.mjs`。
- **头是否"上线"= 前向是否读取**（⛔ 非"训练过"）：判据 = 去掉该头后动作**逐位不变**；见 `docs/TRAINER-CPP.md` §6.25。
- ⛔ **critic 只剩"基线尺度"这一件事可做**（三条探针：`--ev-ref legit` / `--readout` / `--shaping`）：① 判据**别用绝对门槛、也别拿标签侧真值当参照**（量出的是**作弊上界**）；闸门 = `EV ≥ 0.7 × 合法天花板` + 覆盖率 ≤3pp；② **基线必须与奖励同尺度**（`--baseline-fit scale`）；③ **加宽/加深读出头没用**；④ **PBRS shaping 也没用**（⇒ **`A'=A`**）—— ③④ 用**绝对量纲的残差比**判，别用 EV。见 NOTES §6.5 附录 E。
- ⛔ **贴标签的读数不是强弱判据**（`engine_top1`/引擎 CE/危险度旧 AUC）：强弱只能由闸门（多套新牌山配对、CI 排除 0 且为正）答。
- **PPO 数值口径**：`--grad-clip 0.5`（日志 `|g|` 是**裁剪前**范数）、`--kl-early-stop 0.03`（纯函数 `kl_stop_hit`，越过停**整轮**）、**双口径 KL**、`ppo` 与 `--rwr-beta>0` **硬拒**、优势**只归一化一次**；训练口径开关（**`--max-steps`**、`--value-target delta`、`--advantage`、`--rank-weight`）**必须走 `v4 loop`**，不许只活在 shell 历史里。
- **数据生产者可切**：`MAHJONG_PRODUCER=java|cpp`（缺省 java）—— 采集与 `--features` 都走 `python/mahjong_ml/producer.py`；判据 = **同种子产物逐字节相同**（`tools\trainer-{selfplay,aux}-parity.mjs`）。C++ 侧未实现的（`--teacher-label`、`net:` 的 `@α` 先验）**显式报错**；完整半庄验收**≥200 场**。
- **批前向（`selfplay --batch-forward K`，缺省关）**：**键 = 场号 · 询问序 · 座位**；批 = **屏障时刻未决键里字典序最小的 min(K,|未决|) 个** —— ⛔ **按到达顺序凑批永远红**；**同 seed + 同 K ⇒ 同批计划指纹**。
- ⛔ **"同种子 ⇒ 逐字节相同"在批前向上已退役**：批模式判据 = **argmax 逐行全同 + 统计可复现**；开关**关着**时旧路径必须**逐位不变**。
- ⛔ **链式续训若 `--init` ≠ 数据生成策略 ⇒ `pretrain.py` 的 PPO 口径闸门 `raise SystemExit`（exit 1、无 traceback）** ⇒ 要跑多段必须**先有优化器状态续跑（或单进程分片循环）能力**。
- ⛔ 多段训练须 `--save-optimizer`+`--resume-optimizer`（缺省全关）：**续跑逐位 == 一口气跑** 且 **负向：清零动量必不同**（"相等"是假绿）；⚠ **合法切点 = 同 epoch 内 ∧ `opt.step()` 边界 ∧ `stop-after-steps` 正常收尾 ∧ 全部历史敏感状态逐位同一份（含 `--ref-beta>0` 时的 KL 锚 π_ref）**；见 NOTES §6.5 附录 E / `TRAINING-V4 §17.9`。
- ⛔ **链式续训的合规段数没有"2 段"上限**：硬拒判据锚在**策略身份**（`behaviour.sha256`+温度+策略串）与**链条起点 `kl0 ≤1e-2`**，段边界漂移 `kl_segment` 只记账+告警；⛔ **单段信任域不是常数、随 `--kl-early-stop` 变**（同数据同网的实测曲线见 NOTES §6.5 第八十二轮）⇒ 要更长**先量曲线**、再换**新鲜采集**（BL-13/BL-14）或换配方，⛔ 别拿一个常数当"必须换新鲜采集"的理由，⛔ 不是加步数；⚠ 段末撞 KL 早停的存档**不许续**。见 NOTES §6.5 附录 E。
- ⛔ **`pair` 重叠的 collect 只能拿到 θ_i**（collect 命令行在 train 起跑前就定死）⇒ **"每轮用当时 θ 现采"与"重叠"不可兼得**；判据 = 采集 `g0.jsonl` 的 `policy=` 必须等于**该轮**的 θ，且预注册里明写本轮用的是 θ_i。
- ⛔ **等算力下「数据新鲜度」不是瓶颈**：k=2 轮新鲜采集 vs 一次冻结长训 ⇒ 该分辨率下测不出（实测数字与所需场次见 NOTES §6.5 第八十三轮）。
- ⛔ **包装器绕过该闸门不得用于产出结论**（仅限探索 + 标注"口径已动"）。
- ⛔ **异步/全重叠（数据生成策略 ≠ 训练 `--init`）**：数据侧必须逐行记 `behaviour_id`（+ `meta.behaviour.table[{spec,temp,sha256}]`，sha256 **构建器自己算**；⛔ 该列**不许进模型**，判据 = 加/不加它前向**逐位相同**）；多代 ⇒ `--behaviour-multi`（**逐 id** 重算 `logp_old` + 逐 id 复算 `KL(记录‖复算)≈0`），陈旧度**降级为预算/记账**，但 **`--init` 与全部 id 脱钩仍 `SystemExit`**；ρ 必须**有界**（`--rho-max`，超限响亮失败或显式降级 + 记账，⛔ 不许静默裁剪；**双边**判据）。见 NOTES §6.5 第八十一轮。
- **判"有没有可用信号"的判据**：样本外 **AUC/增益必须同时胜过 0.5 与最强安慰剂**（打乱标签 / 非学生席 / 只用动作键），且幻想增益要**显著高于同量纲神谕的 0.2**。
- ⛔ **"状态条件下的方向选择"（押し引き / fold）已由四条独立证据判为不可学 ⇒ 不再投算力**。
- ⛔ **护栏容忍带必须与计划 N 的实际 MDE 对账**：判据 = **跑前先算该 N 下每条主/护栏指标的 MDE，并声明"容忍带/MDE"比值；比值 <1 的护栏不得作为否决依据**（红证见附录 E）。
- ⛔ **跨族不自洽 ⇒ 判决只对本族成立、不跨族外推**：判据 = **判决必须写明"条件于哪一族牌山"；比较两网时"族间散布"要事先写进判据**（红证/经验见附录 E）。
- ⛔ **便宜探针的判决必须用「同任务配对增益」，⛔ 不能拿不同任务的安慰剂 AUC 直接比**（"非目标席"安慰剂换的是标签 ⇒ 正比不同、AUC 不可跨任务比较）：判据 = `Δ_cand > max(0, 打乱标签增益, 只用动作键增益)` **且** `Δ ≥ 实测 MDE_paired`（2.802×SE_boot）**且** `Δ ≥ 预注册 MDE`，并**照报三个安慰剂的原始 AUC**；⚠ 探针必须预留一个**预期出局**的阴性对照（第八十四轮 C4 = ctx 线性重编码，按预期出局）以证明判据没放水。见 NOTES §6.5 第八十四轮。
- ⛔ **取权重/证据副本必须按"解析后真实路径 + sha256"定位，⛔ 不许按目录名**：`tools/build/_rejected/<name>/` 与 `<name>/` 同名 18 组 ⇒ 按名索引会被覆盖取错 ⇒ 优先非 `_rejected` 且断言 `_rejected` 计数 = 0；**`bot-ai/<pkg>/net.bin` 与 `tools/build/` 同名目录可能是两份不同模型**（实测见 NOTES §6.5 第八十五轮）⇒ **已发布权重以包内为权威**、逐包 sha256 反查，⛔ 反查不中不许按名顶替。见 NOTES §6.5 第八十五轮。

17. ⛔ **头"上线"= 前向实际读取 且带非退化 `policy_gate`**：`heads.policy_gate.weight` 全 0（或缺张量）⇒ 该 ckpt 的 `belief_tenpai` 不在推理路径（`gate ≡ 1+tanh(bias)`，与 `bt` 无关 ⇒ 退化为 `{policy}`）。逐 ckpt 量：`node tools/policy-gate-check.mjs`（⛔ 表级常量不算）。（NOTES §6.5 第八十六轮）
18. ⛔ **自更新脚本不许用 `releases/latest` + 按 tag 拼资产名**：只认非 draft（prerelease 缺省跳过）、**按资产名模式**匹配服务端包、**语义版本**排序取最大；⛔ 解析 JSON 别用 sed/grep 扫全文（release body 里有同名文字）；⛔ **别用 `cp` 覆盖正在运行的脚本**（bash 读到错位 ⇒ 半装静默退出 0）。（NOTES §9.5）
19. ⛔ **清理临时/共享目录必须逐项白名单，⛔ 不许 `-Recurse -Force` 整个共享目录**：删前先列清单 + 逐项 `-LiteralPath` + 复核解析后的绝对路径；⚠ **被 gitignore 的目录也是别人的财产**，删错不可恢复。（NOTES §6.5 第八十九轮）

**第七十六轮新增的判据 ①–⑦**（红证 / 实测 / 原始输出逐字见 `NOTES.md` §6.5 附录 E；批次工具见
`tools/overlap-orchestrator.py` 与 `tools/AUTOMATION.md` §8）：

- ① ⛔ **任何环境探针（进程表 / 传感器 / 计数器 / 文件比较）必须 fail-closed**：**取不到数据 ⇒ 报 FAIL / 拒跑**，⛔ **绝不当"干净"或"相同"**（取法见附录 E）。
- ② ⛔ **静窗 / 竞争闸门必须带"枚举自检"三条**（自己的 pid · 系统进程 ≥3 · `netstat` LISTENING 属主 pid 全在枚举里）——**缺一 ⇒ 拒跑**。
- ③ ⛔ **内存峰值口径 = 已提交私有字节（committed）**，**常驻工作集（ws）另报**；⚠ 口径必须先拿已知负载标定（否则把 mmap 文件页当真实内存）。
- ④ ⛔ **大输入必须分块物化**（跑完即释放）；**`--limit` 必须真夹住读取行数**；峰值**随 `--chunk` 线性、与总行数无关**；**`--chunk ≤ 0` 必须报错**。
- ⑤ ⛔ **"零训练 / 零负载"探测的判据 = 解释器镜像 ∧ 特征两个条件同时成立**（单看命令行会**误报**）。
- ⑥ ⛔ **零和对开口径下 ρ(Δ, 基准) 恒为 −1**（设计恒等式）⇒ **先判"可识别性"**，真 ρ 只能从**学生席口径**读；⚠ **新臂先过 `tools/ceiling-gate.py`（≤30 min、零训练、fail-closed）否则不许开训**。
- ⑦ ⛔ **退出码 / 计时口径**：某一相非 0 退出**不得**被"读数被污染"的退出码吃掉（**exit 7**，优先级 **5>7>6>0**）；**`own_wall` / 重叠区间必须按采样器看到的"真退出时刻"算**。

### 6.6 teacher（内置机器人）的五层取舍 ⤴ 正文已移至 `AGENTS-TRAIN.md`
### 6.7 无用代码清理（可重复的判据）

**`node tools/deadcode-scan.mjs [java|cpp]`** 扫出"只在声明/定义处出现"的函数（用法与全仓清理的
实测数字见 `NOTES.md` §6.7）。⚠ 它给的是**候选**，不是判决书 —— 反射、宏、Qt 元对象（信号槽）、
虚函数/接口实现、以及"故意留给外部使用者"的公开 API 都会漏判。删之前按这个顺序过一遍：

1. 读一眼那段代码，确认没有副作用（初始化、注册、JNI/反射入口）；
2. `override` / `virtual` / `signals:` / `slots:` / `Q_INVOKABLE` 段里的一律**别删**；
3. **文档里写成契约的不删**：例 `Visible.unseen` 只被 PROTOCOL §8 的观测字段表引用（代码里没人调），
   那也算"在用"，扫描器照样会报它 —— 这类要**留着并在代码注释里写清为什么**；
4. 删完**编译 + 跑对应层自检**（服务端 `--selftest`、客户端 `--selftest`）—— 这一步才是判据。

顺带：`server/build/unused-imports.mjs` 一类临时脚本不必进仓库；可复用的（扫描器）放 `tools/`。

### 6.8 身份（uuid）与玩家档案 ⤴ 正文已移至 `AGENTS-PROTO.md`
### 6.9 掉线托管 与 结束对局投票 ⤴ 正文已移至 `AGENTS-PROTO.md`
## 7. 常见症状 → 先查哪里 ⤴ 正文已移至 `AGENTS-SYMPTOM.md`
## 8. 文档维护约定（AGENTS.md / NOTES.md 的分工）

**本文件"每次会话自动加载"，而指令预算只有 64 KB —— 超了会被静默截断（不报错、也没人发现）。** 所以：

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
⚠ **`NOTES.md` 独有**的章节一律取编号 **≥10**（AGENTS 里同号常是别的主题 —— 例"已知限制"与
AGENTS §8 撞号，现已改号为 **NOTES §10**）；
③ **改完跑 `node tools\doc-refs-check.mjs`**：它会查 AGENTS 的**字节预算**、章节号**完整性**、
   全仓 `§引用`是否**都解得出目标**，以及 **`docs/INDEX.md` 的完整性**
   （索引列出的路径都存在 + `docs/` 下每份 `.md` 都被登记）。

其它：`README.md` 面向玩家（改了玩家看得见的行为就同步）；`docs/PROTOCOL.md` 两端唯一契约
（**改协议先改它**）；`docs/DESIGN.md` 架构与取舍；`docs/AUDIT.md` 审计条目（`S-nn`）；`docs/DEPLOY.md` 部署。
⚠ **历次压缩搬走的片段逐字留档在 `lab/housekeeping/AGENTS-COMPRESS-APPENDIX.md`**。

### 8.1 切片与路由协议（域切片怎么用）

规则**三条**：① **判据不复制** —— 域切片只放"我要…⇒读哪一节"+ 域内判据指针，
判据本体留在本文件（§2 / §6.5）或 `NOTES.md`；② **切片永不自动加载** —— 文件名走 `AGENTS-<域>.md`，
不在 harness 候选表（`AGENTS.md` / `CLAUDE.md` / `*.local.md`）内、也不匹配任何 glob ⇒ 需要时**显式读**；
③ **加了新域** ⇒ 只做两件事：本文件路由表加一行 + 建一个切片文件（⛔ 切片之间不互相复制判据）。

**焦点块**（本文件最顶部 `<FOCUS:BEGIN>`）是**唯一**的"现在该读哪一域"开关，⛔ 不写轮次号 / 日期 / 权重路径
（易变物进 `AGENTS-TRAIN.md` 的「现行上下文」段）。**改指判据**见焦点块内三条；**改完必跑** `doc-refs-check`。
⚠ 焦点块**不是**"下一步"：⛔ 不写 `| **下一步（唯一清单）** |` 这类措辞（那是 `docs/HANDOVER.md` 第 1 节的保留字）。

**留档纪律**：任何"搬走 / 改写 / 删除"的行都要逐字落 `lab/housekeeping/AGENTS-SLICING-APPENDIX.md`
（同 `AGENTS-COMPRESS-APPENDIX.md` 的做法），并跑**三组机械核对**（判据句 CJK bigram / 代码 token / 数字常量，
"未找到"必须为 0）+ **逐字段 sha256 全等** + **字节守恒对账**。

## 9. 素材与字体管线（牌面 SVG + 结算字体 + 音效 + 打包）

**要改素材/字体/音效或发布打包时才读这一节**（细节见 `NOTES.md` §9）。速记：**牌面 SVG、结算内嵌字体
与音效的判据都在 §6.1/§6.2**，别在这里重写一遍。

- **发布打包**：`tools/package-release.ps1`（服务端用 `tools/make-zip.mjs` 手写 zip ——
  ⚠ **必须保留 Unix 可执行位**，`.NET Compress-Archive` 写出来的 zip 在 Linux 上 `./start.sh` 会
  `Permission denied`）。服务端包结构、两个资产的命名与 release 流程见 **NOTES §9.5**。
