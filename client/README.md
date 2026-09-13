# 立直麻将 · Qt6 客户端（Windows）

Qt 6 Widgets + C++17，MinGW 构建。**只负责界面与通信，不做任何规则判定** ——
役种、番数、符数、点数、振听、流局、连庄全部由 Java 服务端裁定。

## 构建

```powershell
cd client
.\build.ps1                 # 配置 + 编译 + 自动把 Qt 运行时拷到 exe 旁边
.\build.ps1 -Clean          # 先清空 build
.\build.ps1 -Deploy         # 额外产出 client\dist 发布目录（只含运行必需文件）
.\build.ps1 -SelfTest       # 编译后跑一次 --selftest
.\build.ps1 -Ninja          # 用 Ninja 生成器（默认 MinGW Makefiles）
.\build.ps1 -NoRuntime      # 跳过 Qt 运行时拷贝（只要 exe 时用）
.\build.ps1 -Plan           # 只打印「找到了什么 / 将要下载什么」，不动手（排障用）
```

### 不必预装 Qt：脚本会自己把 Qt 取回来

**仓库里不放 Qt**（头文件与库是几百 MB 的第三方产物，不该进仓库）。构建脚本按下面顺序解析依赖：

1. `-QtDir <目录>` 显式指定
2. 环境变量 `MAHJONG_QT_DIR` / `QT_ROOT_DIR` / `QTDIR` / `CMAKE_PREFIX_PATH`
3. `PATH` 上的 `qmake` / `qtpaths` 反查
4. 各磁盘的常见安装位置（Qt 官方安装器、aqt、`<盘>\Qt`、`<盘>\Dependencies\Qt` 等）
5. 仓库内缓存 `<仓库根>\.qt\<版本>\mingw_64`
6. **从 `download.qt.io` 在线仓库自动下载**（与 Qt 官方安装器同源；也可用 `-QtRepo` 换镜像）

第 6 步只取本项目真正用到的模块 —— `qtbase`（Core/Gui/Widgets/Network）+ `qtsvg`（Svg），
Qt 6.11.2 下合计约 **22 MB**（整包 `qt-everywhere-src` 是 973 MB，所以「按模块挑归档」很关键），
解包出来就是一个完整的 Qt 前缀（`bin/moc.exe`、`include/`、`lib/cmake/`、`plugins/` 齐全）。
MinGW / CMake / Ninja 缺失时，同样从该仓库取（`qt.tools.*`）。

所以「一台只有 Windows + PowerShell 的机器」也能一条命令构建出来：

```powershell
pwsh -File client\build.ps1 -Deploy     # 没装 Qt 就自动下载；装了就用现成的
```

常用覆盖参数：

| 参数 | 作用 |
| --- | --- |
| `-QtDir <目录>` | 用指定的 Qt（跳过一切探测） |
| `-QtVersion 6.8.3` | 用/下载别的版本（`latest` = 仓库里最新的） |
| `-Provision always` | **无视**系统里装的 Qt，必须用脚本缓存/下载的那份（可复现构建） |
| `-Provision never` | 禁止自动下载；找不到就直接报错 |
| `-QtCacheDir <目录>` | 自动获取的 Qt 放哪（默认 `<仓库根>\.qt`） |
| `-RefreshQt` | 重新解包/下载缓存的 Qt |
| `-Offline` | 完全不出网（只用已有缓存） |
| `-CMakeExe` / `-MingwBin` / `-NinjaDir` | 显式指定构建工具 |

`.qt/` 已经写进 `.gitignore` —— **不要**把它提交进仓库（下一次构建会自动重新取）。

产物：
- `client/build/mahjong-client.exe` —— **自带 Qt 运行时**，双击即可运行
- `client/dist/` —— 发布目录，整个文件夹拷到任何 Windows 机器都能跑

## 关于链接方式：为什么是动态链接，以及如何做到"免安装"

**官方分发的这套 Qt 无法静态链接。** 它的 `mkspecs/qconfig.pri` 里明确写着：

```
QT.global.enabled_features  = ... shared ...
QT.global.disabled_features = static cross_compile ...
QT_CONFIG += shared no-pkg-config ... release
```

