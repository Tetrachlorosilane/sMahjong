# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""P2-11b · 域掩码的**逐行机械自证**（零训练）：比较两条真跑采集（基线 A0 / 混合臂 H）。

判据（口径 = `PREREGISTRATION-SHARED-TRUNK.md` §4(b)「机械自证 D 外两臂逐位相同」）：

  ① **未分岔前缀**：一场里**第一处差异之前**的每一行必须逐字节相同（决策行 + hand 行）；
     其中**学生席没有任何 `kind=="claim"` 决策行的小局（¬D 小局）**必须**完全**逐字节相同（不符 = 0）。
  ② **第一处差异必须落在学生席的一条 D 行（claim）上** —— 即分岔是"可辨状态的函数"，不是别处漂移。
     ② 失败 ⇒ 判「实现没做到同到可辨状态再分歧」，本轮作废。
  ③ 报告前缀里被改判的 D 行数。

⚠ 两处**必须归一化**（否则会把"元数据/事后回填"误判成分歧；第一版就踩了这个，见报告）：
  · `"policy":"…"` —— 学生席策略串两臂**本来就不同**（`net:A` vs `net:A!net:B@cond`），逐字节比必然不等；
    ⇒ 把该字段替换成 `"policy":""` 再比（它是**元数据**，不是决策）。
  · 行尾 `,"reward_to_go":…,"final_scores":…,"placement":…}` —— 这三项是**整场结束才回填**的，
    一场只要在**后面**的小局分岔过，它前面所有行的这三项都会变（那是"下游"，不是"决策变了"）；
    ⇒ 比较时截断到 `,"reward_to_go":` **之前**（保留 `obs` / `legal` / `chosen` / `hand_delta` 等）。

逐场流式处理（每场 ~2.5 MB），不整目录读进内存。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

DEC = re.compile(
    rb'^\{"type":"decision","game":(\d+),"hand_no":(\d+),"hand":"[^"]*","step":(\d+),'
    rb'"seat":(\d+),"policy":"(?:[^"\\]|\\.)*","kind":"([^"]*)",')
HAND = re.compile(rb'^\{"type":"hand","game":(\d+),"hand_no":(\d+),')
GAME = re.compile(rb'^\{"type":"game","game":(\d+),')
POLICY = re.compile(rb'"policy":"(?:[^"\\]|\\.)*"')
# 决策行里**事后回填**的三段：`hand_delta`/`hand_winner`/`hand_loser`/`hand_agari`（小局结束时才知道）
# 与 `reward_to_go`/`final_scores`/`placement`（整场结束时才知道）。它们对"这一行做了什么决策"是
# **未来信息**：一手只要在本行**之后**分岔过，这些字段就会变 ⇒ 必须截断掉，否则会把"下游"当"决策变了"。
TAIL = (b',"hand_delta":', b',"reward_to_go":')


def gnum(p: Path) -> int:
    s = p.stem[1:]
    return int(s) if s.isdigit() else 0


def load_lines(p: Path):
    out = p.read_bytes().split(b"\n")
    if out and out[-1] == b"":
        out.pop()
    return [ln + b"\n" for ln in out]


def classify(line: bytes):
    m = DEC.match(line)
    if m:
        return ("decision", int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(5).decode())
    m = HAND.match(line)
    if m:
        return ("hand", int(m.group(2)), None, None, None)
    if GAME.match(line):
        return ("game", None, None, None, None)
    return ("?", None, None, None, None)


