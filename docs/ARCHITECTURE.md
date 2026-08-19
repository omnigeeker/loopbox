# Loopbox architecture

Internals of loopbox: components, data model, backend contract, snapshot
semantics, service auth, and the loop engine state machine. For usage, see
the [README](../README.md) and the [tutorial](TUTORIAL.en.md).

## Components

```
                        ┌─────────────────────────────────────────┐
                        │              entry surfaces             │
                        │                                         │
                        │   CLI (loopbox.cli)   SDK (loopbox.sdk) │
                        │   `loopbox …`         Sandbox / SandboxError │
                        │                                         │
                        │   service (loopbox.service)             │
                        │   ThreadingHTTPServer :31885            │
                        └───────┬──────────────────┬──────────────┘
                                │                  │
             reads/writes        │                  │
        ┌────────────────────────▼─┐     ┌─────────▼───────────────┐
        │  store (loopbox.store)   │     │  auth (loopbox.auth)    │
        │  ~/.loopbox/sandboxes.json   │ X-API-Key token (0600)    │
        │  fcntl lock, atomic writes │     │  LOOPBOX_NO_AUTH=1 bypass  │
        └──────────────────────────┘     └─────────────────────────┘
                                │
                     backends (loopbox.backends.*)
                                │
                ┌───────────────┴────────────────┐
                ▼                                ▼
         seatbelt (default)               vz (experimental)
         sandbox-exec profile.sb          vzrunner Swift helper
         SIGSTOP/SIGCONT on pgids         Virtualization.framework
         APFS clonefile snapshots         machine-state snapshots
                │                                │
  ~/.loopbox/sandboxes/<id>/{workspace,profile.sb}
  ~/.loopbox/snapshots/<id>/<snap>/    ~/.loopbox/vms/<id>/ (bundle)

         harness (loopbox.harness) — codex / claude / dsh / custom adapters,
         launched inside a sandbox via backend.exec/spawn.

         loop engine (loopbox.loop.*) — durable JSON ledgers + human gates
         under ~/.loopbox/loops/<loop_id>/; execution via the SDK.
```

Layering rules that keep the three surfaces consistent:

- The CLI drives the registry and backends directly and intentionally never
  imports the SDK layer.
- The SDK talks to the registry and backends directly — no HTTP involved.
- The service is a thin E2B-shaped HTTP surface over the same registry and
  backends (`loopbox/server.py` is a legacy pre-service façade, superseded by
  `loopbox/service.py` and not importable).

## Module map

| Module | Responsibility |
|---|---|
| `loopbox/cli.py` | `loopbox` entry point: `new/ls/exec/spawn/pause/resume/snapshot/snapshots/restore/fork/rm/serve/doctor/harness/loop`; global `--json`; exit codes 1 (runtime), 2 (usage), 124 (timeout), 130 (Ctrl-C). |
| `loopbox/sdk.py` | `Sandbox` class mirroring the E2B SDK: `create/connect/list`, `commands.run`, `files.read/write/list`, `pause/resume`, `snapshot/list_snapshots`, `restore/fork`, `kill`, `set_timeout`, `info`. Workspace path containment; `SandboxError` for every failure. |
| `loopbox/store.py` | Registry and on-disk layout. `home()` = `~/.loopbox` (`LOOPBOX_HOME` override). Atomic JSON writes (tmp + `os.replace`); mutations hold an exclusive `fcntl.flock` on `.registry.lock` for the whole read-modify-write. |
| `loopbox/auth.py` | Service token: generated once (`lbx_` + 32 urlsafe bytes) into `auth.json` mode 0600, `X-API-Key` header, `Authorization: Bearer` alias, constant-time compare, `LOOPBOX_NO_AUTH=1` bypass. |
| `loopbox/service.py` | E2B-compatible REST API and timeout sweeper; see below. |
| `loopbox/backends/base.py` | `Backend` protocol and `ExecResult` dataclass. |
| `loopbox/backends/seatbelt.py` | Default backend: Seatbelt profile generation, exec/spawn via `sandbox-exec`, SIGSTOP/SIGCONT pause/resume, APFS clonefile snapshots/forks. |
| `loopbox/backends/vz.py` | Experimental backend: shells out to `vzrunner` for pause/resume/snapshot/restore/kill; fork is a Python-side APFS bundle clone. |
| `loopbox/harness.py` | `loopbox harness list/describe/doctor/run`: static `HarnessSpec`s (codex, claude, dsh) plus opaque custom binaries; argv after `--` passed verbatim; no permission flags ever injected. |
| `loopbox/loop/state.py` | Per-loop durable ledger: schema, todo/decision/evidence/run_history helpers, quota accounting, atomic checkpoints. |
| `loopbox/loop/gates.py` | Human gate state machine; `GATE.md`/`gate.json` projections. |
| `loopbox/loop/engine.py` | The self-think → self-check → self-iterate driver. |
| `loopbox/loop/cli.py` | `loopbox loop new/run/status/approve/reject/steer/history`. |
| `vzrunner/` | Single-file Swift helper (`swiftc`, no SwiftPM): boots ARM64 Linux guests, owns the `VZVirtualMachine` in a resident manager process, exposes newline-JSON ops over `run/manager.sock`. |

