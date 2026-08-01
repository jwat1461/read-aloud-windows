<#
.SYNOPSIS
    Start "Read Aloud Anywhere" automatically when you log in.

.DESCRIPTION
    Drops a shortcut in the current user's Startup folder, so the Ctrl+Alt+R
    hotkey is available in every application from the moment you sign in.
    Nothing is written outside your own profile.

    Remove it with -Remove, or by deleting the shortcut from:
    shell:startup

.PARAMETER Remove
    Delete the startup shortcut instead of creating it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install_startup.ps1
    powershell -ExecutionPolicy Bypass -File tools\install_startup.ps1 -Remove
#>

[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$startup = [Environment]::GetFolderPath('Startup')
$link = Join-Path $startup 'Read Aloud Anywhere.lnk'

if ($Remove) {
    if (Test-Path $link) {
        Remove-Item $link -Force
        Write-Host "Removed $link" -ForegroundColor Green
    }
    else {
        Write-Host 'No startup shortcut found.' -ForegroundColor Yellow
    }
    return
}

$target = Join-Path $repo 'app\global_reader.py'
if (-not (Test-Path $target)) { throw "Could not find $target" }

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $python) { throw 'Python is not on PATH. Install Python 3 and retry.' }
    $pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
    if (-not (Test-Path $pythonw)) { $pythonw = $python }
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"{0}"' -f $target
$shortcut.WorkingDirectory = Join-Path $repo 'app'
$shortcut.Description = 'Read the selected text aloud from any application'

$icon = Join-Path $repo 'app\readaloud.ico'
if (Test-Path $icon) { $shortcut.IconLocation = $icon }

$shortcut.Save()

Write-Host "Created $link" -ForegroundColor Green
Write-Host 'Read Aloud Anywhere will start with Windows.'
Write-Host 'Undo with:  tools\install_startup.ps1 -Remove'
