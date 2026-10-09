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
| （无新列）**引擎逐候选标签** | 由 `cand` / `effect` 现算 | `engine_best_indices()` ⇒ 每行一个"引擎最优候选"下标（W4 的模仿学习目标；`pretrain --il-weight`） |
| `value` | float32 | `final_scores[seat] − 起点`（**千点**；值头的 HL-Gauss 目标） |
| `rtg` | float32 | **逐决策** reward-to-go（千点）：`Σ_{本局及其后} 收支 + 终局余棒`（轨迹 `reward_to_go`）—— λ=1 的 GAE 目标；老轨迹写 NaN |
| `placement` | int8 | 该座位终局顺位 − 1（0..3；缺字段填 −1） |
| `seat` / `game` / `hand_no` | int8 / int32 / int16 | 溯源与按座位诊断 |
| `h0` | `[dm]` float32 | **窗口之前**的 GRU carry（W1b）：用**学生网**重放 `events[0, max(0,n-K))`（`cache.CarryTracker`）；只有 `carry_model` 非空时才建这一列 |
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
#: ⚠ 只为了 `DERIVED_CANDIDATE`（`cand.derived` 块的**实维数**）：`features` 是纯 numpy 模块，
#: 没有 torch 依赖 ⇒ 不会把 torch 拖进 `dataset` 的构建路径。
from .. import features as _f3
from . import blocks, spec, traces
#: ⚠ `cache` 只在模块层导入（它自己**惰性**引 torch，见 `cache.torch_encoder`）——
#: `model` / `export` 是 torch 重依赖，只在**真的带 carry 构建**时才在 `build` 里导入。
from . import cache as v4cache
#: **风格奖励塑形**（`--style-bonus`）：解析 / 逐小局指示量 / 白化 / 付奖账**都在那一份**里，
#: 这里只负责"什么时候调它"。⚠ 缺省（`--style-bonus ''`）时下面每一处都**一行都不走**
#: ⇒ 与加这个功能之前逐位相同（判据在 `python/selfcheck.py`）。
from . import style_reward as _style

#: 张量的存盘 dtype（float16：显存/磁盘减半，训练时再转 float32 —— 与 v3 紧凑集同一个取舍）
TENSOR_DTYPE = np.float16

#: 数据集格式版本（列增删要 +1）
#: v2 = W1b 起**可能**多一列 `h0`（窗口之前的 carry；只有给了 `carry_model` 才建 ——
#: 所以"版本 2"不等于"一定有这一列"，判据看 `meta.has_h0` / 列文件在不在）。
DATASET_VERSION = 2


def trace_dir_files(directory: str | Path) -> list[Path]:
    """目录里按序号排好的 `g*.jsonl`（复用 `v4/traces.py` 的闸门与排序）。"""
    return traces.trace_files(directory)


#: `_rank_points_of` 的记忆表：**键 = (路径, mtime_ns, 大小)**（见它的 docstring —— 文件变了
#: 键就变，不会把"当时还不存在/还是旧的"那个结果留在这里）。
_RANK_POINTS_CACHE: dict[tuple, dict] = {}


def clear_rank_points_cache() -> None:
    """清空 `_rank_points_of` 的记忆表（**判据/测试用**，生产路径不需要）。

    ⚠ 为什么必须有一个**显式**的清空口（2026-10 实测）：记忆键是 `(路径, st_mtime_ns, st_size)`，
    而 **Windows 的文件时间戳分辨率 ≈ 时钟 tick（~15.6 ms）** ⇒ "同一个 tick 内、**字节数不变**
    地重写同一个 `summary.json`"会算出**完全一样的键** ⇒ `_rank_points_of` 读回旧表
    （实测：背靠背两次 `write_text`，**90% 命中**；隔 >1 ms 才不命中）。
    生产路径靠"`summary.json` 是采集时一次写成的、不会原地改"回避它，但 `python/selfcheck.py`
    的**负向对照**恰恰要"改坏再读一次"—— 那时必须能看见新内容，**不能拿 sleep 赌 tick**。
    """
    _RANK_POINTS_CACHE.clear()


