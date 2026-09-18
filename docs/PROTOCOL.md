# 立直麻将 网络协议契约 v1

> 本文件是**服务端（Java）与客户端（Qt/C++）之间唯一的接口契约**。
> 两端都必须严格按本文件实现；任何改动都要先改本文件，再改两端代码。

## 0. 传输层

- TCP，默认端口 `10086`（可用 `--port` 覆盖）。
- 报文为 **UTF-8 编码的 NDJSON**：一行一个 JSON 对象，以 `\n` 结尾。
  JSON 序列化时**不得输出裸换行**（`\n` 必须转义为 `\\n`）。收到空行忽略。
- 单条报文上限 1 MiB。
- 客户端可选发送 `{"cmd":"ping"}`，服务端回 `{"ev":"pong"}`。服务端每 20 秒在 TCP 层没有写数据时发送 `{"ev":"pong"}` 作为心跳；客户端 60 秒无任何下行报文可判定掉线。
- 所有 `cmd` / `ev` 字段值都是小写 snake_case。

**两端都显式钉死 UTF-8，不依赖平台默认字符集**：
服务端 `OutputStreamWriter/InputStreamReader(socket…, StandardCharsets.UTF_8)`；
客户端 `QJsonDocument::toJson()`（UTF-8 字节）直写 socket、`fromJson()` 按 UTF-8 解。

> ⚠ **报文里不带中文**：所有**词汇性文本**都只发 ASCII 码，中文文案在客户端语言文件里。

| 字段 | 内容 | 例 |
| --- | --- | --- |
| `yaku[].code` | 役种**码**（ASCII 词汇码） | `"riichi"`、`"kokushi"` |
| `yaku[].tile` | **参数化役种**的牌码（只有役牌/场风/自风有） | `"5z"`（役牌 白） |
| `limit` | 打点档位**码** | `"mangan"`、`"kazoe_yakuman"`、`"yakuman"` |
| `round_end.reason` / `ryuukyoku.reason` | 流局原因**码** | `"exhaustive"`、`"four_kans"` |
| `error.code` / `error.arg` | 错误**码** / 可选 ASCII 参数 | `"unknown_cmd"` + `"frobnicate"` |
| `seats[].name` / `room.name` | 玩家名 / 房间名（客户端提交后原样回显） | 任意文本（**可含中文**） |
| `chat.text` | 聊天 | 任意文本（**可含中文**） |

**只有最后一类（用户数据）可以是任意文本**，`name` / `text` 两个字段名是白名单；
其余任何字段出现非 ASCII 都算协议违规（`node tools\check-i18n.mjs` 静态核对码表，
`node tools\e2e-test.mjs` 在真 socket 上逐条审计报文）。

**客户端不得拿这些码的中文译名做逻辑判断**（那等于把服务端的文案当协议常量）。判据一律用 ASCII：
事件名、`type`、`win_note`（`furiten`/`no_yaku`）、牌码（`1m`）、`yakuman`（数字）…

**文案只在客户端**：`client/assets/i18n/<locale>.json`（键 = `<族>.<码>`，如 `yaku.riichi`）。
加载顺序与 `tiles/`、`fonts/` 同一套约定 —— **exe 同级 `i18n/` 优先，qrc 兜底**，
所以**改文案不用重新编译**（删掉整个 `i18n/` 也只会退回显示码，不会白屏）。
语言由 `--lang <code>` 或环境变量 `DSH_MAHJONG_LANG` 指定，缺省 `zh_CN`。
认不出的码由客户端**原样显示码本身**（而不是空串或裸键），一眼能看出是"服务端加了码、语言文件没跟上"。
新旧混跑兼容：码为空串时客户端回退用老字段（`yaku[].name` / 原样 `limit` / `error.msg`）。
**界面固定文案也在这张表里**（`ui.*` 族：按钮、标题、标签、结算 HTML），客户端源码里不留中文字面量，
所以改任何一句文案都只需编辑 json、重启客户端 —— 不必重新编译。校验：
`node tools\check-i18n.mjs`（协议词表 ↔ 语言文件）、`node tools\i18n-scan.mjs --check`（源码里不许剩中文）、
`node tools\i18n-gen.mjs --check`（搬运映射 ↔ 语言文件）。

**长度上限按「码点」算，不是 UTF-16 码元**：玩家名 24、聊天 200，
服务端用 `Json.clampCodePoints()` 截断 —— 直接 `substring` 会把代理对
（`𠮷` U+20BB7、`🀄` U+1F004 这类 BMP 之外的字符）切成半个，写出时**静默变成 `?`**。
回归：`node tools\utf8-test.mjs`（走真 socket 验中文/代理对往返与截断边界）。

**词表核对**：`node tools\check-i18n.mjs` —— 以服务端为权威，逐个码核对客户端语言文件
有没有对应文案；缺一条就失败（这是"服务端加了役种、客户端忘了翻译"的唯一静态哨兵）。
`node tools\e2e-test.mjs` 另外在真 socket 上**逐条审计报文**：除 `name` / `text` / `msg`
三个用户数据字段外，任何字符串出现非 ASCII 都直接判失败。

## 1. 牌的表示（唯一合法写法）

| 类别 | 字符串 | 说明 |
| --- | --- | --- |
| 万子 | `"1m"` … `"9m"` | 1万~9万 |
| 筒子 | `"1p"` … `"9p"` | 1筒~9筒 |
| 索子 | `"1s"` … `"9s"` | 1索~9索 |
| 风牌 | `"1z"`=`东` `"2z"`=`南` `"3z"`=`西` `"4z"`=`北` | |
| 三元牌 | `"5z"`=`白` `"6z"`=`发` `"7z"`=`中` | |
| 赤宝牌 | `"0m"` `"0p"` `"0s"` | 赤5万/赤5筒/赤5索 |

- 手牌、牌河、副露中的牌一律用上述字符串。
- 服务端内部牌 id 为 `0..135`，`kind = id / 4`，`kind 0..8=1m..9m`、`9..17=1p..9p`、`18..26=1s..9s`、`27..33=1z..7z`。
  赤5 = `kind ∈ {4,13,22}` 且 `id % 4 == 0`。
- 字符串解析规则（两端一致）：`0m`→kind 4 且赤；`5m`→kind 4 非赤；`0p`→kind 13 赤；`0s`→kind 22 赤。
  内部向字符串序列化时：赤5 输出 `0?`，普通5 输出 `5?`。

## 2. 客户端 → 服务端（`cmd`）

### 2.1 握手 / 大厅

