# Talk to a running Upshot the way its interface does. Dot-source it:  . .\upshot-api.ps1
# Windows PowerShell 5.1: its Invoke-WebRequest cannot post a file, so this uses HttpClient.
#
# Every mutating request needs the CSRF double-submit (app/api/security.py): the up_csrf
# cookie, which any response sets, echoed in the x-csrf-token header.
#
# Keep this file ASCII.
Add-Type -AssemblyName System.Net.Http

function New-UpshotClient([string]$Base) {
    $cookies = New-Object System.Net.CookieContainer
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.CookieContainer = $cookies
    $http = New-Object System.Net.Http.HttpClient($handler)
    $http.Timeout = [TimeSpan]::FromMinutes(5)
    [void]$http.GetAsync("$Base/api/status").Result
    $csrf = ($cookies.GetCookies([Uri]$Base) | Where-Object { $_.Name -eq 'up_csrf' } | Select-Object -First 1).Value
    if (-not $csrf) { throw 'the app set no up_csrf cookie' }
    $http.DefaultRequestHeaders.Add('x-csrf-token', $csrf)
    return [pscustomobject]@{ Base = $Base; Http = $http }
}

function Invoke-Upshot($Client, [string]$Method, [string]$Path, $Body = $null) {
    $request = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::new($Method), "$($Client.Base)$Path")
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 10 -Compress
        $request.Content = New-Object System.Net.Http.StringContent($json, [Text.Encoding]::UTF8, 'application/json')
    }
    $response = $Client.Http.SendAsync($request).Result
    $text = $response.Content.ReadAsStringAsync().Result
    if (-not $response.IsSuccessStatusCode) { throw "$Method $Path -> $([int]$response.StatusCode): $text" }
    if ($text) { return ($text | ConvertFrom-Json) }
}

function Import-UpshotAudio($Client, [string]$File) {
    $form = New-Object System.Net.Http.MultipartFormDataContent
    $bytes = [IO.File]::ReadAllBytes($File)
    $part = New-Object System.Net.Http.ByteArrayContent(, $bytes)
    $part.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/octet-stream')
    # An ASCII name on the wire: the meeting title is the file's stem, which is not what is tested.
    $name = 'clip' + [IO.Path]::GetExtension($File)
    $form.Add($part, 'file', $name)
    $response = $Client.Http.PostAsync("$($Client.Base)/api/import", $form).Result
    $text = $response.Content.ReadAsStringAsync().Result
    if (-not $response.IsSuccessStatusCode) { throw "import $File -> $([int]$response.StatusCode): $text" }
    return ($text | ConvertFrom-Json)
}

# Waits until the meeting is in one of $States (or FAILED), and returns the meeting.
function Wait-UpshotMeeting($Client, [string]$Id, [string[]]$States, [int]$TimeoutSeconds = 900) {
    $until = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $until) {
        $m = Invoke-Upshot $Client 'GET' "/api/meetings/$Id"
        if ($States -contains $m.state -or $m.state -in @('FAILED', 'DISCARDED', 'INTERRUPTED')) { return $m }
        Start-Sleep -Seconds 3
    }
    return (Invoke-Upshot $Client 'GET' "/api/meetings/$Id")
}

function Get-UpshotTranscript($Client, [string]$Id) {
    $t = Invoke-Upshot $Client 'GET' "/api/meetings/$Id/transcript"
    return [pscustomobject]@{
        Language = $t.language
        Model = ($t.model | ConvertTo-Json -Compress)
        Text = (($t.segments | ForEach-Object { $_.text }) -join ' ')
        Segments = @($t.segments).Count
    }
}

# Words, lower-cased, without punctuation; Hebrew letters kept.
function Get-Words([string]$Text) {
    return @(([regex]::Matches($Text.ToLowerInvariant(), '[\p{L}\p{N}]+')) | ForEach-Object { $_.Value })
}

# How many of the reference's distinct words the transcript also has (0..1).
function Get-WordRecall([string]$Reference, [string]$Text) {
    $ref = @(Get-Words $Reference | Sort-Object -Unique)
    if ($ref.Count -eq 0) { return 0 }
    $have = @{}
    foreach ($w in Get-Words $Text) { $have[$w] = $true }
    $hit = @($ref | Where-Object { $have.ContainsKey($_) }).Count
    return [math]::Round($hit / $ref.Count, 2)
}

# The text of a reference written by lang_experiment.py / verify_d60.py: "[ a- b] words" lines.
function Read-ReferenceText([string]$File) {
    return ((Get-Content -Encoding UTF8 $File | Where-Object { $_ -match '^\[' } | ForEach-Object { $_ -replace '^\[[^\]]*\]\s*', '' }) -join ' ')
}
