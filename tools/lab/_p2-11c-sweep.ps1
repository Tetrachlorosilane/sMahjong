# P2-11c · 其余候选域逐个体检 · 全链（零训练）：A0 抽取 + 逐域（采集 → 读数 → 落盘一行）。
#   用法：pwsh -File _p2-11c-sweep.ps1 [-Only turn,oppriichi]
#   原则：一次只跑一个重负载；一个域失败**不中止**其余域（每域独立 try/catch + 立刻落盘）。
param([string]$Only = '')
"""
[数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$WL  = 'S:\mahjong-training\w-ladder'
$PY  = 'S:\mahjong-training\tools\py312\python.exe'
$repo = 'C:\Users\HP\source\games\mahjong'
$RAW = 'S:\mahjong-training\raw\p2-11c'
$WORK = "$WL\_p2-11c-work"
$env:PYTHONPATH = "$repo\python;$repo\python\.venv\Lib\site-packages"
$env:MAHJONG_DATA_ROOT = 'S:\mahjong-training'
$env:MAHJONG_TRAINER = 'S:\mahjong-training\tools\trainer\trainer.exe'
$env:PYTHONIOENCODING = 'utf-8'
$env:OMP_NUM_THREADS = '6'; $env:MKL_NUM_THREADS = '6'
$A = "$repo\tools\build\v4-wl-dec6170-4g-c2-g04\net.bin"

# 跑序（计划 §3 跑前写死）：最便宜的域当试点验通整条链，然后按信息量降序
$DOMAINS = [ordered]@{
  'claim_t12' = 'kind=claim&draw>=12'
  'turn'      = 'kind=turn'
  'oppriichi' = 'opp_riichi=1'
  'draw12p'   = 'draw>=12'
  'draw0-5'   = 'draw<6'
}
$order = if ($Only) { $Only.Split(',') } else { $DOMAINS.Keys }

function Log([string]$m) { Write-Host ("[sweep " + (Get-Date -Format 'HH:mm:ss') + "] " + $m) }

New-Item -ItemType Directory -Path $WORK -Force | Out-Null
New-Item -ItemType Directory -Path "$WL\logs" -Force | Out-Null

# ---------- 0 A0 抽取（一次，全部域共用） ----------
if (-not (Test-Path "$WORK\rows_base.jsonl")) {
  Log "① A0 抽取（base）起"
  & $PY "$WL\_p2-11c-extract.py" --raw-dir "$RAW\base" --arm base --student-policy "net:$A" --work $WORK
  if ($LASTEXITCODE -ne 0) { throw 'extract base failed' }
  Log "① A0 抽取完成"
} else { Log "① A0 抽取已有产物，跳过" }

# ---------- 逐域 ----------
foreach ($tag in $order) {
  $cond = $DOMAINS[$tag]
  if (-not $cond) { Log "⚠ 未知域 $tag，跳过"; continue }
  Log "==== $tag  ($cond) 开始 ===="
  try {
    if (-not (Test-Path "$RAW\$tag\summary.json")) {
      Log "  采集 H 臂起（1000 场 @workers=6）"
      pwsh -File "$WL\_p2-11c-collect.ps1" -Tag $tag -Cond $cond *> "$WL\logs\p2-11c-collect-$tag.log"
      if ($LASTEXITCODE -ne 0) { throw "collect $tag failed (exit $LASTEXITCODE)" }
      Log "  采集完成"
    } else { Log "  采集已有产物，跳过" }
    pwsh -File "$WL\_p2-11c-run.ps1" -Tag $tag -Cond $cond *> "$WL\logs\p2-11c-chain-$tag.log"
    if ($LASTEXITCODE -ne 0) { throw "run $tag failed (exit $LASTEXITCODE)" }
    $line = Get-Content "$WL\_p2-11c-domains.jsonl" -Tail 1
    Log "  ✅ $tag 落盘：$line"
  } catch {
    Log "  ❌ $tag 失败：$($_.Exception.Message)（照实记，继续下一个域）"
    Add-Content "$WL\_p2-11c-domains.jsonl" (ConvertTo-Json @{tag=$tag; cond=$cond; error="$($_.Exception.Message)"} -Compress)
  }
}
Log "全部域结束"
