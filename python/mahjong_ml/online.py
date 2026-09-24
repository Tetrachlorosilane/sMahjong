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

⚠ 采到的每一代原始轨迹都留在 `raw/`（配额 30 GB，滚动淘汰最旧的）：**判据要能复算**，
所以别删；真要清盘就删 `compact/`（它能从 `raw/` 重建）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from . import league, paths

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
    cmd = ["java", "-jar", str(JAR), "--selfplay", str(games), "--workers", str(workers),
           "--rotate", "--policy", policy, "--seed", str(seed), "--out", str(out_dir)]
    if hands:
        cmd += ["--hands", str(hands)]
    if sample:
        cmd += ["--sample", str(sample)]
    # ⚠ 必须 `return`：调用方拿它写台账（`generations.json` 的 `collect_seconds`）。
    # 漏了 return 时字段是 null —— 不会报错，只是台账里那一列永远是空的（踩过一次）。
    return run(cmd, f"自对弈 {games} 场 → {out_dir.name}")


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
    """选这一代的**两个对手**（见模块 docstring 的"联赛对手"）。

    - 第一代：`teacher` ×2（先跟老师学，避免"两只没学好的自己互啄"）；
    - 之后：按 `league.select_weights`（"谁克我就多跟它打"）抽，**老师常驻一席**；
      没有 fit（还没跑过 `ladder`）就退化成"脚本基线里确定性轮转" —— 保证**可复现**。
    """
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
        # ① 采集要用的权重：第一代从 `--init` 现导（导到 `*-src`，**不动**原始 checkpoint 目录），
        #    之后直接用上一代结束时导出的 `net.bin`（它就是采集时的行为策略，π_old 靠它）
        if g == 1:
            net_bin = paths.allocate("ckpt", f"{tag}-src") / "net.bin"
            runpy(["mahjong_ml.export", "weights", "--ckpt", str(ckpt),
                   "--out", str(net_bin)], "导出采集权重（--init）")
        else:
            net_bin = nets[short_of(args.label, g - 1)]
        # ② 联赛选对手 → 采集（**两席自己 + 两席对手**，`--rotate` 会轮座位）
        opps = pick_opponents(args, g, nets, fit)
        spec = f"net:{net_bin}#{args.temp}"
        raw = paths.allocate("raw", tag)
        t_collect = selfplay(raw, args.gen_games, ",".join([spec, spec] + opps),
                            seed=args.seed + g * 1000, workers=args.workers,
                            hands=args.hands, sample=args.sample)
        # ③ 派生特征（Java 算）+ 紧凑集（Python 打包）
        run(["java", "-jar", str(JAR), "--features", str(raw), "--workers", str(args.workers)],
            "派生特征")
        comp = paths.allocate("compact", tag)
        runpy(["mahjong_ml.dataset", "build", str(raw), str(comp)], "建紧凑集")
        # ④ PPO 一代
        new_ckpt_dir = paths.allocate("ckpt", tag)
        ppo_cmd = ["mahjong_ml.ppo", "--data", str(comp), "--init", str(ckpt),
                   "--label", tag, "--temp", str(args.temp), "--epochs", str(args.epochs),
                   "--lr", str(args.lr), "--batch", str(args.batch), "--clip", str(args.clip),
                   "--ent-coef", str(args.ent_coef), "--max-kl", str(args.max_kl),
                   "--rank-weight", str(args.rank_weight), "--threads", str(args.threads),
                   "--seed", str(args.seed + g)]
        if g == 1 and args.init_value:
            # 只有第一代需要冷启动价值头；之后每一代的 checkpoint 里都带着 value（`--init` 自动继承）
            ppo_cmd += ["--init-value", str(args.init_value)]
        runpy(ppo_cmd, f"PPO 第 {g} 代")
        ckpt = new_ckpt_dir / "model.pt"
        # ⑤ 把这一代导出成 Java 能读的权重（下一代的采集、以及 ladder 都要用它）
        runpy(["mahjong_ml.export", "weights", "--ckpt", str(ckpt),
               "--out", str(new_ckpt_dir / "net.bin")], f"导出 {tag} 的权重")
        nets[tag] = new_ckpt_dir / "net.bin"
        log.append({"generation": g, "tag": tag, "opponents": opps, "raw": str(raw),
                    "compact": str(comp), "ckpt": str(ckpt), "collect_seconds": t_collect,
                    "games": args.gen_games, "temp": args.temp})
        (league_dir / "generations.json").write_text(
            json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{args.generations} 代跑完；台账：{league_dir / 'generations.json'}")
    print("下一步：`python -m mahjong_ml.online ladder --label " + args.label
          + " --generations " + str(args.generations) + "` 拿 Elo 与配对 CI（判据在这里，不在训练日志里）")
    return 0


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
    # sd(Δ)≈80，同样场次的精度差一倍（实测 60 场），所以主判据不在 ladder run 里读
    pair_runs = []
    pairs = []
    if args.pair_games > 0:
        for i, (name, spec) in enumerate(entries):
            out = paths.allocate("raw", f"{args.label}-pair-{name}")
            selfplay(out, args.pair_games, f"{spec},{spec},teacher,teacher",
                     seed=args.seed + 200000 + i * 1000, workers=args.workers,
                     sample=args.sample)
            pair_runs.append(out)
            runpy(["mahjong_ml.eval", str(out), "--labels", f"{name},teacher",
                   "--json", str(league_dir / f"pair-{name}.json")], f"配对评测 {name} vs teacher")
            pr = paired_report(out, name, "teacher")
            pairs.append(pr)
            print(f"  ⇒ {name} − teacher：Δ={pr['delta']:+.2f} 顺位点，95%CI "
                  f"[{pr['ci'][0]:+.2f}, {pr['ci'][1]:+.2f}]，p={pr['p']:.3f}，"
                  f"胜/负/平 {pr['win']}/{pr['lose']}/{pr['tie']}，sd={pr['sd']:.2f}"
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P4：世代循环 + 联赛阶梯")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="跑 N 代（采集→特征→紧凑→PPO→导出）")
    r.add_argument("--init", required=True, help="起始 checkpoint（P4 必须从 BC/AWR 初始化）")
    r.add_argument("--init-value", default=None,
                   help="第一代的价值头冷启动：P3 的 IQL critic（`ckpt/iql-00N/model.pt`）")
    r.add_argument("--label", default="ppo")
    r.add_argument("--generations", type=int, default=4)
    r.add_argument("--start-gen", type=int, default=1,
                   help="从第几代开始（断点续跑：`--start-gen 4 --init <第 3 代的 model.pt>`）")
    r.add_argument("--gen-games", type=int, default=2000, help="每代采集场次（写 raw/，约 1.1 MB/场）")
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
    args = ap.parse_args(argv)
    return cmd_run(args) if args.cmd == "run" else cmd_ladder(args)


if __name__ == "__main__":
    raise SystemExit(main())
