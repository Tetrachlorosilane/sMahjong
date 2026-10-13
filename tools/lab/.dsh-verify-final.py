# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""只读：逐臂判定表的最终数字（含量级换算的两种小局/场口径）。"""
import json, math
from pathlib import Path

RAW = Path(r"S:\mahjong-training\raw\v4-ceiling-common")
WL = Path(r"S:\mahjong-training\w-ladder")
ARMS = ["incumbent", "riichi", "dama", "pon", "meld"]
S = {a: json.loads((RAW / a / "summary.json").read_text(encoding="utf-8")) for a in ARMS}
C = json.loads((WL / "_ceiling-common.json").read_text(encoding="utf-8"))

def stu(a, key):
    for g in S[a]["per_game"]:
        for i, p in enumerate(g["policies"]):
            if "@0#0.5" in p:
                return [gg[key][ii] for gg in S[a]["per_game"]
                        for ii, pp in enumerate(gg["policies"]) if "@0#0.5" in pp]

def stu_series(a, key):
    out = []
    for g in S[a]["per_game"]:
        si = next(i for i, p in enumerate(g["policies"]) if "@0#0.5" in p)
        out.append(g[key][si])
    return out

def stu_hands(a):
    return [g["hands"] for g in S[a]["per_game"]]

print("=" * 96)
print("判据表（逐小局配对 · 同 seed_base=51027301 · 同席 · 同对手 · n=9790 小局 / 1000 场）")
print("=" * 96)
print(f"{'arm':9s} {'Δ点/小局':>10s} {'SE':>8s} {'t':>7s} {'p(2侧)':>10s} {'95%CI':>22s} {'判定@2.81':>10s} {'@2.576':>8s} {'@2.498':>8s}")
CRIT = {"2.81": 2.807033768, "2.576": 2.575829304, "2.498": 2.497734073}
def nsf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
for a in ["riichi", "dama", "pon", "meld"]:
    v = C["vs_incumbent"][a]
    p = 2 * (1 - nsf(abs(v["t"])))
    lo, hi = v["delta"] - 1.959963985 * v["se"], v["delta"] + 1.959963985 * v["se"]
    print(f"{a:9s} {v['delta']:10.2f} {v['se']:8.3f} {v['t']:7.4f} {p:10.3e} [{lo:8.2f},{hi:8.2f}] "
          f"{'PASS' if abs(v['t'])>CRIT['2.81'] else 'fail':>10s} {'PASS' if abs(v['t'])>CRIT['2.576'] else 'fail':>8s} {'PASS' if abs(v['t'])>CRIT['2.498'] else 'fail':>8s}")

print()
print("=" * 96)
print("量级换算（写清每一级用的数）")
print("=" * 96)
hpg_own, hpg_pool = {}, {}
for a in ARMS:
    hpg_own[a] = sum(stu_hands(a)) / 1000
for a in ["riichi", "dama", "pon", "meld"]:
    hpg_pool[a] = (hpg_own[a] + hpg_own["incumbent"]) / 2
print("实测小局/场（学生席，逐臂）: " + " · ".join(f"{a}={hpg_own[a]:.3f}" for a in ARMS))
print(f"五臂均值={sum(hpg_own.values())/5:.4f}  极差={max(hpg_own.values())-min(hpg_own.values()):.4f}  （⛔ 不用假设常数）")
print()
inc_sc, inc_rp = stu_series("incumbent", "final_scores"), stu_series("incumbent", "rank_points")
for a in ["riichi", "dama", "pon", "meld"]:
    d = C["vs_incumbent"][a]["delta"]
    sc, rp = stu_series(a, "final_scores"), stu_series(a, "rank_points")
    n = 1000
    dr = [sc[i] - inc_sc[i] for i in range(n)]
    dp = [rp[i] - inc_rp[i] for i in range(n)]
    m, mr = sum(dr) / n, sum(dp) / n
    ser = math.sqrt(sum((x - m) ** 2 for x in dr) / (n - 1)) / math.sqrt(n)
    sep = math.sqrt(sum((x - mr) ** 2 for x in dp) / (n - 1)) / math.sqrt(n)
    print(f"{a:8s} {d:8.2f} 点/小局 × 小局/场(own {hpg_own[a]:.3f} → {d*hpg_own[a]:9.1f} 点/场 | pool {hpg_pool[a]:.3f} → {d*hpg_pool[a]:9.1f} 点/场 | 共同 11.319 → {d*11.319:9.1f})"
          f"  ‖逐场配对直算 {m:9.1f} 点/场 (t={m/ser:.4f}) · {mr:+7.4f} 马点/场 (t={mr/sep:.4f}, CI[{mr-1.96*sep:+.3f},{mr+1.96*sep:+.3f}])")

print()
print("=" * 96)
print("riichi 的风格读数（by_policy 学生席，冻结值）")
print("=" * 96)
for a in ["incumbent", "riichi"]:
    v = [x for k, x in S[a]["by_policy"].items() if "@0#0.5" in k][0]
    print(f"{a:10s} win_rate={v['win_rate']:.4f} deal_in_rate={v['deal_in_rate']:.4f} avg_place={v['avg_place']:.3f} "
          f"avg_rank_points={v['avg_rank_points']:+.4f} seat_hands={v['seat_hands']}")
ic = [x for k, x in S["incumbent"]["by_policy"].items() if "@0#0.5" in k][0]
rc = [x for k, x in S["riichi"]["by_policy"].items() if "@0#0.5" in k][0]
print(f"差：和了率 {(rc['win_rate']-ic['win_rate'])*100:+.2f}pp · 放铳率 {(rc['deal_in_rate']-ic['deal_in_rate'])*100:+.2f}pp · "
      f"平均顺位 {rc['avg_place']-ic['avg_place']:+.3f} · 马点/场 {rc['avg_rank_points']-ic['avg_rank_points']:+.4f}")

print()
print("=" * 96)
print("与闸门并列（两套口径的分辨率）")
print("=" * 96)
mr = sum(stu_series("riichi", "rank_points")) / 1000 - sum(inc_rp) / 1000
dp = [stu_series("riichi", "rank_points")[i] - inc_rp[i] for i in range(1000)]
sep = math.sqrt(sum((x - mr) ** 2 for x in dp) / 999) / math.sqrt(1000)
print(f"共同评测（本报告）：riichi−现任 = {mr:+.4f} 马点/场  SE={sep:.4f}  CI[{mr-1.96*sep:+.3f},{mr+1.96*sep:+.3f}]  n=1000 场 × 1 套牌山")
print(f"                    = {C['vs_incumbent']['riichi']['delta']:.2f} 点/小局 (CI[{C['vs_incumbent']['riichi']['delta']-1.96*C['vs_incumbent']['riichi']['se']:.2f},"
      f"{C['vs_incumbent']['riichi']['delta']+1.96*C['vs_incumbent']['riichi']['se']:.2f}])  n=9790 小局")
print(f"闸门（GATE-dec6170-4g-strength-verdict）：+3.26 马点/场  CI[+2.29,+4.26]  n=12000 场 × 4 套牌山（套间散布 3.79 = 合并 CI 半宽 0.98 的 3.9×）")
print(f"点估计差 = {mr-3.26:+.4f} 马点/场；共同评测 CI {'含' if mr-1.96*sep <= 3.26 <= mr+1.96*sep else '不含'}闸门点估计 ⇒ 两者在共同评测的分辨率下{'一致' if mr-1.96*sep <= 3.26 <= mr+1.96*sep else '不可区分/不一致'}")
