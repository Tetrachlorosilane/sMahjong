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
    #: 对手池（历史快照的 `net.bin` 路径）：把一个非学生座位换成它、按代轮换。
    #: ⚠ **风格线不要用这个**（它只换 1 席、另外 2 席还是学生自己）—— 风格线用 `style` + `style_pools`。
    opponents: tuple[str, ...] = ()
    #: **风格桌**（2026-10-08 用户指定）：`{风格名: (该线快照的 net.bin, ...)}`，配 `style` = **本线风格名**。
    #: 四席 = **学生 1 席** + **每个风格 1 席**（本线那一席取**别的**快照，不是学生自己）；见 `_style_policy`。
    style_pools: dict[str, tuple[str, ...]] = field(default_factory=dict)
    style: str = ""
    #: **24 种座次关系全排列**（`producer.selfplay_cmd` 的 `--rotate-perm`）：`True/False` 显式指定；
    #: `None`（缺省）= **自动** —— 有风格桌（`style` + `style_pools`）时开、否则关
    #: （关 = 沿用历季的循环移位，保证既有多季读数可比）。
    #: 为什么风格桌**必须**开（2026-10-08 用户指正 + 实测）：循环移位只覆盖 4 个排列、且**保持循环序**
    #: ⇒ 一代之内相对方位被钉死（400 场：`def` 恒 +1 下家、`win` 恒 +2 対面、`atk` 恒 +3 上家，各 400/400）。
    #: 判据：`python tools/seat-balance.py <该代 raw 目录> --check`（② 绝对座位 / ③ 24 种座次关系 / ④ 相对方位）。
    rotate_full: bool | None = None
    #: 风格桌上**对手怎么选**（2026-10-08 用户裁决：对手同样采用**最新模型**）：
    #: · `"latest"`（缺省）= 每个风格取**该风格最新的一代**（按名字排序的最后一个；排除学生自己的网）
    #:   —— 对手随本线推进而更新，永远是"最近练出来的那一版"；
    #: · `"rotate"` = 按代在**整条历史池**里轮转（之前的口径：对手是历次快照的混合）。
    #: ⚠ 这是**配方**的一部分：两口径下的读数不可互比（`def7` 那轮用的是 `rotate`）。
    style_pick: str = "latest"
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
    #: 评测的对手是谁：`teacher` = 2+2 对 teacher（强度锚，绝对口径）；
    #: `prev` = 2+2 对**上一轮的 net**（同牌山配对 ⇒ 直接量"这一轮有没有长进"，多轮战役该看这个）。
    eval_vs: str = "teacher"
    #: **代号的偏移**：`run_round` 的 `generation` 从 1 起算（一次调用内的相对代），
    #: 而"一轮一次调用、外面自己轮换大件"那种跑法要把第 i 次调用编成第 i 代 ——
    #: 否则每次都编 `-g01`，**后一轮会覆盖前一轮的 raw/compact/ckpt/评测目录**
    #: （第三十三轮实测：两轮都编 g01，台账里两行同名、第一轮的产物被第二轮盖掉）。
    gen_offset: int = 0
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
    #: **W3：对现任的 KL 锚**（`docs/VALVES-AND-FIXTURES.md` §3 W3）。β=0（缺省）= 今天的行为。
    #: 锚就是 `--init`（= 本轮的 `net_in` = **现任**）那一份 —— 回路不必再给 `--ref`，
    #: `pretrain` 的缺省锚正是 `--init`。为什么要它：历季每轮只走 66/600 步、候选只是现任的
    #: 微小扰动，而闸门分辨率（±2.35 顺位点）看不见那个量级 ⇒ 改成"放宽 KL 预算让每轮真能动
    #: + 用 β 把方向锚在现任上"两个旋钮分开调（第六十二轮的三条遗留决策之 (B)+(C)）。
    ref_beta: float = 0.0
    #: **防过拟合的验证集早停**：每 `val_every` 步在固定子集上读 train/val 的 policy CE，
    #: 连续 `val_patience` 次未改善就正常收尾（`pretrain --val-every/--val-patience`）。
    #: 缺省 0 = 关闭（老行为）；回路跑长轮次时它是"廉价低噪的第一读数"那条的兜底。
    val_every: int = 0
    val_patience: int = 3
    #: ★ **W4 第一步：逐候选引擎标签的模仿项**（`pretrain --il-weight`）。0（缺省）= 今天的行为。
    #: 目标候选由 `dataset.engine_best_index` 从 `cand.derived`（引擎的逐候选牌效：打后向听/进张/
    #: 听牌形）现算 —— 稠密、无采样噪声、不花闸门算力，且**每一份现有紧凑集里都有**。
    il_weight: float = 0.0
    #: ★ **风格奖励塑形**（`--style-bonus`，2026-10-09 加）：空串（缺省）= **关闭** ——
    #: 紧凑集的命令行里一个字符都不多、`style_bonus` 列不建、`pretrain` 一行都不走
    #: ⇒ 与加这个功能之前**逐位相同**（判据：`python/selfcheck.py` 的命令逐字断言 + 权重哈希）。
    #: 文法见 `v4.style_reward.parse_bonus`（例：`riichi=1.0:no_deal` = 立直率 ↑，
    #: 但只有**该小局未放铳**才付奖 —— 成对质量轴，防止学出无脑立直）。
    #: ⚠ 白化参数 μ/σ **每代从这一份数据集现算**（不是历史常数）⇒ 回路里不存任何标定值。
    style_bonus: str = ""
    #: 白化参数的统计总体：`student`（缺省，= 训练里真正进梯度的那些行）/ `all`。
    style_whiten: str = "student"
    #: 验证集早停**读哪个头的 CE**：`policy`（缺省，= 行为标签）/ `il`（引擎标签）。
    #: ⚠ 开了 `il_weight` 就**必须**配 `--val-metric il`：IL 臂的行为 CE 必然抬高（实测 0.512 → 0.62），
    #: 按行为 CE 判"没改善"会在引擎 CE 还在降的时候把这一轮掐掉（见 `docs/TRAINING-V4.md` §14.12）。
    val_metric: str = "policy"
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

    def policy(self, net: Path, generation: int = 0) -> str:
        """四席策略串。**两种口径**（`style` 非空且有 `style_pools` ⇒ 风格桌）：

        **① 风格桌（风格线专用，2026-10-08 用户指定）**：见 `_style_policy` ——
        学生 1 席 + **每个风格各 1 席**（含本线风格的一枚**别的**快照）。座位平均化由
        `selfplay_cmd(rotate=True, rotate_full=True)` 保证（C++ 侧按场号枚举 **4! = 24 个全排列** ⇒
        绝对座位与**相对方位**都被平均；⛔ 只做循环移位不够 —— 见 `rotate_full` 字段的注释与实测）。

        **② 旧口径（通用联赛）**：学生 N 席（`4 - teacher_seats`）+ teacher +（可选）**对手池里的一个**历史快照。

        ⚠ 为什么要 `--opponents`（第四十三轮）：v4 回路原来固定"自己×2 + teacher×2"，
        于是**结果奖励里关于"通用强度"的信息很少** —— 对手永远是同一批（teacher 与自己的上一代），
        你变强变弱都在同一张桌子上。v3 谱系唯一出过正结果的那条路，采集桌上有**历史快照**。
        这里按代轮换对手池（`generation % len(opponents)`），其余座位不变；
        **学生席位数不变**（`--student` 串照旧逐字匹配，见 `student_spec`）。

        ⚠⚠ 但**风格线不能用口径②**（2026-10-08 用户指正）：桌上 2/4 席是学生自己的风格、对手只有 1 席
        ⇒ 学不到"对另外两种风格怎么打"，而"结果奖励"里关于风格强度的信息几乎是常数
        （改动前的实测配置：`-PoolSeed atk2-g09,league14-g05` ⇒ 每代只有 1 个风格对手上桌）。
        """
        if self.style and self.style_pools:
            return self._style_policy(net, generation)
        students = [self.student_spec(net)] * (4 - self.teacher_seats)
        others = ["teacher"] * self.teacher_seats
        if self.opponents and others:
            # 对手用**贪心**（不带温度）：它是"标尺"，不该跟自己一样抖。
            pick = self.opponents[generation % len(self.opponents)]
            others[-1] = f"net:{pick}"
        return ",".join(students + others)

    def _style_policy(self, net: Path, generation: int) -> str:
        """**风格桌**：学生 1 席 + 3 席对手，**每个风格恰好 1 席**（风格数 ≠ 3 时按下述规则）。

        规则（一条公式 `slots = [风格[(generation + k) % n] for k in 0..2]` 覆盖三种情形）：

        - **n == 3（本仓现状：atk / def / win）**：三席正好三个风格各一 ⇒ **每代桌上都有全部三种风格**；
        - **n > 3**：按代轮转起点取连续 3 个风格 ⇒ 长期每个风格等频（每 n 代覆盖一遍）；
        - **n < 3**：轮转补齐（某风格占 2 席），起点按代轮转 ⇒ 长期"谁多占一席"也等频。

        ⛔ **别在这里引入随机数**：对手必须是 `(generation, 池内容)` 的**确定**函数 ——
        否则 `run-chain` 的续跑与"同代可复现"都会破（历季的伪重复事故见 `NOTES.md` §6.5 第八季审计）。

        判据（`python/selfcheck.py` 的「风格桌」组，逐条机械断言）：
        ① 恒 4 席；② 学生**恰好 1 席**；③ 覆盖的风格集合 == `style_pools` 的全部风格；
        ④ 本线风格的对手 ≠ 学生自己的网（`--rotate` 要求四席策略互异，见 `league.py:140` 的"撞车"判据）；
        ⑤ 跨代看，每个风格在每个**席序位**上等频（这层是**生成级**轮转；**游戏级**必须靠
        `--rotate-perm` 的 24 排列把全部座次关系覆盖 —— 只有 `--rotate` 的循环移位会漏掉相对方位）。
        """
        styles = sorted(self.style_pools)
        if not styles:
            raise ValueError("style_pools 为空 —— 风格桌至少要有一个风格")
        if self.style not in self.style_pools:
            raise ValueError(f"style={self.style!r} 不在 style_pools={styles} 里 —— 风格桌必须包含本线的池")
        n = len(styles)
        slots = [styles[(generation + k) % n] for k in range(3)]
        opp = [self._style_pick(s, net, generation, k) for k, s in enumerate(slots)]
        return ",".join([self.student_spec(net)] + [f"net:{p}" for p in opp])

    def _style_pick(self, style: str, student_net: Path, generation: int, k: int) -> str:
        """从某个风格的池里**确定地**取一枚快照，并**避开学生自己的网**。

        两种口径（`style_pick`）：
        · `"latest"`（缺省，2026-10-08 用户裁决"对手同样采用最新模型"）= 取**该风格最新的一代**
          （排除学生自己的网之后按名字排序的最后一个 —— 网络名是 `g%02d` 零填充 ⇒ 字典序 = 代序）；
          本线的对手因此永远是"上一次练出来的那一版"（学生自己那一版不能上台：`--rotate` 要求四席互异）。
        · `"rotate"` = `pool[(generation + k) % len(pool)]`（旧口径：历次快照的混合）。

        为什么必须避开学生自己的网：`--rotate` 要求四席策略互异（同一份权重坐两席 = `league.py:140`
        的"撞车"判据，轻则座位运气被记成风格强弱、重则那条判据直接报错）。
        """
        pool = [p for p in self.style_pools[style] if str(Path(p)) != str(Path(student_net))]
        if not pool:
            pool = list(self.style_pools[style])
        if not pool:
            raise ValueError(f"风格 {style!r} 的池是空的 —— 风格桌每个风格至少要有一枚快照")
        if self.style_pick == "latest":
            return sorted(pool)[-1]
        if self.style_pick != "rotate":
            raise ValueError(f"style_pick 只能是 'latest' / 'rotate'，收到 {self.style_pick!r}")
        return pool[(generation + k) % len(pool)]


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
    #: 评测两侧的标签（`judge` 用它们去轨迹里找系列；由 `plan_commands` 决定是不是 `prev` 模式）
    eval_label_a: str = ""
    eval_label_b: str = "teacher"

    @property
    def critic_ckpt(self) -> Path:
        """`critic` 相的 checkpoint 目录（`--label <tag>-critic`）。"""
        return paths.DATA_ROOT / "ckpt" / f"{self.ckpt_label}-critic"

    def phases(self) -> list[str]:
        return [p for p in PHASES if p in self.commands]


