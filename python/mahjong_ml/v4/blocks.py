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

# ------------------------------------------------------------------ 每行都要查的常量（缓存）
#
# 这一层是 2026-10-05 的**纯提速**改造：紧凑集构建（`dataset build`）是训练回路里最大的一块
# CPU 开销，而它的热路径是"逐决策行"的——1170 万次 `dict.get` / 一百多万次 `np.asarray`
# 全在这里。三条纪律（守住它们，产物就**逐位不变**）：
#
# ① **只缓存"输入相同 ⇒ 输出相同"的纯函数**（牌码→kind、动作键→基础候选向量、dora 集合→列）；
# ② 缓存的是**值**不是"跳过"：命中时仍然写同样的浮点数（没有近似、没有稀疏化）；
# ③ 计数器/位图一律**整数精确**（float32 里 0..136 的整数加法可交换）⇒ 与逐条累加逐位相同。

#: `f3.tile_kind` 的**带缓存**版本。它一次构建被调用 165 万次（0.72 s），而牌码只有 ~40 种。
_KIND_CACHE: dict[str, int] = {}


def _kind(code: Any) -> int:
    """`f3.tile_kind(str(code or ""))` 的缓存版（返回值与它**逐位相同**，见文件头纪律①）。"""
    if type(code) is str:
        k = _KIND_CACHE.get(code)
        if k is None:
            k = f3.tile_kind(code)
            _KIND_CACHE[code] = k
        return k
    return f3.tile_kind(str(code or ""))


def _kindv(v: Any) -> int:
    """事件字段版：`f3.tile_kind(str(v or ""))`（`str` 的转换口径与原实现逐字一致）。"""
    if type(v) is str:
        k = _KIND_CACHE.get(v)
        if k is None:
            k = f3.tile_kind(v)
            _KIND_CACHE[v] = k
        return k
    return f3.tile_kind(str(v or ""))


def _counts_cached(codes: Sequence[str]) -> np.ndarray:
    """`features._counts` 的**等价**实现：逐码 `+= 1.0`，只是牌码→kind 走 `_kind` 的记忆表。

    ⚠ 为什么"逐条累加"可以原样留着：这里的计数上限是 4（同种牌只有 4 张），float32 里
    0..136 的整数加法**精确可交换** ⇒ 与 `features._counts` 逐位相同（不需要改成 bincount）。
    """
    out = np.zeros(f3.KIND_COUNT, dtype=np.float32)
    for c in codes:
        k = _kind(c)
        if k >= 0:
            out[k] += 1.0
    return out


def _counts(codes: Sequence[str]) -> np.ndarray:
    return _counts_cached(codes) if codes else np.zeros(f3.KIND_COUNT, dtype=np.float32)


#: 静态牌性通道（`is_aka .. suit_s`，7 列，**与行无关**）—— 原来每行用 7 次
#: `np.array([[1.0 if pred(k) ...] for k in range(34)])` 现造（10 万次/次构建）。
_STATIC_FLAGS: np.ndarray = np.array(
    [[1.0 if pred(k) else 0.0 for k in range(f3.KIND_COUNT)] for pred in (
        lambda k: k in _AKA_KINDS,
        lambda k: k >= 27,                      # is_yakuhai
        lambda k: k in (0, 8, 9, 17, 18, 26),   # is_terminal
        lambda k: k >= 27,                      # is_honor
        lambda k: k < 9,                        # suit_m
        lambda k: 9 <= k < 18,                  # suit_p
        lambda k: 18 <= k < 27,                 # suit_s
    )], dtype=np.float32).T                     # [34, 7]

#: `dora_kinds`（一手最多 5 个牌种）→ `[34]` 的 0/1 列。集合内容相同 ⇒ 列相同。
_DORA_FLAG_CACHE: dict[frozenset, np.ndarray] = {}


def _dora_flag(kinds) -> np.ndarray:
    key = frozenset(kinds)
    col = _DORA_FLAG_CACHE.get(key)
    if col is None:
        col = np.zeros(f3.KIND_COUNT, dtype=np.float32)
        for k in key:
            col[k] = 1.0
        _DORA_FLAG_CACHE[key] = col
    return col


