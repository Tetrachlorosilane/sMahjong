# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`dama` 轴的**硬判据 ①**：与"改前版影子包"对拍（`.npy` + meta + 审计账 sha256 全同）。

为什么这么写：
  * 影子包 = 改前的整份 `mahjong_ml`（`_dama-shadow\mahjong_ml`）；
  * ⚠ `-m` 会把 cwd 放进 `sys.path`（上一轮有人在这里**假绿**过）⇒ 全程 `PYTHONSAFEPATH=1`
    且 PYTHONPATH **显式**给出要用的那一份（影子跑**不含**仓库 `python`，新跑只含仓库 `python`）；
  * 两次跑**同一个 out 目录**（跑完取哈希、删掉、再跑另一版）⇒ 产物路径逐字节可比，
    ⛔ 不需要"把路径归一化后再比"那种会掩盖差异的做法。

用法：python _dama-invariance.py [games]
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
DATA = Path(r"S:\mahjong-training")
PY = r"S:\mahjong-training\tools\py312\python.exe"
TRAINER = DATA / "tools" / "trainer" / "trainer.exe"
SHADOW = DATA / "w-ladder" / "_dama-shadow"
WORK = DATA / "w-ladder" / "_dama-inv"
RAW = DATA / "raw" / "_dama-inv-g01"
OUT = DATA / "compact" / "_dama-inv-out"
AUDIT = WORK / "audit"
VENV = REPO / "python" / ".venv" / "Lib" / "site-packages"
STU = f"net:{REPO / 'tools' / 'build' / 'v4-expert-defrep-g10' / 'net.bin'}@0#0.5"
B = REPO / "tools" / "build"
OPP = [f"net:{B / 'v4-expert-def7-g10' / 'net.bin'}", f"net:{B / 'v4-expert-win2-g10' / 'net.bin'}",
       f"net:{B / 'v4-expert-atk3-g10' / 'net.bin'}"]

CONFIGS = [
    ("缺省（无 --style-bonus）", []),
    ("riichi + hand", ["--style-bonus", "riichi=1000:no_deal", "--style-bonus-whiten", "student"]),
    ("riichi + decision", ["--style-bonus", "riichi=1000:no_deal", "--style-bonus-whiten", "student",
                           "--style-bonus-mode", "decision"]),
    ("meld + hand", ["--style-bonus", "meld=1000:no_deal", "--style-bonus-whiten", "student"]),
    ("meld + decision", ["--style-bonus", "meld=1000:no_deal", "--style-bonus-whiten", "student",
                         "--style-bonus-mode", "decision"]),
    ("dama + decision（新轴）", ["--style-bonus", "dama=1000:no_deal", "--style-bonus-whiten", "student",
                                 "--style-bonus-mode", "decision"]),
]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def env_for(which: str) -> dict:
    e = dict(os.environ)
    e["MAHJONG_DATA_ROOT"] = str(DATA)
    e["MAHJONG_TRAINER"] = str(TRAINER)
    e["PYTHONSAFEPATH"] = "1"           # ⛔ 不许把 cwd 塞进 sys.path（上一轮的假绿点）
    e["PYTHONIOENCODING"] = "utf-8"
    e["MAHJONG_STYLE_AUDIT_DIR"] = str(AUDIT)
    e["PYTHONPATH"] = (f"{SHADOW};{VENV}" if which == "shadow" else f"{REPO / 'python'};{VENV}")
    return e


