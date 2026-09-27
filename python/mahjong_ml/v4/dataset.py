"""v4 **数据集**：轨迹（obs + sidecar + aux 标签）→ 四张量定长数组 + 监督标签。

## 为什么单独一份（不复用 `mahjong_ml.dataset`）

v3 的紧凑集存的是 **state 615 / cand 96**（特征 v3 的拼装结果）；v4 的输入是
**`tile[34,48] / evt[60,96] / ctx[64] / cand[n,128]`**（`FEATURES-V4.md` §4）——
形状、来源、版本判据都不同。混在一份里就得在读取时靠 `feature_version` 分叉，
迟早有人读错（而读错的症状是"训练照跑、指标难看"）。

**两条硬闸门**（都在 `v4/traces.py`，这里只是调用方）：
① obs 必须 v3（没有 `events` 的记录**整份报错**）；
② sidecar 必须 `derived_version == DERIVED_VERSION_V4`；`aux` 标签按 `--aux` 要求。

## 存的列（全部按**决策行顺序**，与轨迹一一对应）

| 列 | 形状/dtype | 来源 |
| --- | --- | --- |
| `tile` / `evt` / `ctx` / `cand` | `[34,48] [60,96] [64] [lmax,128]` **float16** | `blocks.assemble`（obs + sidecar） |
| `nlegal` | int16 | 决策行的 `legal` 长度 |
| `label` | int16 | **教师动作**：有 `teacher_index` 用它（DAgger），否则用 `chosen_index` |
| `effect` | `[lmax,3]` float16 | sidecar 逐候选派生量的前 3 维（向听/进张种数/进张枚数）⇒ 牌效头的回归目标 |
| `value` | float32 | `final_scores[seat] − 起点`（**千点**；值头的 HL-Gauss 目标） |
| `placement` | int8 | 该座位终局顺位 − 1（0..3；缺字段填 −1） |
| `seat` / `game` / `hand_no` | int8 / int32 / int16 | 溯源与按座位诊断 |
| `aux_opp_hand` | `[3,34]` uint8 | `g*.aux.npz`（**隐藏真值**；信念头） |
| `aux_opp_tenpai` | `[3]` uint8 | 同上（对手听牌信念头） |
| `aux_opp_dealin` | `[3]` uint8 | 本小局是否放铳给第 j 家（危险头；只在**被选中的那个候选**上有标签） |
| `aux_own_shanten_after` / `aux_own_tenpai` / `aux_win_flag` | 标量 | 牌效/价值辅助标签 |
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

from .. import auxlabels as _aux
from .. import dataset as _v3
from . import blocks, spec, traces

#: 张量的存盘 dtype（float16：显存/磁盘减半，训练时再转 float32 —— 与 v3 紧凑集同一个取舍）
TENSOR_DTYPE = np.float16

#: 数据集格式版本（列增删要 +1）
DATASET_VERSION = 1


def trace_dir_files(directory: str | Path) -> list[Path]:
    """目录里按序号排好的 `g*.jsonl`（复用 `v4/traces.py` 的闸门与排序）。"""
    return traces.trace_files(directory)


def _game_start_score(jsonl: Path) -> int:
    """该场的起点分（`game` 行的 `start_score`；缺了就按 25000 —— M.League 默认）。"""
    last = None
    with jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                last = line
    if last is None:
        return 25000
    row = json.loads(last)
    return int(row.get("start_score", 25000)) if row.get("type") == "game" else 25000


def _aux_arrays(ax: dict | None, n: int) -> dict[str, np.ndarray]:
    """把标签侧 npz 的列搬成**定长数组**（缺标签时给全 0 并把 `has_aux` 记 False）。"""
    if ax is None:
        return {"opp_hand": np.zeros((n, 3, 34), np.uint8),
                "opp_tenpai": np.zeros((n, 3), np.uint8),
                "opp_dealin": np.zeros((n, 3), np.uint8),
                "own_shanten_after": np.zeros((n,), np.int8),
                "own_tenpai": np.zeros((n,), np.uint8),
                "win_flag": np.zeros((n,), np.uint8)}
    return {"opp_hand": np.asarray(ax["opp_hand"], np.uint8),
            "opp_tenpai": np.asarray(ax["opp_tenpai"], np.uint8),
            "opp_dealin": np.asarray(ax["opp_dealin"], np.uint8),
            "own_shanten_after": np.asarray(ax["own_shanten_after"], np.int8),
            "own_tenpai": np.asarray(ax["own_tenpai"], np.uint8),
            "win_flag": np.asarray(ax["win_flag"], np.uint8)}


def count_file(jsonl: Path) -> tuple[int, int]:
    """`(决策数, 最大 legal 长度)` —— 第一遍扫描（与 `_write_one_file` 同一口径）。"""
    n = 0
    lmax = 0
    with jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "decision":
                continue
            n += 1
            lmax = max(lmax, len(row.get("legal") or []))
    return n, lmax


def rows_for_label(row: dict) -> int:
    """教师标签：有 `teacher_index` 用它（DAgger），否则 `chosen_index`。"""
    if "teacher_index" in row:
        return int(row["teacher_index"])
    return int(row.get("chosen_index", -1))


#: 动作类型的固定表（诊断用：val 里"哪类动作学得像"）—— 与 `features.ACTION_TYPES` 同集合
ACTION_TYPES = ("discard", "riichi", "pon", "chi", "kan", "tsumo", "ron", "pass", "kyuushu")


def _type_id(key: str) -> int:
    """动作键 → 类型下标（认不出记 0；`label_type` 只服务诊断，不进任何输入）。"""
    t = key.split(":", 1)[0]
    return ACTION_TYPES.index(t) if t in ACTION_TYPES else 0


#: 逐列定义（名 → (dtype, 尾形状)）：父进程建、子进程按同一份定义开（**只有一份**）
_COLUMNS: dict[str, tuple] = {
    "tile": (TENSOR_DTYPE, (34, spec.C_TILE)),
    "evt": (TENSOR_DTYPE, (spec.K_EVT, spec.C_EVT)),
    "ctx": (TENSOR_DTYPE, (spec.C_CTX,)),
    "cand": (TENSOR_DTYPE, ("lmax", spec.C_CAND)),
    "nlegal": (np.int16, ()),
    "label": (np.int16, ()),
    "label_type": (np.int8, ()),
    "effect": (TENSOR_DTYPE, ("lmax", 3)),
    "value": (np.float32, ()),
    "placement": (np.int8, ()),
    "seat": (np.int8, ()),
    "game": (np.int32, ()),
    "hand_no": (np.int16, ()),
    # ---- 自对弈改进（P3 开局）用的两列（2026-09-27 加；老数据集没有这两列，`load_split` 给 None）----
    # `is_student`：这一行的动作是不是**本轮要训练的那一代**做的（`build --student <策略串>` 判定）
    "is_student": (np.int8, ()),
    # `delta`：本小局该家的收支（点）—— RWR/优势加权的回报来源（轨迹里 `hand_delta` 是四家数组）
    "delta": (np.int32, ()),
    "aux_opp_hand": (np.uint8, (3, 34)),
    "aux_opp_tenpai": (np.uint8, (3,)),
    "aux_opp_dealin": (np.uint8, (3,)),
    "aux_own_shanten_after": (np.int8, ()),
    "aux_own_tenpai": (np.uint8, ()),
    "aux_win_flag": (np.uint8, ()),
}


def open_columns(out_dir: Path, tag: str, n: int, lmax: int,
                 mode: str = "w+") -> dict[str, np.memmap]:
    """建/开这一份切分的全部列（`mode="w+"` 建、`"r+"` 由子进程开）。"""
    out: dict[str, np.memmap] = {}
    for name, (dt, tail) in _COLUMNS.items():
        shape = (n, *tuple(lmax if t == "lmax" else t for t in tail))
        out[name] = np.lib.format.open_memmap(out_dir / f"{tag}.{name}.npy", mode=mode,
                                              dtype=dt, shape=shape)
    return out


def _write_file(mm: dict, f: Path, i0: int, take: int, *, lmax: int, aux: bool,
                student: str | None = None) -> None:
    """把一个文件的**前 `take` 条**决策写进 `mm` 的 `[i0, i0+take)` 行。

    ⚠ 串行与并行**共用这一份行逻辑**（并行只是把不同文件分给不同进程，各写各的连续行区间）
    ⇒ "并行产物 == 串行产物"是构造出来的，不是大概一样。
    """
    start = _game_start_score(f)
    ax = _aux.load_aux(_aux.aux_path(f)) if aux else None
    axcols = _aux_arrays(ax, int(ax["n"]) if ax else 0)
    sc = traces.load_sidecar(_v3.sidecar_path(f))
    j = 0
    with f.open(encoding="utf-8") as fh:
        for ln, line in enumerate(fh, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "decision":
                continue
            if j >= take:
                break
            i = i0 + j
            obs = row.get("obs")
            if not isinstance(obs, dict):
                raise spec.ContractError(f"{f.name}:{ln} 决策行没有 obs")
            traces.gate_obs(obs, where=f"{f.name}:{ln}")
            side = traces.sidecar_dict(sc, j)
            t = blocks.assemble(obs, side, allow_degraded=False)
            # ⚠ **降级 = 静默换任务**：`blocks.assemble` 的 `allow_degraded=False` 只拦"版本不够"
            #   那两条路；侧存段的**缺失**（例如 `sidecar_dict` 忘了给 `cand`）会走"填 0 +
            #   `degraded.add(...)`"那条路 —— 没有这一条断言，`cand[88:128]` 就会整块是 0，
            #   而训练照跑、指标照出（2026-09-27 实测踩到：`v4-bc-002` 的 40 列全 0）。
            if t.degraded:
                raise spec.ContractError(
                    f"{f.name}:{ln} 张量拼装发生降级：{sorted(t.degraded)} —— 这些块会被静默填 0"
                    f"（等于删掉那一路信息）。检查 obs 版本、sidecar 段长与 `traces.sidecar_dict`")
            legal = list(row.get("legal") or [])
            # ⚠ `blocks` 是按 **obs.legal** 展开候选的，而标签下标来自 **行上的 legal** ——
            #   两者必须逐字相同（不同就是拿别人的下标当标签，且不报错）
            if list(obs.get("legal") or []) != legal:
                raise spec.ContractError(
                    f"{f.name}:{ln} obs.legal 与行上的 legal 不一致"
                    f"（{len(obs.get('legal') or [])} vs {len(legal)} 条）")
            if len(legal) > lmax:
                raise spec.ContractError(f"{f.name}:{ln} legal {len(legal)} > lmax {lmax}")
            # ⚠ sidecar 的逐候选段必须与这一行的 legal **逐条对齐**（行数也要对）：
            #   错位就会把别人的牌效目标训到这一行上，而且不报错
            if int(sc["nlegal"][j]) != len(legal):
                raise spec.ContractError(
                    f"{f.name}:{ln} sidecar nLegal={int(sc['nlegal'][j])} != "
                    f"jsonl legal={len(legal)} —— sidecar 是旧轨迹生成的，重新 --features")
            lab = rows_for_label(row)
            if not (0 <= lab < len(legal)):
                raise spec.ContractError(f"{f.name}:{ln} 标签 {lab} 越界（legal {len(legal)}）")
            mm["tile"][i] = t.tile.astype(TENSOR_DTYPE)
            mm["evt"][i] = t.evt.astype(TENSOR_DTYPE)
            mm["ctx"][i] = t.ctx.astype(TENSOR_DTYPE)
            mm["cand"][i, :len(legal)] = t.cand.astype(TENSOR_DTYPE)
            mm["nlegal"][i] = len(legal)
            mm["label"][i] = lab
            mm["label_type"][i] = _type_id(legal[lab])
            # 牌效头目标：sidecar 的逐候选派生量前 3 维（向听 / 进张种数 / 进张枚数）
            per = np.asarray(sc["cand"][int(sc["offsets"][j]):int(sc["offsets"][j + 1])],
                             dtype=np.float32)[:, :3]
            mm["effect"][i, :len(legal)] = per.astype(TENSOR_DTYPE)
            seat = int(row.get("seat", -1))
            fs = row.get("final_scores")
            mm["value"][i] = (float(fs[seat]) - start) / 1000.0 \
                if isinstance(fs, list) and 0 <= seat < 4 else 0.0
            pl = row.get("placement")
            mm["placement"][i] = int(pl[seat]) - 1 \
                if isinstance(pl, list) and 0 <= seat < 4 else -1
            mm["seat"][i] = seat
            mm["game"][i] = int(row.get("game", -1))
            mm["hand_no"][i] = int(row.get("hand_no", -1))
            # ① `is_student`：这一行的动作是不是**本轮被训练的那一代**做的（v3 的 `--student` 口径）。
            #    ⚠ 不遮的后果是**静默**的：把对手网/老师的动作也算进策略损失（v3 实测学生行占比
            #    0.75 而非 0.25，指标照样好看）。
            mm["is_student"][i] = 1 if (student is None or row.get("policy") == student) else 0
            # ② `delta`：本小局该家的收支（点）—— RWR / 优势加权的**唯一回报来源**（事后回填）。
            #    缺字段记 0（那这一行的权重就是 1，等于回到纯模仿）。
            hd = row.get("hand_delta")
            mm["delta"][i] = int(hd[seat]) if isinstance(hd, list) and 0 <= seat < len(hd) else 0
            if ax is not None:
                mm["aux_opp_hand"][i] = axcols["opp_hand"][j]
                mm["aux_opp_tenpai"][i] = axcols["opp_tenpai"][j]
                mm["aux_opp_dealin"][i] = axcols["opp_dealin"][j]
                mm["aux_own_shanten_after"][i] = axcols["own_shanten_after"][j]
                mm["aux_own_tenpai"][i] = axcols["own_tenpai"][j]
                mm["aux_win_flag"][i] = axcols["win_flag"][j]
            j += 1
    if j != take:
        raise spec.ContractError(f"{f.name} 实际 {j} 条 != 第一遍数的 {take} 条")
    if j != int(sc["n"]):
        raise spec.ContractError(f"{f.name} 决策 {j} 条 != sidecar {int(sc['n'])} 条")
    if aux and j != int(ax["n"]):
        raise spec.ContractError(f"{f.name} 决策 {j} 条 != 标签侧 {int(ax['n'])} 条")


def _chunk_split(items: list, k: int) -> list[list]:
    """把列表切成 ≤k 段（尽量均匀；给子进程分工用）。"""
    if not items:
        return []
    k = max(1, min(k, len(items)))
    per = (len(items) + k - 1) // k
    return [items[i:i + per] for i in range(0, len(items), per)]


def _spawn(jobs: list[list[str]], log_dir: Path) -> None:
    """起 `len(jobs)` 个**无管道**子进程（stdout/stderr 直接写日志文件），等它们全部成功。

    ⚠ **为什么不用 `multiprocessing.Pool`**：本机沙箱禁**命名管道**（`Pool` 的队列就是命名管道，
    构造时即炸）。子进程 + 文件同样是真并行，且没有管道 —— 与 `mahjong_ml.dataset._spawn` 同一套做法
    （那边耦合在它自己的 argparse 上，所以这里各留一份二十行）。
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    procs = []
    for k, args in enumerate(jobs):
        log = (log_dir / f"job{k}.log").open("w", encoding="utf-8")
        procs.append((k, subprocess.Popen([sys.executable, "-m", "mahjong_ml.v4.dataset", *args],
                                          stdout=log, stderr=subprocess.STDOUT), log))
    for k, p, log in procs:
        rc = p.wait()
        log.close()
        if rc != 0:
            tail = (log_dir / f"job{k}.log").read_text(encoding="utf-8", errors="replace")[-800:]
            raise RuntimeError(f"v4 数据集并行子进程 job{k} 退出码 {rc}：\n{tail}")


