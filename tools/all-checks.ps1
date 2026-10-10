<#
.SYNOPSIS
  一键全检查：把本会话里**手工反复敲**的那几条验证命令收敛成一条，输出紧凑 PASS/FAIL 表 + 汇总退出码。

.DESCRIPTION
  为什么要有它：`AGENTS.md` §4 的验证五层是**判据**，但"改完要跑哪几层"在会话里表现为
  一连串手敲的命令（L1 selftest / L2 selftest / python selfcheck / 三条文档自检 / git status），
  于是 ① 漏跑一层没人发现 ② 每层的原始输出与退出码散在历史里、事后无法复核
  ③ "看起来绿了"里混着"其实没跑"。这里把这几层变成**一张表 + 一个退出码**。

  三条纪律（与仓库其它工具一致）：
    · **不静默降级**：缺前置（jar / exe / venv）判 **FAIL**，⛔ 不判 SKIP —— 只有调用者**显式**
      写 `-Skip<层>` 才是跳过，且跳过会**打在表里**并写进"已跳过"一行。
    · **退出码 0 不算证据**：L1 / L2 还要求输出里真的出现 `SELFTEST PASS` 标记（退出码 0 而没标记
      = FAIL），因为这两层的自检**自己会打印** PASS/FAIL。
    · **JVM 崩溃不算通过**：L1 走 `tools/jvm-guard.mjs`（本机偶发 `c2 mahjong.rules.Shanten.dfs`
      访问违例，见 `NOTES.md` §4）—— 靠重试才绿的状态是 **RETRIED-PASS**（退出码 3），
      不是 PASS；表里与汇总都会写明"原始失败 N 次"。

.PARAMETER SkipL1 / SkipL2 / SkipPython / SkipStatic / SkipGit
  显式跳过的层（跳过会打在表里，不是静默）。
.PARAMETER RequireClean
  把"工作树有未提交文件"也算失败（日常不推荐：改动中间态本来就该是脏的）。
.PARAMETER OutDir
  每层的原始输出落点（缺省 `python\.tmp\all-checks`，已 gitignore）。
.PARAMETER Selftest
  自检 + **负向自证**：用桩命令钉住"该红的必须红"（含"缺前置必须 FAIL"）。

.EXAMPLE
  pwsh -File tools\all-checks.ps1                 # 全跑
  pwsh -File tools\all-checks.ps1 -SkipL2         # 跳过客户端自检（慢/需要桌面）
  pwsh -File tools\all-checks.ps1 -Selftest       # 自检 + 负向自证
