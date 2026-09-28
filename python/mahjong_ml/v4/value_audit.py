"""价值头审计：`docs/TRAINING-V4.md` §8.2 的分布判据（CRPS / 覆盖率 / MAE / ρ / EV）。

**为什么必须单独有这一条**：训练日志里的 `val_loss_value`（HL-Gauss 交叉熵）单独看会骗人 ——
"对每个局面都输出**边缘分布**"这个退化解也能拿到很小的 CE（因为它只差"条件信息"那一项），
而一个塌掉的值头（预测不随状态变化）恰恰就收敛到那个解。所以这里把 CE 与
**边缘基线**、**气候学分布基线**、**解释方差 EV**、**逐小局聚合**摆在一起看。

⚠ 两条必须先知道的结构性事实（不然会误诊）：

1. **`rtg` 在 `(game, hand_no, seat)` 内是常量** —— 它按定义（`Σ_{本局及其后} 收支 + 终局余棒`）
   只随小局变化，与"本小局内第几个决策"无关 ⇒ 同一小局内的逐决策值头**本来就没有可分的东西**；
   `rtg_structure()` 把这条钉成 `rtg_round_spread_max`（应为 0）。
2. **`value − rtg` = 已经滚入的点数差**（就在 obs 的 `ctx.points` 里）—— 但 ⚠ 它**不是** rtg 的预测器：
   实测 `Corr(acc, rtg) = +0.034`（几乎正交）。"只减已知部分"换不来 EV，**别拿它当上界**。

**实测（v4-sp-004 val，34,738 条；`Var` 单位 = 千点²）**：
`Var(value)=410.7`、`Var(rtg)=229.5`、`Var(value−rtg)=167.8`；
最好的整场值头的残差 `Var(value − V)=238 > Var(rtg)=229.5`
⇒ **"整场值头 − 已滚入"这种构造在数学上就赢不过 0 EV**（要赢需 `EV_value > 0.44`，实测最好 0.386–0.42）。
再叠加"知道是哪一小局"的 oracle 组均值也只解释 ~3% 的 rtg 方差 ⇒ **per-decision 的 suffix-sum
不是一个好 critic 目标**（细节与结论见 `NOTES.md` §6.5 第十二轮）。

用法（离线、只读；不训练、不写盘，除非给 `--out`）：

    python -m mahjong_ml.v4 value-audit --data S:\\mahjong-training\\compact\\v4-sp-004 \\
        --ckpt S:\\mahjong-training\\ckpt\\v4-ppo-002 [--ckpt ...] [--split val] [--out x.json]

退出码：0 = 审计跑完；2 = `--strict` 且有关键判据不过（EV 低于阈值 / 覆盖率偏离 > 3pp）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from . import dataset as ds
from . import model as M
from .pretrain import _batch

#: 分箱中心（千点）与两两距离矩阵 —— CRPS 的离散形式要用
CENTERS: np.ndarray = np.linspace(-M.VALUE_RANGE + (2.0 * M.VALUE_RANGE / M.VALUE_BINS) / 2,
                                  M.VALUE_RANGE - (2.0 * M.VALUE_RANGE / M.VALUE_BINS) / 2,
                                  M.VALUE_BINS, dtype=np.float64)
DIST: np.ndarray = np.abs(CENTERS[:, None] - CENTERS[None, :])

#: §8.2 的覆盖率判据：与标称差 ≤ 3pp
COVERAGE_TOL = 0.03
#: "值头还有状态信号"的最低解释方差（低于它当塌了处理；按 §8.2 的意图取 0.10）
EV_FLOOR = 0.10


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64) - float(np.mean(a))
    b = np.asarray(b, dtype=np.float64) - float(np.mean(b))
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else float("nan")


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    return pearson(np.argsort(np.argsort(np.asarray(a, dtype=np.float64))),
                   np.argsort(np.argsort(np.asarray(b, dtype=np.float64))))


def explained_variance(pred: np.ndarray, y: np.ndarray) -> float:
    """`EV = 1 − Var(y−V)/Var(y)` —— critic 的标准判据（对**均值**，与分布形状无关）。

    ⚠ 残差必须**去中心**再比：用未去中心的 MSE 会把"常数偏置"算成"没解释力"
    （自检里"只把已知的已滚入点数减掉"那条当场抓出这个 bug）。
    """
    pred = np.asarray(pred, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    v = float(y.var())
    return float(1.0 - (y - pred).var() / v) if v > 0 else float("nan")


def hl_gauss_ce(prob: np.ndarray, target: np.ndarray) -> float:
    """HL-Gauss 交叉熵（与训练同口径）：`prob` 已归一化、`target` 是软标签。"""
    prob = np.asarray(prob, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    return float(-(np.log(np.clip(prob, 1e-12, None)) * target).sum(-1).mean())


def marginal_ce(target: np.ndarray) -> float:
    """**边缘基线**：对每个局面都输出数据集里的平均软标签时的 CE（塌掉的值头就停在这条线上）。"""
    t = np.asarray(target, dtype=np.float64)
    m = t.mean(0)
    return float(-(np.log(np.clip(m, 1e-12, None)) * m).sum())


def metrics(prob: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """分布预测 `prob[n,bins]` 对真值 `y[n]`（千点）的全套判据。"""
    prob = np.asarray(prob, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    mean = prob @ CENTERS
    err = mean - y
    cdf = np.cumsum(prob, axis=1)
    out: dict[str, float] = {
        "n": float(y.size),
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err * err).mean())),
        "bias": float(err.mean()),
        "pearson": pearson(mean, y),
        "spearman": spearman(mean, y),
        "pred_std": float(mean.std()),
        "true_std": float(y.std()),
        # 同粒度标量基线：恒预测 0 / 恒预测均值
        "mae_zero": float(np.abs(y).mean()),
        "mae_const": float(np.abs(y - y.mean()).mean()),
        "ev": explained_variance(mean, y),
        # CRPS（离散分布）：E|X−y| − ½E|X−X'|
        "crps": float((prob * np.abs(CENTERS[None, :] - y[:, None])).sum(1).mean()
                      - 0.5 * np.einsum("ij,jk,ik->i", prob, DIST, prob).mean()),
    }
    # 气候学基线：把 val 真值的直方图当预测分布（"无信息但已校准"的参照；CRPS 比它差 = 白给）
    hist, _ = np.histogram(np.clip(y, -M.VALUE_RANGE, M.VALUE_RANGE), bins=M.VALUE_BINS,
                           range=(-M.VALUE_RANGE, M.VALUE_RANGE))
    q = hist / max(hist.sum(), 1)
    out["crps_climatology"] = float((q * np.abs(CENTERS - y[:, None])).sum(1).mean()
                                    - 0.5 * float(q @ DIST @ q))
    for lvl in (0.5, 0.8, 0.95):
        a = (1.0 - lvl) / 2.0
        lo = CENTERS[np.argmax(cdf >= a, axis=1)]
        hi = CENTERS[np.argmax(cdf >= 1.0 - a, axis=1)]
        out[f"cov{lvl:g}"] = float(((y >= lo) & (y <= hi)).mean())
        out[f"wid{lvl:g}"] = float((hi - lo).mean())
    return out


def round_groups(game: np.ndarray, hand: np.ndarray) -> dict[tuple[int, int], list[int]]:
    """按 `(game, hand_no)` 聚合（用于逐小局的 ρ / MAE）。"""
    out: dict[tuple[int, int], list[int]] = {}
    for i, key in enumerate(zip(np.asarray(game).tolist(), np.asarray(hand).tolist())):
        out.setdefault((int(key[0]), int(key[1])), []).append(i)
    return out


def group_metrics(mean: np.ndarray, y: np.ndarray,
                  groups: dict[tuple[int, int], list[int]]) -> dict[str, float]:
    gm = np.array([mean[v].mean() for v in groups.values()])
    gt = np.array([y[v].mean() for v in groups.values()])
    return {"rounds": float(len(gm)), "round_pearson": pearson(gm, gt),
            "round_mae": float(np.abs(gm - gt).mean()),
            "round_mae_zero": float(np.abs(gt).mean())}


def rtg_structure(value: np.ndarray, rtg: np.ndarray, game: np.ndarray, hand: np.ndarray,
                  seat: np.ndarray) -> dict[str, float]:
    """`rtg` 的两条结构事实（**判"塌了"要用的硬下界**）：

    ① `rtg` 在 `(game, hand_no, seat)` 内是常量（按定义只随小局变化）；
    ② `value − rtg` = 已经滚入的点数差，而当前点数就在 obs 的 `ctx.points` 里
       ⇒ "只把已知部分减掉"就能达到 `Var(value−rtg)/Var(rtg)` 的解释方差。
    """
    groups: dict[tuple[int, int, int], list[int]] = {}
    for i, key in enumerate(zip(np.asarray(game).tolist(), np.asarray(hand).tolist(),
                                np.asarray(seat).tolist())):
        groups.setdefault((int(key[0]), int(key[1]), int(key[2])), []).append(i)
    spread = max((max(rtg[v]) - min(rtg[v]) for v in groups.values()), default=0.0)
    known = value - rtg
    share = float(known.var() / rtg.var()) if float(rtg.var()) > 0 else float("nan")
    return {"rtg_round_spread_max": float(spread), "rtg_known_var_share": share,
            "var_value": float(value.var()), "var_rtg": float(rtg.var()),
            "corr_acc_rtg": pearson(known, rtg)}


@torch.no_grad()
def forward_value(model: M.V4Model, data: dict, n: int, device: str,
                  batch: int = 2048) -> np.ndarray:
    """把 val 全切分前向一遍，返回 `[n,bins]` 的价值头概率（float64）。"""
    prob = np.empty((n, M.VALUE_BINS), dtype=np.float64)
    for i0 in range(0, n, batch):
        idx = np.arange(i0, min(i0 + batch, n))
        b = _batch(data, idx, device)
        out = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"])
        prob[idx] = torch.softmax(out["value"].float(), dim=-1).cpu().numpy()
    return prob


def load_model(ckpt: str | Path, device: str) -> M.V4Model:
    ck = torch.load(Path(ckpt) / "model.pt", map_location=device, weights_only=False)
    model = M.build().to(device)
    model.load_state_dict(ck["model"], strict=True)
    model.eval()
    return model


def audit(data_dir: str | Path, split: str, ckpts: list[str],
          device: str | None = None, batch: int = 2048,
          value_key: str = "auto") -> dict[str, dict]:
    """对每个 ckpt 报一份判据表。`value_key="auto"` = 用数据集里可用的一列（优先 `value`）。"""
    data = ds.load_split(data_dir, split)
    lens = {k: (int(data[k].shape[0]) if data.get(k) is not None else -1)
            for k in ("tile", "evt", "ctx", "cand", "nlegal", "value", "label")}
    n = min(v for v in lens.values() if v > 0)
    y = np.asarray(data["value"][:n], dtype=np.float64)
    rtg = np.asarray(data["rtg"][:n], dtype=np.float64) if data.get("rtg") is not None else None
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    groups = round_groups(data["game"][:n], data["hand_no"][:n])
    struct: dict[str, float] = {}
    if rtg is not None and not bool(np.isnan(rtg).all()):
        struct = rtg_structure(y, np.nan_to_num(rtg), data["game"][:n], data["hand_no"][:n],
                               data["seat"][:n])
    report: dict[str, dict] = {}
    for ck in ckpts:
        model = load_model(ck, dev)
        prob = forward_value(model, data, n, dev, batch)
        m = metrics(prob, y)
        m.update(group_metrics(prob @ CENTERS, y, groups))        # CE 与边缘基线（对 value 与 rtg 两个目标各算一次 —— 两者不是同一个量，别混着读）
        for key, tgt in (("value", y), ("rtg", rtg)):
            if tgt is None or bool(np.isnan(tgt).all()):
                continue
            t = M.hl_gauss_targets(torch.from_numpy(tgt).float().to(dev)).cpu().numpy()
            m[f"ce_{key}"] = hl_gauss_ce(prob, t)
            m[f"ce_marginal_{key}"] = marginal_ce(t)
            m[f"ev_{key}"] = explained_variance(prob @ CENTERS, tgt)
        name = Path(ck).name
        # 这份权重当初按哪个目标训的值头？读 ckpt 旁边的 `metrics.json`（缺了就按整场口径）
        target = "value"
        try:
            meta = json.loads((Path(ck) / "metrics.json").read_text(encoding="utf-8"))
            if str((meta.get("args") or {}).get("value_target", "final")) == "rtg":
                target = "rtg"
        except (OSError, ValueError):
            pass
        report[name] = {"path": str(ck), "value_target": target, **struct, **m}
    return report


def verdicts(row: dict, value_key: str = "value") -> list[tuple[bool, str]]:
    """把一行判据翻成 PASS/FAIL（`False` = 不合格）。判据口径见 `docs/TRAINING-V4.md` §8.2。"""
    out = []
    ev = row.get(f"ev_{value_key}")
    share = row.get("rtg_known_var_share")
    hint = (f"（rtg 口径：`rtg` 在小局内是常量，`value−rtg` 与它几乎正交"
            f"；已滚入部分的方差份额 {share:.2f} 只是目标侧的结构数，**不是 EV 上界**）"
            if value_key == "rtg" and share is not None else "")
    if ev is not None:
        out.append((ev >= EV_FLOOR,
                    f"解释方差 EV({value_key}) {ev:+.4f} ≥ {EV_FLOOR:g}{hint}"))
    ce, ce_m = row.get(f"ce_{value_key}"), row.get(f"ce_marginal_{value_key}")
    if ce is not None and ce_m is not None:
        out.append((ce < ce_m, f"CE({value_key}) {ce:.4f} < 边缘基线 {ce_m:.4f}"))
    for lvl in (0.5, 0.8, 0.95):
        cov = row.get(f"cov{lvl:g}")
        if cov is not None:
            out.append((abs(cov - lvl) <= COVERAGE_TOL,
                        f"{lvl * 100:g}% 区间覆盖率 {cov:.4f}（标称差 {abs(cov - lvl) * 100:.1f}pp ≤ 3pp）"))
    if row.get("crps") is not None and row.get("crps_climatology") is not None:
        out.append((row["crps"] < row["crps_climatology"],
                    f"CRPS {row['crps']:.4f} < 气候学基线 {row['crps_climatology']:.4f}"))
    return out


def ridge_ev(x_train: np.ndarray, y_train: np.ndarray, x_val: np.ndarray,
             y_val: np.ndarray, lam: float = 1e-3) -> float:
    """岭回归的**解释方差**（在训练切分上闭式求解、在 val 上量 EV）。

    用途：给"值头最多能做到多少"一个**不依赖训练**的参照 —— 把引擎真值特征线性喂进去，
    看这些目标还剩多少可解释的方差。⚠ 它是**参照**不是上界（非线性模型可能更好），
    但"连线性真值特征都解释不了"足以判死一条路。
    """
    xtx = x_train.T @ x_train + lam * np.eye(x_train.shape[1])
    w = np.linalg.solve(xtx, x_train.T @ y_train)
    return explained_variance(x_val @ w, y_val)


def _flat_aux(data: dict, n: int, *, with_opp_hand: bool) -> tuple[np.ndarray, list[str]]:
    """把标签侧的引擎真值压成一个特征矩阵（列名带出来，便于读结论）。"""
    cols: list[np.ndarray] = []
    names: list[str] = []
    src = [("own_shanten", "aux_own_shanten_after"), ("own_tenpai", "aux_own_tenpai"),
           ("win_flag", "aux_win_flag"), ("opp_tenpai", "aux_opp_tenpai"),
           ("opp_dealin", "aux_opp_dealin")]
    if with_opp_hand:
        src.append(("opp_hand", "aux_opp_hand"))
    for name, key in src:
        arr = data.get(key)
        if arr is None:
            continue
        a = np.asarray(arr[:n], dtype=np.float64)
        a = a[:, None] if a.ndim == 1 else a.reshape(n, -1)
        cols.append(a)
        names += [f"{name}[{i}]" for i in range(a.shape[1])]
    x = np.concatenate(cols, axis=1) if cols else np.zeros((n, 0))
    return np.concatenate([x, np.ones((n, 1))], axis=1), names


def ceiling_report(data_dir: str | Path, split: str = "val", *,
                   lam: float = 1e-3) -> dict[str, dict[str, float]]:
    """**引擎真值特征的线性参照**：`value` / `rtg` / 小局收支 `delta` 各能解释多少。

    为什么要它：`rtg` 值头训不出来时，必须分清"是我们的训练/架构不行"还是"这个目标本身
    就没有可解释的方差"。这里把标签侧的引擎真值（自家向听/听牌/和了 + 对手听牌/放铳）线性喂进去，
    再看它能不能排 rtg —— 排不动，就不是训练的问题。
    ⚠ `with_opp_hand=True` 那一栏用了**别家的真手牌**（推理端拿不到），只作"作弊参照"。
    """
    tr = ds.load_split(data_dir, "train")
    va = ds.load_split(data_dir, split)
    out: dict[str, dict[str, float]] = {}
    for tag, with_hand in (("state", False), ("oracle_hand", True)):
        n_tr = int(tr["nlegal"].shape[0])
        n_va = int(va["nlegal"].shape[0])
        x_tr, names = _flat_aux(tr, n_tr, with_opp_hand=with_hand)
        x_va, _ = _flat_aux(va, n_va, with_opp_hand=with_hand)
        row: dict[str, float] = {"features": float(x_tr.shape[1])}
        for key, scale in (("value", 1.0), ("rtg", 1.0), ("delta", 1000.0)):
            y_tr = np.asarray(tr[key][:n_tr], dtype=np.float64) / scale
            y_va = np.asarray(va[key][:n_va], dtype=np.float64) / scale
            if not np.isfinite(y_tr).all() or not np.isfinite(y_va).all():
                continue
            row[f"ev_{key}"] = ridge_ev(x_tr, y_tr, x_va, y_va, lam)
        out[tag] = row
    out["state"]["columns"] = float(len(names))            # type: ignore[assignment]
    return out


def fit_temperature(prob: np.ndarray, target: np.ndarray | None = None,
                    y: np.ndarray | None = None, *, lo: float = 0.05, hi: float = 20.0,
                    steps: int = 60) -> float:
    """**温度缩放**：`q ∝ p^(1/T)` 上拟合一个标量 T（在 train 切分上拟合、val 上量效果）。

    为什么能修覆盖率：HL-Gauss 训出来的分布**欠覆盖**（95% 区间只盖到 82–87%）说明它太自信；
    T>1 把分布摊平（区间变宽）。只用一个标量，所以过拟合风险极小。

    @param target 软标签（有它就用 CE 当目标）；不给就用 `y` 的 NLL（把真值当成点质量）
    """
    p = np.asarray(prob, dtype=np.float64)
    p = np.clip(p, 1e-12, None)
    lp = np.log(p)

    def loss_at(t: float) -> float:
        z = lp / t
        z -= z.max(axis=1, keepdims=True)
        logq = z - np.log(np.exp(z).sum(axis=1, keepdims=True))
        if target is not None:
            return float(-(np.asarray(target, dtype=np.float64) * logq).sum(-1).mean())
        idx = np.clip(np.searchsorted(CENTERS, np.asarray(y, dtype=np.float64)),
                      0, CENTERS.size - 1)
        return float(-logq[np.arange(logq.shape[0]), idx].mean())

    # 一维凸问题：先粗网格再二分细化（不引 scipy）
    grid = np.geomspace(lo, hi, steps)
    vals = [loss_at(float(t)) for t in grid]
    best = float(grid[int(np.argmin(vals))])
    a, b = best / 1.3, best * 1.3
    for _ in range(40):
        m1, m2 = a + (b - a) / 3, b - (b - a) / 3
        if loss_at(m1) < loss_at(m2):
            b = m2
        else:
            a = m1
    return float((a + b) / 2)


def apply_temperature(prob: np.ndarray, temp: float) -> np.ndarray:
    """`q ∝ p^(1/T)`（与 `softmax(logits/T)` 等价 —— 概率取幂再归一化即可恢复 logits 的缩放）。"""
    if abs(float(temp) - 1.0) < 1e-12:
        return np.asarray(prob, dtype=np.float64)
    p = np.clip(np.asarray(prob, dtype=np.float64), 1e-12, None) ** (1.0 / float(temp))
    return p / p.sum(axis=1, keepdims=True)


def gae_target_report(data_dir: str | Path, split: str, ckpt: str, *, behaviour: str,
                      lam: float = 0.9, rank_weight: float = 0.0, gamma: float = 1.0,
                      device: str | None = None, batch: int = 2048) -> dict[str, Any]:
    """按**手级 GAE 的 λ-回报**给这份值头打分（P1 的判据口径）。

    为什么不能只看 `value`/`rtg`：`--advantage gae-hand` 之后值头学的是 TD(λ) 回报，
    用别的列量 EV 是"拿另一把尺子量"（§14 P1 的判据①必须是它自己的目标）。
    这里用**同一份** `v4/adv.py` + 行为策略的 `V_old` 复算 val 切分的 λ-回报，
    再量值头对它的 EV / 覆盖率 / CRPS。
    """
    from . import pretrain as v4pt                       # 循环导入在这里解（只在调用时用）
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    va = ds.load_split(data_dir, split)
    n = int(va["nlegal"].shape[0])
    beh = v4pt._load_behaviour(behaviour, dev)
    v_old = v4pt._behaviour_values(beh, va, dev, batch)
    del beh
    res = v4pt._hand_advantage(va, v_old, gamma=gamma, lam=lam, rank_weight=rank_weight,
                               is_student=None)
    y = np.asarray(res["vtarget"][:n], dtype=np.float64)
    model = load_model(ckpt, dev)
    prob = forward_value(model, va, n, dev, batch)
    t = M.hl_gauss_targets(torch.from_numpy(y).float().to(dev)).cpu().numpy()
    met = metrics(prob, y)
    met["ce_value"] = hl_gauss_ce(prob, t)                 # 与 `audit()` 同口径：CE + 边缘基线
    met["ce_marginal_value"] = marginal_ce(t)
    return {"target": "gae-hand", "lam": lam, "gamma": gamma, "rank_weight": rank_weight,
            "behaviour": str(behaviour), "behavior_stats": res["stats"], "metrics": met}


def calibration_report(data_dir: str | Path, split: str, ckpt: str, *, target: str = "value",
                       rows: int = 20000, device: str | None = None,
                       batch: int = 2048) -> dict[str, Any]:
    """温度缩放的**前后对照**：温度在**训练切分**上拟合（避免"在 val 上拟合再报 val"的乐观偏差），
    覆盖率/CRPS/EV 在 val 上报。返回 `{temp, before, after}`。
    """
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    tr = ds.load_split(data_dir, "train")
    va = ds.load_split(data_dir, split)
    n_tr = min(int(rows), int(tr["nlegal"].shape[0]))
    n_va = int(va["nlegal"].shape[0])
    model = load_model(ckpt, dev)
    p_tr = forward_value(model, tr, n_tr, dev, batch)
    y_tr = _target_of(tr, target, n_tr)[0]
    temp = fit_temperature(p_tr, y=y_tr)
    p_va = forward_value(model, va, n_va, dev, batch)
    y_va = _target_of(va, target, n_va)[0]
    return {"target": target, "temp": temp,
            "before": metrics(p_va, y_va),
            "after": metrics(apply_temperature(p_va, temp), y_va)}


def _native_target(ckpt: str) -> str:
    """这份权重当初按哪个目标训的值头（读旁边的 `metrics.json`；缺了按整场口径）。"""
    try:
        meta = json.loads((Path(ckpt) / "metrics.json").read_text(encoding="utf-8"))
        vt = str((meta.get("args") or {}).get("value_target", "final"))
    except (OSError, ValueError):
        return "value"
    return "rtg" if vt == "rtg" else ("delta" if vt == "delta" else "value")


def _target_of(data: dict, target: str, n: int) -> tuple[np.ndarray, str]:
    """取某个目标列（千点）。`delta` 是点数 ⇒ 除 1000。"""
    if target == "delta":
        if data.get("delta") is None:
            raise SystemExit("数据集没有 `delta` 列")
        return np.asarray(data["delta"][:n], dtype=np.float64) / 1000.0, "delta"
    if data.get(target) is None:
        raise SystemExit(f"数据集没有 `{target}` 列")
    return np.asarray(data[target][:n], dtype=np.float64), target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mahjong_ml.v4 value-audit",
                                 description="价值头分布判据审计（§8.2）")
    ap.add_argument("--data", required=True, help="紧凑数据集目录（含 val.* / meta.json）")
    ap.add_argument("--split", default="val")
    ap.add_argument("--ckpt", action="append", required=True, help="checkpoint 目录（可重复）")
    ap.add_argument("--device", default=None, help="cuda / cpu（缺省自动）")
    ap.add_argument("--batch", type=int, default=2048)
    ap.add_argument("--value-key", default="auto", choices=["auto", "value", "rtg"],
                    help="判据按哪一列的目标算（auto = 该 ckpt 训练时的口径看不出来时用 value）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--strict", action="store_true", help="有关键判据不过就返回 2")
    ap.add_argument("--ceiling", action="store_true",
                    help="另报**引擎真值特征的线性参照**（value / rtg / delta 各能解释多少方差）")
    ap.add_argument("--calibrate", action="store_true",
                    help="另报**温度缩放**前后的覆盖率/CRPS（一个标量，修欠覆盖最便宜的一招）")
    ap.add_argument("--calib-rows", type=int, default=20000,
                    help="拟合温度用训练切分的前多少行（一个标量，两万行足够）")
    ap.add_argument("--gae-target", default=None, metavar="BEHAVIOUR",
                    help="另报**手级 GAE 的 λ-回报**口径（P1 的判据口径）：值是行为策略 ckpt/net.bin；"
                         "配合 `--gae-lambda` / `--rank-weight` / `--gae-gamma`")
    ap.add_argument("--gae-lambda", type=float, default=0.9)
    ap.add_argument("--gae-gamma", type=float, default=1.0)
    ap.add_argument("--rank-weight", type=float, default=0.0)
    args = ap.parse_args(argv)

    if args.gae_target:
        for ck in args.ckpt:
            rep = gae_target_report(args.data, args.split, ck, behaviour=args.gae_target,
                                    lam=args.gae_lambda, rank_weight=args.rank_weight,
                                    gamma=args.gae_gamma, device=args.device, batch=args.batch)
            m = rep["metrics"]
            st = rep["behavior_stats"]
            print(f"\n手级 GAE 口径（{Path(ck).name}）λ={rep['lam']:g} γ={rep['gamma']:g} "
                  f"rank_weight={rep['rank_weight']:g}；行为策略 {Path(rep['behaviour']).name}")
            print(f"  小局 {int(st['hands'])} 个；模板回报 std {st['reward_std']:.3f} 千点")
            print(f"  EV {m['ev']:+.4f} · CE/边缘 {m.get('ce_value', float('nan')):.4f} · "
                  f"CRPS/气候学 {m['crps']:.4f}/{m['crps_climatology']:.4f} · "
                  f"MAE/常数 {m['mae']:.3f}/{m['mae_const']:.3f}")
            print(f"  覆盖率 50/80/95% = {m['cov0.5']:.3f}/{m['cov0.8']:.3f}/{m['cov0.95']:.3f}"
                  f"（标称差 {abs(m['cov0.5'] - 0.5) * 100:.1f}/{abs(m['cov0.8'] - 0.8) * 100:.1f}/"
                  f"{abs(m['cov0.95'] - 0.95) * 100:.1f}pp；判据 ≤3pp）")
            for passed, text in verdicts({**m, "ev": m["ev"]}, "value"):
                print(f"   [{'ok  ' if passed else 'FAIL'}] {text}")

    if args.ceiling:
        ceil = ceiling_report(args.data, args.split)
        print(f"引擎真值特征的线性参照（在 train 上闭式拟合、在 {args.split} 上量 EV；"
              f"features = 用了几列）：")
        for tag, row in ceil.items():
            if tag not in ("state", "oracle_hand"):
                continue
            note = "（含别家**真手牌**，推理端拿不到 ⇒ 只作作弊参照）" if tag == "oracle_hand" else ""
            print(f"  {tag:<12}{note}features={int(row.get('features', 0))}  "
                  + "  ".join(f"EV({k[3:]})={v:+.4f}" for k, v in row.items() if k.startswith("ev_")))

    if args.calibrate:
        for ck in args.ckpt:
            rep = calibration_report(args.data, args.split, ck, target=_native_target(ck),
                                     rows=args.calib_rows, device=args.device, batch=args.batch)
            b, a = rep["before"], rep["after"]
            print(f"\n温度缩放（{Path(ck).name}，目标 {rep['target']}，"
                  f"温度在 train 前 {args.calib_rows} 行上拟合）：T = {rep['temp']:.3f}")
            for k in ("crps", "cov0.5", "cov0.8", "cov0.95", "wid0.5", "wid0.8", "wid0.95", "ev"):
                mark = ""
                if k.startswith("cov") and abs(a[k] - float(k[3:])) <= COVERAGE_TOL:
                    mark = "   ← 覆盖率达标（≤3pp）"
                print(f"   {k:<10}前 {b[k]:+.4f} → 后 {a[k]:+.4f}{mark}")

    report = audit(args.data, args.split, args.ckpt, device=args.device, batch=args.batch)
    bad = False
    for name, row in report.items():
        print(f"\n== {name} （n={int(row['n'])}，训练时的值头目标 {row.get('value_target')}）==")
        for k in ("ce_value", "ce_marginal_value", "ce_rtg", "ce_marginal_rtg",
                  "ev_value", "ev_rtg", "mae", "mae_const", "mae_zero", "rmse", "bias",
                  "pearson", "spearman", "pred_std", "true_std", "crps", "crps_climatology",
                  "cov0.5", "wid0.5", "cov0.8", "wid0.8", "cov0.95", "wid0.95",
                  "rounds", "round_pearson", "round_mae", "round_mae_zero",
                  "rtg_round_spread_max", "rtg_known_var_share", "var_value", "var_rtg",
                  "corr_acc_rtg"):
            if k in row:
                print(f"   {k:<20}{row[k]:+.4f}")
        key = args.value_key
        if key == "auto":
            key = str(row.get("value_target", "value"))
        print(f"   ---- 判据（口径：{key}）----")
        for passed, text in verdicts(row, key):
            print(f"   [{'ok  ' if passed else 'FAIL'}] {text}")
            bad = bad or not passed
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写 {args.out}")
    return 2 if (args.strict and bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
