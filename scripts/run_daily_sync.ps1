# Run by the "CareerAgent Daily Sync" scheduled task (see 03_SCHEDULE_DAILY_SYNC.ps1).
# Syncs the Company Radar, sends the one daily JSearch request and writes today's digest,
# logging all output to data\logs\sync-<date>.log.
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Project
. (Join-Path $Project "scripts\python_env.ps1")

$Logs = Join-Path $Project "data\logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$Log = Join-Path $Logs ("sync-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

$Python = Find-ProjectPython $Project
if (-not $Python) {
    Add-Content -Path $Log -Value "$(Get-Date -Format s) Project Python environment not found."
    exit 1
}
Add-Content -Path $Log -Value "$(Get-Date -Format s) Backing up the tracker database"
& $Python scripts\tracker_admin.py backup *>> $Log
$BackupCode = $LASTEXITCODE
Add-Content -Path $Log -Value "$(Get-Date -Format s) Starting Company Radar sync"
& $Python -m app.sources.sync *>> $Log
$Code = $LASTEXITCODE
Add-Content -Path $Log -Value "$(Get-Date -Format s) Finished with exit code $Code (backup exit code $BackupCode)"
# A sync that worked still reports a backup that did not, so the task's last result shows it.
if ($Code -eq 0) { $Code = $BackupCode }
exit $Code