#>
[CmdletBinding()]
param(
    [switch]$Selftest,
    [switch]$SkipL1,
    [switch]$SkipL2,
    [switch]$SkipPython,
    [switch]$SkipStatic,
    [switch]$SkipGit,
    [switch]$RequireClean,
    [switch]$AcceptRetryPass,
    [string]$OutDir = 'python\.tmp\all-checks',
    [int]$TimeoutSec = 1200
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot        # tools\ 的上一级 = 仓库根
$OutPath = if ([System.IO.Path]::IsPathRooted($OutDir)) { $OutDir } else { Join-Path $Root $OutDir }
$Stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')

# ---------------------------------------------------------------- 层定义
# `Mark` = 该层"通过"时输出里**必须**出现的标记（没有标记 ⇒ 即便退出码 0 也判 FAIL）。
# `Pre`  = 前置文件（相对仓库根）；缺了判 FAIL，并给出构建命令。
function Get-Layers {
    @(
        @{ Name = 'L1 规则引擎';     Key = 'SkipL1';   Cmd = 'node'; Args = @('tools/jvm-guard.mjs',
                '--out', (Join-Path $OutPath 'jvm-guard'), '--json', (Join-Path $OutPath 'jvm-guard.json'),
                '--mark', 'SELFTEST PASS',
                '--', 'java', '-jar', 'server/build/mahjong-server.jar', '--selftest');
           Pre = 'server\build\mahjong-server.jar'
           Hint = 'pwsh -File server\build.ps1'; Guard = $true },
        @{ Name = 'L2 客户端自检';   Key = 'SkipL2';   Cmd = (Join-Path $Root 'client\dist\mahjong-client.exe');
           Args = @('--selftest', (Join-Path $Root 'client\build\st'));
           Mark = 'SELFTEST PASS'; Pre = 'client\dist\mahjong-client.exe'
           Hint = 'pwsh -File client\build.ps1 -Deploy' },
        @{ Name = 'python selfcheck'; Key = 'SkipPython'; Cmd = (Join-Path $Root 'python\.venv\Scripts\python.exe');
           Args = @('selfcheck.py'); Cwd = (Join-Path $Root 'python');
           Env = @{ MAHJONG_DATA_ROOT = (Join-Path $Root 'python\.tmp\selfcheck-root') };
           Mark = 'SELFCHECK PASS'; Pre = 'python\.venv\Scripts\python.exe'
           Hint = 'python\ 下建 .venv（见 NOTES.md §4 的三道坑）' },
        @{ Name = 'check-i18n';      Key = 'SkipStatic'; Cmd = 'node'; Args = @('tools/check-i18n.mjs') },
        @{ Name = 'i18n-gen --check';Key = 'SkipStatic'; Cmd = 'node'; Args = @('tools/i18n-gen.mjs', '--check') },
        @{ Name = 'i18n-scan --check';Key = 'SkipStatic'; Cmd = 'node'; Args = @('tools/i18n-scan.mjs', '--check') },
        @{ Name = 'doc-refs-check';  Key = 'SkipStatic'; Cmd = 'node'; Args = @('tools/doc-refs-check.mjs', '--quiet');
           Mark = '[doc-refs] PASS' },
        @{ Name = 'round-index --check'; Key = 'SkipStatic'; Cmd = 'node'; Args = @('tools/round-index.mjs', '--check');
           Mark = '[round-index] PASS' },
        @{ Name = 'handover-facts --check'; Key = 'SkipStatic'; Cmd = 'node'; Args = @('tools/handover-facts.mjs', '--check');
           Mark = '[handover-facts] PASS' }
    )
}

# ---------------------------------------------------------------- 单层执行
function Invoke-Layer {
    param([hashtable]$Layer, [string]$LogDir)
    $r = [ordered]@{ name = $Layer.Name; state = 'FAIL'; sec = 0.0; summary = ''; exit = -1 }
    # 前置：缺了就是 FAIL（⛔ 不静默降级成 SKIP）
    if ($Layer.Pre) {
        $pre = Join-Path $Root $Layer.Pre
        if (-not (Test-Path -LiteralPath $pre)) {
            $r.summary = "缺前置 $($Layer.Pre) —— 先跑：$($Layer.Hint)"
            return $r
        }
    }
    $exe = $Layer.Cmd
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $exe)) {
        $r.summary = "找不到可执行文件 $exe"
        return $r
    }
    $log = Join-Path $LogDir ("{0}-{1}.log" -f ($Layer.Name -replace '[^\w\.\-]', '_'), $Stamp)
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $exe
    foreach ($a in $Layer.Args) { [void]$psi.ArgumentList.Add($a) }
    $psi.WorkingDirectory = if ($Layer.Cwd) { $Layer.Cwd } else { $Root }
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    if ($Layer.Env) { foreach ($k in $Layer.Env.Keys) { $psi.Environment[$k] = $Layer.Env[$k] } }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $proc = [System.Diagnostics.Process]::Start($psi)
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    $ok = $proc.WaitForExit($TimeoutSec * 1000)
    if (-not $ok) {
        try { $proc.Kill($true) } catch { }
        $sw.Stop()
        $r.sec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
        $r.summary = "超时（> $TimeoutSec s），已杀进程"
        return $r
    }
    $stdout = $outTask.Result
    $stderr = $errTask.Result
    $sw.Stop()
    $r.sec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    $r.exit = $proc.ExitCode
    $text = ($stdout, $stderr -join "`n")
    Set-Content -LiteralPath $log -Value $text -Encoding utf8
    $r.log = $log

    # 摘要 = 输出里最后一条非空行（人一眼能看出"哪一句说了结论"）
    $lines = @($text -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
    $r.summary = if ($lines.Count -gt 0) { $lines[-1].Trim() } else { '(无输出)' }

    # 状态判定：退出码 + 通过标记（两者都要）——
    #   jvm-guard 的退出码 3 = 「带重试的通过」，**单列**成 RETRIED-PASS，不许混进 PASS
    if ($r.exit -eq 3 -and $Layer.Guard) {
        $r.state = 'RETRIED-PASS'
        $orig = [regex]::Match($text, '原始失败 \*\*(\d+)\*\* 次')
        if ($orig.Success) { $r.originalFailures = [int]$orig.Groups[1].Value }
    } elseif ($r.exit -eq 0) {
        if ($Layer.Mark -and ($text -notmatch [regex]::Escape($Layer.Mark))) {
            $r.state = 'FAIL'
            $r.summary = "退出码 0 但输出里没有「$($Layer.Mark)」标记 ⇒ 判 FAIL（退出码 0 不算证据）"
        } else {
            $r.state = 'PASS'
        }
    } else {
        $r.state = 'FAIL'
    }
    # 计数提取：L1「通过 N 项，失败 M 项」/ L2「检查项 N，失败 M」/ selfcheck「N/N」
    foreach ($rx in @('通过\s*(\d+)\s*项[，,]\s*失败\s*(\d+)\s*项',
                      '检查项\s*(\d+)[，,]\s*失败\s*(\d+)',
                      'SELFCHECK PASS（(\d+)\s*/\s*(\d+)')) {
        $m = [regex]::Match($text, $rx)
        if ($m.Success) { $r.counts = "$($m.Groups[1].Value) 项 / 失败 $($m.Groups[2].Value)"; break }
    }
    return $r
}

