# =============================================================================
# tools/qt-provision.ps1 —— 「把 Qt 与构建工具备齐」的通用模块
#
# 为什么存在：仓库里**不能**放 Qt 的头文件与库（几百 MB、第三方产物），但编译 Qt 客户端
# 又必须有它们。于是本模块负责在**目标机器上自动把依赖取回来**，让构建脚本不依赖
# 「这台机器碰巧装过 Qt、而且装在某个固定路径」。
#
# 解析顺序（Resolve-QtPrefix）：
#   1) -QtDir 显式指定                    2) 环境变量（MAHJONG_QT_DIR / QT_ROOT_DIR / QTDIR / CMake 变量）
#   3) PATH 上的 qmake/qtpaths 反查        4) 各磁盘常见安装位置（含 Qt 官方安装器与 aqt 布局）
#   5) 本模块缓存 <CacheDir>\<版本>\<工具链>   6) **从 download.qt.io 在线仓库下载**（与 Qt 官方安装器同源）
#
# 下载什么：只取本项目真正用到的模块（qtbase = Core/Gui/Widgets/Network，qtsvg = Svg），
# 6.11.2 合计约 22 MB（整包 qt-everywhere-src 是 973 MB —— 所以**按模块挑归档**很关键）。
# 编译器 / CMake / Ninja 缺失时同样从该仓库取 qt.tools.*（见 Resolve-CMakeExe 等）。
#
# 实测得到的仓库结构（别再猜，改了先复核）：
#   * 版本目录两代布局并存：
#       老（≤6.8 一类）: desktop/qt6_683/qt6_683/Updates.xml
#       新（6.11+）    : desktop/qt6_6112/qt6_6112_mingw/Updates.xml
#     两种都试，谁解析出 <PackageUpdate> 就用谁（本模块的候选表覆盖）。
#   * 归档 URL = <版本目录>/<包名>/<包版本><归档名>（包版本与归档名**直接拼接、无分隔符**）
#   * 每个归档旁边有 `<归档>.sha1`（实测可下载）→ 一律校验后再解包
#   * 归档内部路径是**相对 Qt 前缀**的（bin/ include/ lib/ plugins/…，实测），
#     所以把各模块归档解到同一个前缀目录即拼出完整 Qt，无需搬目录
#
# 环境适配（都踩过）：
#   * 不假设 7z 命令存在：Windows 自带的 tar.exe 是 bsdtar/libarchive，**能读 Qt 的 .7z**（实测）；
#     但 GNU tar 不能 → 用前先看 `tar --version` 里有没有 libarchive
#   * 不假设 .NET/schannel 的 TLS 可用：受限环境里 Invoke-WebRequest 与 curl.exe 都会
#     `SEC_E_NO_CREDENTIALS` 失败 → 下载链为 iwr → curl → node（tools/fetch-url.mjs）
#   * 只写工作区内：缓存默认 <仓库根>/.qt（已被 .gitignore 忽略）
# =============================================================================

# ---- 默认常量（可用参数 / 环境变量覆盖）-------------------------------------
$script:QtRepoDefault     = 'https://download.qt.io/online/qtsdkrepository/windows_x86/desktop'
$script:QtOfficialDefault = 'https://download.qt.io/official_releases/qt'
# 与本项目实际验证过的版本一致；MAHJONG_QT_VERSION 或 -QtVersion 可覆盖（支持 'latest'）
$script:QtDefaultVersion  = '6.11.2'
# 本项目需要的模块（Core/Gui/Widgets/Network 来自 qtbase，Svg 来自 qtsvg）
$script:QtDefaultModules  = @('qtbase', 'qtsvg')
$script:FetchHelper       = Join-Path $PSScriptRoot 'fetch-url.mjs'
$script:SkipDirNames      = @('Windows', 'ProgramData', '$Recycle.Bin', 'System Volume Information',
                              'node_modules', '.git', '.qt', 'AppData', 'Temp', 'tmp', 'build',
                              'dist', '.cache', 'CMakeFiles')

# ---- 日志 --------------------------------------------------------------------
function Write-QtHead([string]$Message) { Write-Host $Message -ForegroundColor Cyan }
function Write-QtInfo([string]$Message) { Write-Host "      $Message" -ForegroundColor DarkGray }
function Write-QtOk([string]$Message)   { Write-Host "      $Message" -ForegroundColor Green }
function Write-QtWarn([string]$Message) { Write-Host "      $Message" -ForegroundColor Yellow }

# ---- 小工具 ------------------------------------------------------------------
function Test-FileExists([string]$Path) { return ($Path -and (Test-Path -LiteralPath $Path -PathType Leaf)) }

function Get-EnvFirst([string[]]$Names) {
    foreach ($n in $Names) {
        $v = [Environment]::GetEnvironmentVariable($n)
        if ($v) { return $v }
    }
    return $null
}

function Get-CommandFile([string]$Name) {
    $c = Get-Command $Name -ErrorAction SilentlyContinue
    if ($c -and $c.Source) { return $c.Source }
    return $null
}

function Assert-ContentHash {
    param([string]$Path, [string]$Algorithm, [string]$Expected)
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm $Algorithm).Hash.ToLower()
    if ($actual -ne $Expected.ToLower()) {
        throw "$Algorithm 校验失败：期望 $Expected，实际 $actual（$Path）"
    }
}

# ---- 网络传输：iwr → curl → node --------------------------------------------
function Get-FetchOrder([string]$Transport) {
    if (-not $Transport) { $Transport = $env:MAHJONG_FETCH_TRANSPORT }
    if (-not $Transport) { $Transport = 'auto' }
    if ($Transport -ne 'auto') { return @($Transport) }
    $list = @('iwr')
    if (Get-CommandFile 'curl.exe') { $list += 'curl' }
    if (Get-CommandFile 'node')     { $list += 'node' }
    return $list
}

