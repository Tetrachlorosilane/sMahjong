#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style-reward-gate-table.py —— 把两条臂的终点闸门读数排成一张表。

数据来源：`S:\\mahjong-training\\style-reward\\v4-sty-<arm>-end-gate.log`（`v4.gate` 的原样输出）
与 `S:\\mahjong-training\\gate\\v4-sty-<arm>-end\\verdict.json`（判决的机器可读版）。
⛔ 本脚本**不重算**任何统计：判决是 `gate.py` 给的，这里只是把逐套 + 合并的数抄成表。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

S = Path(r"S:\mahjong-training")
LOGS = S / "style-reward"


def parse_log(arm: str) -> dict:
    p = LOGS / f"v4-sty-{arm}-end-gate.log"
    out: dict = {"arm": arm, "log": str(p), "walls": [], "pooled": {}}
    if not p.is_file():
        return out
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    cur: dict | None = None
    for ln in lines:
        m = re.match(r"\s*牌山 (\d+)：Δ=\s*([+-][\d.]+) CI\[\s*([+-][\d.]+),\s*([+-][\d.]+)\] "
                     r"n=(\d+)（候选 − 现任）(?: · 护栏 rank_points Δ=([+-][\d.]+))?", ln)
        if m:
            cur = {"seed": int(m.group(1)), "deals_delta": float(m.group(2)),
                   "deals_lo": float(m.group(3)), "deals_hi": float(m.group(4)),
                   "n": int(m.group(5)),
                   "rank_delta": float(m.group(6)) if m.group(6) else None,
                   "policies": {}}
            out["walls"].append(cur)
            continue
        # 自对弈汇总表：`<策略串> <场数> <平均顺位> <平均顺位点> <和了率> <放铳率> <平均收支> <平均打点>`
        cells = ln.split()
        if cur is not None and len(cells) >= 7 and cells[0].startswith("net:") and cells[1].isdigit():
            cur["policies"][cells[0]] = {
                "games": int(cells[1]), "place": float(cells[2]),
                "rank_points": float(cells[3]),
                "win_rate": float(cells[4].rstrip("%")) / 100.0,
                "deal_rate": float(cells[5].rstrip("%")) / 100.0,
                "avg_delta": float(cells[6]),
                "avg_win_score": float(cells[7]) if len(cells) > 7 else None}
        m2 = re.search(r"护栏（rank_points）：Δ=([+-][\d.]+) CI\[([+-][\d.]+),([+-][\d.]+)\]", ln)
        if m2:
            out["pooled"].update({"guard_delta": float(m2.group(1)),
                                  "guard_lo": float(m2.group(2)),
                                  "guard_hi": float(m2.group(3))})
        m3 = re.match(r"\s*合并判决：主口径（deals）Δ=([+-][\d.]+) CI\[\s*([+-][\d.]+),([+-][\d.]+)\]",
                      ln)
        if m3:
            out["pooled"].update({"deals_delta": float(m3.group(1)),
                                  "deals_lo": float(m3.group(2)),
                                  "deals_hi": float(m3.group(3))})
        m4 = re.search(r"套间散布：极差 ([\d.]+) / 合并 CI 半宽 ([\d.]+)", ln)
        if m4:
            out["pooled"].update({"wall_spread": float(m4.group(1)),
                                  "ci_half": float(m4.group(2))})
    v = S / "gate" / f"v4-sty-{arm}-end" / "verdict.json"
    if v.is_file():
        out["verdict"] = json.loads(v.read_text(encoding="utf-8"))
    return out


def wall_summary(rec: dict) -> dict:
    """一套牌山里 **候选 − 现任** 的顺位点/和了率/放铳率 差（同一套牌山，直接相减）。"""
    pol = rec.get("policies") or {}
    inc = [v for k, v in pol.items() if "v4-expert-defrep-g10" in k]
    cand = [v for k, v in pol.items() if "v4-sty-" in k]
    if not inc or not cand:
        return {}
    i, c = inc[0], cand[0]
    # 两边各坐 2 席 ⇒ summary 里已经是"每席平均"（场数 = 2× 套场次）
    return {"rank_delta_summary": c["rank_points"] - i["rank_points"],
            "win_delta": c["win_rate"] - i["win_rate"],
            "deal_delta": c["deal_rate"] - i["deal_rate"],
            "score_delta": c["avg_delta"] - i["avg_delta"],
            "cand": c, "inc": i}


def main() -> int:
    ap = argparse.ArgumentParser(prog="style-reward-gate-table.py")
    ap.add_argument("--arms", default="a,b")
    ap.add_argument("--json", default=str(LOGS / "gate-table.json"))
    ap.add_argument("--md", default=str(LOGS / "gate-table.md"))
    args = ap.parse_args()
    arms = [x.strip() for x in args.arms.split(",") if x.strip()]
    payload = {}
    md = ["| 臂 | 套（牌山） | 主口径 deals Δ（候选−现任） | CI | 护栏 rank_points Δ | 候选放铳率 | 现任放铳率 "
          "| 候选和了率 | 现任和了率 | 候选顺位点 | 现任顺位点 | 候选平均打点 |",
          "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm in arms:
        rec = parse_log(arm)
        payload[arm] = rec
        for w in rec["walls"]:
            s = wall_summary(w)
            c, i = s.get("cand", {}), s.get("inc", {})
            md.append("| {arm} | {seed} | {d:+.2f} | [{lo:+.2f},{hi:+.2f}] | {r} | {cd} | {id_} | {cw} | {iw} "
                      "| {cr} | {ir} | {cs} |".format(
                          arm=arm, seed=w["seed"], d=w["deals_delta"], lo=w["deals_lo"],
                          hi=w["deals_hi"],
                          r=("%+.2f" % w["rank_delta"]) if w.get("rank_delta") is not None else "n/a",
                          cd=("%.2f%%" % (100 * c["deal_rate"])) if c else "n/a",
                          id_=("%.2f%%" % (100 * i["deal_rate"])) if i else "n/a",
                          cw=("%.2f%%" % (100 * c["win_rate"])) if c else "n/a",
                          iw=("%.2f%%" % (100 * i["win_rate"])) if i else "n/a",
                          cr=("%+.2f" % c["rank_points"]) if c else "n/a",
                          ir=("%+.2f" % i["rank_points"]) if i else "n/a",
                          cs=("%.0f" % c["avg_win_score"]) if c and c.get("avg_win_score") else "n/a"))
        p = rec.get("pooled") or {}
        if "deals_delta" in p:
            md.append(f"| **{arm} 合并** | 4 套 × 3000 | **{p['deals_delta']:+.2f}** "
                      f"| [{p['deals_lo']:+.2f},{p['deals_hi']:+.2f}] | "
                      f"{(('%+.2f' % p['guard_delta']) + ' [' + ('%+.2f' % p['guard_lo']) + ',' + ('%+.2f' % p['guard_hi']) + ']') if 'guard_delta' in p else 'n/a'} "
                      f"| | | | | | | |")
        v = rec.get("verdict") or {}
        if v:
            md.append(f"| {arm} 判决 | | adopt={v.get('adopt')} guard_ok={v.get('guard_ok')} "
                      f"| | | | | | | | | | |")
    text = "\n".join(md) + "\n"
    print(text)
    Path(args.md).write_text(text, encoding="utf-8")
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[落盘] {args.md}\n[落盘] {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
