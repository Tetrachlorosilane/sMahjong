# 自动化工具总入口（减少人机回路）

> **一句话**：这个文件是**本轮会话里"手工反复做"的那些动作**的唯一入口 —— 每条都是
> **① 明确的判据 ② 自带自检 + 负向自证 ③ 不静默降级 ④ 一行可复制调用**。
> 判据与命令的分工不变（`AGENTS.md` §8）：验证**五层的判据**在 `AGENTS.md` §4、
> 每个既有脚本的注解在 `NOTES.md` §4；本文件只管**"哪一条命令替掉了哪一串手敲"**。

## 0. 先跑这一条（覆盖最广）

```powershell
pwsh -File tools\all-checks.ps1            # 全跑；-SkipL2 / -SkipL1 / -SkipPython / -SkipStatic 可分层跳过
```

| 项 | 内容 |
| --- | --- |
| 判据 | 每层**退出码 + 输出里的通过标记**（如 `SELFTEST PASS`）：退出码 0 而没标记 ⇒ **FAIL**（"退出码 0 不算证据"）；缺前置（jar/exe/venv）⇒ **FAIL**，⛔ 不静默降级成 SKIP —— 只有显式 `-Skip<层>` 才是跳过，且跳过会**打在表里** |
| 输出 | 紧凑 PASS/FAIL 表 + 每层原始日志与 `summary-<时间>.json`（落 `python\.tmp\all-checks\`，已 gitignore） |
| 退出码 | 0 = 全绿 / 1 = 有 FAIL / 3 = 只有「带重试的通过」（见 §1，`-AcceptRetryPass` 才当 0） |
| 自检 | `pwsh -File tools\all-checks.ps1 -Selftest` ⇒ 4/4（**其中 3 条是负向自证**：退出码非 0、退出码 0 但无标记、缺前置） |

## 1. JVM 偶发崩溃：重试与记录（`tools/jvm-guard.mjs`）

```powershell
node tools\jvm-guard.mjs --out python/.tmp/jvm-guard --mark 'SELFTEST PASS' --port 10086 -- `
     java -jar server/build/mahjong-server.jar --port 10086
node tools\jvm-guard.mjs --show-signature hs_err_pid42620.log     # 事后补签名，不重跑
node tools\jvm-guard.mjs --selftest                                # 5/5（3 条负向自证）
```

| 项 | 内容 |
| --- | --- |
| 为什么 | 本机偶发 `EXCEPTION_ACCESS_VIOLATION @ c2 mahjong.rules.Shanten.dfs`（`NOTES.md` §4 L1 注解已登记，2026-10-07 20 分钟内撞 2 次） |
| 用途扩展（2026-10-11） | **重试 + 记录崩溃签名，并作为不稳定环境的证据来源**（签名 / elapsed / 局号可回填 `NOTES.md` §4 的硬件根因链）；⛔ 它**不能**规避硬件错误（WHEA 处理器核 corrected machine check + 意外重启），只把"偶发失败"变成**可数的证据** |
| 判据 | 一次尝试"干净" ⇔ 退出码 0 **且** 没新落 `hs_err_pid*.log` **且**（给了 `--mark` 时）输出里有标记。⛔ **重试不算通过**：状态只有 `PASS` / `RETRIED-PASS`（退出码 **3**）/ `FAIL`，汇总**永远**打印**原始失败次数**与全部签名（崩溃帧 / elapsed / 局号） |
| 附带 | `--port` 跑完按端口杀 + **复查残留监听**（收尾用）；`--echo`… 不需要，逐次 stdout/stderr 落盘 |

## 2. 推送核对（`tools/verify-push.mjs`）

```powershell
node tools\verify-push.mjs                    # 推完就跑；--handover 追加跑 handover-facts --check
node tools\verify-push.mjs --selftest         # 5/5（3 条负向自证）
```

