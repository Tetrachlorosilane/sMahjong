"""网络：**候选打分头**（`docs/TRAINING.md` §3.1）。

    共享 trunk(state) → h ；score(h ⊕ cand_i) → 每个合法动作一个标量 → 对本次 legal 做 softmax

为什么是"候选打分"而不是固定 79 路输出：`chi` / `kan` / `pon` 是**参数化**的
（一次询问里最多各几种取法，取法还进了动作键，见 `PROTOCOL.md` §8.3），
打分头天然统一处理任意动作集，并且**逐条对齐轨迹里的 `chosen_index`**（"枚举 + 掩码"的监督信号）。

⚠ 这个结构的参数量要能在 **纯 Java 手写前向**里跑（§2 的硬约束）：默认 **266,753 参数 / 1.02 MB**
（state 607 → 256 → 256，每候选 (256+96) → 128 → 1）。实测（2026-09，见 `docs/TRAINING.md` §3.3）：
**网络本身 ≈ 0.20 ms，每多一个候选 +~85 µs**（一次决策约 0.4 ms）——
而一次决策**真正的开销在特征工程**（逐候选 `HandEval`/`Shanten` ≈ 0.5 ms/候选，整条通路 ≈ 5.4 ms）：
要提速先动特征，别动网络。
"""

from __future__ import annotations

import torch
import torch.nn as nn


class CandidateScorer(nn.Module):
    """共享 trunk + 逐候选打分头（logits 只在合法候选上有意义，其余由 mask 填 -inf）。"""

    def __init__(self, state_dim: int, cand_dim: int, hidden: int = 256, head: int = 128,
                 trunk_layers: int = 2) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        in_dim = state_dim
        for _ in range(trunk_layers):
            layers += [nn.Linear(in_dim, hidden), nn.ReLU()]
            in_dim = hidden
        self.trunk = nn.Sequential(*layers)
        self.head = nn.Sequential(
            nn.Linear(hidden + cand_dim, head), nn.ReLU(),
            nn.Linear(head, 1),
        )
        self.state_dim = state_dim
        self.cand_dim = cand_dim
        self.hidden = hidden
        self.head_dim = head                   # ⚠ 别叫 `head`：那名字被上面的 nn.Sequential 占了
        self.trunk_layers = trunk_layers      # 导出权重/Java 前向要按它排张量

    def forward(self, state: torch.Tensor, cand: torch.Tensor,
                mask: torch.Tensor | None = None) -> torch.Tensor:
        """
        @param state `[B, state_dim]`
        @param cand  `[B, L, cand_dim]`（定长，尾部是填充）
        @param mask  `[B, L]` bool，True = 合法；给了就把非法位置填成 -inf
        @return logits `[B, L]`
        """
        h = self.trunk(state)                                   # [B, H]
        h = h.unsqueeze(1).expand(-1, cand.size(1), -1)         # [B, L, H]
        logits = self.head(torch.cat([h, cand], dim=-1)).squeeze(-1)   # [B, L]
        if mask is not None:
            logits = logits.masked_fill(~mask, float("-inf"))
        return logits

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


def build(state_dim: int, cand_dim: int, *, hidden: int = 256, head: int = 128) -> CandidateScorer:
    return CandidateScorer(state_dim, cand_dim, hidden=hidden, head=head)


