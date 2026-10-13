# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""探针「同状态对比式优势 · 可学天花板」—— 拟合与判决（CPU 分钟级；口径 = PROBE-CONTRAST-PLAN.md §4/§5，跑前冻结）。

主判据 = **样本外 AUC**（按场 5 折 GroupKFold；同一场的配对行只进一折）+ 按场 cluster bootstrap 95%CI。
安慰剂三对照（同一学习器、同一折）：① 打乱标签（按场整场打乱 ×3 seed）② 非学生席对照 ③ 只用动作键。
另报：幻想增益上界 `E[max(0,Δ)]`（神谕）、交叉拟合可实现增益 `E[ŷ·Δ]`、`ρ(Δ, A0 自身该局得分)`（均值回归检查）。
⛔ 只做前向/拟合，不含任何训练、闸门、PPO。
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

K_FOLDS = 5
SEED = 20261012


# ---------------------------------------------------------------- 度量（不引 sklearn/scipy：本环境没有）

def auc_score(scores, labels) -> float:
    """Mann-Whitney AUC（并列取平均秩；与 sklearn 的口径同义）。"""
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.int64)
    n = scores.size
    order = np.argsort(scores, kind="mergesort")
    s = scores[order]
    ranks = np.empty(n, dtype=np.float64)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and s[j + 1] == s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    n1 = int(labels.sum())
    n0 = n - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((ranks[labels == 1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0))


def pearson(x, y) -> float:
    x = np.asarray(x, float); y = np.asarray(y, float)
    if x.std() == 0 or y.std() == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def spearman(x, y) -> float:
    def rank(v):
        v = np.asarray(v, float)
        o = np.argsort(v, kind="mergesort")
        r = np.empty(v.size, float)
        sv = v[o]
        i = 0
        while i < v.size:
            j = i
            while j + 1 < v.size and sv[j + 1] == sv[i]:
                j += 1
            r[o[i:j + 1]] = (i + j) / 2.0 + 1.0
            i = j + 1
        return r
    return pearson(rank(x), rank(y))


# ---------------------------------------------------------------- 学习器（小 MLP，plan §4）

class MLP(nn.Module):
    def __init__(self, d: int):
        super().__init__()
        self.f = nn.Sequential(
            nn.Linear(d, 64), nn.ReLU(), nn.Dropout(0.1),
            nn.Linear(64, 32), nn.ReLU(), nn.Dropout(0.1),
            nn.Linear(32, 1))

    def forward(self, x):
        return self.f(x).squeeze(-1)


def fit_fold(Xtr, ytr, Xva, yva, Xte, seed: int, epochs=30, patience=5, batch=256, lr=1e-3):
    """在 (Xtr,ytr) 上训、按 (Xva,yva) 的 AUC 早停，返回 `Xte` 的概率与最佳内层 AUC。"""
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    mu = Xtr.mean(0); sd = Xtr.std(0); sd[sd == 0] = 1.0
    tf = lambda A: torch.from_numpy(((A - mu) / sd).astype(np.float32))   # noqa: E731
    xtr, ytr_t = tf(Xtr), torch.from_numpy(ytr.astype(np.float32))
    xva, xte = tf(Xva), tf(Xte)
    m = MLP(Xtr.shape[1])
    opt = torch.optim.AdamW(m.parameters(), lr=lr, weight_decay=1e-4)
    lossf = nn.BCEWithLogitsLoss()
    best = (-1.0, None)
    bad = 0
    n = len(ytr)
    for _ in range(epochs):
        m.train()
        perm = rng.permutation(n)
        for b0 in range(0, n, batch):
            idx = perm[b0:b0 + batch]
            opt.zero_grad()
            lossf(m(xtr[idx]), ytr_t[idx]).backward()
            opt.step()
        m.eval()
        with torch.no_grad():
            pv = torch.sigmoid(m(xva)).numpy()
        a = auc_score(pv, yva)
        if a == a and a > best[0] + 1e-6:
            best = (a, copy.deepcopy(m.state_dict()))
            bad = 0
        else:
            bad += 1
            if bad >= patience:
                break
    if best[1] is not None:
        m.load_state_dict(best[1])
    m.eval()
    with torch.no_grad():
        return torch.sigmoid(m(xte)).numpy(), best[0]


def oos_predict(X, y, game, seed=SEED):
    """按场 5 折：外层 OOS 概率 + 每折内层早停 AUC。"""
    ug = np.unique(game)
    fold_of = {int(g): i % K_FOLDS for i, g in enumerate(ug)}
    row_fold = np.array([fold_of[int(g)] for g in game])
    p_oos = np.full(len(y), np.nan)
    inner = []
    for k in range(K_FOLDS):
        te = row_fold == k
        tr_games = [int(g) for g in ug if fold_of[int(g)] != k]
        val_games = set(tr_games[::4])
        itr_games = set(g for g in tr_games if g not in val_games)
        m_itr = np.isin(game, list(itr_games))
        m_val = np.isin(game, list(val_games))
        p, ia = fit_fold(X[m_itr], y[m_itr], X[m_val], y[m_val], X[te], seed + k)
        p_oos[te] = p
        inner.append(None if ia != ia else float(ia))
    return p_oos, inner


def metric_block(p, y, dy, y0, game, n_boot=1000, boot_seed=SEED):
    """一个（学习器输出的）预测 → 读数：AUC/准确率 + CI + 增益 + 相关性。"""
    ok = ~np.isnan(p)
    p, y, dy, y0 = p[ok], y[ok], dy[ok], y0[ok]
    auc = auc_score(p, y)
    yhat = (p > 0.5).astype(int)
    acc = float((yhat == y).mean())
    ug = np.unique(game[ok])
    idx_by_game = {int(g): np.where(game[ok] == g)[0] for g in ug}
    rng = np.random.default_rng(boot_seed)
    boot = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(ug), len(ug))
        idx = np.concatenate([idx_by_game[int(ug[i])] for i in pick])
        a = auc_score(p[idx], y[idx])
        if a == a:
            boot.append(a)
    lo, hi = (float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))) if boot else (float("nan"),) * 2
    return {
        "n": int(len(p)), "n_games": int(len(ug)), "auc": auc, "auc_ci95": [lo, hi],
        "acc": acc, "pos_share": float(y.mean()), "share_pred_pos": float(yhat.mean()),
        "gain_pred_pts_per_hand": float(np.mean(yhat * dy)),
        "gain_pred_minus_always_a1_pts_per_hand": float(np.mean(yhat * dy) - np.mean(dy)),
        "gain_pred_signform_pts_per_hand": float(np.mean((2 * yhat - 1) * dy)),
        "gain_always_a1_pts_per_hand": float(np.mean(dy)),
        "gain_oracle_pts_per_hand": float(np.mean(np.maximum(0.0, dy))),
        "gain_share_of_oracle": float(np.mean(yhat * dy) / max(1e-12, np.mean(np.maximum(0.0, dy)))),
        "rho_pearson_dy_y0": pearson(dy, y0), "rho_spearman_dy_y0": spearman(dy, y0),
    }


