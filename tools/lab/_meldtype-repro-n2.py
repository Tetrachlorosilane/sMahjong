# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""`pon` 轴 **n=2 复现**：把两条独立链的 `paired/*-POST.json` 合起来读（判据见
`PREREGISTRATION-MELD-TYPE.md` §6，**写在看到本轮结果之前**）。

  ① 每条链各自的**行级** `pon_type_rate` 逐场配对 Δ + 95% CI（方向为升）；
  ② 两链**逆方差加权**固定效应合并（链间 seed 族不同 ⇒ ⛔ 不能逐场配对，只能合并统计量）
     + **等权平均**对照 + **链间极差** `|Δ₁−Δ₂|`；
  ③ **"已复现" ⇔ 两链 Δ 同号为正 ∧ 合并 CI 排除 0**（两个条件缺一不可）；
  ④ 六个伴随量按**新方向判据**（`bad_when` 由轴的意图定；⛔ 不是方向无关的旧式）：
     `down`（越多越好）：和了率/副露率/立直率/打点/立直后和了率；
     `up`  （越少越好）：**放铳率** ⇒ 越界 ⇔ `lo > 0` 或 `Δ > +tol`；
  ⑤ 止损：任一伴随量**在坏方向上**越界、或 `deals` Δ ≥ +0.10/场/席 ⇒ 立即结案。

