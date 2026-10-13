# B1（compact 构建 ‖ 采集）落进回路 —— 实现 · 五条判据 · 实测

窗口 2026-10-12 18:07–19:25（78 min）。**全程有兄弟任务**在循环跑 `trainer/build/trainer.exe v4cache … --workers 8`
（每次 ≥200 s、两条各 ~8 核）⇒ 静窗只有几十秒～几分钟；干净窗口 19:14–19:23 拿到。
脚本 `S:\mahjong-training\w-ladder\b1-overlap.py`（**编排层/数据根**）· 原始输出全在 `S:\mahjong-training\w-ladder\_overlap\b1\`（`crit*.log`/`*.b1.jsonl`/`*.sys.csv`/`*.proc.csv`）。

## 1 实现形态
- **开关 `--overlap-compact`，缺省关**（缺省 = compact 跑完再采集，与加它之前**同一条命令序列**）；⛔ **未动 `loop.py` 一个字节**。
  ⚠ 可用前提：`loop.py` 里 collect(g) 用 `--init`=net_out(g−1)、compact(g) 在 train(g) 之前 ⇒ **链内 `compact(g)‖collect(g+1)` 不可用**（要等 export(g)，属 (C)）。B1 要的是**采集策略已落盘冻结**（G4 机检）。
- **七条闸门 G1–G7**（不过就 exit 3/4 **且一条命令都不打**）：G1 kind∈{collect}（train/critic/audit/eval/第二个 compact/未知 **一律拒**，fail closed）· G2 两臂写目录不相交 · G3 compact 输入分片冻结且与采集写目录分开 · G4 策略权重+可执行文件已落盘（跑前后各 pin sha256）· G5 采集命令行不引用正在被写的 compact（**按解析后绝对路径**判，⛔ 不按目录名——会假阳性）· G6 独占锁=一次只做一个重叠对 · G7 **静窗闸门**（`trainer.exe` 一律算竞争；`python/java` 只算 ≥200 MB 的）。
- **有界内存**：只"起两条进程+采样+收尸"，⛔ 不把任何数据读进本进程（不重蹈 B2 `--rows` 全量物化 15.1 GB）；compact 与消费者之间**无全量物化**。
- **内存闸门**：`commit_free`（= `GlobalMemoryStatusEx.ullAvailPageFile`）< 阈值 ⇒ **tree-kill compact** ⇒ 清半成品 ⇒ **回退串行**，全程记账、**回退看退出码**。

## 2 五条判据（原文见 `crit*.log`）
**① 开/关产物逐字节相同**（40 场；A 串行 / B 重叠 / C 串行重跑）——`crit1-compare.log`（复跑亦通过）
```
[相同] compact A vs B：n=49 bytes=558835441 digest=66d490d344dd3f872f515157602ee2b4…
[相同] compact A vs C（基线）同上 · [相同] raw(80 文件) A vs B bytes=118613295 digest=c0e253add487213d7ba9bbbb…
[相同] summary.json（去墙钟）A vs B norm=f904b4fcbc580dec8fe1d6c5e418d401…  ⇒ 结论：✅ 逐字节相同
```
⚠ `summary.json` 须去掉 `seconds/games_per_second/decisions_per_second` 三个**墙钟**字段（三臂 9.82/10.83/11.01 s）；compact 的 `meta.json` 无墙钟字段 ⇒ 整目录全量哈希。第一层（`crit1-cmds.log`）：命令逐字相同，唯一差异是各臂自己的输出目录（G2 强制不相交）。

**② 重叠窗口内两进程确实并发**（两路独立证据）——`crit2-result.log`
```
[编排器] other start=+0.7s end=+50.3s own=49.7s pid=11116 · compact start=+0.7s end=+151.8s own=151.2s pid=38412
⇒ 重叠区间=[0.7s,50.3s]=49.7s ⇒ 确实并发；「两条同时在」采样点 49/149
[ov_sample2.ps1(Get-Process,非 CIM)] 同两个 pid：other 47 次均核 7.90；compact 144 次均核 0.33
⇒ 独立采样器里两 pid **同秒各 >0.3 核** = 37 秒（首个 19:19:53）
```
⚠ compact 的**父**进程只 0.33 核：真活在它的 4 个 `_parallel-train` 子进程里。

**③ 内存闸门生效（构造逼近 ⇒ 停/回退）**——`crit3-passB3.log`
```
（自然下限：400 场 compact‖200 场采集，commit_free 20.21→最低 15.88 GB/125 采样点）
[内存闸门] commit_free=16.74 GB < 16.9 GB ⇒ 杀 compact、回退串行
kill_tree rc=0（"SUCCESS: … PID 39364 (child of 34100) … 15252…"）· 清理 {'removed':True,'verified_gone':True,'gb':5.56}
compact_serial_fallback code=0 own_wall=53.0s ⇒ fell_back_ok=true   exit=0
```
⚠ 本判据暴露我**三个真缺陷，全已修+复测**（红证 `crit3-passB{,2}.log`）：① `rmtree(ignore_errors=True)` 静默吞掉"删不掉"⇒ 回退撞 `OSError [Errno 22]`；② 不看回退退出码 ⇒ 把"失败"印成"回退"；③ 只 kill 父不够——`_parallel-train` 子进程握着 `job0.log` 句柄（`PermissionError [WinError 32]`）⇒ **回退根本走不通**。现在：tree-kill+等真退+带重试删+复验消失+看退出码；删不掉就**不回退**并 exit 5。

**④ 实测收益（安静窗口，同分片大小：compact 1000 场 / 采集 200 场 w=8）**——`crit4-result.log`
```
① compact 单独 own=126.1s commit_free 19.97→15.53 污染=False 同盘写 mean=127.1 peak=1036 MB/s
② 采集 单独   own= 46.7s commit_free 20.92→20.58 污染=False 同盘写 mean=  5.2 MB/s
③ 重叠      墙钟=152.0s 分相 other=49.7 compact=151.2 污染=False 同盘写 mean=121.5 peak=1026 MB/s
⇒ 串行=126.1+46.7=**172.8s**（分相之和，同探索口径）；重叠=**152.0s** ⇒ 省=**20.8s（12.0%）**
⇒ 对照已测 16.4%（182.0→152.2=29.8s）：**−4.4 pp**；被拖慢 compact 1.20×（已测 1.15×）、采集 1.06×；捕获率 44.5%（已测 59.6%）
```
**为什么不是 16.4%**：重叠墙钟几乎逐秒复现（152.0 vs 152.2），但我**串行基线更短**（172.8 vs 182.0）⇒ 可省的绝对量本就少，且 compact 被拖慢更狠 ⇒ 省 20.8s 而非 29.8s。方向与机制一致。

**⑤ 负向：compact‖训练必须被拒**——`crit5-*.log`
```
check --pair neg-train.json → [拒绝] G1 other.kind='train' + [拒绝] G5（命令行引用了正在被写的 compact）→ exit=3
run   --pair neg-train.json → ⛔ 闸门未过（2 条）⇒ **不打任何命令**。 exit=3
check --pair neg-eval.json  → [拒绝] G1 other.kind='eval' … exit=3
selftest-negative → train/critic/compact/eval/mystery-phase 全拒、collect 允许；全部通过（6 例）
（G7 实测也拒过：对方 trainer.exe 在跑时 [拒绝] G7 静窗 … pid=38672 trainer.exe ← 竞争 exit=4）
```

## 3 内存 / IO
`commit_free` 最低：compact 单独 **15.53** · 重叠 **15.69** GB（≫8 GB 闸门，余量 ≈7.5 GB ⇒ 真实负载下不触发）。
同盘写：127.1 → 121.5 MB/s（**−4.4%**）⚠ 与探索的 176.2→129.1（−27%）不同：我绝对值更低、退化更小；PDH 这计数极爆发（p50 常为 0），`-IoEverySec 5` 的稀疏均值只当量级看，**不当判据**。

## 4 前置条件与残留
前置：分片 ≤1000 场 · `--workers ≤8` · `OMP/MKL=8` · 一次只做一个重叠对 · compact 落 **E: 独立根**、⛔ 无 junction · ⛔ 未改 `powercfg`/页面文件 · ⛔ 未重启 · **先查静窗**。
残留：硬链接分片 `raw-shard/{b1-src-small,b1-src-mid,ov-A-s00}` · compact `b1P` 14.78 + `b1m-{off,gate}` 11.72 + `b1s-*` 1.56 GB · raw `b1*` ~2.1 GB · `_overlap/b1/` 全部证据。**已删** `compact/b1L-{off,on}` 29.56 GB（④ 串行 bug 期间的无效产物）+ 半成品 `b1m-gate`。
⚠ G7 只认 `trainer.exe`/重 python·java —— `java -jar mahjong-server.jar` 不在名单，要扩加一行。

## 5 算力账
78 min：①小负载 4 次 · ②③中负载 4 次 · ④大负载 3 段（一次拿到）· 两次失败的 ④ 整数对尝试（各 ~5 min，被 G7 判污染后整对重来）· 1 次自己拆掉的 ④ 串行 bug。**崩溃/掉盘/ENOSPC=0**。⚠ 兄弟任务全程在跑：我 G7 拒 2 次、标 5 次 `contaminated`；我也压了它 —— 双向都要进两边的时间账。

## 6 git
`git status` 只有**兄弟任务**的 `NOTES.md`/`docs/TRAINER-CPP.md`/`trainer/src/{batchforward.hpp,main.cpp,v4policy.{cpp,hpp}}`。我这批**仓库零改动 ⇒ 不提交、不推送**，只交"数据根脚本 + 本结果档"。

## 7 确认
⛔ 未跑 PPO 主线（只跑 compact + `trainer.exe selfplay` 采集）· ⛔ 未改 `powercfg`/页面文件 · ⛔ 未用 junction · ⛔ 未重启。

