"""**手级信用分配**（`docs/TRAINING-V4.md` §14 的 P1）：把小局当时间步，做 λ<1 的 GAE + bootstrap。

## 为什么要它（不是"想换个算法"，是实测逼出来的）

| 目标（同一批数据、同一表示） | 可解释方差（引擎真值 10 列线性参照） |
| --- | --- |
| **本小局收支 `delta`** | **+0.632** |
| `rtg`（本局及其后的多手后缀和） | +0.090 |
| `value`（整场结果） | +0.059 |

而值头的 `rtg` 口径实测 **EV 0.002–0.010**（三次独立尝试）⇒ **λ=1 的多手后缀和不是可学目标**，
`A = R − V̂` 退化成"原始回报减常数"（没有 baseline）。手级 GAE 把 V 的目标从"预测之后所有小局"
换成 **TD(λ) 回报**：每步只要求 V 预测"下一小局的收支 + 之后的 bootstrap"，方差大降。

## 口径（与数据集列一一对应，不许另写一份）

- **时间步** = 一"小局"的 (game, hand_no, seat)；状态 = 该小局**第一个决策**的 obs；
- **奖励** `r_h = delta_h / 1000`（千点）+ `rank_weight · rank_points`（**只在整场最后一小局**给，
  与 v3 `rewards.transitions` 同口径：顺位点本来就是小量纲，不再除 1000）；
- **`V_h`** = 行为策略在该小局第一个决策上的价值期望（千点，`softmax(value) · 分箱中心`）；
- `δ_h = r_h + γ·V_{h+1} − V_h`、`A_h = δ_h + γλ·A_{h+1}`（末小局 `V_{h+1} = 0`）；
- **值头目标** = `A_h + V_h`（TD(λ) 回报）；逐决策展开时同一小局的行**共享** `(A_h, V^λ_h)`
  —— 与数据集 `rtg` 的"小局内常量"口径一致（`rtg` 在本小局内本来就是常量）。

⚠ 这里的 rank 项**必须**来自生产者登记的 `summary.json`（`per_game[g].rank_points[seat]`）：
uma/oka 是 `Payments`/`Settlement` 的活，在 Python 里重写必然漂移（v3 `rewards.py` 的同一条纪律）。
"""

from __future__ import annotations

import numpy as np

#: 折扣（与 v3 `rewards.py` 同口径：1.0 = 不折扣，半庄只有 8~12 小局）
GAMMA = 1.0


