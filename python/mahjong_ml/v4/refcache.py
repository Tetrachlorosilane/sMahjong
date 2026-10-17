"""**零算力跨臂缓存**：π_ref logits 与 V_old 的磁盘复用（MV-1 效率包 ①）。

为什么需要它：MV-1 的两臂（base/aux）用**同一份数据、同一 `--init`、同 seed**，只有
`--aux-own-win-weight` 一个开关不同；而 `_ref_logits()`（π_ref 的冻结 logits，实测 67.3 s）与
`_behaviour_values()`（V_old，实测 48.6 s）**只取决于「数据 + 权重 + 前向口径」**，与臂名、
训练步数、辅助头**无关** ⇒ 第二臂可以把它们整份读回来。

## 三条纪律（少一条就等于把别人的残渣当自己的读数）

① **键必须钉死，不匹配就拒绝**：缓存里存的不是"文件名"，而是**完整的键字典**（数据清单
   sha256 / 权重 state 指纹 / 温度 / seed / 代码指纹 / 前向参数）；命中 = **键字典逐字段相等**
   **且** 内容 sha256 对得上。任何一项不同 ⇒ 拒用（fail-closed）并打印原因 ⇒ 重算。
   ⛔ 没有"差不多就用"这一档。
② **写必须原子**：先写同目录的 `.<key>.<pid>.<rand>.tmp`，`flush + fsync` 后 `os.replace`。
   中断/并发都只会留下**没人认领的 tmp**（从不被读），绝不会留下半份正式文件。
③ **默认关闭**：`--cache-ref-logits <dir>` 不传 ⇒ 本模块一行都不执行（缺省路径逐字节不变）。

⚠ 为什么键里要 `label` **之外**的一切：`--label` 只决定 ckpt 子目录名，**不影响**这两个量；
把它写进键只会让"换了个名字"白白重算。反过来 `--seed` 虽然也不影响数值（前向是确定性的、
`no_grad`），仍按判据**写进键** —— 宁可少复用，不可多复用。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import numpy as np

#: 缓存格式版本。**改了缓存的内容/口径就 +1** ⇒ 旧文件自动键不匹配（不必手动清目录）。
CACHE_VERSION = 1

#: 宽张量只抽头/中/尾各这么多字节参与指纹（全量 12.4 GB 要 ~25 s，见 EFFICIENCY-AUDIT 实现结果）。
SAMPLE_BYTES = 4 << 20

#: 小于这个大小的张量**全量**哈希（逐决策的小列：label / nlegal / is_student / 奖励列…）。
FULL_HASH_MAX = 16 << 20


def _sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sampled_sha256(path) -> str:
    """大文件的内容指纹：**头 / 中 / 尾**各 `SAMPLE_BYTES`（外加精确字节数）。

    为什么不全量：全量 sha256 要读 12.4 GB（本机 592 MiB/s ⇒ ~25 s），而它保护的是一份
    67 s 的产物 ⇒ 光算键就吃掉 37% 的收益。取样的口径在 `EFFICIENCY-AUDIT.md`「实现结果」
    里写死（覆盖率 = 3×4 MiB / 文件大小），⛔ 不声称"等于全量 sha256"。
    """
    size = os.path.getsize(path)
    h = hashlib.sha256()
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(SAMPLE_BYTES))
        if size > 2 * SAMPLE_BYTES:
            f.seek(size // 2 - SAMPLE_BYTES // 2)
            h.update(f.read(SAMPLE_BYTES))
        if size > SAMPLE_BYTES:
            f.seek(size - SAMPLE_BYTES)
            h.update(f.read(SAMPLE_BYTES))
    return h.hexdigest()


def content_digest(path) -> str:
    """文件内容指纹（小的全量、大的取样）。返回值写进键字典。"""
    path = Path(path)
    return _sha256_file(path) if path.stat().st_size <= FULL_HASH_MAX else _sampled_sha256(path)


def split_manifest_sha256(data_dir, split: str) -> str:
    """一份切分（`train` / `val`）的**数据指纹**（键字段 `data_sha256` 的来源）。

    口径（逐项写进摘要，⛔ 不依赖 mtime）：
      · 该切分下**每个** `<split>.*.npy` 的文件名 · dtype · shape · 字节数 · `content_digest`；
      · `meta.json` 的**全量** sha256（它记着 feature_version / lmax / 切分种子 / 文件清单）。
    ⇒ 数据被重写（换列、换行、换值、换顺序）就会变；只换个目录名（路径不进摘要）不会变。
    """
    d = Path(data_dir)
    rows = []
    for p in sorted(d.glob(f"{split}.*.npy")):
        a = np.load(p, mmap_mode="r", allow_pickle=False)
        rows.append({"file": p.name, "dtype": a.dtype.str, "shape": list(a.shape),
                     "nbytes": int(p.stat().st_size), "sha256": content_digest(p)})
    meta = d / "meta.json"
    payload = {"split": str(split), "files": rows,
               "meta_sha256": (_sha256_file(meta) if meta.exists() else None)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def code_fingerprint(*paths) -> str:
    """产生缓存的那份代码的指纹（`pretrain.py` + 它依赖的 v4 模块）。"""
    rows = []
    for p in paths:
        p = Path(p)
        rows.append({"file": p.name, "sha256": _sha256_file(p) if p.exists() else None})
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def make_key(kind: str, fields: dict) -> tuple[str, dict]:
    """键 = `sha256(canonical_json(键字典))`；返回 `(key, keydict)`（keydict 要**整份存进文件**）。"""
    kd = {"kind": str(kind), "version": int(CACHE_VERSION), **fields}
    canon = json.dumps(kd, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode()).hexdigest(), kd


def _why_mismatch(got: dict, want: dict) -> str:
    """逐字段列出不同项（打印用；⛔ 只给前 4 条，免得刷屏）。"""
    diffs = [f"{k}: 缓存 {got.get(k)!r} ≠ 本次 {want.get(k)!r}"
             for k in sorted(set(got) | set(want)) if got.get(k) != want.get(k)]
    return "；".join(diffs[:4]) + ("…" if len(diffs) > 4 else "")


def load(cache_dir, kind: str, key: str, keydict: dict) -> dict | None:
    """读缓存。**fail-closed**：任何一处对不上 ⇒ 打印原因、返回 `None`（调用方重算）。

    返回 `{name: np.ndarray}`；`None` = 不能用（未命中 / 键不匹配 / 损坏 / 形状不对）。
    """
    path = Path(cache_dir) / f"{kind}.{key}.npz"
    if not path.exists():
        print(f"[cache] 未命中：{kind}（{path.name} 不存在）⇒ 重算")
        return None
    try:
        with np.load(path, allow_pickle=False) as z:
            got = json.loads(str(z["__keyjson__"]))
            if got != keydict:
                print(f"[cache] ⛔ 键不匹配 ⇒ **拒用**并重算：{_why_mismatch(got, keydict)}")
                return None
            if str(z["__key__"]) != key:
                print(f"[cache] ⛔ 文件名与内容里的键不一致（{key[:16]}… vs "
                      f"{str(z['__key__'])[:16]}…）⇒ 拒用并重算")
                return None
            arrays = {n: z[n] for n in z.files if not n.startswith("__")}
            digests = json.loads(str(z["__sha256__"]))       # {数组名: 内容 sha256}（JSON，⛔ 不用
            bad = [n for n, a in arrays.items()               #    object 数组 —— 那要 allow_pickle）
                   if digests.get(n) != hashlib.sha256(
                       np.ascontiguousarray(a).tobytes()).hexdigest()]
            if bad:
                print(f"[cache] ⛔ 内容 sha256 对不上（{bad}）⇒ 文件被截断/改坏，拒用并重算")
                return None
    except Exception as e:                      # 截断的 npz / 坏 header / 非 npz
        print(f"[cache] ⛔ 缓存文件读不出来（{type(e).__name__}: {e}）⇒ 当作损坏，拒用并重算")
        return None
    print(f"[cache] ✅ 命中：{kind}（{path.name}，键 {key[:16]}…）—— 省去重算")
    return arrays


def store(cache_dir, kind: str, key: str, keydict: dict, arrays: dict) -> Path | None:
    """写缓存（**原子**：`.tmp` + `os.replace`）。返回最终路径；写不了就打印原因并返回 `None`。

    ⚠ 写失败**不影响**本次训练的数值（缓存只是加速），所以不 `SystemExit`；
    但必须**说出来**，否则"以为在复用、其实每次都重算"没人发现。
    """
    d = Path(cache_dir)
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{kind}.{key}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        payload = {"__key__": np.array(key), "__keyjson__": np.array(
            json.dumps(keydict, sort_keys=True, ensure_ascii=False)),
            "__sha256__": np.array(json.dumps(
                {n: hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()
                 for n, a in arrays.items()}, sort_keys=True))}
        payload.update(arrays)
        with open(tmp, "wb") as f:
            np.savez(f, **payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, d / f"{kind}.{key}.npz")
        return d / f"{kind}.{key}.npz"
    except Exception as e:
        print(f"[cache] ⚠ 缓存**写入失败**（{type(e).__name__}: {e}）—— 不影响结果，但本次不复用")
        try:
            if 'tmp' in locals() and Path(tmp).exists():
                Path(tmp).unlink()
        except Exception:
            pass
        return None
