# 机器人 AI 包（`bot-ai/`）—— 格式与装载

给**部署者**（要把某代网络挂到服务器上）与**训练侧**（要新打一个包）看的格式文档。
面向玩家的说明在 `README.md`「机器人用哪一代 AI」；协议字段在 `docs/PROTOCOL.md` §3.1；
实现判据在 `AGENTS.md` §6.6 与 `NOTES.md` §6.6。

---

## 1. 一句话

**服务端启动时自动挂载 `bot-ai/`（与 `replays/`、`players/` 同层）下的每个一级子目录**，
每个子目录 = 一个可选的"机器人 AI"，客户端建房间时按**名字**选一代。
深度模型（纯 Java 手写前向）与启发搜索（内置牌效机器人）**共用同一套包接口**，
所以加一代新网络 = 往 `bot-ai/` 里丢一个目录，**不用改代码、不用加启动参数、不用重启以外的任何操作**。

```
<服务端启动目录>/
├─ mahjong-server.jar
├─ replays/            ← 对局记录（运行时数据）
├─ players/            ← 玩家档案（运行时数据）
└─ bot-ai/             ← ★ 机器人 AI 包（本文档）
   ├─ README.txt          （给部署者的速记，服务端不读它）
   ├─ teacher/            {bot.json}                       启发搜索（内置牌效）
   ├─ ppo2-g04/           {bot.json, net.bin}              深度模型
   └─ awr-002/            {bot.json, net.bin, net.json}    深度模型（net.json 只是备注）
```

---

## 2. 装包：三条途径

| 途径 | 怎么写 | 什么时候用 |
| --- | --- | --- |
| **自动挂载**（推荐） | 把包目录放进**启动目录**下的 `bot-ai/` | 发布包默认形态：解压即用，不需要参数 |
| 再挂一个目录 | `--bot-ai-dir <目录>`（可重复） | 直接指到训练侧的 `ckpt/`（老目录、没有 `bot.json` 也能用） |
| 点名注册/覆盖 | `--bot-ai-reg <名字>=<策略串>`（可重复） | 临时试一个权重、或覆盖同名包 |

启动顺序固定：`clear()`（铺四个内置锚点）→ **自动扫 `bot-ai/`** → 逐个 `--bot-ai-dir` →
逐个 `--bot-ai-reg` → `--bot-ai <名字>` 定**默认**。
**后面的覆盖前面的**（同一个名字重复注册 = 覆盖，不是报错），所以"点名的串"总能压过包。

`bot-ai/` **不存在不算错**（可以一个包都不放）：只在启动日志里留一句提示，
四个内置锚点（`teacher` / `first` / `pass` / `random`）永远可选。

启动日志里能看到最终清单：

```
[INFO] 挂载机器人 AI 包 `ppo2-g04`（kind=net，来源 …（打包 2026-09-25）；α=auto（让位 95.1%，数据集 rl-001））
[INFO] 机器人 AI：teacher, first, pass, random, awr-002, bc-003, ppo-g04, ppo2-g04（默认 teacher）
```

---

## 3. 包格式：一个目录 + `bot.json`

`bot.json` 是**唯一接口**（服务端只认这个名字）。**载荷跟着 `kind` 走**，
两种形态共用同一套字段名，所以客户端的下拉框、服务端的注册表、协议里的 `bot_ai` 都只有一份。

### 3.1 `kind: "net"` —— 深度模型

```
bot-ai/ppo2-g04/
  bot.json   {"kind":"net","name":"ppo2-g04","model":"net.bin","alpha":2,"temp":0,"note":"…"}
  net.bin    权重文件（`python -m mahjong_ml.export` 的形状，纯 Java 手写前向读它）
```

### 3.2 `kind: "builtin"` —— 启发搜索（内置牌效 / 脚本机器人）

```
bot-ai/teacher/
  bot.json   {"kind":"builtin","name":"teacher","policy":"teacher","note":"内置启发搜索（牌效机器人）"}
```

`policy` ∈ `teacher` | `first` | `pass` | `random`：

| policy | 是什么 |
| --- | --- |
| `teacher` | **内置牌效机器人**（`mahjong.bot.Bot`）：牌效 + 押し引き + 危险度，是训练标签的来源 |
| `first` | 每步选第一个合法动作（最笨的对照臂） |
| `pass` | 不鸣牌、不和（对照臂） |
| `random` | 合法动作里随机（对照臂） |

### 3.3 字段表