⚠ 判据**只有一处实现**（`tools/w-ladder.py` 的 `companion_verdict`）；本脚本 import 它，
⛔ 不在这里抄第二份公式（抄第二份就是两套判据漂移的起点）。
"""
import importlib.util
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                     # noqa: BLE001
    pass

REPO = Path(r"C:\Users\HP\source\games\mahjong")
Z95 = 1.96
spec = importlib.util.spec_from_file_location("wl", REPO / "tools" / "w-ladder.py")
wl = importlib.util.module_from_spec(spec)
sys.modules["wl"] = wl
spec.loader.exec_module(wl)

#: 六个伴随量（`PREREGISTRATION-MELD-TYPE.md` §6.2）+ 两个读数
COMPANIONS = ("riichi_rate", "meld_rate", "win_rate", "deal_rate", "avg_win_score",
              "riichi_win_rate")
TOL = {"avg_win_score": 150.0}


def combine(rows):
    w = [1.0 / (r["se"] ** 2) for r in rows]
    sw = sum(w)
    return (sum(wi * r["delta"] for wi, r in zip(w, rows)) / sw, (1.0 / sw) ** 0.5)


def main(argv=None) -> int:
    paths = [Path(p) for p in (argv or sys.argv[1:])]
    docs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]

    print("=== ① 配对自证（每条链：seed / 学生席不一致数）===")
    for d, p in zip(docs, paths):
        pc = d.get("pairing_check") or {}
        print(f"  {d.get('label'):<24} {p.name:<30} games={pc.get('games')} "
              f"seed_mismatch={pc.get('seed_mismatch')} student_seat_mismatch="
              f"{pc.get('student_seat_mismatch')} ok={pc.get('ok')} "
              f"seed_base={pc.get('seed_base_pre')}/{pc.get('seed_base_post')}")
    print()

    print("=== ② 行级对账（判据侧独立实现 == 奖励侧账；pre 那一侧有账）===")
    for d in docs:
        mt = d["meld_type"]
        t = mt["pre"]["totals"]
        print(f"  {d.get('label'):<24} reconciled={mt['pre'].get('reconciled')} "
              f"audit_is_recomputed={mt['pre'].get('audit_is_recomputed')}")
        print(f"      pre  totals: 决策 {t['pt_decision_rows']} / 鸣牌 {t['pt_meld_rows']} / "
              f"碰 {t['pt_pon_rows']} / 吃 {t['pt_chi_rows']} / 杠 {t['pt_kan_rows']} / "
              f"付奖 {t['pt_paid_rows']} / 拦下 {t['pt_blocked_rows']}")
        print(f"      冻结账差（⛔ 只报不抹）: "
              f"{json.dumps(mt.get('frozen_audit', {}).get('delta_vs_recomputed'), ensure_ascii=False)}")
        print(f"      aggregate pre : {json.dumps(mt['aggregate']['pre'], ensure_ascii=False)}")
        print(f"      aggregate post: {json.dumps(mt['aggregate']['post'], ensure_ascii=False)}")
    print()

    print("=== ③ 主判据 `pon_type_rate`（行级：碰的行/鸣牌行；方向**为升**）===")
    rows = []
    for d in docs:
        b = d["paired_delta"]["pon_type_rate"]
        rows.append({"delta": float(b["delta_pp"]), "se": float(b["se_pp"]),
                     "n": int(b["n_games"]), "label": d.get("label")})
        print(f"  {d.get('label'):<24} {b['pre']:.4%} → {b['post']:.4%}  "
              f"Δ={b['delta_pp']:+.4f}pp SE={b['se_pp']:.4f} t={b['t']:+.2f} "
              f"CI[{b['lo_pp']:+.4f},{b['hi_pp']:+.4f}] n={b['n_games']}")
    dc, sec = combine(rows)
    lo, hi = dc - Z95 * sec, dc + Z95 * sec
    eqd = sum(r["delta"] for r in rows) / len(rows)
    eqse = (sum(r["se"] ** 2 for r in rows) ** 0.5) / len(rows)
    spread = abs(rows[0]["delta"] - rows[1]["delta"])
    same_sign = all(r["delta"] > 0 for r in rows) or all(r["delta"] < 0 for r in rows)
    excl0 = (lo > 0) or (hi < 0)
    pos = same_sign and lo > 0
    print(f"  **逆方差合并**（{len(rows)} 链）Δ={dc:+.4f}pp SE={sec:.4f} t={dc / sec:+.2f} "
          f"CI[{lo:+.4f},{hi:+.4f}]")
    print(f"  等权平均（对照）Δ={eqd:+.4f}pp SE={eqse:.4f} CI[{eqd - Z95 * eqse:+.4f},"
          f"{eqd + Z95 * eqse:+.4f}]")
    print(f"  **链间极差** |Δ₁−Δ₂| = {spread:.4f}pp（= 合并 SE 的 {spread / sec:.2f}×）")
    print(f"  **复现判据（§6.1 ④，先写死）**：两链同号={same_sign} 合并 CI 排除 0={excl0} "
          f"（正向且排除 0 = {pos}）⇒ **{'已复现' if pos else '未复现'}**")
    b = docs[0]["paired_delta"]["pon_rows_per_game"]
    b2 = docs[1]["paired_delta"]["pon_rows_per_game"]
    print(f"  辅判据 `pon_rows_per_game`：{docs[0].get('label')} Δ={b['delta_pp']:+.4f} "
          f"CI[{b['lo_pp']:+.4f},{b['hi_pp']:+.4f}] ｜ {docs[1].get('label')} "
          f"Δ={b2['delta_pp']:+.4f} CI[{b2['lo_pp']:+.4f},{b2['hi_pp']:+.4f}]")
    print()

    print("=== ④ 六个伴随量（**新方向判据**：坏方向按轴意图定）===")
    print(f"  {'量':<18}{'bad_when':<10}{'tol':<7}" + "".join(f"{d.get('label'):<26}" for d in docs)
          + "合并")
    stop = []
    for metric in COMPANIONS:
        blocks = [d["paired_delta"][metric] for d in docs]
        tol = wl._companion_tol(metric, TOL.get(metric))
        unit = wl._companion_unit(metric)
        cells, crows = [], []
        for d, bb in zip(docs, blocks):
            v = wl.companion_verdict(float(bb["delta_pp"]), float(bb["se_pp"]),
                                     int(bb["n_games"]), metric=metric, tol=tol)
            crows.append({"delta": v["delta"], "se": v["se"], "n": int(bb["n_games"]),
                          "label": d.get("label")})
            cells.append(f"Δ={v['delta']:+9.3f} [{v['lo']:+.3f},{v['hi']:+.3f}] "
                         f"{'OK' if v['ok'] else '**越界**':<8}")
            if not v["ok"]:
                stop.append(f"{metric}[{d.get('label')}]")
        dc2, se2 = combine(crows)
        v2 = wl.companion_verdict(dc2, se2, sum(r["n"] for r in crows), metric=metric, tol=tol)
        if not v2["ok"]:
            stop.append(f"{metric}[合并]")
        print(f"  {metric:<18}{v2['bad_when']:<10}{tol:<7g}" + "".join(f"{c:<26}" for c in cells)
              + f"Δ={dc2:+.3f} [{v2['lo']:+.3f},{v2['hi']:+.3f}] "
                f"{'OK' if v2['ok'] else '**越界**'}")
    print()

    print("=== ⑤ 止损量 `deals`（放铳次数/场/席）===")
    for d in docs:
        b = d["paired_delta"]["deal_rate"]                    # 单位 pct ⇒ 1pp = 0.01/场/席
        print(f"  {d.get('label'):<24} Δ={b['delta_pp'] / 100:+.4f}/场/席 "
              f"(={b['delta_pp']:+.4f}pp) SE={b['se_pp'] / 100:.4f} "
              f"CI[{b['lo_pp'] / 100:+.4f},{b['hi_pp'] / 100:+.4f}] ⇒ "
              f"{'**破**' if b['delta_pp'] / 100 >= 0.10 else '未破'}（阈 +0.10）")
    print()
    print(f"=== 止损判定（§6.3）：{'**触发 ⇒ 立即结案**' if stop else '未触发'}"
          f"{'（' + ', '.join(stop) + '）' if stop else ''} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
