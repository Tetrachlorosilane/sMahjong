#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""P3-01 读数（**不新造统计**）：全部借 `mahjong_ml.eval` / `mahjong_ml.v4.arena` / `mahjong_ml.v4.gate`
的既有实现（`pooled_diffs` / `decide` / `judge_series`），本文件只做「取数 + 除以 T_arm + 给区间」。

主判据（预注册 §3）= 共享判据 seed 逐场配对的 **Δ顺位点/场/席（B − A）**；进度 = Δ ÷ T_arm。
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
DATA = Path(os.environ.get("MAHJONG_DATA_ROOT", r"S:\mahjong-training"))
sys.path[:0] = [str(REPO / "python"), str(REPO / "python" / ".venv" / "Lib" / "site-packages")]

from mahjong_ml.v4 import arena as A          # noqa: E402
from mahjong_ml.v4 import gate as G           # noqa: E402
from mahjong_ml.v4 import eval as E           # noqa: E402

INC = str(REPO / "tools" / "build" / "v4-wl-p301-A-g01" / "net.bin")
CAND = str(REPO / "tools" / "build" / "v4-wl-p301-B-g06" / "net.bin")
SEEDS = [52930001, 52930002]
GROOT = DATA / "gate" / "v4-wl-p301"


def arms_T() -> dict:
    """两臂的 T_arm 与阶段拆分：来自 `loop` 自己写的台账 `league/<label>-v4.json`。"""
    out = {}
    for arm, lab in (("A", "v4-wl-p301-A"), ("B", "v4-wl-p301-B")):
        rows = []
        # 台账名可能是 <label> 或 <label>-gNN（`--label` 直接给的就是系列名）
        for p in (DATA / "league" / f"{lab}-v4.json",):
            if p.is_file():
                rows = json.loads(p.read_text(encoding="utf-8"))
        out[arm] = rows
    # B 的 6 轮台账分别写在同一个文件里（label 相同）—— 若分开写就逐个并
    rows_b = out["B"]
    if not rows_b:
        for i in range(1, 7):
            p = DATA / "league" / f"v4-wl-p301-B-g{i:02d}-v4.json"
            if p.is_file():
                rows_b += json.loads(p.read_text(encoding="utf-8"))
        out["B"] = rows_b
    return out


def phase_sum(rows: list[dict]) -> dict:
    agg: dict[str, float] = {}
    for r in rows:
        for k, v in (r.get("seconds") or {}).items():
            agg[k] = agg.get(k, 0.0) + float(v)
    agg["_total"] = round(sum(agg.values()), 1)
    return agg


def line(tag: str, d: dict) -> str:
    return (f"{tag}: Δ={d['delta']:+.4f}  SE={d['sd']/math.sqrt(d['n']):.4f}  "
            f"t={d['delta']/(d['sd']/math.sqrt(d['n'])):+.3f}  "
            f"CI95=[{d['lo']:+.4f},{d['hi']:+.4f}]  n={d['n']}  "
            f"win/lose/tie={d['win']}/{d['lose']}/{d['tie']}  MDE(1.96SE)={1.96*d['sd']/math.sqrt(d['n']):.3f}")


