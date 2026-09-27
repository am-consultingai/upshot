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
    [switch]$StayOpen,
    # Leave the shutdown to the host (run-sandbox-job.ps1 closes the window). A sandbox that
    # shut itself down with Stop-Computer was followed, twice, by a next start that failed to
    # initialise until B restarted (jobs 023 and 026).
    [switch]$NoShutdown,
    # A verified model folder (read-only, mapped from the host). The installer then skips
    # its download (/MERGETASKS="!speechmodel") and the model is copied into place: the
    # download is proven (jobs 025, 032), and this saves minutes per run.
    [string]$ModelCache = '',
    # Save a picture of the sandbox's screen every this many seconds into <Out>\screens,
    # for whoever could not watch it happen. 0 = none.
    [int]$ScreenshotSeconds = 0
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
function Save-Screen([string]$name) {
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
        $bmp.Save((Join-Path $Out "$name.png"), [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
    } catch { }
}
# The tray icon, by its tooltip ("Upshot - ..."): on the taskbar, or among the hidden icons,
# which is where Windows 11 puts a new app's icon. Returns @(element, where) or $null.
function Find-TrayIcon {
    Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
    $AE = [System.Windows.Automation.AutomationElement]
    $scope = [System.Windows.Automation.TreeScope]::Descendants
    $button = New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)
    $taskbar = $AE::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, (New-Object System.Windows.Automation.PropertyCondition($AE::ClassNameProperty, 'Shell_TrayWnd')))
    if (-not $taskbar) { return $null }
    $buttons = @($taskbar.FindAll($scope, $button))
    $hit = $buttons | Where-Object { $_.Current.Name -like 'Upshot*' } | Select-Object -First 1
    if ($hit) { return @($hit, 'taskbar') }
    $chevron = $buttons | Where-Object { $_.Current.Name -match 'hidden icons' } | Select-Object -First 1
    if (-not $chevron) { return $null }
    try { $chevron.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch { return $null }
    Start-Sleep -Milliseconds 1500
    foreach ($w in $AE::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
        $hit = @($w.FindAll($scope, $button)) | Where-Object { $_.Current.Name -like 'Upshot*' } | Select-Object -First 1
        if ($hit) { return @($hit, "hidden icons ($($w.Current.ClassName))") }
    }
    return $null
}
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class Mouse {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
  [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
  // UI Automation reports physical pixels; without DPI awareness SetCursorPos takes scaled
  // ones, and on a scaled display the click misses the icon.
  public static void RightClick(int x, int y) { SetProcessDPIAware(); SetCursorPos(x, y); mouse_event(0x0008, 0, 0, 0, IntPtr.Zero); mouse_event(0x0010, 0, 0, 0, IntPtr.Zero); }
}
public static class PopupMenu {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int n);
  [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
  // Open Upshot's tray menu the way a right-click does: pystray's hidden window
  // ("upshot<id>SystemTrayIcon") gets its notify message (WM_USER + 11) with WM_RBUTTONUP.
  // No mouse: clicking by coordinates missed on a scaled display.
  public static bool OpenTray() {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, l) => {
      var c = new System.Text.StringBuilder(128); GetClassName(h, c, 128);
      if (System.Text.RegularExpressions.Regex.IsMatch(c.ToString(), "^upshot[0-9]+SystemTrayIcon$")) { found = h; return false; }
      return true; }, IntPtr.Zero);
    if (found == IntPtr.Zero) return false;
    return PostMessage(found, 0x0400 + 11, IntPtr.Zero, (IntPtr)0x0205);
  }
  [DllImport("user32.dll")] static extern IntPtr FindWindowEx(IntPtr parent, IntPtr after, string cls, string name);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  // The process that owns the visible context menu (window class #32768), or 0. Windows
  // keeps a hidden, empty #32768 window around, so only a visible one counts. The items
  // themselves cannot be read from another process (GetMenuItemCount gives -1, UI
  // Automation sees none), so the screenshot shows them.
  public static uint OpenMenuOwner() {
    for (IntPtr h = FindWindowEx(IntPtr.Zero, IntPtr.Zero, "#32768", null); h != IntPtr.Zero; h = FindWindowEx(IntPtr.Zero, h, "#32768", null)) {
      if (IsWindowVisible(h)) { uint pid; GetWindowThreadProcessId(h, out pid); return pid; }
    }
    return 0;
  }
}
"@
# One JavaScript expression evaluated in the page Edge has open on <port> (DevTools protocol).
function Invoke-PageJs([int]$Port, [string]$Expression) {
    $targets = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json" -TimeoutSec 10
    $page = @($targets | Where-Object { $_.type -eq 'page' }) | Select-Object -First 1
    $ws = New-Object System.Net.WebSockets.ClientWebSocket
    $ws.ConnectAsync([Uri]$page.webSocketDebuggerUrl, [Threading.CancellationToken]::None).Wait()
    try {
        $message = @{ id = 1; method = 'Runtime.evaluate'; params = @{ expression = $Expression; returnByValue = $true } } | ConvertTo-Json -Depth 5 -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($message)
        $ws.SendAsync((New-Object ArraySegment[byte] (, $bytes)), 'Text', $true, [Threading.CancellationToken]::None).Wait()
        $buffer = New-Object byte[] 65536
        $text = ''
        do {
            $r = $ws.ReceiveAsync((New-Object ArraySegment[byte] (, $buffer)), [Threading.CancellationToken]::None).Result
            $text += [Text.Encoding]::UTF8.GetString($buffer, 0, $r.Count)
        } while (-not $r.EndOfMessage)
        return (($text | ConvertFrom-Json).result.result.value)
    } finally { $ws.Dispose() }
}
# One move through first-run setup, as a person would make it: pick "notify me" on the
# recording step, then Finish, else Skip, else Next. Says what it did.
$SetupMove = @'
(() => {
  const flow = document.querySelector('[data-testid=setup-flow]');
  if (!flow) return 'left:' + location.pathname;
  const step = flow.dataset.step;
  const q = (id) => document.querySelector('[data-testid=' + id + ']');
  if (step === 'capture' && q('capture-shadow')) q('capture-shadow').click();
  const b = q('setup-finish') || q('setup-skip') || q('setup-next');
  if (b && !b.disabled) { b.click(); return 'clicked:' + step + ':' + b.dataset.testid; }
  return 'waiting:' + step;
})()
'@
function Wait-Enter([int]$minutes) {
    $until = (Get-Date).AddMinutes($minutes)
    while ((Get-Date) -lt $until) {
        if ([Console]::KeyAvailable -and [Console]::ReadKey($true).Key -eq 'Enter') { return }
        Start-Sleep -Milliseconds 250
    }
}