function Invoke-Fetch {
    param([string]$Url, [string]$OutFile, [string]$Sha1, [string]$Sha256,
          [string]$Transport, [switch]$Quiet)
    $dir = Split-Path -Parent $OutFile
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $problems = @()
    foreach ($t in (Get-FetchOrder $Transport)) {
        try {
            switch ($t) {
                'iwr' {
                    # PS 5.1 默认可能还是 TLS 1.0，显式抬到 1.2
                    try {
                        [Net.ServicePointManager]::SecurityProtocol =
                            [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
                    } catch { }
                    $oldPref = $ProgressPreference
                    $ProgressPreference = 'SilentlyContinue'
                    try {
                        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -ErrorAction Stop
                    } finally { $ProgressPreference = $oldPref }
                }
                'curl' {
                    & curl.exe -fsSL --retry 2 --connect-timeout 30 -o $OutFile $Url
                    if ($LASTEXITCODE -ne 0) { throw "curl 退出码 $LASTEXITCODE" }
                }
                'node' {
                    if (-not (Test-FileExists $script:FetchHelper)) { throw "缺少 $script:FetchHelper" }
                    $nodeArgs = @($script:FetchHelper, $Url, $OutFile)
                    if ($Sha1)   { $nodeArgs += @('--sha1', $Sha1) }
                    if ($Sha256) { $nodeArgs += @('--sha256', $Sha256) }
                    if ($Quiet)  { $nodeArgs += '--quiet' }
                    & node @nodeArgs
                    if ($LASTEXITCODE -ne 0) { throw "node fetch 退出码 $LASTEXITCODE" }
                }
            }
            if ($Sha1)   { Assert-ContentHash $OutFile 'SHA1'   $Sha1 }
            if ($Sha256) { Assert-ContentHash $OutFile 'SHA256' $Sha256 }
            return $t
        } catch {
            $problems += "  [$t] $($_.Exception.Message)"
            Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue
        }
    }
    throw ("下载失败：$Url`n" + ($problems -join "`n"))
}

function Get-RemoteSha1 {
    param([string]$Url, [string]$CacheDir, [string]$Transport)
    $tmp = Join-Path (Join-Path $CacheDir '_meta') ([IO.Path]::GetFileName($Url) + '.sha1')
    try {
        Invoke-Fetch -Url ($Url + '.sha1') -OutFile $tmp -Transport $Transport -Quiet | Out-Null
        return ((Get-Content -LiteralPath $tmp -Raw).Trim() -split '\s+')[0].ToLower()
    } catch { return '' }
}

# 带缓存的下载：同名归档只下一次，之后按本地记下的 sha1 校验复用
function Get-CachedDownload {
    param([string]$Url, [string]$CacheDir, [string]$Name, [string]$Transport, [switch]$Offline)
    $file    = Join-Path (Join-Path $CacheDir '_dl') $Name
    $shaFile = "$file.sha1"
    if (Test-FileExists $file) {
        if (Test-FileExists $shaFile) {
            $want = (Get-Content -LiteralPath $shaFile -Raw).Trim()
            try {
                Assert-ContentHash $file 'SHA1' $want
                Write-QtInfo "复用缓存：$Name"
                return $file
            } catch { Write-QtWarn "缓存校验失败，重新下载：$Name" }
        } elseif ($Offline) {
            Write-QtInfo "离线：直接使用 $Name（无 sha1 记录）"
            return $file
        }
    }
    if ($Offline) { throw "离线模式且缓存里没有 $Name（预期位置：$CacheDir\_dl）" }
    $sha = Get-RemoteSha1 -Url $Url -CacheDir $CacheDir -Transport $Transport
    if (-not $sha) { Write-QtWarn "官方未提供 .sha1，本次跳过校验：$Name" }
    Write-QtInfo "下载 $Name"
    Invoke-Fetch -Url $Url -OutFile $file -Sha1 $sha -Transport $Transport | Out-Null
    if ($sha) { Set-Content -LiteralPath $shaFile -Value $sha -Encoding ASCII }
    return $file
}

# ---- 解包：7z → bsdtar → Expand-Archive -------------------------------------
function Find-SevenZip {
    foreach ($n in @('7z.exe', '7za.exe', '7zz.exe', '7z', '7za', '7zz')) {
        $p = Get-CommandFile $n
        if ($p) { return $p }
    }
    return $null
}

function Test-BsdTar([string]$TarExe) {
    if (-not $TarExe) { return $false }
    try {
        $v = (& $TarExe --version 2>&1 | Out-String)
        return ($v -match 'libarchive')
    } catch { return $false }
}

function Expand-QtArchive {
    param([string]$File, [string]$Destination)
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $sevenZip = Find-SevenZip
    if ($sevenZip) {
        & $sevenZip x -y "-o$Destination" $File | Out-Null
        if ($LASTEXITCODE -eq 0) { return '7z' }
        Write-QtWarn "7z 解包失败，改用 tar 重试"
    }
    $tarExe = Get-CommandFile 'tar'
    if ($tarExe -and (Test-BsdTar $tarExe)) {
        & $tarExe -xf $File -C $Destination
        if ($LASTEXITCODE -eq 0) { return 'bsdtar' }
        throw "bsdtar 解包失败：$File"
    }
    if ($File -like '*.zip') {
        Expand-Archive -LiteralPath $File -DestinationPath $Destination -Force
        return 'Expand-Archive'
    }
    throw ("无法解包 $File：既没有 7-Zip，也没有支持 7z 的 bsdtar（libarchive）。`n" +
           "      解决：装 7-Zip，或用 -QtDir 指定一台已装 Qt 的机器上的 Qt 目录。")
}

# 归档解开后真正的「根」：多数是自身（含 bin/include/lib），个别是套了一层目录
function Get-ExtractedRoot {
    param([string]$Dir)
    foreach ($probe in @('include', 'bin', 'lib')) {
        if (Test-Path (Join-Path $Dir $probe)) { return $Dir }
    }
    $cur = $Dir
    for ($i = 0; $i -lt 3; $i++) {
        $subs = @(Get-ChildItem -LiteralPath $cur -Directory -ErrorAction SilentlyContinue)
        if ($subs.Count -ne 1) { break }
        $cur = $subs[0].FullName
        foreach ($probe in @('include', 'bin', 'lib')) {
            if (Test-Path (Join-Path $cur $probe)) { return $cur }
        }
    }
    return $Dir
}

function Merge-Tree {
    param([string]$From, [string]$To)
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    Get-ChildItem -LiteralPath $From -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $To -Recurse -Force
    }
}