def main() -> int:
    print("== 0 配对自证（硬）：两侧 seed_base / 场数 / 四席策略表必须一致 ==")
    metas = {}
    for s in SEEDS:
        d = GROOT / f"s{s}"
        meta = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        pols = sorted(set(p for g in meta["per_game"] for p in g["policies"]))
        metas[s] = (meta["seed_base"], meta["games"], tuple(pols), meta["hands"], meta["workers"])
        print(f"  s{s}: seed_base={meta['seed_base']} games={meta['games']} workers={meta['workers']} "
              f"hands={meta['hands']} 策略表={[Path(p).parent.name for p in pols]}")
    sb0, g0, p0, _, _ = metas[SEEDS[0]]
    ok = all(metas[s][1] == g0 and metas[s][2] == p0 for s in SEEDS)
    print(f"  场数一致={all(metas[s][1]==g0 for s in SEEDS)} 策略表一致={all(metas[s][2]==p0 for s in SEEDS)} "
          f"⇒ 自证{'PASS' if ok else 'FAIL'}")
    hands_per_game = sum(metas[s][3] for s in SEEDS) / sum(metas[s][1] for s in SEEDS)

    print("\n== 1 主判据：Δ顺位点/场/席（B − A），按场配对，两套牌山合并 ==")
    ser = {m: [] for m in ("rank_points", "score", "deals", "wins", "win_points")}
    for s in SEEDS:
        d = GROOT / f"s{s}"
        for m in ser:
            ser[m].append(A.judge_series(d, INC, CAND, m))
    res = {}
    for m, ss in ser.items():
        sa, sb = G.pooled_diffs(ss)
        res[m] = G.decide(sa, sb, INC, CAND, m)
        print("  " + line(m, res[m]))

    rp = res["rank_points"]
    sc = res["score"]
    print(f"\n  次口径（净得失点/小局）：Δ={sc['delta']/hands_per_game:+.3f} 点/小局 "
          f"CI=[{sc['lo']/hands_per_game:+.3f},{sc['hi']/hands_per_game:+.3f}]"
          f"（场次={g0}/套 × {len(SEEDS)} 套；小局/场={hands_per_game:.3f}）")

    print("\n== 2 套间散布（判决条件性）==")
    for m in ("rank_points", "score"):
        print(f"  {m}: 逐牌山 Δ={[round(x,3) for x in res[m].get('wall_deltas', [])]} "
              f"极差={res[m]['wall_spread']:.3f} CI半宽={(res[m]['hi']-res[m]['lo'])/2:.3f} "
              f"比值={res[m]['wall_spread_over_ci']:.2f}×")

    print("\n== 3 T_arm（两臂同一把尺：采集+特征+校验+紧凑集+训练+导出+审计+评测+启停落盘）==")
    led = arms_T()
    T: dict[str, float] = {}
    for arm in ("A", "B"):
        rows = led[arm]
        ph = phase_sum(rows)
        T[arm] = ph["_total"]
        print(f"  {arm}: 轮数={len(rows)} 场次/轮={[r.get('games') for r in rows]} "
              f"台账 Σseconds={ph['_total']:.1f}s ({ph['_total']/3600:.3f} h)")
        print(f"     拆分 " + " · ".join(f"{k}={v:.1f}s" for k, v in sorted(ph.items()) if k != "_total"))

    print("\n== 4 进度 = Δ ÷ T_arm（顺位点/小时）+ 比值与其不确定度 ==")
    d, lo, hi = rp["delta"], rp["lo"], rp["hi"]
    for arm in ("A", "B"):
        h = T[arm] / 3600.0
        print(f"  进度_{arm} = Δ/T_{arm} = {d/h:+.4f} 顺位点/小时   "
              f"CI95=[{lo/h:+.4f},{hi/h:+.4f}]（把 Δ 的 CI 按 1/T 缩放）")
    print(f"  比值 进度_B/进度_A = T_A/T_B = {T['A']/T['B']:.4f}"
          f"  （⚠ **Δ 在比值里精确约掉** ⇒ 这是个点值，与 Δ 的抽样误差无关）")
    # 比值的不确定度：唯一有抽样不确定性的量是 T（跑一次的量），用**轮/相级计时自助法**给区间。
    rng = random.Random(20261012)
    rowsA, rowsB = led["A"], led["B"]
    boots = []
    for _ in range(4000):
        a = sum(sum((r.get("seconds") or {}).values()) for r in (rng.choices(rowsA, k=len(rowsA)) if len(rowsA) > 1 else rowsA))
        b = sum(sum((r.get("seconds") or {}).values()) for r in (rng.choices(rowsB, k=len(rowsB)) if len(rowsB) > 1 else rowsB))
        boots.append(a / b)
    boots.sort()
    print(f"     比值的不确定度（**只来自 T**：轮/相级计时自助法 n=4000）= "
          f"[{boots[int(0.025*len(boots))]:.4f}, {boots[int(0.975*len(boots))]:.4f}]"
          f"（A 只有 1 轮 ⇒ 其自助退化为点值 ⇒ 区间主要反映 B 的 6 轮散布）")
    # 非退化口径：B 比 A **多花**的时间里买到的强度
    if abs(T["B"] - T["A"]) > 1e-9:
        dT = (T["B"] - T["A"]) / 3600.0
        print(f"  增量口径（非退化）：B 多花 {dT:+.4f} h 换来 Δ={d:+.4f} 顺位点 "
              f"⇒ {d/dT:+.3f} 顺位点/小时  CI95=[{lo/dT:+.3f},{hi/dT:+.3f}]")

    print("\n== 5 止损分支（预注册 §6，判在 Δ顺位点 的 95% CI 上）==")
    if rp["lo"] > 0:
        print(f"  CI 下界 {rp['lo']:+.4f} > 0 ⇒ **B 优**（多轮短采）")
    elif rp["hi"] < 0:
        print(f"  CI 上界 {rp['hi']:+.4f} < 0 ⇒ **A 优**（少轮长训）")
    else:
        print(f"  CI 含 0（[{rp['lo']:+.4f},{rp['hi']:+.4f}]）⇒ **该分辨率下 A ≈ B**，"
              f"按预注册取 T_arm 低者 = {'A' if T['A'] < T['B'] else 'B'}")
    print(f"  实际 MDE = 1.96×SE = {1.96*rp['sd']/math.sqrt(rp['n']):.3f} 顺位点"
          f"（预注册目标 ≤1.0；SE 实测 {rp['sd']/math.sqrt(rp['n']):.4f} 对预注册 1.199√(2000/12000)={1.199*math.sqrt(2000/12000):.4f}）")
    print(f"  ⚠ 条件性：结论只对 seed 族 {SEEDS} 成立（套间极差 {rp['wall_spread']:.2f} 顺位点）")

    (DATA / "w-ladder" / "p301-readout.json").write_text(
        json.dumps({"paired": {k: {kk: vv for kk, vv in v.items() if kk != "guards"} for k, v in res.items()},
                    "T_arm_s": T, "hands_per_game": hands_per_game, "seeds": SEEDS},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n读数落盘：{DATA / 'w-ladder' / 'p301-readout.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
