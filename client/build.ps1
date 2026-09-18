# 立直麻将 Qt6 客户端 · 一键构建脚本（不依赖本机环境）
#
#   pwsh -File client\build.ps1                配置 + 构建 + 把 Qt 运行时拷到 exe 旁边
#   pwsh -File client\build.ps1 -Deploy        额外产出 client\dist 发布目录（只含运行必需文件）
#   pwsh -File client\build.ps1 -SelfTest      构建完成后跑一次 --selftest
#   pwsh -File client\build.ps1 -Clean         先清空 build 目录
#   pwsh -File client\build.ps1 -Ninja         改用 Ninja 生成器（默认用 MinGW Makefiles）
#   pwsh -File client\build.ps1 -Plan          只打印「找到/将下载什么」，不动手（排障用）
#
# 为什么这个脚本不写死任何本机路径：
#   它**不假设**这台机器装过 Qt、也不假设 Qt / CMake / MinGW 装在哪里。
#   解析顺序是「显式指定 → 环境变量 → PATH → 常见安装位置 → 本地缓存 → 自动下载」，
#   全都由 tools\qt-provision.ps1 实现（下载源是 download.qt.io 在线仓库，与 Qt 官方安装器同源，
#   只取本项目用到的 qtbase + qtsvg，约 22 MB）。
#   于是「一台只有 PowerShell、别的什么都没有的 Windows」也能一条命令构建出来。
#
# 常用覆盖参数：
#   -QtDir D:\Qt\6.11.2\mingw_64     用指定的 Qt（跳过一切探测）
#   -QtVersion 6.8.3                 换一个版本（'latest' = 仓库里最新的）
#   -Provision never                 禁止自动下载；找不到就直接报错
#   -Provision always                无视系统里装的 Qt，必须用脚本自己缓存/下载的那份
#   -QtCacheDir <目录>               自动获取的 Qt 放哪（默认 <仓库根>\.qt）
#   -RefreshQt                       重新解包/下载缓存的 Qt（缓存坏了或换了模块时用）
#   -Offline                         完全不出网（只用已有缓存）
#   -CMakeExe / -MingwBin / -NinjaDir  显式指定构建工具
#
# 关于 Qt 链接方式：
#   官方提供的 Qt 是 **shared（动态）** 构建，qconfig.pri 里 static 是 disabled feature，
#   libQt6*.a 只是 DLL 导入库，因此**无法静态链接**。本脚本的做法是：构建后把 Qt6*.dll、
#   MinGW 运行时 DLL 和 platforms/styles/tls 插件拷到 exe 同级目录 —— 效果等价于「绿色版」，
#   拷到任何 Windows 机器都能直接双击运行，不需要装 Qt、不需要配 PATH。
#   许可义务见 licenses\NOTICE.txt 与 docs\THIRD-PARTY.md（LGPLv3，可替换共享库）。
#
#   moc：默认走标准 AUTOMOC；若受限环境禁止构建期派生子进程（AutoMoc subprocess
#   error）或缓存陈旧（mocs_compilation.cpp 规则缺失），脚本会清空 build 目录并
#   改用「配置期预生成 moc」重建。
param(
    [string]$BuildDir = "",
    [string]$QtDir = "",
    [string]$QtVersion = "",
    [string]$QtCacheDir = "",
    [string]$QtRepo = "",
    [ValidateSet('auto', 'never', 'always')][string]$Provision = 'auto',
    [string]$CMakeExe = "",
    [string]$MingwBin = "",
    [string]$NinjaDir = "",
    [string]$Transport = "",
    [string]$BuildType = "Release",
    [switch]$Clean,
    [switch]$Deploy,
    [switch]$SelfTest,
    [switch]$Ninja,
    [switch]$NoRuntime,
    [switch]$WithQtSource,
    [switch]$NoQtSource,
    [switch]$IncludeQtSourceInDist,
    [switch]$RefreshQt,
    [switch]$Offline,
    [switch]$DeepScan,
    [switch]$Plan
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$RepoRoot = Split-Path -Parent $Root
if ([string]::IsNullOrEmpty($BuildDir))    { $BuildDir = Join-Path $Root "build" }
if ([string]::IsNullOrEmpty($QtCacheDir))  { $QtCacheDir = Join-Path $RepoRoot ".qt" }

# 探测 + 自动获取 Qt / 工具链的实现都在这里（可被别的脚本复用）
. (Join-Path $RepoRoot "tools/qt-provision.ps1")

$stopwatch = [Diagnostics.Stopwatch]::StartNew()

# ---------------------------------------------------------------------------
# 1) 解析工具链（Qt / CMake / MinGW / 可选 Ninja）
# ---------------------------------------------------------------------------
Write-QtHead "[1/5] 解析 Qt 与构建工具"
$qt = Resolve-QtPrefix -Explicit $QtDir -Version $QtVersion -CacheDir $QtCacheDir `
                       -Provision $Provision -RepoBase $QtRepo -Transport $Transport `
                       -Offline:$Offline -DeepScan:$DeepScan -Refresh:$RefreshQt -Plan:$Plan
$cmake = Resolve-CMakeExe -Explicit $CMakeExe -CacheDir $QtCacheDir -RepoBase $QtRepo `
                          -Transport $Transport -Offline:$Offline -Plan:$Plan
$mingw = Resolve-MingwBin -Explicit $MingwBin -CacheDir $QtCacheDir -RepoBase $QtRepo `
                          -Transport $Transport -Offline:$Offline -Plan:$Plan
$ninjaExe = $null
if ($Ninja) {
    $ninjaExe = Resolve-NinjaExe -Explicit $NinjaDir -CacheDir $QtCacheDir -RepoBase $QtRepo `
                                 -Transport $Transport -Offline:$Offline -Plan:$Plan
}

Write-QtInfo ("Qt    : {0}  ({1})" -f $qt.Prefix, $qt.Source)
Write-QtInfo ("       版本 {0}" -f $(if ($qt.Version) { $qt.Version } else { '未知' }))
Write-QtInfo ("CMake : {0}  ({1})" -f $cmake.Exe, $cmake.Source)
Write-QtInfo ("MinGW : {0}  (g++ {1}, {2})" -f $mingw.Bin, $mingw.Version, $mingw.Source)
if ($Ninja) {
    if ($ninjaExe) { Write-QtInfo ("Ninja : {0}" -f $ninjaExe) }
    else { Write-QtWarn "没找到 Ninja（-Ninja 已忽略，回退 MinGW Makefiles）"; $Ninja = $false }
}

# Qt 官方 MinGW 与本地 g++ 主版本不一致时给个提醒（ABI/libstdc++ 可能对不上）
$qtMingwMajor = '13'
if ($mingw.Version) {
    $gccMajor = ($mingw.Version -split '\.')[0]
    if ($gccMajor -ne $qtMingwMajor) {
        Write-QtWarn ("注意：Qt 官方包是用 MinGW $qtMingwMajor 构建的，本机 g++ 是 $gccMajor。" +
                      "跨大版本链接通常可以，但若出现异常崩溃/缺少 libstdc++-6.dll，优先用 -MingwBin 指定 MinGW $qtMingwMajor")
    }
}

if ($Plan) {
    Write-QtHead "[Plan] 只做解析，不做任何构建/下载："
    Write-QtInfo ("构建目录: {0}" -f $BuildDir)
    Write-QtInfo ("生成器  : {0}" -f $(if ($Ninja) { 'Ninja' } else { 'MinGW Makefiles' }))
    Write-QtInfo ("Qt 缓存 : {0}" -f $QtCacheDir)
    if (-not (Test-QtPrefix $qt.Prefix)) { Write-QtWarn "Qt 前缀尚不存在，实际构建时会自动下载到上面这个位置" }
    exit 0
}

# 2) 工具链 PATH（系统 PATH 里通常没有 g++，必须自己拼）
$pathParts = @((Join-Path $qt.Prefix "bin"), $mingw.Bin)
if ($ninjaExe) { $pathParts += (Split-Path -Parent $ninjaExe) }
$env:PATH = (($pathParts | Where-Object { $_ }) -join ';') + ';' + $env:PATH
Write-QtOk "PATH 就绪"

$generator = if ($Ninja) { "Ninja" } else { "MinGW Makefiles" }
$makeProgram = if ($Ninja) { $ninjaExe } else { (Join-Path $mingw.Bin "mingw32-make.exe") }
if (-not $Ninja -and -not (Test-Path $makeProgram)) {
    throw "缺少 mingw32-make.exe（$makeProgram）；可加 -Ninja 改用 ninja，或用 -MingwBin 指定正确的 MinGW bin 目录"
}

# ---------------------------------------------------------------------------
# 3) 清理
# ---------------------------------------------------------------------------
if ($Clean -and (Test-Path $BuildDir)) {
    Write-QtHead "[2/5] 清理 $BuildDir"
    Remove-Item -Recurse -Force $BuildDir
} else {
    Write-QtHead "[2/5] 保留已有构建目录（-Clean 可清空）"
}

function Invoke-Configure([bool]$useAutomoc) {
    $flag = if ($useAutomoc) { "ON" } else { "OFF" }
    Write-QtHead "[3/5] cmake configure（生成器 $generator, AUTOMOC=$flag）"
    # Windows PowerShell 5.1 下原生命令的 stderr 会触发 NativeCommandError，这里临时放宽
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $cmake.Exe -S $Root -B $BuildDir -G $generator `
        "-DCMAKE_BUILD_TYPE=$BuildType" `
        "-DCMAKE_MAKE_PROGRAM=$makeProgram" `
        "-DCMAKE_PREFIX_PATH=$($qt.Prefix)" `
        "-DMJ_USE_AUTOMOC=$flag" 2>&1 | Out-Host
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    if ($code -ne 0) { throw "cmake 配置失败（exit $code）" }
}

function Invoke-Build([string]$logFile) {
    Write-QtHead "[3/5] cmake build"
    # exe 正在运行会锁住文件导致链接失败，先给出明确提示并自动结束它
    $running = Get-Process mahjong-client -ErrorAction SilentlyContinue
    if ($running) {
        Write-QtWarn "检测到 mahjong-client.exe 正在运行，先结束它（否则链接会失败）…"
        $running | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 800
    }
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $cmake.Exe --build $BuildDir 2>&1 | Tee-Object -FilePath $logFile | Out-Host
    $code = $LASTEXITCODE
    $ErrorActionPreference = $old
    return ($code -eq 0)
}

Invoke-Configure $true
$log = Join-Path $BuildDir "build.log"
$ok = Invoke-Build $log
if (-not $ok) {
    $text = Get-Content $log -Raw -ErrorAction SilentlyContinue
    # * 受限沙箱禁止构建期派生子进程 → AUTOMOC 报 "AutoMoc subprocess error"
    # * 上一次以预生成模式构建过、这次切回 AUTOMOC → mocs_compilation.cpp 规则陈旧
    # 两种情况都：清空构建目录 → 用配置期预生成 moc 重建
    if ($text -match "AutoMoc subprocess error" -or $text -match "mocs_compilation\.cpp") {
        Write-QtWarn "AUTOMOC 不可用（沙箱或缓存陈旧），清空构建目录后改用配置期预生成 moc…"
        Remove-Item -Recurse -Force $BuildDir -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
        Invoke-Configure $false
        $ok = Invoke-Build (Join-Path $BuildDir "build-pregen.log")
    }
}
if (-not $ok) { throw "构建失败（详见 $log）" }

$exe = Join-Path $BuildDir "mahjong-client.exe"
if (-not (Test-Path $exe)) { throw "未生成 $exe" }
Write-QtOk "构建完成: $exe"

# ---------------------------------------------------------------------------
# 4) 把 Qt 运行时拷到 exe 旁边（否则直接运行会报「找不到 Qt6Core.dll」）
# ---------------------------------------------------------------------------
function Get-MingwRuntimeDlls {
    # MinGW 运行时三件套：优先取编译器 bin 里的（与本次实际链接的 libstdc++ 同源），
    # 其次取 Qt 前缀 bin 里的 —— 自动下载的 Qt 包里也带了一份（Qt 仓库的 MinGW runtime 归档）。
    $names = @("libgcc_s_seh-1.dll", "libstdc++-6.dll", "libwinpthread-1.dll")
    $map = @{}
    foreach ($d in @($mingw.Bin, (Join-Path $qt.Prefix "bin"))) {
        foreach ($n in $names) {
            if ($map.ContainsKey($n)) { continue }
            $p = Join-Path $d $n
            if (Test-Path $p) { $map[$n] = $p }
        }
    }
    return $map
}

function Deploy-QtRuntime([string]$TargetDir) {
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

    # 牌面矢量素材：拷到 exe 同级的 tiles/。
    # 用目录而不是 qrc 内嵌，是为了让美术**改完 SVG 重启客户端即可生效**，无需重新编译；
    # 整个 tiles/ 删掉也不影响运行（会回退到程序化绘制）。
    $svgSrc = Join-Path $PSScriptRoot "assets/tiles"
    if (Test-Path $svgSrc) {
        $svgDst = Join-Path $TargetDir "tiles"
        New-Item -ItemType Directory -Force -Path $svgDst | Out-Null
        Copy-Item (Join-Path $svgSrc "*.svg") $svgDst -Force
    }

    # 结算界面用的牌面字体（M+ 授权，可再分发）。
    # 同样用目录而不是 qrc：换字体只需替换文件，不必重新编译。
    $fontSrc = Join-Path $PSScriptRoot "assets/fonts"
    if (Test-Path $fontSrc) {
        $fontDst = Join-Path $TargetDir "fonts"
        New-Item -ItemType Directory -Force -Path $fontDst | Out-Null
        Copy-Item (Join-Path $fontSrc "*.otf") $fontDst -Force -ErrorAction SilentlyContinue
    }

    # 语言文件（文案表）。
    # ⚠ 同样走**目录优先**：协议里只传 ASCII 码（役种 code / limit / reason / error），
    #   中文文案只存在于这里，**改文案不用重新编译**，删掉整个 i18n/ 也有 qrc 兜底。
    $i18nSrc = Join-Path $PSScriptRoot "assets/i18n"
    if (Test-Path $i18nSrc) {
        $i18nDst = Join-Path $TargetDir "i18n"
        New-Item -ItemType Directory -Force -Path $i18nDst | Out-Null
        Copy-Item (Join-Path $i18nSrc "*.json") $i18nDst -Force -ErrorAction SilentlyContinue
    }

    # 音效（离线合成的 WAV，见 tools/gen-sfx.mjs）。
    # 同样**目录优先**：换音效只需替换 wav、或由材质包的 `sfx/` 覆盖，
    # 不必重新编译；删掉整个 sfx/ 也有 qrc 兜底（没声音但不会崩）。
    $sfxSrc = Join-Path $PSScriptRoot "assets/sfx"
    if (Test-Path $sfxSrc) {
        $sfxDst = Join-Path $TargetDir "sfx"
        New-Item -ItemType Directory -Force -Path $sfxDst | Out-Null
        Copy-Item (Join-Path $sfxSrc "*.wav") $sfxDst -Force -ErrorAction SilentlyContinue
    }

    # Qt 自带的许可文本（LGPLv3 / GPLv3 / M+ 等）。因为音效走 Qt Multimedia，
    # 随包分发了 Qt6Multimedia.dll 与它依赖的 FFmpeg（LGPL/GPL 双授权），
    # 所以**必须**把上游许可文本一并带上（见 docs/THIRD-PARTY.md）。
    # ⚠ 放在「已就绪就跳过」判断**之前**（与 tiles/fonts/i18n/sfx 同理无条件拷贝）：
    #   否则已经就绪的旧目录永远补不进 licenses/qt/（这正是当年 dist 缺 styles 的同一个坑）。
    #   上游把文本放在 **Qt 安装根**的 `Licenses/`（不是 `mingw_64/Licenses/`），
    #   所以要从前缀往上找 —— 常见布局是 …/6.11.2/mingw_64（根在 6.11.2 的**父**目录）。
    $licDirs = @((Join-Path $qt.Prefix "Licenses"),
                 (Join-Path $qt.Prefix "licenses"),
                 (Join-Path (Split-Path -Parent $qt.Prefix) "Licenses"),
                 (Join-Path (Split-Path -Parent (Split-Path -Parent $qt.Prefix)) "Licenses"))
    foreach ($src in $licDirs) {
        if (Test-Path $src) {
            $dst = Join-Path $TargetDir "licenses/qt"
            New-Item -ItemType Directory -Force -Path $dst | Out-Null
            Copy-Item "$src/*" $dst -Recurse -Force -ErrorAction SilentlyContinue
            break
        }
    }

    # 第三方许可文本。Qt 是 **LGPLv3（动态链接）**：分发本程序时必须随附许可全文与版权声明，
    # 并让用户能够替换 Qt 的共享库 —— 详见 docs/THIRD-PARTY.md 与 licenses/NOTICE.txt。
    #
    # ⚠ 必须放在下面「已就绪就跳过」判断**之前**（与 tiles/fonts/i18n 同理，无条件拷贝）：
    #   一旦放到判断之后，已经就绪的旧目录就永远补不进 licenses\ ——
    #   这正是当年 dist\ 缺 styles\qmodernwindowsstyle.dll 的同一个坑。
    $licSrc = Join-Path $PSScriptRoot "licenses"
    if (Test-Path $licSrc) {
        $licDst = Join-Path $TargetDir "licenses"
        New-Item -ItemType Directory -Force -Path $licDst | Out-Null
        Copy-Item "$licSrc/*" $licDst -Force -ErrorAction SilentlyContinue
    }

    # ⚠ 必需文件清单：**新增 Qt 模块时必须同步这里**。
    #   漏一个模块的 DLL，用户双击 exe 会直接起不来（无任何输出）。
    #   注意不能只看 Qt6Core.dll 在不在就认为「已就绪」——那会让新模块永远拷不进来
    #   （历史上漏过 Qt6Svg.dll：本机因 PATH 里有 Qt 而能跑，用户机器上却起不来）。
    #
    #   Qt6Multimedia.dll：音效走 `QSoundEffect`（跨平台 Qt API，见 CMakeLists 里的选型说明）。
    #   它**不是**可选装饰 —— exe 链接了它，缺了就直接起不来，所以必须进必需清单。
    $required = @("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "Qt6Network.dll", "Qt6Svg.dll")
    # 构建时如果找到了 Multimedia，就把它（以及 FFmpeg 后端那一组）一起当必需项。
    $mmDll = Join-Path (Join-Path $qt.Prefix "bin") "Qt6Multimedia.dll"
    $haveMultimedia = Test-Path $mmDll
    if ($haveMultimedia) { $required += "Qt6Multimedia.dll" }
    # ⚠ 就绪判断必须覆盖**本函数负责的全部文件**，插件的**目录**也要算进去。
    #   曾经只看 5 个 DLL + platforms/qwindows.dll —— 只要这几个在就整个 return，
    #   于是 dist\ 永远补不进 styles\（build\ 因重建被清空过，走完整路径，所以有）。
    #   症状：build\ 与 dist\ 的插件集不一致，且用户机器上换个 Qt 样式就报缺插件。
    $pluginSubs = @("platforms", "styles", "tls")
    if ($haveMultimedia) { $pluginSubs += "multimedia" }
    $pluginsOk = $true
    foreach ($sub in $pluginSubs) {
        $p = Join-Path $TargetDir $sub
        if (-not (Test-Path $p) -or
            -not (Get-ChildItem (Join-Path $p "*.dll") -ErrorAction SilentlyContinue)) {
            $pluginsOk = $false
        }
    }
    if ($missing.Count -eq 0 -and $pluginsOk) {
        Write-QtOk "Qt 运行时已就绪: $TargetDir"
        return $true
    }
    if ($missing.Count -gt 0) {
        Write-QtWarn "需补齐的 Qt DLL: $($missing -join ', ')"
    } elseif (-not $pluginsOk) {
        Write-QtWarn "需补齐的 Qt 插件目录: $($pluginSubs -join ' / ')"
    }

    # 首选 windeployqt（会顺带处理插件与依赖）
    $deployed = $false
    $wdq = Join-Path $qt.Prefix "bin/windeployqt.exe"
    if (Test-Path $wdq) {
        $old = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $wdq --release --no-translations --no-system-d3d-compiler "$TargetDir/mahjong-client.exe" *> (Join-Path $BuildDir "windeployqt.log")
        if ($LASTEXITCODE -eq 0) { $deployed = $true }
        $ErrorActionPreference = $old
    }

    if (-not $deployed) {
        Write-QtWarn "windeployqt 不可用，改用手动拷贝 Qt DLL 与插件…"
        # ⚠ 新增 Qt 模块时**必须同步这个列表**：漏一个模块的 DLL，exe 会直接起不来
        #   （现象是双击无反应、命令行也无输出）。历史上漏过 Qt6Svg.dll。
        foreach ($d in $required) {
            $src = Join-Path (Join-Path $qt.Prefix "bin") $d
            if (Test-Path $src) { Copy-Item $src $TargetDir -Force }
        }
        # Qt Multimedia 的**媒体后端**是同目录下的一组 FFmpeg DLL（avcodec/avformat/avutil/
        # swresample/swscale）。`QSoundEffect` 放 WAV 走的是内置解码器，但 Qt6Multimedia.dll
        # 静态导入这些库 —— 缺了会直接起不来，所以一起拷。
        if ($haveMultimedia) {
            $mmExtra = @("avcodec-*.dll", "avformat-*.dll", "avutil-*.dll",
                         "swresample-*.dll", "swscale-*.dll")
            foreach ($pat in $mmExtra) {
                Copy-Item (Join-Path (Join-Path $qt.Prefix "bin") $pat) $TargetDir `
                          -Force -ErrorAction SilentlyContinue
            }
            # 许可文本已在上面的"无条件拷贝"里处理（licenses/qt），这里不再重复。
        }
        $runtimeDlls = Get-MingwRuntimeDlls
        foreach ($n in $runtimeDlls.Keys) { Copy-Item $runtimeDlls[$n] $TargetDir -Force }
        if ($runtimeDlls.Count -lt 3) {
            Write-QtWarn ("MinGW 运行时 DLL 只找到 {0}/3（{1}）—— 在没装 MinGW 的机器上可能起不来" -f `
                          $runtimeDlls.Count, ($runtimeDlls.Keys -join ', '))
        }
        $pluginRoot = Join-Path $qt.Prefix "plugins"
        if (-not (Test-Path $pluginRoot)) { $pluginRoot = Join-Path (Split-Path -Parent $qt.Prefix) "plugins" }
        foreach ($sub in $pluginSubs) {
            $src = Join-Path $pluginRoot $sub
            if (-not (Test-Path $src)) { continue }
            $dst = Join-Path $TargetDir $sub
            New-Item -ItemType Directory -Force -Path $dst | Out-Null
            Copy-Item "$src/*.dll" $dst -Force -ErrorAction SilentlyContinue
        }
        # 只保留 Windows 平台插件
        Remove-Item (Join-Path $TargetDir "platforms/qminimal.dll") -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $TargetDir "platforms/qoffscreen.dll") -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $TargetDir "platforms/qdirect2d.dll") -ErrorAction SilentlyContinue
    }
    return $true
}

if (-not $NoRuntime) {
    Write-QtHead "[4/5] 部署 Qt 运行时"
    [void](Deploy-QtRuntime $BuildDir)
}

# ---------------------------------------------------------------------------
# 5) 可选：Qt 源码（LGPLv3 合规用。动态链接下并非强制，见 docs/THIRD-PARTY.md §3）
#    -Deploy 时默认拉取一次并写出 dist\QT-SOURCE.txt（URL + SHA256 + 本地路径）
# ---------------------------------------------------------------------------
$wantSource = ($WithQtSource -or ($Deploy -and -not $NoQtSource)) -and -not $NoQtSource
if ($wantSource) {
    Write-QtHead "[5/5] 获取 Qt 源码（LGPLv3 合规素材）"
    try {
        $srcFiles = Get-QtSourceArchives -Version $qt.Version -CacheDir $QtCacheDir `
                                         -Transport $Transport -Offline:$Offline
        $qtSourceFiles = $srcFiles
        Write-QtOk ("源码已就绪：{0}" -f (($srcFiles | ForEach-Object { Split-Path -Leaf $_ }) -join ', '))
    } catch {
        Write-QtWarn "Qt 源码获取失败（不影响构建）：$($_.Exception.Message)"
        Write-QtWarn "合规兜底：licenses\NOTICE.txt 里已写明 Qt 源码的官方下载地址，可手工提供"
        $qtSourceFiles = @()
    }
}

