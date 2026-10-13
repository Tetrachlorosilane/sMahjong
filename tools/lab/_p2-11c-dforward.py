# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""P2-11c · 域 D 内的**前向机械自证**（零训练；`trainer v4net`，不另写前向实现）。

域 D 由 `--cond`（Python 侧等价求值器，见 `_p2-11c-cond.py`）在**基线臂 A0 的逐行元数据**上选出。
两个读数（与候选① `_p2-11b-dforward.py` 同一把尺子）：
  ① **域覆盖** `n_D / n_rows`（分母 = A0 学生席全部决策行）
  ② **域内改判率**：把 A0 的全部 D 行喂给替换网 B ⇒ `argmax_B != A0.chosen_index` 的占比
  ③ **同状态自证**（`--rows-h`）：H 轨迹里的 `(game,hand_no,step)` + `obs_hash` 对齐到 A0 的 D 行，
     `argmax_B` 必须**逐行等于** H 实际打出（不符 **必须 0**）。
     ⚠ 这同时是"**D 集合没被判宽**"的证伪测试：判宽 ⇒ 多喂的行 H 与 A0 相同 ⇒ 不符数 > 0。
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import importlib.util  # noqa: E402


def load_cond(src: str):
    """按路径加载 `_p2-11c-cond.py`（文件名带 `-`，不能直接 import）。"""
    p = Path(__file__).resolve().parent / "_p2-11c-cond.py"
    spec = importlib.util.spec_from_file_location("p2_11c_cond", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse_cond(src)


LINE = re.compile(r"^step=(-?\d+) n=(\d+) argmax=(-?\d+) logits=(.*)$")


def gnum(p: Path) -> int:
    s = p.stem[1:]
    return int(s) if s.isdigit() else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_p2-11c-dforward.py")
    ap.add_argument("--work", required=True)
    ap.add_argument("--trainer", required=True)
    ap.add_argument("--arm", default="base", help="被前向的一侧（= 基线臂 A0）")
    ap.add_argument("--cond", required=True, help="域条件（与混合臂逐字相同）")
    ap.add_argument("--net-b", required=True, help="域内替换网 B 的权重路径")
    ap.add_argument("--rows-h", default="", help="混合臂的 rows（做同状态自证；可空）")
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--max-rows", type=int, default=0,
                    help="D 行超过它时只前向**等距抽样**这么多行（确定性；跑前写死，见计划 §3）")
    ap.add_argument("--out-json", default="")
    a = ap.parse_args(argv)

    cond = load_cond(a.cond)
    work = Path(a.work)
    rows = [json.loads(l) for l in
            (work / f"rows_{a.arm}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    src = work / "stu" / a.arm
    files = sorted((p for p in src.iterdir() if p.suffix == ".jsonl"), key=gnum)
    lines: list[str] = []
    for p in files:
        lines.extend([l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()])
    if len(lines) != len(rows):
        raise SystemExit(f"⛔ 对齐失败：stu 行 {len(lines)} vs rows {len(rows)}")

    dall = [i for i, r in enumerate(rows) if cond(r)]
    if not dall:
        raise SystemExit(f"⛔ 域为空：cond=\"{a.cond}\" 在 A0 的 {len(rows)} 行里一行都没选中")
    # ⚠ 抽样口径**跑前写死**（计划 §3）：等距、确定性、无随机源；只在"描述量"上降本，不动判据
    stride = 1
    if a.max_rows and len(dall) > a.max_rows:
        stride = math.ceil(len(dall) / a.max_rows)
    didx = dall[::stride][:a.max_rows] if a.max_rows else dall
    sampled = len(didx) != len(dall)
    outd = work / "stuD" / (a.arm + "__" + re.sub(r"[^A-Za-z0-9_]+", "_", a.cond))
    outd.mkdir(parents=True, exist_ok=True)
    for old in outd.glob("g*.jsonl"):
        old.unlink()
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
        "arm": a.arm, "cond": a.cond, "net_b": a.net_b,
        "n_rows_all": len(rows), "n_D": len(didx),
        "n_D_full": len(dall), "sampled": sampled, "stride": stride,
        "D_row_share": len(dall) / len(rows),
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

    text = json.dumps(rep, ensure_ascii=False, indent=1)
    if a.out_json:
        Path(a.out_json).write_text(text, encoding="utf-8")
    print(text)
    print(f"[dforward:{a.cond}] 域覆盖 {len(dall)}/{len(rows)} = {rep['D_row_share']*100:.2f}% · "
          f"前向 {len(didx)} 行" + (f"（等距抽样 stride={stride}）" if sampled else "（全量）")
          + f" · 域内改判 {len(flips)} 行 = {rep['flip_rate_D']*100:.3f}%"
          + (f" · 同状态自证 matched={rep['same_state']['n_D_matched']} "
             f"mismatch={rep['same_state']['mismatch']}" if a.rows_h else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
