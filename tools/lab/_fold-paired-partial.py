# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`fold` 轴：**零新采集**能取到的全部读数（补采轨迹已被永久删除 ⇒ 主读数取不到）。

能取的（两来源都是**已落盘的冻结产物**）：
  * **配对自证**：`style-vector-per-gen/<tag>.json`（训练前）与 `<tag>-POST.json`（训练后）的
    `runs[0].summary.per_game` 都带逐场 `seed` / `policies`（⇒ 学生席）/ 逐席 `wins`/`deals`/
    `win_points` + 该场 `hands` ⇒ 逐场配对的 seed / 学生席一致性**可当场自证**；
  * **三个伴随量的逐场配对 Δ/CI**：和了率 `wins/hands`、放铳率 `deals/hands`、
    打点 `win_points/wins` —— 以及**止损量** `deals`（次/场/席）；
  * **另外三个伴随量只有聚合口径**（立直率 / 副露率 / 立直后和了率：逐场手级信息在补采轨迹里，
    已随轨迹删除）；
  * **一代 SE 的功效估算**：用训练前轨迹的逐场 `fold_rate` 分布 + 上面三个量的
    "配对效率"（配对 SE / 非配对 SE）推出主判据一代的 SE ⇒ 要几条链。

⛔ 本脚本**不采集、不训练**；它读的全是已落盘的 json（外加只读训练前的 1000 个 jsonl 一次）。
⚠ 主判据 `fold_rate` 的配对读数**不在**这里（补采侧没有行级数据）—— 别把它当主读数。
"""
import json
import statistics
import sys
from pathlib import Path

STAGE = Path(r"S:\mahjong-training\w-ladder")
REPO = Path(r"C:\Users\HP\source\games\mahjong")
SV = STAGE / "style-vector-per-gen"
TAG = "v4-wl-fold-c1-g01"
Z95 = 1.96
TOL = 2.0                     # `COMPANION_TOL`（pp，单侧）
sys.path.insert(0, str(REPO / "python"))


def load(tag):
    j = json.loads((SV / f"{tag}.json").read_text(encoding="utf-8"))
    s = j["runs"][0]["summary"]
    stu_lab = next(k for k in j["policies"] if "@" in k)
    out = {}
    for g in s["per_game"]:
        si = next(i for i, p in enumerate(g["policies"]) if "@" in p)
        out[int(g["game"])] = {"seed": int(g["seed"]), "si": si, "hands": int(g["hands"]),
                               "wins": int(g["wins"][si]), "deals": int(g["deals"][si]),
                               "win_points": int(g["win_points"][si])}
    dims = j["policies"][stu_lab]["dims"]
    cnt = j["policies"][stu_lab]["counts"]
    return out, dims, cnt, s.get("seed_base"), stu_lab


def paired(pre, post, num, den, label, unit="pct"):
    diffs, unpaired = [], []
    for g in sorted(set(pre) & set(post)):
        a, b = pre[g], post[g]
        if den and (a[den] <= 0 or b[den] <= 0):
            continue
        ra = a[num] / a[den] if den else float(a[num])
        rb = b[num] / b[den] if den else float(b[num])
        diffs.append(rb - ra)
    # 非配对 SE（两侧各自 sd²/n 相加再开方）= "配平配对到底省了多少方差"的对照
    pre_r, post_r = [], []
    for g in sorted(set(pre) & set(post)):
        a, b = pre[g], post[g]
        if den and (a[den] <= 0 or b[den] <= 0):
            continue
        pre_r.append(a[num] / a[den] if den else float(a[num]))
        post_r.append(b[num] / b[den] if den else float(b[num]))
    n = len(diffs)
    scale = 100.0 if unit == "pct" else 1.0
    m = statistics.fmean(diffs)
    se = statistics.stdev(diffs) / n ** 0.5
    se_un = ((statistics.stdev(pre_r) ** 2 + statistics.stdev(post_r) ** 2) / n) ** 0.5
    return {"label": label, "unit": unit, "n_games": n,
            "pre": statistics.fmean(pre_r) * scale, "post": statistics.fmean(post_r) * scale,
            "delta": m * scale, "se": se * scale,
            "lo": (m - Z95 * se) * scale, "hi": (m + Z95 * se) * scale,
            "t": (m / se if se else float("nan")),
            "se_unpaired": se_un * scale, "pairing_factor": (se / se_un if se_un else float("nan")),
            "sd_single_side": statistics.stdev(pre_r) * scale}


def pre_fold_per_game():
    """训练前轨迹的逐场 `fold_rate`（只读一次；用 `tools/w-ladder.py` 的**判据侧**实现）。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("wl", REPO / "tools" / "w-ladder.py")
    wl = importlib.util.module_from_spec(spec)
    sys.modules["wl"] = wl
    spec.loader.exec_module(wl)
    per = wl._student_per_game(STAGE / "traces" / TAG)
    rates, fr, thr = [], [], []
    for g, c in per.items():
        if c["f_threat_rows"] > 0:
            rates.append(c["f_fold_rows"] / c["f_threat_rows"])
        fr.append(c["f_fold_rows"])
        thr.append(c["f_threat_rows"])
    return rates, fr, thr


