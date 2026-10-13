# P2-11d · 「点棒/顺位」域的采集与读数（零训练；口径 = P2-11D-PLAN.md）
#   pwsh -File _p2-11d-run.ps1 -Tag base -Base                    # 只抽 A0 基线（需先 _p2-11d-collect.ps1 -Tag base）
#   pwsh -File _p2-11d-run.ps1 -Tag rank4 -Cond 'rank=4'           # 一域全链（采集另跑，见 -CollectOnly）
#   pwsh -File _p2-11d-run.ps1 -Tag rank4 -Cond 'rank=4' -CollectOnly
#   pwsh -File _p2-11d-run.ps1 -Tag rank4 -Cond 'rank=4' -ReadOnly
# 落盘：`_p2-11d-domains.jsonl`（一域一行）· `_p2-11d-domains.md`（同行的人读表）· `_p2-11d-<tag>.json`（全量读数）
# ⚠ 与上一轮 `_p2-11c-run.ps1` 的差别**只有落点**（⛔ 不动上一轮的 `_p2-11c-domains.*`）
param(
  [Parameter(Mandatory=$true)][string]$Tag,
  [string]$Cond = '',
  [switch]$Base,
  [switch]$CollectOnly,
  [switch]$ReadOnly
)
"""
[数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false   # domdiff 的"判红"是**读数**，不许中止整条链
$repo = 'C:\Users\HP\source\games\mahjong'
$env:PYTHONPATH = "$repo\python;$repo\python\.venv\Lib\site-packages"
$env:PYTHONIOENCODING = 'utf-8'
$env:OMP_NUM_THREADS = '6'; $env:MKL_NUM_THREADS = '6'
$env:MAHJONG_DATA_ROOT = 'S:\mahjong-training'
$env:MAHJONG_TRAINER = 'S:\mahjong-training\tools\trainer\trainer.exe'
$PY  = 'S:\mahjong-training\tools\py312\python.exe'
$TR  = 'S:\mahjong-training\tools\trainer\trainer.exe'
$WL  = 'S:\mahjong-training\w-ladder'
$WORK = "$WL\_p2-11c-work"
$PREV = "$WL\_p2-11-work"
$RAW  = 'S:\mahjong-training\raw\p2-11c'
$A = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"    # A0 = riichi
$B = "$repo\tools\build\v4-wl-meld3030-4g-c2-g04\net.bin"   # A1 = meld（域内替换网）
New-Item -ItemType Directory -Path "$WL\logs" -Force | Out-Null
function Stamp { (Get-Date -Format 'HH:mm:ss') }

if ($Base) {
  Write-Host "[p2-11d] ① A0 基线抽取 起 $(Stamp)"
  & $PY "$WL\_p2-11c-extract.py" --raw-dir "$RAW\base" --arm base --student-policy "net:$A" --work $WORK
  if ($LASTEXITCODE -ne 0) { throw 'extract base failed' }
  Write-Host "[p2-11d] ① 完 $(Stamp)"
  exit 0
}
if ($Cond -eq '') { throw '非 -Base 必须给 -Cond' }
$stuMask = "net:$A!net:$B@$Cond"

if (-not $ReadOnly) {
  Write-Host "[p2-11d:$Tag] ⓪ 采集（1000 场 / workers 8 / seed 52870101 / keep-traces）起 $(Stamp)"
  & $PSHOME\pwsh -File "$WL\_p2-11d-collect.ps1" -Tag $Tag -Cond $Cond
  if ($LASTEXITCODE -ne 0) { throw 'collect failed' }
  Write-Host "[p2-11d:$Tag] ⓪ 完 $(Stamp)"
}
if ($CollectOnly) { exit 0 }

Write-Host "[p2-11d:$Tag] ① 抽取 起 $(Stamp)"
& $PY "$WL\_p2-11c-extract.py" --raw-dir "$RAW\$Tag" --arm $Tag --student-policy $stuMask --work $WORK
if ($LASTEXITCODE -ne 0) { throw 'extract failed' }

Write-Host "[p2-11d:$Tag] ② 域内前向（B 网，只跑 A0 的 D 行）起 $(Stamp)"
& $PY "$WL\_p2-11c-dforward.py" --work $WORK --trainer $TR --arm base --cond $Cond --net-b $B `
    --rows-h "$WORK\rows_$Tag.jsonl" --max-rows 20000 --out-json "$WL\_p2-11d-dforward-$Tag.json" `
    *> "$WL\logs\p2-11d-dforward-$Tag.log"
if ($LASTEXITCODE -ne 0) { throw 'dforward failed' }

Write-Host "[p2-11d:$Tag] ③ 逐行机械自证（¬D 逐字节）起 $(Stamp)"
& $PY "$WL\_p2-11c-domdiff.py" --raw-a "$RAW\base" --raw-h "$RAW\$Tag" `
    --hands-a "$WORK\hands_base.json" --rows-base "$WORK\rows_base.jsonl" --cond $Cond `
    --out-json "$WL\_p2-11d-domdiff-$Tag.json" *> "$WL\logs\p2-11d-domdiff-$Tag.log"
if ($LASTEXITCODE -ne 0) { Write-Host "⚠ domdiff FAIL（¬D 不逐位相同 ⇒ 该域判据作废）—— 仍落盘，由报告照实写" }

Write-Host "[p2-11d:$Tag] ④ 统计（并落盘一行）起 $(Stamp)"
& $PY "$WL\_p2-11c-analyze.py" --tag $Tag --cond $Cond --work $WORK --work-prev $PREV `
    --summary-base "$RAW\base\summary.json" --summary-mask "$RAW\$Tag\summary.json" `
    --summary-meld 'S:\mahjong-training\raw\p2-11\meld\summary.json' `
    --summary-base-prev 'S:\mahjong-training\raw\p2-11b\base\summary.json' `
    --meta-base "$WORK\meta_base.json" --meta-mask "$WORK\meta_$Tag.json" `
    --domdiff-json "$WL\_p2-11d-domdiff-$Tag.json" --dforward-json "$WL\_p2-11d-dforward-$Tag.json" `
    --out-json "$WL\_p2-11d-$Tag.json" `
    --append-jsonl "$WL\_p2-11d-domains.jsonl" --append-md "$WL\_p2-11d-domains.md" `
    *> "$WL\logs\p2-11d-analyze-$Tag.log"
if ($LASTEXITCODE -ne 0) { throw 'analyze failed' }
Write-Host "[p2-11d:$Tag] 完成 $(Stamp)"
& $PY -c "import json,sys; r=json.load(open(r'$WL\_p2-11d-$Tag.json',encoding='utf-8'))['summary_row']; print(json.dumps(r,ensure_ascii=False))"
