# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""P2-11c · 域掩码的**逐行机械自证**（零训练）：比较两条真跑采集（基线 A0 / 混合臂 H_D）。

判据（口径 = `PREREGISTRATION-SHARED-TRUNK.md` §4(b)）：
  ① **未分岔前缀**：一场里**第一处差异之前**的每一行必须逐字节相同（决策行 + hand 行）；
     其中**学生席没有任何 D 行的小局（¬D 小局）**必须**完全**逐字节相同（不符 = 0）。
  ② **第一处差异必须落在学生席的一条 D 行上** ⇒ 分岔是"可辨状态的函数"，不是别处漂移。
     ② 失败 ⇒ 判「实现没做到同到可辨状态再分歧」，该域作废。
  ③ **D 行数自证**：轨迹里数出来的 D 行数 == `rows_*.jsonl` 里 `cond` 选中的键数（键错位就红）。
  ④ 报告前缀里被改判的 D 行数 + `first_diff_hand_of_game`（供配对读数用）。

⚠ **小局必须按 `hand_no` 归并成一个块再比**（不是"连续行成块"）：轨迹里四家的决策行是
**按座位分段**写的（`seat0 h0..hN` → `seat1 h0..hN` → …）⇒ 同一小局的行**不连续**。
本轮第一版按"连续成块"流式化，`n_D_keys` 当场自证失败（5957 > 总键数 5305）——
**在这里流式化是错的**（会把同一个小局切成 4 块并重复计数）；成本只能整场读。

