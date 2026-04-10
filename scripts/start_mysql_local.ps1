# Start MySQL using this project's data directory (mysql_data\my.ini).
# Use after a one-time init (see db/MYSQL.md). Listens on port 3306.
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ini = Join-Path $root "mysql_data\my.ini"
$mysqld = "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysqld.exe"
if (-not (Test-Path $ini)) { Write-Error "Missing $ini — see db/MYSQL.md"; exit 1 }
if (-not (Test-Path $mysqld)) { Write-Error "Missing $mysqld — install MySQL Server"; exit 1 }
Start-Process -FilePath $mysqld -ArgumentList "--defaults-file=$ini" -WindowStyle Hidden
Write-Host "mysqld started (defaults-file=$ini). Port 3306. Stop: taskkill /IM mysqld.exe"
