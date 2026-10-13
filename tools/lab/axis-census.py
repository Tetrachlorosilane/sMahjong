#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""风格轴**普查**（AXIS CENSUS）—— 对 `AXIS-CANDIDATES.md` 的候选轴量"四列"。

⛔ 纪律（写在最前面，改这个脚本前先读）：

 1. **独立实现**：本脚本 **⛔ 不 import `style_reward`**（也不 import `w-ladder.py`）——
    它自己按 `docs/PROTOCOL.md` §8.3/§8.4 的字段读轨迹、自己数行。理由与 `decline_rate` 那一轮相同：
    两套实现各算一套还都写进报告，是这类工具最容易犯的错；**只读轨迹的数字才有对账价值**。
    （`sys.modules` 里出现 `style_reward` 直接报错退出。）
 2. **只用该座位自己的合法观测**：`legal` / `chosen` / `obs.hand` / `obs.hand_red` / `obs.drawn` /
    `obs.melds` / `obs.discards` / `obs.dora_indicators` / `obs.riichi` / `obs.riichi_turn` /
    `obs.scores` / `obs.round` / `obs.player_draws` / `obs.total_discards`。
    ⛔ **不得**读别家手牌、牌山、里宝、别家听牌 —— 那会让训练作弊（本脚本只读轨迹里那些**公开**字段）。
 3. **口径**：只数**学生席**的行（学生 = `game` 行里策略串带 `@` 的那一席）。
    分母一律是学生席的**决策行数**（"行占比"）与**场数**（"行/场/席"）—— 每场恰好 1 个学生席。

四列（`AXIS-CANDIDATES.md` §0 的三条硬判据 + §4 的频率门槛）：

 ① 机会行数 / 占比：该轴的"有机会做这个选择"的行数（决策行）与占学生决策行的百分比；小局级的轴另给小局数。
 ② 每席每局出现次数：行数 / 学生场数（= 行/场/席）。
 ③ 可识别性：能否**唯一**由该行的 `legal` + `chosen` + 手级公开状态确定"付奖行"（是/否 + 一句话理由）。
    这一列是**设计判断**（写死在下表里），不是测量 —— 脚本只把它打出来。
 ④ 伴随量是否恒真：**实测** —— 对该轴的付奖行，逐个伴随量的真值率。
    `真值率 == 100%` ⇒ **恒真 = 废**（付奖行蕴含它与否无关，见 `AXIS-CANDIDATES.md` §0 判据 2）；
    `0% < 真值率 < 100%` ⇒ **真门**；`== 0%` ⇒ 恒假（只能当负向对照）。

用法：
    python axis-census.py --traces <dir> [--json out.json] [--md out.md]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

#: Windows 控制台缺省是 GBK ⇒ 表里的 `⇒`/`⛔` 会 `UnicodeEncodeError`（实测踩过）。
#: 显式改 stdout 为 UTF-8：否则"跑到最后打印表格时才炸"，前面的测量白跑。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                    # noqa: BLE001（老 Python 没有 reconfigure）
    pass

#: ⛔ 独立性判据（纪律 1）：本脚本**不许**在 `style_reward` 之上跑。
if any(m == "style_reward" or m.endswith(".style_reward") for m in sys.modules):
    raise SystemExit("⛔ axis-census.py 必须独立实现：检测到 style_reward 已被 import —— 拒绝运行")

SEATS = 4
MELD_PREFIXES = ("chi:", "pon:", "kan:")
RIICHI_PREFIX = "riichi:"
WIN_ACTIONS = ("ron", "tsumo")
#: 赤五在 `obs.hand` 里按**普通五**计数（`hand[4]/[13]/[22]`），`obs.hand_red` 标出哪一张是赤
#: ⇒ 牌码要按 `legal` 的口径还原成 `0m/0p/0s`（实测 `legal` 用的是 `0m`，见 `_tile_codes`）。
RED_SLOT = {4: "0m", 13: "0p", 22: "0s"}
#: 频率门槛（`AXIS-CANDIDATES.md` §4）：行占比 <1% ⇒ 不值得做成轴。
FREQ_FLOOR = 0.01


# ---------------------------------------------------------------------------
# 牌码工具（只处理轨迹里的字符串码；⛔ 不引入别处的实现）
# ---------------------------------------------------------------------------

