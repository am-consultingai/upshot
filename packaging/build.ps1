# Build the Windows one-dir app and the per-user installer.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 [-Sign [-NoReports]] [-SkipInstaller]
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
    # A signed build is the one users get, so it refuses to build without the release
    # settings (no DSNs means no crash reports and no feedback). -NoReports says that a
    # signed build without them is meant.
    [switch]$NoReports,
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
# Windows PowerShell 5.1 redraws Invoke-WebRequest's progress bar for every chunk, which
# once made a 100 MB download (BtbN's ffmpeg) take most of an hour on machine A.
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
# The official build's Sentry DSNs (D87), from the release settings: the file that
# UPSHOT_RELEASE_SETTINGS in this clone's .env names (gitignored; .env.example lists the keys),
# else a gitignored packaging\release.local.json in this clone. A DSN only lets the
# app send crash reports and feedback, so it ships; it stays out of the repository so
# that a build from source has none and sends nothing. The secrets it lists (the Sentry
# auth token, the update signing keys) are never read into the build: only the publish
# step uses them, and the scan below refuses a build that carries one.
#
# Each build clone used to need its own copy, and a clone without one built a signed
# installer with reports off behind a warning nobody read (machine A, 2026-10-08).
function Read-DotEnv([string]$path) {
    $values = @{}
    if (-not (Test-Path -LiteralPath $path)) { return $values }
    foreach ($raw in Get-Content -LiteralPath $path -Encoding UTF8) {  # UTF8 also takes a BOM
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith('#')) { continue }
        $key, $value = $line -split '=', 2
        if ($null -eq $value) { throw "$path`: expected KEY=value, got '$line'" }
        $value = $value.Trim()
        if ($value.Length -ge 2 -and $value[0] -eq $value[-1] -and '"', "'" -contains [string]$value[0]) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$key.Trim()] = $value
    }
    return $values
}
$dotEnv = Read-DotEnv (Join-Path $root ".env")
if ($dotEnv['UPSHOT_RELEASE_SETTINGS']) {
    $releaseLocal = $dotEnv['UPSHOT_RELEASE_SETTINGS']
    if (-not (Test-Path -LiteralPath $releaseLocal)) { throw "UPSHOT_RELEASE_SETTINGS in .env names $releaseLocal, which cannot be read" }
} else {
    $releaseLocal = Join-Path $PSScriptRoot "release.local.json"
}
$secretFiles = @()
if ($NoReports) {
    Write-Host "reports: off (-NoReports)"
} elseif (Test-Path -LiteralPath $releaseLocal) {
    $release = Get-Content -Raw -Encoding UTF8 -LiteralPath $releaseLocal | ConvertFrom-Json
    if (-not $release.sentry.desktop.dsn -or -not $release.sentry.frontend.dsn) { throw "$releaseLocal has no DSNs" }
    $info.sentry = [ordered]@{ dsn = $release.sentry.desktop.dsn; frontend_dsn = $release.sentry.frontend.dsn }
    $secretFiles = @($release.secret_files | Where-Object { $_ })
    # The scan looks for each secret's own bytes only where it can read the file; one it
    # cannot read is skipped. A signed build must have at least one of them to look for.
    if ($Sign -and -not ($secretFiles | Where-Object { Test-Path $_ })) {
        throw "none of the secret_files in $releaseLocal can be read here: the scan could not look for them"
    }
    Write-Host "reports: on (DSNs from $releaseLocal)"
} elseif ($Sign) {
    throw ("no release settings: set UPSHOT_RELEASE_SETTINGS in .env or add packaging\release.local.json " +
        "(see the release runbook), or pass -NoReports for a signed build that sends nothing")
} else {
    Write-Host "reports: off (no release settings)"
}
[System.IO.File]::WriteAllText((Join-Path $root "app\build_info.json"), ($info | ConvertTo-Json), (New-Object System.Text.UTF8Encoding $false))
Write-Host "version $version, commit $commit"
# One version everywhere (D87, A1): the front end's package.json must say the same, or
# the release, its manifest and its crash reports would disagree about what this is.
$frontendVersion = (Get-Content -Raw -Encoding UTF8 (Join-Path $root "frontend\package.json") | ConvertFrom-Json).version
if ($frontendVersion -ne $version) { throw "frontend\package.json says $frontendVersion, pyproject.toml says $version" }

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
# Upshot's own audio-only build (licence epic S3): FFmpeg's LGPL audio decoders, the
# demuxers ingest whitelists and zlib, built by packaging\ffmpeg\build.sh in the ffmpeg
# workflow and published, with its sources, as a pre-release. Measured against the BtbN
# build it replaced on 175 real files: the same files convert, every sample within 1 LSB.
# The release's SHA256SUMS is pinned here, and every file taken is checked against it;
# the marker names the release, so a cache from another one is replaced, never shipped.
$ffmpegRelease = "ffmpeg-8.1.3-audio-1"
$ffmpegSums = "61a9f586459e79bd9f8691d606995ef990fe2e21f4b827b951018c17a44fb69e"
$ffmpegBase = "https://github.com/am-consultingai/upshot/releases/download/$ffmpegRelease"
$vendor = Join-Path $root "vendor"
$ffmpeg = Join-Path $vendor "ffmpeg.exe"
$ffmpegMarker = Join-Path $vendor "ffmpeg.source.txt"
$ffmpegFiles = @("ffmpeg.exe", "LICENSE-FFmpeg.txt", "LICENSE-zlib.txt", "LICENSE-mingw-w64-runtime.txt")
# The release's SHA256SUMS, kept beside the files (not named ffmpeg*, so the purge below
# leaves it), so every build checks them again, cached or not.
$sumsPath = Join-Path $vendor "release-ffmpeg.SHA256SUMS"
$cachedFrom = if (Test-Path $ffmpegMarker) { "$(Get-Content $ffmpegMarker -Raw)".Trim() } else { "" }
$cached = $cachedFrom -eq "$ffmpegRelease $ffmpegSums" -and (Test-Path $sumsPath) -and
    -not ($ffmpegFiles | Where-Object { -not (Test-Path (Join-Path $vendor $_)) })
