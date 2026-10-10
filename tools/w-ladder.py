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
    python tools/w-ladder.py post    --label <链> [--dry-run]  # ★ 用**末代权重补采**一次 = 训练后主读数
    python tools/w-ladder.py gate    [--dry-run]            # 臂对臂闸门（最高档 × 对照，2×2 配对）
    python tools/w-ladder.py report                         # 把上面几张表拼成 Markdown

⛔⛔ **口径纪律（2026-10-10 钉进来，见 `PROBE-dec6170.md` §1）**：链内第 g 代采集用的是**这一代开始时
的权重**（`loop.py` L308-330 的 `net_in`）⇒ **`g01` 行测的是"训练前"的策略（= `--init`/现任）**，
它**在原理上测不到任何训练效果**（每一臂的 `g01` 都是同一个现任，跨臂比它 ≡ 比采样噪声）。
真正测"训练后"的只有两种行：① 链内 `gNN`（g ≥ 2，= 训过 g−1 代）；② `post` 子命令用**末代权重**
在**同一 seed 族**上补采一次（配对 ⇒ 逐场 SE ≈ 0.5pp，这是本设计的精度来源）。
`measure()` 会给每一行打 `phase`（`pre` / `post` / `post_final`），`pre` 行**打警告**、并显式标
`effect_reading=false`；`report()` 把口径写进表头列。
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

#: 付奖模式的**模式名单一来源**在 `mahjong_ml.v4.style_reward`（⛔ 不在这里抄一份）。
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "python" / ".venv" / "Lib" / "site-packages"))
from mahjong_ml.v4 import style_reward as v4sr                # noqa: E402

#: ---- 实验的**唯一**配置表（预注册 §2 与它一一对应；改这里必须先改预注册）-------------------
#: `w=0` 那档**刻意不传** `--style-bonus`（空串 = 那个功能不存在）：这样它与上一轮的对照臂
#: `v4-sty-a` **逐字同配方** ⇒ 才能拿来做"同配方第 2/3 次跑"的噪声地板读数（预注册 §5）。
W_TOP = 2800                     # ← 校准探针实测 `w_for_parity = 2782` 就近取整到百位（预注册 §1）
W_MID = 300                      # ≈ 最高档的 10%（尺度阶梯；见预注册 §1）
#: 臂对臂闸门的牌山（**4 套全新**，预注册 §4 ② 的"4 套"口径）。
#: 为什么不是上一轮那两个（`77760001/2`）：任务口径要 **4 套 × 3,000 场 = 12,000 场/配对**，
#: 且**明确要求 seed 与历次实验的牌山都不相交**（`2026xxxx` / `2073xxxx` / `3000xxxx` /
#: `31000001-4` / `32000001-4` / `33000001-4` / `77760001-4` / `60010101` / `61997007`）。
#: 取 `40970001..4`：与上面每一族都不同，且与六条链的采集 seed（243100xx）不相交。
WALLS = (40970001, 40970002, 40970003, 40970004)
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
# ★ 行级「口径」：训练前 / 训练后（2026-10-10 钉进来）
# ---------------------------------------------------------------------------
#: 事故（`PROBE-dec6170.md` §1）：链内**第 g 代的采集**跑的是"这一代**开始时**的权重"
#: （`loop.py`：`net_in = --init`，跑完这一轮才 `net_in = net_out`；采集命令行里就是
#: `--policy net:…<init>/net.bin@0#0.5`）⇒ **`g01` 行 = 训练前**。
PHASE_PRE = "pre"                 # ⛔ 训练前（= `--init` / 现任）
PHASE_POST = "post"               # ✓ 训练后（链内第 g≥2 代采集 = 训过 g−1 代）
PHASE_POST_FINAL = "post_final"   # ✓ 训练后（`post` 子命令补采 = 末代权重，训过 N 代）
PHASE_LABEL = {
    PHASE_PRE: "⛔训练前（g01 = --init/现任）",
    PHASE_POST: "✓训练后（链内采集，训过 g−1 代）",
    PHASE_POST_FINAL: "✓训练后（末代权重补采）",
}
#: ⛔ 这句要**原样**出现在警告与报告里 —— 它是本次修的那条口径本身。
PRE_WARNING = ("⛔ 这一行是**训练前**（`g01` 的采集用的是 `--init`/现任）：**不许当效果读数** —— "
               "跨臂比 `g01` 在原理上测不到训练效果（每一臂的 g01 都是同一个现任，差多少只是采样噪声）。"
               "要效果读数就用 `g≥2` 的行或 `post` 子命令补采的那一行。")

#: 配对检验的轴（**只用于"训练前 vs 训练后"的逐场配对**）：`(维, 分子键, 分母键, 单位)`
#: ⛔ 键必须与 `style-vector.py` 的 `counts` 同名 —— 配对钱（`_student_per_game`）按整数记账，
#: 再拿它的合计值与 `style-vector` 的 `counts` **逐项对账**（对不上就不出数），所以两边不会各算一套。
PAIR_AXES = (
    ("riichi_rate", "riichi_hands", "seat_hands", "pct"),
    ("meld_rate", "meld_hands", "seat_hands", "pct"),
    ("win_rate", "wins", "seat_hands", "pct"),
    ("deal_rate", "deals", "seat_hands", "pct"),
    ("riichi_win_rate", "riichi_wins", "riichi_hands", "pct"),
    ("avg_win_score", "win_points", "wins", "abs"),
    # ★ `dama` 轴（2026-10-10）：**主**读数 = 每场每席的 `dama_wins` **计数**（分母 `games` =
    #   这一场的学生席数 = 1 ⇒ Δ 的单位就是"场/席"）；**辅**读数 = 聚合口径 `dama_rate`
    #   （= `dama_wins / wins`，与 `tools/style-vector.py` 的 `dama_rate` 同名同分子分母）。
    ("dama_wins_per_game", "dama_wins", "games", "abs"),
    ("dama_rate", "dama_wins", "wins", "pct"),
    # ★★ `dama` 轴的**对齐后**判据（2026-10-10 第二轮，本任务的核心）：奖励侧的付奖行 = "该行
    #   `legal` 含 `riichi:*` **且** `chosen` 不是它" ⇒ 判据必须量**同一个集合**（**行级**），
    #   而不是手级的 `dama_wins`（实测只有 **8%** 重叠、92% 来自**根本不能立直**的副露手）。
    #   **主** = 每场每席的"能立而不立"**行数**（`games` 当分母 = 这一场的学生席数 = 1，单位 = 行/场/席）；
    #   **辅** = 聚合 `decline_rate`（= `decline_rows / legal_rows`，行级分母）。
    #   ⚠ 分母 `legal_rows` 是**每一场自己的**行数 ⇒ 该场没有"能立直"的行时这一场不进 `decline_rate`
    #   的配对（`_paired_delta` 的既有约定）；**主判据（计数）不受此影响，1000 场全用**。
    ("decline_rows_per_game", "decline_rows", "games", "abs"),
    ("decline_rate", "decline_rows", "legal_rows", "pct"),
)
#: 95% CI 的正态近似系数（与 `tools/w-ladder-gate-table.py` / `eval._paired_from_diffs` 同族做法）。
Z95 = 1.96
#: 补采的落点（与 `PROBE-dec6170.md` 的 `traces-post\…` 同一约定）。
TRACES_POST = STAGE / "traces-post"
PAIRED = STAGE / "paired"


def phase_of(generation: int) -> str:
    """链内第 `generation` 代采集的行属于哪个口径（⛔ **不是**"训练过几代"）。

    `g01` = `--init`（训练前）；`g ≥ 2` = 训过 `g−1` 代（训练后）。末代权重（训过 N 代）**没有**
    链内轨迹 —— 那是 `post_final`，只能靠 `post` 子命令补采。
    """
    return PHASE_PRE if int(generation) <= 1 else PHASE_POST


def phase_fields(generation: int, post_final: bool = False) -> dict:
    """行上的口径字段（工具与报告**共用同一份**，避免两处各写一套判据）。"""
    ph = PHASE_POST_FINAL if post_final else phase_of(generation)
    out = {"phase": ph, "phase_label": PHASE_LABEL[ph],
           "effect_reading": ph != PHASE_PRE,
           "phase_warning": PRE_WARNING if ph == PHASE_PRE else None}
    return out


# ---------------------------------------------------------------------------
# 配置 → 链
# ---------------------------------------------------------------------------

def chains(arms=ARMS):
    """展开成链表：每条链 = `(arm 配置, 链号, label, seed, **付奖模式**)`。

    ⚠ `mode` **必须在这里带过来**：它只经 `MAHJONG_STYLE_MODE` 进回路（`run-league.ps1` 不许改），
    而 `run()` 是从**这个函数**产出的链表里取 `mode` 的 —— 漏了它，探针会**静默按 `hand` 跑**
    （实测踩过：`decision` 探针的紧凑集命令行里没有 `--style-bonus-mode`，整条链白跑）。
    """
    out = []
    for a in arms:
        # ★ 复现（**每臂 ≥2 链**）用：`chain_labels` 给出**完整**链标签（⛔ 不再追加 `-cN`）。
        #   为什么需要：`probe` 原来把链数写死成 1（拼 `-c1`）⇒ 想跑第 2 条链只能把 `-c2` 塞进
        #   臂标签里，拼出 `…-c2-c1` 这种误导性名字。缺这个键时**逐位不变**（走下面的老分支）。
        labs = list(a.get("chain_labels") or [])
        for k, seed in enumerate(a["seeds"], start=1):
            lab = labs[k - 1] if k - 1 < len(labs) else f"{a['label']}-c{k}"
            out.append({"arm": a["arm"], "w": a["w"], "bonus": a["bonus"],
                        "mode": (a.get("mode") or v4sr.MODE_HAND),
                        "chain": k, "label": lab, "seed": seed})
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


