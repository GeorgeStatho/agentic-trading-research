from __future__ import annotations

from flask import jsonify

from api_support.demo_mode import build_demo_mode_payload


def register_demo_mode_routes(app) -> None:
    @app.get("/api/demo-mode")
    def demo_mode():
        return jsonify(build_demo_mode_payload()), 200
