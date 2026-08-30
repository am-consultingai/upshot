<#
.SYNOPSIS
  Test real two-track recording and real transcription on Windows.

.DESCRIPTION
  Real microphone, real system-audio loopback, real ivrit-ai transcription.
  The summarizer is left as a fake so this test needs no API key — the point here is
  audio and words, not notes.

  Steps: check uv -> install dependencies -> probe the audio hardware -> report the ASR
  device and model -> start the app -> open the browser.

.PARAMETER ModelPath
  An existing ivrit-ai CTranslate2 model directory (the folder containing model.bin).
  Without it the first transcription downloads ~1.6 GB.

.PARAMETER SkipHardwareTest
  Skip the 60-second dual-stream test. Not recommended the first time: that test is what
  proves this machine can hold a microphone and a loopback stream at once.

.EXAMPLE
  .\test-recording.ps1 -ModelPath 'D:\models\ivrit-ai-whisper-large-v3-ct2'
#>
[CmdletBinding()]
param(
    [string] $ModelPath = "",
    [string] $HomeDir = "$env:LOCALAPPDATA\meeting-agent-test",
    [int]    $Port = 8000,
    [switch] $SkipHardwareTest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

function Write-Step { param([string] $Text) Write-Host "`n=== $Text" -ForegroundColor Cyan }
function Write-Good { param([string] $Text) Write-Host "  $Text" -ForegroundColor Green }
function Write-Warn { param([string] $Text) Write-Host "  $Text" -ForegroundColor Yellow }
function Write-Bad  { param([string] $Text) Write-Host "  $Text" -ForegroundColor Red }

function Confirm-Continue {
    param([string] $Question)
    $answer = Read-Host "$Question [y/N]"
    return ($answer -eq "y" -or $answer -eq "Y")
}

$appProcess = $null
try {
    Write-Host ""
    Write-Host "  Meeting Agent - recording and transcription test" -ForegroundColor White
    Write-Host "  repo: $root"

    # ---------------------------------------------------------------- prerequisites
    Write-Step "Checking uv"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Warn "uv is not installed. It manages Python and the dependencies."
        if (Confirm-Continue "Install it now with winget?") {
            winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
        }
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-Bad "uv still not found. Install from https://docs.astral.sh/uv/ and re-run."
            return
        }
    }
    Write-Good ("uv " + (uv --version))

    Write-Step "Installing dependencies (first run takes a few minutes)"
    uv sync
    if ($LASTEXITCODE -ne 0) { Write-Bad "uv sync failed."; return }
    Write-Good "dependencies ready (PyAudioWPatch, pywin32, pycaw, pystray installed here)"

    if (-not (Test-Path "frontend\dist\index.html")) {
        Write-Step "Building the web UI"
        if (Get-Command npm -ErrorAction SilentlyContinue) {
            Push-Location frontend
            if (-not (Test-Path node_modules)) { npm ci }
            npm run build
            Pop-Location
        } else {
            Write-Warn "npm not found - the UI will be a placeholder page."
            Write-Warn "Install Node.js, or build the UI in WSL, then re-run."
        }
    }

    # ---------------------------------------------------------------- the model
    if ($ModelPath -eq "") {
        Write-Step "Transcription model"
        Write-Host "  Point at an existing ivrit-ai CTranslate2 folder (with model.bin) to skip"
        Write-Host "  a ~1.6 GB download. Leave blank to download on first use."
        $ModelPath = Read-Host "  Model folder (blank to download)"
    }
    if ($ModelPath -ne "") {
        if (Test-Path (Join-Path $ModelPath "model.bin")) {
            $env:MA_ASR__MODEL_PATH = '"' + ($ModelPath -replace '\\', '\\') + '"'
            Write-Good "using $ModelPath"
        } else {
            Write-Warn "no model.bin in $ModelPath - ignoring it and downloading instead"
        }
    }

    # ---------------------------------------------------------------- audio hardware
    $env:MA_HOME = $HomeDir
    New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null

    Write-Step "Probing the audio hardware (T2 loopback echo test)"
    Write-Host "  Plays a signal out your speakers and captures it back. You may hear it."
    uv run python -m app.selftest audio --report "docs\spike-report.json"
    $audioOk = ($LASTEXITCODE -eq 0)
    if ($audioOk) {
        Write-Good "loopback capture works - report written to docs\spike-report.json"
    } else {
        Write-Bad "the loopback test did not pass. Recording of system audio may not work."
        Write-Warn "See TECHNICAL-DESIGN.md section 4.0 for the fallback options."
        if (-not (Confirm-Continue "Continue anyway?")) { return }
    }

    if (-not $SkipHardwareTest) {
        Write-Step "Dual-stream test (60 seconds) - the one that decides everything"
        Write-Host "  Holds the microphone and the loopback open together. If this fails,"
        Write-Host "  two-track recording is not possible on this machine as built."
        uv run pytest -q -m "audio_hw and not slow"
        if ($LASTEXITCODE -eq 0) {
            Write-Good "dual-stream capture holds"
        } else {
            Write-Bad "hardware tests failed - tell the developer, this is the project's stop condition"
            if (-not (Confirm-Continue "Continue anyway?")) { return }
        }
    }

    Write-Step "Transcription setup"
    uv run python -m app.selftest asr

    # ---------------------------------------------------------------- run it
    Write-Step "Starting the app"
    $env:MA_SERVER__PORT      = "$Port"
    $env:MA_AUDIO__CAPTURE    = '"wasapi"'   # real microphone + real loopback
    $env:MA_ASR__BACKEND      = '"local"'    # real ivrit-ai transcription
    $env:MA_LLM__PROVIDER     = '"fake"'     # no API key needed for this test
    $env:MA_DELIVERY__NOTIFIER = '"windows"'
    $env:MA_DETECTION__MODE   = '"off"'      # manual Start/Stop only
    $env:MA_AUDIO__MIN_MEETING_S = "5"       # do not discard a short test
    $env:MA_JOB_POLICY        = '"asap"'     # transcribe as soon as you press Stop

    # Python's logging writes to stderr and the banner to stdout, so both are captured;
    # the URL is looked for in each, and the live log is the error stream.
    $outLog = Join-Path $HomeDir "console.out.log"
    $errLog = Join-Path $HomeDir "console.err.log"
    foreach ($f in @($outLog, $errLog)) { if (Test-Path $f) { Remove-Item $f -Force } }
    $appProcess = Start-Process -FilePath "uv" `
        -ArgumentList @("run", "python", "-m", "app.main") `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
        -NoNewWindow -PassThru

    $url = $null
    foreach ($attempt in 1..60) {
        Start-Sleep -Milliseconds 500
        foreach ($f in @($outLog, $errLog)) {
            if ((-not $url) -and (Test-Path $f)) {
                $match = Select-String -Path $f -Pattern "http://127\.0\.0\.1:\d+/\?k=\S+" |
                         Select-Object -First 1
                if ($match) { $url = $match.Matches[0].Value }
            }
        }
        if ($url) { break }
        if ($appProcess.HasExited) { break }
    }

    if ($url) {
        Write-Good "opening $url"
        Start-Process $url
    } else {
        Write-Bad "the app did not start. Last lines of its output:"
        foreach ($f in @($outLog, $errLog)) {
            if (Test-Path $f) { Get-Content $f -Tail 20 }
        }
        return
    }

    Write-Host ""
    Write-Host "  WHAT TO DO" -ForegroundColor White
    Write-Host "    1. Press Start recording in the browser."
    Write-Host "    2. Speak into your microphone, and play something through your speakers"
    Write-Host "       (a video, a call) so the 'them' track has content too."
    Write-Host "    3. Wait at least 15 seconds, then press Stop."
    Write-Host "    4. The meeting turns Ready once transcription finishes. Open it and read"
    Write-Host "       the transcript - ME is your microphone, THEM is your system audio."
    Write-Host ""
    Write-Host "    Recordings and transcripts: $HomeDir\meetings"
    Write-Host "    The summary is a placeholder in this test (no API key needed)."
    Write-Host ""
    Write-Host "  Press Ctrl+C to stop the app." -ForegroundColor White
    Write-Host ""

    Get-Content $errLog -Wait
}
finally {
    if ($appProcess -and -not $appProcess.HasExited) {
        Write-Host "`n  stopping the app..." -ForegroundColor Cyan
        Stop-Process -Id $appProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host ""
    Read-Host "  Done. Press Enter to close this window"
}