#: 合法动作键 → `features.cand_vector(key)` 的**前 88 维基础段**。键只有几百种，
#: 而一次构建要展开 11 万个候选 —— 每个都在 `cand_vector` 里现造一个 96 长零数组 + 字典/列表查找。
#: 缓存的是**纯函数的值**（同一键永远同一向量），命中时照抄同样的浮点数。
_BASE_CAND_CACHE: dict[str, np.ndarray] = {}


def _dora_kinds(indicators: Sequence[str]) -> set[int]:
    """指示牌 → 宝牌牌种（数牌 +1、9→1；风 E→S→W→N→E；三元 白→発→中→白）。"""
    out: set[int] = set()
    for code in indicators or ():
        k = _kind(code)
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
            k = _kind(code)
            if k >= 0:
                out[k] += 1.0
    return out


# ------------------------------------------------------------------ tile
def tile_matrix(obs: Mapping[str, Any], sidecar: Mapping[str, Any] | None,
                *, degraded: set[str] | None = None) -> np.ndarray:
    """`[34, C_TILE]` 逐牌种通道。四家块**旋转到自己为下标 0**。

    ⚠ 2026-10-05 起这里**不再用 `put()` 闭包**：原来每行 25 次 `put(name, col)` ⇒ 每次构建
    32 万次"`np.asarray` + `reshape` + 切片赋值"的调用开销（实测 0.39 s self、0.85 s cum）。
    现在直接按 `TILE_OFF` 的偏移写切片/单列 —— **赋值的目标与值一个字都没变**。
    """
    degrad = degraded if degraded is not None else set()
    m = np.zeros((f3.KIND_COUNT, C_TILE), dtype=np.float32)
    seat = int(obs.get("seat", 0))

    hand = np.asarray(obs.get("hand", np.zeros(f3.KIND_COUNT)), dtype=np.float32)
    hand_red = np.asarray(obs.get("hand_red", np.zeros(f3.KIND_COUNT)), dtype=np.float32)
    drawn_kind = _kindv(obs.get("drawn"))
    m[:, TILE_OFF["own_count"][0]] = hand * 0.25
    m[:, TILE_OFF["own_aka"][0]] = hand_red
    if drawn_kind >= 0:
        m[drawn_kind, TILE_OFF["own_drawn"][0]] = 1.0

    discards = obs.get("discards") or [[], [], [], []]
    per = np.zeros((f3.KIND_COUNT, 18), dtype=np.float32)        # 6 组 × 3 家
    ev3 = int(obs.get("v", 2)) >= spec.OBS_VERSION_V4
    events = obs.get("events") or ()
    # ⚠ 三家各自的"打牌事件"**只筛一遍**（原来是每家都把整条事件流扫一遍 ⇒ 3× 的 `e.get`）。
    #   筛出来的行**逐条相同**（同一批事件按演员分组），后面的计数口径一个字都没变。
    by_actor: dict[int, list] = {}
    if ev3:
        for e in events:
            if e.get("type") == "discard":
                a = e.get("actor")
                if a is not None:
                    by_actor.setdefault(int(a), []).append(e)
    for j, s in enumerate(range(seat, seat + N_PLAYERS)):        # 下标 0 = 自己
        abs_s = s % N_PLAYERS
        if j == 0:
            continue                                            # 自家牌河在 evt 里，tile 不给
        col = j - 1
        river = list(discards[abs_s]) if abs_s < len(discards) else []
        per[:, col] = _counts(river) * 0.25                      # river_all
        if ev3:
            rows = by_actor.get(abs_s)
            if rows:
                # 四组计数**一趟走完**（原来是 4 个列表推导 + 4 次 `_counts`）：计数是整数的
                # float32 累加（≤18），与逐条累加**逐位相同**。
                before_c = np.zeros(f3.KIND_COUNT, dtype=np.float32)
                after_c = np.zeros(f3.KIND_COUNT, dtype=np.float32)
                tsumo_c = np.zeros(f3.KIND_COUNT, dtype=np.float32)
                tedashi_c = np.zeros(f3.KIND_COUNT, dtype=np.float32)
                for e in rows:
                    k = _kindv(e.get("tile"))
                    if k < 0:
                        continue
                    (after_c if e.get("rip_phase") else before_c)[k] += 1.0
                    (tsumo_c if e.get("tsumogiri") else tedashi_c)[k] += 1.0
                per[:, 3 + col] = before_c * 0.25
                per[:, 6 + col] = after_c * 0.25
                per[:, 9 + col] = tedashi_c * 0.25
                per[:, 12 + col] = tsumo_c * 0.25
        else:
            before, after, tsumo, tedashi = river, [], [], []
            degrad.add("tile.per_opp")
            per[:, 3 + col] = _counts(before) * 0.25
        per[:, 15 + col] = _meld_counts(obs.get("melds"), abs_s) * 0.25
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
    # 块 → 通道组（顺序与 TILE_LAYOUT 一致）
    for name, chunk in (("river_all", per[:, 0:3]), ("river_before_riichi", per[:, 3:6]),
                        ("river_after_riichi", per[:, 6:9]), ("river_tedashi", per[:, 9:12]),
                        ("river_tsumogiri", per[:, 12:15]), ("meld_count", per[:, 15:18]),
                        ("safety_genbutsu", safety[:, 0:3]), ("safety_suji", safety[:, 3:6]),
                        ("danger_all", danger[:, 0:3]), ("danger_riichi", danger[:, 3:6])):
        s, w = TILE_OFF[name]
        m[:, s:s + w] = chunk

    visible = np.asarray(obs.get("visible", np.zeros(f3.KIND_COUNT)), dtype=np.float32)
    unseen = np.maximum(0.0, 4.0 - visible)
    drawable = np.maximum(0.0, 4.0 - visible - hand)
    dora_kinds = _dora_kinds(obs.get("dora_indicators") or [])

    m[:, TILE_OFF["visible"][0]] = visible * 0.25
    m[:, TILE_OFF["unseen"][0]] = unseen * 0.25
    m[:, TILE_OFF["drawable"][0]] = drawable * 0.25
    m[:, TILE_OFF["dora_indicator"][0]] = _counts(obs.get("dora_indicators") or []) * 0.25
    m[:, TILE_OFF["is_dora"][0]] = _dora_flag(dora_kinds)
    # 7 列静态牌性一次写完（`is_aka .. suit_s` 在布局里连续）；预留 3 列保持 0
    s_static = TILE_OFF["is_aka"][0]
    m[:, s_static:s_static + _STATIC_FLAGS.shape[1]] = _STATIC_FLAGS
    return m


