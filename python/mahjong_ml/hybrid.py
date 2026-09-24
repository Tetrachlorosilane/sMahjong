"""P5b 混合（teacher 先验）的 **α 选择**：离线"让位曲线"（`docs/TRAINING.md` §4 P5b）。

    python -m mahjong_ml.hybrid --ckpt S:\\mahjong-training\\ckpt\\bc-003\\model.pt \\
                                --data S:\\mahjong-training\\compact\\dagger-r1

服务端侧的策略是 `net:<权重文件>@<α>` = `argmax(student(obs) + α · 1[该候选 == 老师动作])`
（`docs/PROTOCOL.md` §8.4）。α 是"多听老师"的旋钮，但**没有一个通用的好值** ——
它取决于学生 logit 的**尺度**：网络训过之后 logit 间距是几个单位，未训练的网络间距接近 0
（实测：golden 小网在 α=0.25 就 100% 让位）。所以要在真模型上量出来。

逐行算 **margin** = (除老师那一条之外的最大 logit) − (老师那一条的 logit)：
`margin < α` ⇔ 这一行会被老师接管，所以曲线 = `P(margin < α)`，**不必真的跑推理**。

⚠ **只能回答"让位多少"，不能回答"是不是更强"**：数据集里唯一的真值就是老师，
一旦让位，按定义就"对了" —— 所以离线一致率随 α 单调升到 1 是**构造出来的**，不是效果。
强弱只能由同牌山配对实战评测（`mahjong_ml.eval`）给出。

自检：`python/selfcheck.py`（margin / 曲线 / 推荐 α 三条纯函数的断言）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import torch

from . import bc, dataset as ds, features, guard, nets

# 默认扫描的 α（logit 单位）。0 = 纯网络（不接管），越大越听老师。
DEFAULT_ALPHAS = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)


def margins_from_logits(logits: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """逐行 margin（"要多大 α 才能让老师那条翻上来"）。

    `logits` 里非法候选已由模型填成 `-inf`（`nets.CandidateScorer` 的掩码），
    所以"除 label 外的最大 logit"天然只在合法候选里取。
    """
    lg = np.array(logits, dtype=np.float64, copy=True)
    n = lg.shape[0]
    if n == 0:
        return np.zeros((0,), dtype=np.float64)
    rows = np.arange(n)
    lab = np.asarray(labels, dtype=np.int64)
    if lab.shape != (n,):
        raise ValueError(f"标签形状 {lab.shape} != ({n},)")
    if np.any(lab < 0) or np.any(lab >= lg.shape[1]):
        raise ValueError("标签越界（不是候选下标）—— 数据集的 label 列应为 chosen/teacher 候选下标")
    own = lg[rows, lab]
    lg[rows, lab] = -np.inf                     # 排除自己那条，取"对手最强"
    rival = lg.max(axis=1)
    return rival - own


def defer_curve(margins: np.ndarray, alphas=DEFAULT_ALPHAS) -> dict[float, float]:
    """α → 被老师接管的行占比（`margin < α`）。α ≤ 0 时为 0（纯网络不动）。"""
    m = np.asarray(margins, dtype=np.float64)
    out: dict[float, float] = {}
    for a in alphas:
        out[float(a)] = 0.0 if a <= 0 or m.size == 0 else float((m < float(a)).mean())
    return out


def recommend_alpha(margins: np.ndarray, target: float = 0.95,
                    alphas=DEFAULT_ALPHAS) -> float:
    """最小的 α，使让位比例 ≥ `target`（曲线单调 ⇒ 直接扫一遍即可）。"""
    curve = defer_curve(margins, alphas)
    for a in sorted(curve):
        if curve[a] >= target:
            return float(a)
    return float(max(curve)) if curve else 0.0


@torch.no_grad()
def logits_of(model: nets.CandidateScorer, data: dict, device: str,
              batch: int = 8192) -> np.ndarray:
    """整份数据集的 logits（内存够用：16k × 29 的 float32 只有 1.9 MB）。"""
    model.eval()
    n = data["state"].shape[0]
    out = np.empty((n, int(data["cand"].shape[1])), dtype=np.float32)
    for start in range(0, n, batch):
        idx = np.arange(start, min(start + batch, n))
        state, cand, mask, _ = bc._batch(data, idx, device)
        out[start:start + idx.size] = model(state, cand, mask).float().cpu().numpy()
    return out


def analyse(ckpt: Path, data_dir: Path, split: str = "val", device_pref: str = "auto",
            alphas=DEFAULT_ALPHAS, target: float = 0.95, batch: int = 8192) -> dict:
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
    labels = np.asarray(data["label"], dtype=np.int64)
    lg = logits_of(model, data, device, batch)
    margins = margins_from_logits(lg, labels)
    top1 = float((lg.argmax(axis=1) == labels).mean()) if lg.shape[0] else 0.0
    curve = defer_curve(margins, alphas)
    return {
        "ckpt": str(ckpt), "data": str(data_dir), "split": split, "device": device,
        "n": int(lg.shape[0]), "games": int(np.unique(data["game"]).size),
        "student_top1": top1,                       # = 与老师一致（α=0）
        "margin_median": float(np.median(margins)) if margins.size else 0.0,
        "margin_p90": float(np.percentile(margins, 90)) if margins.size else 0.0,
        "defer_curve": curve,
        "recommend_alpha": recommend_alpha(margins, target, alphas),
        "target": target,
        "label_source": data["meta"].get("label_source"),
        "teacher_labeled": data["meta"].get(f"teacher_labeled_{split}"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P5b 混合策略的 α 选择（离线让位曲线）")
    ap.add_argument("--ckpt", required=True, help="学生 checkpoint（model.pt）")
    ap.add_argument("--data", required=True, help="紧凑数据集目录（label 列 = 老师动作）")
    ap.add_argument("--split", default="val", choices=["train", "val"])
    ap.add_argument("--alphas", default=",".join(str(a) for a in DEFAULT_ALPHAS),
                    help="扫描的 α（逗号分隔，logit 单位）")
    ap.add_argument("--target", type=float, default=0.95,
                    help="推荐 α 的让位目标（取「让位 ≥ target」的最小 α）")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--batch", type=int, default=8192)
    ap.add_argument("--out", default=None, help="把结果 JSON 写到这个路径（可选）")
    args = ap.parse_args(argv)

    alphas = tuple(float(x) for x in args.alphas.split(",") if x.strip())
    r = analyse(Path(args.ckpt), Path(args.data), args.split, args.device, alphas,
                args.target, args.batch)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print(f"\n学生（{Path(args.ckpt).parent.name}）在 {Path(args.data).name}[{args.split}]："
          f"n={r['n']}，与老师一致 {r['student_top1']:.4f}")
    print(f"margin：中位 {r['margin_median']:.3f} / p90 {r['margin_p90']:.3f}"
          f"（α 超过它就翻盘）")
    print("让位曲线：" + "  ".join(f"α={a:g}→{v:.3f}" for a, v in r["defer_curve"].items()))
    print(f"推荐 α = {r['recommend_alpha']:g}（让位 ≥ {r['target']:.0%} 的最小值）"
          f" → 策略串 net:<{Path(args.ckpt).parent.name}/net.bin>@{r['recommend_alpha']:g}")
    print("⚠ 这只说明「让位多少」，不说明「更强」—— 强弱要 `python -m mahjong_ml.eval` 的配对实战检验。")
    if args.out:
        Path(args.out).write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