def _base_code(i: int) -> str:
    """`obs.hand` 的下标 → 牌码（下标顺序 = 万 1-9 / 筒 1-9 / 索 1-9 / 字 1-7）。"""
    if i < 9:
        return f"{i + 1}m"
    if i < 18:
        return f"{i - 9 + 1}p"
    if i < 27:
        return f"{i - 18 + 1}s"
    return f"{i - 27 + 1}z"


def _tile_codes(obs: dict) -> list:
    """该座位**暗手**的牌码表（含刚摸到的那张；不含副露）。

    ⚠ 赤五：`obs.hand` 把它记成普通五，`legal` 里却是 `0m` —— 两处口径必须在这里统一，
    否则"手里的赤五"这件事会被静默算漏（实测：不统一的初次版本有 305/631 行对不上 `legal`）。
    """
    out, hr = [], obs.get("hand_red") or []
    for i, c in enumerate(obs.get("hand") or []):
        if i in RED_SLOT and i < len(hr) and hr[i]:
            out.append(RED_SLOT[i])
            out += [_base_code(i)] * (int(c) - 1)
        else:
            out += [_base_code(i)] * int(c)
    return out


def _norm(c: str) -> str:
    """赤五归一成普通五（比较"是不是同一张牌"时用；`0m` → `5m`）。"""
    return "5" + c[1] if c[:1] == "0" else c


def _dora_from(indicator: str) -> str:
    """宝牌指示牌 → 宝牌（字牌 7 循环；数牌 9 循环；赤五指示牌按普通五算）。"""
    c = indicator if indicator[:1] != "0" else "5" + indicator[1:]
    n, s = int(c[:-1]), c[-1]
    return f"{(n % 7) + 1}z" if s == "z" else f"{(n % 9) + 1}{s}"


def _suit(c: str) -> str:
    return c[-1]


# ---------------------------------------------------------------------------
# 逐行特征（一个学生决策行 → 一组布尔/计数）
# ---------------------------------------------------------------------------

def row_features(row: dict) -> dict:
    """把一行决策折成普查要用的特征（**只用合法观测**）。"""
    obs = row["obs"]
    legal = row.get("legal")
    if legal is None:
        legal = obs.get("legal")
    legal = [str(x) for x in (legal or [])]
    chosen = str(row["chosen"])
    seat = int(row["seat"])
    kind = row.get("kind")

    hand = _tile_codes(obs) if kind == "turn" else _tile_codes(obs)
    hand_set = {_norm(t) for t in hand}
    hi = {_suit(t): 0 for t in ("m", "p", "s", "z")}
    for t in hand:
        hi[_suit(_norm(t))] += 1

    # 公开舍牌（四家的，都是公开信息）→ 现物判定
    disc_all = {_norm(t) for s in range(SEATS) for t in (obs.get("discards") or [[], [], [], []])[s]}
    ch_tile = chosen.split(":", 1)[1] if ":" in chosen else chosen
    ch_norm = _norm(ch_tile)
    #: `chosen` 不一定是出牌（`pass`/`ron`/`tsumo`/`riichi:…`）⇒ 只有数牌/字牌码才有"花色"。
    ch_suit = (ch_norm[-1] if ch_norm and ch_norm[-1] in "mpsz" and ch_norm[:-1].isdigit()
               else None)

    others_riichi = any(bool(obs["riichi"][s]) for s in range(SEATS) if s != seat)
    others_melds = sum(len(obs["melds"][s]) for s in range(SEATS) if s != seat)

    dora_inds = obs.get("dora_indicators") or []
    dora = {_norm(_dora_from(str(d))) for d in dora_inds}
    hand_dora = sum(1 for t in hand if _norm(t) in dora)
    hand_red5 = sum(1 for t in hand if t[:1] == "0")

    # 对子数（暗手内的对子/刻子块数）
    cnt: dict = {}
    for t in hand:
        cnt[_norm(t)] = cnt.get(_norm(t), 0) + 1
    pairs = sum(1 for v in cnt.values() if v >= 2)

    f = {
        "seat": seat,
        "kind": kind,
        "chosen": chosen,
        "turn_no": int(obs.get("player_draws") or 0),
        "has_legal": bool(legal),
        # ---- 行的类别 ----
        "is_discard_row": chosen.startswith("discard:"),
        "legal_riichi": any(x.startswith(RIICHI_PREFIX) for x in legal),
        "chosen_riichi": chosen.startswith(RIICHI_PREFIX),
        "legal_meld": any(x.startswith(MELD_PREFIXES) for x in legal),
        "legal_chi": any(x.startswith("chi:") for x in legal),
        "legal_pon": any(x.startswith("pon:") for x in legal),
        "legal_kan": any(x.startswith("kan:") for x in legal),
        "chosen_meld": chosen.startswith(MELD_PREFIXES),
        "chosen_chi": chosen.startswith("chi:"),
        "chosen_pon": chosen.startswith("pon:"),
        "chosen_kan": chosen.startswith("kan:"),
        "legal_win": any(x in WIN_ACTIONS for x in legal),
        "chosen_win": chosen in WIN_ACTIONS,
        "chosen_pass": chosen == "pass",
        # ---- 威胁 / 安全 ----
        "others_riichi": others_riichi,
        "others_melds2": others_melds >= 2,
        "self_riichi": bool(obs["riichi"][seat]),
        "chosen_genbutsu": ch_norm in disc_all,
        # ---- 摸切 / 手切 ----
        "tsumogiri": bool(obs.get("drawn")) and ch_tile == str(obs.get("drawn")),
        # ---- 宝牌 / 赤五 ----
        "hand_has_dora": hand_dora > 0,
        "hand_has_red5": hand_red5 > 0,
        "chosen_is_dora": ch_norm in dora,
        "chosen_is_red5": ch_tile[:1] == "0",
        # ---- 手作り ----
        "max_suit_ge8": max(hi.values()) >= 8,
        "chosen_in_dominant": (ch_suit is not None and hi[ch_suit] == max(hi.values())) if hand else False,
        "pairs_ge2": pairs >= 2,
        "chosen_breaks_pair": cnt.get(ch_norm, 0) >= 2,
    }
    return f


