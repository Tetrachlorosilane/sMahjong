#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`eval-variance` —— **评估侧方差缩减的离线对账工具**（O0 现状 / O1 同山配对+按场聚类 / O2 CUPED）。

要回答的问题（`lab/research/LIT-SYNTHESIS-2026-10.md` D1/D2 + Q2）：
    「一半结构性，但**配对 + CUPED + 序贯**可在不加场次下拿 ≈2× 等效分辨率」——**在这批既有数据上成立吗？**
    若成立，`ceiling-gate.py` 才值得加 `--cuped`；若不成立，就把"加不了"的原因写成公式（⛔ 不硬凑）。

⛔ **本工具零采集、零训练、零新算力**：只读既有 `summary.json` 的 `per_game[]`（逐场：seed / 四席 `policies` /
`rank_points` / `final_scores` / `wins` / `deals` / `win_points` / `hands` / `ryukyoku`），完全离线。

⚠ **O0 一个统计量都不自己算**：Δ / sd / 自助 CI / 符号检验 / 需求场次一律走既有的
`mahjong_ml.eval`（`paired_test` / `bootstrap_ci` / `required_n`）—— 与 `tools/ceiling-gate.py` 逐位同源，
所以"O0 == 闸门读数"是可核对的（`--expect` 可断言既有 `verdict.json` 的 Δ/SE）。
⚠ O1/O2 是**新增估计器**（仓内原本没有），公式与实现要点写在 `lab/mv1/EVAL-VARIANCE.md`。

三种估计器（都估同一个量：候选臂学生席的**配对差** Δ = cand − base，正 = 候选更好）：

  O0  **现状**：`Δ̄ = mean(d)`，`SE = sd(d)/√N`（N = 跨族池化的**场**数，一组族 = 一套牌山）。
       —— 这就是闸门现在的口径；它**已经是同山配对 + 按场一个观测**。
  O1  **同山配对 + 按场聚类**：以"牌山(seed)"为簇的 **cluster-robust** SE
       `v̂ = G/(G−1) · Σ_g S_g² / N²`（`S_g` = 第 g 簇的残差和）。
       本数据的簇内只有 **1 个学生席观测** ⇒ 公式**代数上退化成 O0**（工具里断言这一点，比 ≈1）。
       ⚠ 真正的坑不是"少算了聚类"，而是**把四席 / 24 排列当独立样本**（`--seats4`）：零和指标下
       Δ 恒等于 0、簇残差和恒等于 0 ⇒ 既"零信号"又"零 SE"。这一条本工具会实测给你看。
  O2  **CUPED**：协变量 `X_g` = **基线臂在同一副牌山、同一个座位上的结果**（同山天然协变量；判决前可得、
       不受候选臂影响）。`β = Cov(d, X)/Var(X)`，`θ = Δ̄ − β(X̄ − X̄_∅)`，残差方差降 **1−ρ_X²**。
       ⚠ **必须交叉拟合**（本工具 K 折）：β 与参照均值都只用**其余折**估，SE 只用**折外**残差
       —— 否则 β 用同一批数据估，会把 SE 压小（`--insample` 就是那个错误做法的对照）。

判据（**跑前冻写**，见 `lab/mv1/EVAL-VARIANCE.md`）：
    O2 相对 O0：**SE 降低 ≥25%**（等效 n ≥1.78×）**且** 点估计漂移 ≤ 1 个 SE_O0 ⇒ 视为有效；
    ⚠ 若协变量带来方向性偏移 / 负向对照（置换协变量）不≈0 ⇒ 记"不可用"。

退出码：0 = 判据通过（可集成）；1 = 判据不通过（**不集成**，读数仍完整）；
        2 = ERROR（文件/字段/协变量缺失 —— **fail-closed**，⛔ 不许静默退回 O0）；
        3 = 自检失败。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "python" / ".venv" / "Lib" / "site-packages"))

try:
    import numpy as np
except ImportError as e:                                    # pragma: no cover
    print(f"[eval-variance] ERROR：numpy 不可用（{e}）—— 用 python\\.venv\\Scripts\\python.exe 跑", flush=True)
    raise SystemExit(2)

from mahjong_ml import eval as ml_eval                       # noqa: E402

SCHEMA = "eval-variance/1"
Z = 1.959963985
ZP = 0.8416212336        # 80% 功效（与 `eval.required_n` 同一个 k = 2.8016）
START_SCORE = 25000.0
#: 预注册判据（⛔ 别改这些数去凑结论；要改就另开一批并写"口径已动"）
CRIT_SE_DROP = 0.25      # SE 相对 O0 至少降 25%
CRIT_DRIFT_SE = 1.0      # 点估计漂移上限 = 1 个 SE_O0
DEFAULT_FOLDS = 10

