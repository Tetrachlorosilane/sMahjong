"""观测 → 特征：**唯一规格来源**（Java 侧照它对齐；派生量由 Java 算、这里只读）。

设计约束（`docs/TRAINING.md` §3.2 / §3.4）：
  · **只用 `obs` 里合法可见的字段**（白名单见 `PROTOCOL.md` §8.2），不碰任何隐藏信息；
  · **纯函数 + 定长 + 可复现**：同一个 `obs` 永远得到同一个向量，维度由 `state_dim()` 给出；
  · **可移植**：只用 numpy 的切片/加法；`describe()` 打印每段的偏移与宽度；
  · **特征版本号**：字段/顺序一变就 +1（数据集靠它判兼容），与 Java 侧必须同号。

**v2 起接上派生特征**：向听 / 进张 / 听牌形 / 逐张危险度由 Java 的权威实现
（`mahjong.ai.ObsFeatures` → `HandEval` / `Danger`）算好，存成轨迹的二进制 sidecar
（`g*.feat.bin`，格式见 `mahjong_ml/dataset.py`），这里**只读不重算** ——
在 Python 里再写一份向听/Danger 就是两套实现、必然漂移（`docs/TRAINING.md` §3.4）。

容量：`state_dim() == 615`、`cand_dim() == 96`（自检里钉着，改了必须同步 Java 与两个自检）。
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np

FEATURE_VERSION = 3                # v3：四家块旋转到自己为 0 + points/5 + 派生段加 3 维牌力/打点

KIND_COUNT = 34          # 34 种牌（0..8=1m..9m，9..17=1p..9p，18..26=1s..9s，27..33=1z..7z）
TILE_SLOTS = 37          # 34 种 + 赤 5m/5p/5s（与 Java 的 `Action.tileIndex` 逐字对齐）
N_PLAYERS = 4

#: 派生特征的段长（**与 Java `mahjong.ai.ObsFeatures` 必须一致**；改了两边一起改 + 版本 +1）
DERIVED_VERSION = 2      # = Java `ObsFeatures.FEATURE_VERSION`（sidecar 头部也带它）
DERIVED_DECISION = 71    # danger_worst[34] + danger_riichi[34] + shanten_now, value_han, value_points
DERIVED_CANDIDATE = 8    # shanten, advance_types, advance_tiles, wait_types, wait_tiles,
                         # good_wait_types, good_wait_tiles, dora_count

#: 派生量的**归一化**（Java 侧推理时也必须用同一组系数；单点定义，别散在各处）
DERIVED_SCALE_DANGER = 100.0
#: 逐决策段**逐维**分母（v3 起这一段不再同量纲 —— 危险度 0..100，向听/打点各有各的轴）
DERIVED_DECISION_SCALE = ((DERIVED_SCALE_DANGER,) * (2 * KIND_COUNT)
                          + (8.0, 13.0, 32000.0))
DERIVED_SCALE_CAND = (8.0, 34.0, 136.0, 34.0, 136.0, 34.0, 136.0, 4.0)

ACTION_TYPES = ("discard", "riichi", "pon", "chi", "kan", "tsumo", "ron", "pass", "kyuushu")
KAN_KINDS = ("ankan", "kakan", "daiminkan")
MELD_KINDS = ("chi", "pon", "ankan", "kakan", "daiminkan")
WIN_NOTES = ("furiten", "no_yaku")

_SUIT = {"m": 0, "p": 9, "s": 18}
_AKA_KIND = {"m": 4, "p": 13, "s": 22}         # 赤五对应的 kind（0m→5m …）
#: 场风：轨迹里是**字母**（`TraceRecorder.WINDS = {"E","S","W","N"}` —— 实测，别当数字用）
_WINDS = {"E": 0, "S": 1, "W": 2, "N": 3}


def wind_index(v) -> float:
    """场风 → 0..3；容忍字母（真实轨迹）与数字（自造/未来格式）两种写法。"""
    if isinstance(v, str):
        return float(_WINDS.get(v.strip().upper(), 0))
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _num(v, default: float = 0.0) -> float:
    """宽容取数（缺字段/类型意外都不该让整条特征炸掉，但也别指望它兜住逻辑错误）。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------ 牌码

