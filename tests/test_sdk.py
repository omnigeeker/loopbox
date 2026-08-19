"""Tests for the public Sandbox SDK (direct store + backend access, no HTTP)."""

from __future__ import annotations

import shutil
import time

import pytest

from loopbox.sdk import Sandbox, SandboxError
from loopbox.store import Store

pytestmark = pytest.mark.skipif(
    shutil.which("sandbox-exec") is None,
    reason="requires macOS sandbox-exec",
)


@pytest.fixture()
def sbx(tracked_home):
    sandbox = Sandbox.create()
    yield sandbox
    try:
        sandbox.kill()
    except SandboxError:
        pass


def test_create_registers_running_sandbox(tracked_home):
    sandbox = Sandbox.create(metadata={"k": "v"})
    try:
        assert sandbox.status == "running"
        assert sandbox.is_running
        assert sandbox.metadata == {"k": "v"}
        info = sandbox.info()
        assert info["sandbox_id"] == sandbox.id
        assert info["status"] == "running"
        assert Store().get(sandbox.id)["id"] == sandbox.id
    finally:
        sandbox.kill()


def test_unknown_template_rejected(tracked_home):
    with pytest.raises(SandboxError, match="unknown template"):
        Sandbox.create(template="nope")


def test_commands_run_string_and_argv(sbx):
    via_shell = sbx.commands.run("echo sdk-$((1 + 1))")
    assert via_shell.ok and via_shell.stdout.strip() == "sdk-2"
    direct = sbx.commands.run(["/bin/echo", "argv-ok"])
    assert direct.ok and direct.stdout.strip() == "argv-ok"
    to_dict = direct.to_dict()
    assert to_dict["command_line"] == "/bin/echo argv-ok"
    assert to_dict["exit_code"] == 0


def test_commands_run_empty_rejected(sbx):
    with pytest.raises(SandboxError):
        sbx.commands.run([])


def test_commands_run_env_and_cwd(sbx):
    sbx.files.write("a/b/.keep", "")
    result = sbx.commands.run(
        "echo $SDK_TEST_VAR && pwd",
        envs={"SDK_TEST_VAR": "env-ok"},
        cwd="a/b",
    )
    assert result.ok
    lines = result.stdout.splitlines()
    assert lines[0] == "env-ok"
    assert lines[1].endswith("/a/b")


def test_commands_run_cwd_escape_rejected(sbx):
    with pytest.raises(SandboxError, match="workspace"):
        sbx.commands.run("pwd", cwd="..")


def test_files_write_read_list(sbx):
    sbx.files.write("notes/hello.txt", "hi")
    assert sbx.files.read("notes/hello.txt") == "hi"
    assert sbx.files.read("notes/hello.txt", format="bytes") == b"hi"
    assert sbx.files.list() == ["notes"]
    assert sbx.files.list("notes") == ["hello.txt"]


def test_files_absolute_path_is_workspace_root(sbx):
    sbx.files.write("/abs.txt", "rooted")
    assert sbx.files.list() == ["abs.txt"]
    assert sbx.files.read("abs.txt") == "rooted"


@pytest.mark.parametrize(
    "bad_path",
    ["../escape.txt", "../../etc/passwd", "notes/../../escape.txt", "/../../../escape"],
)
def test_files_path_escape_rejected(sbx, bad_path):
    with pytest.raises(SandboxError, match="escape"):
        sbx.files.read(bad_path)
    with pytest.raises(SandboxError, match="escape"):
        sbx.files.write(bad_path, "x")
    with pytest.raises(SandboxError, match="escape"):
        sbx.files.list(bad_path)


def test_files_missing_read_raises(sbx):
    with pytest.raises(SandboxError, match="cannot read"):
        sbx.files.read("missing.txt")


def test_pause_resume_lifecycle(sbx):
    sbx.pause()
    assert sbx.status == "paused"
    with pytest.raises(SandboxError):
        sbx.pause()
    sbx.resume()
    assert sbx.status == "running"
    with pytest.raises(SandboxError):
        sbx.resume()


def test_set_timeout_persists(sbx):
    sbx.set_timeout(123.0)
    assert Store().get(sbx.id)["timeout"] == 123.0


def test_snapshot_restore_roundtrip(sbx):
    sbx.files.write("state.txt", "v1")
    snap_id = sbx.snapshot("v1")
    assert snap_id == "v1"
    assert any(s["snapshot_id"] == "v1" for s in sbx.list_snapshots())
    assert any(s["snapshot_id"] == "v1" for s in sbx.snapshots())  # CLI alias
    sbx.files.write("state.txt", "v2")
    sbx.restore(snap_id)
    assert sbx.files.read("state.txt") == "v1"


def test_fork_from_live_state(sbx):
    sbx.files.write("data.txt", "live")
    clone = sbx.fork()
    try:
        assert clone.id != sbx.id
        assert clone.files.read("data.txt") == "live"
        info = clone.info()
        assert info["parent_id"] == sbx.id
        # The twin is independent.
        clone.files.write("twin.txt", "twin")
        with pytest.raises(SandboxError):
            sbx.files.read("twin.txt")
    finally:
        clone.kill()


def test_fork_from_snapshot_sandbox_lifecycle(sbx):
    """End-to-end: snapshot -> diverge -> fork from snapshot -> old content."""
    sbx.files.write("v.txt", "v1")
    snap = sbx.snapshot("checkpoint")
    sbx.files.write("v.txt", "v2")
    sbx.files.write("only-after.txt", "x")

    clone = sbx.fork(snapshot_id=snap)
    try:
        assert clone.files.read("v.txt") == "v1"
        with pytest.raises(SandboxError):
            clone.files.read("only-after.txt")
        record = Store().get(clone.id)
        assert record.get("forked_from_snapshot") == snap
        # And the fork can still execute commands itself.
        assert clone.commands.run("echo fork-exec").ok
    finally:
        clone.kill()


def test_kill_blocks_further_operations(sbx):
    sbx.kill()
    with pytest.raises(SandboxError):
        sbx.commands.run("echo nope")
    with pytest.raises(SandboxError):
        sbx.snapshot()
    # The record is kept for forensics and reports killed.
    assert sbx.status == "killed"
    assert not sbx.is_running


def test_connect_rehydrates_sandbox(tracked_home):
    sandbox = Sandbox.create()
    try:
        again = Sandbox.connect(sandbox.id)
        assert again.id == sandbox.id
        assert again.commands.run("echo reconnect").ok
    finally:
        sandbox.kill()
    with pytest.raises(SandboxError):
        Sandbox.connect("sbx_missing")


def test_list_includes_created_sandbox(tracked_home):
    sandbox = Sandbox.create()
    try:
        ids = [r["id"] for r in Sandbox.list()]
        assert sandbox.id in ids
    finally:
        sandbox.kill()


def test_command_timeout_enforced(tracked_home):
    sandbox = Sandbox.create(timeout=1.0)
    try:
        started = time.monotonic()
        with pytest.raises(SandboxError):
            sandbox.commands.run("sleep 30")
        assert time.monotonic() - started < 20
    finally:
        sandbox.kill()
