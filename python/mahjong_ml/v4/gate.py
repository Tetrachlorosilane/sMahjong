"""训练的**接受闸门**：新一代要替换"现任"，必须在**多套牌山集合**上赢下来。

<h2>为什么必须有它（2026-09-30 第二季的教训）</h2>

第二季 8 代"跑完即采纳"，终点比它自己的起点（第一季 `g08`）**退步 6.13 顺位点** `[-9.50,-2.74]`，
而**逐代 200 场的读数全是噪声**（第二季 g05 对 g04 `+10.29 [+2.52,+18.03]`，紧接着 g06 对 g05
`-8.65 [-16.02,-1.13]` —— 一涨一跌正好抵消，CI 半宽本来就有 ±8）。
⇒ **没有闸门时，"训练"会把退步当推进**（3 小时 × 8 代换来一次无声退步）。

<h2>判据</h2>

1. 同一对策略在**多个 `--seed`**（= 多套牌山集合）上各跑 `block` 场，`--rotate` 让两边都坐过四家；
2. 把各套的**逐场配对差分合并**（⚠ **不是**把各套的 CI 取并集 —— 那既丢配对的方差削减、
   又把结论说得比实测更保守），再重算一次配对检验；
3. 只有"**候选 − 现任**"的 CI **排除 0 且为正**才 `adopt=True`，否则一律回滚到现任。

⚠ 合并时必须给每套牌山一个**键偏移**：`per_game_series` 的键是场次 id，不同 `--seed` 之间会撞车，
直接 `update` 会把上一套的场次覆盖掉（配对关系也跟着错位）。

用法：

    python -m mahjong_ml.v4.gate --incumbent <net.bin> --candidate <net.bin> \\
        --seeds 20260930,20261001,20261002 --games 2000 --block 1000 --out <数据根>/gate

退出码：**0 = 采纳**；**3 = 不采纳**（脚本据此决定是否回滚）；其它 = 出错。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mahjong_ml import eval as ml_eval
from mahjong_ml import paths

from . import arena

#: 跨牌山集合合并时的键偏移（每套给一段互不重叠的场次号）
_KEY_STRIDE = 1_000_000


def pooled_diffs(series: list[tuple[dict[int, float], dict[int, float]]],
                 stride: int = _KEY_STRIDE) -> tuple[dict[int, float], dict[int, float]]:
    """把多套牌山集合的逐场值合并成一份（**加键偏移，避免撞车**）。

    @param series 每套一份 `(a 的逐场值, b 的逐场值)`（来自 `arena.judge_series`）
    """
    sa: dict[int, float] = {}
    sb: dict[int, float] = {}
    for i, (x, y) in enumerate(series):
        off = i * stride
        for k, v in x.items():
            sa[int(k) + off] = float(v)
        for k, v in y.items():
            sb[int(k) + off] = float(v)
    return sa, sb


def decide(sa: dict[int, float], sb: dict[int, float], a: str, b: str,
           metric: str = "rank_points") -> dict:
    """纯判据：CI 排除 0 且为正 ⇒ 采纳。（`a` = 现任，`b` = 候选）"""
    r = arena.judge_pair(sa, sb, a, b, metric)
    return {"delta": r.delta, "lo": r.lo, "hi": r.hi, "p": r.p, "n": r.games,
            "sd": r.sd, "win": r.win, "lose": r.lose, "tie": r.tie,
            "need_for_2": r.need_for_2, "verdict": r.verdict,
            "adopt": bool(r.lo > 0.0)}


def gate(incumbent: str, candidate: str, seeds: list[int], games: int, block: int,
         out_root: Path, *, workers: int = 16, metric: str = "rank_points",
         tag: str = "gate", prod: str | None = None) -> dict:
    """跑闸门：多套牌山集合 × 分块，最后按**合并后的**配对差分判决。"""
    root = out_root / tag
    series: list[tuple[dict[int, float], dict[int, float]]] = []
    print(f"闸门：现任={Path(incumbent).parent.name} vs 候选={Path(candidate).parent.name}"
          f"；{len(seeds)} 套牌山 × 每套至多 {games} 场（block={block}）")
    for s in seeds:
        d = root / f"s{s}"
        arena.run_pair(d, incumbent, candidate, block, s, workers=workers, prod=prod)
        x, y = arena.judge_series(d, incumbent, candidate, metric)
        part = arena.judge_pair(x, y, incumbent, candidate, metric)
        print(f"  牌山 {s}：Δ={part.delta:+6.2f} CI[{part.lo:+6.2f},{part.hi:+6.2f}] n={part.games}")
        series.append((x, y))
    sa, sb = pooled_diffs(series)
    out = decide(sa, sb, incumbent, candidate, metric)
    out["seeds"] = list(seeds)
    out["incumbent"] = incumbent
    out["candidate"] = candidate
    print(f"== 合并判决：Δ={out['delta']:+6.2f} CI[{out['lo']:+6.2f},{out['hi']:+6.2f}] "
          f"n={out['n']} ⇒ {'采纳' if out['adopt'] else '不采纳（回滚到现任）'}")
    root.mkdir(parents=True, exist_ok=True)
    (root / "verdict.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    return out


def _policy(spec: str) -> str:
    """把 `net.bin` 路径或 `net:...` 策略串统一成策略串（调用方两种写法都行）。"""
    s = spec.strip()
    return s if s.startswith(("net:", "teacher", "first", "pass", "random")) else f"net:{s}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="训练的接受闸门（多套牌山集合的合并配对判决）")
    ap.add_argument("--incumbent", required=True, help="现任 net.bin（a），可写路径或 net: 串")
    ap.add_argument("--candidate", required=True, help="候选 net.bin（b），可写路径或 net: 串")
    ap.add_argument("--seeds", default="20260930,20261001,20261002",
                    help="逗号分隔的牌山 seed（每套一个 block）")
    ap.add_argument("--games", type=int, default=2000, help="每套的场次上限（配合 --block 提前收工）")
    ap.add_argument("--block", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--metric", default="rank_points")
    ap.add_argument("--out", default=None, help="输出根（缺省 <数据根>/gate）")
    ap.add_argument("--tag", default="gate")
    ap.add_argument("--producer", choices=["cpp", "java"], default="cpp")
    args = ap.parse_args(argv)
    out_root = Path(args.out).resolve() if args.out else paths.DATA_ROOT / "gate"
    arena.guard_out(out_root)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    r = gate(_policy(args.incumbent), _policy(args.candidate), seeds, args.games, args.block,
             out_root, workers=args.workers, metric=args.metric, tag=args.tag,
             prod=args.producer)
    return 0 if r["adopt"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
