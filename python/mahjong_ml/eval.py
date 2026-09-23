"""P0 评测口径：**顺位点** + **配对显著性**（`docs/TRAINING.md` §4 P0 / §7）。

为什么要有它：`avg_place` 与和了率**测不出顺位意识**（领先时少赢一把、把放铳率压下去，
这两项都要吃亏），而顺位点才是这项运动的记分。更关键的是**牌山方差极大**
（实测 σ(placement)≈1.12），不配对、不算置信区间的"变强了"全是噪声。

    python -m mahjong_ml.eval <dir>                  # 单次 run：各策略指标 + 95%CI + 组内配对检验
    python -m mahjong_ml.eval <dirA> <dirB>          # 两次 run：**按 seed 配对**的 A/B 检验
    python -m mahjong_ml.eval <dir> --json out.json  # 附机器可读结果

判据（都打印出来，方便直接写进结论）：
  · 自助法 95% 置信区间（固定 RNG 种子 → **同输入结果可复现**）；
  · **符号检验** p 值（精确二项，不依赖正态近似）；
  · 按实测的差值标准差反推"还要打多少场才能检出 Δ"（对应 §7 的样本量表）。

约定：**正数一律表示"更好"**，所以 `place` 指标取负（`-placement`）；默认指标是 `rank_points`（顺位点）。
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

METRICS = ("rank_points", "place")
ALPHA = 0.05
BOOT_DEFAULT = 10_000
BOOT_SEED = 20260101          # 固定：同输入 → 同置信区间（可复现是硬要求）


# ------------------------------------------------------------------ 载入

@dataclass
class Run:
    path: Path
    games: int
    hands: int
    seed_base: int | None
    workers: int | None
    by_policy: dict
    per_game: list

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)


def load_run(d: str | Path) -> Run:
    """读一次自对弈的 `summary.json`（P0 只依赖它：`per_game` 带四家顺位点）。"""
    d = Path(d)
    f = d / "summary.json" if d.is_dir() else d
    if not f.is_file():
        raise FileNotFoundError(f"找不到 summary.json：{f}")
    s = json.loads(f.read_text(encoding="utf-8"))
    pg = s.get("per_game", [])
    if not pg:
        raise ValueError(f"{f} 里没有 per_game（配对检验需要它）")
    missing = [g.get("game") for g in pg if "rank_points" not in g]
    if missing:
        raise ValueError(
            f"per_game 缺 rank_points（前几场：{missing[:5]}）—— 那是顺位点指标，"
            f"用带它的服务端重跑自对弈（docs/PROTOCOL.md §8.4）")
    return Run(path=d, games=s.get("games", len(pg)), hands=s.get("hands", 0),
               seed_base=s.get("seed_base"), workers=s.get("workers"),
               by_policy=s.get("by_policy", {}), per_game=pg)


# ------------------------------------------------------------------ 逐场取值

def seat_values(row: dict, label: str, metric: str) -> list[float]:
    """一场里该策略占的座位上的指标值（一般 1 个；同标签多座位时取平均）。"""
    out = []
    for i, lab in enumerate(row.get("policies", [])):
        if lab != label:
            continue
        if metric == "rank_points":
            out.append(float(row["rank_points"][i]))
        elif metric == "place":
            out.append(-float(row["placement"][i]))     # 取负：正数=更好
        else:
            raise ValueError(f"未知指标 {metric}（可用：{', '.join(METRICS)}）")
    return out


def per_game_series(run: Run, label: str, metric: str) -> dict[int, float]:
    """`seed → 该场该策略的指标`（**按 seed 索引**是跨 run 配对的前提）。"""
    series: dict[int, float] = {}
    for g in run.per_game:
        vals = seat_values(g, label, metric)
        if vals:
            series[int(g["seed"])] = sum(vals) / len(vals)
    return series


def seat_series(run: Run, label: str, metric: str) -> dict[tuple[int, int], float]:
    """`(seed, 座位) → 指标` —— 跨 run 比较**不同策略**时必须按座位配（同一副牌山 + 同一个位次）。"""
    out: dict[tuple[int, int], float] = {}
    for g in run.per_game:
        seed = int(g["seed"])
        for i, lab in enumerate(g.get("policies", [])):
            if lab != label:
                continue
            out[(seed, i)] = (float(g["rank_points"][i]) if metric == "rank_points"
                              else -float(g["placement"][i]))
    return out


# ------------------------------------------------------------------ 统计

def bootstrap_ci(values: np.ndarray, n_boot: int = BOOT_DEFAULT,
                 alpha: float = ALPHA) -> tuple[float, float]:
    """均值的自助法置信区间（固定种子 → 可复现）。"""
    if values.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(BOOT_SEED)
    idx = rng.integers(0, values.size, size=(n_boot, values.size))
    means = values[idx].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def sign_test_p(diffs: np.ndarray) -> float:
    """双侧**符号检验**的精确 p 值（丢掉 0；n=0 时返回 1）。"""
    pos = int(np.sum(diffs > 0))
    neg = int(np.sum(diffs < 0))
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return float(min(1.0, 2 * tail))


def required_n(sd: float, delta: float, alpha: float = ALPHA) -> int:
    """要检出 `delta` 的配对差值、在观测到的 `sd` 下需要多少场（正态近似，单样本配对）。"""
    if delta <= 0 or not math.isfinite(sd) or sd <= 0:
        return 0
    z = 1.959963985 if abs(alpha - 0.05) < 1e-9 else 1.959963985
    return int(math.ceil((z * sd / delta) ** 2))


@dataclass
class Paired:
    a: str
    b: str
    metric: str
    n: int
    mean: float
    sd: float
    ci: tuple[float, float]
    p: float
    win: int
    lose: int
    tie: int


def _paired_from_diffs(a: str, b: str, metric: str, diffs: np.ndarray) -> Paired:
    return Paired(a=a, b=b, metric=metric, n=int(diffs.size),
                  mean=float(diffs.mean()) if diffs.size else float("nan"),
                  sd=float(diffs.std(ddof=1)) if diffs.size > 1 else float("nan"),
                  ci=bootstrap_ci(diffs), p=sign_test_p(diffs),
                  win=int((diffs > 0).sum()), lose=int((diffs < 0).sum()),
                  tie=int((diffs == 0).sum()))


def paired_test(a_series: dict[int, float], b_series: dict[int, float],
                a: str, b: str, metric: str) -> Paired:
    """按 seed 交集做配对（**同一副牌山**），差值 = a − b（正 = a 更好）。"""
    seeds = sorted(set(a_series) & set(b_series))
    diffs = np.array([a_series[s] - b_series[s] for s in seeds], dtype=float)
    return _paired_from_diffs(a, b, metric, diffs)


def paired_seat_test(sa: dict[tuple[int, int], float], sb: dict[tuple[int, int], float],
                     a: str, b: str, metric: str) -> Paired:
    """按 `(seed, 座位)` 交集配对 —— **比较两个不同策略**的正解：
    同一副牌山、同一个位次，差值 = a 在 A 的位次 − b 在 B 的位次（正 = a 更好）。
    """
    keys = sorted(set(sa) & set(sb))
    diffs = np.array([sa[k] - sb[k] for k in keys], dtype=float)
    return _paired_from_diffs(a, b, metric, diffs)


# ------------------------------------------------------------------ 报告

def _fmt_p(p: float) -> str:
    if not math.isfinite(p):
        return "n/a"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def format_single(run: Run, metric: str) -> str:
    lines = [f"== 自对弈 run：{run.name}",
             f"   场数 {run.games} / 小局 {run.hands}"
             + (f"（seed_base={run.seed_base}，workers={run.workers}）" if run.seed_base else ""),
             f"指标：{'顺位点' if metric == 'rank_points' else '顺位（取负，正=更好）'}"
             f"　（置信区间 = 自助法 {BOOT_DEFAULT} 次，固定种子可复现）", ""]
    head = f"{'策略':<12}{'场数':>6}{'平均顺位':>10}{'平均顺位点':>12}{'95%CI':>20}{'和了率':>9}{'放铳率':>9}{'平均收支':>10}"
    lines.append(head)
    for label, st in sorted(run.by_policy.items(),
                            key=lambda kv: -(kv[1].get("avg_rank_points") or 0)):
        series = per_game_series(run, label, "rank_points")
        vals = np.array(list(series.values()), dtype=float)
        lo, hi = bootstrap_ci(vals)
        rp = st.get("avg_rank_points")
        lines.append(f"{label:<12}{st.get('games', 0):>6}{st.get('avg_place', 0):>10.3f}"
                     f"{(rp if rp is not None else float('nan')):>12.2f}"
                     f"{f'[{lo:+.1f}, {hi:+.1f}]':>20}"
                     f"{100*st.get('win_rate', 0):>8.1f}%{100*st.get('deal_in_rate', 0):>8.1f}%"
                     f"{st.get('avg_delta', 0):>10.0f}")
    labels = list(run.by_policy.keys())
    if len(labels) >= 2:
        series = {lab: per_game_series(run, lab, metric) for lab in labels}
        lines += ["", f"组内配对检验（同一副牌山按场配对；正 = 前者更好，指标={metric}）："]
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                a, b = labels[i], labels[j]
                pr = paired_test(series[a], series[b], a, b, metric)
                if pr.n == 0:
                    continue
                lines.append(f"  {a} vs {b}: n={pr.n}  Δ={pr.mean:+.2f}  "
                             f"95%CI=[{pr.ci[0]:+.2f},{pr.ci[1]:+.2f}]  p={_fmt_p(pr.p)}  "
                             f"胜{pr.win}/负{pr.lose}/平{pr.tie}  sd(Δ)={pr.sd:.2f}  "
                             f"（检出 Δ=2.0 约需 {required_n(pr.sd, 2.0)} 场）")
    return "\n".join(lines)


def format_pair(ra: Run, rb: Run, metric: str, labels: tuple[str, str] | None = None) -> str:
    lines = [f"== A/B：{ra.name} vs {rb.name}（**按 seed 配对**，指标={metric}）",
             f"   {ra.name}: {ra.games} 场 / {rb.name}: {rb.games} 场", ""]
    if labels is not None:
        la, lb = labels
        pr = paired_seat_test(seat_series(ra, la, metric), seat_series(rb, lb, metric),
                              f"{ra.name}:{la}", f"{rb.name}:{lb}", metric)
        lines.append("按**座位**配对（同一副牌山 + 同一个位次）——比较两个**不同策略**的正解：")
        if pr.n == 0:
            lines.append(f"  {la} vs {lb}: 没有共同 (seed, 座位)，无法配对（两次 run 要用同一 --seed）")
        else:
            lines.append(f"  {la} vs {lb}: n={pr.n}  Δ={pr.mean:+.2f}  "
                         f"95%CI=[{pr.ci[0]:+.2f},{pr.ci[1]:+.2f}]  p={_fmt_p(pr.p)}  "
                         f"胜{pr.win}/负{pr.lose}/平{pr.tie}  sd(Δ)={pr.sd:.2f}  "
                         f"（检出 Δ=2.0 约需 {required_n(pr.sd, 2.0)} 组座位-场）")
        lines += ["", "读法：CI 跨 0 或 p≥0.05 → **证不出差别**（牌山方差大，别拿单次跑分下结论）；",
                  "      要检出更小的 Δ，按上面的 sd(Δ) 反推场次（docs/TRAINING.md §7）。"]
        return "\n".join(lines)

    labels_common = sorted(set(ra.by_policy) & set(rb.by_policy))
    if not labels_common:
        lines.append("两次 run 没有共同策略标签 —— 想比较不同策略请用 --labels A,B（按座位配对）")
        return "\n".join(lines)
    lines.append("同名策略跨 run 的配对（用来量「对手场」或「规则改动」的影响）：")
    for lab in labels_common:
        pr = paired_test(per_game_series(ra, lab, metric), per_game_series(rb, lab, metric),
                         f"{ra.name}:{lab}", f"{rb.name}:{lab}", metric)
        if pr.n == 0:
            lines.append(f"  {lab}: 两次 run 没有共同 seed，无法配对（改同一 --seed 重跑）")
            continue
        lines.append(f"  {lab}: n={pr.n}  Δ={pr.mean:+.2f}  "
                     f"95%CI=[{pr.ci[0]:+.2f},{pr.ci[1]:+.2f}]  p={_fmt_p(pr.p)}  "
                     f"胜{pr.win}/负{pr.lose}/平{pr.tie}  sd(Δ)={pr.sd:.2f}")
    lines += ["", "读法：CI 跨 0 或 p≥0.05 → **证不出差别**（牌山方差大，别拿单次跑分下结论）；",
              "      要比较**不同策略**（如 网络 vs teacher）请用 --labels 网络,teacher。"]
    return "\n".join(lines)


def to_json(runs: dict[str, Run], metric: str) -> dict:
    out: dict = {"metric": metric, "runs": {}}
    for name, run in runs.items():
        pol: dict = {}
        for label, st in run.by_policy.items():
            vals = np.array(list(per_game_series(run, label, "rank_points").values()), dtype=float)
            lo, hi = bootstrap_ci(vals)
            pol[label] = {**st, "rank_points_ci": [lo, hi]}
        out["runs"][name] = {"games": run.games, "hands": run.hands,
                            "seed_base": run.seed_base, "by_policy": pol}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P0 评测：顺位点 + 配对显著性")
    ap.add_argument("dirs", nargs="+", help="一次（或两次）自对弈的输出目录")
    ap.add_argument("--metric", default="rank_points", choices=METRICS)
    ap.add_argument("--labels", default=None,
                    help="两次 run 时按**座位**配对比较两个不同策略，如 `--labels net,teacher`")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)

    runs = [load_run(d) for d in args.dirs[:2]]
    if len(runs) == 1:
        print(format_single(runs[0], args.metric))
    else:
        labels = None
        if args.labels:
            parts = [x.strip() for x in args.labels.split(",") if x.strip()]
            if len(parts) != 2:
                ap.error("--labels 需要恰好两个标签，如 `--labels net,teacher`")
            labels = (parts[0], parts[1])
        print(format_pair(runs[0], runs[1], args.metric, labels))
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(to_json({r.name: r for r in runs}, args.metric),
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写出 {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
