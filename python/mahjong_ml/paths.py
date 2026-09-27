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
import re
import shutil
import time
from pathlib import Path

# ------------------------------------------------------------------ 常量（与 §0.1.1 表一致）

#: 可用 `MAHJONG_DATA_ROOT` 覆盖（跨机器/换盘时只改环境变量，不动代码）。
DATA_ROOT = Path(os.environ.get("MAHJONG_DATA_ROOT", r"S:\mahjong-training"))

SUBDIRS = ("raw", "compact", "ckpt", "league", "logs", "probe")

#: 各子目录的**硬配额**（GB）。超了就滚动淘汰最旧的（`raw` 先淘汰），并始终保留 MIN_FREE_GB。
#: ⚠ `compact` 10 → **50 GB**（2026-09 用户指定）→ **100 GB**（同日再次指定）：P4 的世代循环每代要
#: 一份紧凑集（~1.5 GB），四代就把 10 GB 顶爆，而滚动淘汰**按 mtime 从最旧的开始删**、
#: 不区分"废弃数据集"和"还要复算的数据集"，于是把 `compact/rl-001`（P3 的数据集）删了。
#: ⚠ **2026-09-26：S 盘上限抬到 200 GB（用户指定）** —— 见下面的 `TOTAL_QUOTA_GB`：
#: `raw` 30 → **80 GB**（≈44 代）、`compact` 100 → **116 GB**（≈77 代），
#: 五项之和**恰好 = 200 GB**（这样"总量上限"与"分项上限"不会互相打脸：分项加起来够不到总量时，
#: 总配额就是一句空话）。
QUOTA_GB: dict[str, float] = {
    "raw": 80.0,
    "compact": 116.0,
    "ckpt": 2.0,
    "league": 1.0,
    "logs": 1.0,
}

#: **数据根总配额**（GB，2026-09-26 用户指定 **200**）。
#: 与分项配额**同时生效**：先按 kind 压回自己的配额，再看总量 —— 超了就从最旧的开始淘汰
#: （只在"每代一份、可复算"的 `raw` / `compact` 里挑，`probe` 用完即删、不参与）。
#: ⚠ 它只是**策略上限**：真正的兜底是下面的 `MIN_FREE_GB`（S 盘物理 231 GB，别把它当成"一定有 200 GB 可用"）。
TOTAL_QUOTA_GB = 200.0

#: 磁盘余量下限（GB）：任何时刻都要留这么多（写满盘会让采集静默失败）。
MIN_FREE_GB = 10.0

#: **永不淘汰**的条目（名字精确匹配）∪ 任何带 `.keep` 标记文件的条目。
#: ⚠ 为什么需要它：`bc-v3-001` 是 v3 的 BC 基座（整条 P4 谱系的 `--init`），而它的 `raw`
#: 早已被配额淘汰 ⇒ **删了就再也造不出来**。同理，凡是想长期留档的集合，在里面放一个 `.keep` 即可。
KEEP_NAMES = frozenset({"bc-v3-001"})
KEEP_MARKER = ".keep"

#: **保留最近 N 代**（只对"每代一份"的条目生效：名字形如 `<label>-g<数字>`，按 label 前缀分别计数）。
#: 超出的旧代会被排到淘汰队列**最前面**（真正删不删仍由配额/余量决定）⇒ 稳态占用可预测，
#: 而不是"一路写到爆、爆了才删最旧的"。⚠ 它**不会**碰 protected 条目，也不会碰评测小目录
#: （`*-ladder-*` / `*-pair-*` 那些不匹配 `-g<数字>$`，体积也小）。
KEEP_LAST: dict[str, int] = {"raw": 2, "compact": 4}

#: "每代一份"的命名（与 `online.short_of()` 一致：`<label>-g01`）
_GEN_RE = re.compile(r"^(?P<label>.+)-g(?P<gen>\d+)$")


class DataRootError(RuntimeError):
    """数据根不可用（不存在/不可写）—— **不要**降级去写别处（那会违反约束 ①）。"""


class QuotaError(RuntimeError):
    """配额或磁盘余量不够，且滚动淘汰也腾不出空间。"""


# ------------------------------------------------------------------ 基本量

def _tree_bytes(root: str) -> int:
    """目录总字节（`os.scandir` 版）。

    ⚠ **为什么不用 `Path.rglob("*")` + `p.stat()`**：`raw` 长到 20 万文件时那一路要 **20 秒**，
    而每轮要分配 5 次目录、还要算"可回收多少" ⇒ 光数文件就要几分钟（实测 2026-09-27）。
    `os.scandir` 的 `DirEntry.stat()` 在 Windows 上直接用目录枚举时缓存的元数据，
    不额外开文件、也不建 `Path` 对象。
    """
    total = 0
    stack = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def entry_sizes(kind: str) -> dict[str, int]:
    """**一次扫描**拿到某子目录下每个一级条目的字节数（`{名字: 字节}`）。

    给"可回收多少"用：逐个条目分别遍历会把同一棵树走 N 遍（实测 `raw` 54 个条目 = 26 秒）。
    """
    d = DATA_ROOT / kind
    out: dict[str, int] = {}
    if not d.is_dir():
        return out
    try:
        with os.scandir(d) as it:
            entries = list(it)
    except OSError:
        return out
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False):
                out[e.name] = _tree_bytes(e.path)
            elif e.is_file(follow_symlinks=False):
                out[e.name] = e.stat(follow_symlinks=False).st_size
        except OSError:
            out[e.name] = 0
    return out


