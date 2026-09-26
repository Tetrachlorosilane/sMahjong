"""轨迹 → 紧凑数组（`docs/TRAINING.md` §5 的落地）。

    python -m mahjong_ml.dataset build <轨迹目录> <输出目录> [--max-decisions N] [--val-frac 0.1]

三件事：
  · **流式读** `g*.jsonl`（每场一个文件），只取 `type == "decision"` 的行；
  · 用 `features.py` 把 `obs` + `legal` 转成 `state[544]` + `cand[L,88]`，**落盘用内存映射**
    （两遍：先数、再写）—— 否则 30 万条决策 × (544+12×88) 的 float32 会把内存吃干净；
  · **按文件（= 整场）切分训练/验证**：同一场的后续决策**绝不能**进验证集（那是最隐蔽的信息泄漏，
    见 §5）。切分只由 `--split-seed` 决定，**与数据量无关** → 可复现。

产出（`<输出目录>/`）：
    train.npz / val.npz   定长数组（`state` / `cand` / `n_legal` / `label` / `hand_delta` / `game`
                          + P3 的 `file` / `hand_no` / `seat` / `placement` / `is_student`，见 `RL_COLUMNS`）
    meta.json             特征版本、维度、文件清单、切分参数、条数 —— 复现与排错都靠它
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import features

#: 候选数上限（一次询问的 `legal` 长度）：正常最多十几个，留足余量；超了直接报错而不是悄悄截断。
MAX_LEGAL = 64

#: 派生特征 sidecar 的魔数（Java `mahjong.train.TraceFeatures` 写）。
SIDECAR_MAGIC = 0x4D4A4654          # "MJFT"

#: P3（离线 RL）需要的额外列 —— 紧凑集里**没有它们就组不出转移**：
#:   · `file`      **切分内**的文件序号（0..n-1；`game` 在每个文件里都从 0 开始、跨来源会撞车）
#:   · `hand_no`   小局序号（同一小局内的决策才构成一条 episode）
#:   · `seat`      行动座位（`delta` 是四家的收支，得知道读哪一家）
#:   · `placement` 该座位的终局顺位 1..4（整场奖励；**-1 = 未知**，组转移时剔除而不是当第 0 名）
#:   · `is_student` 这一行是不是学生策略打的（teacher 与学生数据混着训时的 off-policy 诊断）
#: ⚠ 旧紧凑集没有这些列 → `load_split` 容忍缺失（返回 None），但 **P3 必须重新 build 一次**。
RL_COLUMNS = {"file": np.int32, "hand_no": np.int16, "seat": np.int8,
              "placement": np.int8, "is_student": np.uint8}


# ------------------------------------------------------------------ 读

def trace_files(src: str | Path) -> list[Path]:
    """目录里的 `g*.jsonl`（按场号数值排序 —— 决定切分顺序，必须稳定）。

    **也接受直接给一个 jsonl 文件**：`dagger.py` 的受控切分要"只把这几十场当验证集"
    （用 `val_frac=1.0` 建一个评测专用的紧凑集），把文件列表原样喂进来最不容易出错。
    """
    src = Path(src)
    if src.is_file():
        if src.suffix != ".jsonl":
            raise ValueError(f"{src} 不是 jsonl（目录或 g*.jsonl 都行）")
        return [src]
    files = sorted(src.glob("g*.jsonl"), key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0)
    if not files:
        raise FileNotFoundError(f"{src} 里没有 g*.jsonl（先用 --selfplay --out 采集）")
    return files


def trace_files_multi(srcs: list[str | Path]) -> list[Path]:
    """多个来源目录的轨迹（按 `(目录, 场号)` 排序 —— 顺序稳定，切分才可复现）。

    为什么要多来源：**DAgger 要把"学生跑出来的状态"和原来的 BC 数据合并训练**（P2），
    两份轨迹在两个目录里，直接并起来即可（文件名可能重名，但它们在不同目录、互不影响）。
    """
    out: list[Path] = []
    for s in srcs:
        out.extend(trace_files(s))
    return sorted(out, key=lambda p: (str(p.parent), int(p.stem[1:]) if p.stem[1:].isdigit() else 0))


def sidecar_path(jsonl: Path) -> Path:
    """`g0.jsonl` → `g0.feat.bin`（同目录、同名前缀）。"""
    return jsonl.parent / (jsonl.name.split(".")[0] + ".feat.bin")


def load_sidecar(path: str | Path) -> dict:
    """读派生特征 sidecar（三段式、小端；格式见 `mahjong.train.TraceFeatures` 的 javadoc）。

    返回 `danger[n,73]` / `nlegal[n]` / `cand[Σnlegal,8]`（都是只读视图）+ 候选偏移。
    `danger` 是**逐决策派生段**（v3 起 73 维：危险度 68 + 牌力/打点 5），int16 存盘。
    """
    p = Path(path)
    raw = p.read_bytes()
    if len(raw) < 20:
        raise ValueError(f"{p.name} 太短（不是 sidecar？）")
    magic, ver, ndec, pdec, pcand = struct.unpack_from("<5i", raw, 0)
    if magic != SIDECAR_MAGIC:
        raise ValueError(f"{p.name} 魔数不对：{magic:#x}（期望 {SIDECAR_MAGIC:#x}）")
    if ver != features.DERIVED_VERSION:
        raise ValueError(f"{p.name} 派生特征版本 {ver} != Python 侧 {features.DERIVED_VERSION}"
                         f" —— 重新 --features 或同步两边")
    if (pdec, pcand) != (features.DERIVED_DECISION, features.DERIVED_CANDIDATE):
        raise ValueError(f"{p.name} 派生块布局不符：perDec={pdec} perCand={pcand}"
                         f"（Python 侧期望 {features.DERIVED_DECISION}/"
                         f"{features.DERIVED_CANDIDATE}）—— 两边一起改并 +版本")
    off_b = 20 + ndec * pdec * 2                  # A 段是 int16（v2 起；见 TraceFeatures 的格式说明）
    nlegal = np.frombuffer(raw, dtype="<i2", count=ndec, offset=off_b)
    off_c = off_b + ndec * 2
    total = int(nlegal.sum())
    if off_c + total * pcand * 2 != len(raw):
        raise ValueError(f"{p.name} 长度不自洽：文件 {len(raw)} 字节，按头部推算应为 "
                         f"{off_c + total * pcand * 2}")
    danger = np.frombuffer(raw, dtype="<i2", count=ndec * pdec, offset=20).reshape(ndec, pdec)
    cand = np.frombuffer(raw, dtype="<i2", count=total * pcand, offset=off_c).reshape(total, pcand)
    return {"ver": ver, "n": ndec, "danger": danger, "nlegal": nlegal, "cand": cand,
            "offsets": np.concatenate([[0], np.cumsum(nlegal)]).astype(np.int64)}


def iter_decisions(files: list[Path]):
    """逐条产出 `(file, line_no, row)`；坏行直接报错（数据要可信，不要跳过）。"""
    for f in files:
        with f.open(encoding="utf-8") as fh:
            for ln, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("type") == "decision":
                    yield f, ln, row


def _spawn(jobs: list[list[str]], log_dir: Path) -> None:
    """起 `len(jobs)` 个**无管道**子进程（stdout/stderr 直接写日志文件），等它们全部成功结束。

    ⚠ **为什么不用 `multiprocessing.Pool`**：本机沙箱禁**命名管道**
    （`_winapi.CreateFile` → `PermissionError: [WinError 5]`），而 Pool 的队列正是命名管道
    ⇒ `Pool(...)` 在构造时就炸。子进程 + 文件同样是"真并行"，且没有任何管道。
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    procs = []
    for k, args in enumerate(jobs):
        log = (log_dir / f"job{k}.log").open("w", encoding="utf-8")
        procs.append((k, subprocess.Popen([sys.executable, "-m", "mahjong_ml.dataset", *args],
                                          stdout=log, stderr=subprocess.STDOUT), log))
    for k, p, log in procs:
        rc = p.wait()
        log.close()
        if rc != 0:
            tail = (log_dir / f"job{k}.log").read_text(encoding="utf-8", errors="replace")[-800:]
            raise RuntimeError(f"并行子进程 job{k} 退出码 {rc}：\n{tail}")


