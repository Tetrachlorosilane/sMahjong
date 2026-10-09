#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""w-ladder.py —— **风格奖励的剂量-反应**实验驱动器（`riichi` 轴 × 三档 `w` × 每档 2 条链）。

为什么要有它（而不是接着用 `tools/style-reward-run.py`）：
  ① 上一轮是**每条臂 1 条链**（n=1/臂）⇒ 训练侧噪声（几个顺位点）盖过要测的效应。
     本实验每档 `w` 跑 **2 条独立链**（不同 seed 族）⇒ 才有"**臂间**区间"这个读数。
  ② 上一轮的审计账落在 `compact/<tag>/style-reward.json`，而 `run-league.ps1` **每代轮换删掉**
     `compact/<tag>` ⇒ 账一份没留下（只能从 `release/*.log` 里摘文字）。
     本脚本把账指到 `w-ladder/audit/<tag>.json`（不参与轮换；判据在 `style_reward.AUDIT_DIR_ENV`）。
  ③ 产物统一落在 `S:\\mahjong-training\\w-ladder\\`（预注册 §0 写的落点）。

⛔ 它**不做判决**：判决只看 `mahjong_ml/v4/gate.py`（多套新牌山 + 逐场配对 CI）。
   风格向量由 `tools/style-vector.py`（独立实现）算，本脚本只负责调度与排表。

用法（子命令）：

    python tools/w-ladder.py run     [--only v4-wl-w0-c1]   # 跑链（顺序；每条链 2 代 × 1000 场）
    python tools/w-ladder.py measure                        # 逐代风格向量 + 臂均值/臂间区间
    python tools/w-ladder.py gate    [--dry-run]            # 臂对臂闸门（最高档 × 对照，2×2 配对）
    python tools/w-ladder.py report                         # 把上面几张表拼成 Markdown
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MAHJONG_DATA_ROOT", r"S:\mahjong-training"))
STAGE = DATA / "w-ladder"
TRACES = STAGE / "traces"
AUDIT = STAGE / "audit"
LOGS = STAGE / "logs"
STYLE_VEC = STAGE / "style-vector-per-gen"
PY = r"S:\mahjong-training\tools\py312\python.exe"

#: ---- 实验的**唯一**配置表（预注册 §2 与它一一对应；改这里必须先改预注册）-------------------
#: `w=0` 那档**刻意不传** `--style-bonus`（空串 = 那个功能不存在）：这样它与上一轮的对照臂
#: `v4-sty-a` **逐字同配方** ⇒ 才能拿来做"同配方第 2/3 次跑"的噪声地板读数（预注册 §5）。
W_TOP = 2800                     # ← 校准探针实测 `w_for_parity = 2782` 就近取整到百位（预注册 §1）
W_MID = 300                      # ≈ 最高档的 10%（尺度阶梯；见预注册 §1）
WALLS = (77760001, 77760002)     # 臂对臂闸门的牌山（与上一轮终点判决同一批 ⇒ 可交叉比对）
GATE_GAMES = 3000
GATE_WORKERS = 12

ARMS = (
    {"arm": "w0", "w": 0, "bonus": "", "label": "v4-wl-w0", "seeds": (24310011, 24310012)},
    {"arm": "w300", "w": W_MID, "bonus": f"riichi={W_MID}:no_deal",
     "label": f"v4-wl-w{W_MID}", "seeds": (24310021, 24310022)},
    {"arm": "wtop", "w": W_TOP, "bonus": f"riichi={W_TOP}:no_deal",
     "label": f"v4-wl-w{W_TOP}", "seeds": (24310031, 24310032)},
)
GENERATIONS = 2
GAMES = 1000
INCUMBENT = REPO / "tools" / "build" / "v4-expert-defrep-g10" / "net.bin"

#: 上一轮的对照臂（**同配方**）—— 噪声地板的独立样本 ①（预注册 §5）。
PRIOR_SAME_RECIPE = {"label": "v4-sty-a", "traces": DATA / "style-reward" / "traces",
                     "generations": (1, 2)}
