"""LoopX-inspired durable loop engine for loopbox.

A *loop* is a long-horizon objective plus a durable state kernel (the JSON
ledger in :mod:`loopbox.loop.state`) that survives restarts: goal, todos,
semantic decisions, evidence, run history, quota, and human gates
(:mod:`loopbox.loop.gates`). The driver (:mod:`loopbox.loop.engine`)
self-thinks the next step (delegated to an agent-harness CLI when one is on
``PATH``), self-checks it inside a loopbox sandbox, and self-iterates under
budget and gate constraints.

Typical use::

    from loopbox.loop import create_loop, run_loop

    ledger = create_loop("make the tests pass", template="seatbelt")
    rc = run_loop(ledger["id"])          # checkpoints after every step
"""

from __future__ import annotations

from loopbox.loop import engine, gates, state
from loopbox.loop.cli import main as loop_main
from loopbox.loop.engine import RC_BLOCKED, RC_DONE, RC_FAILED, RC_STOPPED, run_loop
from loopbox.loop.gates import (
    APPROVE_PLAN,
    APPROVE_STEP,
    ON_FAILURE,
    GateError,
    pending_gate,
    resolve_gate,
)
from loopbox.loop.state import (
    LoopError,
    create_loop,
    list_loops,
    load_ledger,
    loop_dir,
    quota_remaining,
    save_ledger,
)

__all__ = [
    "APPROVE_PLAN",
    "APPROVE_STEP",
    "ON_FAILURE",
    "GateError",
    "LoopError",
    "RC_BLOCKED",
    "RC_DONE",
    "RC_FAILED",
    "RC_STOPPED",
    "create_loop",
    "engine",
    "gates",
    "list_loops",
    "load_ledger",
    "loop_dir",
    "loop_main",
    "pending_gate",
    "quota_remaining",
    "resolve_gate",
    "run_loop",
    "save_ledger",
    "state",
]
