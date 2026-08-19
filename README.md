# Loopbox

Local-first, E2B-protocol-compatible sandboxes for macOS on Apple Silicon (M1–M5).

[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md) | [Español](README.es.md) | [Português](README.pt.md) | [Deutsch](README.de.md) | [Français](README.fr.md)

Loopbox lets AI agent harnesses (Codex CLI, Claude Code, DSH / DeepSeek Harness,
or your own runners) execute untrusted work inside a real sandbox on your own
Mac — no cloud, no Linux VM tax for everyday tasks — while keeping the E2B
*usage protocol*: SDK shape, HTTP API shape, and snapshot semantics. The
default backend uses the macOS Seatbelt process sandbox; an experimental
Virtualization.framework backend adds whole-VM pause / snapshot / fork /
resume. Runtime is Python standard library only.

## Features

- **Two isolation backends**
  - `seatbelt` (default): macOS process sandbox via `sandbox-exec`. Instant
    startup, write-scoped filesystem isolation, network policy per sandbox
    (`outbound` / `all` / `deny`), `SIGSTOP`/`SIGCONT`-based pause/resume,
    APFS copy-on-write clonefile snapshots.
  - `vz` (experimental): Virtualization.framework ARM64 Linux VMs via the
    bundled Swift helper `vzrunner`. Machine-state snapshots via
    `saveMachineStateToURL` / `restoreMachineStateFromURL`, fork via APFS
    bundle clone.
- **E2B-compatible surface**
  - Python SDK shaped like the E2B SDK: `Sandbox.create()`,
    `sandbox.commands.run()`, `sandbox.files.read/write/list()`,
    `sandbox.pause()`, `sandbox.fork()`, `sandbox.kill()`.
  - REST API with E2B-shaped routes (`POST /sandboxes`, pause/resume/timeout)
    plus local extensions (exec, files, snapshots, fork), secured with
    `X-API-Key` token auth.
- **Credential carve-outs** — `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`,
  keychains and browser cookie stores are never readable inside a seatbelt
  sandbox; deny rules win over allow rules regardless of order.
- **Harness integration** — `loopbox harness run <sandbox> codex|claude|dsh -- ...`
  runs the whole agent CLI inside a sandbox so the loopbox boundary applies
  no matter how the harness configures itself. Loopbox never silently
  injects permission-bypass flags.
- **Loop engine** — `loopbox loop` runs a durable self-check → self-think →
  self-iterate cycle with human-in-the-loop gates, checkpointed to JSON after
  every step and resumable after any kill.
- **Zero runtime dependencies** — Python ≥ 3.10, standard library only.

## How it works

```
┌────────────┐   ┌────────────┐   ┌────────────────────────┐
│ CLI        │   │ Python SDK │   │ HTTP service           │
│ loopbox …  │   │ from …     │   │ :31885, E2B-shaped     │
│            │   │ import …   │   │ (loopbox.service)      │
└─────┬──────┘   └─────┬──────┘   └───────────┬────────────┘
      │                │                      │
      └────────────────┴──────────┬───────────┘
                                  │ backend interface
                     ┌────────────┴─────────────┐
                     ▼                          ▼
             ┌──────────────┐          ┌────────────────────┐
             │ seatbelt     │          │ vz                 │
             │ sandbox-exec │          │ vzrunner (Swift) → │
             │ profile      │          │ Virtualization.fw  │
             └──────┬───────┘          └─────────┬──────────┘
                    └──────────────┬─────────────┘
                                   ▼
              ~/.loopbox/  (LOOPBOX_HOME override)
                sandboxes.json        fcntl-locked atomic registry
                sandboxes/<id>/       workspace/ + profile.sb
                snapshots/<id>/<sid>/ APFS clonefile snapshots
                loops/<loop_id>/      loop ledgers, GATE.md, gate.json
                vms/<id>/             vz VM bundles
                auth.json             service token (mode 0600)
```

The CLI drives the registry and backends directly (it never goes through the
HTTP layer); the SDK does the same. The service is a thin E2B-shaped surface
over the same registry/backends, so all three views of a sandbox agree.

## Requirements

- macOS 13+ on Apple Silicon (arm64); the `vz` backend needs macOS 14+.
- Python ≥ 3.10 (`python3 --version`). No third-party runtime dependencies.
- APFS (standard on Apple Silicon) for copy-on-write snapshots.
- JSON schema of the state files is internal; `~/.loopbox` may be moved with
  `LOOPBOX_HOME`.