#: 上一轮的**处理臂**（`w=2`，同样只有 1 条链）—— 只作"更低剂量"的参照读数，不参与判据。
PRIOR_W2 = {"label": "v4-sty-b", "traces": DATA / "style-reward" / "traces",
            "generations": (1, 2)}

#: 风格向量的读数维（与 `tools/style-vector.py` 的 `DIMENSIONS` 同键；只读它给的 JSON）。
DIMS = ("riichi_rate", "meld_rate", "win_rate", "deal_rate", "avg_win_score", "avg_win_turn",
        "avg_riichi_turn", "dama_rate", "riichi_win_rate")


# ---------------------------------------------------------------------------
# 配置 → 链
# ---------------------------------------------------------------------------

def chains(arms=ARMS):
    """展开成链表：每条链 = `(arm 配置, 链号, label, seed)`。"""
    out = []
    for a in arms:
        for k, seed in enumerate(a["seeds"], start=1):
            out.append({"arm": a["arm"], "w": a["w"], "bonus": a["bonus"],
                        "chain": k, "label": f"{a['label']}-c{k}", "seed": seed})
    return out


def chain_by_label(label: str):
    for c in chains():
        if c["label"] == label:
            return c
    raise SystemExit(f"⛔ 配置表里没有这条链：{label}（有：{[c['label'] for c in chains()]}")


def style_pools() -> str:
    """对手池 = 各风格**最新**一代（与上一轮 §3 同一份池；`--style-pick latest` 缺省）。"""
    b = REPO / "tools" / "build"
    return ";".join(
        f"{name}=" + ",".join(str(b / f"v4-expert-{pre}-g{g:02d}" / "net.bin") for g in range(1, 11))
        for name, pre in (("def", "def7"), ("atk", "atk3"), ("win", "win2")))


# ---------------------------------------------------------------------------
# ① run：跑一条链（并在 `run-league.ps1` 轮换删掉轨迹之前**另存**一份）
# ---------------------------------------------------------------------------

#: 轮询节奏：采集是并行写多个 `g*.jsonl` 的 ⇒ 判据是"连续两次轮询形状不变"（不是"目录存在"）。
POLL = 5.0


def _traces_of(d: Path):
    return sorted(d.glob("g*.jsonl"))


def _copy_once(raw: Path, dst: Path) -> dict:
    dst.mkdir(parents=True, exist_ok=True)
    n = tot = 0
    for f in _traces_of(raw):
        t = dst / f.name
        if not (t.is_file() and t.stat().st_size == f.stat().st_size):
            shutil.copy2(f, t)
        n += 1
        tot += f.stat().st_size
    s = raw / "summary.json"
    if s.is_file():
        shutil.copy2(s, dst / "summary.json")
    return {"files": n, "bytes": tot}