# ---- 仓库索引（Updates.xml）--------------------------------------------------
function ConvertFrom-UpdatesXml {
    param([string]$Path)
    $doc = New-Object System.Xml.XmlDocument
    $doc.Load($Path)
    $list = @()
    foreach ($p in $doc.Updates.PackageUpdate) {
        $archives = @()
        if ($p.DownloadableArchives) {
            $archives = @(($p.DownloadableArchives -split ',') |
                          ForEach-Object { $_.Trim() } | Where-Object { $_ })
        }
        $list += [pscustomobject]@{
            Name      = [string]$p.Name
            Version   = [string]$p.Version
            Archives  = $archives
            HasFiles  = ($archives.Count -gt 0)
        }
    }
    return $list
}

# 把 Updates.xml 取到本地缓存后解析；候选路径覆盖两代布局
function Get-RepoUpdates {
    param([string]$RepoBase, [string[]]$RelCandidates, [string]$CacheDir,
          [string]$Transport, [switch]$Offline)
    $tried = @()
    foreach ($rel in $RelCandidates) {
        $url = "$RepoBase/$rel"
        $name = ($rel -replace '[\\/]', '_')
        $local = Join-Path (Join-Path $CacheDir '_meta') $name
        try {
            if ($Offline) {
                if (-not (Test-FileExists $local)) { throw "离线且无缓存 $local" }
            } else {
                Invoke-Fetch -Url $url -OutFile $local -Transport $Transport -Quiet | Out-Null
            }
            $pkgs = @(ConvertFrom-UpdatesXml $local)
            if ($pkgs.Count -gt 0) { return [pscustomobject]@{ Url = $url; Packages = $pkgs } }
            $tried += "$rel（解析出 0 个包）"
        } catch {
            $tried += $rel
        }
    }
    throw ("在 $RepoBase 下找不到可用的 Updates.xml。`n      试过：`n        " + ($tried -join "`n        "))
}

function Get-QtVersionDirName([string]$Version) { return ('qt6_' + ($Version -replace '\.', '')) }

# Qt 主包索引：桌面版 / MinGW 工具链
function Get-QtRepoIndex {
    param([string]$RepoBase, [string]$Version, [string]$Toolchain,
          [string]$CacheDir, [string]$Transport, [switch]$Offline)
    $vd = Get-QtVersionDirName $Version
    $cands = @(
        "$vd/Updates.xml",
        "$vd/$vd/Updates.xml",
        "$vd/${vd}_$Toolchain/Updates.xml",
        "${vd}_$Toolchain/Updates.xml",
        "$vd/${vd}_msvc2022_64/Updates.xml"
    ) | Select-Object -Unique
    return (Get-RepoUpdates -RepoBase $RepoBase -RelCandidates $cands -CacheDir $CacheDir `
                            -Transport $Transport -Offline:$Offline)
}

# 列出仓库里最新的 Qt 版本（-QtVersion latest 用）
function Get-LatestQtVersion {
    param([string]$RepoBase, [string]$CacheDir, [string]$Transport)
    $local = Join-Path (Join-Path $CacheDir '_meta') 'desktop-index.html'
    Invoke-Fetch -Url "$RepoBase/" -OutFile $local -Transport $Transport -Quiet | Out-Null
    $html = Get-Content -LiteralPath $local -Raw
    $vers = @([regex]::Matches($html, 'qt6_(\d{3,4})/') |
              ForEach-Object { $_.Groups[1].Value } |
              Sort-Object -Unique)
    if ($vers.Count -eq 0) { throw '无法从仓库目录列表解析出版本号' }
    $digits = $vers[-1]
    # 6112 → 6.11.2（末位是 patch）
    $major = $digits.Substring(0, 1)
    $minor = $digits.Substring(1, $digits.Length - 2)
    $patch = $digits.Substring($digits.Length - 1)
    return "$major.$minor.$patch"
}

# ---- Qt 前缀识别与安装 ------------------------------------------------------
function Test-QtPrefix {
    param([string]$Dir)
    if (-not $Dir -or -not (Test-Path -LiteralPath $Dir)) { return $false }
    $hasCore = (Test-Path (Join-Path $Dir 'include/QtCore/qglobal.h')) -or
               (Test-Path (Join-Path $Dir 'lib/cmake/Qt6/Qt6Config.cmake'))
    if (-not $hasCore) { return $false }
    return (Test-Path (Join-Path $Dir 'bin')) -or (Test-Path (Join-Path $Dir 'lib'))
}

function Get-QtPrefixVersion {
    param([string]$Dir)
    $qmake = Join-Path $Dir 'bin/qmake.exe'
    if (-not (Test-FileExists $qmake)) { $qmake = Join-Path $Dir 'bin/qmake' }
    if (Test-FileExists $qmake) {
        try {
            # ⚠ 这里**不能**看 $LASTEXITCODE：`& exe | Select-Object -First 1` 会提前掐断管道，
            #   $LASTEXITCODE 可能压根没被赋值（实测空值），于是版本号被误判成"未知"。
            #   改为只校验输出形状。
            $v = (& $qmake -query QT_VERSION 2>$null | Select-Object -First 1)
            if ($v -and ($v -is [string]) -and $v.Trim() -match '^\d+\.\d+') { return $v.Trim() }
        } catch { }
    }
    $verFile = Join-Path $Dir 'include/QtCore/qtcoreversion.h'
    if (Test-FileExists $verFile) {
        $m = [regex]::Match((Get-Content -LiteralPath $verFile -Raw), 'QT_VERSION_STR\s+"([^"]+)"')
        if ($m.Success) { return $m.Groups[1].Value }
    }
    return ''
}

# 各磁盘的常见安装位置（不写死某一台机器的路径，而是列出常见布局）
function Get-QtSearchRoots {
    $roots = New-Object System.Collections.Generic.List[string]
    $drives = @()
    try {
        $drives = @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
                    Where-Object { $_.Root -match '^[A-Za-z]:[\\/]$' } |
                    ForEach-Object { $_.Root })
    } catch { }
    if ($drives.Count -eq 0) { $drives = @('C:\') }
    $rels = @('Qt', 'Dependencies\Qt', 'Dependencies', 'dev\Qt', 'Dev\Qt', 'Libraries\Qt',
              'sdk\Qt', 'tools\Qt', 'Program Files\Qt', 'Program Files (x86)\Qt', 'Qt6')
    foreach ($d in $drives) {
        foreach ($rel in $rels) {
            $p = Join-Path $d $rel
            if (Test-Path -LiteralPath $p) { $roots.Add($p) }
        }
    }
    # ⚠ 别用 $HOME 当循环变量：PowerShell 里它是只读自动变量
    foreach ($homeDir in @($env:USERPROFILE, $env:HOME)) {
        if (-not $homeDir) { continue }
        foreach ($rel in @('Qt', 'dev/Qt', 'sources')) {
            $p = Join-Path $homeDir $rel
            if (Test-Path -LiteralPath $p) { $roots.Add($p) }
        }
    }
    foreach ($p in @('/opt/Qt', '/opt/qt', '/usr/local/Qt', '/usr/lib/x86_64-linux-gnu/cmake')) {
        if (Test-Path -LiteralPath $p) { $roots.Add($p) }
    }
    return @($roots | Select-Object -Unique)
}

# 「宿主机可用」的 Qt 前缀：必须有给宿主用的 bin（Android/iOS 那种目标包没有，不能用来构建桌面程序）
function Test-QtHostPrefix {
    param([string]$Dir)
    foreach ($rel in @('bin/Qt6Core.dll', 'bin/qmake.exe', 'bin/qmake6', 'bin/qmake',
                       'lib/libQt6Core.so.6', 'lib/libQt6Core.so', 'lib/libQt6Core.dylib')) {
        if (Test-Path (Join-Path $Dir $rel)) { return $true }
    }
    return $false
}

# 收集候选（有界）：命中即不再往里钻（前缀的子目录不可能是另一个前缀）
function Add-QtPrefixCandidates {
    param([string]$Dir, [int]$Depth, [int]$MaxDepth, [ref]$Budget, $List)
    if ($Budget.Value -le 0 -or $Depth -gt $MaxDepth) { return }
    $Budget.Value--
    if (Test-QtPrefix $Dir) { [void]$List.Add($Dir); return }
    foreach ($sub in @(Get-ChildItem -LiteralPath $Dir -Directory -ErrorAction SilentlyContinue)) {
        if ($script:SkipDirNames -contains $sub.Name) { continue }
        Add-QtPrefixCandidates -Dir $sub.FullName -Depth ($Depth + 1) -MaxDepth $MaxDepth `
                               -Budget $Budget -List $List
    }
}

