"""**v4 世代回路（Python 编排）** —— 训练端不再依赖 Java。

一轮 = **计划 → 采集（C++）→ 派生特征 → 校验 → 紧凑集 → 训练 → 导出 `net.bin` → 2+2 评测 → 配对判据 → 台账**。

为什么要单独有这一层（而不是继续手工敲命令）：
① v4 的轮次以前是**手敲的**（`online.py` 只认 v3 的 `state/cand` 紧凑集，一行 v4 都没有），
   于是"这一轮到底用了哪些开关"只活在 shell 历史里，复现不了；
② 采集与派生特征两个贵步骤以前**只有 Java 能全做**（`--aux` 标签侧缺失），而 C++ 同核数快十几倍 ——
   本回路默认 `MAHJONG_PRODUCER=cpp`，并且 `--no-java` 可以在"不许起 JVM"的前提下把整轮跑完
   （判据：`--dry-run` 打出的命令里**一个 `java` 都没有**）；
③ 判据（2+2 同牌山配对 + 聚类 bootstrap + `required_n`）本来就有唯一实现（`mahjong_ml.eval`），
   这里只负责**把它接在每一轮的出口上**，并把结果写进台账（`league/<label>-v4.json`）。

设计口径（与 `docs/TRAINING-V4.md` §7.2 对齐、与 `online.py` 的 v3 回路同精神）：
· **一处的开关只出现一次**：学生策略串（`--student-spec`）同时喂给采集、`dataset --student`；
  采集温度 `#T` 同时喂给 `--behaviour-temp`（preflight 会核对数据集 meta）。
· **每轮从上一轮的 `net.bin` 起步**（`--init` = 上一轮导出物），采完即训、训完即评、评完入账。
· **时间预算可选**：`--target-minutes > 0` 时用台账实测反推场次（`budget.plan_round` + 资源闸门），
  否则用 `--games`。

用法：

    python -m mahjong_ml.v4 loop --label v4-g01 --init tools/build/v4-bc-004/net.bin \\
        --generations 3 --games 400 --workers 24 --objective ppo --value-target final \\
        --student-temp 0.5 --eval-games 2000 --seed 20261010 --no-java
    python -m mahjong_ml.v4 loop ... --dry-run        # 只打印命令，不跑（无 java 的那条判据就在这看）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import budget as ml_budget
from .. import eval as ml_eval
from .. import paths, producer

ROOT = Path(__file__).resolve().parents[3]

#: 一轮里要跑的相（与 `budget.PHASES` 同集合 —— 那个模型算时长用的就是这几个名字）。
PHASES = ("collect", "features", "compact", "train", "export", "eval")
#: 哪些相是 `python -m …`（**工作目录必须是 `python/`**，否则 `mahjong_ml` 不在 sys.path 上 ——
#: 实测报 `No module named 'mahjong_ml'`）。node / 训练端可执行仍从仓库根跑。
PY_PHASES = frozenset({"compact", "train", "export"})


@dataclass
class LoopConfig:
    """一轮的全部开关（`plan_commands` 的唯一输入；纯数据，便于 --dry-run 与自检）。"""

    label: str
    init: str
    student_temp: float = 0.5
    teacher_seats: int = 2
    objective: str = "ppo"
    value_target: str = "final"
    epochs: int = 4
    batch: int = 256
    lr: float = 1e-4
    stage_a: float = 0.0
    stage_b: float = 0.0
    workers: int = 24
    eval_games: int = 2000
    eval_workers: int = 24
    seed: int = 20261010
    hands: int = 0
    producer: str = "cpp"
    no_java: bool = False
    sample: int = 1
    value_key: str = "value"

    def student_spec(self, net: Path) -> str:
        """采集/数据集共用的学生策略串：**逐字同一个字符串**（`is_student` 靠字符串相等判定）。"""
        return f"net:{net}@0#{self.student_temp:g}"

    def policy(self, net: Path) -> str:
        """四席策略串：学生 N 席 + teacher 其余（2+2 是默认，`--teacher-seats` 可调）。"""
        seats = [self.student_spec(net)] * (4 - self.teacher_seats) + ["teacher"] * self.teacher_seats
        return ",".join(seats)


@dataclass
class Round:
    """一轮的路径与命令（`plan_commands` 的返回值）。"""

    generation: int
    label: str
    net_in: Path
    net_out: Path
    ckpt_label: str
    raw: Path
    compact: Path
    eval_dir: Path
    commands: dict[str, list[str]] = field(default_factory=dict)

    def phases(self) -> list[str]:
        return [p for p in PHASES if p in self.commands]


def _py() -> str:
    """当前解释器（venv 里的那个）—— 子进程用它，避免"系统 python 没有 torch"。"""
    return sys.executable


def plan_commands(cfg: LoopConfig, generation: int, net_in: Path, games: int) -> Round:
    """生成一轮的全部命令（**不执行、不建目录**）—— `--dry-run` 与执行共用同一份。"""
    tag = f"{cfg.label}-g{generation:02d}"
    r = Round(
        generation=generation,
        label=tag,
        net_in=net_in,
        net_out=ROOT / "tools" / "build" / tag / "net.bin",
        ckpt_label=tag,
        raw=paths.DATA_ROOT / "raw" / tag,
        compact=paths.DATA_ROOT / "compact" / tag,
        eval_dir=paths.DATA_ROOT / "raw" / f"eval-{tag}",
    )
    spec = cfg.student_spec(net_in)
    p = cfg.producer
    r.commands["collect"] = producer.selfplay_cmd(
        games, cfg.workers, cfg.policy(net_in), cfg.seed + generation, r.raw,
        hands=cfg.hands, sample=cfg.sample, rotate=True, aux=True, name=p)
    r.commands["features"] = producer.features_cmd(r.raw, cfg.workers, name=p)
    r.commands["check"] = ["node", "tools/selfplay-check.mjs", str(r.raw)]
    r.commands["compact"] = [
        _py(), "-m", "mahjong_ml.v4.dataset", str(r.raw), str(r.compact),
        "--aux", "--student", spec, "--workers", str(max(1, cfg.workers // 2)),
    ]
    train = [
        _py(), "-m", "mahjong_ml.v4.pretrain", "--data", str(r.compact),
        "--label", r.ckpt_label, "--objective", cfg.objective,
        "--value-target", cfg.value_target, "--epochs", str(cfg.epochs),
        "--batch", str(cfg.batch), "--lr", f"{cfg.lr:g}",
        "--stage-a", f"{cfg.stage_a:g}", "--stage-b", f"{cfg.stage_b:g}",
        "--seed", str(cfg.seed + generation), "--init", str(net_in),
    ]
    if cfg.objective == "ppo":
        # π_old 必须来自**采集那份权重**与**采集那个温度**；数据集 meta 里记着学生串，训练端会核对
        train += ["--behaviour", str(net_in), "--behaviour-temp", f"{cfg.student_temp:g}"]
    r.commands["train"] = train
    r.commands["export"] = [
        _py(), "-m", "mahjong_ml.v4.export", "weights",
        "--ckpt", str(paths.DATA_ROOT / "ckpt" / r.ckpt_label / "model.pt"),
        "--out", str(r.net_out), "--label", r.ckpt_label,
    ]
    r.commands["eval"] = [
        str(producer.TRAINER), "selfplay", str(cfg.eval_games), "--workers", str(cfg.eval_workers),
        "--rotate", "--policy", f"net:{r.net_out},net:{r.net_out},teacher,teacher",
        "--seed", str(cfg.seed + 5000 + generation), "--out", str(r.eval_dir),
    ]
    return r


def assert_no_java(cfg: LoopConfig, rounds: list[Round]) -> None:
    """`--no-java` 的判据：**打出来的命令里一个 `java` 都不能有**（含 `-jar`）。"""
    if not cfg.no_java:
        return
    bad = [(r.label, phase, cmd) for r in rounds for phase, cmd in r.commands.items()
           if Path(str(cmd[0])).name.lower().startswith("java")]
    if bad:
        label, phase, cmd = bad[0]
        raise SystemExit(f"--no-java 但第 {label} 轮的 {phase} 相要起 JVM：{' '.join(cmd)}")


def _run(cmd: list[str], what: str, *, cwd: Path | None = None) -> float:
    """跑一条命令，返回墙钟秒数。失败**当场抛出**（不吞）。"""
    where = cwd or ROOT
    print(f"\n▶ {what}\n  $ {' '.join(cmd)}\n  （cwd={where}）", flush=True)
    t0 = time.perf_counter()
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(ROOT / "python"))     # 双保险：-m 找不到包时还有它
    r = subprocess.run(cmd, cwd=str(where), env=env)       # noqa: S603 —— 命令由本模块自己拼
    dt = time.perf_counter() - t0
    if r.returncode != 0:
        raise SystemExit(f"{what} 失败（退出码 {r.returncode}）：{' '.join(cmd)}")
    print(f"  ✓ {what} 用时 {dt:.1f}s", flush=True)
    return dt


def _ledger_path(label: str) -> Path:
    return paths.DATA_ROOT / "league" / f"{label}-v4.json"


def read_ledger(label: str) -> list[dict]:
    p = _ledger_path(label)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def write_ledger(label: str, rows: list[dict]) -> Path:
    p = _ledger_path(label)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def plan_games(cfg: LoopConfig, rows: list[dict], games_arg: int,
               target_minutes: float) -> tuple[int, str]:
    """场次：`--target-minutes > 0` 时按台账实测反推 + 资源闸门夹逼，否则就用 `--games`。

    @return `(场次, 一句人话说明是谁定的)`
    """
    if target_minutes <= 0:
        return games_arg, f"按 --games 取 {games_arg} 场（未开时间预算）"
    cost = ml_budget.fit_cost(rows)
    disk = ml_budget.fit_disk(rows)
    gates = ml_budget.gates_now(disk)
    plan = ml_budget.plan_round(target_minutes * 60.0, cost, gates,
                                min_games=min(100, games_arg), max_games=max(games_arg, 100))
    return plan.games, ml_budget.format_plan(plan, cost, target_minutes * 60.0)


def judge(eval_dir: Path, a_label: str, b_label: str) -> dict[str, Any]:
    """2+2 配对判据（复用 `eval.py` 的唯一实现，别在这里另写统计）。"""
    run = ml_eval.load_run(eval_dir)
    la = ml_eval.resolve_label(run, a_label)
    lb = ml_eval.resolve_label(run, b_label)
    p = ml_eval.paired_test(ml_eval.per_game_series(run, la, "rank_points"),
                            ml_eval.per_game_series(run, lb, "rank_points"), la, lb, "rank_points")
    return {
        "a": la, "b": lb, "n": p.n, "delta": p.mean, "lo": p.ci[0], "hi": p.ci[1],
        "p": p.p, "sd": p.sd, "win": p.win, "lose": p.lose, "tie": p.tie,
        "required_n_for_2": ml_eval.required_n(p.sd, 2.0),
    }


def run_round(cfg: LoopConfig, generation: int, net_in: Path, games: int,
              dry_run: bool = False) -> dict[str, Any]:
    """跑一轮（`--dry-run` 只打印）。返回台账行。"""
    r = plan_commands(cfg, generation, net_in, games)
    assert_no_java(cfg, [r])
    if dry_run:
        print(f"\n== 第 {generation} 代（{r.label}）计划：{games} 场"
              f"；student={cfg.student_spec(net_in)}；producer={cfg.producer} ==")
        for phase in r.phases():
            print(f"  [{phase}] {' '.join(r.commands[phase])}")
        return {"generation": generation, "label": r.label, "games": games, "dry_run": True,
                "net_out": str(r.net_out)}

    seconds: dict[str, float] = {}
    print(f"\n===== 第 {generation} 代（{r.label}）：{games} 场 / producer={cfg.producer} =====")
    for phase, what in (("collect", "采集（自对弈）"), ("features", "派生特征（sidecar）")):
        seconds[phase] = _run(r.commands[phase], what,
                              cwd=ROOT / "python" if phase in PY_PHASES else ROOT)
    _run(r.commands["check"], "轨迹校验（selfplay-check）", cwd=ROOT)
    for phase, what in (("compact", "紧凑集（四张量 + 标签）"),
                        ("train", f"训练（{cfg.objective}）"),
                        ("export", "导出 net.bin")):
        seconds[phase] = _run(r.commands[phase], what, cwd=ROOT / "python")
    seconds["eval"] = _run(r.commands["eval"], f"2+2 评测（{cfg.eval_games} 场）", cwd=ROOT)
    got = judge(r.eval_dir, f"net:{r.net_out}", "teacher")
    print("  判据：" + f"Δ={got['delta']:+.2f} 顺位点 [{got['lo']:+.2f}, {got['hi']:+.2f}] "
          f"p={got['p']:.3f}（n={got['n']}；按观测 sd 检出 Δ=2 需 {got['required_n_for_2']} 场）")
    row = {
        "generation": generation, "label": r.label, "games": games, "seed": cfg.seed + generation,
        "producer": cfg.producer, "objective": cfg.objective, "value_target": cfg.value_target,
        "student_spec": cfg.student_spec(net_in), "net_in": str(net_in), "net_out": str(r.net_out),
        "ckpt": str(paths.DATA_ROOT / "ckpt" / r.ckpt_label), "raw": str(r.raw),
        "compact": str(r.compact), "eval_dir": str(r.eval_dir),
        "seconds": {k: round(v, 1) for k, v in seconds.items()},
        "seconds_total": round(sum(seconds.values()), 1),
        "eval": got,
    }
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mahjong_ml.v4 loop",
                                 description="v4 世代回路（默认 C++ 生产者；训练端不依赖 Java）")
    ap.add_argument("--label", required=True, help="这一轮的系列名（台账 league/<label>-v4.json）")
    ap.add_argument("--init", required=True, help="起始权重（ckpt 目录下的 model.pt 或 net.bin）")
    ap.add_argument("--generations", type=int, default=1)
    ap.add_argument("--games", type=int, default=400, help="每代采集场次（未开时间预算时就用它）")
    ap.add_argument("--target-minutes", type=float, default=0.0,
                    help=">0 = 按台账实测反推场次（budget.plan_round + 资源闸门）")
    ap.add_argument("--workers", type=int, default=24)
    ap.add_argument("--eval-games", type=int, default=2000)
    ap.add_argument("--eval-workers", type=int, default=24)
    ap.add_argument("--objective", choices=["bc", "rwr", "ppo"], default="ppo")
    ap.add_argument("--value-target", choices=["final", "rtg"], default="final")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--stage-a", type=float, default=0.0)
    ap.add_argument("--stage-b", type=float, default=0.0)
    ap.add_argument("--teacher-seats", type=int, default=2)
    ap.add_argument("--student-temp", type=float, default=0.5)
    ap.add_argument("--hands", type=int, default=0, help="每场最多几小局（0 = 完整半庄）")
    ap.add_argument("--sample", type=int, default=1, help="每 K 次决策记 1 条（冒烟用）")
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--producer", choices=producer.PRODUCERS, default=None,
                    help="缺省读 MAHJONG_PRODUCER，再缺省 **cpp**（v4 回路就是为脱离 Java 建的）")
    ap.add_argument("--no-java", action="store_true",
                    help="硬保证整轮不起 JVM（打出的命令里出现 java 就报错）")
    ap.add_argument("--dry-run", action="store_true", help="只打印这一轮的命令，不跑")
    args = ap.parse_args(argv)

    if args.no_java:
        # 双保险：环境变量那条守卫（`producer.guard_java_free`）也是给别的入口用的
        os.environ["MAHJONG_NO_JAVA"] = "1"
    chosen = args.producer or (os.environ.get("MAHJONG_PRODUCER") or "cpp").strip().lower()
    if chosen not in producer.PRODUCERS:
        raise SystemExit(f"--producer 只能是 {'/'.join(producer.PRODUCERS)}，收到 {chosen!r}")
    cfg = LoopConfig(
        label=args.label, init=args.init, student_temp=args.student_temp,
        teacher_seats=args.teacher_seats, objective=args.objective,
        value_target=args.value_target, epochs=args.epochs, batch=args.batch, lr=args.lr,
        stage_a=args.stage_a, stage_b=args.stage_b, workers=args.workers,
        eval_games=args.eval_games, eval_workers=args.eval_workers, seed=args.seed,
        hands=args.hands, producer=chosen, no_java=args.no_java, sample=args.sample,
    )
    net_in = Path(args.init)
    if not net_in.is_file() and not args.dry_run:
        raise SystemExit(f"--init 找不到文件：{net_in}（ckpt 目录请给里面的 model.pt / net.bin）")
    paths.ensure_root()
    rows = read_ledger(args.label)
    games, why = plan_games(cfg, rows, args.games, args.target_minutes)
    print(f"v4 回路：系列 {args.label} / {args.generations} 代 / producer={cfg.producer}"
          f"{'（--no-java）' if cfg.no_java else ''}\n  场次：{why}")
    for g in range(1, args.generations + 1):
        row = run_round(cfg, g, net_in, games, dry_run=args.dry_run)
        net_in = Path(row["net_out"])            # dry-run 与真跑都从"这一轮该产出的 net"往下走
        if args.dry_run:
            continue
        rows.append(row)
        p = write_ledger(args.label, rows)
        print(f"  台账：{p}")
        net_in = Path(row["net_out"])
    if args.dry_run:
        print("\n（dry-run：没有跑任何命令；上面的命令里不应出现 java —— 那就是 --no-java 的判据）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
