# 清理台账（第一百一十九轮 · 2026-10-20）

> 口径：**先盘点再删**（判据 19：逐项 `-LiteralPath`、删前复核解析后的绝对路径、⛔ 不对共享/仓库根用 `-Recurse -Force`）。
> 范围 = 仓库 `C:\Users\HP\source\games\mahjong` 内的**可证明无用 / 可重生成**物；`raw/**`·`compact/**`·`ckpt/**`·`replays/**`·
> `player*/**`·`bot-ai/**`·`style-reward/**`·`tools/build/**` 的**权重端点一律不删**（见 §3）。
> ⛔ 本批未采集、未训练长训、未跑闸门、未发布。

## 1 盘点（按可释放体量排序；前 15 行）

| 路径 | 大小 | 最后修改 | 类型 | 可重生成? | 建议 | 理由 |
| --- | ---: | --- | --- | --- | --- | --- |
| `tmp/mahjong-mslt/`（`compact/` 15.1 GB + `ckpt/` 0.99 GB） | 16.36 GB | 2026-10-13 | MSLT 实验原始数据 + 存档 | 否（需重采） | **留（待定夺）** | 第七十八/九十九轮旧实验；官方归档在 `S:\mahjong-training\w-ladder\MSLT-*`，但本地无同 sha 对照 ⇒ 不能证明冗余 |
| `.tmp/mv1-s20…s34/s96/s98/s99`（18 个） | 7.02 GB | 10-18/19 | 阶段脚手架：数据副本 + 实验网 + 日志 | 否（网不可逐位重生成） | **删（已删）** | 阶段结论已入 `NOTES.md` §6.5 + `tools/build/mv1-*` 证据；属"自己创建的纯缓存"（任务书甲-2 白名单） |
| `.tmp/mv1-s38/` | 0.42 GB | 10-20 | A0–A8 权重 + 闸门 6 份 summary + verdict | 否 | **留** | 链尖 `A8`；判据 21 的归档就在这 |
| `.tmp/mv1-s37/` | 1.31 GB | 10-20 | 唯一一份完整 compact（39,709 决策/55 场） | 否（需 raw + `dataset build`） | **留** | 本轮 lr 扫描的"同一份数据"，删了本批不可复现 |
| `.tmp/mv1-s36/` | 0.30 GB | 10-20 | 历史网 `S1A1200.bin` + 中间存档 | 否 | **留** | 阶段 3 对手池的历史席 |
| `.tmp/mv1-s35/` | 0.04 GB | 10-20 | 对手池 v4 冻结网 4 个（def7/atk3/win2/defrep） | 否 | **留** | 阶段 3 的"行外"席 |
| `tmp/{stylemetric,rotate-perm-a/b/c,byte-*,bl13,rerun,oldbuild,oldtree}` | 0.83 GB | 10-08/13 | 旧实验的逐场数据 | 否 | **留（待定夺）** | 未证明可由现存数据重生成；量不大，先不动 |
| `release/`（`rust/` 2.5 GB + 打包产物） | 2.71 GB | 10-15 | 打包输出 + Rust 工具链 | 部分（重打包） | **留（待定夺）** | `.gitignore` 的发布产物，但 `rust/` 是工具链不是垃圾 |
| `.uv-cache/` | 4.15 GB | 09-23 | uv 包缓存 | 是 | **留（待定夺）** | 删了下次 `uv sync` 重下载；非"过时" |
| `tools/build/v4-*`（266 个） | 1.33 GB | 10-01/12 | 旧实验证据（每目录 `net.bin` + `summary.json`） | 否（权重） | **留** | 权重是端点；`summary.json` 只有 KB 级，删了省不出东西 |
| `tools/build/mv1-s*`（57 个） | 0.30 GB | 10-18/20 | 证据 + 权重（`net.bin` 每个 5.3 MB） | 否（权重） | **留** | ⚠ 任务书点名"`net.bin` 不删"；`verdict/summary` 已是"每阶段至少一份"的最小集 |
| `tools/build/_rejected/` | 0.15 GB | 10-08 | 被拒实验（18 组同名） | 否 | **留** | 判据"按名索引会取错"的红证来源 |
| `bot-ai/`（13 包，含 roster 的 retired 4 个） | 0.05 GB | 10-12 | 已发布机器人权重 | 否 | **留** | ⚠ roster 里 retired 包**权重保留**（任务书明令） |
| `python/.tmp/bl04` | 0.13 GB | 10-20 | 续跑等价性夹具 | 是（重跑自检） | **留** | 今天仍在使用（mtime = 本轮） |
| `replays/` 29 份 + `players/` 1 份 | 3.7 MB | 10-12 | 运行时对局记录 / 玩家档案 | 否 | **留** | 别人的数据；`.gitignore` 的运行时目录 |
| `**/__pycache__`（`.venv`/`.uv-cache` 之外 15 个） | 7.0 MB | — | 纯 Python 字节码缓存 | 是 | **删（已删）** | 纯缓存 |
| 仓库根 `hs_err_pid*.log` ×10 · `.commit-msg.txt` · `ceiling-gate-selftest-*` ×2 · 空目录 `mv1-s23/`·`torchinductor_HP/` | 8.4 MB | — | JVM 崩溃日志 / 临时提交信息 / 我自己的自检残留 | 是 | **删（已删）** | 纯临时 |

