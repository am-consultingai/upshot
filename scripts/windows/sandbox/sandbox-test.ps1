# Install, check and uninstall Upshot inside Windows Sandbox. Runs INSIDE the sandbox.
#
#   -Mode auto   unattended, for machine B's runner: silent install, every check, silent
#                uninstall, then the sandbox shuts itself down.
#   -Mode show   the same, visibly and still hands-off: the installer's own progress window
#                (no questions), the app opened in a visible Edge for -HoldSeconds, its log
#                streaming in a second window, the uninstaller's progress window. The
#                sandbox shuts down at the end unless -StayOpen.
#   -Mode watch  for a person at the keyboard: the real installer wizard (you click through
#                it), then as show, with a pause before the uninstall that waits for Enter.
#                The sandbox stays open at the end.
#
# Both modes print every step to this console as it starts and as it ends, and append the
# same lines to <Out>\steps.log. <In> holds Upshot-*-installer.zip (the installer and its
# .sha256 inside), the zip's .sha256 and upshot-test-signing.cer.
#
# Keep this file ASCII: Windows PowerShell 5.1 reads a script without a BOM as ANSI.
param(
    [ValidateSet('auto', 'show', 'watch')] [string]$Mode = 'auto',
    [string]$In = 'C:\in',
    [string]$Out = 'C:\out',
    # watch: how long the pause before the uninstall waits for Enter.
    [int]$PauseMinutes = 20,
    # show: how long the app stays up, in view, before the uninstall.
    [int]$HoldSeconds = 60,
    # show: leave the sandbox open at the end instead of shutting it down.
    [switch]$StayOpen
)
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$watch = $Mode -eq 'watch'
# show and watch both put the installer, the app, its log and the uninstaller on screen.
$visible = $Mode -ne 'auto'
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Host.UI.RawUI.WindowTitle = "Upshot sandbox test ($Mode)"
$steps = [System.Collections.ArrayList]::new()
$logFile = Join-Path $Out 'steps.log'

