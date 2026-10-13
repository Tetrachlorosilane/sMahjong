# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""P2-11 最小判据 · 统计（零训练；只用公开观测 + 逐手账）。

读数（口径见 `P2-11-MINIMAL-PLAN.md`）：
  T0 机械自证   —— v4 前向复算 A0 逐行 argmax 必须 == 实际 chosen_index（全行）；据此混合臂 A0[D←A1]
                   在 **D 外**逐位等于基线；两网看同一 legal（n 一致 ⇒ 落位必然合法）。
  T2 结构读数   —— D（`obs.kind=="claim"`）上的**背离率** / 近平局率 / 覆盖率；D2（别家已立直 ∧ 自家出牌行）并列。
  T1-proxy      —— 两条**可玩**臂的逐小局配对账，**只在 D 手**上做交叉拟合（奇/偶场互评）的状态限制神谕：
                   样本外增益 + 配对 Δ/SE/95%CI + ρ(增益, A0 自身该小局得分) + 剔最差 X%。
                   ⚠ 上界代理：**不能**把增益归因到 D 决策（两臂在 D 外也不同）⇒ 不是预注册 T1。
⛔ 不做第二个统计实现：均值/SE/t/CI/按场聚类 SE 与 `_expA-analyze.py` 同一套公式。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

Z = 1.959963985
DRAW_BAND = lambda n: 0 if n <= 6 else (1 if n <= 12 else 2)


def ncdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def pearson(xs, ys) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return float("nan")
    return sxy / math.sqrt(sxx * syy)


