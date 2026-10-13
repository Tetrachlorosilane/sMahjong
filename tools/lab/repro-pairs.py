#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""repro-pairs.py —— **复现轮**的逐代差值账（`decision@6170` 三条链）。

为什么需要它（而不是只跑 `w-ladder.py post`）：
  · `post` 只算**末代 vs g01** 那一对（它以 g01 的 `seed_base` 补采 ⇒ **真配对**）；
  · 本脚本把**链内每一代 vs g01** 也算出来，但**必须如实标成"不配对"** —— 因为链内每一代的
    采集 seed 是**不同的**（`loop.py`：采集 seed = `cfg.seed + 绝对代号`；实测 c2 = 24310072/73/74/75）
    ⇒ 逐场 seed 一个都对不上（`_check_pairing` 报 `seed_mismatch=1000`）。这不是 bug，是设计：
    「同一副牌山、只换权重」只有 `post` 那条路才有。

所以这里的口径写死两条：
  ① **能配对就配对**（`_check_pairing` 通过 ⇒ 用 `w-ladder._paired_delta`），绝不硬凑；
  ② 配不上就用**逐场聚类的两样本 SE**（`_unpaired_delta`：`sqrt(var_a/n_a + var_b/n_b)`，
     方差取**每场的比率** —— 同一场里 11 个小局是相关的，按小局算 SE 会假窄），
     并在结果里写 `paired=False` 与原因，⛔ 表里也照标，不许混着看。

纪律（与 `w-ladder.py post` 逐条相同，缺一不出数）：
  · 逐场整数账的**合计必须与 `style-vector.py` 的 `counts` 逐项相等**（`_verify_pair_totals`）；
  · 逐代一行一个 JSON，落 `paired-repro/<label>-gNN.json`。

用法：python repro-pairs.py --labels a,b,c [--gens 1,2,3,4]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
S = Path(r"S:\mahjong-training")
STAGE = S / "w-ladder"
TRACES = STAGE / "traces"
STYLE_VEC = STAGE / "style-vector-per-gen"
OUT = STAGE / "paired-repro"

sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "python" / ".venv" / "Lib" / "site-packages"))

#: ⛔ 文件名带连字符 ⇒ 不能 `import`，只能按路径加载（**同一份实现**，不抄第二套）。
_spec = importlib.util.spec_from_file_location("wladder", REPO / "tools" / "w-ladder.py")
wl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wl)

Z95 = wl.Z95