function Say([string]$text, [string]$color = 'Gray') {
    $line = "$(Get-Date -Format 'HH:mm:ss') $text"
    Write-Host $line -ForegroundColor $color
    $line | Out-File -Append -Encoding utf8 $logFile
}
# Before a step that takes a while, so the console never sits silent.
function Doing([string]$text) { Say "...  $text" 'Cyan' }
function Step([string]$name, [bool]$ok, [string]$detail) {
    [void]$steps.Add([ordered]@{ name = $name; ok = $ok; detail = $detail; at = (Get-Date).ToUniversalTime().ToString('o') })
    if ($ok) { Say "ok   $name : $detail" 'Green' } else { Say "FAIL $name : $detail" 'Red' }
    $steps | ConvertTo-Json -Depth 5 | Out-File -Encoding utf8 (Join-Path $Out 'steps.json')
}
# Headless Edge leaves helper processes behind. Start-Process -Wait in PowerShell 5.1 waits
# for the whole process tree, so it never returned (job 019 hung there for 20 minutes). Wait
# for msedge.exe itself, with a limit, and then end whatever used that profile folder.
function Stop-EdgeProfile([string]$profileDir) {
    Get-CimInstance Win32_Process -Filter "Name = 'msedge.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$profileDir*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
function Wait-Enter([int]$minutes) {
    $until = (Get-Date).AddMinutes($minutes)
    while ((Get-Date) -lt $until) {
        if ([Console]::KeyAvailable -and [Console]::ReadKey($true).Key -eq 'Enter') { return }
        Start-Sleep -Milliseconds 250
    }
}

"" | Out-File -Encoding utf8 $logFile
Say "Upshot sandbox test, mode $Mode, as $(whoami)" 'White'
Say "Log: $logFile" 'White'

$app = Join-Path $env:LOCALAPPDATA 'Programs\Upshot'
$exe = Join-Path $app 'upshot.exe'
$home_ = Join-Path $env:LOCALAPPDATA 'upshot'
$uninstallRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall'
$appId = '*6F1B7C2E*'

try {
    # 0. The installer travels inside a zip: B's host never handles the .exe itself.
    $zip = Get-ChildItem $In -Filter 'Upshot-*-installer.zip' | Select-Object -First 1
    if (-not $zip) { throw "no Upshot-*-installer.zip in $In" }
    Doing "checking and unpacking $($zip.Name)"
    $zipExpected = ((Get-Content -Raw (Join-Path $In "$($zip.Name).sha256")) -split '\s+')[0].ToLower()
    $zipActual = (Get-FileHash -Algorithm SHA256 $zip.FullName).Hash.ToLower()
    Step 'zip-sha256' ($zipExpected -eq $zipActual) "$($zip.Name) $zipActual"
    $unpacked = Join-Path $env:TEMP 'installer'
    Expand-Archive -Path $zip.FullName -DestinationPath $unpacked -Force

    # 1. The installer is the one A built.
    $setup = Get-ChildItem $unpacked -Filter 'Upshot-*-Setup.exe' | Select-Object -First 1
    $expected = ((Get-Content -Raw (Join-Path $unpacked "$($setup.Name).sha256")) -split '\s+')[0].ToLower()
    $actual = (Get-FileHash -Algorithm SHA256 $setup.FullName).Hash.ToLower()
    Step 'sha256' ($expected -eq $actual) "$($setup.Name) $actual"

    # 2. Signature, untrusted first (a stranger's machine), then with the test .cer trusted.
    # Self-signed: a stranger's machine reports UnknownError and still names the signer.
    $sig = Get-AuthenticodeSignature $setup.FullName
    Step 'signature-untrusted' ($null -ne $sig.SignerCertificate) "status=$($sig.Status) signer=$($sig.SignerCertificate.Subject)"
    # Trusting the test .cer needs the machine store, which needs admin, and the LogonCommand
    # runs without it (019: E_ACCESSDENIED). Try it; when refused, say so and carry on.
    try {
        Import-Certificate -FilePath (Join-Path $In 'upshot-test-signing.cer') -CertStoreLocation Cert:\LocalMachine\Root -ErrorAction Stop | Out-Null
        Import-Certificate -FilePath (Join-Path $In 'upshot-test-signing.cer') -CertStoreLocation Cert:\LocalMachine\TrustedPublisher -ErrorAction Stop | Out-Null
        $sig2 = Get-AuthenticodeSignature $setup.FullName
        Step 'signature-trusted' ($sig2.Status -eq 'Valid') "status=$($sig2.Status)"
    } catch { Say "skip signature-trusted : not elevated, the machine certificate store refused ($($_.Exception.Message))" 'DarkYellow' }

    # 3. Per-user install from a local copy, which is what a download looks like.
    $local = if ($visible) { Join-Path $env:USERPROFILE 'Downloads' } else { $env:TEMP }
    New-Item -ItemType Directory -Force -Path $local | Out-Null
    $setupLocal = Join-Path $local $setup.Name
    Copy-Item $setup.FullName $setupLocal
    if ($visible) { Say "Installer placed in Downloads: $setupLocal" 'White' }
    $installLog = Join-Path $Out 'install.log'
    if ($watch) {
        Say "The installer is opening. Click through it as a user would." 'Yellow'
        Say "(Ticking 'Launch Upshot' on the last page is fine; this script carries on after it closes.)" 'Yellow'
        $installArgs = @('/CURRENTUSER', "/LOG=`"$installLog`"")
    } elseif ($visible) {
        Doing "installing: the installer's progress window, no questions (/SILENT)"
        $installArgs = @('/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', "/LOG=`"$installLog`"")
    } else {
        Doing 'installing silently (/VERYSILENT)'
        $installArgs = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', "/LOG=`"$installLog`"")
    }
    $t0 = Get-Date
    # Setup.exe waits for its own .tmp stage and returns that exit code, so wait for Setup.exe
    # alone: -Wait would also wait for Upshot itself when the wizard's "Launch Upshot" starts it.
    $p = Start-Process -FilePath $setupLocal -ArgumentList $installArgs -PassThru
    $null = $p.Handle  # without a handle taken now, 5.1 loses the exit code
    $p.WaitForExit()
    $uninstKey = Get-ChildItem $uninstallRoot -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like $appId }
    $hklm = Get-ChildItem 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like $appId }
    Step 'install' (($p.ExitCode -eq 0) -and (Test-Path $exe) -and $uninstKey -and -not $hklm) "exit=$($p.ExitCode) seconds=$([int]((Get-Date) - $t0).TotalSeconds) exe=$(Test-Path $exe) hkcuKey=$([bool]$uninstKey) hklmKey=$([bool]$hklm)"
    if (-not (Test-Path $exe)) { throw 'upshot.exe is not installed; nothing further to check' }
    Step 'start-menu' (Test-Path (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Upshot.lnk')) 'shortcut in the user Start menu'
    $exeSig = Get-AuthenticodeSignature $exe
    Step 'app-signed' ($null -ne $exeSig.SignerCertificate) "status=$($exeSig.Status)"
    Step 'bootstrap' (Test-Path $home_) "app home exists=$(Test-Path $home_)"

    # The app's own log, live, in a window of its own.
    if ($visible) {
        $appLog = Join-Path $home_ 'logs\app.log'
        $tail = "`$Host.UI.RawUI.WindowTitle = 'Upshot app.log'; while (-not (Test-Path '$appLog')) { Start-Sleep 1 }; Get-Content -Wait -Tail 200 '$appLog'"
        Start-Process powershell.exe -ArgumentList @('-NoProfile', '-NoExit', '-Command', $tail) | Out-Null
    }

    # 4. Frozen selftest, the suites that need no microphone, speakers or speech model.
    #    Each one starts upshot.exe afresh, which is most of this step's time.
    foreach ($suite in @('imports', 'clock', 'config', 'db', 'queue', 'audio-synthetic', 'pipeline', 'api')) {
        Doing "selftest $suite"
        $report = Join-Path $Out "selftest-$suite.json"
        $t = Get-Date
        $sp = Start-Process -FilePath $exe -ArgumentList @('--selftest', $suite, '--report', "`"$report`"", '--quiet') -Wait -PassThru
        $failed = ''
        if (Test-Path $report) {
            $r = Get-Content -Raw -Encoding UTF8 $report | ConvertFrom-Json
            $failed = ($r.checks | Where-Object { -not $_.ok } | ForEach-Object { "$($_.name): $($_.detail)" }) -join ' | '
        }
        Step "selftest-$suite" ($sp.ExitCode -eq 0) "exit=$($sp.ExitCode) seconds=$([int]((Get-Date) - $t).TotalSeconds) report=$(Test-Path $report) $failed"
    }

    # 5. Start the app like a person would and talk to it. In watch mode the wizard's
    #    "Launch Upshot" may already have started it; then that copy is the one tested.
    $app_p = Get-Process -Name upshot -ErrorAction SilentlyContinue | Select-Object -First 1
    $startedBy = 'the installer'
    $shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Upshot.lnk'
    if (-not $app_p -and $visible -and (Test-Path $shortcut)) {
        Doing 'starting Upshot from its Start-menu shortcut'
        Start-Process -FilePath $shortcut | Out-Null
        for ($i = 0; $i -lt 30 -and -not $app_p; $i++) {
            Start-Sleep -Seconds 1
            $app_p = Get-Process -Name upshot -ErrorAction SilentlyContinue | Select-Object -First 1
        }
        $startedBy = 'the Start-menu shortcut'
    }
    if (-not $app_p) {
        Doing 'starting Upshot'
        $app_p = Start-Process -FilePath $exe -PassThru
        $startedBy = 'this script'
    }
    $t1 = Get-Date
    $portFile = Join-Path $home_ 'server.port'
    $port = $null
    for ($i = 0; $i -lt 90 -and -not $port; $i++) {
        if (Test-Path $portFile) { $port = (Get-Content -Raw $portFile).Trim() } else { Start-Sleep -Seconds 2 }
    }
    Step 'app-start' ([bool]$port) "started by $startedBy, port=$port seconds=$([int]((Get-Date) - $t1).TotalSeconds) alive=$(-not $app_p.HasExited)"
    $edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($port) {
        $base = "http://127.0.0.1:$port"
        if ($visible -and $edge) {
            Say "Opening $base in Edge. The tray icon is in the taskbar's corner (^ if hidden)." 'Yellow'
            Start-Process -FilePath $edge -ArgumentList @('--no-first-run', '--no-default-browser-check', '--start-maximized', "--user-data-dir=$env:TEMP\edge-visible", $base) | Out-Null
        }
        foreach ($path in @('/api/status', '/api/settings', '/api/model', '/api/meetings', '/')) {
            try {
                $r = Invoke-WebRequest -UseBasicParsing -Uri "$base$path" -TimeoutSec 30
                $r.Content | Out-File -Encoding utf8 (Join-Path $Out ("http" + ($path -replace '[/]', '_') + '.txt'))
                Step "http $path" ($r.StatusCode -eq 200) "status=$($r.StatusCode) bytes=$($r.RawContentLength)"
            } catch { Step "http $path" $false "$($_.Exception.Message)" }
        }
        # The UI renders (JavaScript ran, no blank page): headless Edge dumps the DOM. Its own
        # profile folder, so it never touches the visible window's.
        foreach ($route in @('/welcome', '/', '/search', '/settings', '/actions')) {
            if (-not $edge) { Step "ui $route" $false 'no msedge.exe'; break }
            Doing "rendering $route in headless Edge"
            $dump = Join-Path $Out ("dom" + ($route -replace '[/]', '_') + '.html')
            $headless = "$env:TEMP\edge-headless"
            $ep = Start-Process -FilePath $edge -ArgumentList @('--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', "--user-data-dir=$headless", '--virtual-time-budget=15000', '--dump-dom', "$base$route") -RedirectStandardOutput $dump -PassThru -WindowStyle Hidden
            $finished = $ep.WaitForExit(60000)
            Stop-EdgeProfile 'edge-headless'
            $html = if (Test-Path $dump) { Get-Content -Raw -Encoding UTF8 $dump } else { '' }
            $rendered = $html -match 'data-testid="app"'
            Step "ui $route" $rendered "edgeFinished=$finished bytes=$($html.Length) appRoot=$rendered"
        }
        # A second launch must not start a second server; it opens the running one and exits.
        Doing 'launching a second copy (it should hand over to the first and exit)'
        $second = Start-Process -FilePath $exe -PassThru
        $exited = $second.WaitForExit(60000)
        Step 'second-launch' ($exited -and -not $app_p.HasExited) "secondExited=$exited exit=$(if ($exited) { $second.ExitCode }) firstAlive=$(-not $app_p.HasExited)"
        if (-not $visible) {
            Get-Process -Name msedge -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        }
    }
    Copy-Item -Recurse -Force (Join-Path $home_ 'logs') (Join-Path $Out 'app-logs') -ErrorAction SilentlyContinue

    if ($watch) {
        $bad = @($steps | Where-Object { -not $_.ok }).Count
        Say "Checks so far: $($steps.Count - $bad) ok, $bad failed." $(if ($bad) { 'Red' } else { 'Green' })
        Say "Look around the app now. Press Enter in this window to uninstall (it continues by itself in $PauseMinutes minutes)." 'Yellow'
        Wait-Enter $PauseMinutes
        Copy-Item -Recurse -Force (Join-Path $home_ 'logs') (Join-Path $Out 'app-logs') -ErrorAction SilentlyContinue
    } elseif ($visible) {
        $bad = @($steps | Where-Object { -not $_.ok }).Count
        Say "Checks so far: $($steps.Count - $bad) ok, $bad failed." $(if ($bad) { 'Red' } else { 'Green' })
        Doing "leaving the app in view for $HoldSeconds seconds, then uninstalling"
        Start-Sleep -Seconds $HoldSeconds
        Copy-Item -Recurse -Force (Join-Path $home_ 'logs') (Join-Path $Out 'app-logs') -ErrorAction SilentlyContinue
    }

    # 6. Uninstall with the app running (the uninstaller must close it). Watch mode shows
    #    the uninstaller's progress window in show and watch; auto shows nothing.
    $unins = Get-ChildItem $app -Filter 'unins*.exe' | Select-Object -First 1
    $uSig = Get-AuthenticodeSignature $unins.FullName
    Step 'uninstaller-signed' ($null -ne $uSig.SignerCertificate) "$($unins.Name) status=$($uSig.Status)"
    Doing 'uninstalling'
    $quiet = if ($visible) { '/SILENT' } else { '/VERYSILENT' }
    $up = Start-Process -FilePath $unins.FullName -ArgumentList @($quiet, '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$(Join-Path $Out 'uninstall.log')`"") -Wait -PassThru
    Start-Sleep -Seconds 10
    $left = if (Test-Path $app) { (Get-ChildItem -Recurse $app | Measure-Object).Count } else { 0 }
    $key = Get-ChildItem $uninstallRoot -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -like $appId }
    $running = Get-Process -Name upshot -ErrorAction SilentlyContinue
    Step 'uninstall' (($up.ExitCode -eq 0) -and $left -eq 0 -and -not $key -and -not $running) "exit=$($up.ExitCode) filesLeft=$left key=$([bool]$key) stillRunning=$([bool]$running)"
    $kept = @(Get-ChildItem $home_ -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    Step 'user-data-kept' ((Test-Path (Join-Path $home_ 'index.db')) -or $kept.Count -gt 0) "app home $home_ holds: $($kept -join ', ')"
    Step 'shortcut-removed' (-not (Test-Path (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Upshot.lnk'))) 'Start menu entry gone'
} catch {
    Step 'script' $false "$($_ | Out-String)"
}

$bad = @($steps | Where-Object { -not $_.ok }).Count
Say "Finished: $($steps.Count - $bad) ok, $bad failed. Results in $Out" $(if ($bad) { 'Red' } else { 'Green' })
"done $(Get-Date -Format o)" | Out-File -Encoding utf8 (Join-Path $Out 'DONE.txt')
if ($watch -or $StayOpen) {
    Say 'The sandbox stays open. Close its window when you are done; everything in it is discarded.' 'Yellow'
} else {
    Start-Sleep -Seconds 5
    Stop-Computer -Force
}