def watch(label: str, generations: int, stop: threading.Event) -> list:
    rows, staged, seen = [], set(), {}
    while not stop.is_set():
        for g in range(1, generations + 1):
            tag = f"{label}-g{g:02d}"
            if tag in staged:
                continue
            raw = DATA / "raw" / tag
            if not raw.is_dir():
                continue
            files = _traces_of(raw)
            if not files:
                continue
            shape = (len(files), sum(f.stat().st_size for f in files))
            if seen.get(tag) != shape:
                seen[tag] = shape
                continue
            info = _copy_once(raw, TRACES / tag)
            row = {"tag": tag, "generation": g, "files": info["files"], "bytes": info["bytes"],
                   "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "copy_matches": len(_traces_of(TRACES / tag)) == info["files"]}
            rows.append(row)
            staged.add(tag)
            print(f"[stage] {tag}：{info['files']} 个轨迹 / {info['bytes'] / 2**20:.0f} MB "
                  f"→ {TRACES / tag}（一致={row['copy_matches']}）", flush=True)
        stop.wait(POLL)
    return rows


def run(only: list[str], dry: bool) -> int:
    STAGE.mkdir(parents=True, exist_ok=True)
    AUDIT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    todo = [c for c in chains() if not only or c["label"] in only]
    if only:
        missing = [x for x in only if x not in [c["label"] for c in chains()]]
        if missing:
            raise SystemExit(f"⛔ --only 里有配置表没有的链：{missing}")
    print(f"[cfg] 最高档 w={W_TOP} / 中间档 w={W_MID} / 对照 w=0（不传 --style-bonus）")
    for c in todo:
        cmd = ["pwsh", "-NoProfile", "-File", str(REPO / "tools" / "run-league.ps1"),
               "-Label", c["label"], "-Seed", str(c["seed"]),
               "-Generations", str(GENERATIONS), "-Games", str(GAMES),
               "-NoGate", "-Style", "def", "-StylePools", style_pools(),
               "-Incumbent", str(INCUMBENT)]
        env = dict(os.environ)
        env["MAHJONG_DATA_ROOT"] = str(DATA)
        env["MAHJONG_TRAINER"] = str(DATA / "tools" / "trainer" / "trainer.exe")
        env["PYTHONPATH"] = f"{REPO / 'python'};{REPO / 'python' / '.venv' / 'Lib' / 'site-packages'}"
        env["PYTHONIOENCODING"] = "utf-8"
        env["MAHJONG_STYLE_BONUS"] = c["bonus"]
        env["MAHJONG_STYLE_WHITEN"] = "student"
        # ★ 审计账的**持久**落点（`compact/<tag>` 会被 run-league 每代轮换删掉）。
        env["MAHJONG_STYLE_AUDIT_DIR"] = str(AUDIT)
        log = LOGS / f"{c['label']}.log"
        print(f"\n{'=' * 74}\n[chain] {c['label']}（臂 {c['arm']}，w={c['w']}，seed={c['seed']}，"
              f"{GENERATIONS} 代）bonus={c['bonus']!r}\n[chain] 日志 {log}", flush=True)
        if dry:
            print("[dry] " + " ".join(cmd))
            continue
        stop = threading.Event()
        box: list = []
        th = threading.Thread(target=lambda: box.extend(watch(c["label"], GENERATIONS, stop)),
                              daemon=True)
        th.start()
        t0 = time.perf_counter()
        with log.open("w", encoding="utf-8", errors="replace") as fh:
            rc = subprocess.run(cmd, cwd=str(REPO), env=env, stdout=fh,
                                stderr=subprocess.STDOUT).returncode
        dt = time.perf_counter() - t0
        time.sleep(2 * POLL)
        stop.set()
        th.join(timeout=30)
        man = {"chain": c, "returncode": rc, "seconds": round(dt, 1), "log": str(log),
               "staged": box, "generations": GENERATIONS, "games": GAMES,
               "incumbent": str(INCUMBENT), "audit_dir": str(AUDIT)}
        (STAGE / f"{c['label']}-manifest.json").write_text(
            json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[chain] {c['label']} 退出码 {rc}，用时 {dt / 60:.1f} 分钟，另存 {len(box)} 代轨迹",
              flush=True)
        if rc != 0:
            print(f"⛔ {c['label']} 失败 —— 看 {log} 尾部；**停止**后续链（保留现场）", file=sys.stderr)
            for ln in log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]:
                print("   " + ln, file=sys.stderr)
            return rc
    return 0


# ---------------------------------------------------------------------------
# ② measure：逐代风格向量（`style-vector.py`）+ 臂均值 ± 臂间区间
# ---------------------------------------------------------------------------

