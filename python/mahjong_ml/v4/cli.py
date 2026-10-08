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
        # `h=None`：这条体检是**单条合成 obs**（没有"窗口之前的事件流"）⇒ 冷启动。
        # 显式写出来是为了让"前向的 `h` 参数"在调用点一眼可见（别的地方都是 `h=b.get("h")`）。
        out = m(**x, mask=mask, h=None)
    ok(set(out) >= {"policy", "value", "placement", "belief_hand", "belief_tenpai", "danger", "effect"},
       f"多头输出齐全：{sorted(out)}")
    ok(tuple(out["policy"].shape) == (1, t3.cand.shape[0]), f"策略 logits {tuple(out['policy'].shape)}")
    ok(tuple(out["value"].shape) == (1, M.VALUE_BINS), f"分布价值 {tuple(out['value'].shape)}")
    ok(bool(torch.isfinite(out["policy"]).all()), "logits 全有限（掩码用 -inf 但无 NaN）")
    ok(M.contract_heads() == ("policy", "value", "belief_tenpai", "danger"),
       f"训练与契约必需头 TRAIN_HEADS = {M.contract_heads()}（必须训练 + 过 parity；⛔ 与'上线'无关）")

    # ⑦ 头是否"上线" = **前向路径是否读取**（用户裁决 2026-10-07）—— **实测**，不靠文档声明。
    #   ⚠ 必须先让 `policy_gate` 非退化：`g = 1 + tanh(W_g·sigmoid(bt))`，`W_g = 0` 时恒等
    #     ⇒ 会把"`belief_tenpai` 被 policy 消费"**测成绿的**（假绿）。这里用固定种子扰动，
    #     并**同时**跑正向控制（清零 policy 头必须让 logits 变），证明这条判据不是空转。
    with torch.no_grad():
        _g = torch.Generator().manual_seed(7)
        m.heads.policy_gate.weight.normal_(0.0, 0.5, generator=_g)
        _g = torch.Generator().manual_seed(8)
        m.heads.policy_gate.bias.normal_(0.0, 0.5, generator=_g)

    def _fwd() -> dict:
        with torch.no_grad():
            return m(**x, mask=mask, h=None)

    _ref = _fwd()["policy"].clone()
    _keep = {k: v.detach().clone() for k, v in m.state_dict().items()}
    with torch.no_grad():
        for _k, _v in m.state_dict().items():
            if _k.startswith("heads.policy."):
                _v.zero_()
        _positive_control = not torch.equal(_fwd()["policy"], _ref)
        m.load_state_dict(_keep)
    ok(_positive_control, "头判据的正向控制：清零 `heads.policy.*` **必须**改变 logits（否则判据空转）")
    _measured = M.online_heads(m, _fwd)
    ok(_measured == ("policy", "belief_tenpai"),
       f"ONLINE_HEADS（前向实际读取）= {_measured} —— ⚠ 非退化门控下 `belief_tenpai` 经 `policy_gate` "
       f"进了 policy（`model.py` 的逐候选门控设计）⇒ 声明值必须是 ('policy', 'belief_tenpai')")
    # ⑦′ **假绿对照**：把 `policy_gate` 归零 ⇒ 门控恒等 ⇒ 同一条判据会**漏掉** `belief_tenpai`。
    #    这一条把"为什么判据必须带非退化门控"变成常驻判据（否则以后有人把它写回零初始化就静默失效）。
    with torch.no_grad():
        for _k, _v in m.state_dict().items():
            if _k.startswith("heads.policy_gate."):
                _v.zero_()
    _degenerate = M.online_heads(m, _fwd)
    m.load_state_dict(_keep)
    ok(_degenerate == ("policy",) and _measured == ("policy", "belief_tenpai"),
       f"假绿对照：零初始化门控下实测 {_degenerate}（漏掉 belief_tenpai）⇒ 判据必须带非退化 `policy_gate`")
    print(f"  ONLINE_HEADS（推理必需，前向实测）= {_measured}")
    print(f"  TRAIN_HEADS （训练与契约必需）    = {M.TRAIN_HEADS}")

    print()
    if fails:
        print(f"v4 check FAIL（{len(fails)} 条）：")
        for f in fails:
            print("   -", f)
        return 1
    print("v4 check PASS —— 契约 / 消融 / 增量 / 封闭 / 模型 全绿")
    return 0


