# Show a Windows notification that stays until dismissed: how the loop reaches the user.
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File notify.ps1 -Title "..." -Text "..."
# Windows PowerShell 5.1 only, no modules. See RUNBOOK.md, "Needs you".
param([string]$Title = 'Upshot testing', [string]$Text = '')
$null = [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$null = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$t = [Security.SecurityElement]::Escape($Title)
$b = [Security.SecurityElement]::Escape($Text)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast scenario='reminder'><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual><actions><action content='OK' arguments='ok' activationType='foreground'/></actions></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