def build(src: str | Path, out_dir: str | Path, *, val_frac: float = 0.05, split_seed: int = 0,
          aux: bool = False, limit_files: int | None = None, workers: int = 0,
          quiet: bool = False, student: str | None = None) -> dict:
    """轨迹目录 → v4 数据集（`train.*.npy` / `val.*.npy` 各一组列 + `meta.json`）。

    @param aux 是否要求并读取标签侧 `g*.aux.npz`（信念/危险头的监督来源）
    @param student 本轮要训练的那一代的**确切策略串**（写 `is_student` 列；None = 全部算学生）
    @param workers 并行进程数（0 = 自动，≤75% 的核、上限 12）；**产物与串行逐字节相同**是判据
        （各进程只写自己那段连续行区间，行逻辑只有 `_write_file` 一份）
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if workers <= 0:
        workers = max(1, min(12, (os.cpu_count() or 4) * 3 // 4))
    files = trace_dir_files(src)
    if limit_files:
        files = files[:limit_files]
    train_files, val_files = _v3.split_files(files, val_frac, split_seed)

    def one_split(part: list[Path], tag: str) -> dict:
        counts = []
        lmax = 0
        for f in part:
            c, m = count_file(f)
            counts.append(c)
            lmax = max(lmax, m)
        n = sum(counts)
        if n == 0:
            raise spec.ContractError(f"{tag} 切分里没有决策（val_frac={val_frac} 太小？）")
        paths = {name: out_dir / f"{tag}.{name}.npy" for name in
                 ("tile", "evt", "ctx", "cand", "nlegal", "label", "label_type", "effect",
                  "value", "placement", "seat", "game", "hand_no",
                  "aux_opp_hand", "aux_opp_tenpai", "aux_opp_dealin",
                  "aux_own_shanten_after", "aux_own_tenpai", "aux_win_flag")}
        mm = open_columns(out_dir, tag, n, lmax, mode="w+")
        i = 0
        # 每个文件的**行区间**（行号只由"文件顺序 + 文件内行序"决定，与并行度无关）
        tasks: list[tuple[Path, int, int]] = []
        off = 0
        for f, c in zip(part, counts):
            if c > 0:
                tasks.append((f, off, c))
            off += c
        if workers > 1 and len(tasks) > 1:
            # 并行：按文件分组切块 → **无管道子进程**（沙箱禁命名管道，见 `_spawn` 的注释）
            chunks = _chunk_split(tasks, workers)
            for m in mm.values():                      # 先落盘并**关掉父进程的映射**
                m.flush()                              # （Windows 上 mmap 会锁文件，子进程打不开 r+）
                m._mmap.close()                        # noqa: SLF001（numpy 没给公开的 close）
            tmp = out_dir / f"_parallel-{tag}"
            req = tmp / "req.json"
            tmp.mkdir(parents=True, exist_ok=True)
            req.write_text(json.dumps({
                "out_dir": str(out_dir), "tag": tag, "lmax": lmax, "aux": bool(aux),
                "student": student,
                "chunks": [[[str(f), int(i0), int(take)] for f, i0, take in ch] for ch in chunks],
            }, ensure_ascii=False), encoding="utf-8")
            _spawn([["_chunk", str(req), str(k)] for k in range(len(chunks))], tmp / "logs")
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            for f, i0, take in tasks:
                _write_file(mm, f, i0, take, lmax=lmax, aux=aux, student=student)
            for m in mm.values():
                m.flush()
        return {"files": [f.name for f in part], "decisions": n, "lmax": lmax}

    train = one_split(train_files, "train")
    val = one_split(val_files, "val")
    meta = {
        "dataset_version": DATASET_VERSION,
        "feature_version": spec.FEATURE_VERSION_V4,
        "obs_version": spec.OBS_VERSION_V4,
        "derived_version": spec.DERIVED_VERSION_V4,
        "blocks_fingerprint": spec.fingerprint(),
        "has_aux": bool(aux),
        "action_types": list(ACTION_TYPES),
        "tensor_dtype": np.dtype(TENSOR_DTYPE).name,
        "shapes": {"tile": [34, spec.C_TILE], "evt": [spec.K_EVT, spec.C_EVT],
                   "ctx": [spec.C_CTX], "cand": ["lmax", spec.C_CAND]},
        "src": str(src),
        "train_decisions": train["decisions"], "val_decisions": val["decisions"],
        "train_files": train["files"], "val_files": val["files"],
        "lmax": max(train["lmax"], val["lmax"]),
        "val_frac": val_frac, "split_seed": split_seed,
        "student": student,
        "source": ("teacher 自对弈轨迹（`--aux` 带标签侧）" if aux else "自对弈轨迹"),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    if not quiet:
        print(f"v4 数据集：{out_dir}")
        print(f"  训练 {train['decisions']} 条（{len(train['files'])} 场）/ "
              f"验证 {val['decisions']} 条（{len(val['files'])} 场）；lmax={meta['lmax']}；"
              f"dtype={meta['tensor_dtype']}")
        print(f"  标签侧：{'有（' + str(aux) + '）' if aux else '无'}；"
              f"块指纹 {meta['blocks_fingerprint']}；obs v{meta['obs_version']} / "
              f"derived v{meta['derived_version']}")
    return meta


def load_split(out_dir: str | Path, split: str) -> dict:
    """读回一份切分（内存映射）。⚠ 版本不符**报错**（不猜）。"""
    out_dir = Path(out_dir)
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    if meta["feature_version"] != spec.FEATURE_VERSION_V4:
        raise ValueError(f"数据集特征版本 {meta['feature_version']} != 代码 "
                         f"{spec.FEATURE_VERSION_V4} —— 重建数据集")
    if meta["blocks_fingerprint"] != spec.fingerprint():
        raise ValueError(f"块清单指纹 {meta['blocks_fingerprint']} != 当前 {spec.fingerprint()}"
                         f" —— 注册表变了，重建数据集")
    out: dict = {"meta": meta, "split": split}
    for name in ("tile", "evt", "ctx", "cand", "nlegal", "label", "label_type", "effect", "value",
                 "placement", "seat", "game", "hand_no", "is_student", "delta",
                 "aux_opp_hand", "aux_opp_tenpai",
                 "aux_opp_dealin", "aux_own_shanten_after", "aux_own_tenpai", "aux_win_flag"):
        p = out_dir / f"{split}.{name}.npy"
        out[name] = np.load(p, mmap_mode="r") if p.is_file() else None
    return out


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `build` 可以省：`python -m mahjong_ml.v4.dataset <src> <out>` 等价于加了它
    # （⚠ 位置参数不能被 `nargs="?"` 的 cmd 吃掉 —— 用显式改写而不是设默认值）
    if not argv or argv[0] not in ("build", "_chunk"):
        argv = ["build", *argv]
    ap = argparse.ArgumentParser(description="v4 数据集：轨迹 → 四张量 + 监督标签")
    ap.add_argument("cmd", choices=["build", "_chunk"])
    ap.add_argument("src", help="轨迹目录（build）或 req.json（_chunk）")
    ap.add_argument("out", nargs="?", help="输出目录（build）或分块下标（_chunk）")
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--split-seed", type=int, default=0)
    ap.add_argument("--aux", action="store_true", help="读取标签侧 g*.aux.npz（信念/危险头监督）")
    ap.add_argument("--limit-files", type=int, default=None, help="只取前 N 个轨迹文件（冒烟）")
    ap.add_argument("--student", default=None,
                    help="本轮要训练的那一代的**确切策略串**（写 `is_student` 列；缺省 = 全部算学生）")
    ap.add_argument("--workers", type=int, default=0,
                    help="并行进程数（0 = 自动，≤75%% 的核、上限 12）；产物与串行逐字节相同")
    args = ap.parse_args(argv)
    if args.cmd == "_chunk":
        # 内部子命令（并行用；见 `_spawn`）：把 req.json 里第 k 组任务写进（父进程已建好的）列
        req = json.loads(Path(args.src).read_text(encoding="utf-8"))
        k = int(args.out)
        out_dir = Path(req["out_dir"])
        tag = req["tag"]
        tasks = [(Path(f), int(i0), int(take)) for f, i0, take in req["chunks"][k]]
        n = int(np.load(out_dir / f"{tag}.nlegal.npy", mmap_mode="r").shape[0])
        mm = open_columns(out_dir, tag, n, int(req["lmax"]), mode="r+")
        for f, i0, take in tasks:
            _write_file(mm, f, i0, take, lmax=int(req["lmax"]), aux=bool(req["aux"]),
                        student=req.get("student"))
        for m in mm.values():
            m.flush()
        return 0
    build(args.src, args.out, val_frac=args.val_frac, split_seed=args.split_seed, aux=args.aux,
          limit_files=args.limit_files, workers=args.workers, student=args.student)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
