# 季联赛：在线自对弈（带**对手池** + **接受闸门**）+ 结果奖励。
#
#   · 采集桌 = 学生×2（当前网，温度 0.5）+ teacher + **对手池里的一个历史快照**（按代轮换）
#   · 奖励 = 小局收支（`--value-target delta`）+ 顺位点（`--rank-weight 0.2`）
#   · **接受闸门**（第五十五轮加）：每代跑完，用**多套牌山集合**的合并配对判决跟"现任"比；
#     只有 CI 排除 0 且为正才采纳（成为新现任、进对手池），否则**回滚**并把这代挪进 `_rejected/`。
#     ⚠ 为什么必须有：第二季 8 代"跑完即采纳"，终点比它的起点退步 6.13 顺位点
#     `[-9.50,-2.74]`，而逐代 200 场读数是纯噪声（±8~10 的 CI 摆动）⇒ 没有闸门，训练会把退步当推进。
#   · **只跑缓存档**：固定 `--games 1000`（紧凑集实测 ≈14.8 GB < 0.8×31.6 GB = 25.3 GB）
#     —— 刻意**不**用 `--target-minutes`，免得规划器为了凑时间把轮次顶到磁盘档（45 分钟那档）。
#   · S 盘：每代成功后轮换删掉这一代的大件（raw/compact/eval）。
#
# 用法：pwsh -File tools\run-league.ps1 [-Generations 5] [-Games 1000] [-GateBlock 1000]
param([int]$Generations = 5, [int]$Games = 1000, [int]$GateBlock = 1000)
$ErrorActionPreference = 'Stop'
$root = 'C:\Users\HP\source\games\mahjong'
$S    = 'S:\mahjong-training'

# ⚠ 解释器：**优先用 S: 上那份 3.12**（镜像在工作区外 ⇒ 不受 overlay hook 管，能写数据根）。
$pyS = 'S:\mahjong-training\tools\py312\python.exe'
$pyVenv = Join-Path $root 'python\.venv\Scripts\python.exe'
$py = if (Test-Path $pyS) { $pyS } else { $pyVenv }

# ⚠ 训练端也要用**数据根上那份副本**（工作区里的 exe 写数据根会被静默吞掉），
#   且**每次开跑前按时间戳同步**：`v4policy.cpp` 一改就要重编，忘了同步会拿旧 exe 跑，
#   症状极具误导性（例如"加载 v4 权重失败：[1,195] != [1,192]"，看着像代码 bug）。
$trainerS = 'S:\mahjong-training\tools\trainer\trainer.exe'
if (Test-Path $trainerS) {
    $trainerRepo = Join-Path $root 'trainer\build\trainer.exe'
    if ((Test-Path $trainerRepo) -and
        ((Get-Item $trainerRepo).LastWriteTime -gt (Get-Item $trainerS).LastWriteTime)) {
        Copy-Item $trainerRepo $trainerS -Force
        Write-Output "（已把仓库里的 trainer.exe 同步到 S:）"
    }
    $env:MAHJONG_TRAINER = $trainerS
}
$env:PYTHONPATH = (Join-Path $root 'python') + ';' + (Join-Path $root 'python\.venv\Lib\site-packages')
$label = 'v4-league6'
$seed  = 20261001
# 现任 = **第一季终点 g08**（第五十四轮三个配对里 2 胜 0 负的那个）。
$incumbent = Join-Path $root 'tools\build\v4-league-g08\net.bin'
$pool  = New-Object System.Collections.Generic.List[string]
$rejected = Join-Path $root 'tools\build\_rejected'
$gateSeeds = "$seed,$($seed + 1)"   # 2 套牌山（省 ~1/3 闸门时间，仍是多集合）

function Free-GB { (Get-PSDrive S).Free / 1GB }
function Log($m) { Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $m) }

Log ("开始：{0} 代 × {1} 场（在线自对弈 + 对手池 + **接受闸门**）" -f $Generations, $Games)
Log ("现任（incumbent）= {0}" -f (Split-Path $incumbent -Parent | Split-Path -Leaf))
Log ("闸门：每代 2 套牌山（{0}）× {1} 场/套；S 盘剩余 {2:N0} GB" -f $gateSeeds, $GateBlock, (Free-GB))

