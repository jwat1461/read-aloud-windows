<#
.SYNOPSIS
    Adds a "Read aloud" entry to the Windows Explorer right-click menu for text
    files, the way DiffMerge adds its own entry.

.DESCRIPTION
    Right-click a .txt (or .md, .log, .csv, .json, .sql, .py, ...) file and pick
    "Read aloud" to open Read Aloud with that file loaded and speaking.

    Everything is written under HKCU:\Software\Classes, so this needs no
    administrator rights and only affects the current user. Undo it at any time:

        powershell -ExecutionPolicy Bypass -File tools\uninstall_context_menu.ps1

.PARAMETER Extensions
    File extensions to add the entry to. Defaults to common text formats.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install_context_menu.ps1
#>

[CmdletBinding()]
param(
    [string[]]$Extensions = @(
        '.txt', '.md', '.log', '.csv', '.json', '.xml', '.yml', '.yaml',
        '.sql', '.py', '.js', '.ts', '.html', '.css', '.ini', '.rtf'
    )
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo 'app\tts_app.py'
$icon = Join-Path $repo 'app\readaloud.ico'

if (-not (Test-Path $script)) {
    throw "Could not find $script - run this from inside the project folder."
}

# pythonw.exe runs the GUI without a console window flashing up.
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
    if (-not $python) { throw 'Python is not on PATH. Install Python 3 and retry.' }
    $pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
    if (-not (Test-Path $pythonw)) { $pythonw = $python }
}

$command = '"{0}" "{1}" "%1"' -f $pythonw, $script

foreach ($ext in $Extensions) {
    $key = "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell\ReadAloud"
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name '(default)' -Value 'Read aloud' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name 'MUIVerb' -Value 'Read aloud' -PropertyType String -Force | Out-Null

    if (Test-Path $icon) {
        New-ItemProperty -Path $key -Name 'Icon' -Value $icon -PropertyType String -Force | Out-Null
    }

    $cmdKey = Join-Path $key 'command'
    New-Item -Path $cmdKey -Force | Out-Null
    New-ItemProperty -Path $cmdKey -Name '(default)' -Value $command -PropertyType String -Force | Out-Null

    Write-Host "  added  $ext" -ForegroundColor DarkGray
}

Write-Host ''
Write-Host 'Installed.' -ForegroundColor Green
Write-Host "Command: $command"
Write-Host ''
Write-Host 'Right-click a text file and choose "Read aloud".'
Write-Host 'On Windows 11 it may sit under "Show more options" (Shift+F10).'
