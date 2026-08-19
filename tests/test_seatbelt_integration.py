"""Integration tests for the Seatbelt backend.

These exercise real sandbox-exec isolation and APFS clone snapshots.
Run with: LOOPBOX_INTEGRATION=1 pytest tests/test_seatbelt_integration.py
"""

from __future__ import annotations

import os
import shutil

import pytest

from loopbox.sdk import Sandbox, SandboxError
from loopbox.store import Store, workspace_dir

pytestmark = pytest.mark.skipif(
    os.environ.get("LOOPBOX_INTEGRATION") != "1" or shutil.which("sandbox-exec") is None,
    reason="set LOOPBOX_INTEGRATION=1 on macOS to run Seatbelt integration tests",
)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPBOX_HOME", str(tmp_path))
    return tmp_path


def test_exec_captures_output(home):
    sbx = Sandbox.create()
    try:
        result = sbx.commands.run("echo hello-from-sandbox")
        assert result.ok
        assert "hello-from-sandbox" in result.stdout
    finally:
        sbx.kill()


def test_writes_are_contained(home, tmp_path):
    sbx = Sandbox.create()
    escape_target = os.path.expanduser("~/loopbox-escape-test.txt")
    try:
        # Writing inside the workspace works.
        ok = sbx.commands.run("echo data > inside.txt && cat inside.txt")
        assert ok.ok and "data" in ok.stdout
        # Writing to the user home directory is denied by Seatbelt.
        denied = sbx.commands.run(f"echo nope > {escape_target}")
        assert not denied.ok
        assert not os.path.exists(escape_target)
    finally:
        sbx.kill()
        if os.path.exists(escape_target):
            os.unlink(escape_target)


def test_snapshot_fork_restore(home):
    sbx = Sandbox.create()
    try:
        sbx.files.write("state.txt", "v1")
        snap = sbx.snapshot("v1")
        sbx.files.write("state.txt", "v2")

        clone = sbx.fork(snapshot_id=snap)
        try:
            assert clone.files.read("state.txt") == "v1"
        finally:
            clone.kill()

        sbx.restore(snap)
        assert sbx.files.read("state.txt") == "v1"
    finally:
        sbx.kill()


def test_pause_requires_running(home):
    sbx = Sandbox.create()
    try:
        sbx.pause()
        with pytest.raises(SandboxError):
            sbx.pause()
        sbx.resume()
        assert sbx.status == "running"
    finally:
        sbx.kill()


def test_files_escape_rejected(home):
    sbx = Sandbox.create()
    try:
        with pytest.raises(SandboxError):
            sbx.files.read("../outside.txt")
    finally:
        sbx.kill()
