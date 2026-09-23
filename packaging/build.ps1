# Build the Windows one-dir app and the per-user installer.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#
# Chain: deps -> ffmpeg -> vite build -> PyInstaller -> selftest against the freeze -> Inno.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "== dependencies =="
uv sync --frozen

Write-Host "== ffmpeg =="
$ffmpeg = Join-Path $root "vendor\ffmpeg.exe"
if (-not (Test-Path $ffmpeg)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root "vendor") | Out-Null
    $zip = Join-Path $env:TEMP "ffmpeg.zip"
    Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath (Join-Path $env:TEMP "ffmpeg") -Force
    $found = Get-ChildItem -Path (Join-Path $env:TEMP "ffmpeg") -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    Copy-Item $found.FullName $ffmpeg
}

Write-Host "== frontend =="
Push-Location (Join-Path $root "frontend")
npm ci
npm run build
Pop-Location

Write-Host "== PyInstaller =="
Remove-Item -Recurse -Force (Join-Path $root "dist\upshot") -ErrorAction SilentlyContinue
uv run pyinstaller --noconfirm --clean packaging\upshot.spec

Write-Host "== selftest against the freeze =="
$exe = Join-Path $root "dist\upshot\upshot.exe"
& $exe --selftest imports
if ($LASTEXITCODE -ne 0) { throw "the frozen build failed --selftest imports" }
& $exe --selftest pipeline
if ($LASTEXITCODE -ne 0) { throw "the frozen build failed --selftest pipeline" }

Write-Host "== installer =="
# A machine-wide install lands in Program Files (x86); winget's default per-user one in LOCALAPPDATA.
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc packaging\installer.iss
} else {
    Write-Warning "Inno Setup 6 not found (Program Files (x86) or LOCALAPPDATA\Programs) — skipping the installer"
}

Write-Host "== done =="
