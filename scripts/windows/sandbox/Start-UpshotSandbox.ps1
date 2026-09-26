# Machine B, the host side of a watched Sandbox run. Started by Run-Upshot-Sandbox.cmd, which
# has just fetched this file, sandbox-test.ps1 and show.wsb from Drive.
#
#   1. Checks Windows Sandbox, and that no other sandbox is open.
#   2. Pauses B's test runner for the length of the run (and turns it back on at the end).
#   3. Brings the installer zip up to date from Drive, checked by SHA-256.
#   4. Opens the sandbox (show.wsb), which installs, runs and uninstalls Upshot on screen.
#   5. Streams every step into this window as it happens, then prints the summary.
#
# Nothing is installed on this machine: the installer is unpacked and run only inside the
# sandbox, and everything in the sandbox is discarded when its window closes.
#
# Keep this file ASCII: Windows PowerShell 5.1 reads a script without a BOM as ANSI.
param(
    [string]$Kit = 'C:\upshot-work\sandbox-watch',
    [string]$DriveFolder = 'tools/sandbox-watch',
    [int]$TimeoutMinutes = 40
)
$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'Upshot sandbox test'
$started = Get-Date

function Say([string]$text, [string]$color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Title([string]$text) { Write-Host ''; Write-Host "== $text ==" -ForegroundColor White }
function Good([string]$text) { Write-Host "  ok   $text" -ForegroundColor Green }
function Warn([string]$text) { Write-Host "  !    $text" -ForegroundColor Yellow }
function Bad([string]$text) { Write-Host "  FAIL $text" -ForegroundColor Red }
function Ask([string]$question) {
    $answer = Read-Host "  $question [Y/n]"
    return ($answer -eq '' -or $answer -match '^[Yy]')
}
# B's WSL holds drive.py, the Drive token and the runner.
function Wsl([string]$command) {
    # Out-Host: anything it prints goes to the screen, not into the return value.
    & wsl.exe -e bash -lc $command | Out-Host
    return $LASTEXITCODE
}
function To-Wsl([string]$windowsPath) {
    return '/mnt/' + $windowsPath.Substring(0, 1).ToLower() + ($windowsPath.Substring(2) -replace '\\', '/')
}
function Sandbox-Windows {
    return @(Get-Process -Name WindowsSandbox, WindowsSandboxRemoteSession, WindowsSandboxClient -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne 0 })
}
function Line-Color([string]$line) {
    if ($line -match '^\S+ ok ') { return 'Green' }
    if ($line -match '^\S+ FAIL ') { return 'Red' }
    if ($line -match '^\S+ skip ') { return 'DarkYellow' }
    if ($line -match '^\S+ \.\.\. ') { return 'Cyan' }
    return 'White'
}

