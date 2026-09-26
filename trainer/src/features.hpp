// `trainer features <dir> [--workers K]` —— 派生特征 sidecar（`g*.feat.bin`）的生产者。
//
// 与 Java `mahjong.train.TraceFeatures` **逐字节同产物**（判据：tools/trainer-features-parity.mjs）。
//
// ⚠ 为什么 sidecar 而不是改轨迹本身：轨迹（`g*.jsonl`）是**被消费的契约**（PROTOCOL §8.4），
//   改它要动三处 + 旧数据集作废；而派生特征是 obs 的**纯函数**，所以单独放一个二进制文件。
//
// 文件格式（小端；Python 侧按固定 dtype 内存映射读，见 `python/mahjong_ml/dataset.py`）：
//   header: magic(4)=0x4D4A4654 "MJFT" · featureVersion(4) · nDec(4) · perDec(4) · perCand(4)
//   A: nDec × perDec            uint8   逐决策危险度
//   B: nDec × int16             nLegal  每条决策的候选数（与 jsonl 里的决策行**同序**）
//   C: ΣnLegal × perCand        int16   逐候选派生量（按 legal 顺序）
#pragma once

namespace trainer {

/** CLI：`trainer features <dir> [--workers K]`（`K<=0` → 默认 ≤75% 的核）。 */
int featuresCli(int argc, char **argv);

}  // namespace trainer
