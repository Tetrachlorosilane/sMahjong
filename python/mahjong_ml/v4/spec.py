"""v4 输入框架的**唯一注册表**（规范：`docs/FEATURES-V4.md` §3 / §4 / §6 / §7）。

这一层只做三件事，别塞别的：

1. **声明**每个信息块（`Block`）的 id / 宽度 / 依赖的 obs 字段 / 需要的 sidecar 版本；
2. 把 `obs`（+sidecar）**拼装**成四个张量 `tile / evt / ctx / cand`（`assemble`）；
3. 给出**块清单指纹**（`fingerprint`）——它写进 `net.bin` 格式 2，加载时逐块核对（规范 §6）。

⚠ 三条纪律（违反了就是静默漂移）：

- **`requires_obs` 缺字段必须报错**，不许填 0 —— 填 0 等于"悄悄少给信息"，训练出来才发现。
  只有显式 `allow_degraded=True`（兼容 obs v2 老数据）才降级，且**降级过的块 id 会记在
  `Tensors.degraded` 里**，由自检与台账一起盯着。
- **四家块一律旋转到自己为下标 0**（v3 起的老纪律，规范 §1.3）。
- **块只增不改**：改宽度 = 改 `FEATURE_VERSION`，老权重/紧凑集构造期拒绝（规范 §6）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .. import features as f3

# ------------------------------------------------------------------ 版本
#: obs 格式版本（v4 要求；obs v2 缺 `events` / `riichi_turn` / 逐张属性 ⇒ 只能降级跑）
OBS_VERSION_V4 = 3
#: 张量布局版本（写进 net.bin；与 v3 的 3 不同）
FEATURE_VERSION_V4 = 4
#: 派生段版本（sidecar 头部）
DERIVED_VERSION_V4 = 3

#: 张量形状（规范 §3）——**单一来源**，三端（Python/Java/C++）都从这里抄
C_TILE = 48
C_EVT = 96
C_CTX = 64
C_CAND = 128
K_EVT = 60

#: 候选派生段：v3 的 8 维（逐候选）+ v4 新增 3 维（打点期望 / 打后危险度两种口径）
CAND_DERIVED = 11
CAND_RESERVED = C_CAND - 88 - CAND_DERIVED          # = 29

#: `evt` 的字段布局（96 = 8+34+1+34+1+4+4+5+1+1+1+1+1，规范 §4.2）
EVT_TYPES = ("draw", "discard", "meld", "riichi", "kan", "dora_flip", "agari", "ryuukyoku")
EVT_LAYOUT: tuple[tuple[str, int], ...] = (
    ("type", len(EVT_TYPES)),        # 0..8
    ("tile_kind", f3.KIND_COUNT),    # 8..42
    ("tile_aka", 1),                 # 42
    ("called_kind", f3.KIND_COUNT),  # 43..77
    ("called_aka", 1),               # 77
    ("actor", 4),                    # 78..82
    ("from", 4),                     # 82..86
    ("meld_kind", len(f3.MELD_KINDS)),   # 86..91
    ("turn", 1),                     # 91
    ("tsumogiri", 1),                # 92
    ("sideways", 1),                 # 93
    ("rip_phase", 1),                # 94
    ("seq_delta", 1),                # 95
)

#: `tile` 的通道布局（48 = 3 + 30 + 13 + 2，规范 §4.1）
TILE_LAYOUT: tuple[tuple[str, int], ...] = (
    ("own_count", 1), ("own_aka", 1), ("own_drawn", 1),                      # 自家 3
    ("river_all", 3), ("river_before_riichi", 3), ("river_after_riichi", 3),  # 三家 × 12
    ("river_tedashi", 3), ("river_tsumogiri", 3), ("meld_count", 3),
    ("safety_genbutsu", 3), ("safety_suji", 3), ("danger_all", 3), ("danger_riichi", 3),
    ("visible", 1), ("unseen", 1), ("drawable", 1), ("dora_indicator", 1),    # 全局 15
    ("is_dora", 1), ("is_aka", 1), ("is_yakuhai", 1), ("is_terminal", 1), ("is_honor", 1),
    ("suit_m", 1), ("suit_p", 1), ("suit_s", 1),
    ("reserved", 3),
)

#: `ctx` 的通道布局（64，规范 §4.4）
CTX_LAYOUT: tuple[tuple[str, int], ...] = (
    ("round", 8), ("points", 12), ("wall", 8), ("self", 10), ("seats", 12), ("ask", 8), ("reserved", 6),
)
for _n, _w in (("TILE", C_TILE), ("EVT", C_EVT), ("CTX", C_CTX), ("CAND", C_CAND)):
    _layout = {"TILE": TILE_LAYOUT, "EVT": EVT_LAYOUT, "CTX": CTX_LAYOUT, "CAND": None}[_n]
    if _layout is not None:
        _sum = sum(w for _, w in _layout)
        assert _sum == _w, f"{_n}_LAYOUT 合计 {_sum} != {_w}"


class ContractError(RuntimeError):
    """契约违反（缺 obs 字段 / 版本不符 / 越界块 id）——**必须报错，不许填 0**。"""


@dataclass(frozen=True)
class Block:
    """一个信息块：id 同时是**消融开关**（规范 §7）。"""

    bid: str
    tensor: str                     # tile / evt / ctx / cand
    width: int
    requires_obs: tuple[str, ...] = ()
    needs_sidecar: int | None = None    # 需要的 derived_version
    needs_obs_version: int = 2          # obs v3 才有的块填 3
    note: str = ""


def _blocks() -> tuple[Block, ...]:
    b: list[Block] = []
    # ---- tile（自家 / 三家 / 全局）——宽度与 TILE_LAYOUT 的分组一一对应
    b.append(Block("tile.own", "tile", 3, ("hand", "hand_red")))
    per_opp = ("discards", "melds", "riichi")
    b.append(Block("tile.per_opp", "tile", 18, per_opp,
                   note="river_all/before/after + tedashi/tsumogiri + meld_count（6 组 × 3 家）；"
                        "before/after 分区与 tedashi/tsumogiri 需要 obs v3 的逐张属性"))
    b.append(Block("tile.safety", "tile", 6, per_opp, needs_sidecar=DERIVED_VERSION_V4,
                   note="现物 / 筋壁（引擎 Danger 的判据）"))
    b.append(Block("tile.danger", "tile", 6, per_opp, needs_sidecar=DERIVED_VERSION_V4,
                   note="逐家危险度：对四家 / 只对已立直家"))
    b.append(Block("tile.global", "tile", 15, ("visible", "dora_indicators"),
                   note="可见/剩余/可摸 + 宝牌指示 + 静态牌性（12 实 + 3 预留）"))
    # ---- evt（事件流：v4 的核心增量）
    b.append(Block("evt.stream", "evt", C_EVT, ("events",), needs_obs_version=3,
                   note="最近 K=60 条公开事件（有序、只追加）"))
    # ---- ctx
    b.append(Block("ctx.round", "ctx", 8, ("round",)))
    b.append(Block("ctx.points", "ctx", 12, ("scores",)))
    b.append(Block("ctx.wall", "ctx", 8, ("tiles_left", "dead_wall_left")))
    b.append(Block("ctx.self", "ctx", 10, ("seat", "menzen", "self_riichi", "furiten")))
    b.append(Block("ctx.seats", "ctx", 12, ("riichi", "ippatsu", "riichi_turn"),
                   needs_obs_version=3, note="立直巡数（v3 只有布尔，规范 §2 硬伤③）"))
    b.append(Block("ctx.ask", "ctx", 8, (), note="询问上下文；缺 `kind` 时按 turn 处理"))
    b.append(Block("ctx.reserved", "ctx", 6))
    # ---- cand
    b.append(Block("cand.base", "cand", 88, ("legal",), note="沿用 v3 的 type/tile/tiles/tsumogiri/kan/noarg"))
    b.append(Block("cand.derived", "cand", CAND_DERIVED, ("legal",), needs_sidecar=DERIVED_VERSION_V4,
                   note="打后向听/进张/听牌形/宝牌 + v4 新增的打点期望与打后危险度"))
    b.append(Block("cand.reserved", "cand", CAND_RESERVED))
    return tuple(b)


BLOCKS: tuple[Block, ...] = _blocks()
_BY_ID: dict[str, Block] = {b.bid: b for b in BLOCKS}

#: ⚠ 块宽度必须**铺满**每个张量（少一块 = 有通道没人负责；多一块 = 张量装不下）
_TENSOR_WIDTH = {"tile": C_TILE, "evt": C_EVT, "ctx": C_CTX, "cand": C_CAND}
for _t, _w in _TENSOR_WIDTH.items():
    _sum = sum(b.width for b in BLOCKS if b.tensor == _t)
    assert _sum == _w, f"{_t}：块宽度合计 {_sum} != {_w}（BLOCKS 与张量布局脱节）"


def block(bid: str) -> Block:
    if bid not in _BY_ID:
        raise ContractError(f"未注册的块 id：{bid}（可用：{', '.join(_BY_ID)}）")
    return _BY_ID[bid]


def blocks_of(tensor: str) -> tuple[Block, ...]:
    out = tuple(b for b in BLOCKS if b.tensor == tensor)
    if not out:
        raise ContractError(f"没有属于 {tensor} 的块")
    return out


def block_slices() -> dict[str, tuple[str, int, int]]:
    """块 id → `(张量, 起始通道, 宽度)`：块在 `BLOCKS` 里就是**按布局顺序**声明的
    （每个张量的块宽度合计 == 该张量宽度，见上面的断言），所以累加即可。

    用途：**消融矩阵**要在紧凑集上把某块整段置 0（`docs/TRAINING-V4.md` §8.3/§14 P0）——
    没有这张表就只能手抄通道号（必然漂移）。
    """
    out: dict[str, tuple[str, int, int]] = {}
    off: dict[str, int] = {}
    for b in BLOCKS:
        start = off.get(b.tensor, 0)
        out[b.bid] = (b.tensor, start, b.width)
        off[b.tensor] = start + b.width
    for t, want in _TENSOR_WIDTH.items():
        if off.get(t, 0) != want:
            raise ContractError(f"{t} 的块偏移合计 {off.get(t, 0)} != {want}")
    return out


def fingerprint(blocks: Sequence[Block] = BLOCKS) -> str:
    """块清单指纹（有序 id+width）→ 写进 `net.bin`/`net.json`，加载时逐块核对。"""
    payload = json.dumps([[b.bid, b.width] for b in blocks], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class Tensors:
    """一次决策的输入（规范 §3）。`degraded` 记录**降级过的块**（自检与台账都盯它）。"""

    tile: np.ndarray            # [34, C_TILE]
    evt: np.ndarray             # [K, C_EVT]（新事件在**尾部**，与增量缓存同序）
    ctx: np.ndarray             # [C_CTX]
    cand: np.ndarray            # [n, C_CAND]
    legal: list[str] = field(default_factory=list)
    degraded: set[str] = field(default_factory=set)
    ablated: set[str] = field(default_factory=set)
    obs_version: int = OBS_VERSION_V4

    def as_dict(self) -> dict[str, np.ndarray]:
        return {"tile": self.tile, "evt": self.evt, "ctx": self.ctx, "cand": self.cand}

    def shapes(self) -> dict[str, tuple[int, ...]]:
        return {k: tuple(v.shape) for k, v in self.as_dict().items()}


def _need(obs: Mapping[str, Any], block_: Block, *, allow_degraded: bool,
          degraded: set[str]) -> bool:
    """块依赖的 obs 字段齐不齐；不齐**报错**（除非显式降级）。返回是否已降级。"""
    missing = [k for k in block_.requires_obs if obs.get(k) is None]
    if not missing:
        return False
    if not allow_degraded:
        raise ContractError(f"{block_.bid} 需要 obs 字段 {missing}，但报文里没有"
                            f"（要兼容老数据就显式 allow_degraded=True，降级会被记录下来）")
    degraded.add(block_.bid)
    return True


def check_versions(obs: Mapping[str, Any], *, allow_degraded: bool = False) -> int:
    """obs 版本体检：v4 要求 `OBS_VERSION_V4`；低于它只能降级跑。"""
    v = int(obs.get("v", 2))
    if v < OBS_VERSION_V4 and not allow_degraded:
        raise ContractError(f"obs v{v} < v{OBS_VERSION_V4}：v4 需要事件流与逐张属性"
                            f"（要跑兼容路径就 allow_degraded=True）")
    return v


def declare(obs: Mapping[str, Any], *, allow_degraded: bool = False) -> tuple[int, set[str]]:
    """**只体检不拼装**：返回 (obs_version, 会降级的块集合)。准备阶段/台账用。"""
    degraded: set[str] = set()
    v = check_versions(obs, allow_degraded=allow_degraded)
    for b in BLOCKS:
        if b.needs_obs_version > v:
            if not allow_degraded:
                raise ContractError(f"{b.bid} 需要 obs v{b.needs_obs_version}，当前 v{v}")
            degraded.add(b.bid)
            continue
        _need(obs, b, allow_degraded=allow_degraded, degraded=degraded)
    return v, degraded


def summary() -> str:
    """给 `verify_env` / `v4 check` 打印的块清单。"""
    lines = [f"FEATURE_VERSION={FEATURE_VERSION_V4} obs>={OBS_VERSION_V4} "
             f"derived={DERIVED_VERSION_V4} fingerprint={fingerprint()}",
             f"tile[34,{C_TILE}] evt[{K_EVT},{C_EVT}] ctx[{C_CTX}] cand[n,{C_CAND}]"]
    for t in ("tile", "evt", "ctx", "cand"):
        for b in blocks_of(t):
            flag = f" (obs>={b.needs_obs_version})" if b.needs_obs_version > 2 else ""
            lines.append(f"  {b.bid:16} {t}[{b.width}]{flag}")
    return "\n".join(lines)
