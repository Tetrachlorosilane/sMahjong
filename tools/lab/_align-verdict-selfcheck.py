# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""质量护栏**假警修法**的三条自证（原始输出）：旧规则 vs 新规则。

旧规则 = `ok ⇔ CI 下界 ≥ −tol`；新规则 = `ok ⇔ (CI 上界 ≥ 0) ∧ (点估计 ≥ −tol)`。
⛔ 本脚本不 import 奖励侧任何东西；`w-ladder.py` 以文件方式加载（它只有 `main()` 在 `__main__` 下）。
"""
import importlib.util
import pathlib
import sys

REPO = pathlib.Path(r"C:\Users\HP\source\games\mahjong")
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "python" / ".venv" / "Lib" / "site-packages"))
spec = importlib.util.spec_from_file_location("w_ladder", REPO / "tools" / "w-ladder.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def old_rule(delta, se, tol):
    return (delta - m.Z95 * se) >= -tol


CASES = (
    ("① 假警格（本任务实测的那个形状）", 0.30, 1.32, 2.0, "**不再报**"),
    ("② 显著为负（幅度也越过 tol）", -3.00, 0.50, 2.0, "**仍报**"),
    ("③ 题面数值 Δ=−1/SE=3", -1.00, 3.00, 2.0, "（题面说'CI 上界<0'，实测上界=+4.88 ⇒ 见下）"),
    ("③′ 题面**描述**的那一格：点估计没到 −2 但 CI 上界 < 0", -1.00, 0.30, 2.0, "**仍报**"),
    ("④ 边界：点估计恰 = −tol（且 CI 全负）", -2.00, 0.10, 2.0, "（**仍报**：由 ① 触发；`<` 不含等号）"),
)
print("== 质量护栏：旧规则 vs 新规则（tol 单侧 = 2pp）==")
print(f"{'case':<52} {'Δ':>7} {'SE':>6} {'CI 95%':>20} {'旧规则':>7} {'新规则':>7} {'rc':>3}")
for name, d, s, tol, note in CASES:
    r = m.companion_verdict(d, s, 1000, metric="decline_rate", tol=tol)
    o = old_rule(d, s, tol)
    print(f"{name:<52} {d:>+7.2f} {s:>6.2f} [{r['lo']:>+7.3f},{r['hi']:>+7.3f}] "
          f"{'不越界' if o else '越界':>7} {'不越界' if r['ok'] else '越界':>7} "
          f"{0 if r['ok'] else 3:>3}   {note}")
print()
print("字段（键顺序一字不动，保证同一份输入逐字节可比）：",
      tuple(m.COMPANION_KEYS), "| 实际 row 键：", tuple(m.companion_verdict(0.0, 1.0, 1)))
