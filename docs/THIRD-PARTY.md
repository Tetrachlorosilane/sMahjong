# 第三方组件与许可（THIRD-PARTY）

本文件说明**本项目使用了哪些第三方组件、各自适用哪种许可、以及分发时必须履行的义务**。
面向要分发客户端（把 `client/dist/` 整个拷给别人）或要把代码用于其它用途的人。

> 一句话结论：**客户端以 LGPLv3 使用 Qt 6，且是「动态链接 + DLL 随 exe 同目录」的用法** ——
> 这是 LGPL 下最省事的形态：**本项目自身源码无须开放**，代价是必须随附许可文本、
> 不得阻止用户替换 Qt 库、并能提供 Qt 的对应源码。

---

## 1. 实测清单：客户端到底链接/分发了什么

`client/dist/`（`build.ps1 -Deploy` 的产物）里与第三方有关的文件：

| 文件 | 来源 | 许可 |
| --- | --- | --- |
| `Qt6Core.dll` `Qt6Gui.dll` `Qt6Widgets.dll` `Qt6Network.dll` `Qt6Svg.dll` `Qt6Multimedia.dll` | Qt 6.11.2 (mingw_64) | **LGPLv3**（或 GPLv3 / 商业，三选一） |
| `platforms\qwindows.dll` `styles\qmodernwindowsstyle.dll` `tls\*.dll`、`multimedia\*.dll` | Qt 插件（qwindows/qmodernwindowsstyle/tls 属 Qt Base，multimedia 属 Qt Multimedia） | **LGPLv3** |
| `ffmpeg\` 或 exe 同级的 `avcodec-*.dll` `avformat-*.dll` `avutil-*.dll` `swresample-*.dll` `swscale-*.dll` | FFmpeg（**由 Qt 官方二进制包捆绑**，Qt Multimedia 的媒体后端） | FFmpeg 自身是 **LGPLv2.1+（默认构建）**；Qt 捆绑的那份**具体配置未在上游文档中给出确定性说明**，故分发时应把 Qt 安装目录 `Licenses/` 的文本一并带上（见 §3 第 ② 条） |
| `libgcc_s_seh-1.dll` `libstdc++-6.dll` `libwinpthread-1.dll` | MinGW 13.1 运行时 | **GPLv3 + GCC Runtime Library Exception** |
| `fonts\I.MahjongJP.otf` | 结算界面牌面字体 | **M+ 字型授权条款** |
| `tiles\*.svg` | 牌面矢量素材 | 本项目自制 / 或由上述字体轮廓化而来（后者同样受 M+ 约束） |
| `sfx\*.wav` | 音效（吃/碰/杠/立直/自摸/荣和/提示/摸牌） | 本项目自制（**由仓库内脚本 `tools/gen-sfx.mjs` 离线合成**，不含任何第三方采样） |
| `i18n\zh_CN.json` | 界面文案 | 本项目自制 |
| `licenses\`（含 `licenses\qt\`） | 许可文本与声明 | —— |

服务端（`server/`）是纯 JDK 标准库字节码，**不随客户端分发任何第三方库**；用 JDK 运行本身不产生分发义务
（若将来捆绑 JRE，则是 GPLv2 + Classpath Exception，同样允许闭源程序随附）。

Qt 的来源有两种：用机器上已装的 Qt，或由 `client/build.ps1` 从 `download.qt.io` **自动下载官方二进制包**
（只取 qtbase + qtsvg + qtmultimedia，详见 `client/README.md`）。两者是同一份官方产物，许可结论完全一致。
自动获取的东西都落在仓库根的 `.qt/`（已写进 `.gitignore`，**不进仓库**）。

> **音效为什么用 Qt Multimedia**：客户端要支持多平台，`QSoundEffect` 是跨平台统一的 Qt API
> （平台原生 API 各写一套更难维护）。代价是随包多分发 `Qt6Multimedia.dll` + 它的 FFmpeg 后端
> （约 20 MB）—— 用户已知情并接受。选型与分层兜底写在 `client/CMakeLists.txt` 与
> `client/src/model/Sound.h` 的注释里（没有 Multimedia 时 Windows 退回 `winmm`，再没有就静默）。

---

## 2. 为什么是 LGPLv3

Qt 6 只提供三种许可分支：**商业授权 / LGPLv3 / GPLv3**（Qt 6 起不再提供 LGPLv2.1 与 GPLv2）。

- 官方安装包与自动下载的二进制包都是开源分支：Qt 目录里的 `licenseInfo.txt` 第一行 = `License type [Opensource]`，
  随包的 `Licenses/LICENSE` 亦写明「most of the functionality is available under LGPLv3 …
  the tools as well as some add-on components are available only under GPLv3」。
- 官方页面（[Qt Licensing，Qt 6.11](https://doc.qt.io/qt-6/licensing.html)）列出了**仅 GPLv3** 的模块：
  Qt Canvas Painter、Qt CoAP、Qt Graphs、Qt GRPC、Qt HTTP Server、Qt Lottie Animation、Qt MQTT、
  Qt Network Authorization、Qt Qml Compiler、Qt Quick 3D、Qt Quick 3D Physics、Qt Quick Timeline、
  Qt Virtual Keyboard、Qt Wayland Compositor。
  **本项目一个都没用到** —— 这是"能走 LGPLv3"的前提。
  ⚠ **Qt Multimedia 不在该名单里**（它是 LGPLv3 模块），所以音效走 `QSoundEffect` 不破坏这个前提；
  但它捆绑的 **FFmpeg** 是另一家的组件，见 §1 表格与 §3 第 ② 条。
- 构建期工具 `moc` / `uic` / `rcc` 属「Qt tools and utilities」：商业授权，或 **GPLv3 + Qt GPL Exception 1.0**。
  该例外正是为了让这些工具处理你的源码时不"传染" —— 本项目用 `moc`/`uic` 生成代码，不影响自身许可。

---

## 3. LGPLv3 的三条义务，以及本项目的落地方式

| 义务 | 本项目怎么满足 |
| --- | --- |
| **① 允许替换/重新链接 LGPL 库** | Qt 是**独立 DLL**，用户自行编译后覆盖即可；程序里没有 DLL 校验、签名锁定或完整性检查来阻止替换（见 §5 的"不要做的事"） |
| **② 随附许可文本与版权声明** | `client/licenses/`：`Qt-LICENSE.txt`（Qt 官方 LICENSE，含 **LGPLv3 全文 + GPLv3 全文**；LGPLv3 是 GPLv3 的补充条款，必须一并提供）、`Qt-Copyright.txt`（第三方版权声明）、`NOTICE.txt`（本项目的说明）。`build.ps1 -Deploy` 会自动把它们拷进 `client/dist/licenses/`。⚠ 因为音效走了 Qt Multimedia（含 FFmpeg 后端），`build.ps1` 还会把**上游 Qt 安装根 `Licenses/` 下的文本**原样拷到 `dist/licenses/qt/`（`COPYING.txt` / `Copyright.txt` / `LICENSE` / `LICENSE.FDL`）—— 这一份是 FFmpeg 等捆绑组件的许可来源，**不要删** |
| **③ 能提供库的对应源码** | `NOTICE.txt` 写明版本与官方获取地址；此外 `build.ps1 -Deploy` **默认会自动下载**当前 Qt 版本对应模块的源码包（`qtbase` / `qtsvg` 的 `*-everywhere-src-<版本>.tar.xz`，逐个校验官方 `.sha256`）到 `.qt/src/<版本>/`，并在 `dist/QT-SOURCE.txt` 里列出「官方 URL + SHA-256 + 本地路径」；加 `-IncludeQtSourceInDist` 还能把源码包直接拷进 `dist/qt-source/`（约 50 MB）。动态链接本身已满足 §4(d)(1)，源码包算"更省心"的那一档 |

> 另外两条容易忽略的：**修改 Qt 本身**要把修改按 LGPLv3 公开（本项目未改 Qt）；
> **不要用 Qt 商标**暗示官方背书（本项目未使用 Qt 品牌素材）。

---

## 4. 如果将来想静态链接 Qt

只有三条路：

1. **仍在 LGPLv3 下**：额外提供"可重新链接"所需的目标文件（LGPLv3 §4(d)(0) 的 Minimal Corresponding
   Source），让用户能替换静态库后重新链接；
2. **把整个客户端改用 GPLv3**：静态链接不再有障碍，但**客户端源码必须按 GPLv3 开放**；
3. **购买 Qt 商业授权**。

本机这套 Qt 是 **shared 构建**（`qconfig.pri` 里 `static` 是 disabled feature），物理上无法静态链接，
所以现状必然落在第 1/3 条的语境里 —— 详见 `client/README.md`「关于链接方式」。

---

## 5. 分发时的注意事项（不要做的事）

- **不要**给 Qt 的 DLL 加校验、加密或强制签名检查 —— 那会阻止用户替换 Qt，直接违反 LGPLv3。
- **不要**只拷 `mahjong-client.exe` 而丢掉 `licenses/`（以及 `fonts/`、`tiles/`、`i18n/`）：
  分发即触发随附义务，且丢 `fonts/` 会让结算界面退回文字。
- 分发自己的修改版时，**Qt 的修改部分**要按 LGPLv3 公开；**本项目自己的代码**不受此约束。

---

## 6. 维护规则

- **新增 Qt 模块**：先确认它不在 §2 的"仅 GPLv3"名单里；若是，要么换成 LGPLv3 的替代方案，
  要么整体改用 GPLv3/商业授权。同时更新**五处**：`client/CMakeLists.txt` 的 `find_package`、
  `client/build.ps1` 的必需 DLL 清单（`$required`）与插件目录（`$pluginSubs`）、
  `tools/qt-provision.ps1` 的 `$script:QtDefaultModules`（决定自动下载取哪些模块归档）、
  本文件 §1 的表格、以及 `client/licenses/NOTICE.txt` 里的模块列表。
  ⚠ **新模块若捆绑了别的第三方库（如 Multimedia 捆绑 FFmpeg）**，要额外确认那份库的许可、
  并把它随包带上（`dist/licenses/qt/` 那一步就是为此加的）。
- **新增其它第三方库**：在 §1 的表格里登记，并把它的许可文本放进 `client/licenses/`。
- `client/licenses/` 的文件由 `build.ps1 -Deploy` 拷到 `dist/licenses/`；
  该函数的"已就绪就跳过"判断**必须覆盖 licenses 目录**（否则老 dist 永远补不齐 —— 与
  `styles\qmodernwindowsstyle.dll` 那次的历史坑同源）。
