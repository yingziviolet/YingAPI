# Build the Windows frontend, portable archive, and installer.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\build.ps1
param([string]$Version = "1.0.0")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$release = Join-Path $root "release"

Write-Host "[1/4] Building console..." -ForegroundColor Cyan
Push-Location (Join-Path $root "console")
try {
    if (-not (Test-Path "node_modules")) { npm ci }
    npm run build
} finally {
    Pop-Location
}

Write-Host "[2/4] Building Ying.exe..." -ForegroundColor Cyan
Push-Location $root
try {
    python -m pip install --quiet pyinstaller pywebview pystray pillow
    python -m PyInstaller packaging\gateway.spec --noconfirm --distpath dist --workpath build
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $release | Out-Null
$portable = Join-Path $release "Ying-portable.zip"
Compress-Archive -Path (Join-Path $root "dist\Ying\*") -DestinationPath $portable -Force

Write-Host "[3/4] Building installer..." -ForegroundColor Cyan
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    throw "Inno Setup 6 was not found. Install it, then rerun this script."
}

& $iscc "/DAppVersion=$Version" (Join-Path $PSScriptRoot "installer.iss")
$installer = Join-Path $release "Ying-Setup-$Version.exe"
Copy-Item (Join-Path $PSScriptRoot "output\Ying-Setup-$Version.exe") $installer -Force

Write-Host "[4/4] Writing checksums..." -ForegroundColor Cyan
$artifacts = @($installer, $portable)
$checksums = $artifacts | ForEach-Object {
    "$(Get-FileHash $_ -Algorithm SHA256 | Select-Object -ExpandProperty Hash)  $(Split-Path $_ -Leaf)"
}
$checksums | Set-Content (Join-Path $release "SHA256SUMS.txt") -Encoding ascii
Write-Host "Release artifacts are in $release" -ForegroundColor Green
