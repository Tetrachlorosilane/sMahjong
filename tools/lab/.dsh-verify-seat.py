# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""只读：核对学生席位置（是否真的"同席"）+ 用正确座位复算逐场读数。"""
import json, math
from pathlib import Path

RAW = Path(r"S:\mahjong-training\raw\v4-ceiling-common")
ARMS = ["incumbent", "riichi", "dama", "pon", "meld"]
S = {a: json.loads((RAW / a / "summary.json").read_text(encoding="utf-8")) for a in ARMS}

def student_seat(g):
    for i, p in enumerate(g["policies"]):
        if "@0#0.5" in p:
            return i
    return None

print("=" * 78)
print("[A2] 学生席下标（带 @0#0.5 的那一席）逐场分布")
print("=" * 78)
for a in ARMS:
    seats = [student_seat(g) for g in S[a]["per_game"]]
    from collections import Counter
    c = Counter(seats)
    same_as_inc = sum(1 for i, g in enumerate(S[a]["per_game"]) if seats[i] == student_seat(S["incumbent"]["per_game"][i]))
    print(f"{a:10s} 分布={dict(sorted(c.items()))}  与现任同席场数={same_as_inc}/1000")

print()
print("=" * 78)
print("[A3] 同 seed ⇒ 旋转排列是否一致（前 8 场明细）")
print("=" * 78)
for i in range(8):
    print(f"game{i} seed={S['incumbent']['per_game'][i]['seed']}")
    for a in ARMS:
        g = S[a]["per_game"][i]
        names = ["/".join(p.split("\\")[-2:]) for p in g["policies"]]
        print(f"   {a:10s} seat0..3 = {names}   student_seat={student_seat(g)}")
    if i >= 3:
        break

print()
print("=" * 78)
print("[D2] 逐场配对读数（按各自学生席取分，点/场 与 马点/场）")
print("=" * 78)
def series(a, key):
    out = []
    for g in S[a]["per_game"]:
        s = student_seat(g)
        out.append(g[key][s])
    return out

inc_sc, inc_rp = series("incumbent", "final_scores"), series("incumbent", "rank_points")
print(f"{'arm':10s} {'Δ点/场':>11s} {'SE':>9s} {'t':>8s} {'95%CI':>24s} | {'Δ马点/场':>10s} {'SE':>8s} {'t':>8s} {'95%CI':>22s}")
for a in ["riichi", "dama", "pon", "meld"]:
    sc, rp = series(a, "final_scores"), series(a, "rank_points")
    n = len(sc)
    dfr = [sc[i] - inc_sc[i] for i in range(n)]
    dfp = [rp[i] - inc_rp[i] for i in range(n)]
    m, mr = sum(dfr) / n, sum(dfp) / n
    sr = math.sqrt(sum((x - m) ** 2 for x in dfr) / (n - 1)) / math.sqrt(n)
    sp = math.sqrt(sum((x - mr) ** 2 for x in dfp) / (n - 1)) / math.sqrt(n)
    print(f"{a:10s} {m:11.3f} {sr:9.3f} {m/sr:8.4f} [{m-1.96*sr:9.2f},{m+1.96*sr:9.2f}] | {mr:10.4f} {sp:8.4f} {mr/sp:8.4f} [{mr-1.96*sp:+7.3f},{mr+1.96*sp:+7.3f}]")

print()
print("=" * 78)
print("[D3] 用 per_game.by_policy 的口径核对：报告 §4 'mean Δ' 是不是 by_policy.avg_delta")
print("=" * 78)
for a in ARMS:
    bp = [v for v in S[a]["by_policy"].values() if "@0#0.5" in [k for k in S[a]["by_policy"]][0] or True]
    stu = None
    for k, v in S[a]["by_policy"].items():
        if "@0#0.5" in k:
            stu = v
    hand_mean = None
    print(f"{a:10s} by_policy(学生席) avg_delta={stu['avg_delta']:10.3f} avg_place={stu['avg_place']:.3f} "
          f"avg_rank_points={stu['avg_rank_points']:+8.4f} win_rate={stu['win_rate']:.4f} deal_in_rate={stu['deal_in_rate']:.4f} "
          f"seat_hands={stu['seat_hands']}")