def style_vector(tdir: Path, tag: str) -> dict:
    """跑判据侧的独立实现 `tools/style-vector.py`（**只解析它的 JSON**，不自己算风格量）。"""
    STYLE_VEC.mkdir(parents=True, exist_ok=True)
    out = STYLE_VEC / f"{tag}.json"
    p = subprocess.run([PY, str(REPO / "tools" / "style-vector.py"), str(tdir),
                        "--json", str(out)],
                       cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"⛔ style-vector 失败（{tag}，退出码 {p.returncode}）：\n"
                         f"{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    (STYLE_VEC / f"{tag}.md").write_text(p.stdout, encoding="utf-8")
    return json.loads(out.read_text(encoding="utf-8"))


def student_row(sv: dict) -> dict:
    """从 style-vector 的 `policies` 里取**学生**那一行。

    判据：风格桌上学生串是 `net:<路径>@0#0.5`（带 `@`），对手串是 `net:<路径>`（不带）
    —— 与 `style-reward-report.py` 同一条（⛔ 不能用 `startswith("net:")`，那会混进三个对手）。
    """
    for name, st in (sv.get("policies") or {}).items():
        if "@" in name:
            return {"policy": name, **{k: st["dims"].get(k) for k in DIMS},
                    "seat_hands": st["counts"].get("seat_hands")}
    return {}


def collect_row(tdir: Path) -> dict:
    """从**采集**的 `summary.json` 读学生那一行的强度读数 + **逐场**标准误。

    ⚠ 为什么另给标准误：这张表是 1000 场采集的读数，`rank_points` 的逐场方差很大 ⇒
    不给 SE 就会把"±1 点"的噪声当成"差 3 点"。逐场值在 `per_game[].rank_points[seat]` 里，
    学生那一席由 `policies[]` 里带 `@` 的那个下标决定（座位每场轮转）。
    """
    f = tdir / "summary.json"
    if not f.is_file():
        return {}
    s = json.loads(f.read_text(encoding="utf-8"))
    by = s.get("by_policy") or {}
    key = next((k for k in by if "@" in k), None)
    if key is None:
        return {}
    st = by[key]
    rp, wins, deals, wpts = [], [], [], []
    for pg in s.get("per_game") or []:
        try:
            i = pg["policies"].index(key)
        except (KeyError, ValueError):
            continue
        rp.append(float(pg["rank_points"][i]))
        wins.append(float(pg["wins"][i]))
        deals.append(float(pg["deals"][i]))
        wpts.append(float(pg["win_points"][i]))
    def mean_se(x):
        if len(x) < 2:
            return (float("nan"), float("nan"))
        return (statistics.fmean(x), statistics.stdev(x) / len(x) ** 0.5)
    m_rp, se_rp = mean_se(rp)
    m_w, se_w = mean_se(wins)
    m_d, se_d = mean_se(deals)
    m_p, se_p = mean_se(wpts)
    return {"policy": key, "games": st.get("games"), "seat_hands": st.get("seat_hands"),
            "rank_points": m_rp, "rank_points_se": se_rp,
            "avg_rank_points_reported": st.get("avg_rank_points"),
            "wins_per_game": m_w, "wins_per_game_se": se_w,
            "deals_per_game": m_d, "deals_per_game_se": se_d,
            "win_points_per_game": m_p, "win_points_per_game_se": se_p,
            "win_rate": st.get("win_rate"), "deal_in_rate": st.get("deal_in_rate"),
            "avg_win_score": st.get("avg_win_score"), "avg_place": st.get("avg_place"),
            "avg_delta": st.get("avg_delta"), "ryukyoku_rate": s.get("ryukyoku_rate")}


def read_audit(tag: str) -> dict:
    """持久审计账（`w-ladder/audit/<tag>.json`）—— 记录 w/μ/σ/付奖小局数/付奖总额。"""
    f = AUDIT / f"{tag}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


def _prior_rows(spec: dict, arm: str) -> list:
    out = []
    for g in spec["generations"]:
        tag = f"{spec['label']}-g{g:02d}"
        tdir = spec["traces"] / tag
        if not tdir.is_dir():
            continue
        sv = style_vector(tdir, tag)
        out.append({"arm": arm, "w": None, "chain": 0, "label": spec["label"], "tag": tag,
                    "generation": g, "traces": str(tdir),
                    "style": student_row(sv), "collect": collect_row(tdir)})
    return out


def noise_floor(rows: list, prior: list) -> dict:
    """**同配方的多次跑**之间的散布 = 训练噪声地板（预注册 §5）。

    样本 = 上一轮的对照臂 `v4-sty-a`（1 条链）+ 本轮 `w=0` 的 2 条链（同配方、不同 seed）
    ⇒ 每个 `w` 档要测的效应必须**大于**这个散布才有意义。
    ⚠ 只报"极差 + 逐场 SE"，**不做检验**（n=3 条链，做检验是给自己戴高帽）。
    """
    same = [r for r in rows if r["arm"] == "w0"] + prior
    out: dict = {"n_chains": len(same), "labels": sorted({r["label"] for r in same})}
    for g in range(1, GENERATIONS + 1):
        sel = [r for r in same if r["generation"] == g]
        if len(sel) < 2:
            continue
        ent: dict = {"n": len(sel), "runs": [r["label"] for r in sel]}
        for k in DIMS:
            v = [r["style"].get(k) for r in sel if r["style"].get(k) is not None]
            if len(v) > 1:
                ent[k] = {"values": v, "min": min(v), "max": max(v), "range": max(v) - min(v)}
        for k in ("rank_points", "deals_per_game", "wins_per_game"):
            v = [r["collect"].get(k) for r in sel if r["collect"].get(k) is not None]
            se = [r["collect"].get(k + "_se") for r in sel if r["collect"].get(k) is not None]
            if len(v) > 1:
                ent["collect_" + k] = {"values": v, "min": min(v), "max": max(v),
                                       "range": max(v) - min(v), "se": se}
        out[f"g{g:02d}"] = ent
    return out


def measure() -> dict:
    rows = []
    for c in chains():
        for g in range(1, GENERATIONS + 1):
            tag = f"{c['label']}-g{g:02d}"
            tdir = TRACES / tag
            if not tdir.is_dir():
                print(f"⚠ 缺轨迹 {tdir} —— 跳过（链没跑完？）")
                continue
            sv = style_vector(tdir, tag)
            row = {"arm": c["arm"], "w": c["w"], "chain": c["chain"], "label": c["label"],
                   "tag": tag, "generation": g, "traces": str(tdir),
                   "style": student_row(sv), "collect": collect_row(tdir),
                   "audit": read_audit(tag)}
            rows.append(row)
            print(f"[measure] {tag} 立直率 {row['style'].get('riichi_rate')} "
                  f"副露率 {row['style'].get('meld_rate')}", flush=True)
    prior = _prior_rows(PRIOR_SAME_RECIPE, "prior-a")
    prior_w2 = _prior_rows(PRIOR_W2, "prior-b")
    # 臂表：每个 (臂, 代) 的均值 ± **臂间**极差（2 条链）
    arms: dict = {}
    for a in ARMS:
        for g in range(1, GENERATIONS + 1):
            sel = [r for r in rows if r["arm"] == a["arm"] and r["generation"] == g]
            if not sel:
                continue
            ent: dict = {"n_chains": len(sel), "w": a["w"], "generation": g, "chains": {}}
            for k in DIMS:
                v = [r["style"].get(k) for r in sel if r["style"].get(k) is not None]
                if not v:
                    continue
                ent[k] = {"mean": statistics.fmean(v), "min": min(v), "max": max(v),
                          "spread": max(v) - min(v)}
                ent["chains"][k] = v
            for k in ("rank_points", "wins_per_game", "deals_per_game", "win_points_per_game"):
                pair = [(r["collect"].get(k), r["collect"].get(k + "_se"))
                        for r in sel if r["collect"].get(k) is not None]
                if pair:
                    ent["collect_" + k] = {
                        "mean": statistics.fmean([x for x, _ in pair]),
                        "min": min(x for x, _ in pair), "max": max(x for x, _ in pair),
                        "se": [s for _, s in pair]}
            arms.setdefault(a["arm"], {})[g] = ent
    out = {"tool": "tools/w-ladder.py measure",
           "warning": "⛔ 不是判据：判决只看 mahjong_ml/v4/gate.py。这里是描述性方向读数。",
           "config": {"w_top": W_TOP, "w_mid": W_MID, "generations": GENERATIONS,
                      "games": GAMES, "incumbent": str(INCUMBENT), "walls": list(WALLS)},
           "rows": rows, "prior_same_recipe": prior, "prior_w2": prior_w2,
           "noise_floor": noise_floor(rows, prior), "arms": arms}
    p = STAGE / "style-ladder.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[measure] 落盘 {p}（{len(rows)} 条链·代 + 上一轮 {len(prior)} 代）")
    return out


