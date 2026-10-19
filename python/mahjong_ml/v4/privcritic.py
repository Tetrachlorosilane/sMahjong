"""MV-2 阶段 2：**特权 critic（2a）+ 蒸馏到主网 value 头（2b）**。

⛔ 本模块只在**训练期**跑：特权量（对手暗牌 / 牌山剩余 / 里宝）只在这里被读，
**不写 obs、不写 `net.bin`、推理路径永不 import 它**（`AGENTS.md` §6.5 判据 22①）。

## 三件事

1. `data`：轨迹（obs）+ `g*.priv.npz`（`v4/privside.py`）→ 定长特征表
   `pub[n,D_pub]` / `priv[n,D_priv]` / 标签 `y = final_scores[seat] − 起点`（千点，与主网 value 头同尺度）。
   公开特征**只用 obs**（自家手牌计数 / 四家牌河 / 可见牌 / 点数 / 巡目 …）；
   特权特征 = `opp_hand(3×34)` + `wall_next(14×34 one-hot)` + `ura(5×34 one-hot)`。
2. `train`：小 MLP（≤200k 参数、81 bin HL-Gauss 头，与主网 value 头**同一 bin/量纲**）→
   val `EV / CRPS / Spearman`；**同一批行**上再训一个"只用公开特征"的同规模小网（判据 ③ 的基线）。
3. `distill`：主网**只更新 `heads.value`**（其余全冻结），目标 = privileged critic 的分布（软标签 CE）。
   导出 format 3 / `topoId=3` 的 `net.bin`，参数个数不变（只换 value 头权重）。

## 用法

    python -m mahjong_ml.v4.privcritic data   --raw <轨迹目录> --out <data.npz> [--limit-files N]
    python -m mahjong_ml.v4.privcritic train  --data <data.npz> --out <dir>      # 两个小网 + 读数
    python -m mahjong_ml.v4.privcritic distill --data <data.npz> --critic <ckpt.pt> \
        --net <主网 net.bin> --compact <compact 目录> --out <new net.bin>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

from . import model as M
from . import privside

#: 值标签的 bin 数与量纲 —— **必须与主网 a− value 头一致**（81 bin / ±60 千点）。
VALUE_BINS = M.A_VALUE_BINS
VALUE_RANGE = M.A_VALUE_RANGE
CENTERS = np.linspace(-VALUE_RANGE + (2.0 * VALUE_RANGE / VALUE_BINS) / 2,
                      VALUE_RANGE - (2.0 * VALUE_RANGE / VALUE_BINS) / 2,
                      VALUE_BINS, dtype=np.float64)

#: 判据 ②/③ 的阈值（**预注册**：跑之前写死，⛔ 事后调）。
#: ② ρ ≥ 0.5（"主网 value 与 privileged critic 同序"的弱必要条件）；
#:    EV ≥ 0.7 × critic 的 EV（"内化至少拿到七成"——沿用 AGENTS §6.5 的 0.7 口径）。
#: ③ 主网 EV > 公开基线 EV（**严格更好**），且 CRPS 更低。
RHO_MIN = 0.5
EV_RATIO_MIN = 0.7

_TILE_KINDS = {c: i for i, c in enumerate("mpsz")}


def kind_of(tile: str) -> int:
    """`'5m'`/`'0m'`(赤五) → 牌种 0..33（与 `Tiles.kind` 同一套编号）。"""
    n = int(tile[0])
    if n == 0:
        n = 5
    c = tile[-1]
    if c == "z":
        return 27 + n - 1
    return _TILE_KINDS[c] * 9 + (n - 1)


def _counts(kinds: list[int], n: int = 34) -> np.ndarray:
    return np.bincount(np.asarray(kinds, dtype=np.int64), minlength=n).astype(np.float32)


def _onehot34(kind: int) -> np.ndarray:
    v = np.zeros(34, np.float32)
    if 0 <= kind < 34:
        v[kind] = 1.0
    return v


def public_features(o: dict, start_score: int, cand: np.ndarray | None = None,
                    decision: np.ndarray | None = None) -> np.ndarray:
    """**只用公开 obs** 的摘要特征（与主网 `ctx` 同一信息面，但更贴"critic 的输入"）。

    `cand` = sidecar 的逐候选 8 维（第 0 维 = 该候选打完之后向听；**自家手牌的公开派生量**）。
    取 `min`（"任何合法动作里最好的向听"）与"达到它的候选数"—— 两者都只依赖**当前状态**，
    不依赖策略最后选了哪一手（⛔ 不用 `aux_own_shanten_after`：那是**做完动作之后**的量，
    拿它当输入等于把动作也喂进去）。
    """
    seat = int(o["seat"])
    rel = [(seat + i) % 4 for i in range(4)]          # self, 下家, 对家, 上家
    rnd = o["round"]
    parts: list[np.ndarray] = [
        np.asarray(o["hand"], np.float32) / 4.0,                        # 34 自家手牌计数
        np.asarray(o["visible"], np.float32) / 4.0,                     # 34 可见牌计数
        np.asarray([sum(1 for x in o.get("melds", [[]] * 4)[s]) for s in rel], np.float32) / 4.0,
        np.asarray([1.0 if o.get("menzen") else 0.0,
                    1.0 if o.get("self_riichi") else 0.0,
                    1.0 if o.get("furiten") else 0.0,
                    1.0 if o.get("any_call") else 0.0,
                    1.0 if o.get("rinshan") else 0.0,
                    1.0 if o.get("haitei") else 0.0,
                    1.0 if o.get("houtei") else 0.0,
                    1.0 if o.get("kuitan") else 0.0,
                    1.0 if o.get("drawn") is not None else 0.0], np.float32),
    ]
    # 四家牌河（相对方位；每 34 维计数）
    for s in rel:
        kinds = [kind_of(t) for t in o.get("discards", [[]] * 4)[s]]
        parts.append(_counts(kinds) / 4.0)
    # 四家立直 / 一发 / 分数（相对方位）
    riichi = o.get("riichi") or [False] * 4
    ippatsu = o.get("ippatsu") or [False] * 4
    parts.append(np.asarray([1.0 if riichi[s] else 0.0 for s in rel], np.float32))
    parts.append(np.asarray([1.0 if ippatsu[s] else 0.0 for s in rel], np.float32))
    sc = np.asarray(o["scores"], np.float64)
    rel_sc = (sc[rel] - start_score) / 1000.0
    parts.append(rel_sc.astype(np.float32))
    parts.append((rel_sc - rel_sc.mean()).astype(np.float32))
    # 局信息
    bakaze = {"E": 0, "S": 1, "W": 2, "N": 3}.get(rnd.get("bakaze", "E"), 0)
    kyoku = int(rnd.get("kyoku", 1)) - 1
    ky = np.zeros(4, np.float32)
    ky[kyoku] = 1.0
    bk = np.zeros(4, np.float32)
    bk[bakaze] = 1.0
    parts.append(bk)
    parts.append(ky)
    parts.append(np.asarray([int(rnd.get("honba", 0)) / 3.0,
                             int(rnd.get("riichi_sticks", 0)) / 5.0,
                             1.0 if int(rnd.get("dealer", 0)) == seat else 0.0], np.float32))
    # 牌山 / 巡目（公开量）
    parts.append(np.asarray([o.get("tiles_left", 0) / 70.0,
                             o.get("dead_wall_left", 0) / 4.0,
                             o.get("kan_count", 0) / 4.0,
                             o.get("total_discards", 0) / 70.0,
                             o.get("player_draws", 0) / 20.0], np.float32))
    # 宝牌指示牌（公开；按牌种计数）
    parts.append(_counts([kind_of(t) for t in o.get("dora_indicators", [])]) / 4.0)
    # 询问本身
    kind = o.get("kind", "turn")
    parts.append(np.asarray([1.0 if kind == "turn" else 0.0,
                             1.0 if kind == "claim" else 0.0,
                             len(o.get("legal") or []) / 30.0], np.float32))
    # 自家手牌的**公开派生量**（向听：任何合法动作里最好的那个）
    if cand is not None and len(cand):
        sh = np.asarray(cand, np.float32)[:, 0]
        best = float(sh.min())
        parts.append(np.asarray([best / 8.0 if best >= 0 else -1.0,
                                 float((sh == best).sum()) / 8.0,
                                 float(sh.max()) / 8.0 if sh.max() >= 0 else -1.0], np.float32))
    else:
        parts.append(np.zeros(3, np.float32))
    # A 段 71 维逐决策派生量（`danger_worst` 34 + `danger_riichi` 34 + 向听/打点/点值）——
    # 全部由**公开信息**算出（自家手牌 + 牌河 + 可见牌），与主网 `ctx/cand` 同一信息面。
    parts.append(np.zeros(71, np.float32) if decision is None
                 else np.asarray(decision, np.float32).reshape(-1))
    return np.concatenate(parts).astype(np.float32)


def privileged_features(priv: dict, i: int, opp_tenpai: np.ndarray | None = None) -> np.ndarray:
    """**特权**特征（只由 ①他家手牌 ②牌山顺序 ③里宝 派生，外加 ①的函数 `opp_tenpai`）。

    ⚠ 维数刻意压到 ~309（而不是把 14 张牌逐张 one-hot 成 476 维）：有效样本只有
    **~8 千个 hand-seat**（同一小局内的决策高度相关），476 维 one-hot 只会记住训练集。
    保留的信息：他家暗牌计数（102）· 他家听牌（3；`opp_hand` 的确定性函数，取自 aux）·
    剩余牌山**按牌种计数**（34）· 接下来 4 张的**顺序** one-hot（4×34）· 里宝按牌种计数（34）。
    """
    wn = priv["wall_next"][i]
    bag = np.zeros(34, np.float32)
    for t in wn:
        if t != 255:
            bag[int(t)] += 1.0
    ura_bag = np.zeros(34, np.float32)
    for t in priv["ura"][i]:
        if t != 255:
            ura_bag[int(t)] += 1.0
    parts: list[np.ndarray] = [
        np.asarray(priv["opp_hand"][i], np.float32).reshape(-1) / 4.0,   # 102
        (np.zeros(3, np.float32) if opp_tenpai is None
         else np.asarray(opp_tenpai, np.float32).reshape(-1)),          # 3
        bag / 4.0,                                                       # 34
    ]
    for k in range(4):                                                   # 4 × 34（顺序）
        t = int(wn[k])
        parts.append(_onehot34(t if t != 255 else -1))
    parts.append(ura_bag / 4.0)                                          # 34
    return np.concatenate(parts).astype(np.float32)


# ---------------------------------------------------------------- 数据集

def build_data(raw: str | Path, out: Path, limit_files: int | None = None) -> dict:
    """轨迹目录（可逗号分隔多个）→ `data.npz`（公开特征 / 特权特征 / 两个 value 标签 / 行键）。

    ⚠ 多个目录的**场号会撞**（每个目录都从 `g0` 起、行内 `game` 也各自从 0 起）⇒ 第 d 个目录
    统一加 **10000×d** 的偏移。偏移只影响行键/切分，不影响任何特征。
    """
    from .. import dataset as v3ds                                    # noqa: PLC0415
    from . import dataset as v4ds                                   # noqa: PLC0415
    from . import traces                                           # noqa: PLC0415

    dirs = [Path(p) for p in str(raw).split(",") if p.strip()]
    per_dir = []
    for d_i, d in enumerate(dirs):
        fs = traces.trace_files(d)
        if limit_files:
            fs = fs[:limit_files]
        if not fs:
            raise SystemExit(f"{d} 里没有 g*.jsonl")
        per_dir.append((d_i, fs))
    pub: list[np.ndarray] = []
    prv: list[np.ndarray] = []
    ys: list[float] = []
    ys_delta: list[float] = []
    keys: list[tuple] = []
    n_files = 0
    for d_i, files in per_dir:
        goff = 10_000 * d_i
        for f in files:
            n_files += 1
            rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
            dec = [r for r in rows if r.get("type") == "decision"]
            start = int(v4ds._game_start_score(f))
            pv = privside.load_priv(privside.priv_path(f))
            privside.check_against_decision_rows(pv, dec, f.name)
            sc = traces.load_sidecar(v3ds.sidecar_path(f))
            ax = None
            if f.with_suffix(".aux.npz").is_file():
                ax = np.load(f.with_suffix(".aux.npz"))
            for i, r in enumerate(dec):
                o = r["obs"]
                side = traces.sidecar_dict(sc, i)
                pub.append(public_features(o, start, side["cand"], side.get("decision")))
                prv.append(privileged_features(
                    pv, i, None if ax is None else ax["opp_tenpai"][i]))
                ys.append((float(r["final_scores"][o["seat"]]) - start) / 1000.0)
                hd = r.get("hand_delta")
                ys_delta.append((float(hd[o["seat"]]) if hd else float("nan")) / 1000.0)
                keys.append((int(r["game"]) + goff, int(r["hand_no"]), int(r["step"]),
                             int(r["seat"])))
    x_pub = np.stack(pub)
    x_prv = np.stack(prv)
    y = np.asarray(ys, np.float32)
    k = np.asarray(keys, np.int64)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, pub=x_pub, priv=x_prv, y=y,
                        y_delta=np.asarray(ys_delta, np.float32), keys=k,
                        games=np.asarray([p.stem for p in sum((fs for _ in per_dir), [])]))
    meta = {"n": int(len(y)), "d_pub": int(x_pub.shape[1]), "d_priv": int(x_prv.shape[1]),
            "files": n_files, "raw": [str(d) for d in dirs], "game_offsets": 10_000,
            "value_bins": VALUE_BINS, "value_range": VALUE_RANGE,
            "labels": {"final": "final_scores[seat] − start（千点）",
                       "delta": "hand_delta[seat]（本小局收支，千点；= --value-target delta）"}}
    (out.with_suffix(".json")).write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


# ---------------------------------------------------------------- 指标

def pearson(a, b) -> float:
    a = np.asarray(a, np.float64) - np.mean(a)
    b = np.asarray(b, np.float64) - np.mean(b)
    d = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else float("nan")


def spearman(a, b) -> float:
    return pearson(np.argsort(np.argsort(np.asarray(a, np.float64))),
                   np.argsort(np.argsort(np.asarray(b, np.float64))))


def explained_variance(pred, y) -> float:
    pred = np.asarray(pred, np.float64)
    y = np.asarray(y, np.float64)
    v = float(y.var())
    return float(1.0 - (y - pred).var() / v) if v > 0 else float("nan")


_DIST = np.abs(CENTERS[:, None] - CENTERS[None, :])


def prob_metrics(prob: np.ndarray, y: np.ndarray) -> dict:
    prob = np.asarray(prob, np.float64)
    y = np.asarray(y, np.float64)
    mean = prob @ CENTERS
    err = mean - y
    return {
        "n": int(y.size),
        "ev": explained_variance(mean, y),
        "spearman": spearman(mean, y),
        "pearson": pearson(mean, y),
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err * err).mean())),
        "bias": float(err.mean()),
        "crps": float((prob * np.abs(CENTERS[None, :] - y[:, None])).sum(1).mean()
                      - 0.5 * np.einsum("ij,jk,ik->i", prob, _DIST, prob).mean()),
        "pred_std": float(mean.std()),
        "true_std": float(y.std()),
        "mean_pred": float(mean.mean()),
    }


# ---------------------------------------------------------------- 小网

def _mlp(d_in: int, hidden: tuple[int, ...], d_out: int, drop: float = 0.0):
    import torch.nn as nn                                       # noqa: PLC0415

    layers: list = []
    last = d_in
    for h in hidden:
        layers += [nn.Linear(last, h), nn.GELU()]
        if drop > 0:
            layers.append(nn.Dropout(drop))
        last = h
    layers += [nn.Linear(last, d_out)]
    return nn.Sequential(*layers)


def _param_count(net) -> int:
    return int(sum(p.numel() for p in net.parameters()))


def _split3(games: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """按**场**切 train / val（早停）/ test（报告）：`%10==5` 早停、`%10==0` 报告，互不重叠。"""
    return (games % 10 != 0) & (games % 10 != 5), games % 10 == 5, games % 10 == 0


#: 特权块口径（`priv` 列布局：opp_hand 102 | opp_tenpai 3 | wall bag 34 |
#: wall 前 4 张 one-hot 136 | ura bag 34）。`narrow` / `tenpai` 是**降维对照**：
#: 有效样本只有 ~3 万个 hand-seat，309 维会过拟合（读数见 `PRIVILEGED-CRITIC.md`）。
PRIV_SLICES: dict[str, tuple[slice, ...] | None] = {
    "full": None,
    "narrow": (slice(102, 105), slice(105, 139), slice(275, 309)),
    "tenpai": (slice(102, 105),),
}


def priv_block(x: np.ndarray, block: str) -> np.ndarray:
    sl = PRIV_SLICES[block]
    return x if sl is None else np.concatenate([x[:, s] for s in sl], 1)


def train_net(x_tr, y_tr, x_va, y_va, hidden, seed: int, epochs: int = 25,
              lr: float = 3e-4, wd: float = 1e-3, drop: float = 0.3,
              batch: int = 512, sigma: float = 1.5):
    """HL-Gauss（**高斯核**）交叉熵训一个小 MLP；按 val CE 早停、返回最优权重。

    ⚠ 目标用**高斯核**而不是仓库 `hl_gauss_targets` 的两点线性插值：81 bin / ±60 千点下
    1.5 千点的核宽给出平滑的软标签（值域宽度 1.48 千点/格）—— 线性插值太尖，
    在 ~3 万个有效 hand-seat 上会把"预测均值"学成"预测众数"。
    """
    import torch                                                # noqa: PLC0415
    import torch.nn.functional as F                             # noqa: PLC0415

    def tgt(yy):
        z = torch.from_numpy(np.asarray(yy, np.float32))[:, None] - torch.from_numpy(CENTERS.astype(np.float32))[None, :]
        return torch.softmax(-0.5 * (z / sigma) ** 2, -1)

    torch.manual_seed(seed)
    net = _mlp(x_tr.shape[1], hidden, VALUE_BINS, drop=drop)
    opt = torch.optim.Adam(net.parameters(), lr=lr, weight_decay=wd)
    xt = torch.from_numpy(x_tr)
    xv = torch.from_numpy(x_va)
    T = tgt(y_tr)
    Tva = tgt(y_va)
    n = xt.shape[0]
    g = torch.Generator().manual_seed(seed)
    best = (float("inf"), None, -1)
    for ep in range(epochs):
        net.train()
        perm = torch.randperm(n, generator=g)
        for i in range(0, n, batch):
            idx = perm[i:i + batch]
            loss = -(T[idx] * F.log_softmax(net(xt[idx]), -1)).sum(-1).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            vl = float(-(Tva * F.log_softmax(net(xv), -1)).sum(-1).mean())
        if vl < best[0] - 1e-5:
            best = (vl, {k: v.clone() for k, v in net.state_dict().items()}, ep)
    net.load_state_dict(best[1])
    net.eval()
    return net, {"val_ce": best[0], "epoch": best[2], "params": _param_count(net)}


def _boot(pred_a, pred_b, y, games, n: int = 1000, seed: int = 0) -> dict:
    """按**场**聚类的 bootstrap：EV(a)、EV(b)、ΔEV = EV(a) − EV(b) 的 95% 区间。"""
    uniq = np.unique(games)
    by = {int(g): np.where(games == g)[0] for g in uniq}
    rng = np.random.default_rng(seed)
    ea, eb = [], []
    for _ in range(n):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        ii = np.concatenate([by[int(g)] for g in pick])
        ea.append(explained_variance(pred_a[ii], y[ii]))
        eb.append(explained_variance(pred_b[ii], y[ii]))

    def q(v):
        return [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]

    return {"n_boot": n, "n_games": int(len(uniq)),
            "ev_a": float(np.mean(ea)), "ev_a_ci": q(ea),
            "ev_b": float(np.mean(eb)), "ev_b_ci": q(eb),
            "d_ev": float(np.mean(ea) - np.mean(eb)),
            "d_ev_ci": q([a - b for a, b in zip(ea, eb)])}


def _probs(net, x) -> np.ndarray:
    import torch                                                # noqa: PLC0415

    with torch.no_grad():
        return torch.softmax(net(torch.from_numpy(x)), -1).numpy()


# ---------------------------------------------------------------- 2b：蒸馏

_COMPACT_COLS = ("tile", "evt", "ctx", "cand", "nlegal", "value", "delta", "game", "seat", "hand_no")


def load_compact(path: Path) -> dict:
    """读一份 compact（train/val 两半拼起来）——只取蒸馏/评估要用的列。

    ⚠ `cand` 的 `lmax` 两半可能不同（train 27 / val 21）⇒ 按**较宽**的那半补 0；
    补齐的槽由 `nlegal` 掩码排除，不影响前向。
    """
    from . import dataset as ds                                # noqa: PLC0415

    def cat(arrs: list[np.ndarray]) -> np.ndarray:
        shapes = [a.shape[1:] for a in arrs]
        if len({s for s in shapes}) == 1:
            return np.concatenate(arrs)
        mx = [max(s[i] for s in shapes) for i in range(len(shapes[0]))]
        pads = [[(0, 0)] + [(0, mx[i] - s[i]) for i in range(len(s))] for s in shapes]
        return np.concatenate([np.pad(a, p) for a, p in zip(arrs, pads)])

    out: dict[str, list] = {}
    for split in ("train", "val"):
        d = ds.load_split(path, split)
        for k in _COMPACT_COLS:
            if d.get(k) is None:
                raise SystemExit(f"compact 缺列 {split}.{k}")
            out.setdefault(k, []).append(np.asarray(d[k]))
    comp = {k: cat(v) for k, v in out.items()}
    # ⚠ train / val 是**两段拼接**（val 文件在最后）⇒ 行序不是"按场号"。
    # 与轨迹/privileged sidecar 对齐必须按场号重排（同场内保持行序，split 按文件切、不会混）。
    order = np.argsort(comp["game"], kind="stable")
    return {k: v[order] for k, v in comp.items()}


def _forward_capture(net, comp: dict, idx: np.ndarray, chunk: int = 512) -> dict:
    """跑主网前向，**截住 value 头真正读到的那一维**（`state_pool` 之后）+ 各头输出。"""
    import torch                                                # noqa: PLC0415
    import torch.nn.functional as F                             # noqa: PLC0415

    cap: dict[str, list] = {"s": [], "value": [], "policy": []}

    def hook(_m, inp, _o):
        cap["s"].append(inp[0].detach().clone())

    hk = net.heads.value.register_forward_hook(hook)
    try:
        with torch.no_grad():
            for st in range(0, len(idx), chunk):
                ii = idx[st:st + chunk]
                tile = torch.from_numpy(comp["tile"][ii].astype(np.float32))
                evt = torch.from_numpy(comp["evt"][ii].astype(np.float32))
                ctx = torch.from_numpy(comp["ctx"][ii].astype(np.float32))
                cand = torch.from_numpy(comp["cand"][ii].astype(np.float32))
                nl = torch.from_numpy(comp["nlegal"][ii].astype(np.int64))
                mask = torch.arange(cand.shape[1])[None, :] < nl[:, None]
                o = net(tile, evt, ctx, cand, mask=mask, h=None)
                cap["value"].append(torch.softmax(o["value"], -1))
                cap["policy"].append(o["policy"])
    finally:
        hk.remove()
    return {"s": torch.cat(cap["s"]), "value": torch.cat(cap["value"]),
            "policy": torch.cat(cap["policy"])}


def cmd_distill(args) -> int:
    """只更新 `heads.value`（其余全冻结），目标 = privileged critic 的 81-bin 分布。"""
    import torch                                                # noqa: PLC0415
    import torch.nn.functional as F                             # noqa: PLC0415

    from . import export as X                                  # noqa: PLC0415

    z = np.load(args.data)
    x_pub, x_prv, y, keys = z["pub"], z["priv"], z["y"], z["keys"]
    Y = z["y"] if args.label == "final" else z["y_delta"]
    games = keys[:, 0]
    tr, va, te = _split3(games)
    # 蒸馏用"既不在早停也不在报告集"的行；报告集 = test ∩ compact（compact 只覆盖部分场）
    dist_games = np.unique(games[tr])
    test_games = np.unique(games[te])
    print(f"label={args.label}  distill_games={len(dist_games)} test_games={len(test_games)}")

    # ---- critic / 公开基线（拿它们的分布当蒸馏目标与判据③的参照）
    ck = torch.load(args.critic, map_location="cpu", weights_only=False)
    net_c = _mlp(ck["d_in"], tuple(ck["hidden"]), VALUE_BINS, drop=float(ck.get("drop", 0.3)))
    net_c.load_state_dict(ck["state"])
    net_c.eval()
    xn_all = ((np.concatenate([x_pub, priv_block(x_prv, ck.get("priv_block", "full"))], 1)
               - ck["mu"]) / ck["sd"]).astype(np.float32)
    p_critic = _probs(net_c, xn_all)
    ck_b = torch.load(args.baseline, map_location="cpu", weights_only=False)
    net_b = _mlp(ck_b["d_in"], tuple(ck_b["hidden"]), VALUE_BINS, drop=float(ck_b.get("drop", 0.3)))
    net_b.load_state_dict(ck_b["state"])
    net_b.eval()
    xb_all = ((x_pub - ck_b["mu"]) / ck_b["sd"]).astype(np.float32)
    p_base = _probs(net_b, xb_all)

    # ---- 主网
    parsed = X.read_net(args.net)
    sd = X.state_from_net(parsed)
    dims = parsed["dims"]
    arch = parsed["arch"]
    net = M.build(1, arch=arch, **dims, **X.arch_kwargs(parsed))
    net.load_state_dict(sd, strict=True)
    net.eval()
    n_params0 = net.param_count()

    comp = load_compact(Path(args.compact))
    comp_games = set(int(g) for g in np.unique(comp["game"]))
    dist_games_c = np.array(sorted(comp_games & set(int(g) for g in dist_games)))
    test_games_c = np.array(sorted(comp_games & set(int(g) for g in test_games)))
    if not len(dist_games_c) or not len(test_games_c):
        raise SystemExit(f"compact 与 data 的场次没有交集：compact={sorted(comp_games)[:5]}…")
    dist_mask = tr & np.isin(games, dist_games_c)
    test_mask = te & np.isin(games, test_games_c)
    res: dict = {"net_in": str(args.net), "arch": arch, "params_in": n_params0,
                 "label": args.label,
                 "games": {"distill": len(dist_games_c), "test": len(test_games_c)}}

    def align(sel_games: np.ndarray, crit_mask: np.ndarray) -> np.ndarray:
        """compact 行 ∩ 选中场 —— 与 critic 行**逐行**核对（场/局/座），返回 compact 行下标。"""
        cm = np.isin(comp["game"], sel_games)
        if int(cm.sum()) != int(crit_mask.sum()):
            raise SystemExit(f"行数不齐：compact {int(cm.sum())} != critic {int(crit_mask.sum())}")
        if not np.array_equal(comp["hand_no"][cm], keys[crit_mask, 1]):
            raise SystemExit("hand_no 序列不齐（compact ↔ critic）")
        if not np.array_equal(comp["seat"][cm], keys[crit_mask, 3]):
            raise SystemExit("seat 序列不齐（compact ↔ critic）")
        col = (comp["value"] if args.label == "final"
               else comp["delta"].astype(np.float32) / 1000.0)
        dv = float(np.abs(col[cm] - Y[crit_mask]).max())
        print(f"  compact↔critic 行对齐 ✓  n={int(cm.sum())}  |Δ{args.label}|max={dv:.6g}")
        if dv > 1e-6:
            raise SystemExit(f"{args.label} 标签不齐：max|Δ|={dv}")
        return np.where(cm)[0]

    idx_tr = align(dist_games_c, dist_mask)
    idx_te = align(test_games_c, test_mask)
    res["align"] = {"distill_rows": int(len(idx_tr)), "test_rows": int(len(idx_te)),
                    "label_max_abs_delta": 0.0}
    cap_tr = _forward_capture(net, comp, idx_tr)
    cap_te_pre = _forward_capture(net, comp, idx_te)

    # ---- 只训 heads.value（输入 = 已缓存的 state_pool，等价于只动这个线性头）
    head = net.heads.value
    W0 = head.weight.detach().clone()
    b0 = head.bias.detach().clone()
    S = cap_tr["s"].detach()
    T = torch.from_numpy(p_critic[dist_mask]).float()
    W = W0.clone().requires_grad_(True)
    b = b0.clone().requires_grad_(True)
    opt = torch.optim.Adam([W, b], lr=args.lr)
    n = S.shape[0]
    g = torch.Generator().manual_seed(args.seed)
    loss0 = float(-(T * F.log_softmax(S @ W0.T + b0, -1)).sum(-1).mean())
    for step in range(args.steps):
        idx = torch.randperm(n, generator=g)[:args.batch]
        logits = S[idx] @ W.T + b
        loss = -(T[idx] * F.log_softmax(logits, -1)).sum(-1).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
    with torch.no_grad():
        loss1 = float(-(T * F.log_softmax(S @ W.T + b, -1)).sum(-1).mean())
        dw = float((W - W0).abs().max())
        db = float((b - b0).abs().max())
    res["distill"] = {"steps": args.steps, "batch": args.batch, "lr": args.lr,
                      "ce_before": loss0, "ce_after": loss1,
                      "max_abs_dW": dw, "max_abs_db": db}
    print("distill", json.dumps(res["distill"], ensure_ascii=False))

    # ---- 判据读数（test ∩ compact）：主网蒸馏前后 vs critic vs 公开基线
    y_te = Y[test_mask]
    p_pre = cap_te_pre["value"].numpy()
    with torch.no_grad():
        p_post = torch.softmax(cap_te_pre["s"] @ W.T + b, -1).numpy()
    res["test"] = {
        "main_before": prob_metrics(p_pre, y_te),
        "main_after": prob_metrics(p_post, y_te),
        "privileged_critic": prob_metrics(p_critic[test_mask], y_te),
        "public_baseline": prob_metrics(p_base[test_mask], y_te),
    }
    m_after = res["test"]["main_after"]
    m_c = res["test"]["privileged_critic"]
    m_b = res["test"]["public_baseline"]
    mean_post = p_post @ CENTERS
    mean_c = p_critic[test_mask] @ CENTERS
    res["criterion2"] = {"spearman_main_vs_critic": spearman(mean_post, mean_c),
                         "ev_main": m_after["ev"], "ev_critic": m_c["ev"],
                         "ev_ratio": (m_after["ev"] / m_c["ev"]) if m_c["ev"] else None}
    res["criterion3"] = {"ev_main": m_after["ev"], "ev_public": m_b["ev"],
                         "ev_delta": m_after["ev"] - m_b["ev"],
                         "crps_main": m_after["crps"], "crps_public": m_b["crps"],
                         "crps_delta": m_after["crps"] - m_b["crps"]}
    # 逐行预测落盘：判据 ②③ 的按场 bootstrap 区间要在**同一批行**上算（可复核、可重跑）
    np.savez(Path(args.out).with_suffix(".test.npz"), y=y_te, game=games[test_mask],
             main_before=p_pre, main_after=p_post,
             critic=p_critic[test_mask], base=p_base[test_mask])
    print(json.dumps({k: res["test"][k] for k in res["test"]}, ensure_ascii=False, indent=1))
    print("criterion2", json.dumps(res["criterion2"], ensure_ascii=False))
    print("criterion3", json.dumps(res["criterion3"], ensure_ascii=False))

    # ---- 导出新 net.bin（只换 value 头权重；参数个数不变）
    sd2 = {k: v.detach().clone() for k, v in net.state_dict().items()}
    sd2["heads.value.weight"] = W.detach().clone()
    sd2["heads.value.bias"] = b.detach().clone()
    changed = sorted(k for k in sd2 if not torch.equal(sd2[k], sd[k]))
    res["changed_tensors"] = changed
    if changed != ["heads.value.bias", "heads.value.weight"]:
        raise SystemExit(f"只许改 value 头，实际改了 {changed}")
    net2 = M.build(1, arch=arch, **dims, **X.arch_kwargs(parsed))
    net2.load_state_dict(sd2, strict=True)
    if net2.param_count() != n_params0:
        raise SystemExit(f"参数个数变了：{n_params0} → {net2.param_count()}")
    out_net = Path(args.out)
    X.save_net(sd2, dims, out_net, arch=arch, label="mv1-s37-priv-distill",
               source=f"distill heads.value from privileged critic; base={Path(args.net).name}")
    res["out"] = {"net": str(out_net), "bytes": out_net.stat().st_size,
                  "params": net2.param_count()}
    (out_net.with_suffix(".distill.json")).write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print("out", json.dumps(res["out"], ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- CLI

def cmd_data(args) -> int:
    build_data(Path(args.raw), Path(args.out), args.limit_files)
    return 0


def cmd_train(args) -> int:
    import torch                                                # noqa: PLC0415

    z = np.load(args.data)
    x_pub, x_prv, keys = z["pub"], z["priv"], z["keys"]
    Y = z["y"] if args.label == "final" else z["y_delta"]
    games = keys[:, 0]
    tr, va, te = _split3(games)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    hidden = tuple(int(h) for h in args.hidden.split(","))
    print(f"label={args.label} rows tr/va/te={int(tr.sum())}/{int(va.sum())}/{int(te.sum())} "
          f"d_pub={x_pub.shape[1]} d_priv={x_prv.shape[1]} block={args.priv_block}")
    res: dict = {"label": args.label, "priv_block": args.priv_block,
                 "split": {"train": int(tr.sum()), "val": int(va.sum()), "test": int(te.sum()),
                           "val_games": int(len(np.unique(games[va]))),
                           "test_games": int(len(np.unique(games[te]))),
                           "d_pub": int(x_pub.shape[1]), "d_priv": int(x_prv.shape[1]),
                           "note": "按场切：%10==5 早停、%10==0 报告（互不重叠）"}}
    preds: dict[str, np.ndarray] = {}
    for name, x in (("public_baseline", x_pub),
                    ("privileged_critic", np.concatenate([x_pub, priv_block(x_prv, args.priv_block)], 1))):
        mu = x[tr].mean(0)
        sd = x[tr].std(0)
        sd[sd < 1e-6] = 1.0
        xn = ((x - mu) / sd).astype(np.float32)
        net, info = train_net(xn[tr], Y[tr], xn[va], Y[va], hidden, args.seed,
                              epochs=args.epochs, lr=args.lr, wd=args.wd, drop=args.drop)
        prob = _probs(net, xn[te])
        preds[name] = prob @ CENTERS
        met = prob_metrics(prob, Y[te])
        met.update(params=info["params"], val_ce=info["val_ce"], best_epoch=info["epoch"])
        res[name] = met
        torch.save({"state": net.state_dict(), "hidden": hidden, "mu": mu, "sd": sd,
                    "d_in": int(x.shape[1]), "priv_block": args.priv_block, "drop": args.drop,
                    "label": args.label, "metrics": met}, out / f"{name}.pt")
        print(name, json.dumps({k: round(v, 6) if isinstance(v, float) else v
                                for k, v in met.items()}, ensure_ascii=False))
    # 判据 ②/③：**同一批 test 行** + 按场聚类的 bootstrap 区间
    boot = _boot(preds["privileged_critic"], preds["public_baseline"], Y[te], games[te],
                 n=args.boot, seed=args.seed)
    res["bootstrap"] = boot
    res["criterion2_critic_vs_public"] = {
        "ev_critic": boot["ev_a"], "ev_public": boot["ev_b"],
        "d_ev": boot["d_ev"], "d_ev_ci": boot["d_ev_ci"],
        "spearman_critic_vs_public": spearman(preds["privileged_critic"], preds["public_baseline"])}
    res["criterion3_baseline"] = {"ev_public": boot["ev_b"],
                                  "crps_public": res["public_baseline"]["crps"]}
    (out / "metrics.json").write_text(json.dumps(res, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print("bootstrap", json.dumps(boot, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="privcritic")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("data")
    d.add_argument("--raw", required=True)
    d.add_argument("--out", required=True)
    d.add_argument("--limit-files", type=int, default=None)
    d.set_defaults(func=cmd_data)
    t = sub.add_parser("train")
    t.add_argument("--data", required=True)
    t.add_argument("--out", required=True)
    t.add_argument("--label", choices=["delta", "final"], default="delta")
    t.add_argument("--priv-block", choices=list(PRIV_SLICES), default="full")
    t.add_argument("--hidden", default="64")
    t.add_argument("--epochs", type=int, default=25)
    t.add_argument("--lr", type=float, default=3e-4)
    t.add_argument("--wd", type=float, default=1e-3)
    t.add_argument("--drop", type=float, default=0.3)
    t.add_argument("--boot", type=int, default=1000)
    t.add_argument("--seed", type=int, default=20261020)
    t.set_defaults(func=cmd_train)
    s = sub.add_parser("distill")
    s.add_argument("--data", required=True)
    s.add_argument("--critic", required=True)
    s.add_argument("--baseline", required=True)
    s.add_argument("--net", required=True)
    s.add_argument("--compact", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--label", choices=["delta", "final"], default="delta")
    s.add_argument("--steps", type=int, default=600)
    s.add_argument("--batch", type=int, default=1024)
    s.add_argument("--lr", type=float, default=1e-2)
    s.add_argument("--seed", type=int, default=20261020)
    s.set_defaults(func=cmd_distill)
    args = ap.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

