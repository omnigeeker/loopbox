"""Loopbox command line interface.

The CLI mirrors the sandbox lifecycle offered by the SDK::

    loopbox new [--template seatbelt|vz] [--network outbound|all|deny]
    loopbox ls
    loopbox exec <sandbox_id> [--cwd P] [--timeout N] -- <argv...>
    loopbox spawn <sandbox_id> -- <argv...>
    loopbox pause|resume|snapshot|snapshots|restore|fork|rm <sandbox_id>
    loopbox serve          # E2B-compatible HTTP API
    loopbox doctor         # environment self-check
    loopbox harness ...    # eval harness (delegates to loopbox.harness)
    loopbox loop ...       # loop engine (delegates to loopbox.loop.cli)

The CLI drives the registry (:mod:`loopbox.store`) and the execution
backends directly; it intentionally never imports the SDK layer.

Most commands accept ``--json`` for machine-readable output. Errors are
reported as one-line messages with a nonzero exit code; set
``LOOPBOX_DEBUG=1`` to see full tracebacks.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Sequence

from loopbox.backends import backend_names, get_backend
from loopbox.backends.seatbelt import SeatbeltBackend
from loopbox.store import Store, StoreError, home, new_id, sandbox_dir, snapshot_root

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 31885

# Subcommands whose trailing arguments belong to the sandboxed command (or to
# a lazily-imported sub-CLI) rather than to this parser.
_ARGV_SUBCOMMANDS = {"exec", "spawn"}
_PASSTHROUGH_SUBCOMMANDS = {"harness", "loop"}

_ALL_SUBCOMMANDS = {
    "new",
    "create",  # alias of new
    "ls",
    "exec",
    "spawn",
    "pause",
    "resume",
    "snapshot",
    "snapshots",
    "restore",
    "fork",
    "rm",
    "serve",
    "doctor",
    "harness",
    "loop",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _err(message: str) -> None:
    """Print a one-line error message to stderr."""
    print(f"loopbox: error: {message}", file=sys.stderr)


def _warn(message: str) -> None:
    """Print a one-line warning to stderr."""
    print(f"loopbox: warning: {message}", file=sys.stderr)


def _emit(payload: Any, args: argparse.Namespace, *, human: str | None = None) -> None:
    """Print ``payload`` as JSON when requested, else the ``human`` string."""
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif human is not None:
        print(human)


def _format_time(ts: Any) -> str:
    """Format an epoch timestamp for table output."""
    if not isinstance(ts, (int, float)):
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    """Render a plain-text table with aligned columns."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*["-" * w for w in widths]))
    for row in rows:
        print(fmt.format(*row))


def _parse_kv(pairs: Sequence[Sequence[str]] | None, flag: str) -> dict[str, str]:
    """Parse ``--env``/``--metadata`` values of the form ``KEY=VALUE``.

    Each flag may be repeated and may carry several pairs per occurrence, so
    argparse hands over a list of lists which is flattened here.
    """
    out: dict[str, str] = {}
    for group in pairs or []:
        for pair in group:
            key, sep, value = pair.partition("=")
            if not sep or not key:
                raise ValueError(f"{flag} expects KEY=VALUE pairs, got {pair!r}")
            out[key] = value
    return out


def _want_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


# ---------------------------------------------------------------------------
# subcommand handlers
# ---------------------------------------------------------------------------


def cmd_new(args: argparse.Namespace) -> int:
    """Create a sandbox, register it, and print its id."""
    backend = get_backend(args.template)
    record: dict[str, Any] = {
        "id": new_id(),
        "backend": backend.name,
        "status": "running",
        "network": args.network,
        "env": _parse_kv(args.env, "--env"),
        "metadata": _parse_kv(args.metadata, "--metadata"),
    }
    if args.timeout is not None:
        record["timeout"] = args.timeout
    backend.create(record)
    Store().add(record)
    if _want_json(args):
        print(json.dumps(record, indent=2, sort_keys=True))
    else:
        # Bare id keeps `SID=$(loopbox new)` usable in scripts.
        print(record["id"])
    return 0


