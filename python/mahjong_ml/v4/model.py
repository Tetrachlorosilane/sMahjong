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
from typing import Any, Callable

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

#: state 级头（value / placement / belief_hand / belief_tenpai）的池化口径（**C2**）：
#:
#: - `False`（缺省）= **mask-aware**：只对 `n_legal` 个**合法槽**求均值 —— 与 Java
#:   `V4Policy.forwardAll` / C++ `v4policy.cpp` **同一个分母**（推理端本来就没有 padding）；
#: - `True` = **legacy**：`u.mean(dim=1)`，分母 = `Lmax`（把 padding 槽也平均进去）。
#:   ⛔ 只用于**逐位复现**改动前的训练行为（`pretrain --legacy-pool`），不许当缺省。
#:
#: ⚠ 这是**训练侧**口径：`net.bin` 格式 / Java / C++ 前向 / 特征 / 数据一个字节都没动
#: （那两端一直除以真实 `n` ⇒ 旧网在两端逐位不变）。旧口径的问题实测见
#: `lab/mv1/ARCH-AUDIT.md` §4：value logits 对"合法集大小 + padding" 的 rel=1.118、
#: max|Δ|=149.7，而自身 logit std 只有 5.74 ⇒ value/belief 主要由 padding 决定，不是状态。
LEGACY_POOL = False

#: `fusion.out` 末端的 ReLU（**C1**，第一百零一轮）：
#:
#: - `False`（缺省，新行为）= **去掉** `Mlp` 末端 ReLU ⇒ `u` 可正可负（候选表示不再被截断到 ≥0）；
#: - `True` = legacy：保留旧的 `nn.ReLU()` 末端，**逐位复现**改动前（`pretrain --legacy-out-relu`）。
#:
#: ⚠ 只动 `Fusion.out` 这一处；`ctx`/`cand` 的 `Mlp`（无缺陷证据）保持 ReLU。
#: ⚠ 这只是**训练侧**语义：`net.bin` 格式 / 张量名 / 形状 / 参数一个没变（0 参数），
#: 但同一份权重在新旧语义下 logits **会变**（历史 `u` 有 78.5% 维恒 0、有效宽度 41/192，
#: 曾有 ReLU 全死塌方 top1 0.165）⇒ ⛔ Java/C++ 前向尚未同步，C1-a 的网**不得**上线上闸门。
LEGACY_OUT_RELU = False

#: `fusion.mem` / `heads.policy_gate` 的初始化（**C3**，第一百零二轮）：
#:
#: - `False`（缺省，新行为）= **小非零**：`weight ~ N(0, ZERO_INIT_STD²)`、`bias = 0`；
#: - `True` = legacy：两块**全零**初始化（`g ≡ 1`、`h_evt` 被乘 0），**逐位复现**改动前
#:   （`pretrain --legacy-zero-init`）。
#:
#: 为什么要有非零值：零初始化让"事件顺序 / 对手听牌信念"在**出生时**对 logits 的影响**恰好为 0**
#: （门控恒等），而实测 9 个 v4 包里 4 个 `policy_gate.weight` 至今全 0、`fusion.mem.*` 整组缺失
#: ⇒ 那些包的 `belief_tenpai` 根本不在推理路径（`tools/policy-gate-check.mjs`）。
#: ⚠ 只改**训练侧初始化**：前向图 / 张量名 / 形状 / `net.bin` 格式**一个字节没动**，0 参数；
#:   旧网（含 `--init` 载入的网）从文件读到的值**原样覆盖**这里的初值 ⇒ 对 warm-start 逐位不变。
#: ⚠ σ 的选择依据见 `lab/mv1/ARCH-CHANGE-LIST.md` C3 与 `NOTES.md` §6.5 第一百零二轮：
#:   σ=1e-3 ⇒ 出生时门控 `1+tanh(W·sigmoid(bt))` 偏离 1 约 1e-3 量级（相对 `u` 的 ~0.1%），
#:   是"有影响但不动 argmax 主体"的量级；σ 太大（≥1e-2）就等于直接换掉 `--init` 的语义。
ZERO_INIT_STD = 1e-3
LEGACY_ZERO_INIT = False

#: `fusion` 关键路径上的 **`u` 尺度约束**（**B3**，第一百零四轮）：
#:
#: - `False`（缺省，新行为）= `u ← u + tanh(u_res) ⊙ LayerNorm(u)`：
#:   `u_res`（逐维，**零初始化**）⇒ 出生恒等；`u_norm` 的 LN 给尺度一条**独立可学**的路径，
#:   不再让 `fusion.out → 三处 tanh 门 → 线性 policy` 这条链的尺度漂移直接级联到 policy；
#: - `True` = legacy：**不建**这段残差（连 LN 都不算），**逐位复现**改动前
#:   （`pretrain --legacy-u-res`）。
#:
#: ⚠ 参数 **+576**（`u_norm.weight/bias` 2×192 + `u_res` 192）；`u_res=0` 时
#:   `u + tanh(0)·LN(u) = u` ⇒ **旧包（缺这三组张量 ⇒ 补 `(w=1,b=0)`/`0`）逐位不变**。
#: ⚠ 自欺警告：LN 会让 `frac(u==0)` **结构上必绿** ⇒ 判据必须**同时**报 LN **之前**
#:   （`fusion.out` 的输出）的 `frac(u==0)`，见 `NOTES.md` §6.5 第一百零四轮。
LEGACY_U_RES = False


