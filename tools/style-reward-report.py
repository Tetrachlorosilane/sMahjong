#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style-reward-report.py —— 风格奖励塑形受控实验的**读数表**（只读，不改任何判据）。

它把三份现成的产物拼成一张表：
  ① 每代轨迹 → 交给 **`tools/style-vector.py`**（独立实现）读 7 维风格向量；
  ② 每代 2+2 评测（`raw/eval-<tag>/summary.json`）→ 顺位点 / 和了率 / 放铳率 / 打点；
  ③ 终点的闸门判决（`gate/<tag>/s<seed>/summary.json`，由 `v4.gate` 写的）→ 主口径 + 护栏。

⛔ 本脚本**不是判据**：判决只看 `mahjong_ml/v4/gate.py`。这里只做"把数排成一张表"。
⚠ 它**不**自己算风格量（那是 `tools/style-vector.py` 的活，判据侧独立实现）—— 只解析它的 JSON。

用法：

    python tools/style-reward-report.py --traces <轨迹副本根> --labels v4-sty-a,v4-sty-b \\
        --start v4-sty-a-g00 --json out.json --md out.md
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = Path(__import__("os").environ.get("MAHJONG_DATA_ROOT", r"S:\mahjong-training"))

#: 要看的两臂（label 前缀）→ 该臂的 `--style-bonus` 口径（**从 manifest 读**，不写死）。
STYLE_COMPONENTS = ("riichi_rate", "meld_rate", "win_rate", "deal_rate", "avg_win_score",
                    "avg_win_turn", "dama_rate")


def read_summary(d: Path) -> dict:
    f = d / "summary.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def style_vector(traces: list[Path], out_prefix: Path, py: str) -> dict:
    """跑 `tools/style-vector.py`（**独立实现**，自带与 summary 的逐项对账）。"""
    cmd = [py, str(REPO / "tools" / "style-vector.py"), *[str(t) for t in traces],
           "--json", str(out_prefix) + ".json"]
    p = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"⛔ style-vector 失败（退出码 {p.returncode}）：\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    return json.loads((Path(str(out_prefix) + ".json")).read_text(encoding="utf-8"))


def eval_row(tag: str) -> dict:
    """该代的读数：优先读 2+2 评测的 `summary.json`；**它已被 `run-league.ps1` 轮换删掉**时，
    从 `release/<tag>.log` 里把两张现成的表解析出来（**不重算、不猜**）。

    为什么必须有日志这条兜底（实测）：`run-league.ps1` 在每代结束时删
    `raw/<tag>`、`compact/<tag>`、`raw/eval-<tag>` —— 评测表只活在日志里。日志里有两张：
    · **采集**（1000 场）自对弈汇总表 → 学生那行：平均顺位 / 平均顺位点 / 和了率 / 放铳率 / 打点；
    · **评测**（200 场，`-eval-vs prev`）的 `判据：Δ=… 顺位点 [lo,hi]` 行 → 这一代 vs **上一代**。
    """
    d = DATA / "raw" / f"eval-{tag}"
    s = read_summary(d)
    if s:
        by = s.get("by_policy") or {}
        cand = [k for k in by if k.startswith("net:") and tag in k]
        lab = cand[0] if cand else None
        row: dict = {"source": "summary.json", "eval_dir": str(d),
                     "games": s.get("games"), "hands": s.get("hands")}
        if lab and lab in by:
            st = by[lab]
            row.update({"label": lab, "win_rate": st.get("win_rate"),
                        "deal_in_rate": st.get("deal_in_rate"),
                        "avg_win_score": st.get("avg_win_score"),
                        "avg_rank_points": st.get("avg_rank_points"),
                        "avg_place": st.get("avg_place")})
        return row
    log = REPO / "release" / f"{tag}.log"
    if not log.is_file():
        return {}
    text = log.read_text(encoding="utf-8", errors="replace")
    row = {"source": "release-log", "log": str(log)}
    # ① 采集自对弈汇总表：表头行之后，找**学生**那一行（学生串是唯一带 `@` 的）
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("策略") and "平均顺位点" in ln:
            for j in range(i + 1, min(i + 12, len(lines))):
                cells = lines[j].split()
                if len(cells) < 7:
                    break
                if "@" not in cells[0]:
                    continue
                row.update({"collect_label": cells[0], "collect_games": int(cells[1]),
                            "collect_player": float(cells[2]), "collect_rank_points": float(cells[3]),
                            "collect_win_rate": float(cells[4].rstrip("%")) / 100.0,
                            "collect_deal_rate": float(cells[5].rstrip("%")) / 100.0,
                            "collect_avg_delta": float(cells[6]),
                            "collect_avg_win_score": float(cells[7]) if len(cells) > 7 else None})
                break
            break
    # ② 评测判据行（这一代 vs 上一代）—— ⚠ 必须锚在 `✓ 2+2 评测` **之后**那一行：
    #   日志里还有一条**值头判据**也以 `判据：` 开头（`judge()` 打印的是"顺位点"那条，
    #   而值头那条打印的是 ✅/❌ 值头闸门），拿第一条会取错（第一版就这么取了值头那条）。
    for i, ln in enumerate(lines):
        if "2+2 评测" in ln or "2+2 评测（" in ln:
            for j in range(i + 1, min(i + 6, len(lines))):
                if lines[j].strip().startswith("判据：Δ="):
                    row["eval_line"] = lines[j].strip()
                    break
            if "eval_line" in row:
                break
    if "eval_line" not in row:
        for ln in lines:
            if ln.strip().startswith("判据：Δ="):
                row["eval_line"] = ln.strip()
                break
    return row


