"""127.0.0.1限定の標準ライブラリHTTP API。認証導入はM3第3便。"""

from __future__ import annotations

import json
import logging
import re
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol

LOOPBACK_HOST = "127.0.0.1"
ALLOWED_ORIGIN = "http://localhost:3000"
MAX_REQUEST_BYTES = 1_000_000
TASK_COMPLETE_PATH = re.compile(r"^/v1/tasks/(\d+)/complete$")

log = logging.getLogger("astrolabe.tutor.server")


class TutorRuntime(Protocol):
    def turn(self, history: list[dict], session_id: str) -> dict: ...

    def list_tasks(self) -> list[dict]: ...

    def create_task(
        self,
        concept_id: str,
        concept_name: str,
        title: str,
        kind: str,
        est_minutes: int,
        edges: list[dict],
    ) -> dict: ...

    def complete_task(
        self, task_id: int, evidence: str, confidence_delta: float = 0.2
    ) -> dict: ...


class TutorRequestHandler(BaseHTTPRequestHandler):
    server_version = "AstrolabeTutor/0.1"

    def __init__(self, *args, runtime: TutorRuntime, **kwargs) -> None:
        self.runtime = runtime
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args) -> None:
        log.info("%s - %s", self.client_address[0], format % args)

    def _origin_is_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin == ALLOWED_ORIGIN

    def _send_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if self.headers.get("Origin") == ALLOWED_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Lengthが不正") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise ValueError("リクエストサイズが不正")
        try:
            value = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as exc:
            raise ValueError("JSONが不正") from exc
        if not isinstance(value, dict):
            raise ValueError("JSONのルートはobjectで指定する")
        return value

    def _reject_origin(self) -> bool:
        if self._origin_is_allowed():
            return False
        self._send_json(403, {"error": "許可されていないOrigin"})
        return True

    def do_OPTIONS(self) -> None:
        if self._reject_origin():
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:
        if self._reject_origin():
            return
        try:
            if self.path == "/health":
                self._send_json(200, {"status": "ok", "bind": LOOPBACK_HOST})
            elif self.path == "/v1/tasks":
                self._send_json(200, {"tasks": self.runtime.list_tasks()})
            else:
                self._send_json(404, {"error": "not found"})
        except Exception:
            log.exception("GET %s failed", self.path)
            self._send_json(500, {"error": "ローカルAPIの処理に失敗"})

    def do_POST(self) -> None:
        if self._reject_origin():
            return
        try:
            body = self._read_json()
            if self.path == "/v1/tutor/turn":
                history = body.get("history")
                session_id = body.get("session_id")
                if not isinstance(history, list) or not isinstance(session_id, str):
                    raise ValueError("history配列とsession_id文字列が必要")
                self._send_json(200, self.runtime.turn(history, session_id))
                return
            if self.path == "/v1/tasks":
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
                    raise ValueError("taskの入力が不正")
                self._send_json(
                    201,
                    self.runtime.create_task(
                        concept_id, concept_name, title, kind, est_minutes, edges
                    ),
                )
                return
            match = TASK_COMPLETE_PATH.match(self.path)
            if match:
                evidence = body.get("evidence")
                confidence_delta = body.get("confidence_delta", 0.2)
                if not isinstance(evidence, str) or not isinstance(
                    confidence_delta, int | float
                ):
                    raise ValueError("evidence文字列とconfidence_delta数値が必要")
                result = self.runtime.complete_task(
                    int(match.group(1)), evidence, float(confidence_delta)
                )
                self._send_json(200, result)
                return
            self._send_json(404, {"error": "not found"})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except Exception:
            log.exception("POST %s failed", self.path)
            self._send_json(500, {"error": "ローカルAPIの処理に失敗"})


def create_tutor_server(runtime: TutorRuntime, port: int) -> ThreadingHTTPServer:
    if not 0 <= port <= 65_535:
        raise ValueError("portは0..65535で指定する")
    handler = partial(TutorRequestHandler, runtime=runtime)
    # 認証なしの書き込みAPIなので、利用者が変更できるhostオプションは設けない。
    return ThreadingHTTPServer((LOOPBACK_HOST, port), handler)


def serve_tutor(runtime: TutorRuntime, port: int) -> None:
    if not 1 <= port <= 65_535:
        raise ValueError("portは1..65535で指定する")
    server = create_tutor_server(runtime, port)
    try:
        log.info("Tutor API: http://%s:%d", LOOPBACK_HOST, server.server_address[1])
        server.serve_forever()
    finally:
        server.server_close()