- Only to build the `vz` helper: Xcode Command Line Tools
  (`xcode-select --install`).

Run `loopbox doctor` to verify all of the above (architecture, macOS version,
`sandbox-exec`, APFS clone support, vzrunner presence, seatbelt smoke test).

## Quickstart

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
uv tool install .          # or: pipx install .  /  python3 -m pip install .
loopbox doctor             # self-check: arch, sandbox-exec, APFS clone, smoke
```

Create and use a sandbox (CLI):

```bash
SID=$(loopbox new)                           # seatbelt sandbox, network=outbound
loopbox exec $SID -- echo "hello sandbox"    # exit code mirrored to caller
loopbox exec $SID --cwd sub --timeout 30 -- make test
loopbox ls
loopbox pause $SID && loopbox resume $SID    # SIGSTOP / SIGCONT freeze
loopbox snapshot $SID --name v1              # APFS copy-on-write snapshot
loopbox snapshots $SID
loopbox fork $SID --snapshot v1              # branch off an identical twin
loopbox restore $SID v1                      # roll workspace back
loopbox rm $SID --purge                      # kill + delete files for real
```

Or the SDK:

```python
from loopbox import Sandbox

sbx = Sandbox.create(template="seatbelt", network="deny",
                     timeout=60, metadata={"job": "test"})
result = sbx.commands.run("echo hello > note.txt && cat note.txt")
assert result.ok
sbx.files.write("more.txt", "hi")
snap = sbx.snapshot(name="v1")
twin = sbx.fork(snapshot_id=snap)     # a new Sandbox, running
sbx.pause(); sbx.resume()
sbx.kill()                            # files kept; CLI rm --purge deletes
```

Run the HTTP service and drive it with curl:

```bash
loopbox serve                              # 127.0.0.1:31885, X-API-Key auth
TOKEN=$(python3 -c "import json,os.path; \
  print(json.load(open(os.path.expanduser('~/.loopbox/auth.json')))['token'])")