def gate_row(tag: str) -> dict:
    """闸门判决（`gate/<tag>/verdict.json`，由 `v4.gate` 写）—— **只读，不重算**。"""
    f = DATA / "gate" / tag / "verdict.json"
    if not f.is_file():
        return {}
    v = json.loads(f.read_text(encoding="utf-8"))
    return {k: v.get(k) for k in ("metric", "delta", "lo", "hi", "p", "n", "adopt",
                                  "guard_metric", "guard_delta", "guard_lo", "guard_hi",
                                  "guard_ok", "wall_deltas", "wall_spread")}


def fmt(x, pct=False, nd=2):
    if x is None:
        return "n/a"
    return f"{100 * x:.{nd}f}%" if pct else f"{x:.{nd}f}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="style-reward-report.py")
    ap.add_argument("--traces", default=str(DATA / "style-reward" / "traces"))
    ap.add_argument("--labels", required=True, help="两个 label 前缀，逗号分隔（例 v4-sty-a,v4-sty-b）")
    ap.add_argument("--generations", type=int, default=4)
    ap.add_argument("--start-tag", default="", help="起点那一代（它没有轨迹，只在表里占一行）")
    ap.add_argument("--py", default=r"S:\mahjong-training\tools\py312\python.exe")
    ap.add_argument("--json", default="")
    ap.add_argument("--md", default="")
    args = ap.parse_args(argv)

    traces_root = Path(args.traces)
    labels = [x.strip() for x in args.labels.split(",") if x.strip()]
    payload: dict = {"tool": "tools/style-reward-report.py",
                     "warning": "⛔ 不是判据：判决只看 mahjong_ml/v4/gate.py。本文件只是把数排成表。",
                     "labels": labels, "arms": {}}
    stages = Path(traces_root).parent / "style-vector-per-gen"
    stages.mkdir(parents=True, exist_ok=True)

    md: list[str] = ["# 风格奖励塑形受控实验 · 读数表", "",
                     "⛔ 判决只看 `mahjong_ml/v4/gate.py`；本表是**描述性方向读数** + 现成的判决摘录。", ""]
    for lab in labels:
        arm: dict = {"generations": [], "start": args.start_tag or None}
        md += [f"## 臂 `{lab}`", ""]
        md += ["| 代 | 轨迹小局 | 立直率 | 副露率 | 和了率 | 放铳率 | 平均打点 | 平均和了巡 | 默听率 "
               "| 采集顺位点 | 采集和了率 | 采集放铳率 | 评测（vs 上一代） |",
               "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
        for g in range(1, args.generations + 1):
            tag = f"{lab}-g{g:02d}"
            tdir = traces_root / tag
            sv: dict = {}
            if tdir.is_dir():
                sv = style_vector([tdir], stages / tag, args.py)
            counts = {}
            dims = {}
            tot_hands = 0
            for name, st in (sv.get("policies") or {}).items():
                # 只看**学生**那一行：本实验的学生串是 `net:<路径>@0#<T>`（带 `@0#` 后缀），
                # 而风格桌上的对手串是 `net:<路径>`（不带后缀）⇒ 判据就落在"带不带 `@`"上。
                # ⚠ 不用 `startswith("net:")`：那会把三个对手也算进来（第一版就是这么写的）。
                if "@" not in name:
                    continue
                counts, dims = st["counts"], st["dims"]
                tot_hands = counts.get("seat_hands")
            ev = eval_row(tag)
            row = {"tag": tag, "traces": str(tdir), "counts": counts, "dims": dims, "eval": ev,
                   "style_vector_json": str(stages / f"{tag}.json") if sv else None}
            arm["generations"].append(row)
            md.append("| {tag} | {h} | {a} | {b} | {c} | {d} | {e} | {f} | {g} | {rp} | {wr} | {dr} | {el} |".format(
                tag=tag, h=tot_hands or "n/a",
                a=fmt(dims.get("riichi_rate"), True), b=fmt(dims.get("meld_rate"), True),
                c=fmt(dims.get("win_rate"), True), d=fmt(dims.get("deal_rate"), True),
                e=fmt(dims.get("avg_win_score"), False, 0), f=fmt(dims.get("avg_win_turn")),
                g=fmt(dims.get("dama_rate"), True),
                rp=fmt(ev.get("collect_rank_points") if ev.get("collect_rank_points") is not None
                       else ev.get("avg_rank_points")),
                wr=fmt(ev.get("collect_win_rate") if ev.get("collect_win_rate") is not None
                       else ev.get("win_rate"), True),
                dr=fmt(ev.get("collect_deal_rate") if ev.get("collect_deal_rate") is not None
                       else ev.get("deal_in_rate"), True),
                el=(ev.get("eval_line") or "n/a").replace("|", "/")))
        # 终点判决（逐代闸门在 -NoGate 下没有；这里读的是可选的手工/后续闸门产物）
        arm["gate"] = {f"{lab}-g{g:02d}": gate_row(f"{lab}-g{g:02d}")
                       for g in range(1, args.generations + 1)}
        payload["arms"][lab] = arm
        md += [""]

    text = "\n".join(md) + "\n"
    print(text)
    if args.md:
        Path(args.md).write_text(text, encoding="utf-8")
        print(f"[落盘] {args.md}")
    if args.json:
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"[落盘] {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