def _unpaired_delta(a: dict, b: dict, axes=wl.PAIR_AXES) -> dict:
    """两个**独立**样本（不同 seed 族）的差值 —— ⛔ 不是配对。

    `a` = g01 的逐场账、`b` = 这一代的逐场账（`{game: {计数键…}}`）。
    SE 按**场**聚类：先算每场的比率，再 `sqrt(var_a/n_a + var_b/n_b)` —— 同一场里的 11 个小局
    是相关的（同一副牌山、同一批对手），拿小局当独立样本会把 CI 算窄。
    ⚠ 场数不同（两代采集的场数都是 1000，但有效小局数不同）⇒ 两边的 n 各自算。
    """
    out = {}
    for name, num, den, unit in axes:
        ra, rb = [], []
        for per, dst in ((a, ra), (b, rb)):
            for g in sorted(per):
                c = per[g]
                if c[den] > 0:
                    dst.append(c[num] / c[den])
        if len(ra) < 2 or len(rb) < 2:
            continue
        ma, mb = statistics.fmean(ra), statistics.fmean(rb)
        se = (statistics.variance(ra) / len(ra) + statistics.variance(rb) / len(rb)) ** 0.5
        scale = 100.0 if unit == "pct" else 1.0
        d = mb - ma
        out[name] = {"unit": unit, "unit_cn": "百分点 pp" if unit == "pct" else "点/和了",
                     "pre": ma, "post": mb, "delta_pp": d * scale, "se_pp": se * scale,
                     "t": (d / se if se > 0 else float("nan")),
                     "lo_pp": (d - Z95 * se) * scale, "hi_pp": (d + Z95 * se) * scale,
                     "n_games": min(len(ra), len(rb)), "paired": False,
                     "n_pre": len(ra), "n_post": len(rb)}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="repro-pairs.py")
    ap.add_argument("--labels", required=True, help="逗号分隔的链标签")
    ap.add_argument("--gens", default="", help="只算这些代（缺省全部 gNN）")
    a = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    only = {int(x) for x in a.gens.split(",") if x.strip()} if a.gens else None
    for label in [x.strip() for x in a.labels.split(",") if x.strip()]:
        gens = wl._chain_tags(label)
        if not gens:
            raise SystemExit(f"⛔ 找不到 {TRACES / (label + '-g01')}")
        pre_dir = TRACES / f"{label}-g01"
        sv_pre = json.loads((STYLE_VEC / f"{label}-g01.json").read_text(encoding="utf-8"))
        per_pre = wl._student_per_game(pre_dir)
        tot_pre = wl._verify_pair_totals(per_pre, sv_pre, f"{label}-g01")
        print(f"[{label}] g01 整数账对账 ✓ {tot_pre}", flush=True)
        for g, tdir in gens:
            if only is not None and g not in only:
                continue
            tag = tdir.name
            sv = json.loads((STYLE_VEC / f"{tag}.json").read_text(encoding="utf-8"))
            per = wl._student_per_game(tdir)
            tot = wl._verify_pair_totals(per, sv, tag)
            if g == 1:
                pair = {"games": len(per_pre), "seed_mismatch": 0, "student_seat_mismatch": 0,
                        "ok": True, "note": "自己配自己（零点）"}
                delta = _unpaired_delta(per_pre, per_pre)
            else:
                # ① 先试真配对（只有 `post` 那条路能过）；过不了就**如实**落不配对。
                raw = {"pre": str(pre_dir), "post": str(tdir), "games": 0,
                       "seed_mismatch": 0, "student_seat_mismatch": 0, "ok": False}
                try:
                    pair = wl._check_pairing(pre_dir, tdir)
                except SystemExit:
                    pair = {"pre": str(pre_dir), "post": str(tdir),
                            "seed_mismatch": None, "student_seat_mismatch": None, "ok": False}
                    pair.update(_pair_stats(pre_dir, tdir))
                delta = (wl._paired_delta(per_pre, per)
                         if pair.get("ok") else _unpaired_delta(per_pre, per))
            res = {"tool": "repro-pairs.py", "label": label, "tag": tag, "generation": g,
                   "phase": wl.phase_of(g), "pre_tag": f"{label}-g01",
                   "pairing_check": pair, "totals": tot, "paired_delta": delta,
                   "discipline": ("⛔ 链内每代的采集 seed 不同（`loop.py`：cfg.seed + 绝对代号）⇒ "
                                  "配不上就不许说配对；只有 `post`（抄 g01 的 seed_base）是真配对。")}
            (OUT / f"{tag}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
            rd = res["paired_delta"].get("riichi_rate") or {}
            kind = "配对" if rd.get("paired", True) and pair.get("ok") else "**不配对**"
            print(f"[{label}] {tag}（{wl.phase_of(g)}，{kind}）立直率 "
                  f"{100 * rd.get('pre', float('nan')):.3f}% → {100 * rd.get('post', float('nan')):.3f}%"
                  f" ＝ Δ {rd.get('delta_pp', float('nan')):+.2f}pp "
                  f"SE {rd.get('se_pp', float('nan')):.2f} "
                  f"CI [{rd.get('lo_pp', float('nan')):+.2f},{rd.get('hi_pp', float('nan')):+.2f}]"
                  f"｜seed 不一致 {pair.get('seed_mismatch')}/{pair.get('games')}，"
                  f"学生席不一致 {pair.get('student_seat_mismatch')}", flush=True)
    print(f"[repro-pairs] 落盘 {OUT}")
    return 0


def _pair_stats(pre_dir: Path, post_dir: Path) -> dict:
    """配对失败的**证据**（逐场 seed / 学生席对上了几个）—— 报数用，不做判据。"""
    def load(d):
        s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        return {int(g["game"]): (int(g["seed"]), next(
            (i for i, p in enumerate(g["policies"]) if "@" in p), None)) for g in s["per_game"]}
    ga, gb = load(pre_dir), load(post_dir)
    common = sorted(set(ga) & set(gb))
    return {"games": len(common),
            "seed_mismatch": sum(1 for k in common if ga[k][0] != gb[k][0]),
            "student_seat_mismatch": sum(1 for k in common if ga[k][1] != gb[k][1]),
            "seed_base_pre": json.loads((pre_dir / "summary.json").read_text(encoding="utf-8")).get("seed_base"),
            "seed_base_post": json.loads((post_dir / "summary.json").read_text(encoding="utf-8")).get("seed_base")}


if __name__ == "__main__":
    raise SystemExit(main())
