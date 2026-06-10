"""Serve the standalone Plant Analysis React application."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, make_response, send_from_directory

bp = Blueprint("plant_analysis", __name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "plant_analysis"


@bp.route("/plant-analysis")
@bp.route("/plant-analysis/")
@bp.route("/plant-analysis/<path:subpath>")
def plant_analysis_app(subpath: str = ""):
    """SPA shell and built asset delivery for the Plant Analysis UI."""
    if subpath == "api" or subpath.startswith("api/"):
        return jsonify({"error": "API route not found"}), 404
    if subpath:
        asset_path = STATIC_DIR / subpath
        if asset_path.is_file():
            response = make_response(send_from_directory(STATIC_DIR, subpath))
            # Avoid stale SPA bundles after `npm run build` (hashed filenames still get cached by browsers).
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
    response = make_response(send_from_directory(STATIC_DIR, "index.html"))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def register(app):
    app.register_blueprint(bp)
