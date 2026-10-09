#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style-reward-run.py —— **风格奖励塑形受控实验**的驱动器（一条臂）。

## 为什么需要它（而不是直接敲 `run-league.ps1`）

预注册要求"每代结束后用 `tools/style-vector.py` 读风格位移"，而 `run-league.ps1`
**每代结束时会把这一代的采集轨迹轮换删掉**（`raw\\<tag>` / `compact\\<tag>` / `raw\\eval-<tag>`）。
本仓 ⛔ 不许改 `run-league.ps1` ⇒ 这里**在旁边看着**：另起一个线程跑联赛，
主线程每 5 秒看一次 `raw\\<tag>` 有没有落地，一出现就**立刻把 `g*.jsonl` + `summary.json` 复制走**
（复制 2.8 GB 约十几秒，而那一代的采集是 4 分钟、后面的闸门/轮换在更晚 ⇒ 窗口很宽）。

## 用法

    python tools/style-reward-run.py --arm a --label v4-sty-a --seed 24261001 --generations 4 \\
        --style def --style-pools "def=...;atk=...;win=..."
    python tools/style-reward-run.py --arm b --label v4-sty-b --seed 24261002 --generations 4 \\
        --style def --style-pools "..." --style-bonus 'riichi=2.0:no_deal'

产物：
* `S:\\mahjong-training\\style-reward\\traces\\<tag>\\` —— 每代轨迹的**副本**（只读，留着随时重量）；
* `S:\\mahjong-training\\style-reward\\<label>-run.log` —— 联赛的全部输出（原样）；
* `S:\\mahjong-training\\style-reward\\<label>-manifest.json` —— 每代的
  `tag / raw 目录 / 复制到的目录 / 文件数 / 字节数 / 看到它的时间`（可审计）。

⛔ 本脚本**不**做测量（测量走 `tools/style-vector.py`，那是判据侧的独立实现），
   也**不**改任何判据口径。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MAHJONG_DATA_ROOT", r"S:\mahjong-training"))
STAGE = DATA / "style-reward"
TRACES = STAGE / "traces"

#: 每代先盯这几个文件（`g*.jsonl` 与 `summary.json`）—— 采集一侧的**全部**产物都在这里。
#: ⚠ 只看 `g0.jsonl` 会误判"落地了"：采集是并行写多个文件的，`g0` 出现时别的可能还没写完。
#: 判据 = 目录里 `g*.jsonl` 的数量在两次轮询之间**不再增长**（且 ≥ 1）。
POLL_SECONDS = 5.0
QUIET_SECONDS = 2 * POLL_SECONDS


def _traces_of(raw: Path) -> list[Path]:
    return sorted(p for p in raw.glob("g*.jsonl"))


def _copy_once(raw: Path, dst: Path) -> dict:
    """把这一代的轨迹复制到 `dst`（幂等：已存在且大小一致就跳过）。"""
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    total = 0
    for f in _traces_of(raw):
        t = dst / f.name
        if t.is_file() and t.stat().st_size == f.stat().st_size:
            n += 1
            total += t.stat().st_size
            continue
        shutil.copy2(f, t)
        n += 1
        total += t.stat().st_size
    s = raw / "summary.json"
    if s.is_file():
        shutil.copy2(s, dst / "summary.json")
    return {"files": n, "bytes": total}


