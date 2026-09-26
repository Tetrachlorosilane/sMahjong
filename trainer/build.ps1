# trainer/ —— 训练端 C++ 自对弈引擎（构建脚本）
#
#   pwsh -File trainer\build.ps1              # 编译（增量：源码/头文件变过才重编）
#   pwsh -File trainer\build.ps1 -Clean       # 清 build/ 里的**构建产物**再编（轨迹目录保留）
#   pwsh -File trainer\build.ps1 -Dbg         # -O1 -g（调试用；⚠ 别用 -Debug：那是 PS 的通用参数）
#   pwsh -File trainer\build.ps1 -Cxx <path>  # 指定编译器
#   pwsh -File trainer\build.ps1 -San         # -fsanitize=address,undefined（跑对拍用）
#   pwsh -File trainer\build.ps1 -NoSelfTest  # 编完**不跑** `--selftest`（工作流里省时间）
#                                             # 等价环境变量：$env:TRAINER_NO_SELFTEST = '1'
#
# 为什么不写死编译器路径：与 client/build.ps1 同一套口径 ——
# 「显式参数 → PATH → 常见安装位置」；找不到就**明确报错**，不静默降级成别的编译器
# （口径不同会让对拍结论失效，见 docs/TRAINER-CPP.md §2）。
#
# 产物：trainer\build\trainer.exe（build/ 已 gitignore）
[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$Dbg,
    [switch]$San,
    [switch]$NoSelfTest,
    [string]$Cxx,
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcDir = Join-Path $root 'src'
$buildDir = if ($OutDir) { $OutDir } else { Join-Path $root 'build' }

# 「编完自检」开关：`-NoSelfTest` 或环境变量 `TRAINER_NO_SELFTEST=1`（工作流/CI 走这条省时间）。
# ⚠ 环境变量只在**没给** `-NoSelfTest` 时才看：给了 `-NoSelfTest:$false` 也不能把它掰回来
# （"显式参数优先"在这里没有意义 —— 两个来源都只能表达"跳过"）。
$runSelfTest = -not $NoSelfTest
if ($runSelfTest -and $env:TRAINER_NO_SELFTEST -eq '1') {
    $runSelfTest = $false
}

function Find-Cxx {
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path $Explicit)) { throw "指定的编译器不存在：$Explicit" }
        return $Explicit
    }
    foreach ($name in @('clang++.exe', 'g++.exe')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    foreach ($guess in @(
            'D:\Program Files\LLVM\bin\clang++.exe',
            'C:\Program Files\LLVM\bin\clang++.exe',
            'C:\msys64\mingw64\bin\g++.exe')) {
        $hit = Get-Item $guess -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    foreach ($hit in (Get-Item (Join-Path $root '..\.qt\Tools\mingw*\bin\g++.exe') -ErrorAction SilentlyContinue)) {
        return $hit.FullName
    }
    throw '找不到 C++ 编译器（clang++ / g++）。装一个（如 LLVM/clang 或 MinGW-w64），或用 -Cxx <路径> 指定。'
}

$cxx = Find-Cxx -Explicit $Cxx
$version = (& $cxx --version 2>&1 | Select-Object -First 1)

if ($Clean -and (Test-Path $buildDir)) {
    # ⚠ `-Clean` **只删构建产物**，不删 `build/` 整个目录 —— 这个目录同时是**运行产物**的家：
    #   轨迹目录（`--out` 写的 `g*.jsonl` / `*.feat.bin` / `summary.json`）、对拍脚本的中间目录
    #   都放在这儿。整目录 `Remove-Item -Recurse` 会把它们一起删掉
    #   （2026-09 实测：一次 `-Clean` 删掉了别人正在对拍的 200 场 Java 轨迹，整条对拍白跑）。
    #   增量判据只看 `.stamp`，所以删掉它 + exe 已经足够触发重编。
    $stale = @(Get-ChildItem -Path $buildDir -File | Where-Object {
            $_.Name -in @('.stamp', 'trainer', 'trainer.exe') -or $_.Extension -in @('.obj', '.o', '.pdb')
        })
    foreach ($f in $stale) { Remove-Item -Force $f.FullName }
    Write-Host "==> -Clean：已清除 $($stale.Count) 个构建产物（轨迹/对拍目录保留）"
}
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

# 编译单元 = *.cpp；**指纹要把头文件也算进来** —— 只盯 .cpp 的话改 `wall.hpp` 不会重编（这个坑踩过一次）
$allSrc = Get-ChildItem -Path $srcDir -Recurse -File |
    Where-Object { $_.Extension -in @('.cpp', '.hpp', '.h') }
$sources = @($allSrc | Where-Object { $_.Extension -eq '.cpp' } | ForEach-Object { $_.FullName })
if (-not $sources) { throw "src\ 里没有 .cpp：$srcDir" }
$exe = Join-Path $buildDir 'trainer.exe'

# ⚠ `-ffp-contract=off`：**禁 FMA 收缩**。`net.cpp` 的前向必须与 Java（float、正序单累加器）
# 逐位同序 —— 收缩成 FMA 只改最后一位舍入，而 1e-4 的逐元素容差**挡不住** argmax 翻转，
# 我们的判据是"同种子 → 逐字节轨迹"（docs/TRAINER-CPP.md §2 / §6.15、trainer/src/net.hpp 顶部）。
$flags = @('-std=c++23', '-march=native', '-fno-exceptions', '-ffp-contract=off', '-Wall', '-Wextra', '-Wpedantic', '-D_CRT_SECURE_NO_WARNINGS')
if ($Dbg) { $flags += @('-O1', '-g') } else { $flags += @('-O3', '-DNDEBUG') }
if ($San) { $flags += @('-fsanitize=address,undefined', '-fno-omit-frame-pointer') }

Write-Host "==> 编译器   $cxx"
Write-Host "==> $version"
Write-Host "==> 源文件   $($sources.Count) 个 .cpp（另有 $($allSrc.Count - $sources.Count) 个头文件参与指纹）→ $exe"

$stamp = Join-Path $buildDir '.stamp'
$newest = ($allSrc | ForEach-Object { $_.LastWriteTimeUtc } | Sort-Object -Descending)[0]
$fingerprint = "$($newest.Ticks)|$cxx|$($flags -join ' ')|selftest=$runSelfTest"
$needBuild = $true
if ((Test-Path $exe) -and (Test-Path $stamp)) {
    $needBuild = ((Get-Content $stamp -Raw).Trim() -ne $fingerprint)
}
if (-not $needBuild) {
    Write-Host '==> 已是最新（源码未变）；要强制重编加 -Clean'
    exit 0
}

$cxxArgs = @()
$cxxArgs += $flags
$cxxArgs += $sources
$cxxArgs += @('-o', $exe)
& $cxx @cxxArgs
if ($LASTEXITCODE -ne 0) { throw "编译失败（退出码 $LASTEXITCODE）" }

$fingerprint | Set-Content -Path $stamp -NoNewline -Encoding ascii
Write-Host "==> 完成：$exe"
if ($runSelfTest) {
    & $exe --selftest
} else {
    Write-Host '==> 已跳过自检（-NoSelfTest）'
}
