# MV-1 第一刀 —— **零训练**可判性核查（`own_win` 辅助头）｜2026-10-16

> 预注册：`lab/mv1/MV1-PLAN.md`（与本文件**同一提交**冻存 = **冻写点**，sha 用 `git log --format=%H -1 -- lab/mv1/MV1-PLAN.md` 现取；副本 `S:\mahjong-training\w-ladder\MV1-PLAN.md`）。
> 依据（⛔ 不重测）：`lab/nextphase/NEXTPHASE-{PLAN,SCREEN}.md` + `lab/nextphase/evidence/nextphase-probe-full.json`
> ⇒ 同一表示只换 y：**`own_win` 合法上限 R² = 0.13939 = `delta`（0.06643）的 ×2.10**（唯一不受天花板压）；
> `own_win_delta` ΔR² = +0.01102、95%CI **[−0.00337, +0.02460]** 含 0 ⇒ 出局；`hand_agg`/`winner_delta`/`hand_norm`/`dealin_sum` 亦出局。
> 纪律：⛔ 未起 `pretrain` / `v4 loop` / 正式 `selfplay` / 强度闸门；⛔ 未采集、未评测、未发布；只读 `ov-A-s00`；临时件用完即删。

## 0 零训练 / 零采集证明（本批实际起过的子进程）

| 判据 | 原始读数 |
| --- | --- |
| 起了训练/采集/闸门？ | **没有**。本批子进程只有：两个**只读** npy 探针（各秒级）+ `python selfcheck.py`（仓库自带自检，**合成数据的微型 `train()`**，与历轮同）+ `node`/`python` 静态检查 |
| `commit_free` 最低值 | **9.92 GB**（`Win32_OperatingSystem.FreeVirtualMemory`；纪律线 8 GB **未触及**） |
| 探针峰值内存 | 未单列（只 mmap/整数运算；两探针合计 < 1 GB；非训练相） |
| 只读端点 | `E:\mahjong-training\compact\ov-A-s00`（全程 `np.load(mmap_mode="r")`，⛔ 未写）；⛔ 未动 `raw/**`、`style-reward/**`、`compact\v4-heads-g01`、`traces\v4-wl-fold-c1-g01`、`tools\build\**` |

## 1 核查① —— **标签已经在手**（结论：**不需要扩列、不需要重采**）

`E:\mahjong-training\compact\ov-A-s00`（**只读**）的 `aux_*` 全部键（两个切分逐键核对：**dtype 与非首维形状必须一致**，否则 fail-closed 退出）：

```
root = E:\mahjong-training\compact\ov-A-s00
n_rows: train=663176 val=33615

== aux_* 键（同一份清单，逐 split 形状必须一致）==
key                      train descr train shape  val shape
aux_opp_dealin           |u1      [663176, 3]  [33615, 3]
aux_opp_hand             |u1      [663176, 3, 34] [33615, 3, 34]
aux_opp_tenpai           |u1      [663176, 3]  [33615, 3]
aux_own_shanten_after    |i1      [663176]     [33615]
aux_own_tenpai           |u1      [663176]     [33615]
aux_win_flag             |u1      [663176]     [33615]

== 覆盖率（首维 / 行数）与非零比例（逐 split）==
key                      train cov  train nz   val cov    val nz     val mean   val nuniq
aux_opp_dealin           1.000000   0.163118   1.000000   0.156508   0.052169   2
aux_opp_hand             1.000000   1.000000   1.000000   1.000000   0.374130   5
aux_opp_tenpai           1.000000   0.305626   1.000000   0.296505   0.133581   2
aux_own_shanten_after    1.000000   0.858130   1.000000   0.859200   1.695761   7
aux_own_tenpai           1.000000   0.141870   1.000000   0.140800   0.140800   2
aux_win_flag             1.000000   0.217515   1.000000   0.221687   0.221687   2

== meta.json 键（前 40）==
action_types, blocks_fingerprint, carry_model, dataset_version, derived_version, feature_version, h0_dim,
h0_nonzero_frac, h0_nonzero_frac_splits, has_aux, has_h0, lmax, obs_version, rank_frac, rtg_frac, shapes,
source, split_seed, src, student, style_reward, tensor_dtype, train_decisions, train_files, val_decisions,
val_files, val_frac

[OK] 无 fail-closed 触发
```

**对齐方式**（关键：它是不是"自家本小局和了"这一件事，而不是别的）：