# ⚠ **开跑前先验闸门方向**：`eval.paired_test` 的约定是 `diff = a − b`（正 = a 更好），
#   而"采纳"问的是**候选更好吗** ⇒ `gate.decide` 必须按 `(候选, 现任)` 的顺序算。
#   实测踩过：顺序写反 ⇒ **采纳判据整个反向**（把更差的候选采纳、把略好的拒掉），
#   而**空对照（Δ=0）两种写法都判不采纳**，抓不出方向问题 ⇒ 必须跑强弱对照。
$scLog = Join-Path $root 'release\gate-selfcheck.log'
& $py -m mahjong_ml.v4.gate --self-check --workers 12 --out (Join-Path $S 'gate') *> $scLog
if ($LASTEXITCODE -ne 0) {
    Log '⛔ 闸门方向自证失败（弱现任 vs 强候选没能采纳）—— 不开始训练，先修闸门'
    Get-Content $scLog | Select-String -Pattern 'PASS|FAIL|正确顺序|写反顺序' | ForEach-Object { Log ("  " + $_.Line.Trim()) }
    exit 1
}
Log '闸门方向自证通过（弱现任 vs 强候选 ⇒ 采纳；反向 ⇒ 拒绝）'
for ($g = 1; $g -le $Generations; $g++) {
    $tag = "$label-g{0:D2}" -f $g
    $opp = @()
    if ($pool.Count -gt 0) { $opp = $pool | Select-Object -Last 2 }
    $largs = @('-m', 'mahjong_ml.v4', 'loop', '--label', $label, '--init', $incumbent,
               '--generations', '1', '--gen-offset', "$($g - 1)",
               '--games', "$Games", '--workers', '20', '--eval-workers', '20',
               '--objective', 'ppo', '--value-target', 'delta', '--advantage', 'hand',
               '--rank-weight', '0.2', '--max-steps', '600', '--epochs', '1',
               '--kl-early-stop', '0.03', '--kl-min-steps', '60', '--critic-steps', '0',
               '--eval-games', '200', '--eval-vs', 'prev', '--seed', "$seed", '--no-java')
    foreach ($p in $opp) { $largs += @('--opponents', $p) }
    Log ("=== 第 {0} 代（{1}）init={2} 对手池={3} ===" -f $g, $tag,
         (Split-Path $incumbent -Parent | Split-Path -Leaf),
         $(if ($opp.Count) { ($opp | ForEach-Object { Split-Path $_ -Parent | Split-Path -Leaf }) -join ',' } else { '（无）' }))
    $log = Join-Path $root "release\$tag.log"
    & $py @largs *> $log
    $rc = $LASTEXITCODE
    if ($rc -ne 0) { Log ("第 {0} 代失败（退出码 {1}）—— 保留现场并停止" -f $g, $rc); break }

    foreach ($d in @("raw\$tag", "compact\$tag", "raw\eval-$tag")) {
        $p = Join-Path $S $d
        if (Test-Path $p) {
            $sz = (Get-ChildItem $p -Recurse | Measure-Object Length -Sum).Sum / 1MB
            Remove-Item -Recurse -Force $p
            Log ("  轮换删除 {0}（{1:N0} MB）" -f $d, $sz)
        }
    }
    $candidate = Join-Path $root "tools\build\$tag\net.bin"
    if (-not (Test-Path $candidate)) { Log "缺 $candidate，停止"; break }

    # ---- 接受闸门：多套牌山集合的合并配对判决（CI 排除 0 且为正才采纳）
    Log ("  闸门：现任 vs {0}（2 套牌山 × {1} 场）" -f $tag, $GateBlock)
    $gateLog = Join-Path $root "release\gate-$tag.log"
    & $py -m mahjong_ml.v4.gate --incumbent $incumbent --candidate $candidate `
        --seeds $gateSeeds --games $GateBlock --block $GateBlock --workers 20 `
        --out (Join-Path $S 'gate') --tag $tag *> $gateLog
    $grc = $LASTEXITCODE
    Get-Content $gateLog | Select-String -Pattern '牌山 20|合并判决' | ForEach-Object { Log ("    " + $_.Line.Trim()) }
    # ⚠ **候选一律进池（无论是否被采纳）**：池是**采集时的对手**、现任是**基准**，两件事。
    #   第五季实测踩过：把"进池"绑在"采纳"上 ⇒ 一个都没采纳 ⇒ 池永远是空的 ⇒ 采集桌上
    #   只剩 student×2 + teacher×2，**把第一季赖以起效的历史快照多样性整条掐掉了**。
    $pool.Add($candidate)
    if ($grc -eq 0) {
        $incumbent = $candidate
        Log ("  ⇒ **采纳**：新现任 = {0}；对手池 {1} 个" -f $tag, $pool.Count)
    } elseif ($grc -eq 3) {
        New-Item -ItemType Directory -Force -Path (Join-Path $rejected $tag) | Out-Null
        Move-Item $candidate (Join-Path $rejected "$tag\net.bin") -Force
        Log ("  ⇒ **不采纳**：回滚到现任；这一代挪到 _rejected\{0}" -f $tag)
    } else {
        Log ("  ⛔ 闸门出错（退出码 {0}）—— 保留现场并停止" -f $grc)
        break
    }
    Log ("S 盘剩余 {0:N0} GB" -f (Free-GB))
}
Log '季联赛结束'