即：`static` 是编译 Qt 时就被**禁用**的 feature；`libQt6Core.a`(5.0 MB)、`libQt6Widgets.a`(6.3 MB)
只是 DLL 的**导入库**，不是静态库（真静态 `libQt6Core.a` 通常 30–50 MB）；安装器里也只有
`mingw_64` 一个 shared kit，没有 static kit，也没有装 Qt 源码（`6.11.2\mingw_64\src` 不存在）。
Qt 官方在线安装器不提供 static 包。

因此本项目的做法是**把动态链接做成"绿色版"**：`build.ps1` 每次构建后自动把

```
Qt6Core.dll  Qt6Gui.dll  Qt6Widgets.dll  Qt6Network.dll
libgcc_s_seh-1.dll  libstdc++-6.dll  libwinpthread-1.dll
platforms\qwindows.dll      ← 缺这个会报 "could not find or load the Qt platform plugin windows"
styles\qmodernwindowsstyle.dll
tls\qschannelbackend.dll  tls\qopensslbackend.dll  tls\qcertonlybackend.dll
```

拷到 exe 同级目录（优先用 `windeployqt`，它不可用时脚本自动改为手动拷贝）。
拷完以后：**不需要装 Qt、不需要配 PATH、不需要管理员权限**，双击即运行。

> 已实测：把 PATH 清成 `C:\Windows\system32;C:\Windows`（完全不含 Qt/MinGW），
> `client\build\mahjong-client.exe` 与 `client\dist\mahjong-client.exe` 均能启动 GUI，
> 并跑完一整场东风战。

### 如果你确实需要"单个 exe 静态链接"

必须先从源码编一份 **static Qt**（官方不提供），大致流程：

```powershell
# 1) 取源码（约 1.5 GB，需要网络）
git clone --depth 1 --branch v6.11.2 https://code.qt.io/qt/qt5.git D:\QtStatic\src
cd D:\QtStatic\src
perl init-repository --module-subset=qtbase,qtimageformats

# 2) 用 MinGW 配置为静态（注意 -static 与 -static-runtime）
mkdir D:\QtStatic\build-static && cd D:\QtStatic\build-static
D:\QtStatic\src\configure.bat -static -static-runtime -release -opensource -confirm-license `
    -nomake examples -nomake tests -no-dbus -no-openssl `
    -prefix D:\QtStatic\6.11.2\mingw_static `
    -platform win32-g++

