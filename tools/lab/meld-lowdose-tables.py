#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""meld-lowdose-tables.py —— **低剂量带**（`meld` 轴）的读表器：位移—剂量 + 代价—剂量。

⛔ 不重算任何风格量，也**不走 `w-ladder.py measure`**（`measure()` 会 glob **全部** `traces/*`
并给"轨迹已删、也没有缓存 JSON"的老 tag 报错 —— 本任务的读数不需要它）。三份输入：
  ① `style-vector-per-gen/<tag>.json` —— `tools/style-vector.py` 的口径（逐代 + POST）；
  ② `paired/<label>-gNN-POST.json`   —— `w-ladder.py post` 落的**同 seed 逐场配对**账；
  ③ `<traces>/summary.json` 的 `per_game[]` —— **采集侧逐场配对**（和了/放铳/打点/顺位点）。

为什么 ③ 要自己算：`w-ladder.py` 的 `PAIR_AXES` 里没有 `rank_points` / `wins_per_game`（它们是
**采集口径**的计数），而本任务的代价表要"采集顺位点"的**配对 Δ + CI** 与 `wins` 护栏的
**同量纲换算**（0.10/场/席）—— 只能在 `per_game[]` 上逐场配对，⛔ 不能拿两次的 `mean ± SE` 当配对读数。

用法：python meld-lowdose-tables.py --doses 3030:mid:v4-wl-meld3030-4g-c1,1515:low:v4-wl-meld1515-4g-c1,6060:ref:v4-wl-meld-4g-c1
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

S = Path(r"S:\mahjong-training")
STAGE = S / "w-ladder"
PAIRED = STAGE / "paired"
SVEC = STAGE / "style-vector-per-gen"
TRACES = STAGE / "traces"
TRACES_POST = STAGE / "traces-post"
Z = 1.96
#: 预注册容忍带（`PREREGISTRATION-MELD.md` §3 护栏 `wins`）：**每场每席和了次数**。
TOL_WINS = 0.10
MAIN = "meld_rate"
COST = ("riichi_rate", "win_rate", "deal_rate", "riichi_win_rate", "avg_win_score")
DIMS = ("riichi_rate", "meld_rate", "win_rate", "deal_rate", "avg_win_score", "avg_win_turn",
        "avg_riichi_turn", "dama_rate", "riichi_win_rate")


def f_pct(x, nd=3):
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def f_num(x, nd=1):
    return "n/a" if x is None else f"{x:,.{nd}f}"


def load_json(p: Path):
    return json.loads(Path(p).read_text(encoding="utf-8")) if Path(p).is_file() else None


def student(svec_json: Path) -> dict:
    """`style-vector.py` 的**学生**行（判据同 `w-ladder.student_row`：策略串里带 `@` 的那个）。"""
    sv = load_json(svec_json)
    if not sv:
        return {}
    for name, st in (sv.get("policies") or {}).items():
        if "@" in name:
            return {"policy": name, **{k: (st.get("dims") or {}).get(k) for k in DIMS},
                    "seat_hands": (st.get("counts") or {}).get("seat_hands"),
                    "counts": st.get("counts") or {}, "checks_all_ok": sv.get("checks_all_ok")}
    return {}


def per_game_collect(tdir: Path) -> dict:
    """学生席的**逐场**采集读数（`rank_points`/`wins`/`deals`/`win_points`/`seat_hands`）。

    ⛔ 学生席由 `policies[]` 里带 `@` 的下标决定（座位每场轮转）—— 与 `w-ladder.collect_row()`
    **同一条判据**（这里只是把逐场值也留下做配对）。
    """
    s = load_json(Path(tdir) / "summary.json")
    if not s:
        return {}
    key = next((k for k in (s.get("by_policy") or {}) if "@" in k), None)
    if key is None:
        return {}
    out = {}
    for pg in s.get("per_game") or []:
        try:
            i = pg["policies"].index(key)
        except (KeyError, ValueError):
            continue
        sh = pg.get("seat_hands")
        out[int(pg["game"])] = {
            "seed": int(pg.get("seed", -1)), "rank_points": float(pg["rank_points"][i]),
            "wins": float(pg["wins"][i]), "deals": float(pg["deals"][i]),
            "win_points": float(pg["win_points"][i]),
            "seat_hands": float(sh[i]) if sh else None,
            "rank": float(pg["placement"][i]) if pg.get("placement") else None}
    return out


