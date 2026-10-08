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
    """纯判据：**候选（`b`）− 现任（`a`）** 的 CI 排除 0 且为正 ⇒ 采纳。

    ⚠ **符号陷阱（实测踩过，代价是整轮判决方向反了）**：`eval.paired_test` 的约定是
    `diff = a_series − b_series`、**正 = `a` 更好**；而"采纳"要问的是**候选更好吗**。
    所以这里**必须**按 `judge_pair(sb, sa, b, a)`（候选在前）算 —— 若写成 `(sa, sb, a, b)`，
    `lo > 0` 就变成"**现任显著更好**"却去采纳候选（方向整反）。
    空对照（`a == b` ⇒ Δ=0 ⇒ CI[0,0]）**两种写法都判不采纳**，所以它抓不出这个反向 ——
    必须有**方向性对照**（见 `self_check`：弱现任 vs 强候选 ⇒ 必须采纳）。
    """
    r = arena.judge_pair(sb, sa, b, a, metric)          # 候选 − 现任（正 = 候选更好）
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
    wall_deltas: list[float] = []
    print(f"闸门：现任={Path(incumbent).parent.name} vs 候选={Path(candidate).parent.name}"
          f"；{len(seeds)} 套牌山 × 每套至多 {games} 场（block={block}）")
    for s in seeds:
        d = root / f"s{s}"
        arena.run_pair(d, incumbent, candidate, block, s, workers=workers, prod=prod)
        x, y = arena.judge_series(d, incumbent, candidate, metric)
        # ⚠ **逐牌山这行必须与下面的合并判决同一个符号**（候选 − 现任）。`judge_pair(sa, sb, a, b)`
        #   的约定是 `Δ = a − b`，所以这里要按 `(y, x, 候选, 现任)` 调 —— 写成 `(x, y, 现任, 候选)`
        #   会打印出**符号相反**的逐牌山读数（判决本身不受影响，因为它走 `decide`），
        #   症状极具误导性：两套牌山 `+2.34 / −0.08` 而合并是 `−1.13`，读日志的人会以为
        #   "候选赢了一套"。（实测踩过：第八季 g01；判决逻辑没错，错的是这行 printf。）
        part = arena.judge_pair(y, x, candidate, incumbent, metric)
        print(f"  牌山 {s}：Δ={part.delta:+6.2f} CI[{part.lo:+6.2f},{part.hi:+6.2f}] n={part.games}"
              f"（候选 − 现任）")
        wall_deltas.append(float(part.delta))
        series.append((x, y))
    sa, sb = pooled_diffs(series)
    out = decide(sa, sb, incumbent, candidate, metric)
    out["seeds"] = list(seeds)
    out["incumbent"] = incumbent
    out["candidate"] = candidate
    # ⚠ **套间散布要打出来**（第八季审计）：合并 CI 只含"套内"的配对噪声，而"换一套牌山"本身
    #   能把 Δ 摆动好几个点（实测同一候选：闸门 −2.26 vs 换牌山的自评 +1.32）。所以
    #   ① 判决只能读成"**条件于这几套牌山**"；② 套间极差 ≫ CI 半宽时，这个判决不该被当成
    #   "这一代普遍更好/更差"的证据，更不该把历次判决当独立复现来统计（牌山若是共用的就会伪重复）。
    spread = (max(wall_deltas) - min(wall_deltas)) if len(wall_deltas) > 1 else 0.0
    out["wall_deltas"] = wall_deltas
    out["wall_spread"] = spread
    half = (out["hi"] - out["lo"]) / 2
    out["wall_spread_over_ci"] = (spread / half) if half > 0 else float("inf")
    print(f"  套间散布：极差 {spread:.2f} / 合并 CI 半宽 {half:.2f}"
          f" = {out['wall_spread_over_ci']:.2f}×"
          + ("  ⚠ 套间散布已超过 CI 半宽 ⇒ **判决是条件于这几套牌山的**，别当普遍结论"
             if out["wall_spread_over_ci"] > 1 else ""))
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


