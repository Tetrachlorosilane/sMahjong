# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`fold` 轴的**威胁来源分档表**（T1 / T2 / T3 / 均不属）—— 零新采集，只用已有轨迹。

⛔ 独立实现：**不 import** `style_reward` / `w-ladder.py`；只用公开字段
（`obs.riichi` / `obs.melds` / `obs.dora_indicators` / `obs.events` / `chosen` / `player_draws`）。

分档（对**学生席的每一个出牌行**，按优先级取第一个满足的档）：
  * **T1** = 至少一家处于**立直状态**（`obs.riichi` 里有真）；
  * **T2** = 无 T1，但（a）有家**副露数 ≥ 2**（手牌 ≤7 张，牌理上几乎テンパイ圏）或
    （b）有家副露里含**役牌**（三元/自风/场风）或**宝牌**（按 `obs.dora_indicators` 自推）；
  * **T3**（**推断档**，只用"冻结且与结果无关"的公开估计量）= 无 T1/T2，但至少一家"dama 可疑"：
    （i）该家巡目 ≥ 10 **且** 其最近 6 张舍牌**全为ツモ切り**；
    （ii）该家**牌河单一花色占比 ≥ 70%**（染め形）；
    （iii）该家无立直无副露 **且** 中張牌（2~8）占其舍牌 ≥ 80%。
  ⛔ 不许用别家手牌 / 牌山 / 里宝 / 本局结果标定。
