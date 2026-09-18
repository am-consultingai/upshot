<#
.SYNOPSIS
  Run Meeting Agent on Windows for a real, manual test.

.DESCRIPTION
  Starts the application with real two-track capture (your microphone plus system audio)
  and real local transcription, then opens the UI. You drive it: press Start, talk, play
  something through your speakers, press Stop, read the transcript.

  Everything that needs downloading is worked out and asked about up front, before
  anything starts.

  The summarizer is whatever Settings says. Pass -Provider to override it for one run.

.PARAMETER ModelPath
  The ivrit-ai CTranslate2 model folder (the one containing model.bin).

.PARAMETER CudaDir
  A folder holding cuBLAS and cuDNN DLLs. Without it CTranslate2 silently runs on the CPU,
  which on a 3 GB large-v3 model is several times slower.

.PARAMETER Provider
  Summarizer, for this run only: fake, anthropic, gemini, openai, ollama,
  claude-subscription. Omit it and the app uses whatever is chosen on the Settings
  screen - which is the point of that screen, and was previously overridden on every
  start by a default of "fake".

.PARAMETER CheckAudio
  Run the loopback probe before starting, to diagnose capture problems.

.PARAMETER KeepClaude
  Never ask about removing Claude Code. Without it, a start that finds Claude Code
  installed offers to remove it first, so the Settings screen goes back to "Not installed"
  and its Install button can be tested from a clean state. The prompt defaults to keeping
  it: reinstalling costs a 218 MB download, so pressing Enter must never trigger one.

.EXAMPLE
  .\run-app.ps1
.EXAMPLE
  .\run-app.ps1 -Provider gemini -CheckAudio
#>
[CmdletBinding()]
param(
    [string] $ModelPath = "D:\deprecated_project\Learning Managers\temp\Scripts\ivrit_model",
    [string] $CudaDir   = "D:\deprecated_project\Learning Managers\temp\Scripts",
    [string] $Provider = "",
    [string] $HomeDir = "$env:LOCALAPPDATA\meeting-agent",
    [string] $WorkDir = "$env:LOCALAPPDATA\meeting-agent-win",
    [int]    $Port = 8000,
    [switch] $CheckAudio,
    [switch] $Uninstall,
    [switch] $KeepClaude,
    [switch] $Yes
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

function Write-Step { param([string] $Text) Write-Host "`n=== $Text" -ForegroundColor Cyan }
function Write-Good { param([string] $Text) Write-Host "  $Text" -ForegroundColor Green }
function Write-Warn { param([string] $Text) Write-Host "  $Text" -ForegroundColor Yellow }
function Write-Bad  { param([string] $Text) Write-Host "  $Text" -ForegroundColor Red }
function Test-PortFree {
    <# A port is "free" if nothing accepts a connection on it. This deliberately probes
       127.0.0.1 rather than asking Windows: on WSL, a Linux listener (a Docker
       container, say) answers here while Windows reports the port as free. #>
    param([int] $Candidate)
    $client = New-Object System.Net.Sockets.TcpClient
    $free = $true
    try { $client.Connect("127.0.0.1", $Candidate); $free = -not $client.Connected }
    catch { $free = $true }
    finally { $client.Close() }
    return $free
}

function Get-RunningInstances {
    <# Previous runs of *this* application, whatever port they took. #>
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "app\.main" -and $_.ProcessId -ne $PID }
}

function Ask {
    param([string] $Question)
    if ($Yes) { return $true }
    $answer = Read-Host "$Question [y/N]"
    return ($answer -eq "y" -or $answer -eq "Y")
}

# Every Windows-side artifact goes in $WorkDir, never in the source tree. Two reasons:
# the repo may well be a WSL folder shared with a Linux checkout, where a 600 MB Windows
# venv is both wrong and painfully slow to write over the network bridge; and it keeps
# "remove it cleanly" to a single directory. Without these four variables uv would put
# the interpreter and the package cache in %LOCALAPPDATA%\uv instead. Nothing is ever
# put on PATH.
$env:UV_PROJECT_ENVIRONMENT  = Join-Path $WorkDir "venv"
$env:UV_CACHE_DIR            = Join-Path $WorkDir "cache"
$env:UV_PYTHON_INSTALL_DIR   = Join-Path $WorkDir "python"
$env:UV_TOOL_DIR             = Join-Path $WorkDir "tools"
# Anything that reaches for Hugging Face lands here too, not in %USERPROFILE%\.cache.
$env:HF_HOME                 = Join-Path $WorkDir "hf"
# Otherwise the interpreter litters __pycache__ through the source tree.
$env:PYTHONPYCACHEPREFIX     = Join-Path $WorkDir "pycache"

