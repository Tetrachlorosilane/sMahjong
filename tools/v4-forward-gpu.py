#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""v4 前向的 **python torch 批量**入口（CUDA/CPU 同一份实现）—— 评估/分析路径的权威前向。

## 为什么要有它（而不是继续逐行 subprocess 调 `trainer.exe v4net`）

2026-10-11 之前的评估/分析路径（`_p2-11*-dforward.py` 一类）是这么调的：把要前向的行**切成
一批一批的 jsonl 文件**，再把文件名列表拼进命令行、**为每一批起一个 `trainer.exe` 子进程**，
从它的 stdout 里正则抠 `step=… argmax=… logits=…`（现状逐行见 `docs/AUTOMATION.md`）。
那条路有三个结构性代价：

1. **每批一次进程启动 + 一次权重加载**（5.6 MB 的 `net.bin` 解析 + 张量装配），批量越大越划算，
   但命令行长度（Windows ≈ 32 KB）把批量卡在几十个文件；
2. **没有设备概念**：RTX 5060（sm_120）只能干看着，全在 CPU 上按行算；
3. 输出是**文本**，要比较就得再解析一遍文本（`%g` 的是十进制字符，不是 float32 位模式）。

这份工具的判据很简单：**同一份权重、同一批行级张量 ⇒ 与 C++ `v4net` 逐行 argmax 相同、
逐元素 `maxΔ ≤ 1e-5`，而墙钟快一个量级**。实现上只做三件事：读权重、读**已经算好的**行级
输入张量、按 `n` 分桶批量前向。

## ⛔ 特征侧的红线（这条比性能重要）

**这里绝不重新实现特征抽取。** 前向只消费「既有产物里已经算好的逐行输入张量」：

| 输入形态 | 是什么 |
| --- | --- |
| `forward-v4.bin` | `python/tests/golden/forward-v4.bin`（**仓库里冻结的** P5 夹具：obs + legal + h0 + 四张量 + Python 参考输出） |
| `*.npz` | 抽取脚本 / 本工具 `--traces` 物化出来的行级张量（键 `tile/evt/ctx/cand/nlegal[/h0]`） |
| 紧凑集目录 | `python -m mahjong_ml.v4.dataset build` 的产物（`{split}.tile.npy` …，`--split` 选切分） |

唯一允许的"从轨迹到张量"的通道是 `--traces`，而它**只调用既有实现**
（`mahjong_ml.v4.blocks.assemble` + `mahjong_ml.v4.traces.sidecar_dict` —— 与
`v4 dataset build` 逐行同一份代码，`python/selfcheck.py` 正反两条判据盯着它）。
⛔ 谁在这里再手搓一遍 `tile`/`evt`/`ctx`/`cand`，就是**第二套特征语义**：两份实现会漂移，
而漂移的那天两边的读数都还打得开。

## 为什么按 `legal` 条数 `n` **分桶**而不是补 0 到 `lmax`（这条决定判据能不能过）

`v4/model.py` 的 `V4Model.forward` 里 `state = u.mean(dim=1)`（候选维求均值），而 `Heads` 用
`belief_tenpai(state)` 经 `policy_gate` **逐候选**调制 policy 的输入表示。补 0 行会把均值拉偏
⇒ 门控变 ⇒ **logits 变**（不是舍入级的差，是实打实的语义差）。所以"一次前向塞进不同 `n` 的行"
与 C++ `v4net` 的**逐行**语义**不等价**。本工具按 `n` 分桶：同一批内部 `n` 完全相同 ⇒
`mask` 恒为 None、没有一行是 padding ⇒ **逐行等价**，同时仍然是批量前向（矩阵乘）。

## 用法

    # ① 只用既有张量做前向（GPU；默认设备）
    python tools\\v4-forward-gpu.py --weights <net.bin> --rows <rows.npz> --out logits.jsonl
    # ② 同一份实现换设备（对拍）
    python tools\\v4-forward-gpu.py --weights <net.bin> --rows <rows.npz> --device cpu --out cpu.jsonl
    # ③ 读仓库里冻结的 P5 夹具（顺带与夹具里的 Python 参考 logits 逐行核一遍）
    python tools\\v4-forward-gpu.py --weights <夹具里抽出的 net.bin> --rows python\\tests\\golden\\forward-v4.bin
    # ④ 从轨迹**物化**行级张量（内部只调既有 blocks.assemble）
    python tools\\v4-forward-gpu.py --weights <net.bin> --traces <dir> --policy "net:<...>" \\
        --where-kind claim --rows-out rows.npz --out logits.jsonl
    # ⑤ 自检（四条，含三条负向）
    python tools\\v4-forward-gpu.py --selftest

退出码：0 = 通过 / 1 = 判据不过（与参考 logits 不符）/ 2 = 用法、环境或输入坏了（**不是**静默降级）。

⚠ **临时产物一律落仓库内 `python/.tmp/`**：仓库 `.venv` 的 python 在本机**不能**在工作区外建目录
（`PermissionError`，实测），`--tmp` 可改但别指到工作区外。
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
#: ⛔ 关掉字节码缓存：在 `tools/` 下留 `__pycache__/` 会把 `git status` 弄脏（同 `paired-eval-bridge.py`）。
sys.dont_write_bytecode = True
#: `mahjong_ml` 的根（仓库 `python/`）—— 本工具不复制它的任何逻辑，只 import。
sys.path.insert(0, str(REPO / "python"))

#: 退出码（与 `tools/trainer-v4-parity.mjs` 同一套语义：0/1/2，1 = 判据不过）
EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2

#: `forward-v4.bin`（P5 夹具）的魔数与格式号 —— 与 `mahjong_ml.v4.export` 同值，
#: 这里**不 import 常量**是为了让"读夹具"这条路径也能在 torch 没装时报出可读的错。
GOLDEN_MAGIC = 0x4D4A3447          # "MJ4G"
GOLDEN_FORMAT = 2
NET_MAGIC = 0x4D4A4E4E             # "MJNN"（`net.bin` 格式 2）
NET_FORMAT = 2