def set_legacy_u_res(flag: bool) -> None:
    """**进程级** B3 残差开关（`pretrain --legacy-u-res` 在 `train()` 开头设一次）。

    为什么是进程级：一次训练会建好几张 V4Model（主网 / `--init` / `π_ref` / 行为策略）——
    只设主网会让参照网的前向图不同 ⇒ 旧轨迹复现不出来（与 `--legacy-out-relu` 同一个理由）。
    """
    global LEGACY_U_RES
    LEGACY_U_RES = bool(flag)


def set_legacy_zero_init(flag: bool) -> None:
    """**进程级**门控初始化开关（`pretrain --legacy-zero-init` 在 `train()` 开头设一次）。

    为什么是进程级：一次训练里会建**好几张** V4Model（主网、`--init`、`π_ref`、行为策略）——
    只设主网会让"从零建的参照网"与主网出生分布不同（`--init` 路径本来就被文件覆盖，
    所以这条只影响**冷启**的那些网）。
    """
    global LEGACY_ZERO_INIT
    LEGACY_ZERO_INIT = bool(flag)


def _init_gate_(lin: "nn.Linear", *, legacy: bool) -> None:
    """门控层的初始化：legacy ⇒ 全零；否则 `N(0, ZERO_INIT_STD²)` + bias 0。

    ⚠ **为什么先存/后恢复 RNG 状态**：C3 的判据要求"**只有**这两块张量和旧初始化不同"。
    如果直接 `normal_()`，它会消耗全局 RNG ⇒ 后面所有层（`heads.value`/`placement`/…）的
    初值全都跟着漂 —— 那样"出生影响"就分不清是门控造成的还是别的层造成的（审计口径要能归因）。
    存/恢复一次，既保住"同 seed ⇒ 同初值"的既有性质，又让差异**恰好**落在这两块上。
    """
    if legacy:
        nn.init.zeros_(lin.weight)
        nn.init.zeros_(lin.bias)
        return
    st = torch.get_rng_state()
    try:
        nn.init.normal_(lin.weight, mean=0.0, std=ZERO_INIT_STD)
        nn.init.zeros_(lin.bias)
    finally:
        torch.set_rng_state(st)


#: 非法/缺失的合法数在这里被抓住（fail-closed）：⛔ **绝不悄悄退回全槽均值**。
class LegalCountError(ValueError):
    """`state_pool` 拿不到可用的 `n_legal`（缺失 / ≤0 / >Lmax / 与 mask 不一致）。"""


def set_legacy_pool(flag: bool) -> None:
    """**进程级**池化口径开关（`pretrain --legacy-pool` 在 `train()` 开头设一次）。

    为什么是进程级而不是"每个网自己传"：一次训练里会建**好几张** V4Model（主网、`--init`
    载入的网、`π_ref`、行为策略网）—— 只把主网设成 legacy、参照网还用新口径，
    两边的 state/belief 口径不同 ⇒ **复现不出旧轨迹**（sha 会变）。
    """
    global LEGACY_POOL
    LEGACY_POOL = bool(flag)


def set_legacy_out_relu(flag: bool) -> None:
    """**进程级** `fusion.out` 末端 ReLU 开关（`pretrain --legacy-out-relu` 在 `train()` 开头设一次）。

    为什么也是进程级：一次训练里会建**好几张** V4Model（主网、`--init`、`π_ref`、行为策略）——
    只设主网会让参照网的前向语义不同 ⇒ 旧轨迹复现不出来、KL 锚也对不上。
    """
    global LEGACY_OUT_RELU
    LEGACY_OUT_RELU = bool(flag)


#: **显式语义档**（丙，第一百零五轮）：把三处进程级开关打成一档，供
#: `export.py golden --semantics` 重生成夹具、`tools/v4-forward-gpu.py --semantics` 驱动 R-4。
#:
#: 为什么必须显式：仓库夹具 `407BBE21…` 是 **C2 语义**（`fusion.out` 末端**有** ReLU），而
#: 当前 Python 缺省是 **C1**（去 ReLU）+ **C3**（非零初始化）+ **B3**（`u_res` 残差）；
#: Java `V4Policy` / C++ `v4policy.cpp` 至今仍按 C2 前向 ⇒ 用缺省语义重生成夹具必定 FAIL。
#: ⛔ 那种 FAIL **不是**"对拍不通过"；也⛔ 不许反过来拿"夹具恰好与当前 Python 一致"当 PASS。
#: 判据（本批冻写）：`c2` 重生 ⇒ Java/C++ **PASS 11/11**；`current` 重生 ⇒ **FAIL 并如实报**。
SEMANTICS: dict[str, tuple[bool, bool, bool]] = {
    #            legacy_out_relu, legacy_zero_init, legacy_u_res
    "c2":        (True,            True,             True),   # 部署端语义：ReLU + 零初始化 + 无 B3
    "c2b3":      (True,            True,             False),  # C2 的**输出语义** + B3 架构（驱动现有 B3 网）
    "c1":        (False,           True,             True),   # C1：去末端 ReLU（其余同 C2）
    "c3":        (False,           False,            True),   # C1+C3：小非零初始化
    "b3":        (False,           True,             False),  # C1+B3：u 的 LN+残差
    "current":   (False,           False,            False),  # 当前训练侧缺省（C1+C3+B3）
}


