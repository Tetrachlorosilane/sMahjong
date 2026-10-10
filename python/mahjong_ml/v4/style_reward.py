# -*- coding: utf-8 -*-
"""**风格奖励塑形**（`--style-bonus`）：把"过程轴"做成可配置、可关闭、可审计的一路奖励。

## 这个模块治的是什么病

上一轮实测：默认奖励下训练**总是往"打点"漂**（两条不同名的线都出现"打点 ↑、和了率 ↓"）。
根因不是"奖励写错了"，而是**方差不均**：小局收支（`delta`）的点数量纲 std 在 5 千点量级，
而"立直了没有"这种 0/1 指示量的 std 只有 ~0.36（p≈0.2）—— 同一个优势里混着两把量纲完全
不同的尺子，梯度自然被大方差那一轴支配（第十九轮量到过同类事故：`std(A)/std(delta)=2.671×`）。
所以本模块的**全部要点**就是：把要推的那一轴**白化**（减均值、除标准差）之后，再按权重加进奖励。

    r_hand' = r_hand + Σ_i w_i · z_i,      z_i = (x_i − μ_i) / σ_i

## 口径的合同（与 `tools/style-vector.py` 的**独立实现**对齐，⛔ 这里不许 import 它）

本仓的既定分工：**判据独立实现**。所以 `tools/style-vector.py` 是"测量"那一份（判据），
本模块是"训练"那一份（被塑形的对象），两边各自实现同一套分子/分母口径，由对账钉住：

| 轴 | 分子 | 分母（= 一个小局 × 一个座位） | 与 `style-vector.py` 的对应 |
| --- | --- | --- | --- |
| `riichi` | 该席本小局**宣言过立直**（`obs.riichi[seat]` / `obs.riichi_turn[seat]>0` / `chosen` 以 `riichi:` 开头，三者取或） | 该席本小局**在轨迹里有决策行** | `riichi_rate` |
| `meld` | 该席本小局**有副露**（`obs.melds[seat]` 非空 / `chosen` 以 `chi:`/`pon:`/`kan:` 开头） | 同上 | `meld_rate` |
| `win` | `hand_winner == seat` 且本行 `hand_agari` | 同上 | `win_rate` |
| `deal` | `hand_loser == seat` | 同上 | `deal_rate` |
| `no_deal` | `hand_loser != seat`（含流局） | 同上 | `1 − deal_rate` |
| `win_points` | `hand_winner == seat` 时 `hand_delta[seat]`，否则 0（点） | 同上 | `avg_win_score` 的分子（÷ 和了次数才是比率） |

⚠ **分母的一处刻意差异**（唯一一处，写在这里免得以后当 bug 查）：`style-vector.py` 的分母是
"场号 × 席位标签 × 该场小局行数"（**含**一次询问都没有的小局，例如庄家第一张就被荣和）；
本模块的分母是"轨迹里**真的有决策行**的 (小局, 座位)"。差集只有"整局没有任何决策行"的
(小局, 座位) —— 那些行在数据集里**根本不存在**，对梯度没有任何贡献（不存在 = 塑形不到），
所以拿它们去算 μ/σ 只会让白化参数偏离真正参与训练的那个分布。判据留在 `selfcheck.py`。

## 成对质量轴（⛔ 防"奖励被钻空子"）

`--style-bonus 'riichi=w:PAIR'` 里的 `PAIR` 是一条**伴随质量量**：**只有它为真时才付这笔奖励**。
为什么要它：奖励"立直率"而不管质量，等价于告诉策略"立直就行"—— 而"无脑立直"（听个边张也宣言、
拿着无役愚形也宣言）在立直麻将里是**负收益**打法 ⇒ 奖励会被钻空子，训练出来的是"爱立直"而不是
"会立直"。所以预注册（`S:\\mahjong-training\\style-reward\\PREREGISTRATION.md`）用的是
`riichi=1.0:no_deal`：**立直且没有放铳**才拿得到这笔钱 —— 它把"立直"与"立直之后别点炮"
绑成一条质量轴，同时仍然只推"立直率"这一个**方向**。

⛔⛔ **绝不支持 `PAIR = 恒真`**：`riichi=1.0:true` 会被 `parse_bonus` 直接拒绝（见那里的注释）。
理由：那正是"只按立直了付奖励"，是这个模块**唯一**要防的事。

## 白化的口径（硬要求）

* **本数据集实测**：μ/σ 从**这一代这一份数据集**上现算（不是历史常数）—— 每一代的策略分布、
  桌上对手都不一样，拿上一代的 μ/σ 去白化这一代，等于白化到错的分布上。
* **总体选择**（`--style-bonus-whiten`）：`student`（缺省，给了 `--student` 时）=
  只用**学生座位**的小局（训练里真正进梯度的那些行）；`all` = 全部座位。
  ⚠ 用"全部座位"会把 teacher/对手的行为分布混进 μ/σ（学生行只占 1/4），白化就不准了 ——
  所以缺省取 student，且**必须**能在日志/meta 里看出来用的是哪一份。
* **统计对象**：Bernoulli 指示量用 `p`、`√(p(1−p))`（与 `x.std(ddof=0)` 逐位一致）；
  连续量（`win_points`）用样本均值与 `x.std(ddof=0)`。
  ⚠ **σ ≤ 0 必须报错**（不许 `1/0`，也不许悄悄跳过一个轴 —— "静默少一个轴"会让白化失效且不报错）。
* 白化后必须在数据集上核对 **均值 ≈ 0、方差 ≈ 1**（`audit_whitening`；数字进日志与 meta）。

## 付奖模式：`hand`（缺省）/ `decision`（`--style-bonus-mode`，2026-10-10 加）

**动机（上一轮的实证，见 `S:\\mahjong-training\\w-ladder\\PROBE-w14000.md`）**：`hand` 口径下奖励是
**小局级常数、该小局四行同值** ⇒ 对手级行为（"要不要立直"）**几乎没有信度分配**：同一小局里
学生那十几行决策拿到**同一个**增量，PPO 只被"整手结局"间接带动。实测把塑形 std 抬到结果奖励的
**2.52×**（`w=14000`）立直率仍只动 **+0.39pp**（同配方 g01 噪声地板 0.85pp）⇒ **不是尺度问题**。
于是要问的是：**把信度分配到"立直那一手"之后，立直率能不能被推上去**。

所以两种模式的差别**只有一处**：那笔奖励**落在哪一行**。

| | `hand`（缺省，老口径**逐位不变**） | `decision` |
| --- | --- | --- |
| 付奖行 | 该小局的**每一行**（常数） | **只有真的执行了该轴动作、且我就是被记录的那一家的那一行**（`chosen` 以该轴的动作键开头；见下面"多家荣和"），其余行 = **0** |
| 白化的总体 | 小局（四席取或成一份） | **学生席的决策行**（逐行；见下面"为什么"） |
| 伴随质量轴 `PAIR` | 按该小局判定 | **完全一样**（仍按该小局判定，⛔ 不改成"按那一行"） |
| 动作行找不到 | 不适用 | ⛔ **当场报错**（`BonusSpecError`），**绝不**退化成 `hand` |

**为什么 `decision` 要把白化换成"逐行"（这是本模式唯一一处不显然的口径）**：
`hand` 模式下 `std(塑形列) ≈ w`（白化后那一轴的标准差就是 `w`）⇒ 上一轮说的"`w=6170` = 真实 1:1 尺度"
就是"**std(塑形列) ≈ std(delta)**"。若 `decision` 模式**沿用**手级 μ/σ（μ≈0.286、σ≈0.452），
付奖只落在 ~1/15 的行上 ⇒ 列的 std 掉到 `w` 的 ~0.2× ⇒ **剂量**被顺手改掉，读数会因为"钱变少了"
而不是"信度分配变了"变平（那是这类实验最经典的混杂）。**逐行白化**让 `1[这一行做了动作]` 先归一化，
付奖行拿到 `w·(1−μ_row)/σ_row`，于是 `std(塑形列) ≈ w` **两种模式同一个量级** ⇒ 唯一被改动的变量
是**信度落在哪一行**。两条 std 都会打进日志与审计账（⛔ 不靠"看起来差不多"）。

**`decision` 模式只对"行级动作"的轴成立**：`riichi` / `meld` / `win`（动作键见 `PROTOCOL.md` §8.3）。
`deal` / `no_deal` / `win_points` 是**结局量**、没有"哪一行做了它" ⇒ 拿它们当**付奖轴**一律**报错**
（⛔ 不猜一行、也不静默退回 `hand`）。它们当**伴随质量轴**（`PAIR`）照常可用 —— 质量轴一直是
小局级判定的。

### ⚠ 多家荣和（双响）的语义陷阱 —— `win` 轴的行级判据必须多一条合取

**手级** `AXES['win']` 的口径 = "该席是**被记录的和了者**"（`hand_winner == seat`；生产端只写
`winners[0]` = **离放铳者最近那家**，`round.cpp`/`Round.agariRon` + `headBump`）。
**行级**若只判"这一行执行了 `tsumo`/`ron`"，那么**双响时两家都为真** —— 而其中只有一家是
`hand_winner`（另一家也真的收到点棒，但供託/本场只归 `winners[0]`）。实测 **30 / 11,258 小局
（0.27%）** 对不上：手级指示量 = 0（不是被记录的那家）却有动作行 ⇒ 白化那一遍就抛
`BonusSpecError`（"取不到哪一行做了这个动作"）。所以行级判据是

    1[这一行做了轴 i 的动作]  ∧  1[我就是被记录的那家（`ROW_SELF_SEAT`，只有 `win` 需要）]

**语义后果（写清，别当 bug 查）**：双响里**非"最近那家"的宣和席拿不到这笔奖励** —— 学生和了行里
约 **1.3%** 属于这种。所以这笔奖励是**软信号少付**（把"和了"这件事付给真正被记账的那家），
**不是"判据换了尺子"**：⛔ **判据侧一律仍用"手级被记录和了者"**（`tools/style-vector.py` 的
`win_rate` 与 `AXES['win']` 一个字都不许动）—— 换尺子会让"训练奖励"和"测量口径"错位。
（备选方案 (a)：把生产端的 `hand_winner` 改成"多家都算"—— 那要同时改 `style-vector.py` 与
服务端 `winners[0]` 口径，属于**未来的语义升级**，本轮不做。）

## 列与"叠加一次"的纪律

数据集里存一列 `style_bonus`（float32，**点**，逐决策行）。⚠ 它的**行内结构随模式变**：
`hand` 模式同一小局四行同值（小局级常数）；`decision` 模式**只有动作行非 0**。
奖励**只在一处**叠加上去：`pretrain._hand_advantage` 的 `adv = (delta + style_bonus + 顺位点) − V(s)`。
⛔ 不把 bonus 折进 `delta` 列本身 —— 那样会波及 `_row_weights`（RWR/权重）与 `audit` 的口径，
"叠了一次还是两次"就再也说不清了。

## 逐位不变的判据（默认关闭）

`--style-bonus ''`（缺省）⇒ 不建 `style_bonus` 列（`load_split` 给 `None`）、
`loop` 的命令行里**一个字符都不多**、`pretrain` 一行都不走 ⇒ 与加这个功能之前**逐位相同**。
判据在 `python/selfcheck.py`（列不存在 + 命令逐字 + 权重哈希）。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path

#: **持久审计目录**的环境变量名（2026-10-09 加）。
#: 为什么需要：奖励账默认落在 `<紧凑集>/style-reward.json`（= `S:\mahjong-training\compact\<tag>\`），
#: 而 `tools\run-league.ps1` **每代结束会把 `compact\<tag>` 整目录轮换删掉** ⇒ 账跟着没了
#: （实测：上一轮 4 代的账一份都没活下来，只能从 `release\<tag>.log` 里摘文字）。
#: ⛔ `run-league.ps1` 不许改（它决定哈希基线）⇒ 改在**工具侧**：给了这个环境变量就**另写一份**
#: 到 `<dir>/<紧凑集目录名>.json`（紧凑集目录名就是 `tag`），并把**它**当作 `meta.audit_path`。
#: 副本与 compact 里那份**逐字节相同**（同一份 payload），不是第二套口径。
AUDIT_DIR_ENV = "MAHJONG_STYLE_AUDIT_DIR"

#: 一座桌几席（与 `style-vector.py` 的 `SEATS_PER_TABLE` 同一个数；写死在这里让"席位数"唯一）。
SEATS_PER_TABLE = 4

#: 动作键前缀（`docs/PROTOCOL.md` §8.3 的文法）—— 与 `style-vector.py` 同口径。
RIICHI_PREFIX = "riichi:"
MELD_PREFIXES = ("chi:", "pon:", "kan:")

#: 数据集里那一列的名字（唯一字面量：`dataset.py` / `pretrain.py` / 自检都引用它）。
COLUMN = "style_bonus"

# ---------------------------------------------------------------------------
# 付奖模式（`--style-bonus-mode`）
# ---------------------------------------------------------------------------

#: `hand`（缺省）= 这个功能从 2026-10-09 起的**老口径**：奖励是**小局级常数**、该小局四行同值。
MODE_HAND = "hand"
#: `decision` = **逐决策**付奖：奖励只落在"真的做了那个动作"的那一行，其余行为 0。见模块 docstring。
MODE_DECISION = "decision"
MODES: tuple[str, ...] = (MODE_HAND, MODE_DECISION)

#: 模式的**环境变量**入口（与 `--style-bonus` 同理：`tools/run-league.ps1` 不许改，
#: 所以"不改那个脚本也能换模式"这件事只能由环境变量 + `loop.py` 的缺省值完成）。
MODE_ENV = "MAHJONG_STYLE_MODE"


def check_mode(mode: str) -> str:
    """模式名的**唯一**校验点（未登记的模式一律当场报错，⛔ 不猜、不近似、不默默当 `hand`）。"""
    m = (mode or MODE_HAND).strip()
    if m not in MODES:
        raise BonusSpecError(f"付奖模式只认 {'|'.join(MODES)}（收到 {mode!r}）")
    return m


#: 轴 → "**这一行**是不是那个动作"的**前缀**判据（`STRICT_PREFIX` 模式）。
ROW_ACTION_PREFIX: dict[str, tuple[str, ...]] = {
    "riichi": (RIICHI_PREFIX,),
    "meld": MELD_PREFIXES,
}
#: 轴 → "**这一行**是不是那个动作"的**整串相等**判据（`tsumo` / `ron` 是无参数动作）。
ROW_ACTION_EXACT: dict[str, tuple[str, ...]] = {
    "win": ("tsumo", "ron"),
}
#: 能在 `decision` 模式下当**付奖轴**的那些轴。
ROW_ACTION_AXES: tuple[str, ...] = tuple(sorted(set(ROW_ACTION_PREFIX) | set(ROW_ACTION_EXACT)))

#: ★ **多家荣和的语义陷阱**（2026-10-10 加，见模块 docstring 同名小节）：行级判据除了"这一行
#: 执行了该轴的动作"，有些轴还要"**我就是被记录的那家**"。只有 `win` 需要它 —— 双响时两家都
#: 执行了 `ron`，而手级口径（`AXES['win']` + 生产端 `winners[0]`）只记**离放铳者最近那家**。
#: ⛔ 不加这条合取，手级与行级必然漂移（实测 30/11258 小局），白化那一遍就会抛 `BonusSpecError`。
ROW_SELF_SEAT: dict[str, str] = {"win": "winner"}

#: 违规**样例**最多留几条（⚠ 只影响"列几条给日志看"，**不影响**报出来的总数）。
#: 上一轮的事故：`if bad and len(viol) < 8: viol.append(...)` 之后拿 `len(viol)` 当"共 N 个小局"
#: 打印 ⇒ 日志**恒说 8**（真实是 30）—— 把"样例上限"当成了计数。
VIOL_SAMPLES = 8


def row_is_action(axis: str, chosen: str) -> bool:
    """**这一行**（`chosen` = 实际执行的动作键）是不是轴 `axis` 的那个动作。

    ⛔ 没有行级语义的轴（`deal` / `no_deal` / `win_points`）在这里**报错**，不返回 False ——
    返回 False 会让"这个轴在 decision 模式下付不出奖"变成**静默的 0**（正是本仓最忌讳的降级）。

    ⚠ 这只是行级判据的**一半**：`win` 轴还要过 `ROW_SELF_SEAT`（我就被记录的那家）那一关，
    两者合起来才是 `row_is_axis_action`。别在别处直接用这个函数决定"要不要付奖"。
    """
    chosen = str(chosen or "")
    if axis in ROW_ACTION_PREFIX:
        return chosen.startswith(ROW_ACTION_PREFIX[axis])
    if axis in ROW_ACTION_EXACT:
        return chosen in ROW_ACTION_EXACT[axis]
    raise BonusSpecError(
        f"轴 {axis!r} 在 `decision` 模式下没有'哪一行做了它'的定义（它是**结局量**，不是行级动作）。"
        f"能当付奖轴的只有：{', '.join(ROW_ACTION_AXES)}；它当**伴随质量轴**（`PAIR`）仍然可用。"
        f"⛔ 不猜一行、也不静默退回 `hand` 模式")


def row_is_axis_action(axis: str, seat: int, chosen: str, st: HandState) -> bool:
    """★ `decision` 模式的**唯一**行级判据：这一行**执行了该轴动作** ∧ **我就是被记录的那家**。

    `ROW_SELF_SEAT` 里登记过的轴（现在只有 `win`）带上第二条合取：多家荣和时**两家都执行了
    `ron`**，但被记录的和了者只有一家（`winners[0]` = 离放铳者最近那家）⇒ 只有**那一家**的
    和了行拿钱。⛔ 判据侧（`AXES`/`style-vector.py`）仍用"手级被记录和了者"，一个字都不动 ——
    这里是**软信号少付**（学生和了行里约 1.3%），不是"换了尺子"。
    """
    if not row_is_action(axis, chosen):
        return False
    if ROW_SELF_SEAT.get(axis) == "winner":
        return st.winner == seat
    return True


# ---------------------------------------------------------------------------
# 轴定义
# ---------------------------------------------------------------------------

#: **成对质量轴**（`PAIR`）的名字表：**只有它为真时才付奖**。
#: 表里**没有**、也**不允许**出现"恒真"（见 `parse_bonus`）—— 那正是"只按立直了付奖"的写法。
PAIR_KEYS: dict[str, tuple[str, str]] = {
    "no_deal": ("该小局**未放铳**（`hand_loser != seat`，含流局）",
                "把'立直'与'立直之后别点炮'绑成一条质量轴：奖励立直率时不会去换放铳率"),
    "win": ("该小局**和了**（`hand_winner == seat`）",
            "质量轴 = 立直且和了（更严；但'立直后被别人自摸'也会被判成没质量，会更偏向早立直）"),
    "deal": ("该小局**放铳**（⚠ 反向质量轴：只有放铳才付奖 —— 只留给负向对照，别用于正推）",
             "负向对照用；正推里用它等于奖励放铳"),
}

#: 轴定义表（**唯一数据源**：`parse_bonus` 校验用它、审计打印用它、自检引用它）。
#: `kind` 只决定白化用哪套 μ/σ（`bernoulli` = `p, √(p(1−p))`；`continuous` = 样本均值/标准差）。
AXES: dict[str, dict] = {
    "riichi": {
        "kind": "bernoulli",
        "meaning": "立直率（该席本小局宣言过立直）",
        "src": "obs.riichi[seat] / obs.riichi_turn[seat] > 0 / chosen 以 riichi: 开头（三者取或）",
        "style_vector": "riichi_rate = riichi_hands / seat_hands",
    },
    "meld": {
        "kind": "bernoulli",
        "meaning": "副露率（该席本小局有吃/碰/杠任一，含暗杠）",
        "src": "obs.melds[seat] 非空 / chosen 以 chi:/pon:/kan: 开头",
        "style_vector": "meld_rate = meld_hands / seat_hands",
    },
    "win": {
        "kind": "bernoulli",
        "meaning": "和了率（该席本小局是和了者）",
        "src": "决策行的 hand_agari 且 hand_winner == seat",
        "style_vector": "win_rate = wins / seat_hands",
    },
    "deal": {
        "kind": "bernoulli",
        "meaning": "放铳率（该席本小局是放铳者）",
        "src": "决策行的 hand_loser == seat",
        "style_vector": "deal_rate = deals / seat_hands",
    },
    "no_deal": {
        "kind": "bernoulli",
        "meaning": "未放铳率（放铳率的补）",
        "src": "决策行的 hand_loser != seat（含流局）",
        "style_vector": "1 − deal_rate",
    },
    "win_points": {
        "kind": "continuous",
        "meaning": "小局打点（和了就取 `hand_delta[seat]`，没和就是 0；**点**）",
        "src": "决策行的 hand_delta[seat]（和了者）否则 0",
        "style_vector": "avg_win_score 的分子口径（win_points）",
    },
}

#: `PAIR` 里被允许的两个"恒假"哨兵（**只用于红证/负向对照**，不参与任何正推）：
#: `none` = 永不付奖（用来证明"成对质量轴确实在拦"）。
PAIR_NEVER = "none"


class BonusSpecError(SystemExit):
    """`--style-bonus` 写法错误 —— 一律**当场报错**，不静默降级（静默降级 = 塑形没了但不报）。"""


# ---------------------------------------------------------------------------
# ① 解析
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BonusAxis:
    """一个轴的完整配置（`name=w:PAIR`）。"""

    name: str
    weight: float
    pair: str = ""

    def describe(self) -> str:
        return f"{self.name}={self.weight:g}" + (f":{self.pair}" if self.pair else "")


@dataclass(frozen=True)
class BonusSpec:
    """`--style-bonus` 的解析结果（**不可变**；`axes` 的顺序 = 命令行里的顺序）。"""

    axes: tuple[BonusAxis, ...]
    raw: str = ""

    def __bool__(self) -> bool:
        return bool(self.axes)

    def describe(self) -> str:
        return "; ".join(a.describe() for a in self.axes)


def parse_bonus(text: str | None) -> BonusSpec:
    """`'riichi=1.0:no_deal'`（多轴用 `,` 分隔）→ `BonusSpec`。

    文法（每个轴）：`NAME[=WEIGHT][:PAIR]`
      · `NAME` ∈ `AXES`（未登记的轴**报错**，⛔ 不猜、不近似）；
      · `WEIGHT` 缺省 1.0；必须是有限实数（`nan`/`inf` 一律报错）；
      · `PAIR` ∈ `PAIR_KEYS` 或 `none`（`none` = 恒假，只给红证用）。

    ⛔ **没有 `PAIR` 就不许用**（`riichi=1.0` 直接报错）—— 见模块 docstring 的"成对质量轴"：
    只按"立直了"付奖励会学出无脑立直，那是这个模块唯一要防的事。要"不设质量轴"请显式写
    `:none` 并**说清为什么**（正推里那等于关掉防钻空子的那一层）。
    """
    text = (text or "").strip()
    if not text:
        return BonusSpec(axes=())
    out: list[BonusAxis] = []
    seen: set[str] = set()
    for item in text.split(","):
        item = item.strip()
        if not item:
            raise BonusSpecError(f"--style-bonus 里有空项（{text!r}）—— 逗号分隔的每一项都要写成 NAME=w:PAIR")
        head, sep, pair = item.partition(":")
        name, eq, wtext = head.partition("=")
        name = name.strip()
        if name not in AXES:
            raise BonusSpecError(
                f"--style-bonus 里有未登记的轴 {name!r}；已登记：{', '.join(sorted(AXES))}"
                f"（⛔ 不静默忽略 —— 忽略等于「这个轴没生效」却不报错）")
        if name in seen:
            raise BonusSpecError(f"--style-bonus 里轴 {name!r} 出现了两次（权重会互相覆盖，别这么写）")
        seen.add(name)
        weight = 1.0
        if eq:
            try:
                weight = float(wtext.strip())
            except ValueError:
                raise BonusSpecError(f"轴 {name!r} 的权重不是数：{wtext!r}") from None
        if not math.isfinite(weight):
            raise BonusSpecError(f"轴 {name!r} 的权重必须是有限实数（收到 {weight!r}）")
        if not sep:
            raise BonusSpecError(
                f"轴 {name!r} 没有写**成对质量轴**（`{name}=w:PAIR`）。⛔ 只按 `{name}` 付奖励会学出"
                f"无脑打法（那是本模块唯一要防的事）。要质量轴请挑一个：{', '.join(sorted(PAIR_KEYS))}；"
                f"确实要不设质量轴就显式写 `{name}={weight:g}:{PAIR_NEVER}` 并在预注册里说明理由")
        pair = pair.strip()
        if pair not in PAIR_KEYS and pair != PAIR_NEVER:
            raise BonusSpecError(
                f"轴 {name!r} 的成对质量轴 {pair!r} 不认识；可选：{', '.join(sorted(PAIR_KEYS))}"
                f"（或 {PAIR_NEVER} = 恒假，只给红证用）")
        out.append(BonusAxis(name=name, weight=weight, pair=pair))
    return BonusSpec(axes=tuple(out), raw=text)


# ---------------------------------------------------------------------------
# ② 逐小局提取指示量（**必须从轨迹的事件算**：见 `docs/PROTOCOL.md` §8.4）
# ---------------------------------------------------------------------------

def _seat_riichi(obs: dict, chosen: str) -> bool:
    """该席本小局**是否宣言过立直**：三个来源取或（与 `style-vector.py` 同一口径）。

    ⚠ 为什么要三个来源：`obs.riichi[seat]` 是"**已经**宣言过"的公开状态位，
    **宣言那一手的 obs 它还是 false**（服务端先广播 `riichi` 再广播 `discard` 那套时序）；
    而 `style-vector.py` 也是这么取或的 —— 少一个来源就会丢掉"本局最后一次询问就是立直"的那批。
    """
    if chosen.startswith(RIICHI_PREFIX):
        return True
    riichi = obs.get("riichi") or [False] * SEATS_PER_TABLE
    rt = obs.get("riichi_turn") or [0] * SEATS_PER_TABLE
    seat = int(obs.get("seat", -1))
    if 0 <= seat < SEATS_PER_TABLE:
        if seat < len(riichi) and bool(riichi[seat]):
            return True
        if seat < len(rt) and int(rt[seat]) > 0:
            return True
        return False
    # `obs.seat` 缺字段时退化成"四家取或"（宁可多算，不可漏算）
    return bool(any(riichi)) or any(int(x) > 0 for x in rt)


def _riichi_turn(obs: dict) -> int:
    seat = int(obs.get("seat", -1))
    rt = obs.get("riichi_turn") or [0] * SEATS_PER_TABLE
    if 0 <= seat < len(rt):
        return int(rt[seat])
    return int(max(rt)) if rt else 0


@dataclass
class HandState:
    """一个 (game, hand_no) 上的**逐席公开状态**（只装本模块要用的量）。"""

    riichi: list[bool] = field(default_factory=lambda: [False] * SEATS_PER_TABLE)
    meld: list[bool] = field(default_factory=lambda: [False] * SEATS_PER_TABLE)
    riichi_turn: list[int] = field(default_factory=lambda: [0] * SEATS_PER_TABLE)
    #: 逐席本小局的收支（点；来自决策行的 `hand_delta`，四家数组）
    delta: list[int] = field(default_factory=lambda: [0] * SEATS_PER_TABLE)
    agari: bool = False
    winner: int = -1
    loser: int = -1
    rows: int = 0
    #: 逐席这一小局的策略串（从决策行的 `policy` 字段来）—— 只用来判"这个座位是不是学生"，
    #: 从而决定白化的**统计总体**（见 `whitening_stats` 的 `student_policies`）。
    policy: list[str] = field(default_factory=lambda: [""] * SEATS_PER_TABLE)
    #: ★ **逐决策行**的最小记录 `(seat, chosen)`（`decision` 模式要回答"**这一行**做了什么"）。
    #: ⚠ 只存这两样：手级指示量已经在上面那几列里，多存一份 obs 只是白占内存（一手 ~60 行）。
    row_seat: list[int] = field(default_factory=list)
    row_chosen: list[str] = field(default_factory=list)

    def seat_is_student(self, seat: int, student_policies: set[str]) -> bool:
        """该席本小局是不是"学生"（= 白化统计要不要算它）。

        ⚠ 判据是**本小局自己的策略串**（不是全局 seat 集合）：风格桌上学生那一席
        由 `--rotate-perm` 在四家轮转 ⇒ 拿一个固定 seat 集合去筛会整份筛错（还不报错）。
        """
        return 0 <= seat < SEATS_PER_TABLE and self.policy[seat] in student_policies

    def feed(self, row: dict) -> None:
        """吃一条 `decision` 行（**唯一**累积点：所有指示量都从这里来）。"""
        obs = row.get("obs")
        if not isinstance(obs, dict):
            raise BonusSpecError(f"决策行没有 obs（game={row.get('game')} hand={row.get('hand_no')}）"
                                 f"—— 没有 obs 就算不出风格量，不猜")
        seat = int(row.get("seat", obs.get("seat", -1)))
        chosen = str(row.get("chosen") or "")
        # ★ 逐决策行的记录（`decision` 模式唯一的"哪一行做了动作"来源）。
        #   ⚠ 与 `st.riichi[seat]` 这类**手级**状态分开：后者是"本小局宣言过没有"，
        #   前者是"**这一行**是不是宣言那一行"（服务端先广播 `riichi` 再广播 `discard`
        #   ⇒ 宣言之后的每一行 `obs.riichi[seat]` 都是 true，不能拿它当行级判据）。
        self.row_seat.append(seat)
        self.row_chosen.append(chosen)
        if 0 <= seat < SEATS_PER_TABLE:
            self.policy[seat] = str(row.get("policy") or "")
            if _seat_riichi(obs, chosen):
                self.riichi[seat] = True
            self.riichi_turn[seat] = max(self.riichi_turn[seat], _riichi_turn(obs))
            if chosen.startswith(MELD_PREFIXES):
                self.meld[seat] = True
        # 副露是**公开状态**：随便哪一席的 obs 都能读出别家（`--no-claims` 只丢鸣牌决策行）
        melds = obs.get("melds") or []
        for s in range(min(SEATS_PER_TABLE, len(melds))):
            if melds[s]:
                self.meld[s] = True
        # 小局级结局：四家数组，逐行重复出现 ⇒ 直接覆盖（同一小局内恒定）
        hd = row.get("hand_delta")
        if isinstance(hd, list) and len(hd) == SEATS_PER_TABLE:
            self.delta = [int(x) for x in hd]
        self.agari = bool(row.get("hand_agari"))
        w = row.get("hand_winner")
        self.winner = int(w) if isinstance(w, int) else -1
        lo = row.get("hand_loser")
        self.loser = int(lo) if isinstance(lo, int) else -1
        self.rows += 1


def indicator_values(st: HandState) -> list[dict[str, float]]:
    """一个小局 → **四席各一份**指示量字典（值 = 该席在该轴上的原始量）。

    ⚠ 分母口径 = "该席在这一小局里**真的有决策行**"由调用方保证（调用方只把出现在轨迹里的
    小局喂进来；`style-vector.py` 那一处刻意差异见模块 docstring）。
    """
    out: list[dict[str, float]] = []
    for s in range(SEATS_PER_TABLE):
        win = bool(st.agari) and st.winner == s
        deal = st.loser == s
        vals = {
            "riichi": 1.0 if st.riichi[s] else 0.0,
            "meld": 1.0 if st.meld[s] else 0.0,
            "win": 1.0 if win else 0.0,
            "deal": 1.0 if deal else 0.0,
            "no_deal": 0.0 if deal else 1.0,
            "win_points": float(st.delta[s]) if win else 0.0,
        }
        out.append(vals)
    return out


def pair_ok(pair: str, vals: dict[str, float]) -> bool:
    """**成对质量轴**是否为真（`none` 恒假 —— 只给红证用）。"""
    if pair == PAIR_NEVER:
        return False
    if pair == "no_deal":
        return vals["no_deal"] > 0.5
    if pair == "win":
        return vals["win"] > 0.5
    if pair == "deal":
        return vals["deal"] > 0.5
    raise BonusSpecError(f"成对质量轴 {pair!r} 没有实现（`PAIR_KEYS` 与 `pair_ok` 漂移了）")


def paid_indicator(ax: BonusAxis, vals: dict[str, float]) -> float:
    """**付奖用的指示量**：`x` 当且仅当伴随质量量为真，否则 0。

    ⚠ 这是"成对质量轴"的**唯一落点**：白化的对象是 `x`（未加质量门），付奖的对象是
    `1[PAIR]·x`。两者必须分开 —— 混在一起（把质量门折进白化）会让 μ/σ 变成
    "只在那批小局上"的统计，白化就不是对边际分布做的了。
    """
    return vals[ax.name] if pair_ok(ax.pair, vals) else 0.0


def iter_hands(path: Path):
    """流式产出 `(game, hand_no, HandState)` —— **一行一行读，不把整个文件读进内存**（2.7 MB/场）。"""
    key = None
    st: HandState | None = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "decision":
                continue
            k = (int(row.get("game", -1)), int(row.get("hand_no", -1)))
            if k != key:
                if st is not None and st.rows:
                    yield (*key, st)                        # type: ignore[misc]
                key, st = k, HandState()
            st.feed(row)                                     # type: ignore[union-attr]
    if st is not None and st.rows and key is not None:
        yield (*key, st)


# ---------------------------------------------------------------------------
# ③ 白化（μ/σ 从**本数据集**实测）+ 付奖
# ---------------------------------------------------------------------------

@dataclass
class Whitening:
    """一个轴的白化参数（μ/σ 都来自**本数据集**实测）。"""

    name: str
    kind: str
    mu: float
    sd: float
    n: int
    n_all: int

    def to_json(self) -> dict:
        return {"axis": self.name, "kind": self.kind, "mu": self.mu, "sd": self.sd,
                "n_used": self.n, "n_seen": self.n_all}


@dataclass
class Audit:
    """付奖的**可审计**账（每轴：w / μ / σ / 付奖小局数 / 付奖总额）。"""

    axes: list[dict] = field(default_factory=list)
    hands_total: int = 0
    hands_eligible: int = 0
    whiten_scope: str = ""
    spec: str = ""
    #: 付奖模式（`hand` / `decision`）—— 账里**必须**有它：同样一份 `μ/σ/付奖` 读数在两种模式下
    #: 的含义不同（`decision` 的 μ/σ 是**逐行**的），不写下来就没法复判。
    mode: str = MODE_HAND
    #: `decision` 模式：进入白化统计的**行**数（`hand` 模式下 = 0，不写进账）。
    rows_used: int = 0
    #: 逐轴：付奖的 (小局, 座位) 数（**带上限**，免得一个 1000 场的采集把 JSON 撑爆）
    paid_max_rows: int = 100000

    def to_json(self) -> dict:
        return {"spec": self.spec, "mode": self.mode, "whiten_scope": self.whiten_scope,
                "hands_total": self.hands_total, "hands_eligible": self.hands_eligible,
                "rows_used": self.rows_used,
                "axes": self.axes}


def indicator_cells(st: HandState, student_policies: set[str] | None):
    """一个小局 → **参与统计的 (席, 指示量字典)** 列表。

    ★ **唯一**的"总体"定义（`whitening_stats` / `audit_whitening` / `paid_accounting` 都调它）：
    `student_policies is None` ⇒ 四席全要；否则只留 `policy` 命中学生串的那些席。
    为什么要这么严：白化的 μ/σ 与"自证均值 0/方差 1"必须**在同一份样本上**，
    两处各写一份筛选条件必然漂移（而漂移的症状是"自证过了、白化其实是错的"这种静默失真）。
    """
    vals_all = indicator_values(st)
    if student_policies is None:
        return [(s, vals_all[s]) for s in range(SEATS_PER_TABLE)]
    return [(s, vals_all[s]) for s in range(SEATS_PER_TABLE)
            if st.seat_is_student(s, student_policies)]


# ---------------------------------------------------------------------------
# ②b `decision` 模式：逐行动作 / 逐行白化总体 / 逐行付奖
# ---------------------------------------------------------------------------

def rows_in_scope(st: HandState, student_policies: set[str] | None):
    """这一小局里**在统计范围内**的决策行：`[(seat, chosen), ...]`。

    ⚠ 与 `indicator_cells` 共用**同一套**范围判据（`seat_is_student`）—— 行级白化的 μ/σ 与
    "这一行该不该付奖"必须是同一份总体，两处各写一份筛选必然漂移。
    """
    out = []
    for s, c in zip(st.row_seat, st.row_chosen):
        if not (0 <= s < SEATS_PER_TABLE):
            continue
        if student_policies is None or st.seat_is_student(s, student_policies):
            out.append((s, c))
    return out


def require_row_axes(spec: BonusSpec) -> None:
    """`decision` 模式的前置闸门：每个**付奖轴**都必须有"哪一行做了它"的定义。

    ⛔ 这里**必须报错**（不是打印告警、也不是退回 `hand`）：一个没有行级语义的付奖轴在
    `decision` 模式下**付不出任何奖**，静默跑完等于"这次实验什么都没做"。
    """
    bad = [a.name for a in spec.axes if a.name not in ROW_ACTION_AXES]
    if bad:
        raise BonusSpecError(
            f"`--style-bonus-mode decision` 不支持这些付奖轴：{', '.join(bad)}"
            f"（它们是**结局量**，没有'哪一行做了它'）。能当付奖轴的只有："
            f"{', '.join(ROW_ACTION_AXES)}。⛔ 不猜一行、也不静默退回 `hand` 模式")


def axis_row_action_counts(spec: BonusSpec, st: HandState,
                           student_policies: set[str] | None) -> dict[str, int]:
    """逐轴：这一小局在范围内**真的执行了该轴动作**（且 `win` 轴还要"我就是被记录的那家"）的行数。"""
    rows = rows_in_scope(st, student_policies)
    return {ax.name: sum(1 for s, c in rows if row_is_axis_action(ax.name, s, c, st))
            for ax in spec.axes}


def spec_axis_pay(spec: BonusSpec, stats: dict[str, Whitening], ax: BonusAxis) -> float:
    """`decision` 模式里**动作行**拿到的那笔钱（点）：`w·(1−μ)/σ`（**唯一落点**，别处再算一遍必漂移）。"""
    w = stats[ax.name]
    return float(ax.weight * (1.0 - w.mu) / w.sd)


def decision_bonus(spec: BonusSpec, stats: dict[str, Whitening], st: HandState,
                   seat: int, chosen: str, own_policies: set[str] | None = None
                   ) -> tuple[float, dict[str, float]]:
    """`decision` 模式：**一行**决策 → 奖励（点）+ 逐轴明细。

    口径：`Σ_i w_i · 1[PAIR_i(这一小局)] · 1[这一行做了轴 i 的动作] · (1 − μ_i)/σ_i`，其余行 = **0**。

    * `1[PAIR_i]` 按**小局**判定（与 `hand` 模式逐字相同：`_merge_cells` + `pair_ok`）—— 需求就是
      "伴随质量轴 `no_deal` 仍按该小局是否放铳判定"，⛔ 不是"按那一行判"。
    * `(1 − μ_i)/σ_i` 里 μ/σ 是**逐行**白化参数（`whitening_stats(..., mode="decision")`）——
      见模块 docstring"为什么 `decision` 要把白化换成逐行"。⚠ 非动作行**不付**那个 `−μ/σ` 的负偏移
      （付了就等于"每一行都付奖"，`付奖行数 == 动作行数` 这条判据当场失效）。
    * ★ 行级判据 = `row_is_axis_action`（不是 `row_is_action`）：`win` 轴还要"**我就是被记录的那家**"
      （多家荣和时两家都执行了 `ron`，只有 `winners[0]` 那家算 —— 见模块 docstring 同名小节）。
    * 席位是否在范围内由调用方保证（`rows_in_scope`）；这里**不**再判一次。
    """
    total = 0.0
    detail: dict[str, float] = {}
    for ax in spec.axes:
        cells = [v for _s, v in indicator_cells(st, own_policies)]
        merged = _merge_cells(cells)
        hit = pair_ok(ax.pair, merged) and row_is_axis_action(ax.name, seat, chosen, st)
        c = spec_axis_pay(spec, stats, ax) if hit else 0.0
        detail[ax.name] = c
        total += c
    return total, detail


def decision_violations(spec: BonusSpec, st: HandState,
                        student_policies: set[str] | None) -> list[tuple[str, str]]:
    """`decision` 模式的**自救判据**（需求 ④）：两手口径必须逐手对上。

    * 手级指示量 = 1 但**找不到**动作行 ⇒ "哪一行做了它"取不到 ⇒ **必须报错**（⛔ 不许退化成 `hand`）；
    * 手级指示量 = 0 却有动作行 ⇒ 两套口径漂移（同样是错，不能静默）。

    ★ 行级判据用 `row_is_axis_action`（含 `win` 的"**我就是被记录的那家**"合取）。上一轮只判
    "这一行执行了动作" ⇒ 双响里非 `winners[0]` 那家的 `ron` 行被判成动作行，而手级指示量是 0
    ⇒ 实测 30/11258 小局被这条**误判**成"口径漂移"（修法见模块 docstring 同名小节）。

    @return `[(轴, 原因)]`（空列表 = 这一小局没问题）。
    """
    rows = rows_in_scope(st, student_policies)
    if not rows:
        return []
    merged = _merge_cells([v for _s, v in indicator_cells(st, student_policies)])
    out: list[tuple[str, str]] = []
    for ax in spec.axes:
        hit = [c for s, c in rows if row_is_axis_action(ax.name, s, c, st)]
        hx = merged[ax.name] > 0.5
        if hx and not hit:
            out.append((ax.name, "手级指示量=1（本小局发生过这个动作），但**范围内一行都没有**执行它"
                                  "—— 取不到'哪一行做了它'"))
        elif hit and not hx:
            out.append((ax.name, f"范围内 {len(hit)} 行执行了这个动作，手级指示量却是 0"
                                  f"—— 两套口径漂移了"))
    return out


def whitening_stats(spec: BonusSpec, hands, *, student_policies: set[str] | None = None,
                    quiet: bool = False, mode: str = MODE_HAND):
    """扫一遍 `(game, hand_no, HandState)`，算每个轴的 μ/σ + 付奖账。

    @param student_policies `None` = 全部座位（`--style-bonus-whiten all`）；否则只用这些策略串
        坐的座位（`--style-bonus-whiten student` = 训练里真正进梯度的那些行）。
        ⚠ 用"全部座位"会把 teacher/对手的行为分布混进 μ/σ（风格桌上学生只占 1/4），
        白化就不准了 ⇒ 缺省取 student，且**必须**能在日志/meta 里看出来用的是哪一份。
    @param mode 付奖模式（`hand` 缺省 = 老口径逐位不变；`decision` = 逐行付奖 + **逐行**白化）。
        ⚠ `hand` 分支的每一行代码都没动过 ⇒ 与加这个参数之前**逐位相同**。
    @return `(stats: dict[str, Whiten], audit: Audit)`
    """
    mode = check_mode(mode)
    if mode == MODE_DECISION:
        require_row_axes(spec)              # ⛔ 前置闸门：付奖轴必须能定位到行
    acc: dict[str, list[float]] = {a.name: [] for a in spec.axes}
    paid: dict[str, dict] = {a.name: {"hands": 0, "cells": 0, "raw_sum": 0.0}
                             for a in spec.axes}
    #: ★ `decision` 模式的逐轴账（**只在那个模式下才有数字**）：
    #: `action_rows` = 范围内真的执行了该动作的行数；`paid_rows` = 其中通过质量轴的；
    #: `blocked_rows` = 被质量轴拦下的（**独立累加**，用来和 `action_rows − paid_rows` 对账）；
    #: `hands_hit` = 手级指示量为 1 的小局数（= "立直小局数"，需求里要拿它当分母报比率）。
    dav: dict[str, dict] = {a.name: {"action_rows": 0, "paid_rows": 0, "blocked_rows": 0,
                                    "hands_hit": 0} for a in spec.axes}
    #: `decision` 模式下顺带记一份**手级** μ/σ（**只作参照**，⛔ 不参与付奖）——
    #: 它让"剂量有没有被换掉"这件事在日志里当场可比（两条 std 都打出来）。
    hand_acc: dict[str, list[float]] = {a.name: [] for a in spec.axes}
    #: 违规的**前若干条样例**（`VIOL_SAMPLES` 封顶，只给日志看）。
    viol: list[tuple] = []
    #: ★ 违规的**真实小局数**（每一个违规小局都 +1，**与 `len(viol)` 无关**）。
    #: ⚠ 上一轮就是拿 `len(viol)` 当"共 N 个小局"打印 ⇒ 日志恒说 8（真实 30）。计数与样例必须分开。
    n_viol = 0

    n_hands = 0
    n_used = 0
    n_rows = 0
    for _g, _h, st in hands:
        n_hands += 1
        cells = [vals for _s, vals in indicator_cells(st, student_policies)]
        if not cells:
            continue
        # ⚠ 统计单位是**小局**（与付奖单位一致，见 `hand_bonus` 的 ★★ 注释）：
        #   四席取或成一份之后，白化参数与"付奖的分布"才是同一个东西。
        vals = _merge_cells(cells)
        n_used += 1
        rows = None
        acts: dict[str, int] = {}
        if mode == MODE_DECISION:
            # ★ 逐行白化的总体 = 范围内（学生席）的**每一行**；分子 = "这一行做了这个动作"。
            rows = rows_in_scope(st, student_policies)
            n_rows += len(rows)
            acts = axis_row_action_counts(spec, st, student_policies)
            bad = decision_violations(spec, st, student_policies)
            if bad:
                # ★ 计数**无条件** +1；样例才受上限约束（`len(viol)` 不是总数 —— 见 `n_viol` 的注释）。
                n_viol += 1
                if len(viol) < VIOL_SAMPLES:
                    viol.append((_g, _h, bad))
            for ax in spec.axes:
                hand_acc[ax.name].append(vals[ax.name])
                dav[ax.name]["action_rows"] += acts[ax.name]
                if vals[ax.name] > 0.5:
                    dav[ax.name]["hands_hit"] += 1
        for ax in spec.axes:
            if mode == MODE_DECISION:
                acc[ax.name].extend([1.0] * acts[ax.name]
                                    + [0.0] * (len(rows) - acts[ax.name]))
            else:
                acc[ax.name].append(vals[ax.name])
            if pair_ok(ax.pair, vals):
                paid[ax.name]["cells"] += 1
                paid[ax.name]["raw_sum"] += vals[ax.name]
                if vals[ax.name] > 0:
                    paid[ax.name]["hands"] += 1
                if mode == MODE_DECISION:
                    dav[ax.name]["paid_rows"] += acts[ax.name]
            elif mode == MODE_DECISION:
                dav[ax.name]["blocked_rows"] += acts[ax.name]
    if n_viol:
        # ⛔ 需求 ④：取不到"哪一行做了它" ⇒ **当场报错**，绝不退化成 `hand` 模式。
        #   ⚠ 报的是 `n_viol`（真实总数），不是 `len(viol)`（样例条数，封顶 `VIOL_SAMPLES`）。
        head = "；".join(f"game={g} hand={h}: " + "，".join(f"[{a}] {r}" for a, r in bad)
                         for g, h, bad in viol[:4])
        raise BonusSpecError(
            f"`decision` 模式取不到'哪一行做了这个动作'（共 **{n_viol}** 个小局；"
            f"下面只列前 {len(viol)} 条样例）：{head}。"
            f"⛔ **不退化**成 `hand` 模式：行级信度分配是这个模式的全部意义。"
            f"要么这份轨迹里没有记下那个动作键，要么范围（`--style-bonus-whiten` / `--student`）"
            f"与轨迹对不上")

    stats: dict[str, Whitening] = {}
    for ax in spec.axes:
        x = acc[ax.name]
        n = len(x)
        if n == 0:
            raise BonusSpecError(f"轴 {ax.name!r} 在统计范围里一个 (小局×座位) 样本都没有（n=0）"
                                 f"—— 白化无从谈起。检查 `--style-bonus-whiten student` 与 "
                                 f"`--student` 的策略串是否对得上")
        mu = float(sum(x) / n)
        if AXES[ax.name]["kind"] == "bernoulli":
            # Bernoulli：σ = √(p(1−p))（与 `x.std(ddof=0)` 逐位一致 —— 自检钉住这条）
            var = mu * (1.0 - mu)
        else:
            var = float(sum((v - mu) ** 2 for v in x) / n)
        sd = math.sqrt(max(var, 0.0))
        if not (sd > 0.0):
            # ⛔ 不静默跳过这个轴：跳过 = 那一轴的塑形**没了**，而训练照跑（正是本仓最忌讳的静默降级）。
            raise BonusSpecError(
                f"轴 {ax.name!r} 的白化 σ = {sd:g}（μ={mu:g}, n={n}）—— 这一轴在本数据集上是常数，"
                f"白化无从谈起（1/σ 会炸）。要么换一个在本桌上真的会变的轴，要么把它从 "
                f"`--style-bonus` 里去掉（**不许**默默跳过）")
        stats[ax.name] = Whitening(name=ax.name, kind=str(AXES[ax.name]["kind"]),
                                   mu=mu, sd=sd, n=n, n_all=n)
    # ★ `decision` 模式的**第二条**对账（需求 ② 的算术形式）：
    #   `付奖行数 + 被质量轴拦下的行数 == 动作行数`。两个加数**各自独立累加**
    #   （`paid_rows` 在 `pair_ok` 为真那一支、`blocked_rows` 在 else 支）⇒ 这条等式真的在查东西。
    if mode == MODE_DECISION:
        for ax in spec.axes:
            d = dav[ax.name]
            if d["paid_rows"] + d["blocked_rows"] != d["action_rows"]:
                raise BonusSpecError(
                    f"轴 {ax.name!r} 的付奖账对不上：付奖行 {d['paid_rows']} + 拦下 "
                    f"{d['blocked_rows']} != 动作行 {d['action_rows']} —— 付奖行/拦下行的累计点"
                    f"有漏（那是本仓最忌讳的静默错位）")
            if d["action_rows"] and not d["paid_rows"]:
                raise BonusSpecError(
                    f"轴 {ax.name!r}：动作行有 {d['action_rows']} 行，付奖行 **0** —— 质量轴 "
                    f"`{ax.pair}` 把所有动作行都拦下了。这多半不是想要的口径（先把质量轴的问题"
                    f"看清再跑，别拿一个'付奖恒 0'的模式去跑实验）")
    # 付奖总额 = Σ 1[PAIR]·w·(x − μ)/σ —— 只需 `Σ 1[PAIR]·x`（= raw_sum）与付奖格数
    paid_sums: dict[str, float] = {}
    for ax in spec.axes:
        w = stats[ax.name]
        p = paid[ax.name]
        paid_sums[ax.name] = ax.weight * (p["raw_sum"] - p["cells"] * w.mu) / w.sd
    audit = Audit(
        spec=spec.describe(),
        whiten_scope=("all" if student_policies is None else "student"),
        hands_total=n_hands, hands_eligible=n_used,
        mode=mode, rows_used=n_rows,
        axes=[{**stats[ax.name].to_json(), "weight": ax.weight, "pair": ax.pair,
               "pair_meaning": (PAIR_KEYS.get(ax.pair) or ("（恒假：只给红证用）", ""))[0],
               "paid_cells": paid[ax.name]["cells"],
               "paid_hands": paid[ax.name]["hands"],
               "paid_sum": paid_sums[ax.name],
               "raw_sum": paid[ax.name]["raw_sum"],
               # `decision` 模式才有意义的**五项**（`hand` 模式下这些键**根本不出现** ——
               # ⛔ 不是"出现但为 None"：那样 `hand` 模式的审计账会多一个键，
               #    "其它轴/缺省逐位不变"就只能靠嘴说；现在是**逐字节**相同）。
               "granularity": ("row" if mode == MODE_DECISION else "hand"),
               # ★ `win` 轴的行级判据多一条合取（"我就是被记录的那家"）—— 账里必须留下这件事，
               #   否则同一份 `action_rows` 在"有没有这条合取"下含义不同（多家荣和）。
               **({"row_self_seat": ROW_SELF_SEAT.get(ax.name)}
                  if mode == MODE_DECISION else {}),
               "action_rows": (dav[ax.name]["action_rows"] if mode == MODE_DECISION else None),
               "paid_rows": (dav[ax.name]["paid_rows"] if mode == MODE_DECISION else None),
               "blocked_rows": (dav[ax.name]["blocked_rows"] if mode == MODE_DECISION else None),
               "hands_indicator": (dav[ax.name]["hands_hit"] if mode == MODE_DECISION else None)}
              for ax in spec.axes],
    )
    if not quiet:
        print(f"风格奖励：白化统计（**本数据集实测**，范围 = {audit.whiten_scope}；"
              f"{n_hands} 个小局，其中 {n_used} 个进入统计（= 该小局在统计范围里有决策行））")
        for row in audit.axes:
            print(f"  轴 {row['axis']:<10} w={row['weight']:g}  成对质量轴={row['pair'] or '（无）'}  "
                  f"μ={row['mu']:.6f}  σ={row['sd']:.6f}  付奖小局 {row['paid_hands']}  "
                  f"付奖总额 {row['paid_sum']:+.1f}")
        if mode == MODE_DECISION:
            # ★ 需求 ②③ 的**原始输出落点**：付奖行数 / 动作行数 / 质量轴拦下 / 立直小局数，
            #   以及"逐行白化 vs 手级白化"两条 μ/σ（剂量有没有被换掉，当场可比）。
            print(f"风格奖励：**逐决策（decision）付奖** —— 奖励只落在'真的做了那个动作'那一行；"
                  f"白化总体 = {audit.whiten_scope} 席的**决策行**（{n_rows} 行）")
            for ax in spec.axes:
                d = dav[ax.name]
                hx = hand_acc[ax.name]
                hmu = float(sum(hx) / len(hx)) if hx else float("nan")
                hsd = math.sqrt(max(hmu * (1 - hmu), 0.0)) if hx else float("nan")
                w = stats[ax.name]
                ratio = (d["paid_rows"] / d["hands_hit"]) if d["hands_hit"] else float("nan")
                print(f"  轴 {ax.name}：动作行（`{ax.name}` 的动作键）{d['action_rows']}  "
                      f"付奖行 {d['paid_rows']}  质量轴 `{ax.pair}` 拦下 {d['blocked_rows']}  "
                      f"⇒ 付奖行 +拦下 == 动作行 "
                      f"({'✓' if d['paid_rows'] + d['blocked_rows'] == d['action_rows'] else '✗'})")
                print(f"     白化（逐行，**本模式实际使用**）μ={w.mu:.6f} σ={w.sd:.6f} n={w.n} 行  "
                      f"⇒ 动作行拿到的钱 w·(1−μ)/σ = {ax.weight * (1 - w.mu) / w.sd:+.1f} 点")
                print(f"     ⚠ 手级白化（**仅作参照**，本模式不用）：μ={hmu:.6f} σ={hsd:.6f}"
                      f"；『立直小局数』（手级指示量=1）{d['hands_hit']}"
                      f" ⇒ 付奖行数 / 立直小局数 = {ratio:.3f}")
    return stats, audit



def bonus_of(spec: BonusSpec, stats: dict[str, Whitening],
             vals: dict[str, float]) -> tuple[float, dict[str, float]]:
    """**一格**（小局 × 座位）→ 奖励（点）+ 逐轴明细。

    口径：`Σ_i w_i · 1[PAIR_i] · (x_i − μ_i)/σ_i`（⚠ μ/σ 是**本数据集**实测值，见 `whitening_stats`）。
    """
    total = 0.0
    detail: dict[str, float] = {}
    for ax in spec.axes:
        w = stats[ax.name]
        z = (paid_indicator(ax, vals) - w.mu) / w.sd
        c = ax.weight * z
        detail[ax.name] = c
        total += c
    return total, detail


def hand_bonus(spec: BonusSpec, stats: dict[str, Whitening], st: HandState,
               own_policies: set[str] | None = None) -> tuple[float, dict[str, float], int, dict]:
    """**一个小局** → 该小局的奖励（点）+ 逐轴明细。

    ★★ 为什么奖励是**小局级**而不是"逐席各一份"（2026-10-09 改，第一版就在这里错了）：

    训练侧的信用分配是**小局级**的 —— `pretrain._hand_advantage` 先把逐行的 `delta`/`style_bonus`
    **取那一小局的第一行**当成这一小局的价值（`reward_rows`），再对整小局共享。所以：

    * 如果奖励按**席位**给（四席四个值），那么"哪一席的值被取到"就变成**行序的偶然**：
      实测（3 场小冒烟）学生席拿到的塑形 std = **0.0**（每一小局取到的都是同一个教师席的值）；
    * 按**小局**给一份（下面 `own_policies` 指定的"自己"那一席的风格量），四行同值、
      取哪一行都一样 ⇒ 口径唯一、可复现。

    "自己"是谁（`own_policies`）：
    * `None`（`--style-bonus-whiten all`）= **四席取或**：这一小局**任何一家**立直且未放铳都算 ——
      读作"这一局桌上出现了立直，且（某个）立直的人没放铳"，方向仍是"多立直、别点炮"；
    * 给了策略串集合（`--style-bonus-whiten student` 缺省）= 只看**学生席位**的风格量 ——
      与"被训练的那条策略"严格对齐（奖励的是"**你**立直了没有"）。

    @return `(总奖励, 逐轴明细, 参与统计的席位数, 参与统计的席位量字典)`
    """
    cells = [vals for s, vals in indicator_cells(st, own_policies)]
    if not cells:
        # 这一小局学生一次询问都没有（庄家第一张就被荣和那种）⇒ 它不进梯度，奖励天然是 0。
        return 0.0, {}, 0, {}
    # 多席合成一份（风格桌上学生可能就是 1 席；`all` 时是四席）—— 取或见下面注释。
    merged = _merge_cells(cells)
    total, detail = bonus_of(spec, stats, merged)
    return total, detail, len(cells), merged


def _merge_cells(cells: list[dict[str, float]]) -> dict[str, float]:
    """把若干席的风格量合成"这一小局"的一份（**取或**，不是取均值）。

    为什么取或：奖励问的是"**这一小局有没有出现这个行为**"（`style-vector.py` 的分子口径也是
    "该小局宣言过立直"、同一小局多次只算 1）。
    为什么 `no_deal` **不能**跟着 `max`：三家没放铳、一家放铳时 `max(no_deal) = 1` —— 那等于把
    放铳洗掉了。所以它按"**这一小局有没有人放铳**"归一：有人放铳 ⇒ 0。
    """
    any_deal = any(v["deal"] > 0.5 for v in cells)
    out = {k: max(v[k] for v in cells) for k in ("riichi", "meld", "win", "win_points")}
    out["deal"] = 1.0 if any_deal else 0.0
    out["no_deal"] = 0.0 if any_deal else 1.0
    return out


def audit_whitening(spec: BonusSpec, stats: dict[str, Whitening], hands, *,
                    student_policies: set[str] | None = None,
                    mean_tol: float = 1e-9, var_tol: float = 1e-9,
                    mode: str = MODE_HAND) -> dict:
    """**白化正确性自证**：白化后的指示量在数据集上**均值 ≈ 0、方差 ≈ 1**。

    ⚠ 这里的"白化后"= `(x − μ)/σ`（**不加质量门**）—— 因为质量门 `1[PAIR]` 是**付奖**那一层，
    不是白化那一层（见 `paid_indicator` 的注释）。两者都要报：均值方差归零归一验的是**白化**，
    付奖小局数/总额验的是**付奖规则**。

    ⚠ **总体必须与 `whitening_stats` 一致**（`hand`：`indicator_cells` + `_merge_cells`、以**小局**
    为单位；`decision`：`rows_in_scope` 的**每一行**）：拿"全部座位逐席"去验一个按小局算出来的
    μ/σ，会得到 ≠0 的均值 —— 那不是容差问题，是真的错。
    """
    mode = check_mode(mode)
    acc: dict[str, list[float]] = {a.name: [] for a in spec.axes}
    for _g, _h, st in hands:
        cells = [vals for _s, vals in indicator_cells(st, student_policies)]
        if not cells:
            continue
        if mode == MODE_DECISION:
            for _s, chosen in rows_in_scope(st, student_policies):
                for ax in spec.axes:
                    w = stats[ax.name]
                    # ★ 与 `whitening_stats` **同一把尺子**（含 `win` 的"我就是被记录的那家"合取）
                    x = 1.0 if row_is_axis_action(ax.name, _s, chosen, st) else 0.0
                    acc[ax.name].append((x - w.mu) / w.sd)
            continue
        vals = _merge_cells(cells)
        for ax in spec.axes:
            w = stats[ax.name]
            acc[ax.name].append((vals[ax.name] - w.mu) / w.sd)
    out: dict[str, dict] = {}
    ok_all = True
    for ax in spec.axes:
        x = acc[ax.name]
        n = len(x)
        mu = float(sum(x) / n) if n else float("nan")
        var = float(sum((v - mu) ** 2 for v in x) / n) if n else float("nan")
        good = bool(n) and abs(mu) <= mean_tol and abs(var - 1.0) <= var_tol
        ok_all = ok_all and good
        out[ax.name] = {"n": n, "mean": mu, "var": var, "ok": good}
    out["_all_ok"] = {"ok": ok_all}
    return out


# ---------------------------------------------------------------------------
# ④ 数据集里的落点：逐行 bonus（`dataset.build` 用）
# ---------------------------------------------------------------------------

class BonusTracker:
    """**逐文件**预扫 → 逐行 `style_bonus`（点）。

    ## 为什么必须先预扫（而不是"边走边算"）

    `style_bonus` 是**小局级**量，而它依赖的是**整小局**的状态（"这一小局立直过没有 / 放铳没有"）。
    所以"边走边算"必然写不出第一行 —— 第一行出现时这一小局的结局还不知道。要在行写出之后回填，
    就得把行号缓冲起来（多一份状态、多一个可能漂移的地方）。

    预扫把这件事变成**纯查表**：先扫一遍这个文件、把**每个小局**的 bonus 算出来
    （一个小局一个值，四行同值 —— 见 `hand_bonus` 的 ★★ 注释），
    之后每一行只做一次 `dict` 查（`value(row)`）。判据三条：
      ① 与"缓冲行号再回填"**逐位等价**（同一份 `HandState` + 同一个 `bonus_of`）；
      ② 行序无关（`HandState.feed` 全是 OR / max / 覆盖 ⇒ 与喂入顺序无关）；
      ③ 查不到就是**报错**（不是 0）—— 静默给 0 = 那一小局的奖励悄悄没了。

    成本：多扫一遍 `g*.jsonl`（2.7 MB/场）。那是 `json.loads` 的活，实测 1000 场 ≈ 几十秒，
    相对于紧凑集构建（~115 s/1000 场）可以忽略；换来的是"奖励值精确"而不是"近似"。

    ## `mode="decision"`（2026-10-10 加）

    预扫仍然只做一次（每个小局一次），但缓存的东西从"**一个数**"变成"**这一小局的付奖规则**"：
    `(范围席位, 逐轴(质量轴成立?, 动作行拿多少点))`；`value(row)` 再按**这一行自己的 `chosen`**
    决定要不要付。两件事因此是分开的、也都是可核的：
      * **质量轴**（`PAIR`）按小局判 —— 与 `hand` 模式逐字相同；
      * **落在哪一行**按行判 —— `chosen` 以该轴的动作键开头。
    ⛔ 非动作行**付 0**（不是 `−μ/σ` 的负偏移）：付了就等于"每一行都付奖"，
    "付奖行数 == 动作行数"这条判据当场失效（需求 ②）。
    """

    def __init__(self, spec: BonusSpec | None, stats: dict[str, Whitening] | None,
                 own_policies: set[str] | None = None, mode: str = MODE_HAND):
        self.spec = spec
        self.stats = stats or {}
        #: "自己"是谁（见 `hand_bonus`）：`None` = 四席取或；否则只看这些策略串坐的席位。
        self.own_policies = own_policies
        self.mode = check_mode(mode)
        if self.mode == MODE_DECISION and self.spec:
            require_row_axes(self.spec)          # ⛔ 与 `whitening_stats` 同一道前置闸门
        #: `(game, hand_no) -> bonus`（**一个小局一个值**，四行同值；`hand` 模式）
        self.cache: dict[tuple[int, int], float] = {}
        #: `(game, hand_no) -> (范围内席位, {轴: (质量轴成立, 动作行拿多少点)}, 被记录的和了者)`（`decision` 模式）
        self.dcache: dict[tuple[int, int],
                          tuple[frozenset, dict[str, tuple[bool, float]], int]] = {}
        self.hands_done = 0
        self.paid_hands = 0
        self.paid_sum = 0.0
        self.paid_rows = 0
        self.rows = 0
        self.cache_hits = 0
        #: 本文件开始时的 `cache_hits`（`prebuild` 记）—— 每文件的查表判据按它做差
        self.hits0 = 0
        self.prebuilt_files = 0
        self.hands_no_cells = 0
        #: `decision` 模式的逐轴账（动作行 / 付奖行 / 被质量轴拦下）—— 需求 ② 的**原始输出**来源
        self.dec: dict[str, dict] = {a.name: {"action_rows": 0, "paid_rows": 0, "blocked_rows": 0}
                                     for a in (spec.axes if spec else ())}

    @property
    def enabled(self) -> bool:
        return bool(self.spec)

    def prebuild(self, path: Path) -> None:
        """扫一遍这个文件、把**每个小局**的 bonus 算进 `cache`（**幂等**，同一个 key 只算一次）。

        ⚠ 同时记下"本文件开始时 `cache_hits` 是多少"（`hits0`）—— 查表计数是**每文件**的判据
        （同一个 tracker 会被串行分支跨文件复用，累积计数对不上"本文件写了多少行"）。
        """
        if not self.enabled:
            return
        self.prebuilt_files += 1
        self.hits0 = self.cache_hits
        for g, h, st in iter_hands(path):
            k = (int(g), int(h))
            if self.mode == MODE_DECISION:
                if k in self.dcache:
                    continue
                self.dcache[k] = self._decision_rule(int(g), int(h), st)
                self.hands_done += 1
                continue
            if k in self.cache:
                continue
            total, _detail, n_cells, _vals = hand_bonus(self.spec, self.stats, st,
                                                        self.own_policies)
            if n_cells == 0:
                self.hands_no_cells += 1
            if total != 0.0:
                self.paid_hands += 1
                self.paid_sum += total
            self.cache[k] = total
            self.hands_done += 1

    def _decision_rule(self, g: int, h: int, st: HandState):
        """这一小局的付奖规则（`decision` 模式）：`(范围内席位, {轴: (质量轴, 动作行金额)}, 被记录的和了者)`。

        ⚠ 需求 ④ 的**第二道闸门**在这里：`prebuild` 也走一遍 `decision_violations`
        （`whitening_stats` 是第一道）—— 因为 `prebuild` 是**真正写列**的那条路，
        只在白化那一步查的话，"白化过了、写列时口径漂了"就没人管。

        ★ 第三个分量 `winner` 是 `win` 轴行级判据要的"**我就是被记录的那家**"（`HandState.winner`，
        与 `whitening_stats`/`axis_row_action_counts` 同一个来源）—— 写列时**不重读** `row` 里的
        `hand_winner`（两个来源必然漂移）。
        """
        bad = decision_violations(self.spec, st, self.own_policies)
        if bad:
            raise BonusSpecError(
                f"`decision` 模式在 game={g} hand={h} 取不到'哪一行做了这个动作'："
                + "，".join(f"[{a}] {r}" for a, r in bad)
                + "。⛔ **不退化**成 `hand` 模式（那会让这次实验什么都没测）")
        cells = [v for _s, v in indicator_cells(st, self.own_policies)]
        if not cells:
            return (frozenset(), {}, int(st.winner))
        merged = _merge_cells(cells)
        seats = frozenset(s for s, _c in rows_in_scope(st, self.own_policies))
        per: dict[str, tuple[bool, float]] = {}
        for ax in self.spec.axes:
            ok = pair_ok(ax.pair, merged)
            per[ax.name] = (ok, spec_axis_pay(self.spec, self.stats, ax))
        n_act = axis_row_action_counts(self.spec, st, self.own_policies)
        for ax in self.spec.axes:
            self.dec[ax.name]["action_rows"] += n_act[ax.name]
            if per[ax.name][0]:
                self.dec[ax.name]["paid_rows"] += n_act[ax.name]
            else:
                self.dec[ax.name]["blocked_rows"] += n_act[ax.name]
        return (seats, per, int(st.winner))

    def value(self, row: dict) -> float:
        """这一行的 `style_bonus`（点）。没开塑形时恒 0.0（一行都不多走）。"""
        if not self.enabled:
            return 0.0
        self.rows += 1
        k = (int(row.get("game", -1)), int(row.get("hand_no", -1)))
        if self.mode == MODE_DECISION:
            return self._decision_value(k, row)
        total = self.cache.get(k)
        if total is None:
            # ⛔ 查不到**必须是错**：静默给 0 = 这一行的奖励悄悄没了（而训练照跑）。
            raise BonusSpecError(
                f"`{COLUMN}` 查不到小局 {k} —— 预扫 `prebuild` 漏了这个文件/小局。"
                f"要么是拼接调用方没按文件喂，要么是 `game`/`hand_no` 字段缺失。不猜、不填 0")
        self.cache_hits += 1
        v = float(total)
        if v != 0.0:
            self.paid_rows += 1
        return v

    def _decision_value(self, k: tuple[int, int], row: dict) -> float:
        """`decision` 模式：**这一行**的奖励（只在该行真的做了动作、且该小局质量轴成立时非 0）。

        ★ 行级判据与预扫**同一把尺子**：`row_is_axis_action` 需要 `HandState`，而这里只有 `row`
        ⇒ 用预扫存下来的 **`winner`**（该小局被记录的和了者），**不**重读 `row["hand_winner"]`。
        """
        ent = self.dcache.get(k)
        if ent is None:
            raise BonusSpecError(
                f"`{COLUMN}`（decision 模式）查不到小局 {k} —— 预扫 `prebuild` 漏了这个文件/小局。"
                f"不猜、不填 0")
        self.cache_hits += 1
        seats, per, winner = ent
        seat = int(row.get("seat", -1))
        if seat not in seats:
            return 0.0                     # 不在统计范围内（不是学生席）⇒ 一行都不付
        chosen = str(row.get("chosen") or "")
        total = 0.0
        for ax in self.spec.axes:
            ok, pay = per[ax.name]
            if not ok:
                continue
            if not row_is_action(ax.name, chosen):
                continue
            if ROW_SELF_SEAT.get(ax.name) == "winner" and winner != seat:
                continue                   # 多家荣和：只有被记录的那家拿得到这笔钱
            total += pay
        v = float(total)
        if v != 0.0:
            self.paid_rows += 1
        return v

    def audit(self) -> dict:
        out = {"hands": self.hands_done, "paid_hands": self.paid_hands,
               "paid_rows": self.paid_rows, "paid_sum": self.paid_sum,
               "rows": self.rows, "cache_hits": self.cache_hits,
               "hands_no_cells": self.hands_no_cells,
               "prebuilt_files": self.prebuilt_files, "mode": self.mode}
        if self.mode == MODE_DECISION:
            out["per_axis"] = {k: dict(v) for k, v in self.dec.items()}
        return out


def paid_accounting(spec: BonusSpec, stats: dict[str, Whitening], hands,
                    own_policies: set[str] | None = None, mode: str = MODE_HAND) -> dict:
    """扫一遍全部小局算**付奖账**（父进程写 meta 用；逐轴：付奖小局数 / 付奖总额 / 原始分子和）。

    ⚠ 与 `BonusTracker` 走**同一份** `hand_bonus` / `_merge_cells` 口径（不是第二套实现）——
    两套实现漂移是这类量最经典的静默 bug。`decision` 模式下**同一份 `decision_bonus`**，
    并且额外汇总"动作行 / 付奖行 / 被质量轴拦下"（与 `BonusTracker.dec` 同一个口径）。
    """
    mode = check_mode(mode)
    if mode == MODE_DECISION:
        require_row_axes(spec)
    paid_hands: dict[str, int] = {a.name: 0 for a in spec.axes}
    paid_sum: dict[str, float] = {a.name: 0.0 for a in spec.axes}
    raw_sum: dict[str, float] = {a.name: 0.0 for a in spec.axes}
    dec: dict[str, dict] = {a.name: {"action_rows": 0, "paid_rows": 0, "blocked_rows": 0}
                            for a in spec.axes}
    n_hands = 0
    n_cells = 0
    paid_total = 0
    tot_sum = 0.0
    for _g, _h, st in hands:
        n_hands += 1
        cells = [vals for _s, vals in indicator_cells(st, own_policies)]
        if not cells:
            continue
        vals = _merge_cells(cells)
        if mode == MODE_DECISION:
            n_cells += 1
            total = 0.0
            for s, chosen in rows_in_scope(st, own_policies):
                t, _d = decision_bonus(spec, stats, st, s, chosen, own_policies)
                total += t
            tot_sum += total
            if total != 0.0:
                paid_total += 1
            n_act = axis_row_action_counts(spec, st, own_policies)
            for ax in spec.axes:
                if pair_ok(ax.pair, vals) and vals[ax.name] > 0:
                    paid_hands[ax.name] += 1
                    paid_sum[ax.name] += n_act[ax.name] * spec_axis_pay(spec, stats, ax)
                    raw_sum[ax.name] += vals[ax.name]
                dec[ax.name]["action_rows"] += n_act[ax.name]
                if pair_ok(ax.pair, vals):
                    dec[ax.name]["paid_rows"] += n_act[ax.name]
                else:
                    dec[ax.name]["blocked_rows"] += n_act[ax.name]
            continue
        total, detail, _n, _v = hand_bonus(spec, stats, st, own_policies)
        n_cells += 1
        tot_sum += total
        if total != 0.0:
            paid_total += 1
        for ax in spec.axes:
            if pair_ok(ax.pair, vals) and vals[ax.name] > 0:
                paid_hands[ax.name] += 1
                paid_sum[ax.name] += detail[ax.name]
                raw_sum[ax.name] += vals[ax.name]
    out = {"hands": n_hands, "hands_counted": n_cells, "paid_hands_total": paid_total,
           "paid_sum_total": tot_sum, "mode": mode,
           "per_axis": {a.name: {"paid_hands": paid_hands[a.name],
                                 "paid_sum": paid_sum[a.name],
                                 "raw_sum": raw_sum[a.name]} for a in spec.axes}}
    if mode == MODE_DECISION:
        for a in spec.axes:
            out["per_axis"][a.name].update(dec[a.name])
    return out


def write_audit(out_dir: Path, audit: Audit, whitening: dict, extra: dict | None = None) -> Path:
    """把奖励账落盘成 `<紧凑集>/style-reward.json`（**可审计**：w/μ/σ/付奖小局数/付奖总额）。

    ★ 持久副本（`AUDIT_DIR_ENV`）：设了 `MAHJONG_STYLE_AUDIT_DIR` 时**同时**写一份到
    `<dir>/<紧凑集目录名>.json`，并**返回那一份的路径**（`meta.audit_path` 指向持久路径）。
    理由见 `AUDIT_DIR_ENV` 的注释：`run-league.ps1` 每代会把 `compact\<tag>` 删掉。
    """
    payload = {
        "tool": "mahjong_ml/v4/style_reward.py",
        "note": "风格奖励塑形的账。判据（强弱）⛔ 不看这里 —— 只看 mahjong_ml/v4/gate.py；"
                "本文件回答的是「奖励到底付了多少钱、按什么口径付的」。",
        "spec": audit.spec,
        "whiten_scope": audit.whiten_scope,
        "hands_total": audit.hands_total,
        "hands_eligible": audit.hands_eligible,
        "axes": audit.axes,
        "whitening_check": whitening,
        "source": extra or {},
    }
    p = Path(out_dir) / "style-reward.json"
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    p.write_text(text, encoding="utf-8")
    # ★ 持久副本（见 `AUDIT_DIR_ENV`）：`compact\<tag>` 会被 `run-league.ps1` 每代轮换删掉，
    #   所以给了目录就**另写一份逐字节相同**的账，并把它当成 `meta.audit_path` 返回。
    persist = (os.environ.get(AUDIT_DIR_ENV) or "").strip()
    if persist:
        q = Path(persist) / f"{Path(out_dir).name}.json"
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(text, encoding="utf-8")
        return q
    return p


#: 当前这条链的 `w` 从哪读（**只用于日志措辞**）—— 与 `loop.py` 的 `--style-bonus` 缺省同一来源。
WEIGHT_ENV = "MAHJONG_STYLE_BONUS"


def current_weight(axis: str | None = None) -> float | None:
    """从环境变量读回**当前这一路的 `w`**（只给日志措辞用；读不到就返回 `None`，⛔ 不猜）。

    ⚠ 为什么需要它（2026-10-10 修正一处**误标定**的根因）：`pretrain.py` 打的那行尺度读数是
    `base_std / std(塑形列)`，而塑形列**本身已经乘过 `w`** ⇒ 那个数
    `= (base_std/std(z)) / w = w*/w`，是**放大倍数**，**不是"该设的 w"**。
    上一轮把它读成"要 1:1 大约要 `w=2`"，于是三档里最高档（`w=2800`）被当成"已经 1:1"
    （实际只有 0.45× std、真实 `w* ≈ 6170`）。
    要打出真正的"所需 w"就必须知道**当前 `w`**：`所需 w = 当前 w × 放大倍数`。
    """
    spec = parse_bonus(os.environ.get(WEIGHT_ENV))
    axes = [a for a in spec.axes if axis is None or a.name == axis]
    return float(axes[0].weight) if axes else None


def parity_note(mult: float, axis: str | None = None) -> str:
    """`w_for_parity` 那行的**措辞**（唯一落点）：明确区分「**放大倍数** `w*/w`」与「**所需 w**」。

    @param mult `base_std / std(塑形列)` = 把**当前这笔**塑形抬到与结果奖励 1:1 的**倍数**
        （⚠ 不是"该设的 `w`" —— 见 `current_weight` 的注释）。
    """
    w_now = current_weight(axis)
    head = f"**放大倍数 w*/w = {mult:.2f}**（⛔ 不是「该设的 w」）"
    if w_now is None:
        return (f"{head}；**所需 w** = 当前 w × 该倍数（⚠ 当前 w 读不到：`{WEIGHT_ENV}` "
                f"没设或不含这一轴 ⇒ ⛔ 不拿倍数当 w 用）")
    return (f"{head} ⇒ **所需 w**（= 当前 w {w_now:g} × {mult:.2f}）≈ **{w_now * mult:.0f}**")


def stats_to_json(stats: dict[str, Whitening]) -> dict:
    return {k: v.to_json() for k, v in stats.items()}


def stats_from_json(obj: dict) -> dict[str, Whitening]:
    """`meta.json` 里的白化参数 → `Whitening`（子进程/训练端读回用 —— 只有一份解析）。"""
    out: dict[str, Whitening] = {}
    for k, v in (obj or {}).items():
        out[k] = Whitening(name=str(v.get("axis", k)), kind=str(v.get("kind", "bernoulli")),
                           mu=float(v["mu"]), sd=float(v["sd"]),
                           n=int(v.get("n_used", v.get("n", 0))),
                           n_all=int(v.get("n_seen", v.get("n", 0))))
    return out
