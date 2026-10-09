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
#   · S 盘轮换：**每代判决之后**才删这一代的大件（`raw/<tag>`、`compact/<tag>`、`raw/eval-<tag>`、
#     `gate/<tag>/**/*.jsonl`）—— 顺序很重要，见闸门轮换那一段的注释（第八季补的轮换插错了位置，
#     整整两季**从未生效**）。
#   · **W1b：`h0` 列随 `--student` 的网自动来** —— 数据集由 `mahjong_ml.v4 loop` 内部调
#     `v4.dataset build --student <这一轮学生的确切策略串>` 生成，而 `h0`（窗口之前的整手 carry）
#     就从这个 `--student` 串里的 `net:<路径>` 解析（剥掉 `@α`/`#T` 后缀）⇒ **这里不需要额外传
#     `--carry-model`**。口径：`h0` 一律用**学生网**算（= 采集那份权重 = 现任网 = 上线那份），
#     所以训练与推理同源；缺 `h0` 列时 `pretrain` 会**硬拒**（不许静默退化成窗口冷启动）。
#     本脚本自己**不建**数据集（没有别的 `dataset build` 调用点）—— 要改口径请改 `v4/loop.py`。
#
# 用法：pwsh -File tools\run-league.ps1 [-Generations 5] [-Games 1000] [-GateBlock 1000] [-Label <季标签>]
#   `-Style <名>` + `-StylePools "atk=a1,a2;def=d1,d2;win=w1,w2"`：**风格桌**（2026-10-08 用户指定）——
#     训某风格线时，桌上**每个风格各 1 席**（学生 1 席 + 三种风格对手各 1 席），座位由 `--rotate` 平均。
#     ⚠ 这与 `-PoolSeed` 是**两件事**：后者只把 1 个座位换成池里的一员（桌上还有学生×2）⇒ 不许用于风格线。
#   `-DryRun`：打印这一季真会跑的命令行（含 `--il-weight` / `--val-metric` 的实际取值）后退出，不训练。
#   `-Label`：不传就用下面那个缺省标签（**换季不必改脚本** —— 改脚本正是"标签被复用"的来源之一）。
# ⚠ `-IlWeight` 的缺省**必须是 0**（2026-10-03 第六十三轮的负结果）：见下面 `--il-weight` 那一段。
param([int]$Generations = 5, [int]$Games = 1000, [int]$GateBlock = 1000, [int]$Seed = 0,
      [double]$IlWeight = 0.0, [double]$RefBeta = 0.5, [string]$Label = '', [switch]$DryRun, [switch]$NoGate, [string]$Incumbent = [string]::Empty, [string]$PoolSeed = [string]::Empty,
      [string]$Style = [string]::Empty, [string]$StylePools = [string]::Empty)
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
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
# ⚠⚠ **PowerShell 变量名大小写不敏感** ⇒ `$Seed`（参数）与下面派生的 `$seed` **是同一个变量**：
#   第 69 行一算，`$Seed` 就被改成了派生值（永远 > 0）⇒ 之后任何"是不是显式给了 `-Seed`"的判断
#   都会恒真（本次写标签占用闸门时实测踩到：闸门本该硬拦却放行）。所以**先把它记在另一个名字里**。
$explicitSeed = ($Seed -gt 0)
$label = if ($Label) { $Label } else { 'v4-league11' }
# ⚠ 标签必须**未占用**（第八季的 `v4-league10` 已被 A 路的负对照用掉）：下面有**硬闸门**自动拦
#   （`tools\build\<label>-*` 非空 ⇒ 报错退出），换季时不必再手工 `Test-Path` 六处。
# ⚠ **季种子不能再是常数**（第八季审计）：采集 seed = `cfg.seed + 绝对代号`，而整条命令行也是
#   同一批参数的确定函数 ⇒ 两个季只要 `--seed` 相同、"现任 + 对手池"相同，**同代号代就是逐字节
#   同一次运行**（实测：`v4-league4-g04` 与 `_rejected\v4-league5-g04` 同 SHA256、同 5,446,406 B；
#   league3/4/5 的 g01/g02、league6/7 的 g01 的逐套 Δ **逐位重复**）。后果有两条：
#   ① 白跑一整代（含 33 分钟闸门）；② 把"N 个候选"当独立样本统计是**伪重复**（有效样本远小于 N）。
#   现在按标签派生（**取标签里的全部数字** —— `-replace '\D',''` 会把 `v4` 的 `4` 也算进去）：
#   `v4-league8` ⇒ `'48'` ⇒ 20261001 + 480000 = **20741001**；`v4-league9` ⇒ `'49'` ⇒ **20751001**
#   （实测它的闸门目录就是 `s20751002/3`）；`v4-league10` ⇒ `'410'` ⇒ **24361001**。
#   ⚠ 第八季（`v4-league8`）当时是**显式** `-Seed 20261001` 跑的（它的闸门目录是 `s20261001/2`）
#   ⇒ 复现旧季**必须**显式 `-Seed`；这里原来那句"`v4-league8` ⇒ 20341001"是**错的**（公式漏了 `v4` 的 4）。
$seed  = if ($Seed -gt 0) { $Seed } else { 20261001 + 10000 * [int]($label -replace '\D', '') }
# 现任 = **第一季终点 g08**（第五十四轮三个配对里 2 胜 0 负的那个）。
# ⚠ 现任可换（多专家路线）：`-Incumbent <net.bin 绝对路径>`；缺省仍是第一季终点 g08。
#   专家线的种子是**已认证的专家**（打点线 = `v4-league14-g05`），而不是 g08。
$incumbent = if ($Incumbent) { $Incumbent } else { Join-Path $root 'tools\build\v4-league-g08\net.bin' }
$pool  = New-Object System.Collections.Generic.List[string]
# ⚠ 威胁源/风格对手预置（专家线用）：逗号分隔的 net.bin 绝对路径。
#   为什么需要：池空时桌面只有 student×2 + teacher×2 ⇒ "结果奖励里关于通用强度的信息很少"（loop.py 原话），
#   防守线要的是"高打点对手在桌上"的压力，不预置就造不出目标风格。
if ($PoolSeed) { foreach ($p in $PoolSeed.Split(',')) { if ($p.Trim()) { $pool.Add($p.Trim()) } } }
$rejected = Join-Path $root 'tools\build\_rejected'
# ⚠ **牌山每代换新**（第八季审计的第二条）：原来历季 19 个候选都用同一对 `20261001/2`，
#   而"牌山"这个误差分量是**共模**的 —— 同一个候选换成别的牌山会翻号（实测：回路自评换 seed
#   5/5 为正 +1.32~+3.10，闸门固定牌山 5/5 为负/零 −2.26~−0.09）⇒ 判决只能读成
#   "**条件于这两套牌山**"，不能读成"这一代普遍更好/更差"，更不能把历次判决当独立复现来统计。
#   从现在起按代派生两套新牌山（同一代内仍是多套合并 + 每套一个 block）。
# ⚠ +1000 偏移（2026-10-03 修）：采集 seed = `cfg.seed + 绝对代号`（loop.py:194/209），而原来闸门 = `seed+2g-1, seed+2g`
#   ⇒ **第 1 代的第一套闸门牌山（seed+1）正好是它自己的采集牌山**、g≥2 代则撞上一代的采集山 ⇒
#   "判决条件于一套见过的牌山"（第八季的教训）其实没修掉。挪开 1000 保证两者不相交。
$gateSeedsFor = { param($g) "$($seed + 1000 + 2 * $g - 1),$($seed + 1000 + 2 * $g)" }