#: 既有证据的位置（**只读**；`.tmp/**` 是 gitignored 的既有产物，缺了就 fail-closed）
PRESETS = {
    "s35-pilot": [(f, REPO / "tools/build/mv1-s35-evidence/pilot-summaries" / f"summary-{f}-{a}.json")
                  for f in ("53610001", "53610002", "53610003") for a in ("base", "cand")],
    "s35-formal": [(f, REPO / "tools/build/mv1-s35-evidence" / f"summary-{f}-{a}.json")
                   for f in ("53610001", "53610002", "53610003") for a in ("base", "cand")],
    "s38-pilot": [(f, REPO / ".tmp/mv1-s38/gate/p3-pilot" / f"{f}-{a}" / "summary.json")
                  for f in ("53610001", "53610002", "53610003") for a in ("base", "cand")],
    "s38-formal": [(f, REPO / ".tmp/mv1-s38/gate/p3-formal" / f"{f}-{a}" / "summary.json")
                   for f in ("53610001", "53610002", "53610003") for a in ("base", "cand")],
}


def _say(s: str = "") -> None:
    print(s, flush=True)


# ---------------------------------------------------------------------------
# 载入：逐场（seed / 座位 / 四席指标）+ 学生席识别
# ---------------------------------------------------------------------------
def _student_label(per_game: list) -> str:
    """学生席 = 策略串里带 `@` 的那一个（与 `ceiling-gate._student_label` 同一条判据）。"""
    labs = sorted({str(l) for g in per_game for l in g.get("policies", [])})
    hit = [l for l in labs if "@" in l]
    if len(hit) != 1:
        raise SystemExit(f"[eval-variance] ERROR：带 `@` 的学生席命中 {len(hit)} 个：{hit or labs}")
    return hit[0]


def load_arm(path: str | Path, metric: str) -> dict:
    """一个 run 的逐场表：`seed → {seat, values[4], student, hands, ryukyoku}`。

    ⚠ 四席**全留**（`values`），因为 `--seats4` 那条负向对照要证明"把四席当独立样本"是错的。
    """
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"[eval-variance] ERROR：找不到 summary.json：{p}（fail-closed，⛔ 不静默跳过）")
    s = json.loads(p.read_text(encoding="utf-8"))
    pg = s.get("per_game") or []
    if not pg:
        raise SystemExit(f"[eval-variance] ERROR：{p} 没有 per_game（配对检验需要逐场数据）")
    lab = _student_label(pg)
    out: dict = {"path": str(p), "seed_base": s.get("seed_base"), "student": lab, "games": {}}
    for g in pg:
        pols = [str(x) for x in g.get("policies") or []]
        if len(pols) != 4:
            raise SystemExit(f"[eval-variance] ERROR：{p} 有场次缺四席身份（policies={pols}）")
        if "rank_points" not in g:
            raise SystemExit(f"[eval-variance] ERROR：{p} 的 per_game 缺 rank_points")
        if metric == "score" and "final_scores" not in g:
            raise SystemExit(f"[eval-variance] ERROR：{p} 的 per_game 缺 final_scores")
        vals = [float(v) for v in (g["rank_points"] if metric == "rank_points"
                                   else [x - START_SCORE for x in g["final_scores"]])]
        try:
            seat = pols.index(lab)
        except ValueError:
            raise SystemExit(f"[eval-variance] ERROR：{p} 的某场里没有学生席 {lab}")
        out["games"][int(g["seed"])] = {
            "seat": seat, "values": vals,
            "hands": float(g.get("hands") or 0), "ryukyoku": float(g.get("ryukyoku") or 0),
            # 合法的"基线臂其余结果"协变量（都在**基线**臂上取，不含候选臂自己的结果）
            "wins": [float(x) for x in (g.get("wins") or [0] * 4)],
            "deals": [float(x) for x in (g.get("deals") or [0] * 4)],
            "scores": [float(x) - START_SCORE for x in (g.get("final_scores") or [25000] * 4)],
        }
    return out


def build_families(specs: list, metric: str) -> list:
    """`标签=<base>;<cand>` ⇒ 逐族配对表（**按 seed 配对 = 同一副牌山**）。"""
    fams = []
    for spec in specs:
        label, _, pair = spec.partition("=")
        b, _, c = pair.partition(";")
        if not (label and b and c):
            raise SystemExit(f"[eval-variance] ERROR：--family 语法是 `标签=<base>;<cand>`，收到 {spec!r}")
        rb, rc = load_arm(b, metric), load_arm(c, metric)
        common = sorted(set(rb["games"]) & set(rc["games"]))
        if not common:
            raise SystemExit(f"[eval-variance] ERROR：{label} 两臂没有公共牌山（seed 全不重叠）")
        rows = []
        for s in common:
            gb, gc = rb["games"][s], rc["games"][s]
            if gb["seat"] != gc["seat"]:
                raise SystemExit(f"[eval-variance] ERROR：{label} 在 seed={s} 两臂座位不同"
                                 f"（{gb['seat']} vs {gc['seat']}）⇒ 不是同山同席配对")
            rows.append({"seed": s, "seat": gb["seat"],
                         "Y": gc["values"][gb["seat"]],          # 候选臂学生席
                         "X": gb["values"][gb["seat"]],          # 基线臂学生席（同山同席）
                         "Y4": gc["values"], "X4": gb["values"],
                         "Xw": gb["wins"][gb["seat"]], "Xd": gb["deals"][gb["seat"]],
                         "Xs": gb["scores"][gb["seat"]],
                         "hands": gb["hands"], "ryukyoku": gb["ryukyoku"],
                         "policy_base": rb["student"], "policy_cand": rc["student"]})
        fams.append({"name": label, "n": len(rows), "rows": rows,
                     "student": rb["student"], "cand": rc["student"],
                     "seed_base": rb["seed_base"], "base_path": rb["path"], "cand_path": rc["path"]})
    return fams


