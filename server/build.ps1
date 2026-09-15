# 立直麻将服务端 —— 构建脚本（Windows；Linux/macOS 用 build.sh）
#
#   产物：build\mahjong-server.jar（纯字节码，Windows 上构建的也能直接跑在 Ubuntu 上）
#
#   pwsh -File server\build.ps1                 编译 + 打包
#   pwsh -File server\build.ps1 -SelfTest       顺带跑一次规则引擎自检
#   pwsh -File server\build.ps1 -JdkHome D:\jdk-21   指定 JDK（默认自动探测）
#   pwsh -File server\build.ps1 -Release 21     改用 --release 21 编译（默认 17，见下）
#
# 为什么不依赖本机环境：
#   本脚本**不写死任何 JDK 路径**，而是按「-JdkHome → JAVA_HOME → PATH →
#   各磁盘/各发行版的常见安装位置」依次找 javac，并校验版本 ≥ 17；
#   找不到时报错会直接给出各平台的安装命令。所以「JDK 装在哪」不影响构建。
#
#   默认用 `--release 17` 编译：这样**用 JDK 21 构建出来的 jar 也能跑在 JDK 17 上**，
#   产物不再取决于构建机的 JDK 版本（与本仓库 docs/DEPLOY.md 的「JDK 17+」一致）。
#   想用本机 JDK 的默认目标版本就传 -Release ''。
param(
    [string]$JdkHome = "",
    [string]$Release = "17",
    [string]$OutputDir = "",
    [switch]$SelfTest
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
if ([string]::IsNullOrEmpty($OutputDir)) { $OutputDir = Join-Path $Root "build" }
$srcDir  = Join-Path $Root "src/main/java"
$classes = Join-Path $OutputDir "classes"
$jarFile = Join-Path $OutputDir "mahjong-server.jar"

function Get-ExeIn([string]$Dir, [string]$Name) {
    if (-not $Dir) { return $null }
    foreach ($n in @("$Name.exe", $Name)) {
        $p = Join-Path $Dir $n
        if (Test-Path -LiteralPath $p -PathType Leaf) { return $p }
    }
    return $null
}

# javac -version 输出去掉 "javac " 前缀；JDK 9+ 写 stdout，8 及更早写 stderr
function Get-JavacVersion([string]$Javac) {
    try {
        $out = (& $Javac -version 2>&1 | Out-String).Trim()
        $m = [regex]::Match($out, '(\d+)(\.(\d+))?')
        if ($m.Success) { return $m.Value }
    } catch { }
    return ''
}

function Get-JdkCandidates {
    $list = New-Object System.Collections.Generic.List[string]
    if ($JdkHome) { $list.Add($JdkHome) }
    foreach ($v in @('JDK_HOME', 'JAVA_HOME')) {
        $h = [Environment]::GetEnvironmentVariable($v)
        if ($h) { $list.Add($h) }
    }
    $onPath = Get-Command javac -ErrorAction SilentlyContinue
    if ($onPath -and $onPath.Source) { $list.Add((Split-Path -Parent $onPath.Source)) }

    # 各磁盘上的常见安装位置（覆盖 Oracle / Adoptium / Microsoft / Amazon / Azul / BellSoft / SAP / GraalVM / scoop / Android Studio）
    $drives = @()
    try {
        $drives = @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
                    Where-Object { $_.Root -match '^[A-Za-z]:[\\/]$' } | ForEach-Object { $_.Root })
    } catch { }
    if ($drives.Count -eq 0) { $drives = @('C:\') }
    $vendorDirs = @('Java', 'Eclipse Adoptium', 'Eclipse Foundation', 'Microsoft', 'Amazon Corretto',
                    'Zulu', 'BellSoft', 'Semeru', 'IBM', 'RedHat', 'GraalVM', 'Android\Android Studio')
    foreach ($d in $drives) {
        foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, "$d")) {
            if (-not $base) { continue }
            foreach ($v in $vendorDirs) { $list.Add((Join-Path $base $v)) }
            $list.Add((Join-Path $base 'jdk'))
        }
        # 直接装在盘根下的 jdk-* / java-*
        foreach ($pat in @('jdk*', 'java-*', 'zulu*', 'graalvm*', 'Dependencies\jdk*', 'Dependencies\*\jdk*')) {
            foreach ($p in @(Get-ChildItem -Path (Join-Path $d $pat) -Directory -ErrorAction SilentlyContinue)) {
                $list.Add($p.FullName)
            }
        }
    }
    foreach ($base in @($env:LOCALAPPDATA, $env:USERPROFILE)) {
        if (-not $base) { continue }
        foreach ($rel in @('Programs\Eclipse Adoptium', 'Programs\Microsoft', 'Programs\Java', 'scoop\apps',
                           '.jdks', '.sdkman\candidates\java')) {
            $list.Add((Join-Path $base $rel))
        }
    }
    # Linux / macOS（在 Linux 上跑 pwsh 时同样可用）
    foreach ($p in @('/usr/lib/jvm', '/usr/java', '/opt/java', '/opt/jdk', '/Library/Java/JavaVirtualMachines',
                     (Join-Path $HOME '.sdkman/candidates/java'))) {
        $list.Add($p)
    }
    return $list
}

