# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""`dama` 轴的**独立全量复核**（硬判据 ② 的第 1 条对账）：从轨迹**重数**一遍"能立而不立"。

⛔ 这份脚本**不 import** `mahjong_ml.v4.style_reward`（判据独立实现是本仓的既定分工）：
它自己按 PROTOCOL §8.3/§8.4 的字段重数一遍，再与
  ① 奖励侧的审计账（`w-ladder/audit/<tag>.json` 的 `action_rows/paid_rows/blocked_rows/legal_rows`）、
  ② 判据侧的 `tools/style-vector.py` 的 `counts.dama_wins`（+`wins`）
对账。三个来源**各不相同**，对不上就报错。

口径（与 `style_reward` 逐条对应）：
  * 范围 = **学生席**（`policy` 里带 `@` 的那一席，与 `style-vector.py`/`w-ladder.py` 同一判据）；
  * `legal_rows` = 该席决策行里 `legal` 含 `riichi:*` 的行数；
  * `declined_rows` = 其中 `chosen` 不以 `riichi:` 开头的行（= **付奖行候选**）；
  * `paid_rows` / `blocked_rows` = 付奖行里"该小局学生未放铳" / "学生放铳"的行数；
  * `dama_wins` = 学生和了且该小局该席**未立直**的小局数（= `style-vector.py` 同名口径）；
  * 结构检查 ① 付奖行那一刻 `obs.riichi[seat]`/`riichi_turn[seat]` 必须还是 0；
  * 结构检查 ② 付奖行必须在该席第一次 `chosen=riichi:*` **之前**。

用法：
    python _dama-reconcile.py <轨迹目录> [--audit <审计账.json>] [--style-vector <sv.json>]
                              [--files N] [--json out.json]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

RIICHI = "riichi:"
MELDS = ("chi:", "pon:", "kan:")
S = 4