def _game_start_score(jsonl: Path) -> int:
    """该场的起点分（`game` 行的 `start_score`；缺了就按 25000 —— M.League 默认）。

    ⚠ 2026-10-05：只读**文件尾**（原来是 `for line in fh` 走完全文件——一场 1.9 MB、
    1000 场就是 1.9 GB 的白读）。`start_score` 只在最后那条 `game` 行上，而一行 ≤ 几十 KB，
    所以读尾部 1 MiB 足够；**读不出两行以上就退回整文件读**（不猜、不近似）。
    """
    size = jsonl.stat().st_size
    take = min(size, 1 << 20)
    tail = b""
    with jsonl.open("rb") as fh:
        if take:
            fh.seek(size - take)
            tail = fh.read(take)
    lines = [ln for ln in tail.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    if not lines or (take < size and len(lines) < 2):
        # 尾部窗口里只有一行（说明最后一行超过了 1 MiB）⇒ 退回原来的整文件读法
        last = None
        with jsonl.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    last = line
        if last is None:
            return 25000
        lines = [last]
    row = json.loads(lines[-1])
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


# ==================================================================== 引擎逐候选标签
#
# W4 第一步（`docs/VALVES-AND-FIXTURES.md` §3 的 W4）：给策略灌一路**稠密、低噪、非采样**的
# 监督信号。RL 那条路已证明"看不见自己的进步"（每轮只走 100~150 步、KL 预算用满也只有 0.05 nats，
# 效应小于闸门在小算力下的分辨率 ±2.35 顺位点），而**引擎原本就在每一行给出逐候选的牌效量** ——
# 它无采样噪声、无 critic、不花一格闸门算力，且**每一份现有紧凑集里都有**。
#
# 这一段的唯一职责：把"引擎的逐候选量"翻成**每行一个目标候选下标**（纯函数、确定性、可单测）。
# 它**不**碰权重、不碰张量通道、不新增列（老紧凑集直接用）。

#: 逐候选"引擎导航量"的列序 —— 与 `features.DERIVED_CANDIDATE` / Java `ObsFeatures.perCandidate`
#: **同一份顺序**（`cand.derived` 块就是这些量按 `features.DERIVED_SCALE_CAND` 归一化后的结果；
#: 老的 3 列 `effect` 是它的**前 3 维原值**）。除 `shanten_after` 外一律"越大越好"。
ENGINE_FEATURE_NAMES: tuple[str, ...] = (
    "shanten_after",     # 0：打完之后的向听（**听牌记 0**；只可能是 0..6）
    "advance_types",     # 1：进张**种数**（只在未听牌时有值，否则 0）
    "advance_tiles",     # 2：进张**枚数**（已扣掉可见牌；未听牌时有值）
    "wait_types",        # 3：听牌种数（只在听牌时有值）
    "wait_tiles",        # 4：听牌枚数
    "good_wait_types",   # 5：良形听牌种数
    "good_wait_tiles",   # 6：良形听牌枚数
    "dora_count",        # 7：打后手牌里的宝牌数（⚠ 本函数**不消费**它，见 `engine_best_index`）
)


def _lex_best(keys: list[np.ndarray], valid: np.ndarray, idx0: np.ndarray) -> np.ndarray:
    """按 `keys` 的顺序取**字典序最大**的候选下标（同分保留**更小下标**）。

    实现口径（避免"两把尺子"）：维护"当前最优下标"`idx` `[n]` 与"还没被淘汰的候选"`alive` `[n, L]`，
    逐个键比较 `key[row, i] > key[row, idx[row]]`；严格更大才改判（⇒ **平局时先到者胜**，
    即 `legal` 顺序），比较完这个键就把**严格更小**的候选淘汰掉。

    ⚠ **两个坑**（都是 2026-10-04 的边界单测抓出来的，别改回去）：

    1. **必须淘汰**：只比较"当前最优"而不淘汰的话，已经在前一个键上输掉的候选会在下一个键上
       跟"当前最优"**那个键的值**比较 —— 字典序于是变成"最后一个键说话"（`进张枚数 9 > 8` 的
       胜者被 `进张种数 2 < 9` 的败者翻掉）。所以每个键比完就把**严格更小**的淘汰掉。
    2. **取最大，不是取"第一个更大的"**：`better.argmax()` 给的是"第一个比当前最优大的候选"，
       而字典序要的是"alive 里这一键最大的那个" —— 写成前者时，两个都优于当前最优的候选会
       按**下标**而不是按**这一键的值**决出（上例里 `col4 = 2 vs 3` 会被判成 2 胜）。

    @param valid `[n, L]` 合法掩码（非法的一律淘汰）
    @param idx0  `[n]` 起步下标（只在 `keys` 为空时返回它 —— 正常路径每个键都会重算）
    """
    n, L = valid.shape
    if L == 0:
        # `L == 0`（这一行没有任何候选）是调用方的契约违反：这里必须炸而不是返回 0
        raise ValueError("逐候选引擎量宽度为 0：这一行没有任何候选可评")
    rows = np.arange(n)
    alive = np.asarray(valid, dtype=bool).copy()
    idx = np.asarray(idx0, dtype=np.int64).copy()
    for k in keys:
        kk = np.where(alive, k, -np.inf)
        # 这一键上 alive 里的**最大者**（`argmax` 取第一个最大值 ⇒ 平局取最小下标 = 牌序）
        idx = kk.argmax(axis=1)
        best_val = kk[rows, idx]
        alive &= kk >= best_val[:, None]
    return idx


def engine_best_indices(feats, nlegal, *, chunk: int = 65536) -> np.ndarray:
    """`[n, L, k]` 逐候选引擎量 + `[n]` 合法数 → `[n]` **引擎最优候选下标**（int64）。

    判据（刻意与 `ai/SearchPolicy.utility()` 同序，见它的类注释：`U = -1000·向听 + 2·进张枚数
    + 1·进张种类`，听牌那一支换成听牌枚数/良形枚数 ⇒ 在 1000 的权重下就是"向听优先"的字典序）：

    1. **全 0 行排第一**：`tsumo` / `ron` 这类**终局和牌**的派生行是**整行 0**
       （`ObsFeatures.perCandidate` 对它们直接 `return new int[PER_CANDIDATE]`）——
       引擎没给"打完之后"的形态，但"能和就和"是这一行唯一正确的答案。
       ⚠ 反过来也成立：**真实候选不可能整行 0**（听牌行的 `wait_types ≥ 1`，未听牌行的
       `shanten ≥ 1`），所以这条不会误吞普通候选 —— 这条不变量是 `cli il-check` 的判据之一。
    2. 本行**最好向听 == 0**（有候选听牌）⇒ 键 `(听牌枚数, 良形枚数, 听牌种数, 良形种数)`；
       键里带 `-向听` 打头是为了让"打完不听牌"的候选**永远输给**听牌候选。
    3. 否则 ⇒ 键 `(-向听, 进张枚数, 进张种数)`。
    4. 键全平 ⇒ **取最小下标**（下标就是 `legal` 顺序，`dataset` 在 build 期已硬校验两者对齐）
       —— 这就是"同分按牌序确定性打破平局"，也是"同分时不下任何判断"的诚实写法。
    5. ⚠ 只给 3 列（老 `effect` 列）时第 2 条的四个键里只剩 `-向听` ⇒ 听牌行**退化成下标序**
       （引擎量里没有听牌形，谁也分不出好坏）。要真正的听牌比较就给 8 列
       （`engine_feature_block` 从紧凑集的 `cand.derived` 块取）。
       ⚠ `dora_count`（第 7 列）**不参与**排序：宝牌是"打点"不是"牌效"，把它塞进平局判据
       会让这个标签的性质变掉（牌效标签应当只讲牌效）。

    @param feats  `[n, L, k≥3]`（float16/float64 都行；逐列单调变换不影响任何比较）
    @param nlegal 逐行合法数（**只评前 nlegal 个候选**；尾部的 padding 必须被排除）
    @param chunk  每批处理多少行（内存：float64 的 `[chunk, L, k]`；不是性能开关）
    """
    F = np.asarray(feats)
    if F.ndim != 3:
        raise ValueError(f"逐候选引擎量必须是 [n, L, k]（收到 shape={F.shape}）")
    n, L, k = F.shape
    if k < 3:
        raise ValueError(f"逐候选引擎量至少要 3 列（向听/进张种数/进张枚数），收到 {k} 列")
    nl = np.asarray(nlegal, dtype=np.int64)
    if nl.shape != (n,):
        raise ValueError(f"nlegal 形状 {nl.shape} != ({n},)")
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    # ⚠ `nlegal ≤ 0` 是数据坏了（那一行没有任何合法动作）：不猜、当场报错。
    #   注意 `nlegal > L` 同罪（候选比张量宽还多 ⇒ 下标会越界到 padding 之外）。
    if nl.min() < 1 or nl.max() > L:
        raise ValueError(f"nlegal 越界（min={int(nl.min())}, max={int(nl.max())}, L={L}）")
    out = np.empty(n, dtype=np.int64)
    cols = np.arange(L)
    for i0 in range(0, n, int(chunk)):
        blk = np.asarray(F[i0:i0 + int(chunk)], dtype=np.float64)
        nn = blk.shape[0]
        sub = nl[i0:i0 + nn]
        valid = cols[None, :] < sub[:, None]
        # ① 全 0 行（终局和牌）优先；`any` 沿候选维
        zero = valid & ~np.any(blk != 0.0, axis=2)
        idx0 = valid.argmax(axis=1)                     # 第一个合法候选（valid 至少一个 True）
        sh = np.where(valid, blk[:, :, 0], np.inf)
        tenpai = sh.min(axis=1) <= 0.0
        adv = _lex_best([-blk[:, :, 0], blk[:, :, 2], blk[:, :, 1]], valid, idx0)
        if k >= 7:
            wait = _lex_best([-blk[:, :, 0], blk[:, :, 4], blk[:, :, 6],
                              blk[:, :, 3], blk[:, :, 5]], valid, idx0)
        else:
            wait = adv                                 # 3 列口径：听牌行只能靠下标序（见 docstring ⑤）
        tgt = np.where(tenpai, wait, adv)
        win = zero.any(axis=1)
        if bool(win.any()):
            tgt = np.where(win, zero.argmax(axis=1), tgt)   # 第一个全 0 行 = 和牌动作
        out[i0:i0 + nn] = tgt
    return out


def engine_best_index(effect_row, nlegal=None, legal=None) -> int:
    """**一行的**引擎最优候选下标（`docs/VALVES-AND-FIXTURES.md` §3 W4 的纯函数）。

    ★ 单一实现：它直接调 `engine_best_indices`（一行一批）—— 所以"标量口径"与"整表的
    向量化口径"不可能漂移，判据只有一份。

    @param effect_row `[L, k≥3]`：该行**逐候选**的引擎量。列序见 `ENGINE_FEATURE_NAMES`
        （前 3 列 = 老的 `effect` 列：打后向听 / 进张种数 / 进张枚数；给到 7 列以上时
        第 4~7 列参与听牌行的比较）。
    @param nlegal 这一行的合法候选数（缺省 = `len(effect_row)`，即整行都合法）。
        **尾部的 padding 必须靠它排除**（紧凑集是定长张量，尾部是 0）。
    @param legal 这一行的动作键（`legal` 顺序 = 候选行顺序）—— **只用来核对长度**：
        它是"牌序"的载体（平局取下标的依据），长度不符说明调用方拿错了行，必须报错。
    """
    row = np.asarray(effect_row)
    if row.ndim != 2:
        raise ValueError(f"effect_row 必须是 [L, k]（收到 shape={row.shape}）")
    length = int(row.shape[0])
    n = length if nlegal is None else int(nlegal)
    if legal is not None:
        keys = list(legal)
        if len(keys) != n:
            raise ValueError(f"legal 有 {len(keys)} 条 != nlegal {n}（拿错了行？）")
    if not (1 <= n <= length):
        raise ValueError(f"nlegal {n} 越界（这一行只有 {length} 个候选槽位）")
    return int(engine_best_indices(row[None, :, :], np.array([n], dtype=np.int64))[0])


def engine_feature_block(cand) -> np.ndarray:
    """紧凑集的 `cand` 张量 → `[n, L, 8]` 逐候选引擎量（`cand.derived` 块的**实前 8 维**）。

    为什么不是只读 `effect` 列（3 维）：那 3 维里没有听牌形 —— 听牌行（实测约占 20%，且是
    最该打对的那一段）的 `(向听, 进张种数, 进张枚数)` 全是 `(0, 0, 0)`，标签会退化成下标序
    = 往策略里灌噪声。`cand.derived` 块里**本来就有** `wait_*/good_wait_*`（同一个引擎调用产出，
    也**已经在模型输入里**），所以这不是新增信息、不破坏"训练输入 == 推理输入"。

    ⚠ 块的声明宽度是 11（`CAND_DERIVED`），但**实维只有 8**（`features.DERIVED_CANDIDATE`），
    后 3 维是 v4 预留位、当前三端生产者恒写 0 ⇒ 这里只取实前 8 维（否则那 3 个恒 0 列会变成
    "平局键"混进比较）。

    ⚠ `block_slices()` 给的偏移是**通道**偏移（与 `blocks.zeroBlock` 的 `cand[:, 88:99] = 0` 同一套），
    所以切的是**最后一维**：`cand[n, L, 88:96]` —— 别切成 `cand[n, 88:96, :]`（那个切的是候选维，
    结果是空数组，而且**不报错**，只会静默给出 0 行候选）。
    """
    tensor, s0, w0 = spec.block_slices()["cand.derived"]
    if tensor != "cand":
        raise spec.ContractError(f"`cand.derived` 挂在 {tensor} 上（期望 cand）—— 注册表变了？")
    n_real = int(_f3.DERIVED_CANDIDATE)
    if w0 < n_real:
        raise spec.ContractError(f"`cand.derived` 宽 {w0} < 实维 {n_real}：注册表与 features 脱节")
    out = cand[:, :, s0:s0 + n_real]
    if int(out.shape[1]) == 0:
        raise spec.ContractError(
            f"逐候选引擎量取出来是空的（shape={out.shape}）—— 通道偏移 {s0} 与候选维弄反了？")
    return out


def engine_targets(data: dict) -> np.ndarray:
    """紧凑集（`load_split` 的返回）→ 逐行**引擎最优候选下标** `[n]`（int64）。

    ⚠ **硬拒**"逐候选引擎量整块为 0"：那是 2026-09-27 那个静默坑（sidecar 没给 `cand`，
    `cand[88:128]` 整块 0 而训练照跑）。这里若整块 0，"引擎标签"根本不存在 —— 继续跑就是
    拿一个凭空造出来的下标去训策略，宁可当场报错（`train()` 在 `--il-weight 0` 时把它降级成
    **一行醒目告警 + 读数不可用**，而不是静默）。
    """
    feats = engine_feature_block(data["cand"])
    nleg = np.asarray(data["nlegal"], dtype=np.int64)
    n = int(nleg.shape[0])
    mx = 0.0
    for i0 in range(0, n, 65536):
        mx = max(mx, float(np.abs(np.asarray(feats[i0:i0 + 65536], dtype=np.float32)).max()))
    if mx == 0.0:
        raise spec.ContractError(
            "紧凑集的 `cand.derived`（逐候选引擎量）**整块为 0** ⇒ 引擎标签不存在"
            "（症状与 `compact/v4-bc-002` 同源：sidecar 缺逐候选段而构建期没拦）。"
            "重新 `dataset build`，不要在这份数据上开 `--il-weight`")
    return engine_best_indices(feats, nleg)


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
    # `rtg`：**逐决策** reward-to-go（千点）—— λ=1 的 GAE 目标（`A = R_tg − V(s)`）。
    #   2026-09-27 加（第六轮）：此前只有整场结果（`value`），优势里没有"这一手之后发生了什么"。
    #   ⚠ 老轨迹没有 `reward_to_go` ⇒ 这里写 **NaN**（显式缺席）：`--value-target rtg` 会当场报错，
    #   而 `final` 口径照常能跑老数据集 —— 不静默填 0（那会把优势变成 `0 − V(s)`）。
    "rtg": (np.float32, ()),
    # `rank_points`：该行所属**整场**的顺位点（`summary.json` 的 `per_game[g].rank_points[seat]`）。
    #   2026-09-28 加（§14 P1）：训练回报要与**评测口径**同量纲 —— 评测读的是 `rank_points`，
    #   而原来只有点数差（`value`/`rtg`）。
    #   ⚠ **不在 Python 里重算 uma/oka**（那是 `Payments`/`Settlement` 的活，重写必然漂移）：
    #   直接读生产者登记的 `summary.json`；缺它就写 NaN，`--rank-weight > 0` 会当场报错。
    "rank_points": (np.float32, ()),
    # `h0`（W1b）：**窗口之前**的长程 carry（GRU 隐状态），形状 `[dm]`（dm = 学生网的 d_model，
    #   开列时才定）。它是"训练侧的整手 carry" —— 上线端（Java/C++）算的是同一个量
    #   （从 0 起逐事件推进整手），所以训练与推理同源。
    #   ⚠ 尾形状写成占位符 `"dm"`：`open_columns` 把它换成实参（没给就**不建这一列**，
    #   见 `build`/`open_columns` 的注释 —— 不静默建一个宽度错的列）。
    #   ⚠ 用 float32 而**不是** float16（其它张量列）：它是隐状态、逐维门控 `fusion.mem` 的输入，
    #   16 位尾数（~1e-3 相对误差）会让"h0 是不是同一个量"这条判据失去意义。
    "h0": (np.float32, ("dm",)),
    # `style_bonus`（风格奖励塑形）：**点**，逐决策行；同一小局的四行同值（它是小局级量）。
    #   ⚠ 缺省（`--style-bonus ''`）**不建这一列**（与 `h0` 同一个做法）⇒ 老紧凑集读回 `None`，
    #   `pretrain` 一行都不走。⛔ 它**不**折进 `delta` 列：奖励只在优势那一处叠一次，
    #   折进 `delta` 会波及 `_row_weights`（RWR）与 audit 的口径（"叠几次"就说不清了）。
    _style.COLUMN: (np.float32, ()),
    "aux_opp_hand": (np.uint8, (3, 34)),
    "aux_opp_tenpai": (np.uint8, (3,)),
    "aux_opp_dealin": (np.uint8, (3,)),
    "aux_own_shanten_after": (np.int8, ()),
    "aux_own_tenpai": (np.uint8, ()),
    "aux_win_flag": (np.uint8, ()),
}