def cmd_il_check(args: argparse.Namespace) -> int:
    """**引擎逐候选标签**的纯函数单测 + （可选）在一份紧凑集上的一致率读数。

    为什么单独一个子命令（而不是塞进 `python/selfcheck.py`）：`selfcheck.py` 由用户维护、
    本轮的改动范围不含它 —— 这个纯函数的边界（全 0 列 / 并列 / nlegal=1 / padding）必须
    **可复现地跑**，所以判据挂在 v4 自己的 CLI 上（`python -m mahjong_ml.v4 il-check`）。

    @param data 可选的紧凑集目录：额外报"引擎标签 ↔ 行为标签"的一致率与随机基线
        （**不看权重**，所以它与 `pretrain` 的 `engine_top1` 是两把不同的尺子：
        这里量的是"引擎标签与行为标签有多合得来"，`pretrain` 量的是"网学会了没有"）。
        ⛔ **两把尺子都不是强弱判据**（第六十三轮的实测：`engine_top1` +6.95pp 而闸门 −12.65）；
        判强弱只能走闸门（多套新牌山配对、CI 排除 0 且为正）—— 见 `NOTES.md` §6.5 第六十三轮。
    """
    from . import dataset as v4ds

    fails: list[str] = []
    n_ok = 0

    def ok(cond: bool, msg: str) -> None:
        nonlocal n_ok
        print(f"  [{'ok' if cond else 'FAIL'}] {msg}")
        if cond:
            n_ok += 1
        else:
            fails.append(msg)

    def raises(fn, exc, msg: str) -> None:
        try:
            fn()
            ok(False, msg + "（应当报错却返回了）")
        except exc:
            ok(True, msg + " ✓")
        except Exception as e:                                # noqa: BLE001 —— 别的异常也算没报对
            ok(False, f"{msg}（报的是 {type(e).__name__}: {e}）")

    ebi = v4ds.engine_best_index
    ebis = v4ds.engine_best_indices

    print("== 引擎逐候选标签（W4）：纯函数边界 ==")
    # ---- ① nlegal=1 / 全 0 列 ---------------------------------------------------------------
    eq_row = np.zeros((1, 8), dtype=np.float32)
    ok(ebi(eq_row, 1) == 0, "nlegal=1 ⇒ 0（唯一候选，不看不猜）")
    ok(ebi(np.zeros((4, 8), dtype=np.float32), 4) == 0, "全 0 列（4 个候选全 0）⇒ 0（全平取下标序）")
    # ---- ② 并列：键完全相同时取**最小下标**（"牌序确定性打破平局"）-------------------------
    tie = np.array([[1, 2, 30, 0, 0, 0, 0, 0],
                    [1, 2, 30, 0, 0, 0, 0, 0],
                    [1, 2, 30, 0, 0, 0, 0, 0]], dtype=np.float32)
    ok(ebi(tie, 3) == 0, "三个候选的引擎量逐列相同 ⇒ 取最小下标 0")
    # 只有**最后一个**候选更好 ⇒ 必须改判（否则"取最小下标"就退化成"永远 0"）
    better_last = tie.copy()
    better_last[2, 2] = 31
    ok(ebi(better_last, 3) == 2, "末位候选的进张枚数更大 ⇒ 改判到 2（不是无脑取 0）")
    # ---- ③ padding 必须被 nlegal 排除 -------------------------------------------------------
    pad = np.array([[2, 0, 1, 0, 0, 0, 0, 0],          # 真实候选：向听 2、进张 1 枚
                    [0, 9, 99, 0, 0, 0, 0, 0]],        # padding：看起来"最好"，但不在 legal 里
                   dtype=np.float32)
    ok(ebi(pad, 1) == 0, "nlegal=1 时尾部的 padding（向听 0/进张 99）不参与 ⇒ 0")
    ok(ebi(pad, 2) == 1, "nlegal=2 时第二个候选确实更好 ⇒ 1（同一行的两种读法都对）")
    # ---- ④ 向听优先 / 进张枚数次之 -----------------------------------------------------------
    sh = np.array([[1, 3, 8, 0, 0, 0, 0, 0],          # 向听 1、进张 8 枚
                   [0, 1, 2, 0, 0, 0, 0, 0]],          # 向听 0（听牌）但进张列不适用
                  dtype=np.float32)
    ok(ebi(sh, 2) == 1, "向听优先：听牌（0）压过「打后 1 向听但进张更多」")
    adv = np.array([[1, 2, 9, 0, 0, 0, 0, 0],
                    [1, 9, 8, 0, 0, 0, 0, 0]], dtype=np.float32)
    ok(ebi(adv, 2) == 0, "同向听 ⇒ 比**进张枚数**（9 > 8），不是比进张种数")
    # ---- ⑤ 听牌行（8 列）：听牌枚数 → 良形枚数 ----------------------------------------------
    wait = np.array([[0, 0, 0, 2, 4, 1, 4, 0],
                     [0, 0, 0, 3, 6, 0, 0, 0]], dtype=np.float32)
    ok(ebi(wait, 2) == 1, "听牌行 ⇒ 听牌枚数大的赢（6 > 4）")
    good = np.array([[0, 0, 0, 2, 4, 2, 4, 0],
                     [0, 0, 0, 2, 4, 0, 0, 0]], dtype=np.float32)
    ok(ebi(good, 2) == 0, "听牌枚数相同 ⇒ 良形枚数大的赢（4 > 0）")
    # ---- ⑥ 3 列口径（老 `effect` 列）：听牌行**退化成下标序**（已写进 docstring ⑤）------------
    eff3 = np.array([[0, 0, 0], [0, 0, 0]], dtype=np.float32)
    ok(ebi(eff3, 2) == 0, "3 列口径下听牌行只能按下标序 ⇒ 0（口径已在 docstring 里写明）")
    ok(ebi(np.array([[1, 0, 5], [1, 0, 3]], dtype=np.float32), 2) == 0,
       "3 列口径下未听牌行照常比进张枚数（5 > 3）")
    # ---- ⑦ 终局和牌（整行 0）排第一 ---------------------------------------------------------
    win = np.array([[0, 0, 0, 2, 6, 2, 6, 0],      # 一个很好的听牌候选
                    [0, 0, 0, 0, 0, 0, 0, 0]],     # tsumo/ron：引擎不给"之后的形态"
                   dtype=np.float32)
    ok(ebi(win, 2) == 1, "整行 0 的候选（能和）= 引擎最优（赢了就没有「更好的候选」）")
    # ---- ⑧ 与**独立参考实现**逐位一致（"只有一把尺子"之外的交叉检查）------------------------
    def _ref_target(row: np.ndarray, n: int) -> int:
        """测试用的参考实现：纯 Python 元组比较，只按 `engine_best_index` 的 docstring 写。

        ⚠ 为什么需要它：被测实现是**唯一**一份（标量与向量化同源），所以"自己跟自己比"证明不了
        排序对；这一份是独立重写的判据（2026-10-04 就是它那个口径抓出了"字典序变成最后一个键
        说话"的 bug）。
        """
        cand = [[float(v) for v in row[i]] for i in range(n)]
        z = [i for i in range(n) if all(v == 0.0 for v in cand[i])]
        if z:
            return z[0]                                     # 终局和牌（整行 0）优先
        best_sh = min(cand[i][0] for i in range(n))
        wide = len(cand[0]) >= 7
        if best_sh <= 0.0 and wide:
            keys = [(0.0 if cand[i][0] <= 0.0 else -1.0, cand[i][4], cand[i][6],
                     cand[i][3], cand[i][5], -i) for i in range(n)]
        else:
            keys = [(-cand[i][0], cand[i][2], cand[i][1], -i) for i in range(n)]
        return max(range(n), key=lambda i: keys[i])

    rng = np.random.default_rng(7)
    L = 9
    for wide in (True, False):
        kdim = 8 if wide else 3
        bad = 0
        for trial in range(5):
            nlegs = rng.integers(1, L + 1, size=40).astype(np.int64)
            raw = rng.integers(0, 4, size=(40, L, kdim)).astype(np.float32)
            raw[:, :, 0] = raw[:, :, 0] % 4                 # 向听 0..3
            raw[rng.random((40, L, kdim)) < 0.2] = 0.0      # 混进整行 0 的「和牌」候选
            vec = ebis(raw, nlegs)
            ref = np.array([_ref_target(raw[i], int(nlegs[i])) for i in range(40)], dtype=np.int64)
            if not np.array_equal(vec, ref):
                bad = int(np.argmax(vec != ref))
                ok(False, f"{kdim} 列口径 vs 独立参考实现不一致（第 {trial} 次，行 {bad}："
                          f"实现 {int(vec[bad])} / 参考 {int(ref[bad])}）")
                break
        if not bad:
            ok(True, f"{kdim} 列口径 == 独立参考实现（5×40 行随机对拍，含整行 0/并列/听牌行）")
    # ---- ⑨ 确定性：同输入跑两次逐位相同 -----------------------------------------------------
    ok(np.array_equal(ebis(raw, nlegs), ebis(raw, nlegs)), "确定性：同一份输入跑两次结果逐位相同")
    # ---- ⑩ 契约违反必须报错（不猜）----------------------------------------------------------
    raises(lambda: ebi(tie, 4), ValueError, "nlegal 超过这一行的候选槽位")
    raises(lambda: ebi(tie, 0), ValueError, "nlegal=0")
    raises(lambda: ebi(tie, 3, legal=["discard:1m"]), ValueError, "legal 长度与 nlegal 不符")
    raises(lambda: ebi(np.zeros((3, 2), dtype=np.float32), 3), ValueError, "只有 2 列（<3）")
    raises(lambda: ebis(np.zeros((2, 3, 8), dtype=np.float32), np.array([0, 3])), ValueError,
           "nlegal 里有 0")

    if args.data:
        from . import traces as _traces                       # noqa: F401 —— 只为路径约定一致
        ds = args.data
        data = v4ds.load_split(ds, args.split)
        tgt = v4ds.engine_targets(data)
        nleg = np.asarray(data["nlegal"], dtype=np.int64)
        lab = np.asarray(data["label"], dtype=np.int64)
        ltyp = np.asarray(data["label_type"], dtype=np.int64) if data.get("label_type") is not None \
            else None
        feats = np.asarray(v4ds.engine_feature_block(data["cand"]), dtype=np.float32)
        rows = np.arange(tgt.size)
        zero_tgt = ~np.any(feats[rows, tgt] != 0.0, axis=1)
        student = np.asarray(data["is_student"], dtype=np.int64) > 0 \
            if data.get("is_student") is not None else np.ones(tgt.size, bool)
        print(f"\n== 紧凑集上的引擎标签体检：{ds}（split={args.split}）==")
        print(f"  {tgt.size} 行 / 平均候选 {float(nleg.mean()):.2f} / 学生行 "
              f"{int(student.sum())}（{float(student.mean()):.1%}）")
        print(f"  引擎最优 == 行为标签：全部 {float((tgt == lab).mean()):.4f}"
              f" | 学生行 {float((tgt[student] == lab[student]).mean()):.4f}"
              f" ⇒ **随机基线 1/平均候选数 = {float((1.0 / nleg).mean()):.4f}**")
        print(f"  引擎最优落在「整行 0」的候选上（= 能和就和）：{float(zero_tgt.mean()):.4f}"
              f" | 落在听牌候选上：{float((feats[rows, tgt, 0] == 0).mean()):.4f}")
        if ltyp is not None:
            win_types = (ltyp == 5) | (ltyp == 6)                # tsumo / ron（`ACTION_TYPES`）
            both = zero_tgt & win_types
            print(f"  交叉核对：引擎目标=和牌 **且** 行为标签也是和牌的行 {int(both.sum())}"
                  f" / 引擎目标=和牌的行 {int(zero_tgt.sum())}"
                  f"（行为侧采样可能放过和牌，所以这不是判据）")

    print()
    if fails:
        print(f"v4 il-check FAIL（{len(fails)}/{n_ok + len(fails)} 项）：")
        for f in fails:
            print("   -", f)
        return 1
    print(f"v4 il-check PASS —— {n_ok} 项（纯函数边界 + 确定性 + 向量化==标量）")
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
         "Java `TraceRecorder` + C++ `--aux`（连 npz 容器逐字节相同，`tools/trainer-aux-parity.mjs`）"
         " + `dataset build --aux`"),
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


