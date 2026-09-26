"""**数据生产者**：谁来做自对弈采集与派生特征 —— Java 发布版服务端，还是训练端 C++ 引擎。

为什么需要这一层：训练回路的两个"贵"步骤（`--selfplay` 采集、`--features` 派生特征）
原本只有 Java 一份实现；训练端 C++ 引擎是它的**同种子逐字节等价重写**
（契约见 `docs/TRAINER-CPP.md` §2），目标就是**把这两步换成 C++ 跑**（同核数下决策/秒 ≥ 3×）。
所以"接进工作流"的判据不是"能跑"，而是**同种子产物逐字节相同** ——
切换生产者**不应该**改变数据集内容，只改变它跑得多快。

用法：环境变量 `MAHJONG_PRODUCER` = `java`（默认，权威实现）/ `cpp`（训练端引擎）。
⚠ 两个实现的能力**不是**一开始就对齐的：C++ 侧还在补齐（`docs/TRAINER-CPP.md` §5 里程碑），
所以这里对"C++ 还不支持的东西"**显式报错**，而不是悄悄产出一份不同的数据集。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 仓库根（`python/mahjong_ml/producer.py` → 上三级）
ROOT = Path(__file__).resolve().parents[2]
JAR = ROOT / "server" / "build" / "mahjong-server.jar"
#: 训练端可执行（`trainer/build.ps1` 的产物；Windows 带 `.exe`）
TRAINER = ROOT / "trainer" / "build" / ("trainer.exe" if os.name == "nt" else "trainer")

PRODUCERS = ("java", "cpp")

#: C++ 侧**还没**实现的能力（用到了就报错，别悄悄降级）——
#: 每补上一项就从这里删掉，并在 `docs/TRAINER-CPP.md` 的里程碑里记一笔。
#: ⚠ `--sample` 曾在这里，已于 2026-09 删除：C++ CLI 一直支持它，且**逐字节验过**
#: （`trainer-selfplay-parity.mjs 50 0 first 20260101 --sample 7` PASS、
#: `… 50 0 net:<ckpt> 20260101 --sample 7` PASS；见 docs/TRAINER-CPP.md §6.16）——
#: 留在这里会**挡住** P5 世代采集（`online.py` 的阶梯/评测默认 `--sample 64`）。
CPP_MISSING = {
    "--teacher-label": "DAgger 的老师标注（P2 采集用）；teacher 本体已移植，"
                       "缺的是记录器的 `teacher`/`teacher_index` 两列",
}


def producer(name: str | None = None) -> str:
    """当前生产者（`MAHJONG_PRODUCER`，缺省 `java`）。认不出的值直接报错。"""
    p = (name or os.environ.get("MAHJONG_PRODUCER") or "java").strip().lower()
    if p not in PRODUCERS:
        raise SystemExit(f"MAHJONG_PRODUCER 只能是 {'/'.join(PRODUCERS)}，收到 {p!r}")
    return p


def label(name: str | None = None) -> str:
    """给日志用的一句话（写进"自对弈 …"那行，方便台账里认出这批数据是谁产的）。"""
    p = producer(name)
    return f"生产者 java（{JAR.name}）" if p == "java" else f"生产者 cpp（{TRAINER.name}）"


def _guard(p: str, opts: dict[str, object]) -> None:
    if p != "cpp":
        return
    for flag, why in CPP_MISSING.items():
        if opts.get(flag):
            raise SystemExit(
                f"训练端 C++ 引擎还不支持 {flag}（{why}）——"
                f"这一次请用 MAHJONG_PRODUCER=java（见 docs/TRAINER-CPP.md §5）")


def selfplay_cmd(games: int, workers: int, policy: str, seed: int, out_dir: Path, *,
                 hands: int = 0, sample: int = 0, rotate: bool = True,
                 teacher_label: bool = False, name: str | None = None) -> list[str]:
    """自对弈采集命令（Java 与 C++ 两版**参数口径相同**，所以调用方不必分叉）。"""
    p = producer(name)
    _guard(p, {"--sample": sample, "--teacher-label": teacher_label})
    if p == "java":
        cmd = ["java", "-jar", str(JAR), "--selfplay", str(games), "--workers", str(workers)]
    else:
        cmd = [str(TRAINER), "selfplay", str(games), "--workers", str(workers)]
    if rotate:
        cmd += ["--rotate"]
    if teacher_label:
        cmd += ["--teacher-label"]
    cmd += ["--policy", policy, "--seed", str(seed), "--out", str(out_dir)]
    if hands:
        cmd += ["--hands", str(hands)]
    if sample:
        cmd += ["--sample", str(sample)]
    return cmd


def features_cmd(directory: Path, workers: int = 0, *, name: str | None = None) -> list[str]:
    """派生特征 sidecar（607/96 那些由服务端算的字段）。"""
    p = producer(name)
    if p == "java":
        cmd = ["java", "-jar", str(JAR), "--features", str(directory)]
    else:
        cmd = [str(TRAINER), "features", str(directory)]
    if workers:
        cmd += ["--workers", str(workers)]
    return cmd
