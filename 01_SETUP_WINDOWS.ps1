$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Project
. (Join-Path $Project "scripts\python_env.ps1")

$Conda = Find-Conda
if ($Conda) {
    Write-Host "Using Conda: $Conda" -ForegroundColor Cyan
    $envs = & $Conda env list
    if ($envs -notmatch "job-agent") {
        Write-Host "Creating job-agent environment with Python 3.11..." -ForegroundColor Cyan
        & $Conda create -n job-agent python=3.11 pip -y
    }
    $Python = Get-CondaEnvPython $Conda
} else {
    # No conda: use a project virtual environment with Python 3.11 or newer.
    $Launcher = Get-Command py -ErrorAction SilentlyContinue
    $System = Get-Command python -ErrorAction SilentlyContinue
    if (-not ($Launcher -or $System)) {
        Write-Host "Neither Conda nor Python was found. Install Python 3.11+ or Miniconda first." -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Write-Host "Conda not found; creating .venv..." -ForegroundColor Cyan
        if ($Launcher) { & py -3.11 -m venv .venv } else { & python -m venv .venv }
    }
    $Python = Join-Path $Project ".venv\Scripts\python.exe"
}

if (-not $Python -or -not (Test-Path $Python)) {
    Write-Host "Could not locate the project Python environment." -ForegroundColor Red
    exit 1
}
Write-Host "Using Python: $Python" -ForegroundColor Cyan

Write-Host "Installing project dependencies..." -ForegroundColor Cyan
& $Python -m pip install -r requirements.txt

if (!(Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created root .env file." -ForegroundColor Green
}

Write-Host ""
Write-Host "SETUP COMPLETE" -ForegroundColor Green
Write-Host "Now add at least one job provider key (RAPIDAPI_KEY, ADZUNA_APP_ID/ADZUNA_APP_KEY or JOOBLE_API_KEY) to .env, then run 02_START_WINDOWS.ps1"
