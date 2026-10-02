"""v4 权重导出 + golden 对拍夹具（P5：Java / C++ 手写 v4 前向的**唯一输入格式**）。

    python -m mahjong_ml.v4.export weights --ckpt <model.pt> --out <net.bin> [--label <名字>]
       训练好的 v4 checkpoint → 服务端/C++ 能直读的 **`net.bin` 格式 2**（小端 float32，
       **带块清单与张量表**）。Java `mahjong.ai.V4Policy` 与 C++ `trainer` 各自手写前向，
       服务端仍是零第三方依赖。

    python -m mahjong_ml.v4.export golden --trace <轨迹目录> --out <夹具> [--cases N]
       真实轨迹 → **golden 夹具**（magic `MJ4G`）：固定种子的**小** v4 + 若干真实决策的
       `tile/evt/ctx/cand`（Python 侧按 `blocks.assemble` 拼出来）+ 四个推理头的输出。
       Java 的 `SelfTest.v4ForwardTests` 与 C++ 的 `trainer v4net` 都读它，
       **逐元素**核对"特征拼装 + 前向"三端一致（容差 1e-4）。

## 二进制格式（`docs/FEATURES-V4.md` §6「net.bin 格式 2」逐字段）

⚠ **魔数与 v3 相同（`MJNN`），靠 `format` 区分**：v3 是 `format=1`（定长 MLP，无块清单），
v4 是 `format=2`。这样 v3 的 `NeuralPolicy` 见到 v4 权重会报**格式版本 2 != 本服务端 1**
（构造期拒绝），反之 v4 加载器见到 `format=1` 也会拒 —— 两侧都不会"尽力而为"。

    头部（64 B）：
      magic u32 "MJNN" · format u32 = 2 · featureVersion u32 = 4 · obsVersion u32 = 3
      · derivedVersion u32 = 3 · dModel u32 · tileD u32 · nHeads u32 · valueBins u32
      · nBlocks u32 · nTensors u32 · fingerprint 16 B（ASCII 的 16 个十六进制字符）
      · params u32
    块清单（nBlocks 条，按注册表顺序；**子集 = 消融**）：
      idLen u16 · id(utf8) · width u32
    张量表（nTensors 条，**名字升序**，名字 = PyTorch `state_dict()` 的键）：
      nameLen u16 · name(utf8) · ndim u8 · dims u32×ndim · 数据 f32（行主序、紧凑、无对齐）

    ⚠ 头部**不带**隐层宽度之外的结构信息：三塔 + 融合 + 多头这套**拓扑是固定的**，
    只有 `dModel / tileD / nHeads / valueBins` 四个宽度可变（与 v3 的 `hidden/head/trunk_layers`
    同一个思路）—— 这样 golden 夹具能用**小网络**（几十 KB，能进仓库），
    而真权重照跑（`dModel=192 / tileD=64 / nHeads=4 / valueBins=51`）。

## golden 夹具布局

    magic u32 "MJ4G" · format u32 = 2 · nCases u32 · netLen u32 · net（上面那个格式 2）
    每个 case：obsLen u32 · obs(utf8 JSON) · nLegal u16 · (keyLen u16 · key)×n
      · **h0 f32[dModel]**（W1 起：**窗口之前**的 GRU carry，三端都拿它当递推初值）
      · tile f32[34×C_TILE] · evt f32[K_EVT×C_EVT] · ctx f32[C_CTX] · cand f32[n×C_CAND]
      · logits f32[n]（策略头，逐候选）· value f32[valueBins] · beliefTenpai f32[3]
      · danger f32[n×4] · **h_evt f32[dModel]**（窗口之后的 carry —— 融合门控的输入）

⚠ **格式 2 = 格式 1 + 每用例的 `h0`（输入）与 `h_evt`（期望输出）**（W1，第六十轮）：
`h_evt`（长程记忆）接进融合之后，"三端同值"必须**给定同一个 `h0`** 才可判据（否则 Java 的
整手 carry 与 Python 的窗口冷启动是两个量，见 `FEATURES-V4.md` §5.3）。`fusion.mem.*` 因此在
夹具里**显式随机化**（零初始化 ⇒ `g_mem ≡ 1` ⇒ 不扰动则这条路径三端都没被测到）。
⚠ 反过来，**别**拿"扰动 `h0` 就该让输出变"当判据：随机初始化的 GRU 是收缩的，
满窗上 `h0` 的影响只有 3e-8（实测）——"`h0` 路线"由 `SelfTest.v4CacheTests` 的
"前缀 carry + 窗口 == 整手重放"（**逐位**，无容差）钉住，而不是靠夹具的容差。

⚠ **夹具里的四张量是"训练侧输入"**（Python 按 `blocks.assemble(obs, sidecar_dict)` 拼的，
其中 `cand[88:96]` 与 `tile.danger/safety` 来自 sidecar）。Java/C++ 侧要**只用 obs**
（+ 自己的引擎派生量）拼出同一张量 —— 这正是"训练输入 == 推理输入"的判据：
对不上就是两套实现漂移，而不是"大概一样"。
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import torch

from .. import dataset as _dataset
from . import blocks, cache as _cache, model as M, spec, traces

#: 与 v3 同一个魔数（`mahjong_ml.export.WEIGHT_MAGIC`），用 `format` 区分代号。
NET_MAGIC = 0x4D4A4E4E          # "MJNN"
NET_FORMAT = 2                  # v4 = 格式 2（带块清单）
GOLDEN_MAGIC = 0x4D4A3447       # "MJ4G"
GOLDEN_FORMAT = 2

#: 格式 2 的头部字节数（9 个 u32 + 2 个 u32 + 16 B 指纹 + 1 个 u32 = 64）
HEADER_BYTES = 64

#: 夹具缺省用**小网络**（能进仓库；真权重是 192/64/4/51）
GOLDEN_DIMS: dict[str, int] = {"d_model": 32, "tile_d": 16, "n_heads": 2, "value_bins": 5}
#: 上线架构（`model.py` 的缺省值）
FULL_DIMS: dict[str, int] = {"d_model": M.D_MODEL, "tile_d": M.TILE_D,
                             "n_heads": M.N_HEADS, "value_bins": M.VALUE_BINS}

#: 每个头的输出宽度（推理头见 `model.inference_heads()`）
HEAD_WIDTHS = {"policy": 1, "placement": 4, "belief_hand": 3 * 34, "belief_tenpai": 3,
               "danger": 4, "effect": 3}


class NetFormatError(RuntimeError):
    """权重文件/夹具不合格 —— 一律**报错**，不猜、不降级。"""


# ------------------------------------------------------------------ 形状审计

def expected_shapes(d: Mapping[str, int]) -> dict[str, tuple[int, ...]]:
    """这套宽度下**应该**出现的每个张量名与形状（逐条核对，缺/多/形状不符都报错）。

    为什么要有这张表：`state_dict()` 是"训练代码的现状"，而推理端要的是"契约"。
    两者之间的任何漂移（改名、换层、少一个 bias）都必须在这里**当场**变成错误。
    """
    dm, td, vb = int(d["d_model"]), int(d["tile_d"]), int(d["value_bins"])
    k, ct, ce, cc = spec.K_EVT, spec.C_TILE, spec.C_EVT, spec.C_CAND
    out: dict[str, tuple[int, ...]] = {
        # 牌种塔
        "tile.enc.0.weight": (td, ct), "tile.enc.0.bias": (td,),
        "tile.enc.2.weight": (td, td), "tile.enc.2.bias": (td,),
        "tile.attn.in_proj_weight": (3 * td, td), "tile.attn.in_proj_bias": (3 * td,),
        "tile.attn.out_proj.weight": (td, td), "tile.attn.out_proj.bias": (td,),
        "tile.norm.weight": (td,), "tile.norm.bias": (td,),
        "tile.proj.weight": (dm, td), "tile.proj.bias": (dm,),
        # 事件塔（MLP + GRUCell + 1 层 TransformerEncoder）
        "event.enc.0.weight": (dm, ce), "event.enc.0.bias": (dm,),
        "event.enc.2.weight": (dm, dm), "event.enc.2.bias": (dm,),
        "event.cell.weight_ih": (3 * dm, dm), "event.cell.weight_hh": (3 * dm, dm),
        "event.cell.bias_ih": (3 * dm,), "event.cell.bias_hh": (3 * dm,),
        "event.tr.layers.0.self_attn.in_proj_weight": (3 * dm, dm),
        "event.tr.layers.0.self_attn.in_proj_bias": (3 * dm,),
        "event.tr.layers.0.self_attn.out_proj.weight": (dm, dm),
        "event.tr.layers.0.self_attn.out_proj.bias": (dm,),
        "event.tr.layers.0.linear1.weight": (2 * dm, dm), "event.tr.layers.0.linear1.bias": (2 * dm,),
        "event.tr.layers.0.linear2.weight": (dm, 2 * dm), "event.tr.layers.0.linear2.bias": (dm,),
        "event.tr.layers.0.norm1.weight": (dm,), "event.tr.layers.0.norm1.bias": (dm,),
        "event.tr.layers.0.norm2.weight": (dm,), "event.tr.layers.0.norm2.bias": (dm,),
        # 两个 MLP 编码器（注意 Mlp 末尾还有一个 ReLU，见 model.Mlp）
        "ctx.net.0.weight": (dm, spec.C_CTX), "ctx.net.0.bias": (dm,),
        "ctx.net.2.weight": (dm, dm), "ctx.net.2.bias": (dm,),
        "cand.net.0.weight": (dm, cc), "cand.net.0.bias": (dm,),
        "cand.net.2.weight": (dm, dm), "cand.net.2.bias": (dm,),
        # 融合
        "fusion.tile_proj.weight": (dm, td), "fusion.tile_proj.bias": (dm,),
        "fusion.q_tile.in_proj_weight": (3 * dm, dm), "fusion.q_tile.in_proj_bias": (3 * dm,),
        "fusion.q_tile.out_proj.weight": (dm, dm), "fusion.q_tile.out_proj.bias": (dm,),
        "fusion.q_evt.in_proj_weight": (3 * dm, dm), "fusion.q_evt.in_proj_bias": (3 * dm,),
        "fusion.q_evt.out_proj.weight": (dm, dm), "fusion.q_evt.out_proj.bias": (dm,),
        "fusion.gate.weight": (dm, 2 * dm), "fusion.gate.bias": (dm,),
        "fusion.write.weight": (dm, dm), "fusion.write.bias": (dm,),
        "fusion.film.weight": (dm, dm), "fusion.film.bias": (dm,),
        # 长程记忆门控（W1，第六十轮）：**可缺** —— 旧网没有它 ⇒ 补 0 ⇒ `g = 1+tanh(0) = 1`
        # ⇒ 恒等（与 `heads.policy_gate` 同一个兼容套路，见 `normalize_state`）。
        "fusion.mem.weight": (None, (dm, dm)),
        "fusion.mem.bias": (None, (dm,)),
        "fusion.out.net.0.weight": (dm, 3 * dm), "fusion.out.net.0.bias": (dm,),
        "fusion.out.net.2.weight": (dm, dm), "fusion.out.net.2.bias": (dm,),
        # 头
        # ⚠ **policy 的输入宽度 = `dm`**（第五十五轮把 belief 改成"逐候选门控"后恢复）；
        #   但**旧网**里它可能是 `dm+3`（第五十轮那版"拼接"）⇒ 加载期**截断**掉后 3 列：
        #   那 3 列乘的是逐行常数 ⇒ 对 argmax/softmax 无贡献（红证见 NOTES §6.5），丢掉等价。
        "heads.policy.weight": ((1, dm), (1, dm + 3)), "heads.policy.bias": (1,),
        # 逐候选门控：**可缺**（旧网没有它 ⇒ 补 0 ⇒ `g = 1 + tanh(0) = 1` ⇒ 恒等）
        "heads.policy_gate.weight": ((dm, 3),),
        "heads.policy_gate.bias": ((dm,),),
        "heads.value.weight": (vb, dm), "heads.value.bias": (vb,),
    }
    for name, width in HEAD_WIDTHS.items():
        if name == "policy":
            continue
        out[f"heads.{name}.weight"] = (width, dm)
        out[f"heads.{name}.bias"] = (width,)
    # 事件窗口长度只影响前向的循环次数，不在权重里；这里顺手记一下契约
    assert k > 0 and ct == 48 and ce == 96
    return out


def _check_shapes(sd: Mapping[str, torch.Tensor], d: Mapping[str, int], what: str) -> None:
    want = expected_shapes(d)
    got = {k: tuple(v.shape) for k, v in sd.items()}
    bad: list[str] = []
    for k, s in want.items():
        # ⚠ 期望形状支持两种写法（见 `expected_shapes` 的注释）：
        #   · 多个候选形状：`((1,dm), (1,dm+3))` —— `heads.policy.weight` 用（新旧两版都接受）；
        #   · **可缺**：候选里带 `None`，如 `(None, (dm,3))` —— 逐候选门控张量用
        #     （旧网没有它 ⇒ 补 0 ⇒ `g = 1+tanh(0) = 1` ⇒ 恒等，不影响任何旧行为）。
        # ⚠ 先分清"这是**一个形状**还是**一串候选**"：只有当元组的**每个元素都是元组或 None**
        #   时才算候选串（`(64,48)` 是一个形状，`((1,dm),(1,dm+3))` 是候选串，
        #   `(None,(dm,3))` 是"可缺 + 形状"）。搞混的后果是**所有**张量都报形状不符。
        if isinstance(s, tuple) and s and all(isinstance(a, tuple) or a is None for a in s):
            raw = s
        else:
            raw = (s,)
        alts = tuple(a for a in raw if isinstance(a, tuple))
        optional = any(a is None for a in raw)
        if k not in got:
            if not optional:
                bad.append(f"缺 {k}{alts[0] if alts else ''}")
        elif got[k] not in alts:
            bad.append(f"{k} 形状 {got[k]} != " + " 或 ".join(str(x) for x in alts))
    for k in got:
        if k not in want:
            bad.append(f"多 {k}{got[k]}")
    if bad:
        raise NetFormatError(f"{what}：张量表与 v4 契约不符（{len(bad)} 条）：" + "；".join(bad[:8]))


def dims_from_state(sd: Mapping[str, torch.Tensor]) -> dict[str, int]:
    """从权重形状反推四个宽度（`nHeads` 反推不出来 —— 只是分组方式，必须外部给）。"""
    # ⚠ `d_model` **从 `tile.proj.weight`（`[dm, td]`）反推**，别再从 policy 权重反推：
    #   第四十九轮起 policy 的输入是 `dm+3`（多拼了 3 个 belief 概率），而**旧网仍是 `dm` 宽**
    #   ⇒ 从它反推会把 d_model 读成 192 或 195 两种，取决于网的新旧。`tile.proj` 与新旧无关。
    dm = int(sd["tile.proj.weight"].shape[0])
    return {"d_model": dm, "tile_d": int(sd["tile.enc.0.weight"].shape[0]),
            "n_heads": M.N_HEADS, "value_bins": int(sd["heads.value.weight"].shape[0])}


# ------------------------------------------------------------------ 权重：torch → 格式 2

def _block_table(bl: tuple[spec.Block, ...] = spec.BLOCKS) -> list[tuple[str, int]]:
    return [(b.bid, int(b.width)) for b in bl]


def fingerprint_of(bl: tuple[spec.Block, ...] = spec.BLOCKS) -> str:
    """**这一份块清单**的指纹（子集消融时与注册表全量指纹不同，两者都要能算）。"""
    return spec.fingerprint(bl)


def net_blob(sd: Mapping[str, torch.Tensor], dims: Mapping[str, int], *,
             blocks_: tuple[spec.Block, ...] = spec.BLOCKS) -> bytes:
    """权重 state_dict → `net.bin` 格式 2（**确定性**：同输入两次导出逐字节相同）。"""
    d = {k: int(v) for k, v in dims.items()}
    _check_shapes(sd, d, "net_blob")
    bl = _block_table(blocks_)
    tensors = sorted(((k, v.detach().cpu().numpy().astype("<f4", copy=False))
                      for k, v in sd.items()), key=lambda kv: kv[0])
    params = int(sum(int(np.prod(t.shape)) for _, t in tensors))
    parts = [struct.pack("<9I", NET_MAGIC, NET_FORMAT, spec.FEATURE_VERSION_V4,
                         spec.OBS_VERSION_V4, spec.DERIVED_VERSION_V4,
                         d["d_model"], d["tile_d"], d["n_heads"], d["value_bins"]),
             struct.pack("<2I", len(bl), len(tensors)),
             fingerprint_of(blocks_).encode("ascii"), struct.pack("<I", params)]
    for bid, width in bl:
        b = bid.encode("utf-8")
        parts.append(struct.pack("<H", len(b)) + b + struct.pack("<I", width))
    for name, arr in tensors:
        nb = name.encode("utf-8")
        parts.append(struct.pack("<H", len(nb)) + nb + struct.pack("<B", arr.ndim))
        parts.append(b"".join(struct.pack("<I", int(s)) for s in arr.shape))
        parts.append(np.ascontiguousarray(arr).tobytes())
    return b"".join(parts)


def save_net(sd: Mapping[str, torch.Tensor], dims: Mapping[str, int], out: str | Path, *,
             label: str = "", source: str = "", blocks_: tuple[spec.Block, ...] = spec.BLOCKS,
             extra: Mapping[str, Any] | None = None) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {k: int(v) for k, v in dims.items()}
    blob = net_blob(sd, d, blocks_=blocks_)
    p.write_bytes(blob)
    meta: dict[str, Any] = {
        "kind": "v4", "format": NET_FORMAT, "bytes": len(blob),
        "feature_version": spec.FEATURE_VERSION_V4, "obs_version": spec.OBS_VERSION_V4,
        "derived_version": spec.DERIVED_VERSION_V4,
        "dims": d, "params": int(struct.unpack_from("<I", blob, 60)[0]),
        "blocks_fingerprint": fingerprint_of(blocks_),
        "registry_fingerprint": spec.fingerprint(),
        "blocks": [list(x) for x in _block_table(blocks_)],
        "inference_heads": list(M.inference_heads()),
        "label": label, "source": source,
        "tensors": {k: list(v) for k, v in sorted(expected_shapes(d).items())},
    }
    if extra:
        meta.update(dict(extra))
    # ⚠ 显式 `newline="\n"`：Windows 上 `Path.write_text` 默认把 `\n` 翻成 `\r\n`，
    #   而仓库的 `.gitattributes` 是 `* text=auto eol=lf` —— 让**工作区**直接是 LF，
    #   免得同一份文件出现"工作区 CRLF / 索引 LF"的假改动（v1.12.0 那次 round.hpp 就踩过）。
    p.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                      encoding="utf-8", newline="\n")
    return p


def read_net(path: str | Path) -> dict[str, Any]:
    """读 `net.bin`（格式 2）→ 头部 + 块清单 + 张量表（`{名字: np.ndarray}`）。"""
    raw = Path(path).read_bytes()
    if len(raw) < HEADER_BYTES:
        raise NetFormatError(f"权重文件太短（{len(raw)} B < {HEADER_BYTES} B）")
    h = struct.unpack_from("<9I", raw, 0)
    magic, fmt, feat, obs, derived, dm, td, nh, vb = h
    if magic != NET_MAGIC:
        raise NetFormatError(f"权重魔数不对：{magic:#x}（期望 {NET_MAGIC:#x}）")
    if fmt != NET_FORMAT:
        raise NetFormatError(f"权重格式版本 {fmt} != {NET_FORMAT}（v3 的格式 1 走 NeuralPolicy）")
    n_blocks, n_tensors = struct.unpack_from("<2I", raw, 36)
    fp = raw[44:60].decode("ascii", "replace")
    params = struct.unpack_from("<I", raw, 60)[0]
    off = HEADER_BYTES
    bl: list[tuple[str, int]] = []
    for _ in range(n_blocks):
        (ln,) = struct.unpack_from("<H", raw, off); off += 2
        bid = raw[off:off + ln].decode("utf-8"); off += ln
        (w,) = struct.unpack_from("<I", raw, off); off += 4
        bl.append((bid, w))
    tensors: dict[str, np.ndarray] = {}
    for _ in range(n_tensors):
        (ln,) = struct.unpack_from("<H", raw, off); off += 2
        name = raw[off:off + ln].decode("utf-8"); off += ln
        nd = raw[off]; off += 1
        shape = struct.unpack_from("<" + "I" * nd, raw, off); off += 4 * nd
        n = int(np.prod(shape)) if shape else 1
        arr = np.frombuffer(raw, dtype="<f4", count=n, offset=off).reshape(shape).copy()
        off += 4 * n
        tensors[name] = arr
    if off != len(raw):
        raise NetFormatError(f"权重文件尾部多了 {len(raw) - off} 字节（张量表与头部不符）")
    return {"magic": magic, "format": fmt, "feature_version": feat, "obs_version": obs,
            "derived_version": derived,
            "dims": {"d_model": dm, "tile_d": td, "n_heads": nh, "value_bins": vb},
            "blocks": bl, "fingerprint": fp, "params": params, "tensors": tensors,
            "bytes": len(raw)}


def normalize_state(sd: Mapping[str, torch.Tensor]) -> dict[str, torch.Tensor]:
    """把**任意一代**的状态字典归一成**当前模型**的形状 —— 所有加载点共用这一处。

    处理三件跨代差异（第五十/五十五/六十轮）：

    1. `heads.policy.weight` 可能是 `[1, dm]`（第一季及以前、第五十五轮起）或 `[1, dm+3]`
       （第五十轮那版"拼接 sigmoid(belief_tenpai)"）⇒ `dm+3` **截断**：那 3 列乘的是逐行常数
       ⇒ 对 argmax/softmax 无贡献（红证见 NOTES §6.5），丢掉等价；
    2. `heads.policy_gate.{weight,bias}`（第五十五轮加的逐候选门控）**可能不存在** ⇒ 补 **0**
       ⇒ `g = 1 + tanh(0) = 1` ⇒ 恒等（不给旧权重强加新行为，`strict=True` 也不会红）；
    3. `fusion.mem.{weight,bias}`（W1 加的长程记忆门控，第六十轮）同上：**可能不存在** ⇒ 补 **0**
       ⇒ `g_mem = 1 + tanh(0) = 1` ⇒ 恒等（旧网的前向**逐位不变**）。

    ⚠ 别把这段逻辑抄到各处：`state_from_net`（net.bin）与 `value_audit.load_model`（ckpt）
    都必须走它 —— 抄漏一处就是"某些旧权重能载入、某些不能"的鬼故事（实测踩过两次：
    `value_audit` 直接 `strict=True` 载 ckpt ⇒ `Missing key(s): heads.policy_gate.*`）。
    """
    out = dict(sd)
    dm = int(out["tile.proj.weight"].shape[0])
    w = out.get("heads.policy.weight")
    if w is not None and w.ndim == 2:
        if int(w.shape[1]) == dm + 3:
            out["heads.policy.weight"] = w[:, :dm].contiguous()
        elif int(w.shape[1]) != dm:
            raise NetFormatError(f"heads.policy.weight 宽度 {int(w.shape[1])} 既不是 {dm} "
                                 f"也不是旧版 {dm + 3}")
    dtype = out["tile.proj.weight"].dtype
    # 可缺张量（旧网没有）⇒ 补 0 即"恒等"：加新的可缺张量时**只需要动这张表**
    for name, shape in (("heads.policy_gate.weight", (dm, 3)), ("heads.policy_gate.bias", (dm,)),
                        ("fusion.mem.weight", (dm, dm)), ("fusion.mem.bias", (dm,))):
        if name not in out:
            out[name] = torch.zeros(shape, dtype=dtype)
    return out


def state_from_net(parsed: Mapping[str, Any]) -> dict[str, torch.Tensor]:
    """张量表 → `state_dict()`（自检用它做"导出 → 读回 → 前向"的闭环）。

    跨代差异（policy 宽度 / 可缺的门控张量）统一交给 `normalize_state`，见那里的注释。
    """
    return normalize_state({k: torch.from_numpy(np.ascontiguousarray(v))
                            for k, v in parsed["tensors"].items()})


# ------------------------------------------------------------------ 夹具

def _iter_cases(trace_dir: Path, want: int, *, scan_files: int = 8):
    """挑决策：**先按"覆盖面"贪心**（每种会走不同特征分支的状态各来一条），再补足条数。

    `yield (文件, 决策序号, 行)` —— 序号要与 sidecar 的行序对齐（那是逐决策段）。

    ⚠ 为什么不能只按 `kind` 去重（2026-09-27 的教训）：第一版夹具只覆盖了
    `turn` / `claim` / 带副露，7 个用例**恰好都没人立直** —— 于是 Java 把 obs 里的
    `riichi`/`ippatsu`（**布尔数组**）用 `ints()` 读成 0、而 Python 读成 1，三端对拍
    全绿却掩盖了 8 个通道的漂移。现在按状态打标签，并把必须覆盖的标签写成闸门。

    ⚠ W1 起还多要一条：**至少一个用例的事件流长于窗口 `K_EVT`**（`h0` 非 0）。否则每个用例
    的 `h0` 都是全 0，"同一 `h0` ⇒ 同一输出"就成了空转（三端都忽略 `h0` 也全绿）。
    """
    picked: list[tuple[Path, int, dict]] = []
    seen_tags: set[str] = set()
    seen_kinds: set[str] = set()
    long_seen = False
    for f in traces.trace_files(trace_dir)[:scan_files]:
        idx = 0
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") != "decision":
                continue
            obs = row["obs"]
            tags = _tags(obs)
            new = tags - seen_tags
            kind = str(obs.get("kind"))
            long = len(obs.get("events") or []) > spec.K_EVT
            if new or kind not in seen_kinds or (long and not long_seen):
                seen_tags |= tags
                seen_kinds.add(kind)
                picked.append((f, idx, row))
            long_seen = long_seen or long
            idx += 1
            if len(picked) >= want and long_seen:
                return picked
    return picked


#: 夹具**必须**覆盖的状态标签（覆盖不到就报错 —— 免得夹具"看着有 7 条"却全在一条分支上）
#: ⚠ `evt_short`（事件流 ≤8 条）是 W1 加的：窗口前部是**零 padding**，而"喂不喂 padding"
#:   在**满窗**用例上被 GRU 的收缩性洗掉（实测 |Δh|=1.3e-7，判据看不见）；只有在
#:   "绝大多数是 padding、真实事件只有几条"的用例上才会放大到 1e-2 量级 ⇒ 缺了它，
#:   三端把 padding 也喂进 GRU 也照样全绿。
REQUIRED_TAGS = ("turn", "claim", "own_meld", "riichi_any", "ippatsu", "evt_short")


def _tags(obs: Mapping[str, Any]) -> set[str]:
    """一条决策"走了哪些特征分支"（选例与覆盖闸门都用它）。"""
    tags = {str(obs.get("kind"))}
    seat = int(obs.get("seat", 0))
    melds = obs.get("melds") or []
    if seat < len(melds) and melds[seat]:
        tags.add("own_meld")
    if any(obs.get("riichi") or []):
        tags.add("riichi_any")
    if obs.get("self_riichi"):
        tags.add("self_riichi")
    if any(obs.get("ippatsu") or []):
        tags.add("ippatsu")
    if obs.get("furiten"):
        tags.add("furiten")
    events = obs.get("events") or []
    types = {str(e.get("type")) for e in events}
    if len(events) <= 8:
        tags.add("evt_short")                                     # 窗口几乎全是 padding（W1）
    for t in ("kan", "dora_flip", "meld"):
        if t in types:
            tags.add("evt_" + t)
    discards = [e for e in events if str(e.get("type")) == "discard"]
    if any(e.get("tsumogiri") for e in discards):
        tags.add("tsumogiri")
    if any(not e.get("tsumogiri") for e in discards):
        tags.add("tedashi")
    if any(str(e.get("tile") or "").startswith("0")
           or str(e.get("called_tile") or "").startswith("0") for e in events):
        tags.add("aka_evt")
    if any(obs.get("hand_red") or []):
        tags.add("aka_hand")
    return tags


def build_golden(trace_dir: str | Path, out: str | Path, *, cases: int = 8,
                 dims: Mapping[str, int] | None = None, seed: int = 20260928,
                 scan_files: int = 8) -> Path:
    """真实轨迹 → golden 夹具（小 v4 + 四张量 + 四个推理头输出）。"""
    d = dict(dims or GOLDEN_DIMS)
    torch.manual_seed(seed)
    net = M.build(seed, **d)
    # ⚠ **夹具必须显式扰动"零初始化"的张量**，否则那条路径根本没被测到（"假接入"的温床）。
    #   第五十七轮的手写版只扰动了 `policy_gate`；W1 起改成**通用规则**（`VALVES-AND-FIXTURES`
    #   §2.1 铁律①）：凡 `state_dict()` 里 `max|·| == 0` 的参数一律 `normal_(0, 0.5)` ——
    #   零初始化层的设计意图就是"起步恒等"（`policy_gate` / `fusion.mem`），
    #   而夹具要的恰恰是**非恒等**，否则 tanh/索引/偏置换成错的也照样全绿。
    with torch.no_grad():
        perturbed: list[str] = []
        for name, p in net.state_dict().items():
            if p.numel() and float(p.abs().max()) == 0.0:
                p.normal_(0.0, 0.5)
                perturbed.append(name)
        # ⚠ **让初始状态活得过窗口**：随机初始化的 GRU 是收缩的（更新门 z≈0.5 ⇒ `0.5^60 ≈ 1e-18`），
        #   实测满窗上 `h0` 的影响只有 3e-8 < 夹具容差 1e-4 ⇒ 三端**都忽略 `h0`** 也照样全绿，
        #   而 `h0` 恰恰是 W1 唯一新增的输入。把更新门偏置抬到 +3（z≈0.95）⇒ `h0` 的影响
        #   放大到 1e-2 量级，"同一 h0 ⇒ 同一输出"第一次有牙齿。
        #   ⚠ 这是**夹具的权重选择**（合成小网本来就不是真实权重），不是模型改动：上线的网
        #   自己的遗忘门是多少由训练决定。
        #   ⚠ 门序是 PyTorch `GRUCell` 的 **(r, z, n)**：更新门 `z` 在第 **2** 块 `[dm:2dm)`，
        #   不是第 3 块（写错成 `n` 只是把候选值顶饱和，`h0` 照样被洗掉 —— 实测 h0 探针为 0）。
        dm = int(d["d_model"])
        pr = dict(net.named_parameters())
        for name in ("event.cell.bias_ih", "event.cell.bias_hh"):
            pr[name][dm:2 * dm] += 3.0
        # ⚠ **让注意力真的"看"候选**（W1 顺手补的夹具缺陷）：随机小网的 `q·k/√d` 只有 ~0.02
        #   量级 ⇒ softmax 近乎均匀 ⇒ `u_evt` 与候选**逐元素相同**、策略头 logits 的组内极差
        #   只有 **1e-7** ⇒ ① argmax 由浮点噪声决定（三端谁的求和顺序不同谁就换个动作）；
        #   ② "候选 → 注意力 → 策略"整条路**根本没被测到**（注意力权重写错也全绿）。
        #   把融合两处注意力的 `in_proj` 放大 **20×**（score 放大 20×，softmax 变尖）。
        #   ⚠ 别贪大：再放大（60×/150×）softmax 会**饱和成硬 argmax** ⇒ 所有候选查到的
        #   都是同一个 key ⇒ `u` 重新变成与候选无关（实测 60× 时极差回落到 7e-7、150× 为 0）。
        #   20× 是实测的峰值（组内极差 1e-4~1e-3，比不放大的 1e-7 高三个数量级）。
        for name in ("fusion.q_tile.in_proj_weight", "fusion.q_tile.in_proj_bias",
                     "fusion.q_evt.in_proj_weight", "fusion.q_evt.in_proj_bias"):
            pr[name] *= 20.0
    sd = net.state_dict()
    blob = net_blob(sd, d)

    rows = _iter_cases(Path(trace_dir), cases, scan_files=scan_files)
    if not rows:
        raise NetFormatError(f"{trace_dir} 里没读到 decision 行")
    covered: set[str] = set()
    for _, _, r in rows:
        covered |= _tags(r["obs"])
    missing = [t for t in REQUIRED_TAGS if t not in covered]
    if missing:
        raise NetFormatError(
            f"夹具覆盖不足：缺 {missing}（已覆盖 {sorted(covered)}）—— 换个轨迹目录，"
            f"或加长扫描（`--scan-files`）。**别降级跳过**：夹具没覆盖到的分支就是没钉住的漂移面")
    cache: dict[Path, dict] = {}
    body = bytearray()
    dm = int(d["d_model"])
    mem_probe = 0.0                                 # ① `fusion.mem` 被消费（归零 ⇒ 输出变）
    h0_probe = 0.0                                  # ② `h0` 真的传播（仅长事件流用例）
    n_long = 0                                      # 有用例真的走了"重放前缀"那条路
    spread_min = float("inf")                       # ③ 策略头**真的在区分候选**（否则 argmax 是噪声）
    for src, idx, row in rows:
        obs = row["obs"]
        traces.gate_obs(obs, where=f"{src.name}:{idx}")
        sc = cache.get(src)
        if sc is None:
            sc = traces.load_sidecar(_dataset.sidecar_path(src))
            cache[src] = sc
        side = traces.sidecar_dict(sc, idx)
        t = blocks.assemble(obs, side, allow_degraded=False)
        if t.degraded:
            raise NetFormatError(f"{src.name} 第 {idx} 条降级：{sorted(t.degraded)}")
        legal = list(obs.get("legal") or [])
        if len(legal) != t.cand.shape[0]:
            raise NetFormatError(
                f"{src.name} 第 {idx} 条：legal {len(legal)} != cand {t.cand.shape[0]}")
        with torch.no_grad():
            # `h0` = **窗口之前**的 carry：重放本手事件流的前 `n-K` 条（离线端的**唯一**算法，
            # 见 `cache.carry_prefix`）⇒ 夹具里的 `h0` 与**生产端**（Java 重放整手 / 缓存槽）
            # 是同一个量，"同 h ⇒ 同输出"才是真的在生产语义上被判据。
            pre = max(0, len(obs.get("events") or []) - spec.K_EVT)
            n_long += 1 if pre > 0 else 0
            h0 = _cache.carry_prefix(
                lambda tok, h: net.step_event(torch.from_numpy(np.asarray(tok, np.float32))[None], h),
                obs, pre)
            h0 = torch.zeros(dm) if h0 is None else h0[0].detach()
            args = (torch.from_numpy(t.tile[None]), torch.from_numpy(t.evt[None]),
                    torch.from_numpy(t.ctx[None]), torch.from_numpy(t.cand[None]))
            o = net(*args, h=h0[None])
            # 红证①（**夹具必须能测到 `fusion.mem`**）：把 `fusion.mem.weight` 归零 ⇒ 策略头
            #   必须变。零初始化只保证"起步恒等"，不保证"这条接线真的在算"。
            if float(net.fusion.mem.weight.abs().sum()) == 0.0:
                raise NetFormatError("夹具空转：`fusion.mem.weight` 全 0（零初始化没被扰动）")
            saved = net.fusion.mem.weight.detach().clone()
            net.fusion.mem.weight.zero_()
            o0 = net(*args, h=h0[None])
            net.fusion.mem.weight.copy_(saved)
            fin = torch.isfinite(o["policy"] - o0["policy"])
            mem_probe = max(mem_probe, float((o["policy"] - o0["policy"])[fin].abs().max()))
            # ③ 策略头组内极差（>1 个候选时才有意义）：太小 ⇒ argmax 判据是噪声（见下面的闸门）
            if len(legal) > 1:
                pol = o["policy"][0]
                spread_min = min(spread_min, float(pol.max() - pol.min()))
            # 红证②（**夹具必须能测到 `h0`**，只在 `h0 != 0` 的用例上有意义）：扰动 `h0`
            #   ⇒ 输出必须变。没有它，"三端都忽略 h0"也能全绿（GRU 收缩时实测差 3e-8）。
            if pre > 0:
                o1 = net(*args, h=(h0 + 0.5)[None])
                fin1 = torch.isfinite(o["policy"] - o1["policy"])
                h0_probe = max(h0_probe, float((o["policy"] - o1["policy"])[fin1].abs().max()))
        obs_b = json.dumps(obs, ensure_ascii=False).encode("utf-8")
        body += struct.pack("<I", len(obs_b)) + obs_b
        body += struct.pack("<H", len(legal))
        for k in legal:
            kb = k.encode("utf-8")
            body += struct.pack("<H", len(kb)) + kb
        body += h0.numpy().astype("<f4").tobytes()          # 格式 2：h0 在四张量之前
        for arr in (t.tile, t.evt, t.ctx, t.cand):
            body += np.ascontiguousarray(arr, dtype="<f4").tobytes()
        body += o["policy"][0].numpy().astype("<f4").tobytes()
        body += o["value"][0].numpy().astype("<f4").tobytes()
        body += o["belief_tenpai"][0].numpy().astype("<f4").tobytes()
        body += o["danger"][0].numpy().astype("<f4").tobytes()
        # 格式 2 起**多一个期望输出**：`h_evt`（窗口之后的 carry，即喂进融合的那个向量）。
        # 它让"padding 喂不喂"这条口径**直接**可比 —— 只看七个头的话，满窗用例上
        # 那个差被 GRU 收缩性洗到 1e-7（判据看不见），而 `evt_short` 用例上它是 1e-2 量级。
        body += o["h_evt"][0].numpy().astype("<f4").tobytes()

    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    # ② 空转闸门：`h0` 若对输出毫无影响（例如 `fusion.mem` 没被扰动 / 前向没消费它），
    #    这份夹具就**测不到 W1 那条接线的三端一致性** —— 当场报错，别写出一份假绿的夹具。
    # 空转闸门（三条一起）：`fusion.mem` 必须被消费、`h0` 必须能传播、且**至少一个用例
    # 的 `h0` 非 0**（否则"忽略 h0"的三端也能全绿 —— 判据必须能测到那条路，不是"看起来全绿"）。
    if not (mem_probe > 1e-4):
        raise NetFormatError(
            f"夹具空转：归零 `fusion.mem.weight` 只让策略头动了 {mem_probe:.3g}（应 >1e-4）—— "
            f"`fusion.mem.*` 是否被随机化？`Fusion` 是否真的消费了 `h_evt`？")
    if n_long == 0:
        raise NetFormatError(
            "夹具覆盖不足：没有任何用例的事件流长于窗口 K_EVT ⇒ 每个用例的 `h0` 都是全 0，"
            "三端**都忽略 `h0`** 也照样全绿（换个轨迹目录，或加长 `--scan-files`）")
    if not (h0_probe > 1e-4):
        raise NetFormatError(
            f"夹具空转：长事件流用例上扰动 `h0` 只让策略头动了 {h0_probe:.3g}（应 >1e-4）—— "
            f"夹具的 GRU 太收缩（`event.cell.*` 的更新门偏置没抬？），三端忽略 `h0` 也测不出来")
    # ③ **候选必须真的区分开**：极差 ~1e-7 时 argmax 由浮点噪声决定（"每条的 argmax 一致"这条
    #    判据会随机红），而更糟的是"候选 → 注意力 → 策略"那条路根本没被测到。
    if not (spread_min > 1e-4):
        raise NetFormatError(
            f"夹具空转：策略头 logits 的**组内极差**只有 {spread_min:.3g}（应 >1e-4）—— "
            f"注意力太接近均匀（`fusion.q_*` 的 in_proj 没放大？），候选差异没有进 logits")
    head_b = struct.pack("<4I", GOLDEN_MAGIC, GOLDEN_FORMAT, len(rows), len(blob))
    p.write_bytes(head_b + blob + bytes(body))
    meta = {"cases": len(rows), "format": GOLDEN_FORMAT, "dims": d, "net_bytes": len(blob),
            "bytes": p.stat().st_size, "trace": str(trace_dir), "seed": seed,
            "blocks_fingerprint": fingerprint_of(),
            "case_kinds": [f"{r[2].get('kind')}" for r in rows],
            "coverage": sorted(covered), "required_tags": list(REQUIRED_TAGS),
            "perturbed_zero_params": perturbed,
            "mem_probe_max_dlogit": mem_probe,
            "h0_probe_max_dlogit": h0_probe,
            "logit_spread_min": spread_min,
            "cases_with_long_carry": n_long,
            "tol": 1e-4}
    p.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                      encoding="utf-8", newline="\n")
    return p


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="v4 权重导出 / golden 夹具（P5 的 Java+C++ 前向输入）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("weights", help="v4 checkpoint → net.bin 格式 2")
    w.add_argument("--ckpt", required=True)
    w.add_argument("--out", required=True)
    w.add_argument("--label", default="")
    w.add_argument("--n-heads", type=int, default=0, help="缺省从 checkpoint 的 config.dims 读，再缺省 4")
    g = sub.add_parser("golden", help="真实轨迹 → golden 对拍夹具")
    g.add_argument("--trace", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--cases", type=int, default=8)
    g.add_argument("--scan-files", type=int, default=8, help="最多扫几个轨迹文件来找齐覆盖面")
    g.add_argument("--full", action="store_true", help="用上线架构（192/64/4/51）而不是小网络")
    g.add_argument("--seed", type=int, default=20260928)
    a = ap.parse_args(argv)

    if a.cmd == "weights":
        ck = torch.load(a.ckpt, map_location="cpu", weights_only=False)
        sd = ck["model"]
        cfg = ck.get("config") or {}
        d = dict(cfg.get("dims") or dims_from_state(sd))
        if a.n_heads:
            d["n_heads"] = a.n_heads
        p = save_net(sd, d, a.out, label=a.label or Path(a.ckpt).parent.name,
                     source=str(a.ckpt))
        print(f"已导出 v4 权重：{p}（{p.stat().st_size} 字节，格式 2，"
              f"{json.loads(p.with_suffix('.json').read_text(encoding='utf-8'))['params']:,} 参数，"
              f"dims={d}）")
        print(f"  ↓ 服务端用法：java -jar mahjong-server.jar --selfplay 100 "
              f"--policy net:{p},teacher,teacher,teacher --out <dir>")
    else:
        p = build_golden(a.trace, a.out, cases=a.cases, scan_files=a.scan_files,
                         dims=FULL_DIMS if a.full else GOLDEN_DIMS, seed=a.seed)
        # ⚠ 用例数可能**多于** `--cases`：`_iter_cases` 要凑齐覆盖闸门（含"事件流长于窗口"那条）
        #   才会停 ⇒ 报数从写出的 meta 读，别报 `--cases`（那会与实际不符）。
        n = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))["cases"]
        print(f"已写出 v4 golden 夹具：{p}（{p.stat().st_size} 字节，{n} 个用例）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
