# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`pon` 轴（**副露种类 = 碰取向**）的**口径冒烟测试**（模板判据①，≤50 场、零新采集）。

要回答的事（⛔ 全部由数据答，"命令行一样"不算）：
  ① 奖励侧**记账路**（`style_reward.whitening_stats` → `meld_type_row_numbers`，学生席总体）
     的四格（鸣牌行 / 碰 / 吃 / 杠）+ `action_rows` / `paid_rows` / `blocked_rows` / `n_used`
     是否**逐键**等于**判据侧独立实现**（`tools/w-ladder.py` 的 `MELDTYPE_KEYS` 口径，这里另写一份）；
  ② 奖励侧**付钱路**（`BonusTracker.prebuild` → `_decision_rule` 的规则集 + 真正写进
     `style_bonus` 列的 `value()` 非零集合）是否与 ① 的**行集合**逐行相等（键 = `step`，
     ⛔ 不是行内下标 —— 上一轮 1578→150 的漏付就是两套坐标系混用）；
  ③ **方向单向**：`chi:` / `kan:` 的行**一行都不拿钱**（付奖集 ⊆ 碰的集合），
     且四格互不重叠（鸣牌行 == 碰 + 吃 + 杠）；
  ④ 门槛（模板判据②③）：**同口径同版代码**的两个率 —— 碰的行 / 鸣牌行（主判据的分母口径）
     与 碰的行 / 学生席决策行（"≥1%/行"的历史口径）；并给出**逐行 parity 读数** `k = std/w`
     （剂量 `w = δ/k`，δ = 5191.4 点 = meld 校准的纯结果奖励逐行 std）。

⛔ 判据侧**不 import** `style_reward`（两套实现各自算，才有对账价值）。

用法：`python _meldtype-smoke.py [N] [Tag] [TracesRoot]`（缺省 50 场 / `v4-wl-fold-c1-g01`）。
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
STAGE = Path(r"S:\mahjong-training\w-ladder")
WORK = STAGE / "_meldtype-smoke"
SPEC = "pon=5400:no_deal"
#: 纯结果奖励的**逐行** std（点）：meld 剂量校准实测（`PREREGISTRATION-DAMA.md` §2 /
#: `W-REPORT-MELD.md` §1 的 δ ∈ [5062.6, 5191.4]）—— 取上界，与 dose 反解同一份来源。
DELTA_PTS = 5191.4

sys.path.insert(0, str(REPO / "python"))
from mahjong_ml.v4 import style_reward as sr           # noqa: E402


def load_wl():
    spec = importlib.util.spec_from_file_location("wl", REPO / "tools" / "w-ladder.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wl"] = mod
    spec.loader.exec_module(mod)
    return mod


MELD_PREFIXES = ("chi:", "pon:", "kan:")      # ⛔ 判据侧自己写一份（不 import 奖励侧）
PAID_PREFIX = "pon:"


# ---------------------------------------------------------------------------
# 判据侧（独立实现；与 `tools/w-ladder.py` 的 `MELDTYPE_KEYS` 段同口径，这里另写一份比集合）
# ---------------------------------------------------------------------------
def judge(d: Path, n_games: int):
    """返回 `(五数+决策行, 碰的行集, 鸣牌行集)`；行集键 = `(game, hand, step)`。"""
    tot = {"decision_rows": 0, "meld_rows": 0, "pon_rows": 0, "chi_rows": 0, "kan_rows": 0}
    pon_set, meld_set = set(), set()
    for f in sorted(d.glob("g*.jsonl"), key=lambda x: int(x.name[1:-6]))[:n_games]:
        rows, labs = [], []
        for line in f.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("type") == "game":
                labs = list(r.get("policies") or [])
            elif r.get("type") == "decision":
                rows.append(r)
        stu = next((i for i, p in enumerate(labs) if "@" in p), None)
        for r in rows:
            seat = int(r.get("seat", -1))
            if not (0 <= seat <= 3) or seat != stu:
                continue
            tot["decision_rows"] += 1
            chosen = str(r.get("chosen") or "")
            pf = next((p for p in MELD_PREFIXES if chosen.startswith(p)), "")
            if not pf:
                continue
            tot["meld_rows"] += 1
            key = (int(r.get("game", -1)), int(r.get("hand_no", -1)), int(r.get("step", -1)))
            meld_set.add(key)
            if pf == PAID_PREFIX:
                tot["pon_rows"] += 1
                pon_set.add(key)
            elif pf == "chi:":
                tot["chi_rows"] += 1
            else:
                tot["kan_rows"] += 1
    return tot, pon_set, meld_set


