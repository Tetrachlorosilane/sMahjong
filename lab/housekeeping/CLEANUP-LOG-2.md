# 清理台账 2 —— 只删派生/缓存/可重建（2026-10-12，第二轮；墙钟 ~35 min）

> 纪律：逐个**绝对路径**核对 → 查引用 → 查占用进程（`python|trainer|java|node|mahjong` 全无）→ 记账才删。⛔ 无通配符一把梭；⛔ 零重负载（没跑训练/采集/闸门/selftest/selfcheck，只读 + 删 + 统计 + git）。
## 1 逐项账

| # | 绝对路径 | 大小 | 删除依据 |
| ---: | --- | ---: | --- |
| 1 | `S:\mahjong-training\gate\v4-wl-p301-AB` | 12,003 文件 / 32,100.61 MB | P3-01 A-vs-B 闸门 raw（2 套牌山 × 6,000 场 `g*.jsonl`）。读数全在：`_p301-evidence\{p301-reading-AB-rank_points,p301-reading-AB-deals,p301-reading-AB-FINAL}.json` + 同目录 `verdict.json`(820 B) + `p301-gate-AB.log`(3,098 B) |
| 2 | `S:\mahjong-training\gate\v4-wl-p301-A-selfcheck` | 12,003 文件 / 31,958.02 MB | A 端点 vs 起点网闸门 raw（6,000 场 ×2）。读数在：`_p301-evidence\p301-reading-A-vs-init.json`(2,514 B) + `verdict.json`(854 B) + `p301-gate-A-vs-init.log`(3,136 B) |
| 3 | `S:\mahjong-training\gate\v4-wl-p301` | 0 文件 / 0 B | 早先尝试留下的空目录（`p301-gate.log:9` 记其路径） |
| 4–7 | `S:\...\w-ladder\{_p2-11-work,_p2-11b-work,_p2-11c-work,_expA-work}` | 30 文件 / 33.76 MB | 只剩 `hands_*/meta_*.json` 逐小局台账；其 `rows_*.jsonl` 与 `stu/*.jsonl` **早已空**（`NOTES.md:3872` 已记载"冻结读数输入已不可复算"）⇒ 台账已非可重算入口。消费方产物 `_p2-11c-domdiff-*.json`/`_p2-11c-domains.jsonl`/`_p2-11c-dforward-*.json`/`_expA-*` 全保留 |
| 8 | `S:\mahjong-training\compact\v4-wl-win-cal-c1-g01` | 0 B | 空目录（其 raw 仍在 `raw\v4-wl-win-cal-c1-g01`） |
| 9–10 | `<repo>\python\.tmp\{scroot,selfcheck}` | 183 文件 / 49.23 MB | 自检 scratch 根与 fixture（`ckpt\*/model.pt`、`dataset/`、`v4-*-src/`），`selfcheck.py` 重建；`.gitignore:124` |
| 11–13 | `<repo>\release\{stage-bot,stage-bot-v113,stage-server}` | 42 文件 / 22.17 MB | 发布打包**暂存**目录，`tools\package-release.ps1:52` 每次重建；`.gitignore:112` |
| 14–15 | `C:\Users\HP\AppData\Local\Temp\{q2ittc5x,VSTelem}` | 1,054.86 MB | Win11 SDK 安装器残留（今日 02:34–02:45 重新生成）+ VS 遥测临时；⚠ VSTelem 剩 3.03 MB 被占用留下（纯缓存，无影响） |
| 16–19 | `C:\Users\HP\AppData\Local\Temp\{preamble-384b25.pch, PCL, pkg-32VBgE, pkg-AYfLCC}` | 82 文件 / 40.39 MB | MSVC 预编译头 / 安装器暂存残留 |

**总计（逐项实测之和）= 65,034.49 MB = 63.51 GB**。⚠ 上一轮已清过的项本轮**复核为空/无可删**：`uv\cache`·`pip\cache`·`npm-cache`（absent）、`C:\Users\HP\mahjong-tmp`（absent）、`NVIDIA\DXCache`（系统已重建，仅 18.3 MB）、`trainer\build`（除 `wk-8` 外无子目录）。⛔ `_p301-evidence` 里**没有**大二进制（73 文件，最大 0.32 MB）⇒ 该项无可删。

## 2 三盘前后（`Get-PSDrive`，GiB）

| 盘 | 清理前可用 | 清理后可用 | 净释放 |
| --- | ---: | ---: | ---: |
| `S:` | 60.90 | **123.50** | **+62.60** |
| `C:` | 130.80 | **131.90** | +1.10 |
| `E:` | 546.10 | 546.10 | +0.00（本批没在 E: 上删字节） |