def run(only: list[str], dry: bool, todo: list | None = None, generations: int | None = None) -> int:
    STAGE.mkdir(parents=True, exist_ok=True)
    AUDIT.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    gens = GENERATIONS if generations is None else int(generations)
    if todo is None:
        todo = [c for c in chains() if not only or c["label"] in only]
    if only:
        missing = [x for x in only if x not in [c["label"] for c in chains()]]
        if missing:
            raise SystemExit(f"⛔ --only 里有配置表没有的链：{missing}")
    print(f"[cfg] 最高档 w={W_TOP} / 中间档 w={W_MID} / 对照 w=0（不传 --style-bonus）"
          f"｜本轮代数 {gens}")
    for c in todo:
        cmd = ["pwsh", "-NoProfile", "-File", str(REPO / "tools" / "run-league.ps1"),
               "-Label", c["label"], "-Seed", str(c["seed"]),
               "-Generations", str(gens), "-Games", str(GAMES),
               "-NoGate", "-Style", "def", "-StylePools", style_pools(),
               "-Incumbent", str(INCUMBENT)]
        env = dict(os.environ)
        env["MAHJONG_DATA_ROOT"] = str(DATA)
        env["MAHJONG_TRAINER"] = str(DATA / "tools" / "trainer" / "trainer.exe")
        env["PYTHONPATH"] = f"{REPO / 'python'};{REPO / 'python' / '.venv' / 'Lib' / 'site-packages'}"
        env["PYTHONIOENCODING"] = "utf-8"
        env["MAHJONG_STYLE_BONUS"] = c["bonus"]
        env["MAHJONG_STYLE_WHITEN"] = "student"
        # ★ **付奖模式**（`--style-bonus-mode`）：与 `MAHJONG_STYLE_BONUS` 同一条路进回路。
        #   缺省（`hand`）**也显式写**：`loop.py` 的缺省本来就等于它 ⇒ 数值语义不变，
        #   但"这一轮跑的到底是哪个模式"在环境里一眼可见（复现时不用去猜）。
        #   ⚠ `c["mode"]` 由 `chains()` 带过来；这里再核一次，**宁可当场报错**也不要静默跑成 hand。
        env[v4sr.MODE_ENV] = v4sr.check_mode(c.get("mode") or v4sr.MODE_HAND)
        # ★ 审计账的**持久**落点（`compact/<tag>` 会被 run-league 每代轮换删掉）。
        env["MAHJONG_STYLE_AUDIT_DIR"] = str(AUDIT)
        log = LOGS / f"{c['label']}.log"
        print(f"\n{'=' * 74}\n[chain] {c['label']}（臂 {c['arm']}，w={c['w']}，seed={c['seed']}，"
              f"{gens} 代，**付奖模式 {env[v4sr.MODE_ENV]}**）bonus={c['bonus']!r}\n"
              f"[chain] 日志 {log}", flush=True)
        if dry:
            print("[dry] " + " ".join(cmd))
            continue
        stop = threading.Event()
        box: list = []
        th = threading.Thread(target=lambda: box.extend(watch(c["label"], gens, stop)),
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
        # ★ 把 `release\<tag>.log`（`run-league.ps1` **每代覆盖**，只有最后一代活下来）另存一份。
        #   要它的原因：`w_for_parity` 那行**只**打在那里（`pretrain.py` 的尺度读数），而它是本
        #   实验"实测 w_for_parity"的原始出处（预注册 §1 要报"日志原文"）。
        saved_release = []
        for g in range(1, gens + 1):
            rl = REPO / "release" / f"{c['label']}-g{g:02d}.log"
            if rl.is_file():
                dst = LOGS / f"{c['label']}-g{g:02d}-from-release.log"
                shutil.copy2(rl, dst)
                saved_release.append(str(dst))
        man = {"chain": c, "returncode": rc, "seconds": round(dt, 1), "log": str(log),
               "staged": box, "generations": gens, "games": GAMES,
               "incumbent": str(INCUMBENT), "audit_dir": str(AUDIT),
               "release_logs": saved_release}
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
# ①b probe：**判别性探针**（`CONTINGENCY.md` §7 台阶 2）—— 单链 1 代、指定 `w`
# ---------------------------------------------------------------------------

#: 探针的代数：**1 代**（台阶 2 的允许成本 20–35 min 里最小的一格；⛔ 不跑 2 代不跑第二条链）。
PROBE_GENERATIONS = 1
#: 探针的采集 seed：与六条链（24310011..32）、闸门牌山（40970001..4）都**不相交**。
PROBE_SEED = 24310041


def probe(w: float, label: str, seed: int, dry: bool = False,
          mode: str = "hand", generations: int = PROBE_GENERATIONS,
          seeds: list | None = None, labels: list | None = None,
          axis: str = "riichi", pair: str = "no_deal") -> int:
    """单链**多代**、**指定 `w`** 的探针（除 `w`、`mode` 与代数外，与六条链**逐字同配方**）。

    ⛔ 这不是判决：单链谈不上"臂间散布"，只回答一个是非题 ——「在 `w = k·w*` 这个**极端剂量**下，
    立直率会不会**大幅上移 / 会不会随代数累积**」。所以：`-NoGate`、不跑对照链、不跑闸门
    （`CONTINGENCY.md` §7 的门槛写明"单链谈不上判决"）。

    @param mode **付奖模式**（`--style-bonus-mode`）：`hand`（缺省，老口径）/ `decision`
        （逐决策付奖：奖励只落在真的执行了动作的那一行）。⚠ 它由 `MAHJONG_STYLE_MODE` 进回路
        （与 `MAHJONG_STYLE_BONUS` 同一条路 —— `run-league.ps1` 不许改）。
    @param generations 代数（缺省 = `PROBE_GENERATIONS` = 1，与加这个参数之前**逐位相同**）。
        ⚠ **代数 > 1 时读数纪律变了**（见 `measure()` 的 `phase`）：第 1 代采集的是 `--init`
        （= **训练前**），第 2..N 代采集的是"训过 1..N−1 代"的策略，**末代权重本身没有任何一条
        链内轨迹** —— 要量"训练后"，必须补采（`post` 子命令）。
    @param seeds / @param labels ★ **复现用**：一次跑**多条**链（每个 seed 族一条），与单链口径
        逐字相同、只是链数 ≥2 ⇒ 才有"**臂间散布**"这个读数（单链不算采纳证据）。
        `labels` 与 `seeds` **一一对应**且必须是**完整链标签**（例 `v4-wl-dec6170-4g-c2`）；
        ⛔ 两者长度不一致就当场报错。都不给 = 老行为（单链 `label-c1`）。
    @param axis / @param pair ★ **换轴**（`PREREGISTRATION-MELD.md` §0：配方与 `decision@6170` **逐字
        相同、只换奖励轴与剂量**）。缺省 `riichi` + `no_deal` ⇒ 与加这两个参数之前**逐位相同**。
        ⚠ `axis` 必须是**行级动作轴**（`style_reward.ROW_ACTION_AXES`：`riichi`/`meld`/`win`）——
        `decision` 模式下"这一行做了它吗"没有定义的轴**当场报错**（⛔ 不猜、不静默退回 `hand`）。
        `pair` 只在 `PAIR_KEYS`（+ `none`）里选；文法校验**复用** `style_reward.parse_bonus`
        （⛔ 不在这里抄第二份轴表）。
    """
    mode = v4sr.check_mode(mode)
    gens = int(generations)
    if gens < 1:
        raise SystemExit(f"⛔ --generations 必须 ≥ 1（收到 {gens}）")
    seed_list = [int(s) for s in (seeds or [])] or [int(seed)]
    lab_list = [str(x) for x in (labels or [])]
    if lab_list and len(lab_list) != len(seed_list):
        raise SystemExit(f"⛔ --labels 与 --seeds 必须一一对应（{len(lab_list)} vs {len(seed_list)}）")
    bonus = f"{axis}={w:g}:{pair}"
    try:
        v4sr.parse_bonus(bonus)                 # 轴 / 权重 / 质量轴的**唯一**校验点
    except v4sr.BonusSpecError as e:
        raise SystemExit(f"⛔ --axis/--pair 拼出的 `--style-bonus` 不合法：{bonus!r}\n   {e}") from None
    if mode == v4sr.MODE_DECISION and axis not in v4sr.ROW_ACTION_AXES:
        raise SystemExit(
            f"⛔ `decision` 模式下 {axis!r} 不是**行级动作轴**（能做付奖轴的只有 "
            f"{', '.join(v4sr.ROW_ACTION_AXES)}）—— 换轴，或改用 `--mode hand`；"
            f"⛔ 不静默退回 `hand`（那正是这个模式要修的病）")
    arm = {"arm": (f"probe-w{w:g}" if axis == "riichi" else f"probe-{axis}-w{w:g}"),
           "w": w, "bonus": bonus,
           "label": label, "seeds": tuple(seed_list), "mode": mode}
    if lab_list:
        arm["chain_labels"] = tuple(lab_list)
    print(f"[probe] {len(seed_list)} 条链 × {gens} 代 · w={w:g} · **付奖模式 {mode}** · "
          f"seeds={seed_list} · labels={lab_list or f'{label}-c1..'} · "
          f"bonus={arm['bonus']!r}（其余配方与六条链逐字相同）")
    return run([], dry, todo=chains((arm,)), generations=gens)


# ---------------------------------------------------------------------------
# ② measure：逐代风格向量（`style-vector.py`）+ 臂均值 ± 臂间区间
# ---------------------------------------------------------------------------

def style_vector(tdir: Path, tag: str, reuse: bool = True) -> dict:
    """跑判据侧的独立实现 `tools/style-vector.py`（**只解析它的 JSON**，不自己算风格量）。

    ⚠ **硬判据**：`style-vector.py` 自带与 `summary.json` 的逐项对账（自检①②③）——
    对不上（例如"轨迹副本是采集途中拷的、比 summary 少几场"）时它给的读数是**假的**。
    所以 `checks_all_ok` 不是 True 就**当场报错**，不把坏数往表里抄。

    @param reuse `STYLE_VEC/<tag>.json` 已存在就**直接复用**（缺省）。为什么要它：本仓规矩是
        「**删轨迹、留摘要**」，轨迹删掉之后重跑 `measure` 就只能靠这份 JSON —— 复用同时也让
        逐代表和 `post` 的补采**共用同一份读数**（不会出现"表里两个数、来源不同"）。
        要强制重算（比如怀疑那份 JSON 是坏的）就传 `reuse=False`。
    """
    STYLE_VEC.mkdir(parents=True, exist_ok=True)
    out = STYLE_VEC / f"{tag}.json"
    if reuse and out.is_file():
        sv = json.loads(out.read_text(encoding="utf-8"))
        _assert_checks_ok(sv, f"{tag}（复用 cached JSON）")
        print(f"[measure] 复用已有读数 {out}（轨迹已删/不重算；自检仍逐项查过）", flush=True)
        return sv
    if not _traces_of(tdir):
        raise SystemExit(f"⛔ {tag}：{tdir} 里没有 g*.jsonl，也没有可复用的 {out}"
                         f" —— 轨迹已删且没算过风格向量，这个读数**取不回来**")
    p = subprocess.run([PY, str(REPO / "tools" / "style-vector.py"), str(tdir),
                        "--json", str(out)],
                       cwd=str(REPO), capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0:
        raise SystemExit(f"⛔ style-vector 失败（{tag}，退出码 {p.returncode}）：\n"
                         f"{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    (STYLE_VEC / f"{tag}.md").write_text(p.stdout, encoding="utf-8")
    sv = json.loads(out.read_text(encoding="utf-8"))
    _assert_checks_ok(sv, tag)
    return sv


def _assert_checks_ok(sv: dict, tag: str) -> None:
    """`style-vector` 的自检必须全过（否则它的数是假的，别往表里抄）。"""
    if sv.get("checks_all_ok"):
        return
    bad = [(c.get("name") or c.get("check"), c.get("detail")) for c in (sv.get("checks") or [])
           if not c.get("ok")]
    raise SystemExit(f"⛔ {tag} 的轨迹副本与 summary.json 对不上（style-vector 自检红）：\n"
                     + "\n".join(f"   {n}：{d}" for n, d in bad))


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
                # ⚠ 只有**比率**维打印成百分数；`avg_win_score`（点）/`avg_win_turn`（巡）/
                #   `avg_riichi_turn` 是**绝对量**（原先把 6945 印成 `694500%`，是把"比率"的
                #   排版套到绝对量上 —— 数与极差是对的，只有排版错）。
                ent[k] = {"values": v, "min": min(v), "max": max(v), "range": max(v) - min(v),
                          "unit": "pct" if k.endswith("_rate") else "abs"}
        for k in ("rank_points", "deals_per_game", "wins_per_game"):
            v = [r["collect"].get(k) for r in sel if r["collect"].get(k) is not None]
            se = [r["collect"].get(k + "_se") for r in sel if r["collect"].get(k) is not None]
            if len(v) > 1:
                ent["collect_" + k] = {"values": v, "min": min(v), "max": max(v),
                                       "range": max(v) - min(v), "se": se}
        out[f"g{g:02d}"] = ent
    return out


def _chain_tags(label: str) -> list:
    """`traces/<label>-gNN` 的已有代 `[(代号, 目录)]`（按代号排序）。

    ⛔ 不写死 `GENERATIONS`：探针链可能跑 4 代，写死 2 会**静默少读三代**。
    """
    out = []
    for d in TRACES.glob(f"{label}-g[0-9][0-9]"):
        try:
            out.append((int(d.name[-2:]), d))
        except ValueError:
            continue
    return sorted(out)


def _post_rows(c: dict, gens: list, reuse: bool = True) -> list:
    """★ **补采行**（`post` 子命令的产物）：末代权重、**同一 seed 族** ⇒ 训练后的主读数。

    为什么要它（而不是只读链内 `gNN`）：末代权重（训过 N 代）**没有任何链内轨迹** ——
    链内最后一条 `gNN` 采集用的是"训过 N−1 代"的权重。要答"训了 N 代之后策略变成什么样"，
    只能在末代权重上补采一次；用**同一 seed 族**采 ⇒ 与 `g01`（训练前）**逐场配对**。
    """
    if not gens:
        return []
    n = max(g for g, _ in gens)
    tag_pre = f"{c['label']}-g01"
    tag_post = f"{c['label']}-g{n:02d}-POST"
    tdir = TRACES_POST / f"{c['label']}-g{n:02d}"
    js = STYLE_VEC / f"{tag_post}.json"
    if not js.is_file():
        print(f"[measure] ⛔ {c['label']} **没有补采行**（缺 {js.name}）：链内末代 g{n:02d} 采集的是"
              f"「训过 {n - 1} 代」的权重，**训过 {n} 代**的策略没有任何轨迹 ⇒ "
              f"跑 `post --label {c['label']}` 才能拿到训练后主读数", flush=True)
        return []
    sv = style_vector(tdir, tag_post, reuse=reuse)
    row = {"arm": c["arm"], "w": c["w"], "chain": c["chain"], "label": c["label"],
           "tag": tag_post, "generation": None, "generation_label": f"POST(g{n:02d} 权重)",
           "sort_key": n + 1, "weights_generation": n, "trained_generations": n,
           "post_final": True, "traces": str(tdir),
           "style": student_row(sv), "collect": collect_row(tdir), "audit": read_audit(tag_post),
           "paired_with": tag_pre}
    row.update(phase_fields(n, post_final=True))
    pf = PAIRED / f"{c['label']}-g{n:02d}-POST.json"
    if pf.is_file():
        row["paired"] = json.loads(pf.read_text(encoding="utf-8"))
    print(f"[measure] {tag_post} 立直率 {row['style'].get('riichi_rate')} "
          f"（{row['phase_label']}，配对基准 {tag_pre}）", flush=True)
    return [row]


def _probe_cfg(label: str) -> dict:
    """配置表（`ARMS`）**之外**的链（探针 / 临时链）的伪配置：`w`/`mode` 从审计账里读。

    ⛔ 读不到就写 `None`（**不编**）：审计账（`w-ladder/audit/<tag>.json`）的 `axes[].weight`
    与 `source.accounting_all_seats.mode` 是这条链"到底按什么口径跑的"的唯一持久证据。
    """
    au = read_audit(f"{label}-g01")
    w = next((ax.get("weight") for ax in (au.get("axes") or [])
              if ax.get("weight") is not None), None)
    mode = ((au.get("source") or {}).get("accounting_all_seats") or {}).get("mode")
    chain_no, tail = 0, label.rsplit("-c", 1)
    if len(tail) == 2 and tail[1].isdigit():
        chain_no = int(tail[1])
    return {"arm": (f"probe-w{w:g}" if w else "probe"), "w": w, "bonus": au.get("spec"),
            "mode": mode, "chain": chain_no, "label": label, "seed": None}


def measure(reuse: bool = True) -> dict:
    rows, warnings = [], []
    cfgs = list(chains())
    # ★ **配置表外的链**（探针 / 临时链）也要能读数 —— 否则"训练后主读数"这条路对探针根本不成立
    #   （上一轮就是这么漏的：`decision@6170` 那条链的风格向量只能手敲命令算）。w / mode 从审计账读。
    known = {c["label"] for c in cfgs}
    for d in sorted(TRACES.glob("*-g[0-9][0-9]")):
        lab = d.name.rsplit("-g", 1)[0]
        if lab in known:
            continue
        known.add(lab)
        cfgs.append(_probe_cfg(lab))
    for c in cfgs:
        gens = _chain_tags(c["label"])
        if not gens:
            print(f"⚠ 缺轨迹 {TRACES / (c['label'] + '-g01')} —— 跳过（链没跑完？）")
            continue
        for g, tdir in gens:
            tag = tdir.name
            sv = style_vector(tdir, tag, reuse=reuse)
            row = {"arm": c["arm"], "w": c["w"], "chain": c["chain"], "label": c["label"],
                   "tag": tag, "generation": g, "generation_label": f"g{g:02d}", "sort_key": g,
                   # ★ 这一行采集用的是**这一代开始时**的权重 = 训过 g−1 代（g=1 ⇒ 训过 0 代）
                   "weights_generation": g - 1,
                   "traces": str(tdir), "style": student_row(sv), "collect": collect_row(tdir),
                   "audit": read_audit(tag)}
            row.update(phase_fields(g))
            rows.append(row)
            print(f"[measure] {tag} 立直率 {row['style'].get('riichi_rate')} "
                  f"副露率 {row['style'].get('meld_rate')}｜{row['phase_label']}", flush=True)
            if row["phase"] == PHASE_PRE:
                msg = f"{tag}：{PRE_WARNING}"
                warnings.append(msg)
                print(f"[measure] {msg}", flush=True)
        rows.extend(_post_rows(c, gens, reuse=reuse))
    prior = _prior_rows(PRIOR_SAME_RECIPE, "prior-a")
    prior_w2 = _prior_rows(PRIOR_W2, "prior-b")
    # 臂表：每个 (臂, 代) 的均值 ± **臂间**极差（2 条链）
    arms: dict = {}
    for a in ARMS:
        gens_present = sorted({r["sort_key"] for r in rows if r["arm"] == a["arm"]})
        for g in gens_present:
            sel = [r for r in rows if r["arm"] == a["arm"] and r["sort_key"] == g]
            if not sel:
                continue
            ent: dict = {"n_chains": len(sel), "w": a["w"], "generation": g,
                         "phase": sel[0]["phase"], "phase_label": sel[0]["phase_label"],
                         "chains": {}}
            for k in DIMS:
                v = [r["style"].get(k) for r in sel if r["style"].get(k) is not None]
                if not v:
                    continue
                pct_k = k.endswith("_rate")
                ent[k] = {"mean": statistics.fmean(v), "min": min(v), "max": max(v),
                          "spread": max(v) - min(v), "unit": "pct" if pct_k else "abs"}
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
    # ★ 主读数（训练后）：每条链取**排序最后的那一行**（= 补采优先，否则链内最高代）
    main = {}
    for c in cfgs:
        sel = sorted([r for r in rows if r["label"] == c["label"]], key=lambda r: r["sort_key"])
        if not sel:
            continue
        post = [r for r in sel if r["effect_reading"]]
        main[c["label"]] = {
            "main_reading_tag": (post[-1]["tag"] if post else None),
            "main_reading_phase": (post[-1]["phase"] if post else None),
            "main_reading_riichi_rate": (post[-1]["style"].get("riichi_rate") if post else None),
            "pre_reading_tag": (sel[0]["tag"] if sel[0]["phase"] == PHASE_PRE else None),
            "pre_reading_riichi_rate": (sel[0]["style"].get("riichi_rate") if sel else None),
            "warnings": [w for w in warnings if w.startswith(sel[0]["tag"])],
        }
    out = {"tool": "tools/w-ladder.py measure",
           "warning": "⛔ 不是判据：判决只看 mahjong_ml/v4/gate.py。这里是描述性方向读数。",
           "discipline": {"pre_rows_are_not_effect_readings": True,
                          "rule": PRE_WARNING,
                          "phase_labels": PHASE_LABEL},
           "config": {"w_top": W_TOP, "w_mid": W_MID, "generations": GENERATIONS,
                      "games": GAMES, "incumbent": str(INCUMBENT), "walls": list(WALLS)},
           "rows": rows, "phase_warnings": warnings, "main_readings": main,
           "prior_same_recipe": prior, "prior_w2": prior_w2,
           "noise_floor": noise_floor(rows, prior), "arms": arms}
    p = STAGE / "style-ladder.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[measure] 落盘 {p}（{len(rows)} 条链·代 + 上一轮 {len(prior)} 代；"
          f"⚠ 训练前行 {len(warnings)} 条已打标）")
    return out


# ---------------------------------------------------------------------------
# ②b post：**用末代权重补采一次** = 训练后的主读数（并与 g01 逐场配对）
# ---------------------------------------------------------------------------

#: 与 `style-vector.py` 同名的动作前缀（配对的钱只认这三个来源，见 `_student_per_game`）。
RIICHI_PREFIX = "riichi:"
MELD_PREFIXES = ("chi:", "pon:", "kan:")
#: 配对要对账的整数键（名字与 `style-vector.py` 的 `counts` **逐字相同**）。
#: ★ `dama_wins` / `games`（2026-10-10 加）：`dama` 轴的主配对量是"每场每席 `dama_wins` 计数"
#:   （`games` 当分母 = 这一场学生占了几席 = 1），而它们**必须**与 `style-vector.py` 的
#:   `counts.dama_wins` / `counts.games` 逐项相等 —— 所以两个键都得在这里对账。
COUNT_KEYS = ("seat_hands", "riichi_hands", "meld_hands", "wins", "deals", "win_points",
              "riichi_wins", "dama_wins", "games")
#: ★★ **行级**「能立而不立」的独立记账键（2026-10-10 第二轮）—— ⛔ **刻意不进 `COUNT_KEYS`**：
#: `COUNT_KEYS` 的合计必须与 `tools/style-vector.py` 的 `counts` **逐项相等**（`_verify_pair_totals`），
#: 而那份工具是**手级**口径、根本没有行级的 `legal_rows`/`decline_rows`（它的独立性要保住，
#: 本任务明确 **⛔ 不许把行级量塞进 `style-vector.py`**）。
#: 这组数的对账对象是**奖励侧的审计账**（`w-ladder/audit/<tag>.json`），口径逐条对应：
#:   * `decision_rows` = 学生席的决策行数（审计账的 `n_used`）；
#:   * `legal_rows` = 其中 `legal` 含 `riichi:*` 的行数（审计账同名键）；
#:   * `decline_rows` = 其中 `chosen` **不是** `riichi:*` 的行数（= 付奖行候选；审计账的
#:     `declined_rows` / `action_rows` —— 这两个键在奖励侧是同一个数）；
#:   * `decline_paid_rows` / `decline_blocked_rows` = 付奖行里"学生该小局未放铳"/"学生放铳"；
#:   * `decline_missing_legal_rows` = `legal` 取不到的行（⛔ 非 0 就不能做这个判据）；
#:   * `decline_viol_state` / `decline_viol_after_decl` = 两条**结构检查**（付奖行那一刻公开状态位
#:     必须还没立直 / 必须在首次宣言之前），都必须 = 0。
DECLINE_KEYS = ("decision_rows", "legal_rows", "decline_rows",
                "decline_paid_rows", "decline_blocked_rows", "decline_missing_legal_rows",
                "decline_viol_state", "decline_viol_after_decl")


def student_counts(sv: dict) -> dict:
    """从 style-vector 的 `policies` 里取**学生**那一行的整数账（判据同 `student_row`）。"""
    for name, st in (sv.get("policies") or {}).items():
        if "@" in name:
            return st.get("counts") or {}
    return {}


def _student_per_game(tdir: Path) -> dict:
    """★ **逐场**的学生席整数账 `{game: {计数键…}}` —— 只为"训练前 vs 训练后"的**逐场配对**。

    为什么要自己再读一遍轨迹：配对要的是"**每个场一个读数**"，而 `style-vector.py` 只给合计
    （配对 SE ≈0.5pp vs 不配对 ≈0.6pp，差的就是这份逐场信息；见 `PROBE-dec6170.md` §2）。

    ⛔ 纪律（同 `style-vector.py` 的 ①②③）：这里的**整数合计必须与 `style-vector.py` 的
    `counts` 逐项相等**（`_verify_pair_totals`）—— 两套实现各算一套、还都写进报告，是这类工具
    最容易犯的错；对不上就**不出数**（宁可没有配对读数，也不给一个自己都对不上的数）。
    """
    out: dict = {}
    for f in sorted(_traces_of(tdir), key=lambda x: int(x.name[1:-6])):
        riichi_flag: dict = {}      # (seat, hand_no) -> 该席这一小局宣言过立直
        meld_flag: dict = {}        # (seat, hand_no) -> 该席这一小局有副露（公开状态，任一行都能读到）
        hands: list = []            # (hand_no, agari, winner, loser, delta)
        dec_rows: list = []         # ★ 行级记账的原料（见 `DECLINE_KEYS`）：(行号, 席, 小局, chosen, 能立直?, 状态位, riichi_turn)
        game, labs = None, []
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                t = row.get("type")
                if t == "game":
                    # ⚠ `game` 行在文件**末尾**（`style-vector.py` 也是读完整个文件才取 policies）
                    #   ⇒ 这里必须**先缓存**再结算，不能边读边按学生席过滤（第一版就是这么错的：
                    #   逐场合计全是 0，被 `_verify_pair_totals` 当场抓住）。
                    labs = list(row.get("policies") or [])
                    game = int(row.get("game", f.name[1:-6]))
                elif t == "decision":
                    seat = int(row.get("seat", -1))
                    obs = row["obs"]
                    chosen = row["chosen"]
                    hn = row["hand_no"]
                    if obs["riichi"][seat] or (obs.get("riichi_turn") or [0] * 4)[seat] > 0 \
                            or chosen.startswith(RIICHI_PREFIX):
                        riichi_flag[(seat, hn)] = True
                    # 副露是**公开状态**：随便哪一席的行都能读出别家的副露（与 style-vector 同一条）
                    for s in range(4):
                        if obs["melds"][s]:
                            meld_flag[(s, hn)] = True
                    if chosen.startswith(MELD_PREFIXES):
                        meld_flag[(seat, hn)] = True
                    # ★★ **行级**「能立而不立」的**独立**实现（⛔ 不 import 奖励侧、⛔ 不进 style-vector.py）：
                    #   口径与 `ROW_ACTION_DECLINE['dama']` / 审计账的 `row_rule` **逐字相同** ——
                    #   该行 `legal` 含 `riichi:*` ∧ `chosen` 不是 `riichi:*`。
                    #   `legal` 的取法也与生产侧一致：行上的 `legal`，缺了退到 `obs.legal`；
                    #   两处都缺 = `None`（"这一行的合法动作集在轨迹里根本没有" ⇒ 对这条判据**无定义**，
                    #   记进 `decline_missing_legal_rows`，⛔ 不按 False 静默算 0）。
                    #   这里只**收集**：学生是哪一席要等文件末尾的 `game` 行（它在文件最后）。
                    legal = row.get("legal")
                    if legal is None:
                        legal = obs.get("legal")
                    ob_ri = obs.get("riichi") or []
                    ob_rt = obs.get("riichi_turn") or []
                    dec_rows.append((
                        len(dec_rows), seat, hn, chosen,
                        None if legal is None
                        else any(str(k).startswith(RIICHI_PREFIX) for k in legal),
                        bool(ob_ri[seat]) if 0 <= seat < len(ob_ri) else False,
                        int(ob_rt[seat]) if 0 <= seat < len(ob_rt) else 0))
                elif t == "hand":
                    hands.append((row["hand_no"], bool(row.get("agari")),
                                  int(row.get("winner", -1)), int(row.get("loser", -1)),
                                  row.get("delta") or [0] * 4))
        stu = next((i for i, p in enumerate(labs) if "@" in p), None)
        if stu is None:
            raise SystemExit(f"⛔ {f} 的 `game` 行里找不到带 `@` 的学生席：{labs}")
        c = {k: 0 for k in COUNT_KEYS}
        # `games` 的口径与 `style-vector.py` 同尺子：**场 × 席**（学生每场占 1 席 ⇒ 这一场 +1）。
        c["games"] = 1
        for hn, agari, winner, loser, delta in hands:
            c["seat_hands"] += 1
            if riichi_flag.get((stu, hn)):
                c["riichi_hands"] += 1
            if meld_flag.get((stu, hn)):
                c["meld_hands"] += 1
            if agari and winner == stu:
                c["wins"] += 1
                c["win_points"] += int(delta[stu])
                if riichi_flag.get((stu, hn)):
                    c["riichi_wins"] += 1
                else:
                    # ★ `dama_wins`（与 `style-vector.py` 逐字同一谓词）：和了且该小局**未立直**。
                    c["dama_wins"] += 1
            if loser == stu:
                c["deals"] += 1
        # ★★ **行级**「能立而不立」的记账（`DECLINE_KEYS`；口径见那一段注释）------------------------
        #   两遍扫：先取"该席第一次真的宣言立直"的行号（结构检查 ② 的界），再逐行数四个数。
        c.update({k: 0 for k in DECLINE_KEYS})
        first_decl: dict = {}
        for i, seat, hn, chosen, _has, _ori, _ort in dec_rows:
            if seat == stu and chosen.startswith(RIICHI_PREFIX):
                first_decl.setdefault((hn, seat), i)
        loser_of_hand = {hn: lo for hn, _ag, _w, lo, _dl in hands}
        for i, seat, hn, chosen, has, ori, ort in dec_rows:
            if seat != stu:
                continue
            c["decision_rows"] += 1
            if has is None:
                # ⛔ 对"能而不为"这类**依赖 `legal`** 的判据，取不到 `legal` 就是**无定义**：
                #   记账并（由 `_decline_selfcheck`）报错，绝不静默当成"不能立直"。
                c["decline_missing_legal_rows"] += 1
                continue
            if not has:
                continue
            c["legal_rows"] += 1
            if chosen.startswith(RIICHI_PREFIX):
                continue                      # 真的选了它（宣言行）⇒ 不是"能而不为"
            c["decline_rows"] += 1
            if ori or ort > 0:
                # 结构检查 ①：付奖行那一刻**公开状态位**必须还没立直（与奖励侧同一条检查）。
                c["decline_viol_state"] += 1
            fd = first_decl.get((hn, seat))
            if fd is not None and i >= fd:
                # 结构检查 ②：付奖行必须在该席**首次宣言之前**（之后的"没选它"不是自由选择）。
                c["decline_viol_after_decl"] += 1
            if loser_of_hand.get(hn, -1) == stu:
                c["decline_blocked_rows"] += 1      # 伴随量 `no_deal` 拦下（该小局学生放铳）
            else:
                c["decline_paid_rows"] += 1
        out[game if game is not None else int(f.name[1:-6])] = c
    return out


def _verify_pair_totals(per_game: dict, sv: dict, tag: str) -> dict:
    """逐场账的**合计**必须与 `style-vector.py` 的 `counts` 逐项相等（对不上就报错、不出数）。"""
    st = student_counts(sv)
    tot = {k: sum(g[k] for g in per_game.values()) for k in COUNT_KEYS}
    bad = [f"{k}: 逐场合计 {tot[k]} vs style-vector {st.get(k)}"
           for k in COUNT_KEYS if int(st.get(k, -1)) != tot[k]]
    if bad:
        raise SystemExit(f"⛔ {tag}：逐场账与 style-vector 的 counts 对不上 —— 配对读数**不予发布**：\n"
                         + "\n".join("   " + b for b in bad))
    return tot


def _decline_totals(per_game: dict) -> dict:
    """把逐场的 `DECLINE_KEYS` 加总（**行级**口径；对账对象是奖励侧审计账，见那段注释）。"""
    return {k: sum(g[k] for g in per_game.values()) for k in DECLINE_KEYS}


def _decline_aggregate(tot: dict, games: int) -> dict:
    """**聚合**读数（与"逐场配对"并列的那一份，`PREREGISTRATION-DAMA.md` §5 ① 的"辅"）。"""
    return {"games": int(games), "decline_rows": tot["decline_rows"], "legal_rows": tot["legal_rows"],
            "decline_rate": ((tot["decline_rows"] / tot["legal_rows"]) if tot["legal_rows"]
                             else float("nan")),
            "decline_rows_per_game": ((tot["decline_rows"] / games) if games else float("nan"))}


def _decline_selfcheck(tot: dict, tag: str) -> dict:
    """**口径自洽**（不需要奖励侧也有账就能查）：付奖 + 拦下 == 能而不为、结构检查 = 0、`legal` 全取到。"""
    g = tot["decline_paid_rows"] + tot["decline_blocked_rows"]
    bad = []
    if g != tot["decline_rows"]:
        bad.append(f"付奖行 + 拦下行 = {g} != 能而不为 {tot['decline_rows']}（累计点漏了/重了）")
    if tot["decline_missing_legal_rows"]:
        bad.append(f"`legal` 取不到的行 {tot['decline_missing_legal_rows']} != 0"
                   f"（对'能立而不立'这类判据 = **无定义** ⇒ 不猜 False）")
    for k, why in (("decline_viol_state", "付奖行那一刻公开状态位已说立直"),
                   ("decline_viol_after_decl", "付奖行在该席首次宣言之后")):
        if tot[k]:
            bad.append(f"结构检查 {k} = {tot[k]} != 0（{why}）")
    if bad:
        raise SystemExit(f"⛔ {tag}：行级「能立而不立」账**自相矛盾** —— 读数不予发布：\n"
                         + "\n".join("   " + b for b in bad))
    return tot


def _verify_decline_totals(per_game: dict, tag: str) -> dict:
    """★★ **判据与奖励对齐**的验收（本任务的核心）：行级判据的每一格 == 奖励侧审计账的对应键。

    为什么这一条是硬判据（而不是"顺便看一眼"）：`decline_rate` 之所以要新加，就是因为上一轮拿
    **手级** `dama_wins` 去当**行级**付奖行的判据 —— 两者只有 **8%** 重叠（92% 的 `dama_wins`
    来自**根本不能立直**的副露手）⇒ 推那个决策对那个读数**没有杠杆**。对齐的唯一可证伪说法 =
    **两个独立实现数同一个集合**，五个数 + `mu` **逐项相等**。⛔ 对不上就**不出读数**（先修判据）。

    独立性的边界：本函数**只看轨迹**（按 PROTOCOL §8.3/§8.4 的字段自己数），数值一律来自
    `_student_per_game`（w-ladder 的测量路径）；奖励侧的数从 `audit/<tag>.json` **读**
    （⛔ 不 import `style_reward`，也不问它怎么算的）。
    """
    au = read_audit(tag)
    if not au:
        raise SystemExit(f"⛔ 审计账不存在：{AUDIT / (tag + '.json')} —— 没有奖励侧的账就**没法**做"
                         f"「判据与奖励对齐」的对账（⛔ 不出读数）")
    ax = (au.get("axes") or [{}])[0]
    tot = _decline_selfcheck(_decline_totals(per_game), tag)
    pairs = (("decline_rows", "action_rows"),        # 奖励侧的"动作行"就是这个集合
             ("decline_rows", "declined_rows"),      # 同一件事的另一个键（要求两边都在、且都相等）
             ("legal_rows", "legal_rows"),
             ("decline_paid_rows", "paid_rows"),
             ("decline_blocked_rows", "blocked_rows"),
             ("decline_missing_legal_rows", "missing_legal_rows"),
             ("decision_rows", "n_used"))
    bad = [f"{mine:<26} 判据侧 {tot[mine]:>7} vs 审计账 `{theirs}` {ax.get(theirs)}"
           for mine, theirs in pairs if int(ax.get(theirs, -1)) != tot[mine]]
    mu = (tot["decline_rows"] / tot["decision_rows"]) if tot["decision_rows"] else float("nan")
    if not (isinstance(ax.get("mu"), (int, float))
            and abs(float(ax["mu"]) - mu) <= 1e-12):
        bad.append(f"{'mu':<26} 判据侧 {mu!r} vs 审计账 {ax.get('mu')!r}")
    if bad:
        raise SystemExit(f"⛔ {tag}：**判据与奖励对不上** —— `decline_rate` 读数不予发布（先修判据）：\n"
                         + "\n".join("   " + b for b in bad)
                         + f"\n   （审计账 {AUDIT / (tag + '.json')}，spec={au.get('spec')!r}）")
    return {"tag": tag, "audit": str(AUDIT / f"{tag}.json"), "spec": au.get("spec"),
            "axis": ax.get("axis"), "row_rule": ax.get("row_rule"), "totals": tot, "mu": mu,
            "reconciled": True}


def _paired_delta(pre: dict, post: dict, axes=PAIR_AXES) -> dict:
    """逐场配对：`Δ_g = post_rate_g − pre_rate_g`（**按场配**，不是把分子分母各自累加）。

    为什么按场：同一个场号 = 同一副牌山 + 同一批对手 + 同一座位 ⇒ 只差"学生换了个权重"，
    场间方差那一大块（牌山运气）在 Δ 里被消掉 ⇒ SE 从 ≈0.6pp 降到 ≈0.5pp。
    """
    out = {}
    for name, num, den, unit in axes:
        diffs, pres, posts = [], [], []
        for g in sorted(set(pre) & set(post)):
            a, b = pre[g], post[g]
            if a[den] <= 0 or b[den] <= 0:
                continue
            ra, rb = a[num] / a[den], b[num] / b[den]
            diffs.append(rb - ra)
            pres.append(ra)
            posts.append(rb)
        n = len(diffs)
        if n < 2:
            continue
        m = statistics.fmean(diffs)
        se = statistics.stdev(diffs) / n ** 0.5
        scale = 100.0 if unit == "pct" else 1.0
        out[name] = {"unit": unit, "unit_cn": "百分点 pp" if unit == "pct" else "点/和了",
                     "pre": statistics.fmean(pres), "post": statistics.fmean(posts),
                     "delta_pp": m * scale, "se_pp": se * scale,
                     "t": (m / se if se > 0 else float("nan")),
                     "lo_pp": (m - Z95 * se) * scale, "hi_pp": (m + Z95 * se) * scale,
                     "n_games": n}
    return out


def _check_pairing(pre_dir: Path, post_dir: Path) -> dict:
    """**配对成立的前提**：逐场 `seed` 与**学生席座位**必须与 `g01` 完全一致。

    判据不是"命令行看起来一样"：`--rotate` / `--rotate-perm` / `--policy` 的写法差一点，
    座位就会换 ⇒ 同一场里学生**摸的是另一副手牌**，那样"配对"就名不副实（牌子换了，不是策略换了）。
    """
    def load(d):
        s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
        return s, {int(g["game"]): (int(g["seed"]), next(
            (i for i, p in enumerate(g["policies"]) if "@" in p), None)) for g in s["per_game"]}
    a, ga = load(pre_dir)
    b, gb = load(post_dir)
    common = sorted(set(ga) & set(gb))
    seed_bad = [k for k in common if ga[k][0] != gb[k][0]]
    seat_bad = [k for k in common if ga[k][1] != gb[k][1]]
    ok = bool(common) and not seed_bad and not seat_bad and len(ga) == len(gb)
    res = {"pre": str(pre_dir), "post": str(post_dir), "games": len(common),
           "seed_mismatch": len(seed_bad), "student_seat_mismatch": len(seat_bad),
           "seed_base_pre": a.get("seed_base"), "seed_base_post": b.get("seed_base"),
           "ok": ok}
    if not ok:
        raise SystemExit(f"⛔ 配对被破坏（逐场 seed/学生席对不上）—— 保留轨迹、不出读数：\n"
                         f"   {res}\n   先查两条采集的 seed / --rotate-perm / --policy 写法")
    return res


def post(label: str, dry: bool = False, games: int = GAMES, workers: int = 12,
         keep_traces: bool = False) -> int:
    """★ **训练后的主读数**：拿**末代权重**在**同一 seed 族**再采一次（= `g01` 的配对基准）。

    为什么非要这一步：链内第 g 代采集的是"训过 g−1 代"的权重 ⇒ 链内最后一条 `gNN` 也**不是**
    "训过 N 代"的策略。而"这个奖励训 N 代之后立直率是多少"只能问**末代权重本身**。
    seed 直接取 `g01` 那份 `summary.json` 的 `seed_base`（⛔ 不手敲）⇒ 配对关系是**读出来的**、
    不是猜的；跑完再用 `_check_pairing` 逐场对账。

    ⚠ 只补采**一次**（末代那一份）：链内 `g02..gNN` 已经把"训过 1..N−1 代"读全了。
    """
    gens = _chain_tags(label)
    if not gens:
        raise SystemExit(f"⛔ 找不到 {TRACES / (label + '-g01')} —— 链没跑完？（`status` 看进度）")
    n = max(g for g, _ in gens)
    pre_dir = TRACES / f"{label}-g01"
    pre_sum = json.loads((pre_dir / "summary.json").read_text(encoding="utf-8"))
    seed = int(pre_sum["seed_base"])
    net = REPO / "tools" / "build" / f"{label}-g{n:02d}" / "net.bin"
    if not net.is_file():
        raise SystemExit(f"⛔ 末代权重不存在：{net}（这一代没跑完？）")
    # 四席策略串：**抄 `g01` 的第 0 场**（那是 `--policy` 的原始顺序），只把学生那一席换成末代权重
    labs = list(pre_sum["per_game"][0]["policies"])
    si = next((i for i, p in enumerate(labs) if "@" in p), None)
    if si is None:
        raise SystemExit(f"⛔ {pre_dir} 的第 0 场里找不到带 `@` 的学生席：{labs}")
    suffix = labs[si][labs[si].index("@"):]            # 例 `@0#0.5`（采样口径必须与链内一致）
    labs[si] = f"net:{net}{suffix}"
    tag_post = f"{label}-g{n:02d}-POST"
    out_dir = TRACES_POST / f"{label}-g{n:02d}"
    STYLE_VEC.mkdir(parents=True, exist_ok=True)
    TRACES_POST.mkdir(parents=True, exist_ok=True)
    PAIRED.mkdir(parents=True, exist_ok=True)
    js = STYLE_VEC / f"{tag_post}.json"
    cmd = [PY, str(REPO / "tools" / "style-vector.py"), "--run-out", str(out_dir),
           "--trainer", str(DATA / "tools" / "trainer" / "trainer.exe"),
           "--games", str(games), "--policy", ",".join(labs), "--seed", str(seed),
           "--workers", str(workers), "--keep-traces",
           "--json", str(js), "--md", str(STYLE_VEC / f"{tag_post}.md")]
    env = dict(os.environ)
    env["MAHJONG_DATA_ROOT"] = str(DATA)
    env["MAHJONG_TRAINER"] = str(DATA / "tools" / "trainer" / "trainer.exe")
    env["PYTHONPATH"] = f"{REPO / 'python'};{REPO / 'python' / '.venv' / 'Lib' / 'site-packages'}"
    env["PYTHONIOENCODING"] = "utf-8"
    print(f"[post] 补采（训练后）· 链 {label} · 末代 g{n:02d} · **seed={seed}（抄 g01 的 seed_base）**\n"
          f"[post] 学生串 {labs[si]}\n[post] 落点 {out_dir}", flush=True)
    if dry:
        print("[dry] " + " ".join(cmd))
        return 0
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"post-{tag_post}.log"
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        fh.write("[cmd] " + " ".join(cmd) + "\n")
        fh.flush()
        rc = subprocess.run(cmd, cwd=str(REPO), env=env, stdout=fh, stderr=subprocess.STDOUT).returncode
    if rc != 0:
        raise SystemExit(f"⛔ 补采失败（退出码 {rc}）—— 看 {log} 尾部；轨迹保留在原处")
    sv = json.loads(js.read_text(encoding="utf-8"))
    _assert_checks_ok(sv, f"{tag_post}（补采）")
    pre_js = STYLE_VEC / f"{label}-g01.json"
    if not pre_js.is_file():
        raise SystemExit(f"⛔ 缺 {pre_js} —— 先跑 `measure`（g01 的读数必须先算出来才能配对）")
    sv_pre = json.loads(pre_js.read_text(encoding="utf-8"))
    pair = _check_pairing(pre_dir, out_dir)
    per_pre = _student_per_game(pre_dir)
    per_post = _student_per_game(out_dir)
    _verify_pair_totals(per_pre, sv_pre, f"{label}-g01")
    _verify_pair_totals(per_post, sv, tag_post)
    # ★★ **判据与奖励对齐**（本任务的核心，见 `_verify_decline_totals`）：行级「能立而不立」的
    #   五数 + `mu` 必须与奖励侧审计账**逐项相等**；对不上 ⇒ `SystemExit`（不出读数）。
    #   ⚠ 只有**训练前**那一侧有奖励侧的账（链内的采集才挂 `MAHJONG_STYLE_BONUS`）；`post` 补采走的是
    #   `style-vector.py --run-out` 的采集路径、**不挂风格奖励** ⇒ post 侧只能做**口径自洽**那一层。
    dec_pre = _verify_decline_totals(per_pre, f"{label}-g01")
    tot_post = _decline_selfcheck(_decline_totals(per_post), tag_post)
    dec_post = {"tag": tag_post, "totals": tot_post, "audit": None, "reconciled": False,
                "note": "`post` 补采不挂风格奖励 ⇒ 奖励侧没有账；这一侧只做口径自洽"
                        "（付奖+拦下 == 能而不为、结构检查 0、`legal` 全取到）"}
    res = {"tool": "tools/w-ladder.py post", "label": label, "generations": n,
           "final_net": str(net), "student_spec": labs[si], "policy": labs,
           "games": games, "seed": seed, "log": str(log),
           "pre_tag": f"{label}-g01", "post_tag": tag_post,
           "pre_dir": str(pre_dir), "post_dir": str(out_dir), "style_vector": str(js),
           "pairing_check": pair,
           "totals_check": {k: int(student_counts(sv).get(k, -1)) for k in COUNT_KEYS},
           "decline": {"pre": dec_pre, "post": dec_post,
                       "aggregate": {"pre": _decline_aggregate(dec_pre["totals"], len(per_pre)),
                                     "post": _decline_aggregate(tot_post, len(per_post))},
                       "note": "行级「能立而不立」：分子 = 该席该行 `legal` 含 `riichi:*` 且 `chosen` "
                               "不是它（= 奖励侧付奖行的**同一集合**）；分母 = 该席该行 `legal` 含 "
                               "`riichi:*`。⛔ 与手级 `dama_wins`/`dama_rate` **刻意不同源**（那是副读数）。"},
           "paired_delta": _paired_delta(per_pre, per_post)}
    (PAIRED / f"{tag_post}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2),
                                             encoding="utf-8")
    pd = res["paired_delta"].get("riichi_rate") or {}
    print(f"[post] ✓ 配对通过（{pair['games']} 场，seed 不一致 {pair['seed_mismatch']}、"
          f"学生席不一致 {pair['student_seat_mismatch']}）"
          f"｜立直率 {100 * pd.get('pre', float('nan')):.3f}% → {100 * pd.get('post', float('nan')):.3f}%"
          f"＝ **Δ {pd.get('delta_pp', float('nan')):+.2f}pp**（SE {pd.get('se_pp', float('nan')):.2f}，"
          f"t={pd.get('t', float('nan')):.2f}，95% CI "
          f"[{pd.get('lo_pp', float('nan')):+.2f},{pd.get('hi_pp', float('nan')):+.2f}]）", flush=True)
    # ★★ 行级「能立而不立」：**先贴对账**（判据 vs 奖励），再贴配对读数（`PREREGISTRATION-DAMA.md` §5 ①）。
    dp, dt = dec_pre["totals"], tot_post
    for tag, t, rec in ((dec_pre["tag"], dp, "✓ 与审计账逐项相等"), (tag_post, dt, "口径自洽（无审计账）")):
        print(f"[post] ★ 行级能立而不立 · {tag}：`legal` 含 `riichi:*` {t['legal_rows']}"
              f"（其中真的选了 {t['legal_rows'] - t['decline_rows']}）｜未选它 {t['decline_rows']}"
              f"（= 付奖行候选）｜真付奖 {t['decline_paid_rows']}｜被 `no_deal` 拦下 "
              f"{t['decline_blocked_rows']}｜结构检查 {t['decline_viol_state']}/"
              f"{t['decline_viol_after_decl']}｜μ_行 {t['decline_rows'] / max(t['decision_rows'], 1):.8f}"
              f" ⇒ {rec}", flush=True)
    dd = res["paired_delta"].get("decline_rows_per_game") or {}
    dr = res["paired_delta"].get("decline_rate") or {}
    ap, aq = res["decline"]["aggregate"]["pre"], res["decline"]["aggregate"]["post"]
    print(f"[post] ★★ **主判据** `decline_rows_per_game`（每场每席行数）："
          f"{dd.get('pre', float('nan')):.4f} → {dd.get('post', float('nan')):.4f}"
          f"＝ **Δ {dd.get('delta_pp', float('nan')):+.4f}**（SE {dd.get('se_pp', float('nan')):.4f}，"
          f"t={dd.get('t', float('nan')):.2f}，95% CI "
          f"[{dd.get('lo_pp', float('nan')):+.4f},{dd.get('hi_pp', float('nan')):+.4f}]，n={dd.get('n_games')}）", flush=True)
    print(f"[post] ★ 辅判据 `decline_rate`（逐场配对）：Δ {dr.get('delta_pp', float('nan')):+.3f}pp"
          f"（SE {dr.get('se_pp', float('nan')):.3f}，95% CI "
          f"[{dr.get('lo_pp', float('nan')):+.3f},{dr.get('hi_pp', float('nan')):+.3f}]，n={dr.get('n_games')}）"
          f"｜**聚合**口径 {ap['decline_rate']:.4%} → {aq['decline_rate']:.4%}"
          f"（{ap['decline_rows']}/{ap['legal_rows']} → {aq['decline_rows']}/{aq['legal_rows']}）"
          f"＝ Δ {(aq['decline_rate'] - ap['decline_rate']) * 100:+.3f}pp", flush=True)
    print(f"[post] 落盘 {PAIRED / (tag_post + '.json')}")
    if not keep_traces:
        mb = 0.0
        k = 0
        for f in out_dir.glob("g*.jsonl"):
            mb += f.stat().st_size / 2 ** 20
            f.unlink()
            k += 1
        print(f"[post] 删轨迹 {k} 个（{mb:,.0f} MB）；保留 summary.json + 风格向量 + 配对账")
    return 0


# ---------------------------------------------------------------------------
# ②c quality：**轨迹侧的伴随质量量**（`riichi` 轴的稀释判据）—— 与 `gate.py` 的新护栏同构
# ---------------------------------------------------------------------------
#: ★ 为什么在**轨迹侧**判、而不是进 `gate.py`（2026-10-10）：`riichi` 轴的两个量
#: **不在 `summary.json` 里** —— `summary.by_policy` 只有 和了率 / 放铳率 / 打点 / 收支 / 顺位
#: 这几条"结果轴"，而 立直率 / **立直后和了率** 是从轨迹的 `obs.riichi` / `chosen=riichi:*`
#: 数出来的（口径 = `tools/style-vector.py` 的 `riichi_win_rate` = `riichi_wins / riichi_hands`）。
#: 闸门读的是 `summary.json`（`arena.judge_series`）⇒ 它**看不到**这个量。
#: 所以它只能在**测量路径**上加一条**判据式**检查：训练前/训练后（或候选/现任）**同 seed** 的
#: **逐场配对 Δ**，按预注册容忍带**单侧**判"稀释是否越界"（⛔ 只否掉"变差"）。
COMPANION_METRIC = "riichi_win_rate"
#: ★ 已**预注册**的伴随质量量（`--metric` 只许从这张表里选；⛔ 表外的一律报错）。
#: `riichi_win_rate` = `riichi` 轴（`PROBE-dec6170.md`）；`win_rate` = **`meld` 轴** ——
#: `PREREGISTRATION-MELD.md` §2 明写："副露 = 开手牌换速度，最可能的『买副露卖什么』是**和了**与**放铳**"
#: ⇒ 该轴的伴随量是**和了率**（`wins`），轨迹侧对应 `win_rate`（同一对分子分母 `wins/seat_hands`）。
#: `dama_rate` = **`dama` 轴**（`PREREGISTRATION-DAMA.md` §2）：付奖行是"能立而不立"，
#: 最该盯的伴随量是**默听/立直取向本身**（`dama_wins / wins`；主配对量另给"每场每席 `dama_wins` 计数"）。
#: ★ `decline_rate` = **同一根 `dama` 轴的"对齐后"判据**（`PREREGISTRATION-DAMA.md` §5 ①）：
#: 它也是**行级**量、分子集合**就是奖励侧的付奖行**，所以既能当主判据、也能当"这笔钱有没有
#: 真的推上去"的护栏读数（⛔ 表外的一律报错；加进来只是**多一个选项** ⇒ 缺省口径逐字节不变）。
COMPANION_METRICS: tuple[str, ...] = ("riichi_win_rate", "win_rate", "dama_rate", "decline_rate")
#: 预注册容忍带（**百分点 pp**，单侧）。★ 越界判据 2026-10-10 修过一次（见 `companion_verdict`）：
#: 旧规则"CI 下界 ≥ −tol"在 **SE 大**时**必然假警**（实测 `dama_rate` Δ=+0.30pp、SE=1.32pp、tol=2pp
#: ⇒ 旧规则报"越界"）。新规则 = 「**CI 上界 < 0**」**或**「**点估计 < −tol**」。
COMPANION_TOL = 2.0
#: 判据行里要用的字段名（与 `gate.guard_row` 的 `guards[]` **同名同义** ⇒ 两边读数可以直接并排放）。
COMPANION_KEYS = ("metric", "unit", "tol", "delta", "lo", "hi", "ok")


def _companion_axis(metric: str):
    """在 `PAIR_AXES` 里找该指标的 `(维, 分子键, 分母键, 单位)` —— ⛔ 不许另立一套分子/分母。"""
    for ax in PAIR_AXES:
        if ax[0] == metric:
            return (ax,)
    raise SystemExit(f"⛔ 伴随质量量认不出：{metric}（可判的只有 {[a[0] for a in PAIR_AXES]}；"
                     f"⛔ 别在这里另立一套分子/分母，口径只在 `PAIR_AXES`／`style-vector.py` 里有一份）")


def companion_verdict(delta: float, se: float, n: int, *, metric: str = COMPANION_METRIC,
                      tol: float = COMPANION_TOL, unit: str = "pp", sources: list | None = None,
                      combine: str = "") -> dict:
    """**单侧**判据行：`ok ⇔ (CI 上界 ≥ 0) ∧ (点估计 ≥ −tol)`（与 `gate.guard_row` 同族）。

    ⛔ **为什么不是"CI 下界 ≥ −tol"**（2026-10-10 修，实测假警）：那条规则在 **SE 大**时**必然假警**
    —— 实测 `dama_rate` Δ=**+0.30pp**、SE=**1.32pp**、tol=2pp ⇒ 下界 −2.29 < −2 就判"越界"，
    可**点估计是正的**（这个量一个字都没说在退化；区间宽只是"没测准"，不是"变差了"）。
    ⇒ 改成两条**并列**的越界条件（满足任一即越界，退出码 3）：
      ① **CI 上界 < 0** —— 整个区间都在负侧，这是**有证据**的退化（哪怕幅度很小）；
      ② **点估计 < −tol** —— 幅度真的越过容忍带（⛔ 不许拿宽 CI 藏一个大负数）。
    **点估计为正 + CI 宽 ⇒ 不报**（这正是旧规则唯一会假警的那一格）。
    ⛔ 单侧的本意不变：只否掉"**变差**"（稀释变好不该被这条卡住）；退出码约定不变
    （**0 = 不越界 / 3 = 越界**）；字段一个不加、顺序一字不动 ⇒ 同一份输入的两份账仍可**逐字节**对比。
    """
    lo, hi = float(delta) - Z95 * float(se), float(delta) + Z95 * float(se)
    bad = bool(hi < 0.0) or bool(float(delta) < -float(tol))
    return {"metric": metric, "unit": unit, "tol": float(tol), "side": "one-sided",
            "delta": float(delta), "lo": lo, "hi": hi, "se": float(se), "n_games": int(n),
            "ok": not bad, "combine": combine, "sources": list(sources or [])}


def companion_combine(rows: list) -> tuple:
    """多份配对账的**逆方差加权固定效应**合并（与 `repro-pairs.py` 同一条）。

    ⚠ 为什么不是"把逐场差分并起来"：`paired/*.json` 里只留了**统计量**（Δ/SE/n），逐场值没留
    ⇒ 能做的合法合并只有逆方差加权。把"逐场值"存下来是另一件事（要改 `post` 的产物格式），
    ⛔ 本轮**没有**改它（改动最小、且已有那份账就是本任务的判据输入）。
    """
    w = [1.0 / (float(r["se"]) ** 2) for r in rows]
    sw = sum(w)
    delta = sum(wi * float(r["delta"]) for wi, r in zip(w, rows)) / sw
    return delta, (1.0 / sw) ** 0.5, sum(int(r["n_games"]) for r in rows)


def _companion_from_paired(paths: list, metric: str = COMPANION_METRIC) -> list:
    """读**已有**的配对账（`w-ladder.py post` 写的 `paired/*.json`）里的伴随质量量。"""
    out = []
    for p in paths:
        f = Path(p)
        if not f.is_file():
            raise SystemExit(f"⛔ 配对账不存在：{f}")
        d = json.loads(f.read_text(encoding="utf-8"))
        blk = ((d.get("paired_delta") or {}).get(metric))
        if not blk:
            raise SystemExit(f"⛔ {f.name} 里没有 `paired_delta.{metric}` —— "
                             f"这份账是拿别的口径算的（有：{sorted((d.get('paired_delta') or {}))}）")
        out.append({"source": str(f), "label": d.get("label") or f.stem,
                    "delta": float(blk["delta_pp"]), "se": float(blk["se_pp"]),
                    "n_games": int(blk["n_games"])})
    return out


def _companion_from_dirs(pre_dir: Path, post_dir: Path,
                         metric: str = COMPANION_METRIC) -> list:
    """现算：两条**同 seed** 的采集 ⇒ 学生的**逐场配对 Δ**（口径 = `style-vector.py`）。

    ⛔ 逐场账的**整数合计必须与 `style-vector.py` 的 `counts` 逐项相等**（`_verify_pair_totals`）
    —— 对不上就不出数（两套实现各算一套、还都写进报告，是这类工具最容易犯的错）。
    """
    pre_sum = pre_dir / "summary.json"
    if not pre_sum.is_file():
        raise SystemExit(f"⛔ {pre_dir} 里没有 summary.json —— 无法判定'这两条采集是不是同 seed'")
    pre_sum = json.loads(pre_sum.read_text(encoding="utf-8"))
    seed = int(pre_sum["seed_base"])
    pair = _check_pairing(pre_dir, post_dir)
    per_pre = _student_per_game(pre_dir)
    per_post = _student_per_game(post_dir)
    if not per_pre or not per_post:
        raise SystemExit(f"⛔ 轨迹已被删（`g*.jsonl` 不在）⇒ 逐场配对算不出来；"
                         f"改用 `--paired <已算好的 paired/*.json>`（seed_base={seed}）")
    for tdir, per in ((pre_dir, per_pre), (post_dir, per_post)):
        js = STYLE_VEC / f"{tdir.name}.json"
        if js.is_file():
            _verify_pair_totals(per, json.loads(js.read_text(encoding="utf-8")), tdir.name)
        else:
            _verify_pair_totals(per, style_vector(tdir, tdir.name), tdir.name)
    blk = (_paired_delta(per_pre, per_post, axes=_companion_axis(metric))
           .get(metric))
    if not blk:
        raise SystemExit(f"⛔ {metric} 的配对算不出来（公共场次 < 2；"
                         f"seed_base 前 {seed} / 后 {pair['seed_base_post']}）")
    return [{"source": f"{pre_dir} → {post_dir}", "label": post_dir.name,
             "delta": float(blk["delta_pp"]), "se": float(blk["se_pp"]),
             "n_games": int(blk["n_games"]), "pairing_check": pair}]


def quality(paired: list, pre: str, post: str, tol: float, metric: str, out: str) -> int:
    """★ 轨迹侧伴随质量量的**单侧判据**（见本节开头的"为什么在轨迹侧判"）。

    ⚠ `--metric` 只许从 **`COMPANION_METRICS`**（= 已预注册的那几个）里选：`meld` 轴的伴随量是
    `win_rate`（`PREREGISTRATION-MELD.md` §2），`riichi` 轴的是 `riichi_win_rate`。

    退出码：**0 = 稀释没越界**；**3 = 越界**（脚本据此止损）；其它 = 出错。
    """
    if bool(pre) != bool(post):
        raise SystemExit("⛔ --pre 与 --post 必须**成对**给（同 seed 的训练前 / 训练后）")
    if pre:
        rows = _companion_from_dirs(Path(pre), Path(post), metric)
        combine = "逐场配对（同 seed，一条链）"
    elif paired:
        rows = _companion_from_paired(paired, metric)
        combine = "逆方差加权固定效应（多份配对账）"
    else:
        raise SystemExit("⛔ 要么给 `--pre <dir> --post <dir>`（现算逐场配对），"
                         "要么给一个或多个 `--paired <paired/*.json>`")
    if metric not in COMPANION_METRICS:
        raise SystemExit(f"⛔ 预注册过的伴随质量量只有 {'/'.join(COMPANION_METRICS)}（收到 {metric}）—— "
                         f"别的量要先进预注册再放开")
    delta, se, n = companion_combine(rows)
    row = companion_verdict(delta, se, n, metric=metric, tol=tol, combine=combine,
                            sources=[r["source"] for r in rows])
    row["rows"] = rows
    row["warning"] = ("⛔ 这不是闸门：主判据仍只看 `mahjong_ml/v4/gate.py`（多套牌山 + 逐场配对 CI）。"
                      "本行是**测量路径**上的伴随质量量检查 —— 它回答「稀释越界了吗」，"
                      "不回答「这一代要不要采纳」。")
    print(f"== 轨迹侧伴随质量量：**{metric}**（{combine}）==")
    for r in rows:
        print(f"  · {r['label']:<28} Δ={r['delta']:+7.2f}pp  SE={r['se']:5.2f}  "
              f"n={r['n_games']:>5}  ← {r['source']}")
    print(f"  合并：Δ={row['delta']:+.3f}pp  CI[{row['lo']:+.3f},{row['hi']:+.3f}]  "
          f"n={row['n_games']}  ⇒ {'不越界' if row['ok'] else '**越界（稀释）**'}"
          f"（预注册：单侧，**越界 ⇔ CI 上界 < 0 或 点估计 < −{tol:g}pp**；"
          f"点估计为正 + CI 宽 ⇒ 不报）")
    v = {k: row[k] for k in COMPANION_KEYS}
    print(f"  判据行：{json.dumps(v, ensure_ascii=False)}")
    dst = Path(out) if out else (STAGE / "companion-quality.json")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  落盘 {dst}")
    return 0 if row["ok"] else 3


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
    """**臂对臂**闸门：`w=W_TOP` 的 2 条链 × `w=0` 的 2 条链 = **2 组配对**。

    ⚠ 与"各自 vs 现任"不同：这里现任就是**对照臂的终点**（`w=0` 那条链），
    所以要测的位移**不含现任漂移**这个混杂项（预注册 §4 ②）。
    ⚠ 只跑"最高档 vs 对照"这一个方向（`PREREGISTRATION.md` **§7** 的"节约算力"偏离记录：
    上级 2026-10-10 追加口径；两组**共用**同一批 4 套牌山 `40970001..4` ⇒ 仍可比）。
    """
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
                fh.write("[cmd] " + " ".join(cmd) + "\n")
                fh.flush()
                rc = subprocess.run(cmd, cwd=str(REPO), env=env, stdout=fh,
                                    stderr=subprocess.STDOUT).returncode
            pv = DATA / "gate" / tag / "verdict.json"
            if not pv.is_file():
                print(f"⛔ 闸门没落盘 verdict.json（退出码 {rc}）—— 看 {log} 尾部", file=sys.stderr)
                for ln in log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]:
                    print("   " + ln, file=sys.stderr)
                return 3
            v = json.loads(pv.read_text(encoding="utf-8"))
            n = _clean_jsonl(tag)
            print(f"[gate] 退出码 {rc}；**{v['delta']:+.3f}** "
                  f"CI[{v['lo']:+.3f},{v['hi']:+.3f}] n={v['n']}"
                  f"｜护栏 rank Δ={v.get('guard_delta'):+.2f} "
                  f"CI[{v.get('guard_lo'):+.2f},{v.get('guard_hi'):+.2f}] "
                  f"（{'不破' if v.get('guard_ok') else '破了'}）；"
                  f"逐套 {[round(x, 3) for x in (v.get('wall_deltas') or [])]}；"
                  f"删掉 {n} 个闸门轨迹（保留 summary/verdict）", flush=True)
            out.append({"tag": tag, "candidate_chain": topc["label"],
                        "incumbent_chain": ctlc["label"], "returncode": rc,
                        "cleaned_jsonl": n, "log": str(log), "walls": list(WALLS),
                        "games": GATE_GAMES, "verdict": v})
    p = STAGE / "gate-arm-vs-arm.json"
    if not dry:
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[gate] 落盘 {p}（{len(out)} 组配对）")
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
          "## ⛔ 口径（先读这一段再看数）", "",
          "| 口径 | 含义 | 能不能当效果读数 |", "| --- | --- | --- |",
          "| ⛔ `pre` | `g01`：采集用的是 **`--init`/现任**的权重 | **不能** —— 每一臂的 `g01` 都是"
          "同一个现任，差多少只是采样噪声 |",
          "| ✓ `post` | 链内 `gNN`（N ≥ 2）：采集用的是「训过 N−1 代」的权重 | 能（同一链的代数趋势） |",
          "| ✓ `post_final` | `post` 子命令用**末代权重**在**同一 seed 族**补采一次 | 能，且与 `pre` "
          "**逐场配对**（SE ≈ 0.5pp） |", ""]
    warns = d.get("phase_warnings") or []
    if warns:
        md += ["**⚠ 本表里的训练前行（⛔ 不许静默当效果读数）**：", ""]
        md += [f"- {w}" for w in warns]
        md += [""]
    main = d.get("main_readings") or {}
    if main:
        md += ["## ★ 主读数：训练前 vs 训练后（每条链）", "",
               "| 链 | 训练前 ⛔ | 主读数（训练后）✓ | 口径 | 配对 Δ（逐场）|", "| --- | --- | --- | --- | --- |"]
        for lab, m in main.items():
            pd = None
            for r in d["rows"]:
                if r["label"] == lab and r.get("paired"):
                    pd = r["paired"]["paired_delta"].get("riichi_rate")
            txt = "（没补采 ⇒ 跑 `post`）"
            if pd:
                txt = (f"立直率 {pd['delta_pp']:+.2f}pp（SE {pd['se_pp']:.2f}，"
                       f"t={pd['t']:.2f}，95% CI [{pd['lo_pp']:+.2f},{pd['hi_pp']:+.2f}]，"
                       f"n={pd['n_games']} 场）")
            elif m.get("main_reading_phase") == PHASE_POST_FINAL:
                txt = "（有补采行，但缺逐场配对账 —— 配对账由 `post` 写 `paired/*.json`）"
            md.append(f"| {lab} | {_pct(m.get('pre_reading_riichi_rate'))}"
                      f"（{m.get('pre_reading_tag')}） | "
                      f"{_pct(m.get('main_reading_riichi_rate'))}（{m.get('main_reading_tag')}） | "
                      f"{m.get('main_reading_phase')} | {txt} |")
        md += [""]
    md += ["## 逐链风格向量", "",
           "| 链 | w | 代 | **口径** | 小局 | 立直率 | 副露率 | 和了率 | 放铳率 | 平均打点 | 和了巡 | 立直巡 | 默听率 | 立直后和了率 |",
           "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in d["rows"]:
        s = r["style"]
        md.append(f"| {r['label']} | {r['w']} | {r.get('generation_label') or r['generation']} "
                  f"| {r['phase_label']} | {s.get('seat_hands')} "
                  f"| {_pct(s.get('riichi_rate'))} | {_pct(s.get('meld_rate'))} "
                  f"| {_pct(s.get('win_rate'))} | {_pct(s.get('deal_rate'))} "
                  f"| {_num(s.get('avg_win_score'), 0)} | {_num(s.get('avg_win_turn'))} "
                  f"| {_num(s.get('avg_riichi_turn'))} | {_pct(s.get('dama_rate'))} "
                  f"| {_pct(s.get('riichi_win_rate'))} |")
    md += ["", "## ★ 配对（训练前 `g01` vs 训练后补采；逐场同牌山/同对手/同座位）", "",
           "| 链 | 轴 | 训练前 | 训练后 | Δ | 逐场 SE | t | 95% CI | 配到场数 |",
           "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |"]
    for r in d["rows"]:
        p = r.get("paired")
        if not p:
            continue
        for k, v in p["paired_delta"].items():
            md.append(f"| {r['label']} | {k} *({v['unit_cn']})* | {v['pre']:.4f} | {v['post']:.4f} "
                      f"| {v['delta_pp']:+.2f} | {v['se_pp']:.2f} "
                      f"| {v['t']:.2f} | [{v['lo_pp']:+.2f}, {v['hi_pp']:+.2f}] | {v['n_games']} |")
    md += ["", "## 逐链采集读数（1000 场自对弈，对风格桌；`se` = 逐场标准误）", "",
           "| 链 | w | 代 | 口径 | 采集顺位点 (se) | 每场和了 (se) | 每场放铳 (se) | 每场和了点 (se) | 和了率 | 放铳率 | 平均打点 |",
           "| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in d["rows"]:
        c = r["collect"]

        def cs(k, nd=2):
            v, se = c.get(k), c.get(k + "_se")
            return "n/a" if v is None else f"{v:.{nd}f} ({se:.{nd}f})"
        md.append(f"| {r['label']} | {r['w']} | {r.get('generation_label') or r['generation']} "
                  f"| {r['phase_label']} | {cs('rank_points')} "
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
    p = sub.add_parser("probe", help="判别性探针：单链 N 代（缺省 1）、指定 w（CONTINGENCY §7 台阶 2）")
    p.add_argument("--w", type=float, required=True, help="风格奖励权重（如 14000）")
    p.add_argument("--label", default="", help="链标签（缺省 v4-wl-probe-w<w>-c1）")
    p.add_argument("--seed", type=int, default=PROBE_SEED)
    # ★ 复现：一次跑 **多条链**（`--seeds` 与 `--labels` 一一对应，`--labels` 是**完整**链标签）。
    #   缺省都为空 ⇒ 老行为（单链，拼 `-c1`）**逐位不变**。
    p.add_argument("--seeds", default="",
                   help="逗号分隔的**多**链 seed（复现：每臂 ≥2 条链）—— 与 --labels 一一对应")
    p.add_argument("--labels", default="",
                   help="逗号分隔的**完整**链标签（如 v4-wl-dec6170-4g-c2,v4-wl-dec6170-4g-c3）")
    p.add_argument("--mode", default="hand", choices=list(v4sr.MODES),
                   help="付奖模式：`hand`（缺省，小局级常数）/ `decision`（逐决策；"
                        "奖励只落在真的做了那个动作的那一行）")
    # ★ **换轴**（`PREREGISTRATION-MELD.md` §0：只换奖励轴与剂量）：缺省 `riichi`+`no_deal`
    #   ⇒ 与加这两个参数之前**逐位相同**。校验在 `probe()` 里（复用 `style_reward.parse_bonus`）。
    p.add_argument("--axis", default="riichi",
                   help=f"付奖轴（缺省 riichi）；`decision` 模式下只能是 "
                        f"{'/'.join(v4sr.ROW_ACTION_AXES)} 之一（如 `meld` = 副露率轴）")
    p.add_argument("--pair", default="no_deal",
                   help=f"成对质量轴（缺省 no_deal）：{'/'.join(sorted(v4sr.PAIR_KEYS))}/none")
    p.add_argument("--generations", type=int, default=PROBE_GENERATIONS,
                   help=f"代数（缺省 {PROBE_GENERATIONS} = 原单链 1 代，逐位不变）。"
                        "⚠ >1 时**第 1 代仍是训练前**，末代要用 `post` 补采才算训练后读数")
    p.add_argument("--dry-run", action="store_true")
    sub.add_parser("measure", help="逐代风格向量 + 臂表（自动打「训练前/训练后」标）")
    po = sub.add_parser("post", help="★ 用**末代权重**在同一 seed 族补采一次 = 训练后主读数")
    po.add_argument("--label", required=True, help="链标签（如 v4-wl-dec6170-4g-c1）")
    po.add_argument("--games", type=int, default=GAMES, help=f"补采场数（缺省 {GAMES}）")
    po.add_argument("--workers", type=int, default=12)
    po.add_argument("--keep-traces", action="store_true", help="读完**不删**补采的 g*.jsonl")
    po.add_argument("--dry-run", action="store_true")
    q = sub.add_parser("quality", help="★ 轨迹侧伴随质量量（立直后和了率）的**单侧**判据 —— "
                                      "稀释越界了吗（⛔ 不是闸门）")
    q.add_argument("--paired", action="append", default=[],
                   help="**已有**的配对账 `paired/*.json`（可重复 ⇒ 逆方差加权合并）")
    q.add_argument("--pre", default="", help="训练前 / 现任的采集目录（必须与 --post 成对）")
    q.add_argument("--post", default="", help="训练后 / 候选的采集目录（必须与 --pre 成对；同 seed）")
    q.add_argument("--tol", type=float, default=COMPANION_TOL,
                   help=f"预注册容忍带（pp，**单侧**；缺省 {COMPANION_TOL}）")
    q.add_argument("--metric", default=COMPANION_METRIC,
                   help=f"伴随质量量（缺省 {COMPANION_METRIC}）；预注册过的只有 "
                        f"{'/'.join(COMPANION_METRICS)}")
    q.add_argument("--out", default="", help=f"落盘（缺省 {STAGE / 'companion-quality.json'}）")
    g = sub.add_parser("gate", help="臂对臂闸门")
    g.add_argument("--dry-run", action="store_true")
    sub.add_parser("report", help="拼 Markdown 表")
    sub.add_parser("status", help="看进度")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        return run(a.only, a.dry_run)
    if a.cmd == "probe":
        return probe(a.w, a.label or f"v4-wl-probe-w{a.w:g}-c1", a.seed, a.dry_run, a.mode,
                     a.generations,
                     seeds=[x for x in a.seeds.split(",") if x.strip()],
                     labels=[x.strip() for x in a.labels.split(",") if x.strip()],
                     axis=a.axis, pair=a.pair)
    if a.cmd == "measure":
        measure()
        return 0
    if a.cmd == "post":
        return post(a.label, a.dry_run, a.games, a.workers, a.keep_traces)
    if a.cmd == "quality":
        return quality(a.paired, a.pre, a.post, a.tol, a.metric, a.out)
    if a.cmd == "gate":
        return gate(a.dry_run)
    if a.cmd == "report":
        return report()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
