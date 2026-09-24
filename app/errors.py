
"""HTTP 边界的结构化错误。"""

from flask import jsonify


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}

    def to_response(self):
        payload = {"error": {"code": self.code, "message": self.message}}
        if self.details:
            payload["error"]["details"] = self.details
        response = jsonify(payload)
        response.status_code = self.status
        return response


def bad_request(message: str, details=None) -> ApiError:
    return ApiError("invalid_request", message, 400, details)


def not_found(message: str) -> ApiError:
    return ApiError("not_found", message, 404)


def conflict(message: str, details=None) -> ApiError:
    return ApiError("conflict", message, 409, details)


def unprocessable(message: str, details=None) -> ApiError:
    return ApiError("unprocessable", message, 422, details)
