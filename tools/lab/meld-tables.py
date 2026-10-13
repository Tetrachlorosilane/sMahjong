#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""meld-tables.py —— **`meld` 轴**（副露率）轮的读表器（`repro-tables.py` 的同族，主读数换成 `meld_rate`）。

⛔ 本脚本**不重算**任何风格量 / 配对量：数来自 `style-vector.py`（经 `w-ladder.py measure` →
`style-ladder.json`）与 `w-ladder.py post` / `repro-pairs.py` 落下的配对账。这里只排表。

读的三份：
  · `style-ladder.json`             —— 逐链·代（**训练后**）的 9 维 + 采集顺位点
  · `paired/<label>-gNN-POST.json`  —— 末代 vs g01 的**真配对**（`w-ladder.py post`）
  · `paired-repro/<label>-gNN.json` —— 每一代 vs g01 的差值（**不配对**，各代 seed 不同）

用法：python meld-tables.py --labels v4-wl-meld-4g-c1,v4-wl-meld-4g-c2
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

S = Path(r"S:\mahjong-training")
STAGE = S / "w-ladder"

#: ① 主判据轴 + ② 伴随量（`PREREGISTRATION-MELD.md` §2：主伴随量 = `wins`，另报 deals/rank_points/riichi_win_rate）。
MAIN = "meld_rate"
ACC = ("win_rate", "deal_rate", "riichi_win_rate", "avg_win_score", "riichi_rate")
TOL_MAIN = 2.0


def f_pct(x, nd=3):
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def f_num(x, nd=1):
    return "n/a" if x is None else f"{x:,.{nd}f}"


