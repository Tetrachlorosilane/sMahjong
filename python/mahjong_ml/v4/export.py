"""v4 权重导出 + golden 对拍夹具（P5：Java / C++ 手写 v4 前向的**唯一输入格式**）。

    python -m mahjong_ml.v4.export weights --ckpt <model.pt> --out <net.bin> [--label <名字>]
       训练好的 v4 checkpoint → 服务端/C++ 能直读的 **`net.bin` 格式 3**（小端 float32，
       **带 2,048 B arch 段 / 块清单 / 张量表**）。Java `mahjong.ai.V4Policy` 与 C++ `trainer`
       各自手写前向，服务端仍是零第三方依赖。

    python -m mahjong_ml.v4.export info --net <net.bin>
       只读打印头部 + arch 段（段 3 对账用）；`format==2` ⇒ 专用错误、退出码 2。

    python -m mahjong_ml.v4.export golden --trace <轨迹目录> --out <夹具> [--cases N]
       真实轨迹 → **golden 夹具**（magic `MJ4G`）：固定种子的**小** v4 + 若干真实决策的
       `tile/evt/ctx/cand`（Python 侧按 `blocks.assemble` 拼出来）+ 四个推理头的输出。
       Java 的 `SelfTest.v4ForwardTests` 与 C++ 的 `trainer v4net` 都读它，
       **逐元素**核对"特征拼装 + 前向"三端一致（容差 1e-4）。

## 二进制格式（`docs/FEATURES-V4.md` §6.1「net.bin 格式 2/3」逐字段）

⚠ **魔数与 v3 相同（`MJNN`），靠 `format` 区分**：v3 是 `format=1`（定长 MLP，无块清单），
**v4 与 A− 都是 `format=3`**（64 B 头 + **2,048 B arch 段** + 块清单 + 张量表）。
`format=2`（A− 之前的老包）**已硬切退役**：读到就报专用错误（指向冻结快照），
⛔ 不自动升级、不补 0、不猜 arch —— 见 `read_net` 的 `_RETIRED_FORMAT2`。

    头部（64 B，偏移与格式 2 完全一致，只有 `format` 变 3）：
      magic u32 "MJNN" · format u32 = 3 · featureVersion u32 = 4 · obsVersion u32 = 3
      · derivedVersion u32 = 3 · dModel u32 · tileD u32 · nHeads u32 · valueBins u32
      · nBlocks u32 · nTensors u32 · fingerprint 16 B（ASCII 的 16 个十六进制字符）
      · params u32（**校验位**：必须等于张量表实算的元素数）
    arch 段（**总长固定 2,048 B** = 96 B 核心 + 1,952 B 保留尾，全 0；`archLen` 写在首 4 B）：
      见 `_ARCH_FIELDS` / `arch_profile`（§3 字段表；写端与读端共用同一张表）
    块清单（nBlocks 条，按注册表顺序；**子集 = 消融**）：
      idLen u16 · id(utf8) · width u32
    张量表（nTensors 条，**名字升序**，名字 = PyTorch `state_dict()` 的键）：
      nameLen u16 · name(utf8) · ndim u8 · dims u32×ndim · 数据 f32（行主序、紧凑、无对齐）

    ⚠ **两套拓扑共用 format 3**（`arch=v4` / `arch=a-`）：头部只写四个宽度，**拓扑由 arch 段
    表达**（`topoId/actId/normId/flags` 决定前向分支）。这样 golden 夹具仍能用**小网络**
    （几十 KB，能进仓库），而真权重照跑（v4 `192/64/4/51`、A− `128/160/4/81`）。
    ⚠ 段 2 只做 Python 侧：Java/C++ 仍认 `format=2` ⇒ **format 3 的包现在两端都读不了**，
    要等段 3（本批 ⛔ 不动 C++/Java）。

## golden 夹具布局

    magic u32 "MJ4G" · format u32 = 2 · nCases u32 · netLen u32 · net（**format 3** 的 net.bin）
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
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import torch

from .. import dataset as _dataset
from . import blocks, cache as _cache, model as M, spec, traces

#: 与 v3 同一个魔数（`mahjong_ml.export.WEIGHT_MAGIC`），用 `format` 区分代号。
NET_MAGIC = 0x4D4A4E4E          # "MJNN"
#: 段 2（第一百零八轮）：`v4` 与 `a-` 都写 **格式 3**（64 B 头 + 2,048 B arch 段 + 块表 + 张量表）。
NET_FORMAT = 3
#: 格式 1 = v3 的定长 MLP（`mahjong_ml.export.NeuralPolicy` 路径）—— 本模块不解析、但**保留**。
NET_FORMAT_V3 = 1
#: 格式 2 = A− 之前的老包 —— **硬切退役**（读到 ⇒ `NetFormatError` + 指向冻结快照）。
NET_FORMAT_RETIRED = 2
GOLDEN_MAGIC = 0x4D4A3447       # "MJ4G"
#: 夹具**容器**格式（用例布局）：2 = 每用例带 `h0`/`h_evt`。⚠ 与**内嵌 net 的**格式解耦。
GOLDEN_FORMAT = 2

#: 头部字节数（9 个 u32 + 2 个 u32 + 16 B 指纹 + 1 个 u32 = 64）；格式 3 的偏移一个字节没动。
HEADER_BYTES = 64

#: arch 段：**总长固定 2,048 B** = 96 B 核心 + 1,952 B 保留尾（全 0）。`_ARCH_FIELDS` 是唯一规格。
ARCH_SEG_BYTES = 2048
ARCH_CORE_BYTES = 96
#: `archVersion`：1 = 本表。>1 ⇒ 报错（⛔ 不猜新版本字段的含义）。
ARCH_VERSION = 1
#: `topoId` 注册表（§3 只命名 3 = A−/T1；v4 在格式 3 里顺延为 2，段 2 起 v4 也写格式 3）
TOPO_IDS: dict[int, str] = {2: M.ARCH_V4, 3: M.ARCH_A}
ARCH_TOPO_IDS: dict[str, int] = {v: k for k, v in TOPO_IDS.items()}
#: `actId` / `normId` / `segEmbMode` / `memTokenMode` 的注册值（未注册 ⇒ 报错）
ACT_IDS: dict[int, str] = {0: "relu", 1: "gelu"}
NORM_IDS: dict[int, str] = {0: "post-ln", 1: "pre-ln"}
SEG_EMB_MODES: dict[int, str] = {0: "none", 1: "three-seg-sum"}
MEM_TOKEN_MODES: dict[int, str] = {0: "mem-gate", 1: "h_evt-token-95"}
#: `flags` 的位定义（§3）：bit0 `hasB3` · bit1 `hasTileId` · bit2 `hasFiLM` · bit3 `hasMemGate`
FLAG_B3 = 1 << 0
FLAG_TILE_ID = 1 << 1
FLAG_FILM = 1 << 2
FLAG_MEM_GATE = 1 << 3
#: ★ 第一百一十六轮：arch 段的**保留区**（偏移 70/71，原来是"必须全 0"的 26 B）里新开的两个字段。
#: ⚠ **不升 format**（`format` 仍是 3、`archVersion` 仍是 1、`archLen` 仍是 2,048）——
#: 保留区的语义从"必须全 0"缩到"72..95 必须全 0"。旧二进制读到这些字节非 0 会**响亮报错**
#: （这是刻意的：它们不认识这个分支，绝不能静默当成 0 继续跑）。
#: · `evtReadout` = 1 ⇒ 有 S1 的逐候选事件读出（`fusion.evt_kv.*` / `fusion.evt_gamma`）
#: · `evtLN`      = 1 ⇒ `evt` 首层有 LayerNorm（`event.norm0.*`，P4）
EVT_READOUT_KEYS = ("fusion.evt_kv.weight", "fusion.evt_gamma")
EVT_LN_KEYS = ("event.norm0.weight", "event.norm0.bias")

#: 夹具缺省用**小网络**（能进仓库；真权重是 v4 192/64/4/51 · A− 128/160/4/81）
GOLDEN_DIMS: dict[str, int] = {"d_model": 32, "tile_d": 16, "n_heads": 2, "value_bins": 5}
#: A− 夹具的小网：`32/16/2/81`（`A-REFACTOR-IMPL-PLAN` §3 的 G1 口径；81 = value ±60）
GOLDEN_DIMS_A: dict[str, int] = {"d_model": 32, "tile_d": 16, "n_heads": 2,
                                 "value_bins": M.A_VALUE_BINS}
#: 上线架构（`model.py` 的缺省值）
FULL_DIMS: dict[str, int] = {"d_model": M.D_MODEL, "tile_d": M.TILE_D,
                             "n_heads": M.N_HEADS, "value_bins": M.VALUE_BINS}

#: 每个头的输出宽度（**契约头**见 `model.contract_heads()`；**推理头**见 `model.ONLINE_HEADS`）
HEAD_WIDTHS = {"policy": 1, "placement": 4, "belief_hand": 3 * 34, "belief_tenpai": 3,
               "danger": 4, "effect": 3}


class NetFormatError(RuntimeError):
    """权重文件/夹具不合格 —— 一律**报错**，不猜、不降级。"""


#: `format==2` 的**硬切**文案（§3）：要让用户一眼看出"这是老包、需要旧二进制"，
#: 而不是"文件坏了"。⛔ 不许在这里**自动升级**（补 0 / 猜 arch 都会静默换掉前向语义）。
_RETIRED_FORMAT2 = (
    "权重格式版本 2 已退役：format 3 起是 v4 新拓扑（64 B 头 + arch 段）。"
    "**这是 A− 之前的老包**（format 2，没有 arch 段）—— 只有**旧二进制**能读它："
    "冻结快照 `lab/mv1/frozen-format2`（`mahjong-server.jar` sha 3E7D0ADC…、`trainer.exe` "
    "F6AAA307…）。⛔ 本加载器不会自动升级 / 不补 0 / 不猜 arch；要转格式请用冻结快照里的"
    "旧工具重新导出，或按段 3 的新端重训。"
)


# ------------------------------------------------------------------ arch 段（格式 3）

#: §3 字段表 = **唯一规格**（写端 `_pack_arch_segment` 与读端 `_read_arch_segment` 共用；
#: 自检逐字段对账）。`(偏移, 名字, 类型, 字节数)`，偏移相对 **arch 段首**。
#: ⚠ 核心 = 96 B（含首 4 B 的 `archLen`）；`archLen` 之外的部分是**保留尾**，必须全 0。
_ARCH_FIELDS: tuple[tuple[int, str, str, int], ...] = (
    (0, "archLen", "u32", 4),
    (4, "archVersion", "u16", 2),
    (6, "topoId", "u16", 2),
    (8, "dTile", "u16", 2),
    (10, "dEvt", "u16", 2),
    (12, "dFuse", "u16", 2),
    (14, "dHead", "u16", 2),
    (16, "dCand", "u16", 2),
    (18, "headDim", "u16", 2),
    (20, "nTileConv", "u8", 1),
    (21, "nTileAttn", "u8", 1),
    (22, "nJointLayer", "u8", 1),
    (23, "actId", "u8", 1),
    (24, "normId", "u8", 1),
    (25, "segEmbMode", "u8", 1),
    (26, "memTokenMode", "u8", 1),
    (27, "flags", "u8", 1),
    (28, "valueBins", "u16", 2),
    (30, "nHeads", "u16", 2),
    (32, "valueRange", "f32", 4),
    (36, "lnEps", "f32", 4),
    (40, "tileKernel0", "u8", 1),
    (41, "tileKernel1", "u8", 1),
    (42, "tileKernel2", "u8", 1),
    (43, "tileKernel3", "u8", 1),
    (44, "nSeg", "u8", 1),
    (45, "pad", "u8", 1),
    (46, "pad2", "u8", 1),
    (47, "pad3", "u8", 1),
    (48, "featVersion", "u32", 4),
    (52, "obsVersion", "u32", 4),
    (56, "derivedVersion", "u32", 4),
    (60, "cTile", "u16", 2),
    (62, "cEvt", "u16", 2),
    (64, "kEvt", "u16", 2),
    (66, "cCtx", "u16", 2),
    (68, "cCand", "u16", 2),
    # ★ 第一百一十六轮：**保留区里新开的两位**（不升 format；见 `EVT_READOUT_KEYS` 的注释）
    (70, "evtReadout", "u8", 1),
    (71, "evtLN", "u8", 1),
    # 72..95 = 24 B 保留（archVersion=1 时非 0 ⇒ 报错）
)


def arch_profile(arch: str, d: Mapping[str, int], *, have: Any = ()) -> dict[str, Any]:
    """该 arch 在**这份宽度**下的 arch 段字段（§3 字段表；写端/读端共用的**唯一规格**）。

    `have` = 实际存在的张量名（只用来定 `flags` 里那三位"可缺张量在不在"）。
    ⚠ 字段值**全部由代码常量推出**（`topoId/actId/normId/...`）；读端拿同一函数算出期望值
    逐字段比对 ⇒ 文件里写了别的值就是"文件在说谎"，当场报错。
    """
    a = str(arch)
    if a not in ARCH_TOPO_IDS:
        raise NetFormatError(f"未注册的 arch {a!r}（可选：{sorted(ARCH_TOPO_IDS)}）")
    names = set(have or ())
    dm, td, nh, vb = (int(d["d_model"]), int(d["tile_d"]),
                      int(d["n_heads"]), int(d["value_bins"]))
    if nh <= 0 or td % nh or dm % nh:
        raise NetFormatError(f"n_heads={nh} 必须同时整除 tile_d={td} 与 d_model={dm}")
    common = {"topoId": ARCH_TOPO_IDS[a], "archVersion": ARCH_VERSION,
              "dCand": int(spec.C_CAND), "headDim": td // nh,
              "valueBins": vb, "nHeads": nh, "lnEps": 1e-5,
              "featVersion": int(spec.FEATURE_VERSION_V4),
              "obsVersion": int(spec.OBS_VERSION_V4),
              "derivedVersion": int(spec.DERIVED_VERSION_V4),
              "cTile": int(spec.C_TILE), "cEvt": int(spec.C_EVT), "kEvt": int(spec.K_EVT),
              "cCtx": int(spec.C_CTX), "cCand": int(spec.C_CAND)}
    if a == M.ARCH_A:
        return {**common,
                "dTile": td, "dEvt": dm, "dFuse": dm, "dHead": dm,
                "nTileConv": 4, "nTileAttn": 1, "nJointLayer": 1,
                "actId": 1, "normId": 1,                      # 1 = GELU / pre-LN
                "segEmbMode": 1, "memTokenMode": 1,           # 1 = 3 段相加 / h_evt 第 95 token
                "valueRange": float(M.A_VALUE_RANGE),
                "tileKernel0": 5, "tileKernel1": 5, "tileKernel2": 3, "tileKernel3": 3,
                "nSeg": int(M.N_SEG), "pad": 0, "pad2": 0, "pad3": 0,
                "evtReadout": 1 if all(k in names for k in EVT_READOUT_KEYS) else 0,
                "evtLN": 1 if all(k in names for k in EVT_LN_KEYS) else 0,
                "flags": (FLAG_B3 if all(k in names for k in _B3_KEYS) else 0)
                         | (FLAG_TILE_ID if "tile.tile_id" in names else 0)}
    return {**common,
            "dTile": td, "dEvt": dm, "dFuse": dm, "dHead": dm,
            "nTileConv": 0, "nTileAttn": 1, "nJointLayer": 0,   # v4 的 tile 塔没有 conv
            "actId": 0, "normId": 0,                            # 0 = ReLU / post-LN
            "segEmbMode": 0, "memTokenMode": 0,                 # 0 = 无段嵌入 / 走 mem 门
            "valueRange": float(M.VALUE_RANGE),
            "tileKernel0": 0, "tileKernel1": 0, "tileKernel2": 0, "tileKernel3": 0,
            "nSeg": 0, "pad": 0, "pad2": 0, "pad3": 0,
            "evtReadout": 0, "evtLN": 0,
            "flags": (FLAG_B3 if all(k in names for k in _B3_KEYS) else 0)
                     | (FLAG_TILE_ID if "tile.tile_id" in names else 0)
                     | (FLAG_MEM_GATE if "fusion.mem.weight" in names else 0)}


def _arch_segment_fields(prof: Mapping[str, Any]) -> dict[str, Any]:
    """补上 `archLen`（= 整段 2,048 B）后的完整字段（写端/读端报错都用它）。"""
    out = dict(prof)
    out["archLen"] = ARCH_SEG_BYTES
    return out


def _pack_arch_segment(prof: Mapping[str, Any]) -> bytes:
    """arch 段 = 96 B 核心 + 1,952 B **全 0** 保留尾（§3）。"""
    f = _arch_segment_fields(prof)
    core = bytearray(ARCH_CORE_BYTES)
    for off, name, kind, size in _ARCH_FIELDS:
        v = f[name]
        if kind == "u8":
            struct.pack_into("<B", core, off, int(v))
        elif kind == "u16":
            struct.pack_into("<H", core, off, int(v))
        elif kind == "u32":
            struct.pack_into("<I", core, off, int(v))
        elif kind == "f32":
            struct.pack_into("<f", core, off, float(v))
        else:                                            # pragma: no cover
            raise AssertionError(f"未知字段类型 {kind}")
    return bytes(core) + bytes(ARCH_SEG_BYTES - ARCH_CORE_BYTES)


def _unpack_arch_segment(raw: bytes, *, feat: int, obs: int, derived: int,
                         dm: int, td: int, nh: int, vb: int) -> tuple[str, dict[str, Any], int]:
    """解析 `[64, 64+archLen)` 的 arch 段 ⇒ `(arch, 字段, 块表偏移)`；不合格一律报错。

    判据（§3 fail-loud 清单，逐条对应）：
      · `archLen ∉ [96, 2048]` / `%4` / **截断** ⇒ 报错（⑥）；
      · `archVersion != 1` / `topoId` 未注册 / `actId/normId/segEmbMode/memTokenMode` 未注册；
      · 保留尾（`96..archLen`，含 46/47 两个 pad）非 0；
      · **头 ↔ arch 段**的 `feat/obs/derived` 与 `valueBins`/`nHeads` 必须相等（①）；
      · `dFuse/dTile` 与头部 `dModel/tileD` 必须相等；
      · 其余结构字段（层数/卷积核/`nSeg`/五常量）必须等于**同一函数算出的期望值**（「文件在说谎」）。
    ⚠ 块表偏移 = `64 + archLen`（§3 原文）；`archLen` 写的是**整段** 2,048 B，
      所以 96 B 核心之后的 1,952 B 保留尾在块表**之前**，不会漏读。
    """
    if len(raw) < HEADER_BYTES + 4:
        raise NetFormatError(f"权重文件被截断：{len(raw)} B，连 arch 段的 `archLen` 都读不到")
    (alen,) = struct.unpack_from("<I", raw, HEADER_BYTES)
    if alen < ARCH_CORE_BYTES or alen > ARCH_SEG_BYTES or alen % 4:
        raise NetFormatError(
            f"archLen={alen} 越界：必须在 [{ARCH_CORE_BYTES}, {ARCH_SEG_BYTES}] 且为 4 的倍数"
            f"（§3：arch 段 96 B 核心 + 1,952 B 保留尾）")
    if len(raw) < HEADER_BYTES + alen:
        raise NetFormatError(
            f"权重文件被截断：arch 段声明 {alen} B，文件只剩 {len(raw) - HEADER_BYTES} B")
    seg = raw[HEADER_BYTES:HEADER_BYTES + alen]
    got: dict[str, Any] = {}
    for off, name, kind, _size in _ARCH_FIELDS:
        if kind == "u8":
            got[name] = seg[off]
        elif kind == "u16":
            got[name] = struct.unpack_from("<H", seg, off)[0]
        elif kind == "u32":
            got[name] = struct.unpack_from("<I", seg, off)[0]
        else:
            got[name] = struct.unpack_from("<f", seg, off)[0]
    got["archLen"] = alen
    if int(got["archVersion"]) != ARCH_VERSION:
        raise NetFormatError(
            f"archVersion={got['archVersion']} 不支持（本实现只认 {ARCH_VERSION}）")
    topo = int(got["topoId"])
    if topo not in TOPO_IDS:
        raise NetFormatError(
            f"topoId={topo} 未注册（已注册：{ {k: v for k, v in TOPO_IDS.items()} }）")
    arch = TOPO_IDS[topo]
    # 枚举字段：**先查"注册没注册"**（未注册 ⇒ 报错），再查"与拓扑一致"（见下面的 check 列表）
    for name, table in (("actId", ACT_IDS), ("normId", NORM_IDS),
                        ("segEmbMode", SEG_EMB_MODES), ("memTokenMode", MEM_TOKEN_MODES)):
        if int(got[name]) not in table:
            raise NetFormatError(
                f"arch 段的 `{name}`={got[name]} 未注册（已注册：{sorted(table)}）")
    # 头 ↔ arch 段：三版本号 + 两个宽度必须相等（§3「头与 arch 两处必须相等」）
    for name, hv in (("featVersion", feat), ("obsVersion", obs), ("derivedVersion", derived)):
        if int(got[name]) != int(hv):
            raise NetFormatError(
                f"头与 arch 段的 `{name}` 不一致：头 {hv} / arch {got[name]}（文件自相矛盾）")
    if int(got["valueBins"]) != int(vb):
        raise NetFormatError(
            f"`valueBins` 三处不一致：头 {vb} / arch {got['valueBins']}"
            f" / 期望按 arch 的 value_bins（读端会在张量表再核 heads.value.weight 行数）")
    if int(got["nHeads"]) != int(nh):
        raise NetFormatError(f"头与 arch 段的 `nHeads` 不一致：头 {nh} / arch {got['nHeads']}")
    prof = arch_profile(arch, {"d_model": dm, "tile_d": td, "n_heads": nh, "value_bins": vb})
    if int(got["dFuse"]) != int(dm) or int(got["dTile"]) != int(td):
        raise NetFormatError(
            f"arch 段的 `dFuse/dTile` = {got['dFuse']}/{got['dTile']} 与头部 `dModel/tileD` = "
            f"{dm}/{td} 不一致")
    if int(got["flags"]) & ~(FLAG_B3 | FLAG_TILE_ID | FLAG_FILM | FLAG_MEM_GATE):
        raise NetFormatError(
            f"arch 段的 `flags`={got['flags']:#04x} 用了**保留位**（bit4–7 必须为 0）")
    # 其余"由拓扑决定"的字段：必须逐字段等于期望值（⛔ 不许出现"文件说 GELU、代码建 ReLU"）
    check = ("nTileConv", "nTileAttn", "nJointLayer", "actId", "normId", "segEmbMode",
             "memTokenMode", "tileKernel0", "tileKernel1", "tileKernel2", "tileKernel3",
             "nSeg", "cTile", "cEvt", "kEvt", "cCtx", "cCand")
    bad = [f"{n}={got[n]}（应 {prof[n]}）" for n in check if int(got[n]) != int(prof[n])]
    if bad:
        raise NetFormatError(
            f"arch 段与 `{arch}` 拓扑不符（{len(bad)} 条）：" + "；".join(bad))
    if abs(float(got["valueRange"]) - float(prof["valueRange"])) > 0.0:
        raise NetFormatError(
            f"`valueRange`={got['valueRange']} 与 arch `{arch}` 的声明 "
            f"{prof['valueRange']} 不符（① fail-loud）")
    # 保留：核心 72..95（24 B）+ 2 个 pad + 保留尾 96..archLen 全 0
    # ⚠ 第一百一十六轮：70/71 已是 `evtReadout`/`evtLN` 两个具名字段（不再是保留区）。
    if seg[46] or seg[47]:
        raise NetFormatError(f"arch 段的 pad 字节非 0（{seg[46]}/{seg[47]}）")
    for name in ("evtReadout", "evtLN"):
        if int(got[name]) not in (0, 1):
            raise NetFormatError(f"arch 段的 `{name}`={got[name]} 未注册（只认 0 / 1）")
    if arch == M.ARCH_V4 and (int(got["evtReadout"]) or int(got["evtLN"])):
        raise NetFormatError(
            f"arch 段的 `evtReadout`/`evtLN` = {got['evtReadout']}/{got['evtLN']}：v4 的图里"
            f"没有这两条路径（只有 a- 有）")
    if any(seg[72:ARCH_CORE_BYTES]):
        raise NetFormatError("arch 段的保留区（偏移 72–95，24 B）非 0（§3：archVersion=1 必须全 0）")
    if alen > ARCH_CORE_BYTES and any(seg[ARCH_CORE_BYTES:alen]):
        raise NetFormatError(
            f"arch 段的保留尾（偏移 96–{alen - 1}）非 0 —— 写端必须全 0")
    got["topoName"] = arch
    return arch, got, HEADER_BYTES + alen


# ------------------------------------------------------------------ 形状审计

def expected_shapes(d: Mapping[str, int], arch: str = M.ARCH_V4) -> dict[str, tuple[int, ...]]:
    """这套宽度下**应该**出现的每个张量名与形状（逐条核对，缺/多/形状不符都报错）。

    为什么要有这张表：`state_dict()` 是"训练代码的现状"，而推理端要的是"契约"。
    两者之间的任何漂移（改名、换层、少一个 bias）都必须在这里**当场**变成错误。

    ⚠ **两套拓扑**（`arch`）：`v4` = 现有图（**逐字节不变**）；`a-` = T1 新图
    （`ARCH-REDESIGN` §2.2）。段 2 起两者的 `net.bin` 都是**格式 3**（arch 段判拓扑）。
    """
    if arch == M.ARCH_A:
        return _expected_shapes_a(d)
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
        # ★ B3（第一百零四轮）：`u` 的 LayerNorm + 零初始化逐维残差门。**可缺**（旧网没有）⇒
        # 补 `weight=1 / bias=0 / u_res=0` ⇒ `u + tanh(0)·LN(u) = u` ⇒ 恒等（逐位已实测）。
        # ⚠ 补的 LN 必须是 **(1,0)** 而不是 0：补 0 会让 LN 输出恒 0 ⇒ 从 warm-start 学不动。
        "fusion.u_norm.weight": (None, (dm,)),
        "fusion.u_norm.bias": (None, (dm,)),
        "fusion.u_res": (None, (dm,)),
        # ★ #0（第一百零六轮）：34 行身份嵌入。**可缺**（旧包没有）⇒ 补 0 ⇒ 逐位不变；
        #   `--legacy-tile-id` 的网也不建它 ⇒ 两种张量表都合法（net.bin 逐字节复旧）。
        #   ⚠ Java/C++ 前向尚未认识这个张量（本批只做 Python 侧）。
        "tile.tile_id": (None, (34, ct)),
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


def _expected_shapes_a(d: Mapping[str, int]) -> dict[str, tuple[int, ...]]:
    """**A−（T1）**的张量表（`ARCH-REDESIGN` §2.2；93 个张量，含可缺的 `tile.tile_id`）。

    ⚠ 逐模块参数（与 §0.1 T1 列**逐项相等**）：tile 446,144（+`tile_id` 1,632）·
    evt 210,560 · fusion 216,064（+B3 384）· ctx 24,960 · cand 33,280 · heads 26,054。
    """
    dm, td, vb = int(d["d_model"]), int(d["tile_d"]), int(d["value_bins"])
    ct, ce, cc = spec.C_TILE, spec.C_EVT, spec.C_CAND
    out: dict[str, tuple[int, ...]] = {
        # ★ #0 身份嵌入：**可缺**（`--legacy-tile-id` 的网不建它 ⇒ 逐字节复旧）
        "tile.tile_id": (None, (34, ct)),
        # 34 轴卷积 + 3 个 pre-LN 残差块 + 轴向注意力 + 投影到 fusion 宽
        "tile.stem.weight": (td, ct, 5), "tile.stem.bias": (td,),
        "tile.stem_norm.weight": (td,), "tile.stem_norm.bias": (td,),
        "tile.res.0.norm.weight": (td,), "tile.res.0.norm.bias": (td,),
        "tile.res.0.conv.weight": (td, td, 5), "tile.res.0.conv.bias": (td,),
        "tile.res.1.norm.weight": (td,), "tile.res.1.norm.bias": (td,),
        "tile.res.1.conv.weight": (td, td, 3), "tile.res.1.conv.bias": (td,),
        "tile.res.2.norm.weight": (td,), "tile.res.2.norm.bias": (td,),
        "tile.res.2.conv.weight": (td, td, 3), "tile.res.2.conv.bias": (td,),
        "tile.attn_norm.weight": (td,), "tile.attn_norm.bias": (td,),
        "tile.attn.in_proj_weight": (3 * td, td), "tile.attn.in_proj_bias": (3 * td,),
        "tile.attn.out_proj.weight": (td, td), "tile.attn.out_proj.bias": (td,),
        "tile.proj.weight": (dm, td), "tile.proj.bias": (dm,),
        "tile.proj_norm.weight": (dm,), "tile.proj_norm.bias": (dm,),
        # 事件塔：Linear + GRUCell（增量 carry 契约不变）+ 2×Conv1d(k=3)+LN
        "event.enc.weight": (dm, ce), "event.enc.bias": (dm,),
        "event.cell.weight_ih": (3 * dm, dm), "event.cell.weight_hh": (3 * dm, dm),
        "event.cell.bias_ih": (3 * dm,), "event.cell.bias_hh": (3 * dm,),
        "event.conv1.weight": (dm, dm, 3), "event.conv1.bias": (dm,),
        "event.norm1.weight": (dm,), "event.norm1.bias": (dm,),
        "event.conv2.weight": (dm, dm, 3), "event.conv2.bias": (dm,),
        "event.norm2.weight": (dm,), "event.norm2.bias": (dm,),
        # ctx / cand 编码器：**首层 LN（C5）** + 2 个 Linear（死维/恒等维在首层被掩码挡住）
        "ctx.norm.weight": (spec.C_CTX,), "ctx.norm.bias": (spec.C_CTX,),
        "ctx.fc1.weight": (dm, spec.C_CTX), "ctx.fc1.bias": (dm,),
        "ctx.fc2.weight": (dm, dm), "ctx.fc2.bias": (dm,),
        "cand.norm.weight": (cc,), "cand.norm.bias": (cc,),
        "cand.fc1.weight": (dm, cc), "cand.fc1.bias": (dm,),
        "cand.fc2.weight": (dm, dm), "cand.fc2.bias": (dm,),
        # 95-token joint attention + FFN + cross + out
        "fusion.seg": (M.N_SEG, dm),
        "fusion.joint_norm.weight": (dm,), "fusion.joint_norm.bias": (dm,),
        "fusion.joint.in_proj_weight": (3 * dm, dm), "fusion.joint.in_proj_bias": (3 * dm,),
        "fusion.joint.out_proj.weight": (dm, dm), "fusion.joint.out_proj.bias": (dm,),
        "fusion.ffn_norm.weight": (dm,), "fusion.ffn_norm.bias": (dm,),
        "fusion.ffn1.weight": (dm, dm), "fusion.ffn1.bias": (dm,),
        "fusion.ffn2.weight": (dm, dm), "fusion.ffn2.bias": (dm,),
        "fusion.cross.in_proj_weight": (3 * dm, dm), "fusion.cross.in_proj_bias": (3 * dm,),
        "fusion.cross.out_proj.weight": (dm, dm), "fusion.cross.out_proj.bias": (dm,),
        "fusion.out.weight": (dm, 3 * dm), "fusion.out.bias": (dm,),
        "fusion.out_norm.weight": (3 * dm,), "fusion.out_norm.bias": (3 * dm,),
        # ★ B3 按 128 重算（+2×128+128 = +384）：**可缺**（`--legacy-u-res` 的网不建）
        "fusion.u_norm.weight": (None, (dm,)), "fusion.u_norm.bias": (None, (dm,)),
        "fusion.u_res": (None, (dm,)),
        # ★ S1（第一百一十六轮）：逐候选事件读出 = `Linear(d→2d, bias=False)` + `gamma(d)`
        #   = 32,768 + 128 = **32,896**。**可缺**（旧包没有 ⇒ arch 段 `evtReadout=0` ⇒ 不建）。
        "fusion.evt_kv.weight": (None, (2 * dm, dm)),
        "fusion.evt_gamma": (None, (dm,)),
        # ★ P4（第一百一十六轮）：`evt` 首层 LayerNorm(C_EVT) = 2×96 = **+192**。
        #   **可缺**（旧包没有 ⇒ arch 段 `evtLN=0` ⇒ 不建）。
        "event.norm0.weight": (None, (ce,)),
        "event.norm0.bias": (None, (ce,)),
        # 头（26,054）
        "heads.policy.weight": (1, dm), "heads.policy.bias": (1,),
        "heads.policy_gate.weight": (dm, 3), "heads.policy_gate.bias": (dm,),
        "heads.value.weight": (vb, dm), "heads.value.bias": (vb,),
    }
    for name, width in HEAD_WIDTHS.items():
        if name == "policy":
            continue
        out[f"heads.{name}.weight"] = (width, dm)
        out[f"heads.{name}.bias"] = (width,)
    assert ct == 48 and ce == 96
    return out


def arch_of(sd: Mapping[str, torch.Tensor]) -> str:
    """按**张量名**反推拓扑（`format 3` 起这只当**交叉复核**，判据在 arch 段）。

    判据是 `tile.stem.weight`：只有 A− 的 tile 塔有 34 轴卷积；v4 的 tile 塔是 `tile.enc.*`。
    ⚠ 两种都不是 ⇒ **报错**（不许猜成 v4 —— 猜错会让 `_check_shapes` 报一堆"缺 tile.enc.0"）。
    ⚠ **段 2 起 `read_net` 的判据是 arch 段的 `topoId`**，本函数只做一致性断言
      （`read_net` 里 `by_names != arch` ⇒ 报"arch 段与张量表自相矛盾"）。
    """
    if "tile.stem.weight" in sd:
        return M.ARCH_A
    if "tile.enc.0.weight" in sd:
        return M.ARCH_V4
    raise NetFormatError("认不出拓扑：既没有 `tile.stem.weight`（A−）也没有 `tile.enc.0.weight`（v4）")


def _check_shapes(sd: Mapping[str, torch.Tensor], d: Mapping[str, int], what: str,
                  arch: str = M.ARCH_V4) -> None:
    want = expected_shapes(d, arch)
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
    """从权重形状反推四个宽度（`nHeads` 反推不出来 —— 只是分组方式，必须外部给）。

    ⚠ `d_model` **从 `tile.proj.weight`（`[dm, td]`）反推**，别再从 policy 权重反推：
      第四十九轮起 policy 的输入是 `dm+3`（多拼了 3 个 belief 概率），而**旧网仍是 `dm` 宽**
      ⇒ 从它反推会把 d_model 读成 192 或 195 两种，取决于网的新旧。`tile.proj` 与新旧无关。
    ⚠ A− 的 `tile_d` 从 `tile.stem.weight`（`[td, 48, k]`）反推（`tile.enc.0` 不存在）。
    """
    if arch_of(sd) == M.ARCH_A:
        return {"d_model": int(sd["tile.proj.weight"].shape[0]),
                "tile_d": int(sd["tile.stem.weight"].shape[0]),
                "n_heads": M.A_N_HEADS,
                "value_bins": int(sd["heads.value.weight"].shape[0])}
    dm = int(sd["tile.proj.weight"].shape[0])
    return {"d_model": dm, "tile_d": int(sd["tile.enc.0.weight"].shape[0]),
            "n_heads": M.N_HEADS, "value_bins": int(sd["heads.value.weight"].shape[0])}


# ------------------------------------------------------------------ 权重：torch → 格式 3

def _block_table(bl: tuple[spec.Block, ...] = spec.BLOCKS) -> list[tuple[str, int]]:
    return [(b.bid, int(b.width)) for b in bl]


def fingerprint_of(bl: tuple[spec.Block, ...] = spec.BLOCKS) -> str:
    """**这一份块清单**的指纹（子集消融时与注册表全量指纹不同，两者都要能算）。"""
    return spec.fingerprint(bl)


def net_blob(sd: Mapping[str, torch.Tensor], dims: Mapping[str, int], *,
             blocks_: tuple[spec.Block, ...] = spec.BLOCKS,
             arch: str | None = None) -> bytes:
    """权重 state_dict → `net.bin` **格式 3**（**确定性**：同输入两次导出逐字节相同）。

    布局 = **64 B 头**（`format=3`，其余偏移与格式 2 相同）+ **arch 段（2,048 B）** + 块表 + 张量表。
    ⚠ `arch` 缺省 = 按张量名反推（`arch_of`）；写端**必写全** arch 段（`arch_profile`），
      并且先把 `params` 用张量表实算一遍再写进头部（读端会复核同一件事）。
    """
    d = {k: int(v) for k, v in dims.items()}
    a = arch_of(sd) if arch is None else str(arch)
    _check_shapes(sd, d, "net_blob", a)
    bl = _block_table(blocks_)
    tensors = sorted(((k, v.detach().cpu().numpy().astype("<f4", copy=False))
                      for k, v in sd.items()), key=lambda kv: kv[0])
    params = int(sum(int(np.prod(t.shape)) for _, t in tensors))
    arch_bytes = _pack_arch_segment(arch_profile(a, d, have=[k for k, _ in tensors]))
    parts = [struct.pack("<9I", NET_MAGIC, NET_FORMAT, spec.FEATURE_VERSION_V4,
                         spec.OBS_VERSION_V4, spec.DERIVED_VERSION_V4,
                         d["d_model"], d["tile_d"], d["n_heads"], d["value_bins"]),
             struct.pack("<2I", len(bl), len(tensors)),
             fingerprint_of(blocks_).encode("ascii"), struct.pack("<I", params),
             arch_bytes]
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
             extra: Mapping[str, Any] | None = None, arch: str | None = None) -> Path:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = {k: int(v) for k, v in dims.items()}
    a = arch_of(sd) if arch is None else str(arch)
    blob = net_blob(sd, d, blocks_=blocks_, arch=a)
    p.write_bytes(blob)
    prof = _arch_segment_fields(arch_profile(a, d, have=list(sd)))
    meta: dict[str, Any] = {
        "kind": "v4", "format": NET_FORMAT, "bytes": len(blob),
        "arch": a,
        # ★ 段 2：arch 段逐字段落盘（读端 `read_net` 用同一张表复核）
        "arch_segment": {"bytes": ARCH_SEG_BYTES, "core_bytes": ARCH_CORE_BYTES,
                         "fields": dict(sorted(prof.items()))},
        "feature_version": spec.FEATURE_VERSION_V4, "obs_version": spec.OBS_VERSION_V4,
        "derived_version": spec.DERIVED_VERSION_V4,
        "dims": d, "params": int(struct.unpack_from("<I", blob, 60)[0]),
        "blocks_fingerprint": fingerprint_of(blocks_),
        "registry_fingerprint": spec.fingerprint(),
        "blocks": [list(x) for x in _block_table(blocks_)],
        "contract_heads": list(M.contract_heads()),  # 旧 key 名 inference_heads（语义=训练与契约必需，2026-10-07 改名）
        "label": label, "source": source,
        "tensors": {k: list(v) for k, v in sorted(expected_shapes(d, a).items())},
    }
    if extra:
        meta.update(dict(extra))
    # ⚠ 显式 `newline="\n"`：Windows 上 `Path.write_text` 默认把 `\n` 翻成 `\r\n`，
    #   而仓库的 `.gitattributes` 是 `* text=auto eol=lf` —— 让**工作区**直接是 LF，
    #   免得同一份文件出现"工作区 CRLF / 索引 LF"的假改动（v1.12.0 那次 round.hpp 就踩过）。
    p.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                      encoding="utf-8", newline="\n")
    return p


def _check_shapes_f3(sd: Mapping[str, Any], d: Mapping[str, int], arch: str, flags: int,
                     what: str, arch_info: Mapping[str, Any] | None = None) -> None:
    """格式 3 读端的形状/张量集审计：**可缺组由 arch 段的 flags / 两个新字段决定**。

    与 `_check_shapes` 的区别（这是 §3 fail-loud ②③④ 的落点）：
      · `flags` 说"有" ⇒ 该组**必须存在**（缺 ⇒ 报"arch 声称有 … 但缺"）；
      · `flags` 说"没有" ⇒ 该组**必须不存在**（在 ⇒ 报"arch 没声明 … 但包里有"）；
      · 除这些组之外，名字/形状必须与 `expected_shapes(d, arch)`**逐条相等**（多/缺/形状不符都报）。
    ⚠ 不许退化成"可缺张量缺了就补 0"：补 0 会让前向跑一个 arch 没声明的分支。
    ⚠ 第一百一十六轮：`evtReadout`/`evtLN` 不是 `flags` 的位（它们占的是 arch 段保留区的两个字节）
    ⇒ 同一把双向尺子在这里也要对它们跑一遍（`arch_info` 缺席 ⇒ 只当 0，但 `read_net` 一定传）。
    """
    want = dict(expected_shapes(d, arch))
    required: list[tuple[str, int, tuple[str, ...], str]] = [
        ("hasB3", FLAG_B3, _B3_KEYS, "B3（`fusion.u_norm.*`/`fusion.u_res`）"),
        ("hasTileId", FLAG_TILE_ID, _TILE_ID_KEYS, "#0 身份嵌入（`tile.tile_id`）"),
        ("hasMemGate", FLAG_MEM_GATE, ("fusion.mem.weight", "fusion.mem.bias"),
         "长程记忆门（`fusion.mem.*`）"),
    ]
    got = {k: tuple(np.asarray(v).shape) for k, v in sd.items()}
    bad: list[str] = []
    for label, bit, keys, human in required:
        claimed = bool(int(flags) & bit)
        for k in keys:
            spec_ = want.get(k)
            alts = _shape_alts(spec_) if spec_ is not None else ()
            concrete = tuple(a for a in alts)
            if claimed:
                if not concrete:
                    raise NetFormatError(
                        f"arch 段声明 {label}=1（{human}）但 `{arch}` 的期望表里没有 `{k}` —— "
                        f"拓扑与 arch 段自相矛盾")
                want[k] = concrete[0]
                if k not in got:
                    raise NetFormatError(
                        f"arch 段声明 {label}=1（{human}），但包里**缺** `{k}`"
                        f"（⛔ 不补 0：那等于让前向跑一个 arch 没声明的分支）")
            else:
                if spec_ is not None:
                    want.pop(k, None)
                    if k in got:
                        raise NetFormatError(
                            f"arch 段声明 {label}=0（无{human}），但包里**有** `{k}` —— "
                            f"文件与水印（flags）不符")
    ai = arch_info or {}
    for label, keys, human in (("evtReadout", EVT_READOUT_KEYS,
                                "S1 逐候选事件读出（`fusion.evt_kv.*`/`fusion.evt_gamma`）"),
                               ("evtLN", EVT_LN_KEYS, "evt 首层 LN（`event.norm0.*`）")):
        claimed = bool(int(ai.get(label, 0) or 0))
        for k in keys:
            spec_ = want.get(k)
            alts = _shape_alts(spec_) if spec_ is not None else ()
            if claimed:
                if k not in got:
                    raise NetFormatError(
                        f"arch 段声明 `{label}`=1（{human}），但包里**缺** `{k}`"
                        f"（⛔ 不补 0：那等于让前向跑一个 arch 没声明的分支）")
            else:
                want.pop(k, None)
                if k in got:
                    raise NetFormatError(
                        f"arch 段声明 `{label}`=0（无{human}），但包里**有** `{k}` —— "
                        f"文件与水印不符")
    for k, s in want.items():
        alts = _shape_alts(s)
        if k not in got:
            bad.append(f"缺 {k}{alts[0] if alts else ''}")
        elif got[k] not in alts:
            bad.append(f"{k} 形状 {got[k]} != " + " 或 ".join(str(x) for x in alts))
    for k in got:
        if k not in want:
            bad.append(f"多 {k}{got[k]}")
    # ③ 段嵌入：arch 要求（`segEmbMode=1`）时**必须有** `fusion.seg`；不要求时必须没有。
    #    ⚠ PE（sin/cos 相对位置）是 **0 参数非持久 buffer**，net.bin 里根本没有它的位置 ⇒
    #    "PE 缺失"不可表达；它的三端一致性由 golden 的 logits 对拍钉住（段 3）。
    if arch == M.ARCH_A:
        if "fusion.seg" not in got:
            raise NetFormatError("arch 声明 `segEmbMode=1`（3 段嵌入）但缺 `fusion.seg`")
    if bad:
        raise NetFormatError(
            f"{what}：张量表与 `{arch}` 契约不符（{len(bad)} 条）：" + "；".join(bad[:8]))


def _shape_alts(spec_: Any) -> tuple[tuple[int, ...], ...]:
    """把 `expected_shapes` 的一项解成候选形状串（`(None,(dm,))` ⇒ 单元素串）。"""
    if isinstance(spec_, tuple) and spec_ and all(
            isinstance(a, tuple) or a is None for a in spec_):
        return tuple(a for a in spec_ if isinstance(a, tuple))
    return (tuple(int(x) for x in spec_),)


def _cursor(raw: bytes, off: int):
    """带**边界检查**的读游标：越界一律 `NetFormatError`（⑥ 截断/越界），⛔ 不许 struct 裸抛。"""
    class _C:
        def __init__(self) -> None:
            self.off = int(off)

        def need(self, n: int, what_: str) -> None:
            if self.off + n > len(raw):
                raise NetFormatError(
                    f"权重文件被截断/越界：读 {what_} 需要 {n} B（偏移 {self.off}），"
                    f"文件只有 {len(raw)} B")

        def u8(self, what_: str) -> int:
            self.need(1, what_)
            v = raw[self.off]
            self.off += 1
            return int(v)

        def u16(self, what_: str) -> int:
            self.need(2, what_)
            (v,) = struct.unpack_from("<H", raw, self.off)
            self.off += 2
            return int(v)

        def u32(self, what_: str) -> int:
            self.need(4, what_)
            (v,) = struct.unpack_from("<I", raw, self.off)
            self.off += 4
            return int(v)

        def text(self, n: int, what_: str) -> str:
            self.need(n, what_)
            b = raw[self.off:self.off + n]
            self.off += n
            try:
                return b.decode("utf-8")
            except UnicodeDecodeError as e:
                raise NetFormatError(f"读 {what_} 时 UTF-8 解码失败：{e}") from None

    return _C()


def read_net(path: str | Path) -> dict[str, Any]:
    """读 `net.bin` **格式 3** → 头部 + arch 段 + 块清单 + 张量表（`{名字: np.ndarray}`）。

    **硬切**（§3）：`format==1` ⇒ v3 的定长 MLP（仍走 `NeuralPolicy`，本函数不解析）；
    `format==2` ⇒ **专用错误**（A− 之前的老包，需要旧二进制/冻结快照）；其它 ⇒ 报版本号。
    ⛔ 三种都**不**自动升级、不补 0、不猜 arch。

    arch 的判据 = **arch 段**（`topoId`），不再是"按张量名反推"；
    `arch_of(张量表)` 只当**一致性断言**（两者不一致 ⇒ 文件在说谎）。
    """
    raw = Path(path).read_bytes()
    if len(raw) < HEADER_BYTES:
        raise NetFormatError(f"权重文件太短（{len(raw)} B < {HEADER_BYTES} B）")
    magic, fmt, feat, obs, derived, dm, td, nh, vb = struct.unpack_from("<9I", raw, 0)
    if magic != NET_MAGIC:
        raise NetFormatError(f"权重魔数不对：{magic:#x}（期望 {NET_MAGIC:#x}）")
    if fmt == NET_FORMAT_V3:
        raise NetFormatError(
            "权重格式版本 1 = v3 的定长 MLP（`magic` 相同、无块清单/无 arch 段）—— "
            "那是 `mahjong_ml.export.NeuralPolicy` 的路径，本模块不解析（⛔ 不猜）")
    if fmt == NET_FORMAT_RETIRED:
        raise NetFormatError(_RETIRED_FORMAT2)
    if fmt != NET_FORMAT:
        raise NetFormatError(
            f"权重格式版本 {fmt} 未知（本模块只认 {NET_FORMAT}；1 = v3、2 = 已退役）")
    arch, arch_info, off = _unpack_arch_segment(raw, feat=feat, obs=obs, derived=derived,
                                               dm=dm, td=td, nh=nh, vb=vb)
    n_blocks = int(struct.unpack_from("<I", raw, 36)[0])
    n_tensors = int(struct.unpack_from("<I", raw, 40)[0])
    fp = raw[44:60].decode("ascii", "replace")
    params = int(struct.unpack_from("<I", raw, 60)[0])
    if n_blocks > 10_000 or n_tensors > 10_000:
        raise NetFormatError(
            f"块/张量数越界：nBlocks={n_blocks} / nTensors={n_tensors}（上限 10,000）")
    cur = _cursor(raw, off)
    bl: list[tuple[str, int]] = []
    for _ in range(n_blocks):
        ln = cur.u16("块 id 长度")
        bid = cur.text(ln, "块 id")
        w = cur.u32("块宽")
        bl.append((bid, w))
    tensors: dict[str, np.ndarray] = {}
    for i in range(n_tensors):
        ln = cur.u16(f"第 {i} 个张量的名字长度")
        name = cur.text(ln, f"第 {i} 个张量的名字")
        nd = cur.u8(f"`{name}` 的维数")
        if nd > 8:
            raise NetFormatError(f"`{name}` 的维数 {nd} 不合理（>8）")
        shape: list[int] = []
        for _k in range(nd):
            shape.append(cur.u32(f"`{name}` 的形状"))
        n = int(np.prod(shape)) if shape else 1
        cur.need(4 * n, f"`{name}` 的数据")
        arr = np.frombuffer(raw, dtype="<f4", count=n, offset=cur.off).reshape(shape).copy()
        cur.off += 4 * n
        if name in tensors:
            raise NetFormatError(f"张量名重复：`{name}`（张量表必须是映射，⛔ 不许后者覆盖前者）")
        tensors[name] = arr
    if cur.off != len(raw):
        raise NetFormatError(
            f"权重文件尾部多了 {len(raw) - cur.off} 字节（张量表与头部/arch 段不符）")
    # ⑤ `params` 是**校验位**：必须等于张量表实算（⛔ 三端"读了不用"的时代结束）
    actual = int(sum(int(np.prod(np.asarray(v).shape)) for v in tensors.values()))
    if actual != params:
        raise NetFormatError(
            f"头部的 `params`={params:,} 与张量表实算 {actual:,} 不符（差 {actual - params:+,}）")
    # ④ 名字/形状 ↔ arch；②③ 可缺组与段嵌入
    _check_shapes_f3(tensors, {"d_model": dm, "tile_d": td, "n_heads": nh,
                              "value_bins": vb}, arch, int(arch_info["flags"]), "read_net",
                    arch_info=arch_info)
    # ⚠ 双路一致性断言：arch 段是**判据**，`arch_of` 只做交叉复核（⛔ 不许靠反推决定前向分支）
    by_names = arch_of({k: torch.from_numpy(np.ascontiguousarray(v))
                        for k, v in tensors.items()})
    if by_names != arch:
        raise NetFormatError(
            f"arch 段说 `{arch}`（topoId={arch_info['topoId']}），但张量名反推是 `{by_names}` "
            f"—— arch 段与张量表自相矛盾")
    return {"magic": magic, "format": fmt, "feature_version": feat, "obs_version": obs,
            "derived_version": derived,
            "dims": {"d_model": dm, "tile_d": td, "n_heads": nh, "value_bins": vb},
            "arch": arch, "arch_info": arch_info, "flags": int(arch_info["flags"]),
            # ★ 第一百一十六轮：两个**结构开关**（`build()` 直接吃它们；缺省都是 False = 旧图）
            "evt_readout": bool(int(arch_info["evtReadout"])),
            "evt_ln": bool(int(arch_info["evtLN"])),
            "blocks": bl, "fingerprint": fp, "params": params, "tensors": tensors,
            "arch_segment_bytes": int(arch_info["archLen"]), "bytes": len(raw)}


def normalize_state(sd: Mapping[str, torch.Tensor], arch: str | None = None
                    ) -> dict[str, torch.Tensor]:
    """把**任意一代**的状态字典归一成**当前模型**的形状 —— 所有加载点共用这一处。

    处理三件跨代差异（第五十/五十五/六十轮）：

    1. `heads.policy.weight` 可能是 `[1, dm]`（第一季及以前、第五十五轮起）或 `[1, dm+3]`
       （第五十轮那版"拼接 sigmoid(belief_tenpai)"）⇒ `dm+3` **截断**：那 3 列乘的是逐行常数
       ⇒ 对 argmax/softmax 无贡献（红证见 NOTES §6.5），丢掉等价；
    2. `heads.policy_gate.{weight,bias}`（第五十五轮加的逐候选门控）**可能不存在** ⇒ 补 **0**
       ⇒ `g = 1 + tanh(0) = 1` ⇒ 恒等（不给旧权重强加新行为，`strict=True` 也不会红）；
    3. `fusion.mem.{weight,bias}`（W1 加的长程记忆门控，第六十轮）同上：**可能不存在** ⇒ 补 **0**
       ⇒ `g_mem = 1 + tanh(0) = 1` ⇒ 恒等（旧网的前向**逐位不变**）。

    ★ **段 2 起按 arch 分流**（第一百零八轮）：
      · `arch=v4` ⇒ 上面三条"缺 ⇒ 补恒等"（旧包照旧能载）；
      · `arch=a-` ⇒ **必须是全套**：`fusion.u_norm.*`/`fusion.u_res`/`heads.policy_gate.*` 缺任一
        ⇒ **报错**（⛔ 不补 0：B3/门控是 T1 参数账的一部分，补 0 等于静默换掉前向语义）。
        只有 `tile.tile_id` 仍可缺（`--legacy-tile-id` 的**构造期**开关，缺 ⇒ 0 = `tile + 0` 恒等）。

    ⚠ 别把这段逻辑抄到各处：`state_from_net`（net.bin）与 `value_audit.load_model`（ckpt）
    都必须走它 —— 抄漏一处就是"某些旧权重能载入、某些不能"的鬼故事（实测踩过两次：
    `value_audit` 直接 `strict=True` 载 ckpt ⇒ `Missing key(s): heads.policy_gate.*`）。
    """
    out = dict(sd)
    a = arch_of(out) if arch is None else str(arch)
    dm = int(out["tile.proj.weight"].shape[0])
    w = out.get("heads.policy.weight")
    if w is not None and w.ndim == 2:
        if int(w.shape[1]) == dm + 3:
            out["heads.policy.weight"] = w[:, :dm].contiguous()
        elif int(w.shape[1]) != dm:
            raise NetFormatError(f"heads.policy.weight 宽度 {int(w.shape[1])} 既不是 {dm} "
                                 f"也不是旧版 {dm + 3}")
    dtype = out["tile.proj.weight"].dtype
    if a == M.ARCH_A:
        # ★ 段 2：**补 0 规则按 arch 分流**。
        #   A− 的 B3（`u_norm`/`u_res`）与 `policy_gate` 是 T1 拓扑的一部分（参数账里算着），
        #   **缺了不许补 0** —— 补 0 等于把 B3 静默降级成恒等、换掉前向语义。
        missing = [k for k in _B3_KEYS if k not in out]
        missing += [k for k in ("heads.policy_gate.weight", "heads.policy_gate.bias")
                    if k not in out]
        if missing:
            raise NetFormatError(
                f"`a-` 的权重缺 {len(missing)} 个**拓扑必需**张量：{missing[:6]} —— "
                f"a- 必须是全套（B3 = `u_norm`/`u_res`、`policy_gate`）；⛔ 不补 0 / 不猜")
        # `tile.tile_id` 是 `--legacy-tile-id` 的**构造期**开关：缺 ⇒ 0 = 恒等（`tile + 0`）
        if "tile.tile_id" not in out:
            out["tile.tile_id"] = torch.zeros(34, spec.C_TILE, dtype=dtype)
        # ★ 第一百一十六轮：S1 读出 / evt LN 也是**构造期**开关（`arch_kwargs` 从这里/`read_net`
        #   的 arch 段决定建不建）。⛔ 这里**不补**它们：补一组 0 会让前向跑一个 arch 没声明的
        #   分支（B3 的教训），而 `adapt_to_model` 会把"目标网没有、字典里有"的那几组丢掉。
        return out
    # 可缺张量（旧网没有）⇒ 补"恒等"：加新的可缺张量时**只需要动这张表**。
    # ⚠ 第三列是**补什么**：0 ⇒ 恒等门（`1+tanh(0)=1` / `u+tanh(0)·…=u`）；
    #   1 ⇒ LN 的 `weight` 恒等（1,0）。⛔ 别把 LN 的 weight 补 0（那会让 LN 输出恒 0）。
    for name, shape, fill in (
            ("heads.policy_gate.weight", (dm, 3), 0.0), ("heads.policy_gate.bias", (dm,), 0.0),
            ("fusion.mem.weight", (dm, dm), 0.0), ("fusion.mem.bias", (dm,), 0.0),
            # ★ B3：旧包缺这三组 ⇒ LN 用 (1,0)、残差门用 0 ⇒ 恒等
            ("fusion.u_norm.weight", (dm,), 1.0), ("fusion.u_norm.bias", (dm,), 0.0),
            ("fusion.u_res", (dm,), 0.0),
            # ★ #0（第一百零六轮）：旧包缺身份嵌入 ⇒ 补 **0** ⇒ `tile + 0` 逐位不变
            #   （形状 = `[34, C_TILE]`，与 `TileTower` 的输入宽同一来源）。
            ("tile.tile_id", (34, spec.C_TILE), 0.0)):
        if name not in out:
            out[name] = torch.full(shape, fill, dtype=dtype)
    return out


def state_from_net(parsed: Mapping[str, Any]) -> dict[str, torch.Tensor]:
    """张量表 → `state_dict()`（自检用它做"导出 → 读回 → 前向"的闭环）。

    跨代差异（policy 宽度 / 可缺的门控张量）统一交给 `normalize_state`，见那里的注释。
    """
    return normalize_state({k: torch.from_numpy(np.ascontiguousarray(v))
                            for k, v in parsed["tensors"].items()})


#: `--legacy-u-res` 的网**没有**这三组张量（B3 在构造期就不建）；把新包载入那种网时
#: 只允许**这三组**多出来（其余多/少一律照旧报错 —— ⛔ 别把这条放宽成"多余键都忽略"）。
_B3_KEYS = ("fusion.u_norm.weight", "fusion.u_norm.bias", "fusion.u_res")
#: 同理：`--legacy-tile-id` 的网没有 `tile.tile_id`（#0 在构造期就不建）⇒ 新包载入它时只多这一组。
_TILE_ID_KEYS = ("tile.tile_id",)
#: 第一百一十六轮：S1 读出 / P4 的 evt LN 同样是**构造期可缺**的两组（`--legacy-evt-readout` /
#: `--legacy-evt-ln` 或旧包）。新包载入"不建这两条路径"的网时只允许这四组多出来。
_OPTIONAL_KEYS = _B3_KEYS + _TILE_ID_KEYS + EVT_READOUT_KEYS + EVT_LN_KEYS


def arch_kwargs(src: Mapping[str, Any]) -> dict[str, Any]:
    """`build()` 的**结构开关**（从 `read_net` 的结果或 ckpt 的 `config` 里取；缺 ⇒ 旧图）。

    ⚠ 用途是**让建成什么图跟着包走**：旧包（`evtReadout=0`/`evtLN=0`）⇒ 不建那两组张量 ⇒
    `strict=True` 能载、且前向**逐位**走旧路（不是"建了再靠 0 抵消"）。
    """
    return {"evt_readout": bool(src.get("evt_readout", False)),
            "evt_ln": bool(src.get("evt_ln", False))}


def adapt_to_model(sd: Mapping[str, torch.Tensor], model: torch.nn.Module,
                   *, what: str = "") -> dict[str, torch.Tensor]:
    """把（可能来自新包的）状态字典适配到**这张网的张量集**上。

    存在的唯一理由 = **构造期可缺的两类张量**：
    - **B3 的 legacy 网**（`--legacy-u-res` 不建 `u_norm`/`u_res`）；
    - **#0 的 legacy 网**（`--legacy-tile-id` 不建 `tile_id`）。
    新包里有、目标网没有 ⇒ 丢掉；反过来（目标有、字典缺）`normalize_state` 已经补过
    (1,0)/0 ⇒ 不会走到"缺键"。⚠ 除这两类之外的任何多余键仍然**报错**（保持 fail-closed）。
    """
    want = set(model.state_dict())
    have = set(sd)
    missing = sorted(want - have)
    if missing:
        raise NetFormatError(f"{what or '权重'} 缺 {len(missing)} 个张量：{missing[:6]}")
    extra = sorted(have - want)
    bad = [k for k in extra if k not in _OPTIONAL_KEYS]
    if bad:
        raise NetFormatError(f"{what or '权重'} 多出 {len(bad)} 个张量：{bad[:6]}")
    return {k: v for k, v in sd.items() if k in want}


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
            # ⚠ 停止条件必须**包含覆盖闸门**：`seen_tags` 只在"这条被选走"时才更新
            #   （见上面的 `seen_tags |= tags`），所以它 == 这批用例**真正覆盖到**的标签集。
            #   少了这个条件，新加的必须覆盖项就只是"扫的时候恰好捡到了没有"——
            #   一个出现在后面文件里的 `legal_kan` 会被 `len(picked) >= want` 提前掐掉，
            #   于是闸门报"覆盖不足"，而其实只是**没去找**。
            if len(picked) >= want and long_seen and set(REQUIRED_TAGS) <= seen_tags:
                return picked
    return picked


#: 夹具**必须**覆盖的状态标签（覆盖不到就报错 —— 免得夹具"看着有 7 条"却全在一条分支上）
#: ⚠ `evt_short`（事件流 ≤8 条）是 W1 加的：窗口前部是**零 padding**，而"喂不喂 padding"
#:   在**满窗**用例上被 GRU 的收缩性洗掉（实测 |Δh|=1.3e-7，判据看不见）；只有在
#:   "绝大多数是 padding、真实事件只有几条"的用例上才会放大到 1e-2 量级 ⇒ 缺了它，
#:   三端把 padding 也喂进 GRU 也照样全绿。
#: ⚠ `legal_kan`（R2 加的）：夹具里必须有 `kan:` 候选。缺了它，`cand` 的**候选键解析**这一路
#:   （`kan:daiminkan:a+b+c` 的主牌 one-hot + 取法计数）在 Java/C++/Python 三端**没有任何用例**
#:   —— 实测漏掉了一个真 bug（51/13,408 行 logit 差 4.4~10、1 行 argmax 翻转）。
#:   ⚠ 这一条**必须**同时进 `_iter_cases` 的停止条件（见那里的注释），否则它只是在事后检查
#:   "恰好捡到了没有"，而不是"必须捡到"。
REQUIRED_TAGS = ("turn", "claim", "own_meld", "riichi_any", "ippatsu", "evt_short", "legal_kan")


def _tags(obs: Mapping[str, Any]) -> set[str]:
    """一条决策"走了哪些特征分支"（选例与覆盖闸门都用它）。"""
    tags = {str(obs.get("kind"))}
    # ⚠ **候选侧也要有覆盖维度**：夹具原来 10 例的 `legal` 里**一个 `kan:` 都没有** ⇒
    #   大明杠候选的主牌/取法两处（`features.cand_vector` 与 Java `Features.candidate`）
    #   在**三端 parity 上结构性地抓不到** —— 实测就漏了 51/13,408 行、1 行 argmax 翻转，
    #   而夹具一直全绿（NOTES §6.5）。候选键是独立的语义面，不能只按 obs 的状态分支打标签。
    if any(str(k).startswith("kan:") for k in (obs.get("legal") or [])):
        tags.add("legal_kan")
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


#: **golden 三档**（`A-REFACTOR-IMPL-PLAN` §3）：`--fixture-semantics` 的取值 → `(arch, semantics)`。
#: · `c2`      = **G0**：冻结 C2 语义（部署端 `fusion.out` 仍带 ReLU）——只给冻结快照/`wl-*` 回归；
#: · `current` = **G1**：当前 v4 图 + 当前语义，**format 3**（跨端要等段 3）；
#: · `a-`      = **G2**：A−（T1）图，**format 3**，配真实 3,083 决策语料（R-4）。
FIXTURE_SEMANTICS: dict[str, tuple[str, str]] = {
    "c2": (M.ARCH_V4, "c2"),
    "current": (M.ARCH_V4, "current"),
    "a-": (M.ARCH_A, "current"),
}


def build_golden(trace_dir: str | Path, out: str | Path, *, cases: int = 8,
                 dims: Mapping[str, int] | None = None, seed: int = 20260928,
                 scan_files: int = 8, semantics: str = "current",
                 fixture_semantics: str = "current") -> Path:
    """真实轨迹 → golden 夹具（小网 + 四张量 + 四个推理头输出）。

    `fixture_semantics` = **三档之一**（`c2|current|a-`，见 `FIXTURE_SEMANTICS`）：
    它同时决定 **arch**（v4 / a-）与**语义档**（`model.SEMANTICS`）。夹具的权重与 Python 参考
    输出必须按**指定**档生成，⛔ 不许"生成时恰好是缺省语义"就当对拍通过。
    · G0（`c2`）：仓库夹具 `407BBE21…`（**format 2**）的语义来源；本函数重生的是 **format 3** 版，
      只给新读端用（冻结 jar/exe 仍读旧文件）。
    · G1（`current`）：当前 v4 图；`net.bin` 是 format 3 ⇒ **跨端一致要等段 3**。
    · G2（`a-`）：A− 图（value ±60/81 bin）；配 R-4 的 3,083 决策语料，同样 format 3。
    ⚠ `dims` 缺省按档取小网：v4 = `GOLDEN_DIMS`（32/16/2/5）、a- = `GOLDEN_DIMS_A`（32/16/2/81）；
      给 `dims` 就以它为准（`--full` 用上线宽度）。
    ⚠ `semantics`（旧参数）只在 `fixture_semantics` 是缺省值时生效，供消融档（`c1|c3|b3|...`）用。
    """
    if fixture_semantics not in FIXTURE_SEMANTICS:
        raise NetFormatError(
            f"未知 fixture-semantics {fixture_semantics!r}（可选：{sorted(FIXTURE_SEMANTICS)}）")
    arch, sem = FIXTURE_SEMANTICS[fixture_semantics]
    if semantics != "current":
        sem = semantics                       # 旧口子：显式语义档（只对 v4 有意义）
    d = dict(dims or (GOLDEN_DIMS_A if arch == M.ARCH_A else GOLDEN_DIMS))
    saved = (M.LEGACY_OUT_RELU, M.LEGACY_ZERO_INIT, M.LEGACY_U_RES, M.ARCH)
    M.apply_semantics(sem)
    M.set_arch(arch)
    try:
        torch.manual_seed(seed)
        # ★ 第一百一十六轮：a- 的夹具**必须带上**两条新结构（S1 读出 + evt LN）——
        #   否则"新路径一个字都没被测到"正是最贵的假绿；v4 的图里没有它们，严格不传。
        #   ⚠ `evt_readout_init="zero"` 是**故意的**：下面那条"凡 max|·|==0 的参数一律
        #   `normal_(0,0.5)`"才会把 `evt_gamma` 扰动成**非恒等**（照缺省 `small` 建的话
        #   γ≈1e-3 ⇒ tanh(γ)≈1e-3 ⇒ 读出路径在夹具里几乎不起作用，等于没测）。
        net = M.build(seed, arch=arch, **d,
                      **({"evt_readout": True, "evt_ln": True, "evt_readout_init": "zero"}
                         if arch == M.ARCH_A else {}))
    finally:
        # 只影响**构造**：`Fusion`/`V4Model` 在 __init__ 里已把开关存成实例属性，
        # 后续前向（夹具的参考输出）不受进程级全局影响 ⇒ 立刻还原，避免污染同进程的别的网。
        M.set_legacy_out_relu(saved[0])
        M.set_legacy_zero_init(saved[1])
        M.set_legacy_u_res(saved[2])
        M.set_arch(saved[3])
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
        if arch == M.ARCH_A:
            # A− 的融合是 joint(95 token) + cross(cand→95)；「候选 → 注意力 → 策略」这条路上
            # 两处注意力都要变尖（`q_tile`/`q_evt` 在 A− 里不存在 ⇒ 不能照抄 v4 的名字）。
            for name in ("fusion.joint.in_proj_weight", "fusion.joint.in_proj_bias",
                         "fusion.cross.in_proj_weight", "fusion.cross.in_proj_bias"):
                pr[name] *= 20.0
        else:
            for name in ("fusion.q_tile.in_proj_weight", "fusion.q_tile.in_proj_bias",
                         "fusion.q_evt.in_proj_weight", "fusion.q_evt.in_proj_bias"):
                pr[name] *= 20.0
    sd = net.state_dict()
    blob = net_blob(sd, d, arch=arch)

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
            # C2：state 级头缺省 mask-aware 池化 ⇒ 显式声明"这一条没有 padding"。
            # 单条用例的 L 就是真实候选数 ⇒ 与旧的 `u.mean(1)` 逐位相同（夹具字节不变）。
            full_kw = {"mask": torch.ones(1, t.cand.shape[0], dtype=torch.bool)}
            o = net(*args, h=h0[None], **full_kw)
            # 红证①（**夹具必须能测到"事件隐状态真的进决策"**）：
            #   · v4：把 `fusion.mem.weight` 归零 ⇒ 策略头必须变；
            #   · A−：没有 `fusion.mem` 门（`h_evt` 是第 95 个 token）⇒ 归零 `fusion.seg[SEG_MEM]`
            #     那一行段嵌入，该 token 的"记忆身份"消失 ⇒ 策略头必须变。
            if arch == M.ARCH_A:
                seg_p = net.fusion.seg
                if float(seg_p.detach().abs().sum()) == 0.0:
                    raise NetFormatError("夹具空转：`fusion.seg` 全 0（段嵌入没被扰动）")
                seg_row = seg_p[M.SEG_MEM].detach().clone()
                with torch.no_grad():
                    seg_p[M.SEG_MEM].zero_()
                o0 = net(*args, h=h0[None], **full_kw)
                with torch.no_grad():
                    seg_p[M.SEG_MEM].copy_(seg_row)
                fin = torch.isfinite(o["policy"] - o0["policy"])
                mem_probe = max(mem_probe, float((o["policy"] - o0["policy"])[fin].abs().max()))
            else:
                if float(net.fusion.mem.weight.abs().sum()) == 0.0:
                    raise NetFormatError("夹具空转：`fusion.mem.weight` 全 0（零初始化没被扰动）")
                saved = net.fusion.mem.weight.detach().clone()
                net.fusion.mem.weight.zero_()
                o0 = net(*args, h=h0[None], **full_kw)
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
                o1 = net(*args, h=(h0 + 0.5)[None], **full_kw)
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
            f"夹具空转：归零「事件隐状态那条接线」只让策略头动了 {mem_probe:.3g}（应 >1e-4）—— "
            + ("A−：`fusion.seg` 是否被随机化？joint attention 是否真的消费了第 95 个 token？"
               if arch == M.ARCH_A else
               "`fusion.mem.*` 是否被随机化？`Fusion` 是否真的消费了 `h_evt`？"))
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
            f"注意力太接近均匀（融合两处 in_proj 没放大？），候选差异没有进 logits")
    head_b = struct.pack("<4I", GOLDEN_MAGIC, GOLDEN_FORMAT, len(rows), len(blob))
    p.write_bytes(head_b + blob + bytes(body))
    meta = {"cases": len(rows), "format": GOLDEN_FORMAT,
            "fixture_semantics": fixture_semantics, "arch": arch, "semantics": sem,
            # ⚠ 内嵌 net 的格式与夹具**容器**格式是两件事：容器 2 = 用例带 h0/h_evt；
            #   内嵌 net 段 2 起是 **3**（旧 C++/Java 读不了 ⇒ G1 的跨端一致留到段 3）。
            "net_format": NET_FORMAT, "net_arch_segment_bytes": ARCH_SEG_BYTES,
            "dims": d, "net_bytes": len(blob),
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
    w = sub.add_parser("weights", help="v4 checkpoint → net.bin 格式 3（含 arch 段）")
    w.add_argument("--ckpt", required=True)
    w.add_argument("--out", required=True)
    w.add_argument("--label", default="")
    w.add_argument("--n-heads", type=int, default=0, help="缺省从 checkpoint 的 config.dims 读，再缺省 4")
    g = sub.add_parser("golden", help="真实轨迹 → golden 对拍夹具（G0/G1/G2 三档）")
    g.add_argument("--trace", required=True)
    g.add_argument("--out", required=True)
    g.add_argument("--cases", type=int, default=8)
    g.add_argument("--scan-files", type=int, default=8, help="最多扫几个轨迹文件来找齐覆盖面")
    g.add_argument("--full", action="store_true", help="用上线架构（v4 192/64/4/51；a- 128/160/4/81）")
    g.add_argument("--seed", type=int, default=20260928)
    g.add_argument("--fixture-semantics", choices=sorted(FIXTURE_SEMANTICS), default="current",
                   help="三档：c2 = G0（冻结 C2 语义）· current = G1（format 3 新图）· "
                        "a- = G2（A− 图 + 真实 3,083 决策语料）")
    g.add_argument("--semantics", choices=sorted(M.SEMANTICS), default="current",
                   help="（旧口子）显式语义档；只在 `--fixture-semantics` 用缺省值时覆盖语义部分")
    i = sub.add_parser("info", help="只读打印 net.bin 的头部 + arch 段（format==2 ⇒ 专用错误、退出码 2）")
    i.add_argument("--net", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "info":
        try:
            p = read_net(a.net)
        except NetFormatError as e:
            print(f"读不了 {a.net}：{e}", file=sys.stderr)
            return 2
        fi = p["arch_info"]
        print(f"{a.net}：format={p['format']} · arch={p['arch']}（topoId={fi['topoId']}）· "
              f"params={p['params']:,} · {p['bytes']:,} B")
        print(f"  dims={p['dims']} · arch 段 {p['arch_segment_bytes']} B · flags={p['flags']:#04x} · "
              f"blocks={len(p['blocks'])} · tensors={len(p['tensors'])}")
        print("  arch 字段：" + " ".join(f"{k}={fi[k]}" for _o, k, _t, _s in _ARCH_FIELDS
                                       if k not in ("archLen",)))
        return 0
    if a.cmd == "weights":
        ck = torch.load(a.ckpt, map_location="cpu", weights_only=False)
        sd = ck["model"]
        cfg = ck.get("config") or {}
        d = dict(cfg.get("dims") or dims_from_state(sd))
        if a.n_heads:
            d["n_heads"] = a.n_heads
        p = save_net(sd, d, a.out, label=a.label or Path(a.ckpt).parent.name,
                     source=str(a.ckpt), arch=cfg.get("arch"))
        print(f"已导出 v4 权重：{p}（{p.stat().st_size} 字节，格式 {NET_FORMAT}，"
              f"arch={json.loads(p.with_suffix('.json').read_text(encoding='utf-8'))['arch']}，"
              f"{json.loads(p.with_suffix('.json').read_text(encoding='utf-8'))['params']:,} 参数，"
              f"dims={d}）")
        # ⚠ 段 2 只做 Python 侧：C++/Java 仍认 format 2 ⇒ 这份包现在两端都读不了（段 3 才同步）
        print("  ⚠ format 3：C++/Java 尚未同步（段 3）⇒ 这份包现在**不能**给 server/trainer 用")
    else:
        arch = FIXTURE_SEMANTICS[a.fixture_semantics][0]
        dims = ((FULL_DIMS if arch == M.ARCH_V4 else
                 {"d_model": M.A_D_MODEL, "tile_d": M.A_TILE_D,
                  "n_heads": M.A_N_HEADS, "value_bins": M.A_VALUE_BINS})
                if a.full else None)
        p = build_golden(a.trace, a.out, cases=a.cases, scan_files=a.scan_files,
                         dims=dims, seed=a.seed, semantics=a.semantics,
                         fixture_semantics=a.fixture_semantics)
        # ⚠ 用例数可能**多于** `--cases`：`_iter_cases` 要凑齐覆盖闸门（含"事件流长于窗口"那条）
        #   才会停 ⇒ 报数从写出的 meta 读，别报 `--cases`（那会与实际不符）。
        m = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
        print(f"已写出 golden 夹具：{p}（{p.stat().st_size} 字节，{m['cases']} 个用例，"
              f"fixture_semantics={m['fixture_semantics']}，arch={m['arch']}，"
              f"内嵌 net format {m['net_format']}，{m['net_bytes']} B）")
        print("  ⚠ 内嵌 net 是 format 3 ⇒ 跨端（Java/C++）一致要等段 3；"
              "G0 的冻结二进制仍读仓库里的 format-2 夹具。")
    return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
