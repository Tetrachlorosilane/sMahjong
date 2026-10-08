#Requires -Version 7.0
<#
长链自动续跑包装器（`tools\run-chain.ps1`）—— 给"多专家训练长链"补上
**自动检测 + 自动续跑 + 心跳告警**这一层。

为什么要它（背景，别删）：长链已经三次中途死亡 —— `v4-league17` g25（torch 段错误）、
`v4-expert-atk` g02（断言误报）、`v4-expert-def2` g03（node 段错误）；其中两次是 `0xC0000005`
且**跨运行时**（torch 与 node），而**同一份数据重跑就过** ⇒ 判为"长时间满载下的机器级偶发不稳"
（本机有严重发热降频）。可 `tools\run-league.ps1` 死亡时是"**保留现场并停止**"，全靠人发现才续，
中间的空档可能是几小时到几天。续跑的材料早就齐了（`-Incumbent` 可从任意种子起步、
`-PoolSeed` 预置对手池），缺的只是"发现 + 接上" —— 就是本脚本。

本脚本做四件事：
  ① **磁盘事实优先**：已完成多少代**只看权重文件**（不看退出码、不看日志）。
  ② **绝不重训已完成的代**：只跑剩余代数，且每次调用 `run-league.ps1` 都用一个**全新的系列标签**
     （`<label>-r<K>`）—— 理由见下面第 2 条坑。
  ③ **心跳**：`release\<label>.heartbeat` 每代一行（ISO 时间 / 已完成代数 / wrapper PID / 子进程 PID …）。
  ④ **自检**：`-Check` 只读心跳与权重，判"心跳过期 or 进程不在"，并**打印**续跑命令行（不真跑）。

> 细节、红证（为什么必须换系列标签、阈值怎么定）与"**仍会误判的情形**"见 `NOTES.md` §11。

⚠ 四条读代码 + 实测得来的坑（是这个脚本的**存在理由**，改之前先看）：
  1. **`run-league.ps1` 中途死代仍然返回 0**：它那句 `if ($rc -ne 0) { …; break }` 只是 `break`
     出 for 循环，脚本末尾没有 `exit`（第 215 / 309 行）⇒ **退出码 0 不等于"跑完了"**。
     所以本脚本的循环条件**只能是磁盘代数**；退出码只用来写日志 / 决定要不要冷却。
     （反过来说：如果按退出码判成败，这条链会在"死了一代但 rc=0"时**假装成功退出**。）
  2. **`run-league.ps1` 有"标签占用闸门"**（第 103-113 行）：`tools\build\<label>-*` 非空时
     它 `exit 1`（除非显式 `-Seed`）。而它的代次目录名里的**代数只由自己循环的 `$g` 决定**
     （`tag = <label>-g$g`，`python\mahjong_ml\v4\loop.py:194` 那句 `generation + cfg.gen_offset`）
     ⇒ 用**同一个标签**续跑，它会**从 g01 重跑**、把已完成的代**原地覆盖重训**。
     ⇒ 本脚本每次调用 run-league 都换一个新的系列标签 ⇒ 既绝不重训、也撞不上它的闸门。
     ⚠ 代价（诚实记下）：新标签的数字会进 `$seed`（run-league 第 75 行的派生公式）⇒
     续跑那几代的采集种子 / 闸门牌山与"一次跑完"不同。这是不改 run-league 的必然结果。
  3. **`save_net` 是先写 `net.bin`、后写 `net.json`，而且不是原子写**
     （`python\mahjong_ml\v4\export.py:246-269`，只有一句 `p.write_bytes(blob)`）
     ⇒ 崩在导出中途会留下**半份 bin**。判据 = **json 在 且 `json.bytes == bin 的实际长度`**：
     json 是**后**写的，它在了、长度又对得上，才认这一代完成。
     ⛔ 别把"文件存在"当"这一代跑完了" —— 那会让链条从**半份权重**起步。
     （本脚本还会把这类残留单独报出来：`Suspects`，见 `Get-NetVerdict`。）
  4. **本脚本被 Ctrl-C 时不会去杀子进程**：那个 python 进程可能是机器上唯一还在跑的合法训练，
     杀了就白跑一代。⇒ 中断后**先跑 `-Check`**（它会告诉你是"wrapper 不在但子进程还在跑"，
     那种情况**别重启**，会双开抢同一份 S: 目录）。

