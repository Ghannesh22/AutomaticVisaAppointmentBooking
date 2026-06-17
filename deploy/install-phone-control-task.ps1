$ErrorActionPreference = "Stop"

$TaskName = "VisaBotControlServer"
$StartupLauncher = Join-Path ([Environment]::GetFolderPath("Startup")) "VisaBotControlServer.vbs"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$EnvPath = Join-Path $ProjectRoot ".env"
$EnvExamplePath = Join-Path $ProjectRoot ".env.example"
$StartScript = Join-Path $ProjectRoot "deploy\start-control-server.ps1"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

if (!(Test-Path $EnvPath)) {
    Copy-Item $EnvExamplePath $EnvPath
}

$EnvContent = Get-Content -Path $EnvPath -Raw
if ($EnvContent -notmatch "(?m)^CONTROL_TOKEN=.+") {
    $Token = & $Python -c "import secrets; print(secrets.token_urlsafe(24))"
    if ($EnvContent -match "(?m)^CONTROL_TOKEN=\s*$") {
        $EnvContent = $EnvContent -replace "(?m)^CONTROL_TOKEN=\s*$", "CONTROL_TOKEN=$Token"
        Set-Content -Path $EnvPath -Value $EnvContent -Encoding UTF8
    } else {
        Add-Content -Path $EnvPath -Value "`nCONTROL_TOKEN=$Token"
    }
    Write-Host "Generated CONTROL_TOKEN in .env"
} else {
    Write-Host "Existing CONTROL_TOKEN found in .env"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 1) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$InstalledAs = "scheduled task"
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Starts the visa bot phone control server at Windows login." `
        -ErrorAction Stop `
        -Force | Out-Null
} catch {
    $InstalledAs = "Startup folder launcher"
    $EscapedStartScript = $StartScript -replace '"', '""'
    $LauncherContent = @"
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$EscapedStartScript""", 0, False
"@
    Set-Content -Path $StartupLauncher -Value $LauncherContent -Encoding ASCII
    Write-Host "Scheduled task registration failed, so a Startup folder launcher was created instead."
}

$IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if ($IsAdmin) {
    New-NetFirewallRule `
        -DisplayName "Visa Bot Control Server" `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 8765 `
        -Profile Any `
        -ErrorAction SilentlyContinue | Out-Null
    Write-Host "Firewall rule ensured for TCP port 8765"
} else {
    Write-Host "Not running as Administrator, so no firewall rule was added."
    Write-Host "If the phone cannot connect, allow Python or TCP port 8765 through Windows Firewall."
}

if ($InstalledAs -eq "scheduled task") {
    Start-ScheduledTask -TaskName $TaskName
} else {
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`"" `
        -WindowStyle Hidden
}

Write-Host ""
Write-Host "Phone control installed as: $InstalledAs"
Write-Host "Phone control server started."
Write-Host "Next:"
Write-Host "1. Install Tailscale on this laptop and your phone, then sign into the same account."
Write-Host "2. In Tailscale, copy this laptop's 100.x.y.z address."
Write-Host "3. On your phone, open http://100.x.y.z:8765 and enter CONTROL_TOKEN from .env."
Write-Host ""
Write-Host "Normal laptop bot startup remains unchanged: python -m src.main"
