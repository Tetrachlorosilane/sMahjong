#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判据 8（在线位移）v2 检查器 —— **可机检 + 可证伪**。

## 它检查什么（判据版本 `C8-v2`）

作废的旧判据（C8-v1，第一百零二轮裁定）：`KL(π_trained‖π_init) ≥ 0.1`（学生行、跑满步数）。
作废理由：**它把"离出生点有多远"当成了"在线"** —— 冻结数据 + 锚的 off-policy 漂移
（实测 0.1599）能过，而真正 on-policy 的一代（a− 自采 + `--init` == 数据策略）只有
0.0319 却判红；0.1 在真 on-policy 下等于要求单轮 1,200 步大幅偏离自己刚采的数据分布，
与 PPO 信任域语义冲突。

新判据（合取，全部可机检）：

- **主判据 M1**：`KL/100step` = **末 100 步窗口内每步 `KL(π_old‖π_new)` 的均值** ≥ `ε`
  （`ε = 0.020` nats/步；标定见 `lab/mv1/CRITERION8-REBUILD.md` §3）。
  ⚠ 这是**训练批次上的信任域位移**（`metrics.json` 的 `history[].train_parts.kl`，
  每个 epoch 一条 = 该轮所有步的均值），**不是**端点距离。
- **M2 跑满声明步数**：`len(history) × steps_per_epoch ≥ epochs × max_steps`（缺省口径）。
- **M3 无早停**：`stop_reason` 不得含"早停"（KL 早停或验证集早停都算没跑满）。
- **M4 未塌**：学生 `top1 ≥ 0.8 × init top1`（`--probe` 或 `--init-top1`）。
- **M5 `frac(u==0)`（LN 前）< 10%**：**仅 `arch=a-` 适用**（v4 没有 LN 前口径 ⇒ `n/a`）。
- **M6 `tile_id max|emb|` ≥ 0.01**：**仅 `arch=a-` 适用**；防"身份嵌入被压回 0 而 KL 仍大"
  （初值 std 0.02 ⇒ 0.01 = 半倍初值；实测 0.5666 = 56× 门槛）。
  读数优先从 `--net <net.bin>` 里**逐字节解析**（纯标准库，含 `params` 校验位），
  其次 `--probe` 的 `tile_id_max_abs_emb`，再次 `--tile-id-max-emb`。

**只报告、不作门槛**：`KL(π_t‖π_init)`（端点位移）、`argmax` 翻转、`KL/100step` 的 p50/min。

## fail-closed

用得到的腿必须算得出来：算不出来 ⇒ 该腿 `MISSING` ⇒ **整条 FAIL**（⛔ 不当"通过"）。
输入缺文件 / 解析失败 ⇒ 退出码 2。

## 用法

```
python tools/criterion8-check.py --run .tmp/mv1-s30/data-root/ckpt/mv1-s30-online
python tools/criterion8-check.py --run <dir|metrics.json> --probe .tmp/mv1-s30/probe-kl.json \
       --probe .tmp/mv1-s30/probe-crit1.json  --net .tmp/mv1-s30/mv1-s30-online.bin
python tools/criterion8-check.py --selftest
```

退出码：`0` = PASS · `1` = FAIL · `2` = 输入错误。
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ------------------------------------------------------------------ 判据常量

CRITERION = 8
VERSION = "C8-v2"
EPS = 0.020          # nats / 步（末窗均值）—— 标定见 CRITERION8-REBUILD.md §3
WINDOW = 100         # 末窗步数
TOP1_RATIO_MIN = 0.8
FRAC_U0_MAX = 0.10
TILE_ID_MIN = 0.01   # = 0.5 × 身份嵌入初值 std(0.02)
ARCH_A = "a-"
ARCH_V4 = "v4"


class InputError(RuntimeError):
    """输入不可用（缺文件 / 解析失败 / 字段缺失）—— 退出码 2。"""


