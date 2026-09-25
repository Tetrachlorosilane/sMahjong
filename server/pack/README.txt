立直麻将服务端（sMahjong server）
====================================

这是一个**开箱即跑**的服务端包：只要目标机有 JDK 17+（`java -version` 能跑），
解压后 `./start.sh` 就能开局。规则引擎、牌局推进、结算、对局记录全在 jar 里，
不需要 Maven/Gradle，也不需要源码。

一、最快路径
------------

    ./start.sh            # 后台启动（监听 0.0.0.0:10086，日志在 logs/）
    ./status.sh           # 看进程 / 端口 / 版本 / 日志尾部
    ./stop.sh             # 停止（先 SIGTERM，最多等 20s 再 SIGKILL）
    ./restart.sh          # 重启
    ./update.sh           # 从 GitHub Release 拉最新包，校验 sha256 后原地替换（在跑就自动重启）

客户端连 `服务器IP:10086` 即可（本机就是 `localhost:10086`）。
换端口：`PORT=9000 ./start.sh`；只监听本机：`HOST=127.0.0.1 ./start.sh`。
其它开关（如 `--fast`、`--no-replay`）直接跟在后面，原样透传给 jar：
`./start.sh --fast --no-replay`。

二、目录里有什么
----------------

    mahjong-server.jar   服务端本体（唯一权威方：洗牌/判定/结算/记录）
    VERSION              版本号（update.sh 用它比对）
    start.sh             后台启动（PID 写 run/，日志写 logs/）
    stop.sh              停止（幂等；--force 直接 SIGKILL）
    restart.sh           重启（stop + start）
    status.sh            状态一览（只读）
    update.sh            自动更新（--check 只看不装；--tag 指定版本；--force 重装）
    DEPLOY.md            部署细节：防火墙、systemd、WSL 端口转发、思考时间等
    logs/  run/          运行期产生（日志 / PID / 备份），升级时会保留
    replays/             对局记录（服务端写，回放界面读）
    players/             玩家档案（uuid → 昵称/登录时间），升级时会保留，见下
    bot-ai/              机器人 AI 包（可选，自己放进来；服务端**启动时自动挂载**，见四）

三、身份与更新
--------------

**玩家身份（uuid）**：客户端第一次连上会拿到一个 uuid（存在它自己的 `settings.json` 里），
服务端按 uuid 记一份档案（昵称、首次/最近登录时间、登录次数）。同一个 uuid = 同一个玩家：
牌局中掉线的人只要用同一个 uuid 连回来，就会**接回原座位**（牌局不重开、手牌不重发）。
超过 60 天没登录的档案会被自动清理。`players/` 是**要备份**的目录（里面有玩家身份），
它和 `replays/` 一样在升级时原样保留。相关开关（跟在 `./start.sh` 后面透传）：

    --player-dir <dir>     档案目录（默认 players）
    --uuid-ttl-days <n>    多久没登录就清理（默认 60 天）
    --no-player-store      不落盘（uuid 握手照常，只是服务端不记得人）

**更新**：

    ./update.sh --check   # 只查有没有新版（不动任何东西）
    ./update.sh           # 装最新版；本来在跑就自动重启
    ./update.sh --tag v1.8.0 --force   # 指定版本 / 强制重装

判断"有没有新版"不只看版本号：**同一个 tag 下原地重发的资产**（本仓库修 bug 时
会这么做）也会被认出来 —— 比对的是 release 资产的 sha256。旧 jar 会备份到
`run/backup/`，回滚就是把备份拷回去再 `./restart.sh`。

四、机器人用哪一代 AI（bot-ai/）
--------------------------------

电脑玩家默认是**内置牌效 AI**。发布页另外给了几代**训练出来的网络**，按代一个 zip：

    sMahjong-bot-ai-v1.10.2.zip  ← 一包全给（解压即得 bot-ai/，推荐）
    teacher.zip first.zip pass.zip random.zip         内置启发搜索 / 三个对照臂
    bc-003.zip awr-002.zip ppo-g04.zip ppo2-g04.zip   训练网络（各约 1 MB）

装法：把 zip 解压到**这个目录**（`mahjong-server.jar` 与 `start.sh` 所在处，
即 `replays/`、`players/` 的同层），然后 `./restart.sh`。启动日志里会看到：

    [INFO] 挂载机器人 AI 包 `ppo2-g04`（kind=net，…）
    [INFO] 机器人 AI：teacher, first, pass, random, awr-002, bc-003, ppo-g04, ppo2-g04（默认 teacher）

之后客户端**建房间**时的「机器人 AI」下拉框里就能选一代（房主在等待室里还能改，
**开局后不可改**）。不想给玩家选就把 `bot-ai/` 整个删掉，回到内置牌效。

一个包 = 一个目录 + 里面的 `bot.json`（唯一接口），两种形态同一套格式：

    深度模型：  bot-ai/<名字>/bot.json   {"kind":"net","model":"net.bin","alpha":2}
                bot-ai/<名字>/net.bin    权重
    启发搜索：  bot-ai/<名字>/bot.json   {"kind":"builtin","policy":"teacher"}

要点：名字必须是**可打印 ASCII**；`teacher/first/pass/random` 是内置锚点，深度模型包不许占用；
坏包（清单读不出 / 权重损坏 / 名字撞内置）只跳过它一个，其余照常用（日志里有 WARN）。
也可以直接用训练侧的 checkpoint 目录（一层子目录一个 `net.bin`）：`./start.sh --bot-ai-dir /path/ckpt`。
完整格式见仓库 `docs/BOT-AI.md`。⚠ 客户端只发**名字**，`net:<路径>` 这类串一律被拒
（否则等于让房间读服务器任意文件）。

五、常见问题
------------

* `./start.sh` 报权限不足 → `chmod +x *.sh`（zip 正常保留了可执行位，从别处拷进来可能丢）。
* 客户端连不上 → `./status.sh` 看端口；云主机还要放行安全组/防火墙（见 DEPLOY.md）。
* 想开机自启 / 交给 systemd 管 → 见 DEPLOY.md 的 systemd 单元示例（
  `systemctl start mahjong` 那套；用 systemd 时就不必再用 start.sh 常驻了）。
* 换了机器或重装了服务端之后"玩家身份没了" → 看看 `players/` 有没有跟过来（见上）。
* 升级之后机器人那几代不见了 → `bot-ai/` 不会被 `./update.sh` 动（它只换 jar 与脚本），
  真丢了就把上面的 zip 重新解压一次。

