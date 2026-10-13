"""
[数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""
# P2-11 最小判据 · 采集之后的全部读数（零训练）：抽取 → 双 net v4 前向 → T0/T2/T1-proxy
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\HP\source\games\mahjong'
$env:PYTHONPATH = "$repo\python;$repo\python\.venv\Lib\site-packages"
$env:PYTHONIOENCODING = 'utf-8'
$env:OMP_NUM_THREADS = '6'; $env:MKL_NUM_THREADS = '6'
$PY  = 'S:\mahjong-training\tools\py312\python.exe'
$TR  = 'S:\mahjong-training\tools\trainer\trainer.exe'
$WL  = 'S:\mahjong-training\w-ladder'
$WORK = "$WL\_p2-11-work"
$A = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"   # 臂 A0 = riichi
$B = "$repo\tools\build\v4-wl-meld3030-4g-c2-g04\net.bin"  # 臂 A1 = meld

New-Item -ItemType Directory -Path $WORK -Force | Out-Null
foreach ($arm in @('riichi','meld')) {
  $sn = if ($arm -eq 'riichi') { $A } else { $B }
  & $PY "$WL\_expA-extract.py" --raw-dir "S:\mahjong-training\raw\p2-11\$arm" --arm $arm --student-net $sn --work $WORK
  if ($LASTEXITCODE -ne 0) { throw "extract $arm failed" }
}
& $PY "$WL\_p2-11-forward.py" --work $WORK --arm riichi --trainer $TR --net "A0=$A" --net "A1=$B"
if ($LASTEXITCODE -ne 0) { throw "forward failed" }
& $PY "$WL\_p2-11-analyze.py" --work $WORK --out-json "$WL\_p2-11.json" `
    --raw "riichi=S:\mahjong-training\raw\p2-11\riichi" --raw "meld=S:\mahjong-training\raw\p2-11\meld"
if ($LASTEXITCODE -ne 0) { throw "analyze failed" }
Write-Host "[p2-11] 读数完成"