def main() -> int:
    ap = argparse.ArgumentParser(prog="meld-tables.py")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--gens", default="1,2,3,4")
    a = ap.parse_args()
    labels = [x.strip() for x in a.labels.split(",") if x.strip()]
    gens = [int(x) for x in a.gens.split(",") if x.strip()]

    d = json.loads((STAGE / "style-ladder.json").read_text(encoding="utf-8"))
    rows = {(r["label"], r.get("generation")): r for r in d["rows"]}
    posts = {r["label"]: r for r in d["rows"] if r.get("post_final")}
    ng = max(gens)

    print(f"## A. 逐代（训练后）**副露率** + 伴随量 + 采集读数\n")
    print("| 链 | 行 | 口径 | **副露率** | 和了率 | 放铳率 | 立直后和了率 | 平均打点 | 立直率 "
          "| 每场和了 (SE) | 每场放铳 (SE) | 每场和了点 (SE) | 采集顺位点 (SE) |")
    print("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")

    def line(lab, tag, phase, r):
        st, co = r["style"], r.get("collect") or {}

        def cs(k, nd=3):
            v, se = co.get(k), co.get(k + "_se")
            return "n/a" if v is None else f"{v:.{nd}f} ({se:.{nd}f})"

        print(f"| `{lab}` | {tag} | {phase} | **{f_pct(st.get(MAIN))}** "
              f"| {f_pct(st.get('win_rate'))} | {f_pct(st.get('deal_rate'))} "
              f"| {f_pct(st.get('riichi_win_rate'))} | {f_num(st.get('avg_win_score'), 0)} "
              f"| {f_pct(st.get('riichi_rate'))} "
              f"| {cs('wins_per_game')} | {cs('deals_per_game')} "
              f"| {cs('win_points_per_game', 0)} | {cs('rank_points', 2)} |")

    for lab in labels:
        for g in gens:
            r = rows.get((lab, g))
            if r:
                line(lab, f"g{g:02d}", r["phase_label"], r)
        p = posts.get(lab)
        if p:
            line(lab, "**POST**", "✓训练后（末代权重补采，主读数）", p)
        print("|  |  |  |  |  |  |  |  |  |  |  |  |  |")

    print(f"\n## B. 差值 Δ（每一代 / 末代 POST vs 该链 `g01`）\n")
    for lab in labels:
        print(f"### `{lab}`\n")
        files = sorted((STAGE / "paired-repro").glob(f"{lab}-g[0-9][0-9].json")) \
            + sorted((STAGE / "paired").glob(f"{lab}-g[0-9][0-9]-POST.json"))
        if not files:
            print("（无账）\n")
            continue
        print("| 行 | 口径 | 轴 | 训练前 | 训练后 | Δ | SE | t | 95% CI | 场数 | 是否配对 |")
        print("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |")
        for f in files:
            j = json.loads(f.read_text(encoding="utf-8"))
            tag = j.get("tag") or j.get("post_tag") or f.stem
            phase = "post_final" if tag.endswith("-POST") else j.get("phase", "post")
            for name in (MAIN,) + ACC:
                v = (j.get("paired_delta") or {}).get(name)
                if not v:
                    continue
                unit = "pp" if v["unit"] == "pct" else "点"
                dec = 3 if v["unit"] == "pct" else 1
                mul = 100 if v["unit"] == "pct" else 1
                pai = "✓ 真配对（同 seed）" if v.get("paired", True) else "✗ **不配对**（各代 seed 不同）"
                print(f"| {tag} | {phase} | {name} | {v['pre'] * mul:.{dec}f} | {v['post'] * mul:.{dec}f} "
                      f"| **{v['delta_pp']:+.2f}{unit}** | {v['se_pp']:.2f} | {v['t']:+.2f} "
                      f"| [{v['lo_pp']:+.2f},{v['hi_pp']:+.2f}] | {v['n_games']} | {pai} |")
        print()

    print(f"## C. 末代（POST）**副露率**极差（臂间散布）\n")
    vals = []
    for lab in labels:
        p = posts.get(lab)
        if p and p["style"].get(MAIN) is not None:
            vals.append((lab, 100 * p["style"][MAIN]))
    for lab, v in vals:
        print(f"- `{lab}` 末代副露率 = **{v:.3f}%**")
    if len(vals) >= 2:
        vs = [v for _, v in vals]
        print(f"- **极差 = {max(vs) - min(vs):.3f}pp**（min {min(vs):.3f} / max {max(vs):.3f}）")

    print(f"\n## D. 主判据 ①：末代 vs `g01` 的**真配对** Δ（副露率）\n")
    print(f"| 链 | Δ（副露率） | SE | t | 95% CI | ≥ +{TOL_MAIN:g}pp 且 CI 排除 0 |")
    print("| --- | ---: | ---: | ---: | --- | --- |")
    for lab in labels:
        f = STAGE / "paired" / f"{lab}-g{ng:02d}-POST.json"
        if not f.is_file():
            print(f"| `{lab}` | （缺配对账） | — | — | — | — |")
            continue
        v = json.loads(f.read_text(encoding="utf-8"))["paired_delta"][MAIN]
        ok = v["delta_pp"] >= TOL_MAIN and v["lo_pp"] > 0
        print(f"| `{lab}` | **{v['delta_pp']:+.2f}pp** | {v['se_pp']:.2f} | {v['t']:+.2f} "
              f"| [{v['lo_pp']:+.2f},{v['hi_pp']:+.2f}] | {'✓ 过' if ok else '✗ 不过'} |")

    print("\n## E. ② 伴随量：**末代 POST** 的逐链 Δ + 固定效应合并（探索性，⛔ 不是预注册判据）\n")
    print("| 轴 | 单位 | 各链 Δ | 合并 Δ | 合并 SE | 95% CI | 判读 |")
    print("| --- | --- | --- | ---: | ---: | --- | --- |")
    for name in (MAIN,) + ACC:
        ds, ses = [], []
        for lab in labels:
            f = STAGE / "paired" / f"{lab}-g{ng:02d}-POST.json"
            if not f.is_file():
                continue
            v = (json.loads(f.read_text(encoding="utf-8"))["paired_delta"] or {}).get(name)
            if v:
                ds.append(v["delta_pp"])
                ses.append(v["se_pp"])
        if len(ds) < 2:
            continue
        w = [1.0 / (s * s) for s in ses]
        m = sum(wi * dd for wi, dd in zip(w, ds)) / sum(w)
        se = (1.0 / sum(w)) ** 0.5
        lo, hi = m - 1.96 * se, m + 1.96 * se
        sig = "**显著**" if lo > 0 or hi < 0 else "含 0"
        tag = "改善" if (name == MAIN and lo > 0) or (name == "win_rate" and lo > 0) \
            or (name == "deal_rate" and hi < 0) else ""
        print(f"| {name} | {'pp' if name != 'avg_win_score' else '点'} "
              f"| {' / '.join(f'{x:+.2f}' for x in ds)} | **{m:+.2f}** | {se:.2f} "
              f"| [{lo:+.2f},{hi:+.2f}] | {sig} {tag} |")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
