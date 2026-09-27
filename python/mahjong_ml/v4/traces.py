"""v4 **数据集层的唯一入口**：轨迹（`g*.jsonl`）+ 派生特征 sidecar（`g*.feat.bin`）。

## 为什么要有这一层

`blocks.assemble(..., allow_degraded=True)` 那条**降级**路径是给"老数据诊断 / 对照实验"用的；
**数据集构建绝不许走它** —— 因为两种降级都是**静默**的：

| 降级 | 后果 |
| --- | --- |
| obs v2（没有 `events`） | `evt` 张量整块全 0 ⇒ 等于换了个任务，训练照样跑、指标照样出 |
| sidecar `derived_version` 不符 | `tile.danger` / `tile.safety` / `cand.derived` 填 0 ⇒ 相当于把"危险度"这一路信息删掉 |

所以 v4 的数据构建只能走这里：`iter_decisions()` 逐行读轨迹并在**任何降级发生之前**抛
`spec.ContractError`；`load_sidecar()` / `sidecar_dict()` 同理（版本与长度都在
`mahjong_ml.dataset.load_sidecar` 里硬校验）。**判据 = `v4 check` 与 `python/selfcheck.py`
里的正反两条**（v3 通过 / v2 报错），不是"文档里写了"。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import numpy as np

from .. import dataset as _dataset
from .. import features as _features
from . import spec

#: 轨迹里"一条决策"的 obs 必须达到的 obs 版本（v4 = 3）。
REQUIRED_OBS_VERSION = spec.OBS_VERSION_V4


def gate_obs(obs: Mapping[str, Any], *, where: str = "<obs>") -> int:
    """obs 版本闸门：低于 `REQUIRED_OBS_VERSION` **一律报错**（不降级、不填 0）。

    返回 obs 版本，方便调用方顺手打日志。
    """
    v = int(obs.get("v", 2))
    if v < REQUIRED_OBS_VERSION:
        raise spec.ContractError(
            f"{where}: obs v{v} < v{REQUIRED_OBS_VERSION} —— v4 数据集**不接受**没有事件流的老轨迹"
            f"（`events` 缺失会让 evt 张量静默全 0）。请用 obs v{REQUIRED_OBS_VERSION} 重新采集"
            f"（`docs/TRAINING-V4.md` §「P0 剩余工作」纪律②）")
    return v


def data_dir(path: str | Path) -> Path:
    """轨迹目录（不存在就报错——**不**静默返回空集）。"""
    d = Path(path)
    if not d.is_dir():
        raise spec.ContractError(f"轨迹目录不存在：{d}")
    return d


def trace_files(directory: str | Path) -> list[Path]:
    """目录里的 `g<序号>.jsonl`（按序号排）。"""
    d = data_dir(directory)
    out = [p for p in d.iterdir() if p.is_file() and p.name.startswith("g")
           and p.suffix == ".jsonl"]
    if not out:
        raise spec.ContractError(f"目录里没有 g*.jsonl：{d}")
    return sorted(out, key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0)


def iter_decisions(directory: str | Path) -> Iterator[tuple[Path, int, dict]]:
    """逐条产出 `(文件, 行号, 决策行)`；每条的 obs 都过 `gate_obs`。

    ⚠ 坏行 / 非法的 obs 版本**直接抛错**（数据要可信，不要跳过 —— 与 `dataset.iter_decisions`
    同一条纪律：跳过的样本会静默改变数据分布）。
    """
    for f in trace_files(directory):
        with f.open(encoding="utf-8") as fh:
            for ln, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("type") != "decision":
                    continue
                obs = row.get("obs")
                if not isinstance(obs, Mapping):
                    raise spec.ContractError(f"{f.name}:{ln} 决策行没有 obs")
                gate_obs(obs, where=f"{f.name}:{ln}")
                yield f, ln, row


def load_sidecar(path: str | Path) -> dict:
    """读一份 sidecar 并**再确认一次**派生段版本（`mahjong_ml.dataset.load_sidecar` 已按
    `features.DERIVED_VERSION` 硬校验；这里把它与 v4 要求的 `spec.DERIVED_VERSION_V4` 对齐）。"""
    sc = _dataset.load_sidecar(path)
    if int(sc["ver"]) != spec.DERIVED_VERSION_V4:
        raise spec.ContractError(
            f"{Path(path).name}: sidecar 派生段版本 {sc['ver']} != v4 要求的 "
            f"{spec.DERIVED_VERSION_V4} —— 重新 --features（`docs/TRAINING-V4.md` 第 4 步）")
    return sc


def unpack_bitmap(packed: np.ndarray) -> np.ndarray:
    """位图 `(..., 5)` → `(..., 34)` 的 0/1 数组（**字节内 LSB 在前**，与 Java/C++ 同约定）。"""
    bits = np.unpackbits(np.asarray(packed, dtype=np.uint8), axis=-1, bitorder="little")
    return bits[..., :_features.KIND_COUNT]


def sidecar_dict(sc: Mapping[str, Any], i: int) -> dict[str, Any]:
    """把整份 sidecar 的**第 i 条决策**转成 `blocks.assemble(obs, sidecar)` 要的那张表。

    ⚠ 位图在这里展开成 34 宽（文件里是打包的 5 字节）—— 特征侧要的是"每个牌种一位"。

    ⚠ **`cand`（逐候选 8 维，C 段）必须带进来**：少了它就是 `cand[88:96]` 静默全 0
    （= 把"牌效 / 危险"这一路信息整块删掉，而训练照跑、指标照出）。2026-09-27 实测
    `compact/v4-bc-002`：`cand[88:128]` **逐列 maxabs = 0**（262,095 条决策全零），
    原因就是这里没给 `cand` 而 `dataset` 又没查 `Tensors.degraded`。判据：
    `python/selfcheck.py` 的「v4 数据集不许有降级块」+ `dataset._write_file` 的硬拒。
    """
    if i < 0 or i >= int(sc["n"]):
        raise spec.ContractError(f"sidecar 里没有第 {i} 条决策（共 {int(sc['n'])} 条）")
    lo, hi = int(sc["offsets"][i]), int(sc["offsets"][i + 1])
    cand = np.asarray(sc["cand"][lo:hi], dtype=np.float32)
    # 逐候选段必须与这一条决策的候选数**逐条对齐**（错位 = 把别人的牌效目标当输入，且不报错）
    n_legal = int(sc["nlegal"][i])
    if cand.shape[0] != n_legal:
        raise spec.ContractError(
            f"sidecar 第 {i} 条：逐候选段 {cand.shape[0]} 行 != nLegal {n_legal} —— sidecar 坏了")
    return {
        "derived_version": spec.DERIVED_VERSION_V4,
        "danger_per_seat": np.asarray(sc["danger_per_seat"][i], dtype=np.float32),
        "danger_riichi_per_seat": np.asarray(sc["danger_riichi_per_seat"][i], dtype=np.float32),
        "genbutsu_per_seat": unpack_bitmap(sc["genbutsu_per_seat"][i]).astype(np.float32),
        "suji_per_seat": unpack_bitmap(sc["suji_per_seat"][i]).astype(np.float32),
        "cand": cand,
    }


__all__ = ["REQUIRED_OBS_VERSION", "gate_obs", "data_dir", "trace_files", "iter_decisions",
           "load_sidecar", "unpack_bitmap", "sidecar_dict"]
