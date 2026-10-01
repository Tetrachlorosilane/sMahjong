"""对手模型（belief / danger 头）的**成色测量** —— 回答"这些头到底有多准"。

为什么要有它（2026-09-30）：策略侧要"把对手模型接进决策"，但**先得知道接进去的是不是有用的东西**。
仓库里此前只有训练时的 `val_loss_belief_tenpai`（一个 BCE 标量，没有基线、没有 AUC），
所以这里量三件事，**每一件都给"相对基线"的对照**（否则数字没有意义）：

1. `belief_tenpai[3]` vs 真值 `aux_opp_tenpai[3]`：**AUC** + BCE，对照"全预测边缘基率"的 BCE；
2. `danger[L,4]` 在**被选中的那个候选**上 vs 真值 `aux_opp_dealin[3]`：**AUC** + BCE
   （⚠ 标签只落在被选中的候选上 —— 见 `dataset.py` 的表，所以只能这么量，不能拿"未选中候选的
   danger"去和"没放铳"比，那会把样本量虚增几十倍、AUC 虚高）；
3. `belief_hand[3,34]`：逐格 BCE，对照"用本切分的每格频率当常数预测"的 BCE；外加 **top-1 命中率**
   （模型给每家 argmax 的那张牌，真的在该家手里的比例）与"该家手里有几张"的边缘基率。

用法（`--data` 是 compact 目录，`--ckpt` 是 ckpt 目录）：

    python -m mahjong_ml.v4.heads_audit --data S:\\mahjong-training\\compact\\v4-heads-g01 \\
        --ckpt S:\\mahjong-training\\ckpt\\v4-heads-g01 --split val

⚠ 它**只读**：不训练、不改权重、不写数据根。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import torch

from . import dataset as v4ds
from . import model as M
from . import value_audit as va
from .pretrain import _batch


def auc(score: np.ndarray, y: np.ndarray) -> float:
    """秩和法 AUC（不依赖 sklearn）。`y` 是 0/1；全 0 或全 1 时返回 `nan`。"""
    y = np.asarray(y).astype(np.int64)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=np.float64)
    ranks[order] = np.arange(1, len(score) + 1, dtype=np.float64)
    # 并列取平均秩（否则同分候选的顺序会凭空造出 AUC）
    s = np.asarray(score)[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def bce(prob: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(np.asarray(prob, dtype=np.float64), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=np.float64)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def forward_heads(model: M.V4Model, data: dict, n: int, device: str,
                  out_keys: tuple[str, ...], batch: int = 2048) -> dict[str, np.ndarray]:
    """整切分前向一遍，取需要的头（logits 原样返回，概率在各指标处再算）。"""
    acc: dict[str, list[np.ndarray]] = {k: [] for k in out_keys}
    for i0 in range(0, n, batch):
        idx = np.arange(i0, min(i0 + batch, n))
        b = _batch(data, idx, device)
        with torch.no_grad():
            out = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"])
        for k in out_keys:
            acc[k].append(out[k].float().cpu().numpy())
    return {k: np.concatenate(v, axis=0) for k, v in acc.items()}


def report(data_dir: Path, ckpt: Path, split: str, device: str) -> int:
    data = v4ds.load_split(data_dir, split)
    n = int(data["label"].shape[0])
    print(f"== {Path(ckpt).name} on {Path(data_dir).name}/{split}（n={n}）==")
    for k in ("aux_opp_tenpai", "aux_opp_dealin", "aux_opp_hand"):
        if data.get(k) is None:
            print(f"⛔ 这份数据没有 `{k}`（老数据集 / 没带 --aux）⇒ 量不了")
            return 2
    model = va.load_model(ckpt, device)
    heads = forward_heads(model, data, n, device,
                          ("belief_tenpai", "danger", "belief_hand"))
    label = np.asarray(data["label"], dtype=np.int64)
    # ⚠ `belief_hand` 可能以 `[B,102]` 或 `[B,3,34]` 出来（取决于头是否 reshape）——两种都接受
    bh = heads["belief_hand"]
    if bh.ndim == 2:
        bh = bh.reshape(len(bh), 3, 34)
    heads["belief_hand"] = bh
    bt_y = np.asarray(data["aux_opp_tenpai"], dtype=np.float64)
    dl_y = np.asarray(data["aux_opp_dealin"], dtype=np.float64)
    hd_y = np.asarray(data["aux_opp_hand"], dtype=np.float64)

    print("\n-- belief_tenpai（对手是否听牌）--")
    print("  家  基率     AUC     BCE     边缘BCE   相对")
    for j in range(3):
        p = 1.0 / (1.0 + np.exp(-heads["belief_tenpai"][:, j]))
        base = float(bt_y[:, j].mean())
        a = auc(heads["belief_tenpai"][:, j], bt_y[:, j])
        b_model, b_base = bce(p, bt_y[:, j]), bce(np.full(n, base), bt_y[:, j])
        print(f"  {j}  {base:6.3f}  {a:6.3f}  {b_model:6.4f}  {b_base:6.4f}  "
              f"{(1 - b_model / b_base) * 100:+6.1f}%")

    print("\n-- danger（在**被选中的候选**上对实际放铳）--")
    print("  家  基率     AUC     BCE     边缘BCE   相对")
    for j in range(3):
        s = heads["danger"][np.arange(n), label, j]          # 只看被选中那一手
        p = 1.0 / (1.0 + np.exp(-s))
        base = float(dl_y[:, j].mean())
        a = auc(s, dl_y[:, j])
        b_model, b_base = bce(p, dl_y[:, j]), bce(np.full(n, base), dl_y[:, j])
        print(f"  {j}  {base:6.3f}  {a:6.3f}  {b_model:6.4f}  {b_base:6.4f}  "
              f"{(1 - b_model / b_base) * 100:+6.1f}%")

    print("\n-- belief_hand（逐对手手牌 3×34 格）--")
    print("  家  基率     BCE     边缘BCE   相对    top1命中  top1基率")
    for j in range(3):
        p = 1.0 / (1.0 + np.exp(-heads["belief_hand"][:, j]))
        base_tile = hd_y[:, j].mean(axis=0)                  # 每格频率（本切分）
        b_model = bce(p, hd_y[:, j])
        b_base = bce(np.broadcast_to(base_tile, hd_y[:, j].shape), hd_y[:, j])
        top1 = np.asarray(heads["belief_hand"][:, j]).argmax(axis=1)
        hit = float(hd_y[np.arange(n), j, top1].mean())
        rate = float(hd_y[:, j].mean())
        print(f"  {j}  {rate:6.3f}  {b_model:6.4f}  {b_base:6.4f}  "
              f"{(1 - b_model / b_base) * 100:+6.1f}%   {hit:7.3f}  {rate:7.3f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="量对手模型（belief/danger 头）的成色")
    ap.add_argument("--data", required=True, help="compact 目录")
    ap.add_argument("--ckpt", required=True, help="ckpt 目录（含 model.pt）")
    ap.add_argument("--split", default="val", choices=["train", "val"])
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args(argv)
    return report(Path(args.data), Path(args.ckpt), args.split, args.device)


if __name__ == "__main__":
    raise SystemExit(main())
