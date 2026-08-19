"""Tests for the Seatbelt backend: profile rendering and real lifecycle.

The lifecycle tests run real ``sandbox-exec`` commands on macOS (fast, no
network). They are skipped when ``sandbox-exec`` is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from loopbox.backends.seatbelt import SeatbeltBackend
from loopbox.store import Store, new_id, snapshot_root, workspace_dir

pytestmark = pytest.mark.skipif(
    shutil.which("sandbox-exec") is None,
    reason="requires macOS sandbox-exec",
)


def _make_record(backend: SeatbeltBackend, sandbox_id: str | None = None, **overrides) -> dict:
    record = {
        "id": sandbox_id or new_id("sbx"),
        "backend": backend.name,
        "status": "running",
        "network": overrides.pop("network", "outbound"),
        "env": {},
        "metadata": {},
    }
    record.update(overrides)
    return record


def _wait_process_dead(pid: int, timeout: float = 5.0) -> None:
    """Wait until ``ps`` reports the process gone or zombie-reaped."""
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


@pytest.fixture()
def sandbox(tracked_home):
    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    Store().add(record)
    yield record


def test_create_writes_profile_and_workspace(tracked_home):
    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    ws = workspace_dir(record["id"])
    assert ws.is_dir()
    profile = ws.parent / "profile.sb"
    assert profile.is_file()
    assert record["engine"]["profile"] == str(profile)
    assert "(deny default)" in profile.read_text(encoding="utf-8")


def test_exec_roundtrip(sandbox):
    backend = SeatbeltBackend()
    result = backend.exec(sandbox, ["/bin/echo", "hello-loopbox"])
    assert result.ok
    assert result.stdout == "hello-loopbox\n"
    assert result.exit_code == 0
    assert result.duration_s > 0


def test_exec_shell_features_and_failure_exit_code(sandbox):
    backend = SeatbeltBackend()
    ok = backend.exec(sandbox, ["/bin/sh", "-c", "echo piped | tr a-z A-Z"])
    assert ok.ok and ok.stdout.strip() == "PIPED"
    bad = backend.exec(sandbox, ["/bin/sh", "-c", "exit 3"])
    assert bad.exit_code == 3 and not bad.ok


def test_exec_cwd_relative_and_contained(sandbox):
    backend = SeatbeltBackend()
    ws = workspace_dir(sandbox["id"])
    (ws / "sub").mkdir()
    in_sub = backend.exec(sandbox, ["/bin/pwd"], cwd="sub")
    assert in_sub.ok
    assert Path(in_sub.stdout.strip()) == (ws / "sub").resolve()


def test_exec_cwd_escape_rejected(sandbox):
    backend = SeatbeltBackend()
    for bad_cwd in ("..", "../..", "/tmp", "../../.."):
        with pytest.raises(ValueError, match="workspace"):
            backend.exec(sandbox, ["/bin/echo", "x"], cwd=bad_cwd)


def test_exec_cwd_in_symlinked_workspace(tmp_path, monkeypatch):
    """LOOPBOX_HOME behind a symlink must not break the cwd containment check."""
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    link = tmp_path / "link-home"
    link.symlink_to(real_home)
    monkeypatch.setenv("LOOPBOX_HOME", str(link))

    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    result = backend.exec(record, ["/bin/echo", "linked-ok"])
    assert result.ok and result.stdout.strip() == "linked-ok"
    with pytest.raises(ValueError, match="workspace"):
        backend.exec(record, ["/bin/echo", "x"], cwd="..")


def test_workspace_write_allowed_home_write_denied(sandbox):
    backend = SeatbeltBackend()
    inside = backend.exec(sandbox, ["/bin/sh", "-c", "echo data > wsfile && cat wsfile"])
    assert inside.ok and inside.stdout.strip() == "data"

    # The escape target must be outside every writable allowance (workspace
    # + /tmp + /private/var/folders scratch): use the user's real home.
    escape = Path.home() / ".loopbox-escape-attempt"
    try:
        outside = backend.exec(
            sandbox, ["/bin/sh", "-c", f"echo nope > '{escape}'"], timeout=30
        )
        assert not outside.ok
        assert not escape.exists()
    finally:
        escape.unlink(missing_ok=True)


def test_sensitive_store_denied(sandbox):
    backend = SeatbeltBackend()
    ssh_key = Path.home() / ".ssh"
    if not ssh_key.exists():
        pytest.skip("no ~/.ssh on this machine")
    result = backend.exec(sandbox, ["/bin/ls", str(ssh_key)], timeout=30)
    assert not result.ok
    assert (
        "Operation not permitted" in result.stderr
        or "Operation not permitted" in result.stdout
    )


@pytest.mark.parametrize(
    ("mode", "allowed_line"),
    [
        ("outbound", "(allow network-outbound)"),
        ("all", "(allow network*)"),
    ],
)
def test_network_profile_modes(tmp_path, monkeypatch, mode, allowed_line):
    monkeypatch.setenv("LOOPBOX_HOME", str(tmp_path))
    backend = SeatbeltBackend()
    record = _make_record(backend, network=mode)
    backend.create(record)
    profile = Path(record["engine"]["profile"]).read_text(encoding="utf-8")
    assert allowed_line in profile


def test_network_deny_profile_and_behavior(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPBOX_HOME", str(tmp_path))
    backend = SeatbeltBackend()
    record = _make_record(backend, network="deny")
    backend.create(record)
    profile = Path(record["engine"]["profile"]).read_text(encoding="utf-8")
    assert "(deny network*)" in profile
    # A network attempt must fail fast inside the sandbox.
    result = backend.exec(
        record,
        ["/usr/bin/curl", "-sS", "-m", "3", "-o", "/dev/null", "http://127.0.0.1:1/"],
        timeout=30,
    )
    assert not result.ok


def test_spawn_pause_resume_kill(tracked_home):
    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    pgid = backend.spawn(record, ["/bin/sleep", "60"])
    assert pgid > 0
    assert pgid in record["engine"]["pgids"]

    backend.pause(record)
    backend.resume(record)  # must not raise

    backend.pause(record)
    backend.kill(record)
    _wait_process_dead(pgid)
    assert record["engine"]["pgids"] == []


def test_spawn_env_visible(tracked_home):
    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    out = workspace_dir(record["id"]) / "env.txt"
    pgid = backend.spawn(
        record,
        ["/bin/sh", "-c", f"echo \"$LOOPBOX_TEST_ENV\" > '{out}'"],
        env={"LOOPBOX_TEST_ENV": "spawn-env-ok"},
    )
    assert pgid > 0
    for _ in range(50):
        if out.exists():
            break
        time.sleep(0.1)
    backend.kill(record)
    assert out.read_text(encoding="utf-8").strip() == "spawn-env-ok"


def test_snapshot_list_restore(tracked_home):
    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    ws = workspace_dir(record["id"])
    (ws / "state.txt").write_text("v1", encoding="utf-8")

    snap_id = backend.snapshot(record, name="v1")
    assert (snapshot_root(record["id"]) / snap_id).is_dir()

    (ws / "state.txt").write_text("v2", encoding="utf-8")
    (ws / "extra.txt").write_text("new", encoding="utf-8")

    listed = backend.list_snapshots(record)
    assert [s["snapshot_id"] for s in listed] == ["v1"]
    assert listed[0]["kind"] == "workspace-clone"
    assert listed[0]["created_at"] > 0

    backend.restore(record, snap_id)
    assert (ws / "state.txt").read_text(encoding="utf-8") == "v1"
    assert not (ws / "extra.txt").exists()
    # The restored workspace must not keep the snapshot marker file.
    assert not (ws / ".loopbox-snapshot.json").exists()


def test_restore_unknown_snapshot_raises(tracked_home):
    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    with pytest.raises(FileNotFoundError):
        backend.restore(record, "snap_missing")


def test_fork_copies_state_and_registers(tracked_home):
    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    Store().add(record)
    ws = workspace_dir(record["id"])
    (ws / "data.txt").write_text("shared", encoding="utf-8")

    child = backend.fork(record)
    try:
        assert child["id"] != record["id"]
        assert child["parent_id"] == record["id"]
        assert child["forked_from_snapshot"] is None
        # Fork contract: the child is already registered in the store.
        assert Store().get(child["id"])["id"] == child["id"]
        assert (workspace_dir(child["id"]) / "data.txt").read_text(
            encoding="utf-8"
        ) == "shared"

        # The twin is independent: writes to it never touch the parent.
        workspace_dir(child["id"]).joinpath("twin-only.txt").write_text("x")
        assert not (ws / "twin-only.txt").exists()
    finally:
        backend.kill(child)


def test_fork_from_snapshot(tracked_home):
    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    Store().add(record)
    ws = workspace_dir(record["id"])
    (ws / "v.txt").write_text("v1", encoding="utf-8")
    snap = backend.snapshot(record, name="base")
    (ws / "v.txt").write_text("v2", encoding="utf-8")

    child = backend.fork(record, snapshot_id=snap)
    try:
        assert child["forked_from_snapshot"] == snap
        assert (workspace_dir(child["id"]) / "v.txt").read_text(
            encoding="utf-8"
        ) == "v1"
    finally:
        backend.kill(child)


def test_fork_from_missing_snapshot_raises(tracked_home):
    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    with pytest.raises(FileNotFoundError):
        backend.fork(record, snapshot_id="snap_missing")


def test_kill_without_processes_is_quiet(tracked_home):
    backend = SeatbeltBackend()
    record = _make_record(backend)
    backend.create(record)
    backend.kill(record)  # must not raise
    assert record["engine"]["pgids"] == []
