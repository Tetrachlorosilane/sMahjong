"""RERUN 读数与判决（纯读数：只读 `_rerun\\gate\\*` 的逐场产物，⛔ 不重跑任何自对弈）。

口径（预注册 = `S:\\mahjong-training\\w-ladder\\RERUN-PLAN.md` §2，与 `BL13-PLAN.md` §1 同源）：
  主口径 = `rank_points`（顺位点/场/席），Δ = **A − B**，逐场按 seed 配对并跨族合并。
  · 严格判据 = **Bonferroni（族数 ⇒ α=0.05/k）修正后 CI 排除 0** ∧ 各族逐族同号 ∧ 方向。
  · 每条护栏报 **tol / MDE 比值**；**比值 <1 的护栏不得作否决依据**。
  · 安慰剂 = 打乱 B 的 seed→值映射后重算同一统计量（沿用 BL-13 定义，便于直接对照）。
  · ρ（可识别性）：两臂同族同桌坐**不同**席位（`--rotate-perm` 24 排列）⇒ 学生席口径，ρ 可识别。
  · **四种结果（跑前写死）**：① A 胜 / ② 无差异（该分辨率下测不出）/ ③ B 胜 / ④ 测不出（护栏破·族间异号·预算没训满）。

用法： python rerun_readout.py --a <A 的 net 标签> --b <B 的 net 标签>
        --family <seed>=<目录> ... --out <verdict.json>
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "python" / ".venv" / "Lib" / "site-packages"))

import numpy as np                                          # noqa: E402
from mahjong_ml import eval as ml_eval                      # noqa: E402

GUARDS: list[tuple[str, float | None]] = [("deals", 1.0), ("wins", 0.05), ("win_points", 300.0)]
PRIMARY = "rank_points"


def series_of(run_dir: Path, label: str, metric: str) -> dict[int, float]:
    run = ml_eval.load_run(run_dir)
    if label not in run.by_policy:
        raise SystemExit(f"⛔ {run_dir} 里没有策略 {label}（有的：{sorted(run.by_policy)}）")
    return ml_eval.per_game_series(run, label, metric)


def diffs_of(sa: dict[int, float], sb: dict[int, float]) -> np.ndarray:
    return np.array([sa[s] - sb[s] for s in sorted(set(sa) & set(sb))], dtype=float)


def bonf_ci(d: np.ndarray, alpha: float) -> tuple[float, float]:
    from mahjong_ml.eval import _paired_from_diffs
    p = _paired_from_diffs("A", "B", "x", d)
    z = float(np.percentile(np.abs(np.random.default_rng(0).standard_normal(400000)),
                            100 * (1 - alpha)))
    half = z * p.sd / math.sqrt(p.n) if p.n else float("nan")
    return float(p.mean - half), float(p.mean + half)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--family", action="append", required=True, metavar="SEED=DIR")
    ap.add_argument("--out", required=True)
    ap.add_argument("--primary", default=PRIMARY)
    ap.add_argument("--compliance", default=None,
                    help="合规/操作检查 JSON（①②③ 结论）；给 `fail` ⇒ 判决强制落 ④")
    a = ap.parse_args()

    fams = [(s.partition("=")[0], Path(s.partition("=")[2])) for s in a.family]
    out: dict = {"primary_metric": a.primary, "delta_sign": "A − B（正 = 臂 A 更好）",
                 "label_a": a.a, "label_b": a.b, "families": [], "guards": [], "placebo": {}}

    all_a: dict[int, float] = {}
    all_b: dict[int, float] = {}
    per_fam: list[dict] = []
    for seed, d in fams:
        sa = series_of(d, a.a, a.primary)
        sb = series_of(d, a.b, a.primary)
        p = ml_eval.paired_test(sa, sb, "A", "B", a.primary)
        se = p.sd / math.sqrt(p.n)
        per_fam.append({"seed": seed, "dir": str(d), "n": p.n, "delta": p.mean, "se": se,
                        "t": p.mean / se, "ci": [p.ci[0], p.ci[1]], "mde": 1.96 * se,
                        "win": p.win, "lose": p.lose, "tie": p.tie,
                        "ci_excl_0": bool(p.ci[0] * p.ci[1] > 0)})
        all_a.update(sa)
        all_b.update(sb)
    pooled = ml_eval.paired_test(all_a, all_b, "A", "B", a.primary)
    pooled_se = pooled.sd / math.sqrt(pooled.n)
    d = diffs_of(all_a, all_b)
    k = len(fams)
    lo_b, hi_b = bonf_ci(d, 0.05 / k)
    same_sign = all(x["delta"] > 0 for x in per_fam) or all(x["delta"] < 0 for x in per_fam)
    mde = 1.96 * pooled_se
    out["families"] = per_fam
    out["pooled"] = {
        "n": pooled.n, "delta": pooled.mean, "se": pooled_se, "t": pooled.mean / pooled_se,
        "ci": [pooled.ci[0], pooled.ci[1]], "ci_excludes_zero": bool(pooled.ci[0] * pooled.ci[1] > 0),
        "bonf_alpha": 0.05 / k, "bonf_ci": [lo_b, hi_b],
        "bonf_excludes_zero": bool(lo_b * hi_b > 0),
        "win": pooled.win, "lose": pooled.lose, "tie": pooled.tie, "p_sign": pooled.p,
        "mde": mde, "mde_note": f"只有 ≥{max(1.4, round(mde, 1)):.1f} 顺位点的效应可判（MDE={mde:.2f}）",
        "same_sign_all_families": bool(same_sign),
        "family_spread": max(x["delta"] for x in per_fam) - min(x["delta"] for x in per_fam),
        "ci_halfwidth": (pooled.ci[1] - pooled.ci[0]) / 2}
    out["pooled"]["spread_over_halfwidth"] = (out["pooled"]["family_spread"]
                                              / out["pooled"]["ci_halfwidth"])
    # 所需 N：检出**观测到的** Δ 与检出 1.4 顺位点，α=0.05、功效 80%
    out["power"] = {
        "n_now": pooled.n,
        "required_n_for_observed_delta": ml_eval.required_n(pooled.sd, abs(pooled.mean)),
        "required_n_for_delta_1.4": ml_eval.required_n(pooled.sd, 1.4),
        "required_n_for_delta_2.5": ml_eval.required_n(pooled.sd, 2.5)}

    for metric, tol in GUARDS:
        if metric == a.primary:
            continue
        ga: dict[int, float] = {}
        gb: dict[int, float] = {}
        for _seed, dd in fams:
            ga.update(series_of(dd, a.a, metric))
            gb.update(series_of(dd, a.b, metric))
        p = ml_eval.paired_test(ga, gb, "A", "B", metric)
        gmde = 1.96 * p.sd / math.sqrt(p.n)
        out["guards"].append({
            "metric": metric, "tol": tol, "delta": p.mean, "ci": [p.ci[0], p.ci[1]], "n": p.n,
            "mde": gmde, "tol_over_mde": (None if tol is None else tol / gmde),
            "usable_as_veto": bool(tol is not None and gmde > 0 and tol / gmde >= 1.0),
            "ok": bool(tol is None or p.ci[0] >= -tol)})

    rng = np.random.default_rng(20261013)
    keys = sorted(all_b)
    vals = np.array([all_b[i] for i in keys], dtype=float)
    rng.shuffle(vals)
    sh = {i: float(v) for i, v in zip(keys, vals)}
    pl = ml_eval.paired_test(all_a, sh, "A", "B", a.primary)
    out["placebo"] = {"what": "打乱 B 的 seed→逐场值映射后重算同一统计量（BL-13 同款定义）",
                      "delta": pl.mean, "ci": [pl.ci[0], pl.ci[1]], "n": pl.n,
                      "mde": 1.96 * pl.sd / math.sqrt(pl.n),
                      "ci_excludes_zero": bool(pl.ci[0] * pl.ci[1] > 0)}
    out["rho_identifiability"] = {
        "why": "两臂在同一族/同一桌坐**不同**席位（`--rotate-perm` 的 24 个全排列）⇒ 学生席口径；"
               "不是零和对开（零和对开下 ρ(Δ,基准) 恒为 −1，只能判可识别性）",
        "rotate_perm": True, "student_seats": 2}

    broke = [g["metric"] for g in out["guards"] if g["usable_as_veto"] and not g["ok"]]
    comp = None
    if a.compliance:
        cp = Path(a.compliance)
        comp = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else {"verdict": a.compliance}
    out["compliance"] = comp
    comp_fail = bool(comp and comp.get("verdict") == "fail")
    if comp_fail:
        verdict, bucket = "④ **测不出** —— 操作检查①②③ 不成立（自变量仍不可判）⇒ ⛔ 按预注册不跑闸门", "4"
    elif not same_sign:
        verdict, bucket = "④ **测不出** —— 族间异号（判决只对本族成立，⛔ 不跨族外推）", "4"
    elif broke:
        verdict, bucket = (f"④ **测不出** —— 护栏破（可用作否决者：{broke}）", "4")
    elif lo_b > 0:
        verdict, bucket = "① **A 显著更强 ⇒ 新鲜度胜**（等算力下「勤采」优于「囤数据」）", "1"
    elif hi_b < 0:
        verdict, bucket = "③ **B 显著更强 ⇒ 数据量胜**（冻结数据上多训更划算）", "3"
    elif pooled.ci[0] * pooled.ci[1] > 0:
        side = "A" if pooled.ci[0] > 0 else "B"
        verdict, bucket = (f"② **无差异（该分辨率下测不出）** —— 95%CI 排除 0 且指向 {side}，"
                           f"但 **Bonferroni 修正后的 CI 含 0** ⇒ 严格判据不满足；方向性提示 = {side} 数值更强", "2")
    else:
        verdict, bucket = "② **无差异（等算力下新鲜度不是瓶颈）** —— CI 含 0，报 MDE 与所需 N", "2"
    out["pooled"]["verdict"] = verdict
    out["pooled"]["verdict_bucket"] = bucket
    out["pooled"]["adopt"] = bool(lo_b > 0 and same_sign and not broke)

    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[out] {a.out}")
    print(f"  合并 Δ={pooled.mean:+.4f} SE={pooled_se:.4f} t={pooled.mean / pooled_se:+.3f} "
          f"CI[{pooled.ci[0]:+.4f},{pooled.ci[1]:+.4f}] 实际MDE={mde:.4f} n={pooled.n}")
    print(f"  ⚠ {out['pooled']['mde_note']}")
    print(f"  Bonferroni({k} 族, α={0.05 / k:.5f}) CI[{lo_b:+.4f},{hi_b:+.4f}] · 逐族同号={same_sign}"
          f" · 族间极差={out['pooled']['family_spread']:.4f} CI半宽={out['pooled']['ci_halfwidth']:.4f}"
          f" = {out['pooled']['spread_over_halfwidth']:.2f}×")
    for x in per_fam:
        print(f"  族 {x['seed']}: Δ={x['delta']:+.4f} n={x['n']} "
              f"CI[{x['ci'][0]:+.3f},{x['ci'][1]:+.3f}] MDE={x['mde']:.3f} 排除0={x['ci_excl_0']}")
    for g in out["guards"]:
        print(f"  护栏 {g['metric']}: Δ={g['delta']:+.4f} CI[{g['ci'][0]:+.3f},{g['ci'][1]:+.3f}] "
              f"tol={g['tol']} MDE={g['mde']:.4f} tol/MDE={g['tol_over_mde']:.2f} "
              f"usable_as_veto={g['usable_as_veto']} ok={g['ok']}")
    print(f"  安慰剂: Δ={pl.mean:+.4f} CI[{pl.ci[0]:+.4f},{pl.ci[1]:+.4f}] "
          f"排除0={out['placebo']['ci_excludes_zero']}")
    print(f"  功效：所需 N（观测 Δ={abs(pooled.mean):.2f}）= {out['power']['required_n_for_observed_delta']} · "
          f"（Δ=1.4）= {out['power']['required_n_for_delta_1.4']} · （Δ=2.5）= {out['power']['required_n_for_delta_2.5']}")
    print(f"  判决[{bucket}]：{verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
