<#
.SYNOPSIS
    Run the whole Read Aloud test suite.

.DESCRIPTION
    Four suites:
      chunker  - sentence splitting (fast, silent)
      engine   - live round trip against the PowerShell SAPI server (makes sound)
      app      - drives the real Tk window (a window flashes; makes sound)
      global   - the OS-wide hotkey reader (registers real hotkeys; makes sound)
      parity   - the Python and JavaScript chunkers must agree (needs node)

    Speech during the tests plays at low volume.

    A sixth suite covers the extension's DOM mapping and needs a real browser.
    Run it with -Browser, which serves the extension folder and opens the
    harness in your default browser; results render on the page.

.PARAMETER Quiet
    Only run the suites that make no sound and open no windows.

.PARAMETER Browser
    Serve and open the extension DOM test harness instead of the other suites.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File run_tests.ps1
    powershell -ExecutionPolicy Bypass -File run_tests.ps1 -Quiet
    powershell -ExecutionPolicy Bypass -File run_tests.ps1 -Browser
#>

[CmdletBinding()]
param([switch]$Quiet, [switch]$Browser)

if ($Browser) {
    $port = 8777
    $url = "http://127.0.0.1:$port/test/harness.html"
    Write-Host "Serving extension\ on port $port" -ForegroundColor Cyan
    Write-Host "Opening $url" -ForegroundColor Cyan
    Write-Host 'Results render on the page. Press Ctrl+C here when done.'
    Write-Host ''

    $server = Start-Process -PassThru -WindowStyle Hidden python `
        -ArgumentList '-m', 'http.server', "$port", '--bind', '127.0.0.1' `
        -WorkingDirectory (Join-Path $PSScriptRoot 'extension')
    try {
        Start-Sleep -Milliseconds 700
        Start-Process $url
        Write-Host 'Press Enter to stop the server...' -NoNewline
        [void][Console]::ReadLine()
    }
    finally {
        if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    }
    return
}

$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$appDir = Join-Path $root 'app'

$suites = @(
    @{ Name = 'chunker'; Dir = $appDir; Args = @('-m', 'unittest', 'test_chunker', '-v'); Silent = $true }
    @{ Name = 'parity'; Dir = $root; Args = @('tools\test_parity.py', '-v'); Silent = $true }
    @{ Name = 'engine'; Dir = $appDir; Args = @('-m', 'unittest', 'test_engine', '-v'); Silent = $false }
    @{ Name = 'app'; Dir = $appDir; Args = @('-m', 'unittest', 'test_app', '-v'); Silent = $false }
    @{ Name = 'global'; Dir = $appDir; Args = @('-m', 'unittest', 'test_global_reader', '-v'); Silent = $false }
)

$failed = @()
$ran = 0

foreach ($suite in $suites) {
    if ($Quiet -and -not $suite.Silent) {
        Write-Host "SKIP  $($suite.Name)  (-Quiet)" -ForegroundColor DarkGray
        continue
    }

    Write-Host ''
    Write-Host "==== $($suite.Name) ====" -ForegroundColor Cyan
    Push-Location $suite.Dir
    try {
        & python @($suite.Args)
        $code = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }

    $ran++
    if ($code -ne 0) { $failed += $suite.Name }
}

Write-Host ''
if ($failed.Count -eq 0) {
    Write-Host "All $ran suite(s) passed." -ForegroundColor Green
    exit 0
}

Write-Host "FAILED: $($failed -join ', ')" -ForegroundColor Red
exit 1
