# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`riichi_turn` 轴的**硬判据 ③**（白化 mean≈0 / var≈1）与**负向硬判据**（⛔ 一律当场报错，不许静默退化）。

  ① **正例**：付奖行 == `declared_turn ≤ T` 的宣言行数（四数自洽）、白化 mean≈0 / var≈1；
  ② **σ=0**（本数据集上一行付奖候选都没有）⇒ 报错（不许 1/σ 炸、也不许悄悄少一个轴）；
  ③ 宣言行**取不到巡目**（`obs.player_draws` 缺失）⇒ 报错（⛔ 不猜 0 = 不把"取不到"读成"最早立直"）；
  ④ `hand` 模式用 `riichi_turn` ⇒ 报错（合并量不是"宣言那一行" = 静默换轴）；
  ⑤ `riichi_turn` + 伴随量在真数据上**恒真**（`blocked_rows == 0`）⇒ 报错（`ROW_PAIR_MUST_BLOCK`）；
  ⑥ ⚠ **与既有 `riichi` 轴共存**：同一份轨迹上两轴各自付奖；**晚立直（巡目 > T）那一侧一行都不付**。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\HP\source\games\mahjong\python")
from mahjong_ml.v4 import style_reward as sr      # noqa: E402

STU = "net:x@0#0.5"
T = int(sr.ROW_TURN_LIMIT["riichi_turn"])
TMP = Path(r"S:\mahjong-training\w-ladder\_rt-neg")
TMP.mkdir(parents=True, exist_ok=True)


def row(hand, chosen, draws=3, loser=-1, winner=-1, agari=False, seat=0, legal=None):
    obs = {"v": 3, "seat": seat, "riichi": [False] * 4, "riichi_turn": [0] * 4,
           "melds": [[], [], [], [], ], "player_draws": draws}
    r = {"type": "decision", "game": 0, "hand_no": hand, "seat": seat, "policy": STU,
         "kind": "turn", "chosen": chosen, "chosen_index": 0,
         "hand_delta": [0, 0, 0, 0], "hand_winner": winner, "hand_loser": loser,
         "hand_agari": agari, "obs": obs}
    if legal is not None:
        r["legal"] = list(legal)
        obs["legal"] = list(legal)
    return r


def write(name, rows):
    p = TMP / f"{name}.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return p


def scan(p, spec_text="riichi_turn=1000:no_deal", mode="decision"):
    spec = sr.parse_bonus(spec_text)
    st, au = sr.whitening_stats(spec, sr.iter_hands(p), student_policies={STU}, quiet=True, mode=mode)
    chk = sr.audit_whitening(spec, st, sr.iter_hands(p), student_policies={STU}, mode=mode)
    return spec, st, au, chk


def expect(name, fn, needle):
    try:
        fn()
        print(f"  ✗ {name}：**没有报错**（应当报错！）")
        return False
    except sr.BonusSpecError as e:
        good = needle in str(e)
        print(f"  {'✓' if good else '✗'} {name}：{str(e)[:160]}")
        return good


ok = True
print(f"== `riichi_turn` 硬判据 ③ + 负向硬判据（T = {T}）==")

# ---- ① 正例：3 个小局；早立直且未放铳 / 早立直且放铳 / 晚立直且未放铳 -------------------------
p = write("ok", [
    row(0, "riichi:1m", draws=T - 3, loser=-1),          # 早立直 + 未放铳 ⇒ 付奖
    row(1, "riichi:1m", draws=T - 2, loser=0),           # 早立直 + 放铳   ⇒ 被伴随量拦下
    row(2, "riichi:1m", draws=T + 4, loser=-1),          # 晚立直（> T）    ⇒ 一行都不付
    row(3, "discard:9m", draws=2, loser=-1),             # 根本不是宣言行   ⇒ 不参与
])
spec, st, au, chk = scan(p)
ax = au.axes[0]
four = (ax["decl_rows"], ax["decl_turn_rows"], ax["decl_late_rows"], ax["missing_turn_rows"])
eq_ok = (four == (3, 2, 1, 0) and ax["action_rows"] == 2 and ax["paid_rows"] == 1
         and ax["blocked_rows"] == 1 and ax["paid_rows"] + ax["blocked_rows"] == ax["action_rows"]
         and ax["turn_limit"] == T and ax["decl_turn_rows"] == ax["action_rows"])
print(f"  {'✓' if eq_ok else '✗'} ① 四数 (宣言, ≤T, >T, 取不到) = {four}；动作行 {ax['action_rows']}"
      f" == 巡目 ≤ T 的宣言行 {ax['decl_turn_rows']}；付奖 {ax['paid_rows']} + 拦下 {ax['blocked_rows']}"
      f" == 动作行 {ax['action_rows']}；turn_limit={ax['turn_limit']}")
wh = chk["riichi_turn"]
w_ok = abs(wh["mean"]) <= 1e-9 and abs(wh["var"] - 1.0) <= 1e-9
print(f"  {'✓' if w_ok else '✗'} ① 白化：mean={wh['mean']:+.2e} var={wh['var']!r} n={wh['n']}"
      f"（行级总体 = 学生席决策行）")
ok &= eq_ok and w_ok

# ---- ② σ=0：本数据集上一行付奖候选都没有（宣言行全是晚立直）⇒ 必须报错 ------------------------
p = write("sigma0", [row(h, "discard:1m", draws=1, loser=-1) for h in (0, 1)])
ok &= expect("② σ=0（这一轴在本数据集上是常数）⇒ 报错",
             lambda: scan(p)[2], "σ")

