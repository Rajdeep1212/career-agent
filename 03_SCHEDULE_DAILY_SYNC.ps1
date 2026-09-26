# Registers a Windows scheduled task that runs the Company Radar sync every day at 07:00
# (companies, the one daily JSearch request, then the digest in data\digests).
#   .\03_SCHEDULE_DAILY_SYNC.ps1            register (or update) the task
#   .\03_SCHEDULE_DAILY_SYNC.ps1 -At 06:30  another time
#   .\03_SCHEDULE_DAILY_SYNC.ps1 -Remove    delete the task
param(
    [string]$At = "07:00",
    [switch]$Remove
)
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "CareerAgent Daily Sync"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed the '$TaskName' task." -ForegroundColor Green
    } else {
        Write-Host "No '$TaskName' task is registered."
    }
    exit 0
}

. (Join-Path $Project "scripts\python_env.ps1")
if (-not (Find-ProjectPython $Project)) {
    Write-Host "Project Python environment not found. Run 01_SETUP_WINDOWS.ps1 first." -ForegroundColor Red
    exit 1
}

$Runner = Join-Path $Project "scripts\run_daily_sync.ps1"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Runner`"" -WorkingDirectory $Project
$Trigger = New-ScheduledTaskTrigger -Daily -At $At
# Runs later if the computer was off at the scheduled time; never overlaps a running sync.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Career Agent: sync the Company Radar index and write today's digest." -Force | Out-Null

Write-Host "Registered '$TaskName' to run daily at $At (as $env:USERNAME, only while you are signed in)." -ForegroundColor Green
Write-Host "Logs: data\logs\sync-<date>.log   Digest: data\digests\<date>.html"
Write-Host "Run it now once: Start-ScheduledTask -TaskName '$TaskName'"
