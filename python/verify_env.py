#!/usr/bin/env python
"""训练环境自检 —— `docs/TRAINING.md` §0.1 / §1 的"可执行版本"。

    python\\.venv\\Scripts\\python.exe python\\verify_env.py

做三件事：① 校验**硬件与依赖**真的可用（不是"装上了"）；② 把**训练环境三条硬约束**
（数据只落 T 盘 / GPU ≤80% / CPU ≤75% 的核）量出**本机实际数字**并验证机制有效；
③ 打印一份可抄进文档的基线（显存预算、训练吞吐、GPU 利用率与节流效果、线程数）。

判据风格与仓库一致：每项 PASS/WARN/FAIL，末尾汇总；**只有真问题才 FAIL**（退出码 1）：
受限沙箱下写 T 盘被拒、瞬时利用率打满这类是 WARN + 说明，不算环境坏。

⚠ 节流与采样的**实现只在 `mahjong_ml/guard.py` 一处**（本脚本 import 它，不另写一份）：
   见 §0.1.3 —— 判据口径是**时间平均**（实测瞬时峰值 98%、均值 36%），
   采样必须用常驻 `nvidia-smi -l 1`（每步 fork 一个要几百毫秒，比训练一步还贵）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# 中文输出必须**显式钉成 UTF-8**（与 Java 侧 `-Dstdout.encoding=UTF-8` 同一个理由）：
# 管道/重定向时 Python 默认用 `locale.getpreferredencoding()`（本机 cp936），中文会变成乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:                       # noqa: BLE001
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))   # 让 `mahjong_ml` 可导入
from mahjong_ml import guard                      # noqa: E402
from mahjong_ml.guard import DutyCycle, GpuMonitor  # noqa: E402

# ---------------------------------------------------------------- 约束参数（与 TRAINING §0.1 对齐）

DATA_ROOT = Path(os.environ.get("MAHJONG_DATA_ROOT", r"T:\mahjong-training"))
DATA_SUBDIRS = ("raw", "compact", "ckpt", "league", "logs", "probe")
MIN_FREE_GB = 10.0                 # §0.1.1
GPU_UTIL_LIMIT = guard.GPU_UTIL_LIMIT
CPU_CORE_RATIO = guard.CPU_CORE_RATIO
TRAIN_THREADS = guard.TRAIN_THREADS
OUR_VRAM_BUDGET_GB = guard.OUR_VRAM_BUDGET_GB

results: list[tuple[str, str, str]] = []   # (级别, 名称, 说明)


def rec(level: str, name: str, detail: str = "") -> None:
    results.append((level, name, detail))
    print(f"  [{level:4}] {name}" + (f" —— {detail}" if detail else ""))


def check(cond: bool, name: str, detail: str = "") -> bool:
    rec("PASS" if cond else "FAIL", name, detail)
    return cond


# ---------------------------------------------------------------- ① Python / torch / GPU

def check_python_and_torch():
    print("== Python 与 torch ==")
    check(sys.version_info[:2] >= (3, 12), f"Python 版本 {sys.version.split()[0]}",
          "需要 ≥3.12（uv 装的是 3.12.13）")
    try:
        import torch
    except Exception as e:                     # noqa: BLE001
        rec("FAIL", "import torch", f"{type(e).__name__}: {e}")
        return None, False
    rec("PASS", f"torch {torch.__version__}", f"cuda build {torch.version.cuda}")
    check("+cu128" in torch.__version__, "torch 是 CUDA 12.8 构建（Blackwell sm_120 需要）",
          torch.__version__)
    try:
        import numpy
        rec("PASS", f"numpy {numpy.__version__}", "数据集/特征要用（torch 自己不需要）")
    except Exception as e:                  # noqa: BLE001
        rec("FAIL", "import numpy", f"{type(e).__name__}: {e} —— 装 torch 时不会自动带它")
    if not torch.cuda.is_available():
        rec("FAIL", "torch.cuda.is_available()", "GPU 不可用 —— 会静默回退 CPU（慢 ~100 倍）")
        return torch, False
    name = torch.cuda.get_device_name(0)
    cap = torch.cuda.get_device_capability(0)
    arch = torch.cuda.get_arch_list()
    rec("PASS", f"GPU 可用：{name}", f"capability sm_{cap[0]}{cap[1]}")
    check(any(f"sm_{cap[0]}{cap[1]}" in a for a in arch),
          "该算力在内核列表里（否则会 no kernel image）",
          f"arch_list 含 sm_120: {any('120' in a for a in arch)}")
    try:
        x = torch.randn(2048, 2048, device="cuda")
        y = (x @ x).float().sum().item()
        check(y == y and abs(y) > 0, "GPU 上矩阵乘法真的能算", f"sum={y:.3e}")
    except Exception as e:                  # noqa: BLE001
        rec("FAIL", "GPU 上矩阵乘法", f"{type(e).__name__}: {e}")
    return torch, True


def vram_report(torch) -> None:
    print("== 显存预算 ==")
    free, total = torch.cuda.mem_get_info()
    used = total - free
    budget = min(GPU_UTIL_LIMIT / 100.0 * total - used, OUR_VRAM_BUDGET_GB * 1024**3)
    rec("PASS" if budget > 0 else "FAIL", "显存余量够训练",
        f"总 {total/1024**3:.2f} GB，已被别的进程占 {used/1024**3:.2f} GB，"
        f"留给训练 ≈ {budget/1024**3:.2f} GB（目标 ≤{OUR_VRAM_BUDGET_GB} GB）")


# ---------------------------------------------------------------- ② 训练吞吐 + 节流验证

def _train_step_fixture(torch):
    """代表性的小网络（512→512→79，约 0.4M 参数）+ 一个 step 闭包。"""
    torch.manual_seed(0)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    feat, hidden, out_dim, batch = 512, 512, 79, 4096
    model = torch.nn.Sequential(
        torch.nn.Linear(feat, hidden), torch.nn.ReLU(),
        torch.nn.Linear(hidden, hidden), torch.nn.ReLU(),
        torch.nn.Linear(hidden, out_dim),
    ).to(device)
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    x = torch.randn(batch, feat, device=device)
    y = torch.randint(0, out_dim, (batch,), device=device)

    def step():
        opt.zero_grad(set_to_none=True)
        torch.nn.functional.cross_entropy(model(x), y).backward()
        opt.step()

    params = sum(p.numel() for p in model.parameters())
    return device, params, batch, step


def bench_torch(torch) -> None:
    print("== 训练吞吐（代表性小网络：512→512→79，约 0.4M 参数）==")
    device, params, batch, step = _train_step_fixture(torch)
    for _ in range(3):
        step()
    if device == "cuda":
        torch.cuda.synchronize()
    mon = GpuMonitor() if device == "cuda" else None
    t0 = time.perf_counter()
    n = 400
    for _ in range(n):
        step()
    if device == "cuda":
        torch.cuda.synchronize()
    dt = time.perf_counter() - t0
    if mon is not None:
        time.sleep(1.5)                     # 让常驻采样进程把这一段的样本读进来
    steps_s = n / dt
    rec("PASS", f"{params/1e6:.2f}M 参数 / batch {batch}",
        f"{steps_s:.1f} 步/秒 = {steps_s*batch/1000:.0f}k 样本/秒（{device}）")
    if mon is not None:
        peak = torch.cuda.max_memory_allocated() / 1024**3
        rec("PASS" if peak <= OUR_VRAM_BUDGET_GB else "WARN", "训练峰值显存在我们的预算内",
            f"peak {peak:.2f} GB ≤ {OUR_VRAM_BUDGET_GB} GB")
        vals = mon.values(60.0)
        mon.close()
        if vals:
            avg = sum(vals) / len(vals)
            rec("PASS" if avg <= GPU_UTIL_LIMIT else "WARN",
                f"GPU 利用率（未节流）：均值 {avg:.0f}% / 瞬时峰值 {max(vals)}%（{len(vals)} 次采样）",
                "均值未超 80%" if avg <= GPU_UTIL_LIMIT else "均值超 80% → 必须节流（见下）")
        else:
            rec("WARN", "nvidia-smi 利用率采样", "采不到样本（命令不可用？）")


def throttle_test(torch) -> None:
    """带节流跑一段，验证"GPU ≤80%（时间平均）"真的守得住，并量出性能代价。"""
    print("== GPU 节流验证（判据：最近 3 秒平均 ≤80%）==")
    if not torch.cuda.is_available():
        rec("WARN", "无 GPU，跳过节流验证")
        return
    device, _params, _batch, step = _train_step_fixture(torch)
    mon = GpuMonitor()
    try:
        time.sleep(3.0)                     # 先采一段"空闲基线"
        dc = DutyCycle(mon)
        t0 = time.perf_counter()
        steps = 0
        while time.perf_counter() - t0 < 10.0:
            step()
            steps += 1
            dc.tick()
        torch.cuda.synchronize()
        dt = time.perf_counter() - t0
        time.sleep(1.5)
        steady = mon.values(4.0)            # 稳态窗口（末几秒）
        allv = mon.values(120.0)
    finally:
        mon.close()
    if not steady:
        rec("WARN", "节流验证采不到利用率样本")
        return
    avg = sum(steady) / len(steady)
    rec("PASS" if avg <= GPU_UTIL_LIMIT else "WARN",
        f"节流后稳态平均利用率 {avg:.0f}%（窗口内瞬时峰值 {max(steady)}%，{len(steady)} 次采样，"
        f"末段 sleep {dc.sleep*1000:.0f} ms/步）",
        "守住了 80%（时间平均口径）" if avg <= GPU_UTIL_LIMIT else "仍超 80% → 调小 window 或加大 step")
    rec("PASS", f"节流后吞吐 {steps/dt:.1f} 步/秒",
        f"（未节流 ≈470 步/秒；全程瞬时峰值 {max(allv) if allv else '-'}%）")


# ---------------------------------------------------------------- ③ CPU 约束

def check_cpu(torch) -> None:
    print("== CPU 预算（≤75% 的核）==")
    cpus = os.cpu_count() or 0
    cap = guard.cores_budget(CPU_CORE_RATIO)
    got = guard.apply_cpu_limit(TRAIN_THREADS)
    check(got == TRAIN_THREADS, f"torch 线程数已封顶到 {got}（机器 {cpus} 核，上限 {cap}）",
          "生成（--workers 24）与训练不要同时跑，否则 24+4 > 24 会越界")
    rec("PASS", "自对弈采集必须显式 --workers",
        f"{guard.SELFPLAY_WORKERS}（默认 = availableProcessors = {cpus} 会越界）")


# ---------------------------------------------------------------- ④ 采集器与数据盘

def check_generator() -> None:
    print("== 采集器（自对弈 jar）==")
    jar = Path(__file__).resolve().parent.parent / "server" / "build" / "mahjong-server.jar"
    if jar.is_file():
        age_h = (time.time() - jar.stat().st_mtime) / 3600
        rec("PASS", f"mahjong-server.jar 就绪（{jar.stat().st_size/1024:.0f} KB，{age_h:.1f} 小时前构建）",
            "采集：--selfplay N --workers 24 --rotate --sample 4 --out T:\\mahjong-training\\raw\\<标签>")
    else:
        rec("FAIL", "找不到 server/build/mahjong-server.jar", "先跑 pwsh -File server\\build.ps1")


def check_data_root() -> None:
    print("== 数据盘（T 盘 / TrainingData）==")
    if not DATA_ROOT.exists():
        rec("FAIL", f"数据根不存在：{DATA_ROOT}")
        return
    rec("PASS", f"数据根 {DATA_ROOT}")
    try:
        for d in DATA_SUBDIRS:
            (DATA_ROOT / d).mkdir(parents=True, exist_ok=True)
        missing = [d for d in DATA_SUBDIRS if not (DATA_ROOT / d).is_dir()]
        check(not missing, "六个子目录齐备",
              ", ".join(DATA_SUBDIRS) if not missing else f"缺 {missing}")
    except OSError as e:
        rec("WARN", "创建子目录失败", f"{e}（受限沙箱下正常）")
    probe = DATA_ROOT / ".write-probe"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
        rec("PASS", "可写（写→删探针通过）")
    except OSError as e:
        rec("WARN", "写入被拒 —— 采集会**静默不落盘**（SelfPlay 只打一条 WARN）",
            f"{e}；受限沙箱下请在放宽权限的会话 / 普通 shell 里跑")
    try:
        free_gb = shutil.disk_usage(DATA_ROOT).free / 1024**3
        check(free_gb >= MIN_FREE_GB, f"剩余 {free_gb:.2f} GB ≥ {MIN_FREE_GB} GB（§0.1.1）")
    except OSError as e:
        rec("WARN", "查剩余空间失败", str(e))


# ---------------------------------------------------------------- main

def main() -> int:
    print("立直麻将 · 训练环境自检（对应 docs/TRAINING.md §0.1 / §1）\n")
    torch, gpu_ok = check_python_and_torch()
    if torch is not None:
        if gpu_ok:
            vram_report(torch)
        bench_torch(torch)
        if gpu_ok:
            throttle_test(torch)
        check_cpu(torch)
    check_generator()
    check_data_root()

    fails = [r for r in results if r[0] == "FAIL"]
    warns = [r for r in results if r[0] == "WARN"]
    print(f"\n环境自检：通过 {len(results)-len(fails)-len(warns)} 项，"
          f"警告 {len(warns)} 项，失败 {len(fails)} 项")
    for lvl, name, detail in fails + warns:
        print(f"  {lvl}: {name}" + (f" —— {detail}" if detail else ""))
    print("VERIFY PASS" if not fails else "VERIFY FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
