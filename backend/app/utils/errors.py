"""Uniform JSON error envelope for the whole API."""
from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException


class APIError(Exception):
    """Raise anywhere in a view to return a structured JSON error."""

    def __init__(self, message: str, status_code: int = 400, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def to_response(self):
        payload = {"error": {"message": self.message, "status": self.status_code}}
        if self.details:
            payload["error"]["details"] = self.details
        return jsonify(payload), self.status_code


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(APIError)
    def _handle_api_error(exc: APIError):
        return exc.to_response()

    @app.errorhandler(HTTPException)
    def _handle_http_error(exc: HTTPException):
        return (
            jsonify({"error": {"message": exc.description, "status": exc.code}}),
            exc.code,
        )

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):
        app.logger.exception("Unhandled exception: %s", exc)
        return (
            jsonify({"error": {"message": "Internal server error", "status": 500}}),
            500,
        )
