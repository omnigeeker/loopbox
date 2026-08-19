# Loopbox — LoopX Goal

## Objective

Build and continuously improve **Loopbox**: a local-first, E2B-protocol-compatible
sandbox for macOS on Apple Silicon (M1–M5) that lets agent harnesses
(Codex CLI, Claude Code, DSH / DeepSeek Harness, custom runners) execute
untrusted work safely on the user's own machine.

## Success criteria

1. `loopbox doctor` passes on a clean M-series Mac (macOS 13+).
2. Full CLI parity with the SDK: create / ls / exec / spawn / pause / resume /
   snapshot / restore / fork / rm / serve / harness / loop.
3. E2B-shaped HTTP API with `X-API-Key` auth passes the smoke suite.
4. Seatbelt backend: write containment + credential carve-outs verified by
   integration tests (`LOOPBOX_INTEGRATION=1`).
5. Snapshots are APFS copy-on-write; fork produces an independent twin.
6. Harness CLIs (codex / claude / dsh) run inside a sandbox via
   `loopbox harness <name>`.
7. The loop engine self-checks, plans, executes bounded slices, and writes a
   `GATE.md` question instead of guessing when human judgment is required.
8. Experimental `vz` backend compiles with `cd vzrunner && ./build.sh` and
   boots a Linux guest on macOS 14+.

## Operating rules for the loop

- Code and comments in English; README/i18n stays in sync across languages.
- Never weaken the Seatbelt profile without a human gate.
- Never commit `.loopx/`, `.loopbox/`, `.local/`, tokens, or local paths.
- Every slice ends with: run tests, write evidence, propose next todo.
- Human gates: publishing, security-model changes, destructive commands,
  dependency additions.

## Backlog (priority order)

1. Harden `vz` backend: guest image pipeline, guest agent for `exec`.
2. PTY streaming for `exec` (envd-style streaming output).
3. Template registry (prebuilt workspace images with toolchains).
4. `loopbox loop` scheduler hints aligned with LoopX quota protocol.
5. CI on GitHub Actions (macos-14 arm64 runner).
