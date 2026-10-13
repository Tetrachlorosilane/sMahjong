# P3-PKG 挂载实测包装器 v2（**给 `tools/jvm-guard.mjs` 包**：它只包一条会退出的命令，所以把
# 「起服务端 → 挂新包 → bot-ai-test → 真机 autoplay 打完整场 → 收服务端」做成**一条**命令。
#
# 判据（全绿才打印 MOUNT-TEST PASS、退出码 0）：
#   ① 启动日志出现 ASCII 锚点 `LISTENING`；
#   ② 启动日志有「包名 + kind=net」同一行（新包真的被挂上，不是被跳过）；
#   ③ 服务端日志 `WARN` **0** 条；
#   ④ 清单行（ASCII 前缀 `teacher, first, pass, random`）里含新包 = 它是默认 AI；
#   ⑤ `node tools/bot-ai-test.mjs` 打印 `BOT-AI PASS`；
#   ⑥ 客户端 `--autoplay` 打印 `AUTOPLAY PASS`（其内部判据 = **点数和 == 100000** 且对局真的开始）。
#
# v2 的三处修改（v1 实测踩坑）：
#   · ⛔ **不用 `Get-NetTCPConnection`**（CIM cmdlet；同机有别的进程每秒采进程表时它会**挂死**
#     —— v1 就是这么卡了 18 分钟）。改用 .NET `TcpClient` 直连探测，纯用户态、毫秒级。
#   · **硬墙钟截止**（Stopwatch）而不是"循环 N 次 × sleep"，并且**每步打时间戳心跳**
#     ⇒ 卡住时一眼看出卡在哪一步。
#   · 客户端 stdout/stderr **落盘**（v1 只读 autoplay.log，出现"日志是空的"时无从判断）。
param(
  [int]$Port = 10099,
  [string]$BotName = 'wl-p301pkg',
  [string]$Repo = 'C:\Users\HP\source\games\mahjong',
  [int]$GameTimeoutSec = 900
)
"""
[数据根可覆盖性 ① 可覆盖] 数据根默认 S:（搬运时的本机实测路径），可用命令行/参数覆盖：把脚本里残留的 S: 字面路径换成你的数据根，或直接用参数给出输入目录。数值/结论不受路径影响；g*.jsonl / *.feat.bin 等大件不在仓库里。
"""
$ErrorActionPreference = 'Continue'
$S = 'S:\mahjong-training'
$slog = "$S\w-ladder\_p3pkg-mount-server.$Port.log"
$failed = @()
$sw = [Diagnostics.Stopwatch]::StartNew()

function Note($m) { Write-Output ("[{0,6:N1}s] {1}" -f $sw.Elapsed.TotalSeconds, $m) }
function Bad($m) { Write-Output ("[{0,6:N1}s]   [FAIL] {1}" -f $sw.Elapsed.TotalSeconds, $m); $script:failed += $m }
function Good($m) { Write-Output ("[{0,6:N1}s]   [ok]   {1}" -f $sw.Elapsed.TotalSeconds, $m) }
function Test-Port([int]$p) {
  try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $p); $c.Close(); return $true }
  catch { return $false }
}

# 起跑前清掉自己的残留（只按名字清 mahjong-client —— 那是本测试的客户端；⛔ 不动别的进程）
@(Get-Process mahjong-client -ErrorAction SilentlyContinue) | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
if (Test-Port $Port) { Bad "端口 $Port 起跑前就被占（不是本脚本的残留）⇒ 中止"; Note 'MOUNT-TEST FAIL'; exit 1 }

Note "起服务端（默认机器人 AI = $BotName，--bot-ai-dir $Repo\bot-ai）端口 $Port"
Remove-Item $slog, "$slog.err" -ErrorAction SilentlyContinue
$srv = Start-Process -FilePath 'java' -PassThru -WindowStyle Hidden -WorkingDirectory $Repo `
  -ArgumentList @('-jar', "$Repo\server\build\mahjong-server.jar", '--port', "$Port",
                  '--bot-ai-dir', "$Repo\bot-ai", '--bot-ai', $BotName) `
  -RedirectStandardOutput $slog -RedirectStandardError "$slog.err"

$listening = $false
while ($sw.Elapsed.TotalSeconds -lt 90) {
  Start-Sleep -Milliseconds 400
  if (Test-Port $Port) { $listening = $true; break }
  if ($srv.HasExited) { break }
}
if ($listening) { Good '服务端 LISTENING（TcpClient 直连探测）' } else { Bad "服务端未起（exit=$($srv.ExitCode)）" }

