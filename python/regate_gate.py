"""用**当前**的合并判据，在**已存闸门摘要**上重算历季判决（不跑新对局）。

为什么要有它：闸门每代把 `gate/<tag>/s<seed>/` 下的 1000 个逐场轨迹删掉、**只留 `summary.json`**
（判据只读它：`per_game[].rank_points/placement/final_scores/policies/seed`；实测复算整季 5 代
只花 7 秒、26 GB 的 jsonl 一个字节都没读）。于是"某季当时的判决到底对不对"可以在**任何时候**
用当天的判据重算一遍 —— 这正是第八季 g01 暴露出 `gate.py` 逐牌山打印符号相反时该做的事：
先拿已存数据复算，确认**判决链**没被污染（第七季 5 代逐位复现 −1.11/−1.38/−2.26/+0.79/−1.53），
再去改那行 printf。

用法：
    python regate_gate.py --label v4-league7 --incumbent v4-league-g08 --generations 5 \
        --seeds 20261001,20261002 [--build-root tools/build] [--gate-root <数据根>/gate]

**多 tag 池化（2026-10-06 加）**：一次端点判决常常被拆成**两次互不相交的采集**（各自的 tag 目录、
各自的牌山 seed），单看哪一次都是"分不出"。把它们的牌山**池化成一个合并判决**就走这个模式：

    python regate_gate.py --tags v4-expert-def-endpoint,v4-expert-def-confirm \
        --incumbent v4-league-g08 --candidate v4-expert-def3-g08 [--metric rank_points]

⚠ 它**没有第二份统计**：逐墙读 `summary.json` → `gate.pooled_diffs`（加键偏移 + 池化）→
`gate.decide`，与 `gate.gate()` / 按季复判走的是**同一对调用**（所以单 tag 时能与当时写下的
`verdict.json` 逐位对上）。合并合法性由**现成判据**守：`eval.pool_diffs` 自带
"同一副牌山不能重复计入"的守卫（牌山 seed 重叠就报错），本文件不另写一份。

方法、数字、红证与"能说与不能说"见 `NOTES.md` §12。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mahjong_ml import eval as E                         # noqa: E402
from mahjong_ml.v4 import arena as A, gate as G          # noqa: E402


def regrade(build_root: Path, gate_root: Path, label: str, incumbent: str, generations: int,
            seeds: list[int] | None) -> int:
    """`seeds=None` ⇒ **自动发现**该代实际用过的牌山目录（`s*`）。

    ⚠ 必须自动发现：**牌山套数在历史上变过**（第三季 3 套、第五季起 2 套）。硬编码 2 套会让
    复算结果与当时的判决**不一样**，而且看起来完全正常（实测：第三季 g01 真值 −0.27（3 套），
    只读 2 套得 −0.56）—— "复算对不上"会让人怀疑数据，其实是工具读少了。
    """
    inc = f"net:{build_root / incumbent / 'net.bin'}"
    bad = 0
    for g in range(1, generations + 1):
        tag = f"{label}-g{g:02d}"
        # ⚠ **不能**要求 `build/<tag>/net.bin` 还在：被拒的那几代原件可能只剩
        #   `_rejected/<tag>/net.bin` 的副本。而 `judge_series` 是按 **summary 里的策略串**
        #   匹配的（不是按文件）⇒ 只要标签串对得上就行，权重文件在不在都不影响复算。
        cand = f"net:{build_root / tag / 'net.bin'}"
        root = gate_root / tag
        walls = ([root / f"s{s}" for s in seeds] if seeds is not None
                 else sorted(p for p in root.glob("s*") if p.is_dir()))
        walls = [d for d in walls if (d / "summary.json").is_file()]
        if not walls:
            print(f"{tag}: 没有闸门摘要 —— 跳过")
            continue
        print(f"{tag}（{len(walls)} 套牌山：{[d.name for d in walls]}；权重在不在原位不影响复算）")
        series = []
        for d in walls:
            x, y = A.judge_series(d, inc, cand)
            # ⚠ 逐牌山与合并必须同一个符号：`judge_pair(sa, sb, a, b)` 的约定是 `Δ = a − b`
            part = A.judge_pair(y, x, cand, inc)              # 候选 − 现任
            print(f"    {d.name}: Δ={part.delta:+6.2f} CI[{part.lo:+6.2f},{part.hi:+6.2f}] "
                  f"n={part.games}")
            series.append((x, y))
        sa, sb = G.pooled_diffs(series)
        out = G.decide(sa, sb, inc, cand)
        vj = root / "verdict.json"
        was = ""
        if vj.is_file():
            import json
            old = json.loads(vj.read_text(encoding="utf-8"))
            was = (f"  [当时台账 Δ={old['delta']:+.2f} "
                   f"CI[{old['lo']:+.2f},{old['hi']:+.2f}] n={old['n']}"
                   f"{'  ✓ 一致' if abs(old['delta'] - out['delta']) < 1e-6 else '  ✗ 不一致！'}]")
        print(f"  == 复算合并：Δ={out['delta']:+6.2f} "
              f"CI[{out['lo']:+.2f},{out['hi']:+.2f}] n={out['n']} ⇒ "
              f"{'采纳' if out['adopt'] else '不采纳'}{was}")
    return 1 if bad else 0


def _as_policy(build_root: Path, spec: str) -> str:
    """把"tools/build 下的目录名"或"已经写好的策略串（`net:` / `teacher` …）"统一成策略串。

    ⚠ 必须与 `gate._policy` 同口径：摘要里存的策略标签就是 `net:<绝对路径>`，配对是按**字符串
    相等**过滤的（`eval.per_game_series`），差一个字符就一场都配不上（症状：n=0、看起来像"没数据"）。
    """
    s = spec.strip()
    if s.startswith(("net:", "teacher", "first", "pass", "random")):
        return s
    return f"net:{build_root / s / 'net.bin'}"


def _pooled_verdict(series: list, inc: str, cand: str, metric: str) -> dict:
    """把若干套牌山的逐场值池化后判决 —— **就是 `gate.gate()` 结尾那两行**（加键偏移 + 池化 → decide）。"""
    sa, sb = G.pooled_diffs(series)
    return G.decide(sa, sb, inc, cand, metric)


def regrade_tags(build_root: Path, gate_root: Path, tags: list[str], incumbent: str, candidate: str,
                 metric: str = "rank_points", out_json: Path | None = None) -> int:
    """把**多个 tag** 的牌山池化成一个合并判决（只读 `summary.json`，不碰 `*.jsonl`）。

    为什么需要这个模式：端点判决被拆成两次独立采集时（`…-endpoint` 8 套 + `…-confirm` 8 套），
    每一次的单次 CI 都跨 0（"分不出"），而**两次的点估计很接近** —— 这时唯一合法的增强就是
    **把两次的逐场配对差合并**（各套牌山的墙 seed 互不相同），再按同一套判据重算一次。
    ⛔ 不许把两次的 CI 取并集/取平均，也不许把同一套牌山喂两遍（后者会把 CI 假窄）。
    """
    inc = _as_policy(build_root, incumbent)
    cand = _as_policy(build_root, candidate)
    print(f"合并复判：{len(tags)} 个 tag；指标={metric}")
    print(f"  现任（−）= {inc}")
    print(f"  候选（+）= {cand}")

    walls: list[tuple[str, Path]] = []
    for tag in tags:
        root = gate_root / tag
        ws = sorted(d for d in root.glob("s*") if d.is_dir() and (d / "summary.json").is_file())
        if not ws:
            print(f"  ⚠ {tag}: 没有闸门摘要（{root}）—— 跳过")
            continue
        print(f"  {tag}：{len(ws)} 套牌山 {[d.name for d in ws]}")
        walls.append((tag, ws))
    flat = [(tag, d) for tag, ws in walls for d in ws]
    if not flat:
        print("⛔ 一套牌山都没有，无法判决")
        return 2

    # ── 判据①（目录级，人话版）：同一套牌山的**目录**不许喂两遍（`--tags a,a` 这种）
    seen: dict[str, str] = {}
    for tag, d in flat:
        key = str(d.resolve())
        if key in seen:
            print(f"⛔ {d} 被喂了两遍（{seen[key]} 与 {tag}）—— 合并会把同一副牌数两遍、CI 假窄")
            return 2
        seen[key] = tag

    # ── 判据②（逐场级，**复用现成守卫**）：`eval.pool_diffs` 自带"同一副牌山不能重复计入"——
    #    两次 run 只要有任何一场牌山 seed 相同就 raise。顺带拿到逐墙（run 级）读数与随机效应。
    try:
        runs = [E.load_run(d) for _, d in flat]              # ← 只读 summary.json
        # ⚠ 参数顺序 = **候选在前**：`eval.pool_diffs(a, b)` 的约定是 `a − b`（正 = a 更好）。
        #   写反不会报错、守卫也照样工作，只会让下面打印的 μ / 逐墙 SE **与 Δ 符号相反**
        #   （实测：同一份数据 Δ=+0.80 而 μ=−0.80）—— 这种"数字对、符号反"只有人眼看得出。
        #   采纳判据本身仍走 `gate.decide`（它自己管方向），这里只是把两边的口径对齐。
        pooled, per_run = E.pool_diffs(runs, cand, inc, metric)
    except ValueError as e:
        print(f"⛔ 合并被拒（`eval.pool_diffs` 的牌山重叠守卫）：{e}")
        return 2
    print(f"  牌山合法性：{len(flat)} 套墙 / {pooled.size} 场可配对，"
          f"跨墙重复的 per-game seed = 0（`eval.pool_diffs` 守卫放行）")

    # ── 判决口径 = gate（逐墙 judge_series → pooled_diffs → decide），与单季复判同一个函数
    series: list = []
    wall_deltas: list[float] = []
    per_tag: dict[str, list] = {}
    per_tag_deltas: dict[str, list[float]] = {}
    print("  逐牌山（Δ = 候选 − 现任）：")
    for tag, d in flat:
        x, y = A.judge_series(d, inc, cand, metric)
        part = A.judge_pair(y, x, cand, inc, metric)         # 候选在前 ⇒ 正 = 候选更好
        print(f"    {tag}/{d.name}: Δ={part.delta:+7.3f} "
              f"CI[{part.lo:+7.3f},{part.hi:+7.3f}] n={part.games}")
        wall_deltas.append(float(part.delta))
        series.append((x, y))
        per_tag.setdefault(tag, []).append((x, y))
        per_tag_deltas.setdefault(tag, []).append(float(part.delta))

    out = _pooled_verdict(series, inc, cand, metric)
    spread = (max(wall_deltas) - min(wall_deltas)) if len(wall_deltas) > 1 else 0.0
    half = (out["hi"] - out["lo"]) / 2
    ratio = (spread / half) if half > 0 else float("inf")
    pos = sum(1 for v in wall_deltas if v > 0)
    neg = sum(1 for v in wall_deltas if v < 0)
    print(f"  逐牌山符号：{pos} 套为正 / {neg} 套为负（共 {len(wall_deltas)} 套）")
    print(f"  套间散布：极差 {spread:.3f} / 合并 CI 半宽 {half:.3f} = {ratio:.2f}×"
          + ("  ⚠ 超过 1 ⇒ 判决是**条件于这几套牌山**的（gate.gate() 的原话）"
             if ratio > 1 else ""))
    print(f"== 合并判决：Δ={out['delta']:+.4f} CI[{out['lo']:+.4f},{out['hi']:+.4f}] "
          f"n={out['n']} p={out['p']:.4f} sd={out['sd']:.3f} "
          f"win/lose/tie={out['win']}/{out['lose']}/{out['tie']} ⇒ "
          f"{'采纳（CI 排除 0 且为正）' if out['adopt'] else '不采纳（CI 跨 0）'}")

    # ── 每个 tag 的**分项**：单 tag 时这一行就是上面的合并行，与当时写下的 verdict.json 逐位对账
    #    （这就是"合并工具没有改口径"的红证：同样的输入必须给同样的浮点数）
    for tag, series_t in per_tag.items():
        sub = _pooled_verdict(series_t, inc, cand, metric)
        print(f"  [{tag}] 分项（{len(series_t)} 套）：Δ={sub['delta']:+.4f} "
              f"CI[{sub['lo']:+.4f},{sub['hi']:+.4f}] n={sub['n']} p={sub['p']:.4f}")
        vj = gate_root / tag / "verdict.json"
        if not vj.is_file():
            print(f"    （{tag} 没有 verdict.json，没得对账）")
            continue
        old = json.loads(vj.read_text(encoding="utf-8"))
        same_d = old["delta"] == sub["delta"]
        same_ci = (old["lo"] == sub["lo"] and old["hi"] == sub["hi"])
        same_p = old["p"] == sub["p"]
        same_n = old["n"] == sub["n"]
        print(f"    [红证①] 与当时台账 verdict.json 逐位对账："
              f"Δ {'✓' if same_d else '✗'}（{old['delta']!r} vs {sub['delta']!r}）；"
              f"lo {'✓' if old['lo'] == sub['lo'] else '✗'}（{old['lo']!r} vs {sub['lo']!r}）；"
              f"hi {'✓' if old['hi'] == sub['hi'] else '✗'}；"
              f"p {'✓' if same_p else '✗'}；n {'✓' if same_n else '✗'}；"
              f"CI 两项都 {'逐位一致' if same_ci else '**不一致**'}")
        wd = old.get("wall_deltas") or []
        if wd:
            same_w = (len(wd) == len(per_tag_deltas[tag])
                      and all(a == b for a, b in zip(wd, per_tag_deltas[tag])))
            print(f"    [红证①] 逐牌山 Δ 列表（{len(wd)} 条）："
                  f"{'逐位一致 ✓' if same_w else '**不一致**'}")

    # ── 套间（run 级）随机效应：池化 CI 只含**套内**配对噪声；换一批牌山本身能摆动几个点
    re_ = E.random_effects(per_run)
    lo2, hi2 = E.bootstrap_ci(pooled)
    print(f"  逐场配对口径交叉核对（`eval.pool_diffs` 的同一份逐场差，只是排序不同）："
          f"mean={pooled.mean():+.4f} n={pooled.size} "
          f"CI[{lo2:+.4f},{hi2:+.4f}]"
          + ("  ✓ 与 gate.decide 的 Δ 一致（1e-9）"
             if abs(pooled.mean() - out["delta"]) < 1e-9 else "  ✗ 与 Δ 不一致！"))
    print(f"  （两口径 CI 的差 |Δlo|={abs(lo2 - out['lo']):.5f} / |Δhi|={abs(hi2 - out['hi']):.5f}"
          f" —— 自助法把固定 RNG 流按数组顺序映射到样本上，换个顺序就在第三位小数上抖，"
          f"不是口径分歧）")
    print(f"  套间随机效应（`eval.random_effects`，{len(per_run)} 套）："
          f"τ={re_['tau']:.3f} μ={re_['mu']:+.3f} "
          f"CI[{re_['ci'][0]:+.3f},{re_['ci'][1]:+.3f}] Q={re_['q']:.1f} df={re_['df']}")
    print("  读法：上面那行合并 CI 是**逐场配对**口径（`gate.decide`，采纳判据用的就是它）；"
          "随机效应那行把换牌山的不确定性也算进去，是更保守的读法。")

    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps({**out, "tags": list(tags), "metric": metric,
                                        "wall_deltas": wall_deltas, "wall_spread": spread,
                                        "wall_spread_over_ci": ratio,
                                        "walls_positive": pos, "walls_negative": neg,
                                        "random_effects": re_, "per_wall": per_run},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  （已写 {out_json}）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="在已存的闸门摘要上重算历季判决（不跑对局）")
    ap.add_argument("--label", default="", help="季标签（如 v4-league7）；与 --tags 二选一")
    ap.add_argument("--incumbent", required=True, help="现任目录名（如 v4-league-g08）")
    ap.add_argument("--generations", type=int, default=5)
    ap.add_argument("--seeds", default="",
                    help="逗号分隔的牌山 seed；**留空 = 自动发现**该代实际用过的 s* 目录"
                         "（牌山套数历史上变过：第三季 3 套、第五季起 2 套）")
    ap.add_argument("--tags", default="",
                    help="（2026-10-06 加）逗号分隔的闸门 tag：把它们的牌山**池化成一个合并判决**"
                         "（用于「同一次端点判决被拆成两次独立采集」的合并）。只读 summary.json")
    ap.add_argument("--candidate", default="",
                    help="--tags 模式必填：候选权重（tools/build 下的目录名，或 net:/路径）")
    ap.add_argument("--metric", default="rank_points", help="逐场指标（缺省 rank_points，与闸门一致）")
    ap.add_argument("--out-json", default="",
                    help="可选：把合并判决写到这个 json（缺省**不写任何文件**，保持只读）")
    ap.add_argument("--build-root", default=str(Path(__file__).resolve().parent.parent
                                                / "tools" / "build"))
    ap.add_argument("--gate-root", default=r"S:\mahjong-training\gate")
    a = ap.parse_args(argv)
    if a.tags:
        tags = [t.strip() for t in a.tags.split(",") if t.strip()]
        if not a.candidate:
            ap.error("--tags 模式必须给 --candidate（候选权重）")
        return regrade_tags(Path(a.build_root), Path(a.gate_root), tags, a.incumbent, a.candidate,
                            metric=a.metric,
                            out_json=(Path(a.out_json) if a.out_json else None))
    if not a.label:
        ap.error("要么给 --tags（多 tag 池化），要么给 --label（按季复判）")
    seeds = [int(s) for s in a.seeds.split(",") if s.strip()] or None
    return regrade(Path(a.build_root), Path(a.gate_root), a.label, a.incumbent, a.generations,
                   seeds)


if __name__ == "__main__":
    raise SystemExit(main())