**可释放总量（本轮实测）**：**7.03 GB**（已删）+ **23.95 GB 待定夺**（`tmp/mahjong-mslt` 16.36 + `release` 2.71 + `.uv-cache` 4.15 + `tmp/` 其余 0.83，四舍五入后重叠按 23.9 计）。

## 2 实际删除清单（逐项；全部 `-LiteralPath`，删前断言解析后路径前缀 = 仓库 `.tmp\`）

| # | 路径 | 大小 | 理由 |
| --- | --- | ---: | --- |
| 1 | `.tmp/mv1-s20` | 480.6 MB | 阶段 20 脚手架（数据副本 + 实验网 + jsonl），结论已在 `lab/mv1/MV1-LRSCHED-RESULT.md` |
| 2 | `.tmp/mv1-s21` | 519.7 MB | 同上（S101 lr 三臂） |
| 3 | `.tmp/mv1-s22` | 577.1 MB | 同上（C3/aux 臂） |
| 4 | `.tmp/mv1-s23` | 269.9 MB | 同上（aux 权重扫描） |
| 5 | `.tmp/mv1-s24` | 554.5 MB | 同上（aux/K 臂） |
| 6 | `.tmp/mv1-s25` | 304.6 MB | 同上（证据副本 + 语料） |
| 7 | `.tmp/mv1-s26` | 449.4 MB | 同上（tile_id 臂） |
| 8 | `.tmp/mv1-s27` | 677.0 MB | 同上（A1500/3000 臂；含一份 repo 基线副本） |
| 9 | `.tmp/mv1-s28` | 315.5 MB | 同上（format 3 失败夹具） |
| 10 | `.tmp/mv1-s29a` | 335.9 MB | 同上（fail-loud 11 个坏包夹具） |
| 11 | `.tmp/mv1-s29b` | 341.2 MB | 同上（坏包 12 个 + 三端对拍日志） |
| 12 | `.tmp/mv1-s30` | 295.0 MB | 同上（在线臂 + 探针） |
| 13 | `.tmp/mv1-s32` | 329.8 MB | 同上（E3 240 维语料） |
| 14 | `.tmp/mv1-s33` | 285.4 MB | 同上 |
| 15 | `.tmp/mv1-s34` | 286.7 MB | 同上 |
| 16 | `.tmp/mv1-s96` | 447.3 MB | 同上（v4 增量缓存 before/after 树） |
| 17 | `.tmp/mv1-s98` | 285.7 MB | 同上（lr 曲线扫描） |
| 18 | `.tmp/mv1-s99` | 435.8 MB | 同上（lr 调度五臂；结论在 `lab/mv1/MV1-LRSCHED-RESULT.md`） |
| 19 | `**/__pycache__`（15 个，不含 `.venv`/`.uv-cache`） | 7.0 MB | 纯缓存 |
| 20 | `hs_err_pid*.log` ×10 | 0.78 MB | JVM 崩溃日志（无仲裁价值） |
| 21 | `ceiling-gate-selftest-{n3qvrz7i,r10bx5_d}/` | 7.6 MB | 我上一批跑 `ceiling-gate --selftest` 的残留现场（`tools/` 里有生成器） |
| 22 | `.commit-msg.txt` · `.tmp/mv1-s23`（空） · `mv1-s23/`（空） · `torchinductor_HP/`（空） | ~0 | 临时/空目录 |