# ---------------------------------------------------------------- 主流程
function Invoke-All {
    param([switch]$DoNotRun)
    if (-not (Test-Path -LiteralPath $OutPath)) { New-Item -ItemType Directory -Force -Path $OutPath | Out-Null }
    $layers = Get-Layers
    $skipped = @()
    $rows = @()
    Write-Host "== all-checks @ $Stamp =="
    Write-Host "仓库：$Root   输出：$OutPath"
    foreach ($l in $layers) {
        $flagName = $l.Key
        # 开关在**脚本作用域**：函数里用 Get-Variable 往父作用域找（`$PSBoundParameters` 在这里是空的）
        $isSkip = [bool](Get-Variable -Name $flagName -ValueOnly -ErrorAction SilentlyContinue)
        if ($isSkip) {
            $skipped += $l.Name
            $rows += [ordered]@{ name = $l.Name; state = 'SKIP'; sec = 0.0; summary = "显式跳过（-$flagName）"; exit = $null }
            continue
        }
        Write-Host ("  … 跑 {0}" -f $l.Name)
        $rows += Invoke-Layer -Layer $l -LogDir $OutPath
    }
    # git status 是**信息行**：脏只提示，不判失败（除非 -RequireClean）
    if (-not $SkipGit) {
        $porcelain = (& git -C $Root status --porcelain 2>$null | Out-String).Trim()
        $dirty = @($porcelain -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
        if ($dirty.Count -eq 0) {
            $rows += [ordered]@{ name = 'git status'; state = 'CLEAN'; sec = 0.0; summary = '工作树干净'; exit = 0 }
        } else {
            $state = if ($RequireClean) { 'FAIL' } else { 'DIRTY' }
            $rows += [ordered]@{ name = 'git status'; state = $state; sec = 0.0
                summary = "$($dirty.Count) 个未提交条目（$($dirty[0])…）"; exit = 0 }
        }
    }
    Write-Host ''
    Write-Host '层                        状态          秒     摘要'
    Write-Host ('-' * 96)
    foreach ($r in $rows) {
        $counts = if ($r.counts) { "（$($r.counts)）" } else { '' }
        Write-Host ("{0,-24}  {1,-12}  {2,7}  {3}{4}" -f $r.name, $r.state, $r.sec, $r.summary, $counts)
    }
    Write-Host ('-' * 96)
    if ($skipped.Count -gt 0) { Write-Host "已跳过（显式）：$($skipped -join ' / ')" }

    $fails = @($rows | Where-Object { $_.state -eq 'FAIL' })
    $retried = @($rows | Where-Object { $_.state -eq 'RETRIED-PASS' })
    $code = if ($fails.Count -gt 0) { 1 } elseif ($retried.Count -gt 0 -and -not $AcceptRetryPass) { 3 } else { 0 }
    if ($retried.Count -gt 0) {
        foreach ($r in $retried) {
            Write-Host ("⛔ {0} 是**带重试的通过**（原始失败 {1} 次）—— 这不是干净通过；要当通过用请显式加 -AcceptRetryPass" `
                -f $r.name, $r.originalFailures)
        }
    }
    Write-Host ("汇总：PASS {0} / RETRIED-PASS {1} / FAIL {2} / SKIP {3} ⇒ 退出码 {4}" -f `
        @($rows | Where-Object { $_.state -in @('PASS', 'CLEAN') }).Count, $retried.Count, $fails.Count, $skipped.Count, $code)
    $json = Join-Path $OutPath "summary-$Stamp.json"
    ($rows | ConvertTo-Json -Depth 4) | Set-Content -LiteralPath $json -Encoding utf8
    Write-Host "汇总 JSON：$json"
    return $code
}

# ---------------------------------------------------------------- 自检 + 负向自证
function Invoke-SelfTest {
    # 判据不是"能跑起来"，而是**该红的必须红**：四条桩里两条是故意应当失败的。
    $bad = 0
    $sb = Join-Path $OutPath "selftest-$Stamp"
    New-Item -ItemType Directory -Force -Path $sb | Out-Null
    $node = (Get-Command node).Source
    $cases = @(
        @{ desc = '① 干净的成功层 ⇒ PASS'; want = 'PASS'; layer = @{ Name = 'stub-pass'; Cmd = $node
                Args = @('-e', 'console.log("SELFTEST PASS");'); Mark = 'SELFTEST PASS' } },
        @{ desc = '② 退出码非 0 ⇒ FAIL（负向自证）'; want = 'FAIL'; layer = @{ Name = 'stub-fail'; Cmd = $node
                Args = @('-e', 'console.log("boom"); process.exit(1)') } },
        @{ desc = '③ 退出码 0 但没有通过标记 ⇒ FAIL（负向自证：退出码 0 不算证据）'; want = 'FAIL'
            layer = @{ Name = 'stub-nomark'; Cmd = $node; Args = @('-e', 'console.log("看着挺好")');
                       Mark = 'SELFTEST PASS' } },
        @{ desc = '④ 缺前置 ⇒ FAIL（负向自证：⛔ 不许静默降级成 SKIP）'; want = 'FAIL'
            layer = @{ Name = 'stub-pre'; Cmd = $node; Args = @('-e', 'console.log("不该跑到这里")');
                       Pre = 'tools\__no_such_file__.xyz'; Hint = 'n/a' } }
    )
    foreach ($c in $cases) {
        $r = Invoke-Layer -Layer $c.layer -LogDir $sb
        $ok = ($r.state -eq $c.want)
        if (-not $ok) { $bad++ }
        Write-Host ("{0} {1} ⇒ state={2}（期望 {3}）" -f `
            $(if ($ok) { '  [ok]  ' } else { '  [FAIL]' }), $c.desc, $r.state, $c.want)
    }
    Write-Host $(if ($bad -eq 0) { '[all-checks] SELFTEST PASS（4/4，含 3 条负向自证）' }
                 else { "[all-checks] SELFTEST FAIL（$bad 例）" })
    return $(if ($bad -eq 0) { 0 } else { 1 })
}

if ($Selftest) { exit (Invoke-SelfTest) }
exit (Invoke-All)