def dir_size_bytes(path: Path) -> int:
    """目录占用（字节）。目录不存在返回 0。"""
    if not path.exists():
        return 0
    return _tree_bytes(str(path))


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


def _protected(p: Path) -> bool:
    """永不淘汰：名字在 `KEEP_NAMES` 里，或目录里有 `.keep` 标记（见 `KEEP_NAMES` 的说明）。"""
    try:
        return p.name in KEEP_NAMES or (p / KEEP_MARKER).is_file()
    except OSError:
        return True                                   # 读不动就当它受保护（宁可少删）


def _over_retention(kind: str) -> set[Path]:
    """**超出 `KEEP_LAST` 名额的旧代**（按 label 分组、每组只留最新 N 个）。

    这些条目会被排到淘汰队列最前面 —— 于是"回收"表现为**滚动回收旧代**，而不是
    "一路写到配额上限、然后从最旧的（可能是 BC 基座那种不可重建的）开始删"。
    """
    n = KEEP_LAST.get(kind, 0)
    if not n:
        return set()
    groups: dict[str, list[tuple[float, Path]]] = {}
    for t, p in _entries(kind):                        # 已按 mtime 升序（最旧在前）
        m = _GEN_RE.match(p.name)
        if m:
            groups.setdefault(m.group("label"), []).append((t, p))
    over: set[Path] = set()
    for items in groups.values():
        for _, p in items[:-n] if n < len(items) else []:
            over.add(p)                               # 旧的那几代
    return over


def evictable(kind: str) -> list[Path]:
    """**可淘汰条目**（最该删的在前）：① 超出 `KEEP_LAST` 的旧代（最旧在前）；
    ② 其余非保护条目（最旧在前）。⚠ 与 `enforce_quota` 用的是同一份判据。"""
    ents = [p for _, p in _entries(kind) if not _protected(p)]
    over = _over_retention(kind)
    return [p for p in ents if p in over] + [p for p in ents if p not in over]


def reclaimable_bytes(kind: str) -> int:
    """**不破坏「最近 `KEEP_LAST` 代」的前提下**能腾出多少字节。

    = 超出保留名额的旧代 + 非"每代一份"的杂项（旧的评测轨迹等），**不含**受保护条目、
    也**不含**还在保留名额内的那几代 —— 后者是"实在没别的可删才动"的最后手段，不该被规划当成可用空间。

    ⚠ 规划端（`budget.gates_now`）要用它：`allocate(need_bytes=…)` 现在会**预扣**回收，
    所以"配额余量 + 可回收"才是这一轮真正能用多少 —— 否则规划会永远卡在"余量只剩 8 GB"，
    而实际上前面躺着 16 GB 的旧代等着被回收。
    """
    over = _over_retention(kind)
    sizes = entry_sizes(kind)                         # **一次扫描**（别逐条目各走一遍）
    total = 0
    for _, p in _entries(kind):
        if _protected(p):
            continue
        m = _GEN_RE.match(p.name)
        if p in over or not m:                        # 超名额的旧代 / 不是"每代一份"的杂项
            total += sizes.get(p.name, 0)
    return total


def total_gb() -> float:
    """数据根下**全部**子目录的占用（GB）—— 总配额的判据。"""
    return sum(dir_size_bytes(DATA_ROOT / k) for k in SUBDIRS) / 1024**3


def _next_among(kinds: tuple[str, ...]) -> Path | None:
    """这些子目录里**下一个该淘汰**的条目（跨两类比较：先比"是否超保留名额"，再比 mtime）。"""
    best: tuple[int, float, Path] | None = None
    for k in kinds:
        over = _over_retention(k)
        for t, p in _entries(k):
            if _protected(p):
                continue
            cand = (0 if p in over else 1, t, p)      # 超名额的旧代优先删
            if best is None or cand[:2] < best[:2]:
                best = cand
            break                                     # `_entries` 已按 mtime 升序
    return None if best is None else best[2]


