# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`fold` 轴：**只读已有轨迹**重跑判据侧 + `_verify_fold_totals` 对账（⛔ 不采集、不训练）。

用法：`python _fold-verify.py <tag> [<traces_root>]`
  * `<tag>` = 轨迹目录名（如 `v4-wl-fold-c1-g01`）；
  * `<traces_root>` 缺省 = `<w-ladder>/traces`（链内 g01）；给 `traces-post` 就量补采那一侧。
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
STAGE = Path(r"S:\mahjong-training\w-ladder")


def load_wl():
    spec = importlib.util.spec_from_file_location("wl", REPO / "tools" / "w-ladder.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wl"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else "v4-wl-fold-c1-g01"
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else STAGE / "traces"
    d = root / tag
    if not d.is_dir():
        print(f"⛔ 轨迹目录不存在：{d}")
        return 2
    wl = load_wl()
    n = len(list(d.glob("g*.jsonl")))
    print(f"== 判据侧重算 · {d}（{n} 个 g*.jsonl）==")
    t0 = time.time()
    per = wl._student_per_game(d)
    t1 = time.time()
    print(f"[耗时] `_student_per_game`（1000 场全量、含逐行重建現物）= {t1 - t0:.1f}s；"
          f"场数 {len(per)}")
    tot4 = wl._fold_totals4(per)
    print(f"[四席口径·另出的一份] {tot4}")
    g0 = json.loads((d / "g0.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    stu = next(p for p in g0["policies"] if "@" in p)
    _frozen = wl.read_audit(tag) or {}
    spec_text = _frozen.get("spec") or "fold=5400:no_deal"
    print(f"[基准] 用**当前奖励代码**在同一份轨迹上重算 `fold` 的账（spec={spec_text!r}）…")
    acc = wl._reward_fold_account(d, spec_text, stu)
    try:
        res = wl._verify_fold_totals(per, tag, audit=acc)
    except SystemExit as e:
        print(str(e))
        return 1
    tot = res["totals"]
    print(f"\n[✓ 对账通过] 判据侧（学生席，独立实现）== **当前奖励代码重算**的账：")
    for k in ("f_decision_rows", "f_threat_rows", "f_fold_rows", "f_push_rows", "f_missing_rows",
              "fold_paid_rows", "fold_blocked_rows"):
        print(f"   {k:<22} = {tot[k]}")
    print(f"   口径句 row_rule = {res['row_rule']}")
    print(f"   口径句 genbutsu_src = {res['genbutsu_src']}")
    print(f"   四席交叉核对 = {json.dumps(res['all_seats_check'], ensure_ascii=False)}")
    _fz = next((a for a in (_frozen.get("axes") or []) if a.get("axis") == "fold"), None)
    if _fz:
        print(f"\n[⚠ 冻结审计账] {wl.AUDIT / (tag + '.json')}（训练当时那一版代码的产物，"
              f"**不作为对账基准**）：")
        for mine, theirs in (("f_threat_rows", "threat_rows"), ("f_fold_rows", "fold_rows"),
                             ("f_push_rows", "push_rows"), ("fold_paid_rows", "paid_rows"),
                             ("fold_blocked_rows", "blocked_rows")):
            print(f"   {mine:<22} 判据侧 {tot[mine]:>7} vs 冻结账 {_fz.get(theirs):>7}"
                  f"（差 {int(_fz.get(theirs, 0)) - tot[mine]:+d}）")
    agg = wl._fold_aggregate(tot, len(per))
    print(f"\n[聚合读数 · 这一侧] {json.dumps(agg, ensure_ascii=False, indent=2)}")
    print(f"   门槛（付奖行率/学生席决策行 = {agg['paid_rows_frac']:.6%}）"
          f"⇒ {'过' if agg['paid_rows_frac'] >= 0.01 else '**不过**'} 1%/行")
    print(f"   降り率 = {agg['fold_rows']}/{agg['threat_rows']} = {agg['fold_rate']:.4%}")
    out = STAGE / "_fold-verify" / f"{tag}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"tag": tag, "traces": str(d), "files": n,
                               "seconds_student_per_game": t1 - t0,
                               "totals_student": tot, "totals_all_seats": tot4,
                               "all_seats_check": res["all_seats_check"],
                               "aggregate": agg, "verify": {k: res[k] for k in
                                                            ("tag", "audit", "spec", "axis",
                                                             "row_rule", "genbutsu_src",
                                                             "reconciled", "caliber")}},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[落盘] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