def cmd_ls(args: argparse.Namespace) -> int:
    """List registered sandboxes."""
    records = Store().list()
    if _want_json(args):
        print(json.dumps(records, indent=2, sort_keys=True))
        return 0
    if not records:
        _warn("no sandboxes found")
        return 0
    rows = [
        [
            r.get("id", "?"),
            r.get("backend", "?"),
            r.get("status", "?"),
            r.get("network", "?"),
            _format_time(r.get("created_at")),
        ]
        for r in records
    ]
    _print_table(["ID", "BACKEND", "STATUS", "NETWORK", "CREATED"], rows)
    return 0


def _load(store: Store, record_id: str) -> tuple[dict, Any]:
    """Fetch a record and instantiate its backend."""
    record = store.get(record_id)
    return record, get_backend(record.get("backend"))


def _command_argv(args: argparse.Namespace, usage: str) -> list[str]:
    """Extract the sandboxed command line captured after ``--``."""
    argv = list(getattr(args, "command_argv", None) or [])
    if not argv:
        raise ValueError(f"no command given (usage: {usage})")
    return argv


def cmd_exec(args: argparse.Namespace) -> int:
    """Run one command inside a sandbox and mirror its exit code."""
    store = Store()
    record, backend = _load(store, args.sandbox_id)
    argv = _command_argv(args, f"loopbox exec {args.sandbox_id} [options] -- <argv...>")
    timeout = args.timeout if args.timeout is not None else record.get("timeout")
    result = backend.exec(record, argv, cwd=args.cwd, timeout=timeout)
    if _want_json(args):
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        sys.stdout.write(result.stdout)
        sys.stdout.flush()
        sys.stderr.write(result.stderr)
        sys.stderr.flush()
    return result.exit_code


