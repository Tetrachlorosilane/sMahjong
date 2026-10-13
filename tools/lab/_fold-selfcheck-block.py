# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
# ================================================================ `fold` 轴（**被威胁时的降り率**，2026-10-11）
# 判据（路线 2 核心轴，与整份改动逐条对应）：
#   ① 「被威胁」= **当行** `obs.riichi` 里有真（别家已宣言立直），現物 = `obs.events` 里该家
#      `riichi` 事件**之后**的舍牌 **＋ 宣言牌本身**（`sideways`，麻将标准口径：进过河不与荣）；
#   ② 付奖行 = 出牌 ∧ 被威胁 ∧ 打出的牌对**每一个**威胁家都是現物 ⇒ **方向单向**（押し那一侧付 0）；
#   ③ 四数自洽 `threat == fold + push + missing`，且 `fold_rows == action_rows`（独立累加）；
#   ④ 白化 mean≈0 / var≈1；⑤ 取不到威胁状态 / 現物 ⇒ **当场硬拒**（⛔ 不按 False 静默压低付奖行率）；
#   ⑥ `hand` 模式用它 ⇒ 报错（合并量不是"这一行" = 静默换轴）；⑦ 伴随量恒真 ⇒ 报错。
print("== `fold` 轴（**被威胁时的降り率** · 弱口径 = 現物）==")
eq("降り：动作前缀 = 出牌（`discard:`；鸣牌询问 / pass / riichi / win 都不是降り行）",
   dict(v4_sr.ROW_ACTION_FOLD), {"fold": "discard:"})
eq("降り：**实测**恒真闸门（付奖行必须真被伴随量拦下）",
   dict(v4_sr.ROW_PAIR_MUST_BLOCK), {"riichi_turn": ("no_deal",), "fold": ("no_deal",)})
ok("fold" in v4_sr.DECISION_ONLY_AXES and "fold" in v4_sr.ROW_ACTION_AXES,
   "降り：登记为 `decision` 专用 + 行级付奖轴",
   f"DECISION_ONLY_AXES={v4_sr.DECISION_ONLY_AXES}")
# 現物重建（**只用公开事件**）：宣言点之后的舍牌 + 宣言牌本身（`sideways`），⛔ 不含宣言**之前**的舍牌。
_fold_ev = [
    {"type": "discard", "tile": "3z", "actor": 0, "turn": 1, "sideways": False},
    {"type": "discard", "tile": "9m", "actor": 2, "turn": 2, "sideways": True},   # 宣言牌
    {"type": "riichi", "actor": 2, "turn": 2},
    {"type": "discard", "tile": "1p", "actor": 1, "turn": 2, "sideways": False},
    {"type": "discard", "tile": "5m", "actor": 2, "turn": 3, "sideways": False},
    {"type": "meld", "tile": "5z", "tiles": ["5z", "5z", "5z"], "meld_kind": "pon",
     "actor": 3, "from": 0, "turn": 6},
    {"type": "discard", "tile": "2s", "actor": 2, "turn": 4, "sideways": False},
    {"type": "discard", "tile": "0p", "actor": 2, "turn": 5, "sideways": False},  # 赤五
]
_fold_gb, _fold_decl = v4_sr.genbutsu_from_events(_fold_ev)
eq("降り：現物 = 宣言牌(9m) + 宣言后的舍牌(5m/2s/赤五⇒5p)，⛔ 不含宣言前的 3z",
   sorted(_fold_gb[2]), ["2s", "5m", "5p", "9m"])
eq("降り：宣言牌本身由 `sideways` 认定（⛔ 不猜）", _fold_decl, {2: "9m"})
_fold_obs = {"riichi": [False, False, True, False], "events": _fold_ev}
eq("降り：「被威胁」= **当行** `obs.riichi` 里有真（⛔ 不是'本小局曾经有人立直'）",
   v4_sr.threat_seats(_fold_obs), (True, (2,)))
eq("降り：打出的牌对威胁家是現物 ⇒ 付奖行；不是 ⇒ 押し（False）",
   (v4_sr.fold_row_ok(_fold_obs, "discard:9m"), v4_sr.fold_row_ok(_fold_obs, "discard:2s"),
    v4_sr.fold_row_ok(_fold_obs, "discard:7p")), (True, True, False))
eq("降り：非出牌行（pass / riichi: / ron）永远不是降り行",
   tuple(v4_sr.fold_row_ok(_fold_obs, _c) for _c in ("pass", "riichi:1m", "ron")),
   (False, False, False))