```jsonc
{"cmd":"hello","name":"玩家名","ver":1}          // 连接后第一条，必须
{"cmd":"list_rooms"}                              // 请求房间列表
{"cmd":"create_room","name":"房间名","rules":{...},"fill_bots":0}  // 建房间，自己自动入座
{"cmd":"join_room","room":"AB12"}                 // 加入房间（作为玩家；满员则成为观战者）
{"cmd":"leave_room"}
{"cmd":"ready","ready":true}                      // 准备/取消准备
{"cmd":"take_seat","seat":2}                      // **开局前自选座位**（门风 = 座次，见下）
{"cmd":"shuffle_seats"}                           // 房主：随机洗座（随机门风），洗完所有人重新准备
{"cmd":"add_bot"}                                 // 房主给空位补一个机器人
{"cmd":"remove_bot","seat":2}                     // 房主移除机器人
{"cmd":"start_game"}                              // 房主（4 人齐且全 ready 时）开始
{"cmd":"chat","text":"..."}                       // 房间内聊天
{"cmd":"replay_list","offset":0,"limit":30}       // 对局记录列表（只读，见 §3.11）
{"cmd":"replay_get","id":"7FQ3M2XK9A","from":0,"count":600}  // 取一场记录（分块）
{"cmd":"ping"}
```

`rules` 见 §5。

**座位与门风（`take_seat` / `shuffle_seats`）**

门风**就是**座次（`seat` 0=東/起家、1=南、2=西、3=北），所以"选座位"与"选门风"是同一件事。
两个命令都只在**开局前**有效：

- `take_seat`：把自己与目标座位的住户**互换**（目标是真人就两家对调，是机器人就机器人搬过去）
  —— 语义是互换而**不是抢占**，任何情况下都不会把别人挤出去。成功后服务端回
  `room_joined`（新的座位号）并广播 `room`。座位号非法或牌局已开始：`error.code = bad_seat`
  （前者）/ 静默忽略（后者，同时记日志）。
- `shuffle_seats`：**房主专用**（非房主回 `not_host`）。用 CSPRNG 洗四家座位，
  洗完把所有人的 `ready` 清成 false（机器人视为已准备）—— 否则"准备了却被换了风"会让人困惑。
- 两端都要能接受**没有**这两个命令的对端：老服务端回 `unknown_cmd`，老客户端不发它们。

### 2.2 对局动作

统一信封：`{"cmd":"action","type":"<动作>", ...}`

| type | 附加字段 | 含义 |
| --- | --- | --- |
| `discard` | `tile`（必填，字符串）、`tsumogiri`（bool，**新客户端必须带**，见下） | 打出一张手牌 |
| `riichi` | `tile`（立直宣言牌，必填） | 宣告立直并打出该牌 |
| `tsumo` | — | 自摸和 |
| `ron` | — | 荣和 |
| `kan` | `kind`（`ankan`/`kakan`/`daiminkan`）、`tile`（kind 对应的普通牌，如 `"5m"`）、`tiles`（可选） | 杠 |
| `chi` | `tiles`：手中取出的 2 张，如 `["3m","4m"]` | 吃（`tile` 由服务端当前 pending 的舍张决定，不传） |
| `pon` | `tiles`（可选，见「副露与赤宝」） | 碰 |
| `pass` | — | 放弃（跳过本次鸣牌/和牌机会） |
| `kyuushu` | — | 九种九牌 宣告流局 |
| `reveal_tenpai` | `tiles`（可选） | 流局时自报听牌（服务端会自动判定，此动作可省略） |

**副露与赤宝（`chi` / `pon` / `kan` 的 `tiles`）**：赤五 `0m` 与普通五 `5m` **同牌种、不同价值**
（赤宝牌多一番），所以"拿哪一张去鸣牌"由玩家决定 —— 客户端用**精确牌码**表达偏好，
服务端按它取牌：

```jsonc
{"cmd":"action","type":"pon","tiles":["0m","5m"],"ask_id":13}   // 用一张赤五 + 一张普通五
{"cmd":"action","type":"chi","tiles":["0m","6m"],"ask_id":14}   // 吃的两张里要那张赤五
{"cmd":"action","type":"kan","kind":"daiminkan","tile":"5m","tiles":["5m","5m","5m"]}
```

- **`pon` / `kan` 的 `tiles` 是可选的**：不给（老客户端）时服务端退回"手里同牌种取前 n 张"。
- 给了就必须**对得上**：牌种必须与被鸣的那张一致、张数必须正好（碰 2 / 大明杠 3）、
  要赤五就必须真有赤五 —— 任一条不满足就**作废这次鸣牌**（按没鸣处理），
  绝不替玩家拿另一张顶上（`SelfTest.meldAkaPickTests`）。
- ⚠ 客户端界面目前**没有**"选赤宝"的专用菜单：`chi` 的选项里赤五会以 `0m` 列出（选它即用赤五），
  而 `pon` 直接点按钮时走默认取法。要做界面级选择，只要在发 `pon`/`kan` 时带上 `tiles` 即可 ——
  服务端与协议这一侧**已经就绪**。

**`discard.tsumogiri` 为必需字段（老客户端可省略，服务端按 `false` 处理）**：
服务端要按它决定**从摸牌位取还是从暗手取**，而 `tile` 只是一个牌码，没法表达这件事：

- 手里已有 `5m`、又摸到 `5m`，玩家点的是**手里那张**时，两者牌码完全相同；
- 赤五 `0m` 与普通 `5m` 更是**同 kind 不同牌码**，只发 `tile` 时服务端只能"同 kind 取第一个副本"去猜。

猜错的后果是**两端手牌各差一张**（服务端动了摸牌位、客户端按手切扣了暗牌），越打越歪
（报障：幽灵手牌）。所以：

- `tsumogiri: true` → 服务端先看 `tile` 是否就是**刚摸到的那张**的牌码：是则取摸牌位那张；
  不是则**按牌码在暗手里找**（等价于一次手切）。**绝不**直接退回默认摸切（见下）；
- `tsumogiri: false`（或省略）→ 一律在**暗手**里找同牌码的那张（刚摸到的那张也在暗手里，照常参与）；
- 牌码找不到（不在手里）才是非法 → 退回默认摸切，与超时代打同一条路径。

⚠ **「声明摸切但牌码对不上」= 一次手切，不是一次摸切。** 默认摸切打的是**刚摸到的那张**，
而那正是玩家没点的那张牌 —— 拿它兜底等于替玩家打出一张他没选的牌，而且客户端会按自己的
点击扣牌：两端手牌**张数相同、内容差一张**（幽灵手牌，且不会自愈）。这个坑的典型触发场景是
**庄家第一巡**：`round_start.hand` 是**已排序**的 14 张，客户端猜不出哪张是第 14 张
—— 所以服务端用 `round_start.drawn` 直接点名（见 §3.3）。


**客户端必须回带 `ask_id`**（见 §3.6）：它是服务端唯一能识别「这条答复属于哪一次询问」的凭据，
`{cmd:"action","type":"discard","tile":"5m","ask_id":13}`。
不带 `ask_id` 的答复按「老客户端」兼容处理 —— 服务端**无法**判定它是否过期，
于是重复提交（玩家连点按钮、UI 没来得及锁）的那一条会被下一巡的 `awaitAction` 当成答复拾走。