# 在某个目录里找 javac：可能传进来的是 JDK 根（<根>/bin/javac），也可能直接就是 bin 目录
function Find-JavacIn([string]$Dir) {
    foreach ($sub in @((Join-Path $Dir 'bin'), $Dir)) {
        $jc = Get-ExeIn $sub 'javac'
        if ($jc) { return $jc }
    }
    return $null
}

Write-Host "==> 查找 JDK（javac ≥ 17）" -ForegroundColor Cyan
$best = $null
$bestVersion = ''
# ⚠ 别用 $home 当循环变量：PowerShell 里它是只读自动变量（踩过两次）
foreach ($cand in (Get-JdkCandidates | Select-Object -Unique)) {
    if (-not $cand -or -not (Test-Path -LiteralPath $cand)) { continue }
    $javac = Find-JavacIn $cand
    if (-not $javac) {
        # 目录本身可能是"一堆版本"的容器（如 C:\Program Files\Java），往里看一层
        foreach ($sub in @(Get-ChildItem -LiteralPath $cand -Directory -ErrorAction SilentlyContinue)) {
            $javac = Find-JavacIn $sub.FullName
            if ($javac) { break }
        }
    }
    if (-not $javac) { continue }
    # ⚠ JDK 根目录必须从 javac 的**路径反推**（<根>/bin/javac），不能拿候选目录当根 ——
    #   候选可能是 bin 本身（PATH 上找到 javac 时就是），当根会去找 <bin>/bin/jar（踩过）
    $jdkHome = Split-Path -Parent (Split-Path -Parent $javac)
    $ver = Get-JavacVersion $javac
    $major = 0
    if ($ver) { $major = [int](($ver -split '\.')[0]) }
    if ($major -ge 17) {
        if (-not $best -or $major -gt ([int](($bestVersion -split '\.')[0]))) {
            $best = @{ Home = $jdkHome; Javac = $javac; Version = $ver }
            $bestVersion = $ver
        }
    }
}

if (-not $best) {
    Write-Host ""
    Write-Host "未找到可用的 JDK（需要 javac ≥ 17）。安装方式：" -ForegroundColor Yellow
    Write-Host "  Windows : winget install EclipseAdoptium.Temurin.21.JDK"
    Write-Host "            或 https://adoptium.net/temurin/releases/?version=21"
    Write-Host "  Ubuntu  : sudo apt update && sudo apt install -y openjdk-21-jdk-headless"
    Write-Host "  已有 JDK : 用 -JdkHome <JDK 目录> 指定，或设 JAVA_HOME"
    exit 1
}

$jdkHome  = $best.Home
$javacExe = $best.Javac
$jarExe   = Get-ExeIn (Join-Path $jdkHome 'bin') 'jar'
$javaExe  = Get-ExeIn (Join-Path $jdkHome 'bin') 'java'
if (-not $jarExe) { throw "在 $jdkHome\bin 里找不到 jar —— 这看起来是 JRE 而不是 JDK" }
Write-Host "      JDK   : $jdkHome  (javac $($best.Version))" -ForegroundColor DarkGray

# --release 需要 JDK 9+；传 -Release '' 表示用本机默认目标版本
$releaseFlag = @()
if ($Release) {
    try {
        $help = (& $javacExe --help 2>&1 | Out-String)
        if ($help -match '--release') { $releaseFlag = @('--release', $Release); Write-Host "      目标  : --release $Release" -ForegroundColor DarkGray }
        else { Write-Host "      目标  : 本机默认（该 JDK 不支持 --release）" -ForegroundColor DarkGray }
    } catch { }
}

Write-Host "==> 清理"
if (Test-Path -LiteralPath $classes) { Remove-Item -Recurse -Force -LiteralPath $classes }
New-Item -ItemType Directory -Force -Path $classes | Out-Null

Write-Host "==> 编译"
$files = @(Get-ChildItem -Recurse -Filter *.java -LiteralPath $srcDir | ForEach-Object { $_.FullName })
if ($files.Count -eq 0) { throw "在 $srcDir 下没找到任何 .java 文件" }
$listFile = Join-Path $OutputDir "sources.txt"
# 必须写成「无 BOM 的 UTF-8」，否则 javac 的 @argfile 会把 BOM 当成文件名的一部分
[System.IO.File]::WriteAllLines($listFile, [string[]]$files, (New-Object System.Text.UTF8Encoding($false)))

& $javacExe -encoding UTF-8 @releaseFlag -d $classes "@$listFile"
if ($LASTEXITCODE -ne 0) { throw "编译失败（javac 退出码 $LASTEXITCODE）" }

Write-Host "==> 打包"
& $jarExe --create --file $jarFile --main-class mahjong.Main -C $classes .
if ($LASTEXITCODE -ne 0) { throw "打包失败（jar 退出码 $LASTEXITCODE）" }

$sizeKB = [math]::Round((Get-Item -LiteralPath $jarFile).Length / 1KB, 1)
Write-Host "==> 完成: $jarFile  ($sizeKB KB)" -ForegroundColor Green
Write-Host ""
Write-Host "运行:"
Write-Host "  `"$javaExe`" -jar `"$jarFile`"              # 监听 0.0.0.0:10086"
Write-Host "  `"$javaExe`" -jar `"$jarFile`" --selftest # 规则引擎自检"

if ($SelfTest) {
    Write-Host ""
    Write-Host "==> 自检 --selftest" -ForegroundColor Cyan
    & $javaExe -jar $jarFile --selftest
    exit $LASTEXITCODE
}
