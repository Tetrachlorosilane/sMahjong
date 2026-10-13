"""
[数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""
# P2-11b · 采样之后的全部读数（零训练）：抽取 → 域内前向 → 逐行机械自证 → 统计
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\HP\source\games\mahjong'
$env:PYTHONPATH = "$repo\python;$repo\python\.venv\Lib\site-packages"
$env:PYTHONIOENCODING = 'utf-8'
$env:OMP_NUM_THREADS = '6'; $env:MKL_NUM_THREADS = '6'
$PY  = 'S:\mahjong-training\tools\py312\python.exe'
$TR  = 'S:\mahjong-training\tools\trainer\trainer.exe'
$WL  = 'S:\mahjong-training\w-ladder'
$WORK = "$WL\_p2-11b-work"
$PREV = "$WL\_p2-11-work"
$A = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"    # A0
$B = "$repo\tools\build\v4-wl-meld3030-4g-c2-g04\net.bin"   # A1

New-Item -ItemType Directory -Path $WORK -Force | Out-Null
$stuBase = "net:$A"
$stuMask = "net:$A!net:$B@kind=claim"

Write-Host ("[p2-11b-run] ① 抽取 起 " + (Get-Date -Format 'HH:mm:ss'))
& $PY "$WL\_p2-11b-extract.py" --raw-dir 'S:\mahjong-training\raw\p2-11b\base' --arm base `
    --student-policy $stuBase --work $WORK
if ($LASTEXITCODE -ne 0) { throw 'extract base failed' }
& $PY "$WL\_p2-11b-extract.py" --raw-dir 'S:\mahjong-training\raw\p2-11b\mask' --arm mask `
    --student-policy $stuMask --work $WORK
if ($LASTEXITCODE -ne 0) { throw 'extract mask failed' }

Write-Host ("[p2-11b-run] ② 域内前向（B 网，只跑 A0 的 D 行）起 " + (Get-Date -Format 'HH:mm:ss'))
& $PY "$WL\_p2-11b-dforward.py" --work $WORK --trainer $TR --arm base --net-b $B `
    --rows-h "$WORK\rows_mask.jsonl" --out-json "$WL\_p2-11b-dforward.json"
if ($LASTEXITCODE -ne 0) { throw 'dforward failed' }

Write-Host ("[p2-11b-run] ③ 逐行机械自证（¬D 逐字节）起 " + (Get-Date -Format 'HH:mm:ss'))
& $PY "$WL\_p2-11b-domdiff.py" --raw-a 'S:\mahjong-training\raw\p2-11b\base' `
    --raw-h 'S:\mahjong-training\raw\p2-11b\mask' --hands-a "$WORK\hands_base.json" `
    --out-json "$WL\_p2-11b-domdiff.json"
if ($LASTEXITCODE -ne 0) { throw 'domdiff FAIL（¬D 不逐位相同 ⇒ 判据作废）' }

Write-Host ("[p2-11b-run] ④ 统计 起 " + (Get-Date -Format 'HH:mm:ss'))
& $PY "$WL\_p2-11b-analyze.py" --work $WORK --work-prev $PREV `
    --summary-base 'S:\mahjong-training\raw\p2-11b\base\summary.json' `
    --summary-mask 'S:\mahjong-training\raw\p2-11b\mask\summary.json' `
    --summary-meld 'S:\mahjong-training\raw\p2-11\meld\summary.json' `
    --summary-base-prev 'S:\mahjong-training\raw\p2-11\riichi\summary.json' `
    --meta-base "$WORK\meta_base.json" --meta-mask "$WORK\meta_mask.json" `
    --domdiff-json "$WL\_p2-11b-domdiff.json" --dforward-json "$WL\_p2-11b-dforward.json" `
    --out-json "$WL\_p2-11b.json"
if ($LASTEXITCODE -ne 0) { throw 'analyze failed' }
Write-Host ("[p2-11b-run] 读数完成 " + (Get-Date -Format 'HH:mm:ss'))