def self_check(out_root: Path, *, workers: int = 12, prod: str | None = None) -> int:
    """闸门自证：**方向必须对**（空对照抓不出方向问题，所以这里用强弱对照）。

    两组对照（用竞技场自证那对已知强弱的策略，200 场足够把 Δ≈90 拉开）：

    1. 弱现任（`first`）vs 强候选（`teacher`）⇒ **必须采纳**；
    2. 强现任（`teacher`）vs 弱候选（`first`）⇒ **必须拒绝**。

    另外把"符号写反"的后果**显式跑一遍**（同两份序列按相反顺序调 `decide`）：
    正确顺序采纳、反向顺序不采纳 ⇒ 一旦有人把顺序改回去，这两条会立刻打脸。
    """
    ok = 0
    print("== 闸门自证①：弱现任 vs 强候选 ⇒ 必须采纳 ==")
    d = {"incumbent": "first", "candidate": "teacher"}
    arena.run_pair(out_root / "selfcheck-weak-to-strong", "first", "teacher", 200, 20260930,
                   workers=workers, prod=prod)
    sa, sb = arena.judge_series(out_root / "selfcheck-weak-to-strong", "first", "teacher")
    r1 = decide(sa, sb, "first", "teacher")
    r1_wrong = decide(sb, sa, "teacher", "first")          # 故意写反
    print(f"   正确顺序：Δ={r1['delta']:+.2f} CI[{r1['lo']:+.2f},{r1['hi']:+.2f}] "
          f"⇒ {'采纳' if r1['adopt'] else '不采纳'}")
    print(f"   写反顺序：Δ={r1_wrong['delta']:+.2f} ⇒ {'采纳' if r1_wrong['adopt'] else '不采纳'}"
          "（必须与正确顺序**相反**，否则这条自证没意义）")
    if r1["adopt"] and not r1_wrong["adopt"]:
        ok += 1
        print("   ✓ 方向对照通过（且反向写法会被抓住）")
    else:
        print("   ✗ 方向对照失败")

    print("== 闸门自证②：强现任 vs 弱候选 ⇒ 必须拒绝 ==")
    arena.run_pair(out_root / "selfcheck-strong-to-weak", "teacher", "first", 200, 20260930,
                   workers=workers, prod=prod)
    sa2, sb2 = arena.judge_series(out_root / "selfcheck-strong-to-weak", "teacher", "first")
    r2 = decide(sa2, sb2, "teacher", "first")
    print(f"   Δ={r2['delta']:+.2f} CI[{r2['lo']:+.2f},{r2['hi']:+.2f}] "
          f"⇒ {'采纳' if r2['adopt'] else '不采纳'}")
    if not r2["adopt"]:
        ok += 1
        print("   ✓ 通过")
    else:
        print("   ✗ 失败（把更弱的候选采纳了）")
    print("GATE SELFCHECK PASS：方向与判据都不是空转" if ok == 2
          else "GATE SELFCHECK FAIL")
    return 0 if ok == 2 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="训练的接受闸门（多套牌山集合的合并配对判决）")
    ap.add_argument("--incumbent", help="现任 net.bin（a），可写路径或 net: 串")
    ap.add_argument("--candidate", help="候选 net.bin（b），可写路径或 net: 串")
    ap.add_argument("--self-check", action="store_true",
                    help="跑方向性对照（弱现任 vs 强候选必须采纳、反向必须拒绝）")
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
    if args.self_check:
        return self_check(out_root, workers=args.workers, prod=args.producer)
    if not (args.incumbent and args.candidate):
        ap.error("要么给 --incumbent/--candidate，要么用 --self-check")
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    r = gate(_policy(args.incumbent), _policy(args.candidate), seeds, args.games, args.block,
             out_root, workers=args.workers, metric=args.metric, tag=args.tag,
             prod=args.producer)
    return 0 if r["adopt"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