def build(which: str, extra: list) -> dict:
    if OUT.exists():
        shutil.rmtree(OUT)
    AUDIT.mkdir(parents=True, exist_ok=True)
    (AUDIT / f"{OUT.name}.json").unlink(missing_ok=True)
    cmd = [PY, "-m", "mahjong_ml.v4.dataset", "build", str(RAW), str(OUT),
           "--aux", "--student", STU, "--workers", "1", *extra]
    p = subprocess.run(cmd, cwd=str(WORK), env=env_for(which),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        return {"rc": p.returncode, "tail": (p.stdout + p.stderr)[-400:], "hashes": {}}
    files = sorted(OUT.glob("*.npy")) + [OUT / "meta.json"]
    h = {f.name: sha(f) for f in files if f.is_file()}
    au = AUDIT / f"{OUT.name}.json"
    if au.is_file():
        h[f"audit:{au.name}"] = sha(au)
    return {"rc": 0, "tail": "", "hashes": h}


def main() -> int:
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    WORK.mkdir(parents=True, exist_ok=True)
    AUDIT.mkdir(parents=True, exist_ok=True)
    if not list(RAW.glob("g*.jsonl")):
        print(f"[gen] 采 {games} 场 → {RAW}")
        RAW.mkdir(parents=True, exist_ok=True)
        cmd = [str(TRAINER), "selfplay", str(games), "--workers", "4", "--rotate", "--rotate-perm",
               "--aux", "--policy", ",".join([STU, *OPP]), "--seed", "90001",
               "--out", str(RAW), "--sample", "1"]
        p = subprocess.run(cmd, cwd=str(REPO), env=env_for("new"))
        if p.returncode != 0:
            print("⛔ 采集失败"); return 1
        p = subprocess.run([str(TRAINER), "features", str(RAW), "--workers", "4"],
                           cwd=str(REPO), env=env_for("new"))
        if p.returncode != 0:
            print("⛔ sidecar 失败"); return 1
    print(f"[gen] 轨迹 {len(list(RAW.glob('g*.jsonl')))} 个 / sidecar "
          f"{len(list(RAW.glob('g*.feat.bin')))} 个")
    bad = 0
    rows = []
    for name, extra in CONFIGS:
        hs = build("shadow", extra)
        hn = build("new", extra)
        if hs["rc"] != 0 or hn["rc"] != 0:
            rows.append((name, f"shadow rc={hs['rc']} / new rc={hn['rc']}",
                         "⛔" if hs["rc"] == 0 or hn["rc"] != 0 else "（新轴：影子包不认识 ⇒ 预期）"))
            # 新轴在影子包里必然失败（未登记的轴）—— 那不是"逐位不变"要覆盖的配置
            if name.startswith("dama"):
                print(f"\n== {name} ==\n  shadow rc={hs['rc']}: {hs['tail'].strip().splitlines()[-1][:120] if hs['tail'] else ''}"
                      f"\n  new    rc={hn['rc']}: {hn['tail'].strip().splitlines()[-1][:120] if hn['tail'] else ''}")
                if hs["rc"] == 0 or hn["rc"] != 0:
                    bad += 1
                continue
        same = hs["hashes"] == hn["hashes"]
        diff = sorted(set(hs["hashes"]) ^ set(hn["hashes"])) + \
            sorted(k for k in set(hs["hashes"]) & set(hn["hashes"])
                   if hs["hashes"][k] != hn["hashes"][k])
        rows.append((name, f"{len(hs['hashes'])} 个产物", "✓ 逐位相同" if same else f"✗ 差异 {diff}"))
        if not same:
            bad += 1
        print(f"\n== {name} ==\n  shadow {len(hs['hashes'])} 个产物 / new {len(hn['hashes'])} 个"
              f" ⇒ {'**逐位相同** ✓' if same else '**不一致** ' + str(diff)}")
        for k in sorted(hs["hashes"]):
            print(f"    {k:<28} {hs['hashes'][k][:16]}")
    print("\n==== 汇总 ====")
    for a, b, c in rows:
        print(f"  {a:<26} {b:<22} {c}")
    print(f"\n结论：{'PASS（改前/改后逐位相同；新轴只在改后可用）' if bad == 0 else f'FAIL（{bad} 处）'}")
    (WORK / "invariance.json").write_text(json.dumps(
        {"rows": rows, "bad": bad, "games": games, "raw": str(RAW)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
