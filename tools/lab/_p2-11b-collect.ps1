# P2-11b · 正式判据采集（零训练）：A0 基线 + H 混合臂（域掩码真跑）。
#   用法：pwsh -File _p2-11b-collect.ps1 -Arm base|mask
#   口径：seed_base=52870101（与 P2-11 最小判据同族 ⇒ A1 的既有账可直接复用）· 1000 场 · --workers 6
#         · 同一三席风格桌（def7/win2/atk3）· `--rotate-perm`（style-vector.py 自动带）· 保留逐手轨迹。
param([Parameter(Mandatory=$true)][ValidateSet('base','mask')][string]$Arm)
"""
[数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""

$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\HP\source\games\mahjong'
$env:PYTHONPATH        = "$repo\python;$repo\python\.venv\Lib\site-packages"
$env:MAHJONG_DATA_ROOT = 'S:\mahjong-training'
$env:MAHJONG_TRAINER   = 'S:\mahjong-training\tools\trainer\trainer.exe'
$env:PYTHONIOENCODING  = 'utf-8'
$env:OMP_NUM_THREADS   = '6'
$env:MKL_NUM_THREADS   = '6'

$A = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"    # A0 = riichi
$B = "$repo\tools\build\v4-wl-meld3030-4g-c2-g04\net.bin"   # A1 = meld（域内替换网）
$style = @(
  "$repo\tools\build\v4-expert-def7-g10\net.bin",
  "$repo\tools\build\v4-expert-win2-g10\net.bin",
  "$repo\tools\build\v4-expert-atk3-g10\net.bin"
)
foreach ($p in @($A, $B) + $style) { if (-not (Test-Path $p)) { throw "缺权重：$p" } }
if ($env:MAHJONG_TRAINER -ne 'S:\mahjong-training\tools\trainer\trainer.exe') { throw 'trainer 落点自证失败' }
& $env:MAHJONG_TRAINER --selftest *> $null
if ($LASTEXITCODE -ne 0) { throw '数据根 trainer.exe 自检不通过（是不是忘了 Copy-Item？）' }

$stu = if ($Arm -eq 'base') { "net:$A" } else { "net:$A!net:$B@kind=claim" }
$out = "S:\mahjong-training\raw\p2-11b\$Arm"
if ($out -ne "S:\mahjong-training\raw\p2-11b\$Arm") { throw "落点自证失败：$out" }
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out -Force | Out-Null

$policy = (@($stu) + ($style | ForEach-Object { "net:$_" })) -join ','
$argv = @("$repo\tools\style-vector.py", '--run-out', $out, '--trainer', $env:MAHJONG_TRAINER,
          '--games', '1000', '--workers', '6', '--seed', '52870101',
          '--keep-traces', '--policy', $policy,
          '--json', "S:\mahjong-training\w-ladder\style-vector-per-gen\P211B-$Arm.json",
          '--md',   "S:\mahjong-training\w-ladder\style-vector-per-gen\P211B-$Arm.md")

Write-Host ("[p2-11b] arm=$Arm seed_base=52870101 games=1000 workers=6 起跑 " + (Get-Date -Format 'HH:mm:ss'))
Write-Host "[p2-11b] student=$stu"
& 'S:\mahjong-training\tools\py312\python.exe' @argv
Write-Host ("[p2-11b] arm=$Arm 结束 exit=$LASTEXITCODE " + (Get-Date -Format 'HH:mm:ss'))
exit $LASTEXITCODE
