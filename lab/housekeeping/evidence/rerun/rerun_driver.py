"""RERUN 驱动器 —— BL-13 的 P3-01 型对照在 **信任域打开（`--kl-early-stop 0.10`）** 后重跑。

权威口径 = `S:\\mahjong-training\\w-ladder\\RERUN-PLAN.md`（**跑前冻结**）。
用法： `python rerun_driver.py A|B|ALL`
  · 臂 A（新鲜·多轮）：k=2 轮 × 700 场，**先训后采**（D_1@θ_0 → 训 100 步 → D_2@θ_1 → 训 100 步）
  · 臂 B（冻结·少轮长训）：**一次** 1,400 场 @θ_0 → 该冻结数据上**一段** 200 步
⛔ 跑后不改这个文件；偏离写进 `RERUN-RESULT.md`。
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
PY = Path(r"S:\mahjong-training\tools\py312\python.exe")
TRAINER = Path(r"S:\mahjong-training\tools\trainer\trainer.exe")
ORCH = REPO / "tools" / "overlap-orchestrator.py"
BUILD = REPO / "tools" / "build"
BASE = BUILD / "v4-expert-defrep-g10" / "net.bin"          # θ_0
POOLS = {"def": BUILD / "v4-expert-def7-g10" / "net.bin",
         "win": BUILD / "v4-expert-win2-g10" / "net.bin",
         "atk": BUILD / "v4-expert-atk3-g10" / "net.bin"}
W = REPO / "tmp" / "rerun"
EV = Path(r"S:\mahjong-training\w-ladder\_rerun")
LOGS = EV / "logs"
WORKERS = 8
STEPS = 100            # M（每轮/每段预算步数）
K = 2                  # 轮数
GAMES = 700            # 每轮场数
TEMP = 0.5
THRESH = 0.10
PAT = re.compile(rb'"policy":"([^"]*)"')

WALL: dict[str, dict] = {}          # 每相墙钟/退出码（算力对账）
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "python" / ".venv" / "Lib" / "site-packages"))
try:                                 # 控制台是 GBK ⇒ ⭐/⚠ 会 UnicodeEncodeError（实测已崩过一次）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                    # noqa: BLE001
    pass


# ------------------------------------------------------------------ 基础
def say(f: Path, t: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {t}"
    print(line, flush=True)
    with f.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class MEM(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def commit_free_gb() -> float:
    m = MEM(); m.dwLength = ctypes.sizeof(MEM)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPageFile / (1 << 30)


def preflight(phase: str) -> dict:
    """⭐ fail-closed 环境探针：取不到数据 ⇒ 拒跑（⛔ 不许当"机器很安静"）。"""
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
          "Get-CimInstance Win32_Process | Select-Object ProcessId,Name,WorkingSetSize,"
          "CommandLine,ParentProcessId | ConvertTo-Json -Compress")
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise SystemExit("⛔ 探针不可用（找不到 powershell）⇒ fail-closed 拒跑")
    p = subprocess.run([exe, "-NoProfile", "-Command", ps], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0 or not (p.stdout or "").strip():
        raise SystemExit(f"⛔ 探针取不到数据（rc={p.returncode}）⇒ fail-closed 拒跑")
    rows = json.loads(p.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    sysnames = {"smss.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe"}
    seen = {str(r.get("Name", "")).lower() for r in rows}
    if len(rows) < 3 or not (sysnames & seen):
        raise SystemExit(f"⛔ 枚举自检不成立（{len(rows)} 个进程 / 系统进程 {sorted(sysnames & seen)}）⇒ 拒跑")
    if not any(r.get("CommandLine") for r in rows):
        raise SystemExit("⛔ 命令行探针取不到（Win32_Process.CommandLine 全空）⇒ fail-closed 拒跑")
    busy = [r for r in rows
            if str(r.get("Name", "")).lower() == "trainer.exe"
            or (str(r.get("Name", "")).lower().startswith(("python", "java"))
                and (r.get("WorkingSetSize") or 0) >= 200 << 20)]
    cf = commit_free_gb()
    rec = {"phase": phase, "ts": time.strftime("%F %T"), "n_procs": len(rows),
           "system_seen": sorted(sysnames & seen), "busy": [(r["ProcessId"], r["Name"]) for r in busy],
           "paddleocr": [r["ProcessId"] for r in rows if str(r.get("Name", "")).lower() == "paddleocr_mcp.exe"],
           "commit_free_gb": round(cf, 2)}
    if busy:
        raise SystemExit(f"⛔ 有竞争负载 {rec['busy']} ⇒ 拒跑（预注册：一次一个重负载）")
    if cf < 8.0:
        raise SystemExit(f"⛔ commit_free {cf:.2f} GB < 8 GB ⇒ 拒跑")
    return rec


def run(cmd: list[str], tag: str, logf: Path) -> int:
    env = dict(os.environ)
    env.update({"PYTHONPATH": f"{REPO / 'python'};{REPO / 'python' / '.venv' / 'Lib' / 'site-packages'}",
                "MAHJONG_DATA_ROOT": str(W / "root"), "MAHJONG_TRAINER": str(TRAINER),
                "MAHJONG_PRODUCER": "cpp", "OMP_NUM_THREADS": "8", "MKL_NUM_THREADS": "8"})
    cf0 = commit_free_gb()
    cfmin = [cf0]
    stop = [False]

    def _sampler() -> None:                     # 1 Hz 取 system commit_free（纯 ctypes，⛔ 不再起子进程）
        while not stop[0]:
            try:
                cfmin[0] = min(cfmin[0], commit_free_gb())
            except Exception:                    # noqa: BLE001 —— 采样失败不算崩溃，但最小值保持保守
                pass
            time.sleep(1.0)

    t0 = time.time()
    th = threading.Thread(target=_sampler, daemon=True)
    th.start()
    try:
        with logf.open("a", encoding="utf-8") as fh:
            fh.write(f"\n$ {' '.join(cmd)}\n")
            fh.flush()
            p = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(REPO), env=env)
    finally:
        stop[0] = True
        th.join(timeout=3.0)
        cfmin[0] = min(cfmin[0], commit_free_gb())
    cfmin = cfmin[0]
    wall = time.time() - t0
    WALL[tag] = {"exit": p.returncode, "wall_s": round(wall, 1),
                 "commit_free_before_gb": round(cf0, 2), "commit_free_min_gb": round(cfmin, 2)}
    say(LOGS / "rerun.driver.log", f"  {tag}: exit={p.returncode} wall={wall:.1f}s "
                                   f"commit_free_min={cfmin:.2f}GB")
    (EV / "compute.json").write_text(json.dumps(WALL, ensure_ascii=False, indent=1), encoding="utf-8")
    return p.returncode


# ------------------------------------------------------------------ 命令
def collect_cmd(games: int, theta: Path, seed: int, out_dir: Path) -> list[str]:
    student = f"net:{theta}@0#{TEMP:g}"
    pol = ",".join([student] + [f"net:{POOLS[s]}" for s in ("def", "win", "atk")])
    return [str(TRAINER), "selfplay", str(games), "--workers", str(WORKERS),
            "--rotate", "--rotate-perm", "--aux", "--policy", pol,
            "--seed", str(seed), "--out", str(out_dir)]


def features_cmd(src: Path) -> list[str]:
    return [str(TRAINER), "features", str(src), "--workers", str(WORKERS)]


def compact_cmd(src: Path, out: Path, student_spec: str) -> list[str]:
    return [str(PY), "-m", "mahjong_ml.v4.dataset", str(src), str(out), "--aux",
            "--student", student_spec, "--workers", "6",
            "--behaviour-id", "--behaviour-root", str(BUILD)]


def train_cmd(data: Path, label: str, init: Path, seed: int, *, total_steps: int,
              stop_after: int = 0, save_opt: bool = False, resume: Path | None = None) -> list[str]:
    c = [str(PY), "-u", "-m", "mahjong_ml.v4.pretrain", "--data", str(data), "--label", label,
         "--objective", "ppo", "--value-target", "delta", "--advantage", "hand",
         "--rank-weight", "0.2", "--epochs", "1", "--batch", "256", "--lr", "1e-4",
         "--stage-a", "0", "--stage-b", "0", "--seed", str(seed),
         "--init", str(init), "--behaviour", str(init), "--behaviour-temp", f"{TEMP:g}",
         "--behaviour-multi", "--rho-max", "100", "--rho-max-action", "fail",
         "--stale-kl-budget", "0.30", "--ref-beta", "0.5", "--grad-clip", "0.5",
         "--kl-early-stop", f"{THRESH:g}", "--kl-min-steps", "60",
         "--baseline-fit", "scale", "--threads", "8", "--max-steps", str(total_steps)]
    if stop_after:
        c += ["--stop-after-steps", str(stop_after)]
    if save_opt:
        c += ["--save-optimizer"]
    if resume is not None:
        c += ["--resume-optimizer", str(resume)]
    return c


def export_cmd(ckpt_dir: Path, out: Path, label: str) -> list[str]:
    return [str(PY), "-m", "mahjong_ml.v4.export", "weights", "--ckpt", str(ckpt_dir / "model.pt"),
            "--out", str(out), "--label", label]


def spec_single(tag: str, src: Path, cmd: list[str], dummy: list[str]) -> dict:
    """`other` = **哑采 1 场**：只为让 G4 有可 pin 的权重/可执行文件（BL-13 `spec-B-s01` 同款）。
    ⛔ `other.cmd` 留空会被 G4 拒（BL-13 的 `spec-A-r2-seq` 实测 exit=3）。"""
    return {"tag": tag, "evidence_dir": str(EV),
            "primary": {"phase": "train", "src": str(src), "out": str(W / "root" / "ckpt"),
                        "student": str(src), "workers": WORKERS, "cwd": str(REPO / "python"),
                        "cmd": cmd},
            "other": {"kind": "collect", "out_dir": str(W / "raw" / f"{tag}-none"), "cmd": dummy},
            "gate": {"min_commit_free_gb": 8.0, "poll_sec": 1.0, "fallback": "abort"}}


def train_phase(tag: str, src: Path, cmd: list[str], dummy: list[str], round_no: int,
                logf: Path) -> int:
    if (W / "root" / "ckpt" / tag / "metrics.json").exists():
        say(logf, f"  ↩ {tag}: 已有 metrics.json ⇒ **跳过训练**（续跑）")
        WALL[tag] = {"exit": 0, "wall_s": 0.0, "skipped": True}
        (EV / "compute.json").write_text(json.dumps(WALL, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        return 0
    guard_phase(tag, logf)
    sp = EV / f"spec-{tag}.json"
    sp.write_text(json.dumps(spec_single(tag, src, cmd, dummy), ensure_ascii=False, indent=1),
                  encoding="utf-8")
    args = [str(PY), str(ORCH), "pair", "--pair", "train:collect", "--driver", str(sp),
            "--data-root", str(W / "root"), "--compact-root", str(W / "compact"),
            "--raw-root", str(W / "raw"), "--workers", str(WORKERS), "--evidence-dir", str(EV),
            "--only", "primary", "--round", str(round_no), "--rounds", str(EV / "rounds.jsonl"),
            "--out", str(EV / f"{tag}.result.json")]
    return run(args, tag, logf)


def metrics_of(label: str) -> dict:
    mj = W / "root" / "ckpt" / label / "metrics.json"
    if not mj.exists():
        return {"steps": None, "stop_reason": "<没有 metrics.json>"}
    m = json.loads(mj.read_text(encoding="utf-8"))
    hist = m.get("history") or []
    kl = None
    for h in reversed(hist):
        if h.get("kl") is not None:
            kl = h["kl"]; break
    return {"steps": m.get("steps") or (hist[-1].get("step") if hist else None),
            "stop_reason": str(m.get("stop_reason") or ""), "kl_last": kl,
            "label": label}


def scan_policy(raw: Path, theta: Path, forbid: list[Path], tag: str) -> dict:
    """⭐ 操作检查①：**逐** `g*.jsonl` 的 `policy=` 必须含本轮 θ_i、⛔ 不许出现 θ_{i-1}。"""
    want = f"net:{theta}@0#{TEMP:g}"
    bad_want = [f"net:{f}@0#{TEMP:g}" for f in forbid]
    files = sorted(raw.glob("g*.jsonl"))
    n_ok = n_missing = n_forbid = 0
    seen: set[str] = set()
    examples = []
    for i, f in enumerate(files):
        blob = f.read_bytes()
        pols = {m.decode("utf-8", "replace") for m in PAT.findall(blob)}
        seen |= pols
        if want in pols:
            n_ok += 1
        else:
            n_missing += 1
        if any(b in pols for b in bad_want):
            n_forbid += 1
        if i < 2:
            examples.append({"file": f.name, "policies": sorted(pols)})
    rec = {"tag": tag, "raw": str(raw), "n_files": len(files), "want": want,
           "forbidden": bad_want, "n_files_with_want": n_ok, "n_files_missing_want": n_missing,
           "n_files_with_forbidden": n_forbid, "distinct_policies": sorted(seen),
           "examples": examples, "ok": bool(files) and n_missing == 0 and n_forbid == 0}
    (EV / f"policy-{tag}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1),
                                           encoding="utf-8")
    say(LOGS / "rerun.driver.log",
        f"  ⭐ policy-check[{tag}]：{n_ok}/{len(files)} 个 g*.jsonl 含 `{want}` · "
        f"缺={n_missing} · 出现禁项={n_forbid} ⇒ {'OK' if rec['ok'] else '⛔FAIL'}")
    return rec


def cleanup(dirs: list[Path], tag: str) -> None:
    freed = 0
    for d in dirs:
        if d.exists():
            freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            shutil.rmtree(d, ignore_errors=True)
    say(LOGS / "rerun.driver.log", f"  🧹 {tag}：删 {[d.name for d in dirs]}，释放 {freed / 1e9:.2f} GB")


# ------------------------------------------------------------------ 续跑标记 + 相前闸门
def _n(d: Path, pat: str) -> int:
    return len(list(d.glob(pat))) if d.exists() else 0


def done_collect(d: Path, games: int) -> bool:
    return _n(d, "g*.jsonl") >= games and _n(d, "g*.aux.npz") >= games


def done_features(d: Path, games: int) -> bool:
    return _n(d, "g*.feat.bin") >= games


def done_compact(c: Path) -> bool:
    m = c / "meta.json"
    return m.exists() and m.stat().st_size > 0


def guard_phase(tag: str, logf: Path) -> None:
    """相前 fail-closed 闸门：commit_free <8 GB ⇒ 等最多 5 min，仍不够就拒跑（纪律字面）。"""
    for _ in range(300):
        cf = commit_free_gb()
        if cf >= 8.0:
            return
        time.sleep(1.0)
    raise SystemExit(f"⛔ {tag}: commit_free {commit_free_gb():.2f} GB < 8 GB ⇒ 按纪律拒跑")


def step(tag: str, cmd: list[str], marker: bool, logf: Path) -> int:
    if marker:
        say(logf, f"  ↩ {tag}: **已存在 ⇒ 跳过**（续跑）")
        WALL[tag] = {"exit": 0, "wall_s": 0.0, "skipped": True}
        (EV / "compute.json").write_text(json.dumps(WALL, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        return 0
    guard_phase(tag, logf)
    return run(cmd, tag, logf)


# ------------------------------------------------------------------ 两臂
def arm_a(logf: Path) -> int:
    rc: dict[str, int] = {}
    theta = BASE
    d1, c1 = W / "raw" / "rerun-A-r1", W / "compact" / "rerun-A-r1"
    say(logf, "== 臂 A：预采 D_1（700 场，θ_0）==")
    rc["A-collect-r1"] = step("A-collect-r1", collect_cmd(GAMES, theta, 52950101, d1),
                              done_collect(d1, GAMES), logf)
    rc["A-features-r1"] = step("A-features-r1", features_cmd(d1), done_features(d1, GAMES), logf)
    rc["A-compact-r1"] = step("A-compact-r1", compact_cmd(d1, c1, f"net:{theta}@0#{TEMP:g}"),
                              done_compact(c1), logf)
    scan_policy(d1, theta, [], "A-r1")
    for i in range(1, K + 1):
        label = f"rerun-A-r{i}"
        data = W / "compact" / label
        tr = train_cmd(data, label, theta, 52950101 + i, total_steps=STEPS)
        dummy = collect_cmd(1, theta, 52950109 + i, W / "raw" / f"{label}-none")
        say(logf, f"== 臂 A 第 {i}/{K} 轮：训 {STEPS} 步（init == behaviour == 本轮 θ）==")
        rc[f"A-train-r{i}"] = train_phase(label, data, tr, dummy, i, logf)
        m = metrics_of(label); m["tag"] = label
        (EV / f"metrics-{label}.json").write_text(json.dumps(m, ensure_ascii=False, indent=1),
                                                 encoding="utf-8")
        say(logf, f"  {label}: 步数={m['steps']} / 预算={STEPS} / stop_reason={m['stop_reason'] or '(空)'} "
                  f"/ KL终值={m['kl_last']}")
        out = BUILD / label / "net.bin"
        out.parent.mkdir(parents=True, exist_ok=True)
        rc[f"A-export-r{i}"] = step(f"A-export-r{i}", export_cmd(W / "root" / "ckpt" / label, out, label),
                                    out.exists() and (out.stat().st_mtime
                                                      >= (W / "root" / "ckpt" / label / "model.pt").stat().st_mtime),
                                    logf)
        theta = out
        if i < K:
            raw2, c2 = W / "raw" / f"rerun-A-r{i + 1}", W / "compact" / f"rerun-A-r{i + 1}"
            say(logf, f"== 臂 A：**先用 θ_{i} 采** D_{i + 1}（⛔ 不用 pair --overlap）==")
            rc[f"A-collect-r{i + 1}"] = step(f"A-collect-r{i + 1}",
                                             collect_cmd(GAMES, theta, 52950101 + i, raw2),
                                             done_collect(raw2, GAMES), logf)
            rc[f"A-features-r{i + 1}"] = step(f"A-features-r{i + 1}", features_cmd(raw2),
                                              done_features(raw2, GAMES), logf)
            rc[f"A-compact-r{i + 1}"] = step(f"A-compact-r{i + 1}",
                                             compact_cmd(raw2, c2, f"net:{theta}@0#{TEMP:g}"),
                                             done_compact(c2), logf)
            scan_policy(raw2, theta, [BASE], f"A-r{i + 1}")
        cleanup([W / "raw" / f"rerun-A-r{i}", W / "compact" / f"rerun-A-r{i}"], f"A-r{i}")
    say(logf, f"  θ_2（臂 A 候选）= {theta}")
    return 0 if all(v == 0 for v in rc.values()) else 1


def arm_b(logf: Path) -> int:
    rc: dict[str, int] = {}
    db, cb = W / "raw" / "rerun-B-all", W / "compact" / "rerun-B-all"
    say(logf, f"== 臂 B：**一次**采 {K * GAMES} 场（θ_0）+ 冻结数据上**一段** {K * STEPS} 步 ==")
    rc["B-collect"] = step("B-collect", collect_cmd(K * GAMES, BASE, 52950302, db),
                           done_collect(db, K * GAMES), logf)
    rc["B-features"] = step("B-features", features_cmd(db), done_features(db, K * GAMES), logf)
    rc["B-compact"] = step("B-compact", compact_cmd(db, cb, f"net:{BASE}@0#{TEMP:g}"),
                           done_compact(cb), logf)
    scan_policy(db, BASE, [], "B-all")
    label = "rerun-B-s01"
    tr = train_cmd(cb, label, BASE, 52950202, total_steps=K * STEPS,
                   stop_after=K * STEPS, save_opt=True)
    dummy = collect_cmd(1, BASE, 52950309, W / "raw" / f"{label}-none")
    rc["B-train"] = train_phase(label, cb, tr, dummy, 11, logf)
    m = metrics_of(label); m["tag"] = label
    (EV / f"metrics-{label}.json").write_text(json.dumps(m, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
    say(logf, f"  {label}: 步数={m['steps']} / 预算={K * STEPS} / stop_reason={m['stop_reason'] or '(空)'} "
              f"/ KL终值={m['kl_last']}")
    out = BUILD / label / "net.bin"
    out.parent.mkdir(parents=True, exist_ok=True)
    rc["B-export"] = step("B-export", export_cmd(W / "root" / "ckpt" / label, out, label),
                          out.exists(), logf)
    say(logf, f"  θ_B（臂 B 候选）= {out}")
    cleanup([db, cb], "B")
    return 0 if all(v == 0 for v in rc.values()) else 1


def main() -> int:
    arm = (sys.argv[1] if len(sys.argv) > 1 else "ALL").upper()
    for d in (W / "root", W / "compact", W / "raw", EV, LOGS, BUILD):
        d.mkdir(parents=True, exist_ok=True)
    logf = LOGS / f"rerun-{arm}.log"
    say(logf, f"===== RERUN（阈值 {THRESH:g}）：臂 {arm} · k={K} · 每轮 {GAMES} 场 · M={STEPS} 步 "
              f"· 开始 {time.strftime('%F %T')} =====")
    pf = preflight(arm)
    say(logf, f"  preflight: {json.dumps(pf, ensure_ascii=False)}")
    (EV / f"preflight-{arm}.json").write_text(json.dumps(pf, ensure_ascii=False, indent=1),
                                              encoding="utf-8")
    fps = {"theta0": {"path": str(BASE), "sha256": sha256(BASE), "bytes": BASE.stat().st_size},
           "trainer": {"path": str(TRAINER), "sha256": sha256(TRAINER)}}
    for k, v in POOLS.items():
        fps[k] = {"path": str(v), "sha256": sha256(v)}
    (EV / "fingerprints.json").write_text(json.dumps(fps, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    say(logf, f"  recipe 指纹：θ_0={fps['theta0']['sha256'][:12]} def={fps['def']['sha256'][:8]} "
              f"win={fps['win']['sha256'][:8]} atk={fps['atk']['sha256'][:8]} "
              f"trainer={fps['trainer']['sha256'][:12]}")
    t0 = time.time()
    r = 0
    if arm in ("A", "ALL"):
        r |= arm_a(logf)
    if arm in ("B", "ALL"):
        r |= arm_b(logf)
    say(logf, f"===== RERUN 臂 {arm} 结束：rc={r} 墙钟 {(time.time() - t0) / 60:.1f} min =====")
    return r


if __name__ == "__main__":
    raise SystemExit(main())