#: 与 C++ `v4net` 的 `argmax` 同口径（**第一个**最大值所在下标；`std::max_element` 也是首现）。
def _argmax(vals) -> int:
    best, bi = None, -1
    for i, v in enumerate(vals):
        if best is None or v > best:
            best, bi = v, i
    return bi


class InputError(RuntimeError):
    """输入本身坏了（权重 / 张量 / 设备）—— 一律**报错**，绝不静默回落或填 0。"""


# ---------------------------------------------------------------- 设备

def resolve_device(name: str):
    """`cuda` / `cpu` → `torch.device`；**cuda 不可用时报错，不回落**。

    为什么强调"不回落"：静默回落会让"GPU 前向跑通了"变成一句假话 —— 报告里写着 GPU、实际跑的是
    CPU，而且**没有任何一行输出**能看出来。这里宁可当场退出。

    ⚠ 同时把 `allow_tf32` 钉成 False（判据要的是 **fp32**）：TF32 只有 10 位尾数，相对误差 ~1e-3，
    与 CPU 的 fp32 比 `maxΔ` 会直接超 `1e-5`。这不是"调参调过的绿"—— 判据④问的就是
    "同一份实现在两个后端上是不是同一个数"，那两边都必须是 fp32。
    """
    import torch

    if name == "cpu":
        return torch.device("cpu")
    if name != "cuda" and not name.startswith("cuda:"):
        raise InputError(f"--device 只认 cpu / cuda[:N]，收到 {name!r}")
    if not torch.cuda.is_available():
        raise InputError(
            "--device cuda 但 torch.cuda.is_available() == False —— **不静默回落到 CPU**。"
            "要么装对 CUDA 版 torch（本机 `python/.venv` 是 2.11.0+cu128），要么显式写 --device cpu")
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    return torch.device(name)


def pin_threads(n: int) -> None:
    """CPU 线程数钉死（缺省 8，与仓库并行度口径一致）。

    ⚠ 为什么要钉：torch 的 intra-op 线程默认 = 物理核数，而本机是**一次一个重负载**的口径
    （自对弈采集占比本来就高）。钉到 8 让"我这次加了多少 CPU"是个**可写的数**，而不是"核数个线程"。
    """
    import torch
    torch.set_num_threads(max(1, int(n)))


# ---------------------------------------------------------------- 权重

@dataclass
class Net:
    model: object
    dims: dict


def load_net(weights: Path, device, tmp: str | None = None):
    """`net.bin`（格式 2）→ eval 模式的 `V4Model`；坏文件**报错**。

    三步全部走**既有实现**，一行都不重写：
    ① `v4.export.read_net`（魔数 / 格式号 / 尾部多余字节都在那里硬校验）；
    ② `v4.export.state_from_net` → `normalize_state`（跨代差异**只在那里处理一次**：
       `heads.policy.weight` 的 `dm+3` 截断、`policy_gate` / `fusion.mem` 缺则补 0）；
    ③ `V4Model.load_state_dict(strict=True)` —— **strict** 是判据：少一个张量就该红
       （缺张量走的是 `normalize_state` 的补 0 白名单，不是"随便少"）。
    """
    from mahjong_ml.v4 import export as v4export
    from mahjong_ml.v4 import model as M

    tmp = tmp or str(REPO / "python" / ".tmp" / "v4-forward-gpu")
    p = Path(weights)
    if not p.is_file():
        raise InputError(f"找不到权重文件：{p}")
    if p.stat().st_size == 0:
        raise InputError(f"权重文件是空文件：{p}")
    # ⚠ 允许直接给 **P5 夹具**：权重本来就内嵌在里面（`head 4I(net_bytes)` 之后那一段）。
    #   抽出来的 blob 与 `export.save_net` 写出的字节**逐字节相同**，所以这是"同一份权重"，
    #   不是第二份权重；只是省掉"手工 dd 出 net.bin"这一步。
    if struct.unpack_from("<I", p.read_bytes()[:4])[0] == GOLDEN_MAGIC:
        raw = p.read_bytes()
        (net_bytes,) = struct.unpack_from("<I", raw, 12)
        blob = raw[16:16 + int(net_bytes)]
        p = Path(tmp) / (Path(weights).stem + "-embedded-net.bin")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
        print(f"[net] 从夹具里抽出内嵌权重 → {p}（{len(blob)} B）", file=sys.stderr)
    try:
        parsed = v4export.read_net(p)
    except Exception as e:                                    # noqa: BLE001（原样上报，含 NetFormatError）
        raise InputError(f"权重文件读不了：{p.name}: {type(e).__name__}: {e}") from e
    dims = {k: int(v) for k, v in parsed["dims"].items()}
    if dims["d_model"] % dims["n_heads"] or dims["tile_d"] % dims["n_heads"]:
        raise InputError(f"权重头部的宽度自相矛盾：{dims}")
    net = M.build(**dims)
    state = v4export.state_from_net(parsed)
    try:
        net.load_state_dict(state, strict=True)
    except Exception as e:                                    # noqa: BLE001
        raise InputError(f"权重张量表与模型拓扑不符（{p.name}）：{e}") from e
    got = sum(int(v.numel()) for v in net.state_dict().values())
    if got != int(parsed["params"]):
        # 头部记的参数个数与真实张量表不符 ⇒ 文件被动过（截断/拼接），不当"能跑就行"
        raise InputError(f"权重头部声明 {int(parsed['params'])} 个参数，实际张量 {got} 个 —— 文件被改过")
    net.eval()
    net.requires_grad_(False)
    net.to(device)
    return Net(model=net, dims=dims)


# ---------------------------------------------------------------- 行级张量（三种既有产物）

