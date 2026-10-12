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

## 4. 其它候选：**没有**新增（及其理由）

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
| `tools\ceiling-gate.py --selftest` | 32 条检查（`say` 行；另有 55 条探针行、8 条**故意红**的负向探针） | 缺目录（两文件都缺）⇒ 探针必红、族数 0；缺 `per_game` ⇒ 既有 `load_run` 当场报错；缺权重 / 两臂同权重 / 与旧 `verdict.json` 复算对不上 / 零训练自证取不到 ⇒ **`ERROR` + 退出码 2**；单族（1 族）不许判「过」；墙钟超预算不许判「过」；容忍带 < MDE ⇒ 标 `usable_as_veto=false` 且**不判破**（tol ≥ MDE 才判破）|
| `tools\overlap-orchestrator.py selftest` | **8/8** | ① 未知 kind/phase ⇒ 拒（fail closed）；② 命令行探针取不到数据 ⇒ **FAIL/拒跑**；③ 闸门不过 ⇒ **一条命令都不打**（桩 marker 作证）；④ 两文件都缺的比较 ⇒ **必红**（假绿守卫）；⑤ `--chunk<=0` 是坏值、预算用尽置 **0**（⛔ 非 −1）；⑥ 收尸删完**复验消失**、被占用 ⇒ **exit 5**；⑦ 枚举自检不过（令牌限域）⇒ **FAIL/拒跑**；⑧ 某一相非 0 退出 ⇒ **exit 7**（⛔ 不被"污染 6"吃掉）、`own_wall` 不被别的相虚大（见 §8） |
| `tools\v4-forward-gpu.py --selftest` | 45/45 | 权重（不存在 / 空 / 截断 / 魔数坏）⇒ 报错；张量（行数不齐 / `nlegal` 越界 / tile 非 34 行 / `nlegal=0`）⇒ 报错；**`cuda` 不可用 ⇒ 报错不回落 CPU**；⑤ `--rows <npz>` + `--index-file` 在**没有 `steps` 列**时也必须出文件（旧实现在 `forward` **之后**才 `TypeError`，`[fwd]` 照打而 `--out` 的文件不存在）；⑥ **紧凑集/分块负向**：目录不存在（**本批新判据：先断存在性**）/ 缺列文件 / 列文件截断 ⇒ 报错，`--chunk 0` 与 `--chunk -1` ⇒ **exit=2 且不留半截输出**，分块区间越界/空块 ⇒ 报错，`--materialize-all` vs `--chunk 1` 输出**逐字节相同**；⑦ 分块 == 全量（chunk 1/2/3/7/33/100 两档口径，见 §6） |
| `tools\v4-batch-serve.py --selftest` | 26/26 | **乱序的批计划 ⇒ 报错不许提交**（批内乱序 / 批间乱序 / 中间批少一行）；行对齐（`row` 越界 / 重复 / 未覆盖 / `(game,step)` 重复）⇒ 报错；`K>行数` / `K=1` / `不整除`（`keep`/`drop`/`error`，含"`drop` 必须能过守卫"）/ `K=0` 行为逐条断言；**`cuda` 不可用 ⇒ 报错不回落 CPU** |
| `tools\v4dforward-gpu.py`（评估路径的等价替换，无自检，靠与 `trainer v4net` 逐行对拍） | — | `--select-from` 里重复键 / 键在 `--traces` 找不到 / 枚举行数与 `materialize` 不等 ⇒ **当场报错**（⛔ 不"看着差不多就往下算"）；⚠ 选行**必须按 `trace_files` 的规范序**（数值序），字典序会让两边整片错位而 flip 数仍然相同 |

## 6. v4 前向（GPU 批量 · `tools\v4-forward-gpu.py`）

