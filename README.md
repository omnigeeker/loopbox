# Loopbox

**Local-first, E2B-protocol-compatible sandboxes for macOS on Apple Silicon (M1–M5).**

[简体中文](docs/i18n/README.zh-CN.md) · [繁體中文](docs/i18n/README.zh-TW.md) · [日本語](docs/i18n/README.ja.md) · [Español](docs/i18n/README.es.md) · [Português](docs/i18n/README.pt.md) · [Deutsch](docs/i18n/README.de.md) · [Français](docs/i18n/README.fr.md)

Loopbox lets AI agent harnesses (Codex CLI, Claude Code, DSH / DeepSeek Harness, and
your own runners) execute untrusted work inside a real sandbox on your own Mac —
no cloud, no Linux VM tax for everyday tasks, and an experimental
Virtualization.framework backend when you want whole-machine isolation with
pause / fork / resume of a running VM.

```
pip install -e .        # Python 3.10+, macOS 13+ on Apple Silicon
loopbox doctor          # self-check: arch, seatbelt, APFS clones, smoke test

SID=$(loopbox new)                        # create a sandbox
loopbox exec $SID -- echo "hello sandbox" # run inside it
loopbox snapshot $SID --name v1           # APFS copy-on-write snapshot
loopbox fork $SID --snapshot v1           # branch off an identical twin
loopbox pause $SID && loopbox resume $SID # freeze / continue
loopbox harness codex                     # start Codex CLI inside a sandbox
loopbox rm $SID --purge
```

## Why

Hosted sandboxes (E2B and friends) are excellent, but sometimes the code,
credentials, and latency budget must stay on your own machine. Loopbox keeps
the E2B *usage protocol* — SDK shape, HTTP API shape, snapshot semantics —
while executing entirely locally.

## Features

- **Apple Silicon native** — runs on M1 through M5; requires macOS 13+
  (the experimental `vz` VM backend needs macOS 14+).
- **Two isolation engines**
  - `seatbelt` (default): macOS Seatbelt process sandbox via `sandbox-exec`.
    Instant startup, write-scoped filesystem isolation, credential stores
    (`~/.ssh`, keychains, browser cookies, cloud CLI configs) are never
    readable, network policy per sandbox (`outbound` / `all` / `deny`).
  - `vz` (experimental): Virtualization.framework microVM via the bundled
    Swift `vzrunner` helper. Whole-machine state enables true
    pause → snapshot → fork → resume of a running machine.
- **E2B-compatible protocol**
  - Python SDK shaped like the E2B SDK: `Sandbox.create()`, `sandbox.commands.run()`,
    `sandbox.files.read/write()`, `sandbox.pause()`, `sandbox.fork()`, `sandbox.kill()`.
  - HTTP API with E2B-shaped routes (`POST /sandboxes`, `POST /sandboxes/{id}/exec`,
    `pause` / `resume` / `fork` / `snapshots`, file operations), protected by an
    `X-API-Key` bearer token, exactly like pointing an E2B client at a local base URL.
- **Full local CLI** — every feature is available from `loopbox ...`;
  `--json` everywhere for scripting.
- **High-performance snapshots** — APFS copy-on-write clones make workspace
  snapshots and forks O(1) in unchanged data.
- **Agent-harness ready** — `loopbox harness codex|claude|dsh` starts the
  agent CLI inside a sandbox; the `loopbox harness` / `loopbox loop`
  subcommands provide an eval harness and a LoopX-friendly loop engine with
  human-in-the-loop gates.
- **Zero runtime dependencies** — Python standard library only.

## Install

```bash
git clone https://github.com/omnigeeker/loopbox.git
cd loopbox
python3 -m pip install -e .   # or: pipx install .
loopbox doctor
```

Optional, for the VM backend (macOS 14+, Xcode CLT):

```bash
cd vzrunner && ./build.sh     # builds + ad-hoc signs with the virtualization entitlement
```

## The E2B protocol mapping

| E2B SDK (hosted)             | Loopbox SDK (local)                    |
| ---------------------------- | -------------------------------------- |
| `Sandbox.create(template=…)` | `Sandbox.create(template="seatbelt")`  |
| `Sandbox.connect(id)`        | `Sandbox.connect(sandbox_id)`          |
| `sbx.commands.run(cmd)`      | `sbx.commands.run(cmd)`                |
| `sbx.files.read/write(path)` | `sbx.files.read/write(path)`           |
| `sbx.beta_pause()`           | `sbx.pause()` / `sbx.beta_pause()`     |
| `sbx.fork()`                 | `sbx.fork()`                           |
| `sbx.kill()`                 | `sbx.kill()`                           |

| E2B HTTP                             | Loopbox HTTP                              |
| ------------------------------------ | ----------------------------------------- |
| `POST /sandboxes`                    | `POST /sandboxes`                         |
| `GET /sandboxes`, `GET /sandboxes/{id}` | identical                              |
| `DELETE /sandboxes/{id}`             | identical                                 |
| `POST /sandboxes/{id}/pause`         | identical                                 |
| `POST /sandboxes/{id}/resume`        | identical                                 |
| `POST /sandboxes/{id}/fork`          | identical                                 |
| envd `POST /process`                 | `POST /sandboxes/{id}/exec`               |
| envd file ops                        | `GET|PUT /sandboxes/{id}/files`           |

Auth: the service generates a token at `~/.loopbox/auth.json` (0600) and
expects it as `X-API-Key` — the same header an E2B client already sends.

## Running agent harnesses inside a sandbox

```bash
loopbox harness codex          # Codex CLI, isolated, workspace = sandbox
loopbox harness claude         # Claude Code
loopbox harness dsh -- --profile web   # DSH / DeepSeek Harness, args after --
```

The harness process runs under Seatbelt: it can read your toolchain and reach
the network (default policy `outbound`), but it can only *write* inside the
sandbox workspace, and it can never read your credentials. From another
terminal you can `loopbox pause` the whole session, `loopbox snapshot` it,
`loopbox fork` an exploratory branch, and `loopbox resume` — the human stays
in the loop.

## The loop engine

`loopbox loop` runs a self-check → self-plan → act → verify cycle on a goal,
with durable state under `.loopbox/` and **human gates**: whenever the loop
needs judgment (plan approval, risky action, ambiguous evidence) it writes a
`GATE.md` question and waits for a human answer instead of guessing. Pair it
with [LoopX](https://github.com/huangruiteng/loopx) for cross-harness goal
state, quotas and heartbeats — see [docs/loopx-integration.md](docs/loopx-integration.md).

## Security model (honest version)

- `seatbelt` is a strong **process** sandbox: write containment, credential
  carve-outs, optional network denial. It is not a VM; a determined attacker
  with a kernel exploit is out of scope. For that, use the `vz` backend.
- The HTTP service binds to `127.0.0.1` by default and always requires a
  token unless `LOOPBOX_NO_AUTH=1` is set (never do that on a shared machine).
- Sandbox state lives in `~/.loopbox` (override with `LOOPBOX_HOME`).

## Development

```bash
python3 -m pip install -e '.[dev]'
python3 -m pytest tests/                        # unit tests
LOOPBOX_INTEGRATION=1 python3 -m pytest tests/  # + real seatbelt integration tests
```

Code and comments are in English. The documentation is multilingual:
简体中文 · 繁體中文 · 日本語 · Español · Português · Deutsch · Français.

## License

Apache-2.0
