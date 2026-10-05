# Build the Windows one-dir app and the per-user installer.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Sign] [-SkipInstaller]
#
# Chain: deps -> ffmpeg -> vite build -> PyInstaller -> selftest against the freeze ->
# secret scan -> Inno -> (optionally) Authenticode signing -> sha256.
#
# Keep this file ASCII. Windows PowerShell 5.1 reads a script without a BOM as the ANSI
# code page, so a UTF-8 em dash arrives as three characters, one of them a closing quote,
# and the whole script fails to parse (the first build on machine A, 2026-09-23).
param(
    # Sign upshot.exe and Setup with the code-signing certificate in Cert:\CurrentUser\My
    # whose subject is $CertSubject. The private key never leaves this machine's store.
    [switch]$Sign,
    [string]$CertSubject = "CN=Upshot test signing",
    [string]$TimestampServer = "http://timestamp.digicert.com",
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 redraws Invoke-WebRequest's progress bar for every chunk, which
# made the 100 MB ffmpeg download take most of an hour on machine A.
$ProgressPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# A native command's non-zero exit does not stop a 5.1 script; every step checks it.
function Assert-Exit([string]$what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" }
}

Write-Host "== dependencies =="
uv sync --frozen
Assert-Exit "uv sync"

Write-Host "== version =="
# One version for the app, the installer and the file name: pyproject's, plus the commit,
# written where the freeze bundles it (app/build_info.json, not committed).
$version = (uv run python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()
Assert-Exit "reading the version"
$commit = (git rev-parse --short=12 HEAD).Trim()
Assert-Exit "git rev-parse"
$built = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$info = [ordered]@{ version = $version; commit = $commit; built = $built }
# The official build's Sentry DSNs (D87), from the gitignored packaging\release.local.json.
# A DSN only lets the app send crash reports and feedback, so it ships; it stays out of
# the repository so that a build from source has none and sends nothing. The secrets it
# lists (the Sentry auth token, the update signing keys) are never read into the build:
# only the publish step uses them, and the scan below refuses a build that carries one.
$releaseLocal = Join-Path $PSScriptRoot "release.local.json"
$secretFiles = @()
if (Test-Path $releaseLocal) {
    $release = Get-Content -Raw -Encoding UTF8 $releaseLocal | ConvertFrom-Json
    $info.sentry = [ordered]@{ dsn = $release.sentry.desktop.dsn; frontend_dsn = $release.sentry.frontend.dsn }
    $secretFiles = @($release.secret_files | Where-Object { $_ })
    Write-Host "reports: on (DSNs from packaging\release.local.json)"
} elseif ($Sign) {
    Write-Warning "packaging\release.local.json is missing: this signed build will send no crash reports or feedback"
} else {
    Write-Host "reports: off (no packaging\release.local.json)"
}
[System.IO.File]::WriteAllText((Join-Path $root "app\build_info.json"), ($info | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))
Write-Host "version $version, commit $commit"

Write-Host "== terms =="
# The installer's licence page, the website and the app all render app\legal\terms.md
# (D83). A Terms edit that was not rendered would ship one text in the installer and
# another on the website, so the build refuses it.
uv run python scripts/build_legal.py --check
Assert-Exit "the rendered Terms are out of date: run scripts/build_legal.py"
$termsVersion = (uv run python -c "from app.legal.terms import bundled; print(bundled().version)").Trim()
Assert-Exit "reading the Terms version"
Write-Host "terms $termsVersion"

Write-Host "== ffmpeg =="
# An LGPL build (D84): Upshot only decodes imported files to WAV, which needs nothing from
# FFmpeg's GPL-only parts (x264, x265 and similar encoders), and the LGPL's obligations are
# lighter. The marker names the build the cached ffmpeg.exe came from, so a cache filled
# by an earlier, different build is replaced instead of shipped.
$ffmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-lgpl-8.1.zip"
$ffmpeg = Join-Path $root "vendor\ffmpeg.exe"
$ffmpegMarker = Join-Path $root "vendor\ffmpeg.source.txt"
$ffmpegLicence = Join-Path $root "vendor\ffmpeg-LICENSE.txt"
$cachedFrom = if (Test-Path $ffmpegMarker) { (Get-Content $ffmpegMarker -Raw).Trim() } else { "" }
if (-not (Test-Path $ffmpeg) -or $cachedFrom -ne $ffmpegUrl) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root "vendor") | Out-Null
    $zip = Join-Path $env:TEMP "ffmpeg-lgpl.zip"
    $unpacked = Join-Path $env:TEMP "ffmpeg-lgpl"
    if (Test-Path $unpacked) { Remove-Item -Recurse -Force $unpacked }
    Invoke-WebRequest -UseBasicParsing -Uri $ffmpegUrl -OutFile $zip
    Expand-Archive -Path $zip -DestinationPath $unpacked -Force
    $found = Get-ChildItem -Path $unpacked -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    Copy-Item $found.FullName $ffmpeg -Force
    $licence = Get-ChildItem -Path $unpacked -Recurse -Filter LICENSE.txt | Select-Object -First 1
    if ($licence) { Copy-Item $licence.FullName $ffmpegLicence -Force }
    [System.IO.File]::WriteAllText($ffmpegMarker, $ffmpegUrl)
}
# Whatever the cache says, refuse to ship a GPL build.
$ffmpegConfig = (& $ffmpeg -hide_banner -version) -join " "
if ($ffmpegConfig -match "--enable-gpl") { throw "vendor\ffmpeg.exe is a GPL build; delete it and build again" }
Write-Host "ffmpeg: LGPL build from $ffmpegUrl"

