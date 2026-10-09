<#
.SYNOPSIS
  Run Upshot on Windows for a real, manual test.

.DESCRIPTION
  Starts the application with real two-track capture (your microphone plus system audio)
  and real local transcription, then opens the UI. You drive it: press Start, talk, play
  something through your speakers, press Stop, read the transcript.

  Everything that needs downloading is worked out and asked about up front, before
  anything starts.

  The summarizer is whatever Settings says. Pass -Provider to override it for one run.

  The speech models and the GPU libraries are the installed app's own, in -HomeDir
  (models\asr and cuda): a run from source uses exactly what the installed app uses. If
  any of them is missing the run stops and says so - the installer (or upshot.exe
  --prepare) is what puts them there, and a gap means the installation needs repairing.

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
    [string] $Provider = "",
    [string] $HomeDir = "$env:LOCALAPPDATA\upshot",
    [string] $WorkDir = "$env:LOCALAPPDATA\upshot-win",
    [int]    $Port = 8000,
    [switch] $CheckAudio,
    [switch] $Uninstall,
    [switch] $KeepClaude,
    [switch] $Yes
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

# The installed app's own speech models, shared with this run: the folder names
# app/asr/model_manager.py (target_for) uses. tests/unit/test_run_app_script.py keeps them
# in step with app/asr/models.py. Checked twice: here, before anything is touched, that
# they are there; after the dependencies, by the app itself, that each is the pinned
# revision, and which GPU libraries it would use.
$speechModels = [ordered]@{
    classifier = "Systran__faster-whisper-small"
    hebrew     = "ivrit-ai__whisper-large-v3-turbo-ct2"
    other      = "Systran__faster-whisper-large-v3"
}
# [System.IO.Path]::Combine, not Join-Path: Join-Path resolves the drive, and a -HomeDir on
# a drive this machine does not have would end the run with DriveNotFound.
$modelsDir = [System.IO.Path]::Combine($HomeDir, "models", "asr")

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

