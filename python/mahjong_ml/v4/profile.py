"""**模型画像**（model profile）：给定**已有的**自对弈产物，对每个策略输出一张多维统计表。

判据是**对拍**，不是"看着像"：工具算出的 `和率 / 铳率 / 平均打点 / 平均顺位点` 必须与
**生产者自己** `summary.json` 的 `by_policy` 同名数字一致（`--cross-check`）。对不上就是工具错。

## 为什么需要它（这一步不做，后面的"挑专家"全是盲选）

闸门（`v4/gate.py`）只回答一个问题：**综合起来谁更强**（顺位点配对 CI）。
它答不了"强在哪 / 弱在哪"——挑专家模型（多线训练 / 场景化多权重）要的是**分轴画像**：
`v4-league17-g24` 可能与现任 `g08` 打平（闸门 Δ=+0.95，CI 跨 0），但它的**打点**明显更高、
**铳率**明显更低 —— 那它就是"高打点专家"，值得单独留一条线。只看闸门会把这种模型直接丢掉。

## 轴与口径（**每个数都要说得出分母**）

| 块 | 轴 | 分母 / 口径 | 来源 |
| --- | --- | --- | --- |
| 完场 | 平均顺位 / 平均顺位点 / 得点 | 场（`placement` / `rank_points` / `final_scores`） | `summary.json` |
| 完场 | 和率 / 铳率 / 平均收支 / 平均打点 | **座位·小局**（`seat_hands`，与生产者同分母） | 轨迹 `hand` 行 |
| 和了 | 平均净打点（去本场棒/立直棒） | 和了的**座位·小局** | 轨迹 `hand` 行（点数反推，见下） |
| 和了 | 自摸率 / 平均和了巡目 | 和了 / 自家摸牌数（`player_draws` 那把尺子） | 轨迹 `decision` 行 |
| 和了 | 満貫以上率 / 跳満 / 倍満 / 三倍満 / 役満以上率 | 和了（**按实际收到的点数判档**） | 轨迹 `hand` 行 |
| 役 | 平均役种数 / 复合役率 / 平均番数 / 役种频次 Top-N | **有役种数据的和了手** | 轨迹 `hand` 行的 `yaku` |
| 役 | 真·役满率（`yakuman>0`） / 満貫以上率（按 `limit`） | 同上（**役种表口径**，不是点数近似） | 轨迹 `hand` 行的 `yakuman`/`limit` |
| 放铳 | 平均铳点 | 放铳（荣和那一手） | 轨迹 `hand` 行 |
| 放铳 | 被自摸率 / 被自摸失点 | 座位·小局 / 被自摸的那几手 | 轨迹 `hand` 行 |
| 行为 | 立直率 / 副露率 / 暗杠率 | **座位·小局**（与 `seat_hands` 同分母） | 轨迹 `decision` 行 |
| 行为 | 追立率 | 立直宣言 | 轨迹 `decision` 行 |
| 一致性 | 与现任同场的 `judge_pair` 读数 | 逐场配对（**复用 `v4/arena.judge_pair`**），**配对域 = 同一个 run**（见下） | `summary.json` |

### 配对域：**同一个 run 之内**（2026-10 修正，此前是静默错值）

`per_game[i].seed` 是**墙 seed**，只由 `(seed_base, 场次下标)` 决定 —— 实测
`gate/p1-17g24/s20261020` 与 `gate/p1-10g01/s20261020` 的 `game0.seed` **完全相同**。
而"跨模型可比"的**前提**正是"共用同一批 seed" ⇒ 只按 seed 存逐场序列就是**后写覆盖**，
**现任的序列会被最后一个 run 覆盖**，于是"候选在墙 i 的值"被拿去和"最后一个 run 里现任对
**别的候选**在同一墙 i 的值"配对。数字看起来正常、事实错误（实测 17g24 的真值
`rank_points −4.152` 被读成 `−8.067`）。所以：

- 逐场序列的键 = **`(run 序号, 墙 seed)`**（`PolicyStat.add_series`）；
- `eval.paired_test` 取交集 ⇒ **配对只在同一个 run 内成立**（该 run 里候选与现任同场同墙）；
- 这**就是** `gate.pooled_diffs` 那套"**加键偏移 + 池化**"的同一个构造（偏移落在元组第一格），
  统计量仍是 `eval.paired_test`（经 `arena.judge_pair`）—— **没有第二份统计实现**；
- ⛔ 本模块**不做**跨 run 的台面合并（那件事归 `v4.gate`，它按 `--seed` 逐套墙判决）。

### 打点怎么"反推"（不依赖任何新字段）

`hand` 行的**打点**没有直接给，但四家收支 `delta` 是精确的，于是打点可**反解**：

- 荣和：`delta[loser] = −(打点 + 300 × 本场棒)` ⇒ `打点 = −delta[loser] − 300 × honba`；
- 自摸：`Σ_{非和了家} delta = −(打点合计 + 300 × 本场棒)` ⇒ 同理；
- 立直棒**不进**这个式子（宣言时 −1000、和了时 +1000×n，两边抵掉）—— 所以反解**不需要**知道
  桌上有几根棒。附带一条可断言的不变式：`Σ delta ≡ 0 (mod 1000)`
  （= `1000 × (本小局收棒数 − 本小局立直宣言数)`）。
- 分档按**实际收到的点数**：子 満貫 8000 / 亲 12000，跳満 ×1.5、倍満 ×2、三倍満 ×3、役満 ×4
  ⇒ 切り上げ満貫与累计役満**自动落对档**（不需要知道番符）。
- ⚠ 它**替代不了**役种表口径的两根轴（`真·役满率` / `満貫以上率（按 limit）`）：
  点数近似会把数え役満算进"役満以上"，也会把"役满但只收到 <32000"（包牌/供托等）漏掉。
  两个口径都留着，轴名里点名了是哪一个。

### 庄家怎么来（`round` 里**没有** `dealer`）

`hand` 行的 `round` 子对象只有 `bakaze / kyoku / honba / riichi_sticks`（PROTOCOL §8.4），
没有庄家座次 —— 而 `连庄率` 与**打点分档（亲 12000 / 子 8000）**都要它。所以：

- **报文里有 `dealer` 就以它为准**（引擎真值，权威）；
- 没有才**派生** `dealer = (kyoku − 1) % 4`（依据：每个风位恰好 4 个小局、引擎里
  `kyoku = dealer + 1`；**连庄只加 `honba`、不动 `kyoku`/风位/庄家** ⇒ 连庄天然对）。
  口径与 `v4/harness.py` 同一套。看不清的（`kyoku ∉ 1..4`）返回 `-1` = 未知，照旧画 `—`。

### 役种轴（2026-10 起）：数据来自 `hand` 行的 `yaku` / `han` / `fu` / `yakuman` / `limit`

字段表与"何时缺省"见 `docs/PROTOCOL.md` §8.4；口径细节见 `docs/TRAINING-V4.md` §15.5。
本模块**不重算役**（那一份是引擎已经算过的 `Round.Result.winScore`），只做归约与显示。

### ⛔ 取不到的轴（要动契约；清单见 `docs/TRAINING-V4.md`「模型画像」一节）

只剩一根：`默听率` —— 观察里没有"自家是否听牌"这个字段，而"没立直" ≠ "默听"。
本模块**不猜**：这些轴一律标 `—`（`None`），并在表下点名缺哪几个字段。

⚠ **老轨迹（2026-10 之前采的）没有 `yaku` 那五列** ⇒ 六根役种轴同样标 `—`（`None`），
⛔ 不许填 0 —— 判据是 `table["yaku_field"]`（一条 `hand` 行带 `yaku` 就算新格式）。

用法：

    python -m mahjong_ml.v4 profile --run <run 目录> [--run ...] [--cross-check] [--incumbent <net.bin>]
    python -m mahjong_ml.v4 profile --self-check

`--run` 给"含 `summary.json` + `g*.jsonl` 的目录"，或它们的父目录（如 `<数据根>/gate/<tag>`，
自动取里面的 `s<seed>/`）。
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# ⚠ 逐场序列的 `rank_points` / `place` / `score` 三个口径**直接复用 `mahjong_ml.eval`**
#   （`load_run` + `per_game_series`）—— 那是全仓唯一的"per_game → seed 索引序列"实现，
#   这里**不另写第二份**（`eval.METRICS` 就是那把尺子）。
from mahjong_ml import eval as ml_eval

from . import arena

#: 打点分档（升序）；阈值 = `満貫 × 倍数`，満貫阈值随亲/子不同。
TIERS = ("満貫未満", "満貫", "跳満", "倍満", "三倍満", "役満以上")
#: 満貫阈值：子 8000 / 亲 12000（跳満 ×1.5、倍満 ×2、三倍満 ×3、役満 ×4）
MANGAN = {False: 8000, True: 12000}
#: `avg_rank_points` 的对拍容差：生产者累加的是**未取整**的顺位点，而 `per_game` 里是
#: **按 0.1 分取整**过的值 ⇒ 重算会有 1e-2 量级的差。与 `tools/selfplay-check.mjs` 第 ③ 条同容差。
RP_TOL = 0.05
#: 逐场序列的键（给 `judge_pair`）—— `rank_points`/`place`/`score` 是 `eval.METRICS` 的三个口径，
#: 后三个是"从轨迹重算的每场率"（`summary.json` 里没有逐场值）。
SERIES_KEYS = ("rank_points", "place", "score", "win_rate", "deal_in_rate", "avg_win_score")


# ================================================================= 纯函数
# 这一段**无副作用**：计数 / 率 / 均值 / 分位 / 打点反解 / 分档。自检只喂它们。

def safe_div(num: float, den: float) -> float:
    """`num/den`；分母为 0 ⇒ `0.0`（**不是** NaN —— 表里要打印得出来）。"""
    return 0.0 if not den else num / den


def mean(xs: Iterable[float]) -> float:
    """算术均值；空输入 ⇒ `0.0`。"""
    ys = list(xs)
    return safe_div(sum(ys), len(ys))


def quantile(xs: Iterable[float], q: float) -> float:
    """线性插值分位（`numpy.percentile` 的 `linear` 法）；空输入 ⇒ `nan`。"""
    ys = sorted(float(x) for x in xs)
    if not ys:
        return float("nan")
    if len(ys) == 1:
        return ys[0]
    pos = (len(ys) - 1) * min(max(float(q), 0.0), 1.0)
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ys[int(pos)]
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def placement_from_scores(scores: Sequence[int]) -> list[int]:
    """终局分数 → 顺位（1 = 最高分）。

    **同点时按座次先后**拆开（M.League 起家优先）—— 与生产者 `SelfPlay.placementOf` 逐字同口径，
    否则"平均顺位"会被同点挤掉一个名次。只在 `summary.json` 缺席时当回退（有 summary 一律以它为准）。
    """
    order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    place = [0] * len(scores)
    for rank, i in enumerate(order):
        place[i] = rank + 1
    return place


def placement_dist(places: Iterable[int]) -> list[int]:
    """顺位分布：下标 0..3 对应 1 位..4 位（越界 / 非整数一律忽略，不抛）。"""
    out = [0, 0, 0, 0]
    for p in places:
        if isinstance(p, int) and 1 <= p <= 4:
            out[p - 1] += 1
    return out


def base_points(hand: "HandRow") -> int | None:
    """本小局的**实际打点**（点，不含本场棒与回收的立直棒）；非和了 ⇒ `None`。

    反解见模块 docstring。⚠ 荣和用 `delta[loser]`、自摸用三家合计 —— 两者都先减掉 `300 × honba`
    （本场棒由放铳者付 300 或三家各付 100，合计都是 300）。
    """
    if not hand.agari or not (0 <= hand.winner < 4):
        return None
    if hand.tsumo:
        paid = -sum(hand.delta[i] for i in range(4) if i != hand.winner)
    else:
        if not (0 <= hand.loser < 4):
            return None
        paid = -hand.delta[hand.loser]
    return paid - 300 * hand.honba


def score_tier(points: int, dealer: bool) -> str:
    """打点 → 档名（`TIERS` 之一）。按**实际收到的点数**判 ⇒ 切り上げ満貫 / 累计役満自动落对档。"""
    thr = MANGAN[bool(dealer)]
    if points >= thr * 4:
        return "役満以上"
    if points >= thr * 3:
        return "三倍満"
    if points >= thr * 2:
        return "倍満"
    if points >= thr * 3 // 2:
        return "跳満"
    if points >= thr:
        return "満貫"
    return "満貫未満"


def sticks_delta(hand: "HandRow") -> int:
    """`Σ delta / 1000` = `本小局收棒数 − 本小局立直宣言数`（立直棒的进出账）。

    和了一手恒 ≥ 0（收回桌上原有的棒）；流局恒 ≤ 0（本局新放的棒留在桌上）。纯函数。
    """
    return sum(hand.delta) // 1000


#: 満貫及以上的**打点档码**（`hand.limit` 的值域里除 `""` 之外的全部；PROTOCOL §8.4）。
#: ⚠ 与"按实收点数判档"是**两个量**（`kazoe_yakuman` 在点数口径里落在"役満以上"，
#: 而在役种口径里根本不是役满 —— 见 `axes_of` 的两根轴）。
MANGAN_PLUS_LIMITS = ("mangan", "haneman", "baiman", "sanbaiman", "kazoe_yakuman", "yakuman")


def yaku_top_n(freq: Mapping[str, int], total: int, n: int = 5) -> str:
    """役种频次 Top-N → **一格文本**（"役种 率%(次数) / …"）；纯函数，并列按名字定序。

    分母是"有役种数据的和了手"（不是决策数）—— `total` 由调用方传，免得这个函数自己
    去猜分母。⚠ 参数化役种在这里显示成 `code:tile`（`yakuhai:5z` 就是"役牌 白"）：
    "役牌 白"与"役牌 中"在麻将里是两个役，折成一个 `yakuhai` 会把它们混掉。
    """
    if total <= 0 or not freq:
        return ""
    items = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:max(1, n)]
    return " / ".join(f"{k} {100 * v / total:.0f}%({v})" for k, v in items)


# ================================================================= 行模型

@dataclass(frozen=True)
class HandRow:
    """一条 `hand` 记录里画像用得上的部分（**含**役种轴五列 —— 2026-10 起才有）。"""

    game: int
    hand_no: int
    round_wind: str
    kyoku: int
    honba: int
    #: 庄家座次。**报文没这个字段** ⇒ 由 `dealer_of()` 按 `(kyoku − 1) % 4` 派生
    #: （连庄只加 honba、不改 kyoku，所以派生对连庄成立）；`-1` = 未知（画 `—`，不猜）。
    dealer: int
    sticks_after: int
    delta: tuple[int, int, int, int]
    agari: bool
    abortive: bool
    reason: str
    renchan: bool
    winner: int
    loser: int
    tsumo: bool
    nagashi: bool
    tenpai: tuple[bool, ...]
    policies: tuple[str, ...]
    #: 和了者那一手的役列表：`(code, tile|None, han, yakuman)`（键序与 `agari` 报文同源）。
    #: ⚠ **`None` 表示"这条轨迹没有这五列"**（2026-10 之前采的老轨迹）—— 与"这一小局没有
    #: 和了者"共用同一个 `None`：两者对役种轴都是"取不到"，分母只数真的有役种数据的和了手。
    yaku: tuple[tuple[str, str | None, int, int], ...] | None = None
    han: int | None = None
    fu: int | None = None
    yakuman: int | None = None
    limit: str | None = None

    @property
    def is_nagashi(self) -> bool:
        return bool(self.agari and self.nagashi)

    @property
    def yaku_names(self) -> tuple[str, ...]:
        """役种名（参数化役种写成 `code:tile`）—— 与 `yaku_top_n` 的键同一套写法。"""
        return tuple(f"{c}:{t}" if t else c for c, t, _h, _y in (self.yaku or ()))


@dataclass(frozen=True)
class DecisionRow:
    """一条 `decision` 记录里画像用得上的部分（**行为轴**的来源）。"""

    game: int
    hand_no: int
    seat: int
    policy: str
    kind: str                      # turn / claim
    chosen: str                    # 动作键（§8.3 文法）
    player_draws: int | None
    riichi_others: bool            # 这一条是立直宣言时：是否已有他家立直（追立判据）
    ron_turn: int | None           # 这一条是荣和时：打出那张的舍主当年的摸牌数

    @property
    def action(self) -> str:
        return self.chosen.split(":", 1)[0]

    @property
    def kan_kind(self) -> str | None:
        parts = self.chosen.split(":")
        return parts[1] if len(parts) > 1 and parts[0] == "kan" else None

    @property
    def is_open_call(self) -> bool:
        """副露（吃 / 碰 / 大明杠 / 加杠）—— **暗杠不算副露**。"""
        if self.action in ("chi", "pon"):
            return True
        return self.action == "kan" and self.kan_kind in ("daiminkan", "kakan")


@dataclass
class RunData:
    """一个自对弈 run（= 一个含 `summary.json` + `g*.jsonl` 的目录）。"""

    dir: Path
    per_game: list[dict] = field(default_factory=list)
    by_policy: dict = field(default_factory=dict)
    game_rows: list[dict] = field(default_factory=list)
    hands: list[HandRow] = field(default_factory=list)
    decisions: list[DecisionRow] | None = None
    sampled_every: int = 1
    trace_note: str = ""
    seeds: list[int] = field(default_factory=list)
    #: `mahjong_ml.eval.Run`（有 `summary.json` 且它带 `per_game.rank_points` 时才有）
    #: —— 逐场序列的 `rank_points`/`place`/`score` 走它，**不另写第二份**。
    eval_run: Any = None

    @property
    def name(self) -> str:
        return self.dir.name or str(self.dir)


# ================================================================= 读盘

def dealer_of(kyoku: int, honba: int, has_field: bool = False, field: Any = None) -> int:
    """庄家座次：**轨迹里有 `dealer` 就用它（权威），没有才派生**。**纯函数**。

    ⚠ 为什么会有"派生"这一支：这份轨迹的 `round` 子对象**只有**
    `bakaze / kyoku / honba / riichi_sticks`（PROTOCOL §8.4；两个生产者逐字节对拍钉住），
    **没有 `dealer`**。而 `连庄率`（本小局为亲且 `renchan`）与打点分档（亲 12000 / 子 8000）
    **都**要它 —— 缺了就会"连庄率恒 `—`、并把亲家的 12000 当成跳満"（实测踩过）。

    派生依据（三条，缺一条这个式子就不成立）：
      ① **每个风位恰好 4 个小局**（东/南/西各 1~4），引擎里 `dealer` 恒 ≤ 3 且
         `kyoku = dealer + 1`（`Table.playGame`：非连庄时 `nd = (dealer+1) % 4`，
         `nd == 0` 换风且 `kyoku = 1`，否则 `kyoku = nd + 1`）⇒ `dealer = (kyoku − 1) % 4`
         对**东/南/西任一风**都成立（南 1 的庄家仍是 0 号，不是"接着东 4 往后数"）；
      ② **连庄不改 `kyoku`**（连庄只 `honba += 1`，风位/`kyoku`/庄家三样都不动）
         ⇒ 同一个庄家连续几把手牌都用同一个 `kyoku`，派生天然对连庄成立
         （所以 `honba` 不参与这个式子，它只是把"同一庄的第几把"记下来）；
      ③ 口径与 `v4/harness.py`（`dealer_seat = (kyoku - 1) % 4`）**同一套** —— 不另立第二把尺子。

    ⚠ **权威边界**：将来报文里真带上 `dealer` 时，**以报文里的为准**（那是引擎的真值，
    能覆盖"某天风位不再是 4 个小局"这类规则改动）；派生值只是**缺字段时的兜底**，
    并且 `kyoku ∉ 1..4`（形状不对/老数据缺键）时返回 `-1`（= 未知，下游照旧画 `—`，不猜）。
    """
    if has_field and isinstance(field, int) and not isinstance(field, bool):
        return int(field)
    if 1 <= int(kyoku) <= 4:
        return (int(kyoku) - 1) % 4
    return -1


def parse_hand_row(o: Mapping[str, Any], policies: tuple[str, ...]) -> HandRow:
    """一条 `hand` JSON 对象 → `HandRow`。**纯函数**（自检直接喂合成对象，不读盘）。

    役种轴（2026-10 起，PROTOCOL §8.4）在这里的判据是 **"键在不在"**，不是"值是不是 0"：
    整块缺席（流局 / 途中流局 / 老轨迹）⇒ 五列一律 `None` —— 下游据此画 `—` 而不是 `0.00`。

    庄家见 `dealer_of`：**报文有 `dealer` 就用它，没有才按 `(kyoku − 1) % 4` 派生**。
    """
    rnd = o.get("round") or {}
    # ⚠ 只要有 `yaku` 且是**非空**列表才算"有役种数据"：空列表在报文里不合法（和了一定有役），
    #   真拿到时按缺席处理比按"0 役"处理安全（后者会把一只手算进分母、把平均番数拉低）。
    yk = o.get("yaku")
    yaku = (tuple((str(y.get("code", "")), y.get("tile"), int(y.get("han", 0)),
                   int(y.get("yakuman", 0))) for y in yk)
            if isinstance(yk, list) and yk else None)
    kyoku = int(rnd.get("kyoku", 0))
    honba = int(rnd.get("honba", 0))
    return HandRow(
        game=int(o.get("game", -1)), hand_no=int(o.get("hand_no", -1)),
        round_wind=str(rnd.get("bakaze", "")), kyoku=kyoku,
        honba=honba,
        dealer=dealer_of(kyoku, honba, has_field="dealer" in rnd, field=rnd.get("dealer")),
        sticks_after=int(rnd.get("riichi_sticks", 0)),
        delta=tuple(int(x) for x in o.get("delta", [0, 0, 0, 0])),
        agari=bool(o.get("agari")), abortive=bool(o.get("abortive")),
        reason=str(o.get("reason", "")), renchan=bool(o.get("renchan")),
        winner=int(o.get("winner", -1)), loser=int(o.get("loser", -1)),
        tsumo=bool(o.get("tsumo")), nagashi=bool(o.get("nagashi")),
        tenpai=tuple(bool(x) for x in (o.get("tenpai") or [])),
        policies=tuple(policies),
        yaku=yaku,
        han=int(o["han"]) if "han" in o else None,
        fu=int(o["fu"]) if "fu" in o else None,
        yakuman=int(o["yakuman"]) if "yakuman" in o else None,
        limit=str(o["limit"]) if "limit" in o else None)


def _load_jsonl(path: Path, policies_by_game: dict[int, tuple]
                ) -> tuple[list[dict], list[HandRow], list[DecisionRow]]:
    """读一个 `g*.jsonl`：返回 `(game 行, hand 行, decision 行)`。

    ⚠ 逐行 `json.loads`（不抽样）—— `obs` 占了体积的绝大部分，但行为轴（立直 / 追立 / 和了巡目）
    全在里面，省不掉。
    """
    games: list[dict] = []
    hands: list[HandRow] = []
    decisions: list[DecisionRow] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if '"type"' not in line:
                continue
            o = json.loads(line)
            t = o.get("type")
            if t == "game":
                games.append(o)
            elif t == "hand":
                hands.append(parse_hand_row(o, tuple(policies_by_game.get(int(o.get("game", -1)), ()))))
            elif t == "decision":
                obs = o.get("obs") or {}
                chosen = str(o.get("chosen", ""))
                seat = int(o.get("seat", -1))
                riichi_others = False
                if chosen.startswith("riichi:"):
                    r = obs.get("riichi") or []
                    riichi_others = any(bool(x) for j, x in enumerate(r) if j != seat)
                ron_turn = None
                if chosen == "ron":
                    ev = obs.get("events") or []
                    frm = obs.get("from")
                    if ev and ev[-1].get("type") == "discard" and ev[-1].get("actor") == frm:
                        ron_turn = int(ev[-1].get("turn", 0))
                pd = obs.get("player_draws")
                decisions.append(DecisionRow(
                    game=int(o.get("game", -1)), hand_no=int(o.get("hand_no", -1)),
                    seat=seat, policy=str(o.get("policy", "")), kind=str(o.get("kind", "")),
                    chosen=chosen, player_draws=int(pd) if isinstance(pd, int) else None,
                    riichi_others=riichi_others, ron_turn=ron_turn))
    return games, hands, decisions


def load_run_data(dirpath: Path, *, with_decisions: bool = True,
                  limit_files: int | None = None) -> RunData:
    """读一个 run 目录：`summary.json`（可选）+ 全部 `g*.jsonl`。"""
    rd = RunData(dir=dirpath)
    sp = dirpath / "summary.json"
    if sp.is_file():
        raw = json.loads(sp.read_text(encoding="utf-8"))
        rd.per_game = list(raw.get("per_game") or [])
        rd.by_policy = dict(raw.get("by_policy") or {})
        try:
            # 复用 `eval.load_run`：它顺带**校验** `per_game` 里有没有 `rank_points`
            # （没有就抛 ⇒ 这里退回原始 json，只把顺位点那一轴标成"缺席"而不是整个 run 失败）
            rd.eval_run = ml_eval.load_run(dirpath)
        except (ValueError, FileNotFoundError):
            rd.eval_run = None
    pol_by_game: dict[int, tuple] = {}
    for g in rd.per_game:
        pol_by_game[int(g["game"])] = tuple(g.get("policies") or ())

    def _num(p: Path) -> int:
        s = p.stem[1:]
        return int(s) if s.isdigit() else 0

    files = sorted(dirpath.glob("g*.jsonl"), key=_num)
    if limit_files:
        files = files[:limit_files]
        # ⚠ 只读一部分轨迹时，`per_game` **也必须跟着裁到同一批场次** —— 否则表里会出现
        #   "完场轴来自 1500 场、和了轴来自 20 场"的**混合画像**（读起来像一致的数据，其实不是）。
        #   裁掉之后对拍仍然会红（分母与 summary 对不上），但那正是 `--limit-files` 的用途：
        #   冒烟看管线通不通。`eval_run` 也要丢掉（它的 `per_game` 是全量的）。
        keep_games = {_num(p) for p in files}
        rd.per_game = [g for g in rd.per_game if int(g["game"]) in keep_games]
        rd.eval_run = None
    all_games: list[dict] = []
    all_hands: list[HandRow] = []
    all_dec: list[DecisionRow] = []
    for p in files:
        g_rows, h_rows, d_rows = _load_jsonl(p, pol_by_game)
        all_games += g_rows
        all_hands += h_rows
        all_dec += d_rows
    for g in all_games:                      # 轨迹里的 `game` 行更权威（带 start_score）
        pol_by_game.setdefault(int(g["game"]), tuple(g.get("policies") or ()))
    if any(not h.policies for h in all_hands):
        # summary.json 缺席时标签只能从 `game` 行补（那行在文件**末尾**，所以先读完再补）
        all_hands = [h if h.policies else replace(h, policies=tuple(pol_by_game.get(h.game, ())))
                     for h in all_hands]
    if not rd.per_game:
        for g in all_games:
            rd.per_game.append({"game": int(g["game"]), "seed": g.get("seed"),
                                "policies": list(g.get("policies") or ()),
                                "final_scores": g.get("final_scores"),
                                "placement": g.get("placement"),
                                "rank_points": None,
                                "hands": g.get("hands"), "ryukyoku": None})
    if all_games:
        rd.sampled_every = int(all_games[0].get("sampled_every", 1) or 1)
    rd.game_rows = all_games
    rd.hands = all_hands
    rd.seeds = sorted({int(g["seed"]) for g in rd.per_game if g.get("seed") is not None})
    if not files:
        rd.trace_note = "没有 g*.jsonl"
        rd.decisions = None
    elif not with_decisions:
        rd.trace_note = "--no-decisions：跳过决策行 ⇒ 行为轴缺席"
        rd.decisions = None
    elif rd.sampled_every != 1:
        # ⚠ 抽样过的轨迹**不能**算行为率（分母不再等于局数）—— 显式判红，不悄悄给个数
        rd.trace_note = f"sampled_every={rd.sampled_every} ≠ 1 ⇒ 行为轴缺席（抽样轨迹算不出率）"
        rd.decisions = None
    else:
        rd.decisions = all_dec
    if limit_files:
        rd.trace_note = (rd.trace_note + "；" if rd.trace_note else "") + \
            (f"--limit-files={limit_files} ⇒ **只读了前 {limit_files} 场**"
             f"（`per_game` 已同步裁到同一批场次；对拍仍会红 —— 那是本开关的用途：只给冒烟）")
    return rd


def discover_runs(p: Path) -> list[Path]:
    """把 `--run` 的取值展开成 run 目录列表。

    给"含 `summary.json` / `g*.jsonl` 的目录"就返回它自己；给父目录（如 `gate/<tag>`）
    就返回里面**每个**子目录（`s<seed>/`）。
    """
    if (p / "summary.json").is_file() or any(p.glob("g*.jsonl")):
        return [p]
    subs = [d for d in sorted(p.iterdir()) if d.is_dir()
            and ((d / "summary.json").is_file() or any(d.glob("g*.jsonl")))] \
        if p.is_dir() else []
    return subs or [p]


# ================================================================= 牌山可比性（**逐 run** 判据）

@dataclass(frozen=True)
class WallCheck:
    """一个 run 的「墙对齐」体检（`problem == ""` = 对齐）。"""

    run_index: int
    name: str
    labels: int          # 这个 run 里出现过的策略数
    walls: int           # 这个 run 里各策略共同覆盖的墙 seed 数
    problem: str = ""


def check_walls(runs: Sequence[RunData]) -> list[WallCheck]:
    """**逐 run** 体检「各策略是不是打了同一批墙 seed」—— 可比性的正确判据。**纯函数**。

    ⚠ 原来的判据是 `all(set(rd.seeds) == set(runs[0].seeds))`（**跨 run** 比 seed 集合），
    它在 gate 场景下**恒为假警报**：`gate/<tag>` 展开出的 8 个 `s<seed>/` 本来就各是一套**不同的墙**，
    seed 集合**天然不同** —— 而"不同"根本不是错误（那 8 套墙要被池化才对）。
    真正要问的是："**同一个 run 内部**，各策略打的是不是同一批墙"（同一个 run 里 X 与现任必须同场同墙，
    配对才成立）；跨 run 的墙本来就不同，配对**不跨 run**（见 `add_series`）。

    返回每个 run 一条 `WallCheck`；`problem` 非空 = 这个 run 的配对会漏场 / 张冠李戴，逐条点名。
    """
    out: list[WallCheck] = []
    for i, rd in enumerate(runs):
        cov: dict[str, set] = {}
        bad_rows = 0
        for g in rd.per_game:
            sd = g.get("seed")
            if sd is None:
                continue
            pols = g.get("policies") or ()
            if len(pols) != 4:
                # ⚠ `reduce_run` 里有一条 `if len(labels) != 4: continue` —— 这种场会被**静默丢掉**，
                #   而"丢掉几场"在多 run 合并后根本看不出来 ⇒ 在这里点名。
                bad_rows += 1
                continue
            for lab in pols:
                cov.setdefault(str(lab), set()).add(int(sd))
        name = str(rd.dir)
        if not cov:
            out.append(WallCheck(i, name, 0, len(rd.seeds),
                                 "读不到 `per_game` 的墙 seed（这个 run 没有可配对的场次）"))
            continue
        ref = max(cov.values(), key=len)
        probs: list[str] = []
        miss = [(lab, len(v)) for lab, v in sorted(cov.items()) if v != ref]
        if miss:
            probs.append("；".join(f"{short_label(lab, 20)} 只打了 {n}/{len(ref)} 副墙"
                                   for lab, n in miss))
        if bad_rows:
            probs.append(f"{bad_rows} 场没有 4 个策略标签（`reduce_run` 会把它们静默丢掉）")
        out.append(WallCheck(i, name, len(cov), len(ref), "；".join(probs)))
    return out


def fmt_run_ids(ids: Iterable[int]) -> str:
    """run 序号 → 压缩写法（`r0-7` / `r0,3-5`）—— "这次配对用了哪几个 run"一眼可见。**纯函数**。"""
    xs = sorted({int(i) for i in ids})
    if not xs:
        return "—"
    parts: list[str] = []
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[j] + 1:
            j += 1
        parts.append(f"r{xs[i]}" if j == i else f"r{xs[i]}-{xs[j]}")
        i = j + 1
    return ",".join(parts)


# ================================================================= 归约

@dataclass
class PolicyStat:
    label: str
    games: int = 0
    seat_hands: int = 0
    place_counts: list = field(default_factory=lambda: [0, 0, 0, 0])
    rank_point_sum: float = 0.0
    final_score_sum: int = 0
    start_score_sum: int = 0
    delta_sum: int = 0
    wins: int = 0
    tsumo_wins: int = 0
    deal_ins: int = 0
    win_score_sum: int = 0
    win_base_sum: int = 0
    win_base_n: int = 0
    tier_counts: dict = field(default_factory=dict)
    deal_in_sum: int = 0
    deal_in_n: int = 0
    tsumo_against_n: int = 0
    tsumo_loss_sum: int = 0
    dealer_hands: int = 0
    dealer_renchan: int = 0
    decisions: int = 0
    turn_decisions: int = 0
    seat_hands_with_turn: int = 0
    riichi: int = 0
    riichi_chase: int = 0
    open_call_hands: int = 0
    ankan_hands: int = 0
    win_turns: list = field(default_factory=list)
    # ---- 役种轴（2026-10 起；分母 = **真的有役种数据的和了手**，老轨迹 ⇒ 0 ⇒ 轴画 `—`）----
    yaku_hands: int = 0
    yaku_count_sum: int = 0          # 役种数（= 役列表长度）之和
    compound_wins: int = 0           # 役种数 ≥ 2 的和了手
    han_sum: int = 0
    yakuman_wins: int = 0            # `yakuman > 0`（**真·役满**，数え役満不算）
    limit_counts: dict = field(default_factory=dict)
    yaku_freq: dict = field(default_factory=dict)
    #: 逐场序列：`度量 → {(run 序号, 墙 seed): 值}`。⚠ **键里必须有 run**（见 `add_series`）——
    #: 只按 seed 存会被后一个 run 覆盖，而"跨模型可比"的前提恰恰是各 run 共用同一批 seed。
    series: dict = field(default_factory=dict)

    def add_series(self, key: str, run_index: int, seed: int, value: float) -> None:
        """记一条逐场值：**键 = `(run 序号, 墙 seed)`**。

        ⚠ 为什么键里必须有 run（2026-10 修正的**静默错值**）：`per_game[i].seed` 是墙 seed，只由
        `(seed_base, 场次下标)` 决定 —— 实测 `gate/p1-17g24/s20261020` 与
        `gate/p1-10g01/s20261020` 的 `game0.seed` **完全相同**。原来的写法是
        `series[度量][seed] = 值`（**后写覆盖**）⇒ 一次 `profile --run A --run B …`
        （跨模型可比的前提正是**共用同一批 seed**）时，**现任的逐场序列被最后一个 run 覆盖**，
        `--incumbent` 就把"X 在墙 i 的值"与"最后一个 run 里现任**对别的候选**在同一墙 i 的值"
        配成对 —— **数字看起来正常、事实错误**（实测 17g24 的 rank_points 真值 −4.152 被读成 −8.067）。

        加上 run 序号后，`eval.paired_test` 的 `set(a) & set(b)` 自然只在**同一个 run 内**相交；
        而"每个 run 的键各自加一段偏移再池化"正是 `v4/gate.pooled_diffs` 的同一个构造
        （只是偏移落在元组第一格），统计量依旧是 `eval.paired_test` —— **没有第二份统计实现**。
        """
        self.series.setdefault(key, {})[(int(run_index), int(seed))] = value


def reduce_run(runs: Sequence[RunData], label_filter: Sequence[str] | None = None
               ) -> tuple[dict[str, PolicyStat], dict]:
    """把若干 run 归约成「策略 → 画像」。**这是唯一的归约实现**（自检喂合成行也走它）。

    与生产者逐字同口径的几条（`--cross-check` 就对它们）：
    `seat_hands` / `wins` / `deal_ins`（`h.loser == i`）/ `delta_sum` / `win_score_sum` /
    `place_counts` / `rank_point_sum` —— 分母与累加顺序都照抄 `SelfPlay.PolicyStat`。
    """
    stats: dict[str, PolicyStat] = {}

    def st_of(label: str) -> PolicyStat:
        if label not in stats:
            stats[label] = PolicyStat(label=label)
        return stats[label]

    def keep(lab: str) -> bool:
        return label_filter is None or lab in label_filter

    table = {"hands": 0, "ryukyoku": 0, "abortive": 0, "nagashi": 0, "games": 0,
             "win_turn_covered": 0, "win_turn_extra": 0,
             # 役种轴的"这批数据到底有没有这几列"判据：只要有一条 `hand` 行带 `yaku`，
             # 这一批就是 2026-10 之后采的；`0` ⇒ 老轨迹 ⇒ 六根轴一律画 `—`（⛔ 不画 0.00）。
             "yaku_field": 0, "yaku_hands": 0,
             # 逐场序列的键里那个 **run 序号 → 目录** 的图例（`--incumbent` 表用它说明"这次配对
             # 用了哪几个 run"）。必须由这里给：`RunData.dir` 的名字会撞（`gate/<tag>` 展开出来的
             # 8 个子 run 都叫 `s<seed>`），只有**在 `runs` 里的下标**是唯一的。
             "run_names": [str(rd.dir) for rd in runs]}
    seen_riichi: set = set()
    seen_open: set = set()
    seen_ankan: set = set()
    seen_turn: set = set()

    for rd_i, rd in enumerate(runs):
        # ⚠ 跨 run 的 `game` 号会撞车 ⇒ 每套牌山给一段互不重叠的偏移（同 `gate.pooled_diffs` 的用意）。
        #   必须由**下标**算出来（不能写"循环末尾 `game_off += …`"：下面有一处 `continue`，
        #   漏掉自增就会让两套牌山的 `(game, hand_no, seat)` 撞在一起 —— 去重与巡目筛全错，
        #   而且**不报错**）。`rd_i` 是 `enumerate` 给的，`continue` 也带不走它。
        game_off = rd_i * 1_000_000
        per_game = {int(g["game"]): g for g in rd.per_game}
        game_rows = {int(g["game"]): g for g in rd.game_rows}
        start_score = 25000
        for g in rd.game_rows:
            if g.get("start_score") is not None:
                start_score = int(g["start_score"])
                break
        by_game_hands: dict[int, list[HandRow]] = {}
        for h in rd.hands:
            by_game_hands.setdefault(h.game, []).append(h)
        for gno in sorted(set(list(per_game) + list(game_rows) + list(by_game_hands))):
            pg = per_game.get(gno) or {}
            gr = game_rows.get(gno) or {}
            labels = tuple(pg.get("policies") or gr.get("policies") or ())
            if len(labels) != 4:
                continue
            seed = int(pg.get("seed") or gr.get("seed") or 0)
            final = list(pg.get("final_scores") or gr.get("final_scores") or [0, 0, 0, 0])
            place = pg.get("placement") or gr.get("placement")
            if place is None:
                place = placement_from_scores(final)
            rp = pg.get("rank_points")
            hands_here = by_game_hands.get(gno, [])
            table["games"] += 1
            table["hands"] += len(hands_here)
            for h in hands_here:
                if not h.agari:
                    table["ryukyoku"] += 1
                    if h.abortive:
                        table["abortive"] += 1
                elif h.nagashi:
                    table["nagashi"] += 1
                # 役种轴的**存在性**判据（与座位/策略无关，是这批数据的属性）
                if h.yaku is not None:
                    table["yaku_field"] += 1
            # ---- ① 逐场：场数 / 顺位 / 顺位点 / 终局得点 / 逐场序列 ----
            acc: dict[str, dict] = {}
            for i in range(4):
                lab = labels[i]
                if not keep(lab):
                    continue
                s = st_of(lab)
                s.games += 1
                s.place_counts[int(place[i]) - 1] += 1
                s.final_score_sum += int(final[i])
                s.start_score_sum += start_score
                if rp is not None:
                    s.rank_point_sum += float(rp[i])
                a = acc.setdefault(lab, {"n": 0.0, "rp": 0.0, "place": 0.0, "score": 0.0, "sh": 0.0,
                                         "wins": 0.0, "di": 0.0, "ws": 0.0, "wn": 0.0})
                a["n"] += 1
                a["rp"] += float(rp[i]) if rp is not None else 0.0
                a["place"] += float(place[i])
                a["score"] += float(final[i]) - start_score
            # ---- ② 逐小局：座位·小局口径 ----
            for h in hands_here:
                for i in range(4):
                    lab = h.policies[i] if len(h.policies) == 4 else labels[i]
                    if not keep(lab):
                        continue
                    s = st_of(lab)
                    # 防御：`hand` 行的标签与 `game`/`per_game` 不一致时（真轨迹不会有，
                    # 但合成数据/手改的轨迹会有）不能 KeyError —— 补一个空累加器，序列那侧
                    # 用 `a["n"] <= 0` 显式跳过，不写假值。
                    a = acc.setdefault(lab, {"n": 0.0, "rp": 0.0, "place": 0.0, "score": 0.0,
                                             "sh": 0.0, "wins": 0.0, "di": 0.0, "ws": 0.0,
                                             "wn": 0.0})
                    s.seat_hands += 1
                    a["sh"] += 1
                    s.delta_sum += h.delta[i]
                    if h.winner == i:
                        s.wins += 1
                        a["wins"] += 1
                        s.win_score_sum += h.delta[i]
                        a["ws"] += h.delta[i]
                        a["wn"] += 1
                        if h.tsumo:
                            s.tsumo_wins += 1
                        bp = base_points(h)
                        if bp is not None:
                            s.win_base_sum += bp
                            s.win_base_n += 1
                            t = score_tier(bp, dealer=(h.dealer == i))
                            s.tier_counts[t] = s.tier_counts.get(t, 0) + 1
                        # ---- 役种轴（分母 = **真的有役种数据**的和了手）----
                        # ⚠ 与 `wins` 分开记：老轨迹有 `wins` 但没有役种数据 ⇒ 分母 0 ⇒ 轴画 `—`；
                        #   新轨迹里"没有和了者的小局"本来就不进这个分母（那些行没有五列）。
                        if h.yaku is not None:
                            s.yaku_hands += 1
                            table["yaku_hands"] += 1
                            s.yaku_count_sum += len(h.yaku)
                            if len(h.yaku) >= 2:
                                s.compound_wins += 1
                            s.han_sum += h.han or 0
                            if (h.yakuman or 0) > 0:
                                s.yakuman_wins += 1
                            lim = h.limit or ""
                            s.limit_counts[lim] = s.limit_counts.get(lim, 0) + 1
                            for name in h.yaku_names:
                                s.yaku_freq[name] = s.yaku_freq.get(name, 0) + 1
                    # ⚠ 与生产者逐字同口径：`h.loser == i`（自摸时 loser = −1，天然不计）
                    if h.loser == i:
                        s.deal_ins += 1
                        a["di"] += 1
                        if not h.tsumo:
                            s.deal_in_sum += -h.delta[i]
                            s.deal_in_n += 1
                    if h.tsumo and 0 <= h.winner < 4 and h.winner != i:
                        s.tsumo_against_n += 1
                        s.tsumo_loss_sum += -h.delta[i]
                    if h.dealer == i:
                        s.dealer_hands += 1
                        if h.renchan:
                            s.dealer_renchan += 1
            for lab, a in acc.items():
                s = st_of(lab)
                n = a["n"] or 1.0
                sh = a["sh"] or 1.0
                # `rank_points` / `place` / `score`：**复用 `eval.per_game_series`**（唯一实现）
                if rd.eval_run is not None:
                    for metric in ml_eval.METRICS:
                        for sd, v in ml_eval.per_game_series(rd.eval_run, lab, metric).items():
                            s.add_series(metric, rd_i, int(sd), float(v))
                else:
                    if a["n"] > 0:
                        s.add_series("rank_points", rd_i, seed, a["rp"] / n)
                        s.add_series("place", rd_i, seed, -a["place"] / n)  # 取负：正 = 更好（`eval` 口径）
                        s.add_series("score", rd_i, seed, a["score"] / n)
                # 后三个是"从轨迹重算的每场率"（`summary.json` 里没有逐场值）⇒ 只能这里算
                if a["sh"] > 0:
                    s.add_series("win_rate", rd_i, seed, a["wins"] / sh)
                    s.add_series("deal_in_rate", rd_i, seed, a["di"] / sh)
                if a["wn"]:
                    s.add_series("avg_win_score", rd_i, seed, a["ws"] / a["wn"])
        # ---- ③ 逐决策：行为轴 ----
        if rd.decisions is None:
            continue
        # ⚠ 和了巡目**只认真正和了的那一家**：一次荣和可能给多家发 `ron` 询问，而只有优先级
        #   最高的那家真的和（实测多出 ≈1% 的 `ron` 决策行）⇒ 不筛就会把"见逃/被压过"的
        #   决策当成和了，巡目被拉偏、覆盖率会 >100%。
        winner_of = {(game_off + h.game, h.hand_no): h.winner for h in rd.hands}
        for d in rd.decisions:
            if not d.policy or not keep(d.policy):
                continue
            s = st_of(d.policy)
            s.decisions += 1
            key = (game_off + d.game, d.hand_no, d.seat, d.policy)
            if d.kind == "turn":
                s.turn_decisions += 1
                seen_turn.add(key)
            if d.action == "riichi":
                if key not in seen_riichi:
                    seen_riichi.add(key)
                    s.riichi += 1
                    if d.riichi_others:
                        s.riichi_chase += 1
            elif d.is_open_call:
                if key not in seen_open:
                    seen_open.add(key)
                    s.open_call_hands += 1
            elif d.kan_kind == "ankan":
                if key not in seen_ankan:
                    seen_ankan.add(key)
                    s.ankan_hands += 1
            if d.chosen == "tsumo" and d.player_draws is not None:
                turn = float(d.player_draws)
            elif d.chosen == "ron" and d.ron_turn is not None:
                turn = float(d.ron_turn)
            else:
                continue
            hk = (game_off + d.game, d.hand_no)
            if winner_of.get(hk) != d.seat:
                table["win_turn_extra"] += 1        # 被压过的 / 见逃的 `ron` 询问：不算和了
                continue
            s.win_turns.append(turn)
            table["win_turn_covered"] += 1
    for (_g, _h, _s, lab) in seen_turn:
        st_of(lab).seat_hands_with_turn += 1
    for s in stats.values():
        s.tier_counts = {t: s.tier_counts.get(t, 0) for t in TIERS if s.tier_counts.get(t)}
    return stats, table


# ================================================================= 出表

def _fmt_value(block: str, name: str, v: Any) -> str:
    """轴的显示格式（率一律百分比；取不到一律 `—`）。"""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        return str(v)
    if "率" in name or "覆盖" in name:
        return f"{100 * float(v):.2f}%"
    return f"{float(v):,.2f}"


@dataclass
class Axes:
    """一个策略的一条轴（`value is None` = **取不到**，绝不填 0）。"""

    block: str
    name: str
    value: Any = None
    note: str = ""


#: 各块的轴顺序（表按它出行；**取不到的轴也占一行**，免得"没有"被读成"没问题"）
MISSING_AXES = ("默听率",)
#: 「越小越好」的轴（其余非中性轴一律「越大越好」）—— 只用于**轴级领先者**那张速览
LOWER_BETTER = ("平均顺位", "铳率", "平均铳点", "被自摸率", "被自摸失点", "二位率", "三位率", "四位率")
#: 中性轴（不参与"谁领先"：结构量 / 派生分母 / **风格与运气**（役种多不多、有没有役满都不是强弱））
NEUTRAL_AXES = ("场数", "座位·小局", "决策数", "流局率", "途中流局率", "和了巡目覆盖",
                "平均得点", "平均得点收支", "平均和点",
                "平均役种数", "复合役率（役种数≥2）", "真·役满率（yakuman>0）")


def axes_of(s: PolicyStat, table: dict, *, decisions_ok: bool) -> list[Axes]:
    """把 `PolicyStat` 摊成「轴 = 值」的列表（= 表的行）。"""
    a: list[Axes] = []
    hands_total = table.get("hands", 0)
    wins = s.wins
    tot_place = sum(s.place_counts)
    tier_at_least = {t: sum(v for k, v in s.tier_counts.items() if TIERS.index(k) >= TIERS.index(t))
                     for t in TIERS}

    # ---- 完场 ----
    a.append(Axes("完场", "场数", s.games, "该策略占的座位数（4 家同场时 = 4 × 场数 / 4）"))
    a.append(Axes("完场", "平均顺位", safe_div(sum((i + 1) * c for i, c in enumerate(s.place_counts)),
                                              tot_place) if tot_place else None))
    a.append(Axes("完场", "平均顺位点", safe_div(s.rank_point_sum, s.games) if s.games else None,
                  "`per_game.rank_points`（0.1 分取整）的均值；与 `by_policy.avg_rank_points` 同口径"))
    a.append(Axes("完场", "平均得点", safe_div(s.final_score_sum, s.games) if s.games else None))
    a.append(Axes("完场", "平均得点收支", safe_div(s.final_score_sum - s.start_score_sum, s.games)
                  if s.games else None, "相对配给原点（起点分），**与平均收支不同分母**"))
    for i, nm in enumerate(("一位率", "二位率", "三位率", "四位率")):
        a.append(Axes("完场", nm, safe_div(s.place_counts[i], tot_place) if tot_place else None))
    a.append(Axes("完场", "连庄率", safe_div(s.dealer_renchan, s.dealer_hands)
                  if s.dealer_hands else None, "本小局为亲且 `renchan`"))
    a.append(Axes("完场", "被自摸率", safe_div(s.tsumo_against_n, s.seat_hands), "每座位·小局"))
    a.append(Axes("完场", "被自摸失点", safe_div(s.tsumo_loss_sum, s.tsumo_against_n)
                  if s.tsumo_against_n else None))
    a.append(Axes("完场", "流局率", safe_div(table.get("ryukyoku", 0), hands_total)
                  if hands_total else None, "**全桌共享**（结构量，与策略无关）"))
    a.append(Axes("完场", "途中流局率", safe_div(table.get("abortive", 0), hands_total)
                  if hands_total else None, "九种九牌/四风连打/四家立直/三家和/四杠散了"))

    # ---- 和了 ----
    a.append(Axes("和了", "座位·小局", s.seat_hands, "下面所有率的公共分母（= 生产者的 `seat_hands`）"))
    a.append(Axes("和了", "和率", safe_div(wins, s.seat_hands), "每座位·小局"))
    a.append(Axes("和了", "平均和点", safe_div(s.win_score_sum, wins) if wins else None,
                  "含本场棒与回收的立直棒（= 生产者 `avg_win_score`）"))
    a.append(Axes("和了", "平均净打点", safe_div(s.win_base_sum, s.win_base_n) if s.win_base_n else None,
                  "**反推**的实际打点（去本场棒 / 立直棒）"))
    a.append(Axes("和了", "自摸率", safe_div(s.tsumo_wins, wins) if wins else None, "占和了"))
    a.append(Axes("和了", "平均和了巡目", mean(s.win_turns) if s.win_turns else None,
                  "自家摸牌数那把尺子；荣和取打出那张的舍主当年摸牌数"))
    a.append(Axes("和了", "満貫以上率", safe_div(tier_at_least["満貫"], wins) if wins else None,
                  "**按实际收到的点数判档**（非役种表）"))
    for t in ("跳満", "倍満", "三倍満", "役満以上"):
        a.append(Axes("和了", f"{t}率", safe_div(tier_at_least[t], wins) if wins else None))

    # ---- 放铳 ----
    a.append(Axes("放铳", "铳率", safe_div(s.deal_ins, s.seat_hands), "每座位·小局（只数荣和）"))
    a.append(Axes("放铳", "平均铳点", safe_div(s.deal_in_sum, s.deal_in_n) if s.deal_in_n else None,
                  "放铳者实付（含本场棒）"))
    a.append(Axes("放铳", "被自摸失点", safe_div(s.tsumo_loss_sum, s.tsumo_against_n)
                  if s.tsumo_against_n else None))

    # ---- 役 ----
    a.append(Axes("役", "役满率（点数近似）", safe_div(s.tier_counts.get("役満以上", 0), wins)
                  if wins else None, "打点 ≥ 32000（子）/ 48000（亲）—— 含数え役満，**不是役种表**"))
    # 役种轴（2026-10 起 `hand` 行带 `yaku`/`han`/`fu`/`yakuman`/`limit`，PROTOCOL §8.4）：
    # 分母是**真的有役种数据的和了手** ⇒ **老轨迹（没有那五列）分母为 0 ⇒ 整块画 `—`**，
    # ⛔ 绝不画 0.00（AGENTS §6.5：「取不到」与「真的是 0」必须分得开）。
    yk_n = s.yaku_hands
    n_lim = {k: v for k, v in s.limit_counts.items()}
    mangan_plus = sum(v for k, v in n_lim.items() if k in MANGAN_PLUS_LIMITS)
    a.append(Axes("役", "平均役种数", safe_div(s.yaku_count_sum, yk_n) if yk_n else None,
                  "役列表长度（含宝牌/赤宝/里宝）；分母 = 有役种数据的和了手"))
    a.append(Axes("役", "复合役率（役种数≥2）", safe_div(s.compound_wins, yk_n) if yk_n else None,
                  "役种数 ≥ 2 的和了手占比"))
    a.append(Axes("役", "平均番数", safe_div(s.han_sum, yk_n) if yk_n else None,
                  "合计番数（役满按「13 × 倍数」折算，与逐役之和一致）"))
    a.append(Axes("役", "真·役满率（yakuman>0）", safe_div(s.yakuman_wins, yk_n) if yk_n else None,
                  "**役种表**口径：役满役出现率 —— 数え役満（`kazoe_yakuman`）不算"))
    a.append(Axes("役", "満貫以上率（按 limit）", safe_div(mangan_plus, yk_n) if yk_n else None,
                  "打点档**码** ≥ 满贯（含累计役满）—— 与上面「按点数」那根不是同一个量"))
    a.append(Axes("役", "役种频次 Top-N", yaku_top_n(s.yaku_freq, yk_n) if yk_n else None,
                  "`code:tile`（参数化役种按牌分开）；率的分母与上面五根相同"))

    # ---- 行为 ----
    ok = bool(decisions_ok)
    a.append(Axes("行为", "立直率", safe_div(s.riichi, s.seat_hands) if ok else None, "每座位·小局"))
    a.append(Axes("行为", "立直率（仅自家回合）",
                  safe_div(s.riichi, s.seat_hands_with_turn) if ok and s.seat_hands_with_turn else None,
                  "换一个分母：只算「有过自家摸打」的座位·小局"))
    a.append(Axes("行为", "追立率", safe_div(s.riichi_chase, s.riichi) if ok and s.riichi else None,
                  "宣言那一刻已有他家立直"))
    a.append(Axes("行为", "副露率", safe_div(s.open_call_hands, s.seat_hands) if ok else None,
                  "吃 / 碰 / 大明杠 / 加杠（**暗杠不算**）"))
    a.append(Axes("行为", "暗杠率", safe_div(s.ankan_hands, s.seat_hands) if ok else None))
    a.append(Axes("行为", "和了巡目覆盖", safe_div(len(s.win_turns), wins) if ok and wins else None,
                  "有「真和了」决策行的小局占比（被压过/见逃的 `ron` 询问不算；"
                  "`--sample` 抽样过的轨迹会掉这个）"))
    a.append(Axes("行为", "决策数", s.decisions if ok else None, "轨迹里该策略的 `decision` 行数"))
    a.append(Axes("行为", "默听率", None, "⛔ obs 没有「自家是否听牌」⇒ 取不到"))
    return a


def leader_report(profiles: dict[str, list[Axes]], colnames: Sequence[str],
                  names: Sequence[str]) -> list[str]:
    """轴级领先者速览：**每个轴谁最高**（铳率那类看谁最低），并汇总成"谁在哪几轴领先"。

    ⚠ 这**不是**显著性判据 —— 它只说"这批牌山上谁在这根轴上更好"。要"显著"必须走
    `--incumbent` 的 `arena.judge_pair`（逐场配对 + CI）。
    """
    lines = ["== 轴级领先者速览（**不是显著性判据**；显著性只走 `--incumbent` 的 judge_pair） ==",
             f"   {'轴':<22}{'领先者':<18}{'读数':>14}{'与次席差':>12}"]
    leads: dict[str, list[str]] = {c: [] for c in colnames}
    for ax0 in profiles[names[0]]:
        if ax0.name in NEUTRAL_AXES or ax0.name in MISSING_AXES or "Top-N" in ax0.name:
            continue
        vals: list[tuple[float, str]] = []
        for c, n in zip(colnames, names):
            cell = next((x for x in profiles[n] if x.block == ax0.block and x.name == ax0.name), None)
            v = None if cell is None else cell.value
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                vals.append((float(v), c))
        if len(vals) < 2:
            continue
        vals.sort()
        best = vals[0] if ax0.name in LOWER_BETTER else vals[-1]
        second = vals[1] if ax0.name in LOWER_BETTER else vals[-2]
        margin = abs(second[0] - best[0])
        scale = max(abs(best[0]), abs(second[0]), 1e-9)
        winners = [c for v, c in vals if abs(v - best[0]) <= 1e-6 * scale]
        for c in winners:
            if ax0.name not in leads[c]:          # 同名轴（如"被自摸失点"在完场/放铳各一行）只记一次
                leads[c].append(ax0.name)
        shown = _fmt_value(ax0.block, ax0.name, best[0])
        lines.append(f"   {ax0.name:<22}{'/'.join(winners):<18}{shown:>14}{margin:>12.4g}"
                     + ("（并列）" if len(winners) > 1 else ""))
    lines.append("   汇总（挑专家的输入 —— 每条线上留哪个模型，就看它领先哪些轴）：")
    for c in colnames:
        tag = "、".join(leads[c]) if leads[c] else "（无）"
        lines.append(f"     {c:<20} 领先 {len(leads[c]):>2} 轴：{tag}")
    return lines


def render_table(profiles: dict[str, list[Axes]], names: Sequence[str], *, width: int = 18) -> str:
    """行 = 轴、列 = 模型（`names` 决定列序，**用短名**当表头 —— 策略串是整条路径）。"""
    head = f"{'块':<5}{'轴':<22}" + "".join(f"{n:>{width}}" for n in names)
    lines = [head, "-" * len(head)]
    for ax0 in profiles[names[0]]:
        row = f"{ax0.block:<5}{ax0.name:<22}"
        for n in names:
            cell = next((x for x in profiles[n] if x.block == ax0.block and x.name == ax0.name), None)
            row += f"{_fmt_value(ax0.block, ax0.name, None if cell is None else cell.value):>{width}}"
        lines.append(row)
    return "\n".join(lines)


# ================================================================= 自检（纯函数单测）

def _mk_hand(game: int, hand_no: int, delta, *, agari=True, winner=None, loser=None, tsumo=False,
             dealer=0, honba=0, renchan=False, tenpai=(False,) * 4, policies=("A", "A", "A", "A"),
             abortive=False, nagashi=False, sticks_after=0, yaku=None, han=None, fu=None,
             yakuman=None, limit=None, kyoku=1, round_wind="E") -> HandRow:
    """合成一条 `hand` 行。

    ⚠ `winner` / `loser` 的默认值**随 `agari` 走**：流局真的发 `winner = -1`（实测轨迹如此），
    而生产者的 `wins` 判据是裸的 `h.winner == i` —— 所以合成行要是给流局留个 `winner = 0`，
    就会凭空多出 4 个"和了"。

    ⚠ 役种五列**默认全 `None`**（= 老轨迹的形状）：要测役种轴必须显式给 `yaku=`。
    给 `yaku` 时若没给 `han`/`fu`，按"逐役之和 / 20 符"补齐 —— 免得每个用例都手算一遍。
    """
    if agari:
        winner = 0 if winner is None else winner
        loser = -1 if loser is None else loser
    else:
        winner = -1 if winner is None else winner
        loser = -1 if loser is None else loser
    if yaku is not None:
        yk = tuple(yaku)
        han = sum(h for *_x, h, _y in yk) if han is None else han
        fu = 30 if fu is None else fu
        yakuman = sum(y for *_x, y in yk) if yakuman is None else yakuman
        limit = "" if limit is None else limit
    return HandRow(game=game, hand_no=hand_no, round_wind=round_wind, kyoku=kyoku, honba=honba,
                   dealer=dealer, sticks_after=sticks_after,
                   delta=tuple(int(x) for x in delta), agari=agari, abortive=abortive, reason="",
                   renchan=renchan, winner=winner, loser=loser, tsumo=tsumo, nagashi=nagashi,
                   tenpai=tuple(bool(x) for x in tenpai), policies=tuple(policies),
                   yaku=tuple(yaku) if yaku is not None else None, han=han, fu=fu,
                   yakuman=yakuman, limit=limit)


#: 合成役条目的简写：`_Y("riichi", 1)` / `_Y("yakuhai", 1, tile="5z")` / `_Y("kokushi", 13, yk=1)`。
def _Y(code: str, han: int, *, tile: str | None = None, yk: int = 0):
    return (code, tile, han, yk)


def _mk_dec(game, hand_no, seat, policy, kind, chosen, draws=None, riichi_others=False,
            ron_turn=None) -> DecisionRow:
    return DecisionRow(game=game, hand_no=hand_no, seat=seat, policy=policy, kind=kind,
                       chosen=chosen, player_draws=draws, riichi_others=riichi_others,
                       ron_turn=ron_turn)


def _mk_run(games: list[dict], hands: list[HandRow], decisions: list[DecisionRow] | None,
            start_score: int = 25000) -> RunData:
    """合成一个 run（`per_game` 直接给全，`game_rows` 只补 `start_score`）。

    ⚠ 合成数据里**行与场的策略标签必须自洽**（真轨迹天然如此）：这里按 `per_game.policies`
    把 `hand` / `decision` 行的标签统一覆盖，免得测试数据自己写错还怪被测代码。
    """
    pol = {int(g["game"]): tuple(g["policies"]) for g in games}

    def lab_of(game: int, seat: int) -> str:
        row = pol.get(game) or ()
        return row[seat] if 0 <= seat < len(row) else ""

    hands = [replace(h, policies=pol.get(h.game, h.policies)) for h in hands]
    if decisions is not None:
        decisions = [replace(d, policy=lab_of(d.game, d.seat)) for d in decisions]
    return RunData(dir=Path("<合成>"), per_game=games,
                   game_rows=[{"game": g["game"], "seed": g["seed"], "policies": g["policies"],
                               "start_score": start_score} for g in games],
                   hands=hands, decisions=decisions, sampled_every=1)


def self_check() -> int:
    """`v4 profile --self-check`：纯函数的边界与不变量（**不读盘、不起 exe**）。"""
    fails: list[str] = []
    n_ok = 0

    def ok(cond: bool, msg: str) -> None:
        nonlocal n_ok
        print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
        if cond:
            n_ok += 1
        else:
            fails.append(msg)

    print("== 计数 / 率 / 均值 / 分位 ==")
    ok(safe_div(0, 0) == 0.0, "空分母 ⇒ 0.0（不是 NaN）")
    ok(abs(safe_div(1, 4) - 0.25) < 1e-12, "率 1/4 = 0.25")
    ok(safe_div(-3, 6) == -0.5, "率允许负数（放铳收支那类口径）")
    ok(mean([]) == 0.0, "空输入的均值 ⇒ 0.0")
    ok(abs(mean([1, 2, 3, 4]) - 2.5) < 1e-12, "均值 [1,2,3,4] = 2.5")
    ok(math.isnan(quantile([], 0.5)), "空输入的分位 ⇒ nan")
    ok(abs(quantile([1, 2, 3, 4], 0.5) - 2.5) < 1e-12, "中位数 [1,2,3,4] = 2.5（线性插值）")
    ok(abs(quantile([1, 2, 3, 4], 0.0) - 1.0) < 1e-12 and abs(quantile([1, 2, 3, 4], 1.0) - 4.0) < 1e-12,
       "q=0 / q=1 取端点")
    ok(abs(quantile([5], 0.9) - 5.0) < 1e-12, "单点分位 = 它自己")
    ok(abs(quantile([1, 2, 3, 4], 0.25) - 1.75) < 1e-12, "q=0.25 ⇒ 1.75（逐点核过）")

    print("== 顺位：并列按座次拆开（不许挤掉名次）==")
    ok(placement_from_scores([30000, 30000, 20000, 10000]) == [1, 2, 3, 4],
       "并列头名 ⇒ 座次在前的拿 1 位（[1,2,3,4]）")
    ok(placement_from_scores([10000, 30000, 30000, 10000]) == [3, 1, 2, 4],
       "并列 + 座次乱序 ⇒ 仍是 1..4 的一个排列")
    ok(sorted(placement_from_scores([25000] * 4)) == [1, 2, 3, 4], "四家同点 ⇒ 仍是一个排列")
    ok(placement_dist([1, 1, 4, 2]) == [2, 1, 0, 1], "顺位分布计数")
    ok(placement_dist([]) == [0, 0, 0, 0] and placement_dist([0, 5]) == [0, 0, 0, 0],
       "空 / 越界顺位一律忽略（不抛）")

    print("== 打点反解（荣和 / 自摸 / 本场棒 / 立直棒）==")
    h = _mk_hand(0, 0, [-8000, 8000, 0, 0], winner=1, loser=0)
    ok(base_points(h) == 8000, f"荣和 8000 反解 ⇒ {base_points(h)}")
    h = _mk_hand(0, 0, [-8600, 8600, 0, 0], winner=1, loser=0, honba=2)
    ok(base_points(h) == 8000, f"带 2 本场棒的荣和 ⇒ {base_points(h)}（本场棒要减掉）")
    h = _mk_hand(0, 0, [-8000, 9000, 0, 0], winner=1, loser=0)
    ok(base_points(h) == 8000 and sticks_delta(h) == 1,
       f"荣和 + 收 1 根棒 ⇒ 打点 {base_points(h)}、ΣΔ/1000 = {sticks_delta(h)}（棒不进反解）")
    h = _mk_hand(0, 0, [-2000, 8000, -2000, -4000], winner=1, tsumo=True)
    ok(base_points(h) == 8000, f"子 自摸満貫（2000/2000/4000）⇒ {base_points(h)}")
    h = _mk_hand(0, 0, [-4000, 12000, -4000, -4000], winner=1, tsumo=True, dealer=1)
    ok(base_points(h) == 12000, f"亲 自摸満貫（4000 all）⇒ {base_points(h)}")
    h = _mk_hand(0, 0, [0, 0, 0, 0], agari=False)
    ok(base_points(h) is None and not h.is_nagashi, "流局行 ⇒ 打点 None（不猜 0）")
    h = _mk_hand(0, 0, [0, 0, 0, 0], agari=True, winner=-1)
    ok(base_points(h) is None, "agari 但没有 winner ⇒ None")

    print("== 打点分档（子/亲阈值不同；按实际点数判 ⇒ 切り上げ満貫自动落对）==")
    cases = [(7700, False, "満貫未満"), (8000, False, "満貫"), (12000, False, "跳満"),
             (16000, False, "倍満"), (24000, False, "三倍満"), (32000, False, "役満以上"),
             (64000, False, "役満以上"), (11600, True, "満貫未満"), (12000, True, "満貫"),
             (18000, True, "跳満"), (24000, True, "倍満"), (36000, True, "三倍満"),
             (48000, True, "役満以上")]
    bad = [(p, d, score_tier(p, d), w) for p, d, w in cases if score_tier(p, d) != w]
    ok(not bad, f"13 个档位边界逐条对上{'：' + str(bad) if bad else ''}")

    print("== Σdelta 的账（立直棒进出）==")
    # 真实数据里长这样：三家（都是听牌家）各宣言立直（−1000）、随后各收 1000 罚符 ⇒ 净 0；
    # 只有不听的那家付 3000 ⇒ Σ = −3000 = 桌上新放的 3 根棒（实测 `0-4-0` 那一手）。
    ok(sticks_delta(_mk_hand(0, 0, [0, -3000, 0, 0], agari=False, tenpai=(True, False, True, True))) == -3,
       "流局：三家立直（新放 3 根棒）⇒ ΣΔ/1000 = −3")
    ok(sticks_delta(_mk_hand(0, 0, [0, 9000, -8000, 0], winner=1, loser=2)) == 1,
       "荣和：回收 1 根棒 ⇒ ΣΔ/1000 = +1")
    ok(sticks_delta(_mk_hand(0, 0, [0, 0, 0, 0], agari=False)) == 0, "空流局 ⇒ 0")

    print("== 归约：空输入 / 单场 ==")
    st, tab = reduce_run([])
    ok(st == {} and tab["hands"] == 0, "空 run 列表 ⇒ 无策略、0 小局（不抛）")
    rd = _mk_run([{"game": 0, "seed": 1, "policies": ["A"] * 4,
                   "final_scores": [30000, 20000, 20000, 30000],
                   "placement": [1, 3, 4, 2], "rank_points": [30.0, -10.0, -50.0, 30.0]}],
                 [_mk_hand(0, 0, [0, -8000, 0, 0], winner=1, loser=0)],
                 [_mk_dec(0, 0, 0, "A", "turn", "discard:1m", draws=1)])
    st, tab = reduce_run([rd])
    sA = st["A"]
    ok(sA.games == 4 and sA.seat_hands == 4,
       f"单场 ⇒ games=4（按座位）/ seat_hands=4（实得 {sA.games}/{sA.seat_hands}）")
    ok(abs(safe_div(sA.wins, sA.seat_hands) - 0.25) < 1e-12, "单场：和率 = 1/4")
    ok(sA.place_counts == [1, 1, 1, 1], f"单场：顺位分布 [1,1,1,1]（实得 {sA.place_counts}）")
    ok(abs(sA.rank_point_sum - 0.0) < 1e-12, "单场：顺位点之和 = 0（马点为 0 的那套合成数）")
    ok(tab["hands"] == 1 and tab["ryukyoku"] == 0, "单场：1 小局 / 0 流局")
    ok(sA.series["win_rate"][(0, 1)] == 0.25,
       "单场：逐场序列 `win_rate[(run 0, seed 1)]` = 0.25（**键里带 run**）")

    print("== ryukyoku 行 与 无和了的场次 ==")
    rd2 = _mk_run([{"game": 0, "seed": 2, "policies": ["B"] * 4,
                    "final_scores": [25000] * 4, "placement": [1, 2, 3, 4],
                    "rank_points": [0.0] * 4}],
                  [_mk_hand(0, 0, [0, -3000, 0, 0], agari=False, tenpai=(True, False, True, True)),
                   _mk_hand(0, 1, [0, 0, 0, 0], agari=False)],
                  [])
    st2, tab2 = reduce_run([rd2])
    sB = st2["B"]
    ok(sB.wins == 0 and sB.seat_hands == 8 and safe_div(sB.wins, sB.seat_hands) == 0.0,
       "无和了的场次：和率 = 0.0（不是 NaN）")
    ok(abs(safe_div(tab2["ryukyoku"], tab2["hands"]) - 1.0) < 1e-12, "两行 ryukyoku ⇒ 流局率 100%")
    ok(sB.deal_ins == 0 and safe_div(sB.deal_in_sum, sB.deal_in_n) == 0.0,
       "无放铳 ⇒ 平均铳点 0.0（分母 0 不崩）")
    ok(sB.tier_counts == {}, "无和了 ⇒ 档位分布为空")
    ok(safe_div(sB.win_score_sum, sB.wins) == 0.0, "无和了 ⇒ 平均和点落到 safe_div ⇒ 0.0（表里显示 0.00）")

    print("== 行为侧去重（立直 / 副露 / 暗杠按座位·小局只记一次）==")
    rd3 = _mk_run([{"game": 0, "seed": 3, "policies": ["C"] * 4,
                    "final_scores": [25000] * 4, "placement": [1, 2, 3, 4],
                    "rank_points": [0.0] * 4}],
                  [_mk_hand(0, 0, [8000, -2000, -2000, -4000], winner=0, tsumo=True)],
                  [_mk_dec(0, 0, 0, "C", "turn", "riichi:1m", draws=2),
                   _mk_dec(0, 0, 0, "C", "turn", "riichi:1m", draws=2),
                   _mk_dec(0, 0, 0, "C", "turn", "discard:2m", draws=2),
                   _mk_dec(0, 0, 0, "C", "claim", "pon:3m+3m"),
                   _mk_dec(0, 0, 0, "C", "claim", "pon:3m+3m"),
                   _mk_dec(0, 0, 0, "C", "claim", "kan:ankan:4m"),
                   _mk_dec(0, 0, 0, "C", "claim", "kan:daiminkan:5m+5m+5m"),
                   _mk_dec(0, 0, 0, "C", "turn", "tsumo", draws=5)])
    st3, _ = reduce_run([rd3])
    sC = st3["C"]
    ok(sC.riichi == 1, f"同一座位·小局里重复的立直行只记 1 次（实得 {sC.riichi}）")
    ok(sC.open_call_hands == 1, f"碰 / 大明杠各自记 1 次（实得 {sC.open_call_hands}）")
    ok(sC.ankan_hands == 1, f"暗杠单独计数、不算副露（实得 {sC.ankan_hands}）")
    ok(sC.seat_hands_with_turn == 1, "有过自家回合的座位·小局 = 1")
    ok(abs(mean(sC.win_turns) - 5.0) < 1e-12, f"自摸和了巡目 = 自家摸牌数 5（实得 {sC.win_turns}）")
    ax = {x.name: x.value for x in axes_of(sC, {"hands": 1}, decisions_ok=True)}
    ok(abs(ax["立直率"] - 0.25) < 1e-12 and abs(ax["追立率"] - 0.0) < 1e-12,
       f"立直率 = 1/4、追立率 = 0/1（实得 {ax['立直率']} / {ax['追立率']}）")
    ok(ax["默听率"] is None and ax["平均役种数"] is None and ax["平均番数"] is None,
       "取不到的轴一律 `None`（不是 0）—— 老轨迹的役种轴走的就是这条路")
    ax_off = {x.name: x.value for x in axes_of(sC, {"hands": 1}, decisions_ok=False)}
    ok(ax_off["立直率"] is None and ax_off["副露率"] is None and ax_off["决策数"] is None,
       "没有决策行 ⇒ **整块行为轴**缺席（None）")

    print("== 庄家：(kyoku,honba) → dealer 的边界（报文没有 dealer 字段 ⇒ 必须派生）==")
    ok([dealer_of(k, 0) for k in (1, 2, 3, 4)] == [0, 1, 2, 3],
       "东 1..4 ⇒ 庄家 0/1/2/3")
    ok([dealer_of(1, h) for h in (0, 1, 2, 7)] == [0, 0, 0, 0],
       "连庄（kyoku 不变、honba 递增）⇒ 同一个庄家（式子不含 honba）")
    ok([dealer_of(k, 0) for k in (1, 2, 3, 4)] == [0, 1, 2, 3] == [dealer_of(k, 3) for k in (1, 2, 3, 4)],
       "南场（bakaze=S）同样按 kyoku 轮转：南1 的庄家仍是 0 号，不接着东4 往后数")
    ok(dealer_of(0, 0) == -1 and dealer_of(5, 0) == -1,
       "kyoku 形状不对（0 / 5）⇒ -1 = 未知（画 `—`，⛔ 不猜成 3 号庄）")
    ok(dealer_of(2, 0, has_field=True, field=3) == 3 and dealer_of(2, 0, has_field=True, field=None) == 1,
       "**报文里带 dealer 时以它为准（权威）**；带了个 null 就退回派生")
    ok(parse_hand_row({"round": {"bakaze": "E", "kyoku": 2}}, ()).dealer == 1
       and parse_hand_row({"round": {"bakaze": "E", "kyoku": 2, "dealer": 3}}, ()).dealer == 3,
       "解析路径：没有 dealer ⇒ 派生；有 ⇒ 用它")
    # 这条是**踩过的坑**：老口径下 dealer 恒 -1 ⇒ 亲家 12000 被判成跳満
    h_dealer = _mk_hand(0, 0, [-4000, 12000, -4000, -4000], winner=1, tsumo=True, dealer=1)
    ok(base_points(h_dealer) == 12000 and score_tier(12000, dealer=(h_dealer.dealer == 1)) == "満貫"
       and score_tier(12000, dealer=False) == "跳満",
       "亲家自摸 4000 all = 12000 ⇒ **満貫**（按子家阈值算才会错成跳満 —— 这就是 dealer 修正的动因）")

    print("== 修 dealer 解锁的两根轴：连庄率 不再 `—`、亲家打点不再按子家阈值 ==")
    rd_dl = _mk_run([{"game": 0, "seed": 31, "policies": ["D"] * 4,
                      "final_scores": [25000] * 4, "placement": [1, 2, 3, 4],
                      "rank_points": [0.0] * 4}],
                    [_mk_hand(0, 0, [-4000, 12000, -4000, -4000], winner=1, tsumo=True,
                              dealer=1, renchan=True),
                     _mk_hand(0, 1, [0, 0, 0, 0], agari=False, dealer=1, renchan=True, honba=1,
                              tenpai=(True, False, True, True))],
                    [])
    st_dl, tab_dl = reduce_run([rd_dl])
    sD = st_dl["D"]
    ax_dl = {x.name: x.value for x in axes_of(sD, tab_dl, decisions_ok=False)}
    ok(sD.dealer_hands == 2 and sD.dealer_renchan == 2 and abs(ax_dl["连庄率"] - 1.0) < 1e-12,
       f"连庄率 = 2/2 = 100%（实得 {ax_dl['连庄率']}；dealer 恒 -1 时这里是 None/`—`）")
    ok(abs(ax_dl["満貫以上率"] - 1.0) < 1e-12 and abs(ax_dl["跳満率"] - 0.0) < 1e-12
       and sD.tier_counts.get("満貫") == 1,
       f"亲家那手 12000 落「満貫」、跳満率 0（实得 tier_counts={sD.tier_counts}）")

    print("== 役种轴（`yaku`/`han`/`fu`/`yakuman`/`limit`；老轨迹必须画 —，不许画 0.00）==")
    # ① 解析：判据是**键在不在**，不是值是不是 0
    r_new = parse_hand_row({"type": "hand", "game": 0, "hand_no": 0,
                            "round": {"bakaze": "E", "kyoku": 1, "honba": 0, "riichi_sticks": 0},
                            "delta": [8000, -8000, 0, 0], "agari": True, "winner": 0, "loser": 1,
                            "yaku": [{"code": "riichi", "han": 1},
                                     {"code": "yakuhai", "tile": "5z", "han": 1},
                                     {"code": "dora", "han": 2}],
                            "han": 4, "fu": 40, "yakuman": 0, "limit": ""}, ("A",) * 4)
    ok(r_new.yaku is not None and len(r_new.yaku) == 3 and r_new.han == 4 and r_new.fu == 40
       and r_new.yakuman == 0 and r_new.limit == "" and r_new.yaku_names == ("riichi", "yakuhai:5z", "dora"),
       f"新轨迹：役列表 + 番/符/役满/档位都解析出来（实得 {r_new.yaku}）")
    r_old = parse_hand_row({"type": "hand", "game": 0, "hand_no": 0, "delta": [8000, -8000, 0, 0],
                            "agari": True, "winner": 0, "han": 4}, ("A",) * 4)
    ok(r_old.yaku is None and r_old.han == 4 and r_old.fu is None,
       "老轨迹：`yaku` 缺席 ⇒ None（⛔ 不是空元组）；但**已存在的** `han` 照常读出来")
    ok(parse_hand_row({"yaku": [], "agari": True}, ()).yaku is None,
       "空 `yaku` 列表按**缺席**处理（空列表在报文里不合法）")

    print("== 役种轴的六根轴：分母是「有役种数据的和了手」，且与点数口径**不同量** ==")
    # 合成一场：3 手和了（第 3 手是役满，但实收点数只有 8000 = 包牌/供託那类）+ 1 手流局
    rd_yk = _mk_run([{"game": 0, "seed": 21, "policies": ["Y"] * 4,
                      "final_scores": [25000] * 4, "placement": [1, 2, 3, 4],
                      "rank_points": [0.0] * 4}],
                    [_mk_hand(0, 0, [8000, -8000, 0, 0], winner=0, loser=1,
                              yaku=[_Y("riichi", 1), _Y("tanyao", 1), _Y("dora", 2)], fu=40),
                     _mk_hand(0, 1, [1000, -1000, 0, 0], winner=0, loser=1,
                              yaku=[_Y("yakuhai", 1, tile="5z")], fu=30),
                     _mk_hand(0, 2, [8000, -8000, 0, 0], winner=0, loser=1, limit="yakuman",
                              yaku=[_Y("kokushi", 13, yk=1)], fu=0),
                     _mk_hand(0, 3, [0, -3000, 0, 0], agari=False, tenpai=(True, False, True, True))],
                    [])
    st_yk, tab_yk = reduce_run([rd_yk])
    ax_yk = {x.name: x.value for x in axes_of(st_yk["Y"], tab_yk, decisions_ok=False)}
    ok(tab_yk["yaku_field"] == 3 and st_yk["Y"].yaku_hands == 3,
       f"有役种数据的小局 3 条（流局那条不算；实得 table={tab_yk['yaku_field']}）")
    ok(abs(ax_yk["平均役种数"] - 5 / 3) < 1e-12,
       f"平均役种数 = (3+1+1)/3（实得 {ax_yk['平均役种数']}）")
    ok(abs(ax_yk["复合役率（役种数≥2）"] - 1 / 3) < 1e-12,
       f"复合役率 = 1/3（只有第一手 ≥2；实得 {ax_yk['复合役率（役种数≥2）']}）")
    ok(abs(ax_yk["平均番数"] - 6.0) < 1e-12, f"平均番数 = (4+1+13)/3 = 6（实得 {ax_yk['平均番数']}）")
    ok(abs(ax_yk["真·役满率（yakuman>0）"] - 1 / 3) < 1e-12
       and abs(ax_yk["役满率（点数近似）"] - 0.0) < 1e-12,
       "**两个口径不同量**：真·役满率 1/3，而按点数近似的役満以上率是 0（那手只收 8000）")
    ok(abs(ax_yk["満貫以上率（按 limit）"] - 1 / 3) < 1e-12,
       f"満貫以上率（按 limit）= 1/3（只有那手役满的档位码 ≥ 満貫；实得 {ax_yk['満貫以上率（按 limit）']}）")
    ok(ax_yk["役种频次 Top-N"].startswith("dora 33%(1) / kokushi 33%(1) / riichi 33%(1)"),
       f"役种频次 Top-N 按次数降序、并列按名字定序（实得 {ax_yk['役种频次 Top-N']}）")
    ok(_fmt_value("役", "平均役种数", None) != _fmt_value("役", "平均役种数", 0.0)
       and _fmt_value("役", "真·役满率（yakuman>0）", None) == "—",
       "老轨迹/无数据 ⇒ `—`，与「真的是 0」显示不同")
    # 同一份数据把五列全删掉 ⇒ 六根轴**整块**回到 None（老轨迹的形状）
    rd_old = _mk_run([{"game": 0, "seed": 21, "policies": ["Y"] * 4,
                       "final_scores": [25000] * 4, "placement": [1, 2, 3, 4],
                       "rank_points": [0.0] * 4}],
                     [replace(h, yaku=None, han=None, fu=None, yakuman=None, limit=None)
                      for h in rd_yk.hands], [])
    st_old, tab_old = reduce_run([rd_old])
    ax_old = {x.name: x.value for x in axes_of(st_old["Y"], tab_old, decisions_ok=False)}
    ok(tab_old["yaku_field"] == 0 and all(ax_old[k] is None for k in
                                          ("平均役种数", "复合役率（役种数≥2）", "平均番数",
                                           "真·役满率（yakuman>0）", "満貫以上率（按 limit）",
                                           "役种频次 Top-N")),
       "老轨迹（整份没有五列）⇒ 六根役种轴**全部** None（⛔ 不填 0）")
    ok(ax_old["和率"] is not None and ax_old["満貫以上率"] is not None,
       "…而**别的**轴照常出数（缺席只限役种那六根）")

    print("== 追立判据 / 荣和巡目 ==")
    ok(_mk_dec(0, 0, 0, "C", "turn", "riichi:1m", riichi_others=True).riichi_others
       and not _mk_dec(0, 0, 0, "C", "turn", "riichi:1m", riichi_others=False).riichi_others,
       "追立只看宣言那一刻有没有他家立直")
    d3 = _mk_dec(0, 0, 1, "C", "claim", "ron", ron_turn=7)
    ok(d3.action == "ron" and d3.ron_turn == 7, "荣和巡目取「打出那张的舍主当年摸牌数」")
    ok(_mk_dec(0, 0, 0, "C", "claim", "kan:daiminkan:5m+5m+5m").is_open_call
       and not _mk_dec(0, 0, 0, "C", "claim", "kan:ankan:5m").is_open_call,
       "大明杠算副露、暗杠不算")

    print("== 和了巡目只认真正和了的那一家（一次荣和会给多家发 `ron` 询问）==")
    rd4 = _mk_run([{"game": 0, "seed": 4, "policies": ["P0", "P1", "P2", "P3"],
                    "final_scores": [20000, 20000, 35000, 25000],
                    "placement": [4, 3, 1, 2], "rank_points": [-40.0, -30.0, 60.0, 10.0]}],
                  [_mk_hand(0, 0, [-8000, 0, 8000, 0], winner=2, loser=0)],
                  [_mk_dec(0, 0, 0, "P0", "claim", "ron", ron_turn=6),      # 被压过
                   _mk_dec(0, 0, 2, "P2", "claim", "ron", ron_turn=6)])     # 真的和了
    st4, tab4 = reduce_run([rd4])
    ok(st4["P0"].win_turns == [] and st4["P2"].win_turns == [6.0],
       f"被压过的 `ron` 询问不算和了（P0={st4['P0'].win_turns} / P2={st4['P2'].win_turns}）")
    ok(tab4["win_turn_extra"] == 1 and tab4["win_turn_covered"] == 1,
       f"被丢弃的 `ron` 询问单独计数（extra={tab4['win_turn_extra']}）")
    ok(abs(safe_div(len(st4["P2"].win_turns), st4["P2"].wins) - 1.0) < 1e-12,
       "覆盖率 ≤ 100%（不再出现 101%）")

    print("== 跨 run 的去重键必须分开（同一策略跑两套牌山，(game,hand_no,seat) 会撞车）==")

    def _one(label: str) -> RunData:
        return _mk_run([{"game": 0, "seed": 11, "policies": [label] * 4,
                         "final_scores": [25000] * 4, "placement": [1, 2, 3, 4],
                         "rank_points": [0.0] * 4}],
                       [_mk_hand(0, 0, [0] * 4, agari=False)],
                       [_mk_dec(0, 0, 0, label, "turn", "riichi:1m", draws=2)])

    st6, _ = reduce_run([_one("S"), _one("S")])
    ok(st6["S"].riichi == 2,
       f"同一策略跨两套牌山的两次立直各记一次（实得 {st6['S'].riichi}；"
       f"没有 run 偏移就会退化成 1）")
    ok(st6["S"].seat_hands == 8 and st6["S"].games == 8, "两套牌山的座位·小局与场数照常累加")
    rd_nod = _mk_run([{"game": 0, "seed": 12, "policies": ["T"] * 4, "final_scores": [25000] * 4,
                       "placement": [1, 2, 3, 4], "rank_points": [0.0] * 4}],
                     [_mk_hand(0, 0, [0] * 4, agari=False)], None)      # 无决策行的 run
    st7, _ = reduce_run([rd_nod, _one("T")])
    ok(st7["T"].riichi == 1,
       "前面那个 run 没有决策行（走 `continue`）也不得让后一个 run 的键偏移错位")

    print("== 与现任对比的**符号方向**（`gate.decide` 的同一条陷阱）==")
    inc = PolicyStat(label="INC")
    mod = PolicyStat(label="MOD")
    for sd in range(200):
        inc.add_series("rank_points", 0, sd, -5.0)
        mod.add_series("rank_points", 0, sd, +5.0)
    lines_v, n_better = vs_incumbent({"INC": inc, "MOD": mod}, "INC", metrics=("rank_points",))
    row = next((x for x in lines_v if "rank_points" in x and "MOD" in x), "")
    ok(n_better == 1 and "+10.000" in row,
       f"明显更强的一方必须报「更好」且 Δ=+10（实得 {row.strip()[:60]}…）")
    wrong = arena.judge_pair(inc.series["rank_points"], mod.series["rank_points"],
                             "INC", "MOD", "rank_points")
    ok(wrong.delta < 0 and wrong.verdict == "更差",
       f"红证：把 `judge_pair(现任, 候选, …)` 写反 ⇒ Δ={wrong.delta:+.1f}、判「更差」"
       f"（所以顺序不许改）")

    print("== 显示格式（取不到必须画 `—`，不能画 0）==")
    ok(_fmt_value("役", "平均番数", None) == "—", "None ⇒ —")
    ok(_fmt_value("和了", "和率", 0.2304) == "23.04%", "率 ⇒ 百分比两位")
    ok(_fmt_value("完场", "平均顺位", 2.4973) == "2.50", "均值 ⇒ 两位小数")
    ok(_fmt_value("和了", "座位·小局", 33966) == "33,966", "计数 ⇒ 千分位整数")
    ok(_fmt_value("役", "平均番数", None) != _fmt_value("和了", "和率", 0.0),
       "「取不到」与「真的是 0」显示必须不同（0.00% vs —）")

    print()
    if fails:
        print(f"v4 profile --self-check FAIL（{len(fails)}/{n_ok + len(fails)} 项）：")
        for f in fails:
            print("   -", f)
        return 1
    print(f"v4 profile --self-check PASS —— {n_ok} 项（纯函数边界 + 打点反解 + 归约 + 显示）")
    return 0


# ================================================================= 对拍（独立实现 vs 官方实现）

#: 对拍口径：`(轴名, 容差)`；`0.0` = 同一批整数算出来的，应当逐位相等
CROSS_KEYS = (
    ("games", 0.0), ("seat_hands", 0.0), ("wins", 0.0), ("deal_ins", 0.0),
    ("win_rate", 0.0), ("deal_in_rate", 0.0), ("avg_win_score", 1e-6), ("avg_delta", 1e-6),
    ("avg_place", 1e-9), ("avg_rank_points", RP_TOL),
)


def tool_values(s: PolicyStat) -> dict:
    """工具侧的口径值（**只在有定义时**给；`wins == 0` 时 `avg_win_score` 记 `None`）。"""
    return {
        "games": s.games,
        "seat_hands": s.seat_hands,
        "wins": s.wins,
        "deal_ins": s.deal_ins,
        "win_rate": safe_div(s.wins, s.seat_hands),
        "deal_in_rate": safe_div(s.deal_ins, s.seat_hands),
        "avg_win_score": safe_div(s.win_score_sum, s.wins) if s.wins else None,
        "avg_delta": safe_div(s.delta_sum, s.seat_hands),
        "avg_place": safe_div(sum((i + 1) * c for i, c in enumerate(s.place_counts)),
                              sum(s.place_counts)) if sum(s.place_counts) else None,
        "avg_rank_points": safe_div(s.rank_point_sum, s.games) if s.games else None,
    }


def _producer_merged(runs: Sequence[RunData], label_filter: Sequence[str] | None) -> dict:
    """把生产者 `summary.json.by_policy` 按 run 合并回「和 / 计数」（**只用 summary 里有的字段**）。

    ⚠ 生产者不落盘 `wins` / `deal_ins` / 各类 sum，只落**率与均值** ⇒ 这里按
    `率 × 分母` 反推回整数再合并。反推对整数率是精确的（`seat_hands` 远大于 1）。
    """
    merged: dict[str, dict] = {}
    for rd in runs:
        for lab, st in (rd.by_policy or {}).items():
            if label_filter is not None and lab not in label_filter:
                continue
            m = merged.setdefault(lab, {"games": 0, "seats": 0, "wins": 0, "di": 0,
                                        "delta": 0.0, "ws": 0.0, "place": 0.0, "rp": 0.0})
            g = int(st.get("games", 0))
            sh = int(st.get("seat_hands", 0))
            w = round(float(st.get("win_rate", 0.0)) * sh)
            d = round(float(st.get("deal_in_rate", 0.0)) * sh)
            m["games"] += g
            m["seats"] += sh
            m["wins"] += w
            m["di"] += d
            m["delta"] += float(st.get("avg_delta", 0.0)) * sh
            m["ws"] += float(st.get("avg_win_score", 0.0)) * w
            m["place"] += float(st.get("avg_place", 0.0)) * g
            m["rp"] += float(st.get("avg_rank_points", 0.0)) * g
    out: dict[str, dict] = {}
    for lab, m in merged.items():
        out[lab] = {
            "games": m["games"], "seat_hands": m["seats"], "wins": m["wins"], "deal_ins": m["di"],
            "win_rate": safe_div(m["wins"], m["seats"]),
            "deal_in_rate": safe_div(m["di"], m["seats"]),
            "avg_win_score": safe_div(m["ws"], m["wins"]) if m["wins"] else None,
            "avg_delta": safe_div(m["delta"], m["seats"]),
            "avg_place": safe_div(m["place"], m["games"]) if m["games"] else None,
            "avg_rank_points": safe_div(m["rp"], m["games"]) if m["games"] else None,
        }
    return out


def cross_check(stats: dict[str, PolicyStat], runs: Sequence[RunData],
                label_filter: Sequence[str] | None = None) -> tuple[list[str], int]:
    """工具 vs 生产者 `summary.json.by_policy` 的逐条对拍（返回 `(报告行, 不一致条数)`）。"""
    prod = _producer_merged(runs, label_filter)
    lines = ["== 对拍：工具（独立实现） vs 生产者 `summary.json.by_policy`（官方实现） ==",
             "   同一批场次、同一策略；`wins`/`deal_ins` 由 `率 × seat_hands` 反推"
             "（summary 里只有率，没有计数）",
             f"   {'策略':<14}{'口径':<16}{'工具':>16}{'生产者':>16}{'差值':>13}{'容差':>9}  判定"]
    bad = 0
    skipped: list[str] = []
    for lab in sorted(stats):
        if lab not in prod:
            lines.append(f"   {short_label(lab, 16):<18}（这批 run 的 summary 里没有它 —— 跳过）")
            continue
        if stats[lab].seat_hands == 0:
            # ⚠ 只有 `summary.json`、没有 `g*.jsonl` 的 run（如闸门里跑完就删了轨迹的那种）——
            # 轨迹侧的轴全都没有，这不是"不一致"，是"没东西可比"。别把它报成工具错。
            skipped.append(lab)
            continue
        tv, pv = tool_values(stats[lab]), prod[lab]
        for name, tol in CROSS_KEYS:
            got, want = tv[name], pv[name]
            if got is None or want is None:
                lines.append(f"   {short_label(lab, 16):<18}{name:<16}{'—':>16}{'—':>16}{'—':>13}{'—':>9}"
                             f"  （无定义，跳过）")
                continue
            diff = float(got) - float(want)
            good = abs(diff) <= tol + 1e-12
            bad += 0 if good else 1
            lines.append(f"   {short_label(lab, 16):<18}{name:<16}{float(got):>16.6f}{float(want):>16.6f}"
                         f"{diff:>+13.2e}{tol:>9.3g}  {'一致 ✓' if good else '**不一致 ✗**'}")
    if skipped:
        lines.append(f"   ⚠ 跳过 {len(skipped)} 个策略（这批 run 只有 `summary.json`、没有 `g*.jsonl`，"
                     f"轨迹侧的轴无从对拍）：{', '.join(short_label(x) for x in skipped)}")
    lines.append(f"   ⇒ {'全部一致 ✓（独立实现 == 官方实现）' if bad == 0 else f'**{bad} 条不一致**（工具错）'}")
    lines.append(f"   `avg_rank_points` 容差 {RP_TOL}：生产者累加**未取整**的顺位点，而 `per_game` 里"
                 f"是按 0.1 分取整过的 ⇒ 末位有 1e-2 噪声（同 `tools/selfplay-check.mjs` 第 ③ 条）")
    return lines, bad


# ================================================================= 与现任同场对比

def short_label(lab: str, n: int = 20) -> str:
    """策略串 → 短名（取倒数第二段，即 `<...>/<模型名>/net.bin` 里的 `<模型名>`）。"""
    parts = [p for p in lab.replace("/", "\\").split("\\") if p]
    base = parts[-2] if len(parts) >= 2 else lab
    return base[:n]


def match_label(labels: Sequence[str], sub: str) -> str:
    """把 `net.bin` 路径 / 子串解析成**唯一**的策略标签（命中不唯一就报错，**不猜**）。"""
    hit = [l for l in labels if sub in l]
    if len(hit) != 1:
        raise SystemExit(f"--incumbent {sub!r} 命中 {len(hit)} 个策略标签：{hit or list(labels)}")
    return hit[0]


def vs_incumbent(stats: dict[str, PolicyStat], incumbent: str, metrics: Sequence[str] = SERIES_KEYS,
                 run_names: Sequence[str] | None = None) -> tuple[list[str], int]:
    """与现任**同场**的配对对比：**复用 `v4/arena.judge_pair`**（不另写检验）。

    ⚠ **符号**（与 `gate.decide` 同一条陷阱）：`judge_pair(sa, sb, a, b)` 的约定是
    `Δ = a − b`、**正 = `a` 更好**。这里要问的是"**该模型**比现任好吗" ⇒ 必须按
    `judge_pair(该模型, 现任, 该模型, 现任)` 调（候选在前）。写成 `(现任, 该模型, …)`
    会把每一行都反号 —— 而"更强的那一侧"读起来照样像结论。

    ⚠ **配对域 = 同一个 run**：序列的键是 `(run 序号, 墙 seed)`（`PolicyStat.add_series`），
    所以 `judge_pair` 取到的交集**只含"同一个 run 里候选与现任同场同墙"的那些场**。
    `run` 那一列打出这次配对用到的 run 序号（`[rN]` 见开头"读入 N 个 run"那张表），
    免得"跑了 16 个 run、实际只有 8 个进了这一行"这件事看不出来。

    返回 `(报告行, 该模型"显著更好"的轴数)`；`metrics` 里每一个都是**逐场序列**。
    """
    lines = ["== 与现任同场的配对对比（`arena.judge_pair` 口径；**正 = 该模型更好**，即 候选 − 现任） ==",
             f"   现任 = {incumbent}",
             f"   ⚠ 配对域 = **同一个 run**（该 run 里候选与现任同场同墙）；跨 run 的墙不配对、也不合并"
             f"（要合并走 `v4.gate` 的加键偏移 + 池化 `gate.pooled_diffs`）",
             f"   {'模型':<18}{'指标':<16}{'Δ':>12}{'95%CI':>24}{'p':>9}{'n':>7}{'run':>9}  判定"]
    n_better = 0
    rows = 0
    used_runs: set = set()
    overlap: list[str] = []
    if incumbent not in stats:
        return lines + ["   （这批 run 里没有现任 ⇒ 跳过）"], 0
    for lab in sorted(stats):
        for metric in metrics:
            sa = stats[incumbent].series.get(metric) or {}
            sb = stats[lab].series.get(metric) or {}
            keys = set(sa) & set(sb)
            if not keys:
                continue
            rows += 1
            rid = [k[0] for k in keys]
            used_runs.update(rid)
            # ⚠ 同一副墙在**多个 run** 里参与同一对配对 ⇒ 那些 run 若是重复跑（同 seed 基），
            #   合并就把同一副牌数了两遍、CI 假窄（同 `gate.pooled_diffs` 的那条判据）。
            #   现任自比那一行（Δ≡0 的退化对照）不算，否则多 tag 混跑时它必然满屏。
            if lab != incumbent and len(rid) > len({k[1] for k in keys}):
                overlap.append(f"{short_label(lab, 16)}·{metric}（{len(rid)} 场 / "
                               f"{len({k[1] for k in keys})} 副墙）")
            r = arena.judge_pair(sb, sa, lab, incumbent, metric)   # 候选在前 ⇒ 正 = 该模型更好
            if lab != incumbent and r.verdict == "更好":
                n_better += 1
            tag = "（现任自己：恒 0）" if lab == incumbent else ""
            lines.append(f"   {short_label(lab, 16):<18}{metric:<16}{r.delta:>+12.3f}"
                         f"{f' [{r.lo:+.3f},{r.hi:+.3f}]':>24}{r.p:>9.3f}{r.games:>7}"
                         f"{fmt_run_ids(rid):>9}  {r.verdict}{tag}")
    if overlap:
        lines.append("   ⚠ **同一副墙在多个 run 里进了同一对配对** ⇒ 那几行把同一副牌数了两遍"
                     "（CI 假窄，同 `gate.pooled_diffs` 的判据）：" + "；".join(overlap))
    lines.append(f"   汇总：{n_better} 个 (模型, 轴) 组合**显著更好**（CI 排除 0 且为正）。")
    if run_names:
        lines.append(f"   配对用到 {len(used_runs)} 个 run（共读入 {len(run_names)} 个）："
                     f"{fmt_run_ids(sorted(used_runs))}"
                     f"　—— 序号 → 目录见开头「读入 N 个 run」那张表")
    lines.append("   读法：Δ 是与**同一批牌山**上现任的逐场配对差（`place` 已取负、正 = 更好）；"
                 "CI 跨 0 ⇒ 这一轴分不出（牌山方差大，别拿单次跑分下结论）。")
    if rows == 0:
        lines.append("   ⛔ 一行都配不上：候选与现任**没有任何同一个 run** —— 本模块不跨 run 配对")
    return lines, n_better


# ================================================================= CLI

def _resolve_runs(paths_in: Sequence[str]) -> list[Path]:
    out: list[Path] = []
    for p in paths_in:
        d = Path(p)
        if not d.exists():
            raise SystemExit(f"--run 路径不存在：{d}")
        out += discover_runs(d)
    seen: set = set()
    uniq: list[Path] = []
    for d in out:
        r = str(d.resolve())
        if r not in seen:
            seen.add(r)
            uniq.append(d)
    return uniq


def _collect_labels(runs: Sequence[RunData]) -> list[str]:
    out: list[str] = []
    for rd in runs:
        for lab in (rd.by_policy or {}):
            if lab not in out:
                out.append(lab)
    for rd in runs:
        for h in rd.hands:
            for lab in h.policies:
                if lab and lab not in out:
                    out.append(lab)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mahjong_ml.v4 profile",
                                 description="模型画像：把自对弈产物摊成多维统计表"
                                             "（轴定义见模块 docstring）")
    ap.add_argument("--run", action="append", default=[],
                    help="自对弈产物目录（含 summary.json + g*.jsonl），或其父目录（gate/<tag>）；可重复")
    ap.add_argument("--self-check", action="store_true", help="纯函数单测（不读盘）")
    ap.add_argument("--cross-check", action="store_true",
                    help="与生产者 summary.json 的 by_policy 逐条对拍"
                         "（和率 / 铳率 / 平均打点 / 平均顺位点 / 平均顺位 / 平均收支）")
    ap.add_argument("--incumbent", default=None,
                    help="现任 net.bin（路径或唯一子串）：出「与现任同场」的 judge_pair 对比")
    ap.add_argument("--policy", default=None, help="只看这些策略（标签子串，逗号分隔）")
    ap.add_argument("--no-decisions", action="store_true",
                    help="不读决策行（行为轴缺席，但快得多）")
    ap.add_argument("--limit-files", type=int, default=None,
                    help="每个 run 只读前 N 个 g*.jsonl（⚠ 只给冒烟用，会让对拍红）")
    ap.add_argument("--json", dest="json_out", default=None, help="把画像写成机器可读 JSON")
    args = ap.parse_args(argv)

    if args.self_check:
        return self_check()
    if not args.run:
        ap.error("要么给 --run，要么用 --self-check")

    dirs = _resolve_runs(args.run)
    print(f"读入 {len(dirs)} 个 run：")
    runs: list[RunData] = []
    for i, d in enumerate(dirs):
        rd = load_run_data(d, with_decisions=not args.no_decisions, limit_files=args.limit_files)
        runs.append(rd)
        note = f"；{rd.trace_note}" if rd.trace_note else ""
        # `[rN]` 是**配对表的 run 列**用的序号（`RunData.dir` 的名字会撞：`gate/<tag>` 展开出来的
        # 子 run 都叫 `s<seed>`），所以图例必须打在这里。
        print(f"  [r{i}] {d}：{len(rd.hands)} 小局 / {len(rd.per_game)} 场 / "
              f"{0 if rd.decisions is None else len(rd.decisions)} 决策{note}")

    all_labels = _collect_labels(runs)
    label_filter = None
    if args.policy:
        subs = [x.strip() for x in args.policy.split(",") if x.strip()]
        label_filter = [l for l in all_labels if any(s in l for s in subs)]
        if not label_filter:
            raise SystemExit(f"--policy 一个都没命中；可选：{all_labels}")
        print(f"只看 {len(label_filter)} 个策略（--policy {args.policy}）")

    stats, table = reduce_run(runs, label_filter)
    if not stats:
        print("没有可画像的策略。")
        return 1

    checks = check_walls(runs)
    wall_bad = [c for c in checks if c.problem]
    same = bool(checks) and not wall_bad
    if not runs:
        print("\n牌山一致性：没有 run。")
    elif wall_bad:
        print(f"\n牌山一致性：**{len(wall_bad)}/{len(checks)} 个 run 内部各策略没打同一批墙 seed**"
              f" ⇒ 这些 run 的配对会漏场（不可比）：")
        for c in wall_bad:
            print(f"   [r{c.run_index}] {c.name}：{c.problem}")
    elif len(runs) == 1:
        print(f"\n牌山一致性：只有 1 个 run ⇒ 无「跨模型可比」可言（单模型画像）；"
              f"该 run 内部 {checks[0].labels} 个策略打的是同一批 {checks[0].walls} 副墙 ✓")
    else:
        walls = {int(s) for rd in runs for s in rd.seeds}
        print(f"\n牌山一致性：**逐 run 对齐** ✓ —— 判据是「**同一个 run 内部**各策略打的是不是"
              f"同一批墙 seed」，**不是**「各 run 的 seed 集合相不相同」：")
        print(f"   {len(checks)} 个 run，每个 run {checks[0].walls} 副墙（各策略同场同墙）；"
              f"全表不同的墙 seed 共 {len(walls)} 个。")
        print("   ⚠ 跨 run 的 seed 批次**天然不同**（`gate/<tag>` 展开出的 `s<seed>/` 就是 8 套"
              "**不同的墙**）——那不是错误。配对只在**同一个 run 内**成立（见 `--incumbent` 的 `run` 列）；"
              "跨 run 的合并归 `v4.gate`（加键偏移 + 池化，`gate.pooled_diffs`）。")

    decisions_ok = all(rd.decisions is not None for rd in runs)
    if not decisions_ok:
        print("⚠ 有 run 没有决策行 ⇒ **行为轴整列缺席**（立直率 / 追立率 / 副露率 / 和了巡目 / 默听率）")

    names = sorted(stats, key=lambda l: -(safe_div(stats[l].rank_point_sum, stats[l].games)
                                          if stats[l].games else 0))
    profiles = {n: axes_of(stats[n], table, decisions_ok=decisions_ok) for n in names}
    # 列名用短名（策略串是整条路径，直接当表头会把表撑爆）；重名时补序号
    colnames: list[str] = []
    for n in names:
        base = short_label(n, 18)
        cand, k = base, 2
        while cand in colnames:
            cand = f"{base}#{k}"
            k += 1
        colnames.append(cand)
    print()
    print(render_table({c: profiles[n] for c, n in zip(colnames, names)}, colnames, width=18))
    print("\n列（左 → 右按平均顺位点降序）：")
    for c, n in zip(colnames, names):
        print(f"  {c:<22} = {n}")
    print("\n轴注解（表里放不下的口径）：")
    seen_note: set = set()
    for n in names:
        for ax in profiles[n]:
            if ax.note and (ax.block, ax.name) not in seen_note:
                seen_note.add((ax.block, ax.name))
                print(f"  {ax.block} · {ax.name}：{ax.note}")
    if len(names) >= 2:
        print()
        print("\n".join(leader_report(profiles, colnames, names)))
    missing = [(b, nm) for n in names for (b, nm, v) in
               [(x.block, x.name, x.value) for x in profiles[n]] if v is None]
    uniq_missing = sorted({nm for _b, nm in missing if nm in MISSING_AXES or "默听" in nm})
    if uniq_missing:
        print("\n⛔ 取不到的轴（**要动契约**，本模块不猜）：" + "；".join(uniq_missing))

    rc = 0
    if args.cross_check:
        print()
        lines, bad = cross_check(stats, runs, label_filter)
        print("\n".join(lines))
        if bad:
            print("CROSS-CHECK FAIL（工具与生产者不一致 —— 工具错）")
            rc = 1

    if args.incumbent:
        inc = match_label(all_labels, args.incumbent)
        print()
        lines, n_better = vs_incumbent(stats, inc, run_names=table.get("run_names"))
        print("\n".join(lines))

    if args.json_out:
        payload = {
            "runs": [str(d) for d in dirs],
            # ⚠ 判据是**逐 run**的（同一个 run 内部各策略同场同墙）；**不是**"各 run 的 seed 集合相同"。
            #   旧名字 `same_walls` 装的是后者 ⇒ 换成 `run_aligned`（语义变了，名字也得换）。
            "run_aligned": bool(same),
            "run_walls": [{"run": c.run_index, "dir": c.name, "labels": c.labels,
                           "walls": c.walls, "problem": c.problem} for c in checks],
            "axes": {n: [{"block": a.block, "name": a.name, "value": a.value, "note": a.note}
                         for a in profiles[n]] for n in names},
            "raw": {n: {k: getattr(stats[n], k) for k in
                        ("games", "seat_hands", "wins", "deal_ins", "delta_sum", "win_score_sum",
                         "win_base_sum", "win_base_n", "deal_in_sum", "deal_in_n",
                         "tsumo_against_n", "tsumo_loss_sum", "dealer_hands", "dealer_renchan",
                         "riichi", "riichi_chase", "open_call_hands", "ankan_hands",
                         "decisions", "turn_decisions", "seat_hands_with_turn",
                         "yaku_hands", "yaku_count_sum", "compound_wins", "han_sum",
                         "yakuman_wins")} | {
                        "tier_counts": stats[n].tier_counts,
                        "limit_counts": stats[n].limit_counts,
                        "yaku_freq": stats[n].yaku_freq,
                        "place_counts": stats[n].place_counts,
                        "win_turns": len(stats[n].win_turns)}
                    for n in names},
            "table": table,
        }
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
        print(f"\n已写出 {args.json_out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
