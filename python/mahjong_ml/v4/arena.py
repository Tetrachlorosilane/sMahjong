"""竞技场：多策略、**共享牌山**、带**序贯判决**的强度比较（2026-09-29）。

## 为什么需要它（这一步不做，后面全是盲飞）

实测的功率问题：逐场顺位点的 `sd ≈ 53`，要在 α=0.05 / power=0.8 下检出 **Δ=2 顺位点**需要
**~5,000 场**。而回路里以前每轮只跑 **200 场** ⇒ 那些 Δ 的 CI 全都跨 0，**根本没有判决力**，
却容易被读成"训练没效果"或"有效果"——两种误读都发生过。

所以这里把三件事做成工具：

1. **共享牌山**：所有配对用**同一个 seed** 跑（`--policy A,A,B,B --rotate`）⇒ 同一副牌山、
   同一批局面，配对差分的方差远小于独立样本；而且**跨配对可比**（同一副牌山上谁赢谁）。
2. **序贯判决**：按 `--block` 分块跑，每块之后看配对 CI —— CI 排除 0 就**提前收工**（省算力），
   否则跑满 `--games` 再给"分不出"的结论（并报出**还差多少场**才够功率）。
3. **自带两个对照**（工具自身的红证，任何结论之前先跑它们）：
   - `--self-check`：拿**已知更强**的一对（`teacher` vs `first`）⇒ 必须判出"更好"；
   - 同策略对同策略（A vs A）⇒ 必须判"分不出"（Δ≈0、CI 跨 0）。
   工具判不出这两个，它的结论一文不值。

## 用法

    python -m mahjong_ml.v4 arena --policies teacher,net:tools/build/v4-ppo-001/net.bin \
        --games 3000 --block 500 --seed 20260930 --out <可写目录>

⚠ `--rotate` 让每个策略在四家座位上都坐过 ⇒ 座位偏差被消掉（**别去掉它**）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

from mahjong_ml import eval as ml_eval
from mahjong_ml import paths
from mahjong_ml import producer

#: 一场比赛默认落在哪个输出目录（`S:` 的 `arena/` 下，和 `raw/` 同一个数据根）
ARENA_DIRNAME = "arena"


def pair_plan(policies: list[str]) -> list[tuple[str, str]]:
    """要跑哪些配对（**全部两两组合**；顺序稳定，便于比对两次运行的台账）。"""
    return list(combinations(policies, 2))


def verdict(delta: float, lo: float, hi: float) -> str:
    """配对结论（**只看 CI**，不看 p：p 会被样本量放大而失去"多大算大"的量纲）。"""
    if lo > 0:
        return "更好"
    if hi < 0:
        return "更差"
    return "分不出"


def seq_decide(games_done: int, delta: float, lo: float, hi: float, *,
               games_target: int, block: int) -> str:
    """序贯判决：`continue` 继续跑、`stop_better` / `stop_worse` / `stop_null` 收工。

    ⚠ 三条口径：
    ① **CI 排除 0 就停**（无论跑没跑满）——序贯地反复看 CI 会略微抬高假阳性率，所以下面这条
       比"单次 95% CI"更严：`stop_better` 还要求**至少跑满一个 block**（避免 20 场就宣布获胜）；
    ② 跑满 `games_target` 还没排除 0 ⇒ `stop_null`（**这是结论**："在这个样本量下分不出"），
       并且把"要检出 Δ=2 还差多少场"一起报出来；
    ③ `block` 只影响"多久看一次"，不影响最终样本量。
    """
    if games_done <= 0:
        return "continue"
    if lo > 0:
        return "stop_better" if games_done >= block else "continue"
    if hi < 0:
        return "stop_worse" if games_done >= block else "continue"
    if games_done >= games_target:
        return "stop_null"
    return "continue"


@dataclass
class PairResult:
    """一对策略在某一块之后的读数。"""

    a: str
    b: str
    games: int
    delta: float
    lo: float
    hi: float
    p: float
    sd: float
    win: int
    lose: int
    tie: int
    need_for_2: int
    verdict: str = "分不出"

    @property
    def short(self) -> str:
        return (f"Δ={self.delta:+6.2f} CI[{self.lo:+6.2f},{self.hi:+6.2f}] "
                f"{self.verdict:<4} n={self.games:<5} 胜/负/平 {self.win}/{self.lose}/{self.tie}"
                f" 检出Δ=2需 {self.need_for_2}")


def _label_of(policy: str) -> str:
    """自对弈轨迹里的策略标签：`net:` / `teacher` / `first`… 原样（`eval.resolve_label` 认得）。"""
    return policy


def run_pair(dir_out: Path, a: str, b: str, games: int, seed: int, *,
             workers: int = 16, hands: int = 0) -> None:
    """跑一对：`--rotate` + `a,a,b,b`（同牌山配对的唯一正确跑法）。"""
    cmd = [str(producer.TRAINER), "selfplay", str(games), "--workers", str(workers),
           "--rotate", "--policy", f"{a},{a},{b},{b}",
           "--seed", str(seed), "--hands", str(hands), "--out", str(dir_out)]
    dir_out.mkdir(parents=True, exist_ok=True)
    print("  $ " + " ".join(cmd), flush=True)
    rc = subprocess.run(cmd, cwd=str(producer.ROOT)).returncode
    if rc != 0:
        raise SystemExit(f"自对弈失败（退出码 {rc}）：{' '.join(cmd)}")


def judge_series(dir_out: Path, a: str, b: str, metric: str = "rank_points"):
    """读一次自对弈：返回 `(a 的逐场值, b 的逐场值)`（**按 seed 索引**，跨块可合并）。

    ⚠ 一定返回**逐场值**而不是只返回统计量：分块跑之后要把各块的逐场值**并起来重算**，
    而不是把各块的 CI 取并集 —— 后者是错的（并集 CI 会把"两块各 500 场"说得比实测更保守，
    而且丢掉配对的方差削减）。
    """
    run = ml_eval.load_run(dir_out)
    return (ml_eval.per_game_series(run, a, metric), ml_eval.per_game_series(run, b, metric))


def judge_pair(sa: dict[int, float], sb: dict[int, float], a: str, b: str,
               metric: str = "rank_points") -> PairResult:
    """按 seed 做配对检验（`sa` / `sb` 可以已经是多块并起来的结果）。"""
    p = ml_eval.paired_test(sa, sb, a, b, metric)
    return PairResult(a=a, b=b, games=p.n, delta=p.mean, lo=p.ci[0], hi=p.ci[1], p=p.p,
                      sd=p.sd, win=p.win, lose=p.lose, tie=p.tie,
                      need_for_2=ml_eval.required_n(p.sd, 2.0),
                      verdict=verdict(p.mean, p.ci[0], p.ci[1]))


def arena(policies: list[str], games: int, block: int, seed: int, out_root: Path, *,
          workers: int = 16, metric: str = "rank_points", tag: str = "arena") -> dict:
    """跑完整竞技场：每一对分块跑、块间看 CI、排除 0 就提前收工。"""
    root = out_root / tag
    plan = pair_plan(policies)
    print(f"竞技场：{len(policies)} 个策略 / {len(plan)} 对；每对至多 {games} 场"
          f"（block={block}）；牌山 seed={seed}")
    result: dict[str, list[PairResult]] = {}
    for a, b in plan:
        key = f"{a}__vs__{b}"
        done = 0
        history: list[PairResult] = []
        cum_a: dict[int, float] = {}
        cum_b: dict[int, float] = {}
        while done < games:
            this = min(block, games - done)
            d = root / f"{tag}-{len(history) + 1:02d}-{abs(hash((a, b, seed, done))) % 10**6:06d}"
            run_pair(d, a, b, this, seed + done, workers=workers)
            sa, sb = judge_series(d, a, b, metric)
            cum_a.update(sa)          # 逐场值并起来（seed 唯一 ⇒ update 即合并）
            cum_b.update(sb)
            done += this
            cum = judge_pair(cum_a, cum_b, a, b, metric)
            history.append(cum)
            print(f"  [{a} vs {b}] {cum.short}")
            if seq_decide(cum.games, cum.delta, cum.lo, cum.hi,
                          games_target=games, block=block).startswith("stop"):
                break
        result[key] = [history[-1]]
    return {"policies": policies, "seed": seed, "games_target": games, "metric": metric,
            "pairs": {k: [r.__dict__ for r in v] for k, v in result.items()}}


def null_shuffle(sa: dict[int, float], sb: dict[int, float], *, k: int = 200,
                 seed: int = 20260930, metric: str = "rank_points") -> dict:
    """**校准 CI 的空对照**：把一对**真实读数**逐场随机互换标签 K 次（真值 Δ=0），
    统计有多少次 CI 排除 0 —— 应当 ≈ α（0.05）。高于 α 说明 CI 偏窄、判决偏乐观。

    ⚠ 为什么需要它：`A vs A`（同策略）是**退化**空对照（Δ 恒等于 0、sd=0），它只能证明
    管线通，**证明不了"有噪声时不会假阳"**。而随机换标签把"真实噪声 + 真实牌山"
    留下来、只把因果抹掉 ⇒ 这才是对判决规则的校准。
    """
    import numpy as np
    keys = sorted(set(sa) & set(sb))
    if not keys:
        return {"k": 0, "false_positive_rate": float("nan"), "games": 0}
    xa = np.array([sa[g] for g in keys], dtype=float)
    xb = np.array([sb[g] for g in keys], dtype=float)
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(k):
        flip = rng.random(len(keys)) < 0.5          # 每场独立决定要不要把两侧互换
        a2 = {g: (xb[i] if flip[i] else xa[i]) for i, g in enumerate(keys)}
        b2 = {g: (xa[i] if flip[i] else xb[i]) for i, g in enumerate(keys)}
        p = ml_eval.paired_test(a2, b2, "shuffle-a", "shuffle-b", metric)
        if p.ci[0] > 0 or p.ci[1] < 0:
            hits += 1
    return {"k": k, "games": len(keys), "false_positive_rate": hits / k}


def _unused_pool_removed() -> None:
    """（占位：旧的 `_pool` 已删 —— 分块必须**合并逐场值**再重算，见 `judge_series` 的注释。）"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="竞技场：多策略共享牌山 + 序贯判决")
    ap.add_argument("--policies", required=True,
                    help="逗号分隔（`teacher` / `net:<权重文件>` / `first` / …）；两两组合都要打")
    ap.add_argument("--games", type=int, default=3000, help="每对的**至多**场次（够功率的量级见模块注释）")
    ap.add_argument("--block", type=int, default=500, help="分块大小（每块之后看一次 CI 决定是否收工）")
    ap.add_argument("--seed", type=int, default=20260930)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--metric", default="rank_points", choices=["rank_points", "place", "score"])
    ap.add_argument("--out", default=None, help="输出根（缺省 `S:<数据根>/arena`）")
    ap.add_argument("--tag", default="arena")
    ap.add_argument("--self-check", action="store_true",
                    help="先跑工具自身对照：`teacher vs first`（必须判「更好」）与 `A vs A`（必须「分不出」）")
    args = ap.parse_args(argv)

    # ⚠ **必须转成绝对路径**：自对弈是在仓库根下起的（`cwd=producer.ROOT`），相对路径会被
    #   trainer 解析到仓库根，而 Python 这边按自己的 cwd 去读 ⇒ 找不到 summary.json（实测踩过）。
    out_root = Path(args.out).resolve() if args.out else paths.DATA_ROOT / ARENA_DIRNAME
    policies = [s for s in args.policies.split(",") if s]

    if args.self_check:
        print("== 工具自证①：已知更强的一对必须判出来（teacher vs first）==")
        arena(["teacher", "first"], min(args.games, 400), min(args.block, 200), args.seed,
              out_root, workers=args.workers, metric=args.metric, tag=f"{args.tag}-ctrl-strong")
        print("== 工具自证②：同策略对同策略必须判「分不出」（退化空对照：证明管线通）==")
        arena([policies[0], policies[0]], min(args.games, 400), min(args.block, 200), args.seed,
              out_root, workers=args.workers, metric=args.metric, tag=f"{args.tag}-ctrl-null")
        print("== 工具自证③：**校准**空对照（把真实读数逐场随机换标签，真值 Δ=0）==")
        d = out_root / f"{args.tag}-ctrl-strong"
        runs = sorted(p for p in d.glob("*") if (p / "summary.json").is_file())
        if runs:
            sa, sb = judge_series(runs[0], "teacher", "first", args.metric)
            cal = null_shuffle(sa, sb, k=200, seed=args.seed, metric=args.metric)
            print(f"  假阳率 {cal['false_positive_rate']:.3f}（k={cal['k']}，n={cal['games']}；"
                  f"应当 ≈0.05 —— 明显更高说明 CI 偏窄、判决偏乐观）")
        return 0

    got = arena(policies, args.games, args.block, args.seed, out_root,
                workers=args.workers, metric=args.metric, tag=args.tag)
    f = out_root / f"{args.tag}.json"
    f.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n台账：{f}")
    print("\n== 排名（按两两胜负计数；Δ 是顺位点，正 = 前者更好）==")
    score: dict[str, int] = {p: 0 for p in policies}
    for key, rows in got["pairs"].items():
        r = rows[-1]
        if r["verdict"] == "更好":
            score[r["a"]] += 1
        elif r["verdict"] == "更差":
            score[r["b"]] += 1
    for p, s in sorted(score.items(), key=lambda kv: -kv[1]):
        print(f"  {s:>2} 胜  {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