# 3) 编译（单核约 1.5~4 小时，-j 可并行）
<MinGW>\bin\mingw32-make.exe -j8        # 例如 Qt 工具链里的 Tools\mingw1310_64\bin
mingw32-make.exe install
```

然后：

```powershell
cd client
.\build.ps1 -QtDir "D:/QtStatic/6.11.2/mingw_static"
# 并在 CMakeLists 里加： set(CMAKE_EXE_LINKER_FLAGS "-static -static-libgcc -static-libstdc++")
```

注意 static Qt 的许可证约束：想静态链接 Qt 只有三条路 —— ① 仍在 LGPLv3 下额外提供"可重链接"所需的
目标文件（Minimal Corresponding Source）、② **把整个客户端改用 GPLv3**（那就必须开源）、③ 购买商业授权。
若只是想让程序"拷走就能跑"，上面的绿色版（动态链接 + DLL 同目录）已经完全等价，且没有这些约束。

## 运行

| 命令 | 说明 |
| --- | --- |
| `mahjong-client.exe` | 正常启动：大厅 → 输入服务器地址/昵称 → 连接 → 建房或加入 |
| `mahjong-client.exe --selftest [outdir]` | 自检（**401 项**）：牌码/协议单测 + `TableModel` 事件 + 风盘布局不变量 + 自动开关 + 结算面，并输出 PNG |
| `mahjong-client.exe --lobbytest <host> <port>` | UI 回归：连上后检查大厅「建房间」按钮是否可用，并真的点它建房（退出码 0 = 通过） |
| `mahjong-client.exe --autoplay <host> <port> [--name 名] [--timeout 秒]` | 联调自走：真连服务端、自动建房补机器人、打完一场东风战，结果写 `autoplay.log` |
| `mahjong-client.exe --demo <host> <port> [--bots N] [--no-answer] [--shot <png>] [--after 秒]` | 演示：起 GUI 自动进房（`--no-answer` = 建房但不自动应答，截图用），可定时把窗口渲染成 PNG 后退出 |
| `mahjong-client.exe --gentiles <outdir> [字体路径]` | 用字体把 Unicode 麻将牌字形**轮廓化**成 SVG 素材 |
| `mahjong-client.exe --fontprobe <out.png> [字体路径] [轮次]` | 渲染候选输入串，**实测字体的连字语法**（轮次 1=总览 2=组合符放大） |

服务端行为回归（配合 `node tools/timeout-test.mjs <host> <port>`）：
超时不出牌时服务端应代打**摸切**（`tsumogiri` 恒为 true），且连接断开后房间**立即回收**。

> 这些模式都能配合重定向使用：`mahjong-client.exe --lobbytest 127.0.0.1 10086 > out.txt`
> 现在会正确写入文件（已修：GUI 子系统程序不再无条件把 stdout 抢成控制台）。

### UI 回归测试输出示例

```
LOBBYTEST [连接前] 「建房间」enabled=0（预期 0：未连接时置灰）
LOBBYTEST [连接后] 「建房间」enabled=1（必须为 1）
LOBBYTEST 点击「建房间」…
LOBBYTEST PASS: 建房成功，标题 = 立直麻将 · 房间 4Z2F
```

单人试玩：启动客户端 → 连接 → 创建房间 → 「补 3 个机器人」→ 准备，即可开局。

> `--selftest -platform offscreen` 也能退出码 0，但 **Qt 的 offscreen 平台插件在 Windows 上
> 没有字体数据库**，PNG 里的文字会画成空心方框。要看正常牌面，请去掉 `-platform offscreen`。

## 界面

界面布局参考**雀魂 / 天凤**，整体扁平化（纯色填充 + 1px 描边，**牌面单层外框、无厚度层/立体感**，不用渐变、阴影、发光）。

- **方位**：自己永远在下方；`pos = (seat - mySeat + 4) % 4` → `0=下 1=右 2=上 3=左`。
- **手牌**：四家统一牌尺寸；自己/对家横排在上下边缘，上家/下家竖排在左右边缘。
  **手牌区不加任何外框/底衬**。手牌区布局规则（关键在于整行**不抖**）：

  | 区域 | 规则 |
  | --- | --- |
  | 手牌左缘 | 锚点按**满手 13 张**算（与当前实际张数无关），整体再比居中**偏左 1.5 张牌宽** |
  | 手牌对齐 | 手牌在块内居左；**摸牌紧贴手牌右侧**（独立间隔 `0.26 × 牌宽`），副露使手牌变短时摸牌跟着一起往左收 |
  | 副露 | 钉在**各家自己的右下角**（`+u` 行末，四家正好落在屏幕四角）：**最早的在最右、之后依次向左**（从右到左 = 旧→新），整组右对齐；已有的副露不会移动。一副副露**内部**仍是左→右，被鸣那张按来源方位落位 |
  | 避让 | 若「手牌 + 摸牌」整块会压到副露，把**两者一起**左移，绝不重新居中 |

  实测（假服务端脚本化 0/1/2/3 副露，量像素）：手牌左缘**四次完全一致**；
  副露右缘钉在行末；摸牌随手牌变短同步左移。
- **牌河**：贴着中央风盘围成一圈（每行 6 张，向外生长）。
- **横置方向以「牌主视角」判定**（关键规则）：
  - **只有立直宣言牌横置**，后序牌一律不横置；宣言牌若被鸣走，横置**顺延**到该立直者下一张打出的牌（再被鸣走就继续顺延，直到某张没被鸣走）——这段顺延逻辑在服务端，客户端只按 discard.sideways 绘制；
  - 每家在**自己的局部坐标系**里出牌，整块再按座位旋转到屏幕；
  - 因此本家看到：邻家的**非横置牌是横着的**，邻家的**横置牌（立直宣言牌）是竖着的**；
  - 副露里**被鸣的那张同样横置**，位置由**来源方位**决定（上家=最左、对家=中间、下家=最右），
  **与点数顺序无关**：吃上家的 3s（手里 2s、4s）显示成 `3s-2s-4s`，而不是升序的 `2s-3s-4s`。
  这纯粹是**显示顺序**（`meldDisplayTiles()` 只挪摆放），服务端仍按顺子判定，不影响胡牌计算。
- **名牌**沿对角线向内收进「两家牌河之间的拐角空隙」（内移量 `1.9 × 牌高`），
  避开宝牌栏、邻家手牌行与副露带。
- **中央风盘（尺寸由内容反推）**：先按盘内要放的东西（宝牌行 5 张、四家点数、场次、立直棒）与盘外
  要留的河区算出尺寸，再钳制到外圈预算 —— 所以牌河够大、五张指示牌永远落在四家得点框以内。
  盘内自上而下：四边同构的「立直棒带 + 点数带」→ 宝牌指示牌行（只占中间列）→ 中间（局数/本场 → 余牌·供託）。
  当前行动者高亮；得点在盘内，不在名牌上。
- **宝牌指示牌**：在**风盘内、紧贴对家点数下方**（不再占屏幕左上角），一局最多 5 张（宝牌 1 + 四个杠各 1），
  与牌河同量级（`手牌宽 × 0.76`），宽高同缩以保住牌的形状。
- **三个自动开关**（牌桌下方，不在操作栏里）：「自动胡了」「不吃碰杠」「自动摸切」。判据只看服务端下发的
  `ask.options`，**和牌永远优先**（自动摸切绝不切出能胡的牌、不吃碰杠绝不放过荣和），
  每小局结束（`round_end`）自动复位为关。文案在语言文件 `ui.auto.*`。
- **名牌**：半透明，贴在自己手牌旁边（自家左下、对家右上、下家右下、上家左上），
  只显示昵称 / 自风徽标 / 立直标记 —— **得点放在风盘内，不放名牌**。
- **出牌动画**：区分「手切」与「摸切」——
  - **手切**：牌从手牌中（非摸牌）对应的位置飞出，同种牌多次出现时随机取一处；
  - **摸切**：牌从刚摸到的那张位置飞出；
  - 动画期间该张在牌河中暂不绘制，避免"同时出现两张"。
- **鸣牌**：被鸣的那张从牌河**移除**（不是复制一张），直接出现在副露里。
- **牌面素材**：走 **SVG 矢量素材**，可在不改代码的前提下替换美术。

  | 项 | 约定 |
  | --- | --- |
  | 目录 | `client/assets/tiles/`（38 个：37 种牌 + `back.svg`） |
  | 命名 | **文件名 = 牌码**，与协议里的牌字符串一致：`1m`…`9m`、`1p`…`9p`、`1s`…`9s`、`1z`…`7z`、`0m`/`0p`/`0s`（赤五）、`back`（牌背） |
  | 尺寸 | 保持 `viewBox="0 0 300 400"`（3:4；代码按矩形缩放，比例对即可） |
  | 生效方式 | 构建时拷到 exe 同级 `tiles/`；客户端**优先读目录** → **改完 SVG 重启客户端即生效，无需重新编译** |
  | 缺失时 | 逐级回退：目录 → qrc 内嵌 → `TileRenderer` 的程序化绘制（**删掉整个 `tiles/` 也不会白屏**） |
  | 重新生成占位符 | `node tools/gen-tile-placeholders.mjs`（**不覆盖**已存在的文件） |

  占位符就是一张写着牌码的牌形（赤五为红色），见 `docs/images/tiles.png`。

  ![37 种牌](docs/images/tiles.png)

- **牌桌实拍**（真连 Java 服务端的一局）：

  ![对局中](docs/images/table-live.png)

  ![轮到自己摸牌](docs/images/table-turn.png)

> 右侧「分数表 + 聊天」栏是主窗口的附属面板（`MainWindow`），不属于牌桌控件；
> 得点已经放进风盘，这一栏的分数部分是冗余的，需要的话可以去掉。

## 排错：大厅「建房间 / 加入 / 刷新」按钮是灰的、点不动

**已修复（v1.0 修复项）**：`MainWindow::onConnected()` 以前没有调用 `LobbyDialog::setConnected(true)`，
而 `setConnected()` 是唯一会 `setEnabled(true)` 的地方 —— 它此前只在 `showLobby()` 里被调用
（构造时=未连接 → 按钮置灰；断线时 → 再置灰）。结果就是：**TCP 已经连上、握手也成功了，
但三个按钮仍然是灰的，用户点不动**，表现为「无法通过 UI 创建房间」。

修复后连接成功会立即 `setConnected(true)`。回归测试直接点真实按钮：

```powershell
.\dist\mahjong-client.exe --lobbytest 127.0.0.1 10086 > out.txt
# 期望：连接前 enabled=0 → 连接后 enabled=1 → 点击 → PASS
```

若你的版本仍出现按钮置灰，请确认客户端是本次修复后重新编译的（`.\build.ps1 -Deploy`）。

## 排错：`Connection refused`（连接被拒绝）

**连接被拒绝 ≠ 服务端没启动。** 最常见的原因是 **IPv4 回环上没有监听**，而客户端填的是 `127.0.0.1`。

典型场景：**服务端跑在 WSL / 容器里**。WSL2 的 localhost 转发会把 Linux 侧的监听端口
映射到 Windows，但可能**只绑在 IPv6 回环 `[::1]`** 上：

```
TCP    [::1]:10086   LISTENING   ← wslrelay.exe
（没有 0.0.0.0:10086 / 127.0.0.1:10086）
```

此时 `localhost:10086` 通（Windows 把 localhost 优先解析成 `::1`），而 `127.0.0.1:10086` 被拒。

**本项目已经从两侧都做了处理，正常情况下不需要你手工改任何东西：**

1. **客户端自动回退**（`NetClient::connectToServer`）：把主机名解析成**全部**候选地址逐个尝试；
   如果写的是 `127.0.0.1` 会自动补上 `::1`，反之亦然；写 `localhost` 则 IPv4/IPv6 都试。
   连不上时会提示「已尝试 127.0.0.1, ::1」，不会只丢一句 connection refused。
2. **服务端双栈监听**（`Server.bind`）：优先绑定 `[::]` 并关闭 `IPV6_V6ONLY`，
   于是同一个端口在 `0.0.0.0` 与 `[::]` 上同时可连；环境不支持 IPv6 时自动回退纯 IPv4。
   启动日志会打印 `（双栈：IPv4 与 IPv6 都可连）`。

如果仍然连不上，按顺序排查：

```powershell
# 1) 服务端到底在不在？在服务器的 shell 里（WSL 里也一样）
ss -lntp | grep 10086          # Linux
netstat -ano | findstr :10086  # Windows

