<#
.SYNOPSIS
  Remove Claude Code from this machine, whichever way it was installed.

.DESCRIPTION
  A reset for testing the Settings screen's Install button from a genuinely clean state.
  It removes every install method rather than the one that happens to be on PATH - a
  machine can carry several at once, and leaving any behind means the next test starts
  dirty and lies about it.

  It also takes the configuration and sign-in, because "not installed" has to mean it.

  Meant to be launched by double-clicking remove-claude.cmd. Nothing here needs elevation.
#>
[CmdletBinding()]
param()

# Deliberately NOT "Stop": this script's whole job is to keep going through failures and
# report each one. A single missing path must not abandon the other seven.
$ErrorActionPreference = "Continue"

function Write-Step { param([string] $Text) Write-Host "`n=== $Text" -ForegroundColor Cyan }
function Write-Good { param([string] $Text) Write-Host "  $Text" -ForegroundColor Green }
function Write-Warn { param([string] $Text) Write-Host "  $Text" -ForegroundColor Yellow }
function Write-Bad  { param([string] $Text) Write-Host "  $Text" -ForegroundColor Red }

Write-Step "Stopping anything that would hold the files open"

# The application first, and not out of politeness. Its settings page runs
# `claude --version` and `claude auth status` on every load, which re-locks the
# executable and recreates ~\.claude the moment it is deleted. Leaving it running means
# racing it and losing - which is exactly what happened the first time this was tried.
$app = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "app\.main" })
if ($app.Count -gt 0) {
    Write-Warn "stopping the app ($($app.Count) process(es)) - it respawns claude on every settings load"
    foreach ($proc in $app) { Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 750
} else { Write-Host "    the app is not running" }

$running = @(Get-Process claude -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    Write-Warn "stopping $($running.Count) claude process(es) - a running exe cannot be deleted"
    Stop-Process -Name claude -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 750
} else { Write-Host "    no claude process running" }

Write-Step "Package managers"

if (Get-Command winget -ErrorAction SilentlyContinue) {
    $listed = (winget list --id Anthropic.ClaudeCode --exact 2>&1 | Out-String)
    if ($listed -match "Anthropic\.ClaudeCode") {
        # Output is shown, never swallowed: when winget refuses, its reason is the
        # entire diagnosis, and discarding it is how the first attempt failed silently.
        winget uninstall --id Anthropic.ClaudeCode --exact --disable-interactivity 2>&1 |
            ForEach-Object { Write-Host "    $_" }
    } else { Write-Host "    no winget package registered" }
} else { Write-Warn "winget is not on this machine - skipping" }

if (Get-Command npm -ErrorAction SilentlyContinue) {
    if ((npm ls -g --depth 0 2>$null | Out-String) -match "@anthropic-ai/claude-code") {
        npm uninstall -g @anthropic-ai/claude-code 2>&1 | ForEach-Object { Write-Host "    $_" }
    } else { Write-Host "    no npm install" }
} else { Write-Host "    npm is not on this machine - skipping" }

Write-Step "Files"

# Whatever the package managers left, the native installer's own layout, and the state
# every method shares. The winget folder is matched by wildcard on purpose: its suffix
# names the package *source*, so hardcoding one machine's is how this silently misses
# on the next one.
$targets = @(
    "$env:USERPROFILE\.local\bin\claude.exe",
    "$env:USERPROFILE\.local\share\claude",
    "$env:USERPROFILE\.local\state\claude",
    "$env:LOCALAPPDATA\Microsoft\WinGet\Links\claude.exe",
    "$env:USERPROFILE\.claude",
    "$env:USERPROFILE\.claude.json"
)
$targets += @(Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Anthropic.ClaudeCode*" -Directory -ErrorAction SilentlyContinue |
    ForEach-Object { $_.FullName })

$stubborn = @()
foreach ($path in $targets) {
    if (-not (Test-Path -LiteralPath $path)) { continue }
    Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $path) {
        Start-Sleep -Seconds 2   # a process caught mid-exit holds its handle a moment longer
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $path) { $stubborn += $path; Write-Bad "could not remove $path" }
    else { Write-Good "removed $path" }
}

Write-Step "Result"

$still = Get-Command claude -ErrorAction SilentlyContinue
if ($stubborn.Count -gt 0) {
    Write-Bad "$($stubborn.Count) path(s) survived - something still has them open:"
    foreach ($path in $stubborn) { Write-Host "    $path" }
    Write-Host ""
    Write-Host "  Close any open Claude Code window and run this again."
} elseif ($still) {
    Write-Bad "claude still resolves to $($still.Source)"
    Write-Host "  That is an install this script does not know how to find - tell Claude about it."
} else {
    Write-Good "Claude Code is gone, including its sign-in."
    Write-Host ""
    Write-Host "  Start the app and open Settings: the Claude Code row should now read"
    Write-Host "  'Not installed' and offer an Install button."
}