两条由此而来的服务端铁律：

- **立直不成立时不得退化成普通打牌。** `riichi` 的 `tile` 是**为立直挑的宣言牌**，不是玩家想打出的牌；
  若这条立直无法成立（重复提交/过期回包），服务端一律退回**默认摸切**，
  绝不把那张牌当 `discard` 执行 —— 否则等于替玩家扔掉一张他自己没选过的牌。
- **每个动作只被消费一次。** 同一个 `ask_id` 的第二次提交会在下一次 `awaitAction` 里因 `ask_id` 不匹配被丢弃。

除了 `ask_id`，服务端还有一条**独立的**校验：**回包的 `type` 必须属于本次询问下发过的 `option.type`**。
不属于就丢弃、并**继续等待**（玩家照样拿到完整的 `deadline_ms`）。
这条是专门用来挡「作废询问的迟到回包」的：高优先级鸣牌一旦成立，低优先级那家的询问会被立刻取消
（`ask_cancel`），但它的 `chi`/`pon` 可能**已经在路上** —— 这类包没有 `ask_id` 可用，
只有「出牌询问根本不会给 chi/pon 这个选项」这一事实能识别它。漏过去的后果同样是**玩家没动就被代打**。

### 2.3 局间确认

```json
{"cmd":"confirm"}
```

小局与小局之间服务端会等 **所有玩家确认**，或**最多等 5 秒**（先到者为准），然后开下一局。
收到 `round_wait` 事件（见 §3.7）后即可发送本命令表示「看完了，可以开下一局」。
不是对局进行中时发送会被忽略；同一局间重复发送无副作用。

### 2.4 断线重连

```jsonc
{"cmd":"rejoin","pid":12345,"token":"..."}   // 用 hello 回包给的 pid/token 重连
```

## 3. 服务端 → 客户端（`ev`）

### 3.1 通用

```jsonc
{"ev":"error","code":"unknown_cmd","arg":"frobnicate"}   // arg 可选，永远是 ASCII
{"ev":"pong"}
{"ev":"hello_ok","pid":12345,"token":"...","name":"玩家名","ver":1}
{"ev":"rooms","rooms":[{"id":"AB12","name":"房间名","players":2,"seats":4,"playing":false}]}
{"ev":"room_joined","room":"AB12","seat":0}      // 自己入座成功（随后必有一条 room）
{"ev":"room","id":"AB12","name":"房间名","host":1,"playing":false,
 "rules":{...},
 "seats":[{"seat":0,"pid":12345,"name":"甲","ready":true,"bot":false,"score":25000},
          {"seat":1,"pid":0,"name":"CPU-1","ready":true,"bot":true,"score":25000},
          null, null]}
{"ev":"left_room"}
{"ev":"spectate","room":"AB12"}
{"ev":"chat","seat":0,"name":"甲","text":"..."}
```

`seats` 中 `null` 表示空位；机器人 `pid=0`、`bot=true`。
客户端可用 `hello_ok.pid` 在 `seats` 中匹配自己的座位，`room_joined` 是便捷通知。

`error.code` 的取值（全部 ASCII，文案在客户端 `error.*`）：
`too_large` / `bad_json` / `internal` / `need_hello` / `bad_token` / `in_room` /
`no_room` / `not_host` / `bad_seat` / `unknown_cmd`（`arg` = 那个命令名）。

### 3.2 开局

```jsonc
{"ev":"game_start","rules":{...},"seats":[{"seat":0,"name":"甲","score":25000},...],
 "round":{"bakaze":"E","kyoku":1,"honba":0}}
```

### 3.3 每局开始（**逐个玩家单独发送，手牌只含自己**）

```jsonc
{"ev":"round_start",
 "round":{"bakaze":"E","kyoku":1,"honba":0,"riichi_sticks":0},
 "seat":0,                         // 自己的座位 0..3（0=起家，逆时针递增）
 "dealer":0,
 "scores":[25000,25000,25000,25000],
 "hand":["1m","1m","3m",...],      // 13 张（庄家 14 张），**已排序**
 "drawn":"3m",                     // 仅庄家：本巡"刚摸到"的那张（= hand 里的第 14 张）。
                                   // hand 已排序 → 位置推不出来，只能点名；缺这个字段的
                                   // 老服务端只能让客户端猜，那正是幽灵手牌的来源（见 §2.2）
 "dora_indicators":["5m"],         // 表宝牌指示牌
 "tiles_left":70,                  // 牌山剩余可摸数
 "dead_wall_left":4,               // 剩余**岭上**牌数（杠后从王牌摸的就是它，见 §3.4）
 "cans":{"riichi":true,"kyuushu":false}   // 本局开局能力（仅供参考）
}
```

### 3.4 摸牌

```jsonc
{"ev":"draw","seat":0,"tiles_left":69,"dead_wall_left":4,"rinshan":false,"tile":"7p"}  // 摸牌者收到 tile
{"ev":"draw","seat":0,"tiles_left":69,"dead_wall_left":4,"rinshan":false}              // 其他玩家不含 tile
```

`rinshan = true` 表示这张是**杠后从岭上（王牌）摸的**。两条账要分清：

| 事件 | `tiles_left` | `dead_wall_left` |
| --- | --- | --- |
| 普通摸牌 | −1 | 不变 |
| **开杠时**（明杠/加杠/暗杠都一样） | −1（牌山末尾一张被移进王牌补位） | 不变 |
| **杠后岭上摸牌** | **不变**（这张来自王牌，不再吃牌山） | **−1** |

> ⚠ 所以「岭上牌有没有被摸走」**只能看 `dead_wall_left`** —— 余牌在岭上摸牌时是不动的。
> 这个字段**每次摸牌都要带**（只在小局开始带一次的话，界面上的「岭上 N」会整局停在 4）。
> 一局最多 4 次杠，`dead_wall_left` 依次是 4 → 3 → 2 → 1 → 0。
> 回归：`SelfTest.rinshanTests`（抓真报文）+ `client --selftest` 的岭上组
> + `node tools\mock-server.mjs <port> kan`（截图看「岭上 N」）+ `node tools\e2e-test.mjs` 的岭上账审计。

**一局最多 4 次杠**（`Round.canKan()`）：岭上牌只有 4 张，第 4 次之后**服务端不再下发 `kan` 选项**
（`ask.options` 里没有 `type:"kan"`，客户端自然画不出按钮），已发出的 `kan` 动作也会被拒。
判据是两条一起看：王牌里还有岭上牌 **且** 本局开杠次数 < 4。

