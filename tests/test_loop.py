"""Tests for the loop engine (loopbox.loop) driven in-process.

All tests run with the rule-based thinking fallback (the ``no_harness``
fixture also shields them from a real codex/claude CLI on PATH) and a fresh
temp ``LOOPBOX_HOME``; ``run_loop`` is called directly, never through a
subprocess.
"""

from __future__ import annotations

import io
import json
import shutil

import pytest

from loopbox.loop import engine, gates, state
from loopbox.store import home

pytestmark = pytest.mark.skipif(
    shutil.which("sandbox-exec") is None,
    reason="requires macOS sandbox-exec",
)

GOAL = "smoke goal"


def _new_loop(**kwargs) -> dict:
    return state.create_loop(GOAL, max_steps=8, max_seconds=600, **kwargs)


def test_plan_gate_blocks_then_approve_then_budget_stop_then_finish(
    tracked_home, no_harness
):
    """Drive: plan gate -> rc=3 -> approve -> steps until budget -> finish."""
    ledger = _new_loop()
    loop_id = ledger["id"]

    rc = engine.run_loop(loop_id, stream=io.StringIO())
    assert rc == engine.RC_BLOCKED
    blocked = state.load_ledger(loop_id)
    assert blocked["status"] == state.STATUS_BLOCKED
    gate = gates.pending_gate(blocked)
    assert gate is not None and gate["type"] == gates.APPROVE_PLAN
    # GATE.md + gate.json projections exist on disk and explain how to answer.
    gate_md = gates.gate_md_path(loop_id).read_text(encoding="utf-8")
    assert gates.gate_json_path(loop_id).exists()
    assert "approve" in gate_md and gate["id"] in gate_md

    ledger = state.load_ledger(loop_id)
    resolved = gates.resolve_gate(ledger, gate["id"], status=gates.APPROVED)
    assert gates.apply_gate_outcome(ledger, resolved) == "approved"

    # One-step budget: the fallback's exploration step runs, then the budget
    # fires at the next iteration boundary.
    rc = engine.run_loop(loop_id, max_steps=1, stream=io.StringIO())
    assert rc == engine.RC_STOPPED
    stopped = state.load_ledger(loop_id)
    assert stopped["status"] == state.STATUS_STOPPED
    assert stopped["quota"]["steps_used"] == 1
    step = stopped["run_history"][0]
    assert step["command"] == "ls -la" and step["ok"]

    # Confirm completion via the finish gate (as the engine raises it when
    # all todos are closed), approved through the public helpers.
    ledger = state.load_ledger(loop_id)
    finish_gate = gates.request_gate(
        ledger,
        gates.APPROVE_STEP,
        "All todos are closed. Is the goal met?",
        context=f"Goal: {ledger['goal']}",
        on_approve="finish",
    )
    resolved = gates.resolve_gate(ledger, finish_gate["id"], status=gates.APPROVED)
    assert gates.apply_gate_outcome(ledger, resolved) == "finished"
    assert state.load_ledger(loop_id)["status"] == state.STATUS_DONE
    # A done loop is a no-op to run again.
    assert engine.run_loop(loop_id, stream=io.StringIO()) == engine.RC_DONE


def test_steered_command_executes_in_sandbox(tracked_home, no_harness):
    """A human steer note ('run: <cmd>') becomes a todo executed in-sandbox."""
    ledger = _new_loop()
    loop_id = ledger["id"]
    state.add_todo_from_note(ledger, "run: echo steered-step > done.txt")
    state.save_ledger(ledger)

    rc = engine.run_loop(loop_id, stream=io.StringIO())
    # The command ran and the loop then raised the "is the goal met?" gate.
    assert rc == engine.RC_BLOCKED
    reloaded = state.load_ledger(loop_id)
    step = reloaded["run_history"][0]
    assert step["command"] == "echo steered-step > done.txt"
    assert step["ok"] and step["exit_code"] == 0
    assert reloaded["todos"][0]["status"] == state.TODO_DONE
    gate = gates.pending_gate(reloaded)
    assert gate is not None and gate["on_approve"] == "finish"

    # stdout was redirected, so no output evidence; the sandbox-creation note
    # is still recorded.

    workspace = home() / "sandboxes" / reloaded["sandbox_id"] / "workspace"
    assert (workspace / "done.txt").read_text(encoding="utf-8").strip() == "steered-step"

    resolved = gates.resolve_gate(reloaded, gate["id"], status=gates.APPROVED)
    assert gates.apply_gate_outcome(reloaded, resolved) == "finished"


def test_exhausted_seconds_budget_stops(tracked_home, no_harness):
    ledger = _new_loop()
    rc = engine.run_loop(ledger["id"], max_seconds=0, stream=io.StringIO())
    assert rc == engine.RC_STOPPED
    last = state.load_ledger(ledger["id"])["decisions"][-1]
    assert "budget" in last["summary"]


def test_reject_plan_gate_fails_loop(tracked_home, no_harness):
    ledger = _new_loop()
    loop_id = ledger["id"]
    rc = engine.run_loop(loop_id, stream=io.StringIO())
    assert rc == engine.RC_BLOCKED

    ledger = state.load_ledger(loop_id)
    gate = gates.pending_gate(ledger)
    resolved = gates.resolve_gate(ledger, gate["id"], status=gates.REJECTED, note="nope")
    assert gates.apply_gate_outcome(ledger, resolved) == "rejected"

    rc = engine.run_loop(loop_id, stream=io.StringIO())
    assert rc == engine.RC_FAILED
    assert state.load_ledger(loop_id)["status"] == state.STATUS_FAILED


