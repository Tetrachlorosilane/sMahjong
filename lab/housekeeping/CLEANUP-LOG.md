# 清理台账 —— 只删缓存/派生/临时（2026-10-12）

> 纪律：⛔ 不用通配符一把梭；逐项打印**绝对路径** → 查引用 → 查占用进程 → 记账。范围外的都没碰（见 §3）。
> 全机 313 个进程逐一筛过：**没有任何 python / trainer / java / node / mahjong 进程在跑**，删除时无人占用。

## 1 逐项

| # | 绝对路径 | 大小 | 删除依据 | 释放 |
| ---: | --- | ---: | --- | ---: |
| 1 | `E:\mahjong-p301\v4-wl-p301-A-g01` | 171.61 GiB | A 臂 compact，**派生自** `S:\...\raw\v4-wl-p301-A-g01`；raw 已核对完整 | 171.61 GiB |
| 2 | `S:\mahjong-training\compact\v4-wl-p301-A-g01` | 0 B | 指向 #1 的 **junction 链接本身**（`cmd rmdir` 只断链、不递归进目标） | 0 B |
| 3 | `<repo>\trainer\build\{106 个子目录}` | 8.54 GiB | 构建/对拍/soak 运行产物，`trainer\build.ps1` 可重建；`.gitignore:91` | 8.54 GiB |
| 4 | `<repo>\python\.tmp\{20 个子目录}` | 295.8 MiB | 自检/采集 scratch（`.gitignore:124`；`v4/arena.py:49` 自述曾涨到 17.1 GB） | 295.8 MiB |
| 5 | `<repo>\release\*.zip`（根部 46 个） | 573 MiB | 旧版本发布包，`tools/package-release.ps1` 可重建 | 573 MiB |
| 6 | `<repo>\release\stage-client` | 85.5 MiB | 打包暂存目录 | 85.5 MiB |
| 7 | `C:\Users\HP\AppData\Local\Temp\mahjong-parity` | 2,958.3 MiB | 本项目对拍脚本的 scratch（`ds/` 2.95 GB） | 2,958.3 MiB |
| 8 | `C:\Users\HP\AppData\Local\Temp\q2ittc5x` | 830.3 MiB | Windows 11 SDK 安装器残留（`Win11SDK_*`） | 830.3 MiB |
| 9 | `C:\Users\HP\AppData\Local\uv\cache` | 22,484.9 MiB | uv（Python 包管理器）下载缓存 = pip 缓存同类 | 22,484.9 MiB |
| 10 | `C:\Users\HP\AppData\Local\pip\cache` | 442.1 MiB | pip 下载缓存 | 442.1 MiB |
| 11 | `C:\Users\HP\AppData\Local\npm-cache\_cacache` | 810.5 MiB | npm 下载缓存 | 810.5 MiB |
| 12 | `C:\Users\HP\AppData\Local\npm-cache\_npx` | 505.8 MiB | npx 缓存 | 505.8 MiB |
| 13 | `C:\Users\HP\mahjong-tmp` | 1,015.5 MiB | 本项目 **4 份重复 compact 快照**（`base`/`before20ex`/`after20ex`/`after20final`），无脚本/文档引用 | 1,015.5 MiB |
| 14 | `C:\Users\HP\AppData\Local\NVIDIA\DXCache` | 36.68 GiB | DirectX 着色器缓存（纯缓存，驱动自动重建） | 36.66 GiB ⚠ |

⚠ #14 剩 **19 个文件 / 18 MiB** 被显示驱动占用删不掉（`Remove-Item` 报 in-use）—— 纯缓存，无影响。

**总计（逐项实测之和）= 246.8 GiB**。**三盘实测净变化 = C: +56.90 · E: +171.61 · S: +0.00 = 228.51 GiB**。
⚠ 差额 18.3 GiB 的成因：测量窗口内有**并发写入**（自检日志/DSH spill）且 **uv 缓存与 venv 之间有硬链接**（删缓存不释放被链接的块）⇒ **以"三盘实测净变化"为准**，不以逐项之和为准。

## 2 三盘前后（`Get-PSDrive`，GiB）

| 盘 | 清理前可用 | 清理后可用 | 净释放 |
| --- | ---: | ---: | ---: |
| `C:` | 79.03 | **135.93** | +56.90 |
| `E:` | 379.28 | **550.89** | +171.61 |
| `S:` | 160.16 | 160.16 | +0.00（本次没在 S: 上删任何数据字节） |

## 3 明确**没删**的（来之不易 / 别人的 / 契约）

- `S:\...\raw\**`（37,147 文件 / 36.89 GB）。其中 **P3-01 A 臂 raw = 36,001 文件 / 36.376 GB = 12,000 场**（`g0..g11999` 无缺口、`summary.json.games=12000`、seed_base 52920002）⇒ 这才是 #1 可删的前提。
- `S:\...\tools\**`（含 `trainer\trainer.exe` 等 4 个 exe）· **他人产物 `S:\...\style-reward\**`（8,148 文件 / 21.2 GB）** · `w-ladder\traces\v4-wl-fold-c1-g01`（1001 个，有意保留）。
- 仓库 `tools\build\`（1.53 GB：端点权重 + 起点网 `v4-expert-defrep-g10\net.bin`，预注册 §2 要它）· `python\.venv\`（4.19 GB）· **`git` 跟踪的一切**。
- `release\rust\`（2.49 GB：离线 vendored Rust 工具链 + `vendor/` + `vendorize.mjs`，`tools\upstream-parse-check\README.md` 引用）· `release\bot-ai\` · `RELEASE-*.md`(13) · `assets-*.txt`(10) · `release-{body,patch}-*.json` · digest/`commit-msg-*` 文本（全部保留）。
- `trainer\build\wk-8`（14.7 MB，`docs\TRAINER-CPP.md:628` 按名引用）+ `trainer\build` **全部根文件**（6 个 exe / 日志 / 报告 / 读数，38.3 MB）—— 保住 `producer.py:27` 的缺省 `trainer.exe` 路径。
- `python\.tmp` **全部根文件**（含 `head-notes.md` 报告）· `w-ladder\_p2-11*-work`、`_profile-work`（被 `_p2-11*.ps1` 当 `$WORK`、`COLLECT-PROFILE.md:5` 引用）· `w-ladder\logs`、`S:\...\logs`（无超大日志）。
- `S:\...\compact\v4-heads-g01`（5.82 GB）—— ⚠ **它的 `raw` 已不存在 ⇒ 这是该数据集唯一副本**，按"来之不易"**保留**；若你确认 heads 实验已结案，可另删 5.82 GB（需你确认）。
- 未动：`.dsh` 会话(311 MB)/`dsh-runtimes`/`plugins`/`attachments`（DSH 会话历史，**待你逐项确认**）· `CrashDumps`(0.58 GB，他应用诊断) · `VSTelem`(0.22 GB) · `NVIDIA\GLCache` · 任何文档/下载/照片 · `C:\Windows` · `C:\Program Files`。
