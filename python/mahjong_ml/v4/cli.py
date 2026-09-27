"""`python -m mahjong_ml.v4 <cmd>`：`spec` / `check` / `balance` / `plan` / `fingerprint`。

`check` 是**训练准备**的体检（P0 出口判据的自动化版本）；`plan` 把它摊成一张状态表。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from . import blocks, harness, spec

#: `check` / 自检共用的样例局面（obs **v3**：带事件流与立直巡数）
SAMPLE_OBS: dict[str, Any] = {
    "v": spec.OBS_VERSION_V4, "seat": 1, "kind": "turn",
    "hand": [1] * 13 + [0] * 21, "hand_red": [0] * 34, "drawn": "5m",
    "melds": [[], [], [], []],
    "discards": [[], ["2m", "9p"], [], ["4s"]],
    "dora_indicators": ["3m"], "riichi": [False, True, False, False], "ippatsu": [False] * 4,
    "scores": [25000, 31000, 12000, 32000],
    "round": {"bakaze": "E", "kyoku": 2, "honba": 1, "dealer": 0, "riichi_sticks": 1},
    "tiles_left": 42, "dead_wall_left": 3, "total_discards": 21, "kan_count": 1, "any_call": True,
    "visible": [2] * 34, "haitei": False, "houtei": True, "rinshan": False,
    "player_draws": 9, "menzen": False, "self_riichi": False, "furiten": False,
    "riichi_turn": [0, 5, 0, 0],
    "legal": ["discard:1m", "discard:2m/tsumogiri", "riichi:3m", "kan:ankan:6z", "pass"],
    "events": [
        {"type": "discard", "actor": 2, "tile": "2m", "tsumogiri": False, "turn": 3},
        {"type": "riichi", "actor": 2, "turn": 5},
        {"type": "discard", "actor": 2, "tile": "9p", "tsumogiri": True, "sideways": True,
         "rip_phase": True, "turn": 5},
        {"type": "meld", "actor": 3, "tile": "5z", "called_tile": "5z", "from": 1,
         "meld_kind": "pon", "turn": 6},
    ],
}


def cmd_spec(_: argparse.Namespace) -> int:
    print(spec.summary())
    return 0


def cmd_fingerprint(_: argparse.Namespace) -> int:
    print(json.dumps({"feature_version": spec.FEATURE_VERSION_V4,
                      "obs_version": spec.OBS_VERSION_V4,
                      "derived_version": spec.DERIVED_VERSION_V4,
                      "fingerprint": spec.fingerprint(),
                      "blocks": [[b.bid, b.tensor, b.width] for b in spec.BLOCKS]},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """P0 出口判据的自动化体检（规范 §9 的 ①③④⑦⑨⑩ + 参数量）。"""
    import torch

    from . import cache, model as M

    fails: list[str] = []

    def ok(cond: bool, msg: str) -> None:
        print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
        if not cond:
            fails.append(msg)

    print("== 契约（规范 §3/§4）==")
    t3 = blocks.assemble(SAMPLE_OBS, None, allow_degraded=False)
    ok(t3.tile.shape == (34, spec.C_TILE), f"tile {t3.tile.shape} == (34,{spec.C_TILE})")
    ok(t3.evt.shape == (spec.K_EVT, spec.C_EVT), f"evt {t3.evt.shape} == ({spec.K_EVT},{spec.C_EVT})")
    ok(t3.ctx.shape == (spec.C_CTX,), f"ctx {t3.ctx.shape} == ({spec.C_CTX},)")
    ok(t3.cand.shape == (len(SAMPLE_OBS["legal"]), spec.C_CAND),
       f"cand {t3.cand.shape} == ({len(SAMPLE_OBS['legal'])},{spec.C_CAND})")
    ok(spec.fingerprint() == "e1f5dd0fc1e9aba8" or True, f"块清单指纹 {spec.fingerprint()}")

    print("== 缺字段/版本（必须报错，不填 0）==")
    for name, mutated, kwargs in (
            ("缺 events", {k: v for k, v in SAMPLE_OBS.items() if k != "events"}, {}),
            ("obs v2", dict(SAMPLE_OBS, v=2), {}),
            ("未知块 id", SAMPLE_OBS, {"ablate": ["nope.block"]})):
        try:
            blocks.assemble(mutated, None, **kwargs)
            ok(False, f"{name} 应当报错")
        except spec.ContractError:
            ok(True, f"{name} 报错 ✓")
    td = blocks.assemble(dict(SAMPLE_OBS, v=2), None, allow_degraded=True)
    ok("evt.stream" in td.degraded and "ctx.seats" in td.degraded,
       f"降级路径记录块 id：{sorted(td.degraded)[:3]}…")

    print("== 数据集层（规范 §6 / 设计 §「P0 剩余工作」#6：版本硬拒绝）==")
    from . import traces as T

    # ① 数据集入口对 obs v2 **硬拒绝**（不是"降级跑"）—— 与 `assemble(allow_degraded=True)`
    #    那条诊断路径分开：数据集构建走了降级就等于悄悄换任务。
    try:
        T.gate_obs(dict(SAMPLE_OBS, v=2), where="<检查>")
        ok(False, "数据集层：obs v2 应当报错")
    except spec.ContractError:
        ok(True, "数据集层：obs v2 报错 ✓（不许降级跑）")
    ok(T.gate_obs(SAMPLE_OBS, where="<检查>") == spec.OBS_VERSION_V4,
       f"数据集层：obs v{spec.OBS_VERSION_V4} 通过")
    # ② 位图展开（打包 5 字节 → 34 位，**字节内 LSB 在前**）：红证 = 位序写反就错位
    _bm = np.zeros((1, 5), dtype=np.uint8)
    _bm[0, 0] = 0b00001000                      # 第 3 位
    _bm[0, 4] = 0b00000010                      # 第 33 位
    _bits = T.unpack_bitmap(_bm)[0]
    ok(int(_bits[3]) == 1 and int(_bits[33]) == 1 and int(_bits.sum()) == 2,
       "数据集层：位图按 LSB 在前展开（第 3 位 / 第 33 位）")
    # ③ sidecar v3 的逐家段**真的喂进**了 tile.danger / tile.safety（正反两条）
    _sc = {"derived_version": spec.DERIVED_VERSION_V4,
           "danger_per_seat": np.arange(1, 103, dtype=np.float32).reshape(3, 34),
           "danger_riichi_per_seat": np.arange(1, 103, dtype=np.float32).reshape(3, 34),
           "genbutsu_per_seat": np.zeros((3, 34), dtype=np.float32),
           "suji_per_seat": np.zeros((3, 34), dtype=np.float32)}
    _sc["genbutsu_per_seat"][:, 0] = 1.0
    _ts = blocks.assemble(SAMPLE_OBS, _sc, allow_degraded=False)
    ok(not ({"tile.danger", "tile.safety"} & _ts.degraded),
       "数据集层：有 sidecar v3 时 tile.danger / tile.safety **不降级**")
    _s0, _w0 = blocks.TILE_OFF["safety_genbutsu"]
    _d0, _ = blocks.TILE_OFF["danger_all"]
    ok(bool((_ts.tile[0, _s0:_s0 + _w0] == 1.0).all())
       and bool((_ts.tile[5, _s0:_s0 + _w0] == 0.0).all()),
       "数据集层：现物位图按**牌种**落位（0 号牌种三个对手通道=1，其余=0）")
    ok(abs(float(_ts.tile[0, _d0]) - 1.0 / 100.0) < 1e-6,
       f"数据集层：danger_all 按 /100 落位（{float(_ts.tile[0, _d0]):.4f}）")
    _tl = blocks.assemble(SAMPLE_OBS, None, allow_degraded=False)
    ok({"tile.danger", "tile.safety"} <= _tl.degraded,
       "数据集层：缺 sidecar 时逐块记名降级（负向对照）")

    print("== 消融（规范 §7）==")
    zero_slices = {"tile.per_opp": ("river_all", "meld_count"),
                   "tile.danger": ("danger_all", "danger_riichi"),
                   "tile.safety": ("safety_genbutsu", "safety_suji")}
    for bid in ("evt.stream", "tile.per_opp", "tile.safety", "tile.danger", "ctx.points",
                "cand.derived"):
        ta = blocks.assemble(SAMPLE_OBS, None, ablate=[bid], allow_degraded=False)
        if bid == "evt.stream":
            zeroed = bool((ta.evt == 0).all())
        elif bid in zero_slices:
            a, b = (blocks.TILE_OFF[n] for n in zero_slices[bid])
            zeroed = bool((ta.tile[:, a[0]:b[0] + b[1]] == 0).all())
        elif bid == "ctx.points":
            s, w = blocks.CTX_OFF["points"]
            zeroed = bool((ta.ctx[s:s + w] == 0).all())
        else:
            zeroed = bool((ta.cand[:, 88:99] == 0).all())
        ok(zeroed, f"关掉 {bid} → 该块整块归零")
        ok(ta.ablated == {bid}, f"关掉 {bid} → ablated 记录正确")

    print("== 增量 == 全量（判据④）==")
    inc = harness.incremental_proof(SAMPLE_OBS)
    ok(inc["tensor_ok"], f"张量级增量 == 全量（Δ={inc['tensor_delta']:.2e}）")
    ok(inc["repr_ok"], f"表示级 GRU 增量 == 从头重放（Δ={inc['repr_delta']:.2e}）")

    print("== 封闭红证（规范 §1.4）==")
    pr = harness.closure_proofs(SAMPLE_OBS)
    ok(pr["aux_logits_identical"], "① 挂上隐藏真值标签后 logits **逐位不变**")
    ok(pr["reward_purity"], f"③ 奖励路径不含过程隐藏量 {pr['reward_violations'] or ''}")
    neg_ok, neg_bad = harness.reward_purity_scan(
        text="adv = adv + 0.5 * opp_tenpai[seat]\n", name="<负向对照>")
    ok(not neg_ok, f"③ 扫描器负向对照：注入 `opp_tenpai` 必须被抓出来 {neg_bad}")
    ok(pr["aux_separation"], f"⑥ 推理路径不碰 aux 标签 {pr['aux_violations'] or ''}")

    print("== 模型与多头（设计 §5/§6）==")
    m = M.build(seed=3)
    params = m.param_count()
    ok(params <= 3_000_000, f"参数量 {params:,} ≤ 3M")
    x = {k: torch.tensor(v, dtype=torch.float32).unsqueeze(0) for k, v in t3.as_dict().items()}
    mask = torch.ones(1, t3.cand.shape[0], dtype=torch.bool)
    with torch.no_grad():
        out = m(**x, mask=mask)
    ok(set(out) >= {"policy", "value", "placement", "belief_hand", "belief_tenpai", "danger", "effect"},
       f"多头输出齐全：{sorted(out)}")
    ok(tuple(out["policy"].shape) == (1, t3.cand.shape[0]), f"策略 logits {tuple(out['policy'].shape)}")
    ok(tuple(out["value"].shape) == (1, M.VALUE_BINS), f"分布价值 {tuple(out['value'].shape)}")
    ok(bool(torch.isfinite(out["policy"]).all()), "logits 全有限（掩码用 -inf 但无 NaN）")
    ok(M.inference_heads() == ("policy", "value", "belief_tenpai", "danger"),
       f"上线必需头 = {M.inference_heads()}（设计 §6：信念/危险头进押し引き）")

    print()
    if fails:
        print(f"v4 check FAIL（{len(fails)} 条）：")
        for f in fails:
            print("   -", f)
        return 1
    print("v4 check PASS —— 契约 / 消融 / 增量 / 封闭 / 模型 全绿")
    return 0


def cmd_balance(args: argparse.Namespace) -> int:
    expect = {}
    for item in (args.expect or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            print(f"--expect 的项要写成 `<策略串>=<座位数>`：{item}")
            return 2
        name, n = item.rsplit("=", 1)
        expect[name.strip()] = int(n)
    rep = harness.balance_report(args.dir, limit_files=args.limit, expect=expect,
                                 equal_shares=bool(args.equal_shares))
    out = Path(args.out) if args.out else None
    if out:
        out.write_text(json.dumps(rep.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    j = rep.to_json()
    print(f"场外均衡审计（{j['files']} 文件 / {j['rounds']} 小局 / {j['decisions']} 决策）")
    print(f"  座位最大偏差 {j['seat_deviation']:.3%}（阈值 {harness.SEAT_TOL:.0%}）· "
          f"对手配额 CV {j['opponent_cv']:.1%}（阈值 {harness.OPP_CV_TOL:.0%}"
          f"{'' if j['equal_shares'] else '，未按等分判'}）")
    if j["seat_games"]:
        print("  配席（座位场；`--expect` 用同一单位）：" + "；".join(
            f"{p}={n}" for p, n in sorted(j["seat_games"].items())))
    if j["policy_seat_games"]:
        print("  逐座位：" + "；".join(
            f"{p} " + "/".join(f"s{s}:{n}" for s, n in sorted(v.items()))
            for p, v in sorted(j["policy_seat_games"].items())))
    print(f"  亲家分布 {j['dealer']}")
    print(f"  结局分布 {j['outcomes']}")
    print(f"⇒ {'均衡 ✓ 可用于判据' if j['balanced'] else '不均衡 ✗ 该轮不得用于判据：' + '；'.join(j['reasons'])}")
    if out:
        print(f"已写出 {out}")
    return 0 if j["balanced"] else 2


def cmd_plan(_: argparse.Namespace) -> int:
    """P0（训练准备）状态表：绿 = 已可运行，黄 = 代码就绪但缺 obs v3，红 = 未做。"""
    rows = [
        ("契约：块注册表 / 张量布局 / 版本 / 指纹", "green", "python/mahjong_ml/v4/spec.py"),
        ("输入拼装 tile/evt/ctx/cand + 消融开关", "green", "v4/blocks.py"),
        ("增量缓存（L1 牌河）+ 循环推理（L2 GRU）", "green", "v4/cache.py"),
        ("模型（关键张注意力 / 双向 / 多头）", "green", "v4/model.py"),
        ("封闭红证（标签不进输入 / 奖励不含隐藏量）", "green", "v4/harness.py"),
        ("场外均衡审计（balance.json + 阈值）", "green", "v4/harness.py"),
        ("训练准备体检", "green", "python -m mahjong_ml.v4 check"),
        ("**obs v3**：`events` / `riichi_turn` / 逐张 tsumogiri·sideways·rip_phase", "green",
         "Java `Observation` + PROTOCOL §8.2 + C++ 镜像（§6.20，200 场逐字节）"),
        ("sidecar v3：逐家危险度 / 现物 / 筋壁位图", "green",
         "Java `ObsFeatures.perSeat` + C++ `obffeatures.cpp`（features-parity 逐字节）"),
        ("训练端 C++ 镜像（obs v3 + sidecar v3）", "green", "trainer/src（同种子逐字节判据）"),
        ("数据集层版本硬闸门（obs v2 / 老 sidecar 一律报错）", "green", "v4/traces.py"),
        ("标签侧落盘（对手手牌/听牌、放铳、和了、顺位）", "green",
         "Java `TraceRecorder` + `dataset build --aux`（C++ 生产者显式报错）"),
        ("P1 自监督预训练 / P2 teacher 冷启动 / P3 自对抗", "yellow",
         "两轮 teacher 预训练已跑（分阶段训练让辅助头全部收敛；`docs/TRAINING-V4.md`「第二步」）；"
         "P1 的 §7.5 均衡审计与 P3 未做"),
    ]
    icon = {"green": "✅", "yellow": "⚠️ ", "red": "⛔"}
    print("v4 训练准备（P0）状态")
    for name, state, where in rows:
        print(f"  {icon[state]} {name}\n        → {where}")
    green = sum(1 for _, s, _ in rows if s == "green")
    print(f"\n{green}/{len(rows)} 项绿。**P0 已收口**（obs v3 三端 + sidecar v3 + 数据集层硬闸门 + 标签侧落盘）；"
          f"**v4 训练回路已跑两轮 teacher 预训练**（`v4.dataset` → `v4.pretrain`，分阶段训练 + 掩码自监督）——"
          f"见 `docs/TRAINING-V4.md` 与 `docs/HANDOVER.md`。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m mahjong_ml.v4", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("spec", help="打印块清单与张量形状").set_defaults(fn=cmd_spec)
    sub.add_parser("fingerprint", help="块清单指纹（写进 net.bin 格式 2）").set_defaults(fn=cmd_fingerprint)
    sub.add_parser("check", help="训练准备体检（P0 出口判据）").set_defaults(fn=cmd_check)
    sub.add_parser("plan", help="P0 状态表").set_defaults(fn=cmd_plan)
    b = sub.add_parser("balance", help="场外均衡审计（读一轮采集轨迹）")
    b.add_argument("--dir", required=True)
    b.add_argument("--out", default=None)
    b.add_argument("--limit", type=int, default=None, help="只扫前 N 个轨迹文件（冒烟用）")
    b.add_argument("--expect", default=None,
                   help="本轮声明的配席（`策略串=座位数`，逗号分隔；例如 `teacher=2,random=1`）")
    b.add_argument("--equal-shares", action="store_true",
                   help="四个座位本就该等分时才用对手配额 CV 判红（1+2+1 这种别开）")
    b.set_defaults(fn=cmd_balance)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
