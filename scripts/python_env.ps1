# Shared by 01_SETUP_WINDOWS.ps1 and 02_START_WINDOWS.ps1: locate conda and the
# project's Python without assuming an install location.

function Find-Conda {
    $default = Join-Path $env:USERPROFILE "anaconda3\Scripts\conda.exe"
    if (Test-Path $default) { return $default }
    foreach ($name in @("conda.exe", "conda.bat", "conda")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }
    foreach ($base in @("miniconda3", "anaconda3", "Miniconda3", "Anaconda3")) {
        foreach ($root in @($env:USERPROFILE, $env:ProgramData, $env:LOCALAPPDATA)) {
            if (-not $root) { continue }
            $candidate = Join-Path $root "$base\Scripts\conda.exe"
            if (Test-Path $candidate) { return $candidate }
        }
    }
    return $null
}

function Get-CondaEnvPython([string]$Conda, [string]$EnvName = "job-agent") {
    $base = (& $Conda info --base 2>$null | Select-Object -Last 1)
    if (-not $base) { return $null }
    $python = Join-Path $base.Trim() "envs\$EnvName\python.exe"
    if (Test-Path $python) { return $python }
    return $null
}

function Find-ProjectPython([string]$Project) {
    # A project virtual environment wins; otherwise the conda "job-agent" env.
    $venv = Join-Path $Project ".venv\Scripts\python.exe"
    if (Test-Path $venv) { return $venv }
    $conda = Find-Conda
    if ($conda) { return Get-CondaEnvPython $conda }
    return $null
}
