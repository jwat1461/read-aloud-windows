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

.PARAMETER Quiet
    Only run the suites that make no sound and open no windows.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File run_tests.ps1
    powershell -ExecutionPolicy Bypass -File run_tests.ps1 -Quiet
#>

[CmdletBinding()]
param([switch]$Quiet)

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
