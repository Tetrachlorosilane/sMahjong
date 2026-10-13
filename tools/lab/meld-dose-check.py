#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""meld-dose-check.py —— 从**日志原文**核对"`w=6060` 是不是真的落在**方差同权**点上"。

判据（可核对，不靠口味）：逐行那一行的两个数分别是
  · `std Y 点` = 塑形列（`meld=6060:no_deal`）在**学生行**上的 std；
  · `std Z 点` = **被减的那个奖励**（`delta + 塑形 + 顺位点`）在同一批行上的 std。
两者关系是 `Z² = δ² + Y²`（δ = 纯结果奖励 `delta` 的 std）⇒
  · 塑形与结果奖励**方差同权**（`Y == δ`）⇔ `Y/Z == 1/√2 ≈ 0.7071`（⚠ **不是 1**：Z 里含塑形本身，
    所以"逐行比值 = 1"是**达不到**的目标 —— 这正是打印的 `w_for_parity` 在 `decision` 模式下
    不能照字面取用的第二条理由）。
本脚本只读日志（⛔ 不算任何风格量），把 `Y/δ` 与"若要同权应取的 w"排出来。

用法：python meld-dose-check.py --logs <release/*.log> ...
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

#: 逐行那一行的两个 std（`mean … std Y 点 vs 同一批行上被减奖励 std Z 点 ⇒ R×`）。
ROW_RE = re.compile(r"逐行（(?P<pop>[\w\u4e00-\u9fff]+) 行 (?P<n>\d+) 行）mean (?P<mean>[+-][\d.]+) "
                    r"std (?P<y>[\d.]+) 点 vs 同一批行上被减奖励 std (?P<z>[\d.]+) 点 ⇒ \*\*(?P<r>[\d.]+)×")
#: 付奖轴那一行（拿它的 `w=`）—— 用于反推"同权该取多少"。
AXIS_RE = re.compile(r"轴 (?P<axis>\w+): w=(?P<w>[\d.]+) 质量轴")


def main() -> int:
    ap = argparse.ArgumentParser(prog="meld-dose-check.py")
    ap.add_argument("--logs", nargs="+", required=True)
    a = ap.parse_args()
    print("| 日志 | 轴 w | 学生行 | 塑形 std Y | 被减奖励 std Z | δ=√(Z²−Y²) | **Y/δ（1.00 = 方差同权）** "
          "| 同权应取的 w | 打印的比值 R | 1/√2 |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for p in a.logs:
        t = Path(p).read_text(encoding="utf-8", errors="replace")
        row = ROW_RE.search(t)
        ax = AXIS_RE.search(t)
        w = float(ax.group("w")) if ax else float("nan")
        if not row:
            print(f"| {Path(p).name} | — | — | — | — | — | — | — | — | — |")
            continue
        y, z = float(row.group("y")), float(row.group("z"))
        d = math.sqrt(max(z * z - y * y, 0.0))
        ratio = y / d if d > 0 else float("nan")
        print(f"| {Path(p).name} | {w:g} | {row.group('n')} | {y:.1f} | {z:.1f} | {d:.1f} "
              f"| **{ratio:.3f}** | {w / ratio:.0f} | {float(row.group('r')):.3f} | 0.7071 |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
