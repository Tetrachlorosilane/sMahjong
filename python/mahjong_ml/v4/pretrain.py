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
from . import dataset as v4ds
from . import model as M
from . import spec

#: 牌效头 3 维的归一化分母（= `features.DERIVED_SCALE_CAND[:3]`：向听 / 进张种数 / 进张枚数）
EFFECT_SCALE = (8.0, 34.0, 136.0)

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


def _batch(data: dict, idx: np.ndarray, device: str) -> dict[str, torch.Tensor]:
    """按行号取一个 batch（memmap → torch）。`idx` **升序**（与 v3 的 `bc._batch` 同规矩）。

    ⚠ 候选宽度取**这一份切分自己的** `cand.shape[1]`，不是 `meta["lmax"]`：
    train/val 两份的 lmax 可能不同（`build` 里按切分各自定宽），拿全局 lmax 去做掩码
    会与 logits 的长度对不上（实测报 `size of tensor a (27) must match b (15)`）。
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
        "placement": torch.from_numpy(
            np.asarray(data["placement"][idx], dtype=np.int64)).to(device),
        "effect": torch.from_numpy(
            np.asarray(data["effect"][idx], dtype=np.float32)).to(device),
        "nlegal": torch.from_numpy(nleg).to(device),
    }
    for src, dst in (("aux_opp_hand", "opp_hand"), ("aux_opp_tenpai", "opp_tenpai"),
                     ("aux_opp_dealin", "opp_dealin"), ("aux_own_tenpai", "own_tenpai"),
                     ("aux_own_shanten_after", "own_shanten")):
        if data.get(src) is not None:
            b[dst] = torch.from_numpy(np.asarray(data[src][idx], dtype=np.float32)).to(device)
    return b


def compute_loss(out: dict[str, torch.Tensor], b: dict[str, torch.Tensor],
                 weights: dict[str, float] | None = None,
                 ssl: tuple | None = None) -> tuple[torch.Tensor, dict[str, float]]:
    """多头加权损失。返回 `(总损失, 逐头损失字典)`。

    @param weights 逐头权重（`model.loss_weights()` 的注册表；分阶段训练时按阶段缩放）
    @param ssl     `(ssl_head, mask, target)` —— 掩码事件重建（P1 pretext）；不传就不算
    """
    w = weights if weights is not None else M.loss_weights()
    parts: dict[str, float] = {}
    total = torch.zeros((), device=b["label"].device)

    l_policy = F.cross_entropy(out["policy"], b["label"])
    parts["policy"] = float(l_policy.detach())
    total = total + w["policy"] * l_policy

    # 值头：HL-Gauss **软标签**的交叉熵（与自检里"每行和为 1"同一套目标）
    target = M.hl_gauss_targets(b["value"])
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
        l_d = (F.binary_cross_entropy_with_logits(d[:, :3], b["opp_dealin"])
               + F.binary_cross_entropy_with_logits(d[:, 3], any_deal)) / 2.0
        parts["danger"] = float(l_d.detach())
        total = total + w["danger"] * l_d
    if b.get("effect") is not None:
        scale = torch.tensor(EFFECT_SCALE, device=b["effect"].device).view(1, 1, 3)
        tgt = b["effect"] / scale
        m = b["mask"].unsqueeze(-1).expand_as(tgt)
        l_e = F.mse_loss(out["effect"][m], tgt[m])
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
             ssl_head: MaskedEventHead | None = None, mask_frac: float = 0.0) -> dict:
    """val：教师动作一致率（总/按类型）+ 首合法基线 + 各头损失 + SSL 掩码重建准确率。"""
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
        b = _batch(data, idx, device)
        ssl = None
        if ssl_head is not None and mask_frac > 0:
            evt_masked, mask_rows, target = mask_events(b["evt"], mask_frac, gen)
            b["evt"] = evt_masked
            ssl = (ssl_head, mask_rows, target)
        out = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"])
        loss, parts = compute_loss(out, b, ssl=ssl)
        loss_sum += float(loss) * idx.size
        for k, v in parts.items():
            parts_sum[k] = parts_sum.get(k, 0.0) + v * idx.size
        if ssl is not None and bool(mask_rows.any()):
            pred = ssl_head(out["e_tokens"]).argmax(-1)
            ssl_hit += int((pred[mask_rows] == target[mask_rows]).sum())
            ssl_n += int(mask_rows.sum())
        pred = out["policy"].argmax(dim=-1)
        hit += int((pred == b["label"]).sum())
        first_legal += int((b["label"] == 0).sum())
        tot += idx.size
        # 按动作类型分解（教师偏爱哪些类型、模型学会了没有）—— 类型来自数据集里的 `label_type`
        if data.get("label_type") is not None:
            tids = np.asarray(data["label_type"][idx], dtype=np.int64)
            for tid, p, y in zip(tids.tolist(), pred.tolist(), b["label"].tolist()):
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
    weights = dict(M.loss_weights())
    weights["ssl"] = args.ssl_weight
    trunk = [p for m in (model.tile, model.event, model.ctx, model.cand, model.fusion)
             for p in m.parameters()]
    heads = list(model.heads.parameters()) + list(ssl_head.parameters())
    opt = torch.optim.Adam([{"params": trunk, "lr": args.lr},
                            {"params": heads, "lr": args.lr * args.head_lr_mult}])
    mon = guard.GpuMonitor() if device == "cuda" else None
    dc = guard.DutyCycle(mon) if mon else None

    n = int(train_data["nlegal"].shape[0])
    steps = max(1, min(args.max_steps or 10 ** 9, n // args.batch))
    total_steps = steps * args.epochs
    print(f"数据：训练 {n} 条（{len(meta['train_files'])} 场）/ 验证 "
          f"{int(val_data['nlegal'].shape[0])} 条；lmax={meta['lmax']}；"
          f"标签侧 {'有' if meta['has_aux'] else '无'}")
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
                for p in trunk:
                    p.requires_grad = stage != "b"
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
            b = _batch(train_data, idx, device)
            ssl = None
            if stage in ("a", "c") and args.mask_frac > 0:
                evt_masked, mask_rows, target = mask_events(b["evt"], args.mask_frac, rng_gen)
                b["evt"] = evt_masked
                ssl = (ssl_head, mask_rows, target)
            out = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"])
            loss, parts = compute_loss(out, b, STAGE_WEIGHTS.get(stage) or weights, ssl)
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
                      mask_frac=args.mask_frac)
        row = {"epoch": epoch, "stage_at_end": cur_stage, "train_loss": run / max(1, seen),
               "train_parts": {k: v / max(1, seen) for k, v in run_parts.items()},
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
              f"ssl_acc {ev.get('ssl_acc', float('nan')):.3f}")
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
    # ⚠ 2026-09-27 实测：把 `cand` 的派生段真正喂进来之后（此前是整块 0），a/b 段的教师一致率
    #   从 0.619 涨到 **0.837**，但 c 段（联合微调、主干 lr = `--lr`）**当场把策略头练塌**
    #   （top1 掉回首合法基线 0.165，策略 CE 恒定 ⇒ 融合输出 ReLU 全死、logits 变成常数）。
    #   所以 c 段的主干 lr 必须再降一档：这个倍率乘在 a/c 的基准 lr 上（缺省 1.0 = 老行为）。
    ap.add_argument("--stage-c-lr-mult", type=float, default=1.0,
                    help="阶段 c（联合微调）学习率相对 --lr 的倍率（塌了就调小，例如 0.1）")
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
