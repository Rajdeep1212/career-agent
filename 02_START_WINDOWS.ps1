$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Project

$Python = Join-Path $env:USERPROFILE "anaconda3\envs\job-agent\python.exe"
if (!(Test-Path $Python)) {
    Write-Host "job-agent environment not found. Run 01_SETUP_WINDOWS.ps1 first." -ForegroundColor Red
    exit 1
}

& $Python check_config.py
Write-Host ""
Write-Host "Starting Career Agent..." -ForegroundColor Green
Write-Host "Open: http://localhost:8010/app/" -ForegroundColor Green
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
