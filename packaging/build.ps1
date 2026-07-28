# Build the Windows frontend, portable archive, and installer.
# Usage: powershell -ExecutionPolicy Bypass -File packaging\build.ps1
param([string]$Version = "1.0.0")

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$release = Join-Path $root "release"

Write-Host "[1/4] Building console..." -ForegroundColor Cyan
Push-Location (Join-Path $root "console")
try {
    if (-not (Test-Path "node_modules")) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE." }
    }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}

Write-Host "[2/4] Building Ying.exe..." -ForegroundColor Cyan
Push-Location $root
try {
    python -m pip install --quiet pyinstaller pywebview pystray pillow
    if ($LASTEXITCODE -ne 0) { throw "Dependency install failed with exit code $LASTEXITCODE." }
    python -m PyInstaller packaging\gateway.spec --noconfirm --distpath dist --workpath build
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path $release | Out-Null
$portable = Join-Path $release "Ying-portable.zip"
for ($attempt = 1; $attempt -le 5; $attempt++) {
    try {
        Compress-Archive -Path (Join-Path $root "dist\Ying\*") -DestinationPath $portable -Force
        break
    } catch {
        if ($attempt -eq 5) { throw }
        Start-Sleep -Milliseconds (500 * $attempt)
    }
}

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
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE." }
$installer = Join-Path $release "Ying-Setup-$Version.exe"
Copy-Item (Join-Path $PSScriptRoot "output\Ying-Setup-$Version.exe") $installer -Force

Write-Host "[4/4] Writing checksums..." -ForegroundColor Cyan
$artifacts = @($installer, $portable)
$checksums = $artifacts | ForEach-Object {
    "$((Get-FileHash $_ -Algorithm SHA256).Hash.ToLowerInvariant())  $(Split-Path $_ -Leaf)"
}
$checksums | Set-Content (Join-Path $release "SHA256SUMS.txt") -Encoding ascii
Write-Host "Release artifacts are in $release" -ForegroundColor Green
