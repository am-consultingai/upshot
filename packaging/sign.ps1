# Authenticode-sign one file with the code-signing certificate in Cert:\CurrentUser\My.
#
#   powershell -ExecutionPolicy Bypass -File packaging\sign.ps1 <file> [-CertSubject "CN=..."]
#
# build.ps1 calls it for upshot.exe and the installer, and hands it to Inno Setup as the
# SignTool, which is how the uninstaller Inno generates gets signed too. The certificate
# is self-signed for now (the user's decision, 2026-09-23); its private key stays in this
# machine's store and only the public .cer travels. Keep this file ASCII.
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Path,
    [string]$CertSubject = "CN=Upshot test signing",
    [string]$TimestampServer = "http://timestamp.digicert.com"
)
$ErrorActionPreference = "Stop"
$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -eq $CertSubject } |
    Sort-Object NotAfter -Descending |
    Select-Object -First 1
if (-not $cert) { throw "no code-signing certificate '$CertSubject' in Cert:\CurrentUser\My" }
$sig = $null
# The timestamp server is a network call and fails now and then; a signature without a
# timestamp dies with the certificate, so retry rather than sign without one.
for ($attempt = 1; $attempt -le 3; $attempt++) {
    $sig = Set-AuthenticodeSignature -FilePath $Path -Certificate $cert -TimestampServer $TimestampServer -HashAlgorithm SHA256
    if ($sig.SignerCertificate -and $sig.TimeStamperCertificate) { break }
    Start-Sleep -Seconds (5 * $attempt)
}
# A self-signed certificate that is not in Trusted Root reports UnknownError here even
# though the file is signed; no signer certificate, or no timestamp, is a real failure.
if (-not $sig.SignerCertificate) { throw "signing $Path failed: $($sig.StatusMessage)" }
if (-not $sig.TimeStamperCertificate) { throw "signing $Path: no timestamp from $TimestampServer" }
Write-Host "signed $Path ($($sig.Status), timestamped by $($sig.TimeStamperCertificate.Subject))"