```powershell
python\.venv\Scripts\python.exe tools\v4-forward-gpu.py --weights <net.bin> --rows <rows.npz|紧凑集目录|python\tests\golden\forward-v4.bin> --device cuda --batch 512 --repeat 3 --out logits.jsonl
# 紧凑集（大输入）：**分块物化**是缺省 —— 一次只在内存里放 --chunk 行（缺省 100000），跑完即释放；`--limit` 真的夹住
python\.venv\Scripts\python.exe tools\v4-forward-gpu.py --weights <net.bin> --rows E:\mahjong-training\compact\<集> --split train --limit 200000 --chunk 100000 --device cuda --out logits.jsonl
python\.venv\Scripts\python.exe tools\v4-forward-gpu.py --weights <net.bin> --rows <紧凑集> --materialize-all --out old-path.jsonl   # 旧路径（一次全物化）—— 只给小输入 / 对拍基线
python\.venv\Scripts\python.exe tools\v4-forward-gpu.py --weights <net.bin> --traces <轨迹目录> --policy "<学生策略串>" --where-kind claim --rows-out rows.npz   # 物化行级张量（内部只调 blocks.assemble）
python\.venv\Scripts\python.exe tools\v4-forward-gpu.py --selftest
# 评估路径（`_p2-11c-dforward.py` 一族）的**等价替换**：只换前向那一步，行序与读数口径都不变
python\.venv\Scripts\python.exe tools\v4dforward-gpu.py --traces <该臂原始轨迹目录> --select-from <stuD/<arm>__<cond>> --weights <net-b> --device cuda --out logits.txt --out-json reading.json
# **确定批跨局前向**（argmax 型自对弈上 GPU 的"前向那一格"）：规范序 (game,step,seat) → 固定 K 的批
python\.venv\Scripts\python.exe tools\v4-batch-serve.py --weights <net.bin> --rows rows.npz --keys-from-traces <轨迹目录> --batch 512 --device cuda --margins --out logits.jsonl --manifest plan.json --compare cpp-v4net.txt
python\.venv\Scripts\python.exe tools\v4-batch-serve.py --selftest
```

| 项 | 内容 |
| --- | --- |
| 判据 | 同权重同输入时 **argmax 逐行相同** + 逐元素 `maxΔ ≤ tol × max(1, \|参考值\|)`（`TOL_REL = 1e-4`，见那里的量纲依据；⛔ argmax 这条不放）；`--traces` 与 `--rows` 两条输入路径必须**逐位相同**；`--repeat` 每次 argmax 必须逐行相同（⛔ 不同就是"同一份输入两个答案"）；`--device cuda` 在 `cuda.is_available()=False` 时**报错不回落**；按 `legal` 条数分桶（补 0 行会经 `u.mean`→`policy_gate` 改 argmax，与逐行语义**不等价**） |
| **分块物化 / 内存上界**（`--rows`，本批新增） | 判据 = **峰值常驻不随行数增长**：663,176 行与 66,317 行同一量级（旧路径 663k 行 ⇒ **15.1 GB** 常驻，全量读数不能与训练共存）。`--chunk N`（缺省 **100000**，≈2.26 GB 块缓冲）跑完一块**立刻释放**；⛔ `N ≤ 0` 是**坏值**（exit=2），**不是"不限"**（`AGENTS.md` §6.5 第三条）；`--limit` **真的夹住**（旧实现在 `--rows <紧凑集>` 上根本没夹）；`--materialize-all` = 旧路径（小输入/对拍）；`--rows` 分块 == 全量：**逐行 argmax 相同**（跨块大小，实测 0 处不符）+ 同批划分下 logits 逐位/`≤tol`（⚠ **跨块大小的 logits 逐位相同做不到**：批大小会改 fp32 归约顺序，实测相对差 ≤6e-8）。语义与实测见 `docs/TRAINER-CPP.md` §6.28 |
| ⛔ 特征侧不重实现 | 只消费**既有行级张量**；唯一的"轨迹→张量"通道（`--traces`）只调 `mahjong_ml.v4.blocks.assemble` + `v4.traces.sidecar_dict`（降级**一律报错**）。`tools\v4dforward-gpu.py` 同理：它**不重实现前向**，只做选行/对齐/记账 |
| **确定批**（`tools\v4-batch-serve.py`） | 判据 = **批成员与顺序只由键表决定**：`batch(b) = 规范序[b·K, (b+1)·K)`，规范序 = `(game, step, seat)` 字典序（键必须唯一，⛔ 不用 `row` 兜底）；提交前 `assert_canonical` 逐行比对，`--shuffle-plan`（负向控制）必须被拦成退出码 2；输出**按行号回填**（`--out-order row`）⇒ 逐行消费者读第 k 行拿到的就是第 k 行的答案；`K` 组内仍按 `legal` 条数分桶；`--device cpu` 与 `--device cuda` **同一份代码**；⚠ 吞吐的分子是 **`served`（真正前向的行数）**，`--allow-subset` 时不许拿张量行数顶替 |
| ⚠ 现状（2026-10-11 **R2**） | **五条判据全过 ⇒ 已切到 GPU**。R1 的两条不过都修掉了：① 真 bug = `features.py` 的 `action_parts` 对 `kan:daiminkan:a+b+c` **既没拆整个键、也没填主牌槽**（契约是 Java `Action.parse` 拆整个键得 3 段 + `Action.kan(kind, tile, hand)` **同时**带 `tile`/`tiles`，`Features.candidate` 两处都读）⇒ 51/13,408 行该候选 logit 差 4.4~10、1 行 argmax 翻转；② `1e-5` 是**绝对**阈值而 \|logits\| 中位 29.5 ⇒ fp32 1 ulp ≈ 1.3e-5（比阈值还大），改成**相对** `maxΔ ≤ tol × max(1,\|ref\|)`、`tol = 1e-4`（= 仓库对**同一条前向**既有的夹具 tol）。**R2 实测**：argmax **13,408/13,408**、相对 maxΔ 最大 **1.502e-05**（超限 0 行）、51 行大明杠候选格 \|Δ\| ≤ **1.53e-05**、GPU↔CPU argmax 13,408/13,408（相对 maxΔ 1.192e-05）、P5 夹具已补 `kan:` 覆盖 ⇒ **11 例**、两条路径 argmax 11/11。CUDA 冷启动 ≈16 s 是一次性预热（⛔ 不摊进吞吐）。详见 `NOTES.md` §6.5（R2 段） |
| 退出码 | 0 = 通过 / 1 = 判据不过（夹具参考 logits 不符） / 2 = 用法、环境或输入坏了 |
| **在线确定批**（`trainer.exe selfplay --batch-forward K --batch-games L [--batch-tail keep\|drop\|error] [--batch-order canonical\|arrival] [--batch-plan F.json]`，2026-10-11 **R4**） | 与上面那个**离线**工具的规范序**不是同一个东西**（离线是按场串行的全序，在线不可能复现）：在线 = **屏障最小 K 键**（一波 = 全部在场 worker 都阻塞在自己那一个询问上，取未决键里最小的 `min(K,\|未决\|)` 个）；守卫 `assertCanonical` 在**前向之前**拦"批内乱序 / 跳过更小的键 / 键重复" ⇒ 整波硬失败退出码非 0；`--batch-order arrival` = **故意红的负向控制**（必红）；判据 = 同 seed + 同 K + 同 L ⇒ **同批计划指纹**（`--batch-plan` 落盘）。开关关着时路径逐位不变。见 `docs/TRAINER-CPP.md` §6.26 |

