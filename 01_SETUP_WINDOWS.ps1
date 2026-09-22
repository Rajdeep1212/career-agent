$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Project

$Conda = Join-Path $env:USERPROFILE "anaconda3\Scripts\conda.exe"
if (!(Test-Path $Conda)) {
    $cmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($cmd) { $Conda = $cmd.Source }
}
if (!(Test-Path $Conda)) {
    Write-Host "Conda not found. Install Anaconda/Miniconda first." -ForegroundColor Red
    exit 1
}

Write-Host "Using Conda: $Conda" -ForegroundColor Cyan
$envs = & $Conda env list
if ($envs -notmatch "job-agent") {
    Write-Host "Creating job-agent environment with Python 3.11..." -ForegroundColor Cyan
    & $Conda create -n job-agent python=3.11 pip -y
}

$Python = Join-Path $env:USERPROFILE "anaconda3\envs\job-agent\python.exe"
if (!(Test-Path $Python)) {
    Write-Host "job-agent Python not found at $Python" -ForegroundColor Red
    exit 1
}

Write-Host "Installing project dependencies..." -ForegroundColor Cyan
& $Python -m pip install -r requirements.txt

if (!(Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created root .env file." -ForegroundColor Green
}

Write-Host "" 
Write-Host "SETUP COMPLETE" -ForegroundColor Green
Write-Host "Now add your RAPIDAPI_KEY to the root .env, then run 02_START_WINDOWS.ps1"
