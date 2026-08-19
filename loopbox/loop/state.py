"""Durable per-loop state ledger -- the loop's "state kernel".

Layout (root defaults to ``~/.loopbox``, override with ``LOOPBOX_HOME``)::

    loops/<loop_id>/loop.json     ledger: goal, quota, todos, decisions,
                                  evidence, run_history, gates
    loops/<loop_id>/GATE.md       human-readable rendering of the pending gate
    loops/<loop_id>/gate.json     machine-readable pending gate (user-editable)

The ledger is the single source of truth. ``GATE.md`` / ``gate.json`` are
projections of the current pending gate managed by
:mod:`loopbox.loop.gates`. All writes are atomic (tmp file + rename) so a
killed loop always resumes from the last checkpoint with
``loopbox loop run <loop_id>``.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from loopbox.store import home, new_id

LOOPS_DIR = "loops"
LEDGER_FILE = "loop.json"
SCHEMA_VERSION = 1

DEFAULT_MAX_STEPS = 50
DEFAULT_MAX_SECONDS = 3600.0

# Loop statuses. ``blocked_gate`` means the engine checkpointed and exited
# because a human gate is pending; ``stopped`` means a budget ran out. Both
# are resumable via ``loopbox loop run``.
STATUS_NEW = "new"
STATUS_RUNNING = "running"
STATUS_BLOCKED = "blocked_gate"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
TERMINAL_STATUSES = (STATUS_DONE, STATUS_FAILED)

# Todo statuses.
TODO_PENDING = "pending"
TODO_IN_PROGRESS = "in_progress"
TODO_DONE = "done"

# Decision sources: who produced a decision.
SOURCE_ENGINE = "engine"
SOURCE_HARNESS = "harness"
SOURCE_HUMAN = "human"


class LoopError(Exception):
    """Raised when a loop ledger cannot be created, found, or parsed."""


def loops_root() -> Path:
    """Return the directory holding all loop ledgers."""
    root = home() / LOOPS_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def loop_dir(loop_id: str) -> Path:
    """Return the directory of one loop (it may not exist yet)."""
    return loops_root() / loop_id


def new_loop_id() -> str:
    """Return a short, URL-safe loop id like ``loop_9f2c41ab07d1``."""
    return new_id("loop")


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".loop-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_ledger(ledger: dict[str, Any]) -> None:
    """Checkpoint ``ledger`` to disk atomically; bumps ``updated_at``.

    Every engine mutation goes through this function, so the on-disk ledger
    is always a consistent resume point.
    """
    ledger["updated_at"] = time.time()
    _atomic_write_json(loop_dir(ledger["id"]) / LEDGER_FILE, ledger)


def create_loop(
    goal: str,
    template: str | None = None,
    *,
    max_steps: int = DEFAULT_MAX_STEPS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
) -> dict[str, Any]:
    """Create a new loop ledger for ``goal`` and persist it.

    Args:
        goal: The durable objective the loop works towards.
        template: Sandbox template (backend) used for self-checks; ``None``
            selects the SDK default.
        max_steps: Step budget counted across all ``run`` invocations.
        max_seconds: Execution-time budget in seconds, counted across all
            ``run`` invocations.

    Returns:
        The freshly created ledger dict.

    Raises:
        LoopError: If ``goal`` is empty.
    """
    if not goal or not goal.strip():
        raise LoopError("a loop needs a non-empty goal")
    loop_id = new_loop_id()
    d = loop_dir(loop_id)
    d.mkdir(parents=True, exist_ok=True)
    now = time.time()
    ledger: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "id": loop_id,
        "goal": goal.strip(),
        "template": template,
        "status": STATUS_NEW,
        "sandbox_id": None,
        "created_at": now,
        "updated_at": now,
        "quota": {
            "max_steps": int(max_steps),
            "max_seconds": float(max_seconds),
            "steps_used": 0,
            "seconds_used": 0.0,
        },
        "todos": [],
        "decisions": [],
        "evidence": [],
        "run_history": [],
        "gates": [],
    }
    save_ledger(ledger)
    return ledger


def load_ledger(loop_id: str) -> dict[str, Any]:
    """Load the ledger for ``loop_id``.

    Raises:
        LoopError: If the loop does not exist or its ledger is corrupt.
    """
    path = loop_dir(loop_id) / LEDGER_FILE
    if not path.exists():
        raise LoopError(f"loop {loop_id!r} not found (no ledger at {path})")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise LoopError(f"corrupt ledger for loop {loop_id!r}: {exc}") from exc
    if not isinstance(data, dict) or data.get("id") != loop_id:
        raise LoopError(f"corrupt ledger for loop {loop_id!r}: bad id")
    for key in ("todos", "decisions", "evidence", "run_history", "gates"):
        data.setdefault(key, [])
    data.setdefault(
        "quota",
        {
            "max_steps": DEFAULT_MAX_STEPS,
            "max_seconds": DEFAULT_MAX_SECONDS,
            "steps_used": 0,
            "seconds_used": 0.0,
        },
    )
    return data


def list_loops() -> list[dict[str, Any]]:
    """Return summary dicts of all loops, oldest first.

    Corrupt loop directories are skipped instead of failing the listing.
    """
    out: list[dict[str, Any]] = []
    root = loops_root()
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        try:
            ledger = load_ledger(entry.name)
        except LoopError:
            continue
        quota = ledger["quota"]
        out.append(
            {
                "id": ledger["id"],
                "goal": ledger["goal"],
                "status": ledger["status"],
                "template": ledger.get("template"),
                "sandbox_id": ledger.get("sandbox_id"),
                "created_at": ledger["created_at"],
                "steps_used": quota.get("steps_used", 0),
                "max_steps": quota.get("max_steps", DEFAULT_MAX_STEPS),
                "pending_gate": any(g.get("status") == "pending" for g in ledger["gates"]),
            }
        )
    return out


# -- ledger item helpers -----------------------------------------------------


def add_todo(
    ledger: dict[str, Any],
    title: str,
    *,
    command: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Append a todo and return it (caller checkpoints via save_ledger)."""
    todo = {
        "id": f"t{len(ledger['todos']) + 1}",
        "title": title.strip(),
        "status": TODO_PENDING,
        "command": command,
        "note": note,
        "created_at": time.time(),
        "closed_at": None,
    }
    ledger["todos"].append(todo)
    return todo


