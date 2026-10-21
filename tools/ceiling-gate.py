#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`ceiling-gate` —— **便宜 ceiling 闸门：一条命令 + 一份 `verdict.json`**（`BACKLOG.md` BL-03 / P3-09）。

要解决的事（`BACKLOG.md` §3 ③ 逐字）：
    「新臂在投训练之前必须先过 ceiling 闸门（墙钟 ≤30 min、零训练进程）；未过或测不出 ⇒ 不许开训」
在这之前，"新臂先过 ceiling"是**每次手搓**（照抄 `ROUTING-CEILING-COMMON.md` 那一批命令、自己拼配对、
自己算 MDE、自己看均值回归）⇒ 判据散在会话里、没法复判。本工具把那一串收口成**一条命令**。

⛔ **本工具一个统计量都不自己算**（`AGENTS.md` §6.5「⛔ 不要复制出第二套规则」）：
  · 逐场取值 / 配对 / 自助 CI / 符号检验 / 合并 / 随机效应 / 需求场次   ← `mahjong_ml.eval`
  · 加键合并 / 护栏行 / 判决行                                         ← `mahjong_ml.v4.gate`
  · 逐场序列 / 一对策略的读数 / 结论词                                 ← `mahjong_ml.v4.arena`
  本文件只做**三件**事：① 把"哪几个采集目录 = 哪几族牌山"翻译成上面那几个函数的输入；
  ② 补上**既有实现里没有**的三个读数 —— 实际 MDE（= 反解既有 `eval.required_n`）、均值回归 ρ、
  安慰剂对照；③ 拼成一份带**判决**与**口径声明**的 `verdict.json`。
  ⚠ **方向**不另立表：`eval.seat_values` 已把所有指标折成「正 = 更好」（`deals` 取负）⇒
  护栏一律走 `gate.guard_row` 的单侧规则（`ok ⇔ CI 下界 ≥ −tol`）。
  ⚠ **安慰剂口径**（2026-10-17 据 `MV1-STATIC-AUDIT.md` §1-5 修）：主口径若**结构性加和**
  （每场四席求和恒定，`rank_points`/`score` 为 0、`place` 为 −10）⇒ "非学生席同策略对照"
  的 Δ **≡ −Δ_student**，它量的是**再分配**而不是零假设 ⇒ `placebo.usable_for_veto=false`
  （只记账 `why_zero_sum`），并改用**非零和指标**（`wins`/`deals`）上的同类对照做零假设。
  旧口径可显式回退：`--legacy-placebo`（该开关打开时 `placebo` 与判决理由**逐字节不变**）。

三种口径（选一个）：

    # ① 复算既有闸门采集（**零采集**）：逐族读 <数据根>/gate/<tag>/s<seed>/summary.json
    python tools/ceiling-gate.py --tag p3pkg-B-g06-vs-defrep
    # ② 两条**既有**采集目录逐族配对（学生席口径；也可复算 style-vector.py 的产物）
    python tools/ceiling-gate.py --family f1=<base目录>;<cand目录> [--family f2=...]
    # ③ 新臂：自己采（≤--games 场/臂/族、--workers ≤8）+ 立刻判定（⛔ 不训练）
    python tools/ceiling-gate.py --collect --tag myarm --wall 53600001 --wall 53600002 ^
        --games 500 --base-net <现任 net.bin> --cand-net <新臂 net.bin> --workers 8 ^
        --table <def7 net.bin>,<win2 net.bin>,<atk3 net.bin>

自检（**离线**：造数据 + 负向必红；⛔ 不采集、不训练、不出网）：

    python tools/ceiling-gate.py --selftest

判决（逐字写进产物的 `verdict`）：

    过     ⇔ 主口径 **CI 下界 > 0** ∧ 所有**可用**护栏未越界 ∧ 安慰剂不显著 ∧ 族数 ≥ `--min-walls`
             ∧ 各族 Δ 逐族为正 ∧ |ρ| < 0.9 ∧ 墙钟 ≤ 预算 ∧ **测量有效性四条全过**（见下）。
    不过   ⇔ 主口径 **CI 上界 < 0**（候选在坏方向显著）。
    测不出 ⇔ 其余（CI 含 0 / 单族 / 安慰剂与主口径不可分辨 / ρ 报警 / 超预算 / SE 取不到）。
    ERROR  ⇔ **任何必需探针取不到**（目录 / `summary.json` / `per_game` / 权重文件 / 族数 0 / 进程扫描失败 …）。
    ⛔ fail-closed：**取不到就 ERROR 且退出码非 0**，⛔ 不许"取不到就当干净/当通过"。

⭐ **测量有效性硬前置（项目方 2026-10-19；原话与判据见 `lab/mv1/MEASUREMENT-VALIDITY.md`）**：
 ① **24 排列**：每族每臂 `A^4_4 = 24` 种全排列都要出现，且每种场次 ≥ `max(1, floor(n/24×0.8))`
    （`seat_perm`）。缺一 ⇒ **拒判（退出码 5）**。
 ② **共轭三条**：① candidate 与 baseline **绝不同桌**（`arm_isolation`）；② 对手池必须**冻结 + sha256
    白名单 + 非本族/本产品线**（`--opponent-pin`，`opponent_pool`）；③ 同支邻家顺位优势 ≤ 非同支
    邻家的 **1.2×**（`adjacency_conditional`，同支邻家胜率同样 ≤1.2×）。
 ③ **护栏**：`self_win_rate` / `deal_in_rate` / `meld_rate` / `avg_hand_value` /
    `adjacency_conditional` 必须进 `verdict.json`；`summary.json` 缺的字段记 `null` + `available:false`
    + 警告（⛔ 不伪造）。
 ④ **向后兼容**：旧证据没有四席身份（`per_game[].policies`）⇒ `seat_perm = null` + 判决里警告
    **"座次覆盖不可证"**，⛔ **不许当成通过**。

退出码：**0 = 过**；**3 = 不过**；**4 = 测不出**；**2 = ERROR**（用法/文件/探针坏）；
**5 = 测量有效性硬前置不满足**（座次覆盖 / 臂隔离 / 对手池 / 共轭嫌疑）——拒绝出判决（fail-closed）。
⛔ 本工具**不训练**：`--collect` 只跑自对弈采集（`style-vector.py` → `trainer.exe selfplay`），
并自带"零训练进程"自证（起跑/收尾各扫一次全部进程的**命令行**，命中任何训练特征 ⇒ ERROR）。

⭐ `--cuped-report`（**可选，缺省关**）：只在 `verdict.json` 额外写 `estimator.cuped_report`
（`β/SE/CI/VR/SE↓/等效 n/配对覆盖`），**判决仍用 O0**、⛔ 不改 PASS/FAIL/退出码；缺省路径一个键都不加。
协变量 = 基线臂同山同席结果；缺失/常数 ⇒ 该字段 `null` + 警告（失败只作用于该字段）。
"""
from __future__ import annotations

import argparse
import collections
import ctypes
import ctypes.wintypes as wt
import hashlib
import itertools
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
#: 数据根（环境变量优先；与 `w-ladder.py` / `gate.py` 同源，⛔ 不写死在别处）
DATA = Path(os.environ.get("MAHJONG_DATA_ROOT") or r"S:\mahjong-training")
GATE_DIR = DATA / "gate"
CEIL_STAGE = DATA / "w-ladder" / "ceiling-gate"
COLLECT_ROOT = DATA / "raw" / "ceiling-gate"

#: `python/` 与 venv 的 site-packages 都得进 `sys.path`（`w-ladder.py` 同一条；numpy 在 venv 里）
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "python" / ".venv" / "Lib" / "site-packages"))

import numpy as np                                                    # noqa: E402

from mahjong_ml import eval as ml_eval                                # noqa: E402
from mahjong_ml.v4 import arena as v4arena                            # noqa: E402
from mahjong_ml.v4 import gate as v4gate                              # noqa: E402


def _load_r2():
    """**复用** `overlap-orchestrator.py` 的同一份 R-2 准入判据（⛔ 不在这里写第二套口径）。

    `overlap-orchestrator.py` 没有 import 副作用（`main()` 在 `__main__` 守卫里）⇒ 直接加载。
    取不到就**抛**（fail-closed：没有准入判据的门 **不许** 退回"越线即杀"的绝对口径）。
    """
    import importlib.util
    p = Path(__file__).with_name("overlap-orchestrator.py")
    spec = importlib.util.spec_from_file_location("_ov_orch_r2", p)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"⛔ 载不到 {p}（R-2 准入判据的唯一实现）")
    mod = importlib.util.module_from_spec(spec)
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True          # ⛔ 不在 tools/ 里留 __pycache__
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = old
    for k in ("admission_check", "AdmissionGate", "IDLE_BASELINE_GB",
              "HARD_FLOOR_GB", "ADMISSION_MARGIN"):
        if not hasattr(mod, k):
            raise RuntimeError(f"⛔ {p} 里没有 {k}（R-2 判据被改名/删了）")
    return mod


_R2 = _load_r2()
admission_check = _R2.admission_check
IDLE_BASELINE_GB = _R2.IDLE_BASELINE_GB
HARD_FLOOR_GB = _R2.HARD_FLOOR_GB
ADMISSION_MARGIN = _R2.ADMISSION_MARGIN

SCHEMA = "ceiling-gate/1"
DEFAULT_BUDGET_MIN = 30.0            # BL-03 的硬判据：墙钟 ≤30 min
DEFAULT_METRIC = "rank_points"       # 与闸门主口径/护栏同一个指标名（`eval.METRICS` 之一）
DEFAULT_GUARDS = "wins:0.10,deals:0.10,win_points:150"   # = `gate.QUALITY_TOL`（预注册容忍带）
MAX_WORKERS = 8                      # 本任务纪律：`--workers ≤8`
MIN_COMMIT_FREE_GB = 8.0             # **旧口径**（只在 `--mem-kill` 打开时用；R-2 起缺省=准入控制）
QUIET_MIN_MB = 200.0                 # G7 静窗：python/java 只算"重"的（b1-overlap 既有判据）

#: ⭐ 测量有效性硬前置（项目方 2026-10-19）。判据本体见 `lab/mv1/MEASUREMENT-VALIDITY.md`。
SEAT_PERMS = tuple(itertools.permutations(range(4)))     # 4! = 24，字典序（与 C++ `kPerm24` 同序）
SEAT_MIN_FRACTION = 0.8              # 每种排列场次 ≥ max(1, floor(n/24 × 0.8))
ADJ_RATIO_MAX = 1.2                  # 同支邻家顺位/胜率优势 ≤ 非同支邻家的 1.2×
EXIT_MEASUREMENT_VALIDITY = 5        # 专用退出码：测量有效性拒判（fail-closed）

#: ★ **零训练进程**的判据 = **引擎/解释器镜像 ∧ 命令行特征**（两个条件同时成立才命中）。
#:   · 镜像必须是 `python*` / `trainer*` —— 本仓的训练入口都是 Python（`-m mahjong_ml.v4.loop`、
#:     `pretrain.py`、`p301_train.py`）。
#:   · ⚠ **为什么必须加镜像这一条**（实测）：单看命令行特征词会误报 —— DSH 自己的进程
#:     （`DeepSeek Harness.exe`）的命令行里带着**本会话的任务文本**，文本里就写着 `--grad-clip`
#:     `--kl-early-stop`（连"探测名单"这几个字都在里面）。判据要求"真的是个解释器进程在跑"。
#:   · 采集侧（`trainer.exe selfplay`）**不在**名单里 —— 那正是本工具允许做的事。
#:   · ⛔ 宁可误报（多报 ⇒ 人来看一眼；漏报 ⇒ "零训练"这句话本身成了假的）。
TRAINING_IMAGES = ("python", "python3", "pythonw", "trainer", "torchrun")
TRAINING_PATTERNS = (
    "mahjong_ml.v4.loop", "mahjong_ml.pretrain", "pretrain.py", "p301_train",
    "train.py", "--grad-clip", "--kl-early-stop", "--rwr-beta", "--target-minutes",
    "--ppo",
)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _say(s: str) -> None:
    print(s, flush=True)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _sha256(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _net_path(label: str) -> str:
    """策略串 → 权重文件路径（`net:<路径>@0#0.5` ⇒ `<路径>`；非 `net:` 返回空串）。"""
    s = str(label)
    return s[4:].split("@")[0].split("#")[0] if s.startswith("net:") else ""


class Probes:
    """探针账：每条都要 `ok` + `detail`（fail-closed 的骨架就是"必需探针里不许有红的"）。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, pid: str, ok: bool, detail: str, **kw) -> bool:
        row = {"id": pid, "ok": bool(ok), "detail": detail}
        row.update(kw)
        self.rows.append(row)
        _say(f"  [{'OK  ' if ok else 'FAIL'}] {pid}：{detail}")
        return bool(ok)

    def failures(self) -> list[dict]:
        return [r for r in self.rows if not r["ok"]]


