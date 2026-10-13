# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""`riichi_turn` 轴（**早立直**）的**独立全量复核**（硬判据 ② 的第 1 条对账 + 剂量反解）。

⛔ 这份脚本**不 import** `mahjong_ml.v4.style_reward`（判据独立实现是本仓的既定分工）：
它自己按 PROTOCOL §8.3/§8.4 的字段重数一遍，再与
  ① 奖励侧审计账（`w-ladder/audit/<tag>.json` 的 `decl_rows` / `decl_turn_rows` / `action_rows` /
     `decl_late_rows` / `missing_turn_rows` / `paid_rows` / `blocked_rows` / `turn_limit`）、
  ② 判据侧 `tools/style-vector.py` 的 `counts`（`riichi_turn_sum` / `riichi_turn_known` /
     `riichi_turn_missing` / `riichi_hands` / `seat_hands`）对账。三个来源**各不相同**，对不上就报错。

口径（与 `style_reward.ROW_ACTION_TURN` 逐条对应，T = 8）：
  * 范围 = **学生席**（`policy` 里带 `@` 的那一席，与 `style-vector.py`/`w-ladder.py` 同一判据）；
  * `decl_rows` = 该席决策行里 `chosen` 以 `riichi:` 开头的行数（= 宣言立直的那一行）；
  * `decl_turn_le_rows` = 其中**本行 `obs.player_draws` ≤ T** 的（= 付奖行候选 = 奖励侧"动作行"）；
  * `decl_late_rows` = 其中 > T 的（⛔ 不付奖的一侧，必须真的存在）；`missing_turn_rows` = 取不到巡目的；
  * `paid_rows` / `blocked_rows` = 付奖行里"该小局学生未放铳" / "学生放铳"的行数；
  * 结构检查：宣言行那一刻 `obs.riichi[seat]` 与 `obs.riichi_turn[seat]` **必须还是 0/False**
    （服务端不会给已经立直的人再下发立直 ⇒ 不成立就是轨迹/判据坏了）；
  * 手级：`riichi_turn_sum/known/missing` 与 `style-vector.py` 的**回退口径**逐字相同
    （`obs.riichi_turn[seat]` 为 0 时用宣言那一行的 `player_draws`；两者都取不到 ⇒ missing）。

用法：
    python _rt-reconcile.py <轨迹目录> [--audit <审计账.json>] [--style-vector <sv.json>]
                            [--files N] [--json out.json]
