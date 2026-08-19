"""Sandbox execution backends.

``seatbelt``  - process-level isolation via the macOS Seatbelt sandbox
                (``sandbox-exec``). Instant startup, write-scoped isolation,
                signal-based pause/resume, APFS clonefile snapshots.
``vz``        - VM-grade isolation via Virtualization.framework through the
                ``vzrunner`` helper binary (experimental). Full machine-state
                save/restore enables true pause/fork/resume of a whole VM.
"""

from __future__ import annotations

from loopbox.backends.base import Backend, ExecResult
from loopbox.backends.seatbelt import SeatbeltBackend

_BACKENDS: dict[str, type[Backend]] = {
    SeatbeltBackend.name: SeatbeltBackend,
}


def get_backend(name: str | None) -> Backend:
    """Instantiate a backend by name; ``None`` selects the default."""
    if name is None:
        name = SeatbeltBackend.name
    if name == "vz":
        # Imported lazily so the Swift helper is only required when used.
        from loopbox.backends.vz import VzBackend

        return VzBackend()
    try:
        return _BACKENDS[name]()
    except KeyError:
        known = ", ".join(sorted([*_BACKENDS, "vz"]))
        raise ValueError(f"unknown backend {name!r} (available: {known})") from None


def backend_names() -> list[str]:
    return sorted([*_BACKENDS, "vz"])


__all__ = ["Backend", "ExecResult", "get_backend", "backend_names"]
