"""P4 · **世代循环**（采集 → 导数特征 → 紧凑集 → PPO → 导出）与**联赛阶梯**（Elo / 配对判据）。

    # 一代一代往上爬（每代都用**上一代的网络**采样自对弈，对手来自联赛池）
    python -m mahjong_ml.online run --init S:\\mahjong-training\\ckpt\\awr-002\\model.pt \\
        --label ppo --generations 8 --gen-games 2000 --temp 1.0 --workers 24
    # 阶梯：每代一跑 `net:gNN,teacher,first,random`（--rotate 四座位轮转）→ Plackett-Luce + 配对 CI
    python -m mahjong_ml.online ladder --label ppo --generations 8 --games 1200

## 为什么是这个形状（对着 `docs/TRAINING.md` §4 P4 的三条保命措施）

1. **BC/IQL 初始化**：`--init` 必填（默认接 AWR 的 checkpoint），不从零开始 —— 从零开始的 PPO
   在这个动作空间上只会先学会"别放铳"这种平凡行为。
2. **奖励塑形**：奖励口径直接沿用 `rewards.py`（小局收支 + 整场顺位点），不是只给终局 ——
   见 `ppo.py` 的口径段。
3. **联赛对手**：对手**不是**"只有自己最新版"。第一代先跟老师打（两只没学好的自己互啄学不到东西），
   之后每一代从池子（`teacher` + `first`/`pass`/`random` + 历史各代网络）里按
   `league.select_weights` 采样 —— "谁克我就多跟它打"，且**老师常驻一个席位**（它是基准）。

## 判据（P4 的"过/不过"）

- **Elo 相对 `teacher` 单调上升**：`ladder` 的 Plackett-Luce θ（同一份名次数据，按场聚类 bootstrap）；
- **对脚本基线不掉**：`first`/`pass`/`random` 的 θ 不许"我涨它也涨到超过我"（同一次 fit 里读）；
- 出现"对 `teacher` 变强、对 `random` 变弱"= 过拟合到自己的策略 → **回退**（见 §4 P4 的判据原文）。

⚠ 采到的每一代原始轨迹都留在 `raw/`（配额 80 GB，滚动淘汰最旧的）：**判据要能复算**，
所以别删；真要清盘就删 `compact/`（它能从 `raw/` 重建）。

## "一轮"有多长：时间预算，不是固定场次（2026-09-27）

`--target-minutes 30` = **这一轮的场次由时间预算反推**（成本模型见 `budget.py`，从台账实测回填），
再被资源闸门夹逼（raw/compact/总配额余量、盘余量、`--max-games`）。报告里会写清"是谁限制了这一轮"。
`--gen-games` 只在 `--target-minutes 0`（默认）时起作用 —— 老命令行行为逐字不变。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import torch

from . import budget, features, league, paths
from . import producer as producers

#: 仓库根（`python/mahjong_ml/online.py` → 上三级）
ROOT = Path(__file__).resolve().parents[2]
JAR = ROOT / "server" / "build" / "mahjong-server.jar"


# ------------------------------------------------------------------ 外部命令


def py() -> str:
    """当前解释器（`.venv` 里那个）—— 子进程必须用同一个，别指望 `python` 在 PATH 上。"""
    return sys.executable


def run(cmd: list[str], what: str, cwd: Path | None = None) -> float:
    """跑一条外部命令。⚠ **不捕获 stdout**（受限沙箱下管道会 EPERM）：直接继承，边跑边看。"""
    print(f"\n$ {' '.join(cmd)}", flush=True)
    t = time.perf_counter()
    rc = subprocess.run(cmd, cwd=str(cwd or ROOT)).returncode
    dt = time.perf_counter() - t
    if rc != 0:
        raise SystemExit(f"{what} 失败（退出码 {rc}）：{' '.join(cmd)}")
    print(f"== {what} 完成（{dt:.1f}s）", flush=True)
    return dt


def runpy(module_args: list[str], what: str) -> float:
    """跑本仓库的训练侧模块。

    ⚠ `cwd` 必须是 `python/`（`mahjong_ml` 装在那下面，不是仓库根）——
    用仓库根跑会得到 `No module named 'mahjong_ml'`，而那条错误看起来像"没装依赖"，很容易误诊。
    """
    return run([py(), "-m"] + module_args, what, cwd=ROOT / "python")


def selfplay(out_dir: Path, games: int, policy: str, *, seed: int, workers: int,
             hands: int = 0, sample: int = 0) -> float:
    """一次自对弈采集/评测（`--rotate` **必须开**：否则座位运气会被记成策略强弱）。

    ⚠ 策略串先在这儿**体检**（`_check_policy`）：把"名字写错"的失败挡在 JVM 启动之前 ——
    否则 `--selfplay 1500` 会在跑完 0 场后抛「未知策略名」，白等一场空（真踩过：见 `pick_opponents`）。
    """
    for spec in policy.split(","):
        _check_policy(spec)
    # 生产者可切（`MAHJONG_PRODUCER=java|cpp`）：两版**同种子逐字节等价**（docs/TRAINER-CPP.md §2）
    cmd = producers.selfplay_cmd(games, workers, policy, seed, out_dir,
                                 hands=hands, sample=sample)
    # ⚠ 必须 `return`：调用方拿它写台账（`generations.json` 的 `collect_seconds`）。
    # 漏了 return 时字段是 null —— 不会报错，只是台账里那一列永远是空的（踩过一次）。
    return run(cmd, f"自对弈 {games} 场 → {out_dir.name}（{producers.label()}）")


#: 内置策略名（与 `Policies.byName` 的 switch 一一对应 —— 加一个就两处一起加）
BUILTIN_POLICIES = ("teacher", "bot", "first", "pass", "random")


def _check_policy(spec: str) -> None:
    """策略串文法体检（`net:<路径>[@<α>][#<T>]` / 内置名）。写错就**现在**报错，别等 JVM 起来。"""
    s = spec.strip()
    if s.lower() in BUILTIN_POLICIES:
        return
    if not s.lower().startswith("net:"):
        raise SystemExit(f"策略串 '{spec}' 不是内置名（{'/'.join(BUILTIN_POLICIES)}）"
                         f"也不以 `net:` 开头 —— 是不是把网络短名当成策略串了？")
    path = s[4:]
    if "#" in path:
        path, _, temp = path.rpartition("#")
        try:
            float(temp)
        except ValueError:
            raise SystemExit(f"策略串 '{spec}' 的温度不是数：{temp!r}") from None
    if "@" in path:
        path, _, alpha = path.rpartition("@")
        try:
            float(alpha)
        except ValueError:
            raise SystemExit(f"策略串 '{spec}' 的先验权重不是数：{alpha!r}") from None
    if not path:
        raise SystemExit(f"策略串 '{spec}' 里没有权重文件路径")
    if not Path(path).is_file():
        raise SystemExit(f"策略串 '{spec}' 的权重文件不存在：{path}")


# ------------------------------------------------------------------ 世代循环


def short_of(label: str, g: int) -> str:
    return f"{label}-g{g:02d}"


def pick_opponents(args, g: int, nets: dict[str, Path], fit: league.Fit | None) -> list[str]:
    """选这一代的**对手**（见模块 docstring 的"联赛对手"）。

    - **显式给了 `--opponents`**：直接用那一串（`online screen` 选出的**跨代联合联赛**就是这么来的：
      "候选人 vs 其余三代 + teacher"），不再看联赛权重 —— 口径要可复现、可指名；
    - 第一代：`teacher` ×2（先跟老师学，避免"两只没学好的自己互啄"）；
    - 之后：按 `league.select_weights`（"谁克我就多跟它打"）抽，**老师常驻一席**；
      没有 fit（还没跑过 `ladder`）就退化成"脚本基线里确定性轮转" —— 保证**可复现**。
    """
    if getattr(args, "opponents", None):
        return [s.strip() for s in args.opponents.split(",") if s.strip()]
    me = short_of(args.label, g - 1)
    pool = ["teacher"] + list(league.SCRIPT_BASELINES) + list(nets)
    if g <= 1:
        return ["teacher", "teacher"]                     # 第一代先跟老师打（见 docstring）
    w = None
    if fit is not None:
        try:
            w = league.select_weights(fit, focus=me, floor=0.02, exponent=1.0)
        except ValueError as e:                           # focus 不在阶梯里（换过 label / 阶梯更短）
            print(f"（提示）联赛权重用不上：{e} → 退回确定性轮转")
            w = None
    if w is None:
        rng = np.random.default_rng(args.seed + 7 * g)
        # ⚠ 这里必须走 `spec_of`：池子里既有内置名（`teacher`/`random`…）也有**历史各代网络**，
        # 后者直接当策略串交给 Java 会说「未知策略名: ppo-g02」（自对弈跑 0 场就退出）。
        # 这个坑真踩过：g=2/g=3 恰好抽到脚本基线，直到 g=4 抽中上一代网络才炸。
        return ["teacher", spec_of(str(rng.choice(pool)), nets)]
    w = {n: v for n, v in w.items() if n in pool and n != me}
    if not w:
        return ["teacher", "teacher"]
    rng = np.random.default_rng(args.seed + 7 * g)
    picked = league.sample_opponents(w, 2, rng=rng)
    while len(picked) < 2:
        picked.append("teacher")
    if "teacher" not in picked:
        picked[-1] = "teacher"                            # 老师常驻：它是"没有变弱"的基准
    return [spec_of(x, nets) for x in picked]


def spec_of(name: str, nets: dict[str, Path]) -> str:
    """池子里的短名 → 策略串（网络要带上它的 `net.bin` 路径）。"""
    if name in nets:
        return f"net:{nets[name]}"
    return name


def cmd_run(args) -> int:
    paths.ensure_root()
    init = Path(args.init)
    if not init.is_file():
        raise SystemExit(f"--init 不存在：{init}")
    league_dir = paths.allocate("league", args.label)
    nets: dict[str, Path] = {}
    ckpt = init
    # ⚠ 台账**续跑时不能从头开始**：`--start-gen 4` 那一跑如果从空列表写起，会把前 3 代的行
    # 整段覆盖掉（踩过一次：第 4 代续跑后 `generations.json` 只剩一行）。所以先读回来再追加。
    log: list[dict] = []
    if (league_dir / "generations.json").is_file():
        try:
            log = json.loads((league_dir / "generations.json").read_text(encoding="utf-8"))
            log = [r for r in log if int(r.get("generation", 0)) < args.start_gen]
        except Exception as e:                          # noqa: BLE001
            print(f"（提示）台账读不出来（{e}），从空开始")
            log = []
    fit = _load_fit(league_dir / "ladder.json")
    start = max(1, args.start_gen)
    if args.target_minutes > 0:
        print(f"按时间预算跑：目标 {args.target_minutes:g} 分钟/轮"
              f"（场次由成本模型 + 资源闸门定；`--gen-games {args.gen_games}` 本轮不生效）")
    elif args.dry_run:
        raise SystemExit("--dry-run 只在 `--target-minutes > 0` 时有内容可出（场次是常数，没有计划可算）")
    if args.dry_run:
        # 只出计划：**什么都不做**（连续跑要用的权重都不导 —— dry-run 不该有副作用）
        _plan_games(args, budget.fit_cost(log), budget.fit_disk(log), start)
        print("（--dry-run：只出计划，不采不训）", flush=True)
        return 0
    if start > 1:
        # 断点续跑（`--start-gen 4 --init ckpt/ppo-g03/model.pt`）：把上一代的 net.bin 登记回池子，
        # 否则 g-1 的对手就是"不存在"（`pick_opponents` 会去 `nets` 里找它）
        prev = short_of(args.label, start - 1)
        nb = ckpt.parent / "net.bin"
        if not nb.is_file():
            runpy(["mahjong_ml.export", "weights", "--ckpt", str(ckpt), "--out", str(nb)],
                  f"导出 {prev} 的权重（续跑用）")
        nets[prev] = nb
        print(f"续跑：从第 {start} 代开始，上一代 {prev} → {nb}")
    for g in range(start, args.generations + 1):
        tag = short_of(args.label, g)
        # ⓪ **这一轮采多少场**：成本/磁盘模型**每轮重新标定**（上一轮刚写进台账；第一轮只能用默认值）
        cost, disk = budget.fit_cost(log), budget.fit_disk(log)
        games, plan = _plan_games(args, cost, disk, g)
        phases: dict[str, float] = {}
        t_round = time.perf_counter()
        # ① 采集要用的权重：第一代从 `--init` 现导（导到 `*-src`，**不动**原始 checkpoint 目录），
        #    之后直接用上一代结束时导出的 `net.bin`（它就是采集时的行为策略，π_old 靠它）
        if g == 1:
            net_bin = paths.allocate("ckpt", f"{tag}-src") / "net.bin"
            phases["export"] = runpy(["mahjong_ml.export", "weights", "--ckpt", str(ckpt),
                                      "--out", str(net_bin)], "导出采集权重（--init）")
        else:
            net_bin = nets[short_of(args.label, g - 1)]
        # ② 联赛选对手 → 采集（**两席自己 + 两席对手**，`--rotate` 会轮座位）
        opps = pick_opponents(args, g, nets, fit)
        spec = f"net:{net_bin}#{args.temp}"
        seats = [spec] * int(getattr(args, "student_seats", 2) or 2) + opps
        if len(seats) != 4:
            raise SystemExit(f"这一桌是 {len(seats)} 席（学生 {args.student_seats} 席 + 对手 "
                             f"{len(opps)} 席）—— 必须正好 4 席：`--student-seats` 与 `--opponents` 对不上")
        # ⚠ **预扣空间**：把这一轮的预计字节数交给配额闸门 ⇒ 回收发生在**写之前**
        #   （不传就是"写完超了、下次分配才发现"，2026-09-27 把 compact 顶到 92/116 GB 就是这么来的）
        raw = paths.allocate("raw", tag, need_bytes=int(disk.raw_bytes_per_game * games))
        phases["collect"] = selfplay(raw, games, ",".join(seats),
                                     seed=args.seed + g * 1000, workers=args.workers,
                                     hands=args.hands, sample=args.sample)
        # ③ 派生特征（服务端算）+ 紧凑集（Python 打包）；生产者与采集同源（MAHJONG_PRODUCER）
        phases["features"] = run(producers.features_cmd(raw, args.workers), "派生特征")
        comp = paths.allocate("compact", tag, need_bytes=int(disk.compact_bytes_per_game * games))
        # ⚠ `--student` 必须给**这一轮学生的确切策略串**：`is_student` 的旧口径是"任何 `net:`"，
        #   跨代对局里对手也是网络 ⇒ 它们的决策会被算进**策略损失**（off-policy 污染、且静默）。
        #   实测：候选人 1 席 + 对手 g05/g07 两席 ⇒ 学生行占比 0.75 而不是 0.25（2026-09-27）。
        phases["compact"] = runpy(["mahjong_ml.dataset", "build", str(raw), str(comp),
                                   "--workers", str(args.compact_workers or args.workers),
                                   "--student", spec], "建紧凑集")
        # ④ PPO 一代
        new_ckpt_dir = paths.allocate("ckpt", tag)
        ppo_cmd = ["mahjong_ml.ppo", "--data", str(comp), "--init", str(ckpt),
                   "--label", tag, "--temp", str(args.temp), "--epochs", str(args.epochs),
                   "--lr", str(args.lr), "--batch", str(args.batch), "--clip", str(args.clip),
                   "--ent-coef", str(args.ent_coef), "--max-kl", str(args.max_kl),
                   "--rank-weight", str(args.rank_weight), "--threads", str(args.threads),
                   "--seed", str(args.seed + g)]
        if g == start and args.init_value:
            # **这一跑的第一代**才做价值头冷启动；之后每一代的 checkpoint 里都带着 value（`--init` 继承）
            # ⚠ 原来写的是 `g == 1`：`--start-gen 2` 续跑时 g 从 2 起 ⇒ 冷启动**被静默忽略**
            #   （2026-09-26 实测踩到：`--start-gen 2 --init-value <iql>` 跑完价值头还是上一代的）
            ppo_cmd += ["--init-value", str(args.init_value)]
        phases["ppo"] = runpy(ppo_cmd, f"PPO 第 {g} 代")
        ckpt = new_ckpt_dir / "model.pt"
        # ⑤ 把这一代导出成 Java 能读的权重（下一代的采集、以及 ladder 都要用它）
        phases["export"] = phases.get("export", 0.0) + runpy(
            ["mahjong_ml.export", "weights", "--ckpt", str(ckpt),
             "--out", str(new_ckpt_dir / "net.bin")], f"导出 {tag} 的权重")
        nets[tag] = new_ckpt_dir / "net.bin"
        total = time.perf_counter() - t_round
        if plan is not None:
            # 目标 vs 实测**必须打出来**：时间预算的价值全在这条偏差上（下一轮的 per_game 靠它收敛）
            d = total - args.target_minutes * 60.0
            print(f"\n⏱ 第 {g} 轮实测 {total/60:.1f} 分钟（目标 {args.target_minutes:g} 分钟，"
                  f"偏差 {d/60:+.1f}）："
                  + " · ".join(f"{k} {phases[k]/60:.1f}" for k in budget.PHASES if k in phases)
                  + " 分钟", flush=True)
        log.append({"generation": g, "tag": tag, "opponents": opps, "raw": str(raw),
                    "compact": str(comp), "ckpt": str(ckpt),
                    "collect_seconds": phases["collect"], "games": games, "temp": args.temp,
                    # ⏱ 时间预算的**标定素材**：分相时长 + 总时长 + 落盘字节（下一轮拿它们回填成本模型）
                    "phases": {k: phases[k] for k in budget.PHASES if k in phases},
                    "total_seconds": total,
                    "raw_bytes": paths.dir_size_bytes(raw),
                    "compact_bytes": paths.dir_size_bytes(comp),
                    "target_seconds": (args.target_minutes * 60.0) if plan is not None else None,
                    "planned_games": (plan.games if plan is not None else None),
                    "clamped_by": (plan.clamped_by if plan is not None else None)})
        (league_dir / "generations.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{args.generations} 代跑完；台账：{league_dir / 'generations.json'}")
    print("下一步：`python -m mahjong_ml.online ladder --label " + args.label
          + " --generations " + str(args.generations) + "` 拿 Elo 与配对 CI（判据在这里，不在训练日志里）")
    return 0


def _plan_games(args, cost: budget.Cost, disk: budget.Disk, g: int) -> tuple[int, budget.Plan | None]:
    """这一轮采多少场：`--target-minutes > 0` 按时间预算反推 + 资源闸门夹逼；否则就是 `--gen-games`。

    ⚠ 报告里必须写清"**是谁限制了这一轮**"：否则"目标 30 分钟却只跑了 12 分钟"看起来像 bug，
    而真正的原因是配额余量（这一条是**可操作的结论** —— 清理/抬配额 vs 改目标）。
    """
    if args.target_minutes <= 0:
        return args.gen_games, None
    target = args.target_minutes * 60.0
    try:
        plan = budget.plan_round(target, cost,
                                budget.gates_now(disk, cache_gb=args.cache_gb),
                                min_games=args.min_games, max_games=args.max_games)
    except budget.ResourceError as e:
        raise SystemExit(f"{e}\n（先清理数据根或抬配额；只想要个小轮就显式用 `--gen-games`）") from None
    print(budget.format_plan(plan, cost, target, generation=g), flush=True)
    return plan.games, plan


def _load_fit(path: Path) -> league.Fit | None:
    """上一代的阶梯报告 → `league.Fit`（没有/读不出就返回 None = 这一代退回确定性轮转）。

    ⚠ 阶梯是**独立的一次评测**，所以"这一代还没有 fit"是正常状态（第一代必然如此）。
    """
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return league.Fit(theta={k: float(v) for k, v in raw["theta"].items()},
                          se={k: float(v) for k, v in raw["se"].items()},
                          games=int(raw["games"]), players=list(raw["players"]))
    except Exception as e:                              # noqa: BLE001
        print(f"（提示）{path.name} 读不出来（{e}），这一代按「无 fit」处理")
        return None


# ------------------------------------------------------------------ 联赛阶梯


def paired_report(run_dir: Path, a: str, b: str, metric: str = "rank_points") -> dict:
    """一次 run 里 A/B 的**逐场配对检验**（复用 `eval.py` 的实现，绝不另写一套统计）。

    为什么要在编排里再算一遍（而不是只让 `eval.py` 打印）：判据要**落成机器可读的证据**。
    `eval.py --json` 写的是 per-run 汇总（`by_policy` / `per_game`），**不含**配对结果 ——
    所以这里用它的公开函数算完再存进 `league/<label>/pairs.json`。
    """
    from . import eval as ml_eval
    run = ml_eval.load_run(run_dir)
    la, lb = ml_eval.resolve_label(run, a), ml_eval.resolve_label(run, b)
    p = ml_eval.paired_test(ml_eval.per_game_series(run, la, metric),
                            ml_eval.per_game_series(run, lb, metric), la, lb, metric)
    return {"run": str(run_dir), "a": la, "b": lb, "metric": metric, "n": p.n,
            "delta": p.mean, "sd": p.sd, "ci": [float(p.ci[0]), float(p.ci[1])], "p": p.p,
            "win": p.win, "lose": p.lose, "tie": p.tie,
            "required_n_delta2": ml_eval.required_n(p.sd, 2.0)}


def paired_report_multi(run_dirs: list[Path], a: str, b: str, metric: str = "rank_points") -> dict:
    """把**多个 run** 的逐场配对结果并成一个（用于"相邻 + 对家"两种座位配置一起判）。

    实现复用 `eval.py`：逐场序列**按 seed 索引**（跨 run 不撞车，前提是两个 run 用不同的 seed 基），
    拼接后只调用一次 `paired_test` —— 统计口径与单 run 完全一致，只是样本量翻倍、
    且**两种座位配置都被覆盖**。
    """
    from . import eval as ml_eval
    sa: dict[int, float] = {}
    sb: dict[int, float] = {}
    for d in run_dirs:
        run = ml_eval.load_run(d)
        la, lb = ml_eval.resolve_label(run, a), ml_eval.resolve_label(run, b)
        sa.update(ml_eval.per_game_series(run, la, metric))
        sb.update(ml_eval.per_game_series(run, lb, metric))
    p = ml_eval.paired_test(sa, sb, a, b, metric)
    return {"runs": [str(d) for d in run_dirs], "a": a, "b": b, "metric": metric, "n": p.n,
            "delta": p.mean, "sd": p.sd, "ci": [float(p.ci[0]), float(p.ci[1])], "p": p.p,
            "win": p.win, "lose": p.lose, "tie": p.tie,
            "required_n_delta2": ml_eval.required_n(p.sd, 2.0)}


def _arrangement_specs(a: str, b: str) -> list[tuple[str, str, str]]:
    """2+2 的**两种座位配置**（`--rotate` 下 `A,A,B,B` 与 `A,B,A,B` 占的座位完全不同）：

    | 写法 | `--rotate` 下 A（前者）占的座位 | 关系 |
    | --- | --- | --- |
    | `A,A,B,B` | {0,1} {0,3} {1,2} {2,3} | A 的两席**相邻**（上下家） |
    | `A,B,A,B` | {0,2} {1,3} | A 的两席**对家** |

    ⚠ 为什么必须两组都跑：轮转公式是 `src = (seat + game) % 4`，它**保持策略表的块结构** ——
    所以 `A,A,B,B` 永远只采样相邻对，**从不会出现对家配置**；反过来 `A,B,A,B` 也只有对家。
    只跑其中一组，等于把"相邻两席之间的系统性效应（互相喂牌/一炮双响/同巡同判断）"或
    "对家之间的效应"留成未采样偏差。
    """
    return [("相邻", f"{a},{a},{b},{b}", "pair"),
            ("对家", f"{a},{b},{a},{b}", "pairX")]


def cmd_ladder(args) -> int:
    paths.ensure_root()
    league_dir = paths.allocate("league", args.label)
    entries: list[tuple[str, str]] = []
    for g in range(1, args.generations + 1):
        tag = short_of(args.label, g)
        nb = paths.DATA_ROOT / "ckpt" / tag / "net.bin"
        if not nb.is_file():
            raise SystemExit(f"缺 {nb}（先跑 online run；或 --generations 传小一点）")
        entries.append((tag, f"net:{nb}"))
    runs = []
    for i, (name, spec) in enumerate(entries):
        out = paths.allocate("raw", f"{args.label}-ladder-{name}")
        selfplay(out, args.games, f"{spec},teacher,first,random",
                 seed=args.seed + 100000 + i * 1000, workers=args.workers, sample=args.sample)
        runs.append(out)
    # 判据①（Elo）用上面那批；判据②（顺位点配对）另跑 **2+2** —— 一席自己一席老师时
    # sd(Δ)≈80，同样场次的精度差一倍（实测 60 场），所以主判据不在 ladder run 里读。
    # ⚠ 2+2 要跑**两种座位配置**（相邻 `A,A,B,B` + 对家 `A,B,A,B`）：轮转公式保持策略表的块结构，
    #    单跑一组只会采样到一种关系（见 `_arrangement_specs`）。两组用**不同的 seed 基**（互不撞车、
    #    是独立样本），最后再合成一个总 Δ。
    pair_runs = []
    pairs = []
    if args.pair_games > 0:
        for i, (name, spec) in enumerate(entries):
            per_arr = []
            for k, (arr_name, spec_str, tag2) in enumerate(_arrangement_specs(spec, "teacher")):
                out = paths.allocate("raw", f"{args.label}-{tag2}-{name}")
                selfplay(out, args.pair_games, spec_str,
                         seed=args.seed + 200000 + k * 100000 + i * 1000,
                         workers=args.workers, sample=args.sample)
                pair_runs.append(out)
                runpy(["mahjong_ml.eval", str(out), "--labels", f"{name},teacher",
                       "--json", str(league_dir / f"{tag2}-{name}.json")],
                      f"配对评测 {name} vs teacher（{arr_name}座位）")
                pr = paired_report(out, name, "teacher")
                pr["arrangement"] = arr_name
                pr["tag"] = tag2
                per_arr.append(pr)
                print(f"  ⇒ {name} − teacher（**{arr_name}**座位）：Δ={pr['delta']:+.2f} 顺位点，"
                      f"95%CI [{pr['ci'][0]:+.2f}, {pr['ci'][1]:+.2f}]，p={pr['p']:.3f}，"
                      f"胜/负/平 {pr['win']}/{pr['lose']}/{pr['tie']}，sd={pr['sd']:.2f}"
                      f"（检出 Δ=2 需 {pr['required_n_delta2']} 场）")
            pr = paired_report_multi([Path(p["run"]) for p in per_arr], name, "teacher")
            pr["per_arrangement"] = per_arr
            pr["arrangements"] = [p["arrangement"] for p in per_arr]
            pairs.append(pr)
            print(f"  ⇒ {name} − teacher（**两种座位配置合计** n={pr['n']}）：Δ={pr['delta']:+.2f} "
                  f"顺位点，95%CI [{pr['ci'][0]:+.2f}, {pr['ci'][1]:+.2f}]，p={pr['p']:.3f}"
                  f"（检出 Δ=2 需 {pr['required_n_delta2']} 场）")
        (league_dir / "pairs.json").write_text(
            json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8")
    games = league.load_games(runs)
    fit = league.plackett_luce(games)
    ci = league.pl_ci(games, n_boot=args.boot, seed=args.seed)
    print("\n" + league.format_ladder(fit, anchor="teacher"))
    report = {"label": args.label, "games": len(games), "runs": [str(r) for r in runs],
              "pair_runs": [str(r) for r in pair_runs], "pairs": pairs,
              "players": list(fit.players), "theta": fit.theta, "se": fit.se,
              "ci": {k: list(v) for k, v in ci.items()}, "n_boot": args.boot}
    (league_dir / "ladder.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    print(f"\n阶梯报告：{league_dir / 'ladder.json'}")
    print("配对 CI（逐场、按 seed）另用：python -m mahjong_ml.eval "
          + " ".join(str(r) for r in runs) + " --labels <A>,<B>")
    return 0


# ------------------------------------------------------------------ 策略位移（"每代到底动了多少"）


def agreement_matrix(preds: dict[str, np.ndarray]) -> tuple[list[str], np.ndarray]:
    """两两一致率矩阵（**纯函数**，自检直接喂合成数组）。

    `M[i][j] = P(argmax_i == argmax_j)`。为什么先看这个而不是直接看 Elo：
    **Elo 的分辨率是有限的**（1200 场/代下约 ±4 顺位点）—— 如果一代只改了 2% 的决策，
    Elo 必然"看不出趋势"，那不是跑得不够久，而是**步长太小**。先量位移，再决定跑几代。
    """
    names = list(preds)
    n = len(names)
    m = np.ones((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(n):
            a, b = preds[names[i]], preds[names[j]]
            m[i, j] = float((a == b).mean()) if a.size else float("nan")
    return names, m


def resolve_spec(name: str) -> str:
    """名字或路径 → 策略串。`teacher`/`first`/`pass`/`random` 原样；`ppo-g04` → `net:ckpt/ppo-g04/net.bin`；
    已经是 `net:` 串或一个存在的 `net.bin` 路径就直接用。"""
    s = name.strip()
    if s.lower() in BUILTIN_POLICIES or s.lower().startswith("net:"):
        _check_policy(s)
        return s
    p = Path(s)
    if not p.is_file():
        p = paths.DATA_ROOT / "ckpt" / s / "net.bin"
    if not p.is_file():
        raise SystemExit(f"找不到策略 {name!r}：既不是内置名，也没有 {p}")
    spec = f"net:{p}"
    _check_policy(spec)
    return spec


def cmd_pair(args) -> int:
    """**两个策略的 2+2 同牌山配对**（P3/P5b 的判据协议，通用化）：

        python -m mahjong_ml.online pair --a ppo2-g04 --b teacher --games 1500
        python -m mahjong_ml.online pair --a ppo2-g04 --b ppo-g04  --games 1500

    ⚠ 为什么是 **2+2** 而不是一席对一席：实测 `sd(Δ)≈53` vs `≈80`（同场次精度差一倍），
    而"这一代到底比上一代强没有"这种小效应必须用精度高的那个协议。
    """
    a, b = resolve_spec(args.a), resolve_spec(args.b)
    tag = args.tag or f"{_short_of_spec(a)}-vs-{_short_of_spec(b)}"
    na, nb = _short_of_spec(a), _short_of_spec(b)
    per_arr = []
    total_s = 0.0
    for k, (arr_name, spec_str, tag2) in enumerate(_arrangement_specs(a, b)):
        out = paths.allocate("raw", f"{tag}-{tag2}")
        print(f"配对（**{arr_name}**座位）：{na} ×2 vs {nb} ×2 —— {spec_str}（{args.games} 场，"
              f"seed={args.seed + k * 100000}）")
        t = selfplay(out, args.games, spec_str, seed=args.seed + k * 100000,
                     workers=args.workers, sample=args.sample)
        total_s += t
        pr = paired_report(out, na, nb)
        pr["arrangement"] = arr_name
        pr["tag"] = tag2
        pr["games"] = args.games
        pr["seconds"] = t
        pr["raw"] = str(out)
        per_arr.append(pr)
        print(f"  Δ = {pr['delta']:+.2f} 顺位点（正 = 前者更好）｜95%CI "
              f"[{pr['ci'][0]:+.2f}, {pr['ci'][1]:+.2f}]｜p={pr['p']:.3f}｜"
              f"胜/负/平 {pr['win']}/{pr['lose']}/{pr['tie']}｜sd={pr['sd']:.2f}｜"
              f"检出 Δ=2 需 {pr['required_n_delta2']} 场｜{t:.0f}s")
    combined = paired_report_multi([Path(p["raw"]) for p in per_arr], na, nb)
    combined["per_arrangement"] = per_arr
    combined["arrangements"] = [p["arrangement"] for p in per_arr]
    combined["games"] = args.games
    combined["seconds"] = total_s
    print(f"\n  **两种座位配置合计**（n={combined['n']}，{total_s:.0f}s）："
          f"Δ = {combined['delta']:+.2f} 顺位点｜95%CI "
          f"[{combined['ci'][0]:+.2f}, {combined['ci'][1]:+.2f}]｜p={combined['p']:.3f}｜"
          f"检出 Δ=2 需 {combined['required_n_delta2']} 场")
    dest = Path(args.json) if args.json else (paths.allocate("league", args.label) / f"pair-{tag}.json")
    dest.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已写出 {dest}")
    return 0


def _short_of_spec(spec: str) -> str:
    """策略串 → 阶梯/报告里用的短名（与 `league.short_name` 同源，免得两处不一致）。"""
    return league.short_name(spec)


def cmd_displace(args) -> int:
    """量"各代网络在固定数据集上的贪心动作一致率"（P4 的**步长体检**）。

        python -m mahjong_ml.online displace --data <紧凑集> --label ppo2 --generations 4
        python -m mahjong_ml.online displace --data <紧凑集> --ckpts a.pt,b.pt

    ⚠ 参考数据集要选**大家都见过的同一批状态**：用最新一代的紧凑集 val 切分即可
    （或 P3 的 `rl-001`，如果没被配额淘汰 —— 见 `paths.QUOTA_GB`）。
    """
    from . import bc, dataset as ds, nets
    if args.ckpts:
        items = []
        for spec in args.ckpts.split(","):
            spec = spec.strip()
            if not spec:
                continue
            p = Path(spec)
            items.append((p.parent.name or p.stem, p))
    else:
        items = []
        for g in range(1, args.generations + 1):
            tag = short_of(args.label, g)
            items.append((tag, paths.DATA_ROOT / "ckpt" / tag / "model.pt"))
    for name, p in items:
        if not p.is_file():
            raise SystemExit(f"缺 checkpoint：{p}（{name}）")

    split = ds.load_split(args.data, args.split)
    # ⚠ `np.shape` 而不是 `np.asarray(...).shape`：后者会把内存映射的 `state` 整块读进 RAM（见 `rewards.py`）
    n = int(np.shape(split["state"])[0])
    if n == 0:
        raise SystemExit(f"{args.data} 的 {args.split} 切分为空")
    rng = np.random.default_rng(args.seed)
    idx = np.sort(rng.choice(n, size=min(args.rows, n), replace=False))
    state, cand, mask, label = bc._batch(split, idx, "cpu")

    preds: dict[str, np.ndarray] = {}
    for name, p in items:
        ck = torch.load(p, map_location="cpu", weights_only=False)
        cfg = ck["config"]
        model = nets.build(cfg["state_dim"], cfg["cand_dim"], hidden=cfg["hidden"], head=cfg["head"])
        model.load_state_dict(ck["model"])
        model.eval()
        with torch.no_grad():
            preds[name] = model(state, cand, mask).argmax(dim=1).numpy()

    names, m = agreement_matrix(preds)
    lab = label.numpy()
    print(f"\n策略位移：{args.data} 的 {args.split} 切分抽 {len(idx)} 条（seed={args.seed}）")
    print("两两贪心一致率：")
    print("  " + " " * 14 + "".join(f"{x[-6:]:>10}" for x in names) + "   与数据动作")
    for i, a in enumerate(names):
        row = "".join(f"{m[i, j]:>10.4f}" for j in range(len(names)))
        print(f"  {a:<12}{row}{float((preds[a] == lab).mean()):>12.4f}")
    if len(names) >= 2:
        drift = 1.0 - m[0, -1]
        step = [1.0 - m[k, k + 1] for k in range(len(names) - 1)]
        print(f"  逐代位移：{' / '.join(f'{s*100:.2f}%' for s in step)}"
              f"；首→末累计 **{drift*100:.2f}%**")
        print("  读法：累计位移 < 5% 时别指望 Elo 能分辨（1200 场/代约 ±4 顺位点）——"
              "先调步长（lr / epoch），不是加代数。")
    out = {"data": str(args.data), "split": args.split, "rows": int(len(idx)),
           "names": names, "matrix": m.tolist(),
           "data_agreement": {a: float((preds[a] == lab).mean()) for a in names}}
    dest = Path(args.json) if args.json else (paths.allocate("league", args.label) / "displace.json")
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已写出 {dest}")
    return 0


def screen_table(runs: list, label_of: dict[str, str]) -> list[dict]:
    """多批 selfplay 的 `eval.Run` → **每策略一行**，按**平均得点**降序。

    · `avg_delta` = 引擎的账（`by_policy[*]`，**每一小局**的收支）；`score` = 由 `final_scores − 25000`
      逐场算的**整场得点**（两者差一个"每场小局数"，实测 11.1–11.7）—— 交叉核对按 `avg_delta × 每场小局数`
      比，容差 5%（不限死 0：小局数在批之间会飘）。
    · 排序用**整场得点 `score`**（用户口径"平均得点"）；顺位点/和率/放铳率/打点同表给出，用来交叉核对。
    """
    import mahjong_ml.eval as _ev
    rows: dict[str, dict] = {}
    for run in runs:
        hpg = (run.hands / run.games) if run.games else 0.0
        for spec, st in (run.by_policy or {}).items():
            g = int(st.get("games", 0))
            r = rows.setdefault(spec, {"spec": spec, "name": label_of.get(spec, spec),
                                       "seat_games": 0, "avg_delta": 0.0, "rank_points": 0.0,
                                       "win_rate": 0.0, "deal_in_rate": 0.0, "avg_win_score": 0.0,
                                       "hands_per_game": 0.0, "score": 0.0, "n_score": 0})
            r["seat_games"] += g
            # ⚠ 键名是 `by_policy` 的**原名**（`avg_rank_points` / `avg_delta` / `avg_win_score`）——
            #   写错不会报错，只会静默变成 0（2026-09-27 真踩过：顺位点整列都是 +0.00）
            for key, src in (("avg_delta", "avg_delta"), ("rank_points", "avg_rank_points"),
                             ("win_rate", "win_rate"), ("deal_in_rate", "deal_in_rate"),
                             ("avg_win_score", "avg_win_score")):
                r[key] += float(st.get(src, 0.0)) * g
            r["hands_per_game"] += hpg * g
            vals = list(_ev.per_game_series(run, spec, "score").values())
            r["score"] += sum(vals)
            r["n_score"] += len(vals)
    out = []
    for r in rows.values():
        g = max(1, r["seat_games"])
        row = {k: (r[k] / g) for k in
               ("avg_delta", "rank_points", "win_rate", "deal_in_rate", "avg_win_score",
                "hands_per_game")}
        row.update(spec=r["spec"], name=r["name"], seat_games=r["seat_games"],
                   score=(r["score"] / r["n_score"] if r["n_score"] else 0.0))
        expect = row["avg_delta"] * row["hands_per_game"]
        # 容差：绝对下限 60 点 + 相对 5%。⚠ **必须有绝对下限**：引擎的"逐小局账"（`avg_delta`）
        # 与局末 `final_scores` 的差是一个**恒定小量**（实测 24 行：+12…+35 点/场，≈1.5 点/小局 ——
        # 流局/供託那种局末记账），它在**均值接近 0** 时会把相对误差放大到 >30%（实测 66 vs 88），
        # 于是"两本账"看起来像坏了。这个下限仍然能抓住真正的口径事故（把"每小局"当"整场"用会差 11 倍）。
        if abs(expect - row["score"]) > max(60.0, 0.05 * abs(row["score"])):
            raise SystemExit(f"{row['name']}：引擎账 avg_delta={row['avg_delta']:.1f}/小局 × "
                             f"{row['hands_per_game']:.2f} 小局 = {expect:.0f}，与逐场 final_scores 算出的 "
                             f"{row['score']:.0f} 相差 {abs(expect - row['score']):.0f}（>60 点且 >5%）"
                             f"—— 报文口径变了？")
        out.append(row)
    return sorted(out, key=lambda r: -r["score"])


def cmd_screen(args) -> int:
    """**跨代际小批量筛选**：让若干代网络 + teacher 同坐一张桌子，按**平均得点**选下一步的候选人。

    口径（2026-09-27 用户指定）：

    · **座位平均**：`--rotate`（自对弈脚本强制）让每个策略在 4 个座位上**各坐 1/4 的场次** ——
      这是**严格的座位平均**，比"每局随机坐"更强：随机只在期望上平衡，还会多一层座位噪声；
      多批换 seed 拿到的是**独立样本**，不是靠座位随机化去抵消偏差。
    · **小批量**：每批 `--games`（缺省 400）+ `--sample 64`（评测不训练，省盘）；`--batches` 批汇总。
    · **排序指标 = 平均得点**（`score` = `final_scores − 25000`）；同时打印顺位点/和率/放铳率/打点。
    · **判据不留情面**：用 `eval.pool_diffs` 做**逐场配对**（同一副牌山）的"第一名 − 其他人"CI；
      CI 含 0 就说"**小批量证不出差别**"，只把它当"候选人"而不是"已证明更强"。
    · 可选 `--value-data <紧凑集>`：对每个**网络**权重在**同一份** val 切分上做**价值头校准**
      （MAE / 逐点 ρ / 小局 ρ）—— "跨代际价值检验"的第二个口径（同一把尺子）。
    """
    from . import dataset as ds
    from . import eval as ev
    from . import nets as netmod
    from . import ppo as ppomod
    from . import rewards as rw

    specs = [resolve_spec(p.strip()) for p in args.policies.split(",") if p.strip()]
    if len(specs) != 4:
        raise SystemExit(f"跨代际筛选要**正好 4 个策略**（一张桌子坐满），现在是 {len(specs)}：{specs}")
    label_of = {spec: short for spec, short in zip(specs, [p.strip() for p in args.policies.split(",")])}
    league_dir = paths.allocate("league", args.label)
    runs = []
    for k in range(1, args.batches + 1):
        d = paths.allocate("probe", f"screen-{k}")
        selfplay(d, args.games, ",".join(specs), seed=args.seed + 1000 * k,
                 workers=args.workers, sample=args.sample)
        run = ev.load_run(d)
        # 留档**完整** summary（`eval.load_run` 既能吃目录也能吃这个文件 ⇒ 以后不用重跑就能重算表），
        # 再清掉轨迹（probe 本来就是"用完即删"）
        (league_dir / f"screen-b{k}.json").write_text(
            (d / "summary.json").read_text(encoding="utf-8"), encoding="utf-8")
        runs.append(run)
        _rmtree(d)
    table = screen_table(runs, label_of)
    print(f"\n== 跨代际筛选（{args.batches} 批 × {args.games} 场 = {args.batches * args.games} 场 · "
          f"4 席满桌 · `--rotate` ⇒ 每策略每座位各 {args.batches * args.games // 4} 场）==")
    print(f"{'策略':<10}{'平均得点':>10}{'每小局':>9}{'顺位点':>9}{'和了率':>8}{'放铳率':>8}{'平均打点':>10}")
    for r in table:
        print(f"{r['name']:<10}{r['score']:>+10.1f}{r['avg_delta']:>+9.1f}{r['rank_points']:>+9.2f}"
              f"{r['win_rate']:>8.1%}{r['deal_in_rate']:>8.1%}"
              f"{r['avg_win_score']:>10.0f}")
    top = table[0]
    print(f"\n⇒ **平均得点最高：{top['name']}**（{top['score']:+.1f} 千点/场·席）—— 作为进一步训练的候选人")
    for other in table[1:]:
        pooled, per_run = ev.pool_diffs(runs, top["spec"], other["spec"], "score")
        n = int(pooled.size)
        mean = float(pooled.mean())
        sd = float(pooled.std(ddof=1)) if n > 1 else 0.0
        lo, hi = ev.bootstrap_ci(pooled) if n > 1 else (mean, mean)
        p = ev.sign_test_p(pooled)
        need = ev.required_n(sd, max(1.0, abs(mean))) if sd > 0 else 0
        verdict = "CI 排除 0（显著）" if (lo > 0 or hi < 0) else "**小批量证不出差别**"
        print(f"   {top['name']} − {other['name']}：逐场配对 Δ={mean:+.1f} 千点 "
              f"95%CI=[{lo:+.1f},{hi:+.1f}] p={p:.3f} n={n} sd={sd:.1f}（检出同量级 Δ 需 ≈{need} 场）"
              f" ⇒ {verdict}")
    ckpt_dir = Path(args.ckpt_dir) if args.ckpt_dir else paths.DATA_ROOT / "ckpt"
    if args.value_data:
        # **价值头校准**（第二个口径）：所有网络在**同一份** val 切分、同一把尺子上比
        split = ds.load_split(args.value_data, "val")
        tr = rw.transitions(split, rank_weight=args.rank_weight)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"\n== 价值头校准（同一份 val 切分：{args.value_data}，{len(tr['idx'])} 条）==")
        print(f"{'策略':<10}{'MAE(千点)':>12}{'常数基线':>10}{'逐点 ρ':>9}{'小局 ρ':>9}")
        for spec, short in label_of.items():
            ck = ckpt_dir / short / "model.pt"
            if not ck.is_file():
                print(f"{short:<10}{'（没有 ckpt，跳过：teacher/非本仓权重）':>40}")
                continue
            c = torch.load(ck, map_location="cpu", weights_only=False)
            cfg = c["config"]
            # ⚠ 必须 `.to(device)`：`value_report` 把 state 送到 device，权重留在 CPU 会报
            #   "Expected all tensors to be on the same device"（2026-09-27 真踩过）
            v = netmod.build_value(features.state_dim(), hidden=cfg["hidden"], head=cfg["head"]).to(device)
            v.load_state_dict(c["value"])
            rep = ppomod.value_report(v, split, tr, device, batch=args.batch)
            print(f"{short:<10}{rep['mae']:>12.4f}{rep['const_mae']:>10.4f}"
                  f"{rep['pearson']:>+9.3f}{rep['ep_pearson']:>+9.3f}")
        print("读法：MAE 低于常数基线、ρ 越高 = 价值头越可用（与 P3 的 critic 同一把尺子）。")
    print(f"\n落档：{league_dir}/screen-b*.json")
    return 0


def _rmtree(d: Path) -> None:
    """删掉一次评测的轨迹目录（probe 是"用完即删"的：它没有配额，但也不该长住）。"""
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4：世代循环 + 联赛阶梯")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="跑 N 代（采集→特征→紧凑→PPO→导出）")
    r.add_argument("--init", required=True, help="起始 checkpoint（P4 必须从 BC/AWR 初始化）")
    r.add_argument("--init-value", default=None,
                   help="**这一跑的第一代**（`--start-gen` 那一代）的价值头冷启动："
                        "P3 的 IQL critic（`ckpt/iql-00N/model.pt`）；续跑时同样生效")
    r.add_argument("--label", default="ppo")
    r.add_argument("--generations", type=int, default=4,
                   help="**跑到第几代（含）**，不是「再跑几代」—— 实现是 `range(start_gen, generations+1)`；"
                        "例：只跑第 2 代 = `--start-gen 2 --generations 2`")
    r.add_argument("--start-gen", type=int, default=1,
                   help="从第几代开始（断点续跑：`--start-gen 4 --init <第 3 代的 model.pt>`）")
    r.add_argument("--gen-games", type=int, default=2000,
                   help="每代采集场次（写 raw/，约 1.1 MB/场）—— ⚠ **只在 `--target-minutes 0` 时生效**")
    r.add_argument("--target-minutes", type=float, default=20.0,
                   help="**一轮的目标时长（分钟；缺省 20 = 用户 2026-09-27 指定的口径，0 = 关）**："
                        "场次由成本和磁盘模型从台账实测回填后反推（实测点上的分段线性），"
                        "再被资源闸门夹逼（raw/compact/总配额余量、盘余量、--max-games），"
                        "报告里写清是谁限制的。见 `python/mahjong_ml/budget.py`")
    r.add_argument("--min-games", type=int, default=400,
                   help="时间预算下**允许的最小轮次场次**（资源供不起就报错，不静默缩小轮次）")
    r.add_argument("--max-games", type=int, default=16000,
                   help="时间预算下的场次硬上限（**RAM 兜底**：本机 32 GB 实测 14700 场的一轮走通，"
                        "`compact` 并行时物理内存一度只剩 ~1 GB ⇒ 想再大先加固内存或调小 --compact-workers）")
    r.add_argument("--compact-workers", type=int, default=0,
                   help="建紧凑集的并行度（0 = 跟 --workers 一样）。⚠ **这是内存旋钮**：`dataset build` 的"
                        "子进程各持一份 chunk，worker 越多峰值越高（实测 24 worker × 14700 场把可用内存打到 ~1 GB）")
    r.add_argument("--cache-gb", type=float, default=0.0,
                   help="一轮允许占用的**页缓存**（GB）—— **只跑缓存档**的硬闸门（缺省 0 = 自动取 "
                        "0.8 × 物理内存；`-1` = 关掉它，故意跑磁盘档时才用）。"
                        "⚠ 越过它一轮会从 ≈12 分钟掉到 ≈45+ 分钟（PPO 要把紧凑集读 5 遍，S 盘 ~120 MB/s）")
    r.add_argument("--dry-run", action="store_true",
                   help="只按当前余量算出这一轮的场次并打印计划，不采不训（配 `--target-minutes`）")
    r.add_argument("--opponents", default=None,
                   help="**显式指定对手**（逗号分隔的策略串），不再走联赛采样 —— 跨代联合对局用它，"
                        "例：`--opponents teacher,net:<g06>/net.bin,net:<g07>/net.bin`")
    r.add_argument("--student-seats", type=int, default=2,
                   help="学生占几席（缺省 2；与 `--opponents` 的席数相加必须正好 4）。"
                        "对手正好 3 个时用 `--student-seats 1`（候选人 1 席 vs 其余三代 + teacher 3 席）")
    r.add_argument("--temp", type=float, default=1.0, help="行为策略温度（`#<T>`，必须与 ppo --temp 一致）")
    r.add_argument("--epochs", type=int, default=4)
    r.add_argument("--lr", type=float, default=1e-4)
    r.add_argument("--batch", type=int, default=4096)
    r.add_argument("--clip", type=float, default=0.2)
    r.add_argument("--ent-coef", type=float, default=0.01)
    r.add_argument("--max-kl", type=float, default=0.03)
    r.add_argument("--rank-weight", type=float, default=1.0)
    r.add_argument("--workers", type=int, default=24)
    r.add_argument("--threads", type=int, default=4)
    r.add_argument("--seed", type=int, default=701001)
    r.add_argument("--hands", type=int, default=0, help="每场最多 n 小局（0 = 完整半庄；冒烟用）")
    r.add_argument("--sample", type=int, default=0,
                   help="每 k 次决策记 1 条（0 = 全记 —— **训练要全记**，只省盘不省时间）")

    l = sub.add_parser("ladder", help="每代一跑 net:gNN,teacher,first,random → Elo + 配对")
    l.add_argument("--label", default="ppo")
    l.add_argument("--generations", type=int, default=4)
    l.add_argument("--games", type=int, default=1200, help="每代跑多少场")
    l.add_argument("--pair-games", type=int, default=800,
                   help="每代另跑的 **2+2 配对**场次（net×2 vs teacher×2，判据②用它；0 = 跳过）")
    l.add_argument("--boot", type=int, default=200)
    l.add_argument("--workers", type=int, default=24)
    l.add_argument("--seed", type=int, default=701001)
    l.add_argument("--sample", type=int, default=64, help="评测用采样（省盘；不省时间）")

    d = sub.add_parser("displace", help="量各代网络的贪心一致率（P4 的步长体检）")
    d.add_argument("--data", required=True, help="参考紧凑集（大家都见过的同一批状态）")
    d.add_argument("--label", default="ppo", help="配合 --generations 自动找 ckpt/<label>-gNN")
    d.add_argument("--generations", type=int, default=4)
    d.add_argument("--ckpts", default=None, help="显式列 checkpoint（逗号分隔；给了就不看 --label）")
    d.add_argument("--split", default="val", choices=["val", "train"])
    d.add_argument("--rows", type=int, default=40000)
    d.add_argument("--json", default=None)
    d.add_argument("--seed", type=int, default=20260401)

    d = sub.add_parser("screen", help="**跨代际小批量筛选**：4 席满桌 + 座位平均，按平均得点选候选人")
    d.add_argument("--policies", required=True,
                   help="正好 4 个策略（内置名 / ckpt 短名 / net 路径），逗号分隔，如 "
                        "`ppo-v3-g01-g05,ppo-v3-g01-g06,ppo-v3-g01-g07,teacher`")
    d.add_argument("--games", type=int, default=400, help="每批场次（小批量）")
    d.add_argument("--batches", type=int, default=3, help="批数（每批换 seed ⇒ 独立样本）")
    d.add_argument("--sample", type=int, default=64, help="评测采样（省盘；**不训练**，不影响时间）")
    d.add_argument("--workers", type=int, default=24)
    d.add_argument("--seed", type=int, default=910001)
    d.add_argument("--label", default="ppo-v3-g01", help="报告落到 league/<label>/")
    d.add_argument("--value-data", default=None,
                   help="可选：在这份紧凑集的 val 切分上对每个网络做**价值头校准**（同一把尺子）")
    d.add_argument("--ckpt-dir", default=None, help="价值检验找 `model.pt` 的根（缺省 = 数据根/ckpt）")
    d.add_argument("--rank-weight", type=float, default=1.0)
    d.add_argument("--batch", type=int, default=8192)
    d.add_argument("--json", default=None, help="汇总表写到这个文件（可选）")

    p = sub.add_parser("pair", help="两个策略的 2+2 同牌山配对（判据协议）")
    p.add_argument("--a", required=True, help="策略：内置名 / ckpt 短名（ppo-g04）/ net.bin 路径")
    p.add_argument("--b", required=True)
    p.add_argument("--games", type=int, default=1500)
    p.add_argument("--workers", type=int, default=24)
    p.add_argument("--sample", type=int, default=64)
    p.add_argument("--seed", type=int, default=1001001)
    p.add_argument("--tag", default=None, help="run 目录名（缺省 = <A>-vs-<B>）")
    p.add_argument("--label", default="pairs", help="报告落到 league/<label>/")
    p.add_argument("--json", default=None)

    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "displace":
        return cmd_displace(args)
    if args.cmd == "pair":
        return cmd_pair(args)
    if args.cmd == "screen":
        return cmd_screen(args)
    return cmd_ladder(args)


if __name__ == "__main__":
    raise SystemExit(main())
