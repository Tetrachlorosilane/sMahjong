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

    magic u32 "MJ4G" · format u32 = 1 · nCases u32 · netLen u32 · net（上面那个格式 2）
    每个 case：obsLen u32 · obs(utf8 JSON) · nLegal u16 · (keyLen u16 · key)×n
      · tile f32[34×C_TILE] · evt f32[K_EVT×C_EVT] · ctx f32[C_CTX] · cand f32[n×C_CAND]
      · logits f32[n]（策略头，逐候选）· value f32[valueBins] · beliefTenpai f32[3]
      · danger f32[n×4]

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
from . import blocks, model as M, spec, traces

#: 与 v3 同一个魔数（`mahjong_ml.export.WEIGHT_MAGIC`），用 `format` 区分代号。
NET_MAGIC = 0x4D4A4E4E          # "MJNN"
NET_FORMAT = 2                  # v4 = 格式 2（带块清单）
GOLDEN_MAGIC = 0x4D4A3447       # "MJ4G"
GOLDEN_FORMAT = 1

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
        "fusion.out.net.0.weight": (dm, 3 * dm), "fusion.out.net.0.bias": (dm,),
        "fusion.out.net.2.weight": (dm, dm), "fusion.out.net.2.bias": (dm,),
        # 头
        # ⚠ **policy 的输入宽度是 `dm+3`**（2026-09-30 第四十九轮）：输入是 `[u ; sigmoid(belief_tenpai)]`
        #   （与 `model.py` 的 `Heads.forward`、Java `V4Policy`、C++ `v4policy.cpp` 逐位同序）。
        #   **旧网（宽 `dm`）仍可导出**：三端加载期会把旧宽右侧补 0（等价于 belief 输入恒为 0
        #   ⇒ 与接 belief 之前逐位相同），所以这里**两种宽度都接受**。
        "heads.policy.weight": ((1, dm + 3), (1, dm)), "heads.policy.bias": (1,),
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
        # ⚠ 期望形状可以是**多个**（`tuple[tuple[int, ...], ...]`）：目前只有
        #   `heads.policy.weight` 用得上（新宽 `dm+3` / 旧宽 `dm` 都接受，见 `expected_shapes`）。
        alts = s if isinstance(s, tuple) and s and isinstance(s[0], tuple) else (s,)
        if k not in got:
            bad.append(f"缺 {k}{alts[0]}")
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


def state_from_net(parsed: Mapping[str, Any]) -> dict[str, torch.Tensor]:
    """张量表 → `state_dict()`（自检用它做"导出 → 读回 → 前向"的闭环）。

    ⚠ **旧网兼容**（第四十九/五十轮）：`heads.policy.weight` 的宽度可能是**旧宽 `dm`**
    （第四十九轮之前训的网，如 `p3-001` / 第一季 `g08`）。这里**右侧补 3 个 0**再交给
    `load_state_dict(strict=True)` —— 与 Java `V4Policy.matOrPadPolicy`、C++
    `v4policy.cpp` 的绑定期补 0 **同一语义**（那 3 个 `sigmoid(belief_tenpai)` 输入贡献恒为 0
    ⇒ 前向与"接 belief 之前"逐位相同）。⛔ 少了这一步，`--init <旧网>` 会直接
    `size mismatch for heads.policy.weight: [1,192] vs [1,195]`（第二季第一代实测踩过）。
    """
    sd = {k: torch.from_numpy(np.ascontiguousarray(v)) for k, v in parsed["tensors"].items()}
    w = sd.get("heads.policy.weight")
    if w is not None and w.ndim == 2:
        dm = int(sd["tile.proj.weight"].shape[0])
        if int(w.shape[1]) == dm:                # 旧宽 ⇒ 右侧补 0
            pad = torch.zeros((w.shape[0], dm + 3), dtype=w.dtype)
            pad[:, :dm] = w
            sd["heads.policy.weight"] = pad
        elif int(w.shape[1]) != dm + 3:
            raise NetFormatError(f"heads.policy.weight 宽度 {int(w.shape[1])} 既不是新宽 "
                                 f"{dm + 3} 也不是旧宽 {dm}")
    return sd


# ------------------------------------------------------------------ 夹具

def _iter_cases(trace_dir: Path, want: int, *, scan_files: int = 8):
    """挑决策：**先按"覆盖面"贪心**（每种会走不同特征分支的状态各来一条），再补足条数。

    `yield (文件, 决策序号, 行)` —— 序号要与 sidecar 的行序对齐（那是逐决策段）。

    ⚠ 为什么不能只按 `kind` 去重（2026-09-27 的教训）：第一版夹具只覆盖了
    `turn` / `claim` / 带副露，7 个用例**恰好都没人立直** —— 于是 Java 把 obs 里的
    `riichi`/`ippatsu`（**布尔数组**）用 `ints()` 读成 0、而 Python 读成 1，三端对拍
    全绿却掩盖了 8 个通道的漂移。现在按状态打标签，并把必须覆盖的标签写成闸门。
    """
    picked: list[tuple[Path, int, dict]] = []
    seen_tags: set[str] = set()
    seen_kinds: set[str] = set()
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
            if new or kind not in seen_kinds:
                seen_tags |= tags
                seen_kinds.add(kind)
                picked.append((f, idx, row))
            idx += 1
            if len(picked) >= want:
                return picked
    return picked


#: 夹具**必须**覆盖的状态标签（覆盖不到就报错 —— 免得夹具"看着有 7 条"却全在一条分支上）
REQUIRED_TAGS = ("turn", "claim", "own_meld", "riichi_any", "ippatsu")


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
            o = net(torch.from_numpy(t.tile[None]), torch.from_numpy(t.evt[None]),
                    torch.from_numpy(t.ctx[None]), torch.from_numpy(t.cand[None]))
        obs_b = json.dumps(obs, ensure_ascii=False).encode("utf-8")
        body += struct.pack("<I", len(obs_b)) + obs_b
        body += struct.pack("<H", len(legal))
        for k in legal:
            kb = k.encode("utf-8")
            body += struct.pack("<H", len(kb)) + kb
        for arr in (t.tile, t.evt, t.ctx, t.cand):
            body += np.ascontiguousarray(arr, dtype="<f4").tobytes()
        body += o["policy"][0].numpy().astype("<f4").tobytes()
        body += o["value"][0].numpy().astype("<f4").tobytes()
        body += o["belief_tenpai"][0].numpy().astype("<f4").tobytes()
        body += o["danger"][0].numpy().astype("<f4").tobytes()

    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    head_b = struct.pack("<4I", GOLDEN_MAGIC, GOLDEN_FORMAT, len(rows), len(blob))
    p.write_bytes(head_b + blob + bytes(body))
    meta = {"cases": len(rows), "format": GOLDEN_FORMAT, "dims": d, "net_bytes": len(blob),
            "bytes": p.stat().st_size, "trace": str(trace_dir), "seed": seed,
            "blocks_fingerprint": fingerprint_of(),
            "case_kinds": [f"{r[2].get('kind')}" for r in rows],
            "coverage": sorted(covered), "required_tags": list(REQUIRED_TAGS),
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
        print(f"已写出 v4 golden 夹具：{p}（{p.stat().st_size} 字节，{a.cases} 个用例）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
