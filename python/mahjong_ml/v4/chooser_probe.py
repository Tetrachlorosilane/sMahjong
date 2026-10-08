"""**阶段 4「风格抉择」可行性探针**：算出**理想抉择器（oracle chooser）的收益上界**。

## 回答什么问题

多专家路线前三步的结论是"单条风格线单独练 10 代，平均强度与 `g08` 无法区分"。
但那条结论**没有**触及架构的核心 —— **风格抉择**（按局面选哪位专家）。本模块只回答一件事：

> 如果**每一类局面都交给当时最合适的那位专家**，平均能比 `g08` 好多少？

- 上界 **< ~0.5 顺位点** ⇒ 门控**不值得训**（收益小于现有分辨成本），多风格路线正式收口；
- 上界 **≥ ~1.5~2 顺位点** ⇒ 值得训门控，并按桶列出"学什么"。

## 数据前提（**四位专家同桌、共用牌山**）

已有的数据都是"专家 vs `g08` 的 2+2、各线牌山不同" ⇒ **不能**做跨专家比较。
所以本模块要求一批新采集：`trainer selfplay --rotate --aux --policy net:g08,net:atk2,net:def3,net:win`
（**四方同桌**，`--rotate` 让每位专家把四个座位都坐一遍）。

## 度量口径（先说清楚，再报数）

### 单位、加权与配对

- **抉择器的触发条件是"正在行动的那个座位看到什么"** ⇒ 桶由 **`g08` 座位**在该小局的
  **逐条决策 `obs`** 决定（巡目 / 他家立直数 / 自家向听（sidecar）/ 自家是否已立直），
  取**廉价且公开**的字段，**不新造**。
- ⚠ **不用"该座位的第一条决策"当桶**：实测那一条**恒是该座位本局第一次摸牌**
  （`player_draws=1`）⇒ 一个座位在一局里的所有"第一条决策"几乎同一个桶，桶退化成一两个格、
  信息全丢。改成**决策级加权**：一手牌里该座位落进桶 `b` 的决策占 `w_{h,b}` 比例，
  该小局的结果就按这个比例摊给桶（`Σ_b w_{h,b} = 1`）——
  `mean_b(diff) = Σ_h w_{h,b}·diff_h / Σ_h w_{h,b}` 正是
  "**从这一手随机抽一条决策，它落在 b 桶时该小局结果的期望**"。这才是抉择器的目标量。
- **配对单元 = 小局 `(牌山, 场, 小局号)`**：四位专家**同场同牌山**，所以
  `diff_e = delta[座位(e)] − delta[座位(g08)]` 是**同一小局内的精确配对差分**（`diff_g08 ≡ 0`）。
  ⚠ 四位同场 ⇒ `Σ_seat delta ≡ 0` ⇒ 四个 `diff` 之和 `≡ −4·delta[g08]`；而 `g08` 自己那一列恒 0
  ⇒ **零假设下 `max_e diff` 的期望就是 0**（桶的基线被 `g08` 自己吸收掉，不会虚高）。
- **聚类**：同一场的多个小局**不是独立样本** ⇒ 先把同场同桶的小局按场求**加权均值**，再对
  **逐场值**做配对检验（`mahjong_ml.eval.paired_test`，自助法 CI + 符号检验）；统计量**全部**复用
  `eval.py`（含跨牌山的 `random_effects`），本模块**不另写第二份统计**。

### 上界三口径

1. **样本内上界**：`Σ_b freq(b) · max_e mean_b(diff_e)` —— 会**高估**（选择偏差），只作参照；
2. **诚实上界 ①（留一牌山）**：对每套牌山 `w`，专家由**其余套**选，在 `w` 上评估（等权平均）；
3. **诚实上界 ②（CI 规则）**：只在"该桶里某位专家的 CI（含牌山随机效应）**排除 0 且为正**"
   且**跨牌山符号一致**时才切换。

### 顺位点的换算（两条通道，都报）

`rank_points = (终局点数 − 30000)/1000 + 馬点[顺位]`（馬点 = `50/10/−10/−30`，含头名赏）。
本模块**在读到的每一条 `per_game` 上逐行验证这个式子**（`verify_rank_points`），残差不为 0 就报错。
抉择器的收益分两条通道：

- **分数通道（精确）**：每场点数增益 `Δscore` → `Δscore/1000`（`dRP/dScore = 1/1000` 是恒等式）；
- **顺位通道（反事实）**：把 `g08` 座位的终局点数按 `Δscore` 平移，与**其余三家的实测终局点数**
  重新排位（`profile.placement_from_scores`，与生产者同口径），算出新的馬点。
  ⚠ 这假定"其余三家终局点数不变" —— 真实牌局里另外三家也会变（见报告的自评）。

### 负向对照（**不做完不许下结论**）

把四位专家的标签**在桶内随机置换**，重算同一套上界，必须塌到 ≈0：

- `bucket_wall`（主口径）：每个 `(桶, 牌山)` 一个置换 —— 跨牌山**不共享** ⇒ 专门打掉"跨牌山一致"
  这件事，检验诚实上界是不是**选择偏差**的产物；
- `bucket_hand`（最严）：**每手**一个置换（独立逐手 ⇒ 等价于把该手的四位专家整体打乱）
  ⇒ 桶×专家的关联被彻底抹平，**连样本内上界**也该塌。

### ⛔ 取不到的 / 明确不做的

- 不做"把专家搬到 `g08` 座位"的真实重放（那要重跑引擎）⇒ 用"**同小局里该专家的实测收支**"
  当反事实代理。这是本报告最大的不确定性来源（四席同场的相互影响 + 座位角色）；
- 不训练门控、不改训练配方/契约、不动 `obs`；
- 每桶样本量、丢弃了哪些小局、稀疏桶并到了哪一级，全部打印出来。

用法：

    python -m mahjong_ml.v4.chooser_probe --dir <数据根>/stage4 --out report.json
    python -m mahjong_ml.v4.chooser_probe --self-check
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from mahjong_ml import dataset as ml_dataset
from mahjong_ml import eval as ml_eval
from mahjong_ml import features as ml_features

from . import profile as v4profile

# ===================================================================== 常量

#: 四位专家：`(短名, 权重目录名)`。⚠ 用**完整目录名**匹配 —— `g08` 这个子串同时出现在
#: `v4-league-g08` 与 `v4-expert-def3-g08` 里，只按 `g08` 匹配会把两条线混成一条。
EXPERTS: tuple[tuple[str, str], ...] = (
    ("g08", "v4-league-g08"),
    ("atk2", "v4-expert-atk2-g09"),
    ("def3", "v4-expert-def3-g08"),
    ("win", "v4-expert-win-g10"),
)
#: 参照（基线）专家下标：一切收益都是"相对它"。
REF = 0

#: 精算参数（`trainer/src/rules.hpp` 的 mleague 预设：`returnScore=30000`、`uma={30,10,-10,-30}`，
#: 另加头名赏 20 ⇒ 有效馬点 = 50/10/−10/−30）。⚠ **不是照抄常量就完事**：
#: `verify_rank_points()` 会在真实 `per_game` 上逐行验证这个式子。
RETURN_SCORE = 30000
UMA_EFF = (50.0, 10.0, -10.0, -30.0)

#: sidecar 逐决策段里"当前向听"的下标（`DERIVED_DECISION - 3` = 危险度 68 之后第一格）。
SHANTEN_COL = ml_features.DERIVED_DECISION - 3

#: 分桶特征档位名（**唯一数据源**，打印与标签都读它）
TURN_LABELS = ("巡1-6", "巡7-12", "巡13+")
RIICHI_LABELS = ("他家立直0", "他家立直1", "他家立直2+")
SHANTEN_LABELS = ("向听0", "向听1", "向听2", "向听3+")
SELF_RIICHI_LABELS = ("未立直", "已立直")
#: 桶元组的**规范序**（`merge_backoff` 的"最像"判据 = 共享前缀最长 ⇒ 末位最先被并掉）
CELL_ORDER = ("turn", "riichi", "shanten", "self_riichi")
BAND_LABELS: dict[str, tuple[str, ...]] = {
    "turn": TURN_LABELS, "riichi": RIICHI_LABELS,
    "shanten": SHANTEN_LABELS, "self_riichi": SELF_RIICHI_LABELS,
}

#: 逐场序列的键偏移（每套牌山一段互不重叠的场号）—— 与 `v4/gate.py` 的 `_KEY_STRIDE` 同一个构造。
KEY_STRIDE = 1_000_000


# ===================================================================== 分桶（纯函数）

def turn_band(player_draws: int) -> int:
    """巡目档：自家摸牌数 1-6 / 7-12 / 13+（`obs.player_draws`）。纯函数。"""
    d = int(player_draws)
    return 0 if d <= 6 else (1 if d <= 12 else 2)


def riichi_band(n_others: int) -> int:
    """他家立直数档：0 / 1 / 2+（`obs.riichi` 去掉自己那一格）。纯函数。"""
    n = int(n_others)
    return 0 if n <= 0 else (1 if n == 1 else 2)


def shanten_band(shanten: int) -> int:
    """自家向听档：0 / 1 / 2 / ≥3（sidecar `danger[:, SHANTEN_COL]`）。负数（取不到）归 3+。纯函数。"""
    s = int(shanten)
    return 0 if s <= 0 else (1 if s == 1 else (2 if s == 2 else 3))


def bool_band(flag: Any) -> int:
    """布尔 → 0/1 档（`obs.self_riichi`）。纯函数。"""
    return 1 if flag else 0


def features_of(obs: Mapping[str, Any], seat: int, shanten: int) -> dict[str, int]:
    """一条决策的 `obs` → 分桶特征档位表。**纯函数**（只读公开字段，不新造）。"""
    r = obs.get("riichi") or []
    n_others = sum(1 for j, v in enumerate(r) if int(j) != int(seat) and v)
    melds = obs.get("melds") or []
    n_melds = sum(len(m or []) for m in melds)
    scores = obs.get("scores") or []
    diff = 0.0
    if len(scores) == 4:
        diff = float(scores[seat]) - (sum(int(x) for x in scores) - int(scores[seat])) / 3.0
    rnd = obs.get("round") or {}
    return {
        "turn": turn_band(obs.get("player_draws", 0)),
        "riichi": riichi_band(n_others),
        "shanten": shanten_band(shanten),
        "self_riichi": bool_band(obs.get("self_riichi")),
        # 下面几项**不进主桶**（会和主桶交叉成上千个格），只作描述性拆分
        "melds": 0 if n_melds == 0 else (1 if n_melds <= 2 else 2),
        "tiles": 0 if int(obs.get("tiles_left", 0)) >= 53 else (
            1 if int(obs.get("tiles_left", 0)) >= 27 else 2),
        "dealer": 1 if rnd.get("dealer") == seat else 0,
        "score": 0 if diff < -5000 else (1 if diff <= 5000 else 2),
        "raw_turn": int(obs.get("player_draws", 0)),
    }


def cell_of(feat: Mapping[str, int]) -> tuple[int, ...]:
    """特征表 → 主桶元组（按 `CELL_ORDER`）。纯函数。"""
    return tuple(int(feat[k]) for k in CELL_ORDER)


def cell_label(cell: Sequence[int], keep: int) -> str:
    """桶元组 → 文本标签（只取前 `keep` 个特征；`keep=0` ⇒ `(全部)`）。纯函数。"""
    if keep <= 0:
        return "(全部)"
    return " / ".join(BAND_LABELS[k][int(v)] for k, v in zip(CELL_ORDER[:keep], cell[:keep]))


def merge_backoff(counts: Mapping[tuple[int, ...], float], n_feat: int, min_n: float
                  ) -> tuple[dict[tuple[int, ...], tuple[int, ...]],
                             dict[tuple[int, ...], float],
                             dict[tuple[int, ...], list[tuple[int, ...]]]]:
    """**凝聚式合并**（只并、不丢）：每次把**最小**的桶并进"与它最像"的桶（共享前缀最长），
    直到每个桶的**实测**样本量都 ≥ `min_n`（或只剩一个桶）。返回 `(格→桶键, 桶键→样本量, 桶键→成员格)`。

    为什么不是"整体选一个合并级别"：要求**所有**格在同一级别都够样本，会被**最稀的那一格**拖垮 ——
    实测 2 套牌山时"整级"方案从 45 格一路塌到 **3 个桶**（`巡1-6/7-12/13+`），主桶特征全废。
    为什么不是"按前缀逐格回退"：被逼到全域桶的稀格会**孤零零**留在那里
    （实测出现过 36 手的桶 vs 下限 400）—— 前缀链上根本没有够大的桶可以和它并。
    凝聚式合并按"最像"（共享前缀最长）并，稀格会并进**同族里最大的那个桶**，实测样本量才真达标。
    **纯函数**（全部并列用 `(-共享前缀, -样本量, 桶键)` 定序 ⇒ 可复现）。
    """
    n_feat = max(0, min(int(n_feat), len(CELL_ORDER)))
    members: dict[tuple[int, ...], list[tuple[int, ...]]] = {c: [c] for c in counts}
    sizes: dict[tuple[int, ...], float] = {c: float(counts[c]) for c in counts}
    if not members:
        return {}, {}, {}

    def rep(key: tuple[int, ...]) -> tuple[int, ...]:
        """桶的代表格 = 桶内样本量最大的成员（并列取字典序最小）⇒ 稳定。"""
        return min(sorted(members[key]), key=lambda c: (-counts[c], c))

    def shared(a: tuple[int, ...], b: tuple[int, ...]) -> int:
        ra, rb = rep(a), rep(b)
        k = 0
        while k < min(len(ra), len(rb)) and ra[k] == rb[k]:
            k += 1
        return k

    while len(members) > 1:
        b = min(sorted(members), key=lambda x: (sizes[x], x))
        if sizes[b] >= float(min_n):
            break
        others = [x for x in members if x != b]
        best = min(others, key=lambda x: (-shared(b, x), -sizes[x], x))
        members[best] = members[best] + members[b]
        sizes[best] += sizes[b]
        del members[b], sizes[b]
    mapping: dict[tuple[int, ...], tuple[int, ...]] = {}
    for key, cells in members.items():
        for c in cells:
            mapping[c] = key
    return mapping, sizes, members


def resize_backoff(counts: Mapping[tuple[int, ...], float], n_feat: int, min_n: float,
                   max_buckets: int) -> tuple[float, dict[tuple[int, ...], tuple[int, ...]],
                                              dict[tuple[int, ...], float],
                                              dict[tuple[int, ...], list[tuple[int, ...]]]]:
    """把 `min_n` 逐级抬高，直到桶数 ≤ `max_buckets`（返回最终 `min_n` 与合并结果）。**纯函数**。"""
    mn = float(min_n)
    out = merge_backoff(counts, n_feat, mn)
    for _ in range(200):
        if len(out[1]) <= int(max_buckets):
            return mn, out[0], out[1], out[2]
        mn = mn * 1.15 + 1.0
        out = merge_backoff(counts, n_feat, mn)
    return mn, out[0], out[1], out[2]


# ===================================================================== oracle（纯函数）

def pick_best(means: Mapping[int, float]) -> tuple[int, float]:
    """桶内选"当时最好的专家"：取最大均值，**并列按下标定序**（确定性 ⇒ 可复现）。纯函数。"""
    if not means:
        raise ValueError("pick_best：空输入")
    best = min(sorted(means), key=lambda e: (-float(means[e]), int(e)))
    return int(best), float(means[best])


def weighted_oracle(means_by_bucket: Mapping[Any, Mapping[int, float]],
                    share_by_bucket: Mapping[Any, float]) -> float:
    """按桶频率加权的 oracle 总收益（单位 = 被加权量的单位）。**纯函数**。"""
    return sum(float(share_by_bucket[b]) * pick_best(means_by_bucket[b])[1]
               for b in means_by_bucket)


def loo_oracle_total(per_wall: Mapping[int, Mapping[Any, tuple[Mapping[int, float], float]]],
                     ) -> tuple[float, dict[int, float]]:
    """**留一牌山** oracle：每套牌山的专家选择只能看**其余套**，在**本套**上评估。

    @param per_wall `{牌山: {桶: (该桶该套的逐专家均值, 该桶该套的频率)}}`
    @return `(等权平均的每套收益, 每套各自的收益)`

    为什么必须留一：样本内 `max_e` 的选择偏差**随桶数累积**（几十个桶各挑一个最大值 ⇒ 噪声被
    当成收益）。留一之后，"桶里那位专家真的更好"必须**跨牌山复现**才拿得到钱。
    **纯函数**。⚠ 少于 2 套牌山时留一无定义 ⇒ 返回 `(nan, {})`。
    """
    walls = sorted(per_wall)
    if len(walls) < 2:
        return float("nan"), {}
    out: dict[int, float] = {}
    for w in walls:
        others = [x for x in walls if x != w]
        pooled: dict[Any, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        for x in others:
            for b, (m, _s) in per_wall[x].items():
                for e, v in m.items():
                    pooled[b][e].append(float(v))
        gain = 0.0
        for b, (m_w, s_w) in per_wall[w].items():
            cand = {e: float(np.mean(v)) for e, v in pooled.get(b, {}).items()}
            if not cand:
                continue
            e_star, _ = pick_best(cand)
            gain += float(s_w) * float(m_w.get(e_star, 0.0))
        out[w] = gain
    return float(np.mean(list(out.values()))), out


def permute_rows(rng: np.random.Generator, diffs: np.ndarray) -> np.ndarray:
    """负向对照 `bucket_hand`：**每手**独立打乱四位专家的收支列。

    投一个随机置换到每一行 ⇒ 保持"同一小局四家收支之和 = 0"与每行的多重集，只抹掉
    "哪位专家在哪个桶里更好"。**纯函数**（随机源由调用方给 ⇒ 同种子可复现）。
    """
    d = np.asarray(diffs, dtype=float)
    idx = np.argsort(rng.random(d.shape), axis=1)          # 每行一个均匀随机置换
    return np.take_along_axis(d, idx, axis=1)


def permute_bucket_wall(rng: np.random.Generator, n_buckets: int, n_walls: int,
                        n_experts: int = len(EXPERTS)) -> np.ndarray:
    """负向对照 `bucket_wall`：每个 `(桶, 牌山)` 一个置换（**跨牌山不共享**）。

    返回 `perm[b, w, e] = 累加时给桶 b / 牌山 w 的"专家 e"用哪一列原始数据`。
    ⚠ 为什么必须"跨牌山不共享"：若整个桶共用一个置换，那个置换**在跨牌山时是一致的**，
    于是留一 oracle 照样能找到"稳定的最佳专家"（只是名字被换了）⇒ 空对照**不会塌**，
    这条对照就白做了。**纯函数**。
    """
    out = np.zeros((n_buckets, n_walls, n_experts), dtype=np.int64)
    for b in range(n_buckets):
        for w in range(n_walls):
            out[b, w] = rng.permutation(n_experts)
    return out


# ===================================================================== 顺位点（纯函数）

def rank_point_of(score: int, place: int) -> float:
    """`精算点数 = (点数 − 返点)/1000 + 有效馬点[顺位]`（`place` 为 1..4）。**纯函数**。"""
    return (int(score) - RETURN_SCORE) / 1000.0 + UMA_EFF[int(place) - 1]


def verify_rank_points(rows: Iterable[Mapping[str, Any]]) -> tuple[float, int]:
    """在真实 `per_game` 行上**逐行验证**顺位点公式，返回 `(最大绝对残差, 行数)`。

    为什么要它：整个"顺位点上界"都建立在这个式子上；`uma`/`returnScore` 抄错一位，
    报告里的每一个数字都偏，而且**不会报错**（这种错误只能靠对拍抓）。
    """
    worst, n = 0.0, 0
    for g in rows:
        sc, pl, rp = g.get("final_scores"), g.get("placement"), g.get("rank_points")
        if not (sc and pl and rp and len(sc) == len(pl) == len(rp) == 4):
            continue
        n += 1
        for i in range(4):
            worst = max(worst, abs(rank_point_of(sc[i], pl[i]) - float(rp[i])))
    return worst, n


def cf_rank_point_gain(scores: Sequence[int], seat: int, delta_score: float) -> float:
    """把 `seat` 的终局点数平移 `delta_score` 后的**顺位点变化**（其余三家点数不动）。纯函数。

    ⚠ 顺位用 `profile.placement_from_scores`（与生产者 `SelfPlay.placementOf` 同口径：
    同点按座次先后拆开）—— 不另写第二把尺子。
    """
    sc = [int(x) for x in scores]
    before = rank_point_of(sc[seat], v4profile.placement_from_scores(sc)[seat])
    sc[seat] = int(sc[seat] + round(float(delta_score)))
    after = rank_point_of(sc[seat], v4profile.placement_from_scores(sc)[seat])
    return after - before


# ===================================================================== 读盘

def expert_index(label: str) -> int:
    """策略标签 → 专家下标（按**完整目录名**认；认不出抛错，不猜）。纯函数。"""
    hits = [i for i, (_n, d) in enumerate(EXPERTS) if d in str(label)]
    if len(hits) != 1:
        raise ValueError(f"策略标签认不出唯一专家（命中 {hits}）：{label!r}")
    return hits[0]


def discover_walls(paths: Sequence[str]) -> list[Path]:
    """把命令行给的路径展开成"每套牌山一个目录"：给目录就用它；给父目录就取其下含 `g*.jsonl` 的子目录。"""
    out: list[Path] = []
    for p in paths:
        d = Path(p)
        if list(d.glob("g*.jsonl")):
            out.append(d)
        else:
            subs = sorted([x for x in d.iterdir() if x.is_dir() and list(x.glob("g*.jsonl"))])
            if not subs:
                raise SystemExit(f"{d} 里没有 g*.jsonl（也不是含子目录的数据根）")
            out.extend(subs)
    return out


def read_wall(dirpath: Path, wall_index: int) -> dict:
    """读一套牌山目录 → 逐手记录（含**每座位逐决策的桶计数**）/ 逐场记录 / 决策计数。**只读**。

    逐行 `json.loads`（与 `profile._load_jsonl` 同一口径），对 `decision` 行额外取一次 sidecar 的
    `shanten_now`。⚠ sidecar 行序必须与 `decision` 行序**一一对应** —— 所以 `idx` 对**每一条**
    decision 行自增（**不跳行**），并在结束时校验它等于 sidecar 的 `n`（对不上就报错，不猜）。
    """
    games: dict[int, dict] = {}
    hand_rows: dict[tuple[int, int], dict] = {}
    cell_w: dict[tuple[int, int, int], Counter] = defaultdict(Counter)
    dec_counts: Counter = Counter()
    sidecar_mismatch: list[str] = []
    n_dec = 0

    files = sorted(dirpath.glob("g*.jsonl"), key=lambda p: int(p.name[1:].split(".")[0]))
    for path in files:
        sc_path = ml_dataset.sidecar_path(path)
        if not sc_path.is_file():
            raise SystemExit(f"缺派生特征 sidecar：{sc_path}（先跑 `trainer features <dir>`）")
        sc = ml_dataset.load_sidecar(sc_path)
        idx = 0
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                head = line[:40]
                if '"type":"decision"' in head:
                    o = json.loads(line)
                    seat = int(o.get("seat", -1))
                    game = int(o.get("game", -1))
                    hno = int(o.get("hand_no", -1))
                    obs = o.get("obs") or {}
                    sh = int(sc["danger"][idx][SHANTEN_COL])
                    idx += 1
                    n_dec += 1
                    cell = cell_of(features_of(obs, seat, sh))
                    dec_counts[cell] += 1
                    cell_w[(game, hno, seat)][cell] += 1
                elif '"type":"hand"' in head:
                    o = json.loads(line)
                    r = v4profile.parse_hand_row(o, ())
                    hand_rows[(r.game, r.hand_no)] = {
                        "game": r.game, "hand_no": r.hand_no,
                        "delta": tuple(int(x) for x in r.delta),
                        "agari": bool(r.agari), "winner": int(r.winner),
                        "loser": int(r.loser), "tsumo": bool(r.tsumo),
                        "tenpai": tuple(bool(x) for x in r.tenpai)}
                elif '"type":"game"' in head:
                    o = json.loads(line)
                    games[int(o.get("game", -1))] = {
                        "seed": int(o.get("seed", 0)),
                        "policies": tuple(str(x) for x in (o.get("policies") or ())),
                        "final_scores": tuple(int(x) for x in (o.get("final_scores") or ())),
                        "placement": tuple(int(x) for x in (o.get("placement") or ())),
                    }
        if idx != int(sc["n"]):
            sidecar_mismatch.append(f"{path.name}: decision 行 {idx} 条 != sidecar {int(sc['n'])} 条")

    hands = list(hand_rows.values())
    for h in hands:
        h["w"] = tuple(cell_w.get((h["game"], h["hand_no"], s), Counter()) for s in range(4))
    return {"dir": str(dirpath), "wall": int(wall_index), "games": games, "hands": hands,
            "dec_counts": dec_counts, "sidecar_mismatch": sidecar_mismatch, "n_dec": n_dec,
            "n_files": len(files)}


# ===================================================================== 分析装配

def build_pack(walls: Sequence[dict], *, min_per_expert: float, max_buckets: int) -> dict:
    """多套牌山的读盘结果 → 逐手「**每位专家各自视角**的桶权重」+ 合并桶。

    ⚠ **为什么必须"每位专家各自视角"**（本模块最关键的一条口径，第一版在这里翻了车）：
    第一版按"**基线座位（`g08`）的状态**"分桶、再在同一小局里做 `d_e − d_g08` 的配对差分。
    那个设计有**系统性偏差**，而且负向对照抓得出来 —— 因为四家收支恒满足 `Σ_seat delta ≡ 0`，
    一旦按 `g08` **自己的状态**分桶，`g08` 的收支就被条件化了（"整局向听3+"本身就预示这一手输分），
    于是**另外三家的差分被机械地抬高**：零假设（四位专家完全相同）下
    `E[mean_b(diff_e)] = −μ_b`（`μ_b` = `g08` 在该桶的期望收支），而 `diff_g08 ≡ 0`
    ⇒ `max_e` **必然 > 0**。实测给出 `+7.9 顺位点/场` 这种荒谬上界，空对照只降到 `+1.8`
    （同一个偏差的残余，不是噪声）。**这条偏差不是靠"配对"能救的，是分桶口径选错了。**

    改成**对称口径**：每位专家都按**它自己**面对的局面分桶、只统计**它自己**的收支，
    再减去 `g08` 在**同一桶**里的均值。零假设下四位的条件期望完全相同 ⇒ 收益期望 = 0。✓
    代价：不再有"同小局逐手配对"，配对/聚类退到**逐场**（同一场的多小局不当独立样本）。
    """
    per_game: dict[tuple[int, int], dict] = {}
    bad_games: list[tuple] = []
    cell_w: dict[tuple[int, ...], float] = defaultdict(float)
    dec_counts: Counter = Counter()
    sidecar_bad: list[str] = []
    recs: list[dict] = []
    n_games = n_hands_all = n_drop_nocell = n_drop_nogame = 0

    for w in walls:
        dec_counts.update(w["dec_counts"])
        sidecar_bad.extend(w["sidecar_mismatch"])
        for g, gd in w["games"].items():
            n_games += 1
            try:
                labels = tuple(expert_index(p) for p in gd["policies"])
            except ValueError:
                bad_games.append((w["wall"], g, gd["policies"]))
                continue
            if sorted(labels) != list(range(len(EXPERTS))):
                bad_games.append((w["wall"], g, gd["policies"]))
                continue
            per_game[(w["wall"], g)] = {
                "labels": labels, "seat_of": {e: i for i, e in enumerate(labels)},
                "final_scores": gd["final_scores"], "seed": gd["seed"],
                "refseat": labels.index(REF)}

        for h in w["hands"]:
            n_hands_all += 1
            pg = per_game.get((w["wall"], h["game"]))
            if pg is None:
                n_drop_nogame += 1
                continue
            d = h["delta"]
            if len(d) != 4:
                n_drop_nogame += 1
                continue
            seats = tuple(pg["seat_of"][e] for e in range(len(EXPERTS)))
            wseat = h["w"]
            # 丢弃判据**必须对称**：四位专家里只要有一位在该小局没有任何决策，就整手丢掉
            # （只要求"参照座位有决策"会偏袒那一位 —— 第一版就是这么写的）
            if any(sum(wseat[s].values()) <= 0 for s in seats):
                n_drop_nocell += 1
                continue
            for s in seats:
                tot = float(sum(wseat[s].values()))
                for cell, c in wseat[s].items():
                    cell_w[cell] += (c / tot) / len(EXPERTS)   # 除以专家数 ⇒ 下限按"每专家"读
            recs.append({
                "wall": int(w["wall"]), "game": int(h["game"]), "hand_no": int(h["hand_no"]),
                "wseat": wseat, "seats": seats,
                "deltas": tuple(float(d[s]) for s in seats),
                "winner": int(h["winner"]), "loser": int(h["loser"]),
                "tsumo": bool(h["tsumo"]), "agari": bool(h["agari"]),
                "gamekey": int(w["wall"]) * KEY_STRIDE + int(h["game"]),
            })

    # ---- 逐格**凝聚式**合并（只把稀的格并进同族最大的桶；再抬高下限直到桶数 ≤ `--max-buckets`）
    mn, mapping, agg, members = resize_backoff(cell_w, len(CELL_ORDER), min_per_expert,
                                               max_buckets)
    prefix_of = {p: i for i, p in enumerate(sorted(agg))}
    nb = len(prefix_of)
    n = len(recs)
    deltas4 = np.zeros((n, len(EXPERTS)))
    walls_a = np.zeros(n, dtype=np.int64)
    gamekeys = np.zeros(n, dtype=np.int64)
    winners = np.full(n, -1, dtype=np.int64)
    losers = np.full(n, -1, dtype=np.int64)
    tsumos = np.zeros(n, dtype=bool)
    seats = np.zeros((n, len(EXPERTS)), dtype=np.int64)
    # COO：`(手 h, 专家 e, 桶 b, 权重 w)` —— 每位专家用**它自己视角**的桶分布
    hh: list[int] = []
    ee: list[int] = []
    bb: list[int] = []
    ww: list[float] = []
    for i, r in enumerate(recs):
        walls_a[i] = r["wall"]
        gamekeys[i] = r["gamekey"]
        deltas4[i] = r["deltas"]
        winners[i] = r["winner"]
        losers[i] = r["loser"]
        tsumos[i] = r["tsumo"]
        seats[i] = r["seats"]
        for e in range(len(EXPERTS)):
            wc_e: Counter = r["wseat"][r["seats"][e]]
            tot_e = float(sum(wc_e.values()))
            for cell, c in wc_e.items():
                p = mapping.get(cell)
                if p is None:
                    continue
                hh.append(i)
                ee.append(e)
                bb.append(prefix_of[p])
                ww.append(c / tot_e)

    dec_by_bucket: Counter = Counter()
    for cell, c in dec_counts.items():
        p = mapping.get(cell)
        if p is not None:
            dec_by_bucket[prefix_of[p]] += int(c)
    hands_by_bucket = Counter()
    for b in set(bb):
        hands_by_bucket[b] = sum(1 for i, e2, b2 in zip(hh, ee, bb) if b2 == b and e2 == REF)

    def _label(p: tuple[int, ...]) -> str:
        ms = members[p]
        base = cell_label(p, len(p))
        return base if len(ms) == 1 else f"{base}(+{len(ms) - 1}格)"

    return {"hh": np.array(hh, dtype=np.int64), "ee": np.array(ee, dtype=np.int64),
            "bb": np.array(bb, dtype=np.int64), "ww": np.array(ww, dtype=float),
            "deltas4": deltas4, "walls": walls_a, "gamekeys": gamekeys,
            "winners": winners, "losers": losers, "tsumos": tsumos, "seats": seats,
            "per_game": per_game, "n_games": n_games, "n_hands_all": n_hands_all,
            "n_drop_nocell": n_drop_nocell, "n_drop_nogame": n_drop_nogame,
            "bad_games": bad_games, "min_per_expert": mn, "n_buckets": nb,
            "labels_of_bucket": {i: _label(p) for p, i in prefix_of.items()},
            "prefix_of_bucket": {i: p for p, i in prefix_of.items()},
            "members_of_bucket": {i: members[p] for p, i in prefix_of.items()},
            "cell_w": dict(cell_w), "dec_counts": dec_counts, "sidecar_bad": sidecar_bad,
            "dec_by_bucket": dec_by_bucket, "hands_by_bucket": hands_by_bucket,
            "merged_sizes": {i: float(agg[p]) for p, i in prefix_of.items()},
            "hands_by_cell": len(cell_w), "mapping": mapping}


def game_index_map(per_game: Mapping[tuple[int, int], dict]) -> dict[int, int]:
    """`(牌山, 场)` 排序后的下标 → 逐场序列键。**纯函数**（键 = `牌山*KEY_STRIDE + 场`）。"""
    return {int(w) * KEY_STRIDE + int(g): i for i, (w, g) in enumerate(sorted(per_game))}


def oracle_pass(deltas4: np.ndarray, hh: np.ndarray, ee: np.ndarray, bb: np.ndarray,
                ww: np.ndarray, walls: np.ndarray, gamekeys: np.ndarray,
                per_game: Mapping[tuple[int, int], dict], *, n_buckets: int, n_walls: int,
                perm: np.ndarray | None = None, placebo_seed: int = 20270101) -> dict:
    """**对称口径**的 oracle 上界 + 顺位点反事实。**纯聚合**（负向对照直接复用）。

    @param perm `[桶, 牌山, 专家]`：读"专家 e"时用哪一列收支（`None` = 恒等）。
                      因为每位专家都**用它自己的桶**，置换只需换"读谁"，
                      而且对每个 `(桶, 牌山)` 都是一个**双射** ⇒ 零假设下四位可交换。
    """
    n_experts = deltas4.shape[1]
    wi_of = {int(w): i for i, w in enumerate(sorted({int(x) for x in walls}))}
    winv_all = np.array([wi_of[int(x)] for x in walls], dtype=np.int64)
    if perm is None:
        perm = np.tile(np.arange(n_experts, dtype=np.int64), (n_buckets, n_walls, 1))
    winv = winv_all[hh]
    dv = deltas4[hh, perm[bb, winv, ee]]

    flat_be = bb * n_experts + ee
    den = np.bincount(flat_be, weights=ww,
                      minlength=n_buckets * n_experts).reshape(n_buckets, n_experts)
    num = np.bincount(flat_be, weights=ww * dv,
                      minlength=n_buckets * n_experts).reshape(n_buckets, n_experts)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = np.where(den > 0, num / np.maximum(den, 1e-12), 0.0)
    # 桶频率 = 四位专家权重的平均（每位专家的总权重都 = 入样手数 ⇒ 同一把尺子）
    tot = float(den.sum(axis=0).mean())
    share = den.mean(axis=1) / tot if tot > 0 else den.mean(axis=1)

    def _gain(m_b: np.ndarray) -> tuple[int, float]:
        e_star = pick_best({e: m_b[e] for e in range(n_experts)})[0]
        return e_star, float(m_b[e_star] - m_b[REF])

    star = np.zeros(n_buckets, dtype=np.int64)
    gain_b = np.zeros(n_buckets)
    for b in range(n_buckets):
        star[b], gain_b[b] = _gain(m[b])
    in_sample = float((share * gain_b).sum())

    # ---- **不门控**的对照：只挑一位**全局最强**的专家、所有局面都用它
    #   它就是"门控要打败的基线"：门控的价值 = `oracle(按桶切换) − global(选一个人)`。
    #   实测这一项常常吃掉大部分 oracle 收益 ⇒ 那种收益**不需要门控**。
    gmean = np.array([float((share * m[:, e]).sum()) for e in range(n_experts)])
    eg = pick_best({e: gmean[e] for e in range(n_experts)})[0]
    global_ins = float(gmean[eg] - gmean[REF])

    # ---- 逐牌山
    flat = flat_be * n_walls + winv
    wden = np.bincount(flat, weights=ww,
                       minlength=n_buckets * n_experts * n_walls
                       ).reshape(n_buckets, n_experts, n_walls)
    wnum = np.bincount(flat, weights=ww * dv,
                       minlength=n_buckets * n_experts * n_walls
                       ).reshape(n_buckets, n_experts, n_walls)
    with np.errstate(invalid="ignore", divide="ignore"):
        pw_m = np.where(wden > 0, wnum / np.maximum(wden, 1e-12), 0.0)
    wtot = wden.sum(axis=0)                       # [专家, 牌山]
    pw_share = wden.mean(axis=1) / np.maximum(wtot.mean(axis=0)[None, :], 1e-12)

    loo_per_wall = np.full(n_walls, np.nan)
    star_loo = np.zeros((n_walls, n_buckets), dtype=np.int64)
    m_loo = np.zeros((n_walls, n_buckets, n_experts))
    if n_walls >= 2:
        allw = list(range(n_walls))
        for wj in allw:
            others = [j for j in allw if j != wj]
            oden = wden[:, :, others].sum(axis=2)
            onum = wnum[:, :, others].sum(axis=2)
            with np.errstate(invalid="ignore", divide="ignore"):
                mo = np.where(oden > 0, onum / np.maximum(oden, 1e-12), 0.0)
            m_loo[wj] = mo
            g = 0.0
            for b in range(n_buckets):
                e_star, _ = _gain(mo[b])
                star_loo[wj, b] = e_star
                g += float(pw_share[b, wj]) * float(pw_m[b, e_star, wj] - pw_m[b, REF, wj])
            loo_per_wall[wj] = g
    loo = float(np.nanmean(loo_per_wall)) if n_walls >= 2 else float("nan")
    global_loo_per_wall = np.full(n_walls, np.nan)
    global_loo = float("nan")
    if n_walls >= 2:
        for wj in range(n_walls):
            others = [j for j in range(n_walls) if j != wj]
            mo = m_loo[wj]
            gsh = pw_share[:, others].mean(axis=1)
            cand = {e: float((gsh * (mo[:, e] - mo[:, REF])).sum()) for e in range(n_experts)}
            e_g = pick_best(cand)[0]
            global_loo_per_wall[wj] = float(
                (pw_share[:, wj] * (pw_m[:, e_g, wj] - pw_m[:, REF, wj])).sum())
        global_loo = float(np.nanmean(global_loo_per_wall))

    # ---- 顺位点反事实：**插值式**（用桶级均值，不用逐手噪声）
    #   Δscore(场) = Σ_b W_ref(场, b) · [m_b(e*(b)) − m_b(REF)]
    #   ⚠ **评估必须用"被留出那一套自己的"桶级均值**（`pw_m[wj]`），⛔ 不能用挑选时用的
    #     `m_loo`：`e*` 就是让 `m_loo` 最大的那个 ⇒ 拿它去评估等于**又用了一遍选择集**，
    #     实测把"只分数通道"从 `+0.33` 抬到 `+0.84 顺位点/场`（2.6× 虚高）。
    gmap = game_index_map(per_game)
    nk = len(gmap)
    ref_sel = ee == REF
    gref = np.array([gmap[int(k)] for k in gamekeys[hh[ref_sel]]], dtype=np.int64)
    uniq, inv = np.unique(np.stack([gref, bb[ref_sel]], axis=1), axis=0, return_inverse=True)
    gw = np.bincount(inv, weights=ww[ref_sel], minlength=uniq.shape[0])
    wref: dict[tuple[int, int], float] = {}
    for (gi, b), v in zip(map(tuple, uniq.tolist()), gw.tolist()):
        wref[(int(gi), int(b))] = float(v)

    # ⚠ 这里必须是**牌山下标**（0..n_walls−1），不是牌山编号（1..8）—— 第一版写成了编号，
    #    在"牌山数 = 最大编号"时正好越界（合成端到端自检抓到的）。
    wall_of_key = np.array([wi_of[int(w)] for (w, _g) in sorted(per_game)], dtype=np.int64)
    dscore_ins = np.zeros(nk)
    dscore_loo = np.zeros(nk)
    for (gi, b), v in wref.items():
        dscore_ins[gi] += v * (m[b, star[b]] - m[b, REF])
        if n_walls >= 2:
            wj = int(wall_of_key[gi])
            es = int(star_loo[wj, b])
            dscore_loo[gi] += v * (pw_m[b, es, wj] - pw_m[b, REF, wj])
    if n_walls < 2:
        dscore_loo = np.full(nk, np.nan)

    def _rp(dscore: np.ndarray) -> np.ndarray:
        out = np.zeros(nk)
        for i, k in enumerate(sorted(per_game)):
            pg = per_game[k]
            if len(pg["final_scores"]) != 4 or not math.isfinite(float(dscore[i])):
                continue
            out[i] = cf_rank_point_gain(pg["final_scores"], pg["refseat"], dscore[i])
        return out

    def _placebo(dscore: np.ndarray) -> np.ndarray:
        """**安慰剂**：把逐场 Δ 在**同一套牌山内**打乱（保住边际分布、打断与"这一场自家点数"的关联）。

        为什么要它：顺位通道是**非线性**的（点数平移过阈值会换顺位、馬点跳一档）⇒
        `E[反事实顺位点] ≠ Δscore/1000`。安慰剂给出"只有噪声、没有信号"时这套反事实自己会产生多少。
        """
        rng = np.random.default_rng(placebo_seed)
        out = dscore.copy()
        for wv in sorted(set(int(x) for x in wall_of_key)):
            idx = np.flatnonzero((wall_of_key == wv) & np.isfinite(dscore))
            if idx.size > 1:
                out[idx] = dscore[rng.permutation(idx)]
        return out

    return {"in_sample": in_sample, "loo": loo, "loo_per_wall": loo_per_wall.tolist(),
            "global_ins": global_ins, "global_e": int(eg), "global_loo": global_loo,
            "global_loo_per_wall": global_loo_per_wall.tolist(),
            "m": m, "den": den, "share": share, "star": star, "star_loo": star_loo,
            "pw_m": pw_m, "pw_share": pw_share, "wref": wref,
            "dscore_in_sample": dscore_ins, "dscore_loo": dscore_loo,
            "rp_in_sample": _rp(dscore_ins), "rp_loo": _rp(dscore_loo),
            "rp_placebo": _rp(_placebo(dscore_loo)), "n_walls": n_walls,
            "wall_of_key": wall_of_key}


def bucket_re_stats(deltas4: np.ndarray, hh: np.ndarray, ee: np.ndarray, bb: np.ndarray,
                    ww: np.ndarray, walls: np.ndarray, gamekeys: np.ndarray,
                    per_game: Mapping[tuple[int, int], dict], *, n_buckets: int, n_walls: int,
                    perm: np.ndarray | None = None,
                    n_experts: int = len(EXPERTS)) -> dict:
    """每个桶每位专家的**逐场聚类**"专家 − `g08`"配对检验 + **牌山级随机效应**（全走 `eval.py`）。

    逐场值 = 该场该桶的**加权**每小局均值；同场多小局不当独立样本；
    再让 `e` 与 `REF` **按场配对**（同一场里两位各自的桶内均值之差）。
    @param perm 负向对照用（"专家 e 读哪一列"，与 `oracle_pass` 同一套语义）——
                用来数"**在纯噪声下这套 CI 规则会误判多少个桶**"。
    """
    gmap = game_index_map(per_game)
    gi = np.array([gmap[int(k)] for k in gamekeys[hh]], dtype=np.int64)
    if perm is None:
        perm = np.tile(np.arange(n_experts, dtype=np.int64), (n_buckets, n_walls, 1))
    wi_of = {int(w): i for i, w in enumerate(sorted({int(x) for x in walls}))}
    winv = np.array([wi_of[int(w)] for w in walls], dtype=np.int64)[hh]
    dv = deltas4[hh, perm[bb, winv, ee]]
    wall_of_game = np.array([wi_of[int(w)] for (w, _g) in sorted(per_game)], dtype=np.int64)
    flat = (bb * n_experts + ee) * (len(gmap) + 1) + gi
    total = n_buckets * n_experts * (len(gmap) + 1)
    gden = np.bincount(flat, weights=ww, minlength=total)
    gnum = np.bincount(flat, weights=ww * dv, minlength=total)
    with np.errstate(invalid="ignore", divide="ignore"):
        gm = np.where(gden > 0, gnum / np.maximum(gden, 1e-12), 0.0)
    gm = gm.reshape(n_buckets, n_experts, len(gmap) + 1)
    gden = gden.reshape(n_buckets, n_experts, len(gmap) + 1)

    out: dict[tuple[int, int], dict] = {}
    for b in range(n_buckets):
        idx_ref = np.flatnonzero(gden[b, REF] > 0)
        ref_ser = {int(i): float(gm[b, REF, i]) for i in idx_ref}
        for e in range(n_experts):
            idx_e = np.flatnonzero(gden[b, e] > 0)
            ser = {int(i): float(gm[b, e, i]) for i in idx_e}
            if e == REF:
                out[(b, e)] = {"mean": 0.0, "ci": (0.0, 0.0), "p": 1.0, "sd": 0.0,
                               "n_games": len(ser), "ci_re": (0.0, 0.0), "tau": 0.0,
                               "wall_pos": 0, "wall_n": 0, "wall_means": {}, "own_mean": 0.0}
                continue
            pr = ml_eval.paired_test(ser, ref_ser, f"b{b}:{EXPERTS[e][0]}", "ref", "pts")
            keys = sorted(set(ser) & set(ref_ser))
            per_wall: dict[int, list[float]] = defaultdict(list)
            for k in keys:
                per_wall[int(wall_of_game[k])].append(float(ser[k] - ref_ser[k]))
            pu = [{"dir": f"w{w}", "n": len(v), "delta": float(np.mean(v)),
                   "se": float(np.std(v, ddof=1) / math.sqrt(len(v))) if len(v) > 1 else float("nan")}
                  for w, v in sorted(per_wall.items())]
            re_ = ml_eval.random_effects(pu) if len(pu) >= 2 else {
                "mu": float("nan"), "se": float("nan"), "ci": (float("nan"), float("nan")),
                "tau": float("nan"), "q": float("nan"), "df": 0}
            out[(b, e)] = {"mean": float(pr.mean), "ci": (float(pr.ci[0]), float(pr.ci[1])),
                           "p": float(pr.p), "sd": float(pr.sd), "n_games": int(pr.n),
                           "ci_re": (float(re_["ci"][0]), float(re_["ci"][1])),
                           "mu_re": float(re_["mu"]), "tau": float(re_["tau"]),
                           "wall_pos": sum(1 for v in per_wall.values() if float(np.mean(v)) > 0),
                           "wall_n": len(per_wall),
                           "wall_means": {w: float(np.mean(v)) for w, v in per_wall.items()},
                           "own_mean": float(np.mean(list(ser.values()))) if ser else 0.0}
    return out

# ===================================================================== 自检

def self_check() -> int:
    """纯函数自检：分桶 / 加权 / oracle 上界 / 标签置换 / 顺位点换算。"""
    ok, bad = 0, 0

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok, bad
        if cond:
            ok += 1
        else:
            bad += 1
            print(f"  ✗ {msg}")

    # ---- ① 档位
    chk([turn_band(x) for x in (1, 6, 7, 12, 13, 18)] == [0, 0, 1, 1, 2, 2], "巡目档")
    chk([riichi_band(x) for x in (0, 1, 2, 3)] == [0, 1, 2, 2], "他家立直档")
    chk([shanten_band(x) for x in (-1, 0, 1, 2, 3, 6)] == [0, 0, 1, 2, 3, 3], "向听档")
    chk([bool_band(True), bool_band(False)] == [1, 0], "布尔档")
    obs = {"player_draws": 9, "riichi": [True, False, True, False], "self_riichi": True,
           "melds": [[], [["m1"]], [], []], "tiles_left": 40,
           "scores": [25000, 31000, 20000, 24000], "round": {"dealer": 1}}
    f = features_of(obs, 0, 2)
    chk(f["turn"] == 1 and f["riichi"] == 1 and f["shanten"] == 2 and f["self_riichi"] == 1,
        "features_of 主桶四项")
    chk(f["melds"] == 1 and f["tiles"] == 1 and f["dealer"] == 0 and f["score"] == 1,
        "features_of 描述项")
    chk(features_of(obs, 1, 0)["dealer"] == 1, "庄家判据按座位")
    chk(cell_of(f) == (1, 1, 2, 1), "cell_of 顺序 = CELL_ORDER")

    # ---- ② 逐格回退合并（只把稀的格往上并、不丢弃；密格保留最细粒度）
    counts = {c: 10.0 for c in [(0, 0, 0, 0), (0, 0, 0, 1), (1, 1, 1, 0), (1, 1, 1, 1)]}
    m1, s1, g1 = merge_backoff(counts, 4, 20)
    chk(len(s1) == 2 and all(v >= 20 for v in s1.values()), "凝聚合并：4 格 → 2 桶（并掉 self_riichi）")
    chk(m1[(0, 0, 0, 0)] == m1[(0, 0, 0, 1)] and m1[(1, 1, 1, 0)] == m1[(1, 1, 1, 1)],
        "只差 self_riichi 的两格并到一起（共享前缀最长）")
    m2, s2, _g2 = merge_backoff(counts, 4, 40)
    chk(len(s2) == 1 and abs(s2[m2[(1, 1, 1, 0)]] - 40.0) < 1e-9, "下限很大 ⇒ 全并成一个桶（40 手）")
    m3, s3, _g3 = merge_backoff(counts, 4, 1)
    chk(len(s3) == 4, "下限=1 ⇒ 不合并（4 格）")
    thin = {(0, 0, 0, 0): 100.0, (0, 0, 0, 1): 5.0}
    m4, s4, _g4 = merge_backoff(thin, 4, 20)
    chk(m4[(0, 0, 0, 1)] == (0, 0, 0, 0) and abs(s4[(0, 0, 0, 0)] - 105.0) < 1e-9,
        "稀格并进同族最大的桶（5 手 → 105 手），**不是**孤零零留在全域桶")
    two = {(0, 0, 0, 0): 500.0, (0, 0, 0, 1): 6.0, (0, 0, 1, 0): 900.0}
    m6, s6, _g6 = merge_backoff(two, 4, 50)
    chk(all(v >= 50.0 for v in s6.values()) and m6[(0, 0, 0, 1)] == (0, 0, 0, 0),
        "不动点：**每个桶的实测样本量**都 ≥ 下限（只看前缀总量会漏掉这一条）")
    chk(len(merge_backoff(two, 4, 50)[2]) == 2, "只并了那一格，另外两格保持独立")
    mn_r, _m5, s5, _g5 = resize_backoff({(i, 0, 0, 0): 5.0 for i in range(3)}, 4, 3.0, 2)
    chk(mn_r > 3.0 and len(s5) <= 2, "resize_backoff：抬高下限直到桶数 ≤ 上限")
    chk(cell_label((1, 2, 3, 1), 2) == "巡7-12 / 他家立直2+", "桶标签")
    chk(cell_label((1, 2, 3, 1), 0) == "(全部)", "空桶标签")

    # ---- ③ 选取与加权
    chk(pick_best({0: 1.0, 1: 2.0, 2: 2.0, 3: 0.0}) == (1, 2.0), "并列取下标最小")
    chk(abs(weighted_oracle({0: {0: 0.0, 1: 1.0}, 1: {0: 5.0, 1: 4.0}}, {0: 0.5, 1: 0.5}) - 3.0) < 1e-12,
        "加权 oracle（3.0 = 0.5×1 + 0.5×5）")
    chk(abs(weighted_oracle({0: {0: -1.0, 1: -2.0}}, {0: 1.0}) + 1.0) < 1e-12,
        "全负时取最大的那个（纯函数口径）")

    # ---- ④ 留一 oracle：跨牌山一致才有钱
    stable = {w: {"b": ({0: 0.0, 1: 3.0}, 1.0)} for w in (1, 2, 3)}
    g_stable, per_w = loo_oracle_total(stable)
    chk(abs(g_stable - 3.0) < 1e-12 and all(abs(v - 3.0) < 1e-12 for v in per_w.values()),
        "留一：每套都是专家 1 更好 ⇒ 收益 = 3.0")
    flip = {1: {"b": ({0: 0.0, 1: 3.0}, 1.0)}, 2: {"b": ({0: 0.0, 1: 3.0}, 1.0)},
            3: {"b": ({0: 0.0, 1: -9.0}, 1.0)}}
    g_flip, per_flip = loo_oracle_total(flip)
    chk(abs(g_flip - (-3.0)) < 1e-12 and abs(per_flip[3] - (-9.0)) < 1e-12,
        "留一：两套选 1、第三套评估到 −9 ⇒ 均值 −3（不复现的那套被扣回去，可以为负）")
    g_one, _ = loo_oracle_total({1: {"b": ({0: 0.0, 1: 3.0}, 1.0)}})
    chk(math.isnan(g_one), "只有 1 套牌山时留一无定义 ⇒ nan（不许当 0 用）")

    # ---- ⑤ 标签置换：保结构、破关联
    deltas = np.array([[100.0, -50.0, -30.0, -20.0], [0.0, 200.0, -100.0, -100.0]])
    p1 = permute_rows(np.random.default_rng(7), deltas)
    chk(np.allclose(np.sort(p1, axis=1), np.sort(deltas, axis=1)), "逐手置换保持每手四点数的多重集")
    chk(np.allclose(p1.sum(axis=1), deltas.sum(axis=1)), "逐手置换保持每手四点数之和 = 0")
    p2 = permute_bucket_wall(np.random.default_rng(7), 3, 2, 4)
    chk(p2.shape == (3, 2, 4) and all(sorted(p2[b, w].tolist()) == [0, 1, 2, 3]
                                      for b in range(3) for w in range(2)),
        "按桶×牌山的置换矩阵每格都是一个排列")

    # ---- ⑤b 置换必须**连基线一起搬**：差分在置换之后算 ⇒ 被置换出来的基线列恒 0
    D = np.array([[100.0, -50.0, -30.0, -20.0], [0.0, 200.0, -100.0, -100.0]])
    pv = permute_bucket_wall(np.random.default_rng(3), 1, 1, 4)[0, 0]
    dv = D[:, pv] - D[:, pv[REF]][:, None]
    chk(np.allclose(dv[:, REF], 0.0), "置换后**基线列恒 0**（否则空对照的期望会偏）")
    chk(np.allclose(dv[:, 1] - dv[:, 2], D[:, pv[1]] - D[:, pv[2]]),
        "置换后**两两差分**仍是原来那几列的两两差分（只是换了名字）")
    chk(np.allclose(dv.sum(axis=1), -4.0 * D[:, pv[REF]]), "置换后仍满足 Σdiff = −4·d_基线")

    # ---- ⑥ 顺位点
    chk(abs(rank_point_of(35500, 1) - 55.5) < 1e-9, "顺位点：35500 / 1 位 = +55.5")
    chk(abs(rank_point_of(24700, 3) - (-15.3)) < 1e-9, "顺位点：24700 / 3 位 = −15.3")
    chk(abs(sum(rank_point_of(25000, p) for p in (1, 2, 3, 4))) < 1e-9,
        "四家同点 25000 ⇒ 顺位点全 0（与 trainer 的断言同口径）")
    chk(abs(sum([rank_point_of(6700, 4), rank_point_of(24700, 3),
                 rank_point_of(33100, 2), rank_point_of(35500, 1)])) < 1e-9,
        "真实一场：顺位点四家之和 = 0")
    chk(abs(cf_rank_point_gain([24700, 35500, 33100, 6700], 0, 1600) - 1.6) < 1e-9,
        "反事实：名次不变 ⇒ 只有分数通道 +1.6")
    chk(abs(cf_rank_point_gain([24700, 35500, 33100, 6700], 0, 7000) - 7.0) < 1e-9,
        "反事实：仍不换名次 ⇒ +7.0")
    chk(abs(cf_rank_point_gain([24700, 35500, 33100, 6700], 0, 10000) - 30.0) < 1e-9,
        "反事实：升级到 2 位 ⇒ 分数 +10 且馬点 +20")
    chk(abs(cf_rank_point_gain([24700, 35500, 33100, 6700], 0, -24700) - (-44.7)) < 1e-9,
        "反事实：暴跌到 4 位 ⇒ 分数通道 −24.7 与馬点 −20 一起算（−15.3 → −60）")
    worst, nrow = verify_rank_points([
        {"final_scores": [24700, 35500, 33100, 6700], "placement": [3, 1, 2, 4],
         "rank_points": [-15.3, 55.5, 13.1, -53.3]}])
    chk(worst < 1e-9 and nrow == 1, "verify_rank_points 在真实一行上残差为 0")

    # ---- ⑦ 端到端：真交互 ⇒ 留一出正数；打乱 ⇒ 塌
    rng = np.random.default_rng(11)
    n_h, n_w, n_b = 900, 4, 3
    cells = rng.integers(0, n_b, n_h)
    widx = rng.integers(0, n_w, n_h)
    base = rng.normal(0, 800, (n_h, 4))
    base -= base.mean(axis=1, keepdims=True)                 # 每手四家和为 0（同真实数据）
    real = base + np.array([0.0, 300.0, -200.0, 100.0])      # 全局强度差
    for b in range(n_b):
        real[cells == b, 1] += 250.0 * b                     # 桶×专家交互
    W = np.zeros((n_h, n_b))
    W[np.arange(n_h), cells] = 1.0
    walls_arr = widx + 1
    per_wall: dict[int, dict[int, tuple[dict[int, float], float]]] = defaultdict(dict)
    for b in range(n_b):
        for w in range(1, n_w + 1):
            sel = (cells == b) & (walls_arr == w)
            if not sel.any():
                continue
            m = {e: float(real[sel, e].mean()) for e in range(4)}
            per_wall[w][b] = (m, float(sel.mean()))
    g_real = loo_oracle_total(per_wall)[0]
    perm = permute_bucket_wall(np.random.default_rng(5), n_b, n_w, 4)
    # 零假设下"把**基线座位的状态**当分桶条件 + 同局配对差分"必然虚高（第一版踩的坑）：
    one_hand = np.array([[-1000.0, 300.0, 400.0, 300.0]])       # 基线输 1000，其余三家合计 +1000
    bad_diff = one_hand - one_hand[:, [REF]]
    chk(abs(bad_diff[0, REF]) < 1e-12 and bad_diff[0].max() > 0,
        "零假设下「按基线状态分桶 + 同局配对」的 max 也 > 0 ⇒ 第一版偏差的来源，"
        "所以口径必须**对称**（每位专家按它自己的局面分桶、只统计它自己的收支）")
    # 用置换矩阵重算逐牌山均值
    pw_perm: dict[int, dict[int, tuple[dict[int, float], float]]] = defaultdict(dict)
    for b in range(n_b):
        for w in range(1, n_w + 1):
            sel = (cells == b) & (walls_arr == w)
            if not sel.any():
                continue
            cols2 = real[sel][:, perm[b, w - 1]]
            m = {e: float(cols2[:, e].mean()) for e in range(4)}
            pw_perm[w][b] = (m, float(sel.mean()))
    g_perm_w = loo_oracle_total(pw_perm)[0]
    perm_h = permute_rows(np.random.default_rng(5), real)
    pw_hand: dict[int, dict[int, tuple[dict[int, float], float]]] = defaultdict(dict)
    for b in range(n_b):
        for w in range(1, n_w + 1):
            sel = (cells == b) & (walls_arr == w)
            if not sel.any():
                continue
            m = {e: float(perm_h[sel, e].mean()) for e in range(4)}
            pw_hand[w][b] = (m, float(sel.mean()))
    g_perm_h = loo_oracle_total(pw_hand)[0]
    chk(g_real > 100.0, f"端到端：真交互 ⇒ 留一上界为正（实测 {g_real:.1f}）")
    chk(abs(g_perm_w) < 60.0, f"端到端：按桶×牌山打乱 ⇒ 留一上界塌（实测 {g_perm_w:.1f}）")
    chk(abs(g_perm_h) < 60.0, f"端到端：逐手打乱 ⇒ 留一上界塌（实测 {g_perm_h:.1f}）")

    # ---- ⑧ 端到端（**对称口径** `oracle_pass`）：真交互 ⇒ 留一出正数；打乱 ⇒ 塌；零交互 ⇒ ≈0
    # 样本量按"留一上界的理论噪声"配：`sd(loo) ≈ sqrt(Σ_{b,w} share² · 2σ_h²/n_bw)`。
    #   这里 8 套 × 3000 手 × 12 桶、`σ_h ≈ 4.7e3` ⇒ 逐 (桶, 牌山) 差值 sd ≈ 4.0e2 ⇒ 合并后 ≈ 4e1。
    def _synth(rng, strength, inter):
        n_w, n_b, per = 8, 12, 3000
        n_h = n_w * per
        wall = np.repeat(np.arange(n_w), per)
        hh_, ee_, bb_, ww_ = [], [], [], []
        d = np.zeros((n_h, 4))
        bof = np.zeros((n_h, 4), dtype=np.int64)
        for i in range(n_h):
            shared = float(rng.normal(0, 4000))
            for e in range(4):
                b = int(rng.integers(0, n_b))
                bof[i, e] = b
                hh_.append(i)
                ee_.append(e)
                bb_.append(b)
                ww_.append(1.0)
                d[i, e] = shared + float(rng.normal(0, 2500)) + strength[e] + inter[b, e]
        gk_ = np.array([(int(wall[i]) + 1) * KEY_STRIDE + i for i in range(n_h)], dtype=np.int64)
        pg_ = {((int(wall[i]) + 1), i): {"final_scores": (25000, 25000, 25000, 25000),
                                         "refseat": 0, "placement": (1, 1, 1, 1)}
               for i in range(n_h)}
        return (d, np.array(hh_), np.array(ee_), np.array(bb_), np.array(ww_),
                wall + 1, gk_, pg_, n_b, n_w, bof)

    interA = np.zeros((12, 4))
    interA[:, 1] = np.linspace(-100.0, 1500.0, 12)          # 只有专家 1 有桶相关结构
    zero4 = np.zeros(4)
    dA, hhA, eeA, bbA, wwA, wA, gkA, pgA, nbA, nwA, bofA = _synth(
        np.random.default_rng(31), zero4, interA)
    rA = oracle_pass(dA, hhA, eeA, bbA, wwA, wA, gkA, pgA, n_buckets=nbA, n_walls=nwA)
    rAp = oracle_pass(dA, hhA, eeA, bbA, wwA, wA, gkA, pgA, n_buckets=nbA, n_walls=nwA,
                      perm=permute_bucket_wall(np.random.default_rng(5), nbA, nwA, 4))
    _exA = np.arange(4)[None, :]
    dAn = dA - interA[bofA, _exA]                            # 真·零假设：四位完全同分布
    rAn = oracle_pass(dAn, hhA, eeA, bbA, wwA, wA, gkA, pgA, n_buckets=nbA, n_walls=nwA)
    dB, hhB, eeB, bbB, wwB, wB, gkB, pgB, nbB, nwB, _bofB = _synth(
        np.random.default_rng(37), np.array([0.0, 900.0, -80.0, 40.0]), np.zeros((12, 4)))
    rB = oracle_pass(dB, hhB, eeB, bbB, wwB, wB, gkB, pgB, n_buckets=nbB, n_walls=nwB)
    chk(rA["loo"] > 300.0, f"对称口径端到端：真桶×专家交互 ⇒ 留一为正（实测 {rA['loo']:.1f}）")
    chk(abs(rAn["loo"]) < 200.0,
        f"对称口径端到端：**四位完全同分布 ⇒ 留一上界 ≈0**（实测 {rAn['loo']:.1f}，噪声 ±40）")
    chk(abs(rAp["loo"]) < 200.0,
        f"对称口径端到端：按桶×牌山打乱 ⇒ 留一塌（实测 {rAp['loo']:.1f}）")
    chk(abs(rB["loo"] - 900.0) < 250.0,
        f"对称口径端到端：只留**全局**强度差 ⇒ 留一 ≈ 900（实测 {rB['loo']:.1f}）"
        "—— 这一份不是门控的功劳（选一个人就够）")

    print(f"CHOOSER-PROBE SELFCHECK {'PASS' if bad == 0 else 'FAIL'}：{ok} 项通过 / {bad} 项失败")
    return 0 if bad == 0 else 1


# ===================================================================== 主流程

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="阶段 4：理想抉择器（oracle chooser）的收益上界")
    ap.add_argument("--dir", action="append", default=[], help="牌山目录（可多次）；给父目录自动展开")
    ap.add_argument("--out", default=None, help="JSON 报告落盘路径")
    ap.add_argument("--min-per-expert", type=float, default=400.0,
                    help="每个合并桶的**每专家**（加权）样本量下限；不够就往上合并一级")
    ap.add_argument("--max-buckets", type=int, default=60)
    ap.add_argument("--top", type=int, default=15, help="可切换桶清单打印条数")
    ap.add_argument("--perm-seed", type=int, default=20270101)
    ap.add_argument("--perm-reps", type=int, default=20,
                    help="负向对照的置换次数（量出零分布，而不是只看一次是否接近 0）")
    ap.add_argument("--min-wall-consistency", type=float, default=0.75,
                    help="跨牌山符号一致的判据（默认 0.75 = 6/8 套）")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)

    if args.self_check:
        return self_check()
    if not args.dir:
        ap.error("要么给 --dir，要么用 --self-check")

    dirs = discover_walls(args.dir)
    print("== 阶段 4：理想抉择器（oracle chooser）收益上界 ==")
    print(f"读入 {len(dirs)} 套牌山：")
    walls = []
    for i, d in enumerate(dirs, 1):
        w = read_wall(d, i)
        walls.append(w)
        print(f"  w{i} {Path(w['dir']).name}：{len(w['games'])} 场 / {len(w['hands'])} 小局 / "
              f"{w['n_dec']} 条决策 / {w['n_files']} 个轨迹文件")
        if w["sidecar_mismatch"]:
            print(f"     ⚠ sidecar 行数不符：{w['sidecar_mismatch']}")

    seeds = sorted({gd["seed"] for w in walls for gd in w["games"].values()})
    n_games_raw = sum(len(w["games"]) for w in walls)
    print("\n-- 前提体检 --")
    print(f"  牌山（目录）：{', '.join(Path(w['dir']).name for w in walls)}")
    print(f"  逐场 seed 共 {len(seeds)} 个（互不重复：{len(seeds) == n_games_raw}）"
          f"；全部 {min(seeds)} .. {max(seeds)}")

    pack = build_pack(walls, min_per_expert=args.min_per_expert, max_buckets=args.max_buckets)
    hh, ee, bb, ww = pack["hh"], pack["ee"], pack["bb"], pack["ww"]
    deltas4 = pack["deltas4"]
    warr, gk = pack["walls"], pack["gamekeys"]
    n_hands = deltas4.shape[0]
    nb = pack["n_buckets"]
    n_walls = len({int(x) for x in warr})
    print(f"  逐场四席各一位：违背 {len(pack['bad_games'])} 场"
          f"{'（前 3：' + str(pack['bad_games'][:3]) + '）' if pack['bad_games'] else ''}")
    print(f"  小局总数 {pack['n_hands_all']}；**四位里只要有一位在该小局没有决策就整手对称丢弃** "
          f"{pack['n_drop_nocell']} 手；无场记录丢弃 {pack['n_drop_nogame']} 手 ⇒ 入样 {n_hands} 手")
    lens = Counter(len(p) for p in pack["prefix_of_bucket"].values())
    print(f"  分桶：原始格 {pack['hands_by_cell']} → **凝聚式合并**（每桶·每专家加权下限 "
          f"{pack['min_per_expert']:.0f} 手）⇒ **{nb} 个桶**（粒度分布 "
          f"{dict(sorted(lens.items()))} 个特征 → 桶数）")

    # ---- 顺位点公式逐行验证（前提，不通过就别往下读）
    worst, nrow = verify_rank_points(
        [{"final_scores": list(gd["final_scores"]), "placement": list(gd["placement"]),
          "rank_points": [rank_point_of(gd["final_scores"][i], gd["placement"][i])
                          for i in range(4)]}
         for w in walls for gd in w["games"].values()
         if len(gd["final_scores"]) == 4 and len(gd["placement"]) == 4])
    print(f"  顺位点公式 `(点数−30000)/1000 + 馬点[50,10,−10,−30]`：{nrow} 场逐行最大残差 {worst:.2e}"
          f" ⇒ {'成立' if worst < 0.05 else '❌ 不成立，别信下面的顺位点数字'}")

    wsize = sorted(float(v) for v in pack["merged_sizes"].values())
    print(f"  桶样本量（决策加权手数/桶，每桶再 ×4 位专家）：min={wsize[0]:.0f} "
          f"p25={wsize[len(wsize)//4]:.0f} 中位={wsize[len(wsize)//2]:.0f} max={wsize[-1]:.0f}")
    hsize = sorted(pack["hands_by_bucket"].values())
    print(f"  桶样本量（**含权**手数/桶）：min={hsize[0]} 中位={hsize[len(hsize)//2]} max={hsize[-1]}")
    decs = sorted(pack["dec_by_bucket"].values())
    print(f"  桶样本量（决策/桶）：min={decs[0]} 中位={decs[len(decs)//2]} max={decs[-1]}"
          f"（进桶决策 {sum(decs)}）")
    thin = sorted(pack["merged_sizes"].items(), key=lambda kv: kv[1])[:5]
    print("  最少的 5 个桶：" + "；".join(
        f"{pack['labels_of_bucket'][b]}({n:.0f} 手/{pack['dec_by_bucket'].get(b, 0)} 决策)"
        for b, n in thin))
    kept = set(pack["mapping"])
    miss = [c for c in pack["dec_counts"] if c not in kept]
    if miss:
        print(f"  ⚠ 有决策但**无保留小局**因而未进任何桶的格：{len(miss)} 个"
              f"（合计 {sum(pack['dec_counts'][c] for c in miss)} 条决策）—— 已丢弃，在此点名")

    # ---- 三口径
    real = oracle_pass(deltas4, hh, ee, bb, ww, warr, gk, pack["per_game"],
                       n_buckets=nb, n_walls=n_walls)
    hpg = n_hands / max(1, pack["n_games"])
    print(f"\n-- oracle 上界（相对 {EXPERTS[REF][0]}；单位：点/小局，以及只走分数通道的顺位点/场）--")
    print(f"  样本内上界        ：{real['in_sample']:+8.1f} 点/小局"
          f"  = {real['in_sample'] * hpg / 1000:+.3f} 顺位点/场")
    print(f"  诚实① 留一牌山    ：{real['loo']:+8.1f} 点/小局"
          f"  = {real['loo'] * hpg / 1000:+.3f} 顺位点/场")
    if n_walls >= 2:
        print("     （逐套留一读数：" + " ".join(
            f"w{i+1}:{v:+.1f}" for i, v in enumerate(real["loo_per_wall"])) + "）")
    print(f"  不门控·只选一位全局最强（{EXPERTS[real['global_e']][0]}）："
          f"样本内 {real['global_ins']:+8.1f} / 留一 {real['global_loo']:+8.1f} 点/小局"
          f"  = {real['global_loo'] * hpg / 1000:+.3f} 顺位点/场")
    print(f"  ⇒ **门控本身**（按桶切换 − 只选一位）在诚实口径下只多 "
          f"{real['loo'] - real['global_loo']:+.1f} 点/小局"
          f" = {(real['loo'] - real['global_loo']) * hpg / 1000:+.3f} 顺位点/场"
          " —— 这一项才是训门控能换到的东西")

    st = bucket_re_stats(deltas4, hh, ee, bb, ww, warr, gk, pack["per_game"],
                         n_buckets=nb, n_walls=n_walls)
    switchable = []
    for b in range(nb):
        best = None
        for e in range(1, len(EXPERTS)):
            s = st[(b, e)]
            if math.isfinite(s["ci_re"][0]) and s["ci_re"][0] > 0 and s["p"] < 0.05 \
                    and s["wall_pos"] >= args.min_wall_consistency * s["wall_n"]:
                if best is None or s["mean"] > best[2]["mean"]:
                    best = (b, e, s)
        if best is not None:                     # **每个桶只留最好的一位**（否则"占局面"会重复计数）
            switchable.append(best)
    switchable.sort(key=lambda t: -float(real["share"][t[0]]) * t[2]["mean"])
    ci_gain = float(sum(float(real["share"][b]) * s["mean"] for b, _e, s in switchable))
    share_switch = float(sum(float(real["share"][b]) for b, _e, _s in switchable))
    print(f"  诚实② CI 规则     ：{ci_gain:+8.1f} 点/小局"
          f"  = {ci_gain * hpg / 1000:+.3f} 顺位点/场"
          f"；可切换桶 {len(switchable)} 个，占局面 {100 * share_switch:.1f}%")

    # ---- 顺位点（含顺位通道）
    def _rp_line(name: str, arr: np.ndarray) -> dict:
        keep = np.isfinite(arr)
        ser = {i: float(v) for i, v in enumerate(arr) if keep[i]}
        zero = {k: 0.0 for k in ser}
        pr = ml_eval.paired_test(ser, zero, name, "zero", "rank_points")
        per_wall: dict[int, list[float]] = defaultdict(list)
        for i, (wk, _g) in enumerate(sorted(pack["per_game"])):
            if keep[i]:
                per_wall[wk].append(float(arr[i]))
        if not ser:
            print(f"    {name:<22} （无有效场）")
            return {"mean": float("nan"), "ci": [float("nan")] * 2,
                    "ci_re": [float("nan")] * 2, "tau": float("nan"), "p": float("nan"), "n": 0}
        pu = [{"dir": f"w{w}", "n": len(v), "delta": float(np.mean(v)),
               "se": float(np.std(v, ddof=1) / math.sqrt(len(v))) if len(v) > 1 else float("nan")}
              for w, v in sorted(per_wall.items())]
        re_ = ml_eval.random_effects(pu) if len(pu) >= 2 else {
            "ci": (float("nan"), float("nan")), "tau": float("nan")}
        print(f"    {name:<22} Δ={pr.mean:+.3f} 顺位点/场  CI[{pr.ci[0]:+.3f},{pr.ci[1]:+.3f}]"
              f"  牌山随机效应 CI[{re_['ci'][0]:+.3f},{re_['ci'][1]:+.3f}]"
              f"  τ={re_['tau']:.3f}  符号检验 p={pr.p:.4f}  n={pr.n}")
        return {"mean": float(pr.mean), "ci": [float(pr.ci[0]), float(pr.ci[1])],
                "ci_re": [float(re_["ci"][0]), float(re_["ci"][1])], "tau": float(re_["tau"]),
                "p": float(pr.p), "n": int(pr.n)}

    print("\n-- 顺位点（点数平移后**重新排位**算馬点 = 含顺位通道）--")
    rp = {"in_sample": _rp_line("样本内（参照）", real["rp_in_sample"]),
          "loo": _rp_line("诚实①留一牌山", real["rp_loo"]),
          "score_only_loo": _rp_line("留一·只分数通道", real["dscore_loo"] / 1000.0),
          "placebo_loo": _rp_line("留一·安慰剂(只噪声)", real["rp_placebo"])}
    print("  读法：`留一` − `只分数通道` = **顺位通道**；`安慰剂` = 把逐场 Δ 在同套牌山内打乱后"
          "同一套反事实的读数\n        （Δ 的边际分布不变、与'这一场自家点数'的关联被打断）"
          "⇒ 顺位通道与安慰剂同量级就说明它**主要是反事实的噪声偏差**，不是收益。")

    # ---- 可切换桶清单
    print(f"\n-- 可切换桶清单（牌山随机效应 CI 排除 0 且为正 + 跨牌山符号一致 ≥"
          f"{args.min_wall_consistency:.2f}），按贡献排序，最多 {args.top} 条 --")
    print(f"  {'桶':<32}{'专家':<5}{'点/小局':>9}{'占局面':>8}{'顺位点/场':>11}"
          f"{'跨牌山':>8}{'p':>9}{'牌山RE CI':>18}")
    if not switchable:
        print("  （无）")
    for b, e, s in switchable[:args.top]:
        contrib_rp = float(real["share"][b]) * s["mean"] * hpg / 1000.0
        ci_s = "[{:+.1f},{:+.1f}]".format(s["ci_re"][0], s["ci_re"][1])
        print(f"  {pack['labels_of_bucket'][b]:<32}{EXPERTS[e][0]:<5}{s['mean']:>+9.1f}"
              f"{100 * float(real['share'][b]):>7.1f}%{contrib_rp:>+11.3f}"
              f"{s['wall_pos']:>4}/{s['wall_n']:<3}{s['p']:>9.4f}{ci_s:>18}")

    # ---- 负向对照：**跑一列置换**，把"空对照分布"量出来（而不是只跑一次看它是否"≈0"）
    K = int(args.perm_reps)
    ctrl: dict = {}
    if K <= 0:
        print("\n-- 负向对照：**已按 `--perm-reps 0` 跳过**（主读数仍有效，但**不许据此下结论**："
              "§16.4 的零分布是结论的一部分）--")
    else:
        print(f"\n-- 负向对照：桶内随机置换四位专家的标签（K={K} 次，perm_seed={args.perm_seed}）--")
        print("   为什么必须量分布：这套估计量**本身就有噪声底**（选择 + 桶级均值估计），"
              "只跑一次看到 `28.7` 与 `0` 不同\n   并不能说明有信号 —— 要看它是否超出**置换零分布**。")
        print(f"  {'口径':<16}{'留一(点/小局) 均值±sd':>26}{'最大':>10}{'顺位点/场 均值±sd':>24}"
              f"{'最大':>9}{'CI规则误判桶数(均)':>18}")
    for mode in (() if K <= 0 else ("bucket_wall", "bucket_hand")):
        loos, rps, nsw = [], [], []
        for k in range(K):
            rng = np.random.default_rng(args.perm_seed + 1000 * k)
            if mode == "bucket_wall":
                pm = permute_bucket_wall(rng, nb, n_walls, len(EXPERTS))
                dd = deltas4
            else:
                pm = None
                dd = permute_rows(rng, deltas4)
            r = oracle_pass(dd, hh, ee, bb, ww, warr, gk, pack["per_game"],
                            n_buckets=nb, n_walls=n_walls, perm=pm)
            loos.append(float(r["loo"]))
            rps.append(float(np.nanmean(r["rp_loo"])))
            stk = bucket_re_stats(dd, hh, ee, bb, ww, warr, gk, pack["per_game"],
                                 n_buckets=nb, n_walls=n_walls, perm=pm)
            cnt = 0
            for b in range(nb):
                for e in range(1, len(EXPERTS)):
                    s = stk[(b, e)]
                    if math.isfinite(s["ci_re"][0]) and s["ci_re"][0] > 0 and s["p"] < 0.05 \
                            and s["wall_pos"] >= args.min_wall_consistency * s["wall_n"]:
                        cnt += 1
                        break
            nsw.append(cnt)
        ctrl[mode] = {"loo_mean": float(np.mean(loos)), "loo_sd": float(np.std(loos, ddof=1)),
                      "loo_max": float(np.max(loos)), "loo_all": loos,
                      "rp_mean": float(np.mean(rps)), "rp_sd": float(np.std(rps, ddof=1)),
                      "rp_max": float(np.max(rps)), "rp_all": rps,
                      "switch_mean": float(np.mean(nsw)), "switch_max": float(np.max(nsw))}
        print(f"  {mode:<16}{ctrl[mode]['loo_mean']:>+12.1f} ± {ctrl[mode]['loo_sd']:<6.1f}"
              f"{ctrl[mode]['loo_max']:>+10.1f}"
              f"{ctrl[mode]['rp_mean']:>+14.3f} ± {ctrl[mode]['rp_sd']:<6.3f}"
              f"{ctrl[mode]['rp_max']:>+9.3f}{ctrl[mode]['switch_mean']:>18.1f}")
    if K > 0:
        z_loo = ((real["loo"] - ctrl["bucket_hand"]["loo_mean"]) / ctrl["bucket_hand"]["loo_sd"]
                 if ctrl["bucket_hand"]["loo_sd"] > 0 else float("nan"))
        z_rp = ((float(np.nanmean(real["rp_loo"])) - ctrl["bucket_hand"]["rp_mean"])
                / ctrl["bucket_hand"]["rp_sd"] if ctrl["bucket_hand"]["rp_sd"] > 0 else float("nan"))
        print(f"  ⇒ 真实数据：留一 {real['loo']:+.1f} 点/小局 ⇒ 相对 `bucket_hand` 零分布 "
              f"z={z_loo:+.2f}（该零分布最大 {ctrl['bucket_hand']['loo_max']:+.1f}）")
        print(f"  ⇒ 真实数据：顺位点 {float(np.nanmean(real['rp_loo'])):+.3f} ⇒ z={z_rp:+.2f}"
              f"（零分布最大 {ctrl['bucket_hand']['rp_max']:+.3f}）")
        print(f"  ⇒ CI 规则：真实 {len(switchable)} 个可切换桶 vs 纯噪声下平均 "
              f"{ctrl['bucket_hand']['switch_mean']:.1f} 个（最多 "
              f"{ctrl['bucket_hand']['switch_max']:.0f} 个）"
              " —— 两者同量级就说明这个清单**基本是假阳**")

    out = {"dirs": [w["dir"] for w in walls], "n_walls": n_walls, "n_games": pack["n_games"],
           "n_hands": int(n_hands), "min_per_expert": pack["min_per_expert"],
           "n_buckets": nb,
           "heads_per_game": hpg, "rank_point_formula_residual": worst,
           "merged_sizes": pack["merged_sizes"], "labels_of_bucket": pack["labels_of_bucket"],
           "dec_by_bucket": {str(k): v for k, v in pack["dec_by_bucket"].items()},
           "in_sample_pts_per_kyoku": real["in_sample"], "loo_pts_per_kyoku": real["loo"],
           "loo_per_wall": real["loo_per_wall"], "ci_rule_pts_per_kyoku": ci_gain,
           "switchable_share": share_switch, "rp": rp, "control": ctrl,
           "switchable": [{"bucket": pack["labels_of_bucket"][b], "expert": EXPERTS[e][0],
                           "pts_per_kyoku": s["mean"], "share": float(real["share"][b]),
                           "contrib_rp_per_game": float(real["share"][b]) * s["mean"] * hpg / 1000.0,
                           "wall_pos": s["wall_pos"], "wall_n": s["wall_n"], "p": s["p"],
                           "ci_re": list(s["ci_re"])} for b, e, s in switchable]}
    if args.out:
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
