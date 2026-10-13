# -*- coding: utf-8 -*-
# [数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""`fold` 轴：**授权重采之后**的两条自证（本任务要求 ①②）。

① `_check_pairing`：逐场 `seed` / 学生席 不一致必须 == 0（与链内 `g01` 配）；
② **拿冻结的 POST json 与新轨迹 `_verify_pair_totals` 逐项对账**（手级/场级合计必须相等）
   —— 冻结的是 `style-vector-per-gen/v4-wl-fold-c1-g01-POST.json` 在重采前的备份
   （重采会把它覆盖 ⇒ 先备份，见 `_fold-verify/frozen-*/`）。

用法：`python _fold-recollect-verify.py <frozen_sv_json> [<tag>]`
⛔ 只读；不采集、不训练、不删轨迹（删轨迹由调用方在核对完之后做）。
"""
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(r"C:\Users\HP\source\games\mahjong")
STAGE = Path(r"S:\mahjong-training\w-ladder")
TRACES = STAGE / "traces"
TRACES_POST = STAGE / "traces-post"
SV = STAGE / "style-vector-per-gen"


def load_wl():
    spec = importlib.util.spec_from_file_location("wl", REPO / "tools" / "w-ladder.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wl"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    frozen_path = Path(sys.argv[1])
    tag = sys.argv[2] if len(sys.argv) > 2 else "v4-wl-fold-c1-g01"
    pre_dir, post_dir = TRACES / tag, TRACES_POST / tag
    wl = load_wl()

    print("== 重采自证 ==")
    print(f"[冻结基准] {frozen_path}")
    pair = wl._check_pairing(pre_dir, post_dir)
    print(f"[① `_check_pairing`] 共同场 {pair['games']}"
          f"｜seed 不一致 {pair['seed_mismatch']}"
          f"｜**学生席不一致 {pair['student_seat_mismatch']}**"
          f"｜seed_base pre={pair['seed_base_pre']} post={pair['seed_base_post']}"
          f"｜ok={pair['ok']}")
    if not pair["ok"]:
        print("⛔ 配对被破坏 ⇒ 停止")
        return 1

    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    fresh = json.loads((SV / f"{tag}-POST.json").read_text(encoding="utf-8"))

    # ② 新轨迹的逐场账 == **冻结 json** 的 counts（逐项；对不上 `_verify_pair_totals` 自己会 raise）
    per_post = wl._student_per_game(post_dir)
    print(f"[② `_student_per_game` 重读新轨迹] {len(per_post)} 场")
    tot = wl._verify_pair_totals(per_post, frozen, f"{tag}-POST(冻结基准)")
    print(f"[② `_verify_pair_totals(新轨迹, **冻结** json)] ✓ 逐项相等，合计 = "
          f"{json.dumps(tot, ensure_ascii=False)}")

    # 另行：冻结 vs 新采的 style-vector json 逐键比（手级/场级/逐场）
    fp, fq = frozen["policies"], fresh["policies"]
    bad = []
    for name in sorted(set(fp) | set(fq)):
        a, b = fp.get(name), fq.get(name)
        if a is None or b is None:
            bad.append(f"策略行缺失：{name}（frozen={a is not None} fresh={b is not None}）")
            continue
        if a.get("counts") != b.get("counts"):
            for k in sorted(set(a.get("counts") or {}) | set(b.get("counts") or {})):
                x, y = (a.get("counts") or {}).get(k), (b.get("counts") or {}).get(k)
                if x != y:
                    bad.append(f"{name}.counts[{k}]：冻结 {x} vs 新采 {y}")
        if a.get("dims") != b.get("dims"):
            bad.append(f"{name}.dims 不一致：冻结 {a.get('dims')} vs 新采 {b.get('dims')}")
    fa, fb = frozen["runs"][0]["summary"], fresh["runs"][0]["summary"]
    if fa.get("per_game") != fb.get("per_game"):
        d = [f"场 {g.get('game')}" for g, h in zip(fa.get("per_game") or [], fb.get("per_game") or [])
             if g != h]
        bad.append(f"逐场 `per_game` 不一致：{len(d)} 场（前几个 {d[:5]}）")
    # ⚠ 三个**计时**字段本来就不该相等（同一份数据跑两次的墙钟/吞吐）—— 单列出来，不当差异：
    #   `seconds` / `games_per_second` / `decisions_per_second`。
    TIMING = {"seconds", "games_per_second", "decisions_per_second"}
    timing = []
    for k in sorted(set(fa) | set(fb)):
        if k == "per_game":
            continue
        if fa.get(k) != fb.get(k):
            (timing if k in TIMING else bad).append(
                f"summary[{k}]：冻结 {fa.get(k)} vs 新采 {fb.get(k)}")
    print(f"[③ 冻结 json vs 新采 json 逐键对拍] "
          f"{'✓ 玩法数据逐位相同（手级 counts / dims / 场级 summary / 逐场 per_game 1000 场）' if not bad else '✗ 有差异'}")
    for x in timing:
        print("   ○ 仅计时（非玩法，预期不同）：" + x)
    for x in bad:
        print("   ✗ " + x)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
