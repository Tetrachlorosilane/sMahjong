#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""meld-collect.py —— 只读**摘要**（`summary.json`）的逐代采集读数（② 伴随量 / ③ 弱护栏）。

为什么单独一个脚本：链的**轨迹**（`g*.jsonl`，2.7 GB/代）在收尾时会被删掉，而
`summary.json` 里的**逐场**读数（`per_game[].rank_points/wins/deals/win_points`）**不依赖轨迹**
⇒ 用它做 ②③ 的逐代读数与"中途止损"检查最省（不扫 2.7 GB，也不与训练抢 CPU）。
口径**不另立一套**：学生席的判定与逐场标准误全部复用 `tools/w-ladder.py` 的 `collect_row()`。

用法：python meld-collect.py --labels v4-wl-meld-4g-c1,v4-wl-meld-4g-c2 [--post]
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
STAGE = Path(r"S:\mahjong-training") / "w-ladder"
TRACES_POST = STAGE / "traces-post"

sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "python" / ".venv" / "Lib" / "site-packages"))

_spec = importlib.util.spec_from_file_location("wladder", REPO / "tools" / "w-ladder.py")
wl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wl)

HEAD = ("| 链 | 行 | 学生席小局 | 采集顺位点 (SE) | 每场和了 (SE) | 每场放铳 (SE) | "
        "每场和了点 (SE) | 和了率 | 放铳率 | 平均打点 |")
SEP = "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"


def cells(co: dict) -> str:
    def num(k, nd=3):
        v, se = co.get(k), co.get(k + "_se")
        return "n/a" if v is None else f"{v:.{nd}f} ({se:.{nd}f})"

    def pct(k):
        v = co.get(k)
        return "n/a" if v is None else f"{100 * v:.3f}%"

    pts = co.get("avg_win_score")
    return (f"{co.get('seat_hands')} | {num('rank_points', 2)} | {num('wins_per_game')} | "
            f"{num('deals_per_game')} | {num('win_points_per_game', 0)} | {pct('win_rate')} | "
            f"{pct('deal_in_rate')} | " + ("n/a" if pts is None else f"{pts:,.0f}") + " |")


def main() -> int:
    ap = argparse.ArgumentParser(prog="meld-collect.py")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--post", action="store_true", help="也读 traces-post/<label>-gNN")
    a = ap.parse_args()
    print(HEAD)
    print(SEP)
    for label in [x.strip() for x in a.labels.split(",") if x.strip()]:
        for g, tdir in wl._chain_tags(label):
            co = wl.collect_row(tdir)
            if co:
                print(f"| `{label}` | g{g:02d} | " + cells(co))
        if a.post:
            for d in sorted(TRACES_POST.glob(f"{label}-g[0-9][0-9]")):
                co = wl.collect_row(d)
                if co:
                    print(f"| `{label}` | **POST** | " + cells(co))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
