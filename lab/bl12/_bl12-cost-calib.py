# -*- coding: utf-8 -*-
"""[BL-12 预算标定，非交付物] 单折一次拟合的墙钟测量（决定本批能筛几个候选）。

口径与主探针 tools/lab/bl12-probe.py 相同：只读 E:\...\ov-A-s00 冻结件，零训练/零采集。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "lab"))
import importlib.util

spec = importlib.util.spec_from_file_location(
    "_pc", Path(__file__).resolve().parents[2] / "tools" / "lab" / "_probe-contrast-fit.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

D = Path(r"E:\mahjong-training\compact\ov-A-s00")
SPLIT = "val"


def need(p):
    if not Path(p).exists():
        raise SystemExit(f"[fail-closed] 缺文件：{p}")
    return Path(p)


def load(split, n):
    tile = np.asarray(np.load(need(D / f"{split}.tile.npy"), mmap_mode="r")[:n], dtype=np.float32)
    evt = np.asarray(np.load(need(D / f"{split}.evt.npy"), mmap_mode="r")[:n], dtype=np.float32)
    ctx = np.asarray(np.load(need(D / f"{split}.ctx.npy"), mmap_mode="r")[:n], dtype=np.float32)
    y = np.load(need(D / f"{split}.aux_opp_tenpai.npy"))[:n]
    game = np.load(need(D / f"{split}.game.npy"))[:n]
    hand = np.load(need(D / f"{split}.hand_no.npy"))[:n]
    return tile, evt, ctx, y, game, hand


class Net(nn.Module):
    def __init__(self, ctx_d, d=48):
        super().__init__()
        self.te = nn.Sequential(nn.Linear(34 * 48, d), nn.ReLU())
        self.ee = nn.GRU(96, d, batch_first=True)
        self.ce = nn.Sequential(nn.Linear(ctx_d, d), nn.ReLU())
        self.head = nn.Sequential(nn.Linear(d * 3, d), nn.ReLU(), nn.Linear(d, 1))

    def forward(self, tile, evt, ctx):
        h, _ = self.ee(evt)
        return self.head(torch.cat([self.te(tile.reshape(tile.shape[0], -1)), h.mean(1),
                                    self.ce(ctx)], -1)).squeeze(-1)


def main():
    torch.set_num_threads(8)
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    epochs = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    t0 = time.time()
    tile, evt, ctx, y, game, hand = load(SPLIT, n)
    t1 = time.time()
    print(f"[load] {n} 行 tile/evt/ctx 用时 {t1-t0:.1f}s "
          f"（tile {tile.nbytes/1e6:.0f}MB evt {evt.nbytes/1e6:.0f}MB）")
    key = game.astype(np.int64) * 1000 + hand.astype(np.int64)
    ug = np.unique(key)
    rng = np.random.default_rng(0)
    perm = rng.permutation(len(ug))
    fold_of = {int(g): i % 5 for i, g in enumerate(ug[perm])}
    row_fold = np.array([fold_of[int(g)] for g in key])
    tr = row_fold != 0
    te = row_fold == 0
    # 每行只用该行随机选的一个对手席（主探针口径）
    seat_choice = np.random.default_rng(7).integers(0, 3, size=n)
    lab = y[np.arange(n), seat_choice].astype(np.float32)
    print(f"[data] train {tr.sum()} test {te.sum()} pos_rate {lab[tr].mean():.4f}")

    m = Net(ctx.shape[1])
    opt = torch.optim.AdamW(m.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = nn.BCEWithLogitsLoss()
    xt, et, ct = (torch.from_numpy(tile[tr]), torch.from_numpy(evt[tr]), torch.from_numpy(ctx[tr]))
    yt = torch.from_numpy(lab[tr])
    xv, ev_, cv = (torch.from_numpy(tile[te]), torch.from_numpy(evt[te]), torch.from_numpy(ctx[te]))
    yv = lab[te]
    for ep in range(epochs):
        m.train()
        perm2 = torch.randperm(int(tr.sum()))
        for b0 in range(0, len(perm2), 512):
            idx = perm2[b0:b0 + 512]
            opt.zero_grad()
            lossf(m(xt[idx], et[idx], ct[idx]), yt[idx]).backward()
            opt.step()
        m.eval()
        with torch.no_grad():
            p = torch.sigmoid(m(xv, ev_, cv)).numpy()
        print(f"  ep{ep:02d} wall {time.time()-t1:6.1f}s 内层AUC {pc.auc_score(p, yv):.4f}")
    print(f"[total] {time.time()-t0:.1f}s（{n} 行 × {epochs} epoch，单折）")


if __name__ == "__main__":
    main()
