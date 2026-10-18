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
| `policy`（W4 的 `--il-weight`） | **引擎的逐候选牌效**（`cand.derived` 现算的"最优候选"，见 `dataset.engine_best_index`） | 稠密、无采样噪声的模仿项（缺省 0 = 关闭） |

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
import hashlib
import json
import math
import os
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from .. import features as _f3
from .. import guard, paths
from . import adv as v4adv
from . import dataset as v4ds
from . import model as M
from . import refcache as _refcache
from . import spec
#: **风格奖励塑形**：这里只借它的**列名常量**（`style_bonus`）——
#: ⛔ 训练端**不**自己算风格量（那是 `dataset build` 的活），也不 import 判据侧
#: （`tools/style-vector.py` 是**独立实现**，本仓的分工：判据独立实现、两边各自算再由对账钉住）。
from . import style_reward as _style_reward

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

    ⚠ 它**不进推理**（`contract_heads()` 里没有它）：P1 用它逼事件塔把"上下文里的缺格"
    补出来，训完就丢；checkpoint 里单独存一份（`ssl_head`）以便复现。
    """

    def __init__(self, d_model: int | None = None) -> None:
        super().__init__()
        # ⚠ 宽度按**进程级 arch** 取（A− 的 `e_tokens` 是 128 宽；v4 是 192）——
        #   写死 `M.D_MODEL` 会在 a- 上当场形状不匹配（实测 `30720x128 and 192x8`）。
        self.proj = nn.Linear(int(M.arch_dims()["d_model"] if d_model is None else d_model),
                              len(spec.EVT_TYPES))

    def forward(self, e_tokens: torch.Tensor) -> torch.Tensor:
        return self.proj(e_tokens)


class OwnWinHead(nn.Module):
    """★ MV-1 第一刀：辅助子目标头 `own_win`（`Linear(D_MODEL → 1)`，读 `forward()` 的 `state`）。

    y = **既有的二值 `aux_win_flag`**（自家本小局和了，0/1；列已在紧凑集里 ⇒ 零扩列、零重采）。
    与 `MaskedEventHead`（`ssl_head`）**同族的外部头**：⛔ 不进 `HEAD_SPECS` / `TRAIN_HEADS` /
    `ONLINE_HEADS` / 三端头表 / `net.bin` 张量表 / 推理路径；⛔ 不进 `loss_weights()`。

    ⚠ **只当 `--aux-own-win-weight > 0` 时才建**（缺省 0.0 = 关）⇒ 关着时对象不存在：
    无参数、无梯度、无 ckpt 键、`compute_loss(aux_own=None)` 一行都不走
    （与 `ref=None`/`il=None`/`rho=None`/`ssl=None` 同一处口径）。
    """

    def __init__(self, d_model: int | None = None) -> None:
        super().__init__()
        # ⚠ 同 `MaskedEventHead`：宽度按 arch 取（`state` 在 A− 上是 128 维）。
        self.proj = nn.Linear(int(M.arch_dims()["d_model"] if d_model is None else d_model), 1)

    def forward(self, state: torch.Tensor) -> torch.Tensor:
        return self.proj(state).squeeze(-1)


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
           ablate: dict[str, list[tuple[int, int]]] | None = None,
           aux_own_win: bool = False) -> dict[str, torch.Tensor]:
    """按行号取一个 batch（memmap → torch）。`idx` **升序**（与 v3 的 `bc._batch` 同规矩）。

    ⚠ 候选宽度取**这一份切分自己的** `cand.shape[1]`，不是 `meta["lmax"]`：
    train/val 两份的 lmax 可能不同（`build` 里按切分各自定宽），拿全局 lmax 去做掩码
    会与 logits 的长度对不上（实测报 `size of tensor a (27) must match b (15)`）。

    @param ablate **块消融**：`{张量名: [(起点, 宽度), …]}` ⇒ 那些通道整段置 0
        （`docs/TRAINING-V4.md` §8.3 的消融矩阵；只改输入，不动权重，也不碰标签）
    @param aux_own_win ★ MV-1：**仅当开关开**时多喂一个 `own_win` 键（= `aux_win_flag` 的
        float32 视图）。缺省 `False` ⇒ `b` 的**键集合与加这个功能之前逐键相同**
        （判据：`--aux-own-win-weight 0` 与「不带该开关」两次跑的日志/指纹/`net.bin` 全同）。
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
    # `h0`（W1b）：**窗口之前**的 GRU carry ⇒ 前向的 `h`（`EventTower.forward(evt, h)` 的初值）。
    # ⚠ **有才加这个键**（而不是无条件加一个 `None`）：前向统一写成 `h=b.get("h")`，
    #   于是"老紧凑集（没有这一列）"与"新紧凑集"走的是同一个调用点 —— 前者 = 窗口冷启动。
    #   而"缺列算不算允许"不在这里判（这里只负责搬运）：那是 `train()` 的**硬闸门**
    #   + `--no-carry` 的事，避免同一个口径散在两个地方。
    if data.get("h0") is not None:
        b["h"] = torch.from_numpy(np.asarray(data["h0"][idx], dtype=np.float32)).to(device)
    for src, dst in (("aux_opp_hand", "opp_hand"), ("aux_opp_tenpai", "opp_tenpai"),
                     ("aux_opp_dealin", "opp_dealin"), ("aux_own_tenpai", "own_tenpai"),
                     ("aux_own_shanten_after", "own_shanten")):
        if data.get(src) is not None:
            b[dst] = torch.from_numpy(np.asarray(data[src][idx], dtype=np.float32)).to(device)
    # ★ MV-1 第一刀：辅助头 `own_win` 的标签（**只有开关开时才进 `b`**）。二值 `uint8` 标量列，
    #   转 float32 后直接喂 `binary_cross_entropy_with_logits`（它要 float 目标）。
    if aux_own_win and data.get("aux_win_flag") is not None:
        b["own_win"] = torch.from_numpy(
            np.asarray(data["aux_win_flag"][idx], dtype=np.float32)).to(device)
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
    center = torch.linspace(-M.value_range(), M.value_range(), M.value_bins()).to(device)
    n = int(data["nlegal"].shape[0])
    out = np.zeros(n, dtype=np.float64)
    model.eval()
    with torch.no_grad():
        for i0 in range(0, n, batch):
            idx = np.arange(i0, min(i0 + batch, n))
            b = _batch(data, idx, device)
            v = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"],
                      h=b.get("h"), n_legal=b["nlegal"])["value"]
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

    ⚠⚠ **`r_row` 必须是"要减掉基线的那份奖励"本身**（含顺位点项！）。第二十四轮踩过：
    基线拿 `delta` 拟合、优势却减 `delta + θ·顺位点` ⇒ 拟合的不是要减的量，最小二乘最优性不成立，
    `--rank-weight 0.2` 实测 std 比 1.1604× > 1（守卫当场报错，没让它悄悄训完）。
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


def _clip_grad(opt, clip: float) -> float:
    """按**总范数**裁剪梯度，返回**裁剪前**的范数（`clip<=0` = 关闭，返回 nan）。

    为什么必须有（v3 有、v4 一直没有）：PPO 的替代项在 `ρ` 很大时对 logits 的梯度是无界的，
     一个坏 batch（例如一整批都是 teacher 的动作）能把 logits 幅度推上去，下一批就是 inf/NaN。
    v3 用 0.5；这里同口径。**返回裁剪前的范数**是为了让日志能看出"到底有没有被砍" ——
    只看 loss 是看不出来的（砍与不砍的 loss 逐位相同）。
    """
    if not clip or clip <= 0:
        return float("nan")
    params = [p for g in opt.param_groups for p in g["params"] if p.grad is not None]
    if not params:
        return float("nan")
    return float(torch.nn.utils.clip_grad_norm_(params, float(clip)))


