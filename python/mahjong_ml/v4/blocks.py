"""v4 的张量拼装（规范 §4）：`obs`(+sidecar) → `tile / evt / ctx / cand`。

⚠ 这里的每一行都对应 `docs/FEATURES-V4.md` §4 的一张表。改这里 = 改规范，**两边一起改**。

- `assemble()` 是**全量重算**（基准路径，任何时候都能重建；规范 §9 判据④的右半边）；
- `spec.BLOCKS` 决定"哪些通道属于哪个块"，消融就是把这些通道置 0（规范 §7）；
- obs v2 老数据只能走 `allow_degraded=True`：缺的块（事件流 / 立直巡数 / 逐张属性 / v4 派生量）
  按 0 填，**但块 id 会记进 `Tensors.degraded`**，绝不当成"正常输入"。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np

from .. import features as f3
from . import spec
from .spec import (C_CAND, C_CTX, C_EVT, C_TILE, CAND_DERIVED, CAND_RESERVED, ContractError,
                   EVT_LAYOUT, EVT_TYPES, K_EVT, TILE_LAYOUT, CTX_LAYOUT)

N_PLAYERS = 4
#: 场风字母 → one-hot（轨迹里是字母，**不是数字**）
_WINDS = ("E", "S", "W", "N")
#: 赤五对应的牌种（与 features._AKA_KIND 同源）
_AKA_KINDS = tuple(f3._AKA_KIND.values())

TILE_OFF = {name: (sum(w for _, w in TILE_LAYOUT[:i]), width)
            for i, (name, width) in enumerate(TILE_LAYOUT)}
EVT_OFF = {name: (sum(w for _, w in EVT_LAYOUT[:i]), width)
           for i, (name, width) in enumerate(EVT_LAYOUT)}
CTX_OFF = {name: (sum(w for _, w in CTX_LAYOUT[:i]), width)
           for i, (name, width) in enumerate(CTX_LAYOUT)}

_AKA_CODE_KIND = {"0m": 4, "0p": 13, "0s": 22}


def _counts(codes: Sequence[str]) -> np.ndarray:
    return f3._counts(codes) if codes else np.zeros(f3.KIND_COUNT, dtype=np.float32)


def _dora_kinds(indicators: Sequence[str]) -> set[int]:
    """指示牌 → 宝牌牌种（数牌 +1、9→1；风 E→S→W→N→E；三元 白→発→中→白）。"""
    out: set[int] = set()
    for code in indicators or ():
        k = f3.tile_kind(code)
        if k < 0:
            continue
        if k < 27:                      # 数牌
            base = (k // 9) * 9
            out.add(base + (k - base + 1) % 9)
        elif k < 31:                    # 风
            out.add(27 + (k - 27 + 1) % 4)
        else:                           # 三元
            out.add(31 + (k - 31 + 1) % 3)
    return out


def _meld_counts(melds: Any, seat_index: int) -> np.ndarray:
    """某家副露的**牌种计数**（`melds` 按绝对座位索引）。"""
    out = np.zeros(f3.KIND_COUNT, dtype=np.float32)
    if not melds or seat_index >= len(melds) or not melds[seat_index]:
        return out
    for m in melds[seat_index] or ():
        for code in (m or {}).get("tiles", ()) or ():
            k = f3.tile_kind(code)
            if k >= 0:
                out[k] += 1.0
    return out


# ------------------------------------------------------------------ tile
def tile_matrix(obs: Mapping[str, Any], sidecar: Mapping[str, Any] | None,
                *, degraded: set[str] | None = None) -> np.ndarray:
    """`[34, C_TILE]` 逐牌种通道。四家块**旋转到自己为下标 0**。"""
    degrad = degraded if degraded is not None else set()
    m = np.zeros((f3.KIND_COUNT, C_TILE), dtype=np.float32)
    seat = int(obs.get("seat", 0))

    def put(name: str, col: np.ndarray) -> None:
        s, w = TILE_OFF[name]
        col = np.asarray(col, dtype=np.float32).reshape(f3.KIND_COUNT, w)
        m[:, s:s + w] = col

    hand = np.asarray(obs.get("hand", np.zeros(f3.KIND_COUNT)), dtype=np.float32)
    hand_red = np.asarray(obs.get("hand_red", np.zeros(f3.KIND_COUNT)), dtype=np.float32)
    drawn_kind = f3.tile_kind(obs.get("drawn") or "")
    own_drawn = np.zeros(f3.KIND_COUNT, dtype=np.float32)
    if drawn_kind >= 0:
        own_drawn[drawn_kind] = 1.0
    put("own_count", (hand / 4.0).reshape(-1, 1))
    put("own_aka", hand_red.reshape(-1, 1))
    put("own_drawn", own_drawn.reshape(-1, 1))

    discards = obs.get("discards") or [[], [], [], []]
    per = np.zeros((f3.KIND_COUNT, 18), dtype=np.float32)        # 6 组 × 3 家
    ev3 = int(obs.get("v", 2)) >= spec.OBS_VERSION_V4
    for j, s in enumerate(range(seat, seat + N_PLAYERS)):        # 下标 0 = 自己
        abs_s = s % N_PLAYERS
        if j == 0:
            continue                                            # 自家牌河在 evt 里，tile 不给
        col = j - 1
        river = list(discards[abs_s]) if abs_s < len(discards) else []
        per[:, col] = _counts(river) / 4.0                       # river_all
        if ev3:
            rows = [e for e in (obs.get("events") or []) if int(e.get("actor", -1)) == abs_s
                    and e.get("type") == "discard"]
            before = [e.get("tile") for e in rows if not e.get("rip_phase")]
            after = [e.get("tile") for e in rows if e.get("rip_phase")]
            tsumo = [e.get("tile") for e in rows if e.get("tsumogiri")]
            tedashi = [e.get("tile") for e in rows if not e.get("tsumogiri")]
        else:
            before, after, tsumo, tedashi = river, [], [], []
            degrad.add("tile.per_opp")
        per[:, 3 + col] = _counts(before) / 4.0
        per[:, 6 + col] = _counts(after) / 4.0
        per[:, 9 + col] = _counts(tedashi) / 4.0
        per[:, 12 + col] = _counts(tsumo) / 4.0
        per[:, 15 + col] = _meld_counts(obs.get("melds"), abs_s) / 4.0
    # 三家的 safety / danger = **v4 派生量**（sidecar v3）；缺了就 0 + 记降级（不猜）
    safety = np.zeros((f3.KIND_COUNT, 6), dtype=np.float32)
    danger = np.zeros((f3.KIND_COUNT, 6), dtype=np.float32)
    sc = sidecar if (sidecar and sidecar.get("derived_version") == spec.DERIVED_VERSION_V4) else None
    if sc is None:
        degrad.add("tile.safety")
        degrad.add("tile.danger")
    else:
        # ⚠ 逐家段是**数组**（sidecar 由 `dataset.load_sidecar` 以 numpy 视图给出），
        #   所以这里不能写 `sc.get(key) or []` —— numpy 数组的真值判断会直接抛
        #   "truth value of an array is ambiguous"（真实 sidecar 第一次接进来就在这里炸）。
        def _seat_row(key: str, col: int):
            rows = sc.get(key)
            if rows is None or len(rows) <= col:
                return None
            return rows[col]

        for col in range(3):
            for key, base in (("genbutsu_per_seat", 0), ("suji_per_seat", 3)):
                row = _seat_row(key, col)
                if row is None:
                    degrad.add("tile.safety")
                else:
                    safety[:, base + col] = np.asarray(row, dtype=np.float32)[:f3.KIND_COUNT]
            for key, base in (("danger_per_seat", 0), ("danger_riichi_per_seat", 3)):
                row = _seat_row(key, col)
                if row is None:
                    degrad.add("tile.danger")
                else:
                    danger[:, base + col] = (np.asarray(row, dtype=np.float32)[:f3.KIND_COUNT]
                                             / 100.0)
    # 块 → 通道组（顺序与 TILE_LAYOUT 一致）；⚠ `put` 收的是**通道名**，不是块 id
    for name, chunk in (("river_all", per[:, 0:3]), ("river_before_riichi", per[:, 3:6]),
                        ("river_after_riichi", per[:, 6:9]), ("river_tedashi", per[:, 9:12]),
                        ("river_tsumogiri", per[:, 12:15]), ("meld_count", per[:, 15:18]),
                        ("safety_genbutsu", safety[:, 0:3]), ("safety_suji", safety[:, 3:6]),
                        ("danger_all", danger[:, 0:3]), ("danger_riichi", danger[:, 3:6])):
        put(name, chunk)

    visible = np.asarray(obs.get("visible", np.zeros(f3.KIND_COUNT)), dtype=np.float32)
    unseen = np.maximum(0.0, 4.0 - visible)
    drawable = np.maximum(0.0, 4.0 - visible - hand)
    dora_kinds = _dora_kinds(obs.get("dora_indicators") or [])

    def flag(pred) -> np.ndarray:
        return np.array([[1.0 if pred(k) else 0.0] for k in range(f3.KIND_COUNT)], dtype=np.float32)

    put("visible", (visible / 4.0).reshape(-1, 1))
    put("unseen", (unseen / 4.0).reshape(-1, 1))
    put("drawable", (drawable / 4.0).reshape(-1, 1))
    put("dora_indicator", (_counts(obs.get("dora_indicators") or []) / 4.0).reshape(-1, 1))
    put("is_dora", flag(lambda k: k in dora_kinds))
    put("is_aka", flag(lambda k: k in _AKA_KINDS))
    put("is_yakuhai", flag(lambda k: k >= 27))
    put("is_terminal", flag(lambda k: k in (0, 8, 9, 17, 18, 26)))
    put("is_honor", flag(lambda k: k >= 27))
    put("suit_m", flag(lambda k: k < 9))
    put("suit_p", flag(lambda k: 9 <= k < 18))
    put("suit_s", flag(lambda k: 18 <= k < 27))
    # 预留 3 列保持 0
    return m


# ------------------------------------------------------------------ evt
def event_matrix(obs: Mapping[str, Any], *, degraded: set[str] | None = None) -> np.ndarray:
    """`[K, C_EVT]`：最近 K 条公开事件，**新的在尾部**（增量缓存按这个顺序追加）。"""
    degrad = degraded if degraded is not None else set()
    out = np.zeros((K_EVT, C_EVT), dtype=np.float32)
    events = obs.get("events")
    if events is None:
        degrad.add("evt.stream")
        return out
    seat = int(obs.get("seat", 0))
    for row, e in zip(out[-len(events):], events[-K_EVT:]):
        t = str(e.get("type", ""))
        if t in EVT_TYPES:
            row[EVT_OFF["type"][0] + EVT_TYPES.index(t)] = 1.0
        for field_name, key, off in (("tile_kind", "tile", 0), ("called_kind", "called_tile", 0)):
            k = f3.tile_kind(str(e.get(key) or ""))
            if k >= 0:
                row[EVT_OFF[field_name][0] + k] = 1.0
        actor = e.get("actor")
        if actor is not None:
            row[EVT_OFF["actor"][0] + (int(actor) - seat) % N_PLAYERS] = 1.0
        src = e.get("from")
        if src is not None:
            row[EVT_OFF["from"][0] + (int(src) - seat) % N_PLAYERS] = 1.0
        mk = e.get("meld_kind")
        if mk in f3.MELD_KINDS:
            row[EVT_OFF["meld_kind"][0] + f3.MELD_KINDS.index(mk)] = 1.0
        row[EVT_OFF["tile_aka"][0]] = 1.0 if str(e.get("tile", "")).startswith("0") else 0.0
        row[EVT_OFF["called_aka"][0]] = 1.0 if str(e.get("called_tile", "")).startswith("0") else 0.0
        row[EVT_OFF["turn"][0]] = float(e.get("turn", 0)) / 18.0
        row[EVT_OFF["tsumogiri"][0]] = 1.0 if e.get("tsumogiri") else 0.0
        row[EVT_OFF["sideways"][0]] = 1.0 if e.get("sideways") else 0.0
        row[EVT_OFF["rip_phase"][0]] = 1.0 if e.get("rip_phase") else 0.0
        row[EVT_OFF["seq_delta"][0]] = min(float(e.get("seq_delta", 1)), 8.0) / 8.0
    return out


# ------------------------------------------------------------------ ctx
def ctx_vector(obs: Mapping[str, Any], *, degraded: set[str] | None = None) -> np.ndarray:
    degrad = degraded if degraded is not None else set()
    v = np.zeros(C_CTX, dtype=np.float32)

    def put(name: str, arr: Sequence[float]) -> None:
        s, w = CTX_OFF[name]
        a = np.asarray(arr, dtype=np.float32)
        assert a.size == w, f"ctx.{name} 给了 {a.size} 维，规范是 {w}"
        v[s:s + w] = a

    seat = int(obs.get("seat", 0))
    rnd = obs.get("round") or {}
    bakaze = str(rnd.get("bakaze", "E")).upper()
    put("round", [
        *[1.0 if bakaze == w else 0.0 for w in _WINDS],
        float(rnd.get("kyoku", 1)) / 4.0,
        float(rnd.get("honba", 0)) / 10.0,
        float(rnd.get("riichi_sticks", 0)) / 4.0,
        float((int(rnd.get("dealer", 0)) - seat) % N_PLAYERS) / 3.0,
    ])
    scores = np.asarray(obs.get("scores", [25000] * 4), dtype=np.float32)
    rel = np.array([scores[(seat + j) % N_PLAYERS] for j in range(N_PLAYERS)], dtype=np.float32)
    self_score = float(rel[0])
    others = rel[1:]
    rank = 1 + int((others > self_score).sum())
    ups = [x - self_score for x in others if x > self_score]
    downs = [self_score - x for x in others if x < self_score]
    put("points", [
        *[(x - 25000.0) / 1000.0 for x in rel],
        *[1.0 if rank == r else 0.0 for r in range(1, N_PLAYERS + 1)],
        (self_score - float(others.mean())) / 1000.0,
        (self_score - 25000.0) / 1000.0,
        (min(ups) if ups else 0.0) / 1000.0,
        (min(downs) if downs else 0.0) / 1000.0,
    ])
    put("wall", [
        float(obs.get("tiles_left", 0)) / 70.0,
        float(obs.get("dead_wall_left", 0)) / 4.0,
        float(obs.get("total_discards", 0)) / 72.0,
        float(obs.get("kan_count", 0)) / 4.0,
        1.0 if obs.get("any_call") else 0.0,
        1.0 if obs.get("haitei") else 0.0,
        1.0 if obs.get("houtei") else 0.0,
        1.0 if obs.get("rinshan") else 0.0,
    ])
    melds = obs.get("melds") or []
    own_melds = len(melds[seat]) if seat < len(melds) and melds[seat] else 0
    own_river = (obs.get("discards") or [[]])[seat] if seat < len(obs.get("discards") or []) else []
    ev3 = int(obs.get("v", 2)) >= spec.OBS_VERSION_V4
    riichi_turn = obs.get("riichi_turn") or [0] * N_PLAYERS
    if not ev3:
        degrad.add("ctx.seats")
    put("self", [
        float(obs.get("player_draws", 0)) / 18.0,
        1.0 if obs.get("menzen") else 0.0,
        1.0 if obs.get("self_riichi") else 0.0,
        1.0 if obs.get("furiten") else 0.0,
        float(own_melds) / 4.0,
        1.0 if str(obs.get("drawn") or "").startswith("0") else 0.0,
        float(riichi_turn[seat] if seat < len(riichi_turn) else 0) / 18.0,
        0.0,                                     # 自家舍牌摸切率（obs v3 的逐张属性）
        float(len(own_river)) / 18.0,
        0.0,
    ])
    put("seats", [
        *[1.0 if obs.get("riichi", [0] * 4)[(seat + j) % N_PLAYERS] else 0.0 for j in range(N_PLAYERS)],
        *[float(riichi_turn[(seat + j) % N_PLAYERS] if (seat + j) % N_PLAYERS < len(riichi_turn) else 0) / 18.0
          for j in range(N_PLAYERS)],
        *[1.0 if obs.get("ippatsu", [0] * 4)[(seat + j) % N_PLAYERS] else 0.0 for j in range(N_PLAYERS)],
    ])
    fr = [0.0] * N_PLAYERS
    if obs.get("kind") == "claim" and obs.get("from") is not None:
        fr[(int(obs["from"]) - seat) % N_PLAYERS] = 1.0
    wn = obs.get("win_note")
    put("ask", [
        1.0 if obs.get("kind") == "turn" else 0.0,
        1.0 if str(obs.get("called_tile") or "").startswith("0") else 0.0,
        *fr,
        *[1.0 if wn == w else 0.0 for w in f3.WIN_NOTES],
    ])
    return v


# ------------------------------------------------------------------ cand
def cand_matrix(obs: Mapping[str, Any], sidecar: Mapping[str, Any] | None,
                *, degraded: set[str] | None = None) -> tuple[np.ndarray, list[str]]:
    """`[n, C_CAND]` = 88 基础 + 11 派生 + 29 预留；顺序**与 `legal` 一致**。"""
    degrad = degraded if degraded is not None else set()
    legal = list(obs.get("legal") or [])
    out = np.zeros((len(legal), C_CAND), dtype=np.float32)
    if not legal:
        return out, legal
    base_dim = f3._base_cand_dim()
    if base_dim != 88:
        raise ContractError(f"features._base_cand_dim() = {base_dim} != 88（v3 的候选基础段变了？）")
    per_cand = sidecar.get("cand") if sidecar else None      # [n, 8] 逐候选派生（v3 已有）
    for i, key in enumerate(legal):
        out[i, :base_dim] = f3.cand_vector(key)
        if per_cand is not None and i < len(per_cand):
            d = np.asarray(per_cand[i], dtype=np.float32)[:f3.DERIVED_CANDIDATE]
            out[i, base_dim:base_dim + d.size] = d / np.asarray(f3.DERIVED_SCALE_CAND[:d.size],
                                                               dtype=np.float32)
        else:
            degrad.add("cand.derived")
        # v4 新增 3 维（打点期望 / 打后危险度两种口径）在 sidecar v3 里；这里先留 0
        if not (sidecar and sidecar.get("derived_version") == spec.DERIVED_VERSION_V4):
            degrad.add("cand.derived")
    return out, legal


# ------------------------------------------------------------------ 总装
def assemble(obs: Mapping[str, Any], sidecar: Mapping[str, Any] | None = None, *,
             ablate: Sequence[str] | Iterable[str] = (), allow_degraded: bool = False) -> spec.Tensors:
    """把一次决策拼成四个张量。`ablate` 里的块**置 0**（规范 §7 的消融开关）。"""
    ab = set(ablate)
    for bid in ab:
        spec.block(bid)                       # 未知 id 立刻报错，别静默忽略
    degraded: set[str] = set()
    obs_version = spec.check_versions(obs, allow_degraded=allow_degraded)
    for b in spec.BLOCKS:
        if b.needs_obs_version > obs_version:
            if not allow_degraded:
                raise ContractError(f"{b.bid} 需要 obs v{b.needs_obs_version}，当前 v{obs_version}")
            degraded.add(b.bid)
            continue
        spec._need(obs, b, allow_degraded=allow_degraded, degraded=degraded)

    tile = tile_matrix(obs, sidecar, degraded=degraded)
    evt = event_matrix(obs, degraded=degraded)
    ctx = ctx_vector(obs, degraded=degraded)
    cand, legal = cand_matrix(obs, sidecar, degraded=degraded)

    # 消融：按块把通道清零（tile 的 per_opp/safety/danger 是 tile 内的连续段）
    tile_groups = {"tile.own": ("own_count", "own_aka", "own_drawn"),
                   "tile.per_opp": ("river_all", "river_before_riichi", "river_after_riichi",
                                    "river_tedashi", "river_tsumogiri", "meld_count"),
                   "tile.safety": ("safety_genbutsu", "safety_suji"),
                   "tile.danger": ("danger_all", "danger_riichi"),
                   "tile.global": ("visible", "unseen", "drawable", "dora_indicator", "is_dora",
                                   "is_aka", "is_yakuhai", "is_terminal", "is_honor",
                                   "suit_m", "suit_p", "suit_s", "reserved")}
    for bid in ab:
        b = spec.block(bid)
        if b.tensor == "tile":
            for name in tile_groups.get(bid, ()):
                s, w = TILE_OFF[name]
                tile[:, s:s + w] = 0.0
        elif b.tensor == "evt":
            evt[:] = 0.0
        elif b.tensor == "ctx":
            for name, (s, w) in CTX_OFF.items():
                if name in bid:               # "ctx.points" → 通道名 points
                    ctx[s:s + w] = 0.0
        elif b.tensor == "cand":
            if bid == "cand.base":
                cand[:, :88] = 0.0
            elif bid == "cand.derived":
                cand[:, 88:88 + CAND_DERIVED] = 0.0
            else:
                cand[:, 88 + CAND_DERIVED:] = 0.0
    return spec.Tensors(tile=tile, evt=evt, ctx=ctx, cand=cand, legal=legal,
                        degraded=degraded, ablated=ab, obs_version=obs_version)
