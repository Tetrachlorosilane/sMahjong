"""用**当前**的合并判据，在**已存闸门摘要**上重算历季判决（不跑新对局）。

为什么要有它：闸门每代把 `gate/<tag>/s<seed>/` 下的 1000 个逐场轨迹删掉、**只留 `summary.json`**
（判据只读它：`per_game[].rank_points/placement/final_scores/policies/seed`；实测复算整季 5 代
只花 7 秒、26 GB 的 jsonl 一个字节都没读）。于是"某季当时的判决到底对不对"可以在**任何时候**
用当天的判据重算一遍 —— 这正是第八季 g01 暴露出 `gate.py` 逐牌山打印符号相反时该做的事：
先拿已存数据复算，确认**判决链**没被污染（第七季 5 代逐位复现 −1.11/−1.38/−2.26/+0.79/−1.53），
再去改那行 printf。

用法：
    python regate_gate.py --label v4-league7 --incumbent v4-league-g08 --generations 5 \
        --seeds 20261001,20261002 [--build-root tools/build] [--gate-root <数据根>/gate]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mahjong_ml.v4 import arena as A, gate as G          # noqa: E402


def regrade(build_root: Path, gate_root: Path, label: str, incumbent: str, generations: int,
            seeds: list[int] | None) -> int:
    """`seeds=None` ⇒ **自动发现**该代实际用过的牌山目录（`s*`）。

    ⚠ 必须自动发现：**牌山套数在历史上变过**（第三季 3 套、第五季起 2 套）。硬编码 2 套会让
    复算结果与当时的判决**不一样**，而且看起来完全正常（实测：第三季 g01 真值 −0.27（3 套），
    只读 2 套得 −0.56）—— "复算对不上"会让人怀疑数据，其实是工具读少了。
    """
    inc = f"net:{build_root / incumbent / 'net.bin'}"
    bad = 0
    for g in range(1, generations + 1):
        tag = f"{label}-g{g:02d}"
        # ⚠ **不能**要求 `build/<tag>/net.bin` 还在：被拒的那几代原件可能只剩
        #   `_rejected/<tag>/net.bin` 的副本。而 `judge_series` 是按 **summary 里的策略串**
        #   匹配的（不是按文件）⇒ 只要标签串对得上就行，权重文件在不在都不影响复算。
        cand = f"net:{build_root / tag / 'net.bin'}"
        root = gate_root / tag
        walls = ([root / f"s{s}" for s in seeds] if seeds is not None
                 else sorted(p for p in root.glob("s*") if p.is_dir()))
        walls = [d for d in walls if (d / "summary.json").is_file()]
        if not walls:
            print(f"{tag}: 没有闸门摘要 —— 跳过")
            continue
        print(f"{tag}（{len(walls)} 套牌山：{[d.name for d in walls]}；权重在不在原位不影响复算）")
        series = []
        for d in walls:
            x, y = A.judge_series(d, inc, cand)
            # ⚠ 逐牌山与合并必须同一个符号：`judge_pair(sa, sb, a, b)` 的约定是 `Δ = a − b`
            part = A.judge_pair(y, x, cand, inc)              # 候选 − 现任
            print(f"    {d.name}: Δ={part.delta:+6.2f} CI[{part.lo:+6.2f},{part.hi:+6.2f}] "
                  f"n={part.games}")
            series.append((x, y))
        sa, sb = G.pooled_diffs(series)
        out = G.decide(sa, sb, inc, cand)
        vj = root / "verdict.json"
        was = ""
        if vj.is_file():
            import json
            old = json.loads(vj.read_text(encoding="utf-8"))
            was = (f"  [当时台账 Δ={old['delta']:+.2f} "
                   f"CI[{old['lo']:+.2f},{old['hi']:+.2f}] n={old['n']}"
                   f"{'  ✓ 一致' if abs(old['delta'] - out['delta']) < 1e-6 else '  ✗ 不一致！'}]")
        print(f"  == 复算合并：Δ={out['delta']:+6.2f} "
              f"CI[{out['lo']:+.2f},{out['hi']:+.2f}] n={out['n']} ⇒ "
              f"{'采纳' if out['adopt'] else '不采纳'}{was}")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="在已存的闸门摘要上重算历季判决（不跑对局）")
    ap.add_argument("--label", required=True, help="季标签（如 v4-league7）")
    ap.add_argument("--incumbent", required=True, help="现任目录名（如 v4-league-g08）")
    ap.add_argument("--generations", type=int, default=5)
    ap.add_argument("--seeds", default="",
                    help="逗号分隔的牌山 seed；**留空 = 自动发现**该代实际用过的 s* 目录"
                         "（牌山套数历史上变过：第三季 3 套、第五季起 2 套）")
    ap.add_argument("--build-root", default=str(Path(__file__).resolve().parent.parent
                                                / "tools" / "build"))
    ap.add_argument("--gate-root", default=r"S:\mahjong-training\gate")
    a = ap.parse_args(argv)
    seeds = [int(s) for s in a.seeds.split(",") if s.strip()] or None
    return regrade(Path(a.build_root), Path(a.gate_root), a.label, a.incumbent, a.generations,
                   seeds)


if __name__ == "__main__":
    raise SystemExit(main())