> ⚠ **被拒绝的杠绝不能摸到岭上牌**：`kan` 是一条"要兑现"的动作 —— 牌不在手里、动作过期、
> 名额已满，都必须退回**默认摸切**（与立直不成立同一条兜底）。曾经的做法是无条件
> `rinshanNext = true`，于是每一条废杠都会从王牌白抽一张，还会把 4 次名额提前吃掉
> （`rinshanPos` 跑到 `kanCount` 前面）。回归：`SelfTest.kanLimitTests`。

### 3.5 打牌 / 鸣牌 / 立直

```jsonc
{"ev":"discard","seat":1,"tile":"3p","tsumogiri":true,"riichi":false,"riichi_stick":false,
 "sideways":true}
{"ev":"meld","seat":2,"kind":"pon","tiles":["6z","6z","6z"],"from":1,
 "called_tile":"6z","aka":[false,false,false],"called_index":3}
{"ev":"meld","seat":0,"kind":"ankan","tiles":["5m","5m","5m","5m"],"from":0}
{"ev":"riichi","seat":2,"stick_index":0,"sticks":1,"scores":[24000,25000,25000,25000]}
{"ev":"dora_reveal","dora_indicators":["5m","1p"]}       // 杠后新增/更新后的完整指示牌数组
```

`discard.sideways` = 这张牌是否**横置**：**只有立直宣言牌横置**，其余一律 `false`。
若宣言牌被鸣走，服务端会把横置**顺延**到该立直者下一张打出的牌（再被鸣走则继续顺延），
客户端只需照 `sideways` 画即可，不必自己推断。

`meld.kind ∈ {chi, pon, daiminkan, ankan, kakan}`。`from` = 被鸣牌者座位；`ankan` 时 `from = seat`。
`called_index` = 被鸣走的那张牌在原牌河中的下标（-1 表示无，如暗杠）；客户端可据此把牌河中的该张置灰。

### 3.6 询问（Ask）—— 客户端据此弹按钮

```jsonc
{"ev":"ask","ask_id":12,"seat":0,"kind":"turn","deadline_ms":25000,"tiles_left":69,
 "base_ms":5000,"bank_ms":20000,     // 本巡基本时长 / 剩余总额外时长（见 §5.1）
 "options":[
   {"type":"discard","tiles":["1m","2m","3m"]},
   {"type":"riichi","tiles":["4m","7p"]},
   {"type":"tsumo"},
   {"type":"kan","kans":[{"kind":"ankan","tile":"5m"},{"kind":"kakan","tile":"3s"}]},
   {"type":"kyuushu"}
 ]}
```

```jsonc
{"ev":"ask","ask_id":13,"seat":1,"kind":"claim","deadline_ms":25000,"base_ms":5000,"bank_ms":20000,
 "from":0,"tile":"3p",
 "options":[{"type":"ron"},{"type":"pon"},{"type":"kan","kans":[{"kind":"daiminkan","tile":"3p"}]},
            {"type":"pass"}]}
```

```jsonc
{"ev":"ask","ask_id":14,"seat":3,"kind":"chankan","deadline_ms":15000,"from":2,"tile":"5z",
 "options":[{"type":"ron"},{"type":"pass"}]}
```

- `base_ms` / `bank_ms`：思考时间明细（`turn` 与 `claim` 都会带），客户端可用于显示"本巡 5s + 额外 20s"。
- `ask_id`：本次询问的序号，客户端回包时**原样带回**（`{"cmd":"action","ask_id":13,...}`）；
  服务端用它丢弃过期回包。缺省时按座位匹配（**仅**为兼容老客户端；新客户端一律带上，见 §2.2）。
- `kind ∈ {turn, claim, chankan}`
- `option.type ∈ {discard, riichi, tsumo, kan, chi, pon, ron, pass, kyuushu}`
- `chi` 选项形如 `{"type":"chi","sets":[["3m","4m"],["2m","4m"],["2m","3m"]]}`（手中取出的两张）
- 客户端必须在 `deadline_ms` 内回 `{"cmd":"action",...}`；**超时由服务端代打：回合超时一律按「摸切」处理**
  （打出刚摸到的那张，`discard.tsumogiri = true`），鸣牌/抢杠超时按 `pass` 处理。
- 同一时刻可能有多个 `ask` 并发发往不同玩家（`kind=claim`）；每个玩家只需回自己的。
- 服务端在收到足够信息后立即推进；未答的 ask 由 `{"ev":"ask_cancel","seat":1}` 作废（客户端应关闭按钮）。
  ⚠ `ask_cancel` **不带 `ask_id`**，客户端按座位关闭当前询问即可。
- **鸣牌仲裁优先级：荣和 &gt; 杠 = 碰 &gt; 吃**（同级时**离打牌者近的赢**）。
  高优先级一旦成立，**不会再等低优先级的人** —— 他们的 ask 立即作废，
  不会因为他们没点而拖满 `deadline_ms`。被作废的那家如果回包已经在路上，服务端按 §2.2 丢弃。

### 3.7 结算

和牌：

```jsonc
{"ev":"agari","winner":0,"from":-1,"tsumo":true,
 "hand":["1m","1m","1m","2m","3m","4m","5m","6m","7m","8m","9m","9m","9m","9m"],
 "melds":[{"kind":"pon","tiles":["6z","6z","6z"],"from":1,"called_tile":"6z","aka":[false,false,false]}],
 "winning_tile":"9m","dora_indicators":["5m"],"ura_indicators":["1p"],
 "yaku":[{"code":"riichi","han":1},{"code":"menzen_tsumo","han":1},{"code":"dora","han":2}],
 "han":4,"fu":40,"dora":2,"aka":0,"ura":0,"yakuman":0,
 "limit":"mangan","base_points":2000,
 "score_delta":[8000,-2000,-2000,-2000],
 "scores_after":[33000,23000,23000,23000],
 "pao":{"seat":-1},
 "ura_revealed":true}
```

- `from = -1` 表示自摸。`limit` ∈ `{"", "mangan","haneman","baiman","sanbaiman","kazoe_yakuman","yakuman"}`
  （空串表示普通点数；役满不区分倍数，倍数看 `yakuman` 字段 —— 所以 `两倍役满` 也发 `"yakuman"`）。
- ⚠ **`hand` 在自摸与荣和下张数不同，这是刻意约定**：
  - **自摸**：`hand` 是**含和了牌的 14 张**（自摸那张本来就在暗手里），`winning_tile` 只是把
    其中一张**再指认一次**；
  - **荣和**：`hand` 是**不含和了牌的 13 张**，和了牌只在 `winning_tile` 里。
  - 所以客户端做牌面示意时**必须先判断 `tsumo`**：自摸要先把 `winning_tile` 从 `hand` 里摘掉一张
    再单独摆到和了牌位，否则界面上和了牌会出现两次（报障）。
