"""跑一对（`arena.run_pair` 的薄壳）—— ⚠ 不能用 `python -c` 拼 Windows 路径：`\\U`、`\\` 会在
脚本串里被当成转义（实测 `SyntaxError: truncated \\UXXXXXXXX escape`）。参数化就没这个问题。

用法： python bl13_pair.py <out_dir> <policy_a> <policy_b> <games> <seed>
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "python" / ".venv" / "Lib" / "site-packages"))

from mahjong_ml.v4 import arena                            # noqa: E402


def main() -> int:
    out, a, b, games, seed = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
    arena.run_pair(Path(out), a, b, games, seed, workers=8, prod="cpp", rotate_perm=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
