$ErrorActionPreference = "Stop"

$TaskName = "VisaBotControlServer"
$StartupLauncher = Join-Path ([Environment]::GetFolderPath("Startup")) "VisaBotControlServer.vbs"

$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task $TaskName"
} else {
    Write-Host "Scheduled task $TaskName was not installed"
}

if (Test-Path $StartupLauncher) {
    Remove-Item -Path $StartupLauncher -Force
    Write-Host "Removed Startup folder launcher"
}

Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*src.control_server*" } |
    ForEach-Object {
        Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null
        Write-Host "Stopped running control server process $($_.ProcessId)"
    }

$IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if ($IsAdmin) {
    Get-NetFirewallRule -DisplayName "Visa Bot Control Server" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    Write-Host "Removed firewall rule if it existed"
}
