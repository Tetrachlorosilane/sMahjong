"""v4 的两级缓存与循环推理（设计：`docs/TRAINING-V4.md` §5.3）。

**L1 张量级**：`RiverState` 只跟踪"三家牌河"的 6 组计数（规范 §4.1 的 `tile.per_opp` 段），
事件到达时只改**一个牌种的一格**，而不是重算整张 `tile` 矩阵。

**L2 表示级**：`EventStream` 只把**新事件**编码并推进 GRU 隐状态 `h`；`recompute()` 是
"从头重算"的基准路径 —— 两者必须逐元素一致（规范 §9 判据④），这是缓存正确性的**唯一**判据。

⚠ 设计文档 §5.3 的三条纪律：① 隐状态**每小局清零**；② 缓存必须能从当前 obs 完整重建；
③ 自检/对拍永远用无缓存路径当基准。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from .. import features as f3
from . import blocks, spec
from .spec import C_EVT, C_TILE, K_EVT, ContractError

N_PLAYERS = 4

#: `CarryTracker` 的**前缀校验**只比尾部这么多条事件。
#: 为什么不是整条比：一手能有两百条事件、每条决策都要校验 ⇒ 整条比是 O(n²)。
#: 为什么不能不比：`obs.events` 是"同一手内只追加"的**约定**，不是数据结构保证的 ——
#: 换了一手/换了文件而恰好前缀相同（或调用方把事件的语义换了）就会**静默接错状态**，
#: 而错的状态不报错、只是让训练与上线不同源（W1 之前根本看不见）。Java 的 `V4Cache.overlapOk`
#: 是同一套纪律（它比窗口内的编码行，这里比事件本身 —— 更细、更保守：多比出来的差异只会多一次重放）。
_OVERLAP_CHECK = 8


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

    def __init__(self, encoder, *, maxlen: int = K_EVT, seat: int = 0) -> None:
        self.encoder = encoder                    # 一个 nn.Module：tokens -> emb
        self.maxlen = maxlen
        #: ⚠ **座位必须显式给**：事件 token 里的 `actor`/`from` 是**相对自己**的 one-hot
        #:   （`blocks.event_matrix` 按 `obs.seat` 编码）⇒ 写死 0 会让非 0 号座位的事件行
        #:   与训练/推理用的窗口**不是同一批浮点值**，而"增量 == 全量"因为两边都写死 0
        #:   照样全绿（假绿：自洽但与真实窗口不同源）。
        self.seat = int(seat)
        self.buf: list[Mapping[str, Any]] = []
        self.tokens: np.ndarray = np.zeros((maxlen, C_EVT), dtype=np.float32)
        self.h = None
        self.pending: list[Mapping[str, Any]] = []

    def push(self, event: Mapping[str, Any]) -> None:
        self.buf.append(event)
        self.pending.append(event)

    def step(self):
        """把**新事件**编码并推进隐状态（返回 h；没有新事件就返回当前 h）。

        ⚠ 一次 `pending` 不能超过 `maxlen`：`event_matrix` 只取最后 K 条，
        超了会**静默丢掉**前面的（要喂长前缀请分块，见 `carry_prefix`）。
        """
        if self.pending:
            obs_like = {"v": spec.OBS_VERSION_V4, "seat": self.seat, "events": self.pending}
            new_tokens = blocks.event_matrix(obs_like)[-len(self.pending):]
            self.h = self.encoder(new_tokens, self.h)
            self.pending.clear()
        return self.h

    def window(self) -> np.ndarray:
        """最近 K 条事件的 token 矩阵（与 `blocks.event_matrix` 同源、同序）。"""
        obs_like = {"v": spec.OBS_VERSION_V4, "seat": self.seat, "events": self.buf}
        return blocks.event_matrix(obs_like)

    def recompute(self):
        """基准路径：**从头**把所有事件重放一遍（判据④的右半边）。

        ⚠ 只喂**真实事件**的那些 token —— 窗口前面是 padding，把 56 个全零 token 也喂进 GRU
        会改变隐状态（`h_t = GRUCell(0, h_{t-1})` 并不是恒等），曾因此让 Δ=1.76e-2。
        """
        if not self.buf:
            return self.h
        obs_like = {"v": spec.OBS_VERSION_V4, "seat": self.seat, "events": self.buf}
        toks = blocks.event_matrix(obs_like)[-len(self.buf):]
        return self.encoder(toks, None)

    def reset(self) -> None:
        """每小局清零（设计文档 §5.3 纪律①）。"""
        self.buf.clear()
        self.pending.clear()
        self.h = None


def carry_prefix(encoder, obs: Mapping[str, Any], upto: int):
    """`obs.events[0, upto)` → GRU 的 **carry**（`upto == 0` ⇒ `None` = 全 0 冷启动）。

    ⚠ 它是 `CarryTracker` 的**一次性特例**（"只要最后那一个状态、不要历史"）：数据集逐决策
    复用同一手事件时要走 `CarryTracker`（一次重放 + 保存历史），别在这里一次一次重放整条前缀。

    这是**离线端**算"窗口之前的长程 carry"的**唯一**口径（golden 夹具与紧凑集的 `h0` 列
    都走它 / `CarryTracker`）—— 别各写一份：口径（只喂真实事件行、按座位编码、分块不丢事件）
    只要有一处抄错，训练时喂进去的 `h0` 与上线时 Java/C++ 算出来的就不是同一个量，而且**不报错**。

    ⚠ 必须分块喂：`EventStream.step()` 一次只认最后 `K_EVT` 条（`event_matrix` 的窗口）。
    """
    events = list(obs.get("events") or [])[:max(0, int(upto))]
    if not events:
        return None
    st = EventStream(encoder, seat=int(obs.get("seat", 0)))
    for i in range(0, len(events), st.maxlen):
        for e in events[i:i + st.maxlen]:
            st.push(e)
        st.step()
    return st.h


def torch_encoder(model) -> Callable:
    """把 `V4Model` 包成 `EventStream` / `CarryTracker` 认的 encoder。

    约定（`EventStream.step` / `carry_prefix` / `export.build_golden` 三处共用）：
    `encoder(tokens[k, C_EVT], h[1, dm] | None) -> h[1, dm]` —— 可以一次喂多行（批量推进），
    也可以只喂一行（`CarryTracker` 逐事件推进）。batch 维恒为 1：离线端一次只算一条决策流。

    ⚠ 必须走 `model.step_event`（= `EventTower.step`）而不是自己写 `GRUCell` 循环：
    生产端（Java/C++ 的整手重放 / 缓存槽）与导出夹具用的都是这**同一把递推**，
    "训练侧的 `h0` == 生产侧的 carry" 才是构造出来的、而不是"看起来差不多"。
    """
    import torch                    # 惰性导入：cache.py 的纯张量路径（RiverState）不该背 torch 依赖

    def enc(tokens, h):
        t = torch.from_numpy(np.asarray(tokens, dtype=np.float32))[None]
        # ⚠ `no_grad` 是**语义**而不只是省内存：算 carry 永远是推理（`h0` 只是喂进前向的输入
        #   张量，它不该参与反向）—— 而这个网在数据集构建里本来就是"构建期冻结"的。
        #   漏了它，逐事件推进会给每个事件建一张图（一手两百条 ⇒ 显存/内存白涨）。
        with torch.no_grad():
            return model.step_event(t, h)

    return enc


class CarryTracker:
    """离线端推进"整手 carry"并**保存历史** —— 给紧凑集算 `h0`（窗口之前）。

    `h0` = 重放 `events[0, max(0, n-K))` 得到的 carry。为什么保存历史而不是重放两遍：
    一次重放就够（一个事件一步），"窗口之前"只是历史里的一个位置 —— 写两份口径迟早漂移。

    ⚠ 它是**离线端算 `h0` 的唯一实现**（`carry_prefix` 是它的"只取最后一段"特例）：
    事件行怎么编码（`blocks.event_matrix`，按座位、只追加、新事件在尾部）与递推怎么走
    （`EventTower.step`）都由这里统一，`export.build_golden` 与 `dataset` 的 `h0` 列都走它。
    """

    def __init__(self, encoder, seat: int, k: int = K_EVT) -> None:
        self.encoder = encoder
        self.seat = int(seat)
        self.k = int(k)
        self.n = 0                                   #: 已推进的事件数（= `hist` 的计数轴）
        #: 尾部若干条事件（**前缀校验**用；只留 `_OVERLAP_CHECK` 条 ⇒ 内存与手长无关）
        self.tail: deque = deque(maxlen=_OVERLAP_CHECK)
        self.key: Any = None                         #: 上一批事件的"身份"（小局/整场；见 `advance`）
        #: 逐事件编码器：`push` + `step` **一次一个** ⇒ 每个事件之后的 carry 都能进历史
        self._stream = EventStream(encoder, seat=self.seat)
        #: `(事件数, carry)` 对，只留 `[n-k, n]` 这 `k+1` 个 —— `h0(n)` 要的正是 `n-k` 那一个
        self.hist: deque = deque([(0, None)], maxlen=self.k + 1)

    # ---------------------------------------------------------------- 推进
    def advance(self, events: Sequence[Mapping], *, key: Any = None) -> None:
        """把 `events` 推进到最新。

        要求它是"上次那批的**前缀扩展**"，否则**整条丢弃重放**（与 Java `V4Cache.overlapOk`
        同一条纪律：不信"应该是它"，要校验）。

        @param key 这批事件所属**小局**的身份（`dataset` 用 `(game, hand_no)`）。
            ⚠ 为什么除了前缀校验还要它：`obs.events` 是**每小局清零**的（`Round.events`），
            换局后新事件流的头几条与上一局尾部**理论上可能**逐字段相同（同一张牌、同一个演员、
            同样的巡目），前缀校验会漏判 ⇒ 带着上一局的 carry 进新局。给了 `key` 就在
            边界上无条件重放（宁可多一次重放，不可错接状态）；不给就只靠前缀校验。
        """
        ev = list(events or ())
        if key is not None and self.key is not None and key != self.key:
            self.reset()
            self.key = key
        if not self._is_extension(ev):
            self.reset()
            self.key = key
        if key is not None and self.key is None:
            self.key = key
        # ⚠ 逐事件 push + step（不是一次喂一批）：历史要的是**每个事件之后**的 carry，
        #   批量喂只有最后一个能拿到（`EventStream.step` 只返回末态）。
        for e in ev[self.n:]:
            self._stream.push(e)
            self._stream.step()
            self.n += 1
            self.hist.append((self.n, self._stream.h))
            self.tail.append(e)

    def _is_extension(self, ev: Sequence[Mapping]) -> bool:
        """长度不退化 **且** 尾部若干条事件逐条相等（`_OVERLAP_CHECK` 见模块顶部）。"""
        if len(ev) < self.n:
            return False
        m = min(self.n, _OVERLAP_CHECK)
        if m <= 0:
            return True
        old = list(self.tail)[-m:]
        for a, b in zip(ev[self.n - m:self.n], old):
            if not _same_event(a, b):
                return False
        return True

    # ---------------------------------------------------------------- 取用
    def h0(self, n: int):
        """`hist[max(0, n-k)]` → `[dm]` 张量；`None` = **全 0**（窗口之前没有事件）。

        ⚠ 返回的是**去掉 batch 维**的向量：消费方按行写 `h0[i]`，形状必须是 `[dm]`。
        """
        want = max(0, int(n) - self.k)
        for count, h in reversed(self.hist):
            if count == want:
                return None if h is None else h.detach()[0]
        # 不可能走到：`hist` 的 maxlen = k+1 且计数连续 ⇒ 它恒覆盖 `[max(0,n-k), n]`。
        # 真走到了就是"历史被别的口径截断过"（例如 k 中途被改），宁可报错也不返回错的状态。
        raise ContractError(
            f"CarryTracker 历史里没有 count == {want} 的 carry（k={self.k}，已推进 {self.n} 条）"
            f"—— `hist` 覆盖区间与 `h0(n)` 的口径不一致")

    def reset(self) -> None:
        """清零（每小局 / 前缀校验失败 / 调用方显式要求）。"""
        self.n = 0
        self.tail.clear()
        self.key = None
        self._stream.reset()
        self.hist = deque([(0, None)], maxlen=self.k + 1)

    @property
    def h(self):
        """**当前** carry（推进到最新之后的隐状态，`[dm]` 或 `None`）—— 自证用。

        语义自证的右半边就是它：`tracker.h == carry_prefix(encoder, obs, n)[0]`、而
        `tracker.h0(n) == carry_prefix(encoder, obs, max(0, n-k))[0]`（差 ≤1e-6）。
        两个量是同一个递推的两个时刻 —— 只留一个入口，免得有人各写一份。
        """
        h = self.hist[-1][1]
        return None if h is None else h.detach()[0]


def _same_event(a: Mapping, b: Mapping) -> bool:
    """两条事件是否**逐字段相同**（前缀校验用）。

    为什么 `dict(a) == dict(b)` 而不是 `a == b`：事件一般是 JSON 解出来的 `dict`，但调用方
    也可能给任意 `Mapping` —— `dict.__eq__(非 dict)` 返回 `NotImplemented`，落到反射比较时
    可能变成"永不相等"⇒**每次都重放**（不报错、只是慢），甚至在别的实现上变成"永远相等"
    （那就成了**不校验**）。归一成 `dict` 之后两边同一把尺子。
    """
    try:
        return dict(a) == dict(b)
    except (TypeError, ValueError):
        return a is b


def incremental_equals_full(obs: Mapping[str, Any], *, atol: float = 1e-5) -> tuple[bool, float]:
    """判据④（张量级）：把事件流"逐条喂"与"一次性重算"比较。返回 (是否一致, 最大差)。"""
    seat = int(obs.get("seat", 0))
    st = RiverState(seat=seat)
    for e in (obs.get("events") or []):
        st.apply(e)
    ref = RiverState.full_recompute(obs)
    d = float(np.abs(st.channels() - ref.channels()).max()) if obs.get("events") is not None else 0.0
    return d <= atol, d