# ------------------------------------------------------------------ 读 net.bin（纯标准库）
#
# 只读**格式 3**（`lab/mv1/ARCH-REDESIGN.md` §11.1）：
#   64 B 头 · arch 段（`archLen` u32 = 2,048）· 块表 · 张量表 · 尾部必须恰好用完。
# 为什么要自己解析：判据 M6 要 `tile_id` 的读数，而 checker 必须能在**没有 torch/numpy**
# 的解释器上跑（本机 bundled python 就没有 torch ⇒ 不能用 `v4.export.read_net`）。

NET_MAGIC = 0x4D4A4E4E       # "MJNN"
NET_FORMAT = 3
HEADER_BYTES = 64


@dataclass
class NetInfo:
    topo_id: int
    topo: str
    params_header: int
    params_actual: int
    tensors: dict = field(default_factory=dict)


def read_net(path: Path) -> NetInfo:
    raw = path.read_bytes()
    if len(raw) < HEADER_BYTES + 4:
        raise InputError(f"{path}: 太短（{len(raw)} B）")
    magic, fmt = struct.unpack_from("<2I", raw, 0)
    if magic != NET_MAGIC:
        raise InputError(f"{path}: 魔数不对 {magic:#x}（期望 {NET_MAGIC:#x}）")
    if fmt != NET_FORMAT:
        raise InputError(f"{path}: format={fmt}（本 checker 只认 format 3；1=v3、2=已退役）")
    (alen,) = struct.unpack_from("<I", raw, HEADER_BYTES)
    if not (96 <= alen <= 2048) or alen % 4:
        raise InputError(f"{path}: archLen={alen} 不合理")
    seg = raw[HEADER_BYTES:HEADER_BYTES + alen]
    (topo_id,) = struct.unpack_from("<H", seg, 6)
    n_blocks, n_tensors = struct.unpack_from("<2I", raw, 36)
    (params_header,) = struct.unpack_from("<I", raw, 60)
    for name, n in (("nBlocks", n_blocks), ("nTensors", n_tensors)):
        if n > 10_000:
            raise InputError(f"{path}: {name}={n} 越界")
    off = HEADER_BYTES + alen

    def need(n: int, what: str) -> None:
        nonlocal off
        if off + n > len(raw):
            raise InputError(f"{path}: 读 {what} 越界（{off}+{n} > {len(raw)}）")
        off += n

    need(0, "块表起点")
    for _ in range(n_blocks):
        if off + 2 > len(raw):
            raise InputError(f"{path}: 块表越界")
        (ln,) = struct.unpack_from("<H", raw, off)
        need(2 + ln + 4, "块条目")
    tensors: dict[str, tuple[tuple[int, ...], int, int]] = {}
    for i in range(n_tensors):
        if off + 2 > len(raw):
            raise InputError(f"{path}: 张量表越界")
        (ln,) = struct.unpack_from("<H", raw, off)
        need(2, f"第 {i} 个名字长度")
        if off + ln + 1 > len(raw):
            raise InputError(f"{path}: 第 {i} 个张量名字越界")
        name = raw[off:off + ln].decode("utf-8", "replace")
        need(ln, f"`{name}` 的名字")
        (nd,) = struct.unpack_from("<B", raw, off)
        need(1, f"`{name}` 的维数")
        if nd > 8:
            raise InputError(f"{path}: `{name}` 维数 {nd} 不合理")
        shape = []
        for _k in range(nd):
            (d,) = struct.unpack_from("<I", raw, off)
            shape.append(int(d))
            need(4, f"`{name}` 的形状")
        n = 1
        for d in shape:
            n *= d
        data_off = off
        need(4 * n, f"`{name}` 的数据")
        if name in tensors:
            raise InputError(f"{path}: 张量名重复 `{name}`")
        tensors[name] = (tuple(shape), data_off, n)
    if off != len(raw):
        raise InputError(f"{path}: 尾部多 {len(raw) - off} 字节（张量表与头/arch 段不符）")
    actual = sum(n for (_s, _o, n) in tensors.values())
    if actual != params_header:
        raise InputError(f"{path}: 头部 params={params_header:,} ≠ 实算 {actual:,}")
    return NetInfo(topo_id=int(topo_id), topo=("a-" if topo_id == 3 else
                                               "v4" if topo_id == 2 else f"topo{topo_id}"),
                   params_header=int(params_header), params_actual=int(actual), tensors=tensors)