def tile_kind(code: str) -> int:
    """牌码 → kind（0..33）；`0m/0p/0s` 归到 5 的 kind。认不出返回 -1。"""
    if not code or len(code) != 2:
        return -1
    num, suit = code[0], code[1]
    if suit == "z":
        return 27 + int(num) - 1 if num.isdigit() and 1 <= int(num) <= 7 else -1
    if suit not in _SUIT or not num.isdigit():
        return -1
    if num == "0":                             # 赤五
        return _AKA_KIND[suit]
    n = int(num)
    return _SUIT[suit] + n - 1 if 1 <= n <= 9 else -1


def tile_slot(code: str) -> int:
    """牌码 → 槽位（0..36，赤五占 34/35/36）—— **与 Java `Action.tileIndex` 逐字对齐**。"""
    k = tile_kind(code)
    if k < 0:
        return -1
    if code[0] == "0":
        return 34 + _SUIT[code[1]] // 9
    return k


def is_red(code: str) -> bool:
    return bool(code) and code[0] == "0"


# ------------------------------------------------------------------ 状态特征

def state_dim() -> int:
    """状态向量维度（固定；改了要同步 Java 与自检）。末尾 73 维是**派生量**（危险度 68 + 牌力/打点 5）。"""
    return _base_state_dim() + DERIVED_DECISION


def _base_state_dim() -> int:
    return (KIND_COUNT          # 自家暗牌计数
            + KIND_COUNT        # 自家暗牌里的赤五标记
            + N_PLAYERS * (len(MELD_KINDS) + KIND_COUNT)   # 四家副露：种类 + 牌种计数
            + N_PLAYERS * KIND_COUNT                        # 四家牌河（牌种计数）
            + KIND_COUNT        # 宝牌指示牌
            + N_PLAYERS         # 立直
            + N_PLAYERS         # 一发
            + N_PLAYERS         # 点数（相对 25000，/1000；自己在下标 0）
            + 5                 # **位置与点数**：自己点数/与三家均值差/顺位/与上一名差/与下一名差
            + 5                 # 场风/自风/局/本场/供託
            + 5                 # 余牌/岭上/巡目/杠数/有无鸣牌
            + 4                 # 自己第几次摸牌/门清/自家立直/自家振听
            + 4                 # kind（turn/claim）+ 海底/河底/岭上
            + KIND_COUNT        # visible（派生量，服务端已给）
            + TILE_SLOTS        # 刚摸到的那张（自家回合）
            + TILE_SLOTS        # 被鸣/被荣那张（鸣牌询问）
            + N_PLAYERS         # 谁打的（相对座位 one-hot）
            + len(WIN_NOTES) + 1)   # win_note（furiten/no_yaku/无）


def _counts(codes: Iterable[str]) -> np.ndarray:
    out = np.zeros(KIND_COUNT, dtype=np.float32)
    for c in codes:
        k = tile_kind(c)
        if k >= 0:
            out[k] += 1.0
    return out


def _slot_onehot(code) -> np.ndarray:
    out = np.zeros(TILE_SLOTS, dtype=np.float32)
    if isinstance(code, str):
        s = tile_slot(code)
        if s >= 0:
            out[s] = 1.0
    return out