def apply_semantics(name: str) -> None:
    """按名字设置三处进程级开关；未知名字**响亮报错**（⛔ 不静默退化成 current）。"""
    try:
        out_relu, zero_init, u_res = SEMANTICS[name]
    except KeyError:
        raise ValueError(f"未知语义档 {name!r}（可选：{sorted(SEMANTICS)}）") from None
    set_legacy_out_relu(out_relu)
    set_legacy_zero_init(zero_init)
    set_legacy_u_res(u_res)


@dataclass(frozen=True)
class HeadSpec:
    """一个头的：形状 / 监督来源 / 损失 / 权重 / 是否**训练与契约必需**（设计 §6 的表）。

    ⚠ **这不是"上线必需"**（2026-10-07 用户裁决拆成两张表）：
    - 本字段 ⇒ **`TRAIN_HEADS`（训练与契约必需）**：必须训练、必须过 parity、进消融与加载器契约；
    - **`ONLINE_HEADS`（推理必需）** ⇒ 由**前向路径实际是否读取**决定，见下面的常量与
      `online_heads()` 的机械判据。⛔ 两者**不能合并**：训练必需要从来推导不出"影响决策"。
    """

    name: str
    shape: str
    target: str
    loss: str
    weight: float
    contract: bool
    note: str = ""


HEAD_SPECS: tuple[HeadSpec, ...] = (
    HeadSpec("policy", "[L]", "自对弈回报（PPO 优势）", "ppo-clip", 1.0, True, "主力"),
    HeadSpec("value", f"[{VALUE_BINS}]", "final_scores − 起点（千点）", "hl-gauss 交叉熵", 0.5, True,
             "critic / 风险敏感"),
    HeadSpec("placement", "[4]", "整场 placement", "交叉熵", 0.3, False, "终局取舍"),
    HeadSpec("belief_hand", f"[3,34]", "自对弈真值 opp_hand", "交叉熵", 0.2, False,
             "对手手牌信念：**只训练/诊断**，不回喂"),
    HeadSpec("belief_tenpai", "[3]", "自对弈真值 opp_tenpai", "BCE", 0.2, True,
             "⚠ **经 `policy_gate` 进 policy** ⇒ 见 `ONLINE_HEADS`"),
    HeadSpec("danger", "[L,4]", "自对弈放铳结果", "BCE", 0.3, True, "候选 × 目标家的放铳概率"),
    HeadSpec("effect", "[L,3]", "引擎 HandEval.afterDiscard", "回归", 0.3, False, "牌效辅助（免费标签）"),
)

#: **训练与契约必需**的头（必须训练 + 过 parity + 进消融/加载器契约）= `HeadSpec.contract`。
TRAIN_HEADS: tuple[str, ...] = tuple(h.name for h in HEAD_SPECS if h.contract)

#: **推理必需**的头 —— ⛔ 定义是"**前向路径实际读取**"，不是"训练过"（用户裁决 2026-10-07）。
#:
#: **当前值（实测）**：`policy` + `belief_tenpai`。后者的在线**原因**是逐候选门控
#: `gate = 1 + tanh(policy_gate(sigmoid(bt)))` 乘在候选表示上 ⇒ 真的改 argmax（第五十五轮）。
#:
#: ⚠ 它是"**每个 checkpoint 的性质**"，不是"代码拓扑常量"：`heads.policy_gate.*` 属**可缺张量**
#: （`normalize_state` 缺则补 0）⇒ **老网**没有它时门控恒等、`belief_tenpai` **不进推理**，
#: 此时本集合退化成 `("policy",)`。所以换 checkpoint 要**重跑判据**（`v4 check` ⑦/⑦′）。
#: 判据 = `online_heads()`（见下）；唯一规格与升级规则见 `docs/TRAINER-CPP.md` §6.25；
#: 三端同值由 `node tools/head-tables-check.mjs` 机械比对。
ONLINE_HEADS: tuple[str, ...] = ("policy", "belief_tenpai")


