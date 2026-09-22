# 立直麻将（日本麻将）联网对战 —— 服务端部署（Ubuntu）

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Ubuntu 20.04 / 22.04 / 24.04（任何 Linux 均可） |
| JDK | **17 或更高**（推荐 21）。跑发布包只要 `java`；自己构建才需要 `javac` / `jar` |
| 第三方依赖 | **无**。不需要 Maven、Gradle、数据库、Redis |

安装 JDK：

```bash
sudo apt update
sudo apt install -y openjdk-21-jdk-headless     # 只跑服务端可装 openjdk-21-jre-headless
java -version
```

> **JDK 装在哪都行。** 发布包里的脚本与仓库里的 `build.sh` / `run.sh` 都不写死路径，
> 按 `JDK_HOME` → `JAVA_HOME` → `PATH` → `/usr/lib/jvm`、`~/.sdkman`、`/opt/java*` 依次查找，
> 找不到才报错。想指定就用 `JDK_HOME=/path/to/jdk ./build.sh`。

## 2. 拿到服务端：发布包（推荐）或源码构建

### 2.1 发布包（开箱即跑，不需要源码）

从 [Releases](https://github.com/Tetrachlorosilane/sMahjong/releases) 下载
`sMahjong-server-v<版本>.zip`，解压后是**一层版本目录**：

```
sMahjong-server-v1.8.0/
├── mahjong-server.jar    服务端本体（唯一权威方）
├── VERSION               版本号
├── start.sh              后台启动（PID → run/，日志 → logs/）
├── stop.sh               停止（SIGTERM → 最多等 20s → SIGKILL）
├── restart.sh            重启
├── status.sh             状态一览（进程/端口/版本/日志尾部）
├── update.sh             自动获取并安装更新（见 §4）
├── DEPLOY.md             本文件
└── README.txt            一页速览
```

```bash
unzip sMahjong-server-v1.8.0.zip
cd sMahjong-server-v1.8.0
./start.sh                 # 后台启动，监听 0.0.0.0:10086
./status.sh                # 确认在跑
```

> zip **保留了可执行位**（`-rwxr-xr-x`）。如果是从别处拷进来丢了权限：`chmod +x *.sh`。
> 发布包**不再包含 `build.sh`** —— 包里没有源码，那个脚本本来也跑不起来（旧结构的坑）。

### 2.2 从源码构建

```bash
git clone https://github.com/Tetrachlorosilane/sMahjong.git
cd sMahjong/server
./build.sh                 # → build/mahjong-server.jar
./build.sh --selftest      # 编译完顺带跑一次规则引擎自检
```

常用参数（都可选）：`JDK_HOME=/opt/jdk-21 ./build.sh`、`MJ_RELEASE=21 ./build.sh`（默认 `--release 17`，
用 JDK 21 构建的 jar 也能跑在 JDK 17 上）、`MJ_OUT=dist ./build.sh`。
Windows 上同样可以：`pwsh -File server/build.ps1`。

## 3. 运行

### 3.1 后台脚本（发布包）/ 前台运行（源码）

```bash
./start.sh                      # 后台：0.0.0.0:10086，日志 logs/mahjong-server-YYYYMMDD.log
PORT=9000 ./start.sh            # 换端口
HOST=127.0.0.1 ./start.sh       # 只监听本机
./start.sh --fast --no-replay   # 其它参数原样透传给 jar（见 §3.4）
JAVA_OPTS="-Xmx512m" ./start.sh # 额外 JVM 参数
./stop.sh                       # 停止（--force 直接 SIGKILL）
./restart.sh                    # 重启
./status.sh                     # 状态：进程 / 端口 / 版本 / 日志尾部
```

源码目录里则是前台跑：`./run.sh`（同样支持 `PORT=` / `HOST=`，`java` 也是自动查找的）。

启动成功会打印一行 `LISTENING 0.0.0.0:10086`（默认**双栈监听**，IPv4/IPv6 都能连）。

自测（不需要网络，验证规则引擎）：

```bash
java -jar mahjong-server.jar --selftest
# 期望输出：通过 N 项，失败 0 项 / SELFTEST PASS
```

### 3.2 运行期目录

| 路径 | 内容 | 升级时 |
| --- | --- | --- |
| `run/mahjong-server.pid` | 进程号（`stop.sh` / `status.sh` 用它） | 保留 |
| `run/installed.sha256` · `run/installed.tag` | 当前安装的包摘要与 tag（`update.sh` 用它判断"同 tag 重发"） | 保留 |
| `run/backup/mahjong-server-<旧版本>.jar` | 升级前的 jar（回滚用） | 保留 |
| `logs/` | 每次启动追加一个按日期的日志 | 保留 |
| `replays/` | 对局记录（回放用，见 §3.3） | 保留 |
| `players/players.json` | **玩家档案**（uuid → 昵称 / 首次与最近登录时间 / 登录次数，见 §3.4） | 保留（**要备份**） |

### 3.3 对局记录（回放）的磁盘占用

服务端默认把每一场半庄记下来并落盘到 **`./replays/`**（相对启动目录），
每场约 0.3~1 MB（东风战实测 384 KB）。容量是**双上限**，超出会**淘汰最旧的并删文件**：

```bash
--replay-dir /var/lib/mahjong/replays   # 换目录（systemd 里建议给绝对路径，见 §6）
--replay-max 200                        # 最多留多少场（默认 50）
--replay-max-mb 512                     # 记录总字节上限（默认 96）
--no-replay                             # 完全不记录（既省磁盘也省内存）
```

⚠ 要点：

- 目录要**可写**（systemd 里若开了 `ProtectSystem=strict` / `ReadWritePaths`，把它加进可写路径）。
- 回放是**上帝视角**（四家手牌 + 整副牌山），只在**牌局结束后**可读；读取接口只读，
  ID 是随机 10 位字符串。**别把 replays 目录暴露成静态站点** —— 要分享就把 ID 给对方。
- 想保留更久就调大 `--replay-max` / `--replay-max-mb`；两者谁先到按谁淘汰。

### 3.4 玩家档案（身份 / uuid）

服务端默认把玩家身份落在 **`./players/players.json`**（相对启动目录；**一份 JSON**，原子写）：

```bash
--player-dir /var/lib/mahjong/players   # 换目录（systemd 里同样要给绝对路径）
--uuid-ttl-days 60                      # 多久没登录就清理（默认 60 天 = 2 个月）
--no-player-store                       # 不落盘（uuid 握手照常，只是服务端不记得人）
```

- 客户端第一次连上会拿到一个 uuid（存在它自己的 `settings.json` 里），之后**同一个 uuid = 同一个玩家**：
  牌局中掉线重连会**接回原座位**。**昵称仍然按昵称显示**，uuid 只在握手时出现。
- **这个目录要备份**：它是身份的唯一副本（丢了不影响对局，但老玩家会变成"新玩家"、
  掉线回来也接不回座位）。`update.sh` 升级时**原样保留** `players/`。
- 清理是**自动的**：启动时一次，之后每 6 小时一次（判据是"上次登录距今 > TTL"）。
  `--uuid-ttl-days 0` 表示只保留本进程登录过的（调试用）。

### 3.5 全部命令行开关

```bash
java -jar mahjong-server.jar --help        # 权威清单（本节只是摘要）
--host 0.0.0.0 --port 10086                # 监听地址/端口
--fast                                     # 缩短机器人思考与局间停顿（压测用）
--no-replay                                # 不写对局记录
--player-dir players --uuid-ttl-days 60    # 玩家档案（身份）目录与保留期，见 §3.4
--no-player-store                          # 不落盘玩家档案
--selftest                                 # 规则引擎自检后退出
--selfplay N --workers K --policy a,b,c,d  # 自对弈 / 评测（训练接口，见 PROTOCOL §8）
```

## 4. 自动更新（update.sh）

```bash
./update.sh --check     # 只查有没有新内容（不动进程、不下载）
./update.sh             # 装最新；本来在跑就自动重启
./update.sh --tag v1.8.0 --force    # 指定版本 / 强制重装
./update.sh --no-restart            # 只装，不重启
REPO=you/sMahjong ./update.sh        # 换仓库（自建 fork）
```

它做的事：查 GitHub Release → 比"版本 + **资产 sha256**"→ 下载 → 校验摘要（可选 `--sha256` 兜底）
→ `unzip -t` 完整性 → 解包 → 备份旧 jar → 替换 jar/脚本/文档（保留 `logs/ run/ replays/ players/`）→ 重启。

⚠ **为什么不能只比版本号**：本仓库修 bug 时**不换版本号、原地重发同名资产**
（`v1.8.0` 就这样重发过两次）。所以 `update.sh` 还比 release 资产的`digest`，
同一 tag 下资产变了也会装（`--check` 会显示"同一个 tag 下的资产变了"）。

回滚：

```bash
cp run/backup/mahjong-server-<旧版本>.jar mahjong-server.jar
./restart.sh
```

## 5. 开放端口

```bash
sudo ufw allow 10086/tcp
# 云厂商（阿里云/腾讯云/AWS）还要在安全组放行 TCP 10086
ss -lntp | grep 10086        # 或 ./status.sh
```

## 6. 用 systemd 常驻

想开机自启 / 交给 systemd 管（推荐生产环境；用了它就不必再 `./start.sh` 常驻）：

`/etc/systemd/system/mahjong.service`：

```ini
[Unit]
Description=Riichi Mahjong Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mahjong
WorkingDirectory=/opt/mahjong
Environment=JAVA_TOOL_OPTIONS=-Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8
ExecStart=/usr/bin/java -jar /opt/mahjong/mahjong-server.jar --host 0.0.0.0 --port 10086 \
          --replay-dir /var/lib/mahjong/replays --player-dir /var/lib/mahjong/players
# ⚠ 用绝对路径（不是工作目录下的 replays/ players/）：开 ProtectSystem 时只放行这两处，
#   玩家档案与对局记录才不会因为目录不可写而静默丢（服务端只记日志、不会因此崩）
StateDirectory=mahjong
Restart=always
RestartSec=3
StandardOutput=append:/var/log/mahjong/server.log
StandardError=append:/var/log/mahjong/server.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin mahjong
sudo mkdir -p /var/log/mahjong /var/lib/mahjong/replays /var/lib/mahjong/players
sudo chown -R mahjong /var/log/mahjong /var/lib/mahjong
sudo systemctl daemon-reload
sudo systemctl enable --now mahjong
sudo systemctl status mahjong
sudo journalctl -u mahjong -f
```

> `update.sh` 会先 `./stop.sh`（看 PID 文件）。**systemd 管理的进程不在 PID 文件里**，
> 所以 systemd 场景请这样更新：
> ```bash
> sudo systemctl stop mahjong
> sudo -u mahjong ./update.sh --no-restart
> sudo systemctl start mahjong
> ```

## 7. 客户端连接

Windows 上运行 `mahjong-client.exe`（构建方式见 `client/README.md` —— **不需要预先装 Qt**），
服务器地址填 Ubuntu 机器的**公网 IP 或内网 IP**，端口 `10086`，点「连接」→「创建房间」→
勾选补 3 个机器人（单人也能玩）→ 准备。

### 7.1 服务端跑在 WSL 里时（本机开发最常见）

默认双栈监听（日志会打印 `（双栈：IPv4 与 IPv6 都可连）`），`127.0.0.1` 与 `localhost` 都能连。
若环境只支持 IPv4，WSL2 的 localhost 转发可能只绑在 `[::1]`，此时 `127.0.0.1:10086` 会
`Connection refused` 而 `localhost` 能通。排查：

```powershell
netstat -ano | findstr :10086      # 看是 0.0.0.0 / 127.0.0.1 / [::1] 哪个在监听
Test-NetConnection 127.0.0.1 -Port 10086
Test-NetConnection ::1       -Port 10086
ss -lntp | grep 10086              # 在 WSL 里确认服务端确实在监听
```

客户端的 Qt 程序**会自动在 IPv4/IPv6 之间回退**；WSL 场景建议直接填 `localhost`。
想让同局域网其它机器访问 WSL 里的服务端：`%UserProfile%\.wslconfig` 里
`[wsl2] networkingMode=mirrored` 后 `wsl --shutdown`，或在 Windows 防火墙放行
`wslrelay` / `vEthernet (WSL)` 的 TCP 10086。

## 8. 协议与扩容

- 协议见 `docs/PROTOCOL.md`。服务端是纯 TCP NDJSON，一条连接一个玩家。
- 一个进程可承载多个房间；每个房间一个线程，房间之间完全独立。
- 单机压测参考：4 个机器人 + 1 个人类的一桌约占用 1 个 CPU 核的百分之几。
- 多机部署可在前面放 nginx `stream` 做 TCP 负载均衡：

```nginx
stream {
    upstream mahjong { server 127.0.0.1:10086; }
    server { listen 10086; proxy_pass mahjong; }
}
```

## 9. 常见问题

| 现象 | 处理 |
| --- | --- |
| `./start.sh: Permission denied` | `chmod +x *.sh`（zip 本身保留了 `0755`，从别处拷贝可能丢） |
| `javac: command not found` | 只在**自己构建**时需要：`sudo apt install openjdk-21-jdk-headless` |
| 找不到 java | `sudo apt install -y openjdk-21-jre-headless`，或设 `JAVA_HOME` |
| 客户端连不上 | `./status.sh` 或 `ss -lntp \| grep 10086`；检查 ufw / 云安全组 |
| 客户端连上后马上断开 | 服务端日志有原因；确认客户端发的是 `{"cmd":"hello",...}` 且以 `\n` 结尾 |
| 中文日志乱码 | 脚本已带 `-Dstdout.encoding=UTF-8`；自己起 java 时手动加上 |
| `update.sh` 说"已是最新"但确实修了 bug | 加 `--force`，或先 `./update.sh --check` 看资产摘要是否变了 |
| 更新后行为没变 | `./status.sh` 看进程启动时间；systemd 场景别忘了 `systemctl restart mahjong` |
| 老玩家回来变成"新玩家" / 掉线重连接不回座位 | 看 `players/players.json` 在不在、可不可写（§3.4）；换目录/换机器时把这个目录一起搬过去 |
| 玩家档案一直不落盘 | 落盘有 **2 秒节流**（登录太密集时合并写），正常；退出/清理时都会补写。看日志有没有"落盘失败" |
| 日志里出现「玩家档案过期清理」 | 正常：超过 `--uuid-ttl-days`（默认 60 天）没登录的记录被清掉了 |
| 想改规则 | 建房间时客户端可传 `rules` 对象（见 `docs/PROTOCOL.md` §5），服务端逐项校验 |