def summarize(ds, games) -> dict:
    n = len(ds)
    if n == 0:
        return {"n": 0}
    m = sum(ds) / n
    sd = math.sqrt(sum((d - m) ** 2 for d in ds) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else float("nan")
    by = defaultdict(list)
    for d, g in zip(ds, games):
        by[g].append(d)
    gm = [sum(v) / len(v) for v in by.values()]
    ng = len(gm)
    mg = sum(gm) / ng
    sdg = math.sqrt(sum((x - mg) ** 2 for x in gm) / (ng - 1)) if ng > 1 else 0.0
    se_g = sdg / math.sqrt(ng) if ng > 1 else float("nan")
    return {"n": n, "mean": m, "sd": sd, "se": se,
            "t": m / se if se and se == se else float("nan"),
            "lo": m - Z * se, "hi": m + Z * se,
            "p2": 2 * (1 - ncdf(abs(m / se))) if se and se == se else float("nan"),
            "games": ng, "se_cluster": se_g,
            "t_cluster": m / se_g if se_g and se_g == se_g else float("nan"),
            "lo_cluster": m - Z * se_g, "hi_cluster": m + Z * se_g}


def load_jsonl(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_p2-11-analyze.py")
    ap.add_argument("--work", required=True)
    ap.add_argument("--primary-arm", default="riichi")
    ap.add_argument("--other-arm", default="meld")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--raw", action="append", default=[], help="臂=采集目录（配对自证，两次）")
    a = ap.parse_args(argv)
    work = Path(a.work)
    arm, other = a.primary_arm, a.other_arm
    rep: dict = {"primary_arm": arm, "other_arm": other}

    rows = load_jsonl(work / f"rows_{arm}.jsonl")
    tlA = load_jsonl(work / f"tl_{arm}_A0.jsonl")
    tlB = load_jsonl(work / f"tl_{arm}_A1.jsonl")
    if not (len(rows) == len(tlA) == len(tlB)):
        raise SystemExit(f"⛔ 行数不齐 rows={len(rows)} A0={len(tlA)} A1={len(tlB)}")

    kind = [str(r["kind"]) for r in rows]
    isD = [k == "claim" for k in kind]
    d2 = [k == "turn" and int(r["riichi_band"]) == 1 for k, r in zip(kind, rows)]
    div = [int(x["argmax"]) != int(y["argmax"]) for x, y in zip(tlA, tlB)]

    # ---------------- T0 机械自证 ----------------
    t0 = {
        "n_rows": len(rows),
        "align_bad": sum(1 for r, x in zip(rows, tlA) if int(r["chosen_index"]) != int(x["argmax"])),
        "step_bad": sum(1 for r, x in zip(rows, tlA) if int(r["step"]) != int(x["step"])),
        "nlegal_bad_A0": sum(1 for r, x in zip(rows, tlA) if int(r["nlegal"]) != int(x["n"])),
        "nlegal_mismatch_A0_A1": sum(1 for x, y in zip(tlA, tlB) if int(x["n"]) != int(y["n"])),
        "n_rows_nonD": sum(1 for b in isD if not b),
        "hybrid_nonD_mismatch": sum(1 for i, b in enumerate(isD)
                                    if (not b) and int(tlA[i]["argmax"]) != int(rows[i]["chosen_index"])),
        "n_rows_D": sum(1 for b in isD if b),
        "n_flip_rows_in_D": sum(1 for i, b in enumerate(isD) if b and div[i]),
    }
    rep["T0"] = t0

    # ---------------- T2 结构读数 ----------------
    def rate(mask, num=None):
        idx = [i for i, b in enumerate(mask) if b]
        if not idx:
            return {"n": 0}
        num = num or div
        return {"n": len(idx), "rate": sum(1 for i in idx if num[i]) / len(idx)}

    hands_rows: dict[tuple, list] = defaultdict(list)
    for i, r in enumerate(rows):
        hands_rows[(int(r["game"]), int(r["hand_no"]))].append(i)
    hands_with_D = [k for k, v in hands_rows.items() if any(isD[i] for i in v)]
    rep["T2"] = {
        "D_div": rate(isD),
        "D2_div": rate(d2),
        "nonD_div": rate([not b for b in isD]),
        "near_tie_le_0.5_in_D": (sum(1 for i, b in enumerate(isD) if b and float(tlA[i]["margin"]) <= 0.5)
                                 / max(1, sum(1 for b in isD if b))),
        "near_tie_le_0.5_in_nonD": (sum(1 for i, b in enumerate(isD) if (not b) and float(tlA[i]["margin"]) <= 0.5)
                                    / max(1, sum(1 for b in isD if not b))),
        "D_row_share": sum(1 for b in isD if b) / len(rows),
        "D_hand_share": len(hands_with_D) / max(1, len(hands_rows)),
        "n_hands": len(hands_rows),
        "n_hands_with_D": len(hands_with_D),
        "D_rows_per_D_hand": (sum(1 for b in isD if b) / max(1, len(hands_with_D))),
    }
    band_tab = {}
    for i, r in enumerate(rows):
        if not isD[i]:
            continue
        key = f"draw{int(r['draw_band'])}_riichi{int(r['riichi_band'])}"
        e = band_tab.setdefault(key, {"n": 0, "div": 0})
        e["n"] += 1
        e["div"] += 1 if div[i] else 0
    for k in band_tab:
        band_tab[k]["rate"] = band_tab[k]["div"] / band_tab[k]["n"]
    rep["T2"]["claim_by_band"] = band_tab

    # ---------------- 同一状态自证（两臂逐键 obs 指纹相同） ----------------
    rows_o = load_jsonl(work / f"rows_{other}.jsonl")
    key_o = {(int(r["game"]), int(r["hand_no"]), int(r["step"])): r for r in rows_o}
    sm_idx = [i for i, r in enumerate(rows)
              if (int(r["game"]), int(r["hand_no"]), int(r["step"])) in key_o
              and key_o[(int(r["game"]), int(r["hand_no"]), int(r["step"]))]["obs_hash"] == r["obs_hash"]]
    rep["same_state"] = {
        "n_matched": len(sm_idx),
        "share_of_rows": len(sm_idx) / len(rows),
        "p_div": (sum(1 for i in sm_idx if div[i]) / len(sm_idx)) if sm_idx else float("nan"),
        "p_div_in_D": (sum(1 for i in sm_idx if div[i] and isD[i]) / max(1, sum(1 for i in sm_idx if isD[i]))),
        "p_div_in_nonD": (sum(1 for i in sm_idx if div[i] and not isD[i]) / max(1, sum(1 for i in sm_idx if not isD[i]))),
        "action_agree_rate": (1 - (sum(1 for i in sm_idx if div[i]) / len(sm_idx))) if sm_idx else float("nan"),
    }

    # ---------------- 配对：逐小局账 ----------------
    hands = {}
    for tag in (arm, other):
        hj = json.loads((work / f"hands_{tag}.json").read_text(encoding="utf-8"))
        for h in hj["hands"]:
            hands.setdefault((int(h["game"]), int(h["hand_no"])), {})[tag] = h
    common = sorted(k for k, v in hands.items()
                    if arm in v and other in v
                    and v[arm].get("student_delta") is not None
                    and v[other].get("student_delta") is not None)
    rep["pairing"] = {
        "n_common_hands": len(common), "n_union": len(hands),
        "seat_mismatch": sum(1 for k in common
                             if hands[k][arm]["student_seat"] != hands[k][other]["student_seat"]),
        "seat_hist": {str(s): sum(1 for k in common if hands[k][arm]["student_seat"] == s) for s in range(4)},
    }
    if len(a.raw) == 2:
        sms = {}
        for spec in a.raw:
            tag, _, d = spec.partition("=")
            sms[tag] = json.loads((Path(d) / "summary.json").read_text(encoding="utf-8"))
        t1, t2 = list(sms)
        sp1 = json.loads((work / f"meta_{t1}.json").read_text(encoding="utf-8"))["student_policy"]
        sp2 = json.loads((work / f"meta_{t2}.json").read_text(encoding="utf-8"))["student_policy"]
        pg1 = {int(g["game"]): g for g in sms[t1]["per_game"]}
        pg2 = {int(g["game"]): g for g in sms[t2]["per_game"]}
        cg = sorted(set(pg1) & set(pg2))
        seed_bad = [g for g in cg if pg1[g]["seed"] != pg2[g]["seed"]]
        seat_bad, opp_bad = [], []
        for g in cg:
            s1 = pg1[g]["policies"].index(sp1) if sp1 in pg1[g]["policies"] else None
            s2 = pg2[g]["policies"].index(sp2) if sp2 in pg2[g]["policies"] else None
            if s1 != s2:
                seat_bad.append(g)
            if sorted(p for p in pg1[g]["policies"] if p != sp1) != \
               sorted(p for p in pg2[g]["policies"] if p != sp2):
                opp_bad.append(g)
        rep["pairing"]["summary_check"] = {
            "tags": [t1, t2], "seed_base": [sms[t1].get("seed_base"), sms[t2].get("seed_base")],
            "games_each": [len(pg1), len(pg2)], "games_common": len(cg),
            "seed_mismatch": len(seed_bad), "student_seat_mismatch": len(seat_bad),
            "opp_table_mismatch": len(opp_bad),
            "ok": bool(cg) and not seed_bad and not seat_bad and not opp_bad,
        }

    # ---------------- 逐手记录（D 手 / ¬D 手） ----------------
    recs = []
    for k in common:
        g, hn = k
        idxs = hands_rows.get(k, [])
        didx = [i for i in idxs if isD[i]]
        if didx:
            r0 = rows[didx[0]]
            cells = ((int(r0["draw_band"]), int(r0["riichi_band"])),
                     (int(r0["draw_band"]), int(r0["riichi_band"]), bool(r0["menzen"])),
                     (int(r0["draw_band"]), int(r0["riichi_band"]), bool(r0["menzen"]), min(len(didx), 2)))
            is_d_hand = True
        else:
            r0 = rows[idxs[0]] if idxs else {"draw_band": 0, "riichi_band": 0, "menzen": False}
            cells = ((int(r0["draw_band"]), int(r0["riichi_band"])),
                     (int(r0["draw_band"]), int(r0["riichi_band"]), bool(r0["menzen"])),
                     (int(r0["draw_band"]), int(r0["riichi_band"]), bool(r0["menzen"]), 0))
            is_d_hand = False
        recs.append({"k": k, "game": g, "hand_no": hn,
                     "dX": float(hands[k][arm]["student_delta"]),
                     "dY": float(hands[k][other]["student_delta"]),
                     "D": is_d_hand, "c1": cells[0], "c2": cells[1], "c3": cells[2],
                     "n_D_rows": len(didx),
                     "n_flip_rows": sum(1 for i in didx if div[i])})
    rep["hands"] = {"n_D_hands": sum(1 for r in recs if r["D"]),
                    "n_nonD_hands": sum(1 for r in recs if not r["D"]),
                    "n_D_hands_with_flip": sum(1 for r in recs if r["D"] and r["n_flip_rows"] > 0),
                    "D_rows_per_D_hand": (sum(r["n_D_rows"] for r in recs if r["D"])
                                          / max(1, sum(1 for r in recs if r["D"])))}

    # ---------------- T1-proxy：交叉拟合（只在 D 手上） ----------------
    def routing(sel, cells_key):
        sub = [r for r in recs if sel(r)]
        if not sub:
            return {"n": 0}
        out = {"n": len(sub), "games": len({r["game"] for r in sub}),
               "mean_dX": sum(r["dX"] for r in sub) / len(sub),
               "mean_dY": sum(r["dY"] for r in sub) / len(sub)}
        out["paired_XY"] = summarize([r["dX"] - r["dY"] for r in sub], [r["game"] for r in sub])
        out["oracle_hand"] = summarize([max(r["dX"], r["dY"]) - r["dX"] for r in sub],
                                       [r["game"] for r in sub])
        deltas, games, ins, ins_g = [], [], [], []
        for fold in (0, 1):
            fit = [r for r in sub if r["game"] % 2 != fold]
            ev = [r for r in sub if r["game"] % 2 == fold]
            if not fit or not ev:
                continue
            mA = sum(r["dX"] for r in fit) / len(fit)
            mB = sum(r["dY"] for r in fit) / len(fit)
            bench = "X" if mA >= mB else "Y"
            bycell = defaultdict(list)
            for r in fit:
                bycell[r[cells_key]].append(r["dX"] - r["dY"])
            pick = {c: ("X" if sum(v) / len(v) >= 0 else "Y") for c, v in bycell.items()}
            for r in ev:
                c = pick.get(r[cells_key], bench)
                deltas.append((r["dX"] if c == "X" else r["dY"]) - (r["dX"] if bench == "X" else r["dY"]))
                games.append(r["game"])
            bycell_all = defaultdict(list)
            for r in sub:
                bycell_all[r[cells_key]].append(r["dX"] - r["dY"])
            pick_all = {c: ("X" if sum(v) / len(v) >= 0 else "Y") for c, v in bycell_all.items()}
            mb = "X" if out["mean_dX"] >= out["mean_dY"] else "Y"
            for r in ev:
                c = pick_all[r[cells_key]]
                ins.append((r["dX"] if c == "X" else r["dY"]) - (r["dX"] if mb == "X" else r["dY"]))
                ins_g.append(r["game"])
        out["oos_router"] = summarize(deltas, games)
        out["insample_cell_oracle"] = summarize(ins, ins_g)
        out["n_cells"] = len({r[cells_key] for r in sub})
        out["cell_top1_share"] = (max(sum(1 for r in sub if r[cells_key] == c)
                                      for c in {r[cells_key] for r in sub}) / len(sub))
        return out

    rep["T1proxy"] = {
        "D_c1": routing(lambda r: r["D"], "c1"),
        "D_c2": routing(lambda r: r["D"], "c2"),
        "D_c3": routing(lambda r: r["D"], "c3"),
        "nonD_c2": routing(lambda r: not r["D"], "c2"),
    }
    dsel = [r for r in recs if r["D"]]
    rep["T1proxy"]["mean_reversion_D"] = {
        "n": len(dsel),
        "rho_oracle_gain_vs_dX": pearson([max(r["dX"], r["dY"]) - r["dX"] for r in dsel],
                                         [r["dX"] for r in dsel]),
        "rho_dX_vs_dY": pearson([r["dX"] for r in dsel], [r["dY"] for r in dsel]),
    }
    order = sorted(dsel, key=lambda r: r["dX"])
    rep["T1proxy"]["mean_reversion_D"]["trim_worst_dX"] = {
        str(p): {"n": len(order[int(len(order) * p / 100):]),
                 "gain": (sum(max(r["dX"], r["dY"]) - r["dX"] for r in order[int(len(order) * p / 100):])
                          / max(1, len(order[int(len(order) * p / 100):])))}
        for p in (0, 5, 10, 20, 30, 50)}

    (Path(a.out_json)).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