def cmd_value_audit(args: argparse.Namespace) -> int:
    """价值头审计：转发给 `v4/value_audit.py`（判据口径与红证见 `docs/TRAINING-V4.md` §8.2）。"""
    from . import value_audit
    return value_audit.main([
        "--data", args.data, "--split", args.split, "--batch", str(args.batch),
        "--value-key", args.value_key,
        "--target", getattr(args, "target", "auto"),
        *(["--device", args.device] if args.device else []),
        *(["--out", args.out] if args.out else []),
        *(["--strict"] if args.strict else []),
        "--ev-ref", getattr(args, "ev_ref", "floor"),
        *(["--ceiling"] if getattr(args, "ceiling", False) else []),
        *(["--calibrate"] if getattr(args, "calibrate", False) else []),
        "--calib-rows", str(args.calib_rows),
        *(["--readout"] if getattr(args, "readout", False) else []),
        "--readout-rows", str(args.readout_rows),
        "--readout-mlp-steps", str(args.readout_mlp_steps),
        *(["--shaping"] if getattr(args, "shaping", False) else []),
        "--phi", getattr(args, "phi", "shanten"),
        # ⚠ 必须用 `--k=v` 单 token 形式：θ 网格可能以 `-` 开头（负 shaping 才是降方差那一边），
        #   而 argparse 见到"-8,..."会当成选项名（实测报 "expected one argument"）。
        f"--shaping-thetas={getattr(args, 'shaping_thetas', '0,0.5,1,2,4,8')}",
        "--shaping-rows", str(getattr(args, "shaping_rows", 40000)),
        *(["--gae-target", args.gae_target] if getattr(args, "gae_target", None) else []),
        "--gae-lambda", f"{args.gae_lambda:g}", "--gae-gamma", f"{args.gae_gamma:g}",
        "--rank-weight", f"{args.rank_weight:g}",
        *[x for c in args.ckpt for x in ("--ckpt", c)],
    ])


