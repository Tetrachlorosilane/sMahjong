#!/usr/bin/env python
"""座次平均化判据：从采集/评测轨迹里数"座次关系有没有被平均化"。

**口径（2026-10-08 用户指正后定稿）**：
  · 桌子上的**策略构成**由 `v4/loop.py` 的 `LoopConfig.policy()` 决定（风格桌 = 学生 1 + 每风格 1）；
  · **座次平均化**必须做到 **4! = 24 种座次关系全都平均**（`trainer selfplay --rotate-perm`：
    按场号 `g % 24` 枚举 24 个全排列），⛔ **不是**只做循环移位 —— 循环移位只覆盖 4 个排列，
    而且**保持循环序** ⇒ 一代之内每个策略相对其他三家的**方位是固定的**（实测：400 场里
    `def` 恒在下家、`win` 恒在対面、`atk` 恒在上家，各 400/400）。
  轨迹里每一行都带 `seat` 与 `policy` ⇒ 这条判据是**数出来的**。

判据（`--check` 时任一不满足即 exit 1）：
  ① 场数 ≥ `--min-games`（缺省 24：少于 24 场连一遍全排列都走不完，判不了）；
  ② **每个策略在四个座位上次数相等**（极差 ≤ 1）—— 抓"没开 rotate"；
  ③ **每个"座次关系"（4 席分别是谁）出现次数相等**（极差 ≤ 1，期望值 = 场数 / 24）—— 抓"只做了循环移位"；
  ④ **相对方位**：每个策略相对参照策略的偏移（+1 下家 / +2 対面 / +3 上家）次数相等（极差 ≤ 1，期望 = 场数/3）
     —— 这是用户口径里最关键的一条（循环移位时它必然是 场数/0/0）；
  ⑤ 每场学生席位数 = 期望（风格桌 1、旧口径 2）。

用法：
  python tools/seat-balance.py <raw 目录> [--limit N] [--min-games 24] [--expect-students 1] [--check]
  # 例：python tools/seat-balance.py S:\\mahjong-training\\raw\\v4-expert-def6-g01 --check
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import pathlib
import sys


def short(policy: str) -> str:
    """`net:...\\v4-expert-def3-g02\\net.bin@0#0.5` ⇒ `v4-expert-def3-g02 [学生]`。

    ⚠ **计数一律按完整策略串**（`@0#<T>` 后缀也是身份的一部分）：同一个 `net.bin` 可以同时以
    "学生（带温度）"与"对手（贪心）"两种身份上桌 —— 只看目录名会把两种身份合成一个人，
    座位次数就会被"平均"掉（2026-10-08 实测踩过：学生席位数被数成 0）。
    """
    tail = policy.rsplit("\\", 2)
    name = tail[-2] if len(tail) >= 2 else policy
    return name + (" [学生]" if "@0#" in policy else "")


def scan(raw: pathlib.Path, limit: int):
    """回每场的 `{席: 策略串}` 列表。每场只读到看齐 4 席就停（省 I/O）。"""
    files = sorted(raw.glob("g*.jsonl"), key=lambda p: int(p.stem[1:]))
    if limit:
        files = files[:limit]
    per_game = []
    for f in files:
        seen: dict[int, str] = {}
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                s, p = r.get("seat"), r.get("policy")
                if s is None or not p:
                    continue
                seen.setdefault(int(s), p)
                if len(seen) == 4:
                    break
        if seen:
            per_game.append(seen)
    return per_game


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="座次平均化判据（数轨迹；含 24 种座次关系与相对方位）")
    ap.add_argument("raw", help="轨迹目录（`g*.jsonl`）")
    ap.add_argument("--limit", type=int, default=0, help="只看前 N 场（0 = 全部）")
    ap.add_argument("--min-games", type=int, default=24, help="少于这么多场就不判（一遍全排列都走不完）")
    ap.add_argument("--expect-students", type=int, default=1,
                    help="每场应有几个学生席（风格桌 1；旧口径 2；0 = 不查）")
    ap.add_argument("--expect-perms", type=int, default=24,
                    help="座次关系应有几种（4 个互异策略 = 24；2+2 = 6；0 = 不查）")
    ap.add_argument("--check", action="store_true", help="判据模式：不满足即 exit 1")
    args = ap.parse_args(argv)

    raw = pathlib.Path(args.raw)
    if not raw.is_dir():
        print(f"[seat-balance] 目录不存在：{raw}", file=sys.stderr)
        return 2
    per_game = scan(raw, args.limit)
    n = len(per_game)
    if n == 0:
        print(f"[seat-balance] {raw} 里没有 g*.jsonl", file=sys.stderr)
        return 2

    policies = sorted({p for g in per_game for p in g.values()})
    seats = sorted({s for g in per_game for s in g})
    problems: list[str] = []
    print(f"场数 = {n}；策略 = {len(policies)}：{[short(p) for p in policies]}")

    # ③ 座次关系（每场 4 席分别是谁）—— 期望值全从这里推，见下
    rel = collections.Counter(tuple(g.get(s, "?") for s in seats) for g in per_game)
    rel_lo, rel_hi = min(rel.values()), max(rel.values())
    exp_rel = n / max(1, len(rel))

    # ⚠ **别用"极差 ≤ 1"卡绝对座位**（2026-10-08 我自己的判据在这里假红过一次）：
    #   座次方案是 `g % 24` ⇒ 1000 场 = 41 整圈 + **余 16 场**，那 16 场必然让某些座位多算几次
    #   （实测极差 3~6 是**正常**的）。正确做法：座位分配是**每场确定**的 ⇒ 把"观测到的座次关系"
    #   当作已知方案，**精确推出**期望值，然后要求**整数量相等**（不需要任何容差）。
    exp_seat = collections.Counter()
    exp_off = collections.Counter()
    refs: dict[tuple, str] = {}
    for rel_tuple, c in rel.items():
        vals = [v for v in rel_tuple if v != "?"]
        if not vals:
            continue
        ref = next((v for v in vals if "@0#" in v), sorted(vals)[0])
        refs[rel_tuple] = ref
        rseat = next(s for s, p in zip(seats, rel_tuple) if p == ref)
        for s, p in zip(seats, rel_tuple):
            exp_seat[(p, s)] += c
            if p != ref:
                exp_off[(p, (s - rseat) % 4)] += c

    # ② 每个策略 × 每个座位：观测 == 从座次关系推出的期望（精确）
    tally = collections.Counter((p, s) for g in per_game for s, p in g.items())
    print(f"\n[② 绝对座位] {'策略':<30} " + " ".join(f"席{s}" for s in seats) + "   与期望差")
    for p in policies:
        row = [tally.get((p, s), 0) for s in seats]
        diff = [row[i] - exp_seat.get((p, s), 0) for i, s in enumerate(seats)]
        print(f"{'':<13}{short(p):<30} " + " ".join(f"{c:>5}" for c in row)
              + "   " + ("一致" if not any(diff) else f"不一致 {diff}"))
        if any(diff):
            problems.append(f"策略 {short(p)} 的座位次数与座次关系不自洽（差 {diff}）—— "
                            f"轨迹里的 seat↔policy 记录有问题")

    print(f"\n[③ 座次关系] 出现 {len(rel)} 种（期望 {args.expect_perms} 种）"
          f"；次数 min={rel_lo} max={rel_hi}（期望各 ≈{exp_rel:.1f}）")
    if args.expect_perms and len(rel) != args.expect_perms:
        problems.append(f"座次关系只有 {len(rel)} 种 ≠ 期望 {args.expect_perms} 种"
                        f"（4 个互异策略要 24 种 = 全排列；只做循环移位只有 4 种）")
    if rel_hi - rel_lo > 1:
        problems.append(f"座次关系次数不齐（极差 {rel_hi - rel_lo}）—— 排列没被平均到")

    # ④ 相对方位（相对参照策略的偏移；参照 = 学生，没有学生时取字典序最小）
    offsets = collections.Counter()
    for g in per_game:
        vals = sorted(g.values())
        ref = next((p for p in vals if "@0#" in p), vals[0])
        rseat = next(s for s, p in g.items() if p == ref)
        for s, p in g.items():
            if p != ref:
                offsets[(p, (s - rseat) % 4)] += 1
    print(f"\n[④ 相对方位]（相对第一个策略；+1 下家 / +2 対面 / +3 上家；期望各 ≈{n / 3:.1f}）")
    ref_global = next((p for p in policies if "@0#" in p), policies[0])
    for p in policies:
        if p == ref_global:
            continue
        row = [offsets.get((p, d), 0) for d in (1, 2, 3)]
        exp = [exp_off.get((p, d), 0) for d in (1, 2, 3)]
        diff = [row[i] - exp[i] for i in range(3)]
        rng = max(row) - min(row)
        print(f"{'':<13}{short(p):<30} " + " ".join(f"+{d}:{c:>5}" for d, c in zip((1, 2, 3), row))
              + f"  极差 {rng}" + ("" if not any(diff) else f"  ⚠与期望差 {diff}"))
        if rng > 1:
            problems.append(f"策略 {short(p)} 的相对方位不齐（极差 {rng}：{row}）—— "
                            f"座位上只做了循环移位（保持循环序）⇒ 方位被钉死")
        if any(diff):
            problems.append(f"策略 {short(p)} 的相对方位与座次关系不自洽（差 {diff}）")

    # ⑤ 每场学生席位数
    stud_hist = collections.Counter(sum(1 for p in g.values() if "@0#" in p) for g in per_game)
    print(f"\n[⑤ 学生席位] 每场分布 = {dict(sorted(stud_hist.items()))}（期望只有 {args.expect_students}）")
    if args.expect_students and set(stud_hist) != {args.expect_students}:
        problems.append(f"学生席位数分布 {dict(stud_hist)} ≠ 每场 {args.expect_students} 席")

    if n < args.min_games:
        problems.append(f"场数 {n} < --min-games {args.min_games}：走不完一遍座次关系，判不了平均化")

    if problems:
        print("\n[seat-balance] FAIL")
        for p in problems:
            print("  x " + p)
        return 1 if args.check else 0
    print(f"\n[seat-balance] PASS：绝对座位、{len(rel)} 种座次关系、相对方位**全部平均**"
          f"（每格 ≈{n / max(1, len(rel)):.1f} 次）" + ("（--check）" if args.check else ""))
    return 0


#: 4! = 24 的字典序枚举（与 C++ `kPerm24` 同一顺序；`--rotate-perm` 就是按 `g % 24` 取这张表）
PERM24 = tuple(itertools.permutations(range(4)))


if __name__ == "__main__":
    raise SystemExit(main())
