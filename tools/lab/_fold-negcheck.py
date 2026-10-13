# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`fold` 轴的**硬判据 ③**（白化 mean≈0 / var≈1）与**负向硬判据**（⛔ 一律当场报错，不许静默退化）。

  ① **正例**：四数自洽（`threat == fold + push + missing`）、`fold_rows == action_rows`、
     付奖 + 拦下 == 动作行、白化 mean≈0 / var≈1；⚠ 声明的現物口径**含宣言牌**（`sideways`）；
  ② **σ=0**（这一轴在本数据集上是常数）⇒ 报错（不许 1/σ 炸、也不许悄悄少一个轴）；
  ③ **取不到威胁状态**（`obs.riichi` 缺字段）⇒ 报错（⛔ 不按"没被威胁"静默处理）；
  ④ **取不到現物**（`obs.riichi[s]` 为真但 `obs.events` 里没有该家的 `riichi` 事件）⇒ 报错；
  ⑤ `hand` 模式用 `fold` ⇒ 报错（合并量不是"这一行" = 静默换轴）；
  ⑥ 伴随量在真数据上**恒真**（`blocked_rows == 0`）⇒ 报错（`ROW_PAIR_MUST_BLOCK`）；
  ⑦ **方向单向**：押し（不安全）那一侧一行都不付（`push_rows` 可以很大、`paid_rows` 不变）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\HP\source\games\mahjong\python")
from mahjong_ml.v4 import style_reward as sr      # noqa: E402

STU = "net:x@0#0.5"
TMP = Path(r"S:\mahjong-training\w-ladder\_fold-neg")
TMP.mkdir(parents=True, exist_ok=True)


def ev_riichi(actor, decl, after):
    """造一串公开事件：`decl` = 宣言牌（sideways），`after` = 宣言后的舍牌。

    ⚠ 顺序与**真轨迹**一致：`riichi` 事件**在前**、`sideways` 宣言牌**在后**
    （奖励侧 `genbutsu_from_events` 的锚点 = `riichi` 事件下标 ⇒ 宣言牌必须落在锚点之后）。
    """
    out = []
    if decl:
        out.append({"type": "riichi", "actor": actor, "turn": 1})
        out.append({"type": "discard", "tile": decl, "actor": actor, "turn": 1, "sideways": True})
    for i, t in enumerate(after or []):
        out.append({"type": "discard", "tile": t, "actor": actor, "turn": 2 + i, "sideways": False})
    return out


def row(hand, chosen, *, events=None, riichi=None, loser=-1, seat=0, legal=None, drop_riichi=False):
    obs = {"v": 3, "seat": seat, "melds": [[], [], [], []], "player_draws": 5,
           "riichi": riichi if riichi is not None else [False] * 4,
           "riichi_turn": [0] * 4, "events": events or []}
    if drop_riichi:
        obs.pop("riichi")
    r = {"type": "decision", "game": 0, "hand_no": hand, "step": 0, "seat": seat,
         "policy": STU, "kind": "turn", "chosen": chosen, "chosen_index": 0,
         "hand_delta": [0, 0, 0, 0], "hand_winner": -1, "hand_loser": loser,
         "hand_agari": False, "obs": obs}
    if legal is not None:
        r["legal"] = list(legal)
        obs["legal"] = list(legal)
    return r


def write(name, rows):
    p = TMP / f"{name}.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def scan(p, spec_text="fold=1000:no_deal", mode="decision"):
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
        print(f"  {'✓' if good else '✗'} {name}：{str(e)[:150]}")
        return good


ok = True
print("== `fold` 硬判据 ③ + 负向硬判据 ==")

