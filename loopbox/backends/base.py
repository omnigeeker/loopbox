"""Backend protocol shared by all sandbox isolation engines."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


@dataclass
class ExecResult:
    """Result of one command executed inside a sandbox."""

    stdout: str
    stderr: str
    exit_code: int
    duration_s: float
    command: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "command": self.command,
            "command_line": shlex.join(self.command) if self.command else "",
        }


class Backend(Protocol):
    """Isolation engine contract.

    Backends receive the mutable sandbox *record* (a plain dict persisted by
    the store) and may stash engine-specific state under ``record["engine"]``.
    """

    name: str

    def create(self, record: dict) -> None:
        """Prepare engine state for a freshly registered sandbox record."""
        ...

    def exec(
        self,
        record: dict,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Run one command to completion and capture its output."""
        ...

    def spawn(self, record: dict, argv: Sequence[str], *, env: dict[str, str] | None = None) -> int:
        """Start a long-running process; returns its process-group id."""
        ...

    def pause(self, record: dict) -> None:
        """Freeze all sandbox activity. The sandbox must be resumable."""
        ...

    def resume(self, record: dict) -> None:
        """Resume a paused sandbox."""
        ...

    def snapshot(self, record: dict, name: str | None = None) -> str:
        """Capture the sandbox state; returns a snapshot id."""
        ...

    def list_snapshots(self, record: dict) -> list[dict]:
        ...

    def restore(self, record: dict, snapshot_id: str) -> None:
        """Roll the sandbox back to a snapshot."""
        ...

    def fork(self, record: dict, snapshot_id: str | None = None) -> dict:
        """Create a new sandbox record cloned from this one (or a snapshot)."""
        ...

    def kill(self, record: dict) -> None:
        """Stop everything and release resources."""
        ...