# ---------------------------------------------------------------------------
# 估计器
# ---------------------------------------------------------------------------
def se_mean(x) -> float:
    x = np.asarray(x, float)
    return float(x.std(ddof=1) / math.sqrt(x.size)) if x.size > 1 else float("nan")


def _se(x) -> float:
    return se_mean(x)


def iqm(x, trim: float = 0.25) -> float:
    """**IQM**（rliable 的口径：两端各裁 25% 再取均值）。"""
    x = np.sort(np.asarray(x, float))
    k = int(x.size * trim)
    return float(x[k:x.size - k].mean()) if x.size - 2 * k > 0 else float("nan")


def mde(se: float) -> float:
    """设计口径 MDE = k·SE（`k = z_{1−α/2} + z_{80%}`，与 `eval.required_n` 同一个 k）。"""
    return (Z + ZP) * se


def equiv_n(se_ref: float, se: float) -> float:
    """等效 n 倍数 = (SE_ref / SE)²。"""
    return (se_ref / se) ** 2 if (se > 0 and math.isfinite(se)) else float("nan")


def crve(d, clusters) -> tuple:
    """**按簇聚类**的稳健 SE：`v̂ = G/(G−1) · Σ_g S_g² / N²`，`S_g` = 该簇**残差和**（残差已中心化）。

    实现要点：① 残差必须对**估计量本身**取（这里是 `d − d̄`，即配对差的离差），不是对"每行均值"；
    ② 分母是 **N²**（行的总数），不是簇数 —— 换成分母 G² 就是"把簇均值当独立样本"。
    ③ 返回 `(se, G)`；`G == N` 且每簇 1 行时**代数上恒等于** `sd(d)/√N`（工具里断言）。
    """
    d = np.asarray(d, float)
    labs = np.asarray(clusters)
    r = d - d.mean()
    uniq = list(dict.fromkeys(labs.tolist()))
    S = np.array([r[labs == u].sum() for u in uniq])
    G, N = len(uniq), d.size
    if G < 2:
        return float("nan"), G
    return float(math.sqrt(G / (G - 1.0) * float(np.sum(S ** 2)) / (N * N))), G


def cuped(d, z, fam_idx, k: int, mode: str = "cross") -> dict:
    """CUPED（交叉拟合）：`β = Cov(d,z)/Var(z)`，`θ = d̄ − β(z̄ − z̄_∅)`，残差方差 = 判决方差。

    交叉拟合的做法（⛔ 必须）：① 折按 `(族内位置) % k` 切（族均衡、与 seed 无关、可复现）；
    ② 每一折的 β 与 z 的参照均值只用**其余折**；③ SE 用**折外**残差 `r = (d − d̄) − β_k(z − z̄_{−k})`。

    ⚠ `mode="insample"` 是**反面**对照：β/均值都用全样本（会把 SE 压小一点点 —— 本数据 n=4.3k 时
    差异 ~0.05%，但小样本会显著）。
    """
    d = np.asarray(d, float)
    z = np.asarray(z, float)
    n = d.size
    if z.size != n or not np.all(np.isfinite(z)) or float(z.std(ddof=1)) <= 0:
        raise SystemExit("[eval-variance] ERROR：协变量缺失/全常数 ⇒ fail-closed（⛔ 不许静默退回 O0）")
    pos = np.zeros(n, dtype=int)
    for fi in dict.fromkeys(fam_idx.tolist()):
        m = np.asarray(fam_idx) == fi
        pos[m] = np.arange(int(m.sum()))
    fold = pos % k
    if mode == "insample":
        zi = z - z.mean()
        beta = float(np.dot(d - d.mean(), zi) / float(np.dot(zi, zi)))
        r = (d - d.mean()) - beta * zi
        return {"delta": float(d.mean()), "beta": beta, "betas": [beta], "se": _se(r),
                "resid": r, "mode": mode}
    r = np.empty(n)
    betas, adj = [], 0.0
    for f in range(k):
        te, tr = fold == f, fold != f
        if not tr.any() or not te.any():
            continue
        zt = z[tr] - z[tr].mean()
        b = float(np.dot(d[tr] - d[tr].mean(), zt) / float(np.dot(zt, zt)))
        betas.append(b)
        zc = z[te] - z[tr].mean()
        r[te] = (d[te] - d.mean()) - b * zc
        adj += float(np.sum(b * zc))
    r = r - r.mean()                                   # 残差均值归零（O(1/N²) 的记账差）
    return {"delta": float(d.mean()) - adj / n, "beta": float(np.mean(betas)), "betas": betas,
            "se": _se(r), "resid": r, "mode": mode, "adj": adj / n}


