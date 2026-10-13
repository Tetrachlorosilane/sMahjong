# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""实验 A · 第 2 步：对**同一批学生席决策行**跑两条臂的 v4 前向（`trainer.exe v4net`）。

为什么这样做：分歧指示量按定义是 `argmax_a1(obs) != argmax_a2(obs)` —— 它是**策略 × 公开观测的
确定函数**。把同一条 obs 喂给两个 net，就得到"同一状态下两臂各自会做什么"，与牌山运气无关。
（⛔ 不做第二个实现：前向只走仓库自带的 `trainer.exe v4net`，与 `tools/V4Probe.java` 逐字符对拍过。）

产出 `<work>/logits_<arm>_<tag>.jsonl`，逐行 `{"i":全局行号,"step":…,"n":…,"argmax":…}`。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

LINE = re.compile(r"^step=(-?\d+) n=(\d+) argmax=(-?\d+) logits=")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_expA-logits.py")
    ap.add_argument("--work", required=True)
    ap.add_argument("--arm", required=True)
    ap.add_argument("--trainer", required=True)
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--net", action="append", required=True, help="--net tag=path（可重复）")
    a = ap.parse_args(argv)

    work = Path(a.work)
    src = work / "stu" / a.arm
    files = sorted((p for p in src.iterdir() if p.suffix == ".jsonl"),
                   key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0)
    if not files:
        raise SystemExit(f"⛔ 没有学生席行文件：{src}")

    for spec in a.net:
        tag, _, net = spec.partition("=")
        if not Path(net).is_file():
            raise SystemExit(f"⛔ 权重不存在：{net}")
        out = work / f"logits_{a.arm}_{tag}.jsonl"
        i = 0
        with out.open("w", encoding="utf-8") as fh:
            for b0 in range(0, len(files), a.batch):
                batch = files[b0:b0 + a.batch]
                p = subprocess.run([a.trainer, "v4net", net] + [str(x) for x in batch],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   cwd=str(Path(a.trainer).parent))
                if p.returncode != 0:
                    raise SystemExit(f"⛔ v4net 失败（退出码 {p.returncode}）tag={tag} 批次 {b0}")
                txt = p.stdout.decode("utf-8", "replace")
                got = 0
                for ln in txt.splitlines():
                    m = LINE.match(ln)
                    if not m:
                        continue
                    fh.write(json.dumps({"i": i, "step": int(m.group(1)), "n": int(m.group(2)),
                                         "argmax": int(m.group(3))}) + "\n")
                    i += 1
                    got += 1
                if got != len(batch) and got == 0:
                    raise SystemExit(f"⛔ 批次 {b0} 一条 logits 都没解出来（tag={tag}）")
        print(f"[logits:{a.arm}:{tag}] rows={i} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
