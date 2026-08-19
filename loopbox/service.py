"""E2B-protocol-compatible REST API over the local loopbox engine.

The service speaks a subset of the E2B sandbox API (``X-API-Key`` auth,
``{"code": int, "message": str}`` error bodies, the ``/sandboxes``
collection) plus loopbox extensions for local execution, files, snapshots
and forks:

    Core (E2B-compatible)
    ---------------------
    GET    /health                      liveness probe (unauthenticated)
    POST   /sandboxes                   create a sandbox (201)
    GET    /sandboxes                   list sandboxes
    GET    /sandboxes/{id}              describe one sandbox
    DELETE /sandboxes/{id}              kill and deregister a sandbox (204)
    POST   /sandboxes/{id}/timeout      set/reset the sandbox timeout (204)
    POST   /sandboxes/{id}/pause        freeze the sandbox (204)
    POST   /sandboxes/{id}/resume       resume a paused sandbox (204)

    Extensions (not part of the E2B control API)
    --------------------------------------------
    POST   /sandboxes/{id}/exec         run a command, returns ExecResult
    GET    /sandboxes/{id}/files        list workspace files
    PUT    /sandboxes/{id}/files        write a workspace file
    POST   /sandboxes/{id}/snapshots    capture a snapshot
    GET    /sandboxes/{id}/snapshots    list snapshots
    POST   /sandboxes/{id}/fork         clone into a new sandbox

E2B template IDs map to loopbox backends: ``templateID`` should be a
backend name (``seatbelt``, ``vz``; see :mod:`loopbox.backends`).

Sandbox timeouts are stored on the record (``timeout`` in seconds plus a
computed ``timeout_deadline``) and enforced lazily: a background sweeper
thread periodically kills every sandbox whose deadline has passed.

Run directly with ``python -m loopbox.service``; ``LOOPBOX_HOST`` and
``LOOPBOX_PORT`` override the default bind address. See
:mod:`loopbox.auth` for ``LOOPBOX_NO_AUTH``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from loopbox import auth
from loopbox.backends import backend_names, get_backend
from loopbox.store import Store, StoreError, new_id, workspace_dir

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 31885
SWEEP_INTERVAL_S = 1.0

_ROUTES: list[tuple[str, re.Pattern[str], str]] = []  # filled in below the handler class


class ApiError(Exception):
    """An HTTP error rendered as ``{"code": int, "message": str}``."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# -- record and payload helpers -----------------------------------------------


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    """Render a sandbox record in the E2B response shape.

    ``envVars`` are deliberately not echoed back to the client.
    """
    out: dict[str, Any] = {
        "sandboxID": record["id"],
        "templateID": record.get("template") or record.get("backend"),
        "backend": record.get("backend"),
        "status": record.get("status", "running"),
        "metadata": record.get("metadata") or {},
        "timeout": record.get("timeout"),
        "startedAt": _iso(record.get("created_at")),
        "timeoutAt": _iso(record.get("timeout_deadline")),
    }
    if record.get("parent_id"):
        out["parentSandboxID"] = record["parent_id"]
    return out


