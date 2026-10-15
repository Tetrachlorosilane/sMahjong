# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 冻结件根由 --root 指定；输出由 --out-dir 指定；本文件不写工作区以外路径。
"""NEXTPHASE 便宜前置探针 —— **零训练 / 零采集**：目标侧候选能不能被学？（口径 = lab/nextphase/NEXTPHASE-PLAN.md）

问法与 BL-12（样本外 AUC，观测侧）**同族但换到目标侧**：
  · 表示**固定**为"决策那一刻合法可见的状态"（同一份冻结件可算、无隐藏量）；
  · 只换**因变量 y**（每个候选目标一个 y），读数 = **样本外 R²**（岭回归闭式解 + 按手 5 折 GroupKFold）。
  · 配对判据 = 同一次按场聚类的 bootstrap 上 `d_i = (y_i−ŷ_i)^(1)² − (y_i−ŷ_i)^(0)²` 的均值（配对 MSE/方差差）。
  · ⛔ 两条臂**只差因变量**（表示与折完全相同）⇒ 配对差就是"这个目标比那个目标多可学的量"。

与**合法天花板 0.0693** 的关系是**逐候选**报的 `r2_legit`：同一份合法合法特征对**该 y** 的线性 R²。
  · `r2_legit(y) > 0.0693` ⇒ 该目标**不在** `delta` 那条天花板之下（新目标有新上限）；
  · `r2_legit(y) ≤ 0.0693` ⇒ 该目标被同一条信息天花板压住 ⇒ 换它不会解开 0.069。

⛔ 本脚本不训练网络、不采集、不跑强度闸门；只读冻结件，缺文件/缺列一律报错退出（fail-closed）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
for _p in (_REPO / "python", _REPO / "tools" / "lab"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

_spec = importlib.util.spec_from_file_location("_probe_contrast", _REPO / "tools" / "lab" / "_probe-contrast-fit.py")
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)
_spec2 = importlib.util.spec_from_file_location("_bl12", _REPO / "tools" / "lab" / "bl12-probe.py")
bl12 = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(bl12)

AUC = pc.auc_score
K_FOLDS = 5
SEED = 20261014
#: 预注册 MDE（绝对 R² 增益）—— 口径与 BL-12 的 0.020 同源（"小于它视为本 N 下无意义"）
MDE_DECLARED = 0.0020
LEGIT_CEILING = 0.0693          # docs/TRAINING-V4.md §14.8.1
HORIZON = 8


def need(root: Path, split: str, name: str) -> np.ndarray:
    p = root / f"{split}.{name}.npy"
    if not p.exists():
        raise SystemExit(f"[fail-closed] 缺文件：{p}")
    return np.asarray(np.load(p, mmap_mode="r"))


class T:
    def __init__(self, root: Path, split: str, rows: int = 0):
        tile0 = need(root, split, "tile")
        n = tile0.shape[0]
        self.n = n if rows <= 0 else min(n, rows)
        cut = slice(0, self.n)
        self.tile = tile0[cut]
        self.evt = need(root, split, "evt")[cut]
        self.ctx = need(root, split, "ctx")[cut]
        self.delta = need(root, split, "delta")[cut].astype(np.float64)
        self.game = need(root, split, "game")[cut].astype(np.int64)
        self.hand = need(root, split, "hand_no")[cut].astype(np.int64)
        self.seat = need(root, split, "seat")[cut].astype(np.int64)
        self.win = need(root, split, "aux_win_flag")[cut].astype(np.float64)
        self.placement = need(root, split, "placement")[cut].astype(np.float64)
        self.rank_points = need(root, split, "rank_points")[cut].astype(np.float64)
        self.dealin = need(root, split, "aux_opp_dealin")[cut].astype(np.float64)
        self.shanten = need(root, split, "aux_own_shanten_after")[cut].astype(np.float64)
        self.value = need(root, split, "value")[cut].astype(np.float64)
        self.key = self.game * 1000 + self.hand


def hand_group_mean(v: np.ndarray, key: np.ndarray) -> np.ndarray:
    """每行的"本手/本场聚合"值（把行级 y 折成手级量）—— 只用**公开可算的键**（game/hand_no）。"""
    uk, inv = np.unique(key, return_inverse=True)
    s = np.bincount(inv, weights=v, minlength=len(uk))
    c = np.bincount(inv, minlength=len(uk)).astype(np.float64)
    return (s / np.maximum(c, 1))[inv]


def hand_winner_delta(t: T) -> np.ndarray:
    """本小局"自家和了的那一家的收支"（手内常数）；未和局 = 该手最大值（≈0 或某家收入）。"""
    n = t.n
    d = np.full(n, np.nan)
    uk, inv = np.unique(t.key, return_inverse=True)
    for j in range(len(uk)):
        m = inv == j
        w = t.win[m] > 0.5
        d[m] = float(t.delta[m][w].max()) if w.any() else 0.0
    return d


def build_features(t: T) -> tuple[np.ndarray, list[str]]:
    """**合法可见状态**的固定线性表示（同一份冻结件可算；⛔ 不含隐藏量、不含因变量）。

    ① `tile` 直方图 34 维（自家手牌）② `ctx` 64 维（公开状态）③ `evt` 事件直方图聚合 96 维
    ④ 手内进度（决策序占比）。全部是"那一刻可见"的量。
    """
    B = t.n
    tile = t.tile.astype(np.float32)
    hist = tile.reshape(B, 34, 48).sum(2)                      # 逐张牌的计数强度
    evt = t.evt.astype(np.float32)
    ev = evt.sum(1)                                            # [B,96] 事件通道聚合
    ctx = t.ctx.astype(np.float32)
    uk, inv, cnt = np.unique(t.key, return_inverse=True, return_counts=True)
    ranks = np.zeros(B, dtype=np.float32)
    for j in range(len(uk)):
        m = np.where(inv == j)[0]
        ranks[m] = np.arange(len(m), dtype=np.float32) / max(len(m) - 1, 1)
    X = np.concatenate([hist, ctx, ev, ranks[:, None]], 1).astype(np.float64)
    names = [f"tile{i}" for i in range(34)] + [f"ctx{i}" for i in range(64)] + \
            [f"evt{i}" for i in range(96)] + ["hand_rank"]
    return X, names


def legit_ceiling_features(root: Path, split: str, n: int) -> np.ndarray:
    """与 `v4 value-audit` 的 `CEIL_GROUPS['legit_full(全部合法)']` **同组成**的线性参照矩阵。

    = `ctx` + **逐候选派生量的合法聚合**（`cand` 派生段按合法掩码 mean/max）
      + `effect[label]` + `aux_own_shanten_after` + `aux_own_tenpai`。⛔ 不含任何 `label_*`
      （结局 / 别家隐藏真值）—— 那两类是**作弊上界**（`docs/TRAINING-V4.md` §14.8.1）。

    ⚠ 不直接调 `value_audit._cols_of`（它会 `load_split` 整个紧凑集、含 `raw` 侧路径）；这里按
    同一切片口径从 `.npy` 现取，列组成逐项对齐。
    """
    ctx = np.asarray(need(root, split, "ctx")[:n], dtype=np.float64)
    cand = np.asarray(need(root, split, "cand")[:n], dtype=np.float64)
    nleg = np.asarray(need(root, split, "nlegal")[:n], dtype=np.int64)
    lab = np.asarray(need(root, split, "label")[:n], dtype=np.int64)
    effect = np.asarray(need(root, split, "effect")[:n], dtype=np.float64)
    sh = np.asarray(need(root, split, "aux_own_shanten_after")[:n], dtype=np.float64)
    tp = np.asarray(need(root, split, "aux_own_tenpai")[:n], dtype=np.float64)
    der = cand[:, :, 88:99]                       # `spec.block_slices()['cand.derived']` 的 11 维
    m = np.arange(der.shape[1])[None, :] < nleg[:, None]
    w = m[:, :, None].astype(np.float64)
    mean = (der * w).sum(1) / np.maximum(w.sum(1), 1.0)
    mx = np.where(m[:, :, None], der, -np.inf).max(1)
    mx = np.where(np.isfinite(mx), mx, 0.0)
    eff = effect[np.arange(len(lab)), np.clip(lab, 0, effect.shape[1] - 1)]
    return np.concatenate([ctx, mean, mx, eff.reshape(len(lab), -1), sh[:, None], tp[:, None]], 1)


def oof_pred(X, y, key, lam):
    """按**手** 5 折 GroupKFold 的样本外预测（同一手只进一折）。

    标准化参数与截距**只从训练折**取（⛔ 不许用全量统计量 —— 那是泄漏），折内闭式岭回归。
    """
    ug = np.unique(key)
    fold_of = {int(g): i % K_FOLDS for i, g in enumerate(ug)}
    row_fold = np.array([fold_of[int(g)] for g in key])
    p = np.full(len(y), np.nan)
    d = X.shape[1]
    for k in range(K_FOLDS):
        te = row_fold == k
        tr = ~te
        if tr.sum() < 200 or te.sum() < 100:
            raise SystemExit(f"[fail-closed] 折 {k} 样本不足 tr={tr.sum()} te={te.sum()}")
        mu = X[tr].mean(0)
        sd = X[tr].std(0)
        sd[sd == 0] = 1.0
        ym = float(y[tr].mean())
        Z = (X[tr] - mu) / sd
        w = np.linalg.solve(Z.T @ Z + lam * np.eye(d), Z.T @ (y[tr] - ym))
        p[te] = ((X[te] - mu) / sd) @ w + ym
    if not np.isfinite(p).all():
        raise SystemExit("[fail-closed] 样本外预测里有非有限值")
    return p


def r2(y, p) -> float:
    """样本外 R² = 1 − SSE/SS_总（总平方和用**同一批行**的均值 ⇒ 常数因变量时分母为 0）。"""
    ss = float(((y - y.mean()) ** 2).sum())
    return float(1.0 - ((y - p) ** 2).sum() / ss) if ss > 0 else float("nan")


def r2_on(idx: np.ndarray, y: np.ndarray, p: np.ndarray) -> float:
    yy, pp = y[idx], p[idx]
    ss = float(((yy - yy.mean()) ** 2).sum())
    return float(1.0 - ((yy - pp) ** 2).sum() / ss) if ss > 0 else float("nan")


def hand_scale(t: "T") -> np.ndarray:
    """**只看过去**的尺度估计（`E3` 的归一化分母）：逐场内、逐手推进的"本场历史 E|delta|"。

    ⚠ 必须**只用手号更小的行**（⛔ 不许用本手自己的 |delta| —— 那会把因变量的噪声除回去，
    变成"用 y 归一化 y"的假信号）。同一个 `(game, hand)` 的行拿**同一份**尺度（手内常数）。
    """
    out = np.ones(t.n, dtype=np.float64)
    for g in np.unique(t.game):
        gm = np.where(t.game == g)[0]
        hands = t.hand[gm]
        for hh in np.unique(hands):
            past = gm[hands < hh]
            s = float(np.abs(t.delta[past] / 1000.0).mean()) if past.size else 0.0
            if not (s > 0):
                s = 1.0
            out[gm[hands == hh]] = s
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"E:\mahjong-training\compact\ov-A-s00")
    ap.add_argument("--split", default="val")
    ap.add_argument("--rows", type=int, default=0)
    ap.add_argument("--lam", type=float, default=1.0)
    ap.add_argument("--n-boot", type=int, default=400)
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--tag", default="np1")
    ap.add_argument("--only", default="")
    ap.add_argument("--no-ceiling", action="store_true", help="跳过「合法列」逐目标天花板（省时间）")
    a = ap.parse_args(argv)

    root = Path(a.root)
    t = T(root, a.split, a.rows)
    X, names = build_features(t)
    print(f"[data] {root} · {a.split} · n={t.n} · 场 {len(np.unique(t.game))} · 手 {len(np.unique(t.key))} · "
          f"特征 {X.shape[1]}")

    # ---------------- 因变量：目标侧候选（每个 y 只换因变量）
    d_rows = t.delta / 1000.0                                   # 现状：逐行小局收支（千点）
    scale = hand_scale(t)                                       # 只看过去的尺度（E3 分母）
    y = {
        # --- 参考（现有口径：值头目标 = `--value-target delta`）
        "REF_delta": d_rows,
        # --- E1 自家手牌价值类（只从"手里的公开事实"推本小局自家收支的**可学份额**）
        "E1_own_win": t.win,                                    # 自家和了（打点类目标的"二值核"）
        "E1_own_win_delta": np.where(t.win > 0.5, d_rows, 0.0),  # 和了=自家收支，未和=0
        "E1_hand_agg": hand_group_mean(d_rows, t.key),          # 手级聚合（同手同值 ⇒ 噪声↓）
        "E1_winner_delta": hand_winner_delta(t) / 1000.0,       # 本手"和了那家"的收支（打点代理）
        # --- E3 奖励/优势口径的可学化（换尺度，**不换信息**）
        "E3_hand_norm": d_rows / scale,
        # --- E3 的**信息恒等式对照**（`ctx.points` 已经把"现在多少点"写死在输入里）
        "E3_value_final": t.value / 100.0,                       # 整场终值 − 起点（千点/100）
        "E3_rank_pts": t.rank_points / 100.0,
        # --- 辅助头（已在算、但按 HEAD_SPECS 不进决策的那些头）的目标
        "E2_shanten": t.shanten / 8.0,                           # `effect` 头的同类量
        "E2_dealin_sum": t.dealin.sum(1),                        # `danger` 头的同量纲
        "E2_placement": t.placement,                             # `placement` 头
        # --- 阴性对照（按预期出局）
        "NEG_opp_delta_shift": np.roll(t.delta, 1) / 1000.0,      # 前一行（别的手/别家）的收支
        "NEG_neg_delta": -d_rows,                                 # y 取负 ⇒ R² 恒同（配对 ΔR² 必 = 0 的管道对照）
        "NEG_constant_zero": np.zeros(t.n),                       # 常数因变量 ⇒ R² 无定义 ⇒ 必须 fail-closed
    }
    if a.only:
        keep = {c.strip() for c in a.only.split(",") if c.strip()}
        unk = keep - set(y)
        if unk:
            raise SystemExit(f"[fail-closed] 未知目标 {sorted(unk)}")
        y = {k: v for k, v in y.items() if k in keep}

    # ---------------- 先算 base（REF_delta）的样本外预测
    base_name = "REF_delta" if "REF_delta" in y else next(iter(y))
    yb = y[base_name]
    pb = oof_pred(X, yb, t.key, a.lam)
    r2b = r2(yb, pb)
    print(f"[base] {base_name}: 样本外 R² {r2b:.5f}（对照：合法天花板 delta = {LEGIT_CEILING}）")

    ug = np.unique(t.game)
    idx_by_game = [np.where(t.game == g)[0] for g in ug]

    def paired_r2_diff(p_c, y_c, n_boot, seed):
        """按场聚类的**配对** bootstrap：同一次重采样上 `R²(cand) − R²(base)`（方差归一 ⇒ 可跨目标比）。"""
        rng = np.random.default_rng(seed)
        out = []
        for _ in range(n_boot):
            pick = rng.integers(0, len(ug), len(ug))
            ii = np.concatenate([idx_by_game[int(i)] for i in pick])
            rc, rb = r2_on(ii, y_c, p_c), r2_on(ii, yb, pb)
            if rc == rc and rb == rb:
                out.append(rc - rb)
        return np.array(out)

    rep = {"tag": a.tag, "root": str(root), "split": a.split, "n": int(t.n),
           "n_features": int(X.shape[1]), "n_games": int(len(ug)), "n_hands": int(len(np.unique(t.key))),
           "lam": a.lam, "k_folds": K_FOLDS, "seed": SEED, "mde_declared": MDE_DECLARED,
           "legit_ceiling": LEGIT_CEILING, "base": {"name": base_name, "r2": r2b},
           "targets": {}, "negatives": {}}

    for name, yv in y.items():
        if name == base_name:
            continue
        p = oof_pred(X, yv, t.key, a.lam)
        r = r2(yv, p)
        d = paired_r2_diff(p, yv, a.n_boot, SEED + 11)
        bucket = "negatives" if name.startswith("NEG_") else "targets"
        if len(d) < 10:
            # **管道对照**（常数因变量）：R² 分母为 0 ⇒ 配对统计量不存在。⛔ 必须**显式记账**成
            # "无定义/出局"，⛔ 不许当 0 参与判决、也⛔ 不许让整轮静默死掉（fail-closed 但可读）。
            rep[bucket][name] = {"r2": r, "r2_gain_vs_base": None, "paired_dr2_mean": None,
                                 "paired_ci95": [None, None], "n_boot_eff": int(len(d)),
                                 "y_std": float(yv.std()), "verdict": "出局（无定义 · 管道对照）",
                                 "why": "常数因变量 ⇒ R² 与配对 ΔR² 均无定义（分母 0）"}
            print(f"[{name:22s}] 样本外 R² {r:+.5f} · std(y) {yv.std():.4f} · "
                  f"⛔ R² 无定义（常数因变量）⇒ 管道对照，判**出局**")
            continue
        se = float(d.std(ddof=1))
        mde_paired = 2.802 * se
        lo, hi = float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))
        entry = {"r2": r, "r2_gain_vs_base": r - r2b,
                 "paired_dr2_mean": float(d.mean()), "paired_ci95": [lo, hi],
                 "se_paired": se, "mde_paired": mde_paired,
                 "n_boot_eff": int(len(d)), "n_eff": int(t.n), "y_std": float(yv.std()),
                 "y_over_ceiling": bool(r > LEGIT_CEILING), "y_mean": float(yv.mean())}
        rep[bucket][name] = entry
        print(f"[{name:22s}] 样本外 R² {r:+.5f} · ΔR²(vs base) {r - r2b:+.5f} · "
              f"配对 ΔR² {d.mean():+.5f} CI[{lo:+.5f},{hi:+.5f}] · MDE_paired {mde_paired:.5f} · "
              f"std(y) {yv.std():.4f} · {'> 0.0693 天花板' if r > LEGIT_CEILING else '≤ 0.0693'}")

    # ---------------- 安慰剂：打乱标签（同一表示、同一折）
    rng = np.random.default_rng(SEED + 7)
    y_sh = yb.copy()
    for kk in np.unique(t.key):
        m = np.where(t.key == kk)[0]
        y_sh[m] = yb[m][rng.permutation(len(m))]
    p_sh = oof_pred(X, y_sh, t.key, a.lam)
    rep["placebo_shuffle"] = {"r2": r2(y_sh, p_sh), "y_std": float(y_sh.std())}

    # ---------------- 只用动作键（`tile`/`evt` 置 0、`ctx` 只留 round+ask）—— 与 BL-12 同一开关
    Xk = X.copy()
    Xk[:, :34] = 0.0
    Xk[:, 98:194] = 0.0
    Xk[:, 34 + 48:98] = 0.0
    p_k = oof_pred(Xk, yb, t.key, a.lam)
    rep["placebo_action_key"] = {"r2": r2(yb, p_k)}

    # ---------------- 「合法列」口径的逐目标天花板（与 `value-audit` 的 `legit_full` 同组成）
    #   这是**与 docs/TRAINING-V4.md §14.8.1 的 0.0693 同一把尺子**的读数：先把 `delta` 在**同一
    #   数据集**上重算一遍当标定（对不上就如实报，并以后者为准），再看每个候选目标各是多少。
    rep["ceiling"] = {"reference_doc": LEGIT_CEILING, "per_target": {}}
    if not a.no_ceiling:
        XL = legit_ceiling_features(root, a.split, t.n)
        print(f"[合法列] legit_full 组成宽度 {XL.shape[1]}（ctx + cand_agg + effect[label] + 自家向听/听牌）")
        for name, yv in y.items():
            pl = oof_pred(XL, yv, t.key, 1e-3)
            rl = r2(yv, pl)
            rep["ceiling"]["per_target"][name] = float(rl)
            print(f"   [合法列 R²] {name:22s} {rl:+.5f}  "
                  f"{'⚠ 高于 0.0693 ⇒ 不受现状天花板约束' if rl > LEGIT_CEILING else '≤ 0.0693 ⇒ 同一把尺子压住'}")
        dl = rep["ceiling"]["per_target"].get(base_name)
        rep["ceiling"]["calibration_delta"] = dl
        rep["ceiling"]["calibration_ok"] = bool(dl is not None and abs(dl - LEGIT_CEILING) <= 0.02)
        print(f"[标定] 本数据集上 `{base_name}` 的合法列 R² = {dl:+.5f} vs 文档 0.0693 "
              f"⇒ {'对得上' if rep['ceiling']['calibration_ok'] else '⚠ 对不上（判决以本数据集实测为准，文档值只作参照）'}")

    # 恒等式嫌疑名单（**只是标注**，判决不靠它）：这些目标的因变量本身含"已滚入的点数"或与
    # `effect`/向听同源 ⇒ 高 R² 主要是**恒等式**，不是"更会读局面"（对照 §14.8.1 的 `legit_ctx` 0.4457）。
    IDENTITY_SUSPECT = {
        "E3_value_final": "`value = final_scores[seat] − 起点`，而 `ctx` 里就有**当前点数** ⇒ 恒等式",
        "E3_rank_pts": "`rank_points` 由终局点数决定，`ctx` 里就有当前点数 ⇒ 主要是恒等式",
        "E2_placement": "顺位由终局点数决定，`ctx` 里就有当前点数 ⇒ 主要是恒等式",
        "E2_shanten": "`afterDiscard` 向听与 `effect`/`cand.derived` **同源** ⇒ 合法列 R²=1.0 是构造性的",
    }
    base_ceiling = rep["ceiling"]["per_target"].get(base_name)
    for bucket in ("targets", "negatives"):
        for name, e in rep[bucket].items():
            if e.get("paired_dr2_mean") is None:
                continue                                   # 管道对照已自己判过（无定义 ⇒ 出局）
            learnable = (e["paired_ci95"][0] > 0 and e["r2_gain_vs_base"] >= e["mde_paired"]
                         and e["r2_gain_vs_base"] >= MDE_DECLARED and e["r2"] > 0.0)
            rl = rep["ceiling"]["per_target"].get(name)
            e["r2_legit_full"] = rl
            e["learnable_on_this_representation"] = bool(learnable)
            # **主判据**（目标侧）：换这个 y 之后**合法信息上限**是否升过现状那条 0.0693±标定
            #   —— 目标侧候选的全部意义就在这里（现状的合法上限已被值头吃到 80%）。
            ref = float(base_ceiling if base_ceiling is not None else LEGIT_CEILING)
            e["ceiling_up"] = None if rl is None else bool(rl > ref + 0.005)
            e["identity_suspect"] = IDENTITY_SUSPECT.get(name)
            if rl is None:
                e["verdict"] = "未测（--no-ceiling）"
            elif not learnable:
                e["verdict"] = "出局" if e["paired_ci95"][0] <= 0 else "in 弱"
            elif e["ceiling_up"]:
                e["verdict"] = "**可用**（合法上限升 ×%.2f）" % (rl / ref if ref else float("nan"))
            else:
                e["verdict"] = "出局（本表示上可学，但合法上限不升）"
            e["ceiling_relation"] = (
                "未测（--no-ceiling）" if rl is None else
                (f"⛔ 被同一条信息天花板压住（合法列 R²={rl:+.5f} ≤ 现状 {ref:+.5f}）⇒ 换它不解锁 0.069"
                 if not e["ceiling_up"] else
                 f"✅ 合法列 R²={rl:+.5f} > 现状 {ref:+.5f}（×{rl / ref:.2f}）⇒ 该目标**不受**现状天花板约束"))
            if e["identity_suspect"]:
                e["ceiling_relation"] += f"；⚠ 恒等式嫌疑：{e['identity_suspect']}"
    print(f"[安慰剂] 打乱标签 R² {rep['placebo_shuffle']['r2']:+.5f} · "
          f"只用动作键 R² {rep['placebo_action_key']['r2']:+.5f}")
    if a.out_dir:
        od = Path(a.out_dir)
        od.mkdir(parents=True, exist_ok=True)
        # 资源台账（判据 ③：峰值口径 = **已提交私有字节**，ws 另报）。⚠ 这里量的是**本进程自己**，
        # 因为调用方常常只能看到 venv 蹦床（47 KB，假读数）⇒ 自报比外部采样更可信。
        try:
            import ctypes
            import ctypes.wintypes as _wt

            class _PMC(ctypes.Structure):
                _fields_ = [("cb", _wt.DWORD), ("PageFaultCount", _wt.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]
            _h = ctypes.windll.kernel32.OpenProcess(0x1000, False, __import__("os").getpid())
            _m = _PMC()
            _m.cb = ctypes.sizeof(_PMC)
            if _h and ctypes.windll.psapi.GetProcessMemoryInfo(_h, ctypes.byref(_m), _m.cb):
                rep["self_mem"] = {"peak_committed_mb": round(_m.PeakPagefileUsage / 1048576, 1),
                                   "peak_ws_mb": round(_m.PeakWorkingSetSize / 1048576, 1),
                                   "note": "本进程自报（⛔ 不是蹦床那条路的读数）"}
                ctypes.windll.kernel32.CloseHandle(_h)
        except Exception as e:                                   # noqa: BLE001
            rep["self_mem"] = {"error": f"{type(e).__name__}: {e}"}
        (od / f"nextphase-probe-{a.tag}.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[out] {od / f'nextphase-probe-{a.tag}.json'}")
        if rep.get("self_mem"):
            print(f"[self-mem] {json.dumps(rep['self_mem'], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