# ---------------------------------------------------------------------------
# 系统读数：进程扫描（命令行 ⇒ 零训练自证 + G7 静窗）、提交余量
# ---------------------------------------------------------------------------
def scan_processes() -> tuple[list[dict], str]:
    """扫全部进程的 `(pid, name, cmdline, ws_mb)`；失败返回 `([], 原因)`。

    ⚠ 走 `Win32_Process.CommandLine`（**不是** `tasklist /FO CSV /V`）：本机（Win11 26200）
    `tasklist` 的详细模式**没有命令行列** ⇒ 那个写法会恒 False（`BACKLOG.md` BL-05 实测）。
    ⚠ 只在开始/收尾各调一次（高频 CIM 会卡住别的任务 —— `b1-overlap.py` 的既有教训）。
    """
    ps = ("Get-CimInstance Win32_Process | "
          "Select-Object ProcessId,ParentProcessId,Name,CommandLine,WorkingSetSize | "
          "ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as e:
        return [], f"{type(e).__name__}: {e}"
    if r.returncode != 0:
        return [], f"powershell 退出码 {r.returncode}：{(r.stderr or b'')[:200]!r}"
    txt = (r.stdout or b"").decode("utf-8", "replace").strip()
    if not txt:
        return [], "powershell 没有输出（取不到进程表）"
    try:
        raw = json.loads(txt)
    except ValueError as e:
        return [], f"进程表不是 JSON：{e}"
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for p in raw:
        out.append({"pid": int(p.get("ProcessId") or 0), "name": str(p.get("Name") or ""),
                    "ppid": int(p.get("ParentProcessId") or 0),
                    "cmdline": str(p.get("CommandLine") or ""),
                    "ws_mb": round(float(p.get("WorkingSetSize") or 0) / (1 << 20), 1)})
    return out, ""


def self_chain(procs: list[dict]) -> set:
    """**我自己这条启动链**（pid 的祖先 + 命令行里含本文件名的进程）。

    ⚠ 为什么必须显式排除：`--collect` 时本工具自己（以及拉起它的 shell / 编辑器）会出现在进程表里，
    而判据要问的是"**别人**有没有在训练"。排除的那一串会**原样记进** `no_training_proof.skipped_chain`
    （给人看），⛔ 不是"眼不见为净"的静默过滤。
    """
    out = {os.getpid(), os.getppid()}
    by_pid = {p["pid"]: p for p in procs}
    cur = os.getpid()
    for _ in range(16):                      # 祖先链（有环/取不到就停）
        nxt = (by_pid.get(cur) or {}).get("ppid") or 0
        if not nxt or nxt in out:
            break
        out.add(nxt)
        cur = nxt
    for p in procs:
        if "ceiling-gate.py" in p["cmdline"]:
            out.add(p["pid"])
    return out


def skip_chain_detail(procs: list[dict], skip: set) -> list:
    """被跳过的那条链的 `(pid, name, 命令行前 160 字)` —— 记进产物供人复核。"""
    return [{"pid": p["pid"], "name": p["name"], "cmdline": p["cmdline"][:160]}
            for p in procs if p["pid"] in skip]


def training_hits(procs: list[dict], skip: set) -> list[dict]:
    """**有训练在跑**吗？判据 = 镜像 ∈ `TRAINING_IMAGES` **且** 命令行命中 `TRAINING_PATTERNS`。"""
    out = []
    for p in procs:
        if p["pid"] in skip:
            continue
        if not p["name"].lower().startswith(TRAINING_IMAGES):
            continue
        cl = p["cmdline"].lower()
        if not cl:
            continue
        hit = [t for t in TRAINING_PATTERNS if t.lower() in cl]
        if hit:
            out.append({"pid": p["pid"], "name": p["name"], "patterns": hit,
                        "cmdline": p["cmdline"][:400]})
    return out


def quiet_foreign(procs: list[dict], skip: set[int]) -> list[dict]:
    """G7 静窗：`trainer.exe` **一律**算；`python/java` 只算工作集 ≥ `QUIET_MIN_MB` 的。"""
    out = []
    for p in procs:
        if p["pid"] in skip:
            continue
        n = p["name"].lower()
        if n == "trainer.exe" or (n.startswith(("python", "java")) and p["ws_mb"] >= QUIET_MIN_MB):
            out.append(p)
    return out


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def commit_free_gb() -> float:
    """**任务口径**的提交余量 = `Commit Limit − Committed Bytes`（与 B1/B2 起跑闸门同源）。

    Windows 上 Commit Limit = `ullTotalPageFile`、Committed = `ullTotalPageFile − ullAvailPageFile`
    ⇒ 余量就是 `ullAvailPageFile`。取不到 ⇒ NaN（调用处按"取不到"处理，⛔ 不当 0 也不当通过）。
    """
    try:
        st = _MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return float("nan")
        return float(st.ullAvailPageFile) / (1 << 30)
    except (AttributeError, OSError):
        return float("nan")


# ---------------------------------------------------------------------------
# 既有实现里**没有**的三个读数（其余一律调既有函数）
# ---------------------------------------------------------------------------
def mde_from_sd(sd: float, n: int, alpha: float = ml_eval.ALPHA,
                power: float = ml_eval.POWER_DEFAULT) -> float:
    """**实际 MDE** = 在已达样本量 `n` 上、α/功效给定下能检出的最小效应。

    ⛔ 判据是"**别自己写 z 公式**"：直接**反解**既有的 `eval.required_n(sd, delta)`
    （它已经钉死了 α=0.05、功效 80% 的通行口径，以及"50% 功效会把场次低估 2.04×"那条教训）。
    `required_n` 关于 delta 单调递减 ⇒ 二分；上界先翻倍找到可行点。
    """
    if not (n >= 2) or not math.isfinite(sd) or sd <= 0:
        return float("nan")
    lo, hi = 1e-9, max(1e-6, 4.0 * sd)
    for _ in range(80):
        if ml_eval.required_n(sd, hi, alpha, power) <= n:
            break
        hi *= 1.5
    else:
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if ml_eval.required_n(sd, mid, alpha, power) <= n:
            hi = mid
        else:
            lo = mid
    return float(hi)


def pearson_rho(dz: list, base: list) -> float:
    """**均值回归 ρ** = `ρ(Δ, 基准臂自身读数)`（Pearson）。

    ⚠ 诚实标注：ρ 在仓内**没有**既有实现（`ROUTING-CEILING-COMMON.md` §5 与
    `_probe-contrast-fit.py` 各算了一份，都在**数据根**）⇒ 这里用与 `eval.py` 同一个 numpy 的
    `corrcoef`（就是 Pearson 的定义，输入与上面那批"逐场配对"同一份）。
    判据：**配对差里的主导成分是不是"基准臂这一局倒不倒霉"** ⇒ |ρ| 大 ⇒ Δ 不可当技能读。
    """
    if len(dz) < 3 or len(dz) != len(base):
        return float("nan")
    a, b = np.asarray(dz, dtype=float), np.asarray(base, dtype=float)
    if a.std() <= 0 or b.std() <= 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def trimmed_deltas(dz: list, base: list, cuts=(0, 5, 10, 20, 30, 50)) -> dict:
    """**剔尾稳健性**（照 `ROUTING-CEILING-COMMON.md` §5 的手法）：剔掉基准最差 X% 再看 Δ。

    ⛔ 统计量仍走既有实现：把"留下的那些场"喂给 `eval.paired_test`（a = 逐场差、b = 全 0 序列）
    —— 它算出的 `mean` 就是这批场的 Δ 均值、`ci` 就是它的自助 CI（⛔ 没在这里重算均值/CI）。
    """
    if len(dz) < 10:
        return {}
    order = np.argsort(np.asarray(base, dtype=float))          # 基准从最差到最好
    out: dict = {}
    for cut in cuts:
        keep = order[int(len(order) * cut / 100.0):]
        if keep.size < 5:
            continue
        sa = {int(i): float(dz[int(i)]) for i in keep}
        sb = {int(i): 0.0 for i in keep}
        p = ml_eval.paired_test(sa, sb, "delta", "zero", "rank_points")
        out[f"drop_base_worst_{cut}pct"] = {"n": int(p.n), "delta": float(p.mean),
                                            "ci": [float(p.ci[0]), float(p.ci[1])]}
    return out


def mean_reversion(families: list, mode: str, metric: str) -> dict:
    """**均值回归检查**：Δ 里有多大成分是"基准臂这一局倒不倒霉"。

    `ρ = Pearson(Δ_g, 基准臂自身该场读数)`（仓内没有既有 ρ 实现 ⇒ 用与 `eval.py` 同一个 numpy）。

    ⛔★ **"可识别性"必须显式判**（本工具实测踩到并钉住的一条）：`gate-dir` 口径是 **2+2 对开**，
    两臂在同一场里**互为补集**；`rank_points` 这类**零和**指标下 `cand_g + base_g ≡ const`
    ⇒ `Δ_g = cand_g − base_g` 与 `base_g` **机械地** −1 相关（实测 ρ = −1.000）——
    那不是"均值回归证据"，是**设计恒等式**。判据：`sd(cand_g + base_g) ≈ 0`（零和 ⇒ 恒定）
    或 `|ρ| ≥ 0.9999` ⇒ `identified = false`，**本条不进判据**（照实记进产物供人复核）。
    **可识别**的 ρ 只有 `family` 口径：同一个座位、同一副牌山、只换权重 ⇒ 两臂读数不是互补关系
    （`ROUTING-CEILING-COMMON.md` §5 的 −0.531、`PROBE-CONTRAST-RESULT.md` §4 的 −0.694 都是这种）。
    """
    dz, base, total = [], [], []
    for f in families:
        for s in sorted(set(f["a"]) & set(f["b"])):
            a, b = float(f["a"][s]), float(f["b"][s])
            dz.append(a - b)
            base.append(b)
            total.append(a + b)
    rho = pearson_rho(dz, base)
    sd_sum = float(np.std(np.asarray(total, dtype=float), ddof=1)) if len(total) > 1 else float("nan")
    zero_sum = bool(math.isfinite(sd_sum) and sd_sum <= 1e-9)
    identified = not (zero_sum or (math.isfinite(rho) and abs(rho) >= 0.9999))
    why = ("可识别（同一座位、同一副牌山、只换权重）" if identified else
           "**不可识别**："
           + ("两臂逐场合计恒定（零和指标 + 2+2 对开 ⇒ 互为补集）" if zero_sum
              else "|ρ|≥0.9999（机械互补）")
           + " ⇒ ρ 是设计恒等式，本条不作判据")
    return {"rho": rho, "n": len(dz), "identified": identified, "unidentified_why": why,
            "sd_of_sum": sd_sum, "mode": mode, "metric": metric,
            "method": "Pearson(Δ, 基准臂自身读数) · numpy.corrcoef（仓内无既有 ρ）",
            "trimmed": trimmed_deltas(dz, base),
            "trim_note": ("剔基准最差 X% 在 2+2 零和对开口径下**也是机械的**（基准最差 ⇔ 候选最好）"
                          "⇒ 只作读数、不作判据" if not identified else "剔尾读数（照 ROUTING §5 手法）")}


# ---------------------------------------------------------------------------
# 族级读数 / 合并（一律走既有函数）
# ---------------------------------------------------------------------------
def _paired_from_series(name: str, sa: dict, sb: dict, metric: str) -> dict:
    """一族：`eval.paired_test`（既有）⇒ 该族 Δ / sd / n；`se = sd/√n`（`pool_diffs` 的同一个式子）。"""
    p = ml_eval.paired_test(sa, sb, name, name, metric)
    se = float(p.sd) / math.sqrt(p.n) if (p.n > 1 and math.isfinite(p.sd)) else float("nan")
    return {"family": name, "n": int(p.n), "delta": float(p.mean), "se": se, "sd": float(p.sd),
            "win": int(p.win), "lose": int(p.lose), "tie": int(p.tie),
            "ci": [float(p.ci[0]), float(p.ci[1])]}


def _pooled(series: list, a: str, b: str, metric: str):
    """多族合并 = 既有 `gate.pooled_diffs`（加键偏移）→ 既有 `arena.judge_pair`。"""
    p_sa, p_sb = v4gate.pooled_diffs(series)
    return v4arena.judge_pair(p_sa, p_sb, a, b, metric), (p_sa, p_sb)


def _student_label(run, probes: Probes, where: str):
    """学生席判据 = 策略串里带 `@`（与 `w-ladder.py` 的 `student_row` 同一条，⛔ 不用 `startswith('net:')`）。"""
    labs = sorted({lab for g in run.per_game for lab in g.get("policies", [])})
    hit = [l for l in labs if "@" in l]
    if len(hit) != 1:
        probes.add(f"student:{where}", False, f"带 `@` 的学生席命中 {len(hit)} 个：{hit or labs}")
        return ""
    probes.add(f"student:{where}", True, hit[0])
    return hit[0]


def _read_gate_families(tag_dir: Path, inc_sub: str, cand_sub: str, metric: str,
                        guard_specs: list, probes: Probes) -> tuple[list, dict]:
    """`--gate-dir`：逐族读 `<tag>/s<seed>/summary.json`（**零采集**）。"""
    dirs = sorted([d for d in tag_dir.glob("s*") if (d / "summary.json").is_file()],
                  key=lambda p: p.name)
    probes.add("families", bool(dirs),
               f"{tag_dir} 下 {len(dirs)} 个带 summary.json 的族目录：{[d.name for d in dirs]}")
    fams: list = []
    meta: dict = {"label_sub_incumbent": inc_sub, "label_sub_candidate": cand_sub, "tag_dir": str(tag_dir)}
    metrics = [metric] + [g for g, _ in guard_specs]
    for d in dirs:
        run = ml_eval.load_run(d)                       # ⛔ 缺 per_game/rank_points 会当场抛
        inc = ml_eval.resolve_label(run, inc_sub)       # ⛔ 命中不唯一会当场抛
        cand = ml_eval.resolve_label(run, cand_sub)
        if inc == cand:
            probes.add(f"arms:{d.name}", False, f"incumbent/candidate 解析到同一个策略：{inc}")
            continue
        fam = {"name": d.name, "seed_base": run.seed_base, "by_metric": {}, "_run": run}
        for m in metrics:
            fam["by_metric"][m] = {"a": ml_eval.per_game_series(run, cand, m),   # 候选
                                   "b": ml_eval.per_game_series(run, inc, m)}    # 基准
        fam["a"], fam["b"] = fam["by_metric"][metric]["a"], fam["by_metric"][metric]["b"]
        fam["policies"] = {"incumbent": inc, "candidate": cand,
                           "incumbent_net": _net_path(inc), "candidate_net": _net_path(cand),
                           "games": run.games}
        common = sorted(set(fam["a"]) & set(fam["b"]))
        probes.add(f"family:{d.name}:pairing", len(common) == len(fam["a"]) == len(fam["b"]),
                   f"逐场 seed 对齐 {len(common)}/{len(fam['a'])}（候选）/ {len(fam['b'])}（基准）")
        fams.append(fam)
    if fams:
        meta["policies"] = fams[0]["policies"]
    return fams, meta


def _read_family_specs(specs: list, metric: str, guard_specs: list,
                       probes: Probes) -> tuple[list, dict]:
    """`--family 标签=<base目录>;<cand目录>`：两条采集 ⇒ **学生席**口径逐族配对。"""
    fams: list = []
    meta: dict = {}
    metrics = [metric] + [g for g, _ in guard_specs]
    for spec in specs:
        label, _, pair = spec.partition("=")
        base_s, _, cand_s = pair.partition(";")
        base_d, cand_d = Path(base_s.strip()), Path(cand_s.strip())
        ok = bool(label) and base_d.is_dir() and cand_d.is_dir()
        probes.add(f"family:{label or spec}", ok,
                   f"base={base_d} cand={cand_d}")
        if not ok:
            continue
        rb, rc = ml_eval.load_run(base_d), ml_eval.load_run(cand_d)
        lb = _student_label(rb, probes, f"{label}/base")
        lc = _student_label(rc, probes, f"{label}/cand")
        if not (lb and lc):
            continue
        if _net_path(lb) and _net_path(lb) == _net_path(lc):
            probes.add(f"family:{label}:distinct", False,
                       f"两臂解析到**同一个权重**（{_net_path(lb)}）—— 没有比较对象")
            continue
        fam = {"name": label, "seed_base": rc.seed_base, "by_metric": {},
               "_base_run": rb, "_cand_run": rc,
               "policies": {"base": lb, "cand": lc, "base_net": _net_path(lb),
                            "cand_net": _net_path(lc), "games": rc.games}}
        for m in metrics:
            fam["by_metric"][m] = {"a": ml_eval.per_game_series(rc, lc, m),
                                   "b": ml_eval.per_game_series(rb, lb, m)}
        fam["a"], fam["b"] = fam["by_metric"][metric]["a"], fam["by_metric"][metric]["b"]
        common = sorted(set(fam["a"]) & set(fam["b"]))
        probes.add(f"family:{label}:pairing", len(common) > 0,
                   f"公共场 {len(common)}（候选 {len(fam['a'])} / 基准 {len(fam['b'])}）")
        fams.append(fam)
    if fams:
        meta["policies"] = fams[0]["policies"]
    return fams, meta


def weight_probe(families: list, probes: Probes) -> list:
    """权重存在性（必需探针）：**取不到 ⇒ 调用处 ERROR**，⛔ 不许"取不到就当干净"。

    存在的同时把 `sha256` 一起 pin 下来（5.6 MB/个，成本可忽略）—— 报告里"哪个权重"就不是路径猜的了。
    """
    weights: dict = {}
    for f in families:
        for k in ("incumbent_net", "candidate_net", "base_net", "cand_net"):
            p = (f.get("policies") or {}).get(k)
            if p:
                weights[p] = Path(p).is_file()
    if not weights:
        probes.add("weights_exist", False, "一个权重路径都没解析出来（策略串里没有 `net:`？）")
        return ["<没有可核的权重路径>"]
    miss = [p for p, ok in weights.items() if not ok]
    pins = {p: _sha256(p) for p, ok in weights.items() if ok}
    probes.add("weights_exist", not miss,
               f"{len(weights)} 个权重全部在盘上（已 pin sha256）" if not miss
               else f"**找不到**：{miss}", sha256=pins)
    if families:
        families[0].setdefault("policies", {})["weight_sha256"] = pins
    return miss


# ---------------------------------------------------------------------------
# ⭐ 测量有效性硬前置（项目方 2026-10-19 两条要求）
#   · 24 排列覆盖（seat_perm）
#   · 共轭三条：candidate≠baseline 同桌 / 对手池冻结且非本族 / 同支邻家优势 ≤1.2×
#   · 新护栏：self_win_rate / deal_in_rate / meld_rate / avg_hand_value / adjacency_conditional
# 全部 **fail-closed**：取不到 / 不可证 ⇒ `measurement_validity.ok=false` ⇒ judge 退出码 5。
# ---------------------------------------------------------------------------
def _policy_line(label_or_path: str) -> str:
    """策略串 / 权重路径 → **支线标识**（启发式；`--opponent-pin` 的 `line` 优先）。

    只做三件事：取权重所在目录名；去掉代际/版本后缀（`-g10` / `-v3`）；去掉变体后缀
    （`-BASE` / `-cand` / `-AUX03`）。例：`mv1-s24-BASE` 与 `mv1-s24-AUX03` ⇒ `mv1-s24`
    （同一支）；`v4-expert-def7-g10` ⇒ `v4-expert-def7`（不同风格 = 不同支）。
    ⚠ 这是**结构性检测**，不是训练血统的证明；所以对手池还必须过 sha256 白名单。
    """
    p = str(label_or_path or "")
    if p.startswith("net:"):
        p = _net_path(p)
    name = Path(p).parent.name if p else ""
    if not name:
        name = Path(p).stem if p else ""
    name = re.sub(r"-(?:g|G)\d+$", "", name)
    name = re.sub(r"-(?:v|V)\d+$", "", name)
    name = re.sub(r"-(?:BASE|base|CAND|cand|AUX\d*)$", "", name)
    return name


def _family_runs_tagged(f: dict) -> list:
    """一族里可核的采集：`--gate-dir` 一条；`--family` 基准/候选各一条。"""
    out = []
    if f.get("_run") is not None:
        out.append(("gate-dir", f["_run"]))
    if f.get("_base_run") is not None:
        out.append(("base", f["_base_run"]))
    if f.get("_cand_run") is not None:
        out.append(("cand", f["_cand_run"]))
    return out


def _arm_labels_for(f: dict) -> tuple[str, str]:
    """`(candidate, baseline)` 的完整策略标签（两种口径的键名统一）。"""
    pol = f.get("policies") or {}
    if "candidate" in pol or "incumbent" in pol:
        return str(pol.get("candidate") or ""), str(pol.get("incumbent") or "")
    return str(pol.get("cand") or ""), str(pol.get("base") or "")


def _seat_perm_for_run(run, arm_label: str, family: str) -> dict:
    """一条采集的四席排列覆盖：**A^4_4=24** 种全排列 + 每种场次下限。

    判据（`AGENTS.md` §6.5 判第 24 条）：
      · `achievable` = 桌上**不同策略**数的多重集排列数；< 24 ⇒ 永远凑不出 24 ⇒ unusable
        （例：`a,a,b,b` 的 2+2 对开最多 6 种，**不是**项目方要的 4 个模型各坐一次）。
      · `coverage = k/24`，`k` = 实际出现的排列数；
      · `min_count_required = max(1, floor(n_games/24 × 0.8))` —— 0.8 的理由：24 种排列里
        允许"场数不是 24 的整数倍"带来的余数抖动，但不允许某一种只出现 1–2 次就被均值掩盖；
        用 floor 而不是 round，保证小样本（n<24）也不会把门槛降成 0（`max(1, …)`）。
      · `usable ⇔ achievable==24 ∧ k==24 ∧ min(counts) ≥ min_count_required`。
    """
    pg = list(run.per_game or [])
    counts: dict = {}
    missing = 0
    for g in pg:
        pols = g.get("policies")
        if not pols or len(pols) != 4:
            missing += 1
            continue
        key = tuple(str(x) for x in pols)
        counts[key] = counts.get(key, 0) + 1
    base = {"family": family, "arm": arm_label, "n_games": sum(counts.values()),
            "expected": 24, "min_fraction": SEAT_MIN_FRACTION,
            "min_count_required": max(1, int(math.floor(sum(counts.values()) / 24.0
                                                         * SEAT_MIN_FRACTION)))}
    if not counts:
        base.update({"available": False, "usable": False, "coverage": "0/24", "coverage_k": 0,
                     "counts": {}, "min": 0, "max": 0, "achievable": 0,
                     "reason": "per_game 没有四席身份（policies）⇒ 座次覆盖不可证（旧证据）"})
        return base
    first = next(iter(counts))
    roster = list(first)
    n_distinct = len(set(first))
    mult = collections.Counter(first)
    achievable = math.factorial(4) // math.prod(math.factorial(v) for v in mult.values())
    mismatch = sum(1 for k in counts if set(k) != set(roster))
    if n_distinct == 4 and mismatch == 0:
        all_perms = {p: 0 for p in SEAT_PERMS}
        for key, c in counts.items():
            all_perms[tuple(roster.index(x) for x in key)] += c
        counts_out = {",".join(str(i) for i in p): v for p, v in sorted(all_perms.items())}
        k_obs = sum(1 for v in all_perms.values() if v > 0)
        mn, mx = min(all_perms.values()), max(all_perms.values())
    else:
        #: 2 个（或 3 个）不同策略：只能按**实际标签元组**记账；`achievable < 24` ⇒ unusable
        counts_out = {"|".join(k): v for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}
        k_obs = len(counts)
        mn, mx = min(counts.values()), max(counts.values())
    usable = bool(achievable == 24 and k_obs == 24 and mismatch == 0
                  and mn >= base["min_count_required"])
    if usable:
        why = (f"{k_obs}/24；每种 {mn}–{mx} 场 ≥ 门槛 {base['min_count_required']}"
               f"（n={base['n_games']}）")
    elif achievable < 24:
        why = (f"桌上只有 {n_distinct} 个不同策略 ⇒ 最多 {achievable} 种排列，"
               f"凑不出 A^4_4=24（实测 {k_obs}/24）")
    elif mismatch:
        why = f"{mismatch} 场的四席名单不一致 ⇒ 排列不可比"
    elif k_obs < 24:
        why = f"只出现 {k_obs}/24 种排列（缺 {24 - k_obs} 种）"
    else:
        why = f"最少的一种只出现 {mn} 场 < 门槛 {base['min_count_required']}"
    base.update({"available": True, "usable": usable, "roster": roster,
                 "n_distinct": n_distinct, "achievable": achievable,
                 "coverage": f"{k_obs}/24", "coverage_k": k_obs,
                 "coverage_ratio": k_obs / 24.0, "counts": counts_out,
                 "min": mn, "max": mx, "mismatch": mismatch, "reason": why})
    if missing:
        base["reason"] = f"{why}；另有 {missing} 场缺四席身份"
        base["usable"] = False
    return base


def seat_perm_report(families: list) -> dict:
    """每族每臂的 24 排列覆盖（顶层字段 = 项目方要求的那四个）。"""
    arms = []
    for f in families:
        cand, base = _arm_labels_for(f)
        for arm, run in _family_runs_tagged(f):
            lab = cand if arm in ("cand", "candidate", "gate-dir") else base
            arms.append(_seat_perm_for_run(run, lab or arm, str(f.get("name") or "?")))
    if not arms:
        return {"schema": "seat-perm/1", "expected": 24, "min_fraction": SEAT_MIN_FRACTION,
                "coverages": [], "coverage": "0/24", "coverage_k": 0, "counts": {},
                "min": 0, "max": 0, "available": False, "usable": False,
                "reason": "没有可核的采集（seat_perm=null）⇒ 座次覆盖不可证"}
    kmin = min(int(a.get("coverage_k") or 0) for a in arms)
    mn = min(int(a.get("min") or 0) for a in arms)
    mx = max(int(a.get("max") or 0) for a in arms)
    avail = any(bool(a.get("available")) for a in arms)
    usable = all(bool(a.get("usable")) for a in arms)
    bad = [a for a in arms if not a.get("usable")]
    return {"schema": "seat-perm/1", "expected": 24, "min_fraction": SEAT_MIN_FRACTION,
            "min_count_rule": "max(1, floor(n_games/24 × 0.8))",
            "coverage": f"{kmin}/24", "coverage_k": kmin,
            "counts": {f"{a['family']}/{a['arm']}": a.get("counts") for a in arms},
            "min": mn, "max": mx, "arms": arms, "available": avail, "usable": usable,
            "reason": ("每族每臂均 24/24 且每种场次达标"
                       if usable else "；".join(f"{a['family']}/{a['arm']}：{a.get('reason')}"
                                              for a in bad))}


def arm_isolation_report(families: list) -> dict:
    """硬前置 ①：candidate 与 baseline **绝不同桌**（同一场里只能有一个被测臂）。"""
    rows = []
    for f in families:
        cand, base = _arm_labels_for(f)
        co = n = 0
        no_id = 0
        for _arm, run in _family_runs_tagged(f):
            for g in run.per_game or []:
                n += 1
                pols = [str(x) for x in (g.get("policies") or [])]
                if not pols:
                    no_id += 1
                    continue
                if cand and base and cand in pols and base in pols:
                    co += 1
        ok = bool(cand and base and n > 0 and no_id == 0 and co == 0)
        rows.append({"family": str(f.get("name") or "?"), "candidate": cand, "baseline": base,
                     "n_games": n, "cooccur": co, "no_identity": no_id, "ok": ok,
                     "reason": ("candidate 与 baseline 从未同桌" if ok else
                                (f"{co}/{n} 场 candidate 与 baseline 同桌" if co else
                                 ("四席身份不可证" if no_id else "臂标签缺失")))})
    ok = bool(rows) and all(r["ok"] for r in rows)
    return {"schema": "arm-isolation/1", "families": rows, "ok": ok,
            "reason": ("candidate 与 baseline 从未同桌" if ok else
                       "；".join(r["reason"] for r in rows if not r["ok"]))}


def _load_opponent_pin(pin_path: str) -> tuple[dict | None, str]:
    if not pin_path:
        return None, "未提供 --opponent-pin（对手冻结与 sha256 白名单不可证）"
    p = Path(str(pin_path))
    if not p.is_file():
        return None, f"pin 文件不存在：{p}"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"pin 文件读不了：{type(e).__name__}: {e}"
    if d.get("schema") != "opponent-pin/1":
        return None, f"pin schema={d.get('schema')!r} ≠ opponent-pin/1"
    if d.get("frozen") is not True:
        return None, "pin 未声明 `frozen: true`"
    allowed = d.get("allowed")
    if not isinstance(allowed, dict) or not allowed:
        return None, "pin 的 `allowed` 为空/不是对象"
    norm = {}
    for sha, entry in allowed.items():
        s = str(sha).strip().lower()
        if len(s) != 64 or any(c not in "0123456789abcdef" for c in s):
            return None, f"pin 里不是 sha256：{sha!r}"
        norm[s] = dict(entry or {})
    return {"path": str(p), "frozen": True, "frozen_at": d.get("frozen_at"),
            "allowed": norm, "note": d.get("note")}, ""


def opponent_pool_report(families: list, pin_path: str) -> dict:
    """硬前置 ②：对手池**冻结 + sha256 白名单 + 非本族/本产品线**。

    · `whitelisted`：对手 sha256 必须逐字出现在 `--opponent-pin` 的 `allowed` 里；
    · `same_branch`：对手 sha 等于任一被测臂，或对手 `line`（pin 优先，否则路径启发式）
      等于被测臂的 `line` ⇒ 拒跑（"同支模型混入对手池"）；
    · 没有 pin / pin 未 frozen / 有一项不在白名单 ⇒ `ok=false`（fail-closed）。
    """
    pin, why = _load_opponent_pin(pin_path)
    arm_paths = set()
    arm_shas = set()
    armlines = set()
    opp_labels: dict[str, str] = {}
    for f in families:
        cand, base = _arm_labels_for(f)
        for lab in (cand, base):
            p = _net_path(lab)
            if p:
                arm_paths.add(str(Path(p)))
                if Path(p).is_file():
                    arm_shas.add(_sha256(p))
                armlines.add(_policy_line(p))
        for _arm, run in _family_runs_tagged(f):
            for g in run.per_game or []:
                for lab in (g.get("policies") or []):
                    lab = str(lab)
                    if lab and lab not in (cand, base):
                        opp_labels.setdefault(lab, _net_path(lab))
    opps = []
    for lab, p in sorted(opp_labels.items()):
        sha = _sha256(p) if (p and Path(p).is_file()) else None
        entry = (pin or {}).get("allowed", {}).get(sha or "", {})
        line = str(entry.get("line") or _policy_line(p or lab))
        whitelisted = bool(sha and pin and sha in pin["allowed"])
        same_branch = bool((sha and sha in arm_shas)
                           or (line and line in armlines)
                           or (p and str(Path(p).parent) in {str(Path(x).parent) for x in arm_paths}))
        opps.append({"label": lab, "path": p, "sha256": sha, "line": line,
                     "name": entry.get("name") or (Path(p).parent.name if p else lab),
                     "whitelisted": whitelisted, "same_branch": same_branch})
    ok = bool(pin and pin.get("frozen") and opps
              and all(o["whitelisted"] for o in opps)
              and not any(o["same_branch"] for o in opps))
    reasons = []
    if why:
        reasons.append(why)
    if not opps:
        reasons.append("没有解析出对手权重（对手池为空）")
    for o in opps:
        if not o["whitelisted"]:
            reasons.append(f"对手不在白名单：{o['name']} sha={str(o['sha256'])[:12]}…")
        if o["same_branch"]:
            reasons.append(f"对手属本族/本产品线：{o['name']} line={o['line']}")
    return {"schema": "opponent-pin-check/1", "pin": (pin or {}).get("path"),
            "frozen": bool(pin and pin.get("frozen")), "frozen_at": (pin or {}).get("frozen_at"),
            "whitelist_size": len((pin or {}).get("allowed") or {}),
            "arm_shas": sorted(arm_shas), "arm_lines": sorted(armlines),
            "opponents": opps, "ok": ok,
            "reason": ("对手池已冻结、全部 sha256 在白名单、且无同支模型"
                       if ok else "；".join(reasons) or "对手池不可证")}


def _mean(xs: list) -> float | None:
    return float(sum(xs)) / len(xs) if xs else None


def _arm_guard_values(run, label: str) -> dict:
    """一条采集里某臂的护栏原料。`summary.json` 没有的字段**明确记 null**。

    v2 口径（2026-10-21）：`by_policy` 若带 `tsumo` / `meld` / `wins`，则
    `self_win_rate = tsumo / wins`、`meld_rate = meld / hands`（`hands` = `seat_hands`）。
    ⛔ 旧 summary（v1，没有这三个字段）**逐位保持原行为**：两列仍 null + 原警告。
    """
    bp = (run.by_policy or {}).get(label) or {}
    hands = sum(int(g.get("hands") or 0) for g in run.per_game or [])
    wins = deals = win_points = 0.0
    for g in run.per_game or []:
        pols = g.get("policies") or []
        for i, lab in enumerate(pols):
            if str(lab) != label:
                continue
            wins += float((g.get("wins") or [0] * 4)[i])
            deals += float((g.get("deals") or [0] * 4)[i])
            win_points += float((g.get("win_points") or [0] * 4)[i])
    deal_in = bp.get("deal_in_rate")
    if deal_in is None and hands:
        deal_in = deals / hands
    avg_val = bp.get("avg_win_score")
    if avg_val is None and wins:
        avg_val = win_points / wins

    # ---- v2：自摸率 / 副露率（缺字段 ⇒ None，绝不拿 win_rate 冒充）
    tsumo = bp.get("tsumo")
    meld = bp.get("meld")
    # per_game 兜底：只有当**每一场**都带该数组时才求和（缺一场就记 None，不猜）
    def _sum_per_game(key: str) -> float | None:
        gs = run.per_game or []
        if not gs:
            return None
        total = 0.0
        for g in gs:
            arr = g.get(key)
            if not isinstance(arr, list) or len(arr) != 4:
                return None
            idxs = [i for i, lab in enumerate(g.get("policies") or []) if str(lab) == label]
            if not idxs:
                return None
            total += sum(float(arr[i]) for i in idxs)
        return total

    if tsumo is None:
        tsumo = _sum_per_game("tsumo")
    if meld is None:
        meld = _sum_per_game("meld")
    wins_bp = bp.get("wins")
    if wins_bp is None:
        wins_bp = wins if wins > 0 else None
    # ⚠ `meld_rate` 的分母必须是**该策略的** `seat_hands`，不是 `sum(per_game.hands)`
    #   （后者是"四席合计"的表级小局数；多标签同桌时会把分母放大 4 倍）。
    seat_hands = int(bp.get("seat_hands") or 0)
    meld_den = seat_hands if seat_hands > 0 else hands
    self_win = (float(tsumo) / float(wins_bp)) if (tsumo is not None and wins_bp) else None
    meld_rate = (float(meld) / float(meld_den)) if (meld is not None and meld_den) else None
    return {"self_win_rate": self_win,
            "deal_in_rate": float(deal_in) if deal_in is not None else None,
            "meld_rate": meld_rate,
            "avg_hand_value": float(avg_val) if avg_val is not None else None}


def measurement_guards(families: list) -> dict:
    """护栏指标（**必须进 verdict.json**）：缺字段记 null + available:false + 警告。"""
    rows = []
    for f in families:
        cand, base = _arm_labels_for(f)
        runs = _family_runs_tagged(f)
        c_run = next((r for a, r in runs if a == "cand"), runs[-1][1] if runs else None)
        b_run = next((r for a, r in runs if a == "base"), runs[-1][1] if runs else None)
        rows.append({"family": str(f.get("name") or "?"),
                     "candidate": _arm_guard_values(c_run, cand) if c_run and cand else {},
                     "baseline": _arm_guard_values(b_run, base) if b_run and base else {}})
    def agg(key, arm="candidate"):
        vals = [r[arm].get(key) for r in rows if r.get(arm)]
        vals = [float(v) for v in vals if v is not None]
        return _mean(vals)
    out = {"schema": "measurement-guards/1", "families": rows,
           "candidate": {"self_win_rate": agg("self_win_rate"),
                         "deal_in_rate": agg("deal_in_rate"),
                         "meld_rate": agg("meld_rate"),
                         "avg_hand_value": agg("avg_hand_value")},
           "baseline": {"self_win_rate": agg("self_win_rate", "baseline"),
                        "deal_in_rate": agg("deal_in_rate", "baseline"),
                        "meld_rate": agg("meld_rate", "baseline"),
                        "avg_hand_value": agg("avg_hand_value", "baseline")},
           "available": {"self_win_rate": False, "deal_in_rate": False,
                         "meld_rate": False, "avg_hand_value": False},
           "notes": []}
    # `available` = **本轮候选臂真的取到了**（旧 summary 全 null ⇒ 全 false，与改动前一致）
    for _k in out["available"]:
        out["available"][_k] = out["candidate"].get(_k) is not None
    # ⚠ `available` 只报**本轮读数是否拿得到**（旧 summary 仍然 false ⇒ 与改动前逐位相同）
    if out["candidate"]["self_win_rate"] is None:
        notes = out["notes"]
        notes.append("self_win_rate 取不到：trainer summary.json 只记 wins（荣和+自摸合计数），"
                     "无 tsumo 计数 ⇒ 记 null（⛔ 不拿 wins/hands 冒充自摸率）")
    else:
        out["notes"].append("self_win_rate = by_policy.tsumo / by_policy.wins"
                            "（同一策略在所有小局×席位上的自摸和了数 / 和了数）")
    if out["candidate"]["meld_rate"] is None:
        out["notes"].append("meld_rate 取不到：summary.json 无副露计数 ⇒ 记 null（不重采；"
                            "要该轴须在新采集摘要里加字段）")
    else:
        out["notes"].append("meld_rate = by_policy.meld / by_policy.seat_hands"
                            "（至少有一副副露的小局×席位数 / 打过的小局×席位数；"
                            "meld 含暗杠，open_meld 单列在 summary.by_policy）")
    if out["candidate"]["deal_in_rate"] is not None:
        out["notes"].append("deal_in_rate / avg_hand_value 取自 by_policy 的 deal_in_rate / "
                            "avg_win_score（与 per_game 逐项对账）；avg_hand_value 含本场棒/供託")
    return out


def _adj_ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or not math.isfinite(a) or not math.isfinite(b) or b <= 0:
        return None
    return a / b


def _adj_row(run, label: str, family: str) -> dict:
    """同支邻家 vs 非同支邻家的候选读数（顺位点 + 和了次数）。

    `same` = 候选的**任一邻家**（`i±1 mod 4`）与其同一支线（`_policy_line`）。
    判据：`adv_same ≤ 1.2 × adv_other`；`adv_other ≤ 0` 时比值无意义 ⇒ 只要
    `adv_same > adv_other` 就判嫌疑（fail-closed，不给"负数比值"钻空子）。
    """
    line = _policy_line(_net_path(label) or label)
    y_s, y_o, w_s, w_o = [], [], [], []
    for g in run.per_game or []:
        pols = [str(x) for x in (g.get("policies") or [])]
        if len(pols) != 4:
            continue
        for i, lab in enumerate(pols):
            if lab != label:
                continue
            nb = [pols[(i - 1) % 4], pols[(i + 1) % 4]]
            same = any(_policy_line(_net_path(x) or x) == line for x in nb)
            (y_s if same else y_o).append(float((g.get("rank_points") or [0] * 4)[i]))
            (w_s if same else w_o).append(float((g.get("wins") or [0] * 4)[i]))
    adv_s, adv_o = _mean(y_s), _mean(y_o)
    win_s, win_o = _mean(w_s), _mean(w_o)
    r_rank = _adj_ratio(adv_s, adv_o)
    r_win = _adj_ratio(win_s, win_o)
    suspect_rank = bool((r_rank is not None and r_rank > ADJ_RATIO_MAX)
                        or (r_rank is None and adv_s is not None and adv_o is not None
                            and adv_s > adv_o))
    suspect_win = bool((r_win is not None and r_win > ADJ_RATIO_MAX)
                       or (r_win is None and win_s is not None and win_o is not None
                           and win_s > win_o))
    available = bool(y_s) and bool(y_o)
    vacuous = bool(y_o) and not y_s
    ok = bool((not suspect_rank and not suspect_win) and (available or vacuous))
    if vacuous:
        why = ("桌上没有同支邻家（对手池全行外）⇒ 无可测的同支邻家优势；"
               "由 opponent_pool.same_branch=false 保证")
    elif not y_o:
        why = "所有场次都有同支邻家 ⇒ 无非同支对照，无法证明 ≤1.2×"
    elif suspect_rank or suspect_win:
        why = (f"**共轭嫌疑**：同支邻家顺位优势 {adv_s:+.3f} vs 非同支 {adv_o:+.3f}"
               f"（ratio={r_rank if r_rank is None else round(r_rank, 3)}，上限 {ADJ_RATIO_MAX}×）"
               if suspect_rank else
               f"**共轭嫌疑**：同支邻家胜率 {win_s:.3f} vs 非同支 {win_o:.3f}"
               f"（ratio={r_win if r_win is None else round(r_win, 3)}，上限 {ADJ_RATIO_MAX}×）")
    else:
        why = (f"同支邻家顺位优势 {adv_s:+.3f} vs 非同支 {adv_o:+.3f}"
               f"（ratio={r_rank if r_rank is None else round(r_rank, 3)} ≤ {ADJ_RATIO_MAX}×）")
    return {"family": family, "arm": label, "line": line,
            "n_same": len(y_s), "n_other": len(y_o),
            "adv_same": adv_s, "adv_other": adv_o, "rank_ratio": r_rank,
            "win_same": win_s, "win_other": win_o, "win_ratio": r_win,
            "available": available, "vacuous": vacuous,
            "suspect": bool(suspect_rank or suspect_win), "ok": ok, "reason": why}


def adjacency_conditional(families: list) -> dict:
    """`adjacency_conditional`：按"邻家是否同支"分组的候选顺位点/胜率差。"""
    rows = []
    for f in families:
        cand, _base = _arm_labels_for(f)
        runs = _family_runs_tagged(f)
        c_run = next((r for a, r in runs if a == "cand"), runs[-1][1] if runs else None)
        if c_run is not None and cand:
            rows.append(_adj_row(c_run, cand, str(f.get("name") or "?")))
    ok = bool(rows) and all(r["ok"] for r in rows)
    avail = any(r["available"] for r in rows)
    ratios = [r["rank_ratio"] for r in rows if r["rank_ratio"] is not None]
    return {"schema": "adjacency-conditional/1",
            "criterion": f"同支邻家顺位优势 ≤ 非同支邻家 {ADJ_RATIO_MAX}×"
                         f"（胜率同样 ≤{ADJ_RATIO_MAX}×）",
            "families": rows, "available": avail,
            "suspect": any(r["suspect"] for r in rows),
            "max_rank_ratio": (max(ratios) if ratios else None),
            "ok": ok,
            "reason": ("；".join(r["reason"] for r in rows if not r["ok"])
                       if not ok else
                       ("桌上无同支邻家（行外池）⇒ 本轴为空但由对手池检查兜底"
                        if not avail else "同支邻家优势未超 1.2×"))}


def measurement_validity(res: dict) -> dict:
    """四条硬前置的合取（fail-closed）。`judge` 只认 `ok=true`。"""
    reasons = []
    sp = res.get("seat_perm")
    if not sp:
        reasons.append("座次覆盖取不到（seat_perm=null）⇒ 座次覆盖不可证")
    elif not sp.get("usable"):
        reasons.append("座次覆盖不达标/不可证：" + str(sp.get("reason") or sp.get("coverage")))
    ai = res.get("arm_isolation")
    if not ai or not ai.get("ok"):
        reasons.append("candidate/baseline 同桌检查未过：" + str((ai or {}).get("reason") or "取不到"))
    op = res.get("opponent_pool")
    if not op or not op.get("ok"):
        reasons.append("对手池冻结/白名单/非本族检查未过：" + str((op or {}).get("reason") or "取不到"))
    adj = res.get("adjacency_conditional")
    if not adj or not adj.get("ok"):
        reasons.append("共轭（同支邻家）检查未过：" + str((adj or {}).get("reason") or "取不到"))
    return {"schema": "measurement-validity/1", "ok": not reasons,
            "checks": {"seat_perm": bool(sp and sp.get("usable")),
                       "arm_isolation": bool(ai and ai.get("ok")),
                       "opponent_pool": bool(op and op.get("ok")),
                       "adjacency_conditional": bool(adj and adj.get("ok"))},
            "reasons": reasons}


# ---------------------------------------------------------------------------
# 核心判定
# ---------------------------------------------------------------------------
def eval_from_series_per_family(families: list, metric: str, guards: list, args) -> dict:
    """`families` 每项含 `by_metric[m] = {"a": 候选逐场, "b": 基准逐场}`（Δ = 候选 − 基准，正 = 更好）。"""
    out: dict = {"metric": metric, "families": [], "guards": [], "reasons": [], "errors": []}
    # ---- 主口径 -----------------------------------------------------------
    res, _ = _pooled([(f["a"], f["b"]) for f in families], "cand", "base", metric)
    sd = float(res.sd) if math.isfinite(res.sd) else float("nan")
    se = sd / math.sqrt(res.games) if (res.games > 1 and math.isfinite(sd)) else float("nan")
    out["primary"] = {"metric": metric, "delta": float(res.delta), "se": se,
                      "t": (float(res.delta) / se if (math.isfinite(se) and se > 0) else float("nan")),
                      "ci": [float(res.lo), float(res.hi)], "n": int(res.games), "sd": sd,
                      "win": int(res.win), "lose": int(res.lose), "tie": int(res.tie),
                      "verdict": res.verdict}
    # ---- 逐族（套间散布的原料）-------------------------------------------
    for f in families:
        #: Δ = 候选 − 基准（与主口径 `_pooled([(f["a"], f["b"])])` **同一个方向**，⛔ 不许反）
        row = _paired_from_series(f["name"], f["a"], f["b"], metric)
        row["seed_base"] = f.get("seed_base")
        out["families"].append(row)
    deltas = [r["delta"] for r in out["families"] if math.isfinite(r["delta"])]
    #: 与 `gate.py` **同一把尺子**：`wall_spread = max − min`（⛔ 不自己发明散布定义）
    spread = (max(deltas) - min(deltas)) if len(deltas) > 1 else 0.0
    half = (out["primary"]["ci"][1] - out["primary"]["ci"][0]) / 2.0
    out["walls"] = {"n_walls": len(out["families"]), "deltas": deltas, "spread": spread,
                    "ci_half_width": half,
                    "spread_over_ci_half": (spread / half if half > 0 else float("nan")),
                    "all_same_sign": bool(deltas) and (all(d > 0 for d in deltas)
                                                       or all(d < 0 for d in deltas)),
                    "positive_every_wall": bool(deltas) and all(d > 0 for d in deltas)}
    out["walls"]["random_effects"] = ml_eval.random_effects(
        [{"dir": r["family"], "n": r["n"], "delta": r["delta"], "se": r["se"],
          "win": r["win"], "lose": r["lose"]}
         for r in out["families"] if math.isfinite(r["delta"])])
    #: ★ **各族必须比的是同一对权重**：否则"套间散布"里混的是**臂差**（不是牌山差）⇒ 只能读作上界
    pairs = set()
    for f in families:
        pol = f.get("policies") or {}
        pairs.add((pol.get("base_net") or pol.get("incumbent_net") or "?",
                   pol.get("cand_net") or pol.get("candidate_net") or "?"))
    out["walls"]["arm_pair_consistent"] = len(pairs) <= 1
    out["walls"]["arm_pairs"] = sorted([list(p) for p in pairs])
    if len(pairs) > 1:
        out["reasons"].append("各族比较的**不是同一对权重** ⇒ 套间散布里混了臂差（只能读作上界）"
                              "⇒ 不许判「过」")
    # ---- 实际 MDE ---------------------------------------------------------
    out["mde"] = {"value": mde_from_sd(sd, int(res.games)), "unit": metric,
                  "alpha": ml_eval.ALPHA, "power": ml_eval.POWER_DEFAULT,
                  "method": "反解既有 eval.required_n（α=0.05、功效 80%）",
                  "n": int(res.games), "sd": sd}
    # ---- 护栏（`gate.guard_row` 既有；含"容忍带 < MDE ⇒ 不得作否决依据"）----
    for gm, tol in guards:
        #: ⚠ **既有约定**（`gate.gate()` 第 238-250 行）：`(sa, sb) = (现任, 候选)`，
        #: `guard_row` 内部走 `judge_pair(sb, sa, 候选, 现任)` ⇒ Δ = 候选 − 现任（正 = 更好）。
        #: ⛔ 传反了会**整套护栏的符号都反**（本工具自检 ② 就是钉这一条）。
        rows = [(f["by_metric"][gm]["b"], f["by_metric"][gm]["a"]) for f in families]
        sa_m, sb_m = v4gate.pooled_diffs(rows)
        row = dict(v4gate.guard_row(gm, tol, sa_m, sb_m, "base", "cand"))
        jr = v4arena.judge_pair(sb_m, sa_m, "cand", "base", gm)   # `guard_row` 不带 sd ⇒ 借它取 sd
        row["se"] = float(jr.sd) / math.sqrt(jr.games) if jr.games > 1 else float("nan")
        row["mde"] = mde_from_sd(float(jr.sd), int(jr.games))
        row["tol_over_mde"] = (float(tol) / row["mde"]
                               if (math.isfinite(row["mde"]) and row["mde"] > 0) else float("nan"))
        #: ★ 第七十一轮判据：容忍带只有分辨率的几分之一 ⇒ 天生必破 = 假警报 ⇒ 不得作否决依据
        row["usable_as_veto"] = bool(math.isfinite(row["mde"]) and row["mde"] > 0
                                     and float(tol) >= row["mde"])
        if not row["usable_as_veto"]:
            out["reasons"].append(
                f"护栏 {gm}：容忍带 {float(tol):g} < 实际 MDE {row['mde']:.3g}"
                f"（tol/MDE={row['tol_over_mde']:.2f}）⇒ **标为不得作为否决依据**（天生必破 = 假警报）")
        out["guards"].append(row)
    return out


def cuped_report(families: list, metric: str, folds: int = 10) -> tuple[dict | None, list]:
    """**仅报告**的 CUPED 读数（`--cuped-report`，缺省关）：⛔ 不进判决、不改 PASS/FAIL/退出码。

    协变量 `X` = **基线臂在同一副牌山、同一学生席上的结果**（判决前可得、不受候选臂影响）。
    `β = Cov(d,X)/Var(X)`（K 折交叉拟合，折 = 族内位置 % K）；`SE`/`CI` 只用**折外**残差；
    `VR = ρ(d,X)²`、`SE↓ = 1 − SE_cuped/SE_O0`、`equiv_n = (SE_O0/SE_cuped)²`。
    缺失/常数 ⇒ 返回 `(None, [警告…])`（fail-closed 只针对该字段，⛔ 判决仍是 O0）。
    """
    d, x, fold = [], [], []
    missing = 0
    for fi, f in enumerate(families):
        a = ((f.get("by_metric") or {}).get(metric) or {}).get("a") or {}
        b = ((f.get("by_metric") or {}).get(metric) or {}).get("b") or {}
        seeds = sorted(set(a) & set(b))
        for pos, s in enumerate(seeds):
            try:
                ya, xb = float(a[s]), float(b[s])
            except (TypeError, ValueError):
                missing += 1
                continue
            if not (math.isfinite(ya) and math.isfinite(xb)):
                missing += 1
                continue
            d.append(ya - xb)
            x.append(xb)
            fold.append(pos % folds)
    n = len(d)
    cooc = {"n_pairs": n, "n_families": len(families), "n_missing": missing,
            "covariate": "baseline_arm_same_wall_same_seat_result", "folds": folds}
    if missing:
        return None, [f"CUPED 报告：协变量缺失 {missing} 对（同山同席配不上）⇒ 记 null（fail-closed）"]
    if n < max(4, folds * 2):
        return None, [f"CUPED 报告：可配对场只有 {n}（<{max(4, folds * 2)}）⇒ 记 null"]
    dv, xv = np.asarray(d, float), np.asarray(x, float)
    sd = float(dv.std(ddof=1))
    se0 = sd / math.sqrt(n)
    if not (math.isfinite(se0) and se0 > 0):
        return None, ["CUPED 报告：O0 的 SE 为 0/非有限（两臂读数逐位相同？）⇒ 记 null"]
    if float(xv.std(ddof=1)) <= 0:
        return None, ["CUPED 报告：协变量（基线臂同山同席结果）是常数 ⇒ 记 null"]
    fold = np.asarray(fold, int)
    r = np.empty(n)
    betas, adj, zero_var_folds = [], 0.0, 0
    for k in range(folds):
        te, tr = fold == k, fold != k
        if not tr.any() or not te.any():
            continue
        zt = xv[tr] - xv[tr].mean()
        den = float(np.dot(zt, zt))
        if den <= 1e-12:
            b = 0.0
            zero_var_folds += 1
        else:
            b = float(np.dot(dv[tr] - dv[tr].mean(), zt) / den)
        betas.append(b)
        zc = xv[te] - xv[tr].mean()
        r[te] = (dv[te] - dv.mean()) - b * zc
        adj += float(np.sum(b * zc))
    r = r - r.mean()
    se_c = float(r.std(ddof=1) / math.sqrt(n))
    if not (math.isfinite(se_c) and se_c > 0):
        return None, ["CUPED 报告：交叉拟合后的 SE 为 0/非有限 ⇒ 记 null"]
    delta_c = float(dv.mean()) - adj / n
    z95 = 1.959963985
    rep = {
        "beta": float(np.mean(betas)) if betas else float("nan"),
        "se": se_c,
        "ci": [delta_c - z95 * se_c, delta_c + z95 * se_c],
        "delta": delta_c,
        "vr": 1.0 - (se_c / se0) ** 2,
        "se_reduce": 1.0 - se_c / se0,
        "equiv_n": (se0 / se_c) ** 2,
        "cooc": cooc,
        "o0": {"delta": float(dv.mean()), "se": se0, "n": n},
        "note": "仅报告、不进判决（判决仍用 O0）；协变量 = 基线臂同山同席结果",
    }
    warns = []
    if zero_var_folds:
        warns.append(f"CUPED 报告：{zero_var_folds}/{folds} 折的协变量为常数（该折 β=0）")
    return rep, warns


def placebo_null_contrast(families: list, mode: str, metric: str) -> dict:
    """安慰剂对照 —— 两种口径各一条，都是"**真值必须 ≈ 0**"的对照（必须能读出"无信号"）。

    · `gate-dir`（同一场里两两对开 `a,a,b,b`）：**假臂对照** = 候选臂与基准臂**各自内部两席互比
      之差** —— 每条内部互比都是同一权重在两张椅子上 ⇒ 真值恒 0；两臂相减让**座位/亲家偏置
      在互补对上精确抵消**（见下面的推导）。逐臂的内部互比另存 `per_row`（把偏置有多大摆出来）。
    · `family`（两条采集、学生席口径）：**非学生席同策略对照** —— 两边那三席是**同一批对手网**，
      真值同样 ≈ 0 ⇒ 读出来的是"主口径那点差是不是被整体盘面漂移解释"。
    """
    both: list = []
    if mode == "gate-dir":
        #: ★ **平衡的假臂对照**：逐场（同副牌山）算"候选臂**自己两席**互比" c_g 与
        #: "基准臂**自己两席**互比" b_g（两者都是**同一权重**在两张椅子上 ⇒ 真值恒 0），
        #: 安慰剂 = c_g − b_g。为什么必须相减：单看 c_g 会把**座位/亲家偏置**读成"信号"
        #: —— `a,a,b,b` 的 6 个排列里，两席的位置对不是对称出现的（0 号席 3 次当"前"、
        #: 3 号席 0 次），于是 E[c_g] = 3s₀+s₁−s₂−3s₃ ≠ 0；而互补对的那一项**逐项相同**
        #: ⇒ 相减后座位偏置**精确抵消**，剩下的真值就是 0。
        series = []
        for f in families:
            run = f["_run"]
            lc, lb = f["policies"]["candidate"], f["policies"]["incumbent"]
            sa, sb = {}, {}
            for g in run.per_game:
                vc = ml_eval.seat_values(g, lc, metric)          # 既有逐场取值（候选占 2 席）
                vb = ml_eval.seat_values(g, lb, metric)          # 基准占 2 席
                if len(vc) < 2 or len(vb) < 2:
                    continue
                sa[int(g["seed"])] = float(vc[0]) - float(vc[-1])
                sb[int(g["seed"])] = float(vb[0]) - float(vb[-1])
            series.append((sa, sb))
        res, _ = _pooled(series, "cand_same", "base_same", metric)
        per_row: list = []
        #: 逐臂诊断（各自内部互比读多少 —— 正好把"座位偏置有多大"摆在人前）
        for arm, _key in (("candidate", "a"), ("incumbent", "b")):
            per = []
            for f in families:
                run = f["_run"]
                lab = f["policies"][arm]
                sa2 = {}
                for g in run.per_game:
                    v = ml_eval.seat_values(g, lab, metric)
                    if len(v) >= 2:
                        sa2[int(g["seed"])] = float(v[0]) - float(v[-1])
                per.append((sa2, {k: 0.0 for k in sa2}))
            r2, _ = _pooled(per, arm, "zero", metric)
            per_row.append({"label": arm, "delta": float(r2.delta), "n": int(r2.games),
                            "ci": [float(r2.lo), float(r2.hi)], "significant": bool(r2.lo * r2.hi > 0)})
        kind = ("假臂对照：候选臂与基准臂**各自内部两席互比之差**（同权重 ⇒ 真值恒 0；"
                "座位偏置在互补对上精确抵消）")
    else:
        per_row = []
        for lab in sorted({l for f in families for g in f["_base_run"].per_game
                           for l in g.get("policies", []) if "@" not in l}):
            series = []
            for f in families:
                rb, rc = f["_base_run"], f["_cand_run"]
                labs_b = {l for g in rb.per_game for l in g.get("policies", [])}
                labs_c = {l for g in rc.per_game for l in g.get("policies", [])}
                if lab not in labs_b or lab not in labs_c:
                    continue
                series.append((ml_eval.per_game_series(rc, lab, metric),
                               ml_eval.per_game_series(rb, lab, metric)))
            if not series:
                continue
            r2, _ = _pooled(series, "opp", "opp", metric)
            per_row.append({"label": lab, "delta": float(r2.delta), "n": int(r2.games),
                            "ci": [float(r2.lo), float(r2.hi)],
                            "significant": bool(r2.lo * r2.hi > 0)})
        if not per_row:
            return {"kind": "非学生席同策略对照（取不到对手席 ⇒ 无法判定）", "delta": float("nan"),
                    "se": float("nan"), "ci": [float("nan")] * 2, "n": 0,
                    "verdict": "n/a", "significant": False, "available": False, "per_row": []}
        #: 取**更不利**的那条（|Δ| 最大的对手标签）当代表 —— 保守
        worst_i = max(range(len(per_row)), key=lambda i: abs(per_row[i]["delta"]))
        series = []
        for f in families:
            rb, rc = f["_base_run"], f["_cand_run"]
            lab = per_row[worst_i]["label"]
            series.append((ml_eval.per_game_series(rc, lab, metric),
                           ml_eval.per_game_series(rb, lab, metric)))
        res, _ = _pooled(series, "opp", "opp", metric)
        kind = f"非学生席同策略对照（两边同一批对手网 ⇒ 真值 ≈ 0；取更不利的那条：{lab}）"
    se = float(res.sd) / math.sqrt(res.games) if (res.games > 1 and math.isfinite(res.sd)) else float("nan")
    return {"kind": kind, "delta": float(res.delta), "se": se, "ci": [float(res.lo), float(res.hi)],
            "n": int(res.games), "verdict": res.verdict,
            "significant": bool(float(res.lo) * float(res.hi) > 0), "per_row": per_row}


#: 主口径结构性加和时可用的**非零和**替代指标（实测：`wins`/`deals` 的四席求和不恒定）。
#: ⚠ `score` **不是**非零和 —— 2026-10-17 实测 3,000 场 `final_scores` 四席合计恒为 100000
#: （⇒ `score` = `final_scores − 25000` 四席合计恒为 0），与 `rank_points` 同一条结构恒等式。
PLACEBO_FALLBACK_METRICS = ("wins", "deals")
ZERO_SUM_TOL = 1e-9


def _all_seat_values(row: dict, metric: str) -> list[float]:
    """一场里**四席**的指标值（下标 = 座位，与 `eval.seat_values` 同一套口径）。

    ⛔ 这里刻意不复用 `eval.seat_values`：它按**策略标签**过滤（只返回该策略占的席），
    而"每场四席求和"要的是**全部四席**。两者口径必须一致，所以逐指标照抄它的折向规则。
    """
    try:
        if metric == "rank_points":
            return [float(x) for x in row["rank_points"]]
        if metric == "score":
            return [float(x) - ml_eval.START_SCORE for x in row["final_scores"]]
        if metric == "place":
            return [-float(x) for x in row["placement"]]
        if metric == "wins":
            return [float(x) for x in row["wins"]]
        if metric == "deals":
            return [-float(x) for x in row["deals"]]
        if metric == "win_points":
            return [float(x) for x in row["win_points"]]
    except (KeyError, TypeError):
        return []
    return []


def _family_runs(f: dict):
    for key in ("_run", "_base_run", "_cand_run"):
        run = f.get(key)
        if run is not None:
            yield run


def zero_sum_probe(families: list, metric: str) -> dict:
    """实测主口径是否**结构性加和**（每场四席求和恒定）。

    判据（审计 §1-5 的"实测"口径）：
      · `zero_sum` = 每场 `max|Σ四席| ≤ 1e-9`（`rank_points`/`score` 是这一种）；
      · `sum_constant` = 每场求和**恒定**（含 `place`：四席合计恒为 −10，Δ 恒为 0）。
    两者都意味着 `Σ三名对手 Δ ≡ −Δ_student` ⇒ "非学生席同策略对照"量的是**再分配**。
    ⚠ 取不到逐场数据 ⇒ `checked=false`（fail-closed：⛔ 不许当"非零和"）。
    """
    sums: list[float] = []
    seen: set[int] = set()
    for f in families:
        for run in _family_runs(f):
            if id(run) in seen:
                continue
            seen.add(id(run))
            for g in run.per_game:
                vals = _all_seat_values(g, metric)
                if len(vals) < 4:
                    continue
                sums.append(float(sum(vals)))
    if not sums:
        return {"metric": metric, "checked": False, "zero_sum": False, "sum_constant": False,
                "n_games": 0, "max_abs_sum": float("nan"), "max_abs_dev": float("nan"),
                "tolerance": ZERO_SUM_TOL,
                "why": "取不到逐场数据 ⇒ 不能判结构性加和（fail-closed：不当作非零和）"}
    mx = max(abs(x) for x in sums)
    mean = sum(sums) / len(sums)
    mdev = max(abs(x - mean) for x in sums)
    return {"metric": metric, "checked": True,
            "zero_sum": bool(mx <= ZERO_SUM_TOL),
            "sum_constant": bool(mx <= ZERO_SUM_TOL or mdev <= ZERO_SUM_TOL),
            "n_games": len(sums), "max_abs_sum": float(mx), "max_abs_dev": float(mdev),
            "sum_mean": float(mean), "tolerance": ZERO_SUM_TOL}


def attach_placebo(res: dict, families: list, mode: str, metric: str, *, legacy: bool = False) -> dict:
    """算安慰剂并**按主口径的结构性质**决定它能不能作否决依据（本文件的唯一入口）。

    · `legacy=True`（`--legacy-placebo`）：完全照旧 —— `placebo` 就是 `placebo_null_contrast()`
      的返回值（不加任何新字段）、`zero_sum` 不写进 `res` ⇒ 旧路径的产物/判决理由不变。
    · 缺省（新口径）：主口径结构性加和（非 `gate-dir` 的"非学生席同策略对照"）⇒
      `usable_for_veto=false` + `why_zero_sum` + `redistribution_ratio`（只记账），
      并附一条**非零和指标**上的同类对照 `fallback`（作零假设否决用）。
    · `gate-dir` 的假臂对照（同一臂两席互比之差）真值**本就恒 0**，不受这条结构恒等式影响
      ⇒ 仍可作否决依据。
    """
    if legacy:
        res.pop("zero_sum", None)
        res["placebo"] = placebo_null_contrast(families, mode, metric)
        return res["placebo"]

    zs = zero_sum_probe(families, metric)
    res["zero_sum"] = zs
    pl = placebo_null_contrast(families, mode, metric)
    structural = mode != "gate-dir" and zs["checked"] and zs["sum_constant"]
    if mode != "gate-dir" and not zs["checked"]:
        pl["usable_for_veto"] = False
        pl["why_zero_sum"] = ("取不到逐场数据 ⇒ 无法判定主口径是否结构性加和"
                              "（fail-closed：该指标下安慰剂不作否决依据）")
        res["placebo"] = pl
        return pl
    if not structural:
        pl["usable_for_veto"] = True
        res["placebo"] = pl
        return pl

    pl["usable_for_veto"] = False
    prim = res.get("primary") or {}
    pd = abs(float(prim.get("delta", float("nan"))))
    pld = abs(float(pl.get("delta", float("nan"))))
    ratio = (pld / pd) if (math.isfinite(pd) and pd > 0 and math.isfinite(pld)) else float("nan")
    pl["redistribution_ratio"] = float(ratio) if math.isfinite(ratio) else None
    if zs["zero_sum"]:
        how = f"每场四席恒和 0（max|Σ|={zs['max_abs_sum']:.3g} ≤ {ZERO_SUM_TOL:g}，{zs['n_games']} 场）"
    else:
        how = (f"每场四席求和恒为 {zs.get('sum_mean', float('nan')):.6g}"
               f"（max|Σ−均值|={zs['max_abs_dev']:.3g} ≤ {ZERO_SUM_TOL:g}，{zs['n_games']} 场）")
    pl["why_zero_sum"] = (
        f"主口径 {metric} {how} ⇒ Σ三名对手 Δ ≡ −Δ_student；安慰剂量的是**再分配**"
        f"（|安慰剂|/|主口径|={ratio:.3f}），不是零假设 ⇒ 该指标下安慰剂**不可用**")
    pl["fallback"] = None
    pl["fallback_metric"] = None
    for m in PLACEBO_FALLBACK_METRICS:
        if m == metric:
            continue
        z2 = zero_sum_probe(families, m)
        if z2["checked"] and not z2["sum_constant"]:
            fb = placebo_null_contrast(families, mode, m)
            fb["metric"] = m
            fb["why"] = (f"主口径 {metric} 结构性加和 ⇒ 改用非零和指标 {m} 做安慰剂零假设"
                         f"（{m} 实测 max|Σ|={z2['max_abs_sum']:.3g} > {ZERO_SUM_TOL:g}）")
            pl["fallback"] = fb
            pl["fallback_metric"] = m
            break
    res["placebo"] = pl
    return pl


# ---------------------------------------------------------------------------
# 判决
# ---------------------------------------------------------------------------
def judge(res: dict, args, elapsed: float, *, no_training: bool, quiet_ok: bool,
          crosscheck_ok: bool) -> tuple[str, list, int]:
    reasons: list = []
    if res.get("errors"):
        return "ERROR", list(res["errors"]), 2
    #: ⭐ 测量有效性硬前置：**先于任何统计判据**（座次/共轭/对手池不过 ⇒ 读数不作数）。
    mv = res.get("measurement_validity")
    if not mv or not mv.get("ok"):
        rs = list((mv or {}).get("reasons") or ["测量有效性判据取不到"])
        return "ERROR", [f"[测量有效性] {r}" for r in rs], EXIT_MEASUREMENT_VALIDITY
    if not no_training:
        return "ERROR", ["零训练进程取不到证（进程扫描失败，或真的命中训练特征）"], 2
    if not crosscheck_ok:
        return "ERROR", ["与既有 verdict.json 的复算对不上（⛔ 复算不一致就不出数）"], 2
    prim, walls = res["primary"], res["walls"]
    if not (math.isfinite(prim["se"]) and prim["se"] > 0):
        reasons.append("主口径 SE 取不到或为 0（两臂读数逐位相同？）⇒ 测不出")
        return "测不出", reasons, 4
    mde = res["mde"]["value"]
    if math.isfinite(mde):
        reasons.append(f"实际 MDE（主口径）= {mde:.4g} · n={prim['n']} · sd={prim['sd']:.4g} · "
                       f"SE={prim['se']:.4g}")
    lo, hi = prim["ci"]
    pl = res["placebo"]
    legacy_pl = bool(getattr(args, "legacy_placebo", False))
    #: ★ 2026-10-17：零和/结构性加和主口径下，`pl`（非学生席同策略对照）的 Δ ≡ −Δ_student
    #: ⇒ 它不能当"零假设"用。新口径改用 `fallback`（非零和指标）来判；旧口径走 `--legacy-placebo`。
    pl_usable = pl.get("usable_for_veto", True) is not False
    veto_pl = pl if pl_usable else (pl.get("fallback") or {})
    if legacy_pl:
        veto_available = pl.get("available", True) is not False
    else:
        veto_available = (bool(veto_pl) and veto_pl.get("available", True) is not False
                          and math.isfinite(float(veto_pl.get("delta", float("nan")))))
    single = walls["n_walls"] < int(args.min_walls)
    if not walls.get("arm_pair_consistent", True):
        reasons.append("族间不是同一对权重 ⇒ 套间散布混了臂差（读作上界）⇒ 不许判「过」")
    if single:
        reasons.append(f"族数 {walls['n_walls']} < --min-walls {args.min_walls}"
                       f"⇒ 套间散布未测 ⇒ 不许判「过」")
    if not legacy_pl and not pl_usable:
        reasons.append(f"安慰剂口径：{pl.get('why_zero_sum')}"
                       + (f"；已改用非零和指标 `{pl['fallback_metric']}` 做零假设对照"
                          if pl.get("fallback") else "；且取不到非零和替代"))
    if pl.get("available", True) is False:
        reasons.append("安慰剂对照取不到 ⇒ 不许判「过」")
    elif not veto_available:
        reasons.append("主口径结构性加和 ⇒ 该指标下安慰剂不可用，且取不到非零和替代"
                       " ⇒ 不许判「过」")
    elif veto_pl["significant"]:
        if veto_pl is pl:
            reasons.append(f"安慰剂**显著**（Δ={pl['delta']:+.3f} CI[{pl['ci'][0]:+.2f},{pl['ci'][1]:+.2f}]）"
                           f"⇒ 主口径那点差有对照级别的解释 ⇒ 测不出")
        else:
            reasons.append(f"安慰剂**显著**（替代口径 {pl.get('fallback_metric')}："
                           f"Δ={veto_pl['delta']:+.3f} "
                           f"CI[{veto_pl['ci'][0]:+.2f},{veto_pl['ci'][1]:+.2f}]）"
                           f"⇒ 主口径那点差有对照级别的解释 ⇒ 测不出")
    #: ⛔ 零和主口径下这条近似**必须禁用**（审计 §1-5：`|placebo| ≥ |primary|` 在零和下近似必触发，
    #: 量的不是效应而是再分配）。非零和/旧口径下它仍是有效的"效应不超过对照"判据。
    if (pl_usable and pl.get("available", True) is not False
            and abs(pl["delta"]) >= abs(prim["delta"])):
        reasons.append(f"|安慰剂 Δ| {abs(pl['delta']):.3f} ≥ |主口径 Δ| {abs(prim['delta']):.3f}"
                       f"⇒ 主口径不高于对照 ⇒ 测不出")
    rho = res["mean_reversion"]["rho"]
    rho_ok = bool(res["mean_reversion"].get("identified", True))
    if rho_ok and math.isfinite(rho) and abs(rho) >= 0.9:
        reasons.append(f"均值回归 ρ={rho:+.3f}（|ρ|≥0.9 且**可识别**）"
                       f"⇒ Δ 被「基准这局倒不倒霉」解释 ⇒ 测不出")
    elif not rho_ok:
        reasons.append(f"均值回归 ρ={rho:+.3f} —— {res['mean_reversion'].get('unidentified_why')}")
    if not res["wall_clock_ok"]:
        reasons.append(f"墙钟 {elapsed / 60:.1f} min > 预算 {args.budget_min:g} min"
                       f"⇒ **这条不算便宜闸门**，不作为放行依据")
    if not quiet_ok:
        reasons.append("G7 静窗不满足（机器上有别人的 trainer / 重 python / 重 java）"
                       "⇒ 若本跑含采集，计时被污染（读数本身不受影响）")
    bad = [g for g in res["guards"] if g["usable_as_veto"] and not g["ok"]]
    for g in bad:
        reasons.append(f"护栏 {g['metric']} 越界（Δ={g['delta']:+.3f} "
                       f"CI[{g['lo']:+.2f},{g['hi']:+.2f}] tol={float(g['tol']):g} ≥ MDE {g['mde']:.3g}"
                       f" ⇒ 可用作否决）")
    if bad:
        reasons.append("可用护栏在坏方向确凿越界 ⇒ 这笔账不该投（不是「测不出」）")
        return "不过", reasons, 3
    ok_pass = (lo > 0 and veto_available and (not veto_pl["significant"])
               and pl.get("available", True) is not False
               and (not pl_usable or abs(pl["delta"]) < abs(prim["delta"])) and not single
               and walls["positive_every_wall"] and walls.get("arm_pair_consistent", True)
               and not (rho_ok and math.isfinite(rho) and abs(rho) >= 0.9)
               and res["wall_clock_ok"])
    if ok_pass:
        reasons.append("主口径 CI 下界 > 0 ∧ 各族逐族为正 ∧ 护栏/安慰剂/均值回归全部未报警")
        return "过", reasons, 0
    if hi < 0:
        reasons.append("主口径 CI 上界 < 0 ⇒ 候选在坏方向显著")
        return "不过", reasons, 3
    if not any(("测不出" in r) or ("不许判" in r) for r in reasons):
        reasons.append("主口径 CI 含 0 ⇒ 这个样本量下分不出")
    return "测不出", reasons, 4


# ---------------------------------------------------------------------------
# 交叉核对：与既有 verdict.json 复算对账（同一条臂、同一个指标）
# ---------------------------------------------------------------------------
def crosscheck(tag_dir: Path, res: dict, probes: Probes) -> bool:
    vp = Path(tag_dir) / "verdict.json"
    if not vp.is_file():
        probes.add("crosscheck", True, f"{vp} 不存在（新臂本来就没有旧 verdict）⇒ 无需对账")
        res["crosscheck"] = {"available": False, "path": str(vp)}
        return True
    v = json.loads(vp.read_text(encoding="utf-8"))
    metric = res["metric"]
    ref, where = None, ""
    if v.get("metric") == metric:
        ref, where = v, "verdict.json 主口径"
    else:
        for g in (v.get("guards") or []):
            if g.get("metric") == metric:
                ref, where = g, "verdict.json 的护栏行"
                break
    if ref is None:
        probes.add("crosscheck", True, f"verdict.json 里没有 {metric} 这一轴 ⇒ 不对账（照实记）")
        res["crosscheck"] = {"available": False, "path": str(vp),
                             "note": f"旧 verdict 的轴是 {v.get('metric')} + guards"}
        return True
    mine = res["primary"]
    delta, lo, hi, n = float(ref["delta"]), float(ref["lo"]), float(ref["hi"]), int(ref["n"])
    tol = 1e-9
    ok = (abs(mine["delta"] - delta) <= tol * max(1.0, abs(delta))
          and abs(mine["ci"][0] - lo) <= tol * max(1.0, abs(lo))
          and abs(mine["ci"][1] - hi) <= tol * max(1.0, abs(hi))
          and int(mine["n"]) == n)
    wall_ok = None
    if v.get("wall_deltas") and len(v["wall_deltas"]) == len(res["families"]):
        wall_ok = all(abs(r["delta"] - float(w)) <= tol * max(1.0, abs(float(w)))
                      for r, w in zip(res["families"], v["wall_deltas"]))
    res["crosscheck"] = {"available": True, "path": str(vp), "against": where,
                         "theirs": {"delta": delta, "ci": [lo, hi], "n": n},
                         "ours": {"delta": mine["delta"], "ci": mine["ci"], "n": mine["n"]},
                         "wall_deltas_match": wall_ok, "match": bool(ok)}
    probes.add("crosscheck", ok, f"（对 {where}）Δ {mine['delta']:+.9f} vs {delta:+.9f} · "
                                 f"n {mine['n']} vs {n} ⇒ {'逐位一致' if ok else '**对不上**'}")
    if wall_ok is not None:
        probes.add("crosscheck:wall_deltas", bool(wall_ok),
                   f"逐族 Δ vs 旧 verdict 的 wall_deltas：{'逐位一致' if wall_ok else '**对不上**'}")
    return bool(ok)


# ---------------------------------------------------------------------------
# `--collect`：新臂自采（**不训练**）
# ---------------------------------------------------------------------------
def collect_plan(args, probes: Probes) -> list:
    """拼采集命令行（⛔ 只拼不跑）：`style-vector.py --run-out …` ⇒ 生产路径（自对弈）。"""
    trainer = Path(args.trainer) if args.trainer else Path(os.environ.get("MAHJONG_TRAINER", "") or ".")
    probes.add("collect:trainer", trainer.is_file(), str(trainer))
    sv = REPO / "tools" / "style-vector.py"
    probes.add("collect:style-vector", sv.is_file(), str(sv))
    py = Path(args.python) if args.python else (REPO / "python" / ".venv" / "Scripts" / "python.exe")
    probes.add("collect:python", py.is_file(), str(py))
    table = [t.strip() for t in (args.table or "").split(",") if t.strip()]
    probes.add("collect:table", len(table) == 3, f"{len(table)} 个对手权重：{table}")
    for t in table:
        probes.add(f"collect:table:{Path(t).name}", Path(t).is_file(), t)
    for nm, p in (("base", args.base_net), ("cand", args.cand_net)):
        probes.add(f"collect:weight:{nm}", bool(p) and Path(p).is_file(), str(p))
    if probes.failures() and any(r["id"].startswith("collect:") for r in probes.failures()):
        bad = [r["id"] for r in probes.failures() if r["id"].startswith("collect:")]
        raise SystemExit(f"⛔ 采集前置不全 {bad} —— fail-closed，不降级")
    specs = []
    for wall in args.wall:
        for nm, net in (("base", args.base_net), ("cand", args.cand_net)):
            out = Path(args.collect_out) / args.tag / f"{wall}-{nm}"
            policy = ",".join([f"net:{net}{args.student_suffix}"] + [f"net:{t}" for t in table])
            cmd = [str(py), str(sv), "--run-out", str(out), "--trainer", str(trainer),
                   "--games", str(args.games), "--policy", policy, "--seed", str(wall),
                   "--workers", str(args.workers)]
            #: ★ 结构性自证：采集命令行里**不许**出现训练入口（`--rotate-perm` 由 `style-vector.py`
            #:   自己拼 —— 它的自检③硬断言了那一条，所以这里只查"没有训练"）。
            joined = " ".join(cmd).lower()
            bad = [t for t in TRAINING_PATTERNS if t.lower() in joined]
            if bad:
                raise SystemExit(f"⛔ 采集命令行里出现了训练特征 {bad}：{joined}")
            specs.append({"wall": wall, "arm": nm, "out": str(out), "policy": policy, "cmd": cmd})
    return specs


class _PE32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_char * 260)]


