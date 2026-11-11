"""Tiny helpers for API Gateway HTTP API (payload format 2.0) Lambda handlers.

Each handler module declares ``ROUTES = {"GET /api/documents": fn, ...}``. The
keys are the exact API Gateway route keys from template.yaml; a test checks
that the template and the code agree.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from . import log
from .auth import KEY_HEADER, is_valid_key, owner_from_key


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def dumps(body: Any) -> str:
    return json.dumps(body, default=_json_default, ensure_ascii=False)


def response(status: int, body: Any = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
            **(headers or {}),
        },
    }
    if body is not None:
        result["body"] = dumps(body)
    return result


class Request:
    def __init__(self, event: dict[str, Any]) -> None:
        self.event = event
        self.route_key: str = event.get("routeKey", "")
        http = (event.get("requestContext") or {}).get("http") or {}
        self.method: str = http.get("method", "")
        self.path: str = event.get("rawPath", "")
        self.headers: dict[str, str] = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        self.query: dict[str, str] = event.get("queryStringParameters") or {}
        self.path_params: dict[str, str] = event.get("pathParameters") or {}
        self.request_id: str = (event.get("requestContext") or {}).get("requestId", "")

    @property
    def owner(self) -> str:
        key = self.headers.get(KEY_HEADER)
        if not is_valid_key(key):
            raise ApiError(401, "missing_workspace_key", "Send your workspace key in the X-Parchi-Key header.")
        return owner_from_key(key or "")

    def json(self) -> dict[str, Any]:
        raw = self.event.get("body")
        if not raw:
            return {}
        if self.event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ApiError(400, "invalid_json", "Request body must be JSON.") from exc
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_json", "Request body must be a JSON object.")
        return body


Route = Callable[[Request], tuple[int, Any]]


def make_handler(routes: dict[str, Route]) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
    def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
        request = Request(event)
        route = routes.get(request.route_key)
        try:
            if route is None:
                raise ApiError(404, "no_route", f"No route for {request.route_key or request.path}.")
            status, body = route(request)
            return response(status, body)
        except ApiError as exc:
            if exc.status >= 500:
                log.error("api error", route=request.route_key, code=exc.code)
            return response(exc.status, {"error": {"code": exc.code, "message": exc.message, **exc.extra}})
        except Exception as exc:
            log.error("unhandled api error", exc, route=request.route_key, requestId=request.request_id)
            return response(500, {"error": {"code": "internal", "message": "Something went wrong on our side."}})

    handler.routes = routes  # type: ignore[attr-defined]
    return handler
