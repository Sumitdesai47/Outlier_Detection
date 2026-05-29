# Stable Flask dev server (no watchdog reloader — avoids background shell crashes on Windows).
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_flask.ps1
# Enable auto-reload on project file changes only:
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_flask.ps1 -Reload

param(
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $root
try {
    $port = 5001
    if ($env:FLASK_PORT) { $port = [int]$env:FLASK_PORT }

    $onPort = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($procId in $onPort) {
        if ($procId -and $procId -ne 0) {
            Write-Host "Stopping existing listener on port $port (PID $procId)..."
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Seconds 1

    $env:FLASK_DEBUG = "1"
    if ($Reload) {
        $env:FLASK_USE_RELOADER = "1"
        Write-Host "Flask with reloader enabled (project edits will restart the server)."
    } else {
        $env:FLASK_USE_RELOADER = "0"
        Write-Host "Flask stable mode (reloader off)."
    }

    Write-Host "Starting http://127.0.0.1:$port ..."
    python (Join-Path $root "app.py")
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
