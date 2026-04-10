# MySQL — local installation (recommended)

The Flask app **optionally** uses MySQL when `DATABASE_URL` starts with `mysql://` (or `mysql+pymysql://`). Use database name **`anomaly`** (or change the path in the URL to match your database).

You do **not** need Docker if MySQL is already installed on your machine.

## 1. Local MySQL

1. Start the **MySQL** service (Windows: Services, or MySQL Workbench / installer).
2. Create the database and a user (adjust user/password as you like):

```sql
CREATE DATABASE IF NOT EXISTS anomaly
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER 'anomaly'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON anomaly.* TO 'anomaly'@'localhost';
FLUSH PRIVILEGES;
```

(On MySQL 8.0.11+ you may use `CREATE USER IF NOT EXISTS` instead if the user might already exist.)

If you use **root** only for development:

```sql
CREATE DATABASE IF NOT EXISTS anomaly
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

3. Set **`DATABASE_URL`** in a `.env` file in the project root (loaded by `app.py`):

```text
mysql://USER:PASSWORD@127.0.0.1:3306/anomaly
```

Examples:

```text
mysql://anomaly:your_password@127.0.0.1:3306/anomaly
mysql://root:your_root_password@127.0.0.1:3306/anomaly
```

Windows PowerShell (current session only):

```powershell
$env:DATABASE_URL = "mysql://anomaly:your_password@127.0.0.1:3306/anomaly"
```

Default port is **3306** if omitted: `mysql://user:pass@127.0.0.1/anomaly`

## 2. Create tables

```bash
python scripts/init_db.py
```

Schema: `db/schema/001_initial.sql`

`init_db.py` (and the app on first DB use) runs **`CREATE DATABASE IF NOT EXISTS`** for the name in your URL (default **`anomaly`**), then creates tables.

### Web uploads → MySQL tables

When **`DATABASE_URL`** is set (`mysql://...`):

| Portal action | Excel file | Tables written |
|---------------|------------|----------------|
| **Anomaly detection** (submit both files) | Time-series XLSX (wide) | *Not re-inserted* (reuse data already loaded e.g. from Outlier tab) |
| **Anomaly detection** | Causal model XLSX | `causal_dataset`, `causal_sheet`, `causal_row` (per sheet / row, JSON payload) |
| **Anomaly detection** | (same run) | `anomaly_run`, `anomaly_drift_result`; lazy roots → `anomaly_root_cause_result` |
| **Outlier detection** | Drift / outlier XLSX | `timeseries_dataset`, `timeseries_observation` |
| **Outlier detection** | (same run) | `outlier_run`, `outlier_monthly_page` (summaries + monthly row JSON in `page_rows`) |

Same file bytes (**SHA-256**) reuse existing `timeseries_dataset` / `causal_dataset` rows (no duplicate dataset insert).

Browse saved rows under **Uploaded data** (`/data`) in the app.

### Error `1045 Access denied for user 'anomaly'@'localhost'`

Your **`DATABASE_URL`** user/password do not match MySQL.

- **Option A:** Use **`root`** (or whatever admin user you created), e.g.  
  `mysql://root:YOUR_PASSWORD@127.0.0.1:3306/anomaly` in `.env`.
- **Option B:** Create the **`anomaly`** app user (password **`anomaly`**) as admin:

```powershell
Get-Content db\grant_anomaly_user.sql | & "C:\Program Files\MySQL\MySQL Server 8.4\bin\mysql.exe" -u root -p
```

Then set  
`DATABASE_URL=mysql://anomaly:anomaly@127.0.0.1:3306/anomaly`

Also check **no other** `DATABASE_URL` in Windows “Environment variables” (it overrides `.env`).

## 3. Install MySQL Server (Windows, winget)

```powershell
winget install Oracle.MySQL --accept-package-agreements --accept-source-agreements
```

After install, configure an instance (data directory, root password). The app expects **`root` / `root`** if you use the project `.env` as generated.

**PyMySQL** needs the **`cryptography`** package for MySQL 8 default auth (`caching_sha2_password`). It is listed in `requirements.txt`; run `pip install -r requirements.txt`.

### Project-local data directory (optional dev setup)

If the installer does not register a Windows service, you can keep data under the repo in `mysql_data/` (gitignored) and start the server with:

```powershell
.\scripts\start_mysql_local.ps1
```

That uses `mysql_data\my.ini` pointing at `C:\Program Files\MySQL\MySQL Server 8.4` and port **3306**.

## 4. Optional: Docker MySQL

If you prefer a container instead of a local install, you can use `docker compose up -d` from the project root. That is **optional**; skip it when MySQL is already running on your PC.

## 5. Production

- Use strong passwords; do not commit `.env` or real credentials.
- Set `FLASK_SECRET_KEY` for sessions.