```
=== train ===
行数=663176 (小局,座位)组数=43461 小局数=10866
同一 (小局,座位) 内 win 值不唯一的组数 = 0（必须 0）
win=1 的 (小局,座位) 数 = 9825；占组数 0.226065
每小局出现的座位数分布 = {2: 1, 3: 1, 4: 10864}
有和了者的小局里，win=1 的座位 == 该局 delta 最大座位 的比例 = 9825/9825 = 1.000000
win=1 行的 delta mean=7273.81 sd=4541.79; win=0 行的 delta mean=-2037.06 sd=3294.26
win=1 行的 rank_points mean=19.8641; win=0 = -5.5235
用 (delta>0) 冒充 own_win：TP=144251 FP=32875 FN=0 ⇒ **不是同一列**（假阳率 0.185602）
=== val ===
行数=33615 (小局,座位)组数=2252 小局数=563
同一 (小局,座位) 内 win 值不唯一的组数 = 0（必须 0）
win=1 的 (小局,座位) 数 = 515；占组数 0.228686
每小局出现的座位数分布 = {4: 563}
有和了者的小局里，win=1 的座位 == 该局 delta 最大座位 的比例 = 515/515 = 1.000000
win=1 行的 delta mean=7427.64 sd=4388.97; win=0 行的 delta mean=-2109.19 sd=3243.85
win=1 行的 rank_points mean=23.5623; win=0 = -6.4190
用 (delta>0) 冒充 own_win：TP=7452 FP=1347 FN=0 ⇒ **不是同一列**（假阳率 0.153086）

[OK] 无 fail-closed 触发
```

**结论**：`aux_win_flag` = **uint8 标量、覆盖率 1.000000、在 `(game, hand_no, seat)` 内逐行常数**（违规 **0/43461** 与 **0/2252**）、
每小局**恰有一家**为 1、且该家**就是该局收支最大者**（9825/9825、515/515 = 1.000000）⇒ 它就是本刀要的那一列 **`own_win`**。
非零比例 **0.2175 / 0.2217**（边缘率）。它与 `delta > 0` **不是同一列**（假阳率 15.3%/18.6% ⇒ `delta` 含立直棒/放铳收支，不能替代）。
⇒ ✅ **本刀不需要扩列、不需要重采**（与 `NEXTPHASE-PLAN.md` §4·E1 的"打点列不存在"是**两件事**：打点列确实不存在，但**二值核已在手**）。

⚠ **上面那段原始输出里有一行标签写错了**（照实留档 + 更正）：`每小局 win=1 的家数分布 = {...}` 是**按行数求和**得到的（每家十几行 ⇒ 那个"家数"其实是行数，最大到 29 毫无意义）。
**正确口径**（已固化进 `tools/lab/mv1-preflight.py` 判据⑥，按 **小局×座位组** 计）：

```
$ python tools\lab\mv1-preflight.py --root E:\mahjong-training\compact\ov-A-s00 --split val
  [ok  ] ① 标量（shape 的首维 == 行数、无尾维） —— win.shape=(33615,) n=33615
  [ok  ] ② 覆盖率 = 1.0（首维 / 行数） —— 33615 行（读到就是覆盖；缺列已在①拦下）
  [ok  ] ④ 非零比 ∈ [0.15, 0.3]（二值核不该退化） —— 非零比 = 0.221687
  [ok  ] ⑤ (小局,座位) 内逐行常数（违规数 = 0） —— 违规 0/2252 组
  [ok  ] ⑥ 每小局至多 1 家 win=1（按小局×座位组计，⛔ 不是行数） —— 515/563 局有和了者；多家 = 0
  [ok  ] ⑦ win=1 的座位 == 该局 delta 最大者 —— 515/515（有和了者的小局）
  [ok  ] ⑧ 它不是 `delta > 0` 的别名（两列不恒等，且 win=1 必为正） —— TP=7452 FP=1347（假阳率 0.153086）FN=0

MV1-PREFLIGHT PASS：7 条，不过 0 条
（train：`n=663176` · 非零比 0.217515 · 违规 0/43461 组 · 9825/10866 局有和了者 & 多家 0 · 9825/9825 · 假阳率 0.185602 ⇒ **PASS**）
$ python tools\lab\mv1-preflight.py --selftest
  [ok  ] 正向：合成紧凑集全过（7 条）
  [ok  ] 负向：③/② 缺列（win 不在） ⇒ 抓到 键齐（win/game/hand_no/seat/delta）
  [ok  ] 负向：⑤ 对齐被破坏（组内不常数） ⇒ 抓到 ⑤ …
  [ok  ] 负向：⑥ 一局两家和了 ⇒ 抓到 ⑥ …
  [ok  ] 负向：④ 非零比退化到 0 ⇒ 抓到 ④ …
  [ok  ] 负向：⑧ 退化成 `delta>0` 的别名 ⇒ 抓到 ⑧ …

自检：6 组，失败 0
MV1-PREFLIGHT SELFTEST PASS
```