eq("降り：威胁判定按**牌种**（打出的赤五 `0p` 与現物 `5p` 同一种）",
   (v4_sr.fold_row_ok(_fold_obs, "discard:5p"), v4_sr.fold_row_ok(_fold_obs, "discard:0p")),
   (True, True))
eq("降り：无威胁（没别家立直）⇒ 不是被威胁行（threat_rows 不计）",
   (v4_sr.threat_seats({"riichi": [False] * 4, "events": _fold_ev}),
    v4_sr.fold_row_ok({"riichi": [False] * 4, "events": _fold_ev}, "discard:9m")),
   ((True, ()), False))
eq("降り：`obs.riichi` 缺字段 ⇒ `available=False` + 出牌行**硬拒**标记（⛔ 不当'没威胁'）",
   (v4_sr.threat_seats({}), v4_sr.threat_missing({"events": _fold_ev}, "discard:1m")),
   ((False, ()), True))
eq("降り：`riichi` 状态位为真但 `events` 里**没有**该家的 `riichi` 事件 ⇒ 現物重建不出来 ⇒ 硬拒标记",
   v4_sr.threat_missing({"riichi": [False, False, True, False], "events": []}, "discard:1m"), True)
try:
    v4_sr.row_is_action("fold", "discard:1m")
    ok(False, "降り：`row_is_action('fold', …)` 必须当场报错（那会把口径偷换成'随便打一张'）")
except v4_sr.BonusSpecError as _e:
    ok("fold_row_ok" in str(_e), "降り：`row_is_action('fold', …)` 当场报错 ⇒ 唯一落点是 `fold_row_ok` ✓",
       str(_e)[:70])
# ---- 合成 4 小局（学生席 = 0）：现物降り未放铳 / 现物降り放铳 / 押し未放铳 / 取不到 ⇒ 硬拒 ---------
_sr_fold = scratch("v4-style-fold")
_sr_fold_rows = [
    # (小局, chosen, 威胁席, 事件, 放铳者) —— 小局 0/1 切 5m（席 2 立直后的現物）⇒ 降り；小局 2 切 1m ⇒ 押し
    (0, "discard:5m", [False, False, True, False],
     [{"type": "discard", "tile": "9m", "actor": 2, "turn": 2, "sideways": True},
      {"type": "riichi", "actor": 2, "turn": 2},
      {"type": "discard", "tile": "5m", "actor": 2, "turn": 3, "sideways": False}], -1),
    (1, "discard:5m", [False, False, True, False],
     [{"type": "discard", "tile": "9m", "actor": 2, "turn": 2, "sideways": True},
      {"type": "riichi", "actor": 2, "turn": 2},
      {"type": "discard", "tile": "5m", "actor": 2, "turn": 3, "sideways": False}], 0),
    (2, "discard:1m", [False, False, True, False],
     [{"type": "discard", "tile": "9m", "actor": 2, "turn": 2, "sideways": True},
      {"type": "riichi", "actor": 2, "turn": 2},
      {"type": "discard", "tile": "5m", "actor": 2, "turn": 3, "sideways": False}], -1),
]
with (_sr_fold / "g0.jsonl").open("w", encoding="utf-8") as _fh:
    for _h, _c, _ri, _ev, _l in _sr_fold_rows:
        _fh.write(json.dumps({
            "type": "decision", "game": 0, "hand_no": _h, "step": 0, "seat": 0,
            "policy": _STU, "kind": "turn", "legal": ["discard:1m", "discard:5m", "discard:9m"],
            "chosen": _c, "chosen_index": 0, "hand_delta": [0, 0, 0, 0], "hand_winner": -1,
            "hand_loser": _l, "hand_agari": False,
            "obs": dict(_v4obs, v=3, seat=0, riichi=_ri, riichi_turn=[0, 0, 3, 0],
                        legal=["discard:1m", "discard:5m", "discard:9m"], events=_ev)},
            ensure_ascii=False) + "\n")
_sr_fold_spec = v4_sr.parse_bonus("fold=1000:no_deal")
_sr_fold_stats, _sr_fold_audit = v4_sr.whitening_stats(
    _sr_fold_spec, v4_sr.iter_hands(_sr_fold / "g0.jsonl"), student_policies={_STU}, quiet=True,
    mode="decision")
_fa = _sr_fold_audit.axes[0]
eq("降り：四数（被威胁行 / 降り / 押し / 取不到）= (3, 2, 1, 0)",
   (_fa["threat_rows"], _fa["fold_rows"], _fa["push_rows"], _fa["missing_genbutsu_rows"]),
   (3, 2, 1, 0))
