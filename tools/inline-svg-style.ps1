# 把 SVG 里的 CSS 类（<style> + class="st0"）内联成表现属性（fill="#..." 等）。
#
# 为什么需要：Illustrator 导出的 SVG 把颜色写在 <style> 块里、用 class 引用，
# 而 **Qt 的 SVG 渲染器（SVG Tiny）不支持 CSS 类选择器** —— 直接渲染会丢掉所有颜色。
# 内联成属性后视觉完全不变，但任何渲染器都能正确显示。
#
# 用法: pwsh -File tools\inline-svg-style.ps1 [-Dir client\assets\tiles] [-Check]
param(
    [string]$Dir = "client\assets\tiles",
    [switch]$Check
)

$attrNames = @('fill', 'stroke', 'stroke-width', 'stroke-linejoin', 'stroke-linecap',
               'opacity', 'fill-rule', 'fill-opacity', 'stroke-opacity')

$files = Get-ChildItem -LiteralPath $Dir -Filter *.svg -ErrorAction Stop
$changed = 0
foreach ($f in $files) {
    $src = Get-Content -LiteralPath $f.FullName -Raw -Encoding UTF8
    if ($src -notmatch '<style') { continue }

    # 1) 收集所有 <style> 内容并解析出 类名 -> 属性表
    $css = ''
    foreach ($m in [regex]::Matches($src, '(?s)<style[^>]*>(.*?)</style>')) {
        $css += $m.Groups[1].Value + "`n"
    }
    $css = [regex]::Replace($css, '(?s)/\*.*?\*/', '')
    $map = @{}
    foreach ($m in [regex]::Matches($css, '(?s)\.([A-Za-z0-9_-]+)\s*\{([^}]*)\}')) {
        $cls = $m.Groups[1].Value
        if (-not $map.ContainsKey($cls)) { $map[$cls] = @{} }
        foreach ($d in ($m.Groups[2].Value -split ';')) {
            $i = $d.IndexOf(':')
            if ($i -lt 0) { continue }
            $k = $d.Substring(0, $i).Trim()
            $v = $d.Substring($i + 1).Trim()
            if ($k) { $map[$cls][$k] = $v }
        }
    }

    # 2) 去掉 <style> 与其所在的空 <defs>
    $out = [regex]::Replace($src, '(?s)<style[^>]*>.*?</style>\s*', '')
    $out = [regex]::Replace($out, '(?s)<defs>\s*</defs>\s*', '')

    # 3) class="a b" -> 合并后的表现属性
    $out = [regex]::Replace($out, '\sclass="([^"]*)"', {
            param($mm)
            $props = @{}
            foreach ($c in ($mm.Groups[1].Value -split '\s+')) {
                if ($c -and $map.ContainsKey($c)) {
                    foreach ($k in $map[$c].Keys) { $props[$k] = $map[$c][$k] }
                }
            }
            $s = ''
            foreach ($a in $attrNames) {
                if ($props.ContainsKey($a)) {
                    $val = ($props[$a] -replace 'px$', '').Trim()
                    $s += ' ' + $a + '="' + $val + '"'
                }
            }
            return $s
        })

    if ($out -eq $src) { continue }
    $changed++
    if ($Check) {
        Write-Host "  需要转换: $($f.Name)（$($map.Keys.Count) 个类）"
        continue
    }
    $bakDir = Join-Path (Split-Path $Dir -Parent) "tiles-replaced\pre-inline"
    New-Item -ItemType Directory -Force -Path $bakDir | Out-Null
    Copy-Item -LiteralPath $f.FullName -Destination (Join-Path $bakDir $f.Name) -Force
    Set-Content -LiteralPath $f.FullName -Value $out -NoNewline -Encoding UTF8
}

Write-Host "[inline-style] 扫描 $($files.Count) 个，$(if ($Check) { '需转换' } else { '已转换' }) $changed 个"
if (-not $Check -and $changed -gt 0) {
    Write-Host "[inline-style] 原件已备份到 tiles-replaced\pre-inline\"
}
