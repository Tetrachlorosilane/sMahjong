# trainer/ —— 训练端 C++ 自对弈引擎（构建脚本）
#
#   pwsh -File trainer\build.ps1              # 编译（增量：源码/头文件变过才重编）
#   pwsh -File trainer\build.ps1 -Clean       # 先清 build/ 再编
#   pwsh -File trainer\build.ps1 -Dbg         # -O1 -g（调试用；⚠ 别用 -Debug：那是 PS 的通用参数）
#   pwsh -File trainer\build.ps1 -Cxx <path>  # 指定编译器
#   pwsh -File trainer\build.ps1 -San         # -fsanitize=address,undefined（跑对拍用）
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
    [string]$Cxx,
    [string]$OutDir
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$srcDir = Join-Path $root 'src'
$buildDir = if ($OutDir) { $OutDir } else { Join-Path $root 'build' }

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
    Remove-Item -Recurse -Force $buildDir
}
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null

# 编译单元 = *.cpp；**指纹要把头文件也算进来** —— 只盯 .cpp 的话改 `wall.hpp` 不会重编（这个坑踩过一次）
$allSrc = Get-ChildItem -Path $srcDir -Recurse -File |
    Where-Object { $_.Extension -in @('.cpp', '.hpp', '.h') }
$sources = @($allSrc | Where-Object { $_.Extension -eq '.cpp' } | ForEach-Object { $_.FullName })
if (-not $sources) { throw "src\ 里没有 .cpp：$srcDir" }
$exe = Join-Path $buildDir 'trainer.exe'

$flags = @('-std=c++23', '-march=native', '-fno-exceptions', '-Wall', '-Wextra', '-Wpedantic')
if ($Dbg) { $flags += @('-O1', '-g') } else { $flags += @('-O3', '-DNDEBUG') }
if ($San) { $flags += @('-fsanitize=address,undefined', '-fno-omit-frame-pointer') }

Write-Host "==> 编译器   $cxx"
Write-Host "==> $version"
Write-Host "==> 源文件   $($sources.Count) 个 .cpp（另有 $($allSrc.Count - $sources.Count) 个头文件参与指纹）→ $exe"

$stamp = Join-Path $buildDir '.stamp'
$newest = ($allSrc | ForEach-Object { $_.LastWriteTimeUtc } | Sort-Object -Descending)[0]
$fingerprint = "$($newest.Ticks)|$cxx|$($flags -join ' ')"
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
& $exe --selftest