def kill_tree(pid: int) -> dict:
    """**树杀**一个相 —— **纯 ctypes，零子进程**。

    为什么要树杀：采集端常常是"启动器 + 真身"两层（venv 的 `Scripts\\python.exe` 是 47 KB 蹦床，
    真身在 `.uv-python`），只杀顶层会留下孤儿继续吃内存 —— 那正是"纪律线看起来执行了、
    其实没执行"的第二种形态。
    为什么不用 `taskkill`：本文件的 AST 自检 ⑫ 数着"子进程只有**两处**"（进程扫描 / 采集执行）
    ⇒ ⛔ 本文件不许再长出一个子进程落点（更不许出现任何训练入口）。
    """
    out = {"pid": int(pid), "killed": False, "how": "toolhelp32 后代后序 + TerminateProcess"}
    if not pid:
        return out
    snap: dict[int, int] = {}
    h = ctypes.windll.kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if h and h != ctypes.c_void_p(-1).value:
        try:
            e = _PE32()
            e.dwSize = ctypes.sizeof(_PE32)
            ok = ctypes.windll.kernel32.Process32First(h, ctypes.byref(e))
            while ok:
                snap[int(e.th32ProcessID)] = int(e.th32ParentProcessID)
                ok = ctypes.windll.kernel32.Process32Next(h, ctypes.byref(e))
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    kids: dict[int, list[int]] = {}
    for c, pp in snap.items():
        kids.setdefault(pp, []).append(c)
    post, stack, seen = [], [int(pid)], set()
    while stack:
        cur = stack.pop()
        post.append(cur)
        for c in kids.get(cur, []):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    killed = 0
    for q in reversed(post):                       # 后序：先杀真身、再杀蹦床
        h2 = ctypes.windll.kernel32.OpenProcess(0x0001, False, int(q))     # PROCESS_TERMINATE
        if not h2:
            continue
        try:
            if ctypes.windll.kernel32.TerminateProcess(h2, 1):
                killed += 1
        finally:
            ctypes.windll.kernel32.CloseHandle(h2)
    out.update({"killed": killed > 0, "n_terminated": killed, "tree_size": len(post)})
    return out


