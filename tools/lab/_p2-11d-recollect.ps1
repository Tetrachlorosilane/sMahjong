"""
[数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""
# P2-11d · 恢复各臂的 `summary.json`（⚠ 上一轮既定规矩 = **删轨迹、留摘要**；我第一遍把整个目录删掉了）
#   做法：用**同一配方**重跑（同 seed / 同策略串 / 同 --workers），**不带** `--keep-traces`
#   ⇒ `style-vector.py` 自己"读完删轨迹、留 summary.json"（tools/style-vector.py:783）。
#   ⛔ 不带 --keep-traces ⇒ 不落 g*.jsonl ⇒ 残留仍为 0（只有 summary.json）。
#   自证：重跑出来的臂均值必须与 `_p2-11d-<tag>.json` 里已落盘的值**逐位相同**（同配方 ⇒ 同数）。
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\HP\source\games\mahjong'
$env:PYTHONPATH = "$repo\python;$repo\python\.venv\Lib\site-packages"
$env:PYTHONIOENCODING = 'utf-8'
$env:MAHJONG_DATA_ROOT = 'S:\mahjong-training'
$env:MAHJONG_TRAINER = 'S:\mahjong-training\tools\trainer\trainer.exe'
$PY = 'S:\mahjong-training\tools\py312\python.exe'
$A = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"
$B = "$repo\tools\build\v4-wl-meld3030-4g-c2-g04\net.bin"
$style = @(
  "$repo\tools\build\v4-expert-def7-g10\net.bin",
  "$repo\tools\build\v4-expert-win2-g10\net.bin",
  "$repo\tools\build\v4-expert-atk3-g10\net.bin"
)
# tag, 学生串, workers（⚠ 与第一遍**逐字相同**：base/rank4=6，rank1/scorelow=8）
$arms = @(
  @('base',     "net:$A",              6),
  @('rank4',    "net:$A!net:$B@rank=4", 6),
  @('rank1',    "net:$A!net:$B@rank=1", 8),
  @('scorelow', "net:$A!net:$B@score<20000", 8)
)
foreach ($arm in $arms) {
  $tag = $arm[0]; $stu = $arm[1]; $w = [int]$arm[2]
  $out = "S:\mahjong-training\raw\p2-11c\$tag"
  if (-not $out.StartsWith('S:\mahjong-training\raw\p2-11c\')) { throw "落点自证失败：$out" }
  New-Item -ItemType Directory -Path $out -Force | Out-Null
  $policy = (@($stu) + ($style | ForEach-Object { "net:$_" })) -join ','
  $env:OMP_NUM_THREADS = "$w"; $env:MKL_NUM_THREADS = "$w"
  Write-Host ("[recollect:$tag] 起 " + (Get-Date -Format 'HH:mm:ss') + "  workers=$w")
  & $PY "$repo\tools\style-vector.py" --run-out $out --trainer $env:MAHJONG_TRAINER `
      --games 1000 --workers $w --seed 52870101 --policy $policy `
      --json "S:\mahjong-training\w-ladder\style-vector-per-gen\P211C-$tag.json" `
      --md   "S:\mahjong-training\w-ladder\style-vector-per-gen\P211C-$tag.md" *> "S:\mahjong-training\w-ladder\logs\p2-11d-recollect-$tag.log"
  if ($LASTEXITCODE -ne 0) { throw "recollect $tag failed（退出码 $LASTEXITCODE）" }
  $n = (Get-ChildItem $out -File).Count
  Write-Host ("[recollect:$tag] 完 " + (Get-Date -Format 'HH:mm:ss') + "  残留文件 $n 个（应 1 = summary.json）")
}
Write-Host "[recollect] 全部完成"