def scan_file(path: str) -> dict:
    dec = []          # (hand_no, seat, chosen, has_riichi_legal, obs_riichi, obs_rturn)
    hands = []        # (hand_no, agari, winner, loser)
    labs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            r = json.loads(line)
            t = r.get("type")
            if t == "decision":
                obs = r.get("obs") or {}
                seat = int(r.get("seat", -1))
                legal = r.get("legal")
                if legal is None:
                    legal = obs.get("legal")
                has = None if legal is None else any(str(k).startswith(RIICHI) for k in legal)
                ri = obs.get("riichi") or []
                rt = obs.get("riichi_turn") or []
                dec.append((int(r.get("hand_no", -1)), seat, str(r.get("chosen") or ""), has,
                            bool(ri[seat]) if 0 <= seat < len(ri) else False,
                            int(rt[seat]) if 0 <= seat < len(rt) else 0,
                            str(r.get("policy") or ""),
                            [bool(x) for x in (obs.get("melds") or [])]))
            elif t == "hand":
                hands.append((int(r.get("hand_no", -1)), bool(r.get("agari")),
                              int(r.get("winner", -1)), int(r.get("loser", -1))))
            elif t == "game":
                labs = [str(x) for x in (r.get("policies") or [])]
    stu = next((i for i, p in enumerate(labs) if "@" in p), None)
    out = Counter()
    out["files"] = 1
    if stu is None:
        out["files_no_student"] = 1
        return dict(out)
    # 手级：立直 / 副露（四席，与 style-vector 同一把尺子：三个来源取或）
    riichi_flag, meld_flag = {}, {}
    for hn, seat, chosen, _has, ori, ort, pol, melds in dec:
        if ori or ort > 0 or chosen.startswith(RIICHI):
            riichi_flag[(hn, seat)] = True
        if chosen.startswith(MELDS):
            meld_flag[(hn, seat)] = True
        for s in range(min(S, len(melds))):
            if melds[s]:
                meld_flag[(hn, s)] = True
    # 结构检查：该席第一次 `chosen=riichi:*` 的行号（同一 (小局, 席) 内按行序）
    first_decl = {}
    for i, (hn, seat, chosen, *_rest) in enumerate(dec):
        if seat == stu and chosen.startswith(RIICHI) and (hn, seat) not in first_decl:
            first_decl[(hn, seat)] = i
    declined_by_hand = Counter()
    loser_of_hand = {hn: lo for hn, _ag, _w, lo in hands}
    for i, (hn, seat, chosen, has, ori, ort, pol, _m) in enumerate(dec):
        if seat != stu:
            continue
        out["rows_in_scope"] += 1
        if has is None:
            out["missing_legal_rows"] += 1
            continue
        if not has:
            continue
        out["legal_rows"] += 1
        if chosen.startswith(RIICHI):
            out["legal_chosen_rows"] += 1
            continue
        out["declined_rows"] += 1
        declined_by_hand[hn] += 1
        if ori or ort > 0:
            out["viol_state_says_riichi"] += 1
        fd = first_decl.get((hn, seat))
        if fd is not None and i >= fd:
            out["viol_after_declaration"] += 1
        if loser_of_hand.get(hn, -1) == stu:
            out["blocked_rows"] += 1
        else:
            out["paid_rows"] += 1
    for hn, agari, winner, loser in hands:
        out["hands"] += 1
        out["seat_hands"] += 1
        if winner == stu and agari:
            out["wins"] += 1
            if riichi_flag.get((hn, stu)):
                out["riichi_wins"] += 1
            else:
                out["dama_wins"] += 1
                if declined_by_hand.get(hn):
                    out["dama_win_with_decline_row"] += 1
                else:
                    out["dama_win_no_decline_row"] += 1
                    if meld_flag.get((hn, stu)):
                        out["dama_win_no_decline_open"] += 1
                    else:
                        out["dama_win_no_decline_closed"] += 1
        if loser == stu:
            out["deals"] += 1
    out["decline_hands"] = len(declined_by_hand)
    return dict(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--audit", default="")
    ap.add_argument("--style-vector", default="")
    ap.add_argument("--files", type=int, default=0)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    d = Path(a.dir)
    files = sorted((f for f in d.iterdir() if re.fullmatch(r"g\d+\.jsonl", f.name)),
                   key=lambda f: int(f.name[1:-6]))
    if a.files:
        files = files[:a.files]
    if not files:
        raise SystemExit(f"⛔ 目录里没有 g*.jsonl：{d}")
    with Pool(a.workers) as pool:
        parts = pool.map(scan_file, [str(f) for f in files], chunksize=4)
    tot = Counter()
    for p in parts:
        tot.update(p)
    n = tot["rows_in_scope"]
    print(f"== `dama` 独立复核（{len(files)} 个轨迹文件，{d}）==")
    print(f"  范围内（学生席）决策行        {n}")
    print(f"  ① `legal` 里有 `riichi:*` 的行  {tot['legal_rows']}"
          f"（其中**真的选了**它 {tot['legal_chosen_rows']}）")
    print(f"  ② 其中**没选**它（付奖行候选）  {tot['declined_rows']}")
    print(f"  ③ **真付奖**（该小局学生未放铳）  {tot['paid_rows']}")
    print(f"  ④ 被伴随量 `no_deal` 拦下       {tot['blocked_rows']}"
          f"（⇒ ③+④ == {tot['paid_rows'] + tot['blocked_rows']} "
          f"{'✓' if tot['paid_rows'] + tot['blocked_rows'] == tot['declined_rows'] else '✗'}）")
    print(f"  `legal` 取不到的行             {tot['missing_legal_rows']}")
    print(f"  结构检查：付奖行却已立直 {tot['viol_state_says_riichi']} / 宣言之后 {tot['viol_after_declaration']}"
          f"（都必须 = 0）")
    print(f"  手级：wins={tot['wins']} riichi_wins={tot['riichi_wins']} **dama_wins={tot['dama_wins']}**"
          f" deals={tot['deals']} seat_hands={tot['seat_hands']}")
    print(f"  未立直而和了的小局里：有'能立而不立'行 {tot['dama_win_with_decline_row']}"
          f" / 没有 {tot['dama_win_no_decline_row']}"
          f"（其中副露手 {tot['dama_win_no_decline_open']}，门前 {tot['dama_win_no_decline_closed']}）")
    rows = tot["declined_rows"]
    mu = (rows / n) if n else float("nan")
    paid = tot["paid_rows"]
    q = (paid / n) if n else float("nan")
    if n and 0 < mu < 1 and 0 < q < 1:
        k = ((1 - mu) / (mu * (1 - mu)) ** 0.5) * (q * (1 - q)) ** 0.5
        print(f"  剂量换算：μ_行={mu:.6f} σ_行={(mu * (1 - mu)) ** 0.5:.6f} q_付奖={q:.6f}"
              f" ⇒ **k = std(塑形列)/w = {k:.4f}**")
        for delta in (5062.6, 5191.4):
            print(f"     若 δ（结果奖励逐行 std）≈ {delta:.0f} ⇒ 逐行同权 w* = δ/k ≈ **{delta / k:.0f}**")
    res = {"dir": str(d), "files": len(files), "totals": dict(tot), "mu_row": mu, "q_paid": q}
    if a.audit:
        au = json.loads(Path(a.audit).read_text(encoding="utf-8"))
        ax = au["axes"][0]
        pairs = [("declined_rows", "action_rows"), ("paid_rows", "paid_rows"),
                 ("blocked_rows", "blocked_rows"), ("legal_rows", "legal_rows"),
                 ("legal_chosen_rows", "legal_chosen_rows"),
                 ("missing_legal_rows", "missing_legal_rows")]
        print(f"  ── 与审计账对账（{Path(a.audit).name}，spec={au['spec']}）")
        ok_all = True
        for mine, theirs in pairs:
            bad = tot[mine] != ax.get(theirs)
            ok_all &= not bad
            print(f"     {mine:<20} 复核 {tot[mine]:>7} vs 审计 {ax.get(theirs):>7} "
                  f"{'✓' if not bad else '✗ **不一致**'}")
        bad_mu = abs(ax["mu"] - mu) > 1e-12
        ok_all &= not bad_mu
        print(f"     μ_行                复核 {mu:.10f} vs 审计 {ax['mu']:.10f} "
              f"{'✓' if not bad_mu else '✗'}")
        res["audit_check_ok"] = ok_all
    if a.style_vector:
        sv = json.loads(Path(a.style_vector).read_text(encoding="utf-8"))
        st = next((v for k, v in (sv.get("policies") or {}).items() if "@" in k), None)
        if st:
            c = st["counts"]
            print(f"  ── 与判据侧对账（{Path(a.style_vector).name}）")
            for mine, theirs in (("dama_wins", "dama_wins"), ("wins", "wins"),
                                 ("riichi_wins", "riichi_wins"), ("seat_hands", "seat_hands")):
                bad = tot[mine] != c.get(theirs)
                print(f"     {mine:<20} 复核 {tot[mine]:>7} vs style-vector {c.get(theirs):>7} "
                      f"{'✓' if not bad else '✗ **不一致**'}")
                if bad:
                    res["audit_check_ok"] = False
            res["dama_rate_check"] = {
                "mine": (tot["dama_wins"] / tot["wins"]) if tot["wins"] else None,
                "style_vector": st["dims"].get("dama_rate")}
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  落盘 {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
