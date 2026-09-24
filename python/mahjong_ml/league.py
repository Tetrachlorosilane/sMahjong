"""P6 · 联赛口径：**Plackett-Luce 强度阶梯** + **按场聚类的自助法 CI** + 对手采样权重。

    # 把多个 run 的名次合成一张全局阶梯（θ 一把尺子量所有策略）
    python -m mahjong_ml.league S:\\mahjong-training\\raw\\league-r1 S:\\mahjong-training\\raw\\league-r2 \\
        --boot 200 --json ladder.json --focus awr-002
    python -m mahjong_ml.league --selftest          # 自检（含两条红证）

为什么要有它：`eval.py` 只能做**两两配对**（A vs B），策略一多就要 C(n,2) 次检验，而且
"谁比谁强"没有一张全局一致的尺子。Plackett-Luce 是 Bradley-Terry 的四人局推广 ——
把一整场名次（1>2>3>4）折成一条似然，**一次拟合出所有策略的同一把尺子**（θ）。

三条口径（照定义抄，不自己发明）：
  · **只用 `placement`（名次）拟合 θ**。`rank_points` 是**另一个量纲**（马点/头名赏/返点），
    混进来会让阶梯被点数尺度带跑 —— 它只在 CLI 里当"交叉参考列"，且**从 `by_policy` 读**（不重算）；
  · 一场的四家名次**不独立**（同一副牌山），所以 CI 必须**按 run 分层重抽整场**；
    逐行 bootstrap 会给出假窄的 CI —— 这正是本项目反复强调的"按场聚类"口径；
  · θ 只能定到"相差一个常数"，所以每步中心化（`θ -= θ.mean()`）；CI 也按**中心化参数化**算
    （数值 Hessian 的**伪逆**，不是加岭求逆 —— 加岭会把那条零方向变成 1/l2 的巨大方差，SE 直接假大）。

⚠ 策略串文法见 `docs/PROTOCOL.md` §8.4：`net:<权重文件>` / `net:<权重文件>@<α>`
（`#<T>` 是预留的温度采样后缀，短名要原样带出来，否则同权重的两个温度会被折成一个名字）。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# ------------------------------------------------------------------ 常量

#: 脚本基线策略（不需要权重文件，短名 = 策略串本身）。`teacher` 不在里面：它是网络策略的锚点。
SCRIPT_BASELINES: tuple[str, ...] = ("first", "pass", "random")

#: 双侧 95% 正态分位（与 `eval.py` 的 1.96 同一口径；写全精度避免"两个文件两个 1.96"）。
Z95 = 1.959963985

_NET_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
#: `net:<路径>@<α>#<T>` 的后缀。⚠ 只在后缀**看起来是数字**时才切 —— 目录名里带 `@` 的路径
#: 不该被误切成"权重路径 + α"（那会让短名变成另一个策略的名字，阶梯悄悄合并两个策略）。
_NET_SUFFIX_RE = re.compile(rf"@{_NET_NUM}(?:#{_NET_NUM})?\Z|#{_NET_NUM}\Z")


# ------------------------------------------------------------------ 策略串 → 短名

def short_name(spec: str) -> str:
    """策略串 → 短名（ELO 阶梯与日志里用的稳定名字）。

    `teacher` → `teacher`；`net:S:/…/ckpt/awr-002/net.bin` → `awr-002`；
    `net:…/awr-002/net.bin@2` → `awr-002@2`；`net:…@2#1.5` → `awr-002@2#1.5`。

    为什么取**权重文件的父目录名**：权重文件的文件名是固定的 `net.bin`（`export.py` 的口径），
    真正区分策略的是 ckpt 目录（`awr-002` / `bc-003`）；而 `@<α>` / `#<T>` 必须保留 ——
    它们是同一个权重文件的**不同采样口径**，折成一个名字就把两个策略混着统计了。
    """
    if not isinstance(spec, str):
        raise TypeError(f"策略串必须是 str，收到 {type(spec).__name__}: {spec!r}")
    s = spec.strip()
    if not s.startswith("net:"):
        return s
    path, suffix = _split_net_suffix(s[4:])
    return _ckpt_name(path) + suffix


def _split_net_suffix(rest: str) -> tuple[str, str]:
    """把 `路径@α#T` 拆成（路径, `@α#T` 后缀）。后缀不像数字就整串当路径。"""
    cut = max(rest.rfind("/"), rest.rfind("\\"))
    tail = rest[cut + 1:]
    m = re.search(r"[@#]", tail)
    if m is None:
        return rest, ""
    at = cut + 1 + m.start()
    suffix = rest[at:]
    return (rest[:at], suffix) if _NET_SUFFIX_RE.match(suffix) else (rest, "")


def _ckpt_name(path: str) -> str:
    """权重路径 → 目录名（`…/ckpt/awr-002/net.bin` → `awr-002`）。"""
    path = path.rstrip("/\\") or path
    cut = max(path.rfind("/"), path.rfind("\\"))
    if cut < 0:                                     # `net:net.bin` 这种没目录的写法
        return Path(path).stem or path
    parent_cut = max(path.rfind("/", 0, cut), path.rfind("\\", 0, cut))
    parent = path[parent_cut + 1:cut]
    if parent:
        return parent
    fname = path[cut + 1:]                          # 只有一层（`net:ckpt/net.bin`）→ 退回文件名
    return Path(fname).stem or fname


# ------------------------------------------------------------------ 载入

@dataclass
class Game:
    """一场对局的结果（**只保留 PL 拟合需要的部分**）。

    `players` 是**按名次从好到坏排好序**的短名 —— PL 的似然就是按这个顺序连乘的，
    所以排序必须在这里一次做对（别让下游再猜哪个下标是第几名）。
    `run` 是来源 run 目录名：`pl_ci` 按它分层重抽（同一场牌的四个名次必须一起动）。
    """
    players: list[str]
    run: str = ""


def _summary_file(d: str | Path) -> Path:
    p = Path(d)
    if p.is_dir():
        p = p / "summary.json"
    if not p.is_file():
        raise FileNotFoundError(f"找不到 summary.json：{p}（--selfplay --out 的产物目录）")
    return p


def _check_placement(f: Path, gid: object, place: object) -> list[int]:
    """名次必须是 1..4 的**排列** —— 不是就报错（"平均顺位"的前提，别静默吞掉）。"""
    try:
        vals = [int(v) for v in place]              # type: ignore[union-attr]
    except (TypeError, ValueError):
        raise ValueError(f"{f} 第 {gid} 场的 placement 不是整数表：{place!r}") from None
    if sorted(vals) != [1, 2, 3, 4]:
        raise ValueError(f"{f} 第 {gid} 场的 placement 不是 1..4 的排列：{place!r} —— "
                         f"名次被挤掉/重复时名次本身就没有意义（同点按座次拆名次是服务端口径）")
    return vals


def load_games(run_dirs: list[str | Path]) -> list[Game]:
    """读多个 run 的 `summary.json`，折成 `Game` 列表（只取名次，不取顺位点）。

    三条**硬校验**（宁可报错，也不让坏数据悄悄进阶梯）：
      ① 每场必须 4 个座位；② `placement` 必须是 1..4 的排列；
      ③ 一场里四个**短名不许撞车** —— θ 是按名字给的，同一场两个座位同名就分不清是谁的名次
         （`--rotate` 下四家策略互异，撞车说明没开 rotate 或四家策略不互异：请改数据，别改口径）。
    """
    out: list[Game] = []
    for d in run_dirs:
        f = _summary_file(d)
        s = json.loads(f.read_text(encoding="utf-8"))
        pg = s.get("per_game") or []
        if not pg:
            raise ValueError(f"{f} 里没有 per_game（PL 阶梯需要每场的四家名次）")
        run = f.parent.name or str(f.parent)
        for row in pg:
            specs = list(row.get("policies") or [])
            gid = row.get("game")
            if len(specs) != 4:
                raise ValueError(f"{f} 第 {gid} 场的 policies 有 {len(specs)} 个（要 4 个座位）：{specs!r}")
            if not all(isinstance(x, str) for x in specs):
                raise ValueError(f"{f} 第 {gid} 场的 policies 里有非字符串：{specs!r}")
            place = _check_placement(f, gid, row.get("placement"))
            names = [short_name(x) for x in specs]
            dup = sorted({n for n in names if names.count(n) > 1})
            if dup:
                raise ValueError(
                    f"{f} 第 {gid} 场**短名撞车**：{names}（重复：{', '.join(dup)}）—— "
                    f"同一场里两个座位只会是同一个策略（没开 --rotate 或四家策略不互异）；"
                    f"θ 按名字给，撞车就分不清是谁的名次，阶梯会静默算错。请让同一批 run 四家策略互异")
            out.append(Game(players=[n for _, n in sorted(zip(place, names))], run=run))
    return out


# ------------------------------------------------------------------ Plackett-Luce 拟合

@dataclass
class Fit:
    """Plackett-Luce 拟合结果：θ = 强度（log 尺度，**已中心化到均值 0**）。"""
    theta: dict[str, float]
    se: dict[str, float]
    games: int
    players: list[str]


def _suffix_share(z: np.ndarray) -> np.ndarray:
    """逐场**后缀份额**矩阵 `q[i,j] = e^{z_i} / Σ_{l≥j} e^{z_l}`（只有 `i ≥ j` 有意义）。

    - 第 j 名的 PL 选择概率就是**对角元** `q[j,j]`（= `e^{z_j}/Σ_{l≥j} e^{z_l}`）；
    - 而后缀 j 的**每一个**选手在分母里都各有一份，份额各不相同 —— 那些是 `q[i,j]`（i>j），
      漏掉它们就不是本似然的梯度（任务书那行简写只写了 `q[j,j]`，只对第 1 名成立）。
    ⚠ 位移必须**整行同一个**（行最大值）：逐后缀各减各的运行最大值时，`Σ_l e^{z_l−m_l}`
    不是同一个尺子下的和，`q` 会算错（实测这一条也是让真值处梯度 ≠ 0 的元凶之一）。
    """
    m = np.max(z, axis=1, keepdims=True)
    e = np.exp(z - m)                                           # (n, k)
    c = np.cumsum(e[:, ::-1], axis=1)[:, ::-1]                  # 后缀和 Σ_{l≥j} e^{z_l−m}
    return e[:, :, None] / c[:, None, :]                        # q[g, i, j] = e[g,i] / c[g,j]


def _suffix_probs(z: np.ndarray) -> np.ndarray:
    """`p[j] = e^{z_j} / Σ_{l≥j} e^{z_l}`（= 后缀份额的对角元）—— 逐场"第 j 名被选中的概率"。"""
    q = _suffix_share(z)
    return np.diagonal(q, axis1=1, axis2=2).copy()


def _lik_grad_sum(theta: np.ndarray, idx: np.ndarray, P: int) -> np.ndarray:
    """**总**对数似然的梯度（不含 l2）—— 按 ℓ 的定义逐项求导。

    `ℓ = Σ_g Σ_j [θ_{p_j} − log Σ_{l≥j} e^{θ_{p_l}}]`，所以：
      · **分子项**：第 j 名那个选手 `+1`；
      · **分母项**：后缀 j 里**每一个**选手各扣 `e^{θ_i}/S_j`（不是只扣第 j 名那一个）。

    ⚠ 任务书把梯度简写成 `∂ℓ/∂θ_{p_j} += 1 − e^{θ_{p_j}}/S_j` —— 那**只对第 1 名成立**：
    第 2 名之后还要背着"前面每个名额的分母"。实测按简写实现时真值处梯度 ≈ +0.5（**不驻点**），
    2000 场合成数据也恢复不出真 θ。这里以**似然的定义**为准（定义是权威，简写那条是笔误）；
    2 人局可手算对拍：a 输时 `∂ℓ/∂θ_a = −p_a` —— 本式给 `−p_a`，简写给 `0`。
    """
    q = _suffix_share(theta[idx])
    k = idx.shape[1]
    g = np.zeros(P, dtype=np.float64)
    for j in range(k):
        g += np.bincount(idx[:, j], minlength=P)                # 分子项：+1 给第 j 名
        sub = idx[:, j:]                                        # 分母项：后缀里每个选手各自的份额
        g -= np.bincount(sub.ravel(), weights=q[:, j:, j].ravel(), minlength=P)
    return g


def _hessian_total(theta: np.ndarray, idx: np.ndarray, P: int, h: float = 1e-4) -> np.ndarray:
    """**总**对数似然的数值 Hessian（中心差分）—— 二阶导解析式啰嗦，4~10 个选手规模下不划算。"""
    H = np.empty((P, P), dtype=np.float64)
    for j in range(P):
        tp = theta.copy()
        tm = theta.copy()
        tp[j] += h
        tm[j] -= h
        H[:, j] = (_lik_grad_sum(tp, idx, P) - _lik_grad_sum(tm, idx, P)) / (2.0 * h)
    return 0.5 * (H + H.T)


def plackett_luce(games: list[Game], *, l2: float = 1e-3, iters: int = 3000,
                  lr: float = 0.5, tol: float = 1e-9) -> Fit:
    """numpy 梯度上升拟合 θ（`iters` 步、固定 `lr`、`l2` 是 `−0.5·l2·Σθ²` 的正则项）。

    为什么梯度要**除以场数**（用平均对数似然）：`lr` 是调用方给的固定步长，
    若用总梯度的量纲，2400 场时一步就是几百 —— 固定 `lr=0.5` 直接发散。
    平均化之后"一步走多大"与 run 的场数无关，`l2` 的量纲也稳定（否则大数据集的正则项等于没有）。

    收敛判据是 `max|Δθ| < tol`；每步后中心化，解决"只能定到相差一个常数"。

    ⚠ **SE 用未加罚的数值 Hessian 的伪逆**：加罚求逆会把那条零方向（全体同加一个常数）
    变成 `1/l2` 的巨大方差（实测 se≈16），阶梯的 95%CI 就假大；伪逆才是"中心化参数化"的
    正确协方差。若 Hessian 连"只有一个零方向"都不满足（真非正定），退化成
    `1/sqrt(n·0.25)` 的粗值 —— **照实给，不返回 NaN**。
    """
    players = sorted({p for g in games for p in g.players})
    P = len(players)
    n = len(games)
    if n == 0 or P < 2:                             # 退化：没有可辨识的阶梯，粗 SE 兜底
        rough = 1.0 / math.sqrt(max(1, n) * 0.25)
        return Fit(theta={p: 0.0 for p in players}, se={p: rough for p in players},
                   games=n, players=players)

    widths = {len(g.players) for g in games}
    if len(widths) != 1:
        raise ValueError(f"每场的选手数必须一致（现在是 {sorted(widths)}）—— PL 似然按整场名次连乘")
    k = widths.pop()
    if k < 2:
        raise ValueError("一场至少要 2 个选手")
    for g in games:
        if len(set(g.players)) != len(g.players):
            raise ValueError(f"同一场里短名重复：{g.players}（θ 按名字给，分不清是谁的名次）")

    index = {p: i for i, p in enumerate(players)}
    idx = np.array([[index[p] for p in g.players] for g in games], dtype=np.intp)

    theta = np.zeros(P, dtype=np.float64)
    for _ in range(max(0, iters)):
        g = _lik_grad_sum(theta, idx, P) / n - l2 * theta       # 平均对数似然 + l2 罚
        delta = lr * g
        theta = theta + delta
        theta -= theta.mean()                                  # 中心化：不可辨识方向投影掉
        if float(np.max(np.abs(delta))) < tol:
            break
    theta -= theta.mean()

    # ---- SE：未加罚 Hessian 的伪逆（-∇²ℓ 的负逆对角线开方）
    A = -_hessian_total(theta, idx, P)
    eig = np.linalg.eigvalsh(A)
    scale = float(np.max(np.abs(eig))) if eig.size else 0.0
    ok = scale > 0 and float(np.min(eig)) > -1e-6 * scale
    if ok:
        cov = np.linalg.pinv(A, rcond=1e-8, hermitian=True)
        diag = np.diag(cov)
        ok = bool(np.all(np.isfinite(diag))) and float(np.min(diag)) > 0.0
    if ok:
        se_arr = np.sqrt(np.diag(cov))
    else:
        se_arr = np.full(P, 1.0 / math.sqrt(max(1, n) * 0.25), dtype=np.float64)

    return Fit(theta={p: float(theta[index[p]]) for p in players},
               se={p: float(se_arr[index[p]]) for p in players},
               games=n, players=players)


# ------------------------------------------------------------------ 置信区间（按场聚类）

def pl_ci(games: list[Game], *, n_boot: int = 200,
          seed: int = 20260101) -> dict[str, tuple[float, float]]:
    """自助法 95%CI（**按 run 分层重抽整场**）。

    为什么按场：同一场牌的四个名次是**同时产生**的（一副牌山定胜负），逐行走廊里把它们当
    独立样本重抽会把 CI 抽得比真实窄（假显著）。所以重抽单位是**整场**，而且**按 run 分层**
    （每个 run 的原样本量必须保持，否则不同 run 的权重会被 bootstrap 悄悄改掉）。
    固定 `seed` → 同输入同区间（可复现是硬要求）。
    """
    if not games:
        return {}
    base = plackett_luce(games)
    names = base.players
    if n_boot < 2 or len(games) < 2:
        # 退化：没有可重抽的余地 —— 给点估计（长度 0 的区间），不返回 NaN
        return {p: (base.theta[p], base.theta[p]) for p in names}

    groups: dict[str, list[int]] = {}
    for i, g in enumerate(games):
        groups.setdefault(g.run, []).append(i)

    rng = np.random.default_rng(seed)
    draws: dict[str, list[float]] = {p: [] for p in names}
    for _ in range(n_boot):
        sample: list[Game] = []
        for idxs in groups.values():                            # 分层：每个 run 各自重抽同样多场
            pick = rng.integers(0, len(idxs), size=len(idxs))
            sample.extend(games[idxs[j]] for j in pick)
        f = plackett_luce(sample, tol=1e-7)                     # CI 只关心到 ~1e-7（远小于 se）
        for p in names:
            draws[p].append(f.theta.get(p, float("nan")))

    out: dict[str, tuple[float, float]] = {}
    for p in names:
        arr = np.asarray(draws[p], dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            out[p] = (base.theta[p], base.theta[p])
        else:
            lo, hi = np.percentile(arr, [2.5, 97.5])
            out[p] = (float(lo), float(hi))
    return out


# ------------------------------------------------------------------ 阶梯

def boot_thetas(games: list[Game], *, n_boot: int = 200,
                seed: int = 20260101) -> tuple[list[str], np.ndarray]:
    """分层 bootstrap 的 θ 抽样矩阵 `[B, P]` + 选手名顺序（**一次重抽供所有比较复用**）。

    为什么要复用：B 次 PL 拟合是整个 CI 的大头（4 选手 × 4800 场 ≈ 0.2 s/次），
    而"每个网络 vs 老师"这种比较天然应该用**同一批**重抽（配对口径也要求同一次重抽内做减法）。
    逐对调用会让 4 个比较各跑一遍 200 次拟合 —— 4 倍开销、还更容易被误当成独立证据。
    """
    base = plackett_luce(games)
    names = list(base.players)
    groups: dict[str, list[int]] = {}
    for i, g in enumerate(games):
        groups.setdefault(g.run, []).append(i)
    rng = np.random.default_rng(seed)
    rows: list[list[float]] = []
    for _ in range(max(1, n_boot)):
        sample: list[Game] = []
        for idxs in groups.values():                    # 分层：每个 run 各自重抽同样多场
            pick = rng.integers(0, len(idxs), size=len(idxs))
            sample.extend(games[idxs[j]] for j in pick)
        f = plackett_luce(sample, tol=1e-7)
        if all(n in f.theta for n in names):
            rows.append([f.theta[n] for n in names])
    return names, (np.asarray(rows, dtype=np.float64) if rows else np.zeros((0, len(names))))


def ci_from_thetas(names: list[str], thetas: np.ndarray, base: dict[str, float],
                   focus: str, anchor: str) -> dict:
    """从**同一批** θ 抽样里取 `θ_focus − θ_anchor` 的配对 CI 与双侧 p。"""
    for name in (focus, anchor):
        if name not in names:
            raise ValueError(f"{name!r} 不在阶梯里（可用：{', '.join(names)}）")
    i, j = names.index(focus), names.index(anchor)
    delta = float(base[focus] - base[anchor])
    if thetas.shape[0] == 0:
        return {"focus": focus, "anchor": anchor, "delta": delta,
                "ci": [delta, delta], "p": 1.0, "n_boot": 0}
    d = thetas[:, i] - thetas[:, j]
    lo, hi = np.percentile(d, [2.5, 97.5])
    left, right = float((d <= 0).mean()), float((d >= 0).mean())
    return {"focus": focus, "anchor": anchor, "delta": delta,
            "ci": [float(lo), float(hi)], "p": float(min(1.0, 2 * min(left, right))),
            "n_boot": int(d.size)}


def pl_ci_diff(games: list[Game], focus: str, anchor: str, *, n_boot: int = 200,
               seed: int = 20260101) -> dict:
    """**配对**的 θ 差 CI：在**同一次** bootstrap 重抽里算 `θ_focus − θ_anchor`，再看差值的分位数。

    为什么不能拿两次 `pl_ci` 的区间"相减"、更不能看"θ_focus 在不在 θ_anchor 的 95%CI 里"：
    那两个区间是**各自**的分位数，丢掉了同一次重抽里的相关性（中心化约束下 θ̂ 之间是**负相关**）；
    而"落在锚点区间内"这种读法会让**锚点自己的误差**把多个比较伪造成独立证据 ——
    典型症状就是"四个网络齐刷刷高于老师"（其实它们共享同一个锚点偏移）。
    差值必须在**同一次重抽内**做减法，这才是配对口径（与 `eval.py` 的逐场配对同一个道理）。

    ⚠ 要一次算多对就用 `boot_thetas` + `ci_from_thetas`（别逐对调用它的内部循环）。
    """
    base_fit = plackett_luce(games)
    names, thetas = boot_thetas(games, n_boot=n_boot, seed=seed)
    return ci_from_thetas(names, thetas, base_fit.theta, focus, anchor)

def ladder(fit: Fit) -> list[tuple[str, float, float, float]]:
    """按 θ 降序：`[(名字, θ, se, 95%CI 半宽), …]`（同 θ 时按名字定序，保证输出可复现）。"""
    return [(p, float(fit.theta[p]), float(fit.se[p]), Z95 * float(fit.se[p]))
            for p in sorted(fit.players, key=lambda x: (-fit.theta[x], x))]


def format_ladder(fit: Fit, *, anchor: str = "teacher") -> str:
    """人类可读的阶梯表（**同时给绝对 θ 与相对 anchor 的 θ 差**，后者才是判据）。

    为什么"差"才是判据：θ 只能定到相差一个常数，绝对值的含义随"这一批对手是谁"漂移；
    `θ_i − θ_anchor` 才是跨 run 可比的量。
    ⚠ Δ 的 CI 半宽给的是**上界** `1.96·(se_i + se_anchor)`：`Fit` 不带协方差矩阵，而中心化
    约束下 θ̂ 之间是**负相关**的（"相差一个常数"那条零方向把误差推给彼此），`√(se_i²+se_a²)`
    会**低估** Δ 的不确定性（实测约 15%）→ 可能假显著。上界宁可宽一点也不谎报显著
    （数学上 `Var(θ_i−θ_a) ≤ (se_i+se_a)²` 恒成立）。要**精确**的 Δ CI 得让 `Fit` 带上协方差。
    """
    base = fit.theta.get(anchor)
    lines = [f"== Plackett-Luce 阶梯（{fit.games} 场 / {len(fit.players)} 个策略；"
             f"θ 已中心化，均值 0）",
             f"  {'策略':<18}{'θ':>9}{'SE':>8}{'95%CI半宽':>10}"
             f"{'θ−' + anchor:>16}{'ΔCI半宽≤':>10}"]
    for p, th, se, hw in ladder(fit):
        if base is None:
            diff, dhw = "n/a", "n/a"
        else:
            diff = f"{th - base:+.3f}"
            dhw = f"{Z95 * (se + float(fit.se.get(anchor, 0.0))):.3f}"
        lines.append(f"  {p:<18}{th:>+9.3f}{se:>8.3f}{hw:>10.3f}{diff:>16}{dhw:>10}"
                     + ("  ← anchor" if p == anchor else ""))
    if base is None:
        lines.append(f"  ⚠ anchor={anchor!r} 不在这份阶梯里 —— 判据列（θ−anchor）给不出；"
                     f"可用 --anchor 指定一个在列的策略（或先把 teacher 放进这批 run）")
    else:
        lines.append(f"  读法：绝对 θ 只在「同一批对手」下可比；**判据是 θ−{anchor} 那一列** —— "
                     f"|Δ| ≤ 半宽 = 证不出差别（牌山方差大，别拿单次跑分下结论）。")
    return "\n".join(lines)


# ------------------------------------------------------------------ 采样权重

def select_weights(fit: Fit, *, focus: str, floor: float = 0.05,
                   exponent: float = 1.0) -> dict[str, float]:
    """联赛采样权重：`w_i ∝ exp(exponent·(θ_i − θ_focus))`，再按 floor 抬底并归一。

    「谁克我就多跟它打」= **比 focus 强的对手权重大**：θ_i 越大 → 指数越大 → 权重越大
    （⚠ 任务书里写作 `exp(-exponent·(θ_i - θ_focus))`，那个**负号会把"多打强手"反成"多打弱手"**，
    与同一段文字口径和判据「比 focus 强的权重 ≥ 比 focus 弱的」自相矛盾 —— 以文字与判据为准）。

    `floor` 是抬底：最终权重 = `(1−floor)·归一权重 + floor/N`（与均匀分布混合），
    于是最弱的对手也至少有 `floor/N` 的份额 —— 否则"打不过的永远不打"，就再也补不上短板。
    `exponent` 越大越极端（0 = 均匀）。
    """
    if focus not in fit.theta:
        raise ValueError(f"focus={focus!r} 不在阶梯里（可用：{', '.join(fit.players)}）")
    if not 0.0 <= floor <= 1.0:
        raise ValueError(f"floor 必须在 [0,1]（收到 {floor}）—— 它是抬底比例，不是倍数")
    if exponent < 0:
        raise ValueError(f"exponent 必须 ≥ 0（收到 {exponent}）—— 负指数会把「多打强手」反过来")
    names = list(fit.players)
    N = len(names)
    if N == 0:
        return {}
    z = np.array([exponent * (fit.theta[n] - fit.theta[focus]) for n in names], dtype=np.float64)
    z = z - z.max()                                             # 位移：指数最大那位恒为 0，防大 θ 差溢出
    raw = np.exp(z)
    total = float(raw.sum())
    norm = raw / total if total > 0 else np.full(N, 1.0 / N)
    w = (1.0 - floor) * norm + floor / N
    return {n: float(w[i]) for i, n in enumerate(names)}


def sample_opponents(weights: dict[str, float], k: int, *, rng: np.random.Generator,
                     exclude: str | None = None) -> list[str]:
    """按权重**不放回**抽 `k` 个对手（给定 `rng` 与 `weights` 完全确定）。

    - 权重 ≤ 0 的对手**抽不到**（直接从池子里剔除，而不是"概率极低"）；
    - `exclude` 是"自己"（或focus）—— 不跟自己打；
    - 池子不足 `k` 时**能抽几个给几个**（不是补位、不是报错）。
    实现用**逆变换**（`rng.random()` + 累积权重）而不是 `Generator.choice(p=…)`：
    后者的抽样算法不是 numpy 的文档化契约，换版本就可能不再逐元素可复现。
    """
    pool = [n for n, w in weights.items() if n != exclude and float(w) > 0.0]
    w = np.array([float(weights[n]) for n in pool], dtype=np.float64)
    out: list[str] = []
    for _ in range(max(0, int(k))):
        if not pool:
            break
        total = float(w.sum())
        if total <= 0.0:
            break
        u = float(rng.random()) * total
        i = int(np.searchsorted(np.cumsum(w), u, side="right"))
        i = min(i, len(pool) - 1)
        out.append(pool.pop(i))
        w = np.delete(w, i)
    return out


# ------------------------------------------------------------------ CLI

def _rank_points_ref(run_dirs: list[str | Path]) -> dict[str, float]:
    """`短名 → avg_rank_points`（跨 run 按场数加权；**只作交叉参考**，不参与 θ 拟合）。

    顺位点是"另一个量纲"（马点/头名赏/返点），直接当 Elo 用会让阶梯被点数尺度带跑；
    但它确实是这项运动的记分，所以打出来给读者对照 —— ⚠ 值**从 `by_policy` 读**，不自己算。
    """
    acc: dict[str, list[tuple[float, float]]] = {}
    for d in run_dirs:
        f = _summary_file(d)
        s = json.loads(f.read_text(encoding="utf-8"))
        by = s.get("by_policy") or {}
        w = float(s.get("games") or len(s.get("per_game") or []) or 0)
        for spec, st in by.items():
            rp = (st or {}).get("avg_rank_points")
            if rp is None:
                continue
            key = short_name(spec)
            acc.setdefault(key, []).append((w, float(rp)))
    out: dict[str, float] = {}
    for key, pairs in acc.items():
        tw = sum(x[0] for x in pairs)
        out[key] = (sum(x[0] * x[1] for x in pairs) / tw) if tw > 0 else pairs[0][1]
    return out


def _weights_block(fit: Fit, weights: dict[str, float], focus: str, floor: float,
                   exponent: float) -> str:
    lines = [f"== 联赛采样权重（focus={focus}，floor={floor:g}，exponent={exponent:g}）",
             f"  {'策略':<18}{'权重':>9}{'θ':>9}"]
    for n, w in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  {n:<18}{w:>9.4f}{fit.theta[n]:>+9.3f}")
    lines.append(f"  读法：**比 {focus} 强的对手权重大**（赢面小的多打）；"
                 f"floor 抬底保证最弱的也有 {floor / max(1, len(weights)):.4f} 的份额；Σw=1。")
    lines.append(f"  ⚠ {focus} 自己也在这张表里（它权重最大，因为 θ−{focus}=0）；真正排对手时用 "
                 f"`sample_opponents(..., exclude='{focus}')` 把自己剔掉。")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m mahjong_ml.league",
        description="P6 联赛阶梯：Plackett-Luce θ + 按场聚类 CI + 对手采样权重")
    ap.add_argument("runs", nargs="*", help="自对弈 run 目录（含 summary.json；可给多个合并拟合）")
    ap.add_argument("--selftest", action="store_true", help="跑内置自检（不需要任何数据）")
    ap.add_argument("--boot", type=int, default=200,
                    help="自助法次数（按 run 分层重抽整场；0 = 不算 CI）")
    ap.add_argument("--seed", type=int, default=20260101, help="自助法种子（固定 → 可复现）")
    ap.add_argument("--json", dest="json_out", default=None, help="把结果写到这个 JSON 路径")
    ap.add_argument("--focus", default=None, help="给某个短名算联赛采样权重（自己不出现在对手里）")
    ap.add_argument("--anchor", default="teacher", help="阶梯里「相对谁」看 θ 差（默认 teacher）")
    ap.add_argument("--floor", type=float, default=0.05, help="采样权重的抬底比例")
    ap.add_argument("--exponent", type=float, default=1.0, help="采样权重的强度（0 = 均匀）")
    ap.add_argument("--l2", type=float, default=1e-3, help="θ 的 l2 正则（−0.5·l2·Σθ²）")
    ap.add_argument("--iters", type=int, default=3000, help="梯度上升的步数上限（tol 达标会提前停）")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if not args.runs:
        ap.error("需要至少一个 run 目录（或 --selftest）")

    games = load_games(args.runs)
    fit = plackett_luce(games, l2=args.l2, iters=args.iters)
    print(f"== 载入 {len(args.runs)} 个 run / {len(games)} 场；"
          f"{len(fit.players)} 个策略" + ("（名次口径：只用 placement 拟合 θ）" if games else ""))
    print(format_ladder(fit, anchor=args.anchor))

    ref = _rank_points_ref(args.runs)
    if ref:
        print("\n-- 交叉参考（**不当判据**）：by_policy.avg_rank_points，顺位点与 θ 不是一个量纲")
        for n in sorted(ref, key=lambda x: -ref[x]):
            mark = "  ← focus" if n == args.focus else ""
            print(f"  {n:<18}{ref[n]:>+9.3f}{mark}")

    ci: dict[str, tuple[float, float]] = {}
    if args.boot > 0:
        ci = pl_ci(games, n_boot=args.boot, seed=args.seed)
        print(f"\n-- 自助法 95%CI（{args.boot} 次，按 run 分层重抽整场，seed={args.seed}）")
        for n in sorted(ci, key=lambda x: -fit.theta[x]):
            lo, hi = ci[n]
            print(f"  {n:<18}[{lo:+.3f}, {hi:+.3f}]"
                  + ("　跨 0" if lo <= 0.0 <= hi else "　排除 0"))

    weights: dict[str, float] = {}
    if args.focus:
        weights = select_weights(fit, focus=args.focus, floor=args.floor, exponent=args.exponent)
        print("\n" + _weights_block(fit, weights, args.focus, args.floor, args.exponent))

    if args.json_out:
        payload = {
            "runs": [str(Path(d)) for d in args.runs],
            "games": fit.games, "players": fit.players,
            "theta": fit.theta, "se": fit.se,
            "ladder": [[p, th, se, hw] for p, th, se, hw in ladder(fit)],
            "anchor": args.anchor, "ci": {k: list(v) for k, v in ci.items()},
            "boot": args.boot, "seed": args.seed,
            "rank_points_ref": ref, "focus": args.focus, "weights": weights,
        }
        Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        print(f"\n已写出 {args.json_out}")
    return 0


# ------------------------------------------------------------------ 自检（--selftest）
#
# 两条**红证**（故意破坏时必须失败）：
#   ① 真值 θ 全相等 → 拟合出的 95%CI 必须**全部跨 0**（不跨 = SE 太小，阶梯在无中生有）；
#   ② 被操纵的数据（某人永远第 1、`first` 永远最差）→ 两者的 θ 必须**显著**偏离 0。
# 另有"方差版红证"（60 次重复的 SE/SD 比值 + 覆盖率）与"似然对拍"（解析梯度 == 数值差分），
# 这两条比单次重复稳，才是真正抓"式子抄错 / SE 系统性偏小"的闸门。
# 其余是口径检查：短名文法 / load_games 的两条硬校验 / 权重单调与抬底 / 采样可复现与不放回 /
# pl_ci 可复现 + 分层 / 2 人局 SE 与解析式对拍。

def _pl_ranking(theta: np.ndarray, rng: np.random.Generator) -> list[int]:
    """按 PL 概率采一个完整名次（每一步都在**剩余**选手里按 `exp(θ)` 加权抽）。"""
    pool = list(range(theta.size))
    order: list[int] = []
    while pool:
        z = theta[pool]
        w = np.exp(z - z.max())
        p = np.cumsum(w / w.sum())
        u = float(rng.random())
        i = min(int(np.searchsorted(p, u, side="right")), len(pool) - 1)
        order.append(pool.pop(i))
    return order


def _synth(theta_true: dict[str, float], n_games: int, seed: int,
           run: str = "synth") -> list[Game]:
    rng = np.random.default_rng(seed)
    names = list(theta_true)
    th = np.array([theta_true[n] for n in names], dtype=np.float64)
    return [Game(players=[names[i] for i in _pl_ranking(th, rng)], run=run)
            for _ in range(n_games)]


def _report(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))
    return bool(ok)


def _tmp_run_dir() -> Path:
    """给 load_games 的读写检查造一个可写临时目录。

    ⚠ 不用 `tempfile.TemporaryDirectory()`：它建的目录带 0o700，受限沙箱（Windows ACL 闸门）
    会拒绝往里写（实测 PermissionError）；这里按 **系统临时目录 → 当前目录 → 包目录** 依次试
    `mkdir`（默认权限），谁先成功用谁。调用方负责删掉。
    """
    bases = [Path(tempfile.gettempdir()), Path.cwd(), Path(__file__).resolve().parent]
    for base in bases:
        d = base / f"_league_selftest_{os.getpid()}"
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return d
        except OSError:
            continue
    raise RuntimeError("找不到可写目录来跑 load_games 自检（系统临时目录/当前目录/包目录都不可写）")


def selftest() -> int:
    """内置自检：返回 0 = 全过，1 = 有失败。"""
    print("== mahjong_ml.league 自检（Plackett-Luce / CI / 采样）")
    ok_all: list[bool] = []

    # 1. 短名文法（PROTOCOL §8.4 的五种写法 + bot）
    cases = {
        "teacher": "teacher",
        "bot": "bot",
        "first": "first",
        "random": "random",
        r"net:S:\mahjong-training\ckpt\awr-002\net.bin": "awr-002",
        r"net:S:\mahjong-training\ckpt\awr-002\net.bin@2": "awr-002@2",
        r"net:S:\mahjong-training\ckpt\awr-002\net.bin@2#1.5": "awr-002@2#1.5",
        r"net:S:\mahjong-training\ckpt\awr-002\net.bin#0.7": "awr-002#0.7",
        "net:/home/mj/ckpt/bc-003/net.bin@0.25": "bc-003@0.25",
        "net:C:/mj/ckpt/run@weird/net.bin": "run@weird",          # 目录名带 @ 不许被误切
    }
    bad = {k: short_name(k) for k, v in cases.items() if short_name(k) != v}
    ok_all.append(_report("short_name 五种文法（+ bot / 目录名带 @）", not bad,
                          f"不符：{bad}" if bad else f"{len(cases)} 例全对"))

    # 1b. 似然式子对拍：`p` 与手算一致 + 解析梯度 == 数值差分
    #     （这条抓的是"式子抄错"—— 实测真栽过一次：简写梯度 + 后缀各减各的最大值）
    rng_g = np.random.default_rng(11)
    gm = [Game(players=[f"p{i}" for i in rng_g.permutation(4)], run="g") for _ in range(40)]
    pl4 = ["p0", "p1", "p2", "p3"]
    idx4 = np.array([[pl4.index(p) for p in g.players] for g in gm], dtype=np.intp)
    z4 = np.array([[0.4, -0.9, 0.2, 0.1]] * 1)
    p_manual = np.array([np.exp(z4[0, j]) / np.exp(z4[0, j:]).sum() for j in range(4)])
    p_impl = _suffix_probs(z4)[0]
    th4 = np.array([0.3, -0.7, 0.1, -0.2])

    def _loglik(t: np.ndarray) -> float:
        zz = t[idx4]
        m = zz.max(axis=1, keepdims=True)
        e = np.exp(zz - m)
        c = np.cumsum(e[:, ::-1], axis=1)[:, ::-1]
        return float(np.sum(zz - m - np.log(c)))

    hh = 1e-6
    num_grad = np.array([(_loglik(th4 + hh * e) - _loglik(th4 - hh * e)) / (2 * hh)
                         for e in np.eye(4)])
    ana_grad = _lik_grad_sum(th4, idx4, 4)
    gdiff = float(np.max(np.abs(ana_grad - num_grad)))
    ok_all.append(_report("似然对拍：p 与手算一致 + 解析梯度 == 数值差分（式子抄错就红）",
                          bool(np.allclose(p_impl, p_manual)) and gdiff < 1e-4,
                          f"p 最大差 {np.max(np.abs(p_impl - p_manual)):.2e}；梯度最大差 {gdiff:.2e}"))

    # 2. 真值恢复：2000 场合成数据，θ 排序一致且真值落在 95%CI 内
    truth = {"bc-001": 0.8, "awr-001": 0.25, "teacher": -0.25, "first": -0.8}
    games = _synth(truth, 2000, seed=20260101)
    fit = plackett_luce(games)
    order_ok = ([p for p, *_ in ladder(fit)] == sorted(truth, key=lambda p: -truth[p]))
    inside = {p: abs(fit.theta[p] - truth[p]) <= Z95 * fit.se[p] for p in truth}
    detail = "　".join(f"{p}:真值{truth[p]:+.2f} 拟合{fit.theta[p]:+.3f}±{Z95 * fit.se[p]:.3f}"
                       for p in sorted(truth, key=lambda p: -truth[p]))
    ok_all.append(_report("真值恢复（2000 场，4 选手：排序一致 + 真值全在 95%CI 内）",
                          order_ok and all(inside.values()), detail))
    ok_all.append(_report("真值恢复：SE 不是摆设（θ 的真实误差量级 ≈ SE）",
                          all(abs(fit.theta[p] - truth[p]) <= 3.0 * fit.se[p] for p in truth),
                          f"最大 |误差|/SE = {max(abs(fit.theta[p] - truth[p]) / fit.se[p] for p in truth):.2f}"))

    # 3. **红证**：真值 θ 全相等 → 95%CI 必须全部跨 0（SE 太小就会红）
    #
    # ⚠ 这条检查是**单次重复**：4 个选手各自 5% 的漏检率是统计事实（实测单选手覆盖率 95.7%，
    #    "四个全跨 0"的概率 ≈ 86%），所以种子必须固定（否则同代码换年就随机红）。
    #    真正能抓住"SE 系统性太小"的是下面第 3b 条（经验 SD vs 平均 SE）。
    flat = {p: 0.0 for p in truth}
    fit_flat = plackett_luce(_synth(flat, 2000, seed=20260104))
    cross = {p: (fit_flat.theta[p] - Z95 * fit_flat.se[p] <= 0.0
                 <= fit_flat.theta[p] + Z95 * fit_flat.se[p]) for p in flat}
    ok_all.append(_report("红证：真值全相等 → 95%CI 全部跨 0（不跨 = SE 太小、阶梯造假信号）",
                          all(cross.values()),
                          "　".join(f"{p}:{fit_flat.theta[p]:+.3f}±{Z95 * fit_flat.se[p]:.3f}"
                                    for p in sorted(flat))))

    # 3b. 红证（方差版）：θ̂ 的经验标准差必须与平均 SE 同量级、单选手覆盖率 ≈ 95%
    #     比第 3 条稳得多 —— SE 只要系统性偏小（比如漏了分母项、漏乘 1/n），这里必然红。
    reps, n_rep = 60, 1000
    est, ses = [], []
    for r in range(reps):
        f_r = plackett_luce(_synth(flat, n_rep, seed=770000 + r))
        est.append([f_r.theta[p] for p in f_r.players])
        ses.append([f_r.se[p] for p in f_r.players])
    est_a, se_a = np.array(est), np.array(ses)
    sd_emp = est_a.std(axis=0, ddof=1)
    ratio = float(np.mean(se_a.mean(axis=0) / sd_emp))
    cover = float(np.mean(np.abs(est_a) <= Z95 * se_a))
    ok_all.append(_report("红证（方差版）：60 次重复的 SE/SD 比值 ∈ [0.85,1.2] 且单选手覆盖率 ∈ [0.88,0.99]",
                          0.85 <= ratio <= 1.20 and 0.88 <= cover <= 0.99,
                          f"SE/SD={ratio:.3f}（经验 SD {sd_emp.mean():.4f} vs 平均 SE "
                          f"{se_a.mean():.4f}）；{reps}×{n_rep} 场覆盖率={cover:.3f}"))

    # 4. 被操纵的数据：永远第 1 的显著 > 0，永远最差的 `first` 显著 < 0
    rng = np.random.default_rng(20260103)
    rigged: list[Game] = []
    for _ in range(400):
        mid = ["mid1", "mid2"]
        rng.shuffle(mid)
        rigged.append(Game(players=["champ", mid[0], mid[1], "first"], run="rigged"))
    fit_rig = plackett_luce(rigged)
    hi_ok = fit_rig.theta["champ"] - Z95 * fit_rig.se["champ"] > 0.0
    lo_ok = fit_rig.theta["first"] + Z95 * fit_rig.se["first"] < 0.0
    ok_all.append(_report("被操纵数据：永远第 1 的显著 > 0、永远最差的 first 显著 < 0",
                          hi_ok and lo_ok,
                          f"champ {fit_rig.theta['champ']:+.3f}±{Z95 * fit_rig.se['champ']:.3f}；"
                          f"first {fit_rig.theta['first']:+.3f}±{Z95 * fit_rig.se['first']:.3f}"))

    # 5. ladder / format_ladder：降序、半宽=1.96·se、anchor 的差为 0
    rows = ladder(fit)
    desc = all(rows[i][1] >= rows[i + 1][1] for i in range(len(rows) - 1))
    hw_ok = all(abs(hw - Z95 * se) < 1e-12 for _, _, se, hw in rows)
    tbl = format_ladder(fit, anchor="teacher")
    tbl_ok = ("θ−teacher" in tbl and "← anchor" in tbl and "+0.000" in tbl)
    ok_all.append(_report("ladder 降序 + 半宽=1.96·se；format_ladder 给绝对 θ 与 θ−anchor 两列",
                          desc and hw_ok and tbl_ok,
                          f"行数 {len(rows)}；半宽/sp={hw_ok}；表头与 anchor 差 {tbl_ok}"))
    tbl_missing = format_ladder(fit, anchor="不存在的策略")
    ok_all.append(_report("format_ladder：anchor 不在阶梯里时给 n/a 而不是崩",
                          "n/a" in tbl_missing and "不在这份阶梯里" in tbl_missing))

    # 6. select_weights：单调 + Σ=1 + floor 抬底 + 未知 focus 报错
    w = select_weights(fit, focus="awr-001", floor=0.05)
    strong = w["bc-001"]                 # 比 focus 强
    weak = w["first"]                    # 比 focus 弱
    sum_ok = abs(sum(w.values()) - 1.0) < 1e-12
    n = len(w)
    floor_ok = min(w.values()) >= 0.05 / n - 1e-12
    mono_ok = strong >= w["awr-001"] >= weak
    raised = False
    try:
        select_weights(fit, focus="查无此人")
    except ValueError:
        raised = True
    ok_all.append(_report("select_weights：比 focus 强的权重 ≥ 比 focus 弱的、Σw=1、floor 抬底、坏 focus 报错",
                          mono_ok and sum_ok and floor_ok and raised,
                          f"bc-001={strong:.4f} ≥ awr-001={w['awr-001']:.4f} ≥ first={weak:.4f}；"
                          f"Σ={sum(w.values()):.6f}；min={min(w.values()):.4f} ≥ {0.05 / n:.4f}"))
    w0 = select_weights(fit, focus="awr-001", floor=0.0)
    ok_all.append(_report("select_weights：exponent=0 ⇒ 均匀（与 θ 无关）",
                          max(abs(v - 1.0 / n) for v in select_weights(fit, focus="awr-001",
                                                                       exponent=0.0).values()) < 1e-12,
                          f"floor=0 时 min={min(w0.values()):.4f}（不加 floor 就是软最大，可 < floor/N）"))

    # 7. sample_opponents：可复现、不放回、不含 exclude、权重 0 抽不到、池子不足给几个算几个
    weights = select_weights(fit, focus="awr-001", floor=0.0)
    weights["dead"] = 0.0
    a = sample_opponents(weights, 3, rng=np.random.default_rng(7), exclude="awr-001")
    b = sample_opponents(weights, 3, rng=np.random.default_rng(7), exclude="awr-001")
    c = sample_opponents(weights, 99, rng=np.random.default_rng(8), exclude="awr-001")
    ok_all.append(_report("sample_opponents：同 seed 逐元素可复现 + 不放回 + 不含 exclude + 0 权重抽不到",
                          a == b and len(set(a)) == len(a) and "awr-001" not in a and "dead" not in c
                          and len(c) == 3 and set(c) <= {"bc-001", "teacher", "first", "awr-001"},
                          f"{a} / {c}（k=99 只给 3 个：池子不足就少给）"))
    empty = sample_opponents({"a": 0.0, "b": 0.0}, 2, rng=np.random.default_rng(1))
    ok_all.append(_report("sample_opponents：全 0 权重 ⇒ 抽不到任何人（不是「随便给一个」）",
                          empty == [], f"返回 {empty}"))

    # 8. pl_ci：同 seed 可复现、点估计落在区间内、跨 run 分层也跑得通
    two_runs = _synth(truth, 300, seed=20260104, run="run-A") + \
               _synth(truth, 200, seed=20260105, run="run-B")
    ci1 = pl_ci(two_runs, n_boot=40, seed=99)
    ci2 = pl_ci(two_runs, n_boot=40, seed=99)
    fit2 = plackett_luce(two_runs)
    ci_ok = ci1 == ci2 and all(lo - 1e-9 <= fit2.theta[p] <= hi + 1e-9 for p, (lo, hi) in ci1.items())
    ok_all.append(_report("pl_ci：同 seed 完全一致 + 点估计落在区间内（两个 run 分层重抽）",
                          ci_ok, "　".join(f"{p}:[{lo:+.3f},{hi:+.3f}]" for p, (lo, hi) in
                                          sorted(ci1.items()))))

    # 9. load_games：正常读入（顺序按名次）+ 撞车/非排列必须报错
    tmp = _tmp_run_dir()
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        good = {
            "games": 2,
            "by_policy": {r"net:S:\ckpt\awr-002\net.bin": {"games": 2, "avg_rank_points": 12.5},
                          "teacher": {"games": 2, "avg_rank_points": -1.0}},
            "per_game": [
                {"game": 0, "seed": 1,
                 "policies": [r"net:S:\ckpt\awr-002\net.bin", "teacher", "first", "random"],
                 "placement": [1, 3, 4, 2], "rank_points": [61.6, -15.0, -20.0, -26.6]},
                {"game": 1, "seed": 2,
                 "policies": ["teacher", r"net:S:\ckpt\awr-002\net.bin", "random", "first"],
                 "placement": [2, 1, 3, 4], "rank_points": [10.0, 40.0, -20.0, -30.0]},
            ],
        }
        (tmp / "summary.json").write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        gs = load_games([tmp])
        got = (len(gs) == 2 and gs[0].players == ["awr-002", "random", "teacher", "first"]
               and gs[1].players == ["awr-002", "teacher", "random", "first"]
               and gs[0].run == tmp.name)
        ok_all.append(_report("load_games：正常读入（短名、按名次排序、run 名）", got,
                              f"{gs[0].players} / {gs[1].players}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    tmp = _tmp_run_dir()
    try:
        dup = {"per_game": [{"game": 0, "seed": 1,
                             "policies": [r"net:S:\mahjong-training\ckpt\awr-002\net.bin",
                                          r"net:S:\mirror\ckpt\awr-002\net.bin",
                                          "first", "teacher"],
                             "placement": [1, 2, 3, 4]}]}
        (tmp / "summary.json").write_text(json.dumps(dup), encoding="utf-8")
        caught_dup = False
        try:
            load_games([tmp])
        except ValueError as e:
            caught_dup = "撞车" in str(e)
        bads = [[1, 1, 3, 4], [1, 2, 3, 5], [1, 2, 3], None]
        caught_place = True
        for place in bads:
            (tmp / "summary.json").write_text(json.dumps(
                {"per_game": [{"game": 0, "policies": ["a", "b", "c", "d"], "placement": place}]}),
                encoding="utf-8")
            try:
                load_games([tmp])
                caught_place = False
            except ValueError:
                pass
        ok_all.append(_report("load_games：短名撞车 / placement 非 1..4 排列 ⇒ 必须报错（红证）",
                              caught_dup and caught_place,
                              f"短名撞车被拒={caught_dup}；四种坏名次全被拒={caught_place}"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # 10. 退化输入：空 games / 一场 2 人局都不许返回 NaN
    fit_edge = plackett_luce([])
    fit_one = plackett_luce([Game(players=["a", "b"], run="r")])
    edge_ok = (fit_edge.theta == {} and fit_edge.se == {} and fit_edge.games == 0
               and all(math.isfinite(v) for v in fit_one.se.values())
               and len(pl_ci([], n_boot=10)) == 0
               and fit_one.theta["a"] > fit_one.theta["b"])
    ok_all.append(_report("退化输入：空 games / 一场 2 人局 —— 不返回 NaN，赢家 θ 更大",
                          edge_ok, f"2 人局 θ：{fit_one.theta}，se={fit_one.se}"))

    # 11. 2 人局：SE 与解析式对拍（var(θ₁) = 1/(4·n·p(1−p))）—— 数值 Hessian 的量纲检验
    two = _synth({"a": 0.5, "b": -0.5}, 4000, seed=20260106)
    fit_two = plackett_luce(two)
    p_hat = float(np.mean([g.players[0] == "a" for g in two]))
    se_analytic = 1.0 / math.sqrt(4.0 * len(two) * p_hat * (1.0 - p_hat))
    rel = abs(fit_two.se["a"] - se_analytic) / se_analytic
    ok_all.append(_report("2 人局：数值 Hessian 的 SE 与解析式对拍（相对差 < 20%）", rel < 0.20,
                          f"se={fit_two.se['a']:.4f} vs 解析 {se_analytic:.4f}（差 {rel:.1%}）"))

    print(f"\n{'SELFTEST PASS' if all(ok_all) else 'SELFTEST FAIL'}："
          f"{sum(ok_all)}/{len(ok_all)} 项通过")
    return 0 if all(ok_all) else 1


if __name__ == "__main__":
    raise SystemExit(main())
