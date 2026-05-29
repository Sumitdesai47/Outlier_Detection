# Start local MySQL (if needed), optionally apply schema, then run Flask app.
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_dev_stack.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_dev_stack.ps1 -InitDb

param(
    [switch]$InitDb
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$startMysqlScript = Join-Path $root "scripts\start_mysql_local.ps1"
$checkMysqlScript = Join-Path $root "scripts\check_mysql_connection.py"
$initDbScript = Join-Path $root "scripts\init_db.py"
$appScript = Join-Path $root "app.py"

function Test-LocalTcpPortOpen {
    param(
        [string]$Host = "127.0.0.1",
        [int]$Port = 3306,
        [int]$TimeoutMs = 800
    )

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($Host, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if (-not $ok) {
            return $false
        }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

Push-Location $root
try {
    if (-not (Test-Path $appScript)) {
        throw "Missing app.py at $appScript"
    }
    if (-not (Test-Path $checkMysqlScript)) {
        throw "Missing check script at $checkMysqlScript"
    }

    if (Test-LocalTcpPortOpen -Host "127.0.0.1" -Port 3306) {
        Write-Host "MySQL appears to be running on 127.0.0.1:3306"
    } else {
        if (-not (Test-Path $startMysqlScript)) {
            throw "MySQL is not reachable and start script is missing: $startMysqlScript"
        }
        Write-Host "MySQL not reachable. Starting local MySQL..."
        powershell -ExecutionPolicy Bypass -File $startMysqlScript
        Start-Sleep -Seconds 2
    }

    Write-Host "Checking MySQL connection from .env DATABASE_URL..."
    python $checkMysqlScript
    if ($LASTEXITCODE -ne 0) {
        throw "MySQL connection check failed. Fix DATABASE_URL/MySQL and retry."
    }

    if ($InitDb) {
        if (-not (Test-Path $initDbScript)) {
            throw "Missing init_db.py at $initDbScript"
        }
        Write-Host "Applying DB schema..."
        python $initDbScript
        if ($LASTEXITCODE -ne 0) {
            throw "Schema initialization failed."
        }
    }

    $env:FLASK_USE_RELOADER = "0"
    Write-Host "Starting Flask app at http://127.0.0.1:5001 (reloader off) ..."
    python $appScript
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
