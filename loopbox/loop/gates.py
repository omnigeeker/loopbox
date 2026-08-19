"""Human-in-the-loop gates for the loop engine.

A *gate* is a durable question recorded in the loop ledger. While a gate is
pending the engine must not continue the loop: it either resolves the gate
through an interactive prompt (when stdin is a TTY) or checkpoints and
exits, so the operator can resolve the gate later -- via the CLI
(``loopbox loop approve|reject|steer``) or by editing ``gate.json`` in the
loop directory and re-running.

Gate types (LoopX's "human judgment needed? -> ask a concrete question"):

- ``approve_plan``:  the initial todo plan derived from the goal.
- ``approve_step``:  a risky or ambiguous step, before it runs.
- ``on_failure``:    a step failed; retry, steer, or abort.

Gate states: ``pending`` -> ``approved`` | ``rejected`` | ``steered``.
``steered`` means resolved-with-a-note: the note is recorded as a human
decision and injected into the todo list by the engine.

Gate dict fields beyond the state machine:

- ``on_approve``: engine behavior after approval -- ``"continue"`` (just
  resume), ``"close_todo"`` (also close ``todo_id``), ``"finish"`` (close
  ``todo_id`` if set and mark the whole loop done).
- ``todo_id``: the todo this gate is about, if any.
- ``applied``: whether the outcome of a resolved gate has already been
  folded back into the ledger (see :func:`apply_gate_outcome`).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, TextIO

from loopbox.loop import state

GATE_JSON = "gate.json"
GATE_MD = "GATE.md"

APPROVE_PLAN = "approve_plan"
APPROVE_STEP = "approve_step"
ON_FAILURE = "on_failure"
GATE_TYPES = (APPROVE_PLAN, APPROVE_STEP, ON_FAILURE)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
STEERED = "steered"
RESOLVED_STATES = (APPROVED, REJECTED, STEERED)


class GateError(Exception):
    """Raised for invalid gate operations."""


def pending_gate(ledger: dict[str, Any]) -> dict[str, Any] | None:
    """Return the first pending gate of the ledger, or None."""
    for gate in ledger["gates"]:
        if gate.get("status") == PENDING:
            return gate
    return None


def get_gate(ledger: dict[str, Any], gate_id: str | None = None) -> dict[str, Any]:
    """Return a gate by id; with ``gate_id=None`` the latest pending gate.

    Raises:
        GateError: If no matching gate exists.
    """
    if gate_id is None:
        gate = pending_gate(ledger)
        if gate is None:
            raise GateError(f"loop {ledger['id']} has no pending gate")
        return gate
    for gate in ledger["gates"]:
        if gate.get("id") == gate_id:
            return gate
    raise GateError(f"loop {ledger['id']} has no gate {gate_id!r}")


def gate_json_path(loop_id: str) -> Path:
    return state.loop_dir(loop_id) / GATE_JSON


def gate_md_path(loop_id: str) -> Path:
    return state.loop_dir(loop_id) / GATE_MD


def render_gate_md(gate: dict[str, Any], loop_id: str) -> str:
    """Render the human-readable GATE.md body for one gate."""
    lines = [
        f"# Gate {gate['id']} ({gate['type']}) -- {gate['status']}",
        "",
        f"**Loop:** `{loop_id}`",
        "",
        f"**Question:** {gate['question']}",
        "",
    ]
    if gate.get("context"):
        lines += ["## Context", "", gate["context"], ""]
    if gate["status"] == PENDING:
        lines += [
            "## How to answer",
            "",
            "```bash",
            f"loopbox loop approve {loop_id} [gate_id] [--note TEXT]",
            f"loopbox loop reject {loop_id} [gate_id] [--reason TEXT]",
            f"loopbox loop steer {loop_id} --note TEXT   # 'run: <cmd>' enqueues a command",
            "```",
            "",
            f'You may also edit `{GATE_JSON}` directly: set `"status"` to',
            f'`"approved"`, `"rejected"` or `"steered"` and add a `"note"`.',
            f"Then resume with `loopbox loop run {loop_id}`.",
            "",
        ]
    elif gate.get("note"):
        lines += ["## Resolution note", "", gate["note"], ""]
    return "\n".join(lines)


def write_gate_files(loop_id: str, gate: dict[str, Any]) -> None:
    """Materialize the gate as ``GATE.md`` + ``gate.json`` in the loop dir."""
    state._atomic_write_json(gate_json_path(loop_id), gate)
    gate_md_path(loop_id).write_text(render_gate_md(gate, loop_id), encoding="utf-8")


def request_gate(
    ledger: dict[str, Any],
    gate_type: str,
    question: str,
    *,
    context: str = "",
    todo_id: str | None = None,
    on_approve: str = "continue",
) -> dict[str, Any]:
    """Create a pending gate, checkpoint the ledger, and write the gate files.

    Idempotent with respect to crash-resume: if a gate is already pending it
    is returned unchanged instead of stacking a duplicate (the engine never
    needs two unanswered questions at once).

    Raises:
        GateError: If ``gate_type`` is unknown or ``on_approve`` is invalid.
    """
    if gate_type not in GATE_TYPES:
        raise GateError(f"unknown gate type {gate_type!r} (known: {', '.join(GATE_TYPES)})")
    if on_approve not in ("continue", "close_todo", "finish"):
        raise GateError(f"invalid on_approve behavior {on_approve!r}")
    existing = pending_gate(ledger)
    if existing is not None:
        return existing
    gate = {
        "id": f"gate_{len(ledger['gates']) + 1}",
        "type": gate_type,
        "status": PENDING,
        "question": question.strip(),
        "context": context.strip(),
        "note": "",
        "todo_id": todo_id,
        "on_approve": on_approve,
        "applied": False,
        "created_at": time.time(),
        "resolved_at": None,
    }
    ledger["gates"].append(gate)
    state.save_ledger(ledger)
    write_gate_files(ledger["id"], gate)
    return gate


def resolve_gate(
    ledger: dict[str, Any],
    gate_id: str | None = None,
    *,
    status: str,
    note: str = "",
) -> dict[str, Any]:
    """Resolve a pending gate (default: the latest one) and checkpoint.

    Raises:
        GateError: If the status is invalid or the gate is not pending.
    """
    if status not in RESOLVED_STATES:
        raise GateError(
            f"invalid gate status {status!r} (use one of: {', '.join(RESOLVED_STATES)})"
        )
    gate = get_gate(ledger, gate_id)
    if gate.get("status") != PENDING:
        raise GateError(f"gate {gate['id']} is already {gate.get('status')}")
    gate["status"] = status
    gate["note"] = note.strip()
    gate["resolved_at"] = time.time()
    gate["applied"] = False
    state.save_ledger(ledger)
    write_gate_files(ledger["id"], gate)
    return gate


def apply_gate_outcome(ledger: dict[str, Any], gate: dict[str, Any]) -> str | None:
    """Fold a resolved gate back into the ledger, exactly once.

    Pending gates and already-applied gates return None. Otherwise the
    outcome is recorded (decision, todos, todo/loop status transitions) and
    the ledger checkpointed. Returns one of ``"approved"``, ``"rejected"``,
    ``"steered"``, ``"finished"``.
    """
    if gate.get("status") == PENDING or gate.get("applied"):
        return None
    gate["applied"] = True
    status = gate["status"]
    note = gate.get("note") or ""
    if status == REJECTED:
        state.add_decision(
            ledger,
            gate["type"],
            f"gate {gate['id']} rejected",
            rationale=note,
            source=state.SOURCE_HUMAN,
        )
        ledger["status"] = state.STATUS_FAILED
        state.save_ledger(ledger)
        return "rejected"
    if status == STEERED:
        state.add_decision(
            ledger, "steer", note, rationale=f"via gate {gate['id']}", source=state.SOURCE_HUMAN
        )
        state.add_todo_from_note(ledger, note)
        state.save_ledger(ledger)
        return "steered"
    # approved
    state.add_decision(
        ledger, gate["type"], f"gate {gate['id']} approved", rationale=note,
        source=state.SOURCE_HUMAN,
    )
    behavior = gate.get("on_approve", "continue")
    if behavior in ("close_todo", "finish") and gate.get("todo_id"):
        todo = state.find_todo(ledger, gate["todo_id"])
        if todo is not None:
            state.close_todo(todo, note=f"closed by gate {gate['id']}")
    if behavior == "finish":
        ledger["status"] = state.STATUS_DONE
        state.save_ledger(ledger)
        return "finished"
    state.save_ledger(ledger)
    return "approved"


def sync_gate_from_file(ledger: dict[str, Any]) -> dict[str, Any] | None:
    """Adopt a resolution the user hand-edited into ``gate.json``.

    Returns the resolved gate if the file carried a valid non-pending status
    for the currently pending gate, else None.
    """
    gate = pending_gate(ledger)
    if gate is None:
        return None
    path = gate_json_path(ledger["id"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("id") != gate["id"]:
        return None
    status = data.get("status")
    if status not in RESOLVED_STATES:
        return None
    note = data.get("note") if isinstance(data.get("note"), str) else ""
    return resolve_gate(ledger, gate["id"], status=status, note=note)


# -- interactive resolution ----------------------------------------------------


def prompt_gate(
    gate: dict[str, Any],
    loop_id: str,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> tuple[str, str]:
    """Interactively resolve a gate on the terminal.

    Reads one line per attempt: ``a``/``approve``, ``r [reason]``, or
    ``s <note>`` (steer). Returns ``(status, note)``.
    """
    import sys

    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    print(f"\n=== pending gate {gate['id']} ({gate['type']}) on {loop_id} ===", file=stdout)
    print(f"question: {gate['question']}", file=stdout)
    if gate.get("context"):
        print(gate["context"], file=stdout)
    while True:
        print("[a]pprove / [r]eject [reason] / [s]teer <note> > ", end="", file=stdout, flush=True)
        answer = stdin.readline()
        if answer == "":  # EOF: treat like a rejected read, caller re-checks TTY
            raise GateError("stdin closed while waiting for gate input")
        answer = answer.strip()
        if not answer:
            continue
        head, _, rest = answer.partition(" ")
        head = head.lower()
        rest = rest.strip()
        if head in ("a", "approve"):
            return APPROVED, rest
        if head in ("r", "reject"):
            return REJECTED, rest
        if head in ("s", "steer"):
            if not rest:
                print("steer needs a note, e.g. `s run: pytest -q`", file=stdout)
                continue
            return STEERED, rest
        print("unrecognized answer; use a, r [reason] or s <note>", file=stdout)
