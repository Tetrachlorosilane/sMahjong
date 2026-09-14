# 立直麻将（日本麻将）联网对战 —— 服务端部署（Ubuntu）

## 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Ubuntu 20.04 / 22.04 / 24.04（任何 Linux 均可） |
| JDK | **17 或更高**（推荐 21）。只用到 `javac` / `jar` / `java` |
| 第三方依赖 | **无**。不需要 Maven、Gradle、数据库、Redis |

安装 JDK：

```bash
sudo apt update
sudo apt install -y openjdk-21-jdk-headless
java -version
```

> **JDK 装在哪都行。** `build.sh` / `run.sh` 不写死路径，按
> `JDK_HOME` → `JAVA_HOME` → `PATH` → `/usr/lib/jvm`、`~/.sdkman`、`/opt/java*`、
> `/Library/Java/...` 依次查找 `javac`（取版本最高的一个，要求 ≥ 17），
> 找不到才报错。想指定就用 `JDK_HOME=/path/to/jdk ./build.sh`。

## 2. 构建

把 `server/` 目录上传到服务器，例如 `/opt/mahjong/server`：

```bash
cd /opt/mahjong/server
chmod +x build.sh run.sh
./build.sh
```

产物：`build/mahjong-server.jar`（单文件，约 120 KB）。

常用参数（都是可选的）：

```bash
./build.sh --selftest        # 编译完顺带跑一次规则引擎自检
JDK_HOME=/opt/jdk-21 ./build.sh
MJ_RELEASE=21 ./build.sh     # 改用 --release 21 编译
MJ_OUT=dist ./build.sh       # 换产出目录
```

> **默认 `--release 17`**：用 JDK 21 构建出来的 jar 也能跑在 JDK 17 上，
> 产物不再取决于构建机的 JDK 版本。

> 也可以直接在 Windows 上构建：`pwsh -File server/build.ps1`（同样自动找 JDK），
> 产出的 jar 同样能跑在 Ubuntu 上（纯 Java 字节码，没有平台相关内容）。

## 3. 运行

```bash
# 前台运行，监听 0.0.0.0:10086
./run.sh

# 自定义端口
PORT=9000 ./run.sh

# 或直接
java -jar build/mahjong-server.jar --host 0.0.0.0 --port 10086 --verbose
```

`run.sh` 里的 `java` 同样是自动查找的（`JAVA_HOME` → `PATH` → `/usr/lib/jvm` …），
只装了 JRE 也能跑。

启动成功会打印一行：

```
LISTENING 0.0.0.0:10086
```

自测（不需要网络，验证规则引擎）：

```bash
java -jar build/mahjong-server.jar --selftest
# 期望输出：通过 N 项，失败 0 项 / SELFTEST PASS
```

### 3.1 对局记录（回放）的磁盘占用

服务端默认把每一场半庄记下来并落盘到 **`./replays/`**（相对启动目录），
每场约 0.3~1 MB（东风战实测 384 KB）。容量是**双上限**，超出会**淘汰最旧的并删文件**，
所以磁盘占用有硬上限、不会无限增长：

```bash
--replay-dir /var/lib/mahjong/replays   # 换目录（systemd 里建议给绝对路径，见 §5）
--replay-max 200                        # 最多留多少场（默认 50）
--replay-max-mb 512                     # 记录总字节上限（默认 96）
--no-replay                             # 完全不记录（既省磁盘也省内存）
```

⚠ 要点：

- 目录要**可写**（systemd 里若开了 `ProtectSystem=strict` / `ReadWritePaths`，把它加进可写路径）。
- 回放是**上帝视角**（四家手牌 + 整副牌山），所以记录只在**牌局结束后**可读；
  读取接口是只读的，且 ID 是随机生成的 10 位字符串。**别把 replays 目录暴露成静态站点** ——
  要分享某一场，把 ID 给对方，让他用客户端的大厅「对局回放」拉。
- 想保留更久就把 `--replay-max` / `--replay-max-mb` 调大；两者谁先到就按谁淘汰。

## 4. 开放端口

```bash
# 如果开了 ufw
sudo ufw allow 10086/tcp

# 如果在云厂商（阿里云/腾讯云/AWS）上，还要在安全组放行 TCP 10086
```