# ---------------------------------------------------------------------------
# ③ gate：**臂对臂**闸门（不受"现任漂移"混杂的问法）
# ---------------------------------------------------------------------------

def _clean_jsonl(tag: str) -> int:
    """删掉这一 tag 下的 `*.jsonl`（**保留 summary.json / verdict.json** 以便复判）。"""
    root = DATA / "gate" / tag
    if not root.is_dir():
        return 0
    n = 0
    for f in root.rglob("*.jsonl"):
        f.unlink()
        n += 1
    return n


def gate(dry: bool) -> int:
    top = [c for c in chains() if c["arm"] == "wtop"]
    ctl = [c for c in chains() if c["arm"] == "w0"]
    out: list = []
    for ctlc in ctl:
        for topc in top:
            inc = REPO / "tools" / "build" / ctlc["label"] / "net.bin"
            cand = REPO / "tools" / "build" / topc["label"] / "net.bin"
            tag = f"wl-{topc['label']}-vs-{ctlc['label']}"
            cmd = [PY, "-m", "mahjong_ml.v4.gate", "--incumbent", str(inc),
                   "--candidate", str(cand), "--seeds", ",".join(str(s) for s in WALLS),
                   "--games", str(GATE_GAMES), "--block", str(GATE_GAMES),
                   "--workers", str(GATE_WORKERS), "--line", "def",
                   "--out", str(DATA / "gate"), "--tag", tag]
            env = dict(os.environ)
            env["MAHJONG_DATA_ROOT"] = str(DATA)
            env["MAHJONG_TRAINER"] = str(DATA / "tools" / "trainer" / "trainer.exe")
            env["PYTHONPATH"] = (f"{REPO / 'python'};"
                                 f"{REPO / 'python' / '.venv' / 'Lib' / 'site-packages'}")
            env["PYTHONIOENCODING"] = "utf-8"
            LOGS.mkdir(parents=True, exist_ok=True)
            log = LOGS / f"gate-{tag}.log"
            print(f"\n{'=' * 74}\n[gate] {topc['label']}（候选） vs {ctlc['label']}（现任）"
                  f"｜牌山 {WALLS} × {GATE_GAMES} 场｜日志 {log}", flush=True)
            if dry:
                print("[dry] " + " ".join(cmd))
                continue
            if not (inc.is_file() and cand.is_file()):
                print(f"⛔ 缺权重（{inc} / {cand}）—— 先跑完两条链", file=sys.stderr)
                return 2
            with log.open("w", encoding="utf-8", errors="replace") as fh:
                rc = subprocess.run(cmd, cwd=str(REPO), env=env, stdout=fh,
                                    stderr=subprocess.STDOUT).returncode
            v = json.loads((DATA / "gate" / tag / "verdict.json").read_text(encoding="utf-8"))
            n = _clean_jsonl(tag)
            print(f"[gate] 退出码 {rc}（0=采纳/3=不采纳/其它=出错）；删掉 {n} 个闸门轨迹"
                  f"（保留 summary/verdict）", flush=True)
            out.append({"tag": tag, "candidate_chain": topc["label"],
                        "incumbent_chain": ctlc["label"], "returncode": rc,
                        "cleaned_jsonl": n, "log": str(log), "verdict": v})
    p = STAGE / "gate-arm-vs-arm.json"
    if not dry:
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[gate] 落盘 {p}")
    return 0


