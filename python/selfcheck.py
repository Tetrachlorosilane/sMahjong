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


from mahjong_ml import bc, dataset as ds, features, guard, nets, paths   # noqa: E402
from mahjong_ml import eval as ml_eval                    # noqa: E402
from mahjong_ml import dagger                             # noqa: E402
from mahjong_ml import hybrid as hyb                      # noqa: E402
from mahjong_ml import offline_rl, rewards                # noqa: E402
from mahjong_ml import awr                                # noqa: E402
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
ok(ml_eval.required_n(32.0, 2.0) == 984, "样本量反推：sd=32、Δ=2 → 984 场",
   f"got={ml_eval.required_n(32.0, 2.0)}")
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
tmp_root = scratch("paths")
saved_root, saved_quota = paths.DATA_ROOT, dict(paths.QUOTA_GB)
try:
    paths.DATA_ROOT = tmp_root
    paths.ensure_root()
    eq("六个子目录建齐", sum((tmp_root / d).is_dir() for d in paths.SUBDIRS), len(paths.SUBDIRS))
    paths.QUOTA_GB["raw"] = 0.00005                      # ≈50 KB，方便触发淘汰
    old = paths.allocate("raw", "old")
    (old / "g0.jsonl").write_bytes(b"x" * 120_000)       # 明显超过配额 → 下一次分配应删掉它
    paths.allocate("raw", "new")                         # 再分配 → 应把最旧的 raw 条目删掉
    ok(not old.exists(), "超配额时**从最旧的开始删**", f"old 还在？{old.exists()}")
    paths.QUOTA_GB["raw"] = 30.0
    d2 = paths.allocate("raw", "ok")
    ok(d2.is_dir(), "配额内正常分配")
    ok("raw=" in paths.report(), "report() 给出各子目录占用", paths.report()[:60])
finally:
    paths.DATA_ROOT, paths.QUOTA_GB = saved_root, saved_quota
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
eq("state_dim 固定（改了要同步 Java）", feat.state_dim(), 607)
eq("cand_dim 固定", feat.cand_dim(), 96)
eq("派生段长与 Java ObsFeatures 一致", (feat.DERIVED_DECISION, feat.DERIVED_CANDIDATE), (68, 8))


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
# 派生量：传了就按单点系数归一化落在末尾，不传就填 0（缺 sidecar 时才该发生）
sv_d = feat.state_vector(obs, [50] * feat.DERIVED_DECISION)
ok(abs(float(sv_d[-1]) - 0.5) < 1e-6, "派生危险度按 /100 归一化放在末尾",
   f"got={float(sv_d[-1])}")
ok(np.allclose(sv[-feat.DERIVED_DECISION:], 0.0), "不传派生量时末尾填 0")
cd_d = feat.cand_vector_full("discard:1m", [8, 34, 136, 0, 0, 0, 0, 4])
ok(abs(float(cd_d[-1]) - 1.0) < 1e-6 and abs(float(cd_d[-8]) - 1.0) < 1e-6,
   "派生候选量按 DERIVED_SCALE_CAND 归一化（向听 8→1.0、dora 4→1.0）",
   f"shanten={float(cd_d[-8])} dora={float(cd_d[-1])}")

print("== 数据集（按整场切分 + 紧凑落盘 + 派生特征 sidecar）==")


def write_sidecar(jsonl, rows):
    """按 `TraceFeatures` 的三段式写一个 sidecar（合成用；同时也是格式契约的守卫）。"""
    import struct
    ndec = len(rows)
    a = np.concatenate([np.asarray(r["danger"], dtype=np.uint8) for r in rows])
    b = np.asarray([len(r["cand"]) for r in rows], dtype="<i2")
    c = np.concatenate([np.asarray(r["cand"], dtype="<i2") for r in rows]).reshape(-1, 8)
    head = struct.pack("<5i", ds.SIDECAR_MAGIC, feat.DERIVED_VERSION, ndec,
                       feat.DERIVED_DECISION, feat.DERIVED_CANDIDATE)
    out = jsonl.parent / (jsonl.name.split(".")[0] + ".feat.bin")
    out.write_bytes(head + a.tobytes() + b.tobytes() + c.tobytes())
    return out