curl -s -X POST http://127.0.0.1:31885/sandboxes \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"templateID": "seatbelt", "timeout": 600}'
curl -s -X POST http://127.0.0.1:31885/sandboxes/<sandboxID>/exec \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"command": "uname -m"}'             # {"stdout": "arm64\n", ...}
```

The token is generated on first run into `~/.loopbox/auth.json` (mode 0600);
`Authorization: Bearer <token>` is accepted as an alias. `GET /health` is
unauthenticated. `LOOPBOX_NO_AUTH=1` disables auth — use only for local
development. A background sweeper enforces sandbox timeouts.

## E2B compatibility

Loopbox speaks a subset of the E2B control API plus local extensions. E2B
template IDs map to loopbox backend names (`seatbelt`, `vz`).

| Endpoint | Status |
|---|---|
| `POST /sandboxes` | Supported (`{"templateID", "timeout", "metadata", "envVars"}` → 201) |
| `GET /sandboxes` | Supported |
| `GET /sandboxes/{id}` | Supported (E2B-shaped record) |
| `DELETE /sandboxes/{id}` | Supported (kill + deregister, 204) |
| `POST /sandboxes/{id}/timeout` | Supported (sets `timeout_deadline`; sweeper enforces) |
| `POST /sandboxes/{id}/pause` | Supported (204) |
| `POST /sandboxes/{id}/resume` | Supported (204) |
| `POST /sandboxes/{id}/exec` | Loopbox extension — replaces envd `POST /process`; string commands run via `/bin/zsh -lc` |
| `GET /sandboxes/{id}/files?path=…` | Loopbox extension — list workspace entries |
| `PUT /sandboxes/{id}/files` | Loopbox extension — write `{"path", "content"}` |
| `POST /sandboxes/{id}/snapshots` | Loopbox extension (201 `{"snapshotID"}`) |
| `GET /sandboxes/{id}/snapshots` | Loopbox extension |
| `POST /sandboxes/{id}/fork` | Loopbox extension (`{"snapshotID"?}` → 201 `{"sandboxID"}`) |
| `GET /health` | Loopbox extension, unauthenticated |
| envd process streaming / websockets | Not implemented |
| E2B template build/manage APIs | Not implemented — templates are local backend names |
| Hosted-only surface (teams, metrics, auth0) | Out of scope — loopbox is local-only |

Errors are E2B-shaped: `{"code": <int>, "message": <str>}`.

## Harness integration

A harness picks its runtime by *where it executes*: starting `codex` or
`claude` on the host leaves protection to the harness's own sandbox. Loopbox
launches the whole harness CLI inside a sandbox, so loopbox's Seatbelt
profile (or VM) is the outer boundary regardless of harness configuration:

```bash
loopbox harness list                          # detection status of known CLIs
loopbox harness describe claude               # notes + launch examples
loopbox harness doctor                        # what's installed + guidance
loopbox harness run $SID codex -- exec "fix the failing tests"
loopbox harness run $SID claude -- -p "summarise this repo"
loopbox harness run $SID dsh -- cli
loopbox harness run $SID <any-binary> -- ...  # custom harness by PATH name
```

Everything after `--` is passed to the harness verbatim. Loopbox deliberately
never injects harness-native permission flags: `--dangerously-skip-permissions`
or `--sandbox danger-full-access` are the *caller's* choice, meaningful only
inside the sandbox, never something loopbox adds for you
(see [SECURITY.md](SECURITY.md)).

## Loop engineering with human gates

`loopbox loop` is a durable loop engine (concepts from
[LoopX](https://github.com/huangruiteng/loopx); execution isolated by
loopbox sandboxes). The ledger under `~/.loopbox/loops/<loop_id>/` is
checkpointed after every step — a killed loop just resumes with `run` again.

```bash
loopbox loop new --goal "create hello.txt containing 'hi' and verify it" --sandbox seatbelt
loopbox loop run <loop_id>                    # exits 3: blocked on the plan gate
loopbox loop approve <loop_id>                # answer the gate, then:
loopbox loop run <loop_id>                    # executes steps inside the sandbox
loopbox loop status <loop_id> [--json]
loopbox loop history <loop_id>
loopbox loop steer <loop_id> --note "run: make test"   # enqueue a command
loopbox loop reject <loop_id> --reason "wrong approach" # marks loop failed
```

- **Self-think**: an LLM harness CLI (`codex exec` or `claude -p` when on
  PATH; override with `LOOPBOX_HARNESS="cmd {prompt}"`,
  `LOOPBOX_HARNESS_TIMEOUT`) proposes the next action. The think step runs on
  the host; *execution* happens inside the sandbox. Without a harness, a
  deterministic rule-based fallback plans and escalates judgment to gates.
- **Self-check**: the proposed command runs via the SDK inside the loop's
  sandbox; an optional `verify` command must also exit 0.
- **Human gates**: `approve_plan`, `approve_step` (risky commands such as
  `rm -rf` or `git push` always gate unless `--auto-approve`), `on_failure`.
  Answer via the TTY prompt, the CLI from another terminal, or by editing
  `gate.json` / `GATE.md` in the loop dir.
- `run` exit codes: `0` goal met, `1` failed, `2` stopped by budget/interrupt,
  `3` blocked on a pending gate.

## Snapshots, fork, and resume per backend

| Operation | `seatbelt` | `vz` (experimental) |
|---|---|---|
| `pause` / `resume` | `SIGSTOP`/`SIGCONT` on recorded process groups — instant, keeps memory live | `VZVirtualMachine.pause()` / `.resume()` — whole VM frozen |
| `snapshot` | APFS copy-on-write clone (`cp -c`) of the workspace → `snapshots/<id>/<name>/`; O(1) in unchanged data; **filesystem only, no process/memory state** | `saveMachineStateToURL` → `snapshots/<name>/machine-state` plus an APFS clone of `disk.img`; **whole machine state** |
| `fork` | Clone workspace (or a snapshot) into a new sandbox with its own profile; child registered as running | Python-side `cp -Rc` clone of the whole VM bundle (disk + saved states) |
| `restore` | Workspace replaced with the snapshot clone | Snapshot disk cloned back over the live disk; the next `exec` boots from the restored state |

The `vz` backend captures true machine state, which Seatbelt cannot. Its one
real gap today is guest-side control: `exec` runs the command as an `init=`
kernel shim on a fresh boot (no in-guest vsock agent yet), so command stdout
is not captured into `ExecResult` and pause/restore cannot carry a running
shell across `exec` calls. See [vzrunner/README.md](vzrunner/README.md) for
the guest bundle format and the current limitation in detail.

## Security model

Short version — read [SECURITY.md](SECURITY.md) for the full threat model:

- `seatbelt` is a strong **process** sandbox: write containment to the
  workspace (+ scratch tmp), credential-store read denial, per-sandbox
  network policy, signals scoped to the sandbox. It is **not a VM** — the
  kernel attack surface remains, and CPU/RAM are not bounded. For hostile
  code, use `vz`.
- `vz` gives VM-grade isolation, but is experimental (see limitations above).
- The HTTP service binds `127.0.0.1` by default and requires a token (0600
  token file) unless you explicitly set `LOOPBOX_NO_AUTH=1`.
- Loopbox never passes `--dangerously-skip-permissions` to harness CLIs
  itself; risky commands in loops always hit a human gate unless you opt out
  with `--auto-approve`.

Report vulnerabilities via
[GitHub security advisories](https://github.com/omnigeeker/loopbox/security/advisories/new).

## Repository layout

```
loopbox/
├── pyproject.toml            # package metadata; deps: none (stdlib only)
├── README.md / LICENSE / SECURITY.md
├── GOAL.md                   # LoopX goal file used while developing this repo
├── loopbox/
│   ├── __init__.py           # public SDK exports: Sandbox, SandboxError
│   ├── cli.py                # `loopbox` entry point (subcommands, --json)
│   ├── sdk.py                # E2B-shaped Python SDK
│   ├── store.py              # fcntl-locked atomic JSON registry; ~/.loopbox layout
│   ├── auth.py               # X-API-Key token auth for the HTTP service
│   ├── service.py            # E2B-compatible REST API (stdlib ThreadingHTTPServer)
│   ├── server.py             # legacy pre-service HTTP façade — superseded, not importable
│   ├── harness.py            # `loopbox harness` adapters: codex, claude, dsh, custom
│   ├── backends/
│   │   ├── base.py           # Backend protocol + ExecResult
│   │   ├── seatbelt.py       # default backend: sandbox-exec, SIGSTOP, APFS clones
│   │   └── vz.py             # experimental backend: Virtualization.framework
│   └── loop/
│       ├── cli.py            # `loopbox loop` sub-CLI
│       ├── engine.py         # self-think / self-check / self-iterate driver
│       ├── gates.py          # human-in-the-loop gates (GATE.md / gate.json)
│       └── state.py          # durable per-loop JSON ledger
├── tests/                    # pytest suite (LOOPBOX_INTEGRATION=1 for live tests)
├── vzrunner/
│   ├── build.sh              # swiftc build, ad-hoc signs the vz entitlement
│   ├── Sources/vzrunner/main.swift
│   └── README.md             # helper CLI contract + guest bundle format
└── docs/
    ├── TUTORIAL.en.md        # guided tutorial (English)
    ├── TUTORIAL.zh-CN.md     # guided tutorial (简体中文)
    ├── ARCHITECTURE.md       # internals: modules, data model, state machines
    ├── loopx-integration.md  # LoopX concept mapping for the loop engine
    └── i18n/                 # translated READMEs
