# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""P2-11b · 正式判据统计（零训练）：三臂配对账 + 交叉拟合路由增益 + 均值回归。

三臂（同一 seed 族 `52870101` ⇒ 同牌山 ⇒ **逐场配对**）：
  A0 = `net:<wl-riichi>`（基线）· A1 = `net:<wl-meld>`（既有产物复用）·
  H  = `net:<A0>!net:<A1>@kind=claim`（**域掩码真跑**，本轮新能力）。

读数（口径见 `P2-11-FORMAL-RESULT.md` 的冻结段）：
  §1 配对自证：seed / 学生席 / 对手桌 逐场一致 + A0 与上一轮旧采集 `per_game` **逐位相同**
  §2 臂均值（场级 = `final_scores[学生席]`）
  §3 **F1（预注册 §4 正式）**：Δ = H − 最好的单臂，**场级配对** Δ/SE/95%CI（判据 = CI 下界 > 0）
  §4 **F2（D 上样本外路由增益，交叉拟合奇/偶场互评；只用公开观测的 cell）**
  §5 对照：¬D 增益（应恒 0）· ρ（门槛 |ρ|<0.25）· 剔最差 X%
  §6 机械自证读数（domdiff / dforward 的摘要）
⛔ 不做第二个统计实现：均值/SE/t/CI/按场聚类 SE 与 `_p2-11-analyze.py` 同一套公式。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

