# Expose the local Flask app on the internet via ngrok (HTTPS public URL -> localhost).
#
# Prerequisites:
#   1. Install ngrok: https://ngrok.com/download  (add the folder to PATH, or install via your org's method)
#   2. One-time auth: ngrok config add-authtoken <token>   (token from https://dashboard.ngrok.com/get-started/your-authtoken )
#
# Usage (from repo root):
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_with_ngrok.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_with_ngrok.ps1 -Port 5001
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_with_ngrok.ps1 -NgrokOnly   # tunnel only; run Flask elsewhere
#
# Default port matches app.py: FLASK_PORT in .env or 5001. Ngrok opens in a separate window; Flask runs in this console.

param(
    [int]$Port = 0,
    [switch]$NgrokOnly
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $root ".env"

function Read-EnvFilePort {
    if (-not (Test-Path $envFile)) { return 0 }
    foreach ($line in Get-Content $envFile) {
        $t = $line.Trim()
        if ($t.StartsWith("#") -or $t.Length -eq 0) { continue }
        if ($t -match '^\s*FLASK_PORT\s*=\s*(\d+)\s*$') {
            return [int]$Matches[1]
        }
    }
    return 0
}

if ($Port -le 0) {
    if ($env:FLASK_PORT -match '^\d+$') {
        $Port = [int]$env:FLASK_PORT
    } else {
        $Port = Read-EnvFilePort
    }
}
if ($Port -le 0) { $Port = 5001 }

$ngrokCmd = Get-Command ngrok -ErrorAction SilentlyContinue
if (-not $ngrokCmd) {
    Write-Host "ngrok was not found on PATH." -ForegroundColor Red
    Write-Host "Install from https://ngrok.com/download , then run: ngrok config add-authtoken <YOUR_TOKEN>"
    exit 1
}

Write-Host "Ngrok will forward public HTTPS -> http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "Open the ngrok window for the Forwarding URL (e.g. https://xxxx.ngrok-free.app)." -ForegroundColor Cyan
Write-Host "Web UI (inspect requests): http://127.0.0.1:4040" -ForegroundColor DarkGray
Write-Host ""

Start-Process -FilePath "ngrok" -ArgumentList @("http", "$Port") -WorkingDirectory $root

if ($NgrokOnly) {
    Write-Host "Ngrok started. Run Flask in another terminal: python app.py" -ForegroundColor Green
    exit 0
}

Set-Location $root
if (-not $env:FLASK_PORT) {
    $env:FLASK_PORT = "$Port"
}
# Optional: set FLASK_RUN_HOST=0.0.0.0 in .env if you need LAN access without ngrok
python app.py