def collect_run(args, specs: list, probes: Probes) -> None:
    """真跑采集：**串行**（一次一个重负载）+ **起跑前与跑期间都查提交余量**。

    **缺省 = R-2 准入控制**（项目方裁定）：
      · 每相起跑前：`commit_free` vs **该相预期峰值 × 1.5** 与**空闲基线**；不达 ⇒ **拒绝起跑**；
        跑中越线后再要起新相 ⇒ **拒绝新增负载**（⛔ 不因此杀已跑完的相）；
      · 跑中 1 s 栅格：越线**只记账 + 关闭新增负载**，⛔ **不杀当前相**；
      · 只有 `commit_free` 跌到**硬地板**（`HARD_FLOOR_GB = 2.0`，文档里写定）才 kill 进程树。
    `--mem-kill` ⇒ **旧绝对口径逐字保留**：`commit_free < 8 GB`（起跑前或跑中）⇒ 立即停 + 树杀。
    """
    env = dict(os.environ)
    env["MAHJONG_DATA_ROOT"] = str(DATA)
    env["MAHJONG_TRAINER"] = str(args.trainer or os.environ.get("MAHJONG_TRAINER", ""))
    env["PYTHONIOENCODING"] = "utf-8"
    env["OMP_NUM_THREADS"] = str(min(args.workers, MAX_WORKERS))
    env["MKL_NUM_THREADS"] = str(min(args.workers, MAX_WORKERS))
    poll = float(getattr(args, "mem_poll_sec", 1.0) or 1.0)
    mem_kill = bool(getattr(args, "mem_kill", False))
    peak = getattr(args, "adm_peak_gb", None)
    state = {"closed": False, "trip": None}
    for s in specs:
        free = commit_free_gb()
        if mem_kill:
            probes.add(f"collect:mem:{s['wall']}-{s['arm']}", free >= MIN_COMMIT_FREE_GB,
                       f"commit_free={free:.2f} GB（闸门 {MIN_COMMIT_FREE_GB:g} GB）")
            if free < MIN_COMMIT_FREE_GB:
                raise SystemExit(f"⛔ commit_free {free:.2f} GB < {MIN_COMMIT_FREE_GB:g} GB ⇒ 立即停")
        else:
            if state["closed"]:
                probes.add(f"collect:admission_new_load:{s['wall']}-{s['arm']}", False,
                           f"跑中已于 {state['trip']} 越线 ⇒ **拒绝新增负载**（⛔ 不杀已跑完的相）")
                raise SystemExit(f"⛔ R-2 准入：前序相跑中越线（{state['trip']}）⇒ "
                                 f"**拒绝起跑 {s['wall']}-{s['arm']}**（⛔ 不许再加新负载）")
            adm = admission_check(free, peak, idle_baseline_gb=IDLE_BASELINE_GB,
                                  margin=ADMISSION_MARGIN, hard_floor_gb=HARD_FLOOR_GB)
            probes.add(f"collect:admission:{s['wall']}-{s['arm']}", adm["ok"],
                       f"commit_free={adm['commit_free_gb']:.2f} GB / 预期峰值 "
                       f"{adm['expected_peak_gb']} GB × {adm['margin']:g} = {adm['need_gb']} GB"
                       f"（空闲基线 {adm['idle_baseline_gb']:g} GB）"
                       + ("" if adm["ok"] else " ⇒ " + "；".join(adm["reasons"])))
            _say(f"[collect][R-2 准入] {s['wall']}-{s['arm']}：commit_free={adm['commit_free_gb']:.2f} GB"
                 f" · 预期峰值 {adm['expected_peak_gb']} GB × {adm['margin']:g} = {adm['need_gb']} GB"
                 f" ⇒ {'起跑' if adm['ok'] else '**拒绝起跑**'}")
            if not adm["ok"]:
                raise SystemExit("⛔ R-2 准入拒绝起跑："
                                 + "；".join(adm["reasons"]))
        _say(f"[collect] {s['wall']}-{s['arm']} ⇒ {s['out']}\n          $ {' '.join(s['cmd'])}")
        Path(s["out"]).mkdir(parents=True, exist_ok=True)
        log = Path(s["out"]).parent / f"{s['wall']}-{s['arm']}.collect.log"
        with log.open("a", encoding="utf-8", errors="replace") as fh:
            p = subprocess.Popen([str(x) for x in s["cmd"]], cwd=str(REPO), env=env,
                                 stdout=fh, stderr=subprocess.STDOUT)
            trip = None
            t0 = time.time()
            while p.poll() is None:
                time.sleep(poll)
                free = commit_free_gb()
                if mem_kill:
                    if free < MIN_COMMIT_FREE_GB:
                        trip = free
                        break
                else:
                    adm = admission_check(free, peak, idle_baseline_gb=IDLE_BASELINE_GB,
                                          margin=ADMISSION_MARGIN, hard_floor_gb=HARD_FLOOR_GB)
                    if (not adm["ok"]) and (not state["closed"]):
                        state["closed"] = True
                        state["trip"] = (f"{s['wall']}-{s['arm']} commit_free={free:.2f} GB < "
                                         f"{adm['need_gb']} GB")
                        probes.add(f"collect:admission_midrun:{s['wall']}-{s['arm']}", True,
                                   f"跑中越线 commit_free={free:.2f} GB ⇒ **只记账 + 关闭新增负载**"
                                   f"（⛔ 不杀当前相）")
                        _say(f"⚠ [R-2 准入] 跑中越线 commit_free={free:.2f} GB ⇒ "
                             f"只记账 + 拒绝新增负载（⛔ 不杀本相）")
                    if adm["below_hard_floor"]:
                        trip = free
                        break
            gate_gb = MIN_COMMIT_FREE_GB if mem_kill else HARD_FLOOR_GB
            if trip is not None:
                if mem_kill:
                    _say(f"⛔ [采集期内存闸门] commit_free={trip:.2f} GB < {MIN_COMMIT_FREE_GB:g} GB "
                         f"（跑动中）⇒ kill 进程树、本相作废")
                else:
                    _say(f"⛔ [采集期内存闸门] commit_free={trip:.2f} GB < {gate_gb:g} GB "
                         f"（硬地板）⇒ kill 进程树")
                probes.add(f"collect:kill_tree:{s['wall']}-{s['arm']}",
                           bool(kill_tree(p.pid).get("killed")),
                           f"taskkill /T /F /PID {p.pid}（崩床/真身两层都要杀）")
                p.kill()
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    pass
                probes.add(f"collect:mem_kill:{s['wall']}-{s['arm']}", False,
                           f"跑动中 commit_free={trip:.2f} GB < {gate_gb:g} GB ⇒ 杀相并停")
                raise SystemExit(f"⛔ 采集跑动中 commit_free {trip:.2f} GB < {gate_gb:g} GB "
                                 f"⇒ 已杀相并停（⛔ 不把越线读数当合格，也不继续跑剩下的相）")
            rc = p.returncode
            wall = round(time.time() - t0, 1)
        probes.add(f"collect:rc:{s['wall']}-{s['arm']}", rc == 0, f"退出码 {rc}（{wall}s）")
        if rc != 0:
            raise SystemExit(f"⛔ 采集失败（退出码 {rc}）：{' '.join(s['cmd'])}")