- **参数化役种**（役牌/场风/自风）拆成 `code` + `tile` 两个字段：`code ∈ {yakuhai, round_wind, seat_wind}`，
  `tile` 是那张字牌的牌码（`1z..7z`）。客户端用带 `%1` 的模板拼文案（`yaku.yakuhai` = 「役牌 %1」）。
  ```jsonc
  "yaku":[{"code":"yakuhai","tile":"5z","han":1}]     // 役牌 白
  ```
- 认不出的 `code`（服务端加了役种而客户端语言文件还没跟上）由客户端**原样显示码**。
- 全部码表与中文对照见 `server/src/main/java/mahjong/rules/YakuCodes.java`（**唯一**的中文名→码映射处）；
  静态核对：`node tools\check-i18n.mjs`。
- `yaku[].han` 与合计 `han`：**役满役没有「番」这个量纲**，但报文里不能省略、更不能发 0
  （客户端会原样显示成「0 番」）。约定按 **「役满 = 13 番等价」折算**：
  役满役的 `han = 13 × 倍数`，合计 `han = 13 × 总倍数`（所以**逐役之和 == 合计**）。
  真正的量纲在 `yakuman` 字段（`n` = n 倍役满），**结算界面按它显示「n倍役满」**，
  不去显示那个等价番数。
  ```jsonc
  "yaku":[{"code":"suuankou_tanki","han":26,"yakuman":2}], "han":26, "yakuman":2, "limit":"yakuman"
  ```
- 累计役满（番数 ≥13 但**没有**役满役）不是役满：`yakuman = 0`，`han` 照常给（如 13）。
  打点档位**取决于 `rules.kazoe_yakuman`**：采用它的规则（《天凤》/《雀魂》）→ `limit:"kazoe_yakuman"`、
  `base_points = 8000`；**M.League 不采用** → 普通役上限三倍满，`limit:"sanbaiman"`、`base_points = 6000`。
- 役满**不加倍**的规则（M.League /《天凤》，即 `rules.double_yakuman=false`）下，
  国士无双十三面 / 四暗刻单骑 / 纯正九莲宝灯 / 大四喜仍然是**各自的役种名**
  （照常发 `kokushi_13` 等码），只是 `yakuman` 计 1 —— 役种命名与取值是两件事。

流局：

```jsonc
{"ev":"ryuukyoku","type":"exhaustive","tenpai":[true,false,false,true],
 "hands":[[...],[...],[...],[...]],       // 听牌者亮牌，未听牌者该位置为 null
 "score_delta":[1500,-1500,-1500,1500],
 "scores_after":[...],
 "nagashi":[-1,-1,-1,-1],                 // 流局满贯者座位，否则 -1
 "reason":"exhaustive"                       // 码表见下
}
```

`reason` 码表：`exhaustive`（荒牌流局）/ `nagashi`（流局满贯）/ `kyuushu`（九种九牌）/
`four_winds`（四风连打）/ `four_kans`（四杠散了）/ `four_riichi`（四家立直）。
`type` 字段保持原样（老客户端拿它做判据），**两者不一定相同**：`type` 是事件分类，`reason` 是具体原因。

#### 局间等待

`round_end` 之后、下一局 `round_start` 之前，服务端广播：

```jsonc
{"ev":"round_wait","ms":5000}
```

- **含义**：本局已结算完，服务端在等玩家确认，最多等 `ms` 毫秒。
- **提前开始**：所有在座玩家都发来 `{"cmd":"confirm"}`（见 §2.3）就立即开下一局；
  机器人视为立即确认，所以人机对局不必干等满 5 秒。
- **到点必开**：即使无人确认，`ms` 到点也开下一局 —— **防止有人挂机卡住整桌**。
- 客户端收到后应清掉操作栏、显示倒计时/提示；确认与否只影响节奏，**不影响任何规则判定**。

局终：

```jsonc
{"ev":"round_end","round":{"bakaze":"E","kyoku":2,"honba":0,"riichi_sticks":0},
 "scores":[26000,24000,25000,25000],"next":{"bakaze":"E","kyoku":2,"honba":0},
 "renchan":false,"game_over":false}
```

整场结束：

```jsonc
{"ev":"game_end","scores":[41200,...],"ranking":[0,2,1,3],
 "final":[{"seat":0,"name":"甲","score":41200,"point":53.6,"uma":30,"oka":20,"rank":1}, ...]}
```

`point` = **精算点数** = `(score − 返点)/1000 + 马点(uma) + 头名赏(oka)`：

- **头名赏**只给 1 位，`oka = (返点 − 配给原点) × 4 / 1000`（M.League /《天凤》= 20；
  《雀魂》段位场的精算基准与配给原点同为 25000，所以是 0）。见 `docs/日本麻将.md` §精算点数。
- `rules.tie_split_point`（M.League）时，**同点的几家平分对应名次的马点与头名赏** —— 此时
  `uma`/`oka` 与 `rules.uma` 不同（可能是小数），所以报文里回传的是**实际所得**，
  不是规则表里的原值；否则（《天凤》/《雀魂》）按起家座次先后定名次、原值发放。
- `ranking` = 名次 → 座位；`final` 按名次顺序排列，`rank` 从 1 起。

### 3.8 全量同步（重连 / 中途入座）

```jsonc
{"ev":"state","seat":0,"round":{...},"scores":[...],"hand":[...],
 "melds":[[...],[...],[...],[...]],"discards":[[...],[...],[...],[...]],
 "dora_indicators":[...],"tiles_left":40,"dead_wall_left":2,"phase":"playing",
 "riichi":[false,true,false,false],"furiten":[false,false,false,false]}
```

- `hand` 是**请求者自己**的手牌（不是四家的）。
- `tiles_left` / `dead_wall_left` 只是**张数**，不含任何牌面。
- `furiten` 数组长度仍是 4，但**只有请求者自己那一项可能为 true**，其余恒 false；
  旁观者（`seat = -1`）四项全 false。理由见 §3.10 —— 临时振听等价于「他听牌了」。

### 3.9 观战

非入座者收到与其他玩家相同的公开事件（不含 `hand`、不含 `draw.tile`），并收到
`{"ev":"spectate","room":"AB12"}`。观战者不收到 `ask`。

### 3.10 信息可见性：服务端**绝不**下发的东西

客户端属于不可信输入，因此下面这些隐藏信息**任何事件里都不出现** —— 改过的客户端也拿不到：