Z = 1.959963985


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_p2-11b-analyze.py")
    ap.add_argument("--work", required=True, help="本轮 work（hands_base/hands_mask/rows_base）")
    ap.add_argument("--work-prev", required=True, help="上一轮 work（复用 A1 = meld 的逐小局账）")
    ap.add_argument("--summary-base", required=True)
    ap.add_argument("--summary-mask", required=True)
    ap.add_argument("--summary-meld", required=True, help="复用：raw/p2-11/meld/summary.json")
    ap.add_argument("--summary-base-prev", default="", help="上一轮 raw/p2-11/riichi/summary.json（逐位复现自证）")
    ap.add_argument("--meta-base", required=True)
    ap.add_argument("--meta-mask", required=True)
    ap.add_argument("--domdiff-json", default="")
    ap.add_argument("--dforward-json", default="")
    ap.add_argument("--out-json", required=True)
    a = ap.parse_args(argv)

    work, prev = Path(a.work), Path(a.work_prev)
    sm = {k: json.loads(Path(p).read_text(encoding="utf-8")) for k, p in
          (("base", a.summary_base), ("mask", a.summary_mask), ("meld", a.summary_meld))}
    meta = {k: json.loads(Path(p).read_text(encoding="utf-8")) for k, p in
            (("base", a.meta_base), ("mask", a.meta_mask))}
    rep: dict = {"arms": {k: {"policy": meta[k]["student_policy"] if k in meta else None,
                              "seed_base": sm[k].get("seed_base"), "games": sm[k].get("games")}
                          for k in sm}}

    # ---------------- §1 配对自证 ----------------
    pg = {k: {int(g["game"]): g for g in sm[k]["per_game"]} for k in sm}
    stu_pol = {"base": meta["base"]["student_policy"], "mask": meta["mask"]["student_policy"],
               "meld": json.loads((prev / "meta_meld.json").read_text(encoding="utf-8"))["student_policy"]}
    for k in sm:
        rep["arms"][k]["student_policy"] = stu_pol[k]
    common = {k: sorted(pg[k].keys()) for k in sm}
    cg = sorted(set.intersection(*[set(v) for v in common.values()]))
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
        s0 = pg["base"][g]["policies"]
        o0 = sorted(p for p in s0 if p != stu_pol["base"])
        for k in ("mask", "meld"):
            sk = pg[k][g]["policies"]
            ok_ = sorted(p for p in sk if p != stu_pol[k])
            if o0 != ok_:
                opp_eq = False
    rep["pairing"] = {"games_common": len(cg), "seed_mismatch": 0 if seeds_eq else 1,
                      "student_seat_mismatch": 0 if seat_eq else 1,
                      "opp_table_mismatch": 0 if opp_eq else 1,
                      "ok": seeds_eq and seat_eq and opp_eq}
    rep["pairing"]["student_seat_hist"] = {str(s): seat["base"].count(s) for s in range(4)}
    if a.summary_base_prev:
        sp = json.loads(Path(a.summary_base_prev).read_text(encoding="utf-8"))
        p0 = {int(g["game"]): json.dumps(g, sort_keys=True) for g in sp["per_game"]}
        p1 = {int(g["game"]): json.dumps(g, sort_keys=True) for g in sm["base"]["per_game"]}
        bad = [g for g in sorted(set(p0) & set(p1)) if p0[g] != p1[g]]
        rep["pairing"]["reproduce_prev_base_per_game"] = {
            "games_compared": len(set(p0) & set(p1)), "mismatch": len(bad),
            "note": "本轮 A0 新采集 vs 上一轮 raw/p2-11/riichi 的 per_game（seed/policies/final_scores/"
                    "placement/rank_points/wins/deals/hands/ryukyoku）逐位相同 ⇒ "
                    "复用的 A1 逐小局账与本轮同源可比"}

    # ---------------- §2 场级得分（= final_scores[学生席]） ----------------
    def game_score(k, g):
        return int(pg[k][g]["final_scores"][seat[k][cg.index(g)]])
    for k in sm:
        ds = [game_score(k, g) for g in cg]
        rep["arms"][k]["game_score"] = summarize(ds)
        rep["arms"][k]["game_score_zero_sum_check"] = sum(
            int(x) for x in pg[k][cg[0]]["final_scores"])
    best = max(("base", "meld"), key=lambda k: rep["arms"][k]["game_score"]["mean"])
    rep["best_single_arm"] = best

    # ---------------- §3 F1：Δ = H − 最好的单臂（场级配对） ----------------
    dH_best = [game_score("mask", g) - game_score(best, g) for g in cg]
    dH_base = [game_score("mask", g) - game_score("base", g) for g in cg]
    dH_meld = [game_score("mask", g) - game_score("meld", g) for g in cg]
    dbase_meld = [game_score("base", g) - game_score("meld", g) for g in cg]
    rep["F1_pre_registered"] = {
        "reading": "场级配对 Δ = 学生席终局得分差（同 seed ⇒ 同牌山）；判据 = CI 下界 > 0",
        "H_minus_best": summarize(dH_best),
        "H_minus_base": summarize(dH_base),
        "H_minus_meld": summarize(dH_meld),
        "base_minus_meld": summarize(dbase_meld),
        "best_single_arm": best,
        "pass_CI_lower_gt_0": summarize(dH_best)["lo"] > 0,
    }

    # ---------------- 逐小局账（**只在可配对的单位上**） ----------------
    # ⚠ 关键口径（第一版踩过，实测会造出 +350 点/手的**假**增益）：`(game, hand_no)` 只在
    #   "某场第一处分岔**之前**"才是同一个牌局 —— 一场一旦分岔，之后的小局编号在两臂里指的是
    #   **不同的牌局**（庄位/巡目/牌山切片都可能不同），拿它们配对等于把"另一局的结果"当路由增益。
    #   可配对的单位只有三种（都由 domdiff 的逐行自证给出边界）：
    #     ① **场级**：终局得分（同 seed ⇒ 同牌山，配对精确）—— 预注册的主单位；
    #     ② **前缀小局**（`hand_no < 边界`）：两臂逐字节相同 ⇒ 账**逐位相同**（增益恒 0）；
    #     ③ **边界小局**（= 边界那一手）：两臂从同一状态起打**同一局** ⇒ 账配得上，是真路由增益。
    dd = json.loads(Path(a.domdiff_json).read_text(encoding="utf-8")) if a.domdiff_json else {}
    bnd = {int(k): int(v) for k, v in dd.get("first_diff_hand_of_game", {}).items()}

    hands = {}
    for k, p in (("base", work / "hands_base.json"), ("mask", work / "hands_mask.json"),
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
        didx[(int(r["game"]), int(r["hand_no"]))].append(i)

    def unit_of(g, hn):
        b = bnd.get(g, -1)
        if b < 0 or hn < b:
            return "prefix"
        return "boundary" if hn == b else "downstream"

    def cells_of(idxs):
        d0 = [i for i in idxs if str(rows[i]["kind"]) == "claim"]
        r0 = rows[d0[0]] if d0 else (rows[idxs[0]] if idxs else None)
        if r0 is None:
            return None, 0
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
    for k, tag in (("b", "base_A0"), ("h", "mask_H"), ("y", "meld_A1")):
        rep["hands"][f"boundary_hand_delta_{tag}"] = summarize([r[k] for r in bndh],
                                                              [r["game"] for r in bndh]) if bndh else {"n": 0}

    # ---------------- §4 F2：交叉拟合（奇/偶场互评）cell 路由 ----------------
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
        top = max(sum(1 for r in sub if r[cells_key] == c) for c in {r[cells_key] for r in sub})
        out["cell_top1_share"] = top / len(sub)
        ncell = defaultdict(lambda: [0, 0.0])
        for r in sub:
            ncell[r[cells_key]][0] += 1
            ncell[r[cells_key]][1] += r["gain"]
        out["cells"] = {str(c): {"n": v[0], "gain": v[1] / v[0]} for c, v in sorted(ncell.items())}
        return out

    # 场级路由（单位 = 场；cell 取该场**学生席第一条 D 行**的公开观测）
    first_d_row_of_game = {}
    for i, r in enumerate(rows):
        g = int(r["game"])
        if g not in first_d_row_of_game and str(r["kind"]) == "claim":
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
        "unit": "边界小局（真路由增益所在的那些手）",
        "n": len(bndh),
        "rho_oracle_gain_vs_base": pearson([r["oracle"] for r in bndh], [r["b"] for r in bndh]),
        "rho_played_gain_vs_base": pearson([r["gain"] for r in bndh], [r["b"] for r in bndh]),
        "rho_base_vs_mask": pearson([r["b"] for r in bndh], [r["h"] for r in bndh]),
        "rho_base_vs_meld": pearson([r["b"] for r in bndh], [r["y"] for r in bndh]),
        "trim_worst_by_base": {str(p): {"n": len(order[int(len(order) * p / 100):]),
                                        "gain": sum(r["gain"] for r in order[int(len(order) * p / 100):])
                                                / max(1, len(order[int(len(order) * p / 100):]))}
                               for p in (0, 5, 10, 20, 30, 50)},
    }
    rep["pass_rule_pre_registered"] = {
        "(a) F1 CI 下界 > 0": rep["F1_pre_registered"]["pass_CI_lower_gt_0"],
        "(b) 机械自证 D 外逐位相同": None,
        "结论": None,
    }
    if a.domdiff_json:
        rep["domdiff"] = {"judge": dd["judge"],
                          "prefix_lines": dd["prefix_lines"],
                          "prefix_nonD_lines": dd["prefix_nonD_lines"],
                          "prefix_D_lines": dd["prefix_D_lines"],
                          "nonD_hands_prefix": dd["nonD_hands_prefix"],
                          "nonD_lines_prefix": dd["nonD_lines_prefix"],
                          "nonD_hands_mismatch": dd["nonD_hands_mismatch"],
                          "D_rows_prefix": dd["D_rows_prefix"],
                          "D_flip_rows_prefix": dd["D_flip_rows_prefix"],
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
            and dd["judge"]["②_第一处差异都落在学生席的 D 行"])
    if a.dforward_json:
        df = json.loads(Path(a.dforward_json).read_text(encoding="utf-8"))
        rep["dforward"] = {k: df[k] for k in df if k != "band"}
        rep["dforward"]["band"] = df.get("band")
    a_ok = rep["pass_rule_pre_registered"]["(a) F1 CI 下界 > 0"]
    b_ok = rep["pass_rule_pre_registered"]["(b) 机械自证 D 外逐位相同"]
    if b_ok is False:
        rep["pass_rule_pre_registered"]["结论"] = "作废：机械自证失败（实现没做到同到可辨状态再分歧）"
    elif a_ok and b_ok:
        rep["pass_rule_pre_registered"]["结论"] = "过：可投「共享主干 + 域头」"
    else:
        rep["pass_rule_pre_registered"]["结论"] = "不过（详见报告：域选错 / 形态不行 / 条件于该套牌山）"

    Path(a.out_json).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