def state_vector(obs: dict, derived: Sequence[int] | None = None) -> np.ndarray:
    """`obs`（`PROTOCOL.md` §8.2 的字段）→ 定长 float32 向量。

    @param derived 该决策的 68 个派生量（Java `ObsFeatures.perDecision`；来自 sidecar）。
                   传 `None` 就填 0 —— 那只在"没富化过的轨迹"上出现，训练时应当有值。

    ⚠ 只用合法可见字段；**不要**在这里读任何"进程内才拿得到"的东西（那正是要防的作弊面）。
    """
    f = np.zeros(state_dim(), dtype=np.float32)
    i = 0

    def put(v: np.ndarray) -> None:
        nonlocal i
        f[i:i + v.size] = v
        i += v.size

    seat = int(obs.get("seat", 0))

    def rel(s: int) -> int:
        """绝对座位 → **相对下标**（0=自己 / 1=下家 / 2=对家 / 3=上家）。⚠ v3 起四家块统一这一套。"""
        return (int(s) - seat) % N_PLAYERS

    # 自家暗牌（计数 ×0.25 归一化；赤五另给标记）
    hand = np.asarray(obs.get("hand", []), dtype=np.float32)
    put(hand * 0.25)
    put(np.asarray(obs.get("hand_red", []), dtype=np.float32))
    # 四家副露：种类计数 + 牌种计数（按相对下标摆放）
    meld_kind = np.zeros(N_PLAYERS * len(MELD_KINDS), dtype=np.float32)
    meld_tiles = np.zeros(N_PLAYERS * KIND_COUNT, dtype=np.float32)
    melds = obs.get("melds") or []
    for s in range(N_PLAYERS):
        j = rel(s)
        for m in (melds[s] if s < len(melds) else []) or []:
            k = MELD_KINDS.index(m.get("kind")) if m.get("kind") in MELD_KINDS else -1
            if k >= 0:
                meld_kind[j * len(MELD_KINDS) + k] += 1.0
            for code in m.get("tiles", []) or []:
                kk = tile_kind(code)
                if kk >= 0:
                    meld_tiles[j * KIND_COUNT + kk] += 1.0
    put(meld_kind)
    put(meld_tiles * 0.25)
    # 四家牌河
    rivers = np.zeros(N_PLAYERS * KIND_COUNT, dtype=np.float32)
    discards = obs.get("discards") or []
    for s in range(N_PLAYERS):
        if s < len(discards):
            rivers[rel(s) * KIND_COUNT:(rel(s) + 1) * KIND_COUNT] = _counts(discards[s] or [])
    put(rivers * 0.25)
    put(_counts(obs.get("dora_indicators") or []) * 0.25)
    put(np.asarray([obs.get("riichi", [0] * 4)[(seat + j) % N_PLAYERS] for j in range(N_PLAYERS)],
                   dtype=np.float32))
    put(np.asarray([obs.get("ippatsu", [0] * 4)[(seat + j) % N_PLAYERS] for j in range(N_PLAYERS)],
                   dtype=np.float32))
    scores = np.asarray(obs.get("scores", [25000] * 4), dtype=np.float32)
    put((scores[[(seat + j) % N_PLAYERS for j in range(N_PLAYERS)]] - 25000.0) / 1000.0)
    # 位置与点数（v3 新增）：自己那一格 + 顺位/分差（四家点数的非线性组合，网络推不出来）
    self_score = float(scores[seat]) if seat < scores.size else 25000.0
    others = [float(scores[(seat + j) % N_PLAYERS]) for j in range(1, N_PLAYERS)]
    rank = 1 + sum(1 for x in others if x > self_score)      # 同点不算比自己高：并列取最好名次
    ups = [x - self_score for x in others if x > self_score]
    downs = [self_score - x for x in others if x < self_score]
    put(np.array([(self_score - 25000.0) / 1000.0,
                  (self_score - sum(others) / (N_PLAYERS - 1)) / 1000.0,
                  (rank - 1) / 3.0,
                  (min(ups) if ups else 0.0) / 1000.0,
                  (min(downs) if downs else 0.0) / 1000.0], dtype=np.float32))
    rnd = obs.get("round") or {}
    put(np.array([wind_index(rnd.get("bakaze", "E")) / 4.0,
                  _num(rnd.get("kyoku", 1)) / 4.0,
                  _num(rnd.get("honba", 0)) / 10.0,
                  float((int(_num(rnd.get("dealer", 0))) - seat) % N_PLAYERS) / 3.0,
                  _num(rnd.get("riichi_sticks", 0)) / 4.0], dtype=np.float32))
    put(np.array([float(obs.get("tiles_left", 0)) / 70.0,
                  float(obs.get("dead_wall_left", 0)) / 4.0,
                  float(obs.get("total_discards", 0)) / 72.0,
                  float(obs.get("kan_count", 0)) / 4.0,
                  1.0 if obs.get("any_call") else 0.0], dtype=np.float32))
    put(np.array([float(obs.get("player_draws", 0)) / 18.0,
                  1.0 if obs.get("menzen") else 0.0,
                  1.0 if obs.get("self_riichi") else 0.0,
                  1.0 if obs.get("furiten") else 0.0], dtype=np.float32))
    put(np.array([1.0 if obs.get("kind") == "turn" else 0.0,
                  1.0 if obs.get("haitei") else 0.0,
                  1.0 if obs.get("houtei") else 0.0,
                  1.0 if obs.get("rinshan") else 0.0], dtype=np.float32))
    visible = obs.get("visible")
    put(np.asarray(visible, dtype=np.float32) * 0.25 if visible else np.zeros(KIND_COUNT, np.float32))
    put(_slot_onehot(obs.get("drawn")))
    put(_slot_onehot(obs.get("called_tile")))
    fr = np.zeros(N_PLAYERS, dtype=np.float32)
    if obs.get("kind") == "claim" and obs.get("from") is not None:
        fr[(int(obs["from"]) - seat) % N_PLAYERS] = 1.0
    put(fr)
    note = np.zeros(len(WIN_NOTES) + 1, dtype=np.float32)
    wn = obs.get("win_note")
    note[WIN_NOTES.index(wn) if wn in WIN_NOTES else len(WIN_NOTES)] = 1.0
    put(note)
    # 派生危险度 + 牌力/打点（**由 Java 算好**；缺了填 0）
    if derived is not None and len(derived) >= DERIVED_DECISION:
        put(np.asarray(derived[:DERIVED_DECISION], dtype=np.float32) / DERIVED_DECISION_SCALE)
    else:
        put(np.zeros(DERIVED_DECISION, dtype=np.float32))

    assert i == state_dim(), f"特征拼装长度 {i} != state_dim() {state_dim()}"
    return f