## Data model

### Sandbox record (`sandboxes.json`)

One JSON object per sandbox id. Core fields:

```jsonc
{
  "id": "sbx_9f2c41ab07d1",        // new_id("sbx") — uuid4 hex[:12]
  "backend": "seatbelt",           // "seatbelt" | "vz"
  "template": "seatbelt",          // as requested (SDK/service); info() falls back to backend
  "status": "running",             // creating | running | paused | killed
  "network": "outbound",           // outbound | all | deny
  "env": {"K": "V"},               // per-sandbox env merged over os.environ at exec time
  "metadata": {},                  // free-form user labels
  "extra_rw": [],                  // extra read/write paths (SDK.create only)
  "timeout": 600,                  // default per-command timeout (s)
  "timeout_deadline": 1724000000.0,// set by the service/sweeper; not by CLI/SDK
  "created_at": 1724000000.0,
  "parent_id": "sbx_…",            // on children created by fork
  "forked_from_snapshot": "v1",    // seatbelt fork detail
  "engine": { … }                  // per-backend mutable state, see below
}
```

`engine` is owned by the backend:

- seatbelt: `{"profile": "<sandbox dir>/profile.sb", "pgids": [1234, …]}`
  (pgids recorded by `spawn`/harness runs so pause/resume/kill can reach them)
- vz: `{"bundle": "~/.loopbox/vms/<id>", "guest_bundle": …}` (the guest
  kernel + rootfs bundle; see `vzrunner/README.md`)

The registry is the single source of truth for "which sandboxes exist";
backends derive all paths from `LOOPBOX_HOME` and the record id. `kill`
keeps files for forensics — only `loopbox rm --purge` deletes
`sandboxes/<id>/`, `snapshots/<id>/` and `vms/<id>/`.

### Snapshot record

- seatbelt: directory `snapshots/<sandbox_id>/<snapshot_id>/` containing an
  APFS clone of the workspace plus a marker
  `.loopbox-snapshot.json`: `{"snapshot_id", "created_at", "kind":
  "workspace-clone"}`. Named snapshots use the name verbatim as the
  directory; anonymous ones get `snap_<hex>`.
- vz: `<bundle>/snapshots/<name>/machine-state` (bytes from
  `VZVirtualMachine.saveMachineStateToURL`) + `disk.img` (APFS clone of the
  live disk).

### Loop ledger (`~/.loopbox/loops/<loop_id>/loop.json`)

The loop's "state kernel" (schema version 1):