def shuffle_labels_by_game(y, game, seed):
    """按场**整场**打乱标签（保住场内相关性，只打断「状态 → 结果」的对应）。"""
    ug = np.unique(game)
    idx_by_game = {int(g): np.where(game == g)[0] for g in ug}
    perm = np.random.default_rng(seed).permutation(len(ug))
    out = np.empty_like(y)
    for i, g in enumerate(ug):
        out[idx_by_game[int(g)]] = y[idx_by_game[int(ug[perm[i]])]]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_probe-contrast-fit.py")
    ap.add_argument("--work", required=True)
    ap.add_argument("--out-json", default="")
    ap.add_argument("--out-log", default="")
    ap.add_argument("--n-boot", type=int, default=1000)
    a = ap.parse_args(argv)
    torch.set_num_threads(8)

    work = Path(a.work)
    z = np.load(work / "pairs.npz")
    S, A0, A1 = z["S"], z["A0"], z["A1"]
    dy, y0, game = z["dy"].astype(np.float64), z["y0"].astype(np.float64), z["game"]
    y = (dy > 0).astype(int)
    d0a, d1a = z["delta_a0"], z["delta_a1"]
    X = np.concatenate([S, A0, A1, A1 - A0], axis=1)
    X_act = np.concatenate([A0, A1, A1 - A0], axis=1)

    rep = {"plan": "PROBE-CONTRAST-PLAN.md", "n_rows": int(len(y)),
           "dim_state": int(S.shape[1]), "dim_action": int(A0.shape[1]),
           "dim_X_full": int(X.shape[1]), "dim_X_action_only": int(X_act.shape[1]),
           "label_pos_share": float(y.mean()), "kappa_folds": K_FOLDS, "seed": SEED}
    lines = [f"=== 探针 · 同状态对比式优势「可学天花板」 ===",
             f"配对行 {len(y)} · 场 {len(np.unique(game))} · 特征 state {S.shape[1]} + action {A0.shape[1]}×3 "
             f"= {X.shape[1]} · 标签 +1 占比 {y.mean():.4f}",
             f"Δ(净得失点/小局)：均值 {dy.mean():+.2f} · 神谕上界 E[max(0,Δ)] {np.mean(np.maximum(0, dy)):.2f} · "
             f"σ {dy.std():.2f}"]

    # ---- 主判据
    p_main, inner = oos_predict(X, y, game)
    m_main = metric_block(p_main, y, dy, y0, game, n_boot=a.n_boot)
    rep["main"] = m_main
    rep["main"]["inner_val_auc_per_fold"] = inner
    lines.append(f"[主] OOS AUC {m_main['auc']:.4f} CI95 [{m_main['auc_ci95'][0]:.4f}, {m_main['auc_ci95'][1]:.4f}]"
                 f" · 准确率 {m_main['acc']:.4f}（判正占比 {m_main['share_pred_pos']:.4f} / 标签 {m_main['pos_share']:.4f}）"
                 f" · 增益(交叉拟合) {m_main['gain_pred_pts_per_hand']:+.2f} 点/小局"
                 f"（比「总是选 A1」{m_main['gain_always_a1_pts_per_hand']:+.2f} 高 "
                 f"{m_main['gain_pred_minus_always_a1_pts_per_hand']:+.2f}）"
                 f" · 神谕上界 {m_main['gain_oracle_pts_per_hand']:.2f} · 占神谕 {m_main['gain_share_of_oracle']:.3f}"
                 f" · ρ(Δ,y0) P {m_main['rho_pearson_dy_y0']:+.3f} S {m_main['rho_spearman_dy_y0']:+.3f}")
    lines.append(f"    ⚠ 计划 §5 的式子写作 E[(2ŷ−1)Δ]（字面值 {m_main['gain_pred_signform_pts_per_hand']:+.2f}）——"
                 f" 该式把「正确选 A0」记成负收益，**不是**可实现增益；实现用 E[ŷ·Δ]（相对「总是 A0」），两者都照报。")

    # ---- 安慰剂 ① 打乱标签（按场整场打乱 ×3）
    rep["placebo_shuffle"] = []
    for si, s in enumerate((101, 202, 303)):
        ys = shuffle_labels_by_game(y, game, SEED + s)
        ps, _ = oos_predict(X, ys, game, seed=SEED + s)
        mm = metric_block(ps, ys, dy, y0, game, n_boot=a.n_boot, boot_seed=SEED + s)
        rep["placebo_shuffle"].append(mm)
        lines.append(f"[安慰剂①打乱标签 s={s}] AUC {mm['auc']:.4f} CI95 "
                     f"[{mm['auc_ci95'][0]:.4f}, {mm['auc_ci95'][1]:.4f}] · 准确率 {mm['acc']:.4f}"
                     f" · 增益 {mm['gain_pred_pts_per_hand']:+.2f}")

    # ---- 安慰剂 ② 非学生席对照（席 1 的 Δ 当标签，特征不变）
    dy_s1 = (d1a[:, 1] - d0a[:, 1]).astype(np.float64)
    y_s1 = (dy_s1 > 0).astype(int)
    keep = dy_s1 != 0
    p_s1, _ = oos_predict(X[keep], y_s1[keep], game[keep], seed=SEED + 7)
    m_s1 = metric_block(p_s1, y_s1[keep], dy_s1[keep], y0[keep], game[keep], n_boot=a.n_boot, boot_seed=SEED + 7)
    m_s1["note"] = "标签换成席 1 的 Δ（席 1 两臂逐字相同 ⇒ 配对结果差应无可学信号）"
    m_s1["n_dropped_dy_zero"] = int((~keep).sum())
    rep["placebo_nonstudent_seat"] = m_s1
    lines.append(f"[安慰剂②非学生席(席1) n={m_s1['n']}] AUC {m_s1['auc']:.4f} CI95 "
                 f"[{m_s1['auc_ci95'][0]:.4f}, {m_s1['auc_ci95'][1]:.4f}] · 准确率 {m_s1['acc']:.4f}")

    # ---- 安慰剂 ③ 只用动作键不含状态
    p_act, _ = oos_predict(X_act, y, game, seed=SEED + 11)
    m_act = metric_block(p_act, y, dy, y0, game, n_boot=a.n_boot, boot_seed=SEED + 11)
    rep["placebo_action_only"] = m_act
    lines.append(f"[安慰剂③只用动作键] AUC {m_act['auc']:.4f} CI95 "
                 f"[{m_act['auc_ci95'][0]:.4f}, {m_act['auc_ci95'][1]:.4f}] · 准确率 {m_act['acc']:.4f}")

    # ---- 判决（预注册 §5，⛔ 不改判据）
    lo, hi = m_main["auc_ci95"]
    ci_excl = lo > 0.5 or hi < 0.5
    gain_ok = m_main["gain_pred_pts_per_hand"] > 0 and m_main["gain_share_of_oracle"] > 0.2
    placebo_max = max([m["auc"] for m in rep["placebo_shuffle"]] + [m_s1["auc"], m_act["auc"]])
    if not ci_excl:
        verdict = "方向族封存"
        why = f"AUC CI95 [{lo:.4f},{hi:.4f}] 含 0.5"
    elif not gain_ok:
        verdict = "方向族封存"
        why = (f"AUC CI 排除 0.5，但交叉拟合增益 {m_main['gain_pred_pts_per_hand']:+.2f} 点/小局 ≤0 "
               f"或 ≤0.2×神谕（占 {m_main['gain_share_of_oracle']:.3f}）")
    else:
        verdict = "值得实现（方向族有可学的对比信号）"
        why = (f"AUC {m_main['auc']:.4f} CI95 [{lo:.4f},{hi:.4f}] 排除 0.5 且增益 "
               f"{m_main['gain_pred_pts_per_hand']:+.2f} 点/小局 > 0（占神谕 {m_main['gain_share_of_oracle']:.3f}）")
    rep["verdict"] = {"judgement": verdict, "why": why,
                      "auc_ci_excludes_half": bool(ci_excl),
                      "max_placebo_auc": float(placebo_max),
                      "auc_minus_max_placebo": float(m_main["auc"] - placebo_max)}
    lines.append(f"=== 判决：{verdict} —— {why}")
    lines.append(f"    主 AUC {m_main['auc']:.4f} − 最强安慰剂 {placebo_max:.4f} = "
                 f"{m_main['auc'] - placebo_max:+.4f}")
    lines.append("⚠ 条件于该 seed 族（1 个 seed）；⚠ 分岔点后两臂各走自己的网 ⇒ 对照含后继策略混淆。")

    text = "\n".join(lines) + "\n"
    print(text)
    if a.out_json:
        Path(a.out_json).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.out_log:
        Path(a.out_log).write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