| 隐藏信息 | 服务端的处理 |
| --- | --- |
| 牌山的**牌面与顺序**、剩余牌都是什么 | 只发 `tiles_left` / `dead_wall_left` 这类**张数**；`Wall.debugAllTiles()` 仅供内部自检，从不上网 |
| 他家**未出示的手牌**（含刚摸到的那张） | 手牌只发给本人：`round_start` / `state` 按座位**单发**，`draw.tile` 只给摸牌者（其余三家只收到「谁摸了牌 + 剩余张数」） |
| **未翻开**的宝牌指示牌 | `dora_indicators` 只含已翻开的张数（`Wall.doraCount()`）；开杠才 `dora_reveal` 下一张 |
| **里宝指示牌** | 仅在和牌、且和牌者立直（或两立直）且 `rules.ura` 时随 `agari` 下发；其余任何时候都不发 |
| 他家**询问的选项**与 `win_note` | `ask` / `ask_cancel` 只发给被问的那一家，绝不广播 |
| 他家**振听状态** | `state.furiten` 只填请求者自己那一项（§3.8） |
| **机器人**座位的手牌 | 机器人没有连接（`Seat.session == null`），服务端直接调 `Bot.decide`，从不经过网络 |

与之相对的**公开信息**（允许下发）：四家牌河与副露、立直状态与供託、**已翻开**的宝牌指示牌、
各家点数、剩余张数、`tsumogiri`（手切/摸切）、和牌后**和牌者**的手牌与（条件满足时的）里宝指示牌、
流局时**仅听牌家**的手牌（`ryuukyoku.hands` 里未听牌家是 `null`）、`ryuukyoku.tenpai`。

> 新增协议字段时先回答一句：**这条信息改过的客户端拿到会怎样？**
> 只要涉及「牌山 / 他家手牌 / 未翻开的指示牌 / 他家振听」，就必须按座位单发或干脆不发。

### 3.11 对局记录（回放）

**服务端把每一场半庄完整记下来**：客户端实际收到的每一条报文（按到达顺序，含聊天）＋
每小局的**起始牌山 136 张**（按抓牌顺序）。记录在整场结束时落盘（默认 `./replays/`，重启不丢）。

两处**新增字段**（既有客户端忽略即可）：

```jsonc
{"ev":"game_start", ..., "replay_id":"7FQ3M2XK9A", ...}   // 本场的回放标识符（10 位 base32）
{"ev":"game_end",   ..., "replay_id":"7FQ3M2XK9A", ...}
```

取回放（**只读**，两个命令，与是否入座无关）：

```jsonc
// 客户端 → 服务端
{"cmd":"replay_list","offset":0,"limit":30}
{"cmd":"replay_get","id":"7FQ3M2XK9A","from":0,"count":600}

// 服务端 → 客户端
{"ev":"replay_list","total":12,"items":[
  {"id":"7FQ3M2XK9A","created":1766...,"names":["甲","乙","丙","丁"],"room":"AB12",
   "preset":"mleague","rounds":6,"entries":2500,"bytes":321456,"truncated":false}]}

{"ev":"replay_get","id":"7FQ3M2XK9A","from":0,"total":2500,
 "meta":{"id":"...","created":...,"names":[...],"preset":"mleague","rounds":6,"entries":2500,
         "rules":{...},
         "walls":[[136 个牌 id], ...],        // 每个小局一份，**按抓牌顺序**
         "round_at":[0,420,830,...]},         // 每个小局在 entries 里的起始下标
 "entries":[{"seq":0,"t":0,"to":-1,"b":{"ev":"replay_round","index":0,"wall":[...]}}, ...]}
```

- `entries[].b` 就是**实时对局下发的那条报文本体**（`ev` + 原字段），`to` 是收件座位
  （`-1` = 广播）。所以"回放"= 把这些报文按 `seq` 顺序重新喂给客户端 ——
  与实时对局**同一条渲染路径**，不会出现"实时能看、回放拼不出来"。
- **`seq` 单调递增，聊天与操作共用一条序号** → "某句话发生在第几步之后"是确定的；
  客户端按 `seq` 切分即可实现需求里的"以玩家操作分割"。
- **一条逻辑事件可能对应多条 entry**：按座位单发的报文会给每个收件人各记一条
  （`round_start` 必有 4 条 —— 四家手牌各不相同；`game_start` 也有 4 条，内容逐字相同）。
  所以**导航的单位是"步"而不是 entry**：连续、同事件名、且都是单发（`to ≥ 0`）的条目里，
  `round_start` 与"内容完全相同"的那些算同一步。客户端据此把 806 条 entry 显示成 788 步。
- **摸牌只记摸牌者那一份**：`broadcastDraw` 给四家各发一份，但只有摸牌者那份带 `tile`，
  其余三份是同一个事实 —— 记三份会让体积翻三倍、`下一步` 要按三次，
  所以服务端在录制时就丢掉了它们（回放里每条 `draw` 都是"谁摸到什么"）。
- `replay_round` 是**只记不给客户端**的条目：它带该小局的牌山快照与局况
  （`bakaze/kyoku/honba/dealer`），也是小局边界（与 `meta.round_at` 一一对应）。
  小局跳转的落点是**最后一条 `round_start`**（配牌完成）而不是边界条目 ——
  边界上四家手牌还没下发，画面会是"四个空手牌"。
- **分块**：`count` 服务端钳到 **≤ 2000**（越界钳制，不报错）；`from` 超过总数返回空数组。
  客户端按页拉取，不必一次把整场读进内存。
- **限速**：两个命令共用一个闸门，**每连接每 10 秒最多 60 次**，超限回
  `{"ev":"error","code":"replay_rate_limited"}`。
- **找不到 / ID 非法**（含 `../../` 这类路径穿越尝试）一律回
  `{"ev":"error","code":"replay_not_found"}` —— 不区分"不存在"与"非法"，避免被拿来探测。
- ⚠ **回放天然是上帝视角**：`walls` 给了完整牌山顺序，`draw`/`ask`/`round_start` 的
  单发报文也原样保留（`to` 标了收件人，客户端做上帝视角时合并四家）。
  也就是说**拿到回放 ID 的人能看到这一场所有隐藏信息** —— 这是回放功能的前提
  （需求要求"牌山整体视图 + 每一步操作"），所以 ID 用 CSPRNG 生成 10 位 base32（约 50 bit），
  且**只在牌局结束后**才可读（对局进行中取到的也只是已记录的部分）。
- **不记的东西**：`state`（重连/旁观快照，可由事件流重建）、`rooms` / `room`（大厅噪音）、
  `error`。理由与体积账见 `Replay.SKIP` 的注释。
- 落盘格式：一行 JSON 一条，**第一行是头**（含牌山与小局索引），其后是操作。
  所以重建索引只需读每场文件的第一行。
- 服务端开关：`--replay-dir <dir>`（默认 `replays`）、`--replay-max <n>`（默认 50 场）、
  `--replay-max-mb <n>`（默认 96 MB）、`--no-replay`。超出上限按创建时间**淘汰最旧的并删文件**。
- **房间牌谱**：元信息里的 `room` 是这一场所属的**房间号**（录制的就是那个房间的对话与操作）。
  客户端据此显示「（房间 AB12）」，玩家可以按"刚才那一桌"找那一场；
  老记录没有这个字段时是空串（客户端不显示那一截）。

#### 牌山视图的坐标（136 张 → 抓牌顺序）

