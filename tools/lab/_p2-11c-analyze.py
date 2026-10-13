# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""P2-11c · 域 D 的正式判据统计（零训练）：三臂配对账 + 交叉拟合路由增益 + 均值回归。

三臂（同一 seed 族 `52870101` ⇒ 同牌山 ⇒ **逐场配对**）：
  A0 = `net:<wl-riichi>`（本轮新采）· A1 = `net:<wl-meld>`（既有产物复用）·
  H_D = `net:<A0>!net:<A1>@<cond>`（域掩码真跑）。

读数（口径 = `P2-11-FORMAL-PLAN.md` §3-§6，与候选① 的 `_p2-11b-analyze.py` **同一套公式**）：
  §1 配对自证：seed / 学生席 / 对手桌逐场一致 + A0 与上一轮 `raw/p2-11b/base` **逐位相同**
  §2 臂均值（场级 = `final_scores[学生席]`）
  §3 **F1(a)**：Δ = H − 最好的单臂，场级配对 Δ/SE/95%CI（判据 = CI 下界 > 0）
  §4 **F2**：D 上样本外路由增益（交叉拟合奇/偶场互评），≥2 种分层
  §5 对照 ¬D（应恒 0）· ρ（门槛 |ρ|<0.25）· 剔最差 X%
  §6 机械自证（domdiff / dforward 的摘要）
⛔ 不做第二个统计实现：均值/SE/t/CI/按场聚类 SE 与 `_p2-11-analyze.py` 同一套公式。
⚠ 每跑完一域**立刻落盘**（`--append-jsonl` 一行机器可读 + `--append-md` 一行人读表）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

Z = 1.959963985