class CriticScorer(nn.Module):
    """P3（离线 RL）的**价值网络**：共享 trunk + 逐候选 Q 头 + 状态 V 头。

    `Q(s,a)` 用与 `CandidateScorer` **同样的候选打分结构**（所以"一手值多少点"这件事
    是按候选算的，天然对齐 `chosen_index`）；`V(s)` 是 trunk 上的一个小 MLP（只吃 state），
    IQL 的 expectile 回归学的就是它。**两者共享 trunk** —— 这是 IQL 的常见做法，
    也让 V 顺带把 state 表示训好。

    ⚠ 量纲：目标一律是**千点**（`rewards.POINTS_PER_UNIT`），所以 Q/V 的正常范围是 ±10 上下，
    不是 ±30000。别把原始点数直接喂进来（回归会炸）。
    """

    def __init__(self, state_dim: int, cand_dim: int, hidden: int = 256, head: int = 128,
                 trunk_layers: int = 2) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        in_dim = state_dim
        for _ in range(trunk_layers):
            layers += [nn.Linear(in_dim, hidden), nn.ReLU()]
            in_dim = hidden
        self.trunk = nn.Sequential(*layers)
        self.q_head = nn.Sequential(
            nn.Linear(hidden + cand_dim, head), nn.ReLU(),
            nn.Linear(head, 1),
        )
        self.v_head = nn.Sequential(
            nn.Linear(hidden, head), nn.ReLU(),
            nn.Linear(head, 1),
        )
        self.state_dim = state_dim
        self.cand_dim = cand_dim
        self.hidden = hidden
        self.head_dim = head                       # 与 CandidateScorer 同名，便于两处共用导出逻辑
        self.trunk_layers = trunk_layers

    def q_values(self, state: torch.Tensor, cand: torch.Tensor,
                 mask: torch.Tensor | None = None) -> torch.Tensor:
        """`[B, L]`：每个合法候选的 Q（非法位置填 `-inf`，与打分头同一约定）。"""
        h = self.trunk(state)
        h = h.unsqueeze(1).expand(-1, cand.size(1), -1)
        q = self.q_head(torch.cat([h, cand], dim=-1)).squeeze(-1)
        if mask is not None:
            q = q.masked_fill(~mask, float("-inf"))
        return q

    def v_values(self, state: torch.Tensor) -> torch.Tensor:
        """`[B]`：状态价值（IQL 的 expectile 目标）。"""
        return self.v_head(self.trunk(state)).squeeze(-1)

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


def build_critic(state_dim: int, cand_dim: int, *, hidden: int = 256, head: int = 128,
                 ) -> CriticScorer:
    return CriticScorer(state_dim, cand_dim, hidden=hidden, head=head)


class ValueNet(nn.Module):
    """P4（在线 RL）的**状态价值网络**：只有 trunk + V 头（不像 `CriticScorer` 那样还带 Q 头）。

    为什么另起一个而不是复用 `CriticScorer`：在线 PPO 的优势只用 `V(s)`，Q 头**没有任何梯度来源**
    —— 挂在里面只会白算一半参数，还让"这份 checkpoint 是给谁用的"变含糊。

    ⚠ **它不导出到 Java**：线上策略只认 `CandidateScorer`（`export.weights` 的契约），
    V 只活在训练侧（优势估计 / 诊断）。所以这个类的存在**没有**动 `NeuralPolicy.java` 一行。

    ⚠ 量纲同 `CriticScorer`：目标是**千点**（`rewards.POINTS_PER_UNIT`），正常范围 ±10 上下。
    """

    def __init__(self, state_dim: int, hidden: int = 256, head: int = 128,
                 trunk_layers: int = 2) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        in_dim = state_dim
        for _ in range(trunk_layers):
            layers += [nn.Linear(in_dim, hidden), nn.ReLU()]
            in_dim = hidden
        self.trunk = nn.Sequential(*layers)
        self.v_head = nn.Sequential(nn.Linear(hidden, head), nn.ReLU(), nn.Linear(head, 1))
        self.state_dim = state_dim
        self.hidden = hidden
        self.head_dim = head
        self.trunk_layers = trunk_layers

    def forward(self, state: torch.Tensor) -> torch.Tensor:
        """`[B]`：状态价值（千点）。"""
        return self.v_head(self.trunk(state)).squeeze(-1)

    def n_params(self) -> int:
        return sum(p.numel() for p in self.parameters())


def build_value(state_dim: int, *, hidden: int = 256, head: int = 128) -> ValueNet:
    return ValueNet(state_dim, hidden=hidden, head=head)


def export_weights(model: CandidateScorer) -> dict:
    """导出**纯 Java 前向**要用的权重（名字 ↔ 形状一一对应，Java 侧照抄即可）。

    只导出 float32 numpy 数组 + 结构超参，不含任何 torch 特有的东西（这就是 §2 说的"导出权重"）。
    """
    sd = {k: v.detach().cpu().numpy().astype("float32") for k, v in model.state_dict().items()}
    return {
        "state_dim": model.state_dim,
        "cand_dim": model.cand_dim,
        "hidden": model.hidden,
        "weights": sd,
    }
