#!/usr/bin/env python
"""看一条真实轨迹里的字段与类型（schema 核对 / 造 golden 用例时用）。

    python\\.venv\\Scripts\\python.exe python\\inspect_trace.py <轨迹目录> [--rows 2]

为什么需要它：`features.py` 是**唯一规格来源**，而它的字段假设必须与真实轨迹一致 ——
本工具就是"拿真数据核对一遍"的那一步。它抓到过一个真 bug：`round.bakaze` 是字母 `'E'`，
按数字写的特征代码会直接抛 `ValueError`（自造的假 obs 用的是数字，测试照样过）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def typ(v) -> str:
    if isinstance(v, list):
        return f"list[{len(v)}]" + (f" of {type(v[0]).__name__}" if v else "")
    if isinstance(v, dict):
        return "dict{" + ",".join(f"{k}:{typ(x)}" for k, x in list(v.items())[:8]) + "}"
    return type(v).__name__


def main() -> int:
    ap = argparse.ArgumentParser(description="打印真实轨迹的字段类型（核对 features.py 的假设）")
    ap.add_argument("dir")
    ap.add_argument("--rows", type=int, default=2, help="打印几类决策（按 kind 去重）")
    args = ap.parse_args()

    src = Path(args.dir)
    seen: set[str] = set()
    for f in sorted(src.glob("g*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "decision" or row["kind"] in seen:
                continue
            seen.add(row["kind"])
            print(f"--- {row['kind']}（{f.name}，seat={row['seat']}，policy={row.get('policy')}）---")
            for k, v in row["obs"].items():
                print(f"  {k:18} {typ(v):26} {str(v)[:64]}")
            print(f"  {'legal':18} {typ(row['legal']):26} {str(row['legal'])[:80]}")
            print(f"  {'chosen':18} {typ(row['chosen']):26} {row['chosen']}")
            if len(seen) >= args.rows:
                return 0
    print("没读到决策行")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
