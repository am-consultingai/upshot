<#
.SYNOPSIS
  Run Meeting Agent on Windows for a real, manual test.

.DESCRIPTION
  Starts the application with real two-track capture (your microphone plus system audio)
  and real local transcription, then opens the UI. You drive it: press Start, talk, play
  something through your speakers, press Stop, read the transcript.

  Everything that needs downloading is worked out and asked about up front, before
  anything starts.

  The summarizer is a placeholder unless you pass -Provider, so no API key is needed.

.PARAMETER ModelPath
  The ivrit-ai CTranslate2 model folder (the one containing model.bin).

.PARAMETER Provider
  Summarizer: fake (default, no key), anthropic, gemini, openai, ollama,
  claude-subscription.

.PARAMETER CheckAudio
  Run the loopback probe before starting, to diagnose capture problems.

.EXAMPLE
  .\run-app.ps1
.EXAMPLE
  .\run-app.ps1 -Provider gemini -CheckAudio
#>
[CmdletBinding()]
param(
    [string] $ModelPath = "D:\deprecated_project\Learning Managers\temp\Scripts\ivrit_model",
    [string] $Provider = "fake",
    [string] $HomeDir = "$env:LOCALAPPDATA\meeting-agent",
    [int]    $Port = 8000,
    [switch] $CheckAudio,
    [switch] $Yes
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

function Write-Step { param([string] $Text) Write-Host "`n=== $Text" -ForegroundColor Cyan }
function Write-Good { param([string] $Text) Write-Host "  $Text" -ForegroundColor Green }
function Write-Warn { param([string] $Text) Write-Host "  $Text" -ForegroundColor Yellow }
function Write-Bad  { param([string] $Text) Write-Host "  $Text" -ForegroundColor Red }
function Ask {
    param([string] $Question)
    if ($Yes) { return $true }
    $answer = Read-Host "$Question [y/N]"
    return ($answer -eq "y" -or $answer -eq "Y")
}

# The repo's .venv is built for Linux. Keep a separate one for Windows so the two can
# coexist in the same folder.
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"

$appProcess = $null
try {
    Write-Host ""
    Write-Host "  Meeting Agent" -ForegroundColor White
    Write-Host "  repo     $root"
    Write-Host "  home     $HomeDir"

    if ($root -like "\\wsl*") {
        Write-Warn "This repo is on a WSL network path. Windows tools are slow and"
        Write-Warn "sometimes fail there - copying it to a local drive is more reliable."
    }

    # ------------------------------------------------------- what will be downloaded
    Write-Step "Checking what needs downloading"
    $plan = @()

    $haveUv = [bool] (Get-Command uv -ErrorAction SilentlyContinue)
    if (-not $haveUv) { $plan += "uv, the package manager (a few MB, via winget)" }

    $venvPython = Join-Path $root ".venv-win\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        $plan += "Python 3.13 and the dependencies (~400 MB, one time)"
    }

    $modelOk = ($ModelPath -ne "") -and (Test-Path (Join-Path $ModelPath "model.bin"))
    if (-not $modelOk) {
        if ($ModelPath -ne "") { Write-Warn "no model.bin under: $ModelPath" }
        $plan += "the transcription model (~1.6-3 GB, one time)"
    }

    $needUi = -not (Test-Path "frontend\dist\index.html")
    $haveNpm = [bool] (Get-Command npm -ErrorAction SilentlyContinue)
    if ($needUi -and $haveNpm) { $plan += "the web UI's build packages (~200 MB, one time)" }

    # Without CUDA libraries CTranslate2 runs on the CPU, which is several times slower.
    $haveNvidia = [bool] (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
    $cudaWanted = $false
    if ($haveNvidia) {
        $searchPaths = @("$HomeDir\cuda")
        if ($env:CUDA_PATH) { $searchPaths += $env:CUDA_PATH }
        $cudaProbe = Get-ChildItem -Path $searchPaths -Recurse -Filter "cublas*" `
            -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $cudaProbe) {
            $plan += "CUDA libraries for GPU transcription (~700 MB, optional but much faster)"
            $cudaWanted = $true
        }
    }

    if ($plan.Count -eq 0) {
        Write-Good "nothing to download"
    } else {
        Write-Host "  The following will be downloaded:"
        foreach ($item in $plan) { Write-Host "    - $item" }
        Write-Host ""
        if (-not (Ask "  Proceed?")) { Write-Warn "cancelled"; return }
    }

    # ------------------------------------------------------- do it
    if (-not $haveUv) {
        Write-Step "Installing uv"
        winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
        if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
            Write-Bad "uv still not found - install from https://docs.astral.sh/uv/ and re-run"
            return
        }
    }

    Write-Step "Installing dependencies into .venv-win"
    uv sync
    if ($LASTEXITCODE -ne 0) { Write-Bad "uv sync failed"; return }
    Write-Good "ready"

    if ($cudaWanted -and (Ask "  Install the CUDA libraries for GPU transcription?")) {
        uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
        if ($LASTEXITCODE -eq 0) { Write-Good "CUDA libraries installed" }
        else { Write-Warn "CUDA install failed - transcription will run on the CPU" }
    }

    if ($needUi) {
        if ($haveNpm) {
            Write-Step "Building the web UI"
            Push-Location frontend
            if (-not (Test-Path node_modules)) { npm ci }
            npm run build
            Pop-Location
        } else {
            Write-Warn "npm not found - the UI will be a placeholder page"
        }
    }

    # ------------------------------------------------------- configure
    $env:MA_HOME = $HomeDir
    New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null

    $env:MA_SERVER__PORT         = "$Port"
    $env:MA_AUDIO__CAPTURE       = '"wasapi"'    # real microphone + real loopback
    $env:MA_ASR__BACKEND         = '"local"'     # real ivrit-ai transcription
    $env:MA_LLM__PROVIDER        = '"' + $Provider + '"'
    $env:MA_DELIVERY__NOTIFIER   = '"windows"'
    $env:MA_DETECTION__MODE      = '"off"'       # manual Start/Stop; no auto-recording
    $env:MA_AUDIO__MIN_MEETING_S = "5"           # keep short test recordings
    $env:MA_JOB_POLICY           = '"asap"'      # transcribe as soon as you press Stop
    if ($modelOk) {
        $env:MA_ASR__MODEL_PATH = '"' + ($ModelPath -replace '\\', '\\') + '"'
        Write-Good "model: $ModelPath"
    }

    switch ($Provider) {
        "anthropic" { if (-not $env:ANTHROPIC_API_KEY) { Write-Warn "ANTHROPIC_API_KEY is not set" } }
        "gemini"    { if (-not $env:GEMINI_API_KEY)    { Write-Warn "GEMINI_API_KEY is not set" } }
        "openai"    { if (-not $env:OPENAI_API_KEY)    { Write-Warn "OPENAI_API_KEY is not set" } }
    }

    if ($CheckAudio) {
        Write-Step "Probing audio (plays a signal and captures it back)"
        uv run python -m app.selftest audio --report "docs\spike-report.json"
        if ($LASTEXITCODE -eq 0) { Write-Good "loopback capture works" }
        else { Write-Bad "loopback probe failed - system audio may not be captured" }
    }

    # ------------------------------------------------------- run
    Write-Step "Starting"
    $outLog = Join-Path $HomeDir "console.out.log"
    $errLog = Join-Path $HomeDir "console.err.log"
    foreach ($f in @($outLog, $errLog)) { if (Test-Path $f) { Remove-Item $f -Force } }
    $appProcess = Start-Process -FilePath "uv" `
        -ArgumentList @("run", "python", "-m", "app.main") `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
        -NoNewWindow -PassThru

    $url = $null
    foreach ($attempt in 1..90) {
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

    if (-not $url) {
        Write-Bad "the app did not start. Its last output:"
        foreach ($f in @($outLog, $errLog)) { if (Test-Path $f) { Get-Content $f -Tail 20 } }
        return
    }

    Write-Good "opening $url"
    Start-Process $url

    Write-Host ""
    Write-Host "  READY - drive it from the browser" -ForegroundColor White
    Write-Host "    Start recording, speak into your mic, play audio through your speakers,"
    Write-Host "    wait 15+ seconds, then Stop. ME is your microphone, THEM is system audio."
    Write-Host ""
    Write-Host "    First transcription loads the model and may take a minute."
    Write-Host "    Recordings: $HomeDir\meetings"
    if ($Provider -eq "fake") {
        Write-Host "    Summaries are placeholders (-Provider gemini or anthropic for real ones)."
    }
    Write-Host ""
    Write-Host "  Ctrl+C stops the app." -ForegroundColor White
    Write-Host ""

    Get-Content $errLog -Wait
}
finally {
    if ($appProcess -and -not $appProcess.HasExited) {
        Write-Host "`n  stopping..." -ForegroundColor Cyan
        Stop-Process -Id $appProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host ""
    Read-Host "  Press Enter to close this window"
}
