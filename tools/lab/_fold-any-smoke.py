# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`fold` 轴**新口径（对至少一家威胁家是現物）**的**口径冒烟测试**（模板判据①，≤50 场、零新采集）。

要回答的事（⛔ 全部由数据答，"命令行一样"不算）：
  ① 奖励侧**记账路**（`style_reward.whitening_stats` → `fold_row_numbers`，学生席总体）
     的五个数 + `mu` + `n_used`/付奖/拦下 是否**逐键**等于**判据侧独立实现**；
  ② 奖励侧**付钱路**（`BonusTracker.prebuild` → `_decision_rule` 的 `fold_rows` 行号集合，
     = 真正写进 `style_bonus` 列的那个集合）是否与 ①/② 的**行集合**逐行相等；
     ⚠ 这一条是本轮的关键：`_row_obs` 曾经不带 `seat` ⇒ 付钱路**偷偷**按"含行动者自己"的
     字面口径算（上一轮链实际付的就是它）⇒ 必须由**行集合相等**钉住，而不是"看起来一样"；
  ③ 威胁行/降り行在**三格互不重叠**下与判据侧一致，且 `threat == fold + push + missing`；
  ④ 门槛（模板判据②③）：**同口径同版代码**的付奖行率 = `fold_rows / 学生席决策行` ≥ 1%/行。

⛔ 判据侧**不 import** `style_reward`（两套实现各自算，才有对账价值）。

用法：`python _fold-any-smoke.py [N] [Tag] [TracesRoot]`（缺省 50 场 / `v4-wl-fold-c1-g01`）。
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
STAGE = Path(r"S:\mahjong-training\w-ladder")
WORK = STAGE / "_fold-any-smoke"
SPEC = "fold=5400:no_deal"

sys.path.insert(0, str(REPO / "python"))
from mahjong_ml.v4 import style_reward as sr           # noqa: E402


def load_wl():
    spec = importlib.util.spec_from_file_location("wl", REPO / "tools" / "w-ladder.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wl"] = mod
    spec.loader.exec_module(mod)
    return mod


def norm(t):
    t = str(t or "")
    return ("5" + t[-1]) if t and t[:-1] == "0" else t