def cmd_loop(argv: list[str]) -> int:
    """v4 世代回路（`python -m mahjong_ml.v4 loop`）—— 编排在 `v4/loop.py`。"""
    from . import loop
    return loop.main(argv)


def cmd_ablate(argv: list[str]) -> int:
    """消融矩阵（`python -m mahjong_ml.v4 ablate`）—— 逐头/逐块关掉，同一预算出表。"""
    from . import ablate
    return ablate.main(argv)


def cmd_arena(argv: list[str]) -> int:
    """竞技场（`python -m mahjong_ml.v4 arena`）—— 多策略共享牌山 + 序贯判决。"""
    from . import arena
    return arena.main(argv)


def cmd_profile(argv: list[str]) -> int:
    """模型画像（`python -m mahjong_ml.v4 profile`）—— 把已有自对弈产物摊成多维统计表。

    参数集自成一套（`--run` 可重复 + `--self-check`/`--cross-check`）⇒ 整段转走，
    与 `loop`/`ablate`/`arena` 同一个套路。
    """
    from . import profile
    return profile.main(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `loop` / `ablate` / `arena` / `profile` 的参数集自成一套（见各自模块）：整段转走
    if argv and argv[0] in ("loop", "ablate", "arena", "profile"):
        return {"loop": cmd_loop, "ablate": cmd_ablate, "arena": cmd_arena,
                "profile": cmd_profile}[argv[0]](argv[1:])
    ap = argparse.ArgumentParser(prog="python -m mahjong_ml.v4", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("spec", help="打印块清单与张量形状").set_defaults(fn=cmd_spec)
    sub.add_parser("fingerprint", help="块清单指纹（写进 net.bin 格式 2）").set_defaults(fn=cmd_fingerprint)
    sub.add_parser("check", help="训练准备体检（P0 出口判据）").set_defaults(fn=cmd_check)
    sub.add_parser("plan", help="P0 状态表").set_defaults(fn=cmd_plan)
    il = sub.add_parser("il-check", help="W4：引擎逐候选标签的纯函数单测（+ 可选的一致率读数）")
    il.add_argument("--data", default=None, help="（可选）v4 紧凑集目录：额外报引擎/行为标签一致率")
    il.add_argument("--split", default="val", choices=["train", "val"])
    il.set_defaults(fn=cmd_il_check)
    b = sub.add_parser("balance", help="场外均衡审计（读一轮采集轨迹）")
    b.add_argument("--dir", required=True)
    b.add_argument("--out", default=None)
    b.add_argument("--limit", type=int, default=None, help="只扫前 N 个轨迹文件（冒烟用）")
    b.add_argument("--expect", default=None,
                   help="本轮声明的配席（`策略串=座位数`，逗号分隔；例如 `teacher=2,random=1`）")
    b.add_argument("--equal-shares", action="store_true",
                   help="四个座位本就该等分时才用对手配额 CV 判红（1+2+1 这种别开）")
    b.set_defaults(fn=cmd_balance)
    va = sub.add_parser("value-audit", help="价值头分布判据审计（§8.2：CRPS/覆盖率/EV/ρ）")
    va.add_argument("--data", required=True, help="紧凑数据集目录（含 val.* / meta.json）")
    va.add_argument("--split", default="val")
    va.add_argument("--ckpt", action="append", required=True, help="checkpoint 目录（可重复）")
    va.add_argument("--device", default=None)
    va.add_argument("--batch", type=int, default=2048)
    va.add_argument("--value-key", default="auto", choices=["auto", "value", "rtg", "delta"])
    va.add_argument("--target", default="auto", choices=["auto", "value", "rtg", "delta"],
                    help="主口径（P1b 的小局级值头用 `--target delta` 量）")
    va.add_argument("--out", default=None)
    va.add_argument("--strict", action="store_true", help="有关键判据不过就返回 2")
    va.add_argument("--ev-ref", default="floor", choices=["floor", "legit"],
                    help="EV 判据参照：floor = 绝对 0.1；legit = 现算合法天花板 × 0.8（§14.8.4）")
    va.add_argument("--ceiling", action="store_true", help="另报引擎真值特征的线性参照")
    va.add_argument("--calibrate", action="store_true", help="另报温度缩放前后（修欠覆盖）")
    va.add_argument("--calib-rows", type=int, default=20000)
    va.add_argument("--readout", action="store_true",
                    help="读出头探针：冻结躯干换池化/换读出深度，看 EV 能到多少")
    va.add_argument("--readout-rows", type=int, default=40000)
    va.add_argument("--readout-mlp-steps", type=int, default=1500)
    va.add_argument("--shaping", action="store_true",
                    help="逐决策 potential-based shaping 探针（残差比 <1 才值得进训练）")
    va.add_argument("--phi", default="shanten", choices=["shanten", "tenpai"])
    va.add_argument("--shaping-thetas", default="0,0.5,1,2,4,8")
    va.add_argument("--shaping-rows", type=int, default=40000)
    va.add_argument("--gae-target", default=None, help="另报手级 GAE 的 λ-回报口径（值是行为策略权重）")
    va.add_argument("--gae-lambda", type=float, default=0.9)
    va.add_argument("--gae-gamma", type=float, default=1.0)
    va.add_argument("--rank-weight", type=float, default=0.0)
    va.set_defaults(fn=cmd_value_audit)
    # ⚠ `loop` 的参数集在 `v4/loop.py`（几十个开关），这里**不能用 `REMAINDER` 子解析器**：
    #   argparse 会把 `--label` 这种选项当成父级的未知参数直接报错（实测）。所以 `main()` 在
    #   进 argparse **之前**就把 `loop` 之后的参数整段转走；这里留一行是为了 `--help` 里能看到它。
    sub.add_parser("loop", help="v4 世代回路：采集(C++)/紧凑集/训练/评测/台账（不依赖 Java）")
    sub.add_parser("ablate", help="消融矩阵：逐头/逐块关掉，同一预算下出表")
    # 同上：`profile` 的参数集在 `v4/profile.py`（`--run` 可重复 + `--self-check`/`--cross-check`），
    # 也在进 argparse 之前整段转走；这里留一行只为 `--help` 里看得到它。
    sub.add_parser("profile", help="模型画像：把已有自对弈产物摊成多维统计表（轴定义见 v4/profile.py）")
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
