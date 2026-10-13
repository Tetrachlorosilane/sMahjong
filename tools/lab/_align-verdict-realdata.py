# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""**实测数据**上的旧规则 vs 新规则（护栏假警的真实案例，不是合成）。"""
import json
import pathlib
import sys

Z = 1.96
for f in sys.argv[1:]:
    p = pathlib.Path(f)
    d = json.loads(p.read_text(encoding="utf-8"))
    lo, hi, dv, tol = d["lo"], d["hi"], d["delta"], d["tol"]
    old_bad = lo < -tol                      # 旧规则：CI 下界 < −tol 即越界
    new_bad = (hi < 0.0) or (dv < -tol)      # 新规则：CI 上界 < 0 或 点估计 < −tol
    flag = "" if old_bad == new_bad else "  ← **两规则不同**"
    print(f"{p.name:<34} metric={d['metric']:<16} Δ={dv:+7.3f}pp SE={d['se']:5.2f} "
          f"CI[{lo:+.3f},{hi:+.3f}] tol={tol:g} | 旧规则={'越界' if old_bad else '不越界'} rc={3 if old_bad else 0} | "
          f"新规则={'越界' if new_bad else '不越界'} rc={3 if new_bad else 0}{flag}")
