"""
[数据根可覆盖性 ③ ⚠ 硬编码路径，仅供本机复现] 本文件把 S:（或 E:）数据根写死在字面路径里，未做参数化；换机器必须先改这些路径。
"""
# P2-11b · 域掩码策略组合的硬判据自证（零训练；只跑自对弈采集）——
#   ① 旧串逐位不变：**备份的旧二进制** vs 新二进制，同 seed 同场次 ⇒ g*.jsonl 逐字节相同
#   ② 三条负向：cond 取不到的字段 / B 的动作不在 legal / 语法错（`!` 后缺 cond）—— 都必须在**构造期**报错
#   ③ 域内改判的小规模冒烟（完整机械判据在 _p2-11b-domdiff.py / _p2-11b-dforward.py 上做）
# 低压力：workers=1 · OMP/MKL=6 · 一次一个重负载。
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\HP\source\games\mahjong'
$env:OMP_NUM_THREADS = '6'; $env:MKL_NUM_THREADS = '6'
$env:PYTHONIOENCODING = 'utf-8'

$NEW  = "$repo\trainer\build\trainer.exe"
$OLD  = 'S:\mahjong-training\tools\trainer\trainer-pre-p211mask.exe'
$A    = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"   # A0 = riichi
$B    = "$repo\tools\build\v4-wl-meld3030-4g-c2-g04\net.bin"  # A1 = meld
$SC   = "$repo\trainer\build\p211b"
$SEED = '52870101'
$GAMES = '4'; $HANDS = '2'

foreach ($p in @($NEW, $OLD, $A, $B)) { if (-not (Test-Path $p)) { throw "缺文件：$p" } }
if (Test-Path $SC) { Remove-Item $SC -Recurse -Force }
New-Item -ItemType Directory -Path $SC -Force | Out-Null

Write-Host "===== ① 旧串逐位不变（旧/新二进制，同 seed=$SEED 同场次，$GAMES 场 x $HANDS 小局）====="
$legacy = [ordered]@{
  'net-plain'   = "net:$A"
  'net-at0'     = "net:$A@0"
  'net-temp'    = "net:$A#1.0"
  'pass'        = 'pass'
  'first'       = 'first'
  'random'      = 'random'
  'teacher'     = 'teacher'
}
$bad = 0
foreach ($tag in $legacy.Keys) {
  $pol = $legacy[$tag]
  foreach ($side in @('old', 'new')) {
    $exe = if ($side -eq 'old') { $OLD } else { $NEW }
    $out = "$SC\legacy\$side\$tag"
    New-Item -ItemType Directory -Path $out -Force | Out-Null
    & $exe selfplay $GAMES --workers 1 --hands $HANDS --seed $SEED --policy $pol --out $out *> $null
    if ($LASTEXITCODE -ne 0) { throw "selfplay 失败：$side/$tag exit=$LASTEXITCODE" }
  }
  $fo = Get-ChildItem "$SC\legacy\old\$tag\g*.jsonl" | Sort-Object Name
  $fn = Get-ChildItem "$SC\legacy\new\$tag\g*.jsonl" | Sort-Object Name
  $same = ($fo.Count -eq $fn.Count) -and $fo.Count -gt 0
  if ($same) {
    for ($i = 0; $i -lt $fo.Count; $i++) {
      if ($fo[$i].Name -ne $fn[$i].Name) { $same = $false; break }
      $ho = (Get-FileHash $fo[$i].FullName -Algorithm SHA256).Hash
      $hn = (Get-FileHash $fn[$i].FullName -Algorithm SHA256).Hash
      if ($ho -ne $hn) { $same = $false; break }
    }
  }
  # summary.json 的 seconds / games_per_second / decisions_per_second 是墙钟量 ⇒ 只比其余字段
  $jo = Get-Content "$SC\legacy\old\$tag\summary.json" -Raw | ConvertFrom-Json
  $jn = Get-Content "$SC\legacy\new\$tag\summary.json" -Raw | ConvertFrom-Json
  foreach ($k in @('seconds', 'games_per_second', 'decisions_per_second')) { $jo.$k = 0; $jn.$k = 0 }
  $jSame = ($jo | ConvertTo-Json -Depth 9 -Compress) -eq ($jn | ConvertTo-Json -Depth 9 -Compress)
  if (-not $same -or -not $jSame) { $bad++ }
  Write-Host ("  [{0}] {1,-11} policy={2,-8} g*.jsonl {3}/{3} 逐字节相同={4}  summary(去墙钟字段)相同={5}" -f `
      ($(if ($same -and $jSame) { 'ok' } else { 'FAIL' })), $tag, $pol, $fo.Count, $same, $jSame)
}
Write-Host "  ① 小结：7 条老串中 $((7 - $bad))/7 条与新二进制逐字节一致（不符 $bad 条）"

Write-Host ""
Write-Host "===== ② 三条负向（构造期报错；退出码 2 = 拒绝）====="
$negs = [ordered]@{
  'cond-取不到的字段'   = "net:$A!net:$B@wins=1"
  'cond-未支持的公开量' = "net:$A!net:$B@scores=1"
  'cond-语法错(缺cond)' = "net:$A!net:$B"
  'cond-值非法'         = "net:$A!net:$B@kind=foo"
  'cond-空'             = "net:$A!net:$B@"
  'A侧权重不存在'       = "net:$SC\no-such.bin!net:$B@kind=claim"
}
$negOk = 0
foreach ($tag in $negs.Keys) {
  $out = "$SC\neg\$tag"
  $o = & $NEW selfplay 1 --workers 1 --hands 1 --seed $SEED --policy $negs[$tag] --out $out 2>&1
  $code = $LASTEXITCODE
  $msg = ($o | Select-String '\[trainer\]' | Select-Object -First 1).Line
  if ($null -eq $msg) { $msg = ($o | Select-Object -First 1) }
  $wall = Get-ChildItem $out -ErrorAction SilentlyContinue | Measure-Object
  $ok = ($code -ne 0) -and ($wall.Count -eq 0)     # ⛔ 拒绝时必须**没产出任何轨迹**（不静默降级）
  if ($ok) { $negOk++ }
  Write-Host ("  [{0}] {1,-20} exit={2} 产物={3} 个`n        策略串：{4}`n        报错：{5}" -f `
      ($(if ($ok) { 'ok' } else { 'FAIL' })), $tag, $code, $wall.Count, $negs[$tag], $msg)
}
Write-Host "  ② 小结：$negOk/$($negs.Count) 条负向都在构造期被拒绝且零产物"

Write-Host ""
Write-Host "===== ③ 域掩码冒烟：同 seed 下 `net:A` vs `net:A!net:B@kind=claim`（$GAMES 场 x 3 小局）====="
foreach ($side in @('base', 'mask')) {
  $pol = if ($side -eq 'base') { "net:$A" } else { "net:$A!net:$B@kind=claim" }
  $out = "$SC\smoke\$side"
  New-Item -ItemType Directory -Path $out -Force | Out-Null
  & $NEW selfplay $GAMES --workers 1 --hands 3 --seed $SEED --policy $pol --out $out *> $null
  if ($LASTEXITCODE -ne 0) { throw "smoke 失败：$side" }
  Write-Host "  [$side] policy=$pol  产物 $((Get-ChildItem $out -File | Measure-Object).Count) 个文件"
}
Write-Host "  （逐行机械判据见 _p2-11b-domdiff.py 的输出）"
Write-Host ""
Write-Host "[p2-11b] 硬判据自证结束：old-vs-new 不符 $bad 条 · 负向拒绝 $negOk/$($negs.Count)"