# ---------------------------------------------------------------------------
# 奖励侧
# ---------------------------------------------------------------------------
def prep(tr: Path, n: int):
    """取前 N 场（**硬链接**到工作目录，失败退化为拷贝；⛔ 不动原轨迹）。"""
    files = sorted(tr.glob("g*.jsonl"), key=lambda x: int(x.name[1:-6]))[:n]
    if len(files) != n:
        raise SystemExit(f"⛔ 取不到 {n} 场：{tr}")
    d = WORK / "traces"
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        tgt = d / f.name
        if tgt.exists() and tgt.stat().st_size == f.stat().st_size:
            continue
        try:
            os.link(f, tgt)                       # 硬链接：不占空间、零拷贝（同一卷）
        except OSError:
            tgt.write_bytes(f.read_bytes())
    return d, files


def reward_accounting(d: Path, n: int, stu: str):
    """奖励侧**记账路**：`whitening_stats`（学生席总体）+ 逐行 `row_is_axis_action` 的行集合。"""
    files = sorted(d.glob("g*.jsonl"), key=lambda x: int(x.name[1:-6]))[:n]
    spec = sr.parse_bonus(SPEC)

    def hands():
        for f in files:
            yield from sr.iter_hands(f)

    stats, audit = sr.whitening_stats(spec, hands(), student_policies={stu}, quiet=True,
                                      mode="decision")
    ax = audit.axes[0]
    pon_set, meld_set, ord_mismatch = set(), set(), 0
    for f in files:
        for g, h, st in sr.iter_hands(f):
            for i, s, c in sr.rows_in_scope(st, {stu}):
                mt = st.row_meld_type[i] if 0 <= i < len(st.row_meld_type) else ""
                if not mt:
                    continue
                stp = st.row_step[i] if 0 <= i < len(st.row_step) else -1
                if stp < 0:
                    ord_mismatch += 1
                    continue
                key = (int(g), int(h), stp)
                meld_set.add(key)
                if sr.row_is_axis_action("pon", s, c, st, i):
                    pon_set.add(key)
    return spec, stats, ax, pon_set, meld_set, ord_mismatch


