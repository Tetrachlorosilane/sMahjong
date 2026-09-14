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
{"cmd":"add_bot"}                                 // 房主给空位补一个机器人
{"cmd":"remove_bot","seat":2}                     // 房主移除机器人
{"cmd":"start_game"}                              // 房主（4 人齐且全 ready 时）开始
{"cmd":"chat","text":"..."}                       // 房间内聊天
{"cmd":"ping"}
```

`rules` 见 §5。

### 2.2 对局动作

统一信封：`{"cmd":"action","type":"<动作>", ...}`

| type | 附加字段 | 含义 |
| --- | --- | --- |
| `discard` | `tile`（必填，字符串）、`tsumogiri`（bool，可选） | 打出一张手牌 |
| `riichi` | `tile`（立直宣言牌，必填） | 宣告立直并打出该牌 |
| `tsumo` | — | 自摸和 |
| `ron` | — | 荣和 |
| `kan` | `kind`（`ankan`/`kakan`/`daiminkan`）、`tile`（kind 对应的普通牌，如 `"5m"`） | 杠 |
| `chi` | `tiles`：手中取出的 2 张，如 `["3m","4m"]` | 吃（`tile` 由服务端当前 pending 的舍张决定，不传） |
| `pon` | — | 碰 |
| `pass` | — | 放弃（跳过本次鸣牌/和牌机会） |
| `kyuushu` | — | 九种九牌 宣告流局 |
| `reveal_tenpai` | `tiles`（可选） | 流局时自报听牌（服务端会自动判定，此动作可省略） |

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
`no_room` / `not_host` / `unknown_cmd`（`arg` = 那个命令名）。

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
 "hand":["1m","1m","3m",...],      // 13 张（庄家 14 张），已排序
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
- 累计役满（番数 ≥13 但**没有**役满役）不是役满：`yakuman = 0`，照常给 `han`（如 26）与 `limit:"kazoe_yakuman"`。

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
 "final":[{"seat":0,"name":"甲","score":41200,"point":53.6,"uma":15,"rank":1}, ...]}
```

`point` = 精算点数 = `(score - 返点)/1000 + 马点`。

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

## 4. 座位与方向约定

- `seat` `0..3`，逆时针递增（0=东/起家，1=南，2=西，3=北）。
- `seat n` 的**下家**是 `(n+1)%4`（逆时针下一位，即摸切顺序的下一家），**上家**是 `(n+3)%4`。
- 吃只能吃**上家**（`(seat+3)%4`）打出的牌。
- 客户端 UI 永远把自己画在下方：屏幕方位 = `(other - mySeat + 4) % 4` → `{0:下, 1:右, 2:上, 3:左}`。

## 5. 规则配置 `rules`

```jsonc
{
  "length": "hanchan",       // "tonpuu"(东风战) | "hanchan"(半庄)
  "aka": 3,                  // 赤宝牌数量 0|3（传 4 按 3 处理）
  "kuitan": true,            // 食断
  "ura": true,               // 里宝牌
  "kan_dora": true,          // 杠宝牌
  "double_yakuman": true,    // 两倍役满（大四喜/国士13面/四暗刻单骑/纯正九莲）
  "renhou": "off",           // "off"|"mangan"|"yakuman"
  "head_bump": false,        // 头跳（false=不采用头跳，但也不采用三家和流局）
  "sancha_abort": false,     // 三家和了流局
  "four_riichi_abort": true, // 四家立直流局
  "four_kan_abort": true,    // 四杠散了流局
  "kyuushu_abort": true,     // 九种九牌流局
  "nagashi_mangan": true,    // 流局满贯
  "tobi": true,              // 击飞
  "agariyame": false,        // 南4局庄家和了即结束
  "kuikae": true,            // 禁止食替
  "pao": true,               // 包牌
  "noten_penalty": 3000,     // 不听罚符总额
  "start_score": 25000,
  "return_score": 30000,     // 返点
  "uma": [15, 5, -5, -15],   // 马点（1位..4位）
  "thinking_base_ms": 5000,  // 每巡基本时长（这段内出牌不消耗额外时长）
  "thinking_bank_ms": 20000, // 总额外时长（超出基本时长的部分从这里扣，整场共用）
  "thinking_ms": 15000,      // 兼容旧字段：等价于「固定时长」= base 15000 + bank 0
  "min_han": 1               // 起和番数
}
```

缺省即上面的值。客户端建房间时可不传 `rules`，服务端用缺省。

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
```

- 启动后打印 `LISTENING <host>:<port>` 一行到 stdout（便于客户端/脚本探测）。
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
