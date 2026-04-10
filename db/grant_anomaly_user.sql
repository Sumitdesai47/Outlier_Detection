-- Run as MySQL admin (e.g. root) if you use DATABASE_URL=mysql://anomaly:anomaly@127.0.0.1:3306/anomaly
-- mysql -u root -p < db/grant_anomaly_user.sql

CREATE DATABASE IF NOT EXISTS anomaly
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'anomaly'@'localhost' IDENTIFIED BY 'anomaly';
GRANT ALL PRIVILEGES ON anomaly.* TO 'anomaly'@'localhost';
FLUSH PRIVILEGES;