def load_cond(src: str):
    p = Path(__file__).resolve().parent / "_p2-11c-cond.py"
    spec = importlib.util.spec_from_file_location("p2_11c_cond", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_cond(src)


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


def summarize(ds, games=None) -> dict:
    n = len(ds)
    if n == 0:
        return {"n": 0}
    m = sum(ds) / n
    sd = math.sqrt(sum((d - m) ** 2 for d in ds) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n) if n > 1 else float("nan")
    out = {"n": n, "mean": m, "sd": sd, "se": se,
           "t": m / se if se and se == se else float("nan"),
           "lo": m - Z * se, "hi": m + Z * se,
           "p2": 2 * (1 - ncdf(abs(m / se))) if se and se == se else float("nan")}
    if games:
        by = defaultdict(list)
        for d, g in zip(ds, games):
            by[g].append(d)
        gm = [sum(v) / len(v) for v in by.values()]
        ng = len(gm)
        mg = sum(gm) / ng
        sdg = math.sqrt(sum((x - mg) ** 2 for x in gm) / (ng - 1)) if ng > 1 else 0.0
        se_g = sdg / math.sqrt(ng) if ng > 1 else float("nan")
        out.update({"games": ng, "se_cluster": se_g,
                    "t_cluster": m / se_g if se_g and se_g == se_g else float("nan"),
                    "lo_cluster": m - Z * se_g, "hi_cluster": m + Z * se_g})
    return out


def fmt(s: dict, key: str = "mean") -> str:
    if not s or s.get("n", 0) == 0:
        return "n=0"
    return (f"{s[key]:+.1f} ± {s['se']:.1f} [{s['lo']:+.1f}, {s['hi']:+.1f}] (n={s['n']})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_p2-11c-analyze.py")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--cond", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--work-prev", required=True, help="上一轮 _p2-11-work（A1 = meld 的逐小局账）")
    ap.add_argument("--summary-base", required=True)
    ap.add_argument("--summary-mask", required=True)
    ap.add_argument("--summary-meld", required=True)
    ap.add_argument("--summary-base-prev", default="", help="raw/p2-11b/base/summary.json（逐位复现自证）")
    ap.add_argument("--meta-base", required=True)
    ap.add_argument("--meta-mask", required=True)
    ap.add_argument("--domdiff-json", default="")
    ap.add_argument("--dforward-json", default="")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--append-jsonl", default="")
    ap.add_argument("--append-md", default="")
    a = ap.parse_args(argv)

    cond = load_cond(a.cond)
    work, prev = Path(a.work), Path(a.work_prev)
    tag = a.tag
    sm = {k: json.loads(Path(p).read_text(encoding="utf-8")) for k, p in
          (("base", a.summary_base), ("mask", a.summary_mask), ("meld", a.summary_meld))}
    meta = {k: json.loads(Path(p).read_text(encoding="utf-8")) for k, p in
            (("base", a.meta_base), ("mask", a.meta_mask))}
    rep: dict = {"tag": tag, "cond": a.cond,
                 "arms": {k: {"seed_base": sm[k].get("seed_base"), "games": sm[k].get("games")}
                          for k in sm}}

    # ---------------- §1 配对自证 ----------------
    pg = {k: {int(g["game"]): g for g in sm[k]["per_game"]} for k in sm}
    stu_pol = {"base": meta["base"]["student_policy"], "mask": meta["mask"]["student_policy"],
               "meld": json.loads((prev / "meta_meld.json").read_text(encoding="utf-8"))["student_policy"]}
    for k in sm:
        rep["arms"][k]["student_policy"] = stu_pol[k]
    cg = sorted(set.intersection(*[set(pg[k].keys()) for k in sm]))
    seat = {}
    for k in sm:
        s = []
        for g in cg:
            pol = pg[k][g]["policies"]
            s.append(pol.index(stu_pol[k]) if stu_pol[k] in pol else -1)
        seat[k] = s
    seeds_eq = all(pg["base"][g]["seed"] == pg["mask"][g]["seed"] == pg["meld"][g]["seed"] for g in cg)
    seat_eq = all(seat["base"][i] == seat["mask"][i] == seat["meld"][i] for i in range(len(cg)))
    opp_eq = True
    for g in cg:
        o0 = sorted(p for p in pg["base"][g]["policies"] if p != stu_pol["base"])
        for k in ("mask", "meld"):
            ok_ = sorted(p for p in pg[k][g]["policies"] if p != stu_pol[k])
            if o0 != ok_:
                opp_eq = False
    rep["pairing"] = {"games_common": len(cg), "seed_mismatch": 0 if seeds_eq else 1,
                      "student_seat_mismatch": 0 if seat_eq else 1,
                      "opp_table_mismatch": 0 if opp_eq else 1,
                      "ok": seeds_eq and seat_eq and opp_eq,
                      "student_seat_hist": {str(s): seat["base"].count(s) for s in range(4)}}
    if a.summary_base_prev:
        sp = json.loads(Path(a.summary_base_prev).read_text(encoding="utf-8"))
        p0 = {int(g["game"]): json.dumps(g, sort_keys=True) for g in sp["per_game"]}
        p1 = {int(g["game"]): json.dumps(g, sort_keys=True) for g in sm["base"]["per_game"]}
        bad = [g for g in sorted(set(p0) & set(p1)) if p0[g] != p1[g]]
        rep["pairing"]["reproduce_prev_base_per_game"] = {
            "games_compared": len(set(p0) & set(p1)), "mismatch": len(bad),
            "note": "本轮 A0 新采集 vs 上一轮 raw/p2-11b/base 的 per_game 逐位相同 ⇒ "
                    "复用的 A1 逐小局账与本轮同源可比（自证，不是声称）"}

    # ---------------- §2 场级得分 ----------------
    def game_score(k, g):
        return int(pg[k][g]["final_scores"][seat[k][cg.index(g)]])

    for k in sm:
        rep["arms"][k]["game_score"] = summarize([game_score(k, g) for g in cg])
        rep["arms"][k]["game_score_zero_sum_check"] = sum(int(x) for x in pg[k][cg[0]]["final_scores"])
    best = max(("base", "meld"), key=lambda k: rep["arms"][k]["game_score"]["mean"])
    rep["best_single_arm"] = best

    # ---------------- §3 F1(a) ----------------
    dH_best = [game_score("mask", g) - game_score(best, g) for g in cg]
    rep["F1_pre_registered"] = {
        "reading": "场级配对 Δ = 学生席终局得分差（同 seed ⇒ 同牌山）；判据 = CI 下界 > 0",
        "H_minus_best": summarize(dH_best),
        "H_minus_base": summarize([game_score("mask", g) - game_score("base", g) for g in cg]),
        "H_minus_meld": summarize([game_score("mask", g) - game_score("meld", g) for g in cg]),
        "base_minus_meld": summarize([game_score("base", g) - game_score("meld", g) for g in cg]),
        "best_single_arm": best,
        "pass_CI_lower_gt_0": summarize(dH_best)["lo"] > 0,
    }

    # ---------------- 逐小局账（只在可配对的单位上） ----------------
    dd = json.loads(Path(a.domdiff_json).read_text(encoding="utf-8")) if a.domdiff_json else {}
    bnd = {int(k): int(v) for k, v in dd.get("first_diff_hand_of_game", {}).items()}

    hands = {}
    for k, p in (("base", work / "hands_base.json"), ("mask", work / f"hands_{tag}.json"),
                 ("meld", prev / "hands_meld.json")):
        hj = json.loads(p.read_text(encoding="utf-8"))
        for h in hj["hands"]:
            hands.setdefault((int(h["game"]), int(h["hand_no"])), {})[k] = h
    com = sorted(k for k, v in hands.items()
                 if all(t in v and v[t].get("student_delta") is not None for t in ("base", "mask", "meld")))

    rows = [json.loads(l) for l in (work / "rows_base.jsonl").read_text(encoding="utf-8").splitlines()
            if l.strip()]
    didx = defaultdict(list)
    for i, r in enumerate(rows):
        if cond(r):
            didx[(int(r["game"]), int(r["hand_no"]))].append(i)
    n_D_total = sum(len(v) for v in didx.values())
    rep["domain"] = {
        "cond": a.cond, "n_rows": len(rows), "n_D_rows": n_D_total,
        "coverage": n_D_total / len(rows) if rows else float("nan"),
        "n_hands_with_D": len(didx), "n_hands_total": len({(int(r["game"]), int(r["hand_no"]))
                                                            for r in rows}),
        "note": "覆盖率与候选① 同一把尺子：分母 = A0 学生席**全部**决策行",
    }

    def unit_of(g, hn):
        b = bnd.get(g, -1)
        if b < 0 or hn < b:
            return "prefix"
        return "boundary" if hn == b else "downstream"

    def cells_of(idxs):
        d0 = list(idxs)
        if not d0:
            return None, 0
        r0 = rows[d0[0]]
        return ((int(r0["draw_band"]), int(r0["riichi_band"])),
                (int(r0["draw_band"]), int(r0["riichi_band"]), bool(r0["menzen"])),
                (int(r0["draw_band"]), int(r0["riichi_band"]), bool(r0["menzen"])),
                min(len(d0), 2)), len(d0)

    recs = []
    for k in com:
        g, hn = k
        v = hands[k]
        idxs = didx.get(k, [])
        cs, nD = cells_of(idxs)
        if cs is None:
            continue
        recs.append({"k": k, "game": g, "hand_no": hn, "unit": unit_of(g, hn),
                     "b": float(v["base"]["student_delta"]),
                     "h": float(v["mask"]["student_delta"]),
                     "y": float(v["meld"]["student_delta"]),
                     "c1": cs[0], "c2": cs[1], "c3": (cs[1][0], cs[1][1], cs[1][2], nD),
                     "n_D_rows": nD})
    for r in recs:
        r["gain"] = r["h"] - r["b"]
        r["oracle"] = max(r["gain"], 0.0)

    pre = [r for r in recs if r["unit"] == "prefix"]
    bndh = [r for r in recs if r["unit"] == "boundary"]
    down = [r for r in recs if r["unit"] == "downstream"]
    rep["hands"] = {
        "n_common": len(recs), "n_union": len(hands),
        "n_prefix": len(pre), "n_boundary": len(bndh), "n_downstream_不可配对": len(down),
        "n_prefix_with_D_row": sum(1 for r in pre if r["n_D_rows"] > 0),
        "note": "⛔ downstream（`hand_no > 边界`）在两臂里是**不同的牌局**，一律不进任何配对读数",
        "gain_prefix_must_be_0": summarize([r["gain"] for r in pre]) if pre else {"n": 0},
        "gain_prefix_max_abs": max((abs(r["gain"]) for r in pre), default=float("nan")),
        "gain_boundary": summarize([r["gain"] for r in bndh], [r["game"] for r in bndh]) if bndh else {"n": 0},
        "gain_boundary_oracle": summarize([r["oracle"] for r in bndh], [r["game"] for r in bndh])
            if bndh else {"n": 0},
    }
    for k, t in (("b", "base_A0"), ("h", "mask_H"), ("y", "meld_A1")):
        rep["hands"][f"boundary_hand_delta_{t}"] = summarize(
            [r[k] for r in bndh], [r["game"] for r in bndh]) if bndh else {"n": 0}

    # ---------------- §4 F2：交叉拟合（奇/偶场互评） ----------------
    def routing(sub, cells_key):
        if not sub:
            return {"n": 0}
        out = {"n": len(sub), "games": len({r["game"] for r in sub}),
               "fixed_mask_gain": summarize([r["gain"] for r in sub], [r["game"] for r in sub])}
        deltas, games = [], []
        for fold in (0, 1):
            fit = [r for r in sub if r["game"] % 2 != fold]
            ev = [r for r in sub if r["game"] % 2 == fold]
            if not fit or not ev:
                continue
            bench = sum(r["gain"] for r in fit) / len(fit) >= 0
            bycell = defaultdict(list)
            for r in fit:
                bycell[r[cells_key]].append(r["gain"])
            pick = {c: (sum(v) / len(v) >= 0) for c, v in bycell.items()}
            for r in ev:
                deltas.append(r["gain"] if pick.get(r[cells_key], bench) else 0.0)
                games.append(r["game"])
        out["oos_router"] = summarize(deltas, games)
        out["n_cells"] = len({r[cells_key] for r in sub})
        out["cell_top1_share"] = max(sum(1 for r in sub if r[cells_key] == c)
                                     for c in {r[cells_key] for r in sub}) / len(sub)
        ncell = defaultdict(lambda: [0, 0.0])
        for r in sub:
            ncell[r[cells_key]][0] += 1
            ncell[r[cells_key]][1] += r["gain"]
        out["cells"] = {str(c): {"n": v[0], "gain": v[1] / v[0]} for c, v in sorted(ncell.items())}
        return out

    first_d_row_of_game = {}
    for i, r in enumerate(rows):
        g = int(r["game"])
        if g not in first_d_row_of_game and cond(r):
            first_d_row_of_game[g] = i
    grecs = []
    for g in cg:
        i = first_d_row_of_game.get(g)
        if i is None:
            continue
        r0 = rows[i]
        cs = ((int(r0["draw_band"]), int(r0["riichi_band"])),
              (int(r0["draw_band"]), int(r0["riichi_band"]), bool(r0["menzen"])))
        grecs.append({"game": g, "gain": float(game_score("mask", g) - game_score("base", g)),
                      "c1": cs[0], "c2": cs[1]})
    rep["F2_crossfit"] = {
        "reading": "只在 D 上、只用公开观测的 cell；奇/偶场互评，两方向合并。"
                   "`boundary` 单位 = 边界小局（真路由增益所在的那一手）；`game` 单位 = 场（终局得分差）",
        "boundary_c1": routing(bndh, "c1"),
        "boundary_c2": routing(bndh, "c2"),
        "boundary_c3_plus_D行数档": routing(bndh, "c3"),
        "game_c1": routing(grecs, "c1"),
        "game_c2": routing(grecs, "c2"),
        "prefix_control_c2": routing(pre, "c2"),
        "n_D_hands_total_played": len(bndh),
    }

    # ---------------- §5 ρ 与剔最差 X% ----------------
    order = sorted(bndh, key=lambda r: r["b"])
    rep["rho_and_trim"] = {
        "unit": "边界小局（真路由增益所在的那些手）", "n": len(bndh),
        "rho_oracle_gain_vs_base": pearson([r["oracle"] for r in bndh], [r["b"] for r in bndh]),
        "rho_played_gain_vs_base": pearson([r["gain"] for r in bndh], [r["b"] for r in bndh]),
        "rho_base_vs_mask": pearson([r["b"] for r in bndh], [r["h"] for r in bndh]),
        "rho_base_vs_meld": pearson([r["b"] for r in bndh], [r["y"] for r in bndh]),
        "trim_worst_by_base": {str(p): {"n": len(order[int(len(order) * p / 100):]),
                                        "gain": sum(r["gain"] for r in order[int(len(order) * p / 100):])
                                                / max(1, len(order[int(len(order) * p / 100):]))}
                               for p in (0, 5, 10, 20, 30, 50)},
    }

    # ---------------- §6 机械自证摘要 ----------------
    rep["pass_rule_pre_registered"] = {"(a) F1 CI 下界 > 0": rep["F1_pre_registered"]["pass_CI_lower_gt_0"],
                                      "(b) 机械自证 D 外逐位相同": None, "结论": None}
    if a.domdiff_json:
        rep["domdiff"] = {"judge": dd["judge"], "prefix_lines": dd["prefix_lines"],
                          "prefix_nonD_lines": dd["prefix_nonD_lines"],
                          "prefix_D_lines": dd["prefix_D_lines"],
                          "nonD_hands_prefix": dd["nonD_hands_prefix"],
                          "nonD_lines_prefix": dd["nonD_lines_prefix"],
                          "nonD_hands_mismatch": dd["nonD_hands_mismatch"],
                          "D_rows_prefix": dd["D_rows_prefix"],
                          "D_flip_rows_prefix": dd["D_flip_rows_prefix"],
                          "D_rows_a_total": dd["D_rows_a_total"],
                          "n_D_keys_from_rows": dd["n_D_keys_from_rows"],
                          "firstdiff_is_D": dd["firstdiff_is_D"],
                          "firstdiff_not_D": dd["firstdiff_not_D"],
                          "prefix_len_mismatch": dd["prefix_len_mismatch"],
                          "games_identical": dd["games_identical"],
                          "games_diverged": dd["games_diverged"],
                          "n_games_a": dd["n_games_a"],
                          "first_diff_hand_hist": dd["first_diff_hand_hist"],
                          "sample_bad_nonD": dd["nonD_first_mismatch_sample"],
                          "sample_bad_firstdiff": dd["firstdiff_not_D_sample"]}
        rep["pass_rule_pre_registered"]["(b) 机械自证 D 外逐位相同"] = bool(
            dd["judge"]["①_¬D小局(前缀内)逐字节相同"]
            and dd["judge"]["②_第一处差异都落在学生席的 D 行"]
            and dd["judge"]["③_D行数自证(轨迹里数出来 == rows 里的集合大小)"])
    if a.dforward_json:
        df = json.loads(Path(a.dforward_json).read_text(encoding="utf-8"))
        rep["dforward"] = {k: df[k] for k in df if k not in ("band", "same_state")}
        rep["dforward"]["band"] = df.get("band")
        rep["dforward"]["same_state"] = df.get("same_state")
    a_ok = rep["pass_rule_pre_registered"]["(a) F1 CI 下界 > 0"]
    b_ok = rep["pass_rule_pre_registered"]["(b) 机械自证 D 外逐位相同"]
    if b_ok is False:
        rep["pass_rule_pre_registered"]["结论"] = "作废：机械自证失败（实现没做到同到可辨状态再分歧）"
    elif a_ok and b_ok:
        rep["pass_rule_pre_registered"]["结论"] = "过：可投「共享主干 + 域头」"
    else:
        rep["pass_rule_pre_registered"]["结论"] = "不过（详见报告：域选错 / 形态不行 / 条件于该套牌山）"

    # ---------------- §7 一域一行（落盘） ----------------
    f1 = rep["F1_pre_registered"]["H_minus_best"]
    f2b = rep["F2_crossfit"]["boundary_c1"]["oos_router"] if rep["F2_crossfit"]["boundary_c1"] else {"n": 0}
    f2g = rep["F2_crossfit"]["game_c1"]["oos_router"] if rep["F2_crossfit"]["game_c1"] else {"n": 0}
    row = {"tag": tag, "cond": a.cond,
           "coverage": rep["domain"]["coverage"], "n_D_rows": n_D_total, "n_rows": len(rows),
           "flip_rate": (rep.get("dforward") or {}).get("flip_rate_D"),
           "n_D_dforward": (rep.get("dforward") or {}).get("n_D"),
           "f1a_mean": f1.get("mean"), "f1a_se": f1.get("se"), "f1a_lo": f1.get("lo"),
           "f1a_hi": f1.get("hi"), "f1a_t": f1.get("t"),
           "best_arm": best, "pass_f1a": rep["F1_pre_registered"]["pass_CI_lower_gt_0"],
           "pass_f1b": rep["pass_rule_pre_registered"]["(b) 机械自证 D 外逐位相同"],
           "f2_boundary_oos": f2b.get("mean"), "f2_boundary_oos_lo": f2b.get("lo"),
           "f2_game_oos": f2g.get("mean"), "f2_game_oos_lo": f2g.get("lo"),
           "prefix_control_max_abs": rep["hands"].get("gain_prefix_max_abs"),
           "rho_oracle": rep["rho_and_trim"]["rho_oracle_gain_vs_base"],
           "rho_played": rep["rho_and_trim"]["rho_played_gain_vs_base"],
           "n_boundary": rep["hands"]["n_boundary"], "n_prefix": rep["hands"]["n_prefix"],
           "n_downstream": rep["hands"]["n_downstream_不可配对"],
           "verdict": "过" if (a_ok and b_ok) else ("作废" if b_ok is False else "不过")}
    rep["summary_row"] = row

    Path(a.out_json).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.append_jsonl:
        with Path(a.append_jsonl).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"tag": tag, "cond": a.cond, "row": row}, ensure_ascii=False) + "\n")
    if a.append_md:
        cov = f"{row['coverage']*100:.2f}%" if row.get("coverage") is not None else "n/a"
        fl = f"{row['flip_rate']*100:.2f}%" if row.get("flip_rate") is not None else "n/a"
        mdrow = (f"| `{tag}` | `{a.cond}` | {cov} | {fl} | "
                 f"{f1.get('mean', float('nan')):+.1f} ± {f1.get('se', float('nan')):.1f} "
                 f"[{f1.get('lo', float('nan')):+.1f}, {f1.get('hi', float('nan')):+.1f}] | "
                 f"{f2b.get('mean', float('nan')):+.1f} / {f2g.get('mean', float('nan')):+.1f} | "
                 f"{row['rho_oracle']:+.3f} / {row['rho_played']:+.3f} | "
                 f"{row['prefix_control_max_abs']} | **{row['verdict']}** |")
        with Path(a.append_md).open("a", encoding="utf-8") as f:
            f.write(mdrow + "\n")
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
