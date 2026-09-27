# The frozen app end to end on a real Windows machine with a GPU and signed-in AI tools.
# Runs the built dist\upshot\upshot.exe (no install) in a home of its own, so the machine's
# own Upshot is not touched:
#
#   1. upshot.exe --prepare: with a suitable NVIDIA GPU, the CUDA libraries from PyPI (the
#      model is pointed at an existing verified folder, so it is not fetched again).
#   2. The app starts; each test clip is imported, transcribed and compared with its
#      reference transcript (word recall).
#   3. Summaries through the user's own plan: Claude Code first, then Codex on the same
#      meeting (a forced re-run of the summarize stage).
#   4. upshot.exe --quit, and the app must go.
#
# Everything lands in <Out>: steps.log, steps.json, transcripts, notes, summaries, app.log.
# Keep this file ASCII.
param(
    [string]$Exe = 'C:\upshot-build\dist\upshot\upshot.exe',
    [Parameter(Mandatory = $true)] [string]$ModelPath,
    [Parameter(Mandatory = $true)] [string]$Out,
    # name=audio file=reference transcript; the reference may be ''.
    [string[]]$Clips = @(),
    [string[]]$Providers = @('claude-subscription', 'codex-subscription'),
    [double]$MinRecall = 0.6
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'upshot-api.ps1')
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$log = Join-Path $Out 'steps.log'
$steps = [System.Collections.ArrayList]::new()
function Say([string]$t, [string]$c = 'Gray') { $l = "$(Get-Date -Format 'HH:mm:ss') $t"; Write-Host $l -ForegroundColor $c; $l | Out-File -Append -Encoding utf8 $log }
function Step([string]$name, [bool]$ok, [string]$detail) {
    [void]$steps.Add([ordered]@{ name = $name; ok = $ok; detail = $detail })
    if ($ok) { Say "ok   $name : $detail" 'Green' } else { Say "FAIL $name : $detail" 'Red' }
    $steps | ConvertTo-Json -Depth 5 | Out-File -Encoding utf8 (Join-Path $Out 'steps.json')
}

$home_ = Join-Path $Out 'home'
New-Item -ItemType Directory -Force -Path $home_ | Out-Null
$env:UP_HOME = $home_
# A home of its own, set up as first-run setup would leave it; the model is the verified one.
$config = [ordered]@{
    asr = [ordered]@{ model_path = $ModelPath }
    llm = [ordered]@{ provider = $Providers[0]; fallback_provider = '' }
    setup = [ordered]@{ done = $true }
    detection = [ordered]@{ mode = 'off' }
}
$config | ConvertTo-Json -Depth 5 | Out-File -Encoding ascii (Join-Path $home_ 'app_config.json')
Say "home $home_, model $ModelPath" 'White'