`walls[i][k]` 里的下标 `k` 就是**抓牌顺序**（与服务端 `Wall.tiles` 同一份顺序）：

| `k` | 含义 | 归属 |
| --- | --- | --- |
| `0..47` | 配牌：**从庄家起每人一次抓 4 张、共 3 轮**（每人 12 张） | 第 `k/16` 轮的第 `(k%16)/4` 家，该家本轮第 `(k%4)` 张 |
| `48..51` | 每人再各抓 1 张（补齐 13 张） | 第 `k-48` 家 |
| `52` | **庄家的第 14 张**（配牌期就发出，第一巡不再摸） | 庄家 |
| `53..121` | 牌局中一张一张的摸牌（与 `draw` 事件一一对应，`rinshan=false`） | 见该 `draw` 事件的 `seat` |
| `122..125` | 王牌·**岭上** 4 张（杠后从这里摸，`dead_wall_left` 4→3→2→1） | 开杠的那家 |
| `126..130` | 王牌·**表宝牌指示牌** 5 张 | 翻开后所有人可见 |
| `131..135` | 王牌·**里宝指示牌** 5 张 | 仅和牌且立直时下发 |

- 归属里的「第 `s` 家」一律指 **从庄家起逆时针第 `s` 家**（`seat = (dealer + s) % 4`），
  所以左右读一列就是现实中的"庄家→下家→对家→上家各一张"。
- **开杠会把可摸牌山末尾一张移进王牌补位**：第 `m` 次开杠时下标 `122 - m` 的那张从此不再被摸到
  （客户端可据此把它标成"已移入王牌"）。所以 `53..(121-开杠数)` 才是真正摸得到的部分。
- 每张牌在**当前时刻**的去处（在牌山 / 已摸进手牌 / 已打出 / 已副露 / 王牌）
  由"该下标是否已出现"＋当前局面推导 —— 客户端用同一份事件流渲染，不必额外字段。
- **客户端怎么排这 136 张**（`WallView`）：铺成**一条抓牌顺序序列**，**每列 4 张**（自上而下读）、
  每 4 列一个稍大的空隙，末尾 14 张王牌仍在同一序列里（只换底色）。
  ⚠ **不按玩家分行** —— 副露会改变下一个摸牌的人，"一行一家"的排法在副露出现后就是错的；
  "4 张一组"纯粹是防止看花眼的**阅读分组**。
  归属（谁拿走的）用每张牌底部的一条**归属色细线**表达，四家混排也认得出。
- **回放动画**：只有"前进一个操作"播飞牌动画，其余跳转一律静默 —— 因为跳转 = 从小局开头重放，
  带动画就会把前面几巡的弃牌重打一遍。见 `DESIGN.md`「对局记录与回放」。

## 4. 座位与方向约定

- `seat` `0..3`，逆时针递增（0=东/起家，1=南，2=西，3=北）。
- `seat n` 的**下家**是 `(n+1)%4`（逆时针下一位，即摸切顺序的下一家），**上家**是 `(n+3)%4`。
- 吃只能吃**上家**（`(seat+3)%4`）打出的牌。
- 客户端 UI 永远把自己画在下方：屏幕方位 = `(other - mySeat + 4) % 4` → `{0:下, 1:右, 2:上, 3:左}`。

## 5. 规则配置 `rules`

**缺省预设是 M.League**（`"preset": "mleague"`，依据 `docs/日本麻将.md` 2026-09 版里逐条标注的
M.League 规则）。服务端先按 `preset` 铺一整套值，**再用报文里出现的单项字段覆盖**：

```jsonc
{
  "preset": "mleague",       // "mleague"(默认) | "tenhou"《天凤》 | "majsoul"《雀魂》 | "custom"(不铺，保留当前值)
  "length": "hanchan",       // "tonpuu"(东风战) | "hanchan"(半庄)
  "aka": 3,                  // 赤宝牌数量 0|3（传 4 按 3 处理）
  "kuitan": true,            // 食断
  "ura": true,               // 里宝牌
  "kan_dora": true,          // 杠宝牌
  "double_yakuman": false,   // 两倍役满（大四喜/国士13面/四暗刻单骑/纯正九莲）
                             //   M.League/《天凤》= false（这 4 种仍报各自役种名、只计 1 倍）；
                             //   《雀魂》= true
  "renhou": "off",           // "off"|"mangan"|"yakuman"
  "head_bump": true,         // 头跳（M.League：不采用三家和了，改判头跳）
  "sancha_abort": false,     // 三家和了流局
  "four_riichi_abort": false,// 四家立直流局
  "four_kan_abort": false,   // 四杠散了流局
  "four_wind_abort": false,  // 四风连打流局
  "kyuushu_abort": false,    // 九种九牌流局
  "nagashi_mangan": false,   // 流局满贯
  "tobi": false,             // 击飞
  "agariyame": false,        // 南4局庄家和了即结束（和了止）
  "west_extension": false,   // 西入
  "kuikae": true,            // 禁止食替
  "pao": true,               // 包牌
  "pao_four_kan": true,      // 四杠子包牌（只有 M.League 有）
  "pao_covers_all": false,   // 包牌是否承担"复合后的全部役满得点"（《天凤》= true；
                             //   M.League/《雀魂》只包被包的那一役）
  "noten_penalty": 3000,     // 不听罚符总额
  "start_score": 25000,      // 配给原点
  "return_score": 30000,     // 返点（= 精算基准；《雀魂》段位场与原点同为 25000，故无头名赏）
  "uma": [30, 10, -10, -30], // 马点（1位..4位）
  "tie_split_point": true,   // 终局同点：true = 平分对应名次的加点（M.League）；false = 按起家座次定名次
  "kiriage_mangan": true,    // 切上满贯：3番60符 / 4番30符（基本点 1920）按满贯计（M.League 采用）
  "kazoe_yakuman": false,    // 累计役满：番数 ≥13 且无役满役时按役满计；
                             //   M.League 不采用 → 普通役上限三倍满（基本点 6000）
  "double_wind_pair_fu": 2,  // 连风雀头（自风=场风）的符数：M.League = 2，一般规则 = 4
  "riichi_min_score": 0,     // 立直所需最低点数（一般规则 1000；M.League 无要求）
  "riichi_min_tiles_left": 0,// 立直所需剩余可摸牌数（一般规则 4；M.League 无要求）
  "riichi_no_haitei": true,  // 摸到海底牌之后不允许立直（M.League）
  "ankan_keeps_shape": true, // 立直后的暗杠除"所听牌不变"外还要求**面子构成不变**（M.League）
  "thinking_base_ms": 5000,  // 每巡基本时长（这段内出牌不消耗额外时长）
  "thinking_bank_ms": 20000, // 总额外时长（超出基本时长的部分从这里扣，整场共用）
  "thinking_ms": 15000,      // 兼容旧字段：等价于「固定时长」= base 15000 + bank 0
  "min_han": 1               // 起和番数
}
```

