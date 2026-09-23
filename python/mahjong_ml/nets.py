"""网络：**候选打分头**（`docs/TRAINING.md` §3.1）。

    共享 trunk(state) → h ；score(h ⊕ cand_i) → 每个合法动作一个标量 → 对本次 legal 做 softmax

为什么是"候选打分"而不是固定 79 路输出：`chi` / `kan` / `pon` 是**参数化**的
（一次询问里最多各几种取法，取法还进了动作键，见 `PROTOCOL.md` §8.3），
打分头天然统一处理任意动作集，并且**逐条对齐轨迹里的 `chosen_index`**（"枚举 + 掩码"的监督信号）。

⚠ 这个结构的参数量要能在 **纯 Java 手写前向**里跑（§2 的硬约束）：默认约 0.25M，Java 侧 < 100 µs/决策。
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