def main():
    pre, pdim, pcnt, pseed, plab = load(TAG)
    post, qdim, qcnt, qseed, qlab = load(TAG + "-POST")
    common = sorted(set(pre) & set(post))
    seed_bad = [g for g in common if pre[g]["seed"] != post[g]["seed"]]
    seat_bad = [g for g in common if pre[g]["si"] != post[g]["si"]]
    print(f"== `fold` 轴 · **零采集**能取到的读数 · {TAG} ==")
    print(f"[配对自证·来自两侧冻结 json 的逐场 `seed`/`policies`] 共同场 {len(common)}"
          f"（pre {len(pre)} / post {len(post)}）｜seed 不一致 {len(seed_bad)}"
          f"｜学生席不一致 {len(seat_bad)}｜seed_base pre={pseed} post={qseed}"
          f"｜学生串 pre={plab.split(chr(92))[-1]} / post={qlab.split(chr(92))[-1]}")
    if not (len(common) == 1000 and not seed_bad and not seat_bad and pseed == qseed):
        print("⛔ 配对不成立 ⇒ 不出读数")
        return 1
    print("\n[★ 可配对的伴随量（逐场 Δ/CI，训练前 → 训练后）]")
    rows = [paired(pre, post, "wins", "hands", "和了率 win_rate"),
            paired(pre, post, "deals", "hands", "放铳率 deal_rate"),
            paired(pre, post, "win_points", "wins", "打点 avg_win_score", unit="abs"),
            paired(pre, post, "deals", None, "★止损量 `deals`（次/场/席）", unit="abs"),
            paired(pre, post, "hands", None, "对照 `hands`（每场小局数）", unit="abs")]
    for r in rows:
        print(f"   {r['label']:<28} {r['pre']:>9.4f} → {r['post']:>9.4f}  "
              f"Δ {r['delta']:+.4f}（SE {r['se']:.4f}，t={r['t']:+.2f}，95% CI "
              f"[{r['lo']:+.4f},{r['hi']:+.4f}]，n={r['n_games']}）"
              f"｜非配对 SE {r['se_unpaired']:.4f} ⇒ 配对效率 {r['pairing_factor']:.3f}")
    deals = next(r for r in rows if r["label"].startswith("★止损量"))
    print(f"\n[止损判据] `deals` Δ = {deals['delta']:+.4f} 次/场/席"
          f"（预注册阈值 ≥ +0.10 ⇒ {'**触发结案**' if deals['delta'] >= 0.10 else '未触发'}）")
    print("\n[单侧伴随护栏（越界 ⇔ CI 上界 < 0 或 点估计 < −2.0pp）]")
    for r in rows[:3]:
        viol = (r["hi"] < 0) or (r["delta"] < -TOL)
        print(f"   {r['label']:<28} CI [{r['lo']:+.4f},{r['hi']:+.4f}]"
              f" 点估计 {r['delta']:+.4f} ⇒ {'**越界**' if viol else '未越界'}")
    print("\n[只有聚合口径的三个伴随量（逐场手级信息随补采轨迹删除）]")
    for k in ("riichi_rate", "meld_rate", "riichi_win_rate", "win_rate", "deal_rate",
              "avg_win_score"):
        a, b = pdim.get(k), qdim.get(k)
        if a is None:
            continue
        s = 100.0 if k.endswith("_rate") else 1.0
        print(f"   {k:<16} {a * s:.4f} → {b * s:.4f}（Δ {(b - a) * s:+.4f}"
              f"{'pp' if s == 100 else ''}，**无 CI**）")
    rates, fr, thr = pre_fold_per_game()
    n = len(rates)
    mean_r = statistics.fmean(rates)
    sd_r = statistics.stdev(rates)
    print(f"\n[训练前逐场 `fold_rate` 分布（{n} 场有被威胁行）] 均值 {mean_r:.4%}｜"
          f"sd {sd_r:.4%}｜每场 降り {statistics.fmean(fr):.2f} 行 / 被威胁 {statistics.fmean(thr):.1f} 行")
    se_un_fold = sd_r / 1000 ** 0.5
    rbar = statistics.fmean([r["pairing_factor"] for r in rows[:3]])
    se_pair_fold = rbar * se_un_fold
    print(f"[一代功效估算] 单侧 sd {sd_r:.4%} ⇒ 非配对 SE(1000 场) {se_un_fold:.4%}；"
          f"用三个可配对量的**配对效率均值 {rbar:.3f}** 折算 ⇒ **一代配对 SE ≈ {se_pair_fold:.4%}"
          f"（{se_pair_fold * 100:.2f}pp）**")
    for e in (0.01, 0.02):
        print(f"   要检出 Δ = +{e * 100:.0f}pp（2SE 排除 0）：需要 "
              f"{(Z95 * se_pair_fold / e) ** 2:.2f} 条链（每条 1000 场）")
    res = {"tag": TAG, "zero_new_collection": True,
           "main_readout_available": False,
           "main_readout_reason": "补采侧（traces-post/v4-wl-fold-c1-g01）的 1000 个 jsonl 已被"
                                  "永久删除（回收站为空）⇒ 行级 fold_rate 的补采侧逐场读数不存在；"
                                  "冻结 json 只有手级/场级合计",
           "pairing": {"games": len(common), "seed_mismatch": len(seed_bad),
                       "student_seat_mismatch": len(seat_bad), "seed_base": pseed},
           "paired": {r["label"]: r for r in rows},
           "aggregate_only": {k: {"pre": pdim.get(k), "post": qdim.get(k)}
                              for k in ("riichi_rate", "meld_rate", "riichi_win_rate")},
           "pre_fold": {"games_with_threat": n, "mean": mean_r, "sd": sd_r,
                        "fold_rows_mean": statistics.fmean(fr),
                        "threat_rows_mean": statistics.fmean(thr)},
           "power": {"se_unpaired_1chain": se_un_fold, "pairing_factor_mean": rbar,
                     "se_paired_1chain": se_pair_fold,
                     "chains_for_1pp": (Z95 * se_pair_fold / 0.01) ** 2,
                     "chains_for_2pp": (Z95 * se_pair_fold / 0.02) ** 2},
           "stop_loss": {"deals_delta": deals["delta"], "tol": 0.10,
                         "triggered": bool(deals["delta"] >= 0.10)}}
    (STAGE / "_fold-verify" / "paired-partial.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[落盘] {STAGE / '_fold-verify' / 'paired-partial.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
