# BL-14 判据 5 驱动器：安静窗 A/B（串行 vs 异步·全重叠），同一工作量
#
# ⚠ 为什么带 `--allow-busy`：本会话的 G7 **枚举自检**在**工作区解释器**下是 fail-closed 红的
#   （令牌限域：看不见 smss/csrrss/... 与 netstat 属主）⇒ 那一次**拒跑**是正确行为，已留原文。
#   改用**区外**解释器（`S:\...\tools\py312\python.exe`，与历轮一致）后枚举自检 **PASS**
#   （405 进程 / 系统进程 5/5 / netstat 一致），但 G7 仍数到 1 个**常驻**竞争者
#   = `paddleocr_mcp.exe`（pid 38956，RSS 223.9 MB，非本会话所起、⛔ 不动它）。
#   ⇒ 显式用 `--allow-busy`（工具自带的豁免，文档写明「读数已污染，只能当结构验证」），
#   并**另行量化**这个竞争者到底有没有在算：逐秒采它的 CPU 时间与 GPU 利用率，跑完报 ΔCPU。
#   判据：竞争者 ΔCPU ≈ 0（常驻≠在算）⇒ 窗口对 **CPU/IO** 实际是静的；这一点必须在报告里写清。
$ErrorActionPreference = 'Continue'
$pys  = 'S:\mahjong-training\tools\py312\python.exe'
$repo = 'C:\Users\HP\source\games\mahjong'
$w    = 'C:\Users\HP\source\games\mahjong\tmp\bl14'
$ev   = 'S:\mahjong-training\w-ladder\_bl14'
$ab   = Join-Path $ev 'ab'
New-Item -ItemType Directory -Force -Path $ab | Out-Null
$drv  = Join-Path $ab 'driver.log'
$gpu  = Join-Path $ab 'gpu.csv'
$comp = Join-Path $ab 'competitor.csv'
Remove-Item $gpu, $comp -ErrorAction SilentlyContinue

function Say($s) { $s | Tee-Object -FilePath $drv -Append | Out-Null; Write-Output $s }

Say "===== BL-14 判据 5（安静窗 A/B）开始 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====="
Say "解释器（区外，枚举自检要用它）：$pys"

# ---- 竞争者量化：逐秒记 pid 38956 的 CPU 秒 + GPU 利用率（零 CIM，只用 Get-Process + nvidia-smi）
$smp = Start-Job -ScriptBlock {
    param($gpu, $comp)
    $t0 = Get-Date
    while ($true) {
        $g = (& nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits 2>$null) -join ' '
        $p = Get-Process -Id 38956 -ErrorAction SilentlyContinue
        $c = if ($p) { $p.CPU } else { -1 }
        $m = if ($p) { [math]::Round($p.WorkingSet64 / 1MB, 1) } else { -1 }
        "{0:N3},{1},{2},{3}" -f ((Get-Date) - $t0).TotalSeconds, $c, $m, ($g -replace ',', '/') |
            Add-Content -Path $comp
        $g -replace ',', '/' | Add-Content -Path $gpu
        Start-Sleep -Milliseconds 1000
    }
} -ArgumentList $gpu, $comp
Start-Sleep -Seconds 3

$codes = @{}
foreach ($seg in @(@{ tag = 'SER'; ov = $false; spec = "$ev\spec-serial.json" },
                   @{ tag = 'ASY'; ov = $true;  spec = "$ev\spec-async.json" })) {
    Say ""
    Say "----- 段 $($seg.tag)：$(if ($seg.ov) { '异步（训练 ‖ 采集，全重叠）' } else { '串行（训练 → 采集）' }) · $(Get-Date -Format 'HH:mm:ss') -----"
    $chk = & $pys "$repo\tools\overlap-orchestrator.py" check --pair train:collect --driver $seg.spec `
        --data-root "$w\root" --compact-root "$w\compact" --raw-root "$w\raw" --workers 8 `
        --evidence-dir $ab --allow-busy 2>&1
    $chk | ForEach-Object { Say "  $_" }
    $chkrc = $LASTEXITCODE
    # ⚠ `check` 不认 `--allow-busy`（豁免只在 `Runner.run` 里生效）⇒ 这里显式判：
    #   **只在**「枚举自检 OK ∧ 唯一的拒绝理由是 G7 竞争负载」时才继续（正是本会话的已知情形）。
    #   ⛔ 枚举自检/探针 fail-closed 那次红是**不许豁免**的 —— 见到就退出。
    $chkTxt = ($chk | Out-String)
    $enumOk = $chkTxt -match '\[OK  \] G7 枚举自检'
    $onlyBusy = $chkTxt -match '结论：⛔ 拒绝重叠（G7 有竞争负载）' -and
                -not ($chkTxt -match '枚举本身不可信')
    Say "  [check] exit=$chkrc · 枚举自检OK=$enumOk · 唯一拒绝理由=竞争负载($onlyBusy)"
    if ($chkrc -ne 0 -and -not ($enumOk -and $onlyBusy)) {
        Say "  ⛔ check 未过且**不是**已知的『常驻竞争者』情形 ⇒ 跳过这一段（fail-closed）"
        $codes[$seg.tag] = "gate:$chkrc"
        continue
    }
    $flag = @()
    if ($seg.ov) { $flag += '--overlap' }
    $out = & $pys "$repo\tools\overlap-orchestrator.py" pair --pair train:collect --driver $seg.spec `
        --data-root "$w\root" --compact-root "$w\compact" --raw-root "$w\raw" --workers 8 `
        --evidence-dir $ab --allow-busy `
        --out "$ab\$($seg.tag).result.json" --rounds "$ab\rounds.jsonl" @flag 2>&1
    $out | ForEach-Object { Say "  $_" }
    $codes[$seg.tag] = $LASTEXITCODE
    Say "  [run] exit=$LASTEXITCODE · 结束 $(Get-Date -Format 'HH:mm:ss')"
}
Stop-Job $smp -ErrorAction SilentlyContinue
Remove-Job $smp -Force -ErrorAction SilentlyContinue
Say ""
Say "===== 退出码：$($codes | ConvertTo-Json -Compress) ====="
Say "===== 结束 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====="
exit 0
