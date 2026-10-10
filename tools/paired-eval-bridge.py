#!/usr/bin/env python
"""`paired-eval.mjs` 的 Python 桥：**只做一件事** —— 把 `tools/w-ladder.py` 里那份口径**原样借出来**。

为什么不在这里重写一遍配对/方向规则：逐场配对（`_paired_delta`）、"两条采集是不是同一副牌山"
（`_check_pairing`）、逐场整数账（`_student_per_game`）、伴随量的**方向表**（`COMPANION_BAD_WHEN` /
`PAIR_AXES`）全都已经有一份权威实现，而且 `AGENTS.md` §6.5 与 `python/selfcheck.py` 都在盯着它们。
⛔ 在 `.mjs` 里再抄一套分子/分母或"哪个量越大越好"，就是**第二套判据**：两份实现会漂移，
而漂移的那一天两份报告都还打得开。所以这里 `importlib` 加载**同一个文件**，一个数都不自己算。

用法（一般人不直接调它，`tools/paired-eval.mjs` 负责调）：
    <venv>python tools/paired-eval-bridge.py --synthetic          # 自检 + 负向自证（不出网、不读数据）
    <venv>python tools/paired-eval-bridge.py --base <dir> --cand <dir> [--metric NAME]
stdout 末行是 `__PAIRED_JSON__ {…}`（单行 JSON）。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MARK = "__PAIRED_JSON__"

# ⛔ 关掉字节码缓存：`importlib` 加载 `tools/w-ladder.py` 会在 `tools/` 下留一个 `__pycache__/`
#    —— 那是**产物**，会把 `git status` 弄脏（`all-checks.ps1` 的 git 行实测就是这么红的）。
sys.dont_write_bytecode = True


def load_wl():
    """按 `python/selfcheck.py` 的同一条路子加载 `tools/w-ladder.py`（同一份口径，⛔ 不复制）。"""
    spec = importlib.util.spec_from_file_location("_wl_paired", REPO / "tools" / "w-ladder.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def emit(payload: dict) -> None:
    print(MARK + " " + json.dumps(payload, ensure_ascii=False))


# ---------------------------------------------------------------- 自检（含负向自证）
def synthetic(wl) -> int:
    checks: list[dict] = []
    verdicts: dict[str, dict] = {}

    def ck(cond: bool, name: str, detail: str = "") -> None:
        checks.append({"ok": bool(cond), "name": name, "detail": detail})

    # ① 方向表必须覆盖 `PAIR_AXES` 的**每一个**量 —— 漏一个，那个轴在真数据上就会当场报错
    for ax in wl.PAIR_AXES:
        try:
            wl._companion_direction(ax[0])
            ck(True, f"方向表覆盖 {ax[0]}")
        except SystemExit as e:
            ck(False, f"方向表覆盖 {ax[0]}", str(e))

    # ② 四条**实测过**的方向判据（2026-10-10 / 2026-10-11 两次假警的回归；同一批也钉在 selfcheck.py）
    cases = [
        # (metric, delta, se, 期望 ok, 说明)
        ("deal_rate", -0.992, 0.4367, True, "放铳率下降 = 白赚（旧方向无关规则曾误报）"),
        ("deal_rate", +2.0, 0.4, False, "放铳率上升 = 代价，必须报"),
        ("dama_rate", +0.30, 1.32, True, "点估计为正 + 宽 CI（旧 CI 下界规则曾误报）"),
        ("win_rate", -2.0, 0.1, False, "和了率真退化必须报"),
    ]
    for metric, delta, se, want, why in cases:
        row = wl.companion_verdict(delta, se, 1000, metric=metric)
        verdicts[f"{metric}:{delta}:{se}"] = {k: row[k] for k in wl.COMPANION_KEYS} | {"bad_when": row["bad_when"]}
        ck(row["ok"] == want, f"{metric} Δ={delta} SE={se} ⇒ ok={want}",
           f"{why}；实际 ok={row['ok']} bad_when={row['bad_when']}")

    # ③ **负向自证**：没登记过的量必须**当场报错**，⛔ 不许给"缺省向下"的静默兜底
    try:
        wl.companion_verdict(0.0, 1.0, 10, metric="__no_such_metric__")
        ck(False, "未知量必须报错（负向）", "居然返回了结果 —— 说明有静默兜底")
    except SystemExit as e:
        ck(True, "未知量必须报错（负向）", str(e).splitlines()[0][:120])

    # ④ `_paired_delta` 走的是同一份 `PAIR_AXES`：拿一组能手算的数字对拍
    #    ⚠ 造数据**只能一次铺满所有键**：按轴循环赋值的写法会让后面的轴把前面轴用到的**同一个键**
    #    覆盖掉（`avg_win_score` 的分母就是 `wins`，实测把 win_rate 的分子改回了 1 ⇒ 这条自检
    #    第一版就是红的 —— 是自检抓出来的，不是手算抓出来的）。
    keys = {k for ax in wl.PAIR_AXES for k in (ax[1], ax[2])} - {"games"}
    pre, post = {}, {}
    for g in range(1, 11):
        pre[g] = {k: 1 for k in keys} | {"games": 1}
        pre[g]["wins"], pre[g]["seat_hands"] = 1, 10          # pre：和了 1/10 席
        post[g] = dict(pre[g])
        post[g]["wins"] = 2                                   # post：和了 2/10 席 ⇒ Δ=+10pp
    d = wl._paired_delta(pre, post)
    wr = d.get("win_rate")
    ck(wr is not None and abs(wr["delta_pp"] - 10.0) < 1e-9,
       "_paired_delta：win_rate 10%→20% ⇒ Δ=+10pp",
       f"实际 {wr and wr['delta_pp']}")
    ck(wr is not None and wr["n_games"] == 10, "_paired_delta：n_games = 10 场", f"实际 {wr and wr['n_games']}")
    zero = d.get("riichi_rate")
    ck(zero is not None and abs(zero["delta_pp"]) < 1e-9, "未改动的轴 Δ = 0", f"实际 {zero and zero['delta_pp']}")

    bad = [c for c in checks if not c["ok"]]
    emit({"mode": "synthetic", "pass": not bad, "checks": checks, "verdicts": verdicts,
          "n_checks": len(checks), "n_bad": len(bad),
          "pair_axes": [a[0] for a in wl.PAIR_AXES]})
    return 0 if not bad else 1


# ---------------------------------------------------------------- 真数据：两条采集的逐场配对
def analyze(wl, base: str, cand: str, metrics: list[str]) -> int:
    base_d, cand_d = Path(base), Path(cand)
    pairing = wl._check_pairing(base_d, cand_d)          # 破坏配对 ⇒ 这里直接 SystemExit（不出数）
    per_base = wl._student_per_game(base_d)
    per_cand = wl._student_per_game(cand_d)
    axes = [a for a in wl.PAIR_AXES if (not metrics or a[0] in metrics)]
    unknown = [m for m in metrics if m not in {a[0] for a in wl.PAIR_AXES}]
    if unknown:
        emit({"mode": "analyze", "error": f"⛔ 认不出的量：{unknown}（口径只在 w-ladder.py 的 PAIR_AXES 里有一份）"})
        return 1
    delta = wl._paired_delta(per_base, per_cand, axes=tuple(axes))
    rows = []
    for name, st in delta.items():
        row = wl.companion_verdict(st["delta_pp"], st["se_pp"], st["n_games"], metric=name)
        # 逐场 Δ（人读 md 里的附录；同一份 `PAIR_AXES` 的分子/分母，⛔ 不另立）
        ax = next(a for a in wl.PAIR_AXES if a[0] == name)
        per_game = []
        for g in sorted(set(per_base) & set(per_cand)):
            a, b = per_base[g], per_cand[g]
            if a[ax[2]] <= 0 or b[ax[2]] <= 0:
                continue
            scale = 100.0 if ax[3] == "pct" else 1.0
            per_game.append({"game": g, "delta": (b[ax[1]] / b[ax[2]] - a[ax[1]] / a[ax[2]]) * scale})
        rows.append({"metric": name, **{k: st[k] for k in ("unit", "unit_cn", "pre", "post", "delta_pp",
                                                           "se_pp", "t", "lo_pp", "hi_pp", "n_games")},
                     "bad_when": row["bad_when"], "bad_rule": row["bad_rule"], "ok": row["ok"],
                     "tol": row["tol"], "per_game": per_game})
    emit({"mode": "analyze", "pairing": pairing, "seed_base": pairing.get("seed_base_pre"),
          "base": str(base_d), "cand": str(cand_d), "rows": rows})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--base", default="")
    ap.add_argument("--cand", default="")
    ap.add_argument("--metric", default="", help="逗号分隔；缺省 = PAIR_AXES 全部")
    a = ap.parse_args()
    wl = load_wl()
    if a.synthetic:
        return synthetic(wl)
    if not a.base or not a.cand:
        emit({"error": "⛔ 要 `--synthetic`，或者同时给 `--base` 与 `--cand`"})
        return 2
    return analyze(wl, a.base, a.cand, [m for m in a.metric.split(",") if m])


if __name__ == "__main__":
    sys.exit(main())
