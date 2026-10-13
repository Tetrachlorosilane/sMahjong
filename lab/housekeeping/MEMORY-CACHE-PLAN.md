# 内存 / 缓存 / 盘位规划（P3-01 事故后重写 · 2026-10-12）

> 事故：P3-01 **A 臂**（1 轮 × 12,000 场）建 compact 时先 `ENOSPC`（S: `train.cand.npy`）、改 junction 到 E: 后继续，最终**爆掉内存与交换**、间接顶爆 C:，用户手动中止。
> 本文件只用**实测数字**，⛔ 不跑训练/采集/闸门。测量对象 = 已删的 `E:\mahjong-p301\v4-wl-p301-A-g01`（24 个 `.npy` 头 + 文件大小），见 `CLEANUP-LOG.md`。

## 1 每场每张量的字节账 ⇒ 修正 `budget.py` 常数

规模（实测）：**12,000 场 / 7,868,212 决策 = 655.7 决策/场**；compact 目录 **171.61 GiB = 184.27 GB**。

| 张量 | shape | dtype | B/决策 | MB/场 | 来源口径 |
| --- | --- | --- | ---: | ---: | --- |
| `evt` | (N,60,96) | f2 | 11,520 | **7.553** | **W1 的 `h_evt`** 事件窗（K=60） |
| `cand` | (N,29,128) | f2 | 7,424 | **4.868** | 逐候选段（v3 + sidecar） |
| `tile` | (N,34,48) | f2 | 3,264 | **2.140** | v3 逐张属性 |
| `h0` | (N,192) | f4 | 768 | **0.504** | W1b GRU carry 冷启动 |
| 其余 16 个小张量 | (N,…) | i1/i2/i4/f2/f4/u1 | 258 | **0.291** | `effect/ctx/aux_*`（**`--aux`**）+ 标签/座位/顺位 |
| **合计** | | | **15,716** | **15.356** | 大四张 = 15.065（= 用户口径「≈15.1」） |

- **旧常数 `DEFAULT_COMPACT_BYTES_PER_GAME = 3.06e6` 被实测证伪：差 15.356/3.06 = 5.02×** —— 它是 v3 谱系（**无 `--aux`、无 W1 `h_evt`**）的账。
- **已改为 `15.36e6`**（`budget.py:74`，同处注释写明依据）；`selfcheck` 两条断言同步更新 + 新增下界断言 `≥15.0e6`（防再跌回旧口径）。实测输出：
  `[ok] 预算：磁盘默认值与实测一致（raw 1.52 / compact 15.36 MB/场…）`、`[ok] 预算：compact 默认值 ≥ 实测下界 15.0 MB/场（v3 旧值 3.06 已证伪）`。
- ⚠ **同源偏差未修（本批只授权改 compact）**：`raw` 实测 **3.25e6**（36.89 GiB / 12,000，含 `*.feat.bin`）vs 常数 `1.52e6` ⇒ **差 2.14×**。待决。
- 影响面：`paths.allocate(need_bytes=)` 的预扣与 `gates_now` 的场次换算都用它 ⇒ 取小 5× = 把 **181 GB 的一轮当成 37 GB**。

## 2 提交 / 页面文件：31.6 GB 物理 + 4.7 GB 页面文件为什么被打爆

实测：物理 **31.6 GB**；提交上限 **36.4 GB**；`C:\pagefile.sys` 已分配 **4,847 MB**、峰值 **4,668 MB**（顶到上限，无法再涨）。`CACHE_FRACTION = 0.8` ⇒ 缺省缓存档上限 = **0.8 × 31.6 = 25.28 GB**。

- **闸门根本没跑**：`v4 loop` 只在 `--target-minutes > 0` 时调 `gates_now`，预注册命令行没给它（缺省 `0.0`）⇒ `plan_games()` 原样返回 `--games 12000`，**五道闸门一条都不看**。
  ⚠ 且 **`v4 loop` 没有 `--cache-gb`**（那是 v3 `online.py` 的旗标）——"`v4 loop --cache-gb` 缺省 25 GB"应修正为"**闸门存在但没被触发**"。
- 修正常数后的场次闸门（本机）：**缓存档上限 = floor100(25.28 GB ÷ 15.36 MB) = 1,600 场**（错常数时算出 8,200）；`compact` 配额 116 GB ⇒ 7,552 场；`raw` 配额 80 GB ⇒ 24,615 场；总配额 200 GB ⇒ 10,747 场；S: 盘余量 ⇒ 7,262 场。
- 于是 181 GB compact 远超 25 GB 缓存档 ⇒ **PPO 读 5 遍全落磁盘档**；同时页缓存与 6 worker + C++ trainer 抢 31.6 GB 物理内存，提交量 >36.4 GB 时页面文件又顶死 ⇒ 硬故障/换页抖动 ⇒ C: 被页面文件与临时文件一起顶爆。
- **建议值（依据：物理 31.6 / 提交 36.4 / 缓存档实测拐点 27.2 GB 走通）**：缓存预算取 **≤ 0.5 × 物理 = 15.8 GB**（留 ≥15 GB 给 OS + torch + 6 worker + trainer）⇒ **≤ 1,000 场/轮**；>1,600 场必然磁盘档（**两条预注册臂都越档**：A 12,000、B 2,000 场 ⇒ 30.7 GB）。
- `--target-minutes 20`（让闸门自己夹）+ `--workers 6 --eval-workers 6` + `OMP/MKL_NUM_THREADS=6`（维持预注册低 CPU 压力口径，⛔ 别升到 12）；**compact 建集别用 24 worker**（每 worker 一套行缓冲）。
- Windows 无 `madvise`：页缓存只能靠 **cap + 「每轮 net.bin 落盘即删 compact」** 控制；torch 是 CPU-only，别开 `pin_memory`/多进程 DataLoader（会再加一份副本）。