def ols_cf(d, Z, fam_idx, k: int) -> dict:
    """多协变量交叉拟合（探索用，**不是判据**）：`d ~ 1 + Z`，β 只用其余折。"""
    d = np.asarray(d, float)
    Z = np.asarray(Z, float)
    n = d.size
    pos = np.zeros(n, dtype=int)
    for fi in dict.fromkeys(np.asarray(fam_idx).tolist()):
        m = np.asarray(fam_idx) == fi
        pos[m] = np.arange(int(m.sum()))
    fold = pos % k
    r = np.empty(n)
    for f in range(k):
        te, tr = fold == f, fold != f
        mu = Z[tr].mean(axis=0)
        A = np.c_[np.ones(int(tr.sum())), Z[tr] - mu]
        beta, *_ = np.linalg.lstsq(A, d[tr], rcond=None)
        r[te] = d[te] - beta[0] - (Z[te] - mu) @ beta[1:]
    r = r - r.mean()
    return {"se": _se(r)}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def analyse(fams: list, metric: str, folds: int, n_perm: int, seed: int) -> dict:
    d = np.array([r["Y"] - r["X"] for f in fams for r in f["rows"]], float)
    Y = np.array([r["Y"] for f in fams for r in f["rows"]], float)
    X = np.array([r["X"] for f in fams for r in f["rows"]], float)
    fam_idx = np.array([i for i, f in enumerate(fams) for _ in f["rows"]], int)
    cluster = np.array([i * 100000 + j for i, f in enumerate(fams) for j, _ in enumerate(f["rows"])],
                       dtype=np.int64)
    n = d.size
    out: dict = {"schema": SCHEMA, "metric": metric, "n": int(n), "families": [], "weight": {}}

    # ---- O0：**走既有实现**（与 ceiling-gate 同源） ----
    sa = {int(k): float(v) for k, v in enumerate(
        [r["Y"] for f in fams for r in f["rows"]])}
    sb = {int(k): float(v) for k, v in enumerate(
        [r["X"] for f in fams for r in f["rows"]])}
    p = ml_eval.paired_test(sa, sb, "cand", "base", metric)
    se0 = float(p.sd) / math.sqrt(p.n)
    out["O0"] = {"delta": float(p.mean), "sd": float(p.sd), "se": se0,
                 "ci": [float(p.ci[0]), float(p.ci[1])],
                 "ci_note": "自助法（`eval.bootstrap_ci`，10,000 次、固定种子）",
                 "mde": mde(se0), "p_sign": float(p.p),
                 "win": int(p.win), "lose": int(p.lose), "tie": int(p.tie),
                 "n": int(p.n), "source": "mahjong_ml.eval.paired_test（既有实现）"}
    out["O0"]["ci_z"] = [float(p.mean - Z * se0), float(p.mean + Z * se0)]
    if abs(se0 - _se(d)) > 1e-9:
        raise SystemExit(f"[eval-variance] ERROR：O0 的 sd/√n 与我自己算的差 >1e-9"
                         f"（{se0} vs {_se(d)}）⇒ 口径分叉，停")

    # ---- 参考：不配对的"两臂当独立"（⛔ 只作参考，不是任何判决口径） ----
    se_unpaired = math.sqrt(Y.var(ddof=1) / n + X.var(ddof=1) / n)
    out["ref_unpaired"] = {"se": se_unpaired,
                           "pairing_gain_equiv_n": (se_unpaired / se0) ** 2,
                           "note": "两臂当独立样本（σ_Y²/N + σ_X²/N）—— 配对**已经**拿到的收益"}
    out["pairing"] = {"rho_YX": float(np.corrcoef(Y, X)[0, 1]),
                      "rho_dX": float(np.corrcoef(d, X)[0, 1]),
                      "sd_Y": float(Y.std(ddof=1)), "sd_X": float(X.std(ddof=1))}

    # ---- O1：按场聚类（1 观测/簇）----
    se1, G = crve(d, cluster)
    out["O1"] = {"se": se1, "G": G, "equiv_n": equiv_n(se0, se1),
                 "ratio_to_O0": se1 / se0,
                 "identity_holds": bool(abs(se1 / se0 - 1.0) < 1e-9),
                 "formula": "v=G/(G-1)*Σ_g S_g²/N²，S_g = 簇内残差和",
                 "note": ("每簇 1 个学生席观测 ⇒ 与 O0 恒等（现有闸门口径**已经**是按场聚类）；"
                          "⛔ 收益不是「加聚类」，而是防止「把四席/24 排列当独立样本」"
                          if abs(se1 / se0 - 1.0) < 1e-9 else "⚠ 与 O0 不等 ⇒ 簇内有多行，需复查口径")}
    # 族当簇（G=3）：只作诊断，⛔ 不可用
    se_f, Gf = crve(d, fam_idx)
    fam_delta = [float(d[fam_idx == i].mean()) for i in range(len(fams))]
    out["O1_family_cluster"] = {
        "se": se_f, "G": Gf, "ratio_to_O0": se_f / se0,
        "family_deltas": fam_delta,
        "design_effect": float(np.std(fam_delta, ddof=1) / se0),
        "note": ("G=3 ⇒ 方差估计只 2 个自由度（χ²_2），两个方向都不可靠：实测比值在 0.54–1.03 之间跳"
                 "（s35-formal 0.536 / s38-formal 1.03）⇒ ⛔ 不许当判决口径"),
        "usable": False}

    # ---- O1b：把四席当独立样本（错的写法，实测给你看）----
    d4, cl4 = [], []
    for i, f in enumerate(fams):
        for j, r in enumerate(f["rows"]):
            for kk in range(4):
                d4.append(r["Y4"][kk] - r["X4"][kk])
                cl4.append(i * 100000 + j)
    d4 = np.array(d4, float)
    se4_naive = _se(d4)
    se4_crve, G4 = crve(d4, cl4)
    out["O1b_seats4"] = {
        "n_rows": int(d4.size), "delta": float(d4.mean()),
        "se_naive": se4_naive, "se_crve": se4_crve, "G": G4,
        "max_abs_sum_per_game": float(max(abs(np.sum(r["Y4"])) for f in fams for r in f["rows"])),
        "note": ("零和指标 ⇒ 每场四席和 ≡ 0 ⇒ 展开成 4N 行时 Δ̂ ≡ 0；按场聚类的 CRVE ≡ 0（残差簇和恒 0）"
                 "⇒ **既零信号又零 SE**：这是「四席/24 排列当独立样本」那条路的实测下场")}

    # ---- O2：CUPED（协变量 = 基线臂同山同席结果）----
    o2 = cuped(d, X, fam_idx, folds, "cross")
    vr = 1.0 - (o2["se"] / se0) ** 2
    drift = o2["delta"] - out["O0"]["delta"]
    out["O2"] = {"covariate": "base_same_wall_same_seat", "delta": o2["delta"], "se": o2["se"],
                 "ci": [o2["delta"] - Z * o2["se"], o2["delta"] + Z * o2["se"]],
                 "beta": o2["beta"], "betas_per_fold": o2["betas"], "folds": folds,
                 "var_reduction": vr, "se_drop": 1.0 - o2["se"] / se0,
                 "equiv_n": equiv_n(se0, o2["se"]), "mde": mde(o2["se"]),
                 "drift": drift, "drift_in_SE": drift / se0,
                 "cross_fitted": True}
    o2i = cuped(d, X, fam_idx, folds, "insample")
    out["O2_insample"] = {"se": o2i["se"], "beta": o2i["beta"],
                          "se_drop": 1.0 - o2i["se"] / se0, "delta": o2i["delta"],
                          "note": "β 用全样本（错误做法）—— 只用来量「交叉拟合防住了多少过拟合」"}
    # 多协变量（探索，不是判据）
    Zm = np.c_[X, np.array([r["Xs"] for f in fams for r in f["rows"]]),
               np.array([r["Xw"] for f in fams for r in f["rows"]]),
               np.array([r["Xd"] for f in fams for r in f["rows"]])]
    se_m = ols_cf(d, Zm, fam_idx, folds)["se"]
    out["O2_multi_exploratory"] = {
        "se": se_m, "se_drop": 1.0 - se_m / se0, "equiv_n": equiv_n(se0, se_m),
        "covariates": ["base_rank", "base_score", "base_wins", "base_deals"],
        "note": "4 个**合法**基线臂结果一起回归（探索）—— 证明「不是协变量选得不够」"}

    # ---- 负向对照：置换协变量（收益应 ≈0）----
    rng = np.random.default_rng(seed)
    vrs = []
    for _ in range(n_perm):
        zp = X.copy()
        rng.shuffle(zp)
        vrs.append(1.0 - (cuped(d, zp, fam_idx, folds, "cross")["se"] / se0) ** 2)
    vrs = np.array(vrs, float)
    out["O2_placebo_perm"] = {
        "n_perm": int(n_perm), "seed": int(seed),
        "var_reduction_mean": float(vrs.mean()), "sd": float(vrs.std(ddof=1)),
        "q025": float(np.quantile(vrs, 0.025)), "q975": float(np.quantile(vrs, 0.975)),
        "se_drop_median": float(1.0 - math.sqrt(1.0 - float(np.median(vrs)))),
        "note": "置换掉同山对应关系后收益应 ≈0（实测 |mean| 与 95% 分位见上）"}

    # ---- 作弊示范：把"被估量本身 / 候选臂自己的结果"当协变量 ----
    cheat = {}
    rc = cuped(d, d, fam_idx, folds, "cross")
    cheat["z_is_delta"] = {"se": rc["se"], "var_reduction": 1.0 - (rc["se"] / se0) ** 2,
                           "se_drop": 1.0 - rc["se"] / se0, "delta": rc["delta"],
                           "note": "z = 被估量本身（候选自己的结果−基线）⇒ SE→0、Δ 不变、t→∞："
                                   "**纯循环**，能把判据骗过去但读数是假的"}
    rcy = cuped(d, Y, fam_idx, folds, "cross")
    cheat["z_is_candidate_Y"] = {"se": rcy["se"], "var_reduction": 1.0 - (rcy["se"] / se0) ** 2,
                                 "se_drop": 1.0 - rcy["se"] / se0, "beta": rcy["beta"],
                                 "note": "z = 候选臂自己的结果（post-treatment，判决后才有）⇒ "
                                         "数值上与合法同山协变量**对称**，说明「收益大小」本身分不出合法/作弊，"
                                         "必须靠**可得时机**这条边界"}
    out["cheat"] = cheat

    # ---- 逐族读数 + 判据 ----
    out["families"] = [{"name": f["name"], "n": f["n"],
                        "delta": float(d[fam_idx == i].mean()),
                        "se": _se(d[fam_idx == i])} for i, f in enumerate(fams)]

    # ---- IQM（rliable 口径：只作「能不能用」的读数，⛔ 不是判决口径）----
    rngb = np.random.default_rng(ml_eval.BOOT_SEED)
    iqm_val = iqm(d)
    bo = np.array([iqm(d[rngb.integers(0, n, n)]) for _ in range(2000)])
    se_iqm = float(bo.std(ddof=1))
    out["iqm"] = {"iqm": iqm_val, "trim": 0.25, "n_boot": 2000, "se": se_iqm,
                  "se_ratio_to_O0": se_iqm / se0, "equiv_n": equiv_n(se0, se_iqm),
                  "delta_vs_mean": iqm_val - float(d.mean()),
                  "usable": False,
                  "why": ("① IQM 砍掉两端各 25% ⇒ 只剩一半样本，等效 n 反而变小；"
                          "② rliable 的分层 bootstrap 要 ≥5–10 个 run（我们只有 3 个牌山族 = 3 个 run），"
                          "逐族 CI 的覆盖不可证 ⇒ ⛔ 本判据下不用 IQM")}

    passed = (out["O2"]["se_drop"] >= CRIT_SE_DROP
              and abs(out["O2"]["drift_in_SE"]) <= CRIT_DRIFT_SE)
    out["criterion"] = {
        "pre_registered": {"se_drop_min": CRIT_SE_DROP, "equiv_n_min": 1.0 / (1 - CRIT_SE_DROP) ** 2,
                           "drift_max_in_SE": CRIT_DRIFT_SE},
        "se_drop": out["O2"]["se_drop"], "equiv_n": out["O2"]["equiv_n"],
        "drift_in_SE": out["O2"]["drift_in_SE"],
        "passed": bool(passed),
        "verdict": "集成候选（可加 --cuped，缺省 off）" if passed
                   else "不集成（判据未通过：SE 降幅 < 25%）"}
    return out


