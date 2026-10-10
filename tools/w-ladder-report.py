#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""w-ladder-report.py —— 把 `style-ladder.json`（`w-ladder.py measure` 的产物）排成报告正文的几张表。

⛔ 本脚本**不重算**任何风格量：`style-vector.py` 算的都在 `style-ladder.json` 里，这里只排版。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

S = Path(r"S:\mahjong-training")
STAGE = S / "w-ladder"

DIMS = ("riichi_rate", "meld_rate", "win_rate", "deal_rate", "avg_win_score", "avg_win_turn",
        "avg_riichi_turn", "dama_rate", "riichi_win_rate")


def pct(x, nd=2):
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def num(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def w_for_parity_from_logs() -> list:
    """从 `logs/<label>-g0g-from-release.log` 里摘 `w_for_parity` 那行（**日志原文**，不重算）。"""
    rows = []
    for f in sorted((STAGE / "logs").glob("v4-wl-*-from-release.log")):
        txt = f.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"小局级 mean ([+-][\d.]+) std ([\d.]+) \*\*点\*\* "
                      r"vs 结果奖励 std ([\d.]+) 点 ⇒ \*\*([\d.]+)× 结果奖励的尺度\*\*.*?"
                      r"要 1:1 大约要 `w=([\d.]+)`", txt, re.S)
        if not m:
            continue
        rows.append({"log": f.name, "hand_mean": float(m.group(1)), "hand_std": float(m.group(2)),
                     "reward_std": float(m.group(3)), "scale": float(m.group(4)),
                     "w_for_parity_printed": float(m.group(5))})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(prog="w-ladder-report.py")
    ap.add_argument("--md", default=str(STAGE / "ladder-report.md"))
    args = ap.parse_args()
    d = json.loads((STAGE / "style-ladder.json").read_text(encoding="utf-8"))
    md: list[str] = []

    # ---- ① w_for_parity（日志原文） ------------------------------------------------------------
    md += ["## ① 实测 `w_for_parity`（日志原文）", "",
           "| 链·代 | 小局级 mean | std（点） | 结果奖励 std（点） | 尺度（× 结果奖励） | 打印的 `w_for_parity` |",
           "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for r in w_for_parity_from_logs():
        md.append(f"| {r['log'].replace('-from-release.log','')} | {r['hand_mean']:+.1f} "
                  f"| {r['hand_std']:.1f} | {r['reward_std']:.1f} | {r['scale']:.5f}× "
                  f"| **{r['w_for_parity_printed']:.0f}** |")

    # ---- ② 逐链风格向量 ------------------------------------------------------------------------
    md += ["", "## ② 逐链风格向量（`tools/style-vector.py` 读，6 链 × 2 代）", "",
           "| 链 | w | 代 | 小局 | 立直率 | 副露率 | 和了率 | 放铳率 | 平均打点 | 和了巡 | 立直巡 | 默听率 | 立直后和了率 |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in d["rows"]:
        s = r["style"]
        md.append(f"| {r['label']} | {r['w']} | {r['generation']} | {s.get('seat_hands')} "
                  f"| {pct(s.get('riichi_rate'))} | {pct(s.get('meld_rate'))} | {pct(s.get('win_rate'))} "
                  f"| {pct(s.get('deal_rate'))} | {num(s.get('avg_win_score'), 0)} "
                  f"| {num(s.get('avg_win_turn'))} | {num(s.get('avg_riichi_turn'))} "
                  f"| {pct(s.get('dama_rate'))} | {pct(s.get('riichi_win_rate'))} |")
    # 上一轮两条臂（只作参照）
    md += ["", "### 上一轮（`w=2`，各 1 条链）· 只作参照", "",
           "| 链 | 代 | 立直率 | 副露率 | 和了率 | 放铳率 | 默听率 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in (d.get("prior_same_recipe") or []) + (d.get("prior_w2") or []):
        s = r["style"]
        md.append(f"| {r['label']} | {r['generation']} | {pct(s.get('riichi_rate'))} "
                  f"| {pct(s.get('meld_rate'))} | {pct(s.get('win_rate'))} | {pct(s.get('deal_rate'))} "
                  f"| {pct(s.get('dama_rate'))} |")

    # ---- ③ 逐链采集读数 ------------------------------------------------------------------------
    md += ["", "### 逐链采集读数（1,000 场自对弈，对风格桌；`se` = **逐场**标准误）", "",
           "| 链 | w | 代 | 采集顺位点 (se) | 每场和了 (se) | 每场放铳 (se) | 每场和了点 (se) | 和了率 | 放铳率 | 平均打点 |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in d["rows"]:
        c = r["collect"]
        def cs(k, nd=2):
            v, se = c.get(k), c.get(k + "_se")
            return "n/a" if v is None else f"{v:.{nd}f} ({se:.{nd}f})"
        md.append(f"| {r['label']} | {r['w']} | {r['generation']} | {cs('rank_points')} "
                  f"| {cs('wins_per_game', 3)} | {cs('deals_per_game', 3)} "
                  f"| {cs('win_points_per_game', 0)} | {pct(c.get('win_rate'))} "
                  f"| {pct(c.get('deal_in_rate'))} | {num(c.get('avg_win_score'), 0)} |")

    # ---- ④ 臂均值 ± 臂间区间 -------------------------------------------------------------------
    md += ["", "## ③ 臂均值 ± **臂间区间**（2 条链/臂；区间 = [min,max]）", "",
           "| 臂 | w | 代 | 立直率 均值[min,max] | 副露率 | 和了率 | 放铳率 | 平均打点 | 默听率 | 立直巡 | 采集顺位点[min,max] |",
           "| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for arm, gens in d["arms"].items():
        for gen, ent in sorted(gens.items()):
            def cell(k, f=None):
                v = ent.get(k)
                if not v:
                    return "n/a"
                if f is None:
                    f = pct if (v.get("unit") == "pct") else (lambda z: f"{z:.2f}")
                return f"{f(v['mean'])}[{f(v['min'])},{f(v['max'])}]"
            rp = ent.get("collect_rank_points")
            rp_s = "n/a" if not rp else f"{rp['mean']:.2f}[{rp['min']:.2f},{rp['max']:.2f}]"
            md.append(f"| {arm} | {ent['w']} | {gen} | {cell('riichi_rate')} | {cell('meld_rate')} "
                      f"| {cell('win_rate')} | {cell('deal_rate')} "
                      f"| {cell('avg_win_score', lambda z: f'{z:.0f}')} | {cell('dama_rate')} "
                      f"| {cell('avg_riichi_turn')} | {rp_s} |")

    # ---- ⑤ 噪声地板 ----------------------------------------------------------------------------
    nf = d.get("noise_floor") or {}
    if nf:
        md += ["", "## ④ 噪声地板：**同配方三次跑**（上一轮 `v4-sty-a` + 本轮 `w=0` 两条链）", "",
               f"三条链：{', '.join(nf.get('labels', []))}（同配方 = 同起点/同风格桌/同 `-NoGate`/"
               f"同场次；只差采集 seed）", "",
               "| 代 | 读数 | 三次跑的值 | **极差** | 逐场 SE |", "| --- | --- | --- | ---: | --- |"]
        for gen in sorted(k for k in nf if k.startswith("g")):
            ent = nf[gen]
            for k in DIMS:
                if k not in ent:
                    continue
                # ⚠ 只有比率维是百分数；`avg_win_score`（点）/巡目是绝对量（别印成 `694500%`）。
                f = pct if (ent[k].get("unit", "pct" if k.endswith("_rate") else "abs") == "pct") \
                    else (lambda z: f"{z:.2f}")
                vals = ", ".join(f(v) for v in ent[k]["values"])
                md.append(f"| {gen} | {k} | {vals} | {f(ent[k]['range'])} | — |")
            for k in ("rank_points", "deals_per_game", "wins_per_game"):
                key = "collect_" + k
                if key not in ent:
                    continue
                vals = ", ".join(f"{v:.3f}" for v in ent[key]["values"])
                ses = ", ".join(f"{s:.3f}" for s in ent[key]["se"])
                md.append(f"| {gen} | {key} | {vals} | {ent[key]['range']:.3f} | {ses} |")
    text = "\n".join(md) + "\n"
    Path(args.md).write_text(text, encoding="utf-8")
    print(text)
    print(f"[落盘] {args.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