def open_columns(out_dir: Path, tag: str, n: int, lmax: int,
                 mode: str = "w+", dm: int | None = None,
                 style: bool = False) -> dict[str, np.memmap]:
    """建/开这一份切分的全部列（`mode="w+"` 建、`"r+"` 由子进程开）。

    @param dm 学生网的 `d_model` —— 它只用来定 `h0` 列的宽度（`_COLUMNS` 里的 `"dm"` 占位符）。
        **`dm=None` ⇒ 不建 `h0` 列**（= 这一份没有长程 carry 的老行为；`build` 已经在 meta 里
        记了 `has_h0=False` 并打了醒目提示）。给了 `dm` 就必须是整数：形状错在这里、写盘时才炸
        的话，症状是"训练跑起来了但 h0 全是垃圾"，而那是静默的。
    @param style 是否建**风格奖励**那一列 `style_bonus`（`--style-bonus` 给了才 True）。
        ⚠ 与 `dm` 同一个做法：**没开就不建这一列**（不建一个全 0 的列 —— 那会让训练端
        "以为有塑形"却拿到 0，正是本仓最忌讳的静默降级）。
    """
    if dm is not None and int(dm) <= 0:
        raise spec.ContractError(f"dm 必须是正的 d_model（给了 {dm!r}）")
    out: dict[str, np.memmap] = {}
    for name, (dt, tail) in _COLUMNS.items():
        if name == _style.COLUMN and not style:
            continue
        shape = [n]
        for t in tail:
            if t == "lmax":
                shape.append(int(lmax))
            elif t == "dm":
                if dm is None:
                    break               # 没有 carry ⇒ 这一列**不建**（不是建一个 0 宽的）
                shape.append(int(dm))
            else:
                shape.append(int(t))
        else:
            out[name] = np.lib.format.open_memmap(out_dir / f"{tag}.{name}.npy", mode=mode,
                                                  dtype=dt, shape=tuple(shape))
    return out


