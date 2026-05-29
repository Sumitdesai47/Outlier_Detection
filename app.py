"""Application entry: create Flask app and register routes."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask

from routes.dashboard import register
from services.scheduler_service import init_scheduler_if_enabled

APP_ROOT = Path(__file__).resolve().parent
# Optional local config (gitignored): ENABLE_MYSQL, DATABASE_URL, FLASK_SECRET_KEY, etc.
load_dotenv(APP_ROOT / ".env")

app = Flask(__name__, root_path=str(APP_ROOT))
# Required for Flask session (remember last workflow / default tab).
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-change-me-in-production")

register(app)
init_scheduler_if_enabled(app)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


if __name__ == "__main__":
    # Dev server port: set FLASK_PORT in .env (default 5001).
    # Host: default 127.0.0.1; set FLASK_RUN_HOST=0.0.0.0 for LAN/tunnel tools that require bind-all.
    port = int(os.environ.get("FLASK_PORT", "5001"))
    host = os.environ.get("FLASK_RUN_HOST", "127.0.0.1").strip() or "127.0.0.1"
    debug = _env_flag("FLASK_DEBUG", True)
    # Reloader off by default: on Windows it watches site-packages and exits background
    # shells with code 4294967295 when pandas/sklearn files change.
    use_reloader = _env_flag("FLASK_USE_RELOADER", False)
    app.run(host=host, port=port, debug=debug, use_reloader=use_reloader)
