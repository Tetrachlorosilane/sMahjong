# -*- coding: utf-8 -*-
"""[BL-12 辅助核对，非交付物] 冻结件 `ov-A-s00` 的 evt 张量：窗口语义 + 未来立直标签可派生性。

只读 E:\mahjong-training\compact\ov-A-s00 的 meta/evt/ctx/game/hand_no/seat；fail-closed。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

D = Path(r"E:\mahjong-training\compact\ov-A-s00")
SPLIT = sys.argv[1] if len(sys.argv) > 1 else "val"


def need(p: Path) -> Path:
    if not p.exists():
        raise SystemExit(f"[fail-closed] 缺文件：{p}")
    return p


evt = np.load(need(D / f"{SPLIT}.evt.npy"), mmap_mode="r")
game = np.load(need(D / f"{SPLIT}.game.npy"))
hand_no = np.load(need(D / f"{SPLIT}.hand_no.npy"))
ctx = np.load(need(D / f"{SPLIT}.ctx.npy"), mmap_mode="r")
seat = np.load(need(D / f"{SPLIT}.seat.npy"))
n = evt.shape[0]
print(f"[info] {SPLIT}: rows={n} evt={evt.shape}")

# ---- 1. 窗口语义：非空事件必须是右对齐的后缀（= 最近 K 条）
K = evt.shape[1]
CH = 20000
bad_pad = 0
max_nonempty = 0
for b0 in range(0, n, CH):
    sub = np.asarray(evt[b0:b0 + CH, :, 0:8], dtype=np.float32)
    ne = sub.sum(2) > 0
    k = ne.sum(1)
    max_nonempty = max(max_nonempty, int(k.max()))
    for i in range(k.size):
        s = int(k[i])
        if s and not (ne[i, -s:].all() and not ne[i, :-s].any()):
            bad_pad += 1
            if bad_pad < 4:
                print(f"[BAD] row {b0+i} k={s} 非后缀")
print(f"[chk1] 窗口右对齐后缀：非法行 {bad_pad} / {n}（应 0）；非空事件数上限 {max_nonempty} / K={K}")

# ---- 2. 事件类型直方图（1=discard 2=meld 3=riichi 4=kan 5=dora_flip）
hist = {}
for b0 in range(0, n, CH):
    sub = np.asarray(evt[b0:b0 + CH, :, 0:8], dtype=np.float32)
    t = sub.argmax(2)
    ne = sub.sum(2) > 0
    for v, c in zip(*np.unique(t[ne], return_counts=True)):
        hist[int(v)] = hist.get(int(v), 0) + int(c)
print("[chk2] 事件类型计数（0=draw 服务端不发）:", {k: hist[k] for k in sorted(hist)})

# ---- 3. 未来立直标签：扫窗口找 riichi 事件（type==3）
#    seen[i] = 该行窗口里有没有 riichi 事件；actor 由 actor one-hot 给出。
riichi_row = np.zeros(n, dtype=bool)          # 窗口内含 riichi 事件的行
riichi_actor = np.full(n, -1, dtype=np.int8)  # 该 riichi 事件的 actor（相对方位）
for b0 in range(0, n, CH):
    sub = np.asarray(evt[b0:b0 + CH, :, :], dtype=np.float32)
    ty = sub[:, :, 0:8].argmax(2)
    ne = sub[:, :, 0:8].sum(2) > 0
    is_r = (ty == 3) & ne
    has = is_r.any(1)
    idx = np.argmax(is_r, 1)
    act = (sub[:, :, 78:82] - 0.5) > 0            # 0.5/1.0 的 one-hot 编码
    a = np.where(act.any(2), act.argmax(2), -1)
    riichi_row[b0:b0 + sub.shape[0]] = has
    riichi_actor[b0:b0 + sub.shape[0]] = np.where(has, a[np.arange(sub.shape[0]), idx], -1)

print(f"[chk3] 含 riichi 事件的行：{int(riichi_row.sum())} / {n} = {riichi_row.mean():.4f}")
print(f"[chk3] riichi actor 分布：", {int(v): int((riichi_actor[riichi_row] == v).sum()) for v in np.unique(riichi_actor[riichi_row])})

# 手内时间序：按 (game, hand_no) 分组，行序是否等于时间序（用 ctx 的起始摸牌数代理）
key = game.astype(np.int64) * 1000 + hand_no.astype(np.int64)
splits = np.unique(key)
first_riichi_split = {}
mono = 0
tot = 0
for kk in splits:
    m = np.where(key == kk)[0]
    if m.size < 2:
        continue
    tot += 1
    # 用"窗口内 riichi 首次出现"的行下标应单调（一旦出现，后续行都在窗口里 ⇒ 一直为真）
    v = riichi_row[m]
    if (np.diff(v.astype(np.int8)) >= 0).all():
        mono += 1
print(f"[chk4] 手内 riichi_row 单调（0→1 不回退）的手数 {mono} / {tot}")

# ---- 4. 未来立直标签的可用规模（只用冻结件）
#    对每个 (手, 目标席) 取"首次观测到该席立直的行"，它之前的行 = 正例来源。
pos_avail = 0
neg_avail = 0
for kk in splits[:400]:
    m = np.where(key == kk)[0]
    for s_abs in range(4):
        # 相对方位：目标席相对当行行动者的偏移，随行变化 ⇒ 这里只统计"该席会立直的手数"
        pass
dup = 0
for kk in splits:
    m = np.where(key == kk)[0]
    v = riichi_row[m]
    if v.any():
        pos_avail += int((~v).sum())   # 立直之前的行都可当正例来源
        neg_avail += 0
print(f"[chk5] 至少一次立直的手数 {sum(1 for kk in splits if riichi_row[np.where(key==kk)[0]].any())} / {len(splits)}")
print(f"[chk5] 立直出现前的行数（未来立直正例池）= {pos_avail}；全行 {n}")
print("[done]")