def render(res: dict) -> str:
    o0, o2 = res["O0"], res["O2"]
    L = [f"== eval-variance · metric={res['metric']} · n={res['n']} 对（跨 {len(res['families'])} 族）",
         f"   O0   Δ={o0['delta']:+.4f}  SE={o0['se']:.4f}  CI(z)=[{o0['ci_z'][0]:+.3f},{o0['ci_z'][1]:+.3f}]"
         f"  CI(boot)=[{o0['ci'][0]:+.3f},{o0['ci'][1]:+.3f}]  MDE={o0['mde']:.3f}",
         f"   O1   SE={res['O1']['se']:.4f}（簇=场, G={res['O1']['G']}, "
         f"比 O0={res['O1']['ratio_to_O0']:.9f}）等效 n {res['O1']['equiv_n']:.3f}×",
         f"       族当簇 G=3：SE={res['O1_family_cluster']['se']:.4f}"
         f"（比 {res['O1_family_cluster']['ratio_to_O0']:.3f}）—— ⛔ 不可用",
         f"   O1b  四席展开：Δ={res['O1b_seats4']['delta']:+.3e}"
         f"（max|Σ四席|={res['O1b_seats4']['max_abs_sum_per_game']:.2e}）"
         f" 朴素SE={res['O1b_seats4']['se_naive']:.4f} 按场CRVE={res['O1b_seats4']['se_crve']:.2e}",
         f"   O2   β={o2['beta']:+.4f}  SE={o2['se']:.4f}  CI=[{o2['ci'][0]:+.3f},{o2['ci'][1]:+.3f}]"
         f"  MDE={o2['mde']:.3f}",
         f"        VR={o2['var_reduction']*100:+.2f}%  SE↓={o2['se_drop']*100:+.2f}%  "
         f"等效 n {o2['equiv_n']:.3f}×  漂移 {o2['drift']:+.4f}（{o2['drift_in_SE']:+.3f} SE）",
         f"   多协变量（探索）SE↓={res['O2_multi_exploratory']['se_drop']*100:+.2f}%",
         f"   参考：未配对 SE={res['ref_unpaired']['se']:.4f} ⇒ 配对已拿到 "
         f"{res['ref_unpaired']['pairing_gain_equiv_n']:.3f}×；ρ(Y,X)={res['pairing']['rho_YX']:.4f}",
         f"   负向[置换X] VR mean={res['O2_placebo_perm']['var_reduction_mean']*100:+.3f}% "
         f"（95% [{res['O2_placebo_perm']['q025']*100:+.2f}%,{res['O2_placebo_perm']['q975']*100:+.2f}%]）",
         f"   IQM  值={res['iqm']['iqm']:+.4f}（比均值 {res['iqm']['delta_vs_mean']:+.4f}）"
         f"  SE={res['iqm']['se']:.4f}（{res['iqm']['se_ratio_to_O0']:.3f}×O0 ⇒ 等效 n "
         f"{res['iqm']['equiv_n']:.3f}×）—— ⛔ 不用（见 --json 的 why）",
         f"   作弊[z=Δ] SE={res['cheat']['z_is_delta']['se']:.3e} "
         f"SE↓={res['cheat']['z_is_delta']['se_drop']*100:.2f}% ；"
         f"[z=候选Y] SE↓={res['cheat']['z_is_candidate_Y']['se_drop']*100:.2f}%",
         f"   判据：SE↓ {res['criterion']['se_drop']*100:.2f}% vs ≥25% ∧ 漂移 "
         f"{abs(res['criterion']['drift_in_SE']):.3f} SE ≤1.0 ⇒ "
         f"{'**通过**' if res['criterion']['passed'] else '**不通过**'}（{res['criterion']['verdict']}）"]
    for f in res["families"]:
        L.append(f"   {f['name']}: n={f['n']}  Δ={f['delta']:+.4f}  SE={f['se']:.4f}")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# 自检（离线、零数据：造数 + 负向必红）
