# Start MySQL using mysql_data\my.ini (datadir: mysql_data\mysql96).
# Use after a one-time init (see db/MYSQL.md). Listens on port 3306.
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ini = Join-Path $root "mysql_data\my.ini"
$candidates = @(
    "C:\Program Files\MySQL\MySQL Server 9.6\bin\mysqld.exe",
    "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqld.exe",
    "C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe"
)
$mysqld = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not (Test-Path $ini)) { Write-Error "Missing $ini — see db/MYSQL.md"; exit 1 }
if (-not $mysqld) { Write-Error "No mysqld.exe found — install MySQL Server"; exit 1 }
Stop-Process -Name mysqld -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1
$pidPath = Join-Path $root "mysql_data\mysql96\DESKTOP-HVOSKSQ.pid"
if (Test-Path $pidPath) { Remove-Item $pidPath -Force -ErrorAction SilentlyContinue }
Start-Process -FilePath $mysqld -ArgumentList "--defaults-file=$ini" -WindowStyle Hidden
Write-Host "mysqld started ($mysqld)"
Write-Host "defaults-file=$ini"
Write-Host "Port 3306. Stop: taskkill /IM mysqld.exe"
