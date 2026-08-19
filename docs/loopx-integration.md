# LoopX integration: `loopbox.loop`

[LoopX](https://github.com/huangruiteng/loopx) is an open, provider-neutral
state kernel / local-first control plane for loop engineering: objectives,
gates, todos, evidence, quota, and handoffs stay durable while agent
harnesses (Codex, Claude Code, ...) execute bounded turns. `loopbox.loop`
is a minimal, stdlib-only re-implementation of those ideas on top of the
loopbox sandbox SDK — it shares the concepts, not the code, and adds hard
execution isolation: every command a loop proposes is self-checked *inside*
a loopbox sandbox.

## Concept mapping

| LoopX | loopbox.loop |
| --- | --- |
| Objective / state kernel | Ledger `~/.loopbox/loops/<loop_id>/loop.json` (goal, todos, decisions, evidence, run_history, quota) — `loop/state.py` |
| "What happens next" decisions | Self-think in `loop/engine.py`: delegated to a harness CLI when available (see below), else a structured rule-based fallback |
| Bounded turns executed by a harness | Self-check: the proposed command runs via `Sandbox.commands.run()` inside the loop's sandbox; an optional `verify` command must also exit 0 |
| Quota decides the next tick | `quota` in the ledger: `max_steps` / `max_seconds`, charged per executed step; exhaustion stops the loop |
| Gates ("human judgment needed? ask a concrete question and wait") | `approve_plan` / `approve_step` / `on_failure` gates in `loop/gates.py`; the engine blocks while a gate is `pending` |
| Handoff / restartability | The ledger is checkpointed atomically after every step; a killed loop resumes with `loopbox loop run <loop_id>` |
| Not an autonomous production controller | Risky commands (`rm -rf`, `sudo`, `git push`, ...) always require an `approve_step` gate unless `--auto-approve` is set |

## How a human participates

A pending gate blocks the loop. It is materialized as
`~/.loopbox/loops/<loop_id>/GATE.md` (readable question + instructions) and
`gate.json` (machine-editable). Three ways to answer:

- **Interactively**: when `run` has a TTY, the engine prints the gate and
  prompts `a / r [reason] / s <note>` on stdin.
- **Via CLI (async, recommended)**: resolve, then resume.

  ```bash
  loopbox loop approve <loop_id> [gate_id] [--note TEXT]
  loopbox loop reject  <loop_id> [gate_id] [--reason TEXT]
  loopbox loop steer   <loop_id> --note TEXT   # "run: <cmd>" enqueues a command
  loopbox loop run <loop_id>
  ```

- **By editing `gate.json`**: set `"status"` to `"approved"`, `"rejected"`
  or `"steered"` plus an optional `"note"`, then `run` again (the engine
  adopts the file edit on start).

Steering records a human decision in the ledger and injects a new todo;
a note of the form `run: <command>` becomes a command todo executed
verbatim in the sandbox. Rejecting a gate fails the loop.

## Quick start

```bash
loopbox loop new --goal "create hello.txt containing 'hi' and verify it" --sandbox seatbelt
loopbox loop run <loop_id>                 # blocks on the plan gate first
loopbox loop approve <loop_id>             # approve the plan
loopbox loop run <loop_id>                 # executes steps in the sandbox
loopbox loop status <loop_id> [--json]
loopbox loop history <loop_id>
```

`run` exit codes: `0` goal met, `1` failed, `2` stopped by budget or
interrupt, `3` blocked on a pending gate.

## Thinking harnesses

Self-thinking needs an LLM only for planning; execution is always
sandboxed. Detection order:

1. `LOOPBOX_HARNESS` — a full command line; `{prompt}` marks where the
   prompt goes, otherwise it is appended as the final argument
   (e.g. `LOOPBOX_HARNESS="codex exec"`). `LOOPBOX_HARNESS_TIMEOUT` sets the
   per-think timeout (default 600s).
2. `codex` on `PATH` → `codex exec <prompt>`.
3. `claude` on `PATH` → `claude -p <prompt>`.

With no harness available the engine falls back to a deterministic
rule-based thinker: it proposes a fixed plan (inspect workspace → do the
work → verify), runs safe exploration commands, and escalates every
judgment call to a human gate — so a loop stays steerable end-to-end even
with no LLM installed.

## Sandbox lifecycle

The loop's sandbox is created lazily on the first executed step
(`Sandbox.create(template=...)`) and its id is stored in the ledger, so
resume reconnects via `Sandbox.connect`. Workspaces persist across steps;
sandboxes are never killed automatically — inspect them with the regular
`loopbox` CLI/SDK after the loop ends.
