"""P3（离线 RL）的**转移组装**与奖励定义（`docs/TRAINING.md` §4 P3）。

    from mahjong_ml import dataset as ds, rewards
    tr = rewards.transitions(ds.load_split("S:/mahjong-training/compact/rl-001", "train"))

## MDP 口径（判据全靠它，别含糊）

- **一条 episode = 某一家在一小局里的全部决策**（键 = `file` + `hand_no` + `seat`）。
  四家的决策在轨迹里是**交错**的；把"同一家的相邻决策"连起来才是单智能体视角
  （它们之间发生的事是环境在动）。跨小局不连 —— 见下面的"终止"。
- **奖励**：只有该家这一小局的**最后一个决策**吃 `hand_delta[seat]`（小局收支），其余决策 r = 0。
  小局收支是在**小局结束那一刻**才实现的，硬摊到每一步都是编的。
- **折扣 γ = 1**：小局收支本来就不折现。
- **终止**：每小局末尾 bootstrap 到 0 ⇒ `V(s)` 的语义是「**本小局**从这一手起还能再赚多少点」——
  与 teacher 里那三个粗模型（`AVG_DEAL_POINTS=5200` / `winProbability` 的 `/steps` 折 /
  `dealProbability` 的 0.30 上限）**同量纲**，所以判据②"用价值头替换粗模型"才有可比性。
  ⚠ 不做"跨小局累计到终局"的版本：那是另一个量纲（含后续小局），要单独一轮，别混着说。
- **γ=1 的副产品**：因为整条 episode 只有一个非零奖励（在末决策），
  **return-to-go 对同一条 episode 的所有决策都是同一个值**（= 该小局该家的收支）。
  于是"V(s) 的校准"这句话有了最直白的含义：*预测这一小局结束时这家会赚/亏多少点*。

## 刻意**不**放进 round 1 的东西

- **终局顺位（`placement`）**：那是另一套量纲（顺位点）。把它加进来会让"校准"说不清是校准了什么，
  而且顺位点要按 M.League 的精算算（`Payments`/`Settlement` 的事）—— **不在 Python 里再写一份**。
- **中间步骤的塑形**：`hand_delta` 之外没有任何可靠的稠密信号（听牌/放铳风险是特征，不是奖励）。

⚠ 这些列（`file`/`hand_no`/`seat`/`placement`）是 2026-09 才加进紧凑集的：
**旧数据集读回来是 `None`**，这里会明确报错让你重建，而不是拿 `game` 冒充 `file`（跨来源会撞车）。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import paths

#: 一小局里手数上限（`hand_no` 的最大值 + 1），用于把 (file, hand, seat) 压成一个 int64 键。
#: 半庄最多 8 局 + 连庄，几百都到不了；超了直接报错而不是悄悄撞键。
HAND_STRIDE = 1000

#: `hand_delta` 的单位是**点**；价值目标一律除以它变成"千点"，免得回归在 ±30000 的尺度上跑。
POINTS_PER_UNIT = 1000.0


def _check_columns(split: dict, *names: str) -> None:
    missing = [n for n in names if split.get(n) is None]
    if missing:
        raise ValueError(
            f"这份紧凑集缺 P3 需要的列 {missing}（旧数据集没有）——"
            f"重新 `python -m mahjong_ml.dataset build …` 一次（列的定义见 dataset.RL_COLUMNS）")


def keys(split: dict) -> np.ndarray:
    """`(file, hand_no, seat)` → int64 键（**同一家的同一小局**才同键）。"""
    _check_columns(split, "file", "hand_no", "seat")
    f = np.asarray(split["file"], dtype=np.int64)
    h = np.asarray(split["hand_no"], dtype=np.int64)
    s = np.asarray(split["seat"], dtype=np.int64)
    if h.size and (h.max() >= HAND_STRIDE or h.min() < 0):
        raise ValueError(f"hand_no 超出 [0,{HAND_STRIDE})：min={h.min()} max={h.max()}"
                         f"（撞键会让两家的小局连成一条 episode）")
    if s.size and (s.max() >= 8 or s.min() < 0):
        raise ValueError(f"seat 超出 [0,8)：min={s.min()} max={s.max()}")
    return (f * HAND_STRIDE + h) * 8 + s


def next_index(split: dict) -> np.ndarray:
    """每条决策 → **同一家在同一小局里的下一条**决策的行号；`-1` = 这条是该家本小局最后一条。

    时序 = 行序（`TraceRecorder` 按 `step` 顺序写，同一小局内天然按时间排）。
    """
    k = keys(split)
    n = k.size
    nxt = np.full(n, -1, dtype=np.int64)
    if n == 0:
        return nxt
    order = np.lexsort((np.arange(n), k))          # 主键 = k，次键 = 行号（保证组内按时间）
    ks = k[order]
    same = ks[1:] == ks[:-1]
    nxt[order[:-1][same]] = order[1:][same]
    return nxt


def hand_delta_of_seat(split: dict) -> np.ndarray:
    """每条决策 → **行动那一家**在这一小局的收支（点）。全组同值（见模块 docstring）。"""
    _check_columns(split, "seat")
    delta = np.asarray(split["delta"], dtype=np.float64)
    seat = np.asarray(split["seat"], dtype=np.int64)
    if delta.ndim != 2 or delta.shape[1] != 4 or delta.shape[0] != seat.size:
        raise ValueError(f"delta 形状 {delta.shape} 与 seat {seat.shape} 不匹配")
    if seat.size and (seat.max() >= 4 or seat.min() < 0):
        raise ValueError(f"seat 越界：min={seat.min()} max={seat.max()}")
    return delta[np.arange(seat.size), seat]


def transitions(split: dict, *, unit: float = POINTS_PER_UNIT, rank_weight: float = 1.0,
                mem: dict | None = None) -> dict:
    """把一份切分组装成转移（**不复制** state/cand —— 只给索引，训练时再按批取）。

    返回：
      `idx`     每行的行号（0..n-1）
      `nxt`     下一条同家同小局决策（-1 = 本小局结束）
      `reward`  转移奖励（**千点**）：小局末决策吃 `hand_delta[seat]`；整场末决策再另加
                `rank_weight · 顺位点`
      `done`    是否本小局最后一条
      `ret`     return-to-go（**千点**）：一条 episode 全组同值 = 该小局该家收支（若这一小局是
                本场最后一小局，再加上顺位点项）
      `rank`    每行的顺位点（千点；`rank_weight == 0` 时全 0）
      `seat` / `file` / `hand_no` / `is_student` / `placement`
      `stats`   计数与两个奖励分量的分布 —— 训练日志里要写出来

    @param rank_weight 整场顺位点项的权重（**0 = 只用小局收支**，即 P3 第一轮的版本）。
        顺位点本身已是"千点"量纲（1 位 ≈ +50…62、4 位 ≈ −30…−50），而一手牌约 ±8 ——
        所以 λ=1 时**整场名次是主导项**（这也正是这类比赛的目标函数）。
    """
    _check_columns(split, "file", "hand_no", "seat", "placement", "is_student")
    n = int(np.asarray(split["state"]).shape[0])
    nxt = next_index(split)
    done = nxt < 0
    raw = hand_delta_of_seat(split)
    reward = np.where(done, raw, 0.0) / unit
    ret = raw / unit                                   # γ=1 且小局奖励只在小局末 ⇒ 全组同值
    rank = np.zeros(n, dtype=np.float64)
    term = None
    if rank_weight != 0.0 and n:
        rank = rank_points_by_row(split, mem=mem)
        term = terminal_mask(split)
        reward = reward + rank_weight * np.where(term, rank, 0.0)   # 顺位点已是千点量纲，不再除
        # 本场**最后一小局**的 return-to-go 要含顺位点；更早的小局不含（它们是 hand 级终止）
        h = np.asarray(split["hand_no"], dtype=np.int64)
        f = np.asarray(split["file"], dtype=np.int64)
        last_hand = np.full(int(f.max()) + 1, -1, dtype=np.int64)
        np.maximum.at(last_hand, f, h)
        ret = ret + rank_weight * np.where(h == last_hand[f], rank, 0.0)
    seat = np.asarray(split["seat"], dtype=np.int64)
    k = keys(split)
    n_ep = int(np.unique(k).size) if n else 0
    stats = {
        "decisions": n,
        "episodes": n_ep,
        "episodes_per_game": None if n_ep == 0 else round(n_ep / max(1, len(np.unique(k // (HAND_STRIDE * 8)))), 2),
        "mean_len": 0.0 if n_ep == 0 else round(n / n_ep, 2),
        "reward_mean": 0.0 if n == 0 else round(float(ret.mean()), 4),
        "reward_nonzero_frac": 0.0 if n == 0 else round(float((raw != 0).mean()), 4),
        "win_frac": 0.0 if n == 0 else round(float((raw > 0).mean()), 4),
        "deal_in_frac": 0.0 if n == 0 else round(float((raw < 0).mean()), 4),
        "student_row_frac": 0.0 if n == 0 else round(float(np.asarray(split["is_student"]).mean()), 4),
        "rank_weight": rank_weight,
        "rank_mean": 0.0 if n == 0 else round(float(rank.mean()), 4),
        "ret_std": 0.0 if n == 0 else round(float(ret.std()), 4),
        "unit": unit,
    }
    return {"idx": np.arange(n), "nxt": nxt, "reward": reward, "done": done, "ret": ret,
            "rank": rank, "terminal": term,
            "seat": seat, "file": np.asarray(split["file"], dtype=np.int64),
            "hand_no": np.asarray(split["hand_no"], dtype=np.int64),
            "is_student": np.asarray(split["is_student"], dtype=np.uint8),
            "placement": np.asarray(split["placement"], dtype=np.int64), "stats": stats}


def terminal_mask(split: dict) -> np.ndarray:
    """**整场奖励的记账点**：本场最后一小局里、该家最后一次决策。

    为什么是这里：顺位点是**整场打完**才结算的，只能记在本场最后一小局的最后一次决策上
    （和 `hand_delta` 记在小局末决策同一个道理）。
    """
    _check_columns(split, "file", "hand_no")
    f = np.asarray(split["file"], dtype=np.int64)
    h = np.asarray(split["hand_no"], dtype=np.int64)
    n = f.size
    if n == 0:
        return np.zeros((0,), dtype=bool)
    last_hand = np.full(int(f.max()) + 1, -1, dtype=np.int64)
    np.maximum.at(last_hand, f, h)                       # 每个文件的最后一小局号
    return (h == last_hand[f]) & (next_index(split) < 0)


def _resolve_trace_path(p: Path) -> Path:
    """把紧凑集 meta 里记的轨迹路径**对到当前数据根**上。

    为什么要这一步：紧凑集是**迁盘前**建的时，meta 里记的还是旧根（例如 `T:\\mahjong-training\\raw\\…`），
    而数据已经搬到 `S:\\mahjong-training\\` 了。这里**只在原路径不存在时**才按 `paths.DATA_ROOT`
    重映射（`<任意前缀>\\raw\\…` → `<当前数据根>\\raw\\…`），并把重映射事实写进返回值之外的调用方日志。
    找不到就报错 —— **绝不悄悄当成 0**（那会把"整场项"变成噪声）。
    """
    if p.is_file():
        return p
    parts = p.parts
    for i, seg in enumerate(parts):
        if seg == "raw" and i + 1 < len(parts):
            cand = paths.DATA_ROOT.joinpath(*parts[i:])
            if cand.is_file():
                return cand
            raise FileNotFoundError(
                f"{p} 不在，按当前数据根重映射到 {cand} 也不在 —— "
                f"轨迹被删了？那就把 --rank-weight 设 0 重跑")
    raise FileNotFoundError(f"{p} 不在，且路径里没有 `raw/` 段，无法按数据根重映射")


def _run_rank_points(run_dir: Path) -> dict:
    """`<run 目录>/summary.json` → `{场序号: [四家顺位点]}`（**读 Java 算好的**，不重写精算）。"""
    p = run_dir / "summary.json"
    if not p.is_file():
        raise FileNotFoundError(
            f"{p} 不在 —— 顺位点只能从自对弈的 summary.json 读（轨迹目录删了？"
            f"那就把 --rank-weight 设 0 重跑）")
    s = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for g in s.get("per_game") or []:
        rp = g.get("rank_points")
        if isinstance(rp, list) and len(rp) == 4:
            out[int(g.get("game", -1))] = [float(x) for x in rp]
    return out


def rank_points_by_row(split: dict, mem: dict | None = None) -> np.ndarray:
    """每条决策 → 该座位的**整场顺位点**（单位：千点 —— M.League 顺位点本身就是这个量纲：
    精算例子里 41,600 的 1 位 → 61.6，见 `DESIGN.md`）。

    链路：紧凑集 meta 记着该切分的**全路径**清单（`train_files_path`/`val_files_path`）→
    文件名 `g<N>.jsonl` 对回 run 目录 `summary.json` 的 `per_game[N].rank_points` → 取 `seat` 那一家。
    ⚠ **不在 Python 里重写 uma/oka**：那是 `Payments`/`Settlement` 的活，重写必然漂移。
    """
    _check_columns(split, "file", "seat")
    meta = split.get("meta") or {}
    split_name = split.get("split")
    if not split_name:
        raise ValueError("这份切分没带 `split` 名（不是 `dataset.load_split` 读出来的）——"
                         "顺位点项需要它去 meta 里取 *_files_path；只想用小局收支就把 rank_weight 设 0")
    key = f"{split_name}_files_path"
    if key not in meta:
        raise ValueError(f"这份紧凑集 meta 里没有 {key}（旧格式）—— 重新 dataset build 一次"
                         f"（顺位点要靠全路径清单对回 summary.json）")
    files = [_resolve_trace_path(Path(p)) for p in meta[key]]
    mem = {} if mem is None else mem
    per_file = np.zeros((len(files), 4), dtype=np.float64)
    for i, p in enumerate(files):
        rk = str(p.parent)
        if rk not in mem:
            mem[rk] = _run_rank_points(p.parent)
        game = int(p.stem[1:]) if p.stem[1:].isdigit() else -1
        rp = mem[rk].get(game)
        if rp is None:
            raise ValueError(f"{p.name} 在 {p.parent}/summary.json 里找不到第 {game} 场 —— 轨迹与 summary 不同源")
        per_file[i] = rp
    return per_file[np.asarray(split["file"], dtype=np.int64),
                    np.asarray(split["seat"], dtype=np.int64)]


def describe(tr: dict) -> str:
    s = tr["stats"]
    extra = "" if not s.get("rank_weight") else \
        f"；整场项：顺位点均值 {s['rank_mean']:+.2f}（权重 {s['rank_weight']:g}）"
    return (f"转移：{s['decisions']} 条决策 / {s['episodes']} 条 episode"
            f"（均长 {s['mean_len']} 步）；奖励单位 = 千点，"
            f"非零奖励占比 {s['reward_nonzero_frac']:.3f}"
            f"（赢 {s['win_frac']:.3f} / 输 {s['deal_in_frac']:.3f}）；"
            f"学生行占比 {s['student_row_frac']:.3f}；回报 std {s['ret_std']:.2f}{extra}")