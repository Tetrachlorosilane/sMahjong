# 让 python/trainer 写 `S:\mahjong-training`：②/③ 两条路探明（2026-10-22·MV1-S56）

> 结论先行：**② 不支持**（schema 无键、代码无读取点）；**③ 可行**（工作区外 3.12 解释器与外部 `trainer.exe` 都能写 S:，`selfcheck` 1040/0 PASS）。
> ⚠ ③ 只在**当前 `danger-full-access` 会话**下成立，机制见 §3。

## 1. ② 沙箱策略：没有"额外可写根"（b）

源码取自 `app.asar` 内 `dsh/node_modules/@deepseek-ai/…`（抽到 `.tmp/mv1-s56/dsh-src/`，未改安装目录）：

| 位置 | 事实 |
| --- | --- |
| `dsh-sandbox-policy/lib/index.js:97-104` | `Config = object({ mode, workspaceRoot: string })`——**只有一个根**；L145 用 `session.header.cwd` 覆盖它，配置的 root 只对无 session 调用生效 |
| `dsh-sandbox-policy/README.md:146` | 原文：**"extra writable roots are not part of `SandboxExecutionPolicy`"** |
| `dsh-sandbox-local/lib/index.js:253-257` | Config 只有 `runnerCommand` / `runnerFailureSignatures` / `probeTimeoutMs` |
| `dsh-sandbox-local/lib/index.js:357-382` + `runner.js:75-92` | windows-acl 只传 `--workspace`/`--temp`；**其他 flag 一律 `unknown argument`、退出 127** |
| `dsh-sandbox/lib/index.js:166-173` | `writableRoots(policy)` = `workspaceRoot` + `/tmp` + `os.tmpdir()`，仅此三处 |

6 个沙箱包源码里 grep `writableRoots\|additionalRoots\|extraRoots\|allowPaths\|writeRoots\|writablePaths\|extraWritable`：**8 处命中全是同一个 `writableRoots()` 帮手，0 个配置键**。
⇒ 往 `- id: sandbox-policy` 的 `config` 加 `writableRoots:`/`additionalRoots:` **不会生效**（schema 无该键、代码无读取点）⇒ **不给 patch 片段**（给了是假绿）。
唯一的近亲 `sandbox-local.runnerCommand` 是**整体替换** confinement runner（等于自废强制），不是"加一个根"；想靠 `workspaceRoot` 指 S: 也不行——session cwd 永远优先。

## 2. ③ 实测三连（工作区外解释器）

`C:\Users\HP\tools\mj-py`（`--without-pip`；`home = S:\mahjong-training\tools\py312`；3.12.13）：

| 判据 | 结果 |
| --- | --- |
| 写 `S:\mahjong-training\.write-probe-ext.txt` | **OK**（9 B；写完逐项 `-LiteralPath` 删除） |
| `PYTHONPATH=<工作区 venv site-packages>` + `import torch` | **OK** `2.11.0+cu128`（`cuda.is_available()=True`） |
| `python\selfcheck.py`（默认数据根，**未设** `MAHJONG_DATA_ROOT`） | **1040 / 0 失败 / SELFCHECK PASS**，exit 0，48 s |

系统 Python 不可用：`py -0p` 只有 **3.14** 与 **3.11**；torch 是 **cp312**（`torch/_C.cp312-win_amd64.pyd`），3.11 实测 `import torch` → `OSError [WinError 126]` ⇒ **必须 3.12**；本机可用的 3.12 = `S:\mahjong-training\tools\py312\python.exe`（工作区外，历史先例）。
trainer：`trainer\build\trainer.exe` 复制到 `C:\Users\HP\tools\mj-trainer\trainer.exe`（单文件、无需 dll）后 `rules <corpus> shanten S:\…\.write-probe-trainer.txt` → **写入 OK（4 B，exit 0）**；同命令用**工作区内原 exe** → `写不了输出`、**exit 2**。

## 3. 机制：决定成败的是"解释器所在树"，不是当前 mode

本会话 token = **Medium**；父会话日志 `sandbox/mode` seq 1179 已切 `danger-full-access` ⇒ 现在**不是** token 在拦。
实测工作区内 3.12 进程完整性 = **Low（`S-1-16-4096`）**、`S:\…\py312` = **Medium（`S-1-16-8192`）**。
⇒ 根源是早先 `workspace-write` 给工作区树打的 **Low 完整性标签（no-write-up）**：随文件继承、切 mode 不撤销，从该树启动的 exe 只能写 Low 对象（工作区），写 S:/TEMP 都 `EACCES`。
⚠ 若会话切回 `workspace-write`，子进程还会继承 Low+受限 token，③ **未验证**（很可能不成立）。

## 4. 推荐方案与代价 ／ 回退

**推荐 ③**：训练/采集用 `C:\Users\HP\tools\mj-py\Scripts\python.exe` + `PYTHONPATH=C:\Users\HP\source\games\mahjong\python\.venv\Lib\site-packages`；trainer 用 `C:\Users\HP\tools\mj-trainer\trainer.exe`（不设 `MAHJONG_DATA_ROOT`，默认即 S:）。
代价：绑定 S: 上的 py312；**不能**用 3.11/3.14（torch cp312）；venv 无 pip（装包得另想办法）；**只在 danger-full-access 下成立**。
**回退（`.tmp` 绕法）**：`MAHJONG_DATA_ROOT=.tmp/<run>` 在工作区内跑，收尾用 Medium 进程（`pwsh` 或外部 python）搬到 `S:\mahjong-training`；代价 = 双份磁盘 + 人工搬运，优点 = **workspace-write 下也成立**（工作区始终可写）。
（②③ 不同时失败 ⇒ 按约定**不写** `SANDBOX-WRITE-PLUGIN-SPEC.md`。）