def add_todo_from_note(ledger: dict[str, Any], note: str) -> dict[str, Any]:
    """Turn a human steer-note into a todo.

    A note of the form ``"run: <command>"`` becomes a command todo the
    engine executes verbatim in the sandbox; anything else becomes a plain
    todo.
    """
    stripped = note.strip()
    if stripped.lower().startswith("run:"):
        command = stripped[4:].strip()
        return add_todo(ledger, f"run: {command}", command=command, note="from human steer")
    return add_todo(ledger, stripped, note="from human steer")


def find_todo(ledger: dict[str, Any], todo_id: str) -> dict[str, Any] | None:
    """Return the todo with ``todo_id``, or None."""
    for todo in ledger["todos"]:
        if todo.get("id") == todo_id:
            return todo
    return None


def next_pending_todo(ledger: dict[str, Any]) -> dict[str, Any] | None:
    """Return the oldest pending todo, or None."""
    for todo in ledger["todos"]:
        if todo.get("status") == TODO_PENDING:
            return todo
    return None


def close_todo(todo: dict[str, Any], note: str = "") -> None:
    """Mark a todo done."""
    todo["status"] = TODO_DONE
    todo["closed_at"] = time.time()
    if note:
        todo["note"] = note


def add_decision(
    ledger: dict[str, Any],
    kind: str,
    summary: str,
    *,
    rationale: str = "",
    source: str = SOURCE_ENGINE,
) -> dict[str, Any]:
    """Append a semantic decision ("what happens next, and why")."""
    decision = {
        "id": f"d{len(ledger['decisions']) + 1}",
        "step": len(ledger["run_history"]),
        "kind": kind,
        "summary": summary.strip(),
        "rationale": rationale,
        "source": source,
        "created_at": time.time(),
    }
    ledger["decisions"].append(decision)
    return decision


def add_evidence(
    ledger: dict[str, Any],
    kind: str,
    summary: str,
    *,
    command: str | None = None,
    exit_code: int | None = None,
) -> dict[str, Any]:
    """Append an evidence record (command output, verification result, note)."""
    item = {
        "id": f"e{len(ledger['evidence']) + 1}",
        "step": len(ledger["run_history"]),
        "kind": kind,
        "summary": summary.strip(),
        "command": command,
        "exit_code": exit_code,
        "created_at": time.time(),
    }
    ledger["evidence"].append(item)
    return item


def record_step(
    ledger: dict[str, Any],
    *,
    action: str,
    ok: bool,
    command: str | None = None,
    exit_code: int | None = None,
    duration_s: float = 0.0,
    note: str = "",
) -> dict[str, Any]:
    """Append one executed step to ``run_history`` and charge the quota."""
    entry = {
        "step": len(ledger["run_history"]) + 1,
        "action": action,
        "command": command,
        "ok": bool(ok),
        "exit_code": exit_code,
        "duration_s": round(float(duration_s), 3),
        "note": note,
        "created_at": time.time(),
    }
    ledger["run_history"].append(entry)
    quota = ledger["quota"]
    quota["steps_used"] = int(quota.get("steps_used", 0)) + 1
    quota["seconds_used"] = round(float(quota.get("seconds_used", 0.0)) + max(duration_s, 0.0), 3)
    return entry


def quota_remaining(ledger: dict[str, Any]) -> dict[str, float | int]:
    """Return the remaining ``{"steps": int, "seconds": float}`` budget."""
    quota = ledger["quota"]
    return {
        "steps": int(quota.get("max_steps", DEFAULT_MAX_STEPS))
        - int(quota.get("steps_used", 0)),
        "seconds": float(quota.get("max_seconds", DEFAULT_MAX_SECONDS))
        - float(quota.get("seconds_used", 0.0)),
    }
