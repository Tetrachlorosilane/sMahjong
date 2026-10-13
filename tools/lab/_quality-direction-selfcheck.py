# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""**(A) 方向性判据的自证**：新规则（按轴意图取单侧 CI）vs 旧规则（方向无关的机械式）。

判据出处：`PREREGISTRATION-MELD-TYPE.md` §6.0。四条自证：
  ① **真数据**：放铳率 `deal_rate` Δ=−0.992（SE 0.44，`pon` 轴 c1）⇒ **不再报越界**（旧规则会报）；
  ② 合成 **和了率** Δ=−2.0 / SE 0.4 ⇒ **报越界**；
  ③ 合成 **放铳率** Δ=+2.0 / SE 0.4 ⇒ **报越界**；
  ④ **点估计 0 / CI 宽**（Δ=0、SE=5）⇒ **不报**。

⛔ 本脚本**只调** `tools/w-ladder.py` 的 `companion_verdict`（判据只有一处实现）；
   旧规则在本地重写一份（`git show HEAD:tools/w-ladder.py` 那一版就是它）。
"""
import importlib.util
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                     # noqa: BLE001
    pass

REPO = Path(r"C:\Users\HP\source\games\mahjong")
STAGE = Path(r"S:\mahjong-training\w-ladder")
spec = importlib.util.spec_from_file_location("wl", REPO / "tools" / "w-ladder.py")
wl = importlib.util.module_from_spec(spec)
sys.modules["wl"] = wl
spec.loader.exec_module(wl)
Z95 = wl.Z95


def old_rule(delta: float, se: float, tol: float) -> bool:
    """**旧的**方向无关规则（HEAD：`bad ⇔ hi < 0 or delta < −tol`）—— 逐字复刻，用作对照。"""
    return (delta + Z95 * se) < 0.0 or delta < -tol


def old_rule_before_2026_10_10(delta: float, se: float, tol: float) -> bool:
    """更老的规则（`ok ⇔ CI 下界 ≥ −tol`）—— 只用来标注 `companion-quality-dama.json` 那份**陈旧**产物。"""
    return (delta - Z95 * se) < -tol


# ---- ① 真数据：`pon` 轴 c1 的放铳率（取自 paired/v4-wl-meldtype-c1-g01-POST.json）----------------
PAIRED = STAGE / "paired" / "v4-wl-meldtype-c1-g01-POST.json"
doc = json.loads(PAIRED.read_text(encoding="utf-8"))
b = doc["paired_delta"]["deal_rate"]
REAL = (b["delta_pp"], b["se_pp"], b["n_games"])
print(f"① 真数据 `deal_rate`（{PAIRED.name}）：Δ={REAL[0]:+.4f}pp SE={REAL[1]:.4f} "
      f"CI[{b['lo_pp']:+.4f},{b['hi_pp']:+.4f}] n={REAL[2]}")
print()

CASES = [
    ("① 真数据 放铳率 `deal_rate`（方向=上升为坏）", "deal_rate", REAL[0], REAL[1], REAL[2],
     "**不再报越界**（旧规则会报 —— 这正是要修的那条假警）"),
    ("② 合成 和了率 `win_rate`（方向=下降为坏）", "win_rate", -2.0, 0.4, 1000,
     "**报越界**"),
    ("③ 合成 放铳率 `deal_rate`（方向=上升为坏）", "deal_rate", +2.0, 0.4, 1000,
     "**报越界**"),
    ("④ 合成 点估计 0 / CI 宽", "win_rate", 0.0, 5.0, 1000, "**不报**"),
]

print("== (A) 方向性判据自证：新规则 vs 旧规则（方向无关） ==")
ok_all = True
for name, metric, d, se, n, expect in CASES:
    tol = wl._companion_tol(metric, None)
    v = wl.companion_verdict(d, se, n, metric=metric, tol=None)
    o = old_rule(d, se, wl.COMPANION_TOL)
    old_txt = "越界" if o else "不越界"
    new_txt = "越界" if not v["ok"] else "不越界"
    print(f"\n{name}（tol={tol:g}{v['unit']}，n={n}）")
    print(f"   bad_when={v['bad_when']}  {v['bad_rule']}")
    print(f"   点估计 Δ={d:+.4f}  SE={se:.4f}  CI[{v['lo']:+.4f},{v['hi']:+.4f}]")
    print(f"   旧规则（方向无关，tol=2）={old_txt}   |   **新规则 = {new_txt}**   ← 期望 {expect}")
print()

# ---- 逐格对拍：`down` 类量上新旧必须**逐格相同**；`up` 类量上必须不同（这正是修的东西） ---------
# ⚠ **同一个 `tol` 比**（用新代码解析出来的那一个）：这样差异**只**归因于"方向"，不会把
#   `avg_win_score` 的**单位级容忍带**（2 → 150 点，另一件事）混进来。后者在下半段单独报。
print("== 对拍：**同一个 tol** 下，旧规则（方向无关）vs 新规则（方向感知），逐格 ==")
GRID = [round(-4.0 + 0.25 * i, 2) for i in range(33)]
SES = (0.0, 0.1, 0.4, 1.0, 2.0, 5.0)
diff_down = diff_up = 0
for metric in ("riichi_rate", "meld_rate", "win_rate", "riichi_win_rate", "avg_win_score",
               "dama_rate", "decline_rate", "fold_rate", "pon_type_rate",
               "deal_rate", "riichi_turn_mean"):
    tol = wl._companion_tol(metric, None)
    bd = wl._companion_direction(metric)
    nd = 0
    for se in SES:
        for d in GRID:
            nv = not wl.companion_verdict(d, se, 1000, metric=metric, tol=None)["ok"]
            ov = old_rule(d, se, tol)          # ★ 旧规则喂**同一个 tol** ⇒ 差异只来自方向
            nd += int(nv != ov)
    if bd == "down":
        diff_down += nd
    else:
        diff_up += nd
    print(f"  {metric:<18} bad_when={bd:<4} tol={tol:<6g} 差异格数={nd}")
print(f"  ⇒ `down` 类量合计差异 **{diff_down}** 格（必须 = 0：同 tol 下与旧规则逐格相同）｜"
      f"`up` 类量合计差异 **{diff_up}** 格（必须 > 0：旧规则在反方向假警、新规则不报）")
ok_all &= (diff_down == 0 and diff_up > 0)
print()
print("== ⚠ 另报（**不是方向**那一件事）：单位级容忍带 ==")
for metric, t in sorted(wl.COMPANION_TOL_BY_METRIC.items()):
    print(f"  {metric}：缺省容忍带 {wl.COMPANION_TOL:g} → **{t:g} 点**（单位 = "
          f"`{wl._companion_unit(metric)}`）—— 依据 = 闸门侧同量纲口径 `win_points:150`；"
          f"同名量的旧**手搓**读法（`_fold-any-readout.py` 的 `TOL={{'avg_win_score':150.0}}`）"
          f"用的就是它 ⇒ 这里只是把它**收进判据**，⛔ 没有改数")
print("  ⇒ 影响面：`avg_win_score` **原先不在** `COMPANION_METRICS` 里（`quality` 硬拒它）"
      "⇒ **没有任何既有产物**用 2.0 判过打点，逐字节回归不受影响")
print()

# ---- 陈旧产物标注（`companion-quality-dama.json` 是 2026-10-10 **之前**那版规则写的）--------------
STALE = STAGE / "companion-quality-dama.json"
if STALE.is_file():
    s = json.loads(STALE.read_text(encoding="utf-8"))
    print(f"== 陈旧产物标注：{STALE.name} metric={s['metric']} Δ={s['delta']:+.3f} "
          f"SE={s['se']:.3f} ok={s['ok']}")
    print(f"   它的 `ok=false` 来自 **2026-10-10 之前**那条 `ok ⇔ CI 下界 ≥ −tol`："
          f"下界 {s['delta'] - Z95 * s['se']:+.3f} < −{s['tol']:g} ⇒ 判越界"
          f"（更老的规则报 {old_rule_before_2026_10_10(s['delta'], s['se'], s['tol'])}）；"
          f"方向无关那版报 {old_rule(s['delta'], s['se'], s['tol'])}、新规则报 "
          f"{not wl.companion_verdict(s['delta'], s['se'], s['n_games'], metric=s['metric'], tol=None)['ok']}")
    print("   ⛔ 这一份**不是**逐字节回归的基准（它是旧规则的产物）；回归基准用 `paired/*.json` 现算。")

print(f"\n(A) 自证总判：{'PASS' if ok_all else 'FAIL'}")
raise SystemExit(0 if ok_all else 1)
