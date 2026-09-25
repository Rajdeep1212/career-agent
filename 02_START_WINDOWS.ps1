$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Project
. (Join-Path $Project "scripts\python_env.ps1")

$Python = Find-ProjectPython $Project
if (-not $Python) {
    Write-Host "Project Python environment not found. Run 01_SETUP_WINDOWS.ps1 first." -ForegroundColor Red
    exit 1
}

& $Python check_config.py
Write-Host ""
Write-Host "Starting Career Agent..." -ForegroundColor Green
Write-Host "Open: http://localhost:8010/app/" -ForegroundColor Green
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