def cmd_spawn(args: argparse.Namespace) -> int:
    """Start a detached long-running process inside a sandbox."""
    store = Store()
    record, backend = _load(store, args.sandbox_id)
    argv = _command_argv(args, f"loopbox spawn {args.sandbox_id} -- <argv...>")
    pgid = backend.spawn(record, argv)
    # spawn() records the new process group under record["engine"]; persist it
    # so pause/resume/kill can find every process later.
    store.update(record["id"], engine=record.get("engine"))
    _emit({"sandbox_id": record["id"], "pgid": pgid}, args, human=str(pgid))
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    """Freeze all processes of a sandbox."""
    store = Store()
    record, backend = _load(store, args.sandbox_id)
    backend.pause(record)
    store.update(record["id"], engine=record.get("engine"), status="paused")
    _emit({"id": record["id"], "status": "paused"}, args, human=f"paused {record['id']}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume a paused sandbox."""
    store = Store()
    record, backend = _load(store, args.sandbox_id)
    backend.resume(record)
    store.update(record["id"], engine=record.get("engine"), status="running")
    _emit({"id": record["id"], "status": "running"}, args, human=f"resumed {record['id']}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Capture a snapshot of a sandbox and print its snapshot id."""
    store = Store()
    record, backend = _load(store, args.sandbox_id)
    snap_id = backend.snapshot(record, name=args.name)
    store.update(record["id"], engine=record.get("engine"))
    _emit(
        {"sandbox_id": record["id"], "snapshot_id": snap_id},
        args,
        human=snap_id,
    )
    return 0


def cmd_snapshots(args: argparse.Namespace) -> int:
    """List snapshots of a sandbox."""
    store = Store()
    record, backend = _load(store, args.sandbox_id)
    snapshots = backend.list_snapshots(record)
    if _want_json(args):
        print(json.dumps(snapshots, indent=2, sort_keys=True))
        return 0
    if not snapshots:
        _warn(f"sandbox {record['id']} has no snapshots")
        return 0
    rows = [
        [s.get("snapshot_id", "?"), _format_time(s.get("created_at")), s.get("kind", "?")]
        for s in snapshots
    ]
    _print_table(["SNAPSHOT", "CREATED", "KIND"], rows)
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    """Roll a sandbox back to a snapshot."""
    store = Store()
    record, backend = _load(store, args.sandbox_id)
    backend.restore(record, args.snapshot_id)
    store.update(record["id"], engine=record.get("engine"))
    _emit(
        {"id": record["id"], "restored_to": args.snapshot_id},
        args,
        human=f"restored {record['id']} to {args.snapshot_id}",
    )
    return 0


def cmd_fork(args: argparse.Namespace) -> int:
    """Clone a sandbox (or one of its snapshots) into a new sandbox."""
    store = Store()
    record, backend = _load(store, args.sandbox_id)
    child = backend.fork(record, args.snapshot)
    # The seatbelt backend registers the child itself; other backends may not.
    try:
        store.get(child["id"])
    except StoreError:
        store.add(child)
    if _want_json(args):
        print(json.dumps(child, indent=2, sort_keys=True))
    else:
        print(child["id"])
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    """Kill a sandbox and drop it from the registry.

    With ``--purge`` the on-disk state (workspace, snapshots, VM bundle) is
    deleted as well; without it files are kept for forensics.
    """
    store = Store()
    record = store.get(args.sandbox_id)
    sid = record["id"]
    try:
        get_backend(record.get("backend")).kill(record)
    except Exception as exc:  # rm must stay best-effort and always clean up
        _warn(f"kill did not complete cleanly for {sid}: {exc}")
    store.remove(sid)
    if args.purge:
        for path in (sandbox_dir(sid), snapshot_root(sid), home() / "vms" / sid):
            shutil.rmtree(path, ignore_errors=True)
    human = f"removed {sid}" + (" (files purged)" if args.purge else "")
    _emit({"id": sid, "removed": True, "purged": bool(args.purge)}, args, human=human)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the E2B-compatible HTTP service."""
    try:
        from loopbox.service import serve
    except ImportError:
        _err("the HTTP service (loopbox.service) is not available yet")
        return 2
    serve(host=args.host, port=args.port)
    return 0


# -- doctor ------------------------------------------------------------------


def _apfs_clone_test() -> tuple[bool, str]:
    """Verify APFS copy-on-write clones work in a throwaway tempdir."""
    tmp = Path(tempfile.mkdtemp(prefix="loopbox-doctor-"))
    try:
        src = tmp / "src"
        dst = tmp / "dst"
        src.write_text("loopbox", encoding="utf-8")
        proc = subprocess.run(
            ["cp", "-c", str(src), str(dst)],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and dst.exists():
            return True, "copy-on-write clone succeeded"
        return False, proc.stderr.strip() or "clonefile copy failed (not APFS?)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _seatbelt_smoke_test() -> tuple[bool, str]:
    """Run ``echo ok`` inside a throwaway seatbelt sandbox, then clean up."""
    if shutil.which("sandbox-exec") is None:
        return False, "skipped: sandbox-exec not found"
    sid = new_id("doctor")
    backend = SeatbeltBackend()
    record = {"id": sid, "backend": backend.name, "network": "deny", "env": {}}
    try:
        backend.create(record)
        result = backend.exec(record, ["echo", "ok"], timeout=15)
        out = result.stdout.strip()
        if result.exit_code == 0 and out == "ok":
            return True, f"echo ok -> ok ({result.duration_s:.2f}s)"
        return False, f"unexpected output {out!r} (exit code {result.exit_code})"
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(sandbox_dir(sid), ignore_errors=True)


def cmd_doctor(args: argparse.Namespace) -> int:
    """Run environment self-checks and report what would break."""
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str, *, optional: bool = False) -> None:
        checks.append({"name": name, "ok": ok, "optional": optional, "detail": detail})

    machine = platform.machine()
    check("arm64 architecture", machine == "arm64", f"platform.machine() -> {machine}")

    mac_ver = platform.mac_ver()[0]
    try:
        major = int(mac_ver.split(".", 1)[0])
    except (ValueError, IndexError):
        major = 0
    check(
        "macOS version",
        major >= 13,
        f"macOS {mac_ver or 'unknown'} (13+ required; vz backend needs 14+)",
    )

    sandbox_exec = shutil.which("sandbox-exec")
    check("sandbox-exec available", sandbox_exec is not None, sandbox_exec or "not found in PATH")

    apfs_ok, apfs_detail = _apfs_clone_test()
    check("APFS clonefile support", apfs_ok, apfs_detail)

    repo_runner = (
        Path(__file__).resolve().parent.parent.parent
        / "vzrunner"
        / ".build"
        / "release"
        / "vzrunner"
    )
    vzrunner = shutil.which("vzrunner") or (str(repo_runner) if repo_runner.exists() else None)
    check(
        "vzrunner helper (vz backend)",
        vzrunner is not None,
        vzrunner or "not built; optional - build with: cd vzrunner && ./build.sh",
        optional=True,
    )

    smoke_ok, smoke_detail = _seatbelt_smoke_test()
    check("seatbelt smoke test (echo ok)", smoke_ok, smoke_detail)

    failures = [c for c in checks if not c["ok"] and not c["optional"]]
    if _want_json(args):
        print(json.dumps({"ok": not failures, "checks": checks}, indent=2, sort_keys=True))
    else:
        for c in checks:
            status = "ok" if c["ok"] else ("warn" if c["optional"] else "FAIL")
            print(f"[{status:>4}] {c['name']}: {c['detail']}")
        print("doctor: all checks passed" if not failures else f"doctor: {len(failures)} check(s) failed")
    return 0 if not failures else 1


# -- lazily-imported sub-CLIs -------------------------------------------------


def cmd_harness(args: argparse.Namespace) -> int:
    """Delegate to the eval harness, passing arguments through verbatim."""
    try:
        from loopbox.harness import harness_main
    except ImportError:
        _err("the eval harness (loopbox.harness) is not available yet")
        return 2
    result = harness_main(list(getattr(args, "command_argv", None) or []))
    return int(result or 0)


def cmd_loop(args: argparse.Namespace) -> int:
    """Delegate to the loop engine CLI, passing arguments through verbatim."""
    try:
        from loopbox.loop.cli import main as loop_main
    except ImportError:
        _err("the loop engine (loopbox.loop.cli) is not available yet")
        return 2
    result = loop_main(list(getattr(args, "command_argv", None) or []))
    return int(result or 0)


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    # SUPPRESS keeps a subcommand-level flag from clobbering a global `--json`
    # given before the subcommand when it is not repeated there.
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="emit machine-readable JSON",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="loopbox",
        description="Local-first, E2B-compatible sandboxes for macOS on Apple Silicon.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON (also accepted after most subcommands)",
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="<command>")

    p_new = sub.add_parser(
        "new",
        aliases=["create"],
        help="create a sandbox and print its id",
    )
    p_new.add_argument(
        "--template",
        choices=backend_names(),
        default=SeatbeltBackend.name,
        help="isolation engine to use (default: seatbelt)",
    )
    p_new.add_argument(
        "--network",
        choices=["outbound", "all", "deny"],
        default="outbound",
        help="network policy inside the sandbox (default: outbound)",
    )
    p_new.add_argument(
        "--timeout",
        type=float,
        metavar="N",
        help="default timeout in seconds applied to exec calls",
    )
    p_new.add_argument(
        "--env",
        nargs="+",
        action="append",
        metavar="K=V",
        help="environment variables injected into every command (repeatable)",
    )
    p_new.add_argument(
        "--metadata",
        nargs="+",
        action="append",
        metavar="K=V",
        help="free-form labels stored with the sandbox (repeatable)",
    )
    _add_json_flag(p_new)
    p_new.set_defaults(func=cmd_new)

    p_ls = sub.add_parser("ls", help="list sandboxes")
    _add_json_flag(p_ls)
    p_ls.set_defaults(func=cmd_ls)

    p_exec = sub.add_parser(
        "exec",
        help="run a command inside a sandbox: loopbox exec <id> [options] -- <argv...>",
    )
    p_exec.add_argument("sandbox_id", help="sandbox id (sbx_...)")
    p_exec.add_argument("--cwd", metavar="P", help="working directory relative to the workspace")
    p_exec.add_argument("--timeout", type=float, metavar="N", help="timeout in seconds")
    _add_json_flag(p_exec)
    p_exec.set_defaults(func=cmd_exec)

    p_spawn = sub.add_parser(
        "spawn",
        help="start a detached process: loopbox spawn <id> -- <argv...>",
    )
    p_spawn.add_argument("sandbox_id", help="sandbox id (sbx_...)")
    _add_json_flag(p_spawn)
    p_spawn.set_defaults(func=cmd_spawn)

    p_pause = sub.add_parser("pause", help="freeze all processes of a sandbox")
    p_pause.add_argument("sandbox_id")
    _add_json_flag(p_pause)
    p_pause.set_defaults(func=cmd_pause)

    p_resume = sub.add_parser("resume", help="resume a paused sandbox")
    p_resume.add_argument("sandbox_id")
    _add_json_flag(p_resume)
    p_resume.set_defaults(func=cmd_resume)

    p_snap = sub.add_parser("snapshot", help="capture a snapshot of a sandbox")
    p_snap.add_argument("sandbox_id")
    p_snap.add_argument("--name", metavar="N", help="human-friendly snapshot name")
    _add_json_flag(p_snap)
    p_snap.set_defaults(func=cmd_snapshot)

    p_snaps = sub.add_parser("snapshots", help="list snapshots of a sandbox")
    p_snaps.add_argument("sandbox_id")
    _add_json_flag(p_snaps)
    p_snaps.set_defaults(func=cmd_snapshots)

    p_restore = sub.add_parser("restore", help="roll a sandbox back to a snapshot")
    p_restore.add_argument("sandbox_id")
    p_restore.add_argument("snapshot_id")
    _add_json_flag(p_restore)
    p_restore.set_defaults(func=cmd_restore)

    p_fork = sub.add_parser("fork", help="clone a sandbox into a new one")
    p_fork.add_argument("sandbox_id")
    p_fork.add_argument("--snapshot", metavar="S", help="fork from this snapshot instead of live state")
    _add_json_flag(p_fork)
    p_fork.set_defaults(func=cmd_fork)

    p_rm = sub.add_parser("rm", help="kill a sandbox and drop it from the registry")
    p_rm.add_argument("sandbox_id")
    p_rm.add_argument(
        "--purge",
        action="store_true",
        help="also delete workspace files, snapshots and VM bundle from disk",
    )
    _add_json_flag(p_rm)
    p_rm.set_defaults(func=cmd_rm)

    p_serve = sub.add_parser("serve", help="run the E2B-compatible HTTP service")
    p_serve.add_argument("--host", default=DEFAULT_HOST, help=f"bind address (default: {DEFAULT_HOST})")
    p_serve.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"bind port (default: {DEFAULT_PORT})",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_doctor = sub.add_parser("doctor", help="check that this machine can run loopbox")
    _add_json_flag(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_harness = sub.add_parser(
        "harness",
        help="run the eval harness; all following arguments are passed through",
    )
    p_harness.set_defaults(func=cmd_harness)

    p_loop = sub.add_parser(
        "loop",
        help="run the loop engine; all following arguments are passed through",
    )
    p_loop.set_defaults(func=cmd_loop)

    return parser


def _find_subcommand(tokens: Sequence[str]) -> int | None:
    """Return the index of the first token naming a known subcommand."""
    for i, token in enumerate(tokens):
        if token in _ALL_SUBCOMMANDS:
            return i
    return None


def _dispatch(args: argparse.Namespace) -> int:
    """Run the selected handler, converting failures into clean exit codes."""
    func = getattr(args, "func", None)
    if func is None:
        build_parser().print_help()
        return 2
    try:
        return int(func(args) or 0)
    except subprocess.TimeoutExpired as exc:
        _err(f"command timed out after {exc.timeout}s")
        return 124
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
    except (StoreError, FileNotFoundError, ValueError, RuntimeError, NotImplementedError, OSError) as exc:
        if os.environ.get("LOOPBOX_DEBUG"):
            traceback.print_exc()
        else:
            _err(str(exc))
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the ``loopbox`` CLI."""
    tokens = list(sys.argv[1:] if argv is None else argv)
    command_argv: list[str] | None = None
    sub_idx = _find_subcommand(tokens)

    if sub_idx is not None and tokens[sub_idx] in _PASSTHROUGH_SUBCOMMANDS:
        trailing = tokens[sub_idx + 1 :]
        # Keep `-h/--help` on the argparse path; forward everything else
        # verbatim so the sub-CLI sees its own flags untouched.
        if trailing and trailing not in (["-h"], ["--help"]):
            if trailing[0] == "--":
                trailing = trailing[1:]
            func = cmd_harness if tokens[sub_idx] == "harness" else cmd_loop
            args = argparse.Namespace(
                func=func,
                command_argv=trailing,
                json="--json" in tokens[:sub_idx],
            )
            return _dispatch(args)
    elif sub_idx is not None and tokens[sub_idx] in _ARGV_SUBCOMMANDS:
        trailing = tokens[sub_idx + 1 :]
        # argparse.REMAINDER would swallow options placed after the positional,
        # so the separator is honored by splitting the token stream ourselves.
        if "--" in trailing:
            sep = trailing.index("--")
            head, command_argv = trailing[:sep], trailing[sep + 1 :]
            tokens = tokens[: sub_idx + 1] + head

    parser = build_parser()
    args = parser.parse_args(tokens)
    if command_argv is not None:
        args.command_argv = command_argv
    return _dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