def test_risky_command_raises_gate_not_execution(tracked_home, no_harness):
    """A todo carrying a dangerous command must gate before it ever runs."""
    ledger = _new_loop()
    state.add_todo(ledger, "danger", command="rm -rf /tmp/x")
    state.save_ledger(ledger)

    rc = engine.run_loop(ledger["id"], stream=io.StringIO())
    assert rc == engine.RC_BLOCKED
    reloaded = state.load_ledger(ledger["id"])
    gate = gates.pending_gate(reloaded)
    assert gate is not None
    assert "risky" in gate["question"].lower()
    assert reloaded["run_history"] == []  # nothing executed


def test_cli_new_status_flow(tracked_home, no_harness, capsys):
    from loopbox.loop.cli import main as loop_main

    assert loop_main(["new", "--goal", GOAL, "--max-steps", "2"]) == 0
    out = capsys.readouterr().out
    loop_id = next(l.split()[-1] for l in out.splitlines() if l.startswith("created loop"))

    assert loop_main(["status", loop_id]) == 0
    status_out = capsys.readouterr().out
    assert GOAL in status_out
    assert "status:   new" in status_out

    assert loop_main(["status", loop_id, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["id"] == loop_id
    assert payload["quota"]["max_steps"] == 2

    # Unknown loop id -> clean error, rc 1.
    assert loop_main(["status", "loop_missing"]) == 1


def test_cli_approve_and_resume(tracked_home, no_harness, capsys):
    from loopbox.loop.cli import main as loop_main

    assert loop_main(["new", "--goal", GOAL]) == 0
    loop_id = next(
        l.split()[-1]
        for l in capsys.readouterr().out.splitlines()
        if l.startswith("created loop")
    )
    assert loop_main(["run", loop_id]) == engine.RC_BLOCKED
    capsys.readouterr()
    assert loop_main(["approve", loop_id]) == 0
    out = capsys.readouterr().out
    assert "approved" in out

    # After approval the fallback executes its exploration step, then blocks
    # on the next human gate (no harness installed in tests).
    rc = loop_main(["run", loop_id, "--max-steps", "8"])
    assert rc == engine.RC_BLOCKED
    ledger = state.load_ledger(loop_id)
    assert ledger["quota"]["max_steps"] == 8
    assert ledger["run_history"][0]["command"] == "ls -la"


def test_cli_steer_enqueues_command(tracked_home, no_harness, capsys):
    from loopbox.loop.cli import main as loop_main

    assert loop_main(["new", "--goal", GOAL]) == 0
    loop_id = next(
        l.split()[-1]
        for l in capsys.readouterr().out.splitlines()
        if l.startswith("created loop")
    )
    assert loop_main(["steer", loop_id, "--note", "run: echo cli-steered"]) == 0
    assert "recorded steer note" in capsys.readouterr().out

    rc = loop_main(["run", loop_id])
    assert rc == engine.RC_BLOCKED  # finish gate after the command ran
    ledger = state.load_ledger(loop_id)
    assert ledger["run_history"][0]["command"] == "echo cli-steered"
    gate = gates.pending_gate(ledger)
    assert gate is not None and gate["on_approve"] == "finish"
    assert loop_main(["approve", loop_id]) == 0
    assert "finished" in capsys.readouterr().out
    assert state.load_ledger(loop_id)["status"] == state.STATUS_DONE


def test_cli_reject_marks_failed(tracked_home, no_harness, capsys):
    from loopbox.loop.cli import main as loop_main

    assert loop_main(["new", "--goal", GOAL]) == 0
    loop_id = next(
        l.split()[-1]
        for l in capsys.readouterr().out.splitlines()
        if l.startswith("created loop")
    )
    assert loop_main(["run", loop_id]) == engine.RC_BLOCKED
    capsys.readouterr()
    assert loop_main(["reject", loop_id, "--reason", "bad plan"]) == 0
    assert "rejected" in capsys.readouterr().out
    assert state.load_ledger(loop_id)["status"] == state.STATUS_FAILED
    # A failed loop refuses to run again.
    assert loop_main(["run", loop_id]) == engine.RC_FAILED


def test_decision_json_parsing():
    raw = engine._parse_decision('prefix text {"action": "done", "note": "x"} suffix')
    assert raw is not None and raw["action"] == "done"
    assert engine._parse_decision("no json at all") is None
    assert engine._parse_decision("[1, 2]") is None  # arrays are not decisions


def test_risky_pattern_matches_danger():
    assert engine._is_risky("rm -rf /", {})
    assert engine._is_risky("sudo ls", {})
    assert engine._is_risky("echo hi | sh", {})
    assert not engine._is_risky("echo safe", {})


def test_gate_files_roundtrip(tracked_home, no_harness):
    """A hand-edited gate.json is picked up on the next run."""
    ledger = _new_loop()
    loop_id = ledger["id"]
    assert engine.run_loop(loop_id, stream=io.StringIO()) == engine.RC_BLOCKED

    path = gates.gate_json_path(loop_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "approved"
    data["note"] = "hand-edited"
    path.write_text(json.dumps(data), encoding="utf-8")

    rc = engine.run_loop(loop_id, max_steps=1, stream=io.StringIO())
    assert rc == engine.RC_STOPPED  # adopted approval, ran one step, budget out
    reloaded = state.load_ledger(loop_id)
    assert any(
        g["status"] == gates.APPROVED and g["note"] == "hand-edited"
        for g in reloaded["gates"]
    )
    assert not any(g["status"] == gates.PENDING for g in reloaded["gates"])
