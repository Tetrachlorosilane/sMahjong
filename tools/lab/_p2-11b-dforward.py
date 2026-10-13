# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""P2-11b · 域内（`kind=="claim"`）的**前向机械自证**（零训练）。

做两件事（都只用仓库自带 `trainer.exe v4net`，不另写前向实现）：
  ① **域内改判率**：把基线臂 A0 的**全部 D 行**喂给替换网 B ⇒ `argmax_B`；
     改判行 = `argmax_B != chosen_index_A0`。这就是混合臂 H 在 D 上"真会改判"的行数与占比，
     **与 P2-11 最小判据的 13.18% 同一把尺子**（同一批行、同一张网、同一 legal）。
  ② **同状态自证**：把 H 的轨迹行按 `(game,hand_no,step) + obs_hash` 对到 A0 的 D 行上，
     `argmax_B` 必须 **逐行等于** H 实际打出的动作（0 处不符）⇒ "域内用的是 B 的 argmax"
     不是声称，是复算出来的。

输入 = `_expA-extract.py` 的产物（`rows_<arm>.jsonl` + `stu/<arm>/g*.jsonl`，README 里有对齐口径）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

LINE = re.compile(r"^step=(-?\d+) n=(\d+) argmax=(-?\d+) logits=(.*)$")


def gnum(p: Path) -> int:
    s = p.stem[1:]
    return int(s) if s.isdigit() else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_p2-11b-dforward.py")
    ap.add_argument("--work", required=True)
    ap.add_argument("--trainer", required=True)
    ap.add_argument("--arm", default="base")
    ap.add_argument("--net-b", required=True, help="域内替换网 B 的权重路径")
    ap.add_argument("--rows-h", default="", help="混合臂的 rows（做同状态自证；可空）")
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--out-json", default="")
    a = ap.parse_args(argv)

    work = Path(a.work)
    rows = [json.loads(l) for l in
            (work / f"rows_{a.arm}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    src = work / "stu" / a.arm
    files = sorted((p for p in src.iterdir() if p.suffix == ".jsonl"), key=gnum)
    lines = []
    per_game = []
    for p in files:
        ls = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
        lines.extend(ls)
        per_game.append((gnum(p), ls))
    if len(lines) != len(rows):
        raise SystemExit(f"⛔ 对齐失败：stu 行 {len(lines)} vs rows {len(rows)}")

    didx = [i for i, r in enumerate(rows) if str(r["kind"]) == "claim"]
    if not didx:
        raise SystemExit("⛔ 基线臂一行 D 都没有")
    # 逐场写 D 行（⛔ 与对齐无关：只是把要前向的行分文件）
    outd = work / "stuD" / a.arm
    outd.mkdir(parents=True, exist_ok=True)
    by_game = defaultdict(list)
    for i in didx:
        by_game[int(rows[i]["game"])].append(lines[i])
    dfiles = []
    for g in sorted(by_game):
        p = outd / f"g{g}.jsonl"
        p.write_text("".join(x if x.endswith("\n") else x + "\n" for x in by_game[g]),
                     encoding="utf-8")
        dfiles.append(p)

    am = []
    for b0 in range(0, len(dfiles), a.batch):
        batch = dfiles[b0:b0 + a.batch]
        p = subprocess.run([a.trainer, "v4net", a.net_b] + [str(x) for x in batch],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           cwd=str(Path(a.trainer).parent))
        if p.returncode != 0:
            raise SystemExit(f"⛔ v4net 失败（退出码 {p.returncode}）批次 {b0}")
        for ln in p.stdout.decode("utf-8", "replace").splitlines():
            m = LINE.match(ln)
            if m:
                am.append(int(m.group(3)))
    if len(am) != len(didx):
        raise SystemExit(f"⛔ 前向行数不齐：argmax {len(am)} vs D 行 {len(didx)}")

    flips = [j for j, i in enumerate(didx) if am[j] != int(rows[i]["chosen_index"])]
    by_band = defaultdict(lambda: [0, 0])
    for j, i in enumerate(didx):
        k = f"draw{int(rows[i]['draw_band'])}_riichi{int(rows[i]['riichi_band'])}"
        by_band[k][0] += 1
        if am[j] != int(rows[i]["chosen_index"]):
            by_band[k][1] += 1
    band = {k: {"n": v[0], "flip": v[1], "rate": v[1] / v[0]} for k, v in sorted(by_band.items())}

    rep = {
        "arm": a.arm, "net_b": a.net_b,
        "n_rows_all": len(rows), "n_D": len(didx),
        "D_row_share": len(didx) / len(rows),
        "flip_rows_D": len(flips),
        "flip_rate_D": len(flips) / len(didx),
        "band": band,
    }

    if a.rows_h:
        rh = [json.loads(l) for l in
              Path(a.rows_h).read_text(encoding="utf-8").splitlines() if l.strip()]
        keyh = {(int(r["game"]), int(r["hand_no"]), int(r["step"])): r for r in rh}
        matched = same = 0
        bad_sample = []
        for j, i in enumerate(didx):
            r = rows[i]
            k = (int(r["game"]), int(r["hand_no"]), int(r["step"]))
            o = keyh.get(k)
            if o is None or o["obs_hash"] != r["obs_hash"]:
                continue
            matched += 1
            if int(o["chosen_index"]) == am[j]:
                same += 1
            elif len(bad_sample) < 5:
                bad_sample.append({"game": k[0], "hand_no": k[1], "step": k[2],
                                   "argmax_B": am[j], "H_chosen_index": int(o["chosen_index"]),
                                   "H_chosen": o["chosen"], "A0_chosen": r["chosen"]})
        rep["same_state"] = {"n_D_matched": matched, "n_argmaxB_eq_H": same,
                             "mismatch": matched - same, "sample": bad_sample}
        rep["flip_in_same_state"] = sum(
            1 for j, i in enumerate(didx)
            if int(rows[i]["chosen_index"]) != am[j]
            and (lambda r: (int(r["game"]), int(r["hand_no"]), int(r["step"])) in keyh
                  and keyh[(int(r["game"]), int(r["hand_no"]), int(r["step"]))]["obs_hash"]
                  == r["obs_hash"])(rows[i]))

    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.out_json:
        Path(a.out_json).write_text(text, encoding="utf-8")
    print(text)
    print(f"[dforward] D 行 {len(didx)}（占全部行 {rep['D_row_share']*100:.2f}%）· "
          f"域内改判 {len(flips)} 行 = {rep['flip_rate_D']*100:.3f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
