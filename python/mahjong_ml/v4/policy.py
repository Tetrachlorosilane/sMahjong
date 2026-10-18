"""Python 侧 **policy 包装器**：`logits → 合法候选下标`（含 A− 的**确定性 tie-break**）。

为什么要有这一层（`ARCH-REDESIGN` §2.6 / §4-判据 2；`A-REFACTOR-IMPL-PLAN.md` 风险 2）：

- 现状三端都是"并列取**最小合法下标**"（`Logits.java:24-34`、`net.cpp:610-617`、
  `pretrain.py` 的 `torch.argmax`）⇒ 当 `top1−top2 < 1e-3` 时，**选择由浮点末位决定**
  （实测 3,083 行里 6 行 `gap<1e-3`；`TIE` 曾被长 carry 推到 0.430/2.578，比中位 gap 0.6888 小
  一个量级 ⇒ 判据失去分辨率）。
- A− 的口径：`gap < τ=1e-3` **且同动作族** ⇒ 依次比较
  ① `effect` 的**进张数**（`heads.effect` 的第 2 维，越大越好）；
  ② **牌码序**（`cand` 的 tile 槽 `argmax`，0..36，越小越好）；
  ③ 仍并列 ⇒ 最小合法下标。
  ⛔ **不许**用浮点末位/随机破平（`torch.argmax` 的"第一个最大"是**实现细节**，不是规格）。

⚠ **归属 = caller**（`Heads` 拿不到候选语义；`netArgmax` 只看 logits）：所以规则活在这里，
  而调用点必须显式把 `cand` / `effect` 传进来。

⚠ **三端必须同样实现**（Java `Logits.argmaxOf` 的带 keys 重载 / C++ `policies.hpp:269` 的
  caller / Python 本文件）：同一份规格、同一个 `τ`、同一个"同族"定义（动作 type 的 `argmax`）、
  同一个"进张数"口径（`effect[:, 1]`）。三端不一致的症状是"自对弈轨迹与训练标签分叉、
  golden 全绿但闸门是噪声"（风险 2）。**本批只做 Python 侧**（C++/Java 属段 3）。
"""

from __future__ import annotations

import torch

#: tie-break 阈值（`ARCH-REDESIGN` §2.6：有 130× 余量 —— 三端 `max|Δlogits|` 实测 7.7e-6）
TAU = 1e-3

#: `cand` 的槽位（`spec.py` / `blocks.py`）：type one-hot 0..8、tile one-hot 9..45（37 = 34+3 赤）、
#: tiles 46..82、tsumogiri 83、kan_kind 84..86、noarg 87。
_TYPE_SLICE = slice(0, 9)
_TILE_SLICE = slice(9, 46)
#: `heads.effect` 的 3 维 = 打后向听 / **进张数** / 听牌形（`FEATURES-V4.md` §4.4）
EFFECT_DRAW_IDX = 1


def action_family(cand: torch.Tensor) -> torch.Tensor:
    """候选的**动作族** = `cand` 的 type one-hot 取 argmax（0..8；与三端的 `type` 同序）。"""
    return cand[..., _TYPE_SLICE].argmax(dim=-1)


def tile_code(cand: torch.Tensor) -> torch.Tensor:
    """候选的**牌码**（0..36；34/35/36 = 赤五万/筒/索，与三端同序）。"""
    return cand[..., _TILE_SLICE].argmax(dim=-1)


def choose_index(logits: torch.Tensor, *, mask: torch.Tensor | None = None,
                 cand: torch.Tensor | None = None, effect: torch.Tensor | None = None,
                 tau: float = TAU) -> torch.Tensor:
    """`[B,L] logits → [B]` 下标。**不给 `cand`/`effect` 时逐位等于 `argmax`**（旧路径不变）。

    - `mask`：`[B,L]` bool（True = 合法）；非法项按 `-inf` 处理（与 `Heads` 的 `masked_fill` 同口径）。
    - `cand`：`[B,L,C_CAND]`；`effect`：`[B,L,3]`（`heads.effect` 的输出）。
    - 并列 = `max − x < tau` **且** `action_family(x) == action_family(argmax)`。

    ⚠ 返回的下标一定在 `mask` 内（`mask` 全 False 的行是契约违反，直接报错 —— ⛔ 不猜）。
    """
    if mask is None and cand is None and effect is None:
        return logits.argmax(dim=-1)
    if mask is not None:
        logits = logits.masked_fill(~mask, float("-inf"))
    idx = logits.argmax(dim=-1)                                    # [B]
    if cand is None or effect is None:
        return idx
    b, lmax = int(logits.shape[0]), int(logits.shape[1])
    top = logits.gather(1, idx.unsqueeze(1)).squeeze(1)            # [B]
    fam = action_family(cand)                                      # [B,L]
    tie = (top.unsqueeze(1) - logits) < tau                        # [B,L]（-inf 项为 False）
    tie = tie & (fam == fam.gather(1, idx.unsqueeze(1)))           # 同动作族
    if mask is not None:
        tie = tie & mask
    # 排序键：进张数**降序** → 牌码**升序** → 下标**升序**；用"大根"三元组一次 argsort 实现。
    draw = effect[..., EFFECT_DRAW_IDX].to(torch.float64)
    code = tile_code(cand).to(torch.float64)
    ar = torch.arange(lmax, device=logits.device, dtype=torch.float64).unsqueeze(0)
    key = torch.stack([torch.where(tie, -draw, torch.full_like(draw, float("inf"))),
                       torch.where(tie, code, torch.full_like(code, float("inf"))),
                       ar.expand(b, lmax)], dim=-1)                # [B,L,3]
    # 逐行比较：按 (key0, key1, key2) 字典序取最小
    best = torch.zeros(b, dtype=torch.long, device=logits.device)
    best_key = key[:, 0, :]
    for i in range(1, lmax):
        k = key[:, i, :]
        better = (k[:, 0] < best_key[:, 0]) | (
            (k[:, 0] == best_key[:, 0]) & (
                (k[:, 1] < best_key[:, 1]) | (
                    (k[:, 1] == best_key[:, 1]) & (k[:, 2] < best_key[:, 2]))))
        best = torch.where(better, torch.full_like(best, i), best)
        best_key = torch.where(better.unsqueeze(1), k, best_key)
    # 没有并列（key0 = +inf 全行）的那几行退回 argmax（best 会选到下标 0 ⇒ 必须显式恢复）。
    has_tie = tie.any(dim=-1)
    return torch.where(has_tie, best, idx)
