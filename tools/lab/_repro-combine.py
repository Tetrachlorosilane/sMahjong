# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""`dama` 轴 **n=2 复现**：把两份 `paired/*-POST.json`（两条独立重跑）的读数合并。

⛔ 判据口径在 `PREREGISTRATION-DAMA.md` §6 **看结果之前**已写死：

  ① 主判据 = 行级 `decline_rows_per_game`（每场每席"能立而不立"行数）；
  ② 两链**逆方差加权**合并（链间 seed 族不同 ⇒ **不能逐场配对**，只能合并统计量）
     + **等权平均**当对照读数 + **链间极差** `|Δ₁−Δ₂|`；
     判"**已复现**" ⇔ 两链 **Δ 同号** **且** 合并 CI **排除 0**；
  ③ 伴随量的**单侧**越界判据（新规则）= 「CI 上界 < 0」**或**「点估计 < −tol」，`tol = 2.0pp`。
     ⛔ 本脚本自己算这条（不读存档里的 `ok`），并对 `w-ladder.py` 写的 `ok` 做对账。

用法：python _repro-combine.py <chainA.json> <chainB.json> [--tol 2.0]
"""
import argparse
import json
import pathlib
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                     # noqa: BLE001
    pass

Z95 = 1.96
#: 要贴出来的伴随量（`PREREGISTRATION-DAMA.md` §6 ③ 那六个 + 两个读数）
COMPANIONS = ("riichi_rate", "meld_rate", "win_rate", "deal_rate", "avg_win_score",
              "riichi_win_rate", "dama_rate", "decline_rate")


def verdict(delta, se, tol):
    """新规则的越界判据（本脚本**本地**实现，⛔ 不 import `w-ladder.py`）。"""
    lo, hi = delta - Z95 * se, delta + Z95 * se
    return {"delta": delta, "se": se, "lo": lo, "hi": hi, "tol": tol,
            "ok": not (hi < 0.0 or delta < -tol)}


def combine(rows):
    """逆方差加权固定效应合并（与 `w-ladder.companion_combine` 同一条公式）。"""
    w = [1.0 / (r["se"] ** 2) for r in rows]
    sw = sum(w)
    d = sum(wi * r["delta"] for wi, r in zip(w, rows)) / sw
    return d, (1.0 / sw) ** 0.5


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_repro-combine.py")
    ap.add_argument("paired", nargs="+", help="两份（或更多）`paired/*-POST.json`")
    ap.add_argument("--tol", type=float, default=2.0)
    a = ap.parse_args(argv)
    docs = [json.loads(pathlib.Path(p).read_text(encoding="utf-8")) for p in a.paired]

    print("=== 配对自证（每条链：seed / 学生席 不一致数）===")
    for d, p in zip(docs, a.paired):
        pc = d.get("pairing_check") or {}
        print(f"  {d.get('label'):<28} {pathlib.Path(p).name:<24} "
              f"games={pc.get('games')} seed_mismatch={pc.get('seed_mismatch')} "
              f"student_seat_mismatch={pc.get('student_seat_mismatch')} ok={pc.get('ok')} "
              f"seed_base={pc.get('seed_base_pre')}/{pc.get('seed_base_post')}")
    print()

    print("=== 行级对账（判据侧 == 奖励侧审计账；pre 那一侧有账）===")
    for d in docs:
        pre = d["decline"]["pre"]
        t = pre["totals"]
        print(f"  {d.get('label'):<28} reconciled={pre.get('reconciled')} "
              f"decision={t['decision_rows']} legal={t['legal_rows']} decline={t['decline_rows']} "
              f"paid={t['decline_paid_rows']} blocked={t['decline_blocked_rows']} "
              f"missing={t['decline_missing_legal_rows']} viol={t['decline_viol_state']}/"
              f"{t['decline_viol_after_decl']} "
              f"mu={pre['mu']!r}")
    print()

    for metric in ("decline_rows_per_game", "decline_rate") + COMPANIONS:
        blocks = [((d.get("paired_delta") or {}).get(metric)) for d in docs]
        if any(b is None for b in blocks):
            print(f"--- {metric}：**取不到**（有的账里没有这一项）")
            continue
        unit = "pp" if metric.endswith("rate") else "行(或点)/场/席"
        print(f"--- {metric}（单位 {unit}）")
        rows = []
        for d, b in zip(docs, blocks):
            rows.append({"delta": float(b["delta_pp"]), "se": float(b["se_pp"])})
            print(f"    {d.get('label'):<28} pre={b['pre']:.6g} post={b['post']:.6g} "
                  f"Δ={b['delta_pp']:+.4f} SE={b['se_pp']:.4f} t={b['t']:+.2f} "
                  f"CI[{b['lo_pp']:+.4f},{b['hi_pp']:+.4f}] n={b['n_games']}")
        dc, sec = combine(rows)
        lo, hi = dc - Z95 * sec, dc + Z95 * sec
        print(f"    **逆方差合并**（{len(rows)} 链）Δ={dc:+.4f} SE={sec:.4f} t={dc / sec:+.2f} "
              f"CI[{lo:+.4f},{hi:+.4f}]")
        eqd = sum(r["delta"] for r in rows) / len(rows)
        eqse = (sum(r["se"] ** 2 for r in rows) ** 0.5) / len(rows)
        print(f"    等权平均（对照）Δ={eqd:+.4f} SE={eqse:.4f} "
              f"CI[{eqd - Z95 * eqse:+.4f},{eqd + Z95 * eqse:+.4f}]")
        if len(rows) >= 2:
            spread = abs(rows[0]["delta"] - rows[1]["delta"])
            print(f"    **链间极差** |Δ₁−Δ₂| = {spread:.4f}"
                  f"（= 合并 SE 的 {spread / sec:.2f}×）")
        if len(rows) >= 2:
            same_sign = all(r["delta"] > 0 for r in rows) or all(r["delta"] < 0 for r in rows)
            excl0 = (lo > 0) or (hi < 0)
            pos = same_sign and lo > 0
            print(f"    **复现判据**：两链同号={same_sign} 合并 CI 排除 0={excl0} "
                  f"（正向且排除 0 = {pos}）⇒ **{'已复现' if pos else '未复现'}**")
        print()

    print("=== 伴随量越界判据（新规则：CI 上界<0 或 点估计<−tol；本地重算并对照存档 `ok`）===")
    for metric in COMPANIONS:
        blocks = [((d.get("paired_delta") or {}).get(metric)) for d in docs]
        if any(b is None for b in blocks):
            continue
        cells = []
        for d, b in zip(docs, blocks):
            v = verdict(float(b["delta_pp"]), float(b["se_pp"]), a.tol)
            cells.append(f"{d.get('label')}: Δ={v['delta']:+.3f} SE={v['se']:.3f} "
                         f"CI[{v['lo']:+.3f},{v['hi']:+.3f}] "
                         f"{'越界' if not v['ok'] else '不越界'}")
        print(f"  {metric:<16} " + " | ".join(cells))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