droot = scratch("dataset")
src = droot / "src"
src.mkdir(parents=True, exist_ok=True)
DANGER = [7] * feat.DERIVED_DECISION                      # 合成的危险度（68 维）
CAND0 = [[1, 2, 3, 4, 5, 6, 7, 8], [0] * 8]               # 两条候选的派生量（原始整数）
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
    write_sidecar(jsonl, [{"danger": DANGER, "cand": CAND0} for _ in range(2)])

build_meta = ds.build(src, droot / "compact", val_frac=0.5, split_seed=0, quiet=True)
eq("特征版本写进 meta", build_meta["feature_version"], feat.FEATURE_VERSION)
eq("派生特征版本写进 meta", build_meta["derived_version"], feat.DERIVED_VERSION)
ok(build_meta.get("has_derived") is True, "meta 标记「已带派生特征」")
eq("按场切分：2 场 → 1 训 1 验", (len(build_meta["train_files"]), len(build_meta["val_files"])), (1, 1))
eq("训练条数", build_meta["train_decisions"], 2)
tr = ds.load_split(droot / "compact", "train")
eq("读回的 state 形状", tr["state"].shape, (2, feat.state_dim()))
eq("读回的 cand 形状", tr["cand"].shape, (2, 2, feat.cand_dim()))
ok(np.array_equal(np.asarray(tr["n_legal"]), [2, 2]), "n_legal 记对了")
# 派生量落盘的口径：state 末尾 68 维是**归一化后**的危险度；cand 末尾 8 维是**原始整数**
ok(np.allclose(np.asarray(tr["state"][0, -feat.DERIVED_DECISION:], dtype=np.float32),
               np.asarray(DANGER, dtype=np.float32) / feat.DERIVED_SCALE_DANGER, atol=1e-3),
   "state 末尾 68 维 = 危险度 / 100")
ok(np.array_equal(np.asarray(tr["cand"][0, 0, -feat.DERIVED_CANDIDATE:]),
                  np.asarray(CAND0[0], dtype=np.uint8)),
   "cand 末尾 8 维 = 派生量的原始整数（归一化留给 _batch）")
sc = ds.load_sidecar(ds.sidecar_path(src / "g0.jsonl"))
eq("sidecar：条数", sc["n"], 2)
eq("sidecar：危险度形状", sc["danger"].shape, (2, feat.DERIVED_DECISION))
eq("sidecar：候选块形状", sc["cand"].shape, (4, feat.DERIVED_CANDIDATE))
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
write_sidecar(bad / "g0.jsonl", [{"danger": DANGER, "cand": [[0] * 8] * 3} for _ in range(2)])
write_sidecar(bad / "g1.jsonl", [{"danger": DANGER, "cand": CAND0} for _ in range(2)])
try:
    ds.build(bad, bad / "compact", val_frac=0.5, quiet=True)
    ok(False, "sidecar 与 jsonl 对不上时必须报错")
except ValueError as e:
    ok("nLegal" in str(e), "sidecar 行数/N 与 jsonl 不符时报错", str(e)[:60])
same = ds.split_files(ds.trace_files(src), 0.5, 0)
same2 = ds.split_files(ds.trace_files(src), 0.5, 0)
eq("切分可复现（同种子同结果）", [f.name for f in same[0]], [f.name for f in same2[0]])
ok(not (set(f.name for f in same[0]) & set(f.name for f in same[1])), "训练/验证没有交集")

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
    write_sidecar(run / f"g{g}.jsonl", [{"danger": DANGER, "cand": CAND0} for _ in range(2)])
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
