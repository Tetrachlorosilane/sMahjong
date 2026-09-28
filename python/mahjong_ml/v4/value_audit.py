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
    args = ap.parse_args(argv)

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