@dataclass
class Rows:
    """一批**已经算好的**逐行输入张量（本工具只读它，不产它）。"""

    tile: object          # [N,34,C_TILE] float32
    evt: object           # [N,K_EVT,C_EVT] float32
    ctx: object           # [N,C_CTX] float32
    cand: object          # [N,L,C_CAND] float32
    nlegal: object        # [N] int32
    h0: object | None     # [N,dm] float32 或 None（= 从 0 冷启动，与 C++ `v4net` 同口径）
    source: str
    ref_policy: list | None = None   # 夹具里带的 Python 参考 policy logits（有就顺手核一遍）
    legal: list | None = None        # 夹具里的 legal 动作键（便于人工核对，不参与计算）
    steps: list | None = None        # 轨迹里的 `step`（只有物化路径有；纯张量输入不知道它）


def _as_f32(a, name: str):
    import numpy as np
    if a is None:
        raise InputError(f"行级张量缺一列：{name}")
    return np.ascontiguousarray(a, dtype=np.float32)


def _check_rows(rows: Rows, dims: dict) -> None:
    """维度硬闸门：**行数与张量维度不符一律报错**（自检② 就是它）。

    三类错都要拦：① 四张量行数不齐；② 维度宽度不对（tile 不是 34×C_TILE 之类）；
    ③ `nlegal` 超过 `cand` 的第二维（那意味着候选被截断，前向会看错行）。
    """
    import numpy as np
    n = int(rows.tile.shape[0])
    if n == 0:
        raise InputError("行级张量一行都没有")
    for name, a in (("evt", rows.evt), ("ctx", rows.ctx), ("cand", rows.cand),
                    ("nlegal", rows.nlegal)):
        if int(a.shape[0]) != n:
            raise InputError(f"行数不齐：tile {n} 行 vs {name} {int(a.shape[0])} 行")
    if rows.tile.ndim != 3 or rows.tile.shape[1] != 34:
        raise InputError(f"tile 形状应为 [N,34,C_TILE]，实际 {tuple(rows.tile.shape)}")
    if rows.evt.ndim != 3:
        raise InputError(f"evt 形状应为 [N,K,C_EVT]，实际 {tuple(rows.evt.shape)}")
    if rows.ctx.ndim != 2:
        raise InputError(f"ctx 形状应为 [N,C_CTX]，实际 {tuple(rows.ctx.shape)}")
    if rows.cand.ndim != 3:
        raise InputError(f"cand 形状应为 [N,L,C_CAND]，实际 {tuple(rows.cand.shape)}")
    lmax = int(rows.cand.shape[1])
    nl = np.asarray(rows.nlegal)
    if nl.size != n:
        raise InputError(f"nlegal 长度 {nl.size} != 行数 {n}")
    if nl.min() < 1:
        raise InputError(f"nlegal 里有 0 或负值（最小 {int(nl.min())}）—— 一行至少要有一个候选")
    if int(nl.max()) > lmax:
        raise InputError(f"nlegal 最大 {int(nl.max())} > cand 的第二维 {lmax} —— 候选被截断了")
    if rows.h0 is not None and int(rows.h0.shape[0]) != n:
        raise InputError(f"h0 行数 {int(rows.h0.shape[0])} != 行数 {n}")
    if rows.h0 is not None and int(rows.h0.shape[1]) != int(dims["d_model"]):
        raise InputError(f"h0 宽度 {int(rows.h0.shape[1])} != d_model {int(dims['d_model'])}")


def read_golden(path: Path) -> Rows:
    """读 P5 夹具 `forward-v4.bin`（格式 2）—— 仓库里**冻结**的行级张量 + Python 参考输出。

    布局（与 `v4/export.build_golden` 写入顺序逐字段对应）：
    `头 4I(magic, format, n_cases, net_bytes)` + `net blob` + 每个用例
    `obs` + `legal` + `h0[dm]` + `tile[34,48]` + `evt[60,96]` + `ctx[64]` + `cand[n,128]`
    + `policy[n]` + `value[value_bins]` + `belief_tenpai[3]` + `danger[n,4]` + `h_evt[dm]`。
    ⚠ 这里**只读输入那一段**（obs/legal/h0/四张量）与参考 `policy` —— 输出段其余部分留给
    `trainer v4golden` 与 `tools/V4Probe.java`（那是既有闸门的事，本工具不越位）。
    """
    import numpy as np
    from mahjong_ml.v4 import spec

    raw = Path(path).read_bytes()
    if len(raw) < 16:
        raise InputError(f"夹具太短：{path}")
    magic, fmt, n_cases, net_bytes = struct.unpack_from("<4I", raw, 0)
    if magic != GOLDEN_MAGIC:
        raise InputError(f"夹具魔数不对：{magic:#x}（期望 {GOLDEN_MAGIC:#x}）")
    if fmt != GOLDEN_FORMAT:
        raise InputError(f"夹具格式号 {fmt} != {GOLDEN_FORMAT}")
    net_blob = raw[16:16 + net_bytes]
    hdr = struct.unpack_from("<9I", net_blob, 0)
    if hdr[0] != NET_MAGIC or hdr[1] != NET_FORMAT:
        raise InputError(f"夹具里的内嵌权重不是 net.bin 格式 2（magic={hdr[0]:#x} fmt={hdr[1]}）")
    dm = int(hdr[5])
    tile_w, evt_w, ctx_w, cand_w = int(spec.C_TILE), int(spec.C_EVT), int(spec.C_CTX), int(spec.C_CAND)
    off = 16 + net_bytes
    tiles, evts, ctxs, cands, nls, h0s, refs, legals = [], [], [], [], [], [], [], []
    for ci in range(int(n_cases)):
        (oln,) = struct.unpack_from("<I", raw, off); off += 4
        off += int(oln)                                     # obs（前向不需要它；张量已经是结果）
        (n,) = struct.unpack_from("<H", raw, off); off += 2
        keys = []
        for _ in range(int(n)):
            (kln,) = struct.unpack_from("<H", raw, off); off += 2
            keys.append(raw[off:off + kln].decode("utf-8")); off += kln
        h0 = np.frombuffer(raw, dtype="<f4", count=dm, offset=off).copy(); off += 4 * dm
        tile = np.frombuffer(raw, dtype="<f4", count=34 * tile_w, offset=off).copy()
        off += 4 * 34 * tile_w
        evt = np.frombuffer(raw, dtype="<f4", count=spec.K_EVT * evt_w, offset=off).copy()
        off += 4 * spec.K_EVT * evt_w
        ctx = np.frombuffer(raw, dtype="<f4", count=ctx_w, offset=off).copy(); off += 4 * ctx_w
        cand = np.frombuffer(raw, dtype="<f4", count=int(n) * cand_w, offset=off).copy()
        off += 4 * int(n) * cand_w
        pol = np.frombuffer(raw, dtype="<f4", count=int(n), offset=off).copy(); off += 4 * int(n)
        vb = int(hdr[8])
        off += 4 * vb + 4 * 3 + 4 * int(n) * 4 + 4 * dm       # value / belief / danger / h_evt
        tiles.append(tile.reshape(34, tile_w))
        evts.append(evt.reshape(spec.K_EVT, evt_w))
        ctxs.append(ctx.reshape(ctx_w))
        cands.append(cand.reshape(int(n), cand_w))
        nls.append(int(n))
        h0s.append(h0)
        refs.append(pol.astype(np.float32))
        legals.append(keys)
    lmax = max(nls)
    cand_pad = np.zeros((len(nls), lmax, cand_w), dtype=np.float32)
    for i, c in enumerate(cands):
        cand_pad[i, :c.shape[0]] = c
    return Rows(tile=np.stack(tiles), evt=np.stack(evts), ctx=np.stack(ctxs), cand=cand_pad,
                nlegal=np.asarray(nls, dtype=np.int32), h0=np.stack(h0s),
                source=f"{Path(path).name}（P5 夹具 {len(nls)} 例）",
                ref_policy=refs, legal=legals)