def tensor_max_abs(net: NetInfo, raw: bytes, name: str) -> float:
    if name not in net.tensors:
        raise InputError(f"张量 `{name}` 不在 net.bin 里（arch={net.topo}）")
    shape, off, n = net.tensors[name]
    vals = struct.unpack_from(f"<{n}f", raw, off)
    return max(abs(v) for v in vals)


# ------------------------------------------------------------------ 读 run / probe

def load_json(path: Path) -> dict:
    """读 JSON（真实探针文件前面常有 warning 行 ⇒ 从第一个 `{` 起解析）。"""
    if not path.exists():
        raise InputError(f"缺文件：{path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    i = text.find("{")
    if i < 0:
        raise InputError(f"{path}: 找不到 JSON 对象")
    try:
        return json.loads(text[i:])
    except json.JSONDecodeError as e:
        raise InputError(f"{path}: JSON 解析失败（{e}）") from None


def find_metrics(path: Path) -> Path:
    if path.is_file():
        return path
    if not path.is_dir():
        raise InputError(f"既不是文件也不是目录：{path}")
    cand = path / "metrics.json"
    if cand.exists():
        return cand
    hits = sorted(path.glob("**/metrics.json"))
    if not hits:
        raise InputError(f"{path}: 目录里找不到 metrics.json")
    if len(hits) > 1:
        raise InputError(f"{path}: 有 {len(hits)} 份 metrics.json，请直接指到其中一份")
    return hits[0]


@dataclass
class Run:
    path: Path
    arch: str
    spe: int
    epochs_declared: int
    stop_reason: str
    kls: list[float]              # 每 epoch 的「每步 KL 均值」
    steps_recorded: int
    top1_final: float | None
    args: dict


def load_run(path: Path) -> Run:
    m = load_json(find_metrics(path))
    if not isinstance(m, dict):
        raise InputError("metrics.json 顶层不是对象")
    args = m.get("args") or {}
    hist = m.get("history")
    if not isinstance(hist, list) or not hist:
        raise InputError("metrics.json 缺 `history`（没有逐轮读数 ⇒ 判据 M1 无从谈起）")
    kls: list[float] = []
    for h in hist:
        tp = h.get("train_parts") or {}
        if tp.get("kl") is None:
            continue
        kls.append(float(tp["kl"]))
    if not kls:
        raise InputError("`history[].train_parts.kl` 一条都没有（不是 PPO 轮？）")
    spe = int(m.get("steps_per_epoch") or 0)
    if spe <= 0:
        raise InputError("`steps_per_epoch` 缺失/非正 ⇒ 无法定位末窗")
    arch = str(args.get("arch") or "")
    if not arch:
        # ⚠ `--arch` 是 A−（段 1）才加的旋钮 ⇒ 更早的 run 没有这个键，而那时**只有 v4**。
        # 缺键 ⇒ 记 v4（不是猜：a− 的训练一定写 `arch=a-`，见 `model.set_arch` 的进程级约定）。
        arch = ARCH_V4
    if arch not in (ARCH_V4, ARCH_A):
        raise InputError(f"`args.arch` = {arch!r} 不认识（只认 {ARCH_V4!r}/{ARCH_A!r}）")
    top1 = m.get("final_val_top1")
    if top1 is None:
        for h in reversed(hist):
            if h.get("val_top1") is not None:
                top1 = h["val_top1"]
                break
    return Run(path=find_metrics(path), arch=arch, spe=spe,
               epochs_declared=int(args.get("epochs") or 0),
               stop_reason=str(m.get("stop_reason") or ""), kls=kls,
               steps_recorded=spe * len(hist),
               top1_final=(None if top1 is None else float(top1)), args=args)


def kl_last_window(kls: list[float], spe: int, window: int = WINDOW) -> tuple[float, int]:
    """末窗 `window` 步内 **每步** KL 的均值。每个 epoch 的读数已经是该轮每步均值 ⇒
    取末尾 `ceil(window/spe)` 轮按步数加权。返回 (均值, 实际窗口步数)。"""
    need = max(1, (window + spe - 1) // spe)
    tail = kls[-need:]
    return sum(tail) / len(tail), spe * len(tail)


# ------------------------------------------------------------------ 判据

@dataclass
class Leg:
    key: str
    name: str
    ok: bool | None      # None = 不适用（n/a）
    reading: str
    note: str = ""


def check(run: Run, *, probes: list[dict], net: NetInfo | None, raw_net: bytes | None,
          init_top1: float | None, tile_id_override: float | None,
          frac_u_override: float | None, eps: float, window: int) -> tuple[list[Leg], dict]:
    merged: dict = {}
    for p in probes:
        for k, v in p.items():
            merged.setdefault(k, v)
    kl_last, w = kl_last_window(run.kls, run.spe, window)
    p50 = sorted(run.kls)[len(run.kls) // 2]
    legs: list[Leg] = []

    # ---- M1 主判据
    # ⚠ 末窗只是"最后一个 epoch 的均值"（steps_per_epoch == WINDOW 时）⇒ 必须同屏给出
    #    last / mean / p50 / min / 逐轮 ≥ε 的轮数，否则分类会随"用哪个统计量"翻转
    #    （实测 r4：last 0.0218 过、p50 0.0198 不过、仅 6/12 轮过 —— 见 lab/mv1/MISTAKES-2026-10.md 第 2 条）。
    _n = len(run.kls)
    _mean = sum(run.kls) / _n if _n else 0.0
    _n_ge = sum(1 for v in run.kls if v >= eps)
    legs.append(Leg("M1", f"KL/100step(末窗 W={w}) ≥ ε={eps:g}",
                    kl_last >= eps, f"{kl_last:.4f}",
                    f"last={kl_last:.4f} · mean={_mean:.4f} · p50={p50:.4f} · min={min(run.kls):.4f} · 逐轮≥ε {_n_ge}/{_n}"))

    # ---- M2 跑满声明步数
    declared = run.epochs_declared * run.spe
    if declared <= 0:
        legs.append(Leg("M2", "跑满声明步数", None, "n/a", "args.epochs 缺失 ⇒ 无法判"))
    else:
        legs.append(Leg("M2", f"跑满声明步数 {run.steps_recorded}/{declared}",
                        run.steps_recorded >= declared, f"{run.steps_recorded}/{declared}"))

    # ---- M3 无早停
    legs.append(Leg("M3", "无早停（stop_reason 不含「早停」）",
                    "早停" not in run.stop_reason, repr(run.stop_reason)))

    # ---- M4 未塌
    t1 = merged.get("top1_trained_student")
    i1 = merged.get("top1_init_student", init_top1)
    if t1 is None:
        t1 = run.top1_final
    if t1 is None or i1 in (None, 0):
        legs.append(Leg("M4", f"top1 ≥ {TOP1_RATIO_MIN}×init", False, "MISSING",
                        "缺 `top1_trained_student`/`top1_init_student`（--probe）或 --init-top1"))
    else:
        ratio = float(t1) / float(i1)
        legs.append(Leg("M4", f"top1 ≥ {TOP1_RATIO_MIN}×init",
                        ratio >= TOP1_RATIO_MIN, f"{float(t1):.4f}/{float(i1):.4f} = {ratio:.3f}"))

    if run.arch != ARCH_A:
        legs.append(Leg("M5", "frac(u==0) LN 前 < 10%", None, "n/a", "仅 arch=a- 适用"))
        legs.append(Leg("M6", f"tile_id max|emb| ≥ {TILE_ID_MIN:g}", None, "n/a",
                        "仅 arch=a- 适用（v4 没有身份嵌入）"))
    else:
        fu = merged.get("frac_u_zero_pre_ln", frac_u_override)
        if fu is None:
            legs.append(Leg("M5", f"frac(u==0) LN 前 < {FRAC_U0_MAX:.0%}", False, "MISSING",
                            "缺 `frac_u_zero_pre_ln`（--probe）或 --frac-u-zero"))
        else:
            legs.append(Leg("M5", f"frac(u==0) LN 前 < {FRAC_U0_MAX:.0%}",
                            float(fu) < FRAC_U0_MAX, f"{float(fu):.4f}"))
        emb = merged.get("tile_id_max_abs_emb", tile_id_override)
        src = "probe/cli"
        if net is not None and raw_net is not None and "tile.tile_id" in net.tensors:
            emb, src = tensor_max_abs(net, raw_net, "tile.tile_id"), "net.bin"
        if emb is None:
            legs.append(Leg("M6", f"tile_id max|emb| ≥ {TILE_ID_MIN:g}", False, "MISSING",
                            "缺 --net（含 tile.tile_id）/ probe / --tile-id-max-emb"))
        else:
            legs.append(Leg("M6", f"tile_id max|emb| ≥ {TILE_ID_MIN:g}",
                            float(emb) >= TILE_ID_MIN, f"{float(emb):.4f}", f"源={src}"))

    report = {
        "criterion": CRITERION,
        "version": VERSION,
        "arch": run.arch,
        "kl_per100_last": round(kl_last, 6),
        "kl_per100_window": w,
        "kl_per100_p50": round(p50, 6),
        "kl_per100_min": round(min(run.kls), 6),
        "kl_endpoint_vs_init": merged.get("kl_student_rows_t1"),
        "argmax_flip": (merged.get("kl_trained_vs_init") or {}).get("argmax_flip"),
        "steps": f"{run.steps_recorded}/{run.epochs_declared * run.spe}",
        "stop_reason": run.stop_reason,
        "eps": eps,
        "metrics": str(run.path),
    }
    return legs, report


def verdict(legs: list[Leg]) -> bool:
    return all(l.ok is True or l.ok is None for l in legs)


def print_result(legs: list[Leg], report: dict, passed: bool) -> None:
    print(f"CRITERION {CRITERION} version={VERSION} " + ("PASS" if passed else "FAIL"))
    print(f"  run={report['metrics']} · arch={report['arch']}")
    for l in legs:
        tag = "n/a " if l.ok is None else ("PASS" if l.ok else "FAIL")
        note = f"   [{l.note}]" if l.note else ""
        print(f"  {l.key} {tag} {l.name:34} = {l.reading}{note}")
    print(f"  report-only: KL(π_t‖π_init)={report['kl_endpoint_vs_init']} "
          f"· argmax_flip={report['argmax_flip']} · KL/100step p50={report['kl_per100_p50']} "
          f"min={report['kl_per100_min']} · ε={report['eps']:g} · steps={report['steps']}")


# ------------------------------------------------------------------ 自检（可证伪夹具）

def _hist(kls: list[float], val_top1: float | None = 0.7) -> list[dict]:
    return [{"epoch": i + 1, "train_parts": {"kl": k}, "val_top1": val_top1}
            for i, k in enumerate(kls)]


def selftest() -> int:
    """七条夹具：两条真在线/旧配方必须 PASS，五条必须 FAIL（含 fail-closed）。"""
    s30_kls = [0.0692, 0.0626, 0.0609, 0.0573, 0.0551, 0.0619,
               0.0508, 0.0504, 0.0578, 0.0392, 0.0369, 0.0456]
    cases = []

    def case(name, expect, arch, kls, epochs, stop="", probe=None, net=None, raw=None,
             init_top1=None, tile_id=None, frac_u=None):
        run = Run(path=Path("<selftest>"), arch=arch, spe=100, epochs_declared=epochs,
                  stop_reason=stop, kls=kls, steps_recorded=100 * len(kls),
                  top1_final=0.7, args={})
        legs, rep = check(run, probes=[probe or {}], net=net, raw_net=raw,
                          init_top1=init_top1, tile_id_override=tile_id,
                          frac_u_override=frac_u, eps=EPS, window=WINDOW)
        ok = verdict(legs) == expect
        cases.append((name, expect, verdict(legs), ok, legs))

    # ① 真在线（S30 读数）⇒ PASS
    case("S30-online", True, ARCH_A, s30_kls, 12,
         probe={"top1_trained_student": 0.725586, "top1_init_student": 0.747070,
                "frac_u_zero_pre_ln": 0.0, "tile_id_max_abs_emb": 0.5666})
    # ② 旧配方（L100：frozen data + 锚，off-policy 大幅漂移）⇒ 必须也 PASS
    case("old-recipe-L100", True, ARCH_V4, [0.2914], 1, init_top1=0.6307, probe={})
    # ③ 空跑 lr=0 ⇒ KL 恒 0 ⇒ 必须 FAIL（M1）
    case("null-lr0", False, ARCH_V4, [0.0] * 12, 12, init_top1=0.6307, probe={})
    # ④ lr/10（假设每步位移 ∝ lr）⇒ 显著变小且低于 ε ⇒ FAIL（M1）
    case("lr/10", False, ARCH_V4, [0.0029] * 12, 12, init_top1=0.6307, probe={})
    # ⑤ KL 早停 ⇒ FAIL（M2 + M3）
    case("early-stop", False, ARCH_V4, [0.2] * 8, 12, stop="KL 早停：step 737", init_top1=0.6307)
    # ⑥ 塌（top1 0.15 / init 0.63 = 0.24）⇒ FAIL（M4）
    case("collapse", False, ARCH_V4, [0.3] * 12, 12, init_top1=0.6307,
         probe={"top1_trained_student": 0.15, "top1_init_student": 0.6307})
    # ⑦ a− 缺 probe/net ⇒ fail-closed（M5/M6 MISSING）⇒ FAIL
    case("a--missing-probe", False, ARCH_A, s30_kls, 12,
         probe={"top1_trained_student": 0.72, "top1_init_student": 0.75})

    bad = [c for c in cases if not c[3]]
    for name, expect, got, ok, legs in cases:
        mark = "ok " if ok else "BAD"
        print(f"  [{mark}] {name:20} 期望 {'PASS' if expect else 'FAIL'} / 实得 "
              f"{'PASS' if got else 'FAIL'}")
    print(f"C8-v2 selftest：{len(cases) - len(bad)}/{len(cases)} "
          + ("PASS" if not bad else "FAIL"))
    return 0 if not bad else 1


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=True, description="判据 8（在线位移）v2 检查器")
    ap.add_argument("--run", type=Path, help="训练目录或 metrics.json")
    ap.add_argument("--net", type=Path, help="net.bin（算 tile_id max|emb|；需 format 3）")
    ap.add_argument("--probe", type=Path, action="append", default=[], help="验收探针 JSON（可多次）")
    ap.add_argument("--init-top1", type=float, default=None, help="init 的学生 top1（探针没记时）")
    ap.add_argument("--tile-id-max-emb", type=float, default=None, help="覆盖 M6 读数")
    ap.add_argument("--frac-u-zero", type=float, default=None, help="覆盖 M5 读数")
    ap.add_argument("--eps", type=float, default=EPS, help=f"主判据阈值（缺省 {EPS:g}）")
    ap.add_argument("--window", type=int, default=WINDOW, help=f"末窗步数（缺省 {WINDOW}）")
    ap.add_argument("--json", type=Path, default=None, help="把结果写成 JSON")
    ap.add_argument("--selftest", action="store_true", help="跑七条夹具")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.run:
        print("⛔ 需要 --run <dir|metrics.json>（或 --selftest）", file=sys.stderr)
        return 2
    try:
        run = load_run(a.run)
        probes = [load_json(p) for p in a.probe]
        net = raw = None
        if a.net:
            net = read_net(a.net)
            raw = a.net.read_bytes()
            if net.topo != run.arch:
                raise InputError(f"--net 的拓扑 {net.topo}（topoId={net.topo_id}）"
                                 f"与 run 的 arch={run.arch} 不一致（文件自相矛盾）")
        legs, report = check(run, probes=probes, net=net, raw_net=raw,
                             init_top1=a.init_top1, tile_id_override=a.tile_id_max_emb,
                             frac_u_override=a.frac_u_zero, eps=a.eps, window=a.window)
    except InputError as e:
        print(f"⛔ 输入错误：{e}", file=sys.stderr)
        return 2
    passed = verdict(legs)
    print_result(legs, report, passed)
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps({"verdict": "PASS" if passed else "FAIL", **report,
                                      "legs": [{"key": l.key, "name": l.name, "ok": l.ok,
                                                "reading": l.reading, "note": l.note}
                                               for l in legs]},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