# 2) 哪一族通？在 Windows 上逐个数
Test-NetConnection 127.0.0.1 -Port 10086
Test-NetConnection ::1       -Port 10086

# 3) 地址填对了吗
#    服务端在本机        → 127.0.0.1 或 localhost
#    服务端在 WSL        → localhost（推荐）；远端访问用 WSL 的 IP，或改用镜像网络
#    服务端在别的机器    → 那台机器的 IP/公网 IP，并放行防火墙与云安全组 TCP 10086
```

> WSL 用户提示：若想让局域网/其它机器也能连到 WSL 里的服务端，
> 在 `%UserProfile%\.wslconfig` 里加 `[wsl2]` + `networkingMode=mirrored`，
> 或直接在 WSL 里执行 `ip addr` 拿到 IP 后连它（记得放行 Windows 防火墙）。

## 目录

```
client/
├─ CMakeLists.txt
├─ build.ps1
├─ assets/
│  ├─ tiles/            牌面矢量素材（38 个 <牌码>.svg，可替换）
│  ├─ tiles.qrc         兜底资源清单
│  ├─ i18n/zh_CN.json   全部界面文案（键 = <族>.<码>）
│  ├─ i18n.qrc          i18n 兜底资源清单
│  └─ fonts/            结算界面字体 I.MahjongJP.otf
└─ src/
   ├─ main.cpp            入口（--selftest / --lobbytest / --autoplay / --demo / --gentiles / --fontprobe / --lang）
   ├─ AutoPlay.{h,cpp}    自走联调模式
   ├─ SelfTest.{h,cpp}    自检（协议单测 + 布局不变量 + 出图）
   ├─ GenTiles.{h,cpp}    用字体轮廓化 SVG 素材
   ├─ FontProbe.{h,cpp}   实测字体连字语法
   ├─ net/                NetClient（QTcpSocket + NDJSON）、Protocol（编解码）
   ├─ i18n/               Lang（语言文件 + t()/code()/yakuText()/tileName()）
   ├─ model/              Tile（牌字符串 ↔ kind/赤）、TableModel（纯数据状态机）、
   │                       AutoPolicy（自动开关的纯判据）
   └─ ui/                 TableLayout（纯几何：布局求解，无 QWidget）、TileRenderer（画牌）、
                          TileFont（结算字体）、TableView（牌桌：绘制/交互/动画）、
                          ActionBar（操作栏）、AutoBar（自动开关）、LobbyDialog（大厅）、
                          ResultDialog（结算）、MainWindow（会话编排 + 主窗口）
