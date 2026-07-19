import io
import json
from email.message import Message
from types import SimpleNamespace

import pytest

from astrolabe.llm.client import LLMTimeoutError
from astrolabe.public_api import handler as handler_mod
from astrolabe.public_api.auth import AuthenticationError
from astrolabe.public_api.handler import handler

ORIGIN = "https://astrolabe-ui.example"


class StubAuth:
    def __init__(self, error=None):
        self.error = error
        self.headers = []

    def verify(self, authorization):
        self.headers.append(authorization)
        if self.error:
            raise self.error
        return "owner"


class StubRuntime:
    def __init__(self, error=None):
        self.error = error

    def get_artifact(self, key):
        if self.error:
            raise self.error
        return {"schema_version": 1, "key": key}

    def list_tasks(self):
        return [{"id": 1, "status": "open"}]

    def turn(self, history, session_id):
        if self.error:
            raise self.error
        return {"session_id": session_id, "message": "ok", "cards": []}

    def complete_task(self, task_id, evidence, confidence_delta):
        return {"task": {"id": task_id, "status": "done", "evidence": evidence}}


def _request(monkeypatch, path, *, method="GET", body=None, origin=ORIGIN, auth=None, runtime=None):
    auth = auth or StubAuth()
    runtime = runtime or StubRuntime()
    services = SimpleNamespace(auth=auth, runtime=runtime)
    monkeypatch.setattr(handler_mod, "get_services", lambda: services)
    monkeypatch.setenv("ASTROLABE_ALLOWED_ORIGIN", ORIGIN)
    instance = object.__new__(handler)
    instance.path = path
    instance.client_address = ("127.0.0.1", 1)
    instance.headers = Message()
    if origin is not None:
        instance.headers["Origin"] = origin
    instance.headers["Authorization"] = "Bearer token"
    raw = b"" if body is None else json.dumps(body).encode()
    if body is not None:
        instance.headers["Content-Length"] = str(len(raw))
    instance.rfile = io.BytesIO(raw)
    instance.wfile = io.BytesIO()
    instance.response_status = None
    instance.response_headers = {}
    instance.send_response = lambda status: setattr(instance, "response_status", status)
    instance.send_header = lambda name, value: instance.response_headers.__setitem__(name, value)
    instance.end_headers = lambda: None
    getattr(instance, f"do_{method}")()
    value = json.loads(instance.wfile.getvalue()) if instance.wfile.getvalue() else None
    return instance, value, auth


def test_all_data_routes_require_auth_with_uniform_401(monkeypatch):
    instance, value, _ = _request(
        monkeypatch,
        "/v1/map",
        auth=StubAuth(AuthenticationError("owner mismatch at secret URL")),
    )
    assert instance.response_status == 401
    assert value == {"error": "unauthorized"}
    assert "owner" not in instance.wfile.getvalue().decode()


def test_authenticated_artifact_tasks_and_tutor_routes(monkeypatch):
    map_response, map_value, _ = _request(monkeypatch, "/v1/map")
    tasks_response, tasks_value, _ = _request(monkeypatch, "/v1/tasks")
    tutor_response, tutor_value, _ = _request(
        monkeypatch,
        "/v1/tutor/turn",
        method="POST",
        body={"session_id": "tutor-browser", "history": [{"role": "user", "content": "x"}]},
    )
    assert map_response.response_status == 200
    assert map_value["key"] == "map"
    assert tasks_response.response_status == 200
    assert tasks_value["tasks"][0]["status"] == "open"
    assert tutor_response.response_status == 200
    assert tutor_value["message"] == "ok"


def test_error_body_never_contains_exception_details(monkeypatch):
    secret = "https://secret.supabase.co /Users/private/path traceback"
    instance, value, _ = _request(
        monkeypatch, "/v1/map", runtime=StubRuntime(RuntimeError(secret))
    )
    assert instance.response_status == 500
    assert value == {"error": "internal_error"}
    assert secret not in instance.wfile.getvalue().decode()


def test_llm_timeout_is_a_json_504(monkeypatch):
    instance, value, _ = _request(
        monkeypatch,
        "/v1/tutor/turn",
        method="POST",
        body={"session_id": "tutor-browser", "history": []},
        runtime=StubRuntime(LLMTimeoutError("internal detail")),
    )
    assert instance.response_status == 504
    assert value == {"error": "tutor_timeout"}


def test_cors_is_exact_and_preflight_allows_authorization(monkeypatch):
    rejected, value, auth = _request(
        monkeypatch, "/v1/map", origin="https://evil.example"
    )
    assert rejected.response_status == 403
    assert value == {"error": "forbidden"}
    assert auth.headers == []

    allowed, value, _ = _request(monkeypatch, "/v1/map", method="OPTIONS")
    assert allowed.response_status == 204
    assert value is None
    assert allowed.response_headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Authorization" in allowed.response_headers["Access-Control-Allow-Headers"]


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_unsupported_methods_still_authenticate(monkeypatch, method):
    instance, value, auth = _request(monkeypatch, "/v1/map", method=method)
    assert instance.response_status == 405
    assert value == {"error": "method_not_allowed"}
    assert auth.headers == ["Bearer token"]