def paired_delta(a: dict, b: dict, key: str) -> dict:
    """逐场配对 Δ = `b − a`（同场号、**且 seed 必须一致**；不一致的场丢掉并计数）。"""
    diffs, bad = [], 0
    for g in sorted(set(a) & set(b)):
        if a[g]["seed"] != b[g]["seed"]:
            bad += 1
            continue
        if a[g].get(key) is None or b[g].get(key) is None:
            continue
        diffs.append(b[g][key] - a[g][key])
    if len(diffs) < 2:
        return {}
    m = statistics.fmean(diffs)
    se = statistics.stdev(diffs) / math.sqrt(len(diffs))
    return {"delta": m, "se": se, "n": len(diffs), "seed_mismatch": bad,
            "lo": m - Z * se, "hi": m + Z * se, "t": (m / se if se else float("nan"))}


def collect_stats(pg: dict) -> dict:
    out = {}
    for k in ("rank_points", "wins", "deals", "win_points"):
        v = [x[k] for x in pg.values() if x.get(k) is not None]
        if len(v) > 1:
            out[k] = statistics.fmean(v)
            out[k + "_se"] = statistics.stdev(v) / math.sqrt(len(v))
    sh = [x["seat_hands"] for x in pg.values() if x.get("seat_hands") is not None]
    if sh:
        out["seat_hands"] = statistics.fmean(sh)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="meld-lowdose-tables.py")
    ap.add_argument("--doses", required=True, help="w:short:label[,...]（标签可省 = 按 w 拼）")
    ap.add_argument("--gens", default="1,2,3,4")
    a = ap.parse_args()
    doses = []
    for item in a.doses.split(","):
        if not item.strip():
            continue
        parts = item.split(":")
        w, short = float(parts[0]), parts[1].strip()
        lab = (parts[2].strip() if len(parts) > 2 and parts[2].strip()
               else f"v4-wl-meld{w:g}-4g-c1")
        doses.append((w, short, lab))
    gens = [int(x) for x in a.gens.split(",") if x.strip()]

    def gen_row(lab, g):
        """逐代（训练后）行：标签 + 代 ⇒ 风格向量 + 采集读数。"""
        tag = f"{lab}-g{g:02d}"
        return {"tag": tag, "dir": TRACES / tag, "style": student(SVEC / f"{tag}.json"),
                "phase": "⛔训练前（g01 = --init）" if g == 1 else f"✓训练后（训过 {g - 1} 代）"}

    def post_row(lab, n=4):
        tag = f"{lab}-g{n:02d}-POST"
        return {"tag": tag, "dir": TRACES_POST / f"{lab}-g{n:02d}",
                "style": student(SVEC / f"{tag}.json"),
                "phase": f"✓训练后（末代 {n} 代权重**补采**，主读数）"}

    def line(w, short, lab, r):
        st, co = r["style"], collect_stats(per_game_collect(r["dir"]))
        rp = ("n/a" if co.get("rank_points") is None
              else f"{co['rank_points']:.2f} ({co.get('rank_points_se', float('nan')):.2f})")
        wp = ("n/a" if co.get("wins_per_game") is None and co.get("wins") is None
              else f"{co['wins']:.3f} ({co.get('wins_se', float('nan')):.3f})")
        print(f"| {short} | {w:g} | `{lab}` | {r['tag'].rsplit('-', 1)[-1]} | {r['phase']} "
              f"| **{f_pct(st.get(MAIN))}** | {f_pct(st.get('riichi_rate'))} "
              f"| {f_pct(st.get('win_rate'))} | {f_pct(st.get('deal_rate'))} "
              f"| {f_num(st.get('avg_win_score'), 0)} | {rp} | {wp} |")

    # ---------- A. 逐代（训练后） ----------
    have = [student(SVEC / f"{lab}-g{g:02d}.json") for _w, _s, lab in doses for g in gens]
    have = [h for h in have if h]
    n_ok = sum(1 for h in have if h.get("checks_all_ok"))
    print(f"[口径] 风格量 = `style-vector-per-gen/*.json`（`tools/style-vector.py`）——"
          f"已有 {len(have)} 份，`checks_all_ok` 全过 = {n_ok == len(have) and bool(have)}\n")
    print("## A. 逐代（**训练后**）副露率 + 代价（学生席；口径 = `style-vector.py`）\n")
    print("| 档 | w | 链 | 行 | 口径 | **副露率** | 立直率 | 和了率 | 放铳率 | 平均打点 "
          "| 采集顺位点 (SE) | 每场和了 (SE) |")
    print("| --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for w, short, lab in doses:
        got = False
        for g in gens:
            r = gen_row(lab, g)
            if r["style"]:
                line(w, short, lab, r)
                got = True
        p = post_row(lab)
        if p["style"]:
            line(w, short, lab, p)
            got = True
        if not got:
            print(f"| {short} | {w:g} | `{lab}` | — | （还没有读数） |  |  |  |  |  |  |  |")
        print("|  |  |  |  |  |  |  |  |  |  |  |  |")

    # ---------- B. 位移—剂量 ----------
    print("\n## B. ① 位移—剂量：末代 `POST` vs `g01` 的**同 seed 逐场配对** Δ（副露率）\n")
    print("| 档 | w | 链 | Δ副露率 | SE | t | 95% CI | ≥+2pp 且排除 0 | 末代副露率(配对口径) |")
    print("| --- | ---: | --- | ---: | ---: | ---: | --- | --- | ---: |")
    disp = []
    for w, short, lab in doses:
        pj = load_json(PAIRED / f"{lab}-g04-POST.json")
        if not pj:
            print(f"| {short} | {w:g} | `{lab}` | （缺配对账） | — | — | — | — | — |")
            continue
        v = pj["paired_delta"][MAIN]
        ok = v["delta_pp"] >= 2.0 and v["lo_pp"] > 0
        disp.append((w, short, v["delta_pp"], v["se_pp"], v["post"]))
        print(f"| {short} | {w:g} | `{lab}` | **{v['delta_pp']:+.2f}pp** | {v['se_pp']:.2f} "
              f"| {v['t']:+.2f} | [{v['lo_pp']:+.2f},{v['hi_pp']:+.2f}] | {'✓' if ok else '✗'} "
              f"| {f_pct(v['post'])} |")
    print("\n**边际收益**（相邻档之间，每 +1000 剂量换来的 pp）：\n")
    print("| 区间 | Δw | Δ副露率 | **pp / +1000 剂量** |")
    print("| --- | ---: | ---: | ---: |")
    for (w0, s0, d0, _, _), (w1, s1, d1, _, _) in zip(disp, disp[1:]):
        if w1 == w0:
            continue
        print(f"| {s0}→{s1} | +{w1 - w0:.0f} | {d1 - d0:+.2f}pp "
              f"| **{(d1 - d0) / ((w1 - w0) / 1000):+.2f}** |")

    # ---------- C. 代价—剂量 ----------
    print("\n## C. ② 代价—剂量：末代 `POST` vs `g01`（风格侧**逐场配对** + 采集侧**逐场配对**）\n")
    print("| 档 | w | 轴 | 训练前 | 训练后 | Δ | SE | t | 95% CI | 判读 |")
    print("| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |")
    guard = []
    for w, short, lab in doses:
        pj = load_json(PAIRED / f"{lab}-g04-POST.json")
        pa = per_game_collect(TRACES / f"{lab}-g01")
        pb = per_game_collect(TRACES_POST / f"{lab}-g04")
        if pj:
            for name in COST:
                v = (pj["paired_delta"] or {}).get(name)
                if not v:
                    continue
                unit = "pp" if v["unit"] == "pct" else "点"
                mul, dec = (100, 3) if v["unit"] == "pct" else (1, 0)
                worse = v["hi_pp"] < 0
                better = v["lo_pp"] > 0
                if name == "deal_rate":
                    worse, better = better, worse
                if name == "riichi_rate":
                    # ⚠ 立直率**不是**质量轴：买副露卖立直是**预期**的取舍 ⇒ 只报方向，不判好坏。
                    sig = "显著 ↓" if better else ("显著 ↑" if worse else "含 0")
                else:
                    sig = "**显著变差**" if worse else ("**显著变好**" if better else "含 0")
                print(f"| {short} | {w:g} | {name} | {v['pre'] * mul:.{dec}f} "
                      f"| {v['post'] * mul:.{dec}f} | **{v['delta_pp']:+.2f}{unit}** "
                      f"| {v['se_pp']:.2f} | {v['t']:+.2f} | [{v['lo_pp']:+.2f},{v['hi_pp']:+.2f}] "
                      f"| {sig} |")
        if pa and pb:
            for key, nm, nd in (("rank_points", "采集顺位点", 2), ("wins", "每场和了次数", 3),
                                ("deals", "每场放铳次数", 3), ("win_points", "每场和了点", 0)):
                pd = paired_delta(pa, pb, key)
                if not pd:
                    continue
                verdict = ""
                if key == "wins":
                    verdict = ("**破护栏**" if pd["hi"] <= -TOL_WINS else
                               "贴带（CI 跨 −0.10）" if pd["lo"] < -TOL_WINS else "**在带内**")
                ma = statistics.fmean([x[key] for x in pa.values()])
                mb = statistics.fmean([x[key] for x in pb.values()])
                print(f"| {short} | {w:g} | {nm} *(采集·配对)* | {ma:.{nd}f} | {mb:.{nd}f} "
                      f"| **{pd['delta']:+.{nd}f}** | {pd['se']:.{nd}f} | {pd['t']:+.2f} "
                      f"| [{pd['lo']:+.{nd}f},{pd['hi']:+.{nd}f}] | {verdict} |")
                if key == "wins":
                    guard.append((w, short, pd))
        print("|  |  |  |  |  |  |  |  |  |  |")

    # ---------- D. wins 护栏：两种算法互相核对 ----------
    print(f"\n## D. `wins` 护栏（预注册容忍带 **{TOL_WINS}/场/席**）—— 两种算法互相核对\n")
    print("| 档 | w | ① 采集逐场配对 Δ(和了次数/场) | 95% CI | ② 由和了率 Δ × 每场小局数换算 "
          "| 每场每席小局数 | 破带？ |")
    print("| --- | ---: | ---: | --- | ---: | ---: | --- |")
    for w, short, lab in doses:
        pd = next((x[2] for x in guard if x[0] == w), None)
        pj = load_json(PAIRED / f"{lab}-g04-POST.json")
        pa = per_game_collect(TRACES / f"{lab}-g01")
        hs = [x["seat_hands"] for x in pa.values() if x.get("seat_hands") is not None]
        # ⚠ `summary.json` 的 `per_game[]` **不带** `seat_hands` ⇒ 退回到 `style-vector` 的整数账
        #   （`counts.seat_hands` / 场数）；两个来源都对不上就不出换算值（⛔ 不编数）。
        hands = statistics.fmean(hs) if hs else None
        if hands is None and pa:
            sh = (student(SVEC / f"{lab}-g01.json").get("counts") or {}).get("seat_hands")
            if sh:
                hands = float(sh) / len(pa)
        conv = None
        if pj:
            wr = (pj["paired_delta"] or {}).get("win_rate")
            if wr and hands:
                conv = wr["delta_pp"] / 100.0 * hands
        if pd is None:
            print(f"| {short} | {w:g} | （缺） | — | "
                  f"{'n/a' if conv is None else f'{conv:+.3f}'} | "
                  f"{'n/a' if hands is None else f'{hands:.2f}'} | — |")
            continue
        broke = ("**破**（CI 上界 ≤ −0.10）" if pd["hi"] <= -TOL_WINS else
                 "贴带（下界 < −0.10 ≤ 上界）" if pd["lo"] < -TOL_WINS else "**不破**")
        print(f"| {short} | {w:g} | **{pd['delta']:+.3f}** | [{pd['lo']:+.3f},{pd['hi']:+.3f}] | "
              f"{'n/a' if conv is None else f'{conv:+.3f}'} | "
              f"{'n/a' if hands is None else f'{hands:.2f}'} | {broke} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