# 从候选里挑最合适的那个：
#   * 目标包（android_*/ios）直接排除 —— 实测踩过：扫描先撞上 android_arm64_v8a，
#     它有 include/ 与 lib/，看着"像"Qt，但**没有宿主的 bin**，拿去构建必然失败
#   * 版本一致 +100，不一致 -60（不硬排除：用户机器上只有别的 6.x 时也该能用，只是提醒）
#   * 目录名与请求的工具链一致 +30（mingw_64 / msvc2022_64）
function Select-BestQtPrefix {
    param([string[]]$Candidates, [string]$WantVersion, [string]$Toolchain)
    $tcPattern = if ($Toolchain -match 'msvc') { '^msvc' }
                 elseif ($Toolchain -match 'llvm') { '^llvm' }
                 else { '^mingw' }
    $best = $null
    $bestScore = [double]::NegativeInfinity
    foreach ($c in @($Candidates | Select-Object -Unique)) {
        if (-not (Test-QtHostPrefix $c)) { continue }
        $name = Split-Path -Leaf $c
        $v = Get-QtPrefixVersion $c
        $score = 0
        if ($WantVersion) {
            if ($v -eq $WantVersion) { $score += 100 }
            elseif ($v)             { $score -= 60 }
        }
        if ($name -match $tcPattern) { $score += 30 }
        if (Test-Path (Join-Path $c 'plugins')) { $score += 5 }
        if ($score -gt $bestScore) { $bestScore = $score; $best = $c }
    }
    return [pscustomobject]@{ Prefix = $best; Score = $bestScore
                              Version = $(if ($best) { Get-QtPrefixVersion $best } else { '' }) }
}

function Get-QtPrefixFromPath {
    foreach ($tool in @('qmake6', 'qmake', 'qtpaths6', 'qtpaths')) {
        $exe = Get-CommandFile $tool
        if (-not $exe) { continue }
        try {
            # 同 Get-QtPrefixVersion：不看 $LASTEXITCODE（管道提前掐断会丢），只校验形状
            $out = & $exe -query QT_INSTALL_PREFIX 2>$null | Select-Object -First 1
            if ($out -and ($out -is [string])) {
                $p = $out.Trim()
                if ($p -and (Test-QtPrefix $p)) { return $p }
            }
        } catch { }
    }
    return $null
}

function Get-QtPrefixFromEnv {
    $direct = Get-EnvFirst @('MAHJONG_QT_DIR', 'QT_ROOT_DIR', 'QTDIR', 'Qt6_ROOT')
    if ($direct -and (Test-QtPrefix $direct)) { return $direct }
    # Qt6_DIR 指向 lib/cmake/Qt6，往上三层才是前缀
    $cmakeDir = Get-EnvFirst @('Qt6_DIR', 'Qt6Core_DIR')
    if ($cmakeDir) {
        $guess = (Resolve-Path (Join-Path $cmakeDir '../../..') -ErrorAction SilentlyContinue)
        if ($guess -and (Test-QtPrefix $guess.Path)) { return $guess.Path }
    }
    $prefixPath = Get-EnvFirst @('CMAKE_PREFIX_PATH')
    if ($prefixPath) {
        foreach ($p in ($prefixPath -split '[;:]')) {
            if ($p -and (Test-QtPrefix $p)) { return $p }
        }
    }
    return $null
}

# 这个包里是否含有指定模块的归档（用于排除 addons / debug 之类同名干扰包）
function Test-PackageModules {
    param($Package, [string[]]$Modules)
    foreach ($m in $Modules) {
        if (-not ($Package.Archives | Where-Object { $_ -match "^$m[-_]" })) { return $false }
    }
    return $true
}