def watch(label: str, generations: int, stop: threading.Event) -> list:
    """盯着 `raw\\<label>-g??`：一落地（且大小稳定）就复制走。返回 manifest 行。

    ⚠ **判据是"稳定"而不是"存在"**：采集是并行写多个 `g*.jsonl` 的，`g0.jsonl` 出现时别的
    可能还没写完 ⇒ 一看到目录就把文件复制走会拿到**半截**轨迹（而 `style-vector.py` 的对账
    会因此少几条决策）。所以要求"连续两次轮询的文件数与总字节数都不变"才复制。
    """
    rows = []
    staged: set[str] = set()
    seen: dict[str, tuple] = {}
    while not stop.is_set():
        for g in range(1, generations + 1):
            tag = f"{label}-g{g:02d}"
            if tag in staged:
                continue
            raw = DATA / "raw" / tag
            if not raw.is_dir():
                continue
            files = _traces_of(raw)
            if not files:
                continue
            shape = (len(files), sum(f.stat().st_size for f in files))
            if seen.get(tag) != shape:
                seen[tag] = shape
                continue                       # 这一轮只是"记下形状"，下一轮不变才算稳定
            dst = TRACES / tag
            info = _copy_once(raw, dst)
            copy_n = len(_traces_of(dst))
            row = {"tag": tag, "raw": str(raw), "staged": str(dst), "files": info["files"],
                   "bytes": info["bytes"], "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "generation": g, "copy_matches": (copy_n == info["files"])}
            rows.append(row)
            staged.add(tag)
            print(f"[stage] {tag}：{info['files']} 个轨迹文件 / {info['bytes'] / 2**20:.0f} MB "
                  f"→ {dst}（副本文件数 {copy_n}，一致={row['copy_matches']}）", flush=True)
        stop.wait(POLL_SECONDS)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="style-reward-run.py",
                                 description="风格奖励塑形受控实验的驱动器（跑一条臂 + 另存轨迹）")
    ap.add_argument("--arm", required=True, help="臂名（写进 manifest；例 a / b）")
    ap.add_argument("--label", required=True, help="季标签（= run-league.ps1 的 -Label）")
    ap.add_argument("--seed", type=int, required=True, help="显式季种子（= -Seed；见预注册 §3）")
    ap.add_argument("--generations", type=int, default=4)
    ap.add_argument("--games", type=int, default=1000)
    ap.add_argument("--style", default="def", help="本线风格名（= -Style）")
    ap.add_argument("--incumbent", required=True, metavar="NET.BIN",
                    help="起点权重（= -Incumbent；⛔ 不写就会用 `run-league.ps1` 的缺省现任 "
                         "`v4-league-g08` —— 那与预注册的起点不是同一个东西）")
    ap.add_argument("--style-pools", required=True, metavar="NAME=p1,p2;...",
                    help="各风格快照池（= -StylePools，原样透传）。⚠ **风格之间用分号**"
                         "（`def=...;atk=...;win=...`），每个风格里的路径用逗号")
    ap.add_argument("--style-bonus", default="", metavar="SPEC",
                    help="风格奖励塑形（空 = 对照臂）。经环境变量 `MAHJONG_STYLE_BONUS` 传给 "
                         "`v4 loop`（⛔ 本仓不许改 `tools/run-league.ps1`，环境变量是回路本来就支持的入口）")
    ap.add_argument("--style-whiten", default="student", choices=["student", "all"])
    ap.add_argument("--dry-run", action="store_true", help="只打印联赛命令，不跑")
    args = ap.parse_args(argv)

    STAGE.mkdir(parents=True, exist_ok=True)
    TRACES.mkdir(parents=True, exist_ok=True)
    log = STAGE / f"{args.label}-run.log"
    manifest_p = STAGE / f"{args.label}-manifest.json"

    env = dict(os.environ)
    env["MAHJONG_DATA_ROOT"] = str(DATA)
    env["MAHJONG_TRAINER"] = str(DATA / "tools" / "trainer" / "trainer.exe")
    env["PYTHONPATH"] = f"{REPO / 'python'};{REPO / 'python' / '.venv' / 'Lib' / 'site-packages'}"
    env["PYTHONIOENCODING"] = "utf-8"
    # ⚠ 风格奖励**只经这一个环境变量**进回路（`loop.py --style-bonus` 的缺省值读它）——
    #   这样 `tools/run-league.ps1` **一个字都不用改**（本仓的硬约束）。
    env["MAHJONG_STYLE_BONUS"] = args.style_bonus or ""
    env["MAHJONG_STYLE_WHITEN"] = args.style_whiten

    cmd = ["pwsh", "-NoProfile", "-File", str(REPO / "tools" / "run-league.ps1"),
           "-Label", args.label, "-Seed", str(args.seed),
           "-Generations", str(args.generations), "-Games", str(args.games),
           "-NoGate", "-Style", args.style, "-StylePools", args.style_pools,
           "-Incumbent", args.incumbent]
    print(f"[run] 臂 {args.arm}：{' '.join(cmd)}", flush=True)
    print(f"[run] MAHJONG_STYLE_BONUS={env['MAHJONG_STYLE_BONUS']!r} "
          f"MAHJONG_STYLE_WHITEN={env['MAHJONG_STYLE_WHITEN']!r}", flush=True)
    if args.dry_run:
        return 0

    stop = threading.Event()
    box: list = []
    watcher = threading.Thread(target=lambda: box.extend(watch(args.label, args.generations, stop)),
                               daemon=True)
    watcher.start()
    t0 = time.perf_counter()
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        p = subprocess.run(cmd, cwd=str(REPO), env=env, stdout=fh, stderr=subprocess.STDOUT)
    dt = time.perf_counter() - t0
    # 收尾再看一眼（联赛结束后 `raw/<tag>` 已被轮换删掉，但万一还在，别漏掉最后一代）
    time.sleep(QUIET_SECONDS)
    stop.set()
    watcher.join(timeout=30)
    rows = box
    manifest = {"arm": args.arm, "label": args.label, "seed": args.seed,
                "generations": args.generations, "games": args.games,
                "style": args.style, "style_pools": args.style_pools,
                "incumbent": args.incumbent,
                "style_bonus": args.style_bonus, "style_whiten": args.style_whiten,
                "returncode": int(p.returncode), "seconds": round(dt, 1),
                "log": str(log), "staged": rows}
    manifest_p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[run] 结束：退出码 {p.returncode}，用时 {dt / 60:.1f} 分钟；"
          f"另存 {len(rows)} 代轨迹；manifest = {manifest_p}", flush=True)
    if p.returncode != 0:
        print(f"⛔ 联赛退出码 {p.returncode} —— 看 {log} 的尾部：", file=sys.stderr)
        tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:]
        print("\n".join(tail), file=sys.stderr)
        return p.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