def reward_money(d: Path, n: int, stu: str, spec, stats):
    """奖励侧**付钱路**：`BonusTracker` 真正写进 `style_bonus` 列的集合（`value(row) != 0`）。

    ⚠ `rule_set` 必须**同时**满足两点，否则它根本不是"规则集"：① 这一行是**付奖那一侧**的动作
    （`chosen` 以 `pon:` 开头 —— ⛔ 不是"任何鸣牌行"）；② 这一行的行动者是**该场的学生席**
    （⛔ 不是别的座位）。上一版的判据只写了 ① 的一半 ⇒ 集合里混进 466 行别家/别的种类，
    看起来像"付钱路坏了"，其实是**判据自己写错**（这正是冒烟测试要抓的东西）。
    """
    tr = sr.BonusTracker(spec, stats, own_policies={stu}, mode="decision")
    files = sorted(d.glob("g*.jsonl"), key=lambda x: int(x.name[1:-6]))[:n]
    rule_set, paid_set, nonpon_paid, step_bad, n_rows = set(), set(), set(), 0, 0
    stu_by_game: dict = {}
    for f in files:
        tr.prebuild(f)
        for line in f.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("type") == "game":
                labs = list(r.get("policies") or [])
                stu_by_game[int(r.get("game", f.name[1:-6]))] = \
                    next((i for i, p in enumerate(labs) if "@" in p), None)
    for f in files:
        for line in f.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("type") != "decision":
                continue
            n_rows += 1
            k = (int(r.get("game", -1)), int(r.get("hand_no", -1)))
            stp = int(r.get("step", -1))
            if stp < 0:
                step_bad += 1
                continue
            chosen = str(r.get("chosen") or "")
            if chosen.startswith(PAID_PREFIX) and int(r.get("seat", -1)) == stu_by_game.get(k[0]):
                rule_set.add((k[0], k[1], stp))
            if tr.value(dict(r)) != 0.0:
                paid_set.add((k[0], k[1], stp))
                if not chosen.startswith(PAID_PREFIX):
                    nonpon_paid.add((k[0], k[1], stp, chosen))
    return rule_set, paid_set, nonpon_paid, step_bad, n_rows


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    tag = sys.argv[2] if len(sys.argv) > 2 else "v4-wl-fold-c1-g01"
    root = Path(sys.argv[3]) if len(sys.argv) > 3 else STAGE / "traces"
    src = root / tag
    d, files = prep(src, n)
    g0 = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
    stu = next(p for p in g0["policies"] if "@" in p)
    print(f"== pon 轴（副露种类 = 碰取向）口径冒烟 · {n} 场 · {files[0].name}"
          f"..{files[-1].name} · 学生串 {stu} ==")
    t0 = time.time()
    spec, stats, ax, r_pon, r_meld, no_step = reward_accounting(d, n, stu)
    t1 = time.time()
    j, j_pon, j_meld = judge(d, n)
    t2 = time.time()
    rule_set, paid_set, nonpon, step_bad, n_rows = reward_money(d, n, stu, spec, stats)
    t3 = time.time()
    print(f"[耗时] 奖励侧记账 {t1 - t0:.1f}s · 判据侧 {t2 - t1:.1f}s · 付钱路 {t3 - t2:.1f}s"
          f"（付钱路读了 {n_rows} 行）")

    print("\n① 逐键相等（奖励侧记账路 · 学生席 vs 判据侧独立实现）：")
    pairs = (("n_used / decision_rows", ax["n_used"], j["decision_rows"]),
             ("meld_rows", ax["meld_rows"], j["meld_rows"]),
             ("pon_rows", ax["pon_rows"], j["pon_rows"]),
             ("chi_rows", ax["chi_rows"], j["chi_rows"]),
             ("kan_rows", ax["kan_rows"], j["kan_rows"]),
             ("action_rows", ax["action_rows"], j["pon_rows"]),
             ("paid_rows", ax["paid_rows"], None),
             ("blocked_rows", ax["blocked_rows"], None))
    bad = 0
    for name, a, b in pairs:
        okk = (b is None) or (a == b)
        bad += 0 if okk else 1
        print(f"   {name:<30} 奖励 {a!r:>8} vs 判据 {b!r:>8} ⇒ {'**相等**' if okk else '**不等**'}")
    _mu_j = j["pon_rows"] / max(j["decision_rows"], 1)
    print(f"   mu（奖励侧实测）= {ax['mu']:.8f}；判据侧 pon/decision = {_mu_j:.8f} ⇒ "
          f"{'**相等**' if abs(ax['mu'] - _mu_j) < 1e-12 else '**不等**'}")
    print(f"   四格自洽 `meld == pon + chi + kan`：奖励 "
          f"{ax['pon_rows'] + ax['chi_rows'] + ax['kan_rows']} == {ax['meld_rows']} ⇒ "
          f"{'✓' if ax['pon_rows'] + ax['chi_rows'] + ax['kan_rows'] == ax['meld_rows'] else '✗'}")

    print("\n② 行集合逐行相等（记账路 / 付钱路 / 真正付出去的 `value()` / 判据侧）：")
    for name, s in (("奖励·记账路 pon 行集", r_pon), ("奖励·付钱路 rule 行集", rule_set),
                    ("奖励·真正付奖行集 value()", paid_set), ("判据侧 pon 行集", j_pon),
                    ("奖励·记账路 meld 行集", r_meld), ("判据侧 meld 行集", j_meld)):
        print(f"   {name:<28} {len(s)} 行")
    eq1 = r_pon == rule_set
    eq2 = (len(paid_set) == int(ax["paid_rows"])
           and len(rule_set) - len(paid_set) == int(ax["blocked_rows"])
           and paid_set <= rule_set)
    eq3 = r_pon == j_pon
    eq4 = r_meld == j_meld
    print(f"   记账路 == 付钱路（集合相等）⇒ {'**相等**' if eq1 else '**不等**'}"
          f"（对称差 {len(r_pon ^ rule_set)} 行）")
    print(f"   付钱路 **付奖 + 拦下 == 规则集**（{len(paid_set)} + {len(rule_set) - len(paid_set)}"
          f" == {len(rule_set)}；审计账 {ax['paid_rows']} + {ax['blocked_rows']}）⇒ "
          f"{'**相等**' if eq2 else '**不等**'}")
    print(f"   奖励侧 == 判据侧（pon 集合）⇒ {'**相等**' if eq3 else '**不等**'}"
          f"（对称差 {len(r_pon ^ j_pon)} 行）")
    print(f"   奖励侧 == 判据侧（meld 集合）⇒ {'**相等**' if eq4 else '**不等**'}"
          f"（对称差 {len(r_meld ^ j_meld)} 行）")
    print(f"   缺 `step` 的行数 = {step_bad}（奖励侧记账路 {no_step}）—— 键 = **`step`**（每场计数器）")

    print("\n③ 方向单向（⛔ 吃/杠那一侧一分钱不付）：")
    print(f"   真正拿到钱的行数 {len(paid_set)}；其中**不是** `pon:` 的行 = {len(nonpon)}"
          f" ⇒ {'✓ 单向' if not nonpon else '✗ 有非碰行拿钱：' + str(sorted(nonpon)[:5])}")
    print(f"   吃/杠的行数（判据侧）= {j['chi_rows'] + j['kan_rows']}（⛔ 全部 0 元）；"
          f"它们与付奖集交集 = {len(j_pon & j_pon) if False else len((j_meld - j_pon) & paid_set)}")

    print("\n④ 门槛（同口径、同版代码）+ 剂量读数：")
    rate_meld = j["pon_rows"] / max(j["meld_rows"], 1)
    rate_dec = j["pon_rows"] / max(j["decision_rows"], 1)
    print(f"   主判据口径：碰的行 / 鸣牌行 = {j['pon_rows']}/{j['meld_rows']} = {rate_meld:.4%}"
          f" ⇒ {'**过**' if rate_meld >= 0.01 else '**不过**'} 1%/行")
    print(f"   历史口径：碰的行 / 学生席决策行 = {j['pon_rows']}/{j['decision_rows']}"
          f" = {rate_dec:.4%} ⇒ {'**过**' if rate_dec >= 0.01 else '**不过**'} 1%/行")
    _pay = sr.spec_axis_pay(spec, stats, spec.axes[0])
    k = (1.0 - ax["mu"]) * (_pay / _pay)                # = 1 − μ（解析式）
    _std_col = _pay * ax["sd"]                          # 逐行塑形列的 std（= w·(1−μ)）
    print(f"   逐行 parity 读数：μ={ax['mu']:.6f} σ={ax['sd']:.6f} ⇒ 动作行拿到的钱 "
          f"{_pay:+.1f} 点；逐行塑形列 std = {_std_col:,.1f} 点"
          f"（= w·(1−μ)；与解析式一致：{abs(_std_col - 5400.0 * k) < 1e-6}）⇒ k = std/w = {k:.6f}")
    print(f"   ⇒ 1:1 行级同权（δ={DELTA_PTS} 点）所需 w* = δ/k ≈ **{DELTA_PTS / k:.0f}**；"
          f"本轮预注册剂量 w = 5400（= 本族标准档；与 w* 的偏差 {100 * (5400 / (DELTA_PTS / k) - 1):+.2f}%）")
    print(f"   门槛/主判据同口径：分母 = 鸣牌行（{j['meld_rows']} 行）⇒ 用同一个集合判门槛与主读数")

    out = {"tag": tag, "games": n, "spec": SPEC, "student": stu,
           "judge": j, "reward_audit": {kk: ax[kk] for kk in
                                        ("n_used", "meld_rows", "pon_rows", "chi_rows", "kan_rows",
                                         "action_rows", "paid_rows", "blocked_rows", "mu", "sd")},
           "sets": {"reward_pon": len(r_pon), "reward_rule": len(rule_set),
                    "reward_paid": len(paid_set), "judge_pon": len(j_pon),
                    "reward_meld": len(r_meld), "judge_meld": len(j_meld)},
           "set_equal": {"accounting_vs_rule": eq1, "rule_vs_paid": eq2, "reward_vs_judge": eq3,
                         "meld_equal": eq4},
           "symmetric_diff": {"accounting_vs_rule": len(r_pon ^ rule_set),
                              "reward_vs_judge": len(r_pon ^ j_pon)},
           "step_mismatch": step_bad, "no_step_accounting": no_step,
           "one_way_nonpon_paid": len(nonpon),
           "rate_pon_over_meld": rate_meld, "rate_pon_over_decision": rate_dec,
           "dose_k_row": k, "w_star_row_parity": DELTA_PTS / k,
           "pass_threshold_1pct": (rate_meld >= 0.01) and (rate_dec >= 0.01),
           "key_equal": bad == 0}
    WORK.mkdir(parents=True, exist_ok=True)
    p = WORK / f"smoke-{n}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[落盘] {p}")
    ok = (bad == 0) and eq1 and eq2 and eq3 and eq4 and step_bad == 0 and not nonpon
    print(f"[判定] 口径冒烟 {'**PASS**' if ok else '**FAIL**'}（逐键相等 + 行集合相等 + 四格自洽 + 单向）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