验证端口：

```bash
ss -lntp | grep 10086
```

## 5. 用 systemd 常驻

`/etc/systemd/system/mahjong.service`：

```ini
[Unit]
Description=Riichi Mahjong Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=mahjong
WorkingDirectory=/opt/mahjong/server
Environment=PORT=10086
Environment=HOST=0.0.0.0
Environment=JAVA_TOOL_OPTIONS=-Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8
ExecStart=/usr/bin/java -jar /opt/mahjong/server/build/mahjong-server.jar --host 0.0.0.0 --port 10086
Restart=always
RestartSec=3
StandardOutput=append:/var/log/mahjong/server.log
StandardError=append:/var/log/mahjong/server.log

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin mahjong
sudo mkdir -p /var/log/mahjong && sudo chown mahjong /var/log/mahjong
sudo systemctl daemon-reload
sudo systemctl enable --now mahjong
sudo systemctl status mahjong
sudo journalctl -u mahjong -f
```

## 6. 客户端连接

Windows 上运行 `mahjong-client.exe`（构建方式见 `client/README.md` —— **不需要预先装 Qt**，
构建脚本会自己把 Qt 取回来），服务器地址填 Ubuntu 机器的 **公网 IP 或内网 IP**，
端口 `10086`，点「连接」→「创建房间」→ 勾选补 3 个机器人（单人也能玩）→ 准备。

### 6.1 服务端跑在 WSL 里时（本机开发最常见）

服务端默认**双栈监听**（启动日志会打印 `（双栈：IPv4 与 IPv6 都可连）`），
所以 `127.0.0.1` 和 `localhost` 都能连。

如果没有双栈（旧版本 / 环境只支持 IPv4），WSL2 的 localhost 转发可能**只把端口绑在
IPv6 回环 `[::1]`** 上，此时 `127.0.0.1:10086` 会 `Connection refused`，而 `localhost` 能通。

排查：

```powershell
netstat -ano | findstr :10086      # 看是 0.0.0.0 / 127.0.0.1 / [::1] 哪个在监听
Test-NetConnection 127.0.0.1 -Port 10086
Test-NetConnection ::1       -Port 10086
ss -lntp | grep 10086              # 在 WSL 里确认服务端确实在监听
```

客户端的 Qt 程序**会自动在 IPv4/IPv6 之间回退**，所以填 `127.0.0.1` 也能连上只绑了 `::1`
的服务端；但为了少踩坑，WSL 场景建议直接填 `localhost`。

想让 WSL 里的服务端被同局域网其它机器访问：

```ini
# %UserProfile%\.wslconfig
[wsl2]
networkingMode=mirrored
```

改完 `wsl --shutdown` 重启；或在 WSL 里 `ip addr` 取 IP 后连它，并在 Windows 防火墙
放行 `wslrelay`/`vEthernet (WSL)` 的 TCP 10086。

## 7. 协议与扩容

- 协议见 `docs/PROTOCOL.md`。服务端是纯 TCP NDJSON，一条连接一个玩家。
- 一个进程可承载多个房间；每个房间一个线程，房间之间完全独立。
- 单机压测参考：4 个机器人 + 1 个人类的一桌约占用 1 个 CPU 核的百分之几。
- 如果想要多机部署，可在前面放 nginx `stream` 做 TCP 负载均衡：

```nginx
stream {
    upstream mahjong { server 127.0.0.1:10086; }
    server { listen 10086; proxy_pass mahjong; }
}
```

## 8. 常见问题

| 现象 | 处理 |
| --- | --- |
| `javac: command not found` | 装 JDK 而不是 JRE：`sudo apt install openjdk-21-jdk-headless` |
| 客户端连不上 | `ss -lntp \| grep 10086` 确认监听；检查 ufw / 云安全组 |
| 客户端连上后马上断开 | 服务端日志会有原因；确认客户端发的是 `{"cmd":"hello",...}` 且以 `\n` 结尾 |
| 中文日志乱码 | 加 `-Dstdout.encoding=UTF-8`，或把日志重定向到文件 |
| 想改规则 | 建房间时客户端可传 `rules` 对象（见 `docs/PROTOCOL.md` §5），服务端逐项校验 |