if (-not $cached) {
    New-Item -ItemType Directory -Force -Path $vendor | Out-Null
    Get-ChildItem $vendor -File -Filter "ffmpeg*" | Remove-Item -Force
    foreach ($name in $ffmpegFiles) { Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $vendor $name) }
    Invoke-WebRequest -UseBasicParsing -Uri "$ffmpegBase/SHA256SUMS" -OutFile $sumsPath
    foreach ($name in $ffmpegFiles) {
        Invoke-WebRequest -UseBasicParsing -Uri "$ffmpegBase/$name" -OutFile (Join-Path $vendor $name)
    }
}
if ((Get-FileHash $sumsPath -Algorithm SHA256).Hash.ToLower() -ne $ffmpegSums) { throw "the ffmpeg release's SHA256SUMS is not the pinned one" }
$sums = @{}
foreach ($line in Get-Content $sumsPath) { $hash, $name = $line -split "\s+", 2; $sums[$name.Trim()] = $hash }
foreach ($name in $ffmpegFiles) {
    $file = Join-Path $vendor $name
    if ((Get-FileHash $file -Algorithm SHA256).Hash.ToLower() -ne $sums[$name]) { throw "vendor\$name does not match the ffmpeg release's SHA256SUMS" }
}
[System.IO.File]::WriteAllText($ffmpegMarker, "$ffmpegRelease $ffmpegSums")
# Whatever the cache says, refuse to ship a GPL or non-free build.
$ffmpegConfig = (& $ffmpeg -hide_banner -version) -join " "
if (-not $ffmpegConfig) { throw "vendor\ffmpeg.exe does not run" }
if ($ffmpegConfig -match "--enable-(gpl|nonfree)") { throw "vendor\ffmpeg.exe is a GPL or non-free build; delete it and build again" }
Write-Host "ffmpeg: $ffmpegRelease ($((& $ffmpeg -hide_banner -version | Select-Object -First 1)))"

