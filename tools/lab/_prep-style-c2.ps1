"""
[数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""
# 预读风格向量（本轮：meld 0.50x 复现链 c2）。
# 与上一轮 `_prep-style.ps1` **逐字相同**，只换标签；命令行与 `w-ladder.py style_vector()` 一致。
# ⛔ 不删轨迹（收尾单独做）、⛔ 不改任何口径。
$ErrorActionPreference = 'Continue'
$repo = 'C:\Users\HP\source\games\mahjong'
$S = 'S:\mahjong-training'
$py = 'S:\mahjong-training\tools\py312\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$env:MAHJONG_DATA_ROOT = $S
$env:MAHJONG_TRAINER = "$S\tools\trainer\trainer.exe"
$env:PYTHONPATH = "$repo\python;$repo\python\.venv\Lib\site-packages"
$tags = @()
foreach ($lab in @('v4-wl-meld3030-4g-c2')) {
  foreach ($g in 1..4) { $tags += ('{0}-g{1:D2}' -f $lab, $g) }
}
foreach ($tag in $tags) {
  $tdir = Join-Path $S "w-ladder\traces\$tag"
  $js = Join-Path $S "w-ladder\style-vector-per-gen\$tag.json"
  if (Test-Path $js) { Write-Output ("[{0:HH:mm:ss}] skip {1}（已有读数）" -f (Get-Date), $tag); continue }
  $deadline = (Get-Date).AddMinutes(70)
  while ((Get-Date) -lt $deadline) {
    if (Test-Path (Join-Path $tdir 'summary.json')) {
      $n = @(Get-ChildItem $tdir -Filter 'g*.jsonl' -ErrorAction SilentlyContinue).Count
      if ($n -ge 1000) { break }
    }
    Start-Sleep -Seconds 20
  }
  if (-not (Test-Path (Join-Path $tdir 'summary.json'))) {
    Write-Output ("[{0:HH:mm:ss}] ⛔ 等不到 {1}" -f (Get-Date), $tag); continue
  }
  Write-Output ("[{0:HH:mm:ss}] 算 {1}（开始）" -f (Get-Date), $tag)
  & $py (Join-Path $repo 'tools\style-vector.py') $tdir --json $js *>> (Join-Path $S 'w-ladder\logs\_prep-style-c2.log')
  Write-Output ("[{0:HH:mm:ss}] {1} 退出码 {2}；json 存在={3}" -f (Get-Date), $tag, $LASTEXITCODE, (Test-Path $js))
}
Write-Output ("[{0:HH:mm:ss}] prep-c2 结束" -f (Get-Date))
