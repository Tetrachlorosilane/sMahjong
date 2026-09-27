"""**标签侧文件**（`g*.aux.npz`）的读侧与硬闸门 —— 训练专用，推理路径永不读它。

## 为什么单独一层

`docs/FEATURES-V4.md` §5.2 的硬闸门是**物理分离**：

| 文件 | 谁写 | 谁读 | 内容 |
| --- | --- | --- | --- |
| `g*.jsonl` | `--selfplay` | 所有人 | 决策行（obs + 动作） |
| `g*.feat.bin` | `--features` | **推理/训练** | 输入侧派生量（危险度/安全度…） |
| `g*.aux.npz` | `--selfplay --aux` | **只训练** | **隐藏真值**：对手手牌/听牌、放铳、和了、顺位 |

"标签与输入分开两套文件 + 两套列名"不是洁癖：混在一起时，一次不小心的 `concat` 就会让
hidden truth 进到输入里，而且训练照样跑、指标照样出。所以这里也**只管读标签**，
并且把"版本 / 行数 / 与轨迹对齐"三条校验做成**硬拒**（不猜、不填 0）。

## 命令行

    python -m mahjong_ml.auxlabels check <轨迹目录>     # 逐文件核对：n / 版本 / 与 jsonl 对齐
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path

import numpy as np

#: 标签侧文件格式版本（= Java `TraceRecorder.AUX_VERSION`；字段增删要 +1 并同步两边）
AUX_VERSION = 1

#: 标签列（名 → dtype）。`opp_hand` 是**隐藏真值**（对手暗牌计数）。
AUX_SHAPES: dict[str, tuple] = {
    "own_shanten_after": (),          # 这一手做完之后的向听（听牌记 0）
    "own_tenpai": (),                 # 这一手做完之后是否听牌
    "opp_tenpai": (3,),               # 三家对手当前是否听牌（相对方位 0=下家）
    "opp_hand": (3, 34),              # 三家对手的暗牌计数（上帝视角）
    "opp_dealin": (3,),               # 本小局我有没有放铳给第 j 家（事后回填）
    "win_flag": (),                   # 本小局我有没有和了（事后回填）
    "hand_delta": (),                 # 本小局我的收支（事后回填）
    "placement": (),                  # 终局顺位 1..4（整场结束回填）
}

#: 写进**紧凑集**的标签列（名 → dtype）：`hand_delta` / `placement` 不重复落
#: —— 它们已经能从轨迹派生的 `delta`（四家收支）+ `seat`、以及 `placement` 列算出来。
AUX_COMPACT_COLUMNS: dict[str, type] = {
    "aux_own_shanten_after": np.int8,
    "aux_own_tenpai": np.uint8,
    "aux_opp_tenpai": np.uint8,
    "aux_opp_hand": np.uint8,
    "aux_opp_dealin": np.uint8,
    "aux_win_flag": np.uint8,
}


def aux_path(jsonl: str | Path) -> Path:
    """`g0.jsonl` → `g0.aux.npz`（同目录、同名前缀）。"""
    p = Path(jsonl)
    return p.parent / (p.name.split(".")[0] + ".aux.npz")


def compact_key(compact_name: str) -> str:
    """紧凑集列名 → 标签列名（`aux_opp_hand` → `opp_hand`）。"""
    return compact_name[len("aux_"):] if compact_name.startswith("aux_") else compact_name


def load_aux(path: str | Path) -> dict:
    """读一份标签侧 npz（**硬校验**：版本、成员齐、形状自洽）。

    返回 `{列名: ndarray}` + `meta`（dict）+ `n`。⚠ `allow_pickle=False`：
    这些标签都是纯数值数组，**不该**需要 pickle（读到 pickle 就说明文件来路不对）。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"缺标签侧文件：{p.name}（采集时要加 `--aux`）")
    with np.load(p, allow_pickle=False) as z:
        names = set(z.files)
        missing = [k for k in AUX_SHAPES if k not in names]
        if missing:
            raise ValueError(f"{p.name} 缺标签列 {missing}（重跑 --selfplay --aux）")
        out = {k: z[k] for k in AUX_SHAPES}
        meta_raw = z["meta"].tobytes() if "meta" in names else b"{}"
    meta = json.loads(meta_raw.decode("utf-8"))
    ver = int(meta.get("aux_version", -1))
    if ver != AUX_VERSION:
        raise ValueError(f"{p.name} 标签格式版本 {ver} != Python 侧 {AUX_VERSION}"
                         f" —— 重新采集（两边一起改并 +版本）")
    ns = {k: int(v.shape[0]) for k, v in out.items()}
    if len(set(ns.values())) != 1:
        raise ValueError(f"{p.name} 各列行数不一致：{ns}")
    for k, tail in AUX_SHAPES.items():
        if tuple(out[k].shape[1:]) != tail:
            raise ValueError(f"{p.name} 列 {k} 形状 {out[k].shape} != (n, {tail})")
    out["n"] = ns["own_tenpai"]
    out["meta"] = meta
    return out


def check_dir(directory: str | Path) -> tuple[int, list[str]]:
    """核对一个轨迹目录里的**所有** `g*.aux.npz`。

    三条硬判据：① 每个有 `g*.jsonl` 的文件都要有对应的 `.aux.npz`（缺 = 报错）；
    ② `n` == 该文件的**决策行数**（错位等于配错答案）；③ 标签里记的 `obs_version`
    必须与轨迹里 obs 的版本一致（防"新轨迹配旧标签"）。

    返回 `(通过的文件数, 问题清单)`。
    """
    d = Path(directory)
    if not d.is_dir():
        raise SystemExit(f"目录不存在：{d}")
    jsonls = sorted(p for p in d.iterdir() if p.is_file() and p.name.startswith("g")
                    and p.suffix == ".jsonl")
    if not jsonls:
        raise SystemExit(f"目录里没有 g*.jsonl：{d}")
    problems: list[str] = []
    okfiles = 0
    for jl in jsonls:
        ap = aux_path(jl)
        if not ap.is_file():
            problems.append(f"{jl.name}: 缺 {ap.name}（采集时要加 --aux）")
            continue
        decisions = 0
        obs_v = None
        with jl.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("type") != "decision":
                    continue
                decisions += 1
                if obs_v is None:
                    obs_v = int((row.get("obs") or {}).get("v", 0))
        try:
            ax = load_aux(ap)
        except (ValueError, FileNotFoundError) as e:
            problems.append(f"{jl.name}: {e}")
            continue
        if int(ax["n"]) != decisions:
            problems.append(f"{jl.name}: 标签 {ax['n']} 行 != 决策 {decisions} 行（错位）")
            continue
        if obs_v is not None and int(ax["meta"].get("obs_version", -1)) != obs_v:
            problems.append(f"{jl.name}: 标签记的 obs v{ax['meta'].get('obs_version')} "
                            f"与轨迹的 v{obs_v} 不一致（新旧混用）")
            continue
        okfiles += 1
    return okfiles, problems


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "check":
        print("用法：python -m mahjong_ml.auxlabels check <轨迹目录>", file=sys.stderr)
        return 2
    okfiles, problems = check_dir(argv[1])
    for p in problems:
        print(f"  [FAIL] {p}")
    if problems:
        print(f"[aux-check] FAIL：{len(problems)} 处问题（通过 {okfiles} 个文件）")
        return 1
    print(f"[aux-check] PASS：{okfiles} 个标签侧文件的版本/行数/obs 版本都与轨迹对齐")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