# ------------------------------------------------------------------ 候选动作特征

def cand_dim() -> int:
    """候选动作向量维度（固定）。末尾 8 维是**逐候选的派生量**。"""
    return _base_cand_dim() + DERIVED_CANDIDATE


def _base_cand_dim() -> int:
    return len(ACTION_TYPES) + TILE_SLOTS * 2 + 1 + len(KAN_KINDS) + 1


def action_parts(key: str) -> tuple[str, str | None, list[str], str | None]:
    """动作键 → `(type, tile, tiles, kan_kind)`（键文法见 `PROTOCOL.md` §8.3）。"""
    if not key:
        return "", None, [], None
    if key.startswith("discard:"):
        rest = key[len("discard:"):]
        tile = rest[:-len("/tsumogiri")] if rest.endswith("/tsumogiri") else rest
        return "discard", tile, [], None
    if key.startswith("riichi:"):
        return "riichi", key[len("riichi:"):], [], None
    if key.startswith("pon:"):
        return "pon", None, key[len("pon:"):].split("+"), None
    if key.startswith("chi:"):
        return "chi", None, key[len("chi:"):].split("+"), None
    if key.startswith("kan:"):
        parts = key[len("kan:"):].split(":")
        kind = parts[0] if parts else None
        if len(parts) == 2:
            return "kan", parts[1], [], kind
        if len(parts) == 3:                    # 大明杠：第三段是"手里取哪三张"
            return "kan", None, parts[2].split("+"), kind
        return "kan", None, [], kind
    if key in ACTION_TYPES:
        return key, None, [], None
    return "", None, [], None


def cand_vector(key: str) -> np.ndarray:
    """候选动作 → **基础段**向量（长度 `_base_cand_dim()`；派生量由 `cand_vector_full` 拼在后面）。"""
    v = np.zeros(_base_cand_dim(), dtype=np.float32)
    typ, tile, tiles, kan_kind = action_parts(key)
    i = 0
    if typ in ACTION_TYPES:
        v[ACTION_TYPES.index(typ)] = 1.0
    i += len(ACTION_TYPES)
    # 主牌（打牌/立直/单个杠）：槽位 one-hot
    if tile:
        s = tile_slot(tile)
        if s >= 0:
            v[i + s] = 1.0
    i += TILE_SLOTS
    # 取法（吃/碰/大明杠：那几张手里牌）→ 牌种计数
    for c in tiles:
        k = tile_kind(c)
        if k >= 0:
            v[i + k] += 1.0
    i += TILE_SLOTS
    v[i] = 1.0 if key.endswith("/tsumogiri") else 0.0
    i += 1
    if kan_kind in KAN_KINDS:
        v[i + KAN_KINDS.index(kan_kind)] = 1.0
    i += len(KAN_KINDS)
    v[i] = 1.0 if typ in ("tsumo", "ron", "pass", "kyuushu") else 0.0
    i += 1
    return v


