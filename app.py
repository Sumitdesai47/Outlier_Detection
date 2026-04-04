"""Application entry: create Flask app and register routes."""
from __future__ import annotations

from pathlib import Path

from flask import Flask

from routes.dashboard import register

APP_ROOT = Path(__file__).resolve().parent

app = Flask(__name__, root_path=str(APP_ROOT))
register(app)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
