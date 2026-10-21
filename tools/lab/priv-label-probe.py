#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""**神谕标签依赖**三探针（零训练、确定性；2026-10-21）。

问题（项目方文档空白）：`belief_tenpai` 头的**输入是公开信息**，但**训练标签是神谕
`opp_tenpai`**，且它经 `heads.policy_gate` 调制 policy logits。本工具只**量事实**，不裁定规范：

    A. 同一张网，`heads.policy_gate.{weight,bias}` 清零 vs 不清零 ⇒ argmax 变化率 + KL(π原‖π零)
    B. `belief_tenpai` 的**输入**（门控读的 `state` 向量）逐行打乱 / 置零 ⇒ 同上
    C. 公开代理标签（对手 3 巡内立直 / 本家放铳给该家）的监督信号质量：AUC/BCE（不重训）

判据（预注册）：A 或 B 的 argmax 变化率 >0 且 KL ≥ 0.02 ⇒ 策略在功能上依赖该头。
⛔ 本工具不训练、不改权重落盘、不写数据根；只读 compact + raw 轨迹。

用法：
    python tools/lab/priv-label-probe.py \
        --net bot-ai/wl-p301b-g06/net.bin \
        --compact .tmp/mv1-s37/compact \
        --raw S:/mahjong-training/raw/mv1-s37-compact-src \
        --out .tmp/mv1-s48/probe.json

⚠ format 2 的 `net.bin` 已被 Python 生产读端硬切退役（`v4.export.read_net` 拒绝）；本工具
自带一个**只读**的 format-2 解析器（布局与 `tools/policy-gate-check.mjs` 相同），并用
`net.json` 的 `params` 做硬校验。它不参与生产路径。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from mahjong_ml.v4 import dataset as v4ds          # noqa: E402
from mahjong_ml.v4 import export as X             # noqa: E402
from mahjong_ml.v4 import model as M              # noqa: E402
from mahjong_ml.v4.pretrain import _batch         # noqa: E402

NET_MAGIC = 0x4D4A4E4E


# ------------------------------------------------------------------ 只读 format 2
def read_net_fmt2(path: Path) -> dict:
    raw = path.read_bytes()
    magic, fmt, feat, obs, derived, dm, td, nh, vb = struct.unpack_from("<9I", raw, 0)
    if magic != NET_MAGIC or fmt != 2:
        raise SystemExit(f"{path}: 不是 format 2（magic={magic:#x} fmt={fmt}）")
    n_blocks = struct.unpack_from("<I", raw, 36)[0]
    n_tensors = struct.unpack_from("<I", raw, 40)[0]
    params = struct.unpack_from("<I", raw, 60)[0]
    off = 64
    blocks = []
    for _ in range(n_blocks):
        ln = struct.unpack_from("<H", raw, off)[0]
        off += 2
        bid = raw[off:off + ln].decode("utf-8")
        off += ln
        w = struct.unpack_from("<I", raw, off)[0]
        off += 4
        blocks.append((bid, w))
    tensors: dict[str, np.ndarray] = {}
    for _ in range(n_tensors):
        ln = struct.unpack_from("<H", raw, off)[0]
        off += 2
        name = raw[off:off + ln].decode("utf-8")
        off += ln
        nd = raw[off]
        off += 1
        shape = [struct.unpack_from("<I", raw, off + 4 * k)[0] for k in range(nd)]
        off += 4 * nd
        n = int(np.prod(shape)) if shape else 1
        arr = np.frombuffer(raw, dtype="<f4", count=n, offset=off).reshape(shape).copy()
        off += 4 * n
        tensors[name] = arr
    if off != len(raw):
        raise SystemExit(f"{path}: 张量表读完 {off} B，文件 {len(raw)} B")
    actual = int(sum(int(np.prod(v.shape)) for v in tensors.values()))
    if actual != params:
        raise SystemExit(f"{path}: params={params} 与张量表实算 {actual} 不符")
    return {"format": fmt, "feature_version": feat, "obs_version": obs,
            "derived_version": derived,
            "dims": {"d_model": dm, "tile_d": td, "n_heads": nh, "value_bins": vb},
            "blocks": blocks, "params": params, "tensors": tensors}