```jsonc
{
  "schema": 1,
  "id": "loop_9f2c41ab07d1",
  "goal": "…",
  "template": "seatbelt",          // sandbox template for self-checks (None = SDK default)
  "status": "running",             // see state machine below
  "sandbox_id": "sbx_…",           // created lazily on the first executed step
  "created_at": …, "updated_at": …,
  "quota":  {"max_steps": 50, "max_seconds": 3600.0,
             "steps_used": 3, "seconds_used": 12.4},
  "todos":       [{"id": "t1", "title": "…", "status": "pending|in_progress|done",
                   "command": null, "note": "…", "created_at": …, "closed_at": …}],
  "decisions":   [{"id": "d1", "step": 0, "kind": "plan|run|done|steer|…",
                   "summary": "…", "rationale": "…",
                   "source": "engine|harness|human", "created_at": …}],
  "evidence":    [{"id": "e1", "step": 1, "kind": "output|verify|note",
                   "summary": "…", "command": "…", "exit_code": 0, "created_at": …}],
  "run_history": [{"step": 1, "action": "run", "command": "…", "ok": true,
                   "exit_code": 0, "duration_s": 0.4, "note": "…", "created_at": …}],
  "gates":       [ … see below ]
}
```

Every mutation ends in `save_ledger()` (atomic tmp+rename), so the on-disk
ledger is always a consistent resume point. Budgets are cumulative across all
`run` invocations — quota survives interrupts.

## Backend interface contract

`loopbox.backends.base.Backend` (a `typing.Protocol`):

```python
name: str
def create(record) -> None                    # prepare engine state
def exec(record, argv, *, cwd=None, env=None, timeout=None) -> ExecResult
def spawn(record, argv, *, env=None) -> int   # detached process → pgid
def pause(record) -> None                     # must be resumable
def resume(record) -> None
def snapshot(record, name=None) -> str        # → snapshot id
def list_snapshots(record) -> list[dict]
def restore(record, snapshot_id) -> None
def fork(record, snapshot_id=None) -> dict    # → new, registered child record
def kill(record) -> None
```

Contract details all surfaces rely on:

- Backends receive the mutable record and stash private state under
  `record["engine"]`; the caller persists it via the store.
- `ExecResult` = `{stdout, stderr, exit_code, duration_s, command}`, with
  `ok == (exit_code == 0)` and `to_dict()`.
- `fork` returns an *already registered, running* child record (both backends
  call `Store().add(child)` inside `fork`; the CLI tolerates that).
- Selection: `get_backend(None)` → seatbelt; `"seatbelt"`, `"vz"` by name;
  unknown names raise `ValueError` listing the known set.

### seatbelt execution path

`exec` wraps argv as `["sandbox-exec", "-f", profile.sb, *argv]` and runs it
with `start_new_session=True` (each call is its own process group). `cwd` is
confined to the workspace after symlink resolution of both sides
(`/tmp → /private/tmp` must not defeat containment). Env = `os.environ` +
record env + per-call env. `spawn` uses `Popen` detached and records the
pgid. `pause`/`resume` map to SIGSTOP/SIGCONT per recorded pgid; `kill`
SIGCONTs first (SIGKILL is not delivered to stopped groups) then SIGKILLs.

