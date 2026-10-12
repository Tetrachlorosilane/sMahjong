#!/usr/bin/env python3
"""重叠编排器 —— 把「两条互不依赖的相并跑」变成**可机检**的一件事（七闸门 G1–G7）。

## 这个工具替掉了哪一串手敲（为什么要有它）

数据根上先后长出了三代编排器：`b1-overlap.py`（compact ‖ 采集）、`b2-overlap.py`（有界读数 ‖ 采集，
`importlib` 复用 b1）、`b3b-overlap.py`（训练 ‖ 采集，再包一层 b2）。三代**同族演进**，闸门语义
一份、收尸逻辑一份 —— 但**都躺在数据根**：不进仓库、不受版本控制、下一个人要用得先找到数据根那份。
本文件把"最全的那一代"（b3b 的 G1 白名单 + b2 的 G7 java 规则 + b1 的 G2–G6 与收尸）**搬进仓库**，
并补掉 b3b 结果档 §5① 抓到的那条**静默失效**（见下）。

## 七闸门（不过 ⇒ 一条命令都不打；这不是风格，是判据）

  · **G1 kind/phase 白名单** —— 主相 ∈ {compact, readings, train} 且从相 kind ∈ {collect}。
    `train`/`critic`/`audit`/`eval`/**第二个 compact**/**未知 kind 一律拒** ⇒ **fail closed**。
    ⚠ "`compact ‖ train` 必须被拒"与"`train ‖ collect` 必须允许"是**同一条闸门**的两个取值：
    compact 槽被训练端 `pretrain --data <compact>` 读到时读的是**正在被写**的那份（读到哪一半是隐变量）。
  · **G2 两臂写目录不相交**（且互不为祖先）。
  · **G3 主相输入分片冻结**（存在 + 与从相写目录分开）。
  · **G4 采集策略权重 + 可执行文件跑前/跑后各 pin 一次 sha256**（兄弟任务可能中途重建 `trainer.exe`
    ⇒ 事后如实报"窗口内二进制变了"，而不是假装没发生）。
  · **G5 采集命令行不引用正在被写的产物** —— 判据只认**解析后的绝对路径**，⛔ **不按目录名**：
    两臂共用同一个 tag 时按名字一比立刻假阳性，而闸门误拦会让人直接把闸门关掉。
  · **G6 独占锁**（一次只做一个重叠对；stale 锁按 pid 回收）。
  · **G7 静窗** —— 名单含 `trainer.exe`（一律算）、重 `python`/`java`（≥200 MB）、
    **`java -jar mahjong-server.jar`（⛔ 不看内存，按命令行）**。

## ⛔ 本批最重要的修：探针必须 fail-closed（静默失效的红证）

`tasklist /FO CSV /V` 在 Win11 26200 上**根本没有命令行列**（CSV 只有 9 个字段、与不带 `/V`
**逐字符相同**）⇒ 旧实现"解析这一行"**永远拿不到命令行** ⇒ `server_jar` 恒 False ⇒ 真机起一个
**134 MB 的 `java -jar mahjong-server.jar`**，`check` 仍报"算竞争 0 个"、**exit=0（假绿）**，
而判据要求 exit=4。⇒ 修两处：

  ① 命令行改从 **`Win32_Process.CommandLine`**（CIM，**每个 pid 最多一次**，⛔ 不在采样循环里）
     取，退路 `wmic` → 老的 `tasklist /V` 解析，**最先成功的那个赢**，并把用了哪条如实报出来。
  ② 探针带 **"已调用 / 取到过 / 取不到的 pid"** 三个状态：**进程还活着却取不到命令行 ⇒ FAIL / 拒跑**
     （exit=4）。⛔ **绝不把"没取到"当"机器很安静"**，⛔ 也**不提供**任何"忽略探针失败"的开关。
     ⚠ 进程已经退掉而取不到 ⇒ 那是竞态不是失效，按"没这条"处理（否则会永久误拒）。

## 内存口径（⛔ 两条不许混用）

峰值**只认进程已提交私有字节**（`GetProcessMemoryInfo` 的 `PagefileUsage` 一类口径），
**常驻工作集另报**。标定（`b3b-memprobe.py` / `calibrate-mem` 模式，本机能复现）：
  · 2 GiB **匿名**提交   ⇒ committed ≈ +2059 MB（ws 同步）
  · 1 GiB **文件 mmap** ⇒ ws ≈ +1038 MB 而 **committed 仅 +8.8 MB**
⇒ 只拿 ws 报峰值会把 mmap 的文件页当成"提交内存"，把 14.78 GB 紧凑集读成"要 17 GB 提交"（实际 3.7 GB）。

**采样器不得高频调 CIM**：1 s 栅格只用 `GlobalMemoryStatusEx` + `GetProcessTimes` +
`psapi.GetProcessMemoryInfo`（P/Invoke），**零 CIM/WMI**；CIM 只出现在 G7 的**一次性**命令行探针里。

## 收尸与回退（⛔ 不许静默降级）

tree-kill（`taskkill /T /F`）→ **等它真退** → 带重试删 → **复验消失** → **看回退退出码**。
删不掉 ⇒ **不回退并 exit 5**（在半成品上重跑只会再失败；把它印成"已回退"是造假的读数）。

## 标志位语义（AGENTS.md §6.5 第三条，踩过两次十分钟级白跑）

一律写 `<= 0`；"0=关 / <0=不限"式预算**用尽置 0（不是 −1）**。
`--chunk <= 0` 是**坏值**（exit 2），⛔ **不是"不限"**。

## 用法

    python tools/overlap-orchestrator.py pair        --driver <spec.json> --pair train:collect --out r.json
    python tools/overlap-orchestrator.py check       --driver <spec.json> --pair compact:collect --out g.json
    python tools/overlap-orchestrator.py selftest    --out selftest.json
    python tools/overlap-orchestrator.py calibrate-mem --anon-gb 2 --file-gb 1 --out mem.json

`--data-root` / `--compact-root` / `--raw-root` / `--workers` / `--timeout` / `--driver` 全部可覆盖；
驱动规格里可用 `${DATA_ROOT}` `${COMPACT_ROOT}` `${RAW_ROOT}` `${WORKERS}` 占位符，
⇒ **同一份 spec 换根不用改一个字节**（⛔ 代码里除"缺省值（可覆盖）"那一段外不出现任何绝对盘符）。
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# ================================================================ 缺省值（⛔ 全部可覆盖）
# 这一段是**唯一**允许出现绝对盘符的地方（判据③：`Select-String 'S:\\|E:\\'` 的命中只许落在这里）：
DEFAULT_DATA_ROOT = os.environ.get("MAHJONG_DATA_ROOT") or r"S:\mahjong-training"
DEFAULT_COMPACT_ROOT = os.environ.get("MAHJONG_COMPACT_ROOT") or r"E:\mahjong-training\compact"
DEFAULT_RAW_ROOT = os.environ.get("MAHJONG_RAW_ROOT") or r"S:\mahjong-training\raw"
# ================================================================ 缺省值结束

TOOL_NAME = "overlap-orchestrator"
TOOL_VERSION = "1"

#: 主相白名单（⛔ 未知 phase ⇒ 拒，fail closed）
ALLOWED_PRIMARY_PHASES = ("compact", "readings", "train")
#: 从相 kind 白名单。**只有采集**能与上面三相并发。
ALLOWED_OTHER_KINDS = ("collect",)
#: 主相别名 ⇒ 规范名（老 spec 用 `readout` 表示"有界读数"）
PRIMARY_ALIASES = {"compact": "compact", "readout": "readings", "readings": "readings", "train": "train"}

#: 明写给用户的拒绝理由（判据 ① 的正文；⛔ 不许"静默允许"）
REFUSE_WHY = {
    "train": "训练端读紧凑集（`pretrain --data <compact>`）时会读到**正在被写**的那一份（读到哪一半是隐变量）；"
             "且训练峰值提交 ~3.7 GB / ws ~17 GB ⇒ 与 compact 同窗口必过内存闸门",
    "critic": "值头同样是 `pretrain --data <compact>` ⇒ 与 train 同一条禁令",
    "audit": "`value-audit --data <compact>` 读的是同一个紧凑集 ⇒ 同上",
    "eval": "评测起的是第二条采集 + 另一份权重 ⇒ 违反「一次只做一个重叠对」，且抢同一块盘的 IO",
    "compact": "两个 compact 同时写 ⇒ 违反「一次只做一个重叠对」（实测每条被拖慢 1.76×、更亏）",
    "readings": "两个读数相同时跑 ⇒ 抢同一块 GPU/同一份权重，且违反「一次只做一个重叠对」",
    "features": "派生特征写进**采集的输出目录** ⇒ 写集不交的判据不成立",
    "check": "校验相读采集输出，属于采集的尾部而非独立相 ⇒ 别塞进重叠窗口",
}

#: 收尸时允许被删的根（`spec` 里可覆盖）—— ⛔ 白名单外的路径一律拒绝删
DEFAULT_ALLOW_RM_ROOTS: tuple[str, ...] = ()


# ================================================================ 轻量读数（⛔ 高频循环里零 CIM）
class _MEM(ctypes.Structure):
    _fields_ = [("dwLength", wt.DWORD), ("dwMemoryLoad", wt.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def mem() -> dict:
    """**闸门口径**：`commit_free = Commit Limit − Committed Bytes` = `ullAvailPageFile`（P/Invoke）。"""
    m = _MEM()
    m.dwLength = ctypes.sizeof(_MEM)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    G = 1024 ** 3
    return {"commit_free_gb": round(m.ullAvailPageFile / G, 2),
            "commit_limit_gb": round(m.ullTotalPageFile / G, 2),
            "committed_gb": round((m.ullTotalPageFile - m.ullAvailPageFile) / G, 2),
            "free_phys_gb": round(m.ullAvailPhys / G, 2)}


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


_QUERY_LIMITED = 0x1000
_FILETIME = ctypes.c_ulonglong
_MB = 1024 ** 2


def proc_mem(pid: int) -> dict:
    """**峰值只认这个**：已提交私有字节（`PagefileUsage`）+ 常驻工作集（另报，⛔ 不许混用）。

    ⚠ 为什么必须两条都报：文件 mmap 只涨 ws、committed 几乎不动（1 GiB ⇒ ws +1038 / committed +8.8 MB）
    ⇒ 只看 ws 会把 14.78 GB 紧凑集的文件页读成"要 17 GB 提交"。
    """
    h = ctypes.windll.kernel32.OpenProcess(_QUERY_LIMITED | 0x0010, False, pid)
    if not h:
        return {}
    try:
        m = _PMC()
        m.cb = ctypes.sizeof(_PMC)
        if not ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb):
            return {}
        return {"committed_mb": round(m.PagefileUsage / _MB, 1),
                "peak_committed_mb": round(m.PeakPagefileUsage / _MB, 1),
                "ws_mb": round(m.WorkingSetSize / _MB, 1),
                "peak_ws_mb": round(m.PeakWorkingSetSize / _MB, 1)}
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def proc_cpu(pid: int) -> float | None:
    """进程累计 CPU 秒（`GetProcessTimes`，P/Invoke —— 用来判"核吃满没有"，⛔ 不用 CIM）。"""
    h = ctypes.windll.kernel32.OpenProcess(_QUERY_LIMITED, False, pid)
    if not h:
        return None
    try:
        c, e, k, u = (_FILETIME(), _FILETIME(), _FILETIME(), _FILETIME())
        if not ctypes.windll.kernel32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                                     ctypes.byref(k), ctypes.byref(u)):
            return None
        return round((k.value + u.value) / 1e7, 3)
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def proc_alive(pid: int) -> bool:
    """pid 还活着吗（只看句柄拿不拿得到；`OpenProcess` 对已退进程会失败）。"""
    if not pid:
        return False
    h = ctypes.windll.kernel32.OpenProcess(_QUERY_LIMITED, False, int(pid))
    if not h:
        return False
    ctypes.windll.kernel32.CloseHandle(h)
    return True


# ================================================================ 标志位 / 预算语义（AGENTS.md §6.5 第三条）
def chunk_rows(n) -> int:
    """`--chunk` 的语义：`<= 0` 是**坏值**（调用方 exit 2），⛔ **不是"不限"**。

    红证：`v4-forward-gpu.py` 的 `--chunk 0` 曾被当成"不限"⇒ 一次全物化 663k 行 = 15.1 GB 常驻
    （正是它把读数与训练挤在一起的那次）。"不限"必须是**另一个显式开关**，不能是 0/负数。
    """
    try:
        v = int(n)
    except (TypeError, ValueError):
        raise ValueError(f"--chunk 不是整数：{n!r}")
    if v <= 0:
        raise ValueError(f"--chunk={v} 是坏值（⛔ 0/负数**不是**'不限'；要不限请另开显式开关）")
    return v


def limit_active(limit) -> bool:
    """`0=关 / <0=不限` 式的**开关**判据：一律写 `<= 0`（⛔ 别写 `== 0`）。

    `--timeout` 用这条：`<=0` ⇒ 不限（关）。
    """
    try:
        return float(limit) > 0.0
    except (TypeError, ValueError):
        return False


def budget_after_spend(budget, spent) -> float:
    """预算**用尽置 0（不是 −1）**：`spent >= budget` ⇒ 0，⛔ 绝不返回负数。

    红证：`--limit` 过冲成负数被下游当"不限"（`--h0-rows` 用尽置 −1 反成"不限"）⇒ 十分钟级白跑
    两次。**负数在这个语义里等于"无限"，所以用尽必须是 0。**
    """
    try:
        b = float(budget)
        s = float(spent)
    except (TypeError, ValueError):
        return 0.0
    if b <= 0.0:
        return 0.0
    return max(0.0, b - s)


def workers_ok(n) -> int:
    """`--workers ≤ 8`（本机是 8 核档；>8 只会互相踩，且会让别人任务的计时不可信）。"""
    try:
        v = int(n)
    except (TypeError, ValueError):
        raise ValueError(f"--workers 不是整数：{n!r}")
    if v <= 0:
        raise ValueError(f"--workers={v} 是坏值（⛔ 0/负数不是'不限'）")
    if v > 8:
        raise ValueError(f"--workers={v} 超过硬上限 8（⛔ 与别人任务互踩的代价比多出来的核大）")
    return v


# ================================================================ 文件指纹 / 比较 / 收尸
def sha256_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def manifest(root: Path) -> dict:
    """(相对路径 → (大小, mtime_ns)) —— cheap，用来判"冻结的输入有没有被改过"。"""
    out: dict[str, list] = {}
    if root.is_dir():
        for f in sorted(root.rglob("*")):
            if f.is_file():
                st = f.stat()
                out[str(f.relative_to(root))] = [st.st_size, st.st_mtime_ns]
    return out


def compare_files(a, b) -> dict:
    """比较两个文件是否**逐字节**相同 —— 判据 ④ 的假绿守卫。

    ⛔ **两文件都缺 ⇒ 必红**：旧写法"两边哈希都拿不到 ⇒ 都空串 ⇒ 判成相同"就是假绿
    （AGENTS.md §6.5 第五条）。**先断存在性**，缺任何一个都直接 `same=False` 并点名。
    """
    pa, pb = Path(a), Path(b)
    if not pa.is_file() or not pb.is_file():
        missing = [str(p) for p in (pa, pb) if not p.is_file()]
        return {"same": False, "reason": f"⛔ 文件不存在：{missing}（⛔ 缺文件**不是**'相同'，这是假绿守卫）",
                "a_exists": pa.is_file(), "b_exists": pb.is_file()}
    da, db = sha256_file(pa), sha256_file(pb)
    na, nb = pa.stat().st_size, pb.stat().st_size
    return {"same": da == db, "a_sha256": da, "b_sha256": db, "a_bytes": na, "b_bytes": nb,
            "reason": "逐字节相同" if da == db else f"⛔ 不同（{na} B vs {nb} B）"}


def rm_safe(p: Path, why: str, allow_roots: tuple, attempts: int = 6, sleep_s: float = 1.0) -> dict:
    """**白名单内**的目录删除（abort 时清半成品）。路径**先解析再判**，越界一律拒绝。

    ⚠ **不许 `ignore_errors=True`**：刚被 kill 的进程 mmap 的 `.npy` 在 Windows 上不会立刻释放，
    静默吞掉失败会让紧接着的串行回退在同一路径上 `open_memmap(w+)` 直接 `OSError [Errno 22]`，
    而编排器还印着"回退"。⇒ **带重试 + 删完复验真的不在了**；删不掉就如实报失败（调用方升级为 exit 5）。
    """
    rp = p.resolve()
    roots = [Path(r).resolve() for r in (allow_roots or ())]
    ok = any(rp == r or r in rp.parents for r in roots)
    depth_ok = rp.name not in ("", rp.drive) and rp.name != rp.anchor
    if not (ok and depth_ok and rp.is_dir()):
        return {"removed": False, "verified_gone": False, "path": str(rp), "why": why,
                "reason": "⛔ 不在允许删除的白名单根内（或不是目录）—— 拒绝删",
                "allow_roots": [str(r) for r in roots]}
    n = sum(f.stat().st_size for f in rp.rglob("*") if f.is_file())
    last = ""
    for i in range(max(1, int(attempts))):
        try:
            shutil.rmtree(rp)
        except OSError as e:                     # 句柄还没释放 ⇒ 等一下再试
            last = f"{type(e).__name__}: {e}"
        if not rp.exists():
            return {"removed": True, "verified_gone": True, "attempts": i + 1, "path": str(rp),
                    "why": why, "bytes": n, "gb": round(n / 1024 ** 3, 2)}
        time.sleep(sleep_s)
    return {"removed": False, "verified_gone": not rp.exists(), "attempts": int(attempts),
            "path": str(rp), "why": why, "bytes": n, "last_error": last,
            "reason": "⛔ 删不掉（文件仍被占用）—— 半成品留在盘上"}


def exit_for_fallback(rm: dict | None = None, fell_back: bool = True,
                      fell_back_ok: bool = True) -> int:
    """收尸/回退的**退出码判据**（纯函数，判据 ⑥ 用它）。

    ⛔ 删不掉 ⇒ **不回退**且 exit 5；回退了但退出码非 0 ⇒ 也 exit 5。
    ⛔ 绝不把这两件事印成"已回退 OK"（实测踩过：不看退出码 ⇒ "回退失败"被印成"回退"）。
    """
    if rm is not None and rm.get("removed") and not rm.get("verified_gone"):
        return 5
    if rm is not None and not rm.get("removed"):
        return 5
    if not fell_back:
        return 5
    if not fell_back_ok:
        return 5
    return 0


def final_exit_code(rm: dict | None = None, fell_back: bool = True, fell_back_ok: bool = True,
                    failures=(), contaminated: bool = False) -> int:
    """整个 `pair` 的**最终退出码**（纯函数 ⇒ 判据 ⑧ 可以逐条钉住优先级）。

    优先级：**收尸/回退失败（5）> 相失败（7）> 污染（6）> 0**。
    ⛔ 理由：5 = "盘上留了东西、且没兜底"，7 = "有一相真的没跑成"，6 = "跑成了但读数脏了"。
    把 7 排在 6 前面是**必须**的 —— 否则"主相一秒就死"会被印成"跑过了只是脏了"（实测踩过）。
    """
    code = exit_for_fallback(rm=rm, fell_back=fell_back, fell_back_ok=fell_back_ok)
    if code == 0 and failures:
        return 7
    if code == 0 and contaminated:
        return 6
    return code


def phase_failures(res: dict, ignore=()) -> list[list]:
    """哪些相**非 0 退出**（⛔ 这是一条独立的判据，不许被 0/6 吃掉）。

    红证（2026-10-12 本工具自己的第一次真实 `pair`）：compact 相因为 `PermissionError [WinError 5]`
    **1 秒就退出码 1 死掉**，而编排器照旧返回 **exit=6（"有污染"）** —— 看起来像"跑过了、只是脏了"，
    实际上**主相根本没产出**。所以：某一相非 0 ⇒ 必须是一个**看得见的失败**。
    ⚠ `ignore` 用来排除"被内存闸门**故意**杀掉"的那一相（那是预期行为，由回退去兜）。
    """
    ign = set(ignore)
    out = []
    for n in ("primary", "other", "primary_serial_fallback"):
        ph = res.get(n)
        if isinstance(ph, dict) and ph.get("code") not in (0, None) and n not in ign:
            out.append([n, ph.get("code")])
    return out


# ================================================================ 命令行探针（⛔ fail-closed）
_PROBE: dict = {"ran": False, "ok": False, "fail_pids": [], "how": []}


def probe_reset() -> None:
    """清空探针状态（自检用；⛔ 生产路径只在进程启动时天然为空）。"""
    _PROBE.update({"ran": False, "ok": False, "fail_pids": [], "how": []})


def cmdline_of(pid: int, impl=None) -> str:
    """取一个进程的**命令行**。返回空串 = 取不到（调用方按 fail-closed 处理）。

    修复次序（**最先成功的那个赢**，并把用了哪条记进 `_PROBE["how"]`）：
      ① `Win32_Process.CommandLine`（CIM；**每个 pid 最多一次**）；
      ② `wmic process … get commandline`；
      ③ 老的 `tasklist /V` 解析（本机 Win11 26200 **无效** —— 那条 CSV 根本没有命令行列，留着只为别的机器）。
    ⛔ 三条全失败 ⇒ 空串 + 记账（`fail_pids`），**由调用方报 FAIL / 拒跑**。
    """
    _PROBE["ran"] = True
    if impl is not None:                          # 自检注入的桩（如"空实现"）
        s = str(impl(pid) or "").strip()
        if s:
            _PROBE["ok"] = True
            _PROBE["how"].append("stub")
        else:
            _PROBE["fail_pids"].append(int(pid))
        return s
    # ① CIM / Win32_Process
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}')"
                            f".CommandLine"],
                           capture_output=True, text=True, timeout=30)
        s = (r.stdout or "").strip()
        if s:
            _PROBE["ok"] = True
            _PROBE["how"].append("Win32_Process.CommandLine")
            return s
    except Exception:                                            # noqa: BLE001
        pass
    # ② wmic（若还在）
    try:
        r = subprocess.run(["wmic", "process", "where", f"processid={int(pid)}", "get", "commandline"],
                           capture_output=True, text=True, timeout=30)
        for line in (r.stdout or "").splitlines():
            s = line.strip()
            if s and s.lower() != "commandline":
                _PROBE["ok"] = True
                _PROBE["how"].append("wmic")
                return s
    except Exception:                                            # noqa: BLE001
        pass
    # ③ 老解析（本机无效；别的机器可能有效）
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH", "/V"],
                           capture_output=True, text=True, timeout=20)
        for line in (r.stdout or "").splitlines():
            if line.startswith('"'):
                cols = [c.strip('"') for c in line.split('","')]
                if len(cols) > 10:                    # 有命令行列的 tasklist 才有 >10 字段
                    _PROBE["ok"] = True
                    _PROBE["how"].append("tasklist /V")
                    return line
    except Exception:                                            # noqa: BLE001
        pass
    _PROBE["fail_pids"].append(int(pid))
    return ""


def probe_state(alive_check=None) -> dict:
    """探针状态：**已调用 / 取到过 / 取不到但进程还活着**。

    ⛔ `fail_closed=True` ⇒ **拒跑**（exit 4）：我们**证明不了**机器是安静的，
    那就绝不能说它是"干净的"。⚠ 已退掉的 pid 不算（那是竞态，算进去会永久误拒）。
    """
    chk = alive_check or proc_alive
    stuck = sorted({int(p) for p in _PROBE["fail_pids"] if chk(int(p))})
    return {"ran": bool(_PROBE["ran"]), "ok": bool(_PROBE["ok"]), "how": sorted(set(_PROBE["how"])),
            "fail_pids": list(_PROBE["fail_pids"]), "stuck_pids": stuck,
            "fail_closed": bool(stuck)}


def _mem_mb(s: str) -> float:
    digits = "".join(ch for ch in str(s) if ch.isdigit())
    return round(int(digits) / 1024, 1) if digits else 0.0


def tasklist_all_rows(impl=None) -> list[dict]:
    """**不带 `/FI`** 地枚举一遍进程（过滤在 Python 里做 —— 见下面 `enumeration_selfcheck` 的红证）。

    为什么不用 `tasklist /FI "IMAGENAME eq java.exe"`：那一步的过滤是在**被枚举进程的上下文里**
    做的，一旦该上下文受限，`/FI` 会**静默返回"没有匹配的任务"**（实测见 `enumeration_selfcheck`）。
    枚举一遍再自己过滤 ⇒ 至少过滤这一步不再有第二种真相。
    """
    if impl is not None:
        return list(impl())
    out: list[dict] = []
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    for line in (r.stdout or "").splitlines():
        if not line.startswith('"'):
            continue
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2 or not parts[1].isdigit():
            continue
        out.append({"pid": int(parts[1]), "image": parts[0],
                    "session": parts[2] if len(parts) > 2 else "",
                    "session_no": parts[3] if len(parts) > 3 else "",
                    "mem": parts[4] if len(parts) > 4 else "",
                    "mem_mb": _mem_mb(parts[4] if len(parts) > 4 else "")})
    return out


#: 任何**正常**会话都看得见的系统进程（看不见 ⇒ 这个上下文被限域了，枚举不可信）
SYSTEM_IMAGES = ("smss.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe")


def netstat_listening_pids(impl=None) -> list[int]:
    """`netstat -ano` 里所有 **LISTENING** 行的属主 pid。

    ⛔ 为什么信它：TCP 端点表**不按令牌限域**（实测：一个只能看见 18 个进程的受限上下文，
    `netstat` 照样列出全部监听端点及其 pid）⇒ 它是唯一一条**独立于进程枚举**的通道。
    """
    if impl is not None:
        return sorted({int(x) for x in impl()})
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    out = set()
    for line in (r.stdout or "").splitlines():
        t = line.split()
        if len(t) >= 4 and t[-2].upper() == "LISTENING" and t[-1].isdigit():
            out.add(int(t[-1]))
    return sorted(out)


def enumeration_selfcheck(all_rows: list[dict], own_pid=None, netstat_impl=None) -> dict:
    """**枚举本身也得 fail-closed**（这是本轮新抓到的第三种假绿，比命令行缺失更隐蔽）。

    红证（2026-10-12 真机，同一台机器同一条 java 在跑）：
      · `C:\\…\\AppData\\…\\Python314\\python.exe`（工作区外）⇒ 枚举到 **336** 个进程，看得见 java；
      · `python\\.venv\\Scripts\\python.exe`（uv 的 3.12，工作区内）⇒ 只枚举到 **18** 个，
        `tasklist /FI "IMAGENAME eq java.exe"` 回 **"No tasks are running which match the criteria"**，
        `[System.Diagnostics.Process]::GetProcessesByName('java')` = **0**，
        `Get-CimInstance Win32_Process | Measure` = **18**，`Get-Counter '\\Process(*)\\ID Process'` = **19**。
      ⇒ **所有**"数进程"的通道一起瞎（令牌限域），而 `netstat -ano` 仍能报出监听端点的 pid。
      ⇒ 若编排器在这种上下文里跑，G7 会报"算竞争 0 个"、**exit=0**：真机 134 MB 的
        `java -jar mahjong-server.jar` 就在那儿，**还是假绿**。
    所以：三条都必须成立，缺一条就**拒绝起重叠**（⛔ 无豁免开关）。
    """
    own = int(own_pid if own_pid is not None else os.getpid())
    pids = {int(r["pid"]) for r in all_rows}
    images = {str(r.get("image", "")).lower() for r in all_rows}
    seen = [i for i in SYSTEM_IMAGES if i in images]
    ns = netstat_listening_pids(netstat_impl)
    hidden = [p for p in ns if p not in pids]
    checks = [
        {"id": "self", "ok": own in pids,
         "what": "枚举必须看得见**本探针自己**的 pid",
         "detail": f"own_pid={own} ∈ 枚举（{len(pids)} 个进程）= {own in pids}"},
        {"id": "system", "ok": len(seen) >= 3,
         "what": f"枚举必须看得见**任何正常会话都看得见**的系统进程（{list(SYSTEM_IMAGES)} 里至少 3 个）",
         "detail": f"看见 {seen} —— 只有 {len(seen)}/5 ⇒ {'正常' if len(seen) >= 3 else '⛔ 这个上下文被限域了'}"
                   f"（枚举到 {len(all_rows)} 个进程）"},
        {"id": "netstat", "ok": not hidden,
         "what": "`netstat -ano` 里 LISTENING 的属主 pid 必须都在枚举里（TCP 表不受令牌限域 ⇒ 独立通道）",
         "detail": f"netstat 报 {len(ns)} 个 LISTENING pid；枚举里**看不到**的 = {hidden[:8]}"},
    ]
    return {"ok": all(c["ok"] for c in checks), "checks": checks, "n_rows": len(all_rows),
            "n_netstat": len(ns), "hidden_pids": hidden, "system_seen": seen}


def pick_images(rows: list[dict], images=("trainer.exe", "python.exe", "python3.exe", "java.exe")) -> list[dict]:
    """在自己枚举到的全表里挑出关心的镜像（⛔ 不交给 `tasklist /FI` —— 那个过滤会跟着上下文一起瞎）。"""
    want = {i.lower() for i in images}
    return [r for r in rows if str(r.get("image", "")).lower() in want]


def is_competing(rows: list[dict], python_mb: float = 200.0) -> list[dict]:
    """哪些算**竞争负载**（要拒绝起重叠）。

    · `trainer.exe` —— 一律算：这台机器上跑它只有一个理由（自对弈/前向/训练），抢的正是 CPU 与同一块盘的 IO。
    · `python`/`java` —— 只算**重的**（缺省 ≥200 MB 工作集）。⚠ `tasklist` 会抓到刚起来又退掉的
      小启动器 ⇒ 全算会**永久误拒**，而误拒会让人把闸门关掉。
    · **`java -jar mahjong-server.jar`** —— 一律算，**不看内存**（打牌/压测；BL-05）。
    """
    busy = []
    for r in rows:
        img = str(r.get("image", "")).lower()
        if img == "trainer.exe":
            busy.append(r)
        elif img.startswith(("python", "java")) and float(r.get("mem_mb") or 0.0) >= python_mb:
            busy.append(r)
        elif r.get("server_jar"):
            busy.append(r)
    return busy


def g7_scan(skip_pids=None, *, rows_impl=None, cmdline_impl=None, alive_check=None,
            netstat_impl=None, python_mb: float = 200.0) -> dict:
    """G7 静窗：**一次**扫描 + **枚举自检** + 命令行探针 + fail-closed 结论。

    自检靠 `rows_impl` / `cmdline_impl` / `alive_check` / `netstat_impl` 四个注入口做**单变量**负向
    （例如：造一行 java + 命令行桩返回空 ⇒ 必须 FAIL；或：枚举里没有系统进程 ⇒ 必须 FAIL，
    ⛔ 都不许当"干净"）。
    """
    skip = {int(p) for p in (skip_pids or set())}
    all_rows = list((rows_impl or tasklist_all_rows)())      # ⛔ 不过滤：过滤在我们自己手里
    selfcheck = enumeration_selfcheck(all_rows, netstat_impl=netstat_impl)
    rows = [r for r in pick_images(all_rows) if int(r.get("pid", 0)) not in skip]
    for r in rows:
        r.setdefault("server_jar", False)
        if str(r.get("image", "")).lower().startswith("java"):
            cl = cmdline_of(int(r["pid"]), impl=cmdline_impl)
            r["cmdline"] = cl
            r["server_jar"] = "mahjong-server.jar" in cl
            r["cmdline_ok"] = bool(cl)
    probe = probe_state(alive_check=alive_check)
    busy = is_competing(rows, python_mb)
    # ⛔ 枚举瞎了 ⇒ **也**是 fail-closed（否则会报"算竞争 0 个"= 第三种假绿）
    fail_closed = bool(probe["fail_closed"]) or (not selfcheck["ok"])
    return {"rows": rows, "all_rows": all_rows, "busy": busy, "probe": probe,
            "selfcheck": selfcheck, "fail_closed": fail_closed}


# ================================================================ G5/G4 辅助
def net_paths_in_cmd(cmd: list[str]) -> list[str]:
    """从命令行里抠出所有 `net:<路径>`（`@α` / `#T` 后缀不算路径的一部分）。"""
    out = []
    for tok in cmd:
        for part in str(tok).split(","):
            if part.startswith("net:"):
                p = part[4:]
                for cut in ("@", "#"):
                    p = p.split(cut)[0]
                out.append(p)
    return out


def binary_paths_in_cmd(cmd: list[str]) -> list[str]:
    """命令行里的可执行文件（第 0 个 token + 任何以 .exe 结尾的 token）—— **必须 pin**。"""
    out = []
    if cmd and str(cmd[0]).lower().endswith(".exe"):
        out.append(str(cmd[0]))
    out += [str(t) for t in cmd[1:] if str(t).lower().endswith(".exe")]
    return list(dict.fromkeys(out))


def cmd_references_dir(cmd: list[str], target: Path) -> str | None:
    """命令行里**是否引用了** `target`（= 正在被写的那个产物目录）。

    ⚠ 判据只认**解析后的绝对路径**（相等或在它下面），⛔ **不按目录名匹配**：
    两臂常常共用一个 tag（如 `<compact-root>\\b1s-off` 与 `<raw-root>\\b1s-off`），按 `name` 一比
    立刻假阳性，而闸门误拦会让人直接把闸门关掉。宁可少报也不许误拦。
    """
    t = target.resolve()
    flat = [str(x) for x in cmd]
    for s in (str(t), str(t).replace("\\", "/")):
        if any(s in x for x in flat):
            return f"命令行里出现了 {s}"
    for tok in flat:
        for piece in (tok, *tok.split("=")):
            piece = piece.strip().strip('"')
            if not piece or piece.startswith("-"):
                continue
            try:
                cand = Path(piece).resolve()
            except (OSError, ValueError):
                continue
            if cand == t or t in cand.parents:
                return f"参数 {piece!r} 解析后落在 {t}"
    return None


def kill_tree(pid: int) -> dict:
    """**连子树一起收**（`taskkill /T /F`；⛔ 不碰 CIM/WMI）。

    ⚠ 只 `Popen.kill()` 不够：`dataset build` 会起 `_parallel-train` 工作进程，它们握着
    `<compact>\\_parallel-train\\logs\\job*.log` 的句柄 ⇒ 父进程死了、半成品目录删不掉
    ⇒ 内存闸门的"回退串行"永远失败（`PermissionError [WinError 32]`）。
    """
    try:
        r = subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                           capture_output=True, text=True, timeout=30)
        return {"pid": int(pid), "rc": r.returncode, "out": (r.stdout or r.stderr or "").strip()[:300]}
    except (OSError, subprocess.SubprocessError) as e:
        return {"pid": int(pid), "rc": None, "err": f"{type(e).__name__}: {e}"}


# ================================================================ 驱动规格
def _subst(x, ctx: dict):
    """把 spec 里的 `${DATA_ROOT}` 之类占位符换掉 ⇒ **同一份 spec 换根不用改一个字节**。"""
    if isinstance(x, str):
        for k, v in ctx.items():
            x = x.replace("${" + k + "}", str(v))
        return x
    if isinstance(x, list):
        return [_subst(i, ctx) for i in x]
    if isinstance(x, dict):
        return {k: _subst(v, ctx) for k, v in x.items()}
    return x


def load_spec(path: Path, ctx: dict, overrides: dict) -> dict:
    """读驱动规格 + 应用 CLI 覆盖（根/workers/timeout/chunk）。

    ⚠ 兼容老形状：主相可以写在 `primary` / `compact` / `readout` 任一键下（`readout` 在 b2 里
    表示"有界读数"）。canonical 形状是 `primary`。
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    spec = _subst(raw, ctx)
    prim = None
    for key in ("primary", "compact", "readout"):
        if isinstance(spec.get(key), dict):
            prim = dict(spec[key])
            prim["_key"] = key
            break
    if prim is None:
        raise ValueError("⛔ spec 里没有主相（`primary` / `compact` / `readout` 三个键都没有）")
    # 主相 phase 的取法（⛔ 取不到就是取不到 —— 由 G1 拒，别猜）：
    #   · 显式 `phase`（顶层或主相块里）优先；
    #   · 键就是 `compact` / `readout` 且没写 phase ⇒ 按键名定（老 spec 的形状）；
    #   · 键是 `primary` 却没写 phase ⇒ **空** ⇒ G1 拒（fail closed：新形状必须自己声明）。
    if prim.get("phase") or spec.get("phase"):
        phase_raw = str(prim.get("phase") or spec.get("phase")).lower()
    elif prim["_key"] == "compact":
        phase_raw = "compact"
    elif prim["_key"] == "readout":
        phase_raw = "readings"
    else:
        phase_raw = ""
    prim["phase"] = PRIMARY_ALIASES.get(phase_raw, phase_raw)
    prim["phase_raw"] = phase_raw
    other = dict(spec.get("other") or {})
    other["kind"] = str(other.get("kind", "")).lower()
    gate = {"min_commit_free_gb": 8.0, "poll_sec": 1.0, "fallback": "serial"}
    gate.update(spec.get("gate") or {})
    # CLI 覆盖（⛔ 缺省值全部落在 `--data-root/--compact-root/--raw-root/--workers/--timeout` 上）
    if overrides.get("workers") is not None:
        prim["workers"] = overrides["workers"]
        other["workers"] = overrides["workers"]
    if overrides.get("timeout") is not None:
        gate["timeout_s"] = overrides["timeout"]
    if overrides.get("min_commit_free_gb") is not None:
        gate["min_commit_free_gb"] = overrides["min_commit_free_gb"]
    if overrides.get("no_fallback"):
        gate["fallback"] = "abort"
    if overrides.get("allow_rm_roots"):
        spec["allow_rm_roots"] = list(overrides["allow_rm_roots"])
    return {"tag": str(spec.get("tag") or "ov"), "phase": prim["phase"], "primary": prim,
            "other": other, "gate": gate, "raw": spec, "evidence_dir": spec.get("evidence_dir")}