def load_net(path: Path, device: str):
    raw = path.read_bytes()
    fmt = struct.unpack_from("<I", raw, 4)[0]
    if fmt == 3:
        parsed = X.read_net(path)
        sd = X.state_from_net(parsed)
        arch = parsed["arch"]
        dims = parsed["dims"]
        params = parsed["params"]
    elif fmt == 2:
        parsed = read_net_fmt2(path)
        sd = X.normalize_state({k: torch.from_numpy(np.ascontiguousarray(v))
                                for k, v in parsed["tensors"].items()}, arch="v4")
        arch = "v4"
        dims = parsed["dims"]
        params = parsed["params"]
    else:
        raise SystemExit(f"不认的 net.bin format={fmt}")
    model = M.build(arch=arch, **dims).to(device)
    sd = X.adapt_to_model(sd, model, what=str(path))
    model.load_state_dict(sd, strict=True)
    model.eval()
    gate = model.heads.policy_gate
    print(f"权重 {path}：format={fmt} arch={arch} params={params:,} "
          f"gate max|w|={float(gate.weight.abs().max()):.4g} "
          f"max|b|={float(gate.bias.abs().max()):.4g}")
    return model, {"path": str(path), "format": fmt, "arch": arch, "params": params,
                   "gate_max_abs_w": float(gate.weight.abs().max()),
                   "gate_max_abs_b": float(gate.bias.abs().max())}


# ------------------------------------------------------------------ 前向 / 指标
@torch.no_grad()
def run_batches(model, data, device: str, batch: int = 2048, *,
                hook=None):
    n = int(data["cand"].shape[0])
    outs = []
    for i0 in range(0, n, batch):
        idx = np.arange(i0, min(i0 + batch, n))
        b = _batch(data, idx, device)
        h = hook[0] if hook else None
        if h is not None:
            h.remove()
        handle = None
        if hook:
            handle = model.heads.belief_tenpai.register_forward_pre_hook(hook[1])
            hook[0] = handle
        out = model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"], h=b.get("h"))
        if handle is not None:
            handle.remove()
            hook[0] = None
        outs.append({"policy": out["policy"].float().cpu(),
                     "bt": out["belief_tenpai"].float().cpu(),
                     "state": out["state"].float().cpu()})
        del b, out
    if hook and hook[0] is not None:
        hook[0].remove()
        hook[0] = None
    return {k: torch.cat([o[k] for o in outs], 0) for k in outs[0]}


def metrics(base: dict, alt: dict, mask: torch.Tensor) -> dict:
    a = base["policy"].masked_fill(~mask, float("-inf"))
    b = alt["policy"].masked_fill(~mask, float("-inf"))
    p = torch.softmax(a, -1)
    q = torch.softmax(b, -1)
    logp = torch.log(p.clamp_min(1e-30))
    logq = torch.log(q.clamp_min(1e-30))
    kl = torch.where(mask, p * (logp - logq), torch.zeros_like(p)).sum(-1)
    change = (a.argmax(-1) != b.argmax(-1))
    # 只在合法候选 >1 的行上算变化率（单候选行 argmax 必然相同）
    multi = mask.sum(-1) > 1
    return {
        "n": int(a.shape[0]),
        "n_multi": int(multi.sum()),
        "argmax_change": int(change.sum()),
        "argmax_change_rate": float(change.sum()) / int(a.shape[0]),
        "argmax_change_rate_multi": float(change[multi].sum()) / max(1, int(multi.sum())),
        "kl_mean": float(kl.mean()),
        "kl_median": float(kl.median()),
        "kl_p95": float(kl.quantile(0.95)),
        "kl_max": float(kl.max()),
        "kl_sum": float(kl.sum()),
        "bt_max_abs_delta": float((base["bt"] - alt["bt"]).abs().max()),
    }


def softmax_belief(bt: torch.Tensor) -> torch.Tensor:
    return torch.sigmoid(bt)