"""
import json
import sys
from pathlib import Path

RAW = Path(sys.argv[1] if len(sys.argv) > 1 else r"S:\mahjong-training\raw\_fold-cal-g01")
SEATS = 4
YAKUHAI = {"1z", "2z", "3z", "4z", "5z", "6z", "7z"}
WINDS = {"1z", "2z", "3z", "4z"}


def kind(t):
    t = str(t or "")
    if not t:
        return ""
    return ("5" + t[-1]) if t[:-1] == "0" else t


def dora_from(ind):
    """宝牌指示牌 → 宝牌（数牌循环 9→1、风 4→1、三元 7→5）。"""
    t = kind(ind)
    if not t:
        return ""
    d, s = t[:-1], t[-1]
    if s in "mps":
        return ("1" + s) if d == "9" else (str(int(d) + 1) + s)
    if s == "z":
        return ("1z" if d == "4" else ("5z" if d == "7" else str(int(d) + 1) + "z"))
    return ""


def classify(obs, seat):
    """这一行落在哪一档 + 各档命中的原因（只用公开信息）。"""
    ri = obs.get("riichi") or [False] * SEATS
    melds = obs.get("melds") or [[] for _ in range(SEATS)]
    dora = {dora_from(x) for x in (obs.get("dora_indicators") or [])}
    dora.discard("")
    # ---- T1 ----
    if any(bool(ri[s]) for s in range(SEATS) if s != seat):
        return "T1", "别家立直"
    # ---- T2 ----
    for s in range(SEATS):
        if s == seat:
            continue
        ms = melds[s] or []
        if len(ms) >= 2:
            return "T2", "副露≥2"
        for m in ms:
            for t in (m.get("tiles") or []):
                tk = kind(t)
                if tk in YAKUHAI or tk in dora:
                    return "T2", "役牌/宝牌副露"
    # ---- T3（推断档）----
    for s in range(SEATS):
        if s == seat:
            continue
        if bool(ri[s]) or (melds[s] or []):
            continue                      # 立直/副露家归 T1/T2
        r = _river(obs, s)
        if not r:
            continue
        if int(r["draws"]) >= 10 and r["last6"] and all(r["last6"][-6:]):
            return "T3", "巡目≥10 且最近6张全ツモ切り"
        if r["suit_max"] >= 0.70 and r["n"] >= 6:
            return "T3", "牌河单一花色≥70%"
        if r["mid_share"] >= 0.80 and r["n"] >= 6:
            return "T3", "中張牌占≥80%"
    return "—", ""


def _river(obs, s):
    """从**公开事件**重建座位 s 的牌河统计（只到本行为止）。"""
    ev = obs.get("events") or []
    tiles, tsumo, draws = [], [], 0
    for e in ev:
        if e.get("actor") != s:
            continue
        if e.get("type") == "discard":
            tk = kind(e.get("tile"))
            if tk:
                tiles.append(tk)
                tsumo.append(bool(e.get("tsumogiri")))
        if e.get("type") == "riichi":
            pass
    for e in ev:
        if e.get("actor") == s and e.get("type") in ("discard", "riichi"):
            draws = max(draws, int(e.get("turn") or 0))
    if not tiles:
        return None
    suit = {}
    for t in tiles:
        suit[t[-1]] = suit.get(t[-1], 0) + 1
    mid = sum(1 for t in tiles if t[-1] in "mps" and t[:-1].isdigit() and 2 <= int(t[:-1]) <= 8)
    return {"n": len(tiles), "last6": tsumo, "draws": draws,
            "suit_max": (max(suit.values()) / len(tiles)), "mid_share": mid / len(tiles)}


def main():
    cnt = {"T1": 0, "T2": 0, "T3": 0, "—": 0}
    reason = {}
    fold_by = {"T1": 0, "T2": 0, "T3": 0, "—": 0}
    fold_rows = 0
    threats = {"T1": 0, "T1T2": 0, "T1T2T3": 0}
    disc_rows = 0
    dec_rows = 0
    for f in sorted(RAW.glob("g*.jsonl")):
        rows, labs = [], []
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r.get("type") == "game":
                    labs = list(r.get("policies") or [])
                elif r.get("type") == "decision":
                    rows.append(r)
        stu = next((i for i, p in enumerate(labs) if "@" in p), None)
        if stu is None:
            raise SystemExit(f"{f}: 没有学生席 {labs}")
        for r in rows:
            if int(r.get("seat", -1)) != stu:
                continue
            dec_rows += 1
            chosen = str(r.get("chosen") or "")
            if not chosen.startswith("discard:"):
                continue
            disc_rows += 1
            obs = r.get("obs") or {}
            cat, why = classify(obs, stu)
            cnt[cat] += 1
            reason[(cat, why)] = reason.get((cat, why), 0) + 1
            # 现物弱口径（T1 的实际付奖行）
            ri = obs.get("riichi") or [False] * SEATS
            others = [s for s in range(SEATS) if s != stu and bool(ri[s])]
            if others:
                threats["T1"] += 1
            if others or cat == "T2":
                threats["T1T2"] += 1
            if others or cat in ("T2", "T3"):
                threats["T1T2T3"] += 1
            if others:
                after, decl = _gen(obs.get("events") or [])
                if all(s in after for s in others):
                    tk = kind(chosen.split(":", 1)[1])
                    if tk and all(tk in after[s] or tk == decl.get(s) for s in others):
                        fold_rows += 1
                        fold_by["T1"] += 1
    n = dec_rows or 1
    out = []
    out.append(f"学生席决策行 {dec_rows}｜其中出牌行 {disc_rows}（{100*disc_rows/n:.2f}%/决策行）")
    out.append("")
    out.append("| 档 | 定义 | 出牌行数 | 占出牌行 | 占决策行 |")
    out.append("| --- | --- | ---: | ---: | ---: |")
    out.append(f"| **T1** | 至少一家立直 | {cnt['T1']} | {100*cnt['T1']/max(disc_rows,1):.2f}% | "
               f"{100*cnt['T1']/n:.3f}% |")
    out.append(f"| **T2** | 无立直，但副露≥2 或 役牌/宝牌副露 | {cnt['T2']} | "
               f"{100*cnt['T2']/max(disc_rows,1):.2f}% | {100*cnt['T2']/n:.3f}% |")
    out.append(f"| **T3** | 无 T1/T2，但至少一家「dama 可疑」（推断档） | {cnt['T3']} | "
               f"{100*cnt['T3']/max(disc_rows,1):.2f}% | {100*cnt['T3']/n:.3f}% |")
    out.append(f"| **均不属** | 无立直 / 无副露威胁 / 无 dama 嫌疑 | {cnt['—']} | "
               f"{100*cnt['—']/max(disc_rows,1):.2f}% | {100*cnt['—']/n:.3f}% |")
    out.append("")
    out.append(f"**本轮 `fold` 付奖行** {fold_rows} 行 —— 分档：T1 {fold_by['T1']}｜T2 {fold_by['T2']}"
               f"｜T3 {fold_by['T3']}｜均不属 {fold_by['—']}"
               f"（{'✓ 全在 T1（预期）' if fold_by['T1'] == fold_rows else '✗ 有落在 T1 之外的'}）")
    out.append("")
    out.append("**若把威胁集扩档 ⇒ 付奖行率（付奖行数**不变**，分母/机会集扩大的保守估）**：")
    for k, v in threats.items():
        out.append(f"  · 机会集 {k}：{v} 行（{100*v/n:.3f}%/决策行）⇒ 同上付奖行 "
                   f"{fold_rows} 行 ⇒ 付奖行率 ≤ {100*fold_rows/n:.4f}%/决策行（分母不变）"
                   f"；**降り率** 分母从 {threats['T1']} → {v} ⇒ ≈ "
                   f"{100*fold_rows/max(v,1):.3f}%")
    out.append("")
    out.append("**T2/T3 命中的具体原因（计数）**：")
    for (cat, why), c in sorted(reason.items(), key=lambda x: -x[1]):
        if cat in ("T2", "T3"):
            out.append(f"  · {cat} · {why}：{c} 行")
    print("\n".join(out))


def _gen(events):
    after, decl = {}, {}
    for e in events or []:
        a = e.get("actor")
        if not isinstance(a, int):
            continue
        t = e.get("type")
        if t == "riichi":
            after.setdefault(a, set())
        elif t == "discard":
            if a in after:
                k = kind(e.get("tile"))
                if k:
                    after[a].add(k)
            if e.get("sideways") and a not in decl:
                decl[a] = kind(e.get("tile"))
    for a, k in decl.items():
        if a in after and k:
            after[a].add(k)
    return after, decl


if __name__ == "__main__":
    raise SystemExit(main())