| 项 | 内容 |
| --- | --- |
| 判据 | **内容等价看 tree、位置看 sha**：远端头 sha == 本地 `HEAD`？远端提交 tree == 本地 `HEAD^{tree}`？`origin/<分支>`（**本地跟踪 ref**）是否滞后？⛔ 拿不到远端 tree ⇒ `UNKNOWN` + 退出码 2，**不印通过** |
| 通道 | `git fetch`（失败不致命）→ `git ls-remote` → **被网络拦时回退 GitHub API**（`GET /repos/<o>/<r>/git/ref/heads/<b>`，tree 取 `/git/commits/<sha>`） |
| 退出码 | 0 = tree 一致（内容等价） / 1 = tree 不一致 / 2 = 无法判定 |

## 3. 配对评测（`tools/paired-eval.mjs` + `tools/paired-eval-bridge.py`）

```powershell
node tools\paired-eval.mjs collect --policy teacher,net:ckpt/a.bin,first,pass `
     --games 200 --seed 40970001 --run-out <数据根>/paired/src/<标签>
node tools\paired-eval.mjs --base <数据根>/paired/src/a --cand <数据根>/paired/src/b `
     --out <数据根>/paired --md <数据根>/paired/a-vs-b.md
node tools\paired-eval.mjs --selftest          # 桥内 23 项 + 2 条负向自证
```

| 项 | 内容 |
| --- | --- |
| 判据 | 逐场配对 Δ/SE/t/95%CI/n **+ 方向敏感的伴随量判定**；两条采集的 `seed_base` 与学生席对不上 ⇒ `_check_pairing` **直接拒绝出数**（⛔ 不给"看着像配对"的数） |
| ⛔ 只有一份口径 | 统计量与方向表**全部**借 `tools/w-ladder.py`（`PAIR_AXES` / `_paired_delta` / `_check_pairing` / `_student_per_game` / `companion_verdict`），桥用 `importlib` 加载**同一个文件**；本工具**一个数都不自己算**（`AGENTS.md` §6.5「⛔ 不要复制出第二套规则」） |
| 产物 | `paired/<标签>.json`（含逐场 Δ 明细）+ `<标签>.md`；`--out` 落在仓库受管目录（`docs/` `server/` `client/` `tools/` …）**直接拒绝写** |
| 退出码 | 0 = 全部未越界 / 3 = 有伴随量越界（与 `w-ladder.py quality` 同义） / 1 = 出错 |

## 4. 本轮**没有**新增（及其理由）

| 候选 | 为什么不新增 |
| --- | --- |
| 发布 bot 包一键（`publish-bot.ps1`） | `python -m mahjong_ml.packbot --out bot-ai --builtin teacher --zip release\bot-ai` **已经是**"装配 → 落 `bot-ai/` → 打 zip（内层 `bot-ai/<包名>/`）"的一条命令（`docs/BOT-AI.md` §6）。再包一层只会多一处需要同步的默认值；"Java 验证 + 重试"这件真正缺的事已经由 §1 的 `jvm-guard` 提供（`--mark`，可套在 `--selfplay` 上） |
| 预注册生成（`prereg-new.mjs`） | ⛔ **模板不存在**：全仓 `git ls-files | grep -i prereg` 为空（只有 `docs/HANDOVER.md`/`NOTES.md` 提到 `PREREGISTRATION-*.md` 的**产物**）。要"从模板复制"就得先造模板 —— 那是**新增口径**（不是自动化），得先问 |
| 陈旧计数扫描（`doc-stale-counts.mjs`） | 口径已经在 `NOTES.md` §4 写死："当前 N 项只在 `AGENTS.md` §4 记一处，别处写指针"。也就是说**别处本来就不该有数字**，"扫 N 项是否一致"能抓的只剩 AGENTS §4 自己那一处；`doc-refs-check` 已经管住了 §引用与索引，收益/成本比不划算（真要做，判据应该是"全仓除 AGENTS §4 外不得出现 `通过 N 项`"，那属于改文档纪律） |

## 5. 判据一览（照抄用）

