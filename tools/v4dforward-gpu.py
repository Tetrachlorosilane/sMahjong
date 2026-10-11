#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""评估/分析路径的 **GPU 等价替换**：`_p2-11c-dforward.py` 一族里的"前向"那一步。

## 它替换的是什么

`_p2-11c-dforward.py`（以及 `_p2-11d-*` 那些）的前向是这么做的：把 D 集合的决策行
**按场拷成 `stuD/<arm>__<cond>/g<场号>.jsonl`**，再 `subprocess` 起 `trainer.exe v4net`
（每 `--batch` 个文件一个进程，从 stdout 正则抠 `argmax=`），最后拿 argmax 与
`rows_<arm>.jsonl` 里的 `chosen_index` 比出**域内改判率**。

那一步的结构性代价（与 `tools/v4-forward-gpu.py` 的 docstring 同一份分析）：每批一次进程启动 +
一次权重加载 + 命令行长度的批量上限，而且**全在 CPU 上按行算**。

本脚本只替换那一步：**同一批行、同一份权重，改走 `tools/v4-forward-gpu.py` 的批量前向**
（CUDA 缺省），产出**同一种逐行文本**（`step=<s> n=<n> argmax=<i> logits=<…>`，与
`trainer v4net` 逐字符同格式 ⇒ 可以直接 `diff`），外加一个与 `_p2-11c-dforward.py`
**同名字段**的读数 JSON。

## ⛔ 两条红线

1. **不重实现特征，也不重实现前向**：张量与 logits 全部由 `tools/v4-forward-gpu.py`
   （`materialize` / `forward` / `write_out`）产出 —— 那是 R1 落地的**唯一一份** python 前向。
   本脚本只做"选行 + 对齐 + 记账"。
2. **不碰人家正在用的脚本**：`_p2-11c-dforward.py` / `_p2-11d-*` 一个字都不动；这是**新增**的
   等价入口，两个都还能跑，数字必须对得上（回归见 `NOTES.md` §6.5）。

## D 集合怎么选（`--select-from`）

原脚本把 D 行**拷**进 `stuD/`，那些拷贝**没有 sidecar**（`g<i>.feat.bin` 是逐文件的），
所以不能直接把 `stuD/` 当输入喂进来。本脚本改用**键对齐**：

    --traces <该臂的原始轨迹目录（带 sidecar）>  --select-from <stuD 目录>

`--select-from` 里每个 `g*.jsonl` 的 `(game, hand_no, step)` 三元组定义 D 集合，本脚本按**同一
三元组**从 `--traces` 里挑行 —— 比原脚本的"按下标对齐"更紧（原脚本只比行数）。缺 `--select-from`
时就是对整个 `--traces`（可再用 `--where-kind` / `--policy` / `--limit` 缩）。

⚠ **对齐闸门**：枚举出来的行数必须等于 `materialize` 的 `rows` 行数，且逐行 `step` 必须与
`--select-from` 里的对得上 —— 对不上**当场报错**，不"看着差不多就往下算"。

退出码：0 通过 / 1 判据不过 / 2 用法、环境或输入坏了。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.dont_write_bytecode = True          # ⛔ 别在 `tools/` 下留 `__pycache__/`（会弄脏 git status）
sys.path.insert(0, str(REPO / "python"))

EXIT_OK, EXIT_MISMATCH, EXIT_USAGE = 0, 1, 2
_TOOL = REPO / "tools" / "v4-forward-gpu.py"