# ---------------------------------------------------------------------------
# 判据侧（独立实现；与 `tools/w-ladder.py` 的 `FOLD_KEYS` 段同口径，这里另写一份以便逐行比集合）
# ---------------------------------------------------------------------------
def judge(d: Path, n_games: int):
    """返回 `(四数+决策行, 行集合)`；行集合 = {(game, hand, 行内序号)}。"""
    tot = {"decision_rows": 0, "threat_rows": 0, "fold_rows": 0, "push_rows": 0, "missing_rows": 0}
    fold_set, threat_set = set(), set()
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
        for ord_i, r in enumerate(rows):
            seat = int(r.get("seat", -1))
            if not (0 <= seat <= 3) or seat != stu:
                continue
            tot["decision_rows"] += 1
            chosen = str(r.get("chosen") or "")
            if not chosen.startswith("discard:"):
                continue
            obs = r.get("obs") or {}
            ri = obs.get("riichi")
            if not isinstance(ri, (list, tuple)):
                tot["missing_rows"] += 1
                continue
            # ★ seat 口径写死：只数**别家**（行动者自己立直不算威胁 —— 立直后只能摸切、没有选择权）
            others = [s for s in range(min(4, len(ri))) if bool(ri[s]) and s != seat]
            if not others:
                continue
            events = obs.get("events") or []
            ri_idx = {}
            for i, e in enumerate(events):
                a = e.get("actor")
                if isinstance(a, int) and e.get("type") == "riichi" and a not in ri_idx:
                    ri_idx[a] = i
            seen = {a: set() for a in ri_idx}
            decl = {}
            for i, e in enumerate(events):
                a = e.get("actor")
                if not isinstance(a, int) or e.get("type") != "discard":
                    continue
                if a in ri_idx and i >= ri_idx[a]:
                    k = norm(e.get("tile"))
                    if k:
                        seen[a].add(k)
                if e.get("sideways") and a not in decl:
                    decl[a] = norm(e.get("tile"))
            if any(s not in seen for s in others):
                tot["missing_rows"] += 1
                continue
            tot["threat_rows"] += 1
            key = (int(r.get("game", -1)), int(r.get("hand_no", -1)), int(r.get("step", -1)))
            threat_set.add(key)
            tk = norm(chosen.split(":", 1)[1])
            # ★★ **新口径**：对**至少一家**威胁家是現物就算降り（旧口径 = `all` = 对每一家）
            safe = bool(tk) and any((tk in seen[s]) or (s in decl and tk == decl[s]) for s in others)
            if safe:
                tot["fold_rows"] += 1
                fold_set.add(key)
            else:
                tot["push_rows"] += 1
    return tot, fold_set, threat_set


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
    fold_set, threat_set, ord_mismatch = set(), set(), 0
    for f in files:
        for g, h, st in sr.iter_hands(f):
            rows = sr.rows_in_scope(st, {stu})
            for i, s, c in rows:
                # ⚠⚠ 键 = **`step`**（每一场的决策行计数器），⛔ 不是行内下标 `i`：
                #   付钱路（`_decision_value`）手上只有 `row["step"]` ⇒ 两套坐标系必须在这里对齐。
                stp = st.row_step[i] if 0 <= i < len(st.row_step) else -1
                if stp < 0:
                    ord_mismatch += 1
                    continue
                key = (int(g), int(h), stp)
                if st.row_threat_missing[i]:
                    continue
                if st.row_threat[i]:
                    threat_set.add(key)
                if sr.row_is_axis_action("fold", s, c, st, i):
                    fold_set.add(key)
    return spec, stats, ax, fold_set, threat_set, ord_mismatch