#: 伴随量的**真值谓词**（付奖行上判"它恒真吗"）。口径全部来自**小局结局**（决策行上的
#: `hand_agari/hand_winner/hand_loser/hand_delta` —— 事后回填的字段，可当**判据/伴随量**，
#: ⛔ 不可当决策输入）。
COMPANIONS = {
    "no_deal": lambda r: int(r.get("hand_loser", -1)) != int(r["seat"]),
    "win": lambda r: bool(r.get("hand_agari")) and int(r.get("hand_winner", -1)) == int(r["seat"]),
    "deal": lambda r: int(r.get("hand_loser", -1)) == int(r["seat"]),
    "win_pts>0": lambda r: int((r.get("hand_delta") or [0] * 4)[int(r["seat"])]) > 0,
}


# ---------------------------------------------------------------------------
# 主扫描
# ---------------------------------------------------------------------------

def scan(tdir: Path) -> dict:
    files = sorted(tdir.glob("g*.jsonl"), key=lambda p: int(p.name[1:-6]))
    if not files:
        raise SystemExit(f"⛔ {tdir} 里没有 g*.jsonl —— 轨迹已删？（普查必须在轮换删除**之前**跑）")

    #: 计数容器：`c[key]` = 行数；`h[key]` = **小局数**（该小局至少有一行命中）；
    #: `comp[(axis, companion)] = [真值行数, 总行数]`
    c: dict = {}
    h: dict = {}
    comp: dict = {}
    ec: dict = {}            # 扩展读数（巡目均值等）
    games = 0
    hands = 0
    per_game_rows = []

    def bump(k, n=1):
        c[k] = c.get(k, 0) + n

    def bumpc(axis, extra):
        bump(f"comp::{axis}::{extra}")

    for fi, f in enumerate(files):
        labs = None
        rows = []
        hands_of_game = 0
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                t = r.get("type")
                if t == "decision":
                    rows.append(r)
                elif t == "hand":
                    hands_of_game += 1
                elif t == "game":
                    labs = list(r.get("policies") or [])
        stu = next((i for i, p in enumerate(labs or []) if "@" in p), None)
        if stu is None:
            raise SystemExit(f"⛔ {f} 的 game 行里找不到带 `@` 的学生席：{labs}")
        games += 1
        hands += hands_of_game
        hit_hand: dict = {}
        n_rows = 0
        for r in rows:
            if int(r.get("seat", -1)) != stu:
                continue
            n_rows += 1
            fe = row_features(r)
            bump("D")                              # 决策行（分母）
            if fe["kind"] == "turn":
                bump("D_turn")
            else:
                bump("D_claim")
            if fe["is_discard_row"]:
                bump("D_discard")

            # ---- 付奖行的判定，逐轴登记（`paid::<axis>`）+ 伴随量真值表 ----
            paid_axes = []
            # A 押し/降り：被立直威胁下的自家出牌行（机会行），付奖行 = 切了**非现物**
            if fe["kind"] == "turn" and fe["others_riichi"] and not fe["self_riichi"]:
                bump("A_threat_rows")
                hit_hand["A_threat"] = True
                if fe["is_discard_row"]:
                    bump("A_threat_discard")
                    if fe["chosen_genbutsu"]:
                        bump("A_fold")
                        # 诊断组（**不是付奖轴**）：A 轴的**对照**行 —— 只有把"押"与"降"的伴随量
                        # 并排放，才能判 A 的伴随量是不是**有判别力**（真门 ≠ 有判别力）。
                        paid_axes.append(("A_fold", "fold"))
                    else:
                        bump("A_push")
                        paid_axes.append(("A", "push"))
                        hit_hand["A_push"] = True
            # A′ 被鸣牌威胁（他家副露 ≥2）下的自家出牌行
            if fe["kind"] == "turn" and fe["others_melds2"] and not fe["self_riichi"]:
                bump("Ap_threat_rows")
                if fe["is_discard_row"] and not fe["chosen_genbutsu"]:
                    bump("Ap_push")
                    paid_axes.append(("Ap", "push"))
            # B 立直巡目：机会行 = `legal` 含 `riichi:*`；付奖行 = 真的宣言
            if fe["legal_riichi"]:
                bump("B_legal_riichi")
                hit_hand["B_legal"] = True
            if fe["chosen_riichi"]:
                bump("B_decl")
                paid_axes.append(("B", "decl"))
                hit_hand["B_decl"] = True
                ec.setdefault("B_turn", []).append(fe["turn_no"])
            # C/D 副露（机会行 = `legal` 里有吃/碰/杠；付奖行 = 真的鸣了）
            if fe["legal_meld"]:
                bump("C_legal_meld")
            if fe["legal_chi"]:
                bump("C_legal_chi")
            if fe["legal_pon"]:
                bump("C_legal_pon")
            if fe["legal_kan"]:
                bump("C_legal_kan")
            if fe["chosen_meld"]:
                bump("C_meld")
                paid_axes.append(("C", "meld"))
                hit_hand["C_meld"] = True
                ec.setdefault("C_turn", []).append(fe["turn_no"])
                if fe["chosen_chi"]:
                    bump("D_chi")
                    paid_axes.append(("D", "chi"))
                if fe["chosen_pon"]:
                    bump("D_pon")
                    paid_axes.append(("D", "pon"))
                if fe["chosen_kan"]:
                    bump("D_kan")
                    paid_axes.append(("D", "kan"))
            # E 见逃：`legal` 含 ron/tsumo 而这一行选了 pass
            if fe["legal_win"]:
                bump("E_legal_win")
                hit_hand["E_legal_win"] = True
            if fe["chosen_win"]:
                bump("E_win")
                paid_axes.append(("E_win", "win"))
                hit_hand["E_win"] = True
            if fe["legal_win"] and fe["chosen_pass"]:
                bump("E_pass_on_win")
                paid_axes.append(("E", "pass_on_win"))
                hit_hand["E_pass"] = True
            # F dama（能立而不立）—— 上一轮已实现的轴，这里当**对账基准**
            if fe["legal_riichi"] and not fe["chosen_riichi"]:
                bump("F_dama")
                paid_axes.append(("F", "dama"))
                hit_hand["F_dama"] = True
            # G 摸切：付奖行 = 摸切的那一行
            if fe["is_discard_row"]:
                if fe["tsumogiri"]:
                    bump("G_tsumogiri")
                    paid_axes.append(("G", "tsumogiri"))
                else:
                    bump("G_tegiri")
                    paid_axes.append(("G", "tegiri"))
            # H 宝牌 / 赤五 相关的出牌行
            if fe["is_discard_row"] and fe["hand_has_dora"]:
                bump("H_discard_has_dora")
                if fe["chosen_is_dora"]:
                    bump("H_cut_dora")
                    paid_axes.append(("H", "cut_dora"))
                else:
                    bump("H_keep_dora")
                    paid_axes.append(("H", "keep_dora"))
            if fe["is_discard_row"] and fe["hand_has_red5"]:
                bump("H_discard_has_red5")
                if fe["chosen_is_red5"]:
                    bump("H_cut_red5")
                    paid_axes.append(("Hred", "cut_red5"))
                else:
                    bump("H_keep_red5")
                    paid_axes.append(("Hred", "keep_red5"))
            if fe["is_discard_row"] and fe["chosen_is_dora"]:
                bump("H_cut_dora_any")
            # I 单色集中（主色 ≥8 张）的出牌行
            if fe["is_discard_row"] and fe["max_suit_ge8"]:
                bump("I_discard_concentrated")
                if fe["chosen_in_dominant"]:
                    bump("I_cut_dominant")
                    paid_axes.append(("I", "cut_dominant"))
                else:
                    bump("I_keep_dominant")
                    paid_axes.append(("I", "keep_dominant"))
            # J 对子（≥2 对）的出牌行
            if fe["is_discard_row"] and fe["pairs_ge2"]:
                bump("J_discard_pairs2")
                if fe["chosen_breaks_pair"]:
                    bump("J_break_pair")
                    paid_axes.append(("J", "break_pair"))
                else:
                    paid_axes.append(("J", "keep_pair"))

            # ---- 伴随量真值表（对每一组付奖行逐个伴随量） ----
            for axis, sub in paid_axes:
                ck = f"cnt::{axis}::{sub}"
                bump(ck)
                d = comp.setdefault(f"{axis}:{sub}", {})
                for cname, pred in COMPANIONS.items():
                    slot = d.setdefault(cname, [0, 0])
                    slot[1] += 1
                    try:
                        if pred(r):
                            slot[0] += 1
                    except (KeyError, IndexError, TypeError):
                        pass
        per_game_rows.append(n_rows)
        for k in hit_hand:
            h[k] = h.get(k, 0) + 1

    return {"files": len(files), "games": games, "hands": hands, "counts": c, "hand_hits": h,
            "companion": comp,
            "rows_per_game": {"mean": statistics.fmean(per_game_rows),
                              "min": min(per_game_rows), "max": max(per_game_rows)},
            "turns": {k: {"n": len(v), "mean": statistics.fmean(v),
                          "median": statistics.median(v)} for k, v in ec.items() if v},
            "traces": str(tdir)}


