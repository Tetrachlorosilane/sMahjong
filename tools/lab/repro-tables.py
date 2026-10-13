#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""repro-tables.py —— 复现轮（`decision@6170` 三链 × 4 代）的**读表器**。

⛔ 本脚本**不重算**任何风格量 / 配对量：数来自 `style-vector.py`（经 `w-ladder.py measure` →
`style-ladder.json`）与 `w-ladder.py post` / `repro-pairs.py` 落下的配对账。这里只排表。

读的三份：
  · `style-ladder.json`     —— 逐链·代（**训练后** = `post`/`post_final`）的 9 维 + 采集顺位点
  · `paired/<label>-gNN-POST.json` —— 末代 vs g01 的配对（`w-ladder.py post`）
  · `paired-repro/<label>-gNN.json` —— 每一代 vs g01 的配对（`repro-pairs.py`）
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

S = Path(r"S:\mahjong-training")
STAGE = S / "w-ladder"

DIMS = (("riichi_rate", "立直率", "pct"), ("meld_rate", "副露率", "pct"),
        ("win_rate", "和了率", "pct"), ("deal_rate", "放铳率", "pct"),
        ("riichi_win_rate", "立直后和了率", "pct"), ("avg_win_score", "平均打点", "abs"),
        ("win_rate_x", "", ""))
#: 判据 ③ 的四个伴随量（沿用手上那份报告的口径与顺序）。
ACC = ("riichi_win_rate", "deal_rate", "avg_win_score", "win_rate")


def f_pct(x, nd=3):
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def f_num(x, nd=1):
    return "n/a" if x is None else f"{x:,.{nd}f}"