## 2 核查② —— `own_win` 与现有头的关系（结论：**"存在但没人用" ⇒ 本刀 = 新增头**）

| 事实 | 位置（`文件:行`） | 读数 |
| --- | --- | --- |
| 头清单**没有** `own_win`/`win_flag` 头（7 个头：`policy`/`value`/`placement`/`belief_hand`/`belief_tenpai`/`danger`/`effect`） | `python/mahjong_ml/v4/model.py:51-62` | `HEAD_SPECS` 7 条，逐条 `name` 里无 `own_win` |
| `Heads` 只建这 7 个线性头 | `python/mahjong_ml/v4/model.py:199-211`、前向 `:213-230` | 返回键 = 7 头 + `h_evt` + `e_tokens`（`:276-279`） |
| **损失装配里没有**它（aux 喂给清单只有 5 列） | `python/mahjong_ml/v4/pretrain.py:153-157` | `("aux_opp_hand"→opp_hand, "aux_opp_tenpai"→opp_tenpai, "aux_opp_dealin"→opp_dealin, "aux_own_tenpai"→own_tenpai, "aux_own_shanten_after"→own_shanten)` —— ⛔ **没有 `aux_win_flag`** |
| `compute_loss` 的 7 个损失项 | `python/mahjong_ml/v4/pretrain.py:1296-1350`（policy/value/placement/belief_hand/belief_tenpai/danger/effect） | 无 `own_win` |
| 值头目标只有 `final`/`rtg`/`delta` | `python/mahjong_ml/v4/pretrain.py:2902`（`choices=["final","rtg","delta"]`） | ⛔ 没有"二值/打点"选项 |
| 数据侧**列已注册**（`uint8`、标量） | `python/mahjong_ml/v4/dataset.py:427`（`_COLUMNS`） | `"aux_win_flag": (np.uint8, ())` |
| 数据侧**已落列**（生产者登记） | `python/mahjong_ml/v4/auxlabels.py:41`（`"win_flag": ()`；登记见 `:54`）· `dataset.py:380`（回填）· `server/.../train/TraceRecorder.java:470` + `trainer/src/trace.cpp:456` | 数据生产端**已经**在写这一列 |
| 它今天只被**审计/天花板**用（判"作弊列"，不许进输入） | `v4/value_audit.py:366`（`label_win(结局)`）· `selfcheck.py:3361-3370`（`legit*` 不许含它） | 作为**标签**合法，作为**输入**禁止 |

⇒ **最终自变量定义（与任务给定一致，无需改写）**：
**新增一个辅助头 `own_win`（BCE，目标 = 既有二值 `aux_win_flag`）**；今天它**既不是任何头的目标、也不在任何损失里**（"存在但没人用"），
所以本刀是**纯新增**，不是"改现有头目标"。⚠ 与之相对，`own_tenpai`/`own_shanten` 两列**被 `_batch` 读进来却也没有损失项**（同族死载），本刀不碰它们。

## 3 核查③ —— 判据预对账（**零训练纯算术**）：N = 3 族 × 4,000 场/臂 = n 12,000 配对

- **算法（沿用仓内既有实现，⛔ 不自写第二套）**：`SE_paired = sd(Δ)/√n`（`sd` = 逐场逐席配对差的样本 SD，`n` = 合并配对数）；
  `MDE_95 = z_{0.975}·SE = 1.95996·SE`（与主判决"CI 是否排除 0"**同口径**）；`MDE_80 = (z_{0.975}+z_{0.8416})·SE = 2.80158·SE`（= **反解** `eval.py:172 required_n`，即 `ceiling-gate` 的 `verdict.json.mde`）；
  `Bonferroni(3 族)` ⇒ `z_{0.99167} = 2.394`；`usable_as_veto` = `tol ≥ MDE_80`（工具口径）**且** `tol ≥ MDE_95`（更保守）。
