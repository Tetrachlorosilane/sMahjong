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
#: ⚠ `critic` 排在 `train` **之后**（第二十六/二十七轮的实测）：值头在**冻住的表示**上学出来之后，
#: 一旦主干被 PPO 阶段推动，它就**失效**（实测 val CE 3.62 → 5.30、audit EV −3.33）。
#: 所以顺序是"先动策略、再练 critic"，`export` 导的也是 critic 那份权重。
PHASES = ("collect", "features", "compact", "train", "critic", "export", "audit", "eval")
#: 哪些相是 `python -m …`（**工作目录必须是 `python/`**，否则 `mahjong_ml` 不在 sys.path 上 ——
#: 实测报 `No module named 'mahjong_ml'`）。node / 训练端可执行仍从仓库根跑。
PY_PHASES = frozenset({"compact", "train", "critic", "export", "audit"})
#: 值头目标（训练口径）→ `value-audit --target` 的**主口径**名字。
#: ⚠ 这两处必须同一把尺子：`final` 是"整场 − 起点"，审计里叫 `value`；漏了映射就会拿错列去量。
AUDIT_TARGET = {"final": "value", "rtg": "rtg", "delta": "delta"}


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
    # ---- 第二十一轮（路线 B）之后：一轮的训练口径 -------------------------------------------
    #: 每轮最多多少步（0 = 不限，用 `epochs`）。**"多轮 × 每轮短"就靠它**：实测 KL 早停会在
    #: 第 ~100 步触发（单批 KL 0.0707 > 0.03），而跑满 5144 步的 top1 只比 102 步高 0.004
    #: ⇒ 一轮的价值在"重新采一批"（新轨迹 = 新行为策略的分布），不在 epoch 数（§14.10）。
    max_steps: int = 0
    advantage: str = "auto"
    rank_weight: float = 0.0
    baseline_fit: str = "scale"
    grad_clip: float = 0.5
    kl_early_stop: float = 0.03
    kl_min_steps: int = 100
    #: `--strict-gate`：值头闸门（`audit` 相）不过就**停整条回路**（缺省只记账，不停）。
    strict_gate: bool = False
    #: **值头先行**（`critic` 相）的步数：`>0` 时在 PPO 之前先跑一段
    #: `--only-heads value --freeze-trunk`（主干与策略头都不动 ⇒ **KL 恒 0**，不占 KL 预算），
    #: 再把这步的 checkpoint 当作 PPO 的 `--init`（`--behaviour` 仍是采集那份权重）。
    #:
    #: 为什么必须有这一段（第二十五/二十六轮实测）：KL 早停的预算是被**阶段 a（策略+牌效）**
    #: 吃掉的 —— `v4-mr02` 在第 60 步就 `KL=0.0338 > 0.03` 收工，而那 60 步全在阶段 a，
    #: 值头**一步都没轮到**（EV 仍 ≈0、闸门 FAIL）。"先让策略动"与"把 critic 学出来"在
    #: 一个 KL 预算里是**互斥**的；分开跑就没有这个矛盾。
    critic_steps: int = 0
    critic_lr: float = 1e-3

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

    @property
    def critic_ckpt(self) -> Path:
        """`critic` 相的 checkpoint 目录（`--label <tag>-critic`）。"""
        return paths.DATA_ROOT / "ckpt" / f"{self.ckpt_label}-critic"

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
    if cfg.critic_steps > 0:
        # 值头**最后**练（冻主干 + 只训 value）：**策略头逐位不变** ⇒ `KL ≡ 0`、不占 KL 预算，
        # 而且是在**最终那份表示**上练的（先练会被 PPO 阶段推动主干而失效）。
        # `--init` 给 PPO 那一步的 ckpt **目录**（`resolve_weight_path` 按 `<dir>/model.pt` 解）。
        r.commands["critic"] = [
            _py(), "-m", "mahjong_ml.v4.pretrain", "--data", str(r.compact),
            "--label", f"{r.ckpt_label}-critic", "--objective", "bc",
            "--value-target", cfg.value_target,
            "--init", str(paths.DATA_ROOT / "ckpt" / r.ckpt_label),
            "--only-heads", "value", "--freeze-trunk",
            "--max-steps", str(cfg.critic_steps), "--epochs", "1",
            "--batch", str(cfg.batch), "--lr", f"{cfg.critic_lr:g}", "--head-lr-mult", "1",
            "--stage-a", "0", "--stage-b", "1", "--mask-frac", "0", "--ssl-weight", "0",
            "--seed", str(cfg.seed + generation),
        ]
    if cfg.objective == "ppo":
        # π_old 必须来自**采集那份权重**与**采集那个温度**；数据集 meta 里记着学生串，训练端会核对
        train += ["--behaviour", str(net_in), "--behaviour-temp", f"{cfg.student_temp:g}"]
    # 数值口径（缺省与 `pretrain` 一致：grad-clip 0.5 / KL 早停 0.03）—— 显式写出来，
    # 这样台账里那一轮的"实际训练开关"是自解释的（`--dry-run` 也看得见）。
    train += ["--grad-clip", f"{cfg.grad_clip:g}",
              "--kl-early-stop", f"{cfg.kl_early_stop:g}",
              "--kl-min-steps", str(cfg.kl_min_steps)]
    if cfg.max_steps > 0:                      # "每轮短"：一轮的步数上限
        train += ["--max-steps", str(cfg.max_steps)]
    if cfg.advantage != "auto":
        train += ["--advantage", cfg.advantage]
    if cfg.rank_weight:
        train += ["--rank-weight", f"{cfg.rank_weight:g}"]
    train += ["--baseline-fit", cfg.baseline_fit]
    r.commands["train"] = train
    # ⚠ 导出的是**最终那份权重**：开了值头相就导 critic 那份（含新练的值头），否则导 PPO 那份。
    _final_ck = paths.DATA_ROOT / "ckpt" / final_ckpt_label(cfg, r)
    r.commands["export"] = [
        _py(), "-m", "mahjong_ml.v4.export", "weights",
        "--ckpt", str(_final_ck / "model.pt"),
        "--out", str(r.net_out), "--label", r.ckpt_label,
    ]
    # **判据必须判同一份权重**（否则"闸门过了"与"上线的那份"是两回事）。
    r.commands["audit"] = [
        _py(), "-m", "mahjong_ml.v4", "value-audit",
        "--data", str(r.compact), "--ckpt", str(_final_ck),
        "--target", AUDIT_TARGET.get(cfg.value_target, "value"),
        "--split", "val", "--batch", str(cfg.batch),
        "--strict", "--ev-ref", "legit",
        "--out", str(paths.DATA_ROOT / "league" / f"{r.label}-audit.json"),
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


#: 每个相的人话标签（**执行顺序由 `PHASES` 单独决定**，这里只管"打印什么"）。
#: ⚠ 第二十七轮踩过：执行顺序原本是在 `run_round` 里**另写一份元组**，加 `critic` 相时两份漂移了 ——
#: `--dry-run` 按 `PHASES` 打印（顺序对）、真跑按那份元组执行（critic 跑在 `train` 前 ⇒ 拿不到 PPO
#: 的 ckpt 当场报错）。现在**只有一处顺序**：`phase_order()` 读 `PHASES`，谁都别另写。
PHASE_WHAT = {
    "collect": "采集（自对弈）",
    "features": "派生特征（sidecar）",
    "check": "轨迹校验（selfplay-check）",
    "compact": "紧凑集（四张量 + 标签）",
    "train": "训练（策略）",
    "critic": "值头相（冻主干只训 value，KL 恒 0）",
    "export": "导出 net.bin",
    "audit": "值头判据（value-audit --strict）",
    "eval": "2+2 评测",
}


def phase_order(r: Round) -> list[str]:
    """要执行的相**按顺序**（唯一真相 = `PHASES`；`check` 由采集相之后紧跟执行）。"""
    want = list(PHASES)
    want.insert(want.index("features") + 1, "check")
    return [p for p in want if p in r.commands]
#: 值头闸门**需要的数据量 × 步数**（第三十/三十二轮量出来的四点；一行 ≈ 650 个决策、batch 256）：
#: · 120 场 / ~100 步 → `EV(delta)` ≤ 0（−0.075 ~ −3.33）；
#: · 400 场 / 1200 步 → **+0.0157**（合法天花板 0.26×）；
#: · 1000 场 / 1200 步 → **+0.0338**（0.44×）⇒ 数据够了、**步数不够**照样过不了（线 0.0541）；
#: · 1000 场 / 5144 步 → **+0.0496**（0.72×）⇒ **闸门通过（退出码 0）**。
#: ⇒ 读闸门结论要同时满足**两个**轴；只看场次会把"步数不够"读成"模型不行"。
VALUE_GATE_MIN_GAMES = 1000
VALUE_GATE_MIN_STEPS = 3000
#: 一行决策 ≈ 多少个（场次 → 决策行的换算，用于**规划期**估步数；实测 120 场 ≈ 79k 行）
ROWS_PER_GAME = 650


def planned_steps(games: int, batch: int = 256, max_steps: int = 0, epochs: int = 1) -> int:
    """规划期的**步数估算**：`min(max_steps, 行数/batch) × epochs`（行数 ≈ `games × 650`）。"""
    per_epoch = max(1, min(max_steps or 10 ** 9, max(1, games * ROWS_PER_GAME) // max(1, batch)))
    return int(per_epoch * max(1, epochs))


def value_gate_feasible(games: int, steps: int = 0) -> tuple[bool, str]:
    """这一轮的**场次 × 步数**够不够读值头闸门的结论（纯函数，自检直接喂上面那四个实测点）。

    ⚠ 两个轴**各自**都要够，而且不可读时必须**说清是哪一轴**（否则读者会把"步数不够"
    当成"模型不行" —— 第三十二轮就是这么被自己坑了一次：1000 场看着够，实际只到 0.44×）。
    """
    g_ok = games >= VALUE_GATE_MIN_GAMES
    s_ok = bool(steps) and steps >= VALUE_GATE_MIN_STEPS
    if g_ok and s_ok:
        return True, f"{games} 场 × {steps} 步（两个轴都够；实测这一档通过过）"
    parts = []
    if not g_ok:
        parts.append(f"场次不够（{games} < {VALUE_GATE_MIN_GAMES}；实测 400 场 ≈0.26× 天花板、"
                     f"120 场 EV ≤ 0）")
    if not s_ok:
        parts.append(f"步数不够（{steps or '未知'} < {VALUE_GATE_MIN_STEPS}；"
                     f"实测 1000 场/1200 步只到 0.44×）")
    return False, ("；".join(parts) +
                   f" —— 要读闸门结论请 ≥{VALUE_GATE_MIN_GAMES} 场 **且** ≥{VALUE_GATE_MIN_STEPS} 步")


def final_ckpt_label(cfg: LoopConfig, r: Round) -> str:
    """**这一轮最终会导出的那份权重**的 checkpoint 目录名。

    ⚠ 第二十八轮修的真 bug：`audit` 相曾经写死看 `<tag>`（PPO 那份），而开了值头相时 `export`
    导出的是 `<tag>-critic` ⇒ **闸门判的不是我们要上线的那份权重**（实测两者的 EV 差
    2.93 vs 6.03）。判据与产物必须指同一份东西。
    """
    return f"{r.ckpt_label}-critic" if cfg.critic_steps > 0 else r.ckpt_label


def _audit_json_path(label: str) -> Path:
    return paths.DATA_ROOT / "league" / f"{label}-audit.json"


def _read_audit(cfg: LoopConfig, r: Round) -> dict[str, Any]:
    """从 `value-audit --out` 的 JSON 里抽出**这一轮的闸门结论**（判据的机器可读版）。

    判据三条（§14.8.4）：`EV ≥ 0.7 × 合法天花板`（`--ev-ref legit` 现算）、CE < 边缘基线、
    CRPS < 气候学、50/80/95% 覆盖率与标称差 ≤3pp。这里只判**EV 与覆盖率**（最硬的两条），
    其余留在 JSON 里由人看 —— 闸门要少而明确，多了就没人理。
    """
    p = _audit_json_path(r.label)
    out: dict[str, Any] = {"read": False, "ok": False, "path": str(p), "label": r.label}
    if not p.is_file():
        return out
    row = json.loads(_audit_json_path(r.label).read_text(encoding="utf-8"))
    # 审计 JSON 的键 = checkpoint 目录名。**先按"最终那份权重"的名字找**（`<tag>-critic`），
    # 找不到再退回"第一个 dict"（老 JSON / 手工审计的产物）。
    want = final_ckpt_label(cfg, r)
    row = rep.get(want) if isinstance((rep := row), dict) else None
    if not isinstance(row, dict):
        row = next((v for v in rep.values() if isinstance(v, dict)), {})
    if not row:
        return out
    key = str(row.get("target") or AUDIT_TARGET.get(cfg.value_target, "value"))
    ev = row.get(f"ev_{key}")
    ref = row.get("ev_ref")
    need = row.get("ev_need")
    cov = {f"{lvl:g}": row.get(f"cov{lvl:g}") for lvl in (0.5, 0.8, 0.95)}
    cov_ok = all(c is not None and abs(float(c) - float(lvl)) <= 0.03 + 1e-9
                 for lvl, c in ((0.5, cov["0.5"]), (0.8, cov["0.8"]), (0.95, cov["0.95"])))
    ev_ok = (ev is not None and need is not None and float(ev) >= float(need))
    out.update({"read": True, "target": key, "ev": ev, "ev_ref": ref, "ev_need": need,
                "cov": cov, "ev_ok": bool(ev_ok), "cov_ok": bool(cov_ok),
                "ok": bool(ev_ok and cov_ok)})
    return out


def _run(cmd: list[str], what: str, *, cwd: Path | None = None,
         allow_fail: bool = False) -> float:
    """跑一条命令，返回墙钟秒数。失败**当场抛出**（不吞）—— 除非 `allow_fail`。

    `allow_fail` 只给**判据相**用（`value-audit --strict` 用退出码 2 表示"判据没过"）：
    那种失败要**记进台账**并（在 `--strict-gate` 下）决定是否停下，而不是把异常抛得一地都是。
    """
    where = cwd or ROOT
    print(f"\n▶ {what}\n  $ {' '.join(cmd)}\n  （cwd={where}）", flush=True)
    t0 = time.perf_counter()
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(ROOT / "python"))     # 双保险：-m 找不到包时还有它
    r = subprocess.run(cmd, cwd=str(where), env=env)       # noqa: S603 —— 命令由本模块自己拼
    dt = time.perf_counter() - t0
    if r.returncode != 0 and not allow_fail:
        raise SystemExit(f"{what} 失败（退出码 {r.returncode}）：{' '.join(cmd)}")
    flag = "" if r.returncode == 0 else f"（退出码 {r.returncode}）"
    print(f"  {'✓' if r.returncode == 0 else '✗'} {what}{flag} 用时 {dt:.1f}s", flush=True)
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
    gate_readable, gate_why = value_gate_feasible(
        games, planned_steps(games, cfg.batch, cfg.max_steps, cfg.epochs))
    if not gate_readable:
        print(f"  ⚠️ 值头闸门的**结论不可读**：{gate_why}")
    audit: dict[str, Any] = {}
    gate_ok = False
    for phase in phase_order(r):
        what = PHASE_WHAT.get(phase, phase)
        if phase == "train":
            what = f"训练（{cfg.objective}）"
        elif phase == "eval":
            what = f"2+2 评测（{cfg.eval_games} 场）"
        # 判据相：`value-audit --strict --ev-ref legit`（EV ≥ 0.7×合法天花板 + 覆盖率 ≤3pp + CE/CRPS）。
        # ⚠ 它用**退出码 2** 表示"判据没过"（不是命令失败）⇒ `allow_fail`，结论由 `_read_audit` 读 JSON。
        seconds[phase] = _run(r.commands[phase], what,
                              cwd=ROOT / "python" if phase in PY_PHASES else ROOT,
                              allow_fail=(phase == "audit"))
        if phase == "audit":
            audit = _read_audit(cfg, r)
            gate_ok = bool(audit.get("ok"))
            print(f"  判据：{'✅ 值头闸门通过' if gate_ok else '❌ 值头闸门未过'}"
                  f"{'' if audit.get('read') else '（审计输出没读到，按未过处理）'}")
            if cfg.strict_gate and not gate_ok:
                raise SystemExit(
                    f"第 {generation} 代（{r.label}）的值头闸门未过（--strict-gate）—— "
                    f"审计：{_audit_json_path(r.label)}；要么修值头口径，要么去掉 --strict-gate 只记账")
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
        "value_gate_readable": gate_readable, "value_gate_note": gate_why,
        "audit": audit,
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
    ap.add_argument("--value-target", choices=["final", "rtg", "delta"], default="final",
                    help="值头/优势的目标：`delta` = 本小局收支（P1b 的小局口径）")
    ap.add_argument("--advantage", choices=["auto", "gae-hand", "hand"], default="auto",
                    help="优势怎么算（`hand` = 小局级 baseline；`gae-hand` = 手级 GAE）")
    ap.add_argument("--rank-weight", type=float, default=0.0,
                    help="终局顺位点项的权重（只进优势；需数据集有 `rank_points` 列）")
    ap.add_argument("--baseline-fit", choices=["scale", "mean", "none"], default="scale",
                    help="基线尺度对齐（缺省 `scale`：最小二乘 `α+β·V`）")
    ap.add_argument("--max-steps", type=int, default=0,
                    help="每轮训练步数上限（0 = 不限）。**多轮 × 每轮短**就靠它："
                         "实测 KL 早停在第 ~100 步触发，跑满 5000 步只多 0.004 top1（§14.10）")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--stage-a", type=float, default=0.0)
    ap.add_argument("--stage-b", type=float, default=0.0)
    ap.add_argument("--teacher-seats", type=int, default=2)
    ap.add_argument("--student-temp", type=float, default=0.5)
    ap.add_argument("--hands", type=int, default=0, help="每场最多几小局（0 = 完整半庄）")
    ap.add_argument("--sample", type=int, default=1, help="每 K 次决策记 1 条（冒烟用）")
    ap.add_argument("--grad-clip", type=float, default=0.5, help="梯度总范数裁剪（0 = 关）")
    ap.add_argument("--kl-early-stop", type=float, default=0.03,
                    help="KL(π_old‖π_new) 早停阈值（0 = 关；v3 同口径 0.03）")
    ap.add_argument("--kl-min-steps", type=int, default=100, help="KL 早停生效前至少跑多少步")
    ap.add_argument("--strict-gate", action="store_true",
                    help="值头闸门（`value-audit --strict --ev-ref legit`：EV ≥ 0.7×合法天花板 + "
                         "覆盖率 ≤3pp）不过就**停整条回路**；缺省只记账不停（先看几轮再决定）")
    ap.add_argument("--critic-steps", type=int, default=0,
                    help="**值头先行**的步数（>0 时在 PPO 之前跑 `--only-heads value --freeze-trunk`："
                         "主干与策略头都不动 ⇒ KL 恒 0、不占 KL 预算；产物当 PPO 的 `--init`）")
    ap.add_argument("--critic-lr", type=float, default=1e-3, help="值头先行的学习率")
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
        max_steps=args.max_steps, advantage=args.advantage, rank_weight=args.rank_weight,
        baseline_fit=args.baseline_fit, grad_clip=args.grad_clip,
        kl_early_stop=args.kl_early_stop, kl_min_steps=args.kl_min_steps,
        strict_gate=args.strict_gate, critic_steps=args.critic_steps, critic_lr=args.critic_lr,
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