def _chunk_split(items: list, k: int) -> list[list]:
    """把列表切成 ≤k 段（尽量均匀；用于给子进程分工）。"""
    if not items:
        return []
    k = max(1, min(k, len(items)))
    per = (len(items) + k - 1) // k
    return [items[i:i + per] for i in range(0, len(items), per)]


def _count_file(f: Path) -> tuple[int, int]:
    """一个文件的 `(决策数, legal 最大长度)`。"""
    n = 0
    lmax = 0
    for _f, _ln, row in iter_decisions([f]):
        lmax = max(lmax, len(row.get("legal") or []))
        n += 1
    return n, lmax


def count_per_file(files: list[Path], max_decisions: int | None, workers: int = 1,
                   scratch: Path | None = None) -> tuple[list[int], int, int]:
    """第一遍（**可并行**）：每个文件的决策数（按 `max_decisions` 在文件边界截断）+ 总数 + lmax。

    ⚠ 与旧串行实现的**口径完全一致**：截断发生在"文件顺序 + 文件内行序"上，而且 lmax 只统计
    **被算进去的那 `take` 行** —— 否则 `cand` 的第二维会与串行版不同（字节就不再一致）。
    被截断的那个边界文件会**只重扫它的前 `take` 条**，代价 O(1 个文件)。

    @param scratch 子进程交换用的目录（**必须在工作区内**：沙箱给系统 Temp 的权限不一定对子进程开放）
    """
    if workers > 1 and len(files) > 1:
        # ⚠ 不能用 `tempfile.mkdtemp`：本机沙箱**拒绝对 mkdtemp 新建目录的写入**
        #   （实测 `Permission denied`，而 `mkdir` 建的目录正常）—— 所以自己 mkdir 一个固定名。
        tmp = (scratch or Path.cwd()) / f"_pcount-{os.getpid()}"
        tmp.mkdir(parents=True, exist_ok=True)
        chunks = _chunk_split([str(f) for f in files], workers * 4)
        jobs = []
        for k, ch in enumerate(chunks):
            req = tmp / f"req{k}.json"
            req.write_text(json.dumps(ch), encoding="utf-8")
            jobs.append(["_count", str(req), str(tmp / f"res{k}.json")])
        _spawn(jobs, tmp / "logs")
        res = []
        for k in range(len(chunks)):
            got = json.loads((tmp / f"res{k}.json").read_text(encoding="utf-8"))
            res.extend((int(a), int(b)) for a, b in got)
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        res = [_count_file(f) for f in files]
    counts: list[int] = []
    n = 0
    lmax = 0
    for c, lm in res:
        if max_decisions and n >= max_decisions:
            break
        take = c if not max_decisions else min(c, max_decisions - n)
        if take < c:                                   # 边界文件被截断 → 只按前 take 行算 lmax
            lm = 0
            for k, (_f, _ln, row) in enumerate(iter_decisions([files[len(counts)]])):
                if k >= take:
                    break
                lm = max(lm, len(row.get("legal") or []))
        counts.append(take)
        n += take
        lmax = max(lmax, lm)
    return counts, n, lmax