# ---------------------------------------------------------------------------
# 四列表：轴定义 + ③ 可识别性（设计判断，写死在这里 ⇒ 输出可复现）
# ---------------------------------------------------------------------------
#: (轴号, 名称, 机会行的计数键, 付奖行的计数键, 可识别性列, 建议的伴随量键列表)
AXES_TABLE = (
    ("A", "押し率/降り率（被**立直**威胁下的自家出牌行）", "A_threat_discard", "A_push",
     "**是**（唯一）：`legal`/`chosen` + 公开舍牌算「现物」即可落行；⚠ 只是**弱**口径 —— "
     "真正的危险度（引擎 `rules/Danger.java`）在 `obs` 里**取不到**（缺 `danger` 字段）",
     ("A:push",)),
    ("A′", "押し（被**鸣牌**威胁：他家副露 ≥2）", "Ap_threat_rows", "Ap_push",
     "**是（弱口径）**：同上，但「鸣牌威胁」本身没有权威量 ⇒ 阈值（≥2 副露）是拍的",
     ("Ap:push",)),
    ("A″", "（**对照**）被立直威胁下的**降り**行（切现物）", "A_threat_discard", "A_fold",
     "**是**：同 A，取补集（`chosen` 的牌码在**任何一家**的公开舍牌里出现过）",
     ("A_fold:fold",)),
    ("B", "立直巡目（早/晚立直）", "B_legal_riichi", "B_decl",
     "**是**：`legal` 含 `riichi:*` ∧ `chosen` 以 `riichi:` 开头；巡目取 `obs.player_draws`",
     ("B:decl",)),
    ("C", "副露巡目（先副/後副）", "C_legal_meld", "C_meld",
     "**是**：`legal` 含 `chi:/pon:/kan:` ∧ `chosen` 以它们开头（巡目同 B）",
     ("C:meld",)),
    ("D", "副露种类（チー/ポン/カン）", "C_legal_meld", "D_chi/D_pon/D_kan",
     "**是**：动作键前缀直接区分（`chi:`/`pon:`/`kan:`）—— 比 C 还便宜",
     ("D:chi", "D:pon", "D:kan")),
    ("E", "见逃率（能和却 `pass`）", "E_legal_win", "E_pass_on_win",
     "**是**：`legal` 含 `ron`/`tsumo` ∧ `chosen == pass`（⛔ `win_note` 不要用：那是**不能**和的原因）",
     ("E:pass_on_win",)),
    ("E′", "（对照）真的和了的那一行", "E_legal_win", "E_win",
     "**是**：`chosen ∈ {ron,tsumo}` —— 但它就是结果本身（`win` 轴的退化陷阱）",
     ("E_win:win",)),
    ("F", "ダマ（能立而不立）", "B_legal_riichi", "F_dama",
     "**是**：`legal` 含 `riichi:*` ∧ `chosen` **不是**它（= 上一轮已实现的轴，这里当对账基准）",
     ("F:dama",)),
    ("G", "摸切率（手牌管理）", "D_discard", "G_tsumogiri",
     "**是**：`chosen` 的牌码 == `obs.drawn`（⛔ 不要用 `discard.tsumogiri`：它只在**下一行**的 "
     "`events` 里；本行只有 `drawn` 可比）",
     ("G:tsumogiri", "G:tegiri")),
    ("H", "ドラ保持（切/留宝牌·赤五）", "H_discard_has_dora", "H_cut_dora",
     "**是**：宝牌由 `obs.dora_indicators` 自己推（字牌/数牌循环），赤五由 `hand_red` 标；"
     "⚠ 里宝指示牌 `obs` 里**没有**",
     ("H:cut_dora", "H:keep_dora", "Hred:cut_red5", "Hred:keep_red5")),
    ("I", "染め志向（主色集中）", "I_discard_concentrated", "I_cut_dominant",
     "**是（弱口径）**：主色张数 ≥8 这个阈值是拍的；「切掉主色/保留主色」由 `chosen` 的牌码判定",
     ("I:cut_dominant", "I:keep_dominant")),
    ("J", "対子志向（拆对/保对）", "J_discard_pairs2", "J_break_pair",
     "**是（弱口径）**：≥2 对是拍的阈值；「拆对」= `chosen` 的牌码在暗手里 ≥2 张",
     ("J:break_pair", "J:keep_pair")),
)


