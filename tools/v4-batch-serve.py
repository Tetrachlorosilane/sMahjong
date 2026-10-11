#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""v4 前向的**确定批（deterministic batch）跨局推理**入口 —— argmax 型自对弈上 GPU 的"前向那一格"。

它不替代 `tools/v4-forward-gpu.py`（前向本体在那里），只补**它在跨局场景下缺的那一件**：
**批组成可复现**。规则/特征仍在 CPU（C++ `selfplay` 既有实现），这里只换前向的执行后端；
输入只消费**已经算好的**行级张量（`tile/evt/ctx/cand/nlegal[/h0]`），⛔ **绝不重实现特征**。

## 为什么"批组成"比"批大小"重要（这是本工具存在的唯一理由）

普查（`S:\\mahjong-training\\w-ladder\\GPU-SELFPLAY-FEASIBILITY.md`）：C++ 逐行 vs GPU 批在
**121,120 行上 argmax 翻转 0**、maxΔ 9.2e-5；但 0 观测 ≠ 0 概率（95% 上界 ≈ **2.5e-5/决策**）。
⇒ "同种子 ⇒ 逐字节相同"这条**必须退役**，换上来的是"**同种子 + 同场次集合 ⇒ 同一批的成员与顺序
完全相同**"。批若由线程完成时刻决定，同一 seed 两次跑出不同批 ⇒ 域掩码"域外逐字节"、
`--workers` 对拍、配对读数**全部失效**。所以本工具把"批"当成**输入的函数**：

    batch(b) = 规范序里的第 [b·K, (b+1)·K) 行    —— 只依赖键表集合，与到达顺序/线程数无关

## 规范序（唯一的排序键）

键 = `(game, step, seat)`：`game` = **场号**（0 基，= 轨迹文件名 `g<N>.jsonl` 的 N）·
`step` = **该场内的决策序号**（0 基、单调、跨小局连续 ⇒ 已含 `hand_no` 的先后）· `seat` = **座位**。
按 `(game, step, seat)` **字典序**排成一个全序（`seat` 在本仓数据里是冗余项，写出来是为了让键**自解释**、
也为了将来"一场内多座位并行决策"时不用改键）。
⛔ **要求全序唯一**：`(game, step)` 有重复行就**报错**（`规范序不成立`）—— 不做"再拿行号兜底"，
那种兜底会把"到达顺序"偷偷放回排序里。键表由 `--keys-from-traces`（只读 `game/step/seat` 三个
字段，**不碰 obs/特征**）或 `--keys <tsv>` 给出。

## K 与尾批（边界行为写死，不留"看情况"）

| 情况 | 行为 |
| --- | --- |
| `K ≥ 行数` | **一批放全部**（`batches=1`），manifest 里记 `note` |
| `K = 1` | **逐行批**（`batches=N`）= 与逐行版逐批同构，用于对拍同一份代码 |
| `行数 % K != 0` | `--tail keep`（缺省）= 尾批短、**照算**；`drop` = 丢掉余数（记 `dropped`）；`error` = 报错 |
| `K < 1` | 报错 |

⚠ **K 组 ≠ 数学批**：K 组之内，torch 仍按 `legal` 条数分桶（同 `v4-forward-gpu.py` 的分桶理由：
补 0 会经 `u.mean` → `policy_gate` 改 logits，与逐行语义**不等价**）。K 是**组成确定性**的单位，
数学批是 K 组里 `nlegal` 相同的极大段 —— 这条写在这里免得被误读成"K=512 就一次 GEMM 512 行"。

## 用法

    # ① 轨迹 → 规范键表（只读 game/step/seat；⛔ 不读 obs）
    python tools\\v4-batch-serve.py --keys-from-traces <轨迹目录> --keys-out keys.tsv
    # ② 确定批推理（GPU，K=512）
    python tools\\v4-batch-serve.py --weights <net.bin> --rows rows.npz \\
        --keys-from-traces <轨迹目录> --batch 512 --device cuda --out logits.jsonl --manifest plan.json
    # ③ 逐行臂（同一份代码，K=1，CPU）：批版 vs 逐行版的对拍
    python tools\\v4-batch-serve.py … --batch 1 --device cpu --out rows.jsonl
    # ④ 与逐行参考（`trainer v4net` 的 stdout）对拍 + 近边界量化（判据①②⑤）
    python tools\\v4-batch-serve.py … --batch 512 --device cuda --out gpu.jsonl \\
        --compare cpp-v4net.txt --margins
    # ⑤ 自检（四条，含负向）
    python tools\\v4-batch-serve.py --selftest

退出码：0 = 通过 / 1 = 判据不过（`--compare` 有翻转或超容差）/ 2 = 用法、环境、输入或**规范序**坏了。

⚠ 临时产物一律落仓库内 `python\\.tmp\\`（`.venv` 在沙箱里不能在工作区外建目录）。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
#: ⛔ 关掉字节码缓存：在 `tools/` 下留 `__pycache__/` 会把 `git status` 弄脏（同 `v4-forward-gpu.py`）。
sys.dont_write_bytecode = True
sys.path.insert(0, str(REPO / "python"))

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_USAGE = 2

#: 规范序的排序键（**唯一**的一份定义；换键就换这里一处）。
SORT_KEY = ("game", "step", "seat")


class PlanError(RuntimeError):
    """规范序 / 批计划坏了 —— 一律**报错**，绝不"看着差不多就往下算"。"""


