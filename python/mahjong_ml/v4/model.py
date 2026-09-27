"""v4 网络（设计：`docs/TRAINING-V4.md` §5 / §6）：三塔 → 融合 → 多头。

三条设计主线都落在这里：

- **关键张注意力**：候选当 query，34 个牌种 token（+ 最近 K 条事件 token）当 key/value；
- **增量 + 循环推理**：`EventTower.step()` 用 `GRUCell` 只吃**新**事件（`v4/cache.py` 的 `EventStream`）；
- **状态 ⇄ 增量双向**：`Fusion` 里两轮 —— delta 门控写回 state，state 再用 FiLM 调制事件。

⚠ 前向**必须是确定性的**（无 dropout / 无随机）：否则"删标签 logits 逐位不变"（封闭红证①）
与"增量 == 全量"（判据④）都无从判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from .spec import C_CAND, C_CTX, C_EVT, C_TILE, K_EVT

D_MODEL = 192
TILE_D = 64
N_HEADS = 4
VALUE_BINS = 51
#: 值域（千点）：训练时把 `final_scores − 起点` 折进这些分箱（设计 §6 的值头）
VALUE_RANGE = 30.0


@dataclass(frozen=True)
class HeadSpec:
    """一个头的：形状 / 监督来源 / 损失 / 权重 / 是否上线必需（设计 §6 的表）。"""

    name: str
    shape: str
    target: str
    loss: str
    weight: float
    inference: bool
    note: str = ""


HEAD_SPECS: tuple[HeadSpec, ...] = (
    HeadSpec("policy", "[L]", "自对弈回报（PPO 优势）", "ppo-clip", 1.0, True, "主力"),
    HeadSpec("value", f"[{VALUE_BINS}]", "final_scores − 起点（千点）", "hl-gauss 交叉熵", 0.5, True,
             "critic / 风险敏感"),
    HeadSpec("placement", "[4]", "整场 placement", "交叉熵", 0.3, False, "终局取舍（可选上线）"),
    HeadSpec("belief_hand", f"[3,34]", "自对弈真值 opp_hand", "交叉熵", 0.2, False,
             "对手手牌信念：**只训练/诊断**，不回喂"),
    HeadSpec("belief_tenpai", "[3]", "自对弈真值 opp_tenpai", "BCE", 0.2, True, "押し引き输入"),
    HeadSpec("danger", "[L,4]", "自对弈放铳结果", "BCE", 0.3, True, "候选 × 目标家的放铳概率"),
    HeadSpec("effect", "[L,3]", "引擎 HandEval.afterDiscard", "回归", 0.3, False, "牌效辅助（免费标签）"),
)


class TileTower(nn.Module):
    """逐牌种共享 MLP + 自注意力 → `[B,34,TILE_D]` 与池化向量。"""

    def __init__(self, d_tile: int = TILE_D) -> None:
        super().__init__()
        self.enc = nn.Sequential(nn.Linear(C_TILE, d_tile), nn.ReLU(),
                                 nn.Linear(d_tile, d_tile), nn.ReLU())
        self.attn = nn.MultiheadAttention(d_tile, N_HEADS, batch_first=True)
        self.norm = nn.LayerNorm(d_tile)
        self.proj = nn.Linear(d_tile, D_MODEL)

    def forward(self, tile: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.enc(tile)
        a, _ = self.attn(h, h, h, need_weights=False)
        h = self.norm(h + a)
        return h, self.proj(h.mean(dim=1))


class EventTower(nn.Module):
    """事件流：MLP → GRUCell（增量）→ 小 Transformer（窗口内顺序）。"""

    def __init__(self, d: int = D_MODEL) -> None:
        super().__init__()
        self.enc = nn.Sequential(nn.Linear(C_EVT, d), nn.ReLU(), nn.Linear(d, d))
        self.cell = nn.GRUCell(d, d)
        layer = nn.TransformerEncoderLayer(d, N_HEADS, dim_feedforward=2 * d, dropout=0.0,
                                          batch_first=True, norm_first=True)
        self.tr = nn.TransformerEncoder(layer, num_layers=1)

    def forward(self, evt: torch.Tensor, h: torch.Tensor | None = None
                ) -> tuple[torch.Tensor, torch.Tensor]:
        e = self.enc(evt)                                   # [B,K,D]
        if h is None:
            h = torch.zeros(e.shape[0], e.shape[2], dtype=e.dtype, device=e.device)
            for t in range(e.shape[1]):                     # 冷启动：一串全喂
                h = self.cell(e[:, t], h)
        return self.tr(e), h

    def step(self, new_evt: torch.Tensor, h: torch.Tensor | None):
        """**增量**：只吃新事件（`new_evt`：`[B,k,C_EVT]`），返回新隐状态。"""
        e = self.enc(new_evt)
        for t in range(e.shape[1]):
            h = self.cell(e[:, t], h)
        return h


class Mlp(nn.Module):
    def __init__(self, cin: int, cout: int, hidden: int | None = None) -> None:
        super().__init__()
        hidden = hidden or cout
        self.net = nn.Sequential(nn.Linear(cin, hidden), nn.ReLU(), nn.Linear(hidden, cout), nn.ReLU())

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class Fusion(nn.Module):
    """关键张注意力 + 状态/增量**两轮双向**（设计 §5.2）。"""

    def __init__(self, d: int = D_MODEL) -> None:
        super().__init__()
        self.tile_proj = nn.Linear(TILE_D, d)
        self.q_tile = nn.MultiheadAttention(d, N_HEADS, batch_first=True)
        self.q_evt = nn.MultiheadAttention(d, N_HEADS, batch_first=True)
        self.gate = nn.Linear(2 * d, d)
        self.write = nn.Linear(d, d)
        self.film = nn.Linear(d, d)
        self.out = Mlp(3 * d, d)

    def forward(self, h_tile: torch.Tensor, h_tile_pool: torch.Tensor, e_tokens: torch.Tensor,
                h_evt: torch.Tensor, ctx: torch.Tensor, cand: torch.Tensor) -> torch.Tensor:
        kv_tile = self.tile_proj(h_tile)                       # [B,34,D]
        q = cand                                               # [B,L,D]
        u_tile, _ = self.q_tile(q, kv_tile, kv_tile, need_weights=False)
        u_evt, _ = self.q_evt(q, e_tokens, e_tokens, need_weights=False)
        # ① delta → state：门控写回
        g = torch.sigmoid(self.gate(torch.cat([u_evt, u_tile], dim=-1)))
        state = h_tile_pool.unsqueeze(1) + g * self.write(u_evt)
        # ② state → delta：FiLM 调制事件表示
        evt_mod = u_evt * (1.0 + torch.tanh(self.film(state)))
        ctx_b = ctx.unsqueeze(1).expand(-1, q.shape[1], -1)
        return self.out(torch.cat([u_tile, evt_mod, ctx_b], dim=-1))


class Heads(nn.Module):
    """多头：策略 / 分布价值 / 顺位 / 信念（对手手牌、听牌）/ 危险 / 牌效。"""

    def __init__(self, d: int = D_MODEL) -> None:
        super().__init__()
        self.policy = nn.Linear(d, 1)
        self.value = nn.Linear(d, VALUE_BINS)
        self.placement = nn.Linear(d, 4)
        self.belief_hand = nn.Linear(d, 3 * 34)
        self.belief_tenpai = nn.Linear(d, 3)
        self.danger = nn.Linear(d, 4)
        self.effect = nn.Linear(d, 3)

    def forward(self, u: torch.Tensor, state: torch.Tensor, mask: torch.Tensor | None) -> dict:
        logits = self.policy(u).squeeze(-1)                     # [B,L]
        if mask is not None:
            logits = logits.masked_fill(~mask, float("-inf"))
        return {
            "policy": logits,
            "value": self.value(state),                         # [B,VALUE_BINS]
            "placement": self.placement(state),                 # [B,4]
            "belief_hand": self.belief_hand(state).view(*state.shape[:-1], 3, 34),
            "belief_tenpai": self.belief_tenpai(state),         # [B,3]
            "danger": self.danger(u),                           # [B,L,4]
            "effect": self.effect(u),                           # [B,L,3]
        }


class V4Model(nn.Module):
    """v4 全模型。`forward` 返回**每个头**的输出；推理只用 `inference_heads()`。"""

    def __init__(self) -> None:
        super().__init__()
        self.tile = TileTower()
        self.event = EventTower()
        self.ctx = Mlp(C_CTX, D_MODEL)
        self.cand = Mlp(C_CAND, D_MODEL)
        self.fusion = Fusion()
        self.heads = Heads()

    # ---------------------------------------------------------------- 前向
    def forward(self, tile: torch.Tensor, evt: torch.Tensor, ctx: torch.Tensor,
                cand: torch.Tensor, mask: torch.Tensor | None = None,
                h: torch.Tensor | None = None) -> dict[str, torch.Tensor]:
        h_tile, tile_pool = self.tile(tile)
        e_tokens, h_evt = self.event(evt, h)
        u = self.fusion(h_tile, tile_pool, e_tokens, h_evt, self.ctx(ctx), self.cand(cand))
        out = self.heads(u, u.mean(dim=1), mask)
        out["h_evt"] = h_evt
        # ⚠ `e_tokens` 只给 **P1 的掩码事件重建**（先验/自监督 pretext）用 —— 推理路径不读它
        #   （`inference_heads()` 里没有它；多一个键不影响任何上线前向）。
        out["e_tokens"] = e_tokens
        return out

    def step_event(self, new_evt: torch.Tensor, h: torch.Tensor | None) -> torch.Tensor:
        """循环推理的增量入口（`EventStream.step` 用它）。"""
        return self.event.step(new_evt, h)

    # ---------------------------------------------------------------- 元信息
    def param_count(self) -> int:
        return sum(p.numel() for p in self.parameters())

    def block_fingerprint_note(self) -> dict[str, Any]:
        from . import spec
        return {"feature_version": spec.FEATURE_VERSION_V4, "blocks": spec.fingerprint(),
                "params": self.param_count()}


def build(seed: int = 20260927) -> V4Model:
    """建一个**确定**的模型（便于自检逐位比较）。"""
    torch.manual_seed(seed)
    m = V4Model()
    m.eval()
    return m


def inference_heads() -> tuple[str, ...]:
    return tuple(h.name for h in HEAD_SPECS if h.inference)


def loss_weights() -> dict[str, float]:
    return {h.name: h.weight for h in HEAD_SPECS}


def hl_gauss_targets(values: torch.Tensor, bins: int = VALUE_BINS,
                     vrange: float = VALUE_RANGE) -> torch.Tensor:
    """把标量回报折成 `[B,bins]` 的软标签（值头用；与 `VALUE_RANGE` 同一量纲：**千点**）。

    ⚠ 只有**落点那一格**与它的右邻拿到质量（`1−frac` 与 `frac`）—— 第一版把
    `x.floor()`（长度 = bins 的向量）直接喂给 `scatter_`，等于往几十格重复写，行和远大于 1；
    自检里"每行和为 1"那条当场抓住。
    """
    v = values.clamp(-vrange, vrange)
    width = 2.0 * vrange / bins
    pos = (v + vrange) / width                                  # 连续下标 [B]
    idx = pos.floor().clamp(0, bins - 2).long()                 # 落点格 [B]
    frac = (pos - idx.to(pos.dtype)).clamp(0.0, 1.0)            # 落点格内比例 [B]
    t = torch.zeros(values.shape[0], bins, device=values.device, dtype=values.dtype)
    t.scatter_(1, idx.unsqueeze(1), (1.0 - frac).unsqueeze(1))
    t.scatter_(1, (idx + 1).unsqueeze(1), frac.unsqueeze(1))
    return t