The generated profile starts from `(deny default)` and allows: process
fork/exec/signals same-sandbox, sysctl/process-info reads, broad
`file-read*` on `/`, write access only to the workspace + scratch
(`{,/private}/tmp`, `/private/var/folders`) + `extra_rw`, a few device
literals, one network mode, and unconditional deny read/write of the
credential stores (`~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, keychains,
cookies, Chrome profile) — Seatbelt deny rules win regardless of order.

### vz execution path

`vz.py` resolves `vzrunner` (`which vzrunner`, else
`<repo>/vzrunner/.build/release/vzrunner`) and shells out one JSON-style CLI
call per operation: `exec|pause|resume|snapshot|restore|kill --bundle <b>`.

Current exec model (explicit limitation): no in-guest agent, so `exec`
boots the VM with an `init=` shim that runs the command as PID 1 and powers
off; each exec is a fresh boot; stdout is streamed to the manager's console
but not captured into `ExecResult` (`stderr=""`, `exit_code=0`); `spawn` is
`NotImplementedError`. The swap point for a vsock agent is documented in
`vzrunner/README.md`.

## Snapshot semantics per backend

| | seatbelt | vz |
|---|---|---|
| What is captured | workspace filesystem only | whole machine: `machine-state` + `disk.img` clone |
| Mechanism | `cp -c` APFS clonefile (falls back to `copytree`) | `saveMachineStateToURL` + `cp -c` clone of disk |
| Processes/memory | not captured (pause keeps live processes frozen only) | captured in machine state |
| `restore` | workspace is replaced with the clone; marker file stripped | snapshot disk cloned over live disk, VM released → next exec boots restored disk |
| `fork` | clone workspace or a snapshot dir into a new sandbox; new profile generated; `parent_id` recorded | `cp -Rc` of the whole bundle into `~/.loopbox/vms/<child id>` |
| Guarantees | O(1)-ish in unchanged data; point-in-time for files | true time travel of a halted VM |

## HTTP service: auth, routes, sweeper

`loopbox.service` uses stdlib `ThreadingHTTPServer` (default
`127.0.0.1:31885`, `LOOPBOX_HOST`/`LOOPBOX_PORT` overrides).

- **Auth**: every route except `GET /health` requires the token from
  `~/.loopbox/auth.json` as `X-API-Key` (or `Authorization: Bearer`);
  compared with `hmac.compare_digest`. `LOOPBOX_NO_AUTH=1` bypasses.
- **Errors**: `ApiError` renders `{"code", "message"}`; `StoreError` and
  missing files → 404, `ValueError` → 400, `subprocess.TimeoutExpired` → 408,
  anything else → 500 with the same shape. Killed sandboxes → 409.
- **Record rendering**: `public_record` emits E2B keys (`sandboxID`,
  `templateID`, `startedAt`, `timeoutAt`, `parentSandboxID`). `envVars` are
  accepted at create time but never echoed back.
- **Timeout enforcement**: creation/timeout routes compute
  `timeout_deadline = now + timeout`. A daemon thread sweeps the registry
  every `SWEEP_INTERVAL_S = 1.0`s: for each expired live record it resumes
  (so SIGKILL reaches SIGSTOPped groups), kills, and marks the record
  `killed` with the deadline cleared. The record stays visible until an
  explicit `DELETE`; the sweeper is bulletproofed so it can never take the
  server down.

## Loop engine

### Self-think

- Harness detection: `LOOPBOX_HARNESS` (shlex-split; `{prompt}` substituted
  or prompt appended) → `codex exec` on PATH → `claude -p` on PATH → else
  deterministic rule-based fallback. Per-think timeout: `LOOPBOX_HARNESS_TIMEOUT`
  (default 600 s). **The harness runs on the host; it only decides.**
- The prompt carries goal, remaining budget, todos, recent steps/evidence and
  human steering notes, plus a strict reply schema (one JSON object):
  `plan` | `run` | `done` | `ask_human` (+ `command`, `verify`, `todo_done`,
  `todos_add`, `question`, `risky`, `note`). The first JSON object in the
  reply is parsed; unparseable replies fall back to rules.
- The rule-based fallback can only plan, run safe exploration (`ls -la`), or
  escalate — judgment is always delegated to a human gate.

### Self-check

A decided `run` executes `["/bin/sh", "-c", command]` inside the loop's
sandbox via the SDK (`Sandbox.connect` if recorded, else create — sandbox id
kept in the ledger). Step ok = exit 0 AND the optional `verify` command also
exits 0. stdout/stderr tails (500 chars) are recorded as evidence; the step
is charged to quota (`steps_used`, `seconds_used`). Per-step timeout caps at
`STEP_TIMEOUT_S = 300 s` (and never exceeds remaining budget).

### Self-iterate + loop state machine

```
                    loopbox loop new
                          │
                          ▼
                       ┌─────┐    loopbox loop run
                       │ new ├────────────────────┐
                       └─────┘                    ▼
                             ┌──────────────┐  budget exhausted /
        gate pending &  ◄────│   running    │  interrupt ──────────►┌─────────┐
        cannot resolve       │  (run_loop)  │                       │ stopped │── run again
        here ──►┌────────┐   └──┬───┬───┬──┘                       └─────────┘   (resumable)
                │blocked │      │   │   │
                │ _gate  │── run│   │   └── decide "done" / finish gate ─► done (0)
                └────────┘ again│   │
                                │   └─── step failed & human/(fallback) aborts,
                                │         or gate rejected ──────────────► failed (1)
                                └─────── think → check → iterate …
```

Statuses: `new`, `running`, `blocked_gate`, `stopped`, `done`, `failed`.
`blocked_gate` and `stopped` are resumable — `loopbox loop run` reloads the
ledger and continues from the checkpoint (todos stuck in `in_progress` are
reset to `pending` on resume). `done` and `failed` are terminal. `run` exit
codes: `0` done, `1` failed, `2` stopped (budget/interrupt), `3` blocked on
a gate.

### Gate lifecycle

Types: `approve_plan` (initial plan), `approve_step` (risky/ambiguous step;
commands matching `rm -rf`, `sudo`, `git push`, `mkfs`, `dd of=`, pipe-to-
shell, recursive chmod/chown, `> /dev/sd*` always gate unless
`--auto-approve`), `on_failure` (retry/steer/abort).

```
request_gate(type, question)                    resolve_gate(status, note)
  gate = {id, status: pending,                  via TTY prompt (a/r/s),
          question, context, todo_id,           CLI approve/reject/steer,
          on_approve, applied: false}           or editing gate.json
  ledger checkpoint + write
  GATE.md (human rendering) and
  gate.json (machine-editable)
        │
        ▼
   pending ──► approved ─► apply once (applied=true):
                            on_approve = continue | close_todo | finish
             ──► rejected ─► decision + loop status = failed
             ──► steered  ─► human decision + todo from note
                             ("run: <cmd>" → command todo executed
                             verbatim in the sandbox on resume)
```

`request_gate` is idempotent while a gate is pending (no stacked questions);
`apply_gate_outcome` folds a resolved gate into the ledger exactly once.
While a loop runs on a TTY, gates are answered inline; non-interactive runs
checkpoint and exit with code 3. With `--auto-approve` gates resolve
immediately (batch/CI mode).

## Relation to E2B

Loopbox mirrors E2B's *protocol and ergonomics* while executing locally: SDK
shape (`Sandbox.create`, `commands.run`, `files.*`, `pause`, `fork`, `kill`),
HTTP routes (`POST /sandboxes`, pause/resume/timeout with E2B-shaped payloads
and `{"code","message"}` errors), and `X-API-Key` auth. Loopbox extensions
(exec, files, snapshots, fork) cover what E2B does through envd and pause/
fork APIs. Hosted-only surface — template builds, teams, metrics — is out of
scope and not implemented.

## Relation to LoopX

[LoopX](https://github.com/huangruiteng/loopx) is a durable, provider-neutral
loop control plane (objectives, gates, todos, evidence, quota, handoffs).
`loopbox.loop` re-implements those ideas on the loopbox SDK: the concept
mapping (ledger ↔ state kernel, gates ↔ human judgment, quota ↔ budget,
checkpointed handoffs) is documented in
[docs/loopx-integration.md](loopx-integration.md). The distinguishing piece:
every command a loop proposes is executed *inside a loopbox sandbox*, so the
control plane's actions are isolation-checked by construction.
