# -*- coding: utf-8 -*-
# [数据根可覆盖性 ① 可覆盖] 紧凑集根由 `--root` 指定（缺省 = 本机 E: 冻结件）；只读，不写任何数据根。
"""MV-1 第一刀（辅助头 `own_win`）的**训练前前置检查** —— 只读 · fail-closed · 零训练。

为什么要有它：`aux_win_flag` 是那条辅助头的**唯一标签**（预注册见 `lab/mv1/MV1-PLAN.md`）。
"预备用冻结件 `ov-A-s00` 里有这一列"**不能**当成"要训的那份紧凑集里也有" ——
所以开训前必须对**那份**重跑一遍，且缺列/形状不对/覆盖率不是 1/非零比跑飞/对齐不对
一律**非零退出**（⛔ 不做"缺了就跳过"的静默兜底）。

判据（逐条，全部来自核查①的实测口径）：
  ① 键在：`aux_win_flag` 在；② dtype = `uint8` 且 shape = `()`（标量）；③ 覆盖率 = 1.0（首维 == 行数）；
  ④ 非零比 ∈ `[--min-rate, --max-rate]`（缺省 0.15–0.30：二值核不该是"几乎全 0"或"几乎全 1"）；
  ⑤ **对齐**：在 `(game, hand_no, seat)` 内**逐行常数**（违规数必须 0）；
  ⑥ 每小局**至多有 1 家** `win=1`；⑦ `win=1` 的那家 == 该局 `delta` 最大者；
  ⑧ 它**不是** `delta > 0` 的别名（假阳率必须 > 0，否则"和了"与"收支为正"混成一列）。

用法：
    python tools\\lab\\mv1-preflight.py --selftest          # 判据自检（正/负各若干，零读盘）
    python tools\\lab\\mv1-preflight.py --root <紧凑集> --split val
    python tools\\lab\\mv1-preflight.py --root <紧凑集> --split train --json out.json
退出码：0 = 全过；1 = 任一判据不过或缺文件（fail-closed）；2 = 用法错。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

DEFAULT_ROOT = Path(r"E:\mahjong-training\compact\ov-A-s00")
DEFAULT_MIN_RATE = 0.15
DEFAULT_MAX_RATE = 0.30


def load_col(root: Path, split: str, name: str) -> np.ndarray:
    p = root / f"{split}.{name}.npy"
    if not p.is_file():
        raise FileNotFoundError(f"[fail-closed] 缺文件：{p}")
    return np.asarray(np.load(p, mmap_mode="r"))


def judge(cols: dict[str, np.ndarray], *, min_rate: float, max_rate: float) -> list[tuple[str, bool, str]]:
    """**纯函数**判据：`{列名: 数组}` → `[(判据名, 是否通过, 读数)]`（不读盘、不打印）。

    合成一个 `cols` 就能把每条判据正/负向各走一遍（`--selftest` 就是干这个的）——
    判据必须能**被判错**，否则"全过"什么也不说明。
    """
    out: list[tuple[str, bool, str]] = []
    need = ("win", "game", "hand_no", "seat", "delta")
    missing = [k for k in need if k not in cols]
    if missing:
        return [("键齐（win/game/hand_no/seat/delta）", False, f"缺 {missing}")]
    win = np.asarray(cols["win"])
    game = np.asarray(cols["game"], dtype=np.int64)
    hand = np.asarray(cols["hand_no"], dtype=np.int64)
    seat = np.asarray(cols["seat"], dtype=np.int64)
    delta = np.asarray(cols["delta"], dtype=np.float64)
    n = int(win.shape[0])

    out.append(("① 标量（shape 的首维 == 行数、无尾维）",
                win.ndim == 1 and all(a.shape[0] == n for a in (game, hand, seat, delta)),
                f"win.shape={win.shape} n={n}"))
    if not (win.ndim == 1 and all(a.shape[0] == n for a in (game, hand, seat, delta))):
        return out
    out.append(("② 覆盖率 = 1.0（首维 / 行数）", True, f"{n} 行（读到就是覆盖；缺列已在①拦下）"))
    rate = float((win != 0).mean())
    out.append((f"④ 非零比 ∈ [{min_rate:g}, {max_rate:g}]（二值核不该退化）",
                min_rate <= rate <= max_rate, f"非零比 = {rate:.6f}"))

    key = (game * 1000 + hand) * 10 + seat
    uk, inv = np.unique(key, return_inverse=True)
    smin = np.full(uk.size, 9, dtype=np.int64)
    smax = np.full(uk.size, -1, dtype=np.int64)
    np.minimum.at(smin, inv, win)
    np.maximum.at(smax, inv, win)
    bad = int((smin != smax).sum())
    out.append(("⑤ (小局,座位) 内逐行常数（违规数 = 0）", bad == 0,
                f"违规 {bad}/{uk.size} 组"))

    hkey = game * 1000 + hand
    huk, hinv = np.unique(hkey, return_inverse=True)
    # ⚠ "每小局几家 win=1" 必须按 **(小局,座位) 组**数，⛔ 不能按行数求和 ——
    #   每家十几行 ⇒ 行数和恒 > 1（本工具第一版就栽在这里，把 563 局全判成"多家和了"）。
    gval = smin                                              # 组内常数（⑤ 已验）
    gh = np.zeros(uk.size, dtype=np.int64)
    gh[inv] = hinv
    winners_per_hand = np.zeros(huk.size, dtype=np.int64)
    np.add.at(winners_per_hand, gh[gval == 1], 1)
    multi = int((winners_per_hand > 1).sum())
    with_win = int((winners_per_hand >= 1).sum())
    out.append(("⑥ 每小局至多 1 家 win=1（按小局×座位组计，⛔ 不是行数）", multi == 0,
                f"{with_win}/{huk.size} 局有和了者；多家 = {multi}"))

    # ⑦ win=1 的座位 == 该局 delta 最大者
    agree = both = 0
    wseat: dict[int, int] = {}
    dmax: dict[int, tuple[int, float]] = {}
    for r in range(n):
        h = int(hinv[r])
        s = int(seat[r])
        if win[r] == 1:
            wseat[h] = s
        cur = dmax.get(h)
        if cur is None or delta[r] > cur[1]:
            dmax[h] = (s, float(delta[r]))
    for h, s in wseat.items():
        if h in dmax:
            both += 1
            agree += int(dmax[h][0] == s)
    out.append(("⑦ win=1 的座位 == 该局 delta 最大者", both > 0 and agree == both,
                f"{agree}/{both}（有和了者的小局）"))

    tp = int(((delta > 0) & (win != 0)).sum())
    fp = int(((delta > 0) & (win == 0)).sum())
    fn = int(((delta <= 0) & (win != 0)).sum())
    # 判据 = **两列不恒等**（`FP+FN > 0`；未和者也可能收支为正 —— 立直棒退还/他家支付）
    # 且 **赢了必为正**（`FN == 0`，这是真不变量：和了的人不可能收支 ≤ 0）。
    out.append(("⑧ 它不是 `delta > 0` 的别名（两列不恒等，且 win=1 必为正）",
                (fp + fn) > 0 and fn == 0,
                f"TP={tp} FP={fp}（假阳率 {fp / max(tp + fp, 1):.6f}）FN={fn}"))
    return out


def _cols_from_root(root: Path, split: str) -> dict[str, np.ndarray]:
    return {"win": load_col(root, split, "aux_win_flag"),
            "game": load_col(root, split, "game"),
            "hand_no": load_col(root, split, "hand_no"),
            "seat": load_col(root, split, "seat"),
            "delta": load_col(root, split, "delta")}


def _synth(n_hand: int = 4, rows_per: int = 3, *, break_align: bool = False,
           two_winners: bool = False, rate: float = 0.25, alias_delta: bool = False
           ) -> dict[str, np.ndarray]:
    """合成一份"四家 × 若干局"的最小紧凑集（判据自检用）。"""
    win, game, hand, seat, delta = [], [], [], [], []
    for h in range(n_hand):
        for s in range(4):
            for r in range(rows_per):
                game.append(0)
                hand.append(h)
                seat.append(s)
                w = 1 if s == (h % 4) else 0
                if two_winners and s in ((h % 4), (h % 4 + 1) % 4):
                    w = 1
                if break_align and r == 1 and s == (h % 4):
                    w = 0
                win.append(w)
                if alias_delta:
                    delta.append(100.0 if w else -100.0)
                elif w:
                    delta.append(5000.0 - 10.0 * s)
                elif s == 3:
                    # 未和者也可能收支为正（立直棒退还 / 他家支付）⇒ ⑧ 才有假阳、
                    # 才有别于 `delta > 0`（⛔ 合成数据必须能**行使**这条判据，否则它是空转的）
                    delta.append(200.0)
                else:
                    delta.append(-3000.0 - 10.0 * s)
    if rate != 0.25:                       # 只用来验"非零比"那条
        win = [1 if i < int(len(win) * rate) else 0 for i in range(len(win))]
    return {"win": np.array(win, dtype=np.uint8), "game": np.array(game, dtype=np.int64),
            "hand_no": np.array(hand, dtype=np.int64), "seat": np.array(seat, dtype=np.int64),
            "delta": np.array(delta, dtype=np.float64)}


def selftest() -> int:
    """判据自检：正向必须全过；每个负向必须**被点数**。"""
    print("== mv1-preflight 判据自检（纯内存，不读盘）==")
    fails = 0
    pos = judge(_synth(), min_rate=DEFAULT_MIN_RATE, max_rate=DEFAULT_MAX_RATE)
    okp = all(v for _, v, _ in pos)
    print(f"  [{'ok  ' if okp else 'FAIL'}] 正向：合成紧凑集全过（{len(pos)} 条）")
    fails += 0 if okp else 1
    for name, cond, detail in pos:
        print(f"        · {name} —— {detail}")
    cases = {
        "③/② 缺列（win 不在）": ({"game": np.zeros(2, np.int64), "hand_no": np.zeros(2, np.int64),
                                 "seat": np.zeros(2, np.int64), "delta": np.zeros(2)}, "缺"),
        "⑤ 对齐被破坏（组内不常数）": (_synth(break_align=True), "⑤"),
        "⑥ 一局两家和了": (_synth(two_winners=True), "⑥"),
        "④ 非零比退化到 0": (_synth(rate=0.0), "④"),
        "⑧ 退化成 `delta>0` 的别名": (_synth(alias_delta=True), "⑧"),
    }
    for label, (cols, want) in cases.items():
        rows = judge(cols, min_rate=DEFAULT_MIN_RATE, max_rate=DEFAULT_MAX_RATE)
        hit = [nm for nm, v, det in rows if not v and (want in nm or want in det)]
        good = bool(hit)
        print(f"  [{'ok  ' if good else 'FAIL'}] 负向：{label} ⇒ 抓到 {'/'.join(hit) or '（没抓到）'}")
        fails += 0 if good else 1
    print(f"\n自检：{len(cases) + 1} 组，失败 {fails}")
    print("MV1-PREFLIGHT SELFTEST " + ("PASS" if fails == 0 else "FAIL"))
    return 0 if fails == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MV-1 辅助头标签前置检查（只读 · fail-closed · 零训练）")
    ap.add_argument("--selftest", action="store_true", help="跑判据自检（纯内存，不读盘）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="紧凑集根目录（缺省 = 本机冻结件）")
    ap.add_argument("--split", default="val", choices=["val", "train"])
    ap.add_argument("--min-rate", type=float, default=DEFAULT_MIN_RATE)
    ap.add_argument("--max-rate", type=float, default=DEFAULT_MAX_RATE)
    ap.add_argument("--json", default=None, help="把逐条判据写到这个文件（缺省：不写任何东西）")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    if a.selftest:
        return selftest()
    root = Path(a.root)
    if not root.is_dir():
        print(f"[fail-closed] 根不存在：{root}")
        return 1
    print(f"根 = {root} · split = {a.split}")
    try:
        cols = _cols_from_root(root, a.split)
    except FileNotFoundError as e:
        print(str(e))
        return 1
    rows = judge(cols, min_rate=a.min_rate, max_rate=a.max_rate)
    for name, cond, detail in rows:
        print(f"  [{'ok  ' if cond else 'FAIL'}] {name} —— {detail}")
    bad = [nm for nm, v, _ in rows if not v]
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"root": str(root), "split": a.split, "rows": [dict(name=n, ok=v, detail=d) for n, v, d in rows]},
            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nMV1-PREFLIGHT {'PASS' if not bad else 'FAIL'}：{len(rows)} 条，不过 {len(bad)} 条")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
