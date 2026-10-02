# 季联赛：在线自对弈（带**对手池** + **接受闸门**）+ 结果奖励。
#
#   ⚠ **第八季（`v4-league8`）= W1 的验证季**：配方与第七季**逐字相同**（同 `--max-steps 600`、
#     同 `--seed 20261001` ⇒ 同两套闸门牌山），**只多了一个变量** —— 模型多了 `fusion.mem`
#     长程门控、数据集的 `h0` 列（窗口之前的整手 carry）真的喂进前向。
#     于是"这一季的判决"可以直接与第七季（5/5 被拒、均值 −1.10）对照：若 W1 有价值，
#     应当看到"至少一代被采纳"；若还是 5/5 被拒，那 W1 在这套配方下**不是改进算子**。
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
#   · **W1b：`h0` 列随 `--student` 的网自动来** —— 数据集由 `mahjong_ml.v4 loop` 内部调
#     `v4.dataset build --student <这一轮学生的确切策略串>` 生成，而 `h0`（窗口之前的整手 carry）
#     就从这个 `--student` 串里的 `net:<路径>` 解析（剥掉 `@α`/`#T` 后缀）⇒ **这里不需要额外传
#     `--carry-model`**。口径：`h0` 一律用**学生网**算（= 采集那份权重 = 现任网 = 上线那份），
#     所以训练与推理同源；缺 `h0` 列时 `pretrain` 会**硬拒**（不许静默退化成窗口冷启动）。
#     本脚本自己**不建**数据集（没有别的 `dataset build` 调用点）—— 要改口径请改 `v4/loop.py`。
#
# 用法：pwsh -File tools\run-league.ps1 [-Generations 5] [-Games 1000] [-GateBlock 1000]
param([int]$Generations = 5, [int]$Games = 1000, [int]$GateBlock = 1000, [int]$Seed = 0)
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
$label = 'v4-league8'
# ⚠ **季种子不能再是常数**（第八季审计）：采集 seed = `cfg.seed + 绝对代号`，而整条命令行也是
#   同一批参数的确定函数 ⇒ 两个季只要 `--seed` 相同、"现任 + 对手池"相同，**同代号代就是逐字节
#   同一次运行**（实测：`v4-league4-g04` 与 `_rejected\v4-league5-g04` 同 SHA256、同 5,446,406 B；
#   league3/4/5 的 g01/g02、league6/7 的 g01 的逐套 Δ **逐位重复**）。后果有两条：
#   ① 白跑一整代（含 33 分钟闸门）；② 把"N 个候选"当独立样本统计是**伪重复**（有效样本远小于 N）。
#   现在按标签派生：`v4-league8` ⇒ 20261001 + 8×10000 = 20341001（需要复现旧季时显式 `-Seed`）。
$seed  = if ($Seed -gt 0) { $Seed } else { 20261001 + 10000 * [int]($label -replace '\D', '') }
# 现任 = **第一季终点 g08**（第五十四轮三个配对里 2 胜 0 负的那个）。
$incumbent = Join-Path $root 'tools\build\v4-league-g08\net.bin'
$pool  = New-Object System.Collections.Generic.List[string]
$rejected = Join-Path $root 'tools\build\_rejected'
# ⚠ **牌山每代换新**（第八季审计的第二条）：原来历季 19 个候选都用同一对 `20261001/2`，
#   而"牌山"这个误差分量是**共模**的 —— 同一个候选换成别的牌山会翻号（实测：回路自评换 seed
#   5/5 为正 +1.32~+3.10，闸门固定牌山 5/5 为负/零 −2.26~−0.09）⇒ 判决只能读成
#   "**条件于这两套牌山**"，不能读成"这一代普遍更好/更差"，更不能把历次判决当独立复现来统计。
#   从现在起按代派生两套新牌山（同一代内仍是多套合并 + 每套一个 block）。
$gateSeedsFor = { param($g) "$($seed + 2 * $g - 1),$($seed + 2 * $g)" }

function Free-GB { (Get-PSDrive S).Free / 1GB }
function Log($m) { Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $m) }