def count_decisions(files: list[Path], max_decisions: int | None) -> tuple[int, int]:
    """第一遍（串行口径，保留给自检/小数据用）：条数与最大 `legal` 长度。"""
    counts, n, lmax = count_per_file(files, max_decisions, workers=1)
    return n, lmax


# ------------------------------------------------------------------ 切分

def split_files(files: list[Path], val_frac: float, split_seed: int) -> tuple[list[Path], list[Path]]:
    """**按整场**（文件）切分 —— 用固定种子打乱后切，保证可复现且与数据量无关。"""
    rng = np.random.default_rng(split_seed)
    order = rng.permutation(len(files))
    n_val = max(1, int(round(len(files) * val_frac))) if len(files) > 1 else 0
    val_idx = set(order[:n_val].tolist())
    train = [f for i, f in enumerate(files) if i not in val_idx]
    val = [f for i, f in enumerate(files) if i in val_idx]
    return train, val


# ------------------------------------------------------------------ 写

def pick_label(row: dict, label_source: str) -> int:
    """选监督标签：`auto` = 有 teacher 标注就用它（DAgger），否则用 `chosen_index`。"""
    if label_source == "chosen":
        return int(row.get("chosen_index", -1))
    if label_source == "teacher":
        if "teacher_index" not in row:
            raise ValueError("label_source=teacher 但这条决策没有 teacher_index"
                             "（采集时要用 --teacher-label）")
        return int(row["teacher_index"])
    return int(row["teacher_index"]) if "teacher_index" in row else int(row.get("chosen_index", -1))


