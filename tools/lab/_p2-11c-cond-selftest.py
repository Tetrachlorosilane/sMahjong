# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""P2-11c · `cond` 求值器的自检（零重负载）：① 字段名与 C++ 的表**逐字对齐**；② 取值/非法值。

判据（对应 `P2-11-DOMAINS-PLAN.md` §4 的"三处一起改"）：
  · `trainer/src/policies.hpp` 的 `intField` / `boolField` 里出现的字段名，必须**全部**在
    `_p2-11c-cond.py` 的 `FIELDS` 里（否则 Python 侧会把 C++ 认得的域判成"未知字段"而炸）；
  · 反向：`FIELDS` 里**多**出来的只允许是本轮新登记的点棒/顺位字段（`score`/`rank`/`gap_above`/`lead`/`is_oya`）。
  · 未登记字段 / 缺运算 / 非法取值一律**报错**（⛔ 不当假）。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(r"C:\Users\HP\source\games\mahjong")

spec = importlib.util.spec_from_file_location("p2_11c_cond", HERE / "_p2-11c-cond.py")
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

NEW_FIELDS = {"score", "rank", "gap_above", "lead", "is_oya"}
# `kind` 在 C++ 里不走 intField/boolField（是 `parsePred` 里的字符串特例）⇒ 单独允许
STR_FIELDS = {"kind"}

fails: list[str] = []
oks: list[str] = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    (oks if cond else fails).append(f"{name}{(' — ' + detail) if detail else ''}")


# ---------- ① C++ ↔ Python 字段表对齐 ----------
src = (REPO / "trainer" / "src" / "policies.hpp").read_text(encoding="utf-8")
blk = src[src.index("inline bool intField"):src.index("inline bool isIntFieldName")]
cxx = set(re.findall(r'n == "([A-Za-z_]+)"', blk))
missing = sorted(cxx - set(C.FIELDS))
extra = sorted(set(C.FIELDS) - cxx)
ck("C++ 的字段名都在 Python FIELDS 里", not missing, f"缺 {missing}")
ck("Python 多出来的只有字符串字段 kind（新字段两侧都已登记）",
   set(extra) == STR_FIELDS, f"多出 {extra}")
ck("本轮新登记的点棒/顺位字段两侧都在",
   NEW_FIELDS <= (cxx & set(C.FIELDS)), f"缺 {sorted(NEW_FIELDS - (cxx & set(C.FIELDS)))}")
ck("C++ 字段总数 = 23（13 整 + 10 布尔）", len(cxx) == 23, f"实际 {len(cxx)}")
print(f"[cond-selftest] C++ 字段 {len(cxx)} 个：{sorted(cxx)}")
print(f"[cond-selftest] Python 新增：{extra}")

# ---------- ② 取值 ----------
ROW = {"kind": "claim", "player_draws": 5, "tiles_left": 40, "dead_wall_left": 4,
       "total_discards": 12, "kan_count": 1, "kyoku": 3, "honba": 2, "riichi_sticks": 1,
       "seat": 2, "opp_riichi": True, "self_riichi": False, "menzen": True, "furiten": False,
       "any_call": True, "haitei": False, "houtei": False, "rinshan": False, "has_drawn": True,
       "score": 18500, "rank": 3, "gap_above": -1500, "lead": -11500, "is_oya": True}
ROW2 = dict(ROW, kind="turn", player_draws=14, opp_riichi=False, score=41000, rank=1,
            gap_above=0, lead=4000, self_riichi=True, is_oya=False)

CASES = [
    ("kind=claim", True, False), ("kind=turn", False, True), ("kind!=claim", False, True),
    ("draw<6", True, False), ("draw>=12", False, True), ("draw=5", True, False),
    ("opp_riichi=1", True, False), ("opp_riichi", True, False),
    ("kind=claim&draw<6", True, False), ("kind=claim&draw>=12", False, False),
    ("kind=claim|draw>=12", True, True), ("(kind=claim|opp_riichi)&draw<6", True, False),
    ("score<20000", True, False), ("rank>=3", True, False), ("gap_above<-1000", True, False),
    ("lead>0", False, True), ("rank=1&score>30000", False, True),
    # P2-11d：新登记字段（`score`/`rank`/`gap_above`/`lead`/`is_oya`）
    ("score=18500", True, False), ("rank=3", True, False), ("gap_above=-1500", True, False),
    ("gap_above>0", False, False), ("rank=4", False, False),
    ("is_oya", True, False), ("is_oya=true", True, False), ("is_oya=false", False, True),
    ("is_oya!=true", False, True), ("rank=3&is_oya", True, False),
    ("menzen&self_riichi", False, True), ("has_drawn&draw>=12", False, True),
]
for expr, w1, w2 in CASES:
    try:
        f = C.parse_cond(expr)
        ck(f"取值 {expr}", f(ROW) == w1 and f(ROW2) == w2,
           f"row1={f(ROW)}(期望{w1}) row2={f(ROW2)}(期望{w2})")
    except Exception as e:  # noqa: BLE001
        ck(f"取值 {expr}", False, f"抛了 {type(e).__name__}: {e}")

# ---------- ③ 合法性（必须报错）----------
BAD = ["scores=1", "wins=1", "draw", "kind=foo", "", "draw<", "kind", "draw<<3",
       "opp_riichi=yes", "opp_riichi<3", "(draw<6", "draw<6)", "!draw<6", "draw<6&",
       # P2-11d：新字段的非法值 / 缺运算 / **未登记别名**
       "rank", "lead", "rank=", "rank=x", "gap_above<x", "scores<20000",
       "is_oya=maybe", "is_oya<1", "is_oya=2", "my_score=1", "my_rank=1"]
for expr in BAD:
    try:
        C.parse_cond(expr)
        ck(f"非法条件应报错 {expr!r}", False, "居然通过了")
    except Exception:  # noqa: BLE001
        ck(f"非法条件应报错 {expr!r}", True)

print(f"[cond-selftest] {len(oks)} PASS / {len(fails)} FAIL")
for f in fails:
    print(f"  ✗ {f}")
print("SELFTEST " + ("PASS" if not fails else "FAIL"))
sys.exit(0 if not fails else 1)