$pausedRunner = $false
try {
    Write-Host ''
    Write-Host '  Upshot - install test in Windows Sandbox' -ForegroundColor White
    Write-Host '  The installer runs inside a throwaway copy of Windows. This machine is not changed.' -ForegroundColor DarkGray

    # 1 ---------------------------------------------------------------------------------
    Title 'Windows Sandbox'
    $sandboxExe = Join-Path $env:WINDIR 'System32\WindowsSandbox.exe'
    if (-not (Test-Path $sandboxExe)) { throw 'Windows Sandbox is not turned on (no WindowsSandbox.exe).' }
    Good 'Windows Sandbox is installed'
    $open = Sandbox-Windows
    if ($open.Count -gt 0) {
        # Only one sandbox can run at a time. Closing it discards everything in it, including
        # an Upshot installed there, which is what a fresh run wants anyway.
        Warn 'a sandbox is already open; closing it (everything in it is discarded)'
        $open | ForEach-Object { [void]$_.CloseMainWindow() }
        Start-Sleep -Seconds 20
        Get-Process -Name 'WindowsSandbox*' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 10
        Good 'closed the open sandbox'
    } else {
        Good 'no other sandbox is open'
    }

    # 2 ---------------------------------------------------------------------------------
    Title "Machine B's test runner"
    if ((Wsl 'test -e ~/upshot-agent/STOP') -eq 0) {
        Good 'already paused (a STOP file exists); left as it is'
    } else {
        [void](Wsl 'touch ~/upshot-agent/STOP')
        $pausedRunner = $true
        Good 'paused for this run; it starts again when this window finishes'
    }
    if ((Wsl 'pgrep -f "claude -p" >/dev/null') -eq 0) {
        Warn 'the runner is in the middle of a job, which may open a sandbox of its own'
        if (Ask 'End that job now? (it is reported as failed; nothing else is lost)') {
            [void](Wsl 'pkill -f "claude -p"')
            Good 'ended the running job'
        } else {
            Warn 'left running; if its sandbox opens first, this one will fail to start'
        }
    } else {
        Good 'no job is running'
    }

    # 3 ---------------------------------------------------------------------------------
    Title 'Test kit'
    $in = Join-Path $Kit 'in'
    New-Item -ItemType Directory -Force -Path $in | Out-Null
    $inWsl = To-Wsl $in
    $listing = & wsl.exe -e bash -lc "python3 ~/upshot-agent/drive.py ls $DriveFolder/in"
    $zipName = ($listing | Select-String -Pattern 'Upshot-\S+-installer\.zip$' | Select-Object -First 1).Matches.Value
    if (-not $zipName) { throw "no Upshot-*-installer.zip in Drive $DriveFolder/in" }
    foreach ($small in @("$zipName.sha256", 'upshot-test-signing.cer')) {
        if ((Wsl "python3 ~/upshot-agent/drive.py get $DriveFolder/in/$small $inWsl/$small") -ne 0) { throw "could not fetch $small from Drive" }
    }
    $zip = Join-Path $in $zipName
    $want = ((Get-Content -Raw (Join-Path $in "$zipName.sha256")) -split '\s+')[0].ToLower()
    $have = if (Test-Path $zip) { (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower() } else { '' }
    if ($have -eq $want) {
        Good "$zipName is already here and current"
    } else {
        Say "  ...  downloading $zipName from Drive (about 110 MB, a minute or two)" 'Cyan'
        if ((Wsl "python3 ~/upshot-agent/drive.py get $DriveFolder/in/$zipName $inWsl/$zipName") -ne 0) { throw "could not download $zipName" }
        $have = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
        if ($have -ne $want) { throw "$zipName does not match its .sha256 after download" }
        Good "$zipName downloaded and verified"
    }
    Get-ChildItem -Recurse -File $Kit | Where-Object { $_.FullName -notlike "$Kit\out*" } | ForEach-Object {
        Say ("       {0,-45} {1,10:N0} KB" -f $_.FullName.Substring($Kit.Length + 1), [math]::Ceiling($_.Length / 1KB)) 'DarkGray'
    }
    Good "SHA-256 $($want.Substring(0, 16))..."
    # Keep the last run's results rather than overwrite them.
    $out = Join-Path $Kit 'out'
    if (Test-Path $out) {
        $previous = "out-" + (Get-Item $out).LastWriteTime.ToString('yyyyMMdd-HHmmss')
        Rename-Item $out $previous
        Say "       previous results kept as $previous" 'DarkGray'
    }
    New-Item -ItemType Directory -Force -Path $out | Out-Null

    # 4 ---------------------------------------------------------------------------------
    Title 'Starting the sandbox'
    Start-Process -FilePath $sandboxExe -ArgumentList "`"$(Join-Path $Kit 'show.wsb')`"" | Out-Null
    Good 'the Sandbox window is opening; Windows inside it takes about a minute to boot'
    Say '       Watch the Sandbox window: installer, app log, Upshot in Edge, uninstaller.' 'DarkGray'
    Say '       Every step is also printed here as it happens.' 'DarkGray'

    # 5 ---------------------------------------------------------------------------------
    Title 'Inside the sandbox'
    $stepsLog = Join-Path $out 'steps.log'
    $done = Join-Path $out 'DONE.txt'
    $shown = 0
    $warned = $false
    $sawWindow = $false
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $t0 = Get-Date
    while ($true) {
        if (Test-Path $stepsLog) {
            $lines = @(Get-Content -Encoding UTF8 $stepsLog -ErrorAction SilentlyContinue | Where-Object { $_ -ne '' })
            for ($i = $shown; $i -lt $lines.Count; $i++) { Write-Host "  $($lines[$i])" -ForegroundColor (Line-Color $lines[$i]) }
            $shown = [math]::Max($shown, $lines.Count)
        } elseif (-not $warned -and ((Get-Date) - $t0).TotalMinutes -gt 4) {
            $warned = $true
            Warn 'nothing from the sandbox after 4 minutes. If its desktop is up but no console opened,'
            Warn 'open PowerShell inside the sandbox and paste:'
            Say '       powershell -NoProfile -ExecutionPolicy Bypass -File C:\watch\sandbox-test.ps1 -Mode show -StayOpen -In C:\watch\in -Out C:\watch\out' 'White'
        }
        if (Test-Path $done) { break }
        if ((Sandbox-Windows).Count -gt 0) { $sawWindow = $true }
        elseif ($sawWindow) { Bad 'the Sandbox window closed before the test finished'; break }
        if ((Get-Date) -gt $deadline) { Bad "no result after $TimeoutMinutes minutes"; break }
        Start-Sleep -Seconds 1
    }

    Title 'Result'
    $stepsJson = Join-Path $out 'steps.json'
    if (Test-Path $stepsJson) {
        $steps = @(Get-Content -Raw -Encoding UTF8 $stepsJson | ConvertFrom-Json)
        $failed = @($steps | Where-Object { -not $_.ok })
        if ($failed.Count -eq 0) {
            Good "all $($steps.Count) checks passed"
        } else {
            Bad "$($failed.Count) of $($steps.Count) checks failed:"
            $failed | ForEach-Object { Say "       $($_.name): $($_.detail)" 'Red' }
        }
    } else {
        Bad 'the sandbox wrote no results'
    }
    Say "       took $([int]((Get-Date) - $started).TotalMinutes) min; results and logs in $out" 'DarkGray'
    Say '       The sandbox stays open so you can look around. Close its window when done.' 'DarkGray'
} catch {
    Write-Host ''
    Bad "$($_.Exception.Message)"
} finally {
    if ($pausedRunner) {
        [void](Wsl 'rm -f ~/upshot-agent/STOP')
        Good "machine B's test runner is running again"
    }
    Write-Host ''
    Read-Host '  Press Enter to close this window' | Out-Null
}
