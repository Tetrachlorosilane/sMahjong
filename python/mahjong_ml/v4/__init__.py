"""训练框架 v4（规范 `docs/FEATURES-V4.md` · 设计 `docs/TRAINING-V4.md`）。

| 模块 | 干什么 |
| --- | --- |
| `spec` | **唯一注册表**：块 id / 宽度 / 依赖的 obs 字段 / 版本 / 块清单指纹 |
| `blocks` | `obs`(+sidecar) → `tile / evt / ctx / cand` 四个张量（全量重算 = 基准路径） |
| `cache` | L1 牌河增量 + L2 事件流增量（`EventStream`，循环推理用） |
| `model` | 三塔 → 关键张注意力 + 状态⇄增量两轮 → 多头（`HEAD_SPECS`） |
| `harness` | 封闭红证（标签不进输入 / 奖励不含隐藏量）+ 场外均衡审计（`balance.json`） |
| `traces` | **数据集层唯一入口**：轨迹 + sidecar 的版本硬闸门（obs v2 / 老 sidecar 一律报错） |
| `dataset` | 轨迹 → **v4 四张量 + 监督标签**（`train.npz`/`val.npz` 列 + `meta.json`） |
| `pretrain` | **教师预训练**（P1/P2 冷启动）：teacher 动作为主标签 + aux 真值辅助头 |

**训练准备（P0）已落地的部分**：契约、张量、缓存、模型、多头、两套闸门、自检，
obs v3 的三端落地（Java 权威实现 → C++ 镜像 → 契约与校验器）、sidecar v3
（逐家危险度/安全度四段）、标签侧 `aux.npz`、以及数据集的版本硬闸门。
**已能跑**：`v4.dataset` → `v4.pretrain` 的第一轮教师预训练（`docs/TRAINING-V4.md` §9 的 P1/P2 雏形）。
`python -m mahjong_ml.v4 plan` 会打出剩下的 ⚠ 项。
"""

from . import blocks, cache, cli, dataset, harness, model, pretrain, spec, traces   # noqa: F401

__all__ = ["spec", "blocks", "cache", "model", "harness", "cli", "traces", "dataset", "pretrain"]