Write-Host "== frontend =="
Push-Location (Join-Path $root "frontend")
npm ci
Assert-Exit "npm ci"
npm run build
Assert-Exit "npm run build"
Pop-Location

Write-Host "== third-party notices =="
# Every component the app ships and its licence, from what is installed here (the
# Windows-only packages included); the spec bundles it beside upshot.exe.
uv run python scripts/third_party_notices.py vendor\THIRD-PARTY-NOTICES.txt
Assert-Exit "third-party notices"

Write-Host "== PyInstaller =="
# --noconfirm replaces dist\upshot; nothing is removed by hand.
uv run pyinstaller --noconfirm --clean packaging\upshot.spec
Assert-Exit "PyInstaller"

Write-Host "== selftest against the freeze =="
# upshot.exe is a windowed program (console=False): `& upshot.exe` returns at once and
# prints nothing. Wait for it, and read the verdict from the report file it writes.
$exe = Join-Path $root "dist\upshot\upshot.exe"
$reports = Join-Path $root "dist\selftest"
New-Item -ItemType Directory -Force -Path $reports | Out-Null
# A home of its own: the build machine may be someone's real Upshot, and the selftest
# must not write into %LOCALAPPDATA%\upshot. The child inherits this environment.
$savedHome = $env:UP_HOME
$env:UP_HOME = Join-Path $reports ("home-" + (Get-Date).ToString("yyyyMMddHHmmss"))
try {
    foreach ($suite in @("imports", "pipeline")) {
        $report = Join-Path $reports "$suite.json"
        if (Test-Path $report) { Move-Item -Force $report "$report.previous" }
        $p = Start-Process -FilePath $exe -ArgumentList @("--selftest", $suite, "--report", "`"$report`"", "--quiet") -Wait -PassThru
        # No report is a failure whatever the exit code: the first freeze exited 0 without
        # running anything at all.
        if (-not (Test-Path $report)) { throw "the frozen build wrote no report for --selftest $suite (exit $($p.ExitCode))" }
        $result = Get-Content -Raw -Encoding UTF8 $report | ConvertFrom-Json
        foreach ($check in $result.checks) {
            $mark = if ($check.ok) { "ok  " } else { "FAIL" }
            Write-Host "[$mark] $($check.name): $($check.detail)"
        }
        if ($p.ExitCode -ne 0 -or -not $result.ok) { throw "the frozen build failed --selftest $suite (exit $($p.ExitCode), report $report)" }
    }
} finally {
    $env:UP_HOME = $savedHome
}

Write-Host "== no secrets in the build =="
# Neither the Sentry auth token nor an update signing key may ever ship (D87). The scan
# covers what went into the freeze as well as what came out: PyInstaller compresses
# Python modules, so a secret in a .py file is only visible in app\.
$scanArgs = @("app", "frontend\dist", "vendor", "dist\upshot")
foreach ($f in $secretFiles) { $scanArgs += @("--secret-file", $f) }
uv run python scripts/scan_secrets.py @scanArgs
Assert-Exit "the secret scan (a secret is in the build)"

$signer = Join-Path $PSScriptRoot "sign.ps1"
function Sign-File([string]$path) {
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File $signer $path -CertSubject $CertSubject -TimestampServer $TimestampServer
    Assert-Exit "signing $path"
}

if ($Sign) {
    Write-Host "== sign the app =="
    Sign-File $exe
}

if ($SkipInstaller) {
    Write-Host "== done (no installer) =="
    return
}

Write-Host "== installer =="
# A machine-wide install lands in Program Files (x86); winget's default per-user one in LOCALAPPDATA.
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found (Program Files (x86) or LOCALAPPDATA\Programs)" }
$isccArgs = @("/DAppVersion=$version", "/DAppCommit=$commit", "/DTermsVersion=$termsVersion")
if ($Sign) {
    # Inno signs the uninstaller it generates (and Setup itself) through this tool; $f is
    # the file, $q a double quote.
    $isccArgs += "/DSign"
    $isccArgs += "/Supshot=powershell.exe -NoProfile -ExecutionPolicy Bypass -File `$q$signer`$q `$f -CertSubject `$q$CertSubject`$q"
}
& $iscc @isccArgs packaging\installer.iss
Assert-Exit "Inno Setup"
$setup = Join-Path $root "dist\Upshot-$version-Setup.exe"
$hash = (Get-FileHash -Algorithm SHA256 $setup).Hash.ToLower()
[System.IO.File]::WriteAllText("$setup.sha256", "$hash  Upshot-$version-Setup.exe`n", (New-Object System.Text.UTF8Encoding $false))
Write-Host "$setup"
Write-Host "sha256 $hash"

Write-Host "== done =="
