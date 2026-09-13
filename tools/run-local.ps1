# 本地联调：构建并启动服务端（Windows 开发用）
# 用法：pwsh -File tools\run-local.ps1 [-Port 10086] [-Selftest]

param(
    [int]$Port = 10086,
    [switch]$Selftest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$server = Join-Path $root "server"

Push-Location $server
try {
    pwsh -ExecutionPolicy Bypass -File .\build.ps1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "服务端构建失败" }

    if ($Selftest) {
        java -jar build\mahjong-server.jar --selftest
        exit $LASTEXITCODE
    }

    Write-Host "启动服务端 0.0.0.0:$Port  （Ctrl+C 停止）"
    Write-Host "客户端连接地址： 127.0.0.1:$Port"
    java -Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8 -jar build\mahjong-server.jar --host 0.0.0.0 --port $Port --verbose
} finally {
    Pop-Location
}
