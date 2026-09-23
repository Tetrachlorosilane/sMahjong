"""导出给**纯 Java 前向**用的东西（`docs/TRAINING.md` §2 的 B 形态）。

两件事：

    python -m mahjong_ml.export weights --ckpt <model.pt> --out <weights.bin>
       把训练好的候选打分网络导出成**定长二进制**（float32，行主序，按固定顺序排张量）。
       Java 侧 `mahjong.ai.NeuralPolicy` 读它做手写前向 —— 这是"服务端保持零第三方依赖"的关键。

    python -m mahjong_ml.export golden --trace <轨迹目录> --out python/tests/golden/forward.bin
       造一份 **golden 对拍夹具**：随机权重的小网络 + 若干真实 obs/legal，
       连同 Python 算出的 state/cand/logits 一起打包。Java 的 `SelfTest.neuralForwardTests`
       读它，**逐元素**核对"特征拼装 + 前向"两侧一致（容差 1e-4）。
       ⚠ 这是 train/serve 一致性的唯一防线：不钉住它，训练与推理就是两个不同的东西。

二进制格式（小端，Java/Python 两侧共用；`WEIGHT_MAGIC` / `GOLDEN_MAGIC` 各自校验）：

    权重：magic("MJNN") u32 · version u32 · state_dim u32 · cand_dim u32 · hidden u32 · head u32
          · trunk_layers u32
          然后按顺序：每个 trunk 层 W(hidden×in) b(hidden) …、打分头 W(head×(hidden+cand_dim)) b(head)、
          W(1×head) b(1)。全部 float32 行主序、紧凑无对齐。

    夹具：magic("MJGF") u32 · version u32 · n_cases u32 · weights_len u32 · weights 字节
          然后每个 case：obs_len u32 · obs(utf8) · n_legal u16 · (key_len u16 · key)×n
          · state(state_dim×f32) · cand(n×cand_dim×f32) · logits(n×f32)
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np
import torch

from . import features, nets

WEIGHT_MAGIC = 0x4D4A4E4E      # "MJNN"
GOLDEN_MAGIC = 0x4D4A4746      # "MJGF"
FORMAT_VERSION = 1


# ------------------------------------------------------------------ 权重：torch → 二进制

def weight_blob(model: nets.CandidateScorer) -> bytes:
    """把模型压成定长二进制（**固定顺序**，两侧都按同一顺序读）。"""
    sd = {k: v.detach().cpu().numpy().astype("<f4") for k, v in model.state_dict().items()}
    parts = [struct.pack("<7I", WEIGHT_MAGIC, FORMAT_VERSION, model.state_dim,
                         model.cand_dim, model.hidden, model.head_dim, model.trunk_layers)]
    for i in range(model.trunk_layers):
        parts.append(sd[f"trunk.{i * 2}.weight"].tobytes())
        parts.append(sd[f"trunk.{i * 2}.bias"].tobytes())
    parts.append(sd["head.0.weight"].tobytes())
    parts.append(sd["head.0.bias"].tobytes())
    parts.append(sd["head.2.weight"].tobytes())
    parts.append(sd["head.2.bias"].tobytes())
    return b"".join(parts)


def save_weights(model: nets.CandidateScorer, out: str | Path) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(weight_blob(model))
    meta = {"state_dim": model.state_dim, "cand_dim": model.cand_dim, "hidden": model.hidden,
            "head": model.head_dim, "trunk_layers": model.trunk_layers,
            "feature_version": features.FEATURE_VERSION,
            "derived_version": features.DERIVED_VERSION,
            "params": model.n_params(), "bytes": p.stat().st_size}
    p.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ------------------------------------------------------------------ 特征（复用唯一规格）

def full_features(obs: dict, legal: list[str],
                  danger: np.ndarray | None = None,
                  cand_derived: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """`features.py` 的完整向量（状态 607 / 候选 n×96）—— 夹具与训练都用它，保证同源。"""
    state = features.state_vector(obs, None if danger is None else danger.tolist())
    cand = features.candidates(state, legal, None if cand_derived is None else cand_derived.tolist())
    return state, cand


def _iter_cases(trace_dir: Path, want: int):
    """从真实轨迹里挑几类决策（自家回合 / 鸣牌 / 有副露 / 立直），覆盖特征的不同分支。

    yield `(文件, 该文件里的第几条决策, 行)` —— 行号要与 sidecar 的行序对齐。
    """
    picked: list[tuple[Path, int, dict]] = []
    seen_kinds: set[str] = set()
    for f in sorted(trace_dir.glob("g*.jsonl")):
        idx = 0
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "decision":
                continue
            obs = row["obs"]
            tag = row["kind"] + ("+meld" if any(obs["melds"][obs["seat"]]) else "")
            if tag in seen_kinds and len(picked) >= want // 2:
                idx += 1
                continue
            seen_kinds.add(tag)
            picked.append((f, idx, row))
            idx += 1
            if len(picked) >= want:
                return picked
    return picked


# ------------------------------------------------------------------ 夹具

def build_golden(trace_dir: str | Path, out: str | Path, *, cases: int = 6,
                 hidden: int = 32, head: int = 16, seed: int = 20260101) -> Path:
    """写 golden 夹具：小网络（固定种子）+ 真实 obs + Python 侧算出的 state/cand/logits。"""
    torch.manual_seed(seed)
    model = nets.build(features.state_dim(), features.cand_dim(), hidden=hidden, head=head)
    model.eval()
    blob = weight_blob(model)

    rows = _iter_cases(Path(trace_dir), cases)
    if not rows:
        raise FileNotFoundError(f"{trace_dir} 里没读到 decision 行")
    body = bytearray()
    for src_file, row_idx, row in rows:
        obs = row["obs"]
        legal = list(row["legal"])
        danger = cand_der = None
        sc = src_file.resolve().parent / (src_file.name.split(".")[0] + ".feat.bin")
        if sc.is_file():
            from . import dataset as ds
            s = ds.load_sidecar(sc)
            if row_idx < s["n"]:
                danger = s["danger"][row_idx]
                off0, off1 = int(s["offsets"][row_idx]), int(s["offsets"][row_idx + 1])
                cand_der = s["cand"][off0:off1]
        state, cand = full_features(obs, legal, danger, cand_der)
        with torch.no_grad():
            logits = model(torch.from_numpy(state[None, :]),
                           torch.from_numpy(cand[None, :, :])).numpy()[0]
        obs_b = json.dumps(obs, ensure_ascii=False).encode("utf-8")
        body += struct.pack("<I", len(obs_b)) + obs_b
        body += struct.pack("<H", len(legal))
        for k in legal:
            kb = k.encode("utf-8")
            body += struct.pack("<H", len(kb)) + kb
        body += state.astype("<f4").tobytes()
        body += cand.astype("<f4").tobytes()
        body += logits.astype("<f4").tobytes()

    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    head_b = struct.pack("<5I", GOLDEN_MAGIC, FORMAT_VERSION, len(rows), len(blob), 0)
    p.write_bytes(head_b + blob + bytes(body))
    meta = {"cases": len(rows), "state_dim": features.state_dim(), "cand_dim": features.cand_dim(),
            "hidden": hidden, "head": head, "feature_version": features.FEATURE_VERSION,
            "derived_version": features.DERIVED_VERSION, "bytes": p.stat().st_size,
            "trace": str(trace_dir), "seed": seed,
            "case_kinds": [r[2]["kind"] for r in rows]}
    p.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导出 Java 侧前向要用的权重 / golden 夹具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("weights", help="训练好的 checkpoint → 二进制权重")
    w.add_argument("--ckpt", required=True)
    w.add_argument("--out", required=True)
    g = sub.add_parser("golden", help="真实轨迹 → golden 对拍夹具")
    g.add_argument("--trace", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--cases", type=int, default=6)
    args = ap.parse_args(argv)

    if args.cmd == "weights":
        ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
        cfg = ck["config"]
        model = nets.build(cfg["state_dim"], cfg["cand_dim"], hidden=cfg["hidden"], head=cfg["head"])
        model.load_state_dict(ck["model"])
        model.eval()
        p = save_weights(model, args.out)
        print(f"已导出权重：{p}（{p.stat().st_size} 字节，{model.n_params()} 参数）")
        print(f"  ↓ 服务端用法：java -jar mahjong-server.jar --selfplay 100 "
              f"--policy net:{p},teacher,teacher,teacher --out <dir>")
    else:
        p = build_golden(args.trace, args.out, cases=args.cases)
        print(f"已写出 golden 夹具：{p}（{p.stat().st_size} 字节，{args.cases} 个用例）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