上表的**值是 M.League 预设**（`preset` 的默认值即 M.League）。三套预设的差异速查：

| 项 | M.League | 《天凤》 | 《雀魂》 |
| --- | --- | --- | --- |
| 中途流局（四家立直/四杠散了/四风连打/九种九牌） | 全关 | 全开 | 除三家和了外全开 |
| 三家和了 | 头跳 | 流局 | 无（二家/三家和了都成立） |
| 击飞 / 和了止 / 流局满贯 / 西入 | 无 | 有 | 有 |
| 2 倍役满那 4 种 | 1 倍 | 1 倍 | 2 倍 |
| 累计役满 | 无（上限三倍满） | 有 | 有 |
| 切上满贯 | 有 | 无 | 无 |
| 连风雀头符 | 2 符 | 4 符 | 4 符 |
| 立直门槛 | 无点数/残牌要求，摸海底后禁立直 | ≥1000 点且残牌 ≥4 | 同《天凤》 |
| 立直后暗杠 | 听牌不变 **且面子构成不变** | 只听牌不变（禁送杠） | 同《天凤》 |
| 四杠子包牌 | 有 | 无 | 无 |
| 包牌范围 | 只包被包的那一役 | 复合后的全部役满 | 只包被包的那一役 |
| 马点 / 头名赏 | 10-30 + 20 | 10-20 + 20 | 5-15 + 0（精算基准 25000） |
| 同点 | 平分对应名次的加点 | 按起家座次 | 按起家座次 |

客户端建房间时可不传 `rules`（或只传 `preset`），服务端用上面的缺省。

### 5.1 思考时间（"20+5" 格式）

`20+5` 读作 **总额外 20 秒 + 每巡基本 5 秒**，采用国际象棋钟模型。
**写法是「额外+每巡」，大数在前** —— 每巡基本时长一般远小于总额外时长，别写反：

| 规格 | 含义 | `thinking_bank_ms` | `thinking_base_ms` |
| --- | --- | --- | --- |
| `20+5` | 额外 20 秒 + 每巡 5 秒（默认） | 20000 | 5000 |
| `0+15` | 无额外，固定每巡 15 秒 | 0 | 15000 |
| `10+5` | 额外 10 秒 + 每巡 5 秒 | 10000 | 5000 |

- 每巡先给 `thinking_base_ms`，这段内出牌**不扣**额外时长；
- 超出部分从 `thinking_bank_ms` 里扣。**扣减离散到 1 秒**，且**剩余量向上去整**（偏向玩家多给）。
  例：额外 20s + 基本 5s，思考 7.3s → 超出 2.3s → 剩 17.7s → 记为 **18s**；
  再例：思考 18.001s → 超出 13.001s → 剩 4.999s → 记为 **5s**；
- 额外时长**每小局重置**：一局打完（连庄也算新的一局）就回到 `thinking_bank_ms`；
- **鸣牌询问（吃 / 碰 / 杠 / 荣和）同样消耗额外时长**：被问到的每个人各按自己的时钟算，
  全局等待取各自时限中的最长者；谁答得慢扣谁的，未应答者按到时扣。

只传旧字段 `thinking_ms` 时，等价于 `base = thinking_ms`、`bank = 0`（固定时长），
老客户端/脚本无需改动。

## 6. 服务端命令行

```
java -jar mahjong-server.jar [--port 10086] [--host 0.0.0.0] [--verbose]
                             [--replay-dir replays] [--replay-max 50] [--replay-max-mb 96]
                             [--no-replay]
```

- 启动后打印 `LISTENING <host>:<port>` 一行到 stdout（便于客户端/脚本探测）。
- **对局记录默认开启**并落盘到 `--replay-dir`（默认 `./replays`，该目录不属于仓库、
  `.gitignore` 已忽略）：场数 / 总字节双上限，超出按创建时间淘汰最旧的**并删文件**；
  `--no-replay` 关闭（此时 `replay_*` 命令一律回空/`replay_not_found`，且录制零开销）。
- **默认双栈监听**：优先绑定 IPv6 通配地址并关闭 `IPV6_V6ONLY`，同一端口在
  `0.0.0.0`（IPv4）与 `[::]`（IPv6）上同时可连；环境不支持 IPv6 时自动回退纯 IPv4。
  这一点对 WSL / 容器场景很重要 —— 只绑 IPv4 时，宿主机侧的 localhost 转发
  可能只暴露 `[::1]`，客户端填 `127.0.0.1` 就会「连接被拒绝」。
- `SIGTERM` / `SIGINT` 优雅关闭。

### 6.1 客户端连接约定

客户端应当把主机名解析成**全部**候选地址并逐个尝试（写 `127.0.0.1` 时补 `::1`，反之亦然），
而不是只连第一个解析结果。参考实现见 `client/src/net/NetClient.cpp` 的
`buildCandidates()` / `tryNextCandidate()`。

## 7. 动作合法性（服务端权威）

服务端必须校验：非法动作**不得改变状态**（下面第 1 条是当前实现的取舍）。

- 不是该玩家的行动时机
- 打出的牌不在自己手牌中
- 振听状态下 `ron`
- 无役 / 未达番缚 / 无役种而 `tsumo`
- `riichi`：非门前清、已立直、点数 < 1000、牌山剩余 < 4、宣言后未听牌、已振听（可选）
- `chi`：非上家舍张、组合不成立顺子、违反食替
- `pon`/`kan`：手中张数不足
- `kan`：立直后暗杠改变听牌

> ⚠ **回合内动作的现状与「回 error + 重新下发 ask」有出入**（实现见 `Round` 出牌段）：
> 玩家回了一条当前无法成立的动作时，服务端**不重发 ask、也不发 error**，而是退回**默认摸切**
> （`defaultDiscardId`，与服务端超时代打完全一致）。取舍是「宁可摸切，也绝不替玩家
> 打出一张他自己没选过的牌」。`riichi` 尤其如此 —— 它的 `tile` 是**为立直挑的宣言牌**，
> 立直不成立时把这张牌当普通 `discard` 执行是明确的 bug（玩家连点立直按钮即可触发）。
> 若后续要真正落实 §7 的「error + 重发 ask」，需要改 `Round` 出牌段的这条兜底，
> 并在客户端补上 `illegal_action` 的文案与处理（**当前 `error.*` 词表里没有这个码**，
> 客户端收到 `error` 也不会去清询问栏 —— 服务端并没有重发 ask，清了只会让玩家点不动）。
> 鸣牌段的现状不同：**类型不属于本次询问下发过的 `option.type` 的回包会被直接丢弃**
> （这是识别「没有 `ask_id` 的废包」的唯一判据，见 §2.2 与 `AGENTS.md` §2.3-10）。