def enforce_quota(kind: str, *, need_bytes: int = 0) -> list[str]:
    """把数据根压回配额内：**从最该删的开始删**，直到"够用 + 留足余量"。

    三道闸门（依次）：
      ① `kind` 自己的分项配额；② **数据根总配额**（`TOTAL_QUOTA_GB`，只淘汰 raw/compact）；
      ③ 整盘余量（留 `MIN_FREE_GB`）。
    ⚠ `probe` 没有分项配额（用完即删），但**仍受总配额约束**。
    ⚠ **保护**：`KEEP_NAMES` / `.keep` 标记的条目永不删（删了不可重建）；若只剩保护条目，
    抛 `QuotaError`（宁可报错，也不悄悄删掉基座）。
    ⚠ **预扣**：`need_bytes` = 这一次**预计要写**的字节数 —— 调用方（`online.py`）按磁盘模型
    投影后传进来，于是空间在**写之前**就腾好了，而不是"写完超了、等下一次分配才发现"。

    @return 被删掉的条目名（便于日志/审计）
    """
    removed: list[str] = []
    # ① 先按自身配额
    if kind in QUOTA_GB:
        quota = int(QUOTA_GB[kind] * 1024**3)
        # ⚠ 尺寸**只数一次**，之后按"删掉多少"递减：`raw` 有 20 万文件，每次循环重走一遍要 20 秒
        cur = dir_size_bytes(DATA_ROOT / kind)
        while cur + need_bytes > quota:
            cands = evictable(kind)
            if not cands:
                raise QuotaError(
                    f"{kind} 占用 {cur/1024**3:.2f} GB + 本次预计 "
                    f"{need_bytes/1024**3:.2f} GB 超过配额 {QUOTA_GB[kind]:.0f} GB，"
                    f"且非保护条目已删完（受保护：{', '.join(sorted(KEEP_NAMES)) or '无'} + 带 "
                    f"{KEEP_MARKER} 的目录）—— 要么抬配额，要么先手工清理")
            cur -= _rm(cands[0])
            removed.append(cands[0].name)
    # ② 再看**总配额**（2026-09-26 加的 200 GB 上限）：只在可复算的两类里淘汰
    #    ⚠ 同①：总数只数一次，之后按"删掉多少"递减（别忘了减，否则循环不收敛 ⇒ 删空后报错）
    tot = int(total_gb() * 1024**3)
    while tot + need_bytes > TOTAL_QUOTA_GB * 1024**3:
        victim = _next_among(("raw", "compact"))
        if victim is None:
            raise QuotaError(
                f"数据根占用 {tot/1024**3:.2f} GB 超过总配额 {TOTAL_QUOTA_GB:.0f} GB，"
                f"且 raw/compact 已无可删条目")
        tot -= _rm(victim)
        removed.append(f"{victim.parent.name}/{victim.name}")
    # ③ 最后看整盘余量（留 MIN_FREE_GB）—— 不够就继续删（仍从最该删的开始）
    while free_gb() < MIN_FREE_GB + need_bytes / 1024**3:
        victim = _next_among(("raw", "compact")) or (evictable(kind) or [None])[0]
        if victim is None:
            raise QuotaError(
                f"磁盘余量不足（{free_gb():.2f} GB < {MIN_FREE_GB} GB）且已无可删条目"
                f"（raw+compact 共 {tot/1024**3:.2f} GB —— 受保护的不算）")
        _rm(victim)
        removed.append(f"{victim.parent.name}/{victim.name}")
    return removed


def allocate(kind: str, label: str, *, need_bytes: int = 0, root: Path | None = None) -> Path:
    """为一次采集/产物分配目录：**先过闸门（含回收），再给路径**。

    ⚠ `need_bytes` 是**这一次预计要写多少**（调用方按磁盘模型投影）—— 传了它，回收就发生在
    **写之前**；不传（=0）就只能"写完超了、下次分配才发现"，那正是 2026-09-27 把 `compact`
    顶到 92/116 GB 才有人管的原因。回收了谁会被打出来（`[paths] 回收 …`），别让它静默发生。

    @param kind  `raw` / `compact` / `ckpt` / `league` / `logs` / `probe`
    @param label 子目录名（如 `ppo-v3-g01-g07`）
    """
    ensure_root(root)
    if kind not in SUBDIRS:
        raise ValueError(f"未知类别 {kind}（可用：{', '.join(SUBDIRS)}）")
    removed = enforce_quota(kind, need_bytes=need_bytes)
    if removed:
        print(f"[paths] {kind} 回收 {len(removed)} 项：{', '.join(removed)}", flush=True)
    d = DATA_ROOT / kind / label
    d.mkdir(parents=True, exist_ok=True)
    return d


def report() -> str:
    """一行式状态：各子目录占用 / 配额 + **总量/总配额** + 盘余量 + **下一个会被回收的是谁**。"""
    parts = []
    for k in SUBDIRS:
        size = dir_size_bytes(DATA_ROOT / k) / 1024**3
        q = QUOTA_GB.get(k)
        parts.append(f"{k}={size:.2f}GB" + (f"/{q:.0f}GB" if q else ""))
    nxt = [f"{k}:{_next_among((k,)).name}" for k in ("raw", "compact") if _next_among((k,))]
    return (f"{DATA_ROOT} | " + " ".join(parts)
            + f" | total={total_gb():.2f}GB/{TOTAL_QUOTA_GB:.0f}GB | free={free_gb():.2f}GB"
            + (f" | 下一个回收 {' '.join(nxt)}" if nxt else ""))


if __name__ == "__main__":                       # python -m mahjong_ml.paths
    print(report())
    try:
        ensure_root()
        print("可写：是")
    except DataRootError as e:
        print(f"可写：否 —— {e}")