Write-Host "== Microsoft Visual C++ runtime =="
# Visual Studio's redistributable runtime (the x64 CRT and OpenMP folders of VC\Redist),
# from the Visual Studio package that carries them on Microsoft's download server, pinned in
# packaging\msvc_runtime.py with the hash of every file. The specs take the runtime from
# here only, and refuse a file whose hash is not the pinned one (licence review H1).
$msvc = (uv run python -c "import sys; sys.path.insert(0, 'packaging'); import msvc_runtime as m; print(m.PACKAGE); print(m.URL); print(m.SHA256)") -split "`r?`n"
Assert-Exit "reading packaging\msvc_runtime.py"
$msvcPackage, $msvcUrl, $msvcSha = $msvc[0].Trim(), $msvc[1].Trim(), $msvc[2].Trim()
$msvcDir = Join-Path $vendor "msvc-redist"
$msvcMarker = Join-Path $msvcDir "SOURCE.txt"
$msvcCached = (Test-Path $msvcMarker) -and ("$(Get-Content $msvcMarker -Raw)".Trim() -eq "$msvcPackage $msvcSha")
if (-not $msvcCached) {
    if (Test-Path $msvcDir) { Remove-Item -Recurse -Force $msvcDir }
    New-Item -ItemType Directory -Force -Path $msvcDir | Out-Null
    $vsix = Join-Path $vendor "$msvcPackage.vsix"
    Invoke-WebRequest -UseBasicParsing -Uri $msvcUrl -OutFile $vsix
    if ((Get-FileHash $vsix -Algorithm SHA256).Hash.ToLower() -ne $msvcSha) {
        Remove-Item -Force $vsix
        throw "$msvcPackage does not match the SHA-256 pinned in packaging\msvc_runtime.py"
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($vsix)
    try {
        foreach ($entry in $zip.Entries) {
            $name = [Uri]::UnescapeDataString($entry.FullName)
            if ($name -match '^Contents/VC/Redist/MSVC/[^/]+/x64/Microsoft\.VC\d+\.(CRT|OpenMP)/([^/]+\.dll)$') {
                [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $msvcDir $Matches[2]), $true)
            }
        }
    } finally { $zip.Dispose() }
    Remove-Item -Force $vsix
    [System.IO.File]::WriteAllText($msvcMarker, "$msvcPackage $msvcSha")
}
Write-Host "Visual C++ runtime: $msvcPackage"

Write-Host "== frontend =="
Push-Location (Join-Path $root "frontend")
npm ci
Assert-Exit "npm ci"
npm run build
Assert-Exit "npm run build"
Pop-Location
# Source maps (D87): Vite writes them without the comment that points a browser at them.
# They move out of the app into dist\sourcemaps, with the bundles they describe, so
# publish_release.py can upload them to Sentry for this release; none ships to users.
$maps = Join-Path $root "dist\sourcemaps"
if (Test-Path $maps) { Remove-Item -Recurse -Force $maps }
New-Item -ItemType Directory -Force -Path $maps | Out-Null
$assets = Join-Path $root "frontend\dist\assets"
Get-ChildItem $assets -Filter *.js | Copy-Item -Destination $maps
Get-ChildItem $assets -Filter *.map | Move-Item -Destination $maps
if (Get-ChildItem (Join-Path $root "frontend\dist") -Recurse -Filter *.map) { throw "a source map is still in frontend\dist" }
Write-Host "source maps: $((Get-ChildItem $maps -Filter *.map).Count) moved to dist\sourcemaps"