if ($Deploy) {
    $dist = Join-Path $Root "dist"
    Write-QtHead "[4/5] 产出发布目录 -> $dist"
    New-Item -ItemType Directory -Force -Path $dist | Out-Null
    Copy-Item $exe $dist -Force
    [void](Deploy-QtRuntime $dist)
    Write-QtOk "已打包: $dist"

    if ($qtSourceFiles -and $qtSourceFiles.Count -gt 0) {
        $parts = $qt.Version -split '\.'
        $series = "$($parts[0]).$($parts[1])"
        $lines = @()
        $lines += "Qt $($qt.Version) 对应源码（LGPLv3 合规材料）"
        $lines += "生成时间：$(Get-Date -Format s)"
        $lines += ""
        $lines += "本程序以 LGPLv3 动态链接方式使用 Qt（未修改 Qt 源码）。"
        $lines += "Qt 源码官方地址（无需索取，任何人可直接下载）："
        foreach ($f in $qtSourceFiles) {
            $name = Split-Path -Leaf $f
            $mod = ($name -split '-everywhere')[0]
            $lines += "  https://download.qt.io/official_releases/qt/$series/$($qt.Version)/submodules/$name"
            $lines += "    SHA-256: $((Get-FileHash -LiteralPath $f -Algorithm SHA256).Hash.ToLower())"
            $lines += "    本地副本: $f"
        }
        $lines += ""
        $lines += "若要随发布目录一并分发源码压缩包，用：pwsh -File client\build.ps1 -Deploy -IncludeQtSourceInDist"
        Set-Content -LiteralPath (Join-Path $dist "QT-SOURCE.txt") -Value $lines -Encoding UTF8
        Write-QtOk "已写出 $dist\QT-SOURCE.txt"
        if ($IncludeQtSourceInDist) {
            $srcDst = Join-Path $dist "qt-source"
            New-Item -ItemType Directory -Force -Path $srcDst | Out-Null
            foreach ($f in $qtSourceFiles) { Copy-Item $f $srcDst -Force }
            Write-QtOk "源码压缩包已随 dist 分发: $srcDst"
        }
    }
    if (-not $NoRuntime) { }
}

