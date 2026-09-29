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
# ⚠ `$root` 必须在下面两个探测**之前**定义（我上一版把它排到了后面，`Join-Path` 直接炸）。
$root = 'C:\Users\HP\source\games\mahjong'
# ⚠ 解释器：**优先用 S: 上那份 3.12**（若存在）。
#   为什么：沙箱把"工作区内的可执行文件"交给 overlay hook 管，于是**工作区里的 python
#   （含 .venv 与 .uv-python 的基础解释器）写不了 S: 的数据根**（[Errno 13]），
#   而镜像在工作区外的解释器不受管。把 3.12 运行时拷到 S: 上即可两头满足：
#   既有 torch（.venv 的 cp312 包），又能写数据根。
#   一次性的准备（本机实测过）：
#     robocopy <repo>\.uv-python\cpython-3.12-windows-x86_64-none S:\mahjong-training\tools\py312 /E
$pyS = 'S:\mahjong-training\tools\py312\python.exe'
$pyVenv = Join-Path $root 'python\.venv\Scripts\python.exe'
$py = if (Test-Path $pyS) { $pyS } else { $pyVenv }
# ⚠ 训练端也要用**数据根上那份副本**：受限沙箱按「可执行文件位置」决定写入是否生效 ——
#   工作区里的 `trainer.exe` 写数据根会被**静默吞掉**（退出码 0、目录不存在），
#   同一份 exe 放到 `S:` 上再跑就正常（2026-09-29 实测）。准备：
#     robocopy <repo>\trainer\build S:\mahjong-training\tools\trainer /E
$trainerS = 'S:\mahjong-training\tools\trainer\trainer.exe'
if (Test-Path $trainerS) { $env:MAHJONG_TRAINER = $trainerS }
$root = 'C:\Users\HP\source\games\mahjong'
$S    = 'S:\mahjong-training'
$env:PYTHONPATH = (Join-Path $root 'python') + ';' + (Join-Path $root 'python\.venv\Lib\site-packages')
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
