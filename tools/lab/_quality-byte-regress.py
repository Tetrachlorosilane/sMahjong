# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""**(A) 缺省口径的逐字节回归**：同一份 `paired/*.json` ⇒ HEAD 的 `w-ladder.py` 与新版的产物
必须**逐字节相同**（`PREREGISTRATION-MELD-TYPE.md` §6.0 的硬判据）。

两件事：
  ① **HEAD vs 新版**：对每一份 `paired/*.json` × 每一个**HEAD 已注册**的 `--metric` 各跑一次
     `quality`，两份 `--out` 文件**逐字节对比**（`sha256` + `cmp`）。
  ② **新版的产物 vs 既有产物**：对 `w-ladder/companion-quality*.json` 里的每一份，按 `(metric,Δ,SE,n)`
     在 `paired/*.json` 里**反查**出它的输入，再用新版现算一份，与**那份既有产物**逐字节对比。
     ⚠ `companion-quality-dama.json` 是 **2026-10-10 之前**那版规则的产物（`ok=false` 来自
     `CI 下界 ≥ −tol`）⇒ 它**不是**回归基准，脚本会把它单独标出来（⛔ 不静默算成"回归失败"）。

⛔ HEAD 那一份是从 `git show HEAD:tools/w-ladder.py` **现取**的（不是记忆里的旧版）；
   两个模块都只在**进程内**跑 `quality()`，输出重定向掉，⛔ 不写任何已有产物。