"""
import argparse
import json
import re
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

RIICHI = "riichi:"
S = 4
T = 8          # ← 阈值 T 的**第三份独立副本**（判据侧 / 奖励侧 / 本脚本），三方由审计账的 turn_limit 对齐


def scan_file(path: str) -> dict:
    dec = []          # (行号, 小局, 席, chosen, 本行 player_draws, obs.riichi[seat], obs.riichi_turn[seat])
    hands = []
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
                pd = obs.get("player_draws")
                ri = obs.get("riichi") or []
                rt = obs.get("riichi_turn") or []
                dec.append((len(dec), int(r.get("hand_no", -1)), seat, str(r.get("chosen") or ""),
                            None if not isinstance(pd, int) else int(pd),
                            bool(ri[seat]) if 0 <= seat < len(ri) else False,
                            int(rt[seat]) if 0 <= seat < len(rt) else 0))
            elif t == "hand":
                hands.append((int(r.get("hand_no", -1)), bool(r.get("agari")),
                              int(r.get("winner", -1)), int(r.get("loser", -1))))
            elif t == "game":
                labs = [str(x) for x in (r.get("policies") or [])]
    out = Counter()
    out["files"] = 1
    stu = next((i for i, p in enumerate(labs) if "@" in p), None)
    if stu is None:
        out["files_no_student"] = 1
        return dict(out)
    loser_of_hand = {hn: lo for hn, _ag, _w, lo in hands}
    # 手级：立直巡目（与 `style-vector.py` 的回退口径逐字相同）
    rturn_max, decl_draw, riichi_flag = {}, {}, {}
    for _i, hn, seat, chosen, pd, ori, ort in dec:
        if ori or ort > 0 or chosen.startswith(RIICHI):
            riichi_flag[(seat, hn)] = True
        if ort > 0:
            rturn_max[(seat, hn)] = max(rturn_max.get((seat, hn), 0), ort)
        if chosen.startswith(RIICHI):
            decl_draw[(seat, hn)] = pd or 0
    for hn, _ag, _w, _lo in hands:
        out["seat_hands"] += 1
        if riichi_flag.get((stu, hn)):
            out["riichi_hands"] += 1
            rt = rturn_max.get((stu, hn), 0) or decl_draw.get((stu, hn), 0)
            if rt > 0:
                out["riichi_turn_sum"] += rt
                out["riichi_turn_known"] += 1
            else:
                out["riichi_turn_missing"] += 1
    # 行级（只数学生席）
    for _i, hn, seat, chosen, pd, ori, ort in dec:
        if seat != stu:
            continue
        out["rows_in_scope"] += 1
        if not chosen.startswith(RIICHI):
            continue
        out["decl_rows"] += 1
        if ori or ort > 0:
            out["viol_state_says_riichi"] += 1
        if pd is None:
            out["missing_turn_rows"] += 1
            continue
        if pd > T:
            out["decl_late_rows"] += 1
            continue
        out["decl_turn_le_rows"] += 1
        if loser_of_hand.get(hn, -1) == stu:
            out["blocked_rows"] += 1
        else:
            out["paid_rows"] += 1
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
    res = {"dir": str(d), "files": len(files), "T": T, "totals": dict(tot), "ok": True}
    print(f"== `riichi_turn`（早立直，T={T}）独立复核（{len(files)} 个轨迹文件，{d}）==")
    print(f"  范围内（学生席）决策行        {tot['rows_in_scope']}")
    print(f"  宣言立直的行                 {tot['decl_rows']}"
          f"（巡目 ≤ T 的 {tot['decl_turn_le_rows']}｜> T 的 {tot['decl_late_rows']}"
          f"｜取不到巡目的 {tot['missing_turn_rows']}）")
    print(f"  付奖行 + 被 `no_deal` 拦下    {tot['paid_rows']} + {tot['blocked_rows']} == "
          f"{tot['decl_turn_le_rows']} "
          f"{'✓' if tot['paid_rows'] + tot['blocked_rows'] == tot['decl_turn_le_rows'] else '✗'}")
    print(f"  结构检查：宣言行却已立直       {tot['viol_state_says_riichi']}（必须 = 0）")
    print(f"  手级：riichi_hands={tot['riichi_hands']} seat_hands={tot['seat_hands']}"
          f" riichi_turn_sum={tot['riichi_turn_sum']} known={tot['riichi_turn_known']}"
          f" missing={tot['riichi_turn_missing']}")
    n = tot["rows_in_scope"]
    mu = (tot["decl_turn_le_rows"] / n) if n else float("nan")
    q = (tot["paid_rows"] / n) if n else float("nan")
    if n and 0 < mu < 1 and 0 < q < 1:
        k = ((1 - mu) / (mu * (1 - mu)) ** 0.5) * (q * (1 - q)) ** 0.5
        print(f"  剂量换算：μ_行={mu:.6f} σ_行={(mu * (1 - mu)) ** 0.5:.6f} q_付奖={q:.6f}"
              f" ⇒ **k = std(塑形列)/w = {k:.4f}**")
        for delta in (5062.6, 5191.4):
            print(f"     若 δ（结果奖励逐行 std）≈ {delta:.0f} ⇒ 逐行同权 w* = δ/k ≈ **{delta / k:.0f}**")
        res.update(mu_row=mu, q_paid=q, k=k)
    if a.audit:
        au = json.loads(Path(a.audit).read_text(encoding="utf-8"))
        ax = next((x for x in au["axes"] if x.get("axis") == "riichi_turn"), None)
        if ax is None:
            raise SystemExit(f"⛔ 审计账里没有 riichi_turn 那一轴：{a.audit}（spec={au.get('spec')!r}）")
        pairs = [("decl_rows", "decl_rows"), ("decl_turn_le_rows", "decl_turn_rows"),
                 ("decl_turn_le_rows", "action_rows"), ("decl_late_rows", "decl_late_rows"),
                 ("missing_turn_rows", "missing_turn_rows"), ("paid_rows", "paid_rows"),
                 ("blocked_rows", "blocked_rows"), ("rows_in_scope", "n_used")]
        print(f"  ── 与审计账对账（{Path(a.audit).name}，spec={au['spec']}）")
        ok_all = True
        for mine, theirs in pairs:
            bad = tot[mine] != ax.get(theirs)
            ok_all &= not bad
            print(f"     {mine:<20} 复核 {tot[mine]:>7} vs 审计 `{theirs}` {ax.get(theirs):>7} "
                  f"{'✓' if not bad else '✗ **不一致**'}")
        bad_t = ax.get("turn_limit") != T
        ok_all &= not bad_t
        print(f"     {'turn_limit':<20} 复核 {T:>7} vs 审计 {ax.get('turn_limit'):>7} "
              f"{'✓' if not bad_t else '✗ **不一致**'}")
        res["audit_check_ok"] = ok_all
        res["ok"] &= ok_all
    if a.style_vector:
        sv = json.loads(Path(a.style_vector).read_text(encoding="utf-8"))
        st = next((v for k, v in (sv.get("policies") or {}).items() if "@" in k), None)
        if st:
            c = st["counts"]
            print(f"  ── 与判据侧对账（{Path(a.style_vector).name}）")
            for mine, theirs in (("riichi_turn_sum", "riichi_turn_sum"),
                                 ("riichi_turn_known", "riichi_turn_known"),
                                 ("riichi_turn_missing", "riichi_turn_missing"),
                                 ("riichi_hands", "riichi_hands"), ("seat_hands", "seat_hands")):
                bad = tot[mine] != c.get(theirs)
                print(f"     {mine:<20} 复核 {tot[mine]:>7} vs style-vector {c.get(theirs):>7} "
                      f"{'✓' if not bad else '✗ **不一致**'}")
                if bad:
                    res["ok"] = False
            res["avg_riichi_turn_check"] = {
                "mine": (tot["riichi_turn_sum"] / tot["riichi_turn_known"])
                if tot["riichi_turn_known"] else None,
                "style_vector": st["dims"].get("avg_riichi_turn")}
            print(f"     avg_riichi_turn      复核 {res['avg_riichi_turn_check']['mine']!r} vs "
                  f"style-vector {st['dims'].get('avg_riichi_turn')!r}")
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  落盘 {a.json}")
    print(f"  结论：{'PASS（三个来源逐项一致）' if res['ok'] else '**FAIL**（见上面 ✗）'}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