function Free-GB { (Get-PSDrive S).Free / 1GB }
function Log($m) { Write-Output ("[{0:HH:mm:ss}] {1}" -f (Get-Date), $m) }

# ⚠ **标签占用闸门**（本次加）：标签既是 checkpoint 目录名、又**决定 `$seed` 与两套闸门牌山**
#   ⇒ 复用一个已被占用的标签 = **静默重跑同一季**（同 seed、同牌山、覆盖 `tools\build\<tag>`
#   与 S: 的 raw/compact/ckpt）—— 这正是第八季审计里"伪重复"的成因（`v4-league4-g04` 与
#   `_rejected\v4-league5-g04` 同 SHA256）。所以这里**硬拦**：换标签，或显式 `-Seed`（复现旧季的合法路径）。
$taken = @(Get-ChildItem (Join-Path $root 'tools\build') -Directory -ErrorAction SilentlyContinue |
           Where-Object { $_.Name -like "$label-*" })
if ($taken.Count -gt 0) {
    # ⚠ 字符串末尾别让反引号紧贴收尾引号（`` `x `` 会吃掉 `` " `` ⇒ 解析器把后面整段当字符串）——
    #   所以这句用「」而不是反引号包参数名。
    $m = "⛔ 标签 {0} 已被占用：{1} —— 换一个未占用的标签（-Label）；复现旧季要显式 -Seed" -f `
         $label, (($taken | ForEach-Object Name) -join ', ')
    # 显式 `-Seed` = 明知要复现旧季（确定性重跑，覆盖原位产物）⇒ 只警告；`-DryRun` 同理（只读）。
    # ⚠ 判据用 `$explicitSeed`（开头记下的那个）：**不能**用 `$Seed` —— 它已经被派生值改写了（见开头那条）。
    if ($DryRun -or $explicitSeed) { Log $m } else { Log $m; exit 1 }
}

Log ("开始：{0} 代 × {1} 场（在线自对弈 + 对手池 + **接受闸门**）" -f $Generations, $Games)
Log ("现任（incumbent）= {0}" -f (Split-Path $incumbent -Parent | Split-Path -Leaf))
Log ("闸门：每代 2 套**新**牌山（按代派生）× {0} 场/套；S 盘剩余 {1:N0} GB" -f $GateBlock, (Free-GB))

# ⚠ **开跑前先验闸门方向**：`eval.paired_test` 的约定是 `diff = a − b`（正 = a 更好），
#   而"采纳"问的是**候选更好吗** ⇒ `gate.decide` 必须按 `(候选, 现任)` 的顺序算。
#   实测踩过：顺序写反 ⇒ **采纳判据整个反向**（把更差的候选采纳、把略好的拒掉），
#   而**空对照（Δ=0）两种写法都判不采纳**，抓不出方向问题 ⇒ 必须跑强弱对照。
$scLog = Join-Path $root 'release\gate-selfcheck.log'
# ⚠ `-DryRun`：**只打印**这一季真会跑的那条命令行（含 `$label` / `$seed` / `--il-weight` /
#   `--val-metric` 的实际取值）然后退出 —— 空跑一遍是为了能**当场**看见"缺省配方是什么"，
#   不用读脚本猜（这一季的配方本来就是被审计改过两次的地方）。
if (-not $DryRun) {
    & $py -m mahjong_ml.v4.gate --self-check --workers 12 --out (Join-Path $S 'gate') *> $scLog
    if ($LASTEXITCODE -ne 0) {
        Log '⛔ 闸门方向自证失败（弱现任 vs 强候选没能采纳）—— 不开始训练，先修闸门'
        Get-Content $scLog | Select-String -Pattern 'PASS|FAIL|正确顺序|写反顺序' | ForEach-Object { Log ("  " + $_.Line.Trim()) }
        exit 1
    }
    Log '闸门方向自证通过（弱现任 vs 强候选 ⇒ 采纳；反向 ⇒ 拒绝）'
} else {
    Log ("DRY-RUN：label={0} seed={1} IlWeight={2:g} 现任={3}" -f $label, $seed, $IlWeight, $incumbent)
}
for ($g = 1; $g -le $Generations; $g++) {
    $tag = "$label-g{0:D2}" -f $g
    $opp = @()
    if ($pool.Count -gt 0) { $opp = $pool | Select-Object -Last 2 }
    $largs = @('-m', 'mahjong_ml.v4', 'loop', '--label', $label, '--init', $incumbent,
               '--generations', '1', '--gen-offset', "$($g - 1)",
               '--games', "$Games", '--workers', '12', '--eval-workers', '12',
               '--objective', 'ppo', '--value-target', 'delta', '--advantage', 'hand',
               '--rank-weight', '0.2',
               # ---- 方案 (B)：**让一轮真的能动**（2026-10-03 审计后放宽）--------------------------------
               # 旧配方 `--max-steps 600 --kl-early-stop 0.03` 实测在第 **66** 步就被 KL 早停掐掉
               # ⇒ 每代只是现任的微小扰动，而验收闸门在 n=2000 的分辨率是 **±2.35**（实测：一个
               # **行为等价**的候选都测出 −0.13）⇒ 位移小于尺子 ⇒ 六季零累积。
               # 现在放宽到 KL 0.10（第一/三季用的就是 0.15 那一档）+ 2000 步上限：**位移放大到
               # 可测范围**。⚠ 单靠 (B) 会放大优势的噪声（critic 合法天花板只有 0.069）⇒ 必须与
               # **W3 的对现任 KL 锚**（`--ref-beta`，子任务落地后接上）配对使用：锚只管方向，
               # 不占 KL 预算的"位移额度"。
               '--max-steps', '2000', '--epochs', '1',
               '--kl-early-stop', '0.10', '--kl-min-steps', '60', '--critic-steps', '0',
               # ---- (C1/W3) 对现任的 KL 锚 + 验证集早停（子任务已落地，这里接线）--------------------
               # `--ref-beta 0.5`：锚 = 本轮的 `--init`（现任），β 是"别乱动"那个旋钮（0.3~1 起步）。
               # ⚠ 子任务实测：锚的梯度走**共享主干** ⇒ `ref_kl` 只占总损失 ~1%，但 value 头 train 均值
               #   会被抬（4.64→14.32）、裁剪前 |g| 4.32→10.04（val 侧几乎不变）⇒ 读台账要连别的头一起看。
               # ⚠ 放宽 KL 预算时**别同时抬 lr**：实测 `--lr 1e-2`（缺省的 10×）会把 v4 直接打塌
               #   （融合 ReLU 死区 ⇒ 逐候选 logits 全常数）。
               # `--val-every 25 --val-patience 3`：每 25 步在**固定子集**上读 train/val 的 policy CE，
               #   连续 3 次未改善就**正常收尾**（防过拟合；探针只读，实测与关掉时 epoch 1 逐位相同）。
               #   ⚠ 探针子集 ≥1024 行是刻意的（逐行 CE 重尾：256 行子集的噪声 ≈0.048，会盖掉 0.03~0.05 的趋势）
               #   ⇒ 探针的**绝对水位**不等于 epoch 行的全切分数，早停只看**趋势**。
               # ⚠⚠ 早停必须给"位移"留空间（2026-10-03 v4-league11 哨兵实测的结论）：`--val-every 25 × 3` 的
               #   **最早触发点就是 step 100**，而 val policy CE 是"贴行为数据"的尺子 —— 它已被证明与强弱
               #   **反向**（纯 BC 会把引擎一致率练低 1.35pp；A 路就是"更贴数据却 −12.65"）⇒ 拿它当主刹车，
               #   等于**每一轮都注定只走 100 步**。实测那一代 KL 只到 **0.0072**（连旧阈值 0.03 都没碰）
               #   ⇒ 刹车（KL/锚）**压根没参与**，问题是"没有前推力"：`ref_kl` 仅占总损失 0.2%。
               #   现在把触发点推到 **step 500**：真正的护栏交给 KL 预算（0.10），val CE 退回"粗监控/防过拟合"。
               '--ref-beta', "$RefBeta", '--val-every', '100', '--val-patience', '5',
               # ---- (C2/W4-A) 引擎逐候选牌效标签 —— ⛔ **实测有害，缺省 0；不要改回 1** ---------------
               # ⛔ 为什么**必须**是 0（第八季 W4-A 的负结果，实测数字，`NOTES.md` §6.5 第六十三轮）：
               #   `-IlWeight 1` 起的那一季（`v4-league10`）**第 1 代就被闸门判死** ——
               #   同代闸门 **Δ=−12.65 顺位点 CI[−14.92,−10.42] n=2000**（越界 5 倍），
               #   回路自评（换牌山）**−10.66 CI[−17.63,−3.76] p=0.028**，两条独立读数同号。
               #   机制：纯牌效标签**不含打点 / 押し引き**（`dora_count` 刻意不参与）⇒ 优化它必然
               #   拿这两样去换进张：和了率 24.1%→20.8%、放铳率 15.7%→17.0%、**平均打点 7037→6010**；
               #   而且 IL 项与 PPO 同在一个损失里、**不受 ratio clip 约束**（clip_frac 0.345 vs
               #   上一季同位置 0.056）⇒ 60 步（KL 早停 0.114 > 0.10）就把策略拽出信任域。
               # ⚠ 唯一会"变好"的读数是 `engine_top1`（0.6345 → 0.704，+6.95pp）—— 它与闸门
               #   **符号相反且都显著** ⇒ ⛔ **它不是强弱判据**，只是"IL 项有没有接上"的探针。
               #   **判强弱只能走闸门**（多套新牌山配对、CI 排除 0 且为正）。
               # 历史：`--il-weight 0`（缺省）与加这个功能之前**逐位相同**；负对照权重留在
               #   `tools\build\v4-league10-g01\net.bin`（+ `_rejected\` 副本）与
               #   `release\v4-league10-g01.log` / `release\gate-v4-league10-g01.log`。
               # `--val-metric` 跟着权重走（**不是**写死 `il`）：IL 臂必须读 `il` —— 否则行为口径的
               #   val CE 会因为"策略被推向引擎标签"而必然抬升，把正常训练误判成过拟合（实测在 step 300
               #   误停，而那一刻引擎 CE 还在降）；而 `--il-weight 0` 时训练目标**就是**行为标签，
               #   再拿引擎 CE 当早停尺子就是**用错了尺子**（量的是一个没有任何损失项在优化的量）
               #   ⇒ 那时按行为 `policy` 读。
               '--il-weight', "$IlWeight",
               '--val-metric', $(if ($IlWeight -gt 0) { 'il' } else { 'policy' }),
               # ---- 防过拟合（用户点名的要求）--------------------------------------------------------
               # `--val-every/--val-patience`（子任务落地后接上）会在**验证切分**上周期性量 policy CE，
               # 连续若干次不改善就**正常收尾**（打印 train/val 两条 CE，让人一眼看出"还在学"还是
               # "开始记数据"）；配合原有的 5% 验证切分 + grad-clip 0.5 + KL 锚，三件一起才算护栏。
               '--eval-games', '200', '--eval-vs', 'prev', '--seed', "$seed", '--no-java')
    foreach ($p in $opp) { $largs += @('--opponents', $p) }
    # ---- 风格桌（2026-10-08 用户指定）：训某风格时，桌上**每个风格各 1 席** ----------------------
    # 用法：-Style atk -StylePools "atk=a1,a2;def=d1,d2;win=w1,w2"
    # ⚠ 为什么不能沿用 -PoolSeed：那个口径只把**一个**非学生座位换成池里的一员（其余座位是学生×2 + teacher）
    #   ⇒ 桌上 2/4 席与学生同风格、每代只有 1 个风格对手 ⇒ 学不到"对另外两种风格怎么打"（用户指正）。
    # ⚠ 给了 -Style 就必须给 -StylePools，且本线必须在池里 —— 缺一个就**报错退出**，
    #   绝不静默降级成"少一个风格的桌子"（那正是这次要修的病）。
    if ($Style) {
        if (-not $StylePools) { Log '⛔ -Style 必须配 -StylePools（NAME=path1,path2;...）'; exit 1 }
        $poolNames = @()
        foreach ($grp in $StylePools.Split(';')) {
            if (-not $grp.Trim()) { continue }
            $poolNames += ($grp.Split('=')[0].Trim())
            $largs += @('--style-pool', $grp.Trim())
        }
        if ($poolNames -notcontains $Style) {
            Log ("⛔ -Style {0} 不在 -StylePools 的风格里（{1}）" -f $Style, ($poolNames -join ',')); exit 1
        }
        $largs += @('--style', $Style)
        Log ("风格桌：本线 {0}；每代桌上 = 学生 1 席 + 每个风格各 1 席（{1}）；座位靠 `--rotate-perm` 的 24 全排列平均" -f `
             $Style, ($poolNames -join '/'))
    }
    if ($DryRun) {
        # 空跑：把**真会跑的那条命令行**按 token 打印出来（`--il-weight` / `--val-metric` 一眼可见），
        # 顺带报这一季的 `$label` / `$seed` / 闸门牌山，然后退出（不训练、不碰 S 盘）。
        Log ("DRY-RUN 第 {0} 代命令：{1} {2}" -f $g, $py, ($largs -join ' '))
        Log ("DRY-RUN 闸门牌山（第 {0} 代）= {1}" -f $g, (& $gateSeedsFor $g))
        break
    }
    Log ("=== 第 {0} 代（{1}）init={2} 对手池={3} ===" -f $g, $tag,
         (Split-Path $incumbent -Parent | Split-Path -Leaf),
         $(if ($Style) { "风格桌（本线 $Style + 每个风格各 1 席）" }
          elseif ($opp.Count) { ($opp | ForEach-Object { Split-Path $_ -Parent | Split-Path -Leaf }) -join ',' }
          else { '（无）' }))
    $log = Join-Path $root "release\$tag.log"
    & $py @largs *> $log
    $rc = $LASTEXITCODE
    if ($rc -ne 0) { Log ("第 {0} 代失败（退出码 {1}）—— 保留现场并停止" -f $g, $rc); break }

    # ---- 座次平均化判据（2026-10-08）：每代采集完**立刻**数轨迹 --------------------------------
    # 为什么要它：桌子的**构成**由 `loop.py` 的 `policy()` 决定、**座次平均化**由 `--rotate` 决定，
    #   两者都是"设计上应该有"；而"忘了开 rotate"或"桌上有两个同一个策略"（老风格线的学生×2）
    #   只会表现为**某个策略的座位次数不齐** —— 等到几个月后看强弱是完全查不出来的。
    # 判据（`tools/seat-balance.py --check`）：每个策略在四个座位上次数**完全相等** + 每场学生席位数正确。
    #   ⚠ 期望学生席位随口径变：风格桌 1 席；旧口径（学生× teacher_seats）2 席。
    $sb = Join-Path $root 'tools\seat-balance.py'
    if (Test-Path $sb) {
        $wantStud = $(if ($Style) { 1 } else { 2 })
        & $py $sb (Join-Path $S "raw\$tag") --expect-students $wantStud --check *>> $log
        if ($LASTEXITCODE -ne 0) {
            Log ("第 {0} 代**座次平均化判据判红**（期望每场 {1} 个学生席）—— 保留现场并停止；详见 release\{2}.log" -f $g, $wantStud, $tag)
            break
        }
        Log ("第 {0} 代座次平均化判据 PASS（每场 {1} 个学生席；四席次数均等）" -f $g, $wantStud)
    }

    foreach ($d in @("raw\$tag", "compact\$tag", "raw\eval-$tag")) {
        $p = Join-Path $S $d
        if (Test-Path $p) {
            $sz = (Get-ChildItem $p -Recurse | Measure-Object Length -Sum).Sum / 1MB
            Remove-Item -Recurse -Force $p
            Log ("  轮换删除 {0}（{1:N0} MB）" -f $d, $sz)
        }
    }
    # ⚠ **闸门轨迹的轮换必须放在这一代的判决之后**（本次修，第八季补的那一版是**死代码**）：
    #   它当时被插在"raw/compact/eval 轮换"之后、**闸门还没跑**的位置 ⇒ `gate\<tag>` 那时**还不存在**
    #   ⇒ `Test-Path` 恒假 ⇒ 从未生效。证据：`v4-league9/10` 的日志里只有三条 raw/compact/eval
    #   轮换行、**没有 gate 行**，而 `S:\mahjong-training\gate` 已经堆到 26+ GB。
    #   现在它挪到 `$grc` 判决之后（同一 `$tag`、`gate\<tag>\s<seed>\` 这时才存在），并**保留
    #   `summary.json` / `verdict.json`**：`eval.load_run` 只读 `summary.json`
    #   （`per_game[].rank_points/placement/final_scores/policies/seed`），实测整季 5 代复算只用 7 秒、
    #   26 GB 的 jsonl 一个字节都没读 ⇒ **删轨迹、留摘要**：随时可以用 `python regate_gate.py`
    #   在当天的判据下重算历季判决（第八季就是靠它证明了"逐牌山打印符号"没有污染判决链）。
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

    # ⚠ 累积模式（-NoGate，2026-10-03 立）：单轮效应 ~+0.6 点在闸门 ±2.38 的分辨率下**测不出**
    #   ⇒ 逐代把关必然零累积（历季 28 个候选无一被采纳）。`-NoGate` **无条件采纳**（仍做 SHA256 去重
    #   与产物轮换），只在**最后判一次终点 vs 起点**（5 轮累积 ~3 点 ⇒ 越过分辨率）。
    #   ⚠ 判据仍是闸门（多套新牌山配对、CI 排除 0 且为正），**不是**任何贴标签的读数。
    if ($NoGate) {
        $incumbent = $candidate
        $pool.Add($candidate)
        Log ("  ACCUM 累积模式：无条件采纳 {0} ⇒ 新现任 = {0}；对手池 {1} 个" -f $tag, $pool.Count)
        continue
    }
    # ---- 接受闸门：多套牌山集合的合并配对判决（CI 排除 0 且为正才采纳）
    $gateSeeds = & $gateSeedsFor $g
    Log ("  闸门：现任 vs {0}（2 套**新**牌山 {1} × {2} 场）" -f $tag, $gateSeeds, $GateBlock)
    $gateLog = Join-Path $root "release\gate-$tag.log"
    & $py -m mahjong_ml.v4.gate --incumbent $incumbent --candidate $candidate `
        --seeds $gateSeeds --games $GateBlock --block $GateBlock --workers 12 `
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
    # ---- ⚠ **闸门轨迹轮换**：**必须**在判决之后（`gate\<tag>` 这时才存在；插在闸门之前 = 死代码）
    #   `gate\<tag>\s<seed>\` 每个牌山目录里 1000 个 `g*.jsonl` ≈2.7 GB ⇒ 每代 ~5.3 GB。
    #   ⛔ **只删 `*.jsonl`，绝不删 `summary.json` / `verdict.json`**：`eval.load_run` 只读
    #   `summary.json`（`regate_gate.py` 的复算判据就靠它，见本段上方的长注释）。
    $gd = Join-Path $S "gate\$tag"
    if (Test-Path $gd) {
        $gf = @(Get-ChildItem $gd -Recurse -File -Filter '*.jsonl')
        if ($gf.Count -gt 0) {
            $mb = ($gf | Measure-Object Length -Sum).Sum / 1MB
            foreach ($f in $gf) { Remove-Item $f.FullName -Force }
            Log ("  轮换删除 gate\{0} 的 {1} 个 jsonl（{2:N0} MB；**保留 summary.json / verdict.json** 以便复判）" -f $tag, $gf.Count, $mb)
        }
    } else {
        Log ("  ⚠ 闸门目录 gate\{0} 不存在 —— 轮换没跑到（闸门是不是没落盘？）" -f $tag)
    }
    Log ("S 盘剩余 {0:N0} GB" -f (Free-GB))
}
Log '季联赛结束'