| 字段 | 适用 | 缺省 | 含义 |
| --- | --- | --- | --- |
| `kind` | 两者 | **按载荷猜** | `net` / `builtin`。不写时：目录里有 `model`（或 `net.bin`）→ `net`，否则看 `policy` |
| `name` | 两者 | **目录名** | 给客户端看/传的名字（见 §4）。写它就能"目录名 `foo` 但对外叫 `bar`" |
| `model` | `net` | `net.bin` | 权重文件名（**相对包目录**，不许写绝对路径） |
| `policy` | `builtin` | 无（必填） | 内置策略名 |
| `alpha` | `net` | `0`（= 不加先验） | **teacher 先验权重**（P5b 混合）：`>0` 时该包是"启发搜索 + 深度模型"的混合体 |
| `temp` | `net` | `0`（= 贪心） | **采样温度**（P4 探索）：`softmax(logits/T)` 采样 |
| `note` | 两者 | `""` | 人看的备注（来源路径、打包日期、α 是怎么选的）——**只进服务端日志** |

`alpha` / `temp` 允许小数（`0.5` 不会因为"整数读法"被吞掉）。
它们在内部被拼成策略串 `net:<权重>[@<α>][#<T>]`（文法见 `docs/PROTOCOL.md` §8）。

### 3.4 也可以完全没有 `bot.json`

**没有 `bot.json` 的一级子目录**按"老写法"处理：目录里有 `net.bin` 就按**目录名**注册成 `kind=net`，
没有就跳过（WARN）。这是为了**直接指到训练侧的 checkpoint 目录**：
`--bot-ai-dir S:\mahjong-training\ckpt` 里那些 `ppo2-g04/net.bin` 不用搬、不用加清单就能用。

### 3.5 `net.json` 是什么

训练侧 `export.py` 会在权重旁边留一份 `net.json`（特征维度、隐藏层大小等元信息），
**服务端不读它**（权重文件自带魔法数与维度，`Policies.byName` 会校验）。
打包时它会跟着进包，纯粹是"这个权重是怎么来的"的存档。

---

## 4. 名字的规则

名字会进**报文**（`hello_ok.bot_ais[].name` / `room.bot_ai`），所以：

- **必须是可打印 ASCII**（`0x20..0x7E`）——`AGENTS.md` §6.4 的报文纪律，
  e2e 的 ASCII 审计会当场红；中文名字会在客户端显示成乱码。
- **不许含 `,` `@` `#`** —— 这三个在策略串文法里有含义（`--policy a,b,c,d` / `@α` / `#T`），
  放进名字会让日志与命令行分不清"名字"还是"文法"。
- **`teacher` / `first` / `pass` / `random` 是内置锚点**：
  - `kind=net` 的包**不许**占用这些名字（否则"改造前行为"的锚点被偷偷换成一个网络）；
  - `kind=builtin` 且 `policy` **同名**的包**可以**（`{"name":"teacher","policy":"teacher"}`）
    —— 那正是"把启发搜索按同一套格式声明一遍"，好让两种形态共用一套接口。
- 名字重复 = **覆盖**（后面的赢），不报错：`--bot-ai-reg` 就是靠这个覆盖包里的同名项。

---

## 5. 坏包怎么办：**只跳过它一个**

一个半截包（传到一半、权重损坏、清单写错）不该让整个服务起不来，所以包级错误一律
**WARN + 跳过**，其余包照常用：

| 症状（启动日志） | 原因 |
| --- | --- |
| `bot.json 读不出来` | JSON 语法错 / 不是对象 / 编码不是 UTF-8 |
| `找不到权重文件 net.bin` | `kind=net` 但 `model` 指的文件不在 |
| `未知 kind=xxx` | `kind` 只认 `net` / `builtin` |
| `kind=builtin 必须给 policy` | 声明了内置形态却没写 `policy` |
| `名字 xx 与内置策略同名` | `kind=net` 占用了四个锚点名字（见 §4） |
| `跳过机器人 AI 包 xx：机器人 AI 的名字必须是可打印 ASCII` | 目录名/`name` 含非 ASCII |
| `…：net:… 权重文件损坏`（来自 `Policies.byName`） | 魔法数/维度不对 —— 权重与当前代码的特征维度不匹配 |

⚠ **与"点名"不同**：`--bot-ai <名字>`（默认 AI）与 `--bot-ai-reg` 里写错名字/坏权重是
**启动期直接炸**（`IllegalArgumentException`）——那是人显式点的，错了就该当场知道，
而不是"跑起来发现某几桌机器人不动"。

---

## 6. 生成包：`python -m mahjong_ml.packbot`

训练侧的打包器（`python/mahjong_ml/packbot.py`），一条命令把"各代网络 + 启发搜索"打成包：

```powershell
python -m mahjong_ml.packbot --out bot-ai `
    --from-dir S:\mahjong-training\ckpt --include ppo2-g04,ppo-g04,awr-002,bc-003 `
    --builtin teacher,first,pass,random `
    --alpha auto --data S:\mahjong-training\compact\rl-001 `
    --zip release\bot-ai
```

