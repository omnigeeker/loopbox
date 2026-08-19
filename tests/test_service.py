"""Tests for the E2B-compatible HTTP service (loopbox.service).

Each test gets a real ``ThreadingHTTPServer`` on an ephemeral port backed by
a temp ``LOOPBOX_HOME``; requests go through ``urllib``. ``LOOPBOX_NO_AUTH=1``
is set by the fixture except where a test explicitly enables auth.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from loopbox import service
from loopbox.store import Store


def _http(base: str, method: str, path: str, body: Any = None, headers: dict | None = None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        payload = json.loads(raw) if raw else None
        return exc.code, payload


@pytest.fixture()
def api(loopbox_home, monkeypatch):
    """Running test server with auth disabled; yields (base_url, stop_fn)."""
    monkeypatch.setenv("LOOPBOX_NO_AUTH", "1")
    server = service.SandboxService(("127.0.0.1", 0), store=Store())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def sandbox_id(api):
    status, body = _http(api, "POST", "/sandboxes", {"templateID": "seatbelt"})
    assert status == 201
    yield body["sandboxID"]
    _http(api, "DELETE", f"/sandboxes/{body['sandboxID']}")


def test_health_is_public(loopbox_home, monkeypatch):
    # Auth NOT disabled here: /health must still answer without a key.
    monkeypatch.delenv("LOOPBOX_NO_AUTH", raising=False)
    server = service.SandboxService(("127.0.0.1", 0), store=Store())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, body = _http(base, "GET", "/health")
        assert status == 200 and body["ok"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_auth_required_when_enabled(loopbox_home, monkeypatch):
    import secrets

    from loopbox import auth

    monkeypatch.delenv("LOOPBOX_NO_AUTH", raising=False)
    token = auth.TOKEN_PREFIX + secrets.token_urlsafe(32)
    auth._write_token_file(auth.token_path(), token)
    server = service.SandboxService(("127.0.0.1", 0), store=Store())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, body = _http(base, "GET", "/sandboxes")
        assert status == 401
        assert body["code"] == 401
        # Bad token is also rejected.
        status, _ = _http(base, "GET", "/sandboxes", headers={"X-API-Key": "lbx_wrong"})
        assert status == 401
        # Good token passes (both header styles).
        status, body = _http(base, "GET", "/sandboxes", headers={"X-API-Key": token})
        assert status == 200 and body == []
        status, _ = _http(
            base, "GET", "/sandboxes", headers={"Authorization": f"Bearer {token}"}
        )
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_create_get_list_delete(api):
    status, body = _http(
        api,
        "POST",
        "/sandboxes",
        {"templateID": "seatbelt", "metadata": {"purpose": "test"}, "envVars": {"K": "V"}},
    )
    assert status == 201
    sid = body["sandboxID"]
    assert body["templateID"] == "seatbelt"
    assert body["backend"] == "seatbelt"
    assert body["metadata"] == {"purpose": "test"}
    assert "envVars" not in body  # never echoed back

    status, body = _http(api, "GET", f"/sandboxes/{sid}")
    assert status == 200 and body["sandboxID"] == sid

    status, body = _http(api, "GET", "/sandboxes")
    assert status == 200
    assert sid in [r["sandboxID"] for r in body]

    status, _ = _http(api, "DELETE", f"/sandboxes/{sid}")
    assert status == 204
    status, body = _http(api, "GET", f"/sandboxes/{sid}")
    assert status == 404


def test_create_unknown_template_rejected(api):
    status, body = _http(api, "POST", "/sandboxes", {"templateID": "nope"})
    assert status == 400
    assert "unknown templateID" in body["message"]


def test_unknown_route_404(api):
    status, body = _http(api, "GET", "/nope")
    assert status == 404
    assert body["code"] == 404


def test_exec_roundtrip(api, sandbox_id):
    status, body = _http(
        api, "POST", f"/sandboxes/{sandbox_id}/exec", {"command": "echo svc-hello"}
    )
    assert status == 200
    assert body["exit_code"] == 0
    assert body["stdout"] == "svc-hello\n"
    assert body["command_line"]


def test_exec_argv_list_and_exit_code(api, sandbox_id):
    status, body = _http(
        api,
        "POST",
        f"/sandboxes/{sandbox_id}/exec",
        {"command": ["/bin/sh", "-c", "echo err >&2; exit 7"]},
    )
    assert status == 200
    assert body["exit_code"] == 7
    assert body["stderr"].strip() == "err"


def test_exec_requires_command(api, sandbox_id):
    status, body = _http(api, "POST", f"/sandboxes/{sandbox_id}/exec", {})
    assert status == 400
    assert "command" in body["message"]


def _write(api, sid, path, content):
    return _http(api, "PUT", f"/sandboxes/{sid}/files", {"path": path, "content": content})


def test_files_write_and_list(api, sandbox_id):
    status, body = _write(api, sandbox_id, "notes/hello.txt", "hi")
    assert status == 200
    assert body["bytes"] == 2

    status, body = _http(api, "GET", f"/sandboxes/{sandbox_id}/files")
    assert status == 200
    assert [f["name"] for f in body["files"]] == ["notes"]
    assert body["files"][0]["type"] == "dir"

    status, body = _http(api, "GET", f"/sandboxes/{sandbox_id}/files?path=notes")
    assert [f["name"] for f in body["files"]] == ["hello.txt"]
    assert body["files"][0]["size"] == 2

    # exec inside the sandbox sees the file written through the API.
    status, body = _http(
        api, "POST", f"/sandboxes/{sandbox_id}/exec", {"command": "cat notes/hello.txt"}
    )
    assert status == 200 and body["stdout"] == "hi"


def test_files_escape_rejected(api, sandbox_id):
    for verb in ("PUT",):
        status, body = _http(
            api, verb, f"/sandboxes/{sandbox_id}/files", {"path": "../x.txt", "content": "x"}
        )
        assert status == 400
        assert "escapes" in body["message"]
    status, body = _http(api, "GET", f"/sandboxes/{sandbox_id}/files?path=../")
    assert status == 400


def test_files_missing_path_404(api, sandbox_id):
    status, body = _http(api, "GET", f"/sandboxes/{sandbox_id}/files?path=nope.txt")
    assert status == 404


def test_snapshots_list_restore_flow(api, sandbox_id):
    _write(api, sandbox_id, "state.txt", "v1")
    status, body = _http(api, "POST", f"/sandboxes/{sandbox_id}/snapshots", {"name": "v1"})
    assert status == 201
    assert body["snapshotID"] == "v1"

    status, body = _http(api, "GET", f"/sandboxes/{sandbox_id}/snapshots")
    assert status == 200
    assert [s["snapshot_id"] for s in body["snapshots"]] == ["v1"]

    _write(api, sandbox_id, "state.txt", "v2")
    status, body = _http(
        api, "POST", f"/sandboxes/{sandbox_id}/exec", {"command": "cat state.txt"}
    )
    assert body["stdout"] == "v2"
    # Restore is exposed through fork + CLI; the service covers create/list here.


def test_fork_live_and_from_snapshot(api, sandbox_id):
    _write(api, sandbox_id, "data.txt", "v1")
    status, body = _http(api, "POST", f"/sandboxes/{sandbox_id}/snapshots", {"name": "base"})
    assert status == 201
    _write(api, sandbox_id, "data.txt", "v2")

    status, body = _http(api, "POST", f"/sandboxes/{sandbox_id}/fork", {})
    assert status == 201
    live_child = body["sandboxID"]
    try:
        status, body = _http(api, "GET", f"/sandboxes/{live_child}")
        assert status == 200
        assert body.get("parentSandboxID") == sandbox_id
        # Live fork sees v2.
        status, body = _http(
            api, "POST", f"/sandboxes/{live_child}/exec", {"command": "cat data.txt"}
        )
        assert body["stdout"] == "v2"
    finally:
        _http(api, "DELETE", f"/sandboxes/{live_child}")

    status, body = _http(
        api, "POST", f"/sandboxes/{sandbox_id}/fork", {"snapshotID": "base"}
    )
    assert status == 201
    snap_child = body["sandboxID"]
    try:
        status, body = _http(
            api, "POST", f"/sandboxes/{snap_child}/exec", {"command": "cat data.txt"}
        )
        assert body["stdout"] == "v1"
    finally:
        _http(api, "DELETE", f"/sandboxes/{snap_child}")


def test_pause_resume_and_exec_while_paused(api, sandbox_id):
    status, _ = _http(api, "POST", f"/sandboxes/{sandbox_id}/pause")
    assert status == 204
    status, body = _http(api, "GET", f"/sandboxes/{sandbox_id}")
    assert body["status"] == "paused"
    status, body = _http(
        api, "POST", f"/sandboxes/{sandbox_id}/exec", {"command": "echo no"}
    )
    assert status == 409
    status, _ = _http(api, "POST", f"/sandboxes/{sandbox_id}/resume")
    assert status == 204
    status, body = _http(
        api, "POST", f"/sandboxes/{sandbox_id}/exec", {"command": "echo yes"}
    )
    assert status == 200 and body["stdout"] == "yes\n"


def test_timeout_deadline_and_sweep(api, sandbox_id):
    status, _ = _http(api, "POST", f"/sandboxes/{sandbox_id}/timeout", {"timeout": 120})
    assert status == 204
    status, body = _http(api, "GET", f"/sandboxes/{sandbox_id}")
    assert body["timeoutAt"] is not None

    # The background sweeper is part of serve(); here the sweep is driven
    # directly against the shared store, with a slightly aged deadline.
    store = Store()
    record = store.get(sandbox_id)
    store.update(sandbox_id, timeout_deadline=time.time() - 0.01)
    killed = service.sweep_expired(store)
    assert sandbox_id in killed
    assert store.get(sandbox_id)["status"] == "killed"
    # A killed sandbox refuses further work.
    status, body = _http(
        api, "POST", f"/sandboxes/{sandbox_id}/exec", {"command": "echo no"}
    )
    assert status == 409


def test_sweep_keeps_live_sandboxes(api):
    status, body = _http(api, "POST", "/sandboxes", {"timeout": 120})
    assert status == 201
    sid = body["sandboxID"]
    try:
        assert service.sweep_expired(Store()) == []
        assert Store().get(sid)["status"] == "running"
    finally:
        _http(api, "DELETE", f"/sandboxes/{sid}")


def test_timeout_validation(api, sandbox_id):
    for bad in (-1, 0, "ten", True):
        status, body = _http(
            api, "POST", f"/sandboxes/{sandbox_id}/timeout", {"timeout": bad}
        )
        assert status == 400, bad
