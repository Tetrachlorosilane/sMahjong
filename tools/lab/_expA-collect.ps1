# 实验 A《分歧域可学性探针》采集包装（零训练）
# 用法：pwsh -File _expA-collect.ps1 -Arm riichi|meld
# 判据：① 采集目录先清空（jvm-guard 重试时不会把两次的轨迹混在一起）
#       ② 同 seed_base / 同 --games / 同 --policy 顺序（⇒ 同 24 排列 ⇒ 同学生席）
#       ③ 学生臂 = 第 0 席的 net（纯 argmax，无 @/# 后缀）—— 分歧指示量按「argmax 不同」定义
param([Parameter(Mandatory=$true)][ValidateSet('riichi','meld')][string]$Arm)
"""
[数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""

$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\HP\source\games\mahjong'
$env:PYTHONPATH     = "$repo\python;$repo\python\.venv\Lib\site-packages"
$env:MAHJONG_DATA_ROOT = 'S:\mahjong-training'
$env:MAHJONG_TRAINER   = 'S:\mahjong-training\tools\trainer\trainer.exe'
$env:PYTHONIOENCODING  = 'utf-8'

$nets = @{
  riichi = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"   # = bot-ai\wl-riichi\net.bin（sha256 已核）
  meld   = "$repo\tools\build\v4-wl-meld3030-4g-c2-g04\net.bin"  # = bot-ai\wl-meld\net.bin（sha256 已核）
}
$style = @(
  "$repo\tools\build\v4-expert-def7-g10\net.bin",
  "$repo\tools\build\v4-expert-win2-g10\net.bin",
  "$repo\tools\build\v4-expert-atk3-g10\net.bin"
)
$stu = $nets[$Arm]
foreach ($p in @($stu) + $style) { if (-not (Test-Path $p)) { throw "缺权重：$p" } }

$out = "S:\mahjong-training\raw\expA-div\$Arm"
if ($out -ne "S:\mahjong-training\raw\expA-div\$Arm") { throw "落点自证失败：$out" }
if (Test-Path $out) { Remove-Item $out -Recurse -Force }
New-Item -ItemType Directory -Path $out -Force | Out-Null

$policy = (@("net:$stu") + ($style | ForEach-Object { "net:$_" })) -join ','
$sv = "$repo\tools\style-vector.py"
$argv = @($sv, '--run-out', $out, '--trainer', $env:MAHJONG_TRAINER,
          '--games', '1000', '--workers', '12', '--seed', '52870101',
          '--keep-traces', '--policy', $policy,
          '--json', "S:\mahjong-training\w-ladder\style-vector-per-gen\EXPA-$Arm.json",
          '--md',   "S:\mahjong-training\w-ladder\style-vector-per-gen\EXPA-$Arm.md")

Write-Host "[expA] arm=$Arm seed_base=52870101 games=1000 workers=12"
Write-Host "[expA] student=net:$stu"
Write-Host "[expA] out=$out"
& 'S:\mahjong-training\tools\py312\python.exe' @argv
exit $LASTEXITCODE