eq("降り：四数自洽 `threat == fold + push + missing`",
   _fa["threat_rows"], _fa["fold_rows"] + _fa["push_rows"] + _fa["missing_genbutsu_rows"])
eq("降り：对账 `fold_rows == action_rows`（两处独立累加）", _fa["fold_rows"], _fa["action_rows"])
eq("降り：付奖 + 拦下 == 动作行（2 = 1 + 1；切了現物照样可能放铳 ⇒ 非恒真）",
   (_fa["paid_rows"], _fa["blocked_rows"]), (1, 1))
ok("genbutsu_src" in _fa and "events" in _fa["genbutsu_src"] and "threat_src" in _fa,
   "降り：账里带口径句（threat_src / genbutsu_src / row_rule）—— 不写下来就没法复判",
   f"{_fa.get('row_rule', '')[:40]}")
_sr_fold_chk = v4_sr.audit_whitening(_sr_fold_spec, _sr_fold_stats,
                                     v4_sr.iter_hands(_sr_fold / "g0.jsonl"),
                                     student_policies={_STU}, mode="decision")
ok(_sr_fold_chk["_all_ok"]["ok"] and abs(_sr_fold_chk["fold"]["var"] - 1.0) < 1e-9,
   "降り：白化自检通过（范围内的**行**上均值 ≈ 0、方差 == 1）",
   f"mean={_sr_fold_chk['fold']['mean']:+.2e} var={_sr_fold_chk['fold']['var']!r} n={_sr_fold_chk['fold']['n']}")
# 手级指示量（`fold_hit`）与行级判据同一把尺子 ⇒ "手级=1 ⇔ 有动作行"这条蕴含不漂移
_sr_fold_cells = [(h, s, v) for _g, h, st in v4_sr.iter_hands(_sr_fold / "g0.jsonl")
                  for s, v in v4_sr.indicator_cells(st, {_STU})]
eq("降り：手级指示量 = 该席本小局**至少有一行**被判成付奖行（小局 0/1 ⇒ 1，小局 2 ⇒ 0）",
   sorted(h for h, _s, v in _sr_fold_cells if v["fold"] > 0.5), [0, 1])
try:
    v4_sr.whitening_stats(_sr_fold_spec, v4_sr.iter_hands(_sr_fold / "g0.jsonl"),
                          student_policies={_STU}, quiet=True, mode="hand")
    ok(False, "降り：`hand` 模式用它必须报错")
except v4_sr.BonusSpecError as _e:
    ok("decision" in str(_e), "降り：`hand` 模式用它 ⇒ 当场报错（不静默换轴）✓", str(_e)[:70])
# ---- 负向硬拒：取不到威胁状态 / 現物 ⇒ 报错（⛔ 不按 False 静默压低**付奖行率**）-------------------
_sr_fold_bad = scratch("v4-style-fold-nostate")
with (_sr_fold_bad / "g0.jsonl").open("w", encoding="utf-8") as _fh:
    for _h, _ri, _ev in ((0, [False, False, True, False],
                          [{"type": "discard", "tile": "9m", "actor": 2, "turn": 2, "sideways": True},
                           {"type": "riichi", "actor": 2, "turn": 2},
                           {"type": "discard", "tile": "5m", "actor": 2, "turn": 3, "sideways": False}]),
                         (1, [False, False, True, False], [])):      # 第 2 小局：状态位说立直、事件流里没有
        _fh.write(json.dumps({
            "type": "decision", "game": 0, "hand_no": _h, "step": 0, "seat": 0,
            "policy": _STU, "kind": "turn", "legal": ["discard:1m"], "chosen": "discard:1m",
            "chosen_index": 0, "hand_delta": [0, 0, 0, 0], "hand_winner": -1, "hand_loser": -1,
            "hand_agari": False,
            "obs": dict(_v4obs, v=3, seat=0, riichi=_ri, riichi_turn=[0, 0, 3, 0], events=_ev)},
            ensure_ascii=False) + "\n")
try:
    v4_sr.whitening_stats(_sr_fold_spec, v4_sr.iter_hands(_sr_fold_bad / "g0.jsonl"),
                          student_policies={_STU}, quiet=True, mode="decision")
    ok(False, "降り：取不到威胁状态 / 現物必须当场报错")
except v4_sr.BonusSpecError as _e:
    ok("取不到威胁状态" in str(_e) and "付奖行率" in str(_e),
       "降り：取不到威胁状态 / 現物 ⇒ **硬拒**（⛔ 不静默压低付奖行率）✓", str(_e)[:90])