**三盘合计净释放 = +63.70 GiB**（与逐项之和 63.51 GB 一致，差 0.19 = 舍入 + 测量窗口内并发写入）。

## 3 待用户拍板（本批**一项没删**）

| 项 | 现状（实测） | 可省 | 我的建议 |
| --- | --- | ---: | --- |
| **A 臂 raw** `S:\...\raw\v4-wl-p301-A-g01` | 36,001 文件 / 36.38 GB / **12,000 场**（`g0..g11999` 无缺口，`summary.json.games=12000`，`seed_base=52920002`） | 36.38 GB | **保留**：数据来自初始策略 ⇒ 将来重做 A 仍可用（省 ~66 min 采集） |
| **B 臂六轮 raw** `raw\v4-wl-p301-B-g01..g06` | 36,006 文件 / **36.61 GB**（6.15+6.04+6.09+6.13+6.09+6.11） | 36.61 GB | **推荐删**：P3-01 已结案且其自变量是错的（六轮同一份起点网数据）⇒ 对本预注册不可用，仅剩"链式 init"探索价值 |
| `S:\...\compact\v4-heads-g01` | 47 文件 / 5.82 GB —— ⚠ **唯一副本**（其 raw 已不存在） | 5.82 GB | heads 线若已结案**可删**；否则保留 |
| 他人/别会话 `S:\...\style-reward\**` | 8,148 文件 / 21.20 GB（`g*.jsonl`） | 21.20 GB | **不是我们的产物** ⇒ 维持上一轮纪律**不碰**，除非你明确授权 |

**额外发现（同样未删，需你授权）**：`E:\p301-smoke`（914 文件 / **4.64 GB**，P3-01 诊断 smoke 的 compact+raw+ckpt，`P3-01-RESULT.md §6` 声称"诊断产物已删"但这份漏了）· `S:\...\gate\{selfcheck-weak-to-strong,selfcheck-strong-to-weak}`（402 文件 / 1.57 GB，非 P3-01 的旧闸门 raw）· `E:\mahjong-training`（40 文件 / 0.11 GB，P3-01 残留数据根，多为空 ckpt 目录）。

## 4 明确**没删**的关键数据（证明没碰来之不易的 / 别人的 / 契约）

- **A 臂 raw 逐字未动**（现测：`g*.jsonl` **12,000** 个、目录 **36,001** 文件 / **36.38 GB**、`summary.json.games=12000`）· **B 六轮 raw 全在** · `raw\**` 其余（`p2-11c`/`v4-golden`/`expA-div`/`v4-ceiling-common`/`v4-wl-win-cal-c1-g01`/`v4-wl-fold-any-c1-c1-g01`）未动。
- **他人产物** `style-reward\**`（8,148 文件 / 21.20 GB）未动 · `w-ladder\traces\v4-wl-fold-c1-g01`（**1,001** 个，有意保留）未动 · `compact\v4-heads-g01`（5.82 GB 唯一副本）未动。
- **P3-01 全部读数文本**：`_p301-evidence\**`（73 文件 / 0.001 GB，19 个 `.json`）**一个没删**；`S:\...\gate\**` 里各 `verdict.json`/`summary.json` 与 `final-*`/`v4-expert-*`/`v4-league*` 历史闸门目录未动。**数据根工具**：`S:\...\tools\trainer\trainer.exe`（+ 其余 3 个 exe）· `S:\...\tools\**`（3,412 文件）未动；仓库 `tools\build\**`（端点权重）、`python\.venv`、`release\rust`(2.49 GB)、`release\bot-ai`、`RELEASE-*.md`/`assets-*.txt`/digest 文本、`trainer\build\wk-8` 与 `trainer\build` 全部根文件未动。
- ⛔ 未碰：`raw\**`（本批**没删任何 raw**）、`style-reward\**`、`tools\build\**`、`python\.venv`、任何 `git` 跟踪的东西、`C:\Windows`、`C:\Program Files`、用户文档/下载/照片、DSH 会话数据（`%TEMP%` 里 `dsh-*`、`*.tmp.node` 等 DSH 运行时**全部保留**，只删上面 6 项非 DSH 残留）。

## 5 仓库

`git status --porcelain` = **空（干净）**；HEAD = `2eec879` = `@{u}`（0/0）。**本批没有改仓库任何文件 ⇒ 不提交**（删掉的 3 个 `release\stage-*` 与 `python\.tmp\*` 都在 `.gitignore` 内）。