# ---------------------------------------------------------------------------
def selftest() -> int:
    rng = np.random.default_rng(20261020)
    n, nfam = 3000, 3
    fam_idx = np.repeat(np.arange(nfam), n // nfam)
    wall = rng.normal(0, 1, n)                       # 牌山运气（两臂共享）
    y = 40 * wall + rng.normal(0, 30, n) + 1.5
    x = 40 * wall + rng.normal(0, 30, n)
    d = y - x
    checks = []

    def ck(name, cond, detail=""):
        checks.append((name, bool(cond), detail))
        _say(f"  [{'PASS' if cond else 'FAIL'}] {name}：{detail}")

    se0 = _se(d)
    clusters = np.arange(n)
    se1, G = crve(d, clusters)
    ck("O1 == O0（每簇 1 行 ⇒ 按场聚类恒等）", abs(se1 / se0 - 1.0) < 1e-9, f"比 {se1/se0:.12f}")
    # 正向：z 与 d 强相关（同山协变量，d 的方差里有一块能被 z 解释）
    z = x
    r = cuped(d, z, fam_idx, DEFAULT_FOLDS, "cross")
    drop = 100 * (1 - r["se"] / se0)
    ck("O2 正向：同山协变量确实降 SE", r["se"] < se0 * 0.95,
       f"SE {r['se']:.4f} vs {se0:.4f}（SE↓ {drop:.2f}%）")
    ck("交叉拟合 vs 全样本：差异应很小（<0.5%）",
       abs(cuped(d, z, fam_idx, DEFAULT_FOLDS, "insample")["se"] / r["se"] - 1) < 5e-3,
       f"全样本 SE={cuped(d, z, fam_idx, DEFAULT_FOLDS, 'insample')['se']:.4f}")
    # 负向：置换 z ⇒ 收益 ≈0
    zp = z.copy(); rng.shuffle(zp)
    rr = 1 - (cuped(d, zp, fam_idx, DEFAULT_FOLDS, "cross")["se"] / se0) ** 2
    ck("负向：置换协变量 ⇒ VR≈0", abs(rr) < 0.02, f"VR={rr*100:+.3f}%")
    # 作弊：z = d ⇒ SE 被压到 O0 的零头（剩下的只是折均值差，不是信息）
    rc = cuped(d, d, fam_idx, DEFAULT_FOLDS, "cross")
    ck("作弊：z=Δ ⇒ SE 压到 O0 的 <5%（判据可被循环协变量骗）", rc["se"] < 0.05 * se0,
       f"SE={rc['se']:.3e}（= {100*rc['se']/se0:.2f}% of O0，SE↓ {100*(1-rc['se']/se0):.2f}%）")
    # fail-closed：协变量缺失/全常数
    try:
        cuped(d, np.zeros(n), fam_idx, DEFAULT_FOLDS, "cross")
        ok = False
        why = "没有报错"
    except SystemExit as e:
        ok = "fail-closed" in str(e)
        why = str(e)[:60]
    ck("fail-closed：协变量为常数 ⇒ ERROR（⛔ 不静默退回 O0）", ok, why)
    # 端到端：同一批合成数据走 analyse（含 O0 的既有实现）
    fams = []
    for i in range(nfam):
        m = fam_idx == i
        rows = [{"seed": 1000 + i * 1000 + j, "seat": 0, "Y": float(y[idx]), "X": float(x[idx]),
                 "Y4": [float(y[idx]), 0, 0, 0], "X4": [float(x[idx]), 0, 0, 0],
                 "Xw": 0.0, "Xd": 0.0, "Xs": 0.0, "hands": 10.0, "ryukyoku": 0.0}
                for j, idx in enumerate(np.flatnonzero(m))]
        fams.append({"name": f"f{i+1}", "n": len(rows), "rows": rows, "student": "net:a@0",
                     "cand": "net:b@0", "seed_base": i, "base_path": "-", "cand_path": "-"})
    res = analyse(fams, "rank_points", DEFAULT_FOLDS, n_perm=20, seed=7)
    ck("端到端：O0 走既有 eval、判据字段齐全",
       res["schema"] == SCHEMA and "passed" in res["criterion"] and res["n"] == n,
       f"n={res['n']} 判据={res['criterion']['passed']}")
    bad = [c for c in checks if not c[1]]
    _say(f"\n[eval-variance --selftest] {len(checks)} 项，失败 {len(bad)} 项"
         + ("" if not bad else "：" + "；".join(c[0] for c in bad)))
    return 0 if not bad else 3


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="评估侧方差缩减离线对账（O0/O1/O2；零采集零训练）")
    ap.add_argument("--family", action="append", default=[],
                    help="标签=<base 的 summary.json 或目录>;<cand 的 …>（可多次）")
    ap.add_argument("--preset", action="append", default=[], choices=sorted(PRESETS),
                    help="既有证据的命名预置（s35-pilot/s35-formal/s38-pilot/s38-formal）")
    ap.add_argument("--metric", default="rank_points", choices=list(ml_eval.METRICS))
    ap.add_argument("--folds", type=int, default=DEFAULT_FOLDS, help="交叉拟合折数（≥2）")
    ap.add_argument("--perm", type=int, default=200, help="置换协变量的次数（负向对照）")
    ap.add_argument("--seed", type=int, default=20261020)
    ap.add_argument("--json", default="", help="把完整读数写成 JSON")
    ap.add_argument("--expect-delta", type=float, default=None,
                    help="可选：断言 O0 的 Δ 与既有 verdict.json 一致（回读校验）")
    ap.add_argument("--expect-se", type=float, default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.folds < 2:
        _say("[eval-variance] ERROR：--folds 必须 ≥2（交叉拟合）")
        return 2
    specs = list(a.family)
    for nm in a.preset:
        rows = PRESETS[nm]
        miss = [str(p) for _f, p in rows if not Path(p).is_file()]
        if miss:
            _say(f"[eval-variance] ERROR：preset {nm} 缺文件（fail-closed）：{miss}")
            return 2
        fam = {}
        for f, p in rows:
            fam.setdefault(f, []).append(p)
        for f, ps in fam.items():
            b = next((p for p in ps if "base" in str(p)), None)
            c = next((p for p in ps if "cand" in str(p)), None)
            if not (b and c):
                _say(f"[eval-variance] ERROR：preset {nm} 的族 {f} 缺 base/cand")
                return 2
            specs.append(f"{nm}-{f}={b};{c}")
    if not specs:
        _say("[eval-variance] ERROR：至少给一个 --family 或 --preset")
        return 2
    fams = build_families(specs, a.metric)
    res = analyse(fams, a.metric, a.folds, a.perm, a.seed)
    res["families_spec"] = [{"name": f["name"], "n": f["n"], "seed_base": f["seed_base"],
                             "base": f["base_path"], "cand": f["cand_path"]} for f in fams]
    _say(render(res))
    if a.expect_delta is not None and abs(res["O0"]["delta"] - a.expect_delta) > 5e-3:
        _say(f"[eval-variance] FAIL：O0 Δ={res['O0']['delta']:+.4f} ≠ 既有 {a.expect_delta:+.4f}")
        return 2
    if a.expect_se is not None and abs(res["O0"]["se"] - a.expect_se) > 5e-3:
        _say(f"[eval-variance] FAIL：O0 SE={res['O0']['se']:.4f} ≠ 既有 {a.expect_se:.4f}")
        return 2
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        _say(f"\n[写出] {a.json}")
    return 0 if res["criterion"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