- **输入** = 一次**同 N、同族数、同工具**的真实读数：`S:\mahjong-training\w-ladder\ceiling-gate\p3pkg-B-g06-vs-defrep-verdict.json`
  （`n=12000`，逐指标 `se` 由工具给出；`sd` 由 `se·√n` 反推，`rank_points` 的 `sd=54.4084` 是工具直接给的）。
- 标定对账：该文件 `primary.se = 0.496678` ⇒ `MDE_95 = 0.9735`，与台账 **≈0.97**（第七十一轮"实际 MDE **0.974**"）**对上**。

| 指标 | 单位（`eval.seat_values`） | `se`(n=12,000) | `sd`=se·√n | **`MDE_95`** | **`MDE_80`** | tol | **tol/`MDE_80`** | tol/`MDE_95` | `usable_as_veto` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `rank_points`（主口径） | 顺位点/场/席 | 0.496678 | 54.4084 | **0.9735** | 1.3915 | — | — | — | — |
| `wins` | 和了次数/场/席 | 0.0153466 | 1.6813 | 0.0301 | 0.0430 | 0.10 | **2.326** | 3.325 | ✅ **true** |
| `deals` | 放铳次数/场/席（已取负） | 0.0128270 | 1.4052 | 0.0251 | 0.0359 | 0.10 | **2.783** | 3.978 | ✅ **true** |
| `win_points` | 和了所得点数合计/场/席 | 136.1039 | 14,912.6 | 266.8 | 381.3 | 150 | **0.393** | **0.562** | ❌ **false（不得作否决依据）** |
| （参照）`Bonferroni(3 族)` 主口径门限 | — | — | — | **`MDE_Bonf` = 1.189** | — | — | — | — | — |

**三条要写进计划的读数**：
1. **主口径分辨率 = 1.19 顺位点/场/席**（Bonferroni 口径；95%CI 口径 0.97）⇒ **比它小的效应在本 N 下判不了**（"测不出"≠"无效"，见 PLAN §5③）。
2. `wins`/`deals` 两条护栏 `tol/MDE ≥ 1`（两条口径都 ≫1）⇒ **可以作否决依据**；实测效应小到 0.03（`wins`）/0.026（`deals`）就能被看到。
3. ⛔ `win_points` 的 tol 150 **小于其 MDE（381.3 / 266.8）** ⇒ 按 AGENTS §6.5 第七十一轮判据 **`usable_as_veto = false`**、**只记账不否决**；
   要让这条护栏真的能拦人，需要 **n ≥ 77,500 场配对**（80% 功效口径）/ **37,970**（95%CI 口径）—— ⛔ 本刀不改 N（若改 N 必须先冻写 AMENDMENT 并重算全表）。
4. ⚠ `sd` 是**臂对**的性质（同一工具的另一次读数里 `deals` 的 `se` 差 35×）⇒ 本表是**预测量**；跑后一律以本批 `verdict.json` 的
   `mde`/`tol_over_mde`/`usable_as_veto` 为准**重算并照报**，比值翻转就按跑后值判。

## 4 核查④ —— 管线改动清单（`文件:行`）+ `selfcheck` 断言

### 4.1 设计（⛔ 缺省关闭；关着时旧路径**逐字节**不变）

**落点 = 外部头**（`ssl_head` 同族），**不进 `net.bin` / 不进 `HEAD_SPECS` / 不进三端头表**：

