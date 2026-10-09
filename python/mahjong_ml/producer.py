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
#: ⚠ 可用 **`MAHJONG_TRAINER`** 覆盖成别的副本。为什么需要（2026-09-29 实测）：
#: 受限沙箱按「**可执行文件所在位置**」决定写入是否生效 —— **工作区里的 exe 写数据根会被静默吞掉**
#: （退出码 0、目录不存在、连影子副本都找不到），而把同一份 `trainer.exe` 放到数据根上再跑就正常。
#: 沙箱/受限环境里用这个变量指过去即可（`S:\…\tools\trainer\trainer.exe`）。
TRAINER = ROOT / "trainer" / "build" / ("trainer.exe" if os.name == "nt" else "trainer")
if (os.environ.get("MAHJONG_TRAINER") or "").strip():
    TRAINER = Path(os.environ["MAHJONG_TRAINER"].strip())

PRODUCERS = ("java", "cpp")

#: C++ 侧**还没**实现的能力（用到了就报错，别悄悄降级）——
#: 每补上一项就从这里删掉，并在 `docs/TRAINER-CPP.md` 的里程碑里记一笔。
#: ⚠ `--sample` 曾在这里，已于 2026-09 删除：C++ CLI 一直支持它，且**逐字节验过**
#: （`trainer-selfplay-parity.mjs 50 0 first 20260101 --sample 7` PASS、
#: `… 50 0 net:<ckpt> 20260101 --sample 7` PASS；见 docs/TRAINER-CPP.md §6.16）——
#: 留在这里会**挡住** P5 世代采集（`online.py` 的阶梯/评测默认 `--sample 64`）。
#: ⚠ `--aux` 曾在这里，已于 2026-09 删除：C++ 侧补了 `trainer/src/npzwriter.hpp` +
#: `TraceRecorder` 的标签侧行，**连 npz 容器一起逐字节相同**
#: （`node tools/trainer-aux-parity.mjs 5 4 teacher 20260101 --rotate` PASS，
#: 负向对照 `--selfcheck` 也在）。于是"v4 训练全流程脱离 Java"不再缺任何采集能力。
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


def java_forbidden() -> bool:
    """`MAHJONG_NO_JAVA=1` = **训练端脱离 Java** 的守卫（v4 回路默认它）。"""
    return (os.environ.get("MAHJONG_NO_JAVA", "") or "").strip().lower() not in ("", "0", "false", "no")


def guard_java_free(name: str | None = None) -> None:
    """`MAHJONG_NO_JAVA=1` 时**禁止**选中 java 生产者 —— 报错，不静默回退。

    为什么要它：`MAHJONG_PRODUCER` 缺省是 java（v3 谱系的兼容值），而"v4 训练全流程脱离 Java"
    这个目标一旦被环境变量悄悄改回去，症状是"又能跑了、只是慢十几倍"，没有任何报错。
    """
    if java_forbidden() and producer(name) == "java":
        raise SystemExit(
            "MAHJONG_NO_JAVA=1 但当前生产者是 java —— 改成 `MAHJONG_PRODUCER=cpp`，"
            "或去掉这个环境变量（见 docs/TRAINER-CPP.md §5）")


def _guard(p: str, opts: dict[str, object]) -> None:
    if p != "cpp":
        return
    for flag, why in CPP_MISSING.items():
        if opts.get(flag):
            raise SystemExit(
                f"训练端 C++ 引擎还不支持 {flag}（{why}）——"
                f"这一次请用 MAHJONG_PRODUCER=java（见 docs/TRAINER-CPP.md §5）")


def selfplay_cmd(games: int, workers: int, policy: str, seed: int, out_dir: Path, *,
                 hands: int = 0, sample: int = 0, rotate: bool = True, rotate_full: bool = False,
                 teacher_label: bool = False, aux: bool = False,
                 name: str | None = None) -> list[str]:
    """自对弈采集命令（Java 与 C++ 两版**参数口径相同**，所以调用方不必分叉）。

    @param rotate_full **24 种座次关系全排列**（`--rotate-perm`，2026-10-08 用户口径）：
                按场号 `g % 24` 枚举 4! = 24 个全排列 ⇒ 一次训练里**绝对座位与相对方位都被平均**。
                ⚠ 与 `rotate`（循环移位）的差别是**实测出来的**：后者只覆盖 4 个排列、且**保持循环序**
                ⇒ 一代之内每个策略相对其他三家的方位被钉死（400 场里 `def` 恒在下家、`win` 恒在対面、
                `atk` 恒在上家，各 400/400）。判据 = `python tools/seat-balance.py <raw> --check`
                （② 绝对座位 / ③ 座次关系 24 种 / ④ 相对方位）。
                ⛔ **Java 生产者没有这个开关** ⇒ 用 java 跑风格桌**显式报错**，不悄悄降级成循环移位
                （那正是"看起来平均了、其实没有"的假绿）。

    @param aux 额外落标签侧 `g*.aux.npz`（**Java 与 C++ 两个生产者都支持**，且连 npz 容器
               一起**逐字节相同**：C++ 侧是 `npzwriter.hpp` + `TraceRecorder` 的标签侧行，
               判据 `node tools/trainer-aux-parity.mjs`；见本文件 :39-42 与
               `docs/TRAINER-CPP.md` §6.23）。⚠ 当前 `CPP_MISSING` 只剩 `--teacher-label`
               （DAgger 标注）这**一项**（:43-46）。
    """
    p = producer(name)
    guard_java_free(name)
    _guard(p, {"--sample": sample, "--teacher-label": teacher_label, "--aux": aux})
    if rotate_full and p != "cpp":
        raise SystemExit(f"生产者 {p!r} 没有 `--rotate-perm`（24 排列座位）：要求座次全排列平均的采集"
                         f"**必须**用 cpp 生产者（MAHJONG_PRODUCER=cpp）")
    if p == "java":
        cmd = ["java", "-jar", str(JAR), "--selfplay", str(games), "--workers", str(workers)]
    else:
        cmd = [str(TRAINER), "selfplay", str(games), "--workers", str(workers)]
    if rotate:
        cmd += ["--rotate"]
    if rotate_full:
        cmd += ["--rotate-perm"]
    if teacher_label:
        cmd += ["--teacher-label"]
    if aux:
        cmd += ["--aux"]
    cmd += ["--policy", policy, "--seed", str(seed), "--out", str(out_dir)]
    if hands:
        cmd += ["--hands", str(hands)]
    if sample:
        cmd += ["--sample", str(sample)]
    return cmd


def features_cmd(directory: Path, workers: int = 0, *, name: str | None = None) -> list[str]:
    """派生特征 sidecar（615/96 那些由服务端算的字段）。"""
    p = producer(name)
    guard_java_free(name)
    if p == "java":
        cmd = ["java", "-jar", str(JAR), "--features", str(directory)]
    else:
        cmd = [str(TRAINER), "features", str(directory)]
    if workers:
        cmd += ["--workers", str(workers)]
    return cmd
