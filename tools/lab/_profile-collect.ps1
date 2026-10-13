# 自对弈采集的墙钟剖面（纯测量：不改 trainer，不改仓库）。
#   差分法：同 seed_base / 同场数 / 同 workers，只有策略或 --out 开关不同。
#   逐手轨迹只用完即删（调用方负责 Remove-Item）。
# 用法：pwsh -File _profile-collect.ps1 -Tag A1 -Games 40 -Workers 4 -Policy random [-OutDir <path>]
param(
  [Parameter(Mandatory=$true)][string]$Tag,
  [int]$Games = 40,
  [int]$Workers = 4,
  [Parameter(Mandatory=$true)][string]$Policy,
  [string]$OutDir = '',
  [long]$Seed = 52870101,
  [switch]$RotatePerm,
  [int]$MaxHands = 0
)
"""
[数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""

$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\HP\source\games\mahjong'
$env:MAHJONG_DATA_ROOT = 'S:\mahjong-training'
$env:MAHJONG_TRAINER   = 'S:\mahjong-training\tools\trainer\trainer.exe'
$env:OMP_NUM_THREADS   = '4'
$env:MKL_NUM_THREADS   = '4'

$work = 'S:\mahjong-training\w-ladder\_profile-work'
New-Item -ItemType Directory -Path $work -Force | Out-Null
$log  = Join-Path $work "run-$Tag.out.txt"
$elog = Join-Path $work "run-$Tag.err.txt"

$argv = @('selfplay', "$Games", '--workers', "$Workers", '--policy', $Policy, '--seed', "$Seed")
if ($RotatePerm) { $argv += '--rotate-perm' }
if ($MaxHands -gt 0) { $argv += @('--hands', "$MaxHands") }
if ($OutDir -ne '') {
  if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
  New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
  $argv += @('--out', $OutDir)
}

Write-Host ("[$Tag] " + (Get-Date -Format 'HH:mm:ss') + " START games=$Games workers=$Workers out=" + $(if($OutDir){'yes'}else{'no'}))
Write-Host ("[$Tag] policy=$Policy")

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$p = Start-Process -FilePath $env:MAHJONG_TRAINER -ArgumentList $argv -PassThru -NoNewWindow `
       -RedirectStandardOutput $log -RedirectStandardError $elog
$tid = $p.Id
$cpuSamples = @(); $gpuSamples = @(); $wsSamples = @()
while (-not $p.HasExited) {
  Start-Sleep -Seconds 5
  try {
    $cpu = (Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 1 -MaxSamples 1).CounterSamples.CookedValue
    $cpuSamples += [math]::Round($cpu,1)
  } catch {}
  try {
    $pr = Get-Process -Id $tid -ErrorAction Stop
    $wsSamples += [math]::Round($pr.WorkingSet64/1MB,0)
  } catch {}
  try {
    $g = (nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>$null | Select-Object -First 1)
    if ($g) { $gpuSamples += [int]$g }
  } catch {}
  $p.Refresh()
}
$sw.Stop()
$wall = [math]::Round($sw.Elapsed.TotalSeconds, 2)
$procCpuS = $null
try { $p.Refresh(); $procCpuS = [math]::Round($p.TotalProcessorTime.TotalSeconds, 2) } catch {}
$coresUsed = if ($procCpuS -ne $null -and $wall -gt 0) { [math]::Round($procCpuS / $wall, 2) } else { $null }

$out = Get-Content $log -Raw -ErrorAction SilentlyContinue
$err = Get-Content $elog -Raw -ErrorAction SilentlyContinue
$sumLine = ($out -split "`n" | Where-Object { $_ -match '自对弈汇总' } | Select-Object -First 1)

# 若写了 summary.json，取权威字段
$sumJson = $null
if ($OutDir -ne '') {
  $sf = Join-Path $OutDir 'summary.json'
  if (Test-Path $sf) { $sumJson = Get-Content $sf -Raw | ConvertFrom-Json }
}

$rec = [ordered]@{
  tag = $Tag; wall_s = $wall; games = $Games; workers = $Workers
  policy = $Policy; out = $(if($OutDir){'yes'}else{'no'})
  cpu_avg = if ($cpuSamples.Count) { [math]::Round(($cpuSamples | Measure-Object -Average).Average,1) } else { $null }
  cpu_max = if ($cpuSamples.Count) { ($cpuSamples | Measure-Object -Maximum).Maximum } else { $null }
  gpu_avg = if ($gpuSamples.Count) { [math]::Round(($gpuSamples | Measure-Object -Average).Average,1) } else { $null }
  gpu_max = if ($gpuSamples.Count) { ($gpuSamples | Measure-Object -Maximum).Maximum } else { $null }
  ws_max_mb = if ($wsSamples.Count) { ($wsSamples | Measure-Object -Maximum).Maximum } else { $null }
  proc_cpu_s = $procCpuS
  cores_used = $coresUsed
  summary_line = $sumLine
  trainer_seconds = if ($sumJson) { $sumJson.seconds } else { $null }
  decisions = if ($sumJson) { $sumJson.decisions } else { $null }
  hands = if ($sumJson) { $sumJson.hands } else { $null }
  stderr_tail = if ($err) { ($err -split "`n" | Select-Object -Last 3) -join ' | ' } else { '' }
}
$json = $rec | ConvertTo-Json -Compress
Add-Content -Path (Join-Path $work 'profile-records.jsonl') -Value $json
Write-Host ("[$Tag] DONE wall={0}s cpu_avg={1}% gpu_avg={2}%" -f $wall, $rec.cpu_avg, $rec.gpu_avg)
Write-Host ("[$Tag] $sumLine")
exit $p.ExitCode