def _rank_points_of(f: Path) -> dict[int, list[float]]:
    """`<run>/summary.json` → `{game: rank_points[4]}`（**生产者登记的权威值**）。

    为什么读 summary 而不是自己算：uma/oka 在 `Payments`/`Settlement`（Java/C++）里，
    Python 重写一份必然漂移（v3 `rewards.py` 的同一条纪律）。缺文件就返回空表 ⇒ 列写 NaN。

    ⚠ 2026-10-05 起**按 (路径, mtime, 大小) 记忆**：一场调一次、而 1000 场共用同一个
    `summary.json`（实测 446 KB）—— 原来是"每场都重新读盘 + `json.loads` 一遍"，
    1000 场白解析 ~450 MB。记忆的**键带 `st_mtime_ns`/`st_size`**（不是只按路径）：
    `python/selfcheck.py` 会**先在同一个目录里跑一次没有 `summary.json` 的 build、之后再把
    它写出来**（`v4-rt-src`）—— 只按路径记忆会命中那个"当时没有文件"的空表，
    回填当场报"没有 summary.json"（第一次改动就踩到了，自检抓到）。
    """
    p = f.parent / "summary.json"
    try:
        st = p.stat()
        key = (str(p), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        key = (str(p), -1, -1)
    hit = _RANK_POINTS_CACHE.get(key)
    if hit is not None:
        return hit
    out: dict[int, list[float]] = {}
    if p.is_file():
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            s = {}
        for g in s.get("per_game") or []:
            rp = g.get("rank_points")
            if isinstance(g.get("game"), int) and isinstance(rp, list) and len(rp) == 4:
                out[int(g["game"])] = [float(x) for x in rp]
    _RANK_POINTS_CACHE[key] = out
    return out


def _write_file(mm: dict, f: Path, i0: int, take: int, *, lmax: int, aux: bool,
                student: str | None = None, carry=None, bonus=None) -> None:
    """把一个文件的**前 `take` 条**决策写进 `mm` 的 `[i0, i0+take)` 行。

    ⚠ 串行与并行**共用这一份行逻辑**（并行只是把不同文件分给不同进程，各写各的连续行区间）
    ⇒ "并行产物 == 串行产物"是构造出来的，不是大概一样。

    @param carry `seat -> CarryTracker` 的**工厂**（`None` = 这一份没有 `h0` 列）。
        ⚠ 工厂而不是实例：一个文件里四家的决策是**交错**的（同一手事件流被四条座位各自推进），
        所以按座位各留一个 tracker，在**这个文件内复用**（同一手的事件只重放一次）。
    @param bonus `style_reward.BonusTracker`（`None` = 这一份没有 `style_bonus` 列）。
        ⚠ 它必须**已经 `prebuild` 过这个文件**：`style_bonus` 是小局级量，而小局的结局只有扫完
        整小局才知道 ⇒ "边走边算"写不出第一行（详见 `BonusTracker` 的 docstring）。
        查不到小局**当场报错**（不填 0）。
    """
    if carry is not None and "h0" not in mm:
        raise spec.ContractError(
            "要写 `h0` 列但这一份 mm 里没有它 —— `open_columns` 时漏了 `dm`（并行分支要从 "
            "req.json 读回同一个 dm）。**不静默跳过**：跳过就等于训练端拿到一份没有长程 carry 的"
            "数据，而上线端用整手 carry")
    if bonus is not None and _style.COLUMN not in mm:
        raise spec.ContractError(
            f"要写 `{_style.COLUMN}` 列但这一份 mm 里没有它 —— `open_columns` 时漏了 `style=True`"
            f"（并行分支要从 req.json 读回同一个开关）。**不静默跳过**：跳过就等于这些行的风格奖励"
            f"恒为 0，而训练照跑")
    start = _game_start_score(f)
    rp_of_game = _rank_points_of(f)
    if bonus is not None:
        # ★ 风格奖励：**先把这个文件的每个小局都算出来**（查表 ⇒ 行序无关、第一行也有值）。
        bonus.prebuild(f)
    ax = _aux.load_aux(_aux.aux_path(f)) if aux else None
    axcols = _aux_arrays(ax, int(ax["n"]) if ax else 0)
    sc = traces.load_sidecar(_v3.sidecar_path(f))
    trackers: dict[int, object] = {}
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
            # ★ 风格奖励：**查表**取这一行的 bonus（`prebuild` 已经把本文件所有小局算好了）。
            #   查不到 ⇒ `BonusTracker.value` **当场报错**（不填 0 —— 填 0 是静默丢奖励）。
            b_row = 0.0 if bonus is None else float(bonus.value(row))
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
            # ③ `rtg`：逐决策 reward-to-go（点 → 千点）—— 离线 PPO 的 λ=1 目标（第六轮）。
            #    ⚠ 缺席写 NaN 而不是 0：0 会让优势变成 `0 − V(s)`（看着能跑，其实回报没了）。
            rtg = row.get("reward_to_go")
            mm["rtg"][i] = float(rtg) / 1000.0 if isinstance(rtg, (int, float)) else np.nan
            # ④ `rank_points`：整场顺位点（**生产者登记的** `summary.json`；缺就 NaN）
            rp = rp_of_game.get(int(row.get("game", -1)))
            mm["rank_points"][i] = rp[seat] if (rp is not None and 0 <= seat < 4) else np.nan
            if carry is not None:
                # ⑤ `h0`（W1b）：**窗口之前**的 carry —— 训练侧的长程记忆入口。
                #   口径三条（`build` 里也写着同一份，别两处抄歪）：
                #   ① **一律用学生网**（= 这一代的 `--student` 那个网）算，**不**按行换网；
                #   ② 事件是公开的 ⇒ 同一批事件在"学生网的编码器"下的 carry 是良定义的；
                #   ③ 训练起点 = 现任 = 上线部署的那份权重 ⇒ 训练与推理同源。
                #   ⚠ 权重在训练中会移动 ⇒ `h0` 是**构建期冻结**的（与 PPO 的行为策略同类的
                #   off-policy 现象）；**每代重建数据集**即复位（这正是 `v4 loop` 的节奏）。
                #   ⚠ 事件行怎么编码**不在这里管**：`EventStream`/`blocks.event_matrix` 负责
                #   （按 obs.seat 编码、新事件在尾部）—— 自己拼 token 就是第三条口径、必漂移。
                evts = list(obs.get("events") or [])
                seat_of_stream = int(obs.get("seat", seat))     # 编码按 obs.seat（与 evt 张量同源）
                tk = trackers.get(seat_of_stream)
                if tk is None:
                    tk = trackers[seat_of_stream] = carry(seat_of_stream)
                # ★ **只重放"窗口之外"的那一段前缀**（2026-10-05；这是一行纯提速、不含任何近似）：
                #   `h0(n) = carry(events[0, max(0, n-K)))` —— 递推是**前缀函数**，所以窗口内的
                #   `events[n-K, n)` 对 `h0` **没有任何影响**。原先每次都把全部 n 条事件推一遍，
                #   于是"窗口早就装得下"的那些决策（实测占 96%，一手事件数 ≤ K=60 的占绝大多数）
                #   白跑 `EventStream.step` → `event_matrix` → `enc` → `GRUCell`。
                #   截断到 `max(0, n-K)` 之后：`hist` 的末项恰好是 `count == want` ⇒ `h0()` 命中同一格。
                #   ⚠ 三条不变量（改这里必须守住）：
                #   ① 截断长度**单调不减**（同一手内事件只追加）⇒ `advance` 的前缀校验照旧成立；
                #   ② 换手/换文件时长度会变短 ⇒ `_is_extension` 判 False ⇒ `reset()`（本来也要重置）；
                #   ③ `_write_file` 里**只有** `tk.h0(len(evts))` 用得到状态，而
                #      `h0` 只要 `[0, n-K]` 那一段 —— 谁要再拿这个 tracker 做别的事（例如需要
                #      "当前 carry"、`CarryTracker.h`），**必须**改回推全量，否则语义就错了。
                want_n = max(0, len(evts) - spec.K_EVT)
                # `key` = 小局身份：`obs.events` **每小局清零**（`Round.events`）⇒ 换局必须重放，
                # 不能靠"新流的前几条恰好等于旧流尾部"这种小概率去蒙（见 `CarryTracker.advance`）。
                tk.advance(evts[:want_n], key=(int(row.get("game", -1)), int(row.get("hand_no", -1))))
                h0 = tk.h0(len(evts))
                if h0 is None:
                    mm["h0"][i] = 0.0                           # 窗口之前没有事件 ⇒ 全 0 冷启动
                else:
                    mm["h0"][i] = np.asarray(h0.detach().cpu().numpy(),
                                             dtype=np.float32).reshape(-1)
            if ax is not None:
                mm["aux_opp_hand"][i] = axcols["opp_hand"][j]
                mm["aux_opp_tenpai"][i] = axcols["opp_tenpai"][j]
                mm["aux_opp_dealin"][i] = axcols["opp_dealin"][j]
                mm["aux_own_shanten_after"][i] = axcols["own_shanten_after"][j]
                mm["aux_own_tenpai"][i] = axcols["own_tenpai"][j]
                mm["aux_win_flag"][i] = axcols["win_flag"][j]
            if bonus is not None:
                # ★ 风格奖励塑形的那一列（点；同一小局四行同值 —— 它是**小局级**量）。
                mm[_style.COLUMN][i] = b_row
            j += 1
    if bonus is not None:
        # 判据：预扫出来的小局必须覆盖写出去的每一行（`value` 已经对"查不到"报错了，
        # 这里再钉一条"本文件查到的次数 == 本文件写的行数"——两边漏一边都会让奖励错位而训练照跑）。
        got = bonus.cache_hits - bonus.hits0
        if got != j:
            raise spec.ContractError(
                f"{f.name}: `style_bonus` 查到的行数 {got} != 写出去的行数 {j} "
                f"—— 有行没查到（会被填 0）或有行查重了")
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

    ⚠ **子进程必须带 `_v3.CHILD_ENV`（BLAS/OMP 线程 = 1）**：compact 这一相每个子进程都要跑 torch
    （`h0` 的 GRU 重放），而 torch 的 intra-op 线程数默认 = **物理核数**。`--workers 12` 时那就是
    `12 × 24 = 288` 个线程抢 32 个逻辑核 —— 实测（同机、同 raw）`--workers 12` 相对串行只有
    **~2.5× 的加速**（200 场：串行外推 230 s → 实测 90.5 s；1000 场联赛 453 s），
    而钉到 1 之后同一份工作只要 21.9 s / 87.8 s。
    数值上**逐字节不变**（每步 GEMM 的 M 极小、只沿输出列分块 ⇒ K 方向的求和顺序与线程数无关），
    判据仍是 SHA256 逐文件对拍。
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    procs = []
    for k, args in enumerate(jobs):
        log = (log_dir / f"job{k}.log").open("w", encoding="utf-8")
        procs.append((k, subprocess.Popen([sys.executable, "-m", "mahjong_ml.v4.dataset", *args],
                                          stdout=log, stderr=subprocess.STDOUT,
                                          env=_v3.CHILD_ENV), log))
    for k, p, log in procs:
        rc = p.wait()
        log.close()
        if rc != 0:
            tail = (log_dir / f"job{k}.log").read_text(encoding="utf-8", errors="replace")[-800:]
            raise RuntimeError(f"v4 数据集并行子进程 job{k} 退出码 {rc}：\n{tail}")


def pin_torch_threads() -> int:
    """把 torch 的 **intra-op 线程钉到 1**（返回原来的线程数，供日志）。**纯提速、不改数值**。

    ## 为什么要钉（2026-10-05 实测）

    `h0` 的递推是**逐事件**的（`CarryTracker.advance`），每次都是 `[1,C] × [C,d]` 的小 GEMM。
    这种尺寸下 per-op 的线程唤醒/栅栏开销**远大于**计算本身：实测同一个 `enc+GRUCell` 步
    在 `num_threads=24` 下 **115 µs**、`=1` 下 **52 µs**（2.2×）。串行构建因此白等一倍时间。

    并行分支更糟：`--workers 12` 时是 `12 个子进程 × 24 线程 = 288` 个线程抢 32 个逻辑核
    （子进程还各自持有一份 MKL/OpenMP 池）——实测同机同 raw 下 `--workers 12` 相对串行只有
    ~2.5× 的加速（200 场：串行外推 230 s → 90.5 s；1000 场联赛 453 s）。

    ## 为什么**逐字节不变**是构造出来的

    线程数只决定"从哪一维切分工作"：这些算子的 M（行）极小、K（归约维）固定，
    oneDNN/MKL 对 `M=1` 的 GEMM 只会沿 **N（输出列）** 分块 ⇒ 每个输出元素的
    **K 方向求和顺序与线程数无关**，逐位结果不变。判据不靠这段推理，靠
    **SHA256 逐文件对拍**（20 场构建产物与改动前完全相同）。
    """
    import torch
    old = int(torch.get_num_threads())
    if old != 1:
        torch.set_num_threads(1)
    return old


def student_net_path(spec_or_path: str | None) -> str | None:
    """`net:<路径>[@<α>][#<T>]` → `<路径>`（剥掉两个后缀）；不是 `net:` 串就返回 `None`。

    ⚠ **只解析、不换网**：`h0` 一律用**学生网**算（见 `build` 的注释）。
    ⚠ 这里**不校验文件存在**（调用方决定"不存在"怎么办）—— `--student` 的主用途是
    `is_student` 的**字符串比对**，它不要求权重在本机（自检的玩具紧凑集就是一个不存在的串）。
    """
    s = str(spec_or_path or "").strip()
    if not s.lower().startswith("net:"):
        return None
    p = s[4:]
    if "#" in p:                                # 文法：`net:<路径>[@<α>][#<T>]` ⇒ 先剥 `#T`
        p, _, _t = p.rpartition("#")
    if "@" in p:                                # 再剥 `@α`
        p, _, _a = p.rpartition("@")
    p = p.strip()
    return p or None


def build(src: str | Path, out_dir: str | Path, *, val_frac: float = 0.05, split_seed: int = 0,
          aux: bool = False, limit_files: int | None = None, workers: int = 0,
          quiet: bool = False, student: str | None = None,
          carry_model: str | None = None, style_bonus: str = "",
          style_whiten: str = "student") -> dict:
    """轨迹目录 → v4 数据集（`train.*.npy` / `val.*.npy` 各一组列 + `meta.json`）。

    @param aux 是否要求并读取标签侧 `g*.aux.npz`（信念/危险头的监督来源）
    @param student 本轮要训练的那一代的**确切策略串**（写 `is_student` 列；None = 全部算学生）
    @param carry_model `h0` 列（**窗口之前**的 GRU carry，W1b）用哪个网算；缺省 = 从 `student`
        解析（`net:<路径>[@α][#T]` → `<路径>`）。`None` ⇒ **不建 `h0` 列**（老行为）：
        meta 里 `has_h0=false` / `carry_model=null`，并打一行醒目提示（训练端会因此**硬拒**，
        除非显式 `--no-carry` —— 见 `pretrain.train`）。
    @param style_bonus **风格奖励塑形**（`--style-bonus`）：`"riichi=1.0:no_deal"` 这种写法
        （文法见 `style_reward.parse_bonus`）。**空串（缺省）= 不建 `style_bonus` 列、一行都不走**
        ⇒ 与加这个功能之前逐位相同。非空时：先按**本数据集**实测算白化参数 μ/σ（范围见
        `style_whiten`），再把逐小局的 `Σ w·1[PAIR]·(x−μ)/σ`（点）写进 `style_bonus` 列，
        并把 w/μ/σ/付奖小局数/付奖总额落盘到 `<out>/style-reward.json`（**可审计**）。
    @param style_whiten 白化参数的**统计总体**：`student`（缺省）= 只用学生座位的小局
        （= 训练里真正进梯度的那些行）；`all` = 全部座位。
        ⚠ 用 `all` 会把 teacher/对手的行为分布混进 μ/σ（风格桌上学生只占 1/4）⇒ 缺省 student；
        没给 `--student` 时自动退回 `all`（没有学生掩码可用，且**会打印出来**）。
    @param workers 并行进程数（0 = 自动，≤75% 的核、上限 12）；**产物与串行逐字节相同**是判据
        （各进程只写自己那段连续行区间，行逻辑只有 `_write_file` 一份）

    ★ `h0` 的口径（**三条，写死在这里，别在别处再抄一份**）：

    ① **一律用"学生网"**（= 本轮要训练的那一代，也就是 `--student net:<...>` 指的那个网）算，
       **不要**按每一行自己的 `policy` 换网。理由：事件是公开的 ⇒ 同一批事件在"学生网的编码器"
       下的 carry 是良定义的；**若按行换网**，老师行/对手行的 `h0` 会退化成 0（他们的网不在本机、
       或根本不是这一代的编码器），模型就能从"`h0` 是否为 0"反推出 `is_student`（**隐式泄漏**）；
       而训练起点的学生网 == 现任网 == 上线部署的那个网 ⇒ 训练与推理同源。
    ② 生产端（Java `V4Policy`）算的是"**从 0 起逐事件推进整手事件**"的 carry；离线端给的是同一个量：
       `h0 = 重放 events[0, max(0, n-K))`，再由窗口那 60 步把它推进成 `h_evt` —— 两边同一把递推。
    ③ 权重在训练中会移动，所以 `h0` 是**构建期冻结**的（与 PPO 的行为策略同类的 off-policy 现象）；
       **每代重建数据集**即复位（`v4 loop` 的节奏本来就是"每代重采重建"）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if workers <= 0:
        workers = max(1, min(12, (os.cpu_count() or 4) * 3 // 4))
    # ---- `h0` 用哪个网：显式 `--carry-model` 优先；否则从学生串解析（见 docstring 的★口径）----
    dm: int | None = None
    carry_spec: str | None = None
    if carry_model is not None:
        carry_spec = student_net_path(carry_model) or str(carry_model)
        if not Path(carry_spec).is_file():
            # ⚠ **不静默跳过**：显式点名了用哪个网算 `h0`，文件不在就报错 —— 悄悄退化成
            #   "没有 h0"会让训练端拿到一份与上线端不同源的数据，而那是不报错的。
            raise spec.ContractError(f"`carry_model` 指向的权重文件不存在：{carry_spec} —— "
                                     f"`h0` 必须由**学生网**算，别跳过这一列")
    elif student:
        cand = student_net_path(student)
        if cand is not None and Path(cand).is_file():
            carry_spec = cand
        elif cand is not None and not quiet:
            # `--student` 的主用途是字符串比对（写 `is_student`），它不要求权重在本机
            # （自检的玩具紧凑集就是一个不存在的串）⇒ 这里只**醒目提示**、不当成错误。
            print(f"⚠ `--student {student}` 指向的权重不存在（{cand}）⇒ **这一份没有长程 carry**："
                  f"`h0` 列不建，训练会退化成「窗口冷启动」，而上线端用整手 carry"
                  f"（两边不是同一个量）。要 `h0` 就传一个真实存在的 `net:<...>`，"
                  f"或显式 `--carry-model <net.bin>`")
    if carry_spec is not None:
        # torch 只在**带 carry 的构建**里才需要：纯 numpy 的构建路径（`carry_model=None`）
        # 不该因为环境里没有 torch 就跑不动。
        from . import export as v4export
        from . import model as M
        nthr = pin_torch_threads()                  # 逐事件小 GEMM：线程 >1 只会更慢（见该函数）
        parsed = v4export.read_net(carry_spec)
        dims = {k: int(v) for k, v in parsed["dims"].items()}
        net = M.build(**dims)                       # `n_heads` 已在 `read_net` 的 dims 里
        net.load_state_dict(v4export.state_from_net(parsed), strict=True)
        net.eval()
        net.requires_grad_(False)                   # 算 `h0` 是**推理**：不建图、不更新这个网
        dm = int(dims["d_model"])
        # ⚠ encoder 只建一次、四条座位共用（同一个网）；tracker 每座位一个（`_write_file` 里）
        _encoder = v4cache.torch_encoder(net)
        if not quiet:
            print(f"长程 carry：`h0` 列用**学生网** {carry_spec} 算（d_model={dm}；"
                  f"逐事件重放整手、构建期冻结 —— 权重在训练中会移动，每代重建即复位）")
            print(f"  torch intra-op 线程：{nthr} → 1（逐事件小 GEMM 下线程只会更慢；数值逐位不变）")
    else:
        _encoder = None
    if carry_spec is None and not quiet:
        # ⚠ 醒目提示（不是安静的 debug 行）：这一份数据**教不出长程记忆**，
        #   而上线端（Java/C++）是喂整手 carry 的 ⇒ 训练/推理两个量。
        print("=" * 78)
        print("⚠⚠ 这一份紧凑集**没有长程 carry**（`h0` 列未建）：训练会用**窗口冷启动**，")
        print("    而上线端用**整手 carry** ⇒ 两边不是同一个量。`pretrain` 会因此**硬拒**，")
        print("    除非显式 `--no-carry`。要 `h0` 就用 `--student net:<真实存在的网>`")
        print("    （或 `--carry-model <net.bin>`）重建数据集。")
        print("=" * 78)

    def make_carry(seat: int):
        """按座位建一个 tracker（`_write_file` 在**一个文件内**复用同一个座位的实例）。"""
        return v4cache.CarryTracker(_encoder, int(seat))

    carry_factory = make_carry if _encoder is not None else None
    files = trace_dir_files(src)
    if limit_files:
        files = files[:limit_files]
    # ---- ★ 风格奖励塑形：**这一份数据集上的**白化参数（μ/σ）-------------------------------
    # 口径三条（`style_reward` 的模块 docstring 里是同一份，别两处抄歪）：
    #   ① μ/σ **每代现算**（策略分布、桌上对手每代都在变；拿上一代的 μ/σ 白化这一代 = 白化到错的分布）；
    #   ② 白化的**统计总体** = 学生座位（缺省）—— 训练里真正进梯度的就是那些行；
    #   ③ 白化正确性当场自证（均值 ≈ 0、方差 ≈ 1），数字进日志与 `<out>/style-reward.json`。
    spec_raw = (style_bonus or "").strip()
    bonus_spec = _style.parse_bonus(spec_raw) if spec_raw else None
    bonus_on = bool(bonus_spec)
    student_policies: set[str] | None = None
    whitening: dict = {}
    style_audit = None
    style_check: dict | None = None

    def _all_hands():
        """全量小局流（白化统计与自证**共用同一个生成器函数** ⇒ 两遍扫的是同一批样本）。"""
        for f in files:
            yield from _style.iter_hands(f)

    if bonus_on:
        if style_whiten == "student":
            if student:
                student_policies = {student}
            else:
                # 没有学生掩码 ⇒ 只能按全部座位白化。**打印出来**（口径变了必须看得见）。
                print("⚠ `--style-bonus` 要按**学生座位**白化，但没给 `--student`："
                      "没有学生掩码可用 ⇒ 白化退回**全部座位**（μ/σ 里会混进 teacher/对手的分布）")
                student_policies = None
        elif style_whiten == "all":
            student_policies = None
        else:
            raise spec.ContractError(
                f"`--style-bonus-whiten` 只认 student|all（收到 {style_whiten!r}）")
        whitening, style_audit = _style.whitening_stats(
            bonus_spec, _all_hands(), student_policies=student_policies)
        style_check = _style.audit_whitening(
            bonus_spec, whitening, _all_hands(), student_policies=student_policies)
        # ⛔ 白化自证不过就**当场报错**：白化是这次改动的**全部要点**（不白化 = 方差不均照旧支配梯度）。
        if not style_check["_all_ok"]["ok"]:
            raise spec.ContractError(
                f"风格奖励的白化自检没过（要求均值 ≈ 0、方差 ≈ 1）："
                f"{ {k: v for k, v in style_check.items() if k != '_all_ok'} }")
    elif style_whiten not in ("student", "all"):
        raise spec.ContractError(f"`--style-bonus-whiten` 只认 student|all（收到 {style_whiten!r}）")
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
        mm = open_columns(out_dir, tag, n, lmax, mode="w+", dm=dm, style=bonus_on)
        # 每个文件的**行区间**（行号只由"文件顺序 + 文件内行序"决定，与并行度无关）
        tasks: list[tuple[Path, int, int]] = []
        off = 0
        for f, c in zip(part, counts):
            if c > 0:
                tasks.append((f, off, c))
            off += c
        # 串行分支自己累积一份风格奖励的账（并行分支由子进程各写各的行，那一份账只在串行时可得）
        serial_audit: dict = {}
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
                # ⚠ 子进程**自己载网、自己建 tracker**（把 `carry_model` 与 `dm` 一起带过去）：
                #   漏了 `dm` 就会少建 `h0` 列（`open_columns` 只在给了 dm 时才建它），
                #   而"少一列"在写盘时才炸 ⇒ 这里两个键都要有，`_chunk` 也会再校验一次。
                "carry_model": carry_spec, "dm": (None if dm is None else int(dm)),
                # ⚠ 风格奖励的**三样**都要带过去：开关、白化参数（μ/σ，父进程按**全量**算的）、
                #   以及 spec 原文（子进程自己解析）。漏一样都会静默写出错的列 —
                #   漏 `style` 会让子进程少写一列（写盘时才炸），漏 `stats` 会让负号反过来。
                "style": bool(bonus_on),
                "style_spec": (spec_raw if bonus_on else None),
                "style_stats": (_style.stats_to_json(whitening) if bonus_on else None),
                # ⚠ `own_policies` 也要带：子进程要按同一批"学生席"算小局级奖励 ——
                #   不带的话子进程会按四席取或算（并行产物 ≠ 串行产物，而那是判据）。
                "style_own": (sorted(student_policies) if student_policies else None),
                "chunks": [[[str(f), int(i0), int(take)] for f, i0, take in ch] for ch in chunks],
            }, ensure_ascii=False), encoding="utf-8")
            _spawn([["_chunk", str(req), str(k)] for k in range(len(chunks))], tmp / "logs")
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            tracker = _style.BonusTracker(bonus_spec, whitening,
                                          own_policies=student_policies) if bonus_on else None
            for f, i0, take in tasks:
                _write_file(mm, f, i0, take, lmax=lmax, aux=aux, student=student,
                            carry=carry_factory, bonus=tracker)
            if tracker is not None:
                serial_audit.update(tracker.audit())
            for m in mm.values():
                m.flush()
        # 逐决策 reward-to-go 的**覆盖率**（NaN = 老轨迹没有这个字段 ⇒ `--value-target rtg`
        # 会在入口处硬拒，不留"静默用 0 当回报"的口子）。
        # ⚠ 并行分支里父进程的 memmap 已经显式 close 过（Windows 句柄），所以**重新开一份**读。
        rtg_frac = float(np.isfinite(
            np.asarray(np.load(out_dir / f"{tag}.rtg.npy", mmap_mode="r")[:n],
                       dtype=np.float32)).mean())
        # 顺位点覆盖率（NaN = 没有 `summary.json` ⇒ `--rank-weight > 0` 会在入口硬拒）
        rank_frac = float(np.isfinite(
            np.asarray(np.load(out_dir / f"{tag}.rank_points.npy", mmap_mode="r")[:n],
                       dtype=np.float32)).mean())
        # ★ `h0` 的**覆盖率自证**：非全 0 的行占比。为什么这能当判据：采集里必然有一批
        #   `n > K` 的行（一手的事件数远超 60）⇒ 非 0 是**必然**的。若为 0，只可能是算错了
        #   （载错了网 / 没喂进去 / 前缀口径写反）—— 那就**当场报错**，绝不写出一份全 0 的列
        #   （全 0 的列 = 训练时"长程记忆恒为冷启动"，而训练照跑、指标照出）。
        h0_frac: float | None = None
        if dm is not None:
            h0 = np.asarray(np.load(out_dir / f"{tag}.h0.npy", mmap_mode="r")[:n], dtype=np.float32)
            h0_frac = float((np.abs(h0).sum(axis=1) > 0).mean())
            if not h0_frac > 0.0:
                raise spec.ContractError(
                    f"`h0` 列**全 0**（{tag}：{n} 行 / {len(part)} 场，d_model={dm}）—— "
                    f"窗口之前的 carry 不可能是全 0：采集里必然有一批事件数 > K={spec.K_EVT} 的行。"
                    f"查 ① `carry_model` 是不是学生网那份权重；② 事件流有没有真的喂进 tracker"
                    f"（`CarryTracker.advance` 的前缀校验是不是每次都判失败、退化成冷启动）。"
                    f"**不写出一个全 0 的列**")
        # ★ 风格奖励那一列的**覆盖率自证**（与 `h0` 同一套精神，但判据不同）：
        #   这一列**允许**有 0（没被付奖的小局就是 0），所以不能拿"非全 0"当判据。
        #   ⚠ 判据是"**同一小局的每一行同值**"。奖励是**小局级**量（`style_reward.hand_bonus` 的
        #   ★★ 注释解释了为什么必须如此：训练端按小局取第一行的值 ⇒ 按席位给会被行序偶然吃掉）。
        style_info: dict | None = None
        if bonus_on:
            col = np.asarray(np.load(out_dir / f"{tag}.{_style.COLUMN}.npy", mmap_mode="r")[:n],
                             dtype=np.float32)
            nz = int((col != 0.0).sum())
            if nz == 0:
                raise spec.ContractError(
                    f"`{_style.COLUMN}` 列**全 0**（{tag}：{n} 行）—— 一件奖励都没付出去。"
                    f"只可能是：① 成对质量轴恒假（`{spec_raw}` 里的 PAIR 在本桌上从不成立）；"
                    f"② 白化把 `(x−μ)/σ` 算成了 0；③ 预扫根本没喂进去。**不写出一个全 0 的列**")
            gm = np.asarray(np.load(out_dir / f"{tag}.game.npy", mmap_mode="r")[:n], dtype=np.int64)
            hn = np.asarray(np.load(out_dir / f"{tag}.hand_no.npy", mmap_mode="r")[:n],
                            dtype=np.int64)
            key = gm * 100003 + hn          # 小局键（`hand_no` 远小于 100003 ⇒ 不撞车）
            order = np.argsort(key, kind="stable")
            ks, vs = key[order], col[order]
            same = ks[1:] == ks[:-1]
            spread = float(np.abs(vs[1:][same] - vs[:-1][same]).max()) if bool(same.any()) else 0.0
            if spread > 0.0:
                raise spec.ContractError(
                    f"`{_style.COLUMN}` 在同一个 (game, hand_no) 内**不同值**"
                    f"（最大偏差 {spread:g}）—— 它是**小局级**量，同一小局的每一行必须同值。"
                    f"预扫/查表的键写错了")
            style_info = {"nonzero_rows": nz, "rows": int(n), "nonzero_frac": nz / max(1, n),
                          "hands": int(np.unique(key).size), "same_value_within_hand": True}
        return {"files": [f.name for f in part], "decisions": n, "lmax": lmax,
                "rtg_frac": rtg_frac, "rank_frac": rank_frac, "h0_frac": h0_frac,
                "style": style_info, "serial_style_audit": dict(serial_audit)}

    train = one_split(train_files, "train")
    val = one_split(val_files, "val")
    n_all = max(1, train["decisions"] + val["decisions"])
    h0_frac_all = (None if dm is None else
                   (train["h0_frac"] * train["decisions"] + val["h0_frac"] * val["decisions"])
                   / n_all)
    # ---- ★ 风格奖励：付奖账（逐轴 w/μ/σ/付奖小局数/付奖总额）落盘，**可审计** ---------------
    reward_audit_path = None
    if bonus_on:
        # 账按**训练切分**重算一遍（父进程；与写列时用的是同一份 `indicator_values`/`bonus_of`）。
        # 目的：给出"每个轴付了多少小局、一共付了多少点" —— 这是"奖励有没有真的生效"的第二条读数
        # （第一条是 `style_bonus` 列非全 0，由 `one_split` 断言）。
        accounting = _style.paid_accounting(bonus_spec, whitening, _all_hands())
        for row in style_audit.axes:
            pa = accounting["per_axis"].get(row["axis"], {})
            row["paid_hands_all_seats"] = pa.get("paid_hands")
            row["paid_sum_all_seats"] = pa.get("paid_sum")
            row["raw_sum_all_seats"] = pa.get("raw_sum")
        reward_audit_path = _style.write_audit(
            out_dir, style_audit, style_check,
            extra={"accounting_all_seats": accounting,
                   "column": {"train": train["style"], "val": val["style"]},
                   "serial_tracker_train": train["serial_style_audit"],
                   "serial_tracker_val": val["serial_style_audit"]})
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
                   "ctx": [spec.C_CTX], "cand": ["lmax", spec.C_CAND],
                   **({"h0": [int(dm)]} if dm is not None else {})},
        "src": str(src),
        "train_decisions": train["decisions"], "val_decisions": val["decisions"],
        "train_files": train["files"], "val_files": val["files"],
        "lmax": max(train["lmax"], val["lmax"]),
        "val_frac": val_frac, "split_seed": split_seed,
        "student": student,
        "rtg_frac": {"train": train["rtg_frac"], "val": val["rtg_frac"]},
        "rank_frac": {"train": train["rank_frac"], "val": val["rank_frac"]},
        # ---- W1b：长程 carry（`h0` 列）的三条自证 ----
        "has_h0": bool(dm is not None),
        "carry_model": carry_spec,                  # `None` ⇒ 这一份没有 `h0` 列
        "h0_dim": (None if dm is None else int(dm)),
        # 非全 0 的行占比（**全 0 会被上面的 build 直接判错**；这里留数字给验收/台账）
        "h0_nonzero_frac": h0_frac_all,
        "h0_nonzero_frac_splits": {"train": train["h0_frac"], "val": val["h0_frac"]},
        # ---- ★ 风格奖励塑形（`--style-bonus`）：**默认关闭**（`enabled=False`，不建列）----
        # 口径：白化参数从**本数据集**实测（`whiten_scope` 说明用的是哪一份总体），
        # 逐行 bonus（点）写在 `style_bonus` 列；奖励在 `pretrain._hand_advantage` 里**只叠一次**。
        "style_reward": {
            "enabled": bool(bonus_on),
            "spec": (spec_raw or None),
            "column": (_style.COLUMN if bonus_on else None),
            "whiten_scope": (style_audit.whiten_scope if bonus_on else None),
            "whitening": (_style.stats_to_json(whitening) if bonus_on else None),
            "whitening_check": (style_check if bonus_on else None),
            "audit_path": (str(reward_audit_path) if reward_audit_path else None),
        },
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
        print(f"  逐决策 reward-to-go 覆盖：train {train['rtg_frac']:.1%} / val {val['rtg_frac']:.1%}"
              f"（0% = 老采集器；`--value-target rtg` 会在入口报错）")
        print(f"  顺位点覆盖：train {train['rank_frac']:.1%} / val {val['rank_frac']:.1%}"
              f"（0% = 采集目录里没有 summary.json；`--rank-weight > 0` 会在入口报错）")
        if dm is None:
            print("  长程 carry：**无**（`h0` 列未建）—— 训练会退化成窗口冷启动")
        else:
            print(f"  长程 carry：`h0[{dm}]` 非全 0 的行 "
                  f"train {train['h0_frac']:.1%} / val {val['h0_frac']:.1%}"
                  f"（其余行的事件数 ≤ K={spec.K_EVT} ⇒ 窗口之前没有事件、carry 全 0 是正确的）")
        if not bonus_on:
            print("  风格奖励塑形：**关闭**（没给 `--style-bonus`）—— 不建 `style_bonus` 列，"
                  "训练与加这个功能之前逐位相同")
        else:
            print(f"  风格奖励塑形：`{spec_raw}`（白化范围 {style_audit.whiten_scope}；"
                  f"μ/σ 从**本数据集**实测）")
            for row in style_audit.axes:
                print(f"    轴 {row['axis']}: w={row['weight']:g} 质量轴={row['pair']}  "
                      f"μ={row['mu']:.6f} σ={row['sd']:.6f}  "
                      f"付奖小局(学生范围) {row['paid_hands']}  总额(学生范围) {row['paid_sum']:+.1f}  "
                      f"| 全部座位 {row.get('paid_hands_all_seats')} / "
                      f"{row.get('paid_sum_all_seats'):+.1f}")
            print(f"    白化自检（均值≈0、方差≈1）："
                  f"{ {k: (round(v['mean'], 12), round(v['var'], 12)) for k, v in style_check.items() if k != '_all_ok'} }"
                  f" ⇒ {'✅ 通过' if style_check['_all_ok']['ok'] else '❌ 没过'}")
            print(f"    逐行 `{_style.COLUMN}` 非 0 行占比：train {train['style']['nonzero_frac']:.1%} / "
                  f"val {val['style']['nonzero_frac']:.1%}（同一小局四行同值已断言）")
            print(f"    奖励账（可审计）：{reward_audit_path}")
    return meta


def backfill_rank_points(out_dir: str | Path, src_dir: str | Path, *,
                         quiet: bool = False) -> dict:
    """给**已存在**的紧凑集补 `rank_points` 列（**不重算任何张量**）—— 老数据集升级用。

    为什么需要它：`rank_points` 是 2026-09-28（§14 P1）才加的列，而 `compact/v4-sp-004` 那种
    15 GB 的老数据集重算一遍纯属浪费 —— 这一列只依赖 `(game, seat)` 与采集目录的 `summary.json`，
    而 `game`/`seat` 本来就在紧凑集里。

    判据（不满足就报错，不静默写坏数据）：
    ① 覆盖率：每一行都要能查到（`game`/`seat` 越界或 summary 缺那一场 = 报错）；
    ② **同一 `(game, seat)` 上恒定**（顺位点是整场属性）；
    ③ 每场四家之和 ≈ 0（顺位点零和 —— 这是精算的硬性质，能抓住"取错座位"这类错位）。
    """
    out_dir, src_dir = Path(out_dir), Path(src_dir)
    meta_p = out_dir / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    rp_of = _rank_points_of(src_dir / "g0.jsonl")
    if not rp_of:
        raise SystemExit(f"{src_dir} 下没有 summary.json（或没有 per_game.rank_points）—— "
                         f"没有权威顺位点可补；要么把 summary.json 放回去，要么重采")
    gmax = max(rp_of) + 1
    table = np.full((gmax, 4), np.nan, dtype=np.float64)
    for g, vals in rp_of.items():
        table[g] = vals
    out: dict = {}
    for split in ("train", "val"):
        gp, sp = out_dir / f"{split}.game.npy", out_dir / f"{split}.seat.npy"
        if not gp.is_file() or not sp.is_file():
            continue
        game = np.asarray(np.load(gp, mmap_mode="r"), dtype=np.int64)
        seat = np.asarray(np.load(sp, mmap_mode="r"), dtype=np.int64)
        bad = (game < 0) | (game >= gmax) | (seat < 0) | (seat > 3)
        if bool(bad.any()):
            raise SystemExit(f"{split}：有 {int(bad.sum())} 行的 (game, seat) 越界 —— 不猜，先查数据")
        vals = table[game, seat]
        if not np.isfinite(vals).all():
            miss = sorted({int(g) for g in game[~np.isfinite(vals)]})[:5]
            raise SystemExit(f"{split}：有 {int((~np.isfinite(vals)).sum())} 行查不到顺位点"
                             f"（缺的场号示例 {miss}）—— summary.json 与紧凑集不是同一批")
        # ② 同一 (game, seat) 恒定
        key = game * 4 + seat
        order = np.argsort(key, kind="stable")
        ks, vs = key[order], vals[order]
        first = np.ones(ks.size, dtype=bool)
        first[1:] = ks[1:] != ks[:-1]
        uniq_k, uniq_v = ks[first], vs[first]
        spread = 0.0
        for k, v in zip(uniq_k, uniq_v):
            sel = vals[key == k]
            spread = max(spread, float(np.abs(sel - v).max()))
        if spread > 1e-6:
            raise SystemExit(f"{split}：顺位点在同一个 (game, seat) 上不恒定（最大偏差 {spread:g}）")
        # ③ 零和
        smax = 0.0
        for g in np.unique(game):
            smax = max(smax, abs(float(np.nansum(table[g]))))
        if smax > 1e-3:
            raise SystemExit(f"{split}：顺位点四家之和不为 0（最大 |Σ| = {smax:g}）—— 座位错位了")
        # ⚠ 用 `np.save` 写**普通文件**，不走可写 memmap：这一列只有 4 B/行（658k 行 ≈ 2.6 MB），
        #   而"建 w+ memmap 再被别人 mmap_mode='r' 读"在 Windows 上踩过
        #   `OSError: [Errno 22] Invalid argument`（自检里当场红）。读侧仍然是 `np.load(mmap_mode="r")`。
        np.save(out_dir / f"{split}.rank_points.npy", vals.astype(np.float32))
        out[split] = {"decisions": int(game.size), "rank_frac": 1.0,
                      "per_game": int(np.unique(game).size)}
    meta.setdefault("rank_frac", {})["backfilled_from"] = str(src_dir)
    for split, info in out.items():
        meta["rank_frac"][split] = info["rank_frac"]
    meta["source"] = str(meta.get("source", "")) + "（rank_points 由 summary.json 回填）"
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    if not quiet:
        print(f"已回填 `rank_points`：{out_dir}")
        for split, info in out.items():
            print(f"  {split}：{info['decisions']} 行 / {info['per_game']} 场 / 覆盖率 100%，"
                  f"恒定性与零和都通过")
    return out


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
                 "placement", "seat", "game", "hand_no", "is_student", "delta", "rtg",
                 "rank_points", "h0", _style.COLUMN,
                 "aux_opp_hand", "aux_opp_tenpai",
                 "aux_opp_dealin", "aux_own_shanten_after", "aux_own_tenpai", "aux_win_flag"):
        p = out_dir / f"{split}.{name}.npy"
        # ⚠ 缺席写 `None`（**存在才读**）：`h0` 是 W1b 才有的列、`style_bonus` 是风格奖励才有的列，
        #   老紧凑集/没开塑形的紧凑集都没有它们 —— 训练端据此**硬拒**或**一行都不走**
        #   （见 `pretrain.train`），不静默退化成冷启动、也不静默把塑形当成 0。
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
    ap.add_argument("--carry-model", default=None, metavar="NET.BIN",
                    help="算 `h0`（**窗口之前**的 GRU carry，W1b）用哪个网 —— 缺省空 = 从 `--student` "
                         "解析（`net:<路径>[@α][#T]`）。⚠ 一律用**学生网**：见 build 的口径注释。"
                         "给了它却找不到文件会**报错**（不静默跳过这一列）")
    ap.add_argument("--workers", type=int, default=0,
                    help="并行进程数（0 = 自动，≤75%% 的核、上限 12）；产物与串行逐字节相同")
    ap.add_argument("--style-bonus", default="", metavar="SPEC",
                    help="**风格奖励塑形**（缺省空 = 关闭，与加这个功能之前逐位相同）。"
                         "文法 `NAME[=w]:PAIR[,NAME2=w2:PAIR2]`，例如 `riichi=1.0:no_deal`"
                         "（立直率 ↑，但**只有该小局未放铳**才付奖）。轴表见 "
                         "`mahjong_ml/v4/style_reward.py`；⛔ 不写 PAIR 直接报错（不许只按立直付奖）")
    ap.add_argument("--style-bonus-whiten", choices=["student", "all"], default="student",
                    help="白化参数 μ/σ 的统计总体：`student`（缺省）= 只用学生座位的小局"
                         "（= 训练里真正进梯度的行）；`all` = 全部座位。"
                         "⚠ 没给 `--student` 时自动退回 `all` 并打印告警")
    ap.add_argument("--backfill-rank", action="store_true",
                    help="**只回填 `rank_points` 列**（src = 采集目录（含 summary.json），"
                         "out = 已存在的紧凑集；不重算张量）—— 老数据集升级用")
    args = ap.parse_args(argv)
    if args.cmd == "_chunk":
        # 内部子命令（并行用；见 `_spawn`）：把 req.json 里第 k 组任务写进（父进程已建好的）列
        req = json.loads(Path(args.src).read_text(encoding="utf-8"))
        k = int(args.out)
        out_dir = Path(req["out_dir"])
        tag = req["tag"]
        tasks = [(Path(f), int(i0), int(take)) for f, i0, take in req["chunks"][k]]
        n = int(np.load(out_dir / f"{tag}.nlegal.npy", mmap_mode="r").shape[0])
        # ⚠ 子进程**自己载网**（不共享父进程的内存）：行逻辑仍只有 `_write_file` 一份，
        #   所以"并行产物 == 串行产物"依旧是构造出来的（不是"大概一样"）。
        carry_spec = req.get("carry_model")
        dm = req.get("dm")
        if carry_spec is not None and dm is None:
            # 父进程带了 carry、子进程却没拿到 `dm` ⇒ 会少建 `h0` 列。宁可当场报错，
            # 也不要写出一份"列少了但跑完"的数据集（那正是静默降级）。
            raise spec.ContractError(
                f"{Path(args.src).name} 里有 `carry_model` 却没有 `dm` —— 并行分支漏传了列宽，"
                f"会少建 `h0` 列；这是构建器自己的 bug，不猜、直接报错")
        carry_factory = None
        if carry_spec is not None:
            from . import export as v4export
            from . import model as M
            pin_torch_threads()
            parsed = v4export.read_net(carry_spec)
            dims = {kk: int(v) for kk, v in parsed["dims"].items()}
            net = M.build(**dims)
            net.load_state_dict(v4export.state_from_net(parsed), strict=True)
            net.eval()
            net.requires_grad_(False)
            if int(dims["d_model"]) != int(dm):
                raise spec.ContractError(
                    f"req.json 的 dm={dm} != 权重 {carry_spec} 的 d_model={dims['d_model']} "
                    f"—— 并行分支的列宽与父进程不一致")
            _enc = v4cache.torch_encoder(net)

            def _make(seat: int):
                return v4cache.CarryTracker(_enc, int(seat))

            carry_factory = _make
        # ★ 风格奖励：子进程用**父进程算好的** μ/σ（它按全量算的，子进程只看得到自己那几场文件 ——
        #   各算各的会得到**不同的**白化参数，那是最隐蔽的一类"并行产物 ≠ 串行产物"）。
        style_on = bool(req.get("style"))
        style_spec = _style.parse_bonus(req.get("style_spec")) if style_on else None
        style_stats = _style.stats_from_json(req.get("style_stats") or {}) if style_on else {}
        if style_on and not style_stats:
            raise spec.ContractError("req.json 里有 `style` 却没有 `style_stats`（μ/σ）—— "
                                     "并行分支漏传了白化参数，写出来的列会与串行不同")
        mm = open_columns(out_dir, tag, n, int(req["lmax"]), mode="r+", dm=dm, style=style_on)
        if carry_factory is not None:
            # `open_columns(mode="r+")` 是**打开已有文件**（numpy 会忽略传入的 shape/dtype、
            # 按文件头走）⇒ 这里再自己核一次宽度：错了就会把别的宽度写进去（静默）。
            if "h0" not in mm:
                raise spec.ContractError(f"{tag} 里没有 `h0` 列（父进程建列时漏了 dm？）")
            got = int(mm["h0"].shape[1])
            if got != int(dm):
                raise spec.ContractError(f"{tag}.h0.npy 的宽度 {got} != req 的 dm={dm}")
        for f, i0, take in tasks:
            tracker = (_style.BonusTracker(style_spec, style_stats,
                                           own_policies=set(req["style_own"])
                                           if req.get("style_own") else None)
                       if style_on else None)
            _write_file(mm, f, i0, take, lmax=int(req["lmax"]), aux=bool(req["aux"]),
                        student=req.get("student"), carry=carry_factory, bonus=tracker)
        for m in mm.values():
            m.flush()
        return 0
    if args.backfill_rank:
        if not args.out:
            raise SystemExit("--backfill-rank 需要两个位置参数：<采集目录> <已存在的紧凑集>")
        backfill_rank_points(args.out, args.src)
        return 0
    build(args.src, args.out, val_frac=args.val_frac, split_seed=args.split_seed, aux=args.aux,
          limit_files=args.limit_files, workers=args.workers, student=args.student,
          carry_model=args.carry_model, style_bonus=args.style_bonus,
          style_whiten=args.style_bonus_whiten)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