def _py() -> str:
    """当前解释器（venv 里的那个）—— 子进程用它，避免"系统 python 没有 torch"。"""
    return sys.executable


def _parse_style_pools(items: list[str]) -> dict[str, tuple[str, ...]]:
    """`--style-pool NAME=path1,path2`（可重复）⇒ `{NAME: (path1, path2)}`。

    ⚠ 写成 `NAME=` 空池或漏 `NAME=` 一律**报错**（静默当成"没有这个风格"会让风格桌悄悄退化成
    少一个风格的桌子 —— 那正是这次要修的病，绝不能留一个静默降级的入口）。
    """
    out: dict[str, list[str]] = {}
    for it in items:
        name, sep, paths = it.partition("=")
        name = name.strip()
        got = [p.strip() for p in paths.split(",") if p.strip()]
        if not sep or not name or not got:
            raise SystemExit(f"--style-pool 要写成 NAME=path1,path2（收到 {it!r}）")
        out.setdefault(name, []).extend(got)
    return {k: tuple(v) for k, v in out.items()}


def plan_commands(cfg: LoopConfig, generation: int, net_in: Path, games: int) -> Round:
    """生成一轮的全部命令（**不执行、不建目录**）—— `--dry-run` 与执行共用同一份。"""
    generation = generation + cfg.gen_offset          # 绝对代号（跨调用唯一）
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
    # ⚠ **采集桌**用 24 排列（风格桌自动开）：一次训练里把 4! = 24 种座次关系都平均到。
    #   评测/闸门桌**刻意不动**（仍走 `--rotate` 循环移位）—— 它是判据；改判据的座位方案会让
    #   本季与历季（def/def2/def3/defrep…）的判决不可比。
    rf = cfg.rotate_full if cfg.rotate_full is not None else bool(cfg.style and cfg.style_pools)
    r.commands["collect"] = producer.selfplay_cmd(
        games, cfg.workers, cfg.policy(net_in, generation), cfg.seed + generation, r.raw,
        hands=cfg.hands, sample=cfg.sample, rotate=True, rotate_full=rf, aux=True, name=p)
    r.commands["features"] = producer.features_cmd(r.raw, cfg.workers, name=p)
    r.commands["check"] = ["node", "tools/selfplay-check.mjs", str(r.raw)]
    r.commands["compact"] = [
        _py(), "-m", "mahjong_ml.v4.dataset", str(r.raw), str(r.compact),
        "--aux", "--student", spec, "--workers", str(max(1, cfg.workers // 2)),
    ]
    # ★ 风格奖励塑形：**只在给了 spec 时才进命令行**（与 `--ref-beta` / `--il-weight` 同一个做法）
    #   ⇒ 空串时打出来的命令与加这个功能之前**逐字相同**（"默认关闭 = 逐位不变"的第一条判据）。
    #   ⚠ 口径都在 `dataset build` 里（指示量从轨迹的事件算、μ/σ 从这一份数据实测、付奖账落盘），
    #   回路只负责把 spec 传下去 —— 别在这里再实现一遍风格量（两份实现必漂移）。
    if cfg.style_bonus:
        r.commands["compact"] += ["--style-bonus", cfg.style_bonus,
                                  "--style-bonus-whiten", cfg.style_whiten]
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
    # W3：锚的参考策略**就是 `--init`**（现任）⇒ 只转发 β 就够了（`pretrain` 的缺省锚 = `--init`）；
    # 不为 0 时才进命令 —— 这样"β=0 的老轮次"打出来的命令与以前**逐字相同**（台账可比、自检也没动）。
    if cfg.ref_beta:
        train += ["--ref-beta", f"{cfg.ref_beta:g}"]
    # 验证集早停：同样只在开启时进命令（缺省 0 = 今天的行为）
    if cfg.val_every > 0:
        train += ["--val-every", str(cfg.val_every), "--val-patience", str(cfg.val_patience)]
        if cfg.val_metric != "policy":        # ⚠ 只在早停开着时转发（关着就没有"读哪个头"这回事）
            train += ["--val-metric", cfg.val_metric]
    # ★ W4：引擎逐候选标签的模仿项（只在非 0 时进命令 ⇒ 0 的轮次与以前**逐字相同**）
    if cfg.il_weight:
        train += ["--il-weight", f"{cfg.il_weight:g}"]
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
    # 评测：`prev` 模式把**上一轮那份权重**放对面（同牌山 2+2 ⇒ 配对比较"这一轮有没有长进"）；
    # 第 1 代没有"上一轮"（`net_in` 就是起点那代）⇒ 退回 `teacher`。
    # ⚠ 这里**不查文件是否存在**（`--dry-run` 的第 2 代本来就还没产出它）；真跑时由 `run_round`
    # 显式报错 —— 别静默退回 teacher（那会把两种口径混进同一条曲线）。
    use_prev = (cfg.eval_vs == "prev" and generation > 1)
    b_net = net_in if use_prev else None
    r.eval_label_a = f"net:{r.net_out}"
    r.eval_label_b = f"net:{b_net}" if use_prev else "teacher"
    r.commands["eval"] = [
        str(producer.TRAINER), "selfplay", str(cfg.eval_games), "--workers", str(cfg.eval_workers),
        "--rotate",
        *(["--rotate-perm"] if rf else []),
        "--policy", (f"net:{r.net_out},net:{r.net_out},net:{b_net},net:{b_net}" if use_prev
                     else f"net:{r.net_out},net:{r.net_out},teacher,teacher"),
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
    if (not dry_run) and cfg.eval_vs == "prev" and generation > 1 and not net_in.is_file():
        raise SystemExit(f"--eval-vs prev 需要**上一轮的 net.bin**，但找不到 {net_in}"
                         f"（缺了它就只能对 teacher，那会把两种口径混进同一条曲线 —— 宁可报错）")
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
    got = judge(r.eval_dir, r.eval_label_a, r.eval_label_b)
    print("  判据：" + f"Δ={got['delta']:+.2f} 顺位点 [{got['lo']:+.2f}, {got['hi']:+.2f}] "
          f"p={got['p']:.3f}（n={got['n']}；按观测 sd 检出 Δ=2 需 {got['required_n_for_2']} 场）")
    row = {
        "generation": generation, "label": r.label, "games": games, "seed": cfg.seed + generation,
        "producer": cfg.producer, "objective": cfg.objective, "value_target": cfg.value_target,
        "student_spec": cfg.student_spec(net_in), "net_in": str(net_in), "net_out": str(r.net_out),
        # ★ 风格奖励塑形：**记进台账**（复现那一代要知道"奖励是怎么塑的"）——
        #   空串 = 关闭（老台账里没有这个键，读的时候按"没有塑形"看待）。
        "style_bonus": (cfg.style_bonus or ""),
        "style_bonus_whiten": (cfg.style_whiten if cfg.style_bonus else ""),
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
    ap.add_argument("--eval-vs", choices=["teacher", "prev"], default="teacher",
                    help="评测对手：teacher = 2+2 对 teacher（绝对锚）；prev = 2+2 对**上一轮的 net**"
                         "（同牌山配对，直接量这一轮有没有长进；第 1 代自动退回 teacher）")
    ap.add_argument("--gen-offset", type=int, default=0,
                    help="代号的偏移：一次只跑一代、由外面轮换大件时，用它把第 i 次调用编成第 i 代"
                         "（否则每轮都编 -g01，后一轮会覆盖前一轮的产物）")
    ap.add_argument("--opponents", action="append", default=[],
                    help="对手池（可重复）：把一个非学生座位换成这个历史快照的 net.bin，按代轮换。"
                         "⚠ **风格线别用它**（只换 1 席、另 2 席还是学生自己）—— 风格线用 --style/--style-pool")
    ap.add_argument("--style", default="",
                    help="**本线风格名**（与 `--style-pool` 合用 ⇒ **风格桌**：学生 1 席 + 每个风格 1 席）。"
                         "例：`--style atk`。空（缺省）= 走旧的 teacher/opponents 口径")
    ap.add_argument("--style-pool", action="append", default=[], metavar="NAME=path1,path2",
                    help="某风格线的快照池（可重复，路径逗号分隔）。**风格桌上每个风格恰好 1 席**")
    ap.add_argument("--style-pick", choices=["latest", "rotate"], default="latest",
                    help="风格桌上对手怎么选：`latest`（缺省，用户裁决）= 每个风格取**最新一代**；"
                         "`rotate` = 按代在整条历史池里轮转（旧口径，读数不可与 latest 互比）")
    ap.add_argument("--rotate-full", dest="rotate_full", action="store_true", default=None,
                    help="**24 种座次关系全排列**（C++ `--rotate-perm`）：按场号枚举 4! = 24 个排列 ⇒ "
                         "绝对座位与**相对方位**都平均。缺省（不给这个开关）= 有风格桌就自动开、否则关")
    ap.add_argument("--no-rotate-full", dest="rotate_full", action="store_false",
                    help="强制关（只做 `--rotate` 循环移位）—— 复现旧口径或与历季对齐时用")
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
    ap.add_argument("--ref-beta", type=float, default=0.0,
                    help="**W3 对现任的 KL 锚**的权重：`loss += β·KL(π_θ ‖ π_ref)`，π_ref = `--init`"
                         "那一份（本轮现任，冻结）。0（缺省）= 今天的行为；配 `--kl-early-stop` 放宽"
                         "（例如 0.10）就是 W3 的「放宽预算 + 锚住方向」配方")
    ap.add_argument("--val-every", type=int, default=0,
                    help="**防过拟合**：每多少步读一次 train/val 的 policy CE（0 = 关闭）")
    ap.add_argument("--val-patience", type=int, default=3,
                    help="val CE 连续多少次未改善就早停（正常收尾、照常导出 ckpt）")
    ap.add_argument("--val-metric", choices=["policy", "il"], default="policy",
                    help="早停读哪个头的 CE：`policy`（缺省 = 行为标签）/ `il`（**引擎标签**）—— "
                         "开了 `--il-weight` 就用 `il`（行为 CE 在 IL 臂上必然抬高）")
    ap.add_argument("--il-weight", type=float, default=0.0,
                    help="**W4 第一步**：引擎逐候选标签的模仿项权重"
                         "（`loss += w·CE(logits, 引擎最优候选)`，只在学生行上）。0（缺省）= 今天的行为")
    ap.add_argument("--style-bonus", default=(os.environ.get("MAHJONG_STYLE_BONUS") or "").strip(),
                    metavar="SPEC",
                    help="**风格奖励塑形**（缺省空 = 关闭，与加这个功能之前逐位相同）。"
                         "文法 `NAME[=w]:PAIR[,...]`，例 `riichi=1.0:no_deal`"
                         "（立直率 ↑，只在该小局**未放铳**时付奖）。轴表见 `v4/style_reward.py`。"
                         "⛔ 不写 PAIR 直接报错（不许只按立直付奖）。"
                         "⚠ 缺省值也可由环境变量 `MAHJONG_STYLE_BONUS` 给 —— "
                         "那是给**不改 `tools/run-league.ps1`**（本仓不许动它）准备的入口")
    ap.add_argument("--style-bonus-whiten", choices=["student", "all"],
                    default=(os.environ.get("MAHJONG_STYLE_WHITEN") or "student").strip(),
                    help="白化参数 μ/σ 的统计总体：`student`（缺省）= 只用学生座位的小局"
                         "（= 训练里真正进梯度的行）；`all` = 全部座位。"
                         "⚠ 缺省值也可由 `MAHJONG_STYLE_WHITEN` 给（与 `--style-bonus` 同理）")
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
        teacher_seats=args.teacher_seats, opponents=tuple(args.opponents),
        style=args.style, style_pools=_parse_style_pools(args.style_pool),
        style_pick=args.style_pick,
        rotate_full=args.rotate_full,
        objective=args.objective,
        value_target=args.value_target, epochs=args.epochs, batch=args.batch, lr=args.lr,
        stage_a=args.stage_a, stage_b=args.stage_b, workers=args.workers,
        eval_games=args.eval_games, eval_workers=args.eval_workers, seed=args.seed,
        eval_vs=args.eval_vs, gen_offset=args.gen_offset,
        hands=args.hands, producer=chosen, no_java=args.no_java, sample=args.sample,
        max_steps=args.max_steps, advantage=args.advantage, rank_weight=args.rank_weight,
        baseline_fit=args.baseline_fit, grad_clip=args.grad_clip,
        kl_early_stop=args.kl_early_stop, kl_min_steps=args.kl_min_steps,
        ref_beta=args.ref_beta, val_every=args.val_every, val_patience=args.val_patience,
        val_metric=args.val_metric, il_weight=args.il_weight,
        style_bonus=args.style_bonus, style_whiten=args.style_bonus_whiten,
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