## 3 页面文件：现状与**待用户执行**的动作（⛔ 本批没改页面文件、没动 `powercfg`）

现状：`AutomaticManagedPagefile = True`，`C:\pagefile.sys` **上限只到 4.7 GB** ⇒ 提交上限被钉死在 36.4 GB。建议（**管理员 + 重启**）：

```powershell
# ① 关自动管理  ② C: 留 4 GB（内核转储要用）  ③ E: 加 32/64 GB（E: 现 550 GB 可用）
$cs = Get-CimInstance Win32_ComputerSystem; Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile=$false}
Set-CimInstance -InputObject (Get-CimInstance Win32_PageFileSetting -Filter "Name='C:\\pagefile.sys'") -Property @{InitialSize=4096; MaximumSize=8192}
New-CimInstance -ClassName Win32_PageFileSetting -Property @{Name='E:\pagefile.sys'; InitialSize=32768; MaximumSize=65536}
# ④ 重启后核对：提交上限应从 36.4 GB 涨到 ~68 GB
```

⚠ 取舍：放 E: = 与 S: **同一块 USB SSD**（见 §4），掉线时页面文件一起消失；在意就放 **D:（内置 500 GB，192 GB 可用）**。

## 4 盘位规划与"开工前硬报错"清单

- ⚠ **S: 与 E: 是同一块 USB SSD 的两个分区**（`Silicon_files` / `Silicon_SSD`）⇒ **掉线风险共用**（症状：`AccessDeniedException` / `ERROR_IO_DEVICE`，先查盘符在不在）。
- **raw 留 S:**（36.89 GB / 80 GB 配额，够）；**compact 走 E: 且作为独立数据根** `MAHJONG_DATA_ROOT=E:\mahjong-training` —— ⛔ **别再用 S: 里的 junction**：junction 让 `free_gb()`/`dir_size_bytes()` 报 **S:** 的余量而字节落在 E: ⇒ 这次 `ENOSPC` 被误判成"S: 还够"就是这么来的。
- **纪律：每轮 `net.bin` 落盘后立刻删该轮 `raw`/`compact`**（预注册 §6 已写）；compact **一次只留当前轮**。
- **开工前四条（可整段复制；任一条不过就硬报错、别开跑）**：

```powershell
[System.IO.DriveInfo]::GetDrives() | ? { $_.Name -in 'S:\','E:\' } | select Name,@{n='FreeGB';e={[math]::Round($_.AvailableFreeSpace/1GB,1)}}  # ① 盘在不在
Get-CimInstance Win32_OperatingSystem | select @{n='CommitLimitGB';e={[math]::Round($_.TotalVirtualMemorySize/1MB,1)}},@{n='CommitFreeGB';e={[math]::Round($_.FreeVirtualMemory/1MB,1)}}  # ② 提交上限
$g=12000; $need=$g*(15.36e6+3.25e6)/1GB; "本轮需要 {0:N1} GB（raw 3.25 + compact 15.36 MB/场）" -f $need   # ③ 估字节
if ($need -gt ((Get-PSDrive E).Free/1GB - 20)) { throw "空间不够：硬报错，⛔ 不许开跑（需要 $([math]::Round($need,1)) GB）" }   # ④ 硬闸门
```

## 5 P3-01 重启方案（三选一，**三条都要用户批准**；本批只摆数字，不选）

基线：S: 可用 **160.16 GB** · E: 可用 **550.89 GB** · 数据根占用 69.28/200 GB · `compact` 配额 **116 GB**。A 臂 12,000 场 = raw **39.0 GB** + compact **184.3 GB** = **223.3 GB**。

- **(a) 原口径照跑**（A = 1 × 12,000 场）：223.3 GB ⇒ **S: 装不下**（可用 160.2 < 184.3，且 compact 184.3 > 116 配额、总配额 200 < 223.3）；**E: 装得下**（550.9）。须先：数据根移 E:、抬 `QUOTA_GB['compact']`(116→200) 与 `TOTAL_QUOTA_GB`(200→260)、compact worker 压到 6。**为什么这次不会重演**：① 常数修正后闸门/预扣算的是 184 GB 而不是 37 GB；② 数据根真在 E:、不再假装 S:；③ §4 第 ④ 条不过就不开跑；④ 每轮即删。风险：181 GB **必然磁盘档** ⇒ 预注册 §5 的"A ≈ 90 min"作废（磁盘档实测 41.78 GB → PPO 56.8 min/轮；181 GB 且每 epoch 重读 ⇒ **≈3–5 h 仅 A 臂**）。**⚠ 需用户批准**（抬配额 + 放弃"只跑缓存档"）。
- **(b) A 改「分块训练」**（同 12,000 场、顺序训多个 ≤1,000 场分块）：峰值 RSS 可控（分块 compact ≤15.4 GB，进缓存档）。**但改变了 A 的定义**（预注册 §1 是"一次采完、**一次训完（1 批）**"⇒ 变 k 批）⇒ **需用户批准 + 预注册修订**（建议 k=8 或 12，别贴 B 的 k=6，否则 A/B 不可分）。风险 = 定义漂移，结论不能再叫"A vs B"。
- **(c) 降场次**：功效（预注册 §3 `MDE ≈ 1.96·1.199·√(2000/N)`）：**1,600 场 ⇒ 2.63**（= 缓存档硬上限）·3,000 场 ⇒ 1.92（但 compact 46 GB **仍不进缓存档**）·1,000 场 ⇒ 3.32。⇒ 坚持缓存档则最多 **1,600 场/臂**、只能分辨 **≥2.6 顺位点**，结论按预注册 §5 只能写"≥2.6 的差"。**⚠ 需用户批准**（改功效）。