def _parse_timeout(value: Any) -> float:
    """Validate a JSON ``timeout`` value as a positive number of seconds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ApiError(400, "timeout must be a positive number of seconds")
    return float(value)


def _parse_command(value: Any) -> list[str]:
    """Coerce a JSON ``command`` (string or string list) into an argv list.

    A string runs through the login shell (``/bin/zsh -lc``), matching the
    E2B ``commands.run`` contract where shell features like pipes, redirects
    and ``&&`` work; a list is executed verbatim without a shell.
    """
    if isinstance(value, str):
        argv = ["/bin/zsh", "-lc", value]
    elif isinstance(value, list) and all(isinstance(a, str) for a in value):
        argv = list(value)
    else:
        raise ApiError(400, "command must be a string or a list of strings")
    if not argv:
        raise ApiError(400, "command must not be empty")
    return argv


def _workspace_target(record: dict, rel: str) -> Path:
    """Resolve a client-supplied path inside the workspace, rejecting escapes.

    Paths are interpreted relative to the sandbox workspace; a leading
    ``/`` is treated as the workspace root.
    """
    base = workspace_dir(record["id"]).resolve()
    target = (base / rel.lstrip("/")).resolve()
    if target != base and base not in target.parents:
        raise ApiError(400, f"path {rel!r} escapes the sandbox workspace")
    return target


def _file_entry(record: dict, path: Path) -> dict[str, Any]:
    """Describe one workspace file in a stable, JSON-serializable shape."""
    base = workspace_dir(record["id"]).resolve()
    kind = "dir" if path.is_dir() else "file" if path.is_file() else "other"
    return {
        "name": path.name,
        "path": str(path.relative_to(base)),
        "type": kind,
        "size": path.stat().st_size,
    }


# -- timeout enforcement -------------------------------------------------------


def sweep_expired(store: Store, now: float | None = None) -> list[str]:
    """Kill every live sandbox whose timeout deadline has passed.

    Returns the ids of the sandboxes that were killed. Sandboxes keep
    their record with ``status == "killed"`` so clients can observe the
    transition; only an explicit DELETE removes a record.
    """
    now = time.time() if now is None else now
    killed: list[str] = []
    for record in store.list():
        deadline = record.get("timeout_deadline")
        if not deadline or deadline > now or record.get("status") == "killed":
            continue
        try:
            backend = get_backend(record.get("backend"))
            try:
                backend.resume(record)
            except Exception:
                # SIGKILL is only delivered once SIGSTOPped processes continue.
                pass
            backend.kill(record)
        finally:
            try:
                store.update(record["id"], status="killed", timeout_deadline=None)
            except StoreError:
                pass  # the record was concurrently deleted
        killed.append(record["id"])
    return killed


def _sweep_loop(store: Store, interval: float, stop: threading.Event) -> None:
    while not stop.wait(interval):
        try:
            sweep_expired(store)
        except Exception:
            pass  # the sweeper must never take the service down


# -- HTTP server ----------------------------------------------------------------


class SandboxService(ThreadingHTTPServer):
    """Threaded HTTP server holding the shared store and its write lock."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: Store | None = None) -> None:
        super().__init__(address, _RequestHandler)
        self.store = store or Store()
        self.store_lock = threading.Lock()


