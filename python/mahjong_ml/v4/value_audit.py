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
from . import adv as v4adv
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
          value_key: str = "auto", target: str = "auto",
          ev_ref: str = "floor") -> dict[str, dict]:
    """对每个 ckpt 报一份判据表。

    @param value_key 判据按哪一列的目标算（`auto` = 从 ckpt 的 `metrics.json` 读 `value_target`）
    @param target **主口径**：`auto`（同上）/ `value` / `rtg` / `delta` —— P1b 之后值头可能学的是
        "本小局收支"（`delta`），拿 `value` 去量它是"用另一把尺子量"（§14.6）
    @param ev_ref `floor` = 用绝对门槛 `EV_FLOOR`；`legit` = 现算**合法天花板**（`legit_full` 那组，
        同口径）并用 `EV ≥ 0.8 × 天花板` 当判据（§14.8.4：绝对门槛对实现值类目标不可达）
    """
    data = ds.load_split(data_dir, split)
    lens = {k: (int(data[k].shape[0]) if data.get(k) is not None else -1)
            for k in ("tile", "evt", "ctx", "cand", "nlegal", "value", "label")}
    n = min(v for v in lens.values() if v > 0)
    y = np.asarray(data["value"][:n], dtype=np.float64)
    rtg = np.asarray(data["rtg"][:n], dtype=np.float64) if data.get("rtg") is not None else None
    value_audit_target = target
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
        # 这份权重当初按哪个目标训的值头？读 ckpt 旁边的 `metrics.json`（缺了就按整场口径）
        target = "value"
        try:
            meta = json.loads((Path(ck) / "metrics.json").read_text(encoding="utf-8"))
            vt = str((meta.get("args") or {}).get("value_target", "final"))
            target = {"rtg": "rtg", "delta": "delta"}.get(vt, "value")
        except (OSError, ValueError):
            pass
        want = target if value_audit_target in ("auto", None) else value_audit_target
        y = _target_of(data, want, n)[0]           # ★ 主口径：可以不是 `value`（delta / gae 的 vtarget）
        m = metrics(prob, y)
        m.update(group_metrics(prob @ CENTERS, y, groups))
        # CE 与边缘基线（主口径 + `value`/`rtg` 两个副口径 —— 它们不是同一个量，别混着读）
        for key, tgt in (("value", np.asarray(data["value"][:n], dtype=np.float64)),
                         ("rtg", rtg),
                         ("delta", np.asarray(data["delta"][:n], dtype=np.float64) / 1000.0
                          if data.get("delta") is not None else None)):
            if tgt is None or bool(np.isnan(tgt).all()):
                continue
            t = M.hl_gauss_targets(torch.from_numpy(tgt).float().to(dev)).cpu().numpy()
            m[f"ce_{key}"] = hl_gauss_ce(prob, t)
            m[f"ce_marginal_{key}"] = marginal_ce(t)
            m[f"ev_{key}"] = explained_variance(prob @ CENTERS, tgt)
        m["ev"] = m[f"ev_{want}"] if f"ev_{want}" in m else explained_variance(prob @ CENTERS, y)
        name = Path(ck).name
        report[name] = {"path": str(ck), "value_target": target, "target": want, **struct, **m}
    if ev_ref == "legit":
        # 现算**合法天花板**（同一口径）—— 判据用比例，不再用绝对门槛（§14.8.4）
        tr = ds.load_split(data_dir, "train")
        for name, row in report.items():
            want = str(row.get("target", "value"))
            if tr.get(want) is None:
                continue
            n_tr = int(tr["nlegal"].shape[0])
            x_tr = _cols_of(tr, CEIL_GROUPS[CEIL_REF_KEY], n_tr)
            x_va = _cols_of(data, CEIL_GROUPS[CEIL_REF_KEY], n)
            y_tr = _target_of(tr, want, n_tr)[0]
            y_va = _target_of(data, want, n)[0]
            ref = ridge_ev(x_tr, y_tr, x_va, y_va)
            row["ev_ref"] = float(ref)
            row["ev_ref_frac"] = float(EV_CEILING_FRAC)
            row["ev_need"] = float(EV_CEILING_FRAC * ref)
    return report


#: 合法天花板判据的比例：`EV ≥ EV_CEILING_FRAC × legit 天花板`。
#:
#: 为什么是 0.7 而不是更高的数：① 参照本身是**线性**岭回归，val 上 n=3.5 万 ⇒ 抽样噪声约 ±0.005
#: （相对 ±7%）；② 它是线性参照，非线性上限可能略高。取 0.7 能把两件事**分开**：
#: "学到了这一口径的信号"（实测 0.72–0.80 × 天花板）与"塌成均值"（EV ≈ 0.02 ⇒ 0.3 × 天花板）。
#: 取 0.8 会在噪声里判生死（`v4-hand-003` 实测 0.72 就是这么被判 FAIL 的）。
EV_CEILING_FRAC = 0.7


