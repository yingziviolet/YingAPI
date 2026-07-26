# 一键构建 Windows 单机版:前端 -> PyInstaller -> Inno Setup
# 用法:powershell -ExecutionPolicy Bypass -File packaging\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "[1/3] 构建控制台前端…" -ForegroundColor Cyan
Push-Location (Join-Path $root "console")
if (-not (Test-Path "node_modules")) { npm ci }
npm run build
Pop-Location

Write-Host "[2/3] PyInstaller 打包…" -ForegroundColor Cyan
Push-Location $root
python -m pip install --quiet pyinstaller pystray pillow
python -m PyInstaller packaging\gateway.spec --noconfirm --distpath dist --workpath build
Pop-Location

Write-Host "[3/3] 生成安装包(需要 Inno Setup 的 iscc)…" -ForegroundColor Cyan
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($iscc) {
    & $iscc (Join-Path $PSScriptRoot "installer.iss")
    Write-Host "安装包已生成:packaging\output\" -ForegroundColor Green
} else {
    Write-Host "未找到 Inno Setup(iscc)。免安装版已在 dist\LLMGateway\ 可直接运行;" -ForegroundColor Yellow
    Write-Host "需要安装包请安装 Inno Setup 6 后重跑本脚本。" -ForegroundColor Yellow
}