def render(res: dict) -> str:
    c, hh = res["counts"], res["hand_hits"]
    games, hands = res["games"], res["hands"]
    L = []
    L.append(f"统计口径：**{res['files']} 个轨迹分片 / {games} 场 / {hands} 个小局 / "
             f"学生席决策行 {c.get('D', 0)}**（{c.get('D_turn', 0)} 个自家回合行 + "
             f"{c.get('D_claim', 0)} 个鸣牌询问行；出牌行 {c.get('D_discard', 0)}）")
    L.append("")
    L.append(f"频率门槛（`AXIS-CANDIDATES.md` §4）：行占比 < {FREQ_FLOOR:.0%} ⇒ 不值得做成轴。")
    L.append("")
    L.append("## 四列表")
    L.append("")
    L.append("| 轴 | 机会行数 / 占比（占决策行） | 每席每局次数 | 可识别性（能否唯一落「付奖行」） | "
             "伴随量是否恒真（实测真值率） |")
    L.append("| --- | --- | --- | --- | --- |")
    for aid, name, ok, pk, ident, comps in AXES_TABLE:
        opp = c.get(ok, 0)
        pct = 100.0 * opp / max(c.get("D", 1), 1)
        per = opp / max(games, 1)
        cells = []
        for ck in comps:
            ax, sub = ck.split(":", 1)
            np_ = c.get(f"cnt::{ax}::{sub}", 0)
            d = res["companion"].get(ck) or {}
            if not d:
                cells.append(f"`{ck}`：**取不到**（付奖行 {np_}）")
                continue
            for cname in COMPANIONS:
                slot = d.get(cname)
                if not slot or slot[1] == 0:
                    continue
                f = slot[0] / slot[1]
                verdict = ("**恒真 = 废**" if f >= 0.9999 else
                           ("恒假" if f <= 0.0001 else "**真门**"))
                cells.append(f"`{ck}`×`{cname}` {f * 100:.1f}%（{slot[0]}/{slot[1]}）{verdict}")
        L.append(f"| **{aid}** {name} | **{opp}**（{pct:.2f}%） | **{per:.3f}** 行/场/席 | {ident} | "
                 f"{'<br>'.join(cells)} |")
    L.append("")
    L.append("## 机会行 / 付奖行的**原始计数**（对账用，逐个键）")
    L.append("")
    keys = sorted(k for k in c if not k.startswith("comp::"))
    L.append("| 键 | 行数 | 占决策行 | 行/场/席 |")
    L.append("| --- | ---: | ---: | ---: |")
    for k in keys:
        L.append(f"| `{k}` | {c[k]} | {100.0 * c[k] / max(c.get('D', 1), 1):.3f}% | "
                 f"{c[k] / max(games, 1):.3f} |")
    L.append("")
    L.append("## 小局级命中数（该小局至少有一行命中）")
    L.append("")
    L.append("| 键 | 小局数 | 占小局 | 小局/场 |")
    L.append("| --- | ---: | ---: | ---: |")
    for k in sorted(hh):
        L.append(f"| `{k}` | {hh[k]} | {100.0 * hh[k] / max(hands, 1):.2f}% | {hh[k] / max(games, 1):.3f} |")
    L.append("")
    L.append("## 巡目读数（`obs.player_draws`）")
    L.append("")
    L.append("| 键 | n | 均值 | 中位数 |")
    L.append("| --- | ---: | ---: | ---: |")
    for k, v in sorted(res["turns"].items()):
        L.append(f"| `{k}` | {v['n']} | {v['mean']:.2f} | {v['median']:.1f} |")
    L.append("")
    L.append("## ⛔ 取不到的量（缺什么，写在前面）")
    L.append("")
    for s in (
        "- **引擎的危险度数值**（`server/.../rules/Danger.java`）：`obs` 里**没有** `danger` 字段 ⇒ "
        "本普查只能用**现物**（公开舍牌里出现过的牌）当「安全」的弱代理。要强口径必须在轨迹侧挂引擎 "
        "`Danger`，或往 `obs` 加一列（⚠ 加字段要三处一起改，见 `AGENTS.md` §6.5）。",
        "- **别家手牌 / 听牌 / 牌山 / 里宝**：⛔ **不许取**（用了就是作弊）。⇒ 「被威胁的程度」只能由"
        "**公开**的立直宣言、副露数、舍牌、巡目代理。",
        "- **`手切/摸切` 的权威位**（`discard.tsumogiri`）：它只在**下一行**的 `obs.events` 里；"
        "本行的判据只能用 `chosen 牌码 == obs.drawn`（等价，但要知道这不是那个字段）。",
        "- **`win_note` 不能当见逃的判据**：它是「能和却**不能**和」的原因（振听/无役）；"
        "见逃的正确判据是 `legal` 含 `ron`/`tsumo` ∧ `chosen == pass`。",
        "- **宝牌指示牌之后的「宝牌张数」**：`obs.visible` 给的是**牌种可见数**（含自己手牌），"
        "要「剩余宝牌期望」得自己按公开信息推 —— 本普查没做（不当轴）。",
    ):
        L.append(s)
    L.append("")
    L.append(f"（`--traces` = `{res['traces']}`；每场学生决策行 均值 {res['rows_per_game']['mean']:.1f}、"
             f"最少 {res['rows_per_game']['min']}、最多 {res['rows_per_game']['max']}）")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axis-census.py")
    ap.add_argument("--traces", required=True, help="轨迹目录（含 g*.jsonl；⛔ 必须在轮换删除之前跑）")
    ap.add_argument("--json", default="", help="落盘 JSON（缺省不落）")
    ap.add_argument("--md", default="", help="落盘 Markdown 表（缺省不落）")
    a = ap.parse_args(argv)
    res = scan(Path(a.traces))
    md = render(res)
    print(md)
    if a.json:
        Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[census] JSON → {a.json}")
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8")
        print(f"[census] Markdown → {a.md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
