#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""w-ladder-gate-table.py —— 把**臂对臂**闸门（`tools/w-ladder.py gate`）的读数排成一张表。

数据来源：
  * `S:\\mahjong-training\\w-ladder\\gate-arm-vs-arm.json`（`gate.py` 的判决原件：合并 Δ/CI/护栏）
  * `S:\\mahjong-training\\w-ladder\\logs\\gate-<tag>.log`（`v4.gate` 的**原样**输出：逐牌山那几行）
  * `S:\\mahjong-training\\gate\\<tag>\\s<seed>\\summary.json`（**逐场**读数；轨迹已被轮换删掉，
    但 `per_game[].rank_points / deals / wins / win_points` 都在 ⇒ 表里的"逐套主口径"是
    **从 summary 独立重算**的，⛔ 不是抄 gate 日志）

⛔ 本脚本**不做判决**：判决是 `mahjong_ml/v4/gate.py` 给的（本脚本把它抄下来）。
   重算出来的逐套/合并值只用于**对账**（`--cross-check` 会把两者并列，不一致就标红）。
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

S = Path(r"S:\mahjong-training")
STAGE = S / "w-ladder"

#: 与 `mahjong_ml/v4/gate.py::_KEY_STRIDE` 同一个偏移（跨牌山合并时给每套一段互不重叠的场次号）。
KEY_STRIDE = 1_000_000