def cand_vector_full(key: str, derived: Sequence[int] | None = None,
                     normalize: bool = True) -> np.ndarray:
    """候选向量 **+ 逐候选派生量**（8 维，由 Java 算好、来自 sidecar）。

    @param normalize `True` = 除以 `DERIVED_SCALE_CAND`（推理/训练用）；`False` = **原始整数**
                     （紧凑数据集落盘用：uint8 装得下，归一化统一留给 `bc._batch` 一处做）
    """
    v = np.zeros(cand_dim(), dtype=np.float32)
    base = cand_vector(key)
    v[:base.size] = base
    if derived is not None:
        for j in range(DERIVED_CANDIDATE):
            if j < len(derived):
                x = float(derived[j])
                v[base.size + j] = x / DERIVED_SCALE_CAND[j] if normalize else x
    return v


def candidates(state: np.ndarray, legal: list[str],
               derived: Sequence[Sequence[int]] | None = None,
               normalize: bool = True) -> np.ndarray:
    """把一次询问的 `legal` 展开成 `[n_legal, cand_dim]`（顺序**与 legal 一致**，掩码用它）。

    @param derived   每条候选的 8 个派生量（Java `ObsFeatures.perCandidate`，与 `legal` 同序）
    @param normalize 见 `cand_vector_full`
    """
    out = np.zeros((len(legal), cand_dim()), dtype=np.float32)
    for i, k in enumerate(legal):
        d = derived[i] if derived is not None and i < len(derived) else None
        out[i] = cand_vector_full(k, d, normalize=normalize)
    return out


# ------------------------------------------------------------------ 规格自述（给 Java 移植用）

def describe() -> str:
    """打印每段的偏移/宽度 —— Java 侧照这个表逐段实现（也是 golden 对拍的核对清单）。"""
    rows = [
        ("hand/34", KIND_COUNT), ("hand_red/34", KIND_COUNT),
        ("meld_kind/4x5", N_PLAYERS * len(MELD_KINDS)),
        ("meld_tiles/4x34", N_PLAYERS * KIND_COUNT),
        ("river/4x34", N_PLAYERS * KIND_COUNT),
        ("dora_indicators/34", KIND_COUNT),
        ("riichi/4", N_PLAYERS), ("ippatsu/4", N_PLAYERS),
        ("scores/4（自己在下标 0）", N_PLAYERS),
        ("points/5（自己点数·与三家均值差·顺位·与上一名差·与下一名差）", 5),
        ("round/5", 5), ("state/5", 5), ("self/4", 4), ("ctx/4", 4),
        ("visible/34", KIND_COUNT),
        ("drawn/37", TILE_SLOTS), ("called_tile/37", TILE_SLOTS),
        ("from/4", N_PLAYERS), ("win_note/3", len(WIN_NOTES) + 1),
        (f"derived {DERIVED_DECISION}（危险度 68 + 向听/打点 3，Java 算）", DERIVED_DECISION),
    ]
    lines, off = [f"FEATURE_VERSION={FEATURE_VERSION}  state_dim={state_dim()}  "
                  f"cand_dim={cand_dim()}", "state:"], 0
    for name, w in rows:
        lines.append(f"  [{off:>4}, {off + w:>4})  {name}")
        off += w
    lines.append(f"  total = {off}")
    lines.append("cand: " + " | ".join([
        f"type/{len(ACTION_TYPES)}", f"tile/{TILE_SLOTS}", f"tiles/{TILE_SLOTS}",
        "tsumogiri/1", f"kan_kind/{len(KAN_KINDS)}", "noarg/1",
        f"derived/{DERIVED_CANDIDATE}（Java 算）"]))
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
