# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`fold` 轴的**硬判据 ①**：与"改前版影子包"对拍（`.npy` + meta + 审计账 sha256 全同）。

影子包 = **加这根轴之前**的整份 `mahjong_ml`（`S:\\mahjong-training\\w-ladder\\_fold-shadow\\mahjong_ml`，
在本轮任何改动之前复制）。判据覆盖 **缺省 / riichi+hand / meld+decision / dama+decision / riichi_turn+decision**
五种配置的产物 sha256 **全同**，外加第 6 种（**新轴 `fold`**）—— 它在影子包里**必然失败**（未登记的轴），
在改后版必须成功：那一条不是"逐位不变"要覆盖的配置，而是"新轴**只在改后可用**"的正向证据。

⚠ 纪律（上一轮踩过的假绿点）：`-m` 会把 cwd 放进 `sys.path` ⇒ 全程 `PYTHONSAFEPATH=1`，
且 PYTHONPATH **显式**给出要用的那一份（影子跑**不含**仓库 `python`，新跑只含仓库 `python`）；
两次跑**同一个 out 目录**（跑完取哈希、删掉、再跑另一版）⇒ 产物路径逐字节可比。

用法：python _fold-invariance.py [games] [只跑名字含该子串的配置]
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
SHADOW = DATA / "w-ladder" / "_fold-shadow"
WORK = DATA / "w-ladder" / "_fold-inv"
RAW = DATA / "raw" / "_fold-cal-g01"
OUT = DATA / "compact" / "_fold-inv-out"
AUDIT = WORK / "audit"
VENV = REPO / "python" / ".venv" / "Lib" / "site-packages"
STU = f"net:{REPO / 'tools' / 'build' / 'v4-expert-defrep-g10' / 'net.bin'}@0#0.5"

CONFIGS = [
    ("缺省（无 --style-bonus）", []),
    ("riichi + hand", ["--style-bonus", "riichi=1000:no_deal", "--style-bonus-whiten", "student"]),
    ("meld + decision", ["--style-bonus", "meld=1000:no_deal", "--style-bonus-whiten", "student",
                         "--style-bonus-mode", "decision"]),
    ("dama + decision", ["--style-bonus", "dama=1000:no_deal", "--style-bonus-whiten", "student",
                         "--style-bonus-mode", "decision", "--val-frac", "0.5"]),
    ("riichi_turn + decision", ["--style-bonus", "riichi_turn=1000:no_deal",
                                "--style-bonus-whiten", "student", "--style-bonus-mode", "decision"]),
    ("fold + decision（**新轴**：影子包必然不认识）",
     ["--style-bonus", "fold=1000:no_deal", "--style-bonus-whiten", "student",
      "--style-bonus-mode", "decision", "--val-frac", "0.5"]),
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
    cmd = [PY, "-B", "-m", "mahjong_ml.v4.dataset", "build", str(RAW), str(OUT),
           "--aux", "--student", STU, "--workers", "6", *extra]
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
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    only = sys.argv[2] if len(sys.argv) > 2 else ""
    WORK.mkdir(parents=True, exist_ok=True)
    AUDIT.mkdir(parents=True, exist_ok=True)
    n = len(list(RAW.glob("g*.jsonl")))
    print(f"[gen] 轨迹 {n} 个 / sidecar {len(list(RAW.glob('g*.feat.bin')))} 个")
    bad = 0
    rows = []
    for name, extra in CONFIGS:
        if only and only not in name:
            continue
        hs = build("shadow", extra)
        hn = build("new", extra)
        if hs["rc"] != 0 or hn["rc"] != 0:
            new_axis = name.startswith("fold")
            tail_s = (hs["tail"].strip().splitlines() or [""])[-1][:130]
            tail_n = (hn["tail"].strip().splitlines() or [""])[-1][:130]
            good = new_axis and hs["rc"] != 0 and hn["rc"] == 0
            print(f"\n== {name} ==\n  shadow rc={hs['rc']}: {tail_s}\n  new    rc={hn['rc']}: {tail_n}"
                  f"\n  ⇒ {'✓ 预期（新轴只在改后可用）' if good else '✗ 非预期'}")
            rows.append((name, f"shadow rc={hs['rc']} / new rc={hn['rc']}",
                         "✓ 预期（新轴只在改后可用）" if good else "✗ 非预期"))
            if not good:
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
              f" ⇒ {'**逐位相同** ✓' if same else '**不一致** ' + str(diff)}", flush=True)
    print("\n==== 汇总 ====")
    for a, b, c in rows:
        print(f"  {a:<52} {b:<24} {c}")
    print(f"\n结论：{'PASS（缺省/riichi/meld/dama/riichi_turn 逐位相同；新轴只在改后可用）' if bad == 0 else f'FAIL（{bad} 处）'}")
    (WORK / "invariance.json").write_text(json.dumps(
        {"rows": rows, "bad": bad, "games": games, "raw": str(RAW)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    if OUT.exists():                       # 不留大件（判据已落盘）
        shutil.rmtree(OUT, ignore_errors=True)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
