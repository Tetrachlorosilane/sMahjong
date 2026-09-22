<#
  发布打包：把客户端 dist 与服务端产物打成 GitHub Release 用的两个 zip。

  用法：pwsh -File tools\package-release.ps1 [-Version 1.6.0]
  产物：release\sMahjong-client-v<版本>-win64.zip
        release\sMahjong-server-v<版本>.zip
        并在结尾打印两者的 SHA256（与 GitHub 资产上的 digest 对得上才算传对了）

  约定：
    - 客户端包 = `client\dist` 的全部内容 **去掉 `settings.json`** —— 那是本机设置（窗口位置、
      音量、材质包路径），发给别人只会覆盖他的配置。
    - 客户端包内自带 `licenses\`（Qt/FFmpeg 的 LGPLv3/GPLv3 文本），**不要删**：
      Qt 是动态链接分发，LGPLv3 要求随二进制提供许可文本与可替换性。
    - 服务端包 = jar + `build.sh`/`run.sh` + `docs/DEPLOY.md`，目标机只需要 JDK 17+。
    - 版本号优先取 `-Version`，否则读 `client\CMakeLists.txt` 的 `project(... VERSION x.y.z)`。
#>
param([string]$Version = '')

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'client\dist'
$serverJar = Join-Path $root 'server\build\mahjong-server.jar'
$releaseDir = Join-Path $root 'release'

function Resolve-Version {
    param([string]$Explicit)
    if ($Explicit -ne '') { return $Explicit }
    $cml = Get-Content (Join-Path $root 'client\CMakeLists.txt') -Raw
    $m = [regex]::Match($cml, 'project\(mahjong-client\s+VERSION\s+(\d+\.\d+\.\d+)')
    if (-not $m.Success) { throw 'client\CMakeLists.txt 里找不到 project(... VERSION x.y.z)，请用 -Version 指定' }
    return $m.Groups[1].Value
}

$ver = Resolve-Version $Version
Write-Host "==> 版本 $ver"

if (-not (Test-Path (Join-Path $dist 'mahjong-client.exe'))) {
    throw "client\dist 里没有 mahjong-client.exe —— 先跑 pwsh -File client\build.ps1 -Deploy"
}
if (-not (Test-Path $serverJar)) {
    throw "server\build\mahjong-server.jar 不存在 —— 先跑 pwsh -File server\build.ps1"
}

# 版本号与源码必须一致：包名写 v1.6.0 而程序自称 1.5.0 是最容易漏的一处
$mainCpp = Get-Content (Join-Path $root 'client\src\main.cpp') -Raw
if ($mainCpp -notmatch [regex]::Escape("setApplicationVersion(QStringLiteral(`"$ver`"))")) {
    throw "client\src\main.cpp 的 setApplicationVersion 不是 $ver —— 版本号两处要一起改"
}

New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
$clientStage = Join-Path $releaseDir 'stage-client'
$serverStage = Join-Path $releaseDir 'stage-server'
foreach ($dir in @($clientStage, $serverStage)) {
    Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

# ── 客户端 ────────────────────────────────────────────────────────────────
Copy-Item (Join-Path $dist '*') $clientStage -Recurse -Force
Remove-Item (Join-Path $clientStage 'settings.json') -Force -ErrorAction SilentlyContinue
$clientReadme = @"
立直麻将 客户端 v$ver（Windows x64，已自带 Qt 运行时，解压即用）

1. 双击 mahjong-client.exe（**不需要**安装 Qt，也不要把它从本目录挪走）。
2. 地址填 localhost:10086（本机开服）或服务器 IP；建房间时可勾「补 3 个机器人」一个人也能打。
3. licenses\ 是 Qt / FFmpeg / 字体的许可文本，请保留（LGPLv3 要求）。
   想换掉 Qt 的 DLL 是允许的 —— 这正是 LGPLv3 动态链接的要求，本程序不做任何签名校验。
4. 牌面（tiles\）、音效（sfx\）、文案（i18n\）、字体（fonts\）都是可替换素材：
   保持文件名不变、重启客户端即生效，无需重新编译。见 docs\THEME.md。
5. 本包不含 settings.json —— 那是上次运行留下的本机配置。

项目主页：https://github.com/Tetrachlorosilane/sMahjong
"@
Set-Content -Path (Join-Path $clientStage 'README.txt') -Value $clientReadme -Encoding UTF8

$clientZip = Join-Path $releaseDir "sMahjong-client-v$ver-win64.zip"
Remove-Item $clientZip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $clientStage '*') -DestinationPath $clientZip -CompressionLevel Optimal

# ── 服务端 ────────────────────────────────────────────────────────────────
# 结构（2026-09 重构）：**包内一层同名目录**，且带后台 start/stop/restart/status/update 脚本。
# 旧结构把 build.sh / run.sh / jar / README 平铺在 zip 根 —— 两个问题：
#   ① `build.sh` 在包里**没有源码**，根本跑不起来（误导）；
#   ② 解压即散落一地把文件扔进当前目录，没有版本目录可回滚。
# 现在 jar 是构建产物，所以包里**不带 build.sh**（要构建请克隆源码），换成运维脚本。
$serverRoot = Join-Path $serverStage "sMahjong-server-v$ver"
New-Item -ItemType Directory -Force -Path $serverRoot | Out-Null
Copy-Item $serverJar (Join-Path $serverRoot 'mahjong-server.jar') -Force
Copy-Item (Join-Path $root 'docs\DEPLOY.md') $serverRoot -Force
Copy-Item (Join-Path $root 'server\pack\*') $serverRoot -Force
Set-Content -Path (Join-Path $serverRoot 'VERSION') -Value $ver -Encoding UTF8 -NoNewline

$shellScripts = (Get-ChildItem (Join-Path $serverRoot '*.sh')).Name
if ($shellScripts.Count -lt 5) {
    throw "server\pack 下的脚本不全（期望 start/stop/restart/status/update，实际 $($shellScripts -join ','))"
}

$serverZip = Join-Path $releaseDir "sMahjong-server-v$ver.zip"
Remove-Item $serverZip -Force -ErrorAction SilentlyContinue
# ⚠ 不能用 Compress-Archive：它写的条目 versionMadeBy = 0（FAT），Linux 侧**忽略**权限位，
#   解压后 `.sh` 全是 0644 → `./start.sh` 报 Permission denied（实测 bsdtar 显示 -rw-rw-r--）。
#   所以服务端包交给 tools/make-zip.mjs 手写（versionMadeBy=Unix + externalAttrs 高位 = 0755）。
node (Join-Path $root 'tools\make-zip.mjs') $serverZip "sMahjong-server-v$ver" $serverRoot
if ($LASTEXITCODE -ne 0) { throw "make-zip.mjs 打包失败（退出码 $LASTEXITCODE）" }

foreach ($zip2 in @($clientZip, $serverZip)) {
    $item = Get-Item $zip2
    $hash = (Get-FileHash $zip2 -Algorithm SHA256).Hash.ToLower()
    $count = (tar -tf $zip2 | Measure-Object).Count
    Write-Host ("    {0}  {1:N0} 字节  {2} 条目  sha256={3}" -f $item.Name, $item.Length, $count, $hash)
}
Write-Host '==> 完成'