def read_npz(path: Path) -> Rows:
    """读行级张量 `.npz`（键 `tile/evt/ctx/cand/nlegal`，可选 `h0`）。"""
    import numpy as np
    with np.load(path, allow_pickle=False) as z:
        keys = set(z.files)
        need = {"tile", "evt", "ctx", "cand", "nlegal"}
        missing = sorted(need - keys)
        if missing:
            raise InputError(f"{path.name} 缺键：{missing}（有 {sorted(keys)}）")
        h0 = z["h0"] if "h0" in keys else None
        return Rows(tile=_as_f32(z["tile"], "tile"), evt=_as_f32(z["evt"], "evt"),
                    ctx=_as_f32(z["ctx"], "ctx"), cand=_as_f32(z["cand"], "cand"),
                    nlegal=np.asarray(z["nlegal"], dtype=np.int32),
                    h0=None if h0 is None else _as_f32(h0, "h0"), source=path.name)


def read_compact(directory: Path, split: str) -> Rows:
    """读 `v4 dataset build` 的紧凑集目录（`{split}.<列>.npy`，行序 = 决策行序）。"""
    import numpy as np
    d = Path(directory)
    need = ("tile", "evt", "ctx", "cand", "nlegal")
    missing = [f"{split}.{k}.npy" for k in need if not (d / f"{split}.{k}.npy").is_file()]
    if missing:
        raise InputError(f"{d} 里缺列文件：{missing}")
    h0p = d / f"{split}.h0.npy"
    return Rows(tile=_as_f32(np.load(d / f"{split}.tile.npy"), "tile"),
                evt=_as_f32(np.load(d / f"{split}.evt.npy"), "evt"),
                ctx=_as_f32(np.load(d / f"{split}.ctx.npy"), "ctx"),
                cand=_as_f32(np.load(d / f"{split}.cand.npy"), "cand"),
                nlegal=np.asarray(np.load(d / f"{split}.nlegal.npy"), dtype=np.int32),
                h0=_as_f32(np.load(h0p), "h0") if h0p.is_file() else None,
                source=f"{d.name}/{split}")


def read_rows(path: Path, split: str) -> Rows:
    """按形态自动分派：`.npz` / 夹具魔数 / 紧凑集目录。"""
    if not path.exists():
        raise InputError(f"找不到行级张量：{path}")
    if path.is_file():
        head = path.read_bytes()[:4]
        if len(head) == 4 and struct.unpack("<I", head)[0] == GOLDEN_MAGIC:
            return read_golden(path)
        if path.suffix.lower() == ".npz":
            return read_npz(path)
        raise InputError(f"{path.name} 既不是 .npz 也不是 P5 夹具（前 4 字节 {head!r}）")
    return read_compact(path, split)


# ---------------------------------------------------------------- 物化（唯一允许的"轨迹 → 张量"通道）

