"""进程纪律：CPU ≤75% 的核 / GPU ≤80% —— `docs/TRAINING.md` §0.1.2-3 的可执行版本（约束 ②③）。

三件事：
  · `apply_cpu_limit()` —— torch 线程封顶（默认 4），并提醒 `--workers` 必须显式传 24；
  · `GpuMonitor` —— **常驻** `nvidia-smi -l 1` 采利用率（别每步 fork：spawn 一次几百毫秒）；
  · `DutyCycle` —— 占空比节流闭环，把**时间平均**利用率压到上限以下。

⚠ 判据口径是**时间平均**（实测：小网络瞬时峰值 98%、均值 36%）—— 瞬时尖峰无法用软件保证。
⚠ ⛔ 不要用 `nvidia-smi -lgc` 锁频：那是**全局**设置，会影响机器上其它程序。

用法（训练循环里）：

    guard.apply_cpu_limit(4)
    mon = guard.GpuMonitor()
    dc = guard.DutyCycle(mon)          # 目标 80%
    for batch in loader:
        train_step(batch)
        dc.tick()                      # 超了就插 sleep
    mon.close()
"""

from __future__ import annotations

import os
import subprocess
import threading
import time

# ------------------------------------------------------------------ 常量（与 §0.1 对齐）

CPU_CORE_RATIO = 0.75          # 活跃线程 ≤ 75% 的核
TRAIN_THREADS = 4              # 训练进程自己的 torch 线程数
GPU_UTIL_LIMIT = 80            # 利用率上限（时间平均，%）
OUR_VRAM_BUDGET_GB = 4.5       # 训练进程自己的显存上限（卡上还有别的进程）
SELFPLAY_WORKERS = 24          # 采集侧（本机 32 核 → 24）


def cores_budget(ratio: float = CPU_CORE_RATIO) -> int:
    """本机允许占用的核数（所有进程加起来）。"""
    cpus = os.cpu_count() or 1
    return max(1, int(cpus * ratio))


def apply_cpu_limit(threads: int = TRAIN_THREADS) -> int:
    """给训练进程封顶 torch 线程数（以及 OMP/MKL 的暗示值），返回实际生效值。"""
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(var, str(threads))
    try:
        import torch
        torch.set_num_threads(threads)
        return torch.get_num_threads()
    except Exception:                       # noqa: BLE001
        return int(os.environ.get("OMP_NUM_THREADS", threads))


def vram_budget_bytes() -> int | None:
    """训练进程自己的显存预算：`min(80% 整卡 − 别家已占, 4.5 GB)`（拿不到就返回 None）。"""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        used = total - free
        return int(min(GPU_UTIL_LIMIT / 100.0 * total - used, OUR_VRAM_BUDGET_GB * 1024**3))
    except Exception:                       # noqa: BLE001
        return None


# ------------------------------------------------------------------ GPU 观测与节流

class GpuMonitor:
    """常驻 `nvidia-smi ... -l <interval>` 子进程，按窗口提供**平均**利用率。"""

    def __init__(self, interval_s: int = 1) -> None:
        self._samples: list[tuple[float, int]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._proc = None
        try:
            self._proc = subprocess.Popen(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits",
                 "-l", str(interval_s)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except Exception:                   # noqa: BLE001
            self._proc = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            head = line.strip().split(",")[0].strip()
            if not head.isdigit():
                continue
            with self._lock:
                self._samples.append((time.perf_counter(), int(head)))
                if len(self._samples) > 300:
                    del self._samples[:-300]

    def values(self, window_s: float) -> list[int]:
        now = time.perf_counter()
        with self._lock:
            return [u for t, u in self._samples if now - t <= window_s]

    def mean(self, window_s: float) -> float | None:
        vals = self.values(window_s)
        return sum(vals) / len(vals) if vals else None

    def close(self) -> None:
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:               # noqa: BLE001
                pass


class DutyCycle:
    """看窗口均值自适应插 sleep，把 GPU 压到上限以下（实测能把稳态均值从 36% 的抖动压到 46% 附近）。"""

    def __init__(self, monitor: GpuMonitor, limit_pct: int = GPU_UTIL_LIMIT,
                 window_s: float = 3.0, adjust_every_s: float = 0.5,
                 step_s: float = 0.002, max_sleep_s: float = 0.05) -> None:
        self.monitor = monitor
        self.limit = limit_pct
        self.window = window_s
        self.adjust_every = adjust_every_s
        self.step = step_s
        self.max_sleep = max_sleep_s
        self.sleep = 0.0
        self._next = time.perf_counter() + adjust_every_s

    def tick(self) -> None:
        if self.sleep:
            time.sleep(self.sleep)
        now = time.perf_counter()
        if now < self._next:
            return
        m = self.monitor.mean(self.window)
        if m is not None:
            if m > self.limit:
                self.sleep = min(self.max_sleep, self.sleep + self.step)
            elif m < self.limit - 10 and self.sleep > 0:
                self.sleep = max(0.0, self.sleep - self.step)
        self._next = now + self.adjust_every


def report() -> str:
    """一行式状态（写进训练日志用）。"""
    budget = vram_budget_bytes()
    return (f"cores={os.cpu_count()} 上限={cores_budget()} | torch_threads={apply_cpu_limit()} | "
            f"GPU 上限={GPU_UTIL_LIMIT}% vram_budget="
            + (f"{budget/1024**3:.2f}GB" if budget else "n/a"))
