# GPU 迁移可行性侦察 · 结论与方案

侦察时间 2026-10-11 · 只读 + 极轻探针（未改任何训练/推理代码，未跑训练/采集/闸门）。
背景：`trainer.exe selfplay 1000 --workers 8 --policy net:…×4` 正在跑（CPU 97%、GPU 3%）⇒ 探针刻意压到最小。

## 1. 环境测绘（探针原文见 §2）

| 环境 | Python | torch | torch.version.cuda | cuda.is_available() | get_device_capability() | get_num_threads() | 可用性 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 仓库 `python\.venv` | 3.12.13 | **2.11.0+cu128** | **12.8** | **True** | **(12, 0) = sm_120** | 24 | ✅ **唯一带 torch** |
| `S:\mahjong-training\tools\py312` | 3.12.13 | `ModuleNotFoundError` | — | — | — | — | 裸解释器（仅 pip），无 torch/numpy |
| `Python314`（`py -0p` 默认） | 3.14.3 | `ModuleNotFoundError` | — | — | — | — | 仅 numpy 2.3.5；3.14 无官方 cu128 wheel |
| 仓库 `.uv-python\cpython-3.12.13…` | 3.12.13 | （venv 基底，不单独用） | — | — | — | — | 供 `.venv` 的 home |

- `torch.cuda.get_arch_list()` = `['sm_75','sm_80','sm_86','sm_90','sm_100','sm_120']` ⇒ **sm_120 有 kernel image**。
- 设备名 `NVIDIA GeForce RTX 5060 Laptop GPU`，`device_count()=1`，`L2_cache_size=32MB`，`multi_processor_count=26`。
- ⚠ 仓库 `.venv` 本次**未**报 `PermissionError` / `DataRootError`（探测全只读、不建目录，未改其配置）。
- ⛔ **未跑 `python\verify_env.py`**：它内含 400 步训练基准 + 10 秒节流验证（属"训练/重负载"），且会与在跑的采集抢 CPU/GPU ⇒ **未测 + 原因如上**。

## 2. GPU 烟测结果（仓库 `.venv` 原文）

```
torch: 2.11.0+cu128 | cuda: 12.8
[SMOKE-1 matmul]   OK shape=(256, 256) sum=-607.481201
[SMOKE-2 F.linear] OK shape=(64, 79) sum=100.937012
[SMOKE-3 backward] OK grad_norm=701.284363
[PARITY cpu-vs-gpu linear] bitexact=False max_abs_diff=1.490e-07
mem allocated MB: 18.2  peak MB: 18.2
```

`nvidia-smi`：`NVIDIA-SMI 617.14` / `KMD Version: 617.14` / `CUDA UMD Version: 13.4` / `1194MiB / 8151MiB / 11%`；
`--query-gpu` → `617.14, NVIDIA GeForce RTX 5060 Laptop GPU, 8151 MiB`。
⇒ **无 `no kernel image is available`**，GPU 前向与反向都真的算了。

## 3. 兼容性结论（一句话）

✅ **本机可直接用、无需装任何东西**：唯一带 torch 的环境已是 cu128 构建、`arch_list` 含 sm_120，烟测三项全过。

## 4. 代码侧逐阶段表

| 阶段 | 实现位置 | 当前设备 | 搬 GPU 的改动量与风险 | 预期收益 |
| --- | --- | --- | --- | --- |
| **PPO / BC / AWR / IQL 训练** | `python/mahjong_ml/{ppo,bc,awr,offline_rl}.py` 各有 `--device`（缺省 `auto`）；`_batch()` 统一 `.to(device)`；节流 `guard.py:71/121` | ✅ **已可选、缺省自动上 GPU，无硬编码 cpu** | **0**（已就绪） | 已在 GPU 上 |
| v4 预训练 | `v4/pretrain.py:100-103` `_device()` → `cuda if available`；`:1151` | 同上 | **0** | 已在 GPU 上 |
| CPU 线程封顶 | `guard.py:44-50` `apply_cpu_limit` → `torch.set_num_threads(4)`；`v4/dataset.py:743` `set_num_threads(1)` | 故意留 CPU | ⛔ 别动（纪律） | — |
| `map_location="cpu"`（11 处） | `bc/ppo/awr/dagger/hybrid/offline_rl/export/v4/export` | **仅加载期**，随后 `.to(device)` | **不是硬编码**，无需改 | — |
| `device="cpu"` 的 RNG | `v4/pretrain.py:91/945/1155`、`v4/value_audit.py:511`（`torch.Generator`） | 故意的（同种子可比） | ⛔ **别搬**（搬了破坏可复现） | — |
| **自对弈采集** | `trainer/src/selfplay.cpp:225-272`（每场一线程，`--workers` 钳制）；`policies.hpp:217-243` `makeV4NetPolicy` → **每决策同步调一次 `v4Logits`** | 纯 CPU；docs §6.24：**前向占 ≈99% 墙钟** | **大**：调用是「batch=1、同步、线程内」；要跨局/跨座位凑 batch = **改引擎**（非改前向） | 见路线 R3 |
| **`v4net` 批量前向（评估路径）** | `trainer/src/v4policy.cpp:1253 forwardAll` / `:1617 logits`；CLI `v4net`；被 `_p2-11c-dforward.py:95` **逐个 subprocess** 调用 | 单线程 CPU，**1.85–2.9 ms/行** | **中**：CLI 是**纯批处理、无状态机耦合**（只打印 `argmax/logits`）⇒ 可用 torch 批量重算 | **最高**（见 R1） |
| 特征 / 规则 | `trainer/src/{features,v4features,shanten,handeval,evaluator,round}.cpp` | CPU | ⛔ **留 CPU**：`v4bench` 拆解里 JSON+特征仅 **0.08 / 1.70 ms**；逐行小张量 H2D 往返比省下的算力还贵 | — |
| python 统计 / 抽取 | numpy 级 | CPU | 低收益 | — |