Write-Host "== third-party notices =="
# Every component the app ships and its licence, from what is installed here (the
# Windows-only packages included) and from the interface's source maps (dist\sourcemaps,
# just above); the spec bundles the text and its JSON twin beside upshot.exe. --strict:
# an entry without its licence text fails the build.
uv run python scripts/third_party_notices.py vendor\THIRD-PARTY-NOTICES.txt --strict
Assert-Exit "third-party notices"
# The same components against packaging\licenses\policy.toml, and the run-time packages'
# binaries for GPL code, as CI does (scripts\licence_gate.py).
uv run python scripts/licence_gate.py
Assert-Exit "the licence gate"

Write-Host "== PyInstaller =="
# --noconfirm replaces dist\upshot; nothing is removed by hand.
uv run pyinstaller --noconfirm --clean packaging\upshot.spec
Assert-Exit "PyInstaller"

Write-Host "== PyInstaller: upshot-mcp (the bridge for Claude, D86) =="
# Into dist\upshot\mcp\, after the app's freeze has replaced dist\upshot, so the installer's
# one recursive copy of dist\upshot carries it.
uv run pyinstaller --noconfirm --clean --distpath dist\upshot --workpath build\upshot-mcp packaging\upshot-mcp.spec
Assert-Exit "PyInstaller (upshot-mcp)"
$bridge = Join-Path $root "dist\upshot\mcp\upshot-mcp.exe"
# A real MCP handshake against the freeze: the answer must name the server.
# Then tools/list, which must carry the meeting tools from the frozen copy (D98): no
# Upshot is asked for here, so this is the list an AI app sees before Upshot starts.
$answer = @(
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}',
    '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
) | & $bridge --url http://127.0.0.1:9
$answer = $answer -join "`n"
if (-not ($answer -match '"name":"upshot"')) { throw "the frozen upshot-mcp did not answer initialize: $answer" }
if (-not ($answer -match '"get_summary"' -and $answer -match '"transcribe_file"')) {
    throw "the frozen upshot-mcp did not list the meeting and transcription tools: $answer"
}
Write-Host "[ok  ] upshot-mcp answers initialize and lists its tools"

Write-Host "== no GPL component in the freeze =="
# PyAV's bundled x264 and x265 shipped in every build until PyAV was removed; whatever
# brings such a library in next, the build stops here (scripts/check_dist.py).
uv run python scripts/check_dist.py dist\upshot
Assert-Exit "the GPL check of dist\upshot"

Write-Host "== every file in the freeze is in the notices =="
# Each file maps to the package that installed it, or to a notices entry for what no
# package installed (Python, the Microsoft runtime, ffmpeg, Upshot itself); a file that
# maps to nothing, or to a package the bundled notices do not list, stops the build.
uv run python scripts/third_party_notices.py --check dist\upshot
Assert-Exit "the notices check of dist\upshot"

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
    foreach ($suite in @("imports", "pipeline", "transcription")) {
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
    Sign-File $bridge
}

if ($SkipInstaller) {
    Write-Host "== done (no installer) =="
    return
}

Write-Host "== installer =="
# NVIDIA's licence for the installer's GPU page: the one Upshot ships beside the libraries
# (app\asr\licenses), copied as UTF-8 with the BOM Inno's LoadStringsFromFile needs.
$nvidia = @(Get-ChildItem (Join-Path $root "app\asr\licenses") -Filter "nvidia_*-License.txt")
if ($nvidia.Count -ne 1) { throw "expected one NVIDIA licence in app\asr\licenses, found $($nvidia.Count)" }
New-Item -ItemType Directory -Force -Path (Join-Path $root "build") | Out-Null
$nvidiaText = [System.IO.File]::ReadAllText($nvidia[0].FullName, (New-Object System.Text.UTF8Encoding $false))
[System.IO.File]::WriteAllText((Join-Path $root "build\nvidia-licence.txt"), $nvidiaText, (New-Object System.Text.UTF8Encoding $true))
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

Write-Host "== source archive =="
# The source the LGPL and MPL components' licences ask for, beside the installer;
# publish_release.py attaches it to the release with it.
uv run python scripts/source_archive.py (Join-Path $root "dist\Upshot-$version-sources.zip")
Assert-Exit "the source archive"

Write-Host "== done =="
