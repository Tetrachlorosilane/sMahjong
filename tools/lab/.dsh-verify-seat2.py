# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""只读：座位下标硬校验（不许猜 —— per_game 数组是 seat 序还是 policy 序，用守恒量判）。"""
import json, math
from pathlib import Path

RAW = Path(r"S:\mahjong-training\raw\v4-ceiling-common")
ARMS = ["incumbent", "riichi", "dama", "pon", "meld"]
S = {a: json.loads((RAW / a / "summary.json").read_text(encoding="utf-8")) for a in ARMS}

def stu_idx(g):
    for i, p in enumerate(g["policies"]):
        if "@0#0.5" in p:
            return i
    raise SystemExit("no student")

print("=" * 78)
print("[J] 逐场四席分合计守恒（判 per_game 数组是 seat 序）")
print("=" * 78)
for a in ARMS:
    bad = 0
    tot = None
    for g in S[a]["per_game"]:
        s = sum(g["final_scores"])
        if s != 100000:
            bad += 1
        tot = s
    print(f"{a:10s} final_scores 四席合计 != 100000 的场数 = {bad}/1000 (最后一场={tot})")

print()
print("=" * 78)
print("[K] 同名政策出现两席? / 学生席下标 == 带 @0#0.5 的下标 (逐场)")
print("=" * 78)
for a in ARMS:
    mism = 0
    dup = 0
    for g in S[a]["per_game"]:
        if stu_idx(g) != 0:
            mism += 1
        names = [p.split("\\")[-2] for p in g["policies"]]
        if len(set(names)) != 4:
            dup += 1
    print(f"{a:10s} @0#0.5 不在 seat0 的场数={mism}  同名政策重复的场数={dup}")

print()
print("=" * 78)
print("[L] 学生席逐场读数（seat=stu_idx）—— 两通道自证")
print("=" * 78)
def chan_local(a):
    sc = [g["final_scores"][stu_idx(g)] for g in S[a]["per_game"]]
    rp = [g["rank_points"][stu_idx(g)] for g in S[a]["per_game"]]
    hs = [g["hands"] for g in S[a]["per_game"]]
    return sc, rp, hs

def chan_invariant(a):
    """用守恒量反解学生席：fine, 直接用四席合计 - 另三席。此处只做交叉核对。"""
    sc = []
    for g in S[a]["per_game"]:
        si = stu_idx(g)
        # 学生分 = 总分 - 其余三席分（不依赖下标语义时也成立）
        sc.append(sum(g["final_scores"]) - sum(v for i, v in enumerate(g["final_scores"]) if i != si))
    return sc

for a in ARMS:
    sc, rp, hs = chan_local(a)
    sc2 = chan_invariant(a)
    print(f"{a:10s} 点/场={sum(sc)/len(sc):10.2f}  马点/场={sum(rp)/len(rp):+8.4f}  实测小局/场={sum(hs)/len(hs):.3f}  两通道一致={sc == sc2}")
    print(f"           by_policy(学生席) avg_delta={[v for k,v in S[a]['by_policy'].items() if '@0#0.5' in k][0]['avg_delta']:10.3f}"
          f"  avg_rank_points={[v for k,v in S[a]['by_policy'].items() if '@0#0.5' in k][0]['avg_rank_points']:+8.4f}")

print()
print("=" * 78)
print("[M] 逐场配对（同 seed、同席、同对手）Δ 点/场 与 Δ马点/场")
print("=" * 78)
INC_SC, INC_RP, _ = chan_local("incumbent")
print(f"{'arm':10s} {'Δ点/场':>10s} {'SE':>9s} {'t':>7s} {'95%CI':>23s} | {'Δ马点/场':>9s} {'SE':>8s} {'t':>7s} {'95%CI':>21s} {'n':>5s}")
RES = {}
for a in ["riichi", "dama", "pon", "meld"]:
    sc, rp, _ = chan_local(a)
    n = len(sc)
    dr = [sc[i] - INC_SC[i] for i in range(n)]
    dp = [rp[i] - INC_RP[i] for i in range(n)]
    m, mr = sum(dr) / n, sum(dp) / n
    ser = math.sqrt(sum((x - m) ** 2 for x in dr) / (n - 1)) / math.sqrt(n)
    sep = math.sqrt(sum((x - mr) ** 2 for x in dp) / (n - 1)) / math.sqrt(n)
    RES[a] = dict(dr=m, dr_se=ser, dr_t=m / ser, dp=mr, dp_se=sep, dp_t=mr / sep, n=n)
    print(f"{a:10s} {m:10.2f} {ser:9.2f} {m/ser:7.4f} [{m-1.96*ser:8.1f},{m+1.96*ser:8.1f}] | {mr:9.4f} {sep:8.4f} {mr/sep:7.4f} [{mr-1.96*sep:+7.3f},{mr+1.96*sep:+7.3f}] {n:5d}")

print()
print("=" * 78)
print("[N] 换算核对：逐小局 Δ × 实测小局/场 vs 逐场直算 Δ（同一条链）")
print("=" * 78)
CEIL = json.loads((Path(r"S:\mahjong-training\w-ladder") / "_ceiling-common.json").read_text(encoding="utf-8"))
for a in ["riichi", "dama", "pon", "meld"]:
    d = CEIL["vs_incumbent"][a]["delta"]
    _, _, hs = chan_local(a)
    hpg = sum(hs) / len(hs)
    print(f"{a:10s} {d:8.2f} 点/小局 × {hpg:.3f} = {d*hpg:9.2f} 点/场   | 逐场直算 {RES[a]['dr']:9.2f} 点/场  (差 {d*hpg-RES[a]['dr']:+8.2f})"
          f"   逐场马点/场={RES[a]['dp']:+7.4f} (CI [{RES[a]['dp']-1.96*RES[a]['dp_se']:+.3f},{RES[a]['dp']+1.96*RES[a]['dp_se']:+.3f}])")

print()
print("=" * 78)
print("[O] 逐小局 SE 的来源判定：sd(Δ)/sqrt(n)")
print("=" * 78)
for a in ["riichi", "dama", "pon", "meld"]:
    v = CEIL["vs_incumbent"][a]
    sd = v["se"] * math.sqrt(v["n"])
    comp = math.sqrt(CEIL["table"][a]["hand_se"] ** 2 + CEIL["table"]["incumbent"]["hand_se"] ** 2)
    print(f"{a:10s} 反解 sd(逐小局Δ)={sd:8.1f}  合成(非配对)SE={comp:7.3f}  报告SE={v['se']:7.3f}")
