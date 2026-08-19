"""In-process tests for the loopbox CLI (loopbox.cli.main)."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from loopbox.cli import main
from loopbox.store import Store, home, sandbox_dir, snapshot_root

pytestmark = pytest.mark.skipif(
    shutil.which("sandbox-exec") is None,
    reason="requires macOS sandbox-exec",
)


def _cli(*argv: str) -> int:
    return main(list(argv))


def _wait_process_dead(pid: int, timeout: float = 5.0) -> None:
    """Wait until ``ps`` reports the process gone or zombie."""
    import time

    deadline = time.time() + timeout
    stat = "?"
    while time.time() < deadline:
        proc = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
        )
        stat = proc.stdout.strip()
        if not stat or stat.upper().startswith("Z"):
            return
        time.sleep(0.1)
    raise AssertionError(f"process {pid} still alive (stat={stat!r})")


def test_new_ls_exec_flow(loopbox_home, capsys):
    assert _cli("new") == 0
    sid = capsys.readouterr().out.strip()
    assert sid.startswith("sbx_")

    assert _cli("ls") == 0
    assert sid in capsys.readouterr().out

    assert _cli("--json", "ls") == 0
    listing = json.loads(capsys.readouterr().out)
    assert any(r["id"] == sid for r in listing)

    assert _cli("exec", sid, "--", "/bin/echo", "cli-ok") == 0
    assert "cli-ok" in capsys.readouterr().out

    assert _cli("exec", sid, "--", "/bin/sh", "-c", "exit 5") == 5
    capsys.readouterr()

    assert _cli("rm", sid, "--purge") == 0


def test_snapshot_restore_fork_rm_purge(loopbox_home, capsys):
    assert _cli("new") == 0
    sid = capsys.readouterr().out.strip()

    assert _cli("exec", sid, "--", "/bin/sh", "-c", "echo v1 > state.txt") == 0
    assert _cli("snapshot", sid, "--name", "s1") == 0
    assert capsys.readouterr().out.strip() == "s1"

    assert _cli("snapshots", sid) == 0
    assert "s1" in capsys.readouterr().out

    assert _cli("exec", sid, "--", "/bin/sh", "-c", "echo v2 > state.txt") == 0
    assert _cli("restore", sid, "s1") == 0
    assert _cli("exec", sid, "--", "/bin/cat", "state.txt") == 0
    assert capsys.readouterr().out.strip().endswith("v1")

    assert _cli("fork", sid, "--snapshot", "s1") == 0
    child = capsys.readouterr().out.strip()
    assert child.startswith("sbx_") and child != sid

    assert _cli("rm", child, "--purge") == 0
    capsys.readouterr()
    assert not sandbox_dir(child).exists()
    assert not snapshot_root(child).exists()

    registry = Store()
    with pytest.raises(Exception):
        registry.get(child)

    assert _cli("rm", sid, "--purge") == 0
    assert not sandbox_dir(sid).exists()
    # No leakage: nothing left under the temp LOOPBOX_HOME sandboxes dir.
    leftovers = list((home() / "sandboxes").iterdir())
    assert leftovers == []


def test_spawn_pause_resume_kill(loopbox_home, capsys):
    assert _cli("new") == 0
    sid = capsys.readouterr().out.strip()

    assert _cli("spawn", sid, "--", "/bin/sleep", "60") == 0
    pgid = int(capsys.readouterr().out.strip())
    assert pgid > 0

    assert _cli("pause", sid) == 0
    assert "paused" in capsys.readouterr().out
    assert _cli("resume", sid) == 0
    assert "resumed" in capsys.readouterr().out

    assert _cli("rm", sid, "--purge") == 0
    _wait_process_dead(pgid)


def test_exec_without_separator_errors(loopbox_home, capsys):
    assert _cli("new") == 0
    sid = capsys.readouterr().out.strip()
    assert _cli("exec", sid) == 1
    assert "no command given" in capsys.readouterr().err
    assert _cli("rm", sid, "--purge") == 0


def test_rm_unknown_sandbox_errors(loopbox_home, capsys):
    assert _cli("rm", "sbx_missing") == 1
    assert "not found" in capsys.readouterr().err


def test_doctor(loopbox_home, capsys):
    assert _cli("doctor", "--json") == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    names = {c["name"] for c in report["checks"]}
    assert {"arm64 architecture", "sandbox-exec available", "APFS clonefile support"} <= names


def test_harness_and_loop_passthrough(loopbox_home, monkeypatch, capsys):
    monkeypatch.setenv("LOOPBOX_HARNESS", " ")
    from loopbox.loop import engine

    monkeypatch.setattr(engine, "_find_harness", lambda: None)
    assert _cli("harness", "list") == 0
    out = capsys.readouterr().out
    assert "codex" in out

    assert _cli("loop", "new", "--goal", "cli passthrough goal") == 0
    assert "created loop" in capsys.readouterr().out


def test_no_subcommand_prints_help(capsys):
    assert main([]) == 2
    assert "usage: loopbox" in capsys.readouterr().out
