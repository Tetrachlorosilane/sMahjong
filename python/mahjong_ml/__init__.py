"""立直麻将训练侧（Python）。

只做**离线**的事：读服务端产出的轨迹 → 特征 → 训练 → 导出权重。
服务端（Java）保持零第三方依赖，推理用纯 Java 手写前向（见 `docs/TRAINING.md` §2）。

三条硬约束（§0.1）在 `paths`（数据只落 T 盘）与 `guard`（CPU ≤75% 核 / GPU ≤80%）里落地。
"""

import sys as _sys

# 中文输出必须**显式钉成 UTF-8**（与 Java 侧 `-Dstdout.encoding=UTF-8` 同一个理由）：
# 管道/重定向时 Python 用 `locale.getpreferredencoding()`（本机 cp936），中文会变成乱码。
# 放在包初始化里 → 任何 `python -m mahjong_ml.xxx` 入口都自动生效，各脚本不必各写一份。
for _stream in (_sys.stdout, _sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:                       # noqa: BLE001
        pass

__all__ = ["paths", "guard", "features", "dataset", "nets", "eval", "bc"]
