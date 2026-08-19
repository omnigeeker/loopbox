# Security policy

Loopbox is a local sandbox designed to run untrusted code produced by AI
agent harnesses on your own Mac. This document states exactly what isolation
you get, what you do not get, and how to report vulnerabilities.

## Supported versions

Only the latest commit of the main branch receives security fixes. The
project is alpha (`Development Status :: 3 - Alpha`, v0.1.x); interfaces may
change, security boundaries are not.

## Threat model

Asset classes loopbox aims to protect, against code executing *inside* a
sandbox (including code written by an LLM agent):

1. **Filesystem** — everything outside the sandbox workspace.
2. **Credentials** — SSH/GPG keys, cloud CLI configs, GitHub CLI config,
   keychains, browser cookies.
3. **Network** — per the sandbox's network policy.
4. **Availability of the host** — other sandboxes, other users' processes.

### seatbelt backend: a strong *process* sandbox

The default `seatbelt` backend runs each command under macOS `sandbox-exec`
with a per-sandbox profile generated from `(deny default)`:

- **Denied (always)**: read *and write* access to credential stores —
  `~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gh`, `~/Library/Keychains`,
  `~/Library/Cookies`, and the Chrome profile directory
  (`~/Library/Application Support/Google/Chrome`). Deny rules win over allow
  rules in Seatbelt regardless of order.
- **Writes** are allowed only in the sandbox workspace, scratch tmp
  (`/tmp`, `/private/tmp`, `/private/var/folders`), a few character devices
  any runtime expects (`/dev/null`, `/dev/random`, …), and any paths the
  caller explicitly adds via `extra_rw`.
- **Reads** are broadly permitted (system libraries, your toolchain) so agent
  CLIs keep working.
- **Network** policy per sandbox: `outbound` (default), `all`
  (inbound+outbound), or `deny`.
- **Signals** are scoped `same-sandbox`; pause/resume is SIGSTOP/SIGCONT on
  the sandbox's recorded process groups.
- **File APIs** (SDK and HTTP) resolve paths against the workspace after
  symlink resolution and refuse anything that escapes (`SandboxError` / HTTP
  400). `exec --cwd` is confined the same way.

**What seatbelt does not protect against.** It is a process sandbox on a
shared kernel, not a VM:

- A sandbox escape or kernel exploit escapes with it. The macOS kernel attack
  surface (syscalls, XPC, IOKit, Mach) remains reachable from inside the
  profile subject to macOS's own sandbox rules.
- `sandbox-exec` itself is a long-deprecated-but-functional Apple
  mechanism; Apple does not document it as a supported security boundary.
- No resource quotas: nothing bounds CPU, RAM, disk, or the process count of
  sandboxed work. A fork bomb inside the sandbox affects the host.
- Sandboxing is per-invocation: there is no daemon monitoring a sandbox
  between commands.

### vz backend: VM-grade isolation

The experimental `vz` backend runs work in an ARM64 Linux VM
(Virtualization.framework) with its own kernel — the appropriate boundary
when the code is adversarial rather than merely careless. Constraints:

- Requires macOS 14+, the built `vzrunner` helper
  (`cd vzrunner && ./build.sh`), and a caller-supplied guest bundle (kernel +
  rootfs; see `vzrunner/README.md`).
- Networking is NAT outbound-only; the workspace is shared into the guest via
  VirtioFS, so the guest *can* write the sandbox workspace (by design).
- Current exec limitation: there is no in-guest agent. Each `exec` boots the
  VM with an `init=` shim that runs the command as PID 1 and powers off —
  per-boot isolation, no persistent in-guest session, stdout not captured
  into `ExecResult`. True time travel needs `snapshot`/`restore` plus that
  future agent.

### Environment inheritance

Sandboxed commands inherit the calling process environment (record env and
per-call env are merged on top). **Anything exported in your shell —
including LLM provider API keys like `OPENAI_API_KEY` — is visible inside
the sandbox.** Do not export secrets you do not trust the workload with;
prefer `network=deny` when code exfiltration is a concern, and keep secrets
out of env vars when using `outbound`/`all`.

### Harness execution

`loopbox harness run` executes the agent CLI (codex/claude/dsh/…) *inside*
the sandbox so the loopbox profile is the boundary. Loopbox **never** adds
`--dangerously-skip-permissions`, `--sandbox danger-full-access`, or any
other harness permission-bypass flag itself; such flags, if you pass them
after `--`, are your explicit choice and remain meaningful only inside the
sandbox. Never pass them on the host.

### HTTP service

- Binds `127.0.0.1:31885` by default. Binding a non-loopback address
  (`--host 0.0.0.0`) exposes sandbox control to anyone who can reach that
  port — do not do this on an untrusted network.
- Auth is a local bearer token generated into `~/.loopbox/auth.json` (mode
  0600), required on every route except `GET /health`, sent as `X-API-Key`
  or `Authorization: Bearer`, compared in constant time.
- `LOOPBOX_NO_AUTH=1` disables auth entirely — acceptable for local
  development/tests, **unsafe** on any shared or networked machine.

### Loop engine

The think step runs the harness CLI on the host (it only decides); execution
runs inside the loop's sandbox. Risky commands (`rm -rf`, `sudo`,
`git push`, `mkfs`, `dd of=`, pipes into shells, recursive chmod/chown,
writes to `/dev/sd*`) always hit an `approve_step` human gate, unless you run
with `--auto-approve` — treat that flag as CI/batch mode where you
consciously trade the human gate for throughput behind the sandbox boundary.

## Reporting a vulnerability

Please do **not** open a public issue for security reports. Use GitHub's
private advisory flow:

https://github.com/omnigeeker/loopbox/security/advisories/new

Include: the affected version/commit, the backend involved, a reproducer
(command or HTTP request sequence), and the impact (what isolation failed).
We aim to acknowledge within 72 hours and will coordinate disclosure with
you, crediting reporters in the release notes unless you prefer otherwise.