## 7. 便宜 ceiling 闸门（`tools\ceiling-gate.py`，BL-03）

```powershell
python tools\ceiling-gate.py --tag <闸门标签>                      # ① 零采集：复算既有 gate\<tag>\s*\summary.json
python tools\ceiling-gate.py --family <标签>=<base目录>;<cand目录>   # ② 零采集：两条既有采集按**学生席**逐族配对（可重复）
python tools\ceiling-gate.py --collect --tag <标签> --wall <族seed> --games 500 --workers 8 `
     --base-net <现任 net.bin> --cand-net <新臂 net.bin> --table <def7>,<win2>,<atk3>    # ③ 新臂自采（⛔ 不训练）
python tools\ceiling-gate.py --selftest                            # 离线自检（32 条检查 + 负向红证；不采集、不出网）
```

| 项 | 内容 |
| --- | --- |
| 产物 | `verdict.json`（主口径 Δ/SE/t/CI · 套间散布/CI 半宽 · **实际 MDE** · 护栏 · 安慰剂 · 均值回归 ρ · 判决 + 口径声明 + 探针账 + 零训练自证） |
| 退出码 | **0 = 过** / **3 = 不过** / **4 = 测不出** / **2 = ERROR**。⛔ **fail-closed**：目录 / `summary.json` / `per_game` / 权重 / 进程扫描任一条取不到 ⇒ `ERROR` + 非 0（⛔ 不许"取不到就当干净"） |
| ⛔ 只有一份口径 | 统计量**全部借** `mahjong_ml.eval`（逐场取值 / 配对 / 自助 CI / 符号检验 / 合并 / 随机效应 / `required_n`）· `mahjong_ml.v4.gate`（`pooled_diffs` / `guard_row` / `decide`）· `mahjong_ml.v4.arena`（`judge_pair` / `judge_series`）；本工具只补三个**既有实现里没有**的读数：**实际 MDE**（**反解** `required_n`，⛔ 不写 z 公式）、均值回归 ρ、安慰剂对照 |
| 判决 | 过 ⇔ 主口径 CI 下界 > 0 ∧ **可用**护栏未越界 ∧ 安慰剂不显著 ∧ 族数 ≥ `--min-walls`（缺省 2）∧ 各族逐族为正 ∧ 各族是**同一对权重** ∧ \|ρ\| < 0.9（**可识别**时）∧ 墙钟 ≤ `--budget-min`（缺省 30）；不过 ⇔ CI 上界 < 0 **或**可用护栏确凿越界 |
| 护栏 | 容忍带 **< 实际 MDE** ⇒ 标 `usable_as_veto=false`（**不得作为否决依据**，天生必破 = 假警报），⛔ **不据此判破**；只有 `tol ≥ MDE` 的护栏越界才判「不过」（AGENTS.md §6.5 第七十一轮） |
| 零训练自证 | 起跑 / 收尾各扫一次全部进程的**命令行**；判据 = **解释器镜像（`python*` / `trainer*`）∧ 训练特征**两个条件同时成立（单看特征词会误报：DSH 自己的命令行里就带着本会话的任务文本）⇒ 命中即 `ERROR`；采集只走 `style-vector.py`（⛔ 无 PPO / pretrain / v4 loop） |
| ⚠ ρ 的可识别性 | `--gate-dir` 的 `a,a,b,b` 对开 + **零和指标** ⇒ `Δ` 与基准**机械地** −1 相关（实测 −1.000000）⇒ 判 `identified=false`、**不作判据**；要真 ρ 用 `--family` 口径（同座位、同牌山、只换权重；实测 −0.693） |

## 8. 重叠编排器（`tools\overlap-orchestrator.py`）

> **一句话**：把"两条互不依赖的相并跑"从**数据根的三份手写脚本**（`b1/b2/b3b-overlap.py`，三者同族演进）
> 收敛成**仓库内一个工具 + 七闸门 G1–G7**；数据根那三份**保留**、文件头各加一行指向本工具。

```powershell
python tools\overlap-orchestrator.py selftest --out python\.tmp\ov-selftest.json          # 8/8（含 5 条负向）
python tools\overlap-orchestrator.py check --driver <spec.json> --pair compact:collect   # 只跑闸门，⛔ 不打任何命令
python tools\overlap-orchestrator.py pair  --driver <spec.json> --pair train:collect --overlap `
     --data-root <根> --compact-root <独立根> --raw-root <根> --workers 8 --timeout 0 --out <结果.json>
python tools\overlap-orchestrator.py calibrate-mem --anon-gb 2 --file-gb 1 --out <标定.json>
```

