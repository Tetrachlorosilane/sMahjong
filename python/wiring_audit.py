"""模型接线审计（常驻工具，第五十七轮）：抓「**虚假接入**」与「**错误置值**」。

为什么要有它：本项目已经栽过两次同类问题，而**两次都不是靠"跑起来没报错"发现的**：

- 第五十轮：`belief_tenpai` 用"拼接"接进 policy —— 数学上给所有候选加**逐行常数**
  ⇒ softmax/argmax 不变、**梯度恒为 0** ⇒ 接了个寂寞（后来靠红证才发现）；
- 第五十七轮（本工具）：`event.cell.*`（GRU，~222k 参数）**从 policy 损失收不到任何梯度**
  ⇒ 它没接进任何头（`h_evt` 传进 `Fusion` 但没人用），且每次前向还要白跑 K 次 GRUCell。

判据（三步）：

1. **梯度可达性**：只用 policy 损失反传 ⇒ 列出**梯度恒为 0** 的参数。它们要么"只服务别的头"
   （`value/placement/belief_*/danger/effect` ⇒ 正常），要么就是**假接入**（躯干/门控出现在这里 = bug）。
   ⚠ 这是唯一能区分"接上了"与"看着接上了"的机械判据。
2. **零初始化清单**：训练起点为 0（或常量）的**权重**在 golden 夹具里若不显式扰动 ⇒ 那条路径
   **三端都没被测到**（写错也全绿）。`build_golden` 现在会随机化门控；这里把清单列出来做提醒。
3. **导出往返（门控非零）**：零门控时的往返通过是平凡结论；这里显式塞非零门控再导出→读回→比前向，
   才测得到序列化/读取。

用法：`python wiring_audit.py`（无需参数；只读，不训练、不写数据根）。
"""
import sys
from pathlib import Path

sys.path.insert(0, ".")
import torch                                                          # noqa: E402
from mahjong_ml.v4 import export as X, model as M, spec                # noqa: E402

#: 允许"从 policy 损失收不到梯度"的白名单前缀（各有各的损失）。
#: ⚠ `event.cell.`（GRU）**目前仍在白名单里**，但**不是**因为它没价值 —— 见下面那条注释。
EXPECT_ZERO = (
    "heads.value.", "heads.placement.", "heads.belief_hand.", "heads.belief_tenpai.",
    "heads.danger.", "heads.effect.",
    # ⚠ `event.cell.`（GRU）**仍未接** —— 它不是"没价值"（窗口 K=60 覆盖不到半局，它是唯一的长程
    #   记忆），而是**暂时不能接**：Java 的 `h` 是整局 carry、Python/夹具的 `h` 是窗口冷启动，
    #   接进融合会让两条路径分叉（三端不同值 + "增量 == 全量"红证变红）。
    #   ⇒ 正确接法要先把 `h_evt` 变成**显式输入**（夹具/三端/缓存契约一起改）。修好后**从这里删掉**。
    "event.cell.",
)


def main() -> int:
    torch.manual_seed(0)
    m = M.build()
    print("=== 2) 训练起点为全 0 的参数（夹具必须显式扰动，否则那条路径三端都没被测到）")
    zero_init = [n for n, p in m.named_parameters()
                 if p.numel() and float(p.abs().max()) == 0.0]
    for n in zero_init:
        print("   [全 0]", n)

    m.train()
    B, L = 4, 7
    tile = torch.randn(B, 34, spec.C_TILE)
    evt = torch.randn(B, spec.K_EVT, spec.C_EVT)
    ctx = torch.randn(B, spec.C_CTX)
    cand = torch.randn(B, L, spec.C_CAND)
    mask = torch.ones(B, L, dtype=torch.bool)
    mask[1, 4:] = False
    out = m(tile, evt, ctx, cand, mask=mask)
    loss = torch.nn.functional.cross_entropy(out["policy"], torch.randint(0, L, (B,)))
    loss.backward()
    zero = [n for n, p in m.named_parameters()
            if p.grad is None or float(p.grad.norm()) == 0.0]
    print(f"=== 1) 仅 policy 损失：{len(zero)} 个参数梯度恒 0")
    bad = [n for n in zero if not n.startswith(EXPECT_ZERO)]
    for n in zero:
        flag = "" if n.startswith(EXPECT_ZERO) else "   <<< 不在白名单：**可能是假接入**"
        print("   ", n, flag)
    pr = dict(m.named_parameters())
    for n in ("heads.policy_gate.weight", "heads.policy_gate.bias", "heads.policy.weight",
              "tile.enc.0.weight", "fusion.out.net.0.weight"):
        p = pr[n]
        print(f"   抽查 {n:28} grad={0.0 if p.grad is None else float(p.grad.norm()):.3e}")

    m.eval()
    with torch.no_grad():
        m.heads.policy_gate.weight.normal_(0.0, 0.5)
        m.heads.policy_gate.bias.normal_(0.0, 0.5)
    sd = m.state_dict()
    tmp = Path("_wiring-net.bin")
    tmp.write_bytes(X.net_blob(sd, X.dims_from_state(sd)))
    back = X.state_from_net(X.read_net(tmp))
    tmp.unlink()
    m2 = M.build()
    m2.load_state_dict(back, strict=True)
    m2.eval()
    with torch.no_grad():
        a = m(tile, evt, ctx, cand, mask=mask)["policy"]
        b = m2(tile, evt, ctx, cand, mask=mask)["policy"]
    fin = torch.isfinite(a - b)
    print("=== 3) 导出往返（门控非零）：有限项最大绝对差 =",
          float((a - b)[fin].abs().max()),
          "| argmax 一致 =", bool(torch.equal(a.argmax(-1), b.argmax(-1))),
          "| 门控非零 =", float(m.heads.policy_gate.weight.abs().sum()) > 0)
    print("WIRING AUDIT PASS（无白名单外的零梯度参数）" if not bad
          else f"WIRING AUDIT: {len(bad)} 个可疑参数（见上）")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