def load_forward_tool():
    """按路径加载 `tools/v4-forward-gpu.py`（文件名带 `-`，不能 `import`）。

    前向本体（载权重 / 读张量 / 分桶批量 / 设备解析）**只有那一份**；本工具不复制它的任何一行。
    """
    p = REPO / "tools" / "v4-forward-gpu.py"
    spec = importlib.util.spec_from_file_location("v4_forward_gpu", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["v4_forward_gpu"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 键表

@dataclass(order=True)
class Key:
    """一行决策的**规范键** + 它在行级张量里的下标 `row`。

    `order=True` ⇒ dataclass 的字段序**就是**比较序 —— 字段声明顺序必须与 `SORT_KEY` 一致
    （`selftest` 里有断言，免得将来加字段时把序改掉却没人发现）。
    """

    game: int
    step: int
    seat: int
    row: int = field(compare=False)

    def tup(self) -> tuple[int, int, int]:
        return (self.game, self.step, self.seat)


def load_keys_traces(directory: Path, *, policy: str | None, where_kind: str | None,
                     limit: int = 0, quiet: bool = False) -> list[Key]:
    """轨迹目录 → 键表，**行序与 `v4-forward-gpu.py --traces` 的物化行序逐行一致**。

    ⚠ 三个必须镜像的地方（差一个就整片错位，而"每行的答案都对、只是排在不同行上"这种错
    **只看汇总数发现不了**，R2 已经踩过一次）：
    ① 文件序 = `v4traces.trace_files`（**数值序**，不是字典序）；
    ② 只取 `type == "decision"` 的行，且**筛选项与 `materialize` 同一套**（`policy` / `where_kind`）；
    ③ 行号只在**选中**的行上自增（未选中的行不占行号），`limit` 同样在选中数上封顶。

    这里**只读** `type/game/step/seat` 四个字段 —— ⛔ 不读 `obs`、不做特征（那是 `materialize` 的事）。
    """
    from mahjong_ml.v4 import traces as v4traces

    out: list[Key] = []
    for f in v4traces.trace_files(directory):
        with f.open(encoding="utf-8") as fh:
            for ln, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("type") != "decision":
                    continue
                if policy is not None and row.get("policy") != policy:
                    continue
                obs = row.get("obs")
                if not isinstance(obs, dict):
                    raise PlanError(f"{f.name}:{ln} 决策行没有 obs（键表要求 obs 与物化同口径）")
                if where_kind is not None and str(obs.get("kind")) != where_kind:
                    continue
                for k in SORT_KEY:
                    if k not in row:
                        raise PlanError(f"{f.name}:{ln} 决策行缺字段 {k!r}（规范序的键必须齐全）")
                out.append(Key(int(row["game"]), int(row["step"]), int(row["seat"]), len(out)))
                if limit and len(out) >= limit:
                    break
        if limit and len(out) >= limit:
            break
    if not out:
        raise PlanError(f"{directory} 里没有选中的 decision 行（规范序无从谈起）")
    if not quiet:
        print(f"[keys] {directory.name}：{len(out):,} 行 · 场 {min(k.game for k in out)}.."
              f"{max(k.game for k in out)} · step 0..{max(k.step for k in out)} · "
              f"座位 {sorted({k.seat for k in out})}", file=sys.stderr)
    return out


def write_keys(path: Path, keys: list[Key]) -> None:
    """键表落盘（TSV：`row<TAB>game<TAB>step<TAB>seat`）—— 让"批组成"可复算、可审计。"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines = ["row\tgame\tstep\tseat\n"]
    lines += [f"{k.row}\t{k.game}\t{k.step}\t{k.seat}\n" for k in sorted(keys, key=lambda k: k.row)]
    Path(path).write_text("".join(lines), encoding="utf-8", newline="\n")


def read_keys(path: Path) -> list[Key]:
    """读回键表（`--keys`）：格式与 `write_keys` 一致，坏形状**报错**。"""
    out: list[Key] = []
    text = Path(path).read_text(encoding="utf-8")
    for ln, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.startswith("row\t"):
            continue
        parts = line.split("\t")
        if len(parts) != 4:
            raise PlanError(f"{Path(path).name}:{ln} 键表应有 4 列（row/game/step/seat），"
                            f"实际 {len(parts)} 列")
        try:
            r, g, s, seat = (int(x) for x in parts)
        except ValueError as e:
            raise PlanError(f"{Path(path).name}:{ln} 键表里有非整数：{line!r}") from e
        out.append(Key(g, s, seat, r))
    if not out:
        raise PlanError(f"{path} 是空的键表")
    return out


# ---------------------------------------------------------------- 规范序 → 批计划

def canonical_order(keys: list[Key]) -> list[Key]:
    """键表 → **规范序**（按 `(game, step, seat)` 字典序）。唯一性在这里硬校验。

    ⛔ 不拿 `row` 当兜底 tiebreak：键重复就说明"这条轨迹的键不是一个全序"，
    此时任何兜底都会把**到达顺序**偷偷放回排序里（正是本工具要消灭的东西）。
    """
    seen: dict[tuple[int, int], int] = {}
    for k in keys:
        prev = seen.get((k.game, k.step))
        if prev is not None:
            raise PlanError(f"规范序不成立：场 {k.game} 的 step={k.step} 有两行"
                            f"（row {prev} 与 row {k.row}）—— 键必须唯一，⛔ 不用 row 兜底")
        seen[(k.game, k.step)] = k.row
    return sorted(keys, key=lambda k: k.tup())


def check_rows_alignment(keys: list[Key], n_rows: int, *, allow_subset: bool) -> tuple[int, int]:
    """键表 ↔ 行级张量的**行对齐**闸门（错位一律报错，返回 `(已覆盖, 未覆盖)`）。

    四类错都要拦：① `row` 越界；② `row` 重复（两行抢同一格）；③ 没覆盖满（缺行 = 那张量的
    某几行**不会被前向**，而"没报错"会让它看起来像跑完了）；④ `row` 不连续（洞）。
    """
    if n_rows <= 0:
        raise PlanError("行级张量一行都没有")
    got: set[int] = set()
    for k in keys:
        if k.row < 0 or k.row >= n_rows:
            raise PlanError(f"行对齐错位：键表里的 row={k.row} 越界（张量只有 {n_rows} 行）")
        if k.row in got:
            raise PlanError(f"行对齐错位：row={k.row} 在键表里出现两次")
        got.add(k.row)
    missing = sorted(set(range(n_rows)) - got)
    if missing and not allow_subset:
        raise PlanError(f"行对齐错位：键表只覆盖 {len(got)}/{n_rows} 行，缺 {len(missing)} 行"
                        f"（前几个：{missing[:5]}）—— 要故意只算一部分就显式给 --allow-subset")
    return len(got), len(missing)


def plan_batches(order: list[Key], k: int, tail: str) -> dict:
    """规范序 → **固定大小 K 的批计划**（纯函数：同样的 `order`+`K`+`tail` ⇒ 同样的计划）。

    返回 `{"batches": [[Key…]…], "note": str, "dropped": int}`；边界行为见模块 docstring 的表。
    """
    n = len(order)
    if k < 1:
        raise PlanError(f"--batch 必须 ≥ 1，收到 {k}")
    if tail not in ("keep", "drop", "error"):
        raise PlanError(f"--tail 只认 keep / drop / error，收到 {tail!r}")
    if k >= n:
        return {"batches": [list(order)], "dropped": 0,
                "note": f"K={k} ≥ 行数 {n} ⇒ 单批放全部（不是错误，但确定性只到「整批」这一级）"}
    rem = n % k
    full = n // k
    if rem and tail == "error":
        raise PlanError(f"行数 {n} 不能被 K={k} 整除（余 {rem}）：--tail error 下这是错误")
    if rem and tail == "drop":
        return {"batches": [order[i * k:(i + 1) * k] for i in range(full)],
                "dropped": rem, "note": f"尾批 {rem} 行按 --tail drop 丢弃（已记账）"}
    batches = [order[i * k:(i + 1) * k] for i in range(full)]
    if rem:
        batches.append(order[full * k:])
    return {"batches": batches, "dropped": 0,
            "note": "尾批短" if rem else "整除"}


def assert_canonical(plan: dict, order: list[Key], k: int) -> None:
    """**提交前的守卫**：批计划的排放必须**逐行等于规范序**（这就是"确定批"那条判据的活口）。

    乱序（批内乱序、批间乱序、尾批插到前面、批大小不是 K）**一律报错**，且必须在**前向之前**炸
    —— 否则"算完了才发现批是乱的"等于既烧了 GPU 又拿不到可信读数。

    ⚠ `--tail drop` 时计划只服务规范序的**前缀**（丢的是尾巴，且 `plan_batches` 已记账）
    ⇒ 判据是"**等于规范序的前缀**"，不是"逐行等于整条规范序"（本工具第一版就是这么把
    `--tail drop` 误判成红的）。
    """
    flat: list[Key] = []
    for b, batch in enumerate(plan["batches"]):
        if not batch:
            raise PlanError(f"批 {b} 是空的（空批不该出现在计划里）")
        if b < len(plan["batches"]) - 1 and len(batch) != k:
            raise PlanError(f"批 {b} 有 {len(batch)} 行 != K={k}（只有**最后一批**可以短"
                            f" ⇒ 这一批被放到了非尾位置，批序被破坏）")
        flat.extend(batch)
    if len(flat) > len(order):
        raise PlanError(f"批计划有 {len(flat)} 行 > 规范序 {len(order)} 行（多了不该有的行）")
    want = order[:len(flat)]
    if [x.row for x in flat] != [x.row for x in want]:
        bad = next((i for i, (a, b) in enumerate(zip(flat, want)) if a.row != b.row), 0)
        raise PlanError(f"批计划不是规范序：第 {bad} 位是 row {flat[bad].row}，"
                        f"规范序要求 row {want[bad].row} —— ⛔ 乱序的批**不许提交**")
    for b, batch in enumerate(plan["batches"]):
        if [x.tup() for x in batch] != sorted(x.tup() for x in batch):
            raise PlanError(f"批 {b} 内部不是规范序（成员顺序被打乱）")


def plan_hash(plan: dict) -> str:
    """批计划的指纹（sha256）—— "同一输入两次跑出同一批"就是比它。"""
    h = hashlib.sha256()
    for b, batch in enumerate(plan["batches"]):
        for pos, key in enumerate(batch):
            h.update(f"{b}\t{pos}\t{key.row}\t{key.game}\t{key.step}\t{key.seat}\n".encode())
    return h.hexdigest()


# ---------------------------------------------------------------- 前向（逐批，行对齐）

@dataclass
class BatchResult:
    """逐行结果 + 批记账（**行对齐**是这里的硬约束：输出的第 i 条 = `row i` 的答案）。"""

    argmax: list[int]
    logits: list[list[float]]
    n: list[int]
    batch_of: list[int]
    pos_of: list[int]
    seconds: float
    per_batch: list[dict]
    #: **真正被前向的行数**（= 批计划里所有批的成员数之和）。⚠ 不能拿 `len(argmax)` 顶替：
    #: `--allow-subset` 时 `len(argmax)` 是整个张量的行数（没被服务的行也在里面），
    #: 于是吞吐会被系统性放大（本工具第一版就犯了这个错：20k 子集报出 27,745 行/秒）。
    served: int = 0

    def rows_per_sec(self) -> float:
        return (self.served or len(self.argmax)) / max(self.seconds, 1e-9)


def forward_plan(tool, net, rows, device, plan: dict, *, quiet: bool) -> BatchResult:
    """按**批计划给定的批序**逐批前向；结果按 `row` 回填成行对齐的数组。

    ⚠ 用 `pos[row] = 行号`**回填**而不是"按批序 append"：批是按规范序跑的，而输出要按**行号**
    对齐（"逐行消费"的那条链路读的是第 k 行）。顺序敏感的地方只留一处（记账），其余全按 row 索引。
    """
    n_rows = int(rows.tile.shape[0])
    served = sum(len(b) for b in plan["batches"])
    out_arg = [0] * n_rows
    out_log: list[list[float]] = [[] for _ in range(n_rows)]
    out_n = [0] * n_rows
    batch_of = [-1] * n_rows
    pos_of = [-1] * n_rows
    per_batch: list[dict] = []
    t0 = time.time()
    for b, batch in enumerate(plan["batches"]):
        idxs = [key.row for key in batch]
        res = tool.forward(net, rows, device, len(idxs), index=idxs, quiet=True)
        if len(res.argmax) != len(idxs):
            raise PlanError(f"批 {b} 前向返回 {len(res.argmax)} 行 != 请求 {len(idxs)} 行")
        for pos, key in enumerate(batch):
            out_arg[key.row] = int(res.argmax[pos])
            out_log[key.row] = [float(x) for x in res.logits[pos]]
            out_n[key.row] = int(res.n[pos])
            batch_of[key.row] = b
            pos_of[key.row] = pos
        per_batch.append({"batch": b, "members": len(idxs), "seconds": res.seconds,
                          "rows": list(idxs)})
    sec = time.time() - t0
    out = BatchResult(argmax=out_arg, logits=out_log, n=out_n, batch_of=batch_of,
                      pos_of=pos_of, seconds=sec, per_batch=per_batch, served=served)
    if not quiet:
        sizes = [len(x) for x in plan["batches"]]
        more = "" if served == n_rows else f"（张量 {n_rows:,} 行、本次只服务 {served:,} 行）"
        print(f"[batch] device={device} 批={len(sizes)} 服务={served:,} 行{more} "
              f"K={sizes[0] if sizes else 0}（尾批 {sizes[-1] if sizes else 0}）"
              f"用时 {sec:.3f}s（{out.rows_per_sec():,.0f} 行/秒）", file=sys.stderr)
    return out


# ---------------------------------------------------------------- 输出 / 对拍 / 近边界

def write_out(path: Path | None, keys: list[Key], res: BatchResult, fmt: str,
              order: str) -> None:
    """落盘：`jsonl`（本工具口径，带 `row/game/step/seat/batch/pos`）或 `cpp`
    （与 `trainer v4net` **逐字符同格式**，便于直接 diff 那条逐行链路）。

    `--out-order row`（缺省）= 按**行号**排（逐行消费者读的就是第 k 行）；
    `canonical` = 按**批序**排（看"批是怎么装的"）。两者内容相同、只是行序不同。
    """
    by_row = {k.row: k for k in keys}
    seq = sorted(by_row) if order == "row" else [k.row for k in keys]
    lines = []
    for r in seq:
        k = by_row[r]
        if fmt == "cpp":
            vals = ",".join(f"{v:.9g}" for v in res.logits[r])
            lines.append(f"step={k.step} n={res.n[r]} argmax={res.argmax[r]} logits={vals}")
        else:
            lines.append(json.dumps({"row": r, "game": k.game, "step": k.step, "seat": k.seat,
                                     "batch": res.batch_of[r], "pos": res.pos_of[r],
                                     "n": res.n[r], "argmax": res.argmax[r],
                                     "logits": res.logits[r]}, ensure_ascii=False))
    text = "\n".join(lines) + "\n"
    if path is None:
        sys.stdout.write(text)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8", newline="\n")


def read_ref(path: Path) -> list[dict]:
    """读逐行参考（`trainer v4net` 的 stdout 或本工具的 jsonl）—— 两种格式都认。

    对齐判据是"**第 k 行的 n 相同**"：n 不同就说明两边喂的不是同一批行（或行序不同），
    此时比 argmax 毫无意义 ⇒ **当场报错**（R2 的教训：错位时"每行答案都对"仍会给出漂亮的汇总数）。
    """
    out: list[dict] = []
    for ln, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if s.startswith("{"):
            o = json.loads(s)
            out.append({"step": int(o.get("step", -1)), "n": int(o["n"]),
                        "argmax": int(o["argmax"]), "logits": [float(x) for x in o["logits"]]})
            continue
        if not s.startswith("step="):
            raise PlanError(f"{Path(path).name}:{ln} 既不是 jsonl 也不是 `step=…` 格式：{s[:60]!r}")
        d = {}
        for tok in s.split(" ", 3):
            k, _, v = tok.partition("=")
            d[k] = v
        out.append({"step": int(d.get("step", -1)), "n": int(d["n"]),
                    "argmax": int(d["argmax"]),
                    "logits": [float(x) for x in d["logits"].split(",") if x != ""]})
    if not out:
        raise PlanError(f"{path} 里一行参考都没有")
    return out


def compare_lines(keys: list[Key], res: BatchResult, ref: list[dict], tol: float,
                  tool) -> tuple[int, float, float, list[str]]:
    """逐行对拍：**argmax 全同**（硬判据）+ 随量纲的 `maxΔ ≤ tol × max(1,|ref|)`。

    ⛔ argmax 这条不放：数值口径管的是"同一个答案的浮点尾巴"，不是"可以换个答案"。
    """
    bad: list[str] = []
    if len(ref) != len(keys):
        raise PlanError(f"行对齐错位：参考 {len(ref)} 行 vs 键表 {len(keys)} 行")
    by_row = {k.row: k for k in keys}
    wa = wr = 0.0
    flips = 0
    for i, r in enumerate(ref):
        nref = int(r["n"])
        nme = int(res.n[i])
        if nref != nme:
            raise PlanError(f"行对齐错位：第 {i} 行（step={by_row[i].step}）参考 n={nref} != "
                            f"我算的 n={nme}")
        if len(r["logits"]) != len(res.logits[i]):
            raise PlanError(f"第 {i} 行 logits 长度 {len(res.logits[i])} != 参考 {len(r['logits'])}")
        if int(r["argmax"]) != res.argmax[i]:
            flips += 1
            d = max((abs(a - b) for a, b in zip(r["logits"], res.logits[i])), default=0.0)
            bad.append(f"row {i} step={by_row[i].step} seat={by_row[i].seat}：argmax "
                       f"{res.argmax[i]} != 参考 {r['argmax']}（该行 maxΔ {d:.3e}）")
        for a, b in zip(r["logits"], res.logits[i]):
            wa = max(wa, abs(float(a) - float(b)))
            wr = max(wr, tool.rel_delta(a, b))
    if flips:
        bad.insert(0, f"**argmax 翻转 {flips}/{len(ref)} 行**")
    if wr > tol:
        bad.insert(0, f"**相对 maxΔ {wr:.3e} > tol {tol:g}**")
    return flips, wa, wr, bad


def margins(res: BatchResult) -> dict:
    """近边界量化：`top1 − top2` 的分布（p1/p5/p50）+ `<1e-6` / `<1e-5` 的行数。

    这条是**风险**那一半：0 翻转只说明"Δ 还没跨过间距"，跨不跨得过取决于间距分布。
    打分逐元素用 float64（logits 本身就是 float32 的整数升上来的，取差不引入新误差）。
    """
    import numpy as np

    mg = []
    single = 0
    for lg in res.logits:
        if len(lg) < 2:
            single += 1
            continue
        v = np.sort(np.asarray(lg, dtype=np.float64))[::-1]
        mg.append(float(v[0] - v[1]))
    a = np.asarray(mg, dtype=np.float64)
    return {"rows": len(res.logits), "rows_with_1_candidate": single, "n": int(a.size),
            "p1": float(np.percentile(a, 1)), "p5": float(np.percentile(a, 5)),
            "p50": float(np.percentile(a, 50)), "min": float(a.min()),
            "lt_1e-6": int((a < 1e-6).sum()), "lt_1e-5": int((a < 1e-5).sum()),
            "lt_1e-4": int((a < 1e-4).sum())}


# ---------------------------------------------------------------- 自检

def _synth(tool, tmp: Path, n_rows: int = 12):
    """自检用的小网 + 小张量（合成数据：自检只问"实现是否正确"，不问"分布是否真实"）。"""
    import numpy as np
    import torch
    from mahjong_ml.v4 import export as v4export
    from mahjong_ml.v4 import model as M

    dims = {"d_model": 16, "tile_d": 8, "n_heads": 2, "value_bins": 3}
    torch.manual_seed(20261011)
    net = M.build(20261011, **dims)
    with torch.no_grad():                       # 零初始化会让并列满天飞，"间距"就没意义了
        for _, p in net.state_dict().items():
            if p.numel() and float(p.abs().max()) == 0.0:
                p.normal_(0.0, 0.5)
    good = tmp / "selftest-net.bin"
    v4export.save_net(net.state_dict(), dims, good)
    rows = tool._synth_rows(7, n_rows=n_rows)
    npz = tmp / "selftest-rows.npz"
    np.savez(str(npz), tile=rows.tile, evt=rows.evt, ctx=rows.ctx, cand=rows.cand,
             nlegal=rows.nlegal)
    keys = [Key(game=i // 4, step=i % 4, seat=i % 4, row=i) for i in range(n_rows)]
    return good, npz, keys


def selftest(tmp: Path) -> int:
    """四条自检：① 乱序 ⇒ 报错/不许提交（负向）+ 同输入两次指纹相同（正向对照）
    ② K 的边界（K>行数 / K=1 / 不整除）③ 行对齐错位 ⇒ 报错 ④ GPU 不可用 ⇒ 报错不回落。"""
    import numpy as np
    import torch

    tool = load_forward_tool()
    tmp = Path(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    fails: list[str] = []

    def line(ok: bool, msg: str) -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {msg}")
        if not ok:
            fails.append(msg)

    print("⓪ 键的声明序 == 规范序（SORT_KEY 与 Key 的字段序必须一致）")
    line([f.name for f in Key.__dataclass_fields__.values()][:3] == list(SORT_KEY),
         f"Key 前三个字段 {[f.name for f in Key.__dataclass_fields__.values()][:3]} == {list(SORT_KEY)}")

    good, npz, keys = _synth(tool, tmp)
    order = canonical_order(keys)

    # ---- ① 确定批：乱序必须被守卫拦住（负向）；同一输入两次的指纹必须相同（正向对照）
    print("① 确定批（负向：乱序 ⇒ 报错不许提交；正向：同输入两次指纹相同）")
    plan = plan_batches(order, 5, "keep")
    try:
        assert_canonical(plan, order, 5)
        line(True, "规范序计划 ⇒ 守卫通过（正向对照）")
    except PlanError as e:
        line(False, f"规范序计划被守卫拒了：{e}")
    import random
    shuffled = {"batches": [list(b) for b in plan["batches"]], "dropped": 0, "note": ""}
    random.Random(7).shuffle(shuffled["batches"][0])
    try:
        assert_canonical(shuffled, order, 5)
        line(False, "**批内乱序居然通过了守卫**（这条判据是死的）")
    except PlanError as e:
        line(True, f"批内乱序 ⇒ 报错：{str(e)[:80]}")
    swapped = {"batches": [list(plan["batches"][1]), list(plan["batches"][0])]
               + [list(b) for b in plan["batches"][2:]], "dropped": 0, "note": ""}
    try:
        assert_canonical(swapped, order, 5)
        line(False, "**批间乱序居然通过了守卫**")
    except PlanError as e:
        line(True, f"批间乱序 ⇒ 报错：{str(e)[:80]}")
    short = {"batches": [list(plan["batches"][0][:-1])] + [list(b) for b in plan["batches"][1:]],
             "dropped": 0, "note": ""}
    try:
        assert_canonical(short, order, 5)
        line(False, "**中间批少一行居然通过了守卫**")
    except PlanError as e:
        line(True, f"批大小不是 K ⇒ 报错：{str(e)[:80]}")
    line(plan_hash(plan_batches(canonical_order(keys), 5, "keep")) == plan_hash(plan),
         f"同一键表两次规划 ⇒ 同一指纹 {plan_hash(plan)[:16]}…（正向对照）")

    # ---- ② K 的边界
    print("② K 的边界（K>行数 / K=1 / 不能整除 / K<1）")
    big = plan_batches(order, 99, "keep")
    line(len(big["batches"]) == 1 and len(big["batches"][0]) == len(order),
         f"K=99 > 行数 {len(order)} ⇒ 单批放全部（{len(big['batches'][0])} 行）：{big['note']}")
    one = plan_batches(order, 1, "keep")
    line(len(one["batches"]) == len(order) and all(len(b) == 1 for b in one["batches"]),
         f"K=1 ⇒ {len(one['batches'])} 个逐行批（= 逐行版逐批同构）")
    nd = plan_batches(order, 5, "keep")
    line(len(nd["batches"][-1]) == len(order) % 5 and sum(len(b) for b in nd["batches"]) == len(order),
         f"12 行 / K=5 ⇒ {len(nd['batches'])} 批、尾批 {len(nd['batches'][-1])} 行、"
         f"合计 {sum(len(b) for b in nd['batches'])}（--tail keep）")
    dr = plan_batches(order, 5, "drop")
    line(dr["dropped"] == 2 and sum(len(b) for b in dr["batches"]) == 10,
         f"--tail drop ⇒ 丢 {dr['dropped']} 行、{len(dr['batches'])} 批共 "
         f"{sum(len(b) for b in dr['batches'])} 行（记账，不静默）")
    try:
        assert_canonical(dr, order, 5)                    # ⚠ 丢尾批**必须**能过守卫（第一版误判成红）
        line(True, "--tail drop 的计划 ⇒ 守卫放行（判据 = 等于规范序的**前缀**）")
    except PlanError as e:
        line(False, f"--tail drop 被守卫误判成红的：{str(e)[:80]}")
    try:
        plan_batches(order, 5, "error")
        line(False, "**--tail error 在不能整除时没有报错**")
    except PlanError as e:
        line(True, f"--tail error + 不能整除 ⇒ 报错：{str(e)[:80]}")
    try:
        plan_batches(order, 0, "keep")
        line(False, "**K=0 没有报错**")
    except PlanError as e:
        line(True, f"K=0 ⇒ 报错：{str(e)[:80]}")

    # ---- ③ 行对齐错位 ⇒ 报错
    print("③ 行对齐（负向：越界 / 重复 / 缺行 / 键重复 一律报错）")
    n_rows = len(order)
    cases = [
        ("row 越界", [Key(0, 0, 0, n_rows)] + keys[1:], False),
        ("row 重复", [Key(0, 0, 0, keys[1].row)] + keys[1:], False),
        ("缺一行（未覆盖）", keys[:-1], False),
        ("缺一行 + --allow-subset", keys[:-1], True),
    ]
    for label, ks, allow in cases:
        try:
            check_rows_alignment(ks, n_rows, allow_subset=allow)
            line(allow, f"{label} ⇒ {'放行（显式允许）' if allow else '**没有报错**'}")
        except PlanError as e:
            line(not allow, f"{label} ⇒ 报错：{str(e)[:80]}")
    dup = list(keys) + [Key(keys[0].game, keys[0].step, 3, 999)]
    try:
        canonical_order(dup)
        line(False, "**(game,step) 重复没有报错**（规范序不是全序）")
    except PlanError as e:
        line(True, f"(game,step) 重复 ⇒ 报错：{str(e)[:80]}")

    # ---- ④ 设备
    print("④ 设备（负向：cuda 不可用必须报错，⛔ 不静默回落 CPU）")
    real = torch.cuda.is_available
    try:
        torch.cuda.is_available = lambda: False
        try:
            dev = tool.resolve_device("cuda")
            line(False, f"cuda 不可用时居然返回了 {dev}（静默回落！）")
        except Exception as e:                                    # noqa: BLE001
            line(True, f"cuda 不可用 ⇒ 报错：{str(e)[:70]}")
    finally:
        torch.cuda.is_available = real
    line(tool.resolve_device("cpu").type == "cpu", "--device cpu 正常（正向对照）")

    # ---- ⑤ 端到端小跑：真前向两次 ⇒ 指纹与 argmax 逐位相同（"确定批自证"的最小样本）
    print("⑤ 端到端小跑（CPU，K=5）：两次前向的批指纹与 argmax 必须逐位相同")
    net = tool.load_net(good, torch.device("cpu"), tmp=str(tmp))
    rows = tool.read_rows(npz, "train")
    check_rows_alignment(keys, int(rows.tile.shape[0]), allow_subset=False)
    p = plan_batches(order, 5, "keep")
    assert_canonical(p, order, 5)
    r1 = forward_plan(tool, net, rows, torch.device("cpu"), p, quiet=True)
    r2 = forward_plan(tool, net, rows, torch.device("cpu"), p, quiet=True)
    line(r1.argmax == r2.argmax and r1.logits == r2.logits,
         f"两次前向 argmax/logits 逐位相同：{sum(1 for a, b in zip(r1.argmax, r2.argmax) if a == b)}"
         f"/{len(r1.argmax)}")
    line(plan_hash(plan_batches(canonical_order(keys), 5, "keep")) == plan_hash(p),
         "两次规划的批指纹相同（成员与顺序都由键表决定）")
    mg = margins(r1)
    line(mg["n"] + mg["rows_with_1_candidate"] == len(r1.argmax),
         f"近边界量化的分母自洽：{mg['n']} 行有 ≥2 候选 + {mg['rows_with_1_candidate']} 行单候选 "
         f"= {len(r1.argmax)} 行")
    # `--out-order row` 的落盘必须按行号升序（逐行链路读的就是第 k 行）
    out = tmp / "selftest-out.jsonl"
    write_out(out, keys, r1, "jsonl", "row")
    got = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    line([g["row"] for g in got] == list(range(len(keys))),
         f"--out-order row ⇒ 行号升序落盘（{len(got)} 行）")
    ref = tmp / "selftest-ref.txt"
    write_out(ref, keys, r1, "cpp", "row")
    f, wa, wr, bad = compare_lines(order, r1, read_ref(ref), 1e-4, tool)
    line(f == 0 and wr <= 1e-4, f"自比（cpp 参考 vs 我算的）：翻转 {f}/{len(order)}、"
                                f"绝对 maxΔ {wa:.3e}、相对 {wr:.3e}")
    # ⚠ **子集吞吐的记账**（本工具第一版的红）：`--allow-subset` 时 `len(argmax)` 是整个张量的
    #   行数 ⇒ 拿它当分子会把吞吐放大（20k 子集报出 27,745 行/秒）。分子必须是**服务行数**。
    sub = plan_batches(order[:5], 5, "keep")
    rs = forward_plan(tool, net, rows, torch.device("cpu"), sub, quiet=True)
    line(rs.served == 5 and abs(rs.rows_per_sec() - 5 / max(rs.seconds, 1e-9)) < 1e-6 * rs.rows_per_sec(),
         f"子集吞吐的分子 = 服务行数：served={rs.served}（张量 {len(rs.argmax)} 行）"
         f"⇒ {rs.rows_per_sec():,.0f} 行/秒")

    print(("SELFTEST PASS" if not fails else f"SELFTEST FAIL（{len(fails)} 条）") + f" @ {tmp}")
    return EXIT_OK if not fails else EXIT_MISMATCH


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="v4-batch-serve.py",
        description="v4 前向的**确定批**跨局推理（规范序 (game,step,seat) → 固定 K 的批）")
    ap.add_argument("--weights", help="net.bin（格式 2）")
    ap.add_argument("--rows", help="行级张量 .npz / P5 夹具 / 紧凑集目录（**已经算好的**）")
    ap.add_argument("--split", default="train", help="--rows 是紧凑集目录时选哪一份切分")
    ap.add_argument("--keys-from-traces", default=None,
                    help="键表来源①：轨迹目录（只读 game/step/seat，⛔ 不读 obs）")
    ap.add_argument("--keys", default=None, help="键表来源②：TSV（row/game/step/seat）")
    ap.add_argument("--keys-out", default=None, help="把键表落成 TSV（审计 / 复算用）")
    ap.add_argument("--policy", default=None, help="镜像 `materialize` 的 --policy 过滤（保证行序一致）")
    ap.add_argument("--where-kind", default=None, help="镜像 `materialize` 的 --where-kind 过滤")
    ap.add_argument("--limit", type=int, default=0, help="镜像 materialize 的 --limit（0 = 不限）")
    ap.add_argument("--allow-subset", action="store_true",
                    help="允许键表只覆盖一部分张量行（缺行会被记账；缺省是报错）")
    ap.add_argument("--batch", type=int, default=512, help="**K**：确定批的大小（32/128/512…）")
    ap.add_argument("--tail", choices=["keep", "drop", "error"], default="keep",
                    help="行数不能整除 K 时怎么办（缺省 keep = 尾批短、照算）")
    ap.add_argument("--shuffle-plan", action="store_true",
                    help="⛔ **负向控制（只给自检用）**：故意打乱批计划 —— 必须被守卫拦住、退出码 2")
    ap.add_argument("--device", default="cuda", help="cuda（缺省）/ cpu —— 同一份代码")
    ap.add_argument("--threads", type=int, default=4, help="torch CPU 线程数（缺省 4，与并行度口径一致）")
    ap.add_argument("--repeat", type=int, default=1,
                    help="整个计划连跑几次（>1 分开冷/热，并断言每次 argmax 逐位相同）")
    ap.add_argument("--out", default=None, help="输出文件（缺省 stdout）")
    ap.add_argument("--format", choices=["jsonl", "cpp"], default="jsonl",
                    help="jsonl（缺省）/ cpp（与 `trainer v4net` 逐字符同格式）")
    ap.add_argument("--out-order", choices=["row", "canonical"], default="row",
                    help="row（缺省，按行号）/ canonical（按批序）")
    ap.add_argument("--manifest", default=None, help="批计划 + 记账落成 JSON（指纹在里面）")
    ap.add_argument("--compare", default=None, help="逐行参考（`trainer v4net` stdout / 本工具 jsonl）")
    ap.add_argument("--tol", type=float, default=1e-4, help="**相对**容差：|Δ| ≤ tol × max(1,|ref|)")
    ap.add_argument("--margins", action="store_true", help="报 top1−top2 间距分布（近边界风险）")
    ap.add_argument("--tmp", default=str(REPO / "python" / ".tmp" / "v4-batch-serve"),
                    help="自检 / 临时目录（缺省在仓库内）")
    ap.add_argument("--selftest", action="store_true", help="跑四条自检（含三条负向）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return selftest(Path(a.tmp))
    if not a.weights or not a.rows:
        ap.error("要么给 --weights + --rows，要么给 --selftest")
    if bool(a.keys_from_traces) == bool(a.keys):
        ap.error("--keys-from-traces 与 --keys 必须**恰好给一个**（键表是确定批的唯一输入）")

    tool = load_forward_tool()
    tool.pin_threads(a.threads)
    try:
        device = tool.resolve_device(a.device)
        net = tool.load_net(Path(a.weights), device, tmp=a.tmp)
        rows = tool.read_rows(Path(a.rows), a.split)
        n_rows = int(rows.tile.shape[0])
        keys = (load_keys_traces(Path(a.keys_from_traces), policy=a.policy,
                                 where_kind=a.where_kind, limit=a.limit, quiet=a.quiet)
                if a.keys_from_traces else read_keys(Path(a.keys)))
        if a.keys_out:
            write_keys(Path(a.keys_out), keys)
        served, unserved = check_rows_alignment(keys, n_rows, allow_subset=a.allow_subset)
        order = canonical_order(keys)                     # ← 唯一的一份规范序
        plan = plan_batches(order, a.batch, a.tail)
        if a.shuffle_plan:                                # 负向控制：必须被下面那道守卫拦住
            plan["batches"] = plan["batches"][::-1]
            print("[neg] ⛔ --shuffle-plan：已故意把批序倒过来（负向控制，不该出结果）", file=sys.stderr)
        assert_canonical(plan, order, a.batch)            # ← **提交前**的守卫（乱序在这里死）
        ph = plan_hash(plan)
        if not a.quiet:
            print(f"[plan] 键 {len(keys):,} 行（服务 {served} / 未覆盖 {unserved}）· "
                  f"K={a.batch} · 批 {len(plan['batches'])} · {plan['note']} · "
                  f"规范序指纹 sha256={ph[:16]}…", file=sys.stderr)
        res = None
        for rep in range(1, max(1, a.repeat) + 1):
            cur = forward_plan(tool, net, rows, device, plan, quiet=a.quiet)
            # ⚠ 冷/热分开报：CUDA 第一次要建 cuBLAS 句柄 + 载 sm_120 内核（本机实测 ~15 s），
            #   摊进吞吐就会得出"GPU 比 CPU 慢"的假结论。
            print(f"[rep] run {rep}（{'冷' if rep == 1 else '热'}）: {cur.seconds:.3f}s "
                  f"（{cur.rows_per_sec():,.0f} 行/秒）", file=sys.stderr)
            if res is not None and (cur.argmax != res.argmax or cur.logits != res.logits):
                bad = sum(1 for x, y in zip(cur.argmax, res.argmax) if x != y)
                raise PlanError(f"--repeat 第 {rep} 次与第 1 次有 {bad} 行 argmax 不同 —— "
                                f"同一份输入给出两个答案")
            res = cur
        write_out(Path(a.out) if a.out else None, keys, res, a.format, a.out_order)
        mg = margins(res) if a.margins else None
        if mg:
            print(f"[margin] top1−top2：p1={mg['p1']:.3e} p5={mg['p5']:.3e} p50={mg['p50']:.3e} "
                  f"min={mg['min']:.3e}；<1e-6 的 {mg['lt_1e-6']} 行 / <1e-5 的 {mg['lt_1e-5']} 行 / "
                  f"<1e-4 的 {mg['lt_1e-4']} 行（分母 {mg['n']} 行有 ≥2 候选）", file=sys.stderr)
        meta = {"rows": a.rows, "row_count": n_rows, "served": served, "unserved": unserved,
                "keys_source": f"traces:{a.keys_from_traces}" if a.keys_from_traces else f"keys:{a.keys}",
                "policy": a.policy, "where_kind": a.where_kind, "limit": a.limit,
                "sort_key": list(SORT_KEY), "K": a.batch, "tail": a.tail,
                "batches": len(plan["batches"]), "batch_note": plan["note"],
                "dropped": plan["dropped"], "plan_sha256": ph,
                "device": str(device), "repeat": max(1, a.repeat),
                "seconds": res.seconds, "rows_per_sec": res.rows_per_sec(),
                "margins": mg}
        if a.manifest:
            Path(a.manifest).parent.mkdir(parents=True, exist_ok=True)
            Path(a.manifest).write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                        encoding="utf-8", newline="\n")
        if a.compare:
            ref = read_ref(Path(a.compare))
            flips, wa, wr, bad = compare_lines(keys, res, ref, a.tol, tool)
            print(f"[cmp] vs {Path(a.compare).name}：行 {len(ref):,} · **argmax 翻转 {flips}** · "
                  f"绝对 maxΔ {wa:.3e} · 相对 maxΔ {wr:.3e}（≤ {a.tol:g} × max(1,|ref|)）",
                  file=sys.stderr)
            for b in bad[:8]:
                print(f"  ✗ {b}", file=sys.stderr)
            if flips or wr > a.tol:
                return EXIT_MISMATCH
        if not a.quiet:
            print(f"[done] {served:,} 行 → {a.out or 'stdout'}；{res.seconds:.3f}s；"
                  f"{res.rows_per_sec():,.0f} 行/秒；device={device}；指纹 {ph[:16]}…",
                  file=sys.stderr)
        return EXIT_OK
    except (PlanError, tool.InputError) as e:
        print(f"[v4-batch-serve] ⛔ {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