def materialize(traces: Path, *, policy: str | None, where_kind: str | None,
                limit: int, rows_out: Path | None, dump_selected: Path | None,
                ablate: str, quiet: bool) -> Rows:
    """轨迹目录 → 行级张量（**只调既有实现**，见模块 docstring 的红线）。

    - 逐文件 `traces.trace_files` 排序（与 `dataset build` 同口径）；
    - sidecar 走 `mahjong_ml.dataset.load_sidecar`（版本 / 长度硬校验）+ `traces.sidecar_dict`；
    - 张量走 `blocks.assemble(..., allow_degraded=False)`：**降级一律报错**
      （降级的后果是"tile.danger / cand.derived 整块填 0"，等于把那一整路信息删掉却照样能跑）。
    """
    import numpy as np
    from mahjong_ml import dataset as _v3
    from mahjong_ml.v4 import blocks, traces as v4traces

    d = Path(traces)
    files = v4traces.trace_files(d)
    tiles, evts, ctxs, cands, nls = [], [], [], [], []
    steps: list[int] = []
    sc_cache: dict[Path, dict] = {}
    n_out = 0
    dumps: dict[str, list[str]] = {}
    t0 = time.time()
    for f in files:
        sc = sc_cache.get(f)
        if sc is None:
            sc = v4traces.load_sidecar(_v3.sidecar_path(f))
            sc_cache[f] = sc
        # ⚠ `j` **每个文件都要归零**：sidecar 是**逐文件**的（`g1.feat.bin` 只装 `g1.jsonl` 的决策），
        #   而张量是按全局行序攒的。跨文件累加 `j` 会拿"上一个文件的第 k 条"当这一条的派生量 ——
        #   症状是**不报错**地少几列（`cand.derived` 落到"候选数不够"那条降级路上），
        #   于是 argmax 悄悄变了（本工具第一次跑就是这么红的）。
        j = 0
        with f.open(encoding="utf-8") as fh:
            for ln, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("type") != "decision":
                    continue
                idx = j                       # sidecar 的下标 = 本文件内第 idx 条 decision（同序）
                j += 1
                if policy is not None and row.get("policy") != policy:
                    continue
                obs = row.get("obs")
                if not isinstance(obs, dict):
                    raise InputError(f"{f.name}:{ln} 决策行没有 obs")
                if where_kind is not None and str(obs.get("kind")) != where_kind:
                    continue
                v4traces.gate_obs(obs, where=f"{f.name}:{ln}")
                side = v4traces.sidecar_dict(sc, idx)
                t = blocks.assemble(obs, side, allow_degraded=False, ablate=ablate.split(",") if ablate else ())
                if t.degraded:
                    raise InputError(f"{f.name}:{ln} 特征拼装发生降级：{sorted(t.degraded)}")
                legal = list(row.get("legal") or [])
                if len(legal) != t.cand.shape[0]:
                    raise InputError(f"{f.name}:{ln} legal {len(legal)} != cand {t.cand.shape[0]}")
                if not legal:
                    raise InputError(f"{f.name}:{ln} 一行候选都没有")
                tiles.append(t.tile)
                evts.append(t.evt)
                ctxs.append(t.ctx)
                cands.append(t.cand)
                nls.append(len(legal))
                steps.append(int(row.get("step", -1)))
                if dump_selected is not None:
                    key = f.stem
                    dumps.setdefault(key, []).append(line if line.endswith("\n") else line + "\n")
                n_out += 1
                if limit and n_out >= limit:
                    break
        if limit and n_out >= limit:
            break
    if not n_out:
        raise InputError(f"{d} 里没有选中的 decision 行（policy={policy!r} kind={where_kind!r}）")
    lmax = max(nls)
    cand_pad = np.zeros((n_out, lmax, cands[0].shape[1]), dtype=np.float32)
    for i, c in enumerate(cands):
        cand_pad[i, :c.shape[0]] = c
    rows = Rows(tile=np.stack(tiles).astype(np.float32), evt=np.stack(evts).astype(np.float32),
                ctx=np.stack(ctxs).astype(np.float32), cand=cand_pad,
                nlegal=np.asarray(nls, dtype=np.int32), h0=None,
                source=f"{d.name}（policy={policy or '*'} kind={where_kind or '*'}：{n_out} 行）",
                steps=steps)
    if rows_out is not None:
        Path(rows_out).parent.mkdir(parents=True, exist_ok=True)
        # ⚠ 存 **float32**（不跟紧凑集的 float16 口径）：紧凑集是**训练**产物（省一半磁盘），
        #   而这里的用途是**对拍**——float16 只有 11 位尾数（相对误差 ~5e-4），
        #   过了它一遍就等于把"逐元素 ≤1e-5"这条判据冲掉了，之后所有的红绿都不作数。
        np.savez(str(rows_out), tile=rows.tile, evt=rows.evt, ctx=rows.ctx, cand=rows.cand,
                 nlegal=rows.nlegal, steps=np.asarray(steps, dtype=np.int32))
    if dump_selected is not None:
        # 把**选中的同一批行**原样落成 `g<场号>.jsonl`：喂给 C++ `v4net` 的就是同一批行
        # （判据②③要的是"同一份输入"，不是"看起来差不多的一份"）
        outd = Path(dump_selected)
        outd.mkdir(parents=True, exist_ok=True)
        for key, lines in dumps.items():
            (outd / f"{key}.jsonl").write_text("".join(lines), encoding="utf-8")
    if not quiet:
        print(f"[rows] {rows.source}；{n_out} 行 / {len(files)} 个轨迹文件；"
              f"候选数 {min(nls)}..{max(nls)}（cand 第二维 {lmax}）；"
              f"物化 {time.time() - t0:.1f}s", file=sys.stderr)
    return rows


# ---------------------------------------------------------------- 前向

@dataclass
class ForwardResult:
    n: list
    argmax: list
    logits: list
    seconds: float
    tiles_rows: int