def reward_money(d: Path, n: int, stu: str, spec, stats):
    """奖励侧**付钱路**：`BonusTracker` 的 `dcache[...][3]`（= `_decision_rule` 的 `fold_rows`）
    + 逐行 `value(row)` 的非零集合（⛔ 这才是真正写进 `style_bonus` 列的东西）。"""
    tr = sr.BonusTracker(spec, stats, own_policies={stu}, mode="decision")
    files = sorted(d.glob("g*.jsonl"), key=lambda x: int(x.name[1:-6]))[:n]
    rule_set, paid_set, step_bad, n_rows = set(), set(), 0, 0
    for f in files:
        tr.prebuild(f)
    for f in files:
        rows = []
        for line in f.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("type") == "decision":
                rows.append(r)
        for r in rows:
            n_rows += 1
            k = (int(r.get("game", -1)), int(r.get("hand_no", -1)))
            stp = int(r.get("step", -1))
            if stp < 0:
                step_bad += 1
                continue
            ent = tr.dcache.get(k)
            # ⚠ `ent[3]` 装的是**这一小局**付奖行的 `step` 集合（与这里的 `stp` 同坐标系）
            if ent and stp in ent[3]:
                rule_set.add((k[0], k[1], stp))
            if tr.value(dict(r)) != 0.0:
                paid_set.add((k[0], k[1], stp))
    return rule_set, paid_set, step_bad, n_rows


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    tag = sys.argv[2] if len(sys.argv) > 2 else "v4-wl-fold-c1-g01"
    root = Path(sys.argv[3]) if len(sys.argv) > 3 else STAGE / "traces"
    src = root / tag
    d, files = prep(src, n)
    g0 = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
    stu = next(p for p in g0["policies"] if "@" in p)
    print(f"== fold 新口径（对**至少一家**威胁家現物）口径冒烟 · {n} 场 · {files[0].name}"
          f"..{files[-1].name} · 学生串 {stu} ==")
    t0 = time.time()
    spec, stats, ax, r_fold, r_threat, no_step = reward_accounting(d, n, stu)
    t1 = time.time()
    j, j_fold, j_threat = judge(d, n)
    t2 = time.time()
    rule_set, paid_set, step_bad, n_rows = reward_money(d, n, stu, spec, stats)
    t3 = time.time()
    print(f"[耗时] 奖励侧记账 {t1 - t0:.1f}s · 判据侧 {t2 - t1:.1f}s · 付钱路 {t3 - t2:.1f}s"
          f"（付钱路读了 {n_rows} 行）")

    print("\n① 逐键相等（奖励侧记账路 · 学生席 vs 判据侧独立实现）：")
    pairs = (("n_used / decision_rows", ax["n_used"], j["decision_rows"]),
             ("threat_rows", ax["threat_rows"], j["threat_rows"]),
             ("fold_rows", ax["fold_rows"], j["fold_rows"]),
             ("push_rows", ax["push_rows"], j["push_rows"]),
             ("missing_genbutsu_rows", ax["missing_genbutsu_rows"], j["missing_rows"]),
             ("action_rows", ax["action_rows"], j["fold_rows"]),
             ("paid_rows", ax["paid_rows"], None),
             ("blocked_rows", ax["blocked_rows"], None))
    bad = 0
    for name, a, b in pairs:
        okk = (b is None) or (a == b)
        bad += 0 if okk else 1
        print(f"   {name:<30} 奖励 {a!r:>8} vs 判据 {b!r:>8} ⇒ {'**相等**' if okk else '**不等**'}")
    print(f"   mu（奖励侧实测）= {ax['mu']:.8f}；判据侧 fold/decision = "
          f"{j['fold_rows'] / max(j['decision_rows'], 1):.8f} ⇒ "
          f"{'**相等**' if abs(ax['mu'] - j['fold_rows'] / max(j['decision_rows'], 1)) < 1e-12 else '**不等**'}")
    print(f"   四数自洽 `threat == fold + push + missing`：奖励 "
          f"{ax['fold_rows'] + ax['push_rows'] + ax['missing_genbutsu_rows']} == {ax['threat_rows']} ⇒ "
          f"{'✓' if ax['fold_rows'] + ax['push_rows'] + ax['missing_genbutsu_rows'] == ax['threat_rows'] else '✗'}")

    print("\n② 行集合逐行相等（三个来源：记账路 / 付钱路 `_decision_rule` / 真正付出去的 `value()`）：")
    sets = (("奖励·记账路 fold 行集", r_fold), ("奖励·付钱路 rule 行集", rule_set),
            ("奖励·真正付奖行集 value()", paid_set), ("判据侧 fold 行集", j_fold))
    for name, s in sets:
        print(f"   {name:<28} {len(s)} 行")
    eq1 = r_fold == rule_set
    # ⚠ `value()` 还多一道**伴随质量轴**（`no_deal`）：付奖集 = 规则集里的"该小局学生未放铳"那一部分，
    #   其余被拦下 ⇒ 判据是 `付奖 + 拦下 == 规则集`（与审计账的 `paid_rows`/`blocked_rows` 同口径），
    #   ⛔ 不是"付奖集 == 规则集"（那会把质量轴当成不存在）。
    eq2 = (len(paid_set) == int(ax["paid_rows"])
           and len(rule_set) - len(paid_set) == int(ax["blocked_rows"])
           and paid_set <= rule_set)
    eq3 = r_fold == j_fold
    print(f"   记账路 == 付钱路（集合相等）⇒ {'**相等**' if eq1 else '**不等**'}"
          f"（对称差 {len(r_fold ^ rule_set)} 行）")
    print(f"   付钱路 **付奖 + 拦下 == 规则集**（{len(paid_set)} + {len(rule_set) - len(paid_set)}"
          f" == {len(rule_set)}；审计账 {ax['paid_rows']} + {ax['blocked_rows']}）⇒ "
          f"{'**相等**' if eq2 else '**不等**'}")
    print(f"   奖励侧 == 判据侧（集合相等）⇒ {'**相等**' if eq3 else '**不等**'}"
          f"（对称差 {len(r_fold ^ j_fold)} 行）")
    print(f"   威胁行集合：奖励 {len(r_threat)} vs 判据 {len(j_threat)} ⇒ "
          f"{'**相等**' if r_threat == j_threat else '**不等**'}（对称差 {len(r_threat ^ j_threat)}）")
    print(f"   缺 `step` 的行数 = {step_bad}（奖励侧记账路 {no_step}）—— 键 = **`step`**（每场计数器），"
          f"⛔ 不是行内下标（两套坐标系混用 = 上一轮 1578→150 那个漏付）")

    print("\n③ seat 口径写死（只数**别家**）的证据 —— 同一份 50 场：")
    lit = oth = dec = 0
    for f in files:
        for line in f.open(encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("type") != "decision":
                continue
            obs = r.get("obs") or {}
            ri = obs.get("riichi") or []
            seat = int(r.get("seat", -1))
            if not str(r.get("chosen") or "").startswith("discard:") \
                    or not isinstance(ri, (list, tuple)) or not (0 <= seat <= 3):
                continue
            dec += 1
            _any = any(bool(ri[s]) for s in range(min(4, len(ri))))
            _oth = any(bool(ri[s]) for s in range(min(4, len(ri))) if s != seat)
            lit += 1 if _any else 0
            oth += 1 if _oth else 0
    print(f"   出牌行 {dec}；**别家**口径的威胁行 {oth}；**字面**（含行动者自己）口径 {lit}"
          f"（多 {lit - oth} 行 ⇒ 与上一轮冻结账的 +4892 同类偏差）")
    print(f"   奖励侧记账路的威胁行（学生席）= {j['threat_rows']}（⛔ 必须 ≤ 四席的 {oth}）")

    print("\n④ 门槛（同口径、同版代码）：")
    rate = j["fold_rows"] / j["decision_rows"]
    print(f"   付奖行率 = fold_rows / 学生席决策行 = {j['fold_rows']}/{j['decision_rows']}"
          f" = {rate:.4%} ⇒ {'**过**' if rate >= 0.01 else '**不过**'} 1%/行")
    print(f"   降り率（付奖行 / 被威胁行）= {j['fold_rows']}/{j['threat_rows']} = "
          f"{j['fold_rows'] / max(j['threat_rows'], 1):.4%}")

    out = {"tag": tag, "games": n, "spec": SPEC, "student": stu,
           "judge": j, "reward_audit": {k: ax[k] for k in
                                        ("n_used", "threat_rows", "fold_rows", "push_rows",
                                         "missing_genbutsu_rows", "action_rows", "paid_rows",
                                         "blocked_rows", "mu", "sd")},
           "sets": {"reward_fold": len(r_fold), "reward_rule": len(rule_set),
                    "reward_paid": len(paid_set), "judge_fold": len(j_fold),
                    "reward_threat": len(r_threat), "judge_threat": len(j_threat)},
           "set_equal": {"accounting_vs_rule": eq1, "rule_vs_paid": eq2, "reward_vs_judge": eq3,
                         "threat_equal": r_threat == j_threat},
           "symmetric_diff": {"accounting_vs_rule": len(r_fold ^ rule_set),
                              "reward_vs_judge": len(r_fold ^ j_fold)},
           "step_mismatch": step_bad, "no_step_accounting": no_step,
           "self_riichi_only_discard_rows": lit,
           "paid_rows_frac": rate,
           "pass_threshold_1pct": rate >= 0.01,
           "key_equal": bad == 0}
    WORK.mkdir(parents=True, exist_ok=True)
    p = WORK / f"smoke-{n}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[落盘] {p}")
    ok = (bad == 0) and eq1 and eq2 and eq3 and (r_threat == j_threat) and step_bad == 0
    print(f"[判定] 口径冒烟 {'**PASS**' if ok else '**FAIL**'}（逐键相等 + 行集合相等 + 三格自洽）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
