# -*- coding: utf-8 -*-
# [数据根可覆盖性 ② 可覆盖] 本文件不含 S:/E: 绝对路径；数据根本身由调用方/工作目录（或命令行参数）决定，可整体挪到别处，输出默认落数据根 w-ladder。
"""探针 · 逐行前向的**同状态自证 + GPU↔CPU 对拍**（口径见 plan §2；前向一律走 `tools/v4-forward-gpu.py`，⛔ 不碰 `trainer v4net`）。

两件事：
  ① **同状态自证**：把分岔行的状态张量（由既有 `blocks.assemble` 物化）喂给**本臂的学生网**，
     `argmax` 必须逐行等于轨迹里**实际打出**的 `chosen_index`（不符必须 0）。
     ⇒ "两臂在**同一份状态**下选了不同动作" 这句话由网络自己证明，而不是靠我读字符串。
  ② **GPU ↔ CPU 对拍**（`--device cuda` vs `--device cpu`，同一份实现）：判据 = `argmax` 逐行全同
     + 相对 `maxΔ ≤ 1e-4`（与工具内 `TOL_REL` 同一把尺子）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
TOOL = REPO / "tools" / "v4-forward-gpu.py"


def row_index_map(d: Path, policy: str, wanted: dict) -> dict:
    """复刻 `materialize` 的行序（`trace_files` 序 → 文件内 decision 序，按 policy 过滤）
    ⇒ `{(game,hand_no,局内第几次学生决策): 全局行号}`（只留 `wanted` 里的键）。

    ⚠ 键用**局内序号**而不是 `step`：`step` 是**每场累计**的决策计数（跨小局不清零），
    两臂若在更早的小局分岔过，同一行的 `step` 会整体平移（实测 526 个配对里有 141 个）。
    """
    files = sorted((p for p in d.iterdir() if p.is_file() and p.name.startswith("g")
                    and p.suffix == ".jsonl"),
                   key=lambda p: int(p.stem[1:]) if p.stem[1:].isdigit() else 0)
    out, k = {}, 0
    for f in files:
        per_hand: dict = {}
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("type") != "decision" or r.get("policy") != policy:
                    continue
                hn = int(r["hand_no"])
                i = per_hand.get(hn, 0)
                per_hand[hn] = i + 1
                key = (int(r["game"]), hn, i)
                if key in wanted:
                    out[key] = k
                k += 1
    return out, k


def rel_delta(ref: float, got: float) -> float:
    return abs(ref - got) / max(1.0, abs(ref))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="_probe-contrast-fwdcheck.py")
    ap.add_argument("--arm-dir", required=True)
    ap.add_argument("--arm-name", required=True)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--weights", required=True)
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--batch", type=int, default=512)
    a = ap.parse_args(argv)

    work = Path(a.work); work.mkdir(parents=True, exist_ok=True)
    pairs = [json.loads(x) for x in Path(a.pairs).read_text(encoding="utf-8").splitlines() if x.strip()]
    wanted = {(int(p["game"]), int(p["hand_no"]), int(p["i"])): int(p["a0i"] if a.arm_name == "a0" else p["a1i"])
              for p in pairs}
    d = Path(a.arm_dir)
    idx, n_total = row_index_map(d, a.policy, wanted)
    missing = sorted(set(wanted) - set(idx))
    if missing:
        raise SystemExit(f"⛔ 有 {len(missing)} 个分岔行在行序里找不到（例：{missing[:3]}）")

    outs = {}
    for dev in ("cuda", "cpu"):
        out = work / f"{a.arm_name}-{dev}.jsonl"
        cmd = [sys.executable, str(TOOL), "--weights", a.weights, "--traces", str(d),
               "--policy", a.policy, "--device", dev, "--batch", str(a.batch),
               "--out", str(out), "--format", "jsonl", "--quiet"]
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if p.returncode != 0:
            raise SystemExit(f"⛔ 前向失败（{dev}，exit={p.returncode}）：\n"
                             + p.stdout.decode('utf-8', 'replace')[-1500:])
        outs[dev] = out
        print(f"[fwd:{a.arm_name}] {dev} 完 → {out}（{out.stat().st_size/1e6:.1f} MB）")

    # 逐行流式对拍：argmax 全同 + 相对 maxΔ ≤ 1e-4（同时抽查分岔行）
    inv = {v: k for k, v in idx.items()}
    n = 0; n_argmax_same = 0; worst_rel = 0.0; worst_abs = 0.0
    bad_div = []; div_ok = 0; div_n = 0
    argmax_diff = []
    with outs["cuda"].open(encoding="utf-8") as fg, outs["cpu"].open(encoding="utf-8") as fc:
        for lg, lc in zip(fg, fc):
            if not lg.strip() or not lc.strip():
                continue
            g, c = json.loads(lg), json.loads(lc)
            if int(g["row"]) != n or int(c["row"]) != n:
                raise SystemExit(f"⛔ 行号不连续：cuda {g['row']} / cpu {c['row']} vs {n}")
            if int(g["n"]) != int(c["n"]):
                raise SystemExit(f"⛔ 第 {n} 行候选数不一致：{g['n']} vs {c['n']}")
            if int(g["argmax"]) == int(c["argmax"]):
                n_argmax_same += 1
            elif len(argmax_diff) < 10:
                # 平局级差：报前两名 logits 的间距（证明不是"换了个答案"，是舍入把并列名次翻了个面）
                def top2(v):
                    s = sorted(v, reverse=True)
                    return (float(s[0]), float(s[0] - s[1]))
                argmax_diff.append({"row": n, "argmax_cuda": int(g["argmax"]), "argmax_cpu": int(c["argmax"]),
                                    "top2_gap_cuda": top2(g["logits"])[1], "top2_gap_cpu": top2(c["logits"])[1],
                                    "lead_over_other_cuda": float(g["logits"][int(g["argmax"])] -
                                                                  g["logits"][int(c["argmax"])]),
                                    "lead_over_other_cpu": float(c["logits"][int(c["argmax"])] -
                                                                 c["logits"][int(g["argmax"])])})
            for x, y in zip(g["logits"], c["logits"]):
                worst_abs = max(worst_abs, abs(float(x) - float(y)))
                worst_rel = max(worst_rel, rel_delta(float(x), float(y)))
            if n in inv:
                div_n += 1
                want = wanted[inv[n]]
                if int(g["argmax"]) == want:
                    div_ok += 1
                elif len(bad_div) < 5:
                    bad_div.append({"row": n, "key": list(inv[n]), "argmax": int(g["argmax"]),
                                    "recorded": want, "n": int(g["n"])})
            n += 1

    rep = {"arm": a.arm_name, "policy": a.policy, "weights": a.weights,
           "n_traces_dir": str(d), "n_rows_materialized": n, "n_rows_index_mapped": n_total,
           "device_parity": {"argmax_same": n_argmax_same, "n": n,
                             "argmax_all_same": n_argmax_same == n,
                             "n_argmax_diff": n - n_argmax_same, "argmax_diff_detail": argmax_diff,
                             "tol_rel": 1e-4, "max_rel_delta": worst_rel, "max_abs_delta": worst_abs,
                             "within_tol": worst_rel <= 1e-4},
           "same_state_selfcheck": {"n_divergence_rows": div_n, "n_argmax_eq_recorded": div_ok,
                                    "mismatch": div_n - div_ok, "bad_sample": bad_div}}
    (work / f"fwdcheck-{a.arm_name}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    for dev in ("cuda", "cpu"):
        outs[dev].unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