| 项 | 内容 |
| --- | --- |
| 参数化 | `--data-root` / `--compact-root` / `--raw-root` / `--workers`（**硬上限 8**）/ `--timeout`（`<=0` ⇒ 不限）/ `--chunk`（`<=0` ⇒ **exit 2**，⛔ 不是"不限"）/ `--driver`（驱动规格 JSON）。spec 里可写 `${DATA_ROOT}` `${COMPACT_ROOT}` `${RAW_ROOT}` `${WORKERS}` `${REPO}` `${PY}` ⇒ **换根不用改 spec 一个字节**（全文件除"缺省值（可覆盖）"那一段外**零绝对盘符**） |
| 七闸门 | **G1** 主相 `phase ∈ {compact,readings,train}` ∧ 从相 `kind ∈ {collect}`（`train`/`critic`/`audit`/`eval`/**第二个 compact**/**未知一律拒** ⇒ fail closed）· **G2** 两臂写目录不相交 · **G3** 主相输入分片冻结 · **G4** 采集权重+可执行文件跑前/跑后各 pin sha256 · **G5** 采集命令行不引用正在被写的产物（**按解析后绝对路径**判，⛔ 不按目录名）· **G6** 独占锁（一次一个重叠对）· **G7** 静窗（`trainer.exe` 一律算；`python`/`java` ≥200 MB；**`java -jar mahjong-server.jar` 按命令行、不看内存**） |
| ⛔ 探针 fail-closed | 命令行取自 **`Win32_Process.CommandLine`**（CIM **每 pid 最多一次**，⛔ 不在采样循环里）→ `wmic` → 老 `tasklist /V` 解析；**已调用/取到过/取不到的 pid** 三态。**进程还活着却取不到 ⇒ FAIL / 拒跑（exit 4）**，⛔ **没有**任何"忽略探针失败"的开关；⚠ 进程已退 ⇒ 那是竞态、不算（否则永久误拒）。红证：`tasklist /V` 在本机只有 **9 个字段且第 9 个是可执行路径**，旧解析 `len(cols)>10` 永不成立 ⇒ 真机 138 MB 的 `server.jar` 在跑而旧口径报 **算竞争 0 个 / exit=0（假绿）** |
| ⛔ 枚举也 fail-closed（本批新抓） | `tasklist`（带不带 `/FI`）、`.NET GetProcessesByName`、`Get-CimInstance Win32_Process`、`Get-Counter '\Process(*)\ID Process'` 会**一起瞎**：同一台机器上，工作区**外**的 Python314 枚举到 **336** 个进程，工作区**内** uv 的 3.12 只枚举到 **18** 个（受限令牌）⇒ G7 会报"算竞争 0 个"= 假绿。⇒ 加**枚举自检**：① 自己的 pid 必须在枚举里；② `smss/csrss/wininit/services/lsass` 至少看见 3 个；③ `netstat -ano` 里 **LISTENING 的属主 pid 必须都在枚举里**（TCP 表**不受令牌限域** ⇒ 唯一独立通道）。三条缺一条 ⇒ **拒跑（exit 4）** |
| 内存口径 | 峰值**只认已提交私有字节**（`GetProcessMemoryInfo` 的 `PagefileUsage`），**常驻 ws 另报**（⛔ 不许混用）。标定（`calibrate-mem`，本机可复现）：2 GiB 匿名 ⇒ committed ≈ +2059 MB；1 GiB **文件 mmap** ⇒ ws ≈ +1038 MB 而 **committed 仅 +8.8 MB**。采样器 **1 s 栅格、零 CIM/WMI**（`GlobalMemoryStatusEx` + `GetProcessTimes` + P/Invoke），CIM 只出现在 G7 的**一次性**命令行探针里 |
| 收尸与回退 | tree-kill（`taskkill /T /F`）→ **等它真退** → 带重试删 → **复验消失** → **看回退退出码**；删不掉 ⇒ **不回退并 exit 5**。`rm_safe` 只许删白名单根内的目录，越界**拒绝删** |
| 退出码 | **0** = OK / **2** = 参数坏值或 spec 读不了 / **3** = G1–G6 拒（**一条命令都不打**）/ **4** = G7 拒（竞争负载，或探针/枚举 fail-closed）/ **5** = 收尸回退失败 / **6** = 读数被别人的负载污染（调用方应重测）/ **7** = **某一相非 0 退出**（⛔ 优先级高于 6；不许被"污染"吃掉） |
| 输出 JSON | 每相 `own_wall_s`（**按采样器看到的真的退出时刻**算，⛔ 不是"我去 `wait` 它的时刻"）/ `code` / `peak_committed_mb` / `peak_ws_mb` · `wall_s` · `overlap_interval_s` · `capture_rate`（= 重叠区间 / 较短那一相的 own_wall）· `both_present_samples`（真并发的**独立**证据，不靠墙钟）· `commit_free_min_gb` + 采样点数 · 闸门逐条结论 · `probe` / `enumeration_selfcheck` · `pin_changed` / `src_manifest_changed` · `contaminated` / `phase_failures` / `timing_reliable` |
| ⚠ 用什么解释器跑 | ⛔ **工作区内的解释器**（`python\.venv`、`.uv-python`）拿的是**受限令牌**：枚举不全、写不了独立根那些盘（`WinError 5`）。实测工作区外的 `Python314` 可写、但**没有 torch** ⇒ 真 `compact` 相那一侧目前只能用受限解释器。⇒ 跑本工具用**不受限**的解释器（枚举自检会替你判），真 compact 的落盘路径要落在它写得到的地方 |
| 不做 | ⛔ 不把任何数据读进本进程（只"起进程 + 采样 + 收尸"）· ⛔ 不改 `powercfg` / 页面文件 · ⛔ 不用 junction（compact 只读独立根）· ⛔ 不一相多次并发（G6） |