```

## 已知小瑕疵

- 断线重连 `rejoin` 已在协议层实现，但 UI 未暴露入口。
- 观战复用牌桌界面（收公开事件、不弹操作），无专门观战 UI。

> 客户端的安全/健壮性加固（服务端数组长度上限、手牌张数上限、HTML 转义、拆包 O(n)、locale 与牌码的
> 路径校验、询问回包的 `type` 校验等）逐条记在 `docs/AUDIT.md`。

## 素材与字体

### 牌局中：SVG 矢量素材

| 项 | 约定 |
| --- | --- |
| 目录 | `client/assets/tiles/`（38 个：37 种牌 + `back.svg`） |
| 命名 | **文件名 = 牌码**（`1m`…`9m`、`1p`…`9p`、`1s`…`9s`、`1z`…`7z`、赤五 `0m`/`0p`/`0s`、`back`） |
| 尺寸 | `viewBox="0 0 300 400"`（3:4，按矩形缩放） |
| 生效 | 构建拷到 exe 同级 `tiles/`，**优先读目录 → 改完重启即生效，无需重新编译** |
| 缺失 | 逐级回退：目录 → qrc → `TileRenderer` 程序化绘制（**删掉整个 `tiles/` 也不白屏**） |

> ⚠ **Illustrator 导出的 SVG 必须先内联 CSS**：Qt 只实现 SVG Tiny，**不认 `<style>` + `class`**，
> 直接放进去会丢光颜色、只剩线稿。跑 `pwsh -File tools\inline-svg-style.ps1 -Dir client\assets\tiles`，
> 或在 Illustrator 导出时选「样式：表现属性」。

### 语言文件（文案表）

- 位置 `client/assets/i18n/<locale>.json`（缺省 `zh_CN.json`，**293 条**：`yaku.*` / `tile.*` / `limit.*` /
  `reason.*` / `error.*` 是服务端发的 ASCII 码的译文，`ui.*` 是界面固定文案）。
- **协议里不带中文**：服务端只发 ASCII 码（役种 `code`、打点 `limit`、流局 `reason`、错误 `code`），
  **界面固定文案也不写在 C++ 里** → 改任何文案都不用重新编译，加语言也只是多一个 json。
- 加载顺序与 `tiles/`、`fonts/` 一样：**exe 同级 `i18n/` → qrc `:/i18n/` → 都没有则显示码本身**
  （删掉整个目录不会白屏，只是文案退回显示协议码）。语言文件是**启动时载入**的，改完要重启客户端。
- 语言用 `--lang <code>` 或环境变量 `DSH_MAHJONG_LANG` 指定，缺省 `zh_CN`。
- 认不出的码**原样显示码**（不显示成 `yaku.xxx` 裸键），一眼能看出"服务端加了码、语言文件没跟上"。
- 校验与搬运工具：
  | 工具 | 用途 |
  | --- | --- |
  | `node tools\check-i18n.mjs` | 服务端词表 ↔ 语言文件：漏一条就失败 |
  | `node tools\i18n-scan.mjs --check` | 源码里还有没有没搬走的中文字面量（`// i18n-keep` 可豁免） |
  | `tools\i18n-map.mjs` / `i18n-apply.mjs` / `i18n-gen.mjs` | 字面量 → key 的映射表，以及"改代码 / 写 json"两个搬运步骤 |

