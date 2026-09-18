<#
  把 dsh-github 的 host 侧补丁（文件请求体 + uploads 资产上传）打到本地检出，
  重新转译，并安装到已加载的那个插件目录。

  用法：
    pwsh -File tools\dsh-github-patch\apply.ps1                  # 打补丁 → 转译 → 安装
    pwsh -File tools\dsh-github-patch\apply.ps1 -Check           # 只校验能否干净应用，不落盘
    pwsh -File tools\dsh-github-patch\apply.ps1 -Reverse         # 还原上游原文（不打补丁）
    pwsh -File tools\dsh-github-patch\apply.ps1 -PluginDir <插件检出目录> -InstallDir <已安装插件目录>

  打完/还原后插件进程里还是旧代码 —— 要重载才生效：
    dev_reload_package dsh-github

  为什么用自带的 apply-patch.mjs 而不是 git apply / GNU patch：见 README.md「为什么不用
  git apply」。为什么用 `tsc --noCheck`：上游 src 在本机工具链下**本来就有** 11 处
  `Property 'body'/'headers'/'status' does not exist on type 'Response'` 报错（与本补丁无关）。
#>
param(
    [string]$PluginDir = '',
    [string]$InstallDir = '',
    [switch]$Check,
    [switch]$Reverse
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$patch = Join-Path $here 'host-filebody-and-uploads.patch'
$applier = Join-Path $here 'apply-patch.mjs'
$marker = 'github_upload_release_asset'   # 补丁是否已在位的判据

foreach ($file in @($patch, $applier)) {
    if (-not (Test-Path $file)) { throw "缺少 $file" }
}

function Resolve-PluginDir {
    param([string]$Explicit)
    $candidates = @()
    if ($Explicit -ne '') { $candidates += $Explicit }
    if ($env:DSH_GITHUB_SRC) { $candidates += $env:DSH_GITHUB_SRC }
    $candidates += @(
        (Join-Path $env:USERPROFILE 'source\dsh-plugin\dsh-github'),
        (Join-Path $env:USERPROFILE 'source\gitclone\dsh-github'),
        (Join-Path $env:USERPROFILE '.dsh\plugins\dsh-github')
    )
    foreach ($dir in $candidates) {
        if ($dir -and (Test-Path (Join-Path $dir 'src\index.ts'))) { return (Resolve-Path $dir).Path }
    }
    throw '找不到 dsh-github 检出（含 src\index.ts）；用 -PluginDir 指定'
}

function Resolve-InstallDir {
    param([string]$Explicit)
    if ($Explicit -ne '') {
        if (-not (Test-Path $Explicit)) { throw "安装目录不存在：$Explicit" }
        return (Resolve-Path $Explicit).Path
    }
    $dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
    $dir = Join-Path $dshHome 'plugins\dsh-github'
    if (-not (Test-Path $dir)) { throw "找不到已安装的插件目录 $dir；用 -InstallDir 指定" }
    return (Resolve-Path $dir).Path
}

function Resolve-Tsc {
    $candidates = @()
    if ($env:DSH_TSC) { $candidates += $env:DSH_TSC }
    $candidates += @(
        (Join-Path $env:APPDATA 'npm\node_modules\typescript\bin\tsc'),
        (Join-Path $env:ProgramFiles 'nodejs\node_modules\typescript\bin\tsc')
    )
    foreach ($file in $candidates) {
        if ($file -and (Test-Path $file)) { return $file }
    }
    $cmd = Get-Command tsc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw '找不到 tsc；装一个 `npm i -g typescript`，或用 $env:DSH_TSC 指定'
}

$pluginDir = Resolve-PluginDir $PluginDir
$srcFile = Join-Path $pluginDir 'src\index.ts'
Write-Host "==> 插件检出 : $pluginDir"
Write-Host "==> 补丁     : $patch"
$patched = (Get-Content $srcFile -Raw).Contains($marker)

if ($Check) {
    $flag = if ($Reverse) { '--check --reverse' } else { '--check' }
    & node $applier $patch $srcFile @($flag -split ' ')
    if ($LASTEXITCODE -ne 0) { throw '补丁无法干净应用（上游大概改过这几行）' }
    return
}

if ($Reverse) {
    if (-not $patched) { Write-Host '==> 补丁不在位，无需还原'; return }
    & node $applier $patch $srcFile --reverse
    if ($LASTEXITCODE -ne 0) { throw '反向应用失败（src 已被改过？）' }
    Write-Host '==> 已还原上游原文（lib\index.js 需要重新构建才能同步）'
    return
}

if ($patched) {
    Write-Host '==> 补丁已在位，跳过打补丁（幂等）'
} else {
    Copy-Item $srcFile "$srcFile.pre-patch.bak" -Force
    & node $applier $patch $srcFile
    if ($LASTEXITCODE -ne 0) {
        Copy-Item "$srcFile.pre-patch.bak" $srcFile -Force
        throw '打补丁失败，已还原 src'
    }
    Write-Host '==> 补丁已应用（原文备份在 src\index.ts.pre-patch.bak）'
}

$tsc = Resolve-Tsc
$stage = Join-Path ([IO.Path]::GetTempPath()) ('dsh-github-emit-' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
    Write-Host "==> 转译（tsc --noCheck）-> $stage"
    # 单文件 + 显式选项：跳过类型检查（上游本就有 11 处无关报错），emit 与上游一致的 ESM
    & node $tsc --noCheck --target ES2023 --module NodeNext --moduleResolution NodeNext `
        --esModuleInterop --skipLibCheck --outDir $stage $srcFile
    if ($LASTEXITCODE -ne 0) { throw 'tsc 失败' }
    $emit = Join-Path $stage 'index.js'
    if (-not (Test-Path $emit)) { throw "没有产出 $emit" }
    $code = Get-Content $emit -Raw
    foreach ($symbol in @($marker, 'bodyFile', 'effectiveUploadsBase', 'readBodyFile', 'uploadsBase', 'maxUploadBytes')) {
        if (-not $code.Contains($symbol)) { throw "产物缺少 $symbol —— 补丁没生效" }
    }
    if ($code.Contains('require(')) { throw '产物是 CommonJS，插件需要 ESM' }

    $installDir = Resolve-InstallDir $InstallDir
    Write-Host "==> 安装到 : $installDir"
    foreach ($lib in @((Join-Path $pluginDir 'lib'), (Join-Path $installDir 'lib'))) {
        New-Item -ItemType Directory -Force -Path $lib | Out-Null
        Copy-Item $emit (Join-Path $lib 'index.js') -Force
        # 离线转译没有 source map，删掉旧的免得 Node 找到过期映射
        Remove-Item (Join-Path $lib 'index.js.map') -Force -ErrorAction SilentlyContinue
    }
} finally {
    Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
}

foreach ($file in @((Join-Path $pluginDir 'lib\index.js'), (Join-Path $installDir 'lib\index.js'))) {
    $item = Get-Item $file
    Write-Host ("    {0}  {1} 字节  sha256={2}" -f $file, $item.Length, (Get-FileHash $file -Algorithm SHA256).Hash.Substring(0, 32))
}
Write-Host '==> 完成。重载插件才生效：dev_reload_package dsh-github'
