# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`dama` 轴的**负向硬判据**（⛔ 一律当场报错，不许静默退化）：

  ① 轨迹里**没有 `legal`** ⇒ 报错（"能立而不立"没有 legal 就无定义）；
  ② 该轴上**一行付奖候选都没有**（σ=0）⇒ 报错（不许 1/σ 炸、也不许悄悄少一个轴）；
  ③ `hand` 模式用 `dama` ⇒ 报错（`hand` 的合并量不是那个行为 = 静默换轴）；
  ④ `win` + `no_deal`（付奖行蕴含伴随量）⇒ `parse_bonus` 当场报错。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Users\HP\source\games\mahjong\python")
from mahjong_ml.v4 import style_reward as sr      # noqa: E402

STU = "net:x@0#0.5"
TMP = Path(r"S:\mahjong-training\w-ladder\_dama-neg")
TMP.mkdir(parents=True, exist_ok=True)


def row(hand, legal, chosen, loser=-1, winner=-1, agari=False, seat=0):
    obs = {"v": 3, "seat": seat, "riichi": [False] * 4, "riichi_turn": [0] * 4,
           "melds": [[], [], [], []]}
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


def expect(name, fn, needle):
    try:
        fn()
        print(f"  ✗ {name}：**没有报错**（应当报错！）")
        return False
    except sr.BonusSpecError as e:
        good = needle in str(e)
        print(f"  {'✓' if good else '✗'} {name}：{str(e)[:150]}")
        return good


spec = sr.parse_bonus("dama=1000:no_deal")
ok = True
print("== `dama` 负向硬判据 ==")
# ① 没有 legal
p = write("nolegal", [row(0, None, "discard:1m"), row(0, None, "discard:2m", seat=0)])
ok &= expect("① `legal` 取不到 ⇒ 报错",
             lambda: sr.whitening_stats(spec, sr.iter_hands(p), student_policies={STU},
                                        quiet=True, mode="decision"), "legal")
# ② σ=0（legal 里从来没有 riichi ⇒ 付奖候选 0）
p = write("sigma0", [row(h, ["discard:1m"], "discard:1m") for h in (0, 1)])
ok &= expect("② σ=0（本数据集上这一轴是常数）⇒ 报错",
             lambda: sr.whitening_stats(spec, sr.iter_hands(p), student_policies={STU},
                                        quiet=True, mode="decision"), "σ")
# ③ hand 模式用 dama
p = write("handmode", [row(h, ["discard:1m", "riichi:1m"], "discard:1m") for h in (0, 1)])
ok &= expect("③ `hand` 模式用 `dama` ⇒ 报错（不静默换轴）",
             lambda: sr.whitening_stats(spec, sr.iter_hands(p), student_policies={STU},
                                        quiet=True, mode="hand"), "decision")
# ④ 退化组合
ok &= expect("④ `win=…:no_deal`（付奖行蕴含伴随量）⇒ 报错",
             lambda: sr.parse_bonus("win=1000:no_deal"), "恒真")
# ⑤ 正例：真有一条"能立而不立"的行时，四数自洽、白化 0/1
p = write("ok", [row(0, ["discard:1m", "riichi:1m"], "discard:1m", loser=-1),
                 row(1, ["discard:1m", "riichi:1m"], "discard:1m", loser=0),
                 row(2, ["discard:1m", "riichi:1m"], "riichi:1m"),
                 row(3, ["discard:1m"], "discard:1m")])
st, au = sr.whitening_stats(spec, sr.iter_hands(p), student_policies={STU}, quiet=True,
                            mode="decision")
ax = au.axes[0]
chk = sr.audit_whitening(spec, st, sr.iter_hands(p), student_policies={STU}, mode="decision")
good = (ax["legal_rows"], ax["declined_rows"], ax["paid_rows"], ax["blocked_rows"]) == (3, 2, 1, 1) \
    and ax["declined_rows"] == ax["action_rows"] \
    and chk["dama"]["ok"] and abs(chk["dama"]["var"] - 1.0) < 1e-9
print(f"  {'✓' if good else '✗'} ⑤ 正例四数 (legal,declined,paid,blocked) = "
      f"({ax['legal_rows']},{ax['declined_rows']},{ax['paid_rows']},{ax['blocked_rows']})"
      f"；declined == action_rows（{ax['action_rows']}）；白化 mean={chk['dama']['mean']:+.2e} "
      f"var={chk['dama']['var']!r}")
ok &= good
print(f"\n结论：{'PASS' if ok else 'FAIL'}")
raise SystemExit(0 if ok else 1)
