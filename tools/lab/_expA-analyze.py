# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""实验 A · 第 3 步：分歧域 + 样本外（交叉拟合）路由增益（零训练、仅解析）。

判据（**跑之前写死**，见 EXPERIMENT-A-DIVERGENCE.md §判据）：
  主判据 = 分歧域 D 上的**样本外**路由增益（奇数场拟合 / 偶数场评估，两方向合并）
            基准 = 拟合折上更好的那条臂（"固定用其中更好的臂"）
  **CI 排除 0 且为正** ⇒ 口径/分辨率事故；**CI 含 0** ⇒ 原理性不可行。
  对照 1 = 同口径但在**非分歧域**上（预期 ≈ 0）。
  对照 2 = 均值回归 ρ(Δ, 基准臂自身得分) + 剔除最差 X% 后的剩余增益。

⛔ 只用公开观测（`obs` 里该席自己的合法字段）；⛔ 不用"该局结果"选臂（结果只用于**评估**）。
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


def summarize(ds, games) -> dict:
    """逐小局配对 Δ 的均值/SE/t/CI + **按场聚类**的 SE。"""
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
    return {"n": n, "mean": m, "sd": sd, "se": se, "t": m / se if se and se == se else float("nan"),
            "lo": m - Z * se, "hi": m + Z * se,
            "p2": 2 * (1 - ncdf(abs(m / se))) if se and se == se else float("nan"),
            "games": ng, "se_cluster": se_g,
            "t_cluster": m / se_g if se_g and se_g == se_g else float("nan"),
            "lo_cluster": m - Z * se_g, "hi_cluster": m + Z * se_g}


