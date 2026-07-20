"""単一Vercel Python Functionの認証・CORS・routing境界。"""

from __future__ import annotations

import json
import logging
import os
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

from astrolabe.ledger.backend import LedgerBackendError
from astrolabe.llm.client import FatalLLMError, LLMCallError, LLMTimeoutError
from astrolabe.public_api.auth import AuthenticationError, AuthUnavailableError
from astrolabe.public_api.runtime import PublicApiConfigError, get_services
from astrolabe.tutor.engine import TutorEngineError

MAX_REQUEST_BYTES = 1_000_000
TASK_COMPLETE_PATH = re.compile(r"^/v1/tasks/(\d+)/complete$")
REPORT_PATH = re.compile(r"^/v1/reports/(\d{4}-\d{2}-\d{2})$")
ARTIFACT_PATHS = {
    "/v1/map": "map",
    "/v1/layout": "layout",
    "/v1/index": "index",
}

log = logging.getLogger("astrolabe.public_api")


class handler(BaseHTTPRequestHandler):
    server_version = "AstrolabeAPI/0.1"

    def log_message(self, format: str, *args) -> None:
        log.info("request completed")

    def _allowed_origin(self) -> str:
        return os.environ.get("ASTROLABE_ALLOWED_ORIGIN", "").strip()

    def _origin_is_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or (bool(self._allowed_origin()) and origin == self._allowed_origin())

    def _send_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.headers.get("Origin") == self._allowed_origin():
            self.send_header("Access-Control-Allow-Origin", self._allowed_origin())
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _reject_origin(self) -> bool:
        if self._origin_is_allowed():
            return False
        self._send_json(403, {"error": "forbidden"})
        return True

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid request") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("invalid request")
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("invalid request") from exc
        if not isinstance(value, dict):
            raise ValueError("invalid request")
        return value

    def _services_and_authenticate(self):
        services = get_services()
        services.auth.verify(self.headers.get("Authorization"))
        return services

    def _handle_error(self, exc: Exception) -> None:
        # 外部へ例外本文・trace・URL・内部pathを出さない。ログも型だけに絞る。
        log.error("public API failure type=%s", type(exc).__name__)
        if isinstance(exc, AuthenticationError):
            self._send_json(401, {"error": "unauthorized"})
        elif isinstance(exc, LLMTimeoutError):
            self._send_json(504, {"error": "tutor_timeout"})
        elif isinstance(exc, (AuthUnavailableError, PublicApiConfigError)):
            self._send_json(503, {"error": "service_unavailable"})
        elif isinstance(exc, (ValueError, TutorEngineError)):
            self._send_json(400, {"error": "invalid_request"})
        elif isinstance(exc, (FatalLLMError, LLMCallError, LedgerBackendError)):
            self._send_json(503, {"error": "service_unavailable"})
        else:
            self._send_json(500, {"error": "internal_error"})

    def do_OPTIONS(self) -> None:
        if self._reject_origin():
            return
        origin = self._allowed_origin()
        if not origin:
            self._send_json(503, {"error": "service_unavailable"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self) -> None:
        if self._reject_origin():
            return
        try:
            services = self._services_and_authenticate()
            path = urlparse(self.path).path
            if path == "/v1/tasks":
                self._send_json(200, {"tasks": services.runtime.list_tasks()})
                return
            artifact_key = ARTIFACT_PATHS.get(path)
            report_match = REPORT_PATH.match(path)
            if report_match:
                artifact_key = f"report:{report_match.group(1)}"
            if artifact_key:
                payload = services.runtime.get_artifact(artifact_key)
                if payload is None:
                    self._send_json(404, {"error": "not_found"})
                else:
                    self._send_json(200, payload)
                return
            self._send_json(404, {"error": "not_found"})
        except Exception as exc:
            self._handle_error(exc)

    def do_POST(self) -> None:
        if self._reject_origin():
            return
        try:
            services = self._services_and_authenticate()
            path = urlparse(self.path).path
            body = self._read_json()
            if path == "/v1/tutor/turn":
                history = body.get("history")
                session_id = body.get("session_id")
                if not isinstance(history, list) or not isinstance(session_id, str):
                    raise ValueError("invalid request")
                self._send_json(200, services.runtime.turn(history, session_id))
                return
            if path == "/v1/tasks":
                concept_id = body.get("concept_id")
                concept_name = body.get("concept_name")
                title = body.get("title")
                kind = body.get("kind")
                est_minutes = body.get("est_minutes")
                edges = body.get("edges", [])
                if not all(
                    isinstance(value, str)
                    for value in (concept_id, concept_name, title, kind)
                ) or not isinstance(est_minutes, int) or not isinstance(edges, list):
                    raise ValueError("invalid request")
                self._send_json(
                    201,
                    services.runtime.create_task(
                        concept_id, concept_name, title, kind, est_minutes, edges
                    ),
                )
                return
            match = TASK_COMPLETE_PATH.match(path)
            if match:
                evidence = body.get("evidence")
                confidence_delta = body.get("confidence_delta", 0.2)
                if not isinstance(evidence, str) or not isinstance(
                    confidence_delta, int | float
                ):
                    raise ValueError("invalid request")
                result = services.runtime.complete_task(
                    int(match.group(1)), evidence, float(confidence_delta)
                )
                self._send_json(200, result)
                return
            self._send_json(404, {"error": "not_found"})
        except Exception as exc:
            self._handle_error(exc)

    def _unsupported(self) -> None:
        if self._reject_origin():
            return
        try:
            self._services_and_authenticate()
            self._send_json(405, {"error": "method_not_allowed"})
        except Exception as exc:
            self._handle_error(exc)

    do_DELETE = _unsupported
    do_PATCH = _unsupported
    do_PUT = _unsupported