$txt = if (Test-Path $slog) { Get-Content $slog -Raw } else { '' }
# ⚠ **判据一律用 ASCII 锚点**：服务端日志里的中文在重定向+读取时会变乱码，拿中文 `-match` 会假红。
$mountLine = ($txt -split "`n" | Where-Object { $_ -match [regex]::Escape($BotName) -and $_ -match 'kind=net' } | Select-Object -First 1)
if ($mountLine) { Good "启动日志确认挂载了新包 ``$BotName``（kind=net）" } else { Bad "日志里没有 ``$BotName`` + kind=net 的挂载行" }
$warnLines = @($txt -split "`n" | Where-Object { $_ -match '\bWARN\b' })
if ($warnLines.Count -eq 0) { Good '服务端日志 WARN 0 条' } else { Bad "服务端日志 $($warnLines.Count) 条 WARN：$($warnLines[0])" }
$listLine = ($txt -split "`n" | Where-Object { $_ -match 'teacher, first, pass, random' } | Select-Object -First 1)
if ($listLine -and $listLine -match [regex]::Escape($BotName)) { Good "清单含新包，且默认 = $BotName" } else { Bad '清单行里没有新包 / 默认不是它' }

if ($listening) {
  Note 'bot-ai-test.mjs'
  $out = & node "$Repo\tools\bot-ai-test.mjs" 127.0.0.1 $Port 2>&1 | Out-String
  $out.Trim().Split("`n") | Where-Object { $_ -match 'FAIL' } | ForEach-Object { Note "    $_" }
  if ($out -match 'BOT-AI PASS') { Good 'BOT-AI PASS' } else { Bad 'bot-ai-test 未全绿' }

  Note "真机对局（--autoplay，机器人 = 新包；客户端总超时 ${GameTimeoutSec}s）"
  $alog = "$Repo\client\dist\autoplay.log"
  Remove-Item $alog -ErrorAction SilentlyContinue
  $ap = Start-Process -FilePath "$Repo\client\dist\mahjong-client.exe" -PassThru -WindowStyle Hidden `
    -WorkingDirectory "$Repo\client\dist" `
    -RedirectStandardOutput "$S\w-ladder\_p3pkg-client.out.txt" -RedirectStandardError "$S\w-ladder\_p3pkg-client.err.txt" `
    -ArgumentList @('--autoplay', '127.0.0.1', "$Port", '--name', '联调', '--timeout', "$GameTimeoutSec")
  $apSw = [Diagnostics.Stopwatch]::StartNew()
  $verdict = ''
  while ($apSw.Elapsed.TotalSeconds -lt ($GameTimeoutSec + 120)) {
    Start-Sleep -Seconds 2
    if (Test-Path $alog) {
      $m = [regex]::Match((Get-Content $alog -Raw -ErrorAction SilentlyContinue), 'AUTOPLAY (PASS|FAIL)')
      if ($m.Success) { $verdict = $m.Groups[1].Value; break }
    }
    if ($ap.HasExited) { Start-Sleep -Seconds 1; if (Test-Path $alog) { $m = [regex]::Match((Get-Content $alog -Raw), 'AUTOPLAY (PASS|FAIL)'); if ($m.Success) { $verdict = $m.Groups[1].Value } }; break }
  }
  if (-not $ap.HasExited) { Note '客户端仍在跑 ⇒ 强制结束'; Stop-Process -Id $ap.Id -Force -ErrorAction SilentlyContinue }
  $alogTxt = if (Test-Path $alog) { Get-Content $alog -Raw } else { '' }
  $alogTxt.Trim().Split("`n") | Where-Object { $_ -match 'AUTOPLAY|点数和|对局未真正开始|统计|位 ' } | ForEach-Object { Note "    $_" }
  Note ("    客户端退出码 = {0}；autoplay.log {1} 字节；耗时 {2:N1}s" -f $ap.ExitCode, $alogTxt.Length, $apSw.Elapsed.TotalSeconds)
  if ($verdict -eq 'PASS') { Good "AUTOPLAY PASS（客户端总超时 ${GameTimeoutSec}s 内打完整场；内部判据含 点数和 == 100000）" }
  else { Bad "autoplay 未通过（verdict='$verdict'）" }
  if (-not $alogTxt) {
    Note '    ⚠ autoplay.log 是空的 ⇒ 打印客户端 stdout/stderr 前 20 行供诊断：'
    @(Get-Content "$S\w-ladder\_p3pkg-client.out.txt" -ErrorAction SilentlyContinue | Select-Object -First 10) | ForEach-Object { Note "      out| $_" }
    @(Get-Content "$S\w-ladder\_p3pkg-client.err.txt" -ErrorAction SilentlyContinue | Select-Object -First 10) | ForEach-Object { Note "      err| $_" }
  }
}

Note '收服务端'
try { Stop-Process -Id $srv.Id -Force -ErrorAction Stop } catch { }
for ($i = 0; $i -lt 40; $i++) { Start-Sleep -Milliseconds 250; if (-not (Test-Port $Port)) { break } }
if (-not (Test-Port $Port)) { Good '端口已释放、无残留监听' } else { Bad "端口 $Port 仍有监听" }

if ($failed.Count -eq 0) { Note 'MOUNT-TEST PASS'; exit 0 }
Note "MOUNT-TEST FAIL（$($failed.Count) 项）"
exit 1
