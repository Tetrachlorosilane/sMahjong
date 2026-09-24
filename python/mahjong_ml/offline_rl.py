"""P3 · 离线 RL（IQL）—— 学 `Q(s,a)` / `V(s)`，再用它改策略（`docs/TRAINING.md` §4 P3）。

    # 训练 + 校准（判据①）
    python -m mahjong_ml.offline_rl --data S:\\mahjong-training\\compact\\rl-001 --label iql-001
    # 只看已有 checkpoint 的校准
    python -m mahjong_ml.offline_rl --data … --eval-only --ckpt …\\model.pt

IQL（Implicit Q-Learning）三件套 —— **不用查 OOD 动作**，只用数据里那个动作：

    V(s)  ← expectile_τ( Q̄(s,a) )                       # τ>0.5：偏向"好的那半边"
    Q(s,a)← r + γ · V̄(s')                               # 单动作回归（不必 argmax over actions）
    策略  ← AWR：权重 exp(β·(Q − V))（在 `awr.py` 里做，本文件只管价值）

为什么是 IQL 而不是 CQL：CQL 要在一批**未出现过的动作**上压 Q，而我们的动作空间是**每次询问
现枚举的**（`legal` 里有 `chi:1m+2m` 这种参数化键），"随机采样一个动作"这件事本身没有定义。
IQL 只需要数据里出现过的 (s,a) → 与轨迹格式天然契合。

⚠ 判据①（价值校准）**必须带同粒度基线**：这堆奖励非常稀疏（一小局里大多数座位收支为 0），
所以"预测恒等于 0"和"预测恒等于均值"都是很强的基线 —— 打不过它们就没有资格谈"学到了价值"。
本文件把 `mae_zero` / `mae_const` 与模型的 `mae` 一起打出来，并给分箱可靠性曲线。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from . import dataset as ds, features, guard, nets, paths, rewards


# ------------------------------------------------------------------ 数据

def _batch(split: dict, tr: dict, idx: np.ndarray, device: str):
    """按索引取一批（含**下一步状态**；`nxt < 0` 的用 0 占位，训练时用 `done` 屏蔽）。

    ⚠ 派生候选量与 `bc._batch` 同口径：磁盘上是原始整数，这里除以单点系数归一化。
    """
    nxt = tr["nxt"][idx]
    has_next = nxt >= 0
    safe_next = np.where(has_next, nxt, 0)

    def states(i):
        return torch.from_numpy(np.ascontiguousarray(split["state"][i], dtype=np.float32)).to(device)

    def cands(i):
        raw = np.ascontiguousarray(split["cand"][i], dtype=np.float32)
        d = features.DERIVED_CANDIDATE
        if d:
            raw[..., -d:] /= np.asarray(features.DERIVED_SCALE_CAND, dtype=np.float32)
        return torch.from_numpy(raw).to(device)

    def masks(i):
        n_legal = split["n_legal"][i].astype(np.int64)
        L = split["cand"].shape[1]
        return torch.from_numpy(np.arange(L)[None, :] < n_legal[:, None]).to(device)

    return {
        "state": states(idx), "cand": cands(idx), "mask": masks(idx),
        "act": torch.from_numpy(np.ascontiguousarray(split["label"][idx], dtype=np.int64)).to(device),
        "reward": torch.from_numpy(np.ascontiguousarray(tr["reward"][idx], dtype=np.float32)).to(device),
        "next_state": states(safe_next), "next_cand": cands(safe_next), "next_mask": masks(safe_next),
        "has_next": torch.from_numpy(has_next).to(device),
    }


def iql_loss(critic: nets.CriticScorer, target: nets.CriticScorer, batch: dict, *,
             gamma: float, tau: float) -> tuple[torch.Tensor, dict]:
    """IQL 的两项损失。返回 `(总损失, 诊断量)`。

    · **Q**：`MSE(Q(s,a), r + γ·V̄(s'))`（`s'` 不存在时只用 r）
    · **V**：expectile 回归到 `Q̄(s,a)`（τ>0.5 让 V 追"好的一半"）
    """
    q = critic.q_values(batch["state"], batch["cand"], batch["mask"])
    act = batch["act"].clamp(0, q.size(1) - 1)
    q_sa = q.gather(1, act[:, None]).squeeze(1)

    with torch.no_grad():
        v_next = target.v_values(batch["next_state"])
        v_next = torch.where(batch["has_next"], v_next, torch.zeros_like(v_next))
        q_tgt = batch["reward"] + gamma * v_next
        q_bar = target.q_values(batch["state"], batch["cand"], batch["mask"])
        q_bar_sa = q_bar.gather(1, act[:, None]).squeeze(1)

    q_loss = F.mse_loss(q_sa, q_tgt)

    v = critic.v_values(batch["state"])
    diff = q_bar_sa - v
    w = torch.where(diff > 0, torch.full_like(diff, tau), torch.full_like(diff, 1.0 - tau))
    v_loss = (w * diff.pow(2)).mean()

    with torch.no_grad():
        adv = (q_sa - v)
    return q_loss + v_loss, {"q_loss": float(q_loss.detach()), "v_loss": float(v_loss.detach()),
                             "q_mean": float(q_sa.mean()), "v_mean": float(v.mean()),
                             "adv_mean": float(adv.mean()), "adv_std": float(adv.std())}


@torch.no_grad()
def predict_v(critic: nets.CriticScorer, split: dict, device: str, batch: int = 8192) -> np.ndarray:
    """整份切分的 `V(s)`（内存够：几万条 × float32）。"""
    critic.eval()
    n = int(split["state"].shape[0])
    out = np.empty(n, dtype=np.float32)
    for s in range(0, n, batch):
        idx = np.arange(s, min(s + batch, n))
        st = torch.from_numpy(np.ascontiguousarray(split["state"][idx], dtype=np.float32)).to(device)
        out[s:s + idx.size] = critic.v_values(st).float().cpu().numpy()
    return out


# ------------------------------------------------------------------ 校准（判据①）

def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 2 or float(a.std()) == 0 or float(b.std()) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """秩相关 = 秩的 Pearson（自己算，免得为一个函数引 scipy）。"""
    if a.size < 2:
        return float("nan")
    ra = np.argsort(np.argsort(a)).astype(np.float64)
    rb = np.argsort(np.argsort(b)).astype(np.float64)
    return _pearson(ra, rb)


def calibration(v_pred: np.ndarray, ret: np.ndarray, episode: np.ndarray, *,
                bins: int = 10, const: float | None = None) -> dict:
    """价值校准：模型 `V` 能不能预测"这一小局这家赚/亏多少千点"。

    **必须和两个同粒度基线比**（奖励很稀疏，否则会自欺）：
      · `mae_zero`  ：恒预测 0
      · `mae_const` ：恒预测常数 —— **评估验证集时要把训练集均值传进来**（`const=`），
        否则这个基线自己就吃了验证集的信息，比出来偏乐观。
    另外给**按小局聚合**的版本（一条 episode 一个点）：长 episode 不该主导指标。
    """
    v_pred = np.asarray(v_pred, dtype=np.float64)
    ret = np.asarray(ret, dtype=np.float64)
    ep = np.asarray(episode)
    n = v_pred.size
    res: dict = {"n": int(n), "episodes": int(np.unique(ep).size)}
    err = v_pred - ret
    res["mae"] = float(np.abs(err).mean())
    res["rmse"] = float(np.sqrt((err ** 2).mean()))
    res["pearson"] = _pearson(v_pred, ret)
    res["spearman"] = _spearman(v_pred, ret)
    res["mae_zero"] = float(np.abs(ret).mean())                    # 恒预测 0
    c = float(ret.mean()) if const is None else float(const)
    res["mae_const"] = float(np.abs(ret - c).mean())
    res["const_value"] = c
    res["const_from"] = "val-mean" if const is None else "given(train-mean)"
    # 按 episode 聚合：一条 episode 的均值预测 vs 真值（同一条 episode 的 ret 相同）
    order = np.argsort(ep, kind="stable")
    ep_sorted = ep[order]
    starts = np.flatnonzero(np.r_[True, ep_sorted[1:] != ep_sorted[:-1]])
    ep_mean_pred = np.add.reduceat(v_pred[order], starts) / np.diff(np.r_[starts, n])
    ep_true = ret[order][starts]
    res["ep_mae"] = float(np.abs(ep_mean_pred - ep_true).mean())
    res["ep_pearson"] = _pearson(ep_mean_pred, ep_true)
    res["ep_spearman"] = _spearman(ep_mean_pred, ep_true)
    res["ep_mae_zero"] = float(np.abs(ep_true).mean())
    # 只看"有收支"的小局（占多数的是 0，会把相关性稀释得看不懂）
    nz = ep_true != 0
    res["ep_nonzero_frac"] = float(nz.mean())
    if nz.sum() >= 2:
        res["ep_pearson_nonzero"] = _pearson(ep_mean_pred[nz], ep_true[nz])
        res["ep_mae_nonzero"] = float(np.abs(ep_mean_pred[nz] - ep_true[nz]).mean())
        res["ep_mae_zero_nonzero"] = float(np.abs(ep_true[nz]).mean())
    # 分箱可靠性：按**预测值**分位分箱 → 箱内均值预测 vs 均值真值
    qs = np.quantile(v_pred, np.linspace(0, 1, bins + 1))
    qs[0], qs[-1] = -np.inf, np.inf
    curve = []
    for i in range(bins):
        m = (v_pred > qs[i]) & (v_pred <= qs[i + 1])
        if m.sum() == 0:
            continue
        curve.append({"bin": i, "n": int(m.sum()), "pred": float(v_pred[m].mean()),
                      "actual": float(ret[m].mean())})
    res["reliability"] = curve
    # 判断"有没有资格"
    res["beats_zero"] = bool(res["mae"] < res["mae_zero"])
    res["beats_const"] = bool(res["mae"] < res["mae_const"])
    res["ep_beats_zero"] = bool(res["ep_mae"] < res["ep_mae_zero"])
    return res


def fmt_calibration(c: dict) -> str:
    lines = [
        f"校准（n={c['n']} 条决策 / {c['episodes']} 条 episode）：",
        f"  MAE  {c['mae']:.4f}　RMSE {c['rmse']:.4f}　Pearson {c['pearson']:+.3f}　"
        f"Spearman {c['spearman']:+.3f}",
        f"  同粒度基线：恒预测 0 → MAE {c['mae_zero']:.4f}；恒预测均值({c['const_value']:+.3f}) → "
        f"MAE {c['mae_const']:.4f}",
        f"  → {'**优于**' if c['beats_zero'] else '**打不过**'}恒 0，"
        f"{'**优于**' if c['beats_const'] else '**打不过**'}恒均值",
        f"  按小局聚合：MAE {c['ep_mae']:.4f}（恒 0 {c['ep_mae_zero']:.4f}）　"
        f"Pearson {c['ep_pearson']:+.3f}　Spearman {c['ep_spearman']:+.3f}",
    ]
    if "ep_pearson_nonzero" in c:
        lines.append(f"  只看有收支的小局（占 {c['ep_nonzero_frac']:.1%}）："
                     f"Pearson {c['ep_pearson_nonzero']:+.3f}　"
                     f"MAE {c['ep_mae_nonzero']:.4f}（恒 0 {c['ep_mae_zero_nonzero']:.4f}）")
    if c.get("reliability"):
        # 分箱可靠性：按**预测值**分位分箱 → 箱内"平均预测 vs 平均真值"（对角线 = 完美校准）
        lines.append("  分箱可靠性（预测 → 实际）：" + "　".join(
            f"{b['pred']:+.1f}→{b['actual']:+.1f}" for b in c["reliability"]))
    return "\n".join(lines)


# ------------------------------------------------------------------ 训练

def _score(cal: dict, metric: str) -> float:
    """选择指标名 → `calibration()` 的字段（`val_mae` → `mae`，其余去掉 `val_` 前缀）。"""
    return float(cal["mae"] if metric == "val_mae" else cal[metric[4:]])


def _is_better(cal: dict, metric: str, best: dict) -> bool:
    """`val_mae` 越小越好；相关系数越大越好。

    ⚠ 为什么必须能按相关系数选：MAE 会奖励「缩到均值附近」的保守模型 —— 那种模型 MAE 很漂亮，
    但 `Q−V` 的**排序**全是噪声，AWR 从它身上学不到东西。实测（`iql-003`，带顺位点项）：
    按 MAE 选中的 ep4 小局级 ρ 只有 **+0.20**，而 ρ 最高的 ep40 是 **+0.55**（MAE 反而更差）。
    """
    s = _score(cal, metric)
    if best.get("score") is None:
        return True
    return s < best["score"] if metric == "val_mae" else s > best["score"]


def train(args) -> dict:
    paths.ensure_root()
    meta = json.loads((Path(args.data) / "meta.json").read_text(encoding="utf-8"))
    if meta["feature_version"] != features.FEATURE_VERSION:
        raise ValueError(f"数据集特征版本 {meta['feature_version']} != 代码 {features.FEATURE_VERSION}")
    train_data = ds.load_split(args.data, "train")
    val_data = ds.load_split(args.data, "val")
    tr_train = rewards.transitions(train_data, rank_weight=args.rank_weight)
    tr_val = rewards.transitions(val_data, rank_weight=args.rank_weight)
    print(rewards.describe(tr_train))

    threads = guard.apply_cpu_limit(args.threads)
    device = "cuda" if (args.device == "auto" and torch.cuda.is_available()) else \
             ("cpu" if args.device == "auto" else args.device)
    torch.manual_seed(args.seed)
    critic = nets.build_critic(features.state_dim(), features.cand_dim(),
                               hidden=args.hidden, head=args.head).to(device)
    target = nets.build_critic(features.state_dim(), features.cand_dim(),
                               hidden=args.hidden, head=args.head).to(device)
    target.load_state_dict(critic.state_dict())
    for p in target.parameters():
        p.requires_grad_(False)
    opt = torch.optim.Adam(critic.parameters(), lr=args.lr)
    mon = guard.GpuMonitor() if device == "cuda" else None
    dc = guard.DutyCycle(mon) if mon else None

    n = tr_train["idx"].size
    steps = max(1, min(args.max_steps or 10 ** 9, n // args.batch))
    print(f"数据：训练 {n} 条 / 验证 {tr_val['idx'].size} 条；IQL τ={args.tau} γ={args.gamma}；"
          f"模型 {critic.n_params()} 参数（hidden={args.hidden}, head={args.head}）")
    print(f"每 epoch {steps} 步 × batch {args.batch}；device={device}；torch_threads={threads}")

    history = []
    const_train = float(tr_train["ret"].mean())      # 恒均值基线用**训练集**均值（不吃验证集信息）
    best: dict = {"mae": float("inf"), "epoch": 0, "cal": None, "state": None, "target": None}
    t0 = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        rng = np.random.default_rng(args.seed + epoch)
        perm = rng.permutation(n)
        critic.train()
        run = {"q_loss": 0.0, "v_loss": 0.0, "adv_std": 0.0}
        for step in range(steps):
            idx = np.sort(perm[step * args.batch:(step + 1) * args.batch])
            b = _batch(train_data, tr_train, idx, device)
            loss, diag = iql_loss(critic, target, b, gamma=args.gamma, tau=args.tau)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            with torch.no_grad():                     # Polyak 软更新目标网络
                for pt, pc in zip(target.parameters(), critic.parameters()):
                    pt.mul_(1 - args.polyak).add_(pc, alpha=args.polyak)
            if dc:
                dc.tick()
            for k in run:
                run[k] += diag[k]
        row = {"epoch": epoch, **{k: v / steps for k, v in run.items()}}
        if epoch == 1 or epoch % args.eval_every == 0 or epoch == args.epochs:
            vv = predict_v(critic, val_data, device, batch=args.batch)
            cal = calibration(vv, tr_val["ret"], rewards.keys(val_data), const=const_train)
            row["val"] = {k: cal[k] for k in ("mae", "rmse", "pearson", "spearman", "mae_zero",
                                              "mae_const", "ep_mae", "ep_pearson",
                                              "ep_spearman", "beats_zero", "beats_const")}
            print(f"epoch {epoch:>3}: q {row['q_loss']:.4f} v {row['v_loss']:.4f} "
                  f"adv_std {row['adv_std']:.3f} | val MAE {cal['mae']:.4f}"
                  f"（恒0 {cal['mae_zero']:.4f} / 恒均值 {cal['mae_const']:.4f}）"
                  f" Pearson {cal['pearson']:+.3f} | 小局级 Pearson {cal['ep_pearson']:+.3f}")
            # ⚠ **存"最优 epoch"，不是最后一个**：实测 40 epoch 里验证 MAE 在 ~ep10 见底、
            #   之后一路恶化（3.05 → 3.47），只存最后一轮等于把最差的模型交出去。
            if _is_better(cal, args.select_metric, best):
                best = {"mae": cal["mae"], "score": _score(cal, args.select_metric),
                        "epoch": epoch, "cal": cal,
                        "state": {k: v.detach().cpu().clone()
                                  for k, v in critic.state_dict().items()},
                        "target": {k: v.detach().cpu().clone()
                                   for k, v in target.state_dict().items()}}
        else:
            print(f"epoch {epoch:>3}: q {row['q_loss']:.4f} v {row['v_loss']:.4f} "
                  f"adv_std {row['adv_std']:.3f}")
        history.append(row)
    if mon:
        mon.close()
    wall = time.perf_counter() - t0

    if best["state"] is not None:                     # 回滚到最优 epoch 再做最终校准/落盘
        critic.load_state_dict(best["state"])
        target.load_state_dict(best["target"])
        print(f"\n按 {args.select_metric} 选回最优 epoch {best['epoch']}"
              f"（MAE {best['mae']:.4f}、score {best['score']:.4f}）；"
              f"最后一轮（ep{args.epochs}）不是它")
    vv = predict_v(critic, val_data, device, batch=args.batch)
    cal = calibration(vv, tr_val["ret"], rewards.keys(val_data), const=const_train)
    out = paths.allocate("ckpt", args.label, need_bytes=24 * 1024**2)
    torch.save({"model": critic.state_dict(), "target": target.state_dict(),
                "config": {"state_dim": features.state_dim(), "cand_dim": features.cand_dim(),
                           "hidden": args.hidden, "head": args.head},
                "feature_version": features.FEATURE_VERSION,
                "best_epoch": best["epoch"], "select_metric": args.select_metric,
                "best_score": best["score"],
                "iql": {"tau": args.tau, "gamma": args.gamma, "polyak": args.polyak,
                        "unit": rewards.POINTS_PER_UNIT}}, out / "model.pt")
    result = {"label": args.label, "data": str(args.data), "data_meta": meta,
              "model": {"params": critic.n_params(), "hidden": args.hidden, "head": args.head},
              "args": {k: v for k, v in vars(args).items()}, "device": device,
              "torch_threads": threads, "epochs": args.epochs, "batch": args.batch,
              "steps_per_epoch": steps, "wall_seconds": wall, "history": history,
              "best_epoch": best["epoch"], "select_metric": args.select_metric,
              "best_score": best["score"],
              "train_stats": tr_train["stats"], "val_stats": tr_val["stats"],
              "calibration": cal, "calibration_last_epoch": (history[-1].get("val") or {})}
    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(f"\ncheckpoint：{out / 'model.pt'}（= ep{best['epoch']} 的最优权重）；"
          f"指标：{out / 'metrics.json'}（{wall:.1f}s）")
    print(fmt_calibration(cal))
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P3 离线 RL（IQL）—— 学价值，供 AWR / 替换粗模型")
    ap.add_argument("--data", required=True, help="紧凑数据集目录（**必须带 P3 列**，见 dataset.RL_COLUMNS）")
    ap.add_argument("--label", default="iql-smoke", help="checkpoint 子目录名（S 盘 ckpt/ 下）")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=4096)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--rank-weight", type=float, default=1.0,
                    help="整场顺位点项的权重（千点量纲；1 = 名次为主目标，0 = 只用小局收支）")
    ap.add_argument("--select-metric", default="val_mae",
                    choices=["val_mae", "val_pearson", "val_ep_pearson"],
                    help="按哪个验证指标回滚最优 epoch。⚠ AWR 要的是**排序能力**（相关系数）："
                         "MAE 会奖励缩到均值附近的保守模型，那种模型 Q−V 几乎全 0，AWR 学不到东西")
    ap.add_argument("--tau", type=float, default=0.7, help="IQL 的 expectile（>0.5 偏向好的一半）")
    ap.add_argument("--gamma", type=float, default=1.0, help="折扣（小局收支不折现 → 1.0）")
    ap.add_argument("--polyak", type=float, default=0.005)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--head", type=int, default=128)
    ap.add_argument("--threads", type=int, default=guard.TRAIN_THREADS)
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--eval-every", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20260301)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--eval-only", action="store_true", help="只对已有 checkpoint 做校准")
    ap.add_argument("--ckpt", default=None, help="eval-only 时的 model.pt")
    ap.add_argument("--split", default="val", choices=["train", "val"])
    args = ap.parse_args(argv)

    if args.eval_only:
        if not args.ckpt:
            ap.error("--eval-only 需要 --ckpt")
        guard.apply_cpu_limit(args.threads)
        device = "cuda" if (args.device == "auto" and torch.cuda.is_available()) else \
                 ("cpu" if args.device == "auto" else args.device)
        ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
        cfg = ck["config"]
        critic = nets.build_critic(cfg["state_dim"], cfg["cand_dim"],
                                   hidden=cfg["hidden"], head=cfg["head"])
        critic.load_state_dict(ck["model"])
        critic.to(device)
        data = ds.load_split(args.data, args.split)
        tr = rewards.transitions(data, rank_weight=args.rank_weight)
        v = predict_v(critic, data, device, batch=args.batch)
        # 恒均值基线要用**训练集**均值（评估 train 切分时退回它自己）
        const_train = float(tr["ret"].mean()) if args.split == "train" else \
            float(rewards.transitions(ds.load_split(args.data, "train"))["ret"].mean())
        cal = calibration(v, tr["ret"], rewards.keys(data), const=const_train)
        cal.update({"ckpt": str(args.ckpt), "data": args.data, "split": args.split,
                    "stats": tr["stats"]})
        print(json.dumps(cal, ensure_ascii=False, indent=2))
        print(fmt_calibration(cal))
        return 0
    return 0 if train(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
