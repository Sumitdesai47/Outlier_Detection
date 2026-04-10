# Start Live Dashboard scheduled backfill from a UTC date (detached process + log file).
# Example:
#   .\scripts\run_live_dashboard_backfill_background.ps1 -FromDate 2024-11-21

param(
    [Parameter(Mandatory = $false)]
    [string] $FromDate = "2024-11-21"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

$logDir = Join-Path $root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outLog = Join-Path $logDir "live_dashboard_backfill-$stamp.out.log"
$errLog = Join-Path $logDir "live_dashboard_backfill-$stamp.err.log"

Write-Host "Starting backfill from $FromDate (UTC day start)..."
Write-Host "  stdout -> $outLog"
Write-Host "  stderr -> $errLog"

$pythonExe = $null
$pyArgs = @()
foreach ($c in @("python", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) {
        if ($c -eq "py") {
            $pythonExe = "py"
            $pyArgs = @("-3", "scripts\resume_scheduled_from_date.py", "--from", $FromDate)
        } else {
            $pythonExe = $cmd.Source
            $pyArgs = @("scripts\resume_scheduled_from_date.py", "--from", $FromDate)
        }
        break
    }
}
if (-not $pythonExe) {
    Write-Error "python not found on PATH. Install Python or activate your venv, then run again."
    exit 1
}

Start-Process -FilePath $pythonExe `
    -ArgumentList $pyArgs `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog

Write-Host "Process started in background. Check logs folder for progress."
