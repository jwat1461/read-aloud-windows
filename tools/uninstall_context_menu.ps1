<#
.SYNOPSIS
    Removes the "Read aloud" entry from the Windows Explorer right-click menu.

.DESCRIPTION
    Deletes the HKCU keys created by install_context_menu.ps1. Nothing outside
    those keys is touched, and no other user is affected.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\uninstall_context_menu.ps1
#>

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$base = 'HKCU:\Software\Classes\SystemFileAssociations'
if (-not (Test-Path $base)) {
    Write-Host 'Nothing to remove.' -ForegroundColor Yellow
    return
}

$removed = 0
foreach ($assoc in Get-ChildItem $base) {
    $key = Join-Path $assoc.PSPath 'shell\ReadAloud'
    if (Test-Path $key) {
        Remove-Item -Path $key -Recurse -Force
        Write-Host "  removed  $($assoc.PSChildName)" -ForegroundColor DarkGray
        $removed++
    }
}

Write-Host ''
if ($removed -gt 0) {
    Write-Host "Removed the Read aloud entry from $removed file type(s)." -ForegroundColor Green
} else {
    Write-Host 'No Read aloud entries were found.' -ForegroundColor Yellow
}