def mean_se(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    if n < 2:
        return (float("nan"), float("nan"))
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return (m, math.sqrt(var / n))


def ci(xs: list[float], z: float = 1.96) -> tuple[float, float, float, int]:
    """正态近似 CI（与 `eval._paired_from_diffs` 同一族做法：一样本 t → 这里用 z≈1.96）。"""
    m, se = mean_se(xs)
    return (m, m - z * se, m + z * se, len(xs))


def paired_rows(dirpath: Path, cand_chain: str | None = None) -> tuple[str, str]:
    """返回 `(候选策略串, 现任策略串)`（闸门里座位是 `a,a,b,b` ⇒ 各占 2 席）。

    ⚠ 别用"标签里含不含 `v4-wl-`"来认人：那样本工具就**只能**读本轮的产物（自检/复用都废了）。
    判据改成"策略串的**父目录名** == 那一条链的 label"（`candidate_chain` 由调用方给）。
    """
    s = json.loads((dirpath / "summary.json").read_text(encoding="utf-8"))
    labs = list((s.get("by_policy") or {}).keys())
    if len(labs) != 2:
        raise SystemExit(f"⛔ {dirpath} 里的策略不是 2 个：{labs}")
    if cand_chain:
        cand = [x for x in labs if Path(x.replace("net:", "")).parent.name == cand_chain]
        if len(cand) != 1:
            raise SystemExit(f"⛔ {dirpath} 里找不到候选链 {cand_chain}：{labs}")
        return cand[0], next(x for x in labs if x != cand[0])
    return labs[-1], labs[0]


def per_game(dirpath: Path, cand_chain: str | None = None) -> dict:
    """逐场读数：`game → {"deals": Δ(候选−现任), "rank_points": Δ, "wins": Δ, "win_points": Δ}`。

    ⚠ 每场每个策略坐 **2 席**（`a,a,b,b`）⇒ 取两席平均（与 `eval.seat_values` 同一口径）。
    主口径 `deals` 在评估侧**取负**（放铳越少越好）—— 这里按 `-候选 + 现任` 算，与判决同号。
    """
    s = json.loads((dirpath / "summary.json").read_text(encoding="utf-8"))
    cand, inc = paired_rows(dirpath, cand_chain)
    out: dict = {}
    for row in s.get("per_game") or []:
        pols = row.get("policies") or []
        ci_ = [i for i, p in enumerate(pols) if p == cand]
        ii_ = [i for i, p in enumerate(pols) if p == inc]
        if not ci_ or not ii_:
            continue
        # ⚠ 键必须是 `seed`（**不是** `game`）：`eval.per_game_series` 用的就是 `g["seed"]`，
        #   而配对检验按这个键求交集 ⇒ 表里的数与判决同源；用 `game` 只是本目录内的等价编号。
        key = int(row.get("seed", row.get("game", -1)))
        def avg(vals, idx):
            return sum(float(vals[i]) for i in idx) / len(idx)
        out[key] = {
            # 主口径（风格轴 deals）：**取负**后正 = 候选更好（与 gate.py 的 `--line def` 同号）
            "deals": -avg(row["deals"], ci_) + avg(row["deals"], ii_),
            "rank_points": avg(row["rank_points"], ci_) - avg(row["rank_points"], ii_),
            "wins": avg(row["wins"], ci_) - avg(row["wins"], ii_),
            "win_points": avg(row["win_points"], ci_) - avg(row["win_points"], ii_),
        }
    return out


def pooled(series: list[dict], key: str) -> list[float]:
    out: list[float] = []
    for i, m in enumerate(series):
        out += [v[key] for _, v in sorted(m.items())]
    return out


def policy_stats(dirpath: Path, cand_chain: str | None = None) -> dict:
    """该套牌山上两个策略的**率**读数（直接读 summary 的 by_policy，与 summary 同一本账）。"""
    s = json.loads((dirpath / "summary.json").read_text(encoding="utf-8"))
    cand, inc = paired_rows(dirpath, cand_chain)
    b = s["by_policy"]
    return {"cand": {"label": cand, **b[cand]}, "inc": {"label": inc, **b[inc]}}


def parse_log(log: Path) -> dict:
    """从 `v4.gate` 的原样输出里取逐牌山 / 合并那几行（护栏逐套只有这里有）。"""
    out: dict = {"walls": []}
    if not log.is_file():
        return out
    cur = None
    for ln in log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\s*牌山 (\d+)：Δ=\s*([+-][\d.]+) CI\[\s*([+-][\d.]+),\s*([+-][\d.]+)\] "
                     r"n=(\d+)（候选 − 现任）(?: · 护栏 rank_points Δ=([+-][\d.]+))?", ln)
        if m:
            cur = {"seed": int(m.group(1)), "delta": float(m.group(2)),
                   "lo": float(m.group(3)), "hi": float(m.group(4)), "n": int(m.group(5)),
                   "guard_delta": float(m.group(6)) if m.group(6) else None}
            out["walls"].append(cur)
            continue
        m2 = re.search(r"护栏（rank_points）：Δ=([+-][\d.]+) CI\[([+-][\d.]+),([+-][\d.]+)\]", ln)
        if m2:
            out.update({"guard_delta": float(m2.group(1)), "guard_lo": float(m2.group(2)),
                        "guard_hi": float(m2.group(3))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="w-ladder-gate-table.py")
    ap.add_argument("--json", default=str(STAGE / "gate-arm-vs-arm-table.json"))
    ap.add_argument("--md", default=str(STAGE / "gate-arm-vs-arm-table.md"))
    ap.add_argument("--src", default=str(STAGE / "gate-arm-vs-arm.json"),
                    help="`w-ladder.py gate` 落的那份判决原件（缺省 w-ladder/gate-arm-vs-arm.json）")
    ap.add_argument("--gate-root", default=str(S / "gate"),
                    help="闸门产物根（缺省 S:\\mahjong-training\\gate）—— 只为**自检**留的口子")
    args = ap.parse_args()

    src = Path(args.src)
    gate_root = Path(args.gate_root)
    if not src.is_file():
        raise SystemExit(f"⛔ 先跑 `python tools/w-ladder.py gate`（缺 {src}）")
    pairs = json.loads(src.read_text(encoding="utf-8"))

    payload = []
    md = ["# 臂对臂闸门读数（`w=2800` 链 vs `w=0` 链）",
          "",
          "⛔ 判决是 `mahjong_ml/v4/gate.py` 给的（下表 `verdict.json` 列）；"
          "「重算」列是本脚本**从 `summary.json` 独立重算**的对账值，两者应当一致。",
          "",
          "| 组 | 候选链（w=2800） | 现任链（w=0） | 牌山 | 主口径 deals Δ（候选−现任） | 护栏 rank_points Δ | n | 重算 deals Δ | 重算 护栏 Δ |",
          "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for k, e in enumerate(pairs, start=1):
        tag, inc, cand = e["tag"], e["incumbent_chain"], e["candidate_chain"]
        v = e["verdict"]
        log = parse_log(Path(e["log"]))
        walls = [int(x) for x in (e.get("walls") or v.get("seeds") or [])]
        series, pstats, rows = [], [], []
        for s_ in walls:
            d = gate_root / tag / f"s{s_}"
            if not (d / "summary.json").is_file():
                rows.append({"seed": s_, "missing": True})
                continue
            m = per_game(d, cand)
            series.append(m)
            st = policy_stats(d, cand)
            lw = next((w for w in log["walls"] if w["seed"] == s_), {})
            got = ci([x["deals"] for x in m.values()])
            got_g = ci([x["rank_points"] for x in m.values()])
            rec = {"seed": s_, "games": len(m),
                   "deals": {"delta": got[0], "lo": got[1], "hi": got[2], "n": got[3]},
                   "rank_points": {"delta": got_g[0], "lo": got_g[1], "hi": got_g[2], "n": got_g[3]},
                   # `gate.py` 自己打的那两个数（用于对账；护栏逐套只有日志里有）
                   "log_deals_delta": lw.get("delta"), "log_guard_delta": lw.get("guard_delta"),
                   "policies": st}
            rows.append(rec)
            md.append("| {k} | {cand} | {inc} | {seed} | {d:+.3f} [{lo:+.3f},{hi:+.3f}] "
                      "| {g} | n={n} | {rd:+.3f} [{rlo:+.3f},{rhi:+.3f}] | {rg:+.3f} [{rglo:+.3f},{rghi:+.3f}] |".format(
                          k=k, cand=cand, inc=inc, seed=s_,
                          d=(lw.get("delta") if lw.get("delta") is not None else float("nan")),
                          lo=(lw.get("lo") if lw.get("lo") is not None else float("nan")),
                          hi=(lw.get("hi") if lw.get("hi") is not None else float("nan")),
                          g=("%+.2f" % lw["guard_delta"]) if lw.get("guard_delta") is not None else "n/a",
                          n=got[3], rd=got[0], rlo=got[1], rhi=got[2],
                          rg=got_g[0], rglo=got_g[1], rghi=got_g[2]))
        # 合并（本脚本重算；用于与 verdict 对账）
        dpool = ci(pooled(series, "deals")) if series else (float("nan"),) * 4
        gpool = ci(pooled(series, "rank_points")) if series else (float("nan"),) * 4
        ok_d = (abs(dpool[0] - float(v["delta"])) < 0.02) if series else None
        ok_g = (abs(gpool[0] - float(v.get("guard_delta", float("nan")))) < 0.05) if series else None
        md.append("| **{k} 合并** | {cand} | {inc} | {nw} 套 × {g} | **{d:+.3f}** [{lo:+.3f},{hi:+.3f}] "
                  "| **{gd:+.2f}** [{glo:+.2f},{ghi:+.2f}] | n={n} | {rd:+.3f} [{rlo:+.3f},{rhi:+.3f}] "
                  "| {rg:+.3f} [{rglo:+.3f},{rghi:+.3f}] |".format(
                      k=k, cand=cand, inc=inc, nw=len(walls), g=e.get("games"),
                      d=v["delta"], lo=v["lo"], hi=v["hi"],
                      gd=float(v.get("guard_delta", float("nan"))),
                      glo=float(v.get("guard_lo", float("nan"))),
                      ghi=float(v.get("guard_hi", float("nan"))), n=v["n"],
                      rd=dpool[0], rlo=dpool[1], rhi=dpool[2], rg=gpool[0], rglo=gpool[1], rghi=gpool[2]))
        md.append(f"| {k} 判决 | | | | adopt={v.get('adopt')} primary={v.get('adopt_primary')} "
                  f"guard_ok={v.get('guard_ok')} | 套间极差 {v.get('wall_spread'):.3f} "
                  f"/ CI 半宽 {(v['hi'] - v['lo']) / 2:.3f} = {v.get('wall_spread_over_ci'):.2f}× "
                  f"| | | |")
        payload.append({"tag": tag, "candidate_chain": cand, "incumbent_chain": inc,
                        "walls": rows, "verdict": v, "log": log,
                        "recomputed_pooled": {"deals": dpool, "rank_points": gpool},
                        "cross_check": {"deals_delta_ok": ok_d, "guard_delta_ok": ok_g}})

    # 四组一起看**符号一致性**（预注册 §4 ②）
    deltas = [float(e["verdict"]["delta"]) for e in pairs]
    signs = [d > 0 for d in deltas]
    md += ["", "## 四组一起看",
           "",
           f"* 主口径 `deals` Δ 逐组： " + "、".join(f"{d:+.3f}" for d in deltas),
           f"* 符号一致性：**{sum(signs)}/{len(signs)}** 为正"
           + ("（4/4 才算稳，预注册 §4 ②）" if len(signs) == 4 else ""),
           f"* 护栏 `rank_points` CI 下界逐组： " + "、".join(
               f"{float(e['verdict'].get('guard_lo', float('nan'))):+.2f}" for e in pairs)
           + f"（预注册：≥ −1.0；破了的组数 = "
             f"{sum(1 for e in pairs if not e['verdict'].get('guard_ok'))}）"]
    text = "\n".join(md) + "\n"
    print(text)
    Path(args.md).write_text(text, encoding="utf-8")
    Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[落盘] {args.md}\n[落盘] {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