用法：
  # 开链 / 续跑（同一句话：磁盘上有多少代就跑剩下的）
  pwsh -File tools\run-chain.ps1 -Label v4-league18 -Seed tools\build\v4-league17-g24\net.bin -Generations 30
  # 带对手池（透传给 run-league）与配方参数
  pwsh -File tools\run-chain.ps1 -Label v4-expert-def4 -Seed tools\build\v4-expert-def3-g08\net.bin `
       -Generations 10 -PoolSeed "tools\build\v4-expert-atk2-g09\net.bin,tools\build\v4-league14-g05\net.bin" `
       -RefBeta 0.5 -IlWeight 0 -MaxRetry 6 -CooldownSec 180
  # 断链自检（只读，不训练，不写任何文件）
  pwsh -File tools\run-chain.ps1 -Check -Label v4-league18
  # 测试：把 league 换成 stub（`-LeagueScript` 就是为这个可注入的）
  pwsh -File tools\run-chain.ps1 -Label chain-test1 -Seed <种子> -Generations 5 `
       -LeagueScript python\.tmp\chain-stub.ps1 -CooldownSec 1 -PollSec 1

退出码：0 = 链已跑满 `-Generations`（或本来就跑满了）；1 = 重试耗尽 / 参数错（**明确告警，不假装成功**）；
        2 = `-Check` 判为异常（死链 or "wrapper 不在但子进程还在跑"）。
#>

param(
    # 本次链的名字前缀（既是 checkpoint 系列名的前缀，也是 heartbeat / 日志的文件名）
    [string]$Label = '',
    # 第一代的现任 / 种子（net.bin 绝对或仓库内相对路径）。磁盘上已有完成代时**被忽略**（有日志提醒）
    [string]$Seed = '',
    # 可选：逗号分隔的 net.bin，**原样透传**给 `run-league.ps1 -PoolSeed`
    [string]$PoolSeed = '',
    # 这条链一共要多少代（`-Check` 时省略 ⇒ 从心跳里读）
    [int]$Generations = 0,
    # 重试（= 除首次之外的额外调用）次数上限
    [int]$MaxRetry = 6,
    # 每次重试前的冷却秒数（给发热/降频的机器恢复的时间）
    [int]$CooldownSec = 180,
    # league 脚本路径：**必须可注入**（换成 stub 才能做真测试）
    [string]$LeagueScript = 'tools\run-league.ps1',
    # 配方透传（缺省 0/0 —— 与 run-league 的 `--il-weight` 缺省一致；`-NoGate` 恒开）
    [double]$RefBeta = 0.0,
    [double]$IlWeight = 0.0,
    # 只读自检模式：不训练、不写文件，只判活 + 打印续跑命令
    [switch]$Check,
    # 轮询间隔（秒）：子进程在跑时按这个节奏看盘 + 写心跳。测试里给小值
    [int]$PollSec = 15,
    # 即使没有新进度，也至少每这么多秒写一行"我还活着"的心跳
    [int]$HeartbeatSec = 300,
    # 心跳里凑不出"每代实测时长"时用的兜底值（秒），只在 -Check 用
    [int]$GenSecHint = 1800,
    # 死链阈值系数：心跳比 系数 × 每代实测时长 还老 ⇒ 判死链（题目口径是 2×）
    [double]$StaleFactor = 2.0
)

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$ErrorActionPreference = 'Stop'

if (-not $PSScriptRoot) {
    Write-Output '⛔ 请用 `pwsh -File tools\run-chain.ps1` 调用（脚本要靠 $PSScriptRoot 定位仓库根）'
    exit 1
}
# 仓库根从脚本位置反推（比 run-league 里那句写死的 $root 更不容易错位）
$root       = Split-Path -Parent $PSScriptRoot
$buildDir   = Join-Path $root 'tools\build'
$releaseDir = Join-Path $root 'release'
$hbPath     = Join-Path $releaseDir ($Label + '.heartbeat')
$logPath    = Join-Path $releaseDir ($Label + '-chain.log')

# ════════════════════════════════════════════════════════════════════════════════════════
# 小工具
# ════════════════════════════════════════════════════════════════════════════════════════

# 统一出口：控制台一行 +（运行模式下）结构化一行进 `release\<label>-chain.log`。
# ⚠ `-Check` 是**只读**模式 ⇒ `$logPath` 会被清空，绝不写文件。
function Out-Line {
    param([string]$Msg)
    $now = (Get-Date)
    Write-Output ('[{0}] {1}' -f $now.ToString('HH:mm:ss'), $Msg)
    if ($logPath) {
        Add-Content -LiteralPath $logPath -Value ('[{0}] {1}' -f $now.ToString('o'), $Msg) -Encoding utf8
    }
}

# 心跳一行。格式（**前三个字段是题目要求的**，后面是给 -Check 与排障用的）：
#   <ISO 时间> | done=<已完成代数> | pid=<wrapper PID> | total=<总代数> | series=<本次系列> | child=<子进程 PID> | note=<事件>
# 为什么追加而不是覆盖：`-Check` 要靠**相邻进度行的时间差**算出"每代实测时长"，
# 阈值 = 2 × 实测，这样阈值是自校准的（大模型/小模型、冷/热机都不一样，写死一个数必然误判）。
function Write-Heartbeat {
    param([int]$Done, [string]$Series, [int]$Child, [string]$Note, [string]$RcText = '')
    $line = '{0} | done={1} | pid={2} | total={3} | series={4} | child={5} | note={6}' -f (Get-Date).ToString('o'), $Done, $PID, $Generations, $Series, $Child, $Note
    if ($RcText -ne '') { $line = $line + ' | exit=' + $RcText }
    Add-Content -LiteralPath $hbPath -Value $line -Encoding utf8
}

# 一枚权重"到底算不算一代完成" —— 判据见头部第 3 条坑。
# 返回 @{ Ok = $bool; Why = '<人话>' }；Why 只在失败时有用（会作为"崩溃残留"报到日志里）。
function Get-NetVerdict {
    param([string]$Net)
    if (-not (Test-Path -LiteralPath $Net -PathType Leaf)) { return @{ Ok = $false; Why = 'net.bin 不存在' } }
    # 用 -replace 而不是 [IO.Path]::ChangeExtension：少一个 .NET 静态调用，受限语言模式下也不会炸
    $json = $Net -replace '\.bin$', '.json'
    if (-not (Test-Path -LiteralPath $json -PathType Leaf)) {
        return @{ Ok = $false; Why = 'net.json 不在（崩在"先写 bin、后写 json"之间 ⇒ 半份）' }
    }
    $meta = $null
    try { $meta = (Get-Content -LiteralPath $json -Raw -Encoding utf8 | ConvertFrom-Json) }
    catch { return @{ Ok = $false; Why = ('net.json 解不动（写到一半？）：' + $_.Exception.Message) } }
    if ($null -eq $meta.bytes) { return @{ Ok = $false; Why = 'net.json 里没有 bytes 字段' } }
    $want = [int64]$meta.bytes
    if ($want -le 0) { return @{ Ok = $false; Why = 'net.json.bytes <= 0' } }
    $have = (Get-Item -LiteralPath $Net).Length
    if ($want -ne $have) { return @{ Ok = $false; Why = ('长度不符：json.bytes={0} 而实测 {1} B' -f $want, $have) } }
    return @{ Ok = $true; Why = '' }
}

# 目录名 → @{ Series / SeriesIdx / Gen }；形状只认两种：
#   `<label>-g<NN>`（基础系列，SeriesIdx=0） 与  `<label>-r<K>-g<NN>`（重试系列，SeriesIdx=K）
# ⚠ 不用正则：标签里的数字（`v4-league17`）与代号的数字混在一起时，`-replace '\D',''` 这种写法会串味
#   （run-league 第 70-74 行就为这个专门写过注释）。
function Split-SeriesDir {
    param([string]$Name, [string]$Base)
    if (-not $Name.StartsWith($Base + '-')) { return $null }
    $rest = $Name.Substring($Base.Length + 1)
    $idx = 0
    if ($rest.StartsWith('r')) {
        $dash = $rest.IndexOf('-')
        if ($dash -lt 1) { return $null }
        $k = $rest.Substring(1, $dash - 1) -as [int]
        if ($null -eq $k) { return $null }
        $idx = $k
        $rest = $rest.Substring($dash + 1)
    }
    if (-not $rest.StartsWith('g')) { return $null }
    $gen = $rest.Substring(1) -as [int]
    if ($null -eq $gen) { return $null }
    return @{ Series = $(if ($idx -eq 0) { $Base } else { $Base + '-r' + $idx }); SeriesIdx = $idx; Gen = $gen }
}

# ★ 本脚本的心脏：**只看磁盘**回答"已经跑到第几代了"。
#   Done      = 完整权重的**枚数**（一枚 = 一代；不完整的落在 Suspects 里，不算）
#   Incumbent = 链序最靠后的那枚（先按系列序号、再按代号）—— 系列是**按尝试顺序**建的 ⇒ 这就是最新现任
#   MaxRetryIdx = 已存在目录里最大的 r<K>（**按目录存在与否**算，不是按完整与否 ——
#                 run-league 的占用闸门看的是目录，且一个只写了一半的系列也不能再往里写）
function Get-ChainState {
    param([string]$Base)
    $items    = New-Object System.Collections.Generic.List[object]
    $suspects = New-Object System.Collections.Generic.List[object]
    $baseDirs = 0
    $maxIdx   = 0
    foreach ($d in @(Get-ChildItem -LiteralPath $buildDir -Directory -ErrorAction SilentlyContinue)) {
        $p = Split-SeriesDir -Name $d.Name -Base $Base
        if ($null -eq $p) { continue }
        if ($p['SeriesIdx'] -eq 0) { $baseDirs++ } elseif ($p['SeriesIdx'] -gt $maxIdx) { $maxIdx = $p['SeriesIdx'] }
        $net = Join-Path $d.FullName 'net.bin'
        # 连 net.bin 都没有 = 这一代没产出（连崩都算不上"半份"），不报残留
        if (-not (Test-Path -LiteralPath $net -PathType Leaf)) { continue }
        $v = Get-NetVerdict -Net $net
        if ($v.Ok) {
            $items.Add([pscustomobject]@{ Series = $p['Series']; SeriesIdx = $p['SeriesIdx']; Gen = $p['Gen']; Net = $net })
        } else {
            $suspects.Add([pscustomobject]@{ Series = $p['Series']; SeriesIdx = $p['SeriesIdx']; Gen = $p['Gen']; Net = $net; Why = $v.Why })
        }
    }
    $sorted = @($items | Sort-Object SeriesIdx, Gen)
    $inc = $null
    if ($sorted.Count -gt 0) { $inc = $sorted[$sorted.Count - 1].Net }
    # 代号连续性：run-league 是顺序 for 循环 ⇒ 一个系列内部正常是 1..N。
    # 不连续说明有人手工删过/搬过权重 —— Done 仍按实测枚数算，但要**说出来**（不然续跑起点会看着莫名其妙）
    $gaps = New-Object System.Collections.Generic.List[string]
    foreach ($g in ($sorted | Group-Object Series)) {
        $gens = @($g.Group | ForEach-Object { $_.Gen } | Sort-Object)
        if ($gens.Count -eq 0) { continue }
        if (($gens[$gens.Count - 1] - $gens[0] + 1) -ne $gens.Count) {
            $gaps.Add(('系列 {0} 的代号不连续：{1}' -f $g.Name, ($gens -join ',')))
        }
    }
    return @{
        Done        = $sorted.Count
        Items       = $sorted
        # ⚠⚠ 这里**必须** `.ToArray()`：把 `List[object]` 直接塞进哈希表字面量（或 `$h['k'] = @($list)`）
        #   会抛 `Argument types do not match`（`List[string]` 反而不踩 —— 类型参数是 `object` 才踩，
        #   实测 pwsh 7.6.5；第一次跑测试就在这一行炸了）。普通数组字面量 `@()` 不会踩。
        Suspects    = $suspects.ToArray()
        Gaps        = $gaps.ToArray()
        BaseDirs    = $baseDirs
        MaxRetryIdx = $maxIdx
        Incumbent   = $inc
    }
}

# 心跳文件 → 逐行解析出的点（给 -Check 用）。
# ⚠ 只解析，不修改；解析不动/没有时间戳的行直接跳过（宁可少算，不可把 `-Check` 弄崩）。
function Read-Heartbeat {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @() }
    $pts = New-Object System.Collections.Generic.List[object]
    foreach ($l in @(Get-Content -LiteralPath $Path -Encoding utf8)) {
        if (-not $l.Trim()) { continue }
        $f = @{}
        foreach ($seg in ($l -split '\|')) {
            $kv = $seg.Trim()
            if (-not $kv) { continue }
            $i = $kv.IndexOf('=')
            if ($i -gt 0) { $f[$kv.Substring(0, $i)] = $kv.Substring($i + 1) } else { $f['ts'] = $kv }
        }
        if (-not $f.ContainsKey('ts')) { continue }
        $ts = $null
        try { $ts = [datetime]$f['ts'] } catch { continue }
        $pts.Add([pscustomobject]@{
            Ts     = $ts
            TsRaw  = $f['ts']
            Done   = $(if ($f.ContainsKey('done')) { [int]$f['done'] } else { -1 })
            Pid    = $(if ($f.ContainsKey('pid')) { [int]$f['pid'] } else { 0 })
            Child  = $(if ($f.ContainsKey('child')) { [int]$f['child'] } else { 0 })
            Total  = $(if ($f.ContainsKey('total')) { [int]$f['total'] } else { 0 })
            Series = $(if ($f.ContainsKey('series')) { $f['series'] } else { '' })
            Note   = $(if ($f.ContainsKey('note')) { $f['note'] } else { '' })
            Rc     = $(if ($f.ContainsKey('exit')) { $f['exit'] } else { '' })
        })
    }
    # ⚠ 同 `Get-ChainState`：`@($pts)` 会抛 `Argument types do not match`（`$pts` 是 `List[object]`）
    return $pts.ToArray()
}

# PID 活着的判据（顺带把进程名带回来 —— 排障时"PID 被复用成别的进程"一眼可见）
function Test-PidAlive {
    param([int]$ProcId)
    if ($ProcId -le 0) { return $null }
    try {
        $p = Get-Process -Id $ProcId -ErrorAction Stop
        return @{ Alive = $true; Name = $p.ProcessName }
    } catch {
        return @{ Alive = $false; Name = '' }
    }
}

# 续跑命令行：`-Check` 只**打印**它，绝不自己跑。
# ⚠ 这里用**双**引号包路径（`"{1}"`），刻意避开"单引号里写 '' 折叠成一个引号"那个坑。
function Get-ResumeCommand {
    param([string]$IncumbentPath, [int]$Total)
    $seedArg = $(if ($IncumbentPath) { $IncumbentPath } else { '<第一代的种子 net.bin>' })
    $totalArg = $(if ($Total -gt 0) { "$Total" } else { '<总代数>' })
    $cmd = 'pwsh -File tools\run-chain.ps1 -Label {0} -Seed "{1}" -Generations {2} -MaxRetry {3} -CooldownSec {4}' -f $Label, $seedArg, $totalArg, $MaxRetry, $CooldownSec
    if ($PoolSeed) { $cmd = $cmd + (' -PoolSeed "{0}"' -f $PoolSeed) }
    if ($LeagueScript -ne 'tools\run-league.ps1') { $cmd = $cmd + (' -LeagueScript "{0}"' -f $LeagueScript) }
    return $cmd
}

# Start-Process 把 `-ArgumentList` 数组**用空格拼**成一条命令行 ⇒ 含空格的实参必须自带引号，
# 否则会被拆成两个参数（本仓库路径目前无空格，但标签与路径由使用者给，不能赌）。
function Quote-Arg {
    param([string]$A)
    if ($A -match '[\s"]') { return '"' + ($A -replace '"', '\"') + '"' }
    return $A
}

# ════════════════════════════════════════════════════════════════════════════════════════
# 参数校验（都在开跑之前做完 —— 与其跑一代才发现参数错，不如现在就说）
# ════════════════════════════════════════════════════════════════════════════════════════
if (-not $Label) { Write-Output '⛔ 必须给 -Label（本次链的名字前缀）'; exit 1 }
if ($MaxRetry -lt 0) { Write-Output '⛔ -MaxRetry 不能是负数'; exit 1 }
if ($CooldownSec -lt 0) { Write-Output '⛔ -CooldownSec 不能是负数'; exit 1 }
if ($PollSec -lt 1) { $PollSec = 1 }
if ($StaleFactor -le 0) { Write-Output '⛔ -StaleFactor 必须大于 0'; exit 1 }
if ($PSVersionTable.PSVersion.Major -lt 7) { Write-Output '⚠ 本脚本按 pwsh 7 写（-Encoding utf8 = 无 BOM 的 UTF-8）；5.1 下心跳会带 BOM' }

# league 脚本定位：先当"仓库内相对路径"试（缺省值就是这种），否则按原样
$leagueTry = Join-Path $root $LeagueScript
if (Test-Path -LiteralPath $leagueTry -PathType Leaf) { $leagueFull = (Get-Item -LiteralPath $leagueTry).FullName }
elseif (Test-Path -LiteralPath $LeagueScript -PathType Leaf) { $leagueFull = (Get-Item -LiteralPath $LeagueScript).FullName }
else { Write-Output ('⛔ 找不到 league 脚本：{0}' -f $LeagueScript); exit 1 }

# 子进程用哪个 pwsh：优先 $PSHOME（与当前 pwsh 同版本），取不到就退回当前进程自己的 exe
$pwshExe = Join-Path $PSHOME 'pwsh.exe'
if (-not (Test-Path -LiteralPath $pwshExe)) { $pwshExe = (Get-Process -Id $PID).Path }

# ════════════════════════════════════════════════════════════════════════════════════════
# -Check：只读自检（不训练、不写文件、不建目录）
# ════════════════════════════════════════════════════════════════════════════════════════
if ($Check) {
    $logPath = ''            # ← 这一句把 Out-Line 变成纯打印：-Check 绝不写盘
    $state = Get-ChainState -Base $Label
    $pts = Read-Heartbeat -Path $hbPath
    $total = $(if ($Generations -gt 0) { $Generations } elseif ($pts.Count -gt 0 -and $pts[$pts.Count - 1].Total -gt 0) { $pts[$pts.Count - 1].Total } else { 0 })

    if ($pts.Count -eq 0) {
        Out-Line ('⛔ -Check：没有心跳文件 {0}，无法判断"链条还在不在跑"' -f $hbPath)
        Out-Line ('   磁盘上已完成 {0} 代；若这条链本来就该在跑，说明它连第一次心跳都没来得及写。' -f $state.Done)
        Out-Line ('   续跑命令（-Check 不会自己跑）：')
        Out-Line ('     ' + (Get-ResumeCommand -IncumbentPath $state.Incumbent -Total $total))
        exit 2
    }

    $last = $pts[$pts.Count - 1]
    $age = ((Get-Date) - $last.Ts).TotalSeconds
    # "每代实测时长"：只看**进度真的往前走**的那些行（launched/alive/exit 行的 done 不变，不算）
    $marks = New-Object System.Collections.Generic.List[object]
    foreach ($p in $pts) {
        if ($p.Done -lt 0) { continue }
        if ($marks.Count -eq 0 -or $p.Done -gt $marks[$marks.Count - 1].Done) { $marks.Add($p) }
    }
    $ivs = New-Object System.Collections.Generic.List[double]
    for ($i = 1; $i -lt $marks.Count; $i++) {
        $d = ($marks[$i].Ts - $marks[$i - 1].Ts).TotalSeconds
        if ($d -gt 0) { $ivs.Add($d) }
    }
    $perGen = $GenSecHint
    $perGenSrc = ('兜底值 -GenSecHint')
    if ($ivs.Count -gt 0) {
        $sortedIv = @($ivs | Sort-Object)
        $perGen = $sortedIv[[int]($sortedIv.Count / 2)]      # 中位数（取上中位）：抗一次冷启动离群
        $perGenSrc = ('心跳实测 {0} 段进度间隔的中位数' -f $sortedIv.Count)
    }
    $thr = $StaleFactor * $perGen
    $wp = Test-PidAlive -ProcId $last.Pid
    $cp = Test-PidAlive -ProcId $last.Child
    $doneNow = $state.Done

    Out-Line ('[check] label={0} 心跳={1}（{2:N0}s 前，note={3}）' -f $Label, $last.TsRaw, $age, $last.Note)
    Out-Line ('[check] 磁盘进度 {0}/{1}；每代实测 ~{2:N0}s（来源：{3}）⇒ 死链阈值 {4:N0}s' -f $doneNow, $total, $perGen, $perGenSrc, $thr)
    Out-Line ('[check] wrapper pid={0}（{1}）；子进程 child={2}（{3}）' -f `
        $last.Pid, $(if ($wp -and $wp.Alive) { '在跑 ' + $wp.Name } else { '不在' }), `
        $last.Child, $(if ($null -eq $cp) { '未记录' } elseif ($cp.Alive) { '在跑 ' + $cp.Name } else { '不在' }))

    # ① 已经跑满 ⇒ 心跳"老"是正常的，别误报死链
    if ($total -gt 0 -and $doneNow -ge $total) {
        Out-Line ('✅ -Check：这条链**已经跑满**（{0}/{1}），心跳老不老都无所谓，不需要续跑' -f $doneNow, $total)
        exit 0
    }
    # ② wrapper 不在但**子进程还在跑** ⇒ 最危险的一格：重启会双开抢同一份 S: 目录/权重
    if (($null -eq $wp -or -not $wp.Alive) -and $cp -and $cp.Alive) {
        Out-Line ('⚠ -Check：wrapper 进程（pid={0}）不在了，但**子进程** child={1}（{2}）还在 ⇒ 训练可能仍在进行' -f $last.Pid, $last.Child, $cp.Name)
        Out-Line '   ⛔ 现在重启会**双开**（两个 league 抢同一份 compact/ckpt/gate 目录，产物互相覆盖）—— 先确认那个 PID 到底在干什么：'
        Out-Line ('     Get-CimInstance Win32_Process -Filter "ProcessId={0}" | Select-Object -ExpandProperty CommandLine' -f $last.Child)
        Out-Line '   要么等它跑完（然后重跑 -Check），要么**精确**结束它（Get-Process -Id <pid> | Stop-Process；绝不按进程名批量杀）。'
        exit 2
    }
    # ③ 真的死链：心跳过期 or 进程不在
    $reasons = New-Object System.Collections.Generic.List[string]
    if ($age -gt $thr) { $reasons.Add(('心跳 {0:N0}s 前 > {1} × 每代实测 {2:N0}s（= {3:N0}s）' -f $age, $StaleFactor, $perGen, $thr)) }
    if ($null -eq $wp -or -not $wp.Alive) { $reasons.Add(('wrapper 进程 pid={0} 不在' -f $last.Pid)) }
    if ($reasons.Count -eq 0) {
        Out-Line ('✅ -Check：心跳新鲜、进程在跑 —— 这条链是活的，不用管它')
        exit 0
    }
    Out-Line '⛔ -Check 判为死链：'
    foreach ($r in $reasons) { Out-Line ('   · ' + $r) }
    Out-Line ('   · 磁盘上已完成 {0}/{1} 代；最后一代（= 该从哪枚续跑）= {2}' -f $doneNow, $total, $(if ($state.Incumbent) { $state.Incumbent } else { '（无：一枚完整权重都没有）' }))
    if ($state.Suspects.Count -gt 0) {
        foreach ($s in $state.Suspects) { Out-Line ('   · 崩溃残留（不完整的权重，续跑时会**重跑**这一代）：{0} —— {1}' -f $s.Net, $s.Why) }
    }
    Out-Line '   续跑命令（-Check 不会自己跑）：'
    Out-Line ('     ' + (Get-ResumeCommand -IncumbentPath $state.Incumbent -Total $total))
    exit 2
}

# ════════════════════════════════════════════════════════════════════════════════════════
# 运行模式
# ════════════════════════════════════════════════════════════════════════════════════════
if ($Generations -lt 1) { Write-Output ('⛔ 运行模式必须给 -Generations（>0）；只想看状态请加 -Check'); exit 1 }
# ⚠ 建目录这件事放在**运行模式**里（`-Check` 是只读模式，一个字节都不该写）
if (-not (Test-Path -LiteralPath $releaseDir)) { New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null }
if (-not (Test-Path -LiteralPath $buildDir))   { New-Item -ItemType Directory -Force -Path $buildDir   | Out-Null }

# 起始状态：磁盘说了算
$state0 = Get-ChainState -Base $Label
$done0 = $state0.Done
$seedFull = ''
if ($Seed) {
    $seedTry = Join-Path $root $Seed
    if (Test-Path -LiteralPath $seedTry -PathType Leaf) { $seedFull = (Get-Item -LiteralPath $seedTry).FullName }
    elseif (Test-Path -LiteralPath $Seed -PathType Leaf) { $seedFull = (Get-Item -LiteralPath $Seed).FullName }
}

Out-Line ('=== run-chain 启动：label={0} 目标 {1} 代；磁盘已完成 {2} 代；MaxRetry={3} Cooldown={4}s ===' -f $Label, $Generations, $done0, $MaxRetry, $CooldownSec)
Out-Line ('    league={0}；-NoGate 恒开；RefBeta={1:g} IlWeight={2:g}' -f $leagueFull, $RefBeta, $IlWeight)
if ($PoolSeed) { Out-Line ('    对手池（透传 -PoolSeed）= {0}' -f $PoolSeed) } else { Out-Line '    对手池 = （无，未给 -PoolSeed）' }
foreach ($s in $state0.Suspects) { Out-Line ('   ⚠ 崩溃残留（不完整，不算已完成、会被重跑）：{0} —— {1}' -f $s.Net, $s.Why) }
foreach ($g in $state0.Gaps) { Out-Line ('   ⚠ {0}' -f $g) }

if ($done0 -ge $Generations) {
    # ★ 已完成测试走的就是这条路：**一次都不调用 league**
    Out-Line ('✅ 目标已经是"磁盘事实"：已完成 {0} 代 ≥ 目标 {1} 代 ⇒ **不训练**、直接成功退出（没有调用 league）' -f $done0, $Generations)
    Write-Heartbeat -Done $done0 -Series '(none)' -Child 0 -Note 'already-complete'
    Out-Line ('   最后一代权重 = {0}' -f $state0.Incumbent)
    exit 0
}

$incumbent = $state0.Incumbent
if ($done0 -eq 0) {
    if (-not $seedFull) { Out-Line '⛔ 磁盘上没有任何完成的代，必须给 -Seed（第一代的现任/种子 net.bin）'; exit 1 }
    $v = Get-NetVerdict -Net $seedFull
    if (-not $v.Ok) { Out-Line ('⛔ -Seed 不是一枚完整权重：{0} —— {1}' -f $seedFull, $v.Why); exit 1 }
    $incumbent = $seedFull
    Out-Line ('   第 1 代的现任（-Incumbent）= {0}' -f $incumbent)
} else {
    if ($seedFull) { Out-Line ('   注意：磁盘上已有 {0} 代 ⇒ -Seed 被忽略，续跑起点 = {1}' -f $done0, $incumbent) }
    Out-Line ('   续跑起点（-Incumbent）= {0}' -f $incumbent)
}

$attempt = 0
$retries = 0
$lastRc = 'n/a'
$exitCode = 1
$childLogDir = $releaseDir

while ($true) {
    # ── 每轮都**重新**看盘：上一轮可能已经推进了，也可能死在半路
    $state = Get-ChainState -Base $Label
    $done = $state.Done
    if ($done -ge $Generations) { $exitCode = 0; break }
    if ($done -gt 0) { $incumbent = $state.Incumbent }
    $remaining = $Generations - $done
    if (-not $incumbent) { Out-Line '⛔ 没有可用的现任权重（既没种子也没完整代）'; exit 1 }

    # ── 系列标签（见头部坑 #2）：**基础系列只在它还是空的时用**；否则取下一个没人用过的 `-r<K>`
    if ($state.BaseDirs -eq 0 -and $state.MaxRetryIdx -eq 0) { $series = $Label }
    else { $series = '{0}-r{1}' -f $Label, ($state.MaxRetryIdx + 1) }
    $clash = @(Get-ChildItem -LiteralPath $buildDir -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like ($series + '-*') })
    if ($clash.Count -gt 0) {
        # 正常情况下这条永远不会触发（上面那条 if 已经保证了）；留着是因为"往已存在的系列里写"
        # = run-league **从 g01 重跑并覆盖已完成的代**，那是本脚本唯一绝对不能做的事。
        Out-Line ('⛔ 系列 {0} 已被占用（{1}）—— 绝不覆盖，停手等人工看' -f $series, (($clash | ForEach-Object Name) -join ', '))
        exit 1
    }

    $attempt++
    $tagLabel = $(if ($retries -eq 0) { '初始' } else { ('重试 {0}/{1}' -f $retries, $MaxRetry) })
    $childOut = Join-Path $childLogDir ('{0}-chain.{1}.out.log' -f $Label, $series)
    $childErr = Join-Path $childLogDir ('{0}-chain.{1}.err.log' -f $Label, $series)

    $argList = New-Object System.Collections.Generic.List[string]
    foreach ($a in @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Quote-Arg $leagueFull),
                     '-Label', (Quote-Arg $series), '-Generations', "$remaining",
                     '-Incumbent', (Quote-Arg $incumbent),
                     '-NoGate', '-RefBeta', "$RefBeta", '-IlWeight', "$IlWeight")) { $argList.Add($a) }
    if ($PoolSeed) { $argList.Add('-PoolSeed'); $argList.Add((Quote-Arg $PoolSeed)) }

    Out-Line ('--- 第 {0} 次调用 league（{1}）：系列={2} 从 {3} 起 跑 {4} 代（已完成 {5}/{6}）---' -f $attempt, $tagLabel, $series, (Split-Path -Parent $incumbent | Split-Path -Leaf), $remaining, $done, $Generations)
    Out-Line ('    子进程：{0} {1}' -f (Split-Path -Leaf $pwshExe), ($argList -join ' '))
    Out-Line ('    子进程输出：{0} / {1}' -f (Split-Path -Leaf $childOut), (Split-Path -Leaf $childErr))

    $t0 = Get-Date
    $proc = Start-Process -FilePath $pwshExe -ArgumentList $argList.ToArray() -WorkingDirectory $root -NoNewWindow -PassThru `
                          -RedirectStandardOutput $childOut -RedirectStandardError $childErr
    Write-Heartbeat -Done $done -Series $series -Child $proc.Id -Note 'launched'

    # ── 轮询：这就是"每代一行心跳"的来源。
    #    为什么不能只在调用前后各写一行：一代真实耗时 ~30 分钟，那样"心跳"在整代里都是老的，
    #    `-Check` 的"2 × 每代实测"就永远判不出来（而且实测值本身也没了来源）。
    $lastHbAt = Get-Date
    $seen = $done
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds $PollSec
        $st = Get-ChainState -Base $Label
        if ($st.Done -gt $seen) {
            $seen = $st.Done
            Write-Heartbeat -Done $seen -Series $series -Child $proc.Id -Note 'progress'
            Out-Line ('    进度 {0}/{1}（新完成 {2} 代）' -f $seen, $Generations, ($seen - $done))
            $lastHbAt = Get-Date
        } elseif (((Get-Date) - $lastHbAt).TotalSeconds -ge $HeartbeatSec) {
            # 没有新进度也要留"我还活着"的痕迹 —— 否则长代期间心跳会假性过期
            Write-Heartbeat -Done $seen -Series $series -Child $proc.Id -Note 'alive'
            $lastHbAt = Get-Date
        }
    }
    $proc.WaitForExit()
    $rc = $proc.ExitCode
    $lastRc = "$rc"
    $elapsed = ((Get-Date) - $t0).TotalSeconds
    # 退出码可能是 0xC0000005 这类异常码的**有符号**形态（-1073741819）—— 原样记下来，
    # 这正是判断"机器级偶发不稳"要的现场证据
    $rcHex = ('0x{0:X8}' -f $rc)
    $state2 = Get-ChainState -Base $Label
    $doneAfter = $state2.Done
    Write-Heartbeat -Done $doneAfter -Series $series -Child $proc.Id -Note 'exit' -RcText $lastRc

    Out-Line ('    子进程退出：rc={0}（{1}）耗时 {2:N1}s；磁盘进度 {3}/{4} 代' -f $lastRc, $rcHex, $elapsed, $doneAfter, $Generations)
    foreach ($s in $state2.Suspects) { Out-Line ('    ⚠ 崩溃残留：{0} —— {1}' -f $s.Net, $s.Why) }

    if ($doneAfter -ge $Generations) { $exitCode = 0; break }   # 磁盘事实说了算（不是退出码）
    if ($rc -eq 0) {
        # ⚠ 这一句是坑 #1 的正面用法：run-league 中途死代**也返回 0** ⇒ 不能当成功
        Out-Line ('    ⚠ 退出码 0 但进度没满（{0} < {1}）—— run-league 中途死代也返回 0（见脚本头部坑 #1），按"要继续续跑"处理' -f $doneAfter, $Generations)
    }
    if ($attempt -gt $MaxRetry) {
        Out-Line ('⛔ 长链未完成：{0}/{1} 代。已用 {2} 次初始调用 + {3} 次重试（上限 MaxRetry={4}），**不再自动重试**' -f $doneAfter, $Generations, 1, $retries, $MaxRetry)
        Out-Line ('   最后一次子进程退出码 rc={0}（{1}）—— 0xC0000005 这类异常码请按"机器级偶发"或"配方/环境问题"两条路查' -f $lastRc, $rcHex)
        Out-Line ('   续跑命令：{0}' -f (Get-ResumeCommand -IncumbentPath $state2.Incumbent -Total $Generations))
        exit 1
    }
    $retries++
    Out-Line ('    → 冷却 {0}s 后重试（第 {1}/{2} 次重试；给发热降频的机器恢复的时间）…' -f $CooldownSec, $retries, $MaxRetry)
    if ($CooldownSec -gt 0) { Start-Sleep -Seconds $CooldownSec }
}

if ($exitCode -eq 0) {
    $stateF = Get-ChainState -Base $Label
    Out-Line ('✅ 长链完成：{0} {1}/{2} 代（本进程共调用 league {3} 次：初始 + {4} 次重试）' -f $Label, $stateF.Done, $Generations, $attempt, $retries)
    Out-Line ('   最后一代权重 = {0}' -f $stateF.Incumbent)
    Write-Heartbeat -Done $stateF.Done -Series '(done)' -Child 0 -Note 'completed'
}
exit $exitCode
