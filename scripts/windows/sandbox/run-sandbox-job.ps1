# Machine B, host side: run one Sandbox job start to finish, unattended, in one process.
#
#   powershell -ExecutionPolicy Bypass -File run-sandbox-job.ps1 -Work C:\upshot-work\<job>
#
# <Work> holds install.wsb and in\ (the zips, their .sha256 files, the .cer and
# sandbox-test.ps1). The guest writes to <Work>\out. When this script ends it writes
# <Work>\host-result.json, which is the one file the runner waits for:
#   status   done | start_failed | timeout | bad_input
#   attempts, seconds, and the times of each stage
#
# It replaces the runner doing each step as its own command, which cost minutes of overhead
# per job. Every wait here is for something that is actually happening: the 3-minute pause
# before a start is taken only when a sandbox was closed just before (a start right after a
# close failed in jobs 015 and 021).
#
# Nothing is installed on this machine. Keep this file ASCII.
param(
    [Parameter(Mandatory = $true)] [string]$Work,
    [int]$TimeoutMinutes = 40,
    # No steps.log from the guest within this long means the sandbox did not start.
    [int]$StartMinutes = 3
)
$ErrorActionPreference = 'Continue'
$out = Join-Path $Work 'out'
$hostLog = Join-Path $Work 'host.log'
$resultFile = Join-Path $Work 'host-result.json'
$started = Get-Date
$times = [ordered]@{}
"" | Out-File -Encoding utf8 $hostLog
function Note([string]$text) { "$(Get-Date -Format 'HH:mm:ss') $text" | Out-File -Append -Encoding utf8 $hostLog }
function Finish([string]$status, [int]$attempts, [string]$detail) {
    [ordered]@{
        status = $status; attempts = $attempts; detail = $detail
        seconds = [int]((Get-Date) - $started).TotalSeconds; times = $times
    } | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 $resultFile
    Note "finished: $status ($detail)"
    exit 0
}
function Sandboxes { @(Get-Process -Name 'WindowsSandbox*' -ErrorAction SilentlyContinue) }
function Close-Sandboxes {
    Sandboxes | Where-Object { $_.MainWindowHandle -ne 0 } | ForEach-Object { [void]$_.CloseMainWindow() }
    for ($i = 0; $i -lt 30 -and (Sandboxes).Count -gt 0; $i++) { Start-Sleep -Seconds 1 }
    Sandboxes | Stop-Process -Force -ErrorAction SilentlyContinue
    for ($i = 0; $i -lt 120 -and (Sandboxes).Count -gt 0; $i++) { Start-Sleep -Seconds 1 }
}
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class PW {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
}
"@
function Shoot-Sandbox([string]$file) {
    foreach ($p in Sandboxes) {
        $h = $p.MainWindowHandle
        if ($h -eq [IntPtr]::Zero) { continue }
        $r = New-Object PW+RECT
        [void][PW]::GetWindowRect($h, [ref]$r)
        $w = $r.R - $r.L; $ht = $r.B - $r.T
        if ($w -le 0 -or $ht -le 0) { continue }
        $bmp = New-Object Drawing.Bitmap $w, $ht
        $g = [Drawing.Graphics]::FromImage($bmp)
        $dc = $g.GetHdc(); [void][PW]::PrintWindow($h, $dc, 2); $g.ReleaseHdc($dc)
        $bmp.Save($file); $g.Dispose(); $bmp.Dispose()
        Note "screenshot of the Sandbox window: $file"
        return
    }
}

# 1. Inputs: every zip matches its .sha256 (the guest checks the installers inside).
$in = Join-Path $Work 'in'
$wsb = Join-Path $Work 'install.wsb'
if (-not (Test-Path $wsb) -or -not (Test-Path $in)) { Finish 'bad_input' 0 "missing $wsb or $in" }
foreach ($zip in Get-ChildItem $in -Filter '*.zip') {
    $want = ((Get-Content -Raw "$($zip.FullName).sha256") -split '\s+')[0].ToLower()
    $have = (Get-FileHash -Algorithm SHA256 $zip.FullName).Hash.ToLower()
    if ($want -ne $have) { Finish 'bad_input' 0 "$($zip.Name) does not match its .sha256" }
    Note "ok $($zip.Name) $have"
}

# 2. No sandbox may be running; if one was, give Windows 3 minutes to tear it down.
if ((Sandboxes).Count -gt 0) {
    Note 'a sandbox is open; closing it, then waiting 3 minutes'
    Close-Sandboxes
    Start-Sleep -Seconds 180
}

# 3. Start, and retry once if the guest never begins.
$stepsLog = Join-Path $out 'steps.log'
$done = Join-Path $out 'DONE.txt'
for ($attempt = 1; $attempt -le 2; $attempt++) {
    # Set a used out\ aside rather than delete it: nothing is deleted on B's host.
    if (Test-Path $out) { Rename-Item $out ("out-" + (Get-Date).ToString('HHmmss')) }
    New-Item -ItemType Directory -Force -Path $out | Out-Null
    Note "start attempt $attempt"
    $times["start_$attempt"] = (Get-Date).ToUniversalTime().ToString('o')
    # Through Explorer, as a double-click does. Started directly from the runner's process
    # tree (WSL, a headless console), Sandbox failed to initialise on every start from the
    # evening of 2026-09-26, while the same machine opened it fine from the Start menu.
    Start-Process -FilePath (Join-Path $env:WINDIR 'explorer.exe') -ArgumentList "`"$wsb`"" | Out-Null
    $deadline = (Get-Date).AddMinutes($StartMinutes)
    while ((Get-Date) -lt $deadline -and -not (Test-Path $stepsLog)) { Start-Sleep -Seconds 2 }
    if (Test-Path $stepsLog) {
        $times['guest_started'] = (Get-Date).ToUniversalTime().ToString('o')
        Note 'the guest started'
        break
    }
    Note "no guest after $StartMinutes minutes: the sandbox did not start"
    Shoot-Sandbox (Join-Path $Work "start-failed-$attempt.png")
    Close-Sandboxes
    if ($attempt -eq 2) { Finish 'start_failed' 2 'Windows Sandbox did not start twice, 3 minutes apart' }
    Start-Sleep -Seconds 180
}

# 4. Wait for the guest to finish, then for the sandbox to close.
$deadline = (Get-Date).AddMinutes($TimeoutMinutes)
while ((Get-Date) -lt $deadline -and -not (Test-Path $done)) { Start-Sleep -Seconds 5 }
if (-not (Test-Path $done)) {
    Shoot-Sandbox (Join-Path $Work 'timeout.png')
    Close-Sandboxes
    Finish 'timeout' $attempt "no DONE.txt after $TimeoutMinutes minutes"
}
$times['guest_done'] = (Get-Date).ToUniversalTime().ToString('o')
# The guest either shuts itself down or (-NoShutdown) leaves it to us: give it 15 s, then
# close the window from here.
for ($i = 0; $i -lt 15 -and (Sandboxes).Count -gt 0; $i++) { Start-Sleep -Seconds 1 }
if ((Sandboxes).Count -gt 0) { Note 'closing the sandbox from the host'; Close-Sandboxes }
$times['closed'] = (Get-Date).ToUniversalTime().ToString('o')
Finish 'done' $attempt 'the guest finished; see out\steps.json'
