# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`pon` 轴（副露种类 = 碰取向）的**负向脚本**（硬判据 ③）：三条硬拒 + 一条正向对照。

要证的事（⛔ 全部**由代码报错/不报错**回答，不看"看起来对"）：
  ① **伴随量恒真 ⇒ 报错**：合成一局"学生碰了、且该小局未放铳" ⇒ `blocked_rows == 0`
     ⇒ `ROW_PAIR_MUST_BLOCK` 的**实测**闸门必须当场报错（`win=w:no_deal` 的病）；
  ② **取不到鸣牌行 ⇒ 硬拒**：合成只有出牌行的轨迹 ⇒ `meld_rows == 0`（分母取不到）
     ⇒ `MELD_TYPE_ERR` 当场报错（⛔ 不静默付 0）；
  ③ **σ = 0 ⇒ 硬拒**：合成"每一行都是碰"⇒ μ=1 ⇒ σ=0 ⇒ 白化无从谈起，当场报错；
  ④ **正向对照**（必须先绿，否则上面三条就是"什么都报错"的假绿）：碰 + 未放铳 / 碰 + 放铳 /
     吃 + 未放铳 各一局 ⇒ 动作行 2、付奖 1、拦下 1、鸣牌行 3 且 **吃的那一行一分钱不付**（单向），
     手级指示量与行级判据一致（`decision_violations == 0`）。
  ⑤ 另外两条闸门：`hand` 模式下用 `pon` ⇒ 静默换轴 ⇒ 报错；`pon` 之外的轴名（`meld_type`）
     ⇒ **未登记的轴** ⇒ `parse_bonus` 报错。

