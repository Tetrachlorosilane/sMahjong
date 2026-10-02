"""模型接线审计（常驻工具，第五十七轮）：抓「**虚假接入**」与「**错误置值**」。

为什么要有它：本项目已经栽过两次同类问题，而**两次都不是靠"跑起来没报错"发现的**：

- 第五十轮：`belief_tenpai` 用"拼接"接进 policy —— 数学上给所有候选加**逐行常数**
  ⇒ softmax/argmax 不变、**梯度恒为 0** ⇒ 接了个寂寞（后来靠红证才发现）；
- 第五十七轮（本工具）：`event.cell.*`（GRU，~222k 参数）**从 policy 损失收不到任何梯度**
  ⇒ 它没接进任何头（`h_evt` 传进 `Fusion` 但没人用），且每次前向还要白跑 K 次 GRUCell。
  **W1（第六十轮）已修**：`h_evt` 经 `fusion.mem` 逐候选门控接进融合 ⇒ 它从白名单里删掉了。

判据（三步）：

1. **梯度可达性**：只用 policy 损失反传 ⇒ 列出**梯度恒为 0** 的参数。它们要么"只服务别的头"
   （`value/placement/belief_*/danger/effect` ⇒ 正常），要么就是**假接入**（躯干/门控出现在这里 = bug）。
   ⚠ 这是唯一能区分"接上了"与"看着接上了"的机械判据。
   ⚠ **零初始化的门控必须先扰动再量梯度**（否则 `g ≡ 1`，它下游的 `event.cell.*` 梯度**恒 0**
   —— 那不是"没接上"，而是"门控还没学出非 0 权重"）。本工具现在先按铁律①扰动，再反传。
2. **零初始化清单**：训练起点为 0（或常量）的**权重**在 golden 夹具里若不显式扰动 ⇒ 那条路径
   **三端都没被测到**（写错也全绿）。`build_golden` 会**通用地**扰动所有全 0 参数；这里列出来做提醒。
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
#: ⚠ `event.cell.`（GRU）**已经从这里删掉**（W1，第六十轮）：它现在经 `fusion.mem` 门控
#:   接进融合 ⇒ 必须收到非零梯度。下面那条"抽查"会打印它的梯度范数当红证。
EXPECT_ZERO = (
    "heads.value.", "heads.placement.", "heads.belief_hand.", "heads.belief_tenpai.",
    "heads.danger.", "heads.effect.",
)


def main() -> int:
    torch.manual_seed(0)
    m = M.build()
    print("=== 2) 训练起点为全 0 的参数（夹具必须显式扰动，否则那条路径三端都没被测到）")
    zero_init = [n for n, p in m.named_parameters()
                 if p.numel() and float(p.abs().max()) == 0.0]
    for n in zero_init:
        print("   [全 0]", n)
    # 铁律①：先把零初始化参数扰动成非 0 —— 否则门控恒等，量到的"下游梯度恒 0"是假红。
    with torch.no_grad():
        for n in zero_init:
            dict(m.named_parameters())[n].normal_(0.0, 0.5)
    print(f"   （已按铁律①扰动 {len(zero_init)} 个全 0 参数后再量梯度）")

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
              "fusion.mem.weight", "event.cell.weight_ih", "event.cell.weight_hh",
              "tile.enc.0.weight", "fusion.out.net.0.weight"):
        p = pr[n]
        print(f"   抽查 {n:28} grad={0.0 if p.grad is None else float(p.grad.norm()):.3e}")

    # === 4) W1 正向探针：门控真的被消费（归零 ⇒ 输出必须变），且旧网（缺张量）逐位恒等
    m.eval()
    args = (tile, evt, ctx, cand)
    with torch.no_grad():
        a = m(*args, mask=mask)["policy"]                     # 扰动过的门控
        saved_w = m.fusion.mem.weight.detach().clone()
        saved_b = m.fusion.mem.bias.detach().clone()
        m.fusion.mem.weight.zero_()
        b = m(*args, mask=mask)["policy"]                     # 权重归零、偏置仍非 0
        m.fusion.mem.bias.zero_()
        c = m(*args, mask=mask)["policy"]                     # 全零 = "没有门控"
        m.fusion.mem.weight.copy_(saved_w)
        m.fusion.mem.bias.copy_(saved_b)
    fin = torch.isfinite(a - b)                               # 掩码位置是 -inf ⇒ 只比有限项
    live = float((a - b)[fin].abs().max()) if bool(fin.any()) else 0.0
    # 旧网兼容：把 `fusion.mem.*` **从权重里删掉**（就是 v1.14.1 那些网的样子）⇒ 走
    # `normalize_state` 补 0 ⇒ 必须与"门控全 0"**逐位**相同（不是"差不多"）。
    sd_old = {k: v for k, v in m.state_dict().items() if not k.startswith("fusion.mem.")}
    m3 = M.build()
    m3.load_state_dict(X.normalize_state(sd_old), strict=True)
    m3.eval()
    with torch.no_grad():
        d = m3(*args, mask=mask)["policy"]
    ident = bool(torch.equal(c, d))
    print(f"=== 4) 长程门控：归零 `fusion.mem.weight` ⇒ 策略头 maxΔ={live:.3e}"
          f"（{'被消费 ✓' if live > 1e-4 else '**没被消费：假接入**'}）")
    print(f"   旧网兼容（权重里没有 `fusion.mem.*` ⇒ 补 0）：与「没有门控」逐位相同 = {ident}")

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
    ok = not bad and live > 1e-4 and ident
    print("WIRING AUDIT PASS（无白名单外的零梯度参数；长程门控被消费且旧网逐位兼容）" if ok
          else f"WIRING AUDIT: {len(bad)} 个可疑参数 / 门控被消费={live > 1e-4} / 旧网兼容={ident}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