class _RequestHandler(BaseHTTPRequestHandler):
    server: SandboxService
    server_version = "loopbox-e2b/0.1"

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    # -- plumbing --------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if len(path) > 1:
            path = path.rstrip("/")
        query = parse_qs(parsed.query)
        try:
            if path != "/health" and not auth.is_authorized(self.headers):
                raise ApiError(401, "missing or invalid X-API-Key")
            for route_method, pattern, name in _ROUTES:
                if route_method != method:
                    continue
                match = pattern.match(path)
                if match:
                    getattr(self, name)(query=query, **match.groupdict())
                    return
            raise ApiError(404, f"no route for {method} {path}")
        except ApiError as exc:
            self._send_json(exc.code, {"code": exc.code, "message": exc.message})
        except FileNotFoundError as exc:
            self._send_json(404, {"code": 404, "message": str(exc)})
        except StoreError as exc:
            self._send_json(404, {"code": 404, "message": str(exc)})
        except ValueError as exc:
            self._send_json(400, {"code": 400, "message": str(exc)})
        except subprocess.TimeoutExpired:
            self._send_json(408, {"code": 408, "message": "command timed out"})
        except Exception as exc:  # last-resort error body, still E2B-shaped
            self._send_json(500, {"code": 500, "message": f"{type(exc).__name__}: {exc}"})

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int = 204) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(400, "request body must be valid JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "request body must be a JSON object")
        return data

    def _record(self, sandbox_id: str) -> dict[str, Any]:
        try:
            return self.server.store.get(sandbox_id)
        except StoreError:
            raise ApiError(404, f"sandbox {sandbox_id} not found") from None

    @staticmethod
    def _backend(record: dict[str, Any]):
        return get_backend(record.get("backend"))

    @staticmethod
    def _require_live(record: dict[str, Any]) -> None:
        if record.get("status") == "killed":
            raise ApiError(409, f"sandbox {record['id']} has been killed")

    # -- core E2B-compatible routes ---------------------------------------

    def route_health(self, query: dict) -> None:
        self._send_json(200, {"ok": True})

    def route_create_sandbox(self, query: dict) -> None:
        body = self._json_body()
        template = body.get("templateID") or "seatbelt"
        if not isinstance(template, str):
            raise ApiError(400, "templateID must be a string")
        try:
            backend = get_backend(template)
        except ValueError:
            known = ", ".join(backend_names())
            raise ApiError(400, f"unknown templateID {template!r} (available: {known})") from None
        timeout = None
        if body.get("timeout") is not None:
            timeout = _parse_timeout(body["timeout"])
        metadata = body.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ApiError(400, "metadata must be an object")
        env_vars = body.get("envVars") or {}
        if not isinstance(env_vars, dict):
            raise ApiError(400, "envVars must be an object")
        record: dict[str, Any] = {
            "id": new_id("sbx"),
            "backend": backend.name,
            "template": template,
            "status": "running",
            "metadata": dict(metadata),
            "env": {str(k): str(v) for k, v in env_vars.items()},
            "timeout": timeout,
            "timeout_deadline": (time.time() + timeout) if timeout else None,
        }
        backend.create(record)  # fills record["engine"] before persisting
        with self.server.store_lock:
            self.server.store.add(record)
        self._send_json(201, public_record(record))

    def route_list_sandboxes(self, query: dict) -> None:
        records = self.server.store.list()
        self._send_json(200, [public_record(r) for r in records])

    def route_get_sandbox(self, sid: str, query: dict) -> None:
        self._send_json(200, public_record(self._record(sid)))

    def route_kill_sandbox(self, sid: str, query: dict) -> None:
        record = self._record(sid)
        backend = self._backend(record)
        try:
            backend.resume(record)  # let SIGKILL land on SIGSTOPped groups
        except Exception:
            pass  # a sandbox that cannot resume still gets killed
        backend.kill(record)
        with self.server.store_lock:
            self.server.store.remove(sid)
        self._send_empty()

    def route_set_timeout(self, sid: str, query: dict) -> None:
        record = self._record(sid)
        self._require_live(record)
        timeout = _parse_timeout(self._json_body().get("timeout"))
        with self.server.store_lock:
            self.server.store.update(
                sid, timeout=timeout, timeout_deadline=time.time() + timeout
            )
        self._send_empty()

    def route_pause_sandbox(self, sid: str, query: dict) -> None:
        record = self._record(sid)
        self._require_live(record)
        if record.get("status") != "paused":
            self._backend(record).pause(record)
            with self.server.store_lock:
                self.server.store.update(sid, status="paused")
        self._send_empty()

    def route_resume_sandbox(self, sid: str, query: dict) -> None:
        record = self._record(sid)
        self._require_live(record)
        if record.get("status") == "paused":
            self._backend(record).resume(record)
            with self.server.store_lock:
                self.server.store.update(sid, status="running")
        self._send_empty()

    # -- loopbox extensions ------------------------------------------------

    def route_exec_command(self, sid: str, query: dict) -> None:
        record = self._record(sid)
        self._require_live(record)
        if record.get("status") == "paused":
            raise ApiError(409, f"sandbox {sid} is paused; resume it first")
        body = self._json_body()
        if "command" not in body:
            raise ApiError(400, "missing required field: command")
        argv = _parse_command(body["command"])
        cwd = body.get("cwd")
        if cwd is not None and not isinstance(cwd, str):
            raise ApiError(400, "cwd must be a string")
        env = body.get("env") or {}
        if not isinstance(env, dict):
            raise ApiError(400, "env must be an object")
        timeout = body.get("timeout")
        if timeout is not None:
            timeout = _parse_timeout(timeout)
        result = self._backend(record).exec(
            record,
            argv,
            cwd=cwd,
            env={str(k): str(v) for k, v in env.items()},
            timeout=timeout,
        )
        self._send_json(200, result.to_dict())

    def route_list_files(self, sid: str, query: dict) -> None:
        record = self._record(sid)
        self._require_live(record)
        rel = query.get("path", [""])[0]
        target = _workspace_target(record, rel)
        if not target.exists():
            raise ApiError(404, f"path {rel!r} does not exist")
        if target.is_dir():
            entries = [_file_entry(record, p) for p in sorted(target.iterdir())]
        else:
            entries = [_file_entry(record, target)]
        self._send_json(200, {"files": entries})

    def route_write_file(self, sid: str, query: dict) -> None:
        record = self._record(sid)
        self._require_live(record)
        body = self._json_body()
        path = body.get("path")
        content = body.get("content")
        if not isinstance(path, str) or not path:
            raise ApiError(400, "missing required field: path (a string)")
        if not isinstance(content, str):
            raise ApiError(400, "missing required field: content (a string)")
        target = _workspace_target(record, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._send_json(200, {"path": path, "bytes": len(content.encode("utf-8"))})

    def route_create_snapshot(self, sid: str, query: dict) -> None:
        record = self._record(sid)
        self._require_live(record)
        name = self._json_body().get("name")
        if name is not None and not isinstance(name, str):
            raise ApiError(400, "name must be a string")
        snapshot_id = self._backend(record).snapshot(record, name)
        self._send_json(201, {"snapshotID": snapshot_id})

    def route_list_snapshots(self, sid: str, query: dict) -> None:
        record = self._record(sid)
        self._send_json(200, {"snapshots": self._backend(record).list_snapshots(record)})

    def route_fork_sandbox(self, sid: str, query: dict) -> None:
        record = self._record(sid)
        self._require_live(record)
        snapshot_id = self._json_body().get("snapshotID")
        if snapshot_id is not None and not isinstance(snapshot_id, str):
            raise ApiError(400, "snapshotID must be a string")
        child = self._backend(record).fork(record, snapshot_id)
        if record.get("timeout"):
            with self.server.store_lock:
                self.server.store.update(
                    child["id"],
                    timeout=record["timeout"],
                    timeout_deadline=time.time() + record["timeout"],
                )
        self._send_json(201, {"sandboxID": child["id"]})


_ROUTES.extend(
    [
        ("GET", re.compile(r"^/health$"), "route_health"),
        ("POST", re.compile(r"^/sandboxes$"), "route_create_sandbox"),
        ("GET", re.compile(r"^/sandboxes$"), "route_list_sandboxes"),
        ("GET", re.compile(r"^/sandboxes/(?P<sid>[^/]+)$"), "route_get_sandbox"),
        ("DELETE", re.compile(r"^/sandboxes/(?P<sid>[^/]+)$"), "route_kill_sandbox"),
        ("POST", re.compile(r"^/sandboxes/(?P<sid>[^/]+)/timeout$"), "route_set_timeout"),
        ("POST", re.compile(r"^/sandboxes/(?P<sid>[^/]+)/pause$"), "route_pause_sandbox"),
        ("POST", re.compile(r"^/sandboxes/(?P<sid>[^/]+)/resume$"), "route_resume_sandbox"),
        ("POST", re.compile(r"^/sandboxes/(?P<sid>[^/]+)/exec$"), "route_exec_command"),
        ("GET", re.compile(r"^/sandboxes/(?P<sid>[^/]+)/files$"), "route_list_files"),
        ("PUT", re.compile(r"^/sandboxes/(?P<sid>[^/]+)/files$"), "route_write_file"),
        ("POST", re.compile(r"^/sandboxes/(?P<sid>[^/]+)/snapshots$"), "route_create_snapshot"),
        ("GET", re.compile(r"^/sandboxes/(?P<sid>[^/]+)/snapshots$"), "route_list_snapshots"),
        ("POST", re.compile(r"^/sandboxes/(?P<sid>[^/]+)/fork$"), "route_fork_sandbox"),
    ]
)


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Run the loopbox E2B-compatible service until interrupted.

    Args:
        host: Interface to bind. Defaults to loopback only.
        port: TCP port to bind. Defaults to 31885.
    """
    store = Store()
    if auth.auth_disabled():
        auth_note = "auth disabled via LOOPBOX_NO_AUTH=1"
    else:
        auth.get_or_create_token()
        auth_note = f"X-API-Key auth enabled (token file: {auth.token_path()})"
    server = SandboxService((host, port), store=store)
    stop = threading.Event()
    sweeper = threading.Thread(
        target=_sweep_loop,
        args=(store, SWEEP_INTERVAL_S, stop),
        name="loopbox-timeout-sweeper",
        daemon=True,
    )
    sweeper.start()
    print(f"loopbox service listening on http://{host}:{port} ({auth_note})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    serve(
        host=os.environ.get("LOOPBOX_HOST", DEFAULT_HOST),
        port=int(os.environ.get("LOOPBOX_PORT", DEFAULT_PORT)),
    )