def _write_one_file(outs: dict, f: Path, file_id: int, i0: int, take: int, lmax: int,
                    label_source: str, require_derived: bool, base: int) -> tuple[int, int]:
    """把一个文件的决策写进 `outs` 的 `[i0, i0+take)` 行（**串行/并行共用同一份行逻辑**）。

    ⚠ 这是"逐条填数组"的唯一实现：并行版只是把**不同的行区间**分给不同进程（memmap 是文件映射，
    各进程写各自的切片），所以产物与串行**逐字节相同**是构造出来的，不是"大概一样"。
    """
    sc_path = sidecar_path(f)
    sc = None
    if sc_path.is_file():
        sc = load_sidecar(sc_path)
    elif require_derived:
        raise FileNotFoundError(
            f"缺派生特征 sidecar：{sc_path.name}\n"
            f"  先跑：java -jar server/build/mahjong-server.jar --features <轨迹目录>")
    written = 0
    used_teacher = 0
    # 该文件里的决策按行序与 sidecar 行序**一一对应**（同一遍遍历，顺序天然一致）
    row_idx = 0
    with f.open(encoding="utf-8") as fh:
        for ln, line in enumerate(fh, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "decision":
                continue
            if written >= take:
                break
            i = i0 + written
            obs = row.get("obs") or {}
            legal = list(row.get("legal") or [])
            ci = pick_label(row, label_source)
            if "teacher_index" in row and ci == int(row["teacher_index"]) \
                    and label_source != "chosen":
                used_teacher += 1
            if not legal or not (0 <= ci < len(legal)):
                raise ValueError(f"{f.name}:{ln} 标签={ci} 与 legal[{len(legal)}] 不匹配"
                                 f"（label_source={label_source}）")
            if "teacher" in row and row["teacher"] not in legal:
                raise ValueError(f"{f.name}:{ln} teacher={row['teacher']} 不在 legal 里")
            if len(legal) > lmax:
                raise ValueError(f"{f.name}:{ln} legal={len(legal)} 超过 lmax={lmax}")
            danger = None
            cand_derived = None
            if sc is not None:
                if row_idx >= sc["n"]:
                    raise ValueError(f"{f.name}:{ln} sidecar 行数不够（{sc['n']}）—— 重新 --features")
                if int(sc["nlegal"][row_idx]) != len(legal):
                    raise ValueError(
                        f"{f.name}:{ln} sidecar nLegal={int(sc['nlegal'][row_idx])} "
                        f"与 jsonl legal={len(legal)} 不一致 —— sidecar 是旧轨迹生成的，重新 --features")
                danger = sc["danger"][row_idx]
                off0, off1 = int(sc["offsets"][row_idx]), int(sc["offsets"][row_idx + 1])
                cand_derived = sc["cand"][off0:off1]
            outs["state"][i] = features.state_vector(obs, danger)
            # ⚠ 紧凑文件里派生候选量存**原始整数**（uint8 装得下），归一化只在 `bc._batch` 一处做
            full = features.candidates(outs["state"][i], legal, cand_derived, normalize=False)
            # 末尾 8 维是**原始整数**（归一化留给 _batch），写成 uint8 前先夹一下
            full[:, base:] = np.clip(full[:, base:], 0, 255)
            outs["cand"][i, :len(legal)] = full.astype(np.uint8)
            outs["nlegal"][i] = len(legal)
            outs["label"][i] = ci
            hd = row.get("hand_delta")
            outs["delta"][i] = hd if isinstance(hd, list) and len(hd) == 4 else [0, 0, 0, 0]
            outs["game"][i] = int(row.get("game", -1))
            seat_i = int(row.get("seat", -1))
            outs["file"][i] = file_id
            outs["hand_no"][i] = int(row.get("hand_no", -1))
            outs["seat"][i] = seat_i
            # 该座位的**终局顺位**（1..4）：整场奖励（P3 的终局项）。缺字段就填 -1（"未知"，
            # 组转移时会被剔除 —— 不能拿 0 冒充"第 0 名"）
            pl = row.get("placement")
            outs["placement"][i] = int(pl[seat_i]) if isinstance(pl, list) and len(pl) == 4 \
                and 0 <= seat_i < 4 else -1
            # 这一行是**学生策略**打的吗（P3 的 off-policy 诊断要用：teacher 数据与学生数据混着训）
            pol = str(row.get("policy", ""))
            outs["is_student"][i] = 1 if pol.startswith("net:") else 0
            written += 1
            row_idx += 1
    return written, used_teacher


#: 一份切分里的全部数组名（`_write_split` 建、`_write_chunk` 续写；顺序无关）
_OUT_ARRAYS = ("state", "cand", "nlegal", "label", "delta", "game", *RL_COLUMNS)


def _write_chunk(tasks: list[tuple], out_npz: Path, lmax: int, label_source: str,
                 require_derived: bool, base: int) -> tuple[int, int]:
    """并行工作单元：打开一次 memmap，把这一组 `(文件, file_id, i0, take)` 顺序写进去。"""
    outs = {name: np.lib.format.open_memmap(out_npz.with_suffix(f".{name}.npy"), mode="r+")
            for name in _OUT_ARRAYS}
    written = used = 0
    for f, file_id, i0, take in tasks:
        w, u = _write_one_file(outs, f, file_id, i0, take, lmax, label_source,
                               require_derived, base)
        written += w
        used += u
    for m in outs.values():
        m.flush()
    return written, used


def _write_split(files: list[Path], counts: list[int], n: int, lmax: int, out_npz: Path, dtype,
                 require_derived: bool = True, label_source: str = "auto",
                 workers: int = 1) -> tuple[int, int]:
    """把切分里的决策写进定长数组（内存映射，逐条填）。返回 `(写入条数, 用了老师标注的条数)`。

    ⚠ `cand` 固定用 **uint8** 存：前 88 维是 0/1 one-hot 与 ≤4 的计数，**末尾 8 维是派生量的
    *原始整数***（向听 0..8、枚数 ≤136 —— 都装得进 uint8）。归一化在 `bc._batch` 里做
    （除以 `features.DERIVED_SCALE_CAND`），这样紧凑文件仍然很小。`state` 用 `dtype`（默认 float16）。

    @param counts 第一遍按**同一截断口径**数出来的每文件行数（见 `count_per_file`）
    @param workers `>1` 时按文件分组并行写（各进程写 memmap 的不同行切片，产物与串行逐字节相同）
    @param label_source `auto`（有 `teacher_index` 就用它 —— DAgger）/ `chosen` / `teacher`
    """
    if n == 0:                                  # 切分为空（数据太少）时也要给出合法的空数组
        np.save(out_npz.with_suffix(".state.npy"), np.zeros((0, features.state_dim()), dtype))
        np.save(out_npz.with_suffix(".cand.npy"),
                np.zeros((0, lmax, features.cand_dim()), np.uint8))
        np.save(out_npz.with_suffix(".nlegal.npy"), np.zeros((0,), np.int16))
        np.save(out_npz.with_suffix(".label.npy"), np.zeros((0,), np.int16))
        np.save(out_npz.with_suffix(".delta.npy"), np.zeros((0, 4), np.int32))
        np.save(out_npz.with_suffix(".game.npy"), np.zeros((0,), np.int32))
        for name, dt in RL_COLUMNS.items():
            np.save(out_npz.with_suffix(f".{name}.npy"), np.zeros((0,), dt))
        return 0, 0
    state = np.lib.format.open_memmap(out_npz.with_suffix(".state.npy"), mode="w+",
                                      dtype=dtype, shape=(n, features.state_dim()))
    cand = np.lib.format.open_memmap(out_npz.with_suffix(".cand.npy"), mode="w+",
                                     dtype=np.uint8, shape=(n, lmax, features.cand_dim()))
    n_legal = np.lib.format.open_memmap(out_npz.with_suffix(".nlegal.npy"), mode="w+",
                                        dtype=np.int16, shape=(n,))
    label = np.lib.format.open_memmap(out_npz.with_suffix(".label.npy"), mode="w+",
                                      dtype=np.int16, shape=(n,))
    delta = np.lib.format.open_memmap(out_npz.with_suffix(".delta.npy"), mode="w+",
                                      dtype=np.int32, shape=(n, 4))
    game = np.lib.format.open_memmap(out_npz.with_suffix(".game.npy"), mode="w+",
                                     dtype=np.int32, shape=(n,))
    # P3（离线 RL）要的列：**没有它们就组不出转移**（见 `rewards.py` 的 docstring）
    rl = {name: np.lib.format.open_memmap(out_npz.with_suffix(f".{name}.npy"), mode="w+",
                                          dtype=dt, shape=(n,))
          for name, dt in RL_COLUMNS.items()}
    base = features.cand_dim() - features.DERIVED_CANDIDATE
    # 每个文件的行区间（前缀和）—— 行号只由"文件顺序 + 文件内行序"决定，与并行度无关
    tasks: list[tuple] = []
    off = 0
    for file_id, (f, c) in enumerate(zip(files, counts)):
        if c <= 0:
            continue
        tasks.append((f, file_id, off, c))
        off += c
    outs = {"state": state, "cand": cand, "nlegal": n_legal, "label": label, "delta": delta,
            "game": game, **rl}
    if workers > 1 and len(tasks) > 1:
        # 按**文件分组**切成 ≤workers 块（各块行区间连续且互不重叠 → 写 memmap 的不同切片）
        chunks = _chunk_split(tasks, workers)
        for m in outs.values():                 # 先落盘，并**关掉父进程的映射**：
            m.flush()                           # Windows 上 mmap 会锁文件，子进程打不开 r+
            m._mmap.close()                     # noqa: SLF001（numpy 没给公开的 close）
        tmp = out_npz.parent / "_parallel"
        req = tmp / "req.json"
        tmp.mkdir(parents=True, exist_ok=True)
        req.write_text(json.dumps({
            "out_npz": str(out_npz), "lmax": lmax, "label_source": label_source,
            "require_derived": require_derived, "base": base,
            "chunks": [[[str(f), file_id, i0, take] for f, file_id, i0, take in ch]
                       for ch in chunks],
        }, ensure_ascii=False), encoding="utf-8")
        _spawn([["_chunk", str(req), str(k)] for k in range(len(chunks))], tmp / "logs")
        res = [tuple(json.loads((tmp / f"res{k}.json").read_text(encoding="utf-8")))
               for k in range(len(chunks))]
        shutil.rmtree(tmp, ignore_errors=True)
        written = sum(int(w) for w, _u in res)
        used_teacher = sum(int(u) for _w, u in res)
        return written, used_teacher
    written = 0
    used_teacher = 0
    for f, file_id, i0, take in tasks:
        w, u = _write_one_file(outs, f, file_id, i0, take, lmax, label_source, require_derived, base)
        written += w
        used_teacher += u
    for m in outs.values():
        m.flush()
    return written, used_teacher


class _ChunkArgs:
    """把 `_write_chunk` 的固定参数绑成一个可 pickle 的可调用对象（`Pool.map` 只传数据块）。"""

    def __init__(self, out_npz: Path, lmax: int, label_source: str, require_derived: bool, base: int):
        self.out_npz = out_npz
        self.lmax = lmax
        self.label_source = label_source
        self.require_derived = require_derived
        self.base = base

    def __call__(self, tasks: list[tuple]) -> tuple[int, int]:
        return _write_chunk(tasks, self.out_npz, self.lmax, self.label_source,
                            self.require_derived, self.base)


def build(src: str | Path | list[str | Path], out_dir: str | Path, *, val_frac: float = 0.1,
          split_seed: int = 0,
          max_decisions: int | None = None, dtype=np.float16, quiet: bool = False,
          require_derived: bool = True, label_source: str = "auto",
          workers: int = 0) -> dict:
    """采集轨迹 → 紧凑数组。返回 `meta`（也写进 `<out_dir>/meta.json`）。

    @param src 一个目录，或**多个目录**（DAgger：把学生跑出来的状态并进 BC 数据一起训）
    @param require_derived 缺 `g*.feat.bin` 时是否报错（默认**报错**：悄悄用 0 会让训练与推理
                          的特征口径不一致 —— 那种 bug 根本查不出来）
    @param label_source `auto`（有 `teacher_index` 就用它 —— DAgger 的标注）/ `chosen` / `teacher`
    @param workers 并行进程数；`0` = 自动（≤75% 的核，上限 24）。
                   ⚠ 这一步原是**单核 Python**：一代 2000 场 ≈ 500 s，占整代（≈11 min）里的 8 分钟，
                   而采集只用 40 s ⇒ 它是 P5 唯一的瓶颈（2026-09-26 并行化）。
                   判据是**并行产物与串行逐字节相同**（`python/selfcheck.py` 钉着）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    srcs = [src] if isinstance(src, (str, Path)) else list(src)
    files = trace_files_multi(srcs)
    train_files, val_files = split_files(files, val_frac, split_seed)
    if workers <= 0:
        # 自动并行（≤75% 的核、上限 24）。判据：并行产物与串行**逐字节相同** —— 在真实语料上验过
        # （800 场 279,518 条决策：23 个文件 0 差异；截断路径 0 差异；且能逐字节复现训练用过的那份
        #  `compact/bc-v3-001`），并且 `python/selfcheck.py` 里钉着这条闸门。
        workers = max(1, min(24, (os.cpu_count() or 4) * 3 // 4))

    train_counts, n_train, lmax_train = count_per_file(train_files, max_decisions, workers, out_dir)
    val_counts, n_val, lmax_val = count_per_file(val_files, max_decisions, workers, out_dir)
    lmax = max(lmax_train, lmax_val)
    if lmax <= 0:
        raise ValueError("没有读到任何决策（目录/采样参数不对？）")
    if lmax > MAX_LEGAL:
        raise ValueError(f"legal 长度 {lmax} 超过上限 {MAX_LEGAL} —— 先查服务端动作空间")

    written_train, teacher_train = _write_split(
            train_files, train_counts, n_train, lmax, out_dir / "train.npz", dtype,
            require_derived, label_source, workers)
    written_val, teacher_val = _write_split(
            val_files, val_counts, n_val, lmax, out_dir / "val.npz", dtype,
            require_derived, label_source, workers)
    meta = {
        "feature_version": features.FEATURE_VERSION,
        "derived_version": features.DERIVED_VERSION,
        "has_derived": True,
        "state_dim": features.state_dim(),
        "cand_dim": features.cand_dim(),
        "max_legal": lmax,
        "dtype": np.dtype(dtype).name,
        "src": [str(s) for s in srcs],
        "label_source": label_source,
        "teacher_labeled_train": teacher_train,
        "teacher_labeled_val": teacher_val,
        "files_total": len(files),
        "train_files": [f.name for f in train_files],
        "val_files": [f.name for f in val_files],
        # ⚠ 名字会**跨目录重名**（多来源合并时每个目录都从 g0 开始），所以再记一份**全路径** ——
        #   `dagger.py` 靠它算"这一场是不是**两个模型都没训过**"（受控切分，见那边的注释）
        "train_files_path": [str(f) for f in train_files],
        "val_files_path": [str(f) for f in val_files],
        "split_seed": split_seed,
        "val_frac": val_frac,
        "max_decisions": max_decisions,
        "train_decisions": written_train,
        "val_decisions": written_val,
        # 这份紧凑集带了哪些 P3 列（旧数据集没有 → 读回来是 None，P3 会要求重建）
        "rl_columns": sorted(RL_COLUMNS),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    if not quiet:
        print(f"紧凑数据集：{out_dir}")
        print(f"  训练 {written_train} 条（{len(train_files)} 场）/ 验证 {written_val} 条"
              f"（{len(val_files)} 场），legal≤{lmax}，dtype={meta['dtype']}")
        print(f"  state {meta['state_dim']} 维 / cand {meta['cand_dim']} 维，"
              f"特征版本 {meta['feature_version']}，切分种子 {split_seed}，"
              f"标签源 {label_source}（训练集里 {teacher_train} 条用了老师标注）")
    return meta


# ------------------------------------------------------------------ 读回（训练用）

def load_split(out_dir: str | Path, split: str) -> dict:
    """读回一份切分（`train` / `val`）—— 用**内存映射**，训练时不会把整份数据读进 RAM。

    P3 的列（{@link RL_COLUMNS}）**旧数据集里没有** → 这里容忍缺失并返回 `None`，
    调用方（`rewards.py`）见到 `None` 时会明确报错告诉你要重建，而不是悄悄当 0 用。
    """
    out_dir = Path(out_dir)
    mm = lambda tag: np.load(out_dir / f"{split}.{tag}.npy", mmap_mode="r")   # noqa: E731
    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    if meta["feature_version"] != features.FEATURE_VERSION:
        raise ValueError(f"数据集特征版本 {meta['feature_version']} != 代码 "
                         f"{features.FEATURE_VERSION} —— 特征变了要重建数据集")
    out = {"state": mm("state"), "cand": mm("cand"), "n_legal": mm("nlegal"),
           "label": mm("label"), "delta": mm("delta"), "game": mm("game"), "meta": meta,
           # ⚠ 把切分名带上：`rewards.py` 要用它去 meta 里取 `train_files_path` / `val_files_path`
           #   （拿全路径才能把每一行对回 run 目录的 summary.json 读顺位点）
           "split": split}
    for name in RL_COLUMNS:
        p = out_dir / f"{split}.{name}.npy"
        out[name] = mm(name) if p.is_file() else None
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="轨迹 → 紧凑数组（特征唯一规格见 features.py）")
    ap.add_argument("cmd", choices=["build", "info", "_count", "_chunk"])
    ap.add_argument("src", help="轨迹目录（g*.jsonl）或已建好的数据集目录（info）")
    ap.add_argument("out", nargs="?", help="build 的输出目录")
    ap.add_argument("--src", action="append", default=[], dest="src_extra",
                    help="**再加一个**来源目录（可重复；DAgger 把学生状态并进 BC 数据用）"
                         "—— ⚠ dest 必须与位置参数 `src` **分开**：同名时 argparse 会把追加"
                         "作用在这个字符串上（`'str' object has no attribute 'append'`），"
                         "于是这个选项**静默不可用**（2026-09 修）")
    ap.add_argument("--label-source", default="auto", choices=["auto", "chosen", "teacher"],
                    help="auto（默认）= 有 teacher_index 就用它（DAgger 标注），否则用 chosen_index")
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--split-seed", type=int, default=0)
    ap.add_argument("--max-decisions", type=int, default=None,
                    help="每个切分最多取多少条（冒烟用）")
    ap.add_argument("--float32", action="store_true", help="用 float32 存（默认 float16）")
    ap.add_argument("--workers", type=int, default=0,
                    help="并行进程数（0 = 自动，≤75%% 的核、上限 24）；产物与串行逐字节相同")
    args = ap.parse_args(argv)

    if args.cmd == "_count":
        # 内部子命令（并行用的"无管道子进程"，见 `_spawn`）：给一批文件数条数与 lmax
        paths = [Path(p) for p in json.loads(Path(args.src).read_text(encoding="utf-8"))]
        Path(args.out).write_text(json.dumps([list(_count_file(p)) for p in paths]),
                                  encoding="utf-8")
        return 0
    if args.cmd == "_chunk":
        # 内部子命令：把 req.json 里第 k 组任务写进（父进程已建好并关闭的）memmap
        req_path = Path(args.src)
        req = json.loads(req_path.read_text(encoding="utf-8"))
        k = int(args.out)
        tasks = [(Path(f), int(fid), int(i0), int(take))
                 for f, fid, i0, take in req["chunks"][k]]
        w, u = _write_chunk(tasks, Path(req["out_npz"]), int(req["lmax"]), req["label_source"],
                            bool(req["require_derived"]), int(req["base"]))
        (req_path.parent / f"res{k}.json").write_text(json.dumps([w, u]), encoding="utf-8")
        return 0
    if args.cmd == "build":
        srcs = [args.src] + list(args.src_extra)
        out = args.out or (args.src + "-compact")
        build(srcs, out, val_frac=args.val_frac, split_seed=args.split_seed,
              max_decisions=args.max_decisions, label_source=args.label_source,
              workers=args.workers,
              dtype=np.float32 if args.float32 else np.float16)
    else:
        d = load_split(args.src, "train")
        print(json.dumps(d["meta"], ensure_ascii=False, indent=2))
        print("train.state", d["state"].shape, "cand", d["cand"].shape)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
