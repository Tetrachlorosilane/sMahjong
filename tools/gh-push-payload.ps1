<#
  生成「无 git push」环境用的 GitHub REST 载荷（blobs / tree / commit / ref）。

  用法：pwsh -File tools\gh-push-payload.ps1        # 针对当前 HEAD 相对 HEAD^ 的改动
  产物：server\build\push\blob-<n>.json  tree.json  commit.json  map.txt（文件 → blob sha）

  为什么需要它：本机/沙箱环境下 **`git push` 走不通**（broker 到 github.com:443 被拦，
  `git push`/`ls-remote` 都失败），而 **api.github.com 是通的**。于是改走 Git Data API：
  `POST /git/blobs`（每个改动文件一个）→ `POST /git/trees`（带 `base_tree`，只列改动）
  → `POST /git/commits` → `PATCH /git/refs/heads/main`。

  为什么是「先生成载荷文件」而不是脚本自己调 API：
  1. **token 不落脚本**：token 由 dsh-github 插件注入，只在插件的 `github_request` 里生效；
     脚本只写本地文件，联网由 `github_request` 带着 `bodyFile` 发。
  2. 受限沙箱下 Node 拿不到子进程管道（EPERM），所以由 PowerShell（它能跑 git）生成清单。

  三条踩过的坑（都记在这里，别再踩）：
  1. **`bodyFile` 必须是绝对路径** —— 插件的相对路径不是相对工作区解析的（会报「不存在或不可读」）。
  2. **`PATCH /git/refs/...` 的 body 也要走文件**：内联的 JSON 字符串会被原样当字符串发出去
     （422 `is not an object`）。
  3. **提交对象的 message 必须带尾随换行**（`git cat-file commit HEAD` 里的原始字节），
     否则远端算出的 commit sha 与本地**不同**（内容一样但对象不一样）——
     本脚本直接从 commit 对象里取 message / author / committer，并在写文件前**自算一遍
     commit sha 与 `git rev-parse HEAD` 比对**，不一致就报错。
#>
param([string]$Range = 'HEAD^..HEAD')
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
$out = 'server\build\push'
Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $out | Out-Null

$head = (git rev-parse HEAD).Trim()
$raw = (git cat-file commit HEAD)
$sep = [Array]::IndexOf($raw, '')
$firstParent = (git rev-parse "$Range" 2>$null)
$parents = @($raw | Where-Object { $_ -like 'parent *' } | ForEach-Object { ($_ -replace '^parent ', '') })
$parentTree = (git rev-parse "$($parents[0])^{tree}").Trim()
$headTree = (git rev-parse 'HEAD^{tree}').Trim()
$files = @(git -c core.quotepath=false diff --name-only $Range)
if ($files.Count -eq 0) { throw "改动为空：$Range" }
Write-Host "head=$head parent=$($parents[0]) parentTree=$parentTree headTree=$headTree files=$($files.Count)"

function GitBlobSha([byte[]]$bytes) {
    $header = [Text.Encoding]::ASCII.GetBytes("blob $($bytes.Length)`0")
    $all = New-Object byte[] ($header.Length + $bytes.Length)
    [Array]::Copy($header, 0, $all, 0, $header.Length)
    [Array]::Copy($bytes, 0, $all, $header.Length, $bytes.Length)
    (([Security.Cryptography.SHA1]::Create().ComputeHash($all)) | ForEach-Object { $_.ToString('x2') }) -join ''
}

$entries = @(); $map = @(); $n = 0
foreach ($f in $files) {
    $bytes = [IO.File]::ReadAllBytes(($f -replace '/', '\'))
    $sha = GitBlobSha $bytes
    $want = (git rev-parse "HEAD:$f").Trim()
    if ($sha -ne $want) { throw "blob sha 不一致：$f 本地=$sha git=$want（autocrlf？）" }
    $payload = Join-Path $out "blob-$n.json"
    [IO.File]::WriteAllText($payload,
        (@{ content = [Convert]::ToBase64String($bytes); encoding = 'base64' } | ConvertTo-Json -Compress),
        (New-Object Text.UTF8Encoding($false)))
    $entries += [ordered]@{ path = $f; mode = '100644'; type = 'blob'; sha = $sha }
    $map += "$f`t$sha`t$payload`tPOST /repos/{owner}/{repo}/git/blobs"
    $n++
}
[IO.File]::WriteAllText((Join-Path $out 'map.txt'), ($map -join "`n"), (New-Object Text.UTF8Encoding($false)))

[IO.File]::WriteAllText((Join-Path $out 'tree.json'),
    ([ordered]@{ base_tree = $parentTree; tree = $entries } | ConvertTo-Json -Depth 6 -Compress),
    (New-Object Text.UTF8Encoding($false)))

# 提交对象：message 与 identity 全部取自本地对象（含尾随换行），先自算 sha 校验
$msg = (($raw[($sep + 1)..($raw.Count - 1)]) -join "`n") + "`n"
function Ident($prefix) {
    $s = (($raw | Where-Object { $_ -like "$prefix *" }) -replace "^$prefix ", '')
    $m = [regex]::Match($s, '^(.*) <(.*)> (\d+) ([+-])(\d{2})(\d{2})$')
    $span = New-TimeSpan -Hours ([int]$m.Groups[5].Value) -Minutes ([int]$m.Groups[6].Value)
    if ($m.Groups[4].Value -eq '-') { $span = -$span }
    $dt = [DateTimeOffset]::FromUnixTimeSeconds([long]$m.Groups[3].Value).ToOffset($span)
    [ordered]@{ name = $m.Groups[1].Value; email = $m.Groups[2].Value
                date = $dt.ToString("yyyy-MM-dd'T'HH:mm:sszzz") }
}
$a = Ident 'author'; $c = Ident 'committer'
# 自算校验：头部行**原样**取（含时区与 epoch，不做任何日期往返），只把 message 补回去
$authorRaw = ($raw | Where-Object { $_ -like 'author *' })
$committerRaw = ($raw | Where-Object { $_ -like 'committer *' })
$obj = "tree $headTree`n" + (($parents | ForEach-Object { "parent $_`n" }) -join '') +
       "$authorRaw`n$committerRaw`n`n$msg"
$bytes = [Text.Encoding]::UTF8.GetBytes($obj)
$hb = [Text.Encoding]::ASCII.GetBytes("commit $($bytes.Length)`0")
$all = New-Object byte[] ($hb.Length + $bytes.Length)
[Array]::Copy($hb, 0, $all, 0, $hb.Length); [Array]::Copy($bytes, 0, $all, $hb.Length, $bytes.Length)
$selfSha = ((([Security.Cryptography.SHA1]::Create().ComputeHash($all)) | ForEach-Object { $_.ToString('x2') }) -join '')
if ($selfSha -ne $head) { throw "commit sha 自算不一致：$selfSha vs $head" }
[IO.File]::WriteAllText((Join-Path $out 'commit.json'),
    ([ordered]@{ message = $msg; tree = $headTree; parents = $parents; author = $a; committer = $c }
        | ConvertTo-Json -Depth 6 -Compress),
    (New-Object Text.UTF8Encoding($false)))
[IO.File]::WriteAllText((Join-Path $out 'ref.json'),
    (@{ sha = $head; force = $false } | ConvertTo-Json -Compress),
    (New-Object Text.UTF8Encoding($false)))

Write-Host "commit sha 自算通过 = $head（远端应返回同一个 sha）"
Get-ChildItem $out | Select-Object Name, Length