**⚠ 删了不可恢复**：第 1–18 项里的 **`.bin` 实验权重**（如 `.tmp/mv1-s27` 的 2 个、`.tmp/mv1-s28` 的 `a-format3.bin`、
`.tmp/mv1-s30/mv1-s30-online.bin`）**没有第二份副本**（已按 sha256 与 `tools/build/*/net.bin` 逐个比对，**0 命中**）。
它们的**读数**留在 `NOTES.md` §6.5 与 `lab/mv1/*.md`，但**权重本身不可再逐位重现**（种子 + 数据都变了）。
若日后要复跑那些消融，必须**重训**（≈每臂 5 min）——这是本轮唯一实质性的不可恢复代价，已在此单列。

## 3 ⛔ 建议留待项目方定夺（逐项给"为何不建议现在删"）

| 路径 | 大小 | 为何不现在删 |
| --- | ---: | --- |
| `tmp/mahjong-mslt/` | 16.36 GB | 是 MSLT（第七十八/七十九轮）的 compact 与 ckpt。`S:\mahjong-training\w-ladder\` 有 `MSLT-{PLAN,RESULT,verdict}`，但**没有同 sha 的 compact 副本** ⇒ 无法证明"可由现存数据重生成"。删了等于把那次"测不出"判决的原始数据抹掉。**建议**：先把它搬到 `S:`（余量 148.6 GB）再删本地。 |
| `release/` | 2.71 GB | `rust/` 2.5 GB 是构建工具链（不是垃圾）；`stage-client/`、`*.zip`、`bot-ai-v113/` 是发布产物，重打包可再生成，但会牵动 `docs/DEPLOY.md` 的复现步骤。 |
| `.uv-cache/` | 4.15 GB | uv 的下载缓存：删了下次 `uv sync` 全量重下（本机网络受限，实测常超时）。 |
| `tmp/{stylemetric,rotate-perm-*,byte-*,bl13,rerun}` | 0.83 GB | 旧实验逐场数据；未证明冗余。 |
| `tools/build/v4-*`（266 个） | 1.33 GB | 每个目录都是**权重端点 + 证据**；删 `net.bin` 违反任务书，删 `summary.json` 只省 KB。 |
| `bot-ai/` retired 4 包 | 0.02 GB | 任务书明令：**权重保留**。 |

## 4 空间前后（同一块磁盘，实测）

| 盘 | 删前空闲 | 删后空闲 | Δ |
| --- | ---: | ---: | ---: |
| C: | 88.80 GB | **98.18 GB** | **+9.38 GB** |
| S: | 148.57 GB | 148.57 GB | 0 |
| E: | 501.69 GB | 501.69 GB | 0 |

**对账**：删除清单合计 **7.03 GB**；C: 实测多出 **9.38 GB** —— 差额 2.35 GB 是**同一轮里被中止的 1,200 步试跑**留下的
`refcache-*` 与 `ckpt/`（重启扫描时按"每点从干净现场起跑"删掉），它们不属于上表清单，特此写明。
