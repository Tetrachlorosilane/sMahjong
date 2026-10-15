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
             ∧ 各族 Δ 逐族为正 ∧ |ρ| < 0.9 ∧ 墙钟 ≤ 预算。
    不过   ⇔ 主口径 **CI 上界 < 0**（候选在坏方向显著）。
    测不出 ⇔ 其余（CI 含 0 / 单族 / 安慰剂与主口径不可分辨 / ρ 报警 / 超预算 / SE 取不到）。
    ERROR  ⇔ **任何必需探针取不到**（目录 / `summary.json` / `per_game` / 权重文件 / 族数 0 / 进程扫描失败 …）。
    ⛔ fail-closed：**取不到就 ERROR 且退出码非 0**，⛔ 不许"取不到就当干净/当通过"。

退出码：**0 = 过**；**3 = 不过**；**4 = 测不出**；**2 = ERROR**（用法/文件/探针坏）。
⛔ 本工具**不训练**：`--collect` 只跑自对弈采集（`style-vector.py` → `trainer.exe selfplay`），
并自带"零训练进程"自证（起跑/收尾各扫一次全部进程的**命令行**，命中任何训练特征 ⇒ ERROR）。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import math
import os
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

SCHEMA = "ceiling-gate/1"
DEFAULT_BUDGET_MIN = 30.0            # BL-03 的硬判据：墙钟 ≤30 min
DEFAULT_METRIC = "rank_points"       # 与闸门主口径/护栏同一个指标名（`eval.METRICS` 之一）
DEFAULT_GUARDS = "wins:0.10,deals:0.10,win_points:150"   # = `gate.QUALITY_TOL`（预注册容忍带）
MAX_WORKERS = 8                      # 本任务纪律：`--workers ≤8`
MIN_COMMIT_FREE_GB = 8.0             # 本任务纪律：commit_free < 8 GB 立即停
QUIET_MIN_MB = 200.0                 # G7 静窗：python/java 只算"重"的（b1-overlap 既有判据）

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