def forward(net: Net, rows: Rows, device, batch: int, *, index=None, quiet: bool) -> ForwardResult:
    """按 `legal` 条数分桶的批量前向（见模块 docstring「为什么分桶」）。"""
    import numpy as np
    import torch

    sel = list(range(int(rows.tile.shape[0]))) if index is None else list(index)
    if not sel:
        raise InputError("选中的行是空的")
    model = net.model
    # 幂等：把权重放到目标设备（自检里同一张网要先后在 cpu / cuda 上跑；
    # 少了这一句，第二次调用会以 "mat1 is on cuda:0 … other tensors on cpu" 炸掉 ——
    # 那不是判据失败，是调用方忘了搬权重，容易误诊）。
    model.to(device)
    h0_all = rows.h0
    device_str = str(device)
    buckets: dict[int, list[int]] = {}
    for i in sel:
        buckets.setdefault(int(rows.nlegal[i]), []).append(i)
    out_n: list[int] = [0] * len(sel)
    out_arg: list[int] = [0] * len(sel)
    out_log: list[list[float]] = [[] for _ in sel]
    pos = {i: k for k, i in enumerate(sel)}
    t0 = time.time()
    with torch.no_grad():
        for n in sorted(buckets):
            idxs = buckets[n]
            for b0 in range(0, len(idxs), batch):
                part = idxs[b0:b0 + batch]
                tile = torch.from_numpy(rows.tile[part, :, :]).to(device)
                evt = torch.from_numpy(rows.evt[part, :, :]).to(device)
                ctx = torch.from_numpy(rows.ctx[part, :]).to(device)
                cand = torch.from_numpy(rows.cand[part, :n, :]).to(device)
                h = None if h0_all is None else torch.from_numpy(h0_all[part, :]).to(device)
                out = model(tile, evt, ctx, cand, mask=None, h=h)
                pol = out["policy"].detach().to("cpu", torch.float32).numpy()
                for r, i in enumerate(part):
                    lg = [float(x) for x in pol[r]]
                    out_log[pos[i]] = lg
                    out_n[pos[i]] = n
                    out_arg[pos[i]] = _argmax(lg)
    sec = time.time() - t0
    if not quiet:
        print(f"[fwd] device={device_str} 行={len(sel)} 桶={ {k: len(v) for k, v in sorted(buckets.items())} } "
              f"batch={batch} 用时 {sec:.3f}s（{len(sel) / max(sec, 1e-9):,.0f} 行/秒）", file=sys.stderr)
    return ForwardResult(n=out_n, argmax=out_arg, logits=out_log, seconds=sec, tiles_rows=len(sel))


def write_out(path: Path | None, res: ForwardResult, fmt: str, *, steps=None) -> None:
    """落盘：`jsonl`（默认，本工具自己的口径）或 `cpp`（与 `trainer v4net` 逐字符同格式）。"""
    lines = []
    for k in range(len(res.n)):
        step = -1 if steps is None else int(steps[k])
        if fmt == "cpp":
            # ⚠ 与 C++ `v4net` 同格式（`%.9g`）。判据②③的比较器两边都读文本 ⇒ 用同一套排版
            #   最省事，也最不容易把"排版差异"误当成"数值差异"。
            vals = ",".join(f"{v:.9g}" for v in res.logits[k])
            lines.append(f"step={step} n={res.n[k]} argmax={res.argmax[k]} logits={vals}")
        else:
            lines.append(json.dumps({"row": k, "step": step, "n": res.n[k],
                                     "argmax": res.argmax[k], "logits": res.logits[k]},
                                    ensure_ascii=False))
    text = "\n".join(lines) + "\n"
    if path is None:
        sys.stdout.write(text)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- 参考对拍（夹具自带 logits）

def compare_ref(rows: Rows, res: ForwardResult, tol: float) -> tuple[int, float, list]:
    """与夹具里的 **Python 参考** logits 逐行比：argmax 相同 + `maxΔ ≤ tol`。

    这只是"我这份实现在这台机器上、这个后端上，是否复现了**冻结在仓库里的** python 读数"——
    与 C++ 的三端对拍是既有闸门（`tools/trainer-v4-parity.mjs`）的事，本工具不替代它。
    """
    bad: list[str] = []
    worst = 0.0
    if rows.ref_policy is None:
        return -1, 0.0, ["（这份输入没有参考 logits）"]
    for k, ref in enumerate(rows.ref_policy):
        if len(ref) != res.n[k]:
            bad.append(f"第 {k} 行：候选数 {res.n[k]} != 参考 {len(ref)}")
            continue
        for a, b in zip(ref, res.logits[k]):
            worst = max(worst, abs(float(a) - float(b)))
        if _argmax([float(x) for x in ref]) != res.argmax[k]:
            bad.append(f"第 {k} 行：argmax {res.argmax[k]} != 参考 {_argmax([float(x) for x in ref])}")
    return 0, worst, bad


# ---------------------------------------------------------------- 自检

def _synth_rows(seed: int, n_rows: int = 64, dm: int = 32):
    """自检用的小批量行级张量（**合成**数据：自检只问"实现是否正确"，不问"分布是否真实"）。"""
    import numpy as np
    from mahjong_ml.v4 import spec
    rng = np.random.default_rng(seed)
    nl = rng.integers(1, 12, size=n_rows).astype(np.int32)
    lmax = int(nl.max())
    return Rows(
        tile=rng.normal(0, 0.5, size=(n_rows, 34, spec.C_TILE)).astype(np.float32),
        evt=rng.normal(0, 0.5, size=(n_rows, spec.K_EVT, spec.C_EVT)).astype(np.float32),
        ctx=rng.normal(0, 0.5, size=(n_rows, spec.C_CTX)).astype(np.float32),
        cand=rng.normal(0, 0.5, size=(n_rows, lmax, spec.C_CAND)).astype(np.float32),
        nlegal=nl, h0=None, source="selftest/synth")