# ---- ① 正例：4 个小局；現物降り未放铳 / 現物降り放铳 / 押し未放铳 / 没威胁 -------------------------
E = ev_riichi(2, "9m", ["5m", "2s"])
p = write("ok", [
    row(0, "discard:5m", events=E, riichi=[False, False, True, False], loser=-1),   # 降り + 没放铳
    row(1, "discard:9m", events=E, riichi=[False, False, True, False], loser=0),    # 降り + 放铳
    row(2, "discard:1m", events=E, riichi=[False, False, True, False], loser=-1),   # **押し**
    row(3, "discard:1m", events=[], riichi=[False] * 4, loser=-1),                  # 没威胁
])
spec, st, au, chk = scan(p)
ax = au.axes[0]
four = (ax["threat_rows"], ax["fold_rows"], ax["push_rows"], ax["missing_genbutsu_rows"])
eq_ok = (four == (3, 2, 1, 0) and ax["action_rows"] == 2 and ax["paid_rows"] == 1
         and ax["blocked_rows"] == 1
         and ax["threat_rows"] == ax["fold_rows"] + ax["push_rows"] + ax["missing_genbutsu_rows"]
         and ax["fold_rows"] == ax["action_rows"])
print(f"  {'✓' if eq_ok else '✗'} ① 四数 (被威胁, 降り, 押し, 取不到) = {four}；动作行 "
      f"{ax['action_rows']} == 降り行 {ax['fold_rows']}；付奖 {ax['paid_rows']} + 拦下 "
      f"{ax['blocked_rows']} == 动作行 {ax['action_rows']}（切了現物照样可能放铳 ⇒ **非恒真**）")
print(f"     口径句：row_rule={ax['row_rule']!r} / genbutsu_src={ax['genbutsu_src'][:52]!r}…")
wh = chk["fold"]
w_ok = abs(wh["mean"]) <= 1e-9 and abs(wh["var"] - 1.0) <= 1e-9
print(f"  {'✓' if w_ok else '✗'} ① 白化：mean={wh['mean']:+.2e} var={wh['var']!r} n={wh['n']}"
      f"（行级总体 = 学生席决策行）")
ok &= eq_ok and w_ok

# ---- ② σ=0：整个数据集一行降り候选都没有 ⇒ 必须报错 ------------------------------------------
p = write("sigma0", [row(h, "discard:1m", events=[], riichi=[False] * 4, loser=-1) for h in (0, 1)])
ok &= expect("② σ=0（这一轴在本数据集上是常数）⇒ 报错", lambda: scan(p)[2], "σ")

# ---- ③ 取不到威胁状态（`obs.riichi` 缺字段）⇒ 报错（⛔ 不按"没被威胁"静默处理）-------------------
p = write("nostate", [row(0, "discard:5m", events=E, riichi=[False, False, True, False], loser=-1),
                      row(1, "discard:2m", events=[], riichi=[False] * 4, loser=-1),
                      # ★ 这一行才是受测点：被威胁但 `obs.riichi` 缺字段
                      row(2, "discard:1m", events=E, riichi=[False, False, True, False], loser=-1,
                          drop_riichi=True)])
ok &= expect("③ `obs.riichi` 缺字段 ⇒ 报错（⛔ 不按'没被威胁'静默处理）", lambda: scan(p)[2],
             "取不到威胁状态")

# ---- ④ 取不到現物（状态位说立直、`events` 里没有该家的 `riichi` 事件）⇒ 报错 --------------------
p = write("nogen", [row(0, "discard:5m", events=E, riichi=[False, False, True, False], loser=-1),
                    row(1, "discard:2m", events=[], riichi=[False] * 4, loser=-1),
                    # ★ 被威胁但 `events` 里没有该家的 `riichi` 事件 ⇒ 現物重建不出来
                    row(2, "discard:4m", events=[], riichi=[False, False, True, False], loser=-1)])
ok &= expect("④ 現物重建不出来（无 `riichi` 事件）⇒ 报错", lambda: scan(p)[2], "取不到威胁状态")

# ---- ⑤ `hand` 模式用 `fold` ⇒ 报错（= 静默换轴） ---------------------------------------------
p = write("handmode", [row(h, "discard:5m", events=E, riichi=[False, False, True, False], loser=-1)
                       for h in (0, 1)])