# ---------------------------------------------------------------------------
# 判决
# ---------------------------------------------------------------------------
def judge(res: dict, args, elapsed: float, *, no_training: bool, quiet_ok: bool,
          crosscheck_ok: bool) -> tuple[str, list, int]:
    reasons: list = []
    if res.get("errors"):
        return "ERROR", list(res["errors"]), 2
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
    single = walls["n_walls"] < int(args.min_walls)
    if not walls.get("arm_pair_consistent", True):
        reasons.append("族间不是同一对权重 ⇒ 套间散布混了臂差（读作上界）⇒ 不许判「过」")
    if single:
        reasons.append(f"族数 {walls['n_walls']} < --min-walls {args.min_walls}"
                       f"⇒ 套间散布未测 ⇒ 不许判「过」")
    if pl.get("available", True) is False:
        reasons.append("安慰剂对照取不到 ⇒ 不许判「过」")
    elif pl["significant"]:
        reasons.append(f"安慰剂**显著**（Δ={pl['delta']:+.3f} CI[{pl['ci'][0]:+.2f},{pl['ci'][1]:+.2f}]）"
                       f"⇒ 主口径那点差有对照级别的解释 ⇒ 测不出")
    if pl.get("available", True) is not False and abs(pl["delta"]) >= abs(prim["delta"]):
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
    ok_pass = (lo > 0 and (not pl["significant"]) and pl.get("available", True) is not False
               and abs(pl["delta"]) < abs(prim["delta"]) and not single
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
    """真跑采集：**串行**（一次一个重负载）+ **跑之前与跑期间都查提交余量**（< 8 GB 立即停）。

    ⚠ 为什么必须**跑期间**也查：原先只有 `subprocess.run` **起跑前**那一次检查
    （`commit_free >= 8 GB` 才起）⇒ 采集涨起来把提交吃穿之后**没有人再看**，纪律线只写在
    文档里而没有工具执行（NOTES §6.5 第八十三轮的"4 个相瞬时 <8 GB、最低 5.00 GB"就是这么来的）。
    现在：1 s 栅格轮询 + 越线 **kill 进程树** + `SystemExit`（退出码 1，与其它闸门一致）。
    """
    env = dict(os.environ)
    env["MAHJONG_DATA_ROOT"] = str(DATA)
    env["MAHJONG_TRAINER"] = str(args.trainer or os.environ.get("MAHJONG_TRAINER", ""))
    env["PYTHONIOENCODING"] = "utf-8"
    env["OMP_NUM_THREADS"] = str(min(args.workers, MAX_WORKERS))
    env["MKL_NUM_THREADS"] = str(min(args.workers, MAX_WORKERS))
    poll = float(getattr(args, "mem_poll_sec", 1.0) or 1.0)
    for s in specs:
        free = commit_free_gb()
        probes.add(f"collect:mem:{s['wall']}-{s['arm']}", free >= MIN_COMMIT_FREE_GB,
                   f"commit_free={free:.2f} GB（闸门 {MIN_COMMIT_FREE_GB:g} GB）")
        if free < MIN_COMMIT_FREE_GB:
            raise SystemExit(f"⛔ commit_free {free:.2f} GB < {MIN_COMMIT_FREE_GB:g} GB ⇒ 立即停")
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
                if free < MIN_COMMIT_FREE_GB:
                    trip = free
                    break
            if trip is not None:
                _say(f"⛔ [采集期内存闸门] commit_free={trip:.2f} GB < {MIN_COMMIT_FREE_GB:g} GB "
                     f"（跑动中）⇒ kill 进程树、本相作废")
                probes.add(f"collect:kill_tree:{s['wall']}-{s['arm']}",
                           bool(kill_tree(p.pid).get("killed")),
                           f"taskkill /T /F /PID {p.pid}（崩床/真身两层都要杀）")
                p.kill()
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    pass
                probes.add(f"collect:mem_kill:{s['wall']}-{s['arm']}", False,
                           f"跑动中 commit_free={trip:.2f} GB < {MIN_COMMIT_FREE_GB:g} GB ⇒ 杀相并停")
                raise SystemExit(f"⛔ 采集跑动中 commit_free {trip:.2f} GB < {MIN_COMMIT_FREE_GB:g} GB "
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
               w_list: list = None) -> None:
    """造一份 `summary.json`（**学生席口径**的形状：学生串带 `@`，三席对手同序）。

    `per_game[]` 必须带 `rank_points` / `wins` / `deals` / `win_points`（`eval.seat_values` 要它们）；
    学生席在最前（与 `style-vector.py --policy net:<学生>@0#0.5,<对手>…` 的写法一致）。
    ⚠ `w_list`：**同一族两条采集要共用同一份"牌山运气"**（真配对的前提）—— 给了就用它，
    ⛔ 别让两条采集各自抽一份（那样"配对"就退化成两个独立样本了）。
    """
    d.mkdir(parents=True, exist_ok=True)
    stu = f"net:{student_net}@0#0.5"
    ops = [f"net:{t}" for t in opp_nets]
    pg = []
    for i, s in enumerate(seeds):
        w = float(w_list[i]) if w_list is not None else float(rng.normal(0, sd_w))
        rp = [w + delta + float(rng.normal(0, sd_n))] + [w + float(rng.normal(0, sd_n))
                                                         for _ in ops]
        wins = [float(rng.integers(0, 5)) for _ in range(1 + len(ops))]
        #: 学生席刻意多加放铳 ⇒ 用来造一条"真越界"的护栏（`deals` 取负 ⇒ Δ = −bias）
        deals = [float(rng.integers(0, 2)) + deal_bias] + [float(rng.integers(0, 2))
                                                           for _ in ops]
        pg.append({"game": i, "seed": int(s), "policies": [stu] + ops,
                   "rank_points": rp, "wins": wins, "deals": deals,
                   "win_points": [1000.0 * x for x in wins],
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
    net_b, net_c = tmp / "base.bin", tmp / "cand.bin"
    net_b.write_bytes(b"base")
    net_c.write_bytes(b"cand")
    opp = []
    for k in range(3):
        p = tmp / f"opp{k}.bin"
        p.write_bytes(f"opp{k}".encode())
        opp.append(str(p))

    class A:                                  # 判据函数要的 args 子集
        pass

    a = A()
    a.min_walls, a.budget_min, a.metric = 2, 30.0, "rank_points"
    a.guards = [("deals", 0.10), ("wins", 0.10)]

    def mk(prefix, delta, n_games, sd_n=3.0, deal_bias=0.0, student=None):
        seeds = [int(x) for x in rng.integers(1 << 40, 1 << 60, n_games)]
        #: ★ 两条采集共用同一份"牌山运气" w（真配对的前提）
        w_list = [float(rng.normal(0, 50.0)) for _ in range(n_games)]
        b = tmp / f"{prefix}-base"
        c = tmp / f"{prefix}-cand"
        _synth_run(b, seeds, 0.0, student_net=str(net_b), opp_nets=opp, rng=rng,
                   sd_n=sd_n, w_list=w_list)
        _synth_run(c, seeds, delta, student_net=str(student or net_c), opp_nets=opp, rng=rng,
                   sd_n=sd_n, deal_bias=deal_bias, w_list=w_list)
        return f"{prefix}={b};{c}"

    def run_case(specs, metric="rank_points", guards=(("deals", 0.10), ("wins", 0.10))):
        pr = Probes()
        fam, _meta = _read_family_specs(list(specs), metric, list(guards), pr)
        a.guards = list(guards)
        res = eval_from_series_per_family(fam, metric, list(guards), a)
        res["metric"] = metric
        res["placebo"] = placebo_null_contrast(fam, "family", metric) if fam else {}
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
    fam10, meta10 = _read_gate_families(tag, "base.bin", "cand.bin", "rank_points",
                                       [("deals", 0.10)], pr10)
    say(len(fam10) == 2, "⑩-a `--gate-dir` 逐族读通（零采集复算路径）", f"{len(fam10)} 族")
    runs10 = [ml_eval.load_run(g) for g in g_dirs]
    lc = ml_eval.resolve_label(runs10[0], "cand.bin")
    lb = ml_eval.resolve_label(runs10[0], "base.bin")
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
                    help="--collect：采集**跑动期**的提交余量轮询间隔（秒）；越线即树杀 + 停（⛔ 不可关）")
    ap.add_argument("--collect-out", default=str(COLLECT_ROOT), help="--collect：采集落点根")
    ap.add_argument("--allow-busy", action="store_true",
                    help="--collect：机器上有别人的负载也跑（⛔ 计时会被污染）")
    ap.add_argument("--dry-run", action="store_true", help="只打印采集命令行，不真跑、不判定")
    ap.add_argument("--selftest", action="store_true", help="离线自检（含负向红证）")
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
    probes.add("commit_free", (not math.isfinite(free0)) or free0 >= MIN_COMMIT_FREE_GB,
               f"commit_free={free0:.2f} GB（闸门 {MIN_COMMIT_FREE_GB:g} GB）")
    if args.collect and foreign and not args.allow_busy:
        res["errors"].append("G7 静窗不满足（--collect 的计时会被污染）⇒ 先等安静，或显式 --allow-busy")
    if args.collect and math.isfinite(free0) and free0 < MIN_COMMIT_FREE_GB:
        res["errors"].append(f"commit_free {free0:.2f} GB < {MIN_COMMIT_FREE_GB:g} GB ⇒ 立即停")

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

    # ---- 判定 -------------------------------------------------------------------
    if families and not res["errors"]:
        res.update(eval_from_series_per_family(families, args.metric, guards, args))
        res["placebo"] = placebo_null_contrast(families, mode, args.metric)
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
        "crosscheck": res.get("crosscheck"),
        "reasons": reasons,
        "errors": res["errors"],
        "verdict": verdict,
        "exit_code": code,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _say(f"\n[ceiling-gate] 判决 **{verdict}**（退出码 {code}）· 墙钟 {elapsed:.1f} s"
         f"（预算 {args.budget_min:g} min）")
    for r in reasons:
        _say(f"   · {r}")
    _say(f"   落盘 {out}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