def main() -> int:
    ap = argparse.ArgumentParser(prog="repro-tables.py")
    ap.add_argument("--labels", default="v4-wl-dec6170-4g-c1,v4-wl-dec6170-4g-c2,v4-wl-dec6170-4g-c3")
    a = ap.parse_args()
    labels = [x.strip() for x in a.labels.split(",") if x.strip()]

    d = json.loads((STAGE / "style-ladder.json").read_text(encoding="utf-8"))
    rows = {(r["label"], r.get("generation")): r for r in d["rows"]}
    posts = {}
    for r in d["rows"]:
        if r.get("post_final"):
            posts[r["label"]] = r

    print("## A. 逐代（训练后）立直率 + 四个伴随量 + 采集顺位点（自由读数）\n")
    print("| 链 | 行 | 口径 | 立直率 | 立直后和了率 | 放铳率 | 平均打点 | 和了率 | 副露率 | 采集顺位点 (SE) |")
    print("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for lab in labels:
        for g in (1, 2, 3, 4):
            r = rows.get((lab, g))
            if not r:
                continue
            st, co = r["style"], r.get("collect") or {}
            rp = co.get("rank_points")
            se = co.get("rank_points_se")
            print(f"| `{lab}` | g{g:02d} | {r['phase_label']} | {f_pct(st.get('riichi_rate'))} "
                  f"| {f_pct(st.get('riichi_win_rate'))} | {f_pct(st.get('deal_rate'))} "
                  f"| {f_num(st.get('avg_win_score'))} | {f_pct(st.get('win_rate'))} "
                  f"| {f_pct(st.get('meld_rate'))} "
                  f"| {f_num(rp, 2) if rp is not None else 'n/a'} "
                  f"({f_num(se, 2) if se is not None else 'n/a'}) |")
        p = posts.get(lab)
        if p:
            st, co = p["style"], p.get("collect") or {}
            rp, se = co.get("rank_points"), co.get("rank_points_se")
            print(f"| `{lab}` | **POST** | ✓训练后（末代权重补采，主读数） | "
                  f"**{f_pct(st.get('riichi_rate'))}** | {f_pct(st.get('riichi_win_rate'))} "
                  f"| {f_pct(st.get('deal_rate'))} | {f_num(st.get('avg_win_score'))} "
                  f"| {f_pct(st.get('win_rate'))} | {f_pct(st.get('meld_rate'))} "
                  f"| {f_num(rp, 2) if rp is not None else 'n/a'} "
                  f"({f_num(se, 2) if se is not None else 'n/a'}) |")
        print("|  |  |  |  |  |  |  |  |  |  |")

    print("\n## B. 配对 Δ（每一代 / 末代 vs 该链 `g01`）\n")
    for lab in labels:
        print(f"### `{lab}`\n")
        files = sorted((STAGE / "paired-repro").glob(f"{lab}-g[0-9][0-9].json")) \
            + sorted((STAGE / "paired").glob(f"{lab}-g[0-9][0-9]-POST.json"))
        if not files:
            print("（无配对账）\n")
            continue
        print("| 行 | 口径 | 轴 | 训练前 | 训练后 | Δ | SE | t | 95% CI | 场数 | 是否配对 |")
        print("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |")
        for f in files:
            j = json.loads(f.read_text(encoding="utf-8"))
            # ⚠ 两种账的键不同：`repro-pairs.py` 用 `tag`；`w-ladder.py post` 用 `post_tag`。
            tag = j.get("tag") or j.get("post_tag") or f.stem
            phase = "post_final" if tag.endswith("-POST") else j.get("phase", "post")
            for name in ("riichi_rate",) + ACC + ("meld_rate",):
                v = (j.get("paired_delta") or {}).get(name)
                if not v:
                    continue
                unit = "pp" if v["unit"] == "pct" else "点"
                pre = v["pre"] * (100 if v["unit"] == "pct" else 1)
                post = v["post"] * (100 if v["unit"] == "pct" else 1)
                dec = 3 if v["unit"] == "pct" else 1
                pai = "✓ 真配对（同 seed）" if v.get("paired", True) else "✗ **不配对**（各代 seed 不同）"
                print(f"| {tag} | {phase} | {name} | {pre:.{dec}f} | {post:.{dec}f} "
                      f"| **{v['delta_pp']:+.2f}{unit}** | {v['se_pp']:.2f} | {v['t']:+.2f} "
                      f"| [{v['lo_pp']:+.2f},{v['hi_pp']:+.2f}] | {v['n_games']} | {pai} |")
        print()

    print("\n## C. 三链末代立直率极差（臂间散布）\n")
    vals = []
    for lab in labels:
        p = posts.get(lab)
        if p and p["style"].get("riichi_rate") is not None:
            vals.append((lab, 100 * p["style"]["riichi_rate"]))
    for lab, v in vals:
        print(f"- `{lab}` 末代（POST）立直率 = **{v:.3f}%**")
    if len(vals) >= 2:
        vs = [v for _, v in vals]
        print(f"- 三链极差 = **{max(vs) - min(vs):.3f}pp**（min {min(vs):.3f} / max {max(vs):.3f}）")
    print("\n## D. 末代 vs g01 的配对 Δ（主判据 ①）\n")
    print("| 链 | 立直率 Δ | 95% CI | 是否 ≥ +2pp 且 CI 排除 0 |")
    print("| --- | ---: | --- | --- |")
    for lab in labels:
        f = STAGE / "paired" / f"{lab}-g04-POST.json"
        if not f.is_file():
            print(f"| `{lab}` | （缺配对账） | — | — |")
            continue
        v = json.loads(f.read_text(encoding="utf-8"))["paired_delta"]["riichi_rate"]
        ok = v["delta_pp"] >= 2.0 and v["lo_pp"] > 0
        print(f"| `{lab}` | **{v['delta_pp']:+.2f}pp** | [{v['lo_pp']:+.2f},{v['hi_pp']:+.2f}] "
              f"| {'✓ 过' if ok else '✗ 不过'} |")

    print("\n## E. 判据 ③：四个伴随量的**三链固定效应合并**（探索性，⛔ 不是预注册判据）\n")
    print("三条链是**同一次干预的独立重复**（同起点、同配方、不同 seed 族）⇒ 用逆方差加权合并。")
    print("⛔ 单独看每一条都不显著（见 §B）；合并只是把三条同号的读数放到一起看。\n")
    print("| 轴 | 单位 | 三条链的 Δ | 合并 Δ | 合并 SE | 95% CI | 结论 |")
    print("| --- | --- | --- | ---: | ---: | --- | --- |")
    axes = [("riichi_rate", "pp"), ("riichi_win_rate", "pp"), ("deal_rate", "pp"),
            ("avg_win_score", "点"), ("win_rate", "pp"), ("meld_rate", "pp")]
    for name, unit in axes:
        ds, ses = [], []
        for lab in labels:
            f = STAGE / "paired" / f"{lab}-g04-POST.json"
            if not f.is_file():
                continue
            v = (json.loads(f.read_text(encoding="utf-8"))["paired_delta"] or {}).get(name)
            if v:
                ds.append(v["delta_pp"])
                ses.append(v["se_pp"])
        if len(ds) < 2:
            continue
        w = [1.0 / (s * s) for s in ses]
        m = sum(wi * d for wi, d in zip(w, ds)) / sum(w)
        se = (1.0 / sum(w)) ** 0.5
        lo, hi = m - 1.96 * se, m + 1.96 * se
        sig = "**显著**" if lo > 0 or hi < 0 else "含 0"
        good = "改善" if (name in ("deal_rate",) and hi < 0) or \
                         (name in ("win_rate", "riichi_rate") and lo > 0) else \
               ("轻微稀释" if name == "riichi_win_rate" and hi < 0 else "")
        print(f"| {name} | {unit} | {' / '.join(f'{d:+.2f}' for d in ds)} | **{m:+.2f}** "
              f"| {se:.2f} | [{lo:+.2f},{hi:+.2f}] | {sig} {good} |")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