### 结算界面：内嵌牌面字体 + OpenType 连字

- 字体 `client/assets/fonts/I.MahjongJP.otf`（**M+ 字型授权条款**，可自由使用/复制/分发/修改，商业亦可）。
- 只用它的 **`liga`** 连字特性，而 `liga` **默认生效** → **不需要任何 OpenType API 调用**。
- 输入语法（`--fontprobe` 实测）：

  | 形式 | 含义 |
  | --- | --- |
  | `<1-9><m\|p\|s\|z>` | 一张牌（`5z` = 白＝空白牌） |
  | 后缀 `a` | 该牌加**赤标记**（`3ma`/`4za`/`5za` 成立） |
  | `0m`/`0p`/`0s` | **赤五简写**，等同 `5ma`/`5pa`/`5sa` |
  | `-`（**后置**） | **横置前一张** → 副露横置靠它 |
  | `=`（中置） | 相邻两张并成一格（本项目不用） |
  | 空格 | 普通间距，**字宽 ≠ 一个牌位** |

- 结算框的牌面示意是「标签 + 牌面」多行：**手牌 / 宝牌指示牌 / 里宝指示牌**；
  和了牌**不并进手牌**，用固定 44px 间隔**空出一整格**单独摆。
- 字体取不到或串含非法字元 → **整块不显示**，回退到原文字（`一萬二萬…`）。