**v4 权重实量**：`tools/build/v4-wl-dec6170-4g-c2-g04/net.bin` = **5,594,680 B ≈ 1.40 M fp32 参数**（5.46 MB）⇒ 8 GB 卡上四份权重只占 ~22 MB，**显存不是约束**。

## 5. 推荐路线（按「收益/成本」排序，各带判据）

**R1 · 评估路径改走已有 Python torch 前向 + CUDA batch（成本 0，先做）**
`python/mahjong_ml/v4/model.py` 是**权威实现**、C++ 是它的移植（`tools/trainer-v4-parity.mjs` 本就把两侧对到 Python 夹具）
⇒ **不引入第二套语义**，只是换执行后端。
- 判据①（回归）：`node tools/trainer-v4-parity.mjs <net.bin> <corpus…> --tol 1e-5` ⇒ 行数相同、**argmax 逐行相同**、`maxΔ ≤ tol`。
- 判据②（新路径）：同权重同输入 ≥10k 行，**GPU vs CPU argmax 全同** + `maxΔ ≤ 1e-5`（本次实测 CPU↔GPU `F.linear` 非逐位，`maxΔ=1.49e-07`）。
- 成本：**0 行训练/推理代码改动**（只加一个 runner 脚本）。预期 171k 行 5–10 min → **秒级（须实测，不预写数字）**。

**R2 · 自对弈先加 `--workers`（成本 0，不改代码）**
机器 32 逻辑核，当前只用了 **8**；docs §6.24 实测 8 worker 前向净 1.70 → 2.0–2.5 ms（共享 L3/带宽，非权重流量）。
- 判据：`g*.jsonl` 与 `--workers 1` **逐字节相同**（仓库既有判据）+ 200 场完整半庄 parity。
- 成本 0、风险 0（**并行只改调度**，汇合在 join 后按 g 升序）。预期 16/24 workers ≈ **2.5–3×**（docs 估计，未实测到底）。

**R3 · 真 GPU 推理服务（成本大，仅在 R2 撞墙后）**
把「多局/多座位」凑成一个 batch ⇒ 必须把「决策」与「同步前向」解耦（改引擎）。
- 判据：先证 **batch 前向 == 逐行前向**（argmax 全同、`maxΔ ≤ 1e-5`）；再证 `g*.jsonl` 逐字节。
- ⛔ **若轨迹不再逐字节相同就不许上线**（见 §6），此时退回"只用于离线评估"，不用于采集。

## 6. 风险与红线

- ⛔ **Java 端不迁移**（用户口径）：`server/` 保持零第三方依赖 + 纯 Java 手写前向。
- ⛔ **不许出现"两套语义不同的前向"**：任何新前向必须先过 `trainer-v4-parity`（argmax 逐行相同 + `maxΔ ≤ tol`）。
  ⚠ 本次实测量化了这条：CPU↔GPU `F.linear` **不是逐位相同**（`maxΔ=1.49e-07`）⇒ GPU 侧判据只能是
  **「near-bit + argmax 全同」**，且必须**显式写明**，不许把它当成既有「逐字节」判据的等价物。
- ⛔ **不许为了 GPU 放宽任何既有判据**（尤其"同种子 `g*.jsonl` 逐字节相同"）；不允许"先放宽再补证"。
- ⛔ 不动 `guard.py` 的 **GPU ≤80%（时间平均）/ 自身 VRAM ≤4.5 GB** 纪律（8 GB 卡上已有 ~1.1 GB 被别家占）。
- 未装任何东西（本机已就绪；若要给新环境装：`uv pip install --python <env> torch --index-url https://download.pytorch.org/whl/cu128`，
  `docs/TRAINING.md:235`；**实测本机已装占用 4.09 GB**（含 CUDA 运行库 DLL 37 个），wheel 下载量估计 ~3 GB 量级，numpy 需另装）。
