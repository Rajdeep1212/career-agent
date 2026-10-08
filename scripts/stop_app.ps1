# Stops the two servers that scripts\start_app.ps1 starts. A port held by anything else is reported and left alone.
param(
    [int]$ApiPort = 8010,
    [int]$WebPort = 3010
)
$ErrorActionPreference = "Stop"

function Stop-Server([int]$Port, [string]$Name, [string]$Pattern) {
    $owners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
    if (-not $owners) {
        Write-Host "$Name is not running on port $Port."
        return
    }
    foreach ($id in $owners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $id"
        if (-not $process -or $process.CommandLine -notmatch $Pattern) {
            Write-Host "Port $Port is held by process $id, which is not $Name; left alone."
            continue
        }
        & taskkill /PID $id /T /F | Out-Null
        Write-Host "Stopped $Name (process $id)."
    }
}

Stop-Server $ApiPort "FastAPI" "uvicorn\s+app\.main:app"
Stop-Server $WebPort "the web app" "next\\dist\\bin\\next"
