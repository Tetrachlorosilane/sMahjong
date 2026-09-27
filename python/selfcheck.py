#!/usr/bin/env python
"""训练侧自检（Python 侧的 `--selftest`）。

    python\\.venv\\Scripts\\python.exe python\\selfcheck.py

判据风格与仓库一致：`[ok]/[FAIL]` 逐条打印，末尾 `SELFCHECK PASS|FAIL`，退出码 0/1。
覆盖三类只靠"看代码"看不出问题的地方：
  · **统计口径**（符号检验的精确值、自助法的可复现性、样本量反推的单调性）；
  · **配对评测**（按 seed / 按座位的配对是否真的对上；缺 `rank_points` 必须报错而不是悄悄退化成均值）；
  · **两条纪律**（`guard` 的控制律与阈值、`paths` 的配额与"最旧的先删"），
    再钉一条"**实现只有一份**"：`verify_env` 必须复用 `guard` 的类，不许各写一份。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ⚠ 受限沙箱下**系统临时目录不可写**，而且 `tempfile.mkdtemp`/`TemporaryDirectory` 建出来的目录
#   **自己也写不进去**（实测：在 mkdtemp 的目录里再 mkdir → WinError 5；手写 mkdir 正常）。
#   所以自检的 scratch 一律在工作区内手写（`.tmp/` 已 gitignore）。
# ⚠ 而且必须**独占一个子目录**：收尾的 `rmtree` 只该删自己建的东西 —— 曾经把别的进程写在
#   `.tmp/` 下的采集日志一起删了（自检不得损坏它不是自己建的文件）。
SCRATCH = Path(__file__).resolve().parent / ".tmp" / "selfcheck"


def scratch(name: str) -> Path:
    d = SCRATCH / name
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def same_bytes(a: Path, b: Path) -> bool:
    """两个紧凑集目录里的同名文件是否**逐字节相同**（"并行只改调度、不改产出"的判据）。"""
    fa = {p.name: p.read_bytes() for p in sorted(Path(a).iterdir()) if p.is_file()}
    fb = {p.name: p.read_bytes() for p in sorted(Path(b).iterdir()) if p.is_file()}
    return fa == fb


from mahjong_ml import bc, dataset as ds, features, guard, nets, paths   # noqa: E402
from mahjong_ml import eval as ml_eval                    # noqa: E402
from mahjong_ml import dagger                             # noqa: E402
from mahjong_ml import hybrid as hyb                      # noqa: E402
from mahjong_ml import offline_rl, rewards                # noqa: E402
from mahjong_ml import awr                                # noqa: E402
from mahjong_ml import league as ml_league                  # noqa: E402
from mahjong_ml import ppo                                # noqa: E402
from mahjong_ml import budget as ml_budget                 # noqa: E402
import torch                                              # noqa: E402

fails: list[str] = []
count = 0


def ok(cond: bool, name: str, detail: str = "") -> None:
    global count
    count += 1
    print(f"  [{'ok  ' if cond else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def eq(name: str, got, want, tol: float | None = None) -> None:
    if tol is None:
        ok(got == want, name, f"got={got!r} want={want!r}")
    else:
        ok(abs(got - want) <= tol, name, f"got={got!r} want={want!r}±{tol}")


# ---------------------------------------------------------------- ① 统计口径

print("== 统计口径 ==")
eq("符号检验：10 个全正 → 2×0.5^10", ml_eval.sign_test_p(np.ones(10)), 2 * 0.5 ** 10, 1e-12)
eq("符号检验：全为 0 → p=1（无信息）", ml_eval.sign_test_p(np.zeros(5)), 1.0)
eq("符号检验：正负各半 → p=1", ml_eval.sign_test_p(np.array([1, -1, 1, -1])), 1.0, 1e-12)
# 自助法：同输入两次结果必须一致（固定 RNG 种子是"可复现"的硬要求）
v = np.array([1.0, 2, 3, 4, 5, 6, 7, 8])
c1, c2 = ml_eval.bootstrap_ci(v), ml_eval.bootstrap_ci(v)
ok(c1 == c2, "自助法置信区间可复现（同输入 → 同结果）", f"{c1} == {c2}")
ok(c1[0] <= v.mean() <= c1[1], "置信区间包含样本均值", f"{c1} ∋ {v.mean()}")
ok(ml_eval.required_n(32.0, 2.0) == 2010, "样本量反推：sd=32、Δ=2 → 2010 场（α=0.05、功效 80%）",
   f"got={ml_eval.required_n(32.0, 2.0)}")
ok(ml_eval.required_n(32.0, 2.0, power=0.5) == 984,
   "同一输入按 **50% 功效** 才是 984 场（旧口径 —— 就是它把所需场次低估了一半）",
   f"got={ml_eval.required_n(32.0, 2.0, power=0.5)}")
ok(ml_eval.required_n(32.0, 4.0) < ml_eval.required_n(32.0, 2.0), "Δ 越大 → 需要的场次越少")
eq("Δ=0 时不给出场次（避免除零）", ml_eval.required_n(32.0, 0.0), 0)

# ---------------------------------------------------------------- ② 配对评测

print("== 配对评测 ==")
if True:
    td = scratch("eval")
    d = td / "run"
    d.mkdir(parents=True, exist_ok=True)
    # 合成一次 4 场 × 2 策略的 run：A 的顺位点恒比 B 高 20（A 坐 0/3 号位，B 坐 1/2 号位）
    def game(g, seed):
        return {"game": g, "seed": seed, "policies": ["A", "B", "B", "A"],
                "final_scores": [40000, 10000, 10000, 40000],
                "placement": [1, 2, 2, 1],
                "rank_points": [30.0, 10.0, 10.0, 30.0], "hands": 1, "ryukyoku": 0}
    pg = [game(g, 1000 + g) for g in range(4)]
    (d / "summary.json").write_text(json.dumps({
        "games": 4, "hands": 4, "seed_base": 1000, "workers": 2,
        "by_policy": {"A": {"games": 8, "avg_place": 1.5, "avg_rank_points": 25.0,
                            "win_rate": .2, "deal_in_rate": .1, "avg_delta": 0, "avg_win_score": 0},
                      "B": {"games": 8, "avg_place": 2.5, "avg_rank_points": 0.0,
                            "win_rate": .1, "deal_in_rate": .2, "avg_delta": 0, "avg_win_score": 0}},
        "per_game": pg}), encoding="utf-8")
    run = ml_eval.load_run(d)
    eq("读入场数", run.games, 4)
    sa, sb = ml_eval.per_game_series(run, "A", "rank_points"), ml_eval.per_game_series(run, "B", "rank_points")
    pr = ml_eval.paired_test(sa, sb, "A", "B", "rank_points")
    eq("按 seed 配对的样本量", pr.n, 4)
    eq("配对差值 = A−B", pr.mean, 20.0, 1e-9)
    ok(pr.p < 0.2, "4 场全胜 → p 很小但不为 0", f"p={pr.p}")
    ss = ml_eval.seat_series(run, "A", "rank_points")
    eq("按座位配对的键是 (seed, 座位)", sorted(ss)[0], (1000, 0))
    eq("座位系列条数 = 场数 × 该策略座位数", len(ss), 8)
    txt = ml_eval.format_single(run, "rank_points")
    ok("顺位点" in txt and "95%CI" in txt, "报告里含顺位点与置信区间")
    # 缺 rank_points 必须**报错**（不许悄悄退化成平均值比较）
    bad = Path(td) / "bad"
    bad.mkdir()
    (bad / "summary.json").write_text(json.dumps({"games": 1, "per_game": [
        {"game": 0, "seed": 1, "policies": ["A"] * 4, "placement": [1, 2, 3, 4]}]}), encoding="utf-8")
    try:
        ml_eval.load_run(bad)
        ok(False, "缺 rank_points 时报错")
    except ValueError as e:
        ok("rank_points" in str(e), "缺 rank_points 时报错（提示重跑）", str(e)[:48])

# ---------------------------------------------------------------- ③ guard 控制律

print("== guard（CPU/GPU 纪律）==")
eq("核预算 = floor(核数 × 0.75)", guard.cores_budget(0.75), int((os.cpu_count() or 1) * 0.75))
eq("训练线程封顶生效", guard.apply_cpu_limit(4), 4)
ok(guard.SELFPLAY_WORKERS <= guard.cores_budget(),
   "采集 workers 不越界", f"{guard.SELFPLAY_WORKERS} ≤ {guard.cores_budget()}")


class _FakeMonitor:
    def __init__(self, level: float):
        self.level = level

    def mean(self, window_s: float) -> float:
        return self.level


m = _FakeMonitor(95.0)
dc = guard.DutyCycle(m, window_s=0.0, adjust_every_s=0.0, step_s=0.01, max_sleep_s=0.05)
for _ in range(10):
    dc.tick()
ok(dc.sleep > 0, "超限 → 加 sleep", f"sleep={dc.sleep:.3f}s")
m.level = 30.0
for _ in range(10):
    dc.tick()
eq("余量充足 → sleep 收回 0", dc.sleep, 0.0, 1e-12)
m.level = 95.0
for _ in range(100):
    dc.tick()
ok(dc.sleep <= 0.05 + 1e-12, "sleep 有上限（不会把训练卡死）", f"sleep={dc.sleep:.3f}s")

# 实现只有一份：verify_env 必须复用 guard 的类（否则两处节流逻辑会漂）
import verify_env                                        # noqa: E402
ok(verify_env.GpuMonitor is guard.GpuMonitor and verify_env.DutyCycle is guard.DutyCycle,
   "verify_env 复用 guard 的实现（不另写一份节流）")

# ---------------------------------------------------------------- ④ paths 配额

print("== paths（数据根与配额）==")
# 配额数字本身钉住：文档（TRAINING §0.1.1）与代码必须同口径 —— compact 从 10 → 50 → 100 → 116 GB
# 就是因为世代循环每代 ~1.5 GB 的紧凑集把 10 GB 顶爆、滚动淘汰删掉了 `compact/rl-001`。
# 2026-09-26 用户指定「S 盘上限 200 GB」：新增**总配额**，并把 raw/compact 抬到五项之和恰好 200。
eq("配额：S 盘总配额 = 200 GB（2026-09-26 用户指定）", paths.TOTAL_QUOTA_GB, 200.0)
eq("配额：compact = 116 GB（10→50→100→116，防止世代循环挤掉数据集）",
   paths.QUOTA_GB["compact"], 116.0)
eq("配额：raw = 80 GB（30→80；P4 每代 raw ~1.8 GB ⇒ 约 44 代）", paths.QUOTA_GB["raw"], 80.0)
eq("配额：分项之和 == 总配额（否则「总量 200」是句空话）",
   sum(paths.QUOTA_GB.values()), paths.TOTAL_QUOTA_GB)
ok(set(paths.QUOTA_GB) == {"raw", "compact", "ckpt", "league", "logs"},
   "配额：五个带配额的子目录齐全（probe 故意无分项配额：用完即删，但**仍受总配额约束**）",
   str(sorted(paths.QUOTA_GB)))
tmp_root = scratch("paths")
saved_root, saved_quota = paths.DATA_ROOT, dict(paths.QUOTA_GB)
saved_total = paths.TOTAL_QUOTA_GB
try:
    paths.DATA_ROOT = tmp_root
    paths.ensure_root()
    eq("六个子目录建齐", sum((tmp_root / d).is_dir() for d in paths.SUBDIRS), len(paths.SUBDIRS))
    paths.QUOTA_GB["raw"] = 0.00005                      # ≈50 KB，方便触发淘汰
    old = paths.allocate("raw", "old")
    (old / "g0.jsonl").write_bytes(b"x" * 120_000)       # 明显超过配额 → 下一次分配应删掉它
    paths.allocate("raw", "new")                         # 再分配 → 应把最旧的 raw 条目删掉
    ok(not old.exists(), "超配额时**从最旧的开始删**", f"old 还在？{old.exists()}")
    paths.QUOTA_GB["raw"] = 80.0
    d2 = paths.allocate("raw", "ok")
    ok(d2.is_dir(), "配额内正常分配")
    ok("raw=" in paths.report(), "report() 给出各子目录占用", paths.report()[:60])
    ok(f"total=" in paths.report() and "/200GB" in paths.report(),
       "report() 给出**总量/总配额**（2026-09-26 加的 200 GB 那条闸门）", paths.report()[-70:])
    # **总配额**那条闸门也要真的能触发淘汰（新代码路径，别只测分项配额）
    paths.TOTAL_QUOTA_GB = 0.00002                       # 比任何一个条目都小 → 必须淘汰
    big = paths.allocate("raw", "big")
    (big / "g0.jsonl").write_bytes(b"y" * 120_000)
    paths.allocate("compact", "c1")                      # 再分配 → 总配额超了 ⇒ 从 raw/compact 里删最旧的
    ok(not big.exists(), "超**总配额**时也从最旧的（raw/compact）开始删", f"big 还在？{big.exists()}")
    paths.TOTAL_QUOTA_GB = 200.0
finally:
    paths.DATA_ROOT, paths.QUOTA_GB, paths.TOTAL_QUOTA_GB = saved_root, saved_quota, saved_total
    shutil.rmtree(tmp_root, ignore_errors=True)

# 数据根不可写时必须**抛错**（不能静默降级去写别处）
saved = paths.DATA_ROOT
try:
    paths.DATA_ROOT = Path("Z:/definitely-not-here")
    try:
        paths.ensure_root()
        ok(False, "坏数据根必须抛 DataRootError")
    except paths.DataRootError:
        ok(True, "坏数据根抛 DataRootError（不静默降级）")
finally:
    paths.DATA_ROOT = saved

# ---- 配额**自动回收**的三条新判据（2026-09-27，用户："完善配额自动回收机制"）-----------------
# ① 保护的永不删（`bc-v3-001` 是 v3 BC 基座，raw 早被淘汰 ⇒ 删了不可重建）；
# ② 回收**先挑超出 KEEP_LAST 的旧代**（滚动回收），而不是"顺手删掉最旧的那个小目录"；
# ③ `need_bytes` **预扣**：空间在写之前腾好，而不是写完超了、等下次分配才发现。
ok("bc-v3-001" in paths.KEEP_NAMES,
   "回收：v3 的 BC 基座在保护名单里（它的 raw 已被淘汰 ⇒ 不可重建）", str(sorted(paths.KEEP_NAMES)))
ok(set(paths.KEEP_LAST) == {"raw", "compact"},
   "回收：只给「每代一份」的两类设保留名额", str(paths.KEEP_LAST))
_qroot = scratch("quota")
_saved2 = (paths.DATA_ROOT, dict(paths.QUOTA_GB), paths.TOTAL_QUOTA_GB, dict(paths.KEEP_LAST))
try:
    paths.DATA_ROOT = _qroot
    paths.ensure_root()
    paths.TOTAL_QUOTA_GB = 1000.0                     # 先只看分项配额，别让总配额插嘴

    def _mk(kind: str, name: str, mb: int) -> Path:
        d = _qroot / kind / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "x.bin").write_bytes(b"x" * (mb * 1024 * 1024))
        return d

    # 保护：名字在 KEEP_NAMES + `.keep` 标记
    base = _mk("raw", "bc-v3-001", 90)
    marked = _mk("raw", "keepme", 90)
    (marked / paths.KEEP_MARKER).write_text("keep\n", encoding="utf-8")
    junk = _mk("raw", "junk", 90)
    paths.QUOTA_GB["raw"] = 0.00015                   # ≈157 KB：删掉 junk 也还是超（只剩保护条目）
    try:
        paths.allocate("raw", "new")                  # 分配即触发回收
        eq("回收：只剩保护条目且空间不够时必须报 QuotaError", "没报错", "应报 QuotaError")
    except paths.QuotaError as _e:
        ok(base.exists() and marked.exists() and not junk.exists(),
           "回收：`KEEP_NAMES` / `.keep` 的条目**永不删**，非保护条目先被淘汰；腾不出来就**报错**（不静默）",
           str(_e)[:56])
        ok("受保护" in str(_e), "回收：报错里说明「受保护」与「抬配额/手工清理」", str(_e)[:60])
        ok(True, "（同一条断言覆盖「必须报错」）")

    # 旧代优先：5 代 + 一个评测小目录，KEEP_LAST=2 ⇒ 第一个该删的是最旧的**代**（不是评测目录）
    for d in list((_qroot / "raw").iterdir()):
        shutil.rmtree(d, ignore_errors=True)
    paths.KEEP_LAST["raw"] = 2
    gens = [_mk("raw", f"ppo-g{i:02d}", 20) for i in range(1, 6)]
    tiny = _mk("raw", "ppo-ladder-ppo-g05", 1)        # 评测小目录：不匹配 `-gNN$`
    ev = [p.name for p in paths.evictable("raw")]
    eq("回收：淘汰顺序 = **超出保留名额的旧代**（最旧在前）", ev[:3], ["ppo-g01", "ppo-g02", "ppo-g03"])
    ok(ev[-1] == "ppo-ladder-ppo-g05",
       "回收：评测小目录排在最后（KEEP_LAST 只数「每代一份」的条目）", str(ev))
    paths.QUOTA_GB["raw"] = 0.06                      # ≈61 MB：5×20 MB 已用，再加一个 20 MB 就超
    paths.allocate("raw", "ppo-g06", need_bytes=20 * 1024 * 1024)
    ok(not gens[0].exists() and not gens[1].exists(),
       "回收：`need_bytes`**预扣** ⇒ 写之前就把不够的空间腾出来（旧代先走）",
       f"g01={gens[0].exists()} g02={gens[1].exists()}")
    ok(tiny.exists(), "回收：预扣淘汰也只挑旧代，评测小目录仍留着")
    # 保留名额**按 label 分组**：给 a 造 3 代、b 造 1 代 ⇒ 只有 a 的最旧那代超名额
    for nm in ("a-g01", "a-g02", "a-g03", "b-g01"):
        _mk("raw", nm, 1)
    _ovr = sorted(p.name for p in paths._over_retention("raw"))
    ok("a-g01" in _ovr and "a-g02" not in _ovr and "b-g01" not in _ovr,
       "回收：每代保留名额**按 label 分组**（a 有 3 代 ⇒ 只淘汰最旧的 a-g01；b 只有 1 代 ⇒ 不动）",
       str(_ovr))
    ok("下一个回收" in paths.report(), "回收：`report()` 会预告「下一个回收谁」", paths.report()[-60:])
    # 可回收字节：只算"超名额的旧代 + 非每代一份的杂项"，**不算**保护条目、也不算还在名额内的那几代
    _rec = paths.reclaimable_bytes("raw")
    _keep_gen = [p for p in ("a-g02", "a-g03", "ppo-g05") if (_qroot / "raw" / p).exists()]
    ok(_rec > 0 and "bc-v3-001" not in str(_rec),
       "回收：`reclaimable_bytes` 只统计能腾出的（保护条目不计入可用空间）",
       f"reclaimable={_rec/1024:.0f} KB, 保留名额内的代保留 {len(_keep_gen)} 个")
    _g_gate = {g.name: g.games for g in ml_budget.gates_now(ml_budget.fit_disk([]))}
    _no_rec = int(((paths.QUOTA_GB["raw"] * 1024**3 - paths.dir_size_bytes(_qroot / "raw"))
                   / ml_budget.DEFAULT_RAW_BYTES_PER_GAME))
    ok(_g_gate["raw 配额余量"] >= ml_budget._floor100(max(0, _no_rec)),
       "回收：规划端的配额闸门**把可回收空间算进去**（否则永远卡在「余量只剩一点」）",
       f"闸门={_g_gate['raw 配额余量']} 场 vs 只看余量={_no_rec} 场")
finally:
    (paths.DATA_ROOT, paths.QUOTA_GB, paths.TOTAL_QUOTA_GB, paths.KEEP_LAST) = (
        _saved2[0], _saved2[1], _saved2[2], _saved2[3])
    shutil.rmtree(_qroot, ignore_errors=True)

# ---------------------------------------------------------------- ⑤ 特征 / 数据集 / 网络

print("== 特征（唯一规格）==")
feat = features
eq("牌码 → kind：5m", feat.tile_kind("5m"), 4)
eq("牌码 → kind：0m 也是 5m 的 kind", feat.tile_kind("0m"), 4)
eq("牌码 → kind：1z", feat.tile_kind("1z"), 27)
eq("牌码 → slot：5m", feat.tile_slot("5m"), 4)
eq("牌码 → slot：0m 独占 34", feat.tile_slot("0m"), 34)
eq("牌码 → slot：0s 独占 36", feat.tile_slot("0s"), 36)
eq("坏码 → -1", feat.tile_kind("9z"), -1)
eq("场风：字母 'E'（真实轨迹里的写法）", feat.wind_index("E"), 0.0)
eq("场风：字母 'S'/'W'/'N'", (feat.wind_index("S"), feat.wind_index("W"), feat.wind_index("N")),
   (1.0, 2.0, 3.0))
eq("场风：数字写法也容忍", feat.wind_index(2), 2.0)
eq("state_dim 固定（改了要同步 Java）", feat.state_dim(), 615)
eq("cand_dim 固定", feat.cand_dim(), 96)
eq("派生段长与 Java ObsFeatures 一致", (feat.DERIVED_DECISION, feat.DERIVED_CANDIDATE), (71, 8))
eq("派生段逐维分母与 Java 一致（前 68 维危险度 /100，后 3 维向听/打点）",
   (feat.DERIVED_DECISION_SCALE[:5], feat.DERIVED_DECISION_SCALE[-3:]),
   ((100.0,) * 5, (8.0, 13.0, 32000.0)))


def _fake_obs(legal, kind="turn"):
    return {"v": 1, "seat": 0, "kind": kind,
            "hand": [0.0] * 34, "hand_red": [False] * 34, "drawn": "5m", "player_draws": 1,
            "menzen": True, "self_riichi": False, "furiten": False,
            "melds": [[], [], [], []], "discards": [[], [], [], []], "dora_indicators": [],
            "riichi": [False] * 4, "ippatsu": [False] * 4, "scores": [25000] * 4,
            "round": {"bakaze": "E", "kyoku": 1, "honba": 0, "dealer": 0, "riichi_sticks": 0},
            "tiles_left": 70, "dead_wall_left": 4, "total_discards": 0, "kan_count": 0,
            "any_call": False, "visible": [0.0] * 34, "haitei": False, "houtei": False,
            "rinshan": False, "from": None, "called_tile": None, "win_note": None,
            "legal": list(legal)}


obs = _fake_obs(["discard:1m", "discard:5m", "riichi:1m", "pass"])
sv = feat.state_vector(obs)
eq("状态向量长度 = state_dim()", sv.shape[0], feat.state_dim())
ok(np.isfinite(sv).all(), "状态向量没有 NaN/Inf")
ok(np.array_equal(sv, feat.state_vector(obs)), "同一个 obs → 同一个向量（纯函数）")
cand = feat.candidates(sv, obs["legal"])
eq("候选矩阵形状 [legal, cand_dim]", cand.shape, (4, feat.cand_dim()))
eq("候选数 = legal 数（顺序一致）", cand.shape[0], len(obs["legal"]))
eq("discard 的 tile one-hot 落在 5m 槽位（1m=0、5m=4）",
   int(np.argmax(cand[0, 9:9 + feat.TILE_SLOTS])), 0)
eq("赤五/摸切标志位对普通打牌为 0", float(cand[0, 9 + 2 * feat.TILE_SLOTS]), 0.0)
ok(np.array_equal(cand[1][:feat.cand_dim() - feat.DERIVED_CANDIDATE],
                  feat.cand_vector("discard:5m")),
   "候选向量的基础段是纯函数（与单点定义一致）")
# 派生量：传了就按**逐维分母**归一化落在末尾，不传就填 0（缺 sidecar 时才该发生）
sv_d = feat.state_vector(obs, [50] * feat.DERIVED_DECISION)
ok(abs(float(sv_d[-feat.DERIVED_DECISION]) - 0.5) < 1e-6, "派生段第 1 维（危险度）按 /100 归一化",
   f"got={float(sv_d[-feat.DERIVED_DECISION])}")
ok(abs(float(sv_d[-2]) - 50.0 / 13.0) < 1e-6 and abs(float(sv_d[-1]) - 50.0 / 32000.0) < 1e-6,
   "派生段后两维（打点番数/点数）按各自的量纲归一化，而不是统一 /100",
   f"han={float(sv_d[-2])} points={float(sv_d[-1])}")
ok(np.allclose(sv[-feat.DERIVED_DECISION:], 0.0), "不传派生量时末尾填 0")
cd_d = feat.cand_vector_full("discard:1m", [8, 34, 136, 0, 0, 0, 0, 4])
ok(abs(float(cd_d[-1]) - 1.0) < 1e-6 and abs(float(cd_d[-8]) - 1.0) < 1e-6,
   "派生候选量按 DERIVED_SCALE_CAND 归一化（向听 8→1.0、dora 4→1.0）",
   f"shanten={float(cd_d[-8])} dora={float(cd_d[-1])}")

print("== 数据集（按整场切分 + 紧凑落盘 + 派生特征 sidecar）==")


def write_sidecar(jsonl, rows, *, drop_seat_segments=False):
    """按 `TraceFeatures` 的**七段式**写一个 sidecar（合成用；同时也是格式契约的守卫）。

    `drop_seat_segments=True` 时**故意不写** v3 的逐家四段（D~G）—— 用来验"老版/截断的
    sidecar 必须报错"，而不是被当成 0 悄悄吃下去。
    """
    import struct
    ndec = len(rows)
    a = np.concatenate([np.asarray(r["danger"], dtype="<i2") for r in rows])
    b = np.asarray([len(r["cand"]) for r in rows], dtype="<i2")
    c = np.concatenate([np.asarray(r["cand"], dtype="<i2") for r in rows]).reshape(-1, 8)
    head = struct.pack("<5i", ds.SIDECAR_MAGIC, feat.DERIVED_VERSION, ndec,
                       feat.DERIVED_DECISION, feat.DERIVED_CANDIDATE)
    out = jsonl.parent / (jsonl.name.split(".")[0] + ".feat.bin")
    tail = b""
    if not drop_seat_segments:
        seats = np.asarray([r["per_seat"] for r in rows], dtype="<i1")       # (n,3,34)
        riichi = np.asarray([r["per_seat_riichi"] for r in rows], dtype="<i1")
        genbutsu = np.asarray([r["genbutsu"] for r in rows], dtype="u1")     # (n,3,5)
        suji = np.asarray([r["suji"] for r in rows], dtype="u1")
        assert seats.shape == (ndec, feat.DERIVED_SEATS, feat.KIND_COUNT)
        assert genbutsu.shape == (ndec, feat.DERIVED_SEATS, feat.DERIVED_BITMAP_BYTES)
        tail = seats.tobytes() + riichi.tobytes() + genbutsu.tobytes() + suji.tobytes()
    out.write_bytes(head + a.tobytes() + b.tobytes() + c.tobytes() + tail)
    return out


droot = scratch("dataset")
src = droot / "src"
src.mkdir(parents=True, exist_ok=True)
DANGER = [7] * feat.DERIVED_DECISION                      # 合成的逐决策派生量（71 维）
CAND0 = [[1, 2, 3, 4, 5, 6, 7, 8], [0] * 8]               # 两条候选的派生量（原始整数）
# 合成的逐家段：第 0 家（下家）的 3 号牌种是现物、4 号牌种是筋 → 位图低 3 字节有值
_SEAT_DANGER = [[5] * feat.KIND_COUNT, [30] * feat.KIND_COUNT, [65] * feat.KIND_COUNT]
_GENBUTSU = [[0b00001000, 0, 0, 0, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 0]]
_SUJI = [[0b00010000, 0, 0, 0, 0], [0, 0, 0, 0, 0], [0, 0, 0, 0, 0]]
PER_SEAT_ROW = {"per_seat": _SEAT_DANGER, "per_seat_riichi": _SEAT_DANGER,
                "genbutsu": _GENBUTSU, "suji": _SUJI}
for g in range(2):                                        # 两场，每场 2 条决策
    jsonl = src / f"g{g}.jsonl"
    with jsonl.open("w", encoding="utf-8") as fh:
        for i in range(2):
            row = {"type": "decision", "game": g, "hand_no": 1, "step": i, "seat": 0,
                   "policy": "teacher", "kind": "turn",
                   "legal": ["discard:1m", "pass"], "chosen": "discard:1m", "chosen_index": 0,
                   "hand_delta": [100, -100, 0, 0], "obs": _fake_obs(["discard:1m", "pass"])}
            if g == 0:                       # 第 0 场给顺位；第 1 场**故意不给** → 读回来必须是 -1
                row["placement"] = [3, 1, 4, 2]
            if g == 1 and i == 0:            # 一行是"学生策略"打的 → is_student=1
                row["policy"] = "net:T:\\x\\net.bin"
            if i == 1:                       # 第 2 条带 DAgger 标注：老师会 pass（与 chosen 不同）
                row["teacher"] = "pass"
                row["teacher_index"] = 1
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_sidecar(jsonl, [dict(PER_SEAT_ROW, danger=DANGER, cand=CAND0) for _ in range(2)])

build_meta = ds.build(src, droot / "compact", val_frac=0.5, split_seed=0, quiet=True)
eq("特征版本写进 meta", build_meta["feature_version"], feat.FEATURE_VERSION)
eq("派生特征版本写进 meta", build_meta["derived_version"], feat.DERIVED_VERSION)
ok(build_meta.get("has_derived") is True, "meta 标记「已带派生特征」")
eq("按场切分：2 场 → 1 训 1 验", (len(build_meta["train_files"]), len(build_meta["val_files"])), (1, 1))
# ⚠ 跨代对局：对手也是网络 ⇒ `is_student` 必须只认**这一轮学生的确切策略串**（`build(student_prefix=…)`）。
#   旧口径"任何 `net:`"会把对手网的决策算进**策略损失**（实测 1 席学生得到 0.75 的学生行占比）。
_student_spec = "net:T:\\x\\net.bin"                     # 与上面 g1 那行的 policy 完全相同
_other_net = fs = None
for _g in range(2):
    _p = src / f"g{_g}.jsonl"
    _rows = [json.loads(l) for l in _p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if _g == 0:
        for _r in _rows:
            _r["policy"] = "net:T:\\y\\other.bin"        # 对手也是网络（旧口径下会被算成学生）
    _p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in _rows) + "\n", encoding="utf-8")
ds.build(src, droot / "compact2", val_frac=0.5, split_seed=0, quiet=True,
         student_prefix=_student_spec)


def _n_student(d):
    return sum(int(np.asarray(ds.load_split(d, sp)["is_student"]).sum()) for sp in ("train", "val"))


eq("P3 列 is_student：显式 `--student <spec>` 时**对手网不算学生**（跨代对局的关键判据）",
   _n_student(droot / "compact2"), 1)                        # 只有 g1 那一行是"这一轮的学生"
ds.build(src, droot / "compact3", val_frac=0.5, split_seed=0, quiet=True)   # 缺省 `net:`
eq("P3 列 is_student：缺省 `net:` 时**对手网也算学生**（老口径；跨代对局会污染策略损失）",
   _n_student(droot / "compact3"), 3)                        # g0 两行 + g1 一行
eq("训练条数", build_meta["train_decisions"], 2)
tr = ds.load_split(droot / "compact", "train")
eq("读回的 state 形状", tr["state"].shape, (2, feat.state_dim()))
eq("读回的 cand 形状", tr["cand"].shape, (2, 2, feat.cand_dim()))
ok(np.array_equal(np.asarray(tr["n_legal"]), [2, 2]), "n_legal 记对了")
# 派生量落盘的口径：state 末尾 71 维是**归一化后**的派生量；cand 末尾 8 维是**原始整数**
ok(np.allclose(np.asarray(tr["state"][0, -feat.DERIVED_DECISION:], dtype=np.float32),
               np.asarray(DANGER, dtype=np.float32) / np.asarray(feat.DERIVED_DECISION_SCALE,
                                                                 dtype=np.float32), atol=1e-3),
   "state 末尾 71 维 = 派生量 / 逐维分母")
ok(np.array_equal(np.asarray(tr["cand"][0, 0, -feat.DERIVED_CANDIDATE:]),
                  np.asarray(CAND0[0], dtype=np.uint8)),
   "cand 末尾 8 维 = 派生量的原始整数（归一化留给 _batch）")
sc = ds.load_sidecar(ds.sidecar_path(src / "g0.jsonl"))
eq("sidecar：条数", sc["n"], 2)
eq("sidecar：逐决策派生块形状", sc["danger"].shape, (2, feat.DERIVED_DECISION))
eq("sidecar：候选块形状", sc["cand"].shape, (4, feat.DERIVED_CANDIDATE))
# sidecar v3 的逐家四段（D~G）—— v4 的 `tile` 通道就靠它们
eq("sidecar v3：逐家危险度段形状", sc["danger_per_seat"].shape,
   (2, feat.DERIVED_SEATS, feat.KIND_COUNT))
eq("sidecar v3：逐家立直口径段形状", sc["danger_riichi_per_seat"].shape,
   (2, feat.DERIVED_SEATS, feat.KIND_COUNT))
eq("sidecar v3：现物位图段形状", sc["genbutsu_per_seat"].shape,
   (2, feat.DERIVED_SEATS, feat.DERIVED_BITMAP_BYTES))
eq("sidecar v3：筋壁位图段形状", sc["suji_per_seat"].shape,
   (2, feat.DERIVED_SEATS, feat.DERIVED_BITMAP_BYTES))
eq("sidecar v3：逐家危险度原样读回（int8）", int(sc["danger_per_seat"][0][1][7]), 30)
ok(int(sc["genbutsu_per_seat"][0][0][0]) == 0b00001000
   and int(sc["suji_per_seat"][0][0][0]) == 0b00010000,
   "sidecar v3：位图按**字节内 LSB 在前**读回（现物=3 号牌种、筋壁=4 号牌种）")
ok(all(int(b) & 0b11111100 == 0 for b in sc["genbutsu_per_seat"][:, :, 4].ravel()),
   "sidecar v3：位图末字节高位为 0（34 位占 5 字节）")
# ⚠ 老版/截断的 sidecar（缺 v3 的 D~G 段）**必须报错** —— 当成 0 填就等于悄悄换任务
_oldsc = scratch("dataset-oldsidecar")
shutil.copy(src / "g0.jsonl", _oldsc / "g0.jsonl")
shutil.copy(src / "g1.jsonl", _oldsc / "g1.jsonl")
write_sidecar(_oldsc / "g0.jsonl",
              [dict(PER_SEAT_ROW, danger=DANGER, cand=CAND0) for _ in range(2)],
              drop_seat_segments=True)
try:
    ds.load_sidecar(ds.sidecar_path(_oldsc / "g0.jsonl"))
    ok(False, "缺 v3 逐家段的 sidecar 必须报错")
except ValueError as e:
    ok("长度不自洽" in str(e), "缺 v3 逐家段时按长度自洽性报错", str(e)[:60])
# 版本号不符（老 sidecar）也必须报错 —— `FEATURES-V4.md` §6 判据 2（不许当 0 填）
_badver = scratch("dataset-badver")
shutil.copy(src / "g0.jsonl", _badver / "g0.jsonl")
_raw = bytearray((src / "g0.feat.bin").read_bytes())
_raw[4:8] = (2).to_bytes(4, "little")                    # 头部第 2 个 int32 = derived_version
(_badver / "g0.feat.bin").write_bytes(bytes(_raw))
try:
    ds.load_sidecar(_badver / "g0.feat.bin")
    ok(False, "sidecar 版本不符（v2）必须报错")
except ValueError as e:
    ok("派生特征版本" in str(e), "sidecar 版本不符时报错（提示重新 --features）", str(e)[:60])
# 缺 sidecar 必须**报错**（悄悄用 0 会让训练/推理口径不一致，而且查不出来）
noside = scratch("dataset-noside")
shutil.copy(src / "g0.jsonl", noside / "g0.jsonl")
shutil.copy(src / "g1.jsonl", noside / "g1.jsonl")
try:
    ds.build(noside, noside / "compact", val_frac=0.5, quiet=True)
    ok(False, "缺 sidecar 时必须报错")
except FileNotFoundError as e:
    ok("--features" in str(e), "缺 sidecar 时报错并提示怎么生成", str(e)[:60])
# sidecar 与 jsonl 的 nLegal 对不上（旧 sidecar）也必须报错
bad = scratch("dataset-bad")
shutil.copy(src / "g0.jsonl", bad / "g0.jsonl")
shutil.copy(src / "g1.jsonl", bad / "g1.jsonl")
write_sidecar(bad / "g0.jsonl",
              [dict(PER_SEAT_ROW, danger=DANGER, cand=[[0] * 8] * 3) for _ in range(2)])
write_sidecar(bad / "g1.jsonl", [dict(PER_SEAT_ROW, danger=DANGER, cand=CAND0) for _ in range(2)])
try:
    ds.build(bad, bad / "compact", val_frac=0.5, quiet=True)
    ok(False, "sidecar 与 jsonl 对不上时必须报错")
except ValueError as e:
    ok("nLegal" in str(e), "sidecar 行数/N 与 jsonl 不符时报错", str(e)[:60])

# ---- 标签侧（`--aux`）：读侧硬闸门 + 与输入列**物理分离**（2026-09-27）------------------
from mahjong_ml import auxlabels as aux_mod, producer as ml_producer      # noqa: E402


def write_aux(jsonl, n, *, version=aux_mod.AUX_VERSION, obs_version=1):
    """合成一份 `g*.aux.npz`（与 Java `TraceRecorder.writeAux` 同构：成员名/形状/元数据）。

    ⚠ `obs_version` 缺省 **1**：这一节的合成 obs 就是 `_fake_obs` 的 `v: 1`
    （`check_dir` 会拿它与轨迹里的 obs 版本对账 —— 这正是"新轨迹配旧标签"要抓的那种错）。
    """
    out = aux_mod.aux_path(jsonl)
    np.savez(out,
             own_shanten_after=np.full((n,), 2, np.int8),
             own_tenpai=np.full((n,), 1, np.uint8),
             opp_tenpai=np.tile(np.asarray([1, 0, 1], np.uint8), (n, 1)),
             opp_hand=(np.arange(n * 3 * 34).reshape(n, 3, 34) % 4).astype(np.uint8),
             opp_dealin=np.zeros((n, 3), np.uint8),
             win_flag=np.zeros((n,), np.uint8),
             hand_delta=np.full((n,), 100, np.int32),
             placement=np.full((n,), 2, np.uint8),
             meta=np.frombuffer(json.dumps({
                 "aux_version": version, "obs_version": obs_version, "game": 0, "seed": 1,
                 "n": n, "policies": ["teacher"] * 4}).encode("utf-8"), dtype=np.uint8))
    return out


auxdir = scratch("aux")
shutil.copy(src / "g0.jsonl", auxdir / "g0.jsonl")
write_aux(auxdir / "g0.jsonl", 2)
aux1 = aux_mod.load_aux(aux_mod.aux_path(auxdir / "g0.jsonl"))
eq("aux：读回条数", aux1["n"], 2)
eq("aux：opp_hand 形状（n,3,34）", tuple(aux1["opp_hand"].shape), (2, 3, 34))
eq("aux：元数据里的 obs 版本", aux1["meta"]["obs_version"], 1)
ok_files, probs = aux_mod.check_dir(auxdir)
eq("aux：check_dir 通过的文件数", ok_files, 1)
eq("aux：check_dir 无问题", probs, [])
# ① 行数错位（标签 1 行 / 轨迹 2 条决策）必须报错
auxbad = scratch("aux-badn")
shutil.copy(src / "g0.jsonl", auxbad / "g0.jsonl")
write_aux(auxbad / "g0.jsonl", 1)
_okn, _pn = aux_mod.check_dir(auxbad)
ok(_okn == 0 and any("错位" in p for p in _pn), "aux：行数与决策数不符时报错", str(_pn)[:70])
# ② 缺标签文件必须报错（不静默跳过）
auxmiss = scratch("aux-miss")
shutil.copy(src / "g0.jsonl", auxmiss / "g0.jsonl")
_okm, _pm = aux_mod.check_dir(auxmiss)
ok(_okm == 0 and any("缺" in p for p in _pm), "aux：缺 g*.aux.npz 时报错", str(_pm)[:70])
# ③ obs 版本不符（新轨迹配旧标签）必须报错
auxver = scratch("aux-ver")
shutil.copy(src / "g0.jsonl", auxver / "g0.jsonl")
write_aux(auxver / "g0.jsonl", 2, obs_version=2)
_okv, _pv = aux_mod.check_dir(auxver)
ok(_okv == 0 and any("新旧混用" in p for p in _pv), "aux：标签 obs 版本与轨迹不符时报错",
   str(_pv)[:70])
# ④ 格式版本不符必须报错（字段增删没同步 Python 侧）
try:
    aux_mod.load_aux(write_aux(auxver / "g1.jsonl", 2, version=99))
    ok(False, "aux 格式版本不符必须报错")
except ValueError as e:
    ok("标签格式版本" in str(e), "aux：格式版本不符时报错", str(e)[:60])
# ⑤ `--aux` 并进紧凑集：列齐 + **输入列逐字节不变**（物理分离的机器判据）
for g in (0, 1):
    write_aux(src / f"g{g}.jsonl", 2)
m_plain = ds.build(src, droot / "aux-plain", val_frac=0.5, split_seed=0, quiet=True)
m_aux = ds.build(src, droot / "aux-on", val_frac=0.5, split_seed=0, quiet=True, aux=True)
ok(m_aux["has_aux"] is True and m_aux["aux_version"] == aux_mod.AUX_VERSION,
   "aux：meta 标记 has_aux 与版本")
sp_aux = ds.load_split(droot / "aux-on", "train")
ok(all(sp_aux["aux"][k] is not None for k in aux_mod.AUX_COMPACT_COLUMNS),
   f"aux：紧凑集里 {len(aux_mod.AUX_COMPACT_COLUMNS)} 个标签列都在")
eq("aux：opp_hand 列形状", tuple(sp_aux["aux"]["aux_opp_hand"].shape), (len(sp_aux["label"]), 3, 34))
_insame = all((droot / "aux-plain" / f"train.{tag}.npy").read_bytes()
              == (droot / "aux-on" / f"train.{tag}.npy").read_bytes()
              for tag in ("state", "cand", "nlegal", "label", "delta", "game"))
ok(_insame, "aux：开不开 --aux 的**输入列逐字节相同**（标签绝不进输入）")
ok(sp_aux["meta"]["has_aux"] is True and bool(sp_aux["meta"]["aux_columns"]),
   "aux：紧凑集 meta 记下 has_aux 与标签列清单")
# ⑥ `--aux` 但缺标签文件 ⇒ 构建必须报错（不填 0）
_auxnon = scratch("aux-none")
shutil.copy(src / "g0.jsonl", _auxnon / "g0.jsonl")
shutil.copy(ds.sidecar_path(src / "g0.jsonl"), ds.sidecar_path(_auxnon / "g0.jsonl"))
try:
    ds.build(_auxnon, _auxnon / "compact", val_frac=0.5, quiet=True, aux=True)
    ok(False, "aux=True 但缺标签文件时必须报错")
except FileNotFoundError as e:
    ok("--aux" in str(e), "aux：缺标签文件时报错并提示怎么生成", str(e)[:60])
# ⑦ C++ 生产者不支持 `--aux` ⇒ 显式报错（不静默产出一份没有标签的数据集）
try:
    ml_producer.selfplay_cmd(2, 1, "teacher", 1, Path("x"), aux=True, name="cpp")
    ok(False, "cpp 生产者 + --aux 必须报错")
except SystemExit as e:
    ok("--aux" in str(e), "aux：cpp 生产者不支持时报错（提示用 java 采这一批）", str(e)[:70])
same = ds.split_files(ds.trace_files(src), 0.5, 0)
same2 = ds.split_files(ds.trace_files(src), 0.5, 0)
eq("切分可复现（同种子同结果）", [f.name for f in same[0]], [f.name for f in same2[0]])
ok(not (set(f.name for f in same[0]) & set(f.name for f in same[1])), "训练/验证没有交集")

# ---- 紧凑集构建的**并行**路径：只改调度、不改产出（2026-09-26 加）------------------------
# ⚠ 并行实现的两个环境坑都写在这里当回归：① 沙箱禁**命名管道** ⇒ 不能用 `multiprocessing.Pool`
#   （`_winapi.CreateFile` → WinError 5），改用"无管道子进程 + 文件"；② 沙箱拒绝对 `mkdtemp`
#   新建目录的写入 ⇒ scratch 用 `mkdir` 固定名。判据只有一条：**逐字节相同**。
parsrc = scratch("dataset-par-src")                       # 4 个文件 → 训练切分里真的会并行
for i in range(4):
    s = src / f"g{i % 2}.jsonl"
    shutil.copy(s, parsrc / f"g{i}.jsonl")
    shutil.copy(ds.sidecar_path(s), ds.sidecar_path(parsrc / f"g{i}.jsonl"))
pb1, pb4 = scratch("dataset-par1"), scratch("dataset-par4")
ds.build(parsrc, pb1, val_frac=0.25, split_seed=0, quiet=True, workers=1)
ds.build(parsrc, pb4, val_frac=0.25, split_seed=0, quiet=True, workers=4)
ok(same_bytes(pb1, pb4), "并行紧凑集与串行**逐字节相同**（workers=4 vs 1）")
ok(ds.load_split(pb4, "train")["state"].shape[0] == 6, "并行产物的条数也对"
   f"（3 场 × 2 条 = 6，实际 {ds.load_split(pb4, 'train')['state'].shape[0]}）")
pt1, pt4 = scratch("dataset-par1-trunc"), scratch("dataset-par4-trunc")
ds.build(parsrc, pt1, val_frac=0.25, split_seed=0, quiet=True, workers=1, max_decisions=3)
ds.build(parsrc, pt4, val_frac=0.25, split_seed=0, quiet=True, workers=4, max_decisions=3)
ok(same_bytes(pt1, pt4), "并行=串行（--max-decisions 截断路径，含边界文件的 lmax 口径）")

print("== DAgger 标签源与多来源合并 ==")
eq("auto：有 teacher_index 就用它（第 2 条应为 1）",
   int(np.asarray(tr["label"])[1]), 1)
eq("auto：没有标注重音的行仍用 chosen_index（第 1 条应为 0）",
   int(np.asarray(tr["label"])[0]), 0)
eq("meta 记录用了老师标注的条数", build_meta["teacher_labeled_train"], 1)
eq("meta 记录标签源", build_meta["label_source"], "auto")
m_chosen = ds.build(src, droot / "compact-chosen", val_frac=0.5, quiet=True,
                    label_source="chosen")
trc = ds.load_split(droot / "compact-chosen", "train")
eq("label_source=chosen：一律用 chosen_index", list(np.asarray(trc["label"])), [0, 0])
try:                                    # 没有标注却要求 teacher → 必须报错
    ds.build(src, droot / "compact-teacher", val_frac=0.5, quiet=True, label_source="teacher")
    ok(False, "label_source=teacher 且部分行没标注时必须报错")
except ValueError as e:
    ok("teacher_index" in str(e), "label_source=teacher 缺标注时报错", str(e)[:50])
# 多来源合并：两个目录各自的 g0.jsonl 都要进来（DAgger 数据 + BC 数据）
two = scratch("dataset-two")
for name, tag in (("a", 0), ("b", 1)):
    d2 = two / name
    d2.mkdir(parents=True, exist_ok=True)
    shutil.copy(src / "g0.jsonl", d2 / "g0.jsonl")
    shutil.copy(src / "g0.feat.bin", d2 / "g0.feat.bin")
m_multi = ds.build([two / "a", two / "b"], two / "compact", val_frac=0.5, quiet=True)
eq("多来源：两个目录都被读到", m_multi["files_total"], 2)
eq("多来源：条数是两者之和", m_multi["train_decisions"] + m_multi["val_decisions"], 4)
eq("多来源：meta 记下两个来源", len(m_multi["src"]), 2)
# **泄漏不变量**（受控切分的前提）：训练场与验证场按**全路径**不相交；名字会跨目录重名，光比名字没用
eq("全路径清单的长度与文件名清单一致",
   (len(m_multi["train_files_path"]), len(m_multi["val_files_path"])),
   (len(m_multi["train_files"]), len(m_multi["val_files"])))
ok(not (set(m_multi["train_files_path"]) & set(m_multi["val_files_path"])),
   "训练场与验证场的**全路径**不相交（跨来源也不重叠）")
eq("两份清单覆盖全部场次",
   len(set(m_multi["train_files_path"]) | set(m_multi["val_files_path"])), m_multi["files_total"])
eq("同一个文件也能当来源（受控切分要用）", len(ds.trace_files(src / "g0.jsonl")), 1)
notjsonl = droot / "notjsonl.txt"
notjsonl.write_text("x", encoding="utf-8")
try:
    ds.trace_files(notjsonl)
    ok(False, "非 jsonl 的单个文件必须报错")
except ValueError as e:
    ok("不是 jsonl" in str(e), "单个文件不是 jsonl 时报错", str(e)[:40])
m_eval = ds.build([src / "g0.jsonl", src / "g1.jsonl"], droot / "compact-eval",
                  val_frac=1.0, label_source="auto", quiet=True)
eq("val_frac=1.0 → 全部当验证集（评测集里一条训练样本都不留）",
   (m_eval["train_decisions"], m_eval["val_decisions"], len(m_eval["train_files"])), (0, 4, 0))
ok(ds.load_split(droot / "compact-eval", "val")["state"].shape[0] == 4,
   "全 val 的紧凑集能读回（dagger 的无泄漏对比就建在它上面）")
# ---- P3 的列：没有它们组不出转移（file 分文件、hand_no 分小局、seat 决定读 delta 的哪一家）
eq("meta 声明带了哪些 P3 列", build_meta["rl_columns"], sorted(ds.RL_COLUMNS))
tr_rl, val_rl = tr, ds.load_split(droot / "compact", "val")
pl_all = [int(x) for x in np.asarray(tr_rl["placement"]).tolist()] \
    + [int(x) for x in np.asarray(val_rl["placement"]).tolist()]
eq("P3 列 placement = 该座位的顺位（seat=0 → placement[0]=3）",
   sorted(x for x in pl_all if x != -1), [3, 3])
eq("P3 列 placement 缺字段 = -1（**不是 0** —— 0 会被当成「第 0 名」）",
   sorted(x for x in pl_all if x == -1), [-1, -1])
eq("P3 列 is_student：只有 net: 开头的行算学生（teacher 行不算）",
   int(np.asarray(tr_rl["is_student"]).sum() + np.asarray(val_rl["is_student"]).sum()), 1)
eq("P3 列 hand_no / seat 落盘",
   (sorted(set(int(x) for x in np.asarray(tr_rl["hand_no"]).tolist())),
    sorted(set(int(x) for x in np.asarray(tr_rl["seat"]).tolist()))), ([1], [0]))
many = scratch("dataset-files")           # 4 个文件 → 一个切分里就有 2 个文件，才测得出 file 编号
for k in range(4):
    shutil.copy(src / "g0.jsonl", many / f"g{k}.jsonl")
    shutil.copy(src / "g0.feat.bin", many / f"g{k}.feat.bin")
m_many = ds.build(many, many / "compact", val_frac=0.5, split_seed=0, quiet=True)
trm = ds.load_split(many / "compact", "train")
eq("P3 列 file：**切分内**独立编号 0..n-1（每个切分都从 0 开始，不与别的切分共用编号）",
   sorted(set(int(x) for x in np.asarray(trm["file"]).tolist())), [0, 1])
eq("P3 列 file 覆盖该切分的全部文件（2 场 → 2 个编号，各 2 条）",
   [int(np.sum(np.asarray(trm["file"]) == k)) for k in (0, 1)], [2, 2])

print("== 网络（候选打分头）==")
net = nets.build(feat.state_dim(), feat.cand_dim(), hidden=32, head=16)
st = torch.from_numpy(np.stack([sv, sv]).astype(np.float32))
cd = torch.from_numpy(np.stack([cand, cand]).astype(np.float32))
msk = torch.ones((2, cand.shape[0]), dtype=torch.bool)
out = net(st, cd, msk)
eq("logits 形状 [B, L]", tuple(out.shape), (2, cand.shape[0]))
ok(torch.isfinite(out).all(), "合法位置 logits 有限")
msk2 = msk.clone()
msk2[:, 1:] = False
out2 = net(st, cd, msk2)
ok(bool(torch.isinf(out2[:, 1:]).all()), "非法位置被填成 -inf（掩码生效）")
eq("参数量与小网络预期一致（0.02M 级）", net.n_params() > 0, True)
w = nets.export_weights(net)
ok({"state_dim", "cand_dim", "hidden", "weights"} <= set(w), "导出权重含结构超参（Java 侧要）")

print("== 行为克隆（只验跑得通 + 指标形状，不做效果承诺）==")
ev = bc.evaluate(net, tr, "cpu", batch=2)
ok(0.0 <= ev["top1"] <= 1.0 and ev["n"] == 2, "evaluate 给出 top-1 与样本数",
   f"top1={ev['top1']:.2f} n={ev['n']}")
ok(ev["majority_type_acc"] > 0 and 0.0 <= ev["first_legal_acc"] <= 1.0,
   "给出**同粒度**基线（多数类型 / 总是选第一个合法动作）",
   f"多数类型={ev['majority_type_acc']:.2f} 首合法={ev['first_legal_acc']:.2f}")
ok("top1_type" in ev and "uniform_nll" in ev, "同时给出类型准确率与均匀猜测的 NLL（免得拿不同粒度比）")

print("== bc eval 子命令（用任意 checkpoint 评任意数据集 —— DAgger 对比就靠它）==")
ck = droot / "smoke.pt"
torch.save({"model": net.state_dict(),
            "config": {"state_dim": feat.state_dim(), "cand_dim": feat.cand_dim(),
                       "hidden": 32, "head": 16},          # ⚠ 故意不等于 build 的默认值
            "feature_version": feat.FEATURE_VERSION}, ck)
val_data = ds.load_split(droot / "compact", "val")
ref = bc.evaluate(net, val_data, "cpu", batch=2)
got = bc.eval_ckpt(str(droot / "compact"), str(ck), "val", "cpu", batch=2)
ok(abs(got["top1"] - ref["top1"]) < 1e-9 and got["n"] == ref["n"],
   "eval 与 evaluate 同一把尺子（同 split 同指标）", f"{got['top1']} vs {ref['top1']}")
ok(got["feature_version"] == feat.FEATURE_VERSION and got["ckpt"] == str(ck),
   "eval 结果带上 checkpoint 路径与特征版本（写进报告才可追溯）")
ok(got["majority_type_acc"] > 0, "eval 也给同粒度基线（不是只丢一个孤零零的 top-1）")
rows = bc.predict_rows(net, val_data, "cpu", batch=2)
ok(abs(float(rows["correct"].mean()) - ref["top1"]) < 1e-12 and len(rows["pred"]) == ref["n"],
   "逐行预测（聚类检验的原料）与聚合 top-1 同源 —— 免得两套口径各说各话")
ok(rows["game"].shape == rows["correct"].shape, "逐行预测带上场号（按场聚类要用）")
badck = droot / "bad.pt"
torch.save({"model": net.state_dict(),
            "config": {"state_dim": 1, "cand_dim": 1, "hidden": 32, "head": 16},
            "feature_version": feat.FEATURE_VERSION}, badck)
try:                                    # 维度对不上必须报错，不许"结构猜着建、结果照报"
    bc.eval_ckpt(str(droot / "compact"), str(badck), "val", "cpu", batch=2)
    ok(False, "特征维度对不上的 checkpoint 必须报错")
except ValueError as e:
    ok("特征维度" in str(e), "checkpoint 维度与代码不符时报错", str(e)[:60])

print("== DAgger 对比：按场聚类的配对 bootstrap ==")
# ① 全赢：每一行都从错变对 → Δ=1，CI 退化成一个点
one, zero = np.ones(12, dtype=bool), np.zeros(12, dtype=bool)
cl = np.repeat(np.arange(4), 3)
d1 = dagger.cluster_bootstrap_diff(zero, one, cl, iters=200)
eq("全赢：Δ = 1 且 P(涨) = 1", (d1["mean"], d1["p_win"]), (1.0, 1.0))
eq("全赢：CI 上界 = 1，场数记对", (d1["hi"], d1["games"]), (1.0, 4))
# ② 打平：两个模型逐行一样 → Δ=0（别把"没变化"报成显著）
d0 = dagger.cluster_bootstrap_diff(one, one.copy(), cl, iters=200)
eq("打平：Δ = 0 且 P(涨) = 0", (d0["mean"], d0["p_win"]), (0.0, 0.0))
# ③ **红证**：差异全部集中在**一场**里 —— 逐行 bootstrap 会给 lo>0（假显著），
#    按场聚类必须把 0 包进来（重采样很可能整场漏掉），这才叫"换一批牌局还成不成立"
n3 = 100
base3, new3 = np.zeros(n3, dtype=bool), np.zeros(n3, dtype=bool)
new3[:10] = True                                        # 只有第 0 场涨了
cl3 = np.repeat(np.arange(10), 10)
d3 = dagger.cluster_bootstrap_diff(base3, new3, cl3, iters=400)
eq("单场差异：点估计 Δ = 0.1", round(d3["mean"], 6), 0.1)
ok(d3["lo"] == 0.0, "单场差异：按场聚类的 CI 下界 = 0（不许假显著）", f"lo={d3['lo']}")
ok(d3["p_win"] < 1.0, "单场差异：P(涨) < 1（整场可能被漏掉）", f"P={d3['p_win']:.3f}")
d3row = dagger.cluster_bootstrap_diff(base3, new3, np.arange(n3), iters=400)
ok(d3row["lo"] > 0.0, "同一份数据在**逐行**口径下 CI 下界 > 0 —— 所以必须按场聚类",
   f"逐行 lo={d3row['lo']:.4f} vs 按场 lo={d3['lo']:.4f}")
ok(dagger.cluster_bootstrap_diff(base3, new3, np.zeros(n3, dtype=int), iters=10)["iters"] == 0,
   "只有一场时不给假 CI（iters=0，调用方看得出来）")
ok(abs(dagger.policy_string(Path("C:/x/net.bin")).count("net:")) == 2,
   "策略串：学生坐 0/2 号位（配 --rotate 逐场轮转）", dagger.policy_string(Path("C:/x/net.bin")))

print("== DAgger 多臂对比（基线 / 新模型 / 对照臂）==")
net2 = nets.build(feat.state_dim(), feat.cand_dim(), hidden=32, head=16)
with torch.no_grad():                                   # 造一个"行为不同"的第二臂
    for t in net2.parameters():
        t.add_(0.05)
ck2 = droot / "smoke2.pt"
torch.save({"model": net2.state_dict(),
            "config": {"state_dim": feat.state_dim(), "cand_dim": feat.cand_dim(),
                       "hidden": 32, "head": 16},
            "feature_version": feat.FEATURE_VERSION}, ck2)
cmp3 = dagger.compare_arms(droot / "compact", "val",
                           {"baseline": ck, "dagger": ck2, "control": ck},
                           {"baseline": "基线", "dagger": "新", "control": "对照"},
                           "cpu", 2, pairs=[("baseline", "dagger"), ("control", "dagger")])
ok(set(cmp3["arms"]) == {"baseline", "dagger", "control"}, "多臂：三条臂都评了")
ok(all({"top1", "top1_type", "nll", "label"} <= set(a) for a in cmp3["arms"].values()),
   "多臂：每臂都给 top-1 / 类型 / nll / 显示名")
eq("多臂：两个配对差都算了（差 = 后者 − 前者）", sorted(cmp3["pairs"]),
   ["dagger − baseline", "dagger − control"])
ok(all({"mean", "lo", "hi", "p_win"} <= set(d) for d in cmp3["pairs"].values()),
   "多臂：配对差带聚类 CI")
eq("多臂：同权重两臂的差恰好为 0（'对照 vs 基线'那种无变化必须报 0）",
   cmp3["pairs"]["dagger − baseline"]["mean"],
   cmp3["arms"]["dagger"]["top1"] - cmp3["arms"]["baseline"]["top1"])
ok(json.dumps(cmp3, default=dagger._no_numpy), "多臂结果可以直接写进报告（没有 numpy 数组漏出去）")

print("== 旧格式数据集的全路径回退（重算并逐名核对）==")
old = scratch("dataset-oldmeta")
shutil.copytree(droot / "compact", old, dirs_exist_ok=True)
meta_old = json.loads((old / "meta.json").read_text(encoding="utf-8"))
want_val = sorted(meta_old["val_files"])
for k in ("train_files_path", "val_files_path"):        # 模拟 2026-09 之前的 meta
    meta_old.pop(k)
meta_old["src"] = meta_old["src"][0]                    # ⚠ 旧版 `src` 是**字符串**（不是列表）
( old / "meta.json" ).write_text(json.dumps(meta_old, ensure_ascii=False), encoding="utf-8")
got_val = dagger._split_files_of(old, "val")
eq("旧 meta：用 src+seed+val_frac 重算出的验证场与 meta 记的一致",
   sorted(p.name for p in got_val), want_val)
meta_bad = dict(meta_old, val_files=["g999.jsonl"])
( old / "meta.json" ).write_text(json.dumps(meta_bad, ensure_ascii=False), encoding="utf-8")
try:                                    # 红证：重算对不上就必须报错，不许拿它当裁判
    dagger._split_files_of(old, "val")
    ok(False, "重算与 meta 对不上时必须报错")
except SystemExit as e:
    ok("重算对不上" in str(e), "旧 meta 重算对不上时报错（不拿它当裁判）", str(e)[:50])

print("== P5b 混合（teacher 先验）的 α 曲线 ==")
lg = np.array([[5.0, 1.0, 2.0], [1.0, 9.0, 3.0]])
m = hyb.margins_from_logits(lg, np.array([1, 0]))
eq("margin：label 那条自己不算对手（取「除它以外」的最大 logit）",
   [round(float(x), 6) for x in m], [4.0, 8.0])
eq("margin：学生本来就对且很自信 → 负 margin（α 小根本翻不动）",
   round(float(hyb.margins_from_logits(np.array([[9.0, 1.0]]), np.array([0]))[0]), 6), -8.0)
one = hyb.margins_from_logits(np.array([[1.0, -np.inf]]), np.array([0]))
ok(bool(np.isneginf(one[0])), "只有一个合法候选时 margin = -inf（没有对手可翻）")
eq("该行在任何 α>0 下都算「让位」（老师就是唯一合法动作）",
   hyb.defer_curve(one, (1.0,))[1.0], 1.0)
eq("α ≤ 0 时不让位（纯网络不动）", hyb.defer_curve(np.array([0.5]), (0.0,))[0.0], 0.0)
curve = hyb.defer_curve(np.array([0.5, 2.0, 5.0]), (1.0, 3.0, 10.0))
eq("让位曲线：P(margin < α)",
   [round(curve[a], 6) for a in (1.0, 3.0, 10.0)], [round(1 / 3, 6), round(2 / 3, 6), 1.0])
ok(curve[1.0] <= curve[3.0] <= curve[10.0], "让位曲线单调不减")
eq("推荐 α = 让位首次达标的最小 α", hyb.recommend_alpha(np.array([0.5, 2.0, 5.0]), 0.66,
                                                       (1.0, 3.0, 10.0)), 3.0)
eq("目标只有大 α 能满足时，推荐那个大 α（margin=2.5 → α=3 才翻身）",
   hyb.recommend_alpha(np.array([2.5]), 1.0, (1.0, 3.0)), 3.0)
try:                                    # 标签必须是候选下标：越界要说出来，别算出莫名其妙的 margin
    hyb.margins_from_logits(np.array([[1.0, 2.0]]), np.array([7]))
    ok(False, "标签越界必须报错")
except ValueError as e:
    ok("标签越界" in str(e), "标签越界时报错（不是拿别的列当老师）", str(e)[:40])

print("== P3 离线 RL：转移组装与价值校准 ==")


def _rl_split(file, hand, seat, delta, **kw):
    """造一份"只给 P3 需要的列"的切分（不需要真的建数据集 → 自检是秒级的）。"""
    n = len(file)
    return {"state": np.zeros((n, feat.state_dim()), np.float16),
            "cand": np.zeros((n, 2, feat.cand_dim()), np.uint8),
            "n_legal": np.full(n, 2, np.int16), "label": np.zeros(n, np.int16),
            "delta": np.asarray(delta, np.int32), "game": np.zeros(n, np.int32),
            "file": np.asarray(file, np.int32), "hand_no": np.asarray(hand, np.int16),
            "seat": np.asarray(seat, np.int8),
            "placement": np.asarray(kw.get("placement", [-1] * n), np.int8),
            "is_student": np.asarray(kw.get("student", [0] * n), np.uint8),
            "meta": {"feature_version": feat.FEATURE_VERSION}}


# 座位 0 在小局 0 打了 2 手（第 2 条是末决策），座位 1 打了 1 手；第 4 条是**另一场**的同一 (小局,座位)
D = [8000, -8000, 0, 0]
sp = _rl_split([0, 0, 0, 1], [0, 0, 0, 0], [0, 1, 0, 0], [D, D, D, D], placement=[3, 1, 2, 4])
tr2 = rewards.transitions(sp, rank_weight=0.0)
eq("转移：同家同小局的链（第 0 条 → 第 2 条；第 3 条是另一场 → 不连）",
   list(map(int, tr2["nxt"])), [2, -1, -1, -1])
eq("转移：done = 本小局该家最后一条", list(map(bool, tr2["done"])), [False, True, True, True])
eq("转移：奖励只在末决策（千点）—— 座位 0 赢 8、座位 1 输 8",
   [round(float(x), 4) for x in tr2["reward"]], [0.0, -8.0, 8.0, 8.0])
eq("转移：return-to-go 与末决策奖励同值（γ=1 且只有一个非零奖励）",
   [round(float(x), 4) for x in tr2["ret"]], [8.0, -8.0, 8.0, 8.0])
eq("转移：奖励读的是**行动那一家**的收支（座位 1 → delta[1]）",
   round(float(tr2["ret"][1]), 4), -8.0)
eq("转移：episode 数（三组：f0h0s0 / f0h0s1 / f1h0s0）", tr2["stats"]["episodes"], 3)
eq("转移：学生行占比、赢/输占比",
   (tr2["stats"]["student_row_frac"], tr2["stats"]["win_frac"], tr2["stats"]["deal_in_frac"]),
   (0.0, 0.75, 0.25))
sp_stu = _rl_split([0], [0], [0], [[1000, 0, 0, 0]], student=[1])
eq("转移：is_student 从行里读（net: 打的才算）",
   rewards.transitions(sp_stu, rank_weight=0.0)["stats"]["student_row_frac"], 1.0)
try:                                    # 旧数据集没有这些列 → 必须**明确报错**而不是猜
    rewards.transitions({"state": np.zeros((1, feat.state_dim()), np.float16),
                         "delta": np.zeros((1, 4), np.int32),
                         "file": None, "hand_no": None, "seat": None,
                         "placement": None, "is_student": None})
    ok(False, "缺 P3 列时必须报错")
except ValueError as e:
    ok("缺 P3 需要的列" in str(e) and "重新" in str(e),
       "缺 P3 列时报错并告诉你要重建", str(e)[:60])
try:                                    # 座位越界要说出来（否则会读到别家的收支）
    rewards.hand_delta_of_seat(_rl_split([0], [0], [7], [[0, 0, 0, 0]]))
    ok(False, "seat 越界必须报错")
except ValueError as e:
    ok("seat 越界" in str(e), "seat 越界时报错（不读错别家的收支）", str(e)[:40])

# ---- 校准：与两个同粒度基线一起看
v_perfect = np.array([5.0, 5.0, 5.0, 1.0, 1.0, 1.0])
ret_p = np.array([5.0, 5.0, 5.0, 1.0, 1.0, 1.0])
ep_p = np.array([0, 0, 0, 1, 1, 1])
c_perfect = offline_rl.calibration(v_perfect, ret_p, ep_p)
eq("校准：完美预测 → MAE 0 且赢过两个基线",
   (c_perfect["mae"], c_perfect["beats_zero"], c_perfect["beats_const"]), (0.0, True, True))
eq("校准：完美预测的小局级 MAE = 0", c_perfect["ep_mae"], 0.0)
c_zero = offline_rl.calibration(np.zeros(6), ret_p, ep_p)
eq("校准：恒预测 0 的 MAE 正好等于 mae_zero（基线不是摆设）",
   (c_zero["mae"], c_zero["mae"]), (c_zero["mae_zero"], c_zero["mae_zero"]))
ok(c_zero["beats_zero"] is False, "恒预测 0 不能号称赢过恒预测 0")
ret_flat = np.array([2.0, 2.0, 4.0, 4.0])
c_const = offline_rl.calibration(np.full(4, 3.0), ret_flat, np.array([0, 0, 1, 1]))
eq("校准：恒预测均值 → mae == mae_const 且不算赢",
   (round(c_const["mae"], 6), round(c_const["mae_const"], 6), c_const["beats_const"]),
   (1.0, 1.0, False))
c_bad = offline_rl.calibration(-ret_flat, ret_flat, np.array([0, 0, 1, 1]))
ok(c_bad["pearson"] == -1.0 and c_bad["spearman"] == -1.0,
   "校准：完全反向预测 → 相关系数 -1（不是「看起来还行」的 MAE）",
   f"pearson={c_bad['pearson']}")
eq("校准：分箱可靠性曲线的箱数 ≤ bins 且 pred/actual 都在", len(c_perfect["reliability"]) > 0
   and {"bin", "n", "pred", "actual"} <= set(c_perfect["reliability"][0]), True)
ok(offline_rl._pearson(np.array([1.0, 2.0, 3.0]), np.array([1.0, 4.0, 9.0])) > 0.9
   and offline_rl._spearman(np.array([1.0, 2.0, 3.0]), np.array([1.0, 4.0, 9.0])) == 1.0,
   "秩相关：单调非线性 → Spearman 恰好 1（Pearson < 1）")

print("== P3 判据②：AWR 的权重与有效样本量 ==")
w1 = awr.weights_from_adv(np.array([0.0, 1.0, -1.0]), beta=3.0, w_max=100.0)
ok(abs(w1[1] / w1[0] - np.exp(3.0)) < 1e-9 and abs(w1[2] / w1[0] - np.exp(-3.0)) < 1e-9,
   "权重 = exp(β·A)：两两比值精确（整体尺度不影响归一化后的梯度）", f"{w1}")
ok(abs(float(awr.weights_from_adv(np.array([0.0, 50.0]), beta=3.0, w_max=20.0).max())
       - 20.0) < 1e-9,
   "权重上限被 clip 住（先夹指数再 exp ⇒ w_max 真的绑得上；靠浮点相等会假红）")
eq("极端优势不会溢出成 inf/nan",
   bool(np.isfinite(awr.weights_from_adv(np.array([0.0, 1e6, -1e6]), 3.0, 1e9)).all()), True)
eq("ESS：等权时 = 样本数", round(awr.ess(np.ones(100)), 6), 100.0)
eq("ESS：全权重集中在一个样本上 = 1", round(awr.ess(np.array([1.0, 0.0, 0.0])), 6), 1.0)
adv_grid = np.linspace(-3.0, 3.0, 1000)
e1, e5 = awr.ess(awr.weights_from_adv(adv_grid, 1.0, 1e9)), \
    awr.ess(awr.weights_from_adv(adv_grid, 5.0, 1e9))
ok(e5 < e1, "β 越大 ESS 越小（这是选 β 的护栏：ESS 塌了就说明只有极少数决策在说话）",
   f"ESS(β=1)={e1:.0f} > ESS(β=5)={e5:.0f}")

print("== P3 整场顺位点终局项 ==")
# 纯函数：整场奖励只记在「本场最后一小局 × 该家最后一次决策」上
sp2 = _rl_split([0, 0, 0, 0], [0, 1, 1, 1], [0, 0, 1, 0], [[1000, 0, 0, 0]] * 4)
tm = rewards.terminal_mask(sp2)
ok(list(map(bool, tm)) == [False, False, True, True],
   "整场奖励记在末小局里**每家各自**的最后一次决策上（行序：第 2 条是座位 1、第 3 条是座位 0）",
   f"{list(map(bool, tm))}")
ok(list(map(bool, rewards.terminal_mask(_rl_split([0, 1], [0, 2], [0, 0],
                                                  [[0, 0, 0, 0]] * 2)))) == [True, True],
   "每个文件各自算自己那场的末小局（file=0 的末小局是 0、file=1 的是 2）")
# 端到端：紧凑集 meta 的全路径 → run 目录 summary.json 的 per_game[].rank_points[seat]
rpdir = scratch("rank-pts")
run = rpdir / "raw" / "run-x"
run.mkdir(parents=True, exist_ok=True)
for g in (0, 1):
    with (run / f"g{g}.jsonl").open("w", encoding="utf-8") as fh:
        for i in range(2):
            row = {"type": "decision", "game": g, "hand_no": 0, "step": i, "seat": 0,
                   "policy": "teacher", "kind": "turn", "placement": [2, 1, 4, 3],
                   "legal": ["discard:1m", "pass"], "chosen": "discard:1m", "chosen_index": 0,
                   "hand_delta": [100, -100, 0, 0], "obs": _fake_obs(["discard:1m", "pass"])}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    write_sidecar(run / f"g{g}.jsonl",
                  [dict(PER_SEAT_ROW, danger=DANGER, cand=CAND0) for _ in range(2)])
(run / "summary.json").write_text(json.dumps({
    "games": 2, "hands": 2, "per_game": [
        {"game": 0, "rank_points": [61.6, 15.8, -10.0, -30.0]},
        {"game": 1, "rank_points": [5.0, 10.0, -5.0, -10.0]}]}, ensure_ascii=False),
    encoding="utf-8")
ds.build(run, rpdir / "compact", val_frac=0.5, split_seed=0, label_source="auto", quiet=True)
sp_rp = ds.load_split(rpdir / "compact", "train")
sp_rp["meta"]["train_files_path"] = [str(run / "g0.jsonl")]      # 单文件切分：钉住映射
rp_rows = rewards.rank_points_by_row(sp_rp)
eq("顺位点按 (file → run 目录 → 场序号 → seat) 对到行上：seat=0 的场 0 = 61.6",
   [round(float(x), 2) for x in rp_rows], [61.6, 61.6])
tr_rp = rewards.transitions(sp_rp, rank_weight=1.0)
eq("顺位点进 return-to-go（千点量纲，不再除以 1000）",
   [round(float(x), 2) for x in tr_rp["ret"]], [61.7, 61.7])
eq("整场项只落在末决策那条转移上（小局收支 + 顺位点）",
   [round(float(x), 2) for x in tr_rp["reward"]], [0.0, 61.7])
eq("λ=0 时回到第一轮的口径（只有小局收支）",
   [round(float(x), 2) for x in rewards.transitions(sp_rp, rank_weight=0.0)["ret"]],
   [0.1, 0.1])
try:            # ① 轨迹在、但那个 run 目录没有 summary.json → 报错并说清（不能悄悄当 0）
    nosum = rpdir / "raw" / "no-summary"
    nosum.mkdir(parents=True, exist_ok=True)
    (nosum / "g0.jsonl").write_text("", encoding="utf-8")
    sp_gone = dict(sp_rp)
    sp_gone["meta"] = dict(sp_rp["meta"], train_files_path=[str(nosum / "g0.jsonl")])
    rewards.rank_points_by_row(sp_gone)
    ok(False, "缺 summary.json 时必须报错")
except FileNotFoundError as e:
    ok("summary.json" in str(e), "轨迹在但缺 summary.json 时报错", str(e)[:60])
try:            # ② 轨迹本身不在（且按数据根重映射也找不到）→ 报错，别退化成"顺位点=0"
    sp_miss = dict(sp_rp)
    sp_miss["meta"] = dict(sp_rp["meta"],
                           train_files_path=[str(rpdir / "raw" / "ghost" / "g0.jsonl")])
    rewards.rank_points_by_row(sp_miss)
    ok(False, "轨迹不在时必须报错")
except FileNotFoundError as e:
    ok("重映射" in str(e), "轨迹不在时报错（不悄悄当成 0）", str(e)[:60])
# 迁盘遗留：meta 里记的是**旧数据根**的路径时，按当前数据根重映射（原路径存在就不动）
sp_old = dict(sp_rp)
sp_old["meta"] = dict(sp_rp["meta"],
                      train_files_path=[str(run / "g0.jsonl").replace("S:\\", "T:\\")])
eq("迁盘前的紧凑集（meta 里是旧根 T:）按当前数据根重映射后照样读得到顺位点",
   [round(float(x), 2) for x in rewards.rank_points_by_row(sp_old)], [61.6, 61.6])
try:                                    # 路径里根本没有 `raw/` 段 → 无法重映射，必须报错
    sp_bad = dict(sp_rp)
    sp_bad["meta"] = dict(sp_rp["meta"], train_files_path=["Z:\\nowhere\\g0.jsonl"])
    rewards.rank_points_by_row(sp_bad)
    ok(False, "无法重映射时必须报错")
except FileNotFoundError as e:
    ok("无法按数据根重映射" in str(e), "无法重映射时报错（不悄悄当成 0）", str(e)[:50])

print("== P0 评测：合并多次独立 run（把效应钉到显著）==")


def _fake_run(path, seed_base, diffs, label_a="net:A", label_b="net:B"):
    """造一次 run 的 per_game：A 坐 0/2、B 坐 1/3，逐场差值 = `diffs[i]`（单位：顺位点）。

    ⚠ 每席给 `dz/2`：`per_game_series` 对同一策略占的**多席取平均**，所以两席各 dz/2 才让
    整场差值恰好等于 dz（这一点第一次写测试时就搞错了 —— 差值被放大成 2dz）。
    """
    per_game = []
    for i, dz in enumerate(diffs):
        half = dz / 2.0
        per_game.append({"game": i, "seed": seed_base * 1000 + i,
                         "policies": [label_a, label_b, label_a, label_b],
                         "rank_points": [half, -half, half, -half], "placement": [1, 2, 1, 2]})
    return ml_eval.Run(path=Path(path), games=len(diffs), hands=len(diffs), seed_base=seed_base,
                       workers=1, by_policy={}, per_game=per_game)


r1 = _fake_run("run-a", 1, [10.0, 20.0])
r2 = _fake_run("run-b", 2, [-5.0, 15.0, 5.0])
pooled, per = ml_eval.pool_diffs([r1, r2], "net:A", "net:B", "rank_points")
eq("合并：样本量 = 各 run 之和", pooled.size, 5)
eq("合并：逐 run 摘要（n 与 Δ）",
   [(x["n"], round(x["delta"], 3)) for x in per], [(2, 15.0), (3, 5.0)])
eq("合并：均值 = 全部逐场差的均值",
   round(float(pooled.mean()), 6), round((10 + 20 - 5 + 15 + 5) / 5, 6))
p_ok = ml_eval._paired_from_diffs("net:A", "net:B", "rank_points", pooled)
eq("合并：CI 由同一套自助法给出（不是另写一套统计）", (p_ok.n, p_ok.win, p_ok.lose), (5, 4, 1))
eq("单个 run 的配对检验还是原来那条路（合并模式不改变它）",
   ml_eval.paired_test(ml_eval.per_game_series(r1, "net:A", "rank_points"),
                       ml_eval.per_game_series(r1, "net:B", "rank_points"),
                       "net:A", "net:B", "rank_points").n, 2)
try:            # 红证：两次 run 若共用牌山（seed 相同），合并会把同一副牌数两遍 ⇒ 必须报错
    ml_eval.pool_diffs([r1, _fake_run("run-c", 1, [1.0, 2.0])], "net:A", "net:B", "rank_points")
    ok(False, "牌山重叠时必须报错")
except ValueError as e:
    ok("牌山重叠" in str(e) and "两遍" in str(e),
       "两次 run 共用一个 seed 时报错（不许把同一副牌重复计入）", str(e)[:60])
eq("标签子串能解析成完整标签", ml_eval.resolve_label(r1, "A"), "net:A")
try:            # 子串命中多个时必须报错，不许猜（猜错会把两个策略的号混在一起算）
    ml_eval.resolve_label(r1, "net:")
    ok(False, "子串命中多个策略时必须报错")
except ValueError as e:
    ok("命中" in str(e), "标签子串命中不唯一时报错（不猜）", str(e)[:50])
# ⚠ 样本量反推的口径：只用 z_{α/2} 是 **50% 功效**（效应正好压在临界线上），会低估约一半
n50 = ml_eval.required_n(53.0, 2.0, power=0.5)
n80 = ml_eval.required_n(53.0, 2.0)
ok(abs(n80 / n50 - 2.0432) < 0.001 and n80 > n50,
   "required_n 默认按 **80% 功效**（(1.96+0.84)²/1.96² ≈ 2.04 倍于 50% 功效的场次）",
   f"50% → {n50} 场，80% → {n80} 场")
# run 级随机效应：批间散度要真的进 CI（同质时不该变宽，异质时必须变宽）
same = [{"delta": 1.0, "se": 0.5}, {"delta": 1.0, "se": 0.5}, {"delta": 1.0, "se": 0.5}]
re_same = ml_eval.random_effects(same)
ok(re_same["tau"] == 0.0 and abs(re_same["mu"] - 1.0) < 1e-9,
   "随机效应：三批完全相同 → τ=0 且点估计不变", f"τ={re_same['tau']} μ={re_same['mu']}")
het = [{"delta": 1.0, "se": 0.5}, {"delta": 0.9, "se": 0.5}, {"delta": 3.2, "se": 0.5}]
re_het = ml_eval.random_effects(het)
fixed_w = 1.0 / (0.5 ** 2)
fixed_mu = sum(r["delta"] * fixed_w for r in het) / (3 * fixed_w)
fixed_se = float(np.sqrt(1.0 / (3 * fixed_w)))
ok(re_het["tau"] > 0 and (re_het["ci"][1] - re_het["ci"][0]) > 2 * 1.96 * fixed_se,
   "随机效应：批间散度大 → τ>0 且 CI 明显宽于逐场口径（不许把不确定性说窄）",
   f"τ={re_het['tau']:.2f} 逐场半宽={1.96 * fixed_se:.2f} RE半宽={(re_het['ci'][1] - re_het['ci'][0]) / 2:.2f}")
# ⚠ 各批 SE **相等**时两种口径的点估计必然相同（权重都是常数）——要测"权重被 τ² 改过"，必须让 SE 不等
het2 = [{"delta": 1.0, "se": 0.3}, {"delta": 2.0, "se": 0.8}, {"delta": 4.0, "se": 1.2}]
w2 = [1.0 / r["se"] ** 2 for r in het2]
fixed2 = sum(r["delta"] * wi for r, wi in zip(het2, w2)) / sum(w2)
re2 = ml_eval.random_effects(het2)
ok(abs(re2["mu"] - fixed2) > 1e-6,
   "随机效应：SE 不等时 τ² 会改写权重 ⇒ 点估计与逐场口径不同",
   f"RE={re2['mu']:.3f} vs 逐场={fixed2:.3f}")
eq("随机效应：只有一批时不给假 CI（df=0）",
   bool(np.isnan(ml_eval.random_effects([{"delta": 1.0, "se": 0.5}])["tau"])), True)
eq("required_n：Δ 越大所需场次越少（单调）",
   ml_eval.required_n(53.0, 4.0) < ml_eval.required_n(53.0, 2.0), True)

# ---------------------------------------------------------------- P4：PPO 的数学与联赛

# ① `logprobs` 就是 log-softmax（掩码位置概率恒 0）—— PPO 的比率全靠它，先钉住这一条
_net = nets.build(3, 2, hidden=4, head=4)
_state = torch.zeros(2, 3)
_cand = torch.zeros(2, 3, 2)
_mask = torch.tensor([[True, True, False], [True, True, True]])
_lp = ppo.logprobs(_net, _state, _cand, _mask, 1.0)
ok(torch.all(torch.isfinite(_lp[_mask])), "PPO：合法候选的 log 概率有限")
ok(bool((_lp[~_mask] == float("-inf")).all()), "PPO：掩码位置 = -inf（softmax 后概率恒 0）")
eq("PPO：logp 的指数在合法候选上求和 = 1", round(float(_lp[0][:2].exp().sum()), 6), 1.0)
ok(abs(float(ppo.chosen_logprob(_lp, torch.tensor([1, 2]))[0]) - float(_lp[0, 1])) < 1e-9,
   "PPO：chosen_logprob 取的就是数据里那个动作（不是 argmax）")

# ② 熵：均匀 2 候选 = ln2；掩码位置不参与 —— 且**反向不能是 NaN**
#    （`0·log 0` 那个经典坑：前向看着对、反向把整批梯度污染）
_lpu = ppo.logprobs(_net, _state, _cand, _mask, 1.0)
ent = ppo.entropy_of(_lpu, _mask)
ok(ent.requires_grad, "PPO：熵对 logits 可导（会被加进损失）")
ent.sum().backward()
ok(all(p.grad is None or bool(torch.isfinite(p.grad).all()) for p in _net.parameters()),
   "PPO：熵的反向梯度有限（掩码位置的 0·(-inf) 不许变成 NaN）", "")
_net.zero_grad()
# 真·均匀 logp（不是"把 logp 清零"—— 那是个非法分布，测出来的熵恒 0）
_log2 = float(np.log(2))
_uniform = torch.tensor([[-_log2, -_log2, float("-inf")], [0.0, float("-inf"), float("-inf")]])
ent2 = ppo.entropy_of(_uniform, torch.tensor([[True, True, False], [True, False, False]]))
ok(abs(float(ent2[0]) - _log2) < 1e-6 and abs(float(ent2[1])) < 1e-6,
   "PPO：同分 n 个候选的熵 = ln n（2 个 → ln2；1 个 → 0）",
   f"ent={float(ent2[0]):.6f}/{float(ent2[1]):.6f}")

# ③ GAE：`rewards.py` 的口径（γ=1、整条 episode 只有末决策有奖励）+ **λ=1** 时必须塌成 R − V
#    ⚠ 只有 λ=1 才等价：λ<1 时前几步的 A 会被"剩余步数"衰减（对单点奖励就是一种系统性偏差），
#    所以 ppo.py 的默认就是 λ=1（TD(1) = 蒙特卡洛）。这条断言把这个默认值钉住。
_rew = np.array([0.0, 0.0, 3.0, 0.0, 0.0, -1.0])       # 两条 episode（0→1→2 与 3→4→5）
_nxt = np.array([1, 2, -1, 4, 5, -1])
_v = np.array([0.5, -0.2, 1.0, 2.0, 0.0, -0.5])
_adv, _vt = ppo.gae(_rew, _nxt, _v, gamma=1.0, lam=1.0)
eq("PPO：γ=1 + 末决策单点奖励 + λ=1 ⇒ GAE 塌成 R − V（逐条）",
   [round(float(x), 6) for x in _adv], [2.5, 3.2, 2.0, -3.0, -1.0, -0.5])
eq("PPO：value_target = advantage + V（TD(λ) 回报估计）",
   [round(float(x), 6) for x in _vt], [3.0, 3.0, 3.0, -1.0, -1.0, -1.0])
_adv_lam = ppo.gae(_rew, _nxt, _v, gamma=1.0, lam=0.5)[0]
ok(abs(float(_adv_lam[0]) - 0.4) < 1e-9 and abs(float(_adv_lam[0]) - 2.5) > 1e-3,
   "PPO：λ<1 时**不再**等于 R − V（所以默认值只能是 1；这条防「λ 被悄悄改回 0.95」）",
   f"A0(λ=.5)={float(_adv_lam[0]):+.4f}（手算 δ0+0.5·(δ1+0.5·δ2) = −0.7+0.5·2.2）"
   f" vs A0(λ=1)=+2.5000")
_adv2, _ = ppo.gae(_rew, _nxt, _v, gamma=0.5, lam=1.0)   # 折扣真的生效（否则上面那条可能是巧合）
ok(abs(float(_adv2[0]) - 0.25) < 1e-9,
   "PPO：γ<1 时按 (γ·V_next − V) + γλ·A_next 递推（手算 A0 = δ0 + 0.5·A1 = −0.6 + 0.85）",
   f"A0={float(_adv2[0]):+.4f} 期望 +0.2500")

# ④ 优势只在**学生行**上归一化（对手行不进策略损失，混进来会把尺度带跑）
_adv_raw = np.array([10.0, -10.0, 0.5, 0.7])
_stu = np.array([True, True, False, False])
_an = ppo.normalize_adv(_adv_raw, _stu)
ok(abs(float(_an[_stu].mean())) < 1e-6 and abs(float(_an[_stu].std()) - 1.0) < 1e-6,
   "PPO：优势归一化只按学生行（学生行均值 0 / std 1）")
ok(bool(np.sign(_an[2] - _an[3]) == np.sign(_adv_raw[2] - _adv_raw[3])),
   "PPO：优势归一化是同一个线性变换（对手之间的次序不许被改）")

# ⑤ 裁剪的**方向**要能看出"梯度被切断"：ratio 越过上界后代理目标变成常数
_surr = torch.min(torch.tensor([3.0]) * 2.0,
                  torch.clamp(torch.tensor([3.0]), 0.8, 1.2) * 2.0)
eq("PPO：正优势 + ratio 越过上界 ⇒ 代理目标被夹在 (1+ε)·A（梯度为 0）",
   round(float(_surr), 6), round(1.2 * 2.0, 6))
_surr_neg = torch.min(torch.tensor([0.1]) * -2.0,
                      torch.clamp(torch.tensor([0.1]), 0.8, 1.2) * -2.0)
eq("PPO：负优势 + ratio 越过下界 ⇒ 同样被夹住（两个方向都要防）",
   round(float(_surr_neg), 6), round(0.8 * -2.0, 6))
eq("PPO：clip_fraction 数的是 |ratio−1|>ε 的比例",
   ppo.clip_fraction(np.array([0.5, 1.0, 1.3]), 0.2), 2 / 3)
eq("PPO：approx_kl 同号同量纲（ratio=1 时恒 0）",
   round(ppo.approx_kl(np.array([0.0, 0.0]), np.array([0.0, 0.0])), 9), 0.0)

# ⑥ 联赛：短名要认得出五种文法（`#T` 与目录名里带 `@` 的路径都不能被误切）
eq("联赛：short_name 认五种文法",
   [ml_league.short_name("teacher"), ml_league.short_name("first"),
    ml_league.short_name(r"net:S:\x\ckpt\awr-002\net.bin"),
    ml_league.short_name(r"net:S:\x\ckpt\awr-002\net.bin@2"),
    ml_league.short_name(r"net:S:\x\ckpt\awr-002\net.bin@2#1.5")],
   ["teacher", "first", "awr-002", "awr-002@2", "awr-002@2#1.5"])

# ⑦ Plackett-Luce：解析梯度必须**与数值差分一致**。
#    ⚠ 这条是防"任务书里那行简写复辟"的：`∂ℓ/∂θ_{p_j} += 1 − e^{θ_{p_j}}/S_j` 只对第 1 名成立，
#    按它实现时真值处梯度 ≈ +0.5（压根不驻点）、2000 场也恢复不出 θ。
#    ℓ 这里**独立重写一遍**（自检不复用被测实现，才有资格当判据）。
def _pl_loglik(theta: np.ndarray, idx: np.ndarray) -> float:
    total = 0.0
    for row in idx:
        z = theta[row]
        for j in range(len(row)):
            m = float(z[j:].max())
            total += float(z[j]) - (m + float(np.log(np.exp(z[j:] - m).sum())))
    return total


_rng = np.random.default_rng(7)
_theta = _rng.normal(0, 0.5, 4)
_idx = np.array([[0, 1, 2, 3], [1, 0, 3, 2], [2, 3, 0, 1], [3, 2, 1, 0]], dtype=np.intp)
_g = ml_league._lik_grad_sum(_theta, _idx, 4)
_num = np.zeros(4)
for _i in range(4):
    _p, _m = _theta.copy(), _theta.copy()
    _p[_i] += 1e-6
    _m[_i] -= 1e-6
    _num[_i] = (_pl_loglik(_p, _idx) - _pl_loglik(_m, _idx)) / 2e-6
ok(float(np.max(np.abs(_g - _num))) < 1e-4,
   "联赛：PL 解析梯度 == 数值差分（简写那版在这里会差 0.5）",
   f"最大偏差 {float(np.max(np.abs(_g - _num))):.2e}")

# ⑧ 合成数据恢复 + 红证：等 θ 时**不许**造出显著差异（SE 不能靠撑大来好看）
def _synth_pl(theta_true: dict[str, float], n: int, seed: int) -> list:
    names = list(theta_true)
    rng = np.random.default_rng(seed)
    th = np.array([theta_true[n] for n in names])
    out = []
    for _ in range(n):
        left = list(range(len(names)))
        order = []
        for _j in range(len(names)):
            z = th[left]
            p = np.exp(z - z.max())
            p = p / p.sum()
            k = int(rng.choice(len(left), p=p))
            order.append(names[left[k]])
            left.pop(k)
        out.append(ml_league.Game(players=order, run="synth"))
    return out


_real = {"a": 0.8, "b": 0.25, "c": -0.25, "d": -0.8}
_fit = ml_league.plackett_luce(_synth_pl(_real, 600, 11))
ok([p for p, _, _, _ in ml_league.ladder(_fit)] == ["a", "b", "c", "d"],
   "联赛：600 场合成数据恢复出真值排序",
   " / ".join(f"{p}:{_fit.theta[p]:+.2f}" for p in _fit.players))
_ci = ml_league.pl_ci(_synth_pl(_real, 600, 11), n_boot=40, seed=3)
ok(all(_ci[p][0] <= _real[p] <= _ci[p][1] for p in _real),
   "联赛：四个真值都落在 95%CI 内", str({p: (round(_ci[p][0], 2), round(_ci[p][1], 2)) for p in _real}))
_flat = ml_league.plackett_luce(_synth_pl({p: 0.0 for p in _real}, 600, 12))
ok(all(_flat.theta[p] - 1.96 * _flat.se[p] < 0 < _flat.theta[p] + 1.96 * _flat.se[p]
       for p in _flat.players),
   "联赛红证：等 θ 时四个 CI **全部跨 0**（SE 没有被人为压小）",
   str({p: round(_flat.theta[p], 2) for p in _flat.players}))

# ⑨ 采样权重：**比 focus 强的对手权重更大**（负号写反就会变成"专挑软柿子"）、Σw=1、floor 抬底
_sw = ml_league.select_weights(_fit, focus="c", floor=0.1, exponent=1.0)
ok(_sw["a"] > _sw["b"] > _sw["c"] and _sw["c"] < _sw["d"] + 1e-12 or _sw["a"] > _sw["d"],
   "联赛：θ 越高的对手采样权重越大（「谁克我就多跟它打」）",
   str({k: round(v, 3) for k, v in _sw.items()}))
eq("联赛：采样权重归一（Σ=1）", round(sum(_sw.values()), 9), 1.0)
ok(min(_sw.values()) >= 0.1 / 4 - 1e-12,
   "联赛：floor 抬底生效（最弱的对手也有 ≥ floor/N 的份额）", f"min={min(_sw.values()):.4f}")
_r1 = ml_league.sample_opponents(_sw, 3, rng=np.random.default_rng(5))
_r2 = ml_league.sample_opponents(_sw, 3, rng=np.random.default_rng(5))
eq("联赛：不放回抽样同 seed 逐元素可复现", _r1, _r2)
ok(len(set(_r1)) == 3 and "c" not in ml_league.sample_opponents(_sw, 3, rng=np.random.default_rng(9),
                                                         exclude="c"),
   "联赛：不放回、且 exclude 的对手抽不到", str(_r1))
ok(ml_league.sample_opponents({"a": 1.0, "b": 0.0}, 1, rng=np.random.default_rng(1)) == ["a"],
   "联赛：权重为 0 的对手抽不到（即使它排在后面对手池里）")
ok(ml_league.sample_opponents({"a": 1.0}, 5, rng=np.random.default_rng(1)) == ["a"],
   "联赛：对手不足 k 个时能抽几个给几个（不报错、不重复）")

# ⑩ θ 差必须用**配对** bootstrap（同一次重抽内做减法）。⚠ 这条的动机是实战里看到的：
#    四个网络共享**同一个** teacher 估计，"四个都高于老师"看着像 4 份独立证据，
#    其实可能只是 1 份锚点偏移 —— 配对重抽把那件事算进 CI 里。
_pairs = _synth_pl({"teacher": 0.0, "g1": 0.35, "g2": 0.0}, 1200, 21)
_pd = ml_league.pl_ci_diff(_pairs, "g1", "teacher", n_boot=60, seed=5)
ok(_pd["ci"][0] > 0 and _pd["p"] < 0.05,
   "联赛：真差 +0.35 的配对 θ CI 排除 0",
   f"Δ={_pd['delta']:+.3f} CI=[{_pd['ci'][0]:+.3f},{_pd['ci'][1]:+.3f}] p={_pd['p']:.3f}")
# 红证/校准：**报告的区间**必须不窄于真实抽样散度。实测（40 个真值全 0 的合成集）：
#   · 数值 Hessian 的 `Fit.se` **低估** Δ̂ 的采样 SD（n=400 时低估 ~40%、n=1200 时 ~18%）——
#     它是渐近口径，小样本下不够用；
#   · 而**自助法**的区间与实测 SD 基本吻合（n=1200：自助半宽 0.106 vs 真 SD 0.0516×1.96=0.101）。
# 所以"判据"必须用 `pl_ci_diff`（自助法配对），`format_ladder` 里那列 Δ 半宽只当粗略参考。
_base = ml_league.plackett_luce(_pairs)
_hw_hess = 1.96 * ((_base.se["g1"] ** 2 + _base.se["teacher"] ** 2) ** 0.5)
_bd = ml_league.pl_ci_diff(_pairs, "g1", "teacher", n_boot=60, seed=5)
_hw_boot = (_bd["ci"][1] - _bd["ci"][0]) / 2
ok(_hw_boot > _hw_hess,
   "联赛：配对自助法区间比 Hessian 口径**宽**（Hessian 的 SE 是渐近口径、小样本会低估）",
   f"自助半宽 {_hw_boot:.3f} > Hessian 半宽 {_hw_hess:.3f}")
_sd_est = 0.0
for _s in range(1, 9):                                    # 8 个种子实测 Δ̂ 的散度（便宜版校准）
    _f = ml_league.plackett_luce(_synth_pl({"a": 0.0, "b": 0.0, "c": 0.0}, 400, 300 + _s))
    _d = _f.theta["b"] - _f.theta["a"]
    _sd_est += _d * _d
_sd_est = (_sd_est / 7) ** 0.5
ok(_sd_est > 0.5 * (_base.se["g1"] ** 2 + _base.se["teacher"] ** 2) ** 0.5,
   "联赛红证：真值全 0 时实测 Δ̂ 散度与报告 SE **同量级**（SE 不是被压小一个数量级）",
   f"实测 SD≈{_sd_est:.4f} vs 报告 SE≈{(_base.se['g1'] ** 2 + _base.se['teacher'] ** 2) ** 0.5:.4f}")
_names, _th = ml_league.boot_thetas(_pairs, n_boot=60, seed=5)
_med = float(np.median(_th[:, _names.index("g1")] - _th[:, _names.index("teacher")]))
ok(_th.shape == (60, 3) and abs(_med - _pd["delta"]) < 0.02,
   "联赛：`boot_thetas` 一次重抽供多对比较复用，且与 `pl_ci_diff` 同源（不是两套统计）",
   f"抽样矩阵 {_th.shape}，中位差 {_med:+.3f} vs 点估计 {_pd['delta']:+.3f}")

# ⑪ 策略位移矩阵（P4 的"步长体检"）：Elo 的分辨率有限，一代只改 2% 决策时"没有趋势"是必然的 ——
#    所以先量位移。纯函数，直接喂合成数组。
from mahjong_ml import online as ml_online                    # noqa: E402
# ⚠ 2+2 配对**必须跑两种座位配置**：轮转公式 `src=(seat+game)%4` 保持策略表的块结构 ⇒
# `A,A,B,B` 只采样相邻两席、`A,B,A,B` 只采样对家两席（2026-09-26 用户指出后补的）。
eq("2+2 的两种座位配置（相邻 A,A,B,B / 对家 A,B,A,B）",
   ml_online._arrangement_specs("A", "B"),
   [("相邻", "A,A,B,B", "pair"), ("对家", "A,B,A,B", "pairX")])
_a = np.arange(100) % 4
_b = (np.arange(100) + 1) % 4                                  # 与 a 完全错开（循环移位）
_c = np.arange(100) % 4
_c[:10] = 99                                                   # 与 a 只差 10/100
_d = np.arange(100) % 4
_d[:25] = -1                                                   # 与 a 差 25/100
_nm, _m = ml_online.agreement_matrix({"a": _a, "b": _b, "c": _c, "d": _d})
eq("位移：对角线恒 1 且矩阵对称",
   (bool((np.diag(_m) == 1).all()), bool(np.allclose(_m, _m.T))), (True, True))
eq("位移：完全错开（循环移位）一致率 = 0", round(float(_m[0][1]), 6), 0.0)
eq("位移：改了 10/100 的两条 → 一致率 = 0.90", round(float(_m[0][2]), 6), 0.90)
eq("位移：改了 25/100 → 一致率 = 0.75（「看着差不多」在数字上就是 25% 的决策不同）",
   round(float(_m[0][3]), 6), 0.75)

# ⑫ 策略串解析（`pair` / 联赛都走它）：内置名原样、短名找 `ckpt/<名>/net.bin`、找不到就**报错**
eq("解析：内置名原样返回", [ml_online.resolve_spec(x) for x in ("teacher", "first")], ["teacher", "first"])
_real = paths.DATA_ROOT / "ckpt" / "ppo-g04" / "net.bin"
if _real.is_file():
    ok(ml_online.resolve_spec("ppo-g04") == f"net:{_real}",
       "解析：ckpt 短名 → net:<路径>", ml_online.resolve_spec("ppo-g04")[:40])
    ok(ml_online.resolve_spec(str(_real)) == f"net:{_real}",
       "解析：直接给 net.bin 路径也对")
try:
    ml_online.resolve_spec("definitely-not-a-net")
    eq("解析：找不到的策略必须报错（不许当成内置名跑）", "没报错", "应报错")
except SystemExit as _e:
    ok("找不到策略" in str(_e), "解析：找不到的策略报错并指出路径", str(_e)[:60])

# ⑬ **一轮有多长：时间预算**（`budget.py`）—— 2026-09-27 用户口径"记 30 分钟左右的训练为一轮"。
#    三条要害：① 成本模型**必须实测回填**（默认值只配给第一次）；② 资源闸门要能**夹住**时间预算，
#    且报告要说出**是谁**在夹（否则"30 分钟只跑了 12 分钟"看起来像 bug）；③ 资源供不起 `--min-games`
#    时必须**报错**，不许静默把轮次缩小到没意义。
print("== 时间预算（每轮多少场由目标时长反推）==")
eq("预算：默认秒/场 = (288 s − 50 s 固定) / 2000 场（2026-09-27 冒烟实测）",
   ml_budget.DEFAULT_PER_GAME, 0.1184)
eq("预算：默认固定开销 = 50 s/轮（两处独立实测都是 ~50 s；**别并进 per_game**）",
   ml_budget.DEFAULT_FIXED_SECONDS, 50.0)
eq("预算：分相占比之和 == 1（它只用来把预测时长拆开显示）",
   round(sum(ml_budget.DEFAULT_PHASE_SHARE.values()), 9), 1.0)
ok(set(ml_budget.DEFAULT_PHASE_SHARE) == set(ml_budget.PHASES),
   "预算：分相名单与 `online.cmd_run` 的落账字段一一对应", str(ml_budget.PHASES))
ok(0.0 < ml_budget.FREE_SPACE_SAFETY < 1.0,
   "预算：整盘余量只按 90% 排计划（配额类闸门**不打折** —— 它们是策略上限）",
   str(ml_budget.FREE_SPACE_SAFETY))
eq("预算：取整只对 ≥100 场生效（冒烟/自检要用小数字）",
   [ml_budget._floor100(x) for x in (20270, 99, 100, 0)], [20200, 99, 100, 0])
# 成本回填（≥2 个不同场次）→ **实测点上的分段线性（弦）**：
# ⚠ 为什么不是"一个全局 per_game"：成本曲线不线性（紧凑集装得进内存时便宜、装不进时每遍真读盘）。
# 实测两点 (1000, 100 s) / (3000, 280 s) 之间用弦；点外按端点规则外推（下方按比例、上方按末段斜率）。
_led = [
    {"generation": 1, "games": 1000, "total_seconds": 100.0,
     "phases": {"collect": 25.0, "features": 5.0, "compact": 25.0, "ppo": 42.0, "export": 3.0}},
    {"generation": 2, "games": 3000, "total_seconds": 280.0,
     "phases": {"collect": 75.0, "features": 15.0, "compact": 75.0, "ppo": 108.0, "export": 7.0}},
]
_c = ml_budget.fit_cost(_led)
eq("预算：边际秒/场 = 相邻实测点的弦斜率（(280−100)/(3000−1000) = 0.09）",
   round(_c.rate, 9), 0.09)
eq("预算：标定轮数如实报出（`source()` 要能说出这是实测还是默认值）", _c.rounds, 2)
eq("预算：分相占比也从台账来（collect = 100/380）", round(_c.share["collect"], 6), round(100.0 / 380, 6))
eq("预算：实测点上**逐点复现**",
   (round(_c.predict(1000), 6), round(_c.predict(3000), 6)), (100.0, 280.0))
eq("预算：两实测点之间走弦（t(2000) = 190 s）", round(_c.predict(2000), 6), 190.0)
eq("预算：比最大实测点还大 → 按**末段斜率**外推（保守：宁可排短）",
   round(_c.predict(5000), 6), round(280.0 + 0.09 * 2000, 6))
eq("预算：比最小实测点还小 → 按**比例缩**（`fixed + (t_min−fixed)·g/g_min`），绝不为负",
   (round(_c.predict(500), 6), round(_c.predict(0), 6)), (round(50 + 50 * 0.5, 6), 50.0))
ok(all(_c.predict(g) >= 0 for g in range(0, 20000, 500)),
   "预算：预测**处处非负**（⚠ 别用第一段斜率往下推：数据超内存那侧的斜率很陡，往下推会算出负时长 ⇒ "
   "小目标被排成大轮）")
ok(all(_c.predict(g) <= _c.predict(g + 500) for g in range(0, 19000, 500)),
   "预算：预测**单调不减**（场次多不能更省时间）")
eq("预算：`games_for` 与 `predict` 互逆（往返误差 ≤ 100 场，取整粒度）",
   abs(_c.games_for(_c.predict(2000)) - 2000) <= 100, True)
ok("分段线性" in _c.source(), "预算：`source()` 写明「实测点分段线性」与边际秒/场", _c.source())
# ⚠ 只有**一个**实测点：不能画弦 → 固定项取实测常数，`rate` 扣掉它（模型逐字复现那一轮）
_one = ml_budget.fit_cost([{"generation": 1, "games": 2000, "total_seconds": 288.0}])
eq("预算：单点台账 → rate = (288 − 50) / 2000", round(_one.rate, 6),
   round((288.0 - ml_budget.DEFAULT_FIXED_SECONDS) / 2000, 6))
eq("预算：单点台账**逐字复现**那一轮的实测总时长",
   round(_one.predict(2000), 6), 288.0)
eq("预算：单点也能看出「这是实测」（不是默认值）", _one.rounds, 1)
ok(_one.predict(2000) == 288.0 and _one.predict(4000) == 50 + (288.0 - 50) * 2,
   "预算：单点台账按 `fixed + rate·场次` 外推", f"t(4000)={_one.predict(4000):.0f}s")
# ⚠ 老台账只有 `collect_seconds`（单相）—— 拿它当整轮时长会低估到 1/4，必须**拒绝**、退回默认值
_old = [{"generation": 1, "games": 2000, "collect_seconds": 44.0}]
_c0 = ml_budget.fit_cost(_old)
eq("预算：只有 collect_seconds 的老台账**不参与标定**（单相时长会低估整轮）", _c0.rounds, 0)
eq("预算：没有可标定轮次就用默认值",
   (_c0.rate, _c0.fixed_seconds),
   (ml_budget.DEFAULT_PER_GAME, ml_budget.DEFAULT_FIXED_SECONDS))
eq("预算：坏值（负/NaN/非数）一律忽略，不许把模型带偏",
   ml_budget.fit_cost([{"games": 1000, "total_seconds": -5.0},
                       {"games": -1, "total_seconds": 10.0},
                       {"games": 1000, "total_seconds": float("nan")},
                       {"games": 1000, "total_seconds": "12"}]).rounds, 0)
_d = ml_budget.fit_disk([{"games": 2000, "raw_bytes": 2.6e9, "compact_bytes": 5.6e9}])
eq("预算：磁盘模型也实测回填（1.3 MB/场 raw）", round(_d.raw_bytes_per_game, 1), 1.3e6)
eq("预算：磁盘模型（2.8 MB/场 compact）", round(_d.compact_bytes_per_game, 1), 2.8e6)
eq("预算：磁盘模型没有台账 → 默认值", ml_budget.fit_disk([]).rounds, 0)
eq("预算：磁盘默认值与 v3 谱系最近四代实测一致（raw 1.52 / compact 3.06 MB/场）",
   (round(ml_budget.DEFAULT_RAW_BYTES_PER_GAME / 1e6, 2),
    round(ml_budget.DEFAULT_COMPACT_BYTES_PER_GAME / 1e6, 2)), (1.52, 3.06))
# 计划：时间预算 30 分钟、无闸门 → 用弦反解（1800 s 落在 1000/3000 两点之间）
_p = ml_budget.plan_round(1800.0, _c, ())
eq("预算：30 分钟 → 场次由**分段线性**反解并取整到百", _p.by_time,
   ml_budget._floor100(_c.games_for(1800.0)))
eq("预算：取自弦的场次，预计时长 ≈ 目标（差 ≤ 取整粒度 100 场 × 斜率）",
   abs(_p.seconds - 1800.0) <= 60.0, True)
eq("预算：没有闸门时 `clamped_by` 为空（说明是时间预算自己在定）", _p.clamped_by, None)
ok("预计" in ml_budget.format_plan(_p, _c, 1800.0, generation=7)
   and "第 7 轮" in ml_budget.format_plan(_p, _c, 1800.0, generation=7),
   "预算：计划报告带轮次与预计时长")
# 计划：闸门夹住时必须**指名道姓**，并且实际场次 = 最紧那条
_g = (ml_budget.Gate("compact 配额余量", 1100), ml_budget.Gate("盘余量", 9000))
_p2 = ml_budget.plan_round(1800.0, _c, _g, min_games=400, max_games=24000)
eq("预算：闸门更紧时取闸门值", _p2.games, 1100)
eq("预算：报告写明**是谁**限制了这一轮", _p2.clamped_by, "compact 配额余量")
ok("compact 配额余量" in ml_budget.format_plan(_p2, _c, 1800.0), "预算：计划文本里能读到限制原因")
eq("预算：预计时长 = 模型在**实际场次**上的取值（被夹住时就不是目标时长）", round(_p2.seconds, 3),
   round(_c.predict(_p2.games), 3))
_p3 = ml_budget.plan_round(1800.0, _c, (), min_games=400, max_games=1000)
eq("预算：`--max-games` 也是一条会露脸的闸门", _p3.clamped_by, "--max-games")
eq("预算：`--max-games` 生效时场次 = 它", _p3.games, 1000)
try:
    ml_budget.plan_round(1800.0, _c, (ml_budget.Gate("compact 配额余量", 10),), min_games=400)
    eq("预算：闸门供不起 --min-games 时必须**报错**，不许静默缩小轮次", "没报错", "应报 ResourceError")
except ml_budget.ResourceError as _e:
    ok("compact 配额余量" in str(_e) and "10 场" in str(_e),
       "预算：资源不够的报错里带闸门明细", str(_e)[:70])
# 真·闸门（走 `paths`）：拿 scratch 当数据根，配额故意压小 → 该条闸门必须真的变 0
_broot = scratch("budget")
_saved = (paths.DATA_ROOT, dict(paths.QUOTA_GB), paths.TOTAL_QUOTA_GB)
try:
    paths.DATA_ROOT = _broot
    paths.ensure_root()
    _gates = ml_budget.gates_now(ml_budget.fit_disk([]))
    eq("预算：五道闸门齐全（**缓存档** + raw/compact 配额 + 总配额 + 盘余量）",
       [g.name for g in _gates],
       ["缓存档上限", "raw 配额余量", "compact 配额余量", "总配额余量", "盘余量"])
    ok(all(g.games >= 0 for g in _gates), "预算：闸门场次非负",
       str([(g.name, g.games) for g in _gates]))
    paths.QUOTA_GB["raw"] = 0.001                       # ≈1 MB 配额
    (_broot / "raw" / "x").mkdir(parents=True, exist_ok=True)
    (_broot / "raw" / "x" / "g0.jsonl").write_bytes(b"x" * 4_000_000)   # 已经**超**配额
    _g2 = {g.name: g.games for g in ml_budget.gates_now(ml_budget.fit_disk([]))}
    eq("预算：配额已超 → 该闸门给 0 场（不是负数）", _g2["raw 配额余量"], 0)
    try:
        ml_budget.plan_round(1800.0, ml_budget.fit_cost([]),
                             ml_budget.gates_now(ml_budget.fit_disk([])), min_games=400)
        eq("预算：配额满时 `plan_round` 报错", "没报错", "应报 ResourceError")
    except ml_budget.ResourceError:
        ok(True, "预算：配额满时 `plan_round` 报 ResourceError（要人先清理，不静默降级）")
finally:
    paths.DATA_ROOT, paths.QUOTA_GB, paths.TOTAL_QUOTA_GB = _saved[0], _saved[1], _saved[2]
    shutil.rmtree(_broot, ignore_errors=True)
# 编排侧的接线：`--target-minutes 0` 时**逐字**保持老行为（场次 = `--gen-games`），>0 时才走预算
_ns = argparse.Namespace(target_minutes=0.0, gen_games=2000, min_games=400, max_games=24000, dry_run=False)
eq("预算：`--target-minutes 0` → 场次就是 `--gen-games`（老命令行行为不变）",
   ml_online._plan_games(_ns, ml_budget.fit_cost([]), ml_budget.fit_disk([]), 3), (2000, None))


# ---- 大轮次的**内存**前提：`state`/`cand` 是内存映射，任何"顺手物化一下"都会把整块读进 RAM --------
# 2026-09-27 实测：11M 行 × 615 × 2B = **13.6 GB** 被 `np.asarray(split["state"]).shape[0]` 悄悄读进来
# （可用内存从 18 GB 掉到 1 GB）。判据不是"内存够大"，而是**代码里不许有物化**：
# 用一个"有 `.shape` 但没有 `__array__`"的探针列，物化尝试会立刻炸。
class _NoMaterialize:
    """探针列：`np.shape` 走 `.shape`（不物化），`np.asarray` 会调 `__array__`（炸）。"""

    shape = (1234, 615)

    def __array__(self, *a, **k):                        # noqa: D105
        raise AssertionError("这一列是内存映射，不许物化")


_probe = _NoMaterialize()
eq("大轮次：`np.shape(列)[0]` 走 `.shape`（不物化 13.6 GB 的 state）", int(np.shape(_probe)[0]), 1234)
try:
    np.asarray(_probe)
    eq("大轮次：`np.asarray(列)` 会物化 —— 探针必须能抓到它", "没炸", "应炸")
except AssertionError:
    ok(True, "大轮次：探针能抓到「物化内存映射」的写法（`np.asarray(col).shape[0]`）")
_rsplit = {"state": _probe, "file": np.zeros(4, np.int64), "hand_no": np.zeros(4, np.int64),
           "seat": np.zeros(4, np.int64), "placement": np.ones(4, np.int64),
           "is_student": np.ones(4, np.uint8), "delta": np.zeros((4, 4), np.float64)}
try:
    _rt = rewards.transitions(_rsplit, rank_weight=0.0)   # rank_weight=0 → 不需要 meta 里的 split 名
    ok(int(_rt["idx"].shape[0]) == int(np.shape(_probe)[0]),
       "大轮次：`rewards.transitions` 只读 `state` 的行数、不把它读进 RAM（探针没炸）",
       f"idx={int(_rt['idx'].shape[0])}")
except AssertionError as _e:
    ok(False, "大轮次：`rewards.transitions` 会物化内存映射的 `state` —— 这是 13.6 GB 级别的坑", str(_e))

# ---- **缓存档**闸门（用户口径：控制训练只跑缓存档）-----------------------------------------------
# 为什么是硬闸门：一轮的紧凑集装得进内存 → PPO 命中页缓存（实测 ≈12 分钟/轮）；装不进 → 每遍真读盘
# （41.78 GB × 5 遍 @ ~120 MB/s = 46 分钟）。中间**没有过渡**，所以不能靠"少排一点场次"来微调。
eq("缓存档：比例 = 0.8 × 物理内存（实测 27.2 GB 走通、41.8 GB 落磁盘档 ⇒ 压在已验证那一档）",
   ml_budget.CACHE_FRACTION, 0.8)
_ram = ml_budget.total_ram_gb()
ok(_ram is None or _ram > 1.0, "缓存档：物理内存探测（ctypes GlobalMemoryStatusEx）",
   f"{_ram:.1f} GB" if _ram else "取不到（用兜底 32 GB）")
_saved_ram = ml_budget.total_ram_gb
try:
    ml_budget.total_ram_gb = lambda: 32.0
    eq("缓存档：缺省 = 0.8 × 32 GB = 25.6 GB", round(ml_budget.cache_budget_gb(), 6), 25.6)
    eq("缓存档：显式值优先", ml_budget.cache_budget_gb(24.0), 24.0)
    eq("缓存档：`-1` = 关掉这道闸门（故意跑磁盘档时才用）", ml_budget.cache_budget_gb(-1), -1.0)
    _d2 = ml_budget.fit_disk([{"games": 1000, "raw_bytes": 1e9, "compact_bytes": 2.56e9}])
    _g = {g.name: g.games for g in ml_budget.gates_now(_d2)}       # compact 2.56 MB/场
    eq("缓存档：闸门 = 缓存预算 / 实测 compact 字节每场（25.6 GiB ÷ 2.56 MB = 10700 场）",
       _g["缓存档上限"], 10700)
    _c2 = ml_budget.fit_cost([{"games": 1000, "total_seconds": 100.0}])
    _p4 = ml_budget.plan_round(3600.0, _c2, ml_budget.gates_now(_d2, cache_gb=6.4),
                               min_games=400, max_games=24000)     # 6.4 GiB ÷ 2.56 MB = 2600 场
    eq("缓存档：比时间预算更紧时，场次 = 缓存档上限（**夹住**，不是「少排一点」）", _p4.games, 2600)
    eq("缓存档：报告**点名**是它在限制", _p4.clamped_by, "缓存档上限")
    ok("缓存档上限" in ml_budget.format_plan(_p4, _c2, 3600.0),
       "缓存档：计划文本里能读到这道闸门", ml_budget.format_plan(_p4, _c2, 3600.0).splitlines()[3][:60])
    _g_off = {g.name for g in ml_budget.gates_now(_d2, cache_gb=-1)}
    ok("缓存档上限" not in _g_off, "缓存档：`--cache-gb -1` 时这道闸门**不在**闸门表里", str(sorted(_g_off)))
finally:
    ml_budget.total_ram_gb = _saved_ram

# ---- 跨代际筛选（`online screen`）：按**平均得点**排序 + 口径交叉核对 -------------------------------
# 用户口径（2026-09-27）："联合 teacher 与 g05 g06 g07 做小批量跨代际价值检验，坐位平均随机化，
# 取平均得点最高者作为进一步训练的候选人"。`--rotate` 给的是**严格座位平均**（每策略每座位各 1/4 场次）。
ok("score" in ml_eval.METRICS, "筛选：`eval` 支持 `score` 指标（`final_scores − 起点`）", str(ml_eval.METRICS))
_row = {"policies": ["a", "b", "a", "b"], "final_scores": [30000, 20000, 27000, 23000],
        "placement": [1, 4, 2, 3], "rank_points": [50.0, -40.0, 20.0, -30.0]}
eq("筛选：`score` = `final_scores − 25000`（同标签多座位取平均）",
   ml_eval.seat_values(_row, "a", "score"), [5000.0, 2000.0])
eq("筛选：`rank_points` 口径不变", ml_eval.seat_values(_row, "b", "rank_points"), [-40.0, -30.0])
_sruns = []
for _k, _hpg in ((0, 11.0), (1, 12.0)):
    _sruns.append(type("R", (), {
        "games": 2, "hands": 2 * _hpg,
        "by_policy": {
            "a": {"games": 2, "avg_delta": 100.0, "avg_rank_points": 3.0, "win_rate": 0.25,
                  "deal_in_rate": 0.1, "avg_win_score": 7000.0},
            "b": {"games": 2, "avg_delta": -100.0, "avg_rank_points": -3.0, "win_rate": 0.2,
                  "deal_in_rate": 0.2, "avg_win_score": 6000.0},
        },
        "per_game": [
            {"seed": 1000 * _k + 1, "policies": ["a", "b"],
             "final_scores": [25000 + 100 * _hpg, 25000 - 100 * _hpg]},
            {"seed": 1000 * _k + 2, "policies": ["b", "a"],
             "final_scores": [25000 - 100 * _hpg, 25000 + 100 * _hpg]},
        ],
    })())
_st = ml_online.screen_table(_sruns, {"a": "A代", "b": "B代"})
eq("筛选：按**平均得点**降序（A 代 +1150 千点 > B 代 −1150）",
   [r["name"] for r in _st], ["A代", "B代"])
eq("筛选：引擎账（每小局）× 每场小局数 == 逐场得点（交叉核对）",
   round(_st[0]["score"], 1), round(100.0 * 11.5, 1))
eq("筛选：顺位点列读的是 `avg_rank_points`（**不是** `rank_points` —— 写错会静默变 0）",
   (_st[0]["rank_points"], _st[1]["rank_points"]), (3.0, -3.0))
_bad = [type("R", (), {"games": 1, "hands": 11.0,
                       "by_policy": {"a": {"games": 1, "avg_delta": 100.0}},
                       "per_game": [{"seed": 7, "policies": ["a"], "final_scores": [99999]}]})()]
try:
    ml_online.screen_table(_bad, {"a": "A"})
    eq("筛选：两份账差 >5% 必须报错（不悄悄挑一个用）", "没报错", "应 SystemExit")
except SystemExit:
    ok(True, "筛选：引擎账与逐场得点差 >5% 时报错（口径变了要被抓住）")

# ---------------------------------------------------------------- 特征 v4（训练框架）
# 规范 `docs/FEATURES-V4.md` · 设计 `docs/TRAINING-V4.md`。
# 这一组盯的是"框架自己有没有说谎"：注册表与张量铺满、缺字段必须报错、消融必须真归零、
# 增量必须等于全量、封闭红证、多头形状、场外均衡审计（含负向对照）。
from mahjong_ml import v4 as ml_v4                              # noqa: E402
from mahjong_ml.v4 import blocks as v4_blocks, cache as v4_cache  # noqa: E402
from mahjong_ml.v4 import harness as v4_harness, model as v4_model, spec as v4_spec  # noqa: E402

_v4obs = dict(ml_v4.cli.SAMPLE_OBS)                             # v3 样例（带事件流）

ok(all(sum(b.width for b in v4_spec.BLOCKS if b.tensor == t) == w
       for t, w in (("tile", v4_spec.C_TILE), ("evt", v4_spec.C_EVT),
                    ("ctx", v4_spec.C_CTX), ("cand", v4_spec.C_CAND))),
   "v4：块宽度**铺满**四个张量（少一块=有通道没人负责）")
eq("v4：块清单指纹稳定（写进 net.bin 格式 2，加载时逐块核对）",
   v4_spec.fingerprint(), v4_spec.fingerprint())
eq("v4：指纹对块清单敏感（改宽度必须换指纹）",
   len(v4_spec.fingerprint([v4_spec.block("tile.own")])), 16)
eq("v4：obs 版本契约（v4 要求 v3）", v4_spec.OBS_VERSION_V4, 3)
eq("v4：张量布局版本（与 v3 不同）", v4_spec.FEATURE_VERSION_V4, 4)

_t = v4_blocks.assemble(_v4obs, None, allow_degraded=False)
eq("v4：tile 形状", tuple(_t.tile.shape), (34, v4_spec.C_TILE))
eq("v4：evt 形状（K=60 窗口）", tuple(_t.evt.shape), (v4_spec.K_EVT, v4_spec.C_EVT))
eq("v4：ctx 形状", tuple(_t.ctx.shape), (v4_spec.C_CTX,))
eq("v4：cand 形状（与 legal 同序）", tuple(_t.cand.shape),
   (len(_v4obs["legal"]), v4_spec.C_CAND))
ok(all(np.isfinite(x).all() for x in _t.as_dict().values()), "v4：四个张量全为有限数")
ok(not ({"evt.stream", "ctx.seats", "tile.per_opp"} & _t.degraded),
   "v4：obs v3 下 **obs 侧**的块都不降级（事件流/立直巡数/逐张属性齐全）")
ok({"tile.danger", "tile.safety", "cand.derived"} <= _t.degraded,
   "v4：缺 sidecar v3 时 **sidecar 侧**的块逐块记名降级（不猜、不填假值）")

# 缺字段 / 版本不符 / 未知块 id —— **必须报错，不许填 0**
for _name, _mut, _kw in (("缺 events", {k: v for k, v in _v4obs.items() if k != "events"}, {}),
                         ("obs v2 未降级", dict(_v4obs, v=2), {}),
                         ("未知块 id", _v4obs, {"ablate": ["nope.block"]})):
    try:
        v4_blocks.assemble(_mut, None, **_kw)
        ok(False, f"v4：{_name} 应当报错（填 0 = 悄悄少给信息）")
    except v4_spec.ContractError:
        ok(True, f"v4：{_name} 报错 ✓")

_td = v4_blocks.assemble(dict(_v4obs, v=2), None, allow_degraded=True)
ok({"evt.stream", "ctx.seats", "tile.per_opp"} <= _td.degraded,
   f"v4：obs v2 降级路径**逐块记名**（{sorted(_td.degraded)}）")
ok("evt.stream" not in _t.degraded, "v4：v3 数据不会被误标降级")

# ---- 数据集层（设计 §「P0 剩余工作」#6）：obs/sidecar 版本**硬拒绝** + sidecar v3 真的落位
from mahjong_ml.v4 import traces as v4_traces                  # noqa: E402
try:
    v4_traces.gate_obs(dict(_v4obs, v=2), where="<自检>")
    ok(False, "v4 数据集层：obs v2 必须报错（降级跑 = 悄悄换任务）")
except v4_spec.ContractError:
    ok(True, "v4 数据集层：obs v2 被硬拒绝 ✓")
eq("v4 数据集层：obs v3 放行", v4_traces.gate_obs(_v4obs, where="<自检>"), v4_spec.OBS_VERSION_V4)
_bm = np.zeros((1, 5), dtype=np.uint8)
_bm[0, 0] = 0b00001000
_bm[0, 4] = 0b00000010
_bits = v4_traces.unpack_bitmap(_bm)[0]
ok(int(_bits[3]) == 1 and int(_bits[33]) == 1 and int(_bits.sum()) == 2,
   "v4 数据集层：位图展开按**字节内 LSB 在前**（第 3 位 / 第 33 位）")
# sidecar v3 的逐家段真的喂进 tile.danger / tile.safety（含"数组不是列表"这条真 bug 的回归：
# `sc.get(key) or []` 在 numpy 数组上会抛 ambiguous —— 见 NOTES §6.5）
_scd = {"n": 1, "ver": v4_spec.DERIVED_VERSION_V4,
        "danger_per_seat": np.arange(1, 103, dtype=np.int8).reshape(1, 3, 34),
        "danger_riichi_per_seat": np.arange(1, 103, dtype=np.int8).reshape(1, 3, 34),
        "genbutsu_per_seat": np.zeros((1, 3, 5), dtype=np.uint8),
        "suji_per_seat": np.zeros((1, 3, 5), dtype=np.uint8),
        # ⚠ 逐候选段（C 段）**必须**在这张表里：2026-09-27 就是因为它缺了，
        #   `cand[88:128]` 整整 40 列在 v4-bc-001/002 里**全 0**（训练照跑、指标照出）
        "nlegal": np.array([2], dtype=np.int16),
        "offsets": np.array([0, 2], dtype=np.int64),
        "cand": np.arange(1, 17, dtype=np.int16).reshape(2, 8)}
_scd["genbutsu_per_seat"][0, :, 0] = 0b00000001                # 0 号牌种是三家现物
_side = v4_traces.sidecar_dict(_scd, 0)
eq("v4 数据集层：sidecar_dict 带上 derived_version", _side["derived_version"],
   v4_spec.DERIVED_VERSION_V4)
eq("v4 数据集层：位图展开成 34 宽", tuple(np.asarray(_side["genbutsu_per_seat"]).shape), (3, 34))
eq("v4 数据集层：sidecar_dict **带逐候选段**（少了它 cand[88:128] 会静默全 0）",
   tuple(np.asarray(_side["cand"]).shape), (2, 8))
ok(bool((np.asarray(_side["cand"])[1] == _scd["cand"][1]).all()),
   "v4 数据集层：逐候选段按 offsets 切片（第二条 == 文件里的第二条）")
_scd_bad = dict(_scd, nlegal=np.array([3], dtype=np.int16))     # 逐候选行数与 nLegal 不符
try:
    v4_traces.sidecar_dict(_scd_bad, 0)
    ok(False, "v4 数据集层：逐候选段与 nLegal 错位必须报错")
except v4_spec.ContractError as _e:
    ok("nLegal" in str(_e), "v4 数据集层：逐候选段与 nLegal 错位被硬拒 ✓", str(_e)[:60])
_td2 = v4_blocks.assemble(_v4obs, _side, allow_degraded=False)
ok(not ({"tile.danger", "tile.safety"} & _td2.degraded),
   "v4 数据集层：有 sidecar v3 时 tile.danger / tile.safety 不降级（正向对照）")
_s0, _w0 = v4_blocks.TILE_OFF["safety_genbutsu"]
ok(bool((_td2.tile[0, _s0:_s0 + _w0] == 1.0).all()) and bool((_td2.tile[7, _s0:_s0 + _w0] == 0).all()),
   "v4 数据集层：现物位图按牌种落到 safety_genbutsu 三个对手通道")
# 逐候选派生段真的进 `cand[88:96]`（v4-bc-001/002 全 0 的那个坑的正面判据）
_cb = int(np.asarray(_side["cand"]).shape[0])
ok(_cb > 0 and bool((_td2.cand[:_cb, 88:96] != 0).any()),
   "v4 数据集层：逐候选派生段进了 cand[88:96]（不是全 0）",
   f"max={float(np.abs(_td2.cand[:_cb, 88:96]).max()):.3f}")
# 降级闸门：sidecar 不给 cand ⇒ `Tensors.degraded` 里必须有 `cand.derived`（dataset 靠它硬拒）
_side_nocand = dict(_side)
_side_nocand.pop("cand")
_td3 = v4_blocks.assemble(_v4obs, _side_nocand, allow_degraded=False)
ok("cand.derived" in _td3.degraded,
   "v4 数据集层：sidecar 缺逐候选段 ⇒ 记为降级（`Tensors.degraded`，dataset 据此硬拒）")

# 文件级：`iter_decisions` 逐行过闸门（一条 v3 + 一条 v2 ⇒ 读到第二条必须抛）
_trdir = scratch("v4-traces")
with (_trdir / "g0.jsonl").open("w", encoding="utf-8") as _fh:
    _fh.write(json.dumps({"type": "decision", "obs": _v4obs}) + "\n")
    _fh.write(json.dumps({"type": "decision", "obs": dict(_v4obs, v=2)}) + "\n")
try:
    list(v4_traces.iter_decisions(_trdir))
    ok(False, "v4 数据集层：轨迹里混进 obs v2 必须报错")
except v4_spec.ContractError as _e:
    ok("obs v2" in str(_e), "v4 数据集层：iter_decisions 在 v2 那一行报错（并指出是哪一行）",
       str(_e)[:70])

# ---- v4 数据集 + 教师预训练（2026-09-27）：形状/标签/可复现 --------------------------------
# 合成一条最小轨迹（复用伪造 obs + 上面的标签侧 writer），走**真实**的 Java 产物格式
from mahjong_ml.v4 import dataset as v4_ds, pretrain as v4_pt, model as v4_model  # noqa: E402

_v4src = scratch("v4-bc-src")
_v4obs_rows = []
for _g in range(2):
    _jl = _v4src / f"g{_g}.jsonl"
    _rows = []
    for _i in range(3):
        # ⚠ legal 取 **2 条**：与下面 `write_sidecar` 的逐候选段（`CAND0` 两条）逐条对齐 ——
        #   `v4.dataset` 会校验 `sidecar.nLegal == len(legal)`（错位等于把别人的牌效目标训到这一行），
        #   也会校验 `obs.legal == 行上的 legal`（blocks 按 obs.legal 展开候选）
        _legal = list(_v4obs["legal"])[:2]
        _obs = dict(_v4obs, v=3, seat=_i % 4, total_discards=20 + _i, legal=_legal)
        _row = {"type": "decision", "game": _g, "hand_no": 1, "step": _i, "seat": _i % 4,
                "policy": "teacher", "kind": "turn", "legal": _legal,
                "chosen": _legal[_i % 2], "chosen_index": _i % 2,
                "hand_delta": [100, -100, 0, 0], "placement": [1, 2, 3, 4],
                "final_scores": [26000, 25000, 24000, 25000], "obs": _obs}
        _rows.append(_row)
        _jl_rows = _row
    with _jl.open("w", encoding="utf-8") as _fh:
        for _r in _rows:
            _fh.write(json.dumps(_r, ensure_ascii=False) + "\n")
        # `game` 行（起点分）—— `v4.dataset` 要从它读 start_score
        _fh.write(json.dumps({"type": "game", "game": _g, "seed": 1, "policies": ["teacher"] * 4,
                              "start_score": 25000, "final_scores": [26000, 25000, 24000, 25000],
                              "placement": [1, 2, 3, 4]}) + "\n")
    # sidecar（逐决策派生量）：`blocks.assemble` 要它的 4 个逐家段 + 逐候选段
    write_sidecar(_jl, [dict(PER_SEAT_ROW, danger=DANGER, cand=CAND0) for _ in range(3)])
    write_aux(_jl, 3)
_v4meta = v4_ds.build(_v4src, _v4src / "ds", val_frac=0.5, split_seed=0, aux=True, quiet=True)
eq("v4 数据集：训练条数", _v4meta["train_decisions"], 3)
eq("v4 数据集：张量形状写进 meta", _v4meta["shapes"]["tile"], [34, v4_spec.C_TILE])
eq("v4 数据集：obs/derived 版本", (_v4meta["obs_version"], _v4meta["derived_version"]),
   (v4_spec.OBS_VERSION_V4, v4_spec.DERIVED_VERSION_V4))
_v4tr = v4_ds.load_split(_v4src / "ds", "train")
eq("v4 数据集：tile 列形状", tuple(_v4tr["tile"].shape),
   (3, 34, v4_spec.C_TILE))
eq("v4 数据集：evt 列形状", tuple(_v4tr["evt"].shape), (3, v4_spec.K_EVT, v4_spec.C_EVT))
ok(_v4tr["cand"].shape[1] == _v4meta["lmax"], "v4 数据集：cand 宽度 = lmax")
ok(all(_v4tr[k] is not None for k in ("label", "label_type", "effect", "value", "placement",
                                      "aux_opp_hand", "aux_opp_tenpai")),
   "v4 数据集：标签列齐（教师动作 + 值 + 顺位 + aux 真值）")
# ① 缺 aux 却要 --aux ⇒ 报错（不填 0）
try:
    _noaux = scratch("v4-bc-noaux")
    shutil.copy(_v4src / "g0.jsonl", _noaux / "g0.jsonl")
    shutil.copy(ds.sidecar_path(_v4src / "g0.jsonl"), ds.sidecar_path(_noaux / "g0.jsonl"))
    v4_ds.build(_noaux, _noaux / "ds", val_frac=0.5, aux=True, quiet=True)
    ok(False, "v4 数据集：aux=True 但缺标签必须报错")
except FileNotFoundError as _e:
    ok("--aux" in str(_e), "v4 数据集：缺标签侧时报错", str(_e)[:60])
# ② obs v2 的轨迹 ⇒ 报错（数据集层的硬闸门，不是只在校验器里）
_badobs = scratch("v4-bc-badobs")
with (_badobs / "g0.jsonl").open("w", encoding="utf-8") as _fh:
    _fh.write(json.dumps({"type": "decision", "obs": dict(_v4obs, v=2)}) + "\n")
# sidecar 要给（否则先炸在"缺 sidecar"上，测不到 obs 版本这条闸门）
write_sidecar(_badobs / "g0.jsonl", [dict(PER_SEAT_ROW, danger=DANGER, cand=CAND0)])
try:
    v4_ds.build(_badobs, _badobs / "ds", val_frac=0.5, aux=False, quiet=True)
    ok(False, "v4 数据集：obs v2 轨迹必须报错")
except v4_spec.ContractError:
    ok(True, "v4 数据集：obs v2 轨迹被硬拒绝 ✓")
# ③ 教师预训练：**同种子两次跑出同一份 metrics**（可复现是硬要求）
_run = scratch("v4-bc-run")
_ptbase = dict(data=str(_v4src / "ds"), epochs=1, batch=2, eval_batch=2, lr=1e-3, max_steps=1,
               seed=7, threads=1, device="cpu", stage_a=0.25, stage_b=0.35, head_lr_mult=3.0,
               mask_frac=0.0, ssl_weight=0.2, stage_c_lr_mult=1.0, rwr_beta=0.0)
_a = v4_pt.train(argparse.Namespace(**{**_ptbase, "label": "v4-selfcheck-a"}))
_b = v4_pt.train(argparse.Namespace(**{**_ptbase, "label": "v4-selfcheck-b"}))
eq("v4 预训练：同种子两次的 history 逐字段相同",
   json.dumps(_a["history"], sort_keys=True), json.dumps(_b["history"], sort_keys=True))
ok(_a["history"][0]["val_top1"] >= 0.0 and "val_loss_policy" in _a["history"][0]
   and "val_loss_danger" in _a["history"][0],
   "v4 预训练：指标含教师一致率与逐头损失")
ok(_a["params"] <= 3_000_000, f"v4 预训练：参数量 {_a['params']:,} ≤ 3M")
# ④ 分阶段 + 掩码自监督（P1）：阶段划分、冻主干、SSL 头、同种子可复现
_ptargs = dict(data=str(_v4src / "ds"), epochs=2, batch=2, eval_batch=2,
               lr=1e-3, max_steps=2, seed=11, threads=1, device="cpu", stage_a=0.25, stage_b=0.5,
               head_lr_mult=3.0, mask_frac=0.5, ssl_weight=0.2)
_s1 = v4_pt.train(argparse.Namespace(**{**_ptargs, "label": "v4-selfcheck-s"}))
_s2 = v4_pt.train(argparse.Namespace(**{**_ptargs, "label": "v4-selfcheck-t"}))
eq("v4 预训练（分阶段+SSL）：同种子两次 history 逐字段相同",
   json.dumps(_s1["history"], sort_keys=True), json.dumps(_s2["history"], sort_keys=True))
eq("v4 预训练（分阶段）：三个阶段的步数都 > 0",
   sorted(_s1["stage_steps"]), ["a", "b", "c"])
ok("val_ssl_acc" in _s1["history"][0] and 0.0 <= _s1["history"][0]["val_ssl_acc"] <= 1.0,
   "v4 预训练（P1）：val 里带掩码事件重建的准确率")
_bm_evt = torch.zeros(1, v4_spec.K_EVT, v4_spec.C_EVT)
_bm_evt[0, 3, 0] = 1.0
_bm_evt[0, 4, 1] = 1.0
_masked, _mrow, _mtgt = v4_pt.mask_events(_bm_evt, 1.0, torch.Generator().manual_seed(0))
ok(bool(_mrow[0, 3]) and bool(_mrow[0, 4]) and not bool(_mrow[0, 0]),
   "v4 预训练（P1）：掩码只落在**真实事件**上（padding 不动）")
ok(float(_masked[0, 3].abs().sum()) == 0.0 and float(_bm_evt[0, 3].abs().sum()) > 0.0,
   "v4 预训练（P1）：被掩码的 token 真的置 0，且不改原张量")
eq("v4 预训练（P1）：掩码目标 = 原事件的类型下标", int(_mtgt[0, 4]), 1)
# ⑤ 模型多返回 `e_tokens`（pretext 用）—— 推理头清单里**不许**出现它
ok("e_tokens" not in v4_model.inference_heads(), "v4：e_tokens 不进推理头清单")
_ssl = v4_pt.MaskedEventHead()
ok(sum(p.numel() for p in _ssl.parameters()) == 192 * len(v4_spec.EVT_TYPES) + len(v4_spec.EVT_TYPES),
   "v4 预训练：SSL 头只做事件类型分类（D_MODEL → 类型数）")
# ⑥ v4 数据集并行构建 == 串行（逐字节）
_par_src = scratch("v4-bc-par")
for _g in range(4):
    shutil.copy(_v4src / f"g{_g % 2}.jsonl", _par_src / f"g{_g}.jsonl")
    shutil.copy(ds.sidecar_path(_v4src / f"g{_g % 2}.jsonl"),
                ds.sidecar_path(_par_src / f"g{_g}.jsonl"))
    shutil.copy(aux_mod.aux_path(_v4src / f"g{_g % 2}.jsonl"),
                aux_mod.aux_path(_par_src / f"g{_g}.jsonl"))
v4_ds.build(_par_src, _par_src / "ser", val_frac=0.5, split_seed=0, aux=True, workers=1, quiet=True)
v4_ds.build(_par_src, _par_src / "par", val_frac=0.5, split_seed=0, aux=True, workers=3, quiet=True)
_same_cols = []
for _p in sorted((_par_src / "ser").glob("*.npy")):
    _q = _par_src / "par" / _p.name
    _same_cols.append(_q.is_file() and _p.read_bytes() == _q.read_bytes())
ok(all(_same_cols) and len(_same_cols) >= 19,
   f"v4 数据集：并行（3 进程）与串行**逐字节相同**（{len(_same_cols)} 列）")

# 消融：关掉的块必须整块归零，且其它块不受影响
_ts = v4_blocks.assemble(_v4obs, None, ablate=["tile.safety"], allow_degraded=False)
_s, _w = v4_blocks.TILE_OFF["safety_genbutsu"]
ok(bool((_ts.tile[:, _s:_s + _w] == 0).all()), "v4：消融 tile.safety → 该块归零")
ok(bool((_ts.tile[:, :_s] != 0).any()), "v4：消融只动那一块（其它通道还在）")
_ta = v4_blocks.assemble(_v4obs, None, ablate=["evt.stream"], allow_degraded=False)
ok(bool((_ta.evt == 0).all()), "v4：消融 evt.stream → 事件流整块归零")

# 判据④：增量 == 全量（张量级 + 表示级）
_inc = v4_harness.incremental_proof(_v4obs)
ok(_inc["tensor_ok"], f"v4：牌河增量 == 全量（Δ={_inc['tensor_delta']:.1e}）")
ok(_inc["repr_ok"], f"v4：GRU 增量 == 从头重放（Δ={_inc['repr_delta']:.1e}）")
_rs = v4_cache.RiverState.full_recompute(_v4obs)
_eq_ok, _eq_d = v4_cache.incremental_equals_full(_v4obs)
ok(_eq_ok and _eq_d == 0.0, "v4：同一条事件流逐条喂 == 一次性重算（逐元素相等）")
_rs2 = v4_cache.RiverState(seat=1)
for _e in _v4obs["events"]:
    _rs2.apply(_e)
ok(float(np.abs(_rs2.channels() - _rs.channels()).max()) == 0.0,
   "v4：RiverState 增量与基准路径**逐元素相同**")
_eq_ok2, _ = v4_cache.incremental_equals_full(dict(_v4obs, v=2))
ok(_eq_ok2, "v4：obs v2（无事件流）下增量路径也不炸")

# ⚠ 杠是**自己的** `type`（不是 `meld` 的写法）：增量路径必须像全量路径那样把它的 4 张
#   算进 `meld_count`，否则"有杠的局面"上判据④会静默失效（红证：只认 `meld` 时这里是 0）。
_kan_ev = {"type": "kan", "actor": 1, "meld_kind": "ankan", "tile": "7z",
           "tiles": ["7z", "7z", "7z", "7z"]}
_st_kan = v4_cache.RiverState(seat=0)
_st_kan.apply(_kan_ev)
eq("v4：`kan` 事件也要累加 meld_count（增量路径不许漏杠）",
   float(_st_kan.channels()[:, 15].sum()), 4.0)
_st_pon = v4_cache.RiverState(seat=0)
_st_pon.apply({"type": "meld", "actor": 1, "meld_kind": "pon", "tile": "2z",
               "tiles": ["2z", "2z", "2z"]})
eq("v4：`meld`（碰）事件累加 meld_count", float(_st_pon.channels()[:, 15].sum()), 3.0)
_st_irr = v4_cache.RiverState(seat=0)
ok(not _st_irr.apply({"type": "dora_flip", "tile": "1z"})
   and float(np.abs(_st_irr.channels()).max()) == 0.0,
   "v4：无关事件（dora_flip）不改张量且返回 False（增量缓存可以整条跳过）")

# 封闭红证（规范 §1.4）
_pr = v4_harness.closure_proofs(_v4obs)
ok(_pr["aux_logits_identical"], "v4 红证①：挂上隐藏真值标签后 logits 逐位不变（标签不进输入）")
ok(_pr["reward_purity"], f"v4 红证③：奖励路径不含过程隐藏量 {_pr['reward_violations'] or ''}")
_neg_ok, _neg_bad = v4_harness.reward_purity_scan(text="adv += 0.5 * opp_tenpai[s]\n", name="<对照>")
ok(not _neg_ok and _neg_bad, "v4 红证③负向对照：注入 opp_tenpai 必须被抓出来")
ok(v4_harness.reward_purity_scan(text="wall = time.perf_counter()\n", name="<对照>")[0],
   "v4 红证③不误伤：墙钟计时 `wall` 不算隐藏量")
ok(_pr["aux_separation"], f"v4 判据⑥：推理路径不碰 aux 标签 {_pr['aux_violations'] or ''}")

# 模型与多头（设计 §5/§6）
_v4m = v4_model.build(seed=5)
ok(_v4m.param_count() <= 3_000_000, f"v4：参数量 {_v4m.param_count():,} ≤ 3M（上限来自 §0.1.3）")
eq("v4：上线必需头（策略 + 分布价值 + 对手听牌 + 危险）",
   v4_model.inference_heads(), ("policy", "value", "belief_tenpai", "danger"))
eq("v4：头清单权重（策略头 = 1.0，teacher 模仿头**不在**这里）",
   v4_model.loss_weights()["policy"], 1.0)
ok("teacher" not in "".join(v4_model.loss_weights()), "v4：多头里没有 teacher 通道（teacher 只是起点/对手/基准）")
import torch as _torch                                          # noqa: E402
_x = {k: _torch.tensor(v, dtype=_torch.float32).unsqueeze(0) for k, v in _t.as_dict().items()}
with _torch.no_grad():
    _out = _v4m(**_x, mask=_torch.ones(1, _t.cand.shape[0], dtype=_torch.bool))
eq("v4：策略 logits 形状", tuple(_out["policy"].shape), (1, _t.cand.shape[0]))
eq("v4：分布价值分箱数", tuple(_out["value"].shape), (1, v4_model.VALUE_BINS))
eq("v4：对手手牌信念形状", tuple(_out["belief_hand"].shape), (1, 3, 34))
eq("v4：危险头形状（候选 × 4 家）", tuple(_out["danger"].shape), (1, _t.cand.shape[0], 4))
_hg = v4_model.hl_gauss_targets(_torch.tensor([0.0, 5.0, -30.0, 100.0]))
ok(bool((_hg.sum(dim=1) - 1.0).abs().max() < 1e-5), "v4：值头的软标签每行和为 1（HL-Gauss 正确）")
ok(bool((_hg >= 0).all()), "v4：值头软标签非负（不会造出负概率）")
ok(_torch.equal(_v4m(**_x, mask=None)["policy"][:, 0], _v4m(**_x, mask=None)["policy"][:, 0]),
   "v4：前向是确定性的（无 dropout）—— 红证①与判据④都依赖这条")

# ---- P3 开局：学生掩码（`--student`）+ 回报列（`delta`）+ RWR 权重（2026-09-27）------------
_std = "net:tools/build/x/net.bin#1.0"
_sp_src = scratch("v4-sp-src")
for _g in range(2):
    _jl = _sp_src / f"g{_g}.jsonl"
    _sp_rows = []
    for _i in range(4):
        _legal = list(_v4obs["legal"])[:2]
        _obs = dict(_v4obs, v=3, seat=_i % 4, legal=_legal)
        # 一半座位是"学生"、一半是 teacher —— 掩码的判据就是这 1:1
        _sp_rows.append({"type": "decision", "game": _g, "hand_no": 1, "step": _i, "seat": _i % 4,
                         "policy": _std if _i % 2 == 0 else "teacher", "kind": "turn",
                         "legal": _legal, "chosen": _legal[_i % 2], "chosen_index": _i % 2,
                         "hand_delta": [1000 * (_i - 2), -1000 * (_i - 2), 0, 0],
                         "placement": [1, 2, 3, 4],
                         "final_scores": [26000, 25000, 24000, 25000], "obs": _obs})
    with _jl.open("w", encoding="utf-8") as _fh:
        for _r in _sp_rows:
            _fh.write(json.dumps(_r, ensure_ascii=False) + "\n")
        _fh.write(json.dumps({"type": "game", "game": _g, "seed": 1, "policies": ["teacher"] * 4,
                              "start_score": 25000, "final_scores": [26000, 25000, 24000, 25000],
                              "placement": [1, 2, 3, 4]}) + "\n")
    write_sidecar(_jl, [dict(PER_SEAT_ROW, danger=DANGER, cand=CAND0) for _ in range(4)])
_v4sp = v4_ds.build(_sp_src, _sp_src / "ds", val_frac=0.5, split_seed=0, aux=False, quiet=True,
                    student=_std)
_sp = v4_ds.load_split(_sp_src / "ds", "train")
ok(_sp["is_student"] is not None and _sp["delta"] is not None,
   "P3 数据集：`--student` 写出 `is_student`，且 `delta`（回报）列在")
_lab = _sp["meta"].get("student")
eq("P3 数据集：meta 记下学生策略串", _lab, _std)
_is = np.asarray(_sp["is_student"])
ok(0 < int(_is.sum()) < _is.size, "P3 数据集：学生掩码不全是 1 也不全是 0（真遮罩）",
   f"学生 {int(_is.sum())}/{_is.size}")
_dl = np.asarray(_sp["delta"])
ok(int(np.abs(_dl).max()) > 0, "P3 数据集：`delta` 读了轨迹的 `hand_delta`（非全 0）",
   f"max|delta|={int(np.abs(_dl).max())}")
# 不给 `--student` ⇒ 全部算学生（老行为不变）
_v4all = v4_ds.build(_sp_src, _sp_src / "ds2", val_frac=0.5, split_seed=0, aux=False, quiet=True)
eq("P3 数据集：不给 `--student` 时全部算学生（老行为）",
   int(np.asarray(v4_ds.load_split(_sp_src / "ds2", "train")["is_student"]).sum()),
   int(v4_ds.load_split(_sp_src / "ds2", "train")["is_student"].size))
# RWR 权重：与 `delta` 单调、上下夹在 exp(±2)
_k, _w = v4_pt._row_weights({"is_student": _is, "delta": _dl}, np.arange(_is.size), 8.0, "cpu")
ok(_k is not None and _w is not None, "P3 训练：`_row_weights` 同时给出掩码与权重")
_wv = _w.numpy()
ok(bool((_wv >= np.exp(-2.0) - 1e-6).all() and (_wv <= np.exp(2.0) + 1e-6).all()),
   "P3 训练：RWR 权重夹在 exp(±2) 内（β=8 千点）",
   f"min={_wv.min():.3f} max={_wv.max():.3f}")
_ord = np.argsort(_dl)
ok(bool((np.diff(_wv[_ord]) >= -1e-6).all()), "P3 训练：RWR 权重随本小局收支单调不降")
eq("P3 训练：β<=0 时不加权（权重为 None ⇒ 损失里就是 mean）", v4_pt._row_weights(
    {"is_student": _is, "delta": _dl}, np.arange(_is.size), 0.0, "cpu")[1], None)
# 负向：`--student` 与轨迹里的 `policy` 对不上 ⇒ 学生行全 0 ⇒ `train()` 必须**报错退出**
_v4zero = v4_ds.build(_sp_src, _sp_src / "ds0", val_frac=0.5, split_seed=0, aux=False, quiet=True,
                      student="net:这个串和轨迹里的对不上")
eq("P3 数据集：对不上的 `--student` ⇒ `is_student` 全 0", int(np.asarray(
    v4_ds.load_split(_sp_src / "ds0", "train")["is_student"]).sum()), 0)
try:
    v4_pt.train(argparse.Namespace(**{**_ptbase, "data": str(_sp_src / "ds0"),
                                      "label": "v4-selfcheck-stu0"}))
    ok(False, "P3 训练：学生行占比 0 必须报错（否则就是在训对手/老师的动作）")
except SystemExit as _e:
    ok("学生行占比 0" in str(_e), "P3 训练：学生行占比 0 被硬拒 ✓", str(_e)[:70])

# 场外均衡审计（设计 §7.5）—— 含负向对照
_bdir = scratch("v4-balance")
_pols = ["pa", "pb", "pc", "pd"]


def _write_run(d, *, skew: bool, real_shape: bool = True) -> None:
    """写一轮假的采集轨迹（**一场一个文件**，与真轨迹同构）。

    ⚠ 两条教训（2026-09-27）：
    ① 决策行要带 `"type":"decision"`（真轨迹的形状）—— 老形状会让审计在真数据上一条决策都读不到；
    ② **一场 = 一个文件**：座位→策略的映射每场固定、跨场轮转。把 8 场塞进一个文件时，
       审计只能看到"最后一场"的配席 ⇒ 座位偏差算出 75% 的假红（夹具形状不对，判据就不可信）。
    """
    for game in range(8):
        with (d / f"g{game}.jsonl").open("w", encoding="utf-8") as fh:
            for seat in range(4):
                pol = _pols[0] if (skew and seat >= 2) else _pols[(seat + game) % 4]
                row = {"game": game, "seat": seat, "policy": pol, "legal": ["discard:1m"]}
                if real_shape:
                    row["type"] = "decision"
                fh.write(json.dumps(row) + "\n")
            fh.write(json.dumps({"type": "hand", "game": game, "hand_no": 0,
                                 "round": {"bakaze": "E", "kyoku": 1},
                                 "agari": True, "tsumo": True, "abortive": False}) + "\n")


_write_run(_bdir, skew=False)
_rep = v4_harness.balance_report(_bdir)
_ok_bal, _why = _rep.verdict()
ok(_ok_bal, f"v4 均衡：逐座位轮转的一轮判为均衡（座位偏差 {_rep.seat_deviation():.2%}，CV {_rep.opponent_cv():.1%}）")
eq("v4 均衡：小局计数（8 局）", _rep.rounds, 8)
eq("v4 均衡：决策行按**真形状**（`type=decision`）读到了", _rep.decisions, 32)
ok(bool(_rep.policy_seat), "v4 均衡：配席统计非空（读到 (策略, 座位) 了）")
eq("v4 均衡：结局分类（自摸）", _rep.outcomes.get("agari_tsumo"), 8)
_rep_json = _rep.to_json()
ok({"seat_deviation", "opponent_cv", "balanced", "reasons", "rounds", "expect"} <= set(_rep_json),
   "v4 均衡：报告字段齐全（写 league/<label>/balance.json）")

# 负向对照 ①：决策行形状不认识（老形状也认，但**空目录**必须判不均衡而不是假绿）
_bempty = scratch("v4-balance-empty")
(_bempty / "g0.jsonl").write_text(json.dumps({"type": "hand", "game": 0, "hand_no": 0,
                                              "round": {"bakaze": "E", "kyoku": 1}}) + "\n",
                                  encoding="utf-8")
_rep_e = v4_harness.balance_report(_bempty)
_ok_e, _why_e = _rep_e.verdict()
ok(not _ok_e and any("决策行" in w for w in _why_e),
   f"v4 均衡负向对照：只有小局行、没有决策行 ⇒ 判不均衡（{_why_e}）")

# 负向对照 ②：`--expect` 声明了配席但实际不符 ⇒ 判不均衡
_rep_x = v4_harness.balance_report(_bdir, expect={_pols[0]: 999})
ok_x, _why_x = _rep_x.verdict()
ok(not ok_x and any("expect" in w for w in _why_x),
   f"v4 均衡负向对照：配席与 `--expect` 不符 ⇒ 判不均衡（{_why_x}）")
_rep_ok = v4_harness.balance_report(_bdir, equal_shares=True)
eq("v4 均衡：四策略等分时 CV 判据下的场次数", sorted(
    {p: sum(v.values()) for p, v in _rep_ok.policy_seat.items()}.values()), [8, 8, 8, 8])

_bskew = scratch("v4-balance-skew")
_write_run(_bskew, skew=True)
_rep2 = v4_harness.balance_report(_bskew)
_ok2, _why2 = _rep2.verdict()
ok(not _ok2, f"v4 均衡负向对照：座位偏斜必须判不均衡（{_why2}）")
ok(_rep2.seat_deviation() > v4_harness.SEAT_TOL,
   f"v4 均衡：偏斜偏差 {_rep2.seat_deviation():.2%} > 阈值 {v4_harness.SEAT_TOL:.0%}")
eq("v4 均衡：阈值口径（离散 1% / 对手 CV 5%）",
   (v4_harness.SEAT_TOL, v4_harness.OPP_CV_TOL), (0.01, 0.05))
_bout = v4_harness.write_balance(_bdir)
ok(_bout.is_file() and json.loads(_bout.read_text(encoding="utf-8"))["balanced"] is True,
   "v4 均衡：write_balance 落盘且结论一致")

# ---- P5：v4 权重导出（`net.bin` 格式 2）+ golden 夹具 --------------------------------------
import struct as _struct                                               # noqa: E402
from mahjong_ml.v4 import export as v4_export                          # noqa: E402

_v4shape = v4_export.expected_shapes(v4_export.FULL_DIMS)
eq("P5 导出：期望张量表条数（74 个张量）", len(_v4shape), 74)
eq("P5 导出：逐张量名不重复", len(set(_v4shape)), 74)
_v4sd = v4_model.build(20260928).state_dict()
_v4dims = v4_export.dims_from_state(_v4sd)
eq("P5 导出：从权重形状反推的维度", _v4dims, v4_export.FULL_DIMS)
_v4blob = v4_export.net_blob(_v4sd, _v4dims)
_v4blob2 = v4_export.net_blob(_v4sd, _v4dims)
ok(_v4blob == _v4blob2 and len(_v4blob) > v4_export.HEADER_BYTES,
   "P5 导出：同权重两次导出**逐字节相同**（固定顺序 + 无压缩）",
   f"{len(_v4blob)} B")
_pnet = scratch("v4-net") / "net.bin"
_v4p = v4_export.save_net(_v4sd, _v4dims, _pnet)
_eq = v4_export.read_net(_v4p)
eq("P5 导出：读回的格式号", _eq["format"], v4_export.NET_FORMAT)
eq("P5 导出：读回的维度", _eq["dims"], v4_export.FULL_DIMS)
eq("P5 导出：读回的张量数", len(_eq["tensors"]), 74)
eq("P5 导出：读回的块数", len(_eq["blocks"]), len(v4_spec.BLOCKS))
eq("P5 导出：块指纹 == 注册表指纹", _eq["fingerprint"], v4_spec.fingerprint())
ok(all(tuple(np.asarray(_eq["tensors"][k]).shape) == s for k, s in _v4shape.items()),
   "P5 导出：读回的每个张量形状与契约一致")
# 导出 → 读回 → 载入模型 → 前向必须与原始模型逐位相同（"导出的就是训练的那个"）
_v4m2 = v4_model.build(1)
_v4m2.load_state_dict(v4_export.state_from_net(_eq), strict=True)
_v4m2.eval()
_tv = v4_blocks.assemble(_v4obs, _side, allow_degraded=False)
import torch as _torch                                                  # noqa: E402
with _torch.no_grad():
    _args = (_torch.from_numpy(_tv.tile[None]), _torch.from_numpy(_tv.evt[None]),
             _torch.from_numpy(_tv.ctx[None]), _torch.from_numpy(_tv.cand[None]))
    _o1 = v4_model.build(20260928)(*_args)
    _o2 = _v4m2(*_args)
ok(bool((_o1["policy"] == _o2["policy"]).all()),
   "P5 导出：导出→读回→载入后的 logits 与原始模型逐位相同")
# 形状审计的负向对照：少一个张量 / 形状不对 ⇒ `net_blob` 必须报错（不是导出个坏文件）
try:
    v4_export.net_blob({k: v for k, v in _v4sd.items() if k != "heads.policy.bias"}, _v4dims)
    ok(False, "P5 导出：缺张量必须报错")
except v4_export.NetFormatError as _e:
    ok("heads.policy.bias" in str(_e), "P5 导出：缺张量被形状审计抓住 ✓", str(_e)[:60])
# golden 夹具：存在、版本、覆盖闸门、用例数与 meta 一致
_gold = Path(__file__).resolve().parent / "tests" / "golden" / "forward-v4.bin"
ok(_gold.is_file(), "P5 夹具：python/tests/golden/forward-v4.bin 在仓库里")
_gmeta = json.loads(_gold.with_suffix(".json").read_text(encoding="utf-8"))
eq("P5 夹具：格式号", _gmeta["format"], v4_export.GOLDEN_FORMAT)
eq("P5 夹具：块指纹 == 注册表指纹", _gmeta["blocks_fingerprint"], v4_spec.fingerprint())
eq("P5 夹具：用小网络（能进仓库）", _gmeta["dims"], v4_export.GOLDEN_DIMS)
ok(set(v4_export.REQUIRED_TAGS) <= set(_gmeta["coverage"]),
   "P5 夹具：覆盖闸门里的标签全都覆盖到了",
   f"coverage={_gmeta['coverage']}")
ok("ippatsu" in _gmeta["coverage"] and "riichi_any" in _gmeta["coverage"],
   "P5 夹具：覆盖面带立直/一发（Java 把布尔数组读成 0 的那条漂移就是它抓的）")
_graw = _gold.read_bytes()
_gmagic, _gfmt, _gcases, _gnetlen = _struct.unpack_from("<4I", _graw, 0)
eq("P5 夹具：魔数（MJ4G）", _gmagic, v4_export.GOLDEN_MAGIC)
eq("P5 夹具：用例数与 meta 一致", _gcases, _gmeta["cases"])
ok(_gnetlen == _gmeta["net_bytes"] and len(_graw) == _gmeta["bytes"],
   "P5 夹具：内嵌权重长度与总长度都与 meta 对得上")

# ---------------------------------------------------------------- 汇总

# 收尾清掉 scratch（它是**手写**的目录，删得掉；`tempfile` 建的那种在本沙箱下删不掉 —— 见文件头）
# ⚠ 只删自己那个子目录，`.tmp/` 本身**空了才删** —— 里面还可能有别人的东西（采集日志、临时导出）
shutil.rmtree(SCRATCH, ignore_errors=True)
try:
    SCRATCH.parent.rmdir()
except OSError:
    pass

print(f"\n自检：检查项 {count}，失败 {len(fails)}")
for f in fails:
    print(f"  FAIL: {f}")
print("SELFCHECK PASS" if not fails else "SELFCHECK FAIL")
sys.exit(1 if fails else 0)
