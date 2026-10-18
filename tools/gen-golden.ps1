# 构建期生成三端对拍的 golden 夹具（G1 v4 / G2 a-）。见 server/tests/golden/README.md。
# 需要 python/.venv（torch）；⛔ 产物是二进制、不进仓库（缺了 Java --selftest 会打印"跳过"）。
param(
    [string]$Trace = "S:\mahjong-training\raw\v4-wl-p301-A-g01",
    [int]$Cases = 11,
    [int]$ScanFiles = 5
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root "python\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "找不到 $py（先 uv sync / 装依赖）" }
if (-not (Test-Path (Join-Path $root $Trace))) {
    if (-not (Test-Path $Trace)) { throw "找不到轨迹目录：$Trace" }
}
$env:PYTHONPATH = Join-Path $root "python"
$outDir = Join-Path $root "server\tests\golden"
New-Item -ItemType Directory -Force $outDir | Out-Null

& $py -m mahjong_ml.v4.export golden --trace $Trace --out (Join-Path $outDir "forward-v4.bin") `
    --cases $Cases --scan-files $ScanFiles --fixture-semantics current
if ($LASTEXITCODE -ne 0) { throw "G1 生成失败（exit $LASTEXITCODE）" }
& $py -m mahjong_ml.v4.export golden --trace $Trace --out (Join-Path $outDir "forward-a.bin") `
    --cases $Cases --scan-files $ScanFiles --fixture-semantics a-
if ($LASTEXITCODE -ne 0) { throw "G2 生成失败（exit $LASTEXITCODE）" }
Get-ChildItem $outDir -Filter *.bin | Select-Object Name, Length