ok &= expect("⑤ `hand` 模式用 `fold` ⇒ 报错（不静默换轴）", lambda: scan(p, mode="hand"), "decision")

# ---- ⑥ 伴随量恒真（动作行 > 0 但一次都没被拦下）⇒ 报错（`ROW_PAIR_MUST_BLOCK`）-----------------
# ⚠ 必须让 μ ∈ (0,1)（不然先撞 σ=0 那道闸门，测不到这条）：再加一个没威胁的行。
p = write("allpass", [row(0, "discard:5m", events=E, riichi=[False, False, True, False], loser=-1),
                      row(1, "discard:2m", events=[], riichi=[False] * 4, loser=-1)])
ok &= expect("⑥ 伴随量在真数据上恒真（`blocked_rows == 0`）⇒ 报错", lambda: scan(p)[2], "恒真")

# ---- ⑦ 方向单向：押し那一侧一行都不付（`push_rows` 大、`paid_rows` 不变）------------------------
p = write("pushonly", [row(0, "discard:1m", events=E, riichi=[False, False, True, False], loser=-1),
                       row(1, "discard:2m", events=E, riichi=[False, False, True, False], loser=-1),
                       row(2, "discard:5m", events=E, riichi=[False, False, True, False], loser=-1),
                       # ⚠ 加一行"現物降り + 放铳"：否则 `blocked_rows == 0` 会先撞
                       #   `ROW_PAIR_MUST_BLOCK` 那道实测恒真闸门（它是对的）。
                       row(3, "discard:5m", events=E, riichi=[False, False, True, False], loser=0)])
spec7, _st7, au7, _c7 = scan(p)
a7 = au7.axes[0]
push_ok = (a7["push_rows"] == 2 and a7["fold_rows"] == 2 and a7["action_rows"] == 2
           and a7["paid_rows"] == 1 and a7["blocked_rows"] == 1)
print(f"  {'✓' if push_ok else '✗'} ⑦ **方向单向**：押し 2 行（`discard:1m`/`discard:2m`）付 **0 行**，"
      f"只有現物降り那 2 行进付奖候选（动作行 {a7['action_rows']} == fold_rows {a7['fold_rows']}；"
      f"paid {a7['paid_rows']} + blocked {a7['blocked_rows']}）")
ok &= push_ok

# ---- ⑧ 写列那条路（`BonusTracker` = `dataset.build` 真正用的那个口）：只有付奖行非 0、金额对 ------
spec8 = sr.parse_bonus("fold=1000:no_deal")
st8, _au8 = sr.whitening_stats(spec8, sr.iter_hands(p), student_policies={STU}, quiet=True,
                               mode="decision")
tr = sr.BonusTracker(spec8, st8, own_policies={STU}, mode="decision")
tr.prebuild(p)
rows8 = [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
vals8 = [tr.value(r) for r in rows8]
w8 = st8["fold"]
pay8 = 1000.0 * (1 - w8.mu) / w8.sd
tr_ok = (abs(vals8[2] - pay8) < 1e-6 and vals8[0] == 0.0 and vals8[1] == 0.0
         and tr.dec["fold"] == {"action_rows": 2, "paid_rows": 1, "blocked_rows": 1})
print(f"  {'✓' if tr_ok else '✗'} ⑧ 写列路（`BonusTracker`）：4 行得 [{vals8[0]:.1f}, {vals8[1]:.1f}, "
      f"{vals8[2]:.1f}, {vals8[3]:.1f}]（押し/押し/降り/降り+放铳）；金额 == w·(1−μ)/σ = {pay8:.1f}；"
      f"逐轴账 {tr.dec['fold']}")
ok &= tr_ok

print(f"\n结论：{'PASS' if ok else 'FAIL'}")
raise SystemExit(0 if ok else 1)
