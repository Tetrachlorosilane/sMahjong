"""v4 的两级缓存与循环推理（设计：`docs/TRAINING-V4.md` §5.3）。

**L1 张量级**：`RiverState` 只跟踪"三家牌河"的 6 组计数（规范 §4.1 的 `tile.per_opp` 段），
事件到达时只改**一个牌种的一格**，而不是重算整张 `tile` 矩阵。

**L2 表示级**：`EventStream` 只把**新事件**编码并推进 GRU 隐状态 `h`；`recompute()` 是
"从头重算"的基准路径 —— 两者必须逐元素一致（规范 §9 判据④），这是缓存正确性的**唯一**判据。

⚠ 设计文档 §5.3 的三条纪律：① 隐状态**每小局清零**；② 缓存必须能从当前 obs 完整重建；
③ 自检/对拍永远用无缓存路径当基准。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np

from .. import features as f3
from . import blocks, spec
from .spec import C_EVT, C_TILE, K_EVT, ContractError

N_PLAYERS = 4


@dataclass
class RiverState:
    """三家的牌河计数（6 组 × 3 家），事件驱动增量更新。

    组序与 `blocks.TILE_LAYOUT` 一致：`all / before / after / tedashi / tsumogiri / meld`。
    """

    seat: int
    per: np.ndarray = field(default_factory=lambda: np.zeros((f3.KIND_COUNT, 18), dtype=np.float32))
    events_seen: int = 0

    def _col(self, actor: int) -> int:
        rel = (int(actor) - self.seat) % N_PLAYERS
        if rel == 0:
            return -1                       # 自家牌河不进 tile（在 evt 里）
        return rel - 1

    def apply(self, event: Mapping[str, Any]) -> bool:
        """吃一条事件；返回**是否改动了**张量（False = 无关事件，可直接跳过）。"""
        self.events_seen += 1
        typ = str(event.get("type", ""))
        if typ not in ("discard", "meld", "dora_flip", "kan"):
            return False
        if typ in ("meld", "kan"):
            # ⚠ **杠也要算进 `meld_count`**：obs v3 里杠是**自己的** `type`（它翻宝牌、给岭上牌），
            #   不是 `meld` 的一种写法 —— 只认 `meld` 会让增量路径漏掉杠的那 4 张，
            #   而全量路径（`blocks.tile_matrix`）读 `obs.melds` 是把杠算进去的 ⇒ 判据④
            #   "增量 == 全量"在**有杠的局面**上静默失效（风险 R1：练歪且不报错）。
            col = self._col(event.get("actor", -1))
            if col < 0:
                return False
            for code in event.get("meld_tiles") or (event.get("tiles") or ()):
                k = f3.tile_kind(str(code))
                if k >= 0:
                    self.per[k, 15 + col] += 1.0
            return True
        if typ != "discard":
            return False
        col = self._col(event.get("actor", -1))
        if col < 0:
            return False
        k = f3.tile_kind(str(event.get("tile") or ""))
        if k < 0:
            return False
        self.per[k, col] += 1.0                                   # river_all
        self.per[k, (6 if event.get("rip_phase") else 3) + col] += 1.0
        self.per[k, (12 if event.get("tsumogiri") else 9) + col] += 1.0
        return True

    def channels(self) -> np.ndarray:
        return self.per.copy()

    @classmethod
    def full_recompute(cls, obs: Mapping[str, Any]) -> "RiverState":
        """基准路径：从**完整事件流**（或 obs v2 的 `discards`）重建。"""
        st = cls(seat=int(obs.get("seat", 0)))
        events = obs.get("events")
        if events is not None:
            for e in events:
                st.apply(e)
            return st
        # obs v2：只有"整条牌河"，没有逐张属性 ⇒ before 全量 / after=tedashi=tsumogiri=0
        discards = obs.get("discards") or []
        for abs_s in range(N_PLAYERS):
            col = st._col(abs_s)
            if col < 0 or abs_s >= len(discards):
                continue
            for code in discards[abs_s] or ():
                k = f3.tile_kind(str(code))
                if k >= 0:
                    st.per[k, col] += 1.0
                    st.per[k, 3 + col] += 1.0
        return st


class EventStream:
    """只追加的事件缓冲 + 增量 GRU 状态（L2）。`K_EVT` 窗口之外由隐状态承担。"""

    def __init__(self, encoder, *, maxlen: int = K_EVT) -> None:
        self.encoder = encoder                    # 一个 nn.Module：tokens -> emb
        self.maxlen = maxlen
        self.buf: list[Mapping[str, Any]] = []
        self.tokens: np.ndarray = np.zeros((maxlen, C_EVT), dtype=np.float32)
        self.h = None
        self.pending: list[Mapping[str, Any]] = []

    def push(self, event: Mapping[str, Any]) -> None:
        self.buf.append(event)
        self.pending.append(event)

    def step(self):
        """把**新事件**编码并推进隐状态（返回 h；没有新事件就返回当前 h）。"""
        if self.pending:
            obs_like = {"v": spec.OBS_VERSION_V4, "seat": 0, "events": self.pending}
            new_tokens = blocks.event_matrix(obs_like)[-len(self.pending):]
            self.h = self.encoder(new_tokens, self.h)
            self.pending.clear()
        return self.h

    def window(self) -> np.ndarray:
        """最近 K 条事件的 token 矩阵（与 `blocks.event_matrix` 同源、同序）。"""
        obs_like = {"v": spec.OBS_VERSION_V4, "seat": 0, "events": self.buf}
        return blocks.event_matrix(obs_like)

    def recompute(self):
        """基准路径：**从头**把所有事件重放一遍（判据④的右半边）。

        ⚠ 只喂**真实事件**的那些 token —— 窗口前面是 padding，把 56 个全零 token 也喂进 GRU
        会改变隐状态（`h_t = GRUCell(0, h_{t-1})` 并不是恒等），曾因此让 Δ=1.76e-2。
        """
        if not self.buf:
            return self.h
        obs_like = {"v": spec.OBS_VERSION_V4, "seat": 0, "events": self.buf}
        toks = blocks.event_matrix(obs_like)[-len(self.buf):]
        return self.encoder(toks, None)

    def reset(self) -> None:
        """每小局清零（设计文档 §5.3 纪律①）。"""
        self.buf.clear()
        self.pending.clear()
        self.h = None


def incremental_equals_full(obs: Mapping[str, Any], *, atol: float = 1e-5) -> tuple[bool, float]:
    """判据④（张量级）：把事件流"逐条喂"与"一次性重算"比较。返回 (是否一致, 最大差)。"""
    seat = int(obs.get("seat", 0))
    st = RiverState(seat=seat)
    for e in (obs.get("events") or []):
        st.apply(e)
    ref = RiverState.full_recompute(obs)
    d = float(np.abs(st.channels() - ref.channels()).max()) if obs.get("events") is not None else 0.0
    return d <= atol, d
