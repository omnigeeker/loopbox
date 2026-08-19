"""Loop driver: self-think, self-check, self-iterate.

The engine runs a LoopX-style control loop over a durable ledger
(:mod:`loopbox.loop.state`):

- **self-think**: review the ledger and propose the next action. Thinking is
  delegated to an agent-harness CLI (``codex``, ``kimi`` or ``claude``,
  override via ``LOOPBOX_HARNESS``) when one is on ``PATH``; otherwise a
  structured
  rule-based fallback produces the plan and escalates judgment to human
  gates.
- **self-check**: execute the proposed command *inside a loopbox sandbox*
  via the SDK (``commands.run``), plus an optional verify command whose exit
  code must also be zero. Results are recorded as evidence.
- **self-iterate**: repeat, checkpointing the ledger after every step, until
  a stop condition fires: goal met, ``max_steps`` reached, ``max_seconds``
  exhausted, or a human gate blocking progress.

The harness only decides; execution happens inside the sandbox. A killed or
interrupted loop resumes from its last checkpoint via
``loopbox loop run <loop_id>``.

Exit codes returned by :func:`run_loop`:

- ``RC_DONE``    (0): goal met, loop finished.
- ``RC_FAILED``  (1): step failed and the human (or the fallback) aborted.
- ``RC_STOPPED`` (2): budget exhausted or the run was interrupted.
- ``RC_BLOCKED`` (3): a gate is pending and could not be resolved here.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, TextIO

from loopbox.loop import gates, state

RC_DONE = 0
RC_FAILED = 1
RC_STOPPED = 2
RC_BLOCKED = 3

STEP_TIMEOUT_S = 300.0
HARNESS_TIMEOUT_ENV = "LOOPBOX_HARNESS_TIMEOUT"
HARNESS_ENV = "LOOPBOX_HARNESS"
DEFAULT_HARNESS_TIMEOUT_S = 600.0

# Command fragments that make a step "risky": it must pass an approve_step
# gate even when a harness proposed it.
_RISKY_PATTERN = re.compile(
    r"\brm\s+-[a-z]*[rf]|\bsudo\b|\bgit\s+push\b|\bmkfs\b|\bdd\s+.*\bof=|"
    r"\|\s*(sudo\s+)?(ba|z|fi)?sh\b|\bchmod\s+-R\b|\bchown\s+-R\b|>\s*/dev/sd"
)

_DECISION_SCHEMA = """\
Reply with ONE JSON object and nothing else:
{
  "action": "run" | "done" | "ask_human",
  "command": "shell command to run in the sandbox (action=run)",
  "verify": "shell command that exits 0 only if the step succeeded (optional)",
  "todo_done": true,
  "todos_add": ["short follow-up tasks"],
  "question": "concrete question for the human (action=ask_human)",
  "risky": false,
  "note": "one-line rationale"
}
Use "done" only when the goal is verifiably met. Use "ask_human" when human
judgment is required; ask one concrete question."""

_PLAN_SCHEMA = """\
Reply with ONE JSON object and nothing else:
{
  "action": "plan",
  "todos_add": ["3-6 short, ordered, verifiable tasks"],
  "note": "one-line rationale"
}"""


# -- sandbox -----------------------------------------------------------------


def _ensure_sandbox(ledger: dict[str, Any]):
    """Return a live sandbox for the loop, creating or reconnecting as needed.

    Imported lazily: the SDK lives in a sibling module and the loop engine
    must stay importable (and checkpointable) without it.
    """
    from loopbox.sdk import Sandbox, SandboxError

    sandbox_id = ledger.get("sandbox_id")
    if sandbox_id:
        try:
            return Sandbox.connect(sandbox_id)
        except SandboxError:
            ledger["sandbox_id"] = None
    sbx = Sandbox.create(
        template=ledger.get("template"),
        metadata={"loop": ledger["id"], "goal": ledger["goal"][:120]},
    )
    ledger["sandbox_id"] = sbx.id
    state.add_evidence(
        ledger, "note", f"created sandbox {sbx.id} (template {ledger.get('template')!r})"
    )
    state.save_ledger(ledger)
    return sbx


# -- self-think ----------------------------------------------------------------


def _find_harness() -> list[str] | None:
    """Return the argv prefix of a thinking harness, or None for the fallback.

    ``LOOPBOX_HARNESS`` overrides detection: it is a command line
    (``shlex``-split); if it contains ``{prompt}`` the prompt is substituted
    into that argument, otherwise the prompt is appended as the last
    argument.
    """
    override = os.environ.get(HARNESS_ENV, "").strip()
    if override:
        return shlex.split(override)
    if shutil.which("codex"):
        return ["codex", "exec"]
    if shutil.which("kimi"):
        return ["kimi", "-p"]
    if shutil.which("claude"):
        return ["claude", "-p"]
    return None


def _harness_timeout() -> float:
    try:
        return float(os.environ.get(HARNESS_TIMEOUT_ENV, DEFAULT_HARNESS_TIMEOUT_S))
    except ValueError:
        return DEFAULT_HARNESS_TIMEOUT_S


def _summarize_ledger(ledger: dict[str, Any], todo: dict[str, Any] | None) -> str:
    """Build a compact ledger summary for the thinking prompt."""
    parts = [f"GOAL: {ledger['goal']}"]
    remaining = state.quota_remaining(ledger)
    parts.append(f"BUDGET LEFT: {remaining['steps']} steps, {remaining['seconds']:.0f} seconds")
    if ledger["todos"]:
        parts.append("TODOS:")
        for t in ledger["todos"][-10:]:
            parts.append(f"  - [{t['status']}] {t['id']}: {t['title']}")
    if todo is not None:
        parts.append(f"CURRENT TODO: {todo['id']}: {todo['title']}")
    recent = ledger["run_history"][-5:]
    if recent:
        parts.append("RECENT STEPS:")
        for r in recent:
            command = r.get("command") or "-"
            mark = "ok" if r["ok"] else f"FAILED (exit {r['exit_code']})"
            parts.append(
                f"  - step {r['step']}: {r['action']} {command!r} -> "
                f"{mark}; {r.get('note', '')}"
            )
    evidence = ledger["evidence"][-3:]
    if evidence:
        parts.append("RECENT EVIDENCE:")
        for e in evidence:
            parts.append(f"  - {e['summary'][:300]}")
    human_notes = [d for d in ledger["decisions"] if d.get("source") == state.SOURCE_HUMAN][-3:]
    if human_notes:
        parts.append("HUMAN STEERING (follow these):")
        for d in human_notes:
            parts.append(f"  - {d['summary']}: {d.get('rationale', '')}")
    return "\n".join(parts)


def _build_think_prompt(ledger: dict[str, Any], todo: dict[str, Any] | None) -> str:
    header = (
        "You are the thinking step of a bounded engineering loop running on "
        "macOS. Commands execute inside a fresh POSIX shell sandbox whose cwd "
        "is an empty workspace shared across steps."
    )
    if todo is None:
        ask = " Break the goal into a short todo plan."
        schema = _PLAN_SCHEMA
    else:
        ask = " Propose exactly the next bounded action for the current todo."
        schema = _DECISION_SCHEMA
    return header + ask + "\n\n" + _summarize_ledger(ledger, todo) + "\n\n" + schema


def _normalize_decision(raw: dict[str, Any], source: str) -> dict[str, Any]:
    """Coerce a parsed harness reply into the internal decision shape."""
    action = str(raw.get("action") or "").strip().lower()
    if action not in ("run", "done", "ask_human", "plan"):
        action = "ask_human"
    todos_add = raw.get("todos_add")
    return {
        "action": action,
        "command": raw.get("command") if isinstance(raw.get("command"), str) else None,
        "verify": raw.get("verify") if isinstance(raw.get("verify"), str) else None,
        "todo_done": bool(raw.get("todo_done", True)),
        "todos_add": [str(t) for t in todos_add if str(t).strip()]
        if isinstance(todos_add, list)
        else [],
        "question": str(raw.get("question") or "").strip(),
        "risky": bool(raw.get("risky")),
        "note": str(raw.get("note") or "").strip(),
        "source": source,
    }


def _parse_decision(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object found in ``text``; None if none parses."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _harness_decision(
    harness: list[str], ledger: dict[str, Any], todo: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Ask the harness CLI for the next action; None when it fails to answer."""
    prompt = _build_think_prompt(ledger, todo)
    argv = [arg.replace("{prompt}", prompt) if "{prompt}" in arg else arg for arg in harness]
    if not any("{prompt}" in arg for arg in harness):
        argv = [*argv, prompt]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_harness_timeout(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = _parse_decision(proc.stdout or "")
    if raw is None:
        return None
    return _normalize_decision(raw, state.SOURCE_HARNESS)


def _rule_plan(ledger: dict[str, Any]) -> list[str]:
    """Rule-based initial plan for the no-harness case."""
    goal = ledger["goal"]
    return [
        "Inspect the sandbox workspace",
        f"Do the work: {goal[:200]}",
        "Verify the outcome against the goal and summarize",
    ]


def _rule_decision(ledger: dict[str, Any], todo: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fallback decision when no harness is available.

    The fallback can only do bookkeeping and safe exploration; anything
    requiring judgment is escalated to a human gate.
    """
    if todo.get("command"):
        return _normalize_decision(
            {"action": "run", "command": todo["command"], "note": "human-provided command"},
            state.SOURCE_ENGINE,
        )
    if not ledger["evidence"]:
        return _normalize_decision(
            {"action": "run", "command": "ls -la", "note": "initial workspace exploration"},
            state.SOURCE_ENGINE,
        )
    return _normalize_decision(
        {
            "action": "ask_human",
            "question": (
                "No LLM harness found (set LOOPBOX_HARNESS or install codex/kimi/claude). "
                f"How should the loop proceed on todo {todo['id']} ({todo['title']!r})? "
                "Steer with a note like 'run: <command>' to enqueue work, or approve "
                "to close this todo."
            ),
            "note": "rule-based fallback needs human direction",
        },
        state.SOURCE_ENGINE,
    )


def _think(
    ledger: dict[str, Any],
    todo: dict[str, Any] | None,
    *,
    harness: list[str] | None,
    stream: TextIO,
) -> dict[str, Any]:
    """Produce the next decision: harness-backed, or rule-based fallback."""
    if harness is not None:
        decision = _harness_decision(harness, ledger, todo)
        if decision is not None:
            print(f"[{ledger['id']}] think: harness decision: {decision['action']}", file=stream)
            return decision
        print(f"[{ledger['id']}] think: harness gave no usable answer; using fallback", file=stream)
    if todo is None:
        todos = _rule_plan(ledger)
        return _normalize_decision(
            {"action": "plan", "todos_add": todos, "note": "rule-based initial plan"},
            state.SOURCE_ENGINE,
        )
    decision = _rule_decision(ledger, todo)
    print(f"[{ledger['id']}] think: rule decision: {decision['action']}", file=stream)
    return decision


# -- gates -------------------------------------------------------------------


def _resolve_pending_gate(
    ledger: dict[str, Any], *, auto_approve: bool, stream: TextIO
) -> dict[str, Any] | None:
    """Try to resolve the pending gate. Returns the resolved gate, or None if
    the loop must checkpoint and exit as blocked."""
    gate = gates.pending_gate(ledger)
    if gate is None:
        return None
    resolved = gates.sync_gate_from_file(ledger)
    if resolved is None:
        if auto_approve:
            resolved = gates.resolve_gate(ledger, gate["id"], status=gates.APPROVED)
            print(f"[{ledger['id']}] gate {gate['id']} auto-approved", file=stream)
        elif sys.stdin.isatty():
            status, note = gates.prompt_gate(gate, ledger["id"], stdout=stream)
            resolved = gates.resolve_gate(ledger, gate["id"], status=status, note=note)
        else:
            return None
    return resolved


# -- self-check ----------------------------------------------------------------


def _is_risky(command: str, decision: dict[str, Any]) -> bool:
    return bool(decision.get("risky")) or bool(_RISKY_PATTERN.search(command or ""))


def _run_checked(
    ledger: dict[str, Any], decision: dict[str, Any], *, stream: TextIO
) -> tuple[bool, str]:
    """Execute the decision's command in the sandbox and verify the result.

    Self-check means: the step's own exit code must be zero AND, when the
    decision carries a ``verify`` command, that command must also exit zero
    inside the same sandbox. Returns ``(ok, detail)``.
    """
    sbx = _ensure_sandbox(ledger)
    command = decision["command"]
    remaining = state.quota_remaining(ledger)
    timeout = max(1.0, min(STEP_TIMEOUT_S, float(remaining["seconds"] or STEP_TIMEOUT_S)))
    started = time.monotonic()
    result = sbx.commands.run(["/bin/sh", "-c", command], timeout=timeout)
    duration = time.monotonic() - started
    note = decision.get("note") or ""
    detail_parts = [f"exit {result.exit_code}"]
    ok = result.ok
    if result.stdout.strip():
        state.add_evidence(
            ledger,
            "output",
            f"stdout of {command!r}: {result.stdout.strip()[-500:]}",
            command=command,
            exit_code=result.exit_code,
        )
    if result.stderr.strip():
        state.add_evidence(
            ledger,
            "output",
            f"stderr of {command!r}: {result.stderr.strip()[-500:]}",
            command=command,
            exit_code=result.exit_code,
        )
    if ok and decision.get("verify"):
        verify = decision["verify"]
        vres = sbx.commands.run(["/bin/sh", "-c", verify], timeout=timeout)
        duration += vres.duration_s
        state.add_evidence(
            ledger,
            "verify",
            f"verify {verify!r} -> exit {vres.exit_code}",
            command=verify,
            exit_code=vres.exit_code,
        )
        detail_parts.append(f"verify exit {vres.exit_code}")
        ok = ok and vres.ok
    state.record_step(
        ledger,
        action="run",
        command=command,
        ok=ok,
        exit_code=result.exit_code,
        duration_s=duration,
        note=note,
    )
    state.save_ledger(ledger)
    return ok, "; ".join(detail_parts)


# -- self-iterate --------------------------------------------------------------


def _stop_reason(ledger: dict[str, Any], started_at: float) -> str | None:
    """Return why the loop must stop now, or None if it may continue."""
    remaining = state.quota_remaining(ledger)
    if remaining["steps"] <= 0:
        return "step budget exhausted (max_steps)"
    if remaining["seconds"] <= 0:
        return "time budget exhausted (max_seconds)"
    if time.monotonic() - started_at >= remaining["seconds"]:
        return "time budget exhausted for this run (max_seconds)"
    return None


def _finish(ledger: dict[str, Any], status: str, why: str, stream: TextIO) -> int:
    ledger["status"] = status
    state.add_decision(ledger, "loop_end", why, source=state.SOURCE_ENGINE)
    state.save_ledger(ledger)
    print(f"[{ledger['id']}] {status}: {why}", file=stream)
    if status == state.STATUS_DONE:
        return RC_DONE
    if status == state.STATUS_FAILED:
        return RC_FAILED
    return RC_STOPPED


def run_loop(
    loop_id: str,
    *,
    auto_approve: bool = False,
    max_steps: int | None = None,
    max_seconds: float | None = None,
    stream: TextIO | None = None,
) -> int:
    """Drive ``loop_id`` forward until a stop condition; returns an RC_* code.

    The ledger is checkpointed after every step, so interrupting or killing
    this call never loses work: run it again to resume. ``max_steps`` /
    ``max_seconds`` permanently update the loop's quota when given.
    ``auto_approve`` resolves gates without a human (CI/batch mode).
    """
    stream = stream or sys.stdout
    ledger = state.load_ledger(loop_id)
    if max_steps is not None:
        ledger["quota"]["max_steps"] = int(max_steps)
    if max_seconds is not None:
        ledger["quota"]["max_seconds"] = float(max_seconds)
    if ledger["status"] == state.STATUS_DONE:
        print(f"[{loop_id}] loop is already done", file=stream)
        return RC_DONE
    if ledger["status"] == state.STATUS_FAILED:
        print(f"[{loop_id}] loop has failed; edit the ledger or create a new loop", file=stream)
        return RC_FAILED
    ledger["status"] = state.STATUS_RUNNING
    state.save_ledger(ledger)
    harness = _find_harness()
    started_at = time.monotonic()
    try:
        while True:
            # 1. Budget stop conditions.
            why = _stop_reason(ledger, started_at)
            if why:
                return _finish(ledger, state.STATUS_STOPPED, why, stream)

            # 2. Gates: first apply outcomes of gates resolved externally
            #    (via the CLI or a gate.json edit while we were not running).
            for resolved_gate in ledger["gates"]:
                outcome = gates.apply_gate_outcome(ledger, resolved_gate)
                if outcome == "rejected":
                    print(f"[{loop_id}] failed: gate {resolved_gate['id']} rejected", file=stream)
                    return RC_FAILED
                if outcome == "finished":
                    print(
                        f"[{loop_id}] done: goal confirmed by gate {resolved_gate['id']}",
                        file=stream,
                    )
                    return RC_DONE

            # 2b. A pending gate blocks everything else.
            gate = gates.pending_gate(ledger)
            if gate is not None:
                resolved = _resolve_pending_gate(ledger, auto_approve=auto_approve, stream=stream)
                if resolved is None:
                    ledger["status"] = state.STATUS_BLOCKED
                    state.save_ledger(ledger)
                    print(
                        f"[{loop_id}] blocked on gate {gate['id']} ({gate['type']}): "
                        f"{gate['question']}",
                        file=stream,
                    )
                    print(
                        f"[{loop_id}] resolve it (see {state.loop_dir(loop_id)}) then run again",
                        file=stream,
                    )
                    return RC_BLOCKED
                continue

            # 3. Recover todos interrupted mid-step by a previous kill.
            for todo in ledger["todos"]:
                if todo.get("status") == state.TODO_IN_PROGRESS:
                    todo["status"] = state.TODO_PENDING
                    todo["note"] = "resumed after interruption"
            state.save_ledger(ledger)

            # 4. First pass: derive a plan and gate it.
            if not ledger["todos"] and not ledger["decisions"]:
                plan = _think(ledger, None, harness=harness, stream=stream)
                titles = plan["todos_add"] or _rule_plan(ledger)
                for title in titles:
                    state.add_todo(ledger, title)
                state.add_decision(
                    ledger,
                    "plan",
                    plan.get("note") or "initial plan",
                    rationale=", ".join(plan["todos_add"]),
                    source=plan["source"],
                )
                context = "\n".join(
                    f"  {i + 1}. {t['title']}" for i, t in enumerate(ledger["todos"])
                )
                gates.request_gate(
                    ledger,
                    gates.APPROVE_PLAN,
                    "Approve this plan to start work on the goal?",
                    context=f"Goal: {ledger['goal']}\nPlan:\n{context}",
                )
                continue

            # 5. Pick the next todo; when none remains, confirm completion.
            todo = state.next_pending_todo(ledger)
            if todo is None:
                if ledger["run_history"]:
                    gates.request_gate(
                        ledger,
                        gates.APPROVE_STEP,
                        "All todos are closed. Is the goal met?",
                        context=f"Goal: {ledger['goal']}",
                        on_approve="finish",
                    )
                    continue
                # No work ever happened and no harness to change that.
                return _finish(
                    ledger, state.STATUS_FAILED, "no todos and no executable steps", stream
                )

            # 6. Think -> decide the next bounded action for this todo.
            todo["status"] = state.TODO_IN_PROGRESS
            state.save_ledger(ledger)
            decision = _think(ledger, todo, harness=harness, stream=stream)
            state.add_decision(
                ledger,
                decision["action"] if decision["action"] != "plan" else "step",
                decision.get("note") or decision.get("command") or decision.get("question") or "-",
                rationale=f"todo {todo['id']}",
                source=decision["source"],
            )
            for title in decision["todos_add"]:
                state.add_todo(ledger, title, note=f"proposed at step {len(ledger['run_history']) + 1}")

            if decision["action"] == "plan":
                # The harness returned a re-plan mid-loop: keep the current
                # todo pending and let the new todos run.
                todo["status"] = state.TODO_PENDING
                state.save_ledger(ledger)
                continue

            if decision["action"] == "done":
                state.close_todo(todo, note=decision.get("note") or "done per decision")
                state.save_ledger(ledger)
                return _finish(
                    ledger, state.STATUS_DONE, decision.get("note") or "goal met", stream
                )

            if decision["action"] == "ask_human":
                todo["status"] = state.TODO_PENDING
                gates.request_gate(
                    ledger,
                    gates.APPROVE_STEP,
                    decision.get("question") or "How should the loop proceed?",
                    context=f"Goal: {ledger['goal']}\nTodo: {todo['id']}: {todo['title']}",
                    todo_id=todo["id"],
                    on_approve="close_todo",
                )
                continue

            if decision["action"] == "run" and not decision.get("command"):
                todo["status"] = state.TODO_PENDING
                state.save_ledger(ledger)
                gates.request_gate(
                    ledger,
                    gates.APPROVE_STEP,
                    "The think step proposed no command. Steer with a 'run: <command>' note.",
                    todo_id=todo["id"],
                    on_approve="close_todo",
                )
                continue

            command = decision["command"]
            if _is_risky(command, decision) and not auto_approve:
                todo["status"] = state.TODO_PENDING
                gates.request_gate(
                    ledger,
                    gates.APPROVE_STEP,
                    f"Approve running this risky command in the sandbox?\n\n    {command}",
                    context=f"Goal: {ledger['goal']}",
                )
                continue

            # 7. Execute + verify inside the sandbox (self-check).
            ok, detail = _run_checked(ledger, decision, stream=stream)
            print(
                f"[{loop_id}] step {len(ledger['run_history'])}: {command!r} -> "
                f"{'ok' if ok else 'FAILED'} ({detail})",
                file=stream,
            )
            if ok:
                if decision.get("todo_done", True):
                    state.close_todo(todo, note=decision.get("note") or "")
                else:
                    todo["status"] = state.TODO_PENDING
                state.save_ledger(ledger)
                continue

            # 8. Failure path: human gate decides retry / steer / abort.
            todo["status"] = state.TODO_PENDING
            todo["note"] = f"last step failed ({detail})"
            last = ledger["run_history"][-1]
            gates.request_gate(
                ledger,
                gates.ON_FAILURE,
                (
                    f"Step {last['step']} failed ({detail}) for todo {todo['id']} "
                    f"({todo['title']!r}). Approve to retry, steer with a note "
                    "(e.g. 'run: <command>'), or reject to abort the loop."
                ),
                context=f"Command: {command}\nGoal: {ledger['goal']}",
            )
    except KeyboardInterrupt:
        ledger["status"] = state.STATUS_BLOCKED
        state.save_ledger(ledger)
        print(f"\n[{loop_id}] interrupted; checkpoint saved -- run again to resume", file=stream)
        return RC_STOPPED
