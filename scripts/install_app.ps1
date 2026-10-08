# Adds or removes the two shortcuts of the desktop app, for the current user only (no administrator rights):
#   -AutoStart on|off        a shortcut in the Startup folder that starts the servers hidden at Windows login
#   -DesktopShortcut on|off  a "Career Agent" shortcut on the desktop that starts them and opens the app window
# With neither given it reports what is installed. The folders are parameters for the tests.
param(
    [ValidateSet("on", "off", "keep")][string]$AutoStart = "keep",
    [ValidateSet("on", "off", "keep")][string]$DesktopShortcut = "keep",
    [string]$StartupFolder = [Environment]::GetFolderPath("Startup"),
    [string]$DesktopFolder = [Environment]::GetFolderPath("Desktop")
)
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Launcher = Join-Path $Project "scripts\start_app.ps1"
$Icon = Join-Path $Project "web\public\icon.ico"

function Set-Shortcut([string]$Path, [string]$State, [string]$Extra, [string]$Description) {
    if ($State -eq "off") {
        if (Test-Path $Path) { Remove-Item $Path -Confirm:$false }
    } elseif ($State -eq "on") {
        $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
        $shortcut.TargetPath = Join-Path $PSHOME "powershell.exe"
        $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Launcher`"$Extra"
        $shortcut.WorkingDirectory = $Project
        $shortcut.WindowStyle = 7                       # minimised: no console flashes up
        $shortcut.Description = $Description
        if (Test-Path $Icon) { $shortcut.IconLocation = $Icon }
        $shortcut.Save()
    }
    Write-Host ("{0}: {1}" -f $Path, $(if (Test-Path $Path) { "installed" } else { "not installed" }))
}

Set-Shortcut (Join-Path $StartupFolder "Career Agent (start at login).lnk") $AutoStart " -NoWindow" "Starts Career Agent's two local servers at login"
Set-Shortcut (Join-Path $DesktopFolder "Career Agent.lnk") $DesktopShortcut "" "Career Agent"