| # | 文件:行 | 改动 | 关着时为什么不变 |
| --- | --- | --- | --- |
| 1 | `python/mahjong_ml/v4/model.py:222-230` | `Heads.forward` 返回字典加一个 `"state": state`（键，不是张量） | 与 `:277-279` 的 `e_tokens`/`h_evt` 同款：**多一个键不改任何上线前向**（无 dropout/BN，前向逐位不变）；⛔ 不写进 `net.bin` 张量表（`export.py:500` 按 `state_dict()` 建表，字典键不进表） |
| 2 | `python/mahjong_ml/v4/pretrain.py:1846` 附近 | `own_win_head = nn.Linear(D_MODEL, 1).to(device)` **仅当 `args.aux_own_win_weight > 0`**（`<= 0` ⇒ `None`，与"0=关"判据③同款） | 开关关 ⇒ **对象不存在** ⇒ 无参数、无梯度、无 ckpt 键 |
| 3 | `pretrain.py:153-157`（`_batch` 的 aux 喂给清单） | **仅当开关开**时加 `("aux_win_flag", "own_win")` | 关 ⇒ `b` 的键集合与今天**逐键相同** |
| 4 | `pretrain.py:1342-1350` 之后（`effect` 项后） | `if aux is not None: l_own = BCE(out_own, b["own_win"]); total += w_aux*l_own`（**全部行**，⛔ 不吃 `row_keep`/RWR —— 与 `value`/`placement`/`belief_*` 同一条规矩，`pretrain.py:1117-1121`） | `aux=None` ⇒ **一行都不走**（与 `ref=None`/`il=None`/`rho=None`/`ssl=None` 同一处口径，`:1134/1142/1116`） |
| 5 | `pretrain.py:1988`（优化器参数表 `heads = list(model.heads.parameters()) + list(ssl_head.parameters())`） | 追加 `+ list(own_win_head.parameters() if own_win_head else [])` | 关 ⇒ 空列表 ⇒ 参数组逐位相同 |
| 6 | `pretrain.py:1573-1575`（`save_checkpoint`）· `:2723/1868`（`_state_sha256`）· `:1862`（resume） | ckpt 多一个 `own_win_head` 键；`_state_sha256` 收进它（**负向对照的判据就在这里**） | 关 ⇒ 传 `None` ⇒ `state_dict()` 为空、sha 与今天相同（⚠ **必须写成"None ⇒ 不参与 sha"**，否则 `base` 臂的 sha 会变） |
| 7 | `pretrain.py:2902` 附近新增 CLI | `--aux-own-win-weight`（`float`，**缺省 0.0 = 关**）· `--aux-own-win-rows {all,student}`（缺省 `all`，次口径） | 缺省即关 ⇒ 命令行不带它时与今天**逐字相同** |
| 8 | `pretrain.py:1969-1977`（`--drop-heads`/`--only-heads` 校验） | ⛔ **不注册进 `loss_weights()`** ⇒ 这两个开关看不到 `own_win`（刻意的：它不是注册头） | 注册表一个字不改 |
| 9 | **数据侧：零改动** | `auxlabels.py:41/54` · `dataset.py:380/427` · `TraceRecorder.java:470` · `trace.cpp:456` **三处同改的需求不存在**（列已在手，核查①） | — |
| 10 | **新增前置闸门（fail-closed）** | `--aux-own-win-weight > 0` 且 `data["aux_win_flag"] is None` ⇒ `raise SystemExit`（不许静默退化成"没这一列"） | 关 ⇒ 不判 |
| 11 | `tools/lab/mv1-preflight.py`（本批新增，只读） | 训练前对**真正的训练紧凑集**重跑核查①（键在不在 / 覆盖率=1 / 非零比 ∈ [0.15,0.30] / `(小局,座位)` 内常数），fail-closed | 不参与训练路径 |

⚠ **本批未实现上表**（只出清单）：上表 7 处改动落在 2,993 行的训练入口上，而**它的判据（`weight=0 ⇒ 与 base 逐字节相同`）需要一次短训练**——本批禁训 ⇒ 实现即"不可验证的改动"。
⇒ 按 §2.3 的教训（**发现不一致就停止推广**）与 fail-closed 纪律，**实现与它的逐字节验证一起放进"训练相第一步"**（PLAN §3②）；本批交付的是**可批准的设计 + 契约断言 + 只读 preflight**。

### 4.2 `selfcheck` 新增断言（零训练；钉"缺省路径不被这个头污染"）

插在 `python/selfcheck.py:1954` 之后（7 头断言同一段），6 条：

1. `aux_win_flag` 在 `v4_ds._COLUMNS` 里且 = `(uint8, ())`（⇒ 零扩列）；2. 合成紧凑集（`aux=True`）里 `aux_win_flag` **非 None**（数据侧真在落这一列）；
3. `own_win` **不在** `loss_weights()`（缺省 = 权重注册表一个字不改；⚠ 若实现改成"注册头" ⇒ 本条红 ⇒ 回来改计划，别静默绕过）；
4. `own_win` **不在** `TRAIN_HEADS` / `ONLINE_HEADS`（新辅助头不得进三端头表与推理路径）；
5. 缺省模型 `state_dict()` 里 **没有** `heads.own_win.*`（不建头 ⇒ `net.bin`/夹具/ckpt 逐字节不变）；
6. 今天 `v4_pt._batch(...)` **不喂** `own_win`（= 核查②的机械版：标签在数据里、没人用；⚠ 实现之后本条必须改写成"**开关关着 ⇒ 不喂**"，`detail` 里写明了）。