### 相关工具

| 工具 | 用途 |
| --- | --- |
| `tools/gen-tile-placeholders.mjs` | 生成 38 个占位符 SVG（**不覆盖已有**） |
| `tools/inline-svg-style.ps1` | 把 SVG 的 CSS 内联成表现属性（Qt 必需） |
| `tools/dump-otf-features.mjs` | 列出字体的 OpenType feature tag |
| `--gentiles <outdir>` | 用字体把 Unicode 麻将牌字形**轮廓化**成 SVG |
| `--fontprobe <out.png> [字体] [轮次]` | 渲染候选输入串，**实测**连字语法 |

完整说明（含控制字符编码、授权、踩坑）见仓库根 `AGENTS.md` §9。

## 许可与第三方声明

客户端以 **LGPLv3** 使用 Qt 6，且是**动态链接**：Qt 的 DLL 与插件是 exe 同级的独立文件、**可被替换** ——
这正是 LGPL 允许闭源使用的形态（本项目自身源码无须按 LGPLv3 开放）。分发时必须随附许可文本，
`client/licenses/` 已备好：

| 文件 | 内容 |
| --- | --- |
| `Qt-LICENSE.txt` | Qt 官方 LICENSE：**LGPLv3 全文 + GPLv3 全文**（LGPLv3 是 GPLv3 的补充条款，须一并提供） |
| `Qt-Copyright.txt` | Qt 的第三方组件版权声明 |
| `NOTICE.txt` | 本项目声明：动态链接与可替换性、Qt 源码获取地址、**未使用**任何 GPL-only 模块、MinGW 运行时与字体许可 |

`build.ps1 -Deploy` 会把整个 `licenses/` 拷进 `client/dist/licenses/`（与 `tiles/`、`fonts/`、`i18n/`
同一套逻辑，且同样放在「已就绪就跳过」判断**之前**，保证旧的 dist 目录也能补齐）。
**分发 `dist/` 时不要漏掉它。** 完整的义务清单、判定依据与维护规则见 `docs/THIRD-PARTY.md`。