def load_rows(work: Path, arm: str):
    rows = [json.loads(l) for l in (work / f"rows_{arm}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


def load_logits(work: Path, arm: str, tag: str):
    out = []
    p = work / f"logits_{arm}_{tag}.jsonl"
    if not p.is_file():
        raise SystemExit(f"⛔ 缺 {p}")
    for l in p.read_text(encoding="utf-8").splitlines():
        if l.strip():
            out.append(json.loads(l))
    return out


def cell_of(row, kind_key="kind"):
    return (str(row[kind_key]), int(row["draw_band"]), int(row["riichi_band"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_expA-analyze.py")
    ap.add_argument("--work", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--primary-arm", default="riichi")
    ap.add_argument("--raw", action="append", default=[],
                    help="臂=采集目录（逐场配对自证：seed / 学生席 / 另三席策略表；可重复两次）")
    a = ap.parse_args(argv)
    work = Path(a.work)
    rep: dict = {}

    # ---------------- 1. 逐臂：对齐 + 自证 ----------------
    per_arm: dict[str, dict] = {}
    for arm, other in (("riichi", "meld"), ("meld", "riichi")):
        rows = load_rows(work, arm)
        hands_raw = json.loads((work / f"hands_{arm}.json").read_text(encoding="utf-8"))
        meta = json.loads((work / f"meta_{arm}.json").read_text(encoding="utf-8"))
        lp = work / f"logits_{arm}_Y.jsonl"
        # ⚠ 墙钟受限时允许**只跑主臂的前向**（另一臂只贡献逐小局账，不进分歧统计）——不静默降级，写在报告里。
        have = (work / f"logits_{arm}_X.jsonl").is_file() and lp.is_file() and lp.stat().st_size > 0
        if not have:
            per_arm[arm] = {"rows": rows, "hands": hands_raw["hands"], "meta": meta,
                            "other": other, "div": None, "lgX": None, "lgY": None,
                            "step_bad": None, "nlegal_bad": None, "self_bad": None,
                            "logits_skipped": True}
            continue
        lgX = load_logits(work, arm, "X")   # X = 该臂自己的 net
        lgY = load_logits(work, arm, "Y")   # Y = 另一条臂的 net
        if not (len(rows) == len(lgX) == len(lgY)):
            raise SystemExit(f"⛔ 行数不齐 arm={arm}: rows={len(rows)} X={len(lgX)} Y={len(lgY)}")
        step_bad = sum(1 for r, x in zip(rows, lgX) if int(r["step"]) != int(x["step"]))
        nlegal_bad = sum(1 for r, x in zip(rows, lgX) if int(r["nlegal"]) != int(x["n"]))
        self_bad = sum(1 for r, x in zip(rows, lgX) if int(r["chosen_index"]) != int(x["argmax"]))
        div = [int(x["argmax"]) != int(y["argmax"]) for x, y in zip(lgX, lgY)]
        per_arm[arm] = {"rows": rows, "lgX": lgX, "lgY": lgY, "div": div,
                        "hands": hands_raw["hands"], "meta": meta, "other": other,
                        "step_bad": step_bad, "nlegal_bad": nlegal_bad, "self_bad": self_bad,
                        "logits_skipped": False}
    rep["align"] = {}
    for arm in per_arm:
        if per_arm[arm]["logits_skipped"]:
            rep["align"][arm] = {"logits_skipped": True, "rows": len(per_arm[arm]["rows"])}
            continue
        rep["align"][arm] = {k: per_arm[arm][k] for k in ("step_bad", "nlegal_bad", "self_bad")} | {
            "rows": len(per_arm[arm]["rows"]),
            "self_match_rate": 1 - per_arm[arm]["self_bad"] / len(per_arm[arm]["rows"])}

    # ---------------- 2. 分歧率 ----------------
    diver: dict = {}
    for arm in [x for x in per_arm if not per_arm[x]["logits_skipped"]]:
        d = per_arm[arm]["div"]
        rows = per_arm[arm]["rows"]
        by_kind, by_db, by_rb = defaultdict(list), defaultdict(list), defaultdict(list)
        for v, r in zip(d, rows):
            by_kind[str(r["kind"])].append(v)
            by_db[int(r["draw_band"])].append(v)
            by_rb[int(r["riichi_band"])].append(v)
        # 小局级 D + 每场首个分歧行的行号
        first_div: dict[int, int] = {}
        for i, (v, r) in enumerate(zip(d, rows)):
            if v:
                first_div.setdefault(int(r["game"]), i)
        hand_rows: dict[tuple, list[int]] = defaultdict(list)
        for i, r in enumerate(rows):
            hand_rows[(int(r["game"]), int(r["hand_no"]))].append(i)
        hand_div = {k: any(d[i] for i in v) for k, v in hand_rows.items()}
        # 「前缀干净」：该小局的所有行都在本场首个分歧行之前
        hand_prefix_clean = {}
        for k, v in hand_rows.items():
            fd = first_div.get(k[0])
            hand_prefix_clean[k] = (fd is None) or (fd > max(v))
        diver[arm] = {
            "p_row": sum(d) / len(d), "n_rows": len(d), "n_div_rows": sum(d),
            "by_kind": {k: {"n": len(v), "p": sum(v) / len(v)} for k, v in sorted(by_kind.items())},
            "by_draw_band": {str(k): {"n": len(v), "p": sum(v) / len(v)} for k, v in sorted(by_db.items())},
            "by_riichi_band": {str(k): {"n": len(v), "p": sum(v) / len(v)} for k, v in sorted(by_rb.items())},
            "n_hands": len(hand_rows), "n_hands_D": sum(hand_div.values()),
            "p_hand_D": sum(hand_div.values()) / len(hand_rows),
            "n_hands_prefix_clean": sum(hand_prefix_clean.values()),
            "hand_div": hand_div, "hand_prefix_clean": hand_prefix_clean,
            "hand_rows": hand_rows, "first_div_game": first_div,
        }
    rep["divergence"] = {arm: {k: v for k, v in diver[arm].items()
                               if k not in ("hand_div", "hand_prefix_clean", "hand_rows", "first_div_game")}
                         for arm in diver}

    # ---------------- 2b. 「同一状态」自证 + 首次分歧落在哪里 ----------------
    # 判据：两臂在同一 `(game, hand_no, step)` 键上且 **obs 指纹逐字节相同** ⇒ 真的同一状态；
    #       一旦分岔，其后所有行的 obs 就不再相同（确定性 argmax ⇒ 分岔是**一次性**的）。
    km = {}
    for arm in per_arm:
        km[arm] = {(int(r["game"]), int(r["hand_no"]), int(r["step"])): r["obs_hash"]
                   for r in per_arm[arm]["rows"]}
    pa, pb = km["riichi"], km["meld"]
    n_keys_a, n_keys_b = len(pa), len(pb)
    both = set(pa) & set(pb)
    same_obs = sum(1 for k in both if pa[k] == pb[k])
    # 逐场：学生行里"状态已验证相同"的前缀有多长 + 首次分岔落在哪个小局
    if per_arm[a.primary_arm]["logits_skipped"]:
        raise SystemExit(f"⛔ 主臂 {a.primary_arm} 没有前向产物 —— 分歧域算不出来")
    prim = per_arm[a.primary_arm]["rows"]
    otherk = km[per_arm[a.primary_arm]["other"]]
    first_bad: dict[int, int] = {}
    hand_state_ok: dict[tuple, bool] = {}
    for idx, r in enumerate(prim):
        g, hn, st = int(r["game"]), int(r["hand_no"]), int(r["step"])
        ok = (g, hn, st) in otherk and otherk[(g, hn, st)] == r["obs_hash"]
        if not ok:
            first_bad.setdefault(g, idx)
            hand_state_ok[(g, hn)] = False
        else:
            hand_state_ok.setdefault((g, hn), True)
    nrows_by_game: dict[int, int] = defaultdict(int)
    hns_by_game: dict[int, set] = defaultdict(set)
    gstart: dict[int, int] = {}
    for idx, r in enumerate(prim):
        g = int(r["game"])
        gstart.setdefault(g, idx)
        nrows_by_game[g] += 1
        hns_by_game[g].add(int(r["hand_no"]))
    games_sorted = sorted(nrows_by_game)
    # ⚠ 首次分歧要报**场内**行号（全局行号没有意义）
    pref_rows = sorted(first_bad[g] - gstart[g] for g in games_sorted if g in first_bad)
    n_games_nodiv = sum(1 for g in games_sorted if g not in first_bad)
    pref_hands = {}
    for g in games_sorted:
        n = 0
        for hn in sorted(hns_by_game[g]):
            if hand_state_ok.get((g, hn), False):
                n += 1
            else:
                break
        pref_hands[g] = n
    fd_hand = {}
    for g, idx in first_bad.items():
        fd_hand[g] = int(prim[idx]["hand_no"])
    rep["same_state"] = {
        "keys_A": n_keys_a, "keys_B": n_keys_b, "keys_both": len(both),
        "obs_identical": same_obs, "obs_differs_same_key": len(both) - same_obs,
        "p_state_matched": same_obs / len(both) if both else float("nan"),
        "pref_rows_median": pref_rows[len(pref_rows) // 2] if pref_rows else 0,
        "pref_rows_p90": pref_rows[int(len(pref_rows) * 0.9)] if pref_rows else 0,
        "pref_rows_max": pref_rows[-1] if pref_rows else 0,
        "games_with_no_state_div": n_games_nodiv,
        "prim_rows_per_game_mean": len(prim) / max(1, len(pref_rows)),
        "pref_hands_mean": sum(pref_hands.values()) / max(1, len(pref_hands)),
        "games_first_div_in_hand0": sum(1 for v in fd_hand.values() if v == 0),
        "games_first_div_in_hand1": sum(1 for v in fd_hand.values() if v == 1),
        "games_first_div_hand_hist": {str(k): sum(1 for v in fd_hand.values() if v == k)
                                      for k in sorted(set(fd_hand.values()))},
        "games": len(pref_rows),
    }
    # 状态已验证相同的行上，"两臂 argmax 不同"的比例（= 真正的同状态分歧率）
    sm = [(i, r) for i, r in enumerate(prim)
          if (int(r["game"]), int(r["hand_no"]), int(r["step"])) in otherk
          and otherk[(int(r["game"]), int(r["hand_no"]), int(r["step"]))] == r["obs_hash"]]
    div_p = per_arm[a.primary_arm]["div"]
    rep["same_state"]["n_state_matched_rows"] = len(sm)
    rep["same_state"]["p_div_on_state_matched"] = (sum(1 for i, _ in sm if div_p[i]) / len(sm)) if sm else float("nan")

    # ---------------- 3. 配对 + 逐臂均值 ----------------
    pairs: dict[tuple, dict] = {}
    for arm in per_arm:
        for h in per_arm[arm]["hands"]:
            k = (int(h["game"]), int(h["hand_no"]))
            pairs.setdefault(k, {})[arm] = h
    common = sorted(k for k, v in pairs.items() if len(v) == 2 and
                    v["riichi"].get("student_delta") is not None and v["meld"].get("student_delta") is not None)
    seat_mismatch = sum(1 for k in common if pairs[k]["riichi"]["student_seat"] != pairs[k]["meld"]["student_seat"])
    rep["pairing"] = {"n_common_hands": len(common), "n_union": len(pairs), "seat_mismatch": seat_mismatch,
                      "seat_hist": {str(s): sum(1 for k in common if pairs[k]["riichi"]["student_seat"] == s)
                                    for s in range(4)}}
    # 逐场配对自证（读两侧 summary.json）：seed / 学生席 / **另三席策略表**逐场一致。
    # 这三条是"同一副牌山 + 同一张桌、只换学生席那一条臂"的全部前提（= w-ladder.py `_check_pairing` 的同义）。
    if len(a.raw) == 2:
        sms = {}
        for spec in a.raw:
            tag, _, d = spec.partition("=")
            sms[tag] = json.loads((Path(d) / "summary.json").read_text(encoding="utf-8"))
        t1, t2 = list(sms)
        sp1 = per_arm[t1]["meta"]["student_policy"]
        sp2 = per_arm[t2]["meta"]["student_policy"]
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

    # 主臂视角：D 由**主臂自己**的轨迹定义（部署时路由器只看得到自己的行 ⇒ 可实施）
    arm = a.primary_arm
    other = per_arm[arm]["other"]
    hd = diver[arm]["hand_div"]
    hpc = diver[arm]["hand_prefix_clean"]
    rows = per_arm[arm]["rows"]
    first_div_row: dict[tuple, int] = {}
    hand_rows = diver[arm]["hand_rows"]
    for i, r in enumerate(rows):
        if per_arm[arm]["div"][i]:
            k = (int(r["game"]), int(r["hand_no"]))
            first_div_row.setdefault(k, i)
    recs = []
    for k in common:
        g, hn = k
        dX = pairs[k][arm]["student_delta"]
        dY = pairs[k][other]["student_delta"]
        D = bool(hd.get(k, False))
        div_i = first_div_row.get(k)
        if div_i is not None:
            r0 = rows[div_i]
            cell = cell_of(r0)
        else:
            r0 = rows[hand_rows[k][0]]
            cell = cell_of(r0)
        recs.append({"k": k, "game": g, "hand_no": hn, "dX": dX, "dY": dY, "D": D,
                     "prefix_clean": bool(hpc.get(k, False)), "cell": cell,
                     "feat_from": "first_div" if div_i is not None else "first_row",
                     "n_div_rows_hand": sum(per_arm[arm]["div"][i] for i in hand_rows.get(k, []))})
    rep["arms"] = {"primary": arm, "other": other,
                   "mean_dX": sum(r["dX"] for r in recs) / len(recs),
                   "mean_dY": sum(r["dY"] for r in recs) / len(recs),
                   "mean_dX_minus_dY": sum(r["dX"] - r["dY"] for r in recs) / len(recs)}
    rep["arms"]["paired_XY"] = summarize([r["dX"] - r["dY"] for r in recs], [r["game"] for r in recs])
    # 分级对照：小局内**分歧行数**（指示量的"强度"）与 Δ(X−Y) 的关系
    graded = []
    for lo, hi, lab in ((0, 0, "0"), (1, 1, "1"), (2, 3, "2-3"), (4, 10 ** 9, "4+")):
        v = [r for r in recs if lo <= r["n_div_rows_hand"] <= hi]
        if not v:
            continue
        st = summarize([r["dX"] - r["dY"] for r in v], [r["game"] for r in v])
        st["bucket_n_div_rows"] = lab
        graded.append(st)
    rep["graded_density"] = graded

    # ---------------- 4. 交叉拟合路由 ----------------
    def run_routing(sel, label, cells_key="cell"):
        sub = [r for r in recs if sel(r)]
        if not sub:
            return {"label": label, "n": 0}
        out = {"label": label, "n": len(sub), "games": len({r["game"] for r in sub})}
        # 不分割的参考：全体均值
        out["mean_dX"] = sum(r["dX"] for r in sub) / len(sub)
        out["mean_dY"] = sum(r["dY"] for r in sub) / len(sub)
        # 无限制逐小局神谕（上界，含运气）
        out["oracle_hand"] = summarize([max(r["dX"], r["dY"]) - r["dX"] for r in sub],
                                       [r["game"] for r in sub])
        # 逐小局神谕 vs 更好臂（在 D 上）
        deltas, games, oracle_ins, oracle_ins_g = [], [], [], []
        for fold in (0, 1):                       # fold = game % 2 的**评估**侧
            fit = [r for r in sub if r["game"] % 2 != fold]
            ev = [r for r in sub if r["game"] % 2 == fold]
            # 基准臂：拟合折上更好的那条
            mA = sum(r["dX"] for r in fit) / len(fit)
            mB = sum(r["dY"] for r in fit) / len(fit)
            bench = "X" if mA >= mB else "Y"
            # 桶级选臂（只用拟合折）
            bycell = defaultdict(list)
            for r in fit:
                bycell[r[cells_key]].append(r["dX"] - r["dY"])
            pick = {}
            for c, v in bycell.items():
                pick[c] = "X" if sum(v) / len(v) >= 0 else "Y"
            fb = "X" if mA >= mB else "Y"
            for r in ev:
                c = pick.get(r[cells_key], fb)
                dch = r["dX"] if c == "X" else r["dY"]
                db = r["dX"] if bench == "X" else r["dY"]
                deltas.append(dch - db)
                games.append(r["game"])
            # 同折的**利用全数据**桶神谕（in-sample 上界，仅作参照）
            bycell_all = defaultdict(list)
            for r in sub:
                bycell_all[r[cells_key]].append(r["dX"] - r["dY"])
            pick_all = {c: ("X" if sum(v) / len(v) >= 0 else "Y") for c, v in bycell_all.items()}
            mb = "X" if out["mean_dX"] >= out["mean_dY"] else "Y"
            for r in ev:
                c = pick_all[r[cells_key]]
                dch = r["dX"] if c == "X" else r["dY"]
                db = r["dX"] if mb == "X" else r["dY"]
                oracle_ins.append(dch - db)
                oracle_ins_g.append(r["game"])
        out["oos_router"] = summarize(deltas, games)
        out["insample_cell_oracle"] = summarize(oracle_ins, oracle_ins_g)
        out["n_cells_fit"] = None
        # 代价：D 覆盖率
        return out

    main_D = run_routing(lambda r: r["D"], f"{arm} D（分歧域）")
    ctrl1 = run_routing(lambda r: not r["D"], f"{arm} ¬D（非分歧域，对照 1）")
    ctrl1b = run_routing(lambda r: (not r["D"]) and r["prefix_clean"],
                         f"{arm} ¬D ∧ 前缀干净（对照 1b：两臂逐位同局）")
    ctrl1c = run_routing(lambda r: (not r["D"]) and not r["prefix_clean"],
                         f"{arm} ¬D ∧ 本场已分歧（对照 1c）")
    rep["routing"] = {"main_D": main_D, "control_nonD": ctrl1, "control_nonD_prefix_clean": ctrl1b,
                      "control_nonD_after_div": ctrl1c}

    # ---------------- 5. 对照 2：均值回归 ----------------
    dX = [r["dX"] for r in recs]
    dY = [r["dY"] for r in recs]
    gain_oracle = [max(r["dX"], r["dY"]) - r["dX"] for r in recs]
    rep["mean_reversion"] = {
        "rho_gain_vs_bench": pearson(gain_oracle, dX),
        "rho_dX_vs_dY": pearson(dX, dY),
    }
    order = sorted(recs, key=lambda r: r["dX"])
    trim = {}
    for pct in (0, 5, 10, 20, 30, 50):
        cut = int(len(order) * pct / 100)
        keep = order[cut:]
        trim[str(pct)] = {"n": len(keep),
                          "gain": sum(max(r["dX"], r["dY"]) - r["dX"] for r in keep) / len(keep)}
    rep["mean_reversion"]["trim_bench_worst"] = trim
    rep["mean_reversion"]["n_div_rows_hist"] = {
        str(n): sum(1 for r in recs if r["n_div_rows_hand"] == n)
        for n in sorted({r["n_div_rows_hand"] for r in recs})}

    (Path(a.out_json)).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