def hand_chain(game: np.ndarray, hand_no: np.ndarray, seat: np.ndarray
               ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """按 `(game, seat)` 把小局串成链。

    @return `(starts, hand_of_row, next_hand, reward_rows)`
        · `starts[h]`：第 h 个小局的**第一个决策行号**（升序；小局 id 全局递增）；
        · `hand_of_row[i]`：第 i 行属于哪个小局；
        · `next_hand[h]`：同 `(game, seat)` 链上下一个小局的 id，`-1` = 该链末尾（bootstrap 0）；
        · `reward_rows[h]`：该小局"记奖励"的行号（取末决策，与 v3 "只有小局末决策吃收支"一致；
          这里其实任取一行都对，因为 `delta` 在小局内是常量 —— 取末决策是为了与训练口径可对照）。
    """
    g = np.asarray(game).reshape(-1)
    h = np.asarray(hand_no).reshape(-1)
    s = np.asarray(seat).reshape(-1)
    if not (g.size == h.size == s.size):
        raise ValueError(f"game/hand_no/seat 长度不一致：{g.size}/{h.size}/{s.size}")
    n = int(g.size)
    first: dict[tuple[int, int, int], int] = {}
    last: dict[tuple[int, int, int], int] = {}
    for i in range(n):
        k = (int(g[i]), int(h[i]), int(s[i]))
        first.setdefault(k, i)
        last[k] = i                      # 行序就是时间序 ⇒ 最后一次出现即末决策
    # 链：同 (game, seat) 内按 hand_no 升序
    chains: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for (gg, hh, ss), i0 in first.items():
        chains.setdefault((gg, ss), []).append((hh, i0))
    keys = sorted(chains)
    hand_of_row = np.full(n, -1, dtype=np.int64)
    starts: list[int] = []
    reward_rows: list[int] = []
    next_hand: list[int] = []
    for key in keys:
        items = sorted(chains[key])
        ids = []
        for hh, i0 in items:
            ids.append(len(starts))
            starts.append(i0)
            reward_rows.append(last[(key[0], hh, key[1])])
        for j, hid in enumerate(ids):
            next_hand.append(ids[j + 1] if j + 1 < len(ids) else -1)
    # 回填 `hand_of_row`（按 key 匹配，不假设同一小局的行号连续）
    key_to_hand = {}
    for hid, i0 in enumerate(starts):
        key_to_hand[(int(g[i0]), int(h[i0]), int(s[i0]))] = hid
    for i in range(n):
        hand_of_row[i] = key_to_hand[(int(g[i]), int(h[i]), int(s[i]))]
    return (np.asarray(starts, dtype=np.int64), hand_of_row,
            np.asarray(next_hand, dtype=np.int64), np.asarray(reward_rows, dtype=np.int64))


def hand_reward(delta: np.ndarray, rank_points: np.ndarray | None, hand_of_row: np.ndarray,
                next_hand: np.ndarray, reward_rows: np.ndarray, *,
                rank_weight: float = 0.0, unit: float = 1000.0) -> np.ndarray:
    """小局奖励（千点）：`delta/1000` + `rank_weight · rank_points`（只在**链末尾**那一小局）。

    ⚠ `rank_points` 是"整场顺位点"（`summary.json` 里那一列，量纲已经是小量纲），
    整场只有一个值 ⇒ 只加在链末小局上一次（加在每个小局上等于把它乘了"小局数"）。
    """
    d = np.asarray(delta, dtype=np.float64).reshape(-1)
    r = d[reward_rows] / unit
    if rank_weight and rank_points is not None:
        rp = np.asarray(rank_points, dtype=np.float64).reshape(-1)
        term = np.where(next_hand < 0)[0]
        r[term] = r[term] + rank_weight * rp[reward_rows[term]]
    return r


def gae_hand(reward_h: np.ndarray, value_h: np.ndarray, next_hand: np.ndarray, *,
             gamma: float = GAMMA, lam: float = 0.9) -> tuple[np.ndarray, np.ndarray]:
    """链式 GAE（`nxt < 0` = 链末尾，bootstrap 0）。返回 `(advantage, value_target)`。"""
    r = np.asarray(reward_h, dtype=np.float64)
    v = np.asarray(value_h, dtype=np.float64)
    nx = np.asarray(next_hand, dtype=np.int64)
    if not (r.size == v.size == nx.size):
        raise ValueError(f"reward/value/next 长度不一致：{r.size}/{v.size}/{nx.size}")
    adv = np.zeros(v.size, dtype=np.float64)
    for i in range(v.size - 1, -1, -1):
        j = int(nx[i])
        v_next = v[j] if j >= 0 else 0.0
        delta = r[i] + gamma * v_next - v[i]
        adv[i] = delta + (gamma * lam * adv[j] if j >= 0 else 0.0)
    return adv, adv + v


def expand_hand(values_h: np.ndarray, hand_of_row: np.ndarray) -> np.ndarray:
    """小局级数组 → 逐决策（同一小局共享一个值；与 `rtg` 的小局内常量口径一致）。"""
    v = np.asarray(values_h, dtype=np.float64)
    return v[np.asarray(hand_of_row, dtype=np.int64)]


def hand_summary(adv_rows: np.ndarray, rtg_rows: np.ndarray | None = None) -> dict[str, float]:
    """优势的体检数字（P1 判据②：`std(A)` 要明显低于原始回报的 std）。"""
    a = np.asarray(adv_rows, dtype=np.float64)
    out = {"n": float(a.size), "adv_mean": float(a.mean()), "adv_std": float(a.std())}
    if rtg_rows is not None:
        r = np.asarray(rtg_rows, dtype=np.float64)
        out["rtg_std"] = float(r.std())
        out["std_ratio"] = float(a.std() / r.std()) if r.std() > 0 else float("nan")
    return out