$uvBin = Join-Path $WorkDir "bin\uv.exe"

# -Uninstall removes exactly these and nothing else. The source tree is not on the list
# because nothing is written to it.
$ownedPaths = @($WorkDir)

if ($Uninstall) {
    Write-Step "Removing everything this script installed"
    $present = $ownedPaths | Where-Object { Test-Path $_ }
    if (-not $present) { Write-Good "nothing to remove" }
    else {
        foreach ($path in $present) { Write-Host "    $path" }
        Write-Host ""
        if (Ask "  Delete these?") {
            foreach ($path in $present) { Remove-Item $path -Recurse -Force }
            Write-Good "removed"
        } else { Write-Warn "cancelled" }
    }
    if (Test-Path $HomeDir) {
        Write-Host ""
        Write-Warn "your recordings and transcripts are in $HomeDir"
        if (Ask "  Delete those too? (this is your data)") {
            Remove-Item $HomeDir -Recurse -Force
            Write-Good "removed $HomeDir"
        } else { Write-Good "kept $HomeDir" }
    }
    Write-Host ""
    Write-Host "  Nothing was added to PATH, the registry, or Program Files, and nothing"
    Write-Host "  was written into the source folder, so there is nothing further to undo."
    return
}

function Get-ClaudeInstall {
    <# Where Claude Code is on this machine, or nothing.

       Deliberately more than `Get-Command`: winget installs this package *portable*, so
       the binary sits under WinGet\Packages with no symlink on PATH, and resolving by
       name misses it completely. That is the same blind spot that had the application
       reporting "not installed" while it plainly was. #>
    $onPath = (Get-Command claude -ErrorAction SilentlyContinue).Source
    if ($onPath) { return $onPath }
    $candidates = @(
        "$env:USERPROFILE\.local\bin\claude.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\claude.exe"
    ) + @(Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Anthropic.ClaudeCode*\claude.exe" -ErrorAction SilentlyContinue |
        ForEach-Object { $_.FullName })
    return $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not $KeepClaude) {
    # Asked before the app starts, never after: once it is up, its settings page runs
    # `claude --version` and `claude auth status` on every load, which re-locks the
    # executable and recreates ~\.claude the moment they are deleted.
    $existing = Get-ClaudeInstall
    if ($existing) {
        Write-Step "Claude Code is already installed"
        Write-Host "    $existing"
        Write-Host "  Removing it returns the Settings screen to 'Not installed', so the"
        Write-Host "  Install button can be tested. Reinstalling is a 218 MB download, so"
        Write-Host "  the default here is to keep it - just press Enter."
        if (Ask "  Remove it?") {
            # Wrapped: a winget that refuses is no reason to refuse to start the app.
            try { & (Join-Path $PSScriptRoot "remove-claude.ps1") }
            catch { Write-Warn "could not remove Claude Code: $_" }
        } else { Write-Good "kept - the Settings row will show it as installed" }
    }
}