def build_collect_specs(args) -> list:
    """采完后按"每族两条目录"拼 `--family` 输入（标签 = 族名）。"""
    return [f"{w}={Path(args.collect_out) / args.tag / f'{w}-base'};"
            f"{Path(args.collect_out) / args.tag / f'{w}-cand'}" for w in args.wall]


# ---------------------------------------------------------------------------
# 自检（离线；含负向红证）
# ---------------------------------------------------------------------------
def _synth_run(d: Path, seeds: list, delta: float, *, student_net: str, opp_nets: list,
               rng, sd_w: float = 50.0, sd_n: float = 3.0, deal_bias: float = 0.0,
               w_list: list = None, zero_sum: bool = False, redistribute: bool = False,
               seat_offset: float = 0.0, sum_jitter: float = 0.0,
               perm_mode: str = "perm24", boost_same: float = 0.0) -> None:
    """造一份 `summary.json`（**学生席口径**的形状：学生串带 `@`，三席对手同序）。

    `per_game[]` 必须带 `rank_points` / `wins` / `deals` / `win_points`（`eval.seat_values` 要它们）；
    学生席在最前（与 `style-vector.py --policy net:<学生>@0#0.5,<对手>…` 的写法一致）。
    ⚠ `w_list`：**同一族两条采集要共用同一份"牌山运气"**（真配对的前提）—— 给了就用它，
    ⛔ 别让两条采集各自抽一份（那样"配对"就退化成两个独立样本了）。
    ⚠ `redistribute`：把学生席的位移**全部**记到 1 号对手头上（`[x,−x,0,0]` ⇒ 每场恒和 0，
    且"安慰剂 Δ ≡ −主口径 Δ"）—— 复现审计 §1-5 的结构性失效；`sum_jitter` 只给 2 号席加逐场噪声
    （四席求和不再是常数 ⇒ **非结构性求和**，用来证明新口径只在结构性加和时降级、否则判决照旧）。
    ⚠ `perm_mode`：`perm24` = 按场号枚举 24 全排列（正向）；`cycle4` = 只循环移位 4 种
    （**负向夹具**：座次覆盖必须被判不足）；`fixed` = 固定 `[学生, 对手…]`。
    ⚠ `boost_same`：当学生席的**同支邻家**（`_policy_line` 相同）时给该席 rank_points 加这个量，
    用来造"同支邻家优势 2×"的负向夹具。
    """
    d.mkdir(parents=True, exist_ok=True)
    stu = f"net:{student_net}@0#0.5"
    ops = [f"net:{t}" for t in opp_nets]
    roster = [stu] + ops
    stu_line = _policy_line(_net_path(stu) or stu)
    pg = []
    for i, s in enumerate(seeds):
        w = float(w_list[i]) if w_list is not None else float(rng.normal(0, sd_w))
        rp = [w + delta + float(rng.normal(0, sd_n))] + [w + float(rng.normal(0, sd_n))
                                                         for _ in ops]
        if redistribute:
            x = rp[0]
            rp = [x, -x, 0.0, 0.0]                 # 四席恒和 0，且再分配只落在 1 号对手
        elif zero_sum:
            m = sum(rp) / len(rp)
            rp = [v - m for v in rp]               # 只去均值（各席仍带独立噪声）
        if seat_offset:
            rp = [v + float(seat_offset) for v in rp]
        if sum_jitter:
            #: 只给 2 号席加逐场噪声 ⇒ 四席求和**不再是常数**（与"结构性加和"脱钩）
            rp[2] += float(rng.normal(0, sum_jitter))
        wins = [float(rng.integers(0, 5)) for _ in range(1 + len(ops))]
        #: 学生席刻意多加放铳 ⇒ 用来造一条"真越界"的护栏（`deals` 取负 ⇒ Δ = −bias）
        deals = [float(rng.integers(0, 2)) + deal_bias] + [float(rng.integers(0, 2))
                                                           for _ in ops]
        if perm_mode == "perm24":
            perm = SEAT_PERMS[i % 24]
        elif perm_mode == "cycle4":
            perm = tuple((j + i) % 4 for j in range(4))
        elif perm_mode == "fixed":
            perm = (0, 1, 2, 3)
        else:
            raise ValueError(f"未知 perm_mode={perm_mode!r}")
        #: 先把"按名单（学生在前）"的值算好，再按座位排列铺到四个座位上。
        pols = [roster[perm[j]] for j in range(4)]
        rp_seat = [rp[perm[j]] for j in range(4)]
        wins_seat = [wins[perm[j]] for j in range(4)]
        deals_seat = [deals[perm[j]] for j in range(4)]
        wp_seat = [1000.0 * wins[perm[j]] for j in range(4)]
        if boost_same:
            si = pols.index(stu)
            nb = [pols[(si - 1) % 4], pols[(si + 1) % 4]]
            if any(_policy_line(_net_path(x) or x) == stu_line for x in nb):
                rp_seat[si] += float(boost_same)
        pg.append({"game": i, "seed": int(s), "policies": pols,
                   "rank_points": rp_seat, "wins": wins_seat, "deals": deals_seat,
                   "win_points": wp_seat,
                   "placement": [1, 2, 3, 4], "final_scores": [25000.0] * 4, "hands": 10})
    (d / "summary.json").write_text(json.dumps(
        {"games": len(pg), "hands": 10 * len(pg), "seed_base": seeds[0], "workers": 1,
         "per_game": pg, "by_policy": {stu: {}, **{o: {} for o in ops}}},
        ensure_ascii=False), encoding="utf-8")


