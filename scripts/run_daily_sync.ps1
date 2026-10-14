# Run by the "CareerAgent Daily Sync" scheduled task (see 03_SCHEDULE_DAILY_SYNC.ps1).
# Syncs the Company Radar, sends the one daily JSearch request and writes today's digest.
# data\logs\sync-<date>.log records each step and the exit; the sync's own output, one line per company as it
# finishes, goes to data\logs\sync-<date>_<time>.out.log (and .err.log when it writes errors).
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Project
. (Join-Path $Project "scripts\python_env.ps1")

$Logs = Join-Path $Project "data\logs"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$Log = Join-Path $Logs ("sync-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

# A run stopped before it wrote its exit (the laptop slept, shut down or went on battery) ends its log at "Starting";
# the next run says so.
$Previous = Get-ChildItem -Path $Logs -Filter "sync-*.log" | Where-Object { $_.Name -match '^sync-\d{4}-\d{2}-\d{2}\.log$' } |
    Sort-Object Name | Select-Object -Last 1
if ($Previous) {
    $Steps = @(Select-String -Path $Previous.FullName -Pattern "Starting Company Radar sync", "Finished with exit code")
    if ($Steps.Count -gt 0 -and $Steps[-1].Line -match "Starting Company Radar sync") {
        Add-Content -Path $Log -Value "$(Get-Date -Format s) The previous run ($($Previous.Name)) did not record its exit: it was stopped before it finished."
    }
}

$Python = $env:CAREER_AGENT_PYTHON
if (-not $Python -or -not (Test-Path $Python)) { $Python = Find-ProjectPython $Project }
if (-not $Python) {
    Add-Content -Path $Log -Value "$(Get-Date -Format s) Project Python environment not found."
    exit 1
}
Add-Content -Path $Log -Value "$(Get-Date -Format s) Backing up the tracker database"
& $Python scripts\tracker_admin.py backup *>> $Log
$BackupCode = $LASTEXITCODE

$Stamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
$Out = Join-Path $Logs "sync-$Stamp.out.log"
$Err = Join-Path $Logs "sync-$Stamp.err.log"
Add-Content -Path $Log -Value "$(Get-Date -Format s) Starting Company Radar sync; companies are written to $(Split-Path -Leaf $Out) as each finishes"
# Its own process writing straight to files, unbuffered (-u): every finished company is on disk at once, and the sync
# keeps writing even if this host is stopped, as the scheduler stops it when the laptop goes on battery.
$Sync = Start-Process -FilePath $Python -ArgumentList @("-u", "-m", "app.sources.sync") -WorkingDirectory $Project -NoNewWindow `
    -Wait -PassThru -RedirectStandardOutput $Out -RedirectStandardError $Err
$Code = $Sync.ExitCode
if ((Get-Item $Err).Length -eq 0) { Remove-Item $Err }
Add-Content -Path $Log -Value "$(Get-Date -Format s) Finished with exit code $Code (backup exit code $BackupCode)"
# A sync that worked still reports a backup that did not, so the task's last result shows it.
if ($Code -eq 0) { $Code = $BackupCode }
exit $Code
