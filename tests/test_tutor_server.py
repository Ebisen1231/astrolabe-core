import io
import json
from email.message import Message

from astrolabe.tutor import server as server_mod
from astrolabe.tutor.server import ALLOWED_ORIGIN, LOOPBACK_HOST, TutorRequestHandler


class StubRuntime:
    def __init__(self):
        self.turns = []
        self.completions = []

    def turn(self, history, session_id):
        self.turns.append((history, session_id))
        return {
            "session_id": session_id,
            "message": "ok",
            "cards": [],
            "budget_exhausted": False,
        }

    def list_tasks(self):
        return [{"id": 1, "title": "read", "status": "open"}]

    def complete_task(self, task_id, evidence, confidence_delta=0.2):
        self.completions.append((task_id, evidence, confidence_delta))
        return {"type": "task_completed", "task": {"id": task_id, "status": "done"}}


def _handler(runtime, path, *, payload=None, origin=ALLOWED_ORIGIN):
    handler = object.__new__(TutorRequestHandler)
    handler.runtime = runtime
    handler.path = path
    handler.client_address = ("127.0.0.1", 1)
    handler.headers = Message()
    handler.headers["Origin"] = origin
    raw = b"" if payload is None else json.dumps(payload).encode()
    if payload is not None:
        handler.headers["Content-Length"] = str(len(raw))
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    handler.response_status = None
    handler.response_headers = {}
    handler.send_response = lambda status: setattr(handler, "response_status", status)
    handler.send_header = lambda name, value: handler.response_headers.__setitem__(name, value)
    handler.end_headers = lambda: None
    return handler


def _json_body(handler):
    return json.loads(handler.wfile.getvalue())


def test_server_factory_binds_only_ipv4_loopback(monkeypatch):
    seen = {}
    sentinel = object()

    def fake_server(address, handler):
        seen["address"] = address
        seen["handler"] = handler
        return sentinel

    monkeypatch.setattr(server_mod, "ThreadingHTTPServer", fake_server)
    result = server_mod.create_tutor_server(StubRuntime(), 8787)
    assert result is sentinel
    assert seen["address"] == (LOOPBACK_HOST, 8787) == ("127.0.0.1", 8787)


def test_turn_tasks_and_complete_routes():
    runtime = StubRuntime()
    turn = _handler(
        runtime,
        "/v1/tutor/turn",
        payload={
            "session_id": "tutor-browser",
            "history": [{"role": "user", "content": "RoPE?"}],
        },
    )
    turn.do_POST()
    assert turn.response_status == 200
    assert turn.response_headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert _json_body(turn)["message"] == "ok"

    tasks = _handler(runtime, "/v1/tasks")
    tasks.do_GET()
    assert _json_body(tasks)["tasks"][0]["status"] == "open"

    complete = _handler(
        runtime,
        "/v1/tasks/1/complete",
        payload={"evidence": "note", "confidence_delta": 0.3},
    )
    complete.do_POST()
    assert _json_body(complete)["task"]["status"] == "done"
    assert runtime.turns[0][1] == "tutor-browser"
    assert runtime.completions == [(1, "note", 0.3)]


def test_cors_rejects_non_localhost_origin_before_runtime():
    runtime = StubRuntime()
    handler = _handler(
        runtime,
        "/v1/tutor/turn",
        payload={"session_id": "tutor-x", "history": []},
        origin="http://192.168.1.10:3000",
    )
    handler.do_POST()
    assert handler.response_status == 403
    assert runtime.turns == []


def test_preflight_allows_only_configured_local_origin():
    handler = _handler(StubRuntime(), "/v1/tutor/turn")
    handler.do_OPTIONS()
    assert handler.response_status == 204
    assert handler.response_headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert handler.response_headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
