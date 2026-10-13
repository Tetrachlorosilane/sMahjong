# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""只读复算：ROUTING-CEILING-COMMON 的逐臂配对判定。
⛔ 不跑自对弈/训练/采集/闸门；只解析冻结产物 summary.json + _ceiling-common.json。"""
import json, math, sys
from pathlib import Path

RAW = Path(r"S:\mahjong-training\raw\v4-ceiling-common")
WL = Path(r"S:\mahjong-training\w-ladder")
ARMS = ["incumbent", "riichi", "dama", "pon", "meld"]

S = {}
for a in ARMS:
    S[a] = json.loads((RAW / a / "summary.json").read_text(encoding="utf-8"))
CEIL = json.loads((WL / "_ceiling-common.json").read_text(encoding="utf-8"))

print("=" * 78)
print("[A] 配对结构自证（冻结 per_game 记录）")
print("=" * 78)
base = S["incumbent"]["per_game"]
print(f"games 逐臂: " + ", ".join(f"{a}={S[a]['games']}" for a in ARMS))
print(f"seed_base 逐臂: " + ", ".join(f"{a}={S[a]['seed_base']}" for a in ARMS))
ok_seed = ok_idx = ok_stu = 0
for a in ARMS:
    pg = S[a]["per_game"]
    if [g["game"] for g in pg] == [g["game"] for g in base]:
        ok_idx += 1
    if [g["seed"] for g in pg] == [g["seed"] for g in base]:
        ok_seed += 1
    # 学生席 = 第 0 席（policies[0] 含 @0#0.5），其余三席为固定风格桌（--rotate-perm 会换座位，比集合）
    opp_a = [tuple(sorted(p.split("\\")[-2] for p in g["policies"][1:])) for g in pg]
    opp_b = [tuple(sorted(p.split("\\")[-2] for p in g["policies"][1:])) for g in base]
    if opp_a == opp_b:
        ok_stu += 1
seat0 = {a: [g["policies"][0].split("\\")[-2] for g in S[a]["per_game"]] for a in ARMS}
print(f"game 下标一致臂数={ok_idx}/5 · seed 逐场一致臂数={ok_seed}/5 · 另三席(三套风格桌)集合逐场一致臂数={ok_stu}/5")
for a in ARMS:
    print(f"  seat0 政策名集合[{a}] = {sorted(set(seat0[a]))}")
both = [i for i in range(1000) if seat0["riichi"][i] != seat0["incumbent"][i]]
print(f"riichi vs incumbent 学生席换人场数 = {len(both)}/1000")
print(f"=> 逐小局配对前提（同 seed_base、同 seed、同座、同对手）= {'成立' if ok_seed == 5 and ok_stu == 5 and ok_idx == 5 else '不成立'}")

print()
print("=" * 78)
print("[B] 实测每场小局数（⛔ 不用假设常数）")
print("=" * 78)
hp = {}
for a in ARMS:
    hs = [g["hands"] for g in S[a]["per_game"]]
    hp[a] = sum(hs) / len(hs)
    print(f"{a:10s} 学生席总小局={sum(hs)}  场={len(hs)}  实测小局/场={hp[a]:.6f}  (报告 hands_per_game={S[a]['hands_per_game']})")
print(f"五臂实测小局/场 均值={sum(hp.values())/5:.6f}   极差={max(hp.values())-min(hp.values()):.6f}")
print(f"共同配对（交集）小局数 = {CEIL['n_hand_keys']}（并集 {CEIL['n_hand_union']}，臂内小局 11297~11308）")

print()
print("=" * 78)
print("[C] 逐小局配对读数（冻结值）+ 配对 SE 反解")
print("=" * 78)
print(f"{'arm':10s} {'Δ(点/小局)':>12s} {'SE_paired':>10s} {'t':>8s} {'n':>6s} {'SD_Δ':>10s} {'95%CI':>26s} {'p(2侧)':>10s}")
def ncdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
HAND = {}
for a in ["riichi", "dama", "pon", "meld"]:
    v = CEIL["vs_incumbent"][a]
    d, se, t, n = v["delta"], v["se"], v["t"], v["n"]
    sd = se * math.sqrt(n)
    p = 2 * (1 - ncdf(abs(t)))
    lo, hi = d - 1.959963985 * se, d + 1.959963985 * se
    HAND[a] = dict(delta=d, se=se, t=t, n=n, sd=sd, p=p, lo=lo, hi=hi)
    print(f"{a:10s} {d:12.3f} {se:10.4f} {t:8.4f} {n:6d} {sd:10.2f} [{lo:9.2f},{hi:9.2f}] {p:10.3e}")

print()
print("=" * 78)
print("[D] 逐场配对读数（由 per_game.final_scores 独立复算，点/场）")
print("=" * 78)
GAME = {}
inc = [g["final_scores"][0] for g in base]
inc_rp = [g["rank_points"][0] for g in base]
print(f"{'arm':10s} {'Δ点/场':>11s} {'SE':>9s} {'t':>8s} {'95%CI':>24s} | {'Δ马点/场':>10s} {'SE':>8s} {'t':>8s}")
for a in ["riichi", "dama", "pon", "meld"]:
    sc = [g["final_scores"][0] for g in S[a]["per_game"]]
    rp = [g["rank_points"][0] for g in S[a]["per_game"]]
    n = len(sc)
    dfr = [sc[i] - inc[i] for i in range(n)]
    dfp = [rp[i] - inc_rp[i] for i in range(n)]
    m, mr = sum(dfr) / n, sum(dfp) / n
    sdr = math.sqrt(sum((x - m) ** 2 for x in dfr) / (n - 1))
    sdp = math.sqrt(sum((x - mr) ** 2 for x in dfp) / (n - 1))
    ser, sep = sdr / math.sqrt(n), sdp / math.sqrt(n)
    GAME[a] = dict(delta=m, se=ser, t=m / ser, delta_rp=mr, se_rp=sep, t_rp=mr / sep, n=n)
    print(f"{a:10s} {m:11.3f} {ser:9.3f} {m/ser:8.4f} [{m-1.96*ser:9.2f},{m+1.96*ser:9.2f}] | {mr:10.4f} {sep:8.4f} {mr/sep:8.4f}")

print()
print("=" * 78)
print("[E] 只由两臂逐小局 SE 合成的配对 SE（对照：报告里的 vs_incumbent.se）")
print("=" * 78)
hse = {a: CEIL["table"][a]["hand_se"] for a in ARMS}
for a in ["riichi", "dama", "pon", "meld"]:
    comp = math.sqrt(hse[a] ** 2 + hse["incumbent"] ** 2)
    t = CEIL["vs_incumbent"][a]["delta"] / comp
    print(f"{a:10s} SE_composed={comp:8.4f}  t_composed={t:7.4f}   | 报告 SE={CEIL['vs_incumbent'][a]['se']:.4f} t={CEIL['vs_incumbent'][a]['t']:.4f}")

print()
print("=" * 78)
print("[F] Bonferroni 门槛（常正态近似，仅判定用）")
print("=" * 78)
z = {0.05: 1.959963985, 0.05 / 4: 2.497734073, 0.05 / 5: 2.575829304, 0.005: 2.807033768, 0.01: 2.575829304}
for k, v in z.items():
    lbl = "名义 α=0.05" if abs(k - 0.05) < 1e-9 else (f"Bonferroni m=4 → α={k:.5f}" if abs(k - 0.0125) < 1e-9 else (f"Bonferroni m=5 → α={k:.5f}" if abs(k - 0.01) < 1e-9 else f"两侧 α={k}"))
    print(f"  |t| > {v:.4f}   ← {lbl}")
print(f"  报告写的 2.81 ≈ 两侧 p=0.005 的临界值 2.807 ⇒ m=10 口径，不是 m=5")
print()
print("判定（用报告门槛 2.81 / m=5 门槛 2.576 / m=4 门槛 2.498 三档并列）:")
for a in ["riichi", "dama", "pon", "meld"]:
    t = HAND[a]["t"]
    print(f"  {a:8s} t={t:7.4f}  |t|>2.807? {'PASS' if abs(t)>2.807033768 else 'fail'}   |t|>2.576? {'PASS' if abs(t)>2.575829304 else 'fail'}   |t|>2.498? {'PASS' if abs(t)>2.497734073 else 'fail'}")

print()
print("=" * 78)
print("[G] 量级换算（点/小局 → 点/场 → 马点/场）")
print("=" * 78)
for a in ["riichi", "dama", "pon", "meld"]:
    h = HAND[a]["delta"]
    gp = GAME[a]
    print(f"{a:8s} 逐小局Δ={h:8.2f} 点/小局 × 实测 {hp[a]:.6f} 小局/场 = {h*hp[a]:9.2f} 点/场"
          f"   （逐场直算 {gp['delta']:9.2f} 点/场；差 {h*hp[a]-gp['delta']:+.2f}）"
          f"  马点/场(直算)={gp['delta_rp']:+.4f}  CI95=[{gp['delta_rp']-1.96*gp['se_rp']:+.3f},{gp['delta_rp']+1.96*gp['se_rp']:+.3f}]")

print()
print("=" * 78)
print("[H] 逐小局配对读数 vs 逐场读数（同一条链，两种分辨率）")
print("=" * 78)
for a in ["riichi", "dama", "pon", "meld"]:
    print(f"{a:8s} 逐小局 t={HAND[a]['t']:7.4f} (n=9790)   逐场点 t={GAME[a]['t']:7.4f} (n=1000)   逐场马点 t={GAME[a]['t_rp']:7.4f}")

print()
print("=" * 78)
print("[I] riichi 与闸门对照的换算")
print("=" * 78)
rp = GAME["riichi"]["delta_rp"]
print(f"共同评测 riichi：{HAND['riichi']['delta']:.2f} 点/小局 = {HAND['riichi']['delta']*hp['riichi']:.2f} 点/场 = {rp:+.4f} 马点/场 (CI [{rp-1.96*GAME['riichi']['se_rp']:+.3f},{rp+1.96*GAME['riichi']['se_rp']:+.3f}], n=1000 场)")
print(f"闸门 riichi：+3.26 马点/场 CI[+2.29,+4.26] (n=12000 场, 4 套牌山；套间散布 3.79 = 合并 CI 半宽 0.98 的 3.9×)")
print(f"两者是否一致（点估计差 {rp-3.26:+.3f}，共同评测 CI 是否含 3.26：{'含' if rp-1.96*GAME['riichi']['se_rp']<=3.26<=rp+1.96*GAME['riichi']['se_rp'] else '不含'}）")