"" | Out-File -Encoding utf8 $logFile
Say "Upshot sandbox test, mode $Mode, as $(whoami)" 'White'
if ($ScreenshotSeconds -gt 0) {
    # A process of its own, so it keeps shooting while this script waits on the installer.
    # It stops when DONE.txt appears, or after 400 pictures.
    $screens = Join-Path $Out 'screens'
    New-Item -ItemType Directory -Force -Path $screens | Out-Null
    $shooter = Join-Path $env:TEMP 'screens.ps1'
    @"
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
for (`$i = 0; `$i -lt 400 -and -not (Test-Path '$(Join-Path $Out 'DONE.txt')'); `$i++) {
    try {
        `$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        `$bmp = New-Object System.Drawing.Bitmap `$b.Width, `$b.Height
        `$g = [System.Drawing.Graphics]::FromImage(`$bmp)
        `$g.CopyFromScreen(`$b.Location, [System.Drawing.Point]::Empty, `$b.Size)
        `$bmp.Save((Join-Path '$screens' ((Get-Date).ToString('HHmmss') + '.png')), [System.Drawing.Imaging.ImageFormat]::Png)
        `$g.Dispose(); `$bmp.Dispose()
    } catch { }
    Start-Sleep -Seconds $ScreenshotSeconds
}
"@ | Out-File -Encoding ascii $shooter
    Start-Process powershell.exe -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', $shooter) -WindowStyle Hidden | Out-Null
    Say "screenshots every $ScreenshotSeconds s into $screens" 'DarkGray'
}
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

    # 2b. Upgrade: with previous-Upshot-installer.zip in <In>, that older build is installed and
    #     running first, and this one is installed over it, as an update reaches a user.
    $previousZip = Join-Path $In 'previous-Upshot-installer.zip'
    $oldProc = $null
    $oldCommit = ''
    $portFile = Join-Path $home_ 'server.port'
    if (Test-Path $previousZip) {
        Doing 'installing the previous build first (silently, without the model), then starting it'
        $prevDir = Join-Path $env:TEMP 'previous'
        Expand-Archive -Path $previousZip -DestinationPath $prevDir -Force
        $prevSetup = Get-ChildItem $prevDir -Filter 'Upshot-*-Setup.exe' | Select-Object -First 1
        $pp = Start-Process -FilePath $prevSetup.FullName -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/MERGETASKS="!speechmodel"', "/LOG=`"$(Join-Path $Out 'install-previous.log')`"") -PassThru
        $null = $pp.Handle
        $pp.WaitForExit()
        $oldProc = Start-Process -FilePath $exe -PassThru
        for ($i = 0; $i -lt 60 -and -not (Test-Path $portFile); $i++) { Start-Sleep -Seconds 1 }
        try {
            $port0 = (Get-Content -Raw $portFile).Trim()
            $oldCommit = ((Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port0/api/status" -TimeoutSec 20).Content | ConvertFrom-Json).build.commit
        } catch { }
        Step 'previous-running' ((-not $oldProc.HasExited) -and [bool]$oldCommit) "installer exit=$($pp.ExitCode) commit=$oldCommit pid=$($oldProc.Id)"
    }

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
    } elseif ($visible -and $ModelCache) {
        Doing "installing: the installer's progress window (/SILENT), without the model download (it comes from $ModelCache)"
        $installArgs = @('/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/MERGETASKS="!speechmodel"', "/LOG=`"$installLog`"")
    } elseif ($visible) {
        Doing "installing: the installer's progress window, no questions (/SILENT), and the speech model download (about 3 GB, several minutes)"
        $installArgs = @('/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', "/LOG=`"$installLog`"")
    } else {
        # The runner's jobs skip the 3 GB speech model: they test the install, not the download.
        Doing 'installing silently (/VERYSILENT), without the speech model download'
        $installArgs = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CURRENTUSER', '/MERGETASKS="!speechmodel"', "/LOG=`"$installLog`"")
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
    if ($oldProc) {
        # The installer asks a running copy to quit (--quit), then stops what is left after 10 s.
        # The previous build may predate --quit, so either way counts; what matters is it is gone.
        $how = (Select-String -Path $installLog -Pattern 'asking it to quit|taskkill' -ErrorAction SilentlyContinue | ForEach-Object { $_.Line.Substring([Math]::Min(24, $_.Line.Length)) }) -join ' | '
        Step 'upgrade-stopped-previous' $oldProc.HasExited "previous pid $($oldProc.Id) exited=$($oldProc.HasExited); installer: $how"
        # Its port file would point the next check at a server that is gone.
        Remove-Item -Force $portFile -ErrorAction SilentlyContinue
    }
    Step 'start-menu' (Test-Path (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Upshot.lnk')) 'shortcut in the user Start menu'
    $exeSig = Get-AuthenticodeSignature $exe
    Step 'app-signed' ($null -ne $exeSig.SignerCertificate) "status=$($exeSig.Status)"
    Step 'bootstrap' (Test-Path $home_) "app home exists=$(Test-Path $home_)"
    # The installer's download step (app/prepare.py): the model is in place and verified.
    if ($Mode -ne 'auto') {
        $model = Join-Path $home_ 'models\asr\ivrit-ai__whisper-large-v3-ct2'
        if ($ModelCache) {
            Doing "copying the speech model from $ModelCache"
            $t = Get-Date
            New-Item -ItemType Directory -Force -Path $model | Out-Null
            Copy-Item -Path (Join-Path $ModelCache '*') -Destination $model -Recurse -Force
            Copy-Item -Path (Join-Path $ModelCache '.upshot-verified') -Destination $model -Force -ErrorAction SilentlyContinue
            Say "model copied in $([int]((Get-Date) - $t).TotalSeconds) s" 'DarkGray'
        }
        $verified = Test-Path (Join-Path $model '.upshot-verified')
        $mb = if (Test-Path $model) { [int]((Get-ChildItem -Recurse -File $model | Measure-Object Length -Sum).Sum / 1MB) } else { 0 }
        $note = (Select-String -Path $installLog -Pattern 'Prepare ended' -ErrorAction SilentlyContinue | Select-Object -Last 1).Line
        Step 'speech-model' $verified "verified=$verified size=${mb} MB installer: $note"
    }

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
        # Which build is this? The freeze knows its own version and commit (app/version.py).
        try {
            $b = (Get-Content -Raw -Encoding UTF8 (Join-Path $Out 'http_api_status.txt') | ConvertFrom-Json).build
            Say "Installed Upshot $($b.version), commit $($b.commit), built $($b.built)" 'White'
            if ($oldCommit) { Step 'upgraded-build' ($b.commit -ne $oldCommit) "was $oldCommit, now $($b.commit)" }
            # The build carries the Google client, so Settings can connect a calendar.
            try {
                $cal = Invoke-RestMethod -Uri "$base/api/calendar/status" -TimeoutSec 20
                $configured = if ($null -ne $cal.configured) { $cal.configured } else { $cal.auth.configured }
                Step 'calendar-client' ([bool]$configured) "configured=$configured"
            } catch { Step 'calendar-client' $false "$($_.Exception.Message)" }
        } catch { Say "skip build-info : $($_.Exception.Message)" 'DarkYellow' }
        # The UI renders (JavaScript ran, no blank page): headless Edge dumps the DOM. Its own
        # profile folder, so it never touches the visible window's. --timeout, not
        # --virtual-time-budget: the app's open event stream keeps virtual time from ever
        # running out, so Edge never dumped anything (found by machine A, 2026-09-26).
        foreach ($route in @('/welcome', '/', '/search', '/settings', '/actions')) {
            if (-not $edge) { Step "ui $route" $false 'no msedge.exe'; break }
            Doing "rendering $route in headless Edge"
            $dump = Join-Path $Out ("dom" + ($route -replace '[/]', '_') + '.html')
            $headless = "$env:TEMP\edge-headless"
            $ep = Start-Process -FilePath $edge -ArgumentList @('--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', "--user-data-dir=$headless", '--timeout=15000', '--dump-dom', "$base$route") -RedirectStandardOutput $dump -PassThru -WindowStyle Hidden
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
        # First-run setup, clicked through in a visible Edge the way a new user would.
        if ($visible -and $edge) {
            Doing 'clicking through first-run setup in Edge'
            $cdp = Start-Process -FilePath $edge -ArgumentList @('--no-first-run', '--no-default-browser-check', '--start-maximized', '--remote-debugging-port=9222', "--user-data-dir=$env:TEMP\edge-setup", "$base/welcome") -PassThru
            $moves = @()
            try {
                Start-Sleep -Seconds 6
                for ($i = 0; $i -lt 40; $i++) {
                    $move = Invoke-PageJs 9222 $SetupMove
                    if ($moves.Count -eq 0 -or $moves[-1] -ne $move) { $moves += $move }
                    if ("$move" -like 'left:*') { break }
                    Start-Sleep -Milliseconds 1500
                }
                Save-Screen 'setup-finished'
                # The welcome confetti stays still when the system asks for less motion
                # (Confetti.tsx); Sandbox and Remote Desktop turn animations off.
                $reduced = Invoke-PageJs 9222 "matchMedia('(prefers-reduced-motion: reduce)').matches"
                $moves += "reducedMotion=$reduced" 
            } catch { $moves += "error: $($_.Exception.Message)" }
            $settings = Invoke-RestMethod -Uri "$base/api/settings" -TimeoutSec 20
            $done = $settings.config.setup.done
            $left = ("$($moves[-1])" -like 'left:*') -and ("$($moves[-1])" -notlike '*welcome*')
            Step 'setup-clickthrough' ($left -and [bool]$done) "setup.done=$done path: $($moves -join ' > ')"
        }

        # The tray: the icon is there, and its menu opens with the expected items.
        if ($visible) {
            $found = Find-TrayIcon
            if ($found) {
                $icon, $where = $found
                Step 'tray-icon' $true "'$($icon.Current.Name)' in the $where"
                $opened = [PopupMenu]::OpenTray()
                Start-Sleep -Seconds 2
                Save-Screen 'tray-menu'
                $owner = [PopupMenu]::OpenMenuOwner()
                $upshotPids = @(Get-Process -Name upshot -ErrorAction SilentlyContinue | ForEach-Object { [uint32]$_.Id })
                Step 'tray-menu' ($opened -and $upshotPids -contains $owner) "opened=$opened menu owner pid=$owner (Upshot: $($upshotPids -join ',')); the items are in tray-menu.png"
                Add-Type -AssemblyName System.Windows.Forms
                [System.Windows.Forms.SendKeys]::SendWait('{ESC}')
            } else {
                Save-Screen 'tray-not-found'
                Step 'tray-icon' $false 'no "Upshot" button on the taskbar or among the hidden icons (tray-not-found.png)'
            }
        }

        # Transcription with the model the installer fetched: each clip in <In>\audio,
        # compared with its reference (<name>.ref.txt) word by word.
        $audioDir = Join-Path $In 'audio'
        if ((Test-Path $audioDir) -and (Test-Path (Join-Path $In 'upshot-api.ps1'))) {
            . (Join-Path $In 'upshot-api.ps1')
            $client = New-UpshotClient $base
            foreach ($clip in Get-ChildItem $audioDir -Filter '*.mp3') {
                $name = $clip.BaseName
                Doing "transcribing $($clip.Name) on this machine's CPU"
                $t = Get-Date
                try {
                    $imported = Import-UpshotAudio $client $clip.FullName
                    $m = Wait-UpshotMeeting $client $imported.meeting_id @('TRANSCRIBED', 'SUMMARIZED', 'RENDERED', 'DELIVERED') 1500
                    $tr = Get-UpshotTranscript $client $imported.meeting_id
                    $tr.Text | Out-File -Encoding utf8 (Join-Path $Out "transcript.$name.txt")
                    $ref = Join-Path $audioDir "$name.ref.txt"
                    $recall = if (Test-Path $ref) { Get-WordRecall (Read-ReferenceText $ref) $tr.Text } else { -1 }
                    $ok = ($m.state -ne 'FAILED') -and $tr.Segments -gt 0 -and ($recall -lt 0 -or $recall -ge 0.6)
                    Step "transcribe-$name" $ok "state=$($m.state) seconds=$([int]((Get-Date) - $t).TotalSeconds) audio=$($imported.duration_s)s segments=$($tr.Segments) language=$($tr.Language) recall=$recall"
                } catch { Step "transcribe-$name" $false "$($_.Exception.Message)" }
            }
        }

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
    # The uninstaller asked the app to quit (upshot.exe --quit) rather than killing it.
    $asked = @(Select-String -Path (Join-Path $home_ 'logs\app.log') -Pattern 'asked to quit' -ErrorAction SilentlyContinue)
    $killed = (Select-String -Path (Join-Path $Out 'uninstall.log') -Pattern 'taskkill' -ErrorAction SilentlyContinue | Select-Object -Last 1).Line
    Step 'uninstall-graceful-quit' ($asked.Count -gt 0) "app.log 'asked to quit' lines=$($asked.Count); uninstaller: $killed"
} catch {
    Step 'script' $false "$($_ | Out-String)"
}

$bad = @($steps | Where-Object { -not $_.ok }).Count
Say "Finished: $($steps.Count - $bad) ok, $bad failed. Results in $Out" $(if ($bad) { 'Red' } else { 'Green' })
"done $(Get-Date -Format o)" | Out-File -Encoding utf8 (Join-Path $Out 'DONE.txt')
if ($watch -or $StayOpen) {
    Say 'The sandbox stays open. Close its window when you are done; everything in it is discarded.' 'Yellow'
} elseif ($NoShutdown) {
    Say 'Done; the host closes this sandbox.' 'DarkGray'
} else {
    Start-Sleep -Seconds 5
    Stop-Computer -Force
}