# Before anything is asked, stopped or installed: the installed app's speech models. No
# other copy is ever used; a missing one is an installation to repair, not something to
# paper over with a model from elsewhere.
$missingModels = @()
foreach ($role in $speechModels.Keys) {
    $marker = [System.IO.Path]::Combine($modelsDir, $speechModels[$role], ".upshot-verified")
    if (-not (Test-Path -LiteralPath $marker)) { $missingModels += "the $role speech model ($($speechModels[$role]))" }
}
if ($missingModels.Count -gt 0) {
    Write-Bad "The installed app's speech models are missing from ${modelsDir}:"
    foreach ($item in $missingModels) { Write-Bad "  - $item" }
    # A model_path the installed build takes for the Hebrew model (a copy of the pinned
    # size) makes the installer skip it, so reinstalling alone would never fix this.
    $configFile = [System.IO.Path]::Combine($HomeDir, "app_config.json")
    $standIn = $null
    if (Test-Path -LiteralPath $configFile) {
        try { $standIn = (Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json).asr.model_path } catch { }
    }
    if ($standIn) {
        Write-Bad "app_config.json sets asr.model_path to $standIn. If the installer takes"
        Write-Bad "that for the Hebrew model it never installs its own: remove the line first."
    }
    Write-Bad "Run the Upshot installer again (or upshot.exe --prepare) to repair it."
    Read-Host "  Press Enter to close this window" | Out-Null
    return
}
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot "run-app.local.psd1")) {
    Write-Warn "run-app.local.psd1 is no longer read: this run uses the installed app's models."
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
    Write-Host "  Upshot" -ForegroundColor White
    Write-Host "  source   $root  (read only - nothing is written here)"
    Write-Host "  runtime  $WorkDir"
    Write-Host "  data     $HomeDir"
    New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

    # \\wsl.localhost\<distro>\home\... and the older \\wsl$\<distro>\... both split into
    # the distro that owns the files and the Linux path inside it. Anything that has to
    # run *on* the files rather than just read them - npm, which cannot work over UNC -
    # needs both.
    $wslDistro = ""
    $wslPath   = ""
    if ($root -match '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.*)$') {
        $wslDistro = $Matches[1]
        $wslPath   = "/" + ($Matches[2] -replace '\\', '/')
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
        # the app's own CUDA copies, which surfaces as a silent CPU fallback or a crash inside
        # CTranslate2's lazy CUDA loading. A standalone interpreter has no such folder.
        $plan += "Python and the dependencies (~400 MB, one time, into $WorkDir)"
    }

    $needUi = -not (Test-Path "frontend\dist\index.html")
    $haveNpm = [bool] (Get-Command npm -ErrorAction SilentlyContinue)
    if ($needUi -and $haveNpm) { $plan += "the web UI's build packages (~200 MB, one time)" }

    # Without CUDA libraries CTranslate2 runs on the CPU, which is several times slower.
    #
    # "Is there a GPU" is not the same question as "is nvidia-smi installed". That tool
    # ships with the driver and answers happily on a laptop whose card is far too small
    # to hold the model, so asking only whether it exists offered a 700 MB download that
    # could not have paid off. Ask it what the card actually is instead.
    $gpuName  = ""
    $gpuMemMB = 0
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($smi) {
        # Wrapped: a driver mid-upgrade, or a card the tool cannot reach, must read as
        # "no usable GPU" rather than end the run.
        try {
            $reply = @(& $smi.Source --query-gpu=name,memory.total `
                --format=csv,noheader,nounits) | Select-Object -First 1
            if ($reply) {
                $fields = $reply -split ","
                $gpuName  = $fields[0].Trim()
                $gpuMemMB = [int] ($fields[1].Trim())
            }
        } catch { }
    }
    # large-v3 is around 1.6 GB in int8, before activations and CUDA's own context. Below
    # this it does not fit, and finding that out costs a 700 MB download and an OOM at the
    # first transcription - so the question is not worth asking.
    $gpuMinMB = 4096
    $haveNvidia = $gpuMemMB -ge $gpuMinMB
    if ($gpuName -and -not $haveNvidia) {
        Write-Warn "$gpuName has ${gpuMemMB} MB - under the ${gpuMinMB} MB the model needs,"
        Write-Warn "so transcription will run on the CPU and CUDA is not offered."
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

    # The app decides, exactly as the installed build does (frozen, so its rules apply):
    # each model verified at its
    # pinned revision, and the GPU libraries --prepare would use (its own copy, or a CUDA
    # Toolkit it found; none when transcription is set to the CPU or the card is too
    # small). A leftover asr.model_path or asr.cuda_dir in app_config.json counts for
    # nothing. Single quotes only: Windows PowerShell mangles double quotes in arguments.
    Write-Step "Checking the installed app's models and GPU libraries"
    $env:UP_HOME = $HomeDir
    Remove-Item Env:UP_ASR__MODEL_PATH, Env:UP_ASR__CUDA_DIR -ErrorAction SilentlyContinue
    $installCheck = @(& $uv run --frozen python -c @"
import json
from app import paths
from app.config import Config
from app.asr import cuda_libs
from app.asr.model_manager import is_verified, overridden_roles, target_for
from app.asr.models import MODELS
paths.is_frozen = lambda: True
c = Config.load()
missing = ['the %s speech model (%s at %s)' % (r, m.repo, m.revision[:12])
           for r, m in MODELS.items() if not is_verified(target_for(m.repo), m)]
for role in overridden_roles(c):
    if not is_verified(target_for(MODELS[role].repo), MODELS[role]):
        missing.append('remove asr.model_path (%s) from app_config.json first: the '
                       'installer takes it for the %s model and never installs its own'
                       % (c.get('asr.model_path'), role))
c.set('asr.cuda_dir', None)
want, why = cuda_libs.wanted(c)
gpu = ''
if want:
    found = cuda_libs.target_dir() if cuda_libs.ready() else cuda_libs.usable_elsewhere(c)
    if found:
        gpu = str(found)
    else:
        missing.append('the GPU libraries (%s), in %s' % (why, cuda_libs.target_dir()))
print(json.dumps({'missing': missing, 'gpu': gpu, 'why': why}))
"@)
    if ($LASTEXITCODE -ne 0 -or -not $installCheck) {
        Write-Bad "could not check the installed app's models"; return
    }
    $installed = $installCheck[-1] | ConvertFrom-Json
    if ($installed.missing.Count -gt 0) {
        Write-Bad "The installed app's files are missing or out of date in ${HomeDir}:"
        foreach ($item in $installed.missing) { Write-Bad "  - $item" }
        Write-Bad "Run the Upshot installer again (or upshot.exe --prepare) to repair it."
        return
    }
    Write-Good "speech models: $modelsDir"
    if ($installed.gpu) { Write-Good "GPU libraries: $($installed.gpu)" }
    else { Write-Good "no GPU libraries: $($installed.why)" }

    if ($needUi) {
        # The only step that must write into the source tree: npm insists on a
        # node_modules beside package.json. Say so rather than do it silently.
        Write-Warn "building the UI writes frontend\node_modules and frontend\dist"
        Write-Warn "into the source folder - they are gitignored, and are the only"
        Write-Warn "files this script puts there."
        if ($haveNpm -and (Ask "  Build the UI?")) {
            Write-Step "Building the web UI"
            $built = $false
            if ($wslDistro) {
                # Windows npm cannot install into a \\wsl.localhost path. Package install
                # scripts are run as `cmd /d /s /c ...`, and cmd refuses a UNC working
                # directory - it falls back to C:\Windows, where esbuild's installer looks
                # for its own install.js and dies. Nothing on the npm side fixes that, so
                # the build is handed to the distro that owns the files, where the paths
                # are ordinary Linux ones and the installed binaries are the right ones
                # for the platform doing the building.
                Write-Host "  source is inside WSL - building there, where the paths are native"
                $build = "cd '$wslPath/frontend' && { [ -d node_modules ] || npm ci; } && npm run build"
                & wsl.exe -d $wslDistro -- bash -lc $build
                $built = ($LASTEXITCODE -eq 0)
                if (-not $built) { Write-Warn "the build inside WSL failed" }
            } else {
                Push-Location frontend
                try {
                    if (-not (Test-Path node_modules)) {
                        npm ci
                        if ($LASTEXITCODE -ne 0) { Write-Bad "npm ci failed" }
                    }
                    if ($LASTEXITCODE -eq 0) {
                        npm run build
                        $built = ($LASTEXITCODE -eq 0)
                    }
                } finally { Pop-Location }
            }
            # A failed build used to fall through to a started app serving the "not built
            # yet" placeholder, with the reason scrolled far off the top of the window.
            if ($built) { Write-Good "UI built" }
            else {
                Write-Bad "the UI was not built - the app will serve a placeholder page."
                Write-Bad "Build it by hand, then start this script again:"
                if ($wslDistro) { Write-Bad "  (in WSL)  cd $wslPath/frontend && npm ci && npm run build" }
                else { Write-Bad "  cd frontend; npm ci; npm run build" }
            }
        } else {
            Write-Warn "skipped - the UI will be a placeholder page"
        }
    }

    # ------------------------------------------------------- configure
    $env:UP_HOME = $HomeDir
    New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null

    $env:UP_SERVER__PORT         = "$Port"
    $env:UP_AUDIO__CAPTURE       = '"wasapi"'    # real microphone + real loopback
    $env:UP_ASR__BACKEND         = '"local"'     # real ivrit-ai transcription
    # Only when asked. Exporting this unconditionally overrode the provider chosen on
    # the Settings screen at every start, so that choice looked like it reverted by
    # itself - and any later save wrote the override permanently into app_config.json.
    if ($Provider -ne "") { $env:UP_LLM__PROVIDER = '"' + $Provider + '"' }
    $env:UP_DELIVERY__NOTIFIER   = '"windows"'
    # Detection is deliberately NOT set here. Forcing it off overrode the mode chosen on
    # the Settings screen at every start, exactly as the provider override above used to:
    # the file kept saying "shadow" while the running app watched nothing, and the setting
    # looked like it reverted by itself. The shipped default is watch-and-log (DECISIONS
    # D41), and anything else is the user's choice to make and keep.
    $env:UP_AUDIO__MIN_MEETING_S = "5"           # keep short test recordings
    $env:UP_JOB_POLICY           = '"asap"'      # transcribe as soon as you press Stop
    # Named explicitly, not left to the app: from source the app still honours an
    # asr.model_path or asr.cuda_dir an old session left in app_config.json, and this run
    # must use what the check above found. null clears a leftover cuda_dir. Exported
    # values are never saved to the file. The compute type is the app's own choice (int8
    # on Pascal, float16 where the card has it).
    $hebrewModel = [System.IO.Path]::Combine($modelsDir, $speechModels["hebrew"])
    $env:UP_ASR__MODEL_PATH = '"' + ($hebrewModel -replace '\\', '\\') + '"'
    if ($installed.gpu) { $env:UP_ASR__CUDA_DIR = '"' + ($installed.gpu -replace '\\', '\\') + '"' }
    else { $env:UP_ASR__CUDA_DIR = 'null' }

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
    Write-Host "  (any browser on this computer can open http://127.0.0.1:$Port/ directly)"
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
    $keyFile = Join-Path $HomeDir "launcher.key"
    while (-not $appProcess.HasExited) {
        # N: a fresh one-time link, for a browser or profile that was never authorized.
        # The app only hands one out to a caller holding the key it wrote to $HomeDir.
        # KeyAvailable throws when there is no console to read, e.g. under a redirect.
        $pressed = $null
        try {
            if ([Console]::KeyAvailable) { $pressed = [Console]::ReadKey($true) }
        } catch { }
        if ($pressed -and $pressed.Key -eq [ConsoleKey]::N) {
            try {
                $key = (Get-Content $keyFile -Raw).Trim()
                $link = Invoke-RestMethod -Method Post -TimeoutSec 5 `
                    -Uri "http://127.0.0.1:$Port/api/auth/link" `
                    -Headers @{ "X-Upshot-Launcher" = $key }
                Write-Host ""
                Write-Good "a fresh link, good for one browser, once. Paste it into the one you want:"
                Write-Host "  $($link.url)" -ForegroundColor White
                Write-Host ""
            } catch {
                Write-Bad "could not get a fresh link: $($_.Exception.Message)"
            }
        }
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