def effective_kl_min_steps(max_steps: int, kl_min_steps: int) -> int:
    """把 `--kl-min-steps` 钳到**一轮真的够得着**的位置（纯函数，自检直接喂数）。

    ⚠ 第二十五轮实测的坑：`v4-mr01` 里 `--max-steps 100` 配 `--kl-min-steps 100`
    ⇒ 早停只在 `step >= 100` 时判，而那一轮**最多只有 100 步** ⇒ **KL 早停永远不会触发**
    （三代 `stop_reason` 全是 `None`，KL 从 0.079 掉到 0.0085 一路没人管）。
    "每轮短"的配方必须让 `min_steps` 明显小于轮长：这里按 **1/5** 钳（至少 1 步）。
    """
    if max_steps and max_steps > 0:
        return max(1, min(int(kl_min_steps), int(max_steps) // 5))
    return int(kl_min_steps)


def kl_stop_hit(kl: float, *, threshold: float, step: int, min_steps: int) -> bool:
    """**KL 早停**的判据（纯函数，自检直接喂数）。

    为什么要有它：离线单轮 PPO 只有 1~2 个 epoch，一旦 KL(π_old‖π_new) 越过阈值，再往下跑就是
    "离行为策略太远、替代项的估计不再可信"（v3 的阈值 0.03）。`min_steps` 是刻意的：
    第一批的 KL 噪声大（几十条的 Monte-Carlo），不该拿它判生死。
    """
    if not threshold or threshold <= 0 or step < max(0, int(min_steps)):
        return False
    return bool(math.isfinite(kl) and kl > threshold)


def _hand_advantage(data: dict, v_old: np.ndarray, *, gamma: float, lam: float,
                    rank_weight: float, is_student: np.ndarray | None,
                    mode: str = "gae", baseline_fit: str = "scale",
                    style_col: str = "style_bonus") -> dict:
    """小局级信用分配 → 逐决策的 `(adv, vtarget)` + 体检数字。

    两种模式（`docs/TRAINING-V4.md` §14.6）：

    · **`gae`**（P1 第一版）：奖励 = 本小局收支（+ 终局顺位点），**TD(λ) + bootstrap**（跨小局）。
      实测：λ-回报的 EV 只到 0.094（`std(A_raw)` 反而 1.24×）—— 因为 λ=0.9 的 λ-回报
      仍然主要是"未来若干小局的和"，而那一族的可解释上限 ≈0.1（引擎真值线性参照）。
    · **`hand`**（P1b，证据指向的那一步）：**把时间尺度压到一小局** ——
      `A = 本小局收支 − V(s)`（**不跨小局 bootstrap**），值头目标就是**本小局收支**本身。
      依据：本小局收支的可解释方差实测 **0.632**（引擎真值 10 列线性），而多手后缀和只有 0.090。
      终局的顺位点项**只加在优势上**（不进取值头目标）：顺位点四家零和 ⇒ 期望 0 就是它的基线。

    ## ★ 风格奖励塑形（`style_bonus` 列）：**只叠一次**，且只进优势、不进值头目标

    `adv = (delta + style_bonus + 顺位点) − V(s)`，而 `vtarget` 仍然是**纯 `delta`**。三条理由：

    ① **塑形是奖励，不是结局**：值头要学的是"这个状态值多少分"（`delta` 的期望），
       把塑形灌进值头会让它去预测一个**被人为加上去的量**，critic 的读数（EV/覆盖率）当场失真；
       这与"顺位点项只加在优势上"是同一条口径（见上面那句话）。
    ② **基线的拟合目标必须等于被减的那个量**（第二十四轮那个 bug 的同一条）：
       所以 `fit_target` 把 `style_bonus` 一起算进去 —— 否则最小二乘的最优性不成立，
       而守卫（`std_ratio_raw ≤ min(...)`）**会在日志里先炸**（不是悄悄训完）。
    ③ ⛔ **不许在别处再叠一次**：数据集里那一列是**唯一**来源；把奖励折进 `delta` 列会同时
       污染 RWR 权重与 audit 的口径。

    @param style_col 风格奖励那一列的列名（`dataset.load_split` 没这一列时给 `None` ⇒ 一行都不走，
        与加这个功能之前**逐位相同**）。⚠ 名字只在这里出现一次（值引用 `style_reward.COLUMN`）。
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
    # ★ 奖励的**逐行分子**：小局收支 + 顺位点项（**只在链末小局**上，与 `hand_reward` 同一口径）。
    # ⚠⚠ 第二十四轮修的真 bug：基线曾经是拿 `d_row`（只有收支）拟合的，而优势减的是
    #   `d_row + 顺位点` ⇒ **最小二乘的最优性不成立**（拟合的不是要减的那个量），实测
    #   `--rank-weight 0.2` 时 std 比 1.1604× > 1（守卫当场报错）。现在按**要预测的那个奖励**拟合：
    #   最小二乘解 ≤ 常数基线 的结论才重新成立（自检把两条都钉住）。
    rank_row = (rank_weight * np.where(term_hand[hand_of_row], rp_row, 0.0)
                if (rank_weight and rp_row is not None) else None)
    # ★★ 风格奖励塑形（`--style-bonus`）：**唯一**叠加点。
    #   `style_row` = 数据集的 `style_bonus` 列（**千点**，与 `d_row` 同量纲）。
    #   ⚠ 没这一列（没开塑形）⇒ `style_row is None` ⇒ 下面每一处都退化成加 0
    #   ⇒ 与加这个功能之前**逐位相同**（判据在 `python/selfcheck.py`：权重哈希）。
    style_row = None
    if style_col and data.get(style_col) is not None:
        style_row = np.asarray(data[style_col][:n], dtype=np.float64) / 1000.0
    base_reward = d_row if style_row is None else d_row + style_row
    fit_target = base_reward if rank_row is None else base_reward + rank_row
    # ⚠ 归一化**只做一次**（学生行上的全局 mean/std）：放进每个 batch 里做 z-score 的话
    #   优势的绝对尺度会随 batch 变，PPO 的 clip ε 就失去语义（§14 P2-②）。
    alpha, beta = _fit_baseline(v_row, fit_target, keep_m, baseline_fit)
    v_used_h = alpha + beta * v_h
    v_used_row = alpha + beta * v_row
    if mode == "hand":
        adv = fit_target - v_used_row                     # 小局级 baseline，无跨小局 bootstrap
        vtarg = d_row.copy()                             # 值头目标 = 本小局收支（千点；**不含**塑形/顺位点）
        r_h = base_reward[reward_rows]
        stat_extra = {"mode": "hand", "bootstrap": 0.0}
    else:
        # ⚠ `gae` 模式的奖励是 `v4adv.hand_reward` 现算的（不是这里的 `base_reward`）——
        #   所以塑形在 gae 路径上**不生效**。这是刻意的（当前只有 `hand` 口径在用；
        #   要开 gae + 塑形就得先把"链上奖励"那一份也改掉，那件事没做就不许假装做了）。
        if style_row is not None:
            raise SystemExit(
                "`--style-bonus` 只与 `--advantage hand` 口径配套（gae 的链上奖励由 "
                "`v4adv.hand_reward` 现算，塑形项没有接进去）。要么用 `--advantage hand`，"
                "要么去掉 `--style-bonus` —— ⛔ 不静默忽略这笔奖励")
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
    # ★ 风格奖励塑形的体检数字（**只有开了那一列才有**；不开时这几个键根本不存在 ⇒
    #   日志/自检的"关掉时逐位相同"不会被一行多余的输出破坏）。
    #   ⚠ 口径：`style_row` 是**千点**（与 `d_row` 同量纲）⇒ 下面统一按**点**记录并打印。
    #   ⚠⚠ 这个"尺度读数"是本实验里最该看的一个数（见 `docs`/报告）：`w=2` 时它只有
    #   **0.04%**（实测 log 里那行 `std 1.9 点 vs 结果奖励 std 5299.2 点`）—— 也就是说
    #   这一轴在梯度里**几乎不存在**。要它真的推得动，`w` 得按 `结果奖励 std / 塑形 std`
    #   这个倍数往上抬（本数据集实测 ≈ 5000/1.9 ≈ 2600 ⇒ `w≈2000` 才是 1:1 的方差）。
    if style_row is not None:
        _rw = reward_rows if reward_rows.size else np.arange(style_row.size)
        _s_h = style_row[_rw] * 1000.0               # 千点 → 点
        _base_std_pts = float(np.std(base_reward[_rw])) * 1000.0
        # ★ **逐行**读数（2026-10-10 加，`--style-bonus-mode decision` 必需）：
        #   `_rw` 是**每小局取第一行**（`reward_rows`）—— 那对 `hand` 模式（列是小局级常数）是对的；
        #   但 `decision` 模式只有**动作行**非 0，按"每小局第一行"采样会几乎全采到 0
        #   ⇒ 那个尺度读数会失真（看起来"塑形没了"，其实只是采样点选错了）。
        #   所以另给一份**真正进梯度的那些行**（学生行）上的读数 —— 两种模式同一个口径、可比。
        _rr = keep if keep is not None else np.ones(n, dtype=bool)
        _s_rows = style_row[_rr] * 1000.0
        _base_rows_pts = (float(np.std(base_reward[_rr])) * 1000.0) if bool(_rr.any()) else 0.0
        stats.update({
            "style_reward": {
                "rows": int(n),
                "nonzero_rows": int((style_row != 0.0).sum()),
                "hand_mean": float(_s_h.mean()), "hand_std": float(_s_h.std()),
                # ★ **这一轴相对"结果奖励"有多大** —— 也就是"它会不会支配梯度"的直接读数。
                #   ⚠ 别拿它跟 `std(delta)` 比：基线（`α+β·V`）会吸收掉塑形里可被状态预测的那一部分，
                #   剩下的才是真正进梯度的；这个比值只是**尺度**，用来定 `w` 的量级。
                "hand_std_vs_reward": (float(_s_h.std() / _base_std_pts) if _base_std_pts > 0
                                       else float("nan")),
                "reward_base_std": _base_std_pts,     # 点
                # 建议的 `w`（= 把这一轴抬到与结果奖励 1:1 所需的比例；见 `hand_std_vs_reward`）
                "w_for_parity": (float(_base_std_pts / _s_h.std()) if _s_h.std() > 0
                                 else float("inf")),
                # ★ 逐行（学生行）读数：`row_std` = 那**一列**在进梯度的行上的标准差（点），
                #   `row_std_vs_reward` = 它与同一批行上"被减的奖励"std 的比 ⇒ `hand` 与
                #   `decision` 两种模式的**剂量**可以当场对比（`w` 在这两种模式里是同一个量）。
                "row_population": ("student" if keep is not None else "all"),
                "row_n": int(_rr.sum()),
                "row_mean": (float(_s_rows.mean()) if _rr.any() else float("nan")),
                "row_std": (float(_s_rows.std()) if _rr.any() else float("nan")),
                "row_base_std": _base_rows_pts,
                "row_std_vs_reward": (float(_s_rows.std() / _base_rows_pts)
                                      if _base_rows_pts > 0 else float("nan")),
            }})
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
    _fit_s = fit_target if keep is None else fit_target[keep]
    _d_s = d_row if keep is None else d_row[keep]
    _v_s = v_row if keep is None else v_row[keep]
    # ⚠ 参考量：**没开塑形时**用 `delta`（= 老口径，日志逐字不变）；**开了塑形时**用
    #   `base_reward`（= delta + 塑形）—— 因为此时"被拟合/被减"的量本来就是它，
    #   拿 `delta` 当参照会算出一个"看着像方差爆炸"的数（塑形本来就带自己的方差）。
    _ref_name = "delta" if style_row is None else "reward(delta+style)"
    _ref_s = base_reward if style_row is not None else d_row
    _ref = _ref_s if keep is None else _ref_s[keep]
    stats.update({
        "base_name": base_name, "base_std": float(np.std(base)),
        # 「基线有没有用」的**直接度量**：归一化前的 std(A) ÷ 被减的那个奖励的 std（<1 = 削减）
        "ref_name": _ref_name, "ref_std": float(np.std(_ref)),
        "adv_std_raw": float(ref.std()),
        "std_ratio_raw": float(ref.std() / np.std(_ref)) if np.std(_ref) > 0 else float("nan"),
        # 未重标定（β=1,α=0）与"只减均值"（β=0）两条参照 —— **都对同一个 `fit_target` 算**：
        # ⚠ 拿 `d_row` 去当参照是第二十四轮那个 bug 的同一条（顺位点项漏在外面）。
        "std_ratio_unfit": float(np.std(_fit_s - _v_s) / np.std(_ref))
        if np.std(_ref) > 0 else float("nan"),
        "std_ratio_flat": float(np.std(_fit_s - _fit_s.mean()) / np.std(_ref))
        if np.std(_ref) > 0 else float("nan"),
        "adv_std_norm_student": float(adv[keep].std()) if keep is not None else float(adv.std()),
    })
    # ★ 红证：重标定后**不可能**比"只减均值"更差，也不该比"原样相减"更差
    #   （`β=0` 与 `β=1,α=0` 都是最小二乘族的成员 ⇒ 解不会比它们差）。
    #   这条断言是"2.671× 那种量纲事故"的守卫：再出现同类接错，日志里先炸，而不是悄悄训完。
    if baseline_fit == "scale" and mode == "hand" and np.isfinite(stats["std_ratio_raw"]):
        limit = min(v for v in (stats["std_ratio_flat"], stats["std_ratio_unfit"])
                    if np.isfinite(v))
        if stats["std_ratio_raw"] > limit + 1e-6:
            raise SystemExit(f"基线重标定后优势方差不降反升（{stats['std_ratio_raw']:.4f}× > "
                             f"{limit:.4f}×）—— 最小二乘解不可能比常数基线/原样相减更差，"
                             f"说明 `_fit_baseline`、`fit_target` 或优势构造写错了")
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


def assert_ppo_excludes_rwr(objective: str, rwr_beta: float) -> None:
    """**PPO 时禁 RWR**（§14 P2-⑥；抽成函数是为了自检能直接喂数）。

    PPO 的替代项已经用优势（回报减基线）加权了，再叠一层 `exp(回报/β)` 等于把**同一个回报**
    平方加权：少数高回报行会主导整个策略损失，而日志里看不出来（`clip_frac`/`kl`/`gate` 全正常）。
    """
    if objective == "ppo" and rwr_beta and rwr_beta > 0:
        raise SystemExit(
            f"--objective ppo 与 --rwr-beta {rwr_beta:g} 不能同时用：优势已经是回报加权的，"
            f"再乘一层 exp(回报/β) 等于对同一份回报**二次加权**（高回报行会主导策略损失）。"
            f"要 PPO 就把 --rwr-beta 设 0；要 RWR 就用 --objective rwr")


def _advantages(out: dict[str, torch.Tensor], b: dict[str, torch.Tensor],
                row_keep: torch.Tensor | None, key: str = "value",
                norm: str = "batch") -> torch.Tensor:
    """`A = R − E[V(s)]`，**只在学生行上归一化**（设计 §7.4 / v3 P4 的硬口径）。

    - `R` = 数据集里的 `value`（`final_scores − 起点`，千点）或 **`rtg`（逐决策 reward-to-go）**；
      ⚠ 两者**必须是值头正在学的那个量**（`--value-target` 同时决定这里与值头目标），否则
      critic 与优势不同量纲，优势会被系统性偏移（第五轮的 `A = R_整场 − E[V(s)]` 就是这个毛病：
      它没有"这一手之后发生了什么"，信用分配粗到整场，实测 Δ=−3.54 顺位点）；
    - `E[V(s)]` = 值头 51 个分箱的期望（分箱中心 `linspace(-30, +30)`，与 `VALUE_RANGE` 同量纲）；
    - ⚠ **归一化只用学生行**：把对手/老师那 3/4 的行算进均值方差，会把优势的尺度带偏
      （v3 实测过学生行占比 0.75 而非 0.25 那种静默失真）。
    @param norm `batch` = **批内** z-score（老行为；缺点是一个 epoch 里每批的尺子都不同，
        PPO 的 clip ε 因此失去语义 —— 只有"V 随 critic 每步重算"的遗留路径才该用它）；
        `none` = 原样（配合 `adv_fixed` 的**全局**归一化：那条路径在 `_hand_advantage` 里
        已经按学生行全局 z-score 过一次，**绝不能再归一化第二次**）。
    """
    center = torch.linspace(-M.value_range(), M.value_range(), M.value_bins(),
                            device=out["value"].device)
    v_hat = (torch.softmax(out["value"], dim=-1) * center).sum(-1)
    adv = b[key] - v_hat
    if norm == "batch" and row_keep is not None:
        k = row_keep > 0
        if bool(k.any()):
            sel = adv[k]
            adv = torch.where(k, (adv - sel.mean()) / (sel.std(unbiased=False) + 1e-6),
                              torch.zeros_like(adv))
    return adv


def _behaviour_logprobs_at(model: M.V4Model, data: dict, index: np.ndarray, device: str,
                           batch: int, temp: float) -> np.ndarray:
    """**指定行**上的 `log π_old(a|s)`（`index` = 行下标）。逐 id 复算 / 探针共用这一份。

    ⚠ 与 `_behaviour_logprobs` 是同一个实现（那一份就是 `index = arange(n)` 的特例）——
    两份会漂移，而"漂移"在这里意味着"闸门量的尺子与训练用的不一致"（= 假绿）。
    """
    model.eval()
    idx_all = np.asarray(index, dtype=np.int64)
    out = np.zeros(idx_all.size, dtype=np.float32)
    with torch.no_grad():
        for i0 in range(0, idx_all.size, batch):
            idx = idx_all[i0:min(i0 + batch, idx_all.size)]
            b = _batch(data, idx, device)
            logits = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"],
                           h=b.get("h"), n_legal=b["nlegal"])["policy"]
            if temp > 0:
                logits = logits / float(temp)
            lp = torch.log_softmax(logits, dim=-1).gather(1, b["label"][:, None]).squeeze(1)
            out[i0:i0 + idx.size] = lp.to("cpu").numpy()
    return out


def _behaviour_logprobs(model: M.V4Model, data: dict, device: str, batch: int,
                        temp: float) -> torch.Tensor:
    """采集那一步的 `log π_old(a|s)`（**用采集时的权重重算**，不是从轨迹里读的）。

    为什么能重算：前向是**确定性**的（无 dropout），输入就是同一份 obs 张量、
    权重就是采集用的那份 checkpoint、温度就是策略串里的 `#T`
    ⇒ 重算出来的 log-prob 与当时采样用的分布逐位相同（容差内）。这是 PPO 能离线做的前提。
    """
    n = int(data["nlegal"].shape[0])
    return torch.from_numpy(_behaviour_logprobs_at(model, data, np.arange(n), device, batch, temp))


def resolve_weight_path(spec: str) -> Path:
    """`--behaviour` / `--init` 的权重来源：`*.bin` / `*.pt` 文件，**或含 `model.pt` 的 ckpt 目录**。

    ⚠ 第二十六轮实测的 bug：`--init` 的 help 一直写着"ckpt 目录或 net.bin"，但底层只收**文件**
    ⇒ 给 ckpt 目录会报 `--behaviour 找不到文件：<目录>`（**消息里的参数名还是错的**）。
    "值头先行"相正是把 ckpt 目录交给 `--init` 的那种用法，所以这个洞必须补。
    """
    p = Path(spec)
    if p.is_dir():
        cand = p / "model.pt"
        if not cand.is_file():
            raise SystemExit(f"权重目录里没有 model.pt：{p}")
        return cand
    if not p.is_file():
        raise SystemExit(f"权重来源找不到：{p}（给 net.bin / model.pt 文件，或含 model.pt 的目录）")
    return p


def _load_behaviour(spec: str, device: str) -> M.V4Model:
    """采集用的那份权重：`*.pt`（checkpoint）或 `net.bin`（格式 2）都收；目录按其 `model.pt` 解。"""
    p = resolve_weight_path(spec)
    if p.suffix == ".bin":
        from . import export as v4export
        parsed = v4export.read_net(p)
        # ⚠ `arch` **显式传**：A− 训练里拿来当 `--behaviour` 的可能仍是 v4 的现任网
        #   （新拓扑没有 v4→a- 的权重映射，不能 warm-start，但"数据由谁生成"没变）。
        model = M.build(1, arch=parsed.get("arch"), **parsed["dims"])
        model.load_state_dict(v4export.adapt_to_model(v4export.state_from_net(parsed), model, what=str(p)), strict=True)
    else:
        from . import export as v4export
        ck = torch.load(p, map_location="cpu", weights_only=False)
        cfg = ck.get("config", {}) or {}
        model = M.build(1, arch=cfg.get("arch"),
                        **(cfg.get("dims") or M.build(1).dims()))
        # ⚠ 旧 ckpt 没有门控张量 ⇒ `normalize_state` 补 0（门控恒等）；policy 宽度也在这里归一。
        model.load_state_dict(v4export.adapt_to_model(v4export.normalize_state(ck["model"]), model, what=str(p)), strict=True)
    return model.to(device).eval()


def _ref_logits(ref_model: M.V4Model, data: dict, device: str, batch: int,
                ablate: dict[str, list[tuple[int, int]]] | None = None) -> np.ndarray:
    """**冻结**参考策略 π_ref 的逐行 logits `[n, lmax]`（float32）—— KL 锚的右半边。

    为什么**预算一次**而不是每个 batch 现算（与 PPO 预算 `logp_old` 同一思路）：
    π_ref 不训练 ⇒ 同一行的 logits 在整轮里是常量；预算 = `n/batch` 次前向，
    现算 = `epochs ×` 那么多次（β>0 时白烧一半算力，而它**永远不进反向图**）。

    ⚠ 三条纪律（少一条就等于把参考策略也"训"进去了）：

    ① `eval()` + `torch.no_grad()`（调用方还额外 `requires_grad_(False)`）—— π_ref 不是要训的网；
    ② **温度不在这里缩放**（原样存 logits）：温度只在 `ref_kl_term()` 里按"与策略损失同一把尺子"
       加一次。两处各缩一次 = 除以 `T²`（`_policy_logits()` 那个坑的翻版，症状同样是静默失真）；
    ③ `ablate` **必须传进来**、与学生的输入口径一致：否则消融矩阵那一轮里学生看的是被消融的输入、
       锚看的是没消融的输入 ⇒ 量出来的"KL"不是同一对分布上的量。
    """
    n = int(data["nlegal"].shape[0])
    lmax = int(data["cand"].shape[1])
    out = np.empty((n, lmax), dtype=np.float32)
    ref_model.eval()
    with torch.no_grad():
        for i0 in range(0, n, batch):
            idx = np.arange(i0, min(i0 + batch, n))
            b = _batch(data, idx, device, ablate=ablate)
            logits = ref_model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"],
                               h=b.get("h"), n_legal=b["nlegal"])["policy"]
            # ⚠ 非法候选是被 `masked_fill(-inf)` 出来的（`Heads.forward`）⇒ 这里会存进 -inf。
            #   那是**对的**（`ref_kl_term` 里按 `isfinite` 把那几格挑掉），别在这里 clamp 成有限值。
            out[i0:i0 + idx.size] = logits.to("cpu").numpy()
    return out


def ref_kl_term(logits: torch.Tensor, ref_logits: torch.Tensor,
                row_keep: torch.Tensor | None = None, temp: float = 1.0) -> torch.Tensor:
    """`KL(π_θ(·|s) ‖ π_ref(·|s))` 的**逐行均值**（纯函数；自检/临时脚本直接喂数）。

    π 一律取**同一个温度**的 `softmax(logits/T)`（与 PPO 的策略损失同一把尺子，见
    `_policy_logits()` 的坑：温度只作用一侧会让 `ρ` 炸到 1e15、一个 step 打成 NaN）。

    ## 为什么是**反向 KL**（π_θ‖π_ref），不是正向（π_ref‖π_θ）

    · `KL(π_θ‖π_ref) = E_{π_θ}[log π_θ − log π_ref]` —— **mode-seeking**：
      π_ref **低概率**的地方，只要 π_θ 也低就不受罚 ⇒ 它鼓励 π_θ **比 π_ref 更尖**，
      主动放弃 π_ref 的尾巴（把质量挪到 π_ref 的几个峰上）。
    · `KL(π_ref‖π_θ)` —— **mass-covering**：π_ref **有质量**的地方 π_θ 必须也有，
      ⇒ π_θ 被逼着覆盖 π_ref 的整个支撑集（连尾巴一起），在"只想别偏离现任"这个目标下
      反而会**放大** π_θ 的尾巴、把分布摊平。

    W3 的目标是"**单调精修**"（让每轮真的能动、但别离现任太远），`docs/VALVES-AND-FIXTURES.md`
    §3 的 W3 条目写的就是 `β · KL(π_θ ‖ π_ref)` ⇒ 这里按它实现。
    两者的 logits 梯度**长得不一样**（临时脚本拿它当红证，别记反）：
    · 反向（本函数）：`∇_z = π_θ ⊙ (log π_θ − log π_ref − KL)` —— 每个 `π_θ = 0` 的格子上恒为 0，
      所以它只把**学生已经给质量的**候选往 ref 那边挪（这就是 mode-seeking 的梯度长相）；
    · 正向：`∇_z KL(π_ref‖π_θ) = π_θ − π_ref`（处处非零，ref 有质量的格子都会被拉过去）。
    要换方向只改这里的 `logp`/`logq` 互换，日志与判据都不用动。

    ⚠ **两个 `-inf` 相减是 `nan`**（非法候选在两侧都被 `masked_fill(-inf)`）：必须先按
    `isfinite` 把那些格挑掉再求和 —— `logp.exp()` 在那里是 0，但 `0 * nan = nan` 会整行污染
    （症状：`ref_kl=nan`、`KL(θ‖θ) ≠ 0`，而"logits 逐位相同 ⇒ KL=0"那条判据当场红）。
    这里连同 `logq` 一起换成有限替身，**任何中间量都不出现 inf/nan**（`where` 只当"选择"用）。
    """
    if temp and float(temp) != 1.0:
        logits = logits / float(temp)
        ref_logits = ref_logits / float(temp)
    logp = torch.log_softmax(logits, dim=-1)
    logq = torch.log_softmax(ref_logits, dim=-1)
    # 非法候选（两侧都 -inf）：先把 logq 换成 0，`logp - logq_safe` 于是不再是 `-inf - (-inf) = nan`；
    # 再按 `isfinite(logp)` 把那几格的选择值置 0（`logp.exp()` 在那里本来就是 0）。
    logq_safe = torch.where(torch.isfinite(logq), logq, torch.zeros_like(logq))
    d = torch.where(torch.isfinite(logp), logp - logq_safe, torch.zeros_like(logp))
    per_row = (logp.exp() * d).sum(-1)
    if row_keep is None:
        return per_row.mean()
    # 归一化口径与 `compute_loss` 里的 `reduce_row(..., row_keep)` 逐位一致（0/1 掩码）
    use = (row_keep > 0).to(per_row.dtype)
    return (per_row * use).sum() / use.sum().clamp_min(1e-6)


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


def _behaviour_kl(model: M.V4Model, data: dict, idx: np.ndarray,
                  logp_old: torch.Tensor, temp: float, device: str) -> float:
    """`KL(π_old‖π_new)` 的**逐行均值**（在给定行子集上；`π_new` 用 `softmax(logits/T)`）。

    ⚠ 这是**闸门的量本身**，单独抽出来是为了让**续跑路径**能拿同一条探针行做"连续性"判据
    （见 `assert_resume_behaviour` 的第 ③ 条）—— 两份实现必然漂移，所以只留这一个。

    ⚠ 模式：探针必须在**同一个模式**下算 —— 存档那一刻模型可能刚被 `evaluate()` 置 eval，
    而续跑时它还是 train。模型现在**没有** dropout/BN（`model.py` 开头的硬要求：前向必须确定性），
    所以这一步对数值**零影响**；它只是把"模式"从判据里排除掉（将来真加了 dropout 也不会假红）。
    """
    was_training = model.training
    model.eval()
    try:
        with torch.no_grad():
            b0 = _batch(data, idx, device)
            o0 = model(b0["tile"], b0["evt"], b0["ctx"], b0["cand"], mask=b0["mask"], h=b0.get("h"))
            lp_new = torch.log_softmax(_policy_logits(o0, temp), dim=-1).gather(
                1, b0["label"][:, None]).squeeze(1)
            return float((logp_old[idx].to(device) - lp_new).mean())
    finally:
        model.train(was_training)


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
    kl = _behaviour_kl(model, data, idx, logp_old, temp, device)
    if not math.isfinite(kl) or abs(kl) > 1e-2:
        raise SystemExit(
            f"PPO 口径不一致：初始化时 KL(π_old‖π_new) = {kl:.4g}（应为 ≈0）—— 检查"
            f"① `--init` 是不是与 `--behaviour` 同一份权重；② `--behaviour-temp` 是不是与数据集"
            f" `student` 串里的 `#T` 一致（`logp_new` 与 `logp_old` 必须在同一个 `softmax(logits/T)` 上算）")
    return kl


# ================================================================ BL-14：逐 behaviour_id 口径
#: ρ 的分布只报分位数 —— 采样上限（够了，且不许为它撑内存）
RHO_SAMPLE_CAP = 400_000


def behaviour_ids_of(data: dict) -> np.ndarray | None:
    """紧凑集的逐行 `behaviour_id`（`None` = 这份数据集**没有**这一列 ⇒ 旧单策略路径）。"""
    col = data.get("behaviour_id")
    return None if col is None else np.asarray(col, dtype=np.int64)


def behaviour_table_of_meta(meta: dict) -> list[dict]:
    """meta 的 `behaviour.table[]`（`enabled=false` ⇒ 空表 ⇒ 旧路径）。**缺表就报错**。"""
    b = (meta or {}).get("behaviour") or {}
    if not b.get("enabled"):
        return []
    tbl = list(b.get("table") or [])
    if not tbl:
        raise SystemExit("meta.behaviour.enabled=true 但 `behaviour.table` 是空的 —— "
                         "按 fail-closed 拒跑（⛔ 不把「没身份」当成「单策略」）")
    return tbl


def behaviour_table_digest(table: list[dict]) -> str:
    """行为策略表的**规范指纹**（只含身份四元组，⛔ 不含路径 —— 换盘不算换了身份）。"""
    canon = [[int(r["id"]), str(r["spec"]), r.get("temp"), r.get("sha256")] for r in table]
    return hashlib.sha256(json.dumps(canon, sort_keys=True,
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _logp_digest(a: np.ndarray) -> str:
    """一串 `logp` 的指纹（float32 原始字节）—— "记录"侧的载体：同数据同权重 ⇒ 逐位相同。"""
    return hashlib.sha256(np.ascontiguousarray(a, dtype=np.float32).tobytes()).hexdigest()


def _probe_rows(idx: np.ndarray, k: int, seed: int = 0) -> np.ndarray:
    """从 `idx` 里取 `min(k, |idx|)` 行**确定性**探针（固定 rng ⇒ 两次调用同一条探针）。"""
    if idx.size <= k:
        return idx
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(idx, size=int(k), replace=False))


def per_behaviour_logprobs(data: dict, table: list[dict], ids: np.ndarray, device: str,
                           batch: int, *, default_temp: float, allow_unverified: bool,
                           tag: str, needed_ids: set[int] | None = None,
                           probe: int = 0) -> tuple[torch.Tensor, dict]:
    """**逐 `behaviour_id`** 算 `logp_old`（K 个行为 ⇒ K 遍前向；每多一种 ≈ +59 s/轮）。

    三条硬口径（少一条就是"ρ 全错但训练照跑"）：

    ① **身份先核对**：载入 `table[id].path` 之前先用 `sha256` 核对它就是表里声明的那份权重
       （⛔ 不信路径 —— 路径可以指向被换过的文件；这正是 `_behaviour_record` 的注释里那条教训）。
    ② **温度逐 id 取**：`table[id].temp`（策略串里的 `#T`），缺省才退回 `default_temp`。
       一份混代紧凑集里每代的 `#T` 可以不同，用一个温度算全部 = `#0.5` 那个事故的翻版。
    ③ **未验证的 id 不许静默用**：缺省 `SystemExit`；`--allow-unverified-behaviour` 才降级为
       "把这些行**显式排除**出策略损失 + 记账 + 响亮打印"（⛔ 不是静默裁剪）。

    返回 `(logp[n]（float32；被排除的行为在那些行上是 NaN）, info)`。
    """
    n = int(data["nlegal"].shape[0])
    out = np.full(n, np.nan, dtype=np.float32)
    info: dict = {"per_id": {}, "excluded_rows": {}, "builtin_rows": {}, "unverified": [],
                  "order": [], "probe_lp": {}, "probe_idx": {}}
    for bid in sorted(int(x) for x in np.unique(ids)):
        if bid < 0 or bid >= len(table):
            raise SystemExit(f"`behaviour_id`={bid} 越界（表里只有 {len(table)} 项）—— "
                             f"紧凑集与 meta **不是同一份**，fail-closed 拒跑")
        rec = table[bid]
        rows = np.flatnonzero(ids == bid)
        entry: dict = {"rows": int(rows.size), "spec": rec.get("spec"),
                       "temp": rec.get("temp"), "sha256": rec.get("sha256"),
                       "verified": bool(rec.get("verified"))}
        if rec.get("kind") == "builtin":
            # **内置策略**（teacher / bot / first / pass / random）：**没有权重可算 logp**。
            # ⚠ 只有 `kind == "builtin"` 才走这一支 —— `path is None` **不是**"内置"的证据：
            #   一个 `net:` 的策略串解析失败时也是 `path=None`，那种必须按**未验证** fail-closed
            #   （自检的负向断言当场抓到了这条：把"取不到"当"按定义没有"就是假绿）。
            print(f"  [{tag}] behaviour_id={bid} {rec.get('spec')!r} 是**内置策略**（无权重）⇒ "
                  f"{rows.size} 行写 NaN（⛔ 不进策略损失；由学生掩码与 NaN 断言双重保证）")
            info["builtin_rows"][str(bid)] = int(rows.size)
            entry.update({"builtin": True})
            info["per_id"][str(bid)] = entry
            continue
        if not rec.get("verified"):
            if not allow_unverified:
                raise SystemExit(
                    f"⛔ behaviour_id={bid}（{rec.get('spec')!r}）**身份未验证**"
                    f"（{rec.get('note')}）—— PPO 的 `logp_old` 必须来自**已知**的那份权重。"
                    f"要么让构建器钉住 `sha256`（`--behaviour-root` / `--require-behaviour-sha`），"
                    f"要么显式 `--allow-unverified-behaviour` 接受「这些行被排除出策略损失」。"
                    f"按 fail-closed 拒跑（⛔ 不许把「取不到」当成「没问题」）")
            info["unverified"].append(bid)
            info["excluded_rows"][str(bid)] = int(rows.size)
            entry.update({"excluded": True})
            info["per_id"][str(bid)] = entry
            continue
        path = rec.get("path")
        if not path or not Path(path).is_file():
            raise SystemExit(f"⛔ behaviour_id={bid} 记的权重文件不存在：{path!r}")
        got = _sha256_file(path)
        if got != str(rec.get("sha256")):
            raise SystemExit(
                f"⛔ behaviour_id={bid} 的**身份对不上**：表里记 {str(rec.get('sha256'))[:16]}… "
                f"而磁盘上 {path} 是 {got[:16]}… —— 数据是 A 采的却拿 B 的权重算 `logp_old`，"
                f"ρ 会整体错而训练照跑。这正是逐 id 复算要拦的那件事")
        temp = float(rec["temp"]) if rec.get("temp") else float(default_temp)
        t0 = time.perf_counter()
        mdl = _load_behaviour(str(path), device)
        # ★ 探针行**永远算**（它是判据 2 的"记录"侧；每 id 只有 ~4096 行，代价小）。
        pr = _probe_rows(rows, probe, seed=1000 + bid) if probe > 0 else rows
        lp_probe = _behaviour_logprobs_at(mdl, data, pr, device, batch, temp)
        info["probe_lp"][str(bid)] = lp_probe
        info["probe_idx"][str(bid)] = pr
        # ⚠ **全量只在"这一代真有学生行"时算**：非学生行的 `logp_old` 永远不会被读
        #   （策略损失走 `row_keep`、ρ 记账走 `row_keep`、陈旧度与续跑探针只走探针行）
        #   ⇒ 为它们跑一遍 66 万行前向是**纯浪费**（实测 ≈59 s/行为/轮）。
        #   ⛔ 这不是"少算一个判据"：**每个 id 的复算探针照算**，只是不再为不参与损失的行买单。
        if needed_ids is None or bid in needed_ids:
            lp = _behaviour_logprobs_at(mdl, data, rows, device, batch, temp)
            out[rows] = lp
            entry.update({"logp_sha256": _logp_digest(lp),
                          "logp_mean": float(np.mean(lp))})
        else:
            entry.update({"logp_sha256": None, "logp_mean": None,
                          "full_pass_skipped": "该 id 没有学生行（`logp_old` 不会被读）"})
        del mdl
        info["order"].append(bid)
        entry.update({"temp_used": temp, "probe_rows": int(pr.size),
                      "probe_sha256": _logp_digest(lp_probe),
                      "seconds": round(time.perf_counter() - t0, 1)})
        info["per_id"][str(bid)] = entry
        print(f"  [{tag}] behaviour_id={bid} {rec.get('spec')}（温度 {temp:g}）"
              f"：{rows.size} 行"
              + (f" · logp mean {entry['logp_mean']:+.4f}" if entry.get("logp_mean") is not None
                 else " · **全量已跳过**（无学生行）")
              + f" · 探针 {entry['probe_rows']} 行 sha256 {entry['probe_sha256'][:16]}…"
              f" · {entry['seconds']}s")
    return torch.from_numpy(out), info


def assert_behaviour_gate_multi(model: M.V4Model, data: dict, table: list[dict], ids: np.ndarray,
                                logp_old: torch.Tensor, init_sha: str | None, temp_default: float,
                                device: str, *, probe: int, tag: str,
                                probe_lp: dict | None = None) -> dict:
    """**逐 `behaviour_id` 复算并比对** —— 取代「`--init` 必须 == 那**唯一**一份数据生成策略」。

    四把尺子（缺一即 `SystemExit`，退出码 1、无 traceback）：

      ① **挂钩**（唯一硬拒）：`--init` 的 `sha256` 必须与**至少一个** `behaviour_id` 相同
         —— `--init` 与全部行为脱钩 = 起点与数据毫无关系（判据 4）。
      ② **逐 id 复算**：从 `table[id]` 的**身份**重新载入权重、重算探针行上的 `logp`，
         要求 `KL(记录‖复算) ≤ 1e-2` **且** `max|Δlogp| ≤ 1e-4`（同一条数据、同一策略 ⇒ 必须 ≈0）。
      ③ **陈旧度 = 预算/记账**（⛔ 不再是硬拒）：逐 id `KL(π_id‖π_init)`；
         超预算只**记账 + 响亮告警**（见 `--stale-kl-budget` 的预注册理由）。
      ④ **ρ 见 `compute_loss`**：上限与分布由 `--rho-max` 管（判据 3 的负向红证在那一头）。
    """
    n = int(data["nlegal"].shape[0])
    out: dict = {"init_sha256": init_sha, "table_digest": behaviour_table_digest(table),
                 "probe_rows_per_id": int(probe), "per_id": {}, "matched_ids": []}
    if init_sha is None:
        raise SystemExit(
            "⛔ 多行为 PPO **必须**给 `--init` —— 不然证明不了「起点与哪一条数据链同源」")
    present = [int(x) for x in sorted(np.unique(ids))]
    for bid in present:
        rec = table[bid]
        if rec.get("sha256") and str(rec["sha256"]) == init_sha:
            out["matched_ids"].append(bid)
    if not out["matched_ids"]:
        raise SystemExit(
            f"⛔ `--init`（sha256 {init_sha[:16]}…）与**全部** behaviour_id 都脱钩："
            f"{ {b: (table[b].get('sha256') or 'null')[:16] for b in present} } —— "
            f"起点与数据不是同一条链，`logp_old`/ρ/优势全都无从谈起 ⇒ 按 fail-closed 拒跑。"
            f"（判据 4：⛔ 不许把这条闸门削成「查一下、然后照跑」）")
    for bid in present:
        rec = table[bid]
        rows = np.flatnonzero(ids == bid)
        pr = _probe_rows(rows, probe, seed=1000 + bid)
        entry: dict = {"rows": int(rows.size), "probe_rows": int(pr.size),
                       "sha256": rec.get("sha256"), "verified": bool(rec.get("verified"))}
        if rec.get("kind") == "builtin":
            # 内置策略：没有权重可比 ⇒ 跳过复算与陈旧度（**只记账**）。这些行必然是
            # 非学生行（`is_student` 排掉），所以"没有 logp"不影响策略损失。
            entry.update({"builtin": True, "note": "内置策略（无权重）：只记账，不参与复算/陈旧度"})
            out["per_id"][str(bid)] = entry
            continue
        if rec.get("verified"):
            temp = float(rec["temp"]) if rec.get("temp") else float(temp_default)
            mdl = _load_behaviour(str(rec["path"]), device)
            lp = _behaviour_logprobs_at(mdl, data, pr, device, 256, temp)
            del mdl
            # 「**记录**」侧 = 主路径在**同一条探针行**上算出来的那一串（`probe_lp`）。
            #   ⚠ 为什么不用 `logp_old[pr]`：全量前向对"没有学生行"的 id **会跳过**（那 66 万行
            #   永远不被读），但**探针照算** ⇒ 用探针那一串才既严格又不浪费。
            if probe_lp is not None and str(bid) in probe_lp:
                rec_lp = np.asarray(probe_lp[str(bid)], dtype=np.float64)
            else:
                rec_lp = np.asarray(logp_old, dtype=np.float64)[pr]
            bad = ~np.isfinite(rec_lp)
            if bool(bad.any()):
                raise SystemExit(f"⛔ behaviour_id={bid} 有 {int(bad.sum())}/{pr.size} 探针行"
                                 f"没有 `logp_old`（NaN）⇒ 复算无从比对")
            d = rec_lp - np.asarray(lp, dtype=np.float64)
            entry.update({"kl_recompute": float(d.mean()), "max_abs_logp_dev": float(np.abs(d).max()),
                          "recompute_sha256": _logp_digest(lp)})
            if not math.isfinite(entry["kl_recompute"]) or abs(entry["kl_recompute"]) > 1e-2 \
                    or entry["max_abs_logp_dev"] > 1e-4:
                raise SystemExit(
                    f"⛔ behaviour_id={bid} 的**复算对不上**：KL(记录‖复算)="
                    f"{entry['kl_recompute']:+.3e}（应 ≈0）、max|Δlogp|="
                    f"{entry['max_abs_logp_dev']:.3e}（应 ≤1e-4）—— 说明这条数据的"
                    f"`logp_old` 不是用表里那份权重/那个温度算的（挂钩错了）")
            # 陈旧度：**同一个温度尺子**上的行为 KL（π_id ‖ π_init）。
            #   ⚠ 只喂探针行（把别的行留 NaN）—— `_behaviour_kl` 只按 `idx` 取，NaN 不会漏进来；
            #   这样"全量被跳过"的 id 也能给出陈旧度（判据 ③ 对**每一个**行为都成立）。
            lo = torch.full((n,), float("nan"), dtype=torch.float32)
            lo[pr] = torch.from_numpy(rec_lp.astype(np.float32))
            entry["kl_stale"] = _behaviour_kl(model, data, pr, lo, temp, device)
            entry["matched_init"] = bool(bid in out["matched_ids"])
        out["per_id"][str(bid)] = entry
    return out


def rho_summary(acc: dict | None) -> dict:
    """ρ 的分布 + 超限行数（判据 3 的原始输出；`acc=None` ⇒ 空表）。"""
    if not acc:
        return {}
    s = np.concatenate(acc["samples"]) if acc.get("samples") else np.zeros(0, dtype=np.float32)
    # ⚠ **三个数分开报**（⛔ 别用一个"max ρ"糊过去）：
    #   `rho_max`/`rho_min` = ρ 本身的上下极值（**采样**里算）；`rho_worst_two_sided` =
    #   `max(ρ, 1/ρ)` = 上限判据真正比的那个量（它可能来自**很小的** ρ）。实测踩过：
    #   `|log ratio|max = 23.69` 全部来自 ρ = 5.1e-11，只看 `max ρ` 会把这次越界洗成绿的。
    hi = max(float(acc.get("worst_hi", 0.0)), float(s.max()) if s.size else 0.0)
    lo = min(float(acc.get("worst_lo", float("inf"))), float(s.min()) if s.size else float("inf"))
    out = {"n_rows": int(acc.get("n_rows", 0)), "n_sampled": int(s.size),
           "n_gt_rho_max": int(acc.get("n_gt_max", 0)),
           "n_gt_log_ratio_clamp": int(acc.get("n_gt_clamp", 0)),
           "rho_max": hi, "rho_min": (lo if math.isfinite(lo) else None),
           "rho_worst_two_sided": float(acc.get("worst_abs", 0.0)),
           "clipped_batches": int(acc.get("clipped_batches", 0))}
    if s.size:
        out.update({"rho_p50": float(np.quantile(s, 0.50)), "rho_p90": float(np.quantile(s, 0.90)),
                    "rho_p99": float(np.quantile(s, 0.99))})
    return out


def _ppo_plan_multi(*, model: M.V4Model, train_data: dict, device: str, args, meta: dict,
                    resume: dict | None, n: int, b_ids: np.ndarray, b_table: list[dict],
                    beh_rec: dict | None, keep_tr) -> dict:
    """★ BL-14：**多行为（混代）** PPO 的整套前置（逐 id `logp_old` + 逐 id 复算闸门 + 陈旧度预算 + ρ）。

    为什么单独一条路（而不是改老路）：老路的闸门是「**那一份**行为策略必须 == `--init`」。异步回路里
    `train(D_k, θ_k) ‖ collect(D_{k+1}, θ_k)` 天然产出**两代**数据 ⇒ 那条尺子在结构上就不成立。
    这条路把"要守的事"拆成**四条正交的尺子**（见 `assert_behaviour_gate_multi` 的 docstring），
    其中只有"`--init` 与全部 behaviour_id 脱钩"是硬拒 —— 其余是复算证明 + 预算记账。

    ⛔ **只吃开关**：紧凑集没有 `behaviour_id` 列 / meta 没有表 ⇒ 这条路一行都不走（老路逐字节不变）。
    """
    g = getattr
    print(f"PPO（多行为）：紧凑集带 `behaviour_id`，表内 {len(b_table)} 个行为 —— "
          f"逐 id 重算 `log π_old`（总 {n} 条；**每多一种行为 ≈ +59 s/轮**，如实记）")
    allow_unver = bool(g(args, "allow_unverified_behaviour", False))
    probe = int(g(args, "behaviour_probe", 4096) or 0)
    # ⚠ **全量前向只给"真有学生行"的行为算**（其余行的 `logp_old` 永远不会被读）——
    #   每个额外行为 ≈ +59 s/轮的成本**只花在要用的那几代上**。探针行**每个 id 都算**。
    stu = train_data.get("is_student")
    if stu is None:
        needed = {int(x) for x in np.unique(b_ids)}
    else:
        needed = {int(x) for x in np.unique(b_ids[np.asarray(stu) > 0])}
    print(f"PPO（多行为）：需要**全量** `logp_old` 的 id = {sorted(needed)}"
          f"（= 有学生行的那些；其余只算探针行 —— 无条件算 66 万行是纯浪费）")
    logp_tr, lp_info = per_behaviour_logprobs(
        train_data, b_table, b_ids, device, int(g(args, "eval_batch", 256) or 256),
        default_temp=float(g(args, "behaviour_temp", 0.0) or 0.0) or 1.0,
        allow_unverified=allow_unver, tag="train", needed_ids=needed, probe=probe)
    # ---- ②③ 逐 id 复算 + 陈旧度预算 ------------------------------------------------
    init_spec = g(args, "init", None)
    init_sha = _sha256_file(resolve_weight_path(str(init_spec))) if init_spec else None
    gate = assert_behaviour_gate_multi(model, train_data, b_table, b_ids, logp_tr, init_sha,
                                      float(g(args, "behaviour_temp", 0.0) or 0.0) or 1.0,
                                      device, probe=probe, tag="train",
                                      probe_lp=lp_info["probe_lp"])
    matched = int(gate["matched_ids"][0])
    matched_temp = float(b_table[matched]["temp"]) if b_table[matched].get("temp") else \
        (float(g(args, "behaviour_temp", 0.0) or 0.0) or 1.0)
    budget = float(g(args, "stale_kl_budget", 0.30) or 0.0)
    print(f"PPO（多行为）：`--init` sha256 {str(init_sha)[:16]}… ⇒ 挂钩 behaviour_id={matched}"
          f"（其余 {len(gate['matched_ids']) - 1} 个同 sha）· 温度尺子 {matched_temp:g}")
    for bid, e in sorted(gate["per_id"].items(), key=lambda kv: int(kv[0])):
        if e.get("builtin"):
            print(f"  id={bid}: **内置策略**（无权重）· rows={e['rows']} ⇒ 只记账（这些行不是学生行）")
            continue
        if not e.get("verified"):
            print(f"  id={bid}: **未验证** ⇒ 该 id 的 {e['rows']} 行**显式排除**出策略损失"
                  f"（`--allow-unverified-behaviour` 已给；⛔ 不是静默裁剪）")
            continue
        st = e.get("kl_stale")
        flag = ""
        if e.get("matched_init"):
            flag = " ← `--init` 那一份（恒等 ⇒ 必须 ≈0）"
            if not math.isfinite(float(st)) or abs(float(st)) > 1e-2:
                raise SystemExit(
                    f"⛔ behaviour_id={bid} 与 `--init` 声明同 sha256，但 KL(π_id‖π_init)="
                    f"{float(st):+.3e}（应 ≈0）—— 同一份字节的权重不该给出两个分布；"
                    f"查温度尺子（`#T`）与 `--behaviour-temp` 是否一致")
        elif budget > 0 and math.isfinite(float(st)) and abs(float(st)) > budget:
            flag = f" ⚠ **陈旧度超预算**（|{float(st):.3f}| > {budget:g}）—— 记账 + 告警，⛔ 不是硬拒"
        print(f"  id={bid}: KL(记录‖复算)={e.get('kl_recompute', float('nan')):+.2e} · "
              f"max|Δlogp|={e.get('max_abs_logp_dev', float('nan')):.2e} · "
              f"探针={e.get('probe_rows')} 行 · rows={e['rows']} · "
              f"陈旧度 KL(π_id‖π_init)={float(st):+.4f}{flag}")
    over = [b for b, e in gate["per_id"].items()
            if e.get("verified") and not e.get("matched_init")
            and budget > 0 and math.isfinite(float(e.get("kl_stale", float("nan"))))
            and abs(float(e["kl_stale"])) > budget]
    if over:
        print(f"  ⚠⚠ **陈旧度预算**（`--stale-kl-budget {budget:g}`）：{len(over)} 个 id 越界 "
              f"（{over}）—— 按预注册**降级为预算/记账**（陈旧度是质量轴、不是口径轴）："
              f"目标函数仍是「在**记录的行为分布**上」定义的，超预算意味着这几代数据的"
              f"ρ 会更重尾（ρ 分布见下）⇒ ⛔ 结论只能按「混代可用但需降陈旧度」读")
    # ---- 行为可用掩码：未验证的 id / 内置策略的行**显式排除**（⛔ 与"静默裁剪"的区别是有账 + 有打印）
    usable = np.isfinite(np.asarray(logp_tr, dtype=np.float64))
    if not bool(usable.all()):
        if keep_tr is not None:
            bad_stu = int(((np.asarray(keep_tr) > 0) & ~usable).sum())
            if bad_stu:
                raise SystemExit(
                    f"⛔ 有 {bad_stu} 行**学生行**的 `logp_old` 是 NaN（它们的 behaviour_id 要么"
                    f"未验证、要么是内置策略）—— 学生行必须有**有权重**的行为策略，"
                    f"否则 PPO 的替代项无从定义。按 fail-closed 拒跑")
            keep_tr = keep_tr * torch.from_numpy(usable.astype(np.float32)).to(keep_tr.device)
        print(f"  策略损失的行掩码：排除 {int((~usable).sum())} 行 —— 未验证 "
              f"{sum(lp_info['excluded_rows'].values())} 行 · 内置策略 "
              f"{sum(lp_info['builtin_rows'].values())} 行 · **全量跳过的代**（无学生行，"
              f"`logp_old` 永不被读）{int((~usable).sum()) - sum(lp_info['excluded_rows'].values()) - sum(lp_info['builtin_rows'].values())} 行"
              f"（⛔ 三类都显式记账，不是静默裁剪）")
    # ---- ④ ρ 上限 + 分布记账 ------------------------------------------------------
    rho_max = float(g(args, "rho_max", 0.0) or 0.0)
    rho_action = str(g(args, "rho_max_action", "fail") or "fail")
    rho_acc: dict = {"samples": [], "n_rows": 0, "n_gt_max": 0, "n_gt_clamp": 0,
                     "worst": 0.0, "clipped_batches": 0}
    print(f"PPO（多行为）：clip={float(g(args, 'ppo_clip', 0.2) or 0.2):g} "
          f"entropy={float(g(args, 'ppo_entropy', 0.01) or 0.0):g}；"
          f"ρ 上限 `--rho-max`={rho_max:g}（动作 **{rho_action}**；0 = 关）·"
          f" 数值保险 `LOG_RATIO_CLAMP`={LOG_RATIO_CLAMP:g}（ρ≤{math.exp(LOG_RATIO_CLAMP):.1f}）"
          f"**逐行计数上报**（⛔ 不再静默夹取）")
    assert_ppo_excludes_rwr(str(g(args, "objective", "ppo")), float(g(args, "rwr_beta", 0.0) or 0.0))
    print(f"PPO（多行为）：grad-clip {float(g(args, 'grad_clip', 0.5) or 0.0):g}、"
          f"KL 早停阈值 {float(g(args, 'kl_early_stop', 0.03) or 0.0):g}")
    # ---- 续跑：沿用第八十轮那三把尺子（身份 + 链条起点 + 连续性探针），作用在**挂钩那个 id** 上
    pr = _probe_rows(np.flatnonzero(b_ids == matched), 64, seed=7)
    if resume is None:
        kl0 = float(gate["per_id"][str(matched)]["kl_stale"])
        kl_seg = None
    else:
        beh_for_resume = str(g(args, "behaviour", None) or b_table[matched].get("path") or "")
        kl0, kl_seg = assert_resume_behaviour(resume, beh_for_resume, matched_temp, model,
                                              train_data, meta, logp_tr, pr, device)
    if beh_rec is not None:
        beh_rec.update({"temp": float(matched_temp), "kl0": (None if kl0 is None else float(kl0)),
                        "kl_segment": (None if kl_seg is None else float(kl_seg)),
                        "table_digest": gate["table_digest"],
                        "matched_id": matched, "per_id": gate["per_id"]})
    return {"logp_tr": logp_tr, "p_temp": float(matched_temp), "kl0": kl0, "kl_seg": kl_seg,
            "beh_rec": beh_rec, "rho_acc": rho_acc, "keep_tr": keep_tr, "gate": gate,
            "lp_info": lp_info}


def compute_loss(out: dict[str, torch.Tensor], b: dict[str, torch.Tensor],                 weights: dict[str, float] | None = None,
                 ssl: tuple | None = None,
                 row_keep: torch.Tensor | None = None,
                 row_w: torch.Tensor | None = None,
                 ppo: tuple | None = None,
                 value_key: str = "value",
                 policy_temp: float = 1.0,
                 adv_fixed: torch.Tensor | None = None,
                 adv_norm: str = "batch",
                 ref: tuple[float, torch.Tensor] | None = None,
                 il: tuple[float, torch.Tensor] | None = None,
                 rho: tuple[float, str, dict] | None = None,
                 aux_own: tuple[torch.Tensor, float, str] | None = None
                 ) -> tuple[torch.Tensor, dict[str, float]]:
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
        ⚠ **也包括 KL 锚**（`ref`）：锚项必须与策略损失用**同一个** `softmax(logits/T)`。
    @param ref `(β, π_ref 的 logits)` —— **对现任的 KL 锚**（W3）：`total += β·KL(π_θ‖π_ref)`。
        `π_ref` 的 logits 由 `_ref_logits()` **冻结预算**（`no_grad` ⇒ 它不进反向图）；
        `ref=None`（缺省）时**一行都不走** ⇒ β=0 与加这个功能之前逐位相同。
    @param il  `(w, 引擎最优候选下标 [B])` —— **逐候选引擎标签的模仿项**（W4 第一步）：
        `total += w · CE(logits, 引擎最优候选)`，`w` 缺省 0（`il=None` ⇒ 一行都不走）。
        三条口径：① **只在学生行上**（与策略损失同一个 `row_keep`）；② 同一把温度尺子
        （`softmax(logits/T)`，与 `_policy_logits()` 逐字一致）；③ **只吃掩码、不吃 RWR 权重**
        （与 `danger` 同一个理由：它是"这张牌该不该打"的行为标签，按回报加权等于按结果
        去偏"打哪张"，会把标签本身带歪）。`b["il_target"]` 由调用方按行号取好（见 `train()`）。
    @param rho ★ BL-14：`(rho_max, rho_action, rho_acc)` —— **有界重要性比 + 记账**。
        `rho_max <= 0` 且 `rho_acc is None` ⇒ **一行都不走**（旧路径逐字节不变）。
        `rho_action = "fail"`（缺省）超限**响亮失败**；`"account"` 显式降级（夹到 `ln rho_max`）
        并把超限行数/最坏 ρ 写进 `rho_acc`（⛔ 两种都不许静默裁剪）。
    @param aux_own ★ MV-1 第一刀：`(own_win logits [B], 权重 w, 行口径)` —— 辅助子目标头
        `own_win` 的 **BCE** 项：`total += w · BCE(logits, b["own_win"])`。
        三条口径：① **`None`（缺省）⇒ 一行都不走**（与 `ref=None`/`il=None`/`rho=None`/`ssl=None`
        同一处口径 ⇒ 缺省关时逐位不变）；② **走全部行**，⛔ 不吃 `row_keep`/RWR（与
        `value`/`placement`/`belief_*` 同族：标签讲的是"这个局面"，与四条座位里谁动的手无关）；
        ③ `行口径 == "student"` 时**额外**报一个 `own_win_student`（只在学生行上的 BCE）——
        那是**次口径报告**，⛔ 不改总损失、也不改主判据。
    """
    w = weights if weights is not None else M.loss_weights()
    parts: dict[str, float] = {}
    total = torch.zeros((), device=b["label"].device)
    eff = row_keep if row_w is None else (row_w if row_keep is None else row_keep * row_w)
    ref_kl_t: torch.Tensor | None = None
    ref_beta = 0.0
    il_w = 0.0
    il_tgt: torch.Tensor | None = None
    if il is not None:
        il_w, il_tgt = float(il[0]), il[1]
    rho_max = 0.0
    rho_action = "fail"
    rho_acc: dict | None = None
    if rho is not None:
        rho_max, rho_action, rho_acc = float(rho[0]), str(rho[1]), rho[2]

    def reduce_row(per_row: torch.Tensor, w: torch.Tensor | None = None) -> torch.Tensor:
        """逐行损失 → 标量（有掩码/权重时按权重归一；没有时就是 mean，与原行为逐位相同）。

        @param w 用哪份逐行因子：缺省 = `eff`（掩码 × RWR）；只传掩码 = 不吃 RWR。
        """
        use = eff if w is None else w
        if use is None:
            return per_row.mean()
        denom = use.sum().clamp_min(1e-6)
        return (per_row * use).sum() / denom

    # ★ W3：对**现任**的 KL 锚 `β · KL(π_θ ‖ π_ref)` —— 与 objective 无关（bc/rwr/ppo 都成立），
    #   所以放在策略损失之前、只算一次。三条口径（见 `ref_kl_term()` 的推导）：
    #   ① **只在学生行上算**：与策略损失同一个 `row_keep`（对手/老师的行不是"我们要训的那个策略"的行为）；
    #   ② **同一把温度尺子**：`softmax(logits/T)`，与 `_policy_logits()` 逐字一致；
    #   ③ **π_ref 冻结**：logits 由 `no_grad` 预算（`_ref_logits`），这里只读不建图。
    if ref is not None:
        ref_beta, ref_logits = ref
        ref_kl_t = ref_kl_term(out["policy"], ref_logits, row_keep=row_keep, temp=policy_temp)
        parts["ref_kl"] = float(ref_kl_t.detach())
        total = total + ref_beta * ref_kl_t

    if ppo is not None:
        logp_old, clip_eps, ent_coef = ppo
        logits_t = _policy_logits(out, policy_temp)
        logp_new = torch.log_softmax(logits_t, dim=-1).gather(
            1, b["label"][:, None]).squeeze(1)
        # `adv_fixed`（手级 GAE 的预计算优势，**已按学生行全局归一化**）优先：
        #   一条样本的优势在整轮里是常量 ⇒ baseline 不再"跟着 critic 动"（§14 P2-①）。
        #   ⚠ 那条路径**不能**再归一化第二次（全局一次 + 批内一次 = 优势尺度被反复钉成 1，
        #   而 `A` 的大小区间本来是有意义的）。
        adv = (adv_fixed if adv_fixed is not None
               else _advantages(out, b, row_keep, key=value_key, norm=adv_norm))
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
        # ---- ★ BL-14：**有界重要性比 + 记账**（⛔ 取代"静默夹一下就走"）-------------------
        # 为什么必须有：`LOG_RATIO_CLAMP` 是**数值**保险（堵 inf/NaN），它同时也是一个**静默**
        # 的 ρ 上限（≈54.6）—— 超限的行被悄悄改掉，台账上一个字都没有。那正是 `#0.5` 事故
        # （ρ≈2e15、一个 step 整网 NaN、照跑完 4 epoch 并落盘废 ckpt）能藏住的原因。
        # 现在：① 每次都用**裁剪前**的 `logp_new − logp_old` 记分布与超限行数；
        #       ② `--rho-max > 0` 时超限 ⇒ 响亮失败（缺省）或显式降级（夹到 ln rho_max）+ 记账。
        if rho_acc is not None:
            with torch.no_grad():
                raw = (logp_new - logp_old).detach()
                sel0 = row_keep > 0 if row_keep is not None else torch.ones_like(raw, dtype=torch.bool)
                r0 = raw[sel0] if bool(sel0.any()) else raw
                rho_acc["n_rows"] = rho_acc.get("n_rows", 0) + int(r0.numel())
                rho_acc["n_gt_clamp"] = rho_acc.get("n_gt_clamp", 0) + int(
                    (r0.abs() > LOG_RATIO_CLAMP).sum())
                if rho_max > 0:
                    lim = math.log(float(rho_max))
                    # ⚠ **双边**判据：ρ > rho_max **或** ρ < 1/rho_max 都算越界（`|log ratio| > ln ρ_max`）。
                    #   为什么必须双边：ρ 的上界与下界是同一件事的两面（`log_ratio` 的绝对值），
                    #   而**极端小**的 ρ 同样会把 `kl_inv`/悲观支带飞。实测踩到的就是**低侧**：
                    #   `|log ratio|max = 23.69` 全部来自 ρ = 5.1e-11（不是 ρ 大）。
                    over = int((r0.abs() > lim).sum())
                    rho_acc["n_gt_max"] = rho_acc.get("n_gt_max", 0) + over
                    if r0.numel():
                        rho_acc["worst_abs"] = max(float(rho_acc.get("worst_abs", 0.0)),
                                                   float(torch.exp(r0.abs().max())))
                        rho_acc["worst_hi"] = max(float(rho_acc.get("worst_hi", 0.0)),
                                                  float(torch.exp(r0.max())))
                        rho_acc["worst_lo"] = min(float(rho_acc.get("worst_lo", float("inf"))),
                                                  float(torch.exp(r0.min())))
                    if over and rho_action == "fail":
                        hi = float(torch.exp(r0.max())) if r0.numel() else float("nan")
                        lo = float(torch.exp(r0.min())) if r0.numel() else float("nan")
                        raise SystemExit(
                            f"⛔ ρ 超过预注册上限 `--rho-max {rho_max:g}`：本批 {over}/"
                            f"{r0.numel()} 行（|logp_new−logp_old| > ln ρ_max = {lim:.4f}，"
                            f"等价于 ρ > {rho_max:g} **或** ρ < {1.0 / rho_max:.6g}），"
                            f"本批 ρ 实际落在 [{lo:.4g}, {hi:.4g}]、"
                            f"最坏双边比值 max(ρ,1/ρ) = "
                            f"{float(torch.exp(r0.abs().max())):.3g}。"
                            f"⛔ 不许静默裁剪：要么查明数据/温度口径，要么显式 "
                            f"`--rho-max-action account` 接受「夹到上限 + 记账」"
                            f"（那会把这几行的替代项改成有偏估计，账要留着）")
                    if over:
                        raw = raw.clamp(-lim, lim)
                        rho_acc["clipped_batches"] = rho_acc.get("clipped_batches", 0) + 1
                if r0.numel():
                    # ⚠ `cap` 要按**已采样的元素个数**算（不是数组个数）：一个 batch 一个数组，
                    #   按个数算会在"每个 batch 都超大"时把采样上限撑破（400k 行的 budget 失效）。
                    cap = RHO_SAMPLE_CAP - sum(int(a.size) for a in rho_acc["samples"])
                    if cap > 0:
                        v = torch.exp(r0[:cap].to("cpu")).numpy().astype(np.float32)
                        rho_acc["samples"].append(v)
                        rho_acc["sample_max"] = max(float(rho_acc.get("sample_max", 0.0)),
                                                    float(v.max()))
                        if os.environ.get("MAHJONG_RHO_DEBUG"):
                            print(f"    [rho-debug] |r0|max={float(r0.abs().max()):.4f} "
                                  f"sampled_n={int(v.size)} v_max={float(v.max()):.6g} "
                                  f"worst={float(rho_acc.get('worst', 0.0)):.6g}")
                if rho_max > 0 and rho_acc.get("n_gt_max", 0) and rho_action == "account":
                    log_ratio = raw.clamp(-LOG_RATIO_CLAMP, LOG_RATIO_CLAMP)
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
                # **双口径 KL**（§14 P2）：`kl` 是 `KL(π_old‖π_new)`（在"老分布采出来的行"上的均值）；
                # `kl_inv` 是它的**反向** `KL(π_new‖π_old) = E_old[ρ·log(π_new/π_old)]`
                # （重要性加权估计）。两者一起看才知道"是策略整体挪了"还是"少数行被放大" ——
                # 单看 `kl` 会被那少数行掩盖（实测两轮里 kl 都 ≲0.07，而 logits 幅度在涨）。
                parts["kl_inv"] = float((ratio[sub].detach() * (logp_new - logp_old)[sub]).mean())
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
    # ★ MV-1 第一刀：**辅助子目标头** `own_win` 的 BCE 项（`aux_own=None` ⇒ 一行都不走）。
    #   为什么值得加：`own_win` 的**合法上限**（R² 0.13939）是现标量 `delta`（0.06643）的 ×2.10、
    #   且是唯一不受天花板压的目标（`lab/nextphase/NEXTPHASE-SCREEN.md`）；而"份额能不能转成
    #   可学增益"只能由闸门答（⛔ 贴标签的读数不是强弱判据）。
    if aux_own is not None:
        logits_own, w_own, rows_own = aux_own[0], float(aux_own[1]), str(aux_own[2])
        per_row_own = F.binary_cross_entropy_with_logits(logits_own, b["own_win"], reduction="none")
        # ⚠ **全部行**（state 级头的规矩）：不加 `reduce_row` 的默认因子 `eff`（掩码 × RWR）。
        l_own = per_row_own.mean()
        parts["own_win"] = float(l_own.detach())
        total = total + w_own * l_own
        if rows_own == "student" and row_keep is not None:
            # 次口径报告：只在学生行上的 BCE（**只入 `parts`，不进 `total`**）
            parts["own_win_student"] = float(reduce_row(per_row_own, row_keep).detach())
    # ★ W4 第一步：**逐候选引擎标签**的模仿项 `w · CE(logits, 引擎最优候选)`。
    #   为什么值得加：`effect`（打后向听/进张）与 `cand.derived`（含听牌形）是**引擎给的稠密标签** ——
    #   无采样噪声、无 critic、不依赖闸门算力；而 RL 的效应（~1 顺位点）落在 ±2.35 的分辨率里看不见。
    #   ⚠ 三条口径写在 `il` 的 docstring 里（只在学生行 / 同一把温度 / 不吃 RWR 权重）。
    #   ⚠ `w == 0` 时 `total` 加的是**精确的 0 张量**（`il_w * l_il` 不是 `0.0` 常量），
    #   但它与"这一项不存在"在本项目的前向里逐位相同（无 dropout/BN，`+0` 不改任何梯度）——
    #   判据是 `--il-weight 0` 与改动前**逐位相同**（见 `selfcheck` / W3 同款做法）。
    if il_tgt is not None:
        logits_il = _policy_logits(out, policy_temp)
        l_il = F.cross_entropy(logits_il, il_tgt, reduction="none")
        # 掩码口径与 `danger` 一致：**只吃掩码、不吃 RWR**（见 docstring ③）
        l_il = reduce_row(l_il, row_keep)
        parts["il_ce"] = float(l_il.detach())
        total = total + il_w * l_il
    if ssl is not None:
        head, mask, target = ssl
        logits = head(out["e_tokens"])                       # [B,K,类型数]
        if bool(mask.any()):
            l_ssl = F.cross_entropy(logits[mask], target[mask])
            parts["ssl"] = float(l_ssl.detach())
            total = total + w.get("ssl", 1.0) * l_ssl
    if ref_kl_t is not None:
        # 锚项**占总损失的比例**（含它自己）—— 与 `ref_kl` 一起打，用来判断"锚是不是白加"：
        # 占比 ≪1% 说明 β 太小、这一项管不住；≫50% 说明它在主导（策略几乎没在动）。
        with torch.no_grad():
            parts["ref_kl_share"] = float((ref_beta * ref_kl_t).abs()
                                         / total.detach().abs().clamp_min(1e-12))
    return total, parts


@torch.no_grad()
def evaluate(model: M.V4Model, data: dict, device: str, batch: int = 512,
             ssl_head: MaskedEventHead | None = None, mask_frac: float = 0.0,
             row_keep=None, value_key: str = "value",
             ablate: dict[str, list[tuple[int, int]]] | None = None,
             extra: dict[str, np.ndarray] | None = None,
             rows: np.ndarray | None = None,
             il_target: np.ndarray | None = None) -> dict:
    """val：教师动作一致率（总/按类型）+ 首合法基线 + 各头损失 + SSL 掩码重建准确率
    + **引擎最优候选一致率**（W4 的低噪判据）。

    @param row_keep 只在**这些行**上统计一致率（自对弈数据里 = 学生那一代的行；
        不给就统计全部行）。⚠ 损失仍在全部行上算（那是"这一局的局面"的损失，与谁动的手无关）。
    @param ablate **块消融**（消融矩阵）：val 必须与 train 用同一份消融输入，否则表读不出来
    @param extra 额外逐行数组（键名 = batch 键）：`gae-hand` 的 `vtarget` 走它 —— 否则 val 的
        `compute_loss` 会因为取不到 `vtarget` 直接 KeyError
    @param rows 只在**这个行子集**上评（`--val-every` 的廉价读数用它；缺省 = 全部行）。
        ⚠ 行子集必须是**固定的那一份**：每次读数换样本的话，两次之间的差里混着采样噪声，
        "val CE 有没有抬头"就判不出来了（早停会随机早停或永不早停）。
    @param il_target `[n]` 逐行**引擎最优候选下标**（`dataset.engine_targets`）。给了就同时报
        `engine_top1`（argmax(logits) == 引擎最优）与 `engine_random`（**随机基线** `1/nlegal` 的
        均值）——后者是"这个一致率在多少之上才算学到东西"的参照。⚠ 它与 `row_keep` 一起用时
        就是"**行为策略基线**"（`train()` 在开训前用 `--init` 那一份网读一次）。
    """
    model.eval()
    n_all = int(data["nlegal"].shape[0])
    rows_arr = None if rows is None else np.asarray(rows, dtype=np.int64)
    n = n_all if rows_arr is None else int(rows_arr.size)
    types = data["meta"].get("action_types", [])
    hit = tot = 0
    by_type: dict[str, list[int]] = {}
    first_legal = 0
    eng_hit = eng_tot = 0
    eng_rand = 0.0
    loss_sum = 0.0
    parts_sum: dict[str, float] = {}
    ssl_hit = ssl_n = 0
    gen = torch.Generator(device="cpu").manual_seed(12345)          # val 掩码固定 ⇒ 同种子可比
    il_arr = None if il_target is None else np.asarray(il_target, dtype=np.int64)
    for i0 in range(0, n, batch):
        end = min(i0 + batch, n)
        # `rows` 给定时按**行子集**取（下标仍是原表的行号：`_batch` / `row_keep` / `extra` 都按它对齐）
        idx = np.arange(i0, end) if rows_arr is None else rows_arr[i0:end]
        b = _batch(data, idx, device, ablate=ablate)
        if extra:
            for k, arr in extra.items():
                b[k] = torch.from_numpy(np.asarray(arr[idx], dtype=np.float32)).to(device)
        il_b = None
        if il_arr is not None:
            il_b = torch.from_numpy(il_arr[idx]).to(device)
            b["il_target"] = il_b
        ssl = None
        if ssl_head is not None and mask_frac > 0:
            evt_masked, mask_rows, target = mask_events(b["evt"], mask_frac, gen)
            b["evt"] = evt_masked
            ssl = (ssl_head, mask_rows, target)
        out = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"], h=b.get("h"),
                    n_legal=b["nlegal"])
        # ⚠ 掩码要喂进损失：自对弈数据集里 `random` 那 1/4 行的动作在训练过的网看来**几乎是零概率**
        #   （实测逐行 CE 中位 0.05 / 90 分位 24 / 最大 672）—— 不遮的话"策略 CE"这个指标会被它带跑，
        #   而 PPO 的 log 比在那些行上也没有意义（策略损失本来就只算学生行）
        kb_e = None if row_keep is None else torch.from_numpy(
            np.asarray(row_keep[idx], dtype=np.float32)).to(device)
        # ⚠ IL 项在这里**权重给 0**：val 只拿它的读数（`loss_il_ce` = 对引擎标签的 CE），
        #   不让它进 val 的 loss —— 否则"val loss"就成了训练目标的函数，与历史读数不可比。
        loss, parts = compute_loss(out, b, ssl=ssl, row_keep=kb_e, value_key=value_key,
                                   il=(None if il_b is None else (0.0, il_b)))
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
        # ★ W4：**引擎最优候选一致率** + 随机基线（都按同一个 `sel` 取行，与 top1 同口径）
        if il_b is not None:
            nleg_b = b["nlegal"].to(pred.device)
            eng_hit += int((take(pred) == take(il_b)).sum())
            eng_tot += int(take(il_b).numel())
            eng_rand += float((1.0 / nleg_b.clamp_min(1).float())[sel].sum()) if sel is not None \
                else float((1.0 / nleg_b.clamp_min(1).float()).sum())
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
    if eng_tot:
        out["engine_top1"] = eng_hit / eng_tot
        out["engine_random"] = eng_rand / eng_tot
        out["engine_n"] = eng_tot
    if ssl_n:
        out["ssl_acc"] = ssl_hit / ssl_n
        out["ssl_n"] = ssl_n
    return out


#: 验证集早停的**改善容忍**：`val CE` 要比历史最好值低**至少**这个量才算"还在学"。
#: 为什么不是"严格小于"：CE 在第 3 位小数上的抖动没有语义（尤其子集只有几百行），
#: 用严格小于会让"平着走"也被算成改善 ⇒ 早停永远不触发（防过拟合那件事就没了）。
CE_IMPROVE_TOL = 1e-4


def ce_probe(model: M.V4Model, data: dict, device: str, *, rows: np.ndarray, batch: int,
             ssl_head: MaskedEventHead | None = None, mask_frac: float = 0.0,
             value_key: str = "value", ablate: dict[str, list[tuple[int, int]]] | None = None,
             extra: dict[str, np.ndarray] | None = None, metric: str = "policy",
             il_target: np.ndarray | None = None) -> float:
    """**廉价 CE 读数**（`--val-every` 用）：在**固定行子集**上跑现成的 `evaluate()`，取 `loss_<metric>`。

    为什么不另写一份指标：这个数必须与 epoch 行上打的 `policy {val_loss_policy}` **同一个口径**
    （都是 `evaluate()` 的逐头损失），否则"train CE / val CE 两条线"没法与 epoch 行对照 ——
    本仓库在"两把尺子量同一个东西"上踩过坑（`std(A)` 那两次量纲事故）。

    @param metric 读哪个头的 CE：`policy`（缺省 = 行为标签的 CE，与 W3 逐位一致）/ `il`
        （**引擎标签**的 CE，对应 `compute_loss` 里的 `parts["il_ce"]`）。⚠ 加了 `--il-weight > 0`
        之后，**要优化的目标已经不是行为 CE 了**：
    实测（见 `docs/TRAINING-V4.md` §14.12）行为 CE 从 0.512 抬到 0.62（那是**必然**的 ——
    引擎标签与行为标签本来就只有 ~57% 重合），于是按行为 CE 判"没改善"会在 step 250~300 就把
    这一轮掐掉，而那一刻**引擎 CE 还在降**。⇒ 用 `--il-weight` 时必须把护栏切到 `il`
    （缺省仍是 `policy`，所以不给这个开关时逐位等于今天）。

    ⚠ 调用方负责**把 `model` 放回 `train()`**（`evaluate()` 会 `model.eval()`）：见 `train()` 里的
    `model.train(); ssl_head.train()`。本仓库的前向无 dropout/batchnorm，所以这一条今天不影响数值 ——
    但"读一次指标就把训练模式改了"是那种**以后加个 dropout 才会咬人**的静默 bug，不留给下一个人。
    """
    ev = evaluate(model, data, device, batch=batch, ssl_head=ssl_head, mask_frac=mask_frac,
                  value_key=value_key, ablate=ablate, extra=extra, rows=rows,
                  il_target=il_target)
    # ⚠ 头名 → 损失键名：`il` 在 `compute_loss` 里叫 `il_ce`（`loss_il_ce`）—— 两处必须对上，
    #   否则 `--val-metric il` 会在第一次读数时炸（实测踩到，报"这个读数不存在"）。
    part = "il_ce" if metric == "il" else metric
    key = f"loss_{part}"
    if key not in ev:
        raise SystemExit(f"`--val-metric {metric}` 要的那个读数（`{key}`）这一轮不存在 —— "
                         f"可用的是 {sorted(k[5:] for k in ev if k.startswith('loss_'))}"
                         f"（`il` 需要 `--il-weight` 那一项真的在算：缺 `il_target` 就没有它）")
    return float(ev[key])


def probe_rows(n: int, want: int, seed: int = 0) -> np.ndarray:
    """固定行子集（纯函数）：`n` 行里按**固定种子**抽 `want` 行并**升序**返回。

    · 固定种子 ⇒ 每次读数都看同一批行（否则两次之间的差里混着采样噪声，早停判不出来）；
    · **升序** ⇒ `_batch` 的 memmap 花式索引按升序读（与 v3 的 `bc._batch` 同规矩，避开随机读放大）。
    """
    if want >= n:
        return np.arange(n, dtype=np.int64)
    return np.sort(np.random.default_rng(seed).permutation(n)[:max(1, int(want))]).astype(np.int64)


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


def _own_win_sd(own_win_head) -> dict:
    """★ MV-1：辅助头 `own_win` 的 `state_dict()`（**关着时返回空字典**）。

    为什么要有这个中转：`_state_sha256(*sds)` 按 `sorted(sd)` 迭代 ⇒ 传 `None` 会当场炸。
    而"缺省关 ⇒ 与今天逐字节相同"要求**空字典不参与 sha、也不进 ckpt**
    （⚠ 写错成"`None` 也进 sha"⇒ `base` 臂的指纹会变，负向对照当场假红）。
    """
    return {} if own_win_head is None else own_win_head.state_dict()


def save_checkpoint(path, model, ssl_head, meta, weights, extra: dict,
                    own_win_head=None) -> None:
    ck = {"model": model.state_dict(),
          "ssl_head": ssl_head.state_dict(),
          # ⚠ `arch` 与 `dims` 一起落盘：A− 的张量集与 v4 不同，加载时必须建对塔
          #   （缺省 v4 ⇒ 旧 ckpt 逐字节/逐行为不变）。
          "config": {"model": "v4", "arch": getattr(model, "arch", M.ARCH_V4),
                     "params": model.param_count(), "dims": model.dims()},
          "dataset_meta": meta,
          "feature_version": spec.FEATURE_VERSION_V4,
          "blocks_fingerprint": spec.fingerprint(),
          "loss_weights": weights,
          **extra}
    # ★ MV-1：辅助头 `own_win` 只在开关开时落盘 —— 关着时**这个键根本不存在**
    #   （不是"存在且为 None"）⇒ 缺省路径的 ckpt 与加这个功能之前逐字节相同。
    if own_win_head is not None:
        ck["own_win_head"] = own_win_head.state_dict()
    torch.save(ck, path)


# ================================================================ ★ BL-04：优化器状态续跑
# 为什么需要它（2026-10-12）：`pretrain` 的 **PPO 口径闸门**要求"初始化时 KL(π_old‖π_new) ≈ 0"，
# 它守的是"`logp_old` 必须来自数据生成策略"这件事。于是**跨片/跨轮链式续训必然撞墙**：
# 第 N₁ 步的存档已经**不是**数据生成策略了，拿它当 `--init` 会被闸门当场 `SystemExit`
# （退出码 1、无 traceback）。⇒ 要按预注册跑"冻结集上的多段/少轮长训练"，就得能把
# **除权重之外**的那部分状态也存下来并接着走：
#   · Adam 的动量/方差（缺了它 = 从零动量重新加速，**是另一条轨迹**）；
#   · 全局步数与轮次（阶段划分 / 余弦降 lr / KL 早停的 `min_steps` 都看它）；
#   · 四路 RNG（torch / numpy / python / 掩码生成器）+ **数据顺序指纹**；
#   · 段的边界状态（当前阶段、epoch 内累加器、验证集早停的历史）。
# 判据（`docs/TRAINING-V4.md` §17）：**续跑 == 一口气跑（逐位）**；而"故意不加载优化器状态"
# **必须不同**（否则就是假绿）。
#
#: 续跑存档的格式号 —— 改了字段**语义**就 +1 ⇒ 老存档**当场被拒**（⛔ 不静默按新语义读）
#:  · 2 → 3（2026-10-13，第八十轮）：`behaviour.kl0` 恢复"**链条起点**"语义 + 新增
#:    `behaviour.kl_segment`（**本段边界**的漂移量，只记账 + 超 KL 早停阈值时告警）。
#:    为什么必须 +1：格式 2 的存档里 `kl0` 被**续跑那一段覆写**成了本段边界的漂移量
#:    （`kl0 := assert_resume_behaviour(...)` 的返回值），于是"起点 ≈0"那条硬拒判据
#:    实际变成了"段边界漂移 ≤1e-2" —— MSLT 配方实测段 2 起来 `kl0=0.0331`（= 段 1 的
#:    `kl_probe`）⇒ **段 3 被误拒**、"合规链最多 2 段"。格式 2 的 `kl0` 分不清是哪一个量
#:    ⇒ 按新语义静默读会把一个错值当成起点判据（正是这条闸门要防的）。
#:  · 1 → 2（2026-10-13，第七十九轮）：新增 `ref` 段（**KL 锚 π_ref 的来源 + 权重指纹**）。
#:    为什么必须 +1：格式 1 的存档**压根没记"锚是谁"**，而 `--ref-beta > 0` 且没给 `--ref` 时锚的缺省
#:    是"**这一段的** `--init`" ⇒ 续跑那一段的 `--init` 是上一段的存档 ⇒ 锚**静默换成了上一段的权重**。
#:    后果：`--ref-beta > 0` 的配方上"续跑 == 一口气跑"**必然红**（实测 MSLT 配方 10+10 ≠ 20），
#:    而日志里只有一行不起眼的路径。老存档继续按格式 1 读 = 把这个静默洞带下去。
RESUME_FORMAT = 3

#: 续跑状态在 `model.pt` 里的键名（与 `model` / `ssl_head` / `config` 同级）
RESUME_KEY = "resume"


def _sha256_file(p) -> str:
    """文件指纹（**行为策略标识**用：`sha256(net.bin)`）—— 逐字节判"是不是同一份网"。"""
    h = hashlib.sha256()
    with Path(p).open("rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _state_sha256(*sds) -> str:
    """权重指纹：按**参数名排序**逐张量取原始字节（与 `torch.save` 的容器格式无关）。

    ⚠ 传进来的每一份 `state_dict` 都参与指纹（MV-1 的 `own_win` 只在开关开时被传进来；
    **关着时必须不传**（用 `_own_win_sd()` 得到的空字典直接跳过）—— 否则 `base` 臂的
    指纹会与今天不同，负向对照就成了假红。
    """
    h = hashlib.sha256()
    for sd in sds:
        if not sd:                                     # 空字典 ⇒ 不参与（`None` 由调用方过滤）
            continue
        for name in sorted(sd):
            t = sd[name].detach().to("cpu")
            h.update(name.encode("utf-8"))
            h.update(np.ascontiguousarray(t.numpy()).tobytes())
    return h.hexdigest()


def _perm_sha256(perm) -> str:
    """**数据顺序**指纹（某个 epoch 的洗牌结果）——续跑时逐字节核对，⛔ 不靠"种子一样所以一样"。"""
    return hashlib.sha256(np.ascontiguousarray(perm, dtype=np.int64).tobytes()).hexdigest()


def _rng_fingerprint(mask_gen: torch.Generator) -> dict:
    """四路 RNG 状态：torch 全局 / numpy 全局 / python `random` / 掩码用的**独立生成器**。"""
    return {"torch": torch.get_rng_state(), "numpy": np.random.get_state(),
            "python": random.getstate(), "mask_gen": mask_gen.get_state()}


def _restore_rng(state: dict, mask_gen: torch.Generator) -> None:
    torch.set_rng_state(state["torch"])
    np.random.set_state(state["numpy"])
    random.setstate(state["python"])
    mask_gen.set_state(state["mask_gen"])


def _behaviour_record(spec_str: str, meta: dict) -> dict:
    """**行为策略标识**：`sha256(权重文件)` + 策略串（数据集 meta 的 `student`）+ 温度占位。

    ⚠ 为什么不能只记路径：路径可以指向**被换过的**文件，而"是不是同一份网"必须由**字节**判
    （同族假绿 = "两文件都缺 ⇒ 哈希都空 ⇒ 判成相同"）。
    """
    p = resolve_weight_path(spec_str)
    return {"spec": str(spec_str), "sha256": _sha256_file(p),
            "policy": str((meta or {}).get("student") or ""),
            "temp": None, "kl0": None, "kl_segment": None, "kl_probe": None, "probe_n": None}


def _resume_recipe(args, *, resolved_data: str, n: int, steps: int, total_steps: int) -> dict:
    """**口径/日程指纹**：续跑时逐项核对 —— 只要有一项不同，接着跑出来的就不是同一条轨迹。"""
    g = getattr
    return {
        "data": resolved_data, "n": int(n), "steps": int(steps), "total_steps": int(total_steps),
        "epochs": int(g(args, "epochs", 0)), "batch": int(g(args, "batch", 0)),
        "seed": int(g(args, "seed", 0)),
        "lr": float(g(args, "lr", 0.0)), "head_lr_mult": float(g(args, "head_lr_mult", 3.0)),
        "stage_a": float(g(args, "stage_a", 0.25)), "stage_b": float(g(args, "stage_b", 0.35)),
        "stage_c_lr_mult": float(g(args, "stage_c_lr_mult", 1.0)),
        "mask_frac": float(g(args, "mask_frac", 0.0)),
        "ssl_weight": float(g(args, "ssl_weight", 0.0)),
        "grad_clip": float(g(args, "grad_clip", 0.5)),
        "kl_early_stop": float(g(args, "kl_early_stop", 0.03)),
        "kl_min_steps": int(g(args, "kl_min_steps", 100)),
        "objective": str(g(args, "objective", "bc") or "bc"),
        "value_target": str(g(args, "value_target", "final") or "final"),
        "advantage": str(g(args, "advantage", "auto") or "auto"),
        "only_heads": str(g(args, "only_heads", "") or ""),
        "drop_heads": str(g(args, "drop_heads", "") or ""),
        "freeze_trunk": bool(g(args, "freeze_trunk", False)),
        "ablate_blocks": str(g(args, "ablate_blocks", "") or ""),
        "ref_beta": float(g(args, "ref_beta", 0.0)), "il_weight": float(g(args, "il_weight", 0.0)),
        "rank_weight": float(g(args, "rank_weight", 0.0)),
        "rwr_beta": float(g(args, "rwr_beta", 0.0)),
        "val_every": int(g(args, "val_every", 0)), "val_patience": int(g(args, "val_patience", 3)),
        "val_metric": str(g(args, "val_metric", "policy") or "policy"),
        "gae_lambda": float(g(args, "gae_lambda", 0.9)), "gae_gamma": float(g(args, "gae_gamma", 1.0)),
        "baseline_fit": str(g(args, "baseline_fit", "scale") or "scale"),
        "feature_version": int(spec.FEATURE_VERSION_V4), "blocks_fingerprint": spec.fingerprint(),
    }


def _recorded_args(args) -> dict:
    """台账里的 `args`：**新开关在缺省值时一个键都不进**。

    这是"缺省关 ⇒ 旧路径**逐字节**不变"的实现方式（`model.pt` 与 `metrics.json` 都含这份 `args`）：
    与仓库里"只在非缺省时才进命令行"同一个做法，只是这里的落点是台账。
    """
    d = dict(vars(args))
    for k, default in (("save_optimizer", False), ("resume_optimizer", None),
                       ("stop_after_steps", 0),
                       # ★ MV-1：新开关在缺省值时一个键都不进（与上面三条同一个做法）
                       ("aux_own_win_weight", 0.0), ("aux_own_win_rows", "all"),
                       # ★ MV-1 第九十九轮：lr 调度（缺省 = 现行 cosine ⇒ 台账里一个键都不进）
                       ("lr_schedule", "cosine"), ("lr_floor_frac", 0.1),
                       # ★ C2（第一百轮）：池化口径（缺省 = mask-aware ⇒ 台账里一个键都不进，
                       #   于是"旧路径逐字节不变"不依赖任何新参数）
                       ("legacy_pool", False),
                       # ★ C1（第一百零一轮）：`fusion.out` 末端 ReLU（缺省 = 去掉 ⇒ 台账里一个键都不进）
                       ("legacy_out_relu", False),
                       # ★ C3（第一百零二轮）：门控初始化（缺省 = 小非零 ⇒ 台账里一个键都不进）
                       ("legacy_zero_init", False)):
        if d.get(k) == default:
            d.pop(k, None)
    # ⚠ 程序化调用（自检/脚本的 `Namespace(**{...})`）可能给 `Path`，而 CLI 永远给 str：
    #   台账要落 `metrics.json`（JSON）⇒ 这里统一转 str（否则会在最后写文件那一步才 TypeError）。
    return {k: (str(v) if isinstance(v, Path) else v) for k, v in d.items()}


def load_resume_state(spec_str: str) -> dict:
    """读**续跑存档**（`model.pt` 里的 `resume` 段）—— 缺段 / 格式号不对**当场报错**（fail-closed）。

    返回整份 checkpoint（顶层 `model` / `ssl_head` 也在里面：没给 `--init` 时就用它载权重）。
    """
    p = resolve_weight_path(spec_str)
    ck = torch.load(p, map_location="cpu", weights_only=False)
    if not isinstance(ck, dict) or RESUME_KEY not in ck:
        raise SystemExit(
            f"这份 checkpoint 里没有优化器状态（`{RESUME_KEY}` 段）：{p} —— 落盘时要用 "
            f"`--save-optimizer` 才写它。⛔ 缺了还「接着跑」只会得到一条**从零动量开始**的"
            f"另一条轨迹，正是这个开关要防的假绿")
    res = ck[RESUME_KEY]
    fmt = int(res.get("format", 0))
    if fmt != RESUME_FORMAT:
        raise SystemExit(f"续跑存档格式号 {fmt} ≠ 本版 {RESUME_FORMAT}（{p}）—— "
                         f"字段语义变过就**重新起一段**，⛔ 不按新语义读老存档")
    return {"path": str(p), "model": ck.get("model"), "ssl_head": ck.get("ssl_head"),
            "own_win_head": ck.get("own_win_head"),
            "resume": res}


def assert_resume_behaviour(resume: dict, beh_spec: str, temp: float, model: M.V4Model,
                            data: dict, meta: dict, logp_old: torch.Tensor, idx,
                            device: str) -> tuple[float | None, float | None]:
    """**续跑口径闸门**：替代"初始化时 KL(π_old‖π_new) ≈ 0"那把在续跑路径上**必然红**的尺子。

    为什么需要换一把尺子：续跑时 π_new **本来就不等于** π_old（已经走过 N₁ 步）。但闸门想守的
    那件事仍然成立且**必须被守住** —— "`logp_old` 与数据生成策略是同一个分布"。等价的三把尺子
    （缺一即 `SystemExit`，退出码 1、无 traceback）：

      ① **行为策略标识**：`sha256(权重文件)` + 温度 + 策略串必须与存档**逐项相同**
         （`--behaviour` 换一份网 ⇒ 当场拒；那是换了一个分布，静默把 ρ 带偏）；
      ② **链条起点口径**：存档记的 `kl0`（**链条起点**那一次闸门的读数）必须有限且 ≤1e-2
         —— ⛔ 不许拿一条"起点就不干净"的链继续跑；
      ③ **连续性**：同一条**固定探针行**上的 KL 必须与存档记录的 `kl_probe` 一致
         —— 权重 / 行为策略 / 温度任一处不同都会当场露出来。

    ⚠★ 第八十轮（**口径分家**）：**本段边界**的行为 KL **不是**口径判据，它只是**漂移记账**
    （`kl_segment`）。为什么必须分开：那条 1e-2 判据守的是"**起点**那一轮 `--init` 与
    `--behaviour` 是同一份网"，而"在自己数据上多训几轮 ⇒ 现任离数据生成策略越来越远"是
    **训练的正常后果**，不是口径不一致。实测：MSLT 配方段 1 尾部行为 KL 已经 **0.0331**（很正常），
    旧实现把它当成 `kl0` 记进存档 ⇒ 段 3 被误拒、结论变成"合规链最多 2 段"。
    ⇒ **返回 `(kl_start, kl_segment)`**：前者进 `behaviour.kl0`（起点语义，逐段沿用），
    后者进 `behaviour.kl_segment`（本段边界，只记账 + 超过 KL 早停阈值时告警）。
    """
    p = resume["path"]
    rec = resume["resume"].get("behaviour")
    if rec is None:
        raise SystemExit(
            f"⛔ 续跑需要**行为策略标识**，但这份存档没有记（{p}）—— 证明不了 `--behaviour` 与"
            f"数据生成策略是同一份 ⇒ 按 fail-closed 拒跑（⛔ 不许把「没记」当成「对上了」）")
    got_sha = _sha256_file(resolve_weight_path(beh_spec))
    diffs: list[str] = []
    if got_sha != str(rec.get("sha256")):
        diffs.append(f"权重 sha256 {str(rec.get('sha256'))[:16]}… vs {got_sha[:16]}…")
    if rec.get("temp") is not None and abs(float(rec["temp"]) - float(temp)) > 1e-9:
        diffs.append(f"温度 {rec['temp']:g} vs {temp:g}")
    got_pol = str((meta or {}).get("student") or "")
    if str(rec.get("policy") or "") and str(rec["policy"]) != got_pol:
        diffs.append(f"策略串 {rec['policy']!r} vs {got_pol!r}")
    if diffs:
        raise SystemExit(
            f"⛔ 续跑的行为策略与存档**不是同一份**（{p}）：" + "；".join(diffs)
            + " —— `logp_old` 必须来自**数据生成策略**，换一份网就是换一个分布")
    kl0 = rec.get("kl0")
    if kl0 is None or not math.isfinite(float(kl0)) or abs(float(kl0)) > 1e-2:
        raise SystemExit(
            f"⛔ 这条链的**起点口径**不干净：存档记的 KL(π_old‖π_new)={kl0}（应为 ≈0，阈值 1e-2）"
            f"—— 起点那一轮 `--init` 与 `--behaviour` 就不是同一份，续跑只会放大它")
    kl_now = _behaviour_kl(model, data, idx, logp_old, temp, device)
    print(f"PPO 续跑口径：行为策略 {beh_spec}（sha256 {got_sha[:16]}… == 存档）· 温度 {temp:g}"
          f" · 链条起点 KL={float(kl0):+.2e}（≈0 = 起点那一轮口径对上了）")
    want = rec.get("kl_probe")
    if want is None:
        print(f"PPO 续跑口径：连续性探针 KL={kl_now:+.6e}（存档没记探针 ⇒ 只查标识与起点）")
        return float(kl0), kl_now
    print(f"PPO 续跑口径：连续性探针（固定 {int(idx.size)} 行）KL={kl_now:+.6e}"
          f" vs 存档 {float(want):+.6e}")
    if abs(kl_now - float(want)) > 1e-9:
        raise SystemExit(
            f"⛔ 续跑的**连续性探针**与存档不一致：本次 {kl_now:+.9e} vs 存档 {float(want):+.9e}"
            f"（同一份权重 + 同一份行为策略 + 同一温度在同一条探针行上必须给出**同一个数**）")
    return float(kl0), kl_now


def train(args) -> dict:
    paths.ensure_root()
    data_dir = Path(args.data)
    train_data = v4ds.load_split(data_dir, "train")
    val_data = v4ds.load_split(data_dir, "val")
    meta = train_data["meta"]
    train_data["meta"] = meta                        # `evaluate` 要 action_types
    val_data["meta"] = meta

    # ---- ★ C2（第一百轮）：state 级头 = **mask-aware 池化**（只对 `n_legal` 个合法槽求均值）----
    # `--legacy-pool`（缺省关）：开 = 旧的 `u.mean(dim=1)`（分母 Lmax，含 padding），**逐位复现**改动前。
    # ⚠ **进程级**开关：这一次训练里建的每张 V4Model（主网 / `--init` / `π_ref` / 行为策略）都同口径 ——
    #   只设主网会让参照网的口径不同，旧轨迹就复现不出来。
    _legacy_pool = bool(getattr(args, "legacy_pool", False))
    M.set_legacy_pool(_legacy_pool)
    print("state 级头池化：" + (
        "**legacy**（`u.mean(1)`，分母 = Lmax，含 padding 槽；⛔ 只用于逐位复现改动前）"
        if _legacy_pool else
        "mask-aware（只对真实 `n_legal` 个合法槽求均值；与 Java/C++ 前向同一分母）"))
    # ---- ★ C1（第一百零一轮）：`fusion.out` 末端 **去掉 ReLU**（0 参数，只这一处）----------------
    # `--legacy-out-relu`（缺省关）：开 = 保留旧的 `nn.ReLU()` 末端，**逐位复现**改动前。
    # ⚠ 同样进程级：主网 / `--init` / `π_ref` / 行为策略必须同一套前向语义，否则参照网口径不同。
    _legacy_out_relu = bool(getattr(args, "legacy_out_relu", False))
    M.set_legacy_out_relu(_legacy_out_relu)
    print("fusion.out 末端：" + (
        "**legacy ReLU**（`u ≥ 0`，候选表示被截断；⛔ 只用于逐位复现改动前）"
        if _legacy_out_relu else
        "**无 ReLU**（C1：`u` 可正可负，候选表示不再被截断）"))
    # ---- ★ C3（第一百零二轮）：`fusion.mem` / `heads.policy_gate` 的**初始化**（0 参数）--------
    # `--legacy-zero-init`（缺省关）：开 = 两块**全零**初始化（门控恒等、`h_evt` 被乘 0），逐位复现。
    # ⚠ 只影响**冷启**的网：`--init` 载入的权重（含这两块）会被文件值**原样覆盖** ⇒ warm-start 逐位不变。
    # ⚠ 不进 π_ref/V_old 缓存键：初始化不改变任何前向语义（缓存存的是 `--init` 网的前向结果）。
    _legacy_zero_init = bool(getattr(args, "legacy_zero_init", False))
    M.set_legacy_zero_init(_legacy_zero_init)
    print("门控初始化（mem/policy_gate）：" + (
        "**legacy 全零**（`g ≡ 1`、`h_evt` 零影响；⛔ 只用于逐位复现改动前）"
        if _legacy_zero_init else
        f"**小非零** `N(0, {M.ZERO_INIT_STD:g}²)` + bias 0（C3；0 参数，net.bin 格式不变）"))
    # ---- ★ B3（第一百零四轮）：`u` 的 LayerNorm + 零初始化逐维残差门（+576 参数）------------
    # `--legacy-u-res`（缺省关）：开 = **不建**这段残差（连 LN 都不算）⇒ 逐位复现改动前。
    # ⚠ 同样进程级（主网 / `--init` / `π_ref` / 行为策略同一前向图）；⚠ 会改 policy logits ⇒ 进缓存键。
    _legacy_u_res = bool(getattr(args, "legacy_u_res", False))
    M.set_legacy_u_res(_legacy_u_res)
    print("u 的尺度约束（B3）：" + (
        "**legacy**（无 LN / 无残差门；⛔ 只用来自证与逐位复现改动前）"
        if _legacy_u_res else
        "**LN(192) + 零初始化逐维残差门**（`u_res=0` ⇒ 出生恒等；旧包缺张量 ⇒ 补 (1,0)/0）"))
    # ---- ★ #0（第一百零六轮）：`TileTower` 的 34 行身份嵌入（+1,632 参数）-------------------
    # `--legacy-tile-id`（缺省关）：开 = **不建** `tile.tile_id`（`net.bin` 逐字节复旧）；
    # 缺省 = 建（小非零初始化 ⇒ 出生即可区分 34 个牌种；旧包缺张量 ⇒ 补 0 ⇒ 逐位不变）。
    # ⚠ 同样进程级（主网 / `--init` / `π_ref` / 行为策略同一前向图）；⚠ 会改 policy logits ⇒ 进缓存键。
    _legacy_tile_id = bool(getattr(args, "legacy_tile_id", False))
    M.set_legacy_tile_id(_legacy_tile_id)
    print("#0 tile 身份嵌入：" + (
        "**legacy**（不建 `tile.tile_id`；34 行不可区分；⛔ 只用于自证/逐位复现）"
        if _legacy_tile_id else
        f"**34×{M.C_TILE} 行身份嵌入**（+{34 * M.C_TILE} 参数；`N(0, {M.TILE_ID_STD:g}²)` 初始化）"))
    # ---- ★ A−（T1，段 1）：`--arch {v4|a-}` —— 缺省 v4 = **现有图，逐位不变** ------------------
    # `a-` = 34 轴卷积/轴向注意力 + 事件 joint attention（95 token，`h_evt` 作第 95 个 token）
    #       + 全塔 pre-LN/残差/GELU（erf）+ value 81 bin（±60）+ heads d=128。
    # ⚠ 进程级：主网 / `--init` / `π_ref` / 行为策略必须同一张图（张量集不同就不能互载）。
    # ⚠ `a-` 与 v4 **不能互相 warm-start** ⇒ a- 臂只能冷启；`--behaviour` 仍可指 v4 现任网
    #   （"数据由谁生成"没变），但那样 PPO 的口径闸门 `KL(π_old‖π_new)≈0` 必然红 ⇒ 本批 a- 走 BC。
    # ⚠ 三端（Java/C++）**未同步** ⇒ a- 的网**不得**上线/上闸门。
    _arch = str(getattr(args, "arch", M.ARCH_V4) or M.ARCH_V4)
    M.set_arch(_arch)
    print("网络图 arch：" + (
        "**v4（现有图，逐位不变）**" if _arch == M.ARCH_V4 else
        f"**a-（T1 新图）**：tile 34 轴卷积+轴向注意力（{M.A_TILE_D}→{M.A_D_MODEL}）· "
        f"evt GRU+2×conv · fusion 95-token joint attention · value {M.A_VALUE_BINS} bin"
        f"（±{M.A_VALUE_RANGE:g}）· heads d={M.A_D_MODEL} · 全塔 pre-LN/残差/GELU(erf)"))
    if _arch != M.ARCH_V4 and any((_legacy_pool, _legacy_out_relu, _legacy_zero_init,
                                   _legacy_u_res, _legacy_tile_id)):
        print("  ⚠ 这些 legacy 开关只作用于 v4 塔（a- 恒为 GELU/pre-LN）；`--legacy-tile-id` 仍生效")
    # fail-closed：**没有真实 `n_legal` 就拒绝起跑** —— ⛔ 不许悄悄退回全槽均值（那正是 C2 要消灭的
    # 训练/上线两套统计量）。缺列 / 有 ≤0 的行 / 超过候选宽度 都在这里拦住。
    for _split, _data in (("train", train_data), ("val", val_data)):
        _nl = _data.get("nlegal")
        if _nl is None:
            raise SystemExit(
                f"⛔ 数据集 `{_split}` 切分没有 `nlegal` 列 ⇒ C2 的 mask-aware 池化拿不到真实 n，"
                f"拒绝起跑（⛔ 不退回全槽均值）")
        _nl = np.asarray(_nl)
        _lmax = int(_data["cand"].shape[1])
        if _nl.size == 0 or int(_nl.min()) <= 0 or int(_nl.max()) > _lmax:
            raise SystemExit(
                f"⛔ 数据集 `{_split}` 切分的 `nlegal` 越界：必须在 [1, Lmax={_lmax}]，实测 "
                f"min={int(_nl.min()) if _nl.size else 'n/a'} "
                f"max={int(_nl.max()) if _nl.size else 'n/a'} ⇒ 拒绝起跑（⛔ 不退回全槽均值）")

    # ---- ★ W1b 硬闸门：训练必须有"长程 carry"（`h0` 列）------------------------------------
    # 为什么是硬闸门：上线端（Java/C++）喂的是"**从 0 起逐事件推进整手**"的 carry，而紧凑集的
    # `evt` 只有最近 K=60 条事件 —— 没有 `h0` 就只能"窗口冷启动"，**两边不是同一个量**
    # （模型看到的记忆长度都不一样）。而这类不一致**不会报错**：训练照跑、指标照出，
    # 只是上线后的分布对不上。⇒ 缺 `h0` 一律**报错退出**，除非显式 `--no-carry` 承担后果。
    # ⚠ 三态（`--no-carry` 的**存在性**本身就是信息，别把它写成简单的 `getattr(..., False)`）：
    #   · True  = 调用方显式承担（打醒目告警后继续）；
    #   · False = **CLI 的缺省值**（`main()` 里 `action="store_true"`）⇒ **硬拒**；
    #   · 缺失  = 程序化调用（自检/临时脚本直接 `Namespace(**{...})`，压根走不到 CLI 的旗标）
    #             ⇒ 告警后继续。这类调用里的紧凑集多半是**没有 net.bin 的小夹具**
    #             （`python/selfcheck.py` 的玩具数据集就是），硬拒只会让整个夹具跑不起来。
    no_carry = getattr(args, "no_carry", None)
    has_h0 = train_data.get("h0") is not None
    if not has_h0:
        msg = ("这一份紧凑集没有 `h0` 列 ⇒ 训练会用**窗口冷启动**，而上线端用**整手 carry** "
               "⇒ 两边不是同一个量；重建数据集（`dataset build --student net:<...>` —— 它会用"
               "**学生网**重放整手事件算 `h0`）或显式 `--no-carry` 承担后果")
        if no_carry is False:
            raise SystemExit("⛔ " + msg)
        print("⚠⚠ " + msg)
    if val_data.get("h0") is None and has_h0:
        # 两个切分在同一次 `build` 里写出来 ⇒ 一个有、一个没有只可能是**数据集被搬坏了**
        # （手工删/换过某一列）。硬拒：训练与验证在不同输入分布上比数字毫无意义。
        raise SystemExit("训练切分有 `h0` 列但**验证切分没有** —— 数据集被搬坏了（重新 build）")
    if has_h0 and no_carry:
        print("（提示）数据集**有** `h0` 列 ⇒ 它就是前向的 `h`；`--no-carry` 只放宽"
              "「缺列时的硬闸门」，不会把已有的 carry 丢掉")
    if has_h0 and not no_carry:
        print(f"长程 carry：`h0[{int(train_data['h0'].shape[1])}]` 已接入前向"
              f"（**窗口之前**的 GRU 隐状态逐行喂进 `EventTower`）—— 与上线端的整手 carry 同一把递推")
    # ---- ★ A−（T1）：`h0` 的宽度必须 == 本 arch 的 GRU 宽 -------------------------------------
    # 为什么是硬闸门：`h0` 是**上一代网**（v4，d=192）逐手重放出来的隐状态；A− 的 GRU 宽是 128
    # ⇒ 两个隐空间**没有对应关系**（不是"少了几维"，是另一个坐标系）。⛔ 不许切片、⛔ 不许投影
    # （投影要 +参数、会破坏 957,062 的账）。可选路径只有一条：显式 `--no-carry` 承担"窗口冷启动"，
    # 并在报告里写明"增量契约本身没变，变的是窗口之前的起点"。
    if has_h0:
        _h0w = int(train_data["h0"].shape[1])
        _gruw = int(M.arch_dims()["d_model"])
        if _h0w != _gruw:
            _msg = (f"`h0` 的宽度 {_h0w} ≠ 本 arch（{M.ARCH}）的 GRU 宽 {_gruw} —— "
                    f"h0 是**另一个隐空间**（v4 d={_h0w}）里逐手重放出来的，切片/投影都不是同一个量"
                    f"（投影还要 +参数、破坏 957,062 的账）")
            if no_carry is True:
                train_data.pop("h0", None)
                val_data.pop("h0", None)
                has_h0 = False
                print(f"⚠⚠ {_msg} ⇒ 按显式 `--no-carry` **丢弃 h0**（窗口冷启动："
                      f"增量契约不变，变的是「窗口之前」的起点；要恢复得用 a- 学生网重放整手事件 = 重新采集）")
            else:
                raise SystemExit(
                    f"⛔ {_msg}；要跑就只能显式 `--no-carry`（承担窗口冷启动），"
                    f"或重新采集把 `h0` 重算成 {_gruw} 维")

    # ---- ★ MV-1 第一刀：辅助子目标头 `own_win` 的**前置闸门（fail-closed）**--------------------
    # 三条（`lab/mv1/MV1-FEASIBILITY.md` §4.1 #10）：① 权重不能为负；② 权重 > 0 时**标签列必须在**
    # （缺列 ⇒ 当场退出，⛔ 绝不静默退化成"没这一列、这一项不生效"——那会得到一条"看着在训辅助头、
    # 其实一笔没算"的轨迹，正是本项目最贵的那类假绿）；③ 行口径只认 all|student。
    # ⚠ **权重 == 0（缺省）时这里一个字符都不打印**（缺省关 ⇒ 旧日志逐字不变）。
    aux_own_w = float(getattr(args, "aux_own_win_weight", 0.0) or 0.0)
    aux_own_rows = str(getattr(args, "aux_own_win_rows", "all") or "all")
    if aux_own_rows not in ("all", "student"):
        raise SystemExit(f"`--aux-own-win-rows` 只认 all|student（收到 {aux_own_rows!r}）")
    if aux_own_w < 0:
        raise SystemExit(f"`--aux-own-win-weight` 不能为负（收到 {aux_own_w:g}）：0 = 关（缺省）")
    if aux_own_w > 0 and train_data.get("aux_win_flag") is None:
        raise SystemExit(
            f"⛔ `--aux-own-win-weight {aux_own_w:g}` > 0，但这一份紧凑集**没有 `aux_win_flag` 列**"
            f"（{data_dir}）—— 辅助头 `own_win` 没有监督目标。按 fail-closed 拒跑："
            f"缺列时继续只会得到一条「辅助头一笔没算、其余照旧」的轨迹"
            f"（先 `python tools\\lab\\mv1-preflight.py --root <该紧凑集> --split train` 查标签）")
    if aux_own_w > 0:
        print(f"辅助头 `own_win`（MV-1 第一刀）：**开**，权重 {aux_own_w:g}，目标 = 二值 "
              f"`aux_win_flag`（BCE，**全部行**，⛔ 不吃 row_keep/RWR）；行口径次报告 "
              f"`--aux-own-win-rows {aux_own_rows}`；"
              f"⛔ 它是**外部头**：不进 `HEAD_SPECS`/`net.bin`/三端头表/推理路径")

    device = _device(args.device)
    threads = guard.apply_cpu_limit(args.threads)
    torch.manual_seed(args.seed)
    # 掩码用**固定种子**的生成器：同种子两次跑的掩码序列相同 ⇒ `history` 可逐字段复现
    rng_gen = torch.Generator(device="cpu").manual_seed(args.seed)
    model = M.build(seed=args.seed).to(device)
    ssl_head = MaskedEventHead().to(device)
    # ★ MV-1：辅助子目标头**只当开关开时才建**（缺省 0.0 ⇒ `None` ⇒ 无参数/无梯度/无 ckpt 键）。
    #   ⚠ 建在这里（`ssl_head` 之后）不扰动任何 RNG：`M.build` 已经把 `torch.manual_seed` 用掉，
    #   而训练路径此后不再抽 torch 全局 RNG（Adam 无随机、掩码用独立的 `rng_gen`）。
    own_win_head = OwnWinHead().to(device) if aux_own_w > 0 else None
    # 初始化：`--init <ckpt|net.bin>` 时从上一代权重起步（P3/P4 都是"接着上一代练"）
    init_spec = getattr(args, "init", None)
    if init_spec:
        src = _load_behaviour(init_spec, device)
        model.load_state_dict(src.state_dict(), strict=True)
        print(f"初始化：从 {init_spec} 载入权重")
    # ---- ★ BL-04：**优化器状态续跑**（`--resume-optimizer`；不给 ⇒ 这一段一行都不走）----------
    # ⚠ 顺序：SSL 头**只有存档里有**（`--init` 不管它）⇒ 无条件先从存档载，再核对"权重是不是
    #   存档那一刻的那一份"（JSON 哈希对不上就是「拿 A 的权重套 B 的优化器状态」= 无意义轨迹）。
    resume_spec = getattr(args, "resume_optimizer", None)
    resume: dict | None = None
    entry_gstep = 0
    if resume_spec:
        resume = load_resume_state(resume_spec)
        res0 = resume["resume"]
        ssl_head.load_state_dict(resume["ssl_head"], strict=True)
        # ★ MV-1：辅助头也在存档里（**只有它存在时**才载 —— 关着时存档里根本没有这个键）
        if own_win_head is not None and resume.get("own_win_head"):
            own_win_head.load_state_dict(resume["own_win_head"], strict=True)
        if init_spec:
            pass                                  # 权重已由 `--init` 载入，下面核对指纹
        else:
            model.load_state_dict(resume["model"], strict=True)
            print(f"续跑：权重 + SSL 头从 {resume['path']} 载入（`--init` 没给 ⇒ 就用存档那一份）")
        got_w = _state_sha256(model.state_dict(), ssl_head.state_dict(), _own_win_sd(own_win_head))
        if got_w != str(res0.get("weights_sha256")):
            raise SystemExit(
                f"`--init` 与 `--resume-optimizer` **不是同一份权重**"
                f"（{init_spec or '（未给）'} vs {resume['path']}）—— 续跑必须从**存档那一刻**的权重"
                f"接着走；⛔ 换个起点再套上别人的优化器状态只会得到一条无意义的轨迹")
        # 参数冻结状态（`--only-heads` / 阶段 b 的 `requires_grad`）必须原样恢复
        rg = dict(res0.get("requires_grad") or {})
        for kind, mod in (("model", model), ("ssl", ssl_head), ("own", own_win_head)):
            if mod is None:
                continue                              # ★ MV-1：开关关着时辅助头对象不存在
            for name, p in list(mod.named_parameters()):
                key = f"{kind}.{name}"
                if key in rg:
                    p.requires_grad = bool(rg[key])
        entry_gstep = int(res0["counters"]["gstep"])
        print(f"续跑存档：{resume['path']} · gstep={entry_gstep} · "
              f"epoch={res0['counters']['epoch']} · epoch 内第 {res0['counters']['step_in_epoch']} 步 · "
              f"权重指纹 {got_w[:16]}…（Adam 动量/方差 + 四路 RNG + 数据顺序指纹都在里面）")
    # ---- ★ W3：对**现任**的 KL 锚 `β · KL(π_θ‖π_ref)` -------------------------------------
    # `docs/VALVES-AND-FIXTURES.md` §3 的 W3：历季"每轮只走 66/600 步 ⇒ 候选只是现任的微小扰动"，
    # 而闸门的分辨率（n=2000 时 ±2.35 顺位点）**看不见**那个量级。⇒ 改成"让每轮真的能动
    # （放宽 KL 预算）+ 让方向可靠（**对现任加 KL 锚**）"：锚把 π_θ 的每一次移动都拉回现任，
    # 于是"能动"与"别乱动"可以分别用两个旋钮（KL 早停阈值 / β）调。
    #
    # ⚠ `--ref-beta 0`（缺省）时**这一整段一行都不走**（连参考网都不建、不载）⇒ 与加这个功能
    #   之前**逐位相同**（这是能不能安全上线的判据；临时脚本用 HEAD 那份 `pretrain` 逐位对过）。
    ref_beta = float(getattr(args, "ref_beta", 0.0) or 0.0)
    if ref_beta < 0:
        raise SystemExit(f"--ref-beta 不能为负（收到 {ref_beta:g}）：它是「把 π_θ 拉回现任」的力度")
    ref_spec = getattr(args, "ref", None)
    ref_model: M.V4Model | None = None
    # ★ BL-04（第七十九轮）：`π_ref` 是"续跑必须**原样恢复**"的状态之一 —— 这里把它的**来源**与
    #   **权重指纹**记下来，`--save-optimizer` 时进存档；续跑时以**存档记的那一份**为准。
    ref_src: str | None = None
    ref_sha: str | None = None
    ref_explicit = bool(ref_spec)
    if ref_beta > 0:
        arch_ref = dict(resume["resume"].get("ref") or {}) if resume is not None else {}
        if resume is not None:
            got_beta = float(arch_ref.get("beta", 0.0) or 0.0)
            if abs(got_beta - ref_beta) > 1e-12:
                raise SystemExit(
                    f"⛔ 续跑的 KL 锚力度与存档不一致：存档 β={got_beta:g} vs 本次 {ref_beta:g}"
                    f"（锚是轨迹的一部分，换了它接出来的不是同一条轨迹）")
            if not arch_ref.get("sha256"):
                raise SystemExit(
                    f"⛔ 这份续跑存档没记 **KL 锚的来源**（`{RESUME_KEY}.ref` 段，格式 1 的老存档）"
                    f"⇒ 锚会被按「本段的 `--init`」现推，而续跑时 `--init` 恰好是**上一段的存档**"
                    f"⇒ π_ref 悄悄换了一份网。要接着练请**重新起一段**（`--init` 给这份权重、"
                    f"不带 `--resume-optimizer`）")
        # 缺省锚 = `--init` 那一份**现任**（"单调精修"要锚的就是现任，不是随便一份权重）
        # ⚠★ 续跑时**不**走这一条：`--init` 是"上一段的存档"，拿它当锚 = 每段都换一次参考策略。
        if ref_spec:
            source = ref_spec
        elif resume is not None:
            source = arch_ref.get("source")
            ref_explicit = bool(arch_ref.get("explicit"))
            print(f"续跑：KL 锚**沿用存档记录的参考策略**（⛔ 不再按本段的 `--init` 现推 —— "
                  f"那会让段与段之间的 π_ref 悄悄换人）")
        else:
            source = init_spec
        if not source:
            raise SystemExit("--ref-beta > 0 但**没有参考策略**：给 `--ref <net.bin|model.pt|ckpt 目录>`，"
                             "或给 `--init`（缺省锚 = `--init` 那一份现任网）")
        ref_model = _load_behaviour(source, device)
        # ⚠ 冻结三件套：`requires_grad_(False)`（参数不更新）+ `eval()`（无 dropout，语义正确性）
        #   + `_ref_logits` 里的 `no_grad`（它的 logits 绝不进反向图）。
        ref_model.requires_grad_(False)
        ref_model.eval()
        ref_src = str(source)
        # 指纹取**权重**本身（`net.bin` 与含它的 ckpt 目录会给出**同一个**指纹 ⇒ 判的是"是不是同一份网"，
        # 不是"是不是同一个路径"）。⚠ 必须在 `del ref_model` 之前算。
        ref_sha = _state_sha256(ref_model.state_dict())
        if resume is not None and ref_sha != str(arch_ref.get("sha256")):
            raise SystemExit(
                f"⛔ 续跑换了一份 **π_ref**：本次 {ref_src}（权重指纹 {ref_sha[:16]}…）vs 存档 "
                f"{arch_ref.get('source')}（{str(arch_ref.get('sha256'))[:16]}…）—— 锚是轨迹的一部分，"
                f"换了它，「续跑 == 一口气跑」必然不成立；要换锚就**重新起一段**")
        print(f"KL 锚（W3）：β={ref_beta:g}，参考策略 {source}"
              f"{'（`--ref` 显式指定）' if ref_spec else ('（**续跑：沿用存档**）' if resume is not None else '（**缺省 = `--init` 那一份现任**）')}"
              f"；π_ref **冻结**（requires_grad=False + eval + no_grad 算 logits）"
              f"{'' if ref_sha is None else f'；权重指纹 {ref_sha[:16]}…'}")
        # ⚠ **锚不是目标**（P2 护栏）：它只把 π_θ 拉回现任 —— "别乱动"是**约束**，不是**方向**。
        #   实测：β=0.5 会把 `--il-weight` 的一致率增益**砍半**（KL(π‖init) 0.377→0.168）。
        #   所以"放宽 KL 预算 + 加锚"这一对里，**位移额度靠别的东西给**，锚只负责别跑偏。
        print("   ⚠ 锚**只保证「别乱动」，不提供目标**（不补打点、不补押し引き）："
              "实测 β=0.5 把 `--il-weight` 的增益**砍半**（KL(π‖init) 0.377→0.168）"
              "⇒ 要「能动」必须同时有**别的**信号源，别指望锚本身。")
    elif ref_spec:
        # 不静默：给了 `--ref` 却没给 β ⇒ 锚不生效（那是缺省行为，但要说出来）
        print(f"⚠ 给了 `--ref {ref_spec}` 但 `--ref-beta`=0 ⇒ **锚不生效**（那几行一行都没走，"
              f"与加这个功能之前逐位相同）；要启用就同时给 `--ref-beta <β>`")
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
    # ★ MV-1：辅助头进优化器**只在它存在时**（关着时这一项是空列表 ⇒ 参数组逐位相同）
    if own_win_head is not None:
        heads = heads + list(own_win_head.parameters())
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
    # ---- ★ MV-1 第九十九轮：lr 调度（唯一旋钮；缺省 `cosine` = 老表达式，一个浮点都不改）----
    # ⚠ `cosine` 的 floor **写死 0.1**（= 老代码里的 `0.1 + 0.9·½(1+cos)`）：这样"旧路径逐字节不变"
    #   不依赖任何新参数的取值。要改 floor 必须显式选 `floored-cosine`（⛔ 不许静默忽略旋钮）。
    lr_sched = str(getattr(args, "lr_schedule", "cosine") or "cosine")
    lr_floor = float(getattr(args, "lr_floor_frac", 0.1))
    if lr_sched not in ("cosine", "constant", "floored-cosine"):
        raise SystemExit(f"`--lr-schedule {lr_sched}` 不认识；可选 cosine / constant / floored-cosine")
    if not (0.0 <= lr_floor <= 1.0):
        raise SystemExit(f"`--lr-floor-frac {lr_floor:g}` 越界：必须 ∈ [0,1]（1.0 = 恒定 lr）")
    if lr_sched == "cosine" and lr_floor != 0.1:
        raise SystemExit(f"`--lr-schedule cosine` 的 floor 固定 0.1（老表达式）；"
                         f"要 floor={lr_floor:g} 请显式用 `--lr-schedule floored-cosine`")
    if lr_sched != "cosine" and getattr(args, "resume_optimizer", None):
        raise SystemExit("⛔ 换了 `--lr-schedule` 不许续跑：续跑指纹（`_resume_recipe`）里没有这一项，"
                         "接着跑出来的不是同一条轨迹；要换调度请从 `--init` 重新起一段")
    print(f"lr 调度：{lr_sched}"
          + (f"（floor = {lr_floor:g}·base）" if lr_sched == "floored-cosine" else "")
          + "；c 段之外的阶段照旧保持常数 lr")
    # ⚠ **KL 早停必须够得着**（第二十五轮实测的坑）：`--max-steps 100` 配 `--kl-min-steps 100`
    #   ⇒ 判据只在 step ≥100 时才看，而一轮最多 100 步 ⇒ 早停永不触发（三代 `stop_reason` 全 None）。
    kl_min_eff = effective_kl_min_steps(steps, int(getattr(args, "kl_min_steps", 100) or 0))
    if kl_min_eff != int(getattr(args, "kl_min_steps", 100) or 0):
        print(f"⚠ `--kl-min-steps {int(getattr(args, 'kl_min_steps', 100) or 0)}` 对"
              f"{steps} 步/轮来说**够不着**（早停永远不会触发）⇒ 钳到 {kl_min_eff}"
              f"（轮长的 1/5）")
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
    # π_ref 的 logits **预算一次**（冻结量；见 `_ref_logits` 的三条纪律）。放在这里是为了
    # `ablate` 已经建好 —— 锚必须看与学生**同一份**（可能被消融过的）输入。
    ref_np: np.ndarray | None = None
    if ref_model is not None:
        t_ref = time.perf_counter()
        ref_np = _cached_ref_logits(args, ref_model, train_data, device, ablate or None)
        print(f"KL 锚：π_ref 的 logits 已冻结预算（{ref_np.shape[0]} 行 × {ref_np.shape[1]} 候选，"
              f"{ref_np.nbytes / 2**20:.1f} MiB，用时 {time.perf_counter() - t_ref:.1f}s）"
              f"—— 冻结量算一次就够（每 batch 现算是 `epochs ×` 的浪费）")
        del ref_model                                # logits 已落成数组，参考网本身不再需要
        ref_model = None
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
    # ---- ★ 验证集早停（防过拟合，廉价读数）--------------------------------------------------
    # `--val-every 0`（缺省）= 关闭 ⇒ 与今天的行为逐位相同（一行都不走）。
    # 读数用**现成的 `evaluate()`**（`ce_probe`），口径与 epoch 行的 `policy {val_loss_policy}` 一致；
    # 行子集**固定**（`probe_rows`），否则两次之间的差里混着采样噪声。日志**同时打 train CE 与
    # val CE**：train 还在降、val 开始抬头 = "开始记数据"（这就是要早停的那一刻）。
    val_every = int(getattr(args, "val_every", 0) or 0)
    val_patience = int(getattr(args, "val_patience", 3) or 3)
    # ★ W4：早停读哪个头的 CE —— 缺省 `policy`（= 加这个开关之前逐位相同）；用 `--il-weight > 0`
    #   时必须切到 `il`（行为 CE 在 IL 臂上**必然**抬高，见 `ce_probe` 的注释与 §14.12 的实测）。
    val_metric = str(getattr(args, "val_metric", "policy") or "policy")
    if val_metric not in ("policy", "il"):
        raise SystemExit(f"--val-metric 只认 policy|il（收到 {val_metric!r}）")
    if val_every < 0 or val_patience < 1:
        raise SystemExit(f"--val-every 不能为负（{val_every}）、--val-patience 至少 1（{val_patience}）")
    # 读数子集**至少 1024 行**（而不是 `eval_batch` 的 256）：逐行 policy CE 是**重尾**的
    # （实测：中位 0.17 / p90 1.50 / max 14.3）⇒ 256 行的**自助法标准误 ≈0.048**，而要看的
    # 趋势量级只有 0.03~0.05 ⇒ 读数会被自己的抽样噪声主导（实测同一份权重上 256 行子集给 0.6213、
    # 全 val 切分给 0.5241）。1024 行把标准误压到 ≈0.024，且行子集**固定** ⇒ 趋势可比。
    # ⚠ 绝对水位仍然不等于"全 val 切分"的数（本仓库的旧账：两把尺子量同一个东西）——
    #   早停只用**趋势**，epoch 行上那个 `policy …` 才是全切分的数。
    probe_n = max(int(args.eval_batch), 1024)
    tr_probe = va_probe = None
    if val_every > 0:
        tr_probe = probe_rows(n, probe_n, seed=0)
        va_probe = probe_rows(nv, probe_n, seed=0)
        print(f"验证集早停（防过拟合）：每 {val_every} 步读一次 **{val_metric}** CE（train/val 各 "
              f"{tr_probe.size}/{va_probe.size} 行，**固定子集**；口径 = `evaluate().loss_{val_metric}`），"
              f"连续 {val_patience} 次未改善（相对最好值，容忍 {CE_IMPROVE_TOL:g}）就**正常收尾**"
              f"（返回 metrics + 照常导出 ckpt）")
    if w_tr is not None:
        print(f"RWR：β={args.rwr_beta:g} 千点 → 权重 min={float(w_tr.min()):.3f} "
              f"mean={float(w_tr.mean()):.3f} max={float(w_tr.max()):.3f}")
    # PPO：先重算"采集那一步"的 log π_old（行为策略 = `--behaviour`，温度 = 策略串里的 `#T`）
    logp_tr = None
    ppo_cfg = None
    p_temp = 1.0                     # 策略温度：非 PPO 恒为 1.0（= 不缩放）
    #: ★ BL-14：ρ 的分布记账（`--rho-max > 0` 或多行为路径才非 None ⇒ 旧路径一行都不走）
    rho_acc: dict | None = None
    rho_cfg: tuple | None = None
    #: ★ BL-14：多行为闸门的读数（`None` = 没走那条路 ⇒ `metrics.json` 不多任何键）
    beh_gate: dict | None = None
    # ★ BL-04：**行为策略标识**（`sha256(权重文件)` + 策略串）—— 续跑时"是不是同一份网"由它判。
    # 只要给了 `--behaviour` 就记（PPO 与 gae-hand 两条路都要它）。
    beh_rec: dict | None = None
    if getattr(args, "behaviour", None):
        beh_rec = _behaviour_record(str(args.behaviour), meta)
    # ★ BL-14：**多行为（混代）**路径的开关判据 —— 紧凑集有 `behaviour_id` 列 **且** meta 有表。
    # 两条都不满足（老紧凑集 / 没开 `--behaviour-id`）⇒ `multi=False` ⇒ 下面那条老路**逐字节不变**。
    b_ids = behaviour_ids_of(train_data)
    b_table = behaviour_table_of_meta(meta)
    multi_flag = bool(getattr(args, "behaviour_multi", False))
    # ⚠ 判"混代"只看**学生行**的 id：teacher/对手的行本来就不进策略损失，它们的
    #   行为策略是哪一份**不影响** `logp_old` 的用途（`is_student` 已把它们排掉）。
    stu_col = train_data.get("is_student")
    stu_ids = (b_ids[np.asarray(stu_col) > 0] if (b_ids is not None and stu_col is not None)
               else b_ids)
    n_bids = int(np.unique(stu_ids).size) if stu_ids is not None else 0
    # ⛔ **fail-closed**：**学生行里混了多代**却没开多行为路径 ⇒ 单一 `--behaviour` 会给
    #   别的代的学生行算 `logp_old`（ρ 整体错而训练照跑）。宁可当场拒跑。
    if objective == "ppo" and n_bids > 1 and not multi_flag:
        raise SystemExit(
            f"⛔ 这份紧凑集的**学生行**有 **{n_bids} 个 `behaviour_id`**（混代数据），但没给 "
            f"`--behaviour-multi` —— 单一 `--behaviour` 只能给**一份**行为策略算 `logp_old`，"
            f"其余几代的学生行 ρ 会整体错。要么加 `--behaviour-multi` 走逐 id 路径，"
            f"要么只用单代数据。按 fail-closed 拒跑")
    multi = bool(b_table) and b_ids is not None and objective == "ppo" and multi_flag
    if objective == "ppo" and b_ids is not None and n_bids == 1 and not multi_flag:
        # 学生行只有一代（例：一次采集只用一个学生网 + teacher 补位）⇒ 加不加这一列**数值上
        # 完全等价**。这正是判据「加列 ⇒ 训练逐字节不变」的那条路（`python/selfcheck.py` 钉着）。
        print(f"PPO：紧凑集带 `behaviour_id`（**学生行**单一代）但未开 `--behaviour-multi` "
              f"⇒ 走**逐字节相同**的旧单策略路径")
    if multi:
        plan = _ppo_plan_multi(model=model, train_data=train_data, device=device, args=args,
                               meta=meta, resume=resume, n=n, b_ids=b_ids, b_table=b_table,
                               beh_rec=beh_rec, keep_tr=keep_tr)
        logp_tr, p_temp = plan["logp_tr"], plan["p_temp"]
        kl0, kl_seg = plan["kl0"], plan["kl_seg"]
        beh_rec, rho_acc, keep_tr = plan["beh_rec"], plan["rho_acc"], plan["keep_tr"]
        beh_gate = plan["gate"]
        ppo_cfg = (float(getattr(args, "ppo_clip", 0.2) or 0.2),
                   float(getattr(args, "ppo_entropy", 0.01) or 0.0))
    if objective == "ppo" and not multi:
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
        # ⚠ **PPO 时禁 RWR**（§14 P2-⑥）：见 `assert_ppo_excludes_rwr` 的推导。
        assert_ppo_excludes_rwr(objective, rwr_beta)
        print(f"PPO：grad-clip {float(getattr(args, 'grad_clip', 0.5) or 0.0):g}、"
              f"KL 早停阈值 {float(getattr(args, 'kl_early_stop', 0.03) or 0.0):g}"
              f"（min_steps {kl_min_eff}）")
        # ---- 口径闸门（2026-09-28 加；这条闸门就是为了不让 `#0.5` 那个 bug 再发生一次）----
        # ⚠★ BL-04：**续跑时 π_new 本来就不等于 π_old**（已经走过 N₁ 步）⇒ "初始化时 KL≈0" 这把
        #   尺子在续跑路径上**必然是红的**，而它想守的那件事（`logp_old` 必须来自数据生成策略）
        #   要用**等价**的一组判据守：行为策略标识（sha256 + 策略串 + 温度）· 链条起点口径
        #   （存档记的 kl0 ≈ 0）· 连续性（同一条探针行的 KL 与存档逐位一致）。
        #   ⇒ **不给 `--resume-optimizer` 时这一行逐字节不变**（旧路径的牙齿一颗不少）。
        if resume is None:
            kl0 = assert_behaviour_consistency(model, train_data, np.arange(min(64, n)),
                                               logp_tr, temp, device)
            kl_seg = None
            print(f"PPO：口径自检 KL(π_old‖π_new)={kl0:+.2e}（≈0 = 权重与温度都对上了）")
        else:
            # ★ 第八十轮：**口径判据 ≠ 漂移记账**。`kl0` 是**链条起点**的读数（逐段沿用，硬拒判据）；
            #   `kl_seg` 是**本段边界**的行为 KL（训练的自然后果，只记账 + 告警，⛔ 不作判据）。
            kl0, kl_seg = assert_resume_behaviour(resume, str(beh), float(temp), model, train_data,
                                                  meta, logp_tr, np.arange(min(64, n)), device)
            if kl_seg is not None:
                # 告警阈值 = 本 recipe 的 KL 早停阈值：越过它意味着"这一段基本走不动了"，
                # 那是**训练量**的信号（该换新鲜采集），不是口径不一致。
                thr = float(getattr(args, "kl_early_stop", 0.03) or 0.0)
                tail = (f"；⚠ 已达本 recipe 的 KL 早停阈值 {thr:g} ⇒ 这一段的可用步数基本到顶，"
                        f"要更长就该换**新鲜采集**（BL-13/BL-14），⛔ 不是继续加步数"
                        if thr > 0 and abs(float(kl_seg)) > thr else "")
                print(f"PPO 续跑口径：**本段边界**漂移 KL(π_old‖π_new)={float(kl_seg):+.6e}"
                      f"（= 记账 `behaviour.kl_segment`，⛔ 不是口径判据：口径由「行为策略身份 + 链条起点"
                      f" + 连续性探针」三把尺子守）{tail}")
        if beh_rec is not None:
            # ⚠ `kl0` **只在链条起点那一段**由闸门现算（续跑沿用存档值）；`kl_segment` 记本段边界。
            beh_rec.update({"temp": float(temp),
                            "kl0": (None if kl0 is None else float(kl0)),
                            "kl_segment": (None if kl_seg is None else float(kl_seg))})
    # ★ BL-14：ρ 的分布记账 —— ⚠ **必须在 `if objective == "ppo"` 之外**（多行为路径在
    #   `_ppo_plan_multi` 里已经建好 `rho_acc`，而它走的是另一条分支 ⇒ 放里面会被跳过）。
    #   单行为路径只在显式给了 `--rho-max > 0` 时才建 ⇒ 不给开关时旧路径**一个新键都不多**。
    if objective == "ppo":
        rho_max = float(getattr(args, "rho_max", 0.0) or 0.0)
        if rho_acc is None and rho_max > 0:
            rho_acc = {"samples": [], "n_rows": 0, "n_gt_max": 0, "n_gt_clamp": 0,
                       "worst": 0.0, "clipped_batches": 0}
        if rho_acc is not None:
            rho_cfg = (rho_max, str(getattr(args, "rho_max_action", "fail") or "fail"), rho_acc)
            print(f"PPO：ρ 记账已开（上限 {rho_max:g} / 动作 "
                  f"{rho_cfg[1]}；逐行计数 + 分位数，⛔ 不静默夹取）")
    # ---- 手级 GAE（§14 P1）：`--advantage gae-hand` 的预计算（行为策略的 V ⇒ GAE ⇒ 冻结）----
    adv_tr = None
    val_extra: dict[str, np.ndarray] | None = None
    if advantage in ("gae-hand", "hand"):
        mode = "gae" if advantage == "gae-hand" else "hand"
        beh = getattr(args, "behaviour", None)
        if not beh:
            raise SystemExit(f"--advantage {advantage} 需要 --behaviour <采集用的 ckpt/net.bin>："
                             "V_old 必须来自**行为策略**（与 logp_old 同一份权重）")
        if beh_rec is None:              # ★ BL-04：这一路也要记标识（续跑时同一把尺子）
            beh_rec = _behaviour_record(str(beh), meta)
        beh_model = _load_behaviour(beh, device)
        t_v = time.perf_counter()
        print(f"小局级优势（{mode}）：用行为策略 {beh} 重算 V_old（train {n} / val {nv} 条）…")
        v_old, v_old_va = _cached_behaviour_values(args, beh_model, train_data, val_data, device)
        del beh_model
        res = _hand_advantage(train_data, v_old, gamma=float(getattr(args, "gae_gamma", 1.0) or 1.0),
                              lam=float(getattr(args, "gae_lambda", 0.9) or 0.0),
                              rank_weight=float(getattr(args, "rank_weight", 0.0) or 0.0),
                              is_student=np.asarray(train_data["is_student"]) if keep_tr is not None
                              else None, mode=mode,
                              baseline_fit=str(getattr(args, "baseline_fit", "scale") or "scale"),
                              style_col=_style_reward.COLUMN)
        adv_tr = res["adv"]
        vtar_tr = res["vtarget"]
        res_va = _hand_advantage(val_data, v_old_va,
                                 gamma=float(getattr(args, "gae_gamma", 1.0) or 1.0),
                                 lam=float(getattr(args, "gae_lambda", 0.9) or 0.0),
                                 rank_weight=float(getattr(args, "rank_weight", 0.0) or 0.0),
                                 is_student=None, mode=mode,
                                 baseline_fit=str(getattr(args, "baseline_fit", "scale") or "scale"),
                                 style_col=_style_reward.COLUMN)
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
            flat = s.get("std_ratio_flat")
            extra = (f"（参照：未重标定 {unfit:.3f}× · 只减均值 {flat:.3f}×"
                     f"{' ⇐ 含顺位点项的目标方差更大' if flat and flat > 1.0 else ''}）"
                     if unfit is not None and np.isfinite(unfit) else "")
            print(f"  优势体检（**归一化之前**）：std(A_raw) {s['adv_std_raw']:.3f} vs "
                  f"std({s['base_name']}) {s['base_std']:.3f} ⇒ **{s['std_ratio_raw']:.3f}×**"
                  f"（<1 = 方差被削减；这就是 critic 有没有用的直接度量）{extra}")
        if s.get("style_reward"):
            # ★ 风格奖励塑形的读数（**只有开了那一列才打**；关掉时这里一行都不出现 ⇒
            #   "关掉时逐位相同"连日志都不受影响）。
            sr = s["style_reward"]
            print(f"  风格奖励塑形：`{_style_reward.COLUMN}` 列覆盖 {sr['nonzero_rows']}/{sr['rows']} 行"
                  f"（非 0 = **拿到钱的行**；`hand` 模式是小局级常数 ⇒ 整手都非 0，"
                  f"`decision` 模式只有动作行非 0）；"
                  f"小局级 mean {sr['hand_mean']:+.1f} std {sr['hand_std']:.1f} **点** "
                  f"vs 结果奖励 std {sr['reward_base_std']:.1f} 点 ⇒ "
                  f"**{sr['hand_std_vs_reward']:.5f}× 结果奖励的尺度**"
                  f"（= 「这一轴会不会支配梯度」的**尺度**读数：远小于 1 就推不动；"
                  f"{_style_reward.parity_note(sr['w_for_parity'])}）")
            # ★ 逐行（学生行）读数：`hand` 与 `decision` 两种模式的**剂量**在这一行上可比
            #   （`hand` 的列是小局级常数、`decision` 的列只在动作行非 0 —— 用"每小局第一行"
            #   采样会把后者几乎全采成 0，所以这一份才是两种模式共用的那把尺子）。
            print(f"    逐行（{sr['row_population']} 行 {sr['row_n']} 行）mean {sr['row_mean']:+.1f} "
                  f"std {sr['row_std']:.1f} 点 vs 同一批行上被减奖励 std {sr['row_base_std']:.1f} 点 "
                  f"⇒ **{sr['row_std_vs_reward']:.3f}×**（= 塑形列相对结果奖励的方差尺度；"
                  f"1:1 就是「这一轴与结果奖励同权」）")
            print(f"    被减的那个奖励（delta + 塑形 + 顺位点）的 std：{s['ref_std']:.3f} 千点；"
                  f"std(A_raw)/std({s['ref_name']}) = {s['std_ratio_raw']:.3f}×")
            print(f"  归一化后（学生行 z-score）std = {s['adv_std_norm_student']:.3f}"
                  f"（⚠ 别拿它比参考量：归一化本身就把尺度钉成 1）")
        print(f"  （用时 {time.perf_counter() - t_v:.1f}s；优势已冻结、不再随 critic 变动）")
    # ---- ★ W4 第一步：**逐候选引擎标签**（`docs/VALVES-AND-FIXTURES.md` §3 的 W4）---------------
    # 为什么需要它：RL 这一路已证明"看不见自己的进步"（每轮只走 100~150 步、KL 预算用满也只有
    # 0.05 nats ⇒ 效应小于闸门在小算力下的分辨率 ±2.35 顺位点）。而引擎**每一行**都给逐候选的
    # 牌效量（`cand.derived`：打后向听/进张/听牌形），那是**稠密、无采样噪声、不花闸门算力**的
    # 监督信号，且**每一份现有紧凑集里都有**（不需要新采集、不需要新生产者、不新增列）。
    # 放在这里（而不是函数开头）是为了 `--advantage gae-hand/hand`：那两路的 val 前向要 `vtarget`
    # （`val_extra` 刚建好），否则下面那次"开训前读数"会 KeyError。
    #
    # 两个用途分开，别混：
    #   ① `--il-weight > 0` ⇒ **训练项** `w·CE(logits, 引擎最优候选)`（只在学生行上）；
    #   ② 无论权重多少 ⇒ **读数** `engine_top1`（argmax(logits) == 引擎最优）+ 随机基线
    #      `engine_random`（`1/nlegal` 的均值）+ **行为策略基线**（开训前那份 `--init` 网的同一个数）。
    #      这个一致率就是"这一轮有没有学到东西"的低噪判据（取代 ±2.35 那个闸门）。
    il_w = float(getattr(args, "il_weight", 0.0) or 0.0)
    if il_w < 0:
        raise SystemExit(f"--il-weight 不能为负（收到 {il_w:g}）：它是模仿项的权重，0 = 关闭")
    il_tr: np.ndarray | None = None
    il_va: np.ndarray | None = None
    _il_span = spec.block_slices()["cand.derived"]
    _il_n = int(_f3.DERIVED_CANDIDATE)
    try:
        il_tr = v4ds.engine_targets(train_data)
        il_va = v4ds.engine_targets(val_data)
    except spec.ContractError as e:
        if il_w > 0:
            raise SystemExit(f"⛔ `--il-weight {il_w:g}` 需要**逐候选引擎标签**，但这一份紧凑集给不出：{e}")
        # ⚠ 不静默：老紧凑集（`cand.derived` 整块 0）在 `--il-weight 0` 下照常能训（逐位兼容），
        #   但**必须说出来**"这一轮没有引擎一致率读数"，否则报告里会凭空少一个判据而没人发现。
        print(f"⚠ **没有逐候选引擎标签**（`--il-weight` = 0 ⇒ 不影响训练，但一致率读数本轮不可用）：{e}")
    if il_w > 0:
        # ⛔ **显眼警告**（第七个 P2 护栏）：`--il-weight > 0` 是**已被实测证否**的开关，不改它的
        #   行为（不拦、不报错），但**必须**在日志最上面说清楚，免得下一轮又把它当"进步手段"打开。
        #   只在 > 0 时打（缺省 0 一个字符都不多 ⇒ 行为与加这个警告之前逐位相同）。
        print("=" * 100)
        print(f"⛔⛔ 警告：`--il-weight {il_w:g}` > 0 —— 这个开关**已被实测证否**（不是「还没验证」）：")
        print("    · 引擎逐候选标签是**纯牌效**，**没有打点、没有押し引き**（`dora_count` 刻意不参与）")
        print("      ⇒ 优化它必然拿这两样去换进张；")
        print("    · 实测（一季第 1 代，2026-10-03）：闸门 **Δ=−12.65 顺位点 CI[−14.92,−10.42] n=2000**")
        print("      （越界 5 倍）、和了率 24.1%→20.8%、放铳率 15.7%→17.0%、**平均打点 7037→6010**；")
        print("    · 唯一「变好」的读数是 `engine_top1`（+6.95pp）—— 它与闸门**符号相反**，")
        print("      ⛔ **贴标签的读数不是强弱判据**；**判强弱只能走闸门**（多套新牌山配对、CI 排除 0 且为正）。")
        print("    详见 `NOTES.md` §6.5 第六十三轮 / `docs/TRAINING-V4.md` §14.12.6。")
        print("=" * 100)
    if il_tr is not None:
        print(f"引擎标签（W4）：目标候选从 `cand.derived[{_il_span[1]}:{_il_span[1] + _il_n}]` 现算"
              f"（打后向听/进张/听牌形 —— **引擎给的稠密标签**，无采样噪声、不花闸门算力）；"
              f"模仿项权重 `--il-weight` = {il_w:g}"
              f"{'（**缺省 0 = 逐位等于今天**）' if il_w == 0 else ''}")
    # 行为策略基线：**开训前**（= `--init` 那一份现任网）的引擎一致率 —— 判据是"训练后必须显著高于它"
    il_init = il_init_all = il_rand = None
    if il_va is not None:
        model.eval()
        ev0 = evaluate(model, val_data, device, batch=args.eval_batch, value_key=value_key,
                       ablate=ablate or None, extra=val_extra, il_target=il_va)
        ev0s = (evaluate(model, val_data, device, batch=args.eval_batch, row_keep=keep_va,
                         value_key=value_key, ablate=ablate or None, extra=val_extra, il_target=il_va)
                if keep_va is not None else ev0)
        model.train()
        il_init = ev0s.get("engine_top1")          # 行为策略基线（学生行 = 与策略损失同口径）
        il_init_all = ev0.get("engine_top1")       # 全部行（含教师/对手行）
        il_rand = ev0.get("engine_random")         # 随机基线 1/平均候选数
        print(f"引擎一致率基线（**开训前** = `--init` 那一份网）：学生行 {il_init:.4f}"
              f"（n={ev0s.get('engine_n', 0)}）/ 全部行 {il_init_all:.4f}"
              f" | **随机基线 1/平均候选数** {il_rand:.4f}"
              f" ⇒ 判据：训练后的 `engine_top1`（学生行）必须**显著高于** {il_init:.4f}")
    print(f"数据：训练 {n} 条（{len(meta['train_files'])} 场）/ 验证 "
          f"{nv} 条；lmax={meta['lmax']}；"
          f"标签侧 {'有' if meta['has_aux'] else '无'}；"
          f"长程 carry {'有（`h0` 列）' if has_h0 else '**无**（窗口冷启动）'}；"
          f"学生行占比 训练 {frac_tr:.1%} / 验证 {frac_va:.1%}"
          f"{'（`--student` 遮罩生效）' if keep_tr is not None else '（无掩码：全部行都算学生）'}")
    print(f"模型：{model.param_count():,} 参数（+SSL 头 {sum(p.numel() for p in ssl_head.parameters()):,}）；"
          f"device={device}；threads={threads}；seed={args.seed}")
    print(f"分阶段：a 策略+牌效（{int(args.stage_a * 100)}%）→ b 冻主干只训头"
          f"（{int(args.stage_b * 100)}%）→ c 联合微调（其余）；每 epoch {steps} 步 × batch {args.batch}")

    # ---- ★ BL-04 续跑：优化器 / 计数器 / RNG 的恢复（**必须在 `base_lrs` 之前**）------------
    # ⚠ 为什么 `base_lrs` 要单独存：`opt.load_state_dict()` 会把 `param_groups` 里的 `lr`
    #   覆盖成**存档那一刻的调度值**（阶段 c 的余弦），拿它当"基准 lr"会让余弦重开一段
    #   ⇒ 分段跑与一口气跑**从这里开始分岔**（静默、只体现在权重上，日志看不出来）。
    save_optimizer = bool(getattr(args, "save_optimizer", False))
    stop_after = int(getattr(args, "stop_after_steps", 0) or 0)
    if stop_after < 0:
        raise SystemExit(f"--stop-after-steps 不能为负（收到 {stop_after}）；0 = 不限制")
    base_lrs = [pg["lr"] for pg in opt.param_groups]
    history: list[dict] = []
    stage_steps = {"a": 0, "b": 0, "c": 0}
    t0 = time.perf_counter()
    gstep = 0
    stop_reason: str | None = None
    # 验证集早停的状态（**跨 epoch 连续**：早停判的是"整轮"的 val CE 轨迹，不是单个 epoch）；
    # `ce_log` 逐点存下来进 metrics —— 报告要的是"两条 CE 的数值轨迹"，只打屏会丢。
    best_val_ce: float | None = None
    no_improve = 0
    ce_log: list[dict[str, float]] = []
    start_epoch, start_skip = 1, 0
    resume_cur_stage: str | None = None
    resume_acc: dict = {"run": 0.0, "seen": 0, "run_parts": {}}
    c0: dict = {}
    if resume is not None:
        res0 = resume["resume"]
        c0 = res0["counters"]
        want = _resume_recipe(args, resolved_data=str(Path(args.data).resolve()), n=n,
                              steps=steps, total_steps=total_steps)
        got_recipe = dict(res0.get("recipe") or {})
        diff = {k: [got_recipe.get(k), want[k]] for k in want if got_recipe.get(k) != want[k]}
        if diff:
            raise SystemExit(
                "⛔ 续跑的**口径/日程**与存档不一致（接着跑出来的不是同一条轨迹）："
                + json.dumps(diff, ensure_ascii=False)
                + " —— 要换参数就**重新起一段**（`--init` 给这份权重、不带 `--resume-optimizer`）")
        sr = str(res0.get("stop_reason") or "")
        if sr and "--stop-after-steps" not in sr:
            raise SystemExit(
                f"这份存档是**早停收尾**的（{sr}）：KL 早停那一支在 `gstep += 1` **之前**就 break，"
                f"而 `opt.step()` 已经执行过 ⇒ 续跑会把同一步跑第二遍。⛔ 早停就是这一段的终点；"
                f"要接着练请重新起一段")
        opt.load_state_dict(res0["optimizer"])
        base_lrs = [float(x) for x in res0["base_lrs"]]
        for pg, lr in zip(opt.param_groups, base_lrs):
            pg["lr"] = lr
        gstep = int(c0["gstep"])
        start_epoch = int(c0["epoch"])
        start_skip = int(c0["step_in_epoch"])
        resume_cur_stage = c0.get("cur_stage")
        stage_steps.update({k: int(v) for k, v in (c0.get("stage_steps") or {}).items()})
        resume_acc = {"run": float(c0["accum"]["run"]), "seen": int(c0["accum"]["seen"]),
                      "run_parts": {k: float(v) for k, v in (c0["accum"]["run_parts"] or {}).items()}}
        best_val_ce = c0.get("best_val_ce")
        no_improve = int(c0.get("no_improve") or 0)
        ce_log = list(c0.get("ce_log") or [])
        # 分段跑的 metrics 要能拼成**链条**（否则"续跑"在台账里看着像另一轮）：history 前缀 +
        # 链条起点的行为策略基线（`il_init` 是"开训前那一份网"的读数 ⇒ 续跑沿用链条起点，
        # ⛔ 别用"这一段开跑前"的数，那会让台账里那条判据悄悄换尺子）。
        history = list(res0.get("history") or [])
        chain = res0.get("chain") or {}
        if chain.get("il_init_student") is not None:
            il_init = chain["il_init_student"]
            il_init_all = chain.get("il_init_all")
            il_rand = chain.get("il_rand")
        _restore_rng(res0["rng"], rng_gen)
        if stop_after and stop_after <= gstep:
            raise SystemExit(f"`--stop-after-steps {stop_after}` ≤ 存档的 gstep {gstep}"
                             f" ⇒ 本次一步都不跑（要接着走就把它调大）")
        if start_epoch > args.epochs:
            raise SystemExit(f"存档已经跑满 `--epochs {args.epochs}`（存档停在第 {start_epoch} 个 "
                             f"epoch 的开头）⇒ 没有可续的步；要接着练就把 `--epochs` 调大并"
                             f"**重新起一段**")
        n_adam = len(res0["optimizer"].get("state") or {})
        print(f"续跑：从 epoch {start_epoch} 的第 {start_skip} 步接着跑（gstep {gstep}/{total_steps}）；"
              f"Adam 状态已恢复（{n_adam} 个参数张量的动量/方差）；RNG 四路已恢复"
              f"（torch/numpy/python/掩码生成器）")
    cut_epoch: tuple | None = None      # `--stop-after-steps` 命中在 epoch **中间**时的累加器
    last_epoch = start_epoch - 1        # 最后一个**跑完**的 epoch（分段存档要用它定位续跑点）
    cur_perm_sha: str | None = None
    for epoch in range(start_epoch, args.epochs + 1):
        if stop_after and gstep >= stop_after:
            stop_reason = (f"--stop-after-steps {stop_after}：已跑到全局第 {gstep} 步（在 epoch "
                           f"{epoch} 的边界上）⇒ 正常收尾并落盘")
            print(f"  !! {stop_reason}")
            break
        rng = np.random.default_rng(args.seed + epoch)
        perm = rng.permutation(n)
        # ⚠ **数据顺序是判据的一部分**：续跑的当前 epoch 必须与存档记录的洗牌结果**逐字节**相同
        #   （⛔ 不靠"种子一样所以顺序应该一样" —— 那正是假绿的来源）。
        if resume is not None and epoch == start_epoch and start_skip > 0:
            got_perm = _perm_sha256(perm)
            if got_perm != str(c0["perm_sha256"]):
                raise SystemExit(
                    f"续跑的**数据顺序**与存档不一致（epoch {epoch}）：存档 "
                    f"{str(c0['perm_sha256'])[:16]}… vs 本次 {got_perm[:16]}…"
                    f" —— 数据/种子/步数任一处不同都会这样，⛔ 不许接着跑")
            print(f"数据顺序核对：epoch {epoch} 的洗牌指纹 {got_perm[:16]}… == 存档（逐字节相同）")
        cur_perm_sha = _perm_sha256(perm)
        model.train()
        ssl_head.train()
        if resume is not None and epoch == start_epoch and start_skip > 0:
            run, seen = resume_acc["run"], resume_acc["seen"]
            run_parts: dict[str, float] = dict(resume_acc["run_parts"])
            cur_stage = resume_cur_stage
            done_in_epoch = start_skip
        else:
            run, seen = 0.0, 0
            run_parts = {}
            cur_stage = None
            done_in_epoch = 0
        for step in range(done_in_epoch, steps):
            if stop_after and gstep >= stop_after:
                stop_reason = (f"--stop-after-steps {stop_after}：跑到全局第 {gstep} 步"
                               f"（epoch {epoch} 内已走 {done_in_epoch} 步）⇒ 正常收尾并落盘")
                print(f"  !! {stop_reason}")
                # 半截 epoch：**不** evaluate、**不** append history（那会让"分段 vs 一口气"
                # 的 epoch 读数行对不上）；累加器与步号原样进存档
                cut_epoch = (epoch, done_in_epoch, run, seen, dict(run_parts))
                break
            stage = _stage_of(gstep, total_steps, args.stage_a, args.stage_b)
            stage_steps[stage] += 1
            if stage != cur_stage:
                cur_stage = stage
                # b 段冻结主干（头是新的，先在固定表示上收敛）；a/c 段解冻
                # ⚠ `--freeze-trunk` 时**全程**冻结（阶段 b 的语义被推广到整轮）
                for p in trunk:
                    p.requires_grad = (not freeze_trunk) and stage != "b"
                print(f"  -- 进入阶段 {stage}（step {gstep}/{total_steps}）")
            # c 段 lr 调度（a/b 段保持常数）；`--stage-c-lr-mult` 再整体缩一档。
            # ⚠ 缺省 `cosine` 分支**逐字保留老表达式**（0.1 + 0.9·½(1+cos)）⇒ 旧路径逐字节不变。
            if stage == "c":
                if lr_sched == "constant":
                    for pg, base in zip(opt.param_groups, base_lrs):
                        pg["lr"] = base * args.stage_c_lr_mult
                else:
                    c_start = int(total_steps * (args.stage_a + args.stage_b))
                    prog = max(0.0, min(1.0, (gstep - c_start) / max(1, total_steps - c_start)))
                    if lr_sched == "cosine":
                        for pg, base in zip(opt.param_groups, base_lrs):
                            pg["lr"] = base * args.stage_c_lr_mult * (
                                0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * prog)))
                    else:                                   # floored-cosine
                        shape = lr_floor + (1.0 - lr_floor) * 0.5 * (1 + math.cos(math.pi * prog))
                        for pg, base in zip(opt.param_groups, base_lrs):
                            pg["lr"] = base * args.stage_c_lr_mult * shape
            idx = np.sort(perm[step * args.batch:(step + 1) * args.batch])
            if idx.size == 0:
                break
            b = _batch(train_data, idx, device, ablate=ablate or None,
                       aux_own_win=own_win_head is not None)
            if adv_tr is not None:
                b["vtarget"] = torch.from_numpy(vtar_tr[idx]).to(device)
            # ★ W4：这一批的**引擎最优候选**下标（逐行取好；`il_w=0` 时只用来报 `il_ce` 读数）
            il_b = None if il_tr is None else torch.from_numpy(il_tr[idx]).to(device)
            if il_b is not None:
                b["il_target"] = il_b
            ssl = None
            if stage in ("a", "c") and args.mask_frac > 0:
                evt_masked, mask_rows, target = mask_events(b["evt"], args.mask_frac, rng_gen)
                b["evt"] = evt_masked
                ssl = (ssl_head, mask_rows, target)
            out = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"], h=b.get("h"),
                        n_legal=b["nlegal"])
            kb = None if keep_tr is None else keep_tr[idx].to(device)
            wb = None if w_tr is None else w_tr[idx].to(device)
            ppo = None
            if logp_tr is not None:
                ppo = (logp_tr[idx].to(device), ppo_cfg[0], ppo_cfg[1])
            adv_b = None if adv_tr is None else torch.from_numpy(adv_tr[idx]).to(device)
            ref_b = None if ref_np is None else torch.from_numpy(ref_np[idx]).to(device)
            # ⚠ 优势**只归一化一次**：`adv_tr`（手级路径）已在 `_hand_advantage` 里按学生行全局
            #   z-score 过 ⇒ 这里 `norm="none"`；遗留路径（V 每步重算的 final/rtg 口径）才是批内。
            loss, parts = compute_loss(out, b, weights if only else (STAGE_WEIGHTS.get(stage) or weights),
                                       ssl, row_keep=kb, row_w=wb, ppo=ppo, value_key=value_key,
                                       policy_temp=p_temp, adv_fixed=adv_b,
                                       adv_norm=("none" if adv_tr is not None else "batch"),
                                       ref=(None if ref_b is None else (ref_beta, ref_b)),
                                       il=(None if il_b is None else (il_w, il_b)),
                                       rho=rho_cfg,
                                       # ★ MV-1：辅助头 `own_win`（`None` ⇒ 那一项一行都不走）
                                       aux_own=(None if own_win_head is None
                                                else (own_win_head(out["state"]), aux_own_w,
                                                      aux_own_rows)))
            # ⚠ **发散就停**（2026-09-28 加）：NaN 的 loss 会让整网变成 NaN 参数，而训练"照跑完"
            #   并落盘一份废 checkpoint（`#0.5` 那次就是）。宁可当场报错，也不要产出一个
            #   看着跑完、实际是首合法基线的模型。
            if not math.isfinite(float(loss)):
                raise SystemExit(f"训练发散：第 {gstep} 步 loss={float(loss)}（epoch {epoch}）—— "
                                 f"诊断看 train_parts 的 kl / clip_frac / logp_gap；不落盘废 checkpoint")
            opt.zero_grad(set_to_none=True)
            loss.backward()
            gn = _clip_grad(opt, float(getattr(args, "grad_clip", 0.5) or 0.0))
            opt.step()
            if gn == gn:                                   # 非 NaN ⇒ 记下裁剪前范数
                parts["grad_norm"] = gn
            # **KL 早停**：越过阈值就停整轮（不是只停这个 epoch —— 离线单轮本来就 1~2 个 epoch，
            # 而"离行为策略太远"这件事跨 epoch 只会更严重）。
            if kl_stop_hit(float(parts.get("kl", float("nan"))),
                           threshold=float(getattr(args, "kl_early_stop", 0.03) or 0.0),
                           step=gstep, min_steps=kl_min_eff):
                stop_reason = (f"KL 早停：step {gstep} 的 KL(π_old‖π_new)="
                               f"{parts.get('kl', float('nan')):.4f} > "
                               f"{float(getattr(args, 'kl_early_stop', 0.03)):g}"
                               f"（kl_inv={parts.get('kl_inv', float('nan')):.4f}）")
                print(f"  !! {stop_reason}")
                break
            if dc:
                dc.tick()
            run += float(loss.detach()) * idx.size
            seen += idx.size
            for k, v in parts.items():
                run_parts[k] = run_parts.get(k, 0.0) + v * idx.size
            gstep += 1
            done_in_epoch = step + 1          # ★ BL-04：续跑要用它定位"这一轮走到第几步"
            # ★ **验证集早停**（防过拟合）：每 `--val-every` 步在**固定子集**上读一次 train/val 的
            #   policy CE。判据是"val CE 相对**历史最好值**没有再降（容忍 `CE_IMPROVE_TOL`）"，
            #   连续 `--val-patience` 次 ⇒ 停这一轮。**正常收尾**：只置 `stop_reason` 后 break，
            #   下面照常 `evaluate` / 追加 `history` / 落盘 ckpt（不是异常退出）。
            if val_every > 0 and gstep % val_every == 0:
                # ⚠ `evaluate()` 会把 model 置 eval ⇒ 读完必须放回 train（见 `ce_probe` 的注释）
                model.eval()
                t_ce = ce_probe(model, train_data, device, rows=tr_probe, batch=args.eval_batch,
                                ssl_head=ssl_head, mask_frac=args.mask_frac, value_key=value_key,
                                ablate=ablate or None, metric=val_metric, il_target=il_tr)
                v_ce = ce_probe(model, val_data, device, rows=va_probe, batch=args.eval_batch,
                                ssl_head=ssl_head, mask_frac=args.mask_frac, value_key=value_key,
                                ablate=ablate or None, extra=val_extra, metric=val_metric,
                                il_target=il_va)
                model.train()
                ssl_head.train()
                improved = best_val_ce is None or v_ce < best_val_ce - CE_IMPROVE_TOL
                if improved:
                    best_val_ce = v_ce
                    no_improve = 0
                else:
                    no_improve += 1
                ce_log.append({"step": gstep, "train_ce": t_ce, "val_ce": v_ce,
                               "best_val_ce": best_val_ce, "no_improve": no_improve})
                # 两条线一起打：train CE 还在降而 val CE 抬头 = "开始记数据"（不只是看单个数）
                print(f"  [val] step {gstep:>5}（{val_metric} CE）: train {t_ce:.4f} | val {v_ce:.4f}"
                      f"（最好 {best_val_ce:.4f}，连续 {no_improve} 次未改善）"
                      f" ⇒ {'还在学' if improved else '可能开始记数据'}")
                if no_improve >= val_patience:
                    stop_reason = (f"验证集 {val_metric} CE 连续 {no_improve} 次未改善 ⇒ 早停（防过拟合）"
                                   f"：step {gstep} 的 val {val_metric} CE {v_ce:.4f}（最好 {best_val_ce:.4f}）"
                                   f"、train {t_ce:.4f}")
                    print(f"  !! {stop_reason}")
                    break
        if cut_epoch is not None:
            # ★ BL-04：`--stop-after-steps` 命中在 epoch 中间 ⇒ **不产出**半个 epoch 的读数行，
            # 直接带着累加器去落盘（续跑那一段会把这一轮补完 ⇒ 日志与一口气跑逐字相同）
            break
        if getattr(args, "skip_val_eval", False):
            # ⚠ **只给冒烟**（省 ~4.3 s/次）：val 读数全 = nan，epoch 行与 metrics 逐字段都变 ——
            #   ⛔ 正式跑不许开（开了就没有 val 读数的可比性）。缺省 False ⇒ 下面那段一行不变。
            ev = {"n": 0, "top1": float("nan"), "first_legal_acc": float("nan"),
                  "loss": float("nan"), "by_type": {}}
            ev_stu = None
            print(f"  ⚠ `--skip-val-eval`：epoch {epoch} 末的 val 评估被跳过（读数 = nan）")
        else:
            ev = evaluate(model, val_data, device, batch=args.eval_batch, ssl_head=ssl_head,
                          mask_frac=args.mask_frac, value_key=value_key, ablate=ablate or None,
                          extra=val_extra, il_target=il_va)
            ev_stu = (evaluate(model, val_data, device, batch=args.eval_batch, row_keep=keep_va,
                               value_key=value_key, ablate=ablate or None, extra=val_extra,
                               il_target=il_va)
                      if keep_va is not None else None)
        row = {"epoch": epoch, "stage_at_end": cur_stage, "train_loss": run / max(1, seen),
               "train_parts": {k: v / max(1, seen) for k, v in run_parts.items()},
               "train_student_frac": frac_tr, "val_student_frac": frac_va,
               "val_top1_student": (ev_stu["top1"] if ev_stu else None),
               "val_top1_student_n": (ev_stu["n"] if ev_stu else None),
               # W4：**引擎一致率**（全行 / 学生行）+ 随机基线 + 行为策略基线（开训前那一份）
               "val_engine_top1": ev.get("engine_top1"),
               "val_engine_top1_student": (ev_stu.get("engine_top1") if ev_stu else None),
               "val_engine_random": ev.get("engine_random"),
               "il_init_engine_top1_student": il_init,
               **{f"val_{k}": v for k, v in ev.items() if k != "by_type"},
               "val_by_type": ev.get("by_type", {})}
        history.append(row)
        print(f"epoch {epoch:>3} [{cur_stage}]: train {row['train_loss']:.4f} | val top1(教师一致) "
              f"{ev.get('top1', float('nan')):.3f} | 首合法基线 "
              f"{ev.get('first_legal_acc', float('nan')):.3f} | "
              f"policy {ev.get('loss_policy', float('nan')):.3f} "
              f"value {ev.get('loss_value', float('nan')):.3f} "
              f"place {ev.get('loss_placement', float('nan')):.3f} "
              f"bh {ev.get('loss_belief_hand', float('nan')):.3f} "
              f"bt {ev.get('loss_belief_tenpai', float('nan')):.3f} "
              f"danger {ev.get('loss_danger', float('nan')):.3f} "
              f"effect {ev.get('loss_effect', float('nan')):.4f} "
              f"ssl_acc {ev.get('ssl_acc', float('nan')):.3f}"
              + (f" | **学生行 top1 {ev_stu['top1']:.3f}**(n={ev_stu['n']})" if ev_stu else "")
              + (f" | 学生行 policy {ev_stu['loss_policy']:.3f}" if ev_stu else "")
              # ★ W4 的**低噪判据**：引擎一致率（学生行）—— 括号里是**开训前**的行为策略基线
              + (f" | **引擎一致率 {ev_stu.get('engine_top1'):.3f}**"
                 f"(init {il_init:.3f} / 随机 {ev.get('engine_random'):.3f})"
                 if (ev_stu and ev_stu.get("engine_top1") is not None) else "")
              + (f" | 引擎 CE(学生行) {ev_stu.get('loss_il_ce'):.4f}"
                 if (ev_stu and ev_stu.get("loss_il_ce") is not None) else "")
              + (f" | KL {row['train_parts'].get('kl', float('nan')):.4f}"
                 f"/{row['train_parts'].get('kl_inv', float('nan')):.4f}"
                 f" clip {row['train_parts'].get('clip_frac', float('nan')):.3f}"
                 f" |A| {row['train_parts'].get('adv_abs', float('nan')):.2f}"
                 f" gap {row['train_parts'].get('logp_gap', float('nan')):.2f}"
                 f" |g| {row['train_parts'].get('grad_norm', float('nan')):.2f}"
                 if "kl" in row["train_parts"] else "")
              # W3 的锚项：数值 + **它对总损失的占比**（占比 ≪1% ⇒ β 太小、管不住这一轮）
              + (f" | ref_kl={row['train_parts'].get('ref_kl', float('nan')):.4f}"
                 f"（β={ref_beta:g}，占总损失 "
                 f"{100 * row['train_parts'].get('ref_kl_share', float('nan')):.1f}%）"
                 if ref_beta > 0 else "")
              # 验证集早停的两条线也进 epoch 行：一眼看出"还在学"还是"开始记数据"
              + (f" | CE({val_metric}: train {ce_log[-1]['train_ce']:.4f} / val {ce_log[-1]['val_ce']:.4f})"
                 if ce_log else ""))
        print("        按类型：" + "  ".join(
            f"{t}={d['top1']:.2f}(n={d['n']})" for t, d in sorted(ev["by_type"].items())))
        # ★ BL-14：**ρ 的分布**（判据 3 的原始输出）—— 每一轮都打，不是只在出问题时打。
        if rho_cfg is not None:
            rs = rho_summary(rho_cfg[2])
            row["rho"] = rs
            print(f"        ρ（重要性比，上限 {rho_cfg[0]:g} / 动作 {rho_cfg[1]}；**双边**判据）："
                  f"p50={rs.get('rho_p50', float('nan')):.4f} "
                  f"p90={rs.get('rho_p90', float('nan')):.4f} "
                  f"p99={rs.get('rho_p99', float('nan')):.4f} · "
                  f"min={rs.get('rho_min')} max={rs.get('rho_max'):.4g} · "
                  f"max(ρ,1/ρ)={rs.get('rho_worst_two_sided'):.4g} · "
                  f"超限行 {rs['n_gt_rho_max']}/{rs['n_rows']} · "
                  f"超 LOG_RATIO_CLAMP 行 {rs['n_gt_log_ratio_clamp']} · "
                  f"已夹批数 {rs['clipped_batches']}")
        last_epoch = epoch
        if stop_reason:
            break
    if mon:
        mon.close()
    wall = time.perf_counter() - t0

    # ---- ★ BL-04：这一段的**续跑点**（`--stop-after-steps` 半截 ⇒ 累加器原样带走）------------
    if cut_epoch is not None:
        seg_epoch, seg_step = int(cut_epoch[0]), int(cut_epoch[1])
        seg_acc: dict = {"run": float(cut_epoch[2]), "seen": int(cut_epoch[3]),
                         "run_parts": dict(cut_epoch[4])}
    else:
        seg_epoch, seg_step = int(last_epoch) + 1, 0
        seg_acc = {"run": 0.0, "seen": 0, "run_parts": {}}

    out = paths.allocate("ckpt", args.label, need_bytes=16 * 1024**2)
    extra: dict = {"args": _recorded_args(args), "stage_steps": stage_steps}
    if save_optimizer:
        # **行为策略标识**：续跑闸门的三把尺子里有两把（链条起点 kl0 / 连续性探针 kl_probe）要用
        # 存档那一刻的量 ⇒ 探针**在这里**现算（模型已是这一段的最终权重，与续跑时的重算严格同源）。
        if beh_rec is not None and logp_tr is not None:
            probe_idx = np.arange(min(64, n))
            beh_rec = {**beh_rec, "probe_n": int(probe_idx.size),
                       "kl_probe": _behaviour_kl(model, train_data, probe_idx, logp_tr,
                                                 p_temp, device)}
        extra[RESUME_KEY] = {
            "format": RESUME_FORMAT,
            "optimizer": opt.state_dict(),
            "base_lrs": [float(x) for x in base_lrs],
            "weights_sha256": _state_sha256(model.state_dict(), ssl_head.state_dict(),
                                            _own_win_sd(own_win_head)),
            "counters": {
                "gstep": int(gstep), "epoch": int(seg_epoch), "step_in_epoch": int(seg_step),
                "steps": int(steps), "total_steps": int(total_steps), "n": int(n),
                "batch": int(args.batch), "seed": int(args.seed),
                "cur_stage": (cur_stage if seg_step > 0 else None),
                "stage_steps": {k: int(v) for k, v in stage_steps.items()},
                "accum": seg_acc,
                "perm_sha256": (cur_perm_sha if seg_step > 0 else None),
                "best_val_ce": best_val_ce, "no_improve": int(no_improve), "ce_log": ce_log,
            },
            "requires_grad": {
                **{f"model.{k}": bool(v.requires_grad) for k, v in model.named_parameters()},
                **{f"ssl.{k}": bool(v.requires_grad) for k, v in ssl_head.named_parameters()},
                # ★ MV-1：辅助头只在它存在时进（关着 ⇒ 这一项是空字典 ⇒ 存档逐字节不变）
                **({} if own_win_head is None else
                   {f"own.{k}": bool(v.requires_grad)
                    for k, v in own_win_head.named_parameters()})},
            "rng": _rng_fingerprint(rng_gen),
            "behaviour": beh_rec,
            # ★ BL-04（第七十九轮）：`π_ref` 的**来源 + 权重指纹**。少了它，续跑那一段的锚会被按
            #   「本段的 `--init`」现推 ⇒ `--ref-beta > 0` 时每段换一次参考策略（静默，只体现在权重上）。
            "ref": {"beta": float(ref_beta), "source": ref_src, "sha256": ref_sha,
                    "explicit": bool(ref_explicit) if ref_beta > 0 else False},
            "recipe": _resume_recipe(args, resolved_data=str(Path(args.data).resolve()), n=n,
                                     steps=steps, total_steps=total_steps),
            "stop_reason": stop_reason,
            "history": history,
            # 链条起点的读数（续跑时沿用 ⇒ 台账里"开训前那一份网"始终指**链条**起点）
            "chain": {"il_init_student": il_init, "il_init_all": il_init_all, "il_rand": il_rand},
        }
    save_checkpoint(out / "model.pt", model, ssl_head, meta, weights, extra,
                    own_win_head=own_win_head)
    result = {
        "label": args.label, "data": str(args.data), "dataset_meta": meta,
        "params": model.param_count(), "device": device, "torch_threads": threads,
        "args": _recorded_args(args),
        "epochs": args.epochs, "batch": args.batch, "steps_per_epoch": steps,
        "stage_steps": stage_steps, "wall_seconds": wall, "history": history,
        "stop_reason": stop_reason, "grad_clip": float(getattr(args, "grad_clip", 0.5) or 0.0),
        # ★ BL-14：**多行为口径的台账**（只在真走那条路时多这几个键 —— 缺省关 ⇒ 逐字节不变）。
        #   为什么必须落进 `metrics.json`（而不是只打印）：回路编排器（`overlap-orchestrator`
        #   的 `--rounds`）要按**机器可读**的方式记「这一轮的 θ 指纹 / D 指纹 / per-id 复算 /
        #   陈旧度 / ρ 统计」；从日志里正则捞 = 第二个口径，早晚漂移。
        **({"behaviour_multi": {
            "table_digest": beh_gate["table_digest"],
            "matched_ids": beh_gate["matched_ids"],
            "init_sha256": beh_gate["init_sha256"],
            "probe_rows_per_id": beh_gate["probe_rows_per_id"],
            "per_id": beh_gate["per_id"],
            "stale_kl_budget": float(getattr(args, "stale_kl_budget", 0.0) or 0.0),
            "stale_over_budget": [b for b, e in beh_gate["per_id"].items()
                                  if e.get("verified") and not e.get("matched_init")
                                  and float(getattr(args, "stale_kl_budget", 0.0) or 0.0) > 0
                                  and e.get("kl_stale") is not None
                                  and abs(float(e["kl_stale"]))
                                  > float(getattr(args, "stale_kl_budget", 0.0) or 0.0)],
            "rho_max": float(getattr(args, "rho_max", 0.0) or 0.0),
            "rho_max_action": str(getattr(args, "rho_max_action", "fail") or "fail"),
        },
           "rho": (rho_summary(rho_cfg[2]) if rho_cfg is not None else None),
           "behaviour_identity": beh_rec}
           if beh_gate is not None else {}),
        # ★ BL-04：续跑的那一段**只在真续跑时**多这几个键（缺省关 ⇒ `metrics.json` 逐字节不变）
        **({"resumed_from": resume["path"], "resume_entry_gstep": int(entry_gstep),
            "resume_state_written": bool(save_optimizer),
            "resume_point": {"epoch": int(seg_epoch), "step_in_epoch": int(seg_step)}}
           if resume is not None else {}),
        "kl_early_stop": float(getattr(args, "kl_early_stop", 0.03) or 0.0),
        "adv_norm": ("global" if adv_tr is not None else
                     ("batch" if objective == "ppo" else "n/a")),
        # W1b：这一轮**有没有**长程 carry 进前向（台账里一眼可见；重建数据集是唯一修复手段）
        "has_h0": bool(has_h0),
        "h0_dim": (int(train_data["h0"].shape[1]) if has_h0 else None),
        "no_carry": bool(no_carry),
        # W3（对现任的 KL 锚）：β 与**这一轮实测的** `ref_kl`（最后一个 epoch 的学生行均值）。
        # 台账里"β=0.5 那一轮到底离现任多远"就是一个数；β=0 时是 None（那一项压根没算）。
        "ref_beta": float(ref_beta),
        # ⚠ 记的是**这一轮真正载进去的那一份**（续跑时 = 存档记录的那一份，不是本段的 `--init`）——
        #   台账里这一行必须与 π_ref 的实际来源一致，否则"续跑换了锚"在 metrics 里看不出来。
        "ref_source": (ref_src if ref_beta > 0 else None),
        "ref_weights_sha256": (ref_sha if ref_beta > 0 else None),
        "ref_kl": (history[-1]["train_parts"].get("ref_kl") if (history and ref_beta > 0) else None),
        "ref_kl_mean": (float(np.mean([h["train_parts"]["ref_kl"] for h in history
                                       if "ref_kl" in h["train_parts"]]))
                        if (history and ref_beta > 0) else None),
        # 验证集早停（防过拟合）：开关 + **两条 CE 的完整轨迹**（报告要用；只打屏会丢）
        "val_every": int(val_every), "val_patience": int(val_patience),
        # ★ W4：早停读的是哪个头（`policy` 缺省 = 与 W3 逐位一致；`il` = 引擎标签的 CE）
        "val_metric": val_metric,
        "val_ce_checks": ce_log,
        "val_best_ce": best_val_ce,
        "final_val_top1": history[-1]["val_top1"] if history else None,
        # ★ W4 第一步（逐候选引擎标签）：权重 + **一致率的四个数**（低噪判据；报告的关键读数）。
        #   `init` = 开训前那一份 `--init` 网（= 行为策略基线）；`random` = `1/平均候选数`；
        #   判据是 `final_student` **显著高于** `init_student`（student 口径 = 与策略损失同一个 row_keep）。
        "il_weight": float(il_w),
        "il_target_source": (f"cand.derived[{_il_span[1]}:{_il_span[1] + _il_n}]" if il_tr is not None
                             else None),
        "il_engine_top1_init_student": il_init,
        "il_engine_top1_init_all": il_init_all,
        "il_engine_random": il_rand,
        "il_engine_top1_final_student": (history[-1]["val_engine_top1_student"] if history else None),
        "il_engine_top1_final_all": (history[-1]["val_engine_top1"] if history else None),
        "il_loss_final_student": (history[-1]["train_parts"].get("il_ce") if history else None),
        "stages": {"a": "policy+effect（主干先会打牌）", "b": "冻主干只训头", "c": "联合微调（余弦降 lr）",
                   "ssl": f"掩码事件重建 frac={args.mask_frac} weight={args.ssl_weight}",
                   "il": f"引擎逐候选标签的模仿项 weight={il_w:g}（只在学生行）"},
    }
    (out / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(f"\ncheckpoint：{out / 'model.pt'}；指标：{out / 'metrics.json'}（{wall:.1f}s）")
    return result


import functools   # 只给下面两个 lru_cache 用（放这里是为了**前面一行都不动**）

# ================================================================ 零算力跨臂缓存（MV-1 效率包 ①）
#
# π_ref 的 logits（`_ref_logits`）与 V_old（`_behaviour_values`）**只取决于「数据 + 权重 +
# 前向口径」**，与臂名 / 训练步数 / 辅助头无关 ⇒ 两臂共用同一份时可以把它们读回来。
# ⛔ **缺省关闭**（`--cache-ref-logits` 不传 ⇒ 下面这些函数一个都不进，缺省路径逐字节不变）；
# 打开后**键不匹配/文件损坏一律拒用并重算**（fail-closed），写走 `.tmp` + `os.replace`（原子）。
# 实现与判据见 `python/mahjong_ml/v4/refcache.py` 与 `lab/mv1/EFFICIENCY-AUDIT.md`「实现结果」。


def _cache_dir(args) -> Path | None:
    """`--cache-ref-logits <dir>` 的目录；**不传 ⇒ `None`**（= 缺省路径，一行缓存逻辑都不走）。"""
    d = getattr(args, "cache_ref_logits", None)
    if not d:
        return None
    return Path(str(d)).expanduser().resolve()


@functools.lru_cache(maxsize=16)
def _manifest_cached(data_dir: str, split: str) -> str:
    """切分的数据指纹（同一进程里只算一次；两次臂共用时省掉一次 ~0.6 s 的读盘）。"""
    return _refcache.split_manifest_sha256(data_dir, split)


@functools.lru_cache(maxsize=4)
def _code_fingerprint_cached() -> str:
    here = Path(__file__).resolve().parent
    return _refcache.code_fingerprint(Path(__file__).resolve(), here / "model.py",
                                      here / "dataset.py", here / "spec.py", here / "export.py")


def _artifact_key(args, kind: str, *, weights_sha: str, data_dir: str, splits: tuple[str, ...],
                  device: str, batch: int, extra: dict | None = None) -> tuple[str, dict]:
    """键字典（逐字段钉死）。⛔ `--label` **刻意不进来**（它只决定 ckpt 目录名）。

    字段清单：`data_sha256`（逐切分的数据指纹）· `init_sha256`（`--init` 文件 sha256）·
    `weights_sha256`（**真正产生它的那份网**的 state 指纹）· `temp`（`--behaviour-temp`）·
    `seed` · `code_sha256`（pretrain/model/dataset/spec/export 五份源码）· `torch` · `device` ·
    `batch`（批大小会改 CPU 归约顺序 ⇒ 必须钉住）· `n`/`lmax` · `extra`（消融块等）。
    """
    init_spec = getattr(args, "init", None)
    fields = {
        "data_sha256": {s: _manifest_cached(data_dir, s) for s in splits},
        "init_sha256": (_sha256_file(resolve_weight_path(str(init_spec))) if init_spec else None),
        "weights_sha256": str(weights_sha),
        "temp": float(getattr(args, "behaviour_temp", 1.0) or 1.0),
        "seed": int(getattr(args, "seed", 0) or 0),
        "code_sha256": _code_fingerprint_cached(),
        "torch": torch.__version__,
        "device": str(device),
        "batch": int(batch),
        "n": {s: int(_n_of_split(data_dir, s)) for s in splits},
        "extra": extra or {},
    }
    return _refcache.make_key(kind, fields)


def _n_of_split(data_dir: str, split: str) -> int:
    p = Path(data_dir) / f"{split}.nlegal.npy"
    return int(np.load(p, mmap_mode="r", allow_pickle=False).shape[0])


def _cached_ref_logits(args, model, data: dict, device: str,
                       ablate: dict[str, list[tuple[int, int]]] | None) -> np.ndarray:
    """`_ref_logits()` 的缓存壳：命中就读回（**逐位等于现算**），否则现算并原子落盘。"""
    cache_dir = _cache_dir(args)
    if cache_dir is None:
        return _ref_logits(model, data, device, args.eval_batch, ablate=ablate)
    n = int(data["nlegal"].shape[0])
    lmax = int(data["cand"].shape[1])
    key, kd = _artifact_key(args, "pi_ref_logits", weights_sha=_state_sha256(model.state_dict()),
                            data_dir=str(Path(args.data).resolve()), splits=("train",),
                            device=device, batch=args.eval_batch,
                            extra={"ablate": {k: [list(x) for x in v]
                                              for k, v in (ablate or {}).items()},
                                   # ★ C2：池化口径会改 state 级头（value/belief）的输出 ⇒ 必须进键，
                                   #   否则同一缓存目录下 `--legacy-pool` 与缺省会**静默混用**两份 logits。
                                   "legacy_pool": bool(M.LEGACY_POOL),
                                   # ★ C1：`fusion.out` 去不去 ReLU 会改 **policy** 的 logits（以及
                                   #   value）⇒ 同样必须进键，否则会静默复用旧口径算出的 π_ref。
                                   "legacy_out_relu": bool(M.LEGACY_OUT_RELU),
                                   # ★ B3：LN+残差门会改 **policy** 的 logits（以及 danger/effect）
                                   #   ⇒ 同一理由进键，否则会静默复用旧口径算出的 π_ref。
                                   "legacy_u_res": bool(M.LEGACY_U_RES),
                                   # ★ #0：身份嵌入会改 `tile` 塔（⇒ policy/value 都变）⇒ 进键，
                                   #   否则同一缓存目录会静默复用"没有 tile_id"的 π_ref。
                                   "legacy_tile_id": bool(M.LEGACY_TILE_ID),
                                   # ★ A−（T1）：整张图变了 ⇒ π_ref 的 logits 全变 ⇒ 必须进键
                                   "arch": str(M.ARCH),
                                   "lmax": lmax, "rows": n})
    got = _refcache.load(cache_dir, "pi_ref_logits", key, kd)
    if got is not None:
        arr = got.get("logits")
        if isinstance(arr, np.ndarray) and arr.shape == (n, lmax) and arr.dtype == np.float32:
            return np.ascontiguousarray(arr)
        print(f"[cache] ⛔ 形状/dtype 对不上（要 ({n}, {lmax}) float32，"
              f"给的是 {None if arr is None else (arr.shape, arr.dtype)}）⇒ 拒用并重算")
    arr = _ref_logits(model, data, device, args.eval_batch, ablate=ablate)
    _refcache.store(cache_dir, "pi_ref_logits", key, kd, {"logits": np.ascontiguousarray(arr)})
    return arr


def _cached_behaviour_values(args, model, train_data: dict, val_data: dict, device: str
                             ) -> tuple[np.ndarray, np.ndarray]:
    """`_behaviour_values()`（train+val）的缓存壳：一文件两数组，逐位等于现算。"""
    cache_dir = _cache_dir(args)
    if cache_dir is None:
        return (_behaviour_values(model, train_data, device, args.eval_batch),
                _behaviour_values(model, val_data, device, args.eval_batch))
    n = int(train_data["nlegal"].shape[0])
    nv = int(val_data["nlegal"].shape[0])
    key, kd = _artifact_key(args, "v_old", weights_sha=_state_sha256(model.state_dict()),
                            data_dir=str(Path(args.data).resolve()), splits=("train", "val"),
                            device=device, batch=args.eval_batch,
                            extra={"value_bins": int(M.value_bins()),
                                   "value_range": float(M.value_range()),
                                   # ★ A−（T1）：整张图变了 ⇒ `u`/state/value 全变 ⇒ 必须进键
                                   "arch": str(M.ARCH),
                                   # ★ C2：`V_old` 读 value 头 ⇒ 池化口径必须进键（同源理由见 `_cached_ref_logits`）
                                   "legacy_pool": bool(M.LEGACY_POOL),
                                   # ★ C1：value 头读 `state`（= 池化的 `u`）⇒ 同一理由，ReLU 口径进键
                                   "legacy_out_relu": bool(M.LEGACY_OUT_RELU),
                                   # ★ B3：`u` 的 LN+残差门同样改 `state`/value ⇒ 进键
                                   "legacy_u_res": bool(M.LEGACY_U_RES),
                                   # ★ #0：tile 塔变 ⇒ `u`/state 变 ⇒ value 变，同一理由进键
                                   "legacy_tile_id": bool(M.LEGACY_TILE_ID),
                                   "rows": [n, nv]})
    got = _refcache.load(cache_dir, "v_old", key, kd)
    if got is not None:
        tr, va = got.get("train"), got.get("val")
        if all(isinstance(a, np.ndarray) and a.shape == (m,) and a.dtype == np.float64
               for a, m in ((tr, n), (va, nv))):
            return np.ascontiguousarray(tr), np.ascontiguousarray(va)
        print(f"[cache] ⛔ 形状/dtype 对不上（要 ({n},) / ({nv},) float64）⇒ 拒用并重算")
    tr = _behaviour_values(model, train_data, device, args.eval_batch)
    va = _behaviour_values(model, val_data, device, args.eval_batch)
    _refcache.store(cache_dir, "v_old", key, kd,
                    {"train": np.ascontiguousarray(tr), "val": np.ascontiguousarray(va)})
    return tr, va



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
    # ★ MV-1 第九十九轮：**唯一旋钮 = lr 调度**（缺省 = 老行为；⛔ 不改 lr 数值本身）
    # `cosine` 的 floor 写死 0.1（= 老表达式），`floored-cosine` 才读 `--lr-floor-frac`。
    ap.add_argument("--lr-schedule", choices=["cosine", "constant", "floored-cosine"], default="cosine",
                    help="c 段的 lr 形状：cosine（缺省 = 0.1+0.9·½(1+cos πx)，老行为）/ "
                         "constant（恒定 = --lr，去掉退火）/ "
                         "floored-cosine（F+(1−F)·½(1+cos πx)，F = --lr-floor-frac）")
    ap.add_argument("--lr-floor-frac", type=float, default=0.1, metavar="F",
                    help="`--lr-schedule floored-cosine` 的 lr 下限（占 --lr 的比例，缺省 0.1）；"
                         "⚠ `--lr-schedule cosine` 下必须留缺省（0.1），否则报错")
    # ★ A−（T1，段 1）：拓扑开关。缺省 v4 = 现有图（逐位不变）；`a-` = 新图（`ARCH-REDESIGN` §2.2）。
    ap.add_argument("--arch", choices=[M.ARCH_V4, M.ARCH_A], default=M.ARCH_V4,
                    help="网络图：v4（缺省，现有图，逐位不变）/ a-（T1：34 轴卷积+轴向注意力、"
                         "95-token joint attention、value 81 bin、heads d=128）。"
                         "⚠ a- 与 v4 不能互相 warm-start；三端未同步 ⇒ 不得上线/上闸门")
    # ★ C2（第一百轮）：state 级头池化口径。缺省 = **新**（mask-aware，只对 n_legal 个合法槽求均值，
    #   与 Java/C++ 同分母）；开这个开关才回到旧的 `u.mean(1)`（分母 Lmax）—— 只用于逐位复现旧行为。
    ap.add_argument("--legacy-pool", action="store_true",
                    help="⛔ 只用来自证/复现：state 级头退回旧 `u.mean(dim=1)`（分母 = Lmax，含 padding 槽）。"
                         "缺省 = mask-aware 池化（只对真实 n_legal 个合法槽求均值；与 Java/C++ 前向同口径）")
    # ★ C1（第一百零一轮）：`fusion.out` 末端 ReLU。缺省 = **去掉**（`u` 可正可负）；
    #   开这个开关才回到旧的 `nn.ReLU()` 末端（`u ≥ 0`）—— 只用于逐位复现改动前。
    ap.add_argument("--legacy-out-relu", action="store_true",
                    help="⛔ 只用来自证/复现：`fusion.out` 末端保留旧 ReLU（u ≥ 0）。"
                         "缺省 = 去掉该 ReLU（C1；0 参数，net.bin 格式不变）")
    # ★ C3（第一百零二轮）：`mem` / `policy_gate` 的初始化。缺省 = **小非零** `N(0, 1e-3²)`；
    #   开这个开关才回到旧的全零初始化（出生时门控恒等）—— 只用于逐位复现改动前。
    ap.add_argument("--legacy-zero-init", action="store_true",
                    help="⛔ 只用来自证/复现：`fusion.mem` / `heads.policy_gate` 回到**全零**初始化"
                         "（g ≡ 1、h_evt 零影响）。缺省 = 小非零 N(0, 1e-3²)（C3；0 参数、格式不变）")
    # ★ B3（第一百零四轮）：`u` 的 LN + 零初始化逐维残差门（+576 参数）。缺省 = **开**；
    #   开这个开关才回到"没有这段残差"的前向图（连 LN 都不算）—— 只用于逐位复现改动前。
    ap.add_argument("--legacy-u-res", action="store_true",
                    help="⛔ 只用来自证/复现：不建 B3 的 `u_norm`/`u_res` 残差（逐位复现改动前）。"
                         "缺省 = LN(192)+零初始化逐维残差门（u_res=0 ⇒ 出生恒等；旧包逐位不变）")
    # ★ #0（第一百零六轮）：`TileTower` 的 34 行身份嵌入 `tile_id[34,48]`（+1,632 参数）。
    #   缺省 = **开**（新行为）；开这个开关才回到"没有身份嵌入"的旧前向图 —— 只用于逐位复现。
    ap.add_argument("--legacy-tile-id", action="store_true",
                    help="⛔ 只用来自证/复现：不建 `tile.tile_id` 身份嵌入（34 行不可区分）。"
                         "缺省 = 建（+1,632 参数；旧包缺张量 ⇒ 补 0 ⇒ 逐位不变）")
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
    # ---- ★ BL-14：有界重要性比 + 逐 id 复算闸门（**全部缺省关** ⇒ 旧路径逐字节不变）----
    ap.add_argument("--rho-max", type=float, default=0.0, metavar="RHO",
                    help="重要性比 ρ = π_new/π_old 的**预注册上限**（0 = 关，缺省）。"
                         "超限时按 `--rho-max-action` **响亮失败**或**显式降级 + 记账**；"
                         "⚠ 无论开关如何，ρ 的分布与超限行数都会逐轮上报"
                         "（原来只有 `LOG_RATIO_CLAMP=4` 的**静默**夹取）")
    ap.add_argument("--rho-max-action", choices=["fail", "account"], default="fail",
                    help="ρ 超 `--rho-max` 时怎么办：`fail`（缺省）= `SystemExit`（exit 1、无 traceback）；"
                         "`account` = 夹到 `ln rho_max` + 记账 + 打印超限行数（⛔ 不是静默裁剪）")
    ap.add_argument("--stale-kl-budget", type=float, default=0.30, metavar="KL",
                    help="★ 允许的**陈旧度预算**：逐 behaviour_id 的 `KL(π_id‖π_init)` 超过它就"
                         "**记账 + 响亮告警**（⛔ 不是硬拒：陈旧度是质量轴、不是口径轴）。"
                         "预注册值 0.30 = KL 早停阈值 0.03 的 10×")
    ap.add_argument("--behaviour-probe", type=int, default=4096, metavar="N",
                    help="逐 behaviour_id **复算**的探针行数（每 id 抽 N 行重算 logp 并比对）；"
                         "0 = 全部行（贵但最严）")
    ap.add_argument("--allow-unverified-behaviour", action="store_true",
                    help="允许**身份未验证**（`sha256=null`）的行为：那些行被**显式排除**出策略损失"
                         "+ 记账 + 响亮打印。⚠ 缺省**不**允许 ⇒ `SystemExit`（fail-closed）")
    ap.add_argument("--behaviour-multi", action="store_true",
                    help="★ 走**多行为（混代）** PPO 路径：逐 `behaviour_id` 重算 `logp_old` + "
                         "逐 id 复算闸门 + 陈旧度预算。⚠ 缺省**关**（旧单策略路径逐字节不变）；"
                         "⚠ 数据集混了多个 id 却不开它 ⇒ **当场拒跑**（fail-closed）")
    ap.add_argument("--ppo-entropy", type=float, default=0.01, help="熵奖励系数（学生行上）")
    ap.add_argument("--grad-clip", type=float, default=0.5,
                    help="梯度总范数裁剪（v3 同口径 0.5；0 = 关）。日志里的 `|g|` 是**裁剪前**的范数")
    ap.add_argument("--kl-early-stop", type=float, default=0.03,
                    help="KL(π_old‖π_new) 的早停阈值（v3 同口径 0.03；0 = 关）—— 越过就停整轮")
    ap.add_argument("--kl-min-steps", type=int, default=100,
                    help="KL 早停生效前至少跑多少步（第一批的 Monte-Carlo KL 噪声大，别拿它判生死）")
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
                    help="从这份权重起步（ckpt / net.bin / 含 model.pt 的 ckpt 目录）—— "
                         "缺省随机初始化（纯模仿那几轮的口径）")
    # ---- ★ BL-04：优化器状态续跑（**缺省关 ⇒ 旧路径逐字节不变**）--------------------------
    ap.add_argument("--save-optimizer", action="store_true",
                    help="把**续跑状态**写进 checkpoint：Adam 动量/方差 · 优化器创建时的基准 lr · "
                         "全局步数与轮次（含 epoch 内位置与阶段）· 四路 RNG（torch/numpy/python/"
                         "掩码生成器）· **数据顺序指纹** · 行为策略标识（`sha256(权重)` + 策略串 + "
                         "温度）· 参数冻结状态 · 验证集早停的历史。多段/少轮长训练用"
                         "（`docs/TRAINING-V4.md` §15）")
    ap.add_argument("--resume-optimizer", default=None, metavar="CKPT",
                    help="从这份 checkpoint 的续跑状态**接着跑**（`--save-optimizer` 落的那一份；"
                         "不给 `--init` 时连权重也从它载）。⚠ 续跑时 PPO 口径闸门换成等价的三把"
                         "尺子（行为策略标识 / 链条起点 KL / 连续性探针）—— `--init` 与 "
                         "`--behaviour` 本来就不是同一份了。缺省 None ⇒ 旧路径")
    ap.add_argument("--stop-after-steps", type=int, default=0, metavar="N",
                    help="跑到**全局第 N 步**就正常收尾并落盘（分段训练用；0 = 不限制）。"
                         "命中在 epoch 中间时不产出半个 epoch 的读数行，累加器与步号进存档")
    # ---- ★ W3：对**现任**的 KL 锚（`docs/VALVES-AND-FIXTURES.md` §3 W3）--------------------
    ap.add_argument("--ref", default=None, metavar="NET.BIN|MODEL.PT|CKPT 目录",
                    help="KL 锚的**参考策略** π_ref（冻结）；缺省 = `--init` 那一份（**现任**）。"
                         "只在 `--ref-beta > 0` 时被读取")
    ap.add_argument("--ref-beta", type=float, default=0.0,
                    help="KL 锚的权重 β：`loss += β·KL(π_θ(·|s) ‖ π_ref(·|s))`（只在学生行上、"
                         "与策略损失同一个 softmax(logits/T)）。**缺省 0 = 完全等于今天的行为**"
                         "（逐位相同，向后兼容）；W3 的「放宽 KL 预算 + 对现任加锚」里 β 是"
                         "「别乱动」那个旋钮（0.1~1 量级起步）。"
                         "⛔ **它只保证「别乱动」，不提供目标**：它把 π_θ 拉回现任，"
                         "既不补打点也不补押し引き —— 实测 β=0.5 把 `--il-weight` 的一致率增益"
                         "**砍半**（KL(π‖init) 0.377→0.168）⇒ 想要「能动」就得同时有别的信号源")
    # ---- ★ W4 第一步：**逐候选引擎标签**的模仿项（`docs/VALVES-AND-FIXTURES.md` §3 的 W4）------
    ap.add_argument("--il-weight", type=float, default=0.0,
                    help="**引擎逐候选标签**的模仿项权重 w：`loss += w·CE(logits, 引擎最优候选)`"
                         "（目标由 `dataset.engine_best_index` 从 `cand.derived` 现算：打后向听最小 → "
                         "进张/听牌枚数最多 → 同分取最小下标；**只在学生行上**、与 `--ref-beta` 可同时开）。"
                         "**缺省 0 = 逐位等于今天**（那一项一行都不走）。"
                         "⛔⛔ **>0 已被实测证否，别拿它当进步手段**（2026-10-03 第六十三轮）："
                         "引擎牌效标签**缺打点/押し引き**（`dora_count` 刻意不参与）⇒ 优化它必然"
                         "拿这两样换进张：`-IlWeight 1` 起的那一季第 1 代闸门 **Δ=−12.65 顺位点**"
                         "CI[−14.92,−10.42]（和了率 24.1%→20.8%、打点 7037→6010）。"
                         "唯一「变好」的读数是 `engine_top1`（+6.95pp）—— 它与闸门**符号相反**，"
                         "⛔ **不是强弱判据**。**判强弱只能走闸门**（多套新牌山配对、CI 排除 0 且为正）")
    # ---- ★ 防过拟合：验证集早停（廉价读数）--------------------------------------------------
    ap.add_argument("--val-every", type=int, default=0,
                    help="每多少步在**固定子集**上读一次 train/val 的 policy CE（0 = 关闭 = 今天的行为）")
    ap.add_argument("--val-patience", type=int, default=3,
                    help="val CE 连续多少次没有改善（相对最好值，容忍 1e-4）就停这一轮"
                         "（**正常收尾**：返回 metrics + 照常导出 ckpt）")
    ap.add_argument("--val-metric", choices=["policy", "il"], default="policy",
                    help="早停读哪个头的 CE：`policy`（缺省 = 行为标签，与 W3 逐位一致）/ "
                         "`il`（**引擎标签**，配合 `--il-weight > 0` 时必须用它 —— 行为 CE 在 IL 臂上"
                         "必然抬高，见 `docs/TRAINING-V4.md` §14.12 的实测）")
    # ---- ★ MV-1 效率包（⛔ 两个开关**缺省全关**；关着时下面两条一行都不走）----------------
    ap.add_argument("--cache-ref-logits", default=None, metavar="DIR",
                    help="★ 把 π_ref 的冻结 logits 与 V_old **缓存到 DIR** 并在同参数下复用"
                         "（同数据/同权重/同温度/同 seed/同代码指纹才命中；不匹配即拒用重算）。"
                         "⛔ 缺省不传 = 关闭 = 与加这个开关之前**逐字节相同**")
    ap.add_argument("--skip-val-eval", action="store_true",
                    help="⚠ **只给冒烟**：跳过每个 epoch 末的全 val 评估（省 ~4.3 s，但那个 epoch 的"
                         "`val_*` 读数全变 nan）⇒ **正式跑不许开**（开了就没有 val 读数的可比性）")
    # ⚠ 缺省 False = **硬闸门**：紧凑集没有 `h0` 列（长程 carry）就报错退出。
    #   为什么不是"缺了就退化"：上线端喂整手 carry、训练端喂窗口 —— 两边不是同一个量，
    #   而那种不一致**不报错**（训练照跑、指标照出）。见 `train()` 里的三态说明。
    ap.add_argument("--no-carry", action="store_true",
                    help="允许在**没有 `h0` 列**的紧凑集上训练（= 明知是窗口冷启动、书面承担"
                         "「训练与上线不同源」的后果）。缺省禁止 —— 要消融/复现老数据才用它")
    # P1 自监督：掩码事件重建
    ap.add_argument("--mask-frac", type=float, default=0.15, help="掩码多少比例的真实事件 token（0 = 关）")
    ap.add_argument("--ssl-weight", type=float, default=0.2, help="掩码重建损失权重")
    # ---- ★ MV-1 第一刀：辅助子目标头 `own_win`（**缺省 0.0 = 关**；`lab/mv1/MV1-PLAN.md`）---------
    ap.add_argument("--aux-own-win-weight", type=float, default=0.0, metavar="W",
                    help="★ MV-1：**辅助子目标头 `own_win`** 的损失权重 "
                         "（BCE，目标 = 既有二值 `aux_win_flag` = 自家本小局和了）。"
                         "**缺省 0.0 = 关**：不建头、不进优化器、不进 ckpt、`compute_loss` 一行都不走。"
                         ">0 时该头读 `model.forward()` 的 `state`（**全部行**，⛔ 不吃 row_keep/RWR）。"
                         "⛔ 它是**外部头**（与 `ssl_head` 同族）：不进 `HEAD_SPECS`/`net.bin`/"
                         "三端头表/推理路径（判据 = 把该头丢掉后 policy 输出**逐位相同**）")
    ap.add_argument("--aux-own-win-rows", choices=["all", "student"], default="all",
                    help="`own_win` 头的**次口径报告**（⛔ 不改主判据、不改总损失）："
                         "`all`（缺省）= 只报全部行（= 训练口径，与 value/placement/belief_* 同族）；"
                         "`student` = 额外报一个 `own_win_student`（只在学生行上的 BCE，仅入 `parts`）")
    args = ap.parse_args(argv)
    if args.device == "auto" and not torch.cuda.is_available():
        print("（提示）没有 CUDA，用 CPU 跑；大轮次请用 GPU（设计 §0.1.3 的显存/占用纪律）")
    train(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
