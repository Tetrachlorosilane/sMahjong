"""v4 **教师预训练**（P1/P2 的冷启动）：把 teacher 的动作当标签，喂四个张量。

## 这一轮在做什么（与设计文档的对应）

`docs/TRAINING-V4.md` §9：**P1 表征与自监督**（用引擎真值做自监督）→ **P2 teacher 冷启动**
（小权重模仿头，退火到 0）。本脚本是这两步的**第一版合并实现**：

| 头 | 监督来源（这一轮） | 说明 |
| --- | --- | --- |
| `policy` | **teacher 的动作**（`chosen_index` / DAgger 的 `teacher_index`） | 教师模仿（BC）—— 冷启动的主力 |
| `value` | `final_scores[seat] − 起点`（千点） | HL-Gauss 软标签（与 v3 的分布价值同口径） |
| `placement` | 整场顺位 | 终局取舍 |
| `belief_hand` | **`aux.npz` 的对手手牌真值**（按"有没有该牌种"二值化） | 自监督真值（上帝视角，只训练/诊断） |
| `belief_tenpai` | `aux.npz` 的对手听牌真值 | 押し引き的输入 |
| `danger` | `aux.npz` 的放铳结果（**只在被选中的那个候选上**有标签） | 第 4 通道 = "任一家" |
| `effect` | sidecar 的逐候选派生量（向听/进张种数/进张枚数） | 牌效辅助（免费标签） |

⚠ 与 PPO 的区别：PPO 的策略损失用**自对弈回报**（`HEAD_SPECS` 里写的），
这一轮用**教师动作**（BC）—— 所以**不需要** reward/优势，也不做 clip。
每一步的权重取自 `model.loss_weights()`（注册表，别在这里再抄一份）。

## 可复现

- `torch.manual_seed(seed)` + **每个 epoch 的洗牌种子固定**（`seed + epoch`）。
- 判据：同 `--data` / `--seed` / `--epochs` 跑两次，`metrics.json` 的
  `history` 逐字段相同（`python/selfcheck.py` 钉着这条）。
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from .. import guard, paths
from . import adv as v4adv
from . import dataset as v4ds
from . import model as M
from . import spec

#: 牌效头 3 维的归一化分母（= `features.DERIVED_SCALE_CAND[:3]`：向听 / 进张种数 / 进张枚数）
EFFECT_SCALE = (8.0, 34.0, 136.0)

#: PPO 的 `log(ρ)` 夹取范围（见 `compute_loss` 的"两道数值保险"）：
#: ±4 ⇒ ρ ∈ [0.018, 54.6]。ε=0.2 的截断区间（[0.8, 1.2]）**远在界内** ⇒ 正常样本逐位无影响；
#: 它界住的是"非学生行的零概率动作让 ρ 溢出"与"A<0 时悲观支 ρA 无界"这两种爆炸
#: （2026-09-28 实测：没有它，训练跑到第 757 步整批 NaN）。
LOG_RATIO_CLAMP = 4.0

#: 事件 token 的**类型块**在 `evt` 里的前 8 维（`blocks.EVT_LAYOUT` 的 `type` one-hot）
EVT_TYPE_DIM = 8


class MaskedEventHead(nn.Module):
    """P1 的**掩码事件重建** pretext 头（`Linear(D_MODEL → 事件类型数)`）。

    ⚠ 它**不进推理**（`inference_heads()` 里没有它）：P1 用它逼事件塔把"上下文里的缺格"
    补出来，训完就丢；checkpoint 里单独存一份（`ssl_head`）以便复现。
    """

    def __init__(self) -> None:
        super().__init__()
        self.proj = nn.Linear(M.D_MODEL, len(spec.EVT_TYPES))

    def forward(self, e_tokens: torch.Tensor) -> torch.Tensor:
        return self.proj(e_tokens)


def mask_events(evt: torch.Tensor, frac: float, gen: torch.Generator) -> tuple:
    """把一部分**真实事件** token 置 0 并返回 `(掩码后的 evt, 掩码位, 类型目标)`。

    - "真实事件"= 类型块非全 0 的行（`blocks.event_matrix` 里 padding 在**前面**且全 0）；
    - 掩码比例按**每条样本**在它的真实事件里抽（没抽到的样本 mask 全 0，不参与损失）。
    """
    b, k, _c = evt.shape
    real = evt[:, :, :EVT_TYPE_DIM].sum(-1) > 0                 # [B,K]
    target = evt[:, :, :EVT_TYPE_DIM].argmax(-1)                 # [B,K] 只在 real 上有意义
    keep = torch.rand(b, k, generator=gen, device="cpu").to(evt.device) < frac
    mask = keep & real
    if not bool(mask.any()):
        return evt, mask, target
    out = evt.clone()
    out[mask] = 0.0
    return out, mask, target


def _device(pref: str) -> str:
    if pref != "auto":
        return pref
    return "cuda" if torch.cuda.is_available() else "cpu"


def _batch(data: dict, idx: np.ndarray, device: str,
           ablate: dict[str, list[tuple[int, int]]] | None = None) -> dict[str, torch.Tensor]:
    """按行号取一个 batch（memmap → torch）。`idx` **升序**（与 v3 的 `bc._batch` 同规矩）。

    ⚠ 候选宽度取**这一份切分自己的** `cand.shape[1]`，不是 `meta["lmax"]`：
    train/val 两份的 lmax 可能不同（`build` 里按切分各自定宽），拿全局 lmax 去做掩码
    会与 logits 的长度对不上（实测报 `size of tensor a (27) must match b (15)`）。

    @param ablate **块消融**：`{张量名: [(起点, 宽度), …]}` ⇒ 那些通道整段置 0
        （`docs/TRAINING-V4.md` §8.3 的消融矩阵；只改输入，不动权重，也不碰标签）
    """
    lmax = int(data["cand"].shape[1])
    nleg = np.asarray(data["nlegal"][idx], dtype=np.int64)
    cand = np.asarray(data["cand"][idx], dtype=np.float32)
    mask = np.arange(lmax)[None, :] < nleg[:, None]
    b = {
        "tile": torch.from_numpy(np.asarray(data["tile"][idx], dtype=np.float32)).to(device),
        "evt": torch.from_numpy(np.asarray(data["evt"][idx], dtype=np.float32)).to(device),
        "ctx": torch.from_numpy(np.asarray(data["ctx"][idx], dtype=np.float32)).to(device),
        "cand": torch.from_numpy(cand).to(device),
        "mask": torch.from_numpy(mask).to(device),
        "label": torch.from_numpy(np.asarray(data["label"][idx], dtype=np.int64)).to(device),
        "value": torch.from_numpy(np.asarray(data["value"][idx], dtype=np.float32)).to(device),
        # `rtg`（逐决策 reward-to-go，千点）：第六轮加的 λ=1 GAE 目标。老数据集里是 NaN ⇒
        # `--value-target rtg` 会在入口处硬拒（见 `train()`），不会静默拿 NaN 去训。
        "rtg": torch.from_numpy(np.asarray(data["rtg"][idx], dtype=np.float32)).to(device)
        if data.get("rtg") is not None else None,
        "placement": torch.from_numpy(
            np.asarray(data["placement"][idx], dtype=np.int64)).to(device),
        # `delta`（本小局收支，**点 → 千点**）：P1b 的小局级值头目标（`--value-target delta`）。
        #   `rtg` 是小局内的常量、`delta` 也是 ⇒ 两者在"同一小局共享一个目标"这点上一致。
        "delta": torch.from_numpy(np.asarray(data["delta"][idx], dtype=np.float32) / 1000.0).to(device)
        if data.get("delta") is not None else None,
        "effect": torch.from_numpy(
            np.asarray(data["effect"][idx], dtype=np.float32)).to(device),
        "nlegal": torch.from_numpy(nleg).to(device),
    }
    for src, dst in (("aux_opp_hand", "opp_hand"), ("aux_opp_tenpai", "opp_tenpai"),
                     ("aux_opp_dealin", "opp_dealin"), ("aux_own_tenpai", "own_tenpai"),
                     ("aux_own_shanten_after", "own_shanten")):
        if data.get(src) is not None:
            b[dst] = torch.from_numpy(np.asarray(data[src][idx], dtype=np.float32)).to(device)
    if ablate:
        for tensor, spans in ablate.items():
            if tensor not in b:
                continue
            for start, width in spans:
                b[tensor][..., start:start + width] = 0.0
    return b


def _behaviour_values(model: M.V4Model, data: dict, device: str, batch: int) -> np.ndarray:
    """**行为策略的价值期望**（千点；`softmax(value) · 分箱中心`）—— 手级 GAE 的 `V_old`。

    为什么要"行为策略的 V"而不是"当前 V"：优势必须相对**采样时的那个策略/评价**才算得对
    （`logp_old` 同一道理）；而且算一次就**冻结**，否则一个 epoch 内 baseline 一直在动
    （`docs/TRAINING-V4.md` §14 P2-①）。
    """
    center = torch.linspace(-M.VALUE_RANGE, M.VALUE_RANGE, M.VALUE_BINS).to(device)
    n = int(data["nlegal"].shape[0])
    out = np.zeros(n, dtype=np.float64)
    model.eval()
    with torch.no_grad():
        for i0 in range(0, n, batch):
            idx = np.arange(i0, min(i0 + batch, n))
            b = _batch(data, idx, device)
            v = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"])["value"]
            out[i0:i0 + idx.size] = (torch.softmax(v.float(), dim=-1) * center).sum(-1).cpu().numpy()
    return out


def _fit_baseline(v_row: np.ndarray, r_row: np.ndarray, keep: np.ndarray | None,
                  mode: str) -> tuple[float, float]:
    """把 baseline **线性重标定**到奖励的尺度：用 `α + β·V` 当基线（最小二乘闭式）。

    为什么要这一步（第十九轮的红证）：P1b 的 `std(A_raw)/std(delta)=2.671×` **不是**"critic 没用"，
    而是**量纲接错了** —— 行为策略 `v4-p3-001` 的值头学的是**整场**收文（`value_target=final`），
    它的 `V_old` 实测 `std 12.392` 千点，而奖励（本小局收支）只有 `std 5.047` 千点。
    `A = r − V` 于是把"整场尺度"的量从"小局尺度"的量里减掉，优势方差必然被放大（≈√(5²+12²)=13）。
    ⚠ 基线只要是**状态（动作无关）的函数**，怎么线性变换都**不改变梯度的期望** —— 变的只是方差。
    所以"按最小二乘把尺度对齐"是同一族基线里**方差最小**的那一个（`β=0` 的常数基线是它的嵌套特例
    ⇒ 重标定后 `std(A)` **不可能**比"只减均值"更差；自检把这条钉成红证）。
    """
    if mode == "none":
        return 0.0, 1.0
    v = v_row if keep is None else v_row[keep]
    r = r_row if keep is None else r_row[keep]
    mu_v, mu_r = float(v.mean()), float(r.mean())
    if mode == "mean":
        return mu_r, 0.0
    var = float(((v - mu_v) ** 2).mean())
    beta = float(((v - mu_v) * (r - mu_r)).mean()) / var if var > 1e-12 else 0.0
    return mu_r - beta * mu_v, beta


def _hand_advantage(data: dict, v_old: np.ndarray, *, gamma: float, lam: float,
                    rank_weight: float, is_student: np.ndarray | None,
                    mode: str = "gae", baseline_fit: str = "scale") -> dict:
    """小局级信用分配 → 逐决策的 `(adv, vtarget)` + 体检数字。

    两种模式（`docs/TRAINING-V4.md` §14.6）：

    · **`gae`**（P1 第一版）：奖励 = 本小局收支（+ 终局顺位点），**TD(λ) + bootstrap**（跨小局）。
      实测：λ-回报的 EV 只到 0.094（`std(A_raw)` 反而 1.24×）—— 因为 λ=0.9 的 λ-回报
      仍然主要是"未来若干小局的和"，而那一族的可解释上限 ≈0.1（引擎真值线性参照）。
    · **`hand`**（P1b，证据指向的那一步）：**把时间尺度压到一小局** ——
      `A = 本小局收支 − V(s)`（**不跨小局 bootstrap**），值头目标就是**本小局收支**本身。
      依据：本小局收支的可解释方差实测 **0.632**（引擎真值 10 列线性），而多手后缀和只有 0.090。
      终局的顺位点项**只加在优势上**（不进取值头目标）：顺位点四家零和 ⇒ 期望 0 就是它的基线。

    ⚠ 归一化**在这里一次算完**（学生行上的全局 mean/std）：放进每个 batch 里做 z-score 的话
    优势的绝对尺度会随 batch 变，PPO 的 clip ε 就失去语义（§14 P2-②）。
    """
    n = int(data["nlegal"].shape[0])
    rp = data.get("rank_points")
    if rank_weight and rp is None:
        raise SystemExit("--rank-weight > 0 但数据集没有 `rank_points` 列 —— 用新版 `v4.dataset build` "
                         "重建（它从采集目录的 summary.json 读生产者登记的顺位点）")
    if rank_weight:
        finite = bool(np.isfinite(np.asarray(rp[:n], dtype=np.float32)).all())
        if not finite:
            raise SystemExit("--rank-weight > 0 但 `rank_points` 列有 NaN（采集目录缺 summary.json）"
                             "—— 要么补齐 summary.json 重建数据集，要么把 --rank-weight 设 0")
    starts, hand_of_row, next_hand, reward_rows = v4adv.hand_chain(
        data["game"][:n], data["hand_no"][:n], data["seat"][:n])
    v_h = np.asarray(v_old, dtype=np.float64)[starts]
    v_row = v4adv.expand_hand(v_h, hand_of_row)          # 同一小局共享一个 V（小局级 baseline）
    d_row = np.asarray(data["delta"][:n], dtype=np.float64) / 1000.0
    rp_row = np.asarray(rp[:n], dtype=np.float64) if rp is not None else None
    term_hand = next_hand < 0
    keep_m = None if is_student is None else (np.asarray(is_student[:n]) > 0)
    # ★ 基线的尺度对齐（见 `_fit_baseline` 的推导）：在**学生行**上做最小二乘。
    alpha, beta = _fit_baseline(v_row, d_row, keep_m, baseline_fit)
    v_used_h = alpha + beta * v_h
    v_used_row = alpha + beta * v_row
    if mode == "hand":
        adv = d_row - v_used_row                          # 小局级 baseline，无跨小局 bootstrap
        vtarg = d_row.copy()                             # 值头目标 = 本小局收支（千点）
        r_h = d_row[reward_rows]
        if rank_weight and rp_row is not None:
            adv = adv + rank_weight * np.where(term_hand[hand_of_row], rp_row, 0.0)
        stat_extra = {"mode": "hand", "bootstrap": 0.0}
    else:
        r_h = v4adv.hand_reward(data["delta"][:n], rp_row, hand_of_row, next_hand, reward_rows,
                                rank_weight=rank_weight)
        adv_h, vt_h = v4adv.gae_hand(r_h, v_used_h, next_hand, gamma=gamma, lam=lam)
        adv = v4adv.expand_hand(adv_h, hand_of_row)
        vtarg = v4adv.expand_hand(vt_h, hand_of_row)
        stat_extra = {"mode": "gae", "bootstrap": 1.0}
    keep = keep_m
    raw = adv.copy()                       # 归一化**之前**的优势：方差削减要在它上面量
    if keep is not None and keep.any():
        mu = float(adv[keep].mean())
        sd = float(adv[keep].std())
        adv = np.where(keep, (adv - mu) / (sd + 1e-6), 0.0)
    stats = {"hands": float(starts.size), "gamma": float(gamma), "lam": float(lam),
             "rank_weight": float(rank_weight), "reward_mean": float(r_h.mean()),
             "reward_std": float(r_h.std()), "v_old_mean": float(v_h.mean()),
             "v_old_std": float(v_h.std()), "baseline_fit": baseline_fit,
             "baseline_alpha": float(alpha), "baseline_beta": float(beta),
             "v_used_std": float(v_used_h.std()), **stat_extra}
    ref = raw if keep is None else raw[keep]
    # ⚠ **方差削减必须在归一化之前量**：归一化之后学生行的 std 恒 ≈1、非学生行是 0，
    #   整列 std ≈0.5 ⇒ 拿它比参考量会得到"看起来砍掉 95%"的假象（第一版报的 0.054×）。
    # 参考量按模式选：`hand` 模式的自然参照是**本小局收支**（不是 `rtg`）。
    if mode == "hand":
        base = d_row if keep is None else d_row[keep]
        base_name = "delta"
    else:
        rtg = data.get("rtg")
        base = (np.asarray(rtg[:n], dtype=np.float64) if rtg is not None else r_h)
        if keep is not None and rtg is not None:
            base = base[keep]
        base_name = "rtg"
    stats.update({
        "base_name": base_name, "base_std": float(np.std(base)),
        "adv_std_raw": float(ref.std()),
        "std_ratio_raw": float(ref.std() / np.std(base)) if np.std(base) > 0 else float("nan"),
        "std_ratio_unfit": float(np.std((d_row - v_row)[keep] if keep is not None else d_row - v_row)
                                 / np.std(base)) if np.std(base) > 0 else float("nan"),
        "adv_std_norm_student": float(adv[keep].std()) if keep is not None else float(adv.std()),
    })
    # ★ 红证：重标定后**不可能**比"只减均值"更差（`β=0` 是它的嵌套特例；最小二乘的解就是最优）。
    #   这条断言是"2.671× 那种量纲事故"的守卫：再出现同类接错，日志里先炸，而不是悄悄训完。
    if baseline_fit == "scale" and mode == "hand" and np.isfinite(stats["std_ratio_raw"]):
        limit = stats["std_ratio_unfit"] if np.isfinite(stats["std_ratio_unfit"]) else 1.0
        if stats["std_ratio_raw"] > min(1.0, limit) + 1e-6:
            raise SystemExit(f"基线重标定后优势方差不降反升（{stats['std_ratio_raw']:.4f}× > "
                             f"{min(1.0, limit):.4f}×）—— 最小二乘解不可能比常数基线差，"
                             f"说明 `_fit_baseline` 或优势构造写错了")
    return {"adv": adv.astype(np.float32), "vtarget": vtarg.astype(np.float32), "stats": stats}


def _row_weights(data: dict, idx, beta: float, device: str):
    """逐行权重 = `is_student` 掩码 × RWR 权重（`exp(clip(delta/1000/β, -2, 2))`）。

    为什么这两样必须一起给（见 `docs/TRAINING-V4.md` §「P3 开局」）：
    ① **掩码**：自对弈轨迹里四条座位的动作都在，不加掩码就会把**对手网/老师的动作算进策略损失**
       （v3 实测学生行占比 0.75 而非 0.25，静默失真）；
    ② **权重**：纯模仿只会复现当前水平的动作分布，`exp(回报/β)` 才是"往赢的打法偏"的那一步
       （RWR / AWR-lite：没有 critic 基线，就用**本小局收支**当回报 —— 这是它与正式 PPO 的差距，
       也是这一轮刻意选的最小可证版本）。

    @param beta 温度（**千点**为单位，例如 8 = 8000 点）；`<= 0` = 不加权（全 1）
    """
    keep = None
    if data.get("is_student") is not None:
        v = np.asarray(data["is_student"][idx], dtype=np.float32)
        if (v == 0).any():
            keep = torch.from_numpy(v).to(device)
    w = None
    if beta and beta > 0 and data.get("delta") is not None:
        d = np.asarray(data["delta"][idx], dtype=np.float32) / 1000.0        # 点 → 千点
        adv = np.clip(d / float(beta), -2.0, 2.0)
        w = torch.from_numpy(np.exp(adv).astype(np.float32)).to(device)
    return keep, w


def _advantages(out: dict[str, torch.Tensor], b: dict[str, torch.Tensor],
                row_keep: torch.Tensor | None, key: str = "value") -> torch.Tensor:
    """`A = R − E[V(s)]`，**只在学生行上归一化**（设计 §7.4 / v3 P4 的硬口径）。

    - `R` = 数据集里的 `value`（`final_scores − 起点`，千点）或 **`rtg`（逐决策 reward-to-go）**；
      ⚠ 两者**必须是值头正在学的那个量**（`--value-target` 同时决定这里与值头目标），否则
      critic 与优势不同量纲，优势会被系统性偏移（第五轮的 `A = R_整场 − E[V(s)]` 就是这个毛病：
      它没有"这一手之后发生了什么"，信用分配粗到整场，实测 Δ=−3.54 顺位点）；
    - `E[V(s)]` = 值头 51 个分箱的期望（分箱中心 `linspace(-30, +30)`，与 `VALUE_RANGE` 同量纲）；
    - ⚠ **归一化只用学生行**：把对手/老师那 3/4 的行算进均值方差，会把优势的尺度带偏
      （v3 实测过学生行占比 0.75 而非 0.25 那种静默失真）。
    """
    center = torch.linspace(-M.VALUE_RANGE, M.VALUE_RANGE, M.VALUE_BINS,
                            device=out["value"].device)
    v_hat = (torch.softmax(out["value"], dim=-1) * center).sum(-1)
    adv = b[key] - v_hat
    if row_keep is not None:
        k = row_keep > 0
        if bool(k.any()):
            sel = adv[k]
            adv = torch.where(k, (adv - sel.mean()) / (sel.std(unbiased=False) + 1e-6),
                              torch.zeros_like(adv))
    return adv


def _behaviour_logprobs(model: M.V4Model, data: dict, device: str, batch: int,
                        temp: float) -> torch.Tensor:
    """采集那一步的 `log π_old(a|s)`（**用采集时的权重重算**，不是从轨迹里读的）。

    为什么能重算：前向是**确定性**的（无 dropout），输入就是同一份 obs 张量、
    权重就是采集用的那份 checkpoint、温度就是策略串里的 `#T`
    ⇒ 重算出来的 log-prob 与当时采样用的分布逐位相同（容差内）。这是 PPO 能离线做的前提。
    """
    model.eval()
    n = int(data["nlegal"].shape[0])
    out = torch.zeros(n, dtype=torch.float32)
    with torch.no_grad():
        for i0 in range(0, n, batch):
            idx = np.arange(i0, min(i0 + batch, n))
            b = _batch(data, idx, device)
            logits = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"])["policy"]
            if temp > 0:
                logits = logits / float(temp)
            lp = torch.log_softmax(logits, dim=-1).gather(1, b["label"][:, None]).squeeze(1)
            out[i0:i0 + idx.size] = lp.to("cpu")
    return out


def _load_behaviour(spec: str, device: str) -> M.V4Model:
    """采集用的那份权重：`*.pt`（checkpoint）或 `net.bin`（格式 2）都收。"""
    p = Path(spec)
    if not p.is_file():
        raise SystemExit(f"--behaviour 找不到文件：{p}")
    if p.suffix == ".bin":
        from . import export as v4export
        parsed = v4export.read_net(p)
        model = M.build(1, **parsed["dims"])
        model.load_state_dict(v4export.state_from_net(parsed), strict=True)
    else:
        ck = torch.load(p, map_location="cpu", weights_only=False)
        model = M.build(1, **(ck.get("config", {}).get("dims") or M.build(1).dims()))
        model.load_state_dict(ck["model"], strict=True)
    return model.to(device).eval()


def _policy_logits(out: dict[str, torch.Tensor], temp: float) -> torch.Tensor:
    """按**行为策略的温度**换算出策略 logits：`π(·|s;T) = softmax(logits / T)`。

    ⚠ 为什么这必须是 `logp_new` 与 `logp_old` **同一把尺子**（2026-09-28 实测的坑）：
    采集侧的策略串 `net:<权重>#T` 采的是 `softmax(logits / T)`（`Logits.sampleSoftmax`），
    所以 `π_old` 是**带温度的那个分布**。若只把温度用在 `logp_old` 上，`logp_new` 还是 `T=1` 的分布，
    两者不是同一个分布族 —— 温度越低差得越狠：`#0.5` 实测 `logp_old` 到 −70.5 而 `logp_new ≈ 0`，
    `ρ = exp(65) ≈ 2·10^15`、策略损失 `2.5·10^11`、**一个 step 就把整网打成 NaN**，
    而训练**照跑完 4 个 epoch、还落盘了一份废 checkpoint**（只有 `val top1` 掉回首合法基线 0.156 是线索）。
    ⛔ `--behaviour-temp` 缺省 1.0 时这个 bug 完全不可见（`logits/1.0 == logits`），所以它藏了一整轮。
    """
    return out["policy"] if float(temp) == 1.0 else out["policy"] / float(temp)


def assert_behaviour_consistency(model: M.V4Model, data: dict, idx: np.ndarray,
                                 logp_old: torch.Tensor, temp: float,
                                 device: str) -> float:
    """**PPO 口径闸门**：初始化时 `π_new` 必须**就是** `π_old`（同权重 + 同温度）。

    返回 `KL(π_old‖π_new)`（应 ≈0）；超出容差**直接报错**。为什么值得一道闸门
    （2026-09-28 实测）：`#0.5` 采集时若忘了给 `logp_new` 同一把温度尺子，ρ 会炸到 `2·10^15`、
    策略损失 `2.5·10^11`、**一个 step 之后整网 NaN**，而训练**照跑完 4 个 epoch、还落盘一份
    废 checkpoint**（唯一线索是 `val top1` 掉回首合法基线）。`--behaviour-temp` 缺省 1.0 时
    这个 bug 完全不可见（`logits/1.0 == logits`），所以它整整藏了一轮。

    ⚠ 闸门查的是"**两边是不是同一个分布**"：`--init` 与 `--behaviour` 不是同一份权重、
    或温度只用在了一侧，都会在这里当场炸。它**查不出**"温度填成了另一个值但两边一致" ——
    那条由 `train()` 里"`--behaviour-temp` 必须与 meta 的 `#T` 一致"那条闸门管。
    """
    with torch.no_grad():
        b0 = _batch(data, idx, device)
        o0 = model(b0["tile"], b0["evt"], b0["ctx"], b0["cand"], mask=b0["mask"])
        lp_new = torch.log_softmax(_policy_logits(o0, temp), dim=-1).gather(
            1, b0["label"][:, None]).squeeze(1)
        kl = float((logp_old[idx].to(device) - lp_new).mean())
    if not math.isfinite(kl) or abs(kl) > 1e-2:
        raise SystemExit(
            f"PPO 口径不一致：初始化时 KL(π_old‖π_new) = {kl:.4g}（应为 ≈0）—— 检查"
            f"① `--init` 是不是与 `--behaviour` 同一份权重；② `--behaviour-temp` 是不是与数据集"
            f" `student` 串里的 `#T` 一致（`logp_new` 与 `logp_old` 必须在同一个 `softmax(logits/T)` 上算）")
    return kl


def compute_loss(out: dict[str, torch.Tensor], b: dict[str, torch.Tensor],
                 weights: dict[str, float] | None = None,
                 ssl: tuple | None = None,
                 row_keep: torch.Tensor | None = None,
                 row_w: torch.Tensor | None = None,
                 ppo: tuple | None = None,
                 value_key: str = "value",
                 policy_temp: float = 1.0,
                 adv_fixed: torch.Tensor | None = None) -> tuple[torch.Tensor, dict[str, float]]:
    """多头加权损失。返回 `(总损失, 逐头损失字典)`。

    @param weights 逐头权重（`model.loss_weights()` 的注册表；分阶段训练时按阶段缩放）
    @param ssl     `(ssl_head, mask, target)` —— 掩码事件重建（P1 pretext）；不传就不算
    @param row_keep 逐行掩码（学生行 = 1 / 对手行 = 0）—— **只作用于动作相关的头**
        （`policy` / `effect` / `danger`：它们的标签是"谁动了手、动了之后怎样"）
    @param row_w   逐行权重（RWR）—— 与 `row_keep` 同一适用范围；
        ⚠ 状态级头（`value` / `placement` / `belief_*`）**不吃**掩码与权重：那些标签讲的是"这个局面"，
        与四条座位里谁动的手无关（吃了反而会把"对手造成的局面"也往学生的回报上算）。
    @param ppo     `(logp_old, clip, entropy_coef)` —— 用 **PPO 截断替代项**取代策略头的交叉熵：
        `ρ = exp(logπ_new − logπ_old)`、`−min(ρA, clip(ρ,1±ε)A) + c·H(π)`。
        `A` 由 `_advantages()` 给（学生行归一化）；`logp_old` 由 `_behaviour_logprobs()` 重算。
    @param value_key 值头的监督目标取自哪一列：`value`（整场结果，缺省）/ `rtg`（逐决策
        reward-to-go）。⚠ 它**同时**决定优势里的 `R` —— 两处必须同源，否则 critic 与优势
        不同量纲（见 `_advantages()` 的注释）。
    @param policy_temp 行为策略的采样温度 `T`：`logp_new` 与 `logp_old` 都必须在
        `softmax(logits/T)` 这个**同一个分布**上算（见 `_policy_logits()` 的坑）。
        ⚠ 它只影响 PPO 的替代项与熵，**不影响**值头/辅助头（那些是状态级的量）。
    """
    w = weights if weights is not None else M.loss_weights()
    parts: dict[str, float] = {}
    total = torch.zeros((), device=b["label"].device)
    eff = row_keep if row_w is None else (row_w if row_keep is None else row_keep * row_w)

    def reduce_row(per_row: torch.Tensor, w: torch.Tensor | None = None) -> torch.Tensor:
        """逐行损失 → 标量（有掩码/权重时按权重归一；没有时就是 mean，与原行为逐位相同）。

        @param w 用哪份逐行因子：缺省 = `eff`（掩码 × RWR）；只传掩码 = 不吃 RWR。
        """
        use = eff if w is None else w
        if use is None:
            return per_row.mean()
        denom = use.sum().clamp_min(1e-6)
        return (per_row * use).sum() / denom

    if ppo is not None:
        logp_old, clip_eps, ent_coef = ppo
        logits_t = _policy_logits(out, policy_temp)
        logp_new = torch.log_softmax(logits_t, dim=-1).gather(
            1, b["label"][:, None]).squeeze(1)
        # `adv_fixed`（手级 GAE 的预计算优势，**已按学生行全局归一化**）优先：
        #   一条样本的优势在整轮里是常量 ⇒ baseline 不再"跟着 critic 动"（§14 P2-①）。
        adv = adv_fixed if adv_fixed is not None else _advantages(out, b, row_keep, key=value_key)
        # ⚠⚠ **两道数值保险**（2026-09-28 实测：不加就是"训练跑到第 757 步整批 NaN"）。
        # 机制：**非学生行**（teacher / 随机 的动作）在**学生网**下几乎必然是零概率 ——
        # 实测 `logp_new` 能到 −1000 量级（网的 logits 幅度到 563，`#0.5` 再翻倍）⇒
        # `ρ = exp(logp_new − logp_old)` 溢出成 **inf**；而优势在那些行上的定义值正是 **0**
        # ⇒ `inf * 0 = NaN` 污染 `(per_row * use).sum()`，整批 loss 变 NaN。
        # 症状很好认：`parts["policy"] = nan` 而 `kl`/`clip_frac`/`logp_gap` **全是有限**
        # （它们只看学生行），`|θ|max` 也纹丝不动。
        #   ① `log_ratio` 夹在 ±`LOG_RATIO_CLAMP`：既堵住 inf/NaN，也顺手界住 `A<0` 那条
        #      **悲观支** `ρA`（它在 ρ≫1+ε 时是无界的 —— dual-clip 的动机；ε=0.2 的截断区间
        #      远在界内，所以对正常样本**逐位无影响**）；
        #   ② 替代项在**未选中的行上显式置 0**：`where` 是选择而不是乘法，NaN 漏不过来。
        log_ratio = (logp_new - logp_old).clamp(-LOG_RATIO_CLAMP, LOG_RATIO_CLAMP)
        ratio = torch.exp(log_ratio)
        surr = -torch.min(ratio * adv, torch.clamp(ratio, 1.0 - clip_eps, 1.0 + clip_eps) * adv)
        if row_keep is not None:
            surr = torch.where(row_keep > 0, surr, torch.zeros_like(surr))
        l_policy = reduce_row(surr, row_keep)
        with torch.no_grad():
            # ⚠ 学生行可能一批里一条都没有（`--student` 掩码 + 小 batch）⇒ 先判空再算：
            #   空张量的 `.mean()` 是 NaN、`.max()` 直接抛（`logp_gap` 是后加的，踩到了这条）
            sel = row_keep > 0 if row_keep is not None else None
            n_sel = int(sel.sum()) if sel is not None else int(adv.numel())
            parts["adv_abs"] = float(adv.abs().mean())
            if n_sel > 0:
                sub = slice(None) if sel is None else sel
                parts["kl"] = float((logp_old - logp_new)[sub].mean())
                r = ratio[sub]
                parts["clip_frac"] = float(((r - 1.0).abs() > clip_eps).float().mean())
                # `logp_gap` = `|log π_new − log π_old|` 的最大值：温度口径或权重没对上时它会先炸
                # （`#0.5` 那次是 65 量级，正常应 ≲ 1）—— 它比 `clip_frac` 更早暴露问题
                parts["logp_gap"] = float((logp_new - logp_old)[sub].abs().max())
        if ent_coef:
            p = torch.softmax(logits_t, dim=-1)
            ent = -(p * torch.log(p.clamp_min(1e-9))).sum(-1)
            l_policy = l_policy - ent_coef * reduce_row(ent, row_keep)
        parts["policy"] = float(l_policy.detach())
        total = total + w["policy"] * l_policy
    else:
        l_policy = reduce_row(F.cross_entropy(out["policy"], b["label"], reduction="none"))
        parts["policy"] = float(l_policy.detach())
        total = total + w["policy"] * l_policy

    # 值头：HL-Gauss **软标签**的交叉熵（与自检里"每行和为 1"同一套目标）
    target = M.hl_gauss_targets(b[value_key])
    l_value = -(F.log_softmax(out["value"], dim=-1) * target).sum(-1).mean()
    parts["value"] = float(l_value.detach())
    total = total + w["value"] * l_value

    pl = b["placement"]
    if "placement" in b and bool((pl >= 0).any()):
        keep = pl >= 0
        l_place = F.cross_entropy(out["placement"][keep], pl[keep])
        parts["placement"] = float(l_place.detach())
        total = total + w["placement"] * l_place

    if "opp_hand" in b:
        # 对手手牌：按"有没有该牌种"的二值化目标做**逐牌种 BCE**（34 类"哪一张"不适用：
        #   手牌是一个 13 张的**集合**，不是"选一个牌种"）
        tgt = (b["opp_hand"] > 0).float()
        l_bh = F.binary_cross_entropy_with_logits(out["belief_hand"], tgt)
        parts["belief_hand"] = float(l_bh.detach())
        total = total + w["belief_hand"] * l_bh
    if "opp_tenpai" in b:
        l_bt = F.binary_cross_entropy_with_logits(out["belief_tenpai"], b["opp_tenpai"])
        parts["belief_tenpai"] = float(l_bt.detach())
        total = total + w["belief_tenpai"] * l_bt
    if "opp_dealin" in b:
        # 危险头是**逐候选**的，而放铳结果只有**实际打出的那一张**才有标签 ⇒
        #   只在那一个候选上算（第 4 通道 = "任一家"）
        rows = torch.arange(b["label"].shape[0], device=b["label"].device)
        d = out["danger"][rows, b["label"]]                    # [B,4]
        any_deal = (b["opp_dealin"].sum(-1) > 0).float()
        per_row = (F.binary_cross_entropy_with_logits(d[:, :3], b["opp_dealin"], reduction="none")
                   .mean(-1)
                   + F.binary_cross_entropy_with_logits(d[:, 3], any_deal, reduction="none")) / 2.0
        # ⚠ 危险头**只吃掩码、不吃 RWR 权重**：它标定的是"打这张的放铳概率"，
        #   按回报加权会把"赢的局"（多半没放铳）放大 ⇒ 概率被系统性压低（实测 danger 损失
        #   0.48 → 0.68 一去不回）。策略/牌效才该往高回报的行偏。
        l_d = reduce_row(per_row, row_keep)
        parts["danger"] = float(l_d.detach())
        total = total + w["danger"] * l_d
    if b.get("effect") is not None:
        scale = torch.tensor(EFFECT_SCALE, device=b["effect"].device).view(1, 1, 3)
        tgt = b["effect"] / scale
        m = b["mask"].unsqueeze(-1).expand_as(tgt)
        per_row = (((out["effect"] - tgt) ** 2 * m).sum(dim=(1, 2))
                   / m.sum(dim=(1, 2)).clamp_min(1.0))
        l_e = reduce_row(per_row)
        parts["effect"] = float(l_e.detach())
        total = total + w["effect"] * l_e
    if ssl is not None:
        head, mask, target = ssl
        logits = head(out["e_tokens"])                       # [B,K,类型数]
        if bool(mask.any()):
            l_ssl = F.cross_entropy(logits[mask], target[mask])
            parts["ssl"] = float(l_ssl.detach())
            total = total + w.get("ssl", 1.0) * l_ssl
    return total, parts


@torch.no_grad()
def evaluate(model: M.V4Model, data: dict, device: str, batch: int = 512,
             ssl_head: MaskedEventHead | None = None, mask_frac: float = 0.0,
             row_keep=None, value_key: str = "value",
             ablate: dict[str, list[tuple[int, int]]] | None = None,
             extra: dict[str, np.ndarray] | None = None) -> dict:
    """val：教师动作一致率（总/按类型）+ 首合法基线 + 各头损失 + SSL 掩码重建准确率。

    @param row_keep 只在**这些行**上统计一致率（自对弈数据里 = 学生那一代的行；
        不给就统计全部行）。⚠ 损失仍在全部行上算（那是"这一局的局面"的损失，与谁动的手无关）。
    @param ablate **块消融**（消融矩阵）：val 必须与 train 用同一份消融输入，否则表读不出来
    @param extra 额外逐行数组（键名 = batch 键）：`gae-hand` 的 `vtarget` 走它 —— 否则 val 的
        `compute_loss` 会因为取不到 `vtarget` 直接 KeyError
    """
    model.eval()
    n = int(data["nlegal"].shape[0])
    types = data["meta"].get("action_types", [])
    hit = tot = 0
    by_type: dict[str, list[int]] = {}
    first_legal = 0
    loss_sum = 0.0
    parts_sum: dict[str, float] = {}
    ssl_hit = ssl_n = 0
    gen = torch.Generator(device="cpu").manual_seed(12345)          # val 掩码固定 ⇒ 同种子可比
    for i0 in range(0, n, batch):
        idx = np.arange(i0, min(i0 + batch, n))
        b = _batch(data, idx, device, ablate=ablate)
        if extra:
            for k, arr in extra.items():
                b[k] = torch.from_numpy(np.asarray(arr[idx], dtype=np.float32)).to(device)
        ssl = None
        if ssl_head is not None and mask_frac > 0:
            evt_masked, mask_rows, target = mask_events(b["evt"], mask_frac, gen)
            b["evt"] = evt_masked
            ssl = (ssl_head, mask_rows, target)
        out = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"])
        # ⚠ 掩码要喂进损失：自对弈数据集里 `random` 那 1/4 行的动作在训练过的网看来**几乎是零概率**
        #   （实测逐行 CE 中位 0.05 / 90 分位 24 / 最大 672）—— 不遮的话"策略 CE"这个指标会被它带跑，
        #   而 PPO 的 log 比在那些行上也没有意义（策略损失本来就只算学生行）
        kb_e = None if row_keep is None else torch.from_numpy(
            np.asarray(row_keep[idx], dtype=np.float32)).to(device)
        loss, parts = compute_loss(out, b, ssl=ssl, row_keep=kb_e, value_key=value_key)
        loss_sum += float(loss) * idx.size
        for k, v in parts.items():
            parts_sum[k] = parts_sum.get(k, 0.0) + v * idx.size
        if ssl is not None and bool(mask_rows.any()):
            pred = ssl_head(out["e_tokens"]).argmax(-1)
            ssl_hit += int((pred[mask_rows] == target[mask_rows]).sum())
            ssl_n += int(mask_rows.sum())
        pred = out["policy"].argmax(dim=-1)
        sel = None
        if row_keep is not None:
            sel = torch.from_numpy(np.asarray(row_keep[idx], dtype=bool)).to(pred.device)
            if not bool(sel.any()):
                continue
        def take(t: torch.Tensor) -> torch.Tensor:
            return t if sel is None else t[sel]
        hit += int((take(pred) == take(b["label"])).sum())
        first_legal += int((take(b["label"]) == 0).sum())
        tot += int(take(b["label"]).numel())
        # 按动作类型分解（教师偏爱哪些类型、模型学会了没有）—— 类型来自数据集里的 `label_type`
        if data.get("label_type") is not None:
            tids = np.asarray(data["label_type"][idx], dtype=np.int64)
            keep_list = [True] * len(tids) if sel is None else sel.cpu().numpy().tolist()
            for tid, p, y, kp in zip(tids.tolist(), pred.tolist(), b["label"].tolist(), keep_list):
                if not kp:
                    continue
                name = types[tid] if 0 <= tid < len(types) else "?"
                by_type.setdefault(name, [0, 0])
                by_type[name][1] += 1
                if p == y:
                    by_type[name][0] += 1
    out = {"n": tot, "top1": hit / max(1, tot), "first_legal_acc": first_legal / max(1, tot),
           "loss": loss_sum / max(1, tot),
           **{f"loss_{k}": v / max(1, tot) for k, v in parts_sum.items()},
           "by_type": {k: {"n": v[1], "top1": v[0] / max(1, v[1])} for k, v in by_type.items()}}
    if ssl_n:
        out["ssl_acc"] = ssl_hit / ssl_n
        out["ssl_n"] = ssl_n
    return out


#: 三个阶段（**这一轮的核心改动**：第一轮辅助头不收敛 = 多任务权重冲突，所以分阶段）
#: - `a`：只训**策略 + 牌效**（把主干先学会"打哪张"），辅助头权重为 0；
#: - `b`：**冻结主干**，只训各头（头是随机初始化的，让每个头在固定表示上先收敛）；
#: - `c`：联合微调（注册权重全开 + 余弦降 lr），SSL 掩码重建全程在 a/c 里开。
STAGE_WEIGHTS: dict[str, dict[str, float]] = {
    "a": {"policy": 1.0, "effect": 0.3, "value": 0.0, "placement": 0.0,
          "belief_hand": 0.0, "belief_tenpai": 0.0, "danger": 0.0},
    "b": {},          # 空 = 用注册权重（`M.loss_weights()`）
    "c": {},
}


def _stage_of(step: int, total: int, frac_a: float, frac_b: float) -> str:
    """按全局步号定阶段（默认 25% / 35% / 40%）。"""
    if step < total * frac_a:
        return "a"
    if step < total * (frac_a + frac_b):
        return "b"
    return "c"


def save_checkpoint(path, model, ssl_head, meta, weights, extra: dict) -> None:
    torch.save({"model": model.state_dict(),
                "ssl_head": ssl_head.state_dict(),
                "config": {"model": "v4", "params": model.param_count(), "dims": model.dims()},
                "dataset_meta": meta,
                "feature_version": spec.FEATURE_VERSION_V4,
                "blocks_fingerprint": spec.fingerprint(),
                "loss_weights": weights,
                **extra}, path)


def train(args) -> dict:
    paths.ensure_root()
    data_dir = Path(args.data)
    train_data = v4ds.load_split(data_dir, "train")
    val_data = v4ds.load_split(data_dir, "val")
    meta = train_data["meta"]
    train_data["meta"] = meta                        # `evaluate` 要 action_types
    val_data["meta"] = meta

    device = _device(args.device)
    threads = guard.apply_cpu_limit(args.threads)
    torch.manual_seed(args.seed)
    # 掩码用**固定种子**的生成器：同种子两次跑的掩码序列相同 ⇒ `history` 可逐字段复现
    rng_gen = torch.Generator(device="cpu").manual_seed(args.seed)
    model = M.build(seed=args.seed).to(device)
    ssl_head = MaskedEventHead().to(device)
    # 初始化：`--init <ckpt|net.bin>` 时从上一代权重起步（P3/P4 都是"接着上一代练"）
    init_spec = getattr(args, "init", None)
    if init_spec:
        src = _load_behaviour(init_spec, device)
        model.load_state_dict(src.state_dict(), strict=True)
        print(f"初始化：从 {init_spec} 载入权重")
    weights = dict(M.loss_weights())
    weights["ssl"] = args.ssl_weight
    # `--only-heads`：只训点名的头（其余头的损失权重清零 + 参数冻住）。"修 critic"那一轮用它
    # （`--only-heads value --freeze-trunk`，见 `NOTES.md` §6.5 第十三轮）。
    # ⚠ 点名之后**必须绕过 `STAGE_WEIGHTS`**：阶段 a 的注册权重里 `value` 就是 0（那会让这一轮
    #   静默什么都不学，正是"跑了但没训"的典型翻车）。
    only = [h.strip() for h in str(getattr(args, "only_heads", "") or "").split(",") if h.strip()]
    # `--drop-heads X,Y`：**补集**写法（消融矩阵用）——"除了这些，其余照常训"。
    drop = [h.strip() for h in str(getattr(args, "drop_heads", "") or "").split(",") if h.strip()]
    if drop:
        unknown = [h for h in drop if h not in M.loss_weights()]
        if unknown:
            raise SystemExit(f"--drop-heads 里有未知的头 {unknown}；可选 {sorted(M.loss_weights())}")
        only = [h for h in M.loss_weights() if h not in drop]
        print(f"消融头：丢掉 {drop} ⇒ 只训 {only}")
    if only:
        unknown = [h for h in only if h not in M.loss_weights()]
        if unknown:
            raise SystemExit(f"--only-heads 里有未知的头 {unknown}；可选 {sorted(M.loss_weights())}")
        for k in list(weights):
            if k not in only:
                weights[k] = 0.0
        weights["ssl"] = 0.0
        print(f"只训头：{only}（其余头的损失权重置 0；阶段权重被绕过）")
    freeze_trunk = bool(getattr(args, "freeze_trunk", False))
    if freeze_trunk:
        print("冻结主干：整轮只更新头参数（trunk.requires_grad=False）")
    trunk = [p for m in (model.tile, model.event, model.ctx, model.cand, model.fusion)
             for p in m.parameters()]
    heads = list(model.heads.parameters()) + list(ssl_head.parameters())
    if only:
        for name, p in model.heads.named_parameters():
            if name.split(".")[0] not in only:
                p.requires_grad = False
    opt = torch.optim.Adam([{"params": trunk, "lr": args.lr},
                            {"params": heads, "lr": args.lr * args.head_lr_mult}])
    mon = guard.GpuMonitor() if device == "cuda" else None
    dc = guard.DutyCycle(mon) if mon else None

    n = int(train_data["nlegal"].shape[0])
    steps = max(1, min(args.max_steps or 10 ** 9, n // args.batch))
    total_steps = steps * args.epochs
    # 学生掩码 + RWR 权重（P3 开局；见 `_row_weights`）。⚠ 掩码/权重**只在动作相关的头上生效**。
    # 学生掩码 + RWR 权重（P3 开局；见 `_row_weights`）。⚠ 掩码/权重**只在动作相关的头上生效**。
    # ⚠ `getattr` 取缺省：`rwr_beta` 是后加的可选开关，而 `train()` 也会被自检/脚本直接用
    #   `Namespace(**{...})` 调用（那种调用不该因为少一个可选字段就炸）
    rwr_beta = float(getattr(args, "rwr_beta", 0.0) or 0.0)
    objective = str(getattr(args, "objective", "bc") or "bc")
    # 值头目标 / 优势回报的来源（第六轮）：`rtg` = 逐决策 reward-to-go（λ=1 的 GAE 目标）。
    # ⚠ 两处**必须同源**，所以只有一个开关；老数据集里 `rtg` 是 NaN ⇒ 选了就当场报错（不静默）。
    value_key = "rtg" if str(getattr(args, "value_target", "final") or "final") == "rtg" else "value"
    # `--advantage`（§14 P1）：`auto` = 与 `--value-target` 同源（老行为）；`gae-hand` = 手级 GAE，
    # 值头目标同时换成 TD(λ) 回报（`vtarget`，见 `_hand_advantage`）。
    advantage = str(getattr(args, "advantage", "auto") or "auto")
    if advantage == "gae-hand":
        if value_key == "rtg":
            raise SystemExit("--advantage gae-hand 与 `--value-target rtg` 互斥："
                             "手级 GAE 自带值头目标（TD(λ) 回报），请去掉 `--value-target rtg`")
        value_key = "vtarget"
    elif advantage == "hand":
        # P1b：时间尺度压到一小局 —— 值头目标 = **本小局收支**（`delta`，千点）
        if value_key == "rtg":
            raise SystemExit("--advantage hand 与 `--value-target rtg` 互斥："
                             "小局级 baseline 的值头目标就是本小局收支，请用 `--value-target delta`")
        value_key = "delta"
    ablate_blocks = [b.strip() for b in str(getattr(args, "ablate_blocks", "") or "").split(",")
                     if b.strip()]
    ablate: dict[str, list[tuple[int, int]]] = {}
    if ablate_blocks:
        slices = spec.block_slices()
        for bid in ablate_blocks:
            spec.block(bid)                                  # 未注册的块 id ⇒ 当场报错
            tensor, start, width = slices[bid]
            ablate.setdefault(tensor, []).append((start, width))
        print(f"消融块：{ablate_blocks}（输入张量对应通道整段置 0；权重与标签不动）")
    if value_key == "rtg":
        ok = all(d.get("rtg") is not None
                 and bool(np.isfinite(np.asarray(d["rtg"], dtype=np.float32)).all())
                 for d in (train_data, val_data))
        if not ok:
            raise SystemExit("--value-target rtg 但数据集里没有可用的 `reward_to_go`（rtg 列是 NaN）"
                             "—— 那是**老轨迹**（采集器还没有逐决策回报）。请用新版采集器重采，"
                             "或改用 `--value-target final`。")
        print("值头/优势口径：**逐决策 reward-to-go**（rtg，千点；λ=1 的 GAE 目标）")
    elif value_key == "vtarget":
        print("值头/优势口径：**手级 GAE**（TD(λ) 回报；`--advantage gae-hand`）")
    elif value_key == "delta":
        print("值头/优势口径：**小局级 baseline**（值头目标 = 本小局收支，千点；`--advantage hand`）")
    else:
        print("值头/优势口径：整场结果（value = final_scores − 起点；旧口径）")
    keep_tr, w_tr = _row_weights(train_data, np.arange(n), rwr_beta, "cpu")
    if rwr_beta > 0 and w_tr is None:
        raise SystemExit("--rwr-beta > 0 但数据集里没有 `delta` 列（老数据集）—— "
                         "重新 `python -m mahjong_ml.v4.dataset build …`（它现在会写 `delta`/`is_student`）")
    nv = int(val_data["nlegal"].shape[0])
    keep_va, _ = _row_weights(val_data, np.arange(nv), 0.0, "cpu")
    frac_tr = float(keep_tr.mean()) if keep_tr is not None else 1.0
    frac_va = float(keep_va.mean()) if keep_va is not None else 1.0
    if keep_tr is not None and frac_tr <= 0.0:
        raise SystemExit("学生行占比 0 —— `dataset build --student` 的策略串与轨迹里的 "
                         "`policy` 字段没对上（这是静默失真的源头，宁可报错）")
    if w_tr is not None:
        print(f"RWR：β={args.rwr_beta:g} 千点 → 权重 min={float(w_tr.min()):.3f} "
              f"mean={float(w_tr.mean()):.3f} max={float(w_tr.max()):.3f}")
    # PPO：先重算"采集那一步"的 log π_old（行为策略 = `--behaviour`，温度 = 策略串里的 `#T`）
    logp_tr = None
    ppo_cfg = None
    p_temp = 1.0                     # 策略温度：非 PPO 恒为 1.0（= 不缩放）
    if objective == "ppo":
        beh = getattr(args, "behaviour", None)
        if not beh:
            raise SystemExit("--objective ppo 需要 --behaviour <采集用的 ckpt/net.bin>"
                             "（要重算 log π_old，否则 PPO 的重要性比无从谈起）")
        # ⚠ **别把这个局部变量叫 `spec`**：那会把模块级的 `v4.spec` 在整个函数里遮蔽掉
        #   （Python 的作用域是静态的）⇒ 上面 `--ablate-blocks` 用的 `spec.block_slices()`
        #   会报 `UnboundLocalError: cannot access local variable 'spec'`（2026-09-28 实测踩到）。
        student_str = str(meta.get("student") or "")
        # ⚠ 温度的**权威来源是数据集 meta 里的 `student` 串**（它逐字记录了采集时的策略串）。
        #   `--behaviour-temp` 只在 meta 没写 `#T` 时才允许兜底；**与 meta 冲突就报错** ——
        #   温度错了不会让训练崩，只会让 `logp_old` 是"另一个分布"，静默把重要性比带偏。
        parsed = float(student_str.rsplit("#", 1)[1]) if "#" in student_str else None
        explicit = float(getattr(args, "behaviour_temp", 0.0) or 0.0)
        if explicit > 0 and parsed is not None and abs(explicit - parsed) > 1e-9:
            raise SystemExit(
                f"--behaviour-temp {explicit:g} 与数据集 student 串里的 `#T` {parsed:g} 不一致"
                f"（{student_str}）—— 温度的权威来源是采集时的策略串，别手填；要改就重采")
        temp = explicit if explicit > 0 else (parsed if parsed is not None else 1.0)
        if keep_tr is None:
            raise SystemExit("--objective ppo 需要学生掩码：数据集要用 `--student <策略串>` 构建"
                             "（否则优势会把对手/老师的行算进来）")
        p_temp = float(temp)         # ⚠ `logp_new` 与 `logp_old` 必须同一个 `softmax(logits/T)`
        beh_model = _load_behaviour(beh, device)
        print(f"PPO：行为策略 {beh}（温度 {temp:g}）—— 重算 log π_old（{n} 条）")
        logp_tr = _behaviour_logprobs(beh_model, train_data, device, args.eval_batch, temp)
        del beh_model
        ppo_cfg = (float(getattr(args, "ppo_clip", 0.2) or 0.2),
                   float(getattr(args, "ppo_entropy", 0.01) or 0.0))
        print(f"PPO：clip={ppo_cfg[0]:g} entropy={ppo_cfg[1]:g}；"
              f"优势在学生行上归一化、策略损失只算学生行")
        # ---- 口径闸门（2026-09-28 加；这条闸门就是为了不让 `#0.5` 那个 bug 再发生一次）----
        kl0 = assert_behaviour_consistency(model, train_data, np.arange(min(64, n)),
                                           logp_tr, temp, device)
        print(f"PPO：口径自检 KL(π_old‖π_new)={kl0:+.2e}（≈0 = 权重与温度都对上了）")
    # ---- 手级 GAE（§14 P1）：`--advantage gae-hand` 的预计算（行为策略的 V ⇒ GAE ⇒ 冻结）----
    adv_tr = None
    val_extra: dict[str, np.ndarray] | None = None
    if advantage in ("gae-hand", "hand"):
        mode = "gae" if advantage == "gae-hand" else "hand"
        beh = getattr(args, "behaviour", None)
        if not beh:
            raise SystemExit(f"--advantage {advantage} 需要 --behaviour <采集用的 ckpt/net.bin>："
                             "V_old 必须来自**行为策略**（与 logp_old 同一份权重）")
        beh_model = _load_behaviour(beh, device)
        t_v = time.perf_counter()
        print(f"小局级优势（{mode}）：用行为策略 {beh} 重算 V_old（train {n} / val {nv} 条）…")
        v_old = _behaviour_values(beh_model, train_data, device, args.eval_batch)
        v_old_va = _behaviour_values(beh_model, val_data, device, args.eval_batch)
        del beh_model
        res = _hand_advantage(train_data, v_old, gamma=float(getattr(args, "gae_gamma", 1.0) or 1.0),
                              lam=float(getattr(args, "gae_lambda", 0.9) or 0.0),
                              rank_weight=float(getattr(args, "rank_weight", 0.0) or 0.0),
                              is_student=np.asarray(train_data["is_student"]) if keep_tr is not None
                              else None, mode=mode,
                              baseline_fit=str(getattr(args, "baseline_fit", "scale") or "scale"))
        adv_tr = res["adv"]
        vtar_tr = res["vtarget"]
        res_va = _hand_advantage(val_data, v_old_va,
                                 gamma=float(getattr(args, "gae_gamma", 1.0) or 1.0),
                                 lam=float(getattr(args, "gae_lambda", 0.9) or 0.0),
                                 rank_weight=float(getattr(args, "rank_weight", 0.0) or 0.0),
                                 is_student=None, mode=mode,
                                 baseline_fit=str(getattr(args, "baseline_fit", "scale") or "scale"))
        val_extra = {"vtarget": res_va["vtarget"]}
        s = res["stats"]
        print(f"  小局 {int(s['hands'])} 个 / 模式 {s['mode']} / γ={s['gamma']:g} λ={s['lam']:g} "
              f"rank_weight={s['rank_weight']:g} / 基线拟合 {s['baseline_fit']}")
        print(f"  小局奖励：mean {s['reward_mean']:+.3f} std {s['reward_std']:.3f} 千点；"
              f"V_old：mean {s['v_old_mean']:+.3f} std {s['v_old_std']:.3f}")
        if s["baseline_fit"] == "scale":
            print(f"  基线重标定：α={s['baseline_alpha']:+.3f} β={s['baseline_beta']:.4f} ⇒ "
                  f"std(V_used) {s['v_used_std']:.3f}（原 {s['v_old_std']:.3f}）"
                  f"—— 基线是状态函数，线性重标定**不改梯度期望、只降方差**")
        if "std_ratio_raw" in s:
            unfit = s.get("std_ratio_unfit")
            extra = (f"（**未重标定**时 {unfit:.3f}× ⇒ 量纲接错的代价）"
                     if unfit is not None and np.isfinite(unfit)
                     and abs(unfit - s["std_ratio_raw"]) > 1e-6 else "")
            print(f"  优势体检（**归一化之前**）：std(A_raw) {s['adv_std_raw']:.3f} vs "
                  f"std({s['base_name']}) {s['base_std']:.3f} ⇒ **{s['std_ratio_raw']:.3f}×**"
                  f"（<1 = 方差被削减；这就是 critic 有没有用的直接度量）{extra}")
            print(f"  归一化后（学生行 z-score）std = {s['adv_std_norm_student']:.3f}"
                  f"（⚠ 别拿它比参考量：归一化本身就把尺度钉成 1）")
        print(f"  （用时 {time.perf_counter() - t_v:.1f}s；优势已冻结、不再随 critic 变动）")
    print(f"数据：训练 {n} 条（{len(meta['train_files'])} 场）/ 验证 "
          f"{nv} 条；lmax={meta['lmax']}；"
          f"标签侧 {'有' if meta['has_aux'] else '无'}；"
          f"学生行占比 训练 {frac_tr:.1%} / 验证 {frac_va:.1%}"
          f"{'（`--student` 遮罩生效）' if keep_tr is not None else '（无掩码：全部行都算学生）'}")
    print(f"模型：{model.param_count():,} 参数（+SSL 头 {sum(p.numel() for p in ssl_head.parameters()):,}）；"
          f"device={device}；threads={threads}；seed={args.seed}")
    print(f"分阶段：a 策略+牌效（{int(args.stage_a * 100)}%）→ b 冻主干只训头"
          f"（{int(args.stage_b * 100)}%）→ c 联合微调（其余）；每 epoch {steps} 步 × batch {args.batch}")

    base_lrs = [pg["lr"] for pg in opt.param_groups]
    history = []
    stage_steps = {"a": 0, "b": 0, "c": 0}
    t0 = time.perf_counter()
    gstep = 0
    for epoch in range(1, args.epochs + 1):
        rng = np.random.default_rng(args.seed + epoch)
        perm = rng.permutation(n)
        model.train()
        ssl_head.train()
        run, seen = 0.0, 0
        run_parts: dict[str, float] = {}
        cur_stage = None
        for step in range(steps):
            stage = _stage_of(gstep, total_steps, args.stage_a, args.stage_b)
            stage_steps[stage] += 1
            if stage != cur_stage:
                cur_stage = stage
                # b 段冻结主干（头是新的，先在固定表示上收敛）；a/c 段解冻
                # ⚠ `--freeze-trunk` 时**全程**冻结（阶段 b 的语义被推广到整轮）
                for p in trunk:
                    p.requires_grad = (not freeze_trunk) and stage != "b"
                print(f"  -- 进入阶段 {stage}（step {gstep}/{total_steps}）")
            # c 段余弦降 lr（a/b 段保持常数）；`--stage-c-lr-mult` 再整体缩一档
            if stage == "c":
                c_start = int(total_steps * (args.stage_a + args.stage_b))
                prog = max(0.0, min(1.0, (gstep - c_start) / max(1, total_steps - c_start)))
                for pg, base in zip(opt.param_groups, base_lrs):
                    pg["lr"] = base * args.stage_c_lr_mult * (
                        0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * prog)))
            idx = np.sort(perm[step * args.batch:(step + 1) * args.batch])
            if idx.size == 0:
                break
            b = _batch(train_data, idx, device, ablate=ablate or None)
            if adv_tr is not None:
                b["vtarget"] = torch.from_numpy(vtar_tr[idx]).to(device)
            ssl = None
            if stage in ("a", "c") and args.mask_frac > 0:
                evt_masked, mask_rows, target = mask_events(b["evt"], args.mask_frac, rng_gen)
                b["evt"] = evt_masked
                ssl = (ssl_head, mask_rows, target)
            out = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"])
            kb = None if keep_tr is None else keep_tr[idx].to(device)
            wb = None if w_tr is None else w_tr[idx].to(device)
            ppo = None
            if logp_tr is not None:
                ppo = (logp_tr[idx].to(device), ppo_cfg[0], ppo_cfg[1])
            adv_b = None if adv_tr is None else torch.from_numpy(adv_tr[idx]).to(device)
            loss, parts = compute_loss(out, b, weights if only else (STAGE_WEIGHTS.get(stage) or weights),
                                       ssl, row_keep=kb, row_w=wb, ppo=ppo, value_key=value_key,
                                       policy_temp=p_temp, adv_fixed=adv_b)
            # ⚠ **发散就停**（2026-09-28 加）：NaN 的 loss 会让整网变成 NaN 参数，而训练"照跑完"
            #   并落盘一份废 checkpoint（`#0.5` 那次就是）。宁可当场报错，也不要产出一个
            #   看着跑完、实际是首合法基线的模型。
            if not math.isfinite(float(loss)):
                raise SystemExit(f"训练发散：第 {gstep} 步 loss={float(loss)}（epoch {epoch}）—— "
                                 f"诊断看 train_parts 的 kl / clip_frac / logp_gap；不落盘废 checkpoint")
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            if dc:
                dc.tick()
            run += float(loss.detach()) * idx.size
            seen += idx.size
            for k, v in parts.items():
                run_parts[k] = run_parts.get(k, 0.0) + v * idx.size
            gstep += 1
        ev = evaluate(model, val_data, device, batch=args.eval_batch, ssl_head=ssl_head,
                      mask_frac=args.mask_frac, value_key=value_key, ablate=ablate or None,
                      extra=val_extra)
        ev_stu = (evaluate(model, val_data, device, batch=args.eval_batch, row_keep=keep_va,
                           value_key=value_key, ablate=ablate or None, extra=val_extra)
                  if keep_va is not None else None)
        row = {"epoch": epoch, "stage_at_end": cur_stage, "train_loss": run / max(1, seen),
               "train_parts": {k: v / max(1, seen) for k, v in run_parts.items()},
               "train_student_frac": frac_tr, "val_student_frac": frac_va,
               "val_top1_student": (ev_stu["top1"] if ev_stu else None),
               "val_top1_student_n": (ev_stu["n"] if ev_stu else None),
               **{f"val_{k}": v for k, v in ev.items() if k != "by_type"},
               "val_by_type": ev["by_type"]}
        history.append(row)
        print(f"epoch {epoch:>3} [{cur_stage}]: train {row['train_loss']:.4f} | val top1(教师一致) "
              f"{ev['top1']:.3f} | 首合法基线 {ev['first_legal_acc']:.3f} | "
              f"policy {ev['loss_policy']:.3f} value {ev.get('loss_value', float('nan')):.3f} "
              f"place {ev.get('loss_placement', float('nan')):.3f} "
              f"bh {ev.get('loss_belief_hand', float('nan')):.3f} "
              f"bt {ev.get('loss_belief_tenpai', float('nan')):.3f} "
              f"danger {ev.get('loss_danger', float('nan')):.3f} "
              f"effect {ev.get('loss_effect', float('nan')):.4f} "
              f"ssl_acc {ev.get('ssl_acc', float('nan')):.3f}"
              + (f" | **学生行 top1 {ev_stu['top1']:.3f}**(n={ev_stu['n']})" if ev_stu else "")
              + (f" | 学生行 policy {ev_stu['loss_policy']:.3f}" if ev_stu else "")
              + (f" | KL {row['train_parts'].get('kl', float('nan')):.4f}"
                 f" clip {row['train_parts'].get('clip_frac', float('nan')):.3f}"
                 f" |A| {row['train_parts'].get('adv_abs', float('nan')):.2f}"
                 f" gap {row['train_parts'].get('logp_gap', float('nan')):.2f}"
                 if "kl" in row["train_parts"] else ""))
        print("        按类型：" + "  ".join(
            f"{t}={d['top1']:.2f}(n={d['n']})" for t, d in sorted(ev["by_type"].items())))
    if mon:
        mon.close()
    wall = time.perf_counter() - t0

    out = paths.allocate("ckpt", args.label, need_bytes=16 * 1024**2)
    save_checkpoint(out / "model.pt", model, ssl_head, meta, weights,
                    {"args": vars(args), "stage_steps": stage_steps})
    result = {
        "label": args.label, "data": str(args.data), "dataset_meta": meta,
        "params": model.param_count(), "device": device, "torch_threads": threads,
        "args": {k: v for k, v in vars(args).items()},
        "epochs": args.epochs, "batch": args.batch, "steps_per_epoch": steps,
        "stage_steps": stage_steps, "wall_seconds": wall, "history": history,
        "final_val_top1": history[-1]["val_top1"] if history else None,
        "stages": {"a": "policy+effect（主干先会打牌）", "b": "冻主干只训头", "c": "联合微调（余弦降 lr）",
                   "ssl": f"掩码事件重建 frac={args.mask_frac} weight={args.ssl_weight}"},
    }
    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(f"\ncheckpoint：{out / 'model.pt'}；指标：{out / 'metrics.json'}（{wall:.1f}s）")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="v4 教师预训练（P1/P2 冷启动）")
    ap.add_argument("--data", required=True, help="v4 数据集目录（`python -m mahjong_ml.v4.dataset`）")
    ap.add_argument("--label", default="v4-bc-smoke", help="checkpoint 子目录名（S 盘 ckpt/ 下）")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--eval-batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--max-steps", type=int, default=None, help="每 epoch 最多多少步（冒烟用）")
    ap.add_argument("--seed", type=int, default=20260927)
    ap.add_argument("--threads", type=int, default=guard.TRAIN_THREADS)
    ap.add_argument("--device", default="auto")
    # 分阶段（这一轮的核心改动：第一轮辅助头不收敛 = 多任务权重冲突）
    ap.add_argument("--stage-a", type=float, default=0.25, help="阶段 a（策略+牌效）占总步数比例")
    ap.add_argument("--stage-b", type=float, default=0.35, help="阶段 b（冻主干只训头）占比")
    ap.add_argument("--head-lr-mult", type=float, default=3.0, help="头的学习率倍数（主干 = --lr）")
    # 只训若干头 / 全程冻主干（"修 critic"那一轮：`--only-heads value --freeze-trunk`）
    ap.add_argument("--freeze-trunk", action="store_true",
                    help="整轮冻结主干（等价于把阶段 b 的语义推广到全程；只更新头参数）")
    ap.add_argument("--only-heads", default=None, metavar="H1,H2",
                    help="只训点名的头（其余头损失权重置 0 并冻结；绕过阶段权重）")
    # ⚠ 2026-09-27 实测：把 `cand` 的派生段真正喂进来之后（此前是整块 0），a/b 段的教师一致率
    #   从 0.619 涨到 **0.837**，但 c 段（联合微调、主干 lr = `--lr`）**当场把策略头练塌**
    #   （top1 掉回首合法基线 0.165，策略 CE 恒定 ⇒ 融合输出 ReLU 全死、logits 变成常数）。
    #   所以 c 段的主干 lr 必须再降一档：这个倍率乘在 a/c 的基准 lr 上（缺省 1.0 = 老行为）。
    ap.add_argument("--stage-c-lr-mult", type=float, default=1.0,
                    help="阶段 c（联合微调）学习率相对 --lr 的倍率（塌了就调小，例如 0.1）")
    # P3 开局：自对弈数据的两个开关（学生掩码 + 回报加权）
    ap.add_argument("--rwr-beta", type=float, default=0.0,
                    help="RWR 温度（**千点**，例如 8）：权重 = exp(clip(本小局收支/β, -2, 2))；"
                         "0 = 关闭（纯模仿）。⚠ 只作用于 policy/effect/danger")
    # P3 加强版：PPO（值头当 critic）+ 从上一代权重起步
    ap.add_argument("--objective", choices=["bc", "rwr", "ppo"], default="bc",
                    help="策略损失口径：bc = 纯模仿（缺省）/ rwr = 回报加权 / ppo = 截断替代项")
    ap.add_argument("--behaviour", default=None,
                    help="PPO 的行为策略（采集用的 ckpt 或 net.bin）—— 用来重算 log π_old")
    ap.add_argument("--behaviour-temp", type=float, default=0.0,
                    help="行为策略的采样温度（缺省从数据集 meta 的 `student` 串里解析 `#T`，再缺省 1.0）")
    ap.add_argument("--ppo-clip", type=float, default=0.2, help="PPO 截断 ε")
    ap.add_argument("--ppo-entropy", type=float, default=0.01, help="熵奖励系数（学生行上）")
    ap.add_argument("--value-target", choices=["final", "rtg", "delta"], default="final",
                    help="值头目标 / 优势回报的来源：final = 整场结果（旧口径）/ "
                         "rtg = **逐决策 reward-to-go**（λ=1 的 GAE 目标；需要新版采集器的轨迹）/ "
                         "delta = **本小局收支**（千点；`--advantage hand` 用它）")
    # §14 P1/P1b：小局级信用分配（时间步 = 一小局）
    ap.add_argument("--advantage", choices=["auto", "gae-hand", "hand"], default="auto",
                    help="优势怎么算：auto = 与 --value-target 同源（老行为）/ gae-hand = 手级 **GAE**"
                         "（TD(λ)+bootstrap，值头目标自动换成 TD(λ) 回报）/ hand = **小局级 baseline**"
                         "（A = 本小局收支 − V(s)，不跨小局 bootstrap；值头目标 = 本小局收支）")
    ap.add_argument("--gae-lambda", type=float, default=0.9, help="手级 GAE 的 λ（1.0 = 退化成 rtg）")
    ap.add_argument("--gae-gamma", type=float, default=1.0, help="手级 GAE 的 γ（半庄只有 8~12 小局）")
    ap.add_argument("--rank-weight", type=float, default=0.0,
                    help="整场**顺位点**项的权重（与评测口径 `rank_points` 同量纲；0 = 只用小局收支）")
    ap.add_argument("--baseline-fit", choices=["scale", "mean", "none"], default="scale",
                    help="把行为策略的 `V_old` **线性重标定**到奖励尺度（最小二乘 `α+β·V`）："
                         "scale = 拟合截距+斜率（缺省，同族里方差最小）/ mean = 只减均值 / "
                         "none = 原样相减（P1b 那版，量纲不一致时会把优势方差放大 2.7×）")
    # 消融矩阵（§8.3 / §14 P0）
    ap.add_argument("--drop-heads", default=None, metavar="H1,H2",
                    help="**丢掉**点名的头（补集写法：其余头照常训）—— 消融矩阵用")
    ap.add_argument("--ablate-blocks", default=None, metavar="B1,B2",
                    help="把点名的输入块整段置 0（`v4 spec` 里登记的块 id）—— 消融矩阵用")
    ap.add_argument("--init", default=None,
                    help="从这份权重起步（ckpt 或 net.bin）—— 缺省随机初始化（纯模仿那几轮的口径）")
    # P1 自监督：掩码事件重建
    ap.add_argument("--mask-frac", type=float, default=0.15, help="掩码多少比例的真实事件 token（0 = 关）")
    ap.add_argument("--ssl-weight", type=float, default=0.2, help="掩码重建损失权重")
    args = ap.parse_args(argv)
    if args.device == "auto" and not torch.cuda.is_available():
        print("（提示）没有 CUDA，用 CPU 跑；大轮次请用 GPU（设计 §0.1.3 的显存/占用纪律）")
    train(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