def primary_cmd(spec: dict) -> list:
    """主相的命令行。

    `compact` 相**可以只给 `src`/`out`/`student`**（b1 的形状）⇒ 这里按**唯一那条**已知调用形状
    拼出来：`python -m mahjong_ml.v4.dataset <src> <out> --aux --student <student> --workers N`。
    ⛔ `readings` / `train` **必须显式给 `cmd`** —— 这两条的调用形状各有十几个开关、且随批次在变，
    在这里"猜"一份默认命令只会造出第二套口径（改了那边这里不报错）。
    """
    prim = spec["primary"]
    if prim.get("cmd"):
        return [str(x) for x in prim["cmd"]]
    if prim.get("phase") == "compact":
        if not (prim.get("src") and prim.get("out") and prim.get("student")):
            return []
        return [str(prim.get("py") or sys.executable), "-m", "mahjong_ml.v4.dataset",
                str(prim["src"]), str(prim["out"]), "--aux",
                "--student", str(prim["student"]), "--workers", str(prim.get("workers", 4))]
    return []


def guard_pair(spec: dict) -> dict:
    """G1–G5（G6 锁 / G7 静窗有副作用，在 Runner 里）。返回 {'ok','checks','refusals','pin_paths'}。"""
    checks: list[dict] = []
    prim, other = spec["primary"], spec["other"]

    def add(cid, ok, what, detail):
        checks.append({"id": cid, "ok": bool(ok), "what": what, "detail": detail})

    # ---- G1：主相 phase 白名单 + 从相 kind 白名单（⛔ 未知一律拒 = fail closed）
    phase = str(prim.get("phase", "")).lower()
    kind = str(other.get("kind", "")).lower()
    phase_ok = phase in ALLOWED_PRIMARY_PHASES
    kind_ok = kind in ALLOWED_OTHER_KINDS
    if phase_ok and kind_ok:
        add("G1", True, f"主相 phase={phase!r} ∈ {list(ALLOWED_PRIMARY_PHASES)} 且 "
                        f"从相 kind={kind!r} ∈ {list(ALLOWED_OTHER_KINDS)}",
            f"允许『{phase} ‖ {kind}』")
    else:
        why = []
        if not phase_ok:
            why.append(f"主相 phase={phase!r} 不在白名单 {list(ALLOWED_PRIMARY_PHASES)}"
                       f"（未知 phase 一律拒）")
        if not kind_ok:
            why.append(REFUSE_WHY.get(kind, f"未知 kind={kind!r} ⇒ fail closed（不许静默允许）"))
        add("G1", False, f"不允许『{prim.get('phase_raw') or phase} ‖ {kind}』", "；".join(why))

    # ---- G2：写集不相交（且互不为祖先）
    c_out = Path(str(prim.get("out") or "")).resolve()
    o_out = Path(str(other.get("out_dir") or "")).resolve()
    disj = bool(str(prim.get("out")) and str(other.get("out_dir"))) and \
        c_out != o_out and c_out not in o_out.parents and o_out not in c_out.parents
    add("G2", disj, "主相写目录 vs 从相写目录：不相交、互不为祖先",
        f"{c_out}  ⊥  {o_out}" if disj else f"⛔ 写集冲突/缺写目录：{c_out} vs {o_out}")

    # ---- G3：主相输入是冻结的、且与从相写目录分开
    c_src = Path(str(prim.get("src") or "")).resolve()
    if prim.get("src"):
        frozen = c_src.is_dir() and c_src != o_out and o_out not in c_src.parents and c_src not in o_out.parents
        add("G3", frozen, "主相输入分片存在且与从相写目录分开",
            f"src={c_src}（{len(manifest(c_src))} 个文件）" if frozen else f"⛔ src 不存在或与 {o_out} 重叠")
    else:
        # `train` 相的"输入"是紧凑集；spec 没给 `src` 时以主相命令里出现的路径判据不成立 ⇒ 拒
        add("G3", False, "主相没有声明冻结输入（`src`）",
            "⛔ 判不出『输入有没有被改过』 ⇒ 不许静默放过（fail closed）")

    # ---- G4：策略权重 + 可执行文件**已落盘**（跑前/跑后各 pin 一次 sha256）
    pins = list(dict.fromkeys(net_paths_in_cmd(list(other.get("cmd") or []))
                              + binary_paths_in_cmd(list(other.get("cmd") or []))
                              + net_paths_in_cmd([str(prim.get("student") or "")])))
    missing = [p for p in pins if not Path(p).is_file()]
    pcmd = primary_cmd(spec)
    add("G4", bool(pins) and not missing and bool(pcmd),
        "采集的策略权重 / 可执行文件都已落盘（可 pin sha256），且主相命令行拼得出来",
        f"pin {len(pins)} 个：{pins} · 主相 {len(pcmd)} 个 token"
        if (pins and not missing and pcmd) else
        (f"⛔ 找不到：{missing}" if missing
         else (f"⛔ 主相命令行拼不出来（phase={prim.get('phase')!r} 既没给 `cmd`，"
               f"也不是能自动拼的 compact 形状：需要 src/out/student）" if not pcmd
               else "⛔ 命令行里没有任何可 pin 的权重/可执行文件")))

    # ---- G5：从相命令行**不许引用**正在被写的主相产物（按解析后绝对路径判）
    leak = cmd_references_dir(list(other.get("cmd") or []), c_out)
    add("G5", not leak, "采集命令行里没有引用正在被写的主相产物目录",
        "无引用" if not leak else f"⛔ {leak}")

    refusals = [c for c in checks if not c["ok"]]
    return {"ok": not refusals, "checks": checks, "refusals": refusals,
            "pin_paths": sorted(set(pins))}


