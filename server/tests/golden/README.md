# `server/tests/golden/` —— 三端前向对拍的 golden 夹具（**构建期生成，不进仓库**）

这两个文件是 **Python 导出的二进制夹具**（不是源码、不是构建产物）：

| 文件 | 档 | 语义 | 用途 |
| --- | --- | --- | --- |
| `forward-v4.bin` | G1 | `current`（format 3 / v4 图） | Java `--selftest` 的 `v4ForwardTests` |
| `forward-a.bin` | G2 | `a-`（A− 图 / 95-token joint / format 3） | Java `--selftest` 的 `v4AminusTests` |

## 干净检出会跳过（这是刻意设计，不是漏测）

`SelfTest` 在找不到夹具时**打印提示并跳过**对应的两组用例：

```
（提示）v4 golden 夹具不在，跳过 P5 对拍：python/tests/golden/forward-v4.bin
（提示）a- golden 夹具不在，跳过 A− 前向判据：server/tests/golden/forward-a.bin
```

⇒ 干净检出（或只装了 JDK 的机器）跑 `java -jar server/build/mahjong-server.jar --selftest`
的计数会比带夹具的树**少这两组**；它不会把"缺夹具"当成"通过"，也不会把 selftest 判红。
⛔ 不要把 `.bin` 提交进仓库（二进制、且随小网/语义档变化）。

## 生成（需要 `python/.venv` + torch）

在仓库根：

```powershell
pwsh -File tools\gen-golden.ps1
# 或手动：
$env:PYTHONPATH = "$PWD\python"
python\.venv\Scripts\python.exe -m mahjong_ml.v4.export golden `
    --trace S:\mahjong-training\raw\v4-wl-p301-A-g01 `
    --out server\tests\golden\forward-v4.bin --cases 11 --scan-files 5 --fixture-semantics current
python\.venv\Scripts\python.exe -m mahjong_ml.v4.export golden `
    --trace S:\mahjong-training\raw\v4-wl-p301-A-g01 `
    --out server\tests\golden\forward-a.bin --cases 11 --scan-files 5 --fixture-semantics a-
```

生成后跑 `java -jar server\build\mahjong-server.jar --selftest`（期望 1,593/0）。
