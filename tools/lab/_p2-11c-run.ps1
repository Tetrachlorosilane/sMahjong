# P2-11c · 一个域的全部读数（零训练）：抽取 → 域内前向 → 逐行机械自证 → 统计（并**立刻落盘一行**）。
#   用法：pwsh -File _p2-11c-run.ps1 -Tag turn -Cond 'kind=turn'
#   前提：A0 的 base 臂已采集（raw/p2-11c/base）且已抽取（_p2-11c-work/rows_base.jsonl 等）。
param(
  [Parameter(Mandatory=$true)][string]$Tag,
  [Parameter(Mandatory=$true)][string]$Cond
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
$PY  = 'S:\mahjong-training\tools\py312\python.exe'
$TR  = 'S:\mahjong-training\tools\trainer\trainer.exe'
$WL  = 'S:\mahjong-training\w-ladder'
$WORK = "$WL\_p2-11c-work"
$PREV = "$WL\_p2-11-work"
$RAW  = "S:\mahjong-training\raw\p2-11c"
$A = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"    # A0
$B = "$repo\tools\build\v4-wl-meld3030-4g-c2-g04\net.bin"   # A1 = B（域内替换网）
New-Item -ItemType Directory -Path $WORK -Force | Out-Null

$stuMask = "net:$A!net:$B@$Cond"
Write-Host ("[p2-11c-run:$Tag] ① 抽取 起 " + (Get-Date -Format 'HH:mm:ss'))
& $PY "$WL\_p2-11c-extract.py" --raw-dir "$RAW\$Tag" --arm $Tag --student-policy $stuMask --work $WORK
if ($LASTEXITCODE -ne 0) { throw 'extract failed' }

Write-Host ("[p2-11c-run:$Tag] ② 域内前向（B 网，只跑 A0 的 D 行）起 " + (Get-Date -Format 'HH:mm:ss'))
& $PY "$WL\_p2-11c-dforward.py" --work $WORK --trainer $TR --arm base --cond $Cond --net-b $B `
    --rows-h "$WORK\rows_$Tag.jsonl" --max-rows 20000 --out-json "$WL\_p2-11c-dforward-$Tag.json"
if ($LASTEXITCODE -ne 0) { throw 'dforward failed' }

Write-Host ("[p2-11c-run:$Tag] ③ 逐行机械自证（¬D 逐字节）起 " + (Get-Date -Format 'HH:mm:ss'))
& $PY "$WL\_p2-11c-domdiff.py" --raw-a "$RAW\base" --raw-h "$RAW\$Tag" `
    --hands-a "$WORK\hands_base.json" --rows-base "$WORK\rows_base.jsonl" --cond $Cond `
    --out-json "$WL\_p2-11c-domdiff-$Tag.json"
if ($LASTEXITCODE -ne 0) { Write-Host "⚠ domdiff FAIL（¬D 不逐位相同 ⇒ 该域判据作废）—— 仍落盘，由报告照实写" }

Write-Host ("[p2-11c-run:$Tag] ④ 统计（并落盘一行）起 " + (Get-Date -Format 'HH:mm:ss'))
& $PY "$WL\_p2-11c-analyze.py" --tag $Tag --cond $Cond --work $WORK --work-prev $PREV `
    --summary-base "$RAW\base\summary.json" --summary-mask "$RAW\$Tag\summary.json" `
    --summary-meld 'S:\mahjong-training\raw\p2-11\meld\summary.json' `
    --summary-base-prev 'S:\mahjong-training\raw\p2-11b\base\summary.json' `
    --meta-base "$WORK\meta_base.json" --meta-mask "$WORK\meta_$Tag.json" `
    --domdiff-json "$WL\_p2-11c-domdiff-$Tag.json" --dforward-json "$WL\_p2-11c-dforward-$Tag.json" `
    --out-json "$WL\_p2-11c-$Tag.json" `
    --append-jsonl "$WL\_p2-11c-domains.jsonl" --append-md "$WL\_p2-11c-domains.md" `
    *> "$WL\logs\p2-11c-analyze-$Tag.log"
if ($LASTEXITCODE -ne 0) { throw 'analyze failed' }
Write-Host ("[p2-11c-run:$Tag] 完成 " + (Get-Date -Format 'HH:mm:ss'))
Get-Content "$WL\_p2-11c-$Tag.json" -TotalCount 0 | Out-Null
& $PY -c "import json,sys; r=json.load(open(r'$WL\_p2-11c-$Tag.json',encoding='utf-8'))['summary_row']; print(json.dumps(r,ensure_ascii=False))"