# ---------------------------------------------------------------------------
# ④ report：把表拼成 Markdown（**只抄数，不重算**）
# ---------------------------------------------------------------------------

def _pct(x, nd=2):
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def _num(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


def report() -> int:
    d = json.loads((STAGE / "style-ladder.json").read_text(encoding="utf-8"))
    g = json.loads((STAGE / "gate-arm-vs-arm.json").read_text(encoding="utf-8")) \
        if (STAGE / "gate-arm-vs-arm.json").is_file() else []
    md = ["# `w` 阶梯实验 · 读数表（自动生成）", "",
          "⛔ 判决只看 `mahjong_ml/v4/gate.py`；本表是描述性读数。", "",
          "## 逐链风格向量", "",
          "| 链 | w | 代 | 小局 | 立直率 | 副露率 | 和了率 | 放铳率 | 平均打点 | 和了巡 | 立直巡 | 默听率 | 立直后和了率 |",
          "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in d["rows"]:
        s = r["style"]
        md.append(f"| {r['label']} | {r['w']} | {r['generation']} | {s.get('seat_hands')} "
                  f"| {_pct(s.get('riichi_rate'))} | {_pct(s.get('meld_rate'))} "
                  f"| {_pct(s.get('win_rate'))} | {_pct(s.get('deal_rate'))} "
                  f"| {_num(s.get('avg_win_score'), 0)} | {_num(s.get('avg_win_turn'))} "
                  f"| {_num(s.get('avg_riichi_turn'))} | {_pct(s.get('dama_rate'))} "
                  f"| {_pct(s.get('riichi_win_rate'))} |")
    md += ["", "## 逐链采集读数（1000 场自对弈，对风格桌；`se` = 逐场标准误）", "",
           "| 链 | w | 代 | 采集顺位点 (se) | 每场和了 (se) | 每场放铳 (se) | 每场和了点 (se) | 和了率 | 放铳率 | 平均打点 |",
           "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in d["rows"]:
        c = r["collect"]

        def cs(k, nd=2):
            v, se = c.get(k), c.get(k + "_se")
            return "n/a" if v is None else f"{v:.{nd}f} ({se:.{nd}f})"
        md.append(f"| {r['label']} | {r['w']} | {r['generation']} | {cs('rank_points')} "
                  f"| {cs('wins_per_game', 3)} | {cs('deals_per_game', 3)} "
                  f"| {cs('win_points_per_game', 0)} | {_pct(c.get('win_rate'))} "
                  f"| {_pct(c.get('deal_in_rate'))} | {_num(c.get('avg_win_score'), 0)} |")
    md += ["", "## 臂均值 ± **臂间区间**（2 条链 / 臂）", "",
           "| 臂 | w | 代 | 立直率 均值[min,max] | 副露率 | 和了率 | 放铳率 | 平均打点 | 默听率 | 采集顺位点[min,max] |",
           "| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | --- |"]
    for arm, gens in d["arms"].items():
        for gen, ent in sorted(gens.items()):
            def cell(k, pct=True):
                v = ent.get(k)
                if not v:
                    return "n/a"
                f = _pct if pct else (lambda z: _num(z, 0))
                return f"{f(v['mean'])}[{f(v['min'])},{f(v['max'])}]"
            rp = ent.get("collect_rank_points")
            rp_s = ("n/a" if not rp else
                    "{:.2f}[{:.2f},{:.2f}]".format(float(rp["mean"]), float(rp["min"]),
                                                   float(rp["max"])))
            md.append(f"| {arm} | {ent['w']} | {gen} | {cell('riichi_rate')} | {cell('meld_rate')} "
                      f"| {cell('win_rate')} | {cell('deal_rate')} "
                      f"| {cell('avg_win_score', False)} | {cell('dama_rate')} | {rp_s} |")
    nf = d.get("noise_floor") or {}
    if nf:
        md += ["", "## 噪声地板：**同配方三次跑**（上一轮 `v4-sty-a` + 本轮 `w=0` 两条链）", "",
               f"跑的三条链：{', '.join(nf.get('labels', []))}", "",
               "| 代 | 读数 | 三次跑的值 | 极差 | 逐场 SE |", "| --- | --- | --- | ---: | --- |"]
        for gen in sorted(k for k in nf if k.startswith("g")):
            ent = nf[gen]
            for k in ("riichi_rate", "meld_rate", "win_rate", "deal_rate", "dama_rate"):
                if k not in ent:
                    continue
                vals = ", ".join(_pct(v) for v in ent[k]["values"])
                md.append(f"| {gen} | {k} | {vals} | {_pct(ent[k]['range'])} | — |")
            for k in ("rank_points", "deals_per_game", "wins_per_game"):
                key = "collect_" + k
                if key not in ent:
                    continue
                vals = ", ".join(f"{v:.3f}" for v in ent[key]["values"])
                ses = ", ".join(f"{s:.3f}" for s in ent[key]["se"])
                md.append(f"| {gen} | {key} | {vals} | {ent[key]['range']:.3f} | {ses} |")
    pw2 = d.get("prior_w2") or []
    if pw2:
        md += ["", "## 上一轮 `w=2` 档（1 条链）· 只作参照", "",
               "| 链 | 代 | 立直率 | 副露率 | 和了率 | 放铳率 | 默听率 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for r in pw2:
            s = r["style"]
            md.append(f"| {r['label']} | {r['generation']} | {_pct(s.get('riichi_rate'))} "
                      f"| {_pct(s.get('meld_rate'))} | {_pct(s.get('win_rate'))} "
                      f"| {_pct(s.get('deal_rate'))} | {_pct(s.get('dama_rate'))} |")
    md += ["", "## 臂对臂闸门（`--line def`：主口径 deals + 顺位点护栏）", "",
           "| 配对 | 牌山 | deals Δ [CI] | 护栏 rank Δ [CI] | 套间散布 | 判决 |", "| --- | --- | --- | --- | ---: | --- |"]
    for e in g:
        v = e["verdict"]
        ws = v.get("wall_deltas") or []
        md.append(f"| {e['candidate_chain']} vs {e['incumbent_chain']} | {v.get('seeds')} "
                  f"| {v['delta']:+.3f} [{v['lo']:+.3f},{v['hi']:+.3f}] "
                  f"| {v.get('guard_delta'):+.2f} [{v.get('guard_lo'):+.2f},{v.get('guard_hi'):+.2f}] "
                  f"| {v.get('wall_spread')} | {'采纳' if v.get('adopt') else '不采纳'}"
                  f"（护栏{'✓' if v.get('guard_ok') else '✗'}）；逐套 {ws} |")
    (STAGE / "ladder-tables.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))
    print(f"\n[report] 落盘 {STAGE / 'ladder-tables.md'}")
    return 0


def status() -> int:
    print(f"STAGE={STAGE}  （存在={STAGE.is_dir()}）")
    for c in chains():
        tags = [f"{c['label']}-g{g:02d}" for g in range(1, GENERATIONS + 1)]
        done = [t for t in tags if (TRACES / t).is_dir()]
        net = REPO / "tools" / "build" / c["label"] / "net.bin"
        man = STAGE / f"{c['label']}-manifest.json"
        print(f"  {c['label']:<22} w={c['w']:<5} seed={c['seed']}  轨迹={len(done)}/{len(tags)} "
              f"末代权重={'有' if net.is_file() else '无'} manifest={'有' if man.is_file() else '无'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="w-ladder.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="跑链（顺序）")
    r.add_argument("--only", action="append", default=[], help="只跑指定 label（可重复）")
    r.add_argument("--dry-run", action="store_true")
    sub.add_parser("measure", help="逐代风格向量 + 臂表")
    g = sub.add_parser("gate", help="臂对臂闸门")
    g.add_argument("--dry-run", action="store_true")
    sub.add_parser("report", help="拼 Markdown 表")
    sub.add_parser("status", help="看进度")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(a.only, a.dry_run)
    if a.cmd == "measure":
        measure()
        return 0
    if a.cmd == "gate":
        return gate(a.dry_run)
    if a.cmd == "report":
        return report()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
