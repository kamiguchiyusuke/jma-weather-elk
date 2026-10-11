<#
  Kibana のダッシュボードを、Kibana が無くても開ける静的な HTML と PDF に書き出す。

  使い方（PowerShell、test_jma フォルダで）:
    powershell -ExecutionPolicy Bypass -File elk\kibana\export_static.ps1

  出力:
    viewer\kibana_dashboard.html  … 画像を埋め込んだ1ファイル（どのPCでもブラウザで開ける）
    viewer\kibana_dashboard.pdf   … 縦に長い1ページの PDF

  前提: ELK が起動していること（WSL で docker compose up -d）。Microsoft Edge（Windows 標準）を使う。
  中身は書き出した時点のダッシュボードの選択状態（既定は 東京都・2025年）の画像で、操作はできない。
#>
param(
  [string]$DashboardId = "jma-weather",
  [string]$OutDir = (Join-Path $PSScriptRoot "..\..\viewer"),
  [int]$Width = 1500,          # 画面の幅（px）
  [int]$Height = 4200,         # 画面の高さ（px）。ダッシュボード全体が入る大きさにする。余白は自動で切る
  [int]$Scale = 2,             # 解像度の倍率（2 なら PDF を拡大しても文字がつぶれにくい）
  [int]$WaitMs = 60000         # グラフの描画を待つ時間（ミリ秒）
)
$ErrorActionPreference = "Stop"

$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw "Microsoft Edge が見つかりません" }
try { Invoke-WebRequest -UseBasicParsing "http://localhost:5601/api/status" -TimeoutSec 10 | Out-Null }
catch { throw "Kibana（http://localhost:5601）に接続できません。WSL で ELK を起動してください" }

$OutDir = (New-Item -ItemType Directory -Force $OutDir).FullName
$work = Join-Path $env:TEMP "jma_kibana_export"
New-Item -ItemType Directory -Force $work | Out-Null
$rawPng = Join-Path $work "raw.png"
$html = Join-Path $OutDir "kibana_dashboard.html"
$pdf = Join-Path $OutDir "kibana_dashboard.pdf"

function Invoke-Edge([string[]]$EdgeArgs) {
  $common = @("--headless=new", "--disable-gpu", "--hide-scrollbars", "--user-data-dir=`"$work\profile`"")
  Start-Process -FilePath $edge -ArgumentList ($common + $EdgeArgs) -Wait -WindowStyle Hidden
}

# 1. Kibana のメニューを隠した表示（embed=true）で、ダッシュボード全体を画像にする
Write-Host "ダッシュボードを撮影中（約1分）..."
$url = "http://localhost:5601/app/dashboards#/view/${DashboardId}?embed=true&_g=(time:(from:'1975-12-31T15:00:00.000Z',to:'2025-12-31T15:00:00.000Z'))"
Remove-Item $rawPng -ErrorAction SilentlyContinue
Invoke-Edge @("--window-size=$Width,$Height", "--force-device-scale-factor=$Scale",
              "--virtual-time-budget=$WaitMs", "--screenshot=`"$rawPng`"", "`"$url`"")
if (-not (Test-Path $rawPng)) { throw "撮影に失敗しました" }

# 2. 下の余白（背景色だけの行）を切り落とす
Add-Type -AssemblyName System.Drawing
$img = [System.Drawing.Bitmap]::FromFile($rawPng)
$bg = $img.GetPixel(2, $img.Height - 2).ToArgb()
$bottom = $img.Height - 1
:scan for ($y = $img.Height - 1; $y -ge 0; $y--) {
  for ($x = 0; $x -lt $img.Width; $x += 4) {
    if ($img.GetPixel($x, $y).ToArgb() -ne $bg) { $bottom = $y; break scan }
  }
}
$h = [Math]::Min($img.Height, $bottom + 16 * $Scale)
$cropped = $img.Clone((New-Object System.Drawing.Rectangle 0, 0, $img.Width, $h), $img.PixelFormat)
$ms = New-Object System.IO.MemoryStream
$cropped.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
$bgHex = "#{0:x6}" -f ($bg -band 0xFFFFFF)
$img.Dispose(); $cropped.Dispose()

# 3. 画像を埋め込んだ1ファイルの HTML を作る（@page で PDF を1ページにする）
$cssW = $Width; $cssH = [Math]::Ceiling($h / $Scale)
$b64 = [Convert]::ToBase64String($ms.ToArray())
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"
$page = @"
<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>都道府県の気象データ（Kibana スナップショット）</title>
<!-- Kibana のダッシュボード「都道府県の気象データ」を $stamp に書き出した静的な画像 -->
<style>
@page { size: ${cssW}px ${cssH}px; margin: 0; }
html, body { margin: 0; background: $bgHex; }
img { display: block; width: 100%; max-width: ${cssW}px; height: auto; margin: 0 auto; }
</style>
</head>
<body>
<img alt="Kibana ダッシュボード「都道府県の気象データ」（$stamp 時点）" src="data:image/png;base64,$b64">
</body>
</html>
"@
[System.IO.File]::WriteAllText($html, $page, (New-Object System.Text.UTF8Encoding $false))

# 4. その HTML を PDF にする
Write-Host "PDF を作成中..."
Remove-Item $pdf -ErrorAction SilentlyContinue
$fileUrl = "file:///" + ($html -replace '\\', '/')
Invoke-Edge @("--no-pdf-header-footer", "--print-to-pdf=`"$pdf`"", "`"$fileUrl`"")
if (-not (Test-Path $pdf)) { throw "PDF の作成に失敗しました" }

"{0}（{1:N0} KB）" -f $html, ((Get-Item $html).Length / 1KB)
"{0}（{1:N0} KB）" -f $pdf, ((Get-Item $pdf).Length / 1KB)