if ($SelfTest) {
    Write-QtHead "[5/5] 自检 --selftest"
    # ⚠ 这是 **WIN32 GUI 子系统**的可执行文件（CMake 里 WIN32 + qt_add_executable）：
    #   PowerShell 的 `& exe` **不会等它结束**，于是 $LASTEXITCODE 还是上一条命令（windeployqt）
    #   留下的值 —— 表现为「自检输出晚到、退出码却是 1」，非常容易被误判成自检失败（实测踩过）。
    #   所以：Start-Process 等待 + 把输出重定向到文件（自带结果行，退出码拿不到时用它兜底）。
    $stOut  = Join-Path $BuildDir "selftest"
    $stLog  = Join-Path $BuildDir "selftest.log"
    $stErr  = Join-Path $BuildDir "selftest.err.log"
    $proc = Start-Process -FilePath $exe -ArgumentList @('--selftest', $stOut) -NoNewWindow -Wait -PassThru `
                          -RedirectStandardOutput $stLog -RedirectStandardError $stErr
    if (Test-Path $stLog) { Get-Content $stLog | Out-Host }
    if ((Get-Item $stErr -ErrorAction SilentlyContinue).Length -gt 0) { Get-Content $stErr | Out-Host }

    $code = $null
    if ($proc -and $null -ne $proc.ExitCode) { $code = [int]$proc.ExitCode }
    if ($null -eq $code) {
        # 受限环境里可能取不到子进程退出码 → 用自检自己的结论行兜底（失败数非 0 即判失败）
        $text = Get-Content $stLog -Raw -ErrorAction SilentlyContinue
        $code = if ($text -match 'SELFTEST PASS' -and $text -notmatch '失败：[1-9]') { 0 } else { 1 }
        Write-QtWarn "取不到子进程退出码，按自检输出判定：$code"
    }
    Write-QtOk "selftest exit code: $code"
    exit $code
}

$stopwatch.Stop()
Write-Host ""
Write-QtOk ("构建耗时 {0:N1}s" -f $stopwatch.Elapsed.TotalSeconds)
Write-Host "如何运行（无需配置 PATH，Qt 运行时已随 exe 一起）:" -ForegroundColor Green
Write-Host "  双击  $exe"
if ($Deploy) { Write-Host "  或    $(Join-Path $Root 'dist\mahjong-client.exe')  ← 发布用，可整个目录拷给别人" }

# 没带 -Deploy 时提醒 dist 是否已过期：dist 只在 -Deploy 时才更新，
# 很容易出现「build 是新的、dist 还是几小时前的」，而用户往往拿 dist 去联调/分发。
#
# ⚠ 判据用「dist 的 exe 是否早于最新源文件」，**不要用哈希对比**：
#   每次构建都会 clean 重建 + 重新链接，PE 头里的时间戳不同 → 同一份源码的哈希也会变，
#   拿哈希比会次次报警，等于没有信号。
if (-not $Deploy) {
    $distExe = Join-Path $Root "dist\mahjong-client.exe"
    if (Test-Path $distExe) {
        $newest = Get-ChildItem (Join-Path $Root "src"), (Join-Path $Root "assets") `
                    -Recurse -File -Include *.cpp, *.h, *.svg, *.otf -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($newest -and (Get-Item $distExe).LastWriteTime -lt $newest.LastWriteTime) {
            Write-Host ""
            Write-QtWarn "⚠ dist\ 比最新源文件旧（dist 只在 -Deploy 时更新）："
            Write-QtWarn "    dist  : $((Get-Item $distExe).LastWriteTime)"
            Write-QtWarn "    新源码: $($newest.LastWriteTime)  $($newest.Name)"
            Write-QtWarn "  要同步发布目录请跑： pwsh -File client\build.ps1 -Deploy"
        }
    }
}
