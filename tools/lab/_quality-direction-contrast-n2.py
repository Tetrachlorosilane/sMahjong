# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""旧规则（HEAD · 方向无关）vs 新规则（方向感知）在**合并后的放铳率**上的对照。

⛔ 目的只有一个：证明本次修的假警在**本轮的合并读数**上也真的会发生（不只是 c1 单链）。
`companion-quality-meldtype-n2-deal_rate.json` 是 `quality` 写的新产物；这里拿它的 `delta/se`
分别喂 HEAD 的 `companion_verdict` 与新版的（判据只有一处实现，⛔ 不手搓公式）。
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                     # noqa: BLE001
    pass

REPO = Path(r"C:\Users\HP\source\games\mahjong")
T = Path(tempfile.mkdtemp())
head = subprocess.run(["git", "-C", str(REPO), "show", "HEAD:tools/w-ladder.py"],
                      capture_output=True).stdout
hp = T / "h.py"
hp.write_bytes(head)


def load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    sys.modules[name] = m
    s.loader.exec_module(m)
    return m


H = load("h_head", hp)
N = load("n_new", REPO / "tools" / "w-ladder.py")
Z = H.Z95
doc = json.loads(Path(r"S:\mahjong-training\w-ladder"
                      r"\companion-quality-meldtype-n2-deal_rate.json").read_text(encoding="utf-8"))
dc, sec, n = doc["delta"], doc["se"], doc["n_games"]
print(f"合并 `deal_rate`（两条链逆方差）：Δ={dc:+.4f}pp  SE={sec:.4f}pp  "
      f"CI[{dc - Z * sec:+.4f},{dc + Z * sec:+.4f}]  n={n}")
oh = H.companion_verdict(dc, sec, n, metric="deal_rate", tol=2.0, unit="pp")
on = N.companion_verdict(dc, sec, n, metric="deal_rate", tol=None)
print(f"  旧规则（HEAD · **方向无关** `hi<0 or Δ<−tol`）：ok={oh['ok']} ⇒ "
      f"{'**越界**（hi<0 触发 —— 假警）' if not oh['ok'] else '不越界'}")
print(f"  新规则（`bad_when={on['bad_when']}` · 上升为坏）：ok={on['ok']} ⇒ "
      f"{'越界' if not on['ok'] else '**不越界**（放铳**变少**是白赚，不是代价）'}")
raise SystemExit(0 if (not on["ok"]) is False else 1)
