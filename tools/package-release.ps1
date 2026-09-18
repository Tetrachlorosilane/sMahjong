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
Copy-Item $serverJar (Join-Path $serverStage 'mahjong-server.jar') -Force
Copy-Item (Join-Path $root 'server\build.sh') $serverStage -Force
Copy-Item (Join-Path $root 'server\run.sh') $serverStage -Force
Copy-Item (Join-Path $root 'docs\DEPLOY.md') $serverStage -Force
$serverReadme = @"
立直麻将 服务端 v$ver（目标机只需要 JDK 17+，零第三方依赖）

  ./build.sh && ./run.sh          # 启动后打印 LISTENING 0.0.0.0:10086
  java -jar mahjong-server.jar --selftest    # 规则引擎回归（期望 SELFTEST PASS）

部署（systemd / 防火墙 / WSL 端口转发）见 DEPLOY.md。
客户端下载：https://github.com/Tetrachlorosilane/sMahjong/releases
"@
Set-Content -Path (Join-Path $serverStage 'README.txt') -Value $serverReadme -Encoding UTF8

$serverZip = Join-Path $releaseDir "sMahjong-server-v$ver.zip"
Remove-Item $serverZip -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $serverStage '*') -DestinationPath $serverZip -CompressionLevel Optimal

foreach ($zip in @($clientZip, $serverZip)) {
    $item = Get-Item $zip
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    $count = (tar -tf $zip | Measure-Object).Count
    Write-Host ("    {0}  {1:N0} 字节  {2} 条目  sha256={3}" -f $item.Name, $item.Length, $count, $hash)
}
Write-Host '==> 完成'
