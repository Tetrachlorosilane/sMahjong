"""P3 · 判据②：**优势加权行为克隆（AWR）** —— 用学到的价值去改策略。

    # ① 先训 critic（offline_rl.py），② 再用它加权重训策略：
    python -m mahjong_ml.awr --critic S:\\…\\ckpt\\iql-001\\model.pt \\
        --data S:\\…\\compact\\rl-001 --init S:\\…\\ckpt\\bc-003\\model.pt --label awr-001 --beta 3

## 口径（AWR = 用数据里那个动作的优势给它加权，**不查数据外的动作**）

    A(s,a) = Q(s,a) − V(s)                      # 冻结的 IQL critic 给
    w      = clip(exp(β·A), 0, w_max)           # 好动作权重大
    loss   = − Σ w·log π(a|s) / Σ w  +  λ·CE(π, a)

- **为什么能改策略**：BC 把"老师怎么做"当成唯一正确答案；AWR 允许**在这一小局的结果比预期差**的
  决策上降权（那些决策往往是"看着合理但结果坏"的），于是策略从**结果**里学一点东西。
- ⚠ **必须带行为锚定项**（`--bc-anchor` λ>0）：只用 AWR 时，权重集中到少数样本上会把策略拽飞
  （离线 RL 的经典失效模式），λ·CE 把它拉回行为策略附近。这也是"最坏情况不退化"的实践版。
- ⚠ **有效样本量（ESS = (Σw)²/Σw²）必须打出来**：β 太大时 ESS 塌成几百条，那意味着"训练集里
  只有极少数决策在说话" —— 这种模型就算指标好看也不可信。
- ⚠ **本轮的价值只有小局收支**（`rewards.py` 的口径），没有顺位项 ⇒ 策略优化的是"这一小局多赚点"，
  不直接优化名次。所以**离线指标（与老师一致率）不是判据**，判据只有同牌山配对实战（`eval.py`）。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from . import bc, dataset as ds, features, guard, nets, paths, rewards


def _batch(split: dict, idx: np.ndarray, device: str):
    state, cand, mask, label = bc._batch(split, idx, device)
    return state, cand, mask, label


@torch.no_grad()
def advantages(critic: nets.CriticScorer, split: dict, idx: np.ndarray, device: str) -> np.ndarray:
    """`A(s,a) = Q(s,a) − V(s)`（千点单位）—— 只对**数据里那个动作**算。

    ⚠ 这里**不需要** `rank_weight`：优势来自 critic 的 Q/V，而 critic 是用哪种奖励口径训的
    （只有小局收支 / 再加整场顺位点）已经**烘进权重里**。但**给 critic 的 `--rank-weight` 必须与
    评测的目标一致** —— 拿"只优化小局收支"的 critic 去改一个要被名次评判的策略，就是拿错指南针走路。
    """
    state, cand, mask, label = _batch(split, idx, device)
    q = critic.q_values(state, cand, mask).gather(1, label[:, None]).squeeze(1)
    v = critic.v_values(state)
    return (q - v).float().cpu().numpy()


def weights_from_adv(adv: np.ndarray, beta: float, w_max: float) -> np.ndarray:
    """`w = clip(exp(β·A), 0, w_max)`（千点单位的优势）。

    ⚠ 实现细节：**先把指数夹在 `ln(w_max)` 再 exp**，而不是先 exp 再 clip —— 后者在
    β·A 很大时会先溢出成 inf；而"先减去最大值再 exp"那种写法会让最大权重恒等于 1，
    `w_max` **永远绑不上**（一个绑不上的旋钮比没有更坏：它让人以为设了上限）。
    ⚠ 因为 AWR 的损失按 `Σw` 归一化，权重的**整体尺度不影响梯度**，所以这里不做 max 归一化。
    """
    z = np.clip(beta * np.asarray(adv, dtype=np.float64), -60.0, float(np.log(max(w_max, 1e-9))))
    return np.exp(z)


def ess(w: np.ndarray) -> float:
    """有效样本量：`(Σw)² / Σw²`（权重集中度；塌到几百就说明 β 太大）。"""
    w = np.asarray(w, dtype=np.float64)
    s = float((w.sum()) ** 2)
    s2 = float((w ** 2).sum())
    return 0.0 if s2 <= 0 else s / s2


def train(args) -> dict:
    paths.ensure_root()
    train_data = ds.load_split(args.data, "train")
    val_data = ds.load_split(args.data, "val")
    threads = guard.apply_cpu_limit(args.threads)
    device = "cuda" if (args.device == "auto" and torch.cuda.is_available()) else \
             ("cpu" if args.device == "auto" else args.device)

    ck = torch.load(args.critic, map_location="cpu", weights_only=False)
    cfg = ck["config"]
    critic = nets.build_critic(cfg["state_dim"], cfg["cand_dim"], hidden=cfg["hidden"],
                               head=cfg["head"])
    critic.load_state_dict(ck["model"])
    critic.to(device).eval()
    for p in critic.parameters():
        p.requires_grad_(False)

    meta = json.loads((Path(args.data) / "meta.json").read_text(encoding="utf-8"))
    torch.manual_seed(args.seed)
    policy = nets.build(features.state_dim(), features.cand_dim(),
                        hidden=args.hidden, head=args.head).to(device)
    if args.init:
        init = torch.load(args.init, map_location="cpu", weights_only=False)
        policy.load_state_dict(init["model"])
        print(f"策略初始化自 {args.init}")
    opt = torch.optim.Adam(policy.parameters(), lr=args.lr)
    mon = guard.GpuMonitor() if device == "cuda" else None
    dc = guard.DutyCycle(mon) if mon else None

    n = int(train_data["state"].shape[0])
    steps = max(1, min(args.max_steps or 10 ** 9, n // args.batch))
    print(f"数据：训练 {n} 条 / 验证 {int(val_data['state'].shape[0])} 条；"
          f"AWR β={args.beta} w_max={args.w_max} 行为锚定 λ={args.bc_anchor}")
    print(f"模型 {policy.n_params()} 参数；每 epoch {steps} 步 × batch {args.batch}；device={device}")

    history = []
    t0 = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        rng = np.random.default_rng(args.seed + epoch)
        perm = rng.permutation(n)
        policy.train()
        run_loss = run_bc = run_w = run_ess = run_acc = 0.0
        for step in range(steps):
            idx = np.sort(perm[step * args.batch:(step + 1) * args.batch])
            adv = advantages(critic, train_data, idx, device)
            if args.adv_scale == "std":
                # 按**批内标准差**归一 → β 变成"几个标准差"的可解释温度；
                # 不归一的话 β 的含义随 critic 的尺度漂（本轮带顺位点项后 adv_std 从 0.6 涨到 ~1.1，
                # 同一个 β=3 就突然变得激进得多）。
                adv = adv / (float(adv.std()) + 1e-6)
            w = weights_from_adv(adv, args.beta, args.w_max)
            state, cand, mask, label = _batch(train_data, idx, device)
            logits = policy(state, cand, mask)
            ce = F.cross_entropy(logits, label, reduction="none")
            wt = torch.from_numpy(w.astype(np.float32)).to(device)
            awr = (wt * ce).sum() / wt.sum().clamp_min(1e-9)
            anchor = ce.mean()
            loss = awr + args.bc_anchor * anchor
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            if dc:
                dc.tick()
            run_loss += float(loss.detach()) * idx.size
            run_bc += float(anchor.detach()) * idx.size
            run_w += float(w.mean()) * idx.size
            run_ess += ess(w) * idx.size
            run_acc += float((logits.argmax(dim=1) == label).sum())
        row = {"epoch": epoch, "loss": run_loss / n if n else 0.0, "bc_ce": run_bc / max(1, n),
               "weight_mean": run_w / max(1, n), "ess": run_ess / max(1, n),
               "train_top1": run_acc / max(1, n)}
        if epoch == 1 or epoch % args.eval_every == 0 or epoch == args.epochs:
            ev = bc.evaluate(policy, val_data, device, batch=args.batch)
            row["val"] = {k: ev[k] for k in ("top1", "top1_type", "nll", "first_legal_acc",
                                             "majority_type_acc")}
            print(f"epoch {epoch:>3}: loss {row['loss']:.4f}（BC 项 {row['bc_ce']:.4f}）"
                  f" w̄ {row['weight_mean']:.3f} ESS {row['ess']:.0f} | "
                  f"val top1 {ev['top1']:.4f}（BC 基线看 §4 P1）")
        else:
            print(f"epoch {epoch:>3}: loss {row['loss']:.4f}（BC 项 {row['bc_ce']:.4f}）"
                  f" w̄ {row['weight_mean']:.3f} ESS {row['ess']:.0f}")
        history.append(row)
    if mon:
        mon.close()
    wall = time.perf_counter() - t0

    out = paths.allocate("ckpt", args.label, need_bytes=8 * 1024**2)
    torch.save({"model": policy.state_dict(),
                "config": {"state_dim": features.state_dim(), "cand_dim": features.cand_dim(),
                           "hidden": args.hidden, "head": args.head},
                "feature_version": features.FEATURE_VERSION,
                "awr": {"beta": args.beta, "w_max": args.w_max, "bc_anchor": args.bc_anchor,
                        "critic": str(args.critic), "init": str(args.init or "")}},
               out / "model.pt")
    result = {"label": args.label, "data": str(args.data), "data_meta": meta,
              "critic": str(args.critic), "init": str(args.init or ""),
              "args": {k: v for k, v in vars(args).items()}, "device": device,
              "torch_threads": threads, "wall_seconds": wall, "history": history,
              "final_val": history[-1].get("val")}
    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(f"\ncheckpoint：{out / 'model.pt'}；指标：{out / 'metrics.json'}（{wall:.1f}s）")
    print("⚠ 离线 top-1 不是判据（AWR 优化的是小局收支，不是「更像老师」）——"
          " 强度只能由同牌山配对实战（mahjong_ml.eval）给出。")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P3 判据②：优势加权 BC（AWR）")
    ap.add_argument("--data", required=True)
    ap.add_argument("--critic", required=True, help="IQL 的 model.pt（冻结，提供 Q/V）")
    ap.add_argument("--init", default=None, help="策略初始化权重（一般给 BC 的 model.pt）")
    ap.add_argument("--label", default="awr-smoke")
    ap.add_argument("--rank-weight", type=float, default=1.0,
                    help="整场顺位点项的权重（**必须与 critic 训练时一致**，否则优势对不上）")
    ap.add_argument("--adv-scale", default="std", choices=["std", "raw"],
                    help="优势归一：std = 除以批内标准差（默认，β 可解释为「几个 σ」）/ raw = 原始千点")
    ap.add_argument("--beta", type=float, default=3.0,
                    help="优势温度：`--adv-scale std` 时单位是**标准差**（3 ≈ 只让 ±3σ 的决策有明显权重）")
    ap.add_argument("--w-max", type=float, default=20.0, help="权重上限（防个别样本统治）")
    ap.add_argument("--bc-anchor", type=float, default=0.2, help="行为锚定 λ（0 = 纯 AWR，会漂）")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch", type=int, default=4096)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--head", type=int, default=128)
    ap.add_argument("--threads", type=int, default=guard.TRAIN_THREADS)
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--eval-every", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20260315)
    ap.add_argument("--device", default="auto")
    args = ap.parse_args(argv)
    return 0 if train(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