机制：**合成 `HandState`**（`feed` 真吃 decision 行），⛔ 不跑采集、不碰真轨迹、确定性。
用法：`python _meldtype-negcheck.py`（退出码 0 = 全部符合预期）。
"""
import json
import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
STAGE = Path(r"S:\mahjong-training\w-ladder")
sys.path.insert(0, str(REPO / "python"))
from mahjong_ml.v4 import style_reward as sr           # noqa: E402

SPEC = "pon=5400:no_deal"
STU = "net:C:/tools/build/v4-expert-defrep-g10/net.bin@0#0.5"


def drow(hand, seat, chosen, step, *, deal_seat=-1, policy=STU):
    """一条合成的 `decision` 行（只带 `feed` 真的会读的字段；⛔ 不含隐藏信息）。"""
    delta = [0, 0, 0, 0]
    if deal_seat >= 0:
        delta[deal_seat] = -1000
        delta[(deal_seat + 1) % 4] = 1000
    return {
        "type": "decision", "game": 0, "hand_no": hand, "seat": seat, "step": step,
        "chosen": chosen, "legal": ["discard:1m", "discard:2m"],
        "obs": {"v": 3, "seat": seat, "riichi": [False] * 4, "riichi_turn": [0] * 4,
                "melds": [[], [], [], []], "events": [], "hand": [1, 2, 3],
                "player_draws": 1, "hand_red": []},
        "hand_delta": delta, "hand_agari": False, "hand_winner": -1,
        "hand_loser": deal_seat, "policy": policy,
    }


def hands_of(rows_by_hand):
    """`{hand_no: [row, …]}` → `(game, hand, HandState)` 流（走**真的** `HandState.feed`）。"""
    for hn, rows in rows_by_hand.items():
        st = sr.HandState()
        for r in rows:
            st.feed(r)
        yield (0, hn, st)


def expect_error(name, fn, needle):
    try:
        fn()
    except sr.BonusSpecError as e:
        ok = needle in str(e)
        print(f"  {name:<46} ⇒ {'✓ 按预期报错' if ok else '✗ 报错但理由不对'}"
              f"（期望含 {needle!r}）\n      原文：{str(e)[:150]}")
        return ok
    print(f"  {name:<46} ⇒ ✗ **没有报错**（这条闸门失效了）")
    return False


def main() -> int:
    spec = sr.parse_bonus(SPEC)
    bad = 0

    print("== ④ 正向对照（必须先绿）==")
    rows = {
        0: [drow(0, 0, "pon:5z", 0, deal_seat=0), drow(0, 0, "discard:9m", 1, deal_seat=0)],
        1: [drow(1, 0, "pon:3m", 2), drow(1, 0, "discard:1m", 3)],
        2: [drow(2, 0, "chi:1m2m", 4), drow(2, 0, "discard:2m", 5)],
    }
    _st, au = sr.whitening_stats(spec, hands_of(rows), student_policies={STU}, quiet=True,
                                 mode="decision")
    ax = au.axes[0]
    pairs = (("meld_rows", ax["meld_rows"], 3), ("pon_rows", ax["pon_rows"], 2),
             ("chi_rows", ax["chi_rows"], 1), ("kan_rows", ax["kan_rows"], 0),
             ("action_rows", ax["action_rows"], 2), ("paid_rows", ax["paid_rows"], 1),
             ("blocked_rows", ax["blocked_rows"], 1),
             ("n_used（= 范围内的**行**数）", ax["n_used"], 6),
             ("mu", round(ax["mu"], 6), round(2 / 6, 6)))
    print("  （下面 `n_used` 是白化总体 = 范围内**决策行**数 = 6；`mu` = 动作行/行 = 2/6）")
    for k, got, want in pairs:
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {k:<16} 实测 {got!r:>10} vs 期望 {want!r:>10} ⇒ {'✓' if ok else '✗'}")
    # 单向：吃的那一行在**行级**判据上必须是 False（一分钱不付）
    st2 = sr.HandState()
    for r in rows[2]:
        st2.feed(r)
    one_way = not sr.row_is_axis_action("pon", 0, "chi:1m2m", st2, 0)
    # 手级与行级一致（`decision_violations` 必须为空）
    viol = []
    for _g, _h, st in hands_of(rows):
        viol += sr.decision_violations(spec, st, {STU})
    ok_v = (not viol) and one_way
    bad += 0 if ok_v else 1
    print(f"  单向（吃行不付）+ 手级/行级一致（violations={len(viol)}）⇒ {'✓' if ok_v else '✗'}")

    print("\n== ① 伴随量恒真（碰且未放铳，拦下 0 行）⇒ 必须报错 ==")
    # ⚠ 必须让 μ ∈ (0,1)（否则 σ=0 那条通用闸门先炸，测不到 must-block 这一条）：
    #   一局"碰 + 未放铳"（付奖）+ 一局三行出牌（都不是动作行）⇒ μ = 1/4、拦下 0 行。
    _tru = {0: [drow(0, 0, "pon:5z", 0)],
            1: [drow(1, 0, "discard:9m", 1), drow(1, 0, "discard:8m", 2),
                drow(1, 0, "discard:7m", 3)]}
    bad += 0 if expect_error(
        "blocked_rows == 0（伴随量在付奖行上恒真）",
        lambda: sr.whitening_stats(spec, hands_of(_tru), student_policies={STU}, quiet=True,
                                   mode="decision"),
        "退化成恒真") else 1

    print("\n== ② 取不到鸣牌行（分母 = 0）⇒ 必须硬拒 ==")
    bad += 0 if expect_error(
        "meld_rows == 0（一行鸣牌都没有）",
        lambda: sr.whitening_stats(spec, hands_of({0: [drow(0, 0, "discard:9m", 0)]}),
                                   student_policies={STU}, quiet=True, mode="decision"),
        "一行鸣牌决策行都没有") else 1

    print("\n== ③ σ = 0（范围内每一行都是碰 ⇒ μ = 1）⇒ 必须硬拒 ==")
    bad += 0 if expect_error(
        "μ = 1 ⇒ σ = 0（这一轴在本数据集上是常数）",
        lambda: sr.whitening_stats(spec, hands_of({9: [drow(9, 0, "pon:5z", 0, deal_seat=0)]}),
                                   student_policies={STU}, quiet=True, mode="decision"),
        "白化 σ = 0") else 1

    print("\n== ⑤ 另两条闸门 ==")
    bad += 0 if expect_error(
        "`hand` 模式下用 `pon`（静默换轴）",
        lambda: sr.whitening_stats(spec, hands_of({0: [drow(0, 0, "pon:5z", 0, deal_seat=0)]}),
                                   student_policies={STU}, quiet=True, mode="hand"),
        "只在 `decision` 模式成立") else 1
    bad += 0 if expect_error(
        "未登记的轴名 `meld_type`",
        lambda: sr.parse_bonus("meld_type=1000:no_deal"),
        "未登记的轴") else 1

    print(f"\n[判定] 负向脚本 {'**PASS**' if bad == 0 else f'**FAIL**（{bad} 处）'}"
          f"（正向对照 + 三条硬拒 + 两条闸门）")
    (STAGE / "_meldtype-neg").mkdir(parents=True, exist_ok=True)
    (STAGE / "_meldtype-neg" / "negcheck.json").write_text(json.dumps(
        {"spec": SPEC, "bad": bad, "positive": {k: v for k, v, _w in pairs},
         "pass": bad == 0}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
