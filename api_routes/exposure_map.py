from __future__ import annotations

from flask import jsonify, request

from api_support.common import internal_error, safe_int
from api_support.exposure_map import (
    build_exposure_map_payload,
    create_exposure_run,
    list_recent_exposure_articles,
)


def register_exposure_map_routes(app) -> None:
    @app.get("/api/exposure-map/articles")
    def exposure_map_articles():
        try:
            return jsonify(list_recent_exposure_articles(safe_int(request.args.get("limit"), 50, maximum=100))), 200
        except Exception as exc:
            return internal_error("Failed to load exposure-map articles.", exc)

    @app.post("/api/exposure-map/runs")
    def exposure_map_runs():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object with article_id."}), 400
        try:
            article_id = int(payload.get("article_id"))
        except (TypeError, ValueError):
            return jsonify({"error": "article_id must be an integer."}), 400
        if article_id <= 0:
            return jsonify({"error": "article_id must be positive."}), 400
        try:
            result = create_exposure_run(
                article_id,
                force=bool(payload.get("force")),
                model=payload.get("model"),
            )
            return jsonify(result), 200
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return internal_error("Failed to analyze article exposure.", exc)

    @app.get("/api/exposure-map/<int:article_id>")
    def exposure_map(article_id: int):
        try:
            return jsonify(build_exposure_map_payload(article_id)), 200
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:
            return internal_error("Failed to load exposure map.", exc)
