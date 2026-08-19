"""Virtualization.framework backend (experimental).

This backend delegates to the ``vzrunner`` helper binary (a small Swift
program under ``vzrunner/`` in this repository). It boots a minimal ARM64
Linux guest with the sandbox workspace attached via VirtioFS.

Why a VM backend when Seatbelt exists? Whole-machine state:

- ``vz pause``   -> ``VZVirtualMachine.pause`` (or saveMachineStateToURL)
- ``vz fork``    -> APFS clone of the disk image + saved machine state
- ``vz resume``  -> restoreMachineStateFromURL on the cloned bundle

That gives true pause/fork/resume of a running agent, including its in-memory
state -- something process-level isolation cannot do.

Status: EXPERIMENTAL. The helper must be built first:

    cd vzrunner && ./build.sh        # requires Xcode CLT, macOS 14+

The backend raises a clear error until the binary is present.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Sequence

from loopbox.backends.base import ExecResult
from loopbox.store import Store, home, new_id


def _find_vzrunner() -> Path:
    here = Path(__file__).resolve().parent.parent.parent / "vzrunner" / ".build" / "release" / "vzrunner"
    # shutil.which() returns str | None; ``here`` is already a Path.
    for candidate in (shutil.which("vzrunner"), here):
        if candidate is None:
            continue
        path = candidate if isinstance(candidate, Path) else Path(candidate)
        if path.is_file():
            return path
    raise FileNotFoundError(
        "vzrunner binary not found. Build it with: cd vzrunner && ./build.sh "
        "(requires Xcode Command Line Tools and macOS 14+)."
    )


class VzBackend:
    name = "vz"

    def _runner(self) -> Path:
        return _find_vzrunner()

    def create(self, record: dict) -> None:
        bundle = home() / "vms" / record["id"]
        bundle.mkdir(parents=True, exist_ok=True)
        record["engine"] = {"bundle": str(bundle)}
        # The caller must provide a guest bundle (kernel + rootfs) via
        # record["engine"]["guest_bundle"]; see docs/vz.md.

    def _run(self, args: Sequence[str], timeout: float | None = None) -> str:
        proc = subprocess.run(
            [str(self._runner()), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"vzrunner failed: {proc.stderr.strip()}")
        return proc.stdout

    def exec(self, record: dict, argv: Sequence[str], *, cwd=None, env=None, timeout=None) -> ExecResult:
        bundle = (record.get("engine") or {}).get("bundle")
        if not bundle:
            raise RuntimeError(f"sandbox {record['id']} has no VM bundle")
        started = time.monotonic()
        out = self._run(["exec", "--bundle", bundle, "--", *argv], timeout=timeout)
        return ExecResult(stdout=out, stderr="", exit_code=0,
                          duration_s=time.monotonic() - started, command=[str(a) for a in argv])

    def spawn(self, record: dict, argv: Sequence[str], *, env=None) -> int:
        raise NotImplementedError("vz backend runs commands via the guest agent; use exec()")

    def pause(self, record: dict) -> None:
        bundle = record["engine"]["bundle"]
        self._run(["pause", "--bundle", bundle])

    def resume(self, record: dict) -> None:
        bundle = record["engine"]["bundle"]
        self._run(["resume", "--bundle", bundle])

    def snapshot(self, record: dict, name: str | None = None) -> str:
        snap_id = name or new_id("snap")
        bundle = record["engine"]["bundle"]
        self._run(["snapshot", "--bundle", bundle, "--name", snap_id])
        return snap_id

    def list_snapshots(self, record: dict) -> list[dict]:
        bundle = Path(record["engine"]["bundle"]) / "snapshots"
        if not bundle.exists():
            return []
        return [{"snapshot_id": p.name, "kind": "vm-state"} for p in sorted(bundle.iterdir())]

    def restore(self, record: dict, snapshot_id: str) -> None:
        self._run(["restore", "--bundle", record["engine"]["bundle"], "--name", snapshot_id])

    def fork(self, record: dict, snapshot_id: str | None = None) -> dict:
        child_id = new_id("sbx")
        src = Path(record["engine"]["bundle"])
        dst = home() / "vms" / child_id
        # APFS clone of the whole VM bundle (disk image + saved state).
        subprocess.run(["cp", "-Rc", str(src), str(dst)], check=True)
        child = {
            **record,
            "id": child_id,
            "parent_id": record["id"],
            "created_at": time.time(),
            # Copy mutable fields so the child never aliases the parent's.
            "env": dict(record.get("env") or {}),
            "metadata": dict(record.get("metadata") or {}),
            "engine": {"bundle": str(dst)},
        }
        # Register the child: the SDK contract is that fork returns an
        # already-registered sandbox (as the seatbelt backend does).
        Store().add(child)
        return child

    def kill(self, record: dict) -> None:
        self._run(["kill", "--bundle", record["engine"]["bundle"]])