try {
    # 1. The GPU libraries, as the installer would fetch them.
    $progress = Join-Path $Out 'prepare-progress.txt'
    $t = Get-Date
    $p = Start-Process -FilePath $Exe -ArgumentList @('--prepare', '--progress-file', "`"$progress`"") -PassThru
    $null = $p.Handle
    $p.WaitForExit()
    $final = if (Test-Path $progress) { (Get-Content $progress | Where-Object { $_ -match '^(stage|state|text|error)=' }) -join ' ' } else { 'no progress file' }
    $cuda = Join-Path $home_ 'cuda\nvidia\cu12\bin'
    Step 'prepare-gpu-libraries' ($p.ExitCode -eq 0) "exit=$($p.ExitCode) seconds=$([int]((Get-Date) - $t).TotalSeconds) cudaFolder=$(Test-Path (Join-Path $cuda 'cublas64_12.dll')) $final"

    # 2. The app, and a client for its API.
    $app = Start-Process -FilePath $Exe -PassThru
    $portFile = Join-Path $home_ 'server.port'
    for ($i = 0; $i -lt 60 -and -not (Test-Path $portFile); $i++) { Start-Sleep -Seconds 1 }
    $base = "http://127.0.0.1:$((Get-Content -Raw $portFile).Trim())"
    $c = New-UpshotClient $base
    $status = Invoke-Upshot $c 'GET' '/api/status'
    Step 'app-start' (-not $app.HasExited) "$base build $($status.build.commit)"
    $model = Invoke-Upshot $c 'GET' '/api/model'
    Say "model/device: $($model | ConvertTo-Json -Compress)" 'DarkGray'

    $meetings = @{}
    foreach ($clip in $Clips) {
        $name, $rest = $clip -split '=', 2
        $audio, $reference = $rest -split '\|', 2
        $t = Get-Date
        $imported = Import-UpshotAudio $c $audio
        $m = Wait-UpshotMeeting $c $imported.meeting_id @('RENDERED', 'DELIVERED') 1200
        $meetings[$name] = $imported.meeting_id
        $secs = [int]((Get-Date) - $t).TotalSeconds
        $tr = Get-UpshotTranscript $c $imported.meeting_id
        $tr.Text | Out-File -Encoding utf8 (Join-Path $Out "transcript.$name.txt")
        $recall = if ($reference) { Get-WordRecall (Read-ReferenceText $reference) $tr.Text } else { -1 }
        $okText = $tr.Segments -gt 0 -and ($recall -lt 0 -or $recall -ge $MinRecall)
        Step "transcribe-$name" $okText "state=$($m.state) seconds=$secs duration=$($imported.duration_s)s segments=$($tr.Segments) language=$($tr.Language) recall=$recall model=$($tr.Model)"
        $notes = $null
        try { $notes = Invoke-Upshot $c 'GET' "/api/meetings/$($imported.meeting_id)/notes" } catch { }
        if ($notes) { $notes | ConvertTo-Json -Depth 10 | Out-File -Encoding utf8 (Join-Path $Out "notes.$name.$($Providers[0]).json") }
        Step "summary-$name-$($Providers[0])" (($m.state -eq 'RENDERED' -or $m.state -eq 'DELIVERED') -and [bool]$notes) "state=$($m.state) title=$($notes.title)"
    }

    # 3. The other plans, re-summarizing the first clip.
    $first = $meetings[($Clips[0] -split '=', 2)[0]]
    foreach ($provider in ($Providers | Select-Object -Skip 1)) {
        [void](Invoke-Upshot $c 'PUT' '/api/settings' @{ values = @{ 'llm.provider' = $provider } })
        $t = Get-Date
        [void](Invoke-Upshot $c 'POST' "/api/meetings/$first/jobs/summarize/retry?force=true")
        Start-Sleep -Seconds 3
        $m = Wait-UpshotMeeting $c $first @('RENDERED', 'DELIVERED') 900
        $notes = $null
        try { $notes = Invoke-Upshot $c 'GET' "/api/meetings/$first/notes" } catch { }
        if ($notes) { $notes | ConvertTo-Json -Depth 10 | Out-File -Encoding utf8 (Join-Path $Out "notes.first.$provider.json") }
        Step "summary-again-$provider" (($m.state -eq 'RENDERED' -or $m.state -eq 'DELIVERED') -and [bool]$notes) "state=$($m.state) seconds=$([int]((Get-Date) - $t).TotalSeconds) title=$($notes.title)"
    }

    # 4. Quit the way the installer asks.
    $q = Start-Process -FilePath $Exe -ArgumentList '--quit' -PassThru
    $null = $q.Handle
    $q.WaitForExit()
    $gone = $app.WaitForExit(20000)
    Step 'quit' $gone "--quit exit=$($q.ExitCode) appExited=$gone"
} catch {
    Step 'script' $false "$($_ | Out-String)"
} finally {
    Copy-Item -Recurse -Force (Join-Path $home_ 'logs') (Join-Path $Out 'app-logs') -ErrorAction SilentlyContinue
    Get-Process -Name upshot -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Exe } | Stop-Process -Force -ErrorAction SilentlyContinue
    $bad = @($steps | Where-Object { -not $_.ok }).Count
    Say "Finished: $($steps.Count - $bad) ok, $bad failed. Results in $Out" $(if ($bad) { 'Red' } else { 'Green' })
}
