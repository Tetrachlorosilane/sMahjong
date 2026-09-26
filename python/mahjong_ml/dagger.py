"""P2 · DAgger 一轮（`docs/TRAINING.md` §4 P2）—— 一条命令跑完：采集 → 派生特征 → 校验 → 建数据集 → 重训 → 对比。

    python -m mahjong_ml.dagger --student S:\\mahjong-training\\ckpt\\bc-002 \\
                                --bc-src S:\\mahjong-training\\raw\\bc-001 --label bc-003 \\
                                --round 1 --games 300 --workers 24

**为什么要有这个脚本**（而不是把几条命令抄进文档）：

  · DAgger 的每一步**必须一起看**才判得出来 —— 学生状态分布偏了多少、老师标注覆盖率、
    重训后在**学生自己的状态**上向老师靠拢了多少。散着跑很容易只报一句"重训完了"，
    而看不出到底有没有赢（这是"诚实优先"的直接要求，见 §7）。
  · 对比必须**同一批学生状态**上做：基线与新模型都评 `compact/dagger-rN` 的 val 切分；
    显著性用**按场聚类**的配对 bootstrap（同场决策不独立，逐行 CI 会假窄）。
  · 三条硬约束（§0.1）落在每一步：java 侧 `--workers`（CPU）、`bc.train` 里的 `guard`（GPU/线程）、
    数据只落 S 盘（`paths`，配额闸门在里面）。

断点续跑：`--skip-collect` / `--skip-features` / `--skip-validate`（重训与对比总会重做）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from argparse import Namespace
from pathlib import Path

import numpy as np
import torch

from . import bc, dataset as ds, export, features, guard, nets, paths
from . import producer as producers

ROOT = Path(__file__).resolve().parents[2]          # 仓库根（python/mahjong_ml/dagger.py → 上三级）
JAR = ROOT / "server" / "build" / "mahjong-server.jar"
CHECK = ROOT / "tools" / "selfplay-check.mjs"


# ------------------------------------------------------------------ 子进程

def run(cmd: list[str], what: str) -> float:
    """跑一个外部程序（java / node）。**stdout 一律继承**：

    本仓库的开发沙箱下"捕获管道"会 EPERM（见 AGENTS.md §4 与 NOTES），所以这里不抓输出 ——
    要判读的结论（场数、决策数、校验通过与否）走 `summary.json` 与**退出码**，不从 stdout 里刮。
    """
    print(f"\n$ {' '.join(cmd)}", flush=True)
    t0 = time.perf_counter()
    rc = subprocess.run(cmd, cwd=str(ROOT)).returncode
    dt = time.perf_counter() - t0
    if rc != 0:
        raise SystemExit(f"{what} 失败（退出码 {rc}）—— 先看上面它自己的输出")
    print(f"  → {what}：{dt:.1f}s")
    return dt


def policy_string(net_bin: Path) -> str:
    """学生坐 0/2 号位、老师坐 1/3（配 `--rotate` 逐场轮转 → 学生把四个座位都坐一遍）。"""
    spec = f"net:{net_bin}"
    if "," in spec:
        raise SystemExit(f"net.bin 路径里有逗号，而 --policy 用逗号分隔策略：{spec}")
    return f"{spec},teacher,{spec},teacher"


def ensure_net_bin(ckpt_dir: Path) -> Path:
    """学生要**跑在服务端进程里**，所以必须有纯 Java 前向能读的 `net.bin`（没有就现导）。"""
    nb = ckpt_dir / "net.bin"
    if nb.is_file():
        return nb
    ck = torch.load(ckpt_dir / "model.pt", map_location="cpu", weights_only=False)
    cfg = ck["config"]
    model = nets.build(cfg["state_dim"], cfg["cand_dim"], hidden=cfg["hidden"], head=cfg["head"])
    model.load_state_dict(ck["model"])
    export.save_weights(model, nb)
    print(f"导出 net.bin（{nb}，{nb.stat().st_size} B）")
    return nb


# ------------------------------------------------------------------ 统计

def cluster_bootstrap_diff(base: np.ndarray, new: np.ndarray, cluster: np.ndarray, *,
                           iters: int = 2000, seed: int = 20260101) -> dict:
    """配对差 `new − base` 的**按场聚类** bootstrap 置信区间。

    行级 bootstrap 在这里是**错的**：一局里的决策高度相关（同一手牌、同一牌河），
    当成独立样本会把区间缩窄若干倍，于是"涨了 2 个点"看起来显著、其实只是同一局多记了几条。
    按场重采样后，CI 反映的是"换一批牌局还成不成立"。
    """
    games = np.unique(cluster)
    if games.size < 2:
        return {"mean": float(new.mean() - base.mean()), "lo": float("nan"), "hi": float("nan"),
                "p_win": float("nan"), "games": int(games.size), "iters": 0}
    by_game = {g: np.flatnonzero(cluster == g) for g in games}
    rng = np.random.default_rng(seed)
    diffs = np.empty(iters, dtype=np.float64)
    for i in range(iters):
        pick = rng.choice(games, size=games.size, replace=True)
        sel = np.concatenate([by_game[g] for g in pick])
        diffs[i] = new[sel].mean() - base[sel].mean()
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"mean": float(new.mean() - base.mean()), "lo": float(lo), "hi": float(hi),
            "p_win": float((diffs > 0).mean()), "games": int(games.size), "iters": iters}


def compare_arms(data_dir: Path, split: str, arms: dict[str, Path], labels: dict[str, str],
                 device: str, batch: int, pairs: list[tuple[str, str]]) -> dict:
    """多个模型在**同一批状态**上的师生一致率（= 对老师标签的 top-1），每个配对差带聚类 CI。

    `pairs` 形如 `[("baseline", "dagger"), ("control", "dagger")]`（差 = 后者 − 前者）。
    多臂是**必须的**：只比"基线 vs 新模型"会把"数据量变多"和"数据分布变对"混在一起
    （P2 第一轮就撞上了这个混淆 —— 两个分布的涨幅几乎一样，见 TRAINING.md §4 P2 的记录）。
    """
    data = ds.load_split(data_dir, split)
    rows = {name: _rows_of(ck, data, device, batch) for name, ck in arms.items()}
    out = {"data": str(data_dir), "split": split, "n": int(data["state"].shape[0]),
           "games": int(np.unique(data["game"]).size),
           "meta_label_source": data["meta"].get("label_source"),
           "teacher_labeled": data["meta"].get(f"teacher_labeled_{split}"),
           "arms": {}, "pairs": {}}
    for name, r in rows.items():
        ev = bc.eval_ckpt(str(data_dir), str(arms[name]), split, "cpu", batch=batch)
        out["arms"][name] = {"label": labels.get(name, name), "ckpt": str(arms[name]),
                             "top1": float(r["correct"].mean()), "top1_type": ev["top1_type"],
                             "nll": ev["nll"], "first_legal_acc": ev["first_legal_acc"],
                             "majority_type_acc": ev["majority_type_acc"]}
    for a, b in pairs:
        out["pairs"][f"{b} − {a}"] = cluster_bootstrap_diff(
            rows[a]["correct"], rows[b]["correct"], rows[a]["game"])
    return out


def fmt_cmp(title: str, c: dict) -> str:
    lines = [f"{title}（n={c['n']} 条 / {c['games']} 场，标签源 {c['meta_label_source']}"
             f"，其中 {c['teacher_labeled']} 条带老师标注）"]
    for a in c["arms"].values():
        lines.append(f"  {a['label']:<22} top-1 {a['top1']:.4f}（类型 {a['top1_type']:.3f}，"
                     f"nll {a['nll']:.4f}）")
    lines.append(f"  同粒度基线（多数动作类型）{c['arms'][next(iter(c['arms']))]['majority_type_acc']:.4f}")
    for k, d in c["pairs"].items():
        lines.append(f"  Δ {k}：{d['mean']:+.4f}"
                     f"（按场聚类 95% CI {d['lo']:+.4f}..{d['hi']:+.4f}，P(涨)={d['p_win']:.3f}）")
    return "\n".join(lines)


def _rows_of(ckpt: Path, data: dict, device: str, batch: int) -> dict:
    """逐行预测（要的是**行级**对错与场号，`eval_ckpt` 只给聚合数）。"""
    ck = torch.load(ckpt, map_location="cpu", weights_only=False)
    cfg = ck["config"]
    model = nets.build(cfg["state_dim"], cfg["cand_dim"], hidden=cfg["hidden"], head=cfg["head"])
    model.load_state_dict(ck["model"])
    model.to(device)
    return bc.predict_rows(model, data, device, batch=batch)


def _no_numpy(o):
    """报告里不许出现 numpy 数组：逐行预测有几万条，塞进去只会让报告没法读，还会掩盖"忘了聚合"。"""
    raise TypeError(f"报告里不许出现 {type(o).__name__}（先把指标聚合好再写）")


def _split_files_of(compact_dir: Path, split: str) -> list[Path]:
    """紧凑集某个切分的文件清单（**全路径**）。

    优先读 meta 里的 `*_files_path`；**旧数据集没有这个键**（此键 2026-09 才加）时，
    用 meta 里记着的 `src` + `split_seed` + `val_frac` **重算一遍**（`split_files` 是确定性的），
    并要求重算结果的文件名与 meta 里记的**逐一对上** —— 对不上就报错，不许"差不多就行"。

    为什么非要全路径：多来源合并时每个目录都从 `g0` 起，**文件名必然重名**，
    靠名字判"这一场属于 BC 还是 dagger"会把两批数据搅在一起（受控切分就废了）。
    """
    meta = json.loads((compact_dir / "meta.json").read_text(encoding="utf-8"))
    key = f"{split}_files_path"
    if key in meta:
        return [Path(p) for p in meta[key]]
    # ⚠ `src` 在**更早的**数据集里是**一个字符串**（多来源合并之后才改成列表）—— 两种都要认，
    #   否则 `for s in "S:\\..."` 会逐字符去 glob（报"S 里没有 g*.jsonl"，那种错最费时间）
    srcs = meta["src"]
    srcs = [srcs] if isinstance(srcs, str) else list(srcs)
    files = ds.trace_files_multi([Path(s) for s in srcs])
    train, val = ds.split_files(files, meta["val_frac"], meta["split_seed"])
    got = train if split == "train" else val
    if sorted(p.name for p in got) != sorted(meta[f"{split}_files"]):
        raise SystemExit(f"{compact_dir} 的 {split} 清单重算对不上 meta —— 这个数据集不能当裁判，"
                         f"重新 dataset build 一次")
    return got


# ------------------------------------------------------------------ 主流程

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="P2 DAgger 一轮（采集→特征→校验→建集→重训→对比）")
    ap.add_argument("--student", required=True, help="学生 checkpoint 目录（含 model.pt）")
    ap.add_argument("--round", type=int, default=1, help="第几轮（决定 raw/compact 目录名 dagger-rN）")
    ap.add_argument("--raw", default=None,
                    help="轨迹目录（缺省 raw\\dagger-rN）—— 数据可能来自编排器之外或多轮追加")
    ap.add_argument("--games", type=int, default=300, help="采集场数")
    ap.add_argument("--workers", type=int, default=guard.SELFPLAY_WORKERS,
                    help="java 并行线程（约束③：活跃线程 ≤ 75%% 的核）")
    ap.add_argument("--bc-src", action="append", default=[], help="BC 原始轨迹目录（可重复；聚合进混合集）")
    ap.add_argument("--seed", type=int, default=20260215)
    ap.add_argument("--label", default=None, help="新 checkpoint 名（缺省 dagger-rN）")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=4096)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--head", type=int, default=128)
    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--device", default="auto")
    ap.add_argument("--bc-compact", default=None,
                    help="（可选）原 BC 紧凑集目录：用它给出**老师状态**的无泄漏参照组")
    ap.add_argument("--val-frac", type=float, default=0.1, help="混合集的验证比例（按整场切）")
    ap.add_argument("--split-seed", type=int, default=20260101, help="切分种子（决定哪几场当裁判）")
    ap.add_argument("--control-games", type=int, default=0,
                    help="对照臂：额外采 N 场**纯 teacher** 轨迹，训一个**同数据量的 BC-only** 模型 —— "
                         "用来把「数据量变多」与「数据分布变对（DAgger）」分开（0 = 不做）")
    ap.add_argument("--control-label", default=None, help="对照臂 checkpoint 名（缺省 bc-ctrl-rN）")
    ap.add_argument("--control-max-decisions", type=int, default=None,
                    help="对照臂训练集**总量截断**（把对照臂裁到与 DAgger 臂同量 —— 否则"
                         "「对照多出的那几万行」会把结论搅浑，第一轮就吃过这个亏）")
    ap.add_argument("--skip-collect", action="store_true")
    ap.add_argument("--skip-features", action="store_true")
    ap.add_argument("--skip-validate", action="store_true")
    ap.add_argument("--skip-control-data", action="store_true",
                    help="对照臂的轨迹与 sidecar 已在盘上（只跳过采集+富化，校验照跑）")
    ap.add_argument("--skip-build", action="store_true",
                    help="复用已有的紧凑集（建集要读一千多场，重跑只为对比时不必重来；"
                         "复用前**校验 meta 的来源与切分参数**一致）")
    args = ap.parse_args(argv)

    paths.ensure_root()
    student = Path(args.student)
    label = args.label or f"dagger-r{args.round}"
    raw_dir = Path(args.raw) if args.raw else paths.DATA_ROOT / "raw" / f"dagger-r{args.round}"
    compact_dir = paths.DATA_ROOT / "compact" / f"dagger-r{args.round}"
    mix_dir = paths.DATA_ROOT / "compact" / f"mix-r{args.round}"
    teacher_dir = paths.DATA_ROOT / "compact" / f"teacher-r{args.round}"
    ctrl_raw = paths.DATA_ROOT / "raw" / f"control-r{args.round}"
    ctrl_dir = paths.DATA_ROOT / "compact" / f"control-r{args.round}"
    ctrl_label = args.control_label or f"bc-ctrl-r{args.round}"
    net_bin = ensure_net_bin(student)
    print(f"学生：{student}（net.bin {net_bin.stat().st_size} B → 纯 Java 前向）")
    print(f"数据：{paths.report()}")

    timings: dict[str, float] = {}
    # ① 采集：学生在场打球 → 学生状态；学生座位额外记一次老师的动作（DAgger 的核心）
    if not args.skip_collect:
        timings["collect"] = run(
            producers.selfplay_cmd(args.games, args.workers, policy_string(net_bin), args.seed,
                                   raw_dir, teacher_label=True),
            f"采集 {args.games} 场（老师标注学生座位）")
    else:
        print(f"跳过采集（用已有的 {raw_dir}）")
    # ①b 对照臂的数据：**纯 teacher** 轨迹（全 teacher 座位 → 没有学生状态，也没有标注需求）
    if args.control_games:
        have_traces = (ctrl_raw / "summary.json").is_file()
        n_jsonl = len(list(ctrl_raw.glob("g*.jsonl"))) if ctrl_raw.is_dir() else 0
        n_side = len(list(ctrl_raw.glob("g*.feat.bin"))) if ctrl_raw.is_dir() else 0
        if not have_traces and not args.skip_collect:
            timings["control_collect"] = run(
                producers.selfplay_cmd(args.control_games, args.workers, "teacher",
                                       args.seed + 1, ctrl_raw),
                f"采集对照臂 {args.control_games} 场（纯 teacher）")
        elif not have_traces:
            raise SystemExit(f"对照臂轨迹不在 {ctrl_raw} —— 去掉 --skip-collect（或 --control-games 0）")
        if n_side < n_jsonl and not args.skip_features:
            timings["control_features"] = run(producers.features_cmd(ctrl_raw),
                                              "对照臂派生特征 sidecar")
        elif n_side < n_jsonl:
            raise SystemExit(f"对照臂 sidecar 不全（{n_side}/{n_jsonl}）—— 去掉 --skip-features")
        if not (args.skip_validate or args.skip_control_data):
            run(["node", str(CHECK), str(ctrl_raw)], "对照臂数据集校验")
    summary = json.loads((raw_dir / "summary.json").read_text(encoding="utf-8"))
    games = summary.get("games") or len(list(raw_dir.glob("g*.jsonl")))
    by = summary.get("by_policy", {})
    print(f"轨迹：{games} 场 / {len(list(raw_dir.glob('g*.jsonl')))} 个 jsonl"
          f"（{summary.get('games_per_second'):.2f} 场/秒，{summary.get('decisions_per_second'):.0f} 决策/秒）")
    for k, v in by.items():                          # 顺位点是"和牌收支"的粗代理，只作采集健全性检查
        print(f"  {k if k == 'teacher' else 'net:<学生>'}: {v['games']} 席场，"
              f"平均顺位 {v['avg_place']:.3f}，顺位点 {v['avg_rank_points']:+.1f}，"
              f"和了 {v['win_rate'] * 100:.1f}%，放铳 {v['deal_in_rate'] * 100:.1f}%")

    # ② 派生特征（危险度/向听/dora… 由服务端算，Python 只读 —— 不许两边各算一份）
    if not args.skip_features:
        timings["features"] = run(producers.features_cmd(raw_dir), "派生特征 sidecar")
    else:
        print("跳过特征生成（假定 sidecar 已在）")

    # ③ 校验：**独立实现**的校验器（不复用训练侧的读取代码，否则等于自己批自己的卷）
    if not args.skip_validate:
        timings["validate"] = run(["node", str(CHECK), str(raw_dir)], "数据集校验（selfplay-check）")
    else:
        print("跳过校验")

    # ④ 建集。⚠ **顺序是有讲究的**：先建混合集（训练用），再从它的 **val 切分**里挑出评测集。
    #    为什么不能直接在 `compact/dagger-rN` 的 val 上比：那 10% 与混合集的**训练**切分大面积重叠
    #    （都来自同一批 dagger 文件、只是种子不同），新模型十有八九已经**背过**那些场 —— 比出来的
    #    "涨了"只是泄漏。所以评测集必须是**两个模型都没训过**的那部分：
    #      · 学生状态集 = 混合集 val ∩ dagger 文件（新模型只吃混合集 train；基线根本没碰过 dagger）
    #      · 老师状态集 = 混合集 val ∩ 原 BC 集的 val（基线也没背过这批）
    #    两者都用 `val_frac=1.0` 建成"全 val"的紧凑集 —— 评测集里一条训练样本都不留。
    t0 = time.perf_counter()
    srcs = ([Path(p) for p in args.bc_src] + [raw_dir]) if args.bc_src else [raw_dir]
    if args.skip_build and (mix_dir / "meta.json").is_file():
        # 复用前先校验：来源、切分种子、比例三项都要对得上，否则"复用"回来的可能是别的实验
        m_mix = json.loads((mix_dir / "meta.json").read_text(encoding="utf-8"))
        want_src = [str(s) for s in srcs]
        got_src = m_mix["src"] if isinstance(m_mix["src"], list) else [m_mix["src"]]
        if got_src != want_src or m_mix["split_seed"] != args.split_seed \
                or abs(m_mix["val_frac"] - args.val_frac) > 1e-12:
            raise SystemExit(f"{mix_dir} 是别的实验建的（src/seed/val_frac 对不上）——"
                             f"去掉 --skip-build 重建")
        print(f"复用已有混合集 {mix_dir}（meta 校验通过）")
    else:
        m_mix = ds.build(srcs, mix_dir, val_frac=args.val_frac, split_seed=args.split_seed,
                         label_source="auto", quiet=False)
    print(f"混合集（{len(srcs)} 个来源）：训 {m_mix['train_decisions']} / 验 {m_mix['val_decisions']}"
          f"；老师标注 训 {m_mix['teacher_labeled_train']} / 验 {m_mix['teacher_labeled_val']}")
    mix_val = _split_files_of(mix_dir, "val")
    mix_train = {str(p) for p in _split_files_of(mix_dir, "train")}
    student_eval_files = [p for p in mix_val if p.parent == raw_dir]
    if not student_eval_files:
        raise SystemExit(f"混合集的 val 里一场 dagger 轨迹都没有（{raw_dir}）——"
                         f"调 --val-frac/--split-seed，否则没法做无泄漏对比")
    if args.skip_build and (compact_dir / "meta.json").is_file():
        m_stu = json.loads((compact_dir / "meta.json").read_text(encoding="utf-8"))
        print(f"复用已有评测集 {compact_dir}（{m_stu['val_decisions']} 条）")
    else:
        m_stu = ds.build(student_eval_files, compact_dir, val_frac=1.0, label_source="auto",
                         quiet=False)
    print(f"评测集（学生状态，全部当 val、两边都没训过）：{m_stu['val_decisions']} 条 / "
          f"{len(student_eval_files)} 场，其中 {m_stu['teacher_labeled_val']} 条带老师标注")
    teacher_eval_files: list[Path] = []
    if args.bc_compact:                       # 参照组：老师状态上用同一把尺子（量化分布差距）
        bc_val = {str(p) for p in _split_files_of(Path(args.bc_compact), "val")}
        teacher_eval_files = [p for p in mix_val if str(p) in bc_val]
        if teacher_eval_files:
            if args.skip_build and (teacher_dir / "meta.json").is_file():
                m_tea = json.loads((teacher_dir / "meta.json").read_text(encoding="utf-8"))
                print(f"复用已有评测集 {teacher_dir}（{m_tea['val_decisions']} 条）")
            else:
                m_tea = ds.build(teacher_eval_files, teacher_dir, val_frac=1.0, label_source="auto",
                                 quiet=False)
            print(f"评测集（老师状态，同样的无泄漏口径）：{m_tea['val_decisions']} 条 / "
                  f"{len(teacher_eval_files)} 场")
        else:
            print("⚠ 混合集 val ∩ BC 集 val 是空的 → 跳过老师状态参照（样本太小，比了也没意义）")
    # **无泄漏闸门**：上面那套口径是"嘴上说没泄漏"，这里把它验掉 —— 评测集的每一场都不能出现在
    # 任何一个模型的训练集里（新模型 = 混合集 train；基线 = 原 BC 集的 train，它没见过 dagger）。
    bc_train = ({str(p) for p in _split_files_of(Path(args.bc_compact), "train")}
                if args.bc_compact else set())
    bad_stu = [p for p in student_eval_files if str(p) in mix_train]
    bad_tea = [p for p in teacher_eval_files if str(p) in (mix_train | bc_train)]
    if bad_stu or bad_tea:
        raise SystemExit(f"评测集混进了训练场（学生 {len(bad_stu)} 场 / 老师 {len(bad_tea)} 场）"
                         f"—— 换个 --split-seed 重跑，别拿泄漏的数字当结论")
    leak = {"student_eval_files": len(student_eval_files),
            "teacher_eval_files": len(teacher_eval_files),
            "eval_files_in_any_train": 0,
            "mix_train_files": len(mix_train), "bc_train_files": len(bc_train)}
    timings["build"] = time.perf_counter() - t0

    # ⑤ 重训（guard 在 bc.train 里：CPU 线程 + GPU 占空比）
    print("\n" + "=" * 78)
    result = bc.train(Namespace(
        data=str(mix_dir), out=None, label=label, epochs=args.epochs, batch=args.batch,
        lr=args.lr, hidden=args.hidden, head=args.head, threads=guard.TRAIN_THREADS,
        max_steps=args.max_steps, seed=args.seed, device=args.device))
    new_ckpt_dir = paths.DATA_ROOT / "ckpt" / label
    new_net = ensure_net_bin(new_ckpt_dir)          # 下一轮的自对弈要它
    print(f"新模型 net.bin：{new_net}")

    # ⑤b 对照臂：**同样多的数据，但全是 teacher 轨迹**（BC-only）。它回答的是
    #     "涨幅到底是数据量带来的，还是 DAgger 的学生分布带来的" —— 没有它，这一轮只能说
    #     "新模型更像老师了"，而不能说"DAgger 有用"（两者的差别见 TRAINING.md §4 P2）。
    ctrl_ckpt_dir: Path | None = None
    if args.control_games:
        eval_files = {str(p) for p in student_eval_files + teacher_eval_files}
        ctrl_srcs = [Path(p) for p in args.bc_src] + [ctrl_raw]
        ctrl_train = [f for f in ds.trace_files_multi(ctrl_srcs) if str(f) not in eval_files]
        print("\n" + "=" * 78)
        m_ctrl = ds.build(ctrl_train, ctrl_dir, val_frac=1e-9, split_seed=args.split_seed,
                          label_source="auto", quiet=False,
                          max_decisions=args.control_max_decisions)
        print(f"对照集：{len(ctrl_train)} 场（BC + 纯 teacher 新数据，**剔除了评测场**）；"
              f"训 {m_ctrl['train_decisions']} / 验 {m_ctrl['val_decisions']}"
              f"（截断={args.control_max_decisions}；DAgger 臂训 {m_mix['train_decisions']} 行）")
        print("=" * 78)
        bc.train(Namespace(
            data=str(ctrl_dir), out=None, label=ctrl_label, epochs=args.epochs, batch=args.batch,
            lr=args.lr, hidden=args.hidden, head=args.head, threads=guard.TRAIN_THREADS,
            max_steps=args.max_steps, seed=args.seed, device=args.device))
        ctrl_ckpt_dir = paths.DATA_ROOT / "ckpt" / ctrl_label

    # ⑥ 对比：**同一批状态**上多臂比（基线 / 新模型 / 对照），差带按场聚类的 CI
    device = "cuda" if (args.device == "auto" and torch.cuda.is_available()) else \
             ("cpu" if args.device == "auto" else args.device)
    arms = {"baseline": student / "model.pt", "dagger": new_ckpt_dir / "model.pt"}
    labels = {"baseline": "基线（BC 27.9 万）", "dagger": f"DAgger（{label}）"}
    pairs = [("baseline", "dagger")]
    if ctrl_ckpt_dir:
        arms["control"] = ctrl_ckpt_dir / "model.pt"
        labels["control"] = "对照（BC-only 同量）"
        pairs.append(("control", "dagger"))       # ← **这一对**才是 DAgger 的净效应
    print("\n" + "=" * 78)
    on_student = compare_arms(compact_dir, "val", arms, labels, device, args.batch, pairs)
    print(fmt_cmp("【学生状态】与老师的一致率", on_student))
    report = {"round": args.round, "student": str(student), "new_label": label,
              "games_collected": games, "raw": str(raw_dir), "compact": str(compact_dir),
              "mix": str(mix_dir), "train": result["history"][-1],
              "wall_seconds": result["wall_seconds"], "params": result["model"],
              "on_student_states": on_student, "timings": timings, "leak_check": leak}
    if ctrl_ckpt_dir:
        # 把"两臂各训了多少行"写进报告：差着一个百分点以内的结论都要能追溯到数据量
        report["control_label"] = ctrl_label
        report["control_train_decisions"] = int(m_ctrl["train_decisions"])
        report["dagger_train_decisions"] = int(m_mix["train_decisions"])
        report["control_max_decisions"] = args.control_max_decisions
    if teacher_eval_files:                           # 老师状态上的同一把尺子 → 量化分布差距
        on_teacher = compare_arms(teacher_dir, "val", arms, labels, device, args.batch, pairs)
        print(fmt_cmp("【老师状态】与老师的一致率（无泄漏参照组）", on_teacher))
        report["on_teacher_states"] = on_teacher
    out = new_ckpt_dir / f"dagger-r{args.round}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_no_numpy),
                   encoding="utf-8")
    print(f"\nDAgger 报告：{out}")
    print("⚠ 这只是**离线**指标（对老师的一致率）。别据此宣称「变强了」—— "
          "强度只能由 `eval.py` 的实战顺位检验给出（TRAINING.md §7）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
