"""特权 sidecar（`g*.priv.npz`）的读端 —— **只给训练期的 privileged critic 用**。

⛔ 这个模块的输出**绝不**能进 `obs` / `net.bin` / 推理（`AGENTS.md` §6.5 判据 22①）。
写端 = `server/src/main/java/mahjong/train/TraceRecorder.java`（`--priv` 采集时写）。

一个 sidecar 与同名的 `g*.jsonl` **逐决策同序同长**，每一行带行键
`(game, hand_no, step, seat)` —— 与 compact 的 `game/hand_no/seat` 同一套编号，
`step` = 该场**被记录**的决策序号（与轨迹行的 `step` 字段逐位相同）。

三条特权量（`B` 口径：①他家手牌 ②牌山顺序 ③里宝）：

| 键 | 形状 | 含义 |
| --- | --- | --- |
| `opp_hand` | `[n,3,34]` uint8 | 三家对手**暗牌计数**（相对方位：0 = 下家），与 `g*.aux.npz` 的 `opp_hand` 逐位相同 |
| `wall_next` | `[n,14]` uint8 | **可摸牌山剩余顺序**的前 14 张（牌种 0..33；不足补 255） |
| `ura` | `[n,5]` uint8 | **里宝指示牌**（已翻开 1..5 张；牌种；未翻开的槽 = 255；同一小局内恒定） |

⚠ 读端**fail-closed**：版本/形状/行键不符一律抛错，⛔ 不"补 0 继续跑"——
否则训练会把"没有特权信息"当成"特权信息恰好是 0"，判据 ②③ 的读数就全是假的。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

#: 与 Java `TraceRecorder.PRIV_VERSION` 同步（字段增删要一起 +1）。
PRIV_VERSION = 1
#: 与 Java `TraceRecorder.PRIV_WALL_K` 同步。
WALL_K = 14


class PrivError(RuntimeError):
    """特权 sidecar 缺失 / 版本不符 / 行键不齐 —— 一律响亮失败。"""


def priv_path(jsonl: Path) -> Path:
    """`g3.jsonl` → `g3.priv.npz`（与 `dataset.sidecar_path` 同一套命名）。"""
    return Path(jsonl).with_suffix(".priv.npz")


def load_priv(path: Path) -> dict:
    """读一个 `g*.priv.npz`（返回 numpy 数组 + `meta`），并做**结构级**硬校验。"""
    path = Path(path)
    if not path.exists():
        raise PrivError(
            f"缺特权 sidecar：{path}（采集要加 `--priv`；⛔ 不许拿全 0 顶替 —— "
            "那会把「没有特权信息」训成「特权信息是 0」）")
    z = np.load(path, allow_pickle=False)
    need = {"game", "hand_no", "step", "seat", "opp_hand", "wall_next", "ura", "meta"}
    missing = need - set(z.files)
    if missing:
        raise PrivError(f"{path.name}: 缺成员 {sorted(missing)}（有 {sorted(z.files)}）")
    meta = json.loads(bytes(z["meta"]).decode("utf-8"))
    if int(meta.get("priv_version", -1)) != PRIV_VERSION:
        raise PrivError(f"{path.name}: priv_version={meta.get('priv_version')} != {PRIV_VERSION}")
    n = int(meta["n"])
    want = {"opp_hand": (n, 3, 34), "wall_next": (n, WALL_K), "ura": (n, 5),
            "game": (n,), "hand_no": (n,), "step": (n,), "seat": (n,)}
    for k, shape in want.items():
        got = tuple(z[k].shape)
        if got != shape:
            raise PrivError(f"{path.name}: {k} 形状 {got} != {shape}")
    if int(meta.get("wall_k", -1)) != WALL_K:
        raise PrivError(f"{path.name}: wall_k={meta.get('wall_k')} != {WALL_K}")
    return {k: z[k] for k in need - {"meta"}} | {"meta": meta, "n": n}


def check_against_decision_rows(priv: dict, decisions: list[dict], name: str = "") -> None:
    """行键与轨迹的决策行**逐行**核对（`game/hand_no/step/seat` 四件都查）。"""
    n = len(decisions)
    if priv["n"] != n:
        raise PrivError(f"{name}: priv {priv['n']} 行 != 决策 {n} 行")
    for i, row in enumerate(decisions):
        got = (int(priv["game"][i]), int(priv["hand_no"][i]),
               int(priv["step"][i]), int(priv["seat"][i]))
        want = (int(row["game"]), int(row["hand_no"]), int(row["step"]), int(row["seat"]))
        if got != want:
            raise PrivError(f"{name}: 第 {i} 行行键 {got} != 轨迹 {want}")