| 参数 | 说明 |
| --- | --- |
| `--out` | 包目录（缺省 `bot-ai`） |
| `--from-dir` + `--include` | checkpoint 根目录 + 要哪几代（`all` = 所有含 `model.pt` 的子目录） |
| `--from-ckpt` | 直接给一个 checkpoint 目录（可重复） |
| `--builtin` | 要打哪些内置（启发搜索）包 |
| `--alpha` | `<数值>` 或 **`auto`**（按让位曲线选"约 95% 决策听老师"的 α，需 `--data`） |
| `--temp` | 采样温度（0 = 贪心） |
| `--zip <目录>` | **同时**把每个包压成独立 zip（`<名字>.zip`，解压到 `bot-ai/` 下即可用） |
| `--force` | 覆盖已存在的包 |

它还会在包目录里放一份 `README.txt`（给部署者的速记，服务端不读它）。
`--alpha auto` 走的是 `hybrid.py` 的**让位曲线**：α 随"训练过的网"的 logit 尺度走，
**别照抄常数**——未训练的网 α=0.25 就 100% 让位（见 `AGENTS.md` §6.5 的 P5b 条）。

⚠ `packbot` 里的名字校验与服务端是**同一把尺子**（ASCII / `,@#` / 内置名），
尺子不一致的话"打包成功但服务端跳过"会变成最难查的那种 bug。

---

## 7. 客户端会看到什么

只有**名字清单**，没有策略串：

```json
{"cmd":"hello_ok", … , "bot_ais":[{"name":"teacher","default":true},{"name":"ppo2-g04","default":false}]}
```

- 客户端建房间时选一个名字 → `create_room.bot_ai`；等待室里房主可改 → `set_bot_ai`。
- `room.bot_ai` 回显这一桌**实际生效**的名字（没指定时 = 服务端默认）。
- ⚠ **清单里故意不发 `spec`**：策略串里是**服务器本机的绝对路径**，
  发出去既没必要（客户端只选名字）、又白送一条服务器目录结构的情报。

**安全口径（不许放宽）**：客户端**只能传注册表里的名字**。
把 `net:<路径>` 交给客户端等于把"读服务器上任意文件"开放出去
（`net:/etc/passwd`、`net:C:\Windows\win.ini` 都会被当权重去读）。
`node tools/bot-ai-test.mjs` 里专门有两条断言打这个（都回 `error{bad_bot_ai}`）。
见 `AGENTS.md` §6.6。

---

## 8. 怎么验证

| 层 | 命令 | 期望 |
| --- | --- | --- |
| L1 | `java -jar server\build\mahjong-server.jar --selftest` | `SELFTEST PASS`（含 `botAiTests` 的清单/坏包/命名冲突用例） |
| L2 | `client\dist\mahjong-client.exe --selftest client\build\st` | `SELFTEST PASS`（「机器人 AI」组：清单来自 `hello_ok.bot_ais`、建房带/不带 `bot_ai`、房主且开局前才可改） |
| L3 | 起服务端后 `node tools\bot-ai-test.mjs 127.0.0.1 10086` | `BOT-AI PASS`（清单 / 建房 / 换一代 / 非房主被拒 / 未知名字与路径串被拒） |
| 打包 | `python -m mahjong_ml.packbot --out bot-ai --builtin teacher --force` | 幂等：包目录里出现 `teacher/bot.json` |
| 文档 | `node tools\doc-refs-check.mjs` | `[doc-refs] PASS` |

**手动冒烟**（30 秒）：在启动目录跑服务端，启动日志里应出现
`机器人 AI：teacher, first, pass, random, …（默认 teacher）`；
客户端建房间的下拉框里能选到新包的名字，用它开一局，机器人确实换了一代的行为。

---

## 9. 随发布包附带的几代

`release/` 里的独立包（每个 zip 解压到 `bot-ai/` 下即可用）：

| 包 | kind | 说明 |
| --- | --- | --- |
| `teacher` / `first` / `pass` / `random` | builtin | 内置锚点（启发搜索与三个对照臂） |
| `bc-003` | net | BC 基线（行为克隆） |
| `awr-002` | net | 离线 RL（AWR）：**比 BC 显著强**（+1.89 顺位点，p<0.001，n=12000） |
| `ppo-g04` | net | 在线 PPO 第 1 轮第 4 代 |
| `ppo2-g04` | net | 在线 PPO 第 2 轮第 4 代 + `alpha: 2`（让位 95.1%）—— **首次显著打赢 teacher**（+2.290，p=0.006，n=5100） |

各代怎么训出来的、显著性怎么算的：`docs/TRAINING.md`。