def _synth_gate_run(d: Path, seeds: list, delta: float, *, arm_a: str, arm_b: str, rng,
                    sd_w: float = 50.0, sd_n: float = 3.0) -> None:
    """造一份**闸门形状**的 `summary.json`（`a,a,b,b` 两两对开 —— `arena.run_pair` 的跑法）。

    `a` 占 2 席、`b` 占 2 席 ⇒ `eval.per_game_series` 各自取两席平均 ⇒ 逐场差 `a − b` 的均值 ≈ `delta`；
    **同一臂的两席**互比则是"真值恒 0"的安慰剂（自检用它验安慰剂那条路）。
    ⚠ 合成值**必须做成零和**（四席合计 = 0）—— 真实 `rank_points` 就是零和的
    （实测 `[-27.4, 25.3, 68.1, -66.0]` 合计 0.0），而"2+2 对开 ⇒ 两臂互为补集"这条**设计事实**
    正是自检 ⑭ 要钉住的东西（⛔ 合成数据不零和就测不出那一条）。
    """
    d.mkdir(parents=True, exist_ok=True)
    la, lb = f"net:{arm_a}", f"net:{arm_b}"
    pg = []
    for i, s in enumerate(seeds):
        w = float(rng.normal(0, sd_w))
        rp = [w + delta / 2 + float(rng.normal(0, sd_n)), w + delta / 2 + float(rng.normal(0, sd_n)),
              w - delta / 2 + float(rng.normal(0, sd_n)), w - delta / 2 + float(rng.normal(0, sd_n))]
        m = sum(rp) / 4.0
        rp = [x - m for x in rp]                     # ⇒ 四席合计严格为 0（与真数据同形）
        wins = [float(rng.integers(0, 5)) for _ in range(4)]
        deals = [float(rng.integers(0, 2)) for _ in range(4)]
        pg.append({"game": i, "seed": int(s), "policies": [la, la, lb, lb],
                   "rank_points": rp, "wins": wins, "deals": deals,
                   "win_points": [1000.0 * x for x in wins],
                   "placement": [1, 2, 3, 4], "final_scores": [25000.0] * 4, "hands": 10})
    (d / "summary.json").write_text(json.dumps(
        {"games": len(pg), "hands": 10 * len(pg), "seed_base": seeds[0], "workers": 1,
         "per_game": pg, "by_policy": {la: {}, lb: {}}}, ensure_ascii=False), encoding="utf-8")


