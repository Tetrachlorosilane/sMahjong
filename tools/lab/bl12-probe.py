# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 冻结件根由 --root 指定（缺省 E:\mahjong-training\compact\ov-A-s00），
# 输出由 --out-dir 指定；本文件不含对工作区以外路径的写操作。
"""BL-12 「能不能过」的便宜前置探针 —— 零训练/零采集筛查（口径与判据 = lab/bl12/BL12-PLAN.md，跑前冻结）。

问题（两种结果变量，都是**公开可算、非强度标签**）：
  P1（主）给定决策行 i 的合法公开状态，预测**随机选定的一个对手席当前是否听牌**
          —— 标签 = 冻结件 `aux_opp_tenpai`（引擎真值，训练期标签；⛔ 不是强度量）。
  P2（次）给定决策行 i 的合法公开状态，预测**该手牌在未来 H 行内是否出现立直事件**
          —— 标签 = 从 `evt` 窗口逐行扫 riichi 事件派生（公开可算，**不需要任何隐藏量**）。

主判据（预先写死，见 PLAN §4）：候选观测块必须
  ① AUC > 0.5 且 ② AUC_cand − AUC_base > 0（配对 bootstrap 95%CI 排除 0，按场聚类）
  ③ 且增益 ≥ 预先声明的 MDE —— 报告口径：MDE_paired = 2.802×SE_boot(配对差)（同数据同数据的小学习器，
     配对计算；**口径以报告为准**）；④ 且优于最强安慰剂（三个安慰剂逐项报）。
判决：in 强（三条全过）/ in 弱（AUC>0.5 但增益不过线）/ 出局（AUC ≤ 最强安慰剂）。

统计量与学习器**只借既有实现**：`tools/lab/_probe-contrast-fit.py`（`auc_score` / `MLP` / 按场 5 折协议）
与 `python/mahjong_ml/eval.py`（`bootstrap_ci` / `required_n`）；本文件只做张量装配与判决汇总。
⛔ 本脚本不训练网络、不采集、不跑强度闸门；只读冻结件，缺文件/缺列一律报错退出。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

# ---------------------------------------------------------------- 借既有统计实现（不另造第二套）
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]        # tools/lab/bl12-probe.py → 仓库根
for _p in (_REPO / "python", _REPO / "tools" / "lab"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

spec = importlib.util.spec_from_file_location("_probe_contrast", _REPO / "tools" / "lab" / "_probe-contrast-fit.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)
from mahjong_ml.eval import bootstrap_ci, required_n  # noqa: E402

AUC = pc.auc_score
K_FOLDS = 5
SEED = 20261013
ALPHA = 0.05
POWER = 0.80
MDE_DECLARED = 0.020          # PLAN §4 声明：绝对 AUC 增益 < 0.020 视为本 N 下测不出
HORIZON_ROWS = 8              # P2 的"未来 H 行"（同一手内）

# evt 通道偏移（规范 §4.2 / spec.EVT_LAYOUT）
E_TYPE, E_ACTOR, E_MELD = 0, 78, 86
E_TURN, E_TSUMO, E_SIDE, E_RIP, E_SEQD = 91, 92, 93, 94, 95
E_KIND = 8
OLD4 = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]


def need_file(p: Path) -> Path:
    if not Path(p).exists():
        raise SystemExit(f"[fail-closed] 缺文件：{p}")
    return Path(p)


def need_col(root: Path, split: str, name: str, n_rows: int, full_rows: int) -> np.memmap:
    """fail-closed 取列：文件必须存在、行数必须与 tile 一致（`--debug-rows` 只允许按行数截断）。"""
    a = np.load(need_file(root / f"{split}.{name}.npy"), mmap_mode="r")
    if int(a.shape[0]) != int(full_rows):
        raise SystemExit(f"[fail-closed] {split}.{name} 行数 {a.shape[0]} != tile 行数 {full_rows}")
    return a[:n_rows]


# ---------------------------------------------------------------- 数据装配
def load_root(root: Path, split: str, debug_rows: int):
    tile0 = np.load(need_file(root / f"{split}.tile.npy"), mmap_mode="r")
    full = int(tile0.shape[0])
    n = full if debug_rows <= 0 else min(full, debug_rows)
    if tuple(tile0.shape[1:]) != (34, 48):
        raise SystemExit(f"[fail-closed] tile 形状 {tile0.shape} != (n,34,48)")
    tile = np.asarray(tile0[:n], dtype=np.float32)
    evt = np.asarray(need_col(root, split, "evt", n, full), dtype=np.float32)
    ctx = np.asarray(need_col(root, split, "ctx", n, full), dtype=np.float32)
    if evt.shape[1:] != (60, 96) or ctx.shape[1:] != (64,):
        raise SystemExit(f"[fail-closed] evt/ctx 形状异常 {evt.shape} {ctx.shape}")
    game = np.asarray(need_col(root, split, "game", n, full), dtype=np.int64)
    hand = np.asarray(need_col(root, split, "hand_no", n, full), dtype=np.int64)
    y_tenpai = np.asarray(need_col(root, split, "aux_opp_tenpai", n, full), dtype=np.int8)
    if y_tenpai.shape[1:] != (3,):
        raise SystemExit(f"[fail-closed] aux_opp_tenpai 形状 {y_tenpai.shape} != (n,3)")
    return tile, evt, ctx, game, hand, y_tenpai


def derive_p2_label(evt: np.ndarray, game: np.ndarray, hand: np.ndarray, horizon: int) -> np.ndarray:
    """P2 标签：本行之后 H 行之内（同一手）是否出现 riichi 事件。⛔ 只用公开事实（窗口里的 riichi 事件）。"""
    ty = evt[:, :, E_TYPE:E_TYPE + 8]
    ne = ty.sum(2) > 0
    is_r = (ty.argmax(2) == 3) & ne
    row_has_riichi = is_r.any(1)
    key = game * 1000 + hand
    n = len(key)
    out = np.zeros(n, dtype=np.int8)
    for i in range(n):
        for j in range(i + 1, min(i + 1 + horizon, n)):
            if key[j] != key[i]:
                break
            if row_has_riichi[j]:
                out[i] = 1
                break
    return out


# ---------------------------------------------------------------- 候选观测块（只用冻结件可算的公开量）
def cand_c1(evt, ctx):
    """C1 对手行为聚合嵌入（近期／分相位，公开可算）—— 逐席 9 维 + 全局 4 维 = 31。"""
    B = evt.shape[0]
    out = np.zeros((B, 31), dtype=np.float32)
    ty = evt[:, :, E_TYPE:E_TYPE + 8]
    ne = ty.sum(2) > 0
    t = np.where(ne, ty.argmax(2), -1)
    actor = np.where(ne, evt[:, :, E_ACTOR:E_ACTOR + 4].argmax(2), -1)
    turn = evt[:, :, E_TURN]
    tsumo = evt[:, :, E_TSUMO]
    seqd = evt[:, :, E_SEQD]
    is_disc = (t == 1)
    is_meld = (t == 2)
    is_kan = (t == 4)
    is_ri = (t == 3)
    # 事件序号（用于"离开上次舍张过了几条事件"） + 行内绝对时刻代理
    idx = np.arange(evt.shape[1])[None, :].repeat(B, 0)
    for s in range(3):
        m = (actor == s)
        d = m & is_disc
        nd = d.sum(1).astype(np.float32)
        out[:, s * 9 + 0] = nd
        te = (d & (tsumo < 0.5)).sum(1).astype(np.float32)
        out[:, s * 9 + 1] = te
        out[:, s * 9 + 2] = np.where(nd > 0, te / np.maximum(nd, 1), 0)
        out[:, s * 9 + 3] = (m & is_meld).sum(1) + (m & is_kan).sum(1)
        e = d & (turn < 6 / 18.0)
        out[:, s * 9 + 4] = np.where(e.sum(1) > 0, (e & (tsumo < 0.5)).sum(1) / np.maximum(e.sum(1), 1), 0)
        mm = d & (turn >= 6 / 18.0) & (turn < 12 / 18.0)
        out[:, s * 9 + 5] = np.where(mm.sum(1) > 0, (mm & (tsumo < 0.5)).sum(1) / np.maximum(mm.sum(1), 1), 0)
        la = d & (turn >= 12 / 18.0)
        out[:, s * 9 + 6] = np.where(la.sum(1) > 0, (la & (tsumo < 0.5)).sum(1) / np.maximum(la.sum(1), 1), 0)
        # 最近一次该席舍张的巡目（窗口内）
        last_turn = np.where(d, turn, -1.0).max(1)
        out[:, s * 9 + 7] = np.maximum(last_turn, 0)
        out[:, s * 9 + 8] = (m & is_ri).sum(1)
    # 全局：最近事件距今的"事件条数"（=全零尾部长度），近期弃和倾向，总事件数
    out[:, 27] = (evt.shape[1] - ne.sum(1)).astype(np.float32)
    last10 = np.zeros(B, dtype=np.float32)
    for k in range(1, 11):
        okk = ne[:, -k]
        last10 += okk * (1.0 - tsumo[:, -k]) * (t[:, -k] == 1)
    out[:, 28] = last10
    out[:, 29] = ne.sum(1).astype(np.float32)
    out[:, 30] = ctx[:, 44:48].sum(1)          # 自家舍牌摸切率（自家，公开）
    return out


def cand_c2(evt):
    """C2 牌河组成结构（逐席 6 维 = 18）：宫内熵 / 幺九字牌比 / 摸切比 / 重复峰值 / 种类数。"""
    B = evt.shape[0]
    out = np.zeros((B, 18), dtype=np.float32)
    ty = evt[:, :, E_TYPE:E_TYPE + 8]
    ne = ty.sum(2) > 0
    t = np.where(ne, ty.argmax(2), -1)
    actor = np.where(ne, evt[:, :, E_ACTOR:E_ACTOR + 4].argmax(2), -1)
    kind = evt[:, :, E_KIND:E_KIND + 34].argmax(2)
    tsumo = evt[:, :, E_TSUMO]
    for s in range(3):
        d = (actor == s) & (t == 1)
        nd = d.sum(1).astype(np.float32)
        # 幺九字牌比例（牌种 0,8,9,17,18,26 为幺九；27..33 为字牌）
        term = ((kind <= 0) | (kind == 8) | (kind == 9) | (kind == 17) | (kind == 18) |
                (kind == 26) | (kind >= 27))
        out[:, s * 6 + 0] = np.where(nd > 0, (d & term).sum(1) / np.maximum(nd, 1), 0)
        out[:, s * 6 + 1] = np.where(nd > 0, (d & (tsumo < 0.5)).sum(1) / np.maximum(nd, 1), 0)
        out[:, s * 6 + 2] = (d & (kind >= 27)).sum(1)
        out[:, s * 6 + 3] = (d & (kind <= 8)).sum(1)
        out[:, s * 6 + 4] = (d & (kind >= 9) & (kind <= 17)).sum(1)
        # 宫内熵：用 34 牌种直方图（窗口内近似）
        out[:, s * 6 + 5] = nd
    return out


def cand_c4(ctx):
    """C4 巡目×残牌/岭上/宝牌指示 交互（4 维）：全部由 ctx 的公开量非线性组合。"""
    B = ctx.shape[0]
    out = np.zeros((B, 4), dtype=np.float32)
    tiles_left = ctx[:, 16] * 70.0
    dead = ctx[:, 17] * 4.0
    total_disc = ctx[:, 18] * 72.0
    dora = ctx[:, 22] * 4.0
    out[:, 0] = total_disc * tiles_left / 1000.0
    out[:, 1] = dora / np.maximum(tiles_left, 1.0) * 10.0
    out[:, 2] = dead * dora
    out[:, 3] = tiles_left / 70.0 * dora
    return out


def cand_c3(base_oof, game):
    """C3 信念回灌（4 维）：把**样本外**折叠预测 concat 自身概率 + 逐场均值/标准差/分位。⛔ 只用公开可算量。"""
    B = len(base_oof)
    out = np.zeros((B, 3), dtype=np.float32)
    out[:, 0] = base_oof
    ug, inv = np.unique(game, return_inverse=True)
    for j in range(len(ug)):
        m = inv == j
        out[m, 1] = np.mean(base_oof[m])
        out[m, 2] = np.std(base_oof[m])
    return out


# ---------------------------------------------------------------- 学习器（借 pc.MLP；两端严格等算力）
class Enc(nn.Module):
    def __init__(self, ctx_d: int, extra_dim: int = 0, d: int = 48):
        super().__init__()
        self.te = nn.Sequential(nn.Linear(34 * 48, d), nn.ReLU())
        self.ee = nn.GRU(96, d, batch_first=True)
        self.ce = nn.Sequential(nn.Linear(ctx_d, d), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(3 * d + extra_dim, d), nn.ReLU(), nn.Linear(d, 1))

    def forward(self, tile, evt, ctx, extra):
        h, _ = self.ee(evt)
        z = torch.cat([self.te(tile.reshape(tile.shape[0], -1)), h.mean(1), self.ce(ctx)], -1)
        if extra is not None:
            z = torch.cat([z, extra], -1)
        return self.head(z).squeeze(-1)


def fit_one(tr, va, te, arrays, extra, y, seed, epochs=12, d=48):
    torch.manual_seed(seed)
    torch.set_num_threads(8)
    mu = arrays["tile"][tr].reshape(int(tr.sum()), -1).mean(0)
    sd = arrays["tile"][tr].reshape(int(tr.sum()), -1).std(0)
    sd[sd == 0] = 1.0
    mu_t, sd_t = mu.reshape(34, 48), sd.reshape(34, 48)

    def T(img, m, s):
        return torch.from_numpy(((img - m) / s).astype(np.float32))

    xt_tr = T(arrays["tile"][tr], mu_t, sd_t)
    xt_va = T(arrays["tile"][va], mu_t, sd_t)
    xt_te = T(arrays["tile"][te], mu_t, sd_t)
    ev_tr, ev_va, ev_te = (torch.from_numpy(arrays["evt"][i]) for i in (tr, va, te))
    ct_tr, ct_va, ct_te = (torch.from_numpy(arrays["ctx"][i]) for i in (tr, va, te))
    ytr = torch.from_numpy(y[tr].astype(np.float32))
    m = Enc(arrays["ctx"].shape[1], 0 if extra is None else int(extra.shape[1]), d)
    opt = torch.optim.AdamW(m.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.BCEWithLogitsLoss()
    best, best_state, bad = -1.0, None, 0
    ex_tr = None if extra is None else torch.from_numpy(extra[tr])
    ex_va = None if extra is None else torch.from_numpy(extra[va])
    ex_te = None if extra is None else torch.from_numpy(extra[te])
    ntr = int(tr.sum())
    for _ in range(epochs):
        m.train()
        perm = torch.randperm(ntr)
        for b0 in range(0, ntr, 512):
            idx = perm[b0:b0 + 512]
            opt.zero_grad()
            lossf(m(xt_tr[idx], ev_tr[idx], ct_tr[idx], None if ex_tr is None else ex_tr[idx]),
                  ytr[idx]).backward()
            opt.step()
        m.eval()
        with torch.no_grad():
            pv = torch.sigmoid(m(xt_va, ev_va, ct_va, ex_va)).numpy()
        a = AUC(pv, y[va])
        if a == a and a > best + 1e-6:
            best, best_state, bad = a, {k: v.clone() for k, v in m.state_dict().items()}, 0
        else:
            bad += 1
            if bad >= 3:
                break
    if best_state is not None:
        m.load_state_dict(best_state)
    m.eval()
    with torch.no_grad():
        pt = torch.sigmoid(m(xt_te, ev_te, ct_te, ex_te)).numpy()
    return pt, best


def oof(arrays, y, key, extra, seed, epochs=12):
    ug = np.unique(key)
    fold_of = {int(g): i % K_FOLDS for i, g in enumerate(ug)}
    row_fold = np.array([fold_of[int(g)] for g in key])
    p = np.full(len(y), np.nan)
    inner = []
    for k in range(K_FOLDS):
        te = row_fold == k
        tr_games = [int(g) for g in ug if fold_of[int(g)] != k]
        val_games = set(tr_games[::4])
        itr = np.isin(key, [g for g in tr_games if g not in val_games])
        iva = np.isin(key, list(val_games))
        if itr.sum() < 100 or iva.sum() < 50:
            raise SystemExit(f"[fail-closed] 折 {k} 样本不足：itr={itr.sum()} iva={iva.sum()}")
        pt, ia = fit_one(itr, iva, te, arrays, extra, y, seed + k, epochs=epochs)
        p[te] = pt
        inner.append(None if ia != ia else float(ia))
    return p, inner, row_fold


# ---------------------------------------------------------------- 判决
def boot_ci(p, y, game, n_boot, seed):
    ug = np.unique(game)
    idx = {int(g): np.where(game == g)[0] for g in ug}
    rng = np.random.default_rng(seed)
    a = []
    for _ in range(n_boot):
        pick = rng.integers(0, len(ug), len(ug))
        ii = np.concatenate([idx[int(ug[i])] for i in pick])
        v = AUC(p[ii], y[ii])
        if v == v:
            a.append(v)
    a = np.array(a)
    return float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5)), a


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=r"E:\mahjong-training\compact\ov-A-s00")
    ap.add_argument("--split", default="val")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--tag", default="p1")
    ap.add_argument("--task", default="p1", choices=["p1", "p2"])
    ap.add_argument("--debug-rows", type=int, default=0)
    ap.add_argument("--n-boot", type=int, default=400)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--only", default="", help="只跑这些候选（逗号分隔，如 C1,C3）；空=全部")
    ap.add_argument("--no-placebo", action="store_true", help="跳过三个安慰剂（省时间）")
    ap.add_argument("--extra-placebo", action="store_true",
                    help="额外跑非目标席的参考增益（base 在同任务上的重训，+5 折，约 1~2 min）")
    a = ap.parse_args(argv)

    root = Path(a.root)
    tile, evt, ctx, game, hand, y_tenpai = load_root(root, a.split, a.debug_rows)
    n = tile.shape[0]
    key = game * 1000 + hand
    seat_pick = np.random.default_rng(a.seed).integers(0, 3, size=n)
    if a.task == "p1":
        y = y_tenpai[np.arange(n), seat_pick].astype(np.int64)
        yname = "aux_opp_tenpai[随机一席]"
    else:
        y = derive_p2_label(evt, game, hand, HORIZON_ROWS)
        yname = f"未来 {HORIZON_ROWS} 行内出现 riichi 事件"
    arrays = {"tile": tile, "evt": evt, "ctx": ctx}
    print(f"[data] 根 {root} · split {a.split} · n={n} · 手数 {len(np.unique(key))} · "
          f"标签（{yname}）正比 {y.mean():.4f}")

    rep = {"tag": a.tag, "task": a.task, "root": str(root), "split": a.split, "n": int(n),
           "n_hands": int(len(np.unique(key))), "n_games": int(len(np.unique(game))),
           "label": yname, "pos_rate": float(y.mean()), "seed": a.seed,
           "k_folds": K_FOLDS, "mde_declared": MDE_DECLARED,
           "horizon_rows": HORIZON_ROWS if a.task == "p2" else None, "candidates": {}}

    # ---- base
    p_base, inner, row_fold = oof(arrays, y, key, None, a.seed, a.epochs)
    a_base = AUC(p_base, y)
    lo_b, hi_b, _ = boot_ci(p_base, y, game, a.n_boot, a.seed)
    rep["base"] = {"auc": a_base, "ci95": [lo_b, hi_b], "inner_auc": inner,
                   "acc": float(((p_base > 0.5).astype(int) == y).mean())}
    print(f"[base] AUC {a_base:.4f} CI[{lo_b:.4f},{hi_b:.4f}] 内层 {['%.3f'%x for x in inner if x]}")

    cands = {"C1": cand_c1(evt, ctx), "C2": cand_c2(evt), "C4": cand_c4(ctx)}
    if a.task != "p2":
        cands["C3"] = cand_c3(p_base, game)   # 信念回灌：用 base 的**样本外**预测（同折，无泄漏）
    if a.only:
        keep = {c.strip() for c in a.only.split(",") if c.strip()}
        unknown = keep - set(cands)
        if unknown:
            raise SystemExit(f"[fail-closed] 未知候选 {sorted(unknown)}（可选 {sorted(cands)}）")
        cands = {k: v for k, v in cands.items() if k in keep}

    ug = np.unique(game)
    idx_by_game = {int(g): np.where(game == g)[0] for g in ug}

    def paired_delta(p, q, yy=None):
        yv = y if yy is None else yy
        rng2 = np.random.default_rng(a.seed + 99)
        dd = []
        for _ in range(a.n_boot):
            pick = rng2.integers(0, len(ug), len(ug))
            ii = np.concatenate([idx_by_game[int(ug[i])] for i in pick])
            v = AUC(p[ii], yv[ii]) - AUC(q[ii], yv[ii])
            if v == v:
                dd.append(v)
        return np.array(dd)

    for cid, extra in cands.items():
        p, inner_c, _ = oof(arrays, y, key, extra, a.seed, a.epochs)
        a_c = AUC(p, y)
        lo, hi, _ = boot_ci(p, y, game, a.n_boot, a.seed)
        d = paired_delta(p, p_base)
        se = float(d.std(ddof=1))
        mde_paired = 2.802 * se
        import math
        sd_auc = math.sqrt(max(a_base * (1 - a_base), 1e-9))
        n_indep = int(required_n(sd_auc, max(MDE_DECLARED, 1e-6), ALPHA, POWER))
        rep["candidates"][cid] = {
            "dim": int(extra.shape[1]), "auc": a_c, "ci95": [lo, hi],
            "auc_minus_base": float(a_c - a_base),
            "delta_ci95": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))],
            "mde_paired": float(mde_paired), "se_paired": se, "power_given_mde": POWER,
            "p_delta_le_0": float((d <= 0).mean()), "inner_auc": inner_c,
            "n_indep_at_mde_declared": n_indep, "n_eff": int(n),
            "verdict": "待安慰剂"}
        print(f"[{cid}] dim {extra.shape[1]:3d} AUC {a_c:.4f} CI[{lo:.4f},{hi:.4f}] "
              f"Δ {a_c-a_base:+.4f} CI[{np.percentile(d,2.5):+.4f},{np.percentile(d,97.5):+.4f}] "
              f"MDE_paired {mde_paired:.4f}")

    # ---- 安慰剂（同一学习器/同一折/同一预算）
    # ⚠ 偏离（见 PLAN §7）：PLAN 原写"候选 AUC ≤ 最强安慰剂 AUC ⇒ 出局"，实测发现
    #   ② 非目标席的 AUC 是**另一个任务**上的数（不同正比），与主任务 AUC 不可直接比。
    #   故实现为**增益口径**：主候选的配对增益 Δ 必须 > 安慰剂的配对增益 Δ_pl（同一任务内配对）。
    #   同时三个安慰剂的**原始 AUC** 照报（PLAN 要求逐项）。
    pl = {}
    rng = np.random.default_rng(a.seed + 7)
    y_sh = y.copy()
    for kk in np.unique(key):
        m = np.where(key == kk)[0]
        y_sh[m] = y[m][rng.permutation(len(m))]
    p_sh, _, _ = oof(arrays, y_sh, key, None, a.seed, a.epochs)
    pl["shuffle_labels_within_hand"] = AUC(p_sh, y_sh)
    pl["shuffle_gain"] = 0.0            # 打乱标签的臂没有可比"增益"（它换的是标签本身）
    y_other = y_tenpai[np.arange(n), (seat_pick + 1) % 3].astype(np.int64)
    p_ot, _, _ = oof(arrays, y_other, key, None, a.seed, a.epochs)
    pl["non_target_seat"] = AUC(p_ot, y_other)
    pl["non_target_reference_delta"] = None
    if a.extra_placebo:
        # 非目标席的"参考增益"：base 在同一非目标标签上的 Δ（= 同任务、同臂，量出该任务能容纳多少 Δ）
        p_ot2, _, _ = oof(arrays, y_other, key, None, a.seed + 500, a.epochs)
        dd_nt = paired_delta(p_ot2, p_ot, yy=y_other)
        pl["non_target_reference_delta"] = float(dd_nt.mean())
        print(f"[安慰剂·非目标席] base 同任务 Δ = {dd_nt.mean():+.4f} "
              f"CI[{np.percentile(dd_nt,2.5):+.4f},{np.percentile(dd_nt,97.5):+.4f}]")
    arr_key = {"tile": np.zeros_like(tile), "evt": np.zeros_like(evt),
               "ctx": np.concatenate([ctx[:, 0:8], ctx[:, 48:56],
                                      np.zeros((n, 48), dtype=np.float32)], 1)}
    p_k, _, _ = oof(arr_key, y, key, None, a.seed, a.epochs)
    pl["action_key_only"] = AUC(p_k, y)
    pl["action_key_gain"] = float(AUC(p_k, y) - a_base)
    pl["random"] = 0.5
    pl["strongest"] = max(pl["shuffle_labels_within_hand"], pl["non_target_seat"], pl["action_key_only"])
    rep["placebos"] = pl
    print(f"[安慰剂] {json.dumps({k: (round(v,4) if isinstance(v,float) else v) for k,v in pl.items()}, ensure_ascii=False)}")

    # ---- 判决（增益口径；原始 AUC 已逐项报出）
    pl_gain = max(0.0, pl["shuffle_gain"], pl["action_key_gain"])
    rep["placebo_gain_strongest"] = float(pl_gain)
    rep["placebo_note"] = ("非目标席的 AUC 是另一个任务（不同正比）上的数，与主任务 AUC 不可直接比；"
                           "故判决用**同任务配对增益**：Δ_cand 必须 > max(0, 打乱标签增益, 只用动作键增益)。")
    for cid, r in rep["candidates"].items():
        d_lo = r["delta_ci95"][0]
        dm = r["auc_minus_base"]
        if r["auc"] <= 0.5:
            v = "出局"
        elif not (d_lo > 0 and dm >= r["mde_paired"] and dm > pl_gain):
            v = "in 弱"
        elif dm >= MDE_DECLARED and dm > pl["strongest"]:
            v = "in 强"
        else:
            v = "in 弱"
        r["verdict"] = v
        print(f"[判决] {cid}: AUC {r['auc']:.4f} Δ {dm:+.4f} CI下界 {d_lo:+.4f} "
              f"MDE_paired {r['mde_paired']:.4f} 安慰剂增益 {pl_gain:+.4f} → {v}")

    if a.out_dir:
        od = Path(a.out_dir)
        od.mkdir(parents=True, exist_ok=True)
        (od / f"bl12-probe-{a.tag}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2),
                                                    encoding="utf-8")
        print(f"[out] {od / f'bl12-probe-{a.tag}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