| 脚本 | 自检 | 负向自证 |
| --- | --- | --- |
| `tools\all-checks.ps1 -Selftest` | 4/4 | ② 退出码非 0 ⇒ FAIL；③ 退出码 0 但无 `SELFTEST PASS` ⇒ FAIL；④ 缺前置 ⇒ FAIL（⛔ 非 SKIP） |
| `tools\jvm-guard.mjs --selftest` | 5/5 | ② 一直崩 ⇒ FAIL（不许出现 PASS）；③ 退出码 0 但新落 `hs_err` ⇒ FAIL；⑤ 被包装命令里的 `--selftest` 不许触发 guard 自己的自检 |
| `tools\verify-push.mjs --selftest` | 5/5 | ③ tree 不同 ⇒ `TREE_MISMATCH` 退出码 1；④ 拿不到远端 tree ⇒ `UNKNOWN` 退出码 2；⑤ 取不到远端头 ⇒ `NO_REMOTE` 退出码 2 |
| `tools\paired-eval.mjs --selftest` | 桥内 23 项 | 采集目录不存在 ⇒ 非 0 退出；落点选 `docs/` ⇒ 拒绝写；桥内：未登记方向的量 ⇒ **当场报错**（⛔ 无缺省兜底） |
| `tools\v4-forward-gpu.py --selftest` | 16/16 | 权重（不存在 / 空 / 截断 / 魔数坏）⇒ 报错；张量（行数不齐 / `nlegal` 越界 / tile 非 34 行 / `nlegal=0`）⇒ 报错；**`cuda` 不可用 ⇒ 报错不回落 CPU** |

## 6. v4 前向（GPU 批量 · `tools\v4-forward-gpu.py`）

```powershell
python\.venv\Scripts\python.exe tools\v4-forward-gpu.py --weights <net.bin> --rows <rows.npz|紧凑集目录|python\tests\golden\forward-v4.bin> --device cuda --batch 512 --repeat 3 --out logits.jsonl
python\.venv\Scripts\python.exe tools\v4-forward-gpu.py --weights <net.bin> --traces <轨迹目录> --policy "<学生策略串>" --where-kind claim --rows-out rows.npz   # 物化行级张量（内部只调 blocks.assemble）
python\.venv\Scripts\python.exe tools\v4-forward-gpu.py --selftest
```

| 项 | 内容 |
| --- | --- |
| 判据 | 同权重同输入时 **argmax 逐行相同** + 逐元素 `maxΔ ≤ tol`；`--traces` 与 `--rows` 两条输入路径必须**逐位相同**；`--repeat` 每次 argmax 必须逐行相同（⛔ 不同就是"同一份输入两个答案"）；`--device cuda` 在 `cuda.is_available()=False` 时**报错不回落**；按 `legal` 条数分桶（补 0 行会经 `u.mean`→`policy_gate` 改 argmax，与逐行语义**不等价**） |
| ⛔ 特征侧不重实现 | 只消费**既有行级张量**；唯一的"轨迹→张量"通道（`--traces`）只调 `mahjong_ml.v4.blocks.assemble` + `v4.traces.sidecar_dict`（降级**一律报错**） |
| ⚠ 现状（2026-10-11 R1） | **判据不过 ⇒ 未推广**：1/13,408 行 argmax 翻转（根因 = `python/mahjong_ml/features.py:287-294` 对 `kan:daiminkan:a+b+c` 只拆 2 段，与 `Action.java:403-413` 不一致）；`maxΔ` 超 1e-5 属量纲（\|logits\| 中位 29.5 ⇒ 1 ulp ≈ 1.3e-5）。**热态 20,628 行/秒（≈57.6× 于 C++ 逐行）**，CUDA 冷启动 15.999 s 是一次性预热。详见 `NOTES.md` §6.5 末 |
| 退出码 | 0 = 通过 / 1 = 判据不过（夹具参考 logits 不符） / 2 = 用法、环境或输入坏了 |