def selftest() -> int:
    bad = 0

    def say(ok, name, detail=""):
        nonlocal bad
        if not ok:
            bad += 1
        _say(f"  [{'ok  ' if ok else 'FAIL'}] {name}{' —— ' + detail if detail else ''}")

    _say("== ceiling-gate 自检（离线：造数据 + 负向必红；⛔ 不采集、不训练、不出网）==")
    tmp = Path(tempfile.mkdtemp(prefix="ceiling-gate-selftest-"))
    rng = np.random.default_rng(20261013)
    arms = tmp / "arms"
    net_b = arms / "mv1-s24-BASE" / "net.bin"
    net_c = arms / "mv1-s24-AUX03" / "net.bin"
    net_b.parent.mkdir(parents=True, exist_ok=True)
    net_c.parent.mkdir(parents=True, exist_ok=True)
    net_b.write_bytes(b"base")
    net_c.write_bytes(b"cand")
    opp = []
    for k, name in enumerate(("v4-expert-def7-g10", "v4-expert-win2-g10",
                              "v4-expert-atk3-g10")):
        p = tmp / "opp" / name / "net.bin"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f"opp{k}".encode())
        opp.append(str(p))

    def _write_pin(path: Path, paths: list) -> None:
        allowed = {_sha256(p): {"name": Path(p).parent.name, "line": _policy_line(p)}
                   for p in paths}
        path.write_text(json.dumps({"schema": "opponent-pin/1", "frozen": True,
                                    "frozen_at": "selftest", "allowed": allowed},
                                   ensure_ascii=False), encoding="utf-8")

    pin = tmp / "opponent-pin.json"
    _write_pin(pin, opp)
    #: 负向夹具用：把基线的权重也塞进对手池（与候选同一支 `mv1-s24`）。
    pin_same = tmp / "opponent-pin-same.json"
    _write_pin(pin_same, opp + [str(net_b)])

    class A:                                  # 判据函数要的 args 子集
        pass

    a = A()
    a.min_walls, a.budget_min, a.metric = 2, 30.0, "rank_points"
    a.guards = [("deals", 0.10), ("wins", 0.10)]
    a.legacy_placebo = False

    def mk(prefix, delta, n_games, sd_n=3.0, deal_bias=0.0, student=None,
           opp_nets=None, perm_mode="perm24", cand_boost=0.0, w_sd=50.0, **kw):
        seeds = [int(x) for x in rng.integers(1 << 40, 1 << 60, n_games)]
        #: ★ 两条采集共用同一份"牌山运气" w（真配对的前提）
        w_list = [float(rng.normal(0, w_sd)) for _ in range(n_games)]
        b = tmp / f"{prefix}-base"
        c = tmp / f"{prefix}-cand"
        _synth_run(b, seeds, 0.0, student_net=str(net_b), opp_nets=opp_nets or opp, rng=rng,
                   sd_n=sd_n, w_list=w_list, perm_mode=perm_mode, **kw)
        _synth_run(c, seeds, delta, student_net=str(student or net_c), opp_nets=opp_nets or opp,
                   rng=rng, sd_n=sd_n, deal_bias=deal_bias, w_list=w_list,
                   perm_mode=perm_mode, boost_same=cand_boost, **kw)
        return f"{prefix}={b};{c}"

    def run_case(specs, metric="rank_points", guards=(("deals", 0.10), ("wins", 0.10)),
                 legacy=False, pin_override=None):
        pr = Probes()
        fam, _meta = _read_family_specs(list(specs), metric, list(guards), pr)
        a.guards = list(guards)
        res = eval_from_series_per_family(fam, metric, list(guards), a)
        res["metric"] = metric
        attach_placebo(res, fam, "family", metric, legacy=legacy)
        dz, base = [], []
        for f in fam:
            for s in sorted(set(f["a"]) & set(f["b"])):
                dz.append(float(f["a"][s] - f["b"][s]))
                base.append(float(f["b"][s]))
        res["mean_reversion"] = {"rho": pearson_rho(dz, base), "n": len(dz),
                                 "method": "Pearson(Δ, 基准臂自身读数) · numpy.corrcoef",
                                 "trimmed": trimmed_deltas(dz, base)}
        res["wall_clock_ok"] = True
        res["errors"] = []
        res["seat_perm"] = seat_perm_report(fam)
        res["arm_isolation"] = arm_isolation_report(fam)
        res["opponent_pool"] = opponent_pool_report(fam, str(pin_override or pin))
        res["measurement_guards"] = measurement_guards(fam)
        res["adjacency_conditional"] = adjacency_conditional(fam)
        res["measurement_validity"] = measurement_validity(res)
        return pr, res, fam

    # ① 正向：真 Δ=+3、两族 ⇒ 主口径读回、MDE 有限、判「过」
    specs2 = [mk("f0", 3.0, 300), mk("f1", 3.0, 300)]
    pr, res, fam = run_case(specs2)
    say(abs(res["primary"]["delta"] - 3.0) < 1.0, "①主口径读回真 Δ=+3.0",
        f"Δ={res['primary']['delta']:+.3f} CI[{res['primary']['ci'][0]:+.2f},{res['primary']['ci'][1]:+.2f}]"
        f" n={res['primary']['n']}")
    say(res["walls"]["n_walls"] == 2 and res["walls"]["positive_every_wall"], "①两族逐族为正 + 套间散布出得来",
        f"逐族 {[round(x, 2) for x in res['walls']['deltas']]} spread={res['walls']['spread']:.3f}"
        f" CI半宽={res['walls']['ci_half_width']:.3f}")
    say(math.isfinite(res["mde"]["value"]) and res["mde"]["value"] > 0, "①实际 MDE 有限",
        f"MDE={res['mde']['value']:.3f}（n={res['primary']['n']}）")
    v, why, code = judge(res, a, 60.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(v == "过" and code == 0, "①真信号 ⇒ 判「过」（退出码 0）", f"verdict={v} exit={code}")
    # ⑪ MDE 反解自证（代回既有 required_n）
    mde, n_, sd_ = res["mde"]["value"], res["primary"]["n"], res["primary"]["sd"]
    say(ml_eval.required_n(sd_, mde) <= n_ and ml_eval.required_n(sd_, mde * 0.99) > n_,
        "⑪MDE 是反解 `eval.required_n`（代回自证：MDE 恰好够、再小一点就不够）",
        f"MDE={mde:.4f} required_n(MDE)={ml_eval.required_n(sd_, mde)} ≤ n={n_}")
    # ② 护栏容忍带 < MDE ⇒ 标"不得作为否决依据"，且**不翻判决**
    specs_g = [mk("g0", 3.0, 300, deal_bias=1.0), mk("g1", 3.0, 300, deal_bias=1.0)]
    pr2, res2, _ = run_case(specs_g, guards=(("deals", 1e-9),))
    g = res2["guards"][0]
    say(g["usable_as_veto"] is False and g["ok"] is False,
        "②护栏容忍带 < MDE ⇒ usable_as_veto=false（照实记越界，但不当否决依据）",
        f"tol={float(g['tol']):g} MDE={g['mde']:.3f} Δ={g['delta']:+.3f} ok={g['ok']}")
    v2, _, _ = judge(res2, a, 60.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(v2 == "过", "②不可用护栏**不许**翻掉判决", f"verdict={v2}")
    # ②b 反向：容忍带 ≥ MDE ⇒ 可用作否决，且真越界时**必须**把判决压下来
    pr2b, res2b, _ = run_case(specs_g, guards=(("deals", 0.5),))
    gb = res2b["guards"][0]
    v2b, _, cb = judge(res2b, a, 60.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(gb["usable_as_veto"] is True and gb["ok"] is False and v2b != "过",
        "②b 容忍带 ≥ MDE ⇒ 可用作否决（真越界 ⇒ 不许判「过」）",
        f"tol={float(gb['tol']):g} MDE={gb['mde']:.3f} Δ={gb['delta']:+.3f} "
        f"CI[{gb['lo']:+.2f},{gb['hi']:+.2f}] verdict={v2b}")
    # ③ 真 Δ=0 ⇒ 测不出（安慰剂/ρ 也得出得来）
    specs0 = [mk("z0", 0.0, 200), mk("z1", 0.0, 200)]
    pr3, res0, _ = run_case(specs0)
    v0, _, c0 = judge(res0, a, 60.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(v0 == "测不出" and c0 == 4, "③零信号 ⇒ 测不出（退出码 4）",
        f"Δ={res0['primary']['delta']:+.3f} verdict={v0}")
    say(math.isfinite(res0["placebo"]["delta"]) and math.isfinite(res0["mean_reversion"]["rho"]),
        "③零信号时安慰剂与 ρ 照样给得出",
        f"安慰剂 Δ={res0['placebo']['delta']:+.3f} significant={res0['placebo']['significant']}"
        f" · ρ={res0['mean_reversion']['rho']:+.3f}")
    # ⑮ ★ 零和主口径：安慰剂（非学生席同策略对照）结构性 ≡ −主口径 ⇒ **不可作否决**；
    #    新口径只记账 why_zero_sum，并改用非零和指标（wins/deals）上的对照。
    #    （审计 §1-5 的红证：`rank_points` 每场四席恒和 0 ⇒ Σ三名对手 Δ ≡ −Δ_student。）
    specs_zs = [mk("zs0", 8.0, 600, redistribute=True),
                mk("zs1", 8.0, 600, redistribute=True)]
    pr15, res15, fam15 = run_case(specs_zs)
    pl15 = res15["placebo"]
    say(res15["zero_sum"]["zero_sum"] is True and res15["zero_sum"]["max_abs_sum"] <= 1e-9,
        "⑮-a 零和判据**实测**：每场四席 max|Σ| ≤ 1e-9 ⇒ 主口径结构性加和",
        f"max|Σ|={res15['zero_sum']['max_abs_sum']:.3g} n={res15['zero_sum']['n_games']} 场")
    say(pl15.get("usable_for_veto") is False and bool(pl15.get("why_zero_sum")),
        "⑮-b 零和指标下 `placebo.usable_for_veto=false` + `why_zero_sum`（只记账，不判否决）",
        f"fallback={pl15.get('fallback_metric')} ratio={pl15.get('redistribution_ratio')}")
    say(abs(abs(pl15["delta"]) - abs(res15["primary"]["delta"])) < 0.5
        and pl15.get("fallback") is not None,
        "⑮-c 安慰剂 Δ ≈ −主口径 Δ（结构性再分配）⇒ 已切到非零和替代口径",
        f"主口径 {res15['primary']['delta']:+.3f} · 安慰剂 {pl15['delta']:+.3f}")
    v15, why15, c15 = judge(res15, a, 60.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(v15 == "过" and not any(("|安慰剂 Δ|" in w) or ("安慰剂**显著**" in w) for w in why15),
        "⑮-d **新口径**：安慰剂≈−主口径**不触发**否决（合成红证：旧口径会判死）",
        f"verdict={v15} exit={c15}")
    #: 旧口径复现：`--legacy-placebo` 下 placebo 字段**不带任何新键**（逐字节同旧）
    pr15L, res15L, fam15L = run_case(specs_zs, legacy=True)
    pl15L = res15L["placebo"]
    old_pl = placebo_null_contrast(fam15L, "family", "rank_points")
    say("usable_for_veto" not in pl15L and "zero_sum" not in res15L
        and json.dumps(pl15L, sort_keys=True, ensure_ascii=False)
        == json.dumps(old_pl, sort_keys=True, ensure_ascii=False),
        "⑮-e `--legacy-placebo`：placebo 字段与旧实现**逐字节相同**（无新键）",
        f"键={sorted(pl15L)}")
    a.legacy_placebo = True
    v15L, why15L, c15L = judge(res15L, a, 60.0, no_training=True, quiet_ok=True,
                               crosscheck_ok=True)
    a.legacy_placebo = False
    say(v15L == "测不出" and any("|安慰剂 Δ|" in w for w in why15L)
        and any("安慰剂**显著**" in w for w in why15L),
        "⑮-f **旧口径**：同一份合成数据 ⇒ 判否决（红证：`|placebo|≥|primary|` + 显著）",
        f"verdict={v15L} exit={c15L}")
    say(v15 != v15L, "⑮-g 红证成立：同一份「安慰剂≈−主口径」数据，新口径放行、旧口径判死",
        f"新={v15} vs 旧={v15L}")
    # ⑯ 非零和指标：同样的再分配，但四席求和**逐场有波动**（≠0 且≠常数）
    #    ⇒ 安慰剂仍是有效零假设，判决照旧否决
    specs_ns = [mk("ns0", 8.0, 600, redistribute=True, sum_jitter=1.0),
                mk("ns1", 8.0, 600, redistribute=True, sum_jitter=1.0)]
    pr16, res16, fam16 = run_case(specs_ns)
    v16, why16, _ = judge(res16, a, 60.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(res16["zero_sum"]["zero_sum"] is False
        and res16["placebo"].get("usable_for_veto") is True
        and v16 == "测不出" and any("|安慰剂 Δ|" in w for w in why16),
        "⑯ 非零和指标：安慰剂照常判（`usable_for_veto=true` 且 |placebo|≥|primary| 仍否决）",
        f"max|Σ|={res16['zero_sum']['max_abs_sum']:.3g} verdict={v16}")
    # ④ 负向：单族不许判「过」
    pr4, res1, _ = run_case(specs2[:1])
    v1, why1, _ = judge(res1, a, 60.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(v1 != "过" and any("min-walls" in w for w in why1), "④单族（1 族）不许判「过」",
        f"verdict={v1}")
    # ⑤ 负向：超预算不许判「过」
    res5 = json.loads(json.dumps(res))
    res5["wall_clock_ok"] = False
    v5, _, _ = judge(res5, a, 60.0 * 40, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(v5 != "过", "⑤墙钟超预算 ⇒ 不作为放行依据", f"verdict={v5}")
    # ⑥ 负向：缺目录 / 缺 summary / 两文件都缺 ⇒ 探针必红 + ERROR
    pr6 = Probes()
    f6, _ = _read_family_specs([f"mw={tmp / 'nope-b'};{tmp / 'nope-c'}"], "rank_points",
                               [("deals", 0.10)], pr6)
    say((not f6) and bool(pr6.failures()), "⑥-a 目录不存在（两文件都缺）⇒ 探针必红、族数 0",
        f"族数 {len(f6)} · 红探针 {[r['id'] for r in pr6.failures()]}")
    #: 只写过一半（有 summary.json 但没有 per_game）也要红
    half = tmp / "half"
    half.mkdir(parents=True, exist_ok=True)
    (half / "summary.json").write_text(json.dumps({"games": 0, "per_game": []}), encoding="utf-8")
    raised = ""
    try:
        ml_eval.load_run(half)                       # 既有实现：没有 per_game ⇒ 当场 ValueError
    except ValueError as e:
        raised = str(e)[:40]
    say(bool(raised), "⑥-b 缺 `per_game` ⇒ 既有 `eval.load_run` 当场报错（不静默退化成 0）", raised)
    resE = {"errors": ["一族都没取到"], "primary": {}, "walls": {}, "guards": [], "placebo": {},
            "mean_reversion": {}, "wall_clock_ok": True}
    vE, whyE, cE = judge(resE, a, 1.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(vE == "ERROR" and cE == 2, "⑥-c 探针坏 ⇒ verdict=ERROR ∧ 退出码非 0", f"{vE}/{cE}")
    vNT, _, cNT = judge(res, a, 1.0, no_training=False, quiet_ok=True, crosscheck_ok=True)
    say(vNT == "ERROR" and cNT == 2, "⑥-d 零训练自证取不到 ⇒ ERROR（⛔ 不许当干净）", f"{vNT}/{cNT}")
    vCK, _, cCK = judge(res, a, 1.0, no_training=True, quiet_ok=True, crosscheck_ok=False)
    say(vCK == "ERROR" and cCK == 2, "⑥-e 与既有 verdict 复算对不上 ⇒ ERROR", f"{vCK}/{cCK}")
    # ⑦ 负向：缺权重（形状对、net.bin 不在）⇒ 权重探针红
    pr7 = Probes()
    miss = weight_probe([{"policies": {"candidate_net": str(tmp / "not-there.bin"),
                                       "incumbent_net": str(net_b)}}], pr7)
    say(bool(miss) and bool(pr7.failures()), "⑦缺权重 ⇒ 权重探针红（调用处据此 ERROR）",
        f"找不到：{miss}")
    # ⑧ 负向：两臂同一权重 ⇒ 无比较对象
    seeds_same = [int(x) for x in rng.integers(1 << 40, 1 << 60, 60)]
    _synth_run(tmp / "same-base", seeds_same, 0.0, student_net=str(net_b), opp_nets=opp, rng=rng)
    _synth_run(tmp / "same-cand", seeds_same, 0.0, student_net=str(net_b), opp_nets=opp, rng=rng)
    pr8 = Probes()
    f8, _ = _read_family_specs([f"same={tmp / 'same-base'};{tmp / 'same-cand'}"], "rank_points",
                               [("deals", 0.10)], pr8)
    say((not f8) and any((not r["ok"]) and "distinct" in r["id"] for r in pr8.rows),
        "⑧两臂同一权重（net 路径逐字相同）⇒ 必红", f"族数 {len(f8)}")
    # ⑨ 零训练自证：特征识别 + 真机进程表 + **当前无训练进程**
    fake = [{"pid": 4242, "name": "python.exe", "ws_mb": 900.0,
             "cmdline": "python -m mahjong_ml.v4.loop --init a.bin --target-minutes 20"},
            {"pid": 4243, "name": "trainer.exe", "ws_mb": 300.0,
             "cmdline": "trainer.exe selfplay 1000 --policy teacher --workers 8"}]
    hits = training_hits(fake, skip=set())
    say(len(hits) == 1 and hits[0]["pid"] == 4242,
        "⑨训练特征识别：`v4.loop` 命中、`trainer selfplay`（采集）不误伤",
        f"命中 pid={[h['pid'] for h in hits]}")
    procs, perr = scan_processes()
    say(bool(procs) and not perr, "⑨真机进程表可读（零训练自证的前提）",
        f"{len(procs)} 个进程" if procs else perr)
    if procs:
        chain = self_chain(procs)
        ht = training_hits(procs, chain)
        say(not ht, "⑨当前机器**零训练进程**（跳过的是我自己这条启动链）",
            f"命中 {[(h['name'], h['patterns']) for h in ht]}" if ht else
            f"0 命中 · 跳过 {sorted(chain)}")
    # ⑩ 口径①（`--gate-dir` 复算路径）整条走一遍：族级行 vs 既有 `eval.pool_diffs`、安慰剂、对账
    tag = tmp / "gate-st"
    (tag / "s90001").mkdir(parents=True, exist_ok=True)
    (tag / "s90002").mkdir(parents=True, exist_ok=True)
    g_dirs = [tag / "s90001", tag / "s90002"]
    for k, gd in enumerate(g_dirs):
        seeds = [int(x) for x in rng.integers(1 << 40, 1 << 60, 400)]
        _synth_gate_run(gd, seeds, 2.0, arm_a=str(net_c), arm_b=str(net_b), rng=rng)
    pr10 = Probes()
    fam10, meta10 = _read_gate_families(tag, "mv1-s24-BASE", "mv1-s24-AUX03", "rank_points",
                                       [("deals", 0.10)], pr10)
    say(len(fam10) == 2, "⑩-a `--gate-dir` 逐族读通（零采集复算路径）", f"{len(fam10)} 族")
    runs10 = [ml_eval.load_run(g) for g in g_dirs]
    lc = ml_eval.resolve_label(runs10[0], "mv1-s24-AUX03")
    lb = ml_eval.resolve_label(runs10[0], "mv1-s24-BASE")
    _, per_run = ml_eval.pool_diffs(runs10, lc, lb, "rank_points")
    #: 族级行（本工具）vs `eval.pool_diffs` 的行（既有、另一条拼装路径）
    rows = [_paired_from_series(f["name"], f["a"], f["b"], "rank_points") for f in fam10]
    say(all(abs(rows[i]["delta"] - per_run[i]["delta"]) < 1e-9
            and abs(rows[i]["se"] - per_run[i]["se"]) < 1e-9 for i in range(2)),
        "⑩-b 族级 Δ/se 与既有 `eval.pool_diffs` 的行逐位一致（且符号 = 候选 − 基准）",
        f"{rows[0]['delta']:+.9f} / {per_run[0]['delta']:+.9f}（真值 +2）")
    res10 = eval_from_series_per_family(fam10, "rank_points", [("deals", 0.10)], a)
    res10["metric"] = "rank_points"
    pl10 = placebo_null_contrast(fam10, "gate-dir", "rank_points")
    say(abs(pl10["delta"]) < 0.5 and not pl10["significant"],
        "⑩-c 安慰剂（同一臂自身两席互比，真值恒 0）读出来 ≈0 且不显著",
        f"Δ={pl10['delta']:+.3f} CI[{pl10['ci'][0]:+.2f},{pl10['ci'][1]:+.2f}]")
    #: 对账：按既有函数写一份"旧 verdict.json" ⇒ 必须逐位一致；改一个数 ⇒ 必须红
    p_sa, p_sb = v4gate.pooled_diffs([(f["a"], f["b"]) for f in fam10])
    rr = v4arena.judge_pair(p_sa, p_sb, "cand", "base", "rank_points")
    vjson = {"metric": "rank_points", "delta": rr.delta, "lo": rr.lo, "hi": rr.hi, "n": rr.games,
             "sd": rr.sd, "wall_deltas": [r["delta"] for r in rows]}
    (tag / "verdict.json").write_text(json.dumps(vjson), encoding="utf-8")
    pr10b = Probes()
    ok_ck = crosscheck(tag, res10, pr10b)
    say(ok_ck, "⑩-d 与既有 verdict.json 复算**逐位一致**（这就是「既有 verdict 复算」那条路）",
        f"Δ {res10['primary']['delta']:+.9f} vs {rr.delta:+.9f}")
    vjson2 = dict(vjson)
    vjson2["delta"] = float(vjson["delta"]) + 0.5
    (tag / "verdict.json").write_text(json.dumps(vjson2), encoding="utf-8")
    pr10c = Probes()
    ok_ck2 = crosscheck(tag, res10, pr10c)
    say(ok_ck2 is False and bool(pr10c.failures()), "⑩-e 复算**对不上** ⇒ 必红（⛔ 复算不一致不出数）",
        f"红探针 {[r['id'] for r in pr10c.failures()]}")
    # ⑭ ★ 均值回归 ρ 的**可识别性**：2+2 零和对开 ⇒ ρ 是设计恒等式（不可识别）；
    #    学生席口径（同座位、同牌山、只换权重）⇒ 可识别
    mr_gate = mean_reversion(fam10, "gate-dir", "rank_points")
    say(mr_gate["identified"] is False and abs(mr_gate["rho"] + 1.0) < 1e-9,
        "⑭ 2+2 零和对开下 ρ 判为**不可识别**（机械 −1，不是均值回归证据 ⇒ 不作判据）",
        f"ρ={mr_gate['rho']:+.6f} sd(候选+基准)={mr_gate['sd_of_sum']:.3g}")
    _prf, _resf, fam_f = run_case(specs2)
    mr_fam = mean_reversion(fam_f, "family", "rank_points")
    say(mr_fam["identified"] is True and math.isfinite(mr_fam["rho"]),
        "⑭ 学生席口径（同座位、同牌山、只换权重）⇒ ρ 可识别",
        f"ρ={mr_fam['rho']:+.3f} n={mr_fam['n']}")
    # ⑲ ★ 负向：只给 4 种循环移位（`--rotate` 旧口径）⇒ 座次覆盖必须判不足、拒判
    specs_cyc = [mk("cyc0", 3.0, 240, perm_mode="cycle4"),
                 mk("cyc1", 3.0, 240, perm_mode="cycle4")]
    prcy, rescy, famcy = run_case(specs_cyc)
    say(rescy["seat_perm"]["coverage"] == "4/24" and not rescy["seat_perm"]["usable"],
        "⑲-a 只 4 种循环移位 ⇒ 座次覆盖 4/24、usable=false",
        f"coverage={rescy['seat_perm']['coverage']} reason={rescy['seat_perm']['reason']}")
    vcy, whycy, ccy = judge(rescy, a, 60.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(vcy == "ERROR" and ccy == EXIT_MEASUREMENT_VALIDITY
        and any("座次" in w for w in whycy),
        "⑲-b 覆盖不足 ⇒ fail-closed 拒判（退出码 5，不是 0/3/4）",
        f"verdict={vcy} exit={ccy} why={whycy[:1]}")
    # ⑳ ★ 负向：同支邻家优势 2× ⇒ 必须判"共轭嫌疑"
    specs_sb = [mk("sb0", 2.0, 1200, opp_nets=[str(net_b)] + opp[1:], cand_boost=2.0,
                   w_sd=1.0)]
    prsb, ressb, famsb = run_case(specs_sb, pin_override=pin_same)
    adj = ressb["adjacency_conditional"]
    say(adj["suspect"] is True and (adj["max_rank_ratio"] or 0) > 1.8,
        "⑳-a 同支邻家顺位优势 ≈2× ⇒ adjacency_conditional 判共轭嫌疑",
        f"ratio={adj['max_rank_ratio']} adv_same={adj['families'][0]['adv_same']:+.3f} "
        f"adv_other={adj['families'][0]['adv_other']:+.3f}")
    vsb, whysb, csb = judge(ressb, a, 60.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(vsb == "ERROR" and csb == EXIT_MEASUREMENT_VALIDITY
        and any(("共轭" in w) or ("邻家" in w) for w in whysb),
        "⑳-b 共轭嫌疑 ⇒ fail-closed 拒判（退出码 5）",
        f"verdict={vsb} exit={csb} why={whysb[:1]}")
    # ㉑ 既有 `--gate-dir` 的 `a,a,b,b` 2+2 对开：最多 6 种排列、且两臂同桌 ⇒ 新判据下拒判
    spg = seat_perm_report(fam10)
    aig = arm_isolation_report(fam10)
    say(spg["coverage"] != "24/24" and not spg["usable"]
        and aig["families"][0]["cooccur"] > 0 and not aig["ok"],
        "㉑ `a,a,b,b` 2+2 对开（历次 gate-dir）⇒ 排列天花板 6、candidate 与 baseline 同桌 ⇒ 拒判",
        f"coverage={spg['coverage']} cooccur={aig['families'][0]['cooccur']}"
        f"/{aig['families'][0]['n_games']}")
    resg = dict(res10)
    resg.update({"seat_perm": spg, "arm_isolation": aig,
                 "opponent_pool": opponent_pool_report(fam10, str(pin)),
                 "measurement_guards": measurement_guards(fam10),
                 "adjacency_conditional": adjacency_conditional(fam10)})
    resg["measurement_validity"] = measurement_validity(resg)
    vg, whg, cg = judge(resg, a, 60.0, no_training=True, quiet_ok=True, crosscheck_ok=True)
    say(vg == "ERROR" and cg == EXIT_MEASUREMENT_VALIDITY,
        "㉑-b 该形状 ⇒ 退出码 5（⛔ 不许把历史 2+2 读数直接当 24 排列读数）",
        f"verdict={vg} exit={cg}")
    # ㉒ ★ 向后兼容：旧证据没有四席身份 ⇒ seat_perm=null 语义 + 警告，⛔ 不许当成通过
    old = tmp / "old-base"
    _synth_run(old, [123456789], 0.0, student_net=str(net_b), opp_nets=opp, rng=rng)
    oj = json.loads((old / "summary.json").read_text(encoding="utf-8"))
    for g in oj["per_game"]:
        g.pop("policies", None)
    (old / "summary.json").write_text(json.dumps(oj, ensure_ascii=False), encoding="utf-8")
    sp_old = _seat_perm_for_run(ml_eval.load_run(old), "x", "old")
    sp_rep = seat_perm_report([{"name": "old", "_base_run": ml_eval.load_run(old),
                                "policies": {"base": "b", "cand": "c"}}])
    say(sp_old["available"] is False and sp_old["usable"] is False
        and "不可证" in sp_old["reason"] and sp_rep["available"] is False,
        "㉒ 旧证据无四席身份 ⇒ seat_perm 不可证 + usable=false（⛔ 不当通过）",
        sp_old["reason"])
    # ㉓ 硬前置②：没有对手池 pin ⇒ 不能只凭"我没看到同支"就放行
    op_nopin = opponent_pool_report(fam_f, "")
    say(op_nopin["ok"] is False and "未提供" in op_nopin["reason"],
        "㉓ 未提供 `--opponent-pin` ⇒ 对手冻结/白名单不可证（fail-closed）",
        op_nopin["reason"])
    # ⑫ 结构性自证：本文件的子进程只有**两处**（进程扫描 / 采集执行），且采集只拼 `style-vector.py`
    import ast
    src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr in ("run", "Popen", "call", "check_output")
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"]
    consts = {n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    say(len(calls) == 2, "⑫子进程只有两处（进程扫描 / 采集执行）—— 没有任何训练入口",
        f"{len(calls)} 处")
    say(any("style-vector.py" in c for c in consts),
        "⑫采集只拼既有生产路径 `tools/style-vector.py`（它自己硬断言 `--rotate-perm`）",
        "见 collect_plan()")

    class B(A):
        pass

    b = B()
    for k in ("min_walls", "budget_min", "metric", "guards", "tag", "python", "trainer", "table",
              "base_net", "cand_net", "student_suffix", "workers", "collect_out", "wall"):
        setattr(b, k, getattr(a, k, None))
    b.workers, b.tag, b.table, b.base_net, b.cand_net, b.collect_out = 8, "st", "", "", "", str(tmp)
    b.wall, b.student_suffix, b.python = ["1"], "@0#0.5", ""
    prB = Probes()
    plans = None
    try:
        plans = collect_plan(b, prB)
    except SystemExit as e:
        plans = None
        _say(f"  （采集前置不全的拒绝理由：{str(e)[:80]}）")
    say(plans is None, "⑬采集前置不全（无 trainer / 无权重）⇒ 拒绝、不给计划（fail-closed）",
        f"红探针 {[r['id'] for r in prB.failures()]}")
    # ⑰ R-2 准入（起跑前）：commit_free vs 预期峰值×1.5 **与** 空闲基线
    a_ok = admission_check(10.5, 4.0)          # 4×1.5 = 6 ≤ 10.5 且 ≤ 空闲基线
    a_low = admission_check(5.0, 4.0)          # 5 < 6 ⇒ 拒
    a_none = admission_check(10.5, None)       # 未声明预期峰值 ⇒ fail-closed 拒
    a_inf = admission_check(12.0, 8.0)         # 12 > 空闲基线 10.5 ⇒ 配方不可行，拒
    say(a_ok["ok"] and not a_low["ok"] and not a_none["ok"] and not a_inf["ok"]
        and admission_check is _R2.admission_check,
        "⑰ R-2 准入拒绝（缺省口径；与 overlap-orchestrator 是**同一份**实现）",
        f"准={a_ok['ok']} need={a_ok['need_gb']} · 不足={a_low['ok']} · 未声明={a_none['ok']}"
        f" · 不可行={a_inf['ok']} · 同一实现={admission_check is _R2.admission_check}")
    # ⑱ 跑中越线：只记账 + 拒绝新增负载（⛔ 不杀）；只有硬地板才 kill
    g = _R2.AdmissionGate(4.0)
    o1 = g.observe(10.0)
    o2 = g.observe(5.0)
    allow_after, _why = g.allow_new_load()
    o3 = g.observe(1.5)
    say((not o1["kill"]) and (not o1["closed"]) and o2["closed_now"] and (not o2["kill"])
        and o2["closed"] and (allow_after is False) and o3["kill"],
        "⑱ 跑中越线 ⇒ 只记账 + 拒绝新增负载（⛔ 不杀当前相）；硬地板才 kill",
        f"正常 kill={o1['kill']} · 越线 kill={o2['kill']} closed={o2['closed']} "
        f"新负载={allow_after} · 硬地板 kill={o3['kill']}")
    _say(f"  临时目录：{tmp}（自检结束即删）")
    try:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    except OSError:
        pass
    _say(f"[ceiling-gate] SELFTEST {'PASS' if bad == 0 else f'FAIL（{bad} 例）'}")
    return 0 if bad == 0 else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(description="便宜 ceiling 闸门（BL-03）：一条命令 ⇒ verdict.json")
    ap.add_argument("--tag", default="", help="闸门标签（解析到 <数据根>/gate/<tag>）")
    ap.add_argument("--gate-dir", default="", help="既有闸门采集目录（含 s<seed>/summary.json）")
    ap.add_argument("--family", action="append", default=[],
                    help="`标签=<base目录>;<cand目录>`（可重复；学生席口径）")
    ap.add_argument("--incumbent", default="", help="--gate-dir：基准臂标签子串（缺省抄旧 verdict.json）")
    ap.add_argument("--candidate", default="", help="--gate-dir：候选臂标签子串（缺省抄旧 verdict.json）")
    ap.add_argument("--metric", default=DEFAULT_METRIC, choices=ml_eval.METRICS)
    ap.add_argument("--cuped-report", action="store_true",
                    help="可选（缺省关）：只在 verdict.json 额外写 estimator.cuped_report；"
                         "判决仍用 O0，⛔ 不改 PASS/FAIL/退出码")
    ap.add_argument("--guard-metric", default=DEFAULT_GUARDS,
                    help=f"护栏 `名[:容忍带]`（逗号分隔）；缺省 = gate.QUALITY_TOL：{DEFAULT_GUARDS}")
    ap.add_argument("--out", default="", help="verdict.json 落点（缺省 <数据根>/w-ladder/ceiling-gate/）")
    ap.add_argument("--budget-min", type=float, default=DEFAULT_BUDGET_MIN, help="墙钟预算（分钟）")
    ap.add_argument("--min-walls", type=int, default=2, help="判「过」所需最少族数（缺省 2）")
    ap.add_argument("--collect", action="store_true", help="新臂：自己采（≤--games 场/臂/族）")
    ap.add_argument("--wall", action="append", default=[], help="--collect：牌山族 seed（可重复）")
    ap.add_argument("--games", type=int, default=500, help="--collect：每族每臂场数")
    ap.add_argument("--base-net", default="", help="--collect：基准臂 net.bin")
    ap.add_argument("--cand-net", default="", help="--collect：候选臂 net.bin")
    ap.add_argument("--table", default="", help="--collect：风格桌三个对手 net.bin（逗号分隔）")
    ap.add_argument("--student-suffix", default="@0#0.5", help="--collect：学生席后缀（与链内一致）")
    ap.add_argument("--workers", type=int, default=8, help=f"采集并行度（⛔ ≤{MAX_WORKERS}）")
    ap.add_argument("--python", default="", help="--collect：跑 style-vector.py 的解释器")
    ap.add_argument("--trainer", default="", help="--collect：trainer.exe")
    ap.add_argument("--mem-poll-sec", type=float, default=1.0,
                    help="--collect：跑动期轮询间隔（秒）；缺省=准入控制（越线只记账+拒绝新增负载）")
    ap.add_argument("--adm-peak-gb", type=float, default=None,
                    help="R-2 准入：该相**预期峰值**（GB）。缺省 = 不声明 ⇒ **拒绝起跑**（fail-closed）")
    ap.add_argument("--mem-kill", action="store_true",
                    help="退回**旧绝对口径**：commit_free < 8 GB ⇒ 立即停 + kill 进程树（缺省=准入控制）")
    ap.add_argument("--collect-out", default=str(COLLECT_ROOT), help="--collect：采集落点根")
    ap.add_argument("--allow-busy", action="store_true",
                    help="--collect：机器上有别人的负载也跑（⛔ 计时会被污染）")
    ap.add_argument("--dry-run", action="store_true", help="只打印采集命令行，不真跑、不判定")
    ap.add_argument("--selftest", action="store_true", help="离线自检（含负向红证）")
    ap.add_argument("--legacy-placebo", action="store_true",
                    help="回退旧安慰剂口径（零和主口径下也按 |placebo|≥|primary| 否决；"
                         "该开关下 placebo 字段与判决理由逐字节不变）")
    ap.add_argument("--opponent-pin", default="",
                    help="对手池冻结白名单（JSON：schema=opponent-pin/1、frozen=true、"
                         "allowed={sha256:{name,line}}）；缺省 ⇒ 测量有效性 fail-closed（退出码 5）")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.workers > MAX_WORKERS:
        _say(f"⛔ --workers {args.workers} > {MAX_WORKERS}（本任务纪律）⇒ 拒绝")
        return 2
    mode = "collect" if args.collect else ("gate-dir" if (args.gate_dir or args.tag) else "family")
    t0 = time.time()
    probes = Probes()
    res: dict = {"errors": [], "mode": mode}
    meta: dict = {}
    _say(f"== ceiling-gate · mode={mode} · metric={args.metric} · 预算 {args.budget_min:g} min ==")

    # ---- 起跑体检（零训练自证 + G7 静窗 + 提交余量）------------------------------
    procs, perr = scan_processes()
    probes.add("proc_scan", bool(procs) and not perr,
               f"{len(procs)} 个进程" if procs else f"取不到进程表：{perr}")
    if not procs:
        res["errors"].append(f"进程扫描失败（零训练自证取不到）：{perr}")
    skip = self_chain(procs)
    chain_detail = skip_chain_detail(procs, skip)
    hits0 = training_hits(procs, skip)
    no_training = probes.add("no_training", not hits0,
                             "0 个训练进程（已排除我自己这条启动链："
                             f"{[p['name'] for p in chain_detail]}）" if not hits0
                             else f"**命中 {len(hits0)} 个**：{[(h['name'], h['patterns']) for h in hits0]}")
    foreign = quiet_foreign(procs, skip)
    quiet_ok = probes.add("quiet_window", not foreign,
                          "静窗（无别人的 trainer / 重 python / 重 java）" if not foreign
                          else f"有 {len(foreign)} 个竞争负载：{[(p['name'], p['pid']) for p in foreign]}")
    free0 = commit_free_gb()
    adm0 = None
    if bool(getattr(args, "mem_kill", False)):
        probes.add("commit_free", (not math.isfinite(free0)) or free0 >= MIN_COMMIT_FREE_GB,
                   f"commit_free={free0:.2f} GB（闸门 {MIN_COMMIT_FREE_GB:g} GB）")
        if args.collect and math.isfinite(free0) and free0 < MIN_COMMIT_FREE_GB:
            res["errors"].append(f"commit_free {free0:.2f} GB < {MIN_COMMIT_FREE_GB:g} GB ⇒ 立即停")
    else:
        adm0 = admission_check(free0, getattr(args, "adm_peak_gb", None),
                               idle_baseline_gb=IDLE_BASELINE_GB, margin=ADMISSION_MARGIN,
                               hard_floor_gb=HARD_FLOOR_GB)
        probes.add("commit_free", bool(adm0["ok"]),
                   f"R-2 准入：commit_free={free0:.2f} GB / 预期峰值 {adm0['expected_peak_gb']} GB "
                   f"× {ADMISSION_MARGIN:g} = {adm0['need_gb']} GB（空闲基线 {IDLE_BASELINE_GB:g} GB）"
                   + ("" if adm0["ok"] else " ⇒ " + "；".join(adm0["reasons"])))
        if args.collect and not adm0["ok"]:
            res["errors"].append("R-2 准入不过：" + "；".join(adm0["reasons"]))
    if args.collect and foreign and not args.allow_busy:
        res["errors"].append("G7 静窗不满足（--collect 的计时会被污染）⇒ 先等安静，或显式 --allow-busy")

    # ---- 取数 -------------------------------------------------------------------
    guards = [v4gate.parse_guard_metric(s) for s in args.guard_metric.split(",") if s.strip()]
    if any(g == args.metric for g, _ in guards):
        res["errors"].append(f"护栏指标 {args.metric} 与主口径相同（⛔ 主口径已经管了它自己）")
    families: list = []
    try:
        if args.collect:
            if not args.wall:
                raise SystemExit("⛔ --collect 需要至少一个 --wall <seed>（一族牌山）")
            specs = collect_plan(args, probes)
            if args.dry_run:
                for s in specs:
                    _say(f"[dry-run] {s['wall']}-{s['arm']} ⇒ {s['out']}\n          $ {' '.join(s['cmd'])}")
                _say("[ceiling-gate] DRY-RUN OK（只验命令行，不采集、不判定）")
                return 0
            collect_run(args, specs, probes)
            families, meta = _read_family_specs(build_collect_specs(args), args.metric, guards, probes)
        elif mode == "gate-dir":
            tag_dir = Path(args.gate_dir) if args.gate_dir else (GATE_DIR / args.tag)
            probes.add("gate_dir", tag_dir.is_dir(), str(tag_dir))
            if not tag_dir.is_dir():
                raise SystemExit(f"⛔ 没有这个闸门目录：{tag_dir}")
            inc, cand = args.incumbent, args.candidate
            vp = tag_dir / "verdict.json"
            if (not inc or not cand) and vp.is_file():
                v = json.loads(vp.read_text(encoding="utf-8"))
                inc = inc or str(v.get("incumbent", ""))
                cand = cand or str(v.get("candidate", ""))
                probes.add("arms_from_verdict", bool(inc and cand),
                           f"从 {vp.name} 抄到两臂子串")
            if not (inc and cand):
                raise SystemExit("⛔ --gate-dir 需要 --incumbent/--candidate（或目录里有 verdict.json）")
            families, meta = _read_gate_families(tag_dir, inc, cand, args.metric, guards, probes)
        else:
            if not args.family:
                raise SystemExit("⛔ 要么 --gate-dir/--tag，要么 --family，要么 --collect（或 --selftest）")
            families, meta = _read_family_specs(args.family, args.metric, guards, probes)
    except SystemExit as e:
        res["errors"].append(str(e))

    miss_w = weight_probe(families, probes) if families else []
    if miss_w:
        res["errors"].append(f"权重取不到：{miss_w}")

    # ---- 测量有效性硬前置（先算，失败也照样落盘读数供复核）--------------------
    if families:
        sp = seat_perm_report(families)
        #: ⛔ 向后兼容：旧证据没有四席身份 ⇒ **字面 null**（不是"看起来像通过"的空对象）
        res["seat_perm"] = sp if sp.get("available") else None
        if res["seat_perm"] is None:
            res["seat_perm_note"] = sp.get("reason")
        res["arm_isolation"] = arm_isolation_report(families)
        res["opponent_pool"] = opponent_pool_report(families, args.opponent_pin)
        res["measurement_guards"] = measurement_guards(families)
        res["adjacency_conditional"] = adjacency_conditional(families)
        res["measurement_validity"] = measurement_validity(res)

    # ---- 判定 -------------------------------------------------------------------
    if families and not res["errors"]:
        res.update(eval_from_series_per_family(families, args.metric, guards, args))
        attach_placebo(res, families, mode, args.metric, legacy=bool(args.legacy_placebo))
        res["mean_reversion"] = mean_reversion(families, mode, args.metric)
    elif not families:
        res["errors"].append("没有可用的族（一族的读数都没取到）⇒ fail-closed")
    res["metric"] = args.metric
    res["units"] = {"rank_points": "顺位点/场/席（正 = 更好）",
                    "deals": "放铳次数/场/席（已取负：正 = 更好）",
                    "wins": "和了次数/场/席（正 = 更好）",
                    "win_points": "和了点/场/席（正 = 更好）",
                    "place": "顺位取负/场/席（正 = 更好）",
                    "score": "得点/场/席（正 = 更好）"}.get(args.metric, args.metric)
    elapsed = time.time() - t0
    res["wall_clock_ok"] = elapsed <= args.budget_min * 60.0

    # ---- 收尾体检（采集之后再看一眼：**没有训练进程**）-------------------------
    procs_end, perr_end = [], ""
    if args.collect:
        procs_end, perr_end = scan_processes()
        hits1 = training_hits(procs_end, skip)
        probes.add("no_training_end", bool(procs_end) and not hits1,
                   f"收尾扫描 {len(procs_end)} 个进程："
                   + ("0 个训练进程" if not hits1 else f"**命中 {[h['name'] for h in hits1]}**"))
        if not procs_end or hits1:
            res["errors"].append("收尾零训练自证取不到（或命中训练特征）")

    # ---- 复算对账（只有 `--gate-dir` 有旧 verdict）-----------------------------
    ck_ok = True
    if mode == "gate-dir" and families:
        ck_ok = crosscheck(Path(meta.get("tag_dir") or ""), res, probes)

    verdict, reasons, code = judge(res, args, elapsed, no_training=no_training,
                                   quiet_ok=quiet_ok, crosscheck_ok=ck_ok)

    # ---- 产物 -------------------------------------------------------------------
    out = Path(args.out) if args.out else (CEIL_STAGE / f"{args.tag or 'gate'}-verdict.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SCHEMA,
        "generated_at": _now(),
        "mode": mode,
        "caliber": {
            "统计量来源": "全部借既有实现：`mahjong_ml.eval`（逐场取值 / 配对 / 自助 CI / 符号检验 / 合并 / "
                        "随机效应 / required_n）· `mahjong_ml.v4.gate`（pooled_diffs / guard_row / decide）· "
                        "`mahjong_ml.v4.arena`（judge_pair / judge_series）⇒ ⛔ 本工具不自算第二套统计量",
            "方向": "`eval.seat_values` 已把各指标折成「正 = 更好」（deals 取负）⇒ 护栏走 "
                  "`gate.guard_row` 的单侧规则 ok ⇔ CI 下界 ≥ −tol，⛔ 不另立方向表",
            "主口径": f"{args.metric}（{res.get('units')}）· 逐场配对（同一副牌山）· 多族按 "
                    "`gate.pooled_diffs` 加键偏移后合并",
            "判决规则": "过 ⇔ 主口径 CI 下界>0 ∧ 可用护栏未越界 ∧ 安慰剂不显著 ∧ 族数≥min-walls ∧ "
                      "各族逐族为正 ∧ |ρ|<0.9 ∧ 墙钟≤预算；不过 ⇔ CI 上界<0；测不出 ⇔ 其余；"
                      "ERROR ⇔ 任何必需探针取不到",
            "护栏可用性": "容忍带 ≥ 实际 MDE 才可作否决依据；tol/MDE <1 ⇒ 天生必破 = 假警报，只记录不否决"
                        "（AGENTS §6.5 第七十一轮）",
            "口径边界": "本判决**条件于这几族牌山**；族数 <2 时套间散布未测 ⇒ 不许判「过」"
                      "（跨族不自洽的不外推，AGENTS §6.5）",
            "零训练": "只有自对弈采集（`style-vector.py` → `trainer.exe selfplay`），"
                    "⛔ 无 PPO / 无 pretrain / 无 v4 loop",
            "测量有效性": "项目方 2026-10-19 两条硬前置（fail-closed，退出码 5）："
                        "① 每族每臂 24 排列覆盖 + 每种场次 ≥ max(1, floor(n/24×0.8))；"
                        "② 共轭三条 = candidate≠baseline 同桌 / 对手池冻结+sha256 白名单+非本支 / "
                        "同支邻家优势 ≤1.2×。旧证据无四席身份 ⇒ seat_perm=null + 警告，⛔ 不当通过。",
            "护栏指标": "self_win_rate / deal_in_rate / meld_rate / avg_hand_value / "
                      "adjacency_conditional 必须进本文件；summary.json 缺的字段记 null + "
                      "available:false + warning（⛔ 不伪造）。",
        },
        "wall_clock_s": round(elapsed, 2),
        "wall_clock_budget_s": args.budget_min * 60.0,
        "wall_clock_ok": res["wall_clock_ok"],
        "arms": meta,
        "no_training_proof": {"scanned_at": _now(), "n_procs": len(procs), "scan_error": perr,
                              "training_hits": hits0, "patterns": list(TRAINING_PATTERNS),
                              "skipped_pids": sorted(skip), "skipped_chain": chain_detail,
                              "end_scan": ({"n_procs": len(procs_end), "scan_error": perr_end,
                                            "training_hits": training_hits(procs_end, skip)}
                                           if args.collect else None)},
        "resource_gate": {"commit_free_gb_start": free0, "quiet_window_ok": quiet_ok,
                          "mode": "mem-kill" if bool(getattr(args, "mem_kill", False))
                                  else "admission",
                          "admission_start": adm0,
                          "idle_baseline_gb": IDLE_BASELINE_GB, "hard_floor_gb": HARD_FLOOR_GB,
                          "admission_margin": ADMISSION_MARGIN,
                          "foreign_load": [{"name": p["name"], "pid": p["pid"], "ws_mb": p["ws_mb"]}
                                           for p in foreign],
                          "workers_requested": args.workers, "workers_max": MAX_WORKERS},
        "probes": probes.rows,
        "primary": res.get("primary"),
        "walls": res.get("walls"),
        "mde": res.get("mde"),
        "guards": res.get("guards"),
        "placebo": res.get("placebo"),
        "mean_reversion": res.get("mean_reversion"),
        "seat_perm": res.get("seat_perm"),
        "seat_perm_note": res.get("seat_perm_note"),
        "arm_isolation": res.get("arm_isolation"),
        "opponent_pool": res.get("opponent_pool"),
        "measurement_guards": res.get("measurement_guards"),
        "adjacency_conditional": res.get("adjacency_conditional"),
        "measurement_validity": res.get("measurement_validity"),
        "crosscheck": res.get("crosscheck"),
        "reasons": reasons,
        "errors": res["errors"],
        "verdict": verdict,
        "exit_code": code,
    }
    #: ⛔ 可选、仅报告：缺省**一个键都不加**（判决/judge/退出码在上一行已经定死，后面不再改）。
    if bool(getattr(args, "cuped_report", False)):
        cr, cr_warn = cuped_report(families, args.metric)
        payload["estimator"] = {"cuped_report": cr, "warnings": cr_warn}
        probes.add("cuped_report", cr is not None,
                   ("CUPED 报告已写入（仅报告、判决仍 O0）" if cr is not None
                    else "；".join(cr_warn) or "CUPED 报告取不到 ⇒ null"))
    if not args.legacy_placebo:
        #: ⛔ 只在**新口径**下写这两处：`--legacy-placebo` 的产物必须与旧版逐字节一致。
        payload["caliber"]["安慰剂口径"] = (
            "主口径若**结构性加和**（每场四席求和恒定 ⇒ Σ三名对手 Δ ≡ −Δ_student）"
            "⇒ `placebo.usable_for_veto=false`（只记账 `why_zero_sum`/`redistribution_ratio`），"
            "改用非零和指标（wins/deals）上的同类对照；⛔ 零和下不再用 `|安慰剂|≥|主口径|` 否决"
            "（2026-10-17 据 `lab/mv1/MV1-STATIC-AUDIT.md` §1-5）")
        payload["zero_sum"] = res.get("zero_sum")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _say(f"\n[ceiling-gate] 判决 **{verdict}**（退出码 {code}）· 墙钟 {elapsed:.1f} s"
         f"（预算 {args.budget_min:g} min）")
    for r in reasons:
        _say(f"   · {r}")
    _say(f"   落盘 {out}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