class TileTower(nn.Module):
    """逐牌种共享 MLP + 自注意力 → `[B,34,TILE_D]` 与池化向量。"""

    def __init__(self, d_tile: int = TILE_D, n_heads: int = N_HEADS,
                 d_model: int = D_MODEL) -> None:
        super().__init__()
        self.enc = nn.Sequential(nn.Linear(C_TILE, d_tile), nn.ReLU(),
                                 nn.Linear(d_tile, d_tile), nn.ReLU())
        self.attn = nn.MultiheadAttention(d_tile, n_heads, batch_first=True)
        self.norm = nn.LayerNorm(d_tile)
        self.proj = nn.Linear(d_tile, d_model)

    def forward(self, tile: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.enc(tile)
        a, _ = self.attn(h, h, h, need_weights=False)
        h = self.norm(h + a)
        return h, self.proj(h.mean(dim=1))


class EventTower(nn.Module):
    """事件流：MLP → GRUCell（增量）→ 小 Transformer（窗口内顺序）。"""

    def __init__(self, d: int = D_MODEL, n_heads: int = N_HEADS) -> None:
        super().__init__()
        self.enc = nn.Sequential(nn.Linear(C_EVT, d), nn.ReLU(), nn.Linear(d, d))
        self.cell = nn.GRUCell(d, d)
        layer = nn.TransformerEncoderLayer(d, n_heads, dim_feedforward=2 * d, dropout=0.0,
                                          batch_first=True, norm_first=True)
        self.tr = nn.TransformerEncoder(layer, num_layers=1)

    def forward(self, evt: torch.Tensor, h: torch.Tensor | None = None
                ) -> tuple[torch.Tensor, torch.Tensor]:
        """窗口 → `(e_tokens, carry_after_window)`；`h` = **窗口之前**的 carry（`None` ⇒ 全 0）。

        ⚠ **只喂真实事件行**（前部零 padding 不喂）：`GRUCell(0, h) ≠ h`，把 padding 也喂进去
        会让"窗口冷启动"与"整手 carry"差到 1e-2 量级（`cache.EventStream.recompute` 的注释里
        记着这次实测）。Java 的缓存路径只重放真实事件行 ⇒ 三端必须同一口径，否则 W1 之后
        "增量 == 全量"当场变红。padding 行怎么认：事件 token 的 `type` 段是 one-hot，
        恒有一位为 1 ⇒ **整行全 0** 只可能是 padding。
        """
        e = self.enc(evt)                                   # [B,K,D]
        if h is None:
            h = torch.zeros(e.shape[0], e.shape[2], dtype=e.dtype, device=e.device)
        real = evt.abs().sum(dim=-1) > 0                    # [B,K]
        for t in range(e.shape[1]):
            h_new = self.cell(e[:, t], h)
            h = torch.where(real[:, t].unsqueeze(-1), h_new, h)
        return self.tr(e), h

    def step(self, new_evt: torch.Tensor, h: torch.Tensor | None):
        """**增量**：只吃新事件（`new_evt`：`[B,k,C_EVT]`），返回新隐状态。"""
        e = self.enc(new_evt)
        for t in range(e.shape[1]):
            h = self.cell(e[:, t], h)
        return h


class Mlp(nn.Module):
    """`Linear → ReLU → Linear`（+ 可选末端 ReLU）。

    ⚠ **末端 ReLU 是 `final_relu` 开关**（C1，第一百零一轮）：`ctx`/`cand` 用缺省 `True`
    （与改动前逐位相同）；只有 `Fusion.out` 传 `False` ⇒ `u` 不再被截断到 ≥0。
    ⚠ 形状/张量名/参数**完全不变**：旧实现是 `Sequential(Linear, ReLU, Linear, ReLU)`，
      末端 ReLU 没有参数 ⇒ `net.0.*` / `net.2.*` 两个键名在新旧两边相同，旧 `net.bin`
      `strict=True` 照常加载。`F.relu` 与 `nn.ReLU()` 是同一个算子 ⇒ `final_relu=True`
      逐位复现改动前（`--legacy-out-relu` 要的正是这条）。
    """

    def __init__(self, cin: int, cout: int, hidden: int | None = None, *,
                 final_relu: bool = True) -> None:
        super().__init__()
        hidden = hidden or cout
        self.final_relu = bool(final_relu)
        self.net = nn.Sequential(nn.Linear(cin, hidden), nn.ReLU(), nn.Linear(hidden, cout))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = self.net(x)
        return F.relu(h) if self.final_relu else h


class Fusion(nn.Module):
    """关键张注意力 + 状态/增量**两轮双向**（设计 §5.2）。"""

    def __init__(self, d: int = D_MODEL, tile_d: int = TILE_D, n_heads: int = N_HEADS,
                 legacy_out_relu: bool | None = None,
                 legacy_u_res: bool | None = None) -> None:
        super().__init__()
        self.tile_proj = nn.Linear(tile_d, d)
        self.q_tile = nn.MultiheadAttention(d, n_heads, batch_first=True)
        self.q_evt = nn.MultiheadAttention(d, n_heads, batch_first=True)
        self.gate = nn.Linear(2 * d, d)
        self.write = nn.Linear(d, d)
        self.film = nn.Linear(d, d)
        # ⚠ **长程记忆（GRU 的 carry `h_evt`）走"逐维门控"接进来**（W1，第六十轮）：
        #   与 `Heads.policy_gate` **同一个理由** —— 把 `h_evt` 拼在候选表示后面、或作为逐行常数
        #   加进去，对 policy 是**数学空操作**（同一行所有候选吃到同一个量 ⇒ softmax/argmax 不变、
        #   那几列梯度恒为 0，红证见 NOTES §6.5 第四十八~五十四轮）。门控是**逐候选**的
        #   （`u_i` 各不相同）⇒ 真能改变 argmax。
        #   零初始化 ⇒ `g ≡ 1` ⇒ **旧网（权重里没有这两个张量）逐位不变**：加载期补 0，
        #   见 `export.normalize_state`；`strict=True` 也不会红。
        self.mem = nn.Linear(d, d)
        # ★ C3（第一百零二轮）：零初始化 → **小非零**（`N(0, 1e-3²)`、bias 0）。
        #   `--legacy-zero-init` ⇒ 旧的 `zeros_`（`g ≡ 1`，逐位复现）。⚠ 零参数/零形状变化；
        #   `--init` 载入的旧网从文件读到的值原样覆盖这里 ⇒ warm-start 逐位不变。
        _init_gate_(self.mem, legacy=LEGACY_ZERO_INIT)
        # ★ C1（第一百零一轮）：**这一处**去掉末端 ReLU（`legacy_out_relu=True` ⇒ 旧的 `nn.ReLU()`）。
        #   理由：78.5% 的 `u` 维精确为 0、有效宽度只有 41/192（审计 §4），且有过一次 ReLU 全死
        #   塌方（logits 常数、top1 0.165）。⛔ 其它 Mlp（ctx/cand）不动。
        self.legacy_out_relu = LEGACY_OUT_RELU if legacy_out_relu is None else bool(legacy_out_relu)
        self.out = Mlp(3 * d, d, final_relu=self.legacy_out_relu)
        # ★ B3（第一百零四轮）：`u` 的 **LayerNorm + 零初始化逐维残差门**（+576 参数）。
        #   为什么在这一处：`u` 是 policy/danger/effect/state 的**唯一**入口，而 scale 级联
        #   实测每一跳都从 policy 领跑（第一百零三轮：`g_policy` 97.17 vs `g_aux` 0.615）。
        #   LN 把"尺度"从 `fusion.out → 三处 tanh 门 → 线性 policy` 这条链里**解耦出来**，
        #   残差门让原 `u` 原样保留 ⇒ **不动 argmax 主体、可逐维校正**（不是整行缩放）。
        #   `u_res = 0` ⇒ 数学恒等；`legacy_u_res=True` ⇒ **不建这两块**（张量表退回 78 个 ⇒
        #   `net.bin` 与改动前**逐字节相同**，而不只是"数值等价"）。⚠ 只在构造期决定，别在
        #   前向里改（否则同一份 `state_dict` 会有两种张量集）。
        self.legacy_u_res = LEGACY_U_RES if legacy_u_res is None else bool(legacy_u_res)
        #   ⚠ LN 的 **eps 用 torch 缺省 1e-5**，与 Java/C++ 的手写实现必须同一个常数。
        if self.legacy_u_res:
            self.u_norm = None
            self.u_res = None
        else:
            self.u_norm = nn.LayerNorm(d)
            self.u_res = nn.Parameter(torch.zeros(d))

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
        u = self.out(torch.cat([u_tile, evt_mod, ctx_b], dim=-1))
        # ③ 长程记忆门控（乘在**逐候选**的 `u_i` 上；零初始化 ⇒ 恒等 ⇒ 旧网逐位不变）。
        #    乘在 `u` 上而不是只乘 policy ⇒ danger/effect/state（value 等）一起看到长程记忆。
        u = u * (1.0 + torch.tanh(self.mem(h_evt))).unsqueeze(1)
        # ④ ★ B3：`u ← u + tanh(u_res) ⊙ LN(u)`（逐维、逐候选；`u_res` 零初始化 ⇒ 出生恒等）。
        #    ⚠ legacy 分支**跳过整段**（连 LN 都不算）⇒ 与改动前**逐位相同**；
        #      非 legacy 且 `u_res=0`（旧包）⇒ `u + 0·LN(u)`，数值上也是恒等（逐位已实测）。
        if self.legacy_u_res:
            return u
        return u + torch.tanh(self.u_res) * self.u_norm(u)


class Heads(nn.Module):
    """多头：策略 / 分布价值 / 顺位 / 信念（对手手牌、听牌）/ 危险 / 牌效。"""

    def __init__(self, d: int = D_MODEL, value_bins: int = VALUE_BINS) -> None:
        super().__init__()
        # ⚠ **belief 用"逐候选门控"接进 policy**（第五十五轮；第五十轮的"拼接"是数学空操作）：
        #   `g = 1 + tanh(W_g·sigmoid(bt) + b_g)`，`policy_i = W·(u_i ⊙ g) + b`。
        #   ⛔ 为什么不能像第五十轮那样把 `sigmoid(bt)` **拼到每个候选后面**：那给所有候选加的是
        #   **逐行常数** ⇒ softmax/argmax 数学上不变、那几列权重梯度恒为 0（红证见 NOTES §6.5
        #   第四十八~五十四轮）。门控是**逐候选**的（`u_i` 各不相同）⇒ 真能改变 argmax。
        #   初始化 `W_g = b_g = 0` ⇒ `g ≡ 1` ⇒ 起步等价于"没有门控"（所以从旧网微调是安全的）。
        self.policy = nn.Linear(d, 1)
        self.policy_gate = nn.Linear(3, d)          # [dm,3]：`sigmoid(bt)[3]` → 逐维门控
        # ⚠ **门控必须小初始化**（C3 之前是**零**）：`1 + tanh(W·sigmoid(bt))`，`W=0` 时恒等。
        #   旧注释的理由（"从旧网微调 = 先恒等再学"）没错，但代价是**出生时信念/事件零影响**：
        #   9 个包里 4 个 `policy_gate` 至今全 0 ⇒ `belief_tenpai` 不在推理路径。
        #   C3 改成 `N(0, 1e-3²)`、bias 0：出生时门控偏离 1 仅 ~1e-3 量级（有影响、但不动主体），
        #   旧网加载时仍被文件值覆盖 ⇒ 旧包逐位不变；`--legacy-zero-init` 回到全零。
        _init_gate_(self.policy_gate, legacy=LEGACY_ZERO_INIT)
        self.value = nn.Linear(d, value_bins)
        self.placement = nn.Linear(d, 4)
        self.belief_hand = nn.Linear(d, 3 * 34)
        self.belief_tenpai = nn.Linear(d, 3)
        self.danger = nn.Linear(d, 4)
        self.effect = nn.Linear(d, 3)

    def forward(self, u: torch.Tensor, state: torch.Tensor, mask: torch.Tensor | None) -> dict:
        bt = self.belief_tenpai(state)                          # [B,3]（logits）
        # 门控只作用在 **policy** 的候选表示上（`danger`/`effect` 用未门控的 `u`）：
        # 假设就是"对手听牌信念该影响**打哪张**"，其它头各有各的语义，别一起动。
        gate = 1.0 + torch.tanh(self.policy_gate(torch.sigmoid(bt)))    # [B,dm]
        ug = u * gate.unsqueeze(1)                                      # [B,L,dm] 逐候选
        logits = self.policy(ug).squeeze(-1)                            # [B,L]
        if mask is not None:
            logits = logits.masked_fill(~mask, float("-inf"))
        return {
            "policy": logits,
            # ⚠ **`state` 只是一个键，不是一个头**（MV-1 第一刀，2026-10-16）：外部辅助头
            #   `own_win`（`pretrain.OwnWinHead`，与 `ssl_head` 同族）要读它，而它本来就在
            #   这里现成（C2 之后 = `state_pool(u, mask, n_legal)`，缺省 mask-aware）。
            #   与 `:277-279` 的 `h_evt`/`e_tokens` 同款：**多一个键
            #   不改任何上线前向**（本仓库无 dropout/BN ⇒ 前向逐位不变），也**不进 `net.bin`**
            #   （`export` 按 `state_dict()` 建张量表，字典键不进表）。
            "state": state,                                     # [B,dm]
            "value": self.value(state),                         # [B,VALUE_BINS]
            "placement": self.placement(state),                 # [B,4]
            "belief_hand": self.belief_hand(state).view(*state.shape[:-1], 3, 34),
            "belief_tenpai": bt,                                # [B,3] logits
            "danger": self.danger(u),                           # [B,L,4]
            "effect": self.effect(u),                           # [B,L,3]
        }


def state_pool(u: torch.Tensor, mask: torch.Tensor | None = None,
               n_legal: torch.Tensor | None = None, *, legacy: bool | None = None) -> torch.Tensor:
    """state 级头的池化：`[B,L,dm]` 候选表示 → `[B,dm]` 状态向量（**C2** 的唯一入口）。

    - `legacy=True` ⇒ `u.mean(dim=1)`（分母 `Lmax`；逐位复现改动前，连调用形式都不改）；
    - 否则 ⇒ `sum_{i<L} u_i·m_i / n`，`n` = 每行**真实合法数**（`n_legal`，或由 `mask` 求和）。

    **fail-closed**：新口径下 `n_legal` 与 `mask` **都没有** ⇒ `LegalCountError`；
    任何一行 `n ≤ 0` / `n > Lmax` / `n_legal` 与 `mask` 对不上 ⇒ `LegalCountError`。
    ⛔ 不提供"拿不到 n 就退回全槽均值"的分支 —— 那正是 C2 要消灭的静默口径漂移。

    ⚠ 无 padding 时（`mask` 全 True / `n == Lmax`）逐位等于 `u.mean(dim=1)`：乘 1.0 精确、
    求和顺序与 `mean` 相同、除以同一个 `n`；自检把这条钉成"四头 torch.equal"。
    """
    if legacy is None:
        legacy = LEGACY_POOL
    if legacy:
        return u.mean(dim=1)
    if n_legal is None and mask is None:
        raise LegalCountError(
            "state 级头的 mask-aware 池化拿不到 `n_legal`（也没有 `mask`）—— "
            "属于 fail-closed：⛔ 不退回全槽均值；推理端本来就没有 padding，请显式传 "
            "`mask`（全 True）或 `n_legal`")
    b, lmax = int(u.shape[0]), int(u.shape[1])
    if n_legal is not None:
        n = n_legal.reshape(-1).to(device=u.device, dtype=torch.int64)
        if int(n.numel()) != b:
            raise LegalCountError(f"`n_legal` 长度 {int(n.numel())} ≠ batch {b}")
        if bool((n <= 0).any()) or bool((n > lmax).any()):
            raise LegalCountError(
                f"`n_legal` 越界：必须在 [1, Lmax={lmax}]，实测 "
                f"min={int(n.min())} max={int(n.max())}（⛔ 不许把 ≤0 当'整行 padding'）")
        if mask is None:
            m = torch.arange(lmax, device=u.device)[None, :] < n[:, None]
        else:
            m = mask if mask.dtype == torch.bool else mask.bool()
            # 两个来源必须说同一件事：一个按 n 除、一个按 mask 乘，对不上就是静默失真。
            if not torch.equal(m.sum(dim=1).to(torch.int64), n):
                raise LegalCountError("`n_legal` 与 `mask` 不一致（逐行合法数不同）")
    else:
        m = mask if mask.dtype == torch.bool else mask.bool()
        n = m.sum(dim=1).to(torch.int64)
        if bool((n <= 0).any()):
            raise LegalCountError(
                f"`mask` 里有**整行全 False** 的行（n=0）：合法候选数必须 ≥1（实测 "
                f"{(n <= 0).sum().item()} 行）")
    if m.shape != u.shape[:2]:
        raise LegalCountError(f"`mask` 形状 {tuple(m.shape)} ≠ u 的前两维 {tuple(u.shape[:2])}")
    return (u * m.unsqueeze(-1)).sum(dim=1) / n.to(u.dtype).unsqueeze(-1)


class V4Model(nn.Module):
    """v4 全模型。`forward` 返回**每个头**的输出；推理只用 `ONLINE_HEADS`（前向实测；训练与契约必需是 `contract_heads()`）。

    ⚠ **只有四个宽度可变**（`d_model / tile_d / n_heads / value_bins`），**拓扑固定**
    （三塔 + 融合 + 多头）—— 与 v3 的 `hidden/head/trunk_layers` 同一个思路：
    宽度写进 `net.bin` 格式 2 的头部，于是 golden 夹具能用**小网络**（几十 KB）钉住前向，
    而上线权重照跑 192/64/4/51。
    """

    def __init__(self, *, d_model: int = D_MODEL, tile_d: int = TILE_D, n_heads: int = N_HEADS,
                 value_bins: int = VALUE_BINS, legacy_pool: bool | None = None,
                 legacy_out_relu: bool | None = None) -> None:
        super().__init__()
        if d_model % n_heads or tile_d % n_heads:
            raise ValueError(f"n_heads={n_heads} 必须同时整除 d_model={d_model} 与 tile_d={tile_d}")
        # ⚠ `legacy_pool` **不是宽度**：不进 `dims_cfg`、不进 `net.bin` 头部（格式一个字节没动）。
        self.legacy_pool = LEGACY_POOL if legacy_pool is None else bool(legacy_pool)
        # ⚠ `legacy_out_relu` 同理：0 参数、不改形状 ⇒ 也不进 `dims_cfg`（C1 不改 `net.bin` 格式）。
        self.legacy_out_relu = LEGACY_OUT_RELU if legacy_out_relu is None else bool(legacy_out_relu)
        self.dims_cfg = {"d_model": int(d_model), "tile_d": int(tile_d),
                         "n_heads": int(n_heads), "value_bins": int(value_bins)}
        self.tile = TileTower(tile_d, n_heads, d_model)
        self.event = EventTower(d_model, n_heads)
        self.ctx = Mlp(C_CTX, d_model)
        self.cand = Mlp(C_CAND, d_model)
        self.fusion = Fusion(d_model, tile_d, n_heads, legacy_out_relu=self.legacy_out_relu)
        self.heads = Heads(d_model, value_bins)

    # ---------------------------------------------------------------- 前向
    def forward(self, tile: torch.Tensor, evt: torch.Tensor, ctx: torch.Tensor,
                cand: torch.Tensor, mask: torch.Tensor | None = None,
                h: torch.Tensor | None = None,
                n_legal: torch.Tensor | None = None) -> dict[str, torch.Tensor]:
        """`h` = **窗口之前**的 GRU carry（`[B,dm]`；`None` ⇒ 全 0 冷启动）。

        ⚠ 两个量必须分清（W1 定稿，`docs/FEATURES-V4.md` §5.3）：

        - `h`（进来）：**窗口之前**所有事件的 carry。生产端（Java/C++ 的缓存槽、或对整手事件
          的重放）与离线端（`dataset` 的 `h0` 列）都按"**当前小局**从 0 起、逐事件推进"算它；
        - `h_evt`（出去，喂 `Fusion`）：**窗口之后**的 carry = `EventTower.forward(evt, h)[1]`。

        两者是同一个递推的两个时刻；`h=None` 时 `h_evt` 退化成"窗口内冷启动"，这是**离线
        审计**（`value_audit` / `heads_audit` / 老的紧凑集）没有 `h0` 时的合法退化 —— 但它
        **不等于**生产端喂进来的值，所以**训练必须传 `h0`**（否则训练/上线两套统计量）。
        """
        h_tile, tile_pool = self.tile(tile)
        e_tokens, h_evt = self.event(evt, h)
        u = self.fusion(h_tile, tile_pool, e_tokens, h_evt, self.ctx(ctx), self.cand(cand))
        # ★ C2：state 级头读 **mask-aware 池化**（只对真实 `n_legal` 个合法槽求均值）。
        #   缺省新口径；`legacy_pool=True` ⇒ 旧 `u.mean(dim=1)`（逐位复现）。
        out = self.heads(u, state_pool(u, mask, n_legal, legacy=self.legacy_pool), mask)
        out["h_evt"] = h_evt
        # ⚠ `e_tokens` 只给 **P1 的掩码事件重建**（先验/自监督 pretext）用 —— 推理路径不读它
        #   （`contract_heads()`/`ONLINE_HEADS` 里都没有它；多一个键不影响任何上线前向）。
        out["e_tokens"] = e_tokens
        return out

    def step_event(self, new_evt: torch.Tensor, h: torch.Tensor | None) -> torch.Tensor:
        """循环推理的增量入口（`EventStream.step` 用它）。"""
        return self.event.step(new_evt, h)

    # ---------------------------------------------------------------- 元信息
    def param_count(self) -> int:
        return sum(p.numel() for p in self.parameters())

    def dims(self) -> dict[str, int]:
        """四个宽度（写进 checkpoint 的 `config.dims`，导出时用来核对张量表）。"""
        return dict(self.dims_cfg)

    def block_fingerprint_note(self) -> dict[str, Any]:
        from . import spec
        return {"feature_version": spec.FEATURE_VERSION_V4, "blocks": spec.fingerprint(),
                "params": self.param_count(), "dims": self.dims()}


def build(seed: int = 20260927, **dims: int) -> V4Model:
    """建一个**确定**的模型（便于自检逐位比较）。`dims` 见 `V4Model.__init__`。"""
    torch.manual_seed(seed)
    m = V4Model(**dims)
    m.eval()
    return m


def contract_heads() -> tuple[str, ...]:
    """**训练与契约必需**的头（必须训练 + 过 parity + 进消融/加载器契约）。

    ⚠ 旧名 `inference_heads()` **已废弃**（2026-10-07）：那个名字把"训练必需"说成了"上线必需"，
    而两者**是两张表**（用户裁决）。推理必需见 `ONLINE_HEADS` 与 `online_heads()`。
    """
    return TRAIN_HEADS


def online_heads(m: "V4Model", forward_fn: Callable[[], dict[str, torch.Tensor]]) -> tuple[str, ...]:
    """判据：头是否"**上线**" = **前向路径是否读取**（用户裁决 2026-10-07）—— 机械、不靠文档声明。

    做法：把某个头的参数**清零**后重跑同一次前向 ⇒ 动作 logits **逐位不变** ⇔ 它不在推理路径上。
    返回实测集合（`policy` 恒在）。

    ⚠ **两个必带的对照**（缺任一条就会假绿或空转）：

    ① **正向控制**（调用方做）：清零 `heads.policy.*` **必须**让 logits 变化 —— 否则这条判据
       根本没测到东西；
    ② **非退化门控**：`policy_gate` 是**零初始化**的 `1 + tanh(W_g·sigmoid(bt))` ⇒ `W_g = 0` 时
       恒等，会把"`belief_tenpai` 被 policy 消费"这件事**测成绿的**。所以调用方要么先把
       `policy_gate` 抬离 0、要么直接用训练过的网。本函数不替调用方决定，只报告"清零后变不变"。
    """
    base = {k: v.detach().clone() for k, v in m.state_dict().items()}
    ref = forward_fn()["policy"].detach().clone()
    changed: list[str] = []
    for name in (h.name for h in HEAD_SPECS if h.name != "policy"):
        prefix = f"heads.{name}."
        with torch.no_grad():
            for k, v in m.state_dict().items():
                if k.startswith(prefix):
                    v.zero_()
            differs = not torch.equal(forward_fn()["policy"], ref)
            m.load_state_dict(base)
        if differs:
            changed.append(name)
    return ("policy", *changed)


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
