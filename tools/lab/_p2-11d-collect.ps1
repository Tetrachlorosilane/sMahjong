# P2-11d · 「点棒/顺位」域的采集（P2-11c-collect 的 workers=8 变体；用户授权：并行上限 9 ⇒ 采 --workers 8 + OMP/MKL 8）（零训练）：A0 基线 + 每个域的混合臂 H。
#   用法：pwsh -File _p2-11c-collect.ps1 -Tag base [-Cond <cond>]
#   口径：seed_base=52870101（与候选①同族 ⇒ 逐场配对）· 1000 场 · --workers 8 · --rotate-perm（style-vector 自动带）
#         · 同一三席风格桌（def7/win2/atk3）· 保留逐手轨迹（跑完的读数脚本读它，读完即删）。
param(
  [Parameter(Mandatory=$true)][ValidatePattern('^[a-z0-9_-]+$')][string]$Tag,
  [string]$Cond = ''
)
"""
[数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""

$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\HP\source\games\mahjong'
$env:PYTHONPATH        = "$repo\python;$repo\python\.venv\Lib\site-packages"
$env:MAHJONG_DATA_ROOT = 'S:\mahjong-training'
$env:MAHJONG_TRAINER   = 'S:\mahjong-training\tools\trainer\trainer.exe'
$env:PYTHONIOENCODING  = 'utf-8'
$env:OMP_NUM_THREADS   = '8'
$env:MKL_NUM_THREADS   = '8'

$A = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"    # A0 = riichi（基座）
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

# base = 纯 net:A0；其余 tag = 域掩码（必须给 cond）
if ($Tag -eq 'base') {
  if ($Cond -ne '') { throw 'base 臂不许带 cond' }
  $stu = "net:$A"
} else {
  if ($Cond -eq '') { throw "arm=$Tag 必须给 -Cond" }
  $stu = "net:$A!net:$B@$Cond"
}

$out = "S:\mahjong-training\raw\p2-11c\$Tag"
if ($out -ne "S:\mahjong-training\raw\p2-11c\$Tag") { throw "落点自证失败：$out" }
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out -Force | Out-Null

$policy = (@($stu) + ($style | ForEach-Object { "net:$_" })) -join ','
$argv = @("$repo\tools\style-vector.py", '--run-out', $out, '--trainer', $env:MAHJONG_TRAINER,
          '--games', '1000', '--workers', '8', '--seed', '52870101',
          '--keep-traces', '--policy', $policy,
          '--json', "S:\mahjong-training\w-ladder\style-vector-per-gen\P211C-$Tag.json",
          '--md',   "S:\mahjong-training\w-ladder\style-vector-per-gen\P211C-$Tag.md")

Write-Host ("[p2-11d] tag=$Tag seed_base=52870101 games=1000 workers=8 起跑 " + (Get-Date -Format 'HH:mm:ss'))
Write-Host "[p2-11d] student=$stu"
& 'S:\mahjong-training\tools\py312\python.exe' @argv
Write-Host ("[p2-11d] tag=$Tag 结束 exit=$LASTEXITCODE " + (Get-Date -Format 'HH:mm:ss'))
exit $LASTEXITCODE
