"""Adapters that run agent harness CLIs inside loopbox sandboxes.

A *harness* is an agent-facing CLI (OpenAI Codex, Kimi Code, Claude Code,
DeepSeek Harness, ...). Harnesses choose their runtime by where they
execute: if you start ``codex`` on the host, codex's own sandbox is all that
protects the host. Loopbox therefore runs the *whole* harness CLI inside a
sandbox by passing the harness argv to :meth:`Backend.exec` /
:meth:`Backend.spawn` with the sandbox workspace as cwd, so the outer
loopbox boundary (Seatbelt profile or VM) applies no matter how the harness
is configured internally:

    loopbox harness run <sbx_id> codex -- exec "fix the failing tests"
    loopbox harness run <sbx_id> kimi -- -p "review this repo"
    loopbox harness doctor
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Sequence

from loopbox.backends import ExecResult, get_backend
from loopbox.store import Store


def _default_argv(binary: str, args: Sequence[str]) -> list[str]:
    """Build a launch argv: the harness binary followed by user args verbatim.

    Loopbox deliberately does not inject harness-native sandbox/permission
    flags: the outer loopbox sandbox is the boundary, and silent flag
    injection would fight flags the user already passed.
    """
    return [binary, *[str(a) for a in args]]


@dataclass(frozen=True)
class HarnessSpec:
    """Static description of one agent harness CLI.

    Attributes:
        name: Registry key, e.g. ``"codex"``.
        binary: Executable looked up on ``PATH`` via ``shutil.which``.
        version_args: Arguments appended to the binary for a version probe.
        notes: One paragraph of sandbox-integration notes (see module docstring
            for the trust model the notes assume).
        launch_examples: Example argument lines for ``loopbox harness run``.
        install_hint: How to obtain the CLI when it is missing.
    """

    name: str
    binary: str
    version_args: tuple[str, ...] = ("--version",)
    notes: str = ""
    launch_examples: tuple[str, ...] = ()
    install_hint: str = ""
    known: bool = field(default=True)

    def build_argv(self, args: Sequence[str]) -> list[str]:
        """Return the full argv used to launch this harness."""
        return _default_argv(self.binary, args)


_CODEX_NOTES = (
    "OpenAI Codex CLI. Bare `codex` starts the interactive TUI; "
    "`codex exec \"prompt\"` runs non-interactively. Codex ships its own "
    "OS-level sandbox with modes `--sandbox read-only | workspace-write | "
    "danger-full-access` plus an `--ask-for-approval` policy (`--full-auto` "
    "is the deprecated shortcut for workspace-write); on macOS it enforces "
    "via Seatbelt, the same mechanism as loopbox's seatbelt backend. Global "
    "flags must precede the `exec` subcommand in some versions. Inside "
    "loopbox, launch the whole CLI in the sandbox so the outer profile is "
    "the boundary; codex's native modes then act as defense in depth, not "
    "as the primary control."
)

_CLAUDE_NOTES = (
    "Anthropic Claude Code CLI. Bare `claude` starts the interactive "
    "session; `claude -p \"prompt\"` runs headless. Safety is prompt-based: "
    "each tool call asks permission unless granted via `--allowedTools`, "
    "permission modes, or settings. `--dangerously-skip-permissions` "
    "(equivalent to `--permission-mode bypassPermissions`) auto-approves "
    "everything and refuses to run as root -- use it ONLY inside a loopbox "
    "sandbox, never on the host; loopbox never adds it for you. Recent "
    "versions also offer a native sandboxed-Bash setting (macOS "
    "Seatbelt-based). Running the whole CLI via `loopbox harness run` gives "
    "a boundary independent of the permission system, while default "
    "outbound network access still applies inside the sandbox unless the "
    "sandbox record sets network=\"deny\"."
)

# dsh documentation is thin: it is a developer preview (released
# 2026-08-13) whose README promises breaking changes, and its sandboxing is
# configured through Cordis plugins rather than fixed CLI flags. Verify the
# flags below against `dsh --help` for the installed version.
_DSH_NOTES = (
    "DeepSeek Harness (`dsh`), an MIT-licensed agent harness released as a "
    "developer preview in August 2026. Everything -- models, tools, "
    "sandboxes, UI -- is a plugin loaded by the Cordis runtime from a "
    "`cordis.yml` loader config, so sandbox behavior is plugin-configured "
    "rather than a fixed CLI flag. `dsh cli` is the interactive terminal "
    "agent; `dsh web` serves a UI on 127.0.0.1:3080; `--profile <name>` "
    "selects a plugin bundle (a `headless` profile ships by default). Docs "
    "are thin and the preview warns of compatibility-breaking changes, so "
    "treat flag names (including the `--version` probe) as provisional and "
    "confirm with `dsh --help` for the installed version."
)

_KIMI_NOTES = (
    "Kimi Code CLI (`kimi`). Bare `kimi` starts the interactive session; "
    "`kimi -p \"prompt\"` (or `--prompt`) runs one prompt non-interactively "
    "and prints the response. Permission control is by mode: default asks, "
    "`-y/--yolo` auto-approves regular tool calls (the agent may still ask "
    "questions), and `--auto` runs fully autonomously -- reserve full-autonomy "
    "modes for runs inside a loopbox sandbox, never on the host. `kimi` also "
    "ships `kimi doctor` (config validation), `kimi web` (local web UI), and "
    "an ACP server mode (`kimi acp`). Under loopbox, the outer Seatbelt "
    "profile or VM stays the boundary regardless of the chosen mode."
)


KNOWN_HARNESSES: dict[str, HarnessSpec] = {
    spec.name: spec
    for spec in (
        HarnessSpec(
            name="codex",
            binary="codex",
            notes=_CODEX_NOTES,
            launch_examples=(
                'exec --sandbox workspace-write "fix the failing tests"',
                "--interactive   # full TUI inside the sandbox",
            ),
            install_hint="npm install -g @openai/codex",
        ),
        HarnessSpec(
            name="kimi",
            binary="kimi",
            notes=_KIMI_NOTES,
            launch_examples=(
                '-p "summarise this repo"   # headless one-shot',
                "--interactive             # REPL spawned inside the sandbox",
            ),
            install_hint="see https://github.com/MoonshotAI/kimi-code",
        ),
        HarnessSpec(
            name="claude",
            binary="claude",
            notes=_CLAUDE_NOTES,
            launch_examples=(
                '-p "summarise this repo"',
                "--interactive   # REPL inside the sandbox",
            ),
            install_hint="see https://docs.anthropic.com (official installer)",
        ),
        HarnessSpec(
            name="dsh",
            binary="dsh",
            notes=_DSH_NOTES,
            launch_examples=(
                "cli",
                "--profile headless cli   # plugin-bundle selection",
            ),
            install_hint="npm install -g @deepseek-ai/dsh  (developer preview)",
        ),
    )
}

_VERSION_PROBE_TIMEOUT_S = 5.0


def _spec(name: str) -> HarnessSpec:
    """Return the known spec for ``name`` or a generic custom one by binary."""
    spec = KNOWN_HARNESSES.get(name)
    if spec is not None:
        return spec
    return HarnessSpec(
        name=name,
        binary=name,
        notes=(
            f"Custom harness adapter: `{name}` is treated as an opaque "
            "binary. It is launched inside the sandbox with the workspace "
            "as cwd; any sandboxing/permission flags are the caller's "
            "responsibility and must be passed after `--`."
        ),
        known=False,
    )


def _probe_version(spec: HarnessSpec, path: str) -> str | None:
    """Run the harness's version probe; return None when it fails."""
    try:
        proc = subprocess.run(
            [path, *spec.version_args],
            capture_output=True,
            text=True,
            timeout=_VERSION_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    output = (proc.stdout or proc.stderr).strip()
    return output.splitlines()[0] if output else None


def describe(name: str) -> dict:
    """Describe one harness: detection, version probe, and usage notes.

    Unknown names fall back to a generic custom adapter whose binary equals
    ``name``.
    """
    spec = _spec(name)
    path = shutil.which(spec.binary)
    return {
        "name": spec.name,
        "binary": spec.binary,
        "known": spec.known,
        "installed": path is not None,
        "path": path,
        "version": _probe_version(spec, path) if path else None,
        "version_probe": [spec.binary, *spec.version_args],
        "launch_examples": list(spec.launch_examples),
        "notes": spec.notes,
        "install_hint": spec.install_hint,
    }


def list_available() -> list[dict]:
    """List every known harness with its detection status.

    Each entry carries ``name``, ``binary``, ``installed``, ``path`` and
    ``notes``.
    """
    out = []
    for name in KNOWN_HARNESSES:
        info = describe(name)
        out.append(
            {
                "name": info["name"],
                "binary": info["binary"],
                "installed": info["installed"],
                "path": info["path"],
                "notes": info["notes"],
            }
        )
    return out


def run(
    sandbox_id: str,
    harness: str,
    argv: Sequence[str],
    *,
    interactive: bool = False,
    timeout: float | None = None,
) -> ExecResult:
    """Run a harness CLI inside one sandbox.

    The sandbox record is loaded from the :class:`Store`, its backend is
    resolved from ``record["backend"]``, and the harness argv is executed
    with the sandbox workspace as cwd (backends default ``cwd=None`` to the
    workspace root).

    With ``interactive=True`` the harness is spawned as a detached
    long-running process via :meth:`Backend.spawn` and the recorded process
    groups are persisted so pause/resume/kill still see it; the returned
    :class:`ExecResult` notes the spawned pid/pgid in ``stdout``. Note that
    the seatbelt backend detaches stdio for spawned processes, so a
    full-screen TUI spawned this way has no terminal attached.

    Raises:
        StoreError: If ``sandbox_id`` is not registered.
        FileNotFoundError: If the harness binary is not on ``PATH``.
    """
    spec = _spec(harness)
    if shutil.which(spec.binary) is None:
        raise FileNotFoundError(
            f"harness {spec.name!r} not found: {spec.binary!r} is not on PATH"
            + (f" (install: {spec.install_hint})" if spec.install_hint else "")
        )
    full_argv = spec.build_argv(argv)
    store = Store()
    record = store.get(sandbox_id)
    backend = get_backend(record.get("backend"))
    if interactive:
        pgid = backend.spawn(record, full_argv)
        # spawn() only mutates the in-memory record; persist the new pgid so
        # pause/resume/kill can reach the harness process group.
        store.update(sandbox_id, engine=record.get("engine") or {})
        return ExecResult(
            stdout=f"spawned harness {spec.name!r} in sandbox {sandbox_id}: "
            f"pid/pgid {pgid} ({shlex.join(full_argv)})\n",
            stderr="",
            exit_code=0,
            duration_s=0.0,
            command=full_argv,
        )
    return backend.exec(record, full_argv, cwd=None, timeout=timeout)


# -- CLI --------------------------------------------------------------------

_DOCTOR_GUIDANCE = """\
How to point a harness at loopbox as its runtime:
  Harnesses choose their runtime by where they execute. Starting `codex`,
  `claude` or `dsh` on the host leaves protection to the harness's own
  sandbox/permission system. To make loopbox the runtime, launch the whole
  harness CLI inside a sandbox:

      loopbox harness run <sbx_id> codex -- exec --sandbox workspace-write "task"
      loopbox harness run <sbx_id> kimi -- -p "task"
      loopbox harness run <sbx_id> kimi --interactive
      loopbox harness run <sbx_id> claude -- -p "task"
      loopbox harness run <sbx_id> dsh -- cli

  inside the sandbox, writes are confined to the workspace (plus scratch
  tmp), credential stores are denied, and network follows the sandbox
  record. Do NOT pass --dangerously-skip-permissions or
  --sandbox danger-full-access on the host; reserve full-autonomy flags
  for runs inside a sandbox, where loopbox is the boundary.
"""


def _cmd_list(args: argparse.Namespace) -> int:
    entries = list_available()
    if args.json:
        print(json.dumps(entries, indent=2))
        return 0
    for entry in entries:
        mark = "ok" if entry["installed"] else "missing"
        print(f"{entry['name']:<10} {entry['binary']:<10} {mark:<8} {entry['path'] or '-'}")
    return 0


def _cmd_describe(args: argparse.Namespace) -> int:
    info = describe(args.name)
    if not info["installed"]:
        print(
            f"warning: {info['binary']!r} not found on PATH"
            + (f" (install: {info['install_hint']})" if info["install_hint"] else ""),
        )
    for key in ("name", "binary", "known", "installed", "path", "version"):
        print(f"{key}: {info[key]}")
    print(f"version_probe: {shlex.join(info['version_probe'])}")
    if info["launch_examples"]:
        print("launch_examples:")
        for example in info["launch_examples"]:
            print(f"  loopbox harness run <sbx_id> {info['name']} -- {example}")
    print(f"notes: {info['notes']}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    result = run(
        args.sandbox_id,
        args.name,
        args.harness_argv,
        interactive=args.interactive,
        timeout=args.timeout,
    )
    if args.interactive:
        print(result.stdout, end="")
        return result.exit_code
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    return result.exit_code


def _cmd_doctor(args: argparse.Namespace) -> int:
    found = 0
    for name in KNOWN_HARNESSES:
        info = describe(name)
        if info["installed"]:
            found += 1
            version = info["version"] or "version probe failed"
            print(f"[ok]      {name:<8} {info['path']}  ({version})")
        else:
            print(f"[missing] {name:<8} install: {info['install_hint']}")
    print()
    print(_DOCTOR_GUIDANCE)
    if found == 0:
        print("no known harness CLIs found on PATH", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopbox harness",
        description="Run agent harness CLIs (codex, kimi, claude, dsh, custom) inside loopbox sandboxes.",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_list = sub.add_parser("list", help="List known harnesses and detection status.")
    p_list.add_argument("--json", action="store_true", help="Emit JSON.")
    p_list.set_defaults(func=_cmd_list)

    p_desc = sub.add_parser("describe", help="Describe one harness adapter.")
    p_desc.add_argument("name", help="Harness name (known or custom binary name).")
    p_desc.set_defaults(func=_cmd_describe)

    p_run = sub.add_parser(
        "run",
        help="Run a harness inside a sandbox.",
        epilog="Loopbox flags must precede '--'; everything after '--' is "
        "passed to the harness verbatim.",
    )
    p_run.add_argument("--interactive", action="store_true", help="Spawn detached instead of running to completion.")
    p_run.add_argument("--timeout", type=float, default=None, help="Seconds before exec is aborted.")
    p_run.add_argument("sandbox_id")
    p_run.add_argument("name", help="Harness name (known or custom binary name).")
    p_run.set_defaults(func=_cmd_run)

    p_doctor = sub.add_parser("doctor", help="Check known harnesses and print integration guidance.")
    p_doctor.set_defaults(func=_cmd_doctor)
    return parser


def harness_main(argv: list[str] | None = None) -> int:
    """Entry point for the ``loopbox harness`` subcommand family.

    For the ``run`` subcommand, the argv is split at the first ``--``
    before parsing: the tail is forwarded to the harness untouched, so
    harness flags like ``--sandbox`` or ``-p`` can never collide with
    loopbox flags.
    """
    argv = list(argv) if argv is not None else sys.argv[1:]
    harness_argv: list[str] = []
    if argv and argv[0] == "run" and "--" in argv:
        sep = argv.index("--")
        argv, harness_argv = argv[:sep], argv[sep + 1 :]
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.subcommand == "run":
        args.harness_argv = harness_argv
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(harness_main())
