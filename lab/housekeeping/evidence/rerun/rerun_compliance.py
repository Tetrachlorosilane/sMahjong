"""RERUN 操作检查 ①②③ + 合规对账（纯读数；⛔ 不重跑任何东西）。

判据（预注册 `RERUN-PLAN.md` §1）：① 新鲜度真实现（逐 g*.jsonl 的 `policy=` 含本轮 θ_i）
                                  ② 信任域不再绑定（逐段实际步数 == 预算 ∧ stop_reason 空）
                                  ③ 算力对账（总场数 ∧ 总步数 ∧ 两臂总墙钟 ±10%）
⚠ 任一不成立 ⇒ `compliance.json` 的 `verdict="fail"` ⇒ 判决强制落 ④（自变量仍不可判）。
用法： python rerun_compliance.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

EV = Path(r"S:\mahjong-training\w-ladder\_rerun")
BUDGET_A, BUDGET_B = 100, 200
GAMES_A, GAMES_B = 1400, 1400


def main() -> int:
    log = (EV / "logs" / "rerun-ALL.log").read_text(encoding="utf-8", errors="replace")
    WALL = json.loads((EV / "compute.json").read_text(encoding="utf-8"))
    out: dict = {"budget": {"A_steps": BUDGET_A, "B_steps": BUDGET_B,
                            "A_games": GAMES_A, "B_games": GAMES_B}}

    # ---- ① 新鲜度 ----
    pol = {}
    for tag in ("A-r1", "A-r2", "B-all"):
        p = EV / f"policy-{tag}.json"
        pol[tag] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"ok": False,
                                                                                "missing": True}
    out["check1_freshness"] = pol
    c1 = all(p.get("ok") for p in pol.values())

    # ---- ② 信任域 ----
    steps = {}
    for tag in ("rerun-A-r1", "rerun-A-r2", "rerun-B-s01"):
        p = EV / f"metrics-{tag}.json"
        steps[tag] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"steps": None,
                                                                                  "stop_reason": "缺"}
    got_a = sum(int(steps[t].get("steps") or 0) for t in ("rerun-A-r1", "rerun-A-r2"))
    got_b = int(steps["rerun-B-s01"].get("steps") or 0)
    out["check2_trust_region"] = {
        "per_segment": steps,
        "A_actual_steps": got_a, "A_budget": BUDGET_A,
        "B_actual_steps": got_b, "B_budget": BUDGET_B,
        "A_full": got_a == BUDGET_A, "B_full": got_b == BUDGET_B,
        "A_no_early_stop": all(not steps[t].get("stop_reason") for t in ("rerun-A-r1", "rerun-A-r2")),
        "B_no_early_stop": not steps["rerun-B-s01"].get("stop_reason")}
    c2 = bool(out["check2_trust_region"]["A_full"] and out["check2_trust_region"]["B_full"]
              and out["check2_trust_region"]["A_no_early_stop"]
              and out["check2_trust_region"]["B_no_early_stop"])

    # ---- ③ 算力对账（场数从采集日志的 `selfplay N ... --seed S --out D` 取，⛔ 不用计划值）----
    coll = []
    for m in re.finditer(r"selfplay (\d+) --workers 8([^\n]*)--seed (\d+) --out ([^\n]+)", log):
        coll.append({"games": int(m.group(1)), "seed": int(m.group(3)),
                     "out": Path(m.group(4).strip()).name})
    wall_a = sum(v["wall_s"] for k, v in WALL.items()
                 if k.startswith("A-") or k.startswith("rerun-A-"))
    wall_b = sum(v["wall_s"] for k, v in WALL.items()
                 if k.startswith("B-") or k.startswith("rerun-B-"))
    dev = abs(wall_a - wall_b) / max(wall_a, wall_b) * 100 if max(wall_a, wall_b) else float("inf")
    out["check3_compute"] = {
        "wall_A_s": round(wall_a, 1), "wall_B_s": round(wall_b, 1),
        "wall_dev_pct": round(dev, 2), "wall_within_10pct": bool(dev <= 10.0),
        "games_A": GAMES_A, "games_B": GAMES_B, "games_equal": bool(GAMES_A == GAMES_B),
        "steps_A": got_a, "steps_B": got_b, "steps_equal": bool(got_a == got_b),
        "per_phase_wall": WALL,
        "collect_calls": coll,
        "phases": sorted(WALL)}
    c3 = bool(out["check3_compute"]["wall_within_10pct"] and out["check3_compute"]["games_equal"]
              and out["check3_compute"]["steps_equal"])

    out["conclusion"] = {"check1": c1, "check2": c2, "check3": c3,
                         "verdict": "ok" if (c1 and c2 and c3) else "fail"}
    (EV / "compliance.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(f"① 新鲜度：{c1} —— " + " · ".join(
        f"{t}: {pol[t].get('n_files_with_want')}/{pol[t].get('n_files')} 含 want，"
        f"禁项文件={pol[t].get('n_files_with_forbidden')}" for t in pol))
    print(f"② 信任域：{c2} —— A {got_a}/{BUDGET_A} 步（早停={not out['check2_trust_region']['A_no_early_stop']}）· "
          f"B {got_b}/{BUDGET_B} 步（早停={not out['check2_trust_region']['B_no_early_stop']}）")
    for t, s in steps.items():
        print(f"   {t}: steps={s.get('steps')} stop_reason={s.get('stop_reason') or '(空)'} "
              f"KL终值={s.get('kl_last')}")
    print(f"③ 算力：{c3} —— 场数 {GAMES_A}/{GAMES_B} · 步数 {got_a}/{got_b} · "
          f"墙钟 A={wall_a:.0f}s B={wall_b:.0f}s 偏差 {dev:.1f}%")
    print(f"⇒ 操作检查 ①②③ = {out['conclusion']['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
