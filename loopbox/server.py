"""E2B-protocol-shaped HTTP API for Loopbox (standard library only).

Run with:  loopbox serve --port 8080

Endpoint map (paths follow the E2B control-plane / envd shapes):

    POST   /sandboxes                          create sandbox
    GET    /sandboxes                          list sandboxes
    GET    /sandboxes/{id}                     sandbox info
    DELETE /sandboxes/{id}                     kill sandbox
    POST   /sandboxes/{id}/pause               pause
    POST   /sandboxes/{id}/resume              resume
    POST   /sandboxes/{id}/fork                fork (body: {"snapshot_id": ...}?)

    POST   /sandboxes/{id}/commands            run command (envd shape)
    GET    /sandboxes/{id}/files?path=...      read file (base64 envelope)
    PUT    /sandboxes/{id}/files               write file {"path","content_b64"}
    GET    /sandboxes/{id}/files/list?path=... list directory
    GET    /sandboxes/{id}/snapshots           list snapshots
    POST   /sandboxes/{id}/snapshots           create snapshot {"name": ...}?
    POST   /sandboxes/{id}/snapshots/{sid}/restore

Every response is JSON. Errors use {"error": "...", "code": <http status>}.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from loopbox.sdk import Sandbox, SandboxError, decode_content, encode_content

_ID = r"(?P<id>[A-Za-z0-9_-]+)"
_SID = r"(?P<sid>[A-Za-z0-9_-]+)"

ROUTES: list[tuple[str, re.Pattern[str], str]] = [
    ("POST", re.compile(r"^/sandboxes$"), "create"),
    ("GET", re.compile(r"^/sandboxes$"), "list"),
    ("GET", re.compile(rf"^/sandboxes/{_ID}$"), "info"),
    ("DELETE", re.compile(rf"^/sandboxes/{_ID}$"), "kill"),
    ("POST", re.compile(rf"^/sandboxes/{_ID}/pause$"), "pause"),
    ("POST", re.compile(rf"^/sandboxes/{_ID}/resume$"), "resume"),
    ("POST", re.compile(rf"^/sandboxes/{_ID}/fork$"), "fork"),
    ("POST", re.compile(rf"^/sandboxes/{_ID}/commands$"), "commands"),
    ("GET", re.compile(rf"^/sandboxes/{_ID}/files$"), "read_file"),
    ("PUT", re.compile(rf"^/sandboxes/{_ID}/files$"), "write_file"),
    ("GET", re.compile(rf"^/sandboxes/{_ID}/files/list$"), "list_files"),
    ("GET", re.compile(rf"^/sandboxes/{_ID}/snapshots$"), "list_snapshots"),
    ("POST", re.compile(rf"^/sandboxes/{_ID}/snapshots$"), "create_snapshot"),
    ("POST", re.compile(rf"^/sandboxes/{_ID}/snapshots/{_SID}/restore$"), "restore_snapshot"),
]


class ApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sandbox(sandbox_id: str) -> Sandbox:
    try:
        return Sandbox.connect(sandbox_id)
    except Exception as exc:
        raise ApiError(404, str(exc)) from exc


def dispatch(action: str, match: re.Match[str], body: dict[str, Any], query: dict[str, str]) -> Any:
    sid = match.groupdict().get("id")

    if action == "create":
        sbx = Sandbox.create(
            template=body.get("template"),
            timeout=body.get("timeout"),
            metadata=body.get("metadata"),
            envs=body.get("envs"),
            network=body.get("network", "outbound"),
        )
        return 201, sbx.info()
    if action == "list":
        return 200, {"sandboxes": Sandbox.list()}
    if action == "info":
        return 200, _sandbox(sid).info()
    if action == "kill":
        _sandbox(sid).kill()
        return 200, {"killed": sid}
    if action == "pause":
        _sandbox(sid).pause()
        return 200, {"paused": sid}
    if action == "resume":
        _sandbox(sid).resume()
        return 200, {"resumed": sid}
    if action == "fork":
        child = _sandbox(sid).fork(snapshot_id=body.get("snapshot_id"))
        return 201, child.info()
    if action == "commands":
        cmd = body.get("command") or body.get("cmd")
        if not cmd:
            raise ApiError(400, "missing 'command'")
        result = _sandbox(sid).commands.run(
            cmd,
            cwd=body.get("cwd"),
            envs=body.get("envs"),
            timeout=body.get("timeout"),
        )
        return 200, result.to_dict()
    if action == "read_file":
        path = query.get("path", "/")
        fmt = query.get("format", "text")
        content = _sandbox(sid).files.read(path, format="bytes")
        return 200, {"path": path, "content_b64": encode_content(content), "format": fmt}
    if action == "write_file":
        data = body.get("content_b64")
        if data is None or "path" not in body:
            raise ApiError(400, "missing 'path' or 'content_b64'")
        _sandbox(sid).files.write(body["path"], decode_content(data))
        return 200, {"written": body["path"]}
    if action == "list_files":
        return 200, {"entries": _sandbox(sid).files.list(query.get("path", "/"))}
    if action == "list_snapshots":
        return 200, {"snapshots": _sandbox(sid).snapshots()}
    if action == "create_snapshot":
        snap_id = _sandbox(sid).snapshot(name=body.get("name"))
        return 201, {"snapshot_id": snap_id}
    if action == "restore_snapshot":
        _sandbox(sid).restore(match.group("sid"))
        return 200, {"restored": match.group("sid")}
    raise ApiError(404, f"unknown action {action}")


class Handler(BaseHTTPRequestHandler):
    server_version = "Loopbox/0.1"
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------

    def _json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError(400, f"invalid JSON body: {exc}") from exc

    def _send(self, code: int, payload: Any) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _route(self, method: str) -> None:
        from urllib.parse import parse_qsl, urlparse

        parsed = urlparse(self.path)
        query = dict(parse_qsl(parsed.query))
        for route_method, pattern, action in ROUTES:
            if route_method != method:
                continue
            match = pattern.match(parsed.path)
            if not match:
                continue
            try:
                code, payload = dispatch(action, match, self._json_body(), query)
            except ApiError as exc:
                self._send(exc.code, {"error": str(exc), "code": exc.code})
            except SandboxError as exc:
                self._send(409, {"error": str(exc), "code": 409})
            except FileNotFoundError as exc:
                self._send(404, {"error": str(exc), "code": 404})
            except Exception as exc:  # noqa: BLE001 - API boundary
                self._send(500, {"error": f"{type(exc).__name__}: {exc}", "code": 500})
            else:
                self._send(code, payload)
            return
        self._send(404, {"error": f"no route for {method} {parsed.path}", "code": 404})

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._route("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._route("DELETE")

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
        return


def serve(host: str = "127.0.0.1", port: int = 8080) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"loopbox API listening on http://{host}:{port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