Log ("开始：{0} 代 × {1} 场（在线自对弈 + 对手池 + **接受闸门**）" -f $Generations, $Games)
Log ("现任（incumbent）= {0}" -f (Split-Path $incumbent -Parent | Split-Path -Leaf))
Log ("闸门：每代 2 套**新**牌山（按代派生）× {0} 场/套；S 盘剩余 {1:N0} GB" -f $GateBlock, (Free-GB))

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
    # ⚠ **闸门轨迹也要轮换**（第八季补）：`gate\<tag>\s<seed>\` 每个牌山目录里 1000 个 `g*.jsonl`
    #   约 2.7 GB ⇒ 每代 ~5.3 GB，而**历季从来不删**（第三~七季已堆到 138 GB，实测把 S 盘从
    #   222 GB 压到 90 GB）。这些 jsonl 对**判决是冗余的**：`eval.load_run` 只读 `summary.json`
    #   （`per_game[].rank_points/placement/final_scores/policies/seed`），实测整季 5 代复算只用 7 秒、
    #   26 GB 的 jsonl 一个字节都没读 ⇒ **删轨迹、留摘要**：仍然可以随时用 `python regate_gate.py`
    #   在当天的判据下重算历季判决（第八季就是靠它证明了"逐牌山打印符号"没有污染判决链）。
    $gd = Join-Path $S "gate\$tag"
    if (Test-Path $gd) {
        $gf = Get-ChildItem $gd -Recurse -File -Filter '*.jsonl'
        $mb = ($gf | Measure-Object Length -Sum).Sum / 1MB
        foreach ($f in $gf) { Remove-Item $f.FullName -Force }
        Log ("  轮换删除 gate\{0} 的 {1} 个 jsonl（{2:N0} MB；**保留 summary.json** 以便复判）" -f $tag, $gf.Count, $mb)
    }
    $candidate = Join-Path $root "tools\build\$tag\net.bin"
    if (-not (Test-Path $candidate)) { Log "缺 $candidate，停止"; break }

    # ---- ⚠ 候选去重（第八季审计）：与现任或池里任何一份**逐字节相同** ⇒ 这一代没有任何新信息。
    #   实测踩过：`v4-league4-g04` 与 `v4-league5-g04` 同 SHA256（同 seed、同 offset、池空 ⇒ 整条
    #   流水线逐字节复现）⇒ 白跑一场 33 分钟闸门，而且让"N 个候选"的统计**伪重复**。
    #   命中就跳过闸门、**不进池**（池要的是有信息的对手），并记一行日志。
    $candHash = (Get-FileHash $candidate -Algorithm SHA256).Hash
    $dupOf = $null
    if ((Get-FileHash $incumbent -Algorithm SHA256).Hash -eq $candHash) { $dupOf = '现任' }
    else {
        foreach ($p in $pool) {
            if ((Test-Path $p) -and (Get-FileHash $p -Algorithm SHA256).Hash -eq $candHash) {
                $dupOf = (Split-Path $p -Parent | Split-Path -Leaf); break
            }
        }
    }
    if ($dupOf) {
        Log ("  ⏭ **跳过闸门**：{0} 与 {1} 逐字节相同（没有新信息；换 `-Seed` 或改配方再跑）" -f $tag, $dupOf)
        continue
    }

    # ---- 接受闸门：多套牌山集合的合并配对判决（CI 排除 0 且为正才采纳）
    $gateSeeds = & $gateSeedsFor $g
    Log ("  闸门：现任 vs {0}（2 套**新**牌山 {1} × {2} 场）" -f $tag, $gateSeeds, $GateBlock)
    $gateLog = Join-Path $root "release\gate-$tag.log"
    & $py -m mahjong_ml.v4.gate --incumbent $incumbent --candidate $candidate `
        --seeds $gateSeeds --games $GateBlock --block $GateBlock --workers 20 `
        --out (Join-Path $S 'gate') --tag $tag *> $gateLog
    $grc = $LASTEXITCODE
    Get-Content $gateLog | Select-String -Pattern '牌山 |合并判决|套间' | ForEach-Object { Log ("    " + $_.Line.Trim()) }
    # ⚠ **候选一律进池（无论是否被采纳）**：池是**采集时的对手**、现任是**基准**，两件事。
    #   第五季实测踩过：把"进池"绑在"采纳"上 ⇒ 一个都没采纳 ⇒ 池永远是空的 ⇒ 采集桌上
    #   只剩 student×2 + teacher×2，**把第一季赖以起效的历史快照多样性整条掐掉了**。
    $pool.Add($candidate)
    if ($grc -eq 0) {
        $incumbent = $candidate
        Log ("  ⇒ **采纳**：新现任 = {0}；对手池 {1} 个" -f $tag, $pool.Count)
    } elseif ($grc -eq 3) {
        # ⚠ **必须 `Copy-Item` 而不是 `Move-Item`**：这一代的 net **已经在对手池里**（池记的就是
        #   原位路径），挪走会让池里那条悬空 ⇒ 下一代采集时 `net:` 打不开权重、整季当场死。
        #   实测踩过：`v4-league6` 第 2 代 `打不开权重文件 …\v4-league6-g01\net.bin`（退出码 2）。
        New-Item -ItemType Directory -Force -Path (Join-Path $rejected $tag) | Out-Null
        Copy-Item $candidate (Join-Path $rejected "$tag\net.bin") -Force
        Log ("  ⇒ **不采纳**：回滚到现任；这一代**副本**留在 _rejected\{0}（原位保留给对手池）" -f $tag)
    } else {
        Log ("  ⛔ 闸门出错（退出码 {0}）—— 保留现场并停止" -f $grc)
        break
    }
    Log ("S 盘剩余 {0:N0} GB" -f (Free-GB))
}
Log '季联赛结束'
