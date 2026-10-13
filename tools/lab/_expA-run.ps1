"""
[数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""
# 实验 A · 采集之后的全部读数路径（零训练）：抽取 → 双 net v4 前向 → 分歧域 + 交叉拟合路由
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\HP\source\games\mahjong'
$env:PYTHONPATH = "$repo\python;$repo\python\.venv\Lib\site-packages"
$env:PYTHONIOENCODING = 'utf-8'
$PY  = 'S:\mahjong-training\tools\py312\python.exe'
$TR  = 'S:\mahjong-training\tools\trainer\trainer.exe'
$WL  = 'S:\mahjong-training\w-ladder'
$WORK = 'S:\mahjong-training\w-ladder\_expA-work'
$A = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"   # 臂 A = riichi（= bot-ai\wl-riichi）
$B = "$repo\tools\build\v4-wl-meld3030-4g-c2-g04\net.bin"  # 臂 B = meld   （= bot-ai\wl-meld）

New-Item -ItemType Directory -Path $WORK -Force | Out-Null
foreach ($arm in @('riichi','meld')) {
  $sn = if ($arm -eq 'riichi') { $A } else { $B }
  & $PY "$WL\_expA-extract.py" --raw-dir "S:\mahjong-training\raw\expA-div\$arm" --arm $arm --student-net $sn --work $WORK
  if ($LASTEXITCODE -ne 0) { throw "extract $arm failed" }
}
# X = 该臂自己的 net（应与它实际打出的动作逐行相同 = 自证）；Y = 另一条臂的 net
& $PY "$WL\_expA-logits.py" --work $WORK --arm riichi --trainer $TR --net "X=$A" --net "Y=$B"
if ($LASTEXITCODE -ne 0) { throw "logits riichi failed" }
& $PY "$WL\_expA-logits.py" --work $WORK --arm meld --trainer $TR --net "X=$B" --net "Y=$A"
if ($LASTEXITCODE -ne 0) { throw "logits meld failed" }
& $PY "$WL\_expA-analyze.py" --work $WORK --out-json "$WL\_experiment-a.json" --primary-arm riichi `
    --raw "riichi=S:\mahjong-training\raw\expA-div\riichi" --raw "meld=S:\mahjong-training\raw\expA-div\meld"
if ($LASTEXITCODE -ne 0) { throw "analyze failed" }
Write-Host "[expA] done"