def selftest(tmp: Path, batch: int, tol: float) -> int:
    """四条自检：① 坏权重报错（负向）② 行/维不符报错（负向）③ GPU↔CPU 对拍 ④ 设备不可用报错。"""
    import numpy as np
    import torch
    from mahjong_ml.v4 import export as v4export
    from mahjong_ml.v4 import model as M

    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    fails: list[str] = []

    def line(ok: bool, msg: str) -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {msg}")
        if not ok:
            fails.append(msg)

    # ---- 夹具：一张小网 + 一份小张量（都落在仓库内 `python/.tmp/`）
    dims = {"d_model": 32, "tile_d": 16, "n_heads": 2, "value_bins": 5}
    torch.manual_seed(20261011)
    net = M.build(20261011, **dims)
    with torch.no_grad():                    # 零初始化的门控要抬离 0，否则"门控被消费"测不到
        for name, p in net.state_dict().items():
            if p.numel() and float(p.abs().max()) == 0.0:
                p.normal_(0.0, 0.5)
    good = tmp / "selftest-net.bin"
    v4export.save_net(net.state_dict(), dims, good)
    rows = _synth_rows(7)
    npz = tmp / "selftest-rows.npz"
    np.savez(str(npz), tile=rows.tile, evt=rows.evt, ctx=rows.ctx, cand=rows.cand,
             nlegal=rows.nlegal)

    # ---- ① 坏权重必须报错（缺文件 / 空文件 / 半截文件 / 魔数错）
    print("① 权重（负向：坏文件必须报错）")
    for label, p, touch in (
            ("文件不存在", tmp / "no-such-net.bin", None),
            ("空文件", tmp / "empty.bin", b""),
            ("半截（截断到 32 B）", tmp / "trunc.bin", good.read_bytes()[:32]),
            ("魔数被改坏", tmp / "badmagic.bin",
             b"\x00\x00\x00\x00" + good.read_bytes()[4:])):
        if touch is not None:
            p.write_bytes(touch)
        try:
            load_net(p, torch.device("cpu"))
            line(False, f"{label} ⇒ **没有报错**（坏权重被当成了好权重）")
        except InputError as e:
            line(True, f"{label} ⇒ 报错：{str(e)[:70]}")
    try:
        load_net(good, torch.device("cpu"))
        line(True, "好权重 ⇒ 正常载入（正向对照）")
    except InputError as e:
        line(False, f"好权重载不进来：{e}")

    # ---- ② 行数 / 维度不符必须报错
    print("② 行级张量（负向：行数或维度不符必须报错）")
    netok = load_net(good, torch.device("cpu"))
    cases = []
    bad = _synth_rows(11)
    bad.evt = bad.evt[:-1]                                     # 行数不齐
    cases.append(("行数不齐（evt 少一行）", bad))
    bad = _synth_rows(12)
    bad.cand = bad.cand[:, :1, :]                              # cand 第二维 < nlegal
    cases.append(("nlegal > cand 第二维", bad))
    bad = _synth_rows(13)
    bad.tile = bad.tile[:, :33, :]                             # tile 不是 34 行
    cases.append(("tile 不是 34 行", bad))
    bad = _synth_rows(14)
    bad.nlegal = bad.nlegal.copy()
    bad.nlegal[0] = 0                                          # 一行一个候选都没有
    cases.append(("nlegal 有 0", bad))
    for label, r in cases:
        try:
            _check_rows(r, netok.dims)
            line(False, f"{label} ⇒ **没有报错**")
        except InputError as e:
            line(True, f"{label} ⇒ 报错：{str(e)[:70]}")
    try:
        _check_rows(rows, netok.dims)
        line(True, "合规格的张量 ⇒ 通过（正向对照）")
    except InputError as e:
        line(False, f"合规格的张量被拒了：{e}")

    # ---- ③ 小样本 GPU ↔ CPU 对拍（同一份实现，只换设备）
    print("③ GPU ↔ CPU（同一份实现；argmax 必须全同，maxΔ ≤ 1e-5）")
    if not torch.cuda.is_available():
        print("  SKIP  torch.cuda.is_available() == False ⇒ 这条在本机跑不了"
              "（**不是** PASS：没有 GPU 就无法证明 GPU 与 CPU 一致）")
        fails.append("③ 未执行（本机没有可用 CUDA）")
    else:
        cpu = forward(netok, rows, torch.device("cpu"), batch, quiet=True)
        gpu = forward(netok, rows, resolve_device("cuda"), batch, quiet=True)
        dmax = max((abs(a - b) for ra, rb in zip(cpu.logits, gpu.logits)
                    for a, b in zip(ra, rb)), default=0.0)
        same = sum(1 for a, b in zip(cpu.argmax, gpu.argmax) if a == b)
        line(same == len(cpu.argmax), f"argmax 逐行相同：{same}/{len(cpu.argmax)}")
        line(dmax <= tol, f"maxΔ = {dmax:.3e} ≤ {tol:g}")
        line(cpu.logits[0][:1] != [] and len(gpu.n) == len(cpu.n),
             f"两边的行数一致：cpu {len(cpu.n)} / cuda {len(gpu.n)}")

    # ---- ④ 设备不可用 ⇒ 报错不回落
    print("④ 设备（负向：cuda 不可用必须报错，⛔ 不静默回落 CPU）")
    real = torch.cuda.is_available
    try:
        torch.cuda.is_available = lambda: False                # 只为了让这条**真实代码路径**跑到
        try:
            dev = resolve_device("cuda")
            line(False, f"cuda 不可用时居然返回了 {dev}（静默回落！）")
        except InputError as e:
            line(True, f"cuda 不可用 ⇒ 报错：{str(e)[:70]}")
    finally:
        torch.cuda.is_available = real
    line(resolve_device("cpu").type == "cpu", "--device cpu 正常（正向对照）")
    if torch.cuda.is_available():
        line(resolve_device("cuda").type == "cuda", "--device cuda 正常（正向对照）")

    print(("SELFTEST PASS" if not fails else f"SELFTEST FAIL（{len(fails)} 条）")
          + f"：{good.name} + {npz.name} @ {tmp}")
    return EXIT_OK if not fails else EXIT_MISMATCH


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="v4-forward-gpu.py",
        description="v4 前向：**python torch 批量**（CUDA/CPU）；只消费已经算好的行级输入张量")
    ap.add_argument("--weights", help="net.bin（格式 2）")
    ap.add_argument("--rows", help="行级张量：.npz / P5 夹具 forward-v4.bin / 紧凑集目录")
    ap.add_argument("--split", default="train", help="--rows 是紧凑集目录时选哪一份切分（缺省 train）")
    ap.add_argument("--traces", help="轨迹目录 → 现场物化行级张量（只调既有 blocks.assemble）")
    ap.add_argument("--policy", default=None, help="物化时只取这一条策略串的行（逐字相等）")
    ap.add_argument("--where-kind", default=None, help="物化时只取 obs.kind 等于它的行（如 claim）")
    ap.add_argument("--ablate", default="", help="消融块 id（逗号分隔；交给 blocks.assemble，本工具不解释）")
    ap.add_argument("--limit", type=int, default=0, help="最多取多少行（0 = 不限）")
    ap.add_argument("--rows-out", default=None, help="把物化出来的张量存成 .npz")
    ap.add_argument("--dump-selected", default=None,
                    help="把**选中的同一批行**落成 g<场号>.jsonl（喂 C++ `v4net` 用同一份输入）")
    ap.add_argument("--index-file", default=None, help="只前向这些全局行号（文本，一行一个）")
    ap.add_argument("--device", default="cuda", help="cuda（缺省）/ cpu")
    ap.add_argument("--batch", type=int, default=512, help="同一候选数桶内一次前向多少行")
    ap.add_argument("--repeat", type=int, default=1,
                    help="同一批行连跑几次（>1 用来把 GPU 的**一次性预热**与稳态吞吐分开；"
                         "每次的 argmax 必须逐行相同 —— 不同就是「同一份输入给出两个答案」，当场报错）")
    ap.add_argument("--threads", type=int, default=8, help="torch CPU 线程数（缺省 8，见 pin_threads）")
    ap.add_argument("--out", default=None, help="输出文件（缺省 stdout）")
    ap.add_argument("--format", choices=["jsonl", "cpp"], default="jsonl",
                    help="jsonl（缺省）或 cpp（与 `trainer v4net` 逐字符同格式，便于直接 diff）")
    ap.add_argument("--tol", type=float, default=1e-5, help="与夹具参考 logits 比的容差（缺省 1e-5）")
    ap.add_argument("--tmp", default=str(REPO / "python" / ".tmp" / "v4-forward-gpu"),
                    help="自检的临时目录（缺省在仓库内：工作区外建目录会被沙箱拒）")
    ap.add_argument("--selftest", action="store_true", help="跑四条自检（含三条负向）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest(Path(a.tmp), a.batch, a.tol)
    if not a.weights:
        ap.error("要么给 --weights（+ --rows 或 --traces），要么给 --selftest")
    if bool(a.rows) == bool(a.traces):
        ap.error("--rows 与 --traces 必须**恰好给一个**（前者读既有张量，后者现场物化）")

    pin_threads(a.threads)
    try:
        device = resolve_device(a.device)
        net = load_net(Path(a.weights), device, tmp=a.tmp)
        if a.rows:
            rows = read_rows(Path(a.rows), a.split)
        else:
            rows = materialize(Path(a.traces), policy=a.policy, where_kind=a.where_kind,
                               limit=a.limit, rows_out=Path(a.rows_out) if a.rows_out else None,
                               dump_selected=a.dump_selected, ablate=a.ablate, quiet=a.quiet)
        _check_rows(rows, net.dims)
        index = None
        if a.index_file:
            index = [int(x) for x in Path(a.index_file).read_text(encoding="utf-8").split() if x.strip()]
            if max(index) >= int(rows.tile.shape[0]):
                raise InputError(f"--index-file 里有越界行号：最大 {max(index)} ≥ 行数 {rows.tile.shape[0]}")
        res = forward(net, rows, device, a.batch, index=index, quiet=a.quiet)
        if a.repeat > 1:
            # ⚠ **冷/热必须分开报**：CUDA 的第一次调用要建 cuBLAS/cuDNN 句柄 + 载入 sm_120 的内核
            #   （本机实测 ~15 s），把它摊进吞吐就会得出"GPU 比 CPU 慢"的假结论。
            #   同时每次的 argmax 必须逐行相同 —— 否则就是"同一份输入两个答案"，不是性能问题。
            print(f"[rep] device={a.device} run 1（冷）: {res.seconds:.3f}s "
                  f"（{len(res.n) / max(res.seconds, 1e-9):,.0f} 行/秒）", file=sys.stderr)
            for k in range(2, a.repeat + 1):
                again = forward(net, rows, device, a.batch, index=index, quiet=True)
                if again.argmax != res.argmax:
                    bad = sum(1 for x, y in zip(again.argmax, res.argmax) if x != y)
                    raise InputError(f"--repeat 第 {k} 次的 argmax 与第 1 次有 {bad} 行不同 —— "
                                     f"同一份输入给出两个答案")
                print(f"[rep] device={a.device} run {k}（热）: {again.seconds:.3f}s "
                      f"（{len(again.n) / max(again.seconds, 1e-9):,.0f} 行/秒）", file=sys.stderr)
        steps = rows.steps if index is None else [rows.steps[i] for i in index]
        write_out(Path(a.out) if a.out else None, res, a.format, steps=steps)
        if rows.ref_policy is not None:
            _, worst, bad = compare_ref(rows, res, a.tol)
            msg = f"[ref] 与夹具里的 Python 参考 logits：{'一致' if not bad else '不一致'}"
            print(f"{msg}（maxΔ={worst:.3e}，tol={a.tol:g}）", file=sys.stderr)
            for b in bad[:5]:
                print(f"  ✗ {b}", file=sys.stderr)
            if bad or worst > a.tol:
                return EXIT_MISMATCH
        if not a.quiet:
            print(f"[done] {rows.source} → {len(res.n)} 行；{res.seconds:.3f}s；"
                  f"{len(res.n) / max(res.seconds, 1e-9):,.0f} 行/秒；device={a.device}",
                  file=sys.stderr)
        return EXIT_OK
    except InputError as e:
        print(f"[v4-forward-gpu] ⛔ {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