def auc(score: np.ndarray, y: np.ndarray) -> float:
    y = np.asarray(y).astype(np.int64)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=np.float64)
    ranks[order] = np.arange(1, len(score) + 1, dtype=np.float64)
    s = np.asarray(score)[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def bce(prob: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(np.asarray(prob, dtype=np.float64), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=np.float64)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


# ------------------------------------------------------------------ 代理标签
def load_raw_rows(raw_dir: Path):
    """`(game, hand_no, seat) -> [行, …]`（按文件内顺序；compact 行序同源）。"""
    out: dict[tuple[int, int, int], list[dict]] = {}
    files = sorted((f for f in raw_dir.iterdir() if re.fullmatch(r"g\d+\.jsonl", f.name)),
                   key=lambda p: int(p.name[1:-6]))
    for f in files:
        g = int(f.name[1:-6])
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "decision":
                continue
            o = row["obs"]
            key = (g, int(row["hand_no"]), int(o["seat"]))
            out.setdefault(key, []).append(row)
    return out


def proxy_riichi3(rows: list[dict]) -> np.ndarray:
    """每行 × 3 家：该家是否**已立直**或**在其未来 3 次摸牌内宣告立直**（公开事件）。"""
    n = len(rows)
    y = np.zeros((n, 3), dtype=np.int8)
    for i, row in enumerate(rows):
        o = row["obs"]
        s = int(o["seat"])
        for jj in range(3):
            opp = (s + 1 + jj) % 4
            if bool(o["riichi"][opp]):
                y[i, jj] = 1
                continue
            cnt = 0
            for k in range(i + 1, n):
                r2 = rows[k]
                if int(r2["hand_no"]) != int(row["hand_no"]):
                    break
                if int(r2["obs"]["seat"]) == opp and r2.get("kind") == "turn":
                    cnt += 1
                if bool(r2["obs"]["riichi"][opp]):
                    if cnt <= 3:
                        y[i, jj] = 1
                    break
                if cnt >= 3:
                    break
    return y


def align_compact(data: dict, raw_rows: dict) -> tuple[np.ndarray, np.ndarray]:
    """compact 行 → 原始决策行序号；返回 (行下标, 原始行列表下标)。"""
    game = np.asarray(data["game"], dtype=np.int64)
    hand = np.asarray(data["hand_no"], dtype=np.int64)
    seat = np.asarray(data["seat"], dtype=np.int64)
    cursor: dict[tuple[int, int, int], int] = {}
    keep, pos = [], []
    for i in range(len(game)):
        key = (int(game[i]), int(hand[i]), int(seat[i]))
        rows = raw_rows.get(key)
        if not rows:
            continue
        k = cursor.get(key, 0)
        if k >= len(rows):
            continue
        cursor[key] = k + 1
        keep.append(i)
        pos.append(k)
    return np.asarray(keep, dtype=np.int64), np.asarray(pos, dtype=np.int64)


# ------------------------------------------------------------------ 主
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--net", required=True)
    ap.add_argument("--compact", required=True)
    ap.add_argument("--raw", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--batch", type=int, default=2048)
    ap.add_argument("--seed", type=int, default=20261021)
    args = ap.parse_args(argv)

    torch.manual_seed(args.seed)
    net_path = Path(args.net)
    model, net_info = load_net(net_path, args.device)

    # 只读 train 切分（39,709 ≥3,083）。⚠ train/val 的 `cand` 宽度不同（27 vs 21），
    # 不能直接拼接；本探针不需要 val，也绝不重训。
    data = v4ds.load_split(Path(args.compact), "train")
    data = {k: v for k, v in data.items() if isinstance(v, np.ndarray)}
    n = int(data["cand"].shape[0])
    mask = torch.arange(data["cand"].shape[1])[None, :] < torch.from_numpy(
        np.asarray(data["nlegal"], dtype=np.int64))[:, None]
    print(f"语料 {args.compact}：n={n}（train 切分；只前向，不训练）")

    result: dict = {"net": net_info, "corpus": str(args.compact), "n": n,
                    "seed": args.seed, "probes": {}}

    # ---- 基线
    base = run_batches(model, data, args.device, args.batch)
    # ---- A：gate 清零
    w = model.heads.policy_gate.weight.detach().clone()
    b = model.heads.policy_gate.bias.detach().clone()
    with torch.no_grad():
        model.heads.policy_gate.weight.zero_()
        model.heads.policy_gate.bias.zero_()
    alt_a = run_batches(model, data, args.device, args.batch)
    with torch.no_grad():
        model.heads.policy_gate.weight.copy_(w)
        model.heads.policy_gate.bias.copy_(b)
    result["probes"]["A_gate_zero"] = metrics(base, alt_a, mask)
    print("A gate 清零:", json.dumps(result["probes"]["A_gate_zero"], ensure_ascii=False))

    # ---- B：belief 输入 state 逐行打乱 / 置零（只影响 belief_tenpai 的输入）
    g = torch.Generator(device="cpu").manual_seed(args.seed)

    def hook_perm(_module, inputs):
        st = inputs[0]
        perm = torch.argsort(torch.rand(st.shape, generator=g), dim=1).to(st.device)
        return (st.gather(1, perm),)

    def hook_zero(_module, inputs):
        return (torch.zeros_like(inputs[0]),)

    alt_b1 = run_batches(model, data, args.device, args.batch, hook=[None, hook_perm])
    result["probes"]["B_input_perm"] = metrics(base, alt_b1, mask)
    print("B 输入置换:", json.dumps(result["probes"]["B_input_perm"], ensure_ascii=False))
    alt_b2 = run_batches(model, data, args.device, args.batch, hook=[None, hook_zero])
    result["probes"]["B_input_zero"] = metrics(base, alt_b2, mask)
    print("B 输入置零:", json.dumps(result["probes"]["B_input_zero"], ensure_ascii=False))

    # ---- C：公开代理标签
    if args.raw:
        raw_rows = load_raw_rows(Path(args.raw))
        keep, pos = align_compact(data, raw_rows)
        print(f"C 对齐：compact 行 {n} → 原始行 {len(keep)}")
        # 每条 compact 行在原始对应键里的行列表
        game = np.asarray(data["game"], dtype=np.int64)
        hand = np.asarray(data["hand_no"], dtype=np.int64)
        seat = np.asarray(data["seat"], dtype=np.int64)
        prox = np.zeros((n, 3), dtype=np.int8)
        for ii, kk in zip(keep, pos):
            key = (int(game[ii]), int(hand[ii]), int(seat[ii]))
            rows = raw_rows[key]
            # 只需局部重算：该 key 的行序就是 compact 的 step 序
            yk = proxy_riichi3(rows)
            if kk < len(yk):
                prox[ii] = yk[kk]
        oracle = np.asarray(data["aux_opp_tenpai"], dtype=np.int64)
        dealin = np.asarray(data["aux_opp_dealin"], dtype=np.int64)
        bt = base["bt"].numpy().astype(np.float64)
        p = 1.0 / (1.0 + np.exp(-bt))
        valid = np.zeros(n, dtype=bool)
        valid[keep] = True
        rows = []
        for j in range(3):
            yo, yp, yd = oracle[:, j], prox[:, j], dealin[:, j]
            yo, yp, yd = yo[valid], yp[valid], yd[valid]
            btj, pj = bt[valid, j], p[valid, j]
            rows.append({
                "j": j, "n": int(valid.sum()),
                "base_oracle": float(yo.mean()),
                "base_riichi3": float(yp.mean()),
                "base_dealin": float(yd.mean()),
                "auc_model_oracle": auc(btj, yo),
                "bce_model_oracle": bce(pj, yo),
                "auc_model_riichi3": auc(btj, yp),
                "bce_model_riichi3": bce(pj, yp),
                "auc_model_dealin": auc(btj, yd),
                "bce_model_dealin": bce(pj, yd),
                "auc_riichi3_oracle": auc(yp.astype(np.float64), yo),
                "auc_dealin_oracle": auc(yd.astype(np.float64), yo),
                "corr_riichi3_oracle": float(np.corrcoef(yp, yo)[0, 1])
                if yp.std() > 0 and yo.std() > 0 else float("nan"),
                "corr_dealin_oracle": float(np.corrcoef(yd, yo)[0, 1])
                if yd.std() > 0 and yo.std() > 0 else float("nan"),
                "p_oracle_given_riichi3_1": float(yo[yp == 1].mean()) if (yp == 1).any() else float("nan"),
                "p_oracle_given_riichi3_0": float(yo[yp == 0].mean()) if (yp == 0).any() else float("nan"),
                "p_oracle_given_dealin_1": float(yo[yd == 1].mean()) if (yd == 1).any() else float("nan"),
                "p_oracle_given_dealin_0": float(yo[yd == 0].mean()) if (yd == 0).any() else float("nan"),
            })
        result["probes"]["C_proxy"] = rows
        print("C 代理标签:", json.dumps(rows, ensure_ascii=False, indent=2))

    # ---- 判据（只报事实）
    a = result["probes"]["A_gate_zero"]
    b1 = result["probes"]["B_input_perm"]
    b2 = result["probes"]["B_input_zero"]
    dep = ((a["argmax_change_rate"] > 0 and a["kl_mean"] >= 0.02)
           or (b1["argmax_change_rate"] > 0 and b1["kl_mean"] >= 0.02)
           or (b2["argmax_change_rate"] > 0 and b2["kl_mean"] >= 0.02))
    result["fact_dependency"] = bool(dep)
    result["criterion"] = "A 或 B：argmax 变化率 >0 且 KL_mean ≥0.02 ⇒ 功能依赖"

    out = Path(args.out) if args.out else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写 {out}")
    print(f"事实：{'依赖' if dep else '不依赖'}（只报事实，不裁定规范）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
