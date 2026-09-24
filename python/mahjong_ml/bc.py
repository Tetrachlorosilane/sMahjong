"""P1 行为克隆（`docs/TRAINING.md` §4 P1）：以 teacher 的轨迹为标签，训一个候选打分网络。

    python -m mahjong_ml.bc --data S:\\mahjong-training\\compact\\bc-001 \\
                            --out S:\\mahjong-training\\ckpt\\bc-001 --epochs 3

要守住的东西：
  · **三条硬约束**（§0.1）：`guard.apply_cpu_limit` 封线程、`DutyCycle` 压 GPU、checkpoint 落 S 盘
    （`paths.allocate("ckpt", …)`，配额闸门在它里面）；
  · **可复现**：索引打乱用固定种子的 `np.random.default_rng`（不用 DataLoader 的多进程），
    同 `--seed` 同数据 → 同指标；
  · **诚实**：验证集是**整场**切出来的（`dataset.py` 负责），指标里同时给"多数类基线"，
    否则 60% 的 top-1 看着挺好、其实是"总打摸切"就能拿到的分。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import torch

from . import dataset as ds
from . import features, guard, nets, paths


def _batch(data: dict, idx: np.ndarray, device: str) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """按索引取一批（从内存映射里切片 → 转 tensor）。

    ⚠ 候选矩阵末尾 `DERIVED_CANDIDATE` 维在磁盘上是**原始整数**（uint8，见 `dataset.py`），
    这里除以单点定义的系数归一化 —— 与 `features.DERIVED_SCALE_CAND` 同一份，不许各写一份。
    """
    state = torch.from_numpy(np.ascontiguousarray(data["state"][idx], dtype=np.float32)).to(device)
    raw = np.ascontiguousarray(data["cand"][idx], dtype=np.float32)
    d = features.DERIVED_CANDIDATE
    if d:
        raw[..., -d:] /= np.asarray(features.DERIVED_SCALE_CAND, dtype=np.float32)
    cand = torch.from_numpy(raw).to(device)
    n_legal = data["n_legal"][idx].astype(np.int64)
    L = cand.size(1)
    mask = torch.from_numpy(np.arange(L)[None, :] < n_legal[:, None]).to(device)
    label = torch.from_numpy(np.ascontiguousarray(data["label"][idx], dtype=np.int64)).to(device)
    return state, cand, mask, label


def evaluate(model: nets.CandidateScorer, data: dict, device: str, batch: int = 8192) -> dict:
    """验证集指标 —— **粒度要说清楚**，否则数字会骗人：

    · `top1`：与 teacher 选**同一个动作**（精确到牌码/取法）的比例 —— 这是主指标，随机猜 ≈ 1/legal；
    · `top1_type`：**动作类型**（打牌/吃/碰/荣和…）判对的比例 —— 与"多数类型基线"同粒度才可比；
    · `first_legal_acc`：**总是选第一个合法动作**的基线（同粒度，最朴素但真实的参照）；
    · `majority_type_acc`：**总是猜最常见的动作类型**的基线；
    · `uniform_nll`：在 legal 上均匀猜测的平均负对数似然（`ln(legal)` 的均值）。
    """
    model.eval()
    n = data["state"].shape[0]
    correct = type_correct = first_legal = 0.0
    nll = 0.0
    legal_sum = 0.0
    kinds: dict[int, int] = {}
    with torch.no_grad():
        for start in range(0, n, batch):
            idx = np.arange(start, min(start + batch, n))
            state, cand, mask, label = _batch(data, idx, device)
            logits = model(state, cand, mask)
            nll += float(torch.nn.functional.cross_entropy(logits, label, reduction="sum"))
            pred = logits.argmax(dim=1)
            correct += float((pred == label).sum())
            first_legal += float((label == 0).sum())
            legal_sum += float(mask.sum())
            cdim = cand.size(-1)
            chosen_type = cand.gather(1, label[:, None, None].expand(-1, 1, cdim))[:, 0, :9].argmax(1)
            pred_type = cand.gather(1, pred[:, None, None].expand(-1, 1, cdim))[:, 0, :9].argmax(1)
            type_correct += float((pred_type == chosen_type).sum())
            for v in chosen_type.cpu().numpy():
                kinds[int(v)] = kinds.get(int(v), 0) + 1
    n = max(1, n)
    counts = [kinds[k] for k in sorted(kinds)]
    return {
        "n": int(n),
        "top1": correct / n,
        "top1_type": type_correct / n,
        "first_legal_acc": first_legal / n,
        "majority_type_acc": (max(counts) / n) if counts else 0.0,
        "nll": nll / n,
        "mean_legal": legal_sum / n,
        "uniform_nll": float(np.log(max(1.0, legal_sum / n))),
        "label_types": {features.ACTION_TYPES[k]: v for k, v in sorted(kinds.items())},
    }


def predict_rows(model: nets.CandidateScorer, data: dict, device: str,
                 batch: int = 8192) -> dict:
    """逐行预测（= `evaluate` 的原料）——给**按场聚类**的显著性检验用。

    为什么不能只留聚合数：同一场的决策高度相关，逐行 bootstrap 会把置信区间缩得过窄
    （DAgger 对比是**配对**的，但"配对了"不等于"独立了"）。`game` 列就在数据里，按它聚类即可。
    """
    model.eval()
    n = data["state"].shape[0]
    pred = np.empty(n, dtype=np.int64)
    correct = np.zeros(n, dtype=bool)
    with torch.no_grad():
        for start in range(0, n, batch):
            idx = np.arange(start, min(start + batch, n))
            state, cand, mask, label = _batch(data, idx, device)
            p = model(state, cand, mask).argmax(dim=1)
            pred[start:start + idx.size] = p.cpu().numpy()
            correct[start:start + idx.size] = (p == label).cpu().numpy()
    return {"pred": pred, "label": np.asarray(data["label"], dtype=np.int64),
            "correct": correct, "game": np.asarray(data["game"], dtype=np.int64),
            "n_legal": np.asarray(data["n_legal"], dtype=np.int64)}


def train(args) -> dict:
    paths.ensure_root()
    meta = json.loads((Path(args.data) / "meta.json").read_text(encoding="utf-8"))
    if meta["feature_version"] != features.FEATURE_VERSION:
        raise ValueError(f"数据集特征版本 {meta['feature_version']} != 代码 {features.FEATURE_VERSION}")
    train_data = ds.load_split(args.data, "train")
    val_data = ds.load_split(args.data, "val")

    threads = guard.apply_cpu_limit(args.threads)
    device = "cuda" if (args.device == "auto" and torch.cuda.is_available()) else \
             ("cpu" if args.device == "auto" else args.device)
    torch.manual_seed(args.seed)
    model = nets.build(features.state_dim(), features.cand_dim(),
                       hidden=args.hidden, head=args.head).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    mon = guard.GpuMonitor() if device == "cuda" else None
    dc = guard.DutyCycle(mon) if mon else None

    n = train_data["state"].shape[0]
    steps_per_epoch = max(1, min(args.max_steps or 10 ** 9, n // args.batch))
    print(f"数据：训练 {n} 条（{meta['train_files'].__len__()} 场）/ 验证 "
          f"{val_data['state'].shape[0]} 条；特征 v{features.FEATURE_VERSION}；"
          f"每条 {features.state_dim()}+{features.cand_dim()}×legal")
    print(f"模型：{model.n_params()} 参数（hidden={args.hidden}, head={args.head}）"
          f"；device={device}；torch_threads={threads}；seed={args.seed}")
    print(f"每 epoch {steps_per_epoch} 步 × batch {args.batch}")

    history = []
    t0 = time.perf_counter()
    for epoch in range(1, args.epochs + 1):
        rng = np.random.default_rng(args.seed + epoch)      # 每 epoch 固定 → 可复现
        perm = rng.permutation(n)
        model.train()
        run_loss, run_acc, seen = 0.0, 0.0, 0
        for step in range(steps_per_epoch):
            idx = np.sort(perm[step * args.batch:(step + 1) * args.batch])
            if idx.size == 0:
                break
            state, cand, mask, label = _batch(train_data, idx, device)
            logits = model(state, cand, mask)
            loss = torch.nn.functional.cross_entropy(logits, label)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            if dc:
                dc.tick()                                   # 约束②：把 GPU 时间平均压到 ≤80%
            run_loss += loss.detach().item() * idx.size
            run_acc += float((logits.argmax(dim=1) == label).sum())
            seen += idx.size
        ev = evaluate(model, val_data, device, batch=args.batch)
        row = {"epoch": epoch, "train_loss": run_loss / max(1, seen),
               "train_top1": run_acc / max(1, seen), **{f"val_{k}": v for k, v in ev.items()}}
        history.append(row)
        print(f"epoch {epoch:>3}: train loss {row['train_loss']:.4f} top1 {row['train_top1']:.3f} | "
              f"val top1 {ev['top1']:.3f}（类型 {ev['top1_type']:.3f}） nll {ev['nll']:.4f} | "
              f"基线：首合法 {ev['first_legal_acc']:.3f} / 多数类型 {ev['majority_type_acc']:.3f} / "
              f"均匀 nll {ev['uniform_nll']:.3f}")
    if mon:
        mon.close()
    wall = time.perf_counter() - t0

    out = paths.allocate("ckpt", args.label, need_bytes=8 * 1024**2 * max(1, args.epochs))
    torch.save({"model": model.state_dict(),
                "config": {"state_dim": features.state_dim(), "cand_dim": features.cand_dim(),
                           "hidden": args.hidden, "head": args.head},
                "feature_version": features.FEATURE_VERSION}, out / "model.pt")
    result = {
        "label": args.label, "data": str(args.data), "data_meta": meta,
        "model": {"params": model.n_params(), "hidden": args.hidden, "head": args.head},
        "args": {k: v for k, v in vars(args).items()},
        "device": device, "torch_threads": threads, "seed": args.seed,
        "epochs": args.epochs, "batch": args.batch, "steps_per_epoch": steps_per_epoch,
        "wall_seconds": wall, "history": history,
        "final_val_top1": history[-1]["val_top1"] if history else None,
    }
    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    print(f"\ncheckpoint：{out / 'model.pt'}；指标：{out / 'metrics.json'}（{wall:.1f}s）")
    last = history[-1]
    print(f"最终 val top-1 = {last['val_top1']:.3f}（精确动作；随机 {1/last['val_mean_legal']:.3f}）；"
          f"类型 {last['val_top1_type']:.3f}（基线：首合法 {last['val_first_legal_acc']:.3f} / "
          f"多数类型 {last['val_majority_type_acc']:.3f}）")
    return result


def eval_ckpt(data_dir: str, ckpt: str, split: str = "val", device_pref: str = "auto",
              batch: int = 8192) -> dict:
    """在**任意数据集**上评一个已有 checkpoint（DAgger 对比就靠它：同一批学生状态，两个模型）。"""
    guard.apply_cpu_limit()
    device = ("cuda" if (device_pref == "auto" and torch.cuda.is_available()) else
              ("cpu" if device_pref == "auto" else device_pref))
    ck = torch.load(ckpt, map_location="cpu", weights_only=False)
    cfg = ck["config"]
    if cfg["state_dim"] != features.state_dim() or cfg["cand_dim"] != features.cand_dim():
        raise ValueError(f"checkpoint 的特征维度 ({cfg['state_dim']},{cfg['cand_dim']}) != 代码 "
                         f"({features.state_dim()},{features.cand_dim()})")
    model = nets.build(cfg["state_dim"], cfg["cand_dim"], hidden=cfg["hidden"], head=cfg["head"])
    model.load_state_dict(ck["model"])
    model.to(device)
    data = ds.load_split(data_dir, split)
    ev = evaluate(model, data, device, batch=batch)
    ev["ckpt"] = ckpt
    ev["data"] = data_dir
    ev["split"] = split
    ev["feature_version"] = data["meta"]["feature_version"]
    ev["device"] = device
    return ev


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P1 行为克隆（teacher 轨迹 → 候选打分网络）")
    ap.add_argument("cmd", nargs="?", default="train", choices=["train", "eval"])
    ap.add_argument("--data", required=True, help="紧凑数据集目录（dataset.py build 的产物）")
    ap.add_argument("--ckpt", default=None, help="eval 模式：要评的 model.pt")
    ap.add_argument("--split", default="val", choices=["train", "val"], help="eval 模式评哪个切分")
    ap.add_argument("--out", default=None, help="（保留）checkpoint 目录；缺省由 paths 按 label 分配")
    ap.add_argument("--label", default="bc-smoke", help="checkpoint 子目录名（S 盘 ckpt/ 下）")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=4096)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--head", type=int, default=128)
    ap.add_argument("--threads", type=int, default=guard.TRAIN_THREADS)
    ap.add_argument("--max-steps", type=int, default=None, help="每 epoch 最多多少步（冒烟用）")
    ap.add_argument("--seed", type=int, default=20260101)
    ap.add_argument("--device", default="auto")
    args = ap.parse_args(argv)

    if args.cmd == "eval":
        if not args.ckpt:
            ap.error("eval 模式需要 --ckpt")
        ev = eval_ckpt(args.data, args.ckpt, args.split, args.device, batch=args.batch)
        print(json.dumps(ev, ensure_ascii=False, indent=2))
        print(f"\n{args.ckpt} 在 {args.data}[{args.split}] 上："
              f"top-1 {ev['top1']:.3f}（类型 {ev['top1_type']:.3f}）n={ev['n']}"
              f"　基线：首合法 {ev['first_legal_acc']:.3f} / 多数类型 {ev['majority_type_acc']:.3f}")
        return 0
    return 0 if train(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