⚠ 两处**必须归一化**（否则会把"元数据/事后回填"误判成分歧）：`"policy":""`（两臂本来就不同）·
截断到 `,"reward_to_go":` / `,"hand_delta":` 之前（整场/整手结束才回填的**未来信息**）。
"""
from __future__ import annotations

import argparse
import importlib.util
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
TAIL = (b',"hand_delta":', b',"reward_to_go":')


def load_cond(src: str):
    p = Path(__file__).resolve().parent / "_p2-11c-cond.py"
    spec = importlib.util.spec_from_file_location("p2_11c_cond", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_cond(src)


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


def blocks(lines):
    """按 `hand_no` 归并（见文件头"⚠ 小局必须按 hand_no 归并"）。"""
    out: "OrderedDict[int, list]" = OrderedDict()
    for ln in lines:
        k = classify(ln)
        if k[0] in ("decision", "hand"):
            out.setdefault(k[1], []).append(ln)
    return out


def content(line: bytes, kind: str) -> bytes:
    s = POLICY.sub(b'"policy":""', line)
    if kind == "decision":
        cut = min([j for j in (s.find(t) for t in TAIL) if j >= 0] or [-1])
        if cut >= 0:
            s = s[:cut]
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_p2-11c-domdiff.py")
    ap.add_argument("--raw-a", required=True)
    ap.add_argument("--raw-h", required=True)
    ap.add_argument("--hands-a", required=True)
    ap.add_argument("--rows-base", required=True, help="基线臂 rows_*.jsonl（D 判定在这里做）")
    ap.add_argument("--cond", required=True)
    ap.add_argument("--out-json", default="")
    ap.add_argument("--max-games", type=int, default=0)
    a = ap.parse_args(argv)

    cond = load_cond(a.cond)
    rawA, rawH = Path(a.raw_a), Path(a.raw_h)
    hj = json.loads(Path(a.hands_a).read_text(encoding="utf-8"))
    seat_of = {(int(h["game"]), int(h["hand_no"])): int(h["student_seat"]) for h in hj["hands"]}
    rows = [json.loads(l) for l in
            Path(a.rows_base).read_text(encoding="utf-8").splitlines() if l.strip()]
    d_keys = {(int(r["game"]), int(r["hand_no"]), int(r["step"])) for r in rows if cond(r)}
    if not d_keys:
        raise SystemExit(f"⛔ 域为空：cond=\"{a.cond}\"")
    d_keys_by_hand = defaultdict(set)
    for g, hn, st in d_keys:
        d_keys_by_hand[(g, hn)].add(st)

    filesA = sorted((p for p in rawA.iterdir() if p.suffix == ".jsonl"), key=gnum)
    filesH = {gnum(p): p for p in rawH.iterdir() if p.suffix == ".jsonl"}
    if a.max_games:
        filesA = [p for p in filesA if gnum(p) < a.max_games]

    rep: dict = {
        "raw_a": str(rawA), "raw_h": str(rawH), "cond": a.cond, "n_games_a": len(filesA),
        "n_D_keys_from_rows": len(d_keys), "D_rows_a_total": 0, "D_rows_h_total": 0,
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
        blkA, blkH = blocks(load_lines(fa)), blocks(load_lines(fh))

        diverged = False
        first_diff = None
        for hn in sorted(set(blkA) | set(blkH)):
            ba, bh = blkA.get(hn), blkH.get(hn)
            seat = seat_of.get((g, hn), -1)
            # D 行数自证覆盖**整场**（含下游手）⇒ 计数放在"分岔后跳过"之前
            d_keys_here = d_keys_by_hand.get((g, hn), ())
            d_rows = []
            if ba is not None and seat >= 0 and d_keys_here:
                for i, ln in enumerate(ba):
                    k = classify(ln)
                    if k[0] == "decision" and k[3] == seat and k[2] in d_keys_here:
                        d_rows.append(i)
            rep["D_rows_a_total"] += len(d_rows)
            if bh is not None and seat >= 0 and d_keys_here:
                for ln in bh:
                    k = classify(ln)
                    if k[0] == "decision" and k[3] == seat and k[2] in d_keys_here:
                        rep["D_rows_h_total"] += 1
            if diverged:
                rep["downstream_hands"] += 1
                rep["downstream_lines"] += len(ba or []) + len(bh or [])
                continue
            if bh is None:
                rep["prefix_hand_missing_in_h"] += 1
                diverged = True
                first_diff = (hn, -1, "hand-missing-in-H")
                continue
            if ba is None:
                rep["prefix_hand_missing_in_a"] += 1
                diverged = True
                first_diff = (hn, -1, "hand-missing-in-A")
                continue
            # ⚠ 必须先比内容、再看行数（见 `_p2-11b-domdiff.py` 的同一条注释）。
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
            is_d = (k[0] == "decision" and k[3] == seat and k[2] in d_keys_here)
            pre_d = [i for i in d_rows if i < fd]
            rep["prefix_hands"] += 1
            rep["prefix_lines"] += fd
            rep["prefix_nonD_lines"] += fd - len(pre_d)
            rep["prefix_D_lines"] += len(pre_d)
            if is_d:
                rep["firstdiff_is_D"] += 1
                rep["D_hands_prefix"] += 1
                rep["D_rows_prefix"] += len(pre_d) + 1
                rep["D_flip_rows_prefix"] += 1
            else:
                rep["firstdiff_not_D"] += 1
                if not d_rows:
                    rep["nonD_hands_prefix"] += 1
                    rep["nonD_lines_prefix"] += fd
                    rep["nonD_hands_mismatch"] += 1
                    if len(rep["nonD_first_mismatch_sample"]) < 5:
                        rep["nonD_first_mismatch_sample"].append(
                            {"game": g, "hand_no": hn, "line": fd, "seat": seat,
                             "a": ba[fd][:220].decode("utf-8", "replace"),
                             "h": bh[fd][:220].decode("utf-8", "replace")})
                if len(rep["firstdiff_not_D_sample"]) < 5:
                    rep["firstdiff_not_D_sample"].append({
                        "game": g, "hand_no": hn, "line": fd, "seat": seat,
                        "kind": k[0] if k[0] != "decision" else "decision/" + str(k[4]),
                        "step": k[2],
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
        rep["first_diff_hand_of_game"][str(g)] = first_diff[0]

    rep["first_diff_hand_hist"] = dict(sorted(rep["first_diff_hand_hist"].items(),
                                              key=lambda kv: int(kv[0])))
    n = rep["nonD_hands_prefix"]
    rep["judge"] = {
        "①_¬D小局(前缀内)逐字节相同": rep["nonD_hands_mismatch"] == 0,
        "②_第一处差异都落在学生席的 D 行":
            rep["firstdiff_not_D"] == 0 and rep["prefix_len_mismatch"] == 0
            and rep["prefix_hand_missing_in_h"] == 0 and rep["prefix_hand_missing_in_a"] == 0,
        "③_D行数自证(轨迹里数出来 == rows 里的集合大小)":
            rep["D_rows_a_total"] == rep["n_D_keys_from_rows"],
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
    ok = (rep["judge"]["①_¬D小局(前缀内)逐字节相同"]
          and rep["judge"]["②_第一处差异都落在学生席的 D 行"]
          and rep["judge"]["③_D行数自证(轨迹里数出来 == rows 里的集合大小)"])
    print(f"[domdiff:{a.cond}] {'PASS' if ok else 'FAIL'}：¬D 小局不符 {rep['nonD_hands_mismatch']} 处；"
          f"第一处差异非 D 行 {rep['firstdiff_not_D']} 处；D 行自证 {rep['D_rows_a_total']}"
          f"/{rep['n_D_keys_from_rows']}；前缀行 {rep['prefix_lines']}；"
          f"分岔 {rep['games_diverged']} 场 / 零分岔 {rep['games_identical']} 场")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