def acquire_lock(tag: str, evidence: Path, force_unlock_pid=None) -> dict:
    """G6 —— 一次只做一个重叠对：独占锁（pid 活着就拒；stale 锁按 pid 回收）。"""
    evidence.mkdir(parents=True, exist_ok=True)
    lp = evidence / f"{TOOL_NAME}.lock"
    if lp.is_file():
        try:
            old = json.loads(lp.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
        pid = int(old.get("pid") or 0)
        if force_unlock_pid and pid == int(force_unlock_pid):
            lp.unlink(missing_ok=True)
        elif proc_alive(pid):
            return {"ok": False, "detail": f"⛔ 已有重叠对在跑（pid={pid} tag={old.get('tag')} "
                                           f"since={old.get('since')}）—— 一次只做一个重叠对"}
        else:
            lp.unlink(missing_ok=True)
    lp.write_text(json.dumps({"pid": os.getpid(), "tag": tag,
                              "since": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
    return {"ok": True, "detail": f"拿到独占锁 {lp}（pid={os.getpid()}）", "path": str(lp)}


# ================================================================ 编排
class Runner:
    def __init__(self, spec: dict, overlap: bool, args, ctx: dict) -> None:
        self.spec = spec
        self.overlap = overlap
        self.args = args
        self.ctx = ctx
        self.tag = spec["tag"]
        self.gate = spec["gate"]
        self.ev = Path(str(spec.get("evidence_dir") or (Path(ctx["DATA_ROOT"]) / "w-ladder" / "_overlap" / "tool")))
        self.ev.mkdir(parents=True, exist_ok=True)
        self.only = (getattr(args, "only", None) or "").strip().lower() or None
        self.allow_busy = bool(getattr(args, "allow_busy", False))
        self.wait_quiet_s = float(getattr(args, "wait_quiet", 0) or 0)
        self.allow_rm_roots = tuple(spec.get("allow_rm_roots") or DEFAULT_ALLOW_RM_ROOTS
                                    or (ctx["COMPACT_ROOT"], ctx["RAW_ROOT"], ctx["DATA_ROOT"]))
        self.samples: list[dict] = []
        self.events: list[dict] = []
        #: 每一相**真的**退出的时刻（由采样器在 1 s 栅格上记）—— ⛔ 不用"我去 wait 它的时刻"：
        #  重叠路径先等 other 再等 primary，若 primary **早就死了**，wait 时刻会被拖到 other 的末尾，
        #  `own_wall_s` 就被虚大、`capture_rate` 跟着虚高 = 关于重叠效率的假绿（实测踩过）。
        self.exited_at: dict[str, float] = {}
        self.gate_killed: set[str] = set()
        self.lock: dict = {}
        self.gate_trip = None
        self.commands_run = 0
        self._stop = threading.Event()
        self.ledger_p = self.ev / f"{self.tag}.{TOOL_NAME}.jsonl"
        self.ledger_fh = self.ledger_p.open("a", encoding="utf-8")

    # -- 记账 ---------------------------------------------------------------
    def rec(self, ev: str, **kw) -> dict:
        row = {"ev": ev, "t": time.time(), "ts": time.strftime("%H:%M:%S"), **kw}
        self.events.append(row)
        self.ledger_fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        self.ledger_fh.flush()
        return row

    def say(self, s: str) -> None:
        print(s, flush=True)

    # -- 内存闸门线程（1 s 栅格、零 CIM）-----------------------------------
    def _sampler(self, procs: dict) -> None:
        abort_file = self.ev / f"{self.tag}.abort"
        poll = float(self.gate.get("poll_sec", 1.0) or 1.0)
        while not self._stop.wait(poll):
            m = mem()
            snap = list(procs.items())              # ⚠ 快照：串行路径会**逐步往 dict 里填**子进程
            row = {"ev": "sample", "t": time.time(), "ts": time.strftime("%H:%M:%S"), **m,
                   "procs": {n: {**proc_mem(p.pid), "cpu_s": proc_cpu(p.pid)}
                             for n, p in snap if p.poll() is None}}
            # 每一相**真的**退出时刻：只在第一次看见"已退出"时记（⛔ 不是我去 wait 它的时刻）
            for n, p in snap:
                if p.poll() is not None and n not in self.exited_at:
                    self.exited_at[n] = time.time()
                    self.rec("phase_gone", name=n, pid=p.pid, code=p.returncode,
                             at_offset_s=round(self.exited_at[n] - self.t_start, 1))
            self.samples.append(row)
            self.ledger_fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.ledger_fh.flush()
            if row["commit_free_gb"] < float(self.gate["min_commit_free_gb"]):
                self.gate_trip = {"reason": "commit_free 低于阈值",
                                  "commit_free_gb": row["commit_free_gb"],
                                  "threshold": self.gate["min_commit_free_gb"], "ts": row["ts"]}
                return
            if abort_file.exists():
                self.gate_trip = {"reason": f"外部采样器写了 {abort_file.name}（同一闸门）",
                                  "commit_free_gb": row["commit_free_gb"],
                                  "threshold": self.gate["min_commit_free_gb"], "ts": row["ts"]}
                return

    # -- 跑一条命令 ---------------------------------------------------------
    def _spawn(self, name: str, cmd: list, cwd) -> subprocess.Popen:
        env = dict(os.environ)
        # ⚠ **不覆盖** `MAHJONG_PRODUCER` 之类语义开关（那是被跑的那条命令的身份，不是编排器的）；
        #   要加就写进 spec 的 `env`（⛔ 编排器不替它选生产者）。
        env.update({"PYTHONIOENCODING": "utf-8",
                    "MAHJONG_DATA_ROOT": str(self.ctx["DATA_ROOT"]),
                    "PYTHONPATH": os.pathsep.join(
                        [str(Path(self.ctx["REPO"]) / "python")]
                        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])),
                    "OMP_NUM_THREADS": str(self.ctx["WORKERS"]),
                    "MKL_NUM_THREADS": str(self.ctx["WORKERS"])})
        env.update({str(k): str(v) for k, v in (self.spec["raw"].get("env") or {}).items()})
        log = (self.ev / f"{self.tag}.{name}.log").open("a", encoding="utf-8", errors="replace")
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} $ {' '.join(str(x) for x in cmd)}\n")
        log.flush()
        t0 = time.time()
        self.commands_run += 1
        p = subprocess.Popen([str(x) for x in cmd], cwd=str(cwd) if cwd else None, env=env,
                             stdout=log, stderr=subprocess.STDOUT)
        self.rec("spawn", name=name, pid=p.pid, cmd=" ".join(str(x) for x in cmd), own_t0=t0)
        return p

    def _wait(self, name: str, p: subprocess.Popen, t0: float) -> dict:
        """等一条相结束；`--timeout <= 0` ⇒ **不限**（关）；否则到点 tree-kill 并如实记 `timeout`。"""
        tmo = self.gate.get("timeout_s")
        timed_out = False
        if limit_active(tmo):
            try:
                p.wait(timeout=float(tmo))
            except subprocess.TimeoutExpired:
                timed_out = True
                self.rec("timeout", name=name, pid=p.pid, timeout_s=float(tmo))
                self.rec("kill_tree", **kill_tree(p.pid))
                p.kill()
                try:
                    p.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    self.rec("kill_timeout", pid=p.pid)
        else:
            p.wait()
        self.exited_at.setdefault(name, time.time())
        # ⚠ `own_wall_s` 用**真的退出时刻**算（采样器 1 s 栅格，精度 ±poll_sec）
        dt = round(self.exited_at[name] - t0, 1)
        self.rec("exit", name=name, pid=p.pid, code=p.returncode, own_wall_s=dt, timed_out=timed_out)
        return {"name": name, "pid": p.pid, "code": p.returncode, "own_wall_s": dt,
                "timed_out": timed_out, **proc_mem(p.pid)}

    # -- 主流程 -------------------------------------------------------------
    def run(self) -> tuple[int, dict]:
        t_start = time.time()
        self.t_start = t_start                     # 采样器记"真的退出时刻"要用它做偏移
        result: dict = {"tool": TOOL_NAME, "version": TOOL_VERSION, "mode": "pair",
                        "tag": self.tag, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "override": self.ctx, "allowed_rm_roots": list(self.allow_rm_roots)}
        g = guard_pair(self.spec)
        self.rec("gates", ok=g["ok"], checks=g["checks"])
        self.say(f"=== 重叠编排器 · tag={self.tag} · phase={self.spec['phase']} ‖ "
                 f"{self.spec['other']['kind']} · overlap={'ON' if self.overlap else 'OFF'} · "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        for c in g["checks"]:
            self.say(f"  [{'OK  ' if c['ok'] else '拒绝'}] {c['id']} {c['what']}\n         {c['detail']}")
        result["gates"] = g
        if not g["ok"]:
            self.say(f"⛔ 闸门未过（{len(g['refusals'])} 条）⇒ **不打任何命令**。")
            self.rec("refused", refusals=g["refusals"])
            result.update({"exit_code": 3, "commands_run": 0,
                           "note": "闸门未过 ⇒ 一条命令都没打"})
            return 3, result

        # ---- G7 静窗（名单含 java -jar mahjong-server.jar；探针 fail-closed）
        deadline = time.time() + max(0.0, self.wait_quiet_s)
        wait_t0 = time.time()
        scan = g7_scan()
        while scan["busy"] and time.time() < deadline:
            time.sleep(15)
            scan = g7_scan()
        g7_rows, g7_busy, probe = scan["rows"], scan["busy"], scan["probe"]
        self.rec("quiet_before", foreign=g7_rows, competing=g7_busy, probe=probe,
                 selfcheck=scan["selfcheck"], waited_s=round(time.time() - wait_t0, 1))
        self.say(f"  [{'OK  ' if not g7_busy else '拒绝'}] G7 静窗（含 `java -jar mahjong-server.jar`）："
                 f"别人的 trainer/python/java = {len(g7_rows)} 个，其中**算竞争**的 = {len(g7_busy)} 个")
        for f in g7_rows[:8]:
            tag = " ← 竞争" if f in g7_busy else ""
            tag += "（server.jar）" if f.get("server_jar") else ""
            self.say(f"         pid={f['pid']} {f['image']} mem={f['mem_mb']}MB{tag}")
        # ⛔ 枚举自检：数进程的**所有**通道都可能一起瞎（令牌限域）⇒ 那样报"0 个竞争"是假绿
        sc = scan["selfcheck"]
        self.say(f"  [{'OK  ' if sc['ok'] else 'FAIL'}] G7 枚举自检：枚举到 {sc['n_rows']} 个进程 · "
                 f"系统进程看见 {len(sc['system_seen'])}/5 {sc['system_seen']} · "
                 f"netstat LISTENING {sc['n_netstat']} 个（枚举里看不到的 {sc['hidden_pids'][:5]}）")
        for c in sc["checks"]:
            if not c["ok"]:
                self.say(f"         ⛔ {c['id']}: {c['what']} —— {c['detail']}")
        # ⛔ fail-closed：进程还活着却取不到命令行 ⇒ 证明不了安静 ⇒ 拒跑（⛔ 无豁免开关）
        if probe["fail_closed"]:
            self.say(f"  [FAIL] G7 探针 **取不到命令行**（pid={probe['stuck_pids']} 仍活着）"
                     f"⇒ server.jar 规则**判不出来** ⇒ **拒跑**（⛔ 绝不把『没取到』当『干净』）。")
        self.say(f"        探针：已调用={probe['ran']} 取到过={probe['ok']} 途径={probe['how'] or '—'}"
                 f"{'（⚠ 一条途径都没成功）' if probe['ran'] and not probe['ok'] else ''}")
        result["probe"] = probe
        result["enumeration_selfcheck"] = sc
        if scan["fail_closed"]:
            why = ("G7 命令行探针取不到数据（fail-closed）" if probe["fail_closed"]
                   else "G7 枚举自检不过（令牌限域 ⇒ 数进程的通道全瞎，fail-closed）")
            if not probe["fail_closed"]:
                self.say("  [FAIL] ⛔ **枚举本身不可信** ⇒ 在这种上下文里 G7 会报『算竞争 0 个』"
                         "（= 假绿）。⇒ **拒跑**；⛔ 没有'忽略枚举失败'的开关。")
                self.say("        修法：换一个**不受限域**的解释器跑本工具"
                         "（实测：工作区外的 Python314 看得见 336 个进程，uv 的 3.12 只看得到 18 个）。")
            result.update({"exit_code": 4, "commands_run": 0, "refuse_reason": why,
                           "note": "探针/枚举失效 ⇒ 拒跑；⛔ 没有'忽略'这类失败的开关"})
            self.rec("refused", reason="cmdline_probe_or_enum_fail_closed",
                     probe=probe, selfcheck=sc)
            return 4, result
        if g7_busy and not self.allow_busy:
            self.say("⛔ **检测到竞争负载** ⇒ 拒绝起重叠（计时会被污染）。"
                     "要么等它跑完（`--wait-quiet <秒>`），要么显式 `--allow-busy`"
                     "（那时读数只作结构验证、**不许当收益数字**）。")
            self.rec("refused", reason="foreign_load", foreign=g7_busy)
            result.update({"exit_code": 4, "commands_run": 0,
                           "refuse_reason": "G7 有竞争负载",
                           "note": "有别人在跑 ⇒ 拒跑（`--allow-busy` 可作结构验证）"})
            return 4, result
        if g7_busy:
            self.say("  ⚠ `--allow-busy`：读数**已污染**，只可用于结构验证、⛔ 不得用作收益数字。")
            self.rec("contaminated", why="启动时存在别人的 trainer/python/java", foreign=g7_busy)

        self.lock = acquire_lock(self.tag, self.ev)
        self.rec("lock", **self.lock)
        self.say(f"  [{'OK  ' if self.lock['ok'] else '拒绝'}] G6 {self.lock['detail']}")
        if not self.lock["ok"]:
            result.update({"exit_code": 3, "commands_run": 0, "lock": self.lock,
                           "note": "G6 独占锁没拿到 ⇒ 一条命令都没打"})
            return 3, result

        pin_before = {p: sha256_file(Path(p)) for p in g["pin_paths"]}
        self.rec("pin_before", **pin_before)
        src_before = manifest(Path(str(self.spec["primary"].get("src") or ".")))
        self.rec("src_manifest_before", n=len(src_before))
        self.say(f"  before: {json.dumps(mem())}   进程口径：committed=PagefileUsage（私有提交），ws 另报")

        pcmd = primary_cmd(self.spec)
        ocmd = [str(x) for x in (self.spec["other"].get("cmd") or [])]
        pcwd = self.spec["primary"].get("cwd")
        res: dict = {}
        procs: dict = {}
        phase_start: dict[str, float] = {}
        phase_end: dict[str, float] = {}

        def wait_phase(name, p, t0):
            r = self._wait(name, p, t0)
            phase_end[name] = self.exited_at.get(name, time.time())
            return r

        try:
            if self.only:
                name = {"primary": "primary", "compact": "primary", "readings": "primary",
                        "train": "primary", "other": "other", "collect": "other"}.get(self.only, self.only)
                self.say(f"\n--- 单相模式：只跑 {name} ---")
                t0 = time.time()
                th = threading.Thread(target=self._sampler, args=(procs,), daemon=True)
                th.start()
                cmd, cwd = (pcmd, pcwd) if name == "primary" else (ocmd, None)
                p = self._spawn(name, cmd, cwd)
                procs[name] = p
                phase_start[name] = t0
                res[name] = wait_phase(name, p, t0)
                self._stop.set()
                th.join(timeout=5)
            elif not self.overlap:
                self.say("\n--- 串行（主相 → 从相；顺序是判据本身）---")
                th = threading.Thread(target=self._sampler, args=(procs,), daemon=True)
                th.start()
                t0 = time.time()
                pp = self._spawn("primary", pcmd, pcwd)
                procs["primary"] = pp
                phase_start["primary"] = t0
                res["primary"] = wait_phase("primary", pp, t0)
                t1 = time.time()
                po = self._spawn("other", ocmd, None)
                procs["other"] = po
                phase_start["other"] = t1
                res["other"] = wait_phase("other", po, t1)
                self._stop.set()
                th.join(timeout=5)
                if self.gate_trip:
                    self.rec("gate_seen_in_serial", **self.gate_trip)
                    self.say(f"  （串行里看到 commit_free 触及阈值 {self.gate_trip['commit_free_gb']} GB"
                             f" —— 串行**不动作**，仅记账）")
            else:
                self.say(f"\n--- 重叠：{self.spec['phase']}（primary）‖ {self.spec['other']['kind']}（other）---")
                self.gate_trip = None
                po = self._spawn("other", ocmd, None)
                t_o = time.time()
                phase_start["other"] = t_o
                pp = self._spawn("primary", pcmd, pcwd)
                t_p = time.time()
                phase_start["primary"] = t_p
                procs = {"other": po, "primary": pp}
                th = threading.Thread(target=self._sampler, args=(procs,), daemon=True)
                th.start()
                res["other"] = wait_phase("other", po, t_o)
                tripped = self.gate_trip
                if tripped and pp.poll() is None:
                    self.say(f"\n[内存闸门] {tripped['reason']}：commit_free={tripped['commit_free_gb']} GB "
                             f"< {tripped['threshold']} GB ⇒ 杀主相（派生相，可重建）")
                    self.rec("gate_trip", **tripped, action="kill primary")
                    self.gate_killed.add("primary")
                    self.rec("kill_tree", **kill_tree(pp.pid))
                    pp.kill()
                    try:
                        pp.wait(timeout=60)          # **等它真退**（不等就去删它的 mmap 文件 ⇒ 删不干净）
                    except subprocess.TimeoutExpired:
                        self.rec("kill_timeout", pid=pp.pid)
                    time.sleep(1.5)
                res["primary"] = wait_phase("primary", pp, t_p)
                self._stop.set()
                th.join(timeout=5)
                tripped = self.gate_trip
                if tripped and self.gate["fallback"] == "serial":
                    out = self.spec["primary"].get("out")
                    if out:
                        rm = rm_safe(Path(out), "内存闸门中止后的半成品主相产物", self.allow_rm_roots)
                        self.rec("rm_halfproduct", **rm)
                        self.say(f"  清理半成品：{rm.get('reason') or ('已删并复验消失 ' + str(rm.get('gb')) + ' GB')}")
                        result["reap"] = rm
                        if not rm.get("verified_gone"):
                            self.say("⛔ 半成品**删不掉**（文件仍被占用）⇒ **不做回退**"
                                     "（在半成品上重跑只会再次失败）；请人工清理后重跑。")
                            res["fell_back"] = False
                            res["fallback_error"] = rm.get("reason") or rm
                        else:
                            self.say("--- 回退：主相单独串行重跑 ---")
                            t2 = time.time()
                            pp2 = self._spawn("primary_serial_fallback", pcmd, pcwd)
                            res["primary_serial_fallback"] = wait_phase("primary_serial_fallback", pp2, t2)
                            res["fell_back"] = True
                            # ⚠ **回退成功与否看退出码**：不看的话"回退失败"会被印成"回退"（实测踩过）
                            res["fell_back_ok"] = res["primary_serial_fallback"]["code"] == 0
                            if not res["fell_back_ok"]:
                                self.say(f"⛔ **串行回退失败**（退出码 "
                                         f"{res['primary_serial_fallback']['code']}）—— 主相产物没建出来，"
                                         f"如实报失败，⛔ 不许当成'已回退'。")
        finally:
            for p in procs.values():
                if p.poll() is None:
                    self.rec("cleanup_kill", **kill_tree(p.pid))
                    p.kill()
            self._stop.set()
            try:
                if self.lock.get("path"):
                    Path(self.lock["path"]).unlink(missing_ok=True)
            except OSError:
                pass

        # ---- 相位账（own_wall / 重叠区间 / 捕获率）
        res["phase_start_offset_s"] = {k: round(v - t_start, 1) for k, v in phase_start.items()}
        res["phase_end_offset_s"] = {k: round(v - t_start, 1) for k, v in phase_end.items()}
        overlap_s = 0.0
        if {"primary", "other"} <= set(phase_start) and {"primary", "other"} <= set(phase_end):
            lo = max(phase_start.values())
            hi = min(phase_end.values())
            overlap_s = round(max(0.0, hi - lo), 1)
        res["overlap_interval_s"] = overlap_s
        owns = [res[n]["own_wall_s"] for n in ("primary", "other")
                if isinstance(res.get(n), dict) and "own_wall_s" in res[n]]
        # 捕获率 = 重叠区间 / **较短那一相**的 own_wall（= 被藏住的比例；两相完全重叠 ⇒ 100%）
        res["capture_rate"] = (round(overlap_s / min(owns), 4)
                              if (overlap_s > 0 and owns and min(owns) > 0) else 0.0)
        res["wall_s"] = round(time.time() - t_start, 1)
        res["peak_committed_mb"] = {n: (res[n].get("peak_committed_mb") or 0.0)
                                    for n in ("primary", "other") if isinstance(res.get(n), dict)}
        res["peak_ws_mb"] = {n: (res[n].get("peak_ws_mb") or 0.0)
                             for n in ("primary", "other") if isinstance(res.get(n), dict)}
        if self.samples:
            res["commit_free_min_gb"] = min(s["commit_free_gb"] for s in self.samples)
            res["commit_free_n_samples"] = len(self.samples)
            cs = [sum((s["procs"].get(n) or {}).get("committed_mb") or 0.0 for n in ("primary", "other"))
                  for s in self.samples]
            res["procs_committed_sum_peak_mb"] = round(max(cs), 1) if cs else None
            ws = [sum((s["procs"].get(n) or {}).get("ws_mb") or 0.0 for n in ("primary", "other"))
                  for s in self.samples]
            res["procs_ws_sum_peak_mb"] = round(max(ws), 1) if ws else None
            # "两条同时在"采样点数（真并发的**独立**证据，不靠墙钟）
            res["both_present_samples"] = sum(
                1 for s in self.samples if len(s["procs"]) >= 2)

        pin_after = {p: sha256_file(Path(p)) for p in g["pin_paths"]}
        changed = [p for p in pin_before if pin_before[p] != pin_after.get(p)]
        src_after = manifest(Path(str(self.spec["primary"].get("src") or ".")))
        self.rec("pin_after", **pin_after)
        self.rec("post_check", pin_changed=changed, src_manifest_changed=src_before != src_after)
        foreign2_scan = g7_scan(skip_pids={p.pid for p in procs.values()})
        busy2 = foreign2_scan["busy"]
        self.rec("quiet_after", foreign=foreign2_scan["rows"], competing=busy2,
                 probe=foreign2_scan["probe"])
        if busy2:
            self.rec("contaminated", why="跑期间出现了别人的 trainer/python/java", foreign=busy2)
        self.say(f"\n  pin 变化：{changed or '无'} · 冻结输入变化：{src_before != src_after}")
        self.say(f"  跑后静窗：别人的 trainer/python/java = {len(foreign2_scan['rows'])} 个"
                 f"（算竞争 {len(busy2)}）"
                 f"{'（⚠ 计时读数不可用作收益数字）' if busy2 else ''}")
        self.say(f"  after : {json.dumps(mem())}\n  wall={res['wall_s']}s · 重叠区间={overlap_s}s · "
                 f"捕获率={res['capture_rate']} · commit_free 最低={res.get('commit_free_min_gb')} GB"
                 f"（n={res.get('commit_free_n_samples', 0)}）")
        self.rec("wall", **res)

        contaminated = any(e["ev"] == "contaminated" for e in self.events)
        failures = phase_failures(res, ignore=self.gate_killed)
        code = final_exit_code(rm=result.get("reap"),
                               fell_back=bool(res.get("fell_back", True)),
                               fell_back_ok=bool(res.get("fell_back_ok", True)),
                               failures=failures, contaminated=contaminated)
        if failures:
            self.say(f"\n⛔ **有相非 0 退出**：{failures} ⇒ exit=7"
                     f"（⛔ 不许当成'跑过了'；主相/从相的产物都可能是半成品）")
        result.update({"exit_code": code, "phase_failures": failures, "phases": {
            "primary": res.get("primary"), "other": res.get("other"),
            "primary_serial_fallback": res.get("primary_serial_fallback")},
            "wall_s": res["wall_s"], "overlap_interval_s": overlap_s,
            "capture_rate": res["capture_rate"],
            "commit_free_min_gb": res.get("commit_free_min_gb"),
            "commit_free_n_samples": res.get("commit_free_n_samples"),
            "peak_committed_mb": res["peak_committed_mb"], "peak_ws_mb": res["peak_ws_mb"],
            "procs_committed_sum_peak_mb": res.get("procs_committed_sum_peak_mb"),
            "procs_ws_sum_peak_mb": res.get("procs_ws_sum_peak_mb"),
            "both_present_samples": res.get("both_present_samples"),
            "commands_run": self.commands_run, "pin_changed": changed,
            "src_manifest_changed": src_before != src_after,
            "contaminated": contaminated,
            "gate_killed": sorted(self.gate_killed),
            "timing_reliable": (not contaminated) and (not failures),
            "raw": res})
        for e in self.events:
            if e["ev"] == "contaminated":
                result.setdefault("contamination_notes", []).append(e.get("why"))
        self.ledger_fh.close()
        return code, result


# ================================================================ check 模式
def mode_check(spec: dict, args, ctx: dict) -> tuple[int, dict]:
    """**只跑闸门，不打任何命令**。G1–G5 + G6 锁（只读地看一眼）+ G7（含 fail-closed 探针）。"""
    result: dict = {"tool": TOOL_NAME, "version": TOOL_VERSION, "mode": "check",
                    "tag": spec["tag"], "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "override": ctx, "commands_run": 0}
    g = guard_pair(spec)
    print(f"=== 闸门检查（⛔ 不执行任何命令）· tag={spec['tag']} · "
          f"{time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    for c in g["checks"]:
        print(f"[{'OK  ' if c['ok'] else '拒绝'}] {c['id']} {c['what']}\n       {c['detail']}")
    result["gates"] = g
    scan = g7_scan()
    rows, busy, probe = scan["rows"], scan["busy"], scan["probe"]
    print(f"[{'OK  ' if not busy else '拒绝'}] G7 静窗（含 `java -jar mahjong-server.jar`）："
          f"算竞争 {len(busy)} 个")
    for f in rows[:8]:
        print(f"       pid={f['pid']} {f['image']} mem={f['mem_mb']}MB"
              f"{' ← 竞争' if f in busy else ''}"
              f"{'（server.jar）' if f.get('server_jar') else ''}")
    print(f"       探针：已调用={probe['ran']} 取到过={probe['ok']} 途径={probe['how'] or '—'} "
          f"取不到但还活着的 pid={probe['stuck_pids']}")
    sc = scan["selfcheck"]
    print(f"[{'OK  ' if sc['ok'] else 'FAIL'}] G7 枚举自检：枚举到 {sc['n_rows']} 个进程 · "
          f"系统进程看见 {len(sc['system_seen'])}/5 {sc['system_seen']} · "
          f"netstat LISTENING {sc['n_netstat']} 个（枚举里看不到的 {sc['hidden_pids'][:5]}）")
    for c in sc["checks"]:
        if not c["ok"]:
            print(f"       ⛔ {c['id']}: {c['what']} —— {c['detail']}")
    result["probe"] = probe
    result["enumeration_selfcheck"] = sc
    result["g7"] = {"rows": rows, "busy": busy}
    if scan["fail_closed"]:
        if probe["fail_closed"]:
            print(f"[FAIL] G7 探针取不到命令行（pid={probe['stuck_pids']} 仍活着）"
                  f"⇒ server.jar 规则判不出来 ⇒ **拒跑**（⛔ 绝不把『没取到』当『干净』）。")
            why = "G7 命令行探针取不到数据（fail-closed）"
        else:
            print("[FAIL] ⛔ **枚举本身不可信**（令牌限域 ⇒ 数进程的通道全瞎）⇒ 在这种上下文里 G7 会报"
                  "『算竞争 0 个』= 假绿 ⇒ **拒跑**；⛔ 没有'忽略枚举失败'的开关。")
            why = "G7 枚举自检不过（令牌限域，fail-closed）"
        result.update({"exit_code": 4, "refuse_reason": why})
        print("\n结论：⛔ 拒绝重叠（fail-closed）")
        return 4, result
    if not g["ok"]:
        result.update({"exit_code": 3, "refuse_reason": "G1–G5 未过"})
        print(f"\n结论：⛔ 拒绝重叠（{len(g['refusals'])} 条闸门未过）")
        return 3, result
    if busy:
        result.update({"exit_code": 4, "refuse_reason": "G7 有竞争负载"})
        print("\n结论：⛔ 拒绝重叠（G7 有竞争负载）")
        return 4, result
    result.update({"exit_code": 0})
    print("\n结论：✅ 允许重叠")
    return 0, result


def mode_pair(spec: dict, args, ctx: dict) -> tuple[int, dict]:
    return Runner(spec, bool(args.overlap), args, ctx).run()


# ================================================================ calibrate-mem
_MEM_CHILD = r'''
import ctypes, mmap, os, sys, time
kind, gb, ready, go = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
n = int(gb * 1024**3)
if kind == "anon":
    buf = ctypes.create_string_buffer(n)          # 匿名提交
    ctypes.memset(buf, 0x5A, n)                   # 真的**提交**每一页（否则只是保留）
else:
    p = os.path.join(os.environ.get("TEMP", "."), "ov-memcal-mmap.bin")
    with open(p, "wb") as fh:
        fh.truncate(n)
    fh = open(p, "r+b")
    mm = mmap.mmap(fh.fileno(), 0)
    for off in range(0, n, 4096):                 # 真的**触碰**每一页（否则不进 ws）
        mm[off] = 1
open(ready, "w").write("ok")
while not os.path.exists(go):
    time.sleep(0.2)
'''


def mode_calibrate_mem(args, ctx: dict) -> tuple[int, dict]:
    """内存口径标定：**已知负载 ⇒ 先验计数器**（⛔ 不先标定就报峰值 = 拿错口径编数字）。

    判据（期望来自 b3b 实测；容差是为了吸收机器噪声，⛔ 不是"看着差不多"）：
      · 匿名：`committed_mb` ≈ 名义值（±20%）**且** `ws_mb` 与之同步；
      · 文件 mmap：`ws_mb` ≈ 名义值（±20%）而 `committed_mb` **远小于**它（< 0.25×ws）
        ⇒ **这正是"只许用 committed 报峰值"的红证**。
    """
    ev = Path(str(args.evidence_dir or (Path(ctx["DATA_ROOT"]) / "w-ladder" / "_overlap" / "tool")))
    ev.mkdir(parents=True, exist_ok=True)
    anon_gb = float(args.anon_gb)
    file_gb = float(args.file_gb)
    out: dict = {"tool": TOOL_NAME, "mode": "calibrate-mem", "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "anon_gb": anon_gb, "file_gb": file_gb, "rows": [], "checks": []}
    print("=== 内存口径标定（已知负载 ⇒ 先验计数器；⛔ 不先标定就报峰值 = 拿错口径）===")
    idle = {"kind": "idle", "gb": 0.0, **mem(), **proc_mem(os.getpid())}
    out["rows"].append(idle)
    print(f"① idle   系统 {json.dumps({k: idle[k] for k in ('commit_free_gb', 'committed_gb', 'free_phys_gb')})}")
    print(f"         探针进程 {json.dumps({k: idle.get(k) for k in ('committed_mb', 'ws_mb')})}")

    script = ev / "_ov_memcal_child.py"
    script.write_text(_MEM_CHILD, encoding="utf-8")
    for kind, gb in (("anon", anon_gb), ("file", file_gb)):
        if gb <= 0:                              # ⛔ `<= 0` ⇒ **跳过**（不是"不限"）
            print(f"② {kind}: gb={gb} <= 0 ⇒ 跳过（⛔ 不是'不限'）")
            out["checks"].append({"id": f"mem-{kind}", "ok": True, "skipped": True,
                                  "detail": f"gb={gb} <= 0 ⇒ 跳过"})
            continue
        ready = ev / f"_ov_memcal_{kind}.ready"
        go = ev / f"_ov_memcal_{kind}.go"
        for f in (ready, go):
            f.unlink(missing_ok=True)
        child = subprocess.Popen([sys.executable, str(script), kind, str(gb), str(ready), str(go)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        t0 = time.time()
        while not ready.exists() and child.poll() is None and time.time() - t0 < 300:
            time.sleep(0.2)
        if not ready.exists():
            err = (child.stderr.read() or b"").decode("utf-8", "replace")[:400]
            child.kill()
            print(f"② {kind} {gb} GiB ⛔ 负载没就绪（rc={child.poll()}）：{err}")
            out["rows"].append({"kind": kind, "gb": gb, "error": err})
            out["checks"].append({"id": f"mem-{kind}", "ok": False, "detail": "负载没就绪"})
            continue
        time.sleep(1.5)                          # 让计数器落定
        r = {"kind": kind, "gb": gb, "pid": child.pid, **mem(), **proc_mem(child.pid),
             "settle_s": round(time.time() - t0, 1)}
        go.write_text("go")
        try:
            child.wait(timeout=120)
        except subprocess.TimeoutExpired:
            child.kill()
        time.sleep(2.0)
        r["after_exit"] = {"pid_alive": child.poll() is None, **mem()}
        for f in (ready, go):
            f.unlink(missing_ok=True)
        out["rows"].append(r)
        nominal = gb * 1024.0
        label = "匿名提交" if kind == "anon" else "文件 mmap"
        print(f"② {kind} {gb} GiB（{label}）pid={r['pid']} 用时 {r['settle_s']}s")
        print(f"   系统 {json.dumps({k: r[k] for k in ('commit_free_gb', 'committed_gb', 'free_phys_gb')})}")
        print(f"   进程 {json.dumps({k: r.get(k) for k in ('committed_mb', 'peak_committed_mb', 'ws_mb', 'peak_ws_mb')})}")
        print(f"   Δ vs idle：进程 committed {round(r.get('committed_mb', 0) - idle.get('committed_mb', 0), 1)} MB · "
              f"ws {round(r.get('ws_mb', 0) - idle.get('ws_mb', 0), 1)} MB")
        cm = float(r.get("committed_mb") or 0.0)
        wm = float(r.get("ws_mb") or 0.0)
        if kind == "anon":
            ok = abs(cm - nominal) <= 0.20 * nominal and abs(wm - cm) <= 0.20 * nominal
            detail = (f"committed={cm} MB（名义 {nominal:.0f}）· ws={wm} MB ⇒ "
                      f"匿名负载两者同步 ⇒ 口径可信" if ok else
                      f"⛔ committed={cm} MB 偏离名义 {nominal:.0f} MB 超 20%（或 ws 不同步）")
        else:
            ok = abs(wm - nominal) <= 0.20 * nominal and cm < 0.25 * wm
            detail = (f"ws={wm} MB（名义 {nominal:.0f}）而 committed={cm} MB ⇒ "
                      f"**文件 mmap 只涨 ws、committed 几乎不动** ⇒ ⛔ 只许用 committed 报峰值" if ok else
                      f"⛔ ws={wm} MB / committed={cm} MB 不符合'mmap 只涨 ws'（不可据此区分口径）")
        print(f"   [{'PASS' if ok else 'FAIL'}] {detail}")
        out["checks"].append({"id": f"mem-{kind}", "ok": bool(ok), "nominal_mb": nominal,
                              "committed_mb": cm, "ws_mb": wm, "detail": detail})
    out["ok"] = all(c["ok"] for c in out["checks"])
    print("\n判据：① 匿名负载 ⇒ `commit_free` 与进程 `committed` 同步涨 ~名义值；"
          "② 文件 mmap ⇒ **ws 涨、committed 几乎不动** ⇒ 峰值只认 committed、ws 另报。")
    print(f"\n{'全部通过' if out['ok'] else '⛔ 有不通过'}")
    return (0 if out["ok"] else 1), out


# ================================================================ 自检（含负向）
def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _stub_spec(tmp: Path, ctx: dict, *, phase: str, kind: str, marker: Path,
               src_ok: bool = True) -> Path:
    """造一份**只跑桩命令**的 spec（桩一旦被执行就写 marker ⇒ 用来证"一条命令都没打"）。"""
    stub = tmp / "stub_cmd.py"
    _write(stub, "import sys, pathlib\n"
                 f"pathlib.Path(r'{marker}').write_text('ran', encoding='utf-8')\n"
                 "sys.stdout.write('stub ran\\n')\n")
    src = tmp / "src_shard"
    if src_ok:
        src.mkdir(parents=True, exist_ok=True)
        (src / "a.jsonl").write_text("{}\n", encoding="utf-8")
    net = tmp / "net.bin"
    net.write_bytes(b"stub-weights")
    spec = {
        "tag": "selftest", "phase": phase,
        "primary": {"phase": phase, "src": str(src), "out": str(tmp / "out-primary"),
                    "student": f"net:{net}@0#0.5",
                    "cmd": [sys.executable, str(stub)]},
        "other": {"kind": kind, "out_dir": str(tmp / "out-other"),
                  "cmd": [sys.executable, str(stub), f"net:{net}@0#0.5"]},
        "gate": {"min_commit_free_gb": 8.0, "poll_sec": 1.0, "fallback": "serial"},
        "allow_rm_roots": [str(tmp)],
        "evidence_dir": str(tmp / "ev"),
    }
    p = tmp / f"spec-{phase}-{kind}.json"
    p.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return p


def mode_selftest(args, ctx: dict) -> tuple[int, dict]:
    """自检 —— **必须含负向**（正向自检只能证明"会通过"，证明不了"该拒时真的拒"）。

    六条：① 未知 kind/phase ⇒ 拒（fail closed）· ② 探针取不到数据 ⇒ FAIL · ③ 闸门不过 ⇒
    **一条命令都不打**（真起子进程，用桩的 marker 文件作证）· ④ 两文件都缺的比较 ⇒ **必红**
    （假绿守卫）· ⑤ `--chunk` / 预算 `<= 0` 的语义 · ⑥ 收尸后"半成品确实消失"（+ 删不掉 ⇒ exit 5）。
    """
    import tempfile
    cases: list[dict] = []
    out: dict = {"tool": TOOL_NAME, "mode": "selftest", "ts": time.strftime("%Y-%m-%d %H:%M:%S")}

    def case(cid, ok, what, detail):
        cases.append({"id": cid, "ok": bool(ok), "what": what, "detail": detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {cid} {what}\n         {detail}")

    with tempfile.TemporaryDirectory(prefix="ov-selftest-") as td:
        tmp = Path(td).resolve()
        marker = tmp / "STUB_RAN.marker"

        # ---- ① 未知 kind / 未知 phase ⇒ 拒（fail closed）
        print("=== ① 未知 kind / 未知 phase ⇒ **拒**（fail closed，⛔ 不许静默允许）===")
        bad_kinds = ["train", "critic", "audit", "eval", "compact", "readings",
                     "features", "check", "mystery-phase", ""]
        ok1 = True
        lines = []
        for k in bad_kinds:
            sp = _stub_spec(tmp / "k", ctx, phase="compact", kind=k, marker=marker)
            spec = load_spec(sp, ctx, {})
            g = guard_pair(spec)
            refused = not g["ok"]
            why = g["refusals"][0]["detail"] if g["refusals"] else "(无拒绝)"
            lines.append(f"kind={k or '(空)'!r:18s} ⇒ {'拒绝' if refused else '⛔ 允许'}")
            ok1 = ok1 and refused
        for k in ("mystery-phase", ""):
            sp = _stub_spec(tmp / "k", ctx, phase="compact", kind="collect", marker=marker)
            spec = load_spec(sp, ctx, {})
            spec["primary"]["phase"] = k
            g = guard_pair(spec)
            refused = not g["ok"]
            lines.append(f"phase={k or '(空)'!r:18s} ⇒ {'拒绝' if refused else '⛔ 允许'}")
            ok1 = ok1 and refused
        # 正向对照：白名单里的三相都必须**允许**
        for ph in ALLOWED_PRIMARY_PHASES:
            sp = _stub_spec(tmp / "k", ctx, phase=ph, kind="collect", marker=marker)
            spec = load_spec(sp, ctx, {})
            g = guard_pair(spec)
            lines.append(f"phase={ph!r:18s} ‖ collect ⇒ {'允许' if g['ok'] else '⛔ 拒绝'}")
            ok1 = ok1 and g["ok"]
        case("① G1 白名单 fail-closed", ok1, "未知 kind/phase 一律拒；三个白名单 phase ‖ collect 允许",
             " | ".join(lines))

        # ---- ② 探针取不到数据 ⇒ FAIL（注入空实现）
        print("\n=== ② 探针取不到命令行 ⇒ **FAIL / 拒跑**（⛔ 绝不把『没取到』当『干净』）===")
        # ⚠ 这一组刻意让**枚举自检通过**（自己的 pid + 3 个系统进程 + netstat 的 pid 都在表里）
        #   ⇒ 单变量：只有"命令行探针"这一个变量在动，拒跑的归因才干净。
        def _enum_ok(extra=()):
            base = [{"pid": os.getpid(), "image": "python.exe", "mem": "5,000 K", "mem_mb": 4.9},
                    {"pid": 4, "image": "System", "mem": "1,000 K", "mem_mb": 1.0},
                    {"pid": 100, "image": "smss.exe", "mem": "1,000 K", "mem_mb": 1.0},
                    {"pid": 200, "image": "csrss.exe", "mem": "1,000 K", "mem_mb": 1.0},
                    {"pid": 300, "image": "lsass.exe", "mem": "1,000 K", "mem_mb": 1.0}]
            return base + list(extra)

        fake_rows = _enum_ok([{"pid": 999999, "image": "java.exe", "session": "Console",
                               "mem": "134,000 K", "mem_mb": 130.9}])
        ns_ok = lambda: [4, 999999]                                # noqa: E731 —— 都在枚举里
        probe_reset()
        scan_bad = g7_scan(rows_impl=lambda: fake_rows, cmdline_impl=lambda pid: "",
                           alive_check=lambda pid: True, netstat_impl=ns_ok)  # 还活着却取不到 ⇒ fail closed
        st_bad = scan_bad["probe"]
        probe_reset()
        scan_race = g7_scan(rows_impl=lambda: fake_rows, cmdline_impl=lambda pid: "",
                            alive_check=lambda pid: False, netstat_impl=ns_ok)  # 已退掉 ⇒ 竞态，不算失效
        st_race = scan_race["probe"]
        probe_reset()
        scan_ok = g7_scan(rows_impl=lambda: fake_rows,
                          cmdline_impl=lambda pid: r'"java.exe" -jar mahjong-server.jar --port 10086',
                          alive_check=lambda pid: True, netstat_impl=ns_ok)
        st_ok = scan_ok["probe"]
        java_row_ok = next((r for r in scan_ok["rows"] if r["image"].lower().startswith("java")), {})
        ok2 = (st_bad["fail_closed"] is True and len(scan_bad["busy"]) == 0
               and st_race["fail_closed"] is False and st_ok["ok"] is True
               and scan_ok["selfcheck"]["ok"] is True
               and len(scan_ok["busy"]) == 1 and java_row_ok.get("server_jar") is True)
        case("② 探针 fail-closed", ok2,
             "**旧规则会算成 0 个竞争（正是那次假绿）**，空实现下必须 fail_closed ⇒ 拒跑；"
             "进程已退 ⇒ 不算（免永久误拒）；取到 server.jar ⇒ 算竞争",
             f"取不到/活着：算竞争={len(scan_bad['busy'])}（⛔ 旧的假绿读数）但 "
             f"fail_closed={st_bad['fail_closed']} stuck={st_bad['stuck_pids']} ⇒ **拒跑** · "
             f"取不到/已退 fail_closed={st_race['fail_closed']} · "
             f"取到 server_jar={java_row_ok.get('server_jar')} 算竞争={len(scan_ok['busy'])} · "
             f"（枚举自检同组保持 OK={scan_ok['selfcheck']['ok']} ⇒ 单变量）")

        # ---- ⑦ 枚举瞎了 ⇒ **也** fail-closed（本轮新抓到的第三种假绿）
        print("\n=== ⑦ 数进程的通道全瞎（令牌限域）⇒ **也** fail-closed"
              "（⛔ 否则会报『算竞争 0 个』= 假绿）===")
        blind_rows = [{"pid": os.getpid(), "image": "python.exe", "mem": "5,000 K", "mem_mb": 4.9},
                      {"pid": 424242, "image": "java.exe", "mem": "134,000 K", "mem_mb": 130.9}]
        probe_reset()
        sc_blind = g7_scan(rows_impl=lambda: blind_rows,
                           cmdline_impl=lambda pid: r'"java.exe" -jar mahjong-server.jar',
                           alive_check=lambda pid: True, netstat_impl=lambda: [424242])
        probe_reset()
        strict_rows = _enum_ok()
        sc_hidden = g7_scan(rows_impl=lambda: strict_rows,
                            cmdline_impl=lambda pid: "", alive_check=lambda pid: False,
                            netstat_impl=lambda: [4, 777777])       # 777777 枚举里看不到
        probe_reset()
        sc_good = g7_scan(rows_impl=lambda: _enum_ok(), cmdline_impl=lambda pid: "",
                          alive_check=lambda pid: False, netstat_impl=lambda: [4])
        ok7 = (sc_blind["fail_closed"] is True
               and sc_blind["selfcheck"]["ok"] is False
               and "system" in [c["id"] for c in sc_blind["selfcheck"]["checks"] if not c["ok"]]
               and sc_hidden["selfcheck"]["ok"] is False
               and [c["id"] for c in sc_hidden["selfcheck"]["checks"] if not c["ok"]] == ["netstat"]
               and sc_good["selfcheck"]["ok"] is True and sc_good["fail_closed"] is False)
        case("⑦ 枚举 fail-closed", ok7,
             "系统进程看不见 ⇒ 拒跑；netstat 报了个枚举里没有的 pid ⇒ 拒跑（独立通道抓到瞎枚举）；"
             "两者都正常 ⇒ 不拒",
             f"瞎枚举：selfcheck.ok={sc_blind['selfcheck']['ok']} 不过的项="
             f"{[c['id'] for c in sc_blind['selfcheck']['checks'] if not c['ok']]} "
             f"fail_closed={sc_blind['fail_closed']} · "
             f"netstat 揭露：不过的项={[c['id'] for c in sc_hidden['selfcheck']['checks'] if not c['ok']]} · "
             f"正常：selfcheck.ok={sc_good['selfcheck']['ok']} fail_closed={sc_good['fail_closed']}")

        # ---- ⑧ 某一相非 0 退出 ⇒ **看得见的失败**（exit 7 > 6），且 own_wall 用**真的退出时刻**
        print("\n=== ⑧ 相失败不得被'污染'吃掉（exit 7 > 6）；`own_wall` 不得被别的相的时长虚大 ===")
        pure = (final_exit_code(failures=[["primary", 1]], contaminated=True) == 7
                and final_exit_code(failures=[], contaminated=True) == 6
                and final_exit_code(failures=[], contaminated=False) == 0
                and final_exit_code(rm={"removed": False, "verified_gone": False},
                                    failures=[["primary", 1]], contaminated=True) == 5
                and final_exit_code(fell_back=False) == 5
                and phase_failures({"primary": {"code": 1}, "other": {"code": 0}}) == [["primary", 1]]
                and phase_failures({"primary": {"code": 1}, "other": {"code": 0}},
                                   ignore={"primary"}) == [])
        # 端到端：primary 立刻退 3；other 睡 3 s ⇒ primary 的 own_wall 必须 ≈0（⛔ 不是 ≈3）
        d2 = tmp / "ph"
        d2.mkdir(parents=True, exist_ok=True)
        prim_stub = d2 / "prim.py"
        prim_stub.write_text("import sys\n"
                             f"open(r'{d2 / 'prim.ran'}', 'w').write('1')\n"
                             "sys.exit(3)\n", encoding="utf-8")
        other_stub = d2 / "other.py"
        other_stub.write_text("import time\n"
                              f"open(r'{d2 / 'other.ran'}', 'w').write('1')\n"
                              "time.sleep(3.0)\n", encoding="utf-8")
        (d2 / "src").mkdir(exist_ok=True)
        (d2 / "src" / "a.jsonl").write_text("{}\n", encoding="utf-8")
        (d2 / "net.bin").write_bytes(b"w")
        sp2 = d2 / "spec.json"
        sp2.write_text(json.dumps({
            "tag": "selftest8", "phase": "train",
            "primary": {"phase": "train", "src": str(d2 / "src"), "out": str(d2 / "out-p"),
                        "cmd": [sys.executable, str(prim_stub)]},
            "other": {"kind": "collect", "out_dir": str(d2 / "out-o"),
                      "cmd": [sys.executable, str(other_stub), f"net:{d2 / 'net.bin'}@0"]},
            "gate": {"min_commit_free_gb": 0.0, "poll_sec": 0.5, "fallback": "abort"},
            "allow_rm_roots": [str(d2)], "evidence_dir": str(d2 / "ev"),
        }, ensure_ascii=False), encoding="utf-8")
        r8 = subprocess.run([sys.executable, str(Path(__file__).resolve()), "pair",
                             "--driver", str(sp2), "--overlap", "--allow-busy",
                             "--data-root", str(d2), "--compact-root", str(d2 / "c"),
                             "--raw-root", str(d2 / "r"),
                             "--out", str(d2 / "r8.json")],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        j8 = json.loads((d2 / "r8.json").read_text(encoding="utf-8")) if (d2 / "r8.json").is_file() else {}
        pw = ((j8.get("phases") or {}).get("primary") or {}).get("own_wall_s")
        ow = ((j8.get("phases") or {}).get("other") or {}).get("own_wall_s")
        ok8 = (pure and r8.returncode == 7 and j8.get("phase_failures") == [["primary", 3]]
               and pw is not None and pw < 2.0 and ow is not None and ow >= 2.5
               # primary 0.5 s 就死了 ⇒ 重叠区间只有 ~0.1 s。⛔ 旧实现这里会报 ~2.6 s（= other 的时长）
               and (j8.get("overlap_interval_s") or 99) < 1.5)
        case("⑧ 相失败 / own_wall", ok8,
             "`final_exit_code` 优先级 5>7>6>0；primary 退 3 且 other 睡 3 s ⇒ exit=7 且 "
             "primary 的 own_wall **≈0 s**、重叠区间 **≈0 s**（⛔ 不被 other 的 3 s 虚大）",
             f"纯函数={pure} · 端到端 exit={r8.returncode}（期望 7）"
             f" phase_failures={j8.get('phase_failures')} · "
             f"primary own_wall={pw}s（期望 <2） · other own_wall={ow}s（期望 ≥2.5） · "
             f"重叠区间={j8.get('overlap_interval_s')}s（期望 <1.5；⛔ 旧实现会虚大成 ~2.6）")

        # ---- ③ 闸门不过 ⇒ 一条命令都不打（真起子进程，桩的 marker 作证）
        print("\n=== ③ 闸门不过 ⇒ **一条命令都不打**（桩命令一旦执行就会写 marker）===")
        sp = _stub_spec(tmp / "g", ctx, phase="compact", kind="train", marker=marker)
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "pair",
                            "--driver", str(sp),
                            "--data-root", str(tmp), "--compact-root", str(tmp / "c"),
                            "--raw-root", str(tmp / "r"),
                            "--out", str(tmp / "g.json")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        gj = json.loads((tmp / "g.json").read_text(encoding="utf-8")) if (tmp / "g.json").is_file() else {}
        ok3 = (r.returncode == 3 and not marker.exists()
               and gj.get("commands_run") == 0
               and any("不打任何命令" in (x or "") for x in [r.stdout])
               and gj.get("exit_code") == 3)
        case("③ 闸门不过不打命令", ok3,
             "`compact ‖ train` ⇒ exit 3 且桩从未执行（marker 不存在）、JSON 里 commands_run=0",
             f"exit={r.returncode} marker_exists={marker.exists()} "
             f"commands_run={gj.get('commands_run')} 输出含'不打任何命令'="
             f"{'不打任何命令' in (r.stdout or '')}")

        # ---- ④ 两文件都缺 ⇒ 必红（假绿守卫）
        print("\n=== ④ 两文件都缺的比较 ⇒ **必红**（假绿守卫；旧写法两空哈希会判成'相同'）===")
        c_both = compare_files(tmp / "no-such-a.bin", tmp / "no-such-b.bin")
        f1, f2 = tmp / "s1.bin", tmp / "s2.bin"
        f1.write_bytes(b"same"); f2.write_bytes(b"same")
        c_same = compare_files(f1, f2)
        f2.write_bytes(b"diff")
        c_diff = compare_files(f1, f2)
        c_one = compare_files(f1, tmp / "no-such-b.bin")
        ok4 = (c_both["same"] is False and c_same["same"] is True
               and c_diff["same"] is False and c_one["same"] is False)
        case("④ 缺文件必红", ok4,
             "两文件都缺 ⇒ same=False（⛔ 不是'相同'）；都存在且同 ⇒ True；不同 ⇒ False；缺一个 ⇒ False",
             f"都缺={c_both['same']} 同={c_same['same']} 不同={c_diff['same']} 缺一个={c_one['same']} · "
             f"{c_both['reason']}")

        # ---- ⑤ `--chunk` / 预算 `<= 0` 的语义
        print("\n=== ⑤ `--chunk` / 预算 `<= 0` 的语义（AGENTS.md §6.5 第三条）===")
        bad_chunk = []
        for v in (0, -1, -100):
            try:
                chunk_rows(v)
                bad_chunk.append(f"{v}:⛔未拒")
            except ValueError:
                bad_chunk.append(f"{v}:拒")
        good_chunk = chunk_rows(100000) == 100000
        # 预算用尽 ⇒ 0（⛔ 不是 −1）；"0=关/<0=不限"式开关一律 `<= 0`
        ex = budget_after_spend(10, 10) == 0.0 and budget_after_spend(10, 25) == 0.0 \
            and budget_after_spend(10, 4) == 6.0 and budget_after_spend(0, 3) == 0.0 \
            and budget_after_spend(-5, 3) == 0.0
        fl = (limit_active(0) is False and limit_active(-1) is False
              and limit_active(0.001) is True)
        wk = []
        for v in (0, -1, 9):
            try:
                workers_ok(v)
                wk.append(f"{v}:⛔未拒")
            except ValueError:
                wk.append(f"{v}:拒")
        ok5 = all(x.endswith(":拒") for x in bad_chunk) and good_chunk and ex and fl \
            and workers_ok(8) == 8 and all(x.endswith(":拒") for x in wk)
        case("⑤ `<= 0` 语义", ok5,
             "`--chunk<=0` ⇒ 坏值拒；预算用尽 ⇒ **0**（⛔ 非 −1）；`0=关/<0=不限` 一律判 `<=0`；"
             "`--workers<=0` 或 >8 ⇒ 拒",
             f"chunk {' '.join(bad_chunk)} · chunk(100000)={good_chunk} · 预算用尽置 0={ex} · "
             f"开关判据 {fl} · workers {' '.join(wk)}")

        # ---- ⑥ 收尸后"半成品确实消失"（+ 删不掉 ⇒ exit 5）
        print("\n=== ⑥ 收尸：带重试删 + **复验消失**；删不掉 ⇒ 不回退且 exit 5 ===")
        half = tmp / "halfproduct"
        (half / "sub").mkdir(parents=True, exist_ok=True)
        (half / "train.tile.npy").write_bytes(b"x" * 4096)
        (half / "sub" / "job0.log").write_text("log", encoding="utf-8")
        rm1 = rm_safe(half, "自检半成品", (str(tmp),))
        gone1 = (not half.exists()) and rm1.get("verified_gone") is True
        out_of_root = rm_safe(Path(tmp).resolve(), "越界（不许删白名单根自身/界外）", (str(tmp / "nowhere"),))
        refuse_ok = out_of_root.get("removed") is False
        # 删不掉：Windows 上 `open()` 的文件默认没有 FILE_SHARE_DELETE ⇒ rmtree 必失败
        locked = tmp / "locked_dir"
        locked.mkdir(parents=True, exist_ok=True)
        lf = locked / "held.bin"
        lf.write_bytes(b"held")
        fh = open(lf, "rb")                          # 持着句柄
        try:
            rm2 = rm_safe(locked, "自检：被占用的半成品", (str(tmp),), attempts=2, sleep_s=0.2)
            held_ok = (rm2.get("removed") is False and rm2.get("verified_gone") is False
                       and exit_for_fallback(rm=rm2) == 5)
        finally:
            fh.close()
        rm3 = rm_safe(locked, "自检：句柄释放后重删", (str(tmp),), attempts=3, sleep_s=0.2)
        release_ok = rm3.get("verified_gone") is True
        ok6 = gone1 and refuse_ok and held_ok and release_ok
        case("⑥ 收尸复验消失", ok6,
             "删完**复验路径真的不在了**；越界一律拒删；被占用 ⇒ removed=False 且 exit 5；"
             "句柄释放后**重删成功**",
             f"删除并复验={gone1}（attempts={rm1.get('attempts')} bytes={rm1.get('bytes')}）· "
             f"越界拒删={refuse_ok} · 占用时 removed={rm2.get('removed')} "
             f"verified_gone={rm2.get('verified_gone')} exit={exit_for_fallback(rm=rm2)} · "
             f"释放后重删={release_ok}")

    bad = [c for c in cases if not c["ok"]]
    out["cases"] = cases
    out["ok"] = not bad
    out["exit_code"] = 0 if not bad else 1
    print(f"\n{'全部通过' if not bad else str(len(bad)) + ' 条不通过'}（{len(cases)} 例）")
    return (0 if not bad else 1), out


# ================================================================ main
def build_ctx(a) -> dict:
    """把 CLI 的**根/并发/预算**收成一个占位符上下文（spec 里用 `${DATA_ROOT}` 等引用它）。

    ⚠ 三个根都**只在没给参数时**才回落到"缺省值（可覆盖）"那一段的常量 ⇒ 换根不用改 spec 一个字节。
    """
    data_root = str(Path(getattr(a, "data_root", None) or DEFAULT_DATA_ROOT))
    compact_root = str(Path(getattr(a, "compact_root", None) or DEFAULT_COMPACT_ROOT))
    raw_root = str(Path(getattr(a, "raw_root", None) or DEFAULT_RAW_ROOT))
    workers = workers_ok(getattr(a, "workers", 8))
    chunk = chunk_rows(getattr(a, "chunk", 100000))   # ⛔ `<= 0` ⇒ 坏值（ValueError ⇒ exit 2）
    timeout = float(getattr(a, "timeout", 0.0) or 0.0)
    # `REPO` 由**本文件的位置**推出来（⛔ 不写死）⇒ spec 里可以写 `${REPO}/tools/build/...`
    repo = str(Path(__file__).resolve().parent.parent)
    return {"DATA_ROOT": data_root, "COMPACT_ROOT": compact_root, "RAW_ROOT": raw_root,
            "WORKERS": workers, "CHUNK": chunk, "TIMEOUT": timeout, "REPO": repo,
            # `${PY}` = **编排器自己的解释器**：枚举自检已经证明它是好的，主相就用它
            # （⚠ 实测：工作区内的解释器拿的是**受限令牌**，既枚举不全、也写不了独立根那些盘）
            "PY": sys.executable}


def add_common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--driver", default=None,
                    help="驱动规格 JSON（两条臂的命令/写目录/冻结输入）；可用 ${DATA_ROOT} 等占位符")
    ap.add_argument("--pair", default=None, metavar="A:B",
                    help="重叠对，A ∈ compact|readings|train，B ∈ collect（缺省由 spec 推断）")
    ap.add_argument("--data-root", default=None, help=f"缺省 {DEFAULT_DATA_ROOT}（⛔ 可覆盖）")
    ap.add_argument("--compact-root", default=None, help=f"缺省 {DEFAULT_COMPACT_ROOT}（⛔ 可覆盖）")
    ap.add_argument("--raw-root", default=None, help=f"缺省 {DEFAULT_RAW_ROOT}（⛔ 可覆盖）")
    ap.add_argument("--workers", type=int, default=8, help="并发度（**硬上限 8**）")
    ap.add_argument("--timeout", type=float, default=0.0,
                    help="每相等待上限秒；**<=0 ⇒ 不限（关）**")
    ap.add_argument("--chunk", type=int, default=100000,
                    help="分块上限（行）；**<=0 是坏值 ⇒ exit 2**（⛔ 不是'不限'）")
    ap.add_argument("--min-commit-free-gb", type=float, default=None, help="覆盖内存闸门阈值（缺省 8.0）")
    ap.add_argument("--out", default=None, help="机器可读 JSON 输出路径")
    ap.add_argument("--evidence-dir", default=None, help="账本/日志目录（缺省 <data-root>/w-ladder/_overlap/tool）")
    ap.add_argument("--no-fallback", action="store_true", help="内存闸门触发时只停不回退")


def emit(result: dict, out_path) -> None:
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"\n[out] {p.resolve()}")


def main(argv=None) -> int:
    try:                                     # ⚠ 控制台是 GBK：不强制 UTF-8 会把子进程留成孤儿/乱码
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:                                            # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(prog=TOOL_NAME, description="重叠编排器（七闸门 + fail-closed 探针）")
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("pair", "check"):
        s = sub.add_parser(name)
        add_common(s)
        s.add_argument("--overlap", action="store_true",
                       help="**开重叠**（缺省串行：主相跑完再起从相 —— 顺序是判据本身）")
        s.add_argument("--only", default=None,
                       help="只跑一相（primary|other|compact|readings|train|collect）")
        s.add_argument("--allow-busy", action="store_true",
                       help="G7 的显式豁免：有竞争负载时仍然跑。⚠ 读数**已污染**，只能当结构验证，"
                            "⛔ 不得用作收益数字（**探针 fail-closed 没有豁免开关**）")
        s.add_argument("--wait-quiet", type=float, default=0.0, metavar="SEC",
                       help="先**等**最多 SEC 秒盼一个静窗（每 15 s 扫一次，静了立刻起）")
    st = sub.add_parser("selftest")
    st.add_argument("--out", default=None)
    st.add_argument("--data-root", default=None)
    st.add_argument("--compact-root", default=None)
    st.add_argument("--raw-root", default=None)
    st.add_argument("--workers", type=int, default=8)
    st.add_argument("--timeout", type=float, default=0.0)
    st.add_argument("--chunk", type=int, default=100000)
    cm = sub.add_parser("calibrate-mem")
    cm.add_argument("--anon-gb", type=float, default=2.0)
    cm.add_argument("--file-gb", type=float, default=1.0)
    cm.add_argument("--out", default=None)
    cm.add_argument("--evidence-dir", default=None)
    cm.add_argument("--data-root", default=None)
    a = ap.parse_args(argv)

    try:
        ctx = build_ctx(a)
    except ValueError as e:                  # ⛔ 坏值（如 `--chunk <= 0`）⇒ exit 2，不是"不限"
        print(f"⛔ 参数坏值：{e}")
        return 2

    if a.mode == "selftest":
        code, result = mode_selftest(a, ctx)
        result["override"] = ctx
        emit(result, a.out)
        return code
    if a.mode == "calibrate-mem":
        code, result = mode_calibrate_mem(a, ctx)
        result["override"] = ctx
        emit(result, a.out)
        return code

    if not a.driver:
        print("⛔ `pair`/`check` 需要 `--driver <spec.json>`（两条臂的命令/写目录/冻结输入在里面）")
        return 2
    try:
        spec = load_spec(Path(a.driver), ctx, {
            "workers": a.workers, "timeout": a.timeout,
            "min_commit_free_gb": a.min_commit_free_gb, "no_fallback": a.no_fallback})
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"⛔ 驱动规格读不了/坏了：{type(e).__name__}: {e}")
        return 2
    if a.evidence_dir:
        spec["evidence_dir"] = a.evidence_dir
    # `--pair A:B` = **断言**（你声明"这份 spec 是哪个对"）；与 spec 不符 ⇒ exit 2。
    # ⛔ 刻意**不**用 `--pair` 去覆盖 spec 的 phase/kind：那会让"train 的命令"被当成 compact 跑，
    #    而 G1 还照样绿 —— 闸门被命令行绕过去，比没有闸门更坏。
    if a.pair:
        want = a.pair.split(":")
        if len(want) != 2:
            print(f"⛔ `--pair` 形状应为 A:B（拿到 {a.pair!r}）")
            return 2
        if PRIMARY_ALIASES.get(want[0].strip().lower()) != spec["phase"] \
                or want[1].strip().lower() != spec["other"]["kind"]:
            print(f"⛔ `--pair {a.pair}` 与 spec 不符：spec 是 "
                  f"{spec['phase']}:{spec['other']['kind'] or '(空)'} —— "
                  f"要么改 `--pair`，要么改 spec（⛔ 本工具不许命令行覆盖 spec 的相）")
            return 2

    code, result = (mode_check(spec, a, ctx) if a.mode == "check" else mode_pair(spec, a, ctx))
    emit(result, a.out)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
