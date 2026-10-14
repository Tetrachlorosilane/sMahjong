# RELEASE v1.18.0 —— 服务端包（含修复版自更新脚本）

| 项 | 值 |
| --- | --- |
| tag | `v1.18.0` —— **正式发布**（`draft=false` / `prerelease=false`），现为仓库 **Latest** |
| tag → 提交 | `50a73ec`（版本落点 1.18.0 + `docs/DEPLOY.md` §10）；本记录文件落在紧随其后的那次提交 |
| release | id **413083434** · <https://github.com/Tetrachlorosilane/sMahjong/releases/tag/v1.18.0> |
| 资产 | `sMahjong-server-v1.18.0.zip` · **440,238 B** · sha256 `651c53dce4a66447bfbac849aef748b651c0a9e0183479d7753ce2027e60a4fd` · asset id **641364396** |
| 远端 vs 本地 | 远端 `size=440238` + `digest=sha256:651c53dc…a4fd` **==** 本地 `Get-FileHash`；上传 HTTP 201、**0 次重试** |

**版本落点（1.14.0 → 1.18.0）**：`client/CMakeLists.txt:6` · `client/src/main.cpp:90`（本仓库客户端与服务端**共用一个版本号**，`tools/package-release.ps1` 取前者为默认并强制后者相等）· `docs/DEPLOY.md:201` 与 `:228` + 新增 §10 发版记录 · `server/pack/README.txt:56` · `server/pack/update.sh:7/31/32/64/75`。
⚠ 仓库里**没有 `server/pack/VERSION`** —— 那是打包时按 `$ver` 现写进包里的（包内 6 字节 `1.18.0`，原始字节 `31 2e 31 38 2e 30`）。
⛔ 刻意未改（历史记录 / 录下来的真实数据）：`NOTES.md` · `docs/BOT-AI.md:282` · `tools/release-fixtures/**` · `tools/update-pick-test.mjs`。

**包内容验收**（`tar -tvf` + 解包 grep）
- 5 个 `*.sh` 全 **`-rwxr-xr-x`**，其余 `-rw-r--r--`（`make-zip.mjs` 手写 zip，Unix 可执行位保住）。
- 包内 `update.sh` 是**修复版**：`releases?per_page=100`(L364) · `--include-prerelease`(L97) · 语义版本排序 `sv_cmp(r_tag[i], r_tag[chosen])`(L329) · `install_file`「先写 `./.<名>.new` 再 `mv`」(L523)。
- 服务端 `--selftest`：**1538 项 / 0 失败**（SELFTEST PASS）。

**⭐⭐ 旧脚本收敛证明**（WSL Ubuntu · bash 5.2.21 · **真 API** · 隔离临时目录，⛔ 未碰任何真实安装）
- 旧脚本 = 从 `v1.14.0` 发布包里解出的 `update.sh`，与 `git show 94573ab^:server/pack/update.sh` **逐字节相同**（7202 B · sha256 `80741b2e1fe3fa73805a4179f07e7a3c302ea3c9295fb8607b5c86b828a45c72`）。
- 旧规则：`GET /releases/latest` → tag = **`v1.18.0`** → 按 tag 拼出 **`sMahjong-server-v1.18.0.zip`** → `curl -sI -L` = **HTTP 200**（不是 404）。
- 反向对照：同一规则用在 `v1.16.0`（本版之前 `/releases/latest` 指的正是它）⇒ `curl: (22) … error: 404` —— 即报障原文。
- 旧脚本 `--check` rc=0，打印「远端 tag : v1.18.0 / 有新版本：v1.18.0」；整跑一遍**真下载**本包（sha256 一致）并进入安装段。
- ⚠ 整跑随后命中**旧脚本自身的缺陷**：`./update.sh: line 181: norm_tag: command not found`、**rc=127**，jar 已换（422,090 B）但 `VERSION` / `run/installed.sha256` **没写** ⇒ **半装**。历史记录同一根因当时观测到 rc=0（静默）；本次复现是 rc=127 —— 同因不同果，都是「`cp` 覆盖正在被 bash 读取的 inode」。
- **新脚本自证**：`--list` ⇒ ★本次选中 `v1.18.0` · `--check` ⇒ 有新内容 · 真下载 ⇒ **sha256 校验通过** + **已安装 v1.18.0** · 再 `--check` ⇒ **已是最新（tag 与资产 sha256 都一致）**；四步 rc 全 0，`VERSION` / `run/installed.tag` = `v1.18.0`。

**边界**：既有 release 与全部权重**只读未动**（本版是新增发布）· 未改 `AGENTS.md` · 未碰另一任务的产物（`Action.java` / `SelfTest.java` 等已随 `050ba4b` 入库）· 打包用的 jar 在 `050ba4b` 的干净 worktree 里构建（避免把并发任务的未提交改动带进发布包）。