$appProcess = $null
# Set once the app is up. Until then a failure needs to stay readable on screen.
$script:started = $false
try {
    Write-Host ""
    Write-Host "  Meeting Agent" -ForegroundColor White
    Write-Host "  source   $root  (read only - nothing is written here)"
    Write-Host "  runtime  $WorkDir"
    Write-Host "  data     $HomeDir"
    New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

    if ($root -like "\\wsl*") {
        Write-Good "source is on a WSL path - reading it over the bridge is fine, and"
        Write-Good "the venv and cache go to $WorkDir on local disk, not into WSL."
    }

    # ------------------------------------------------------- one instance, one port
    # Two things this has to get right, because both have already bitten:
    #   1. never leave two copies running - the older one shadows the newer;
    #   2. never fail because some unrelated service owns the default port.
    Write-Step "Making room to start"

    $running = @(Get-RunningInstances)
    if ($running.Count -gt 0) {
        Write-Warn "stopping $($running.Count) instance(s) still running"
        foreach ($proc in $running) {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 500
        if (@(Get-RunningInstances).Count -gt 0) {
            Start-Sleep -Seconds 2   # a killed process holds its socket for a moment
        }
        Write-Good "previous instance stopped"
    }

    # The requested port first, then a range well clear of the usual dev servers.
    # A separate variable on purpose: $Port is typed [int], so assigning $null to it
    # would silently become 0 and the "nothing free" check could never fire.
    $wanted = $Port
    $chosen = 0
    foreach ($candidate in @($wanted) + 8010..8040) {
        if (Test-PortFree $candidate) { $chosen = $candidate; break }
    }
    if ($chosen -eq 0) {
        Write-Bad "no free port between $wanted and 8040. Close something and re-run."
        return
    }
    $Port = $chosen
    if ($Port -ne $wanted) {
        Write-Warn "port $wanted is taken by something else (a Docker container, perhaps)"
    }
    Write-Good "using port $Port"

    # ------------------------------------------------------- what will be downloaded
    Write-Step "Checking what needs downloading"
    $plan = @()

    # Prefer a uv already on the machine; otherwise keep our own copy inside the repo.
    $uv = $null
    if (Test-Path $uvBin) { $uv = $uvBin }
    else {
        $onPath = Get-Command uv -ErrorAction SilentlyContinue
        if ($onPath) { $uv = $onPath.Source }
    }
    if (-not $uv) { $plan += "uv, the package manager (~20 MB, into $WorkDir)" }

    $venvPython = Join-Path $env:UV_PROJECT_ENVIRONMENT "Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        # uv fetches its own Python on purpose. Not because of the version - the suite
        # passes in full on 3.12, so Anaconda's interpreter is new enough - but because
        # conda puts its own cuBLAS in Library\bin and its DLL search order can win over
        # the CudaDir copies, which surfaces as a silent CPU fallback or a crash inside
        # CTranslate2's lazy CUDA loading. A standalone interpreter has no such folder.
        $plan += "Python and the dependencies (~400 MB, one time, into $WorkDir)"
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
    $cudaOk = $false
    $cudaWanted = $false
    if ($CudaDir -ne "" -and (Test-Path $CudaDir)) {
        $cudaOk = [bool] (Get-ChildItem -Path $CudaDir -Filter "cublas*" `
            -ErrorAction SilentlyContinue | Select-Object -First 1)
        if (-not $cudaOk) { Write-Warn "no cuBLAS DLLs in: $CudaDir" }
    }
    if ($haveNvidia -and -not $cudaOk) {
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
    if (-not $uv) {
        # Deliberately not winget: that installs machine-wide and puts a shim on PATH.
        # The release zip is just uv.exe, so it can live in the repo and be called by path.
        Write-Step "Fetching uv into .uv-bin"
        $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "aarch64" } else { "x86_64" }
        $asset = "uv-$arch-pc-windows-msvc.zip"
        $base = "https://github.com/astral-sh/uv/releases/latest/download"
        $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("uv-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        try {
            $zip = Join-Path $tmp $asset
            Invoke-WebRequest -Uri "$base/$asset" -OutFile $zip
            Invoke-WebRequest -Uri "$base/$asset.sha256" -OutFile "$zip.sha256"
            $want = ((Get-Content "$zip.sha256" -Raw).Trim() -split '\s+')[0]
            $got = (Get-FileHash $zip -Algorithm SHA256).Hash
            if ($got -ne $want) { Write-Bad "uv download failed its checksum"; return }
            Expand-Archive -Path $zip -DestinationPath $tmp -Force
            $exe = Get-ChildItem -Path $tmp -Recurse -Filter "uv.exe" | Select-Object -First 1
            if (-not $exe) { Write-Bad "no uv.exe in the release archive"; return }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $uvBin) | Out-Null
            Copy-Item $exe.FullName $uvBin -Force
        } finally {
            Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
        }
        $uv = $uvBin
        Write-Good "uv is in .uv-bin (not on your PATH)"
    }

    Write-Step "Installing dependencies into $WorkDir"
    # --frozen so the lockfile in the source tree is read, never rewritten.
    & $uv sync --frozen
    if ($LASTEXITCODE -ne 0) { Write-Bad "uv sync failed"; return }
    Write-Good "ready"

    if ($cudaWanted -and (Ask "  Install the CUDA libraries for GPU transcription?")) {
        & $uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
        if ($LASTEXITCODE -eq 0) { Write-Good "CUDA libraries installed" }
        else { Write-Warn "CUDA install failed - transcription will run on the CPU" }
    }

    if ($needUi) {
        # The only step that must write into the source tree: npm insists on a
        # node_modules beside package.json. Say so rather than do it silently.
        Write-Warn "building the UI writes frontend\node_modules and frontend\dist"
        Write-Warn "into the source folder - they are gitignored, and are the only"
        Write-Warn "files this script puts there."
        if ($haveNpm -and (Ask "  Build the UI?")) {
            Write-Step "Building the web UI"
            Push-Location frontend
            if (-not (Test-Path node_modules)) { npm ci }
            npm run build
            Pop-Location
        } else {
            Write-Warn "skipped - the UI will be a placeholder page"
        }
    }

    # ------------------------------------------------------- configure
    $env:MA_HOME = $HomeDir
    New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null

    $env:MA_SERVER__PORT         = "$Port"
    $env:MA_AUDIO__CAPTURE       = '"wasapi"'    # real microphone + real loopback
    $env:MA_ASR__BACKEND         = '"local"'     # real ivrit-ai transcription
    # Only when asked. Exporting this unconditionally overrode the provider chosen on
    # the Settings screen at every start, so that choice looked like it reverted by
    # itself - and any later save wrote the override permanently into app_config.json.
    if ($Provider -ne "") { $env:MA_LLM__PROVIDER = '"' + $Provider + '"' }
    $env:MA_DELIVERY__NOTIFIER   = '"windows"'
    # Detection is deliberately NOT set here. Forcing it off overrode the mode chosen on
    # the Settings screen at every start, exactly as the provider override above used to:
    # the file kept saying "shadow" while the running app watched nothing, and the setting
    # looked like it reverted by itself. The shipped default is watch-and-log (DECISIONS
    # D41), and anything else is the user's choice to make and keep.
    $env:MA_AUDIO__MIN_MEETING_S = "5"           # keep short test recordings
    $env:MA_JOB_POLICY           = '"asap"'      # transcribe as soon as you press Stop
    if ($modelOk) {
        $env:MA_ASR__MODEL_PATH = '"' + ($ModelPath -replace '\\', '\\') + '"'
        Write-Good "model: $ModelPath"
    }
    if ($cudaOk) {
        $env:MA_ASR__CUDA_DIR = '"' + ($CudaDir -replace '\\', '\\') + '"'
        # A GTX 1080 is Pascal: it has no fast float16, so int8 is the right compute type
        # (DESIGN.md section 20.5). Remove this line on newer hardware.
        $env:MA_ASR__COMPUTE_TYPE = '"int8"'
        Write-Good "CUDA: $CudaDir (int8, suits Pascal)"
    }

    switch ($Provider) {
        "anthropic" { if (-not $env:ANTHROPIC_API_KEY) { Write-Warn "ANTHROPIC_API_KEY is not set" } }
        "gemini"    { if (-not $env:GEMINI_API_KEY)    { Write-Warn "GEMINI_API_KEY is not set" } }
        "openai"    { if (-not $env:OPENAI_API_KEY)    { Write-Warn "OPENAI_API_KEY is not set" } }
    }

    # Print what the application actually resolves, in its own process. Env vars that
    # look set here but do not reach the app are otherwise invisible until you notice
    # every transcript says the same thing.
    Write-Step "Resolved configuration"
    & $uv run --frozen python -c @"
from app.config import Config
c = Config.load()
for k in ('audio.capture', 'asr.backend', 'asr.model_path', 'asr.cuda_dir',
          'asr.compute_type', 'llm.provider', 'detection.mode'):
    print('  %-18s %s' % (k, c.get(k)))
from app.asr.local import probe_device
device, compute = probe_device(c)[:2]
print('  %-18s %s / %s' % ('asr device', device, compute))
"@
    if ($LASTEXITCODE -ne 0) { Write-Warn "could not read the resolved configuration" }

    if ($CheckAudio) {
        Write-Step "Probing audio (plays a signal and captures it back)"
        & $uv run python -m app.selftest audio --report (Join-Path $WorkDir "audio-probe.json")
        if ($LASTEXITCODE -eq 0) { Write-Good "loopback capture works" }
        else { Write-Bad "loopback probe failed - system audio may not be captured" }
    }

    # ------------------------------------------------------- run
    Write-Step "Starting"
    $outLog = Join-Path $HomeDir "console.out.log"
    $errLog = Join-Path $HomeDir "console.err.log"
    foreach ($f in @($outLog, $errLog)) { if (Test-Path $f) { Remove-Item $f -Force } }
    $appProcess = Start-Process -FilePath $uv `
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

    $script:started = $true
    Write-Good "opening your default browser - no need to click the link below"
    Write-Host "  $url"
    Write-Host "  (the ?k= part authorizes the browser once; afterwards just use"
    Write-Host "   http://127.0.0.1:$Port/. A different browser needs a fresh link,"
    Write-Host "   which means restarting this script.)"
    Start-Process $url

    Write-Host ""
    Write-Host "  Everything downloaded lives in two places, neither of them the source folder:"
    Write-Host "    $WorkDir   (Python, dependencies, package cache)"
    Write-Host "    $HomeDir   (your recordings, transcripts and database)"
    Write-Host "  Nothing was put on PATH. To undo it all: .\run-app.ps1 -Uninstall"

    Write-Host ""
    Write-Host "  READY - drive it from the browser" -ForegroundColor White
    Write-Host "    Start recording, speak into your mic, play audio through your speakers,"
    Write-Host "    wait 15+ seconds, then Stop. ME is your microphone, THEM is system audio."
    Write-Host ""
    Write-Host "    First transcription loads the model and may take a minute."
    Write-Host "    Recordings: $HomeDir\meetings"
    Write-Host ""
    Write-Host "    Logs (plain Windows paths - send these when something goes wrong):"
    Write-Host "      $HomeDir\logs\app.log        everything the app logged"
    Write-Host "      $HomeDir\console.err.log    the same, plus anything it printed"
    if ($Provider -eq "fake") {
        Write-Host "    Summaries are placeholders for this run (-Provider was set to fake)."
    }
    Write-Host ""
    Write-Host "  Ctrl+C stops the app." -ForegroundColor White
    Write-Host ""

    # Tail the log while watching the process. `Get-Content -Wait` on its own waits
    # forever on a file nobody is writing to any more, which is exactly what a crash
    # looks like: on 2026-09-18 the app died inside a Windows notification call and this
    # window went on showing the last line as if all were well.
    $shown = 0
    while (-not $appProcess.HasExited) {
        $lines = @(Get-Content $errLog -ErrorAction SilentlyContinue)
        if ($lines.Count -gt $shown) {
            $lines[$shown..($lines.Count - 1)] | ForEach-Object { Write-Host $_ }
            $shown = $lines.Count
        }
        Start-Sleep -Milliseconds 300
    }
    $lines = @(Get-Content $errLog -ErrorAction SilentlyContinue)
    if ($lines.Count -gt $shown) {
        $lines[$shown..($lines.Count - 1)] | ForEach-Object { Write-Host $_ }
    }

    Write-Host ""
    Write-Bad "the application stopped on its own (exit code $($appProcess.ExitCode))"
    Write-Host "  A fault inside a Windows component ends the process before Python can log"
    Write-Host "  anything, so the log above may simply stop. Windows records it anyway:"
    Write-Host "    Event Viewer > Windows Logs > Application, source 'Application Error'"
    Write-Host "  Meetings already recorded are safe on disk; start this script again to"
    Write-Host "  carry on with them."
    # Hold the window open: this is the one case where there is something to read.
    $script:started = $false
}
finally {
    if ($appProcess -and -not $appProcess.HasExited) {
        Write-Host "`n  stopping..." -ForegroundColor Cyan
        Stop-Process -Id $appProcess.Id -Force -ErrorAction SilentlyContinue
    }
    # Only hold the window open when there is something to read. Ctrl+C is an
    # instruction, not a question: once the app has run, stopping it just stops it.
    if (-not $script:started) {
        Write-Host ""
        Read-Host "  Press Enter to close this window"
    }
}