function Install-QtFromRepo {
    param([string]$Version, [string]$Prefix, [string]$CacheDir, [string[]]$Modules,
          [string]$RepoBase, [string]$Toolchain, [string]$Transport,
          [switch]$Force, [switch]$Offline)
    $marker = Join-Path $Prefix '.mahjong-qt.json'
    if ((Test-FileExists $marker) -and -not $Force -and (Test-QtPrefix $Prefix)) {
        Write-QtOk "缓存已就绪：$Prefix"
        return
    }
    if ($Offline) { throw '离线模式下不能自动获取 Qt（去掉 -Offline，或用 -QtDir 指定已有 Qt）' }

    Write-QtHead "      从 Qt 官方仓库获取 Qt $Version（只取用到的模块：$($Modules -join ', ')）"
    $idx = Get-QtRepoIndex -RepoBase $RepoBase -Version $Version -Toolchain $Toolchain `
                           -CacheDir $CacheDir -Transport $Transport
    # ⚠ 目录名与包名的编号写法不同（实测）：目录是 qt6_6112，包名是 qt.qt6.6112.<工具链>
    # ⚠ 而且同一个版本目录下有几十个包（qt.qt6.6112.addons.qt3d.debug_information.win64_mingw …），
    #   只按前缀匹配会撞上 addons/debug 包（实测撞过一次）。判据必须落到
    #   「这个包里确实含有我们要的模块归档」，而不是名字像。
    $digits = ($Version -replace '\.', '')
    $all = @($idx.Packages | Where-Object { $_.HasFiles })
    $exactName = "qt.qt6.$digits.win64_$Toolchain"
    $pkg = $all | Where-Object { $_.Name -eq $exactName } | Select-Object -First 1
    if (-not $pkg) {
        $pkg = $all | Where-Object { $_.Name -like "qt.qt6.$digits.*$Toolchain*" -and (Test-PackageModules $_ $Modules) } |
               Select-Object -First 1
    }
    if (-not $pkg) {
        $pkg = $all | Where-Object { Test-PackageModules $_ $Modules } | Select-Object -First 1
    }
    if (-not $pkg) {
        $avail = ($all | ForEach-Object { $_.Name }) -join ', '
        throw "仓库里没有同时包含 $($Modules -join '/') 的 Qt $Version 包（$($idx.Url)）。`n      可用包：$avail"
    }
    Write-QtInfo "包：$($pkg.Name)  版本：$($pkg.Version)"

    $wanted = @()
    foreach ($m in $Modules) {
        $hit = $pkg.Archives | Where-Object { $_ -match "^$m[-_]" } | Select-Object -First 1
        if (-not $hit) {
            throw "包 $($pkg.Name) 里找不到模块 '$m' 的归档。可用归档：`n        $($pkg.Archives -join "`n        ")"
        }
        $wanted += $hit
    }
    # MinGW 运行库（libgcc/libstdc++/libwinpthread）顺带取一份放进前缀 bin：
    # 这样即使目标机器上没装 MinGW，dist 也能凑齐绿色运行所需的 DLL。
    $runtime = $pkg.Archives | Where-Object { $_ -match '^MinGW-w64.*runtime' } | Select-Object -First 1
    if ($runtime) { $wanted += $runtime }

    $stageRoot = Join-Path $CacheDir '_stage'
    foreach ($arch in $wanted) {
        $url  = "$($idx.Url -replace '/Updates.xml$', '')/$($pkg.Name)/$($pkg.Version)$arch"
        $file = Get-CachedDownload -Url $url -CacheDir $CacheDir -Name $arch -Transport $Transport
        $stage = Join-Path $stageRoot ([IO.Path]::GetFileNameWithoutExtension($arch))
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        Write-QtInfo "解包 $arch"
        [void](Expand-QtArchive -File $file -Destination $stage)
        $root = Get-ExtractedRoot $stage
        # ⚠ MinGW 运行库归档是**平铺的 DLL**（实测：libgcc_s_seh-1/libstdc++-6/libwinpthread-1
        #   直接躺在归档根，没有 bin/ 也没有 include|lib）—— 这种必须并进 <前缀>/bin，
        #   否则 DLL 会散落在 Qt 前缀根部（既不像 Qt 布局，deploy 也找不到）。
        $dest = $Prefix
        $flatDlls = @(Get-ChildItem -LiteralPath $root -File -Filter '*.dll' -ErrorAction SilentlyContinue)
        if ($flatDlls.Count -gt 0 -and
            -not (Test-Path (Join-Path $root 'bin')) -and
            -not (Test-Path (Join-Path $root 'include')) -and
            -not (Test-Path (Join-Path $root 'lib'))) {
            $dest = Join-Path $Prefix 'bin'
            Write-QtInfo "（平铺 DLL 归档 → 合并到 bin/）"
        }
        Merge-Tree -From $root -To $dest
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-QtPrefix $Prefix)) { throw "解包后仍未形成可用的 Qt 前缀：$Prefix" }
    [pscustomobject]@{
        version   = $Version
        toolchain = $Toolchain
        modules   = $Modules
        archives  = $wanted
        source    = 'download.qt.io'
        fetchedAt = (Get-Date).ToString('s')
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $marker -Encoding UTF8
    Write-QtOk "已安装到 $Prefix"
}

# ---- 工具链（qt.tools.*）-----------------------------------------------------
function Get-ToolsRepoIndex {
    param([string]$ToolDir, [string]$RepoBase, [string]$CacheDir, [string]$Transport, [switch]$Offline)
    return (Get-RepoUpdates -RepoBase $RepoBase -RelCandidates @("$ToolDir/Updates.xml") `
                            -CacheDir $CacheDir -Transport $Transport -Offline:$Offline)
}

