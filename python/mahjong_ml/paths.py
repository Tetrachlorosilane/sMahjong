"""数据根与配额闸门 —— `docs/TRAINING.md` §0.1.1 的可执行版本（约束 ①）。

**唯一数据根 = `S:\\mahjong-training\\`**（卷标 `Silicon_files`）。仓库里不留训练数据。
本模块存在的理由是一条实测教训：`SelfPlay` 建不出目录时**只打一条 WARN 就继续跑**，
采集"看起来成功"却一个文件都没有 —— 所以**落盘前必须由这里显式闸门**，失败就抛异常（不要静默降级）。

⚠ **2026-09 从 `T:\\mahjong-training\\`（卷标 TrainingData）迁到这里**：T 盘扛不住自对弈的
**每文件开销**（实测：把每场轨迹从 1.1 MB 压到 21 KB，吞吐**一点没变**，都是 0.7 场/秒左右，
而且 java 只占 ~15/24 核 —— 瓶颈是"每场一个文件"这件事，不是字节数）。S 盘同一批数据
robocopy 迁移后逐文件校验一致（数量/字节/SHA256）。详见 `docs/TRAINING.md` §0.1.1 的迁移记录。

    from mahjong_ml import paths
    paths.ensure_root()                     # 建六个子目录；不可写 → DataRootError
    out = paths.allocate("raw", "run-001")  # 配额检查 + 滚动淘汰；返回可写的目录
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

# ------------------------------------------------------------------ 常量（与 §0.1.1 表一致）

#: 可用 `MAHJONG_DATA_ROOT` 覆盖（跨机器/换盘时只改环境变量，不动代码）。
DATA_ROOT = Path(os.environ.get("MAHJONG_DATA_ROOT", r"S:\mahjong-training"))

SUBDIRS = ("raw", "compact", "ckpt", "league", "logs", "probe")

#: 各子目录的**硬配额**（GB）。超了就滚动淘汰最旧的（`raw` 先淘汰），并始终保留 MIN_FREE_GB。
#: ⚠ `compact` 从 10 GB 提到 **50 GB**（2026-09，用户指定）：P4 的世代循环每代要一份紧凑集
#: （~1.5 GB），四代就把 10 GB 顶爆，滚动淘汰把 `compact/rl-001`（P3 的数据集）删了 ——
#: 而淘汰是**按 mtime 从最旧的开始**，不区分"废弃数据集"和"还要复算的数据集"。
QUOTA_GB: dict[str, float] = {
    "raw": 30.0,
    "compact": 50.0,
    "ckpt": 2.0,
    "league": 1.0,
    "logs": 1.0,
}

#: 磁盘余量下限（GB）：任何时刻都要留这么多（写满盘会让采集静默失败）。
MIN_FREE_GB = 10.0


class DataRootError(RuntimeError):
    """数据根不可用（不存在/不可写）—— **不要**降级去写别处（那会违反约束 ①）。"""


class QuotaError(RuntimeError):
    """配额或磁盘余量不够，且滚动淘汰也腾不出空间。"""


# ------------------------------------------------------------------ 基本量

def dir_size_bytes(path: Path) -> int:
    """目录占用（字节）。目录不存在返回 0。"""
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def free_gb() -> float:
    """数据根所在盘的剩余空间（GB）。"""
    root = DATA_ROOT if DATA_ROOT.exists() else DATA_ROOT.anchor
    return shutil.disk_usage(root).free / 1024**3


def ensure_root(root: Path | None = None) -> Path:
    """建好数据根与六个子目录，并**验证可写**（写→删探针）。

    @raise DataRootError: 建不出目录或不可写（含受限沙箱的 PermissionError）
    """
    root = Path(root) if root is not None else DATA_ROOT
    try:
        for d in SUBDIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
        probe = root / ".write-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise DataRootError(
            f"数据根不可写：{root} —— {e}\n"
            f"（受限沙箱下子进程写工作区外路径会被拒；采集请在普通 shell 里跑。"
            f"⛔ 不要改存到仓库目录，那违反 §0.1 约束 ①）") from e
    return root


# ------------------------------------------------------------------ 配额与滚动淘汰

def _entries(kind: str) -> list[tuple[float, Path]]:
    """某子目录下的条目（按 mtime 升序 = 最旧的在前）。"""
    d = DATA_ROOT / kind
    if not d.is_dir():
        return []
    out: list[tuple[float, Path]] = []
    for p in d.iterdir():
        try:
            out.append((p.stat().st_mtime, p))
        except OSError:
            continue
    return sorted(out)


def _rm(path: Path) -> int:
    before = dir_size_bytes(path) if path.is_dir() else (path.stat().st_size if path.exists() else 0)
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    except OSError:
        pass
    return before


def enforce_quota(kind: str, *, need_bytes: int = 0) -> list[str]:
    """把 `kind` 压回配额内：**从最旧的开始删**，直到"够用 + 留足余量"。

    @return 被删掉的条目名（便于日志/审计）
    """
    if kind not in QUOTA_GB:
        return []
    quota = int(QUOTA_GB[kind] * 1024**3)
    removed: list[str] = []
    # ① 先按自身配额
    while dir_size_bytes(DATA_ROOT / kind) + need_bytes > quota:
        entries = _entries(kind)
        if not entries:
            break
        _, victim = entries[0]
        _rm(victim)
        removed.append(victim.name)
    # ② 再看整盘余量（留 MIN_FREE_GB）—— 不够就继续删（仍从最旧的删）
    while free_gb() < MIN_FREE_GB + need_bytes / 1024**3:
        entries = _entries(kind)
        if not entries:
            total = sum(t for t, _ in _entries("raw") + _entries("compact"))
            raise QuotaError(
                f"磁盘余量不足（{free_gb():.2f} GB < {MIN_FREE_GB} GB）且 {kind} 已无可删条目"
                f"（raw+compact 共 {total/1024**3:.2f} GB）")
        _, victim = entries[0]
        _rm(victim)
        removed.append(victim.name)
    return removed


def allocate(kind: str, label: str, *, need_bytes: int = 0, root: Path | None = None) -> Path:
    """为一次采集/产物分配目录：**先过闸门，再给路径**。

    @param kind  `raw` / `compact` / `ckpt` / `league` / `logs` / `probe`
    @param label 子目录名（如 `run-001` / `gen-07`）
    """
    ensure_root(root)
    if kind not in SUBDIRS:
        raise ValueError(f"未知类别 {kind}（可用：{', '.join(SUBDIRS)}）")
    enforce_quota(kind, need_bytes=need_bytes)
    d = DATA_ROOT / kind / label
    d.mkdir(parents=True, exist_ok=True)
    return d


def report() -> str:
    """一行式状态：各子目录占用 / 配额 + 盘余量（写进日志用）。"""
    parts = []
    for k in SUBDIRS:
        size = dir_size_bytes(DATA_ROOT / k) / 1024**3
        q = QUOTA_GB.get(k)
        parts.append(f"{k}={size:.2f}GB" + (f"/{q:.0f}GB" if q else ""))
    return f"{DATA_ROOT} | " + " ".join(parts) + f" | free={free_gb():.2f}GB"


if __name__ == "__main__":                       # python -m mahjong_ml.paths
    print(report())
    try:
        ensure_root()
        print("可写：是")
    except DataRootError as e:
        print(f"可写：否 —— {e}")
