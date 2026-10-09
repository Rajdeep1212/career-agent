# Opens Career Agent like a desktop app: starts FastAPI and the built web app hidden in the background, each bound to
# 127.0.0.1 only, then opens the app window on http://localhost:<web port>. A server that is already running is left
# alone. Output goes to data\logs\. Stop both with scripts\stop_app.ps1.
#
#   -NoWindow   start the servers only (used by the start-at-login shortcut)
#   -Rebuild    run "next build" even when the build is current
#   -PlanOnly   print what would be done, as JSON, and do nothing
# The ports are parameters for the tests; the web build and WEB_ORIGIN assume 3010, and APP_ORIGIN assumes 8010.
param(
    [int]$ApiPort = 8010,
    [int]$WebPort = 3010,
    [switch]$NoWindow,
    [switch]$Rebuild,
    [switch]$PlanOnly
)
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Web = Join-Path $Project "web"
$Logs = Join-Path $Project "data\logs"
. (Join-Path $Project "scripts\python_env.ps1")

function Test-Listening([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $attempt = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        return ($attempt.AsyncWaitHandle.WaitOne(1000) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-Listening([int]$Port, [int]$Seconds) {
    for ($waited = 0; $waited -lt $Seconds; $waited++) {
        if (Test-Listening $Port) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# The web/ tree as git sees it, so a build made from older source is rebuilt. Empty when git cannot say.
function Get-WebSourceId {
    try {
        $id = (& git -C $Project rev-parse "HEAD:web" 2>$null)
        if ($LASTEXITCODE -eq 0 -and $id) { return "$id".Trim() }
    } catch { }
    return ""
}

$ApiArguments = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$ApiPort")
$WebArguments = @("node_modules\next\dist\bin\next", "start", "--hostname", "127.0.0.1", "--port", "$WebPort")
$WindowUrl = "http://localhost:$WebPort/app"      # the tracker board; "/" is the landing page
$ApiRunning = Test-Listening $ApiPort
$WebRunning = Test-Listening $WebPort

if ($PlanOnly) {
    [ordered]@{
        api = $(if ($ApiRunning) { "skip" } else { "start" })
        web = $(if ($WebRunning) { "skip" } else { "start" })
        api_command = "python " + ($ApiArguments -join " ")
        web_command = "node " + ($WebArguments -join " ")
        window_url = $WindowUrl
        logs = $Logs
    } | ConvertTo-Json -Compress
    exit 0
}

New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$Log = Join-Path $Logs "launcher.log"
function Write-Log([string]$Message) {
    Add-Content -Path $Log -Value "$(Get-Date -Format s) $Message"
    Write-Host $Message
}

if ($ApiRunning) {
    Write-Log "FastAPI is already running on port $ApiPort; left alone."
} else {
    $Python = $env:CAREER_AGENT_PYTHON
    if (-not $Python -or -not (Test-Path $Python)) { $Python = Find-ProjectPython $Project }
    if (-not $Python) {
        Write-Log "Project Python not found. Set CAREER_AGENT_PYTHON or run 01_SETUP_WINDOWS.ps1."
        exit 1
    }
    $started = Start-Process -FilePath $Python -ArgumentList $ApiArguments -WorkingDirectory $Project -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Logs "api.out.log") -RedirectStandardError (Join-Path $Logs "api.err.log")
    Write-Log "Started FastAPI on 127.0.0.1:$ApiPort (process $($started.Id))."
}

if ($WebRunning) {
    Write-Log "The web app is already running on port $WebPort; left alone."
} else {
    $Node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $Node) {
        Write-Log "Node.js not found on PATH; the web app needs Node 20 or newer."
        exit 1
    }
    if (-not (Test-Path (Join-Path $Web "node_modules\next"))) {
        Write-Log "web\node_modules is missing. Run 'npm install' in web\ once."
        exit 1
    }
    $Marker = Join-Path $Web ".next\career-agent-build.txt"
    $Source = Get-WebSourceId
    $Built = if (Test-Path $Marker) { (Get-Content $Marker -Raw).Trim() } else { "" }
    $Current = (Test-Path (Join-Path $Web ".next\BUILD_ID")) -and ($Source -eq "" -or $Source -eq $Built)
    if ($Rebuild -or -not $Current) {
        Write-Log "Building the web app (next build)..."
        $build = Start-Process -FilePath $Node.Source -ArgumentList @("node_modules\next\dist\bin\next", "build") -WorkingDirectory $Web `
            -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $Logs "web-build.out.log") `
            -RedirectStandardError (Join-Path $Logs "web-build.err.log")
        if ($build.ExitCode -ne 0) {
            Write-Log "The build failed (exit code $($build.ExitCode)); see data\logs\web-build.err.log."
            exit 1
        }
        Set-Content -Path $Marker -Value $Source -Encoding ascii
    }
    $started = Start-Process -FilePath $Node.Source -ArgumentList $WebArguments -WorkingDirectory $Web -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Logs "web.out.log") -RedirectStandardError (Join-Path $Logs "web.err.log")
    Write-Log "Started the web app on 127.0.0.1:$WebPort (process $($started.Id))."
}

if (-not (Wait-Listening $ApiPort 90)) { Write-Log "FastAPI did not answer on port $ApiPort; see data\logs\api.err.log."; exit 1 }
if (-not (Wait-Listening $WebPort 90)) { Write-Log "The web app did not answer on port $WebPort; see data\logs\web.err.log."; exit 1 }

if (-not $NoWindow) {
    # An app window (no tabs or address bar) in Edge or Chrome; otherwise the default browser.
    $Browsers = @(
        (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe"),
        (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe")
    ) | Where-Object { $_ -and (Test-Path $_) }
    if ($Browsers) {
        Start-Process -FilePath $Browsers[0] -ArgumentList "--app=$WindowUrl"
    } else {
        Start-Process $WindowUrl
    }
    Write-Log "Opened $WindowUrl."
}