function Install-QtToolPackage {
    param([string]$ToolDir, [string]$PackagePattern, [string]$Destination,
          [string]$RepoBase, [string]$CacheDir, [string]$Transport, [switch]$Force)
    $marker = Join-Path $Destination '.mahjong-tool.json'
    if ((Test-FileExists $marker) -and -not $Force) {
        Write-QtInfo "工具已就绪：$Destination"
        return
    }
    Write-QtHead "      从 Qt 官方仓库获取工具（$ToolDir）"
    $idx = Get-ToolsRepoIndex -ToolDir $ToolDir -RepoBase $RepoBase -CacheDir $CacheDir -Transport $Transport
    $pkg = @($idx.Packages | Where-Object { $_.HasFiles -and $_.Name -like $PackagePattern }) | Select-Object -First 1
    if (-not $pkg) {
        $names = ($idx.Packages | ForEach-Object { $_.Name }) -join ', '
        throw "仓库 $($idx.Url) 里没有匹配 '$PackagePattern' 的包。可用包：$names"
    }
    foreach ($arch in $pkg.Archives) {
        $url  = "$($idx.Url -replace '/Updates.xml$', '')/$($pkg.Name)/$($pkg.Version)$arch"
        $file = Get-CachedDownload -Url $url -CacheDir $CacheDir -Name $arch -Transport $Transport
        $stage = Join-Path (Join-Path $CacheDir '_stage') ([IO.Path]::GetFileNameWithoutExtension($arch))
        if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        Write-QtInfo "解包 $arch"
        [void](Expand-QtArchive -File $file -Destination $stage)
        $root = Get-ExtractedRoot $stage
        Merge-Tree -From $root -To $Destination
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
    [pscustomobject]@{
        package   = $pkg.Name
        version   = $pkg.Version
        archives  = $pkg.Archives
        fetchedAt = (Get-Date).ToString('s')
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $marker -Encoding UTF8
    Write-QtOk "已安装到 $Destination"
}

function Find-FirstFile {
    param([string]$Root, [string]$Filter)
    if (-not (Test-Path -LiteralPath $Root)) { return $null }
    $f = Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $Filter -ErrorAction SilentlyContinue |
         Select-Object -First 1
    if ($f) { return $f.FullName }
    return $null
}

# ---- 对外：解析 Qt ----------------------------------------------------------
function Resolve-QtPrefix {
    param(
        [string]$Explicit,
        [string]$Version,
        [string]$CacheDir,
        [ValidateSet('auto', 'never', 'always')][string]$Provision = 'auto',
        [string]$RepoBase,
        [string]$Toolchain = 'mingw',
        [string[]]$Modules,
        [string]$Transport,
        [switch]$Offline,
        [switch]$DeepScan,
        [switch]$GlobalScan,
        [switch]$Refresh,
        [switch]$Plan
    )
    if (-not $Version)   { $Version  = (Get-EnvFirst @('MAHJONG_QT_VERSION')); if (-not $Version) { $Version = $script:QtDefaultVersion } }
    if (-not $RepoBase)  { $RepoBase = (Get-EnvFirst @('MAHJONG_QT_REPO'));     if (-not $RepoBase) { $RepoBase = $script:QtRepoDefault } }
    if (-not $Modules -or $Modules.Count -eq 0) { $Modules = $script:QtDefaultModules }
    if (-not $CacheDir)  { $CacheDir = (Get-EnvFirst @('MAHJONG_QT_CACHE')) }
    if (-not $CacheDir)  { $CacheDir = Join-Path (Split-Path -Parent $PSScriptRoot) '.qt' }
    if (-not $Version -or $Version -eq 'latest') {
        if ($Plan) { $Version = $script:QtDefaultVersion }
        else { $Version = Get-LatestQtVersion -RepoBase $RepoBase -CacheDir $CacheDir -Transport $Transport }
    }

    # 1) 显式指定永远优先
    if ($Explicit) {
        if (-not (Test-QtPrefix $Explicit)) {
            throw "-QtDir 指向的目录不是可用的 Qt 前缀（缺 include/QtCore 或 lib/cmake/Qt6）：$Explicit"
        }
        return [pscustomobject]@{ Prefix = (Resolve-Path $Explicit).Path; Source = 'explicit'
                                  Version = (Get-QtPrefixVersion $Explicit); Toolchain = $Toolchain }
    }
    $alwaysFetch = ($Provision -eq 'always')

    if (-not $alwaysFetch) {
        # 2) 环境变量
        $p = Get-QtPrefixFromEnv
        if ($p) { return [pscustomobject]@{ Prefix = $p; Source = 'env'; Version = (Get-QtPrefixVersion $p); Toolchain = $Toolchain } }
        # 3) PATH 上的 qmake / qtpaths
        $p = Get-QtPrefixFromPath
        if ($p) { return [pscustomobject]@{ Prefix = $p; Source = 'path'; Version = (Get-QtPrefixVersion $p); Toolchain = $Toolchain } }
        # 4) 各磁盘常见安装位置（有界扫描：先收集候选，再按 版本/工具链/宿主机可用 打分挑一个）
        $budget = 4000
        $cands = New-Object System.Collections.Generic.List[string]
        foreach ($root in (Get-QtSearchRoots)) {
            Add-QtPrefixCandidates -Dir $root -Depth 0 -MaxDepth 3 -Budget ([ref]$budget) -List $cands
        }
        # 4b) 可选：从各磁盘根目录再扫一层（默认关，Qt 藏在奇怪位置时才用）
        if ($DeepScan -or $GlobalScan) {
            Write-QtInfo "附加扫描：各磁盘根目录下深度 4"
            foreach ($d in @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue |
                             Where-Object { $_.Root -match '^[A-Za-z]:[\\/]$' } | ForEach-Object { $_.Root })) {
                Add-QtPrefixCandidates -Dir $d -Depth 0 -MaxDepth 4 -Budget ([ref]$budget) -List $cands
            }
        }
        if ($cands.Count -gt 0) {
            $pick = Select-BestQtPrefix -Candidates $cands.ToArray() -WantVersion $Version -Toolchain $Toolchain
            if ($pick.Prefix) {
                if ($pick.Version -and $Version -and $pick.Version -ne $Version) {
                    Write-QtWarn ("机器上是 Qt {0}，与请求的 {1} 不同（可用 -QtVersion 指定，或加 -Provision always 强制下载 {1}）" -f `
                                  $pick.Version, $Version)
                }
                return [pscustomobject]@{ Prefix = (Resolve-Path $pick.Prefix).Path; Source = 'scan'
                                          Version = $pick.Version; Toolchain = $Toolchain }
            }
            Write-QtInfo ("扫描到 {0} 个 Qt 目录，但没有宿主可用的（{1}）" -f `
                          $cands.Count, (($cands | ForEach-Object { Split-Path -Leaf $_ }) -join ', '))
        }
        if ($budget -le 0) {
            Write-QtWarn "目录扫描预算用尽（为避免卡住，扫描有上限）；Qt 可能在更深的位置，可用 -DeepScan 或 -QtDir"
        }
    }

    # 5) 本模块缓存 6) 下载
    $cached = Join-Path (Join-Path $CacheDir $Version) $(if ($Toolchain -eq 'mingw') { 'mingw_64' } else { $Toolchain })
    if ((Test-QtPrefix $cached) -and -not $Refresh) {
        return [pscustomobject]@{ Prefix = $cached; Source = 'cache'; Version = (Get-QtPrefixVersion $cached); Toolchain = $Toolchain }
    }
    if ($Provision -eq 'never') {
        throw ("没找到 Qt $Version，且已指定 -Provision never（禁止自动获取）。`n" +
               "      请装 Qt 后用 -QtDir 指定，或去掉 -Provision never 让脚本自动下载。")
    }
    if ($Plan) {
        Write-QtWarn "计划：从 $RepoBase 自动获取 Qt $Version → $cached"
        return [pscustomobject]@{ Prefix = $cached; Source = 'plan-download'; Version = $Version; Toolchain = $Toolchain }
    }
    Install-QtFromRepo -Version $Version -Prefix $cached -CacheDir $CacheDir -Modules $Modules `
                       -RepoBase $RepoBase -Toolchain $Toolchain -Transport $Transport `
                       -Offline:$Offline -Force:$Refresh
    return [pscustomobject]@{ Prefix = $cached; Source = 'downloaded'; Version = (Get-QtPrefixVersion $cached); Toolchain = $Toolchain }
}

# ---- 对外：解析 CMake / MinGW / Ninja ---------------------------------------
function Get-CMakeVersion([string]$Exe) {
    try {
        $out = (& $Exe --version 2>$null | Select-Object -First 1)
        $m = [regex]::Match($out, '(\d+\.\d+(\.\d+)?)')
        if ($m.Success) { return $m.Groups[1].Value }
    } catch { }
    return ''
}

function Resolve-CMakeExe {
    param([string]$Explicit, [string]$CacheDir, [string]$RepoBase, [string]$Transport,
          [switch]$Offline, [switch]$AllowDownload = $true, [switch]$Plan)
    if (-not $CacheDir) { $CacheDir = Join-Path (Split-Path -Parent $PSScriptRoot) '.qt' }
    if (-not $RepoBase) { $RepoBase = $script:QtRepoDefault }

    $cands = @()
    if ($Explicit) { $cands += $Explicit }
    if ($env:CMAKE_EXE) { $cands += $env:CMAKE_EXE }
    $onPath = Get-CommandFile 'cmake'
    if ($onPath) { $cands += $onPath }
    foreach ($root in (Get-QtSearchRoots)) {
        foreach ($rel in @('Tools/CMake_64/bin/cmake.exe', 'Tools/CMake/bin/cmake.exe', 'CMake/bin/cmake.exe')) {
            $p = Join-Path $root $rel
            if (Test-FileExists $p) { $cands += $p }
        }
    }
    foreach ($d in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($d) { $cands += (Join-Path $d 'CMake/bin/cmake.exe') }
    }
    foreach ($c in $cands) {
        if ($c -and (Test-FileExists $c)) {
            $v = Get-CMakeVersion $c
            $major = 0; if ($v) { $major = [int]($v -split '\.')[0] }
            if ($major -eq 0 -or $major -ge 3) {
                return [pscustomobject]@{ Exe = (Resolve-Path $c).Path; Version = $v; Source = 'found' }
            }
        }
    }
    # 缓存里的（上次自动装的）
    $dst = Join-Path (Join-Path $CacheDir 'Tools') 'CMake_64'
    $exe = Find-FirstFile -Root $dst -Filter 'cmake.exe'
    if ($exe) { return [pscustomobject]@{ Exe = $exe; Version = (Get-CMakeVersion $exe); Source = 'cache' } }
    if ($Plan) {
        Write-QtWarn "计划：从 Qt 仓库自动获取 CMake → $dst"
        return [pscustomobject]@{ Exe = (Join-Path $dst 'bin/cmake.exe'); Version = ''; Source = 'plan-download' }
    }
    if (-not $AllowDownload -or $Offline) {
        throw "未找到 CMake。请安装 CMake（>= 3.19），或用 -CMakeExe 指定 cmake 可执行文件。"
    }
    Install-QtToolPackage -ToolDir 'tools_cmake' -PackagePattern 'qt.tools.cmake*' `
                          -Destination $dst -RepoBase $RepoBase -CacheDir $CacheDir -Transport $Transport
    $exe = Find-FirstFile -Root $dst -Filter 'cmake.exe'
    if (-not $exe) { throw "已下载 CMake 工具包但没找到 cmake.exe（$dst）" }
    return [pscustomobject]@{ Exe = $exe; Version = (Get-CMakeVersion $exe); Source = 'downloaded' }
}

function Get-GccVersion([string]$Gxx) {
    try {
        # ⚠ GCC 7+ 的 -dumpversion 只给主版本号，要完整版本得用 -dumpfullversion
        $out = (& $Gxx -dumpfullversion -dumpversion 2>$null | Select-Object -First 1)
        if ($out) { return $out.Trim() }
        $out = (& $Gxx -dumpversion 2>$null | Select-Object -First 1)
        if ($out) { return $out.Trim() }
    } catch { }
    return ''
}

# 只认目标三元组里带 mingw 的编译器 —— 否则会误抓 Android/其它交叉工具链的 g++
function Test-MingwCompiler([string]$Gxx) {
    try {
        $t = (& $Gxx -dumpmachine 2>$null | Select-Object -First 1)
        if ($t -and $t -match 'mingw') { return $t.Trim() }
    } catch { }
    return $null
}

function New-MingwResult([string]$Gxx, [string]$Source) {
    $full = (Resolve-Path $Gxx).Path
    return [pscustomobject]@{ Bin = (Split-Path -Parent $full); Gxx = $full
                              Version = (Get-GccVersion $full); Source = $Source }
}

function Resolve-MingwBin {
    param([string]$Explicit, [string]$CacheDir, [string]$RepoBase, [string]$Transport,
          [switch]$Offline, [switch]$AllowDownload = $true, [switch]$Plan)
    if (-not $CacheDir) { $CacheDir = Join-Path (Split-Path -Parent $PSScriptRoot) '.qt' }
    if (-not $RepoBase) { $RepoBase = $script:QtRepoDefault }
    $exeName = if ($env:OS -eq 'Windows_NT' -or -not $IsLinux) { 'g++.exe' } else { 'g++' }

    # ---- 快路径：显式 → PATH → 常见布局（都不递归大目录）----
    $cands = @()
    if ($Explicit) { $cands += (Join-Path $Explicit $exeName) }
    $onPath = Get-CommandFile 'g++'
    if ($onPath) { $cands += $onPath }
    foreach ($root in (Get-QtSearchRoots)) {
        $patterns = @(
            (Join-Path $root "bin/$exeName"),
            (Join-Path $root "mingw64/bin/$exeName"),
            (Join-Path $root "mingw32/bin/$exeName"),
            (Join-Path $root "Tools/mingw*/bin/$exeName"),
            (Join-Path $root "Tools/*/bin/$exeName"),
            (Join-Path $root "*/bin/$exeName"),
            (Join-Path $root "*/*/bin/$exeName")
        )
        foreach ($pat in $patterns) {
            foreach ($p in @(Get-ChildItem -Path $pat -File -ErrorAction SilentlyContinue)) { $cands += $p.FullName }
        }
    }
    foreach ($c in ($cands | Select-Object -Unique)) {
        if ($c -and (Test-FileExists $c) -and (Test-MingwCompiler $c)) { return (New-MingwResult $c 'found') }
    }
    # ---- 兜底：有界递归（深度 4），只认路径里带 mingw/w64 的 ----
    foreach ($root in (Get-QtSearchRoots)) {
        foreach ($p in @(Get-ChildItem -Path $root -Recurse -Depth 4 -Filter $exeName -File -ErrorAction SilentlyContinue)) {
            if ($p.FullName -notmatch 'mingw|w64') { continue }
            if (Test-MingwCompiler $p.FullName) { return (New-MingwResult $p.FullName 'found') }
        }
    }
    $dst = Join-Path (Join-Path $CacheDir 'Tools') 'mingw1310_64'
    $cached = Find-FirstFile -Root $dst -Filter $exeName
    if ($cached) { return (New-MingwResult $cached 'cache') }
    if ($Plan) {
        Write-QtWarn "计划：从 Qt 仓库自动获取 MinGW 工具链 → $dst"
        return [pscustomobject]@{ Bin = $dst; Gxx = (Join-Path $dst $exeName); Version = ''; Source = 'plan-download' }
    }
    if (-not $AllowDownload -or $Offline) {
        throw "未找到 MinGW-w64 的 g++。请安装 MinGW-w64，或用 -MingwBin 指定其 bin 目录。"
    }
    Install-QtToolPackage -ToolDir 'tools_mingw1310' -PackagePattern 'qt.tools.win64_mingw1310' `
                          -Destination $dst -RepoBase $RepoBase -CacheDir $CacheDir -Transport $Transport
    $cached = Find-FirstFile -Root $dst -Filter $exeName
    if (-not $cached) { throw "已下载 MinGW 工具包但没找到 g++（$dst）" }
    return (New-MingwResult $cached 'downloaded')
}

function Resolve-NinjaExe {
    param([string]$Explicit, [string]$CacheDir, [string]$RepoBase, [string]$Transport,
          [switch]$Offline, [switch]$AllowDownload = $true, [switch]$Plan)
    if (-not $CacheDir) { $CacheDir = Join-Path (Split-Path -Parent $PSScriptRoot) '.qt' }
    if (-not $RepoBase) { $RepoBase = $script:QtRepoDefault }
    $name = if ($env:OS -eq 'Windows_NT' -or $PSVersionTable.Platform -ne 'Unix') { 'ninja.exe' } else { 'ninja' }
    $cands = @()
    if ($Explicit) { $cands += $Explicit }
    $onPath = Get-CommandFile 'ninja'
    if ($onPath) { $cands += $onPath }
    foreach ($root in (Get-QtSearchRoots)) {
        foreach ($rel in @('Ninja/ninja.exe', 'Tools/Ninja/ninja.exe')) {
            $p = Join-Path $root $rel
            if (Test-FileExists $p) { $cands += $p }
        }
    }
    foreach ($c in $cands) { if ($c -and (Test-FileExists $c)) { return (Resolve-Path $c).Path } }
    $cached = Find-FirstFile -Root (Join-Path (Join-Path $CacheDir 'Tools') 'Ninja') -Filter $name
    if ($cached) { return $cached }
    if (-not $AllowDownload -or $Offline -or $Plan) { return $null }
    $dst = Join-Path (Join-Path $CacheDir 'Tools') 'Ninja'
    Install-QtToolPackage -ToolDir 'tools_ninja' -PackagePattern 'qt.tools.ninja' `
                          -Destination $dst -RepoBase $RepoBase -CacheDir $CacheDir -Transport $Transport
    return (Find-FirstFile -Root $dst -Filter $name)
}

# ---- 对外：LGPL 合规用的 Qt 源码（可选，-Deploy 时默认拉取）------------------
function Get-QtSourceArchives {
    param([string]$Version, [string[]]$Modules, [string]$CacheDir,
          [string]$OfficialBase, [string]$Transport, [switch]$Offline)
    if (-not $OfficialBase) { $OfficialBase = $script:QtOfficialDefault }
    if (-not $CacheDir) { $CacheDir = Join-Path (Split-Path -Parent $PSScriptRoot) '.qt' }
    if (-not $Modules -or $Modules.Count -eq 0) { $Modules = $script:QtDefaultModules }
    $parts = $Version -split '\.'
    $series = "$($parts[0]).$($parts[1])"
    $dest = Join-Path (Join-Path $CacheDir 'src') $Version
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $files = @()
    foreach ($m in $Modules) {
        $name = "$m-everywhere-src-$Version.tar.xz"
        $url = "$OfficialBase/$series/$Version/submodules/$name"
        $sha = ''
        try {
            $shaTmp = Join-Path (Join-Path $CacheDir '_meta') "$name.sha256"
            Invoke-Fetch -Url "$url.sha256" -OutFile $shaTmp -Transport $Transport -Quiet | Out-Null
            $sha = ((Get-Content -LiteralPath $shaTmp -Raw).Trim() -split '\s+')[0].ToLower()
        } catch { $sha = '' }
        $local = Join-Path $dest $name
        if (Test-FileExists $local -and $sha) {
            try { Assert-ContentHash $local 'SHA256' $sha; $files += $local; continue } catch { }
        }
        if ($Offline) { throw "离线模式且缺少 Qt 源码包 $name（预期在 $dest）" }
        Write-QtInfo "下载 Qt 源码 $name"
        Invoke-Fetch -Url $url -OutFile $local -Sha256 $sha -Transport $Transport | Out-Null
        $files += $local
    }
    return $files
}
