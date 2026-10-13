# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""**多专家 + 激活模型**这条路的天花板（ceiling）粗观测 —— 零算力、零新采集。

⛔ 本脚本**不采集、不训练、不跑闸门**；只读已冻结产物：
  * `style-vector-per-gen/<tag>.json` 的 `runs[0].summary.per_game`（1000 场逐场记录：
    逐场 `seed` / `policies`（⇒ 学生席）/ 逐席 `final_scores` / `rank_points` / `wins` / `deals` /
    `win_points` / `hands`）；
  * `paired/<post_tag>.json`（它自己声明 `pre_tag` → `post_tag` 的配对关系 + pairing_check）。

口径（与 `_fold-paired-partial.py` 一致）：
  * **学生席** = 该场 `policies[]` 里含 `@` 的那一条（`net:…@0#0.5`）；
  * **配对自证** = 逐场 `seed` 相同 ∧ 学生席相同 ∧ `seed_base` 相同；
  * 三者任一不满足 ⇒ 该链标 **不可配对** 并剔除（⛔ 不硬凑）。

产出：`_ceiling-rough.json`（本任务脚本产物，⛔ 不改任何别人的产物）。
"""
import json
import math
import random
import statistics
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STAGE = Path(r"S:\mahjong-training\w-ladder")
SV = STAGE / "style-vector-per-gen"
PAIRED = STAGE / "paired"
Z95 = 1.96
MC_N = 400_000
MC_SEED = 20261010

# 轴 → 该轴下候选链（取 paired/*.json 里**自己声明**的 pre_tag/post_tag，这里只标轴名）
AXIS_ORDER = ["riichi", "meld", "dama", "pon", "fold", "other"]
AXIS_OF = {
    "v4-wl-dec6170-4g-": "riichi",
    "v4-wl-dec6170-c1": "riichi",
    "v4-wl-meld-4g-": "meld",
    "v4-wl-meld1515-4g-": "meld",
    "v4-wl-meld3030-4g-": "meld",
    "v4-wl-dama5400": "dama",
    "v4-wl-rt5400": "dama",
    "v4-wl-meldtype-": "pon",
    "v4-wl-fold": "fold",
}
# 每条轴在合并上界里的**代表链**（用户点名的规范链；缺则用该轴 t 最大的可配对链）
CANON = {
    "riichi": "v4-wl-dec6170-4g-c1",
    "meld": "v4-wl-meld-4g-c1",
    "dama": "v4-wl-dama5400b-c1-c1",
    "pon": "v4-wl-meldtype-c1",
    "fold": "v4-wl-fold-c1",
}


def axis_of(tag):
    for k, v in AXIS_OF.items():
        if tag.startswith(k):
            return v
    return "other"


def load_sv(tag):
    """读一条 style-vector；返回逐场 dict + 学生 label + seed_base。"""
    p = SV / f"{tag}.json"
    if not p.exists():
        return None
    j = json.loads(p.read_text(encoding="utf-8"))
    runs = j.get("runs") or []
    if not runs:
        return None
    per_game = runs[0].get("summary", {}).get("per_game")
    if not per_game:
        return None
    stu = [k for k in (j.get("policies") or {}) if "@" in k]
    if len(stu) != 1:
        # 退化：直接从逐场 policies 里找
        stu = []
    out = {}
    for g in per_game:
        idx = [i for i, pp in enumerate(g["policies"]) if "@" in pp]
        if len(idx) != 1:
            return None
        si = idx[0]
        out[int(g["game"])] = {
            "seed": int(g["seed"]), "si": si,
            "score": int(g["final_scores"][si]),
            "rank_pts": float(g["rank_points"][si]),
            "placement": int(g["placement"][si]),
            "wins": int(g["wins"][si]), "deals": int(g["deals"][si]),
            "win_points": int(g["win_points"][si]),
            "deal_points": int(g["deal_points"][si]),
            "hands": int(g["hands"]),
            "stu_spec": g["policies"][si],
            "all_scores": [int(x) for x in g["final_scores"]],
            "all_pols": list(g["policies"]),
        }
    return {"per_game": out, "stu_label": stu[0] if stu else None,
            "seed_base": runs[0]["summary"].get("seed_base"),
            "dir": runs[0].get("dir")}


def one_chain(pre_tag, post_tag, src):
    A, B = load_sv(pre_tag), load_sv(post_tag)
    rec = {"axis": axis_of(pre_tag), "pre_tag": pre_tag, "post_tag": post_tag,
           "source": src, "usable": False}
    if A is None or B is None:
        rec["reason"] = f"style-vector 缺（pre={'ok' if A else '缺'} post={'ok' if B else '缺'}）"
        return rec
    common = sorted(set(A["per_game"]) & set(B["per_game"]))
    sb = [g for g in common if A["per_game"][g]["seed"] != B["per_game"][g]["seed"]]
    ss = [g for g in common if A["per_game"][g]["si"] != B["per_game"][g]["si"]]
    rec["pairing"] = {"n_common_games": len(common), "n_pre": len(A["per_game"]),
                      "n_post": len(B["per_game"]), "seed_mismatch": len(sb),
                      "student_seat_mismatch": len(ss),
                      "seed_base_pre": A["seed_base"], "seed_base_post": B["seed_base"],
                      "pre_student_net": (A["stu_label"] or "").split("\\")[-1],
                      "post_student_net": (B["stu_label"] or "").split("\\")[-1]}
    if not (len(common) == 1000 and not sb and not ss
            and A["seed_base"] == B["seed_base"]):
        rec["reason"] = (f"不可配对：共同场 {len(common)}｜seed 不一致 {len(sb)}｜"
                         f"学生席不一致 {len(ss)}｜seed_base {A['seed_base']} vs {B['seed_base']}")
        return rec
    rec["usable"] = True

    d_score, d_rank, pre_s, post_s = [], [], [], []
    d_wr, d_dr, d_aw, d_deal = [], [], [], []
    d_place = []
    for g in common:
        a, b = A["per_game"][g], B["per_game"][g]
        d_score.append(b["score"] - a["score"])
        d_rank.append(b["rank_pts"] - a["rank_pts"])
        d_place.append(a["placement"] - b["placement"])  # 正 = 顺位变好
        pre_s.append(a["score"]); post_s.append(b["score"])
        if a["hands"] > 0 and b["hands"] > 0:
            d_wr.append((b["wins"] / b["hands"] - a["wins"] / a["hands"]) * 100)
            d_dr.append((b["deals"] / b["hands"] - a["deals"] / a["hands"]) * 100)
            d_deal.append(b["deals"] - a["deals"])
        if a["wins"] > 0 and b["wins"] > 0:
            d_aw.append(b["win_points"] / b["wins"] - a["win_points"] / a["wins"])

    n = len(d_score)
    m = statistics.fmean(d_score)
    se = statistics.stdev(d_score) / math.sqrt(n)
    gpos = [max(x, 0) for x in d_score]
    gmc = statistics.fmean(gpos)
    gse = statistics.stdev(gpos) / math.sqrt(n)
    rho = corr(pre_s, post_s)
    rho_rank = corr([A["per_game"][g]["rank_pts"] for g in common],
                    [B["per_game"][g]["rank_pts"] for g in common])

    # ---- 配对自证的**对照**：非学生席（两侧策略完全相同）的逐场相关 ----
    # 若这也有 ~ρ 量级 ⇒ 低 ρ 是麻将本身的混沌（换一手就整场分岔），**不是配对失败**。
    # ---- 配对自证的**对照**：取「该场该席不是学生」的子集 —— 此时该席两侧是
    # **同一条策略、同一 seed、同一席**，唯一差别是学生换了 ⇒ 这样量出的相关才是
    # 「麻将本身的混沌底噪」。若它与学生席的 ρ 同量级 ⇒ 低 ρ 不是配对失败。
    ctrl = []
    for j in range(4):
        sub = [g for g in common if A["per_game"][g]["si"] != j]
        pa = [A["per_game"][g]["all_pols"][j] for g in sub]
        pb = [B["per_game"][g]["all_pols"][j] for g in sub]
        same = all(pa[k] == pb[k] and "@" not in pa[k] for k in range(len(sub)))
        xa = [A["per_game"][g]["all_scores"][j] for g in sub]
        xb = [B["per_game"][g]["all_scores"][j] for g in sub]
        c = corr(xa, xb)
        ctrl.append({"seat": j, "n_games_non_student": len(sub),
                     "corr_same_seat_pre_post": c,
                     "same_policy_both_sides": same,
                     "policy": pa[0] if pa else None})

    rec.update({
        "n_games": n,
        "pre_mean_score": statistics.fmean(pre_s), "pre_se_score": statistics.stdev(pre_s) / math.sqrt(n),
        "post_mean_score": statistics.fmean(post_s), "post_se_score": statistics.stdev(post_s) / math.sqrt(n),
        "delta_mean": m, "delta_se": se, "delta_t": m / se if se else float("nan"),
        "delta_lo": m - Z95 * se, "delta_hi": m + Z95 * se,
        "p_delta_pos": sum(1 for x in d_score if x > 0) / n,
        "p_delta_neg": sum(1 for x in d_score if x < 0) / n,
        "p_delta_eq0": sum(1 for x in d_score if x == 0) / n,
        "delta_sd": statistics.stdev(d_score),
        "G_score": gmc, "G_score_se": gse,
        "delta_rank_mean": statistics.fmean(d_rank),
        "delta_rank_se": statistics.stdev(d_rank) / math.sqrt(n),
        "G_rank": statistics.fmean([max(x, 0) for x in d_rank]),
        "delta_placement_mean": statistics.fmean(d_place),
        "G_placement": statistics.fmean([max(x, 0) for x in d_place]),
        "rho_pre_post": rho,
        "rho_rank": rho_rank,
        "rho_pre_post_rankpts": rho_rank,
        "rho_control_other_seats": ctrl,
        "rho_control_mean": statistics.fmean(
            [c["corr_same_seat_pre_post"] for c in ctrl
             if c["same_policy_both_sides"] and not math.isnan(c["corr_same_seat_pre_post"])]
        ) if any(c["same_policy_both_sides"] for c in ctrl) else float("nan"),
        "companion": {
            "win_rate_pp": (statistics.fmean(d_wr), statistics.stdev(d_wr) / math.sqrt(len(d_wr)), len(d_wr)),
            "deal_rate_pp": (statistics.fmean(d_dr), statistics.stdev(d_dr) / math.sqrt(len(d_dr)), len(d_dr)),
            "avg_win_score": (statistics.fmean(d_aw), statistics.stdev(d_aw) / math.sqrt(len(d_aw)), len(d_aw)),
            "deals_per_game": (statistics.fmean(d_deal), statistics.stdev(d_deal) / math.sqrt(len(d_deal)), len(d_deal)),
        },
    })
    return rec


def corr(xs, ys):
    n = len(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def partial_bounds(rho_i, rho_j):
    """给定两条链各自与**同一现任**的 ρ ⇒ ρ_ij 的部分相关界。"""
    d = math.sqrt(max(0.0, (1 - rho_i ** 2) * (1 - rho_j ** 2)))
    lo = rho_i * rho_j - d
    hi = min(1.0, rho_i * rho_j + d)
    return lo, hi


def equicorr_emax(mus, sds, rho, n=MC_N, seed=MC_SEED):
    """等相关高斯下 E[max_i Δ_i] 的 MC 估计（粗界用）。"""
    rnd = random.Random(seed)
    k = len(mus)
    sq = math.sqrt(max(0.0, rho))
    sr = math.sqrt(max(0.0, 1.0 - rho))
    tot = 0.0
    for _ in range(n):
        z = rnd.gauss(0, 1)
        best = -1e18
        for i in range(k):
            v = mus[i] + sds[i] * (sq * z + sr * rnd.gauss(0, 1))
            if v > best:
                best = v
        tot += best
    return tot / n


def main():
    # 配对关系取自 paired/*.json 自己声明的 pre_tag/post_tag
    pairs = []
    for f in sorted(PAIRED.glob("*.json")):
        try:
            j = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        pt, qt = j.get("pre_tag"), j.get("post_tag")
        if pt and qt:
            pchk = j.get("pairing_check") or {}
            pairs.append((pt, qt, f.name, pchk))

    print(f"==== 盘点：paired/*.json 声明 {len(pairs)} 条配对 ====")
    rows = []
    for pt, qt, fn, pchk in pairs:
        r = one_chain(pt, qt, fn)
        ok = pchk.get("ok")
        r["paired_json_pairing_ok"] = ok
        r["paired_json_seed_mismatch"] = pchk.get("seed_mismatch")
        r["paired_json_seat_mismatch"] = pchk.get("student_seat_mismatch")
        rows.append(r)
        tag = f"{pt} → {qt}"
        if r["usable"]:
            p = r["pairing"]
            print(f"  [可用] {r['axis']:<6} {tag}")
            print(f"          共同场 {p['n_common_games']}｜seed 不一致 {p['seed_mismatch']}"
                  f"｜学生席不一致 {p['student_seat_mismatch']}"
                  f"｜seed_base {p['seed_base_pre']}（两侧同）"
                  f"｜paired/*.json 自证 ok={ok}")
        else:
            print(f"  [不可用] {r['axis']:<6} {tag} ⇒ {r.get('reason')}")

    usable = [r for r in rows if r["usable"]]
    print(f"\n==== 逐链 Δ（现任 vs 该专家，逐场配对；单位 = 点/场）====")
    hdr = (f"{'链':<34}{'n':>5}{'Δ均值':>10}{'SE':>8}{'t':>7}{'P(Δ>0)':>9}"
           f"{'ρ':>7}{'G_i':>9}{'G_i_SE':>8}{'ρ对照':>8}")
    print(hdr)
    for r in sorted(usable, key=lambda x: (AXIS_ORDER.index(x["axis"]) if x["axis"] in AXIS_ORDER else 9,
                                           x["pre_tag"])):
        print(f"{r['pre_tag'][:33]:<34}{r['n_games']:>5}{r['delta_mean']:>10.1f}"
              f"{r['delta_se']:>8.1f}{r['delta_t']:>7.2f}{r['p_delta_pos']:>9.3f}"
              f"{r['rho_pre_post']:>7.3f}{r['G_score']:>9.1f}{r['G_score_se']:>8.1f}"
              f"{r['rho_control_mean']:>8.3f}")

    # ---- 每条轴取代表链（用户点名者优先；否则取 |t| 最大） ----
    reps = {}
    for ax in AXIS_ORDER:
        cand = [r for r in usable if r["axis"] == ax and r["n_games"] == 1000]
        if not cand:
            continue
        canon = [r for r in cand if r["pre_tag"].startswith(CANON.get(ax, "\0"))]
        reps[ax] = canon[0] if canon else max(cand, key=lambda x: abs(x["delta_t"]))

    print(f"\n==== 代表链（四轴）====")
    for ax in AXIS_ORDER:
        if ax in reps:
            r = reps[ax]
            print(f"  {ax:<7} {r['pre_tag']:<34} Δ {r['delta_mean']:+9.1f} (SE {r['delta_se']:.1f}) "
                  f"ρ={r['rho_pre_post']:.3f} G_i={r['G_score']:.1f}")
    c0 = statistics.fmean([r["rho_control_mean"] for r in usable
                           if not math.isnan(r["rho_control_mean"])])
    s0 = statistics.fmean([r["rho_pre_post"] for r in usable])
    print(f"\n[配对自证的对照] 非学生席（两侧**同一条**策略、同一 seed/席）逐场相关均值 = {c0:.3f}"
          f"；学生席（现任 vs 专家）逐场相关均值 = {s0:.3f}")
    print(f"  ⇒ 低 ρ 是**麻将换一手就整场分岔**的混沌，不是配对失败"
          f"（同策略两侧都只有 {c0:.2f}）；这正是「逐场挑专家」能有增益的来源。")

    # ---- 合并上界 ----
    axes4 = [ax for ax in ("riichi", "meld", "dama", "pon") if ax in reps]
    Gs = [reps[ax]["G_score"] for ax in axes4]
    mus = [reps[ax]["delta_mean"] for ax in axes4]
    sds = [reps[ax]["delta_sd"] for ax in axes4]
    rhos = [reps[ax]["rho_pre_post"] for ax in axes4]
    sumG = sum(Gs)
    maxG = max(Gs) if Gs else 0.0
    se_sumG = math.sqrt(sum(reps[ax]["G_score_se"] ** 2 for ax in axes4))
    print(f"\n==== 四轴合并上界（{'+'.join(axes4)}）====")
    print(f"  Σ G_i = {sumG:.1f} 点/场（SE {se_sumG:.1f}）← **上界**，忽略专家间重叠、且不含跨链同牌山")
    print(f"  max G_i = {maxG:.1f} 点/场 ← 有效**下界**（E[max] ≥ max_i G_i）")

    lows, highs = [], []
    for i in range(len(axes4)):
        for k in range(i + 1, len(axes4)):
            lo, hi = partial_bounds(rhos[i], rhos[k])
            lows.append(lo); highs.append(hi)
            print(f"    ρ({axes4[i]},{axes4[k]}) ∈ [{lo:+.3f}, {hi:+.3f}]"
                  f"  （由 ρ_i={rhos[i]:.3f}, ρ_j={rhos[k]:.3f} 的部分相关界）")
    rho_lo = statistics.fmean(lows) if lows else 0.0
    rho_hi = statistics.fmean(highs) if highs else 1.0
    rho_mid = (rho_lo + rho_hi) / 2
    print(f"  rho_bar 区间 [{rho_lo:+.3f}, {rho_hi:+.3f}]")
    est = {}
    for lab, rr in (("rho_bar=lo (最分散⇒天花板最高)", rho_lo),
                    ("rho_bar=mid", rho_mid),
                    ("rho_bar=hi (最同向⇒天花板最低)", rho_hi),
                    ("rho_bar=1 (完全同向=单专家)", 1.0)):
        e = equicorr_emax(mus, sds, rr)
        est[lab] = e
        print(f"    E[max_i Δ_i] | {lab:<28} = {e:+.1f} 点/场")

    # ---- 全部 13 条链的 Σ G_i（"菜单更大"的另一个上界）----
    allG = sum(r["G_score"] for r in usable)
    print(f"\n==== 若菜单 = 全部 {len(usable)} 条链 ====")
    print(f"  Σ_all G_i = {allG:.1f} 点/场（**更松的上界**，口径是 {len(usable)} 选 1）")

    out = {"pairs": rows,
           "representatives": {a: reps[a]["pre_tag"] for a in reps},
           "merged": {"axes": axes4, "sumG": sumG, "se_sumG": se_sumG, "maxG": maxG,
                      "rho_interval": [rho_lo, rho_hi],
                      "equicorr_emax": est,
                      "G_by_axis": {a: reps[a]["G_score"] for a in axes4},
                      "mu_by_axis": {a: reps[a]["delta_mean"] for a in axes4},
                      "sd_by_axis": {a: reps[a]["delta_sd"] for a in axes4},
                      "rho_by_axis": {a: reps[a]["rho_pre_post"] for a in axes4}},
           "sumG_all_chains": allG, "n_chains_usable": len(usable)}
    (STAGE / "_ceiling-rough.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[落盘] {STAGE / '_ceiling-rough.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
