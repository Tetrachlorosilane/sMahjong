#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
r"""P3-PKG 闸门读数（**不新造统计**）：全部借 `mahjong_ml.v4.arena` / `mahjong_ml.v4.gate` 的既有实现
（`judge_series` / `pooled_diffs` / `decide`），本文件只做「取数 + 打表 + 按冻写稿判据判」。

用法：
    python _p3pkg-readout.py <tag> <incumbent-net.bin> <candidate-net.bin> [seeds...]

冻写稿 = `S:\mahjong-training\w-ladder\EVAL-PUBLISH-PLAN.md`（跑前定稿）：
  主口径 rank_points（判决用）· 并列口径 score · 护栏 wins:0.10 / deals:0.10 / score:150
  过线 = 闸门 adopt=True（95% CI 排除 0 且为正）∧ 护栏全不破 ∧ **Bonferroni 校正 CI（±2.2414SE）也排除 0 且为正**
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
DATA = Path(os.environ.get("MAHJONG_DATA_ROOT", r"S:\mahjong-training"))
sys.path[:0] = [str(REPO / "python"), str(REPO / "python" / ".venv" / "Lib" / "site-packages")]

from mahjong_ml.v4 import arena as A          # noqa: E402
from mahjong_ml.v4 import gate as G           # noqa: E402

Z95 = 1.959963985
ZBONF = 2.241402728          # 双侧 α=0.025（2 候选 Bonferroni）
METRICS = ("rank_points", "score", "wins", "deals", "win_points")
TOL = {"wins": 0.10, "deals": 0.10, "score": 150.0}


def main(argv: list[str]) -> int:
    tag, inc, cand = argv[0], argv[1], argv[2]
    # ⚠ 轨迹里的策略标签是 `net:<路径>` 策略串（`gate._policy` 干的就是这件事）—— 直接用
    #   `net.bin` 路径去匹配 `per_game[].policies` **恒不命中**，症状是 n=0（实测踩过一次）。
    inc, cand = G._policy(inc), G._policy(cand)
    seeds = [int(s) for s in argv[3:]] or [53060001, 53060002]
    groot = DATA / "gate" / tag

    print(f"== 0 配对自证（硬）：两侧 seed_base / 场数 / 四席策略表 / 小局 必须一致 ==")
    metas = {}
    for s in seeds:
        meta = json.loads((groot / f"s{s}" / "summary.json").read_text(encoding="utf-8"))
        pols = sorted(set(p for g in meta["per_game"] for p in g["policies"]))
        metas[s] = (meta["seed_base"], meta["games"], tuple(pols), meta["hands"], meta["workers"])
        print(f"  s{s}: seed_base={meta['seed_base']} games={meta['games']} workers={meta['workers']} "
              f"hands={meta['hands']} 策略表={[Path(p).parent.name for p in pols]}")
    g0, p0 = metas[seeds[0]][1], metas[seeds[0]][2]
    ok = all(metas[s][1] == g0 and metas[s][2] == p0 for s in seeds)
    print(f"  场数一致={all(metas[s][1]==g0 for s in seeds)} 策略表一致={all(metas[s][2]==p0 for s in seeds)} "
          f"⇒ 自证{'PASS' if ok else 'FAIL'}")
    hands_per_game = sum(metas[s][3] for s in seeds) / sum(metas[s][1] for s in seeds)

    print(f"\n== 1 逐口径（Δ = 候选 − 现任，逐场按 seed 配对，{len(seeds)} 套牌山合并）==")
    res, walls = {}, {}
    for m in METRICS:
        ss = [A.judge_series(groot / f"s{s}", inc, cand, m) for s in seeds]
        sa, sb = G.pooled_diffs(ss)
        r = G.decide(sa, sb, inc, cand, m)
        res[m] = r
        walls[m] = [A.judge_pair(y, x, cand, inc, m).delta for x, y in ss]   # 逐牌山 Δ（候选 − 现任）
        se = r["sd"] / math.sqrt(r["n"])
        t = r["delta"] / se
        mde = Z95 * se
        print(f"  {m:<12} Δ={r['delta']:+9.4f}  SE={se:7.4f}  t={t:+7.3f}  "
              f"95%CI(自助法)=[{r['lo']:+8.4f},{r['hi']:+8.4f}]  "
              f"正态CI=[{r['delta']-Z95*se:+9.4f},{r['delta']+Z95*se:+9.4f}]  "
              f"BonfCI=[{r['delta']-ZBONF*se:+9.4f},{r['delta']+ZBONF*se:+9.4f}]")
        print(f"  {'':<12} n={r['n']}  胜/负/平={r['win']}/{r['lose']}/{r['tie']}  实际MDE(1.96SE)={mde:.4f}  "
              f"符号检验p={r['p']:.4f}  逐牌山Δ={[round(x,4) for x in walls[m]]}")
    sc = res["score"]
    print(f"\n  次口径换算（净得失点/小局，小局/场={hands_per_game:.3f}）："
          f"Δ={sc['delta']/hands_per_game:+.3f} 点/小局  "
          f"95%CI=[{sc['lo']/hands_per_game:+.3f},{sc['hi']/hands_per_game:+.3f}]")

    print("\n== 2 套间散布（判决条件性）==")
    for m in ("rank_points", "score"):
        half = (res[m]["hi"] - res[m]["lo"]) / 2
        spread = max(walls[m]) - min(walls[m])
        print(f"  {m:<12} 极差={spread:8.4f} / CI半宽={half:7.4f} = {spread/half if half else float('inf'):.2f}×"
              + ("  ⚠ >1×：判决**条件于这两套牌山**、不可外推" if half and spread / half > 1 else ""))

    print("\n== 3 护栏（单侧：CI 下界须 ≥ −TOL）==")
    guard_ok = True
    for m, tol in TOL.items():
        r = res[m]
        okm = r["lo"] >= -tol
        guard_ok &= okm
        print(f"  {m:<12} Δ={r['delta']:+9.4f} 95%CI=[{r['lo']:+9.4f},{r['hi']:+9.4f}] "
              f"TOL=−{tol:g} ⇒ {'不破 ✓' if okm else '破了 ✗'}")

    rp = res["rank_points"]
    se = rp["sd"] / math.sqrt(rp["n"])
    c1 = rp["lo"] > 0.0
    c3 = (rp["delta"] - ZBONF * se) > 0.0
    adopt = c1 and guard_ok and c3
    print("\n== 4 判决（照冻写稿 §4：①∧②∧③）==")
    print(f"  ① 闸门主口径 rank_points 95%CI 排除 0 且为正：{'✓' if c1 else '✗'} ({rp['lo']:+.4f})")
    print(f"  ② 护栏全不破：{'✓' if guard_ok else '✗'}")
    print(f"  ③ Bonferroni（±{ZBONF:.4f}SE）校正 CI 也排除 0 且为正：{'✓' if c3 else '✗'} "
          f"({rp['delta']-ZBONF*se:+.4f})")
    print(f"  ⇒ **{'过线' if adopt else '不过线（该分辨率下不显著 / 或护栏破）'}**")
    if not adopt:
        print(f"  ⛔ 不改判据、不补场次、不换口径；结论只对 seed 族 {seeds} 成立")

    out = DATA / "w-ladder" / f"p3pkg-readout-{tag}.json"
    out.write_text(json.dumps({"tag": tag, "seeds": seeds, "incumbent": inc, "candidate": cand,
                               "paired": {m: {k: v for k, v in res[m].items() if k != "guards"}
                                          for m in METRICS},
                               "wall_deltas": walls, "hands_per_game": hands_per_game,
                               "guard_ok": guard_ok, "adopt_by_plan": adopt,
                               "z95": Z95, "z_bonferroni": ZBONF},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n读数落盘：{out}")
    return 0 if adopt else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
