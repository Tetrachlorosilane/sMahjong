# -*- coding: utf-8 -*-
# [数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""判据侧（`tools/w-ladder.py`）的**口径自检**：拿一份**小**真轨迹（30 场）把
`_student_per_game` 的合计与 `tools/style-vector.py` 的 `counts` **逐项对账**
（含本轮新加的 `riichi_turn_sum/known/missing` 与行级 `RIICHI_TURN_KEYS` 的四数）。

为什么要有它：`_student_per_game` 只在 `post`（1000 场）那一步才被对账，跑一次要 4 分钟；
这里用 30 场先把它钉住（**同一个函数**，不另写一套）。⛔ 不 import `style_reward` 的判据侧口径
（本脚本只调 w-ladder 的函数 + 读 style-vector 的 JSON）。

用法：python _rt-critcheck.py <轨迹目录> [--sv <style-vector.json>]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
PY = r"S:\mahjong-training\tools\py312\python.exe"
sys.dont_write_bytecode = True                     # ⛔ 别在 tools/ 留 .pyc（污染 git status）
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "python" / ".venv" / "Lib" / "site-packages"))
import importlib.util as _ilu                      # noqa: E402

_spec = _ilu.spec_from_file_location("_wl_crit", REPO / "tools" / "w-ladder.py")
wl = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(wl)

ap = argparse.ArgumentParser()
ap.add_argument("dir")
ap.add_argument("--sv", default="")
a = ap.parse_args()
d = Path(a.dir)
sv_path = Path(a.sv) if a.sv else Path(r"S:\mahjong-training\w-ladder\_rt-critcheck-sv.json")
if not sv_path.is_file():
    p = subprocess.run([PY, str(REPO / "tools" / "style-vector.py"), str(d), "--json", str(sv_path)],
                       cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"⛔ style-vector 失败（rc={p.returncode}）：\n{p.stdout[-1200:]}\n{p.stderr[-800:]}")
sv = json.loads(sv_path.read_text(encoding="utf-8"))
per = wl._student_per_game(d)
tot = wl._verify_pair_totals(per, sv, d.name)          # ⛔ 对不上就在这里 SystemExit
print(f"== 判据侧口径自检（{len(per)} 场，{d}）==")
for k in wl.COUNT_KEYS:
    print(f"  {k:<22} w-ladder {tot[k]:>8}  == style-vector "
          f"{int(wl.student_counts(sv).get(k, -1)):>8} ✓")
rt = wl._riichi_turn_totals(per)
wl._riichi_turn_selfcheck(rt, d.name)
print("  ── 行级「早立直」四数（对账对象是奖励侧审计账；这里先自洽）")
for k in wl.RIICHI_TURN_KEYS:
    print(f"  {k:<26} {rt[k]:>8}")
print(f"  T = {wl.RIICHI_TURN_T}（判据侧副本）")
print(f"  聚合 avg_riichi_turn = {tot['riichi_turn_sum'] / tot['riichi_turn_known']:.6f}"
      f"（与 style-vector dims = {wl.student_counts(sv) and next(v['dims'].get('avg_riichi_turn') for k2, v in sv['policies'].items() if '@' in k2)!r}）")
pd = wl._paired_delta(per, per)
print(f"  自配对（pre == post ⇒ Δ 必须 = 0）：riichi_turn_mean Δ = "
      f"{pd['riichi_turn_mean']['delta_pp']:+.6f}，n={pd['riichi_turn_mean']['n_games']}，"
      f"unit_cn={pd['riichi_turn_mean']['unit_cn']!r}")
print(f"  结论：{'PASS' if abs(pd['riichi_turn_mean']['delta_pp']) < 1e-12 else 'FAIL（自配对 Δ != 0）'}")
raise SystemExit(0 if abs(pd["riichi_turn_mean"]["delta_pp"]) < 1e-12 else 1)
