"""**消融矩阵**（`docs/TRAINING-V4.md` §8.3 / §14 P0）：逐头 / 逐块关掉，同一预算下出表。

## 为什么要先量

v4 有 7 个头 + 16 个输入块，但从没量过"哪个值钱"：`effect`（3 维引擎标签）、`belief_hand`（102 维 BCE）
都是**纯诊断**（推理端不读），却各占一份梯度；`value`（critic）实测学不动（`rtg` 口径 EV 0.006）。
在动架构之前，先把"关掉它会发生什么"变成数字 —— 否则每一轮都在猜。

## 口径（三条，缺一条这张表就不能读）

1. **同一份预算**：同一个 `--data`/`--init`/`--seed`/`--epochs`/`--max-steps`/`--batch`，
   唯一变量就是"关掉谁"；
2. **val 指标必须用同一份消融输入**：块消融会把输入通道置 0 ⇒ `evaluate(..., ablate=...)`
   必须一起传（`pretrain.train` 已经这么做了），否则 train/val 口径分裂；
3. **点估计不是判据**：`--repeats K` 换种子重复，报 `mean ± sd`；要 CI 就得走 2+2 配对评测
   （`v4 loop`，一次 2,000 场 ≈40 分钟），本工具只负责"先看哪个方向值得花那个钱"。

用法：

    python -m mahjong_ml.v4 ablate --data S:\\mahjong-training\\compact\\v4-sp-004 \\
        --init tools/build/v4-bc-004/net.bin --epochs 1 --max-steps 300 --batch 128 \\
        --variants control,head:effect,head:belief_hand,head:danger,head:value,block:evt.stream,block:cand.derived
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from .. import paths
from . import dataset as v4ds
from . import model as M
from . import pretrain as v4pt
from . import spec
from . import value_audit as va


def parse_variant(name: str) -> tuple[str, str]:
    """`head:value` / `block:evt.stream` / `control` → `(kind, target)`。"""
    if name == "control":
        return "control", ""
    if ":" not in name:
        raise SystemExit(f"变体名要写成 control / head:<名> / block:<块 id>，收到 {name!r}")
    kind, target = name.split(":", 1)
    if kind == "head":
        if target not in M.loss_weights():
            raise SystemExit(f"未知的头 {target!r}；可选 {sorted(M.loss_weights())}")
    elif kind == "block":
        spec.block(target)                                   # 未注册 ⇒ 当场报错
    else:
        raise SystemExit(f"未知的变体类型 {kind!r}（只能用 head / block）")
    return kind, target


def run_variant(base: dict, name: str, *, seed_offset: int) -> dict:
    """跑一个变体（同一份预算），返回指标行。"""
    kind, target = parse_variant(name)
    ns = dict(base)
    ns["label"] = base["label"] + "-" + name.replace(":", "_").replace(".", "_")
    ns["seed"] = int(base["seed"]) + seed_offset
    if kind == "head":
        ns["drop_heads"] = target
    elif kind == "block":
        ns["ablate_blocks"] = target
    t0 = time.perf_counter()
    res = v4pt.train(argparse.Namespace(**ns))
    wall = time.perf_counter() - t0
    hist = res["history"][-1]
    row = {
        "variant": name, "label": ns["label"], "seed": ns["seed"], "seconds": round(wall, 1),
        "val_top1_student": hist.get("val_top1_student"),
        "val_loss_policy": hist.get("val_loss_policy"),
        "val_loss_value": hist.get("val_loss_value"),
        "val_loss_danger": hist.get("val_loss_danger"),
        "val_loss_placement": hist.get("val_loss_placement"),
        "val_loss_belief_tenpai": hist.get("val_loss_belief_tenpai"),
    }
    # 值头质量：跑一次 val 前向（与训练同一份消融输入）
    try:
        data = v4ds.load_split(ns["data"], "val")
        n = int(data["nlegal"].shape[0])
        dev = "cuda" if __import__("torch").cuda.is_available() else "cpu"
        model = va.load_model(paths.DATA_ROOT / "ckpt" / ns["label"], dev)
        prob = va.forward_value(model, data, n, dev, batch=2048)
        y = np.asarray(data["value"][:n], dtype=np.float64)
        m = va.metrics(prob, y)
        row.update({"ev_value": m["ev"], "crps": m["crps"], "crps_climatology": m["crps_climatology"],
                    "cov0.5": m["cov0.5"], "cov0.8": m["cov0.8"], "cov0.95": m["cov0.95"],
                    "mae": m["mae"], "mae_const": m["mae_const"]})
    except Exception as e:                                   # noqa: BLE001 —— 指标缺了不该让整表失败
        row["value_audit_error"] = str(e)[:80]
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mahjong_ml.v4 ablate",
                                 description="消融矩阵：逐头/逐块关掉，同一预算下出表")
    ap.add_argument("--data", required=True)
    ap.add_argument("--init", default=None,
                    help="起始权重（ckpt 或 net.bin）；缺省随机初始化")
    ap.add_argument("--label", default="v4-ablate", help="这一批的标签前缀（ckpt 子目录名）")
    ap.add_argument("--variants", default="control,head:effect,head:belief_hand,head:danger,head:value",
                    help="逗号分隔：control / head:<名> / block:<块 id>")
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--max-steps", type=int, default=300)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--eval-batch", type=int, default=256)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--stage-a", type=float, default=0.0)
    ap.add_argument("--stage-b", type=float, default=0.0)
    ap.add_argument("--objective", choices=["bc", "rwr", "ppo"], default="bc")
    ap.add_argument("--value-target", choices=["final", "rtg"], default="final")
    ap.add_argument("--mask-frac", type=float, default=0.0)
    ap.add_argument("--ssl-weight", type=float, default=0.0)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--repeats", type=int, default=1, help="同一变体换种子重复几次（报 mean±sd）")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--out", default=None, help="结果 JSON（缺省写到 ckpt 同级 ablate.json）")
    args = ap.parse_args(argv)

    base = {
        "data": args.data, "label": args.label, "epochs": args.epochs,
        "max_steps": args.max_steps, "batch": args.batch, "eval_batch": args.eval_batch,
        "lr": args.lr, "stage_a": args.stage_a, "stage_b": args.stage_b,
        "head_lr_mult": 1.0, "mask_frac": args.mask_frac, "ssl_weight": args.ssl_weight,
        "stage_c_lr_mult": 1.0, "rwr_beta": 0.0, "objective": args.objective,
        "value_target": args.value_target, "init": args.init, "seed": args.seed,
        "threads": 0, "device": args.device,
    }
    names = [v.strip() for v in args.variants.split(",") if v.strip()]
    print(f"消融矩阵：{len(names)} 个变体 × {max(1, args.repeats)} 次重复；"
          f"预算 epochs={args.epochs} max_steps={args.max_steps} batch={args.batch}")
    rows: list[dict] = []
    for name in names:
        for r in range(max(1, args.repeats)):
            # ⚠ 单个变体炸了不该把整张表丢掉：记一行错误、继续跑下一个
            #   （第一版是直接抛 —— 跑到第 7 个变体时崩，前 6 个的数字只在日志里）
            try:
                row = run_variant(base, name, seed_offset=r)
            except BaseException as e:                       # noqa: BLE001
                row = {"variant": name, "seed": int(base["seed"]) + r, "error": f"{type(e).__name__}: {e}"[:200]}
                print(f"  [{name} #{r + 1}] **失败**：{row['error']}")
            rows.append(row)
            if "error" not in row:
                print(f"  [{name} #{r + 1}] top1(学生) {row.get('val_top1_student')} · "
                      f"policy {row.get('val_loss_policy')} · value {row.get('val_loss_value')} · "
                      f"EV {row.get('ev_value')} · cov95 {row.get('cov0.95')} · {row['seconds']}s")
    # 汇总（同一变体多次 → mean±sd）
    agg: dict[str, dict] = {}
    for row in rows:
        a = agg.setdefault(row["variant"], {})
        for k, v in row.items():
            if isinstance(v, (int, float)) and k not in ("seed",):
                a.setdefault(k, []).append(float(v))
    print("\n== 汇总（mean±sd；对照 control 读差值）==")
    keys = ("val_top1_student", "val_loss_policy", "val_loss_value", "ev_value", "cov0.95")
    print(f"  {'变体':<22}" + "".join(f"{k:>18}" for k in keys))
    for name in names:
        a = agg.get(name, {})
        cells = []
        for k in keys:
            xs = a.get(k) or []
            if not xs:
                cells.append(f"{'-':>18}")
            elif len(xs) == 1:
                cells.append(f"{xs[0]:>18.4f}")
            else:
                cells.append(f"{np.mean(xs):>10.4f}±{np.std(xs):<6.4f}")
        print(f"  {name:<22}" + "".join(cells))
    out = Path(args.out) if args.out else (paths.DATA_ROOT / "ckpt" / f"{args.label}-ablate.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"base": base, "rows": rows, "agg": agg},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写 {out}")
    print("⚠ 这是**点估计**（同一预算、换种子）：要 CI 就得走 2+2 配对评测（`v4 loop`，2,000 场 ≈40 分钟）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
