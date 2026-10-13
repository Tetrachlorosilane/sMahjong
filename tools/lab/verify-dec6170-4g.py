# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
r"""独立复算 final-dec6170-4g 闸门读数（不读 gate.py 的结论，只读 summary.json）。

判据约定（与 `mahjong_ml.eval.seat_values` 一致）：
  rank_points：逐场逐席 `rank_points[i]`（正 = 更好）
  deals：逐场逐席 `-deals[i]`（**取负** ⇒ 正 = 放铳更少 = 更好）
  wins / win_points：原值
配对差分 = 候选 − 现任（逐场，按 `seed` 对齐，四套牌山 seed 互不相同 ⇒ 直接并起来）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(r"S:\mahjong-training\gate\final-dec6170-4g")
INC = "v4-expert-defrep-g10"
CAND = "v4-wl-dec6170-4g-c2-g04"


def series(summary: dict, label_sub: str, metric: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for g in summary["per_game"]:
        vals = []
        for i, lab in enumerate(g["policies"]):
            if label_sub not in lab:
                continue
            if metric == "rank_points":
                vals.append(float(g["rank_points"][i]))
            elif metric == "deals":
                vals.append(-float(g["deals"][i]))
            elif metric == "wins":
                vals.append(float(g["wins"][i]))
            elif metric == "riichi":
                vals.append(float(g["riichi"][i]))
            else:
                raise KeyError(metric)
        if vals:
            out[int(g["seed"])] = sum(vals) / len(vals)
    return out


def boot_ci(d: np.ndarray, n_boot: int = 20000, alpha: float = 0.05, seed: int = 20261010):
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, d.size, size=(n_boot, d.size))
    means = d[idx].mean(axis=1)
    lo, hi = np.percentile(means, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return float(lo), float(hi)


def main() -> int:
    walls = sorted([p for p in ROOT.glob("s5*") if (p / "summary.json").is_file()])
    if not walls:
        print("还没有 summary.json")
        return 1
    metrics = ["rank_points", "deals", "wins"]
    keys = None
    per_wall: dict[str, list[np.ndarray]] = {m: [] for m in metrics}
    pooled_diffs: dict[str, np.ndarray] = {}
    for m in metrics:
        diffs = []
        for w in walls:
            s = json.loads((w / "summary.json").read_text(encoding="utf-8"))
            if keys is None:
                keys = sorted(s["per_game"][0].keys())
            a = series(s, INC, m)
            b = series(s, CAND, m)
            common = sorted(set(a) & set(b))
            d = np.array([b[k] - a[k] for k in common], dtype=float)
            diffs.append(d)
            per_wall[m].append(d)
        pooled_diffs[m] = np.concatenate(diffs)

    print("per_game 字段：", keys)
    print(f"牌山数 {len(walls)}：{[w.name for w in walls]}")
    for m in metrics:
        print(f"\n== {m} ==")
        for w, d in zip(walls, per_wall[m]):
            lo, hi = boot_ci(d)
            print(f"  {w.name}: Δ={d.mean():+7.3f} CI[{lo:+7.3f},{hi:+7.3f}] n={d.size}")
        d = pooled_diffs[m]
        lo, hi = boot_ci(d)
        print(f"  合并: Δ={d.mean():+7.3f} CI[{lo:+7.3f},{hi:+7.3f}] n={d.size}"
              f"  sd={d.std(ddof=1):.3f}  se={d.std(ddof=1)/np.sqrt(d.size):.3f}")
        print(f"  套间极差={max(x.mean() for x in per_wall[m]) - min(x.mean() for x in per_wall[m]):.3f}")

    # ---- 交换率：每 +1pp 立直率 ≈ 让出多少顺位点（只用本轮 12,000 场；缺 riichi 列则退回报告实测值）----
    rp = pooled_diffs["rank_points"].mean()
    if keys and "riichi" in keys:
        print("\n(本轮 summary 带 riichi 列)")
    else:
        known_pp = [2.13, 3.40, 3.33]
        mean_pp = sum(known_pp) / len(known_pp)
        print(f"\n[交换率 · 分母用报告实测立直率 Δ 三链 {known_pp} 均值 = +{mean_pp:.2f}pp"
              f"（1000 场链内配对，本轮未重测）]")
        print(f"  顺位点 Δ = {rp:+.3f} ⇒ 每 +1pp 立直率 ≈ {rp / mean_pp:+.3f} 顺位点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