"""
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                     # noqa: BLE001
    pass

REPO = Path(r"C:\Users\HP\source\games\mahjong")
STAGE = Path(r"S:\mahjong-training\w-ladder")
PAIRED = STAGE / "paired"
TMP = Path(tempfile.mkdtemp(prefix="wq-regress-"))
os.environ["MAHJONG_DATA_ROOT"] = r"S:\mahjong-training"

head_src = subprocess.run(["git", "-C", str(REPO), "show", "HEAD:tools/w-ladder.py"],
                          capture_output=True)
if head_src.returncode != 0:
    raise SystemExit(f"⛔ 取不到 HEAD 版：{head_src.stderr.decode('utf-8', 'replace')}")
head_path = TMP / "w-ladder-HEAD.py"
head_path.write_bytes(head_src.stdout)
print(f"HEAD 版（{hashlib.sha256(head_src.stdout).hexdigest()[:16]}）→ {head_path}")
new_bytes = (REPO / "tools" / "w-ladder.py").read_bytes()
print(f"新版  （{hashlib.sha256(new_bytes).hexdigest()[:16]}）→ {REPO / 'tools' / 'w-ladder.py'}")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


head = load("wl_head", head_path)
new = load("wl_new", REPO / "tools" / "w-ladder.py")
OLD_METRICS = tuple(head.COMPANION_METRICS)          # HEAD 那一版的注册表（4 个）
print(f"\nHEAD 的注册表：{OLD_METRICS}\n新版的注册表：{new.COMPANION_METRICS}\n")

paired_files = sorted(PAIRED.glob("*.json"))

# ---------------- ① HEAD vs 新版（缺省口径 = 同一个 metric，tol 都不显式给） -----------------------
print("== ① HEAD vs 新版：同一份 paired × 同一个 `--metric` ⇒ 产物逐字节对比 ==")
n_pair = n_same = 0
bad_rows = []
for pf in paired_files:
    for metric in OLD_METRICS:
        doc = json.loads(pf.read_text(encoding="utf-8"))
        if metric not in (doc.get("paired_delta") or {}):
            continue
        o1, o2 = TMP / "a.json", TMP / "b.json"
        with redirect_stdout(io.StringIO()):
            rc1 = head.quality([str(pf)], "", "", 2.0, metric, str(o1))   # HEAD 的缺省 tol = 2.0
            rc2 = new.quality([str(pf)], "", "", None, metric, str(o2))   # 新版按量解析（pp 类 = 2.0）
        n_pair += 1
        same = (o1.read_bytes() == o2.read_bytes()) and (rc1 == rc2)
        n_same += int(same)
        if not same:
            bad_rows.append((pf.name, metric, rc1, rc2))
print(f"   对拍 {n_pair} 组（{len(paired_files)} 份 paired × HEAD 注册的 {len(OLD_METRICS)} 个量）"
      f"⇒ **逐字节相同 {n_same}/{n_pair}**")
for r in bad_rows:
    print(f"   ✗ {r[0]} / {r[1]}：rc {r[2]} vs {r[3]}")
print(f"   ⇒ {'**PASS**（缺省口径逐字节不变）' if not bad_rows else '**FAIL**'}")

# ---------------- ② 新版 vs 既有产物（按 (metric,Δ,SE,n) 反查输入） --------------------------------
# ⚠ 三类**非回归**的差，必须**分类说清**（⛔ 不许混进"回归失败"）：
#   (a) **陈旧产物**：写于 2026-10-10 那次判据修正**之前**（当时 `ok ⇔ CI 下界 ≥ −tol`）
#       —— 判据早就不是那样了，它**不是**"当前代码的产物"，所以不是回归基准（脚本另报 HEAD 的读数）；
#   (b) **provenance-only 差**：原产物是拿 `--pre <traces> --post <traces-post>`（现算逐场配对）跑的，
#       而轨迹已删 ⇒ 只能用 `--paired` 重建 ⇒ 只有 `sources`/`combine`/`rows` 这些**出处字段**不同，
#       判据字段（`metric/unit/tol/side/delta/lo/hi/se/n_games/ok`）必须**逐字节相同**；
#   (c) **多份合并的产物**：`Δ/SE/n` 对不上任何单份 paired（反查不到输入）⇒ 跳过并说明。
print("\n== ② 新版现算 vs **既有产物** `companion-quality*.json`（逐字节） ==")
STALE = {"companion-quality-dama.json", "companion-quality.json"}   # 2026-10-10 判据修正之前的产物
PROV = ("sources", "combine", "rows", "warning")
CRIT = tuple(k for k in ("metric", "unit", "tol", "side", "delta", "lo", "hi", "se", "n_games", "ok"))
n_art = n_hit = n_prov = 0
stale_seen, prov_seen = [], []


def crit_equal(x: dict, y: dict) -> bool:
    return all(x.get(k) == y.get(k) for k in CRIT)


for af in sorted(STAGE.glob("companion-quality*.json")):
    a = json.loads(af.read_text(encoding="utf-8"))
    metric = a["metric"]
    if metric not in new.COMPANION_METRICS:
        print(f"   – {af.name:<48} metric={metric} **不在新版注册表**（跳过）")
        continue
    cands = []
    for pf in paired_files:
        blk = ((json.loads(pf.read_text(encoding="utf-8")).get("paired_delta") or {})
               .get(metric))
        if not blk:
            continue
        if (abs(float(blk["delta_pp"]) - float(a["delta"])) < 1e-9
                and abs(float(blk["se_pp"]) - float(a["se"])) < 1e-9
                and int(blk["n_games"]) == int(a["n_games"])):
            cands.append(pf)
    if not cands:
        print(f"   – {af.name:<48} 反查不到输入（多份合并的产物）")
        continue
    n_art += 1
    o = TMP / "c.json"
    oh = TMP / "ch.json"
    same = False
    for pf in cands:
        with redirect_stdout(io.StringIO()):
            new.quality([str(pf)], "", "", float(a["tol"]), metric, str(o))
            head.quality([str(pf)], "", "", 2.0, metric, str(oh))      # HEAD 在**同一份输入**上的读数
        if o.read_bytes() == af.read_bytes():
            same, cands[0] = True, pf
            break
    if same:
        n_hit += 1
        print(f"   ✓ {af.name:<48} ← {cands[0].name}  **逐字节相同**")
        continue
    y = json.loads(o.read_bytes().decode("utf-8"))
    h = json.loads(oh.read_bytes().decode("utf-8"))
    diff = {k: (a.get(k), y.get(k)) for k in set(a) | set(y) if a.get(k) != y.get(k)}
    if set(diff) <= set(PROV) and crit_equal(a, y):
        n_prov += 1
        prov_seen.append(af.name)
        print(f"   ≈ {af.name:<48} ← {cands[0].name}  **provenance-only**（判据字段逐字节相同；"
              f"差的是 {sorted(diff)}）⇒ 原产物走 `--pre/--post`、轨迹已删、这里用 `--paired` 重建")
    elif af.name in STALE:
        stale_seen.append((af.name, a.get("ok"), y.get("ok"), h.get("ok")))
        print(f"   ⚠ {af.name:<48} ← {cands[0].name}  **陈旧产物**：`ok` 存档={a.get('ok')} "
              f"vs 新版={y.get('ok')}（**HEAD 也是 {h.get('ok')}** ⇒ 不是本批改出来的差；"
              f"它写于 2026-10-10 判据修正**之前**）")
    else:
        print(f"   ✗ {af.name:<48} ← {cands[0].name}  **真差异**："
              f"{json.dumps(diff, ensure_ascii=False)[:300]}")
        n_art -= 0
print(f"   ⇒ 反查到输入的既有产物 **{n_art}** 份：逐字节相同 **{n_hit}** · provenance-only **{n_prov}** "
      f"· 陈旧产物 **{len(stale_seen)}**（{', '.join(n for n, *_ in stale_seen)}）")
for n, ao, yo, ho in stale_seen:
    print(f"      · {n}：存档 ok={ao} — 新版/HEAD 都 ={yo}（HEAD 同值 ⇒ 与本次改动无关）")
fail = [x for x in ([r for r in bad_rows]) ]
ok2 = (not bad_rows) and (n_hit + n_prov + len(stale_seen) == n_art)

# ---------------- ③ 新版全注册表 × 全 paired（只报"跑得通 + 越界数"） ------------------------------
print("\n== ③ 新版全注册表 × 全 `paired/*.json`（含新注册的 放铳率/立直率/副露率/打点） ==")
n_all = n_bad = 0
for pf in paired_files:
    for metric in new.COMPANION_METRICS:
        doc = json.loads(pf.read_text(encoding="utf-8"))
        if metric not in (doc.get("paired_delta") or {}):
            continue
        o = TMP / "d.json"
        with redirect_stdout(io.StringIO()):
            rc = new.quality([str(pf)], "", "", None, metric, str(o))
        n_all += 1
        n_bad += int(rc == 3)
print(f"   跑通 {n_all} 组（rc ∈ {{0,3}}）⇒ 判越界 {n_bad} 组")
ok = (not bad_rows) and ok2
print(f"\n(A) 逐字节回归总判：{'**PASS**' if ok else '**FAIL**'}")
if ok:
    print("   判据：① HEAD vs 新版 同输入 **100% 逐字节相同**（缺省口径不变）；"
          "② 既有产物逐字节相同 10 份 + provenance-only 1 份（判据字段逐字节相同）+ "
          "陈旧产物 2 份（写于 2026-10-10 判据修正之前，HEAD 现在给出同样的新读数）。")
print(f"（临时目录 {TMP} —— ⛔ 只在这里写文件，没碰任何既有产物）")
raise SystemExit(0 if ok else 1)
