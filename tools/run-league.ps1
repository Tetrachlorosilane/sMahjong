# 在线自对弈（带**对手池**）+ 结果奖励：逐代跑，每代结束把这一代的 net 加进对手池。
#
#   · 采集桌 = 学生×2（当前网，温度 0.5）+ teacher + **对手池里的一个历史快照**（按代轮换）
#   · 奖励 = 小局收支（`--value-target delta`）+ 顺位点（`--rank-weight 0.2`）—— 都是**结果**
#   · 每代评测：2+2 对**上一代**（`--eval-vs prev`，同牌山配对）—— 看"这一代有没有长进"
#   · S 盘：每代成功后删掉那一代的大件（raw/compact/eval），只留 ckpt / net.bin / 台账 / 审计
#
# 用法：pwsh -File release/run-league.ps1 [-Generations 8] [-Games 1000]
param([int]$Generations = 8, [int]$Games = 1000)
$ErrorActionPreference = 'Stop'
$py   = 'C:\Users\HP\source\games\mahjong\python\.venv\Scripts\python.exe'
$root = 'C:\Users\HP\source\games\mahjong'
$S    = 'S:\mahjong-training'
$env:PYTHONPATH = Join-Path $root 'python'
$label = 'v4-league'
$seed  = 20261001
$init  = Join-Path $root 'tools\build\v4-p3-001\net.bin'
$pool  = New-Object System.Collections.Generic.List[string]

function Free-GB { (Get-PSDrive S).Free / 1GB }
function Log($m) { Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $m) }

Log ("开始：{0} 代 × {1} 场（在线自对弈 + 对手池）；S 盘剩余 {2:N0} GB" -f $Generations, $Games, (Free-GB))
for ($g = 1; $g -le $Generations; $g++) {
    $tag = "$label-g{0:D2}" -f $g
    # 对手池取**最近两代**（更早的已经落后太多，留着只会稀释结果奖励的信息）
    $opp = @()
    if ($pool.Count -gt 0) { $opp = $pool | Select-Object -Last 2 }
    $args = @('-m', 'mahjong_ml.v4', 'loop', '--label', $label, '--init', $init,
              '--generations', '1', '--gen-offset', "$($g - 1)",
              '--games', "$Games", '--workers', '20', '--eval-workers', '20',
              '--objective', 'ppo', '--value-target', 'delta', '--advantage', 'hand',
              '--rank-weight', '0.2', '--max-steps', '2000', '--epochs', '2',
              '--kl-early-stop', '0.15', '--kl-min-steps', '60', '--critic-steps', '0',
              '--eval-games', '200', '--eval-vs', 'prev', '--seed', "$seed", '--no-java')
    foreach ($p in $opp) { $args += @('--opponents', $p) }
    Log ("=== 第 {0} 代（{1}）init={2} 对手池={3}；S 盘 {4:N0} GB ===" -f $g, $tag, $init,
         $(if ($opp.Count) { ($opp | ForEach-Object { Split-Path $_ -Parent | Split-Path -Leaf }) -join ',' } else { '（无）' }), (Free-GB))
    $log = Join-Path $root "release\$tag.log"
    & $py @args *> $log
    $rc = $LASTEXITCODE
    Log ("第 {0} 代退出码 {1}（日志 {2}）" -f $g, $rc, $log)
    if ($rc -ne 0) { Log '这一代失败，停止（保留现场）'; break }

    foreach ($d in @("raw\$tag", "compact\$tag", "raw\eval-$tag")) {
        $p = Join-Path $S $d
        if (Test-Path $p) {
            $sz = (Get-ChildItem $p -Recurse | Measure-Object Length -Sum).Sum / 1MB
            Remove-Item -Recurse -Force $p
            Log ("  轮换删除 {0}（{1:N0} MB）" -f $d, $sz)
        }
    }
    $init = Join-Path $root "tools\build\$tag\net.bin"
    if (-not (Test-Path $init)) { Log "缺 $init，停止"; break }
    $pool.Add($init)
    Log ("S 盘剩余 {0:N0} GB；对手池现在 {1} 个" -f (Free-GB), $pool.Count)
}
Log '联赛结束'