**结果**：改前 **923 / 0** → 改后 **929 / 0**（逐条原文见 §6）。

## 5 限制与偏离（照实）

- ⚠ **未实现 flag**（见 §4.1 末）：这是对任务 §一.4"把开关实现为缺省关闭的 flag"的**偏离**，理由是"实现即不可验证（禁训）"；改动清单已精确到 `文件:行`，可在训练相第 0 步一次性落地并同场验证。
- ⚠ **自检断言初版把参数序写反**（`ok(cond, name)` 写成了 `ok(name, cond)`）⇒ 打出 **`[ok] True`**（名字位置是布尔）= **假绿**。
  形态上一眼可见（`AGENTS.md` §6.1 早记过"两侧断言参数序不同"），已改并留提示行；**教训**：`[ok]` 后面必须是一句人话。
  同批还抓到自建工具的两条**自己的错**（⑥ 按行数求和当"家数"、⑧ 判据写成"假阳率必须 > 0"）—— 是工具**自己的 `--selftest`（负向对照）**抓出来的：
  ⇒ 判据工具必须能被判错，否则"全过"什么也不说明。
- ⚠ **核查①的读数是 `ov-A-s00`（预备用冻结件），不是 MV-1 真正要训的那份紧凑集** ⇒ preflight（§4.1 #11，已入库 `tools/lab/mv1-preflight.py`）必须在训练前对**那份**重跑一遍，⛔ 不许把它当"标签一定在"的通行证。
- ⚠ `sd`/`se` 取自**另一个臂对**的既有读数（§3）⇒ 只作预测量（已在 PLAN §4 写明"跑后以实测为准"）。
- ⚠ `wins`/`deals`/`win_points` 的 tol 沿用 `gate.QUALITY_TOL`（0.10/0.10/150）—— ⛔ 本批未改任何容忍带（改它就是事后调判据）。
- ⚠ **本机沙箱下 `python selfcheck.py` 必须用 `MAHJONG_DATA_ROOT` 指到工作区内**（否则 `paths.ensure_root` 对 `S:\mahjong-training` 写探针 ⇒ `PermissionError`）；
  口径 = **逐字不改自检代码**、只改环境变量，⛔ 未把训练数据落进仓库（`.tmp/` 已 gitignore）。
- 未跑 `ceiling-gate --collect`（那是采集/评测，本批禁）；只打算跑它的 `--selftest`（零训练）。


## 6 原始输出（逐字）

```
$ python tmp/mv1/inspect-aux.py           # 核查①（键/dtype/形状/覆盖/非零比）
（原文见 §1 第一段）
$ python tmp/mv1/inspect-align.py         # 核查①（对齐：小局×座位内常数？= 该局收支最大者？）
（原文见 §1 第二段；⚠ 其中"家数分布"一行标签有误，更正见 §1 末）
$ python tools\lab\mv1-preflight.py --selftest · --root E:\...\ov-A-s00 --split {val,train}
（原文见 §1 末：selftest 6 组失败 0 · val/train 各 7 条不过 0）
$ python selfcheck.py                     # 改前 / 改后（MAHJONG_DATA_ROOT 指到工作区内）
自检：检查项 923，失败 0        ← 改前
自检：检查项 929，失败 0        ← 改后（+6 条 MV-1 契约断言，逐条原文见 §4.2）
SELFCHECK PASS（两次）
$ python tools\ceiling-gate.py --selftest  # 零训练（本批不跑 --collect）
（见 §7 与 NOTES 第八十八轮）
```

## 7 索引与交叉引用

- 预注册：`lab/mv1/MV1-PLAN.md`（= `S:\mahjong-training\w-ladder\MV1-PLAN.md`）
- 前置检查工具：`tools/lab/mv1-preflight.py`（`--selftest` = 6 组判据自检 · 只读 · fail-closed）
- 自检断言：`python/selfcheck.py`（MV-1 契约 6 条，紧接 v4 多头断言）
- 判据行：`AGENTS.md` §6.5（第 17 条"头上线 = 前向实际读取 + 非退化门控"；护栏 tol/MDE 对账；ceiling-gate 硬前置）
- 轮次：`NOTES.md` §6.5 **第八十八轮** · 索引行：`lab/README.md` §2