# ------------------------------------------------------------------ evt
#: `type` / `meld_kind` 的字符串 → 段内下标（原来是 `in` + `.index()` 的线性扫描）
_EVT_TYPE_IDX = {t: i for i, t in enumerate(EVT_TYPES)}
_MELD_KIND_IDX = {k: i for i, k in enumerate(f3.MELD_KINDS)}


def event_matrix(obs: Mapping[str, Any], *, degraded: set[str] | None = None) -> np.ndarray:
    """`[K, C_EVT]`：最近 K 条公开事件，**新的在尾部**（增量缓存按这个顺序追加）。

    ⚠ 这是紧凑集构建里**最热的一段**（一手 ~44 条事件、每条决策都要把窗口 ~24 行重新编码一遍）：
    一次构建 5.3 万次调用 / 31 万个事件行。2026-10-05 的改造只做三件事，且都**不改值**：
    ① `EVT_OFF[...]` 的偏移在循环外取一次（原来是每字段一次字典查找）；
    ② 字符串 → 下标的查找换成 `dict`（`EVT_TYPES.index` / `MELD_KINDS.index` 是线性扫描）；
    ③ 输出行是**零初始化**的，所以 `1.0 if 条件 else 0.0` 里"写 0.0"的那一半直接省掉
       （只写 1.0 的那一半）—— 目标元素本来就恒为 0。
    """
    degrad = degraded if degraded is not None else set()
    out = np.zeros((K_EVT, C_EVT), dtype=np.float32)
    events = obs.get("events")
    if events is None:
        degrad.add("evt.stream")
        return out
    n = len(events)
    if n <= 0:
        return out
    if n > K_EVT:
        n = K_EVT
    seat = int(obs.get("seat", 0))
    ev = events[-n:]
    o_type = EVT_OFF["type"][0]
    o_tile = EVT_OFF["tile_kind"][0]
    o_tile_aka = EVT_OFF["tile_aka"][0]
    o_called = EVT_OFF["called_kind"][0]
    o_called_aka = EVT_OFF["called_aka"][0]
    o_actor = EVT_OFF["actor"][0]
    o_from = EVT_OFF["from"][0]
    o_meld = EVT_OFF["meld_kind"][0]
    o_turn = EVT_OFF["turn"][0]
    o_tsumo = EVT_OFF["tsumogiri"][0]
    o_side = EVT_OFF["sideways"][0]
    o_rip = EVT_OFF["rip_phase"][0]
    o_seq = EVT_OFF["seq_delta"][0]
    type_idx = _EVT_TYPE_IDX
    meld_idx = _MELD_KIND_IDX
    # ⚠ `out[-len(events):]` 的左端会被 numpy 的切片规则**钳制到 0**（L > K 时整块都是窗口），
    #   所以这里等价于 `out[K_EVT-n:]` —— 与原来逐字一致。
    for row, e in zip(out[K_EVT - n:], ev):
        ti = type_idx.get(str(e.get("type", "")))
        if ti is not None:
            row[o_type + ti] = 1.0
        k = _kindv(e.get("tile"))
        if k >= 0:
            row[o_tile + k] = 1.0
        k = _kindv(e.get("called_tile"))
        if k >= 0:
            row[o_called + k] = 1.0
        actor = e.get("actor")
        if actor is not None:
            row[o_actor + (int(actor) - seat) % N_PLAYERS] = 1.0
        src = e.get("from")
        if src is not None:
            row[o_from + (int(src) - seat) % N_PLAYERS] = 1.0
        mk = e.get("meld_kind")
        if type(mk) is str:
            mi = meld_idx.get(mk)
            if mi is not None:
                row[o_meld + mi] = 1.0
        if str(e.get("tile", "")).startswith("0"):
            row[o_tile_aka] = 1.0
        if str(e.get("called_tile", "")).startswith("0"):
            row[o_called_aka] = 1.0
        row[o_turn] = float(e.get("turn", 0)) / 18.0
        if e.get("tsumogiri"):
            row[o_tsumo] = 1.0
        if e.get("sideways"):
            row[o_side] = 1.0
        if e.get("rip_phase"):
            row[o_rip] = 1.0
        row[o_seq] = min(float(e.get("seq_delta", 1)), 8.0) / 8.0
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
    # ⚠ `DERIVED_SCALE_CAND` 原来在**每个候选**里现造一次 `np.asarray`（11 万次）：它是常量，
    #   提到循环外 —— 逐元素除法与被除数/除数一个字都没变。
    scale = np.asarray(f3.DERIVED_SCALE_CAND, dtype=np.float32)
    nscale = scale.size
    cache = _BASE_CAND_CACHE
    for i, key in enumerate(legal):
        base = cache.get(key) if type(key) is str else None
        if base is None:
            base = f3.cand_vector(key)
            if type(key) is str:
                cache[key] = base
        out[i, :base_dim] = base
        if per_cand is not None and i < len(per_cand):
            d = np.asarray(per_cand[i], dtype=np.float32)[:f3.DERIVED_CANDIDATE]
            ds = d.size
            out[i, base_dim:base_dim + ds] = d / scale[:ds] if ds < nscale else d / scale
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
