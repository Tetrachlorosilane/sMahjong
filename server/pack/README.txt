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

四、常见问题
------------

* `./start.sh` 报权限不足 → `chmod +x *.sh`（zip 正常保留了可执行位，从别处拷进来可能丢）。
* 客户端连不上 → `./status.sh` 看端口；云主机还要放行安全组/防火墙（见 DEPLOY.md）。
* 想开机自启 / 交给 systemd 管 → 见 DEPLOY.md 的 systemd 单元示例（
  `systemctl start mahjong` 那套；用 systemd 时就不必再用 start.sh 常驻了）。
* 换了机器或重装了服务端之后"玩家身份没了" → 看看 `players/` 有没有跟过来（见上）。