def load_tool():
    """按路径加载 `tools/v4-forward-gpu.py`（文件名带 `-`，不能直接 import）。

    ⚠ 必须 `sys.modules[...] = mod`：那个模块里有 `@dataclass`，它要靠 `cls.__module__`
    反查模块字典，不注册会 `AttributeError: 'NoneType' object has no attribute '__dict__'`。
    """
    spec = importlib.util.spec_from_file_location("v4_forward_gpu", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v4_forward_gpu"] = mod
    spec.loader.exec_module(mod)
    return mod


def iter_meta(traces: Path, files) -> list[dict]:
    """按 `materialize` 的**同一顺序**枚举决策行的元数据（只读 jsonl，⛔ 不碰特征）。

    顺序口径与 `materialize` 逐字对齐：`trace_files()` 的返回序 → 文件内行序 → 只在
    `type == "decision"` 上计数。这里多读几个字段（game/hand_no/step/chosen/chosen_index），
    它们正是"域内改判率"要的账。
    """
    out: list[dict] = []
    for f in files:
        for ln, line in enumerate(f.open(encoding="utf-8"), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "decision":
                continue
            obs = row.get("obs") or {}
            out.append({"file": f.name, "line": ln,
                        "game": int(row.get("game", -1)), "hand_no": int(row.get("hand_no", -1)),
                        "step": int(row.get("step", -1)), "chosen": row.get("chosen"),
                        "chosen_index": row.get("chosen_index"),
                        "kind": str(obs.get("kind")),
                        "draw_band": row.get("draw_band"), "riichi_band": row.get("riichi_band"),
                        "obs_hash": row.get("obs_hash")})
    return out


def key_of(m: dict) -> tuple:
    return (m["game"], m["hand_no"], m["step"])


def _gnum(p: Path) -> int:
    """`g<序号>.jsonl` → 序号（与 `traces.trace_files` 的排序口径逐字相同）。"""
    s = p.stem[1:]
    return int(s) if s.isdigit() else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="v4dforward-gpu.py",
        description="评估路径的 GPU 等价替换：只做选行/对齐/记账，前向走 tools/v4-forward-gpu.py")
    ap.add_argument("--traces", required=True, help="该臂的原始轨迹目录（每个 g*.jsonl 要有同名 .feat.bin）")
    ap.add_argument("--weights", required=True, help="替换网 B 的 net.bin")
    ap.add_argument("--select-from", default="",
                    help="D 集合：一个目录的 g*.jsonl，其 (game,hand_no,step) 定义要前向的行")
    ap.add_argument("--where-kind", default=None, help="无 --select-from 时按 obs.kind 筛（透传 materialize）")
    ap.add_argument("--policy", default=None, help="无 --select-from 时按策略串筛（逐字相等）")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--device", default="cuda", help="cuda（缺省）/ cpu")
    ap.add_argument("--batch", type=int, default=512)
    ap.add_argument("--threads", type=int, default=4, help="torch CPU 线程数（缺省 4：本机一次一个重负载）")
    ap.add_argument("--tol", type=float, default=None,
                    help="与参考 logits 比的**相对**容差；缺省取 v4-forward-gpu.py 的 TOL_REL")
    ap.add_argument("--arm", default="", help="写进读数 JSON 的标签（缺省从 --select-from 目录名解析）")
    ap.add_argument("--cond", default="", help="同上")
    ap.add_argument("--out", default=None, help="逐行文本（cpp 格式，与 trainer v4net 逐字符同格式）")
    ap.add_argument("--out-json", default=None, help="读数 JSON（字段名与 _p2-11c-dforward.py 对齐）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    tool = load_tool()
    tool.pin_threads(a.threads)
    traces = Path(a.traces)
    from mahjong_ml.v4 import traces as _vt          # ⛔ 只借它的"文件序"，不碰特征
    files = _vt.trace_files(traces)
    if not files:
        print(f"[v4dforward] ⛔ {traces} 里没有 g*.jsonl", file=sys.stderr)
        return EXIT_USAGE

    meta = iter_meta(traces, files)

    # ---- 选行：D 集合（键对齐）或全量（可选 where-kind/policy/limit）----------------------
    if a.select_from:
        sd = Path(a.select_from)
        sel_files = sorted(sd.glob("g*.jsonl"), key=_gnum)
        if not sel_files:
            print(f"[v4dforward] ⛔ {sd} 里没有 g*.jsonl", file=sys.stderr)
            return EXIT_USAGE
        want, want_steps = set(), []
        for f in sel_files:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("type") != "decision":
                    continue
                k = (int(r.get("game", -1)), int(r.get("hand_no", -1)), int(r.get("step", -1)))
                want.add(k)
                want_steps.append(k)
        if len(want) != len(want_steps):
            print(f"[v4dforward] ⛔ --select-from 里有重复键（{len(want_steps)} 条 → {len(want)} 个唯一）："
                  f"键对齐会少喂行，先查那份拷贝", file=sys.stderr)
            return EXIT_USAGE
        by_key = {key_of(m): i for i, m in enumerate(meta)}
        missing = [k for k in want_steps if k not in by_key]
        if missing:
            print(f"[v4dforward] ⛔ --select-from 里 {len(missing)} 个键在 --traces 里找不到"
                  f"（前 3 个：{missing[:3]}）—— 两份语料不是同源", file=sys.stderr)
            return EXIT_USAGE
        # ⚠ **行序必须是 `trace_files` 的规范序**（`g<序号>` 按**数值**排 + 文件内行序），
        #   不是 `glob` 的字典序：`glob` 给出 g0,g1,g10,…,g19,g2,… —— 而 `_p2-11c-dforward.py`
        #   是按 game 升序把拷贝写成 `g<场号>.jsonl` 再喂 v4net 的。顺序错了**不会报错**，
        #   只会让"逐行 diff"整片错位（实测 3,037 行里 2,664 行 step 对不上，而两边的
        #   `flip_rows_D` 都是 357 —— 即"每行的答案都对，只是排在不同的行上"）。
        sel = sorted(by_key[k] for k in want_steps)
        if {key_of(meta[i]) for i in sel} != want:
            print(f"[v4dforward] ⛔ 对齐失败：选中的 {len(sel)} 行与 D 集合不是同一批键", file=sys.stderr)
            return EXIT_USAGE
        label = a.arm, a.cond
        if not a.arm and not a.cond:
            parts = sd.name.split("__")
            label = (parts[0], parts[1] if len(parts) > 1 else "")
        rows_all = _materialize_all(tool, traces, a)
        if int(rows_all.tile.shape[0]) != len(meta):
            print(f"[v4dforward] ⛔ 对齐闸门：枚举 {len(meta)} 行 vs materialize "
                  f"{int(rows_all.tile.shape[0])} 行 —— 两份枚举口径不一致，先查代码", file=sys.stderr)
            return EXIT_USAGE
    else:
        rows_all = _materialize_all(tool, traces, a)
        if int(rows_all.tile.shape[0]) != len(meta):
            print(f"[v4dforward] ⛔ 对齐闸门：枚举 {len(meta)} 行 vs materialize "
                  f"{int(rows_all.tile.shape[0])} 行", file=sys.stderr)
            return EXIT_USAGE
        sel = [i for i, m in enumerate(meta)
               if (a.where_kind is None or m["kind"] == a.where_kind)
               and (a.limit <= 0 or i < a.limit)]
        label = (a.arm or traces.name, a.cond or (f"kind={a.where_kind}" if a.where_kind else "*"))
    if not sel:
        print("[v4dforward] ⛔ 选中的行是空的", file=sys.stderr)
        return EXIT_USAGE

    tol = tool.TOL_REL if a.tol is None else a.tol
    try:
        device = tool.resolve_device(a.device)
        net = tool.load_net(Path(a.weights), device, tmp=None)
        tool._check_rows(rows_all, net.dims)
        res = tool.forward(net, rows_all, device, a.batch, index=sel, quiet=a.quiet)
    except tool.InputError as e:
        print(f"[v4dforward] ⛔ {e}", file=sys.stderr)
        return EXIT_USAGE

    tool.write_out(Path(a.out) if a.out else None, res, "cpp",
                   steps=[meta[i]["step"] for i in sel])

    # ---- 记账：与 `_p2-11c-dforward.py` 同名字段 ----------------------------------------
    flip, no_ci = [], 0
    by_band: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for j, i in enumerate(sel):
        m = meta[i]
        ci = m["chosen_index"]
        if ci is None:
            no_ci += 1
            continue
        band = (f"draw{m['draw_band']}_riichi{m['riichi_band']}"
                if m["draw_band"] is not None and m["riichi_band"] is not None else "all")
        by_band[band][0] += 1
        if res.argmax[j] != int(ci):
            flip.append({"row": i, "file": m["file"], "game": m["game"],
                         "hand_no": m["hand_no"], "step": m["step"], "kind": m["kind"],
                         "argmax_B": res.argmax[j], "chosen_index": int(ci), "chosen": m["chosen"]})
            by_band[band][1] += 1
    rep = {
        "arm": label[0], "cond": label[1], "net_b": a.weights, "device": a.device,
        "n_rows_all": len(meta), "n_D": len(sel), "n_D_full": len(sel), "sampled": False, "stride": 1,
        "D_row_share": len(sel) / len(meta),
        "flip_rows_D": len(flip), "flip_rate_D": len(flip) / len(sel),
        "n_missing_chosen_index": no_ci,
        "band": {k: {"n": v[0], "flip": v[1], "rate": (v[1] / v[0]) if v[0] else 0.0}
                 for k, v in sorted(by_band.items())},
        "flip_sample": flip[:5],
        "tol_rel": tol,
    }
    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.out_json:
        Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out_json).write_text(text, encoding="utf-8")
    if not a.quiet:
        print(text)
        print(f"[v4dforward:{label[0]}/{label[1]}] 域覆盖 {len(sel)}/{len(meta)} = "
              f"{rep['D_row_share'] * 100:.2f}% · 前向 {len(sel)} 行 · 域内改判 {len(flip)} 行 = "
              f"{rep['flip_rate_D'] * 100:.3f}% · device={a.device}"
              + (f"（{no_ci} 行没有 chosen_index，未计入）" if no_ci else ""), file=sys.stderr)
    return EXIT_OK


def _materialize_all(tool, traces: Path, a):
    """把 `--traces` 的**全部**决策行物化成张量（行序 == `iter_meta` 的枚举序 ⇒ 下标对得上）。

    ⚠ `--select-from` 存在时**必须**把 `policy/where_kind/limit` 全部关掉：那三个都会让
    `materialize` 少读行，而下标是按"全量枚举"算的 —— 少一行就整体错位（而且不报错）。
    """
    if a.select_from:
        return tool.materialize(traces, policy=None, where_kind=None, limit=0, rows_out=None,
                                dump_selected=None, ablate="", quiet=True)
    return tool.materialize(traces, policy=a.policy, where_kind=a.where_kind, limit=a.limit,
                            rows_out=None, dump_selected=None, ablate="", quiet=True)


if __name__ == "__main__":
    raise SystemExit(main())
