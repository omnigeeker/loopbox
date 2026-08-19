"""CLI for the loop engine: ``loopbox loop ...``.

Subcommands::

    loopbox loop new --goal TEXT [--sandbox TEMPLATE]
    loopbox loop run <loop_id> [--max-steps N] [--max-seconds S] [--auto-approve]
    loopbox loop status <loop_id> [--json]
    loopbox loop approve <loop_id> [gate_id] [--note TEXT]
    loopbox loop reject <loop_id> [gate_id] [--reason TEXT]
    loopbox loop steer <loop_id> --note TEXT
    loopbox loop history <loop_id> [--json]

``run`` exit codes (see :mod:`loopbox.loop.engine`): 0 goal met, 1 failed,
2 stopped by budget/interrupt, 3 blocked on a pending gate.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from loopbox.loop import engine, gates, state


def _age(ts: float | None) -> str:
    if not ts:
        return "-"
    delta = max(0, int(time.time() - ts))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _load(loop_id: str) -> dict[str, Any]:
    """Load a ledger, or exit-style error return via LoopError propagation."""
    return state.load_ledger(loop_id)


def _status_text(ledger: dict[str, Any]) -> str:
    quota = ledger["quota"]
    lines = [
        f"loop:     {ledger['id']}",
        f"goal:     {ledger['goal']}",
        f"status:   {ledger['status']}",
        f"template: {ledger.get('template') or '(default)'}",
        f"sandbox:  {ledger.get('sandbox_id') or '-'}",
        (
            f"quota:    {quota['steps_used']}/{quota['max_steps']} steps, "
            f"{quota['seconds_used']:.0f}/{quota['max_seconds']:.0f} seconds used"
        ),
        f"created:  {_age(ledger.get('created_at'))}",
    ]
    todos = ledger["todos"]
    if todos:
        counts: dict[str, int] = {}
        for t in todos:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        lines.append(f"todos:    {summary}")
    gate = gates.pending_gate(ledger)
    if gate is not None:
        lines.append(f"PENDING GATE: {gate['id']} ({gate['type']}): {gate['question']}")
    if ledger["decisions"]:
        last = ledger["decisions"][-1]
        lines.append(f"last decision: [{last['kind']}] {last['summary']} ({last['source']})")
    lines.append(f"ledger:   {state.loop_dir(ledger['id'])}")
    return "\n".join(lines)


# -- subcommand handlers -------------------------------------------------------


def _cmd_new(args: argparse.Namespace) -> int:
    ledger = state.create_loop(
        args.goal,
        args.sandbox,
        max_steps=args.max_steps,
        max_seconds=args.max_seconds,
    )
    print(f"created loop {ledger['id']}")
    print(f"goal: {ledger['goal']}")
    print(f"next: loopbox loop run {ledger['id']}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    return engine.run_loop(
        args.loop_id,
        auto_approve=args.auto_approve,
        max_steps=args.max_steps,
        max_seconds=args.max_seconds,
    )


def _cmd_status(args: argparse.Namespace) -> int:
    ledger = _load(args.loop_id)
    if args.json:
        print(json.dumps(ledger, indent=2, sort_keys=True))
        return 0
    print(_status_text(ledger))
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    ledger = _load(args.loop_id)
    gate = gates.resolve_gate(ledger, args.gate_id, status=gates.APPROVED, note=args.note or "")
    outcome = gates.apply_gate_outcome(ledger, gate) or "approved"
    print(f"gate {gate['id']} {outcome}" + (f" ({gate['note']})" if gate["note"] else ""))
    if outcome != "finished":
        print(f"resume: loopbox loop run {args.loop_id}")
    return 0


def _cmd_reject(args: argparse.Namespace) -> int:
    ledger = _load(args.loop_id)
    gate = gates.resolve_gate(ledger, args.gate_id, status=gates.REJECTED, note=args.reason or "")
    outcome = gates.apply_gate_outcome(ledger, gate) or "rejected"
    print(f"gate {gate['id']} {outcome}" + (f" ({gate['note']})" if gate["note"] else ""))
    if outcome == "rejected":
        print(f"loop {args.loop_id} marked failed")
    return 0


def _cmd_steer(args: argparse.Namespace) -> int:
    ledger = _load(args.loop_id)
    gate = gates.pending_gate(ledger)
    if gate is not None:
        resolved = gates.resolve_gate(ledger, gate["id"], status=gates.STEERED, note=args.note)
        gates.apply_gate_outcome(ledger, resolved)
        print(f"gate {resolved['id']} steered with note: {args.note}")
    else:
        state.add_decision(
            ledger, "steer", args.note, rationale="via CLI", source=state.SOURCE_HUMAN
        )
        todo = state.add_todo_from_note(ledger, args.note)
        state.save_ledger(ledger)
        print(f"recorded steer note as todo {todo['id']}: {todo['title']}")
    print(f"resume: loopbox loop run {args.loop_id}")
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    ledger = _load(args.loop_id)
    if args.json:
        print(json.dumps(ledger["run_history"], indent=2, sort_keys=True))
        return 0
    if not ledger["run_history"]:
        print("no steps recorded yet")
        return 0
    for entry in ledger["run_history"]:
        mark = "ok" if entry["ok"] else f"FAILED(exit {entry['exit_code']})"
        command = entry.get("command") or "-"
        note = f" -- {entry['note']}" if entry.get("note") else ""
        print(
            f"step {entry['step']:>4}  {_age(entry.get('created_at')):>8}  "
            f"{entry['action']:<6} {command!r} -> {mark} ({entry['duration_s']:.1f}s){note}"
        )
    return 0


# -- parser --------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopbox loop",
        description=(
            "LoopX-style durable loop engine: goals, todos, decisions, evidence, "
            "quota and human gates checkpointed per step."
        ),
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_new = sub.add_parser("new", help="Create a new loop for a goal.")
    p_new.add_argument("--goal", required=True, help="The durable objective of the loop.")
    p_new.add_argument(
        "--sandbox",
        default=None,
        metavar="TEMPLATE",
        help="Sandbox template used for self-checks (default: SDK default).",
    )
    p_new.add_argument("--max-steps", type=int, default=state.DEFAULT_MAX_STEPS)
    p_new.add_argument("--max-seconds", type=float, default=state.DEFAULT_MAX_SECONDS)
    p_new.set_defaults(func=_cmd_new)

    p_run = sub.add_parser("run", help="Run (or resume) a loop until a stop condition.")
    p_run.add_argument("loop_id")
    p_run.add_argument("--max-steps", type=int, default=None, help="Persist a new step budget.")
    p_run.add_argument("--max-seconds", type=float, default=None, help="Persist a new time budget.")
    p_run.add_argument(
        "--auto-approve",
        action="store_true",
        help="Resolve gates automatically (batch/CI mode).",
    )
    p_run.set_defaults(func=_cmd_run)

    p_status = sub.add_parser("status", help="Show the ledger summary of a loop.")
    p_status.add_argument("loop_id")
    p_status.add_argument("--json", action="store_true", help="Dump the full ledger as JSON.")
    p_status.set_defaults(func=_cmd_status)

    p_approve = sub.add_parser("approve", help="Approve the pending gate (default: latest).")
    p_approve.add_argument("loop_id")
    p_approve.add_argument("gate_id", nargs="?", default=None)
    p_approve.add_argument("--note", default="", help="Optional note recorded with the approval.")
    p_approve.set_defaults(func=_cmd_approve)

    p_reject = sub.add_parser("reject", help="Reject the pending gate (default: latest).")
    p_reject.add_argument("loop_id")
    p_reject.add_argument("gate_id", nargs="?", default=None)
    p_reject.add_argument("--reason", default="", help="Why the gate is rejected.")
    p_reject.set_defaults(func=_cmd_reject)

    p_steer = sub.add_parser(
        "steer",
        help="Steer the loop with a note ('run: <cmd>' enqueues a sandbox command).",
    )
    p_steer.add_argument("loop_id")
    p_steer.add_argument("--note", required=True, help="Human steering note.")
    p_steer.set_defaults(func=_cmd_steer)

    p_history = sub.add_parser("history", help="Show the executed step history.")
    p_history.add_argument("loop_id")
    p_history.add_argument("--json", action="store_true", help="Dump run_history as JSON.")
    p_history.set_defaults(func=_cmd_history)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``loopbox loop`` subcommand family."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (state.LoopError, gates.GateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