# ---- ③ 宣言行取不到巡目（`obs.player_draws` 缺失）⇒ 报错（⛔ 不猜 0） -------------------------
rows = [row(0, "riichi:1m", draws=T - 1, loser=-1), row(1, "discard:9m", draws=1, loser=0)]
rows[0]["obs"].pop("player_draws")
p = write("noturn", rows)
ok &= expect("③ 宣言行取不到 `obs.player_draws` ⇒ 报错（⛔ 不猜 0）",
             lambda: scan(p)[2], "巡目")

# ---- ④ hand 模式用 riichi_turn ⇒ 报错（= 静默换轴） -------------------------------------------
p = write("handmode", [row(h, "riichi:1m", draws=T - 1, loser=0) for h in (0, 1)])
ok &= expect("④ `hand` 模式用 `riichi_turn` ⇒ 报错（不静默换轴）",
             lambda: scan(p, mode="hand"), "decision")

# ---- ⑤ 伴随量恒真（动作行 > 0 但一次都没被拦下）⇒ 报错（`ROW_PAIR_MUST_BLOCK`） ---------------
# ⚠ 光看"动作行"还不够：这里必须让 μ ∈ (0,1)（不然先撞 σ=0 那道闸门，测不到这条）
p = write("allpass", [row(0, "riichi:1m", draws=T - 1, loser=-1),
                      row(1, "discard:9m", draws=2, loser=-1)])
ok &= expect("⑤ 伴随量在真数据上恒真（`blocked_rows == 0`）⇒ 报错",
             lambda: scan(p)[2], "恒真")

# ---- ⑥ 与既有 `riichi` 轴**共存**：两轴各自付奖；晚立直那一侧对 riichi_turn 一行都不付 ---------
p = write("coexist", [
    row(0, "riichi:1m", draws=T - 3, loser=-1),          # 早立直 + 未放铳
    row(1, "riichi:1m", draws=T + 4, loser=-1),          # 晚立直 + 未放铳
    row(2, "riichi:1m", draws=T - 1, loser=0),           # 早立直 + 放铳（拦下）
    row(3, "discard:9m", draws=2, loser=-1),             # 没立直（让两轴的 μ 都不退化）
])
spec2, _st2, au2, chk2 = scan(p, "riichi=1000:no_deal,riichi_turn=1000:no_deal")
a_ri, a_rt = au2.axes[0], au2.axes[1]
co_ok = (a_ri["action_rows"], a_ri["paid_rows"], a_ri["blocked_rows"]) == (3, 2, 1) \
    and (a_rt["action_rows"], a_rt["paid_rows"], a_rt["blocked_rows"]) == (2, 1, 1) \
    and a_rt["decl_late_rows"] == 1 and a_rt["decl_rows"] == 3 \
    and chk2["_all_ok"]["ok"] and abs(chk2["riichi"]["var"] - 1.0) < 1e-9 \
    and abs(chk2["riichi_turn"]["var"] - 1.0) < 1e-9
print(f"  {'✓' if co_ok else '✗'} ⑥ 共存：`riichi` 动作/付奖/拦下 "
      f"{a_ri['action_rows']}/{a_ri['paid_rows']}/{a_ri['blocked_rows']}（晚立直那一行**也付奖**）；"
      f"`riichi_turn` {a_rt['action_rows']}/{a_rt['paid_rows']}/{a_rt['blocked_rows']}"
      f"（晚立直 decl_late_rows={a_rt['decl_late_rows']} ⇒ **三格全 0**）；白化 var="
      f"{chk2['riichi']['var']!r}/{chk2['riichi_turn']['var']!r}")
ok &= co_ok

# ---- ⑦ 写列那条路（`BonusTracker` = `dataset.build` 真正用的那个口）：只有"宣言行 ∧ 巡目 ≤ T ∧
#         该小局未放铳"那一行的 `style_bonus` 非 0，且金额 == w·(1−μ)/σ；审计账与上面逐项相同 ----
spec3 = sr.parse_bonus("riichi_turn=1000:no_deal")
st3, _au3 = sr.whitening_stats(spec3, sr.iter_hands(p), student_policies={STU}, quiet=True,
                               mode="decision")
tr = sr.BonusTracker(spec3, st3, own_policies={STU}, mode="decision")
tr.prebuild(p)
rows3 = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
vals3 = [tr.value(r) for r in rows3]
w3 = st3["riichi_turn"]
pay3 = 1000.0 * (1 - w3.mu) / w3.sd
tr_ok = (abs(vals3[0] - pay3) < 1e-6 and vals3[1] == 0.0 and vals3[2] == 0.0 and vals3[3] == 0.0
         and tr.dec["riichi_turn"] == {"action_rows": 2, "paid_rows": 1, "blocked_rows": 1})
print(f"  {'✓' if tr_ok else '✗'} ⑦ 写列路（`BonusTracker`）：4 行得 [{vals3[0]:.1f}, {vals3[1]:.1f}, "
      f"{vals3[2]:.1f}, {vals3[3]:.1f}]（早+未放铳 / 晚 / 早+放铳 / 没立直）；"
      f"金额 == w·(1−μ)/σ = {pay3:.1f}；逐轴账 {tr.dec['riichi_turn']}")
ok &= tr_ok

print(f"\n结论：{'PASS' if ok else 'FAIL'}")
raise SystemExit(0 if ok else 1)