def content(line: bytes, kind: str) -> bytes:
    """归一化后的"决策内容"：去掉 `policy` 元数据 + 截断事后回填字段（见 TAIL 的注释）。"""
    s = POLICY.sub(b'"policy":""', line)
    if kind == "decision":
        cut = min([j for j in (s.find(t) for t in TAIL) if j >= 0] or [-1])
        if cut >= 0:
            s = s[:cut]
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_p2-11b-domdiff.py")
    ap.add_argument("--raw-a", required=True, help="基线臂（`net:<A>`）采集目录")
    ap.add_argument("--raw-h", required=True, help="混合臂（`net:<A>!net:<B>@<cond>`）采集目录")
    ap.add_argument("--hands-a", required=True, help="基线臂的逐小局账 json（含 student_seat）")
    ap.add_argument("--out-json", default="")
    ap.add_argument("--max-games", type=int, default=0)
    a = ap.parse_args(argv)

    rawA, rawH = Path(a.raw_a), Path(a.raw_h)
    hj = json.loads(Path(a.hands_a).read_text(encoding="utf-8"))
    seat_of = {(int(h["game"]), int(h["hand_no"])): int(h["student_seat"]) for h in hj["hands"]}

    filesA = sorted((p for p in rawA.iterdir() if p.suffix == ".jsonl"), key=gnum)
    filesH = {gnum(p): p for p in rawH.iterdir() if p.suffix == ".jsonl"}
    if a.max_games:
        filesA = [p for p in filesA if gnum(p) < a.max_games]

    rep: dict = {
        "raw_a": str(rawA), "raw_h": str(rawH), "n_games_a": len(filesA),
        "games_identical": 0, "games_diverged": 0, "games_missing_in_h": 0,
        "prefix_hands": 0, "prefix_lines": 0, "prefix_mismatch_lines": 0,
        "prefix_nonD_lines": 0, "prefix_D_lines": 0,
        "nonD_hands_prefix": 0, "nonD_lines_prefix": 0, "nonD_hands_mismatch": 0,
        "nonD_first_mismatch_sample": [],
        "D_hands_prefix": 0, "D_rows_prefix": 0, "D_flip_rows_prefix": 0,
        "firstdiff_is_D": 0, "firstdiff_not_D": 0, "firstdiff_not_D_sample": [],
        "prefix_hand_missing_in_h": 0, "prefix_hand_missing_in_a": 0,
        "prefix_len_mismatch": 0, "downstream_hands": 0, "downstream_lines": 0,
        "first_diff_hand_hist": defaultdict(int),
        "first_diff_hand_of_game": {},
    }

    for fa in filesA:
        g = gnum(fa)
        fh = filesH.get(g)
        if fh is None:
            rep["games_missing_in_h"] += 1
            continue
        blkA: "OrderedDict[int, list]" = OrderedDict()
        blkH: "OrderedDict[int, list]" = OrderedDict()
        for ln in load_lines(fa):
            k = classify(ln)
            if k[0] in ("decision", "hand"):
                blkA.setdefault(k[1], []).append(ln)
        for ln in load_lines(fh):
            k = classify(ln)
            if k[0] in ("decision", "hand"):
                blkH.setdefault(k[1], []).append(ln)

        diverged = False
        first_diff = None
        for hn in sorted(set(blkA) | set(blkH)):
            if diverged:
                rep["downstream_hands"] += 1
                rep["downstream_lines"] += len(blkA.get(hn, [])) + len(blkH.get(hn, []))
                continue
            if hn not in blkH:
                rep["prefix_hand_missing_in_h"] += 1
                diverged = True
                first_diff = (hn, -1, "hand-missing-in-H")
                continue
            if hn not in blkA:
                rep["prefix_hand_missing_in_a"] += 1
                diverged = True
                first_diff = (hn, -1, "hand-missing-in-A")
                continue
            ba, bh = blkA[hn], blkH[hn]
            seat = seat_of.get((g, hn), -1)
            d_rows = []
            for i, ln in enumerate(ba):
                k = classify(ln)
                if k[0] == "decision" and k[3] == seat and k[4] == "claim":
                    d_rows.append(i)
            # ⚠ 必须先比内容、再看行数：分岔那一行**本身**就在短的那一侧之前，
            #    先判行数会把"某个 D 行改判导致后续行数变化"误报成"行数不一致"（第一版踩过）。
            fd = None
            for i in range(min(len(ba), len(bh))):
                if content(ba[i], classify(ba[i])[0]) != content(bh[i], classify(bh[i])[0]):
                    fd = i
                    break
            if fd is None and len(ba) != len(bh):
                rep["prefix_len_mismatch"] += 1
                diverged = True
                first_diff = (hn, min(len(ba), len(bh)), "line-count")
                continue
            if fd is None:
                rep["prefix_hands"] += 1
                rep["prefix_lines"] += len(ba)
                rep["prefix_nonD_lines"] += len(ba) - len(d_rows)
                rep["prefix_D_lines"] += len(d_rows)
                if not d_rows:
                    rep["nonD_hands_prefix"] += 1
                    rep["nonD_lines_prefix"] += len(ba)
                else:
                    rep["D_hands_prefix"] += 1
                    rep["D_rows_prefix"] += len(d_rows)
                continue
            # ---- 第一处差异 ----
            k = classify(ba[fd])
            is_d = (k[0] == "decision" and k[3] == seat and k[4] == "claim")
            if is_d:
                rep["firstdiff_is_D"] += 1
                rep["prefix_hands"] += 1
                rep["prefix_lines"] += fd
                pre_d = [i for i in d_rows if i < fd]
                rep["prefix_nonD_lines"] += fd - len(pre_d)
                rep["prefix_D_lines"] += len(pre_d)
                # 分岔那一行**本身**就是学生席的 D 行（fd ∈ d_rows）⇒ 这一手是 D 手，不是 ¬D 手
                rep["D_hands_prefix"] += 1
                rep["D_rows_prefix"] += len(pre_d) + 1
                rep["D_flip_rows_prefix"] += 1
            else:
                rep["firstdiff_not_D"] += 1
                if len(rep["firstdiff_not_D_sample"]) < 5:
                    rep["firstdiff_not_D_sample"].append({
                        "game": g, "hand_no": hn, "line": fd, "seat": seat,
                        "kind": k[0] if k[0] != "decision" else "decision/" + str(k[4]),
                        "a": ba[fd][:220].decode("utf-8", "replace"),
                        "h": bh[fd][:220].decode("utf-8", "replace")})
            rep["first_diff_hand_hist"][str(hn)] += 1
            diverged = True
            first_diff = (hn, fd, "ok")
        if diverged:
            rep["games_diverged"] += 1
        else:
            rep["games_identical"] += 1
        if first_diff is None:
            first_diff = (-1, -1, "identical")
        # 每场的"分岔边界"：`hand_no < 该值` 的小局**完全**逐字节相同（⇒ 逐小局账可跨臂配对）；
        # 该值那一手 = 边界手（第一次改判所在的手，账也配得上，但**手内**之后的状态已不同）；
        # `> 该值` 的小局在两臂里是**不同的牌局**（庄位/巡目/牌山切片都可能不同）⇒ ⛔ 不许配对。
        rep["first_diff_hand_of_game"][str(g)] = first_diff[0]

    rep["first_diff_hand_hist"] = dict(sorted(rep["first_diff_hand_hist"].items(),
                                              key=lambda kv: int(kv[0])))
    n = rep["nonD_hands_prefix"]
    rep["judge"] = {
        "①_¬D小局(前缀内)逐字节相同": rep["nonD_hands_mismatch"] == 0,
        "②_第一处差异都落在学生席的 D 行":
            rep["firstdiff_not_D"] == 0 and rep["prefix_len_mismatch"] == 0
            and rep["prefix_hand_missing_in_h"] == 0 and rep["prefix_hand_missing_in_a"] == 0,
        "③_前缀内 D 行改判数": rep["D_flip_rows_prefix"],
        "③_前缀内 D 行数": rep["D_rows_prefix"],
        "③_前缀内改判占比": rep["D_flip_rows_prefix"] / rep["D_rows_prefix"]
            if rep["D_rows_prefix"] else float("nan"),
        "¬D小局数(前缀内)": n,
        "¬D行数(前缀内)": rep["nonD_lines_prefix"],
        "域外行数(前缀内，= 全部非-D行)": rep["prefix_nonD_lines"],
        "前缀行数(不含分岔那一行)": rep["prefix_lines"],
        "分岔场数": rep["games_diverged"],
        "零分岔场数": rep["games_identical"],
        "分岔场里第一处差异落在 D 行的比例": rep["firstdiff_is_D"] / rep["games_diverged"]
            if rep["games_diverged"] else float("nan"),
    }
    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.out_json:
        Path(a.out_json).write_text(text, encoding="utf-8")
    print(text)
    ok = rep["judge"]["①_¬D小局(前缀内)逐字节相同"] and rep["judge"]["②_第一处差异都落在学生席的 D 行"]
    print(f"[domdiff] {'PASS' if ok else 'FAIL'}：¬D 小局不符 {rep['nonD_hands_mismatch']} 处；"
          f"第一处差异非 D 行 {rep['firstdiff_not_D']} 处；前缀行 {rep['prefix_lines']}；"
          f"分岔 {rep['games_diverged']} 场 / 零分岔 {rep['games_identical']} 场")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
