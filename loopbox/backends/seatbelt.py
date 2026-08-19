"""Seatbelt backend: process-level isolation via macOS ``sandbox-exec``.

Isolation model
---------------
The generated profile starts from ``(deny default)`` and then allows:

- process creation/signals inside the same sandbox;
- read access to system locations and the user's toolchain directories
  (agent CLIs such as ``codex``/``claude``/``dsh`` must stay runnable);
- read+write access ONLY to the sandbox workspace and scratch tmp dirs;
- outbound network (optional; ``network="deny"`` emits no network allows).

Sensitive credential locations (``~/.ssh``, ``~/.gnupg``, keychains, browser
cookies, cloud CLIs) are always explicitly denied -- deny rules win over allow
rules in Seatbelt regardless of order.

Pause/resume use ``SIGSTOP``/``SIGCONT`` on recorded process groups.
Snapshots use APFS copy-on-write clones (``cp -c``), so they are O(1) in the
size of unchanged data.

For untrusted code that needs hard boundaries, use the ``vz`` backend instead;
Seatbelt is a strong *process* sandbox, not a VM.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Sequence

from loopbox.backends.base import ExecResult
from loopbox.store import Store, new_id, snapshot_root, workspace_dir

# Locations that must never be readable from inside a sandbox.
SENSITIVE_DENY_READ = (
    "~/.ssh",
    "~/.gnupg",
    "~/.aws",
    "~/.config/gh",
    "~/Library/Keychains",
    "~/Library/Cookies",
    "~/Library/Application Support/Google/Chrome",
)

# Scratch locations a sandbox may write besides its own workspace.
SCRATCH_RW = (
    "/tmp",
    "/private/tmp",
    "/private/var/folders",
)

# Read-only locations needed by mainstream runtimes (node, python, brew).
SYSTEM_RO = (
    "/",
)


def _expand(path: str) -> str:
    return str(Path(path).expanduser())


def build_profile(
    workspace: Path,
    *,
    network: str = "outbound",
    extra_rw: Sequence[str] = (),
    home_dir: str | None = None,
) -> str:
    """Render a Seatbelt profile for one sandbox workspace.

    ``network`` is ``"outbound"`` (default), ``"all"`` or ``"deny"``.
    """
    home = home_dir or str(Path.home())
    lines: list[str] = [
        "(version 1)",
        "(deny default)",
        "(debug deny)",
        "",
        ";; process control, scoped to this sandbox",
        "(allow process-fork)",
        "(allow process-exec)",
        "(allow process-info*)",
        "(allow signal (target same-sandbox))",
        "(allow sysctl-read)",
        "(allow mach-lookup)",
        "(allow ipc-posix-shm)",
        "(allow user-preference-read)",
        "",
        ";; broad read access so agent runtimes keep working;",
        ";; sensitive stores are carved out below (deny wins).",
        '(allow file-read* (subpath "/"))',
        "",
        ";; writes: workspace + scratch only",
    ]
    writable = [str(workspace), *SCRATCH_RW, *[str(Path(p).expanduser()) for p in extra_rw]]
    for path in writable:
        lines.append(f'(allow file-read* file-write* (subpath "{path}"))')
    lines += [
        "",
        ";; character devices every runtime expects to be writable",
        '(allow file-read* file-write* (literal "/dev/null") (literal "/dev/zero")',
        '    (literal "/dev/tty") (literal "/dev/ptmx") (literal "/dev/urandom") (literal "/dev/random"))',
    ]
    lines += [
        "",
        ";; credential and browser stores are never readable",
    ]
    for path in SENSITIVE_DENY_READ:
        lines.append(f'(deny file-read* file-write* (subpath "{_expand(path)}"))')
    lines.append("")
    if network == "outbound":
        lines.append("(allow network-outbound)")
    elif network == "all":
        lines.append("(allow network*)")
    elif network == "deny":
        lines.append("(deny network*)")
    else:
        raise ValueError(f"unknown network mode {network!r}")
    lines.append("")
    return "\n".join(lines)


def _clone_tree(src: Path, dst: Path) -> None:
    """Clone a directory tree using APFS copy-on-write clones when possible."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    try:
        subprocess.run(
            ["cp", "-Rc", str(src), str(dst)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        shutil.copytree(src, dst, symlinks=True)


class SeatbeltBackend:
    name = "seatbelt"

    # -- lifecycle --------------------------------------------------------

    def create(self, record: dict) -> None:
        ws = workspace_dir(record["id"])
        ws.mkdir(parents=True, exist_ok=True)
        profile = build_profile(
            ws,
            network=record.get("network", "outbound"),
            extra_rw=record.get("extra_rw", ()),
        )
        profile_path = ws.parent / "profile.sb"
        profile_path.write_text(profile, encoding="utf-8")
        record["engine"] = {"profile": str(profile_path), "pgids": []}

    def kill(self, record: dict) -> None:
        engine = record.get("engine") or {}
        for pgid in engine.get("pgids", []):
            self._signal(pgid, signal.SIGKILL)
        engine["pgids"] = []
        # Workspace files are kept on disk for forensics; use `loopbox rm`
        # with --purge to delete them.

    # -- execution --------------------------------------------------------

    def _wrap(self, record: dict, argv: Sequence[str]) -> list[str]:
        profile = (record.get("engine") or {}).get("profile")
        if not profile or not Path(profile).exists():
            raise RuntimeError(f"sandbox {record['id']} has no Seatbelt profile")
        return ["sandbox-exec", "-f", profile, *argv]

    def exec(
        self,
        record: dict,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        # Resolve both sides: the state root may sit behind a symlink (e.g.
        # LOOPBOX_HOME under /tmp on macOS), which must not defeat -- or
        # falsely trigger -- the containment check.
        workdir = workspace_dir(record["id"]).resolve()
        run_cwd = (workdir / cwd).resolve() if cwd else workdir
        if workdir not in run_cwd.parents and run_cwd != workdir:
            raise ValueError("cwd must stay inside the sandbox workspace")
        full_env = dict(os.environ)
        full_env.update(record.get("env") or {})
        full_env.update(env or {})
        started = time.monotonic()
        proc = subprocess.run(
            self._wrap(record, [str(a) for a in argv]),
            cwd=run_cwd,
            env=full_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            start_new_session=True,
        )
        return ExecResult(
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            duration_s=time.monotonic() - started,
            command=[str(a) for a in argv],
        )

    def spawn(self, record: dict, argv: Sequence[str], *, env: dict[str, str] | None = None) -> int:
        """Start a detached long-running process inside the sandbox."""
        full_env = dict(os.environ)
        full_env.update(record.get("env") or {})
        full_env.update(env or {})
        proc = subprocess.Popen(
            self._wrap(record, [str(a) for a in argv]),
            cwd=workspace_dir(record["id"]),
            env=full_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        engine = record.setdefault("engine", {})
        engine.setdefault("pgids", []).append(proc.pid)
        return proc.pid

    # -- pause / resume ---------------------------------------------------

    @staticmethod
    def _signal(pgid: int, sig: int) -> None:
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def pause(self, record: dict) -> None:
        for pgid in (record.get("engine") or {}).get("pgids", []):
            self._signal(pgid, signal.SIGSTOP)

    def resume(self, record: dict) -> None:
        for pgid in (record.get("engine") or {}).get("pgids", []):
            self._signal(pgid, signal.SIGCONT)

    # -- snapshots / fork -------------------------------------------------

    def snapshot(self, record: dict, name: str | None = None) -> str:
        snap_id = new_id("snap") if not name else f"{name}"
        dest = snapshot_root(record["id"]) / snap_id
        _clone_tree(workspace_dir(record["id"]), dest)
        meta = {"snapshot_id": snap_id, "created_at": time.time(), "kind": "workspace-clone"}
        (dest / ".loopbox-snapshot.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )
        return snap_id

    def list_snapshots(self, record: dict) -> list[dict]:
        root = snapshot_root(record["id"])
        if not root.exists():
            return []
        out = []
        for entry in sorted(root.iterdir()):
            meta_file = entry / ".loopbox-snapshot.json"
            if meta_file.exists():
                out.append(json.loads(meta_file.read_text(encoding="utf-8")))
        return out

    def restore(self, record: dict, snapshot_id: str) -> None:
        src = snapshot_root(record["id"]) / snapshot_id
        if not src.exists():
            raise FileNotFoundError(f"snapshot {snapshot_id} not found")
        ws = workspace_dir(record["id"])
        if ws.exists():
            shutil.rmtree(ws)
        _clone_tree(src, ws)
        marker = ws / ".loopbox-snapshot.json"
        if marker.exists():
            marker.unlink()

    def fork(self, record: dict, snapshot_id: str | None = None) -> dict:
        child_id = new_id("sbx")
        child_ws = workspace_dir(child_id)
        if snapshot_id:
            src = snapshot_root(record["id"]) / snapshot_id
            if not src.exists():
                raise FileNotFoundError(f"snapshot {snapshot_id} not found")
        else:
            src = workspace_dir(record["id"])
        _clone_tree(src, child_ws)
        marker = child_ws / ".loopbox-snapshot.json"
        if marker.exists():
            marker.unlink()
        child = {
            "id": child_id,
            "backend": self.name,
            "status": "running",
            "network": record.get("network", "outbound"),
            "env": dict(record.get("env") or {}),
            "metadata": dict(record.get("metadata") or {}),
            "parent_id": record["id"],
            "forked_from_snapshot": snapshot_id,
        }
        self.create(child)
        Store().add(child)
        return child