```

## Development

```bash
python3 -m pip install -e '.[dev]'     # or: uv sync --extra dev
python3 -m pytest tests/               # unit suite (106 passing at v0.1)
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/   # + real seatbelt integration
cd vzrunner && ./build.sh              # build the Swift helper (macOS 14+, CLT)
```

CLI exit codes: `1` runtime error, `2` usage error, `124` command timeout;
`loop run` uses `0..3` as documented above. Set `LOOPBOX_DEBUG=1` for
tracebacks.

## Roadmap / known limitations

- **`vz` guest exec is an `init=` shim**: each `exec` is a fresh boot, stdout
  is not captured into `ExecResult`, and machine-state resume across exec
  boundaries needs the in-guest vsock agent (future work; the bundle/socket
  plumbing already supports it).
- **No resource quotas**: neither backend bounds CPU, RAM, or wall time of
  sandboxed work (per-command `--timeout` exists).
- **`seatbelt` ≠ VM**: process sandbox on a shared kernel; see
  [SECURITY.md](SECURITY.md).
- **`loopbox/server.py` is legacy**: retained for reference only and not
  importable; use `loopbox/service.py` (`loopbox serve`).
- **Packaging**: `pip install loopbox` from PyPI is not published yet;
  install from the repo.

## License

Apache-2.0 — see [LICENSE](LICENSE). © 2026 Loopbox Contributors.

## Documentation

- [docs/TUTORIAL.en.md](docs/TUTORIAL.en.md) — guided tutorial (English)
- [docs/TUTORIAL.zh-CN.md](docs/TUTORIAL.zh-CN.md) — guided tutorial（简体中文）
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — internals deep dive
- [docs/loopx-integration.md](docs/loopx-integration.md) — LoopX concept mapping
- [vzrunner/README.md](vzrunner/README.md) — `vz` helper and VM bundle format
- [SECURITY.md](SECURITY.md) — threat model and disclosure policy