def verdicts(row: dict, value_key: str = "value",
             ev_ref: float | None = None) -> list[tuple[bool, str]]:
    """把一行判据翻成 PASS/FAIL（`False` = 不合格）。判据口径见 `docs/TRAINING-V4.md` §8.2 / §14.8.4。

    @param ev_ref **合法天花板参照**（同口径的 `legit*` 岭回归 EV）。给了就用
        `EV ≥ 0.8 × 天花板` 当判据，不再用绝对值 `EV_FLOOR` —— 绝对门槛在"实现值"类目标上
        是**构造上不可达**的（小局收支的合法天花板只有 0.069，第十九轮实测）。
    """
    out = []
    ev = row.get(f"ev_{value_key}")
    share = row.get("rtg_known_var_share")
    hint = (f"（rtg 口径：`rtg` 在小局内是常量，`value−rtg` 与它几乎正交"
            f"；已滚入部分的方差份额 {share:.2f} 只是目标侧的结构数，**不是 EV 上界**）"
            if value_key == "rtg" and share is not None else "")
    if ev is not None:
        if ev_ref is not None and np.isfinite(ev_ref) and ev_ref > 0:
            need = EV_CEILING_FRAC * ev_ref
            out.append((ev >= need,
                        f"解释方差 EV({value_key}) {ev:+.4f} ≥ {EV_CEILING_FRAC:g} × 合法天花板 "
                        f"{ev_ref:.4f} = {need:.4f}{hint}"))
        else:
            out.append((ev >= EV_FLOOR,
                        f"解释方差 EV({value_key}) {ev:+.4f} ≥ {EV_FLOOR:g}{hint}"))
    ce, ce_m = row.get(f"ce_{value_key}"), row.get(f"ce_marginal_{value_key}")
    if ce is not None and ce_m is not None:
        out.append((ce < ce_m, f"CE({value_key}) {ce:.4f} < 边缘基线 {ce_m:.4f}"))
    for lvl in (0.5, 0.8, 0.95):
        cov = row.get(f"cov{lvl:g}")
        if cov is not None:
            # ⚠ 容差要带一点点浮点余量：`0.53 - 0.5 == 0.030000000000000027 > 0.03`
            #   ⇒ 恰好卡在边界上的覆盖会被判 FAIL（实测 `v4-hand-001` 就这么假红了一次）
            out.append((abs(cov - lvl) <= COVERAGE_TOL + 1e-9,
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


#: 引擎真值列的**合法性分组** —— 上界判据必须按这个分组读。
#:
#: ⚠ 第十九轮之前的 `ceiling_report` 把 `own_shanten/own_tenpai/win_flag/opp_tenpai/opp_dealin`
#: **一锅端**叫 "state" 参照，于是报出来的 `EV(delta)=0.632` 被当成"合法信息的天花板"。
#: 但 `win_flag` 是**本小局的结局本身**（"我这局和了没有"）、`opp_tenpai/opp_dealin/opp_hand`
#: 是**别家的隐藏真值** —— 拿它们预测本小局收支等于**用答案预测答案**，那不是天花板，是作弊。
#: 合法的只有"决策那一刻自家能算的"：做牌后的向听/听牌（`legit_own`）与引擎牌效
#: `HandEval.afterDiscard`（`legit_engine`，也就是 `cand.derived` 的来源）。
CEIL_GROUPS: dict[str, tuple[str, ...]] = {
    "legit_own": ("aux_own_shanten_after", "aux_own_tenpai"),
    "legit_engine": ("effect",),
    "legit": ("aux_own_shanten_after", "aux_own_tenpai", "effect"),
    "legit_ctx(公开状态)": ("ctx",),
    "legit_visible(状态+牌效)": ("ctx", "cand_agg"),
    "legit_full(全部合法)": ("ctx", "cand_agg", "aux_own_shanten_after", "aux_own_tenpai", "effect"),
    "label_win(结局)": ("aux_win_flag",),
    "label_opp(隐藏)": ("aux_opp_tenpai", "aux_opp_dealin"),
    "label_hand(隐藏)": ("aux_opp_hand",),
    "label_all": ("aux_win_flag", "aux_opp_tenpai", "aux_opp_dealin", "aux_opp_hand"),
    "everything": ("aux_own_shanten_after", "aux_own_tenpai", "effect", "aux_win_flag",
                   "aux_opp_tenpai", "aux_opp_dealin", "aux_opp_hand"),
}

#: 判据的**合法天花板参照组**（`--ev-ref legit` 用它现算；改组名必须同步改这里 —— 自检钉住）
CEIL_REF_KEY = "legit_full(全部合法)"


def _cand_agg(data: dict, n: int) -> np.ndarray:
    """逐候选派生量的**合法聚合**（按合法候选取 mean / max）—— "牌效"这条合法信息臂。

    为什么要它：`legit_own` 只有"我打算打哪张 → 之后向听几"这一个数，
    而"**所有**候选里最好的一张能进几张有效牌"这种牌效信息是决策那一刻完全合法的。
    不把这条臂量出来，"delta 学不动"就可能被误诊成"特征不够好"。
    """
    from . import spec as S
    _, s0, w0 = S.block_slices()["cand.derived"]
    cand = np.asarray(data["cand"][:n, :, s0:s0 + w0], dtype=np.float64)
    nleg = np.asarray(data["nlegal"][:n], dtype=np.int64)
    m = np.arange(cand.shape[1])[None, :] < nleg[:, None]
    w = m[:, :, None].astype(np.float64)
    cnt = np.maximum(w.sum(axis=1), 1.0)
    mean = (cand * w).sum(axis=1) / cnt
    mx = np.where(m[:, :, None], cand, -np.inf).max(axis=1)
    return np.concatenate([mean, np.where(np.isfinite(mx), mx, 0.0)], axis=1)


def _cols_of(data: dict, keys: tuple[str, ...], n: int) -> np.ndarray:
    """按列名取一份特征矩阵（一维列当一列、多维列展平），末列接截距。

    ⚠ **逐候选列**（`effect` 的形状是 `[n, L, 3]`）必须先在**候选维**上取一行，
    否则 train/val 的 `L` 不同（29 vs 21）会拼出两个宽度不同的矩阵，
    报错长这样：`matmul … size 88 is different from 64`。取的是**这一手实际打出的那张**的
    引擎牌效（`label` 下标）—— 与 `cand.derived` 同源，是决策那一刻真能算的量。
    """
    per_cand = {"effect", "cand"}
    cols: list[np.ndarray] = []
    for key in keys:
        if key == "cand_agg":
            cols.append(_cand_agg(data, n))
            continue
        arr = data.get(key)
        if arr is None:
            continue
        a = np.asarray(arr[:n], dtype=np.float64)
        if key in per_cand and a.ndim == 3:
            lab = np.asarray(data["label"][:n], dtype=np.int64)
            a = a[np.arange(n), np.clip(lab, 0, a.shape[1] - 1)]
        cols.append(a[:, None] if a.ndim == 1 else a.reshape(n, -1))
    x = np.concatenate(cols, axis=1) if cols else np.zeros((n, 0))
    return np.concatenate([x, np.ones((n, 1))], axis=1)


def ceiling_report(data_dir: str | Path, split: str = "val", *,
                   lam: float = 1e-3) -> dict[str, dict[str, float]]:
    """**引擎真值特征的线性参照**，按 `CEIL_GROUPS` 的**合法性**分组报 `value` / `rtg` / `delta`。

    为什么要它：值头训不出来时，必须分清"是我们的训练/架构不行"还是"这个目标本身
    就没有可解释的方差"。**判据只读 `legit` 那一行** —— 含 `label_*` 的行是作弊上界
    （`win_flag` 就是本小局结局，`opp_*` 是别家隐藏真值），只用来回答"信息在不在标签里"。
    """
    tr = ds.load_split(data_dir, "train")
    va = ds.load_split(data_dir, split)
    out: dict[str, dict[str, float]] = {}
    for tag, keys in CEIL_GROUPS.items():
        n_tr = int(tr["nlegal"].shape[0])
        n_va = int(va["nlegal"].shape[0])
        x_tr = _cols_of(tr, keys, n_tr)
        x_va = _cols_of(va, keys, n_va)
        row: dict[str, float] = {"features": float(x_tr.shape[1])}
        for key, scale in (("value", 1.0), ("rtg", 1.0), ("delta", 1000.0)):
            if tr.get(key) is None:
                continue
            y_tr = np.asarray(tr[key][:n_tr], dtype=np.float64) / scale
            y_va = np.asarray(va[key][:n_va], dtype=np.float64) / scale
            if not np.isfinite(y_tr).all() or not np.isfinite(y_va).all():
                continue
            row[f"ev_{key}"] = ridge_ev(x_tr, y_tr, x_va, y_va, lam)
        out[tag] = row
    return out


def _legacy_ceiling(data_dir: str | Path, split: str = "val", *,
                    lam: float = 1e-3) -> dict[str, dict[str, float]]:
    """老口径（`state` / `oracle_hand` 两栏）—— 只作历史数字的对照，新判据别用它。"""
    tr = ds.load_split(data_dir, "train")
    va = ds.load_split(data_dir, split)
    out: dict[str, dict[str, float]] = {}
    for tag, with_hand in (("state", False), ("oracle_hand", True)):
        n_tr = int(tr["nlegal"].shape[0])
        n_va = int(va["nlegal"].shape[0])
        x_tr, names = _flat_aux(tr, n_tr, with_opp_hand=with_hand)
        x_va, _ = _flat_aux(va, n_va, with_opp_hand=with_hand)
        row: dict[str, float] = {"features": float(x_tr.shape[1]), "columns": float(len(names))}
        for key, scale in (("value", 1.0), ("rtg", 1.0), ("delta", 1000.0)):
            y_tr = np.asarray(tr[key][:n_tr], dtype=np.float64) / scale
            y_va = np.asarray(va[key][:n_va], dtype=np.float64) / scale
            if not np.isfinite(y_tr).all() or not np.isfinite(y_va).all():
                continue
            row[f"ev_{key}"] = ridge_ev(x_tr, y_tr, x_va, y_va, lam)
        out[tag] = row
    return out


def _std_ridge_ev(x_train: np.ndarray, y_train: np.ndarray, x_val: np.ndarray,
                  y_val: np.ndarray, lam: float = 1.0) -> float:
    """**先标准化再岭回归**的解释方差（读出头探针专用）。

    为什么不能直接用 `ridge_ev`：探针要比的是**宽度差一个数量级**的特征块（192 vs 576），
    而 `lam=1e-3` 是在**未标准化**的原始尺度上惩罚的 —— 谁的量纲大谁被压得多，
    比较就不公平了。这里用 train 的均值/标准差把每列拉齐，惩罚才是可比的。
    """
    mu = x_train.mean(axis=0)
    sd = x_train.std(axis=0)
    sd = np.where(sd < 1e-8, 1.0, sd)
    a = np.concatenate([(x_train - mu) / sd, np.ones((x_train.shape[0], 1))], axis=1)
    b = np.concatenate([(x_val - mu) / sd, np.ones((x_val.shape[0], 1))], axis=1)
    w = np.linalg.solve(a.T @ a + lam * np.eye(a.shape[1]), a.T @ y_train)
    return explained_variance(b @ w, y_val)


def _mlp_probe(x_train: np.ndarray, y_train: np.ndarray, x_val: np.ndarray,
               y_val: np.ndarray, *, hidden: int = 256, steps: int = 1500,
               lr: float = 1e-3, batch: int = 4096, seed: int = 20260927,
               device: str = "cpu") -> float:
    """**非线性探针**：同一份冻结特征喂两层 MLP，看"深度"能不能单独把 EV 抬起来。

    与 `_std_ridge_ev` 配对读：`MLP ≫ ridge` ⇒ 瓶颈是**读出头太浅**；
    `MLP ≈ ridge` ⇒ 瓶颈在**特征本身**（再多层也白搭）。
    """
    torch.manual_seed(seed)
    mu = x_train.mean(axis=0)
    sd = np.where(x_train.std(axis=0) < 1e-8, 1.0, x_train.std(axis=0))
    xt = torch.from_numpy(((x_train - mu) / sd).astype(np.float32)).to(device)
    yt = torch.from_numpy(y_train.astype(np.float32)).to(device)
    xv = torch.from_numpy(((x_val - mu) / sd).astype(np.float32)).to(device)
    net = torch.nn.Sequential(
        torch.nn.Linear(xt.shape[1], hidden), torch.nn.GELU(),
        torch.nn.Linear(hidden, hidden), torch.nn.GELU(),
        torch.nn.Linear(hidden, 1)).to(device)
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    gen = torch.Generator(device="cpu").manual_seed(seed)
    n = xt.shape[0]
    for _ in range(steps):
        idx = torch.randperm(n, generator=gen)[:batch].to(device)
        opt.zero_grad(set_to_none=True)
        loss = torch.nn.functional.mse_loss(net(xt[idx]).squeeze(-1), yt[idx])
        loss.backward()
        opt.step()
    with torch.no_grad():
        pred = net(xv).squeeze(-1).cpu().numpy().astype(np.float64)
    return explained_variance(pred, y_val)


def _grab(cap: dict, name: str, idx: int | None = None):
    """抓某个子模块的输出到共享的 `cap` 里（hook 必须**返回 None**，否则会把输出替换掉）。"""
    def hook(_m, _inp, out) -> None:
        cap[name] = (out[idx] if idx is not None else out).detach()
    return hook


def pool_features(u: torch.Tensor, mask: torch.Tensor) -> dict[str, torch.Tensor]:
    """把候选表示 `u`（`[B,L,D]`）按几种方式池化成状态向量（读出头探针的**唯一池化实现**）。

    ⚠ `mean_all` 是**现行**读法（`V4Model.forward` 里的 `u.mean(dim=1)`）：它对**全部 L 个槽位**
    求平均，含掩码掉的填充槽 —— 于是状态里混进一个正比于 `(L−合法数)/L` 的常数项。
    其余三种都只在**合法候选**上算（`mean` / `max` / `std`）。
    """
    m = mask if mask is not None else torch.ones(u.shape[:2], dtype=torch.bool, device=u.device)
    w = m.unsqueeze(-1).to(u.dtype)
    cnt = w.sum(dim=1).clamp(min=1.0)
    mean_m = (u * w).sum(dim=1) / cnt
    mx = u.masked_fill(~m.unsqueeze(-1), torch.finfo(u.dtype).min).max(dim=1).values
    var = ((u - mean_m.unsqueeze(1)) ** 2 * w).sum(dim=1) / cnt
    return {"mean_all": u.mean(dim=1), "mean": mean_m, "max": mx,
            "std": var.clamp(min=0).sqrt()}


@torch.no_grad()
def forward_pools(model: M.V4Model, data: dict, n: int, device: str,
                  batch: int = 2048, rows: int = 0) -> dict[str, np.ndarray]:
    """把冻结躯干在**不同池化方式**下的状态向量各取一份，供线性/非线性探针比较。

    动机（`docs/TRAINING-V4.md` §14.7）：现行值头读的是 `u.mean(dim=1)` —— 对**全部 L 个槽位**
    （含掩码掉的填充槽）求平均。这个池化有三个可疑之处，探针要把它们**分开**量：

    - `mean_all` vs `mean`：填充槽（`cand` 全 0 → 一个常数偏置向量）被平均进去，于是状态随
      "本巡有几个合法候选"漂移；
    - `max` / `std`：**均值把信息抹平**（"某一张牌特别危险"这种信号在均值里只剩 1/L）；
    - `tile_pool` / `h_evt`：融合层手上**本来就有**、但最后没进值头的两个向量（代码事实⑥）。
    """
    cap: dict[str, torch.Tensor] = {}
    hooks = [model.tile.register_forward_hook(_grab(cap, "tile_pool", 1)),
             model.event.register_forward_hook(_grab(cap, "h_evt", 1)),
             model.fusion.register_forward_hook(_grab(cap, "u"))]
    try:
        idx_all = (np.linspace(0, n - 1, rows).astype(np.int64) if 0 < rows < n
                   else np.arange(n))
        acc: dict[str, list[np.ndarray]] = {}
        for i0 in range(0, idx_all.shape[0], batch):
            idx = idx_all[i0:i0 + batch]
            b = _batch(data, idx, device)
            model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"])
            u = cap["u"]                                            # [B,L,D]
            blocks = {**pool_features(u, b["mask"]),
                      "tile_pool": cap["tile_pool"], "h_evt": cap["h_evt"]}
            for k, v in blocks.items():
                acc.setdefault(k, []).append(v.float().cpu().numpy())
        return {k: np.concatenate(v, axis=0).astype(np.float64) for k, v in acc.items()}
    finally:
        for h in hooks:
            h.remove()


def _pool_groups(pool: dict[str, np.ndarray]) -> dict[str, list[str]]:
    """探针要比较的**特征块组合**（名字 → 组成块）。"""
    return {
        "mean_all": ["mean_all"],
        "mean": ["mean"],
        "max": ["max"],
        "std": ["std"],
        "state(tile_pool)": ["tile_pool"],
        "h_evt(未用)": ["h_evt"],
        "mean+max+std": ["mean", "max", "std"],
        "mean_all+tile_pool+h_evt": ["mean_all", "tile_pool", "h_evt"],
        "all": ["mean", "max", "std", "tile_pool", "h_evt"],
    }


def readout_report(data_dir: str | Path, split: str, ckpt: str, *,
                   targets: tuple[str, ...] = ("delta", "value", "rtg"),
                   train_rows: int = 40000, val_rows: int = 20000,
                   lam: float = 1.0, mlp: bool = True, mlp_steps: int = 1500,
                   device: str | None = None, batch: int = 2048) -> dict:
    """**读出头探针**：同一份冻结躯干，换池化方式 / 换读出深度，看 EV 能到多少。

    判据（`docs/TRAINING-V4.md` §14.7 的下一步）：
    - 某个池化块的 ridge EV ≫ `mean_all` ⇒ **瓶颈是池化**（值头读错了向量）；
    - MLP ≫ ridge ⇒ **瓶颈是读出深度**；
    - 两者都 ≈ `mean_all` ⇒ 瓶颈在**躯干表示**（那就要动特征/躯干，不是动头）。
    """
    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    tr = ds.load_split(data_dir, "train")
    va = ds.load_split(data_dir, split)
    model = load_model(ckpt, dev)
    ptr = forward_pools(model, tr, int(tr["nlegal"].shape[0]), dev, batch, train_rows)
    pva = forward_pools(model, va, int(va["nlegal"].shape[0]), dev, batch, val_rows)
    n_tr = next(iter(ptr.values())).shape[0]
    n_va = next(iter(pva.values())).shape[0]
    i_tr = (np.linspace(0, int(tr["nlegal"].shape[0]) - 1, n_tr).astype(np.int64)
            if n_tr < int(tr["nlegal"].shape[0]) else np.arange(n_tr))
    i_va = (np.linspace(0, int(va["nlegal"].shape[0]) - 1, n_va).astype(np.int64)
            if n_va < int(va["nlegal"].shape[0]) else np.arange(n_va))
    groups = _pool_groups(ptr)
    out: dict = {"ckpt": str(ckpt), "split": split, "train_rows": n_tr, "val_rows": n_va,
                 "blocks": {k: int(v.shape[1]) for k, v in ptr.items()}, "ridge": {}, "mlp": {}}
    for tgt in targets:
        if tr.get(tgt) is None:
            continue
        y_tr = _target_of(tr, tgt, int(tr["nlegal"].shape[0]))[0][i_tr]
        y_va = _target_of(va, tgt, int(va["nlegal"].shape[0]))[0][i_va]
        if not np.isfinite(y_tr).all() or not np.isfinite(y_va).all():
            continue
        row: dict[str, float] = {}
        for name, parts in groups.items():
            row[name] = _std_ridge_ev(np.concatenate([ptr[p][:, :] for p in parts], axis=1), y_tr,
                                      np.concatenate([pva[p][:, :] for p in parts], axis=1), y_va, lam)
        out["ridge"][tgt] = row
        if mlp:
            best = max(row, key=lambda k: row[k])
            out["mlp"][tgt] = {
                "mean_all": _mlp_probe(ptr["mean_all"], y_tr, pva["mean_all"], y_va,
                                       steps=mlp_steps, device=dev),
                "best_ridge_pool": best,
                "best_ridge_pool_mlp": _mlp_probe(
                    np.concatenate([ptr[p] for p in groups[best]], axis=1), y_tr,
                    np.concatenate([pva[p] for p in groups[best]], axis=1), y_va,
                    steps=mlp_steps, device=dev),
            }
    return out


#: 逐决策 shaping 探针的**势函数**候选（`--shaping`）：名字 → (数据集列, 每单位千点, 方向)
#: ⚠ 只用**决策那一刻公开且自家能算**的量（引擎 `HandEval` 口径）—— 势函数一旦含隐藏信息，
#: shaping 的"不改变最优策略"就不成立了（那等于给策略喂答案）。
PHI_DEFS: dict[str, tuple[str, float, float]] = {
    "shanten": ("aux_own_shanten_after", 1.0, -1.0),   # 向听越小越好 ⇒ Φ = −向听
    "tenpai": ("aux_own_tenpai", 1.0, +1.0),           # 听牌 ⇒ Φ = +1
}


def _phi_of(data: dict, kind: str, n: int, unit: float) -> np.ndarray:
    """按 `PHI_DEFS` 取势函数（**千点量纲**）：`Φ = dir · col · unit`。"""
    key, scale, direction = PHI_DEFS[kind]
    if data.get(key) is None:
        raise SystemExit(f"数据集没有 `{key}` 列（`--phi {kind}` 需要它）")
    col = np.asarray(data[key][:n], dtype=np.float64)
    if col.ndim != 1:
        raise SystemExit(f"势函数列 `{key}` 应当是一维（实际 {col.shape}）")
    return direction * col * scale * unit


def shaping_report(data_dir: str | Path, split: str = "val", *,
                   phi_kind: str = "shanten", thetas: tuple[float, ...] = (0.0,),
                   train_rows: int = 40000, val_rows: int = 20000,
                   lam: float = 1.0) -> dict:
    """**逐决策 shaping 探针**（§14.9）：势函数 shaping 能不能真的把优势噪声降下来。

    判据不是"shaping 目标的 EV 有多高"（那会被**状态已知**的成分刷高—— `ctx.points` 那次的教训），
    而是**绝对量纲**下的残差：`std(y − ŷ) / std(R_centered)`，其中 `ŷ` = 用**合法特征**
    （`legit_full`）在 train 上闭式拟合、val 上预测的最优线性 critic。

    - `θ=0` 那一行就是现状（`y = R`）：实测 ≈0.97（`EV(delta)=0.069` ⇒ √(1−0.069)）；
    - 若某个 `θ>0` 把它压到 0.8 以下 ⇒ **shaping 值得进训练回路**；
    - 若所有 θ 都 ≥0.97 ⇒ 这条路也到顶，别再动 critic。

    注：θ 也可以理解成"每降一向听值多少千点"（`Φ = −θ·向听`）；PBRS 的望远镜相消保证
    θ 取任何值都**不改变最优策略**（自检红证），所以选 θ 就是在选方差。
    """
    tr = ds.load_split(data_dir, "train")
    va = ds.load_split(data_dir, split)
    n_tr_all = int(tr["nlegal"].shape[0])
    n_va_all = int(va["nlegal"].shape[0])
    i_tr = (np.linspace(0, n_tr_all - 1, min(train_rows, n_tr_all)).astype(np.int64))
    i_va = (np.linspace(0, n_va_all - 1, min(val_rows, n_va_all)).astype(np.int64))

    def _slice(data: dict, idx: np.ndarray) -> dict:
        return {k: (None if v is None else np.asarray(v[:int(data["nlegal"].shape[0])])[idx])
                for k, v in data.items() if k not in ("meta", "split")}

    trs, vas = _slice(tr, i_tr), _slice(va, i_va)
    x_tr = _cols_of(trs, CEIL_GROUPS[CEIL_REF_KEY], i_tr.size)
    x_va = _cols_of(vas, CEIL_GROUPS[CEIL_REF_KEY], i_va.size)
    r_tr = np.asarray(trs["delta"], dtype=np.float64) / 1000.0
    r_va = np.asarray(vas["delta"], dtype=np.float64) / 1000.0
    nxt_tr, _, end_tr = v4adv.decision_chain(trs["game"], trs["hand_no"], trs["seat"])
    nxt_va, _, end_va = v4adv.decision_chain(vas["game"], vas["hand_no"], vas["seat"])
    base = float(np.std(r_va - r_va.mean()))
    out: dict = {"phi": phi_kind, "split": split, "rows": [int(i_tr.size), int(i_va.size)],
                 "unit": "千点", "std_R_centered": base, "targets": {}}
    for theta in thetas:
        if theta == 0.0:
            y_tr, y_va = r_tr.copy(), r_va.copy()          # 现状：θ=0 = 原始小局收支
        else:
            # ⚠ 势函数按"千点/向听"取，θ 就是"每降一向听值多少千点"
            p_tr = _phi_of(trs, phi_kind, i_tr.size, 1.0)
            p_va = _phi_of(vas, phi_kind, i_va.size, 1.0)
            y_tr = v4adv.pbrs_return(r_tr, p_tr, nxt_tr, end_tr, theta)
            y_va = v4adv.pbrs_return(r_va, p_va, nxt_va, end_va, theta)
        mu = x_tr.mean(axis=0)
        sd = np.where(x_tr.std(axis=0) < 1e-8, 1.0, x_tr.std(axis=0))
        a = np.concatenate([(x_tr - mu) / sd, np.ones((x_tr.shape[0], 1))], axis=1)
        b = np.concatenate([(x_va - mu) / sd, np.ones((x_va.shape[0], 1))], axis=1)
        w = np.linalg.solve(a.T @ a + lam * np.eye(a.shape[1]), a.T @ y_tr)
        pred = b @ w
        resid = y_va - pred
        out["targets"][f"{theta:g}"] = {
            "theta": float(theta), "std_y": float(np.std(y_va)),
            "legit_ev": float(explained_variance(pred, y_va)),
            "resid_std": float(np.std(resid)),
            "ratio_vs_R": float(np.std(resid) / base) if base > 0 else float("nan"),
        }
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


def shift_distribution(prob: np.ndarray, shift: float) -> np.ndarray:
    """把整个分布沿分箱轴**平移** `shift` 格（线性插值后重新归一化）。

    为什么需要它（不只是温度）：温度只改"宽窄"，改不了"系统性偏高/偏低"。实测温度缩放把 95%
    覆盖率从 0.853 拉到 0.872，但 50%/95% 仍差 5.3/7.9pp —— 剩下那段是**位置**偏差，得靠平移。
    """
    if abs(float(shift)) < 1e-12:
        return np.asarray(prob, dtype=np.float64)
    p = np.asarray(prob, dtype=np.float64)
    idx = np.arange(p.shape[1], dtype=np.float64)
    src = idx - float(shift)                     # 目标格 i 取原分布 src(i) 处（线性插值）
    lo = np.floor(src).astype(np.int64)
    frac = (src - lo)[None, :]
    valid_lo = (lo >= 0) & (lo < p.shape[1])
    valid_hi = (lo + 1 >= 0) & (lo + 1 < p.shape[1])
    lo_c = np.clip(lo, 0, p.shape[1] - 1)
    hi_c = np.clip(lo + 1, 0, p.shape[1] - 1)
    q = p[:, lo_c] * (1.0 - frac) * valid_lo[None, :] + p[:, hi_c] * frac * valid_hi[None, :]
    return q / q.sum(axis=1, keepdims=True)


def fit_calibration(prob: np.ndarray, *, y: np.ndarray | None = None,
                    target: np.ndarray | None = None, lo: float = 0.2, hi: float = 8.0,
                    max_shift: float = 6.0) -> tuple[float, float]:
    """**loc-scale 两参数校准** `(T, b)`：先用温度定宽窄，再平移定位置。

    两维都很便宜（一维网格 × 一维坐标下降），在**训练切分**上拟合、val 上量效果。
    @return `(temp, shift)`（`shift` 单位 = 分箱格；分箱宽 = 60/51 ≈ 1.18 千点）
    """
    p = np.asarray(prob, dtype=np.float64)
    yv = None if y is None else np.asarray(y, dtype=np.float64)
    tg = None if target is None else np.asarray(target, dtype=np.float64)

    def nll(t: float, b: float) -> float:
        q = shift_distribution(apply_temperature(p, t), b)
        lq = np.log(np.clip(q, 1e-12, None))
        if tg is not None:
            return float(-(tg * lq).sum(-1).mean())
        idx = np.clip(np.searchsorted(CENTERS, yv), 0, CENTERS.size - 1)
        return float(-lq[np.arange(lq.shape[0]), idx].mean())

    best = (1.0, 0.0)
    best_v = nll(1.0, 0.0)
    for t in np.linspace(lo, hi, 9):
        for b in np.linspace(-max_shift, max_shift, 13):
            v = nll(float(t), float(b))
            if v < best_v:
                best, best_v = (float(t), float(b)), v
    # 坐标下降细化
    t, b = best
    for _ in range(3):
        for cand in np.linspace(max(lo, t - 0.2), min(hi, t + 0.2), 9):
            v = nll(float(cand), b)
            if v < best_v:
                t, best_v = float(cand), v
        for cand in np.linspace(b - 0.3, b + 0.3, 13):
            v = nll(t, float(cand))
            if v < best_v:
                b, best_v = float(cand), v
    return float(t), float(b)


def apply_calibration(prob: np.ndarray, temp: float, shift: float) -> np.ndarray:
    """`(T, b)` 两参数一起用（顺序：先温度、再平移）。"""
    return shift_distribution(apply_temperature(prob, temp), shift)


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
    """校准的**前后对照**：参数在**训练切分**上拟合（避免"在 val 上拟合再报 val"的乐观偏差），
    覆盖率/CRPS/EV 在 val 上报。返回 `{target, temp, shift, before, after_temp, after}`。

    两档都报：**温度**（一个标量，只改宽窄）与 **loc-scale**（温度 + 平移，位置偏差也能修）。
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
    temp2, shift = fit_calibration(p_tr, y=y_tr)
    p_va = forward_value(model, va, n_va, dev, batch)
    y_va = _target_of(va, target, n_va)[0]
    return {"target": target, "temp": temp, "temp_locscale": temp2, "shift": shift,
            "before": metrics(p_va, y_va),
            "after_temp": metrics(apply_temperature(p_va, temp), y_va),
            "after": metrics(apply_calibration(p_va, temp2, shift), y_va)}


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
    ap.add_argument("--value-key", default="auto", choices=["auto", "value", "rtg", "delta"],
                    help="判据按哪一列的目标算（auto = 该 ckpt 训练时的口径看不出来时用 value）")
    ap.add_argument("--target", default="auto", choices=["auto", "value", "rtg", "delta"],
                    help="**主口径**：auto = 与 ckpt 的 `value_target` 同源；"
                         "P1b 的小局级值头（`--value-target delta`）要用它才量得对")
    ap.add_argument("--out", default=None)
    ap.add_argument("--strict", action="store_true", help="有关键判据不过就返回 2")
    ap.add_argument("--ev-ref", choices=["floor", "legit"], default="floor",
                    help="EV 判据的参照：floor = 绝对门槛 0.1（老口径）；legit = 现算**合法天花板**"
                         "并用 `EV ≥ 0.8 × 天花板`（§14.8.4 —— 绝对门槛对 `delta`/`rtg` 不可达）")
    ap.add_argument("--ceiling", action="store_true",
                    help="另报**引擎真值特征的线性参照**（value / rtg / delta 各能解释多少方差）")
    ap.add_argument("--calibrate", action="store_true",
                    help="另报**温度缩放**前后的覆盖率/CRPS（一个标量，修欠覆盖最便宜的一招）")
    ap.add_argument("--calib-rows", type=int, default=20000,
                    help="拟合温度用训练切分的前多少行（一个标量，两万行足够）")
    ap.add_argument("--readout", action="store_true",
                    help="另报**读出头探针**：冻结躯干换池化方式（mean/max/std/tile_pool/h_evt）"
                         "与换读出深度（ridge vs MLP）时 EV 能到多少 —— 用来判定瓶颈在池化、"
                         "在读出深度、还是在躯干表示")
    ap.add_argument("--readout-rows", type=int, default=40000,
                    help="读出头探针在 train 上抽多少行（val 抽它的 1/2）")
    ap.add_argument("--readout-mlp-steps", type=int, default=1500)
    ap.add_argument("--shaping", action="store_true",
                    help="另报**逐决策 shaping 探针**（§14.9）：势函数（引擎 HandEval）的 "
                         "potential-based shaping 能不能把优势噪声降下来 —— 判据是**绝对量纲**下的"
                         "残差比 `std(y−ŷ)/std(R)`（<1 才值得进训练回路）")
    ap.add_argument("--phi", default="shanten", choices=sorted(PHI_DEFS),
                    help="势函数：shanten（−向听）/ tenpai（是否听牌）")
    ap.add_argument("--shaping-thetas", default="0,0.5,1,2,4,8",
                    help="shaping 权重扫描（千点/每单位势函数；逗号分隔。θ=0 = 现状对照）")
    ap.add_argument("--shaping-rows", type=int, default=40000,
                    help="shaping 探针在 train 上抽多少行（val 抽它的一半）")
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
        print(f"引擎真值特征的线性参照（在 train 上闭式拟合、在 {args.split} 上量 EV）："
              f"**判据只读 `legit` 行**")
        for tag, row in ceil.items():
            note = ""
            if tag.startswith("label_"):
                note = "   ⚠ 作弊上界（含本小局结局 / 别家隐藏真值，推理端拿不到）"
            elif tag == "legit":
                note = "   ← 合法天花板（决策那一刻自家能算的）"
            print(f"  {tag:<20}features={int(row.get('features', 0)):>3}  "
                  + "  ".join(f"EV({k[3:]})={v:+.4f}" for k, v in row.items()
                              if k.startswith("ev_")) + note)

    if args.shaping:
        thetas = tuple(float(x) for x in str(args.shaping_thetas).split(",") if x.strip())
        rep = shaping_report(args.data, args.split, phi_kind=args.phi, thetas=thetas,
                             train_rows=args.shaping_rows,
                             val_rows=max(1000, args.shaping_rows // 2))
        print(f"\n逐决策 shaping 探针（势函数 `{args.phi}`，train {rep['rows'][0]} 行 / "
              f"{args.split} {rep['rows'][1]} 行；`std(R_c)` = {rep['std_R_centered']:.3f} 千点）")
        print(f"  {'θ':>6}  {'std(y)':>8}  {'合法EV':>8}  {'残差 std':>9}  {'残差/现状':>9}   判据")
        for k, row in rep["targets"].items():
            tag = "现状（θ=0）" if row["theta"] == 0.0 else (
                "✅ 值得进训练回路" if row["ratio_vs_R"] < 0.8 else
                ("≈ 没差别" if row["ratio_vs_R"] >= 0.97 else "略有改善"))
            print(f"  {row['theta']:>6g}  {row['std_y']:>8.3f}  {row['legit_ev']:>+8.4f}  "
                  f"{row['resid_std']:>9.3f}  {row['ratio_vs_R']:>9.3f}   {tag}")
        if args.out:
            p = Path(args.out)
            p.with_name(p.stem + ".shaping" + p.suffix).write_text(
                json.dumps({"shaping": rep}, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.readout:
        for ck in args.ckpt:
            rep = readout_report(args.data, args.split, ck, train_rows=args.readout_rows,
                                 val_rows=max(1000, args.readout_rows // 2),
                                 mlp_steps=args.readout_mlp_steps,
                                 device=args.device, batch=args.batch)
            print(f"\n读出头探针（{Path(ck).name}；冻结躯干，train {rep['train_rows']} 行 / "
                  f"{rep['split']} {rep['val_rows']} 行；块宽度 {rep['blocks']}）")
            for tgt, row in rep["ridge"].items():
                best = max(row, key=lambda k: row[k])
                print(f"  口径 {tgt}（对同一份冻结表示做标准化岭回归，在 val 上量 EV）：")
                for name, ev in sorted(row.items(), key=lambda kv: -kv[1]):
                    mark = "   ← 最好" if name == best else ("   ← 现行读法" if name == "mean_all" else "")
                    print(f"    {name:<28}{ev:+.4f}{mark}")
                m = rep.get("mlp", {}).get(tgt)
                if m:
                    print(f"    非线性（2×256 MLP，{args.readout_mlp_steps} 步）："
                          f"mean_all {m['mean_all']:+.4f} · "
                          f"{m['best_ridge_pool']} {m['best_ridge_pool_mlp']:+.4f}")
            if args.out:
                p = Path(args.out)
                p.with_name(p.stem + ".readout" + p.suffix).write_text(
                    json.dumps({"readout": rep}, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.calibrate:
        for ck in args.ckpt:
            rep = calibration_report(args.data, args.split, ck, target=_native_target(ck),
                                     rows=args.calib_rows, device=args.device, batch=args.batch)
            b, at, a = rep["before"], rep["after_temp"], rep["after"]
            print(f"\n值头校准（{Path(ck).name}，目标 {rep['target']}，参数在 train 前 "
                  f"{args.calib_rows} 行上拟合）：温度 T = {rep['temp']:.3f}；"
                  f"loc-scale T = {rep['temp_locscale']:.3f} + 平移 {rep['shift']:+.2f} 格")
            for k in ("crps", "cov0.5", "cov0.8", "cov0.95", "wid0.95", "ev"):
                mark = "   ← 覆盖率达标（≤3pp）" if k.startswith("cov") \
                    and abs(a[k] - float(k[3:])) <= COVERAGE_TOL else ""
                print(f"   {k:<9}前 {b[k]:+.4f} → 温度 {at[k]:+.4f} → loc-scale {a[k]:+.4f}{mark}")

    report = audit(args.data, args.split, args.ckpt, device=args.device, batch=args.batch,
                   target=args.target, ev_ref=args.ev_ref)
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
            # 主口径优先（`--target delta` 时判据就该按 delta 读，而不是 ckpt 的 value_target）
            key = str(row.get("target") or row.get("value_target", "value"))
        if "ev_ref" in row:
            print(f"   合法天花板（`legit_full` 92 列，同口径现算）EV = {row['ev_ref']:.4f}"
                  f" ⇒ 判据线 {row['ev_need']:.4f}")
        print(f"   ---- 判据（口径：{key}）----")
        for passed, text in verdicts(row, key,
                                     ev_ref=row.get("ev_ref") if args.ev_ref == "legit" else None):
            print(f"   [{'ok  ' if passed else 'FAIL'}] {text}")
            bad = bad or not passed
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写 {args.out}")
    return 2 if (args.strict and bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
