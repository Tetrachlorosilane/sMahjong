"""BL-14 判据 1：`behaviour_id` 列**不进模型输入** ⇒ 加/不加该列，前向**逐位相同**。

口径：两边都是**同一份轨迹**构建的紧凑集（`single-nob` 无该列 / `single-b` 有该列），
其余 48 个 `.npy` 已先验逐字节相同（见 `crit1-bytes.log`）。这里对**同一批行**跑同一份权重，
对 **policy 头全量 logits** 取 sha256（比只比 argmax 严得多）。

⛔ 判据不是"argmax 相同"，是**逐位相同**（sha256 相等）。
"""
import hashlib
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, r"C:\Users\HP\source\games\mahjong\python")
from mahjong_ml.v4 import dataset as D          # noqa: E402
from mahjong_ml.v4 import pretrain as P         # noqa: E402

W = Path(r"C:\Users\HP\source\games\mahjong\tmp\bl14")
NET = r"C:\Users\HP\source\games\mahjong\tools\build\v4-expert-defrep-g10\net.bin"
ROWS = 4096

model = P._load_behaviour(NET, "cpu")
model.eval()
digests = {}
sums = {}
keys = {}
for tag, comp in (("nob", W / "compact" / "single-nob"), ("with", W / "compact" / "single-b")):
    split = D.load_split(comp, "train")
    keys[tag] = tuple(sorted(k for k, v in split.items() if v is not None))
    n = int(split["nlegal"].shape[0])
    idx = np.arange(0, min(ROWS, n))
    outs = []
    with torch.no_grad():
        for i0 in range(0, idx.size, 256):
            j = idx[i0:min(i0 + 256, idx.size)]
            b = P._batch(split, j, "cpu")
            assert "behaviour_id" not in b, "⛔ 该列**进了** batch 输入 —— 那就是泄漏"
            outs.append(model(b["tile"], b["evt"], b["ctx"], b["cand"], mask=b["mask"],
                              h=b.get("h"))["policy"].numpy())
    logits = np.concatenate(outs).astype(np.float32)
    digests[tag] = hashlib.sha256(np.ascontiguousarray(logits).tobytes()).hexdigest()
    sums[tag] = (float(logits.sum()), float(np.abs(logits).sum()), logits.shape)
    print(f"[{tag}] 行 {idx.size} · logits {logits.shape} · sha256 {digests[tag]}")
    print(f"        sum={sums[tag][0]:.6f} · abs_sum={sums[tag][1]:.6f}")
    print(f"        列清单（非 None）：{keys[tag]}")

print()
print("batch 输入键里是否出现 behaviour_id：", "behaviour_id" in
      set(k for t in keys for k in ()) or "否（见上面的 assert，两边都没触发）")
ok = digests["nob"] == digests["with"]
print(f"判据 1（前向逐位相同）：{'✅ 相同' if ok else '❌ 不同'}")
print(f"  nob  = {digests['nob']}")
print(f"  with = {digests['with']}")
print(f"  列清单相同（除 behaviour_id 外）："
      f"{'✅' if [k for k in keys['with'] if k != 'behaviour_id'] == list(keys['nob']) else '❌'}")
sys.exit(0 if ok else 1)
